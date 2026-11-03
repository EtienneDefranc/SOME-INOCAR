"""Pruebas de la réplica dynamical.org de CHIRPS v3 (`espejo.py`) y de la climatología.

    python -m backend.chirps.prueba_espejo
    SOME_CHIRPS_SIN_RED=1 python -m backend.chirps.prueba_espejo    # sin red: se omiten las de red

Las pruebas de interfaz, modo sin red, validación de la malla y del cálculo
(con un almacén sintético en memoria) no usan la red. Con red, además se
consulta la réplica real: cobertura, comparación con los días del CHC que hay
en la caché local, media mensual de 2024, lecturas simultáneas y preparación
de un año de climatología en una carpeta temporal.
"""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from .. import configuracion as cfg

_resultados = {"ok": 0, "fallo": 0, "omitida": 0}


def verificar(condicion: bool, texto: str) -> None:
    if condicion:
        _resultados["ok"] += 1
        print(f"  OK     {texto}")
    else:
        _resultados["fallo"] += 1
        print(f"  FALLO  {texto}")


def omitir(texto: str) -> None:
    _resultados["omitida"] += 1
    print(f"  OMITE  {texto}")


def info(texto: str) -> None:
    print(f"         {texto}")


def pct(x: float) -> str:
    return f"{cfg.fmt_num(100 * x, 3)} %"


@contextmanager
def modo_sin_red():
    """Activa SOME_CHIRPS_SIN_RED=1 temporalmente y restaura el valor anterior."""
    from . import espejo
    anterior = os.environ.get("SOME_CHIRPS_SIN_RED")
    os.environ["SOME_CHIRPS_SIN_RED"] = "1"
    espejo.olvidar()
    try:
        yield
    finally:
        if anterior is None:
            os.environ.pop("SOME_CHIRPS_SIN_RED", None)
        else:
            os.environ["SOME_CHIRPS_SIN_RED"] = anterior
        espejo.olvidar()


def lanza(funcion, tipo) -> tuple[bool, str]:
    try:
        funcion()
    except tipo as e:
        return True, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    return False, "no lanzó"


# ---------------------------------------------------------------------------
# Sin red
# ---------------------------------------------------------------------------
def prueba_interfaz() -> None:
    from . import espejo
    from .constantes import ErrorCHIRPS
    print("Interfaz del módulo espejo:")
    verificar(espejo.TEXTO_ORIGEN == "réplica dynamical.org de CHIRPS v3 (CC BY 4.0)", "TEXTO_ORIGEN")
    verificar(issubclass(espejo.ErrorEspejo, ErrorCHIRPS), "ErrorEspejo hereda de ErrorCHIRPS")
    verificar(isinstance(espejo.disponible(), bool), "disponible() devuelve un booleano")
    firmas = {
        "cobertura": "(forzar: 'bool' = False) -> 'dict'",
        "leer_diario": "(inicio: 'date', fin: 'date', producto: 'str') -> 'tuple[list[date], np.ndarray]'",
        "leer_mensual_anio": "(anio: 'int') -> 'np.ndarray'",
        "disponible": "() -> 'bool'",
    }
    for nombre, firma in firmas.items():
        verificar(str(inspect.signature(getattr(espejo, nombre))) == firma, f"firma de {nombre}{firma}")


def prueba_importacion_perezosa() -> None:
    import subprocess
    print("Importación perezosa:")
    codigo = ("import sys; import backend.chirps.espejo as e, backend.chirps.climatologia as c; "
              "e.disponible(); print('icechunk' in sys.modules, 'zarr' in sys.modules)")
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True,
                       cwd=str(cfg.RAIZ), timeout=120)
    verificar(r.returncode == 0 and r.stdout.strip() == "False False",
              "importar espejo/climatologia y llamar a disponible() no carga icechunk ni zarr"
              + ("" if r.returncode == 0 else f" ({r.stderr.strip()[-200:]})"))


