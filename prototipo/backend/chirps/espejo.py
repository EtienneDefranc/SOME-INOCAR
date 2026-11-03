"""Réplica de CHIRPS v3 publicada por dynamical.org (almacenes Icechunk/Zarr en S3, acceso anónimo).

dynamical.org copia el archivo diario de CHIRPS v3 del Climate Hazards Center
(UCSB) en dos almacenes Icechunk del bucket público `dynamical-ucsb-chc-chirps`
(región us-west-2, licencia CC BY 4.0). Es un tercero sin garantía de servicio:
el prototipo lo usa como fuente alternativa cuando el servidor del CHC no
responde y como fuente rápida de la climatología mensual.

* Producto «final» (`ucsb-chc-chirps-analysis-final`): disgregación diaria con
  ERA5 («rnl»), desde 01/01/1981.
* Producto «preliminar» (`ucsb-chc-chirps-analysis-preliminary`): desde
  01/01/2025 hasta unos días antes de hoy.

Formato de ambos: variable `precipitation_surface (time, latitude, longitude)`
float32 en kg m⁻² s⁻¹ (= mm/s; ×86 400 → mm/día), NaN en el mar, 0.05°,
latitud DESCENDENTE (59.975 … −59.975), longitud −179.975 … 179.975,
fragmentos (365, 60, 60). Los valores vienen redondeados a 8 bits de mantisa
(error relativo ≤ ~0.2 %).

Procesamiento (idéntico al de los GeoTIFF del CHC, ver `malla.py`): recorte de
las 250 × 350 celdas de 0.05° cuyos centros caen dentro de 6.25° S – 6.25° N y
95.25° O – 77.75° O (los índices se calculan a partir de las coordenadas del
almacén y se validan; no se suponen), conversión a mm/día, limpieza
(`lector_tiff.limpiar`), latitud a ascendente, agregación 5×5 a 0.25° nativo y
reproyección a la malla SOME (49 × 69).

Cada almacén se abre una sola vez por proceso (objeto Dataset en memoria,
protegido con un bloqueo); se vuelve a abrir solo para ver días nuevos: al
recalcular la cobertura (cada 30 min o con `forzar=True`) o cuando se pide
una fecha posterior a la última conocida y la apertura tiene más de 30 min.
Las bibliotecas icechunk y zarr se importan de forma perezosa (dentro de las
funciones) para no retrasar el arranque del servidor.

Interfaz pública: `disponible()`, `cobertura()`, `leer_diario()`,
`leer_mensual_anio()`, `ErrorEspejo`, `TEXTO_ORIGEN`.
"""

from __future__ import annotations

import calendar
import importlib.util
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np

from .constantes import (COLUMNAS_NATIVAS, FILAS_NATIVAS, LAT_NORTE, LAT_SUR, LON_ESTE, LON_OESTE,
                         ErrorCHIRPS, sin_red)
from .lector_tiff import limpiar
from .malla import agregar_5x5, reproyectar_a_some

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de la réplica
# ---------------------------------------------------------------------------
TEXTO_ORIGEN = "réplica dynamical.org de CHIRPS v3 (CC BY 4.0)"

BUCKET = "dynamical-ucsb-chc-chirps"
REGION = "us-west-2"
PREFIJOS = {
    "final": "ucsb-chc-chirps-analysis-final/v0.1.0.icechunk/",
    "preliminar": "ucsb-chc-chirps-analysis-preliminary/v0.1.0.icechunk/",
}
PRODUCTOS = tuple(PREFIJOS)
VARIABLE = "precipitation_surface"
URL_ALMACEN = {p: f"s3://{BUCKET}/{pref}" for p, pref in PREFIJOS.items()}
TEXTO_PROCESAMIENTO = (
    "Réplica dynamical.org de CHIRPS v3 (almacén Icechunk/Zarr en S3, CC BY 4.0): tasa de "
    "precipitación diaria a 0.05° en kg m⁻² s⁻¹ convertida a mm/día (×86 400), recorte de "
    "6.25° S – 6.25° N y 95.25° O – 77.75° O, agregación a 0.25° con la media de las celdas válidas "
    "de cada bloque de 5×5 (mínimo 13 de 25) y reproyección a la malla SOME (media de 4 celdas "
    "nativas, mínimo 2). Valores redondeados por la réplica a 8 bits de mantisa (error ≤ 0.2 %)."
)

