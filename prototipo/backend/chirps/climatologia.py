"""Climatología mensual real de CHIRPS v3 (mm/día) en la malla SOME, preparada año por año.

Cada año se guarda en `datos/chirps/mensual/AAAA.npz` (12, 49, 69) con
`almacen.guardar_mensual`. Hay dos fuentes, por orden de preferencia:

1. **Réplica dynamical.org** (`espejo.leer_mensual_anio`, fuente principal):
   media mensual calculada con el diario FINAL a 0.05° de la réplica Icechunk
   de CHIRPS v3 en S3 (por celda: suma de los días con dato / días con dato,
   exigiendo el 80 % de los días del mes), agregada 5×5 y reproyectada a la
   malla SOME. Unos 3 s por año.
2. **NetCDF mensual global del CHC** (fuente secundaria, si la réplica no está
   disponible o falla): por cada año se abre `chirps-v3.0.AAAA.monthly.nc` en
   remoto con h5py sobre un objeto archivo propio (`_ArchivoRemotoCHC`) cuyas
   lecturas son peticiones `red.rango` en bloques de 2 MiB con caché: así los
   metadatos y los fragmentos pasan por el mismo ritmo global (1 pet./s entre
   procesos), el disyuntor, el User-Agent y el contador de bytes. Todo el año se
   descarga bajo `red.un_archivo()` (un archivo del CHC a la vez, también frente
   a las descargas diarias). Se calculan los índices del recorte a partir de las
   coordenadas, se bajan solo los fragmentos HDF5 que cubren el área y se
   descomprimen; luego se agrega 5×5, se reproyecta y se pasa de mm/mes a mm/día
   dividiendo por los días de cada mes de ese año. Si el CHC está marcado caído
   (disyuntor o último contacto fallido) no se le pide nada, y en `preparar_anios`
   el primer fallo del CHC detiene el resto de los años.

Ambas siguen el mismo orden (media mensual a 0.05° → 5×5 → malla SOME), así
que son intercambiables. El origen de cada año queda registrado en
`datos/chirps/mensual/origen.json` para poder citarlo (`origen_anio`,
`resumen_origen`, `texto_origen_periodo`).

Concurrencia: cada año se prepara bajo un bloqueo por año (entre hilos y
procesos) que vuelve a mirar la caché al entrar, así que la sincronización,
las consultas y el script de preparación no calculan dos veces el mismo año.
Una consulta prepara en el momento como mucho `MAX_ANIOS_CLIM_EN_PETICION`
años; si faltan más, lanza la preparación en segundo plano y responde que la
climatología se está preparando (503).
"""

from __future__ import annotations

import calendar
import io
import json
import logging
import os
import tempfile
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable

import numpy as np

from . import almacen, espejo, red
from .bloqueo import bloqueo as _bloqueo_procesos
from .constantes import (ANIO_CLIM_MAX, ANIO_CLIM_MIN, LAT_NORTE, LAT_SUR, LON_ESTE, LON_OESTE,
                         MAX_ANIOS_CLIM_EN_PETICION, URL_MENSUAL_ANIO, ErrorCHIRPS, sin_red)
from .lector_tiff import limpiar
from .malla import agregar_5x5, reproyectar_a_some

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Origen de cada año
# ---------------------------------------------------------------------------
ORIGEN_REPLICA = "replica"
ORIGEN_CHC = "chc"
TEXTO_ORIGEN = {
    ORIGEN_REPLICA: espejo.TEXTO_ORIGEN,
    ORIGEN_CHC: "NetCDF mensual de CHIRPS v3 del CHC (data.chc.ucsb.edu)",
}
TEXTO_METODO = {
    ORIGEN_REPLICA: ("media mensual del diario final a 0.05° de la réplica dynamical.org de CHIRPS v3 "
                     "(por celda, suma de los días con dato / días con dato; mínimo 80 % de los días), "
                     "agregada a 0.25° (bloques de 5×5, mínimo 13 de 25 válidas) y reproyectada a la "
                     "malla SOME (media de 4 celdas, mínimo 2)"),
    ORIGEN_CHC: ("total mensual global a 0.05° de CHIRPS v3 (NetCDF del CHC), agregado a 0.25° "
                 "(bloques de 5×5, mínimo 13 de 25 válidas), reproyectado a la malla SOME (media de 4 "
                 "celdas, mínimo 2) y convertido a mm/día dividiendo por los días de cada mes"),
}