def prueba_sin_red() -> None:
    from . import climatologia, espejo
    print("Modo sin red (SOME_CHIRPS_SIN_RED=1 forzado):")
    with modo_sin_red():
        verificar(espejo.disponible() is False, "disponible() es False")
        t0 = time.perf_counter()
        c = espejo.cobertura(forzar=True)
        verificar(set(c) == {"final", "preliminar", "consultado", "latencia_ms", "error"}
                  and c["final"] is None and c["preliminar"] is None and "sin red" in c["error"].lower()
                  and isinstance(c["latencia_ms"], int) and len(c["consultado"]) == 19,
                  "cobertura() sin red: None y error descriptivo, sin lanzar")
        verificar(time.perf_counter() - t0 < 1.0, "cobertura() sin red es inmediata")
        ok, txt = lanza(lambda: espejo.leer_diario(date(2026, 9, 1), date(2026, 9, 2), "final"),
                        espejo.ErrorEspejo)
        verificar(ok and "sin red" in txt.lower(), "leer_diario() lanza ErrorEspejo")
        ok, txt = lanza(lambda: espejo.leer_mensual_anio(2000), espejo.ErrorEspejo)
        verificar(ok and "sin red" in txt.lower(), "leer_mensual_anio() lanza ErrorEspejo")
        ok, txt = lanza(lambda: climatologia.climatologia_periodo(1850, 1851), ValueError)
        verificar(ok and "modo sin red" in txt, "climatologia_periodo() con años faltantes → ValueError claro")
        r = climatologia.preparar_anios([1850])
        verificar(r["errores"] == 1 and r["anios"] == 0 and r["anios_replica"] == 0
                  and "sin red" in r["detalle_errores"][0], "preparar_anios() sin red informa el error")
    ok, _ = lanza(lambda: espejo.leer_diario(date(2026, 9, 1), date(2026, 9, 2), "otro"), ValueError)
    verificar(ok, "producto desconocido → ValueError")
    ok, _ = lanza(lambda: espejo.leer_diario(date(2026, 9, 2), date(2026, 9, 1), "final"), ValueError)
    verificar(ok, "intervalo invertido → ValueError")


def prueba_ventana() -> None:
    from . import espejo
    from .constantes import LAT_NORTE, LAT_SUR, LON_ESTE, LON_OESTE
    print("Georreferenciación (coordenadas sintéticas como las de la réplica):")
    lat = np.round(59.975 - 0.05 * np.arange(2400), 6)           # descendente, como la réplica
    lon = np.round(-179.975 + 0.05 * np.arange(7200), 6)
    f0, f1, desc = espejo._ventana(lat, LAT_SUR, LAT_NORTE, "latitud")
    verificar(f1 - f0 == 250 and desc and abs(lat[f0] - 6.225) < 1e-9 and abs(lat[f1 - 1] + 6.225) < 1e-9,
              f"latitud: 250 filas [{f0}, {f1}) de 6.225 a −6.225, descendente")
    c0, c1, desc = espejo._ventana(lon, LON_OESTE, LON_ESTE, "longitud")
    verificar(c1 - c0 == 350 and not desc and abs(lon[c0] + 95.225) < 1e-9 and abs(lon[c1 - 1] + 77.775) < 1e-9,
              f"longitud: 350 columnas [{c0}, {c1}) de −95.225 a −77.775")
    ok, _ = lanza(lambda: espejo._ventana(lon + 0.025, LON_OESTE, LON_ESTE, "longitud"), espejo.ErrorEspejo)
    verificar(ok, "malla desplazada medio paso → ErrorEspejo")
    ok, _ = lanza(lambda: espejo._ventana(np.round(-179.95 + 0.1 * np.arange(3600), 6), LON_OESTE, LON_ESTE,
                                          "longitud"), espejo.ErrorEspejo)
    verificar(ok, "paso de 0.1° → ErrorEspejo")