SEGUNDOS_POR_DIA = 86400.0
PASO_GRADOS = 0.05
VIGENCIA_COBERTURA_S = 30 * 60      # caché en memoria de cobertura()
VIGENCIA_ERROR_S = 5 * 60           # tras un fallo no se reintenta antes (salvo forzar=True)
EDAD_MAXIMA_APERTURA_S = 30 * 60    # una apertura más vieja se renueva si se pide una fecha posterior
MIN_FRACCION_DIAS_MES = 0.8         # leer_mensual_anio: días con dato exigidos por celda y mes

# Tiempos de espera y reintentos del cliente S3 (sin red, una apertura falla en ~10 s)
_TIMEOUT_CONEXION_MS = 5_000
_TIMEOUT_LECTURA_MS = 30_000
_TIMEOUT_OPERACION_MS = 120_000
_TIMEOUT_INTENTO_MS = 60_000
_MAX_INTENTOS = 2


class ErrorEspejo(ErrorCHIRPS):
    """Error de acceso o de formato al leer la réplica dynamical.org de CHIRPS v3."""


# ---------------------------------------------------------------------------
# Disponibilidad de las bibliotecas
# ---------------------------------------------------------------------------
_modulos_ok: bool | None = None     # None = aún no comprobado


def disponible() -> bool:
    """True si icechunk y zarr se pueden importar y no está activo SOME_CHIRPS_SIN_RED. Nunca lanza.

    No importa las bibliotecas (solo comprueba que estén instaladas), para ser
    instantáneo; si una importación real falla después, pasa a devolver False.
    """
    global _modulos_ok
    try:
        if sin_red():
            return False
        if _modulos_ok is None:
            _modulos_ok = all(importlib.util.find_spec(m) is not None for m in ("icechunk", "zarr"))
        return bool(_modulos_ok)
    except Exception:  # noqa: BLE001 — contrato: nunca lanza
        return False


def _importar():
    """Importa icechunk y xarray (perezoso). Lanza ErrorEspejo si no se puede."""
    global _modulos_ok
    try:
        import icechunk
        import xarray as xr
        import zarr  # noqa: F401 — lo usa xarray como motor
    except Exception as e:  # noqa: BLE001
        _modulos_ok = False
        raise ErrorEspejo(
            f"No se pudieron cargar las bibliotecas de la réplica de CHIRPS (icechunk/zarr): {e}. "
            "Instálelas con «python -m pip install -r requirements.txt».") from e
    return icechunk, xr


_MARCAS_RED = ("StorageError", "dispatch failure", "ExceptionGroup", "TaskGroup", "timeout", "Timeout",
               "connect", "Connect", "proxy", "Proxy", "dns", "network", "Network", "io error")


def _texto_error(e: BaseException) -> str:
    """Motivo corto en español (los errores de icechunk traen trazas largas en inglés).

    Un fallo de red se resume («la réplica no respondió (tiempo de espera agotado al conectar)»);
    cualquier otro error conserva su tipo y un texto acotado sin cortar palabras. El texto crudo
    completo va al log.
    """
    txt = " ".join(str(e).split())
    log.info("CHIRPS: error de la réplica dynamical.org: %s: %s", type(e).__name__, txt[:2000])
    try:
        from .estado import _traducir_tecnico, acortar
    except Exception:  # noqa: BLE001
        return type(e).__name__
    if any(m in f"{type(e).__name__} {txt}" for m in _MARCAS_RED):
        motivo = _traducir_tecnico(f"{type(e).__name__}: {txt}")
        prefijo = "la réplica no respondió ("
        # Quien llama ya nombra la réplica («No se pudo abrir la réplica…: »): basta el motivo.
        return motivo[len(prefijo):-1] if motivo.startswith(prefijo) else motivo
    for corte in (" context:", " Context:"):
        if corte in txt:
            txt = txt.split(corte)[0]
    txt = acortar(txt, 200)
    return f"{type(e).__name__}: {txt}" if txt else type(e).__name__


