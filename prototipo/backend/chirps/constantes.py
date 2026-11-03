"""Constantes del acceso real a CHIRPS v3 (Climate Hazards Center, UCSB).

URL del servidor del CHC, reglas de cortesía (ritmo, User-Agent con contacto,
disyuntor), orígenes de los datos (servidor del CHC o réplica de
dynamical.org), rutas de la caché local, ventana del área de estudio en las
mallas nativas de 0.25° y 0.05°, y el interruptor de modo sin red
(`SOME_CHIRPS_SIN_RED=1`), que se lee en cada llamada para poder activarlo en
pruebas sin reiniciar el proceso.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from .. import configuracion as cfg

# ---------------------------------------------------------------------------
# Servidor del CHC
# ---------------------------------------------------------------------------
URL_BASE = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0"
URL_README = f"{URL_BASE}/README-CHIRPSv3.0.txt"
# Final «rnl» a 0.05° (carpeta del año, sin «p25/»): se agrega 5×5 en el SOME igual que el
# preliminar, la réplica y la climatología. El producto a 0.25° del CHC («p25/») NO se usa: su
# agregación costera es otra (máscara de tierra distinta y diferencias de hasta ~30 % en la costa).
URL_FINAL_ANIO = URL_BASE + "/daily/final/rnl/{anio:04d}/"
URL_PRELIM_ANIO = URL_BASE + "/daily/prelim/sat/{anio:04d}/"
URL_MENSUAL_ANIO = URL_BASE + "/monthly/global/netcdf/by_year/chirps-v3.0.{anio:04d}.monthly.nc"

# Contacto en el User-Agent, como pide el CHC a quien descarga de forma automática.
# Se define con la variable de entorno SOME_CHIRPS_CONTACTO (correo o URL del responsable
# de la instalación). No hay valor por defecto: el código no envía ningún correo personal
# a terceros sin que quien instala lo configure. Las cabeceras HTTP deben ser ASCII, por
# eso «academico» va sin tilde (y el contacto se limpia de caracteres no ASCII).
CONTACTO = os.environ.get("SOME_CHIRPS_CONTACTO", "").strip().encode("ascii", "ignore").decode()
USER_AGENT = (f"SOME-prototipo-INOCAR/{cfg.VERSION} (proyecto academico ESPOL-INOCAR"
              + (f"; contacto: {CONTACTO})" if CONTACTO else
                 "; contacto sin configurar: defina SOME_CHIRPS_CONTACTO)"))

# Cortesía con el CHC: como máximo 1 petición por segundo en TOTAL entre todos los procesos
# del SOME de este equipo (servidor, script de preparación, pruebas) y un archivo a la vez
# (las descargas del CHC van en serie). Ambas reglas se comparten entre procesos con
# archivos de bloqueo en datos/chirps/.bloqueos/ (ver `red`).
PETICIONES_POR_SEGUNDO = 1.0
TRABAJADORES = 1                  # hilos de descarga hacia el CHC (en serie)
TIMEOUT_CONEXION_S = 10           # para abrir la conexión
TIMEOUT_S = 30                    # para recibir la respuesta
INTENTOS = 3                      # por petición, con espera creciente entre intentos
# Disyuntor: tras FALLOS_DISYUNTOR intentos fallidos seguidos (sin conexión, tiempo
# agotado, HTTP 403/429/5xx) el CHC se marca caído y no se le pide nada durante
# DISYUNTOR_S segundos; pasado ese plazo se permite UN intento de prueba.
FALLOS_DISYUNTOR = 3
DISYUNTOR_S = 15 * 60

MAX_DIAS_BAJO_DEMANDA = 45        # días ausentes que una consulta pide al CHC en el momento
MAX_SEGUNDOS_BAJO_DEMANDA = 90    # tope de tiempo de esa descarga (el resto lo completa la sincronización)
ESPERA_ESPEJO_BAJO_DEMANDA_S = 5  # una consulta no espera más que esto a que termine otra lectura de la réplica
MAX_SEGUNDOS_ESPEJO_BAJO_DEMANDA = 25   # tope de espera de una lectura de la réplica dentro de una consulta
                                        # (sana tarda ~3-5 s por fragmento; si se cuelga, sigue en segundo plano)
ESPEJO_CAIDO_S = 5 * 60           # tras un fallo de lectura de la réplica, las consultas no la reintentan antes
MAX_ANIOS_CLIM_EN_PETICION = 3    # años de climatología que una consulta prepara en el momento (~3 s c/u)
MAX_FALLOS_CONEXION_SEGUIDOS = FALLOS_DISYUNTOR   # compatibilidad: interrumpe una descarga en serie

# Tipos de dato diario guardados en la caché
AUSENTE, PRELIMINAR, FINAL = 0, 1, 2
NOMBRE_TIPO = {AUSENTE: "ausente", PRELIMINAR: "preliminar", FINAL: "final"}

# ---------------------------------------------------------------------------
# Origen de cada día guardado
# ---------------------------------------------------------------------------
# Las dos fuentes publican el MISMO producto (CHIRPS v3 del CHC); la réplica de
# dynamical.org lo redistribuye en Zarr/Icechunk con los valores redondeados a 8 bits
# de mantisa (error ≤ ~0.2 %).
ORIGEN_NINGUNO, ORIGEN_CHC, ORIGEN_ESPEJO = 0, 1, 2
# Días finales que versiones anteriores del SOME tomaron del producto a 0.25° del CHC («p25»):
# otra agregación en la costa. Se conservan como «final provisional» (cuentan como días del CHC)
# y se reemplazan por el final a 0.05° agregado 5×5 (réplica o CHC) en cuanto alguno lo cubre.
ORIGEN_CHC_P25 = 3
CHC, ESPEJO = "chc", "espejo"
CODIGO_ORIGEN = {CHC: ORIGEN_CHC, ESPEJO: ORIGEN_ESPEJO}
NOMBRE_ORIGEN = {ORIGEN_NINGUNO: "", ORIGEN_CHC: CHC, ORIGEN_ESPEJO: ESPEJO, ORIGEN_CHC_P25: CHC}
TEXTO_ORIGEN_CORTO = {CHC: "CHC", ESPEJO: "réplica dynamical.org"}
DOI_REPOSITORIO = "https://doi.org/10.15780/G2JQ0P"
CITA_REPOSITORIO = ("Climate Hazards Center (2025). Climate Hazards Center Infrared Precipitation with Stations "
                    f"version 3 (CHIRPS3) Data Repository. UC Santa Barbara. {DOI_REPOSITORIO}")
# Atribución literal que pide dynamical.org (CC BY 4.0), con su cita y la modificación que hizo (CC BY 4.0,
# §3(a)(1)(B): hay que conservar la indicación de cambios previos).
CREDITO_ESPEJO = ("UCSB Climate Hazards Center CHIRPS version 3.0 data processed by dynamical.org from the Climate "
                  "Hazards Center data archive (Funk et al., 2026, Sci Data 13, 718; data "
                  f"doi:{DOI_REPOSITORIO.removeprefix('https://doi.org/')}). CC BY 4.0; valores redondeados por "
                  "dynamical.org a 8 bits de mantisa para comprimir; réplica sin garantía de servicio.")
URL_ESPEJO = "https://dynamical.org/catalog/"
# Días por lectura de la réplica: sus fragmentos miden 365 días contados desde el primer día
# de cada producto, así que cada lectura se alinea con un fragmento (se descarga una sola vez).
DIAS_BLOQUE_ESPEJO = 365


def url_final(d: date) -> str:
    return URL_FINAL_ANIO.format(anio=d.year) + f"chirps-v3.0.rnl.{d:%Y.%m.%d}.tif"


def url_prelim(d: date) -> str:
    return URL_PRELIM_ANIO.format(anio=d.year) + f"chirps-v3.0.prelim.{d:%Y.%m.%d}.tif"


# ---------------------------------------------------------------------------
# Área de estudio en las mallas nativas
# ---------------------------------------------------------------------------
# Celdas nativas de 0.25° que rodean la malla SOME: bordes 6.25° N … 6.25° S y
# 95.25° O … 77.75° O → 50 filas × 70 columnas (centros 6.125 … −6.125 y
# −95.125 … −77.875). Cada centro SOME (múltiplo de 0.25°) es la esquina común
# de cuatro de estas celdas.
LAT_NORTE, LAT_SUR = 6.25, -6.25
LON_OESTE, LON_ESTE = -95.25, -77.75
FILAS_NATIVAS, COLUMNAS_NATIVAS = 50, 70

# Valores mínimos de celdas válidas
MIN_VALIDAS_5X5 = 13     # de 25 celdas de 0.05° para formar una de 0.25°
MIN_VALIDAS_2X2 = 2      # de 4 celdas de 0.25° para un centro SOME

# Climatología mensual disponible (los periodos de referencia caen en 1981-2022)
ANIO_CLIM_MIN, ANIO_CLIM_MAX = 1981, 2022

# ---------------------------------------------------------------------------
# Caché local
# ---------------------------------------------------------------------------
# SOME_RUTA_CHIRPS (opcional, se lee al importar) lleva toda la caché (diario, mensual, estado.json y
# .bloqueos) a otra carpeta, para servidores o pruebas aislados. SOME_RUTA_DATOS no la redirige, a
# propósito: la prueba de humo usa la caché real. Con otra carpeta, el turno de cortesía con el CHC
# (1 petición por segundo) ya no se comparte con el servidor que usa datos/chirps/: úsela con
# SOME_CHIRPS_SIN_RED=1 o sin otro proceso descargando a la vez.
_RUTA_ENTORNO = os.environ.get("SOME_RUTA_CHIRPS", "").strip()
RUTA_CHIRPS = Path(_RUTA_ENTORNO).resolve() if _RUTA_ENTORNO else cfg.RUTA_DATOS / "chirps"
RUTA_DIARIO = RUTA_CHIRPS / "diario"
RUTA_MENSUAL = RUTA_CHIRPS / "mensual"
RUTA_ESTADO = RUTA_CHIRPS / "estado.json"

INTERVALO_SINCRONIZACION_S = 6 * 3600
INTERVALO_REINTENTO_S = 30 * 60   # si una fuente falló en el último ciclo (mayor que DISYUNTOR_S)
CACHE_DISPONIBILIDAD_S = 30 * 60
LATENCIA_ALTA_MS = 3000
VERIFICACION_VIGENTE_H = 13       # una verificación más antigua ya no respalda «operativa»


def _activa(variable: str) -> bool:
    return os.environ.get(variable, "").strip().lower() in ("1", "true", "si", "sí", "yes")


def sin_red() -> bool:
    """True si `SOME_CHIRPS_SIN_RED` está activo: no se toca la red, solo la caché local."""
    return _activa("SOME_CHIRPS_SIN_RED")


def sin_sincronizacion() -> bool:
    """True si `SOME_CHIRPS_SIN_SINCRONIZACION` está activo: no se arranca el hilo de sincronización
    (pruebas y desarrollo; las consultas siguen llenando bajo demanda lo que falte)."""
    return _activa("SOME_CHIRPS_SIN_SINCRONIZACION")


class ErrorCHIRPS(RuntimeError):
    """Error de acceso o de formato al leer CHIRPS v3."""
