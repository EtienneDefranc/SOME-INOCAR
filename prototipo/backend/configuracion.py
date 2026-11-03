"""Constantes de configuración del prototipo SOME.

Rutas, puerto, área de estudio, malla, fechas, profundidades, periodos de
referencia y pequeñas utilidades de formato en español del Ecuador
(fechas dd/mm/aaaa y punto decimal, RNF-01), compartidas por todo el backend.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Identidad y rutas
# ---------------------------------------------------------------------------
NOMBRE_SISTEMA = "SOME"
NOMBRE_COMPLETO = "Sistema de Observación del Mar Equinoccial"
VERSION = "0.1.0-demo"
INSTITUCION = "INOCAR — Dirección de Oceanografía y Meteorología Marina"
INSIGNIA_SIMULADOS = "Datos simulados para demostración"
# Procedencia honesta de los datos: las tres fuentes son reales. CHIRPS v3 se descarga del servidor
# del CHC (o de su réplica en dynamical.org); Copernicus Marine se lee del Copernicus Marine Data Store
# con el toolbox oficial cuando hay credenciales o caché local; NOAA OISST v2.1 se lee de NOAA PSL
# (respaldo: NOAA NCEI) con una caché local. Si Copernicus Marine o NOAA OISST no tienen acceso ni
# caché (o se fuerza su modo simulado), su conector cae al simulador y lo dice.
INSIGNIA_REALES_CHIRPS = "Datos reales: CHIRPS v3 (CHC, UCSB)"
INSIGNIA_REALES_COPERNICUS = "Datos reales · Copernicus Marine"
INSIGNIA_REALES_NOAA = "Datos reales · NOAA OISST"
INSIGNIA_REALES = "Datos reales"                      # resultado real con varias fuentes reales
INSIGNIA_MIXTOS = "Datos mixtos: reales y simulados"
# Insignia de la cabecera de la interfaz según el modo de NOAA OISST y de Copernicus Marine (texto largo,
# corto y explicación emergente). La elige `catalogo.insignias_cabecera()`.
# Las tres fuentes reales (lo normal):
INSIGNIA_CABECERA_REALES = "Datos reales: NOAA OISST, Copernicus Marine y CHIRPS v3"
INSIGNIA_CABECERA_REALES_CORTA = "Datos reales"
TITULO_CABECERA_REALES = ("La temperatura superficial del mar (NOAA OISST v2.1, NOAA/NCEI), las variables físicas del "
                          "océano (análisis diario de Copernicus Marine, E.U. Copernicus Marine Service Information) y "
                          "la precipitación (CHIRPS v3, Climate Hazards Center, UC Santa Barbara) son datos reales; el "
                          "índice Niño 1+2 se calcula con NOAA OISST en la región oficial. Los últimos ~15 días de NOAA "
                          "OISST son preliminares. Si una fuente no responde y su caché local no tiene el dato, la "
                          "respuesta usa el simulador y lo señala.")
# NOAA OISST y Copernicus Marine en modo simulado (solo CHIRPS v3 es real):
INSIGNIA_CABECERA = "Demostración: datos simulados, salvo precipitación CHIRPS (real)"
INSIGNIA_CABECERA_CORTA = "Simulado, salvo CHIRPS"
TITULO_CABECERA = ("La precipitación proviene de CHIRPS v3 (datos reales del Climate Hazards Center, UC Santa "
                   "Barbara). NOAA OISST y Copernicus Marine son simulados de forma determinista; no son "
                   "observaciones reales.")
# Copernicus Marine real y NOAA OISST en modo simulado:
INSIGNIA_CABECERA_COPERNICUS = "Demostración: CHIRPS v3 y Copernicus Marine con datos reales; NOAA OISST simulado"
INSIGNIA_CABECERA_COPERNICUS_CORTA = "Real: CHIRPS y Copernicus"
TITULO_CABECERA_COPERNICUS = ("La precipitación (CHIRPS v3, Climate Hazards Center, UC Santa Barbara) y las variables "
                              "físicas del océano (análisis diario de Copernicus Marine, E.U. Copernicus Marine Service "
                              "Information) son datos reales. NOAA OISST está en modo simulado (sin acceso a NOAA ni "
                              "caché local, o modo simulado forzado): su SST y el índice Niño 1+2 son simulados de "
                              "forma determinista. Si Copernicus Marine no responde y la caché local no tiene el dato, "
                              "la respuesta usa el simulador y lo señala.")
# NOAA OISST real y Copernicus Marine en modo simulado:
INSIGNIA_CABECERA_NOAA = "Demostración: NOAA OISST y CHIRPS v3 con datos reales; Copernicus Marine simulado"
INSIGNIA_CABECERA_NOAA_CORTA = "Real: NOAA y CHIRPS"
TITULO_CABECERA_NOAA = ("La temperatura superficial del mar (NOAA OISST v2.1, NOAA/NCEI) y la precipitación (CHIRPS "
                        "v3, Climate Hazards Center, UC Santa Barbara) son datos reales; el índice Niño 1+2 se calcula "
                        "con NOAA OISST en la región oficial y los últimos ~15 días de NOAA OISST son preliminares. "
                        "Copernicus Marine está en modo simulado (sin credenciales ni caché local, o modo simulado "
                        "forzado): sus variables físicas del océano son simuladas de forma determinista. Si NOAA no "
                        "responde y la caché local no tiene el dato, la respuesta usa el simulador y lo señala.")
# Lo que piden las licencias de las tres fuentes en toda salida que sale del sistema (CSV, NetCDF, PDF):
# declarar las modificaciones y no sugerir aval del proveedor (Copernicus: Reglamento 1159/2013, art. 8;
# CHIRPS: CC BY 4.0; NOAA: licencia NODD). Ver CONDICIONES-USO-FUENTES.md en la raíz del proyecto.
AVISO_PROCESAMIENTO = ("Datos procesados por el INOCAR con el SOME (recorte, malla común de 0.25°, niveles de "
                       "profundidad, climatologías y anomalías): no son datos originales de los proveedores ni "
                       "cuentan con su aval, y los proveedores los entregan sin garantía.")

RAIZ = Path(__file__).resolve().parent.parent
RUTA_DATOS = RAIZ / "datos"
RUTA_REPORTES = RUTA_DATOS / "reportes"
RUTA_FRONTEND = RAIZ / "frontend"

HOST = "127.0.0.1"
PUERTO = 8050

# ---------------------------------------------------------------------------
# Área de estudio y malla (0.25°)
# ---------------------------------------------------------------------------
# bbox = (lon_min, lat_min, lon_max, lat_max)
BBOX_AREA: tuple[float, float, float, float] = (-95.0, -6.0, -78.0, 6.0)
RESOLUCION_GRADOS = 0.25

LAT = np.round(np.arange(-6, 6.001, RESOLUCION_GRADOS), 4)      # 49 valores
LON = np.round(np.arange(-95, -77.999, RESOLUCION_GRADOS), 4)   # 69 valores

# ---------------------------------------------------------------------------
# Tiempo
# ---------------------------------------------------------------------------
FECHA_INICIO = date(2023, 10, 1)
# Fecha de corte del simulador de NOAA OISST («últimos 3 años»; el de Copernicus llega al 26/10/2026). Las
# fuentes reales tienen su propia fecha de corte efectiva (ver sus conectores). La fecha de corte DEL SISTEMA
# (por defecto de consultas, panel, reportes y exportaciones) es dinámica: `catalogo.fecha_corte_sistema()`
# (el último día de la caché de NOAA OISST real; esta fecha si NOAA está en modo simulado).
FECHA_CORTE_SIMULADA = date(2026, 10, 27)
FECHA_CORTE = FECHA_CORTE_SIMULADA                # compatibilidad: la del simulador, no la del sistema

# ---------------------------------------------------------------------------
# Profundidades (m, positivas hacia abajo) y periodos de referencia
# ---------------------------------------------------------------------------
PROFUNDIDADES: list[float] = [0, 5, 10, 20, 30, 50, 75, 100]
PERIODOS_REFERENCIA: list[str] = ["1991-2020", "1981-2010", "1993-2022"]
PERIODO_REFERENCIA_POR_DEFECTO = "1991-2020"

# Semilla base del ruido reproducible (ver datos_simulados.py)
SEMILLA_BASE = 20261027

# ---------------------------------------------------------------------------
# Nombres de meses en español
# ---------------------------------------------------------------------------
MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun",
                "jul", "ago", "sep", "oct", "nov", "dic"]
MESES_LARGOS = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ---------------------------------------------------------------------------
# Utilidades de formato es-EC
# ---------------------------------------------------------------------------
def fmt_fecha(valor) -> str:
    """Devuelve la fecha como dd/mm/aaaa. Acepta date, datetime, numpy o cadena ISO."""
    if valor is None:
        return "—"
    if isinstance(valor, np.datetime64):
        valor = valor.astype("datetime64[s]").item()
    if isinstance(valor, str):
        valor = datetime.fromisoformat(valor[:19])
    return f"{valor.day:02d}/{valor.month:02d}/{valor.year:04d}"


def fmt_fecha_hora(valor) -> str:
    """Devuelve dd/mm/aaaa hh:mm."""
    if valor is None:
        return "—"
    if isinstance(valor, str):
        valor = datetime.fromisoformat(valor[:19])
    return f"{fmt_fecha(valor)} {valor.hour:02d}:{valor.minute:02d}"


def fmt_mes_anio(valor) -> str:
    """Devuelve «octubre de 2026»."""
    if isinstance(valor, np.datetime64):
        valor = valor.astype("datetime64[s]").item()
    if isinstance(valor, str):
        valor = datetime.fromisoformat(valor[:19])
    return f"{MESES_LARGOS[valor.month - 1]} de {valor.year}"


def fmt_num(x, decimales: int = 1) -> str:
    """Número con punto decimal (RNF-01), espacio fino como separador de miles y signo menos
    tipográfico «−» (U+2212), como `fmt_anomalia` y `util.fmtNum` del frontend. Un negativo que se
    redondea a cero sale sin signo («0.0», nunca «−0.0»)."""
    if x is None:
        return "—"
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return str(x)
    if math.isnan(xf) or math.isinf(xf):
        return "—"
    # Miles → espacio fino; el decimal queda como punto.
    texto = f"{abs(xf):,.{decimales}f}".replace(",", " ")
    negativo = xf < 0 and any(ch in "123456789" for ch in texto)
    return ("−" if negativo else "") + texto


def fmt_valor(x, unidad: str, decimales: int = 1) -> str:
    """«24.6 °C», «4.2 mm/día», «34.15 PSU»."""
    numero = fmt_num(x, decimales)
    if numero == "—" or not unidad:
        return numero
    return f"{numero} {unidad}"


def fmt_anomalia(x, unidad: str, decimales: int = 1) -> str:
    """«+1.8 °C» / «−0.6 °C» (con signo); lo que se redondea a cero va sin signo («0.0 °C», nunca
    «−0.0 °C»), como `util.fmtAnomalia` del frontend."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    cuerpo = fmt_valor(abs(float(x)), unidad, decimales)
    if not any(ch in "123456789" for ch in fmt_num(abs(float(x)), decimales)):
        return cuerpo
    signo = "+" if float(x) > 0 else "−"
    return f"{signo}{cuerpo}"


def fmt_coordenada(lat: float, lon: float, decimales: int = 2) -> str:
    """«2.00° S, 85.00° O»."""
    hemi_lat = "N" if lat >= 0 else "S"
    hemi_lon = "E" if lon >= 0 else "O"
    return (f"{fmt_num(abs(lat), decimales)}° {hemi_lat}, "
            f"{fmt_num(abs(lon), decimales)}° {hemi_lon}")


def iso_fecha(valor) -> str:
    """Fecha en formato ISO aaaa-mm-dd para la API."""
    if isinstance(valor, np.datetime64):
        valor = valor.astype("datetime64[s]").item()
    if isinstance(valor, datetime):
        valor = valor.date()
    return valor.isoformat()
