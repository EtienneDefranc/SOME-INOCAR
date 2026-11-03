"""Único punto de contacto con el Copernicus Marine Toolbox (`copernicusmarine`).

* Importación PEREZOSA y una sola vez (`importar()`, ~4 s): nunca al arrancar el
  servidor ni en una consulta servida desde la caché. Antes de importarlo fija
  `COPERNICUSMARINE_HTTPS_TIMEOUT=20` y `COPERNICUSMARINE_HTTPS_RETRIES=2` (si no
  están definidas; el *toolbox* usa 60 s y 5 reintentos) y después baja su
  *logger* a WARNING.
* **Guardia anti-*prompt***: reemplaza
  `copernicusmarine.core_functions.credentials_utils._retrieve_credential_from_prompt`
  por una función que lanza `ErrorSinCredenciales`. Sin ella, `open_dataset` sin
  credenciales llamaría a `click.prompt` y dejaría un hilo del servidor esperando
  una contraseña. Si el atributo no existe (otra versión), se registra un aviso
  y las descargas exigen credenciales verificadas como válidas.
* Credenciales: este módulo NUNCA pide, escribe, imprime, registra ni guarda
  usuario o contraseña y nunca los pasa como argumento. El usuario los guarda él
  mismo con «copernicusmarine login». `credenciales_presentes()` solo mira si
  existen las variables de entorno o los archivos (sin leer su contenido);
  `verificar_credenciales()` usa solo `login(check_credentials_valid=True)`, que
  no pregunta nunca.
* Lecturas: `leer_ventana(...)` = `open_dataset` (perezoso, ARCO, recorte de área,
  días y servicio explícito) + selección de los niveles necesarios + `.load()`.
  `metadatos(dataset_id)` = `describe`. Los errores se traducen al español
  (`ErrorSinAcceso`, `ErrorSinCredenciales`, `ErrorFormato`, `ErrorCopernicus`).

Las pruebas inyectan un módulo falso con `usar_modulo(...)`.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from .constantes import ErrorCopernicus, ErrorFormato, ErrorSinAcceso, ErrorSinCredenciales, sin_red

log = logging.getLogger("some.copernicus")

MENSAJE_NO_INSTALADO = ("El paquete copernicusmarine no está instalado: pip install -r requirements-copernicus.txt")
MENSAJE_SIN_CREDENCIALES = ("Sin credenciales de Copernicus Marine: ejecute «copernicusmarine login» en una consola "
                            "de este equipo (la cuenta es gratuita)")
MENSAJE_SIN_RED = "Modo sin red (SOME_COPERNICUS_SIN_RED=1): no se consulta Copernicus Marine"
TOLERANCIA_NIVEL = 0.01

_bloqueo_import = threading.Lock()
_modulo = None                 # módulo copernicusmarine (o el falso de las pruebas)
_falso = False
_guardia_instalada = False
_spec_cache: tuple[float, bool] | None = None
_version_cache: str | None = None


# ---------------------------------------------------------------------------
# Paquete e importación
# ---------------------------------------------------------------------------
def usar_modulo(modulo) -> tuple:
    """Inyecta un módulo `copernicusmarine` falso (pruebas). Devuelve el estado anterior para `restaurar`."""
    global _modulo, _falso, _guardia_instalada
    previo = (_modulo, _falso, _guardia_instalada)
    with _bloqueo_import:
        _modulo, _falso, _guardia_instalada = modulo, modulo is not None, False
        if modulo is not None:
            _instalar_guardia(modulo)
    return previo


def restaurar(previo: tuple) -> None:
    global _modulo, _falso, _guardia_instalada
    with _bloqueo_import:
        _modulo, _falso, _guardia_instalada = previo


def paquete_instalado() -> bool:
    """True si `copernicusmarine` está instalado (sin importarlo; resultado negativo recordado 60 s)."""
    global _spec_cache
    if _falso:
        return True
    ahora = time.monotonic()
    if _spec_cache is not None and (_spec_cache[1] or ahora - _spec_cache[0] < 60):
        return _spec_cache[1]
    try:
        importlib.invalidate_caches()
        hay = importlib.util.find_spec("copernicusmarine") is not None
    except (ImportError, ValueError):
        hay = False
    _spec_cache = (ahora, hay)
    return hay


def version_paquete() -> str | None:
    """Versión instalada del *toolbox* (sin importarlo), o None."""
    global _version_cache
    if _falso:
        return str(getattr(_modulo, "__version__", "falso"))
    if _version_cache is None and paquete_instalado():
        try:
            _version_cache = importlib.metadata.version("copernicusmarine")
        except importlib.metadata.PackageNotFoundError:
            return None
    return _version_cache


def _prompt_prohibido(*_args, **_kwargs):
    raise ErrorSinCredenciales(MENSAJE_SIN_CREDENCIALES)


def _instalar_guardia(modulo) -> None:
    """Reemplaza la función del *toolbox* que pediría usuario o contraseña por una que lanza."""
    global _guardia_instalada
    cu = None
    try:
        cu = modulo.core_functions.credentials_utils
    except AttributeError:
        try:
            cu = importlib.import_module(f"{modulo.__name__}.core_functions.credentials_utils")
        except Exception:  # noqa: BLE001
            cu = None
    if cu is not None and hasattr(cu, "_retrieve_credential_from_prompt"):
        cu._retrieve_credential_from_prompt = _prompt_prohibido
        _guardia_instalada = True
    else:
        _guardia_instalada = False
        log.warning("Copernicus: no se pudo instalar la guardia anti-prompt (otra versión del toolbox); "
                    "las descargas exigirán credenciales verificadas como válidas.")


def guardia_instalada() -> bool:
    return _guardia_instalada


def importar():
    """Importa `copernicusmarine` una sola vez (perezoso) con tiempos de espera acotados y la guardia."""
    global _modulo, _guardia_instalada
    with _bloqueo_import:
        if _modulo is not None:
            return _modulo
        if not paquete_instalado():
            raise ErrorSinAcceso(MENSAJE_NO_INSTALADO)
        os.environ.setdefault("COPERNICUSMARINE_HTTPS_TIMEOUT", "20")
        os.environ.setdefault("COPERNICUSMARINE_HTTPS_RETRIES", "2")
        try:
            import copernicusmarine as cm   # noqa: PLC0415 — importación perezosa a propósito
        except Exception as e:  # noqa: BLE001
            raise ErrorSinAcceso(f"No se pudo importar el paquete copernicusmarine ({type(e).__name__})") from e
        logging.getLogger("copernicusmarine").setLevel(logging.WARNING)
        _instalar_guardia(cm)
        _modulo = cm
        return cm


# ---------------------------------------------------------------------------
# Credenciales (sin leer su contenido)
# ---------------------------------------------------------------------------
def rutas_credenciales() -> list[Path]:
    """Archivos donde el *toolbox* busca las credenciales (mismo orden que copernicusmarine 2.x)."""
    base = os.environ.get("COPERNICUSMARINE_CREDENTIALS_DIRECTORY") or ""
    inicio = Path(base) if base else Path.home()
    return [inicio / ".copernicusmarine" / ".copernicusmarine-credentials",
            Path.home() / "motuclient" / "motuclient-python.ini",
            Path.home() / ("_netrc" if os.name == "nt" else ".netrc")]


def _variables_entorno() -> bool:
    return bool(os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME")
                and os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD"))


def credenciales_presentes() -> bool:
    """True si hay variables de entorno o un archivo de credenciales (sin leerlo ni tocar la red)."""
    if _variables_entorno():
        return True
    for p in rutas_credenciales():
        try:
            if p.is_file():
                return True
        except OSError:
            continue
    return False


def credenciales_propias() -> bool:
    """True si las credenciales vienen de «copernicusmarine login» o de las variables de entorno
    (un _netrc o un motuclient-python.ini pueden no tener una cuenta de Copernicus Marine)."""
    if _variables_entorno():
        return True
    try:
        return rutas_credenciales()[0].is_file()
    except OSError:
        return False


def firma_credenciales() -> str:
    """Huella de dónde están las credenciales (existencia, tamaño y mtime; nunca su contenido).

    Cambia si el usuario vuelve a ejecutar «copernicusmarine login»: una verificación de credenciales
    inválidas deja de valer sin reiniciar el servidor."""
    partes = ["env" if _variables_entorno() else "-"]
    for p in rutas_credenciales():
        try:
            st = p.stat()
            partes.append(f"{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            partes.append("-")
    return "|".join(partes)


# ---------------------------------------------------------------------------
# Traducción de errores (nunca incluye credenciales; se quitan las URL)
# ---------------------------------------------------------------------------
_ERRORES_RED = ("ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "MaxRetryError", "SSLError",
                "ProxyError", "NewConnectionError", "ClientConnectorError", "ServerDisconnected",
                "ConnectionResetError", "ConnectionRefusedError", "gaierror", "RemoteDisconnected",
                "ChunkedEncodingError", "IncompleteRead", "HTTPError", "EndpointConnectionError",
                "ReadTimeoutError", "ClientError", "TimeoutError", "socket.timeout")
_ERRORES_FORMATO = ("DatasetNotFound", "DatasetVersionNotFound", "VariableDoesNotExistInTheDataset",
                    "CoordinatesOutOfDatasetBounds", "ServiceNotSupported", "ServiceDoesNotExistForCommand",
                    "NoServiceAvailable", "ServiceNotAvailable", "DatasetVersionPartNotFound",
                    "WrongDatetimeFormat", "MinimumLongitudeGreaterThanMaximumLongitude")


def limpiar(texto) -> str:
    """Texto de error apto para mostrar y guardar: sin URL ni parámetros de consulta."""
    texto = " ".join(str(texto or "").split())
    texto = re.sub(r"https?://\S+", "(URL omitida)", texto)
    texto = re.sub(r"(?i)(token|signature|password|passwd|pwd|secret)=\S+", r"\1=…", texto)
    return texto[:400]


def _nombres_clase(e: BaseException) -> list[str]:
    nombres = [type(e).__name__]
    causa = e.__cause__ or e.__context__
    profundidad = 0
    while causa is not None and profundidad < 5:
        nombres.append(type(causa).__name__)
        causa = causa.__cause__ or causa.__context__
        profundidad += 1
    return nombres


def traducir(e: BaseException) -> ErrorCopernicus:
    """Convierte una excepción del *toolbox*, de requests, de s3/zarr o de xarray en un error del SOME."""
    if isinstance(e, ErrorCopernicus):
        return e
    nombres = _nombres_clase(e)
    detalle = limpiar(e)
    if "InvalidUsernameOrPassword" in nombres:
        err = ErrorSinAcceso("Las credenciales de Copernicus Marine no son válidas: vuelva a ejecutar "
                             "«copernicusmarine login --force-overwrite»")
        err.credenciales_invalidas = True
        return err
    if "CredentialsCannotBeNone" in nombres:
        return ErrorSinCredenciales(MENSAJE_SIN_CREDENCIALES)
    if "CouldNotConnectToAuthenticationSystem" in nombres:
        err = ErrorSinAcceso("No se pudo conectar con el sistema de autenticación de Copernicus Marine en "
                             "auth.marine.copernicus.eu")
        err.es_red = True
        return err
    if any(n in _ERRORES_FORMATO for n in nombres):
        return ErrorFormato(f"Copernicus Marine respondió con un formato inesperado ({nombres[0]}: {detalle})")
    texto = detalle.lower()
    if (any(n in _ERRORES_RED for n in nombres) or "timed out" in texto or "timeout" in texto
            or "connection" in texto or "name resolution" in texto or "getaddrinfo" in texto):
        motivo = ("tiempo de espera agotado" if ("timeout" in texto or "timed out" in texto)
                  else "no se pudo resolver el nombre del servidor" if ("name resolution" in texto
                                                                       or "getaddrinfo" in texto)
                  else "sin conexión")
        err = ErrorSinAcceso(f"No se pudo conectar con Copernicus Marine ({motivo})")
        err.es_red = True
        return err
    return ErrorCopernicus(f"Error inesperado de Copernicus Marine ({nombres[0]}: {detalle})")


# ---------------------------------------------------------------------------
# Operaciones con red
# ---------------------------------------------------------------------------
HOSTS_ALCANCE = ("s3.waw3-1.cloudferro.com", "auth.marine.copernicus.eu")
_alcance: tuple[float, bool] | None = None


def destinos_alcance() -> list[tuple[str, int]]:
    """(host, puerto) que prueba `red_alcanzable`: el puerto 443 de cada host de Copernicus Marine o, si un
    proxy HTTPS lo intercepta (HTTPS_PROXY o ALL_PROXY, la configuración de Windows, NO_PROXY), el proxy:
    es por donde irá el toolbox, y un proxy que no responde debe fallar aquí en segundos y no en sus
    reintentos (~27 s)."""
    import urllib.parse    # noqa: PLC0415
    import urllib.request  # noqa: PLC0415
    try:
        proxies = urllib.request.getproxies()
    except Exception:  # noqa: BLE001 — sin configuración legible, conexión directa
        proxies = {}
    proxy = (proxies.get("https") or proxies.get("all") or "").strip()
    destinos: list[tuple[str, int]] = []
    for host in HOSTS_ALCANCE:
        destino = (host, 443)
        try:
            usa_proxy = bool(proxy) and not urllib.request.proxy_bypass(host)
        except Exception:  # noqa: BLE001
            usa_proxy = bool(proxy)
        if usa_proxy:
            partes = urllib.parse.urlsplit(proxy if "://" in proxy else f"http://{proxy}")
            try:
                puerto = partes.port
            except ValueError:
                puerto = None
            if partes.hostname:
                defecto = 443 if partes.scheme == "https" else 1080 if partes.scheme.startswith("socks") else 80
                destino = (partes.hostname, puerto or defecto)
        if destino not in destinos:
            destinos.append(destino)
    return destinos


def red_alcanzable(espera_s: float = 3.0) -> bool:
    """Prueba rápida (conexión TCP al puerto 443 del almacén ARCO o del sistema de autenticación, o al proxy
    HTTPS si hay uno configurado; resultado recordado 60 s) para que una consulta sin internet no espere los
    tiempos del toolbox. True si alguno responde; en modo sin red, False sin intentarlo."""
    global _alcance
    if sin_red():
        return False
    ahora = time.monotonic()
    if _alcance is not None and ahora - _alcance[0] < 60:
        return _alcance[1]
    import socket          # noqa: PLC0415
    ok = False
    for destino in destinos_alcance():
        try:
            with socket.create_connection(destino, timeout=espera_s):
                ok = True
                break
        except OSError:
            continue
    _alcance = (time.monotonic(), ok)
    return ok


def _exigir_red() -> None:
    if sin_red():
        raise ErrorSinAcceso(MENSAJE_SIN_RED)


def verificar_credenciales() -> tuple[bool | None, int, str]:
    """(True | False | None, latencia_ms, error) con `login(check_credentials_valid=True)` (nunca pregunta).

    True = válidas; False = inválidas (o no encontradas); None = no se pudo verificar (sin red, sin
    paquete o sin conexión con el sistema de autenticación). Nunca registra el nombre de usuario.
    """
    if sin_red():
        return None, 0, MENSAJE_SIN_RED
    if not credenciales_presentes():
        return False, 0, MENSAJE_SIN_CREDENCIALES
    try:
        modulo = importar()
    except ErrorCopernicus as e:
        return None, 0, str(e)
    t0 = time.perf_counter()
    try:
        valido = bool(modulo.login(check_credentials_valid=True))
        return valido, int((time.perf_counter() - t0) * 1000), "" if valido else (
            "Las credenciales de Copernicus Marine no son válidas")
    except ErrorSinCredenciales as e:
        return False, int((time.perf_counter() - t0) * 1000), str(e)
    except Exception as e:  # noqa: BLE001
        return None, int((time.perf_counter() - t0) * 1000), str(traducir(e))


def _a_fecha_ms(valor) -> date | None:
    try:
        return datetime.fromtimestamp(float(valor) / 1000.0, tz=timezone.utc).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def metadatos(dataset_id: str) -> dict:
    """Metadatos del dataset con `describe` (público, sin cuenta): versión, eje de tiempo, actualización
    de ARCO y geometría de los fragmentos por servicio. Lanza ErrorCopernicus si falla."""
    _exigir_red()
    modulo = importar()
    t0 = time.perf_counter()
    try:
        catalogo = modulo.describe(dataset_id=dataset_id, disable_progress_bar=True)
    except Exception as e:  # noqa: BLE001
        raise traducir(e) from e
    latencia = int((time.perf_counter() - t0) * 1000)
    try:
        dataset = None
        for producto in catalogo.products:
            for d in producto.datasets:
                if d.dataset_id == dataset_id:
                    dataset = d
        if dataset is None:
            raise ErrorFormato(f"El catálogo de Copernicus Marine no describe el dataset {dataset_id}.")
        version = dataset.versions[-1]
        parte = version.parts[0]
        geometria, fin, inicio = {}, None, None
        for servicio in parte.services:
            nombre = str(servicio.service_name)
            if not nombre.startswith("arco") or not servicio.variables:
                continue
            ejes = {}
            for c in servicio.variables[0].coordinates:
                ejes[str(c.coordinate_id)] = int(c.chunking_length) if c.chunking_length else None
                if c.coordinate_id == "time":
                    fin = _a_fecha_ms(c.maximum_value) or fin
                    inicio = _a_fecha_ms(c.minimum_value) or inicio
                    if (fin is None or inicio is None) and getattr(c, "values", None):
                        fin = _a_fecha_ms(max(c.values))
                        inicio = _a_fecha_ms(min(c.values))
            geometria[nombre] = ejes
        return {"dataset_id": dataset_id, "version": str(version.label),
                "doi": str(getattr(dataset, "digital_object_identifier", "") or ""),
                "fin_tiempo": fin.isoformat() if fin else None,
                "inicio_tiempo": inicio.isoformat() if inicio else None,
                "arco_updated_date": str(parte.arco_updated_date or "") or None,
                "arco_updating_start_date": str(parte.arco_updating_start_date or "") or None,
                "geometria": geometria, "latencia_ms": latencia,
                "url_metadatos": str(getattr(parte, "url_metadata", "") or "") or None}
    except ErrorCopernicus:
        raise
    except Exception as e:  # noqa: BLE001
        raise ErrorFormato(f"Metadatos de {dataset_id} con una forma inesperada ({type(e).__name__}).") from e


def leer_ventana(dataset_id: str, variables, servicio: str, lat: tuple[float, float], lon: tuple[float, float],
                 inicio: date | None, fin: date | None, niveles_nativos=None):
    """`open_dataset` perezoso sobre ARCO con recorte de área, días y servicio explícito; selección de los
    niveles nativos necesarios ANTES de cargar; `.load()`. Devuelve el `xarray.Dataset` en memoria.

    Nunca pasa usuario ni contraseña: el *toolbox* usa las credenciales guardadas por el usuario (o lanza
    ErrorSinCredenciales a través de la guardia anti-prompt).

    `chunk_size_limit=1`: los bloques de dask coinciden con los fragmentos ARCO. Con el valor por defecto
    (-1) el *toolbox* agrupa hasta 10 niveles por bloque de dask y, al elegir 1 nivel, se descargan los 10
    (medido el 30/10/2026: un día de thetao a 0 m en toda el área bajaba 46 MB en lugar de ~5 MB).
    """
    _exigir_red()
    if not credenciales_presentes():
        raise ErrorSinCredenciales(MENSAJE_SIN_CREDENCIALES)
    modulo = importar()
    argumentos = {"dataset_id": dataset_id, "variables": list(variables),
                  "minimum_latitude": float(lat[0]), "maximum_latitude": float(lat[1]),
                  "minimum_longitude": float(lon[0]), "maximum_longitude": float(lon[1]),
                  "coordinates_selection_method": "inside", "service": servicio, "chunk_size_limit": 1}
    if inicio is not None:
        argumentos["start_datetime"] = f"{inicio.isoformat()}T00:00:00"
    if fin is not None:
        argumentos["end_datetime"] = f"{fin.isoformat()}T00:00:00"
    try:
        ds = modulo.open_dataset(**argumentos)
        if niveles_nativos:
            if "depth" not in ds.dims:
                raise ErrorFormato(f"El dataset {dataset_id} no tiene la dimensión de profundidad esperada.")
            try:
                ds = ds.sel(depth=[float(n) for n in niveles_nativos], method="nearest", tolerance=TOLERANCIA_NIVEL)
            except KeyError as e:
                raise ErrorFormato(f"Faltan niveles del modelo en {dataset_id} ({limpiar(e)}).") from e
        return ds.load()
    except ErrorCopernicus:
        raise
    except Exception as e:  # noqa: BLE001
        raise traducir(e) from e
