"""Llenado de la caché de CHIRPS v3 desde dos fuentes del MISMO producto.

* **Servidor del CHC** (`data.chc.ucsb.edu`): GeoTIFF diario a 0.05° (final
  «rnl» y preliminar «sat») leído por rangos HTTP y agregado 5×5, igual que la
  réplica. Cortés: 1 petición/s en total entre todos los procesos del SOME y un
  archivo a la vez (`red.un_archivo`, también entre procesos) y disyuntor de
  15 min si deja de responder. Antes de bajar cada día se vuelve a mirar la
  caché bajo ese bloqueo y el día se guarda en cuanto llega: dos hilos o
  procesos que planificaron lo mismo no piden dos veces el mismo GeoTIFF.
* **Réplica de dynamical.org** (Zarr/Icechunk en S3, CC BY 4.0): módulo
  `espejo.py`, importado de forma perezosa y tolerante (si no existe o faltan
  icechunk/zarr, simplemente no se usa). Lecturas grandes por bloques de meses.

Regla de llenado (`plan_llenado` + `ejecutar_plan`):

a) días que cubre la réplica → réplica (rápida, no carga al CHC);
b) días más recientes que la réplica y publicados en el CHC → CHC, en serie;
c) un preliminar pasa a final en cuanto cualquiera de las dos lo publique
   (final > preliminar; dentro del mismo tipo no se vuelve a descargar, salvo
   los finales provisionales «p25» de versiones anteriores, que se reemplazan
   por el final a 0.05°);
d) si el CHC está caído, todo lo que cubre la réplica sale de la réplica; si
   la réplica falla, lo que publique el CHC sale del CHC; si ambas fallan se
   sirve la caché local.

La disponibilidad del CHC sale de sus índices HTML (`disponibilidad`, caché de
30 min por año) y la de la réplica de `espejo.cobertura()` (`cobertura_espejo`).
Tras un fallo de lectura de la réplica se marca «caída» unos minutos
(`marcar_espejo_caido`): se registra en el estado, se invalida su cobertura y
las consultas bajo demanda no la reintentan hasta que vence la marca.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable

import numpy as np

from .. import configuracion as cfg
from . import almacen, lector_tiff, red
from .constantes import (AUSENTE, CACHE_DISPONIBILIDAD_S, DIAS_BLOQUE_ESPEJO, CHC, COLUMNAS_NATIVAS, ESPEJO,
                         ESPEJO_CAIDO_S, FILAS_NATIVAS, FINAL, LAT_NORTE, LAT_SUR, LON_ESTE, LON_OESTE, NOMBRE_TIPO,
                         ORIGEN_CHC, ORIGEN_ESPEJO, PRELIMINAR, TRABAJADORES, URL_FINAL_ANIO, URL_PRELIM_ANIO,
                         ErrorCHIRPS, sin_red, url_final, url_prelim)
from .malla import agregar_5x5, reproyectar_a_some

log = logging.getLogger("some.chirps")

FUENTES = ("auto", CHC, ESPEJO)
_PRODUCTO = {FINAL: "final", PRELIMINAR: "preliminar"}

_RE_FINAL = re.compile(r'href="chirps-v3\.0\.rnl\.(\d{4})\.(\d{2})\.(\d{2})\.tif"')
_RE_PRELIM = re.compile(r'href="chirps-v3\.0\.prelim\.(\d{4})\.(\d{2})\.(\d{2})\.tif"')

_bloqueo_disp = threading.Lock()
_disp_anios: dict[int, tuple[float, set[date], set[date]]] = {}
_disp_cache: dict | None = None
_bloqueo_espejo = threading.Lock()        # una lectura de la réplica a la vez (evita duplicar trabajo)
_espejo_caido_hasta = 0.0                 # time.monotonic() hasta el que las consultas no reintentan la réplica


# ---------------------------------------------------------------------------
# Réplica de dynamical.org (importación perezosa y tolerante)
# ---------------------------------------------------------------------------
_espejo_modulo = None
_espejo_error_importacion = ""


def modulo_espejo():
    """Módulo `backend.chirps.espejo`, o None si aún no existe o no se puede importar. Nunca lanza."""
    global _espejo_modulo, _espejo_error_importacion
    if _espejo_modulo is not None:
        return _espejo_modulo
    try:
        from . import espejo as m           # noqa: PLC0415 — importación perezosa a propósito
    except Exception as e:  # noqa: BLE001
        _espejo_error_importacion = f"{type(e).__name__}: {e}"
        return None
    _espejo_modulo = m
    return m


def espejo_instalado() -> bool:
    """True si el módulo de la réplica existe y sus bibliotecas (icechunk, zarr) están instaladas (sin red)."""
    if modulo_espejo() is None:
        return False
    try:
        return all(importlib.util.find_spec(n) is not None for n in ("icechunk", "zarr"))
    except (ImportError, ValueError):
        return False


def _fecha(x) -> date | None:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def _intervalo(x) -> tuple[date, date] | None:
    if not x:
        return None
    try:
        a, b = _fecha(x[0]), _fecha(x[1])
    except (TypeError, ValueError, IndexError):
        return None
    return (a, b) if a and b and a <= b else None


def _cobertura_vacia(error: str) -> dict:
    return {"final": None, "preliminar": None, "consultado": datetime.now().isoformat(timespec="seconds"),
            "latencia_ms": 0, "error": error}


def cobertura_espejo(forzar: bool = False, registrar: bool = True) -> dict:
    """Cobertura de la réplica, normalizada: {"final": (date, date) | None, "preliminar": …,
    "consultado", "latencia_ms", "error"}. Usa la red (vía `espejo.cobertura`, con su caché de
    30 min) y registra el resultado en el estado persistente. Nunca lanza."""
    if sin_red():
        return _cobertura_vacia("Modo sin red activo (SOME_CHIRPS_SIN_RED=1): no se consulta la réplica.")
    m = modulo_espejo()
    if m is None:
        cob = _cobertura_vacia("El acceso a la réplica de dynamical.org (backend/chirps/espejo.py) no está "
                               f"disponible ({_espejo_error_importacion or 'módulo ausente'}).")
    else:
        try:
            if not m.disponible():
                cob = _cobertura_vacia("Faltan las bibliotecas icechunk y zarr para leer la réplica de "
                                       "dynamical.org (pip install -r requirements.txt).")
            else:
                crudo = m.cobertura(forzar=forzar) or {}
                cob = {"final": _intervalo(crudo.get("final")),
                       "preliminar": _intervalo(crudo.get("preliminar")),
                       "consultado": str(crudo.get("consultado") or datetime.now().isoformat(timespec="seconds")),
                       "latencia_ms": int(crudo.get("latencia_ms") or 0),
                       "error": str(crudo.get("error") or "")}
                if not cob["error"] and not (cob["final"] or cob["preliminar"]):
                    cob["error"] = "La réplica de dynamical.org no informó ninguna cobertura."
        except Exception as e:  # noqa: BLE001 — el contrato dice que no lanza, pero por si acaso
            cob = _cobertura_vacia(f"No se pudo consultar la réplica de dynamical.org: {e}")
    if forzar and espejo_accesible(cob):
        _limpiar_caida_espejo()           # una apertura real acaba de funcionar
    if registrar:
        try:
            from . import estado
            estado.registrar_espejo(cob, instalado=m is not None and not cob["error"].startswith("Faltan"))
        except Exception:  # noqa: BLE001
            log.debug("CHIRPS: no se pudo registrar la cobertura de la réplica", exc_info=True)
    return cob


def espejo_accesible(cob: dict | None) -> bool:
    return bool(cob) and not cob.get("error") and bool(cob.get("final") or cob.get("preliminar"))


def _limpiar_caida_espejo() -> None:
    global _espejo_caido_hasta
    _espejo_caido_hasta = 0.0


def espejo_caido_reciente() -> bool:
    """True si una lectura de la réplica falló hace menos de `ESPEJO_CAIDO_S` (las consultas no la reintentan)."""
    return time.monotonic() < _espejo_caido_hasta


def marcar_espejo_caido(error: str) -> None:
    """Tras un fallo de LECTURA de la réplica: marca «caída» unos minutos, invalida su cobertura en
    memoria (para que nadie la dé por accesible con datos de hace 30 min) y lo registra en el estado
    (la tarjeta de fuentes deja de mostrarla «operativa»). Nunca lanza."""
    global _espejo_caido_hasta
    _espejo_caido_hasta = time.monotonic() + ESPEJO_CAIDO_S
    m = modulo_espejo()
    try:
        if m is not None and hasattr(m, "registrar_fallo_lectura"):
            m.registrar_fallo_lectura(error)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import estado
        estado.registrar_espejo(_cobertura_vacia(f"Falló la lectura de datos de la réplica: {error}"),
                                instalado=True)
    except Exception:  # noqa: BLE001
        log.debug("CHIRPS: no se pudo registrar el fallo de la réplica", exc_info=True)


def _en(intervalo, d: date) -> bool:
    return bool(intervalo) and intervalo[0] <= d <= intervalo[1]


# ---------------------------------------------------------------------------
# Disponibilidad en el servidor del CHC
# ---------------------------------------------------------------------------
def _listar(url: str, patron: re.Pattern) -> set[date]:
    html = red.obtener_404_vacio(url)
    if html is None:
        return set()
    texto = html.decode("utf-8", "replace")
    return {date(int(a), int(m), int(d)) for a, m, d in patron.findall(texto)}


def _anios_recientes(hoy: date) -> set[int]:
    """Años que siempre se listan para conocer lo último publicado (el final va 1-2 meses atrás)."""
    return {hoy.year, hoy.year - 1} if hoy.month <= 3 else {hoy.year}


def disponibilidad(forzar: bool = False, anios=None) -> dict:
    """{"final": set[date], "preliminar": set[date], "ultimo_final", "ultimo_preliminar", "consultado", "anios"}.

    Lista los índices HTML del CHC de los `anios` pedidos (por defecto, desde
    `cfg.FECHA_INICIO`) más el año en curso. Caché en memoria de 30 minutos por
    año. Lanza `red.ErrorConexion` si el CHC no responde.
    """
    global _disp_cache
    hoy = date.today()
    pedidos = set(range(cfg.FECHA_INICIO.year, hoy.year + 1)) if anios is None else set(anios)
    pedidos = sorted(a for a in pedidos | _anios_recientes(hoy) if cfg.FECHA_INICIO.year <= a <= hoy.year)
    with _bloqueo_disp:
        for anio in pedidos:
            previo = _disp_anios.get(anio)
            if forzar or previo is None or time.monotonic() - previo[0] >= CACHE_DISPONIBILIDAD_S:
                finales_a = _listar(URL_FINAL_ANIO.format(anio=anio), _RE_FINAL)
                prelims_a = _listar(URL_PRELIM_ANIO.format(anio=anio), _RE_PRELIM)
                _disp_anios[anio] = (time.monotonic(), finales_a, prelims_a)
        finales = {d for a in pedidos for d in _disp_anios[a][1] if d <= hoy}
        prelims = {d for a in pedidos for d in _disp_anios[a][2] if d <= hoy}
        _disp_cache = {
            "final": finales,
            "preliminar": prelims,
            "ultimo_final": max(finales) if finales else None,
            "ultimo_preliminar": max(prelims) if prelims else None,
            "consultado": datetime.now().isoformat(timespec="seconds"),
            "anios": pedidos,
            "_t": time.monotonic(),
        }
        return _disp_cache


def disponibilidad_en_cache() -> dict | None:
    """Última disponibilidad consultada del CHC (sin tocar la red)."""
    return _disp_cache


def ultima_publicada(disp: dict | None, cob: dict | None = None) -> date | None:
    """Último día publicado por cualquiera de las dos fuentes (final o preliminar)."""
    fechas = []
    if disp:
        fechas += [f for f in (disp.get("ultimo_final"), disp.get("ultimo_preliminar")) if f]
    if cob:
        fechas += [i[1] for i in (cob.get("final"), cob.get("preliminar")) if i]
    return min(max(fechas), date.today()) if fechas else None


def anios_a_consultar_chc(desde: date, hasta: date, cob: dict | None) -> set[int]:
    """Años cuyos índices del CHC hacen falta: los de días que la réplica no puede resolver."""
    hoy = date.today()
    hasta = min(hasta, hoy)
    anios: set[int] = set()
    if hasta < desde:
        return anios
    for d, (t, cod) in almacen.tipos_y_codigos(desde, hasta).items():
        # Un día sin final definitivo que la réplica no da como final: el CHC podría tenerlo
        # (o tener al menos el preliminar).
        if (t != FINAL or almacen.es_provisional(t, cod)) and not (cob and _en(cob.get("final"), d)):
            anios.add(d.year)
    return anios


# ---------------------------------------------------------------------------
# Lectura de un día del CHC
# ---------------------------------------------------------------------------
def descargar_dia(fecha: date, tipo: int, espera_max: float | None = None) -> np.ndarray:
    """Campo (49, 69) en mm/día sobre la malla SOME para la fecha y el tipo dados (servidor del CHC).

    Final y preliminar se leen a 0.05° y se agregan 5×5 (como la réplica y la
    climatología). Solo se descarga un archivo del CHC a la vez entre todos los
    hilos y procesos del SOME (`red.un_archivo`); con `espera_max` (segundos) se
    desiste si otro archivo tarda en terminar.
    """
    url = url_final(fecha) if tipo == FINAL else url_prelim(fecha)
    with red.un_archivo(espera_max):
        info = lector_tiff.leer_info(url)
        f0, f1, c0, c1 = info.ventana(LAT_NORTE, LAT_SUR, LON_OESTE, LON_ESTE)
        crudo = lector_tiff.leer_ventana(info, f0, f1, c0, c1)        # norte → sur
    if crudo.shape != (FILAS_NATIVAS * 5, COLUMNAS_NATIVAS * 5):
        raise ErrorCHIRPS(f"Recorte inesperado {crudo.shape} en {url} (se esperaba la malla de 0.05°)")
    return reproyectar_a_some(agregar_5x5(crudo)[::-1, :])


# ---------------------------------------------------------------------------
# Plan de llenado
# ---------------------------------------------------------------------------
def plan_llenado(desde: date, hasta: date, disp: dict | None, cob: dict | None,
                 fuente: str = "auto", solo_ausentes: bool = False) -> dict[date, tuple[int, str]]:
    """{fecha: (tipo, origen)} de lo que conviene obtener en [desde, hasta].

    `disp` es la disponibilidad del CHC (None si no responde) y `cob` la
    cobertura de la réplica (None o con error si no responde). `fuente` limita
    las fuentes ("auto", "chc" o "espejo"). Con `solo_ausentes` no se planifican
    los reemplazos de preliminar por final (uso bajo demanda). Los finales
    provisionales «p25» se planifican para reemplazo como un preliminar.
    """
    if fuente not in FUENTES:
        raise ValueError(f"Fuente desconocida «{fuente}»; use auto, chc o espejo.")
    usar_espejo = fuente in ("auto", ESPEJO) and espejo_accesible(cob)
    usar_chc = fuente in ("auto", CHC) and disp is not None
    if hasta < desde or not (usar_espejo or usar_chc):
        return {}
    fin_e = cob.get("final") if usar_espejo else None
    pre_e = cob.get("preliminar") if usar_espejo else None
    fin_c = disp["final"] if usar_chc else set()
    pre_c = disp["preliminar"] if usar_chc else set()
    plan: dict[date, tuple[int, str]] = {}
    for d, (t, cod) in almacen.tipos_y_codigos(desde, hasta).items():
        if (t == FINAL and not almacen.es_provisional(t, cod)) or (solo_ausentes and t != AUSENTE):
            continue
        if _en(fin_e, d):
            plan[d] = (FINAL, ESPEJO)
        elif d in fin_c:
            plan[d] = (FINAL, CHC)
        elif t != AUSENTE:
            continue                      # ya hay preliminar: se espera al final
        elif _en(pre_e, d):
            plan[d] = (PRELIMINAR, ESPEJO)
        elif d in pre_c:
            plan[d] = (PRELIMINAR, CHC)
    return plan


def plan_descarga(desde: date, hasta: date, disp: dict) -> dict[date, int]:
    """Compatibilidad: {fecha: tipo} de lo que falta según solo el CHC."""
    return {d: t for d, (t, _o) in plan_llenado(desde, hasta, disp, None, CHC).items()}


def _bloques_espejo(dias: dict[date, int], cob: dict | None = None):
    """Agrupa {fecha: tipo} en bloques (tipo, [fechas]) de lectura de la réplica.

    Cada bloque cae dentro de un fragmento temporal de la réplica (DIAS_BLOQUE_ESPEJO días
    contados desde el primer día del producto, según `cob`); sin cobertura conocida se
    agrupa por año calendario.
    """
    grupos: dict[tuple[int, int], list[date]] = {}
    for d, t in dias.items():
        intervalo = (cob or {}).get(_PRODUCTO[t])
        if intervalo:
            clave = (t, (d - intervalo[0]).days // DIAS_BLOQUE_ESPEJO)
        else:
            clave = (t, d.year)
        grupos.setdefault(clave, []).append(d)
    for (t, _b), fechas in sorted(grupos.items(), key=lambda kv: (min(kv[1]), -kv[0][0])):
        yield t, sorted(fechas)


def _nuevo_resultado() -> dict:
    return {"final": 0, "preliminar": 0,
            "por_origen": {ESPEJO: {"final": 0, "preliminar": 0}, CHC: {"final": 0, "preliminar": 0}},
            "errores": 0, "detalle_errores": [], "bytes": 0, "segundos": 0.0, "interrumpida": False,
            "sin_fuente": 0, "pospuestos_chc": 0, "pospuestos_espejo": 0, "ya_presentes": 0,
            "espejo_fallo": False, "chc_fallo": False}


def ampliar_a_fragmentos(desde: date, hasta: date, cob: dict | None,
                         minimo: date | None = None) -> list[tuple[date, date]]:
    """Intervalos que cubren completos los fragmentos de la réplica que toca [desde, hasta].

    Cada lectura de la réplica descarga y descomprime el fragmento entero de
    `DIAS_BLOQUE_ESPEJO` días, así que conviene guardar de una vez todos sus días.
    El final cubre lo que está en su cobertura; el preliminar, solo lo posterior
    al final. Los intervalos se acotan a [minimo, fin de la cobertura] y a hoy.
    """
    if not espejo_accesible(cob):
        return [(desde, hasta)]
    minimo = minimo or desde
    hoy = date.today()
    fin_final = cob["final"][1] if cob.get("final") else None
    intervalos: list[tuple[date, date]] = []
    for producto in ("final", "preliminar"):
        cobertura = cob.get(producto)
        if not cobertura:
            continue
        a, b = cobertura
        lo = max(desde, a)
        if producto == "preliminar" and fin_final:
            lo = max(lo, fin_final + timedelta(days=1))
        hi = min(hasta, b)
        if hi < lo:
            continue
        k0 = (lo - a).days // DIAS_BLOQUE_ESPEJO
        k1 = (hi - a).days // DIAS_BLOQUE_ESPEJO
        ini = a + timedelta(days=k0 * DIAS_BLOQUE_ESPEJO)
        fin = a + timedelta(days=(k1 + 1) * DIAS_BLOQUE_ESPEJO - 1)
        if producto == "preliminar" and fin_final:
            ini = max(ini, fin_final + timedelta(days=1))
        ini, fin = max(ini, minimo), min(fin, b, hoy)
        if fin >= ini:
            intervalos.append((ini, fin))
    intervalos.append((desde, hasta))
    intervalos.sort()
    unidos: list[tuple[date, date]] = []
    for ini, fin in intervalos:
        if unidos and ini <= unidos[-1][1] + timedelta(days=1):
            unidos[-1] = (unidos[-1][0], max(unidos[-1][1], fin))
        else:
            unidos.append((ini, fin))
    return unidos


def _error(resultado: dict, texto: str) -> None:
    resultado["errores"] += 1
    if len(resultado["detalle_errores"]) < 20:
        resultado["detalle_errores"].append(texto[:400])


def ejecutar_plan(plan: dict[date, tuple[int, str]], disp: dict | None = None, fuente: str = "auto",
                  cob: dict | None = None,
                  progreso: Callable[[int, int, int, int, str], None] | None = None,
                  max_dias_chc: int | None = None, max_segundos_chc: float | None = None,
                  mas_recientes_primero: bool = False, espera_archivo: float | None = None,
                  parar: threading.Event | None = None, lote_guardado: int = 1,
                  espera_espejo: float | None = None) -> dict:
    """Obtiene los días del plan: primero la réplica (bloques de ~1 año alineados con sus fragmentos
    según `cob`), luego el CHC (en serie).

    Si la réplica no entrega un día (o falla) y el CHC lo publica, ese día pasa
    a la cola del CHC (salvo `fuente="espejo"`). Un fallo de lectura de la réplica
    la marca caída (`marcar_espejo_caido`). Con `espera_espejo` (segundos) no se
    espera más que eso a que termine otra lectura de la réplica en curso: esos días
    quedan para la sincronización (`pospuestos_espejo`). Si el disyuntor del CHC se
    abre, la cola del CHC se interrumpe (se reintentará en la próxima
    sincronización). Cada día del CHC se vuelve a comparar con la caché bajo el
    bloqueo de archivo del CHC y se guarda en cuanto llega (`lote_guardado` se
    conserva por compatibilidad y ya no agrupa). `progreso(hechos, total, errores,
    bytes, origen)` se llama tras cada bloque de la réplica y cada día del CHC.

    Devuelve {"final", "preliminar", "por_origen": {"espejo": {...}, "chc": {...}}, "errores",
    "detalle_errores", "bytes", "segundos", "interrumpida", "sin_fuente", "pospuestos_chc",
    "pospuestos_espejo", "ya_presentes", "espejo_fallo", "chc_fallo"}.
    """
    t0 = time.perf_counter()
    bytes0 = red.bytes_descargados()
    r = _nuevo_resultado()
    if not plan:
        return r
    total = len(plan)
    hechos = 0
    del_espejo = {d: t for d, (t, o) in plan.items() if o == ESPEJO}
    del_chc = {d: t for d, (t, o) in plan.items() if o == CHC}

    def avisar(origen: str) -> None:
        if progreso:
            progreso(hechos, total, r["errores"], red.bytes_descargados() - bytes0, origen)

    def al_chc_o_descartar(d: date, t: int) -> None:
        nombre = _PRODUCTO[t]
        if fuente != ESPEJO and disp is not None and d in disp.get(nombre, ()):
            del_chc[d] = t
        elif t == FINAL and fuente != ESPEJO and disp is not None and d in disp.get("preliminar", ()) \
                and almacen.tipos(d, d)[d] == AUSENTE:
            del_chc[d] = PRELIMINAR          # al menos el preliminar del CHC
        else:
            r["sin_fuente"] += 1

    # 1) Réplica de dynamical.org --------------------------------------------------------
    m = modulo_espejo() if del_espejo else None
    fallos_espejo = 0
    for t, fechas in _bloques_espejo(del_espejo, cob):
        if parar is not None and parar.is_set():
            r["interrumpida"] = True
            break
        if m is None or fallos_espejo >= 2:
            for d in fechas:
                al_chc_o_descartar(d, t)
            hechos += len(fechas)
            avisar(ESPEJO)
            continue
        if not _bloqueo_espejo.acquire(timeout=-1 if espera_espejo is None else max(0.0, espera_espejo)):
            r["pospuestos_espejo"] += len(fechas)        # otra lectura en curso: lo completa la sincronización
            hechos += len(fechas)
            avisar(ESPEJO)
            continue
        try:
            # Otro hilo pudo haber guardado parte del bloque mientras se esperaba.
            guardados = almacen.tipos_y_codigos(fechas[0], fechas[-1])
            fechas_vivas = [d for d in fechas
                            if almacen.mejora_dia(guardados.get(d, (AUSENTE, 0)), t, ORIGEN_ESPEJO)]
            obtenidos: dict[date, np.ndarray] = {}
            if fechas_vivas:
                try:
                    fechas_e, valores = m.leer_diario(fechas_vivas[0], fechas_vivas[-1], _PRODUCTO[t])
                    valores = np.asarray(valores, dtype=np.float32)
                    if valores.ndim != 3 or valores.shape[1:] != (almacen.NLAT, almacen.NLON) \
                            or valores.shape[0] != len(fechas_e):
                        raise ErrorCHIRPS(f"forma inesperada {valores.shape} para {len(fechas_e)} fechas")
                    obtenidos = {_fecha(f): valores[i] for i, f in enumerate(fechas_e)}
                    fallos_espejo = 0
                    _limpiar_caida_espejo()
                except Exception as e:  # noqa: BLE001 — se registra y se recurre al CHC
                    fallos_espejo += 1
                    r["espejo_fallo"] = True
                    texto = red.texto_error(e) if not isinstance(e, ErrorCHIRPS) else str(e)
                    log.info("CHIRPS: falló la lectura de la réplica: %r", e)
                    _error(r, f"Réplica dynamical.org ({NOMBRE_TIPO[t]}, {cfg.fmt_fecha(fechas_vivas[0])} – "
                              f"{cfg.fmt_fecha(fechas_vivas[-1])}): {texto}")
                    marcar_espejo_caido(texto)
            lote = {}
            for d in fechas_vivas:
                campo = obtenidos.get(d)
                if campo is not None and np.isfinite(campo).any():
                    campo = np.where(np.isfinite(campo), np.maximum(campo, 0.0), np.nan).astype(np.float32)
                    lote[d] = (campo, t, ESPEJO)
                else:
                    al_chc_o_descartar(d, t)
            if lote:
                almacen.guardar(lote)
                r[_PRODUCTO[t]] += len(lote)
                r["por_origen"][ESPEJO][_PRODUCTO[t]] += len(lote)
        finally:
            _bloqueo_espejo.release()
        hechos += len(fechas)
        avisar(ESPEJO)

    # 2) Servidor del CHC, en serie -------------------------------------------------------
    cola = sorted(del_chc.items(), reverse=mas_recientes_primero)
    if max_dias_chc is not None and len(cola) > max_dias_chc:
        recientes = sorted(del_chc.items(), reverse=True)[:max_dias_chc]
        r["pospuestos_chc"] += len(cola) - len(recientes)
        cola = sorted(recientes, reverse=mas_recientes_primero)
    total = hechos + len(cola)
    t_chc = time.perf_counter()
    for i, (d, t) in enumerate(cola):
        if parar is not None and parar.is_set():
            r["interrumpida"] = True
            r["pospuestos_chc"] += len(cola) - i
            break
        if max_segundos_chc is not None and time.perf_counter() - t_chc > max_segundos_chc:
            r["pospuestos_chc"] += len(cola) - i
            break
        try:
            # Bajo el bloqueo de archivo del CHC (entre hilos y procesos): si otro ya guardó el día
            # mientras se esperaba el turno, no se vuelve a pedir; si no, se baja y se guarda ya.
            with red.un_archivo(espera_archivo):
                if almacen.mejora_dia(almacen.tipos_y_codigos(d, d)[d], t, ORIGEN_CHC):
                    campo = descargar_dia(d, t, espera_max=espera_archivo)
                    almacen.guardar({d: (campo, t, CHC)})
                    r[_PRODUCTO[t]] += 1
                    r["por_origen"][CHC][_PRODUCTO[t]] += 1
                else:
                    r["ya_presentes"] += 1
        except red.ErrorConexion as e:
            r["chc_fallo"] = True
            _error(r, f"CHC {cfg.fmt_fecha(d)}: {e}")
            if red.disyuntor_abierto():
                r["interrumpida"] = True
                r["pospuestos_chc"] += len(cola) - i - 1
                _error(r, "Descarga del CHC interrumpida: el servidor dejó de responder; se reintentará "
                          "en la próxima sincronización.")
                break
        except Exception as e:  # noqa: BLE001 — se registra y se sigue
            _error(r, f"CHC {cfg.fmt_fecha(d)}: {e}")
        hechos += 1
        avisar(CHC)
    r["bytes"] = red.bytes_descargados() - bytes0
    r["segundos"] = round(time.perf_counter() - t0, 1)
    return r


def descargar_dias(plan: dict[date, int], trabajadores: int = TRABAJADORES,
                   progreso: Callable[[int, int, int, int], None] | None = None,
                   lote_guardado: int = 1) -> dict:
    """Compatibilidad: descarga del CHC (en serie) los días {fecha: tipo} y los guarda."""
    def adaptar(h, total, e, b, _origen):
        if progreso:
            progreso(h, total, e, b)
    return ejecutar_plan({d: (t, CHC) for d, t in plan.items()}, fuente=CHC, progreso=adaptar,
                         lote_guardado=lote_guardado)


def dias_entre(inicio: date, fin: date):
    d = inicio
    while d <= fin:
        yield d
        d += timedelta(days=1)
