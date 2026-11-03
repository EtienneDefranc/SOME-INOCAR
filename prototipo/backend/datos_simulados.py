"""Generador determinista de campos simulados para la demostración del SOME.

Implementa la sección 3 de la especificación del prototipo:

- malla de 0.25° del área de estudio (6° N a 6° S, 95° O a 78° O);
- máscara tierra/mar a partir de una línea de costa interpolada y de las
  islas Galápagos;
- climatología analítica realista (gradiente latitudinal, lengua fría
  ecuatorial, afloramiento costero, ciclo estacional) para cada variable;
- eventos interanuales (índice Niño 1+2 interpolado entre nodos mensuales);
- ruido espacial suavizado y reproducible (semilla fija por fecha).

Todo está vectorizado con numpy: las funciones reciben lotes de fechas y
devuelven arreglos con forma (tiempo, lat, lon). Ningún valor proviene de una
fuente real: son «Datos simulados para demostración».
"""

from __future__ import annotations

import math
import warnings
from datetime import date, datetime
from functools import lru_cache
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter

from . import configuracion as cfg

LAT = cfg.LAT
LON = cfg.LON
NLAT = LAT.size
NLON = LON.size

# Variables escalares que el generador sabe producir
VARIABLES_ESCALARES = ("sst", "thetao", "so", "uo", "vo", "zos", "mlotst", "precip")
# Variables que dependen de la profundidad
VARIABLES_3D = ("thetao", "so", "uo", "vo")

# Orden fijo de extracción del ruido por fecha (reproducibilidad por variable).
# thetao tiene su propio ruido para que NOAA y Copernicus no coincidan exactamente.
_ORDEN_RUIDO = {"sst": 0, "so": 1, "uo": 2, "vo": 3, "zos": 4, "mlotst": 5, "precip": 6, "thetao": 7}
_N_RUIDOS = len(_ORDEN_RUIDO)

# «Termostato» tropical: por encima de este umbral la SST se comprime suavemente
# (la evaporación limita las aguas más cálidas a ~30–31 °C en la realidad).
_TERMOSTATO_UMBRAL = 29.5
_TERMOSTATO_ESCALA = 1.2
_SIGMA_FILTRO = 2.0
# Un ruido blanco N(0,1) filtrado con una gaussiana 2D de sigma s queda con
# desviación 1/(2·s·sqrt(pi)); este factor lo devuelve a desviación ≈ 1.
_FACTOR_NORMALIZACION = 2.0 * _SIGMA_FILTRO * math.sqrt(math.pi)

_AMPLITUD_RUIDO = {
    "sst": 0.35,      # °C
    "so": 0.08,       # PSU
    "uo": 0.04,       # m/s
    "vo": 0.04,       # m/s
    "zos": 0.01,      # m
    "mlotst": 4.0,    # m
}

# ---------------------------------------------------------------------------
# Línea de costa y máscara tierra/mar
# ---------------------------------------------------------------------------
# Puntos (lat, lon) de la costa, ordenados por latitud creciente.
_PUNTOS_COSTA = [
    (-6.0, -80.9), (-5.5, -81.0), (-4.5, -81.2), (-3.5, -80.3), (-3.0, -80.0),
    (-2.2, -81.0), (-1.0, -80.8), (0.0, -80.1), (1.0, -79.7), (2.0, -78.8),
    (4.0, -77.5), (6.0, -77.4),
]
_COSTA_LAT = np.array([p[0] for p in _PUNTOS_COSTA])
_COSTA_LON = np.array([p[1] for p in _PUNTOS_COSTA])

# Cajas de las islas Galápagos: (lat_min, lat_max, lon_min, lon_max)
_CAJAS_GALAPAGOS = [
    (-1.0, 0.0, -91.5, -90.75),      # Isabela / Fernandina
    (-0.75, -0.5, -90.5, -90.25),    # Santa Cruz
]
_TOL = 1e-6


def costa(lat):
    """Longitud de la costa continental para cada latitud (interpolación lineal)."""
    return np.interp(np.asarray(lat, dtype=float), _COSTA_LAT, _COSTA_LON)


