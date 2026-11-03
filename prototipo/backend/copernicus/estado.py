"""Estado de Copernicus Marine (RF-21), modo y acceso, persistidos en `datos/copernicus/estado.json`.

Guarda lo último verificado de verdad:

* **credenciales**: resultado de `login(check_credentials_valid=True)` (válidas,
  inválidas, error) con la huella de los archivos de credenciales (si el usuario
  vuelve a ejecutar «copernicusmarine login», el veredicto anterior deja de valer
  sin reiniciar el servidor);
* **metadatos** (`describe`): fin del eje de tiempo de ARCO, actualización y
  geometría de fragmentos;
* **servicio**: la última lectura (de prueba o de una descarga) con su latencia;
  una falla de red marca el servicio «caído» durante `CAIDO_S` (las consultas no
  reintentan; la sincronización sí);
* historial diario (el peor estado de cada día), sincronizaciones, descargas y
  errores recientes (en español, sin datos técnicos crudos).

Todo lo que se consulta aquí es SIN RED y no lanza: `estado_actual()`,
`historial_estado()`, `detalle()`, `modo()`, `acceso_disponible()`,
`fecha_corte()`. Las escrituras son transacciones bajo un bloqueo entre procesos
(`.bloqueos/estado.lock`), como en CHIRPS.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import threading
import time
from datetime import date, datetime, timedelta

from .. import configuracion as cfg
from ..chirps.bloqueo import bloqueo as _bloqueo_procesos
from ..chirps.estado import acortar, legible
from . import acceso, almacen, calendario
from .constantes import (CAIDO_MAX_S, CAIDO_S, DOI_GLORYS, LATENCIA_ALTA_MS, METADATOS_VIGENTES_H, PRODUCTO_GLORYS,
                         SERVIDOR_ARCO, TOTAL_PARES_CLIMATOLOGIA, VERIFICACION_VIGENTE_H, modo_configurado,
                         ruta_bloqueos, ruta_estado, sin_red)

log = logging.getLogger("some.copernicus")
ESPERA_BLOQUEO_S = 30

_GRAVEDAD = {"operativa": 0, "degradada": 1, "sin_conexion": 2}
TEXTO_ORIGEN_HISTORIAL = {"copernicus": "Copernicus Marine (ARCO)", "cache": "Caché local", None: "Sin registro",
                          "simulado": "Simulación del prototipo"}
TEXTO_ESTADO_SERVICIO = {"operativa": "Operativa", "degradada": "Degradada", "sin_conexion": "Sin conexión",
                         "sin_registro": "Sin verificar", "sin_red": "No se consulta (modo sin red)",
                         "no_instalado": "Toolbox no instalado", "simulado": "No se consulta (modo simulado)"}
TEXTO_CREDENCIALES = {"validas": "Válidas", "invalidas": "No válidas", "ausentes": "Sin credenciales",
                      "sin_verificar": "Sin verificar", "error": "No se pudieron verificar"}

# Estado simulado anterior (SOME_COPERNICUS_MODO=simulado)
FECHA_CORTE_SIMULADA = date(2026, 10, 26)
LATENCIA_SIMULADA_MS = 1450
MENSAJE_MODO_SIMULADO = ("Modo simulado (SOME_COPERNICUS_MODO=simulado): no se consulta Copernicus Marine; los "
                         "datos de esta fuente son simulados.")

_bloqueo = threading.RLock()
_datos: dict | None = None
_cargado: tuple[str, int] | None = None
_verificando = threading.Event()
_ultimo_guardado_ok = True
_escrituras = 0             # escrituras de este proceso (forman parte de la clave del estado recordado)


def _vacio() -> dict:
    return {"verificaciones": [], "credenciales": {}, "metadatos": {}, "servicio": {}, "caida": {},
            "historial": {}, "historial_origen": {}, "sincronizaciones": [], "ultima_sincronizacion": None,
            "descargas": {"fragmentos": 0, "segundos": 0.0, "ultima": None}, "errores_recientes": []}


def _mtime() -> int:
    try:
        return ruta_estado().stat().st_mtime_ns
    except OSError:
        return -1


def _cargar() -> dict:
    """Copia en memoria; se relee si otro proceso cambió el archivo (o si cambió la raíz de la caché)."""
    global _datos, _cargado
    clave = (str(ruta_estado()), _mtime())
    if _datos is None or clave != _cargado:
        try:
            with open(ruta_estado(), encoding="utf-8") as f:
                leido = json.load(f)
            if not isinstance(leido, dict):
                raise ValueError("estado.json no es un objeto")
            _datos = {**_vacio(), **leido}
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, UnicodeDecodeError):
            if _datos is None or (_cargado and _cargado[0] != clave[0]):
                _datos = _vacio()
        _cargado = clave
    return _datos


def _guardar() -> bool:
    """Escribe el estado (temporal + `os.replace` con reintentos). Nunca lanza."""
    global _cargado, _escrituras
    _escrituras += 1
    datos = _datos if _datos is not None else _cargar()
    ruta = ruta_estado()
    tmp = None
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="estado.", suffix=".tmp", dir=ruta.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=1, default=str)
            f.flush()
            os.fsync(f.fileno())
        for intento in range(6):
            try:
                os.replace(tmp, ruta)
                tmp = None
                break
            except PermissionError:
                if intento < 5:
                    time.sleep(0.05 * (2 ** intento))
        if tmp is not None:          # Windows: otro proceso lo tiene abierto; se escribe en el sitio
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False, indent=1, default=str)
                f.flush()
                os.fsync(f.fileno())
        _cargado = (str(ruta), _mtime())
        return True
    except OSError as e:
        log.error("Copernicus: no se pudo guardar el estado en %s: %s", ruta, e)
        return False
    finally:
        if tmp is not None and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


@contextlib.contextmanager
def _transaccion():
    """Leer-modificar-guardar bajo el bloqueo de hilos y el bloqueo entre procesos (relee del disco)."""
    global _datos, _ultimo_guardado_ok
    with _bloqueo, _bloqueo_procesos(ruta_bloqueos() / "estado.lock", ESPERA_BLOQUEO_S):
        _datos = None
        d = _cargar()
        yield d
        _ultimo_guardado_ok = _guardar()


def recargar() -> None:
    global _datos, _cargado, _memo_estado
    with _bloqueo:
        _datos, _cargado, _memo_estado = None, None, None


def _ahora() -> datetime:
    return datetime.now()


def _iso(x) -> str | None:
    if x is None:
        return None
    return x.isoformat(timespec="seconds") if isinstance(x, datetime) else x.isoformat()


def _dt(texto) -> datetime | None:
    try:
        return datetime.fromisoformat(str(texto)[:19]) if texto else None
    except ValueError:
        return None


def _fecha(texto) -> date | None:
    try:
        return date.fromisoformat(str(texto)[:10]) if texto else None
    except ValueError:
        return None


def datos() -> dict:
    with _bloqueo:
        return json.loads(json.dumps(_cargar(), default=str))


def version_producto() -> str | None:
    """Versión del dataset según los últimos metadatos consultados (p. ej. «202406»), sin red."""
    with _bloqueo:
        return (_cargar().get("metadatos") or {}).get("version")


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------
def _agregar_error(d: dict, mensaje: str) -> None:
    d["errores_recientes"].append({"momento": _ahora().isoformat(timespec="seconds"),
                                   "mensaje": legible(acceso.limpiar(mensaje), 400)})
    d["errores_recientes"] = d["errores_recientes"][-20:]


def registrar_error(mensaje: str) -> None:
    try:
        with _transaccion() as d:
            _agregar_error(d, mensaje)
    except Exception as e:  # noqa: BLE001 — el registro nunca debe impedir seguir
        log.warning("Copernicus: no se pudo registrar el error «%s»: %s", mensaje, e)


def registrar_credenciales(estado_cred: str, error: str = "", latencia_ms: int = 0) -> None:
    """Resultado de una verificación de credenciales (nunca guarda usuario ni contraseña)."""
    ahora = _ahora()
    with _transaccion() as d:
        previo = (d.get("credenciales") or {}).get("estado")
        d["credenciales"] = {"estado": estado_cred, "verificado": ahora.isoformat(timespec="seconds"),
                             "firma": acceso.firma_credenciales(), "latencia_ms": int(latencia_ms),
                             "error": legible(acceso.limpiar(error))}
        if estado_cred in ("invalidas", "error") and previo != estado_cred:
            _agregar_error(d, f"Credenciales: {error or TEXTO_CREDENCIALES[estado_cred]}")
        _marcar_dia(d, ahora)


def registrar_metadatos(meta: dict | None, error: str = "") -> None:
    ahora = _ahora().isoformat(timespec="seconds")
    with _transaccion() as d:
        if meta:
            d["metadatos"] = {**meta, "consultado": ahora, "error": ""}
        else:
            previo = dict(d.get("metadatos") or {})
            previo.update({"error": legible(acceso.limpiar(error)), "error_momento": ahora})
            d["metadatos"] = previo
            _agregar_error(d, f"Metadatos de Copernicus Marine: {error}")


def registrar_servicio(accesible: bool, latencia_ms: int | None = None, error: str = "", prueba: bool = False) -> None:
    """Observación del servicio ARCO: lectura de prueba (`prueba=True`, con su latencia) o resultado de una
    descarga. Una descarga solo escribe si cambia la accesibilidad o pasó más de una hora."""
    ahora = _ahora()
    with _bloqueo:
        previo = dict(_cargar().get("servicio") or {})
    momento_previo = _dt(previo.get("momento"))
    if (not prueba and previo.get("accesible") == bool(accesible) and momento_previo
            and ahora - momento_previo < timedelta(hours=1)):
        return
    with _transaccion() as d:
        previo = dict(d.get("servicio") or {})
        d["servicio"] = {"accesible": bool(accesible), "momento": ahora.isoformat(timespec="seconds"),
                         "latencia_ms": int(latencia_ms if (prueba and latencia_ms is not None)
                                            else previo.get("latencia_ms") or 0),
                         "prueba": ahora.isoformat(timespec="seconds") if prueba else previo.get("prueba"),
                         "error": legible(acceso.limpiar(error))}
        if accesible:
            d["caida"] = {}
        elif error and error != previo.get("error"):
            _agregar_error(d, f"Copernicus Marine: {error}")
        _marcar_dia(d, ahora)


def registrar_caida(motivo: str, contexto: str = "") -> None:
    """Falla de red: el servicio queda «caído» y las consultas no reintentan durante CAIDO_S (5 min), plazo que
    se duplica con cada falla seguida hasta CAIDO_MAX_S (30 min); un contacto exitoso lo reinicia.

    `motivo` se guarda limpio en la caída y en el servicio (lo leen los avisos de CUALQUIER consulta durante
    el plazo); `contexto` (p. ej. «Descarga de thetao: ») solo va al registro de errores."""
    ahora = _ahora()
    with _transaccion() as d:
        previa = d.get("caida") or {}
        seguidas = int(previa.get("seguidas") or 0) + 1 if previa else 1
        plazo = min(CAIDO_S * 2 ** (seguidas - 1), CAIDO_MAX_S)
        d["caida"] = {"desde": ahora.isoformat(timespec="seconds"),
                      "hasta": (ahora + timedelta(seconds=plazo)).isoformat(timespec="seconds"),
                      "motivo": legible(acceso.limpiar(motivo)), "seguidas": seguidas}
        previo = dict(d.get("servicio") or {})
        d["servicio"] = {**previo, "accesible": False, "momento": ahora.isoformat(timespec="seconds"),
                         "error": legible(acceso.limpiar(motivo))}
        _agregar_error(d, f"Copernicus Marine: {contexto}{motivo}")
        _marcar_dia(d, ahora)


def registrar_verificacion(resultado: dict) -> None:
    """Verificación completa (credenciales, metadatos, lectura de prueba)."""
    ahora = _ahora()
    with _transaccion() as d:
        registro = {"momento": ahora.isoformat(timespec="seconds"),
                    "credenciales": resultado.get("credenciales"),
                    "lectura_ok": resultado.get("lectura_ok"),
                    "latencia_ms": int(resultado.get("latencia_ms") or 0),
                    "error": legible(acceso.limpiar(resultado.get("error") or "")),
                    "estado": resultado.get("estado")}
        d["verificaciones"].append(registro)
        d["verificaciones"] = d["verificaciones"][-50:]
        _marcar_dia(d, ahora)


def registrar_descarga(fragmentos: int, segundos: float) -> None:
    if fragmentos <= 0 and segundos <= 0:
        return
    with _transaccion() as d:
        c = d.setdefault("descargas", {"fragmentos": 0, "segundos": 0.0, "ultima": None})
        c["fragmentos"] = int(c.get("fragmentos") or 0) + int(fragmentos)
        c["segundos"] = round(float(c.get("segundos") or 0.0) + float(segundos), 1)
        c["ultima"] = _ahora().isoformat(timespec="seconds")


def registrar_sincronizacion(resumen: dict) -> None:
    ahora = _ahora()
    resumen = json.loads(json.dumps(resumen, default=str))
    resumen["detalle_errores"] = [legible(acceso.limpiar(e), 400) for e in resumen.get("detalle_errores", [])]
    with _transaccion() as d:
        d["sincronizaciones"].append(resumen)
        d["sincronizaciones"] = d["sincronizaciones"][-30:]
        d["ultima_sincronizacion"] = resumen.get("fin") or ahora.isoformat(timespec="seconds")
        for e in resumen.get("detalle_errores", [])[:5]:
            _agregar_error(d, f"Sincronización: {e}")
        _marcar_dia(d, ahora)


def marcar_verificando(activo: bool) -> None:
    (_verificando.set if activo else _verificando.clear)()


def verificando() -> bool:
    return _verificando.is_set()


# ---------------------------------------------------------------------------
# Evaluación (pura: solo lee lo persistido y la configuración)
# ---------------------------------------------------------------------------
def _credenciales(d: dict) -> str:
    """validas | invalidas | ausentes | sin_verificar | error (sin red)."""
    if not acceso.credenciales_presentes():
        return "ausentes"
    c = d.get("credenciales") or {}
    if not c.get("estado"):
        return "sin_verificar"
    if c.get("firma") != acceso.firma_credenciales():
        return "sin_verificar"            # el usuario volvió a ejecutar «copernicusmarine login»
    return str(c["estado"])


def _caida(d: dict, ahora: datetime) -> tuple[bool, datetime | None, str]:
    c = d.get("caida") or {}
    hasta = _dt(c.get("hasta"))
    if not hasta or hasta <= ahora:
        return False, None, ""
    return True, hasta, str(c.get("motivo") or "")


def _metadatos_vigentes(d: dict, ahora: datetime) -> dict | None:
    m = d.get("metadatos") or {}
    consultado = _dt(m.get("consultado"))
    if not consultado or ahora - consultado > timedelta(hours=METADATOS_VIGENTES_H) or not m.get("fin_tiempo"):
        return None
    return m


def boletin_vigente() -> date:
    """Miércoles W del último boletín semanal (por metadatos vigentes o, si no, por calendario). Sin red."""
    with _bloqueo:
        m = _metadatos_vigentes(_cargar(), _ahora())
    return calendario.boletin_semanal(calendario.ahora_utc(), _fecha(m.get("fin_tiempo")) if m else None)


def ultimo_dia_analisis() -> date:
    """W − 2 según metadatos vigentes o calendario; sin red."""
    return boletin_vigente() - timedelta(days=2)


def credenciales_estado() -> str:
    with _bloqueo:
        return _credenciales(_cargar())


def caida_vigente() -> tuple[bool, datetime | None, str]:
    with _bloqueo:
        return _caida(_cargar(), _ahora())


def acceso_posible() -> bool:
    """Paquete, red, modo no simulado, credenciales presentes y no inválidas (sin mirar caídas recientes)."""
    if modo_configurado() == "simulado" or sin_red() or not acceso.paquete_instalado():
        return False
    return credenciales_estado() not in ("ausentes", "invalidas")


def acceso_disponible() -> bool:
    """Se puede descargar AHORA: acceso posible y sin caída reciente (y, sin guardia anti-prompt, credenciales
    verificadas como válidas)."""
    if not acceso_posible():
        return False
    if caida_vigente()[0]:
        return False
    if acceso._modulo is not None and not acceso.guardia_instalada() and credenciales_estado() != "validas":
        return False
    return True


def inicial_minuscula(texto: str) -> str:
    """«No se pudo…» → «no se pudo…» para ir tras dos puntos (deja igual las siglas: «ARCO…»)."""
    return texto[0].lower() + texto[1:] if len(texto) > 1 and texto[0].isupper() and texto[1].islower() else texto


def motivo_sin_acceso() -> str:
    """"" si hay acceso; si no, una frase corta en español (para avisos y mensajes).

    Sin paréntesis propios: los avisos la insertan tras dos puntos o entre paréntesis, y anidarlos la vuelve
    ilegible. Tras una caída dice solo la falla de red (sin el contexto de la consulta que la sufrió)."""
    if modo_configurado() == "simulado":
        return "modo simulado forzado, SOME_COPERNICUS_MODO=simulado"
    if sin_red():
        return "modo sin red, SOME_COPERNICUS_SIN_RED=1"
    if not acceso.paquete_instalado():
        return "el paquete copernicusmarine no está instalado, instálelo con pip install -r requirements-copernicus.txt"
    cred = credenciales_estado()
    if cred == "ausentes":
        return "sin credenciales de Copernicus Marine: ejecute «copernicusmarine login»"
    if cred == "invalidas":
        return "las credenciales de Copernicus Marine no son válidas: ejecute «copernicusmarine login --force-overwrite»"
    caido, hasta, motivo = caida_vigente()
    if caido:
        return (f"{inicial_minuscula(motivo.strip().rstrip('.')) or 'falla de red con Copernicus Marine'}; no se "
                f"reintenta hasta las {hasta.strftime('%H:%M')}")
    if acceso._modulo is not None and not acceso.guardia_instalada() and cred != "validas":
        return "credenciales sin verificar y sin guardia anti-prompt en esta versión del toolbox"
    return ""


def modo() -> str:
    """«real» | «simulado» (regla del diseño, §3.12)."""
    mc = modo_configurado()
    if mc in ("simulado", "real"):
        return mc
    try:
        if almacen.hay_datos():
            return "real"
    except Exception:  # noqa: BLE001
        pass
    return "real" if acceso_posible() else "simulado"


def fecha_corte() -> date | None:
    """Fecha de corte efectiva: con acceso, el último día de análisis; sin acceso, el último día en caché
    (o, si la caché está vacía, el último día de análisis por calendario); en modo simulado, None."""
    if modo() == "simulado":
        return None
    if acceso_disponible():
        return ultimo_dia_analisis()
    try:
        ultimo = almacen.ultimo_dia()
    except Exception:  # noqa: BLE001
        ultimo = None
    return ultimo or ultimo_dia_analisis()


def _evaluar(d: dict, ahora: datetime) -> dict:
    """{"estado", "definitivo", "clave", "latencia_ms", "momento"} a partir de lo persistido (sin red)."""
    if sin_red():
        return {"estado": "sin_conexion", "definitivo": True, "clave": "sin_red", "latencia_ms": 0, "momento": None}
    if not acceso.paquete_instalado():
        return {"estado": "sin_conexion", "definitivo": True, "clave": "no_instalado", "latencia_ms": 0, "momento": None}
    cred = _credenciales(d)
    s = d.get("servicio") or {}
    momento = _dt(s.get("momento"))
    if cred == "ausentes":
        return {"estado": "sin_conexion", "definitivo": True, "clave": "ausentes", "latencia_ms": 0, "momento": momento}
    if cred == "invalidas":
        return {"estado": "sin_conexion", "definitivo": True, "clave": "invalidas", "latencia_ms": 0,
                "momento": momento}
    caido = _caida(d, ahora)[0]
    if caido or s.get("accesible") is False:
        return {"estado": "sin_conexion", "definitivo": True, "clave": "sin_conexion", "latencia_ms": 0,
                "momento": momento}
    if cred == "error":
        return {"estado": "sin_conexion", "definitivo": True, "clave": "error_credenciales", "latencia_ms": 0,
                "momento": momento}
    if cred == "sin_verificar" or s.get("accesible") is None:
        return {"estado": "degradada", "definitivo": False, "clave": "sin_verificar", "latencia_ms": 0,
                "momento": momento}
    latencia = int(s.get("latencia_ms") or 0)
    base = {"definitivo": True, "latencia_ms": latencia, "momento": momento}
    if latencia >= LATENCIA_ALTA_MS:
        return {**base, "estado": "degradada", "clave": "lenta"}
    if not momento or ahora - momento > timedelta(hours=VERIFICACION_VIGENTE_H):
        return {**base, "estado": "degradada", "clave": "no_vigente", "definitivo": False}
    m = _metadatos_vigentes(d, ahora)
    if m is not None:
        utc = calendario.ahora_utc()
        if calendario.boletin_semanal(utc, _fecha(m.get("fin_tiempo"))) < calendario.boletin_semanal(utc, None):
            return {**base, "estado": "degradada", "clave": "atrasado"}
        inicio_act, fin_act = m.get("arco_updating_start_date"), m.get("arco_updated_date")
        # Como en el toolbox: solo los datos posteriores a arco_updating_start_date pueden no estar al día. Si
        # esa fecha cae después del último día de análisis que sirve el SOME (p. ej. en el pronóstico), no lo afecta.
        ultimo = calendario.ultimo_dia_analisis(utc, _fecha(m.get("fin_tiempo")))
        if inicio_act and (not fin_act or str(inicio_act) > str(fin_act)) and str(inicio_act)[:10] <= ultimo.isoformat():
            return {**base, "estado": "degradada", "clave": "actualizando"}
    ultima_sinc = d["sincronizaciones"][-1] if d.get("sincronizaciones") else None
    if ultima_sinc and int(ultima_sinc.get("errores") or 0):
        return {**base, "estado": "degradada", "clave": "errores_sinc"}
    return {**base, "estado": "operativa", "clave": "operativa"}


def _marcar_dia(d: dict, momento: datetime) -> None:
    """Anota el peor estado definitivo del día y si Copernicus Marine respondió ese día."""
    ev = _evaluar(d, momento)
    clave = momento.date().isoformat()
    s = d.get("servicio") or {}
    contacto = _dt(s.get("momento"))
    if s.get("accesible") and contacto and contacto.date() == momento.date():
        d.setdefault("historial_origen", {})[clave] = "copernicus"
    if ev["definitivo"]:
        previo = d["historial"].get(clave)
        if previo is None or _GRAVEDAD[ev["estado"]] > _GRAVEDAD.get(previo, 0):
            d["historial"][clave] = ev["estado"]
    limite = (momento.date() - timedelta(days=366)).isoformat()
    for tabla in (d["historial"], d.setdefault("historial_origen", {})):
        for k in [k for k in tabla if k < limite]:
            del tabla[k]


def _texto_cache() -> str:
    try:
        ultimo = almacen.ultimo_dia()
    except Exception:  # noqa: BLE001
        ultimo = None
    if ultimo is None:
        return ("la caché local de Copernicus Marine está vacía (ejecute python -m "
                "backend.herramientas.preparar_copernicus)")
    return f"se sirve la caché local (hasta el {cfg.fmt_fecha(ultimo)})"


def _mensaje(ev: dict, d: dict, ahora: datetime) -> str:
    cache = _texto_cache()
    respaldo = (f"Mientras tanto {cache}; lo que no esté en ella queda sin dato o, si falta todo, se responde con "
                "datos simulados, señalados como tales.")
    clave = ev["clave"]
    if clave == "sin_red":
        return (f"Modo sin red (SOME_COPERNICUS_SIN_RED=1): no se consulta Copernicus Marine; {cache}; lo que no "
                "esté en ella queda sin dato o, si falta todo, se responde con datos simulados, señalados como tales.")
    if clave == "no_instalado":
        return f"{acceso.MENSAJE_NO_INSTALADO}. {respaldo}"
    if clave == "ausentes":
        return f"{acceso.MENSAJE_SIN_CREDENCIALES}. {respaldo}"
    if clave == "invalidas":
        c = d.get("credenciales") or {}
        cuando = f" (verificado el {cfg.fmt_fecha_hora(c['verificado'])})" if c.get("verificado") else ""
        return (f"Las credenciales de Copernicus Marine no son válidas{cuando}: vuelva a ejecutar «copernicusmarine "
                f"login --force-overwrite». {respaldo}")
    if clave == "error_credenciales":
        c = d.get("credenciales") or {}
        return (f"No se pudieron verificar las credenciales de Copernicus Marine "
                f"({(c.get('error') or 'sin conexión con el sistema de autenticación').rstrip('.')}). {respaldo}")
    if clave == "sin_conexion":
        caido, hasta, motivo = _caida(d, ahora)
        s = d.get("servicio") or {}
        cuando = f" (último intento: {cfg.fmt_fecha_hora(s['momento'])})" if s.get("momento") else ""
        motivo = inicial_minuscula((motivo or s.get("error") or "sin conexión").rstrip("."))
        reintento = f"; las consultas no reintentan hasta las {hasta.strftime('%H:%M')}" if caido and hasta else ""
        return f"No se pudo conectar con Copernicus Marine{cuando}: {motivo}{reintento}. {respaldo}"
    if clave == "sin_verificar":
        if verificando():
            return f"Verificando la conexión con Copernicus Marine… {respaldo}"
        return (f"Aún no se ha verificado la conexión con Copernicus Marine (la sincronización lo hace al arrancar "
                f"y cada 6 horas). {respaldo}")
    w = boletin_vigente()
    corte = w - timedelta(days=2)
    analisis = f"Último día de análisis: {cfg.fmt_fecha(corte)} (boletín semanal del {cfg.fmt_fecha(w)})."
    lat = cfg.fmt_num(ev["latencia_ms"], 0)
    if clave == "lenta":
        return f"Copernicus Marine responde con retraso (lectura de prueba en {cfg.fmt_num(lat, 0)} ms); {cache}. {analisis}"
    if clave == "no_vigente":
        return (f"La última verificación de Copernicus Marine es del {cfg.fmt_fecha_hora(ev['momento'])}; {cache}. "
                f"{analisis}")
    if clave == "atrasado":
        return (f"Los metadatos de Copernicus Marine aún no muestran el boletín semanal esperado por calendario; "
                f"{analisis}")
    if clave == "actualizando":
        return f"Copernicus Marine está actualizando el dataset en ARCO; las lecturas pueden fallar o tardar. {analisis}"
    if clave == "errores_sinc":
        n = int((d["sincronizaciones"][-1] or {}).get("errores") or 0)
        return (f"La última sincronización tuvo {n} {'error' if n == 1 else 'errores'}; se reintentará en 30 minutos. "
                f"{analisis}")
    if not ev["latencia_ms"]:
        cuando = cfg.fmt_fecha_hora(ev["momento"]) if ev.get("momento") else "—"
        return f"Copernicus Marine responde con normalidad (última descarga correcta el {cuando}). {analisis}"
    return f"Copernicus Marine responde con normalidad (lectura de prueba en {cfg.fmt_num(lat, 0)} ms). {analisis}"


_memo_estado: tuple | None = None
MEMO_ESTADO_S = 2.0     # el conector lo consulta varias veces por petición


def estado_actual() -> dict:
    """Estado RF-21 sin red; nunca lanza (ante un error devuelve sin_conexion con el motivo).

    Se recuerda 2 s mientras no cambien la configuración, estado.json, la caché ni las credenciales."""
    global _memo_estado
    try:
        clave = (modo_configurado(), sin_red(), str(ruta_estado()), _mtime(), _escrituras, almacen.marca(), verificando(),
                 acceso.firma_credenciales(), acceso.paquete_instalado(), acceso.guardia_instalada())
    except Exception:  # noqa: BLE001
        clave = None
    memo = _memo_estado
    if clave is not None and memo is not None and memo[0] == clave and time.monotonic() - memo[1] < MEMO_ESTADO_S:
        return dict(memo[2])
    resultado = _estado_actual()
    if clave is not None:
        _memo_estado = (clave, time.monotonic(), dict(resultado))
    return resultado


def _estado_actual() -> dict:
    ahora = _ahora()
    try:
        if modo_configurado() == "simulado":
            return {"estado": "operativa", "mensaje": MENSAJE_MODO_SIMULADO, "latencia_ms": LATENCIA_SIMULADA_MS,
                    "ultimo_chequeo": ahora.isoformat(timespec="seconds"), "fecha_corte": FECHA_CORTE_SIMULADA,
                    "definitivo": True, "modo": "simulado"}
        with _bloqueo:
            d = _cargar()
            ev = _evaluar(d, ahora)
            mensaje = _mensaje(ev, d, ahora)
            momentos = [m for m in (_dt((d.get("servicio") or {}).get("momento")),
                                    _dt((d.get("credenciales") or {}).get("verificado")),
                                    _dt((d.get("metadatos") or {}).get("consultado"))) if m]
        return {"estado": ev["estado"], "mensaje": mensaje,
                "latencia_ms": int(ev["latencia_ms"]) if ev["estado"] != "sin_conexion" else 0,
                "ultimo_chequeo": (max(momentos) if momentos else ahora).isoformat(timespec="seconds"),
                "fecha_corte": fecha_corte(), "definitivo": bool(ev["definitivo"]), "modo": modo()}
    except Exception as e:  # noqa: BLE001 — el estado nunca tumba la API
        log.exception("Copernicus: error al evaluar el estado")
        return {"estado": "sin_conexion", "mensaje": f"No se pudo evaluar el estado de Copernicus Marine: {legible(e)}",
                "latencia_ms": 0, "ultimo_chequeo": ahora.isoformat(timespec="seconds"), "fecha_corte": None,
                "definitivo": True, "modo": "simulado"}


def historial_estado(dias: int = 14) -> list[dict]:
    """Del más antiguo al más reciente: {"fecha", "estado", "origen", "origen_texto"}; nunca lanza."""
    hoy = date.today()
    salida = []
    try:
        if modo_configurado() == "simulado":
            for atras in range(dias - 1, -1, -1):
                salida.append({"fecha": (hoy - timedelta(days=atras)).isoformat(),
                               "estado": "degradada" if atras == 5 else "operativa", "origen": None,
                               "origen_texto": TEXTO_ORIGEN_HISTORIAL["simulado"]})
            return salida
        with _bloqueo:
            d = _cargar()
            hist = dict(d.get("historial") or {})
            origen = dict(d.get("historial_origen") or {})
        for atras in range(dias - 1, -1, -1):
            clave = (hoy - timedelta(days=atras)).isoformat()
            estado = hist.get(clave, "sin_registro")
            o = origen.get(clave) or (None if estado == "sin_registro" else "cache")
            salida.append({"fecha": clave, "estado": estado, "origen": o, "origen_texto": TEXTO_ORIGEN_HISTORIAL[o]})
        if sin_red() and salida:
            salida[-1].update({"estado": "sin_conexion", "origen": "cache",
                               "origen_texto": TEXTO_ORIGEN_HISTORIAL["cache"]})
        return salida
    except Exception:  # noqa: BLE001
        log.exception("Copernicus: error al leer el historial de estado")
        return [{"fecha": (hoy - timedelta(days=atras)).isoformat(), "estado": "sin_registro", "origen": None,
                 "origen_texto": TEXTO_ORIGEN_HISTORIAL[None]} for atras in range(dias - 1, -1, -1)]


# ---------------------------------------------------------------------------
# Detalle para «Estado de fuentes» y piezas del resumen (sin red)
# ---------------------------------------------------------------------------
def texto_modo() -> str:
    mc = modo_configurado()
    if mc == "simulado":
        return "Modo simulado forzado (SOME_COPERNICUS_MODO=simulado)"
    if modo() == "real":
        sufijo = " (modo real estricto, sin respaldo simulado)" if mc == "real" else ""
        if acceso_disponible():
            return f"Datos reales (caché local y Copernicus Marine){sufijo}"
        falta = "lo que falte no se sirve" if mc == "real" else "lo que falte, simulado y señalado"
        return f"Datos reales de la caché local{sufijo}; {falta}. Sin acceso a Copernicus Marine: {motivo_sin_acceso()}"
    return f"Datos simulados: {motivo_sin_acceso() or 'sin acceso a Copernicus Marine'}"


def bloque_paquete() -> dict:
    return {"instalado": acceso.paquete_instalado(), "version": acceso.version_paquete()}


def bloque_credenciales() -> dict:
    with _bloqueo:
        d = _cargar()
        cred = _credenciales(d)
        c = dict(d.get("credenciales") or {})
    if cred == "ausentes":
        texto = acceso.MENSAJE_SIN_CREDENCIALES + "."
    elif cred == "invalidas":
        texto = "No son válidas: vuelva a ejecutar «copernicusmarine login --force-overwrite»."
    elif cred == "validas":
        texto = f"Válidas (verificadas el {cfg.fmt_fecha_hora(c.get('verificado'))})."
    elif cred == "error":
        texto = f"No se pudieron verificar: {(c.get('error') or 'sin conexión').rstrip('.')}."
    else:
        texto = "Presentes, aún sin verificar (la sincronización las verifica al arrancar)."
    return {"estado": cred, "texto": texto, "verificado": c.get("verificado") if cred != "ausentes" else None}


def bloque_servicio() -> dict:
    ahora = _ahora()
    with _bloqueo:
        d = _cargar()
        s = dict(d.get("servicio") or {})
        caido, hasta, motivo = _caida(d, ahora)
        cred = _credenciales(d)
    momento = _dt(s.get("momento"))
    lat = s.get("latencia_ms")
    if modo_configurado() == "simulado":
        estado, detalle = "simulado", "SOME_COPERNICUS_MODO=simulado: no se consulta el servicio."
    elif sin_red():
        estado, detalle = "sin_red", "SOME_COPERNICUS_SIN_RED=1: no se contacta el servicio."
    elif not acceso.paquete_instalado():
        estado, detalle = "no_instalado", acceso.MENSAJE_NO_INSTALADO + "."
    elif cred in ("ausentes", "invalidas"):
        # Sin credenciales válidas no se puede leer ARCO: la última lectura persistida no prueba que hoy responda
        # («Sin verificar», en gris; el último contacto sigue en `ultimo_contacto`)
        estado, detalle = "sin_registro", "No se puede leer sin credenciales válidas de Copernicus Marine."
    elif s.get("accesible") is True and not caido:
        lento = int(lat or 0) >= LATENCIA_ALTA_MS
        vigente = bool(momento and ahora - momento <= timedelta(hours=VERIFICACION_VIGENTE_H))
        estado = "degradada" if lento or not vigente else "operativa"
        detalle = (f"Responde con retraso (lectura de prueba en {cfg.fmt_num(lat, 0)} ms)." if lento else
                   f"Responde (lectura de prueba en {cfg.fmt_num(lat, 0)} ms)." if vigente and lat else
                   f"Responde (último contacto el {cfg.fmt_fecha_hora(momento)}).")
    elif s.get("accesible") is False or caido:
        estado = "sin_conexion"
        detalle = f"No responde: {inicial_minuscula((motivo or s.get('error') or 'sin conexión').rstrip('.'))}"
        if caido and hasta:
            detalle += f"; las consultas no reintentan hasta las {hasta.strftime('%H:%M')}"
        detalle += "."
    else:
        estado, detalle = "sin_registro", "Aún no se ha verificado."
    return {"estado": estado, "texto_estado": TEXTO_ESTADO_SERVICIO[estado], "detalle": detalle,
            "latencia_ms": int(lat) if lat and estado in ("operativa", "degradada") else None,
            "ultimo_contacto": _iso(momento), "servidor": SERVIDOR_ARCO}


def bloque_analisis() -> dict:
    ahora = _ahora()
    with _bloqueo:
        m = _metadatos_vigentes(_cargar(), ahora)
        meta = dict(_cargar().get("metadatos") or {})
    if modo_configurado() == "simulado":
        return {"ultimo_dia": None, "ultimo_definitivo": None, "boletin_semanal": None, "fin_pronostico": None,
                "regla": "calendario", "consultado": None, "texto": "Modo simulado: no se consulta el análisis."}
    w = boletin_vigente()
    corte, definitivo = w - timedelta(days=2), w - timedelta(days=9)
    return {"ultimo_dia": corte.isoformat(), "ultimo_definitivo": definitivo.isoformat(),
            "boletin_semanal": w.isoformat(), "fin_pronostico": meta.get("fin_tiempo"),
            "regla": "metadatos" if m else "calendario", "consultado": meta.get("consultado"),
            "texto": (f"Análisis NRT hasta el {cfg.fmt_fecha(corte)} (boletín del {cfg.fmt_fecha(w)}); mejor análisis "
                      f"hasta el {cfg.fmt_fecha(definitivo)}; el pronóstico no se sirve."
                      + ("" if m else " Fechas según el calendario semanal del producto (metadatos no consultados "
                                      "aún o no vigentes)."))}


def bloque_climatologia() -> dict:
    try:
        pares = almacen.climatologias_en_cache()
        periodo = almacen.periodo_climatologia()
    except Exception:  # noqa: BLE001
        pares, periodo = [], None
    return {"producto": PRODUCTO_GLORYS, "doi": DOI_GLORYS, "periodo": periodo, "en_cache": len(pares),
            "total": TOTAL_PARES_CLIMATOLOGIA, "pares": pares}


def bloque_cache() -> dict:
    try:
        c = almacen.conteo_dias()
        tam = almacen.tamano_bytes()
    except Exception:  # noqa: BLE001
        c, tam = {"dias_con_dato": 0, "dias_nrt": 0, "ultimo_definitivo": None, "ultimo_nrt": None,
                  "ultimo_dia": None}, 0
    return {"fecha_corte": _iso(c.get("ultimo_dia")), "ultimo_definitivo": _iso(c.get("ultimo_definitivo")),
            "ultimo_nrt": _iso(c.get("ultimo_nrt")), "dias_con_dato": int(c.get("dias_con_dato") or 0),
            "dias_nrt": int(c.get("dias_nrt") or 0), "tamano_bytes": int(tam)}


def detalle() -> dict:
    """Bloque que se fusiona en GET /api/fuentes/estado (§2.4); sin red, nunca lanza."""
    try:
        clim = bloque_climatologia()
        clim.pop("pares", None)
        return {"modo_sin_red": sin_red(),
                "copernicus": {"modo": modo(), "modo_configurado": modo_configurado(), "texto_modo": texto_modo(),
                               "acceso": {"disponible": acceso_disponible(), "motivo": motivo_sin_acceso()},
                               "paquete": bloque_paquete(), "credenciales": bloque_credenciales(),
                               "servicio": bloque_servicio(), "analisis": bloque_analisis(),
                               "climatologia": clim, "cache": bloque_cache()}}
    except Exception as e:  # noqa: BLE001
        log.exception("Copernicus: error al armar el detalle de estado")
        return {"modo_sin_red": sin_red(), "copernicus": {"modo": "simulado", "modo_configurado": modo_configurado(),
                                                           "texto_modo": f"Estado no disponible: {legible(e)}",
                                                           "acceso": {"disponible": False, "motivo": legible(e)}}}


def errores_recientes(n: int = 10) -> list[dict]:
    with _bloqueo:
        lista = list(_cargar().get("errores_recientes") or [])
    return [{"momento": x.get("momento"), "mensaje": legible(x.get("mensaje"), 400)}
            for x in lista[-n:] if isinstance(x, dict)]


__all__ = ["estado_actual", "historial_estado", "detalle", "modo", "acceso_disponible", "motivo_sin_acceso",
           "fecha_corte", "ultimo_dia_analisis", "boletin_vigente", "acortar", "legible"]