_TRABAJADORES_REPLICA = 2   # años en paralelo desde la réplica (zarr ya paraleliza cada lectura)
_TRABAJADORES_CHC = 1       # el CHC pide un archivo a la vez: fragmentos en serie

_bloqueo_h5 = threading.Lock()     # h5py serializa internamente; evitamos aperturas simultáneas
_bloqueo_origen = threading.Lock()
_origen_cache: tuple[tuple[str, int], dict] | None = None
ESPERA_ANIO_S = 600                # un año tarda ~3 s (réplica) o ~1 min (CHC); margen amplio
ESPERA_ARCHIVO_CHC_S = 300


class ClimatologiaEnPreparacion(ErrorCHIRPS):
    """Faltan demasiados años para prepararlos dentro de una consulta: se preparan en segundo plano."""


def ruta_origen():
    """`datos/chirps/mensual/origen.json` (sigue a `almacen.RUTA_MENSUAL`, que las pruebas redirigen)."""
    return almacen.RUTA_MENSUAL / "origen.json"


def _mtime_origen() -> int:
    try:
        return ruta_origen().stat().st_mtime_ns
    except OSError:
        return -1


def _leer_origen() -> dict:
    """Contenido de origen.json ({"AAAA": {...}}); vacío si no existe o está dañado. Con el bloqueo."""
    global _origen_cache
    mt = _mtime_origen()
    if _origen_cache is not None and _origen_cache[0] == (str(ruta_origen()), mt):
        return _origen_cache[1]
    datos: dict = {}
    if mt >= 0:
        try:
            with open(ruta_origen(), encoding="utf-8") as f:
                cargado = json.load(f)
            if isinstance(cargado, dict):
                datos = cargado
        except (OSError, json.JSONDecodeError):
            datos = {}
    _origen_cache = ((str(ruta_origen()), mt), datos)
    return datos


def _registrar_origen(anio: int, origen: str) -> None:
    """Anota (escritura atómica, bajo un bloqueo entre procesos) de qué fuente salió el año guardado."""
    global _origen_cache
    with _bloqueo_origen, _bloqueo_procesos(almacen.ruta_bloqueos() / "origen_mensual.lock", 60):
        _origen_cache = None                     # releer del disco bajo el bloqueo entre procesos
        datos = dict(_leer_origen())
        datos[f"{anio:04d}"] = {"origen": origen, "texto": TEXTO_ORIGEN[origen],
                                "obtenido": datetime.now().isoformat(timespec="seconds")}
        ruta = ruta_origen()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="origen.", suffix=".tmp", dir=ruta.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(dict(sorted(datos.items())), f, ensure_ascii=False, indent=1)
            for intento in range(5):
                try:
                    os.replace(tmp, ruta)
                    break
                except PermissionError:          # Windows: destino abierto por otro lector
                    if intento == 4:
                        raise
                    time.sleep(0.2 * (intento + 1))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        _origen_cache = ((str(ruta), _mtime_origen()), datos)


def origen_anio(anio: int) -> dict | None:
    """{"origen": "replica"|"chc", "texto", "obtenido", "inferido"} del año en caché, o None.

    Un año guardado antes de existir el registro solo pudo salir del NetCDF del
    CHC: se informa como "chc" con "inferido": True.
    """
    if almacen.leer_mensual(anio) is None:
        return None
    with _bloqueo_origen:
        reg = _leer_origen().get(f"{anio:04d}")
    if isinstance(reg, dict) and reg.get("origen") in TEXTO_ORIGEN:
        return {"origen": reg["origen"], "texto": TEXTO_ORIGEN[reg["origen"]],
                "obtenido": reg.get("obtenido"), "inferido": False}
    return {"origen": ORIGEN_CHC, "texto": TEXTO_ORIGEN[ORIGEN_CHC], "obtenido": None, "inferido": True}