def mascara_galapagos(lat, lon) -> np.ndarray:
    """bool[lat, lon]: True en las celdas de las islas Galápagos."""
    lat = np.asarray(lat, dtype=float)[:, None]
    lon = np.asarray(lon, dtype=float)[None, :]
    mascara = np.zeros((lat.size, lon.size), dtype=bool)
    for lat0, lat1, lon0, lon1 in _CAJAS_GALAPAGOS:
        mascara |= ((lat >= lat0 - _TOL) & (lat <= lat1 + _TOL)
                    & (lon >= lon0 - _TOL) & (lon <= lon1 + _TOL))
    return mascara


def mascara_tierra(lat, lon) -> np.ndarray:
    """bool[lat, lon]: True en tierra (continente al este de la costa y Galápagos)."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    continente = lon[None, :] > costa(lat)[:, None] + _TOL
    return continente | mascara_galapagos(lat, lon)


# Mallas precalculadas con forma apta para difusión (1, lat, 1) y (1, 1, lon)
_LAT3 = LAT[None, :, None]
_LON3 = LON[None, None, :]
_COSTA3 = costa(LAT)[None, :, None]
_TIERRA = mascara_tierra(LAT, LON)[None, :, :]
_MAR = ~_TIERRA
_GALAPAGOS = mascara_galapagos(LAT, LON)[None, :, :]


def _clip01(x):
    return np.clip(x, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Tiempo: día del año y fase estacional
# ---------------------------------------------------------------------------
def normalizar_fechas(fechas) -> np.ndarray:
    """Convierte fechas (date, datetime, cadenas ISO, DatetimeIndex…) a datetime64[D]."""
    if isinstance(fechas, (date, datetime, str, np.datetime64)):
        fechas = [fechas]
    indice = pd.to_datetime(list(fechas) if not isinstance(fechas, pd.DatetimeIndex) else fechas)
    return indice.values.astype("datetime64[D]")


def dia_del_anio(fechas_d: np.ndarray) -> np.ndarray:
    """Día del año (1–366) de cada fecha datetime64[D]."""
    inicio_anio = fechas_d.astype("datetime64[Y]").astype("datetime64[D]")
    return (fechas_d - inicio_anio).astype(int) + 1


def _fase(d3: np.ndarray) -> np.ndarray:
    """Fase estacional: máximo de temperatura en marzo (d ≈ 75)."""
    return 2.0 * math.pi * (d3 - 75.0) / 365.25


def _ordinales(fechas_d: np.ndarray) -> np.ndarray:
    """Ordinal proléptico gregoriano (como date.toordinal) de cada fecha."""
    base = date(1970, 1, 1).toordinal()
    return fechas_d.astype("datetime64[D]").astype(np.int64) + base


# ---------------------------------------------------------------------------
# Índice ENSO (Niño 1+2) simulado
# ---------------------------------------------------------------------------
# Nodos mensuales (año, mes, °C); se ubican el día 15 de cada mes.
_NODOS_ENSO = [
    (2023, 10, 2.4), (2023, 12, 2.8), (2024, 2, 2.2), (2024, 4, 1.2), (2024, 6, 0.2),
    (2024, 9, -0.6), (2024, 12, -0.9), (2025, 3, -0.4), (2025, 6, 0.0), (2025, 9, 0.3),
    (2025, 12, 0.7), (2026, 3, 1.4), (2026, 6, 1.1), (2026, 9, 1.6), (2026, 10, 1.8),
]
_ENSO_X = np.array([date(a, m, 15).toordinal() for a, m, _ in _NODOS_ENSO], dtype=float)
_ENSO_Y = np.array([v for _, _, v in _NODOS_ENSO], dtype=float)


def indice_enso(fechas):
    """Índice Niño 1+2 simulado (°C) interpolado linealmente entre nodos mensuales.

    Acepta una fecha o un lote de fechas; fuera de los nodos se mantiene el
    valor del extremo más cercano.
    """
    escalar = isinstance(fechas, (date, datetime, str, np.datetime64))
    fechas_d = normalizar_fechas(fechas)
    valores = np.interp(_ordinales(fechas_d).astype(float), _ENSO_X, _ENSO_Y)
    return float(valores[0]) if escalar else valores


def nodos_enso() -> list[dict]:
    """Nodos del índice para la UI (fecha ISO, valor)."""
    return [{"fecha": date(a, m, 15).isoformat(), "valor": v} for a, m, v in _NODOS_ENSO]


@lru_cache(maxsize=1)
def peso_enso() -> np.ndarray:
    """Peso espacial (1, lat, lon) de la anomalía ENSO, mayor en Niño 1+2.

    La forma es la de la especificación: 0.4 + 0.6·exp(−((lat + 4)/5)²)·clip((lon + 95)/10).
    Se normaliza para que su promedio sobre las celdas marinas de la región
    Niño 1+2 (90° O–80° O, 6° S–0°) sea exactamente 1: así la anomalía media
    de SST en esa región coincide con el valor del índice.
    """
    bruto = 0.4 + 0.6 * np.exp(-((_LAT3 + 4.0) / 5.0) ** 2) * _clip01((_LON3 + 95.0) / 10.0)
    bruto = np.broadcast_to(bruto, (1, NLAT, NLON))
    en_nino12 = ((_LAT3 >= -6.0 - _TOL) & (_LAT3 <= 0.0 + _TOL)
                 & (_LON3 >= -90.0 - _TOL) & (_LON3 <= -80.0 + _TOL)) & _MAR
    return bruto / bruto[en_nino12].mean()


# ---------------------------------------------------------------------------
# Climatología analítica por variable (sin eventos ni ruido)
# ---------------------------------------------------------------------------
def capa_mezcla_climatologica(d3: np.ndarray) -> np.ndarray:
    """Capa de mezcla climatológica (m), forma (T, lat, 1)."""
    return 25.0 + 10.0 * _clip01(-_LAT3 / 6.0) + 8.0 * np.cos(_fase(d3) + math.pi)


def clim_sst(d3: np.ndarray) -> np.ndarray:
    """Temperatura superficial del mar climatológica (°C), forma (T, lat, lon)."""
    base = 27.0 - 0.12 * (_LAT3 + 2.0) ** 2 * 0.35
    lengua_fria = -2.2 * np.exp(-((_LAT3 + 0.5) / 1.6) ** 2) * _clip01((_LON3 + 78.0) / -17.0)
    afloramiento = (-1.8 * np.exp(-((_LON3 - _COSTA3 + 1.0) / 1.5) ** 2)
                    * _clip01(-_LAT3 / 6.0))
    estacional = 1.3 * np.cos(_fase(d3)) * (1.0 + 0.4 * _clip01(-_LAT3 / 6.0))
    return base + lengua_fria + afloramiento + estacional


def _termostato(temperatura: np.ndarray) -> np.ndarray:
    """Comprime suavemente las temperaturas superiores al umbral (tope ≈ umbral + escala)."""
    exceso = temperatura - _TERMOSTATO_UMBRAL
    comprimido = _TERMOSTATO_UMBRAL + _TERMOSTATO_ESCALA * np.tanh(exceso / _TERMOSTATO_ESCALA)
    return np.where(exceso > 0, comprimido, temperatura)


def _nanmean_silencioso(arreglo: np.ndarray, axis=None) -> np.ndarray:
    """nanmean sin el aviso «Mean of empty slice» en las celdas sin dato."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(arreglo, axis=axis)


