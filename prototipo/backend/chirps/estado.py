"""Registro persistente del estado de CHIRPS v3 (RF-21) en `datos/chirps/estado.json`.

Guarda lo último verificado de cada fuente del mismo producto:

* **servidor del CHC**: verificaciones reales (latencia, éxito o error), cambios
  de accesibilidad observados al descargar y aperturas/cierres del disyuntor;
* **réplica de dynamical.org**: última cobertura consultada (final y
  preliminar), latencia y error;

además del historial diario (el peor estado de cada día y las fuentes que
respondieron), las sincronizaciones, los bytes descargados y los errores
recientes. `estado_actual()` NO toca la red ni bloquea: evalúa lo último
verificado.

Concurrencia: cada escritura es una transacción (`_transaccion`) bajo un
bloqueo ENTRE PROCESOS (`.bloqueos/estado.lock`): se relee el archivo del
disco, se modifica y se guarda (temporal + `os.replace` con reintentos si
Windows lo tiene abierto). Así el servidor, el script de preparación y las
pruebas no se pisan los contadores ni la apertura del disyuntor.

Los mensajes de error se guardan y se muestran en español y acotados sin
cortar palabras (`legible`); el texto técnico crudo queda solo en el log.

Reglas (`estado_actual`):
* operativa: CHC accesible, latencia < 3000 ms, verificación vigente y sin
  errores en la última sincronización;
* degradada: CHC caído o lento pero réplica accesible, errores parciales o
  verificación en curso;
* sin_conexion: ninguna fuente accesible o modo `SOME_CHIRPS_SIN_RED` (se
  sirve la caché local).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import configuracion as cfg
from . import almacen
from .bloqueo import bloqueo as _bloqueo_procesos
from .constantes import (CHC, ESPEJO, LATENCIA_ALTA_MS, RUTA_ESTADO, VERIFICACION_VIGENTE_H, sin_red)

log = logging.getLogger("some.chirps")
ESPERA_BLOQUEO_S = 30

_GRAVEDAD = {"operativa": 0, "degradada": 1, "sin_conexion": 2}
TEXTO_ORIGEN_HISTORIAL = {"chc+espejo": "Servidor del CHC y réplica dynamical.org",
                          CHC: "Servidor del CHC", ESPEJO: "Réplica dynamical.org",
                          "cache": "Caché local", None: "Sin registro"}
_bloqueo = threading.RLock()
_datos: dict | None = None
_verificando = threading.Event()          # hay una verificación o sincronización en curso


def _vacio() -> dict:
    return {"verificaciones": [], "historial": {}, "historial_origen": {}, "sincronizaciones": [],
            "bytes_totales": 0, "ultima_sincronizacion": None, "errores_recientes": [],
            "disponibilidad": {}, "chc": {}, "disyuntor_chc": {}, "espejo": {}}


_mtime_cargado: int = -1


def _mtime() -> int:
    try:
        return RUTA_ESTADO.stat().st_mtime_ns
    except OSError:
        return -1


def _cargar() -> dict:
    """Copia en memoria; se relee si otro proceso (p. ej. el script de preparación) cambió el archivo."""
    global _datos, _mtime_cargado
    mt = _mtime()
    if _datos is None or mt != _mtime_cargado:
        try:
            with open(RUTA_ESTADO, encoding="utf-8") as f:
                _datos = {**_vacio(), **json.load(f)}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            if _datos is None:
                _datos = _vacio()
        _mtime_cargado = mt
    return _datos


def _guardar() -> bool:
    """Escribe el estado (temporal + `os.replace`, con reintentos si Windows tiene el archivo abierto).
    Devuelve True si quedó en disco; si no, lo registra en el log (nunca lanza)."""
    global _mtime_cargado
    datos = _datos if _datos is not None else _cargar()
    tmp = None
    try:
        RUTA_ESTADO.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="estado.", suffix=".tmp", dir=RUTA_ESTADO.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        mt = os.stat(tmp).st_mtime_ns
        for intento in range(6):
            try:
                os.replace(tmp, RUTA_ESTADO)
                tmp = None
                break
            except PermissionError:          # Windows: otro proceso lo está leyendo en este instante
                if intento < 5:
                    time.sleep(0.05 * (2 ** intento))
        if tmp is not None:
            # Alguien mantiene estado.json abierto: Windows no deja reemplazarlo, pero sí escribirlo en el
            # sitio (los lectores abren sin bloquear la escritura). No es atómico, pero solo escribe quien
            # tiene el bloqueo entre procesos y un lector que lea a medias conserva su copia anterior.
            with open(RUTA_ESTADO, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            mt = _mtime()
            log.warning("CHIRPS: estado.json estaba abierto por otro proceso; se escribió en el sitio.")
        _mtime_cargado = mt
        return True
    except OSError as e:
        log.error("CHIRPS: no se pudo guardar el estado en %s: %s", RUTA_ESTADO, e)
        return False
    finally:
        if tmp is not None and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _ruta_bloqueo() -> Path:
    return Path(RUTA_ESTADO).parent / ".bloqueos" / "estado.lock"


_ultimo_guardado_ok = True


@contextlib.contextmanager
def _transaccion():
    """Leer-modificar-guardar del estado bajo el bloqueo de hilos Y el bloqueo entre procesos,
    releyendo SIEMPRE el archivo del disco dentro del bloqueo. Rinde el diccionario a modificar;
    si el bloque termina sin error, se guarda (si no llega al disco, se registra en el log)."""
    global _datos, _ultimo_guardado_ok
    with _bloqueo, _bloqueo_procesos(_ruta_bloqueo(), ESPERA_BLOQUEO_S):
        _datos = None
        d = _cargar()
        yield d
        _ultimo_guardado_ok = _guardar()
        if not _ultimo_guardado_ok:
            log.error("CHIRPS: un cambio de estado no llegó a estado.json (se conserva solo en memoria).")


def recargar() -> None:
    """Olvida la copia en memoria (relee el archivo en el próximo acceso)."""
    global _datos
    with _bloqueo:
        _datos = None


def _ahora() -> datetime:
    return datetime.now()


_MARCAS_TECNICAS = ("HTTPSConnectionPool", "HTTPConnectionPool", "Max retries exceeded", "urllib3",
                    "StorageError", "dispatch failure", "ExceptionGroup", "TaskGroup", "ConnectTimeout",
                    "ReadTimeout", "NewConnectionError", "ProxyError", "Caused by", "object at 0x",
                    "ClientConnectorError", "ServerDisconnected", "Traceback")


def _traducir_tecnico(crudo: str) -> str:
    """Frase corta en español para un texto técnico de red (urllib3, requests, icechunk, aiohttp)."""
    t = crudo.lower()
    replica = any(m in crudo for m in ("StorageError", "dispatch failure", "ExceptionGroup", "TaskGroup"))
    if "proxy" in t or "tunnel" in t:
        motivo = "error del proxy"
    elif "connecttimeout" in t or "connect timeout" in t or ("timeout" in t and "connect" in t):
        motivo = "tiempo de espera agotado al conectar"
    elif "timeout" in t or "timed out" in t:
        motivo = "tiempo de espera agotado"
    elif "refused" in t or "rechaz" in t:
        motivo = "conexión rechazada"
    elif "name resolution" in t or "getaddrinfo" in t or "nodename" in t or "dns" in t:
        motivo = "no se pudo resolver el nombre del servidor"
    else:
        motivo = "sin conexión"
    return f"la réplica no respondió ({motivo})" if replica else motivo


def acortar(texto: str, n: int) -> str:
    """Acota `texto` a `n` caracteres sin cortar palabras (añade «…» si recorta)."""
    texto = " ".join(str(texto or "").split())
    if len(texto) <= n:
        return texto
    corte = texto[:n - 1]
    if " " in corte:
        corte = corte[:corte.rfind(" ")]
    return corte.rstrip(" ,;:(") + "…"


def legible(texto, n: int = 300) -> str:
    """Mensaje apto para la interfaz: texto técnico de red traducido al español y acotado sin cortar
    palabras. Se aplica al guardar y al mostrar (también a mensajes antiguos ya persistidos)."""
    texto = " ".join(str(texto or "").split())
    if not texto:
        return ""
    posiciones = [texto.find(m) for m in _MARCAS_TECNICAS if m in texto]
    if posiciones:
        i = min(posiciones)
        cabeza = re.sub(r"\s*\(https?://[^)]*\)", "", texto[:i]).rstrip(" :,;(-")
        cabeza = re.sub(r"\s*\((?:…|\.\.\.)\)$", "", cabeza).rstrip(" :,;(-")
        frase = _traducir_tecnico(texto[i:])
        if frase.startswith("la réplica no respondió (") and "réplica" in cabeza:
            frase = frase[len("la réplica no respondió ("):-1]       # la cabeza ya nombra la réplica
        if cabeza.endswith(frase):
            texto = cabeza
        else:
            texto = f"{cabeza}: {frase}" if cabeza else frase[0].upper() + frase[1:]
        texto += ")" * max(0, cabeza.count("(") - cabeza.count(")"))   # paréntesis abiertos antes del corte
        if not texto.endswith((".", "…")):
            texto += "."
    return acortar(texto, n)


def _iso(x) -> str | None:
    if x is None:
        return None
    return x.isoformat(timespec="seconds") if isinstance(x, datetime) else x.isoformat()


def _dt(texto) -> datetime | None:
    try:
        return datetime.fromisoformat(str(texto)[:19]) if texto else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Evaluación de las fuentes (pura: solo lee lo persistido)
# ---------------------------------------------------------------------------
def _fuentes(d: dict, ahora: datetime) -> dict:
    """Lo último conocido de cada fuente, sin tocar la red."""
    vigencia = timedelta(hours=VERIFICACION_VIGENTE_H)
    # --- CHC: la observación más reciente (verificación o cambio visto al descargar)
    obs = dict(d.get("chc") or {})
    ult_verif = d["verificaciones"][-1] if d.get("verificaciones") else None
    if ult_verif and (not obs.get("momento") or ult_verif["momento"] >= obs["momento"]):
        obs = {"accesible": bool(ult_verif["ok"]), "momento": ult_verif["momento"],
               "latencia_ms": ult_verif.get("latencia_ms"), "error": ult_verif.get("error", "")}
    momento = _dt(obs.get("momento"))
    lat_ok = next((int(v.get("latencia_ms") or 0) for v in reversed(d.get("verificaciones") or []) if v.get("ok")), 0)
    dis = d.get("disyuntor_chc") or {}
    hasta = _dt(dis.get("hasta"))
    abierto = bool(hasta and hasta > ahora)
    desde = _dt(dis.get("desde"))
    if abierto and obs.get("accesible") and momento and desde and momento >= desde:
        abierto = False                   # un contacto exitoso posterior (p. ej. de otro proceso) lo anula
    if not abierto:
        hasta = None
    try:
        from . import red                 # disyuntor de este proceso (sin red)
        er = red.estado_disyuntor()
        if er["abierto"] and er["hasta"]:
            abierto, hasta = True, max(hasta or ahora, _dt(er["hasta"]) or ahora)
    except Exception:  # noqa: BLE001
        pass
    accesible = None if momento is None else bool(obs.get("accesible"))
    if abierto:
        accesible = False
    chc = {"accesible": accesible, "momento": momento, "latencia_ms": int(obs.get("latencia_ms") or lat_ok or 0),
           "error": legible(obs.get("error") or dis.get("motivo") or ""), "vigente": bool(momento and ahora - momento <= vigencia),
           "disyuntor_abierto": abierto, "disyuntor_hasta": hasta if abierto else None}
    # --- Réplica
    e = d.get("espejo") or {}
    consultado = _dt(e.get("consultado"))
    instalado = e.get("instalado")
    if instalado is None:
        try:
            from . import descarga
            instalado = descarga.espejo_instalado()
        except Exception:  # noqa: BLE001
            instalado = False
    vigente_e = bool(consultado and ahora - consultado <= vigencia)
    espejo = {"instalado": bool(instalado),
              "accesible": (bool(e.get("accesible")) if consultado and vigente_e else None),
              "final": e.get("final"), "preliminar": e.get("preliminar"), "consultado": consultado,
              "latencia_ms": int(e.get("latencia_ms") or 0), "error": legible(e.get("error", ""))}
    if not espejo["instalado"] and espejo["accesible"] is None:
        espejo["accesible"] = False
    return {"chc": chc, "espejo": espejo}


def _origen(f: dict) -> str:
    c, e = f["chc"]["accesible"] is True, f["espejo"]["accesible"] is True
    return "chc+espejo" if c and e else CHC if c else ESPEJO if e else "cache"


def _clasificar(d: dict, f: dict) -> tuple[str, bool]:
    """(estado, definitivo). No definitivo = aún se está verificando alguna fuente."""
    c, e = f["chc"], f["espejo"]
    if c["accesible"] is True:
        ultima_sinc = d["sincronizaciones"][-1] if d.get("sincronizaciones") else None
        if c["latencia_ms"] >= LATENCIA_ALTA_MS or not c["vigente"] or (ultima_sinc and ultima_sinc.get("errores")):
            return "degradada", True
        return "operativa", True
    if e["accesible"] is True:
        return ("degradada", c["accesible"] is False)
    if c["accesible"] is False and e["accesible"] is False:
        return "sin_conexion", True
    return "degradada", False


def _marcar_dia(momento: datetime | None = None) -> None:
    """Anota en el historial el peor estado del día y las fuentes que respondieron."""
    d = _cargar()
    momento = momento or _ahora()
    f = _fuentes(d, momento)
    estado, definitivo = _clasificar(d, f)
    clave = momento.date().isoformat()
    origenes = d.setdefault("historial_origen", {})
    vistas = set(origenes.get(clave, []))
    if f["chc"]["accesible"] is True:
        vistas.add(CHC)
    if f["espejo"]["accesible"] is True:
        vistas.add(ESPEJO)
    if vistas:
        origenes[clave] = sorted(vistas)
    if definitivo:
        hist = d["historial"]
        previo = hist.get(clave)
        if previo is None or _GRAVEDAD[estado] > _GRAVEDAD.get(previo, 0):
            hist[clave] = estado
    # Conservar solo un año de historial
    limite = (momento.date() - timedelta(days=366)).isoformat()
    for tabla in (d["historial"], origenes):
        for k in [k for k in tabla if k < limite]:
            del tabla[k]


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------
def _agregar_error(mensaje: str) -> None:
    d = _cargar()
    d["errores_recientes"].append({"momento": _ahora().isoformat(timespec="seconds"),
                                   "mensaje": legible(mensaje, 400)})
    d["errores_recientes"] = d["errores_recientes"][-20:]


def registrar_error(mensaje: str) -> None:
    with _transaccion():
        _agregar_error(mensaje)


def registrar_verificacion(ok: bool, latencia_ms: int, error: str = "") -> None:
    """Verificación real del servidor del CHC (petición pequeña al README)."""
    ahora = _ahora()
    resultado = ("operativa" if latencia_ms < LATENCIA_ALTA_MS else "degradada") if ok else "sin_conexion"
    error = legible(error)
    with _transaccion() as d:
        d["verificaciones"].append({"momento": ahora.isoformat(timespec="seconds"), "ok": ok,
                                    "latencia_ms": int(latencia_ms), "error": error,
                                    "resultado": resultado, "fuente": CHC})
        d["verificaciones"] = d["verificaciones"][-50:]
        d["chc"] = {"accesible": bool(ok), "momento": ahora.isoformat(timespec="seconds"),
                    "latencia_ms": int(latencia_ms), "error": error}
        if error:
            _agregar_error(f"Verificación del CHC: {error}")
        _marcar_dia(ahora)


def registrar_chc(accesible: bool, error: str = "", latencia_ms: int | None = None) -> None:
    """Cambio de accesibilidad del CHC observado al descargar (lo notifica `red` solo al cambiar)."""
    ahora = _ahora()
    error = legible(error)
    with _transaccion() as d:
        previo = d.get("chc") or {}
        d["chc"] = {"accesible": bool(accesible), "momento": ahora.isoformat(timespec="seconds"),
                    "latencia_ms": int(latencia_ms if latencia_ms is not None else previo.get("latencia_ms") or 0),
                    "error": error}
        if not accesible and error:
            _agregar_error(f"Servidor del CHC: {error}")
        _marcar_dia(ahora)


def registrar_disyuntor(abierto: bool, hasta: datetime | None, motivo: str, aperturas: int = 0) -> None:
    """Apertura o cierre del disyuntor del CHC (lo notifica `red`). Comprueba que quedó en disco: la
    apertura la respetan los demás procesos del SOME."""
    ahora = _ahora()
    motivo = legible(motivo)
    with _transaccion() as d:
        previo = d.get("disyuntor_chc") or {}
        if abierto:
            d["disyuntor_chc"] = {"abierto": True, "desde": ahora.isoformat(timespec="seconds"),
                                  "hasta": _iso(hasta), "motivo": motivo,
                                  "aperturas": int(previo.get("aperturas", 0)) + 1, "cerrado": None}
            d["chc"] = {"accesible": False, "momento": ahora.isoformat(timespec="seconds"),
                        "latencia_ms": int((d.get("chc") or {}).get("latencia_ms") or 0), "error": motivo}
            _agregar_error(f"El servidor del CHC falló varias veces seguidas; no se reintenta hasta las "
                           f"{hasta.strftime('%H:%M') if hasta else '—'} ({motivo.rstrip('.')})")
        else:
            d["disyuntor_chc"] = {**previo, "abierto": False, "hasta": None,
                                  "cerrado": ahora.isoformat(timespec="seconds")}
            d["chc"] = {"accesible": True, "momento": ahora.isoformat(timespec="seconds"),
                        "latencia_ms": int((d.get("chc") or {}).get("latencia_ms") or 0), "error": ""}
        _marcar_dia(ahora)
    if not _ultimo_guardado_ok:
        log.error("CHIRPS: la %s del disyuntor del CHC no quedó en estado.json; los demás procesos no la verán.",
                  "apertura" if abierto else "cierre")


def _intervalo_iso(x):
    if not x:
        return None
    return [_iso(x[0]), _iso(x[1])]


def registrar_espejo(cob: dict, instalado: bool = True) -> None:
    """Última cobertura consultada de la réplica de dynamical.org (solo escribe si cambió)."""
    accesible = not cob.get("error") and bool(cob.get("final") or cob.get("preliminar"))
    nuevo = {"instalado": bool(instalado), "accesible": accesible,
             "final": _intervalo_iso(cob.get("final")), "preliminar": _intervalo_iso(cob.get("preliminar")),
             "consultado": str(cob.get("consultado") or _ahora().isoformat(timespec="seconds"))[:19],
             "latencia_ms": int(cob.get("latencia_ms") or 0), "error": legible(cob.get("error"))}
    with _bloqueo:
        if (_cargar().get("espejo") or {}) == nuevo:
            return                        # sin cambios: no se escribe
    with _transaccion() as d:
        previo = d.get("espejo") or {}
        d["espejo"] = nuevo
        if nuevo["error"] and nuevo["error"] != previo.get("error") and instalado:
            _agregar_error(f"Réplica dynamical.org: {nuevo['error']}")
        _marcar_dia(_dt(nuevo["consultado"]) or _ahora())


def registrar_sincronizacion(resumen: dict) -> None:
    ahora = _ahora()
    resumen = dict(resumen)
    resumen["detalle_errores"] = [legible(e, 400) for e in resumen.get("detalle_errores", [])]
    for k in ("error_chc", "error_espejo"):
        if resumen.get(k):
            resumen[k] = legible(resumen[k])
    with _transaccion() as d:
        d["sincronizaciones"].append(resumen)
        d["sincronizaciones"] = d["sincronizaciones"][-30:]
        d["ultima_sincronizacion"] = resumen.get("fin") or ahora.isoformat(timespec="seconds")
        d["bytes_totales"] = int(d.get("bytes_totales", 0)) + int(resumen.get("bytes", 0))
        for e in resumen.get("detalle_errores", [])[:5]:
            _agregar_error(f"Sincronización: {e}")
        _marcar_dia(ahora)


def registrar_disponibilidad(ultimo_final: date | None, ultimo_preliminar: date | None) -> None:
    """Lo último publicado en el servidor del CHC (según sus índices HTML)."""
    with _transaccion() as d:
        d["disponibilidad"] = {
            "ultimo_final": ultimo_final.isoformat() if ultimo_final else None,
            "ultimo_preliminar": ultimo_preliminar.isoformat() if ultimo_preliminar else None,
            "consultado": _ahora().isoformat(timespec="seconds"),
        }


def sumar_bytes(n: int) -> None:
    if n <= 0:
        return
    with _transaccion() as d:
        d["bytes_totales"] = int(d.get("bytes_totales", 0)) + int(n)


def marcar_verificando(activo: bool) -> None:
    (_verificando.set if activo else _verificando.clear)()


def verificando() -> bool:
    return _verificando.is_set()


def datos() -> dict:
    with _bloqueo:
        return json.loads(json.dumps(_cargar()))


# ---------------------------------------------------------------------------
# Estado RF-21 (sin red)
# ---------------------------------------------------------------------------
def _texto_preliminares(corte: date | None) -> str:
    if corte is None:
        return ""
    desde = max(cfg.FECHA_INICIO, corte - timedelta(days=120))
    c = almacen.conteo(desde, corte)
    partes = []
    if c["ultimo_final"]:
        partes.append(f"Datos finales hasta el {cfg.fmt_fecha(c['ultimo_final'])}")
    if c["preliminar"]:
        n = c["preliminar"]
        partes.append(f"{'el último día es preliminar' if n == 1 else f'los últimos {n} días son preliminares'} "
                      f"({cfg.fmt_fecha(c['primer_preliminar'])} – {cfg.fmt_fecha(c['ultimo_preliminar'])}) "
                      "y se reemplazarán por la versión final cuando el CHC la publique")
    return ("; ".join(partes) + ".") if partes else ""


def _texto_cobertura_espejo(e: dict) -> str:
    fin = e.get("final")
    pre = e.get("preliminar")
    partes = []
    if fin:
        partes.append(f"final hasta {cfg.fmt_fecha(fin[1])}")
    if pre:
        partes.append(f"preliminar hasta {cfg.fmt_fecha(pre[1])}")
    return ", ".join(partes)


def _ultimo_de_espejo(e: dict) -> date | None:
    fechas = [date.fromisoformat(str(i[1])[:10]) for i in (e.get("final"), e.get("preliminar")) if i]
    return max(fechas) if fechas else None


def estado_actual() -> dict:
    """{"estado", "mensaje", "latencia_ms", "ultimo_chequeo", "fecha_corte", "origen", "fuentes"} sin hacer red."""
    corte = almacen.ultimo_dia()
    texto_corte = cfg.fmt_fecha(corte) if corte else "—"
    sufijo = _texto_preliminares(corte)
    ahora = _ahora()

    if corte is None:
        cache_txt = "la caché local de CHIRPS está vacía (ejecute python -m backend.herramientas.preparar_chirps)"
    else:
        cache_txt = f"se sirven los datos de la caché local hasta el {texto_corte}"

    if sin_red():
        return {"estado": "sin_conexion", "latencia_ms": 0, "origen": "cache", "fuentes": None,
                "ultimo_chequeo": ahora.isoformat(timespec="seconds"), "fecha_corte": corte,
                "mensaje": (f"Modo sin red (SOME_CHIRPS_SIN_RED=1): no se consulta el servidor del CHC "
                            f"ni la réplica de dynamical.org; {cache_txt}. {sufijo}").strip()}

    with _bloqueo:
        d = _cargar()
        f = _fuentes(d, ahora)
        estado, definitivo = _clasificar(d, f)
        ultima_sinc = d["sincronizaciones"][-1] if d["sincronizaciones"] else None
    c, e = f["chc"], f["espejo"]
    cob_e = _texto_cobertura_espejo(e)
    momentos = [m for m in (c["momento"], e["consultado"]) if m]
    ultimo_chequeo = max(momentos) if momentos else ahora
    lat = c["latencia_ms"] if c["accesible"] else 0
    reintento = (f" (no se reintenta hasta las {c['disyuntor_hasta'].strftime('%H:%M')})"
                 if c["disyuntor_abierto"] and c["disyuntor_hasta"] else "")

    if c["accesible"] is True:
        respaldo = (f" La réplica de dynamical.org está disponible como respaldo ({cob_e})."
                    if e["accesible"] and cob_e else "")
        if c["latencia_ms"] >= LATENCIA_ALTA_MS:
            mensaje = (f"El servidor del CHC responde con retraso ({cfg.fmt_num(c['latencia_ms'], 0)} ms)"
                       + (f"; los días que cubre la réplica de dynamical.org ({cob_e}) se obtienen de ella."
                          if e["accesible"] and cob_e else "."))
        elif ultima_sinc and ultima_sinc.get("errores"):
            n = int(ultima_sinc["errores"])
            mensaje = (f"La última sincronización tuvo {n} {'error' if n == 1 else 'errores'} de descarga; "
                       f"se reintentará.{respaldo}")
        elif not c["vigente"]:
            mensaje = (f"La última verificación del servidor del CHC es del {cfg.fmt_fecha_hora(c['momento'])}; "
                       f"{cache_txt}.")
        else:
            mensaje = f"Servidor del CHC respondiendo con normalidad ({cfg.fmt_num(c['latencia_ms'], 0)} ms).{respaldo}"
    elif e["accesible"] is True:
        if c["accesible"] is False:
            mensaje = (f"El servidor del CHC no responde{reintento}; los datos se obtienen de la réplica de "
                       f"dynamical.org ({cob_e or 'cobertura sin informar'}).")
        else:
            mensaje = (f"Aún no se ha verificado el servidor del CHC; los datos se obtienen de la réplica de "
                       f"dynamical.org ({cob_e or 'cobertura sin informar'}).")
        ult_e = _ultimo_de_espejo(e)
        if ult_e:
            ultimo = max(ult_e, corte) if corte else ult_e
            mensaje += (f" Los días posteriores al {cfg.fmt_fecha(ultimo)} se descargarán del CHC cuando "
                        "vuelva a responder")
            if corte and corte > ult_e:
                mensaje += (f"; los del {cfg.fmt_fecha(ult_e + timedelta(days=1))} al {cfg.fmt_fecha(corte)} ya "
                            "están en la caché local (descargados antes del CHC)." if corte - ult_e > timedelta(days=1)
                            else f"; el {cfg.fmt_fecha(corte)} ya está en la caché local (descargado antes del CHC).")
            else:
                mensaje += "."
    elif c["accesible"] is False and e["accesible"] is False:
        cuando = f" (último intento: {cfg.fmt_fecha_hora(c['momento'])})" if c["momento"] else ""
        if e["instalado"]:
            replica = "ni con la réplica de dynamical.org"
        else:
            replica = "y la réplica de dynamical.org no está disponible en este equipo (faltan icechunk y zarr)"
        mensaje = f"No se pudo conectar con el servidor del CHC{cuando}{reintento} {replica}; {cache_txt}."
    elif c["momento"] is None and e["consultado"] is None:
        mensaje = (("Verificando la conexión con el CHC y la réplica de dynamical.org…" if verificando() else
                    "Aún no se ha verificado la conexión con el servidor del CHC.")
                   + f" Mientras tanto {cache_txt}.")
    else:
        mensaje = f"Verificando las fuentes de CHIRPS v3 (CHC y réplica de dynamical.org); {cache_txt}."
    if verificando() and estado != "operativa" and not mensaje.startswith("Verificando"):
        mensaje = "Verificando la conexión con el CHC… " + mensaje
    return {"estado": estado, "latencia_ms": int(lat), "origen": _origen(f),
            "ultimo_chequeo": ultimo_chequeo.isoformat(timespec="seconds"),
            "fecha_corte": corte, "mensaje": f"{mensaje} {sufijo}".strip(),
            "fuentes": {"chc": {**c, "momento": _iso(c["momento"]), "disyuntor_hasta": _iso(c["disyuntor_hasta"])},
                        "espejo": {**e, "consultado": _iso(e["consultado"])}},
            "definitivo": definitivo}


def historial_estado(dias: int = 14) -> list[dict]:
    """Del más antiguo al más reciente: {"fecha", "estado", "origen", "origen_texto"}.

    «sin_registro» donde no hubo verificación. `origen`: "chc", "espejo", "chc+espejo"
    (fuentes que respondieron ese día), "cache" (ninguna) o None (sin registro).
    """
    hoy = date.today()
    with _bloqueo:
        d = _cargar()
        hist = dict(d["historial"])
        hist_origen = dict(d.get("historial_origen") or {})
    salida = []
    for atras in range(dias - 1, -1, -1):
        dia = hoy - timedelta(days=atras)
        clave = dia.isoformat()
        estado = hist.get(clave, "sin_registro")
        vistas = set(hist_origen.get(clave, []))
        if estado == "sin_registro" and not vistas:
            origen = None
        elif {CHC, ESPEJO} <= vistas:
            origen = "chc+espejo"
        elif vistas:
            origen = next(iter(vistas))
        else:
            origen = "cache"
        salida.append({"fecha": clave, "estado": estado, "origen": origen,
                       "origen_texto": TEXTO_ORIGEN_HISTORIAL[origen]})
    if sin_red() and salida:
        # Estado conocido de hoy por configuración (no se persiste: no hubo verificación real).
        salida[-1].update({"estado": "sin_conexion", "origen": "cache",
                           "origen_texto": TEXTO_ORIGEN_HISTORIAL["cache"]})
    return salida


TEXTO_ESTADO_CAMINO = {
    "operativa": "Operativa", "degradada": "Degradada", "sin_conexion": "Sin conexión",
    "sin_registro": "Sin verificar", "sin_red": "No se consulta (modo sin red)",
    "no_instalada": "No instalada en este equipo",
}


def _fecha_iso(x) -> str | None:
    if not x:
        return None
    return str(x)[:10]


def caminos() -> dict:
    """Estado de cada camino de CHIRPS v3 y fecha hasta la que llega cada producto, sin red.

    Para la tarjeta de CHIRPS en «Estado de fuentes» (RF-21). Devuelve
    {"modo_sin_red", "caminos": [chc, espejo], "cache": {...}}; cada camino trae
    `id`, `nombre`, `servidor`, `estado` (operativa | degradada | sin_conexion |
    sin_registro | sin_red | no_instalada), `texto_estado`, `detalle`, `ultimo_contacto`,
    `latencia_ms` y `productos` {"final", "preliminar"} (último día publicado según lo
    último consultado; None si nunca se consultó) con `productos_consultado`.
    """
    ahora = _ahora()
    modo_sin_red = sin_red()
    with _bloqueo:
        d = _cargar()
        f = _fuentes(d, ahora)
        disp = dict(d.get("disponibilidad") or {})
    c, e = f["chc"], f["espejo"]

    # --- Servidor del CHC
    if modo_sin_red:
        estado_c, detalle_c = "sin_red", "SOME_CHIRPS_SIN_RED=1: no se contacta el servidor."
    elif c["accesible"] is True:
        lento = c["latencia_ms"] >= LATENCIA_ALTA_MS
        estado_c = "degradada" if lento or not c["vigente"] else "operativa"
        detalle_c = (f"Responde con retraso ({cfg.fmt_num(c['latencia_ms'], 0)} ms)." if lento else
                     f"Responde ({cfg.fmt_num(c['latencia_ms'], 0)} ms)." if c["vigente"] else
                     f"Última respuesta el {cfg.fmt_fecha_hora(c['momento'])}.")
    elif c["accesible"] is False:
        estado_c = "sin_conexion"
        detalle_c = "No responde"
        if c["disyuntor_abierto"] and c["disyuntor_hasta"]:
            detalle_c += f"; no se le pide nada hasta las {c['disyuntor_hasta'].strftime('%H:%M')} (disyuntor)"
        elif c["momento"]:
            detalle_c += f" (último intento: {cfg.fmt_fecha_hora(c['momento'])})"
        detalle_c += "."
    else:
        estado_c, detalle_c = "sin_registro", "Aún no se ha verificado."
    chc = {"id": CHC, "nombre": "Servidor del CHC", "servidor": "data.chc.ucsb.edu",
           "estado": estado_c, "texto_estado": TEXTO_ESTADO_CAMINO[estado_c], "detalle": detalle_c,
           "ultimo_contacto": _iso(c["momento"]),
           "latencia_ms": int(c["latencia_ms"] or 0) if estado_c in ("operativa", "degradada") else None,
           "productos": {"final": _fecha_iso(disp.get("ultimo_final")),
                         "preliminar": _fecha_iso(disp.get("ultimo_preliminar"))},
           "productos_consultado": disp.get("consultado"),
           "disyuntor_hasta": _iso(c["disyuntor_hasta"]) if c["disyuntor_abierto"] else None}

    # --- Réplica de dynamical.org
    if modo_sin_red:
        estado_e, detalle_e = "sin_red", "SOME_CHIRPS_SIN_RED=1: no se consulta la réplica."
    elif not e["instalado"]:
        estado_e = "no_instalada"
        detalle_e = "Faltan los paquetes opcionales icechunk y zarr (pip install icechunk zarr)."
    elif e["accesible"] is True:
        estado_e = "operativa"
        detalle_e = f"Responde ({cfg.fmt_num(e['latencia_ms'], 0)} ms al abrir el almacén)."
    elif e["accesible"] is False:
        estado_e = "sin_conexion"
        error_e = legible(e["error"], 160).rstrip(".")
        detalle_e = f"No responde{': ' + error_e if error_e else ''}."
    else:
        estado_e = "sin_registro"
        detalle_e = ("Aún no se ha consultado." if not e["consultado"] else
                     f"Última consulta el {cfg.fmt_fecha_hora(e['consultado'])} (ya no vigente).")
    espejo = {"id": ESPEJO, "nombre": "Réplica dynamical.org", "servidor": "dynamical.org (S3, us-west-2)",
              "estado": estado_e, "texto_estado": TEXTO_ESTADO_CAMINO[estado_e], "detalle": detalle_e,
              "ultimo_contacto": _iso(e["consultado"]),
              "latencia_ms": int(e["latencia_ms"] or 0) if estado_e == "operativa" else None,
              "productos": {"final": _fecha_iso((e.get("final") or [None, None])[1]),
                            "preliminar": _fecha_iso((e.get("preliminar") or [None, None])[1])},
              "productos_consultado": _iso(e["consultado"]),
              "licencia": "CC BY 4.0 (tercero, sin garantía de servicio)"}

    # --- Caché local (lo que realmente sirve el SOME)
    corte = almacen.ultimo_dia()
    if corte is not None:
        cuenta = almacen.conteo(cfg.FECHA_INICIO, corte)
    else:
        cuenta = {"final": 0, "preliminar": 0, "ausente": 0, "ultimo_final": None, "ultimo_preliminar": None,
                  "por_origen": {CHC: 0, ESPEJO: 0}}
    cache = {"fecha_corte": _iso(corte), "ultimo_final": _iso(cuenta["ultimo_final"]),
             "ultimo_preliminar": _iso(cuenta["ultimo_preliminar"]),
             "dias_finales": int(cuenta["final"]), "dias_preliminares": int(cuenta["preliminar"]),
             "dias_faltantes": int(cuenta["ausente"]),
             "dias_por_origen": {k: int(v) for k, v in (cuenta.get("por_origen") or {}).items()}}
    return {"modo_sin_red": modo_sin_red, "caminos": [chc, espejo], "cache": cache}


def disyuntor_en_prueba(ahora: datetime | None = None) -> bool:
    """True si un disyuntor del CHC abierto (por cualquier proceso) ya venció sin que el CHC
    haya vuelto a responder: el siguiente intento es el «de prueba» y, si falla, lo reabre."""
    ahora = ahora or _ahora()
    with _bloqueo:
        d = _cargar()
        dis = dict(d.get("disyuntor_chc") or {})
        obs = dict(d.get("chc") or {})
    if not dis.get("abierto"):
        return False
    hasta, desde, momento = _dt(dis.get("hasta")), _dt(dis.get("desde")), _dt(obs.get("momento"))
    if not hasta or hasta > ahora:
        return False
    return not (obs.get("accesible") and momento and desde and momento >= desde)


def disyuntor_vigente(ahora: datetime | None = None) -> tuple[bool, datetime | None, str]:
    """(abierto, hasta, motivo) del disyuntor del CHC persistido por cualquier proceso, sin red.

    Deja de valer si vence o si después de abrirse hubo un contacto exitoso con el CHC.
    No consulta a `red` (lo llama `red` para respetar el disyuntor de otros procesos).
    """
    ahora = ahora or _ahora()
    with _bloqueo:
        d = _cargar()
        dis = dict(d.get("disyuntor_chc") or {})
        obs = dict(d.get("chc") or {})
    hasta = _dt(dis.get("hasta"))
    if not hasta or hasta <= ahora:
        return False, None, ""
    desde, momento = _dt(dis.get("desde")), _dt(obs.get("momento"))
    if obs.get("accesible") and momento and desde and momento >= desde:
        return False, None, ""
    return True, hasta, str(dis.get("motivo") or "")


def ultima_publicada_accesible(incluir_chc: bool = True) -> date | None:
    """Último día publicado por una fuente que responde AHORA, según lo persistido (sin red):
    el fin de la cobertura de la réplica si su última consulta es vigente y sin error y, con
    `incluir_chc`, lo último que listan los índices del CHC si el CHC está accesible. None si no se
    conoce o en modo sin red."""
    if sin_red():
        return None
    with _bloqueo:
        d = _cargar()
        f = _fuentes(d, _ahora())
        disp = dict(d.get("disponibilidad") or {})
    fechas: list[date] = []
    if f["espejo"]["accesible"] is True:
        u = _ultimo_de_espejo(f["espejo"])
        if u:
            fechas.append(u)
    if incluir_chc and f["chc"]["accesible"] is True:
        for k in ("ultimo_final", "ultimo_preliminar"):
            try:
                if disp.get(k):
                    fechas.append(date.fromisoformat(str(disp[k])[:10]))
            except ValueError:
                pass
    return min(max(fechas), date.today()) if fechas else None


def chc_accesible() -> bool | None:
    """Última accesibilidad conocida del CHC (True, False o None si nunca se verificó), sin red.

    False también mientras el disyuntor está abierto. Una falla persiste hasta que una
    verificación o una descarga posterior tenga éxito.
    """
    with _bloqueo:
        return _fuentes(_cargar(), _ahora())["chc"]["accesible"]


def resumen_fuentes() -> dict:
    """Estado de cada fuente para administración (sin red)."""
    ahora = _ahora()
    with _bloqueo:
        d = _cargar()
        f = _fuentes(d, ahora)
        disp = dict(d.get("disponibilidad") or {})
        dis = dict(d.get("disyuntor_chc") or {})
    c, e = f["chc"], f["espejo"]
    try:
        from . import red
        dis_proc = red.estado_disyuntor()
    except Exception:  # noqa: BLE001
        dis_proc = {}
    return {
        CHC: {"nombre": "Servidor del Climate Hazards Center (data.chc.ucsb.edu)",
              "accesible": c["accesible"], "ultimo_contacto": _iso(c["momento"]),
              "latencia_ms": c["latencia_ms"], "error": c["error"],
              "ultimo_final_publicado": disp.get("ultimo_final"),
              "ultimo_preliminar_publicado": disp.get("ultimo_preliminar"),
              "disponibilidad_consultada": disp.get("consultado"),
              "disyuntor": {"abierto": c["disyuntor_abierto"], "hasta": _iso(c["disyuntor_hasta"]),
                            "aperturas": int(dis.get("aperturas", 0)), "ultima_apertura": dis.get("desde"),
                            "ultimo_cierre": dis.get("cerrado"), "motivo": legible(dis.get("motivo", "")),
                            "fallos_seguidos": dis_proc.get("fallos_seguidos", 0),
                            "umbral": dis_proc.get("umbral"), "plazo_min": dis_proc.get("plazo_min")}},
        ESPEJO: {"nombre": "Réplica de dynamical.org (Zarr/Icechunk en S3, CC BY 4.0)",
                 "instalado": e["instalado"], "accesible": e["accesible"],
                 "final": e["final"], "preliminar": e["preliminar"], "consultado": _iso(e["consultado"]),
                 "latencia_ms": e["latencia_ms"], "error": e["error"]},
    }