def _almacen_sintetico(bloque_t: int = 20, unidades: str = "kg m-2 s-1", desplazar: float = 0.0):
    """Almacén en memoria (feb-mar 2023) con señales conocidas para probar el cálculo sin red.

    mm/día = 1 + k (k = índice del día) en tierra; +10 al norte de 3° N; mar
    (NaN) al oeste de 85° O; el 10/02 entero sin dato; en 80.5–79.5° O faltan 7
    días de febrero y en 83.5–82.5° O faltan 4.
    """
    import xarray as xr
    from . import espejo
    lat = np.round(6.375 - 0.05 * np.arange(256), 6)                # descendente, 3 filas de margen
    lon = np.round(-95.375 + 0.05 * np.arange(356), 6) + desplazar
    fechas = np.arange(np.datetime64("2023-02-01"), np.datetime64("2023-04-01"), dtype="datetime64[D]")
    k = np.arange(fechas.size, dtype=np.float32)
    mm = np.broadcast_to((1.0 + k)[:, None, None], (fechas.size, lat.size, lon.size)).copy()
    mm[:, lat > 3.0, :] += 10.0
    mm[:, :, lon < -85.0] = np.nan
    mm[9] = np.nan                                                   # 10/02/2023: hueco
    banda7 = (lon > -80.5) & (lon < -79.5)
    banda4 = (lon > -83.5) & (lon < -82.5)
    for d in (1, 3, 5, 7, 11, 13, 15):
        mm[d][:, banda7] = np.nan
    for d in (2, 4, 6, 8):
        mm[d][:, banda4] = np.nan
    datos = (mm / 86400.0).astype(np.float32)
    da = xr.DataArray(datos, dims=("time", "latitude", "longitude"),
                      coords={"time": fechas.astype("datetime64[ns]"), "latitude": lat, "longitude": lon},
                      attrs={"units": unidades})
    da.encoding["preferred_chunks"] = {"time": bloque_t}
    return espejo._preparar_almacen(xr.Dataset({espejo.VARIABLE: da}), "final"), mm, fechas


def prueba_calculo_sintetico() -> None:
    from . import espejo
    from .malla import agregar_5x5, reproyectar_a_some
    print("Cálculo con un almacén sintético (sin red):")
    alm, mm, fechas = _almacen_sintetico()
    verificar((alm.f1 - alm.f0, alm.c1 - alm.c0) == (250, 350) and alm.lat_descendente and alm.bloque_t == 20,
              "recorte de 250 × 350 calculado desde las coordenadas")
    verificar(list(espejo._bloques(alm, 5, 47)) == [(5, 20), (20, 40), (40, 47)],
              "tramos de lectura alineados con los fragmentos del tiempo")
    f, v = espejo._leer_diario_almacen(alm, date(2023, 2, 1), date(2023, 3, 31))
    verificar(len(f) == 58 and v.shape == (58, 49, 69) and date(2023, 2, 10) not in f and v.dtype == np.float32,
              "58 fechas (el día entero sin dato no se devuelve), forma (58, 49, 69)")
    i_s, i_n = int(np.argmin(abs(cfg.LAT + 5))), int(np.argmin(abs(cfg.LAT - 5)))
    j_t, j_m = int(np.argmin(abs(cfg.LON + 84))), int(np.argmin(abs(cfg.LON + 90)))
    k = np.array([(d - date(2023, 2, 1)).days for d in f], dtype=np.float32)
    verificar(np.allclose(v[:, i_s, j_t], 1 + k, rtol=1e-5), "mm/día correctos (kg m⁻² s⁻¹ × 86 400)")
    verificar(np.allclose(v[:, i_n, j_t], 11 + k, rtol=1e-5),
              "orientación norte-sur correcta (5° N recibe la señal del norte)")
    verificar(np.isnan(v[:, i_s, j_m]).all(), "mar (oeste de 85° O) sin dato")
    f2, v2 = espejo._leer_diario_almacen(alm, date(2023, 3, 25), date(2023, 4, 10))
    verificar(f2 == [date(2023, 3, 25) + timedelta(days=i) for i in range(7)] and v2.shape[0] == 7,
              "intervalo parcialmente cubierto: devuelve solo las fechas disponibles")
    ok, txt = lanza(lambda: espejo._leer_diario_almacen(alm, date(2023, 4, 1), date(2023, 4, 30)),
                    espejo.ErrorEspejo)
    verificar(ok and "fuera de la cobertura" in txt, "intervalo fuera de cobertura → ErrorEspejo")

    i0, i1 = espejo._indices_intervalo(alm, date(2023, 1, 1), date(2023, 12, 31))
    media = espejo._media_mensual(alm, 2023, i0, i1)
    some = reproyectar_a_some(agregar_5x5(media))
    feb_validos = [d for d in range(28) if d != 9]
    esperado_feb = float(np.mean(1 + np.array(feb_validos, dtype=np.float64)))
    esperado_mar = float(np.mean(1 + np.arange(28, 59, dtype=np.float64)))
    verificar(abs(some[1, i_s, j_t] - esperado_feb) < 1e-4 and abs(some[2, i_s, j_t] - esperado_mar) < 1e-4,
              f"media mensual = media de los días con dato (feb {cfg.fmt_num(esperado_feb, 3)}, "
              f"mar {cfg.fmt_num(esperado_mar, 3)} mm/día)")
    verificar(np.isnan(some[[0] + list(range(3, 12))]).all(), "meses sin días quedan sin dato")
    j4, j7 = int(np.argmin(abs(cfg.LON + 83))), int(np.argmin(abs(cfg.LON + 80)))
    feb4 = [d for d in feb_validos if d not in (2, 4, 6, 8)]
    verificar(abs(some[1, i_s, j4] - float(np.mean(1 + np.array(feb4, dtype=np.float64)))) < 1e-4,
              "celda con 23 de 28 días (≥ 80 %): media sin sesgo de los días con dato")
    verificar(np.isnan(some[1, i_s, j7]) and np.isfinite(some[2, i_s, j7]),
              "celda con 20 de 28 días (< 80 %): febrero sin dato, marzo con dato")

    ok, _ = lanza(lambda: _almacen_sintetico(unidades="mm/day"), espejo.ErrorEspejo)
    verificar(ok, "unidades inesperadas → ErrorEspejo")
    ok, _ = lanza(lambda: _almacen_sintetico(desplazar=0.025), espejo.ErrorEspejo)
    verificar(ok, "malla desplazada → ErrorEspejo")