def _perfil_termico(z: float, capa_mezcla: np.ndarray) -> np.ndarray:
    """Descenso de temperatura respecto a la superficie a la profundidad z (m)."""
    return np.clip((z - capa_mezcla) * 0.11, 0.0, 12.0)


def clim_so(z: float) -> np.ndarray:
    """Salinidad climatológica (PSU), forma (1, lat, lon); no tiene ciclo estacional."""
    valor = (34.2 + 0.6 * _clip01((_LON3 + 95.0) / 17.0)
             - 1.2 * np.exp(-((_LAT3 - 3.0) / 2.0) ** 2) * _clip01((_LON3 + 82.0) / 4.0)
             - 0.8 * np.exp(-((_LAT3 + 3.0) / 1.2) ** 2) * np.exp(-((_LON3 + 80.5) / 1.0) ** 2)
             + 0.004 * z)
    return np.broadcast_to(valor, (1, NLAT, NLON))


def clim_uo(z: float) -> np.ndarray:
    """Velocidad zonal climatológica (m/s), forma (1, lat, lon)."""
    valor = (-0.25 - 0.2 * np.exp(-(_LAT3 / 1.5) ** 2)
             + 0.35 * np.exp(-((_LAT3 - 4.5) / 1.2) ** 2)) * math.exp(-z / 80.0)
    return np.broadcast_to(valor, (1, NLAT, NLON))


