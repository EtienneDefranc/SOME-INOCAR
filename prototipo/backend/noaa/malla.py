"""Lectura de los NetCDF de NOAA OISST v2.1 y paso de la malla nativa a la malla SOME de 0.25°.

* `abrir_netcdf(bytes)`: abre en memoria lo que entrega el NCSS de PSL o un archivo
  diario de NCEI (NetCDF4/HDF5, con h5netcdf; sin decodificar el tiempo: la
  climatología diaria de PSL usa el año 0001, que pandas no representa).
* `ventana(ds, variable)`: selecciona, uno a uno, los centros nativos esperados de
  la ventana SOME + Niño 1+2 (65 × 70, latitud ascendente) y devuelve las fechas y
  los campos. Acepta longitudes 0–360 (PSL y NCEI) o −180–180, latitud ascendente o
  descendente, y la dimensión `zlev` de NCEI. Si falta un centro, ErrorFormato.
* `a_some(nativo)`: de los centros x.125/x.375 a los centros SOME (múltiplos de
  0.25°): cada centro SOME es la esquina común de 4 celdas nativas y toma la media de
  las válidas si hay al menos `MIN_VALIDAS_2X2` (2 de 4); si no, sin dato (tierra).
  Es la misma regla que `chirps.malla.reproyectar_a_some` (replicada aquí para no
  depender de las constantes de CHIRPS).
* `region_nino12(nativo)` y `pesos_nino12()`: la región Niño 1+2 oficial (0–10° S,
  90–80° O; 40 × 40 celdas nativas) y sus pesos de área (coseno de la latitud).
"""

from __future__ import annotations

import io
import warnings
from datetime import date, timedelta

import numpy as np

from .constantes import (COLUMNAS_NATIVAS, COLUMNAS_NINO12, FILA_SOME_0, FILAS_NATIVAS, FILAS_NINO12,
                         LAT_NATIVA_SUR, LON_NATIVA_OESTE, MIN_VALIDAS_2X2, ErrorFormato)

PASO = 0.25
LAT_NATIVA = np.round(LAT_NATIVA_SUR + PASO * np.arange(FILAS_NATIVAS), 4)       # −9.875 … 6.125
LON_NATIVA = np.round(LON_NATIVA_OESTE + PASO * np.arange(COLUMNAS_NATIVAS), 4)  # −95.125 … −77.875
_TOL = 1e-3
BASE_LTM = -657073.0                      # 0001-01-01 en «days since 1800-01-01» de la climatología diaria de PSL

TEXTO_PROCESAMIENTO = (
    "NOAA OISST v2.1 diario a 0.25° (centros nativos en x.125° y x.375°), obtenido como subconjunto NCSS del "
    "servidor THREDDS de NOAA PSL (archivos sst.day.mean.AAAA.nc) o, como respaldo, de los archivos diarios "
    "de NOAA NCEI; ambos entregan el mismo producto (comprobado valor a valor). Paso a la malla SOME (centros "
    "en múltiplos de 0.25°): cada centro SOME es la esquina común de 4 celdas nativas y toma la media de las "
    "válidas si hay al menos 2 de 4; si no, sin dato (tierra). Los últimos ~15 días son preliminares: "
    "NOAA/NCEI los reemplaza por la versión final y el SOME los vuelve a pedir hasta que lo son. "
    "Unidades: °C."
)


# ---------------------------------------------------------------------------
# NetCDF
# ---------------------------------------------------------------------------
def abrir_netcdf(contenido: bytes):
    """xarray.Dataset en memoria (h5netcdf, `decode_times=False`). ErrorFormato si no es un NetCDF4 legible
    (p. ej., una página HTML de error con estado 200)."""
    import xarray as xr
    if not contenido or contenido[:4] != b"\x89HDF":
        inicio = bytes(contenido[:60] or b"").decode("latin-1", "replace").strip()
        raise ErrorFormato(f"La respuesta no es un NetCDF4 (comienza con «{inicio[:40]}»).")
    try:
        ds = xr.open_dataset(io.BytesIO(contenido), engine="h5netcdf", decode_times=False)
        return ds.load()
    except Exception as e:  # noqa: BLE001
        raise ErrorFormato(f"No se pudo leer el NetCDF ({type(e).__name__}: {e}).") from e


