"""Pruebas del acceso real a NOAA OISST v2.1 (núcleo `backend/noaa/` y conector).

    python -m backend.noaa.prueba_noaa
    SOME_NOAA_SIN_RED=1 python -m backend.noaa.prueba_noaa      # sin el bloque real

Las pruebas 1 a 11 son SIN RED: usan carpetas temporales (la caché real
`datos/noaa/` no se toca) y un TRANSPORTE HTTP FALSO inyectado en `red` (registra
cada petición; sirve NetCDF4 sintéticos con la misma forma que el NCSS de PSL y que
los archivos diarios de NCEI, el listado HTML de NCEI, el `dataset.xml` de PSL y el
archivo semanal de CPC; puede responder 429, 404, 502 o fallar la conexión). El
reloj y las esperas también son falsos: ninguna prueba sin red espera de verdad.

La prueba 12 (bloque real) corre si no está activo el modo sin red: verifica PSL y
NCEI, descarga de verdad septiembre y octubre de 2026 (1 petición NCSS), un día de
NCEI, la climatología mensual oficial 1991-2020 y parte de la climatología diaria de
la región Niño 1+2, en otra carpeta temporal; compara PSL con NCEI valor a valor y el
índice Niño 1+2 semanal con el de NOAA CPC, e imprime tiempos, tamaños y los valores
de control.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

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


# ---------------------------------------------------------------------------
# Datos sintéticos y NetCDF4 en memoria
# ---------------------------------------------------------------------------
LAT_VENTANA = np.round(np.arange(-10.125, 6.376, 0.25), 3)        # una fila más por lado (el NCSS toca bordes)
LON_VENTANA = np.round(np.arange(264.625, 282.376, 0.25), 3)       # 0–360
BASE_1800 = date(1800, 1, 1)


def sst_sintetica(d: date, lat: np.ndarray, lon360: np.ndarray) -> np.ndarray:
    """SST sintética (lat, lon) con una franja de tierra (NaN) en la costa."""
    la, lo = np.meshgrid(lat, lon360, indexing="ij")
    v = 24.0 + 0.2 * la - 0.05 * (lo - 264.875) + 0.01 * ((d - date(2023, 10, 1)).days % 400)
    tierra = (lo > 279.5) & (la > -5.0) & (la < 2.0)
    return np.where(tierra, np.nan, v).astype(np.float32)


def clim_sintetica(doy: int, lat: np.ndarray, lon360: np.ndarray) -> np.ndarray:
    la, lo = np.meshgrid(lat, lon360, indexing="ij")
    v = 22.0 + 0.2 * la - 0.05 * (lo - 264.875) + 0.001 * doy
    tierra = (lo > 279.5) & (la > -5.0) & (la < 2.0)
    return np.where(tierra, np.nan, v).astype(np.float32)


def mensual_sintetica(anio: int, mes: int, lat, lon360) -> np.ndarray:
    la, lo = np.meshgrid(lat, lon360, indexing="ij")
    v = 25.0 + 0.5 * np.sin(mes) + 0.01 * (anio - 1981) + 0.0 * la
    tierra = (lo > 279.5) & (la > -5.0) & (la < 2.0)
    return np.where(tierra, np.nan, v).astype(np.float32)


def _a_bytes(ds) -> bytes:
    fd, ruta = tempfile.mkstemp(suffix=".nc")
    os.close(fd)
    try:
        ds.to_netcdf(ruta, engine="h5netcdf")
        return Path(ruta).read_bytes()
    finally:
        try:
            os.remove(ruta)
        except OSError:
            pass


def netcdf_psl(tiempos: list[float], campos: np.ndarray, lat=LAT_VENTANA, lon=LON_VENTANA,
               variable: str = "sst", descendente: bool = False) -> bytes:
    import xarray as xr
    if descendente:
        lat = lat[::-1]
        campos = campos[:, ::-1, :]
    ds = xr.Dataset({variable: (("time", "lat", "lon"), campos.astype(np.float32),
                                {"units": "degC", "valid_range": np.array([-3.0, 45.0], dtype=np.float32)})},
                    coords={"time": ("time", np.asarray(tiempos, dtype=float),
                                     {"units": "days since 1800-01-01 00:00:00"}),
                            "lat": ("lat", np.asarray(lat, dtype=np.float32)),
                            "lon": ("lon", np.asarray(lon, dtype=np.float32))})
    return _a_bytes(ds)


def netcdf_ncei(d: date) -> bytes:
    import xarray as xr
    lat = np.round(np.arange(-89.875, 90.0, 0.25), 3)
    lon = np.round(np.arange(0.125, 360.0, 0.25), 3)
    campo = np.full((lat.size, lon.size), np.nan, dtype=np.float32)
    i0 = int(np.argmin(np.abs(lat - LAT_VENTANA[0])))
    j0 = int(np.argmin(np.abs(lon - LON_VENTANA[0])))
    campo[i0:i0 + LAT_VENTANA.size, j0:j0 + LON_VENTANA.size] = sst_sintetica(d, LAT_VENTANA, LON_VENTANA)
    ds = xr.Dataset({"sst": (("time", "zlev", "lat", "lon"), campo[None, None], {"units": "Celsius"}),
                     "anom": (("time", "zlev", "lat", "lon"), (campo - 22.0)[None, None], {"units": "Celsius"})},
                    coords={"time": ("time", [float((d - date(1978, 1, 1)).days)],
                                     {"units": "days since 1978-01-01 12:00:00"}),
                            "zlev": ("zlev", [0.0]), "lat": ("lat", lat.astype(np.float32)),
                            "lon": ("lon", lon.astype(np.float32))})
    return _a_bytes(ds)


def listado_ncei(anio: int, mes: int, ultimo_final: date, ultimo_prelim: date) -> bytes:
    filas = []
    d = date(anio, mes, 1)
    while d.month == mes and d <= ultimo_prelim:
        if d <= ultimo_final:
            nombre = f"oisst-avhrr-v02r01.{d:%Y%m%d}.nc"
            publicado = datetime.combine(d + timedelta(days=15), datetime.min.time()).replace(hour=15, minute=15)
        else:
            nombre = f"oisst-avhrr-v02r01.{d:%Y%m%d}_preliminary.nc"
            publicado = datetime.combine(d + timedelta(days=1), datetime.min.time()).replace(hour=13, minute=31)
        filas.append(f'<tr><td><a href="{nombre}">{nombre}</a></td>\n<td align="right">{publicado:%Y-%m-%d %H:%M}  '
                     f'</td>\n<td align="right">1520061</td>\n<td>&nbsp;</td></tr>')
        d += timedelta(days=1)
    return ("<html><body><table>" + "\n".join(filas) + "</table></body></html>").encode()


CPC_EJEMPLO = """ Weekly SST data starts week centered on 2Sept1981

                Nino1+2      Nino3        Nino34        Nino4
 Week          SST SSTA     SST SSTA     SST SSTA     SST SSTA
 02SEP1981     20.6-0.1     24.8-0.1     26.5-0.2     28.3-0.3
 14OCT2026     26.2 5.2     29.2 4.2     30.0 3.3     29.7 1.1
 21OCT2026     26.3 5.2     29.4 4.3     30.1 3.4     29.8 1.1