def clim_vo(z: float) -> np.ndarray:
    """Velocidad meridional climatológica (m/s), forma (1, lat, lon)."""
    valor = (0.08 * _clip01(-_LAT3 / 6.0)
             * np.exp(-((_LON3 - _COSTA3 + 1.5) / 2.0) ** 2)) * math.exp(-z / 80.0)
    return np.broadcast_to(valor, (1, NLAT, NLON))


def clim_zos(d3: np.ndarray) -> np.ndarray:
    """Altura del nivel del mar climatológica (m), forma (T, lat, lon)."""
    valor = (0.15 * np.exp(-((_LAT3 - 3.0) / 3.0) ** 2)
             - 0.05 * _clip01(-_LAT3 / 6.0) + 0.03 * np.cos(_fase(d3)))
    return np.broadcast_to(valor, (d3.shape[0], NLAT, NLON))


def clim_mlotst(d3: np.ndarray) -> np.ndarray:
    """Profundidad de la capa de mezcla climatológica (m), forma (T, lat, lon)."""
    return np.broadcast_to(capa_mezcla_climatologica(d3), (d3.shape[0], NLAT, NLON))


def _pico_lluvioso(d3: np.ndarray) -> np.ndarray:
    """Factor estacional de la precipitación (0–2), máximo en marzo."""
    return 1.0 + np.cos(2.0 * math.pi * (d3 - 60.0) / 365.25)


def clim_precip(d3: np.ndarray) -> np.ndarray:
    """Precipitación media climatológica (mm/día) en tierra; NaN en el mar."""
    pico = _pico_lluvioso(d3)
    valor = (0.5 + 6.0 * pico) * np.ones((1, NLAT, NLON))
    valor = valor * (1.0 + 0.6 * _clip01((_LAT3 + 1.0) / 5.0))        # más lluvia al norte
    valor = valor * (0.35 + 0.65 * _clip01((_LAT3 + 5.0) / 3.0))      # más seco al sur de 3° S
    valor = np.where(_GALAPAGOS, valor * 0.4, valor)                   # Galápagos
    return np.where(_TIERRA, valor, np.nan)


def campo_climatologico(variable: str, fechas, profundidad: float | None = None) -> np.ndarray:
    """Climatología analítica de `variable` en las fechas dadas: (T, lat, lon).

    Sin eventos interanuales ni ruido. Las variables marinas son NaN en tierra
    y la precipitación es NaN en el mar.
    """
    z = float(profundidad or 0.0)
    fechas_d = normalizar_fechas(fechas)
    d3 = dia_del_anio(fechas_d).astype(float)[:, None, None]
    n = d3.shape[0]

    if variable == "sst":
        campo = clim_sst(d3)
    elif variable == "thetao":
        campo = clim_sst(d3) - _perfil_termico(z, capa_mezcla_climatologica(d3))
    elif variable == "so":
        campo = np.broadcast_to(clim_so(z), (n, NLAT, NLON))
    elif variable == "uo":
        campo = np.broadcast_to(clim_uo(z), (n, NLAT, NLON))
    elif variable == "vo":
        campo = np.broadcast_to(clim_vo(z), (n, NLAT, NLON))
    elif variable == "zos":
        campo = clim_zos(d3)
    elif variable == "mlotst":
        campo = clim_mlotst(d3)
    elif variable == "precip":
        return np.array(clim_precip(d3), dtype=float)
    else:
        raise ValueError(f"Variable desconocida para el generador: «{variable}»")

    return np.where(_MAR, campo, np.nan)


