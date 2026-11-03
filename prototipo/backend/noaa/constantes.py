"""Constantes del acceso real a NOAA OISST v2.1 (temperatura superficial del mar diaria a 0.25°).

Servidores y archivos (comprobados el 30/10/2026 desde el equipo de la demo), reglas
de cortesía (ritmo por servidor, User-Agent genérico, reintentos), geometría de la
ventana nativa que se descarga (el área SOME más la región Niño 1+2 oficial, que se
sale del dominio por el sur), rutas de la caché local `datos/noaa/`, tipos y orígenes
de cada día guardado, interruptores de entorno (se leen en cada llamada, para poder
activarlos en pruebas sin reiniciar el proceso) y excepciones.

Ningún módulo del paquete toca la red al importarse.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from .. import configuracion as cfg

# ---------------------------------------------------------------------------
# Producto y citas
# ---------------------------------------------------------------------------
PRODUCTO = "NOAA 0.25-degree Daily Optimum Interpolation Sea Surface Temperature (OISST), Version 2.1"
DOI = "10.25921/RE9P-PT57"
URL_DOI = f"https://doi.org/{DOI}"
# Cita oficial que pide NCEI para el conjunto de datos (página del DOI)
CITA_OFICIAL = ("Huang, B., C. Liu, V. F. Banzon, E. Freeman, G. Graham, W. Hankins, T. M. Smith y H.-M. Zhang "
                "(2020). NOAA 0.25-degree Daily Optimum Interpolation Sea Surface Temperature (OISST), Version 2.1. "
                f"NOAA National Centers for Environmental Information. {URL_DOI}")
CITA_ARTICULO = ("Huang, B. et al. (2021). Improvements of the Daily Optimum Interpolation Sea Surface Temperature "
                 "(DOISST) Version 2.1. J. Climate, 34(8), 2923–2939. https://doi.org/10.1175/JCLI-D-20-0166.1")
# Crédito corto (≤ 150 caracteres: cabe entero en el pie de las figuras). Cita el conjunto con su año (2020; el
# artículo de 2021 es otra referencia), reconoce al CDR Program como pide su Use Agreement y nombra el servidor del
# que salieron los días. NOAA pide el crédito, pero no lo exige: los datos son abiertos, sin restricciones de uso.
def credito_para_origen(dias_psl: int = 1, dias_ncei: int = 0) -> str:
    via = ("NOAA PSL y NOAA NCEI" if dias_psl and dias_ncei else "NOAA NCEI" if dias_ncei else "NOAA PSL")
    return f"NOAA OISST v2.1 (Huang et al., 2020; doi:{DOI}), NOAA NCEI y CDR Program, vía {via}."


CREDITO = credito_para_origen()
LICENCIA = ("Datos abiertos de NOAA sin restricciones de uso (NOAA CDR Program; dominio público en EE. UU.). "
            "No se puede insinuar aval de NOAA, presentar datos modificados como originales de NOAA ni usar su "
            "emblema.")

# ---------------------------------------------------------------------------
# Servidores
# ---------------------------------------------------------------------------
PSL, NCEI, CPC = "psl", "ncei", "cpc"
NOMBRE_SERVIDOR = {PSL: "NOAA PSL (THREDDS, subconjuntos NCSS)", NCEI: "NOAA NCEI (archivos diarios)",
                   CPC: "NOAA CPC (índices semanales)"}
NOMBRE_CORTO_SERVIDOR = {PSL: "NOAA PSL", NCEI: "NOAA NCEI", CPC: "NOAA CPC"}

# PSL (vía principal): servicio NCSS de THREDDS, que recorta por área y fechas y entrega NetCDF4
# (xarray lo abre con h5netcdf; OPeNDAP directo no sirve aquí: no hay netCDF4 ni pydap).
URL_PSL_NCSS = "https://psl.noaa.gov/thredds/ncss/grid/Datasets/noaa.oisst.v2.highres"
ARCHIVO_DIARIO = "sst.day.mean.{anio:04d}.nc"            # uno por año, hasta el último día publicado
ARCHIVO_MENSUAL = "sst.mon.mean.nc"                      # medias mensuales desde 09/1981
ARCHIVO_LTM_MENSUAL = "sst.mon.ltm.1991-2020.nc"         # climatología mensual oficial 1991-2020
ARCHIVO_LTM_DIARIA = "sst.day.mean.ltm.1991-2020.nc"     # climatología diaria 1991-2020 (365 días, año 0001)

# NCEI (respaldo): un archivo NetCDF4 global por día (~1.5 MB, variables sst, anom, err, ice). El
# listado del directorio de cada mes dice qué días son finales y cuáles preliminares.
URL_NCEI = "https://www.ncei.noaa.gov/data/sea-surface-temperature-optimum-interpolation/v2.1/access/avhrr"

# CPC: índices semanales oficiales (base 1991-2020), solo para contrastar el índice Niño 1+2 del SOME
URL_CPC_SEMANAL = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"


def url_ncss(archivo: str) -> str:
    return f"{URL_PSL_NCSS}/{archivo}"


def url_ncei_mes(anio: int, mes: int) -> str:
    return f"{URL_NCEI}/{anio:04d}{mes:02d}/"


def url_ncei_dia(d: date, preliminar: bool) -> str:
    sufijo = "_preliminary" if preliminar else ""
    return f"{URL_NCEI}/{d:%Y%m}/oisst-avhrr-v02r01.{d:%Y%m%d}{sufijo}.nc"


# User-Agent genérico (sin correo electrónico). Las cabeceras HTTP deben ser ASCII: «demostracion»
# va sin tilde.
USER_AGENT = "SOME-prototipo/0.1 (demostracion para el INOCAR)"

# ---------------------------------------------------------------------------
# Cortesía y tiempos
# ---------------------------------------------------------------------------
# Como máximo 1 petición por segundo por servidor (entre todos los procesos del SOME del equipo).
# PSL además limita con HTTP 429 a quien pide seguido (comprobado: con peticiones cada 20 s, ~1 de
# cada 8 recibe 429): tras cada petición a PSL se deja una pausa de PAUSA_PSL_S antes de la siguiente.
INTERVALO_MINIMO_S = {PSL: 1.0, NCEI: 1.0, CPC: 1.0}
PAUSA_TRAS_PETICION_S = {PSL: 20.0, NCEI: 0.0, CPC: 0.0}
TIMEOUT_CONEXION_S = 15
TIMEOUT_S = {PSL: 90, NCEI: 60, CPC: 30}   # el proxy de PSL corta a los ~60 s con HTTP 502
# Consultas del usuario (`respetar_pausa=True`): nada se cuelga. Un solo intento, 8 s para conectar y 45 s
# para recibir (un tramo de 92 días tarda ~28 s); si el servidor no responde, queda en pausa al instante y
# las consultas siguientes ya no lo esperan (sirven la caché y avisan). Medido el 30/10/2026 con un servidor
# inalcanzable: con los 3 intentos de la sincronización, una consulta con un día ausente tardaba 151 s (61 s
# si el nombre no resolvía); así, ~16 s como máximo (PSL y el respaldo NCEI).
TIMEOUT_CONSULTA_S = (8, 45)
INTENTOS_CONSULTA = 1
INTENTOS = 3                               # por petición (sincronización y preparación), con espera creciente
PAUSA_REINTENTO_S = (5.0, 15.0)            # entre intentos tras un fallo de red o un 5xx
ESPERA_429_S = (60.0, 120.0, 240.0, 300.0)  # PSL limitó el ritmo: la sincronización espera y reintenta
FALLOS_PAUSA = 3                           # tras 3 fallos seguidos el servidor se deja en pausa…
PAUSA_SERVIDOR_S = 10 * 60                 # … 10 min (las consultas no lo reintentan; la sincronización sí)

# Plan de descarga: el NCSS de PSL tarda ~0.3 s por día pedido (cualquier área): 92 días ≈ 28 s, por
# debajo del corte de 60 s de su proxy. Un año entero (~110 s) falla con HTTP 502.
MAX_DIAS_TRAMO = 92
SEGUNDOS_POR_DIA_PSL = 0.31
DIAS_TRIMESTRE_LTM = ((1, 90), (91, 181), (182, 273), (274, 365))   # días del año 0001 por petición
MAX_DIAS_NCEI = 45                 # días que el respaldo NCEI descarga como máximo por ciclo (~1.5 MB c/u)
MAX_PETICIONES_BAJO_DEMANDA = 1    # peticiones a PSL que una consulta hace en el momento (≤ 92 días, ~30 s)
ESPERA_DESCARGA_BAJO_DEMANDA_S = 5  # una consulta no espera más que esto a que termine otra descarga
VENTANA_RENOVACION_DIAS = 21       # días recientes que la sincronización vuelve a pedir (preliminar → final)
MARGEN_FINAL_H = 12                # un día de PSL es final si NCEI publicó su final al menos 12 h antes
DIAS_PRELIMINARES_ESTIMADOS = 15   # sin el listado de NCEI: los últimos 15 días se tratan como preliminares

# ---------------------------------------------------------------------------
# Ventana nativa (0.25°, centros en x.125/x.375/…) que se descarga
# ---------------------------------------------------------------------------
# Une el área SOME (centros nativos 6.125° S … 6.125° N, 95.125° O … 77.875° O: 50 × 70, cada centro
# SOME es la esquina común de 4 celdas nativas) con la región Niño 1+2 oficial (0–10° S, 90–80° O),
# que se sale del dominio por el sur: filas de −9.875° a 6.125° (65) y columnas de −95.125° a
# −77.875° (70). Una sola petición sirve a la vez el mapa y el índice.
LAT_NATIVA_SUR, LAT_NATIVA_NORTE = -9.875, 6.125
LON_NATIVA_OESTE, LON_NATIVA_ESTE = -95.125, -77.875
FILAS_NATIVAS, COLUMNAS_NATIVAS = 65, 70
# Recorte pedido al NCSS en longitudes 0–360 (PSL) con bordes dentro de las celdas extremas: el NCSS
# devuelve las celdas que tocan el recuadro; luego se seleccionan los centros esperados uno a uno.
NCSS_NORTE, NCSS_SUR, NCSS_OESTE, NCSS_ESTE = 6.2, -9.95, 264.8, 282.2
# Filas nativas del área SOME (6.125° S … 6.125° N)
FILA_SOME_0 = 15                                   # índice de la fila −6.125°
# Región Niño 1+2 oficial: filas −9.875 … −0.125 (40) y columnas −89.875 … −80.125 (40)
FILAS_NINO12 = (0, 40)
COLUMNAS_NINO12 = (21, 61)
REGION_NINO12 = {"lat_sur": -10.0, "lat_norte": 0.0, "lon_oeste": -90.0, "lon_este": -80.0}
# Mínimo de celdas nativas válidas para un centro SOME (ver malla.py y DISENO-NOAA.md §3.3)
MIN_VALIDAS_2X2 = 2

# ---------------------------------------------------------------------------
# Tipos y orígenes de cada día guardado
# ---------------------------------------------------------------------------
AUSENTE, PRELIMINAR, FINAL = 0, 1, 2
NOMBRE_TIPO = {AUSENTE: "ausente", PRELIMINAR: "preliminar", FINAL: "final"}
ORIGEN_NINGUNO, ORIGEN_PSL, ORIGEN_NCEI = 0, 1, 2
NOMBRE_ORIGEN = {ORIGEN_NINGUNO: "", ORIGEN_PSL: PSL, ORIGEN_NCEI: NCEI}

# Periodos de referencia de la climatología mensual y primer mes de OISST
PERIODOS = {"1991-2020": (1991, 2020), "1981-2010": (1981, 2010), "1993-2022": (1993, 2022)}
PRIMER_MES_OISST = (1981, 9)       # septiembre de 1981

# ---------------------------------------------------------------------------
# Estado (RF-21) y sincronización
# ---------------------------------------------------------------------------
LATENCIA_ALTA_MS = 8000            # verificación (metadatos de PSL o listado de NCEI) más lenta: «degradada»
VERIFICACION_VIGENTE_H = 13        # una verificación más antigua ya no respalda «operativa»
RETRASO_MAXIMO_DIAS = 3            # PSL publica el día D el D+1: si su último día es anterior a hoy − 3, «degradada»
INTERVALO_SINCRONIZACION_S = 6 * 3600
INTERVALO_REINTENTO_S = 30 * 60
ESPERA_INICIAL_S = 30

# Fecha de corte y latencia del simulador anterior (modo simulado y respaldo)
FECHA_CORTE_SIMULADA = date(2026, 10, 27)
LATENCIA_SIMULADA_MS = 820

# ---------------------------------------------------------------------------
# Rutas de la caché local (se leen en cada llamada: las pruebas las redirigen con `fijar_raiz`)
# ---------------------------------------------------------------------------
# SOME_RUTA_NOAA (opcional, se lee al importar) lleva toda la caché (diario, mensual, climatología,
# estado.json y .bloqueos) a otra carpeta, para servidores o pruebas aislados. SOME_RUTA_DATOS no la
# redirige, a propósito (como CHIRPS y Copernicus). Con otra carpeta, el turno de cortesía con PSL ya
# no se comparte con el servidor que usa datos/noaa/: úsela sin otro proceso descargando a la vez.
_RUTA_ENTORNO = os.environ.get("SOME_RUTA_NOAA", "").strip()
_RAIZ = [Path(_RUTA_ENTORNO).resolve() if _RUTA_ENTORNO else cfg.RUTA_DATOS / "noaa"]


def raiz() -> Path:
    """Carpeta raíz de la caché: datos/noaa/, SOME_RUTA_NOAA o la temporal de las pruebas."""
    return Path(_RAIZ[0])


def fijar_raiz(ruta: Path | str) -> Path:
    """Redirige la caché (pruebas). Devuelve la raíz anterior."""
    previa = _RAIZ[0]
    _RAIZ[0] = Path(ruta)
    return previa


def ruta_diario() -> Path:
    return raiz() / "diario"


def ruta_mensual() -> Path:
    return raiz() / "mensual"


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
    """«auto» (por defecto), «simulado» o «real» según SOME_NOAA_MODO (valor desconocido → «auto»)."""
    valor = os.environ.get("SOME_NOAA_MODO", "").strip().lower()
    return valor if valor in ("simulado", "real") else "auto"


def sin_red() -> bool:
    """True si SOME_NOAA_SIN_RED está activo: no se toca la red, solo la caché local."""
    return _activa("SOME_NOAA_SIN_RED")


def sin_sincronizacion() -> bool:
    """True si SOME_NOAA_SIN_SINCRONIZACION está activo: no arranca el hilo de sincronización (las
    consultas siguen llenando bajo demanda lo que falte)."""
    return _activa("SOME_NOAA_SIN_SINCRONIZACION")


# ---------------------------------------------------------------------------
# Excepciones
# ---------------------------------------------------------------------------
class ErrorNOAA(RuntimeError):
    """Base de los errores de NOAA OISST (desde un conector real, el motor la convierte en 503)."""


class ErrorConexion(ErrorNOAA):
    """El servidor no responde, rechaza la conexión, limita el ritmo (HTTP 429) o falla (5xx)."""

    def __init__(self, mensaje: str, servidor: str = "", limitado: bool = False):
        super().__init__(mensaje)
        self.servidor = servidor
        self.limitado = limitado


class ErrorNoExiste(ErrorNOAA):
    """El servidor respondió 404: el recurso no existe (p. ej., un día aún no publicado)."""


class ErrorFormato(ErrorNOAA):
    """Malla, tiempo, variable o unidades distintos de lo esperado."""


class ErrorDescargaEnCurso(ErrorNOAA):
    """Otra descarga de NOAA está en curso (sincronización o preparación); se reintentará luego."""


class ErrorSinDatos(ErrorNOAA):
    """El dato pedido no está en la caché local y no se pudo obtener."""
