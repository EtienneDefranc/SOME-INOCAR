"""Verificación real y sincronización liviana en segundo plano de Copernicus Marine.

`verificar()` (también la usa `preparar_copernicus --verificar`):
1. metadatos con `describe` del dataset 2D (público, ~3.5 s): fin del eje de
   tiempo, actualización de ARCO;
2. si no hay paquete o credenciales: se registra `sin_conexion` con el motivo;
3. credenciales con `login(check_credentials_valid=True)` (nunca pregunta);
4. lectura de prueba: zos en la celda de (−2, −85) del último día de análisis
   (1 fragmento del servicio de series) → latencia total (autenticación,
   metadatos y lectura).

Hilo *daemon* `copernicus-sincronizacion` (no arranca con SOME_COPERNICUS_SIN_RED=1,
SOME_COPERNICUS_SIN_SINCRONIZACION=1 ni en modo simulado): primer ciclo a los 20 s
del arranque; luego cada 6 h, o cada 30 min si algo falló o faltan credenciales
(así detecta un «copernicusmarine login» hecho con el servidor en marcha). Cada
ciclo: verificación; conjunto liviano (superficie de las 6 variables del último
día de análisis en el área completa y capa de mezcla del golfo de Guayaquil de
7 días; solo lo que falte); paso de NRT a mejor análisis del conjunto liviano;
climatología de superficie que falte. Todo con try/except y registro en
`estado.json`: un fallo nunca tumba el servidor.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

import numpy as np

from .. import configuracion as cfg
from .. import datos_simulados as sim
from . import acceso, climatologia, descarga, estado
from .constantes import (DATASET_METADATOS, ESPERA_INICIAL_S, INTERVALO_REINTENTO_S, INTERVALO_SINCRONIZACION_S,
                         VARIABLES, VARIABLES_3D, ErrorCopernicus, dias_sincronizacion, modo_configurado, sin_red,
                         sin_sincronizacion)

log = logging.getLogger("some.copernicus")

# Copia de catalogo.RECORTES (el núcleo no importa el catálogo al importarse: el catálogo importa los conectores)
_BBOX_RESPALDO = {"area_estudio": (-95.0, -6.0, -78.0, 6.0), "golfo_guayaquil": (-81.5, -3.6, -79.8, -2.0),
                  "nino12": (-90.0, -6.0, -80.0, 0.0)}

_hilo: threading.Thread | None = None
_parar = threading.Event()
_bloqueo_hilo = threading.Lock()
_bloqueo_ciclo = threading.Lock()
_ultimo_ciclo_con_fallo = False
_proximo: datetime | None = None


def bbox_recorte(recorte_id: str) -> tuple[float, float, float, float]:
    try:
        from .. import catalogo           # perezoso: a esta altura el catálogo ya está cargado
        r = catalogo.recorte(recorte_id)
        if r:
            return tuple(float(x) for x in r["bbox"])
    except Exception:  # noqa: BLE001
        pass
    return _BBOX_RESPALDO[recorte_id]


def indices_recorte(recorte_id: str) -> tuple[np.ndarray, np.ndarray]:
    return sim.indices_bbox(bbox_recorte(recorte_id))


def verificar() -> dict:
    """Verificación real completa (metadatos, credenciales, lectura de prueba); registra en estado.json y
    devuelve el resultado. Nunca lanza ni pregunta credenciales."""
    r = {"momento": datetime.now().isoformat(timespec="seconds"), "paquete": acceso.paquete_instalado(),
         "credenciales": None, "metadatos": None, "lectura_ok": None, "latencia_ms": 0, "error": "",
         "estado": None, "omitida": ""}
    if modo_configurado() == "simulado":
        r["omitida"] = "modo simulado, SOME_COPERNICUS_MODO=simulado"
        r["estado"] = "operativa"
        return r
    if sin_red():
        r["omitida"] = "modo sin red, SOME_COPERNICUS_SIN_RED=1"
        r["estado"] = "sin_conexion"
        return r
    estado.marcar_verificando(True)
    try:
        if not r["paquete"]:
            r["error"] = acceso.MENSAJE_NO_INSTALADO
            return r
        # 1) Metadatos (público)
        try:
            meta = acceso.metadatos(DATASET_METADATOS)
            estado.registrar_metadatos(meta)
            r["metadatos"] = {k: meta.get(k) for k in ("fin_tiempo", "version", "arco_updated_date", "latencia_ms")}
        except ErrorCopernicus as e:
            estado.registrar_metadatos(None, str(e))
            r["metadatos"] = {"error": str(e)}
        # 2) Credenciales
        if not acceso.credenciales_presentes():
            r["credenciales"] = "ausentes"
            r["error"] = acceso.MENSAJE_SIN_CREDENCIALES
            return r
        ok, lat, err = acceso.verificar_credenciales()
        r["credenciales"] = "validas" if ok else "invalidas" if ok is False else "error"
        estado.registrar_credenciales(r["credenciales"], err, lat)
        if ok is not True:
            r["error"] = err
            return r
        # 3) Lectura de prueba (un fragmento)
        t = descarga.tramo_prueba()
        t0 = time.perf_counter()
        try:
            ds = descarga._leer_con_tiempo(t)
            r["latencia_ms"] = int((time.perf_counter() - t0) * 1000)
            valores = np.asarray(ds["zos"].values)
            r["lectura_ok"] = bool(valores.size)
            if not r["lectura_ok"]:
                raise ErrorCopernicus("La lectura de prueba no devolvió datos.")
            estado.registrar_servicio(True, r["latencia_ms"], prueba=True)
        except Exception as e:  # noqa: BLE001
            err = acceso.traducir(e)
            r["lectura_ok"] = False
            r["latencia_ms"] = int((time.perf_counter() - t0) * 1000)
            r["error"] = str(err)
            if getattr(err, "es_red", False):
                estado.registrar_caida(str(err), "Lectura de prueba: ")
            else:
                descarga.registrar_fallo(err, "Lectura de prueba")
                estado.registrar_servicio(False, error=str(err), prueba=True)
        return r
    except Exception as e:  # noqa: BLE001 — nunca lanza
        log.exception("Copernicus: error en la verificación")
        r["error"] = str(e)
        return r
    finally:
        estado.marcar_verificando(False)
        try:
            r["estado"] = estado.estado_actual()["estado"]
            if not r["omitida"]:
                estado.registrar_verificacion(r)
        except Exception:  # noqa: BLE001
            log.exception("Copernicus: no se pudo registrar la verificación")


def _ejecutar_seguro(tramos, resumen: dict, etiqueta: str) -> None:
    """Ejecuta tramos sumando al resumen del ciclo; un error se anota y corta solo esta fase."""
    if not tramos:
        return
    try:
        r = descarga.ejecutar(tramos, detener=_parar)
        for k in ("fragmentos", "celdas", "tramos"):
            resumen[k] += int(r.get(k, 0))
        resumen["segundos"] = round(resumen["segundos"] + float(r.get("segundos", 0.0)), 1)
    except ErrorCopernicus as e:
        resumen["errores"] += 1
        resumen["detalle_errores"].append(f"{etiqueta}: {e}")


def sincronizar(dias: int | None = None) -> dict:
    """Un ciclo del conjunto liviano (§3.9). Devuelve el resumen del ciclo; nunca lanza."""
    global _ultimo_ciclo_con_fallo
    resumen = {"inicio": datetime.now().isoformat(timespec="seconds"), "fin": None, "verificacion": None,
               "dias_superficie": None, "tramos": 0, "fragmentos": 0, "segundos": 0.0, "celdas": 0, "errores": 0,
               "detalle_errores": [], "climatologias": 0, "omitida": ""}
    if modo_configurado() == "simulado" or sin_red():
        resumen["omitida"] = estado.motivo_sin_acceso() or "sin red"
        return resumen
    with _bloqueo_ciclo:
        try:
            v = verificar()
            resumen["verificacion"] = {k: v.get(k) for k in ("credenciales", "lectura_ok", "latencia_ms", "estado",
                                                             "error")}
            if not estado.acceso_disponible() or v.get("lectura_ok") is not True:
                resumen["omitida"] = estado.motivo_sin_acceso() or v.get("error") or "la lectura de prueba falló"
                _ultimo_ciclo_con_fallo = True
                resumen["fin"] = datetime.now().isoformat(timespec="seconds")
                return resumen
            n = dias_sincronizacion() if dias is None else max(0, int(dias))
            resumen["dias_superficie"] = n
            corte = estado.ultimo_dia_analisis()
            todo = (np.arange(len(cfg.LAT)), np.arange(len(cfg.LON)))
            golfo = indices_recorte("golfo_guayaquil")
            if n > 0 and not _parar.is_set():
                tramos = descarga.planificar(list(VARIABLES), [0], corte - timedelta(days=n - 1), corte, *todo)
                tramos += descarga.planificar(["mlotst"], None, corte - timedelta(days=6), corte, *golfo)
                _ejecutar_seguro(tramos, resumen, "Conjunto liviano")
                # Paso de NRT a mejor análisis del conjunto liviano (lo demás lo renuevan consultas y preparar)
                if not _parar.is_set():
                    desde = corte - timedelta(days=max(21, n + 14))
                    tramos = descarga.planificar(list(VARIABLES), [0], desde, corte, *todo, renovar=True)
                    tramos += descarga.planificar(["mlotst"], None, desde, corte, *golfo, renovar=True)
                    _ejecutar_seguro(tramos, resumen, "Renovación de NRT")
            if n > 0 and not _parar.is_set():
                pares = [(var, 0 if var in VARIABLES_3D else None) for var in VARIABLES]
                rc = climatologia.preparar(pares, detener=_parar)
                resumen["climatologias"] = rc["descargadas"]
                resumen["fragmentos"] += rc["fragmentos"]
                resumen["errores"] += rc["errores"]
                resumen["detalle_errores"] += rc["detalle_errores"][:5]
            _ultimo_ciclo_con_fallo = bool(resumen["errores"])
        except Exception as e:  # noqa: BLE001 — nunca tumbar el servidor
            log.exception("Copernicus: error en la sincronización")
            _ultimo_ciclo_con_fallo = True
            resumen["errores"] += 1
            resumen["detalle_errores"].append(f"Sincronización interrumpida: {e}")
        resumen["fin"] = datetime.now().isoformat(timespec="seconds")
        try:
            estado.registrar_sincronizacion(resumen)
        except Exception:  # noqa: BLE001
            log.exception("Copernicus: no se pudo registrar la sincronización")
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
            log.exception("Copernicus: ciclo de sincronización fallido")
        espera = proxima_espera_s()
        _proximo = datetime.now() + timedelta(seconds=espera)
        _parar.wait(espera)


def iniciar_sincronizacion() -> None:
    """Arranca el hilo daemon (no bloquea; idempotente). No arranca sin red, sin sincronización o en modo simulado."""
    global _hilo
    if sin_red():
        log.info("Copernicus: modo sin red (SOME_COPERNICUS_SIN_RED=1); no se inicia la sincronización.")
        return
    if sin_sincronizacion():
        log.info("Copernicus: SOME_COPERNICUS_SIN_SINCRONIZACION=1; no se inicia la sincronización en segundo plano.")
        return
    if modo_configurado() == "simulado":
        log.info("Copernicus: modo simulado (SOME_COPERNICUS_MODO=simulado); no se inicia la sincronización.")
        return
    with _bloqueo_hilo:
        if _hilo is not None and _hilo.is_alive():
            return
        _parar.clear()
        _hilo = threading.Thread(target=_bucle, name="copernicus-sincronizacion", daemon=True)
        _hilo.start()


def detener_sincronizacion() -> None:
    """Pide al hilo que termine (no espera a que acaben las descargas en curso)."""
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