# ---------------------------------------------------------------------------
# Apertura de los almacenes (una vez por proceso)
# ---------------------------------------------------------------------------
@dataclass
class _Almacen:
    producto: str
    datos: object            # xarray.DataArray de precipitation_surface (perezoso)
    fechas: np.ndarray       # datetime64[D], ordenadas
    f0: int                  # filas [f0, f1) de latitud del recorte
    f1: int
    c0: int                  # columnas [c0, c1) de longitud del recorte
    c1: int
    lat_descendente: bool
    bloque_t: int            # tamaño del fragmento en el eje del tiempo
    abierto: float           # time.monotonic() de la apertura
    duracion_ms: int         # lo que tardó la apertura

    @property
    def primera(self) -> date:
        return _a_fecha(self.fechas[0])

    @property
    def ultima(self) -> date:
        return _a_fecha(self.fechas[-1])


_bloqueo_global = threading.Lock()
_bloqueos: dict[str, threading.Lock] = {p: threading.Lock() for p in PRODUCTOS}
_almacenes: dict[str, _Almacen] = {}


def _a_fecha(d64) -> date:
    return date.fromisoformat(str(np.datetime64(d64, "D")))


def _ventana(coord: np.ndarray, minimo: float, maximo: float, eje: str) -> tuple[int, int, bool]:
    """[i0, i1) de las celdas cuyo centro cae dentro de (minimo, maximo), validando la malla.

    Comprueba, leyendo las coordenadas del almacén, que el paso sea 0.05°, que el
    número de celdas sea el esperado y que los centros extremos estén a medio paso
    de los bordes. Devuelve también si el eje es descendente.
    """
    coord = np.asarray(coord, dtype=np.float64)
    if coord.ndim != 1 or coord.size < 2:
        raise ErrorEspejo(f"Coordenada «{eje}» inesperada en la réplica (forma {coord.shape})")
    dentro = np.nonzero((coord > minimo + 1e-6) & (coord < maximo - 1e-6))[0]
    esperadas = int(round((maximo - minimo) / PASO_GRADOS))
    if dentro.size != esperadas or dentro[-1] - dentro[0] + 1 != esperadas:
        raise ErrorEspejo(f"La réplica no tiene {esperadas} celdas contiguas de {eje} en el área de "
                          f"estudio (hay {dentro.size})")
    i0, i1 = int(dentro[0]), int(dentro[-1]) + 1
    tramo = coord[i0:i1]
    pasos = np.abs(np.diff(tramo))
    if np.max(np.abs(pasos - PASO_GRADOS)) > 1e-6:
        raise ErrorEspejo(f"El paso de la {eje} en la réplica no es de 0.05° (entre {pasos.min():.6f} "
                          f"y {pasos.max():.6f})")
    lo, hi = float(tramo.min()), float(tramo.max())
    if abs(lo - (minimo + PASO_GRADOS / 2)) > 1e-6 or abs(hi - (maximo - PASO_GRADOS / 2)) > 1e-6:
        raise ErrorEspejo(f"Centros extremos de {eje} inesperados en la réplica: {lo:.4f} … {hi:.4f}")
    return i0, i1, bool(tramo[0] > tramo[-1])


def _abrir_almacen(producto: str) -> _Almacen:
    """Abre (con red) el almacén Icechunk del producto y valida su georreferenciación."""
    icechunk, xr = _importar()
    t0 = time.perf_counter()
    try:
        configuracion = icechunk.RepositoryConfig(storage=icechunk.StorageSettings(
            timeouts=icechunk.StorageTimeoutSettings(
                connect_timeout_ms=_TIMEOUT_CONEXION_MS, read_timeout_ms=_TIMEOUT_LECTURA_MS,
                operation_timeout_ms=_TIMEOUT_OPERACION_MS,
                operation_attempt_timeout_ms=_TIMEOUT_INTENTO_MS),
            retries=icechunk.StorageRetriesSettings(max_tries=_MAX_INTENTOS, initial_backoff_ms=500,
                                                    max_backoff_ms=4_000)))
        almacenamiento = icechunk.s3_storage(bucket=BUCKET, prefix=PREFIJOS[producto], region=REGION,
                                             anonymous=True)
        repo = icechunk.Repository.open(almacenamiento, config=configuracion)
        ds = xr.open_zarr(repo.readonly_session("main").store, consolidated=False, chunks=None)
    except ErrorEspejo:
        raise
    except Exception as e:  # noqa: BLE001
        raise ErrorEspejo(f"No se pudo abrir la réplica dynamical.org de CHIRPS (producto "
                          f"{producto}): {_texto_error(e)}") from e
    return _preparar_almacen(ds, producto, int(round((time.perf_counter() - t0) * 1000)))