# ---------------------------------------------------------------------------
# Ruido reproducible
# ---------------------------------------------------------------------------
def ruido_suavizado(fechas, variable: str) -> np.ndarray:
    """Ruido espacial suavizado con desviación ≈ 1, forma (T, lat, lon).

    Para cada fecha se crea `np.random.default_rng(SEMILLA_BASE + ordinal(fecha))`
    y se extraen, en un orden fijo, los campos de ruido blanco de todas las
    variables; así el ruido de una fecha no depende del lote en que se pidió.
    """
    indice = _ORDEN_RUIDO[variable]
    fechas_d = normalizar_fechas(fechas)
    ordinales = _ordinales(fechas_d)
    blanco = np.empty((fechas_d.size, NLAT, NLON))
    for i, ordinal in enumerate(ordinales):
        rng = np.random.default_rng(cfg.SEMILLA_BASE + int(ordinal))
        blanco[i] = rng.standard_normal((_N_RUIDOS, NLAT, NLON))[indice]
    suave = gaussian_filter(blanco, sigma=(0, _SIGMA_FILTRO, _SIGMA_FILTRO), mode="reflect")
    return suave * _FACTOR_NORMALIZACION


# ---------------------------------------------------------------------------
# Campos completos: climatología + eventos + ruido
# ---------------------------------------------------------------------------
def generar_campos(variable: str, fechas, profundidad: float | None = None) -> np.ndarray:
    """Campos diarios simulados de `variable` para un lote de fechas: (T, lat, lon), float32.

    Incluye la climatología analítica, la anomalía interanual asociada al
    índice Niño 1+2 y el ruido reproducible. Las variables marinas son NaN en
    tierra; la precipitación es NaN en el mar.
    """
    if variable not in VARIABLES_ESCALARES:
        raise ValueError(f"Variable desconocida para el generador: «{variable}»")
    z = float(profundidad or 0.0)
    fechas_d = normalizar_fechas(fechas)
    if fechas_d.size == 0:
        return np.empty((0, NLAT, NLON), dtype=np.float32)

    d3 = dia_del_anio(fechas_d).astype(float)[:, None, None]
    e3 = indice_enso(fechas_d)[:, None, None]          # índice ENSO por fecha
    ruido = ruido_suavizado(fechas_d, variable)

    if variable == "sst":
        campo = _termostato(clim_sst(d3) + e3 * peso_enso() + _AMPLITUD_RUIDO["sst"] * ruido)

    elif variable == "thetao":
        capa_mezcla = capa_mezcla_climatologica(d3) + 6.0 * e3
        anomalia = e3 * peso_enso() * math.exp(-z / 60.0)
        amplitud = _AMPLITUD_RUIDO["sst"] * math.exp(-z / 120.0)
        # Sesgo leve del análisis (modelo) frente a la SST satelital, para que la
        # comparación NOAA vs. Copernicus muestre una diferencia creíble (≈ −0.15 °C).
        sesgo = -0.15 * math.exp(-z / 60.0)
        campo = _termostato(clim_sst(d3) - _perfil_termico(z, capa_mezcla) + anomalia + sesgo
                            + amplitud * ruido)

    elif variable == "so":
        campo = clim_so(z) + _AMPLITUD_RUIDO["so"] * ruido

    elif variable == "uo":
        campo = clim_uo(z) + _AMPLITUD_RUIDO["uo"] * math.exp(-z / 80.0) * ruido

    elif variable == "vo":
        campo = clim_vo(z) + _AMPLITUD_RUIDO["vo"] * math.exp(-z / 80.0) * ruido

    elif variable == "zos":
        campo = clim_zos(d3) + 0.06 * e3 + _AMPLITUD_RUIDO["zos"] * ruido

    elif variable == "mlotst":
        campo = clim_mlotst(d3) + 6.0 * e3 + _AMPLITUD_RUIDO["mlotst"] * ruido
        campo = np.maximum(campo, 5.0)

    else:  # precip
        campo = _precipitacion(d3, e3, ruido)
        return campo.astype(np.float32)

    return np.where(_MAR, campo, np.nan).astype(np.float32)


