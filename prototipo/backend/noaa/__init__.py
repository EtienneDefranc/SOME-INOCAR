"""Acceso real a NOAA OISST v2.1 (temperatura superficial del mar diaria a 0.25°) para el SOME.

Producto: NOAA 0.25-degree Daily Optimum Interpolation Sea Surface Temperature
(OISST) v2.1 (DOI 10.25921/RE9P-PT57). Vía principal: el servicio NCSS del
servidor THREDDS de NOAA PSL (subconjunto por área y fechas de los archivos
anuales `sst.day.mean.AAAA.nc`, NetCDF4 que xarray abre con h5netcdf). Respaldo:
los archivos diarios globales de NOAA NCEI (cuyo listado, además, dice qué días
son finales y cuáles preliminares). Los valores se llevan a la malla SOME de
0.25° y se guardan en la caché local `datos/noaa/` (o `SOME_RUTA_NOAA`), junto
con las celdas nativas de la región Niño 1+2 oficial (0–10° S, 90–80° O) para el
índice Niño 1+2 real, las climatologías mensuales de los tres periodos de
referencia y la climatología diaria 1991-2020 de esa región.

Interfaz pública (§2 de docs/DISENO-NOAA.md): modo y acceso (sin red y baratos),
estado RF-21 (sin red, nunca lanzan), resumen y firma de la caché, lecturas,
índice Niño 1+2, y las operaciones con red para el hilo de fondo, la herramienta
`preparar_noaa` y las pruebas. Importar este paquete NO toca la red.

El conector (`backend.noaa.conector.ConectorNOAA`) se importa aparte; la
integración hará que `backend/conectores/noaa.py` lo reexporte.
"""

from __future__ import annotations

# El conector real (backend.noaa.conector) hereda de backend.conectores.base, y tras la integración
# backend/conectores/noaa.py lo reexporta: se inicializa primero el paquete de conectores para que
# ningún orden de importación deje uno de los dos módulos a medio cargar (ver DISENO-NOAA.md §1.3).
from .. import conectores as _conectores  # noqa: F401  (importación circular controlada)

import logging
from datetime import date, timedelta

from .. import configuracion as cfg
from . import almacen, climatologia, descarga, estado, indice, malla, red
from .constantes import (CREDITO, DOI, PERIODOS, ErrorConexion, ErrorDescargaEnCurso, ErrorFormato, ErrorNOAA,
                         ErrorNoExiste, ErrorSinDatos, modo_configurado, raiz, sin_red, sin_sincronizacion)
from .estado import (acceso_disponible, estado_actual, fecha_corte, historial_estado, modo, motivo_sin_acceso)
from .estado import detalle as detalle_estado
from .sincronizacion import (detener_sincronizacion, iniciar_sincronizacion, proxima_sincronizacion, sincronizando,
                             sincronizar, verificar)

log = logging.getLogger("some.noaa")

__all__ = ["modo", "modo_configurado", "sin_red", "sin_sincronizacion", "acceso_disponible", "motivo_sin_acceso",
           "fecha_corte", "estado_actual", "historial_estado", "detalle_estado", "resumen", "firma_cache",
           "llenar", "climatologia_mensual", "serie_indice_nino12", "verificar", "sincronizar", "preparar",
           "plan", "iniciar_sincronizacion", "detener_sincronizacion", "sincronizando", "proxima_sincronizacion",
           "ErrorNOAA", "ErrorConexion", "ErrorNoExiste", "ErrorFormato", "ErrorDescargaEnCurso", "ErrorSinDatos",
           "CREDITO", "DOI", "PERIODOS"]

CONJUNTO_SINCRONIZADO = ("SST diaria del área SOME y de la región Niño 1+2 oficial desde el 01/10/2023 (lo que falte y "
                         "los días preliminares), climatologías mensuales 1991-2020, 1981-2010 y 1993-2022 y "
                         "climatología diaria 1991-2020 de la región Niño 1+2")


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------
def llenar(inicio: date, fin: date, **kw) -> dict:
    """Descarga lo que falte en [inicio, fin] (ver `descarga.llenar`)."""
    return descarga.llenar(inicio, fin, **kw)


