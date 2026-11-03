"""Niveles SOME (0, 5, 10, 20, 30, 50, 75 y 100 m) a partir de los niveles del modelo.

Los niveles del modelo son comunes a ANFC (GLOBAL_ANALYSISFORECAST_PHY_001_024)
y a GLORYS12 (GLOBAL_MULTIYEAR_PHY_001_030); estos son los 23 más someros, con
los valores exactos de `copernicusmarine.describe` (30/10/2026).

Interpolación lineal entre los dos niveles que rodean cada nivel SOME:
`v(z) = v_a + (v_b − v_a)·(z − a)/(b − a)`, con dato solo si ambos niveles
tienen dato (si no, NaN: fondo o tierra). 0 m = nivel de 0.494 m (el primero
del modelo). En total se leen 15 niveles nativos para los 8 niveles SOME.
"""

from __future__ import annotations

import numpy as np

from .constantes import NIVELES_MODELO_TOTAL, NIVELES_SOME, ErrorFormato

NIVELES_MODELO: tuple[float, ...] = (
    0.49402499198913574, 1.5413750410079956, 2.6456689834594727, 3.8194949626922607, 5.078224182128906,
    6.440614223480225, 7.92956018447876, 9.572997093200684, 11.404999732971191, 13.467140197753906,
    15.810070037841797, 18.495559692382812, 21.598819732666016, 25.211410522460938, 29.444730758666992,
    34.43415069580078, 40.344051361083984, 47.37369155883789, 55.76428985595703, 65.80726623535156,
    77.85385131835938, 92.3260726928711, 109.72930145263672,
)
TOLERANCIA_NIVEL = 0.01


def vecinos(z_some: float) -> tuple[float, float, float]:
    """(nivel_a, nivel_b, peso_b) del nivel SOME `z_some`; z = 0 → (0.494, 0.494, 0.0)."""
    z = float(z_some)
    primero = NIVELES_MODELO[0]
    if z < 0 or z > NIVELES_MODELO[-1]:
        raise ValueError(f"La profundidad {z} m está fuera de los niveles del modelo usados (0 – "
                         f"{NIVELES_MODELO[-1]:.1f} m).")
    if z <= primero:
        return primero, primero, 0.0
    for a, b in zip(NIVELES_MODELO[:-1], NIVELES_MODELO[1:]):
        if abs(z - b) <= 1e-9:
            return b, b, 0.0
        if a <= z < b:
            return a, b, (z - a) / (b - a)
    return NIVELES_MODELO[-1], NIVELES_MODELO[-1], 0.0


def niveles_necesarios(niveles_some) -> list[float]:
    """Niveles nativos (ordenados, sin repetir) que hacen falta para los niveles SOME dados."""
    necesarios: set[float] = set()
    for z in niveles_some:
        a, b, _ = vecinos(float(z))
        necesarios.add(a)
        necesarios.add(b)
    return sorted(necesarios)


NIVELES_NATIVOS: tuple[float, ...] = tuple(niveles_necesarios(NIVELES_SOME))   # los 15 de la tabla del diseño


def indice_modelo(nivel: float) -> int:
    """Índice (desde la superficie) de un nivel nativo; ValueError si no es uno de los conocidos."""
    for i, n in enumerate(NIVELES_MODELO):
        if abs(n - nivel) <= TOLERANCIA_NIVEL:
            return i
    raise ValueError(f"Nivel nativo desconocido: {nivel} m")


def fragmentos_profundidad(niveles_nativos, por_fragmento: int) -> int:
    """Fragmentos de profundidad que tocan esos niveles (los fragmentos se cuentan desde el fondo)."""
    if not niveles_nativos:
        return 1
    grupos = {(NIVELES_MODELO_TOTAL - 1 - indice_modelo(n)) // max(1, por_fragmento) for n in niveles_nativos}
    return len(grupos)


def interpolar(datos: np.ndarray, niveles: np.ndarray, z_some, eje: int = 1) -> np.ndarray:
    """Interpola `datos` (con los niveles nativos `niveles` en el eje `eje`) a los niveles SOME `z_some`.

    Devuelve el mismo arreglo con el eje de profundidad de largo len(z_some). NaN si falta
    alguno de los dos niveles vecinos. `ErrorFormato` si falta un nivel nativo necesario.
    """
    niveles = np.asarray(niveles, dtype=np.float64)
    datos = np.moveaxis(np.asarray(datos, dtype=np.float32), eje, 0)
    if datos.shape[0] != niveles.size:
        raise ErrorFormato(f"{datos.shape[0]} capas de datos para {niveles.size} niveles.")

    def capa(nivel: float) -> np.ndarray:
        dif = np.abs(niveles - nivel)
        i = int(np.argmin(dif))
        if dif[i] > TOLERANCIA_NIVEL:
            raise ErrorFormato(f"Falta el nivel del modelo de {nivel:.3f} m en la lectura.")
        return datos[i]

    salida = []
    for z in z_some:
        a, b, peso = vecinos(float(z))
        va = capa(a)
        if peso == 0.0:
            salida.append(va.astype(np.float32))
            continue
        vb = capa(b)
        salida.append((va * np.float32(1.0 - peso) + vb * np.float32(peso)).astype(np.float32))
    return np.moveaxis(np.stack(salida, axis=0), 0, eje)
