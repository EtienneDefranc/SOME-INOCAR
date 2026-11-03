"""Caché local de Copernicus Marine en la malla SOME (ya regrillada), en `datos/copernicus/`.

Diario: `diario/<variable>/<nivel>/AAAA-MM.npz` (variable ∈ thetao, so, uo, vo,
zos, mlotst; nivel ∈ z000 z005 z010 z020 z030 z050 z075 z100 | sup) con
  * `valores`  (días_del_mes, 49, 69) float32, NaN = tierra/fondo o no obtenido;
  * `tipo`     (días_del_mes, 49, 69) int8 por celda: 0 no obtenido, 1 análisis NRT,
    2 mejor análisis;
  * `obtenido` (días,) texto ISO del momento de la descarga ("" si nada);
  * `boletin`  (días,) fecha ISO del boletín semanal W con que se obtuvo el día;
  * `dia_tipo` (días,) int8 resumen del día: 0 nada, 1 alguna celda NRT, 2 solo mejor
    análisis; `dia_completo` (días,) bool: el área entera obtenida (se leen sin cargar
    los valores, para los conteos);
  * `dataset_id`, `version` (texto) y `formato` int8 = 1.

Climatología: `climatologia/<variable>_<nivel>.npz` con `valores` (12, 49, 69),
`periodo`, `anio_inicio`, `anio_fin`, `fuente_periodo`, `limites` (12, 2),
`obtenido`, `dataset_id`, `version`.

Regla de reemplazo por celda: se escribe si el tipo nuevo es mayor que el
guardado, o si ambos son NRT y el boletín nuevo es posterior; un mejor análisis
nunca se pisa con un NRT y nunca se guarda un día de tipo 0 (simulación o
pronóstico).

Concurrencia (igual que `chirps/almacen.py`): escritura atómica (temporal con
fsync + `os.replace` con reintentos), un bloqueo por archivo ENTRE PROCESOS
(`.bloqueos/<variable>-<nivel>-AAAA-MM.lock`) dentro del cual se relee el mes del
disco antes de escribir, y una caché en memoria (LRU) invalidada por *mtime*.
Los archivos dañados se apartan como `.danado` y se tratan como ausentes.

Marca de generación (`generacion`): se reescribe en cada escritura (de este u
otro proceso); `marca()` la lee y con ella se invalidan los conteos y la firma de
la caché de respuestas.
"""

from __future__ import annotations

import calendar
import logging
import os
import tempfile
import threading
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from .. import configuracion as cfg
from ..chirps.bloqueo import bloqueo as _bloqueo_procesos
from .constantes import (ANALISIS_NRT, MEJOR_ANALISIS, NIVELES_SOME, NO_OBTENIDO, VARIABLES, VARIABLES_2D,
                         nombre_nivel, ruta_bloqueos, ruta_climatologia, ruta_diario, ruta_generacion)

log = logging.getLogger("some.copernicus")

NLAT, NLON = len(cfg.LAT), len(cfg.LON)
FORMATO = 1
ESPERA_BLOQUEO_S = 120
MAX_MESES_EN_MEMORIA = 400            # ~0.5 MB por mes cargado

_bloqueo_tabla = threading.Lock()
_bloqueo_memoria = threading.Lock()        # protege la LRU (la tocan hilos que leen meses distintos)
_bloqueos: dict[tuple, threading.Lock] = {}
_memoria: "OrderedDict[tuple, tuple]" = OrderedDict()
_memoria_clim: dict[str, tuple] = {}
_resumenes: dict[str, tuple] = {}          # ruta → (mtime, dia_tipo, dia_completo)
_agregados: dict[str, tuple] = {}          # nombre → (marca, valor)
_contador = 0


# ---------------------------------------------------------------------------
# Tipos de retorno
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Lectura:
    variable: str
    profundidad: float | None          # nivel SOME (0, 5, …, 100) o None para variables 2D
    fechas: list                       # n días consecutivos (date)
    valores: np.ndarray                # (n, nlat, nlon) float32
    tipo: np.ndarray                   # (n, nlat, nlon) int8: 0 no obtenido, 1 análisis NRT, 2 mejor análisis
    boletin: list                      # n fechas ISO del boletín semanal W con que se obtuvo cada día ("" si ninguno)

    @property
    def cobertura(self) -> float:
        """Fracción de celdas-día obtenidas (tipo > 0); 1.0 si no hay días."""
        if self.tipo.size == 0:
            return 1.0
        return float((self.tipo > NO_OBTENIDO).mean())