def fechas_de(ds) -> list[date]:
    """Fechas del eje `time` («days since AAAA-MM-DD …», con o sin hora; un día por valor)."""
    if "time" not in ds.variables:
        raise ErrorFormato("El NetCDF no tiene eje de tiempo.")
    unidades = str(ds["time"].attrs.get("units") or "")
    if not unidades.startswith("days since "):
        raise ErrorFormato(f"Unidades de tiempo inesperadas: «{unidades}».")
    try:
        texto = unidades.removeprefix("days since ").strip()
        a, m, d = (int(x) for x in texto[:10].split("-"))
        origen = date(a, m, d)
    except (TypeError, ValueError) as e:
        raise ErrorFormato(f"Unidades de tiempo inesperadas: «{unidades}».") from e
    valores = np.atleast_1d(np.asarray(ds["time"].values, dtype=float))
    # NCEI marca el día a las 12:00 (p. ej., 17635.5 desde 1978-01-01 12:00): se toma el día entero
    return [origen + timedelta(days=int(np.floor(v + 1e-6))) for v in valores]


def dias_del_anio_ltm(ds) -> list[int]:
    """Día del año (1–365) de cada paso de la climatología diaria de PSL (año 0001, no bisiesto)."""
    valores = np.atleast_1d(np.asarray(ds["time"].values, dtype=float))
    unidades = str(ds["time"].attrs.get("units") or "")
    if not unidades.startswith("days since 1800-01-01"):
        raise ErrorFormato(f"Unidades de tiempo inesperadas en la climatología diaria: «{unidades}».")
    # En el calendario «standard» de netCDF (juliano antes de 1582) el 0001-01-01 es el día −657073 desde
    # 1800-01-01 (comprobado: `actual_range` = [−657073, −656709], 365 días); no es el gregoriano proléptico.
    dias = [int(round(v - BASE_LTM)) + 1 for v in valores]
    if any(d < 1 or d > 365 for d in dias):
        raise ErrorFormato("La climatología diaria trae días fuera del año 0001.")
    return dias


def _indices(eje: np.ndarray, esperados: np.ndarray, nombre: str) -> np.ndarray:
    eje = np.asarray(eje, dtype=float)
    idx = np.empty(esperados.size, dtype=np.int64)
    for k, v in enumerate(esperados):
        cerca = np.nonzero(np.abs(eje - v) <= _TOL)[0]
        if cerca.size != 1:
            raise ErrorFormato(f"Falta el centro {v:.3f}° de {nombre} en la malla recibida "
                               f"({eje.size} valores de {eje.min():.3f} a {eje.max():.3f}).")
        idx[k] = int(cerca[0])
    return idx


def ventana(ds, variable: str = "sst", lat_centros: np.ndarray | None = None,
            lon_centros: np.ndarray | None = None) -> np.ndarray:
    """Campos (n, 65, 70) float32 de la ventana nativa (u otros centros: `lat_centros` × `lon_centros`, en
    −180–180), latitud y longitud ascendentes. NaN = tierra o hielo."""
    lat_centros = LAT_NATIVA if lat_centros is None else np.asarray(lat_centros, dtype=float)
    lon_centros = LON_NATIVA if lon_centros is None else np.asarray(lon_centros, dtype=float)
    if variable not in ds.variables:
        raise ErrorFormato(f"El NetCDF no trae la variable «{variable}».")
    var = ds[variable]
    nombres_lat = [d for d in var.dims if d in ("lat", "latitude")]
    nombres_lon = [d for d in var.dims if d in ("lon", "longitude")]
    if not nombres_lat or not nombres_lon:
        raise ErrorFormato(f"La variable «{variable}» no tiene dimensiones de latitud y longitud.")
    dlat, dlon = nombres_lat[0], nombres_lon[0]
    unidades = str(var.attrs.get("units") or "").strip().lower()
    if unidades and unidades not in ("degc", "celsius", "degree_celsius", "degrees_c", "°c", "deg c"):
        raise ErrorFormato(f"Unidades inesperadas de {variable}: «{var.attrs.get('units')}».")
    lat = np.asarray(ds[dlat].values, dtype=float)
    lon = np.asarray(ds[dlon].values, dtype=float)
    lon_esperada = lon_centros % 360.0 if lon.max() > 180.0 else lon_centros
    ilat = _indices(lat, lat_centros, "latitud")
    ilon = _indices(lon, lon_esperada, "longitud")
    # zlev (NCEI) u otra dimensión unitaria fuera
    otras = [d for d in var.dims if d not in (dlat, dlon, "time")]
    for d in otras:
        if var.sizes[d] != 1:
            raise ErrorFormato(f"Dimensión inesperada «{d}» de tamaño {var.sizes[d]}.")
        var = var.isel({d: 0})
    if "time" not in var.dims:
        var = var.expand_dims("time")
    var = var.transpose("time", dlat, dlon)
    datos = np.asarray(var.values, dtype=np.float32)
    datos = datos[:, ilat[:, None], ilon[None, :]]
    rango = var.attrs.get("valid_range")
    if variable == "sst":                     # fuera de −5…45 °C no es temperatura del mar
        datos[(datos < -5.0) | (datos > 45.0)] = np.nan
    if rango is not None and np.size(rango) == 2:
        datos[(datos < float(np.min(rango)) - 1e-3) | (datos > float(np.max(rango)) + 1e-3)] = np.nan
    return datos