def resumen_origen(anio_ini: int = ANIO_CLIM_MIN, anio_fin: int = ANIO_CLIM_MAX) -> dict:
    """Cuántos años del periodo salieron de cada fuente: {"replica": n, "chc": m, "faltan": k, "anios": {...}}."""
    cuenta = {ORIGEN_REPLICA: 0, ORIGEN_CHC: 0, "faltan": 0}
    por_anio: dict[int, str | None] = {}
    for a in range(anio_ini, anio_fin + 1):
        o = origen_anio(a)
        por_anio[a] = o["origen"] if o else None
        cuenta[o["origen"] if o else "faltan"] += 1
    return {**cuenta, "anios": por_anio}


def texto_origen_periodo(anio_ini: int, anio_fin: int) -> str:
    """Frase para citar el origen de la climatología del periodo (atributos, informes)."""
    r = resumen_origen(anio_ini, anio_fin)
    n = anio_fin - anio_ini + 1
    articulo = {ORIGEN_REPLICA: ("la", "de la"), ORIGEN_CHC: ("el", "del")}
    partes = []
    for origen in (ORIGEN_REPLICA, ORIGEN_CHC):
        if r[origen] == n:
            return (f"Climatología {anio_ini}-{anio_fin} calculada con {articulo[origen][0]} "
                    f"{TEXTO_ORIGEN[origen]}")
        if r[origen]:
            anios = [str(a) for a, o in r["anios"].items() if o == origen]
            if len(anios) == 1:
                lista = anios[0]
            elif len(anios) <= 6:
                lista = ", ".join(anios[:-1]) + " y " + anios[-1]
            else:
                lista = f"entre {anios[0]} y {anios[-1]}"
            partes.append(f"{r[origen]} {'año' if r[origen] == 1 else 'años'} {articulo[origen][1]} "
                          f"{TEXTO_ORIGEN[origen]}, {lista}")
    if r["faltan"]:
        partes.append(f"{r['faltan']} {'año' if r['faltan'] == 1 else 'años'} aún sin preparar")
    return f"Climatología {anio_ini}-{anio_fin}: " + "; ".join(partes)


# ---------------------------------------------------------------------------
# Fuente secundaria: NetCDF mensual del CHC
# ---------------------------------------------------------------------------
def _indices(coord: np.ndarray, minimo: float, maximo: float) -> tuple[int, int, bool]:
    """[i0, i1) de las celdas cuyo centro cae en (minimo, maximo) y si el eje es ascendente."""
    dentro = np.nonzero((coord > minimo + 1e-4) & (coord < maximo - 1e-4))[0]
    if dentro.size == 0:
        raise ErrorCHIRPS("El recorte del área de estudio no cae dentro de la malla del NetCDF mensual")
    return int(dentro[0]), int(dentro[-1]) + 1, bool(coord[-1] > coord[0])


