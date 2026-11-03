"""Plan y ejecución de las descargas de Copernicus Marine: leer → interpolar → regrillar → guardar.

Plan (`planificar`): para cada dataset (thetao, so, corrientes uo+vo, 2D zos+mlotst)
y los niveles y días que faltan en las celdas pedidas, calcula el costo en
fragmentos ARCO de las dos opciones y elige el menor (empate → geo):

* **geo** (`arco-geo-series`): días exactos; ventana alineada a los fragmentos
  de 512 × 2048 (en la práctica el área SOME completa, 1 o 2 fragmentos por día
  y nivel). Costo = días × niveles nativos × fragmentos espaciales × variables.
* **serie** (`arco-time-series`): ventana de las celdas pedidas AMPLIADA a los
  bordes de fragmento (32 × 64 en 3D, 32 × 32 en 2D) y días ampliados a los
  bloques de 134 días contados desde el 01/06/2022 (recortados a
  [01/10/2023, corte]): lo leído de más no cuesta más y se guarda. Costo =
  bloques × fragmentos de profundidad × fragmentos espaciales × variables.

El planificador nunca pide días posteriores al último día de análisis y
`_procesar` nunca guarda días de simulación o pronóstico aunque lleguen dentro
de un fragmento.

Ejecución (`ejecutar`): un bloqueo por dataset (en el proceso); tras tomarlo se
vuelve a mirar la caché (otra consulta pudo haberlo traído); la lectura corre en
un hilo con tiempo máximo y, si una consulta deja de esperar
(`ErrorDescargaEnCurso`), el hilo sigue y guarda lo que llegue. Una falla de
red marca el servicio «caído» durante `CAIDO_S`; credenciales inválidas quedan
registradas hasta que el usuario vuelva a ejecutar «copernicusmarine login».
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .. import configuracion as cfg
from . import acceso, almacen, calendario, estado, malla, niveles
from .constantes import (COLUMNA0, COLUMNAS_NATIVAS, DATASET_POR_VARIABLE, DIAS_BLOQUE_SERIE, FILA0, FILAS_NATIVAS,
                         GEOMETRIA, INICIO_ANFC, MIB_FRAGMENTO, NO_OBTENIDO, ANALISIS_NRT, MEJOR_ANALISIS,
                         NIVELES_SOME, SERVICIO_GEO, SERVICIO_SERIE, TIEMPO_MAXIMO_LECTURA_S, UNIDADES_ACEPTADAS,
                         VARIABLES, VARIABLES_3D, ErrorDescargaEnCurso, ErrorFormato,
                         ErrorSinAcceso, ErrorSinCredenciales)

log = logging.getLogger("some.copernicus")


@dataclass(frozen=True)
class Tramo:
    dataset_id: str
    variables: tuple[str, ...]
    servicio: str                       # "arco-geo-series" | "arco-time-series"
    lat: tuple[float, float]
    lon: tuple[float, float]
    inicio: date
    fin: date
    niveles_nativos: tuple[float, ...] | None
    niveles_some: tuple[int, ...] | None
    fragmentos: int
    # Lo que se pidió (para volver a mirar la caché tras tomar el bloqueo del dataset)
    filas_some: tuple[int, int] = (0, 48)
    columnas_some: tuple[int, int] = (0, 68)
    dias_pedidos: tuple[date, date] | None = None
    renovar: bool = False
    clase: str = field(default="geo")   # geometría de fragmento: geo | serie3d | serie2d

    @property
    def mib(self) -> float:
        """Estimación del volumen sin comprimir (MiB)."""
        return self.fragmentos * MIB_FRAGMENTO.get(self.clase, 2.0)

    def describir(self) -> str:
        nivel = (f"{len(self.niveles_some)} niveles" if self.niveles_some and len(self.niveles_some) > 1 else
                 f"{self.niveles_some[0]} m" if self.niveles_some else "superficie")
        return (f"{'+'.join(self.variables)} {nivel}, {cfg.fmt_fecha(self.inicio)} – {cfg.fmt_fecha(self.fin)}, "
                f"{'geo' if self.servicio == SERVICIO_GEO else 'serie'}, {self.fragmentos} fragmentos")


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
def _grupos(variables) -> dict[str, list[str]]:
    grupos: dict[str, list[str]] = {}
    for v in variables:
        if v not in VARIABLES:
            raise ValueError(f"Variable de Copernicus Marine desconocida: «{v}».")
        lista = grupos.setdefault(DATASET_POR_VARIABLE[v], [])
        if v not in lista:
            lista.append(v)
    return grupos


def _alinear(primera: int, ultima: int, origen_global: int, tamano: int, total: int) -> tuple[int, int, int]:
    """(primera, última, fragmentos) de las filas/columnas locales ampliadas a los bordes de fragmento y
    recortadas a la ventana; `origen_global` es el índice global de la fila/columna local 0."""
    g0, g1 = origen_global + primera, origen_global + ultima
    c0, c1 = g0 // tamano, g1 // tamano
    a0 = max(c0 * tamano - origen_global, 0)
    a1 = min((c1 + 1) * tamano - 1 - origen_global, total - 1)
    return a0, a1, c1 - c0 + 1


def _bloque(d: date) -> int:
    return (d - INICIO_ANFC).days // DIAS_BLOQUE_SERIE


def _inicio_bloque(b: int) -> date:
    return INICIO_ANFC + timedelta(days=b * DIAS_BLOQUE_SERIE)


def _primer_dia() -> date:
    return max(cfg.FECHA_INICIO, INICIO_ANFC)


def _faltantes(variable: str, nivel, inicio: date, fin: date, ilat, ilon, renovar: bool,
               boletin: date) -> np.ndarray:
    """(n,) bool: días del intervalo a los que les falta algo en las celdas pedidas."""
    lec = almacen.leer(variable, nivel, inicio, fin, ilat, ilon)
    if renovar:
        ahora_definitivo = np.array([calendario.tipo_dia(d, boletin) == MEJOR_ANALISIS for d in lec.fechas])
        return ((lec.tipo == ANALISIS_NRT).any(axis=(1, 2)) & ahora_definitivo) if lec.fechas else np.zeros(0, bool)
    return (lec.tipo == NO_OBTENIDO).any(axis=(1, 2)) if lec.fechas else np.zeros(0, bool)


def planificar(variables, niveles_some, inicio: date, fin: date, ilat, ilon, solo_faltantes: bool = True, *,
               renovar: bool = False, corte: date | None = None, boletin: date | None = None) -> list[Tramo]:
    """Tramos necesarios para tener en la caché `variables` × `niveles_some` en [inicio, fin] y las celdas
    `ilat` × `ilon` (índices SOME contiguos). `renovar=True` planifica solo los días guardados como NRT que
    ya son mejor análisis. Nunca pide días posteriores al último día de análisis."""
    boletin = boletin or estado.boletin_vigente()
    corte = min(corte or boletin - timedelta(days=2), boletin - timedelta(days=2))
    inicio, fin = max(inicio, _primer_dia()), min(fin, corte)
    if fin < inicio:
        return []
    ilat = np.atleast_1d(np.asarray(ilat, dtype=np.int64))
    ilon = np.atleast_1d(np.asarray(ilon, dtype=np.int64))
    if ilat.size == 0 or ilon.size == 0:
        return []
    f0, f1 = malla.filas_nativas_de(ilat)
    k0, k1 = malla.columnas_nativas_de(ilon)
    filas_some = (int(ilat.min()), int(ilat.max()))
    columnas_some = (int(ilon.min()), int(ilon.max()))
    tramos: list[Tramo] = []
    for dataset_id, vars_ds in _grupos(variables).items():
        es3d = vars_ds[0] in VARIABLES_3D
        lista_niveles = sorted({int(n) for n in (niveles_some or NIVELES_SOME)}) if es3d else [None]
        for n in lista_niveles:
            if n is not None and n not in NIVELES_SOME:
                raise ValueError(f"Nivel {n} m no válido para Copernicus Marine en el SOME.")
        if solo_faltantes or renovar:
            dias_falta = np.zeros((fin - inicio).days + 1, dtype=bool)
            niveles_falta = []
            for n in lista_niveles:
                falta_n = np.zeros_like(dias_falta)
                for v in vars_ds:
                    falta_n |= _faltantes(v, n, inicio, fin, ilat, ilon, renovar, boletin)
                if falta_n.any():
                    niveles_falta.append(n)
                    dias_falta |= falta_n
            if not dias_falta.any():
                continue
            idx = np.nonzero(dias_falta)[0]
            d0, d1 = inicio + timedelta(days=int(idx[0])), inicio + timedelta(days=int(idx[-1]))
        else:
            niveles_falta, d0, d1 = lista_niveles, inicio, fin
        nativos = tuple(niveles.niveles_necesarios(niveles_falta)) if es3d else None
        n_vars = len(vars_ds)

        # --- geo: días exactos, ventana alineada a 512 × 2048
        g = GEOMETRIA["geo"]
        gf0, gf1, nf = _alinear(f0, f1, FILA0, g["lat"], FILAS_NATIVAS)
        gc0, gc1, nc = _alinear(k0, k1, COLUMNA0, g["lon"], COLUMNAS_NATIVAS)
        dias = (d1 - d0).days + 1
        costo_geo = dias * (len(nativos) if nativos else 1) * nf * nc * n_vars
        lat_g, lon_g = malla.limites_geograficos(gf0, gf1, gc0, gc1)
        geo = Tramo(dataset_id, tuple(vars_ds), SERVICIO_GEO, lat_g, lon_g, d0, d1, nativos,
                    tuple(niveles_falta) if es3d else None, costo_geo, filas_some, columnas_some, (d0, d1), renovar,
                    "geo")

        # --- serie: ventana ampliada a los fragmentos y días a los bloques de 134 días
        clase = "serie3d" if es3d else "serie2d"
        s = GEOMETRIA[clase]
        sf0, sf1, nf_s = _alinear(f0, f1, FILA0, s["lat"], FILAS_NATIVAS)
        sc0, sc1, nc_s = _alinear(k0, k1, COLUMNA0, s["lon"], COLUMNAS_NATIVAS)
        b0, b1 = _bloque(d0), _bloque(d1)
        s_ini = max(_inicio_bloque(b0), _primer_dia())
        s_fin = min(_inicio_bloque(b1 + 1) - timedelta(days=1), corte)
        n_prof = niveles.fragmentos_profundidad(nativos, s["prof"]) if nativos else 1
        costo_serie = (b1 - b0 + 1) * n_prof * nf_s * nc_s * n_vars
        lat_s, lon_s = malla.limites_geograficos(sf0, sf1, sc0, sc1)
        serie = Tramo(dataset_id, tuple(vars_ds), SERVICIO_SERIE, lat_s, lon_s, s_ini, s_fin, nativos,
                      tuple(niveles_falta) if es3d else None, costo_serie, filas_some, columnas_some, (d0, d1),
                      renovar, clase)
        tramos.append(geo if costo_geo <= costo_serie else serie)
    return tramos


def tramo_prueba(boletin: date | None = None) -> Tramo:
    """Lectura de prueba de la verificación: zos en la celda SOME de (−2, −85) del último día de análisis
    (1 fragmento del servicio de series)."""
    from .constantes import DATASET_2D, PUNTO_PRUEBA
    boletin = boletin or estado.boletin_vigente()
    dia = boletin - timedelta(days=2)
    i = int(np.argmin(np.abs(cfg.LAT - PUNTO_PRUEBA[0])))
    j = int(np.argmin(np.abs(cfg.LON - PUNTO_PRUEBA[1])))
    lat, lon = malla.limites_geograficos(3 * i, 3 * i + 2, 3 * j, 3 * j + 2)
    return Tramo(DATASET_2D, ("zos",), SERVICIO_SERIE, lat, lon, dia, dia, None, None, 1, (i, i), (j, j),
                 (dia, dia), False, "serie2d")


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------
_tabla = threading.Lock()
_bloqueos_dataset: dict[str, threading.Lock] = {}
_en_curso: dict[tuple, float] = {}


def _bloqueo_dataset(dataset_id: str) -> threading.Lock:
    with _tabla:
        if dataset_id not in _bloqueos_dataset:
            _bloqueos_dataset[dataset_id] = threading.Lock()
        return _bloqueos_dataset[dataset_id]


def en_curso() -> list[str]:
    """Descripciones de los tramos que se están descargando ahora en este proceso."""
    with _tabla:
        return [k[-1] for k in _en_curso]


def _sigue_faltando(t: Tramo, boletin: date) -> bool:
    if t.dias_pedidos is None:
        return True
    d0, d1 = t.dias_pedidos
    ilat = np.arange(t.filas_some[0], t.filas_some[1] + 1)
    ilon = np.arange(t.columnas_some[0], t.columnas_some[1] + 1)
    for v in t.variables:
        for n in (t.niveles_some or (None,)):
            if _faltantes(v, n, d0, d1, ilat, ilon, t.renovar, boletin).any():
                return True
    return False


def _leer_con_tiempo(t: Tramo):
    """acceso.leer_ventana en un hilo con tiempo máximo (si se agota, el hilo sigue pero se informa)."""
    caja: dict = {}

    def trabajo():
        try:
            caja["ds"] = acceso.leer_ventana(t.dataset_id, t.variables, t.servicio, t.lat, t.lon, t.inicio, t.fin,
                                             t.niveles_nativos)
        except BaseException as e:  # noqa: BLE001
            caja["e"] = e

    hilo = threading.Thread(target=trabajo, name="copernicus-lectura", daemon=True)
    hilo.start()
    hilo.join(TIEMPO_MAXIMO_LECTURA_S)
    if hilo.is_alive():
        err = ErrorSinAcceso(f"La lectura de Copernicus Marine superó {TIEMPO_MAXIMO_LECTURA_S} s")
        err.es_red = True
        raise err
    if "e" in caja:
        raise caja["e"]
    return caja["ds"]


def _nombre_coord(ds, opciones) -> str:
    for nombre in opciones:
        if nombre in ds.coords or nombre in ds.dims:
            return nombre
    raise ErrorFormato(f"Falta la coordenada {opciones[0]} en la lectura de Copernicus Marine.")


def _validar_unidades(variable: str, unidades) -> None:
    if unidades is None or str(unidades).strip() == "":
        log.warning("Copernicus: %s sin atributo de unidades; se asumen las del producto.", variable)
        return
    if str(unidades).strip().lower() not in UNIDADES_ACEPTADAS[variable]:
        raise ErrorFormato(f"Unidades inesperadas para {variable}: «{unidades}».")


def _procesar(t: Tramo, ds, boletin: date) -> dict:
    """Interpola, regrilla y guarda una lectura. Devuelve {"celdas", "dias_nrt", "dias_definitivos"}."""
    corte = boletin - timedelta(days=2)
    nombre_lat = _nombre_coord(ds, ("latitude", "lat"))
    nombre_lon = _nombre_coord(ds, ("longitude", "lon"))
    filas, cols = malla.indices_nativos(ds[nombre_lat].values, ds[nombre_lon].values)
    if "time" not in ds.coords:
        raise ErrorFormato("Falta la coordenada de tiempo en la lectura de Copernicus Marine.")
    tiempos = pd.to_datetime(ds["time"].values)
    if len(tiempos) and (tiempos != tiempos.normalize()).any():
        raise ErrorFormato("El eje de tiempo de Copernicus Marine no es diario a las 00:00.")
    fechas = [x.date() for x in tiempos]
    seleccion = [k for k, d in enumerate(fechas)
                 if _primer_dia() <= d <= corte and calendario.tipo_dia(d, boletin) > NO_OBTENIDO]
    if not seleccion:
        return {"celdas": 0, "dias_nrt": 0, "dias_definitivos": 0}
    fechas_sel = [fechas[k] for k in seleccion]
    tipos_sel = [calendario.tipo_dia(d, boletin) for d in fechas_sel]
    version = str(estado.version_producto() or "")
    celdas = 0
    for v in t.variables:
        if v not in ds.data_vars:
            raise ErrorFormato(f"La lectura de {t.dataset_id} no trae la variable {v}.")
        _validar_unidades(v, ds[v].attrs.get("units"))
        da = ds[v]
        if t.niveles_some:
            if "depth" not in da.dims:
                raise ErrorFormato(f"{v} no tiene la dimensión de profundidad.")
            arr = da.transpose("time", "depth", nombre_lat, nombre_lon).values.astype(np.float32)
            arr = niveles.interpolar(arr, ds["depth"].values, t.niveles_some, eje=1)
            lista_niveles = list(t.niveles_some)
        else:
            if "depth" in da.dims:
                da = da.isel(depth=0)
            arr = da.transpose("time", nombre_lat, nombre_lon).values.astype(np.float32)[:, None]
            lista_niveles = [None]
        arr = arr[seleccion]
        for j, nivel in enumerate(lista_niveles):
            lienzo, cubierto = malla.lienzo(len(seleccion))
            malla.colocar(lienzo, cubierto, arr[:, j], filas, cols)
            valores, obtenidas = malla.agregar_3x3(lienzo, cubierto)
            if not obtenidas.any():
                continue
            celdas += almacen.guardar(v, nivel, fechas_sel, valores, obtenidas, tipos_sel, boletin,
                                      dataset_id=t.dataset_id, version=version)
    return {"celdas": celdas, "dias_nrt": sum(1 for x in tipos_sel if x == ANALISIS_NRT),
            "dias_definitivos": sum(1 for x in tipos_sel if x == MEJOR_ANALISIS)}


def registrar_fallo(e: BaseException, contexto: str = "") -> None:
    """Anota en el estado una falla de lectura (credenciales, red o formato). Nunca lanza."""
    try:
        prefijo = f"{contexto}: " if contexto else ""
        if getattr(e, "credenciales_invalidas", False):
            estado.registrar_credenciales("invalidas", str(e))
        elif isinstance(e, ErrorSinCredenciales):
            # El toolbox no encontró credenciales donde el SOME creía que estaban (p. ej. un _netrc sin cuenta
            # de Copernicus Marine): se trata como credenciales no válidas hasta un nuevo «copernicusmarine login».
            estado.registrar_credenciales("invalidas", str(e))
        elif getattr(e, "es_red", False):
            # El contexto («Descarga de thetao: ») solo va al registro: la caída la leen todas las consultas
            estado.registrar_caida(str(e), prefijo)
        else:
            estado.registrar_error(f"{prefijo}{e}")
    except Exception:  # noqa: BLE001
        log.exception("Copernicus: no se pudo registrar una falla")


def _resumen_vacio() -> dict:
    return {"tramos": 0, "tramos_omitidos": 0, "fragmentos": 0, "segundos": 0.0, "celdas": 0, "dias_nrt": 0,
            "dias_definitivos": 0, "errores": 0, "detalle_errores": []}


def _ejecutar(tramos: list[Tramo], detener: threading.Event | None = None) -> dict:
    resumen = _resumen_vacio()
    boletin = estado.boletin_vigente()
    for t in tramos:
        if detener is not None and detener.is_set():
            break
        with _bloqueo_dataset(t.dataset_id):
            if not _sigue_faltando(t, boletin):
                resumen["tramos_omitidos"] += 1
                continue
            clave = (t.dataset_id, t.servicio, t.inicio, t.fin, t.describir())
            with _tabla:
                _en_curso[clave] = time.time()
            t0 = time.perf_counter()
            try:
                ds = _leer_con_tiempo(t)
                r = _procesar(t, ds, boletin)
                del ds
                estado.registrar_servicio(True)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:  # noqa: BLE001
                err = acceso.traducir(e)
                registrar_fallo(err, f"Descarga de {'+'.join(t.variables)}")
                if err is e:
                    raise
                raise err from e
            finally:
                segundos = time.perf_counter() - t0
                with _tabla:
                    _en_curso.pop(clave, None)
            estado.registrar_descarga(t.fragmentos, segundos)
            resumen["tramos"] += 1
            resumen["fragmentos"] += t.fragmentos
            resumen["segundos"] = round(resumen["segundos"] + segundos, 1)
            for k in ("celdas", "dias_nrt", "dias_definitivos"):
                resumen[k] += int(r[k])
            log.info("Copernicus: %s en %.1f s (%d celdas-día guardadas).", t.describir(), segundos, r["celdas"])
    return resumen


def ejecutar(tramos: list[Tramo], espera_s: float | None = None, detener: threading.Event | None = None) -> dict:
    """Ejecuta los tramos (leer → interpolar → regrillar → guardar).

    Con `espera_s`, la ejecución corre en un hilo y, si no termina a tiempo, lanza `ErrorDescargaEnCurso`
    mientras el hilo sigue y guarda lo que llegue. Ante la primera falla lanza el error traducido
    (`ErrorSinAcceso`, `ErrorSinCredenciales`, `ErrorFormato`, `ErrorCopernicus`), ya registrado en el estado.
    """
    if not tramos:
        return _resumen_vacio()
    if espera_s is None:
        return _ejecutar(tramos, detener)
    caja: dict = {}

    def trabajo():
        try:
            caja["r"] = _ejecutar(tramos, detener)
        except BaseException as e:  # noqa: BLE001
            caja["e"] = e

    hilo = threading.Thread(target=trabajo, name="copernicus-descarga", daemon=True)
    hilo.start()
    hilo.join(max(0.0, float(espera_s)))
    if hilo.is_alive():
        total = sum(t.fragmentos for t in tramos)
        raise ErrorDescargaEnCurso(
            f"La descarga de Copernicus Marine ({total} {'fragmento' if total == 1 else 'fragmentos'}) superó "
            f"{cfg.fmt_num(espera_s, 0)} s y sigue en segundo plano; vuelva a consultar en unos segundos.")
    if "e" in caja:
        raise caja["e"]
    return caja["r"]