def _preparar_almacen(ds, producto: str, duracion_ms: int = 0) -> _Almacen:
    """Valida el Dataset (variable, dimensiones, unidades, malla, fechas) y calcula el recorte."""
    if VARIABLE not in ds:
        raise ErrorEspejo(f"La réplica (producto {producto}) no contiene la variable «{VARIABLE}»")
    datos = ds[VARIABLE]
    if tuple(datos.dims) != ("time", "latitude", "longitude"):
        raise ErrorEspejo(f"Dimensiones inesperadas en la réplica: {datos.dims}")
    unidades = str(datos.attrs.get("units", "")).replace(" ", "")
    if unidades not in ("kgm-2s-1", "kgm**-2s**-1", "mm/s", "mms-1"):
        raise ErrorEspejo(f"Unidades inesperadas en la réplica: «{datos.attrs.get('units')}» "
                          "(se esperaba kg m-2 s-1)")
    f0, f1, lat_desc = _ventana(ds["latitude"].values, LAT_SUR, LAT_NORTE, "latitud")
    c0, c1, lon_desc = _ventana(ds["longitude"].values, LON_OESTE, LON_ESTE, "longitud")
    if lon_desc:
        raise ErrorEspejo("Longitud descendente no soportada en la réplica")
    if (f1 - f0, c1 - c0) != (FILAS_NATIVAS * 5, COLUMNAS_NATIVAS * 5):
        raise ErrorEspejo(f"Recorte inesperado {(f1 - f0, c1 - c0)} en la réplica")
    fechas = np.asarray(ds["time"].values).astype("datetime64[D]")
    if fechas.size == 0:
        raise ErrorEspejo(f"La réplica (producto {producto}) no tiene fechas")
    if np.any(np.diff(fechas).astype(np.int64) <= 0):
        raise ErrorEspejo(f"Las fechas de la réplica (producto {producto}) no están ordenadas")
    trozos = datos.encoding.get("preferred_chunks", {}).get("time") or \
        (datos.encoding.get("chunks") or (365,))[0]
    return _Almacen(producto=producto, datos=datos, fechas=fechas, f0=f0, f1=f1, c0=c0, c1=c1,
                    lat_descendente=lat_desc, bloque_t=max(1, int(trozos)),
                    abierto=time.monotonic(), duracion_ms=int(duracion_ms))


def _abrir(producto: str, edad_maxima_s: float | None = None) -> _Almacen:
    """Almacén del producto, abierto una sola vez por proceso.

    Si `edad_maxima_s` se indica y la apertura en memoria es más vieja, se vuelve
    a abrir (para ver días nuevos). Si la reapertura falla se conserva la anterior
    y se propaga el error.
    """
    if producto not in PREFIJOS:
        raise ValueError(f"Producto de la réplica desconocido: «{producto}» (use «final» o «preliminar»)")
    if sin_red():
        raise ErrorEspejo("Modo sin red activo (SOME_CHIRPS_SIN_RED=1): no se consulta la réplica de CHIRPS.")
    with _bloqueos[producto]:
        actual = _almacenes.get(producto)
        if actual is not None and (edad_maxima_s is None
                                   or time.monotonic() - actual.abierto <= edad_maxima_s):
            return actual
        nuevo = _abrir_almacen(producto)
        with _bloqueo_global:
            _almacenes[producto] = nuevo
        return nuevo


# ---------------------------------------------------------------------------
# Cobertura
# ---------------------------------------------------------------------------
_bloqueo_cobertura = threading.Lock()
_cobertura_cache: tuple[float, dict] | None = None


