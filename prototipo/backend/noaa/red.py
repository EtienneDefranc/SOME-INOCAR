"""Acceso HTTP respetuoso a los servidores de NOAA (PSL, NCEI y CPC).

Reglas de cortesía:

* **Ritmo por servidor**: como máximo 1 petición por segundo a cada servidor, en
  TOTAL entre todos los hilos y procesos del SOME del equipo (el turno se reserva
  bajo un bloqueo entre procesos en `.bloqueos/ritmo_<servidor>.txt`). A PSL,
  además, una pausa de `PAUSA_TRAS_PETICION_S` (20 s) tras cada petición: su
  servidor limita con HTTP 429 a quien pide seguido.
* **User-Agent genérico** sin correo electrónico (`constantes.USER_AGENT`).
* **Reintentos con espera** (sincronización y preparación): hasta 3 intentos;
  tras un fallo de red o un 5xx se espera 5 s y 15 s; tras un 429 se registra la
  limitación y se espera 60, 120, 240 y 300 s.
* **Consultas** (`respetar_pausa=True`): nada se cuelga. Un solo intento, 8 s para
  conectar y 45 s para recibir; ante un 429 no esperan, y si el servidor no
  responde (o responde 5xx) queda en pausa al instante: las consultas siguientes
  sirven la caché y avisan sin volver a esperarlo.
* **Pausa del servidor**: tras `FALLOS_PAUSA` intentos fallidos seguidos (o tras
  el primer fallo de una consulta), el servidor queda en pausa 10 min (persistida
  en `estado.json`, la respetan los demás procesos): las consultas no lo
  reintentan; la sincronización y la herramienta de preparación sí
  (`respetar_pausa=False`).

Cada hilo usa su propia `requests.Session`. Los mensajes de error van en español y
cortos; el texto técnico crudo queda solo en el log. Con `SOME_NOAA_SIN_RED=1`
ninguna función toca la red.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta

from ..chirps.bloqueo import bloqueo as _bloqueo_procesos
from .constantes import (ARCHIVO_LTM_DIARIA, ESPERA_429_S, FALLOS_PAUSA, INTENTOS, INTENTOS_CONSULTA,
                         INTERVALO_MINIMO_S, NCEI, NCSS_ESTE, NCSS_NORTE, NCSS_OESTE, NCSS_SUR, NOMBRE_CORTO_SERVIDOR,
                         PAUSA_REINTENTO_S, PAUSA_SERVIDOR_S, PAUSA_TRAS_PETICION_S, PSL, TIMEOUT_CONEXION_S,
                         TIMEOUT_CONSULTA_S, TIMEOUT_S, URL_CPC_SEMANAL, USER_AGENT, ErrorConexion, ErrorNOAA,
                         ErrorNoExiste, ruta_bloqueos, sin_red, url_ncei_dia, url_ncei_mes, url_ncss)

log = logging.getLogger("some.noaa")

# Relojes, espera y transporte reemplazables en las pruebas (sin esperas ni red reales)
_reloj_pared = time.time
_dormir = time.sleep
_local = threading.local()
_bloqueo_ritmo = threading.Lock()
_proximo_local: dict[str, float] = {}
ESPERA_BLOQUEO_S = 60
_FUTURO_MAXIMO_S = 600            # un turno compartido más lejano que esto se considera basura (reloj cambiado)


def _requests():
    """`requests` se importa al primer uso (no retrasa el arranque del servidor)."""
    import requests
    return requests


def _transporte_real(url: str, timeout: tuple[float, float]) -> tuple[int, bytes, dict]:
    s = getattr(_local, "sesion", None)
    if s is None:
        s = _requests().Session()
        s.headers["User-Agent"] = USER_AGENT
        _local.sesion = s
    r = s.get(url, timeout=timeout)
    return r.status_code, r.content, dict(r.headers)


_transporte = _transporte_real     # las pruebas lo reemplazan por un doble


# ---------------------------------------------------------------------------
# Ritmo por servidor (entre hilos y procesos)
# ---------------------------------------------------------------------------
def _archivo_turno(servidor: str):
    return ruta_bloqueos() / f"ritmo_{servidor}.txt"


def _leer_turno(servidor: str) -> float:
    try:
        return float(_archivo_turno(servidor).read_text(encoding="ascii").strip() or "nan")
    except (OSError, ValueError):
        return float("nan")


def _escribir_turno(servidor: str, valor: float) -> None:
    ruta = _archivo_turno(servidor)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(f"{valor:.6f}", encoding="ascii")


def _reservar_turno(servidor: str) -> float:
    """Reserva el próximo turno del servidor y devuelve la espera (s) hasta él."""
    intervalo = float(INTERVALO_MINIMO_S.get(servidor, 1.0))
    with _bloqueo_ritmo:
        ahora = _reloj_pared()
        espera = max(0.0, _proximo_local.get(servidor, 0.0) - ahora)
        try:
            with _bloqueo_procesos(ruta_bloqueos() / f"ritmo_{servidor}.lock", ESPERA_BLOQUEO_S):
                previo = _leer_turno(servidor)
                if previo == previo and previo - ahora <= _FUTURO_MAXIMO_S:      # no NaN ni basura futura
                    espera = max(espera, previo - ahora)
                _escribir_turno(servidor, ahora + espera + intervalo)
        except Exception:  # noqa: BLE001 — sin archivo compartido queda el limitador del proceso
            log.debug("NOAA: no se pudo usar el turno compartido de %s", servidor, exc_info=True)
        _proximo_local[servidor] = ahora + espera + intervalo
    return espera


def _pausa_tras(servidor: str) -> None:
    """Tras terminar una petición a PSL, nadie le pide nada durante PAUSA_TRAS_PETICION_S."""
    pausa = float(PAUSA_TRAS_PETICION_S.get(servidor, 0.0))
    if pausa <= 0:
        return
    with _bloqueo_ritmo:
        hasta = _reloj_pared() + pausa
        _proximo_local[servidor] = max(_proximo_local.get(servidor, 0.0), hasta)
        try:
            with _bloqueo_procesos(ruta_bloqueos() / f"ritmo_{servidor}.lock", ESPERA_BLOQUEO_S):
                previo = _leer_turno(servidor)
                _escribir_turno(servidor, max(hasta, previo if previo == previo else 0.0))
        except Exception:  # noqa: BLE001
            log.debug("NOAA: no se pudo registrar la pausa de %s", servidor, exc_info=True)


def _esperar_turno(servidor: str) -> None:
    espera = _reservar_turno(servidor)
    if espera > 0:
        _dormir(espera)


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
            t = str(e).lower()
            if "name resolution" in t or "getaddrinfo" in t or "nodename" in t:
                return "no se pudo resolver el nombre del servidor"
            if "refused" in t:
                return "conexión rechazada"
            return "sin conexión con el servidor"
        if isinstance(e, exc.Timeout):
            return "tiempo de espera agotado"
    except Exception:  # noqa: BLE001
        pass
    if isinstance(e, ErrorNOAA):
        return str(e)
    if isinstance(e, TimeoutError):
        return "tiempo de espera agotado"
    if isinstance(e, ConnectionError):
        return "sin conexión con el servidor"
    return f"error inesperado ({type(e).__name__})"


# ---------------------------------------------------------------------------
# Pausa del servidor tras fallos seguidos (persistida en estado.json)
# ---------------------------------------------------------------------------
_fallos: dict[str, int] = {}
_bloqueo_fallos = threading.Lock()


def _registrar(servidor: str, accesible: bool, latencia_ms: int | None = None, error: str = "",
               limitado: bool = False, pausar: bool = False) -> None:
    """Persiste la observación del servidor y abre la pausa tras FALLOS_PAUSA fallos seguidos (o al instante con
    `pausar`: el primer fallo de una consulta). Nunca lanza."""
    with _bloqueo_fallos:
        if accesible:
            _fallos[servidor] = 0
            abrir = False
        else:
            _fallos[servidor] = _fallos.get(servidor, 0) + 1
            abrir = pausar or _fallos[servidor] >= FALLOS_PAUSA
    try:
        from . import estado
        estado.registrar_servidor(servidor, accesible, latencia_ms, error, limitado=limitado)
        if abrir:
            hasta = datetime.now() + timedelta(seconds=PAUSA_SERVIDOR_S)
            estado.registrar_pausa(servidor, hasta, error)
            log.warning("NOAA: %s no responde (%s); no se reintenta desde las consultas hasta las %s.",
                        NOMBRE_CORTO_SERVIDOR.get(servidor, servidor), error, hasta.strftime("%H:%M"))
    except Exception:  # noqa: BLE001 — el registro nunca debe tumbar una descarga
        log.debug("NOAA: no se pudo registrar el estado de %s", servidor, exc_info=True)


def reiniciar_fallos() -> None:
    with _bloqueo_fallos:
        _fallos.clear()


def pausa_vigente(servidor: str) -> tuple[bool, datetime | None, str]:
    try:
        from . import estado
        return estado.pausa_vigente(servidor)
    except Exception:  # noqa: BLE001
        return False, None, ""


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------
def peticion(url: str, servidor: str, *, respetar_pausa: bool = True, intentos: int = INTENTOS) -> tuple[bytes, int]:
    """(contenido, latencia_ms) de un GET con ritmo, reintentos y registro.

    `respetar_pausa=True` (consultas): si el servidor está en pausa falla al instante; un solo intento con
    `TIMEOUT_CONSULTA_S` (8 s para conectar, 45 s para recibir); ante un HTTP 429 no espera, y si el servidor
    no responde o responde 5xx lo deja en pausa al instante (una consulta no puede quedarse minutos esperando:
    se sirve la caché y se avisa). `False` (sincronización, preparación, pruebas): ignora la pausa, ante un
    429 espera 60, 120, 240 y 300 s antes de rendirse y reintenta los fallos de red y los 5xx hasta
    `intentos` veces (esperas de 5 y 15 s). ErrorNoExiste si el servidor responde 404; ErrorConexion si no
    responde, limita o falla tras los reintentos; ErrorNOAA en modo sin red.
    """
    nombre = NOMBRE_CORTO_SERVIDOR.get(servidor, servidor)
    if sin_red():
        raise ErrorNOAA(f"Modo sin red activo (SOME_NOAA_SIN_RED=1): no se consulta {nombre}.")
    consulta = bool(respetar_pausa)
    if consulta:
        en_pausa, hasta, motivo = pausa_vigente(servidor)
        if en_pausa:
            raise ErrorConexion(f"{nombre} no respondió en los últimos intentos ({motivo.rstrip('.') or 'sin detalle'}); "
                                f"no se reintenta hasta las {hasta.strftime('%H:%M') if hasta else '—'}.", servidor)
        intentos = min(intentos, INTENTOS_CONSULTA)
        timeout = (TIMEOUT_CONSULTA_S[0], min(TIMEOUT_CONSULTA_S[1], TIMEOUT_S.get(servidor, 60)))
    else:
        timeout = (TIMEOUT_CONEXION_S, TIMEOUT_S.get(servidor, 60))
    ultimo = ""
    limitado = False
    fallos = 0              # fallos de red y respuestas 5xx (cuentan contra `intentos`)
    esperas_429 = 0         # esperas tras un HTTP 429 (no cuentan contra `intentos`)
    while True:
        _esperar_turno(servidor)
        t0 = time.perf_counter()
        try:
            estado_http, contenido, _cab = _transporte(url, timeout)
        except Exception as e:  # noqa: BLE001 — requests.ConnectionError, Timeout, …
            ultimo = texto_error(e)
            log.info("NOAA: sin respuesta de %s (%s): %s", nombre, url, e)
            _pausa_tras(servidor)
            _registrar(servidor, False, error=f"sin respuesta: {ultimo}", pausar=consulta)
            fallos += 1
            if fallos >= max(1, intentos):
                break
            _dormir(PAUSA_REINTENTO_S[min(fallos - 1, len(PAUSA_REINTENTO_S) - 1)])
            continue
        latencia = int(round((time.perf_counter() - t0) * 1000))
        _local.ultima_latencia_ms = latencia
        _pausa_tras(servidor)
        if estado_http == 200:
            _registrar(servidor, True, latencia)
            _sumar(servidor, len(contenido), latencia)
            return contenido, latencia
        if estado_http == 404:
            _registrar(servidor, True, latencia)      # el servidor responde: el recurso no existe
            raise ErrorNoExiste(f"{nombre} respondió 404 (no existe): {url}")
        if estado_http == 429:
            limitado = True
            ultimo = "el servidor limitó el ritmo de peticiones (HTTP 429)"
            _registrar(servidor, False, latencia, ultimo, limitado=True)
            if respetar_pausa or esperas_429 >= len(ESPERA_429_S):
                break
            _dormir(ESPERA_429_S[esperas_429])
            esperas_429 += 1
            continue
        if estado_http in (403,) or estado_http >= 500:
            ultimo = (f"el servidor respondió HTTP {estado_http}"
                      + (" (la petición superó el tiempo máximo de su proxy)" if estado_http in (502, 504) else ""))
            _registrar(servidor, False, latencia, ultimo, pausar=consulta)
            fallos += 1
            if fallos >= max(1, intentos):
                break
            _dormir(PAUSA_REINTENTO_S[min(fallos - 1, len(PAUSA_REINTENTO_S) - 1)])
            continue
        ultimo = f"respuesta inesperada HTTP {estado_http}"
        _registrar(servidor, False, latencia, ultimo)
        break
    raise ErrorConexion(f"No se pudo obtener datos de {nombre}: {ultimo}.", servidor, limitado=limitado)


def ultima_latencia_ms() -> int:
    return int(getattr(_local, "ultima_latencia_ms", 0))


_bloqueo_bytes = threading.Lock()
_contadores = {"peticiones": 0, "bytes": 0}


def _sumar(servidor: str, n: int, latencia_ms: int) -> None:
    with _bloqueo_bytes:
        _contadores["peticiones"] += 1
        _contadores["bytes"] += int(n)
    try:
        from . import estado
        estado.registrar_descarga(servidor, int(n), latencia_ms / 1000.0)
    except Exception:  # noqa: BLE001
        log.debug("NOAA: no se pudo registrar la descarga", exc_info=True)


def contadores() -> dict:
    """{"peticiones", "bytes"} de este proceso desde que arrancó."""
    with _bloqueo_bytes:
        return dict(_contadores)


# ---------------------------------------------------------------------------
# PSL (NCSS)
# ---------------------------------------------------------------------------
def _iso_t(d: date) -> str:
    return f"{d.isoformat()}T00:00:00Z"


def url_ncss_datos(archivo: str, variable: str, inicio: date | None, fin: date | None, *,
                   norte: float = NCSS_NORTE, sur: float = NCSS_SUR, oeste: float = NCSS_OESTE,
                   este: float = NCSS_ESTE, inicio_txt: str | None = None, fin_txt: str | None = None) -> str:
    """URL NCSS (NetCDF4) del recorte de la ventana SOME + Niño 1+2 entre `inicio` y `fin` (inclusivos)."""
    t0 = inicio_txt or _iso_t(inicio)
    t1 = fin_txt or _iso_t(fin)
    return (f"{url_ncss(archivo)}?var={variable}&north={norte}&south={sur}&west={oeste}&east={este}"
            f"&time_start={t0}&time_end={t1}&accept=netcdf4")


def ncss(archivo: str, variable: str, inicio: date, fin: date, *, respetar_pausa: bool = True) -> tuple[bytes, int]:
    return peticion(url_ncss_datos(archivo, variable, inicio, fin), PSL, respetar_pausa=respetar_pausa)


def ncss_ltm_diaria(dia_inicio: int, dia_fin: int, *, respetar_pausa: bool = True) -> tuple[bytes, int]:
    """Días `dia_inicio`…`dia_fin` (1–365) de la climatología diaria 1991-2020, solo la región Niño 1+2.

    El archivo usa el año 0001; pedirlo entero (365 días) supera el tiempo máximo del proxy de PSL."""
    t0 = date(1, 1, 1) + timedelta(days=dia_inicio - 1)
    t1 = date(1, 1, 1) + timedelta(days=dia_fin - 1)
    url = url_ncss_datos(ARCHIVO_LTM_DIARIA, "sst", None, None, norte=-0.05, sur=-9.95, oeste=270.05, este=279.95,
                         inicio_txt=f"{t0.isoformat()}T00:00:00Z", fin_txt=f"{t1.isoformat()}T00:00:00Z")
    return peticion(url, PSL, respetar_pausa=respetar_pausa)


def ncss_todo(archivo: str, variable: str = "sst", *, respetar_pausa: bool = True) -> tuple[bytes, int]:
    """Todo el eje de tiempo de un archivo pequeño en tiempo (medias mensuales, climatología mensual)."""
    url = (f"{url_ncss(archivo)}?var={variable}&north={NCSS_NORTE}&south={NCSS_SUR}&west={NCSS_OESTE}"
           f"&east={NCSS_ESTE}&temporal=all&accept=netcdf4")
    return peticion(url, PSL, respetar_pausa=respetar_pausa)


def metadatos_psl(archivo: str, *, respetar_pausa: bool = True) -> tuple[bytes, int]:
    """`dataset.xml` del NCSS (pequeño, ~0.2 s): eje de tiempo publicado. Sirve de verificación con latencia."""
    return peticion(f"{url_ncss(archivo)}/dataset.xml", PSL, respetar_pausa=respetar_pausa, intentos=2)


# ---------------------------------------------------------------------------
# NCEI y CPC
# ---------------------------------------------------------------------------
def listado_ncei(anio: int, mes: int, *, respetar_pausa: bool = True) -> tuple[bytes, int]:
    return peticion(url_ncei_mes(anio, mes), NCEI, respetar_pausa=respetar_pausa, intentos=2)


def archivo_ncei(d: date, preliminar: bool, *, respetar_pausa: bool = True) -> tuple[bytes, int]:
    return peticion(url_ncei_dia(d, preliminar), NCEI, respetar_pausa=respetar_pausa)


def cpc_semanal() -> str:
    contenido, _lat = peticion(URL_CPC_SEMANAL, "cpc", respetar_pausa=False, intentos=2)
    return contenido.decode("latin-1", "replace")