def climatologia_mensual(periodo: str, *, llenar: bool = False) -> climatologia.ClimatologiaNOAA:
    return climatologia.climatologia_mensual(periodo, llenar=llenar)


def serie_indice_nino12(inicio: date, fin: date) -> dict:
    """Índice Niño 1+2 semanal desde la caché (ver `indice.resumen`). ErrorSinDatos si no se puede calcular."""
    return indice.resumen(inicio, fin)


# ---------------------------------------------------------------------------
# Preparación (herramienta preparar_noaa) — con red
# ---------------------------------------------------------------------------
def plan(desde: date | None = None, hasta: date | None = None) -> dict:
    """Plan sin red de lo que falta: {"diario": {...}, "climatologia": [periodos], "ltm_diaria": bool,
    "peticiones", "segundos", "mib"}. `hasta` por defecto: el último día publicado conocido (el de la última
    verificación de PSL, aunque la caché aún no lo tenga), el último de la caché o ayer."""
    desde = max(desde or cfg.FECHA_INICIO, cfg.FECHA_INICIO)
    conocidos = [d for d in (estado.ultimo_publicado(), estado.fecha_corte()) if d]
    hasta = hasta or (max(conocidos) if conocidos else date.today() - timedelta(days=1))
    diario = descarga.dias_estimados(desde, hasta)
    clim = climatologia.periodos_faltantes()
    ltm = almacen.leer_ltm_diaria() is None
    peticiones = len(diario["tramos"]) + (2 if clim else 0) + (4 if ltm else 0)
    from .constantes import PAUSA_TRAS_PETICION_S, PSL
    segundos = (diario["segundos"] + (30.0 if clim else 0.0) + (40.0 if ltm else 0.0)
                + max(0, peticiones - 1) * PAUSA_TRAS_PETICION_S[PSL])
    mib = diario["mib"] + (5.5 if clim else 0.0) + (0.6 if ltm else 0.0)
    return {"desde": desde, "hasta": hasta, "diario": diario, "climatologia": clim, "ltm_diaria": ltm,
            "peticiones": peticiones, "segundos": round(segundos, 0), "mib": round(mib, 1)}


def preparar(desde: date | None = None, hasta: date | None = None, *, diario: bool = True,
             climatologias: bool = True, ltm_diaria: bool = True, progreso=None) -> dict:
    """Llena la caché (diario desde FECHA_INICIO hasta el último día publicado, climatologías y climatología diaria
    de la región Niño 1+2). Verifica antes (PSL y NCEI). Nunca pide nada interactivo.

    Devuelve {"verificacion", "diario", "climatologia", "ltm_diaria", "segundos", "errores", "detalle_errores"}."""
    import time
    t0 = time.perf_counter()
    salida = {"verificacion": None, "diario": None, "climatologia": None, "ltm_diaria": None, "segundos": 0.0,
              "errores": 0, "detalle_errores": []}
    v = verificar()
    salida["verificacion"] = v
    psl_fin = (v.get("psl") or {}).get("ultimo_dia")
    hasta = hasta or (date.fromisoformat(psl_fin) if psl_fin else date.today() - timedelta(days=1))
    desde = max(desde or cfg.FECHA_INICIO, cfg.FECHA_INICIO)
    if diario:
        try:
            salida["diario"] = descarga.llenar(desde, hasta, respetar_pausa=False, progreso=progreso)
            salida["errores"] += int(salida["diario"].get("errores") or 0)
            salida["detalle_errores"] += salida["diario"].get("detalle_errores") or []
        except ErrorNOAA as e:
            salida["errores"] += 1
            salida["detalle_errores"].append(f"Datos diarios: {e}")
    if climatologias and climatologia.periodos_faltantes():
        try:
            if progreso:
                progreso(0, 1, "climatologías mensuales (sst.mon.mean.nc y sst.mon.ltm.1991-2020.nc)")
            salida["climatologia"] = climatologia.preparar_mensual(respetar_pausa=False)
        except ErrorNOAA as e:
            salida["errores"] += 1
            salida["detalle_errores"].append(f"Climatología mensual: {e}")
    if ltm_diaria and almacen.leer_ltm_diaria() is None:
        try:
            salida["ltm_diaria"] = climatologia.preparar_ltm_diaria(respetar_pausa=False, progreso=progreso)
        except ErrorNOAA as e:
            salida["errores"] += 1
            salida["detalle_errores"].append(f"Climatología diaria de la región Niño 1+2: {e}")
    salida["segundos"] = round(time.perf_counter() - t0, 1)
    return salida