class _ArchivoRemotoCHC(io.RawIOBase):
    """Archivo remoto de solo lectura para h5py: cada lectura es `red.rango` en bloques alineados de
    `BLOQUE` bytes con caché. Todo pasa por `red` (ritmo global entre procesos, disyuntor,
    User-Agent con contacto, contador de bytes, mensajes en español); no hay HEAD: el tamaño sale
    de la cabecera Content-Range de la primera respuesta. Si una lectura falla, el error original
    queda en `error` (h5py lo envuelve en un OSError genérico)."""

    BLOQUE = 2 * 1024 * 1024

    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.pos = 0
        self.error: BaseException | None = None
        self._bloques: dict[int, bytes] = {}
        self.tamano = None
        self._bloque(0)                            # abre: primer bloque y tamaño total
        if self.tamano is None:
            raise ErrorCHIRPS(f"El servidor del CHC no informó el tamaño de {url}")

    def _bloque(self, i: int) -> bytes:
        if i not in self._bloques:
            ini = i * self.BLOQUE
            fin = ini + self.BLOQUE - 1
            if self.tamano is not None:
                fin = min(fin, self.tamano - 1)
                if fin < ini:
                    return b""
            try:
                datos, total = red.rango_con_total(self.url, ini, fin)
            except BaseException as e:
                self.error = e
                raise
            if total is not None:
                self.tamano = total
            self._bloques[i] = datos
        return self._bloques[i]

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, desplazamiento: int, desde: int = io.SEEK_SET) -> int:
        if desde == io.SEEK_SET:
            self.pos = int(desplazamiento)
        elif desde == io.SEEK_CUR:
            self.pos += int(desplazamiento)
        elif desde == io.SEEK_END:
            self.pos = int(self.tamano) + int(desplazamiento)
        else:
            raise ValueError(f"Origen de desplazamiento no válido: {desde}")
        return self.pos

    def readinto(self, b) -> int:
        vista = memoryview(b).cast("B")
        n = min(len(vista), max(0, int(self.tamano) - self.pos))
        hecho = 0
        while hecho < n:
            i, off = divmod(self.pos + hecho, self.BLOQUE)
            trozo = self._bloque(i)[off:off + n - hecho]
            if not trozo:
                break
            vista[hecho:hecho + len(trozo)] = trozo
            hecho += len(trozo)
        self.pos += hecho
        return hecho


def _como_error_chc(e: BaseException, url: str) -> ErrorCHIRPS:
    """Convierte un fallo al abrir o leer el NetCDF del CHC en un error de `red` y lo cuenta en el
    disyuntor si aún no pasó por él (cualquier fallo de la apertura remota es un fallo del CHC)."""
    if isinstance(e, ErrorCHIRPS):
        return e
    motivo = red.texto_error(e)
    log.info("CHIRPS: fallo al abrir el NetCDF mensual del CHC (%s): %r", url, e)
    red._registrar_fallo(f"NetCDF mensual del CHC: {motivo}")
    return red.ErrorConexion(f"No se pudo abrir el NetCDF mensual del CHC: {motivo}.")


def _error_lectura_h5(f: "_ArchivoRemotoCHC", e: BaseException, url: str) -> ErrorCHIRPS:
    """Error de h5py al interpretar el archivo remoto. Si falló una lectura remota (`f.error`), es un
    fallo del CHC (ya contado por `red`); si todos los bytes llegaron bien, el servidor respondió pero
    el contenido no es el NetCDF4/HDF5 esperado: no es «sin conexión» ni cuenta para el disyuntor."""
    if f.error is not None:
        return _como_error_chc(f.error, url)
    log.info("CHIRPS: el NetCDF mensual del CHC no tiene el formato esperado (%s): %r", url, e)
    return ErrorCHIRPS("El servidor del CHC respondió, pero el NetCDF mensual no tiene el formato "
                       "esperado (NetCDF4/HDF5).")


def leer_recorte_mensual(anio: int, espera_archivo: float | None = ESPERA_ARCHIVO_CHC_S) -> np.ndarray:
    """(n_meses, 250, 350) mm/mes a 0.05°, latitud ascendente, NaN sin dato (NetCDF del CHC).

    Todo el año (metadatos y fragmentos) se baja con `red` y bajo `red.un_archivo()`.
    """
    if sin_red():
        raise ErrorCHIRPS("Modo sin red activo (SOME_CHIRPS_SIN_RED=1): no se descarga la climatología.")
    abierto, hasta, motivo = red.info_disyuntor()
    if abierto:
        raise red.ErrorDisyuntor(f"El CHC está marcado como caído hasta las "
                                 f"{hasta.strftime('%H:%M') if hasta else '—'} ({motivo}); no se descarga "
                                 "el NetCDF mensual.")
    url = URL_MENSUAL_ANIO.format(anio=anio)
    with red.un_archivo(espera_archivo):
        return _leer_recorte_mensual_url(url)


