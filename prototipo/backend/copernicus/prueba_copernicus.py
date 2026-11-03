"""Pruebas del acceso real a Copernicus Marine (núcleo `backend/copernicus/` y conector).

    python -m backend.copernicus.prueba_copernicus
    SOME_COPERNICUS_SIN_RED=1 python -m backend.copernicus.prueba_copernicus   # sin el bloque real

Las pruebas 1 a 10 son SIN RED: usan carpetas temporales (la caché real
`datos/copernicus/` no se toca) y un módulo `copernicusmarine` FALSO inyectado
en `acceso` (registra cada llamada; `login` devuelve True/False o lanza;
`open_dataset` devuelve un Dataset sintético en la malla nativa de 1/12° con
`time`, `depth`, `latitude` y `longitude`; `describe` devuelve un catálogo
sintético). Las credenciales son un archivo ficticio en la carpeta temporal.

La prueba 11 (bloque real) solo corre si hay credenciales válidas
(`login(check_credentials_valid=True)`, que nunca pregunta) y no está activo el
modo sin red: lee de verdad un día de thetao y un perfil, en otra carpeta
temporal, e imprime tiempos. Sin credenciales: «OMITE» con el motivo.
"""

from __future__ import annotations

import contextlib
import math
import os
import subprocess
import sys
import tempfile
import time
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .. import configuracion as cfg

_RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
_resultados = {"ok": 0, "fallo": 0, "omitida": 0}


def comprobar(condicion: bool, texto: str) -> None:
    if condicion:
        _resultados["ok"] += 1
        print(f"  OK     {texto}")
    else:
        _resultados["fallo"] += 1
        print(f"  FALLO  {texto}")


def omitir(texto: str) -> None:
    _resultados["omitida"] += 1
    print(f"  OMITE  {texto}")


def _lanza(funcion, tipo) -> tuple[bool, str]:
    try:
        funcion()
    except tipo as e:
        return True, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    return False, "no lanzó"


def _anidamiento(texto: str) -> int:
    """Profundidad máxima de paréntesis de un texto (un aviso legible no pasa de 1)."""
    nivel = maximo = 0
    for ch in texto:
        nivel += 1 if ch == "(" else -1 if ch == ")" else 0
        maximo = max(maximo, nivel)
    return maximo


# ---------------------------------------------------------------------------
# Módulo copernicusmarine falso
# ---------------------------------------------------------------------------
UNIDADES = {"thetao": "degrees_C", "so": "1e-3", "uo": "m s-1", "vo": "m s-1", "zos": "m", "mlotst": "m"}


def _ms(d: date) -> float:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000.0


