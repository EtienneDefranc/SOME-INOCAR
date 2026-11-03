"""Prepara la caché local de NOAA OISST v2.1 (diario, climatologías e índice Niño 1+2) para la demo.

Uso (desde la carpeta prototipo-some):

    python -m backend.herramientas.preparar_noaa                  # verificación + todo lo que falte
    python -m backend.herramientas.preparar_noaa --verificar      # solo PSL y NCEI: último día, finales, latencia
    python -m backend.herramientas.preparar_noaa --estimar        # peticiones, MiB y tiempo, sin descargar
    python -m backend.herramientas.preparar_noaa --solo-diario    # solo la SST diaria
    python -m backend.herramientas.preparar_noaa --climatologia   # solo las climatologías mensuales
    python -m backend.herramientas.preparar_noaa --indice         # solo la climatología diaria de Niño 1+2 y el índice
    python -m backend.herramientas.preparar_noaa --desde 2026-09-01 --hasta 2026-10-29

Qué descarga (NOAA PSL, servicio NCSS del servidor THREDDS; ~0.3 s por día pedido, con 20 s de pausa
entre peticiones porque PSL limita el ritmo con HTTP 429):
* SST diaria del 01/10/2023 al último día publicado, en tramos de hasta 92 días de un mismo archivo
  anual (`sst.day.mean.AAAA.nc`), del área SOME más la región Niño 1+2 oficial (0–10° S, 90–80° O);
  los últimos ~15 días quedan como preliminares (el listado de NOAA NCEI dice cuáles);
* climatologías mensuales 1991-2020 (oficial, `sst.mon.ltm.1991-2020.nc`), 1981-2010 y 1993-2022
  (calculadas con `sst.mon.mean.nc`);
* climatología diaria 1991-2020 de la región Niño 1+2 (`sst.day.mean.ltm.1991-2020.nc`, 4 peticiones).

Al final muestra el índice Niño 1+2 de las últimas semanas junto al de NOAA CPC. Solo descarga lo que
falta. La caché va en datos/noaa/ o en SOME_RUTA_NOAA. Códigos de salida: 0 todo bien; 1 hubo
errores; 2 modo sin red o modo simulado (no se descargó nada).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta

from .. import configuracion as cfg
from .. import noaa as nucleo
from ..noaa import almacen, indice, red
from ..noaa.constantes import ErrorNOAA, modo_configurado, raiz, sin_red


def _barra(hechos: int, total: int, extra: str = "", ancho: int = 30) -> None:
    frac = min(1.0, hechos / total) if total else 1.0
    llenos = int(round(frac * ancho))
    texto = f"\r  [{'#' * llenos}{'.' * (ancho - llenos)}] {cfg.fmt_num(frac * 100, 0)} % {extra[:72]}"
    sys.stdout.write(texto.ljust(114))
    sys.stdout.flush()


def _mb(n: float) -> str:
    return f"{cfg.fmt_num(n / 1_048_576, 1)} MB"


def _verificar() -> tuple[int, dict]:
    print("Verificando NOAA PSL (último día publicado) y NOAA NCEI (días finales y preliminares)…")
    t0 = time.perf_counter()
    v = nucleo.verificar()
    psl, ncei = v.get("psl") or {}, v.get("ncei") or {}
    if psl.get("ok"):
        print(f"  NOAA PSL: responde en {cfg.fmt_num(psl['latencia_ms'], 0)} ms; último día publicado: "
              f"{cfg.fmt_fecha(psl['ultimo_dia'])}.")
    else:
        print(f"  NOAA PSL: no responde ({psl.get('error') or 'sin detalle'}).")
    if ncei.get("ok"):
        print(f"  NOAA NCEI: responde en {cfg.fmt_num(ncei['latencia_ms'], 0)} ms; finales hasta el "
              f"{cfg.fmt_fecha(ncei['ultimo_final'])}, preliminares hasta el {cfg.fmt_fecha(ncei['ultimo_preliminar'])}.")
    else:
        print(f"  NOAA NCEI: no responde ({ncei.get('error') or 'sin detalle'}).")
    print(f"  Estado: {v.get('estado')} ({cfg.fmt_num(time.perf_counter() - t0, 1)} s).")
    if not psl.get("ok") and not ncei.get("ok"):
        return 1, v
    return 0, v


def _mostrar_plan(p: dict) -> None:
    print(f"Plan (solo lo que falta) del {cfg.fmt_fecha(p['desde'])} al {cfg.fmt_fecha(p['hasta'])}:")
    d = p["diario"]
    print(f"  · SST diaria: {len(d['tramos'])} peticiones, {cfg.fmt_num(d['dias'], 0)} días (~{cfg.fmt_num(d['mib'], 1)} "
          f"MiB, ~{cfg.fmt_num(d['segundos'], 0)} s en el servidor)")
    for t in d["tramos"]:
        print(f"      {t.describir()}")
    print(f"  · Climatologías mensuales: {', '.join(p['climatologia']) or 'ya en caché'}"
          + (" (2 peticiones, ~5.6 MiB)" if p["climatologia"] else ""))
    print(f"  · Climatología diaria 1991-2020 de la región Niño 1+2: "
          f"{'4 peticiones (~0.6 MiB)' if p['ltm_diaria'] else 'ya en caché'}")
    print(f"  Total: {p['peticiones']} peticiones a NOAA PSL, ~{cfg.fmt_num(p['mib'], 1)} MiB, "
          f"~{cfg.fmt_num(p['segundos'] / 60, 0)} min con las pausas de cortesía.")


def _indice_y_cpc(hasta: date) -> None:
    try:
        r = indice.serie_semanal(hasta - timedelta(days=70), hasta)
    except ErrorNOAA as e:
        print(f"Índice Niño 1+2: no disponible ({e}).")
        return
    try:
        cpc = indice.leer_cpc(red.cpc_semanal())
    except ErrorNOAA as e:
        cpc = []
        print(f"  (no se pudo leer el índice semanal de NOAA CPC: {e})")
    comp = {c["fecha"]: c for c in cpc}
    print("Índice Niño 1+2 (región oficial 0–10° S, 90–80° O; anomalía respecto de 1991-2020):")
    print("  semana (miércoles)   días   SST SOME   anomalía SOME   NOAA CPC (SST / anomalía)")
    for s in r["semanas"][-8:]:
        c = comp.get(s["miercoles"])
        texto_cpc = f"{cfg.fmt_num(c['sst'], 1)} / {cfg.fmt_anomalia(c['anom'], '°C')}" if c else "—"
        print(f"  {cfg.fmt_fecha(s['miercoles'])}           {s['dias']}{'*' if s['parcial'] else ' '}    "
              f"{cfg.fmt_num(s['sst'], 2) if s['sst'] is not None else '—':>6} °C   "
              f"{cfg.fmt_anomalia(s['anom'], '°C', 2) if s['anom'] is not None else '—':>10}      {texto_cpc}")
    c = indice.comparar_cpc(r["semanas"], cpc)
    if c["pares"]:
        print(f"  Diferencia con NOAA CPC en {len(c['pares'])} semanas completas: máxima "
              f"{cfg.fmt_num(c['max_dif'], 2)} °C, media {cfg.fmt_num(c['media_dif'], 2)} °C. (* semana incompleta)")


def main(argv: list[str] | None = None) -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Prepara la caché local de NOAA OISST v2.1 para la demo del SOME.")
    ap.add_argument("--verificar", action="store_true", help="solo PSL y NCEI: último día, finales y latencia")
    ap.add_argument("--estimar", action="store_true", help="peticiones, MiB y tiempo, sin descargar")
    grupo = ap.add_mutually_exclusive_group()
    grupo.add_argument("--solo-diario", action="store_true", help="solo la SST diaria")
    grupo.add_argument("--climatologia", action="store_true", help="solo las climatologías mensuales")
    grupo.add_argument("--indice", action="store_true", help="solo la climatología diaria de Niño 1+2 y el índice")
    ap.add_argument("--desde", type=date.fromisoformat, default=None, help="primer día (aaaa-mm-dd); por defecto 2023-10-01")
    ap.add_argument("--hasta", type=date.fromisoformat, default=None, help="último día (aaaa-mm-dd); por defecto el último publicado")
    args = ap.parse_args(argv)

    print("SOME · NOAA OISST v2.1 (NOAA PSL y NOAA NCEI; DOI 10.25921/RE9P-PT57)")
    print(f"Caché local: {raiz()}" + (" (SOME_RUTA_NOAA)" if raiz() != cfg.RUTA_DATOS / "noaa" else ""))
    if modo_configurado() == "simulado":
        print("SOME_NOAA_MODO=simulado: NOAA OISST está en modo simulado; no se descarga nada.")
        return 2
    if sin_red() and not args.estimar:
        print("El modo sin red está activo (SOME_NOAA_SIN_RED=1): no se puede descargar nada.")
        return 2
    if not args.estimar:
        codigo, v = _verificar()
        if args.verificar or codigo:
            return codigo
    p = nucleo.plan(args.desde, args.hasta)
    if args.climatologia or args.indice:
        p["diario"] = {"tramos": [], "dias": 0, "segundos": 0.0, "mib": 0.0}
    if args.solo_diario or args.indice:
        p["climatologia"] = []
    if args.solo_diario or args.climatologia:
        p["ltm_diaria"] = False
    _mostrar_plan(p)
    if args.estimar:
        return 0

    t0 = time.perf_counter()
    bytes0 = red.contadores()["bytes"]
    try:
        r = nucleo.preparar(args.desde, args.hasta, diario=not (args.climatologia or args.indice),
                            climatologias=not (args.solo_diario or args.indice),
                            ltm_diaria=not (args.solo_diario or args.climatologia),
                            progreso=lambda h, n, texto: _barra(h, n, texto))
        print()
    except KeyboardInterrupt:
        print("\nInterrumpido. Lo descargado hasta ahora quedó guardado en la caché local.")
        return 1
    d = r.get("diario")
    if d:
        print(f"  · SST diaria: {d['peticiones']} peticiones, {d['dias_recibidos']} días recibidos, {d['dias_escritos']} "
              f"escritos ({d['finales']} finales, {d['preliminares']} preliminares"
              + (f", {d['dias_ncei']} de NOAA NCEI" if d.get("dias_ncei") else "") + f") en "
              f"{cfg.fmt_num(d['segundos'], 1)} s de descarga; {_mb(d['bytes'])}.")
    c = r.get("climatologia")
    if c:
        print(f"  · Climatologías mensuales {', '.join(c['periodos'])}: {c['peticiones']} peticiones, {_mb(c['bytes'])}, "
              f"{c['meses']} meses de medias mensuales"
              + (f"; 1991-2020 oficial frente a la calculada: diferencia máxima {cfg.fmt_num(c['dif_1991_2020'], 3)} °C"
                 if c.get("dif_1991_2020") is not None else "") + ".")
    lt = r.get("ltm_diaria")
    if lt:
        print(f"  · Climatología diaria 1991-2020 de la región Niño 1+2: {lt['peticiones']} peticiones, {_mb(lt['bytes'])}.")
    for e in r.get("detalle_errores", [])[:8]:
        print(f"      Error: {e}")
    conteo = almacen.conteo()
    print(f"Listo en {cfg.fmt_num(time.perf_counter() - t0, 0)} s ({_mb(red.contadores()['bytes'] - bytes0)} descargados). "
          f"Caché: {conteo['final']} días finales y {conteo['preliminar']} preliminares"
          f" (último {cfg.fmt_fecha(conteo['ultimo_dia']) if conteo['ultimo_dia'] else '—'}; "
          f"{conteo['ausente']} faltantes), {_mb(almacen.tamano_bytes())} en disco.")
    if conteo["ultimo_dia"] and almacen.leer_ltm_diaria() is not None:
        _indice_y_cpc(conteo["ultimo_dia"])
    return 1 if r.get("errores") else 0


if __name__ == "__main__":
    sys.exit(main())