def _leer_recorte_mensual_url(url: str) -> np.ndarray:
    import h5py

    with _bloqueo_h5:
        try:
            f = _ArchivoRemotoCHC(url)
        except BaseException as e:  # noqa: BLE001
            raise _como_error_chc(e, url) from e
        try:
            h = h5py.File(f, "r")
        except BaseException as e:  # noqa: BLE001 — h5py envuelve el error de la lectura remota
            raise _error_lectura_h5(f, e, url) from e
        with h:
            lat = h["latitude"][:].astype(np.float64)
            lon = h["longitude"][:].astype(np.float64)
            f0, f1, asc_lat = _indices(lat, LAT_SUR, LAT_NORTE)
            c0, c1, asc_lon = _indices(lon, LON_OESTE, LON_ESTE)
            if not asc_lon:
                raise ErrorCHIRPS("Longitud descendente no soportada en el NetCDF mensual")
            ds = h["precip"]
            nt = ds.shape[0]
            sin_dato = float(np.asarray(ds.attrs.get("_FillValue", [-9999.0])).ravel()[0])
            directo = (ds.chunks is not None and ds.compression in ("gzip", None) and not ds.shuffle
                       and not ds.fletcher32 and ds.scaleoffset is None)
            if directo:
                ct, cy, cx = ds.chunks
                piezas = []
                for t0 in range(0, nt, ct):
                    for y0 in range((f0 // cy) * cy, f1, cy):
                        for x0 in range((c0 // cx) * cx, c1, cx):
                            info = ds.id.get_chunk_info_by_coord((t0, y0, x0))
                            piezas.append(((t0, y0, x0), info.byte_offset, info.size, info.filter_mask))
                gzip_activo = ds.compression == "gzip"
                tipo = ds.dtype
                fill = ds.fillvalue
            else:
                try:
                    datos = ds[:, f0:f1, c0:c1].astype(np.float32)   # vía el archivo remoto (más lento)
                except BaseException as e:  # noqa: BLE001
                    raise _error_lectura_h5(f, e, url) from e

    if directo:
        salida = np.full((nt, f1 - f0, c1 - c0), np.nan, dtype=np.float32)

        def bajar(pieza):
            (t0, y0, x0), off, tam, mascara = pieza
            if off is None or tam == 0:
                bloque = np.full((ct, cy, cx), fill, dtype=tipo)
            else:
                crudo = red.rango(url, int(off), int(off) + int(tam) - 1)
                if gzip_activo and not (mascara & 1):
                    crudo = zlib.decompress(crudo)
                bloque = np.frombuffer(crudo, dtype=tipo).reshape(ct, cy, cx)
            return (t0, y0, x0), bloque

        with ThreadPoolExecutor(max_workers=_TRABAJADORES_CHC, thread_name_prefix="chirps-clim") as ex:
            for (t0, y0, x0), bloque in ex.map(bajar, piezas):
                ty = slice(max(f0, y0), min(f1, y0 + cy))
                tx = slice(max(c0, x0), min(c1, x0 + cx))
                tt = slice(t0, min(nt, t0 + ct))
                salida[tt, ty.start - f0:ty.stop - f0, tx.start - c0:tx.stop - c0] = \
                    bloque[:tt.stop - t0, ty.start - y0:ty.stop - y0, tx.start - x0:tx.stop - x0]
        datos = salida
    if not asc_lat:
        datos = datos[:, ::-1, :]
    if datos.shape[1:] != (250, 350):
        raise ErrorCHIRPS(f"Recorte mensual inesperado {datos.shape} en {url}")
    return limpiar(datos, sin_dato)


def mensual_en_malla_some(anio: int) -> np.ndarray:
    """(n_meses, 49, 69) en mm/mes sobre la malla SOME desde el NetCDF del CHC (validaciones)."""
    return reproyectar_a_some(agregar_5x5(leer_recorte_mensual(anio)))


def mensual_chc_mm_dia(anio: int) -> np.ndarray:
    """(12, 49, 69) en mm/día desde el NetCDF mensual del CHC (sin guardar)."""
    mm_mes = mensual_en_malla_some(anio)
    if mm_mes.shape[0] != 12:
        raise ErrorCHIRPS(f"El NetCDF mensual de {anio} tiene {mm_mes.shape[0]} meses (se esperaban 12)")
    dias = np.array([calendar.monthrange(anio, m)[1] for m in range(1, 13)], dtype=np.float32)
    return (mm_mes / dias[:, None, None]).astype(np.float32)


# ---------------------------------------------------------------------------
# Preparación de años
# ---------------------------------------------------------------------------
def _validar_anio(anio: int, mm_dia: np.ndarray, origen: str) -> np.ndarray:
    mm_dia = np.asarray(mm_dia, dtype=np.float32)
    if mm_dia.shape != (12, almacen.NLAT, almacen.NLON):
        raise ErrorCHIRPS(f"Climatología de {anio} con forma {mm_dia.shape} ({TEXTO_ORIGEN[origen]})")
    vacios = [m + 1 for m in range(12) if not np.isfinite(mm_dia[m]).any()]
    if vacios:
        raise ErrorCHIRPS(f"La climatología de {anio} ({TEXTO_ORIGEN[origen]}) no tiene datos en "
                          f"{'el mes' if len(vacios) == 1 else 'los meses'} {', '.join(map(str, vacios))}")
    return mm_dia


def _guardar_anio(anio: int, mm_dia: np.ndarray, origen: str) -> np.ndarray:
    mm_dia = _validar_anio(anio, mm_dia, origen)
    almacen.guardar_mensual(anio, mm_dia)
    _registrar_origen(anio, origen)
    return mm_dia


def _bloqueo_anio(anio: int):
    """Bloqueo del año (entre hilos y procesos): nadie calcula a la vez el mismo año."""
    return _bloqueo_procesos(almacen.ruta_bloqueos() / f"climatologia-{int(anio):04d}.lock", ESPERA_ANIO_S)


def preparar_anio_replica(anio: int, rehacer: bool = False) -> np.ndarray:
    """Calcula el año con la réplica dynamical.org, lo guarda y registra el origen. Lanza ErrorEspejo.

    Bajo el bloqueo del año vuelve a mirar la caché: si otro hilo o proceso ya lo preparó, lo
    devuelve sin descargar nada (salvo `rehacer`)."""
    with _bloqueo_anio(anio):
        if not rehacer:
            ya = almacen.leer_mensual(anio)
            if ya is not None:
                return ya
        return _guardar_anio(anio, espejo.leer_mensual_anio(anio), ORIGEN_REPLICA)


def preparar_anio_chc(anio: int, rehacer: bool = False) -> np.ndarray:
    """Descarga el año del NetCDF mensual del CHC, lo guarda y registra el origen. Lanza ErrorCHIRPS.
    Mismo bloqueo por año y misma comprobación de la caché que `preparar_anio_replica`."""
    with _bloqueo_anio(anio):
        if not rehacer:
            ya = almacen.leer_mensual(anio)
            if ya is not None:
                return ya
        return _guardar_anio(anio, mensual_chc_mm_dia(anio), ORIGEN_CHC)


def preparar_anio(anio: int, fuente: str = "auto") -> np.ndarray:
    """Prepara y guarda el año en la caché (12, 49, 69) mm/día.

    `fuente`: "auto" (réplica y, si falla, el CHC), "replica" o "chc".
    """
    if fuente not in ("auto", ORIGEN_REPLICA, ORIGEN_CHC):
        raise ValueError(f"Fuente de climatología desconocida: «{fuente}»")
    error_replica: Exception | None = None
    if fuente in ("auto", ORIGEN_REPLICA):
        try:
            return preparar_anio_replica(anio)
        except ErrorCHIRPS as e:
            if fuente == ORIGEN_REPLICA:
                raise
            error_replica = e
    try:
        return preparar_anio_chc(anio)
    except ErrorCHIRPS as e:
        if error_replica is not None:
            raise ErrorCHIRPS(f"{e} (réplica: {error_replica})") from e
        raise


def anios_faltantes(anio_ini: int = ANIO_CLIM_MIN, anio_fin: int = ANIO_CLIM_MAX) -> list[int]:
    """Años del periodo que no se pueden leer de la caché (ausentes o dañados: los dañados se apartan)."""
    return [a for a in range(anio_ini, anio_fin + 1) if almacen.leer_mensual(a) is None]


def _chc_caido() -> str:
    """Motivo si el CHC está marcado caído (disyuntor de este u otro proceso, fallo reciente o último
    contacto conocido fallido); "" si se le puede pedir algo."""
    if red.disyuntor_abierto():
        return "el disyuntor del CHC está abierto"
    if red.servidor_caido_reciente():
        return "el CHC falló hace pocos minutos"
    try:
        from . import estado
        if estado.chc_accesible() is False:
            return "el último contacto con el CHC falló"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _replica_utilizable() -> tuple[bool, str]:
    """(True, "") si la réplica responde y tiene producto final; si no, (False, motivo)."""
    if not espejo.disponible():
        return False, ("modo sin red activo" if sin_red() else "bibliotecas icechunk/zarr no instaladas")
    cob = espejo.cobertura()
    if cob["final"] is None:
        return False, cob["error"] or "sin producto final"
    return True, ""


def preparar_anios(anios: list[int], progreso: Callable[[int, int, int], None] | None = None,
                   trabajadores: int = 3) -> dict:
    """Prepara varios años: primero desde la réplica (hasta 2 en paralelo) y los que fallen, del CHC.

    Del CHC se descarga un año a la vez y, si su servidor no responde, no se
    insiste con el resto. Devuelve {"anios", "errores", "detalle_errores",
    "bytes" (solo los del CHC; la réplica no se contabiliza), "segundos",
    "anios_replica", "anios_chc"}.
    """
    t0 = time.perf_counter()
    b0 = red.bytes_descargados()
    anios = sorted(set(int(a) for a in anios))
    total = len(anios)
    errores: list[str] = []
    hechos = 0
    por_origen = {ORIGEN_REPLICA: 0, ORIGEN_CHC: 0}
    motivo_replica: dict[int, str] = {}

    def avanzar():
        if progreso:
            progreso(hechos, total, len(errores))

    pendientes = list(anios)
    if pendientes:
        utilizable, motivo = _replica_utilizable()
        if utilizable:
            n = max(1, min(int(trabajadores), _TRABAJADORES_REPLICA))
            fallidos: list[int] = []
            with ThreadPoolExecutor(max_workers=n, thread_name_prefix="chirps-anio") as ex:
                futuros = {ex.submit(preparar_anio_replica, a): a for a in pendientes}
                for fut in as_completed(futuros):
                    a = futuros[fut]
                    try:
                        fut.result()
                        por_origen[ORIGEN_REPLICA] += 1
                        hechos += 1
                        avanzar()
                    except Exception as e:  # noqa: BLE001
                        fallidos.append(a)
                        motivo_replica[a] = str(e)
            pendientes = sorted(fallidos)
        else:
            motivo_replica = {a: f"réplica no disponible: {motivo}" for a in pendientes}

    chc_caido = _chc_caido() if pendientes and not sin_red() else ""
    for a in pendientes:
        if sin_red():
            errores.append(f"{a}: modo sin red activo (réplica: {motivo_replica.get(a, '—')})")
        elif chc_caido:
            errores.append(f"{a}: no se intentó con el CHC ({chc_caido}) "
                           f"(réplica: {motivo_replica.get(a, '—')})")
        else:
            try:
                preparar_anio_chc(a)
                por_origen[ORIGEN_CHC] += 1
            except Exception as e:  # noqa: BLE001
                errores.append(f"{a}: {e} (réplica: {motivo_replica.get(a, '—')})")
                # Al primer fallo del CHC (de red, HTTP o de formato) no se insiste con el resto.
                chc_caido = "falló con el año anterior"
        hechos += 1
        avanzar()
    return {"anios": total - len(errores), "errores": len(errores), "detalle_errores": errores[:20],
            "bytes": red.bytes_descargados() - b0, "segundos": round(time.perf_counter() - t0, 1),
            "anios_replica": por_origen[ORIGEN_REPLICA], "anios_chc": por_origen[ORIGEN_CHC]}


_bloqueo_fondo = threading.Lock()
_hilo_fondo: threading.Thread | None = None


def preparar_en_segundo_plano(anios: list[int]) -> bool:
    """Lanza (si no hay otra en curso en este proceso) la preparación de `anios` en un hilo daemon.
    Devuelve True si la lanzó. Los bloqueos por año evitan repetir trabajo con la sincronización."""
    global _hilo_fondo

    def trabajo():
        try:
            r = preparar_anios(anios, trabajadores=1)
            if r["errores"]:
                from . import estado
                for e in r["detalle_errores"][:3]:
                    estado.registrar_error(f"Climatología (en segundo plano): {e}")
        except Exception:  # noqa: BLE001 — nunca tumbar el servidor
            log.exception("CHIRPS: error al preparar la climatología en segundo plano")

    with _bloqueo_fondo:
        if _hilo_fondo is not None and _hilo_fondo.is_alive():
            return False
        _hilo_fondo = threading.Thread(target=trabajo, name="chirps-climatologia", daemon=True)
        _hilo_fondo.start()
        return True


def climatologia_periodo(anio_ini: int, anio_fin: int,
                         max_preparar: int | None = MAX_ANIOS_CLIM_EN_PETICION) -> np.ndarray:
    """Promedio (12, 49, 69) en mm/día de los años [anio_ini, anio_fin].

    Si faltan años en la caché y hay red, prepara en el momento hasta `max_preparar` (réplica
    dynamical.org; si no, NetCDF del CHC, solo si no está marcado caído). Si faltan más, lanza la
    preparación en segundo plano y `ClimatologiaEnPreparacion` (la consulta responde 503 y el
    usuario reintenta en unos minutos). Si no se puede, lanza ValueError con un mensaje claro.
    """
    faltan = anios_faltantes(anio_ini, anio_fin)
    if faltan:
        if sin_red():
            raise ValueError(
                f"La climatología CHIRPS v3 {anio_ini}-{anio_fin} no está completa en la caché local "
                f"(faltan {len(faltan)} años: {', '.join(map(str, faltan[:6]))}{'…' if len(faltan) > 6 else ''}) "
                "y el modo sin red está activo. Ejecute «python -m backend.herramientas.preparar_chirps "
                "--climatologia» con conexión a internet.")
        utilizable, _motivo = _replica_utilizable()
        if not utilizable and _chc_caido():
            raise ValueError(
                f"La climatología CHIRPS v3 {anio_ini}-{anio_fin} no está completa en la caché local "
                f"(faltan {len(faltan)} años) y en este momento no responden ni la réplica dynamical.org "
                "ni el servidor del CHC. Inténtelo más tarde; la sincronización la completará "
                "automáticamente.")
        if max_preparar is not None and len(faltan) > max_preparar:
            preparar_en_segundo_plano(faltan)
            raise ClimatologiaEnPreparacion(
                f"la climatología {anio_ini}-{anio_fin} se está preparando en segundo plano (faltan "
                f"{len(faltan)} de {anio_fin - anio_ini + 1} años); inténtelo de nuevo en unos minutos.")
        r = preparar_anios(faltan, trabajadores=1)
        if r["errores"]:
            raise ValueError(
                f"No se pudo preparar la climatología CHIRPS v3 de {r['errores']} "
                f"{'año' if r['errores'] == 1 else 'años'} del periodo {anio_ini}-{anio_fin} (ni desde la "
                "réplica dynamical.org ni desde el CHC). Inténtelo más tarde o ejecute el script de "
                "preparación.")
    anuales = [almacen.leer_mensual(a) for a in range(anio_ini, anio_fin + 1)]
    perdidos = [a for a, v in zip(range(anio_ini, anio_fin + 1), anuales) if v is None]
    if perdidos:
        raise ValueError(f"La climatología CHIRPS v3 {anio_ini}-{anio_fin} no se pudo leer de la caché local "
                         f"(años {', '.join(map(str, perdidos[:6]))}); se volverá a preparar. Inténtelo más tarde.")
    pila = np.stack(anuales)
    validos = np.isfinite(pila).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        media = np.nansum(pila, axis=0) / validos
    media[validos == 0] = np.nan
    return media.astype(np.float32)
