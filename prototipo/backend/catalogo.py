"""Catálogo del SOME: fuentes, variables, recortes, periodos de referencia y estado.

Es la única puerta de entrada del resto del backend a los conectores:
`conector(id)`, `variable(fuente, variable)`, `recorte(id)`, `catalogo_json()`
(respuesta de `GET /api/catalogo`), `estado_fuentes()` (respuesta de
`GET /api/fuentes/estado`) y los conmutadores de habilitación (RF-20).

Procedencia: las tres fuentes son reales. CHIRPS v3 siempre; Copernicus Marine
y NOAA OISST cuando su conector está en modo real (caché local o acceso; si no,
usan su simulador y lo declaran: `datos_reales` e `insignia` son dinámicos).
`fuentes_reales()` e `insignias_cabecera()` se calculan en cada llamada, de modo
que la cabecera de la interfaz cambia sola cuando el usuario ejecuta
«copernicusmarine login» o se llena una caché.

Fecha de corte del sistema (`fecha_corte_sistema()`): la fecha por defecto de
consultas, panel, reportes y exportaciones. Con NOAA OISST real es el último día
de su caché (avanza sola cuando la sincronización guarda un día nuevo); en modo
simulado, la fecha de corte del simulador (`cfg.FECHA_CORTE_SIMULADA`).
"""

from __future__ import annotations

from datetime import date, timedelta

from . import configuracion as cfg
from .conectores import ConectorFuente, VariableFuente, crear_conectores, procedencia_de

# ---------------------------------------------------------------------------
# Conectores (instancias únicas)
# ---------------------------------------------------------------------------
CONECTORES: dict[str, ConectorFuente] = crear_conectores()

# ---------------------------------------------------------------------------
# Recortes (RF-17): bbox = [lon_min, lat_min, lon_max, lat_max]
# ---------------------------------------------------------------------------
RECORTES: list[dict] = [
    {"id": "area_estudio", "nombre": "Área de estudio", "tipo": "area",
     "bbox": [-95.0, -6.0, -78.0, 6.0],
     "descripcion": "Toda el área de estudio del SOME: 6° N a 6° S, 95° O a 78° O."},
    {"id": "nino12", "nombre": "Región Niño 1+2", "tipo": "region_nino",
     "bbox": [-90.0, -6.0, -80.0, 0.0],
     "descripcion": "Región Niño 1+2 recortada al área de estudio (la región real llega a 10° S)."},
    {"id": "nino3_parcial", "nombre": "Región Niño 3 (parte en el área)", "tipo": "region_nino",
     "bbox": [-95.0, -5.0, -90.0, 5.0],
     "descripcion": "Porción de la región Niño 3 contenida en el área de estudio."},
    {"id": "golfo_guayaquil", "nombre": "Golfo de Guayaquil", "tipo": "area",
     "bbox": [-81.5, -3.6, -79.8, -2.0],
     "descripcion": "Aguas del golfo de Guayaquil frente a Guayas, Santa Elena y El Oro."},
    {"id": "galapagos", "nombre": "Galápagos", "tipo": "provincia",
     "bbox": [-92.5, -1.8, -89.0, 1.0],
     "descripcion": "Archipiélago de Galápagos y aguas circundantes."},
    {"id": "esmeraldas", "nombre": "Esmeraldas", "tipo": "provincia",
     "bbox": [-80.2, 0.3, -78.4, 1.5],
     "descripcion": "Provincia de Esmeraldas (recorte rectangular aproximado)."},
    {"id": "manabi", "nombre": "Manabí", "tipo": "provincia",
     "bbox": [-81.0, -1.9, -79.6, 0.4],
     "descripcion": "Provincia de Manabí (recorte rectangular aproximado)."},
    {"id": "santa_elena", "nombre": "Santa Elena", "tipo": "provincia",
     "bbox": [-81.1, -2.7, -80.1, -1.9],
     "descripcion": "Provincia de Santa Elena (recorte rectangular aproximado)."},
    {"id": "guayas", "nombre": "Guayas", "tipo": "provincia",
     "bbox": [-80.7, -3.3, -79.3, -1.3],
     "descripcion": "Provincia del Guayas (recorte rectangular aproximado)."},
    {"id": "el_oro", "nombre": "El Oro", "tipo": "provincia",
     "bbox": [-80.5, -4.0, -79.3, -3.1],
     "descripcion": "Provincia de El Oro (recorte rectangular aproximado)."},
    {"id": "cuenca_guayas", "nombre": "Cuenca del río Guayas", "tipo": "cuenca",
     "bbox": [-80.5, -3.0, -78.8, 0.5],
     "descripcion": "Cuenca hidrográfica del río Guayas (recorte rectangular aproximado)."},
    {"id": "cuenca_esmeraldas", "nombre": "Cuenca del río Esmeraldas", "tipo": "cuenca",
     "bbox": [-79.9, -0.6, -78.3, 1.3],
     "descripcion": "Cuenca hidrográfica del río Esmeraldas (recorte rectangular aproximado)."},
]

