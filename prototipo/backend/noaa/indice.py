"""Índice Niño 1+2 REAL a partir de NOAA OISST v2.1.

Definición (la de NOAA CPC): anomalía de la temperatura superficial del mar promediada
en la región Niño 1+2 oficial, 0–10° S y 90–80° O (se sale del dominio SOME por el sur:
se descarga aparte en la misma petición que el mapa), respecto de la climatología
1991-2020.

Cálculo del SOME:
* cada día, media de área (pesos = coseno de la latitud) de las 40 × 40 celdas nativas
  de 0.25° de la región que tienen dato (las de tierra, en la costa de Ecuador y Perú,
  quedan fuera) de la SST diaria y de la climatología diaria 1991-2020 de NOAA PSL
  (`sst.day.mean.ltm.1991-2020.nc`) del mismo día del año, sobre las mismas celdas; la
  anomalía es su diferencia (el 29 de febrero usa la media del 28 de febrero y el 1 de
  marzo);
* semanas de domingo a sábado etiquetadas con el miércoles central, como las semanas
  de NOAA CPC (`wksst9120.for`): media de las anomalías diarias de la semana (al menos
  4 días con dato; la última semana puede estar incompleta y se marca).

NOAA CPC usa el OISST semanal de 1° y su propia climatología semanal: el índice del
SOME coincide con el suyo dentro de unas décimas de °C (las pruebas lo comprueban).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import numpy as np

from .. import configuracion as cfg
from . import almacen, malla
from .constantes import AUSENTE, FINAL, ORIGEN_NCEI, ORIGEN_PSL, PRELIMINAR, ErrorSinDatos

UMBRAL_NINO = 0.5                # ≥ +0.5 °C: El Niño (fase cálida); ≤ −0.5 °C: La Niña (fase fría)
MIN_DIAS_SEMANA = 4

TEXTO_METODO = (
    "Índice Niño 1+2 calculado por el SOME con NOAA OISST v2.1 diario (0.25°): media de área (pesos según el "
    "coseno de la latitud) de la anomalía de la temperatura superficial del mar en la región Niño 1+2 oficial "
    "(0–10° S, 90–80° O), respecto de la climatología diaria 1991-2020 de NOAA PSL; promedios semanales de "
    "domingo a sábado etiquetados con el miércoles central, como los de NOAA CPC. Umbrales: El Niño ≥ +0.5 °C, "
    "La Niña ≤ −0.5 °C."
)


def dia_del_anio_ltm(d: date) -> tuple[int, int]:
    """(día, día) del año no bisiesto (1–365) para la climatología diaria; el 29 de febrero → (59, 60)."""
    if d.month == 2 and d.day == 29:
        return 59, 60
    n = date(2001, d.month, d.day).timetuple().tm_yday
    return n, n


def _clim_del_dia(ltm: np.ndarray, d: date) -> np.ndarray:
    a, b = dia_del_anio_ltm(d)
    if a == b:
        return ltm[a - 1]
    return (ltm[a - 1] + ltm[b - 1]) / 2.0


def serie_diaria(inicio: date, fin: date) -> dict:
    """{"fechas", "sst", "clim", "anom", "tipo", "origen", "celdas"} de la región Niño 1+2 oficial (listas; None sin dato).

    ErrorSinDatos si la climatología diaria no está en la caché."""
    ltm = almacen.leer_ltm_diaria()
    if ltm is None:
        raise ErrorSinDatos("la climatología diaria 1991-2020 de la región Niño 1+2 no está en la caché local "
                            "(ejecute python -m backend.herramientas.preparar_noaa --indice)")
    lec = almacen.leer_nino12(inicio, fin)
    pesos = malla.pesos_nino12()
    sst, clim, anom, celdas = [], [], [], []
    for k, d in enumerate(lec.fechas):
        campo = lec.valores[k]
        c = _clim_del_dia(ltm, d)
        mascara = np.isfinite(campo) & np.isfinite(c)
        if lec.tipo[k] == AUSENTE or not mascara.any():
            sst.append(None)
            clim.append(None)
            anom.append(None)
            celdas.append(0)
            continue
        s = float(malla.media_area(campo[None], pesos, mascara)[0])
        cl = float(malla.media_area(c[None], pesos, mascara)[0])
        sst.append(s)
        clim.append(cl)
        anom.append(s - cl)
        celdas.append(int(mascara.sum()))
    return {"fechas": lec.fechas, "sst": sst, "clim": clim, "anom": anom,
            "tipo": [int(t) for t in lec.tipo], "origen": [int(o) for o in lec.origen], "celdas": celdas}


def _domingo(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)     # lunes = 0 … domingo = 6


def serie_semanal(inicio: date, fin: date) -> dict:
    """Semanas de domingo a sábado (etiqueta: el miércoles) que tocan [inicio, fin], con datos hasta `fin`.

    {"semanas": [{"miercoles", "desde", "hasta", "dias", "sst", "anom", "parcial", "preliminar"}], "diaria": {...}}.
    Una semana con menos de 4 días con dato queda sin valor (None)."""
    d0 = _domingo(inicio)
    diaria = serie_diaria(d0, fin)
    por_dia = {d: k for k, d in enumerate(diaria["fechas"])}
    semanas = []
    s = d0
    while s <= fin:
        dias = [s + timedelta(days=i) for i in range(7)]
        idx = [por_dia[d] for d in dias if d in por_dia and diaria["anom"][por_dia[d]] is not None]
        anoms = [diaria["anom"][i] for i in idx]
        ssts = [diaria["sst"][i] for i in idx]
        tipos = [diaria["tipo"][i] for i in idx]
        valida = len(idx) >= MIN_DIAS_SEMANA
        semanas.append({"miercoles": s + timedelta(days=3), "desde": s, "hasta": min(s + timedelta(days=6), fin),
                        "dias": len(idx), "sst": float(np.mean(ssts)) if valida else None,
                        "anom": float(np.mean(anoms)) if valida else None,
                        "parcial": len(idx) < 7, "preliminar": any(t == PRELIMINAR for t in tipos)})
        s += timedelta(days=7)
    return {"semanas": semanas, "diaria": diaria}


def fase(valor: float | None) -> tuple[str, str]:
    """(fase, semáforo) como el widget del panel."""
    if valor is None:
        return "sin dato", "gris"
    if valor >= UMBRAL_NINO:
        return "El Niño (fase cálida)", "rojo"
    if valor <= -UMBRAL_NINO:
        return "La Niña (fase fría)", "ambar"
    return "neutra", "verde"


def resumen(inicio: date, fin: date) -> dict:
    """Serie semanal lista para el panel y el boletín (valores redondeados a 2 decimales).

    {"fechas" (miércoles ISO), "valores" (anomalía), "sst", "dias", "parcial", "preliminar", "actual",
    "fecha_actual", "semana_actual" ({"desde", "hasta"} ISO), "fase", "semaforo", "metodo", "dias_finales",
    "dias_preliminares", "dias_faltantes", "dias_origen_psl", "dias_origen_ncei"} (los días con dato de cada
    servidor, para el crédito)."""
    r = serie_semanal(inicio, fin)
    semanas = [s for s in r["semanas"] if s["hasta"] >= inicio]
    validas = [s for s in semanas if s["anom"] is not None]
    if not validas:
        raise ErrorSinDatos(f"no hay días de NOAA OISST en la caché entre el {cfg.fmt_fecha(inicio)} y el "
                            f"{cfg.fmt_fecha(fin)} para calcular el índice Niño 1+2")
    ultima = validas[-1]
    f, semaforo = fase(ultima["anom"])
    tipos = [t for d, t in zip(r["diaria"]["fechas"], r["diaria"]["tipo"]) if inicio <= d <= fin]
    origenes = [o for d, o, t in zip(r["diaria"]["fechas"], r["diaria"]["origen"], r["diaria"]["tipo"])
                if inicio <= d <= fin and t != AUSENTE]
    return {
        "fechas": [s["miercoles"].isoformat() for s in semanas],
        "valores": [None if s["anom"] is None else round(s["anom"], 2) for s in semanas],
        "sst": [None if s["sst"] is None else round(s["sst"], 2) for s in semanas],
        "dias": [s["dias"] for s in semanas], "parcial": [s["parcial"] for s in semanas],
        "preliminar": [s["preliminar"] for s in semanas],
        "actual": round(ultima["anom"], 2), "sst_actual": round(ultima["sst"], 2),
        "fecha_actual": ultima["miercoles"].isoformat(),
        "semana_actual": {"desde": ultima["desde"].isoformat(), "hasta": ultima["hasta"].isoformat(),
                          "dias": ultima["dias"], "parcial": ultima["parcial"], "preliminar": ultima["preliminar"]},
        "fase": f, "semaforo": semaforo, "metodo": TEXTO_METODO,
        "dias_finales": sum(1 for t in tipos if t == FINAL),
        "dias_preliminares": sum(1 for t in tipos if t == PRELIMINAR),
        "dias_faltantes": sum(1 for t in tipos if t == AUSENTE),
        "dias_origen_psl": sum(1 for o in origenes if o == ORIGEN_PSL),
        "dias_origen_ncei": sum(1 for o in origenes if o == ORIGEN_NCEI),
    }


# ---------------------------------------------------------------------------
# NOAA CPC (contraste)
# ---------------------------------------------------------------------------
_LINEA_CPC = re.compile(r"^\s*(\d{2}[A-Z]{3}\d{4})\s+(-?\d+\.\d)\s*(-?\d+\.\d)")


def leer_cpc(texto: str) -> list[dict]:
    """[{"fecha" (miércoles), "sst", "anom"}] de Niño 1+2 en `wksst9120.for` (columnas fijas «26.3 5.2»)."""
    meses = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
                                             "NOV", "DEC"))}
    salida = []
    for linea in texto.splitlines():
        m = _LINEA_CPC.match(linea)
        if not m:
            continue
        t = m.group(1)
        try:
            d = date(int(t[5:9]), meses[t[2:5]], int(t[:2]))
        except (KeyError, ValueError):
            continue
        salida.append({"fecha": d, "sst": float(m.group(2)), "anom": float(m.group(3))})
    return salida


def comparar_cpc(semanas: list[dict], cpc: list[dict]) -> dict:
    """Semanas completas del SOME frente a las de CPC con el mismo miércoles: {"pares", "max_dif", "media_dif"}."""
    por_fecha = {c["fecha"]: c for c in cpc}
    pares = []
    for s in semanas:
        c = por_fecha.get(s["miercoles"])
        if c is None or s["anom"] is None or s["parcial"]:
            continue
        pares.append({"miercoles": s["miercoles"], "anom_some": round(s["anom"], 2), "anom_cpc": c["anom"],
                      "sst_some": round(s["sst"], 2), "sst_cpc": c["sst"], "dif": round(s["anom"] - c["anom"], 2)})
    difs = [abs(p["dif"]) for p in pares]
    return {"pares": pares, "max_dif": max(difs) if difs else None,
            "media_dif": round(float(np.mean(difs)), 3) if difs else None}


def momento() -> str:
    return datetime.now().isoformat(timespec="seconds")
