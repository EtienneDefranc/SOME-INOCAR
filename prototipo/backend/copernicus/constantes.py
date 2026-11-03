"""Constantes del acceso real a Copernicus Marine (GLOBAL_ANALYSISFORECAST_PHY_001_024 y GLORYS12).

Ids de productos y datasets, DOI, geometría de la malla nativa de 1/12° y de los
fragmentos ARCO (valores comprobados con `copernicusmarine.describe` el
30/10/2026; se usan para planificar las descargas y estimar su costo), rutas de
la caché local `datos/copernicus/`, umbrales del estado RF-21, tipos de dato
guardados, interruptores de entorno (se leen en cada llamada, para poder
activarlos en pruebas sin reiniciar el proceso) y excepciones.

Ningún módulo del paquete importa `copernicusmarine` al importarse: solo
`acceso.importar()` lo hace, de forma perezosa (tarda ~4 s).
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from .. import configuracion as cfg

# ---------------------------------------------------------------------------
# Productos y datasets
# ---------------------------------------------------------------------------
PRODUCTO_ANFC = "GLOBAL_ANALYSISFORECAST_PHY_001_024"
DOI_ANFC = "10.48670/moi-00016"
PRODUCTO_GLORYS = "GLOBAL_MULTIYEAR_PHY_001_030"
DOI_GLORYS = "10.48670/moi-00021"

DATASET_THETAO = "cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m"
DATASET_SO = "cmems_mod_glo_phy-so_anfc_0.083deg_P1D-m"
DATASET_CUR = "cmems_mod_glo_phy-cur_anfc_0.083deg_P1D-m"
DATASET_2D = "cmems_mod_glo_phy_anfc_0.083deg_P1D-m"
DATASET_CLIMATOLOGIA = "cmems_mod_glo_phy_my_0.083deg-climatology_P1M-m"
DATASET_METADATOS = DATASET_2D          # el que se describe en cada verificación (público, sin cuenta)

# Variables guardadas en la caché («corrientes» no se guarda: son uo y vo)
VARIABLES: tuple[str, ...] = ("thetao", "so", "uo", "vo", "zos", "mlotst")
VARIABLES_3D: tuple[str, ...] = ("thetao", "so", "uo", "vo")
VARIABLES_2D: tuple[str, ...] = ("zos", "mlotst")
DATASET_POR_VARIABLE = {"thetao": DATASET_THETAO, "so": DATASET_SO, "uo": DATASET_CUR, "vo": DATASET_CUR,
                        "zos": DATASET_2D, "mlotst": DATASET_2D}

# Unidades aceptadas tal como las publica el producto (todas equivalen a las CF del conector: no se
# convierte nada). Una unidad distinta es un cambio de formato: ErrorFormato y respaldo señalado.
UNIDADES_ACEPTADAS = {
    "thetao": {"degrees_c", "degc", "celsius", "°c", "degree_celsius"},
    "so": {"1e-3", "0.001", "psu", "1"},
    "uo": {"m s-1", "m/s", "m.s-1", "m s**-1"},
    "vo": {"m s-1", "m/s", "m.s-1", "m s**-1"},
    "zos": {"m"},
    "mlotst": {"m"},
}

# Niveles SOME (m) y nombre de su carpeta en la caché
NIVELES_SOME: tuple[int, ...] = tuple(int(z) for z in cfg.PROFUNDIDADES)
NIVEL_SUPERFICIE_2D = "sup"


def nombre_nivel(nivel: int | None) -> str:
    """«z000», «z005», …, «z100» para los niveles SOME; «sup» para las variables 2D."""
    return NIVEL_SUPERFICIE_2D if nivel is None else f"z{int(nivel):03d}"


# ---------------------------------------------------------------------------
# Malla nativa (1/12°) y ventana del área de estudio
# ---------------------------------------------------------------------------
LAT0_NATIVA, LON0_NATIVA = -80.0, -180.0     # primer centro de la malla nativa (ascendente)
PASO_NATIVO = 1.0 / 12.0
FILA0, COLUMNA0 = 887, 1019                  # fila/columna global del primer centro de la ventana SOME
FILAS_NATIVAS, COLUMNAS_NATIVAS = 147, 207   # 49 × 3 y 69 × 3
MIN_VALIDAS_3X3 = 5                          # de 9 celdas nativas para formar una celda SOME

# ---------------------------------------------------------------------------
# Tiempo del producto y geometría de los fragmentos ARCO (respaldo si no hay metadatos)
# ---------------------------------------------------------------------------
INICIO_ANFC = date(2022, 6, 1)               # primer día del eje de tiempo y origen de los bloques de 134 días
DIAS_BLOQUE_SERIE = 134
NIVELES_MODELO_TOTAL = 50                    # niveles del modelo (los fragmentos de profundidad se cuentan desde el fondo)
SERVICIO_GEO = "arco-geo-series"
SERVICIO_SERIE = "arco-time-series"
GEOMETRIA = {
    # (tiempo, profundidad, latitud, longitud) en celdas por fragmento
    "geo": {"tiempo": 1, "prof": 1, "lat": 512, "lon": 2048},
    "serie3d": {"tiempo": DIAS_BLOQUE_SERIE, "prof": 2, "lat": 32, "lon": 64},
    "serie2d": {"tiempo": DIAS_BLOQUE_SERIE, "prof": 1, "lat": 32, "lon": 32},
    "clim": {"tiempo": 12, "prof": 4, "lat": 128, "lon": 128},
}
# Tamaño sin comprimir de un fragmento (MiB), para las estimaciones de `preparar_copernicus --estimar`
MIB_FRAGMENTO = {"geo": 4.0, "serie3d": 2.1, "serie2d": 0.55, "clim": 3.0}

# ---------------------------------------------------------------------------
# Tipos de dato guardados por celda
# ---------------------------------------------------------------------------
NO_OBTENIDO, ANALISIS_NRT, MEJOR_ANALISIS = 0, 1, 2
NOMBRE_TIPO = {NO_OBTENIDO: "no obtenido", ANALISIS_NRT: "análisis NRT", MEJOR_ANALISIS: "mejor análisis"}

# ---------------------------------------------------------------------------
# Umbrales y plazos
# ---------------------------------------------------------------------------
LATENCIA_ALTA_MS = 20_000
VERIFICACION_VIGENTE_H = 13
METADATOS_VIGENTES_H = 13
CAIDO_S = 300                        # tras un fallo de red, las consultas no reintentan durante 5 min
CAIDO_MAX_S = 30 * 60                # … plazo que se duplica con cada falla seguida, hasta 30 min
ESPERA_ALCANCE_S = 3.0               # prueba rápida de conexión antes de una descarga bajo demanda
ESPERA_BAJO_DEMANDA_S = 40           # espera máxima de una descarga dentro de una consulta (por defecto)
MAX_FRAGMENTOS_BAJO_DEMANDA = 150    # tope de fragmentos que una consulta descarga en el momento
INTERVALO_SINCRONIZACION_S = 6 * 3600
INTERVALO_REINTENTO_S = 30 * 60
ESPERA_INICIAL_S = 20
TIEMPO_MAXIMO_LECTURA_S = 600        # tope de una lectura (open_dataset + load) en segundo plano
PERIODO_CLIMATOLOGIA_RESPALDO = "1993-2016"   # verificado con climatology_bounds el 30/10/2026
TOTAL_PARES_CLIMATOLOGIA = len(VARIABLES_3D) * len(NIVELES_SOME) + len(VARIABLES_2D)   # 34

# Ventana de prueba de la verificación: celda SOME de (−2, −85)
PUNTO_PRUEBA = (-2.0, -85.0)

SERVIDOR_ARCO = "Copernicus Marine Data Store (ARCO, s3.waw3-1.cloudferro.com)"

# ---------------------------------------------------------------------------
# Rutas de la caché local (se leen en cada llamada: las pruebas las redirigen con `fijar_raiz`)
# ---------------------------------------------------------------------------
# SOME_RUTA_COPERNICUS (opcional, se lee al importar) lleva toda la caché (diario, climatología, estado.json y
# .bloqueos) a otra carpeta: un servidor o una prueba que simule fallas de red o falta de credenciales debe
# apuntarla a una copia, porque estado.json se comparte entre procesos y una caída registrada ahí dejaría al
# servidor real «sin conexión» de 5 a 30 min. SOME_RUTA_DATOS no la redirige, a propósito: la prueba de humo
# usa la caché real.
_RUTA_ENTORNO = os.environ.get("SOME_RUTA_COPERNICUS", "").strip()
_RAIZ = [Path(_RUTA_ENTORNO).resolve() if _RUTA_ENTORNO else cfg.RUTA_DATOS / "copernicus"]


def raiz() -> Path:
    """Carpeta raíz de la caché: datos/copernicus/, SOME_RUTA_COPERNICUS o la temporal de las pruebas."""
    return Path(_RAIZ[0])


def fijar_raiz(ruta: Path | str) -> Path:
    """Redirige la caché (pruebas). Devuelve la raíz anterior."""
    previa = _RAIZ[0]
    _RAIZ[0] = Path(ruta)
    return previa


def ruta_diario() -> Path:
    return raiz() / "diario"


def ruta_climatologia() -> Path:
    return raiz() / "climatologia"


def ruta_estado() -> Path:
    return raiz() / "estado.json"


def ruta_generacion() -> Path:
    return raiz() / "generacion"


def ruta_bloqueos() -> Path:
    return raiz() / ".bloqueos"


# ---------------------------------------------------------------------------
# Interruptores de entorno (leídos en cada llamada)
# ---------------------------------------------------------------------------
def _activa(variable: str) -> bool:
    return os.environ.get(variable, "").strip().lower() in ("1", "true", "si", "sí", "yes")


def modo_configurado() -> str:
    """«auto» (por defecto), «simulado» o «real» según SOME_COPERNICUS_MODO (valor desconocido → «auto»)."""
    valor = os.environ.get("SOME_COPERNICUS_MODO", "").strip().lower()
    return valor if valor in ("simulado", "real") else "auto"


def sin_red() -> bool:
    """True si SOME_COPERNICUS_SIN_RED está activo: no se toca la red, solo la caché local."""
    return _activa("SOME_COPERNICUS_SIN_RED")


def sin_sincronizacion() -> bool:
    """True si SOME_COPERNICUS_SIN_SINCRONIZACION está activo: no arranca el hilo de sincronización."""
    return _activa("SOME_COPERNICUS_SIN_SINCRONIZACION")


def espera_bajo_demanda_s() -> float:
    """Espera máxima de una descarga dentro de una consulta (SOME_COPERNICUS_ESPERA_S, por defecto 40 s)."""
    try:
        valor = float(os.environ.get("SOME_COPERNICUS_ESPERA_S", "") or ESPERA_BAJO_DEMANDA_S)
    except ValueError:
        valor = ESPERA_BAJO_DEMANDA_S
    return max(1.0, valor)


def dias_sincronizacion() -> int:
    """Días de superficie del conjunto liviano (SOME_COPERNICUS_DIAS_SINCRONIZACION, por defecto 1; 0 = solo verificar)."""
    try:
        valor = int(os.environ.get("SOME_COPERNICUS_DIAS_SINCRONIZACION", "") or 1)
    except ValueError:
        valor = 1
    return max(0, min(valor, 120))


# ---------------------------------------------------------------------------
# Excepciones
# ---------------------------------------------------------------------------
class ErrorCopernicus(RuntimeError):
    """Base de los errores de Copernicus Marine (desde un conector real, el motor la convierte en 503)."""


class ErrorSinAcceso(ErrorCopernicus):
    """Sin paquete, sin red, sin credenciales, credenciales inválidas o servicio caído."""


class ErrorSinCredenciales(ErrorSinAcceso):
    """No hay credenciales de Copernicus Marine (también la lanza la guardia anti-prompt)."""


class ErrorDescargaEnCurso(ErrorCopernicus):
    """La descarga bajo demanda superó la espera de la consulta y sigue en segundo plano."""


class ErrorFormato(ErrorCopernicus):
    """Malla, niveles, tiempo o unidades distintos de lo esperado."""


class ErrorSinDatos(ErrorCopernicus):
    """El día pedido no está en la caché local y no se pudo obtener."""
