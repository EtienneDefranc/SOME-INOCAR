"""Calendario del análisis de GLOBAL_ANALYSISFORECAST_PHY_001_024 (puro, sin red).

Según el manual del producto (PUM 001-024, «Processing information» y
«Catalogue updating»): cada día se publica un pronóstico de 10 días; cada
JUEVES se publica el análisis semanal con asimilación del boletín del
MIÉRCOLES `W`:

* análisis NRT (−192 a −24 h): días `W−8 … W−2` (provisional: Mercator lo
  reemplaza la semana siguiente);
* mejor análisis (−360 a −192 h): días `W−15 … W−9` (ya no cambia).

Cada día es NRT en un solo boletín y después pasa a mejor análisis. El SOME
sirve solo análisis: el último día servible es `W − 2`; `W − 1` (simulación sin
asimilación) y los días siguientes (pronóstico) nunca se sirven.

Boletín vigente:
* con metadatos (fin del eje de tiempo de ARCO, `fin_tiempo_arco`): el
  miércoles más reciente con `W + 10 ≤ fin_tiempo_arco` (el pronóstico del
  jueves `W+1` llega hasta `W+10` y el análisis se publica junto con él);
* sin metadatos: el miércoles más reciente cuyo jueves `W+1` a las 18:00 UTC
  ya pasó.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from .constantes import ANALISIS_NRT, MEJOR_ANALISIS, NO_OBTENIDO

HORA_PUBLICACION_UTC = time(18, 0)
DIAS_PRONOSTICO = 10


def _miercoles_hasta(d: date) -> date:
    """El miércoles más reciente que no pasa de `d` (miércoles = weekday 2)."""
    return d - timedelta(days=(d.weekday() - 2) % 7)


def _como_utc(ahora_utc: datetime) -> datetime:
    if ahora_utc.tzinfo is None:
        return ahora_utc
    return ahora_utc.astimezone(timezone.utc).replace(tzinfo=None)


def boletin_semanal(ahora_utc: datetime, fin_tiempo_arco: date | None = None) -> date:
    """Miércoles `W` del último boletín semanal publicado."""
    ahora = _como_utc(ahora_utc)
    # Por calendario: el jueves W+1 a las 18:00 UTC ya pasó.
    w_cal = _miercoles_hasta(ahora.date())
    if datetime.combine(w_cal + timedelta(days=1), HORA_PUBLICACION_UTC) > ahora:
        w_cal -= timedelta(days=7)
    if fin_tiempo_arco is None:
        return w_cal
    # Por metadatos: W + 10 ≤ fin del eje de tiempo (nunca un miércoles futuro).
    tope = min(fin_tiempo_arco - timedelta(days=DIAS_PRONOSTICO), ahora.date())
    return _miercoles_hasta(tope)


def ultimo_dia_analisis(ahora_utc: datetime, fin_tiempo_arco: date | None = None) -> date:
    """Último día de análisis servible: `W − 2`."""
    return boletin_semanal(ahora_utc, fin_tiempo_arco) - timedelta(days=2)


def tipo_dia(dia: date, boletin: date) -> int:
    """2 (mejor análisis) si dia ≤ W−9; 1 (análisis NRT) si W−8 ≤ dia ≤ W−2; 0 (no se sirve) si dia ≥ W−1."""
    if dia <= boletin - timedelta(days=9):
        return MEJOR_ANALISIS
    if dia <= boletin - timedelta(days=2):
        return ANALISIS_NRT
    return NO_OBTENIDO


def ahora_utc() -> datetime:
    """Hora UTC actual sin zona (las funciones de este módulo la aceptan con o sin zona)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
