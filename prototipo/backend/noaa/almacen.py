"""Caché local de NOAA OISST v2.1 en `datos/noaa/` (o `SOME_RUTA_NOAA`).

Diario: `diario/AAAA-MM.npz`, un archivo por mes, con
  * `valores` (días_del_mes, 49, 69) float32 en la malla SOME (NaN = tierra o no obtenido);
  * `nino12`  (días_del_mes, 40, 40) float32: celdas nativas de la región Niño 1+2 oficial
    (0–10° S, 90–80° O; se sale del dominio por el sur), para el índice;
  * `tipo`    (días,) int8: 0 ausente, 1 preliminar, 2 final;
  * `origen`  (días,) int8: 0 ninguno, 1 NOAA PSL, 2 NOAA NCEI;
  * `obtenido` (días,) texto ISO del momento de la descarga ("" si nada);
  * `formato` int8 = 1.

Mensual: `mensual/sst_mon_mean.npz` (medias mensuales nativas de la ventana, 65 × 70,
desde 09/1981) y `climatologia/sst_<periodo>.npz` (climatología mensual en la malla
SOME, con el periodo efectivo de cada mes); `climatologia/ltm_diaria_nino12.npz`
(climatología diaria 1991-2020 de la región Niño 1+2, 365 × 40 × 40).

Regla de reemplazo por día: se escribe si el tipo nuevo es mayor que el guardado,
o si ambos son preliminares (el más reciente gana); un final nunca se pisa con un
preliminar y nunca se guarda un día ausente ni uno sin ninguna celda válida.

Concurrencia (como CHIRPS y Copernicus): escritura atómica (temporal con fsync +
`os.replace` con reintentos), un bloqueo por mes ENTRE PROCESOS dentro del cual se
relee el mes del disco antes de escribir, y una caché en memoria invalidada por
*mtime*. Los archivos dañados se apartan como `.danado` y se tratan como ausentes.
La marca de generación (`generacion`) cambia con cada escritura (firma de la caché).
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
from .constantes import (AUSENTE, FINAL, ORIGEN_NCEI, ORIGEN_NINGUNO, ORIGEN_PSL, PRELIMINAR, ruta_bloqueos,
                         ruta_climatologia, ruta_diario, ruta_generacion, ruta_mensual)

log = logging.getLogger("some.noaa")

NLAT, NLON = len(cfg.LAT), len(cfg.LON)
NR = 40                               # celdas nativas por lado de la región Niño 1+2
FORMATO = 1
ESPERA_BLOQUEO_S = 120
MAX_MESES_EN_MEMORIA = 120

_bloqueo_tabla = threading.Lock()
_bloqueo_memoria = threading.Lock()
_bloqueos: dict[tuple, threading.Lock] = {}
_memoria: "OrderedDict[tuple, tuple]" = OrderedDict()
_memoria_otros: dict[str, tuple] = {}
_resumenes: dict[str, tuple] = {}     # ruta → (mtime, tipo, origen)
_agregados: dict[str, tuple] = {}
_contador = 0


@dataclass(frozen=True)
class Lectura:
    fechas: list                      # n días consecutivos (date)
    valores: np.ndarray               # (n, nlat, nlon) float32
    tipo: np.ndarray                  # (n,) int8: 0 ausente, 1 preliminar, 2 final
    origen: np.ndarray                # (n,) int8: 0 ninguno, 1 PSL, 2 NCEI

    @property
    def obtenidos(self) -> np.ndarray:
        return self.tipo > AUSENTE


@dataclass(frozen=True)
class LecturaNino12:
    fechas: list
    valores: np.ndarray               # (n, 40, 40) float32
    tipo: np.ndarray
    origen: np.ndarray


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def ruta_mes(anio: int, mes: int) -> Path:
    return ruta_diario() / f"{anio:04d}-{mes:02d}.npz"


def ruta_mensual_nativo() -> Path:
    return ruta_mensual() / "sst_mon_mean.npz"


def ruta_clim(periodo: str) -> Path:
    return ruta_climatologia() / f"sst_{periodo}.npz"


def ruta_ltm_diaria() -> Path:
    return ruta_climatologia() / "ltm_diaria_nino12.npz"


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


def meses(inicio: date, fin: date):
    a, m = inicio.year, inicio.month
    while (a, m) <= (fin.year, fin.month):
        yield a, m
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)


def _vacio(anio: int, mes: int):
    n = calendar.monthrange(anio, mes)[1]
    return (np.full((n, NLAT, NLON), np.nan, dtype=np.float32), np.full((n, NR, NR), np.nan, dtype=np.float32),
            np.zeros(n, dtype=np.int8), np.zeros(n, dtype=np.int8), np.array([""] * n, dtype="U19"))


def _apartar_danado(ruta: Path, motivo: str) -> None:
    """Renombra un archivo ilegible a «.danado» (para que se rehaga) y lo registra. Nunca lanza."""
    destino = ruta.with_name(ruta.name + ".danado")
    try:
        if destino.exists():
            destino = ruta.with_name(f"{ruta.name}.{datetime.now():%Y%m%d%H%M%S}.danado")
        os.replace(ruta, destino)
        log.warning("NOAA: archivo de caché dañado (%s); se apartó como %s.", motivo, destino.name)
    except OSError:
        log.warning("NOAA: archivo de caché dañado (%s): %s; no se pudo apartar.", motivo, ruta)
    try:
        from . import estado          # perezoso (estado importa almacen)
        estado.registrar_error(f"Caché local: el archivo {ruta.parent.name}/{ruta.name} estaba dañado; se volverá "
                               "a obtener.")
    except Exception:  # noqa: BLE001
        pass
    tocar_generacion()


def _escribir_atomico(ruta: Path, **arreglos) -> int:
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


def tocar_generacion() -> None:
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
        log.warning("NOAA: no se pudo actualizar la marca de generación: %s", e)
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
# Diario
# ---------------------------------------------------------------------------
def _leer_npz_mes(ruta: Path, anio: int, mes: int):
    n = calendar.monthrange(anio, mes)[1]
    with open(ruta, "rb") as fh, np.load(fh, allow_pickle=False) as z:
        v = z["valores"].astype(np.float32)
        r = z["nino12"].astype(np.float32)
        t = z["tipo"].astype(np.int8)
        o = z["origen"].astype(np.int8)
        ob = z["obtenido"].astype("U19")
    if v.shape != (n, NLAT, NLON) or r.shape != (n, NR, NR) or t.shape != (n,) or o.shape != (n,) or ob.shape != (n,):
        raise ValueError(f"formas inesperadas {v.shape}, {r.shape}, {t.shape}")
    return v, r, t, o, ob


def _cargar_mes(anio: int, mes: int, estricto: bool = False):
    """(valores, nino12, tipo, origen, obtenido) del mes; usa la memoria si el archivo no cambió."""
    ruta = ruta_mes(anio, mes)
    clave = (str(ruta_diario()), anio, mes)
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
            except PermissionError:
                if intento == 2:
                    if estricto:
                        raise
                    log.warning("NOAA: no se pudo abrir %s (en uso por otro proceso).", ruta)
                    return _vacio(anio, mes)
                time.sleep(0.2)
            except Exception as e:  # noqa: BLE001 — BadZipFile, EOFError, formas…
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


def _fechas(inicio: date, fin: date) -> list[date]:
    return [inicio + timedelta(days=i) for i in range(max(0, (fin - inicio).days + 1))]


def leer(inicio: date, fin: date, ilat=None, ilon=None) -> Lectura:
    """Días [inicio, fin] de la malla SOME (las celdas `ilat` × `ilon`; todas si son None)."""
    fechas = _fechas(inicio, fin)
    filas = np.arange(NLAT) if ilat is None else np.atleast_1d(np.asarray(ilat, dtype=np.int64))
    cols = np.arange(NLON) if ilon is None else np.atleast_1d(np.asarray(ilon, dtype=np.int64))
    pv, pt, po = [], [], []
    for a, m in meses(inicio, fin) if fechas else ():
        d0 = inicio.day if (a, m) == (inicio.year, inicio.month) else 1
        d1 = fin.day if (a, m) == (fin.year, fin.month) else calendar.monthrange(a, m)[1]
        with _bloqueo(("mes", a, m)):
            v, _r, t, o, _ob = _cargar_mes(a, m)
            pv.append(v[d0 - 1:d1][:, filas[:, None], cols[None, :]])
            pt.append(t[d0 - 1:d1].copy())
            po.append(o[d0 - 1:d1].copy())
    if pv:
        valores, tipo, origen = np.concatenate(pv), np.concatenate(pt), np.concatenate(po)
    else:
        valores = np.empty((0, len(filas), len(cols)), np.float32)
        tipo = origen = np.empty(0, np.int8)
    # Un día ausente no tiene valores (aunque el archivo traiga restos)
    valores[tipo == AUSENTE] = np.nan
    return Lectura(fechas=fechas, valores=valores, tipo=tipo, origen=origen)


def leer_nino12(inicio: date, fin: date) -> LecturaNino12:
    """Días [inicio, fin] de la región Niño 1+2 oficial (celdas nativas 40 × 40)."""
    fechas = _fechas(inicio, fin)
    pr, pt, po = [], [], []
    for a, m in meses(inicio, fin) if fechas else ():
        d0 = inicio.day if (a, m) == (inicio.year, inicio.month) else 1
        d1 = fin.day if (a, m) == (fin.year, fin.month) else calendar.monthrange(a, m)[1]
        with _bloqueo(("mes", a, m)):
            _v, r, t, o, _ob = _cargar_mes(a, m)
            pr.append(r[d0 - 1:d1].copy())
            pt.append(t[d0 - 1:d1].copy())
            po.append(o[d0 - 1:d1].copy())
    if pr:
        valores, tipo, origen = np.concatenate(pr), np.concatenate(pt), np.concatenate(po)
    else:
        valores = np.empty((0, NR, NR), np.float32)
        tipo = origen = np.empty(0, np.int8)
    valores[tipo == AUSENTE] = np.nan
    return LecturaNino12(fechas=fechas, valores=valores, tipo=tipo, origen=origen)


def guardar(fechas: list, valores: np.ndarray, nino12: np.ndarray, tipos, origen: int) -> int:
    """Guarda los días `fechas` (valores (n, 49, 69), nino12 (n, 40, 40)) con su tipo y su origen.

    Aplica la regla de reemplazo por día; un día sin ninguna celda válida en la malla SOME se descarta.
    Cada mes se actualiza bajo un bloqueo entre procesos releyendo el archivo del disco. Devuelve los
    días escritos.
    """
    valores = np.asarray(valores, dtype=np.float32)
    nino12 = np.asarray(nino12, dtype=np.float32)
    n = len(fechas)
    if valores.shape != (n, NLAT, NLON) or nino12.shape != (n, NR, NR):
        raise ValueError(f"Formas {valores.shape} y {nino12.shape}; se esperaba ({n}, {NLAT}, {NLON}) y ({n}, {NR}, {NR}).")
    if len(tipos) != n:
        raise ValueError("Se necesita un tipo por día.")
    if origen not in (ORIGEN_PSL, ORIGEN_NCEI):
        raise ValueError(f"Origen desconocido: {origen}.")
    por_mes: dict[tuple[int, int], list[int]] = {}
    for k, d in enumerate(fechas):
        if int(tipos[k]) in (PRELIMINAR, FINAL) and np.isfinite(valores[k]).any():
            por_mes.setdefault((d.year, d.month), []).append(k)
    escritos = 0
    ahora = datetime.now().isoformat(timespec="seconds")
    for (a, m), indices in sorted(por_mes.items()):
        candado = ruta_bloqueos() / f"diario-{a:04d}-{m:02d}.lock"
        with _bloqueo(("mes", a, m)), _bloqueo_procesos(candado, ESPERA_BLOQUEO_S):
            with _bloqueo_memoria:
                _memoria.pop((str(ruta_diario()), a, m), None)       # releer del disco
            v, r, t, o, ob = (x.copy() for x in _cargar_mes(a, m, estricto=True))
            cambios = 0
            for k in indices:
                i = fechas[k].day - 1
                nuevo = int(tipos[k])
                guardado = int(t[i])
                if not (nuevo > guardado or (nuevo == PRELIMINAR and guardado == PRELIMINAR)):
                    continue
                v[i] = valores[k]
                r[i] = nino12[k]
                t[i] = nuevo
                o[i] = origen
                ob[i] = ahora
                cambios += 1
            if cambios:
                ruta = ruta_mes(a, m)
                mt = _escribir_atomico(ruta, valores=v, nino12=r, tipo=t, origen=o, obtenido=ob,
                                       formato=np.array(FORMATO, dtype=np.int8))
                _recordar((str(ruta_diario()), a, m), mt, (v, r, t, o, ob))
                _resumenes[str(ruta)] = (mt, t.copy(), o.copy())
                escritos += cambios
    if escritos:
        tocar_generacion()
    return escritos


# ---------------------------------------------------------------------------
# Resúmenes y conteos (sin cargar los valores)
# ---------------------------------------------------------------------------
def _resumen_archivo(ruta: Path) -> tuple[np.ndarray, np.ndarray] | None:
    mt = _mtime(ruta)
    if mt < 0:
        return None
    en_mem = _resumenes.get(str(ruta))
    if en_mem is not None and en_mem[0] == mt:
        return en_mem[1], en_mem[2]
    try:
        with open(ruta, "rb") as fh, np.load(fh, allow_pickle=False) as z:
            t, o = z["tipo"].astype(np.int8), z["origen"].astype(np.int8)
    except Exception:  # noqa: BLE001 — un archivo en uso o dañado no cuenta (la lectura lo apartará)
        return None
    _resumenes[str(ruta)] = (mt, t, o)
    return t, o


def _agregado(nombre: str, calcular):
    mk = (marca(), str(ruta_diario()))
    en_mem = _agregados.get(nombre)
    if en_mem is not None and en_mem[0] == mk:
        return en_mem[1]
    valor = calcular()
    _agregados[nombre] = (mk, valor)
    return valor


def dias() -> dict:
    """{date: (tipo, origen)} de todos los días guardados (solo los que tienen dato)."""
    def calcular():
        salida: dict = {}
        try:
            rutas = sorted(ruta_diario().glob("????-??.npz"))
        except OSError:
            rutas = []
        for p in rutas:
            try:
                a, m = int(p.stem[:4]), int(p.stem[5:7])
            except ValueError:
                continue
            r = _resumen_archivo(p)
            if r is None:
                continue
            t, o = r
            for i in np.nonzero(t > AUSENTE)[0]:
                salida[date(a, m, int(i) + 1)] = (int(t[i]), int(o[i]))
        return salida
    return _agregado("dias", calcular)


def ultimo_dia() -> date | None:
    d = dias()
    return max(d) if d else None


def hay_datos() -> bool:
    return bool(dias())


def tipo_dia(d: date) -> int:
    return dias().get(d, (AUSENTE, ORIGEN_NINGUNO))[0]


def conteo(inicio: date | None = None, fin: date | None = None) -> dict:
    """Días finales, preliminares y ausentes en [inicio, fin] (por defecto, desde FECHA_INICIO hasta el último
    día guardado), por origen, y las fechas límite."""
    todos = dias()
    inicio = inicio or cfg.FECHA_INICIO
    fin = fin or (max(todos) if todos else inicio - timedelta(days=1))
    finales = [d for d, (t, _o) in todos.items() if inicio <= d <= fin and t == FINAL]
    prelim = [d for d, (t, _o) in todos.items() if inicio <= d <= fin and t == PRELIMINAR]
    total = max(0, (fin - inicio).days + 1)
    por_origen = {"psl": 0, "ncei": 0}
    for d, (t, o) in todos.items():
        if inicio <= d <= fin and t > AUSENTE:
            por_origen["psl" if o == ORIGEN_PSL else "ncei"] += 1
    return {"dias": total, "final": len(finales), "preliminar": len(prelim),
            "ausente": total - len(finales) - len(prelim),
            "ultimo_final": max(finales) if finales else None,
            "primer_preliminar": min(prelim) if prelim else None,
            "ultimo_preliminar": max(prelim) if prelim else None,
            "ultimo_dia": max(finales + prelim) if (finales or prelim) else None,
            "por_origen": por_origen}


def faltantes(inicio: date, fin: date, renovar_desde: date | None = None, *,
              incluir_preliminares: bool = True) -> list[date]:
    """Días de [inicio, fin] ausentes, más los preliminares (si `incluir_preliminares`: la sincronización los
    renueva; las consultas solo piden los ausentes) y, desde `renovar_desde`, todos los no finales."""
    todos = dias()
    salida = []
    d = inicio
    while d <= fin:
        t = todos.get(d, (AUSENTE, ORIGEN_NINGUNO))[0]
        if (t == AUSENTE or (incluir_preliminares and t == PRELIMINAR)
                or (renovar_desde is not None and d >= renovar_desde and t != FINAL)):
            salida.append(d)
        d += timedelta(days=1)
    return salida


def ultima_obtencion() -> date | None:
    """Fecha de la escritura más reciente de la caché (fecha de acceso para las referencias)."""
    def calcular():
        ultima = 0
        for carpeta in (ruta_diario(), ruta_mensual(), ruta_climatologia()):
            try:
                for p in carpeta.glob("*.npz"):
                    ultima = max(ultima, _mtime(p))
            except OSError:
                pass
        return datetime.fromtimestamp(ultima / 1e9).date() if ultima > 0 else None
    return _agregado("ultima_obtencion", calcular)


def tamano_bytes() -> int:
    total = 0
    for carpeta in (ruta_diario(), ruta_mensual(), ruta_climatologia()):
        if carpeta.exists():
            for p in carpeta.glob("*.npz"):
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    return total


# ---------------------------------------------------------------------------
# Mensual nativo, climatologías y climatología diaria de la región Niño 1+2
# ---------------------------------------------------------------------------
def _guardar_otro(ruta: Path, nombre_bloqueo: str, **arreglos) -> None:
    with _bloqueo(("otro", str(ruta))), _bloqueo_procesos(ruta_bloqueos() / f"{nombre_bloqueo}.lock",
                                                         ESPERA_BLOQUEO_S):
        mt = _escribir_atomico(ruta, **arreglos)
        _memoria_otros.pop(str(ruta), None)
        _memoria_otros[str(ruta)] = (mt, None)
    tocar_generacion()


def _leer_otro(ruta: Path, claves: tuple[str, ...]) -> dict | None:
    """Arreglos `claves` del npz (con memoria por mtime); None si no existe (o estaba dañado: se aparta)."""
    mt = _mtime(ruta)
    if mt < 0:
        return None
    with _bloqueo(("otro", str(ruta))):
        en_mem = _memoria_otros.get(str(ruta))
        if en_mem is not None and en_mem[0] == mt and en_mem[1] is not None:
            return en_mem[1]
        for intento in range(3):
            try:
                with open(ruta, "rb") as fh, np.load(fh, allow_pickle=False) as z:
                    datos = {k: z[k] for k in claves if k in z.files}
                faltan = [k for k in claves if k not in datos]
                if faltan:
                    raise ValueError(f"faltan {', '.join(faltan)}")
                _memoria_otros[str(ruta)] = (mt, datos)
                return datos
            except PermissionError:
                if intento == 2:
                    return None
                time.sleep(0.2)
            except Exception as e:  # noqa: BLE001
                if intento < 2:
                    time.sleep(0.1)
                    continue
                _memoria_otros.pop(str(ruta), None)
                _apartar_danado(ruta, f"{type(e).__name__}: {e}")
                return None
    return None


def guardar_mensual_nativo(meses_iso: list[str], valores: np.ndarray) -> None:
    """Medias mensuales nativas (n, 65, 70) con sus meses «AAAA-MM»."""
    _guardar_otro(ruta_mensual_nativo(), "mensual", valores=np.asarray(valores, dtype=np.float32),
                  meses=np.array(meses_iso, dtype="U7"), obtenido=np.array(datetime.now().isoformat(timespec="seconds")))


def leer_mensual_nativo() -> tuple[list[str], np.ndarray, str] | None:
    d = _leer_otro(ruta_mensual_nativo(), ("valores", "meses", "obtenido"))
    if d is None:
        return None
    return [str(x) for x in d["meses"]], d["valores"].astype(np.float32), str(d["obtenido"])


def guardar_climatologia(periodo: str, valores: np.ndarray, anio_inicio, anio_fin, anios, fuente: str,
                         nino12: np.ndarray | None = None) -> None:
    """Climatología mensual (12, 49, 69) de un periodo, con el primer y último año y el número de años de
    cada mes (periodo efectivo) y el archivo de PSL del que sale."""
    _guardar_otro(ruta_clim(periodo), f"clim-{periodo}", valores=np.asarray(valores, dtype=np.float32),
                  anio_inicio=np.asarray(anio_inicio, dtype=np.int16), anio_fin=np.asarray(anio_fin, dtype=np.int16),
                  anios=np.asarray(anios, dtype=np.int16), fuente=np.array(fuente), periodo=np.array(periodo),
                  nino12=np.asarray(nino12 if nino12 is not None else np.full((12, NR, NR), np.nan), dtype=np.float32),
                  obtenido=np.array(datetime.now().isoformat(timespec="seconds")))


def leer_climatologia(periodo: str) -> dict | None:
    d = _leer_otro(ruta_clim(periodo), ("valores", "anio_inicio", "anio_fin", "anios", "fuente", "periodo",
                                        "nino12", "obtenido"))
    if d is None:
        return None
    if d["valores"].shape != (12, NLAT, NLON):
        _apartar_danado(ruta_clim(periodo), f"forma inesperada {d['valores'].shape}")
        return None
    return {"valores": d["valores"].astype(np.float32), "anio_inicio": [int(x) for x in d["anio_inicio"]],
            "anio_fin": [int(x) for x in d["anio_fin"]], "anios": [int(x) for x in d["anios"]],
            "fuente": str(d["fuente"]), "periodo": str(d["periodo"]), "nino12": d["nino12"].astype(np.float32),
            "obtenido": str(d["obtenido"])}


def climatologias_en_cache() -> list[str]:
    from .constantes import PERIODOS
    return [p for p in PERIODOS if _mtime(ruta_clim(p)) >= 0]


def guardar_ltm_diaria(valores: np.ndarray, fuente: str) -> None:
    """Climatología diaria 1991-2020 de la región Niño 1+2: (365, 40, 40), día del año 1–365 (año no bisiesto)."""
    valores = np.asarray(valores, dtype=np.float32)
    if valores.shape != (365, NR, NR):
        raise ValueError(f"Climatología diaria con forma {valores.shape}; se esperaba (365, {NR}, {NR}).")
    _guardar_otro(ruta_ltm_diaria(), "ltm-diaria", valores=valores, fuente=np.array(fuente),
                  obtenido=np.array(datetime.now().isoformat(timespec="seconds")))


def leer_ltm_diaria() -> np.ndarray | None:
    d = _leer_otro(ruta_ltm_diaria(), ("valores", "fuente", "obtenido"))
    if d is None or d["valores"].shape != (365, NR, NR):
        return None
    return d["valores"].astype(np.float32)


def olvidar_memoria() -> None:
    """Vacía las cachés en memoria (pruebas y cambios de raíz)."""
    with _bloqueo_memoria:
        _memoria.clear()
    _memoria_otros.clear()
    _resumenes.clear()
    _agregados.clear()