# Tipos de recorte que corresponden a tierra (útiles para CHIRPS) y a mar
TIPOS_RECORTE_TIERRA = ("provincia", "cuenca")
NOMBRES_TIPO_RECORTE = {
    "area": "Área", "region_nino": "Región Niño", "provincia": "Provincia", "cuenca": "Cuenca",
}

# Provincias costeras usadas en el boletín y en el panel («precipitación por provincias»)
PROVINCIAS_COSTERAS = ["esmeraldas", "manabi", "santa_elena", "guayas", "el_oro", "galapagos"]

PERIODOS_REFERENCIA = list(cfg.PERIODOS_REFERENCIA)
PROFUNDIDADES = list(cfg.PROFUNDIDADES)


# ---------------------------------------------------------------------------
# Acceso
# ---------------------------------------------------------------------------
def fuentes() -> list[ConectorFuente]:
    return list(CONECTORES.values())


def conector(fuente_id: str) -> ConectorFuente:
    """Devuelve el conector o lanza KeyError con mensaje en español."""
    try:
        return CONECTORES[fuente_id]
    except KeyError:
        raise KeyError(f"Fuente desconocida: «{fuente_id}». "
                       f"Disponibles: {', '.join(CONECTORES)}.") from None


def existe_fuente(fuente_id: str) -> bool:
    return fuente_id in CONECTORES


def variable(fuente_id: str, variable_id: str) -> VariableFuente | None:
    if fuente_id not in CONECTORES:
        return None
    return CONECTORES[fuente_id].variable(variable_id)


def nombre_variable(fuente_id: str, variable_id: str) -> str:
    v = variable(fuente_id, variable_id)
    return v.nombre if v else variable_id


def nombre_fuente(fuente_id: str) -> str:
    return CONECTORES[fuente_id].nombre if fuente_id in CONECTORES else fuente_id


def recorte(recorte_id: str) -> dict | None:
    for r in RECORTES:
        if r["id"] == recorte_id:
            return r
    return None


def recortes_por_tipo(tipo: str) -> list[dict]:
    return [r for r in RECORTES if r["tipo"] == tipo]


def fuente_habilitada(fuente_id: str) -> bool:
    return fuente_id in CONECTORES and CONECTORES[fuente_id].habilitada


def variable_habilitada(fuente_id: str, variable_id: str) -> bool:
    v = variable(fuente_id, variable_id)
    return bool(v and v.habilitada and fuente_habilitada(fuente_id))


# ---------------------------------------------------------------------------
# Conmutadores (RF-20)
# ---------------------------------------------------------------------------
def establecer_fuente_habilitada(fuente_id: str, habilitada: bool) -> dict:
    c = conector(fuente_id)
    c.habilitada = bool(habilitada)
    return c.a_dict()


def establecer_variable_habilitada(fuente_id: str, variable_id: str, habilitada: bool) -> dict:
    c = conector(fuente_id)
    v = c.variable(variable_id)
    if v is None:
        raise KeyError(f"La fuente {c.nombre} no publica la variable «{variable_id}».")
    v.habilitada = bool(habilitada)
    return v.a_dict()


def aplicar_habilitaciones(fuentes_cfg: dict | None = None, variables_cfg: dict | None = None) -> None:
    """Aplica habilitaciones persistidas: {fuente: bool}, {"fuente/variable": bool}."""
    for fuente_id, valor in (fuentes_cfg or {}).items():
        if fuente_id in CONECTORES:
            CONECTORES[fuente_id].habilitada = bool(valor)
    for clave, valor in (variables_cfg or {}).items():
        if "/" in clave:
            fuente_id, variable_id = clave.split("/", 1)
            v = variable(fuente_id, variable_id)
            if v is not None:
                v.habilitada = bool(valor)


# ---------------------------------------------------------------------------
# Respuestas para la API
# ---------------------------------------------------------------------------
def fuentes_reales() -> list[str]:
    """Ids de las fuentes con datos reales en este momento (chirps_v3 y, en modo real, noaa_oisst y
    copernicus_phy)."""
    return [c.id for c in fuentes() if getattr(c, "datos_reales", False)]


def procedencia_catalogo() -> dict:
    """Procedencia del conjunto de fuentes: «mixto» mientras convivan reales y simuladas."""
    return procedencia_de(fuentes())