def _ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def cobertura(forzar: bool = False) -> dict:
    """Fechas cubiertas por cada producto de la réplica (abre los almacenes; caché de 30 min).

    Devuelve {"final": (date, date) | None, "preliminar": (date, date) | None,
    "consultado": "aaaa-mm-ddThh:mm:ss", "latencia_ms": int, "error": ""}.
    `latencia_ms` es lo que tardó en abrirse el almacén más lento (se abren en
    paralelo). Si algo falla, el producto afectado queda en None y "error"
    describe el problema en español. Nunca lanza.
    """
    global _cobertura_cache
    try:
        with _bloqueo_cobertura:
            if _cobertura_cache is not None and not forzar:
                momento, previo = _cobertura_cache
                vigencia = VIGENCIA_ERROR_S if previo["error"] else VIGENCIA_COBERTURA_S
                if time.monotonic() - momento < vigencia:
                    return dict(previo)
            resultado = _consultar_cobertura(forzar)
            _cobertura_cache = (time.monotonic(), resultado)
            return dict(resultado)
    except Exception as e:  # noqa: BLE001 — contrato: nunca lanza
        return {"final": None, "preliminar": None, "consultado": _ahora_iso(), "latencia_ms": 0,
                "error": f"Error inesperado al consultar la réplica de CHIRPS: {_texto_error(e)}"}


def _consultar_cobertura(forzar: bool) -> dict:
    resultado = {"final": None, "preliminar": None, "consultado": _ahora_iso(), "latencia_ms": 0,
                 "error": ""}
    if sin_red():
        resultado["error"] = "Modo sin red activo (SOME_CHIRPS_SIN_RED=1): no se consulta la réplica de CHIRPS."
        return resultado
    if not disponible():
        resultado["error"] = ("Las bibliotecas icechunk y zarr no están instaladas; la réplica dynamical.org "
                              "de CHIRPS no está disponible (python -m pip install -r requirements.txt).")
        return resultado
    edad = 0.0 if forzar else 60.0     # una apertura de hace menos de 1 min se reutiliza
    errores: list[str] = []
    with ThreadPoolExecutor(max_workers=len(PRODUCTOS), thread_name_prefix="chirps-espejo") as ex:
        futuros = {p: ex.submit(_abrir, p, edad) for p in PRODUCTOS}
        duraciones = []
        for p, fut in futuros.items():
            try:
                alm = fut.result()
                resultado[p] = (alm.primera, alm.ultima)
                duraciones.append(alm.duracion_ms)
            except Exception as e:  # noqa: BLE001
                errores.append(str(e) if isinstance(e, ErrorEspejo) else
                               f"Réplica de CHIRPS (producto {p}): {_texto_error(e)}")
    resultado["latencia_ms"] = int(max(duraciones, default=0))
    # Los dos productos suelen fallar por lo mismo: un solo mensaje en vez de dos casi iguales.
    unicos = list(dict.fromkeys(e.replace("(producto preliminar)", "(producto final)") for e in errores))
    if len(errores) == 2 and len(unicos) == 1:
        errores = [unicos[0].replace("(producto final)", "(productos final y preliminar)")]
    resultado["error"] = " · ".join(errores)
    return resultado


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------
def _leer_recorte(alm: _Almacen, i0: int, i1: int) -> np.ndarray:
    """(i1 − i0, 250, 350) en mm/día a 0.05°, latitud ASCENDENTE, NaN = mar/sin dato.

    Opera en el sitio sobre el arreglo que entrega zarr (ya float32): la conversión a mm/día,
    la inversión de la latitud (una vista) y la limpieza no crean copias completas del recorte
    (un año son ~122 MB).
    """
    try:
        crudo = alm.datos[i0:i1, alm.f0:alm.f1, alm.c0:alm.c1].values
    except Exception as e:  # noqa: BLE001
        raise ErrorEspejo(f"No se pudieron leer los datos de la réplica de CHIRPS (producto "
                          f"{alm.producto}): {_texto_error(e)}") from e
    mm = np.asarray(crudo, dtype=np.float32)
    # Con datos perezosos (zarr) cada lectura entrega un arreglo nuevo que se puede modificar; si el
    # DataArray ya está en memoria (p. ej. en las pruebas), `.values` es una vista del original.
    if not mm.flags.writeable or isinstance(getattr(alm.datos.variable, "_data", None), np.ndarray):
        mm = mm.copy()
    mm *= np.float32(SEGUNDOS_POR_DIA)
    if alm.lat_descendente:
        mm = mm[:, ::-1, :]
    return limpiar(mm, copiar=False)