def prueba_origen_climatologia() -> None:
    from . import almacen, climatologia
    print("Registro del origen de la climatología (carpeta temporal):")
    original = almacen.RUTA_MENSUAL
    with tempfile.TemporaryDirectory() as tmp:
        almacen.RUTA_MENSUAL = Path(tmp) / "mensual"
        almacen._memoria_mensual.clear()
        try:
            campo = np.full((12, 49, 69), 3.0, dtype=np.float32)
            climatologia._guardar_anio(1990, campo, climatologia.ORIGEN_CHC)
            climatologia._guardar_anio(1991, campo, climatologia.ORIGEN_REPLICA)
            almacen.guardar_mensual(1992, campo)       # año antiguo sin registro
            o90, o91, o92 = (climatologia.origen_anio(a) for a in (1990, 1991, 1992))
            verificar(o90["origen"] == "chc" and not o90["inferido"] and o91["origen"] == "replica"
                      and o92["origen"] == "chc" and o92["inferido"] and climatologia.origen_anio(1993) is None,
                      "origen por año (réplica, CHC, inferido y ausente)")
            r = climatologia.resumen_origen(1990, 1993)
            verificar((r["replica"], r["chc"], r["faltan"]) == (1, 2, 1), "resumen_origen cuenta por fuente")
            t = climatologia.texto_origen_periodo(1990, 1993)
            verificar("réplica dynamical.org" in t and "NetCDF mensual" in t and "sin preparar" in t,
                      f"texto para citar: «{t}»")
            verificar(climatologia.texto_origen_periodo(1991, 1991).endswith("con la réplica dynamical.org de "
                                                                             "CHIRPS v3 (CC BY 4.0)"),
                      "texto de un periodo enteramente de la réplica")
            verificar((Path(tmp) / "mensual" / "origen.json").exists()
                      and not list((Path(tmp) / "mensual").glob("*.tmp")), "origen.json sin temporales residuales")
            ok, txt = lanza(lambda: climatologia._guardar_anio(1994, np.full((12, 49, 69), np.nan, np.float32),
                                                               climatologia.ORIGEN_REPLICA), Exception)
            verificar(ok and almacen.leer_mensual(1994) is None, "un año sin datos no se guarda")
            verificar(climatologia.anios_faltantes(1990, 1994) == [1993, 1994], "anios_faltantes")
        finally:
            almacen.RUTA_MENSUAL = original
            almacen._memoria_mensual.clear()