def copernicus_real() -> bool:
    """True si el conector de Copernicus Marine sirve datos reales (modo real: caché local o acceso)."""
    c = CONECTORES.get("copernicus_phy")
    try:
        return bool(c is not None and getattr(c, "datos_reales", False))
    except Exception:  # noqa: BLE001 - la procedencia nunca debe tumbar la API
        return False


def noaa_real() -> bool:
    """True si el conector de NOAA OISST sirve datos reales (modo real: caché local o acceso a NOAA)."""
    c = CONECTORES.get("noaa_oisst")
    try:
        return bool(c is not None and getattr(c, "datos_reales", False))
    except Exception:  # noqa: BLE001 - la procedencia nunca debe tumbar la API
        return False


def fecha_corte_sistema() -> date:
    """Fecha de corte del sistema: la fecha por defecto de consultas, panel, reportes y exportaciones.

    Con NOAA OISST real, su fecha de corte efectiva (el último día de su caché, que siempre tiene dato; con la
    caché vacía y acceso, el último publicado por PSL); en modo simulado, la del simulador
    (`cfg.FECHA_CORTE_SIMULADA`). Nunca lanza."""
    try:
        if noaa_real():
            return max(date.fromisoformat(CONECTORES["noaa_oisst"].estado().fecha_corte), cfg.FECHA_INICIO)
    except Exception as e:  # noqa: BLE001 - la fecha de corte nunca debe tumbar la API
        print(f"[SOME] No se pudo leer la fecha de corte de NOAA OISST: {e}")
    return cfg.FECHA_CORTE_SIMULADA


def insignias_cabecera(procedencia: dict | None = None) -> dict:
    """Textos de la insignia de la cabecera de la interfaz: {insignia_cabecera, insignia_cabecera_corta,
    titulo_cabecera}.

    - Las tres fuentes reales: «Datos reales: NOAA OISST, Copernicus Marine y CHIRPS v3».
    - Mezcla con NOAA OISST real y Copernicus Marine simulado: «Demostración: NOAA OISST y CHIRPS v3 con datos
      reales; Copernicus Marine simulado».
    - Mezcla con Copernicus Marine real y NOAA OISST simulado: «Demostración: CHIRPS v3 y Copernicus Marine con
      datos reales; NOAA OISST simulado».
    - Solo CHIRPS v3 real: «Demostración: datos simulados, salvo precipitación CHIRPS (real)».
    - Todas simuladas (no ocurre: CHIRPS v3 siempre es real): la insignia del catálogo."""
    procedencia = procedencia or procedencia_catalogo()
    if procedencia["procedencia"] == "real":
        return {"insignia_cabecera": cfg.INSIGNIA_CABECERA_REALES,
                "insignia_cabecera_corta": cfg.INSIGNIA_CABECERA_REALES_CORTA,
                "titulo_cabecera": cfg.TITULO_CABECERA_REALES}
    if procedencia["procedencia"] != "mixto":
        return {"insignia_cabecera": procedencia["insignia"], "insignia_cabecera_corta": procedencia["insignia"],
                "titulo_cabecera": procedencia["insignia"]}
    noaa, copernicus = noaa_real(), copernicus_real()
    if copernicus and not noaa:
        return {"insignia_cabecera": cfg.INSIGNIA_CABECERA_COPERNICUS,
                "insignia_cabecera_corta": cfg.INSIGNIA_CABECERA_COPERNICUS_CORTA,
                "titulo_cabecera": cfg.TITULO_CABECERA_COPERNICUS}
    if noaa and not copernicus:
        return {"insignia_cabecera": cfg.INSIGNIA_CABECERA_NOAA,
                "insignia_cabecera_corta": cfg.INSIGNIA_CABECERA_NOAA_CORTA,
                "titulo_cabecera": cfg.TITULO_CABECERA_NOAA}
    return {"insignia_cabecera": cfg.INSIGNIA_CABECERA, "insignia_cabecera_corta": cfg.INSIGNIA_CABECERA_CORTA,
            "titulo_cabecera": cfg.TITULO_CABECERA}


def catalogo_json() -> dict:
    """Cuerpo de `GET /api/catalogo`."""
    procedencia = procedencia_catalogo()
    return {
        "fuentes": [c.a_dict() for c in fuentes()],
        "recortes": [dict(r) for r in RECORTES],
        "periodos_referencia": PERIODOS_REFERENCIA,
        "periodo_referencia_por_defecto": cfg.PERIODO_REFERENCIA_POR_DEFECTO,
        "profundidades": PROFUNDIDADES,
        "fecha_inicio": cfg.FECHA_INICIO.isoformat(),
        "fecha_corte": fecha_corte_sistema().isoformat(),
        "bbox_area": list(cfg.BBOX_AREA),
        "resolucion_grados": cfg.RESOLUCION_GRADOS,
        # False con las tres fuentes reales; True si alguna cae a su simulador (sin acceso ni caché o modo forzado)
        "datos_simulados": procedencia["datos_simulados"],
        "procedencia": procedencia["procedencia"],
        "insignia": procedencia["insignia"],
        **insignias_cabecera(procedencia),
        "fuentes_reales": fuentes_reales(),
        "version": cfg.VERSION,
    }


