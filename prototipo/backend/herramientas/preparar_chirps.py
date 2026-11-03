"""Prepara la caché local de CHIRPS v3 (diario y climatología mensual).

Uso (desde la carpeta prototipo-some):

    python -m backend.herramientas.preparar_chirps                    # diario + climatología
    python -m backend.herramientas.preparar_chirps --solo-diario      # solo días
    python -m backend.herramientas.preparar_chirps --climatologia     # solo climatología 1981-2022
    python -m backend.herramientas.preparar_chirps --desde 2026-09-01 --hasta 2026-09-30
    python -m backend.herramientas.preparar_chirps --fuente espejo    # solo la réplica de dynamical.org

Fuentes del mismo producto (CHIRPS v3 del CHC):
* `auto` (por defecto): lo que cubre la réplica de dynamical.org sale de la
  réplica (rápido, sin cargar al CHC); lo más reciente, del servidor del CHC en
  serie (1 petición por segundo); los preliminares pasan a finales en cuanto
  cualquiera de las dos publique el final; si el CHC no responde, todo lo que
  cubre la réplica sale de ella.
* `chc`: solo el servidor del CHC (lento a propósito: 1 petición por segundo).
* `espejo`: solo la réplica de dynamical.org.

Solo descarga los días que faltan o que pasaron de preliminar a final, muestra
una barra de progreso por origen y un resumen final por origen.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from .. import configuracion as cfg
from ..chirps import almacen, climatologia, descarga, estado
from ..chirps.constantes import ANIO_CLIM_MAX, ANIO_CLIM_MIN, CHC, ESPEJO, sin_red
from ..chirps.sincronizacion import sincronizar_diario, verificar_servidor

_NOMBRE_FASE = {ESPEJO: "réplica", CHC: "CHC"}
_ETIQUETA_ORIGEN = {ESPEJO: "Réplica dynamical.org", CHC: "Servidor del CHC"}


def _barra(hechos: int, total: int, extra: str = "", ancho: int = 32) -> None:
    frac = hechos / total if total else 1.0
    llenos = int(round(frac * ancho))
    texto = (f"\r  [{'#' * llenos}{'.' * (ancho - llenos)}] {hechos}/{total} "
             f"({cfg.fmt_num(frac * 100, 0)} %) {extra}")
    sys.stdout.write(texto.ljust(110))
    sys.stdout.flush()


def _mb(n: int) -> str:
    return f"{cfg.fmt_num(n / 1_048_576, 1)} MB"


def _dias(n: int) -> str:
    return f"{cfg.fmt_num(n, 0)} {'día' if n == 1 else 'días'}"


def _texto_intervalo(i) -> str:
    return f"{cfg.fmt_fecha(i[0])} – {cfg.fmt_fecha(i[1])}" if i else "—"


def main(argv: list[str] | None = None) -> int:
    # La consola de Windows suele usar cp1252 y la salida redirigida falla con «→»: forzamos UTF-8.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Prepara la caché local de CHIRPS v3 para el SOME.")
    ap.add_argument("--desde", type=date.fromisoformat, default=None,
                    help="primer día (aaaa-mm-dd); por defecto 2023-10-01")
    ap.add_argument("--hasta", type=date.fromisoformat, default=None,
                    help="último día (aaaa-mm-dd); por defecto el último publicado")
    ap.add_argument("--fuente", choices=("auto", CHC, ESPEJO), default="auto",
                    help="auto (réplica para lo que cubre y CHC para lo más reciente), chc o espejo")
    grupo = ap.add_mutually_exclusive_group()
    grupo.add_argument("--climatologia", action="store_true",
                       help=f"solo la climatología mensual {ANIO_CLIM_MIN}-{ANIO_CLIM_MAX}")
    grupo.add_argument("--solo-diario", action="store_true", help="solo los datos diarios")
    args = ap.parse_args(argv)

    if sin_red():
        print("El modo sin red está activo (SOME_CHIRPS_SIN_RED=1): no se puede descargar nada.")
        return 2

    t0 = time.perf_counter()
    codigo = 0
    chc_ok, cob = None, None
    if not args.climatologia:
        if args.fuente in ("auto", CHC):
            print("Verificando el servidor del Climate Hazards Center…")
            chc_ok, lat, err = verificar_servidor()
            print(f"  Servidor accesible ({cfg.fmt_num(lat, 0)} ms)." if chc_ok else f"  No responde: {err}")
        if args.fuente in ("auto", ESPEJO):
            print("Consultando la réplica de dynamical.org (Zarr/Icechunk, CC BY 4.0)…")
            cob = descarga.cobertura_espejo(forzar=True)
            if descarga.espejo_accesible(cob):
                print(f"  Réplica accesible ({cfg.fmt_num(cob['latencia_ms'], 0)} ms): final "
                      f"{_texto_intervalo(cob['final'])}; preliminar {_texto_intervalo(cob['preliminar'])}.")
            else:
                print(f"  No disponible: {cob['error']}")
        espejo_ok = descarga.espejo_accesible(cob)
        if args.fuente == CHC and not chc_ok:
            return 1
        if args.fuente == ESPEJO and not espejo_ok:
            return 1
        if args.fuente == "auto" and not chc_ok and not espejo_ok:
            print("Ninguna de las dos fuentes responde; la caché local queda como está.")
            return 1

        print(f"Datos diarios (fuente: {args.fuente}):")
        fase = {"actual": None}

        def progreso(h, total, errores, b, origen):
            if fase["actual"] not in (None, origen):
                sys.stdout.write("\n")
            fase["actual"] = origen
            _barra(h, total, f"· {_NOMBRE_FASE.get(origen, origen)} · {_mb(b)} del CHC · {errores} errores")

        r = sincronizar_diario(args.desde, args.hasta, progreso=progreso, fuente=args.fuente,
                               chc_ok=chc_ok, cob=cob)
        if r["planificados"]:
            print()
        else:
            print("  La caché diaria ya está al día.")
        po = r["por_origen"]
        for origen in (ESPEJO, CHC):
            planif = r["planificados_por_origen"].get(origen, 0)
            obt = po[origen]["final"] + po[origen]["preliminar"]
            if planif or obt:
                print(f"  {_ETIQUETA_ORIGEN[origen]}: "
                      f"{_dias(po[origen]['final'])} finales y {_dias(po[origen]['preliminar'])} preliminares "
                      f"(planificados: {planif}).")
        print(f"  Total: {r['dias_final']} finales y {r['dias_preliminar']} preliminares "
              f"({r['reemplazos_preliminar_a_final']} preliminares reemplazados por finales); "
              f"{_mb(r['bytes'])} del CHC en {cfg.fmt_num(r['segundos'], 1)} s; errores: {r['errores']}.")
        if r.get("sin_fuente"):
            print(f"  {_dias(r['sin_fuente'])} sin fuente disponible por ahora.")
        if r.get("pospuestos_chc"):
            print(f"  {_dias(r['pospuestos_chc'])} del CHC quedaron para la próxima sincronización.")
        for e in r["detalle_errores"][:10]:
            print(f"    · {e}")
        if r.get("interrumpida"):
            print("  La descarga del CHC se interrumpió porque el servidor dejó de responder; vuelva a "
                  "ejecutar el script más tarde (solo descargará lo que falte).")
        if r.get("ultimo_final_publicado") or r.get("ultimo_preliminar_publicado"):
            print(f"  Publicado en el CHC: final hasta {cfg.fmt_fecha(r['ultimo_final_publicado'])}, "
                  f"preliminar hasta {cfg.fmt_fecha(r['ultimo_preliminar_publicado'])}.")
        if r.get("espejo_final") or r.get("espejo_preliminar"):
            print(f"  En la réplica: final {_texto_intervalo(r['espejo_final'])}, "
                  f"preliminar {_texto_intervalo(r['espejo_preliminar'])}.")
        if r["errores"]:
            codigo = 1

    if not args.solo_diario:
        faltan = climatologia.anios_faltantes()
        print(f"Climatología mensual {ANIO_CLIM_MIN}-{ANIO_CLIM_MAX} "
              f"({len(faltan)} {'año' if len(faltan) == 1 else 'años'} por preparar):")
        if faltan:
            rc = climatologia.preparar_anios(
                faltan, progreso=lambda h, total, e: _barra(h, total, f"años · {e} errores"))
            print()
            estado.sumar_bytes(rc.get("bytes", 0))
            print(f"  {rc['anios']} años preparados; {_mb(rc.get('bytes', 0))} en "
                  f"{cfg.fmt_num(rc.get('segundos', 0), 1)} s; errores: {rc['errores']}.")
            for e in rc.get("detalle_errores", [])[:10]:
                print(f"    · {e}")
                estado.registrar_error(f"Climatología: {e}")
            if rc["errores"]:
                codigo = 1
        else:
            print("  La climatología ya está completa.")

    corte = almacen.ultimo_dia()
    print("Resumen de la caché local:")
    if corte:
        c = almacen.conteo(cfg.FECHA_INICIO, corte)
        print(f"  Días: {c['final']} finales + {c['preliminar']} preliminares "
              f"({cfg.fmt_fecha(cfg.FECHA_INICIO)} – {cfg.fmt_fecha(corte)}); faltantes: {c['ausente']}.")
        print(f"  Por origen: CHC {_dias(c['por_origen'][CHC])}; réplica dynamical.org "
              f"{_dias(c['por_origen'][ESPEJO])}.")
        if c["ultimo_final"]:
            print(f"  Final hasta {cfg.fmt_fecha(c['ultimo_final'])}"
                  + (f"; preliminar {cfg.fmt_fecha(c['primer_preliminar'])} – "
                     f"{cfg.fmt_fecha(c['ultimo_preliminar'])}." if c["preliminar"] else "."))
    else:
        print("  Sin datos diarios.")
    anios = almacen.anios_mensuales()
    print(f"  Climatología: {len(anios)} años" + (f" ({anios[0]}-{anios[-1]})." if anios else "."))
    print(f"  Tamaño en disco: {_mb(almacen.tamano_bytes())}. Tiempo total: "
          f"{cfg.fmt_num(time.perf_counter() - t0, 1)} s.")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