# ---------------------------------------------------------------------------
# Con red
# ---------------------------------------------------------------------------
def _comparar(ref: np.ndarray, rep: np.ndarray, mascara: np.ndarray | None = None) -> dict:
    """Error relativo (celdas con ref ≥ 0.1 mm), absoluto y diferencias de máscara."""
    vr, vp = np.isfinite(ref), np.isfinite(rep)
    ambos = vr & vp if mascara is None else vr & vp & mascara
    dif = np.abs(rep - ref)[ambos]
    base = np.abs(ref)[ambos]
    m = base >= 0.1
    return {"comunes": int(ambos.sum()), "solo_ref": int((vr & ~vp).sum()), "solo_rep": int((~vr & vp).sum()),
            "rel_max": float((dif[m] / base[m]).max()) if m.any() else 0.0,
            "abs_max": float(dif.max()) if dif.size else 0.0,
            "abs_max_pequenos": float(dif[~m].max()) if (~m).any() else 0.0}


def _interior(n_dias: int) -> np.ndarray:
    """Celdas SOME cuyas 4 celdas nativas de 0.25° son enteramente de tierra (25 de 25 a 0.05°)."""
    from . import espejo
    alm = espejo._abrir("final")
    i0, i1 = espejo._indices_intervalo(alm, date(2026, 9, 30), date(2026, 9, 30))
    rec = espejo._leer_recorte(alm, i0, i1)[0]
    llenas = np.isfinite(rec).reshape(50, 5, 70, 5).sum(axis=(1, 3)) == 25
    inter = llenas[:-1, :-1] & llenas[1:, :-1] & llenas[:-1, 1:] & llenas[1:, 1:]
    return np.broadcast_to(inter, (n_dias, 49, 69))


