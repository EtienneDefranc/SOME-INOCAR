"""Climatología mensual oficial de GLORYS12 (GLOBAL_MULTIYEAR_PHY_001_030) en la malla SOME.

Dataset `cmems_mod_glo_phy_my_0.083deg-climatology_P1M-m`: 12 pasos (año
representativo 2004) y la variable `climatology_bounds`, que dice el periodo
REAL de la climatología (1993-2016, comprobado el 30/10/2026). Por cada
(variable, nivel SOME) se leen los niveles nativos necesarios (servicio de
series, ventana del área completa: 9 fragmentos espaciales por cuarteto de
niveles), se interpola, se regrilla (3 × 3, mínimo 5 válidas) y se guarda una
vez en `datos/copernicus/climatologia/` (no cambia).

El periodo efectivo se declara SIEMPRE (método, CSV, PDF, respuesta): los tres
periodos del catálogo (1991-2020, 1981-2010, 1993-2022) devuelven la misma
climatología oficial y la respuesta lo dice, junto con la diferencia de
sistema (reanálisis GLORYS12 frente al análisis operativo de los datos
diarios).

Origen del periodo (`fuente_periodo`): `climatology_bounds` leído con los
datos; si no viene, se abre aparte; si eso falla, los metadatos STAC del
dataset (si publican el intervalo); como último recurso,
`PERIODO_CLIMATOLOGIA_RESPALDO` («respaldo»).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime

import numpy as np
import pandas as pd

from .. import configuracion as cfg
from . import acceso, almacen, estado, malla, niveles
from .almacen import ClimatologiaCMEMS
from .constantes import (DATASET_CLIMATOLOGIA, DOI_GLORYS, GEOMETRIA, NIVELES_SOME, PERIODO_CLIMATOLOGIA_RESPALDO,
                         PRODUCTO_GLORYS, SERVICIO_SERIE, TIEMPO_MAXIMO_LECTURA_S, UNIDADES_ACEPTADAS, VARIABLES,
                         VARIABLES_2D, ErrorCopernicus, ErrorDescargaEnCurso, ErrorFormato, ErrorSinAcceso,
                         ErrorSinDatos)

log = logging.getLogger("some.copernicus")

# Sin «Generated using …»: el motor lo antepone si la consulta solo usó la climatología (con datos del análisis,
# lo trae el crédito de los datos). El DOI va como enlace, como pide la licencia (cláusula 2.4).
CREDITO = f"Climatología GLORYS12, https://doi.org/{DOI_GLORYS}."
NOTA_SISTEMA = ("Es otro sistema que el análisis operativo de los datos diarios: parte de la anomalía puede deberse "
                "a diferencias entre ambos sistemas.")
NOTA_ZOS = ("El nivel del mar de ambos sistemas puede tener referencias distintas: la anomalía de zos puede llevar "
            "un desplazamiento constante.")

_tabla = threading.Lock()
_bloqueos: dict[tuple, threading.Lock] = {}


def _bloqueo(variable: str, nivel) -> threading.Lock:
    with _tabla:
        clave = (variable, nivel)
        if clave not in _bloqueos:
            _bloqueos[clave] = threading.Lock()
        return _bloqueos[clave]


def nivel_valido(variable: str, profundidad) -> int | None:
    if variable not in VARIABLES:
        raise ValueError(f"Variable de Copernicus Marine desconocida: «{variable}».")
    if variable in VARIABLES_2D:
        return None
    z = float(profundidad or 0.0)
    for n in NIVELES_SOME:
        if abs(z - n) <= 1e-6:
            return int(n)
    raise ValueError(f"Copernicus Marine se sirve en los niveles {', '.join(map(str, NIVELES_SOME[:-1]))} y "
                     f"{NIVELES_SOME[-1]} m; {cfg.fmt_num(z, 1)} m no es uno de ellos.")


def fragmentos_estimados(variable: str, nivel) -> int:
    """Fragmentos del servicio de series para una climatología (9 espaciales × cuartetos de profundidad)."""
    g = GEOMETRIA["clim"]
    from .descarga import _alinear      # misma alineación que el plan diario
    from .constantes import COLUMNA0, COLUMNAS_NATIVAS, FILA0, FILAS_NATIVAS
    _a, _b, nf = _alinear(0, FILAS_NATIVAS - 1, FILA0, g["lat"], FILAS_NATIVAS)
    _c, _d, nc = _alinear(0, COLUMNAS_NATIVAS - 1, COLUMNA0, g["lon"], COLUMNAS_NATIVAS)
    if nivel is None:
        return nf * nc
    return nf * nc * niveles.fragmentos_profundidad(niveles.niveles_necesarios([nivel]), g["prof"])


# ---------------------------------------------------------------------------
# Periodo
# ---------------------------------------------------------------------------
def periodo_de_limites(limites) -> tuple[int, int, list[tuple[str, str]]]:
    """(año inicial, año final, 12 pares ISO) a partir de climatology_bounds (datetime64, forma 12 × 2)."""
    arr = np.asarray(limites)
    if arr.ndim != 2 or arr.shape[1] != 2 or arr.shape[0] == 0:
        raise ErrorFormato(f"climatology_bounds con forma inesperada {arr.shape}.")
    inicios = pd.to_datetime(arr[:, 0])
    fines = pd.to_datetime(arr[:, 1])
    if inicios.isna().any() or fines.isna().any():
        raise ErrorFormato("climatology_bounds con fechas vacías.")
    ultimo = fines.max()
    anio_fin = ultimo.year - 1 if (ultimo.month == 1 and ultimo.day == 1) else ultimo.year
    pares = [(a.date().isoformat(), b.date().isoformat()) for a, b in zip(inicios, fines)]
    return int(inicios.min().year), int(anio_fin), pares


def _periodo_de_metadatos() -> tuple[int, int] | None:
    """Intervalo de la climatología según los metadatos STAC del dataset (si lo publican). Nunca lanza."""
    try:
        meta = acceso.metadatos(DATASET_CLIMATOLOGIA)
        url = meta.get("url_metadatos")
        if not url:
            return None
        import requests          # noqa: PLC0415 — solo en este camino de respaldo
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        props = (r.json() or {}).get("properties") or {}
        for clave, valor in props.items():
            if "climatology_bounds" in str(clave).lower() and valor:
                texto = valor if isinstance(valor, str) else "/".join(map(str, valor))
                partes = [p for p in texto.replace(",", "/").split("/") if p.strip()]
                if len(partes) >= 2:
                    a0, a1 = date.fromisoformat(partes[0].strip()[:10]), date.fromisoformat(partes[-1].strip()[:10])
                    return a0.year, (a1.year - 1 if (a1.month, a1.day) == (1, 1) else a1.year)
    except Exception as e:  # noqa: BLE001
        log.info("Copernicus: sin periodo de climatología en los metadatos STAC (%s).", type(e).__name__)
    return None


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------
def _leer(variables, nativos):
    caja: dict = {}
    lat, lon = malla.limites_geograficos(0, malla.FILAS_NATIVAS - 1, 0, malla.COLUMNAS_NATIVAS - 1)

    def trabajo():
        try:
            caja["ds"] = acceso.leer_ventana(DATASET_CLIMATOLOGIA, variables, SERVICIO_SERIE, lat, lon, None, None,
                                             nativos)
        except BaseException as e:  # noqa: BLE001
            caja["e"] = e

    hilo = threading.Thread(target=trabajo, name="copernicus-climatologia", daemon=True)
    hilo.start()
    hilo.join(TIEMPO_MAXIMO_LECTURA_S)
    if hilo.is_alive():
        err = ErrorSinAcceso(f"La lectura de la climatología superó {TIEMPO_MAXIMO_LECTURA_S} s")
        err.es_red = True
        raise err
    if "e" in caja:
        raise caja["e"]
    return caja["ds"]


def _procesar(variable: str, nivel, ds) -> np.ndarray:
    lat = "latitude" if "latitude" in ds.coords else "lat"
    lon = "longitude" if "longitude" in ds.coords else "lon"
    filas, cols = malla.indices_nativos(ds[lat].values, ds[lon].values)
    if variable not in ds.data_vars:
        raise ErrorFormato(f"La climatología no trae la variable {variable}.")
    unidades = ds[variable].attrs.get("units")
    if unidades and str(unidades).strip().lower() not in UNIDADES_ACEPTADAS[variable]:
        raise ErrorFormato(f"Unidades inesperadas en la climatología de {variable}: «{unidades}».")
    meses = pd.to_datetime(ds["time"].values).month
    if sorted(meses) != list(range(1, 13)):
        raise ErrorFormato("La climatología no tiene exactamente los 12 meses.")
    orden = np.argsort(meses)
    da = ds[variable]
    if nivel is not None:
        arr = da.transpose("time", "depth", lat, lon).values.astype(np.float32)[orden]
        arr = niveles.interpolar(arr, ds["depth"].values, [nivel], eje=1)[:, 0]
    else:
        if "depth" in da.dims:
            da = da.isel(depth=0)
        arr = da.transpose("time", lat, lon).values.astype(np.float32)[orden]
    lienzo, cubierto = malla.lienzo(12)
    malla.colocar(lienzo, cubierto, arr, filas, cols)
    valores, obtenidas = malla.agregar_3x3(lienzo, cubierto)
    if not obtenidas.all():
        raise ErrorFormato("La lectura de la climatología no cubrió el área de estudio completa.")
    return valores


def descargar(variable: str, nivel) -> ClimatologiaCMEMS:
    """Lee, interpola, regrilla y guarda la climatología de (variable, nivel). Lanza ErrorCopernicus."""
    nativos = niveles.niveles_necesarios([nivel]) if nivel is not None else None
    t0 = time.perf_counter()
    fuente_periodo, limites = "respaldo", []
    try:
        try:
            ds = _leer([variable, "climatology_bounds"], nativos)
        except ErrorFormato:
            ds = _leer([variable], nativos)
        valores = _procesar(variable, nivel, ds)
        anio0 = anio1 = None
        if "climatology_bounds" in ds.variables:
            try:
                anio0, anio1, limites = periodo_de_limites(ds["climatology_bounds"].values)
                fuente_periodo = "climatology_bounds"
            except ErrorFormato as e:
                log.warning("Copernicus: %s", e)
        if anio0 is None:
            try:
                ds_b = _leer(["climatology_bounds"], None)
                anio0, anio1, limites = periodo_de_limites(ds_b["climatology_bounds"].values)
                fuente_periodo = "climatology_bounds"
            except Exception:  # noqa: BLE001
                meta = _periodo_de_metadatos()
                if meta:
                    (anio0, anio1), fuente_periodo = meta, "metadatos"
        if anio0 is None:
            anio0, anio1 = (int(x) for x in PERIODO_CLIMATOLOGIA_RESPALDO.split("-"))
            fuente_periodo = "respaldo"
            estado.registrar_error("Climatología: no se pudo leer climatology_bounds; se declara el periodo "
                                   f"{PERIODO_CLIMATOLOGIA_RESPALDO} verificado el 30/10/2026.")
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        from .descarga import registrar_fallo
        err = acceso.traducir(e)
        registrar_fallo(err, f"Climatología de {variable}")
        if err is e:
            raise
        raise err from e
    segundos = time.perf_counter() - t0
    c = ClimatologiaCMEMS(variable=variable, profundidad=None if nivel is None else float(nivel), valores=valores,
                          periodo=f"{anio0}-{anio1}", anio_inicio=int(anio0), anio_fin=int(anio1),
                          fuente_periodo=fuente_periodo, limites=limites,
                          obtenido=datetime.now().isoformat(timespec="seconds"))
    almacen.guardar_climatologia(c, dataset_id=DATASET_CLIMATOLOGIA)
    estado.registrar_descarga(fragmentos_estimados(variable, nivel), segundos)
    estado.registrar_servicio(True)
    log.info("Copernicus: climatología %s %s descargada en %.1f s (periodo %s, %s).", variable,
             "superficie" if nivel is None else f"{nivel} m", segundos, c.periodo, fuente_periodo)
    return c


def climatologia_mensual(variable: str, profundidad=None, *, llenar: bool = True,
                         espera_s: float | None = None) -> ClimatologiaCMEMS:
    """Climatología (12, 49, 69) de la caché; con `llenar` y acceso, la descarga si falta.

    Lanza `ErrorSinDatos` si no está y no se pide llenar, `ErrorSinAcceso` si no hay acceso,
    `ErrorDescargaEnCurso` si la descarga supera `espera_s` (sigue en segundo plano).
    """
    nivel = nivel_valido(variable, profundidad)
    c = almacen.leer_climatologia(variable, nivel)
    if c is not None:
        return c
    if not llenar:
        raise ErrorSinDatos(f"La climatología de {variable} no está en la caché local.")
    if not estado.acceso_disponible():
        raise ErrorSinAcceso(estado.motivo_sin_acceso() or "sin acceso a Copernicus Marine")
    caja: dict = {}

    def trabajo():
        try:
            with _bloqueo(variable, nivel):
                previa = almacen.leer_climatologia(variable, nivel)
                caja["c"] = previa if previa is not None else descargar(variable, nivel)
        except BaseException as e:  # noqa: BLE001
            caja["e"] = e

    if espera_s is None:
        trabajo()
    else:
        hilo = threading.Thread(target=trabajo, name="copernicus-climatologia-bajo-demanda", daemon=True)
        hilo.start()
        hilo.join(max(0.0, float(espera_s)))
        if hilo.is_alive():
            raise ErrorDescargaEnCurso(f"La climatología de {variable} se sigue descargando en segundo plano; "
                                       "vuelva a consultar en unos segundos.")
    if "e" in caja:
        e = caja["e"]
        raise e if isinstance(e, ErrorCopernicus) else acceso.traducir(e)
    return caja["c"]


def pares_faltantes(pares) -> list[tuple[str, int | None]]:
    return [(v, n) for v, n in pares if almacen.leer_climatologia(v, n) is None]


def todos_los_pares() -> list[tuple[str, int | None]]:
    return [(v, None) if v in VARIABLES_2D else (v, n) for v in VARIABLES
            for n in ((None,) if v in VARIABLES_2D else NIVELES_SOME)]


def preparar(pares, progreso=None, detener: threading.Event | None = None) -> dict:
    """Descarga las climatologías que falten de `pares` [(variable, nivel)]. Nunca lanza: resume errores."""
    faltan = pares_faltantes(pares)
    r = {"pedidas": len(faltan), "descargadas": 0, "errores": 0, "detalle_errores": [], "fragmentos": 0}
    for i, (v, n) in enumerate(faltan):
        if detener is not None and detener.is_set():
            break
        if progreso:
            progreso(i, len(faltan), f"climatología {v} {'sup.' if n is None else f'{n} m'}")
        try:
            with _bloqueo(v, n):
                if almacen.leer_climatologia(v, n) is None:
                    descargar(v, n)
                    r["fragmentos"] += fragmentos_estimados(v, n)
            r["descargadas"] += 1
        except ErrorCopernicus as e:
            r["errores"] += 1
            r["detalle_errores"].append(f"{v} {'sup.' if n is None else f'{n} m'}: {e}")
            if isinstance(e, ErrorSinAcceso):
                break
    if progreso:
        progreso(len(faltan), len(faltan), "climatología")
    return r


# ---------------------------------------------------------------------------
# Textos
# ---------------------------------------------------------------------------
def texto_metodo(c: ClimatologiaCMEMS) -> str:
    if c.profundidad is None:
        vertical = "tomada en superficie"
    elif c.profundidad == 0:
        vertical = "tomada en el nivel de 0.494 m (0 m del SOME)"
    else:
        a, b, _p = niveles.vecinos(c.profundidad)
        vertical = (f"interpolada a {cfg.fmt_num(c.profundidad, 0)} m entre los niveles de {cfg.fmt_num(a, 3)} y "
                    f"{cfg.fmt_num(b, 3)} m")
    return (f"Climatología mensual oficial de GLORYS12 ({PRODUCTO_GLORYS}), periodo {c.periodo}: media de cada mes "
            f"del reanálisis a 1/12°, {vertical} y promediada en bloques de 3 × 3 celdas (mínimo 5 válidas).")


def texto_origen(c: ClimatologiaCMEMS, pedido: str | None, variable: str) -> str:
    texto = f"Climatología oficial de Copernicus Marine (reanálisis GLORYS12, periodo {c.periodo})."
    if pedido and pedido != c.periodo:
        texto += f" Se pidió {pedido}, que Copernicus Marine no publica: se usa {c.periodo}."
    texto += f" {NOTA_SISTEMA}"
    if variable == "zos":
        texto += f" {NOTA_ZOS}"
    return texto
