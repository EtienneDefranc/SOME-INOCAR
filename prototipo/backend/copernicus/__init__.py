"""Acceso real a Copernicus Marine (análisis físico global ANFC y climatología GLORYS12) para el SOME.

Producto diario: GLOBAL_ANALYSISFORECAST_PHY_001_024 (1/12°, 50 niveles, DOI
10.48670/moi-00016), leído con el Copernicus Marine Toolbox (`open_dataset`
perezoso sobre ARCO, recortando área, días y solo los niveles necesarios),
llevado a la malla SOME de 0.25° y a los 8 niveles de 0 a 100 m, y guardado en
la caché local `datos/copernicus/`. Solo se sirve ANÁLISIS (el último día es el
miércoles del boletín semanal menos 2 días); el pronóstico nunca.

Climatología: la oficial de GLORYS12 (GLOBAL_MULTIYEAR_PHY_001_030, DOI
10.48670/moi-00021), periodo 1993-2016 según `climatology_bounds`, declarado
siempre.

Interfaz pública (§2.1 del diseño): modo y acceso (sin red y baratos), estado
RF-21 (sin red, nunca lanzan), lecturas de la caché con llenado bajo demanda, y
las operaciones con red para el hilo de fondo, la herramienta
`preparar_copernicus` y las pruebas. Importar este paquete NO importa
`copernicusmarine` ni toca la red.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import numpy as np

from .. import configuracion as cfg
from . import acceso, almacen, climatologia, descarga, estado
from .acceso import paquete_instalado
from .almacen import ClimatologiaCMEMS, Lectura
from .constantes import (ESPERA_ALCANCE_S, MAX_FRAGMENTOS_BAJO_DEMANDA, NIVELES_SOME, VARIABLES, VARIABLES_2D,
                         VARIABLES_3D, ErrorCopernicus, ErrorDescargaEnCurso, ErrorFormato, ErrorSinAcceso,
                         ErrorSinCredenciales, ErrorSinDatos, espera_bajo_demanda_s, modo_configurado, sin_red,
                         sin_sincronizacion)
from .estado import (acceso_disponible, detalle as detalle_estado, estado_actual, fecha_corte, historial_estado, modo,
                     motivo_sin_acceso, ultimo_dia_analisis)
from .sincronizacion import (detener_sincronizacion, iniciar_sincronizacion, proxima_sincronizacion, sincronizando,
                             sincronizar, verificar)

log = logging.getLogger("some.copernicus")

__all__ = ["modo", "modo_configurado", "sin_red", "sin_sincronizacion", "paquete_instalado", "acceso_disponible",
           "motivo_sin_acceso", "fecha_corte", "ultimo_dia_analisis", "estado_actual", "historial_estado",
           "detalle_estado", "resumen", "firma_cache", "VARIABLES", "campo", "campos", "llenar", "llenar_cache",
           "climatologia_mensual", "origen_dias", "verificar", "sincronizar", "preparar_escenarios",
           "plan_escenarios", "iniciar_sincronizacion", "detener_sincronizacion", "sincronizando", "Lectura",
           "ClimatologiaCMEMS", "ErrorCopernicus", "ErrorSinAcceso", "ErrorSinCredenciales", "ErrorDescargaEnCurso",
           "ErrorFormato", "ErrorSinDatos", "ESCENARIOS"]

CONJUNTO_SINCRONIZADO = ("superficie (0 m) de thetao, so, uo, vo, zos y mlotst del último día de análisis; mlotst del "
                         "golfo de Guayaquil, 7 días")


def _nivel(variable: str, profundidad) -> int | None:
    """Nivel SOME de la caché (None para las variables 2D). ValueError si no es uno de los 8 niveles."""
    if variable not in VARIABLES:
        raise ValueError(f"Copernicus Marine no publica la variable «{variable}» en el SOME.")
    if variable in VARIABLES_2D:
        return None
    return climatologia.nivel_valido(variable, profundidad)


def _todo() -> tuple[np.ndarray, np.ndarray]:
    return np.arange(len(cfg.LAT)), np.arange(len(cfg.LON))


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------
def llenar_cache(variables, niveles_some, inicio: date, fin: date, ilat=None, ilon=None, *,
                 espera_s: float | None = None) -> dict:
    """Descarga lo que falte de `variables` × `niveles_some` en [inicio, fin] y las celdas pedidas.

    ValueError si el plan supera MAX_FRAGMENTOS_BAJO_DEMANDA; ErrorDescargaEnCurso si la descarga supera
    `espera_s` (por defecto SOME_COPERNICUS_ESPERA_S o 40 s) y sigue en segundo plano; ErrorSinAcceso u
    otro ErrorCopernicus si no se pudo (ya registrado en el estado).
    """
    if not acceso_disponible():
        raise ErrorSinAcceso(motivo_sin_acceso() or "sin acceso a Copernicus Marine")
    todo = _todo()
    ilat = todo[0] if ilat is None else ilat
    ilon = todo[1] if ilon is None else ilon
    tramos = descarga.planificar(list(variables), niveles_some, inicio, fin, ilat, ilon)
    if not tramos:
        return descarga.ejecutar([])
    total = sum(t.fragmentos for t in tramos)
    if total > MAX_FRAGMENTOS_BAJO_DEMANDA:
        raise ValueError(
            f"Esta consulta requiere descargar {total} fragmentos de Copernicus Marine (más de "
            f"{MAX_FRAGMENTOS_BAJO_DEMANDA}), demasiados datos para hacerlo en el momento; acote el periodo o el "
            "área, o ejecute «python -m backend.herramientas.preparar_copernicus» para llenar la caché con "
            "antelación.")
    if not acceso.red_alcanzable(ESPERA_ALCANCE_S):
        motivo = "sin conexión a internet (no responden el almacén ARCO ni la autenticación de Copernicus Marine)"
        estado.registrar_caida(motivo)
        err = ErrorSinAcceso(motivo)
        err.es_red = True
        raise err
    return descarga.ejecutar(tramos, espera_s=espera_bajo_demanda_s() if espera_s is None else espera_s)


llenar = llenar_cache      # nombre de la interfaz pública


def campos(variable: str, inicio: date, fin: date, profundidad: float | None = None, *,
           ilat: np.ndarray | None = None, ilon: np.ndarray | None = None,
           llenar: bool = True, espera_s: float | None = None) -> Lectura:
    """Lectura de la caché (con llenado bajo demanda si `llenar` y hay acceso).

    Los errores de acceso o de formato del llenado quedan registrados y se sirve lo que haya; se propagan
    `ValueError` (plan demasiado grande) y `ErrorDescargaEnCurso`.
    """
    nivel = _nivel(variable, profundidad)
    if llenar and acceso_disponible():
        try:
            llenar_cache([variable], None if nivel is None else [nivel], inicio, fin, ilat, ilon,
                         espera_s=espera_s)
        except (ValueError, ErrorDescargaEnCurso):
            raise
        except ErrorCopernicus as e:
            log.warning("Copernicus: no se pudo completar %s %s: %s", variable, nivel, e)
    return almacen.leer(variable, nivel, inicio, fin, ilat, ilon)


def campo(variable: str, fecha: date, profundidad: float | None = None, *, llenar: bool = True) -> np.ndarray:
    """(49, 69) float32 del día en el área completa. ErrorSinDatos si ninguna celda está obtenida."""
    lec = campos(variable, fecha, fecha, profundidad, llenar=llenar)
    if not (lec.tipo > 0).any():
        motivo = motivo_sin_acceso()
        raise ErrorSinDatos(f"El {cfg.fmt_fecha(fecha)} de {variable} no está en la caché local de Copernicus Marine"
                            + (f" y no se pudo obtener: {motivo}." if motivo else "."))
    return lec.valores[0]


def climatologia_mensual(variable: str, profundidad: float | None = None, *, llenar: bool = True,
                         espera_s: float | None = None) -> ClimatologiaCMEMS:
    return climatologia.climatologia_mensual(variable, profundidad, llenar=llenar, espera_s=espera_s)


def origen_dias(variable: str, profundidad: float | None, inicio: date, fin: date) -> dict:
    """{día: "definitivo" | "nrt" | "parcial" | "ausente"} sobre el área completa."""
    return almacen.origen_dias(variable, _nivel(variable, profundidad), inicio, fin)


# ---------------------------------------------------------------------------
# Escenarios de la demo (herramienta preparar_copernicus)
# ---------------------------------------------------------------------------
ESCENARIOS = ("superficie", "perfil", "comparacion", "capa_mezcla", "humo", "climatologia")
DESCRIPCION_ESCENARIOS = {
    "superficie": "6 variables a 0 m de los 30 días que recorre la barra de tiempo del mapa (área completa)",
    "perfil": "thetao y so en 8 niveles y mlotst en 85° O / 2° S, últimos 90 días (perfil, cortina, boletín ERFEN)",
    "comparacion": "thetao a 0 m en Niño 1+2, último año (comparación con NOAA OISST)",
    "capa_mezcla": "mlotst del golfo de Guayaquil, últimos 90 días (widget del panel y series)",
    "humo": "so a 10 m del golfo, último año, y thetao a 20 m del golfo, 30 días (prueba de humo)",
    "climatologia": "climatología GLORYS12 de las 6 variables a 0 m, so a 10 m y thetao a 20 m",
}


def _celda(lat: float, lon: float) -> tuple[np.ndarray, np.ndarray]:
    return (np.array([int(np.argmin(np.abs(cfg.LAT - lat)))]), np.array([int(np.argmin(np.abs(cfg.LON - lon)))]))


def _pares_climatologia(completa: bool) -> list[tuple[str, int | None]]:
    if completa:
        return climatologia.todos_los_pares()
    pares = [(v, 0 if v in VARIABLES_3D else None) for v in VARIABLES]
    return pares + [("so", 10), ("thetao", 20)]


def plan_escenarios(nombres=None, *, climatologia_completa: bool = False) -> dict:
    """{escenario: {"tramos": [Tramo], "clim": [(variable, nivel)], "fragmentos": int, "mib": float}} sin red."""
    from .sincronizacion import indices_recorte
    nombres = list(nombres or ESCENARIOS)
    c = ultimo_dia_analisis()
    todo = _todo()
    perfil = _celda(-2.0, -85.0)
    golfo = indices_recorte("golfo_guayaquil")
    nino = indices_recorte("nino12")
    plan: dict = {}
    for nombre in nombres:
        tramos, clim = [], []
        if nombre == "superficie":
            # Las 6 variables a 0 m de 30 días: con una fecha única, la barra de tiempo del mapa (y «Reproducir»)
            # recorre los 30 días anteriores con cualquier variable; lo que falte se descargaría fotograma a
            # fotograma (~15 s cada uno) o, sin acceso, alternaría fotogramas reales y simulados.
            tramos = descarga.planificar(["thetao", "so", "zos", "mlotst", "uo", "vo"], [0], c - timedelta(days=29), c,
                                         *todo)
        elif nombre == "perfil":
            tramos = (descarga.planificar(["thetao", "so"], list(NIVELES_SOME), c - timedelta(days=89), c, *perfil)
                      + descarga.planificar(["mlotst"], None, c - timedelta(days=89), c, *perfil))
        elif nombre == "comparacion":
            tramos = descarga.planificar(["thetao"], [0], c - timedelta(days=364), c, *nino)
        elif nombre == "capa_mezcla":
            tramos = descarga.planificar(["mlotst"], None, c - timedelta(days=89), c, *golfo)
        elif nombre == "humo":
            tramos = (descarga.planificar(["so"], [10], c - timedelta(days=364), c, *golfo)
                      + descarga.planificar(["thetao"], [20], c - timedelta(days=29), c, *golfo))
        elif nombre == "climatologia":
            clim = climatologia.pares_faltantes(_pares_climatologia(climatologia_completa))
        else:
            raise ValueError(f"Escenario desconocido «{nombre}». Disponibles: {', '.join(ESCENARIOS)}.")
        frag_clim = sum(climatologia.fragmentos_estimados(v, n) for v, n in clim)
        plan[nombre] = {"tramos": tramos, "clim": clim,
                        "fragmentos": sum(t.fragmentos for t in tramos) + frag_clim,
                        "mib": round(sum(t.mib for t in tramos) + frag_clim * 3.0, 1)}
    return plan


def preparar_escenarios(nombres: list[str] | None = None, *, climatologia_completa: bool = False,
                        progreso=None) -> dict:
    """Llena la caché con los escenarios de la demo (§3.13). `progreso(hechos, total, texto)` en fragmentos.

    Nunca pide credenciales; sin acceso lanza ErrorSinAcceso (o ErrorSinCredenciales) antes de descargar.
    Devuelve {"escenarios": {nombre: {...}}, "fragmentos", "segundos", "errores"}.
    """
    if not acceso_disponible():
        motivo = motivo_sin_acceso() or "sin acceso a Copernicus Marine"
        if "credenciales" in motivo and "no son válidas" not in motivo:
            raise ErrorSinCredenciales(motivo[0].upper() + motivo[1:])
        raise ErrorSinAcceso(motivo[0].upper() + motivo[1:])
    plan = plan_escenarios(nombres, climatologia_completa=climatologia_completa)
    total = sum(p["fragmentos"] for p in plan.values()) or 1
    hechos = 0
    salida = {"escenarios": {}, "fragmentos": 0, "segundos": 0.0, "errores": 0}
    abortado = ""
    for nombre, p in plan.items():
        r = {"fragmentos": 0, "segundos": 0.0, "celdas": 0, "dias_nrt": 0, "dias_definitivos": 0, "errores": 0,
             "detalle_errores": [], "climatologias": 0, "omitido": ""}
        if abortado:
            r["omitido"] = abortado
            salida["escenarios"][nombre] = r
            continue
        for t in p["tramos"]:
            if progreso:
                progreso(hechos, total, f"{nombre}: {t.describir()}")
            try:
                rt = descarga.ejecutar([t])
                for k in ("fragmentos", "celdas", "dias_nrt", "dias_definitivos"):
                    r[k] += int(rt.get(k, 0))
                r["segundos"] = round(r["segundos"] + float(rt.get("segundos", 0.0)), 1)
            except ErrorCopernicus as e:
                r["errores"] += 1
                r["detalle_errores"].append(f"{t.describir()}: {e}")
                if isinstance(e, ErrorSinAcceso):
                    abortado = str(e)
                    break
            hechos += t.fragmentos
        if p["clim"] and not abortado:
            def prog_clim(h, n, texto, base=hechos):
                if progreso:
                    progreso(min(total, base + h * 12), total, f"{nombre}: {texto}")
            t0 = time.perf_counter()
            rc = climatologia.preparar(p["clim"], progreso=prog_clim)
            r["segundos"] = round(r["segundos"] + time.perf_counter() - t0, 1)
            r["climatologias"] = rc["descargadas"]
            r["fragmentos"] += rc["fragmentos"]
            r["errores"] += rc["errores"]
            r["detalle_errores"] += rc["detalle_errores"]
            hechos += sum(climatologia.fragmentos_estimados(v, n) for v, n in p["clim"])
        salida["escenarios"][nombre] = r
        salida["fragmentos"] += r["fragmentos"]
        salida["segundos"] = round(salida["segundos"] + r["segundos"], 1)
        salida["errores"] += r["errores"]
    if progreso:
        progreso(total, total, "listo")
    return salida


# ---------------------------------------------------------------------------
# Resumen para administración y firma de la caché (sin red, nunca lanzan)
# ---------------------------------------------------------------------------
def _iso(x) -> str | None:
    return x.isoformat() if x else None


def resumen() -> dict:
    """Tarjeta «Copernicus Marine · caché y sincronización» (Administración › Sistema); sin red."""
    try:
        e = estado_actual()
        d = estado.datos()
        conteo = almacen.conteo_dias()
        clim = estado.bloque_climatologia()
        simulado_forzado = modo_configurado() == "simulado"
        ultima_sinc = dict(d["sincronizaciones"][-1]) if d.get("sincronizaciones") else None
        if ultima_sinc:
            ultima_sinc["detalle_errores"] = [estado.legible(x, 400) for x in ultima_sinc.get("detalle_errores") or []]
        w = None if simulado_forzado else estado.boletin_vigente()
        descargas = d.get("descargas") or {}
        return {
            "fuente": "copernicus_phy", "datos_reales": modo() == "real", "modo": modo(),
            "modo_configurado": modo_configurado(), "modo_sin_red": sin_red(),
            "estado": e["estado"], "mensaje_estado": e["mensaje"], "sincronizacion_activa": sincronizando(),
            "fecha_corte": _iso(fecha_corte()),
            "ultimo_dia_analisis": _iso(w - timedelta(days=2)) if w else None,
            "boletin_semanal": _iso(w),
            "paquete": estado.bloque_paquete(), "credenciales": estado.bloque_credenciales(),
            "servicio": estado.bloque_servicio(),
            "dias_en_cache": int(conteo["dias_con_dato"]), "dias_nrt": int(conteo["dias_nrt"]),
            "dias_definitivos": int(conteo["dias_definitivos"]),
            "conjuntos": almacen.conjuntos(),
            "climatologia": {"periodo": clim["periodo"], "en_cache": clim["en_cache"], "total": clim["total"],
                             "pares": clim["pares"]},
            "conjunto_sincronizado": CONJUNTO_SINCRONIZADO,
            "tamano_cache_bytes": almacen.tamano_bytes(),
            "segundos_descarga": float(descargas.get("segundos") or 0.0),
            "fragmentos_descargados": int(descargas.get("fragmentos") or 0),
            "ultima_sincronizacion": d.get("ultima_sincronizacion"),
            "resultado_ultima_sincronizacion": ultima_sinc,
            "ultima_verificacion": d["verificaciones"][-1] if d.get("verificaciones") else None,
            "proxima_sincronizacion": proxima_sincronizacion(),
            "errores_recientes": estado.errores_recientes(10),
        }
    except Exception as ex:  # noqa: BLE001 — la tarjeta nunca tumba la API
        log.exception("Copernicus: error al armar el resumen")
        return {"fuente": "copernicus_phy", "datos_reales": False, "modo": "simulado",
                "modo_configurado": modo_configurado(), "modo_sin_red": sin_red(), "estado": "sin_conexion",
                "mensaje_estado": f"No se pudo leer el estado de Copernicus Marine: {estado.legible(ex)}",
                "sincronizacion_activa": sincronizando(), "conjuntos": [], "errores_recientes": []}


def firma_cache() -> str:
    """Cambia con cada escritura de la caché, con el modo, el acceso y la fecha de corte (sin recorrer archivos)."""
    try:
        return f"{almacen.marca()}:{modo()}:{int(acceso_disponible())}:{fecha_corte()}"
    except Exception:  # noqa: BLE001
        return "copernicus:sin-firma"