@dataclass(frozen=True)
class ClimatologiaCMEMS:
    variable: str
    profundidad: float | None
    valores: np.ndarray                # (12, 49, 69) float32, mes 1..12
    periodo: str                       # "1993-2016"
    anio_inicio: int
    anio_fin: int
    fuente_periodo: str                # "climatology_bounds" | "metadatos" | "respaldo"
    limites: list                      # 12 pares ISO leídos de climatology_bounds ([] si no se pudieron leer)
    obtenido: str                      # ISO del momento de descarga


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _nivel_valido(variable: str, nivel) -> int | None:
    if variable not in VARIABLES:
        raise ValueError(f"Variable de Copernicus Marine desconocida en la caché: «{variable}».")
    if variable in VARIABLES_2D:
        return None
    if nivel is None:
        raise ValueError(f"La variable «{variable}» necesita un nivel de profundidad.")
    z = float(nivel)
    for n in NIVELES_SOME:
        if abs(z - n) <= 1e-6:
            return int(n)
    raise ValueError(f"Nivel {nivel} m no válido para la caché (niveles: {', '.join(map(str, NIVELES_SOME))} m).")


def ruta_mes(variable: str, nivel: int | None, anio: int, mes: int) -> Path:
    return ruta_diario() / variable / nombre_nivel(nivel) / f"{anio:04d}-{mes:02d}.npz"


def ruta_clim(variable: str, nivel: int | None) -> Path:
    return ruta_climatologia() / f"{variable}_{nombre_nivel(nivel)}.npz"


def _bloqueo(clave: tuple) -> threading.Lock:
    with _bloqueo_tabla:
        if clave not in _bloqueos:
            _bloqueos[clave] = threading.Lock()
        return _bloqueos[clave]


def _mtime(ruta: Path) -> int:
    try:
        return ruta.stat().st_mtime_ns
    except OSError:
        return -1


def _meses(inicio: date, fin: date):
    a, m = inicio.year, inicio.month
    while (a, m) <= (fin.year, fin.month):
        yield a, m
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)


def _vacio(anio: int, mes: int):
    n = calendar.monthrange(anio, mes)[1]
    return (np.full((n, NLAT, NLON), np.nan, dtype=np.float32), np.zeros((n, NLAT, NLON), dtype=np.int8),
            np.array([""] * n, dtype="U19"), np.array([""] * n, dtype="U10"))


def _apartar_danado(ruta: Path, motivo: str) -> None:
    """Renombra un archivo ilegible a «.danado» (para que se rehaga) y lo registra. Nunca lanza."""
    destino = ruta.with_name(ruta.name + ".danado")
    try:
        if destino.exists():
            destino = ruta.with_name(f"{ruta.name}.{datetime.now():%Y%m%d%H%M%S}.danado")
        os.replace(ruta, destino)
        log.warning("Copernicus: archivo de caché dañado (%s); se apartó como %s.", motivo, destino.name)
    except OSError:
        log.warning("Copernicus: archivo de caché dañado (%s): %s; no se pudo apartar.", motivo, ruta)
    try:
        from . import estado          # importación perezosa (estado importa almacen)
        estado.registrar_error(f"Caché local: el archivo {ruta.parent.parent.name}/{ruta.parent.name}/{ruta.name} "
                               f"estaba dañado; se volverá a obtener.")
    except Exception:  # noqa: BLE001 — el registro nunca debe impedir seguir
        pass
    _tocar_generacion()


