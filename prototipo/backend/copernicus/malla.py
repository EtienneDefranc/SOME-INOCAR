"""Paso de la malla nativa de 1/12° de Copernicus Marine a la malla SOME de 0.25°.

Malla nativa: centros en −80 + k/12 (latitud) y −180 + k/12 (longitud), ambos
ascendentes. Cada celda SOME (centro múltiplo de 0.25°, bordes a ±0.125°)
contiene exactamente 3 × 3 = 9 centros nativos (ningún centro cae en un borde:
distan al menos 1/24°).

Ventana del área de estudio: bordes 6.125° S – 6.125° N y 95.125° O –
77.875° O → 147 × 207 celdas nativas (centros −6.0833 … 6.0833 y −95.0833 …
−77.9167); filas 887–1033 y columnas 1019–1225 contadas desde −80° y −180°.

Convención: los arreglos de la ventana tienen la latitud ascendente (fila 0 =
centro −6.0833°) y la longitud ascendente (columna 0 = −95.0833°).

* `indices_nativos(lat, lon)`: valida que las coordenadas leídas estén en la
  malla de 1/12° (redondeo y contigüidad; no se suponen) y devuelve su fila y
  columna dentro de la ventana (pueden quedar fuera de ella: `colocar` las
  descarta).
* `colocar(...)`: pone una lectura (completa o parcial) en el lienzo 147 × 207
  con la máscara «leído».
* `agregar_3x3(nativo, cubierto)`: media de las celdas válidas de cada bloque de
  3 × 3 si hay al menos 5 de 9; la celda SOME cuenta como obtenida solo si sus
  9 centros nativos se leyeron.
"""

from __future__ import annotations

import numpy as np

from .constantes import (COLUMNA0, COLUMNAS_NATIVAS, FILA0, FILAS_NATIVAS, LAT0_NATIVA, LON0_NATIVA,
                         MIN_VALIDAS_3X3, PASO_NATIVO, ErrorFormato)

VENTANA = {"lat": (-6.125, 6.125), "lon": (-95.125, -77.875)}   # bordes de la malla SOME
NLAT_SOME, NLON_SOME = FILAS_NATIVAS // 3, COLUMNAS_NATIVAS // 3   # 49 × 69
_TOLERANCIA_GRADOS = 1e-3      # las coordenadas nativas vienen en float32 (error ~5e-6°)

TEXTO_PROCESAMIENTO = (
    "Análisis diario de Copernicus Marine GLOBAL_ANALYSISFORECAST_PHY_001_024 (malla de 1/12°, 50 niveles), "
    "leído con el Copernicus Marine Toolbox (copernicusmarine.open_dataset, servicio ARCO) recortando el área, "
    "los días y solo los niveles necesarios. Profundidad: cada nivel SOME se interpola linealmente entre los dos "
    "niveles del modelo que lo rodean (0 m = nivel de 0.494 m; con dato solo si ambos niveles lo tienen). "
    "Horizontal: cada celda SOME de 0.25° es la media de las 3 × 3 celdas de 1/12° cuyo centro cae en ella, si al "
    "menos 5 de las 9 tienen dato; si no, sin dato (tierra o fondo). Solo se sirven días de análisis (el último es "
    "el miércoles del boletín semanal menos 2 días); el pronóstico no se sirve. Unidades del producto, sin "
    "conversión."
)


def _indices_ejes(valores: np.ndarray, origen: float, primero: int, nombre: str) -> np.ndarray:
    v = np.asarray(valores, dtype=np.float64)
    if v.ndim != 1 or v.size == 0:
        raise ErrorFormato(f"La coordenada {nombre} leída no es un eje de una dimensión con valores.")
    k = np.round((v - origen) / PASO_NATIVO)
    if np.max(np.abs(origen + k * PASO_NATIVO - v)) > _TOLERANCIA_GRADOS:
        raise ErrorFormato(f"La coordenada {nombre} leída no está en la malla de 1/12° esperada "
                           f"(primer valor {float(v[0]):.5f}).")
    if v.size > 1 and not np.all(np.diff(k) == 1):
        raise ErrorFormato(f"La coordenada {nombre} leída no es ascendente y contigua en la malla de 1/12°.")
    return (k.astype(np.int64) - primero)


