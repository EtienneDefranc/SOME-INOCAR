"""Caché local de CHIRPS v3 en la malla SOME.

Diario: `datos/chirps/diario/AAAA-MM.npz` con
  * `valores`  (días_del_mes, 49, 69) float32, NaN = sin dato;
  * `tipo`     (días_del_mes,) int8: 0 ausente, 1 preliminar, 2 final;
  * `obtenido` (días_del_mes,) texto ISO del momento de descarga ("" si ausente);
  * `origen`   (días_del_mes,) int8: 0 ninguno, 1 servidor del CHC, 2 réplica de
    dynamical.org, 3 final provisional del producto a 0.25° del CHC («p25», ver
    `constantes.ORIGEN_CHC_P25`). Los archivos antiguos sin este arreglo se leen
    como «chc» (todo lo anterior a la réplica se descargó del CHC);
  * `formato`  escalar int8 = 2. Los archivos sin `formato` son anteriores al cambio
    del final del CHC de 0.25° a 0.05°: sus días finales del CHC se marcan al
    leerlos como «p25» (provisionales), para que la sincronización los reemplace.

Mensual (climatología): `datos/chirps/mensual/AAAA.npz` con `valores`
(12, 49, 69) float32 en mm/día.

Concurrencia: escritura atómica (archivo temporal con fsync + `os.replace`),
un bloqueo por mes ENTRE PROCESOS (archivo `.bloqueos/AAAA-MM.lock`, ver
`bloqueo.py`) dentro del cual `guardar` vuelve a leer el mes del disco, aplica
la regla de reemplazo y escribe; y una caché en memoria de los meses cargados
que se invalida si el archivo cambia en disco (p. ej. porque lo actualizó el
script de preparación en otro proceso).

Archivos dañados (truncados, de 0 bytes o con otra forma): se apartan como
`AAAA-MM.npz.danado` / `AAAA.npz.danado` y el mes o el año se tratan como
ausentes, de modo que la sincronización (o `guardar`) los rehace.

Regla de reemplazo (`guardar` / `mejora`): un día solo se escribe si mejora lo
guardado (ausente → preliminar → final). Un final nunca se sobrescribe con un
preliminar y, dentro del mismo tipo, un día ya guardado no se vuelve a
escribir (el CHC y la réplica publican el mismo producto), salvo un final
provisional «p25», que se reemplaza por el final a 0.05° (réplica o CHC).
"""

from __future__ import annotations

import calendar
import logging
import os
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from .. import configuracion as cfg
from .bloqueo import bloqueo as _bloqueo_procesos
from .constantes import (AUSENTE, CHC, CODIGO_ORIGEN, ESPEJO, FINAL, NOMBRE_ORIGEN, ORIGEN_CHC, ORIGEN_CHC_P25,
                         ORIGEN_ESPEJO, ORIGEN_NINGUNO, PRELIMINAR, RUTA_DIARIO, RUTA_MENSUAL)

log = logging.getLogger("some.chirps")

NLAT, NLON = len(cfg.LAT), len(cfg.LON)
FORMATO = 2
ESPERA_BLOQUEO_S = 120            # un mes nunca debería estar bloqueado tanto (escrituras de ~50 kB)