def _precipitacion(d3: np.ndarray, e3: np.ndarray, ruido: np.ndarray) -> np.ndarray:
    """Precipitación diaria (mm/día) con días secos intermitentes y ruido log-normal.

    - La media climatológica se multiplica por (1 + 0.45·índice ENSO), acotado a ≥ 0.2.
    - La fracción de días secos va de ~55 % fuera de la estación lluviosa a ~15 %
      en su pico; se decide con un umbral sobre el ruido suavizado.
    - En los días con lluvia la cantidad sigue un factor log-normal, escalado
      para que la media de largo plazo coincida con la climatología analítica.
    - Un tope suave (75·tanh(x/75)) evita valores extremos poco creíbles.
    """
    from scipy.stats import norm   # importación perezosa: scipy.stats tarda ~0.8 s en cargarse al arrancar

    sigma = 0.35
    pico = _pico_lluvioso(d3)
    media = clim_precip(d3) * np.clip(1.0 + 0.45 * e3, 0.2, None)
    p_seco = 0.55 - 0.40 * _clip01((pico - 0.8) / 1.2)            # (T,1,1)
    umbral = norm.ppf(p_seco)
    # Esperanza del factor log-normal condicionado a «día lluvioso»
    esperanza_lluvia = 1.0 - norm.cdf(umbral - sigma)
    factor = np.exp(sigma * ruido - 0.5 * sigma ** 2) / esperanza_lluvia
    cantidad = np.where(ruido >= umbral, media * factor, 0.0)
    cantidad = 75.0 * np.tanh(cantidad / 75.0)
    return np.where(_TIERRA, cantidad, np.nan)


# ---------------------------------------------------------------------------
# Climatología mensual de referencia (promedio de la función analítica por mes)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=64)
def _climatologia_mensual_cacheada(variable: str, z: float) -> np.ndarray:
    fechas = pd.date_range("2001-01-01", "2001-12-31", freq="D")   # año no bisiesto
    campos = campo_climatologico(variable, fechas, z)
    meses = fechas.month.values
    salida = np.empty((12, NLAT, NLON))
    for m in range(1, 13):
        salida[m - 1] = _nanmean_silencioso(campos[meses == m], axis=0)
    # Conserva la máscara (NaN) de la variable
    mascara = np.isnan(campos[0])
    salida[:, mascara] = np.nan
    return salida


def climatologia_mensual(variable: str, profundidad: float | None = None) -> np.ndarray:
    """Climatología mensual (12, lat, lon): promedio diario de la función analítica por mes."""
    if variable not in VARIABLES_ESCALARES:
        raise ValueError(f"Variable desconocida para el generador: «{variable}»")
    return _climatologia_mensual_cacheada(variable, float(profundidad or 0.0)).copy()


# ---------------------------------------------------------------------------
# Utilidades de malla
# ---------------------------------------------------------------------------
def indices_bbox(bbox: Iterable[float]) -> tuple[np.ndarray, np.ndarray]:
    """Índices (ilat, ilon) de las celdas cuyo centro cae dentro del bbox (inclusivo)."""
    lon_min, lat_min, lon_max, lat_max = [float(v) for v in bbox]
    ilat = np.where((LAT >= lat_min - _TOL) & (LAT <= lat_max + _TOL))[0]
    ilon = np.where((LON >= lon_min - _TOL) & (LON <= lon_max + _TOL))[0]
    return ilat, ilon


def celda_mas_cercana(lat: float, lon: float) -> tuple[float, float]:
    """Centro de la celda de la malla más cercana al punto dado."""
    ilat = int(np.argmin(np.abs(LAT - float(lat))))
    ilon = int(np.argmin(np.abs(LON - float(lon))))
    return float(LAT[ilat]), float(LON[ilon])
