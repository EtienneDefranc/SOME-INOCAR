"""Verificación real y sincronización en segundo plano de NOAA OISST v2.1.

`verificar()` (también la usa `preparar_noaa --verificar`), dos peticiones livianas:
1. PSL: `dataset.xml` del NCSS del archivo diario del año (~0.2 s): último día
   publicado y latencia;
2. NCEI: listado del mes actual y del anterior (~1 s): qué días son finales, desde
   cuándo, y cuáles preliminares.

Hilo *daemon* `noaa-sincronizacion` (no arranca con SOME_NOAA_SIN_RED=1,
SOME_NOAA_SIN_SINCRONIZACION=1 ni en modo simulado): primer ciclo a los 30 s del
arranque; luego cada 6 h, o cada 30 min si algo falló. Cada ciclo: verificación;
días que falten desde el 01/10/2023 y los preliminares (los últimos ~15 días, que
NOAA reemplaza por la versión final), en tramos de hasta 92 días por petición a PSL
(con la caché al día, una sola petición de ~21 días); las climatologías y la
climatología diaria de la región Niño 1+2 que falten. Todo con try/except y
registro en `estado.json`: un fallo nunca tumba el servidor.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime, timedelta

from .. import configuracion as cfg
from . import almacen, climatologia, descarga, estado, red
from .constantes import (ARCHIVO_DIARIO, ESPERA_INICIAL_S, INTERVALO_REINTENTO_S, INTERVALO_SINCRONIZACION_S, NCEI,
                         PSL, VENTANA_RENOVACION_DIAS, ErrorNOAA, ErrorNoExiste, modo_configurado, sin_red,
                         sin_sincronizacion)

log = logging.getLogger("some.noaa")

_hilo: threading.Thread | None = None
_parar = threading.Event()
_bloqueo_hilo = threading.Lock()
_bloqueo_ciclo = threading.Lock()
_ultimo_ciclo_con_fallo = False
_proximo: datetime | None = None
_FIN_TIEMPO = re.compile(r"<TimeSpan>\s*<begin>([^<]+)</begin>\s*<end>([^<]+)</end>", re.S)


def leer_fin_tiempo(xml: str) -> date | None:
    """Último día del eje de tiempo en el `dataset.xml` del NCSS (None si no se encuentra)."""
    m = _FIN_TIEMPO.search(xml or "")
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(2).strip()[:10])
    except ValueError:
        return None


def verificar_psl(hoy: date | None = None) -> dict:
    """Último día publicado por PSL y latencia (registrados). {"ok", "latencia_ms", "ultimo_dia", "error"}."""
    hoy = hoy or date.today()
    r = {"ok": False, "latencia_ms": 0, "ultimo_dia": None, "error": ""}
    for anio in (hoy.year, hoy.year - 1):           # el 1 de enero el archivo del año aún no existe
        try:
            contenido, lat = red.metadatos_psl(ARCHIVO_DIARIO.format(anio=anio), respetar_pausa=False)
        except ErrorNoExiste:
            continue
        except ErrorNOAA as e:
            r["error"] = str(e)
            estado.registrar_servidor(PSL, False, error=str(e), verificacion=True)
            return r
        fin = leer_fin_tiempo(contenido.decode("utf-8", "replace"))
        r.update({"ok": fin is not None, "latencia_ms": int(lat), "ultimo_dia": fin.isoformat() if fin else None})
        if fin is None:
            r["error"] = "El dataset.xml de PSL no trae el eje de tiempo."
            estado.registrar_servidor(PSL, False, lat, r["error"], verificacion=True)
            return r
        estado.registrar_servidor(PSL, True, lat, verificacion=True)
        estado.registrar_publicacion({"psl_fin": fin.isoformat()})
        return r
    r["error"] = "PSL no publica el archivo diario del año en curso ni del anterior."
    estado.registrar_servidor(PSL, False, error=r["error"], verificacion=True)
    return r


def verificar_ncei(hoy: date | None = None) -> dict:
    """Listado de NCEI (registrado). {"ok", "latencia_ms", "ultimo_final", "ultimo_preliminar", "error"}."""
    r = {"ok": False, "latencia_ms": 0, "ultimo_final": None, "ultimo_preliminar": None, "error": ""}
    try:
        info = descarga.actualizar_publicacion(respetar_pausa=False, hoy=hoy)
    except ErrorNOAA as e:
        r["error"] = str(e)
        estado.registrar_servidor(NCEI, False, error=str(e), verificacion=True)
        return r
    r.update({"ok": bool(info.get("ncei_ultimo_final") or info.get("ncei_ultimo_preliminar")),
              "latencia_ms": int(info.get("latencia_ms") or 0), "ultimo_final": info.get("ncei_ultimo_final"),
              "ultimo_preliminar": info.get("ncei_ultimo_preliminar")})
    if r["ok"]:
        estado.registrar_servidor(NCEI, True, r["latencia_ms"], verificacion=True)
    else:
        r["error"] = "El listado de NCEI no trae archivos diarios."
        estado.registrar_servidor(NCEI, False, r["latencia_ms"], r["error"], verificacion=True)
    return r


def verificar() -> dict:
    """Verificación real (PSL y NCEI); registra en estado.json y devuelve el resultado. Nunca lanza."""
    r = {"momento": datetime.now().isoformat(timespec="seconds"), "psl": None, "ncei": None, "estado": None,
         "error": "", "omitida": ""}
    if modo_configurado() == "simulado":
        r["omitida"] = "modo simulado, SOME_NOAA_MODO=simulado"
        r["estado"] = "operativa"
        return r
    if sin_red():
        r["omitida"] = "modo sin red, SOME_NOAA_SIN_RED=1"
        r["estado"] = "sin_conexion"
        return r
    estado.marcar_verificando(True)
    try:
        r["psl"] = verificar_psl()
        r["ncei"] = verificar_ncei()
        errores = [f"{nombre}: {x['error']}" for nombre, x in (("NOAA PSL", r["psl"]), ("NOAA NCEI", r["ncei"]))
                   if x and x.get("error")]
        r["error"] = " ".join(errores)
        return r
    except Exception as e:  # noqa: BLE001 — nunca lanza
        log.exception("NOAA: error en la verificación")
        r["error"] = str(e)
        return r
    finally:
        estado.marcar_verificando(False)
        try:
            r["estado"] = estado.estado_actual()["estado"]
            if not r["omitida"]:
                estado.registrar_verificacion(r)
        except Exception:  # noqa: BLE001
            log.exception("NOAA: no se pudo registrar la verificación")


def sincronizar(*, completo: bool = True) -> dict:
    """Un ciclo (§3.9 del diseño). Devuelve el resumen; nunca lanza."""
    global _ultimo_ciclo_con_fallo
    resumen = {"inicio": datetime.now().isoformat(timespec="seconds"), "fin": None, "verificacion": None,
               "peticiones": 0, "dias_escritos": 0, "finales": 0, "preliminares": 0, "dias_ncei": 0, "bytes": 0,
               "segundos": 0.0, "climatologias": 0, "ltm_diaria": False, "errores": 0, "detalle_errores": [],
               "omitida": ""}
    if modo_configurado() == "simulado" or sin_red():
        resumen["omitida"] = estado.motivo_sin_acceso() or "sin red"
        return resumen
    with _bloqueo_ciclo:
        try:
            v = verificar()
            resumen["verificacion"] = {"psl": v.get("psl"), "ncei": v.get("ncei"), "estado": v.get("estado")}
            psl_ok = bool((v.get("psl") or {}).get("ok"))
            ncei_ok = bool((v.get("ncei") or {}).get("ok"))
            if not psl_ok and not ncei_ok:
                resumen["omitida"] = v.get("error") or "NOAA PSL y NOAA NCEI no respondieron"
                _ultimo_ciclo_con_fallo = True
                resumen["fin"] = datetime.now().isoformat(timespec="seconds")
                estado.registrar_sincronizacion(resumen)
                return resumen
            fin = (date.fromisoformat(v["psl"]["ultimo_dia"]) if psl_ok and v["psl"].get("ultimo_dia")
                   else date.today() - timedelta(days=1))
            inicio = cfg.FECHA_INICIO if completo else fin - timedelta(days=VENTANA_RENOVACION_DIAS)
            if not _parar.is_set():
                try:
                    r = descarga.llenar(inicio, fin, renovar_desde=fin - timedelta(days=VENTANA_RENOVACION_DIAS),
                                        respetar_pausa=False, detener=_parar)
                    for k in ("peticiones", "dias_escritos", "finales", "preliminares", "dias_ncei", "bytes",
                              "errores"):
                        resumen[k] += int(r.get(k) or 0)
                    resumen["segundos"] = round(resumen["segundos"] + float(r.get("segundos") or 0.0), 1)
                    resumen["detalle_errores"] += r.get("detalle_errores") or []
                except ErrorNOAA as e:
                    resumen["errores"] += 1
                    resumen["detalle_errores"].append(f"Datos diarios: {e}")
            if psl_ok and not _parar.is_set() and climatologia.periodos_faltantes():
                try:
                    rc = climatologia.preparar_mensual(respetar_pausa=False)
                    resumen["climatologias"] = len(rc["periodos"])
                    resumen["peticiones"] += rc["peticiones"]
                    resumen["bytes"] += rc["bytes"]
                except ErrorNOAA as e:
                    resumen["errores"] += 1
                    resumen["detalle_errores"].append(f"Climatología mensual: {e}")
            if psl_ok and not _parar.is_set() and almacen.leer_ltm_diaria() is None:
                try:
                    rl = climatologia.preparar_ltm_diaria(respetar_pausa=False)
                    resumen["ltm_diaria"] = True
                    resumen["peticiones"] += rl["peticiones"]
                    resumen["bytes"] += rl["bytes"]
                except ErrorNOAA as e:
                    resumen["errores"] += 1
                    resumen["detalle_errores"].append(f"Climatología diaria de la región Niño 1+2: {e}")
            _ultimo_ciclo_con_fallo = bool(resumen["errores"]) or not psl_ok
        except Exception as e:  # noqa: BLE001 — nunca tumbar el servidor
            log.exception("NOAA: error en la sincronización")
            _ultimo_ciclo_con_fallo = True
            resumen["errores"] += 1
            resumen["detalle_errores"].append(f"Sincronización interrumpida: {e}")
        resumen["fin"] = datetime.now().isoformat(timespec="seconds")
        try:
            estado.registrar_sincronizacion(resumen)
        except Exception:  # noqa: BLE001
            log.exception("NOAA: no se pudo registrar la sincronización")
        return resumen


def proxima_espera_s() -> int:
    return INTERVALO_REINTENTO_S if _ultimo_ciclo_con_fallo else INTERVALO_SINCRONIZACION_S


def proxima_sincronizacion() -> str | None:
    return _proximo.isoformat(timespec="seconds") if _proximo and sincronizando() else None


def _bucle() -> None:
    global _proximo
    _proximo = datetime.now() + timedelta(seconds=ESPERA_INICIAL_S)
    if _parar.wait(ESPERA_INICIAL_S):
        return
    while not _parar.is_set():
        _proximo = None                 # ciclo en curso
        try:
            sincronizar()
        except Exception:  # noqa: BLE001
            log.exception("NOAA: ciclo de sincronización fallido")
        espera = proxima_espera_s()
        _proximo = datetime.now() + timedelta(seconds=espera)
        _parar.wait(espera)


def iniciar_sincronizacion() -> None:
    """Arranca el hilo daemon (no bloquea; idempotente). No arranca sin red, sin sincronización o en modo simulado."""
    global _hilo
    if sin_red():
        log.info("NOAA: modo sin red (SOME_NOAA_SIN_RED=1); no se inicia la sincronización.")
        return
    if sin_sincronizacion():
        log.info("NOAA: SOME_NOAA_SIN_SINCRONIZACION=1; no se inicia la sincronización en segundo plano.")
        return
    if modo_configurado() == "simulado":
        log.info("NOAA: modo simulado (SOME_NOAA_MODO=simulado); no se inicia la sincronización.")
        return
    with _bloqueo_hilo:
        if _hilo is not None and _hilo.is_alive():
            return
        _parar.clear()
        _hilo = threading.Thread(target=_bucle, name="noaa-sincronizacion", daemon=True)
        _hilo.start()


def detener_sincronizacion() -> None:
    """Pide al hilo que termine (no espera a que acabe una descarga en curso)."""
    global _hilo, _proximo
    _parar.set()
    with _bloqueo_hilo:
        h = _hilo
        _hilo = None
    if h is not None and h.is_alive():
        h.join(timeout=2)
    _proximo = None
    estado.marcar_verificando(False)


def sincronizando() -> bool:
    return _hilo is not None and _hilo.is_alive()