class ModuloFalso(types.ModuleType):
    """copernicusmarine simulado: nunca toca la red."""

    def __init__(self):
        super().__init__("copernicusmarine_falso")
        self.__version__ = "falso-1.0"
        self.llamadas: list[tuple[str, dict]] = []
        self.login_resultado = True            # True | False | excepción
        self.fallo_open: BaseException | None = None
        self.fallo_describe: BaseException | None = None
        self.retraso_s = 0.0
        self.desplazar = 0.0                   # desplaza la malla (para ErrorFormato)
        self.unidades = dict(UNIDADES)
        from .calendario import ahora_utc, boletin_semanal
        self.fin_tiempo = boletin_semanal(ahora_utc()) + timedelta(days=10)
        self.core_functions = types.SimpleNamespace(credentials_utils=types.SimpleNamespace(
            _retrieve_credential_from_prompt=lambda *a, **k: "no-debe-usarse"))

    # --- login (solo con check_credentials_valid=True)
    def login(self, **kw):
        self.llamadas.append(("login", dict(kw)))
        if isinstance(self.login_resultado, BaseException):
            raise self.login_resultado
        return self.login_resultado

    # --- describe
    def describe(self, **kw):
        self.llamadas.append(("describe", dict(kw)))
        if self.fallo_describe is not None:
            raise self.fallo_describe
        ns = types.SimpleNamespace
        coords = [ns(coordinate_id="time", minimum_value=_ms(date(2022, 6, 1)), maximum_value=_ms(self.fin_tiempo),
                     chunking_length=1, values=None),
                  ns(coordinate_id="latitude", minimum_value=-80.0, maximum_value=90.0, chunking_length=512, values=None),
                  ns(coordinate_id="longitude", minimum_value=-180.0, maximum_value=179.92, chunking_length=2048,
                     values=None)]
        servicio = ns(service_name="arco-geo-series", variables=[ns(short_name="zos", coordinates=coords)])
        parte = ns(name="default", arco_updated_date="2026-10-30T02:50:56.910Z", arco_updating_start_date=None,
                   url_metadata=None, services=[servicio])
        dataset = ns(dataset_id=kw.get("dataset_id"), digital_object_identifier="10.48670/moi-00016",
                     versions=[ns(label="202406", parts=[parte])])
        return ns(products=[ns(datasets=[dataset])])

    # --- open_dataset
    def open_dataset(self, **kw):
        self.llamadas.append(("open_dataset", dict(kw)))
        if self.retraso_s:
            time.sleep(self.retraso_s)
        if self.fallo_open is not None:
            raise self.fallo_open
        from .constantes import DATASET_CLIMATOLOGIA
        from .niveles import NIVELES_MODELO

        def eje(minimo, maximo, origen):
            k0 = math.ceil((minimo - origen) * 12 - 1e-6)
            k1 = math.floor((maximo - origen) * 12 + 1e-6)
            return (origen + np.arange(k0, k1 + 1) / 12.0 + self.desplazar).astype(np.float32)

        lat = eje(kw["minimum_latitude"], kw["maximum_latitude"], -80.0)
        lon = eje(kw["minimum_longitude"], kw["maximum_longitude"], -180.0)
        clim = kw["dataset_id"] == DATASET_CLIMATOLOGIA
        if clim:
            tiempos = pd.date_range("2004-01-01", periods=12, freq="MS")
        else:
            tiempos = pd.date_range(kw["start_datetime"][:10], kw["end_datetime"][:10], freq="D")
        depth = np.array(list(NIVELES_MODELO) + [130.66600036621094, 155.85069274902344], dtype=np.float32)
        la = lat[None, :, None].astype(np.float64)
        lo = lon[None, None, :].astype(np.float64)
        tierra = (lo > -80.5) & (la < -3.0)                       # «tierra» en la esquina del golfo
        dia = np.arange(len(tiempos), dtype=np.float64)[:, None, None]
        datos = {}
        for v in kw.get("variables") or []:
            if v == "climatology_bounds":
                ini = np.array([f"1993-{m:02d}-01" for m in range(1, 13)], dtype="datetime64[ns]")
                fin = np.array([(pd.Timestamp(f"2016-{m:02d}-01") + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")
                                for m in range(1, 13)], dtype="datetime64[ns]")
                datos[v] = (("time", "nv"), np.stack([ini, fin], axis=1), {"long_name": "time interval endpoints"})
                continue
            if v in ("thetao", "so", "uo", "vo"):
                z = depth.astype(np.float64)[None, :, None, None]
                base = {"thetao": 28.0 - 0.05 * z, "so": 34.5 + 0.005 * z, "uo": 0.3 + 0.0 * z,
                        "vo": -0.2 + 0.0 * z}[v]
                forma = (len(tiempos), len(depth), len(lat), len(lon))
                arr = np.broadcast_to(base + 0.001 * dia[:, None], forma).copy()
                arr[:, :, tierra[0]] = np.nan
                profundo = depth > 60.0
                plataforma = lon > -81.0                            # fondo a ~60 m cerca de la costa
                fondo = np.broadcast_to(profundo[:, None, None] & plataforma[None, None, :], forma[1:])
                arr[:, fondo] = np.nan
                datos[v] = (("time", "depth", "latitude", "longitude"), arr.astype(np.float32),
                            {"units": self.unidades[v]})
            else:
                base = {"zos": 0.2, "mlotst": 15.0}[v]
                arr = np.broadcast_to(base + 0.01 * dia + 0.0 * la + 0.0 * lo,
                                      (len(tiempos), len(lat), len(lon))).copy()
                arr[:, tierra[0]] = np.nan
                datos[v] = (("time", "latitude", "longitude"), arr.astype(np.float32), {"units": self.unidades[v]})
        coords = {"time": tiempos.values, "latitude": lat, "longitude": lon}
        if any(v in ("thetao", "so", "uo", "vo") for v in kw.get("variables") or []):
            coords["depth"] = depth
        return xr.Dataset(datos, coords=coords)

    def llamadas_de(self, nombre: str) -> list[dict]:
        return [kw for n, kw in self.llamadas if n == nombre]


@contextlib.contextmanager
def entorno(con_credenciales: bool = True, modulo: ModuloFalso | None = None):
    """Caché, estado y credenciales aislados en una carpeta temporal, con el módulo falso inyectado."""
    from . import acceso, almacen, constantes, descarga, estado
    modulo = modulo if modulo is not None else ModuloFalso()
    guardado_env = {k: os.environ.get(k) for k in ("SOME_COPERNICUS_SIN_RED", "SOME_COPERNICUS_MODO",
                                                    "COPERNICUSMARINE_SERVICE_USERNAME",
                                                    "COPERNICUSMARINE_SERVICE_PASSWORD", "SOME_COPERNICUS_ESPERA_S")}
    rutas_originales = acceso.rutas_credenciales
    alcance_original = acceso.red_alcanzable
    with tempfile.TemporaryDirectory(prefix="prueba_copernicus_") as tmp:
        tmp = Path(tmp)
        previa = constantes.fijar_raiz(tmp / "copernicus")
        previo_mod = acceso.usar_modulo(modulo)
        try:
            for k in guardado_env:
                os.environ.pop(k, None)
            cred = tmp / "credenciales" / ".copernicusmarine" / ".copernicusmarine-credentials"
            if con_credenciales:
                cred.parent.mkdir(parents=True, exist_ok=True)
                cred.write_text("ficticio", encoding="ascii")      # contenido falso: nunca se lee
            acceso.rutas_credenciales = lambda: [cred, tmp / "motuclient-python.ini", tmp / "_netrc"]
            acceso.red_alcanzable = lambda *_a, **_k: not constantes.sin_red() and getattr(modulo, "alcanzable", True)
            almacen.olvidar_memoria()
            estado.recargar()
            estado.marcar_verificando(False)
            yield {"tmp": tmp, "modulo": modulo, "credenciales": cred}
        finally:
            acceso.rutas_credenciales = rutas_originales
            acceso.red_alcanzable = alcance_original
            acceso.restaurar(previo_mod)
            constantes.fijar_raiz(previa)
            almacen.olvidar_memoria()
            estado.recargar()
            estado.marcar_verificando(False)
            with descarga._tabla:
                descarga._en_curso.clear()
            for k, v in guardado_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


# ---------------------------------------------------------------------------
# 1. Calendario
# ---------------------------------------------------------------------------
def prueba_calendario() -> None:
    from .calendario import boletin_semanal, tipo_dia, ultimo_dia_analisis
    print("1. Calendario del análisis:")
    comprobar(ultimo_dia_analisis(datetime(2026, 10, 30, 12), date(2026, 11, 8)) == date(2026, 10, 26),
              "30/10/2026 con fin de ARCO 08/11 → W = 28/10, corte 26/10/2026")
    comprobar(ultimo_dia_analisis(datetime(2026, 10, 30, 12)) == date(2026, 10, 26),
              "30/10/2026 sin metadatos → corte 26/10/2026")
    comprobar(ultimo_dia_analisis(datetime(2026, 10, 29, 10)) == date(2026, 10, 19),
              "jueves 29/10 10:00 UTC sin metadatos (aún no se publica el análisis) → corte 19/10")
    comprobar(boletin_semanal(datetime(2026, 11, 5, 8), date(2026, 11, 13)) == date(2026, 10, 28),
              "jueves 05/11 antes de la actualización (fin 13/11) → sigue W = 28/10")
    comprobar(ultimo_dia_analisis(datetime(2026, 11, 5, 20), date(2026, 11, 14)) == date(2026, 11, 2),
              "jueves 05/11 después de la actualización (fin 14/11) → W = 04/11, corte 02/11")
    w = date(2026, 10, 28)
    comprobar([tipo_dia(w - timedelta(days=n), w) for n in (9, 8, 2, 1, 0)] == [2, 1, 1, 0, 0],
              "tipo de día en los bordes W−9 (mejor análisis), W−8 y W−2 (NRT), W−1 y W (no se sirven)")


# ---------------------------------------------------------------------------
# 2. Malla
# ---------------------------------------------------------------------------
def prueba_malla() -> None:
    from . import malla
    from .constantes import ErrorFormato
    print("2. Malla 1/12° → 0.25°:")
    lat = (-80 + (887 + np.arange(147)) / 12).astype(np.float32)
    lon = (-180 + (1019 + np.arange(207)) / 12).astype(np.float32)
    f, c = malla.indices_nativos(lat, lon)
    comprobar(f[0] == 0 and f[-1] == 146 and c[0] == 0 and c[-1] == 206 and len(f) == 147 and len(c) == 207,
              "ventana nativa de 147 × 207 (filas 887–1033, columnas 1019–1225)")
    comprobar(abs(lat[0] + 6.0833) < 1e-3 and abs(lon[-1] + 77.9167) < 1e-3,
              "centros extremos −6.0833° y −77.9167°")
    nativo = np.arange(147 * 207, dtype=np.float32).reshape(147, 207)
    cub = np.ones((147, 207), bool)
    v, ob = malla.agregar_3x3(nativo, cub)
    esperado = nativo[3:6, 6:9].mean()
    comprobar(v.shape == (49, 69) and abs(v[1, 2] - esperado) < 1e-3 and ob.all(),
              "media de un bloque 3 × 3 con patrón conocido (celda SOME 1,2)")
    n2 = nativo.copy()
    n2[0:3, 0:3] = np.nan
    n2[0:2, 3:6] = np.nan                     # bloque (0,1): quedan 3 de 9 válidas
    n2[0, 6:9] = np.nan
    n2[1, 6] = np.nan                         # bloque (0,2): quedan 5 de 9 válidas
    v2, _ = malla.agregar_3x3(n2, cub)
    comprobar(np.isnan(v2[0, 0]) and np.isnan(v2[0, 1]) and np.isfinite(v2[0, 2]),
              "mínimo 5 de 9 válidas (0 y 3 válidas → sin dato; 5 válidas → media)")
    lienzo, cubierto = malla.lienzo(1)
    malla.colocar(lienzo, cubierto, np.ones((1, 10, 20), np.float32), np.arange(30, 40), np.arange(60, 80))
    _v3, ob3 = malla.agregar_3x3(lienzo, cubierto)
    comprobar(ob3.sum() == 3 * 6 and ob3[10, 20] and not ob3[9, 20],
              "lienzo parcial: solo las celdas SOME con sus 9 centros leídos cuentan como obtenidas")
    ok, _ = _lanza(lambda: malla.indices_nativos(lat + 0.02, lon), ErrorFormato)
    comprobar(ok, "coordenadas desplazadas → ErrorFormato")
    ok, _ = _lanza(lambda: malla.indices_nativos(lat[::-1], lon), ErrorFormato)
    comprobar(ok, "latitud descendente → ErrorFormato")


# ---------------------------------------------------------------------------
# 3. Niveles
# ---------------------------------------------------------------------------
def prueba_niveles() -> None:
    from . import niveles
    print("3. Niveles SOME desde los niveles del modelo:")
    tabla = {5: (3.819, 0.9379), 10: (9.573, 0.2331), 20: (18.496, 0.4848), 30: (29.445, 0.1113),
             50: (47.374, 0.3130), 75: (65.807, 0.7631), 100: (92.326, 0.4409)}
    ok = all(abs(niveles.vecinos(z)[0] - a) < 1e-3 and abs(niveles.vecinos(z)[2] - p) < 1e-4
             for z, (a, p) in tabla.items())
    comprobar(ok, "pesos de interpolación de la tabla del diseño (5 a 100 m)")
    a, b, p = niveles.vecinos(0)
    comprobar(abs(a - 0.494) < 1e-3 and a == b and p == 0.0, "0 m = nivel de 0.494 m")
    comprobar(len(niveles.NIVELES_NATIVOS) == 15, "15 niveles nativos para los 8 niveles SOME")
    nat = np.array(niveles.NIVELES_NATIVOS)
    datos = np.broadcast_to((10.0 + nat)[None, :, None, None], (2, 15, 3, 4)).astype(np.float32).copy()
    datos[0, list(nat).index(niveles.vecinos(50)[1]), 0, 0] = np.nan
    r = niveles.interpolar(datos, nat, [0, 5, 50, 100], eje=1)
    comprobar(r.shape == (2, 4, 3, 4), "forma (t, z, y, x) conservada")
    comprobar(abs(r[1, 1, 0, 0] - 15.0) < 1e-3 and abs(r[1, 3, 0, 0] - 110.0) < 1e-3,
              "interpolación lineal exacta (v = 10 + z)")
    comprobar(np.isnan(r[0, 2, 0, 0]) and np.isfinite(r[0, 2, 1, 1]), "sin dato si falta un nivel vecino")
    comprobar(niveles.fragmentos_profundidad(niveles.NIVELES_NATIVOS, 2) == 12
              and niveles.fragmentos_profundidad(niveles.NIVELES_NATIVOS, 4) == 7,
              "los 15 niveles tocan 12 pares (series) y 7 cuartetos (climatología)")


# ---------------------------------------------------------------------------
# 4. Almacén
# ---------------------------------------------------------------------------
_HIJO_ALMACEN = r"""
import sys
from datetime import date
from pathlib import Path
import numpy as np
from backend.copernicus import constantes, almacen
constantes.fijar_raiz(Path(sys.argv[1]))
v = np.full((1, 49, 69), 7.0, np.float32)
cub = np.zeros((49, 69), bool); cub[40:, :] = True
for k in range(15):
    almacen.guardar("thetao", 0, [date(2026, 9, 10 + k)], v, cub, [2], date(2026, 10, 14))
print("ok", flush=True)
"""


def prueba_almacen() -> None:
    from . import almacen
    from .constantes import ruta_diario
    print("4. Almacén (caché por variable, nivel y mes):")
    with entorno():
        d = date(2026, 9, 1)
        v = np.full((1, 49, 69), 20.0, np.float32)
        cub = np.zeros((49, 69), bool)
        cub[:10, :] = True
        m0 = almacen.marca()
        n = almacen.guardar("thetao", 0, [d], v, cub, [1], date(2026, 9, 9))
        lec = almacen.leer("thetao", 0, d, d)
        comprobar(n == 10 * 69 and (lec.tipo[0, :10] == 1).all() and (lec.tipo[0, 10:] == 0).all()
                  and lec.boletin == ["2026-09-09"], "escritura y lectura por celda (tipo y boletín)")
        comprobar(almacen.marca() != m0, "la marca de generación cambia al escribir")
        comprobar(almacen.guardar("thetao", 0, [d], v + 1, cub, [2], date(2026, 9, 16)) == 690
                  and float(almacen.leer("thetao", 0, d, d).valores[0, 0, 0]) == 21.0, "reemplazo NRT → mejor análisis")
        comprobar(almacen.guardar("thetao", 0, [d], v + 5, cub, [1], date(2026, 9, 23)) == 0
                  and float(almacen.leer("thetao", 0, d, d).valores[0, 0, 0]) == 21.0,
                  "un mejor análisis nunca se pisa con un NRT")
        d2 = date(2026, 9, 5)
        almacen.guardar("thetao", 0, [d2], v, cub, [1], date(2026, 9, 9))
        comprobar(almacen.guardar("thetao", 0, [d2], v + 2, cub, [1], date(2026, 9, 16)) == 690
                  and almacen.guardar("thetao", 0, [d2], v + 3, cub, [1], date(2026, 9, 9)) == 0,
                  "NRT con boletín posterior reemplaza; con boletín anterior, no")
        comprobar(almacen.guardar("thetao", 0, [d2], v, cub, [0], date(2026, 9, 16)) == 0,
                  "nunca se guarda un día de tipo 0 (simulación o pronóstico)")
        varios = [date(2026, 9, 30), date(2026, 10, 1)]
        almacen.guardar("so", 10, varios, np.full((2, 49, 69), 34.0, np.float32), np.ones((49, 69), bool), [2, 2],
                        date(2026, 10, 14))
        lec = almacen.leer("so", 10, date(2026, 9, 29), date(2026, 10, 2), np.array([3, 4]), np.array([5]))
        comprobar(lec.valores.shape == (4, 2, 1) and list((lec.tipo > 0).all(axis=(1, 2))) == [False, True, True, False],
                  "lectura de varios meses con recorte de celdas")
        origen = almacen.origen_dias("thetao", 0, date(2026, 9, 1), date(2026, 9, 3))
        comprobar(origen == {date(2026, 9, 1): "parcial", date(2026, 9, 2): "ausente", date(2026, 9, 3): "ausente"},
                  "origen por día (parcial / ausente)")
        comprobar(almacen.ultimo_dia() == date(2026, 10, 1) and almacen.conteo_dias()["dias_con_dato"] == 4,
                  "último día y conteo de días con dato")
        # Archivo dañado
        ruta = ruta_diario() / "so" / "z010" / "2026-10.npz"
        ruta.write_bytes(b"no es un npz")
        almacen.olvidar_memoria()
        lec = almacen.leer("so", 10, date(2026, 10, 1), date(2026, 10, 1))
        comprobar(not (lec.tipo > 0).any() and ruta.with_name(ruta.name + ".danado").exists(),
                  "archivo dañado apartado como .danado y tratado como ausente")
        # Escritura concurrente desde otro proceso (mismo mes, otras celdas)
        hijo = subprocess.Popen([sys.executable, "-c", _HIJO_ALMACEN, str(ruta_diario().parent)], cwd=_RAIZ_PROYECTO,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        cub_padre = np.zeros((49, 69), bool)
        cub_padre[:20, :] = True
        for k in range(15):
            almacen.guardar("thetao", 0, [date(2026, 9, 10 + k)], np.full((1, 49, 69), 3.0, np.float32), cub_padre,
                            [2], date(2026, 10, 14))
            time.sleep(0.05)
        salida, error = hijo.communicate(timeout=180)
        almacen.olvidar_memoria()
        lec = almacen.leer("thetao", 0, date(2026, 9, 10), date(2026, 9, 24))
        comprobar(hijo.returncode == 0 and (lec.valores[:, :20] == 3.0).all() and (lec.valores[:, 40:] == 7.0).all(),
                  "escrituras concurrentes de dos procesos en el mismo mes no se pisan"
                  + ("" if hijo.returncode == 0 else f" ({error.strip()[-200:]})"))
    # Aislamiento de las cachés (servidores y pruebas de fallas de red): SOME_RUTA_COPERNICUS y SOME_RUTA_CHIRPS
    # las redirigen; SOME_RUTA_DATOS no (la prueba de humo usa la caché real)
    codigo = ("from backend import configuracion as cfg\nfrom backend.copernicus import constantes as k\n"
              "from backend.chirps import constantes as ch\nprint(k.ruta_estado())\nprint(ch.RUTA_ESTADO)\n"
              "print(cfg.RUTA_DATOS)\n")
    with tempfile.TemporaryDirectory(prefix="prueba_rutas_") as tmp:
        base = {k: v for k, v in os.environ.items() if k not in ("SOME_RUTA_COPERNICUS", "SOME_RUTA_CHIRPS")}
        base["SOME_RUTA_DATOS"] = str(Path(tmp) / "otra")
        salidas = []
        for extra in ({}, {"SOME_RUTA_COPERNICUS": str(Path(tmp) / "cop"), "SOME_RUTA_CHIRPS": str(Path(tmp) / "chi")}):
            r = subprocess.run([sys.executable, "-c", codigo], cwd=_RAIZ_PROYECTO, env={**base, **extra},
                               capture_output=True, text=True, encoding="utf-8", timeout=120)
            salidas.append([Path(x.strip()) for x in r.stdout.splitlines() if x.strip()])
        defecto, aislado = salidas
        comprobar(len(defecto) == 3 and defecto[0] == defecto[2] / "copernicus" / "estado.json"
                  and defecto[1] == defecto[2] / "chirps" / "estado.json",
                  "por defecto (aunque haya SOME_RUTA_DATOS) las cachés son datos/copernicus y datos/chirps")
        comprobar(len(aislado) == 3 and aislado[0] == (Path(tmp) / "cop" / "estado.json").resolve()
                  and aislado[1] == (Path(tmp) / "chi" / "estado.json").resolve(),
                  "SOME_RUTA_COPERNICUS y SOME_RUTA_CHIRPS redirigen las cachés (y su estado.json)")


# ---------------------------------------------------------------------------
# 5. Plan de descarga
# ---------------------------------------------------------------------------
def prueba_plan() -> None:
    from . import descarga
    from .constantes import SERVICIO_GEO, SERVICIO_SERIE
    print("5. Plan de descarga:")
    with entorno() as e:
        w = date(2026, 10, 28)
        corte = w - timedelta(days=2)
        todo = (np.arange(49), np.arange(69))
        t = descarga.planificar(["thetao"], [0], corte, corte, *todo, boletin=w)
        comprobar(len(t) == 1 and t[0].servicio == SERVICIO_GEO and t[0].fragmentos == 2,
                  "mapa de un día a 0 m: servicio geo, 2 fragmentos")
        t = descarga.planificar(["uo", "vo"], [0], corte, corte, *todo, boletin=w)
        comprobar(len(t) == 1 and t[0].variables == ("uo", "vo") and t[0].fragmentos == 4,
                  "corrientes de un día: un tramo con uo y vo, 4 fragmentos")
        i, j = int(np.argmin(np.abs(cfg.LAT + 2))), int(np.argmin(np.abs(cfg.LON + 85)))
        t = descarga.planificar(["thetao"], list(cfg.PROFUNDIDADES), corte, corte, [i], [j], boletin=w)
        comprobar(len(t) == 1 and t[0].servicio == SERVICIO_SERIE and t[0].fragmentos == 12
                  and len(t[0].niveles_nativos) == 15, "perfil de un día en (−2, −85): serie, 12 fragmentos")
        lat_ok = (round((t[0].lat[1] - t[0].lat[0]) * 12) == 32 and round((t[0].lat[0] + 80) * 12 + 0.5) % 32 == 0)
        comprobar(lat_ok, "ventana de series ampliada a los bordes de fragmento (32 filas)")
        from .sincronizacion import indices_recorte
        nino = indices_recorte("nino12")
        t = descarga.planificar(["thetao"], [0], corte - timedelta(days=364), corte, *nino, boletin=w)
        comprobar(len(t) == 1 and t[0].servicio == SERVICIO_SERIE and t[0].fragmentos == 48,
                  "serie de 12 meses de thetao a 0 m en Niño 1+2: serie, 48 fragmentos")
        comprobar(t[0].fin <= corte and t[0].inicio >= cfg.FECHA_INICIO, "nunca días posteriores al corte")
        t = descarga.planificar(["thetao"], [0], corte + timedelta(days=1), corte + timedelta(days=5), *todo, boletin=w)
        comprobar(t == [], "un periodo posterior al corte no planifica nada")
        from . import llenar_cache
        ok, msg = _lanza(lambda: llenar_cache(["thetao"], list(cfg.PROFUNDIDADES), date(2024, 1, 1), corte, *todo),
                         ValueError)
        comprobar(ok and "preparar_copernicus" in msg, "plan mayor que MAX_FRAGMENTOS_BAJO_DEMANDA → ValueError (422)")
        from . import plan_escenarios
        from .estado import ultimo_dia_analisis
        c = ultimo_dia_analisis()
        sup = plan_escenarios(["superficie"])["superficie"]["tramos"]
        comprobar({v for t in sup for v in t.variables} == {"thetao", "so", "uo", "vo", "zos", "mlotst"}
                  and all(t.dias_pedidos == (c - timedelta(days=29), c) and t.niveles_some in ((0,), None) for t in sup)
                  and all(t.filas_some == (0, 48) and t.columnas_some == (0, 68) for t in sup),
                  "escenario «superficie»: las 6 variables a 0 m de los 30 días de la barra de tiempo (área completa)")
        comprobar(not e["modulo"].llamadas, "planificar no toca la red")


# ---------------------------------------------------------------------------
# 6. Acceso y credenciales
# ---------------------------------------------------------------------------
def prueba_acceso() -> None:
    from . import acceso, descarga, estado
    from .constantes import ErrorSinCredenciales
    print("6. Acceso y credenciales:")
    with entorno() as e:
        m = e["modulo"]
        ok, msg = _lanza(lambda: m.core_functions.credentials_utils._retrieve_credential_from_prompt("username", False),
                         ErrorSinCredenciales)
        comprobar(ok and acceso.guardia_instalada() and "copernicusmarine login" in msg,
                  "guardia anti-prompt instalada: pedir credenciales lanza ErrorSinCredenciales")
        corte = estado.ultimo_dia_analisis()
        descarga.ejecutar(descarga.planificar(["thetao"], [0, 10], corte, corte, np.arange(49), np.arange(69)))
        llamadas = m.llamadas_de("open_dataset")
        kw = llamadas[-1] if llamadas else {}
        comprobar(kw.get("service") == "arco-geo-series" and kw.get("start_datetime", "").startswith(corte.isoformat())
                  and kw.get("end_datetime", "").startswith(corte.isoformat())
                  and abs(kw.get("minimum_latitude", 0) + 6.125) < 0.01 and abs(kw.get("maximum_longitude", 0) + 77.875) < 0.01,
                  "open_dataset recibe recorte, días y servicio correctos")
        comprobar(all(k.get("username") is None and k.get("password") is None for k in llamadas)
                  and all(k.get("check_credentials_valid") is True and k.get("username") is None
                          for k in m.llamadas_de("login")),
                  "nunca se pasan usuario ni contraseña; login solo con check_credentials_valid=True")
        comprobar(kw.get("coordinates_selection_method") == "inside" and "minimum_depth" not in kw,
                  "selección «inside»; los niveles se eligen antes de cargar (sin abrir todo el rango)")
        comprobar(kw.get("chunk_size_limit") == 1,
                  "chunk_size_limit=1: bloques de dask = fragmentos ARCO (elegir 1 nivel no descarga 10)")
        m.llamadas.clear()
        os.environ["SOME_COPERNICUS_SIN_RED"] = "1"
        from . import campos, verificar
        verificar()
        lec = campos("thetao", corte - timedelta(days=3), corte, 0.0)
        estado.estado_actual()
        comprobar(not m.llamadas and lec.valores.shape[0] == 4,
                  "con SOME_COPERNICUS_SIN_RED=1 el módulo falso no recibe ninguna llamada (solo caché)")
        os.environ.pop("SOME_COPERNICUS_SIN_RED", None)
    with entorno(con_credenciales=False) as e:
        comprobar(not acceso.credenciales_presentes() and estado.motivo_sin_acceso().startswith("sin credenciales"),
                  "sin archivo ni variables de entorno: credenciales ausentes (sin red)")
        ok, _ = _lanza(lambda: acceso.leer_ventana("x", ["zos"], "arco-time-series", (0, 1), (0, 1), None, None),
                       ErrorSinCredenciales)
        comprobar(ok and not e["modulo"].llamadas, "sin credenciales no se intenta ninguna lectura")
    # Proxy HTTPS: la prueba rápida de alcance va por donde irá el toolbox (solo se toca 127.0.0.1)
    claves = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")
    guardado = {k: os.environ.get(k) for k in claves}
    try:
        for k in claves:
            os.environ.pop(k, None)
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        comprobar(acceso.destinos_alcance() == [("127.0.0.1", 9)], "con HTTPS_PROXY, la prueba de alcance va al proxy")
        os.environ["NO_PROXY"] = "auth.marine.copernicus.eu"
        destinos = acceso.destinos_alcance()
        comprobar(("127.0.0.1", 9) in destinos and ("auth.marine.copernicus.eu", 443) in destinos,
                  "NO_PROXY: el host exceptuado se prueba directo")
        os.environ.pop("NO_PROXY", None)
        acceso._alcance = None
        t0 = time.perf_counter()
        alcanzable = acceso.red_alcanzable(1.0)
        comprobar(alcanzable is False and time.perf_counter() - t0 < 6,
                  f"proxy que no responde → sin red en {cfg.fmt_num(time.perf_counter() - t0, 1)} s (no ~27 s de "
                  "reintentos del toolbox)")
    finally:
        acceso._alcance = None
        for k in claves:
            os.environ.pop(k, None)
        for k, v in guardado.items():
            if v is not None:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# 7. Conector
# ---------------------------------------------------------------------------
def prueba_conector() -> None:
    from ..conectores.base import ParametrosConsulta
    from ..conectores.copernicus import ConectorCopernicus, _SimuladorCopernicus
    from . import estado
    from .constantes import ErrorDescargaEnCurso, ErrorSinAcceso
    print("7. Conector:")
    with entorno() as e:
        m = e["modulo"]
        c = ConectorCopernicus()
        corte = estado.ultimo_dia_analisis()
        comprobar(c.datos_reales and c.insignia.startswith("Datos reales") and c.estado().fecha_corte == corte.isoformat(),
                  "con credenciales: datos_reales e insignia reales; fecha de corte = último día de análisis")
        ds = c.obtener(ParametrosConsulta("thetao", cfg.BBOX_AREA, corte - timedelta(days=1), corte, 0.0))
        a = ds.attrs
        comprobar(ds["thetao"].dims == ("time", "depth", "lat", "lon") and ds["thetao"].shape == (2, 1, 49, 69)
                  and ds["thetao"].attrs.get("units") == "degC" and "3 × 3" in ds["thetao"].attrs.get("cell_methods", ""),
                  "Dataset real: dimensiones, unidades CF y cell_methods")
        comprobar(a.get("datos_reales") == 1 and a.get("procedencia") == "real" and a.get("doi") == "10.48670/moi-00016"
                  and "E.U. Copernicus Marine Service Information" in a.get("credito", "")
                  and a.get("dias_nrt") == 2 and a.get("dias_faltantes") == 0 and "aviso" not in a,
                  "atributos de procedencia, crédito, DOI y días NRT")
        v = ds["thetao"].values[-1, 0]
        comprobar(abs(float(np.nanmax(v)) - (28.0 - 0.05 * 0.494 + 0.001)) < 0.01 and np.isnan(v).any(),
                  "valores del producto (0 m = nivel de 0.494 m) y celdas de tierra sin dato")
        ds = c.obtener(ParametrosConsulta("corrientes", cfg.BBOX_AREA, corte, corte, 0.0))
        comprobar(set(ds.data_vars) == {"uo", "vo"} and ds["uo"].dims == ("time", "depth", "lat", "lon"),
                  "«corrientes» trae uo y vo con (time, depth, lat, lon)")
        ds = c.obtener(ParametrosConsulta("thetao", (-85, -2, -85, -2), corte, corte,
                                          profundidades=list(cfg.PROFUNDIDADES)))
        perfil = ds["thetao"].values.ravel()
        comprobar(ds["thetao"].shape == (1, 8, 1, 1) and np.all(np.diff(perfil) < 0), "perfil de 8 niveles decreciente")
        ok, msg = _lanza(lambda: c.obtener(ParametrosConsulta("thetao", cfg.BBOX_AREA, corte, corte, 15.0)), ValueError)
        comprobar(ok and "0, 5, 10, 20, 30, 50, 75 y 100 m" in msg, "nivel de 15 m → ValueError (422)")
        ds = c.obtener(ParametrosConsulta("mlotst", (-81.5, -3.6, -79.8, -2.0), corte - timedelta(days=6), corte))
        comprobar(ds["mlotst"].dims == ("time", "lat", "lon") and ds.attrs["procedencia"] == "real",
                  "variable 2D (mlotst) con (time, lat, lon)")
        # Descarga que supera la espera → 503 y sigue en segundo plano
        m.retraso_s = 1.5
        os.environ["SOME_COPERNICUS_ESPERA_S"] = "1"
        ok, _ = _lanza(lambda: c.obtener(ParametrosConsulta("zos", cfg.BBOX_AREA, corte - timedelta(days=20),
                                                            corte - timedelta(days=20))), ErrorDescargaEnCurso)
        time.sleep(2.0)
        m.retraso_s = 0.0
        os.environ.pop("SOME_COPERNICUS_ESPERA_S", None)
        ds = c.obtener(ParametrosConsulta("zos", cfg.BBOX_AREA, corte - timedelta(days=20), corte - timedelta(days=20)))
        comprobar(ok and ds.attrs.get("procedencia") == "real" and ds.attrs.get("dias_definitivos") == 1,
                  "descarga lenta → ErrorDescargaEnCurso; el hilo termina y guarda (mejor análisis)")
        # Sin internet: la prueba rápida de alcance evita esperar los tiempos del toolbox
        m.alcanzable = False
        m.llamadas.clear()
        t0 = time.perf_counter()
        ds = c.obtener(ParametrosConsulta("so", cfg.BBOX_AREA, corte - timedelta(days=40), corte - timedelta(days=40), 0.0))
        comprobar(ds.attrs.get("datos_reales") == 0 and "sin conexión a internet" in ds.attrs.get("aviso", "")
                  and not m.llamadas_de("open_dataset") and time.perf_counter() - t0 < 5
                  and estado.caida_vigente()[0], "sin internet → respaldo señalado al instante y servicio marcado caído")
        estado.registrar_caida("segunda falla (simulada)")
        caido, hasta, _m = estado.caida_vigente()
        comprobar(caido and (hasta - datetime.now()).total_seconds() > 500,
                  "fallas seguidas: el plazo sin reintentos se duplica (5 → 10 min)")
        # Una caída en la descarga de thetao: el aviso de OTRA consulta (so) no arrastra ese contexto ni anida
        # paréntesis; el contexto queda en el registro de errores
        from .descarga import registrar_fallo
        falla = ErrorSinAcceso("No se pudo conectar con Copernicus Marine (tiempo de espera agotado)")
        falla.es_red = True
        registrar_fallo(falla, "Descarga de thetao")
        ds = c.obtener(ParametrosConsulta("so", cfg.BBOX_AREA, corte - timedelta(days=41), corte - timedelta(days=41),
                                          30.0))
        aviso = ds.attrs.get("aviso", "")
        comprobar(ds.attrs.get("respaldo_simulado") == 1 and "thetao" not in aviso and _anidamiento(aviso) <= 1
                  and "tiempo de espera agotado" in aviso and "no se reintenta hasta las" in aviso,
                  "aviso tras una caída: sin el contexto de otra consulta ni paréntesis anidados")
        comprobar(any("Descarga de thetao" in x["mensaje"] for x in estado.errores_recientes(3))
                  and "thetao" not in estado.caida_vigente()[2], "el contexto de la caída solo va al registro de errores")
        m.alcanzable = True
        estado.registrar_servicio(True)
        comprobar(not estado.caida_vigente()[0] and estado.acceso_disponible(), "un contacto exitoso levanta la caída")
        # Cobertura parcial: sin acceso, una parte en caché
        os.environ["SOME_COPERNICUS_SIN_RED"] = "1"
        ds = c.obtener(ParametrosConsulta("thetao", cfg.BBOX_AREA, corte - timedelta(days=4), corte, 0.0))
        comprobar(ds.attrs.get("procedencia") == "real" and ds.attrs.get("dias_faltantes") == 3
                  and "no se pudo obtener" in ds.attrs.get("aviso", "") and np.isnan(ds["thetao"].values[0]).all()
                  and _anidamiento(ds.attrs.get("aviso", "")) == 0,
                  "cobertura parcial → días sin dato (NaN), dias_faltantes y aviso (sin paréntesis anidados)")
        ds = c.obtener(ParametrosConsulta("so", cfg.BBOX_AREA, corte - timedelta(days=60), corte - timedelta(days=60), 50.0))
        comprobar(ds.attrs.get("datos_reales") == 0 and ds.attrs.get("respaldo_simulado") == 1
                  and "SIMULADOS" in ds.attrs.get("aviso", "") and "SOME_COPERNICUS_SIN_RED" in ds.attrs.get("aviso", "")
                  and _anidamiento(ds.attrs.get("aviso", "")) == 0,
                  "sin acceso y sin caché → respaldo simulado con datos_reales=0 y el motivo (sin paréntesis anidados)")
        os.environ["SOME_COPERNICUS_MODO"] = "real"
        ok, msg = _lanza(lambda: c.obtener(ParametrosConsulta("so", cfg.BBOX_AREA, corte - timedelta(days=60),
                                                              corte - timedelta(days=60), 50.0)), ValueError)
        comprobar(ok and "preparar_copernicus" in msg, "modo real estricto sin datos → ValueError (422)")
        os.environ["SOME_COPERNICUS_MODO"] = "simulado"
        p = ParametrosConsulta("thetao", (-90, -5, -80, 0), date(2026, 10, 1), date(2026, 10, 3), 20.0)
        ds = c.obtener(p)
        ref = _SimuladorCopernicus(c.variables).obtener(p)
        comprobar(not c.datos_reales and c.insignia == cfg.INSIGNIA_SIMULADOS and ds.attrs.get("datos_reales") == 0
                  and np.array_equal(ds["thetao"].values, ref["thetao"].values, equal_nan=True)
                  and c.estado().fecha_corte == "2026-10-26" and c.estado().latencia_ms == 1450,
                  "modo simulado: mismos valores, estado y fecha de corte que el simulador anterior")
        os.environ.pop("SOME_COPERNICUS_MODO", None)
        os.environ.pop("SOME_COPERNICUS_SIN_RED", None)
    with entorno(con_credenciales=False):
        c = ConectorCopernicus()
        ds = c.obtener(ParametrosConsulta("thetao", cfg.BBOX_AREA, date(2026, 10, 1), date(2026, 10, 1), 0.0))
        comprobar(not c.datos_reales and ds.attrs.get("datos_reales") == 0 and c.estado().estado == "sin_conexion"
                  and c.estado().mensaje.startswith("Sin credenciales de Copernicus Marine"),
                  "sin credenciales ni caché: modo simulado automático, estado sin_conexion con el motivo")


# ---------------------------------------------------------------------------
# 8. Estado RF-21
# ---------------------------------------------------------------------------
def prueba_estado() -> None:
    from . import estado, resumen
    from .constantes import ruta_estado
    from .sincronizacion import verificar
    print("8. Estado RF-21:")
    with entorno(con_credenciales=False):
        e = estado.estado_actual()
        comprobar(e["estado"] == "sin_conexion" and e["mensaje"].startswith("Sin credenciales de Copernicus Marine"),
                  "sin credenciales → sin_conexion «Sin credenciales de Copernicus Marine…»")
        estado.registrar_servicio(True, 900, prueba=True)      # una lectura anterior correcta, ya persistida
        s = estado.bloque_servicio()
        comprobar(s["estado"] == "sin_registro" and s["texto_estado"] == "Sin verificar" and s["latencia_ms"] is None
                  and s["ultimo_contacto"] and "credenciales" in s["detalle"],
                  "sin credenciales: el servicio ARCO figura «Sin verificar» (no «Operativa» por una lectura anterior)")
    with entorno() as ent:
        m = ent["modulo"]
        e = estado.estado_actual()
        comprobar(e["estado"] == "degradada" and not e["definitivo"], "credenciales sin verificar → degradada, no definitivo")
        r = verificar()
        e = estado.estado_actual()
        comprobar(r["credenciales"] == "validas" and r["lectura_ok"] and e["estado"] == "operativa"
                  and "responde con normalidad" in e["mensaje"], "verificación con lectura rápida → operativa")
        estado.registrar_servicio(True, 25000, prueba=True)
        comprobar(estado.estado_actual()["estado"] == "degradada", "lectura de prueba lenta (25 s) → degradada")
        m.fallo_open = ConnectionError("Connection refused (simulada)")
        r = verificar()
        e = estado.estado_actual()
        comprobar(r["lectura_ok"] is False and e["estado"] == "sin_conexion" and "No se pudo conectar" in e["mensaje"]
                  and not estado.acceso_disponible(), "error de red → sin_conexion y servicio caído (sin reintentos)")
        m.fallo_open = None
        m.login_resultado = False
        verificar()
        comprobar(estado.estado_actual()["estado"] == "sin_conexion" and "no son válidas" in estado.estado_actual()["mensaje"],
                  "credenciales inválidas → sin_conexion con instrucción de login")
        comprobar(estado.bloque_servicio()["estado"] == "sin_registro",
                  "credenciales inválidas: el servicio ARCO figura «Sin verificar»")
        ent["credenciales"].write_text("ficticio-2", encoding="ascii")
        os.utime(ent["credenciales"], None)
        comprobar(estado.credenciales_estado() == "sin_verificar",
                  "un nuevo «copernicusmarine login» invalida el veredicto anterior sin reiniciar")
        m.login_resultado = True
        os.environ["SOME_COPERNICUS_SIN_RED"] = "1"
        e = estado.estado_actual()
        h = estado.historial_estado(14)
        comprobar(e["estado"] == "sin_conexion" and e["mensaje"].startswith("Modo sin red") and h[-1]["estado"] == "sin_conexion",
                  "modo sin red → sin_conexion (también hoy en el historial)")
        os.environ.pop("SOME_COPERNICUS_SIN_RED", None)
        h = estado.historial_estado(14)
        comprobar(len(h) == 14 and h[0]["estado"] == "sin_registro" and h[-1]["estado"] == "sin_conexion"
                  and h[-1]["origen"] == "copernicus", "historial de 14 días: sin_registro y el peor estado de hoy")
        ruta_estado().write_text("{dañado", encoding="utf-8")
        estado.recargar()
        try:
            e, r2, d = estado.estado_actual(), resumen(), estado.detalle()
            comprobar(e["estado"] in ("operativa", "degradada", "sin_conexion") and "conjuntos" in r2
                      and "copernicus" in d, "estado(), resumen() y detalle() no lanzan con estado.json dañado")
        except Exception as ex:  # noqa: BLE001
            comprobar(False, f"estado con estado.json dañado lanzó {type(ex).__name__}: {ex}")
        os.environ["SOME_COPERNICUS_MODO"] = "simulado"
        h = estado.historial_estado(14)
        comprobar(estado.estado_actual()["mensaje"].startswith("Modo simulado") and h[-6]["estado"] == "degradada"
                  and h[-1]["estado"] == "operativa", "modo simulado: estado e historial simulados de siempre")
        os.environ.pop("SOME_COPERNICUS_MODO", None)
        claves = set(resumen())
        comprobar({"conjuntos", "climatologia", "paquete", "credenciales", "servicio", "fecha_corte",
                   "ultimo_dia_analisis", "errores_recientes", "conjunto_sincronizado"} <= claves,
                  "resumen() con las claves de la tarjeta de administración")


# ---------------------------------------------------------------------------
# 9. Climatología
# ---------------------------------------------------------------------------
def prueba_climatologia() -> None:
    from ..conectores.base import ParametrosConsulta
    from ..conectores.copernicus import ConectorCopernicus
    from . import climatologia, estado
    print("9. Climatología GLORYS12:")
    with entorno() as ent:
        c = ConectorCopernicus()
        ds = c.climatologia("thetao", "1991-2020", cfg.BBOX_AREA, 20.0)
        a = ds.attrs
        comprobar(a.get("periodo_efectivo") == "1993-2016" and a.get("periodo_fuente") == "climatology_bounds"
                  and a.get("periodo_referencia") == "1991-2020", "climatology_bounds 1993-01-01/2016-12-31 → «1993-2016»")
        comprobar("Se pidió 1991-2020" in a.get("origen_climatologia", "") and "otro sistema" in a.get("origen_climatologia", "")
                  and "interpolada a 20 m" in a.get("metodo", "") and a.get("doi_climatologia") == "10.48670/moi-00021"
                  and ds["thetao"].dims == ("month", "lat", "lon") and ds.attrs.get("datos_reales") == 1,
                  "atributos: pedido frente a efectivo, diferencia de sistema, método y DOI")
        dz = c.climatologia("zos", "1993-2022", cfg.BBOX_AREA)
        comprobar("desplazamiento constante" in dz.attrs.get("origen_climatologia", ""), "nota específica para zos")
        pedidas = len(ent["modulo"].llamadas_de("open_dataset"))
        c.climatologia("thetao", "1981-2010", cfg.BBOX_AREA, 20.0)
        comprobar(len(ent["modulo"].llamadas_de("open_dataset")) == pedidas, "la climatología se descarga una sola vez")
        ds = c.climatologia("so", "1991-2020", cfg.BBOX_AREA, 10.0, simulada=True)
        comprobar(ds.attrs.get("datos_reales") == 0 and "periodo_efectivo" not in ds.attrs,
                  "simulada=True → climatología del simulador (datos_reales=0)")
        corte = estado.ultimo_dia_analisis()
        c.obtener(ParametrosConsulta("thetao", cfg.BBOX_AREA, corte, corte, 0.0))   # caché diaria → modo real
        os.environ["SOME_COPERNICUS_SIN_RED"] = "1"
        ds = c.climatologia("so", "1991-2020", cfg.BBOX_AREA, 10.0)
        comprobar(ds.attrs.get("datos_reales") == 0 and ds.attrs.get("aviso", "").startswith("Climatología simulada")
                  and ds.attrs.get("periodo_referencia") == "1991-2020", "sin acceso ni caché → climatología simulada con aviso")
        os.environ.pop("SOME_COPERNICUS_SIN_RED", None)
        _a0, a1, _p = climatologia.periodo_de_limites(np.array([["1993-01-01", "2017-01-01"]], dtype="datetime64[ns]"))
        comprobar(a1 == 2016, "fin exactamente el 1 de enero → año anterior")
        comprobar(estado.detalle()["copernicus"]["climatologia"]["en_cache"] == 2, "detalle: climatologías en caché")


# ---------------------------------------------------------------------------
# 10. Procedencia
# ---------------------------------------------------------------------------
def prueba_procedencia() -> None:
    from ..conectores import base
    from ..conectores.base import ParametrosConsulta
    from ..conectores.copernicus import ConectorCopernicus
    from ..conectores.noaa import ConectorNOAA
    from . import estado
    print("10. Procedencia real / simulado / mixto:")
    if not hasattr(base, "procedencia_de_datos"):
        omitir("base.procedencia_de_datos aún no existe (lo integra el agente B)")
        return
    with entorno():
        c, noaa = ConectorCopernicus(), ConectorNOAA()
        corte = estado.ultimo_dia_analisis()
        real = c.obtener(ParametrosConsulta("thetao", cfg.BBOX_AREA, corte, corte, 0.0))
        os.environ["SOME_COPERNICUS_SIN_RED"] = "1"
        respaldo = c.obtener(ParametrosConsulta("so", cfg.BBOX_AREA, corte - timedelta(days=90), corte - timedelta(days=90), 75.0))
        os.environ.pop("SOME_COPERNICUS_SIN_RED", None)
        # NOAA OISST ya es real: el caso «mixto» fuerza su modo simulado (sin red ni caché de NOAA)
        modo_noaa = os.environ.get("SOME_NOAA_MODO")
        os.environ["SOME_NOAA_MODO"] = "simulado"
        try:
            sst = noaa.obtener(ParametrosConsulta("sst", cfg.BBOX_AREA, date(2026, 10, 1), date(2026, 10, 1)))
        finally:
            if modo_noaa is None:
                os.environ.pop("SOME_NOAA_MODO", None)
            else:
                os.environ["SOME_NOAA_MODO"] = modo_noaa
        p_real = base.procedencia_de_datos([c], [real])
        p_resp = base.procedencia_de_datos([c], [respaldo])
        p_mixto = base.procedencia_de_datos([noaa, c], [sst, real])
        comprobar(p_real.get("procedencia") == "real", "Dataset real de Copernicus → «real»")
        comprobar(p_resp.get("procedencia") == "simulado" and p_resp.get("avisos"),
                  "Dataset de respaldo → «simulado» con aviso (aunque el conector esté en modo real)")
        comprobar(p_mixto.get("procedencia") == "mixto", "NOAA (simulado) frente a Copernicus real → «mixto»")


# ---------------------------------------------------------------------------
# 11. Bloque real (opcional)
# ---------------------------------------------------------------------------
def prueba_real() -> None:
    from . import acceso, almacen, constantes, estado
    print("11. Copernicus Marine real (opcional):")
    if constantes.sin_red():
        omitir("modo sin red: no se consulta Copernicus Marine")
        return
    if not acceso.paquete_instalado():
        omitir(acceso.MENSAJE_NO_INSTALADO)
        return
    if not acceso.credenciales_presentes():
        omitir(f"{acceso.MENSAJE_SIN_CREDENCIALES}")
        return
    ok, lat, err = acceso.verificar_credenciales()
    if ok is not True:
        omitir(f"credenciales no válidas o sin conexión ({err or 'sin detalle'})")
        return
    from ..conectores.base import ParametrosConsulta
    from ..conectores.copernicus import ConectorCopernicus
    from .sincronizacion import verificar
    with tempfile.TemporaryDirectory(prefix="prueba_copernicus_real_") as tmp:
        previa = constantes.fijar_raiz(Path(tmp))
        almacen.olvidar_memoria()
        estado.recargar()
        try:
            t0 = time.perf_counter()
            v = verificar()
            comprobar(v["lectura_ok"] is True and v["credenciales"] == "validas",
                      f"verificación real: credenciales válidas ({lat} ms) y lectura de prueba en "
                      f"{cfg.fmt_num(v['latencia_ms'], 0)} ms ({cfg.fmt_num(time.perf_counter() - t0, 1)} s en total)")
            fin = (estado.datos().get("metadatos") or {}).get("fin_tiempo")
            corte = estado.ultimo_dia_analisis()
            from .calendario import ahora_utc, ultimo_dia_analisis
            comprobar(fin is not None and corte == ultimo_dia_analisis(ahora_utc(), date.fromisoformat(fin)),
                      f"corte = W − 2 según metadatos: {cfg.fmt_fecha(corte)} (ARCO hasta {cfg.fmt_fecha(fin) if fin else '—'})")
            c = ConectorCopernicus()
            t0 = time.perf_counter()
            ds = c.obtener(ParametrosConsulta("thetao", cfg.BBOX_AREA, corte, corte, 0.0))
            t_mapa = time.perf_counter() - t0
            v0 = ds["thetao"].values
            comprobar(ds.attrs.get("procedencia") == "real" and 15 <= np.nanmin(v0) and np.nanmax(v0) <= 32,
                      f"mapa real de thetao a 0 m del {cfg.fmt_fecha(corte)}: {cfg.fmt_num(np.nanmin(v0), 1)} – "
                      f"{cfg.fmt_num(np.nanmax(v0), 1)} °C en {cfg.fmt_num(t_mapa, 1)} s")
            t0 = time.perf_counter()
            ds = c.obtener(ParametrosConsulta("thetao", (-85, -2, -85, -2), corte, corte,
                                              profundidades=list(cfg.PROFUNDIDADES)))
            perfil = ds["thetao"].values.ravel()
            comprobar(perfil[0] > perfil[-1] and np.all(np.isfinite(perfil)),
                      f"perfil real en 85° O / 2° S: {cfg.fmt_num(perfil[0], 1)} °C en superficie, "
                      f"{cfg.fmt_num(perfil[-1], 1)} °C a 100 m ({cfg.fmt_num(time.perf_counter() - t0, 1)} s)")
            ds = c.obtener(ParametrosConsulta("so", (-85, -2, -85, -2), corte, corte, 0.0))
            s = float(ds["so"].values.ravel()[0])
            comprobar(30 <= s <= 36, f"salinidad real en superficie: {cfg.fmt_num(s, 2)} PSU")
            t0 = time.perf_counter()
            ds = c.obtener(ParametrosConsulta("thetao", cfg.BBOX_AREA, corte, corte, 0.0))
            comprobar(time.perf_counter() - t0 < 2, f"la misma consulta desde la caché: "
                      f"{cfg.fmt_num((time.perf_counter() - t0) * 1000, 0)} ms")
            print(f"         caché temporal: {cfg.fmt_num(almacen.tamano_bytes() / 1024, 0)} KiB")
        finally:
            constantes.fijar_raiz(previa)
            almacen.olvidar_memoria()
            estado.recargar()


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    os.environ.setdefault("SOME_COPERNICUS_SIN_SINCRONIZACION", "1")
    sin_red_externo = os.environ.get("SOME_COPERNICUS_SIN_RED")
    for prueba in (prueba_calendario, prueba_malla, prueba_niveles, prueba_almacen, prueba_plan, prueba_acceso,
                   prueba_conector, prueba_estado, prueba_climatologia, prueba_procedencia):
        try:
            prueba()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            comprobar(False, f"{prueba.__name__} lanzó {type(e).__name__}: {e}")
    if sin_red_externo is not None:
        os.environ["SOME_COPERNICUS_SIN_RED"] = sin_red_externo
    try:
        prueba_real()
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        comprobar(False, f"prueba_real lanzó {type(e).__name__}: {e}")
    r = _resultados
    print(f"\nResultado: {r['ok']} correctas, {r['fallo']} fallidas, {r['omitida']} omitidas.")
    return 1 if r["fallo"] else 0


if __name__ == "__main__":
    sys.exit(main())
