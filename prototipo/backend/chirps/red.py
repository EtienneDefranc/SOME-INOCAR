"""Acceso HTTP cortés al servidor del CHC: ritmo global, disyuntor, reintentos y bytes.

Reglas de cortesía (el CHC pide descargas automáticas pausadas e identificadas):

* **Ritmo global**: como máximo `PETICIONES_POR_SEGUNDO` (1) petición por segundo
  en TOTAL entre todos los hilos y todos los procesos del SOME del equipo
  (servidor, script de preparación, pruebas): el turno se reserva bajo un
  bloqueo entre procesos en `datos/chirps/.bloqueos/ritmo_chc.txt`.
* **Un archivo a la vez**: `un_archivo()` es un bloqueo entre hilos y procesos
  (reentrante por hilo) que toman la descarga de un GeoTIFF diario y la de un
  NetCDF mensual de la climatología.
* **User-Agent con contacto** (`constantes.USER_AGENT`).
* **Disyuntor**: tras `FALLOS_DISYUNTOR` intentos fallidos seguidos (sin
  conexión, tiempo agotado, HTTP 403/429/5xx) el CHC se marca caído y durante
  `DISYUNTOR_S` (15 min) toda petición falla al instante con `ErrorDisyuntor`
  sin tocar la red. Pasado el plazo se permite un intento de prueba: si falla,
  se vuelve a abrir otros 15 min; si responde, se cierra. Un HTTP 429 lo abre
  de inmediato. La apertura se persiste en `estado.json` y la respetan también
  los demás procesos del SOME (servidor, script de preparación, pruebas).

Cada hilo usa su propia `requests.Session` (las sesiones no son seguras para
compartir entre hilos). Los mensajes de error van en español y cortos
(`texto_error`); el texto técnico crudo queda solo en el log.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from typing import TYPE_CHECKING

from .bloqueo import TiempoAgotadoBloqueo
from .bloqueo import bloqueo as _bloqueo_procesos
from .constantes import (DISYUNTOR_S, FALLOS_DISYUNTOR, INTENTOS, PETICIONES_POR_SEGUNDO, RUTA_ESTADO,
                         TIMEOUT_CONEXION_S, TIMEOUT_S, USER_AGENT, ErrorCHIRPS, sin_red)

if TYPE_CHECKING:                 # solo para las anotaciones
    import requests

log = logging.getLogger("some.chirps")


def _requests():
    """`requests` se importa al primer uso (~0.7 s): así no retrasa el arranque del servidor, que
    no hace ninguna petición al CHC antes de responder (la sincronización corre en otro hilo)."""
    import requests
    return requests

# Relojes y espera reemplazables en las pruebas (reloj simulado, sin esperas reales).
_reloj = time.monotonic
_reloj_pared = datetime.now
_dormir = time.sleep
PAUSA_REINTENTO_S = 1.5           # espera base entre intentos (×1, ×2…)

_local = threading.local()


class ErrorConexion(ErrorCHIRPS):
    """El servidor del CHC no responde o rechaza la conexión."""


class ErrorDisyuntor(ErrorConexion):
    """El disyuntor está abierto: el CHC se marcó caído y no se reintenta todavía."""


# ---------------------------------------------------------------------------
# Limitador de ritmo global (entre hilos y entre procesos) y «un archivo a la vez»
# ---------------------------------------------------------------------------
_bloqueo_ritmo = threading.Lock()
_proxima_peticion = 0.0           # respaldo de este proceso (reloj monótono)
ESPERA_BLOQUEO_S = 60
_FUTURO_MAXIMO_S = 120            # un turno compartido más lejano que esto se considera basura (reloj cambiado)


def intervalo_minimo_s() -> float:
    return 1.0 / PETICIONES_POR_SEGUNDO


def carpeta_bloqueos() -> Path:
    """`datos/chirps/.bloqueos/` (sigue a `estado.RUTA_ESTADO`, que las pruebas redirigen)."""
    try:
        from . import estado
        return Path(estado.RUTA_ESTADO).parent / ".bloqueos"
    except Exception:  # noqa: BLE001
        return Path(RUTA_ESTADO).parent / ".bloqueos"


def _reservar_turno_compartido(espera_minima: float, intervalo: float) -> float:
    """Reserva el turno en el archivo compartido entre procesos y devuelve la espera (s) hasta él.

    El archivo guarda la hora de pared (epoch) del último turno reservado por CUALQUIER proceso;
    cada proceso aplica su propio intervalo mínimo desde ese turno.
    """
    carpeta = carpeta_bloqueos()
    ruta = carpeta / "ritmo_chc.txt"
    with _bloqueo_procesos(carpeta / "ritmo_chc.lock", ESPERA_BLOQUEO_S):
        ahora = _reloj_pared().timestamp()
        try:
            ultimo = float(ruta.read_text(encoding="ascii").strip() or "nan")
        except (OSError, ValueError):
            ultimo = float("nan")
        espera = espera_minima
        if ultimo == ultimo and ultimo - ahora <= _FUTURO_MAXIMO_S:      # no NaN ni basura futura
            espera = max(espera, ultimo + intervalo - ahora)
        espera = max(0.0, espera)
        ruta.write_text(f"{ahora + espera:.6f}", encoding="ascii")
    return espera


def _esperar_turno() -> None:
    """Reserva el siguiente hueco del ritmo global y espera hasta él (todos los hilos y procesos).

    Si el archivo compartido no se puede usar (permisos, disco), se aplica al menos el limitador
    de este proceso.
    """
    global _proxima_peticion
    intervalo = intervalo_minimo_s()
    with _bloqueo_ritmo:
        ahora = _reloj()
        espera = max(0.0, _proxima_peticion - ahora)
        try:
            espera = _reservar_turno_compartido(espera, intervalo)
        except Exception:  # noqa: BLE001 — sin archivo compartido queda el limitador del proceso
            log.debug("CHIRPS: no se pudo usar el turno compartido entre procesos", exc_info=True)
        _proxima_peticion = ahora + espera + intervalo
    if espera > 0:
        _dormir(espera)


@contextlib.contextmanager
def un_archivo(espera: float | None = None):
    """Un archivo del CHC a la vez entre todos los hilos y procesos del SOME (reentrante por hilo).

    `espera`: segundos máximos para obtener el turno (None = sin límite); si no se obtiene, lanza
    ErrorCHIRPS («hay otra descarga del CHC en curso»).
    """
    with contextlib.ExitStack() as pila:
        try:
            pila.enter_context(_bloqueo_procesos(carpeta_bloqueos() / "archivo_chc.lock", espera))
        except TiempoAgotadoBloqueo as e:
            raise ErrorCHIRPS("Hay otra descarga del CHC en curso; se reintentará más tarde.") from e
        yield


# ---------------------------------------------------------------------------
# Mensajes de error en español
# ---------------------------------------------------------------------------
def texto_error(e: BaseException) -> str:
    """Motivo corto en español de un fallo de red; el texto técnico crudo va solo al log."""
    try:
        exc = _requests().exceptions
        if isinstance(e, exc.ProxyError):
            return "error del proxy"
        if isinstance(e, exc.ConnectTimeout):
            return "tiempo de espera agotado al conectar"
        if isinstance(e, exc.ReadTimeout):
            return "tiempo de espera agotado al recibir la respuesta"
        if isinstance(e, exc.SSLError):
            return "error de certificado o de cifrado (SSL)"
        if isinstance(e, exc.ConnectionError):
            from . import estado
            motivo = estado._traducir_tecnico(str(e))
            return "sin conexión con el servidor" if motivo == "sin conexión" else motivo
        if isinstance(e, exc.Timeout):
            return "tiempo de espera agotado"
    except Exception:  # noqa: BLE001
        pass
    if isinstance(e, ErrorCHIRPS):
        return str(e)
    try:
        from . import estado
        return estado._traducir_tecnico(f"{type(e).__name__}: {e}")
    except Exception:  # noqa: BLE001
        return type(e).__name__


# ---------------------------------------------------------------------------
# Disyuntor
# ---------------------------------------------------------------------------
_bloqueo_disyuntor = threading.Lock()
_fallos_seguidos = 0
_abierto_hasta = 0.0              # _reloj() hasta el que no se pide nada (0 = cerrado)
_abierto_hasta_reloj: datetime | None = None
_ultimo_error = ""
_aperturas = 0
_ultimo_fallo = 0.0               # _reloj() del último fallo (0 = ninguno desde el último éxito)
_accesible_notificado: bool | None = None   # último cambio de accesibilidad persistido


def _notificar_estado(abierto: bool, hasta: datetime | None, motivo: str) -> None:
    """Persiste la apertura o el cierre del disyuntor (importación perezosa, nunca lanza)."""
    try:
        from . import estado
        estado.registrar_disyuntor(abierto, hasta, motivo, _aperturas)
    except Exception:  # noqa: BLE001 — el registro nunca debe tumbar una descarga
        log.debug("CHIRPS: no se pudo registrar el estado del disyuntor", exc_info=True)


def _notificar_accesible(accesible: bool, error: str = "", latencia_ms: int | None = None) -> None:
    """Persiste solo los CAMBIOS de accesibilidad del CHC observados al descargar (nunca lanza)."""
    global _accesible_notificado
    with _bloqueo_disyuntor:
        if _accesible_notificado is accesible:
            return
        _accesible_notificado = accesible
    try:
        from . import estado
        estado.registrar_chc(accesible, error, latencia_ms)
    except Exception:  # noqa: BLE001
        log.debug("CHIRPS: no se pudo registrar la accesibilidad del CHC", exc_info=True)


def _persistido() -> tuple[bool, datetime | None, str]:
    """Disyuntor abierto por OTRO proceso (registrado en estado.json). Fuera del bloqueo; nunca lanza."""
    try:
        from . import estado
        return estado.disyuntor_vigente(_reloj_pared())
    except Exception:  # noqa: BLE001
        return False, None, ""


def info_disyuntor() -> tuple[bool, datetime | None, str]:
    """(abierto, hasta, motivo) combinando el disyuntor de este proceso y el persistido."""
    with _bloqueo_disyuntor:
        if _reloj() < _abierto_hasta:
            return True, _abierto_hasta_reloj, _ultimo_error
    return _persistido()


def disyuntor_abierto() -> bool:
    """True si el CHC está marcado caído (por este u otro proceso) y aún no toca reintentar."""
    return info_disyuntor()[0]


def estado_disyuntor() -> dict:
    """{"abierto", "fallos_seguidos", "hasta" (ISO o None), "ultimo_error", "aperturas", "umbral", "plazo_min"}."""
    with _bloqueo_disyuntor:
        abierto = _reloj() < _abierto_hasta
        return {"abierto": abierto,
                "fallos_seguidos": _fallos_seguidos,
                "hasta": (_abierto_hasta_reloj.isoformat(timespec="seconds")
                          if abierto and _abierto_hasta_reloj else None),
                "ultimo_error": _ultimo_error,
                "aperturas": _aperturas,
                "umbral": FALLOS_DISYUNTOR,
                "plazo_min": DISYUNTOR_S // 60}


def reiniciar_disyuntor() -> None:
    """Cierra el disyuntor y olvida los fallos (pruebas y administración)."""
    global _fallos_seguidos, _abierto_hasta, _abierto_hasta_reloj, _ultimo_error, _ultimo_fallo, _aperturas
    global _accesible_notificado
    with _bloqueo_disyuntor:
        _fallos_seguidos = 0
        _abierto_hasta = 0.0
        _abierto_hasta_reloj = None
        _ultimo_error = ""
        _ultimo_fallo = 0.0
        _aperturas = 0
        _accesible_notificado = None


def _permitir() -> None:
    """Lanza `ErrorDisyuntor` si el CHC está marcado caído (por este u otro proceso)."""
    with _bloqueo_disyuntor:
        if _reloj() < _abierto_hasta:
            hasta = _abierto_hasta_reloj.strftime("%H:%M") if _abierto_hasta_reloj else "—"
            raise ErrorDisyuntor(
                f"El servidor del CHC se marcó como caído tras {FALLOS_DISYUNTOR} intentos fallidos "
                f"seguidos; no se reintenta hasta las {hasta} ({_ultimo_error})")
    abierto, hasta, motivo = _persistido()
    if abierto:
        raise ErrorDisyuntor(
            f"El servidor del CHC está marcado como caído (lo registró otra ejecución del SOME); no se "
            f"reintenta hasta las {hasta.strftime('%H:%M') if hasta else '—'} ({motivo})")


def _registrar_exito(latencia_ms: int | None = None) -> None:
    global _fallos_seguidos, _abierto_hasta, _abierto_hasta_reloj, _ultimo_fallo
    with _bloqueo_disyuntor:
        estaba_marcado = _abierto_hasta > 0
        _fallos_seguidos = 0
        _abierto_hasta = 0.0
        _abierto_hasta_reloj = None
        _ultimo_fallo = 0.0
    if estaba_marcado:
        log.info("CHIRPS: el servidor del CHC vuelve a responder; se cierra el disyuntor.")
        _notificar_estado(False, None, "El servidor del CHC volvió a responder.")
    _notificar_accesible(True, "", latencia_ms)


def _en_prueba_persistido() -> bool:
    """True si otro proceso abrió el disyuntor, ya venció y el CHC no ha vuelto a responder:
    el intento actual es el de prueba y, si falla, se reabre (no se gastan 3 intentos más)."""
    try:
        from . import estado
        return estado.disyuntor_en_prueba(_reloj_pared())
    except Exception:  # noqa: BLE001
        return False


def _registrar_fallo(error: str, abrir_ya: bool = False) -> None:
    global _fallos_seguidos, _abierto_hasta, _abierto_hasta_reloj, _ultimo_error, _aperturas, _ultimo_fallo
    if not abrir_ya and _fallos_seguidos == 0 and _abierto_hasta == 0.0:
        abrir_ya = _en_prueba_persistido()
    with _bloqueo_disyuntor:
        ahora = _reloj()
        _fallos_seguidos += 1
        _ultimo_error = error[:300]
        _ultimo_fallo = ahora
        abrir = (abrir_ya or _fallos_seguidos >= FALLOS_DISYUNTOR) and ahora >= _abierto_hasta
        if abrir:
            _abierto_hasta = ahora + DISYUNTOR_S
            _abierto_hasta_reloj = _reloj_pared() + timedelta(seconds=DISYUNTOR_S)
            _aperturas += 1
            hasta = _abierto_hasta_reloj
    _notificar_accesible(False, error)
    if abrir:
        log.warning("CHIRPS: el CHC no responde (%s); no se reintenta hasta las %s.",
                    "1 intento fallido" if _fallos_seguidos == 1 else f"{_fallos_seguidos} intentos fallidos seguidos",
                    hasta.strftime("%H:%M"))
        _notificar_estado(True, hasta, error)


def servidor_caido_reciente(segundos: float = 300) -> bool:
    """True si el disyuntor está abierto o hubo un fallo en los últimos `segundos` sin éxito posterior."""
    with _bloqueo_disyuntor:
        ahora = _reloj()
        return ahora < _abierto_hasta or (_ultimo_fallo > 0 and ahora - _ultimo_fallo < segundos)


# ---------------------------------------------------------------------------
# Sesión y contador de bytes
# ---------------------------------------------------------------------------
_bloqueo_bytes = threading.Lock()
_bytes_descargados = 0


def sesion() -> "requests.Session":
    s = getattr(_local, "sesion", None)
    if s is None:
        s = _requests().Session()
        s.headers["User-Agent"] = USER_AGENT
        _local.sesion = s
    return s


def _sumar_bytes(n: int) -> None:
    global _bytes_descargados
    with _bloqueo_bytes:
        _bytes_descargados += n


def bytes_descargados() -> int:
    """Bytes descargados del CHC por este proceso desde que arrancó."""
    with _bloqueo_bytes:
        return _bytes_descargados


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------
def _peticion(url: str, cabeceras: dict | None = None, estados_validos=(200, 206)) -> "requests.Response":
    if sin_red():
        raise ErrorCHIRPS("Modo sin red activo (SOME_CHIRPS_SIN_RED=1): no se consulta el servidor del CHC.")
    requests = _requests()
    ultimo_error: Exception | str | None = None
    for intento in range(INTENTOS):
        _permitir()
        _esperar_turno()
        _permitir()                       # pudo abrirse mientras se esperaba el turno
        t0 = time.perf_counter()
        try:
            r = sesion().get(url, headers=cabeceras or {}, timeout=(TIMEOUT_CONEXION_S, TIMEOUT_S))
        except (requests.ConnectionError, requests.Timeout) as e:
            motivo = texto_error(e)
            ultimo_error = motivo
            log.info("CHIRPS: sin respuesta del CHC (%s): %s", url, e)
            _registrar_fallo(f"sin respuesta del CHC: {motivo}")
            if intento == INTENTOS - 1 or disyuntor_abierto():
                raise ErrorConexion(f"No se pudo conectar con el servidor del CHC: {motivo}.") from e
        except requests.RequestException as e:
            log.info("CHIRPS: error de la petición al CHC (%s): %s", url, e)
            ultimo_error = texto_error(e)
        else:
            lat = int(round((time.perf_counter() - t0) * 1000))
            _local.ultima_latencia_ms = lat
            if r.status_code in estados_validos:
                _sumar_bytes(len(r.content))
                _registrar_exito(lat)
                return r
            if r.status_code == 404:
                _registrar_exito(lat)     # el servidor responde: el recurso simplemente no existe
                raise ErrorCHIRPS(f"El servidor del CHC respondió 404 (no existe): {url}")
            if r.status_code in (403, 429):
                _registrar_fallo(f"HTTP {r.status_code}", abrir_ya=(r.status_code == 429))
                raise ErrorConexion(f"El servidor del CHC rechazó la petición (HTTP {r.status_code}): {url}")
            if r.status_code >= 500:
                _registrar_fallo(f"HTTP {r.status_code}")
                if disyuntor_abierto():
                    raise ErrorConexion(f"El servidor del CHC respondió HTTP {r.status_code}: {url}")
            ultimo_error = f"HTTP {r.status_code}"
        if intento < INTENTOS - 1:
            _dormir(PAUSA_REINTENTO_S * (intento + 1))
    raise ErrorCHIRPS(f"No se pudo descargar {url} tras {INTENTOS} intentos: {ultimo_error}")


def obtener(url: str) -> bytes:
    """Descarga completa (índices HTML, archivos pequeños)."""
    return _peticion(url).content


def obtener_404_vacio(url: str) -> bytes | None:
    """Como `obtener`, pero devuelve None si el recurso no existe (404)."""
    try:
        return _peticion(url).content
    except ErrorConexion:
        raise
    except ErrorCHIRPS as e:
        if "404" in str(e):
            return None
        raise


def rango(url: str, inicio: int, fin: int) -> bytes:
    """Bytes [inicio, fin] (inclusivos) de `url` mediante una petición HTTP Range."""
    return rango_con_total(url, inicio, fin)[0]


def rango_con_total(url: str, inicio: int, fin: int) -> tuple[bytes, int | None]:
    """(bytes [inicio, fin], tamaño total del archivo según Content-Range o None)."""
    if fin < inicio:
        return b"", None
    r = _peticion(url, {"Range": f"bytes={inicio}-{fin}"})
    total = None
    try:
        rango_txt = str((getattr(r, "headers", None) or {}).get("Content-Range") or "")
        if "/" in rango_txt and not rango_txt.endswith("/*"):
            total = int(rango_txt.rsplit("/", 1)[1])
    except (TypeError, ValueError):
        total = None
    if r.status_code == 206:
        return r.content, total
    # El servidor ignoró el Range y mandó el archivo completo
    return r.content[inicio:fin + 1], len(r.content)


def latencia_ms(url: str) -> int:
    """Latencia real de una petición pequeña (512 bytes por Range), sin contar la espera del limitador."""
    rango(url, 0, 511)
    return int(getattr(_local, "ultima_latencia_ms", 0))