# ---------------------------------------------------------------------------
# Malla SOME y región Niño 1+2
# ---------------------------------------------------------------------------
def a_some(nativo: np.ndarray, min_validas: int = MIN_VALIDAS_2X2) -> np.ndarray:
    """(…, 65, 70) nativo → (…, 49, 69) en la malla SOME (media de las válidas de cada 2 × 2)."""
    if nativo.shape[-2:] != (FILAS_NATIVAS, COLUMNAS_NATIVAS):
        raise ValueError(f"Se esperaba (…, {FILAS_NATIVAS}, {COLUMNAS_NATIVAS}); llegó {nativo.shape}")
    a = np.asarray(nativo, dtype=np.float32)[..., FILA_SOME_0:, :]             # 50 filas: −6.125 … 6.125
    cuatro = np.stack([a[..., :-1, :-1], a[..., 1:, :-1], a[..., :-1, 1:], a[..., 1:, 1:]], axis=0)
    validas = np.isfinite(cuatro).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        media = np.nanmean(cuatro, axis=0)
    media[validas < min_validas] = np.nan
    return media.astype(np.float32)


def region_nino12(nativo: np.ndarray) -> np.ndarray:
    """(…, 65, 70) → (…, 40, 40): celdas nativas de la región Niño 1+2 oficial (0–10° S, 90–80° O)."""
    f0, f1 = FILAS_NINO12
    c0, c1 = COLUMNAS_NINO12
    return np.asarray(nativo, dtype=np.float32)[..., f0:f1, c0:c1]


def lat_nino12() -> np.ndarray:
    return LAT_NATIVA[FILAS_NINO12[0]:FILAS_NINO12[1]]


def lon_nino12() -> np.ndarray:
    return LON_NATIVA[COLUMNAS_NINO12[0]:COLUMNAS_NINO12[1]]


def pesos_nino12() -> np.ndarray:
    """(40, 40) pesos de área (coseno de la latitud del centro de cada celda nativa)."""
    return np.broadcast_to(np.cos(np.deg2rad(lat_nino12()))[:, None], (40, 40)).astype(np.float64)


def media_area(campos: np.ndarray, pesos: np.ndarray, mascara: np.ndarray | None = None) -> np.ndarray:
    """Media ponderada por área de (n, f, c) sobre las celdas válidas (y de `mascara`, si se da). NaN si ninguna."""
    campos = np.asarray(campos, dtype=np.float64)
    validas = np.isfinite(campos)
    if mascara is not None:
        validas &= np.broadcast_to(mascara, campos.shape)
    w = np.where(validas, np.broadcast_to(pesos, campos.shape), 0.0)
    suma = np.where(validas, campos, 0.0)
    total = w.sum(axis=(-2, -1))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(total > 0, (suma * w).sum(axis=(-2, -1)) / np.where(total > 0, total, 1.0), np.nan)