def _bloques(alm: _Almacen, i0: int, i1: int):
    """Tramos [a, b) de [i0, i1) alineados con los fragmentos del eje del tiempo."""
    a = i0
    while a < i1:
        b = min(i1, (a // alm.bloque_t + 1) * alm.bloque_t)
        yield a, b
        a = b


def _indices_intervalo(alm: _Almacen, inicio: date, fin: date) -> tuple[int, int]:
    i0 = int(np.searchsorted(alm.fechas, np.datetime64(inicio, "D"), side="left"))
    i1 = int(np.searchsorted(alm.fechas, np.datetime64(fin, "D"), side="right"))
    return i0, i1


def _almacen_para(producto: str, fin: date) -> _Almacen:
    alm = _abrir(producto)
    if fin > alm.ultima and time.monotonic() - alm.abierto > EDAD_MAXIMA_APERTURA_S:
        alm = _abrir(producto, EDAD_MAXIMA_APERTURA_S)     # puede haber días nuevos
    return alm


def leer_diario(inicio: date, fin: date, producto: str) -> tuple[list[date], np.ndarray]:
    """Precipitación diaria de la réplica en la malla SOME.

    `producto`: "final" o "preliminar". Devuelve las fechas disponibles en
    [inicio, fin] y los valores (n, 49, 69) float32 en mm/día (NaN = mar o sin
    dato). Un día cuyo recorte viene entero sin dato (hueco del archivo) no se
    devuelve. Lanza ErrorEspejo si no hay red o almacén, o si el intervalo cae
    fuera de la cobertura; ValueError si los argumentos no son válidos.
    """
    if producto not in PREFIJOS:
        raise ValueError(f"Producto de la réplica desconocido: «{producto}» (use «final» o «preliminar»)")
    if fin < inicio:
        raise ValueError(f"Intervalo vacío: {inicio.isoformat()} > {fin.isoformat()}")
    if not disponible():
        if sin_red():
            raise ErrorEspejo("Modo sin red activo (SOME_CHIRPS_SIN_RED=1): no se consulta la réplica de CHIRPS.")
        raise ErrorEspejo("Las bibliotecas icechunk y zarr no están instaladas; la réplica de CHIRPS no está disponible.")
    return _leer_diario_almacen(_almacen_para(producto, fin), inicio, fin)


def _leer_diario_almacen(alm: _Almacen, inicio: date, fin: date) -> tuple[list[date], np.ndarray]:
    producto = alm.producto
    i0, i1 = _indices_intervalo(alm, inicio, fin)
    if i1 <= i0:
        raise ErrorEspejo(
            f"El intervalo {inicio:%d/%m/%Y} – {fin:%d/%m/%Y} está fuera de la cobertura del producto "
            f"{producto} de la réplica ({alm.primera:%d/%m/%Y} – {alm.ultima:%d/%m/%Y}).")
    fechas: list[date] = []
    partes: list[np.ndarray] = []
    for a, b in _bloques(alm, i0, i1):
        recorte = _leer_recorte(alm, a, b)
        con_dato = np.isfinite(recorte).any(axis=(1, 2))
        if not con_dato.any():
            continue
        some = reproyectar_a_some(agregar_5x5(recorte if con_dato.all() else recorte[con_dato]))
        partes.append(some)
        fechas.extend(_a_fecha(f) for f in alm.fechas[a:b][con_dato])
        del recorte
    if not partes:
        raise ErrorEspejo(f"La réplica no tiene datos válidos del producto {producto} entre "
                          f"{inicio:%d/%m/%Y} y {fin:%d/%m/%Y}.")
    return fechas, np.concatenate(partes).astype(np.float32)


def leer_mensual_anio(anio: int) -> np.ndarray:
    """Media mensual (12, 49, 69) float32 en mm/día del año, calculada con el diario FINAL.

    Método (el mismo orden que el NetCDF mensual del CHC, para que ambas fuentes
    sean comparables): en cada celda de 0.05° y cada mes, suma de los días con
    dato / número de días con dato (si una celda tiene menos del 80 % de los
    días del mes con dato, queda sin dato); después agregación 5×5 y
    reproyección a la malla SOME. Así un día ausente no sesga la media hacia
    abajo (no se divide por días que no existen); con el mes completo equivale a
    total mensual / días del mes. Exige el año completo en el producto final;
    si no, o si falla la red, lanza ErrorEspejo.
    """
    anio = int(anio)
    if not disponible():
        if sin_red():
            raise ErrorEspejo("Modo sin red activo (SOME_CHIRPS_SIN_RED=1): no se consulta la réplica de CHIRPS.")
        raise ErrorEspejo("Las bibliotecas icechunk y zarr no están instaladas; la réplica de CHIRPS no está disponible.")
    inicio, fin = date(anio, 1, 1), date(anio, 12, 31)
    alm = _almacen_para("final", fin)
    if inicio < alm.primera or fin > alm.ultima:
        raise ErrorEspejo(f"El año {anio} no está completo en el producto final de la réplica "
                          f"({alm.primera:%d/%m/%Y} – {alm.ultima:%d/%m/%Y}).")
    i0, i1 = _indices_intervalo(alm, inicio, fin)
    return reproyectar_a_some(agregar_5x5(_media_mensual(alm, anio, i0, i1))).astype(np.float32)


def _media_mensual(alm: _Almacen, anio: int, i0: int, i1: int) -> np.ndarray:
    """(12, 250, 350) mm/día a 0.05° (latitud ascendente) de los días [i0, i1) del año `anio`.

    Por celda y mes: suma de los días con dato / días con dato; NaN si hay menos
    del 80 % de los días del mes (los meses sin días en [i0, i1) quedan en NaN).
    """
    fechas = [_a_fecha(f) for f in alm.fechas[i0:i1]]
    if any(f.year != anio for f in fechas):
        raise ValueError(f"_media_mensual: hay días fuera del año {anio}")
    meses_por_dia = np.array([f.month - 1 for f in fechas], dtype=np.int64)
    filas, cols = alm.f1 - alm.f0, alm.c1 - alm.c0
    suma = np.zeros((12, filas, cols), dtype=np.float64)
    cuenta = np.zeros((12, filas, cols), dtype=np.int32)
    for a, b in _bloques(alm, i0, i1):
        recorte = _leer_recorte(alm, a, b)
        meses = meses_por_dia[a - i0:b - i0]
        for m in np.unique(meses):
            sel = recorte[meses == m]
            validas = np.isfinite(sel)
            suma[m] += np.where(validas, sel, 0.0).sum(axis=0, dtype=np.float64)
            cuenta[m] += validas.sum(axis=0, dtype=np.int32)
        del recorte
    dias_mes = np.array([calendar.monthrange(anio, m)[1] for m in range(1, 13)], dtype=np.int32)
    minimo = np.array([math.ceil(MIN_FRACCION_DIAS_MES * d) for d in dias_mes], dtype=np.int32)
    with np.errstate(invalid="ignore", divide="ignore"):
        media = (suma / cuenta).astype(np.float32)
    media[cuenta < minimo[:, None, None]] = np.nan
    return media


def registrar_fallo_lectura(error: str) -> None:
    """Tras un fallo al LEER datos: la cobertura en memoria pasa a «con error» (vigente
    `VIGENCIA_ERROR_S`), para que nadie siga dando la réplica por accesible con una cobertura de
    hace 30 min. `cobertura(forzar=True)` la vuelve a consultar de verdad."""
    global _cobertura_cache
    with _bloqueo_cobertura:
        _cobertura_cache = (time.monotonic(), {"final": None, "preliminar": None, "consultado": _ahora_iso(),
                                               "latencia_ms": 0,
                                               "error": f"Falló la lectura de datos de la réplica: {error}"})


def olvidar() -> None:
    """Descarta los almacenes abiertos y la caché de cobertura (para pruebas)."""
    global _cobertura_cache
    with _bloqueo_global:
        _almacenes.clear()
    with _bloqueo_cobertura:
        _cobertura_cache = None