"""


class TransporteFalso:
    """Servidores de NOAA simulados: PSL (NCSS y dataset.xml), NCEI (listados y archivos) y CPC."""

    def __init__(self, publicado: date, ultimo_final: date):
        self.publicado = publicado
        self.ultimo_final = ultimo_final
        self.llamadas: list[str] = []
        self.timeouts: list = []                # (conexión, respuesta) de cada llamada
        self.fallar: dict[str, list] = {}       # servidor → lista de respuestas forzadas (int estado o Exception)

    def _forzado(self, servidor: str):
        cola = self.fallar.get(servidor) or []
        return cola.pop(0) if cola else None

    def __call__(self, url: str, timeout):
        self.llamadas.append(url)
        self.timeouts.append(tuple(timeout))
        servidor = "psl" if "psl.noaa.gov" in url else "ncei" if "ncei.noaa.gov" in url else "cpc"
        f = self._forzado(servidor)
        if isinstance(f, BaseException):
            raise f
        if isinstance(f, int):
            return f, b"<html>error</html>", {}
        if servidor == "cpc":
            return 200, CPC_EJEMPLO.encode(), {}
        if servidor == "ncei":
            return self._ncei(url)
        return self._psl(url)

    def _psl(self, url: str):
        u = urlparse(url)
        archivo = u.path.rsplit("/", 2)[-2] if u.path.endswith("dataset.xml") else u.path.rsplit("/", 1)[-1]
        if u.path.endswith("dataset.xml"):
            anio = int(archivo.split(".")[3]) if archivo.startswith("sst.day.mean.") else self.publicado.year
            if anio > self.publicado.year:
                return 404, b"no existe", {}
            fin = min(self.publicado, date(anio, 12, 31))
            xml = (f"<gridDataset><TimeSpan>\n <begin>{anio}-01-01T00:00:00Z</begin>\n <end>{fin}T00:00:00Z</end>\n"
                   "</TimeSpan></gridDataset>")
            return 200, xml.encode(), {}
        q = parse_qs(u.query)
        if archivo.startswith("sst.day.mean.ltm"):
            t0 = date.fromisoformat(q["time_start"][0][:10].replace("0001-", "2001-"))
            t1 = date.fromisoformat(q["time_end"][0][:10].replace("0001-", "2001-"))
            doys = list(range(t0.timetuple().tm_yday, t1.timetuple().tm_yday + 1))
            tiempos = [-657073.0 + k - 1 for k in doys]
            campos = np.stack([clim_sintetica(k, LAT_VENTANA, LON_VENTANA) for k in doys])
            return 200, netcdf_psl(tiempos, campos), {}
        if archivo == "sst.mon.mean.nc":
            meses, tiempos = [], []
            a, m = 1981, 9
            while (a, m) <= (2026, 9):
                meses.append((a, m))
                tiempos.append(float((date(a, m, 1) - BASE_1800).days))
                a, m = (a + 1, 1) if m == 12 else (a, m + 1)
            campos = np.stack([mensual_sintetica(a, m, LAT_VENTANA, LON_VENTANA) for a, m in meses])
            return 200, netcdf_psl(tiempos, campos), {}
        if archivo == "sst.mon.ltm.1991-2020.nc":
            campos = np.stack([np.mean([mensual_sintetica(a, m, LAT_VENTANA, LON_VENTANA)
                                        for a in range(1991, 2021)], axis=0) for m in range(1, 13)])
            tiempos = [-657073.0 + (date(2001, m, 1) - date(2001, 1, 1)).days for m in range(1, 13)]
            return 200, netcdf_psl(tiempos, campos), {}
        if archivo.startswith("sst.day.mean."):
            anio = int(archivo.split(".")[3])
            t0 = date.fromisoformat(q["time_start"][0][:10])
            t1 = min(date.fromisoformat(q["time_end"][0][:10]), self.publicado, date(anio, 12, 31))
            dias = [t0 + timedelta(days=i) for i in range(max(0, (t1 - t0).days + 1))]
            campos = (np.stack([sst_sintetica(d, LAT_VENTANA, LON_VENTANA) for d in dias]) if dias
                      else np.empty((0, LAT_VENTANA.size, LON_VENTANA.size), np.float32))
            return 200, netcdf_psl([float((d - BASE_1800).days) for d in dias], campos), {}
        return 404, b"no existe", {}

    def _ncei(self, url: str):
        nombre = url.rsplit("/", 1)[-1]
        if not nombre:                                          # listado de un mes
            aaaamm = url.rstrip("/").rsplit("/", 1)[-1]
            return 200, listado_ncei(int(aaaamm[:4]), int(aaaamm[4:]), self.ultimo_final, self.publicado), {}
        d = datetime.strptime(nombre.split(".")[1][:8], "%Y%m%d").date()
        preliminar = "_preliminary" in nombre
        if d > self.publicado or (preliminar and d <= self.ultimo_final) or (not preliminar and d > self.ultimo_final):
            return 404, b"no existe", {}
        return 200, netcdf_ncei(d), {}


class entorno:
    """Carpeta temporal + transporte falso + reloj y esperas falsas (context manager)."""

    def __init__(self, publicado: date | None = None, ultimo_final: date | None = None):
        self.publicado = publicado or date(2026, 10, 29)
        self.ultimo_final = ultimo_final or self.publicado - timedelta(days=14)
        self.esperas: list[float] = []

    def __enter__(self):
        from . import almacen, constantes, estado, red
        self.tmp = tempfile.TemporaryDirectory(prefix="prueba_noaa_")
        self.previa = constantes.fijar_raiz(Path(self.tmp.name))
        almacen.olvidar_memoria()
        estado.recargar()
        red.reiniciar_fallos()
        red._proximo_local.clear()
        self.transporte = TransporteFalso(self.publicado, self.ultimo_final)
        self._previos = (red._transporte, red._dormir, os.environ.get("SOME_NOAA_SIN_RED"),
                         os.environ.get("SOME_NOAA_MODO"))
        red._transporte = self.transporte
        red._dormir = lambda s: self.esperas.append(float(s))
        os.environ.pop("SOME_NOAA_SIN_RED", None)
        os.environ.pop("SOME_NOAA_MODO", None)
        return self

    def __exit__(self, *exc):
        from . import almacen, constantes, estado, red
        red._transporte, red._dormir = self._previos[0], self._previos[1]
        for clave, valor in (("SOME_NOAA_SIN_RED", self._previos[2]), ("SOME_NOAA_MODO", self._previos[3])):
            if valor is None:
                os.environ.pop(clave, None)
            else:
                os.environ[clave] = valor
        constantes.fijar_raiz(self.previa)
        almacen.olvidar_memoria()
        estado.recargar()
        red.reiniciar_fallos()
        red._proximo_local.clear()
        self.tmp.cleanup()
        return False


# ---------------------------------------------------------------------------
# 1. Malla
# ---------------------------------------------------------------------------
def prueba_malla() -> None:
    print("\n1. Malla nativa → SOME y región Niño 1+2")
    from . import malla
    from .constantes import ErrorFormato
    comprobar(malla.LAT_NATIVA[0] == -9.875 and malla.LAT_NATIVA[-1] == 6.125 and malla.LAT_NATIVA.size == 65
              and malla.LON_NATIVA[0] == -95.125 and malla.LON_NATIVA[-1] == -77.875 and malla.LON_NATIVA.size == 70,
              "ventana nativa: 65 filas (−9.875 … 6.125) × 70 columnas (−95.125 … −77.875)")
    nativo = np.arange(65 * 70, dtype=np.float32).reshape(1, 65, 70)
    some = malla.a_some(nativo)
    esperado = nativo[0, 15:65, :]
    esperado = (esperado[:-1, :-1] + esperado[1:, :-1] + esperado[:-1, 1:] + esperado[1:, 1:]) / 4
    comprobar(some.shape == (1, 49, 69) and np.allclose(some[0], esperado),
              "cada centro SOME es la media de las 4 celdas nativas que lo rodean (49 × 69)")
    # Centro SOME (−6, −95): esquinas nativas (−6.125/−5.875, −95.125/−94.875)
    n2 = np.full((1, 65, 70), np.nan, np.float32)
    n2[0, 15, 0] = 10.0
    comprobar(np.isnan(malla.a_some(n2)[0, 0, 0]), "con 1 de 4 celdas válidas: sin dato (tierra)")
    n2[0, 16, 1] = 12.0
    comprobar(malla.a_some(n2)[0, 0, 0] == 11.0, "con 2 de 4 válidas: la media de las válidas (11.0)")
    comprobar(malla.a_some(n2, min_validas=1)[0, 0, 0] == 11.0, "el mínimo es configurable (MIN_VALIDAS_2X2)")
    r = malla.region_nino12(nativo)
    comprobar(r.shape == (1, 40, 40) and malla.lat_nino12()[0] == -9.875 and malla.lat_nino12()[-1] == -0.125
              and malla.lon_nino12()[0] == -89.875 and malla.lon_nino12()[-1] == -80.125,
              "región Niño 1+2 oficial: 40 × 40 celdas (−9.875 … −0.125, −89.875 … −80.125)")
    p = malla.pesos_nino12()
    comprobar(abs(p[0, 0] - np.cos(np.deg2rad(-9.875))) < 1e-12 and np.all(p[:, 0] < 1.0),
              "pesos de área = coseno de la latitud")
    campo = np.array([[[1.0, np.nan], [3.0, 5.0]]])
    comprobar(abs(malla.media_area(campo, np.ones((2, 2)))[0] - 3.0) < 1e-12, "media de área ignora celdas sin dato")
    # Lectura de un NetCDF tipo NCSS (latitud descendente, longitudes 0–360, una fila extra por lado)
    d = date(2026, 10, 29)
    campos = sst_sintetica(d, LAT_VENTANA, LON_VENTANA)[None]
    ds = malla.abrir_netcdf(netcdf_psl([float((d - BASE_1800).days)], campos, descendente=True))
    v = malla.ventana(ds, "sst")
    ref = sst_sintetica(d, malla.LAT_NATIVA, malla.LON_NATIVA % 360)
    comprobar(malla.fechas_de(ds) == [d] and v.shape == (1, 65, 70) and np.allclose(v[0], ref, equal_nan=True),
              "ventana(): selecciona los centros esperados de un NetCDF con latitud descendente y longitudes 0–360")
    ok, msg = _lanza(lambda: malla.abrir_netcdf(b"<html><body>Error 502</body></html>"), ErrorFormato)
    comprobar(ok and "no es un NetCDF4" in msg, f"una página HTML no se toma por datos: «{msg[:60]}…»")
    corta = netcdf_psl([float((d - BASE_1800).days)], campos[:, 10:, :], lat=LAT_VENTANA[10:])
    ok, msg = _lanza(lambda: malla.ventana(malla.abrir_netcdf(corta), "sst"), ErrorFormato)
    comprobar(ok and "Falta el centro" in msg, "si falta un centro de la ventana: ErrorFormato")
    xml = netcdf_ncei(d)
    dsn = malla.abrir_netcdf(xml)
    comprobar(malla.fechas_de(dsn) == [d] and np.allclose(malla.ventana(dsn, "sst")[0], ref, equal_nan=True),
              "archivo diario de NCEI (zlev, día a las 12:00, malla global): mismos valores")


# ---------------------------------------------------------------------------
# 2. Plan de descarga
# ---------------------------------------------------------------------------
def prueba_plan() -> None:
    print("\n2. Plan de descarga")
    from .descarga import planificar
    dias = [date(2023, 10, 1) + timedelta(days=i) for i in range(1125)]
    tramos = planificar(dias)
    comprobar(all(t.dias <= 92 and t.inicio.year == t.fin.year for t in tramos) and len(tramos) == 13
              and sum(t.dias for t in tramos) == 1125,
              f"01/10/2023 → 29/10/2026: {len(tramos)} tramos de ≤ 92 días, uno por archivo anual")
    salteados = [date(2026, 10, 1), date(2026, 10, 5), date(2026, 10, 29)]
    comprobar(planificar(salteados) == [planificar([date(2026, 10, 1), date(2026, 10, 29)])[0]]
              and planificar(salteados)[0].dias == 29, "días salteados del mismo año: un solo tramo (huecos incluidos)")
    t = planificar([date(2025, 12, 30), date(2026, 1, 2)])
    comprobar(len(t) == 2 and t[0].archivo == "sst.day.mean.2025.nc" and t[1].archivo == "sst.day.mean.2026.nc",
              "un cambio de año abre otro tramo (otro archivo)")


# ---------------------------------------------------------------------------
# 3. Almacén
# ---------------------------------------------------------------------------
def prueba_almacen() -> None:
    print("\n3. Caché local (almacén)")
    from . import almacen
    from .constantes import FINAL, ORIGEN_NCEI, ORIGEN_PSL, PRELIMINAR
    with entorno():
        fechas = [date(2026, 10, 27), date(2026, 10, 28)]
        v = np.full((2, 49, 69), 25.0, np.float32)
        r = np.full((2, 40, 40), 24.0, np.float32)
        n = almacen.guardar(fechas, v, r, [PRELIMINAR, PRELIMINAR], ORIGEN_PSL)
        lec = almacen.leer(date(2026, 10, 26), date(2026, 10, 28))
        comprobar(n == 2 and list(lec.tipo) == [0, 1, 1] and np.isnan(lec.valores[0]).all()
                  and float(lec.valores[1, 0, 0]) == 25.0, "guardar y leer: días preliminares; el ausente sin valores")
        m0 = almacen.marca()
        n = almacen.guardar(fechas[:1], v[:1] + 1, r[:1], [FINAL], ORIGEN_NCEI)
        comprobar(n == 1 and almacen.marca() != m0 and almacen.tipo_dia(fechas[0]) == FINAL,
                  "un final reemplaza a un preliminar (y cambia la marca de generación)")
        n = almacen.guardar(fechas[:1], v[:1] + 5, r[:1], [PRELIMINAR], ORIGEN_PSL)
        lec = almacen.leer(fechas[0], fechas[0])
        comprobar(n == 0 and float(lec.valores[0, 0, 0]) == 26.0 and int(lec.origen[0]) == ORIGEN_NCEI,
                  "un preliminar nunca pisa un final")
        n = almacen.guardar(fechas[1:], v[1:] + 2, r[1:], [PRELIMINAR], ORIGEN_PSL)
        comprobar(n == 1 and float(almacen.leer(fechas[1], fechas[1]).valores[0, 0, 0]) == 27.0,
                  "un preliminar más reciente reemplaza a otro preliminar")
        n = almacen.guardar([date(2026, 10, 29)], np.full((1, 49, 69), np.nan, np.float32), r[:1], [FINAL], ORIGEN_PSL)
        comprobar(n == 0 and almacen.tipo_dia(date(2026, 10, 29)) == 0, "un día sin ninguna celda válida no se guarda")
        c = almacen.conteo(date(2026, 10, 1), date(2026, 10, 31))
        comprobar(c["final"] == 1 and c["preliminar"] == 1 and c["ausente"] == 29 and c["por_origen"] == {"psl": 1, "ncei": 1}
                  and c["ultimo_dia"] == fechas[1], "conteo por tipo y por origen")
        f1 = almacen.faltantes(date(2026, 10, 26), date(2026, 10, 28))
        f2 = almacen.faltantes(date(2026, 10, 26), date(2026, 10, 28), incluir_preliminares=False)
        comprobar(f1 == [date(2026, 10, 26), date(2026, 10, 28)] and f2 == [date(2026, 10, 26)],
                  "faltantes: la sincronización renueva preliminares; las consultas piden solo los ausentes")
        nino = almacen.leer_nino12(fechas[0], fechas[1])
        comprobar(nino.valores.shape == (2, 40, 40) and float(nino.valores[0, 0, 0]) == 24.0, "región Niño 1+2 guardada")
        ruta = almacen.ruta_mes(2026, 10)
        ruta.write_bytes(b"esto no es un npz")
        almacen.olvidar_memoria()
        lec = almacen.leer(fechas[0], fechas[1])
        apartados = list(ruta.parent.glob("*.danado"))
        comprobar(not lec.obtenidos.any() and apartados, "un archivo dañado se aparta como .danado y se trata como ausente")


# ---------------------------------------------------------------------------
# 4. Red: ritmo, reintentos, 429, pausa
# ---------------------------------------------------------------------------
def prueba_red() -> None:
    print("\n4. Acceso HTTP respetuoso")
    from . import estado, red
    from .constantes import TIMEOUT_CONSULTA_S, USER_AGENT, ErrorConexion, ErrorNOAA, ErrorNoExiste
    comprobar(USER_AGENT.isascii() and "@" not in USER_AGENT and USER_AGENT.startswith("SOME-prototipo/0.1")
              and "INOCAR" in USER_AGENT, f"User-Agent genérico, ASCII y sin correo: «{USER_AGENT}»")
    with entorno() as e:
        reloj = [1_000_000.0]
        previo_reloj = red._reloj_pared
        red._reloj_pared = lambda: reloj[0]
        try:
            red.listado_ncei(2026, 10)
            red.listado_ncei(2026, 10)
            comprobar(len(e.esperas) == 1 and abs(e.esperas[0] - 1.0) < 1e-6,
                      "dos peticiones seguidas a NCEI: la segunda espera 1 s (1 petición por segundo)")
            e.esperas.clear()
            red.metadatos_psl("sst.day.mean.2026.nc")
            red.metadatos_psl("sst.day.mean.2026.nc")
            comprobar(e.esperas and abs(e.esperas[0] - 20.0) < 1e-6, "tras una petición a PSL se dejan 20 s de pausa")
            e.esperas.clear()
            e.transporte.fallar["psl"] = [429, 429]
            red.metadatos_psl("sst.day.mean.2026.nc", respetar_pausa=False)
            comprobar(60.0 in e.esperas and 120.0 in e.esperas and estado.datos()["servidores"]["psl"].get("limitado"),
                      "HTTP 429 de PSL en la sincronización: espera 60 s y 120 s, reintenta y registra la limitación")
            e.esperas.clear()
            e.transporte.fallar["psl"] = [429]
            ok, msg = _lanza(lambda: red.metadatos_psl("sst.day.mean.2026.nc"), ErrorConexion)
            comprobar(ok and "429" in msg and not any(x >= 60 for x in e.esperas),
                      "HTTP 429 en una consulta: no espera minutos, falla al instante (se sirve la caché y se avisa)")
            red.reiniciar_fallos()
            e.esperas.clear()
            e.transporte.fallar["psl"] = [502, ConnectionError("caída"), 503]
            ok, msg = _lanza(lambda: red.ncss("sst.day.mean.2026.nc", "sst", date(2026, 10, 1), date(2026, 10, 2),
                                              respetar_pausa=False), ErrorConexion)
            comprobar(ok and "HTTP 503" in msg and 5.0 in e.esperas and 15.0 in e.esperas,
                      f"sincronización: 502, sin conexión y 503: reintentos con espera de 5 y 15 s y luego error claro "
                      f"(«{msg}»)")
            en_pausa, hasta, _m = estado.pausa_vigente("psl")
            comprobar(en_pausa and hasta is not None, "tras 3 fallos seguidos PSL queda en pausa (persistida)")
            n = len(e.transporte.llamadas)
            ok, msg = _lanza(lambda: red.metadatos_psl("sst.day.mean.2026.nc"), ErrorConexion)
            comprobar(ok and len(e.transporte.llamadas) == n and "no se reintenta hasta" in msg,
                      "en pausa, las consultas no tocan el servidor")
            red.metadatos_psl("sst.day.mean.2026.nc", respetar_pausa=False)
            comprobar(not estado.pausa_vigente("psl")[0], "la sincronización sí reintenta; si responde, la pausa se cierra")
            # Consulta: nada se cuelga (un solo intento, tiempos cortos y pausa al primer fallo)
            red.reiniciar_fallos()
            e.esperas.clear()
            n = len(e.transporte.llamadas)
            e.transporte.fallar["psl"] = [ConnectionError("caída"), ConnectionError("caída")]
            ok, msg = _lanza(lambda: red.ncss("sst.day.mean.2026.nc", "sst", date(2026, 10, 1), date(2026, 10, 2)),
                             ErrorConexion)
            comprobar(ok and len(e.transporte.llamadas) == n + 1 and 5.0 not in e.esperas and 15.0 not in e.esperas
                      and e.transporte.timeouts[-1] == TIMEOUT_CONSULTA_S and estado.pausa_vigente("psl")[0],
                      f"consulta sin respuesta: un solo intento ({TIMEOUT_CONSULTA_S[0]} s para conectar), sin esperas "
                      "de reintento y PSL en pausa al instante")
            n = len(e.transporte.llamadas)
            ok, _msg = _lanza(lambda: red.ncss("sst.day.mean.2026.nc", "sst", date(2026, 10, 1), date(2026, 10, 2)),
                              ErrorConexion)
            comprobar(ok and len(e.transporte.llamadas) == n, "la consulta siguiente no vuelve a esperar al servidor")
            e.transporte.fallar["psl"] = []
            red.metadatos_psl("sst.day.mean.2026.nc", respetar_pausa=False)
            comprobar(not estado.pausa_vigente("psl")[0] and e.transporte.timeouts[-1][0] == 15,
                      "la sincronización usa sus tiempos (15 s para conectar) y cierra la pausa")
            ok, _msg = _lanza(lambda: red.archivo_ncei(date(2030, 1, 1), True), ErrorNoExiste)
            comprobar(ok, "404 → ErrorNoExiste (el servidor responde: el recurso no existe)")
            os.environ["SOME_NOAA_SIN_RED"] = "1"
            n = len(e.transporte.llamadas)
            ok, msg = _lanza(lambda: red.cpc_semanal(), ErrorNOAA)
            comprobar(ok and len(e.transporte.llamadas) == n and "SOME_NOAA_SIN_RED" in msg,
                      "modo sin red: ninguna petición sale")
            os.environ.pop("SOME_NOAA_SIN_RED", None)
            comprobar("HTTPSConnectionPool" not in red.texto_error(ConnectionError("HTTPSConnectionPool(host=x)")),
                      "los errores de red se traducen a frases cortas en español")
        finally:
            red._reloj_pared = previo_reloj


# ---------------------------------------------------------------------------
# 5. Publicación (final o preliminar)
# ---------------------------------------------------------------------------
def prueba_publicacion() -> None:
    print("\n5. Días finales y preliminares")
    from . import descarga, estado
    from .constantes import FINAL, PRELIMINAR
    filas = descarga.leer_listado(listado_ncei(2026, 10, date(2026, 10, 15), date(2026, 10, 29)).decode())
    finales = [f for f in filas if not f[1]]
    prelim = [f for f in filas if f[1]]
    comprobar(len(finales) == 15 and len(prelim) == 14 and finales[-1][0] == date(2026, 10, 15)
              and finales[-1][2] == datetime(2026, 10, 30, 15, 15),
              "listado de NCEI: 15 finales (con su hora de publicación) y 14 preliminares")
    with entorno():
        ahora = datetime(2026, 10, 30, 22, 0)
        comprobar(estado.tipo_publicado(date(2026, 10, 20), ahora) == PRELIMINAR
                  and estado.tipo_publicado(date(2026, 10, 10), ahora) == FINAL,
                  "sin listado: los últimos 15 días (respecto de hoy) se tratan como preliminares; los anteriores, finales")
        info = descarga.actualizar_publicacion(hoy=date(2026, 10, 30))
        comprobar(info["ncei_ultimo_final"] == "2026-10-15" and info["ncei_ultimo_preliminar"] == "2026-10-29",
                  "actualizar_publicacion(): finales hasta el 15/10 y preliminares hasta el 29/10")
        comprobar(estado.tipo_publicado(date(2026, 10, 14), ahora) == FINAL
                  and estado.tipo_publicado(date(2026, 10, 16), ahora) == PRELIMINAR,
                  "con listado: final hasta el último final publicado; después, preliminar")
        comprobar(estado.tipo_publicado(date(2026, 10, 15), datetime(2026, 10, 30, 20, 0)) == PRELIMINAR
                  and estado.tipo_publicado(date(2026, 10, 15), datetime(2026, 10, 31, 4, 0)) == FINAL,
                  "un final publicado hace menos de 12 h aún cuenta como preliminar en PSL (margen de seguridad)")


# ---------------------------------------------------------------------------
# 6. Descarga de punta a punta (PSL y respaldo NCEI)
# ---------------------------------------------------------------------------
def prueba_descarga() -> None:
    print("\n6. Descarga con PSL y respaldo NCEI")
    from . import almacen, descarga, estado
    from .constantes import ErrorDescargaEnCurso, ORIGEN_NCEI
    hoy = date.today()
    publicado = hoy - timedelta(days=1)
    with entorno(publicado=publicado, ultimo_final=publicado - timedelta(days=14)) as e:
        descarga.actualizar_publicacion()
        inicio = publicado - timedelta(days=40)
        r = descarga.llenar(inicio, publicado + timedelta(days=3))
        c = almacen.conteo(inicio, publicado)
        ahora = datetime.now()
        prelim = sum(1 for k in range(41) if estado.tipo_publicado(inicio + timedelta(days=k), ahora) == 1)
        comprobar(r["peticiones"] == 1 and r["dias_escritos"] == 41 and c["preliminar"] == prelim
                  and c["final"] == 41 - prelim and prelim in (14, 15),
                  f"llenar(): 1 petición NCSS, 41 días ({c['final']} finales y {c['preliminar']} preliminares: el "
                  "último final de NCEI cuenta como preliminar hasta 12 h después de publicado)")
        comprobar(estado.datos()["publicacion"].get("psl_fin") == publicado.isoformat(),
                  "lo recibido de PSL marca su último día publicado")
        lec = almacen.leer(publicado, publicado)
        esperado = sst_sintetica(publicado, np.round(np.arange(-6.125, 6.2, 0.25), 3),
                                 np.round(np.arange(264.875, 282.2, 0.25), 3))
        e4 = np.stack([esperado[:-1, :-1], esperado[1:, :-1], esperado[:-1, 1:], esperado[1:, 1:]])
        with np.errstate(invalid="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                ref = np.where(np.isfinite(e4).sum(0) >= 2, np.nanmean(e4, axis=0), np.nan)
        comprobar(np.allclose(lec.valores[0], ref, equal_nan=True, atol=1e-5), "valores en la malla SOME correctos")
        n = len(e.transporte.llamadas)
        r = descarga.llenar(inicio, publicado, incluir_preliminares=False)
        comprobar(r["peticiones"] == 0 and len(e.transporte.llamadas) == n,
                  "con la caché completa, una consulta no pide nada (los preliminares los renueva la sincronización)")
        # PSL caído: respaldo NCEI para los días recientes
        e.transporte.fallar["psl"] = [ConnectionError("caída")] * 3
        hueco = [publicado - timedelta(days=k) for k in range(3)]
        for d in hueco:
            with almacen._bloqueo(("mes", d.year, d.month)):
                pass
        _borrar_dias(hueco)
        r = descarga.llenar(publicado - timedelta(days=5), publicado, usar_ncei=True, incluir_preliminares=False)
        lec = almacen.leer(hueco[-1], hueco[0])
        comprobar(r["dias_ncei"] == 3 and all(int(o) == ORIGEN_NCEI for o in lec.origen) and r.get("psl_caido"),
                  "PSL no responde: los días recientes se piden a los archivos diarios de NCEI (origen NCEI)")
        # Bloqueo de descarga: otra descarga en curso → ErrorDescargaEnCurso a los 0.2 s
        from ..chirps.bloqueo import bloqueo
        from .constantes import ruta_bloqueos
        import threading
        listo, soltar = threading.Event(), threading.Event()

        def tomar():
            with bloqueo(ruta_bloqueos() / "descarga.lock"):
                listo.set()
                soltar.wait(5)
        h = threading.Thread(target=tomar)
        h.start()
        listo.wait(5)
        _borrar_dias([publicado])
        ok, _m = _lanza(lambda: descarga.llenar(publicado, publicado, espera_bloqueo=0.2), ErrorDescargaEnCurso)
        soltar.set()
        h.join()
        comprobar(ok, "si otra descarga tiene el turno, la consulta no espera: ErrorDescargaEnCurso")


def _borrar_dias(dias: list[date]) -> None:
    """Quita días de la caché (pruebas): reescribe el mes con esos días ausentes."""
    from . import almacen
    for d in dias:
        ruta = almacen.ruta_mes(d.year, d.month)
        with np.load(ruta) as z:
            datos = {k: z[k].copy() for k in z.files}
        datos["tipo"][d.day - 1] = 0
        datos["origen"][d.day - 1] = 0
        almacen._escribir_atomico(ruta, **datos)
    almacen.olvidar_memoria()
    almacen.tocar_generacion()


# ---------------------------------------------------------------------------
# 7. Climatologías
# ---------------------------------------------------------------------------
def prueba_climatologia() -> None:
    print("\n7. Climatologías mensuales y periodo efectivo")
    from . import climatologia
    with entorno():
        r = climatologia.preparar_mensual()
        comprobar(r["peticiones"] == 2 and r["meses"] == 541 and set(r["periodos"]) == {"1991-2020", "1981-2010", "1993-2022"},
                  "preparar_mensual(): 2 peticiones (medias mensuales desde 09/1981 y climatología oficial)")
        comprobar(r["dif_1991_2020"] is not None and r["dif_1991_2020"] < 1e-4,
                  f"1991-2020 oficial frente a la calculada: diferencia máxima {r['dif_1991_2020']} °C")
        c = climatologia.climatologia_mensual("1981-2010")
        comprobar(c.anio_inicio[:8] == [1982] * 8 and c.anio_inicio[8:] == [1981] * 4 and c.anios[0] == 29
                  and c.anios[11] == 30 and not c.completa,
                  "1981-2010: enero–agosto con 1982-2010 (29 años) y septiembre–diciembre con 1981-2010 (30)")
        comprobar(c.periodo_efectivo == "1982-2010 en enero–agosto",
                  f"periodo efectivo declarado: «{c.periodo_efectivo}»")
        comprobar("OISST empieza en septiembre de 1981" in climatologia.texto_origen(c, "1981-2010"),
                  "el origen de la climatología explica por qué")
        c2 = climatologia.climatologia_mensual("1993-2022")
        c3 = climatologia.climatologia_mensual("1991-2020")
        comprobar(c2.completa and c2.periodo_efectivo == "1993-2022" and c3.fuente == "sst.mon.ltm.1991-2020.nc"
                  and c3.valores.shape == (12, 49, 69),
                  "1993-2022 completa; 1991-2020 de la climatología oficial de PSL, en la malla SOME")
        esperado = 25.0 + 0.5 * np.sin(10) + 0.01 * (2007.5 - 1981)
        comprobar(abs(float(np.nanmean(c2.valores[9])) - esperado) < 1e-3, "valor de octubre 1993-2022 correcto")


# ---------------------------------------------------------------------------
# 8. Índice Niño 1+2
# ---------------------------------------------------------------------------
def prueba_indice() -> None:
    print("\n8. Índice Niño 1+2")
    from . import almacen, climatologia, descarga, indice, malla
    comprobar(indice.dia_del_anio_ltm(date(2024, 2, 29)) == (59, 60) and indice.dia_del_anio_ltm(date(2024, 3, 1)) == (60, 60)
              and indice.dia_del_anio_ltm(date(2026, 10, 29)) == (302, 302),
              "día del año de la climatología (año no bisiesto; 29 de febrero = media del 28/02 y el 01/03)")
    cpc = indice.leer_cpc(CPC_EJEMPLO)
    comprobar(len(cpc) == 3 and cpc[0] == {"fecha": date(1981, 9, 2), "sst": 20.6, "anom": -0.1}
              and cpc[-1] == {"fecha": date(2026, 10, 21), "sst": 26.3, "anom": 5.2},
              "lectura de wksst9120.for (columnas pegadas «20.6-0.1»)")
    with entorno(publicado=date(2026, 10, 29)):
        climatologia.preparar_ltm_diaria()
        comprobar(almacen.leer_ltm_diaria() is not None, "climatología diaria 1991-2020 de la región: 4 peticiones, 365 días")
        descarga.llenar(date(2026, 9, 1), date(2026, 10, 29))
        d = indice.serie_diaria(date(2026, 10, 29), date(2026, 10, 29))
        lat, lon = malla.lat_nino12(), malla.lon_nino12() % 360
        s = sst_sintetica(date(2026, 10, 29), lat, lon)
        c = clim_sintetica(302, lat, lon)
        w = np.cos(np.deg2rad(lat))[:, None] * np.ones((40, 40))
        m = np.isfinite(s) & np.isfinite(c)
        esperado = float(((s - c)[m] * w[m]).sum() / w[m].sum())
        comprobar(abs(d["anom"][0] - esperado) < 1e-6 and d["celdas"][0] == int(m.sum()) and d["celdas"][0] < 1600,
                  f"anomalía diaria = media de área ponderada sobre las celdas de mar ({d['celdas'][0]} de 1600)")
        r = indice.serie_semanal(date(2026, 10, 1), date(2026, 10, 29))
        ultima = r["semanas"][-1]
        comprobar(all(s["miercoles"].weekday() == 2 for s in r["semanas"]) and ultima["desde"] == date(2026, 10, 25)
                  and ultima["dias"] == 5 and ultima["parcial"] and ultima["preliminar"],
                  "semanas de domingo a sábado etiquetadas con el miércoles; la última (25–29/10) incompleta y preliminar")
        res = indice.resumen(date(2026, 9, 1), date(2026, 10, 29))
        comprobar(res["fecha_actual"] == "2026-10-28" and res["fase"].startswith("El Niño") and res["semaforo"] == "rojo"
                  and len(res["fechas"]) == len(res["valores"]), f"resumen para el panel: {res['actual']} °C, {res['fase']}")
        comp = indice.comparar_cpc(r["semanas"], [{"fecha": date(2026, 10, 21), "sst": 26.3, "anom": 5.2}])
        comprobar(len(comp["pares"]) == 1 and comp["max_dif"] is not None, "comparación con CPC por miércoles")


# ---------------------------------------------------------------------------
# 9. Estado RF-21
# ---------------------------------------------------------------------------
def prueba_estado() -> None:
    print("\n9. Estado RF-21")
    from . import estado, sincronizacion
    with entorno(publicado=date.today() - timedelta(days=1)) as e:
        ev = estado.estado_actual()
        comprobar(ev["estado"] == "degradada" and not ev["definitivo"] and "Aún no se ha verificado" in ev["mensaje"],
                  "sin verificar: «degradada» provisional")
        v = sincronizacion.verificar()
        ev = estado.estado_actual()
        comprobar(v["psl"]["ok"] and v["ncei"]["ok"] and ev["estado"] == "operativa"
                  and "responden con normalidad" in ev["mensaje"],
                  f"PSL y NCEI responden: «operativa» — {ev['mensaje'][:90]}…")
        e.transporte.fallar["psl"] = [ConnectionError("x")] * 6
        sincronizacion.verificar()
        estado.recargar()
        ev = estado.estado_actual()
        comprobar(ev["estado"] == "degradada" and "NOAA PSL no responde" in ev["mensaje"]
                  and "HTTPSConnectionPool" not in ev["mensaje"], f"PSL caído y NCEI no: «degradada» — {ev['mensaje'][:90]}…")
        e.transporte.fallar["psl"] = [ConnectionError("x")] * 6
        e.transporte.fallar["ncei"] = [ConnectionError("x")] * 6
        sincronizacion.verificar()
        ev = estado.estado_actual()
        comprobar(ev["estado"] == "sin_conexion" and "No se pudo conectar con NOAA PSL ni con NOAA NCEI" in ev["mensaje"],
                  "ambos caídos: «sin conexión», con el motivo en español")
        h = estado.historial_estado(14)
        comprobar(len(h) == 14 and h[-1]["estado"] == "sin_conexion" and h[-1]["origen"] in ("psl+ncei", "psl", "ncei"),
                  f"historial de 14 días: hoy «{h[-1]['estado']}» ({h[-1]['origen_texto']})")
        det = estado.detalle()
        comprobar(set(det["noaa"]) >= {"modo", "servidores", "publicacion", "climatologia", "cache", "acceso"}
                  and set(det["noaa"]["servidores"]) == {"psl", "ncei"} and "caminos" not in det,
                  "detalle para «Estado de fuentes»: bloque «noaa» (sin la clave «caminos» de CHIRPS)")
        os.environ["SOME_NOAA_SIN_RED"] = "1"
        estado.recargar()
        ev = estado.estado_actual()
        comprobar(ev["estado"] == "sin_conexion" and "SOME_NOAA_SIN_RED=1" in ev["mensaje"], "modo sin red: «sin conexión»")
        os.environ.pop("SOME_NOAA_SIN_RED")
        os.environ["SOME_NOAA_MODO"] = "simulado"
        estado.recargar()
        ev = estado.estado_actual()
        comprobar(ev["estado"] == "operativa" and ev["modo"] == "simulado" and ev["fecha_corte"] == date(2026, 10, 27)
                  and estado.historial_estado(14)[-1]["origen_texto"] == "Simulación del prototipo",
                  "modo simulado forzado: el estado simulado anterior (operativa, 27/10/2026)")
        os.environ.pop("SOME_NOAA_MODO")


# ---------------------------------------------------------------------------
# 10. Conector
# ---------------------------------------------------------------------------
def prueba_conector() -> None:
    print("\n10. Conector (mismo contrato que el simulado; procedencia honesta)")
    from ..conectores.base import ParametrosConsulta, procedencia_de_datos
    from ..conectores.noaa import ConectorNOAA as ConectorAnterior
    from . import descarga
    from .conector import ConectorNOAA
    anterior = ConectorAnterior()
    with entorno(publicado=date.today() - timedelta(days=1)) as e:
        c = ConectorNOAA()
        mismos = all(getattr(c, k) == getattr(anterior, k) for k in ("id", "nombre", "nombre_corto", "proveedor",
                                                                     "producto", "identificador", "licencia"))
        comprobar(mismos and [v.a_dict() for v in c.variables] == [v.a_dict() for v in anterior.variables],
                  "mismos id, nombres, identificador, licencia y variable «sst» que el conector simulado actual")
        corte = date.today() - timedelta(days=1)
        descarga.actualizar_publicacion()
        descarga.llenar(corte - timedelta(days=30), corte)
        comprobar(c.datos_reales and c.insignia == "Datos reales · NOAA OISST" and "10.25921/RE9P-PT57" in c.credito
                  and c.estado().fecha_corte == corte.isoformat(),
                  f"modo real: insignia «{c.insignia}», crédito con DOI y fecha de corte = último día ({cfg.fmt_fecha(corte)})")
        from . import estado as _estado
        _estado.registrar_publicacion({"psl_fin": (corte + timedelta(days=2)).isoformat()})
        comprobar(c.estado().fecha_corte == corte.isoformat() and _estado.ultimo_publicado() == corte + timedelta(days=2),
                  "PSL publicó días que la caché aún no tiene: la fecha de corte sigue en el último día de la caché "
                  "(la fecha por defecto del sistema siempre tiene dato)")
        _estado.registrar_publicacion({"psl_fin": corte.isoformat()})
        ds = c.obtener(ParametrosConsulta("sst", (-90, -6, -80, 0), corte - timedelta(days=20), corte))
        a = ds.attrs
        from . import estado as _estado
        prelim = sum(1 for k in range(21) if _estado.tipo_publicado(corte - timedelta(days=k)) == 1)
        comprobar(a["datos_reales"] == 1 and a["procedencia"] == "real" and a["insignia"] == "Datos reales · NOAA OISST"
                  and a["dias_finales"] + a["dias_preliminares"] == 21 and a["dias_preliminares"] == prelim
                  and "preliminares" in a["origen_datos"] and a["fuente_id"] == "noaa_oisst"
                  and ds["sst"].dims == ("time", "lat", "lon") and "aviso" not in a,
                  f"Dataset real: {a['origen_datos']}")
        p = procedencia_de_datos([c], [ds])
        comprobar(p["procedencia"] == "real" and p["insignia"] == "Datos reales · NOAA OISST" and not p["datos_simulados"],
                  "procedencia_de_datos: «real» con la insignia de NOAA")
        # Días ausentes y sin red: Dataset real con aviso (nunca días simulados mezclados)
        os.environ["SOME_NOAA_SIN_RED"] = "1"
        ds = c.obtener(ParametrosConsulta("sst", (-90, -6, -80, 0), corte - timedelta(days=40), corte))
        comprobar(ds.attrs["datos_reales"] == 1 and ds.attrs["dias_faltantes"] == 10 and "SOME_NOAA_SIN_RED" in ds.attrs["aviso"]
                  and bool(np.isnan(ds["sst"].values[:10]).all()),
                  "días ausentes sin red: quedan sin dato y el Dataset lo avisa (no se rellenan con el simulador)")
        ds = c.obtener(ParametrosConsulta("sst", (-90, -6, -80, 0), date(2024, 1, 1), date(2024, 1, 31)))
        comprobar(ds.attrs["datos_reales"] == 0 and ds.attrs.get("respaldo_simulado") == 1
                  and "SIMULADOS" in ds.attrs["aviso"] and procedencia_de_datos([c], [ds])["procedencia"] == "simulado",
                  "sin ningún día real en la consulta: respaldo simulado completo, señalado")
        os.environ["SOME_NOAA_MODO"] = "real"
        ok, msg = _lanza(lambda: c.obtener(ParametrosConsulta("sst", (-90, -6, -80, 0), date(2024, 1, 1),
                                                              date(2024, 1, 31))), ValueError)
        comprobar(ok and "preparar_noaa" in msg, "modo real estricto: sin respaldo simulado (ValueError → 422)")
        os.environ.pop("SOME_NOAA_MODO")
        os.environ.pop("SOME_NOAA_SIN_RED")
        # Llenado bajo demanda (una petición)
        n = len(e.transporte.llamadas)
        ds = c.obtener(ParametrosConsulta("sst", (-85, -2, -85, -2), corte - timedelta(days=60), corte - timedelta(days=41)))
        comprobar(ds.attrs["datos_reales"] == 1 and ds.attrs["dias_faltantes"] == 0 and len(e.transporte.llamadas) == n + 1,
                  "días ausentes con red: una consulta los pide a PSL en el momento (1 petición)")
        # Climatología
        cl = c.climatologia("sst", "1981-2010", (-90, -6, -80, 0))
        comprobar(cl.attrs["datos_reales"] == 1 and cl.attrs["periodo_efectivo"] == "1982-2010 en enero–agosto"
                  and cl.attrs["periodo_referencia"] == "1981-2010" and len(cl.attrs["periodo_efectivo_por_mes"]) == 12
                  and cl["sst"].dims == ("month", "lat", "lon"),
                  "climatología real con periodo efectivo (se descargó bajo demanda: 2 peticiones)")
        cs = c.climatologia("sst", "1991-2020", (-90, -6, -80, 0), simulada=True)
        comprobar(cs.attrs["datos_reales"] == 0, "climatologia(simulada=True): la del simulador (para un respaldo)")
        # Índice: sin la climatología diaria, respaldo simulado señalado; con ella, el real
        n = len(e.transporte.llamadas)
        r0 = c.serie_indice_nino12(corte - timedelta(days=60), corte)
        comprobar(r0["procedencia"] == "simulado" and r0.get("respaldo_simulado") and "climatología diaria" in r0["aviso"]
                  and len(e.transporte.llamadas) == n,
                  "sin la climatología diaria: índice simulado con aviso (no se descarga dentro de la consulta)")
        from . import climatologia as _clim
        _clim.preparar_ltm_diaria()
        r = c.serie_indice_nino12(corte - timedelta(days=60), corte)
        comprobar(r["procedencia"] == "real" and r["datos_reales"] and r["fecha_actual"] <= corte.isoformat()
                  and r["semaforo"] in ("rojo", "verde", "ambar"),
                  f"índice Niño 1+2 real desde el conector: {r['actual']} °C ({r['fase']})")
        os.environ["SOME_NOAA_MODO"] = "simulado"
        ds = c.obtener(ParametrosConsulta("sst", (-90, -6, -80, 0), date(2026, 10, 1), date(2026, 10, 3)))
        ri = c.serie_indice_nino12(date(2026, 7, 1), date(2026, 10, 27))
        comprobar(not c.datos_reales and ds.attrs["datos_reales"] == 0 and c.estado().fecha_corte == "2026-10-27"
                  and ri["procedencia"] == "simulado" and c.insignia == cfg.INSIGNIA_SIMULADOS,
                  "SOME_NOAA_MODO=simulado: el simulador anterior, idéntico, y el índice simulado")
        os.environ.pop("SOME_NOAA_MODO")


# ---------------------------------------------------------------------------
# 11. Importación circular controlada (integración simulada)
# ---------------------------------------------------------------------------
_GANCHO = r'''
import importlib.abc, importlib.util, sys
class _Cargador(importlib.abc.Loader):
    def create_module(self, spec):
        return None
    def exec_module(self, modulo):
        exec("from ..noaa.conector import ConectorNOAA\n", modulo.__dict__)
class _Buscador(importlib.abc.MetaPathFinder):
    def find_spec(self, nombre, ruta, destino=None):
        if nombre == "backend.conectores.noaa":
            return importlib.util.spec_from_loader(nombre, _Cargador(), origin="integracion-simulada")
        return None
sys.meta_path.insert(0, _Buscador())
'''


def prueba_importacion() -> None:
    print("\n11. Integración simulada: conectores/noaa.py reexporta backend.noaa.conector")
    ordenes = {
        "primero el catálogo": "import backend.catalogo as k; print(type(k.conector('noaa_oisst')).__module__)",
        "primero el conector real": ("from backend.noaa.conector import ConectorNOAA; import backend.catalogo as k; "
                                     "print(type(k.conector('noaa_oisst')).__module__)"),
        "primero el paquete noaa": "import backend.noaa; from backend.conectores import crear_conectores; "
                                   "print(type(crear_conectores()['noaa_oisst']).__module__)",
    }
    entorno_hijo = {**os.environ, "SOME_NOAA_SIN_RED": "1", "SOME_NOAA_SIN_SINCRONIZACION": "1",
                    "SOME_RUTA_NOAA": tempfile.mkdtemp(prefix="prueba_noaa_imp_")}
    for nombre, codigo in ordenes.items():
        r = subprocess.run([sys.executable, "-c", _GANCHO + codigo], cwd=_RAIZ_PROYECTO, capture_output=True,
                           text=True, timeout=120, env=entorno_hijo)
        salida = (r.stdout or "").strip().splitlines()
        comprobar(r.returncode == 0 and salida and salida[-1] == "backend.noaa.conector",
                  f"{nombre}: el catálogo usa backend.noaa.conector.ConectorNOAA"
                  + ("" if r.returncode == 0 else f" — {(r.stderr or '').strip().splitlines()[-1:]}"))


# ---------------------------------------------------------------------------
# 12. Bloque real (con red)
# ---------------------------------------------------------------------------
def prueba_real() -> None:
    print("\n12. Bloque real (NOAA PSL, NOAA NCEI y NOAA CPC)")
    from .constantes import sin_red
    if sin_red():
        omitir("modo sin red activo (SOME_NOAA_SIN_RED=1)")
        return
    from ..conectores.base import ParametrosConsulta
    from . import almacen, climatologia, constantes, descarga, estado, indice, malla, red, sincronizacion
    from .conector import ConectorNOAA
    from .constantes import ErrorConexion
    with tempfile.TemporaryDirectory(prefix="prueba_noaa_real_") as tmp:
        previa = constantes.fijar_raiz(Path(tmp))
        almacen.olvidar_memoria()
        estado.recargar()
        red.reiniciar_fallos()
        t_total = time.perf_counter()
        try:
            t0 = time.perf_counter()
            v = sincronizacion.verificar()
            psl, ncei = v["psl"] or {}, v["ncei"] or {}
            comprobar(psl.get("ok") and ncei.get("ok"),
                      f"verificación real: PSL en {cfg.fmt_num(psl.get('latencia_ms'), 0)} ms (último día "
                      f"{cfg.fmt_fecha(psl.get('ultimo_dia'))}), NCEI en {cfg.fmt_num(ncei.get('latencia_ms'), 0)} ms "
                      f"(finales hasta {cfg.fmt_fecha(ncei.get('ultimo_final'))}, preliminares hasta "
                      f"{cfg.fmt_fecha(ncei.get('ultimo_preliminar'))}); {cfg.fmt_num(time.perf_counter() - t0, 1)} s")
            if not psl.get("ok"):
                omitir("PSL no responde: el resto del bloque real no se puede hacer")
                return
            fin = date.fromisoformat(psl["ultimo_dia"])
            inicio = date(fin.year, 9, 1) if fin.month >= 10 else fin - timedelta(days=58)
            t0 = time.perf_counter()
            r = descarga.llenar(inicio, fin, respetar_pausa=False)
            t_desc = time.perf_counter() - t0
            c = almacen.conteo(inicio, fin)
            comprobar(r["peticiones"] == 1 and c["ausente"] == 0,
                      f"NCSS de PSL: {r['dias_recibidos']} días ({cfg.fmt_fecha(inicio)} – {cfg.fmt_fecha(fin)}) en "
                      f"{cfg.fmt_num(r['segundos'], 1)} s, {cfg.fmt_num(r['bytes'] / 1024, 0)} KiB; {c['final']} finales y "
                      f"{c['preliminar']} preliminares ({cfg.fmt_num(t_desc, 1)} s con la espera de cortesía)")
            # PSL frente a NCEI valor a valor (último día, preliminar)
            pub = estado.datos().get("publicacion") or {}
            preliminar = not (pub.get("ncei_ultimo_final") and fin.isoformat() <= pub["ncei_ultimo_final"])
            t0 = time.perf_counter()
            contenido, _lat = red.archivo_ncei(fin, preliminar, respetar_pausa=False)
            t_ncei = time.perf_counter() - t0
            ds_n = malla.abrir_netcdf(contenido)
            nat_n = malla.ventana(ds_n, "sst")
            lec_r = almacen.leer_nino12(fin, fin)
            reg_n = malla.region_nino12(nat_n)[0]
            dif = float(np.nanmax(np.abs(reg_n - lec_r.valores[0])))
            comprobar(dif < 1e-4 and np.array_equal(np.isnan(reg_n), np.isnan(lec_r.valores[0])),
                      f"PSL y NCEI coinciden valor a valor el {cfg.fmt_fecha(fin)} (dif. máx. {dif:.5f} °C; archivo "
                      f"{'preliminar' if preliminar else 'final'} de NCEI de {cfg.fmt_num(len(contenido) / 1e6, 2)} MB en "
                      f"{cfg.fmt_num(t_ncei, 1)} s)")
            # SST de control el último día: región oficial y recorte Niño 1+2 del SOME
            w = malla.pesos_nino12()
            sst_oficial = float(malla.media_area(lec_r.valores, w)[0])
            anom_ncei = None
            if "anom" in ds_n.variables:
                anom_reg = malla.region_nino12(malla.ventana(ds_n, "anom"))
                anom_ncei = float(malla.media_area(anom_reg, w)[0])
            from .. import datos_simulados as sim
            ilat, ilon = sim.indices_bbox((-90.0, -6.0, -80.0, 0.0))
            lec_s = almacen.leer(fin, fin, ilat, ilon)
            pesos_s = np.cos(np.deg2rad(cfg.LAT[ilat]))[:, None] * np.ones((ilat.size, ilon.size))
            sst_recorte = float(malla.media_area(lec_s.valores, pesos_s)[0])
            print(f"         SST {cfg.fmt_fecha(fin)}: región Niño 1+2 oficial (0–10° S, 90–80° O) "
                  f"{cfg.fmt_num(sst_oficial, 2)} °C; recorte Niño 1+2 del SOME (0–6° S, malla SOME) "
                  f"{cfg.fmt_num(sst_recorte, 2)} °C"
                  + (f"; anomalía oficial de NCEI en la región {cfg.fmt_anomalia(anom_ncei, '°C', 2)}" if anom_ncei is not None else ""))
            comprobar(20 <= sst_oficial <= 32 and 20 <= sst_recorte <= 32, "SST de control en rango físico")
            # Climatología mensual oficial 1991-2020 (1 petición) → anomalía del recorte
            try:
                t0 = time.perf_counter()
                contenido, _lat = red.ncss_todo(constantes.ARCHIVO_LTM_MENSUAL, respetar_pausa=False)
                oficial = malla.ventana(malla.abrir_netcdf(contenido), "sst")
                climatologia._guardar_periodo("1991-2020", oficial, [1991] * 12, [2020] * 12, [30] * 12,
                                              climatologia.FUENTE_OFICIAL)
                cl = climatologia.climatologia_mensual("1991-2020")
                clim_recorte = float(malla.media_area(cl.valores[fin.month - 1][ilat[:, None], ilon[None, :]][None],
                                                      pesos_s, np.isfinite(lec_s.valores[0]))[0])
                print(f"         climatología mensual oficial 1991-2020: {cfg.fmt_num(len(contenido) / 1024, 0)} KiB en "
                      f"{cfg.fmt_num(time.perf_counter() - t0, 1)} s; anomalía del recorte Niño 1+2 respecto de "
                      f"{cfg.MESES_LARGOS[fin.month - 1]} 1991-2020: {cfg.fmt_anomalia(sst_recorte - clim_recorte, '°C', 2)}")
                comprobar(abs(sst_recorte - clim_recorte) < 8, "anomalía mensual del recorte calculada")
            except ErrorConexion as e:
                if not getattr(e, 'limitado', False):
                    raise
                omitir(f"climatología mensual oficial: NOAA PSL limitó el ritmo (HTTP 429) aun tras esperar ~12 min ({e})")
            # Climatología diaria de la región (solo los días necesarios: 1 petición) e índice frente a CPC
            try:
                d0 = date(2001, inicio.month, inicio.day).timetuple().tm_yday
                d1 = date(2001, fin.month, fin.day).timetuple().tm_yday
                t0 = time.perf_counter()
                contenido, _lat = red.ncss_ltm_diaria(max(1, d0 - 3), d1, respetar_pausa=False)
                ds_l = malla.abrir_netcdf(contenido)
                ltm = np.full((365, 40, 40), np.nan, np.float32)
                for k, dia in enumerate(malla.dias_del_anio_ltm(ds_l)):
                    ltm[dia - 1] = malla.ventana(ds_l, "sst", malla.lat_nino12(), malla.lon_nino12())[k]
                almacen.guardar_ltm_diaria(ltm, constantes.ARCHIVO_LTM_DIARIA)
                print(f"         climatología diaria 1991-2020 de la región (días {d0}–{d1}): "
                      f"{cfg.fmt_num(len(contenido) / 1024, 0)} KiB en {cfg.fmt_num(time.perf_counter() - t0, 1)} s")
                dia = indice.serie_diaria(fin, fin)
                if anom_ncei is not None:
                    print(f"         anomalía diaria del {cfg.fmt_fecha(fin)} en la región: SOME (climatología diaria de "
                          f"PSL) {cfg.fmt_anomalia(dia['anom'][0], '°C', 2)}; NCEI (variable anom) "
                          f"{cfg.fmt_anomalia(anom_ncei, '°C', 2)}")
                    comprobar(abs(dia["anom"][0] - anom_ncei) < 0.3, "anomalía diaria del SOME ≈ anomalía oficial de NCEI (< 0.3 °C)")
                semanal = indice.serie_semanal(inicio + timedelta(days=3), fin)
                try:
                    cpc = indice.leer_cpc(red.cpc_semanal())
                except Exception as e:  # noqa: BLE001
                    cpc = []
                    omitir(f"NOAA CPC no respondió ({e})")
                comp = indice.comparar_cpc(semanal["semanas"], cpc)
                for p in comp["pares"][-4:]:
                    print(f"         semana del {cfg.fmt_fecha(p['miercoles'])}: SOME {cfg.fmt_num(p['sst_some'], 2)} °C / "
                          f"{cfg.fmt_anomalia(p['anom_some'], '°C', 2)}; CPC {cfg.fmt_num(p['sst_cpc'], 1)} °C / "
                          f"{cfg.fmt_anomalia(p['anom_cpc'], '°C', 1)} (dif. {cfg.fmt_anomalia(p['dif'], '°C', 2)})")
                if cpc:
                    comprobar(comp["pares"] and comp["max_dif"] <= 0.3,
                              f"índice semanal del SOME frente a NOAA CPC en {len(comp['pares'])} semanas: diferencia "
                              f"máxima {cfg.fmt_num(comp['max_dif'], 2)} °C (≤ 0.3)")
            except ErrorConexion as e:
                if not getattr(e, 'limitado', False):
                    raise
                omitir(f"climatología diaria e índice frente a CPC: NOAA PSL limitó el ritmo (HTTP 429) aun tras esperar ~12 min ({e})")
            # Conector real sobre esta caché
            os.environ["SOME_NOAA_SIN_RED"] = "1"
            try:
                con = ConectorNOAA()
                t0 = time.perf_counter()
                ds = con.obtener(ParametrosConsulta("sst", cfg.BBOX_AREA, fin, fin))
                t_mapa = time.perf_counter() - t0
                v0 = ds["sst"].values
                comprobar(ds.attrs["procedencia"] == "real" and 15 <= np.nanmin(v0) and np.nanmax(v0) <= 33,
                          f"conector real: mapa del {cfg.fmt_fecha(fin)} {cfg.fmt_num(np.nanmin(v0), 1)} – "
                          f"{cfg.fmt_num(np.nanmax(v0), 1)} °C desde la caché en {cfg.fmt_num(t_mapa * 1000, 0)} ms; "
                          f"{int(np.isfinite(v0).sum())} celdas de mar de {v0.size}")
                ri = con.serie_indice_nino12(inicio + timedelta(days=3), fin)
                print(f"         índice Niño 1+2 del conector: {cfg.fmt_anomalia(ri['actual'], '°C', 2)} (semana del "
                      f"{cfg.fmt_fecha(ri['fecha_actual'])}, {ri['fase']}, {ri['procedencia']}); origen: {ri.get('origen_datos') or ri.get('aviso') or '—'}")
            finally:
                os.environ.pop("SOME_NOAA_SIN_RED", None)
            print(f"         caché temporal: {cfg.fmt_num(almacen.tamano_bytes() / 1024, 0)} KiB; bloque real en "
                  f"{cfg.fmt_num(time.perf_counter() - t_total, 0)} s")
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
    os.environ.setdefault("SOME_NOAA_SIN_SINCRONIZACION", "1")
    sin_red_externo = os.environ.get("SOME_NOAA_SIN_RED")
    for prueba in (prueba_malla, prueba_plan, prueba_almacen, prueba_red, prueba_publicacion, prueba_descarga,
                   prueba_climatologia, prueba_indice, prueba_estado, prueba_conector, prueba_importacion):
        try:
            prueba()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            comprobar(False, f"{prueba.__name__} lanzó {type(e).__name__}: {e}")
    if sin_red_externo is not None:
        os.environ["SOME_NOAA_SIN_RED"] = sin_red_externo
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
