"""Sincronización en segundo plano de la caché de CHIRPS v3 (servidor del CHC y réplica).

Un hilo daemon que, en cada ciclo:
1. verifica el servidor del CHC (latencia real con una petición pequeña; si el
   disyuntor está abierto no se le pide nada) EN PARALELO con la consulta de la
   cobertura de la réplica de dynamical.org (`espejo.cobertura`);
2. si la réplica responde, llena de inmediato todo lo que ella cubre (no espera a
   que venza el tiempo de espera de un CHC caído, ~35 s);
3. con el resultado de la verificación, consulta qué días publica el CHC
   (índices HTML) y completa desde el CHC, en serie, lo que la réplica no cubre
   (lo más reciente) y los preliminares que el CHC ya publica como finales;
4. completa la climatología mensual si falta algún año (módulo `climatologia`,
   un año a la vez para no disparar la memoria).

Las dos fases del diario se registran como UNA sincronización.

Ciclo normal cada 6 horas; si alguna fuente falló, se reintenta a los 30
minutos (después de que venza el disyuntor de 15 min). Todo va con
try/except y registro en `datos/chirps/estado.json`: un fallo nunca tumba el
servidor.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from typing import Callable

from .. import configuracion as cfg
from . import climatologia, descarga, estado, red
from .constantes import (CHC, ESPEJO, INTERVALO_REINTENTO_S, INTERVALO_SINCRONIZACION_S, URL_README, ErrorCHIRPS,
                         sin_red, sin_sincronizacion)

log = logging.getLogger("some.chirps")

_hilo: threading.Thread | None = None
_parar = threading.Event()
_bloqueo_hilo = threading.Lock()
_bloqueo_ciclo = threading.Lock()
_ultimo_ciclo_con_fallo = False


def verificar_servidor() -> tuple[bool, int, str]:
    """Petición pequeña al CHC; registra y devuelve (ok, latencia_ms, error).

    Si el disyuntor está abierto no se toca la red (ni se registra una
    verificación nueva): se devuelve el motivo.
    """
    if sin_red():
        return False, 0, "Modo sin red activo (SOME_CHIRPS_SIN_RED=1)."
    abierto, hasta, motivo = red.info_disyuntor()
    if abierto:
        texto_hasta = hasta.strftime("%H:%M") if hasta else "—"
        return False, 0, f"El CHC está marcado como caído; no se reintenta hasta las {texto_hasta} ({motivo})."
    try:
        lat = red.latencia_ms(URL_README)
        estado.registrar_verificacion(True, lat)
        return True, lat, ""
    except Exception as e:  # noqa: BLE001
        estado.registrar_verificacion(False, 0, str(e))
        return False, 0, str(e)


def verificar_espejo(forzar: bool = True) -> dict:
    """Cobertura de la réplica (registrada en el estado). Nunca lanza."""
    return descarga.cobertura_espejo(forzar=forzar)


def _iso(x) -> str | None:
    return x.isoformat() if x else None


def sincronizar_diario(desde: date | None = None, hasta: date | None = None,
                       progreso: Callable[[int, int, int, int, str], None] | None = None,
                       fuente: str = "auto", chc_ok: bool | None = None, cob: dict | None = None,
                       trabajadores: int | None = None, registrar: bool = True) -> dict:
    """Llena lo que falte (o lo que pase de preliminar a final) en [desde, hasta].

    `fuente`: "auto" (regla de `descarga`), "chc" o "espejo". `chc_ok=False`
    evita consultar el CHC (p. ej. si la verificación acaba de fallar); `cob`
    reutiliza una cobertura de la réplica ya consultada. `trabajadores` se
    ignora (las descargas del CHC van en serie); se conserva por compatibilidad.
    Con `registrar=False` no se anota en el estado (lo hace quien combina fases).
    """
    if fuente not in descarga.FUENTES:
        raise ValueError(f"Fuente desconocida «{fuente}»; use auto, chc o espejo.")
    inicio = datetime.now()
    desde = max(desde or cfg.FECHA_INICIO, cfg.FECHA_INICIO)
    hasta_pedido = min(hasta or date.today(), date.today())

    # Réplica
    if fuente in ("auto", ESPEJO):
        cob = cob if cob is not None else descarga.cobertura_espejo(forzar=True)
    else:
        cob = None
    espejo_ok = descarga.espejo_accesible(cob)

    # CHC (índices HTML de los años que la réplica no resuelve)
    disp, error_chc = None, ""
    if fuente in ("auto", CHC) and chc_ok is not False:
        try:
            anios = descarga.anios_a_consultar_chc(desde, hasta_pedido, cob if fuente == "auto" else None)
            disp = descarga.disponibilidad(forzar=True, anios=anios)
            estado.registrar_disponibilidad(disp["ultimo_final"], disp["ultimo_preliminar"])
        except Exception as e:  # noqa: BLE001 — CHC caído: se sigue con la réplica
            error_chc = str(e)
            disp = None
    elif fuente in ("auto", CHC):
        error_chc = "El servidor del CHC no respondió a la verificación."

    limite = descarga.ultima_publicada(disp, cob)
    hasta_ef = min(hasta_pedido, limite) if limite else None
    if hasta_ef is not None and hasta_ef >= desde:
        plan = descarga.plan_llenado(desde, hasta_ef, disp, cob, fuente)
    else:
        plan = {}
    tipos_previos = descarga.almacen.tipos(desde, hasta_ef) if plan else {}
    reemplazos = sum(1 for d, (t, _o) in plan.items() if t == 2 and tipos_previos.get(d) == 1)
    r = descarga.ejecutar_plan(plan, disp=disp, fuente=fuente, cob=cob, progreso=progreso, parar=_parar)
    planificados_origen = {ESPEJO: sum(1 for _t, o in plan.values() if o == ESPEJO),
                           CHC: sum(1 for _t, o in plan.values() if o == CHC)}
    resumen = {
        "inicio": inicio.isoformat(timespec="seconds"),
        "fin": datetime.now().isoformat(timespec="seconds"),
        "fuente": fuente,
        "desde": desde.isoformat(), "hasta": _iso(hasta_ef),
        "planificados": len(plan), "planificados_por_origen": planificados_origen,
        "dias_final": r["final"], "dias_preliminar": r["preliminar"],
        "por_origen": r["por_origen"],
        "reemplazos_preliminar_a_final": reemplazos,
        "errores": r["errores"], "detalle_errores": r["detalle_errores"],
        "interrumpida": r.get("interrumpida", False),
        "sin_fuente": r.get("sin_fuente", 0), "pospuestos_chc": r.get("pospuestos_chc", 0),
        "bytes": r["bytes"], "segundos": r["segundos"],
        "chc_accesible": disp is not None and not r.get("chc_fallo"),
        "espejo_accesible": espejo_ok and not r.get("espejo_fallo"),
        "error_chc": error_chc[:300],
        "error_espejo": (cob or {}).get("error", "")[:300] if fuente != CHC else "",
        "ultimo_final_publicado": _iso(disp["ultimo_final"]) if disp else None,
        "ultimo_preliminar_publicado": _iso(disp["ultimo_preliminar"]) if disp else None,
        "espejo_final": ([_iso(x) for x in cob["final"]] if cob and cob.get("final") else None),
        "espejo_preliminar": ([_iso(x) for x in cob["preliminar"]] if cob and cob.get("preliminar") else None),
    }
    if registrar:
        estado.registrar_sincronizacion(resumen)
    return resumen


_SUMAS = ("planificados", "dias_final", "dias_preliminar", "reemplazos_preliminar_a_final", "errores", "sin_fuente",
          "pospuestos_chc", "bytes")


def _combinar(previo: dict | None, resumen: dict) -> dict:
    """Une el resumen de la fase de la réplica con el de la fase completa (una sola sincronización)."""
    if not previo:
        return resumen
    r = dict(resumen)
    for k in _SUMAS:
        r[k] = int(previo.get(k, 0)) + int(resumen.get(k, 0))
    r["segundos"] = round(float(previo.get("segundos", 0)) + float(resumen.get("segundos", 0)), 1)
    r["planificados_por_origen"] = {o: int(previo["planificados_por_origen"].get(o, 0))
                                    + int(resumen["planificados_por_origen"].get(o, 0)) for o in (ESPEJO, CHC)}
    r["por_origen"] = {o: {t: int(previo["por_origen"][o][t]) + int(resumen["por_origen"][o][t])
                           for t in ("final", "preliminar")} for o in (ESPEJO, CHC)}
    r["detalle_errores"] = (list(previo.get("detalle_errores") or []) + list(resumen.get("detalle_errores") or []))[:20]
    r["interrumpida"] = bool(previo.get("interrumpida") or resumen.get("interrumpida"))
    r["espejo_accesible"] = bool(previo.get("espejo_accesible")) and bool(resumen.get("espejo_accesible", True))
    r["inicio"] = previo.get("inicio") or resumen.get("inicio")
    r["fuente"] = "auto"
    if previo.get("desde") and resumen.get("desde"):
        r["desde"] = min(previo["desde"], resumen["desde"])
    hastas = [h for h in (previo.get("hasta"), resumen.get("hasta")) if h]
    r["hasta"] = max(hastas) if hastas else None
    return r


def ciclo() -> dict | None:
    """Un ciclo completo (verificación + diario + climatología). Seguro ante errores."""
    global _ultimo_ciclo_con_fallo
    if sin_red():
        return None
    with _bloqueo_ciclo:
        estado.marcar_verificando(True)
        try:
            # 1) Verificación del CHC en paralelo con la réplica: con el CHC caído, sus tiempos de
            #    espera (~35 s) no retrasan el llenado desde la réplica.
            verif: dict = {}

            def verificar():
                try:
                    verif["r"] = verificar_servidor()
                except Exception as e:  # noqa: BLE001
                    verif["r"] = (False, 0, str(e))

            hilo_chc = threading.Thread(target=verificar, name="chirps-verificacion", daemon=True)
            hilo_chc.start()
            cob = verificar_espejo(forzar=True)
            espejo_ok = descarga.espejo_accesible(cob)
            previo = None
            if espejo_ok and not _parar.is_set():
                previo = sincronizar_diario(fuente=ESPEJO, cob=cob, registrar=False)
            hilo_chc.join()
            ok, _lat, err = verif.get("r", (False, 0, "La verificación del CHC no terminó."))
            if not ok:
                log.warning("CHIRPS: servidor del CHC inalcanzable: %s", err)
            if not ok and not espejo_ok:
                log.warning("CHIRPS: tampoco responde la réplica de dynamical.org: %s", cob.get("error"))
                _ultimo_ciclo_con_fallo = True
                return {"ok": False, "error": err, "error_espejo": cob.get("error", "")}
            # 2) Lo que la réplica no cubre (y los finales que solo publica el CHC), desde el CHC.
            resumen = _combinar(previo, sincronizar_diario(chc_ok=ok, cob=cob, registrar=False))
            estado.registrar_sincronizacion(resumen)
            estado.marcar_verificando(False)
            _ultimo_ciclo_con_fallo = (not resumen["chc_accesible"]
                                       or (descarga.espejo_instalado() and not resumen["espejo_accesible"])
                                       or bool(resumen["interrumpida"]))
            faltan = climatologia.anios_faltantes()
            if faltan and not _parar.is_set():
                rc = climatologia.preparar_anios(faltan, trabajadores=1)
                estado.sumar_bytes(rc.get("bytes", 0))
                for e in rc.get("detalle_errores", [])[:5]:
                    estado.registrar_error(f"Climatología: {e}")
            return resumen
        except Exception as e:  # noqa: BLE001 — nunca tumbar el servidor
            log.exception("CHIRPS: error en la sincronización")
            _ultimo_ciclo_con_fallo = True
            try:
                estado.registrar_error(f"Sincronización interrumpida: {e}")
                if isinstance(e, ErrorCHIRPS):
                    estado.registrar_sincronizacion({"fin": datetime.now().isoformat(timespec="seconds"),
                                                     "errores": 1, "detalle_errores": [str(e)], "bytes": 0})
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "error": str(e)}
        finally:
            estado.marcar_verificando(False)


def proxima_espera_s() -> int:
    """Segundos hasta el próximo ciclo: 6 h, o 30 min si alguna fuente falló en el último."""
    return INTERVALO_REINTENTO_S if _ultimo_ciclo_con_fallo else INTERVALO_SINCRONIZACION_S


def _bucle() -> None:
    while not _parar.is_set():
        ciclo()
        _parar.wait(proxima_espera_s())


def iniciar_sincronizacion() -> None:
    """Arranca el hilo daemon de sincronización (no bloquea; idempotente)."""
    global _hilo
    if sin_red():
        log.info("CHIRPS: modo sin red (SOME_CHIRPS_SIN_RED=1); no se inicia la sincronización.")
        return
    if sin_sincronizacion():
        log.info("CHIRPS: SOME_CHIRPS_SIN_SINCRONIZACION=1; no se inicia la sincronización en segundo plano.")
        return
    with _bloqueo_hilo:
        if _hilo is not None and _hilo.is_alive():
            return
        _parar.clear()
        estado.marcar_verificando(True)
        _hilo = threading.Thread(target=_bucle, name="chirps-sincronizacion", daemon=True)
        _hilo.start()


def detener_sincronizacion() -> None:
    """Pide al hilo que termine (no espera a que acaben las descargas en curso)."""
    global _hilo
    _parar.set()
    with _bloqueo_hilo:
        h = _hilo
        _hilo = None
    if h is not None and h.is_alive():
        h.join(timeout=2)
    estado.marcar_verificando(False)


def sincronizando() -> bool:
    return _hilo is not None and _hilo.is_alive()
