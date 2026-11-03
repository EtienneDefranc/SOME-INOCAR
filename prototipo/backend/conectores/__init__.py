"""Conectores de fuentes del SOME.

Los tres conectores son reales: CHIRPS v3 descarga del servidor del Climate
Hazards Center (y de su réplica de dynamical.org); Copernicus Marine lee el
análisis físico global (GLOBAL_ANALYSISFORECAST_PHY_001_024) y la climatología
GLORYS12 con el Copernicus Marine Toolbox; NOAA OISST v2.1 (`backend/noaa/`) lee
la SST diaria del servicio NCSS de NOAA PSL (respaldo: archivos diarios de NOAA
NCEI). Copernicus Marine y NOAA OISST guardan una caché local y, cuando no hay
acceso ni el dato en caché (o con su modo simulado forzado), responden con el
simulador anterior señalado como tal, sin mezclarlo nunca con datos reales en un
mismo Dataset. Todos cumplen el mismo contrato (`ConectorFuente`) y declaran su
procedencia con `datos_reales` e `insignia` (dinámicos en Copernicus Marine y
NOAA OISST: dependen de si hay caché o acceso).
"""

from .base import (ConectorFuente, ConectorSimulado, EstadoFuente, ParametrosConsulta, VariableFuente,
                   combinar_procedencias, procedencia_de)
from .chirps import ConectorCHIRPS
from .copernicus import ConectorCopernicus
from .noaa import ConectorNOAA

__all__ = [
    "ConectorFuente",
    "ConectorSimulado",
    "EstadoFuente",
    "ParametrosConsulta",
    "VariableFuente",
    "ConectorNOAA",
    "ConectorCopernicus",
    "ConectorCHIRPS",
    "crear_conectores",
    "procedencia_de",
    "combinar_procedencias",
]


def crear_conectores() -> dict[str, ConectorFuente]:
    """Instancia los tres conectores, indexados por su `id`."""
    conectores = [ConectorNOAA(), ConectorCopernicus(), ConectorCHIRPS()]
    return {c.id: c for c in conectores}