def _escribir_atomico(ruta: Path, **arreglos) -> int:
    """Escribe el npz en un temporal (con fsync) y lo pone en su sitio con `os.replace`. Devuelve el mtime (ns)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=ruta.stem + ".", suffix=".tmp", dir=ruta.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            np.savez_compressed(f, **arreglos)
            f.flush()
            os.fsync(f.fileno())
        mt = os.stat(tmp).st_mtime_ns
        for intento in range(6):
            try:
                os.replace(tmp, ruta)
                break
            except PermissionError:          # Windows: el destino está abierto por otro lector
                if intento == 5:
                    raise
                time.sleep(0.2 * (intento + 1))
        return mt
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _tocar_generacion() -> None:
    """Reescribe la marca de generación (contenido distinto en cada escritura, de cualquier proceso)."""
    global _contador
    _contador += 1
    ruta = ruta_generacion()
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        contenido = f"{time.time_ns()}:{os.getpid()}:{threading.get_ident()}:{_contador}"
        for intento in range(5):
            try:
                with open(ruta, "w", encoding="ascii") as f:
                    f.write(contenido)
                break
            except PermissionError:
                time.sleep(0.05 * (intento + 1))
    except OSError as e:
        log.warning("Copernicus: no se pudo actualizar la marca de generación: %s", e)
    _agregados.clear()


def marca() -> int:
    """Marca de generación de la caché: cambia con cada escritura (de este u otro proceso). 0 si no existe."""
    ruta = ruta_generacion()
    for _ in range(3):
        try:
            with open(ruta, "rb") as f:
                return zlib.crc32(f.read())
        except FileNotFoundError:
            return 0
        except OSError:
            time.sleep(0.02)
    return _mtime(ruta)


# ---------------------------------------------------------------------------
# Lectura y escritura del diario
# ---------------------------------------------------------------------------
def _resumen_dias(t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    obtenido = t > NO_OBTENIDO
    algo = obtenido.any(axis=(1, 2))
    nrt = (t == ANALISIS_NRT).any(axis=(1, 2))
    dia_tipo = np.where(~algo, NO_OBTENIDO, np.where(nrt, ANALISIS_NRT, MEJOR_ANALISIS)).astype(np.int8)
    return dia_tipo, obtenido.all(axis=(1, 2))


def _leer_npz_mes(ruta: Path, anio: int, mes: int):
    n = calendar.monthrange(anio, mes)[1]
    # Se abre aquí (y no con np.load(ruta)): con un zip dañado np.load deja el descriptor abierto
    # y en Windows el archivo no se podría apartar.
    with open(ruta, "rb") as fh, np.load(fh, allow_pickle=False) as z:
        v = z["valores"].astype(np.float32)
        t = z["tipo"].astype(np.int8)
        o = z["obtenido"].astype("U19")
        b = z["boletin"].astype("U10")
    if v.shape != (n, NLAT, NLON) or t.shape != (n, NLAT, NLON) or o.shape != (n,) or b.shape != (n,):
        raise ValueError(f"formas inesperadas {v.shape}, {t.shape}, {o.shape}, {b.shape}")
    return v, t, o, b


def _cargar_mes(variable: str, nivel: int | None, anio: int, mes: int, estricto: bool = False):
    """(valores, tipo, obtenido, boletin) del mes; usa la memoria si el archivo no cambió. Llamar con el bloqueo."""
    ruta = ruta_mes(variable, nivel, anio, mes)
    clave = (str(ruta_diario()), variable, nombre_nivel(nivel), anio, mes)
    mt = _mtime(ruta)
    with _bloqueo_memoria:
        en_mem = _memoria.get(clave)
        if en_mem is not None and en_mem[0] == mt:
            _memoria.move_to_end(clave)
            return en_mem[1:]
    if mt < 0:
        datos = _vacio(anio, mes)
    else:
        datos = None
        for intento in range(3):
            try:
                datos = _leer_npz_mes(ruta, anio, mes)
                break
            except PermissionError:      # Windows: otro proceso lo reemplaza en este instante
                if intento == 2:
                    if estricto:
                        raise
                    log.warning("Copernicus: no se pudo abrir %s (en uso por otro proceso).", ruta)
                    return _vacio(anio, mes)
                time.sleep(0.2)
            except Exception as e:  # noqa: BLE001 — BadZipFile, EOFError, zlib.error, formas…
                if intento < 2:
                    time.sleep(0.1)
                    continue
                _apartar_danado(ruta, f"{type(e).__name__}: {e}")
                mt = _mtime(ruta)
                datos = _vacio(anio, mes)
    _recordar(clave, mt, datos)
    return datos


def _recordar(clave: tuple, mt: int, datos: tuple) -> None:
    with _bloqueo_memoria:
        _memoria[clave] = (mt, *datos)
        _memoria.move_to_end(clave)
        while len(_memoria) > MAX_MESES_EN_MEMORIA:
            _memoria.popitem(last=False)


def leer(variable: str, nivel: int | None, inicio: date, fin: date, ilat=None, ilon=None) -> Lectura:
    """Lectura de la caché de los días [inicio, fin] (las celdas `ilat` × `ilon`; todas si son None)."""
    nivel = _nivel_valido(variable, nivel)
    fechas = [inicio + timedelta(days=i) for i in range(max(0, (fin - inicio).days + 1))]
    partes_v, partes_t, boletin = [], [], []
    completo = ilat is None and ilon is None
    filas = np.arange(NLAT) if ilat is None else np.atleast_1d(np.asarray(ilat, dtype=np.int64))
    cols = np.arange(NLON) if ilon is None else np.atleast_1d(np.asarray(ilon, dtype=np.int64))
    for a, m in _meses(inicio, fin) if fechas else ():
        d0 = inicio.day if (a, m) == (inicio.year, inicio.month) else 1
        d1 = fin.day if (a, m) == (fin.year, fin.month) else calendar.monthrange(a, m)[1]
        with _bloqueo(("mes", variable, nombre_nivel(nivel), a, m)):
            v, t, _o, b = _cargar_mes(variable, nivel, a, m)
            if completo:
                partes_v.append(v[d0 - 1:d1].copy())
                partes_t.append(t[d0 - 1:d1].copy())
            else:                                   # la indexación avanzada ya copia
                partes_v.append(v[d0 - 1:d1][:, filas[:, None], cols[None, :]])
                partes_t.append(t[d0 - 1:d1][:, filas[:, None], cols[None, :]])
            boletin.extend(str(x) for x in b[d0 - 1:d1])
    nlat, nlon = len(filas), len(cols)
    if partes_v:
        valores, tipo = np.concatenate(partes_v), np.concatenate(partes_t)
    else:
        valores = np.empty((0, nlat, nlon), np.float32)
        tipo = np.empty((0, nlat, nlon), np.int8)
    return Lectura(variable=variable, profundidad=None if nivel is None else float(nivel), fechas=fechas,
                   valores=valores, tipo=tipo, boletin=boletin)


def guardar(variable: str, nivel: int | None, fechas: list, valores: np.ndarray, cubiertas: np.ndarray,
            tipos: list, boletin, dataset_id: str = "", version: str = "") -> int:
    """Guarda los días `fechas` (valores (n, 49, 69)) en las celdas `cubiertas` ((49, 69) o (n, 49, 69)).

    `tipos`: tipo de cada día (1 NRT, 2 mejor análisis; los de tipo 0 se descartan); `boletin`: fecha
    del boletín semanal W. Aplica la regla de reemplazo por celda. Cada mes se actualiza bajo un
    bloqueo entre procesos releyendo el archivo del disco. Devuelve las celdas-día escritas.
    """
    nivel = _nivel_valido(variable, nivel)
    valores = np.asarray(valores, dtype=np.float32)
    if valores.shape != (len(fechas), NLAT, NLON):
        raise ValueError(f"Valores con forma {valores.shape}; se esperaba ({len(fechas)}, {NLAT}, {NLON}).")
    cubiertas = np.asarray(cubiertas, dtype=bool)
    if cubiertas.shape == (NLAT, NLON):
        cubiertas = np.broadcast_to(cubiertas, valores.shape)
    if cubiertas.shape != valores.shape:
        raise ValueError(f"Máscara de celdas con forma {cubiertas.shape}.")
    if len(tipos) != len(fechas):
        raise ValueError("Se necesita un tipo por día.")
    boletin_iso = boletin.isoformat() if isinstance(boletin, date) else str(boletin or "")
    por_mes: dict[tuple[int, int], list[int]] = {}
    for k, d in enumerate(fechas):
        if int(tipos[k]) in (ANALISIS_NRT, MEJOR_ANALISIS):
            por_mes.setdefault((d.year, d.month), []).append(k)
    escritas = 0
    ahora = datetime.now().isoformat(timespec="seconds")
    for (a, m), indices in sorted(por_mes.items()):
        clave = ("mes", variable, nombre_nivel(nivel), a, m)
        candado = ruta_bloqueos() / f"{variable}-{nombre_nivel(nivel)}-{a:04d}-{m:02d}.lock"
        with _bloqueo(clave), _bloqueo_procesos(candado, ESPERA_BLOQUEO_S):
            with _bloqueo_memoria:
                _memoria.pop((str(ruta_diario()), variable, nombre_nivel(nivel), a, m), None)   # releer del disco
            v, t, o, b = _cargar_mes(variable, nivel, a, m, estricto=True)
            v, t, o, b = v.copy(), t.copy(), o.copy(), b.copy()
            cambios = 0
            for k in indices:
                i = fechas[k].day - 1
                nuevo = np.int8(tipos[k])
                guardado = t[i]
                actualizar = cubiertas[k] & ((nuevo > guardado)
                                             | ((nuevo == ANALISIS_NRT) & (guardado == ANALISIS_NRT)
                                                & (boletin_iso > str(b[i]))))
                n = int(actualizar.sum())
                if not n:
                    continue
                v[i][actualizar] = valores[k][actualizar]
                t[i][actualizar] = nuevo
                o[i] = ahora
                if boletin_iso > str(b[i]):
                    b[i] = boletin_iso
                cambios += n
            if cambios:
                dia_tipo, dia_completo = _resumen_dias(t)
                ruta = ruta_mes(variable, nivel, a, m)
                mt = _escribir_atomico(ruta, valores=v, tipo=t, obtenido=o, boletin=b, dia_tipo=dia_tipo,
                                       dia_completo=dia_completo, dataset_id=np.array(str(dataset_id)),
                                       version=np.array(str(version)), formato=np.array(FORMATO, dtype=np.int8))
                _recordar((str(ruta_diario()), variable, nombre_nivel(nivel), a, m), mt, (v, t, o, b))
                _resumenes[str(ruta)] = (mt, dia_tipo, dia_completo)
                escritas += cambios
    if escritas:
        _tocar_generacion()
    return escritas


# ---------------------------------------------------------------------------
# Resúmenes y conteos (sin cargar los valores)
# ---------------------------------------------------------------------------
def _resumen_archivo(ruta: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """(dia_tipo, dia_completo) del archivo mensual, con caché por mtime. None si no se puede leer."""
    mt = _mtime(ruta)
    if mt < 0:
        return None
    en_mem = _resumenes.get(str(ruta))
    if en_mem is not None and en_mem[0] == mt:
        return en_mem[1], en_mem[2]
    try:
        with open(ruta, "rb") as fh, np.load(fh, allow_pickle=False) as z:
            if "dia_tipo" in z.files and "dia_completo" in z.files:
                dt, dc = z["dia_tipo"].astype(np.int8), z["dia_completo"].astype(bool)
            else:
                dt, dc = _resumen_dias(z["tipo"].astype(np.int8))
    except Exception:  # noqa: BLE001 — un archivo en uso o dañado no cuenta (la lectura lo apartará)
        return None
    _resumenes[str(ruta)] = (mt, dt, dc)
    return dt, dc


def _archivos() -> list[tuple[str, int | None, int, int, Path]]:
    """[(variable, nivel, año, mes, ruta)] de todos los archivos mensuales de la caché."""
    salida = []
    base = ruta_diario()
    for variable in VARIABLES:
        niveles = [None] if variable in VARIABLES_2D else list(NIVELES_SOME)
        for nivel in niveles:
            carpeta = base / variable / nombre_nivel(nivel)
            try:
                rutas = sorted(carpeta.glob("????-??.npz"))
            except OSError:
                rutas = []
            for p in rutas:
                try:
                    a, m = int(p.stem[:4]), int(p.stem[5:7])
                except ValueError:
                    continue
                salida.append((variable, nivel, a, m, p))
    return salida


def _agregado(nombre: str, calcular):
    mk = (marca(), str(ruta_diario()))
    en_mem = _agregados.get(nombre)
    if en_mem is not None and en_mem[0] == mk:
        return en_mem[1]
    valor = calcular()
    _agregados[nombre] = (mk, valor)
    return valor


def _dias_por_conjunto() -> dict:
    """{(variable, nivel): {date: (dia_tipo, completo)}} solo con días que tienen algún dato."""
    def calcular():
        conjuntos: dict = {}
        for variable, nivel, a, m, p in _archivos():
            r = _resumen_archivo(p)
            if r is None:
                continue
            dt, dc = r
            dias = conjuntos.setdefault((variable, nivel), {})
            for i in np.nonzero(dt > NO_OBTENIDO)[0]:
                dias[date(a, m, int(i) + 1)] = (int(dt[i]), bool(dc[i]))
        return conjuntos
    return _agregado("dias_por_conjunto", calcular)


def ultimo_dia() -> date | None:
    """Último día con alguna celda obtenida (cualquier variable y nivel)."""
    fechas = [max(d) for d in _dias_por_conjunto().values() if d]
    return max(fechas) if fechas else None


def hay_datos() -> bool:
    return ultimo_dia() is not None


def conjuntos() -> list[dict]:
    """Por variable y nivel: días con dato, rango, días NRT y días con el área entera obtenida."""
    salida = []
    for (variable, nivel), dias in sorted(_dias_por_conjunto().items(),
                                          key=lambda x: (VARIABLES.index(x[0][0]), -1 if x[0][1] is None else x[0][1])):
        if not dias:
            continue
        salida.append({"variable": variable, "nivel": nivel, "dias": len(dias),
                       "desde": min(dias).isoformat(), "hasta": max(dias).isoformat(),
                       "dias_nrt": sum(1 for t, _c in dias.values() if t == ANALISIS_NRT),
                       "dias_completos": sum(1 for _t, c in dias.values() if c)})
    return salida


def conteo_dias() -> dict:
    """Días distintos con dato en alguna variable y nivel, cuántos tienen algo NRT y las fechas límite."""
    con_dato: set[date] = set()
    con_nrt: set[date] = set()
    for dias in _dias_por_conjunto().values():
        for d, (t, _c) in dias.items():
            con_dato.add(d)
            if t == ANALISIS_NRT:
                con_nrt.add(d)
    definitivos = con_dato - con_nrt
    return {"dias_con_dato": len(con_dato), "dias_nrt": len(con_nrt), "dias_definitivos": len(definitivos),
            "ultimo_definitivo": max(definitivos) if definitivos else None,
            "ultimo_nrt": max(con_nrt) if con_nrt else None,
            "ultimo_dia": max(con_dato) if con_dato else None}


def origen_dias(variable: str, nivel, inicio: date, fin: date) -> dict:
    """{día: "definitivo" | "nrt" | "parcial" | "ausente"} sobre el área completa."""
    nivel = _nivel_valido(variable, nivel)
    dias = _dias_por_conjunto().get((variable, nivel), {})
    salida = {}
    d = inicio
    while d <= fin:
        t, completo = dias.get(d, (NO_OBTENIDO, False))
        if t == NO_OBTENIDO:
            salida[d] = "ausente"
        elif not completo:
            salida[d] = "parcial"
        else:
            salida[d] = "nrt" if t == ANALISIS_NRT else "definitivo"
        d += timedelta(days=1)
    return salida


def ultima_obtencion() -> date | None:
    """Fecha de la descarga más reciente guardada (fecha de acceso para las referencias)."""
    def calcular():
        ultima = None
        for _v, _n, _a, _m, p in _archivos():
            mt = _mtime(p)
            if mt > 0:
                ultima = max(ultima or 0, mt)
        for p in _rutas_climatologia():
            mt = _mtime(p)
            if mt > 0:
                ultima = max(ultima or 0, mt)
        return datetime.fromtimestamp(ultima / 1e9).date() if ultima else None
    return _agregado("ultima_obtencion", calcular)


def tamano_bytes() -> int:
    total = 0
    for carpeta in (ruta_diario(), ruta_climatologia()):
        if carpeta.exists():
            for p in carpeta.rglob("*.npz"):
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    return total


# ---------------------------------------------------------------------------
# Climatología
# ---------------------------------------------------------------------------
def _rutas_climatologia() -> list[Path]:
    try:
        return sorted(ruta_climatologia().glob("*.npz"))
    except OSError:
        return []


def leer_climatologia(variable: str, nivel) -> ClimatologiaCMEMS | None:
    """Climatología (variable, nivel) de la caché, o None si no está (o el archivo está dañado: se aparta)."""
    nivel = _nivel_valido(variable, nivel)
    ruta = ruta_clim(variable, nivel)
    mt = _mtime(ruta)
    if mt < 0:
        return None
    with _bloqueo(("clim", variable, nombre_nivel(nivel))):
        en_mem = _memoria_clim.get(str(ruta))
        if en_mem is not None and en_mem[0] == mt:
            return en_mem[1]
        for intento in range(3):
            try:
                with open(ruta, "rb") as fh, np.load(fh, allow_pickle=False) as z:
                    v = z["valores"].astype(np.float32)
                    limites = [tuple(str(x) for x in par) for par in z["limites"]] if "limites" in z.files else []
                    c = ClimatologiaCMEMS(
                        variable=variable, profundidad=None if nivel is None else float(nivel), valores=v,
                        periodo=str(z["periodo"]), anio_inicio=int(z["anio_inicio"]), anio_fin=int(z["anio_fin"]),
                        fuente_periodo=str(z["fuente_periodo"]), limites=limites, obtenido=str(z["obtenido"]))
                if v.shape != (12, NLAT, NLON):
                    raise ValueError(f"forma inesperada {v.shape}")
                break
            except PermissionError:
                if intento == 2:
                    return None
                time.sleep(0.2)
            except Exception as e:  # noqa: BLE001
                if intento < 2:
                    time.sleep(0.1)
                    continue
                _memoria_clim.pop(str(ruta), None)
                _apartar_danado(ruta, f"{type(e).__name__}: {e}")
                return None
        _memoria_clim[str(ruta)] = (mt, c)
        return c


def guardar_climatologia(c: ClimatologiaCMEMS, dataset_id: str = "", version: str = "") -> None:
    nivel = _nivel_valido(c.variable, c.profundidad)
    if c.valores.shape != (12, NLAT, NLON):
        raise ValueError(f"Climatología con forma {c.valores.shape}; se esperaba (12, {NLAT}, {NLON}).")
    ruta = ruta_clim(c.variable, nivel)
    limites = np.array([list(p) for p in c.limites], dtype="U10") if c.limites else np.empty((0, 2), dtype="U10")
    candado = ruta_bloqueos() / f"clim-{c.variable}-{nombre_nivel(nivel)}.lock"
    with _bloqueo(("clim", c.variable, nombre_nivel(nivel))), _bloqueo_procesos(candado, ESPERA_BLOQUEO_S):
        mt = _escribir_atomico(ruta, valores=c.valores.astype(np.float32), periodo=np.array(c.periodo),
                               anio_inicio=np.array(int(c.anio_inicio)), anio_fin=np.array(int(c.anio_fin)),
                               fuente_periodo=np.array(c.fuente_periodo), limites=limites,
                               obtenido=np.array(c.obtenido), dataset_id=np.array(str(dataset_id)),
                               version=np.array(str(version)), formato=np.array(FORMATO, dtype=np.int8))
        _memoria_clim[str(ruta)] = (mt, c)
    _tocar_generacion()


def climatologias_en_cache() -> list[dict]:
    """[{"variable", "nivel"}] de las climatologías guardadas (en el orden de VARIABLES y niveles)."""
    def calcular():
        salida = []
        for variable in VARIABLES:
            niveles = [None] if variable in VARIABLES_2D else list(NIVELES_SOME)
            for nivel in niveles:
                if _mtime(ruta_clim(variable, nivel)) >= 0:
                    salida.append({"variable": variable, "nivel": nivel})
        return salida
    return _agregado("climatologias", calcular)


def periodo_climatologia() -> str | None:
    """Periodo de la primera climatología guardada que se pueda leer (todas comparten el del producto)."""
    for par in climatologias_en_cache():
        c = leer_climatologia(par["variable"], par["nivel"])
        if c is not None:
            return c.periodo
    return None


def olvidar_memoria() -> None:
    """Vacía las cachés en memoria (pruebas y cambios de raíz)."""
    with _bloqueo_memoria:
        _memoria.clear()
    _memoria_clim.clear()
    _resumenes.clear()
    _agregados.clear()