# Patrón determinista del historial de 14 días de las fuentes simuladas (índice 0 = hoy, 13 = hace 13 días).
# CHIRPS v3 no figura: su historial sale de las verificaciones reales que persiste su conector. Los de
# Copernicus Marine y NOAA OISST también, porque sus conectores exponen historial_estado(); este patrón solo
# se usaría si no.
_HISTORIAL_SIMULADO = {
    "noaa_oisst": {},
    "copernicus_phy": {5: "degradada"},
}


def _historial_sin_registro(dias: int, hoy: date) -> list[dict]:
    return [{"fecha": (hoy - timedelta(days=atras)).isoformat(), "estado": "sin_registro"}
            for atras in range(dias - 1, -1, -1)]


def historial_estado(fuente_id: str, dias: int = 14, hoy: date | None = None) -> list[dict]:
    """Historial de estado de los últimos `dias` días (del más antiguo al más reciente).

    Si el conector implementa `historial_estado(dias)` (conectores reales), se usa su
    registro de verificaciones; los días sin verificación llegan como «sin_registro».
    Para las fuentes simuladas se devuelve un patrón determinista.
    """
    hoy = hoy or date.today()
    c = CONECTORES.get(fuente_id)
    if c is not None and callable(getattr(c, "historial_estado", None)):
        try:
            return list(c.historial_estado(dias))
        except Exception as e:  # noqa: BLE001 - el estado nunca debe tumbar la API
            print(f"[SOME] No se pudo leer el historial de estado de {fuente_id}: {e}")
            return _historial_sin_registro(dias, hoy)
    if c is not None and getattr(c, "datos_reales", False):
        return _historial_sin_registro(dias, hoy)
    excepciones = _HISTORIAL_SIMULADO.get(fuente_id, {})
    historial = []
    for atras in range(dias - 1, -1, -1):
        historial.append({
            "fecha": (hoy - timedelta(days=atras)).isoformat(),
            "estado": excepciones.get(atras, "operativa"),
        })
    return historial


def _estado_seguro(c: ConectorFuente) -> dict:
    """`c.estado().a_dict()`; si el conector fallara, «sin_conexion» con el motivo (el estado nunca debe
    tumbar la API ni la pantalla «Estado de fuentes»)."""
    try:
        return c.estado().a_dict()
    except Exception as ex:  # noqa: BLE001
        print(f"[SOME] No se pudo leer el estado de {c.id}: {ex}")
        return {"fuente": c.id, "nombre": c.nombre, "estado": "sin_conexion",
                "fecha_corte": cfg.FECHA_INICIO.isoformat(), "latencia_ms": 0,
                "ultimo_chequeo": "", "mensaje": f"No se pudo leer el estado de la fuente: {ex}"}


def estado_fuentes() -> list[dict]:
    """Cuerpo de `GET /api/fuentes/estado`.

    Las fuentes reales añaden su detalle (`detalle_estado()`): CHIRPS v3, los caminos (servidor del CHC y
    réplica dynamical.org) y la caché; Copernicus Marine, el bloque `copernicus` (modo, acceso, paquete,
    credenciales, servicio ARCO, último día de análisis, climatología y caché); NOAA OISST, el bloque `noaa`
    (modo, acceso, servidores PSL y NCEI, publicación, climatologías y caché con días finales, preliminares y
    faltantes)."""
    salida = []
    for c in fuentes():
        e = _estado_seguro(c)
        e["habilitada"] = c.habilitada
        e["identificador"] = c.identificador
        e["credito"] = c.credito
        e["datos_reales"] = bool(getattr(c, "datos_reales", False))
        e["insignia"] = c.insignia
        e["historial"] = historial_estado(c.id)
        detalle = getattr(c, "detalle_estado", None)
        if callable(detalle):
            try:
                e.update(detalle())
            except Exception as ex:  # noqa: BLE001 - el estado nunca debe tumbar la API
                print(f"[SOME] No se pudo leer el detalle de estado de {c.id}: {ex}")
        salida.append(e)
    return salida


def hay_fuentes_degradadas() -> bool:
    return any(_estado_seguro(c)["estado"] != "operativa" for c in fuentes())
