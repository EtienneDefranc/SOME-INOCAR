"""Paso de las mallas nativas de CHIRPS v3 a la malla SOME de 0.25°.

* `agregar_5x5`: de 0.05° a 0.25° nativo (media de las celdas válidas de cada
  bloque de 5×5; NaN si hay menos de 13 de 25 válidas).
* `reproyectar_a_some`: de 0.25° nativo (centros en x.125/x.375/…) a la malla
  SOME (centros en múltiplos de 0.25°). Cada centro SOME es la esquina común
  de 4 celdas nativas; su valor es la media de las válidas si hay al menos 2
  de 4; si no, NaN.

Convención: los arreglos nativos se pasan con la latitud ASCENDENTE (fila 0 =
celda con centro −6.125°) y la longitud ascendente (columna 0 = −95.125°),
50 × 70. Los GeoTIFF vienen de norte a sur y se invierten antes.
"""

from __future__ import annotations

import warnings

import numpy as np

from .constantes import COLUMNAS_NATIVAS, FILAS_NATIVAS, MIN_VALIDAS_2X2, MIN_VALIDAS_5X5

TEXTO_PROCESAMIENTO = (
    "Recorte de 6.25° S – 6.25° N y 95.25° O – 77.75° O de CHIRPS v3 diario, obtenido por uno de dos "
    "caminos que entregan el producto del CHC: (1) el servidor del Climate Hazards Center, "
    "leyendo por rangos HTTP solo el recorte del GeoTIFF diario (final «rnl», disgregación diaria con "
    "ERA5, y preliminar «sat», disgregación con IMERG, ambos a 0.05°); o (2) la réplica pública "
    "de dynamical.org en formato Zarr/Icechunk (final y preliminar a 0.05°, en kg m⁻² s⁻¹ convertidos "
    "a mm/día y con los valores redondeados a 8 bits de mantisa, error ≤ 0.2 %). Lo que llega a 0.05° "
    "se agrega a 0.25° con la media de las celdas válidas de cada bloque de 5×5 (se exigen al menos "
    "13 de 25). Reproyección a la malla SOME (centros en múltiplos de 0.25°): cada centro SOME es la "
    "esquina común de 4 celdas nativas y toma la media de las válidas si hay al menos 2 de 4; si no, "
    "sin dato. El valor −9999, los NaN y los valores menores que −1 se tratan como sin dato (mar); los "
    "negativos pequeños se fijan en 0. Unidades: mm/día."
)


def agregar_5x5(arr: np.ndarray) -> np.ndarray:
    """(…, 5·F, 5·C) a 0.05° → (…, F, C) a 0.25° (media de válidas; NaN si < 13 de 25)."""
    *pre, filas, cols = arr.shape
    if filas % 5 or cols % 5:
        raise ValueError(f"Forma {arr.shape} no divisible en bloques de 5×5")
    bloques = arr.reshape(*pre, filas // 5, 5, cols // 5, 5)
    validas = np.isfinite(bloques).sum(axis=(-3, -1))
    suma = np.nansum(bloques, axis=(-3, -1))
    with np.errstate(invalid="ignore", divide="ignore"):
        media = suma / validas
    media[validas < MIN_VALIDAS_5X5] = np.nan
    return media.astype(np.float32)


def reproyectar_a_some(nativo_asc: np.ndarray) -> np.ndarray:
    """(…, 50, 70) a 0.25° nativo (latitud ascendente) → (…, 49, 69) en la malla SOME."""
    if nativo_asc.shape[-2:] != (FILAS_NATIVAS, COLUMNAS_NATIVAS):
        raise ValueError(f"Se esperaba (…, {FILAS_NATIVAS}, {COLUMNAS_NATIVAS}); llegó {nativo_asc.shape}")
    a = nativo_asc
    cuatro = np.stack([a[..., :-1, :-1], a[..., 1:, :-1], a[..., :-1, 1:], a[..., 1:, 1:]], axis=0)
    validas = np.isfinite(cuatro).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        media = np.nanmean(cuatro, axis=0)
    media[validas < MIN_VALIDAS_2X2] = np.nan
    return media.astype(np.float32)