def prueba_red() -> dict:
    from . import almacen, climatologia, espejo
    from .constantes import FINAL, PRELIMINAR, sin_red
    medidas: dict = {}
    print("Réplica dynamical.org (con red):")
    if sin_red():
        omitir("modo sin red: no se consulta la réplica")
        return medidas
    if not espejo.disponible():
        omitir("icechunk/zarr no instalados: no se consulta la réplica")
        return medidas
    espejo.olvidar()
    t0 = time.perf_counter()
    c = espejo.cobertura(forzar=True)
    medidas["cobertura_s"] = time.perf_counter() - t0
    if c["final"] is None:
        omitir(f"la réplica no responde ({c['error']})")
        return medidas
    verificar(c["final"][0] == date(1981, 1, 1) and c["final"][1] >= date(2026, 9, 30) and c["error"] == ""
              and c["latencia_ms"] > 0,
              f"cobertura final {cfg.fmt_fecha(c['final'][0])} – {cfg.fmt_fecha(c['final'][1])} "
              f"(apertura {c['latencia_ms']} ms)")
    verificar(c["preliminar"] is not None and c["preliminar"][0] == date(2025, 1, 1)
              and c["preliminar"][1] >= date(2026, 10, 20),
              f"cobertura preliminar {cfg.fmt_fecha(c['preliminar'][0]) if c['preliminar'] else '—'} – "
              f"{cfg.fmt_fecha(c['preliminar'][1]) if c['preliminar'] else '—'}")
    t0 = time.perf_counter()
    c2 = espejo.cobertura()
    verificar(time.perf_counter() - t0 < 0.05 and c2["final"] == c["final"], "cobertura en caché de memoria")

    # --- Final: el final «rnl» del CHC a 0.05° agregado 5×5 frente a la réplica (misma agregación) ---
    from . import descarga, estado, red
    t0 = time.perf_counter()
    f, r = espejo.leer_diario(date(2026, 9, 25), date(2026, 9, 30), "final")
    medidas["diario_6_s"] = time.perf_counter() - t0
    verificar(f == [date(2026, 9, 25) + timedelta(days=i) for i in range(6)] and r.shape == (6, 49, 69),
              "final 25–30/09/2026: 6 días (6, 49, 69)")
    if red.disyuntor_abierto() or estado.chc_accesible() is False:
        omitir("el CHC está marcado caído: no se compara su final a 0.05° con la réplica")
    else:
        try:
            v_chc = np.stack([descarga.descargar_dia(date(2026, 9, d), FINAL) for d in (27, 28)])
            ct = _comparar(v_chc, r[2:4])
            medidas["final_chc_005"] = ct
            verificar(ct["solo_ref"] == 0 and ct["solo_rep"] == 0,
                      f"final del CHC a 0.05° (27–28/09/2026) con la misma máscara que la réplica "
                      f"({ct['comunes']} celdas·día, también en la costa)")
            verificar(ct["rel_max"] < 0.005,
                      f"final del CHC a 0.05° frente a la réplica: error máximo {pct(ct['rel_max'])} (< 0.5 %)")
        except Exception as e:  # noqa: BLE001
            omitir(f"no se pudo leer el final del CHC a 0.05° ({e})")
    v, tipo = almacen.leer(date(2026, 9, 25), date(2026, 9, 30))
    if (tipo == FINAL).all():
        cg = _comparar(v, r)
        verificar(cg["solo_ref"] == 0 and cg["solo_rep"] == 0 and cg["rel_max"] < 0.005,
                  f"los finales 25–30/09/2026 de la caché tienen la agregación de la réplica (error máximo "
                  f"{pct(cg['rel_max'])}, sin celdas de más ni de menos)")
    else:
        omitir("la caché no tiene los días finales 25–30/09/2026")

    # --- Preliminar: días del CHC en la caché (producto sat a 0.05° agregado) ---
    v, tipo = almacen.leer(date(2026, 10, 1), date(2026, 10, 20))
    if (tipo == PRELIMINAR).all():
        t0 = time.perf_counter()
        f, r = espejo.leer_diario(date(2026, 10, 1), date(2026, 10, 20), "preliminar")
        medidas["diario_20_s"] = time.perf_counter() - t0
        cp = _comparar(v, r)
        medidas["preliminar"] = cp
        verificar(len(f) == 20 and cp["solo_ref"] == 0 and cp["solo_rep"] == 0,
                  "preliminar 01–20/10/2026: 20 días con la misma máscara que el CHC")
        verificar(cp["rel_max"] < 0.005 and cp["abs_max_pequenos"] < 0.01,
                  f"preliminar ({cp['comunes']} celdas·día): error máximo {pct(cp['rel_max'])} (< 0.5 %), "
                  f"{cfg.fmt_num(cp['abs_max'], 4)} mm")
    else:
        omitir("la caché no tiene los días preliminares 01–20/10/2026 del CHC")

    fin_final = c["final"][1]
    f, _r = espejo.leer_diario(fin_final - timedelta(days=2), fin_final + timedelta(days=5), "final")
    verificar(f == [fin_final - timedelta(days=i) for i in (2, 1, 0)],
              "intervalo que pasa del último día final: solo las fechas disponibles")
    ok, txt = lanza(lambda: espejo.leer_diario(date(2031, 1, 1), date(2031, 1, 5), "final"), espejo.ErrorEspejo)
    verificar(ok and "fuera de la cobertura" in txt, "intervalo futuro → ErrorEspejo")
    ok, txt = lanza(lambda: espejo.leer_diario(date(2024, 1, 1), date(2024, 1, 5), "preliminar"),
                    espejo.ErrorEspejo)
    verificar(ok, "preliminar antes de 2025 → ErrorEspejo")

    # --- Mensual 2024 ---
    t0 = time.perf_counter()
    m = espejo.leer_mensual_anio(2024)
    medidas["mensual_s"] = time.perf_counter() - t0
    verificar(m.shape == (12, 49, 69) and m.dtype == np.float32 and all(np.isfinite(m[i]).sum() > 300 for i in range(12)),
              f"leer_mensual_anio(2024): (12, 49, 69) en {cfg.fmt_num(medidas['mensual_s'], 1)} s")
    f, d = espejo.leer_diario(date(2024, 1, 1), date(2024, 12, 31), "final")
    meses = np.array([x.month for x in f])
    alt = np.stack([np.nanmean(d[meses == k], axis=0) if (meses == k).any() else np.full((49, 69), np.nan)
                    for k in range(1, 13)])
    cm = _comparar(alt, m)
    medidas["mensual_vs_diarios"] = cm
    verificar(len(f) == 366 and cm["rel_max"] < 1e-4 and cm["solo_ref"] == 0 and cm["solo_rep"] == 0,
              f"2024: media mensual = media de los 366 diarios de la réplica en la malla SOME "
              f"(error máximo {pct(cm['rel_max'])})")
    ok, txt = lanza(lambda: espejo.leer_mensual_anio(2026), espejo.ErrorEspejo)
    verificar(ok and "no está completo" in txt, "año incompleto (2026) → ErrorEspejo")

    # --- CHC mensual (solo si responde) ---
    medidas["chc"] = _comparar_con_chc(m)

    # --- Lecturas simultáneas ---
    ref_f, ref_v = espejo.leer_diario(date(2026, 9, 1), date(2026, 9, 10), "final")
    with ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(lambda _: espejo.leer_diario(date(2026, 9, 1), date(2026, 9, 10), "final"), range(3)))
    verificar(all(rf == ref_f and np.array_equal(rv, ref_v, equal_nan=True) for rf, rv in res),
              "tres lecturas simultáneas dan el mismo resultado")

    # --- Climatología desde la réplica en carpeta temporal ---
    original = almacen.RUTA_MENSUAL
    with tempfile.TemporaryDirectory() as tmp:
        almacen.RUTA_MENSUAL = Path(tmp) / "mensual"
        almacen._memoria_mensual.clear()
        try:
            t0 = time.perf_counter()
            rc = climatologia.preparar_anios([2023, 2024], trabajadores=2)
            medidas["clim_2_anios_s"] = time.perf_counter() - t0
            verificar(rc["anios"] == 2 and rc["anios_replica"] == 2 and rc["errores"] == 0 and rc["bytes"] == 0,
                      f"preparar_anios([2023, 2024]) desde la réplica en {cfg.fmt_num(rc['segundos'], 1)} s")
            guardado = almacen.leer_mensual(2024)
            verificar(guardado is not None and np.array_equal(guardado, m, equal_nan=True),
                      "año guardado con almacen.guardar_mensual idéntico a leer_mensual_anio")
            o = climatologia.origen_anio(2024)
            verificar(o is not None and o["origen"] == "replica" and not o["inferido"],
                      "origen del año registrado: réplica")
            cl = climatologia.climatologia_periodo(2023, 2024)
            verificar(cl.shape == (12, 49, 69) and np.allclose(
                cl, (almacen.leer_mensual(2023) + guardado) / 2, equal_nan=True),
                "climatologia_periodo(2023, 2024) promedia los años")
        finally:
            almacen.RUTA_MENSUAL = original
            almacen._memoria_mensual.clear()
    return medidas


