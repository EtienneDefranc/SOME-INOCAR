"""Prepara la caché local de Copernicus Marine (análisis ANFC y climatología GLORYS12) para la demo.

Uso (desde la carpeta prototipo-some):

    python -m backend.herramientas.preparar_copernicus                    # verificación + escenarios de la demo
    python -m backend.herramientas.preparar_copernicus --verificar        # solo credenciales, metadatos y latencia
    python -m backend.herramientas.preparar_copernicus --estimar          # fragmentos y MiB por escenario, sin descargar
    python -m backend.herramientas.preparar_copernicus --escenarios perfil,comparacion
    python -m backend.herramientas.preparar_copernicus --climatologia-completa
    python -m backend.herramientas.preparar_copernicus --variables thetao,so --profundidades 0,10 \\
        --recorte nino12 --desde 2026-10-01 --hasta 2026-10-26

Requisito: una cuenta gratuita de Copernicus Marine con las credenciales guardadas por el usuario
con «copernicusmarine login» (esta herramienta NUNCA pide, muestra ni guarda usuario o contraseña).

Escenarios (C = último día de análisis):
* superficie   — 6 variables a 0 m (thetao, so, uo, vo, zos y mlotst) de C−29 a C en el área completa
                 (los 30 días que recorre la barra de tiempo del mapa);
* perfil       — thetao y so en 8 niveles y mlotst en 85° O / 2° S, de C−89 a C;
* comparacion  — thetao a 0 m en Niño 1+2, de C−364 a C (comparación con NOAA OISST);
* capa_mezcla  — mlotst del golfo de Guayaquil, de C−89 a C;
* humo         — so a 10 m del golfo (C−364 a C) y thetao a 20 m del golfo (C−29 a C);
* climatologia — climatología GLORYS12 de las 6 variables a 0 m, so a 10 m y thetao a 20 m
                 (con --climatologia-completa: las 34 combinaciones de variable y nivel).

Solo descarga lo que falta. Códigos de salida: 0 todo bien; 1 hubo errores; 2 sin credenciales,
sin paquete, modo sin red o modo simulado (no se descargó nada).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

import numpy as np

from .. import configuracion as cfg
from .. import datos_simulados as sim
from .. import copernicus as nucleo
from ..copernicus import acceso, almacen, descarga
from ..copernicus.constantes import (NIVELES_SOME, VARIABLES, ErrorCopernicus, ErrorSinAcceso,
                                     modo_configurado, raiz, sin_red)


def _barra(hechos: int, total: int, extra: str = "", ancho: int = 30) -> None:
    frac = min(1.0, hechos / total) if total else 1.0
    llenos = int(round(frac * ancho))
    texto = (f"\r  [{'#' * llenos}{'.' * (ancho - llenos)}] {cfg.fmt_num(frac * 100, 0)} % "
             f"{extra[:70]}")
    sys.stdout.write(texto.ljust(112))
    sys.stdout.flush()


def _mib(x: float) -> str:
    return f"{cfg.fmt_num(x, 0)} MiB"


def _lista(texto: str | None) -> list[str]:
    return [x.strip() for x in (texto or "").split(",") if x.strip()]


def _verificar() -> tuple[int, dict]:
    print("Verificando Copernicus Marine (metadatos, credenciales y una lectura de prueba)…")
    t0 = time.perf_counter()
    v = nucleo.verificar()
    meta = v.get("metadatos") or {}
    if meta.get("fin_tiempo"):
        print(f"  Metadatos: eje de tiempo de ARCO hasta el {cfg.fmt_fecha(meta['fin_tiempo'])} (versión "
              f"{meta.get('version') or '—'}); último día de análisis: {cfg.fmt_fecha(nucleo.ultimo_dia_analisis())}.")
    elif meta.get("error"):
        print(f"  Metadatos: no se pudieron consultar ({meta['error']}).")
    textos = {"validas": "válidas", "invalidas": "NO válidas", "ausentes": "no encontradas",
              "error": "no se pudieron verificar"}
    if v.get("credenciales"):
        print(f"  Credenciales: {textos.get(v['credenciales'], v['credenciales'])}.")
    if v.get("lectura_ok") is True:
        print(f"  Lectura de prueba correcta en {cfg.fmt_num(v['latencia_ms'], 0)} ms.")
    elif v.get("lectura_ok") is False:
        print(f"  La lectura de prueba falló: {v.get('error')}")
    print(f"  Estado: {v.get('estado')} ({cfg.fmt_num(time.perf_counter() - t0, 1)} s).")
    if v.get("credenciales") in ("ausentes", "invalidas"):
        return 2, v
    if v.get("lectura_ok") is not True:
        return 1, v
    return 0, v


def _plan_personalizado(args) -> dict:
    variables = _lista(args.variables)
    for v in variables:
        if v not in VARIABLES:
            raise SystemExit(f"Variable desconocida «{v}». Disponibles: {', '.join(VARIABLES)}.")
    profundidades = [int(float(z)) for z in _lista(args.profundidades)] or [0]
    for z in profundidades:
        if z not in NIVELES_SOME:
            raise SystemExit(f"Profundidad {z} m no válida. Niveles: {', '.join(map(str, NIVELES_SOME))} m.")
    if args.recorte:
        from .. import catalogo
        r = catalogo.recorte(args.recorte)
        if r is None:
            raise SystemExit(f"Recorte desconocido «{args.recorte}».")
        ilat, ilon = sim.indices_bbox(r["bbox"])
    else:
        ilat, ilon = np.arange(len(cfg.LAT)), np.arange(len(cfg.LON))
    corte = nucleo.ultimo_dia_analisis()
    hasta = min(args.hasta or corte, corte)
    desde = args.desde or hasta
    tramos = descarga.planificar(variables, profundidades, desde, hasta, ilat, ilon)
    return {"personalizado": {"tramos": tramos, "clim": [], "fragmentos": sum(t.fragmentos for t in tramos),
                              "mib": round(sum(t.mib for t in tramos), 1)}}


def main(argv: list[str] | None = None) -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Prepara la caché local de Copernicus Marine para la demo del SOME.")
    ap.add_argument("--verificar", action="store_true", help="solo credenciales, metadatos y latencia")
    ap.add_argument("--estimar", action="store_true", help="fragmentos y MiB por escenario, sin descargar")
    ap.add_argument("--escenarios", default=None,
                    help=f"lista separada por comas ({', '.join(nucleo.ESCENARIOS)}); por defecto todos")
    ap.add_argument("--climatologia-completa", action="store_true",
                    help="climatología de las 34 combinaciones de variable y nivel")
    ap.add_argument("--variables", default=None, help="descarga personalizada: variables separadas por comas")
    ap.add_argument("--profundidades", default=None, help="niveles SOME separados por comas (m)")
    ap.add_argument("--recorte", default=None, help="id de un recorte del catálogo (p. ej. nino12)")
    ap.add_argument("--desde", type=date.fromisoformat, default=None, help="primer día (aaaa-mm-dd)")
    ap.add_argument("--hasta", type=date.fromisoformat, default=None, help="último día (aaaa-mm-dd)")
    args = ap.parse_args(argv)

    print("SOME · Copernicus Marine (GLOBAL_ANALYSISFORECAST_PHY_001_024 y climatología GLORYS12)")
    print(f"Caché local: {raiz()}" + (" (SOME_RUTA_COPERNICUS)" if raiz() != cfg.RUTA_DATOS / "copernicus" else ""))
    if modo_configurado() == "simulado":
        print("SOME_COPERNICUS_MODO=simulado: Copernicus Marine está en modo simulado; no se descarga nada.")
        return 2
    if sin_red() and not args.estimar:
        print("El modo sin red está activo (SOME_COPERNICUS_SIN_RED=1): no se puede descargar nada.")
        return 2
    if not args.estimar:
        if not acceso.paquete_instalado():
            print(f"{acceso.MENSAJE_NO_INSTALADO}.")
            return 2
        if not acceso.credenciales_presentes():
            print(f"{acceso.MENSAJE_SIN_CREDENCIALES}. Después vuelva a ejecutar esta herramienta.")
            return 2
        codigo, _v = _verificar()
        if codigo == 2:
            print("Sin credenciales válidas de Copernicus Marine: ejecute «copernicusmarine login» (o "
                  "«copernicusmarine login --force-overwrite» si cambió la contraseña) y vuelva a intentarlo.")
            return 2
        if args.verificar or codigo:
            return codigo

    # Plan
    try:
        if args.variables:
            plan = _plan_personalizado(args)
        else:
            nombres = _lista(args.escenarios) or (["climatologia"] if args.climatologia_completa
                                                   else list(nucleo.ESCENARIOS))
            plan = nucleo.plan_escenarios(nombres, climatologia_completa=args.climatologia_completa)
    except ValueError as e:
        print(f"Error: {e}")
        return 1
    corte = nucleo.ultimo_dia_analisis()
    print(f"Último día de análisis (C): {cfg.fmt_fecha(corte)}. Plan (solo lo que falta en la caché):")
    total_frag, total_mib = 0, 0.0
    for nombre, p in plan.items():
        total_frag += p["fragmentos"]
        total_mib += p["mib"]
        descripcion = nucleo.DESCRIPCION_ESCENARIOS.get(nombre, "descarga personalizada")
        print(f"  · {nombre:<13} {p['fragmentos']:>4} fragmentos (~{_mib(p['mib'])} sin comprimir) — {descripcion}")
        for t in p["tramos"]:
            print(f"      {t.describir()}")
        if p["clim"]:
            print(f"      climatología: {len(p['clim'])} "
                  f"{'combinación' if len(p['clim']) == 1 else 'combinaciones'} de variable y nivel")
    print(f"  Total: {total_frag} fragmentos (~{_mib(total_mib)} sin comprimir; comprimidos suelen ser bastante menos).")
    if args.estimar:
        return 0
    if total_frag == 0:
        print("La caché ya tiene todo lo pedido.")
        return 0

    # Ejecución
    t0 = time.perf_counter()
    try:
        if args.variables:
            hechos = 0
            errores = 0
            for t in plan["personalizado"]["tramos"]:
                _barra(hechos, total_frag, t.describir())
                try:
                    descarga.ejecutar([t])
                except ErrorCopernicus as e:
                    errores += 1
                    print(f"\n  Error: {e}")
                    if isinstance(e, ErrorSinAcceso):
                        break
                hechos += t.fragmentos
            _barra(total_frag, total_frag, "listo")
            print()
            resultado = {"escenarios": {}, "errores": errores}
        else:
            resultado = nucleo.preparar_escenarios(list(plan), climatologia_completa=args.climatologia_completa,
                                                   progreso=lambda h, n, texto: _barra(h, n, texto))
            print()
    except ErrorCopernicus as e:
        print(f"\nNo se pudo descargar: {e}")
        return 2 if "credenciales" in str(e).lower() else 1
    except KeyboardInterrupt:
        print("\nInterrumpido. Lo descargado hasta ahora quedó guardado en la caché local.")
        return 1

    for nombre, r in (resultado.get("escenarios") or {}).items():
        if r.get("omitido"):
            print(f"  · {nombre}: omitido. {r['omitido'].rstrip('.')}.")
            continue
        print(f"  · {nombre}: {r['fragmentos']} fragmentos en {cfg.fmt_num(r['segundos'], 1)} s; días de mejor "
              f"análisis {r['dias_definitivos']}, de análisis NRT {r['dias_nrt']}"
              + (f"; climatologías {r['climatologias']}" if r.get("climatologias") else "")
              + (f"; {r['errores']} errores" if r.get("errores") else "") + ".")
        for e in r.get("detalle_errores", [])[:5]:
            print(f"      {e}")
    conteo = almacen.conteo_dias()
    print(f"Listo en {cfg.fmt_num(time.perf_counter() - t0, 0)} s. Caché local: {conteo['dias_con_dato']} días con "
          f"dato (último {cfg.fmt_fecha(conteo['ultimo_dia']) if conteo['ultimo_dia'] else '—'}), "
          f"{cfg.fmt_num(almacen.tamano_bytes() / 1_048_576, 1)} MB en disco.")
    return 1 if resultado.get("errores") else 0


if __name__ == "__main__":
    sys.exit(main())
