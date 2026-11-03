"""Estado de NOAA OISST (RF-21), modo y acceso, persistidos en `datos/noaa/estado.json`.

Guarda lo último verificado de verdad:

* **servidores** (PSL, NCEI, CPC): la última observación de cada uno (verificación
  liviana con su latencia, o resultado de una descarga), la limitación de ritmo
  (HTTP 429) y la pausa tras fallos seguidos (las consultas no reintentan; la
  sincronización sí);
* **publicación**: último día del archivo diario de PSL (de su `dataset.xml`) y,
  del listado de NCEI, el último día final, el último preliminar y el momento en que
  NCEI publicó cada final reciente (decide si un día de PSL es final);
* historial diario (el peor estado de cada día y qué servidores respondieron),
  verificaciones, sincronizaciones, descargas y errores recientes (en español, sin
  texto técnico crudo).

Todo lo que se consulta aquí es SIN RED y no lanza: `estado_actual()`,
`historial_estado()`, `detalle()`, `modo()`, `acceso_disponible()`, `fecha_corte()`.
Las escrituras son transacciones bajo un bloqueo entre procesos
(`.bloqueos/estado.lock`), como en CHIRPS y Copernicus.
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
from . import almacen
from .constantes import (CPC, FECHA_CORTE_SIMULADA, LATENCIA_ALTA_MS, LATENCIA_SIMULADA_MS, MARGEN_FINAL_H, NCEI,
                         NOMBRE_CORTO_SERVIDOR, NOMBRE_SERVIDOR, PERIODOS, PSL, RETRASO_MAXIMO_DIAS,
                         VERIFICACION_VIGENTE_H, modo_configurado, ruta_bloqueos, ruta_estado, sin_red)

log = logging.getLogger("some.noaa")
ESPERA_BLOQUEO_S = 30

_GRAVEDAD = {"operativa": 0, "degradada": 1, "sin_conexion": 2}
TEXTO_ORIGEN_HISTORIAL = {"psl+ncei": "NOAA PSL y NOAA NCEI", "psl": "NOAA PSL", "ncei": "NOAA NCEI (respaldo)",
                          "cache": "Caché local", None: "Sin registro", "simulado": "Simulación del prototipo"}
TEXTO_ESTADO_SERVIDOR = {"operativa": "Operativa", "degradada": "Degradada", "sin_conexion": "Sin conexión",
                         "sin_registro": "Sin verificar", "sin_red": "No se consulta (modo sin red)",
                         "simulado": "No se consulta (modo simulado)", "pausa": "En pausa tras fallos"}
MENSAJE_MODO_SIMULADO = ("Modo simulado (SOME_NOAA_MODO=simulado): no se consulta NOAA; los datos de esta fuente "
                         "son simulados.")

_bloqueo = threading.RLock()
_datos: dict | None = None
_cargado: tuple[str, int] | None = None
_verificando = threading.Event()
_escrituras = 0


def _vacio() -> dict:
    return {"servidores": {}, "pausas": {}, "publicacion": {}, "historial": {}, "historial_origen": {},
            "verificaciones": [], "sincronizaciones": [], "ultima_sincronizacion": None,
            "descargas": {"peticiones": 0, "bytes": 0, "segundos": 0.0, "ultima": None, "por_servidor": {}},
            "errores_recientes": []}


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
        log.error("NOAA: no se pudo guardar el estado en %s: %s", ruta, e)
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
    global _datos
    with _bloqueo, _bloqueo_procesos(ruta_bloqueos() / "estado.lock", ESPERA_BLOQUEO_S):
        _datos = None
        d = _cargar()
        yield d
        _guardar()


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


def inicial_minuscula(texto: str) -> str:
    """«No se pudo…» → «no se pudo…» para ir tras dos puntos (deja igual las siglas: «NOAA…»)."""
    return texto[0].lower() + texto[1:] if len(texto) > 1 and texto[0].isupper() and texto[1].islower() else texto


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------
def _agregar_error(d: dict, mensaje: str) -> None:
    d["errores_recientes"].append({"momento": _ahora().isoformat(timespec="seconds"), "mensaje": legible(mensaje, 400)})
    d["errores_recientes"] = d["errores_recientes"][-20:]


def registrar_error(mensaje: str) -> None:
    try:
        with _transaccion() as d:
            _agregar_error(d, mensaje)
    except Exception as e:  # noqa: BLE001 — el registro nunca debe impedir seguir
        log.warning("NOAA: no se pudo registrar el error «%s»: %s", mensaje, e)


def registrar_servidor(servidor: str, accesible: bool, latencia_ms: int | None = None, error: str = "", *,
                       limitado: bool = False, verificacion: bool = False) -> None:
    """Observación de un servidor (una verificación liviana con su latencia, o el resultado de una descarga).
    Una descarga solo escribe si cambia la accesibilidad, si limita (429) o si pasó más de una hora."""
    ahora = _ahora()
    with _bloqueo:
        previo = dict((_cargar().get("servidores") or {}).get(servidor) or {})
    momento_previo = _dt(previo.get("momento"))
    if (not verificacion and not limitado and previo.get("accesible") == bool(accesible) and momento_previo
            and ahora - momento_previo < timedelta(hours=1)):
        return
    with _transaccion() as d:
        servidores = d.setdefault("servidores", {})
        previo = dict(servidores.get(servidor) or {})
        nuevo = {**previo, "accesible": bool(accesible), "momento": ahora.isoformat(timespec="seconds"),
                 "error": legible(error)}
        if latencia_ms is not None and verificacion:       # la de una descarga (10–30 s) no es latencia
            nuevo["latencia_ms"] = int(latencia_ms)
        if verificacion:
            nuevo["verificado"] = ahora.isoformat(timespec="seconds")
        if limitado:
            nuevo["limitado"] = ahora.isoformat(timespec="seconds")
        servidores[servidor] = nuevo
        if accesible:
            d.setdefault("pausas", {}).pop(servidor, None)
        elif error and error != previo.get("error"):
            _agregar_error(d, f"{NOMBRE_CORTO_SERVIDOR.get(servidor, servidor)}: {error}")
        _marcar_dia(d, ahora)


def registrar_pausa(servidor: str, hasta: datetime, motivo: str) -> None:
    with _transaccion() as d:
        d.setdefault("pausas", {})[servidor] = {"hasta": hasta.isoformat(timespec="seconds"), "motivo": legible(motivo)}
        _marcar_dia(d, _ahora())


def pausa_vigente(servidor: str) -> tuple[bool, datetime | None, str]:
    with _bloqueo:
        p = dict((_cargar().get("pausas") or {}).get(servidor) or {})
    hasta = _dt(p.get("hasta"))
    if not hasta or hasta <= _ahora():
        return False, None, ""
    return True, hasta, str(p.get("motivo") or "")


def registrar_publicacion(info: dict) -> None:
    """Fusiona lo publicado: {"psl_fin": iso} (dataset.xml de PSL) y/o, del listado de NCEI,
    {"ncei_ultimo_final", "ncei_ultimo_preliminar", "finales": {iso_día: publicado_iso}}."""
    ahora = _ahora().isoformat(timespec="seconds")
    with _transaccion() as d:
        p = d.setdefault("publicacion", {})
        if info.get("psl_fin"):
            p["psl_fin"] = info["psl_fin"]
            p["psl_consultado"] = ahora
        if "ncei_ultimo_final" in info:
            p["ncei_ultimo_final"] = info.get("ncei_ultimo_final")
            p["ncei_ultimo_preliminar"] = info.get("ncei_ultimo_preliminar")
            finales = dict(p.get("finales") or {})
            finales.update(info.get("finales") or {})
            # solo los últimos ~70 días (los anteriores son finales sin duda)
            limite = (date.today() - timedelta(days=70)).isoformat()
            p["finales"] = {k: v for k, v in finales.items() if k >= limite}
            p["preliminares"] = sorted(set(info.get("preliminares") or []))
            p["ncei_consultado"] = ahora


def registrar_verificacion(resultado: dict) -> None:
    ahora = _ahora()
    with _transaccion() as d:
        registro = {k: resultado.get(k) for k in ("momento", "psl", "ncei", "estado", "error")}
        registro["momento"] = registro.get("momento") or ahora.isoformat(timespec="seconds")
        registro["error"] = legible(resultado.get("error") or "")
        d["verificaciones"].append(registro)
        d["verificaciones"] = d["verificaciones"][-50:]
        _marcar_dia(d, ahora)


def registrar_descarga(servidor: str, n_bytes: int, segundos: float) -> None:
    try:
        with _transaccion() as d:
            c = d.setdefault("descargas", {"peticiones": 0, "bytes": 0, "segundos": 0.0, "ultima": None,
                                           "por_servidor": {}})
            c["peticiones"] = int(c.get("peticiones") or 0) + 1
            c["bytes"] = int(c.get("bytes") or 0) + int(n_bytes)
            c["segundos"] = round(float(c.get("segundos") or 0.0) + float(segundos), 1)
            c["ultima"] = _ahora().isoformat(timespec="seconds")
            ps = c.setdefault("por_servidor", {}).setdefault(servidor, {"peticiones": 0, "bytes": 0})
            ps["peticiones"] = int(ps.get("peticiones") or 0) + 1
            ps["bytes"] = int(ps.get("bytes") or 0) + int(n_bytes)
    except Exception:  # noqa: BLE001
        log.debug("NOAA: no se pudo registrar la descarga", exc_info=True)


def registrar_sincronizacion(resumen: dict) -> None:
    ahora = _ahora()
    resumen = json.loads(json.dumps(resumen, default=str))
    resumen["detalle_errores"] = [legible(e, 400) for e in resumen.get("detalle_errores", [])]
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
# Publicación: ¿un día de PSL es final o preliminar?
# ---------------------------------------------------------------------------
def tipo_publicado(d: date, obtenido: datetime | None = None) -> int:
    """FINAL (2) o PRELIMINAR (1) de un día obtenido de PSL en el momento `obtenido` (ahora, por defecto).

    Con el listado de NCEI: final si NCEI ya había publicado su versión final al menos MARGEN_FINAL_H horas
    antes (PSL la incorpora el mismo día: comprobado valor a valor) o si el día es anterior al último final
    listado y no figura su momento de publicación; si no, preliminar. Sin listado: preliminares los últimos
    15 días del archivo de PSL (o de hoy), como avisa PSL («Data less than 15 days old may be subject to
    revision»)."""
    from .constantes import DIAS_PRELIMINARES_ESTIMADOS, FINAL, PRELIMINAR
    obtenido = obtenido or _ahora()
    with _bloqueo:
        p = dict(_cargar().get("publicacion") or {})
    ultimo_final = _fecha(p.get("ncei_ultimo_final"))
    if ultimo_final is not None:
        if d > ultimo_final:
            return PRELIMINAR
        publicado = _dt((p.get("finales") or {}).get(d.isoformat()))
        if publicado is None:
            return FINAL
        return FINAL if obtenido - publicado >= timedelta(hours=MARGEN_FINAL_H) else PRELIMINAR
    referencia = _fecha(p.get("psl_fin")) or obtenido.date()
    return FINAL if d <= referencia - timedelta(days=DIAS_PRELIMINARES_ESTIMADOS) else PRELIMINAR


# ---------------------------------------------------------------------------
# Modo, acceso y fecha de corte (sin red)
# ---------------------------------------------------------------------------
def acceso_posible() -> bool:
    """Red permitida y modo no simulado (NOAA no pide credenciales)."""
    return modo_configurado() != "simulado" and not sin_red()


def acceso_disponible() -> bool:
    """Se puede descargar AHORA: acceso posible y algún servidor de datos (PSL o NCEI) fuera de pausa."""
    if not acceso_posible():
        return False
    return not (pausa_vigente(PSL)[0] and pausa_vigente(NCEI)[0])


def motivo_sin_acceso() -> str:
    """"" si hay acceso; si no, una frase corta en español (para avisos y mensajes), sin paréntesis propios."""
    if modo_configurado() == "simulado":
        return "modo simulado forzado, SOME_NOAA_MODO=simulado"
    if sin_red():
        return "modo sin red, SOME_NOAA_SIN_RED=1"
    psl, hasta, motivo = pausa_vigente(PSL)
    ncei, _h2, _m2 = pausa_vigente(NCEI)
    if psl and ncei:
        return (f"NOAA PSL y NOAA NCEI no respondieron en los últimos intentos: "
                f"{inicial_minuscula(motivo.rstrip('.')) or 'sin conexión'}; no se reintenta hasta las "
                f"{hasta.strftime('%H:%M') if hasta else '—'}")
    return ""


def modo() -> str:
    """«real» | «simulado»: simulado solo si se fuerza o si no hay red ni caché."""
    mc = modo_configurado()
    if mc in ("simulado", "real"):
        return mc
    try:
        if almacen.hay_datos():
            return "real"
    except Exception:  # noqa: BLE001
        pass
    return "real" if acceso_posible() else "simulado"


def _psl_fin_vigente(d: dict, ahora: datetime) -> date | None:
    p = d.get("publicacion") or {}
    consultado = _dt(p.get("psl_consultado"))
    if not consultado or ahora - consultado > timedelta(hours=VERIFICACION_VIGENTE_H):
        return None
    return _fecha(p.get("psl_fin"))


def fecha_corte() -> date | None:
    """Último día disponible: el último de la caché (lo que el SOME puede servir); con la caché vacía y acceso,
    el último publicado por PSL (verificado hace menos de 13 h); None en modo simulado o si aún no se sabe nada.

    No se adelanta al último día publicado mientras la caché no lo tenga: la sincronización lo trae después de
    verificarlo (20 s de pausa de cortesía y la descarga; varios minutos si PSL limita con 429, con el turno de
    descarga tomado), y mientras tanto la fecha por defecto del sistema mostraría un día sin dato (o, en un mapa
    de ese único día, el respaldo simulado). Las consultas siguen pidiendo en el momento los días AUSENTES dentro
    del periodo."""
    if modo() == "simulado":
        return None
    try:
        ultimo = almacen.ultimo_dia()
    except Exception:  # noqa: BLE001
        ultimo = None
    if ultimo is not None:
        return ultimo
    if acceso_disponible():
        with _bloqueo:
            return _psl_fin_vigente(_cargar(), _ahora())
    return None


def ultimo_publicado() -> date | None:
    """Último día publicado por PSL según la última verificación registrada (sin red; None si no hay)."""
    with _bloqueo:
        return _fecha((_cargar().get("publicacion") or {}).get("psl_fin"))


# ---------------------------------------------------------------------------
# Evaluación (pura: solo lee lo persistido y la configuración)
# ---------------------------------------------------------------------------
def _servidor(d: dict, servidor: str, ahora: datetime) -> dict:
    """{"estado": operativa|degradada|sin_conexion|sin_registro|pausa, "latencia_ms", "momento", "error", "limitado"}."""
    s = dict((d.get("servidores") or {}).get(servidor) or {})
    p = dict((d.get("pausas") or {}).get(servidor) or {})
    hasta = _dt(p.get("hasta"))
    momento = _dt(s.get("momento"))
    limitado = _dt(s.get("limitado"))
    base = {"latencia_ms": int(s.get("latencia_ms") or 0), "momento": momento, "error": str(s.get("error") or ""),
            "limitado": bool(limitado and ahora - limitado < timedelta(hours=1)), "verificado": _dt(s.get("verificado"))}
    if hasta and hasta > ahora:
        return {**base, "estado": "pausa", "hasta": hasta, "motivo": str(p.get("motivo") or "")}
    if s.get("accesible") is None:
        return {**base, "estado": "sin_registro"}
    if s.get("accesible") is False:
        return {**base, "estado": "sin_conexion"}
    if base["limitado"] or base["latencia_ms"] >= LATENCIA_ALTA_MS:
        return {**base, "estado": "degradada"}
    if not momento or ahora - momento > timedelta(hours=VERIFICACION_VIGENTE_H):
        return {**base, "estado": "degradada", "no_vigente": True}
    return {**base, "estado": "operativa"}


def _evaluar(d: dict, ahora: datetime) -> dict:
    """{"estado", "definitivo", "clave", "latencia_ms", "momento"} a partir de lo persistido (sin red)."""
    if sin_red():
        return {"estado": "sin_conexion", "definitivo": True, "clave": "sin_red", "latencia_ms": 0, "momento": None}
    psl, ncei = _servidor(d, PSL, ahora), _servidor(d, NCEI, ahora)
    momentos = [m for m in (psl["momento"], ncei["momento"]) if m]
    momento = max(momentos) if momentos else None
    lat = psl["latencia_ms"]
    base = {"definitivo": True, "latencia_ms": lat, "momento": momento}
    caido = {"sin_conexion", "pausa"}
    if psl["estado"] == "sin_registro" and ncei["estado"] == "sin_registro":
        return {**base, "estado": "degradada", "definitivo": False, "clave": "sin_verificar", "latencia_ms": 0}
    if psl["estado"] in caido and ncei["estado"] in caido | {"sin_registro"}:
        return {**base, "estado": "sin_conexion", "clave": "sin_conexion", "latencia_ms": 0}
    if psl["estado"] in caido | {"sin_registro"}:
        return {**base, "estado": "degradada", "clave": "solo_ncei", "latencia_ms": ncei["latencia_ms"]}
    if psl["limitado"]:
        return {**base, "estado": "degradada", "clave": "limitado"}
    if lat >= LATENCIA_ALTA_MS:
        return {**base, "estado": "degradada", "clave": "lenta"}
    if psl.get("no_vigente"):
        return {**base, "estado": "degradada", "clave": "no_vigente", "definitivo": False}
    if ncei["estado"] in caido:
        return {**base, "estado": "degradada", "clave": "sin_ncei"}
    psl_fin = _fecha((d.get("publicacion") or {}).get("psl_fin"))
    if psl_fin and psl_fin < ahora.date() - timedelta(days=RETRASO_MAXIMO_DIAS):
        return {**base, "estado": "degradada", "clave": "atrasado"}
    ultima_sinc = d["sincronizaciones"][-1] if d.get("sincronizaciones") else None
    if ultima_sinc and int(ultima_sinc.get("errores") or 0):
        return {**base, "estado": "degradada", "clave": "errores_sinc"}
    return {**base, "estado": "operativa", "clave": "operativa"}


def _marcar_dia(d: dict, momento: datetime) -> None:
    """Anota el peor estado definitivo del día y qué servidores de datos respondieron ese día."""
    ev = _evaluar(d, momento)
    clave = momento.date().isoformat()
    respondieron = []
    for servidor in (PSL, NCEI):
        s = (d.get("servidores") or {}).get(servidor) or {}
        contacto = _dt(s.get("momento"))
        if s.get("accesible") and contacto and contacto.date() == momento.date():
            respondieron.append(servidor)
    previo = (d.setdefault("historial_origen", {}).get(clave) or "").split("+")
    union = [s for s in (PSL, NCEI) if s in respondieron or s in previo]
    if union:
        d["historial_origen"][clave] = "+".join(union)
    if ev["definitivo"]:
        anterior = d["historial"].get(clave)
        if anterior is None or _GRAVEDAD[ev["estado"]] > _GRAVEDAD.get(anterior, 0):
            d["historial"][clave] = ev["estado"]
    limite = (momento.date() - timedelta(days=366)).isoformat()
    for tabla in (d["historial"], d["historial_origen"]):
        for k in [k for k in tabla if k < limite]:
            del tabla[k]


def _ultimo_en_cache() -> date | None:
    try:
        return almacen.ultimo_dia()
    except Exception:  # noqa: BLE001
        return None


def _texto_cache(ultimo: date | None) -> str:
    if ultimo is None:
        return "la caché local de NOAA OISST está vacía (ejecute python -m backend.herramientas.preparar_noaa)"
    return f"se sirve la caché local (hasta el {cfg.fmt_fecha(ultimo)})"


def _texto_sin_dato(vacia: bool) -> str:
    """Qué pasa con lo que no está en la caché. Como Copernicus: los días que falten quedan sin dato y solo sin
    ningún día real se usa el simulador. Con SOME_NOAA_MODO=real no hay respaldo simulado: lo que falte responde
    con un error claro."""
    if modo_configurado() == "real":
        if vacia:
            return ("las consultas responden con un error (modo real estricto, SOME_NOAA_MODO=real: sin respaldo "
                    "simulado)")
        return ("lo que no esté en ella queda sin dato o, si falta todo, la consulta responde con un error "
                "(modo real estricto, SOME_NOAA_MODO=real: sin respaldo simulado)")
    if vacia:
        return "todo se responde con datos simulados, señalados como tales"
    return ("lo que no esté en ella queda sin dato o, si falta todo, se responde con datos simulados, "
            "señalados como tales")


def _texto_publicacion(d: dict) -> str:
    p = d.get("publicacion") or {}
    partes = []
    if p.get("psl_fin"):
        partes.append(f"último día publicado por PSL: {cfg.fmt_fecha(p['psl_fin'])}")
    if p.get("ncei_ultimo_final"):
        partes.append(f"finales hasta el {cfg.fmt_fecha(p['ncei_ultimo_final'])}")
    if p.get("ncei_ultimo_preliminar"):
        partes.append(f"preliminares hasta el {cfg.fmt_fecha(p['ncei_ultimo_preliminar'])}")
    return ("; ".join(partes)[:1].upper() + "; ".join(partes)[1:] + ".") if partes else ""


def _mensaje(ev: dict, d: dict, ahora: datetime) -> str:
    ultimo = _ultimo_en_cache()
    cache = _texto_cache(ultimo)
    publicacion = _texto_publicacion(d)
    sin_dato = _texto_sin_dato(ultimo is None)
    respaldo = f"Mientras tanto {cache}; {sin_dato}."
    clave = ev["clave"]
    psl = _servidor(d, PSL, ahora)
    ncei = _servidor(d, NCEI, ahora)
    if clave == "sin_red":
        return f"Modo sin red (SOME_NOAA_SIN_RED=1): no se consulta NOAA; {cache}; {sin_dato}."
    if clave == "sin_verificar":
        if verificando():
            return f"Verificando la conexión con NOAA PSL y NOAA NCEI… {respaldo}"
        return (f"Aún no se ha verificado la conexión con NOAA (la sincronización lo hace al arrancar y cada 6 "
                f"horas). {respaldo}")
    if clave == "sin_conexion":
        motivo = psl.get("motivo") or psl.get("error") or ncei.get("error") or "sin conexión"
        cuando = f" (último intento: {cfg.fmt_fecha_hora(psl['momento'])})" if psl.get("momento") else ""
        reintento = (f"; las consultas no reintentan hasta las {psl['hasta'].strftime('%H:%M')}"
                     if psl.get("estado") == "pausa" and psl.get("hasta") else "")
        return (f"No se pudo conectar con NOAA PSL ni con NOAA NCEI{cuando}: "
                f"{inicial_minuscula(motivo.rstrip('.'))}{reintento}. {respaldo}")
    if clave == "solo_ncei":
        motivo = psl.get("motivo") or psl.get("error") or "sin verificar"
        return (f"NOAA PSL no responde: {inicial_minuscula(motivo.rstrip('.'))}. Los días recientes se obtienen de "
                f"los archivos diarios de NOAA NCEI (respaldo, hasta 45 días por ciclo); {cache}. {publicacion}").strip()
    lat = cfg.fmt_num(ev["latencia_ms"], 0)
    if clave == "limitado":
        return (f"NOAA PSL limitó el ritmo de peticiones (HTTP 429) en la última hora; el SOME espera y reintenta. "
                f"{cache[0].upper() + cache[1:]}. {publicacion}").strip()
    if clave == "lenta":
        return f"NOAA PSL responde con retraso (verificación en {lat} ms); {cache}. {publicacion}".strip()
    if clave == "no_vigente":
        return (f"La última verificación de NOAA es del {cfg.fmt_fecha_hora(ev['momento'])}; {cache}. "
                f"{publicacion}").strip()
    if clave == "sin_ncei":
        motivo = ncei.get("motivo") or ncei.get("error") or "sin conexión"
        return (f"NOAA PSL responde (verificación en {lat} ms), pero no se pudo consultar el listado de NOAA NCEI "
                f"({inicial_minuscula(motivo.rstrip('.'))}): qué días son finales se estima por su antigüedad. "
                f"{publicacion}").strip()
    if clave == "atrasado":
        return (f"NOAA PSL responde, pero su archivo diario no tiene días recientes. {publicacion}").strip()
    if clave == "errores_sinc":
        n = int((d["sincronizaciones"][-1] or {}).get("errores") or 0)
        return (f"La última sincronización tuvo {n} {'error' if n == 1 else 'errores'}; se reintentará en 30 "
                f"minutos. {publicacion}").strip()
    return f"NOAA PSL y NOAA NCEI responden con normalidad (verificación en {lat} ms). {publicacion}".strip()


_memo_estado: tuple | None = None
MEMO_ESTADO_S = 2.0


def estado_actual() -> dict:
    """Estado RF-21 sin red; nunca lanza (ante un error devuelve sin_conexion con el motivo).

    Se recuerda 2 s mientras no cambien la configuración, estado.json ni la caché."""
    global _memo_estado
    try:
        clave = (modo_configurado(), sin_red(), str(ruta_estado()), _mtime(), _escrituras, almacen.marca(),
                 verificando())
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
        return {"estado": ev["estado"], "mensaje": mensaje,
                "latencia_ms": int(ev["latencia_ms"]) if ev["estado"] != "sin_conexion" else 0,
                "ultimo_chequeo": (ev["momento"] or ahora).isoformat(timespec="seconds"),
                "fecha_corte": fecha_corte(), "definitivo": bool(ev["definitivo"]), "modo": modo()}
    except Exception as e:  # noqa: BLE001 — el estado nunca tumba la API
        log.exception("NOAA: error al evaluar el estado")
        return {"estado": "sin_conexion", "mensaje": f"No se pudo evaluar el estado de NOAA OISST: {legible(e)}",
                "latencia_ms": 0, "ultimo_chequeo": ahora.isoformat(timespec="seconds"), "fecha_corte": None,
                "definitivo": True, "modo": "simulado"}


def historial_estado(dias: int = 14) -> list[dict]:
    """Del más antiguo al más reciente: {"fecha", "estado", "origen", "origen_texto"}; nunca lanza."""
    hoy = date.today()
    salida = []
    try:
        if modo_configurado() == "simulado":
            for atras in range(dias - 1, -1, -1):
                salida.append({"fecha": (hoy - timedelta(days=atras)).isoformat(), "estado": "operativa",
                               "origen": None, "origen_texto": TEXTO_ORIGEN_HISTORIAL["simulado"]})
            return salida
        with _bloqueo:
            d = _cargar()
            hist = dict(d.get("historial") or {})
            origen = dict(d.get("historial_origen") or {})
        for atras in range(dias - 1, -1, -1):
            clave = (hoy - timedelta(days=atras)).isoformat()
            estado = hist.get(clave, "sin_registro")
            o = origen.get(clave) or (None if estado == "sin_registro" else "cache")
            if o not in TEXTO_ORIGEN_HISTORIAL:
                o = "cache"
            salida.append({"fecha": clave, "estado": estado, "origen": o, "origen_texto": TEXTO_ORIGEN_HISTORIAL[o]})
        if sin_red() and salida:
            salida[-1].update({"estado": "sin_conexion", "origen": "cache",
                               "origen_texto": TEXTO_ORIGEN_HISTORIAL["cache"]})
        return salida
    except Exception:  # noqa: BLE001
        log.exception("NOAA: error al leer el historial de estado")
        return [{"fecha": (hoy - timedelta(days=atras)).isoformat(), "estado": "sin_registro", "origen": None,
                 "origen_texto": TEXTO_ORIGEN_HISTORIAL[None]} for atras in range(dias - 1, -1, -1)]


# ---------------------------------------------------------------------------
# Detalle para «Estado de fuentes» y piezas del resumen (sin red)
# ---------------------------------------------------------------------------
def texto_modo() -> str:
    mc = modo_configurado()
    if mc == "simulado":
        return "Modo simulado forzado (SOME_NOAA_MODO=simulado)"
    if modo() == "real":
        sufijo = " (modo real estricto, sin respaldo simulado)" if mc == "real" else ""
        if acceso_disponible():
            return f"Datos reales (caché local, NOAA PSL y NOAA NCEI){sufijo}"
        falta = "lo que falte no se sirve" if mc == "real" else "lo que falte, simulado y señalado"
        return f"Datos reales de la caché local{sufijo}; {falta}. Sin acceso a NOAA: {motivo_sin_acceso()}"
    return f"Datos simulados: {motivo_sin_acceso() or 'sin red ni caché local de NOAA OISST'}"


def bloque_servidor(servidor: str) -> dict:
    ahora = _ahora()
    with _bloqueo:
        s = _servidor(_cargar(), servidor, ahora)
    if modo_configurado() == "simulado":
        estado, detalle = "simulado", "SOME_NOAA_MODO=simulado: no se consulta el servidor."
    elif sin_red():
        estado, detalle = "sin_red", "SOME_NOAA_SIN_RED=1: no se contacta el servidor."
    else:
        estado = s["estado"]
        lat = s["latencia_ms"]
        if estado == "pausa":
            detalle = (f"No respondió en los últimos intentos ({inicial_minuscula(s['motivo'].rstrip('.')) or 'sin detalle'}); "
                       f"las consultas no lo reintentan hasta las {s['hasta'].strftime('%H:%M')}.")
        elif estado == "sin_conexion":
            detalle = f"No responde: {inicial_minuscula((s['error'] or 'sin conexión').rstrip('.'))}."
        elif estado == "sin_registro":
            detalle = "Aún no se ha verificado."
        elif s["limitado"]:
            detalle = "Limitó el ritmo de peticiones (HTTP 429) en la última hora; el SOME espera y reintenta."
        elif lat:
            detalle = (f"Responde con retraso (verificación en {cfg.fmt_num(lat, 0)} ms)." if lat >= LATENCIA_ALTA_MS
                       else f"Responde (verificación en {cfg.fmt_num(lat, 0)} ms).")
        else:
            detalle = f"Responde (último contacto el {cfg.fmt_fecha_hora(s['momento'])})."
    return {"estado": estado, "texto_estado": TEXTO_ESTADO_SERVIDOR.get(estado, estado), "detalle": detalle,
            "latencia_ms": (s["latencia_ms"] or None) if estado in ("operativa", "degradada") else None,
            "ultimo_contacto": _iso(s["momento"]), "servidor": NOMBRE_SERVIDOR[servidor]}


def bloque_publicacion() -> dict:
    with _bloqueo:
        p = dict(_cargar().get("publicacion") or {})
    return {"ultimo_dia_psl": p.get("psl_fin"), "ultimo_final": p.get("ncei_ultimo_final"),
            "ultimo_preliminar": p.get("ncei_ultimo_preliminar"), "consultado_psl": p.get("psl_consultado"),
            "consultado_ncei": p.get("ncei_consultado"), "texto": _texto_publicacion({"publicacion": p}) or
            "Aún no se ha consultado qué días publicó NOAA."}


def bloque_climatologia() -> dict:
    try:
        periodos = almacen.climatologias_en_cache()
        ltm = almacen.leer_ltm_diaria() is not None
    except Exception:  # noqa: BLE001
        periodos, ltm = [], False
    return {"periodos_en_cache": periodos, "total": len(PERIODOS), "ltm_diaria_nino12": ltm,
            "fuente": "NOAA PSL: sst.mon.mean.nc, sst.mon.ltm.1991-2020.nc y sst.day.mean.ltm.1991-2020.nc"}


def bloque_cache() -> dict:
    try:
        c = almacen.conteo()
        tam = almacen.tamano_bytes()
    except Exception:  # noqa: BLE001
        c, tam = {"final": 0, "preliminar": 0, "ausente": 0, "ultimo_final": None, "ultimo_dia": None,
                  "por_origen": {"psl": 0, "ncei": 0}}, 0
    return {"fecha_corte": _iso(c.get("ultimo_dia")), "ultimo_final": _iso(c.get("ultimo_final")),
            "dias_finales": int(c.get("final") or 0), "dias_preliminares": int(c.get("preliminar") or 0),
            "dias_faltantes": int(c.get("ausente") or 0), "dias_por_origen": c.get("por_origen"),
            "tamano_bytes": int(tam)}


def detalle() -> dict:
    """Bloque que se fusiona en GET /api/fuentes/estado (clave «noaa»); sin red, nunca lanza."""
    try:
        return {"modo_sin_red": sin_red(),
                "noaa": {"modo": modo(), "modo_configurado": modo_configurado(), "texto_modo": texto_modo(),
                         "acceso": {"disponible": acceso_disponible(), "motivo": motivo_sin_acceso()},
                         "servidores": {PSL: bloque_servidor(PSL), NCEI: bloque_servidor(NCEI)},
                         "publicacion": bloque_publicacion(), "climatologia": bloque_climatologia(),
                         "cache": bloque_cache()}}
    except Exception as e:  # noqa: BLE001
        log.exception("NOAA: error al armar el detalle de estado")
        return {"modo_sin_red": sin_red(), "noaa": {"modo": "simulado", "modo_configurado": modo_configurado(),
                                                    "texto_modo": f"Estado no disponible: {legible(e)}",
                                                    "acceso": {"disponible": False, "motivo": legible(e)}}}


def errores_recientes(n: int = 10) -> list[dict]:
    with _bloqueo:
        lista = list(_cargar().get("errores_recientes") or [])
    return [{"momento": x.get("momento"), "mensaje": legible(x.get("mensaje"), 400)}
            for x in lista[-n:] if isinstance(x, dict)]


__all__ = ["estado_actual", "historial_estado", "detalle", "modo", "acceso_disponible", "motivo_sin_acceso",
           "fecha_corte", "tipo_publicado", "acortar", "legible", "CPC"]