def _comparar_con_chc(mensual_replica: np.ndarray) -> dict | None:
    """Compara 2024 con el NetCDF mensual del CHC solo si el servidor responde ahora."""
    import requests
    from . import climatologia
    from .constantes import URL_MENSUAL_ANIO, USER_AGENT
    try:
        r = requests.get(URL_MENSUAL_ANIO.format(anio=2024), headers={"Range": "bytes=0-511",
                         "User-Agent": USER_AGENT}, timeout=8)
        responde = r.status_code in (200, 206)
    except requests.RequestException:
        responde = False
    if not responde:
        omitir("el servidor del CHC no responde: la media mensual de 2024 se validó contra la media de los "
               "diarios de la propia réplica, no contra el NetCDF mensual del CHC")
        return None
    try:
        chc = climatologia.mensual_chc_mm_dia(2024)
    except Exception as e:  # noqa: BLE001
        omitir(f"no se pudo leer el NetCDF mensual 2024 del CHC ({e})")
        return None
    cc = _comparar(chc, mensual_replica)
    verificar(cc["rel_max"] < 0.005, f"2024: réplica frente al NetCDF mensual del CHC, error máximo {pct(cc['rel_max'])}")
    return cc


def main() -> int:
    # La consola de Windows suele usar cp1252 y la salida redirigida falla con «→»: forzamos UTF-8.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    prueba_interfaz()
    prueba_importacion_perezosa()
    prueba_sin_red()
    prueba_ventana()
    prueba_calculo_sintetico()
    prueba_origen_climatologia()
    prueba_red()
    r = _resultados
    print(f"\nResultado: {r['ok']} correctas, {r['fallo']} fallidas, {r['omitida']} omitidas.")
    return 1 if r["fallo"] else 0


if __name__ == "__main__":
    sys.exit(main())