def indices_nativos(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Filas y columnas (dentro de la ventana 147 × 207) de las coordenadas nativas leídas.

    Exige coordenadas en la malla de 1/12° (con tolerancia de 0.001°), ascendentes y contiguas;
    si no, `ErrorFormato` (la consulta cae al respaldo señalado y el error queda registrado).
    """
    return (_indices_ejes(lat, LAT0_NATIVA, FILA0, "latitud"),
            _indices_ejes(lon, LON0_NATIVA, COLUMNA0, "longitud"))


def lienzo(*forma_previa: int) -> tuple[np.ndarray, np.ndarray]:
    """Lienzo (…, 147, 207) de NaN y máscara «leído» (147, 207) en False."""
    return (np.full((*forma_previa, FILAS_NATIVAS, COLUMNAS_NATIVAS), np.nan, dtype=np.float32),
            np.zeros((FILAS_NATIVAS, COLUMNAS_NATIVAS), dtype=bool))


def colocar(destino: np.ndarray, cubierto: np.ndarray, datos: np.ndarray,
            filas: np.ndarray, columnas: np.ndarray) -> None:
    """Copia `datos` (…, len(filas), len(columnas)) en el lienzo, descartando lo que cae fuera de la ventana."""
    mf = (filas >= 0) & (filas < FILAS_NATIVAS)
    mc = (columnas >= 0) & (columnas < COLUMNAS_NATIVAS)
    if not mf.any() or not mc.any():
        return
    f, c = filas[mf], columnas[mc]
    sub = datos[..., mf, :][..., :, mc]
    destino[..., f[:, None], c[None, :]] = sub.astype(np.float32)
    cubierto[f[:, None], c[None, :]] = True


def agregar_3x3(nativo: np.ndarray, cubierto: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(…, 147, 207) → valores (…, 49, 69) y (49, 69) bool «celda SOME obtenida».

    Valor = media de las celdas nativas válidas del bloque 3 × 3 si hay al menos 5 de 9; si no, NaN.
    Una celda SOME está obtenida si sus 9 centros nativos están en la ventana leída (aunque sea tierra).
    """
    if nativo.shape[-2:] != (FILAS_NATIVAS, COLUMNAS_NATIVAS):
        raise ErrorFormato(f"Se esperaba (…, {FILAS_NATIVAS}, {COLUMNAS_NATIVAS}); llegó {nativo.shape}.")
    if cubierto.shape != (FILAS_NATIVAS, COLUMNAS_NATIVAS):
        raise ErrorFormato(f"Máscara de lectura con forma {cubierto.shape}.")
    *pre, _, _ = nativo.shape
    bloques = nativo.reshape(*pre, NLAT_SOME, 3, NLON_SOME, 3)
    validas = np.isfinite(bloques).sum(axis=(-3, -1))
    suma = np.nansum(bloques, axis=(-3, -1), dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        media = (suma / validas).astype(np.float32)
    media[validas < MIN_VALIDAS_3X3] = np.nan
    obtenida = cubierto.reshape(NLAT_SOME, 3, NLON_SOME, 3).all(axis=(1, 3))
    return media, obtenida


def filas_nativas_de(ilat: np.ndarray) -> tuple[int, int]:
    """Filas nativas (dentro de la ventana) que cubren las filas SOME `ilat` (contiguas): (primera, última)."""
    return int(np.min(ilat)) * 3, int(np.max(ilat)) * 3 + 2


def columnas_nativas_de(ilon: np.ndarray) -> tuple[int, int]:
    return int(np.min(ilon)) * 3, int(np.max(ilon)) * 3 + 2


def limites_geograficos(fila0: int, fila1: int, col0: int, col1: int) -> tuple[tuple[float, float], tuple[float, float]]:
    """((lat_min, lat_max), (lon_min, lon_max)) que incluyen exactamente esas filas y columnas nativas de la
    ventana con la selección «inside» (medio paso nativo de margen a cada lado)."""
    medio = PASO_NATIVO / 2
    lat = (LAT0_NATIVA + (FILA0 + fila0) * PASO_NATIVO - medio, LAT0_NATIVA + (FILA0 + fila1) * PASO_NATIVO + medio)
    lon = (LON0_NATIVA + (COLUMNA0 + col0) * PASO_NATIVO - medio,
           LON0_NATIVA + (COLUMNA0 + col1) * PASO_NATIVO + medio)
    return (round(lat[0], 6), round(lat[1], 6)), (round(lon[0], 6), round(lon[1], 6))