_bloqueo_global = threading.Lock()
_bloqueos: dict[tuple[int, int], threading.Lock] = {}
_memoria: dict[tuple[int, int], tuple[float, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
_ultimo_dia_cache: tuple[float, date | None] | None = None
_origenes_cache: tuple[float, dict] | None = None
_escrituras = 0                   # escrituras de este proceso (forma parte de `firma_cache`)


def _bloqueo(anio: int, mes: int) -> threading.Lock:
    with _bloqueo_global:
        if (anio, mes) not in _bloqueos:
            _bloqueos[(anio, mes)] = threading.Lock()
        return _bloqueos[(anio, mes)]


def ruta_mes(anio: int, mes: int):
    return RUTA_DIARIO / f"{anio:04d}-{mes:02d}.npz"


def ruta_bloqueos() -> Path:
    """Carpeta de los archivos de bloqueo entre procesos (sigue a RUTA_DIARIO, que las pruebas redirigen)."""
    return Path(RUTA_DIARIO).parent / ".bloqueos"


def _vacio(anio: int, mes: int):
    n = calendar.monthrange(anio, mes)[1]
    return (np.full((n, NLAT, NLON), np.nan, dtype=np.float32),
            np.zeros(n, dtype=np.int8),
            np.array([""] * n, dtype="U19"),
            np.zeros(n, dtype=np.int8))


def _mtime(ruta) -> float:
    try:
        return ruta.stat().st_mtime_ns
    except FileNotFoundError:
        return -1.0


def _apartar_danado(ruta: Path, motivo: str) -> None:
    """Renombra un archivo ilegible a «.danado» (para que se rehaga) y lo registra. Nunca lanza."""
    destino = ruta.with_name(ruta.name + ".danado")
    try:
        if destino.exists():
            destino = ruta.with_name(f"{ruta.name}.{datetime.now():%Y%m%d%H%M%S}.danado")
        os.replace(ruta, destino)
        log.warning("CHIRPS: archivo de caché dañado (%s); se apartó como %s y se volverá a obtener.",
                    motivo, destino.name)
    except OSError:
        log.warning("CHIRPS: archivo de caché dañado (%s): %s; no se pudo apartar.", motivo, ruta)
    try:
        from . import estado          # importación perezosa (estado importa almacen)
        estado.registrar_error(f"Caché local: el archivo {ruta.name} estaba dañado ({motivo}); se volverá a obtener.")
    except Exception:  # noqa: BLE001 — el registro nunca debe impedir seguir
        pass


def _leer_npz_mes(ruta: Path, anio: int, mes: int):
    n = calendar.monthrange(anio, mes)[1]
    # Se abre el archivo aquí (y no con np.load(ruta)): si np.load falla con un zip dañado, deja su
    # descriptor abierto hasta la recolección de basura y en Windows el archivo no se podría apartar.
    with open(ruta, "rb") as fh, np.load(fh, allow_pickle=False) as z:
        v = z["valores"].astype(np.float32)
        t = z["tipo"].astype(np.int8)
        o = z["obtenido"].astype("U19")
        if "origen" in z.files:
            org = z["origen"].astype(np.int8)
        else:       # archivo anterior a la réplica: todo vino del CHC
            org = np.where(t > AUSENTE, ORIGEN_CHC, ORIGEN_NINGUNO).astype(np.int8)
        formato = int(z["formato"]) if "formato" in z.files else 1
    if v.shape != (n, NLAT, NLON) or t.shape != (n,) or o.shape != (n,) or org.shape != (n,):
        raise ValueError(f"formas inesperadas {v.shape}, {t.shape}, {o.shape}, {org.shape}")
    if formato < 2:
        # Antes de este formato el final del CHC se tomaba del producto a 0.25° («p25»).
        org = np.where((t == FINAL) & (org == ORIGEN_CHC), ORIGEN_CHC_P25, org).astype(np.int8)
    return v, t, o, org


def _cargar_mes(anio: int, mes: int, estricto: bool = False):
    """(valores, tipo, obtenido, origen) del mes; usa la memoria si el archivo no cambió. Llamar con el bloqueo.

    Con `estricto` (lo usa `guardar`), un archivo que no se puede abrir por estar en uso lanza
    PermissionError en lugar de servirse como mes vacío (así nunca se escribe encima perdiendo días).
    """
    ruta = ruta_mes(anio, mes)
    mt = _mtime(ruta)
    en_mem = _memoria.get((anio, mes))
    if en_mem is not None and en_mem[0] == mt:
        return en_mem[1], en_mem[2], en_mem[3], en_mem[4]
    if mt < 0:
        v, t, o, org = _vacio(anio, mes)
    else:
        for intento in range(3):
            try:
                v, t, o, org = _leer_npz_mes(ruta, anio, mes)
                break
            except PermissionError:      # Windows: otro proceso lo está reemplazando en este instante
                if intento == 2:         # se sirve como ausente SIN guardarlo en memoria: se reintenta luego
                    if estricto:
                        raise
                    log.warning("CHIRPS: no se pudo abrir %s (en uso por otro proceso).", ruta.name)
                    return _vacio(anio, mes)
                time.sleep(0.2)
            except Exception as e:  # noqa: BLE001 — BadZipFile, EOFError, zlib.error, formas…
                if intento < 2:
                    time.sleep(0.1)
                    continue
                _apartar_danado(ruta, f"{type(e).__name__}: {e}")
                mt = _mtime(ruta)
                v, t, o, org = _vacio(anio, mes)   # se tratará como ausente
    _memoria[(anio, mes)] = (mt, v, t, o, org)
    return v, t, o, org


def _escribir_atomico(ruta, **arreglos) -> int:
    """Escribe el npz en un temporal (con fsync) y lo pone en su sitio con `os.replace`.
    Devuelve el mtime (ns) del archivo escrito."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=ruta.stem + ".", suffix=".tmp", dir=ruta.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            np.savez_compressed(f, **arreglos)
            f.flush()
            os.fsync(f.fileno())
        mt = os.stat(tmp).st_mtime_ns          # os.replace conserva el mtime del temporal
        for intento in range(5):
            try:
                os.replace(tmp, ruta)
                break
            except PermissionError:          # Windows: el destino está abierto por otro lector
                if intento == 4:
                    raise
                time.sleep(0.2 * (intento + 1))
        return mt
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _meses(inicio: date, fin: date):
    a, m = inicio.year, inicio.month
    while (a, m) <= (fin.year, fin.month):
        yield a, m
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)


# ---------------------------------------------------------------------------
# Diario
# ---------------------------------------------------------------------------
def leer_con_origen(inicio: date, fin: date) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Valores (n, 49, 69), tipo (n,) y código de origen (n,) de los días [inicio, fin]."""
    partes_v, partes_t, partes_o = [], [], []
    for a, m in _meses(inicio, fin):
        d0 = inicio.day if (a, m) == (inicio.year, inicio.month) else 1
        d1 = fin.day if (a, m) == (fin.year, fin.month) else calendar.monthrange(a, m)[1]
        with _bloqueo(a, m):
            v, t, _o, org = _cargar_mes(a, m)
            partes_v.append(v[d0 - 1:d1].copy())
            partes_t.append(t[d0 - 1:d1].copy())
            partes_o.append(org[d0 - 1:d1].copy())
    if not partes_v:
        return np.empty((0, NLAT, NLON), np.float32), np.empty(0, np.int8), np.empty(0, np.int8)
    return np.concatenate(partes_v), np.concatenate(partes_t), np.concatenate(partes_o)


def leer(inicio: date, fin: date) -> tuple[np.ndarray, np.ndarray]:
    """Valores (n, 49, 69) y tipo (n,) de los días [inicio, fin]."""
    v, t, _org = leer_con_origen(inicio, fin)
    return v, t


def _tipos_codigos(inicio: date, fin: date) -> tuple[np.ndarray, np.ndarray]:
    """Tipo (n,) y código de origen (n,) sin copiar los valores (más liviano que leer_con_origen)."""
    partes_t, partes_o = [], []
    for a, m in _meses(inicio, fin):
        d0 = inicio.day if (a, m) == (inicio.year, inicio.month) else 1
        d1 = fin.day if (a, m) == (fin.year, fin.month) else calendar.monthrange(a, m)[1]
        with _bloqueo(a, m):
            _v, t, _o, org = _cargar_mes(a, m)
            partes_t.append(t[d0 - 1:d1].copy())
            partes_o.append(org[d0 - 1:d1].copy())
    if not partes_t:
        return np.empty(0, np.int8), np.empty(0, np.int8)
    return np.concatenate(partes_t), np.concatenate(partes_o)


def tipos(inicio: date, fin: date) -> dict[date, int]:
    """Tipo guardado (0/1/2) de cada día del intervalo."""
    t, _org = _tipos_codigos(inicio, fin)
    return {inicio + timedelta(days=i): int(x) for i, x in enumerate(t)}


def tipos_y_codigos(inicio: date, fin: date) -> dict[date, tuple[int, int]]:
    """{fecha: (tipo 0/1/2, código de origen 0/1/2/3)} de cada día del intervalo."""
    t, org = _tipos_codigos(inicio, fin)
    return {inicio + timedelta(days=i): (int(x), int(o)) for i, (x, o) in enumerate(zip(t, org))}


def tipos_y_origenes(inicio: date, fin: date) -> dict[date, tuple[int, str]]:
    """{fecha: (tipo 0/1/2, origen "chc" | "espejo" | "")} de cada día del intervalo."""
    t, org = _tipos_codigos(inicio, fin)
    return {inicio + timedelta(days=i): (int(x), NOMBRE_ORIGEN.get(int(o), ""))
            for i, (x, o) in enumerate(zip(t, org))}


def es_provisional(tipo: int, codigo: int) -> bool:
    """True si el día es un final provisional del producto a 0.25° del CHC (se reemplazará)."""
    return int(tipo) == FINAL and int(codigo) == ORIGEN_CHC_P25


def mejora(tipo_guardado: int, tipo_nuevo: int, codigo_guardado: int = ORIGEN_NINGUNO,
           codigo_nuevo: int | None = None) -> bool:
    """Regla de reemplazo: final > preliminar > ausente; el mismo tipo no se reescribe, salvo
    un final provisional «p25» del CHC, que se reemplaza por un final a 0.05° (réplica o CHC)."""
    if tipo_nuevo not in (PRELIMINAR, FINAL):
        return False
    if tipo_nuevo > tipo_guardado:
        return True
    return (es_provisional(tipo_guardado, codigo_guardado) and tipo_nuevo == FINAL
            and codigo_nuevo in (ORIGEN_CHC, ORIGEN_ESPEJO))


def mejora_dia(guardado: tuple[int, int], tipo_nuevo: int, codigo_nuevo: int) -> bool:
    """`mejora` a partir de la tupla (tipo, código de origen) de `tipos_y_codigos`."""
    tipo_g, codigo_g = guardado
    return mejora(tipo_g, tipo_nuevo, codigo_g, codigo_nuevo)


def guardar(dias: dict, origen: str = CHC) -> int:
    """Guarda {fecha: (campo 49×69, tipo)} o {fecha: (campo, tipo, origen)} agrupando por mes.

    `origen` ("chc" o "espejo") se aplica a las entradas que no traen el suyo.
    Solo escribe los días que mejoran lo guardado (ver `mejora`). Cada mes se
    actualiza bajo un bloqueo entre procesos, releyendo el archivo del disco
    dentro del bloqueo: dos procesos que escriben el mismo mes no se pisan.
    Devuelve los días escritos.
    """
    global _ultimo_dia_cache, _origenes_cache, _escrituras
    por_mes: dict[tuple[int, int], list] = {}
    for d, entrada in dias.items():
        campo, tipo = entrada[0], int(entrada[1])
        org = entrada[2] if len(entrada) > 2 else origen
        if org not in CODIGO_ORIGEN:
            raise ValueError(f"Origen desconocido «{org}»; se esperaba «{CHC}» o «{ESPEJO}»")
        por_mes.setdefault((d.year, d.month), []).append((d, campo, tipo, CODIGO_ORIGEN[org]))
    escritos = 0
    ahora = datetime.now().isoformat(timespec="seconds")
    for (a, m), lista in sorted(por_mes.items()):
        with _bloqueo(a, m), _bloqueo_procesos(ruta_bloqueos() / f"{a:04d}-{m:02d}.lock", ESPERA_BLOQUEO_S):
            _memoria.pop((a, m), None)          # releer SIEMPRE del disco bajo el bloqueo
            v, t, o, org = _cargar_mes(a, m, estricto=True)
            v, t, o, org = v.copy(), t.copy(), o.copy(), org.copy()
            cambios = 0
            for d, campo, tipo, cod in lista:
                i = d.day - 1
                if not mejora(int(t[i]), tipo, int(org[i]), cod):
                    continue
                if campo.shape != (NLAT, NLON):
                    raise ValueError(f"Campo con forma {campo.shape}; se esperaba ({NLAT}, {NLON})")
                v[i] = campo.astype(np.float32)
                t[i] = tipo
                o[i] = ahora
                org[i] = cod
                cambios += 1
            if cambios:
                ruta = ruta_mes(a, m)
                mt = _escribir_atomico(ruta, valores=v, tipo=t, obtenido=o, origen=org,
                                       formato=np.array(FORMATO, dtype=np.int8))
                _memoria[(a, m)] = (mt, v, t, o, org)
                escritos += cambios
    if escritos:
        _ultimo_dia_cache = None
        _origenes_cache = None
        _escrituras += 1
    return escritos


def ultimo_dia() -> date | None:
    """Último día con dato (final o preliminar) en la caché local."""
    global _ultimo_dia_cache
    archivos, firma = _firma_archivos()
    if _ultimo_dia_cache is not None and _ultimo_dia_cache[0] == firma:
        return _ultimo_dia_cache[1]
    resultado = None
    for p in reversed(archivos):
        try:
            a, m = int(p.stem[:4]), int(p.stem[5:7])
        except ValueError:
            continue
        with _bloqueo(a, m):
            _v, t, _o, _org = _cargar_mes(a, m)
        con_dato = np.nonzero(t > AUSENTE)[0]
        if con_dato.size:
            resultado = date(a, m, int(con_dato[-1]) + 1)
            break
    _ultimo_dia_cache = (firma, resultado)
    return resultado


def _firma_archivos() -> tuple[list, float]:
    try:
        archivos = sorted(Path(RUTA_DIARIO).glob("????-??.npz"))
    except OSError:
        archivos = []
    return archivos, max((_mtime(p) for p in archivos), default=-1.0) + len(archivos)


def firma_cache() -> str:
    """Firma barata de TODA la caché (diaria y mensual, de este u otro proceso): cambia en cuanto se
    escribe un día, un año de climatología o su registro de origen. La usa la caché de respuestas."""
    _archivos, f_diario = _firma_archivos()
    try:
        mensual = [p for p in Path(RUTA_MENSUAL).glob("*") if p.suffix in (".npz", ".json")]
    except OSError:
        mensual = []
    f_mensual = max((_mtime(p) for p in mensual), default=-1.0) + len(mensual)
    return f"{f_diario:.0f}:{f_mensual:.0f}:{_escrituras}"


def _contar_origenes(t: np.ndarray, org: np.ndarray) -> dict[str, int]:
    con_dato = t > AUSENTE
    return {CHC: int((con_dato & ((org == ORIGEN_CHC) | (org == ORIGEN_CHC_P25))).sum()),
            ESPEJO: int((con_dato & (org == ORIGEN_ESPEJO)).sum())}


def conteo_origenes() -> dict[str, int]:
    """Días guardados por origen en toda la caché: {"chc": n, "espejo": n} (con caché en memoria)."""
    global _origenes_cache
    archivos, firma = _firma_archivos()
    if _origenes_cache is not None and _origenes_cache[0] == firma:
        return dict(_origenes_cache[1])
    cuenta = {CHC: 0, ESPEJO: 0}
    for p in archivos:
        try:
            a, m = int(p.stem[:4]), int(p.stem[5:7])
        except ValueError:
            continue
        with _bloqueo(a, m):
            _v, t, _o, org = _cargar_mes(a, m)
        for k, n in _contar_origenes(t, org).items():
            cuenta[k] += n
    _origenes_cache = (firma, cuenta)
    return dict(cuenta)


def conteo(inicio: date, fin: date) -> dict:
    """Días final / preliminar / ausente, días por origen y fechas límite en [inicio, fin]."""
    t, org = _tipos_codigos(inicio, fin)
    finales = np.nonzero(t == FINAL)[0]
    prelim = np.nonzero(t == PRELIMINAR)[0]
    return {
        "dias": int(t.size),
        "final": int(finales.size),
        "preliminar": int(prelim.size),
        "ausente": int((t == AUSENTE).sum()),
        "finales_provisionales": int(((t == FINAL) & (org == ORIGEN_CHC_P25)).sum()),
        "por_origen": _contar_origenes(t, org),
        "ultimo_final": (inicio + timedelta(days=int(finales[-1]))) if finales.size else None,
        "primer_preliminar": (inicio + timedelta(days=int(prelim[0]))) if prelim.size else None,
        "ultimo_preliminar": (inicio + timedelta(days=int(prelim[-1]))) if prelim.size else None,
    }


_obtencion_cache: tuple[float, date | None] | None = None


def ultima_obtencion() -> date | None:
    """Fecha de la descarga más reciente guardada en la caché diaria (fecha de acceso al repositorio)."""
    global _obtencion_cache
    archivos, firma = _firma_archivos()
    if _obtencion_cache is not None and _obtencion_cache[0] == firma:
        return _obtencion_cache[1]
    ultimo = ""
    for p in archivos:
        try:
            a, m = int(p.stem[:4]), int(p.stem[5:7])
        except ValueError:
            continue
        with _bloqueo(a, m):
            _v, _t, o, _org = _cargar_mes(a, m)
        if o.size:
            ultimo = max(ultimo, str(max(o)))
    resultado = None
    if ultimo:
        try:
            resultado = date.fromisoformat(ultimo[:10])
        except ValueError:
            resultado = None
    _obtencion_cache = (firma, resultado)
    return resultado


def tamano_bytes() -> int:
    total = 0
    for carpeta in (RUTA_DIARIO, RUTA_MENSUAL):
        carpeta = Path(carpeta)
        if carpeta.exists():
            for p in carpeta.glob("*.npz"):
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    return total


# ---------------------------------------------------------------------------
# Mensual (climatología)
# ---------------------------------------------------------------------------
def ruta_anio_mensual(anio: int):
    return RUTA_MENSUAL / f"{anio:04d}.npz"


_memoria_mensual: dict[int, tuple[float, np.ndarray]] = {}
_bloqueo_mensual = threading.Lock()


def leer_mensual(anio: int) -> np.ndarray | None:
    """(12, 49, 69) mm/día del año, o None si no está en caché (o el archivo está dañado: se aparta)."""
    ruta = ruta_anio_mensual(anio)
    mt = _mtime(ruta)
    if mt < 0:
        return None
    with _bloqueo_mensual:
        en_mem = _memoria_mensual.get(anio)
        if en_mem is not None and en_mem[0] == mt:
            return en_mem[1]
        for intento in range(3):
            try:
                with open(ruta, "rb") as fh, np.load(fh, allow_pickle=False) as z:
                    v = z["valores"].astype(np.float32)
                if v.shape != (12, NLAT, NLON):
                    raise ValueError(f"forma inesperada {v.shape}")
                break
            except PermissionError:
                if intento == 2:
                    return None
                time.sleep(0.2)
            except Exception as e:  # noqa: BLE001 — BadZipFile, EOFError, zlib.error, forma…
                if intento < 2:
                    time.sleep(0.1)
                    continue
                _memoria_mensual.pop(anio, None)
                _apartar_danado(ruta, f"{type(e).__name__}: {e}")
                return None
        _memoria_mensual[anio] = (mt, v)
        return v


def guardar_mensual(anio: int, valores: np.ndarray) -> None:
    global _escrituras
    if valores.shape != (12, NLAT, NLON):
        raise ValueError(f"Climatología mensual con forma {valores.shape}; se esperaba (12, {NLAT}, {NLON})")
    ruta = ruta_anio_mensual(anio)
    with _bloqueo_mensual:
        mt = _escribir_atomico(ruta, valores=valores.astype(np.float32),
                               obtenido=np.array(datetime.now().isoformat(timespec="seconds")))
        _memoria_mensual[anio] = (mt, valores.astype(np.float32))
        _escrituras += 1


def anios_mensuales() -> list[int]:
    if not Path(RUTA_MENSUAL).exists():
        return []
    return sorted(int(p.stem) for p in Path(RUTA_MENSUAL).glob("????.npz") if p.stem.isdigit())