# ---------------------------------------------------------------------------
# Resumen para administración y firma de la caché (sin red, nunca lanzan)
# ---------------------------------------------------------------------------
def _iso(x) -> str | None:
    return x.isoformat() if x else None


def resumen() -> dict:
    """Tarjeta «NOAA OISST · caché y sincronización» (Administración › Sistema); sin red."""
    try:
        e = estado_actual()
        d = estado.datos()
        c = almacen.conteo()
        ultima_sinc = dict(d["sincronizaciones"][-1]) if d.get("sincronizaciones") else None
        if ultima_sinc:
            ultima_sinc["detalle_errores"] = [estado.legible(x, 400) for x in ultima_sinc.get("detalle_errores") or []]
        descargas = d.get("descargas") or {}
        return {
            "fuente": "noaa_oisst", "datos_reales": modo() == "real", "modo": modo(),
            "modo_configurado": modo_configurado(), "modo_sin_red": sin_red(),
            "estado": e["estado"], "mensaje_estado": e["mensaje"], "sincronizacion_activa": sincronizando(),
            "fecha_corte": _iso(fecha_corte()),
            "servidores": {"psl": estado.bloque_servidor("psl"), "ncei": estado.bloque_servidor("ncei")},
            "publicacion": estado.bloque_publicacion(),
            "dias_en_cache": c["final"] + c["preliminar"], "dias_finales": c["final"],
            "dias_preliminares": c["preliminar"], "dias_faltantes": c["ausente"],
            "dias_por_origen": c["por_origen"], "ultimo_final": _iso(c["ultimo_final"]),
            "primer_preliminar": _iso(c["primer_preliminar"]), "ultimo_preliminar": _iso(c["ultimo_preliminar"]),
            "climatologia": estado.bloque_climatologia(),
            "conjunto_sincronizado": CONJUNTO_SINCRONIZADO,
            "tamano_cache_bytes": almacen.tamano_bytes(),
            "bytes_descargados": int(descargas.get("bytes") or 0),
            "peticiones": int(descargas.get("peticiones") or 0),
            "ultima_sincronizacion": d.get("ultima_sincronizacion"),
            "resultado_ultima_sincronizacion": ultima_sinc,
            "ultima_verificacion": d["verificaciones"][-1] if d.get("verificaciones") else None,
            "proxima_sincronizacion": proxima_sincronizacion(),
            "errores_recientes": estado.errores_recientes(10),
        }
    except Exception as ex:  # noqa: BLE001 — la tarjeta nunca tumba la API
        log.exception("NOAA: error al armar el resumen")
        return {"fuente": "noaa_oisst", "datos_reales": False, "modo": "simulado",
                "modo_configurado": modo_configurado(), "modo_sin_red": sin_red(), "estado": "sin_conexion",
                "mensaje_estado": f"No se pudo leer el estado de NOAA OISST: {estado.legible(ex)}",
                "sincronizacion_activa": sincronizando(), "errores_recientes": []}


def firma_cache() -> str:
    """Cambia con cada escritura de la caché, con el modo, el acceso y la fecha de corte (sin recorrer archivos)."""
    try:
        return f"{almacen.marca()}:{modo()}:{int(acceso_disponible())}:{fecha_corte()}"
    except Exception:  # noqa: BLE001
        return "noaa:sin-firma"


_ = (malla, red, raiz)      # reexportados para las pruebas y la herramienta
