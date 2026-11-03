"""Plantillas de reporte y generación de PDF del SOME (RF-09) con reportlab.

Tres plantillas (sección 7.5): `boletin_erfen`, `informe_variable` y
`comparativo`. `generar(plantilla, parametros, usuario, trabajo_id, progreso)`
consulta el motor, dibuja las figuras con `graficos.py` y compone un PDF A4
vertical con carátula, encabezado y pie en cada página («SOME · <insignia de
procedencia>» y número de página), figuras con su crédito y una sección final
«Fuentes y métodos». Todo en español, fechas dd/mm/aaaa.

La procedencia del documento se calcula con los datos que realmente usó (la de
cada resultado, que el motor obtiene de sus Dataset): «Datos reales · NOAA
OISST», «Datos reales · Copernicus Marine» o «Datos reales: CHIRPS v3 (CHC,
UCSB)» si todo es de una fuente real, «Datos reales» si son varias (p. ej., el
boletín ERFEN con las tres fuentes reales: SST e índice Niño 1+2 de NOAA OISST,
perfil de Copernicus Marine y lluvia de CHIRPS v3), «Datos simulados para
demostración» si todo es simulado (modo simulado, o el simulador de respaldo de
una fuente real sin acceso ni caché) y «Datos mixtos: reales y simulados» cuando
se combinan. La carátula, el encabezado de cada página, el texto de método, los
avisos y cada figura lo reflejan; el periodo de referencia efectivo de la
climatología (p. ej. 1993-2016 en Copernicus Marine, o 1982-2010 en enero–agosto
para el 1981-2010 de NOAA OISST) se indica siempre que difiere del pedido. Las
fechas por defecto parten de la fecha de corte del sistema
(`motor.fecha_corte_sistema()`: el último día de NOAA OISST).

El trabajo simula progreso en cuatro etapas con `time.sleep(0.6)` para que la
demostración de la cola sea visible.
"""

from __future__ import annotations

import io
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib
from PIL import Image as ImagenPIL
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from . import almacen, catalogo
from . import configuracion as cfg
from . import graficos, motor

log = logging.getLogger("some.reportes")

COLOR_PRIMARIO = colors.HexColor("#0B4F6C")
COLOR_ACENTO = colors.HexColor("#F4A261")
COLOR_AVISO = colors.HexColor("#E9C46A")
COLOR_REAL = colors.HexColor("#D3EEEA")        # fondo de la insignia de datos reales
COLOR_REAL_TEXTO = colors.HexColor("#145A52")
COLOR_TEXTO = colors.HexColor("#132A3A")
COLOR_TEXTO_2 = colors.HexColor("#5A6B78")
COLOR_BORDE = colors.HexColor("#D9E1E8")
COLOR_FONDO = colors.HexColor("#F3F6F9")

ANCHO_FIGURA = 16.5 * cm
PUNTO_PERFIL = {"lat": -2.0, "lon": -85.0}   # 85° O, 2° S


# ---------------------------------------------------------------------------
# Plantillas (GET /api/reportes/plantillas)
# ---------------------------------------------------------------------------
def _periodo_ultimos(dias: int, fin: date | None = None) -> dict:
    fin = fin or motor.fecha_corte_sistema()
    return {"inicio": (fin - timedelta(days=dias - 1)).isoformat(), "fin": fin.isoformat()}


def _hace_un_anio(fecha: date) -> date:
    """El mismo día del año anterior (el 29 de febrero, el 28): la fecha de corte del sistema ya no es fija."""
    try:
        return fecha.replace(year=fecha.year - 1)
    except ValueError:
        return fecha.replace(year=fecha.year - 1, day=28)


def _opciones_fuentes() -> list[dict]:
    return [{"valor": c.id, "etiqueta": c.nombre} for c in catalogo.fuentes()]


def _opciones_variables() -> list[dict]:
    opciones = []
    for c in catalogo.fuentes():
        for v in c.variables:
            if not v.es_vector:
                opciones.append({"valor": v.id, "etiqueta": f"{v.nombre} ({c.nombre_corto or c.nombre})"})
    return opciones


def plantillas() -> list[dict]:
    """Descripción de las plantillas y sus parámetros (sección 7.5)."""
    corte = motor.fecha_corte_sistema()
    hace_un_anio = _hace_un_anio(corte)
    return [
        {
            "id": "boletin_erfen",
            "nombre": "Boletín quincenal para el CN-ERFEN",
            "descripcion": "Resumen ejecutivo, mapas de SST y anomalía, serie de la región Niño 1+2 de los "
                           "últimos 12 meses con el índice Niño 1+2 semanal, perfil térmico en 85° O / 2° S y "
                           "precipitación acumulada por provincia costera. Insumo del informe quincenal del Comité "
                           "Nacional para el Estudio Regional del Fenómeno El Niño.",
            "icono": "boletin",
            "parametros": [
                {"id": "periodo", "etiqueta": "Periodo del boletín", "tipo": "periodo",
                 "por_defecto": _periodo_ultimos(15)},
                {"id": "recorte", "etiqueta": "Región de análisis", "tipo": "recorte", "por_defecto": "nino12"},
                {"id": "incluir_precipitacion", "etiqueta": "Incluir precipitación por provincia",
                 "tipo": "booleano", "por_defecto": True},
                {"id": "autor", "etiqueta": "Autor o autora", "tipo": "texto", "por_defecto": ""},
            ],
        },
        {
            "id": "informe_variable",
            "nombre": "Informe de una variable",
            "descripcion": "Mapa promedio, mapa de anomalía, serie de tiempo con resumen en lenguaje sencillo, "
                           "climatología mensual y tabla de estadísticas de una variable en un recorte y periodo.",
            "icono": "informe",
            "parametros": [
                {"id": "fuente", "etiqueta": "Fuente", "tipo": "opcion", "opciones": _opciones_fuentes(),
                 "por_defecto": "noaa_oisst"},
                {"id": "variable", "etiqueta": "Variable", "tipo": "opcion", "opciones": _opciones_variables(),
                 "por_defecto": "sst"},
                {"id": "recorte", "etiqueta": "Recorte", "tipo": "recorte", "por_defecto": "nino12"},
                {"id": "periodo", "etiqueta": "Periodo", "tipo": "periodo", "por_defecto": _periodo_ultimos(30)},
                {"id": "profundidad", "etiqueta": "Profundidad (m, solo Copernicus)", "tipo": "opcion",
                 "opciones": [{"valor": str(int(z)), "etiqueta": f"{int(z)} m"} for z in cfg.PROFUNDIDADES],
                 "por_defecto": "0"},
                {"id": "periodo_referencia", "etiqueta": "Periodo de referencia", "tipo": "opcion",
                 "opciones": [{"valor": p, "etiqueta": p} for p in cfg.PERIODOS_REFERENCIA],
                 "por_defecto": cfg.PERIODO_REFERENCIA_POR_DEFECTO},
                {"id": "autor", "etiqueta": "Autor o autora", "tipo": "texto", "por_defecto": ""},
            ],
        },
        {
            "id": "comparativo",
            "nombre": "Comparativo de dos periodos",
            "descripcion": "Dos mapas promedio, series superpuestas (alineadas por día relativo al inicio de cada "
                           "periodo) y diferencias estadísticas de la misma variable en dos periodos distintos.",
            "icono": "comparar",
            "parametros": [
                {"id": "fuente", "etiqueta": "Fuente", "tipo": "opcion", "opciones": _opciones_fuentes(),
                 "por_defecto": "noaa_oisst"},
                {"id": "variable", "etiqueta": "Variable", "tipo": "opcion", "opciones": _opciones_variables(),
                 "por_defecto": "sst"},
                {"id": "recorte", "etiqueta": "Recorte", "tipo": "recorte", "por_defecto": "nino12"},
                {"id": "periodo_a", "etiqueta": "Periodo A", "tipo": "periodo",
                 "por_defecto": _periodo_ultimos(30, hace_un_anio)},
                {"id": "periodo_b", "etiqueta": "Periodo B", "tipo": "periodo", "por_defecto": _periodo_ultimos(30)},
                {"id": "autor", "etiqueta": "Autor o autora", "tipo": "texto", "por_defecto": ""},
            ],
        },
    ]


def plantilla(plantilla_id: str) -> dict | None:
    for p in plantillas():
        if p["id"] == plantilla_id:
            return p
    return None


# ---------------------------------------------------------------------------
# Tipografía (DejaVu Sans de matplotlib: cubre tildes, «», − y ¿¡)
# ---------------------------------------------------------------------------
_FUENTES = {"normal": "Helvetica", "negrita": "Helvetica-Bold", "cursiva": "Helvetica-Oblique"}
_FUENTES_LISTAS = False


def _registrar_fuentes() -> None:
    global _FUENTES_LISTAS
    if _FUENTES_LISTAS:
        return
    carpeta = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    archivos = {"SOME": "DejaVuSans.ttf", "SOME-Negrita": "DejaVuSans-Bold.ttf",
                "SOME-Cursiva": "DejaVuSans-Oblique.ttf", "SOME-NegritaCursiva": "DejaVuSans-BoldOblique.ttf"}
    try:
        if all((carpeta / a).exists() for a in archivos.values()):
            for nombre, archivo in archivos.items():
                pdfmetrics.registerFont(TTFont(nombre, str(carpeta / archivo)))
            pdfmetrics.registerFontFamily("SOME", normal="SOME", bold="SOME-Negrita",
                                          italic="SOME-Cursiva", boldItalic="SOME-NegritaCursiva")
            _FUENTES.update({"normal": "SOME", "negrita": "SOME-Negrita", "cursiva": "SOME-Cursiva"})
    except Exception:  # noqa: BLE001 - se usa Helvetica como respaldo
        pass
    _FUENTES_LISTAS = True


def _estilos() -> dict[str, ParagraphStyle]:
    _registrar_fuentes()
    n, b, c = _FUENTES["normal"], _FUENTES["negrita"], _FUENTES["cursiva"]
    return {
        "normal": ParagraphStyle("normal", fontName=n, fontSize=9.5, leading=13.5, textColor=COLOR_TEXTO,
                                 spaceAfter=5),
        "pequeno": ParagraphStyle("pequeno", fontName=n, fontSize=8, leading=10.5, textColor=COLOR_TEXTO_2),
        "pie_figura": ParagraphStyle("pie_figura", fontName=c, fontSize=7.5, leading=10, textColor=COLOR_TEXTO_2,
                                     spaceAfter=10),
        "vineta": ParagraphStyle("vineta", fontName=n, fontSize=9.5, leading=13.5, textColor=COLOR_TEXTO,
                                 leftIndent=12, bulletIndent=2, spaceAfter=3),
        "h1": ParagraphStyle("h1", fontName=b, fontSize=15, leading=19, textColor=COLOR_PRIMARIO,
                             spaceBefore=10, spaceAfter=8),
        "h2": ParagraphStyle("h2", fontName=b, fontSize=11.5, leading=15, textColor=COLOR_PRIMARIO,
                             spaceBefore=8, spaceAfter=5),
        "tabla": ParagraphStyle("tabla", fontName=n, fontSize=8, leading=10, textColor=COLOR_TEXTO),
        "tabla_cab": ParagraphStyle("tabla_cab", fontName=b, fontSize=8, leading=10, textColor=colors.white),
        "portada_logo": ParagraphStyle("portada_logo", fontName=b, fontSize=46, leading=52,
                                       textColor=COLOR_PRIMARIO, alignment=TA_LEFT),
        "portada_sub": ParagraphStyle("portada_sub", fontName=n, fontSize=13, leading=17, textColor=COLOR_TEXTO_2),
        "portada_inst": ParagraphStyle("portada_inst", fontName=b, fontSize=11, leading=15, textColor=COLOR_TEXTO,
                                       spaceBefore=18),
        "portada_titulo": ParagraphStyle("portada_titulo", fontName=b, fontSize=22, leading=27,
                                         textColor=COLOR_TEXTO, spaceBefore=36, spaceAfter=10),
        "portada_dato": ParagraphStyle("portada_dato", fontName=n, fontSize=11, leading=16, textColor=COLOR_TEXTO),
        "portada_aviso": ParagraphStyle("portada_aviso", fontName=b, fontSize=10, leading=14, textColor=COLOR_TEXTO,
                                        alignment=TA_CENTER, backColor=COLOR_AVISO, borderPadding=6),
        "portada_aviso_real": ParagraphStyle("portada_aviso_real", fontName=b, fontSize=10, leading=14,
                                             textColor=COLOR_REAL_TEXTO, alignment=TA_CENTER, backColor=COLOR_REAL,
                                             borderPadding=6),
    }


_TEXTO_SIMULADO = ("Todos los valores de este documento provienen del generador determinista del prototipo "
                   "y no corresponden a observaciones reales. Los créditos e identificadores de las fuentes "
                   "se incluyen tal como los exigirá el producto final (RF-12).")

# Nombre corto de la licencia para la tabla de «Fuentes y métodos» (el texto completo va debajo de la tabla)
_LICENCIA_CORTA = {"noaa_oisst": "Datos abiertos de NOAA", "copernicus_phy": "Licencia de uso de Copernicus Marine",
                   "chirps_v3": "CC BY 4.0"}

# Qué aporta cada fuente cuando sus datos son reales (carátula del PDF). La de Copernicus Marine no enumera
# variables («las de este documento»): una plantilla puede precisarla con `_Documento.descripciones_reales`
# (el boletín ERFEN solo usa la temperatura del agua). La de NOAA OISST nombra el índice Niño 1+2 (boletín
# ERFEN); el informe por variable y el comparativo, que no lo usan, la precisan sin él (`_descripcion_noaa`).
_DESCRIPCION_REAL = {
    "noaa_oisst": ("la temperatura superficial del mar de NOAA OISST v2.1 (NOAA NCEI, obtenida de NOAA PSL o, de "
                   "respaldo, de NOAA NCEI; llevada a "
                   "la malla de 0.25° del prototipo; los últimos ~15 días son preliminares) y el índice Niño 1+2 "
                   "calculado con ella en la región oficial (0–10° S, 90–80° O)"),
    "chirps_v3": ("la precipitación de CHIRPS v3 (Climate Hazards Center, UC Santa Barbara), obtenida del servidor "
                  "del CHC o de su réplica pública en dynamical.org (con los valores redondeados; «Fuentes y "
                  "métodos» indica cuál se usó) y llevada a la malla de 0.25° del prototipo; los días preliminares, si los hay, se "
                  "indican en el texto y se reemplazarán por la versión final"),
    "copernicus_phy": ("las variables de Copernicus Marine que se presentan aquí, del análisis diario "
                       "(GLOBAL_ANALYSISFORECAST_PHY_001_024; E.U. Copernicus Marine Service Information; nunca el "
                       "pronóstico), llevadas a la malla de 0.25° y a los niveles de 0 a 100 m del prototipo; sus "
                       "anomalías usan la climatología oficial GLORYS12, cuyo periodo efectivo indica cada método"),
}
# Parte real de una fuente «mixta» en el documento (unos resultados reales y otros del simulador de respaldo):
# no se le aplica la descripción de _DESCRIPCION_REAL, que la presentaría como real entera.
_DESCRIPCION_MIXTA = {
    "noaa_oisst": ("parte de los valores de NOAA OISST v2.1 de este documento: los de los días que estaban en la caché "
                   "local o se pudieron obtener de NOAA PSL o NOAA NCEI, llevados a la malla de 0.25° del prototipo; "
                   "cada figura indica al pie si sus datos son reales, simulados o mixtos"),
    "copernicus_phy": ("parte de los valores de Copernicus Marine de este documento: los del análisis diario "
                       "(GLOBAL_ANALYSISFORECAST_PHY_001_024; E.U. Copernicus Marine Service Information; nunca el "
                       "pronóstico) que estaban en la caché local o se pudieron descargar, llevados a la malla de "
                       "0.25° del prototipo; cada figura indica al pie si sus datos son reales, simulados o mixtos"),
}
# Qué es simulado cuando la fuente lo es. Copernicus Marine y NOAA OISST sin aviso: modo simulado configurado, o
# caché local vacía y sin acceso al servicio (el conector no deja aviso en esos casos).
_DESCRIPCION_SIMULADA = {
    "noaa_oisst": "la temperatura superficial del mar de NOAA OISST y el índice Niño 1+2 que deriva de ella",
    "copernicus_phy": ("las variables de Copernicus Marine de este documento (respondió el simulador del prototipo: "
                       "Copernicus Marine está en modo simulado, o su caché local está vacía y no hay acceso al "
                       "servicio)"),
    "chirps_v3": "la precipitación",
}
# Igual, cuando respondió el simulador de RESPALDO de una fuente real (sus resultados traen un aviso que dice por qué)
_DESCRIPCION_RESPALDO = {
    "noaa_oisst": ("los valores de NOAA OISST de este documento (sus servidores, NOAA PSL y NOAA NCEI, o la caché local "
                   "no los tenían disponibles: respondió el simulador de respaldo del prototipo, como se avisa en el "
                   "texto)"),
    "copernicus_phy": ("las variables de Copernicus Marine de este documento (su servicio o la caché local no las "
                       "tenían disponibles: respondió el simulador de respaldo del prototipo, como se avisa en el "
                       "texto)"),
}
# Parte simulada de una fuente «mixta» (siempre es el respaldo, que deja aviso)
_DESCRIPCION_SIMULADA_MIXTA = {
    "noaa_oisst": ("los demás valores de NOAA OISST (sus servidores o la caché local no los tenían disponibles: "
                   "respondió el simulador de respaldo del prototipo, como se avisa en el texto)"),
    "copernicus_phy": ("los demás valores de Copernicus Marine (su servicio o la caché local no los tenían "
                       "disponibles: respondió el simulador de respaldo del prototipo, como se avisa en el texto)"),
}


def _procedencia_info(info: dict) -> str:
    """«real», «simulado» o «mixto» de la información de fuente de un resultado."""
    p = info.get("procedencia")
    return p if p in ("real", "simulado", "mixto") else ("real" if info.get("datos_reales") else "simulado")


def _unir(partes: list[str]) -> str:
    partes = [p for p in partes if p]
    return ", ".join(partes[:-1]) + (" y " if len(partes) > 1 else "") + (partes[-1] if partes else "")


def _descripcion_real(info: dict, propias: dict[str, str]) -> str:
    """Qué es real de una fuente en la carátula; si la fuente es «mixta», solo la parte real."""
    nombre = info.get("nombre_corto") or info.get("nombre")
    if _procedencia_info(info) == "mixto":
        return _DESCRIPCION_MIXTA.get(info["id"], f"parte de los datos de {nombre} (cada figura indica al pie si "
                                                  "sus datos son reales, simulados o mixtos)")
    return propias.get(info["id"], f"los datos de {nombre}")


def _descripcion_simulada(info: dict) -> str:
    """Qué es simulado de una fuente en la carátula: la parte de respaldo si es «mixta»; si es toda simulada,
    según haya respondido el simulador de respaldo (con aviso en el texto) o el modo simulado."""
    nombre = info.get("nombre_corto") or info.get("nombre")
    if _procedencia_info(info) == "mixto":
        return _DESCRIPCION_SIMULADA_MIXTA.get(info["id"], f"el resto de los datos de {nombre}")
    if info.get("aviso") and info["id"] in _DESCRIPCION_RESPALDO:
        return _DESCRIPCION_RESPALDO[info["id"]]
    return _DESCRIPCION_SIMULADA.get(info["id"], f"los datos de {nombre}")


def _texto_procedencia(procedencia: str, infos: list[dict], descripciones: dict[str, str] | None = None) -> str:
    """Párrafo de la carátula que explica qué es real y qué es simulado en el documento (una oración por
    fuente real, para que se lea bien aunque cada descripción tenga sus propias comas). `descripciones`
    precisa, por fuente, lo que el documento usa de ella cuando es real (p. ej., el boletín ERFEN)."""
    if procedencia == "simulado":
        return _TEXTO_SIMULADO
    reales = [i for i in infos if _procedencia_info(i) != "simulado"]
    simuladas = [i for i in infos if _procedencia_info(i) != "real"]
    propias = {**_DESCRIPCION_REAL, **(descripciones or {})}
    descripciones_reales = [_descripcion_real(i, propias) for i in reales]
    # Con varias fuentes reales se numeran (cada descripción tiene sus propias comas y puntos y comas)
    oraciones_reales = (f"{descripciones_reales[0]}." if len(descripciones_reales) == 1 else
                        " ".join(f"{k}) {d}." for k, d in enumerate(descripciones_reales, start=1)))
    if procedencia == "real":
        return (f"Los valores de este documento son datos reales: {oraciones_reales} Cada figura indica su "
                "procedencia al pie (RF-12).")
    descripciones_simuladas = [_descripcion_simulada(i) for i in simuladas] \
        or ["los demás valores (p. ej., el índice Niño 1+2)"]
    # Igual que las reales: con varias se numeran (la de NOAA OISST ya lleva su propia «y»)
    if len(descripciones_simuladas) == 1:
        texto_simuladas = (f"{descripciones_simuladas[0]}; provienen del generador determinista del prototipo y no "
                           "corresponden a observaciones reales.")
    else:
        texto_simuladas = (" ".join(f"{k}) {d}." for k, d in enumerate(descripciones_simuladas, start=1))
                           + " Provienen del generador determinista del prototipo y no corresponden a "
                             "observaciones reales.")
    return (f"Este documento combina datos reales y simulados. Datos reales: {oraciones_reales} Datos simulados: "
            f"{texto_simuladas} Cada figura indica su procedencia al pie (RF-12).")


def _avisos_de(*resultados) -> list[str]:
    """Avisos distintos de los resultados del motor (respaldo simulado o datos parciales)."""
    salida: list[str] = []
    for r in resultados:
        for aviso in (r or {}).get("avisos") or []:
            texto = str(aviso or "").strip()
            if texto and texto not in salida:
                salida.append(texto)
    return salida


def _periodo_ref(r: dict, por_defecto: str = "") -> str:
    """Periodo de referencia de un resultado, con el efectivo si la climatología usada es de otro periodo."""
    return motor.texto_periodo_referencia(r.get("periodo_referencia") or por_defecto,
                                          r.get("periodo_referencia_efectivo"))


def _capitalizar(texto: str) -> str:
    texto = str(texto or "").strip().rstrip(".")
    return texto[:1].upper() + texto[1:]


def _fecha_replica() -> str:
    """Último día publicado por la réplica de dynamical.org según lo último consultado (sin red), en ISO;
    "" si no se sabe."""
    try:
        from .chirps import estado as estado_chirps
        for camino in estado_chirps.caminos()["caminos"]:
            if camino["id"] == "espejo":
                prod = camino.get("productos") or {}
                return str(prod.get("preliminar") or prod.get("final") or "")
    except Exception:  # noqa: BLE001 - la sección de fuentes no debe fallar por esto
        pass
    return ""


def _texto_origen_lluvia(resultados: list[dict]) -> str:
    """Frase sobre de dónde salieron los datos de CHIRPS de la sección de precipitación."""
    usa_replica = any(r.get("usa_replica") for r in resultados)
    origen_dias = next((r.get("origen_datos") for r in resultados if r.get("origen_datos")), "")
    clim = next((r.get("origen_climatologia") for r in resultados if r.get("origen_climatologia")), "")
    if usa_replica and "servidor del CHC" in origen_dias and "réplica" in origen_dias:
        via = ("obtenidos del servidor del CHC y de su réplica pública en dynamical.org, que redistribuye el "
               "producto del CHC con los valores redondeados para comprimir (CC BY 4.0)")
    elif "réplica" in origen_dias:
        via = ("obtenidos de la réplica pública de dynamical.org, que redistribuye el producto del CHC con los "
               "valores redondeados para comprimir (CC BY 4.0)")
    else:
        via = "descargados del servidor oficial del CHC"
    texto = f"{via} y llevados a la malla de 0.25° del prototipo"
    if origen_dias:
        texto += f" (origen de los días del mapa: {origen_dias})"
    texto += "."
    if clim:
        texto += f" El acumulado normal usa la {clim[0].lower()}{clim[1:]}."
    return texto


# ---------------------------------------------------------------------------
# Constructor de documentos
# ---------------------------------------------------------------------------
class _Documento:
    """Acumula los *flowables* del PDF y las fuentes y métodos empleados."""

    def __init__(self, titulo: str, subtitulo: str, periodo_texto: str, autor: str, usuario: dict,
                 generado: datetime | None = None):
        self.e = _estilos()
        self.titulo = titulo
        self.subtitulo = subtitulo
        self.periodo_texto = periodo_texto
        self.autor = autor or usuario.get("nombre") or usuario.get("usuario", "")
        self.usuario = usuario
        # `generado` permite retrodatar los trabajos de ejemplo del arranque para que la
        # portada, el pie de página y el nombre del archivo coincidan con la lista de trabajos
        self.generado = generado or datetime.now()
        self.elementos: list = []
        self.fuentes: dict[str, dict] = {}
        self._procedencias: list = []       # procedencias de resultados derivados (p. ej., índice Niño 1+2)
        self._avisos_emitidos: list[str] = []
        self._insignia_documento = cfg.INSIGNIA_SIMULADOS   # se recalcula en construir()
        self._titulo_pendiente: list = []   # títulos que se pegan al siguiente bloque (sin viudas)
        # Lo que el documento usa de cada fuente real, si es más preciso que _DESCRIPCION_REAL (carátula)
        self.descripciones_reales: dict[str, str] = {}

    # --- utilidades de texto ---------------------------------------------
    @staticmethod
    def _t(texto) -> str:
        return escape(str(texto if texto is not None else ""))

    # --- procedencia ------------------------------------------------------
    def registrar_procedencia(self, resultado: dict | None) -> None:
        """Anota la procedencia de un resultado incluido en el documento."""
        if resultado:
            self._procedencias.append({k: resultado.get(k) for k in ("procedencia", "insignia", "datos_simulados")})

    def procedencia(self) -> dict:
        """Procedencia del documento: combina las fuentes registradas (con la procedencia de los datos que
        realmente aportaron) y los resultados anotados."""
        partes = [{"procedencia": _procedencia_info(e["info"]), "insignia": e["info"].get("insignia")}
                  for e in self.fuentes.values()]
        return motor.combinar_procedencias(*partes, *self._procedencias)

    def avisar(self, *resultados) -> None:
        """Nota con los avisos de los resultados (p. ej., «Copernicus Marine no está disponible: {motivo}. Esta
        consulta usa datos SIMULADOS para demostración.»), una vez por aviso en todo el documento."""
        for aviso in _avisos_de(*resultados):
            if aviso not in self._avisos_emitidos:
                self._avisos_emitidos.append(aviso)
                self.nota(f"Aviso: {aviso}")

    def _portada(self) -> list:
        e = self.e
        procedencia = self.procedencia()
        estilo_aviso = e["portada_aviso_real"] if procedencia["procedencia"] == "real" else e["portada_aviso"]
        elementos = [
            Spacer(1, 3.2 * cm),
            Paragraph("SOME", e["portada_logo"]),
            Paragraph(cfg.NOMBRE_COMPLETO, e["portada_sub"]),
            Paragraph(self._t(cfg.INSTITUCION), e["portada_inst"]),
            Paragraph(self._t(self.titulo), e["portada_titulo"]),
        ]
        if self.subtitulo:
            elementos.append(Paragraph(self._t(self.subtitulo), e["portada_sub"]))
        elementos += [
            Spacer(1, 1.2 * cm),
            Paragraph(f"<b>Periodo:</b> {self._t(self.periodo_texto)}", e["portada_dato"]),
            Paragraph(f"<b>Autor o autora:</b> {self._t(self.autor)}", e["portada_dato"]),
            Paragraph(f"<b>Fecha de generación:</b> {cfg.fmt_fecha_hora(self.generado)}", e["portada_dato"]),
            Paragraph(f"<b>Versión del sistema:</b> {cfg.VERSION}", e["portada_dato"]),
            Spacer(1, 2.5 * cm),
            Paragraph(self._t(procedencia["insignia"].upper()), estilo_aviso),
            Spacer(1, 0.4 * cm),
            Paragraph(self._t(_texto_procedencia(procedencia["procedencia"],
                                                 [x["info"] for x in self.fuentes.values()],
                                                 descripciones=self.descripciones_reales)), e["pequeno"]),
            PageBreak(),
        ]
        return elementos

    # --- bloques ---------------------------------------------------------
    def _emitir(self, *bloques) -> None:
        """Agrega los bloques; si hay un título pendiente, lo mantiene en la misma página que ellos."""
        if self._titulo_pendiente:
            self.elementos.append(KeepTogether(self._titulo_pendiente + list(bloques)))
            self._titulo_pendiente = []
        else:
            self.elementos.extend(bloques)

    def seccion(self, texto: str) -> None:
        self._titulo_pendiente.append(Paragraph(self._t(texto), self.e["h1"]))

    def subseccion(self, texto: str) -> None:
        self._titulo_pendiente.append(Paragraph(self._t(texto), self.e["h2"]))

    def parrafo(self, texto: str) -> None:
        self._emitir(Paragraph(self._t(texto), self.e["normal"]))

    def frases(self, lista) -> None:
        for frase in lista or []:
            self._emitir(Paragraph(self._t(frase), self.e["vineta"], bulletText="•"))

    def nota(self, texto: str) -> None:
        self._emitir(Paragraph(self._t(texto), self.e["pequeno"]), Spacer(1, 4))

    def figura(self, png: bytes, pie: str, ancho: float = ANCHO_FIGURA) -> None:
        with ImagenPIL.open(io.BytesIO(png)) as im:
            ancho_px, alto_px = im.size
        alto = ancho * alto_px / max(ancho_px, 1)
        maximo = 20.5 * cm
        if alto > maximo:
            ancho, alto = ancho * maximo / alto, maximo
        imagen = Image(io.BytesIO(png), width=ancho, height=alto)
        bloque = [imagen, Paragraph(self._t(pie), self.e["pie_figura"])]
        if self._titulo_pendiente:
            bloque = self._titulo_pendiente + bloque
            self._titulo_pendiente = []
        self.elementos.append(KeepTogether(bloque))

    def tabla(self, cabecera: list[str], filas: list[list], anchos: list[float] | None = None) -> None:
        e = self.e
        datos = [[Paragraph(self._t(c), e["tabla_cab"]) for c in cabecera]]
        for fila in filas:
            datos.append([Paragraph(self._t(v), e["tabla"]) for v in fila])
        t = Table(datos, colWidths=anchos, repeatRows=1, hAlign="LEFT")
        estilo = [
            ("BACKGROUND", (0, 0), (-1, 0), COLOR_PRIMARIO),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, COLOR_BORDE),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        for i in range(1, len(datos)):
            if i % 2 == 0:
                estilo.append(("BACKGROUND", (0, i), (-1, i), COLOR_FONDO))
        t.setStyle(TableStyle(estilo))
        self._emitir(t, Spacer(1, 8))

    def registrar_fuente(self, info: dict, metodo: str | None = None) -> None:
        """Anota la fuente y el método usados para la sección «Fuentes y métodos».

        Si un resultado posterior de la misma fuente usó la réplica de dynamical.org (días o
        climatología), el crédito registrado pasa a ser el que la cita. En NOAA OISST y CHIRPS el crédito
        del documento se rehace con la suma de lo que usaron todas sus consultas (días de cada servidor de
        NOAA y sus climatologías; días del CHC y de la réplica), en lugar de quedarse con el de la primera."""
        entrada = self.fuentes.setdefault(info["id"], {"info": dict(info), "metodos": [], "origenes": []})
        if info.get("usa_replica") and not entrada["info"].get("usa_replica"):
            entrada["info"].update({k: info[k] for k in ("credito", "usa_replica", "credito_replica") if k in info})
        # Procedencia de la fuente en el documento: si un resultado usó datos reales y otro el simulador de
        # respaldo (o la climatología simulada), la fuente queda «mixta».
        previa, nueva = _procedencia_info(entrada["info"]), _procedencia_info(info)
        if nueva != previa:
            combinada = motor.combinar_procedencias(previa, nueva)
            entrada["info"].update({"procedencia": combinada["procedencia"], "insignia": combinada["insignia"],
                                    "datos_reales": combinada["procedencia"] != "simulado"})
            if info.get("referencias") and not entrada["info"].get("referencias"):
                entrada["info"]["referencias"] = info["referencias"]
        # Con un aviso (respaldo simulado o datos parciales), la carátula dice que el texto explica el porqué
        if info.get("aviso") and not entrada["info"].get("aviso"):
            entrada["info"]["aviso"] = info["aviso"]
        # Crédito más completo (p. ej., datos del análisis + climatología GLORYS12 de Copernicus Marine)
        credito_nuevo, credito_previo = str(info.get("credito") or ""), str(entrada["info"].get("credito") or "")
        if credito_nuevo and credito_previo and credito_nuevo != credito_previo \
                and credito_previo.rstrip(".") in credito_nuevo and not info.get("credito_replica"):
            entrada["info"]["credito"] = credito_nuevo
        acumulado = entrada.setdefault("acumulado", {"noaa": False, "psl": 0, "ncei": 0, "climatologias": [],
                                                      "chirps": False, "chc": 0, "replica": 0,
                                                      "climatologia_replica": False})
        if "dias_origen_psl" in info or "dias_origen_ncei" in info:
            acumulado["noaa"] = True
            acumulado["psl"] += int(info.get("dias_origen_psl") or 0)
            acumulado["ncei"] += int(info.get("dias_origen_ncei") or 0)
            for texto in info.get("creditos_climatologia") or []:
                if texto not in acumulado["climatologias"]:
                    acumulado["climatologias"].append(texto)
        if "dias_origen_chc" in info or "dias_origen_replica" in info or info.get("climatologia_replica"):
            acumulado["chirps"] = True
            acumulado["chc"] += int(info.get("dias_origen_chc") or 0)
            acumulado["replica"] += int(info.get("dias_origen_replica") or 0)
            acumulado["climatologia_replica"] |= bool(info.get("climatologia_replica"))
        if acumulado["noaa"]:
            from .noaa.constantes import credito_para_origen as credito_noaa
            entrada["info"]["credito"] = " ".join([credito_noaa(acumulado["psl"], acumulado["ncei"])]
                                                  + acumulado["climatologias"])
        elif acumulado["chirps"]:
            from .conectores.chirps import credito_para_origen as credito_chirps
            entrada["info"]["credito"], _ = credito_chirps(acumulado["chc"], acumulado["replica"],
                                                           acumulado["climatologia_replica"])
        for clave in ("origen_datos", "origen_climatologia"):
            texto = info.get(clave)
            if texto and texto not in entrada["origenes"]:
                entrada["origenes"].append(texto)
        if metodo and metodo not in entrada["metodos"]:
            entrada["metodos"].append(metodo)

    def fecha_corte_texto(self, fecha_corte) -> str:
        """dd/mm/aaaa de la fecha de corte de una fuente. En un trabajo de ejemplo retrodatado (generado antes
        de hoy) no puede ser posterior a su generación: se muestra como mucho el día anterior a ella, que es
        el último día de datos de su periodo."""
        try:
            corte = date.fromisoformat(str(fecha_corte)[:10])
        except (TypeError, ValueError):
            return cfg.fmt_fecha(fecha_corte)
        generado = self.generado.date()
        if generado < date.today() and corte >= generado:
            corte = generado - timedelta(days=1)
        return cfg.fmt_fecha(corte)

    def referencias_texto(self, referencias: str) -> str:
        """Referencias de una fuente. En un trabajo de ejemplo retrodatado, «Datos consultados por última vez
        el …» no puede ser posterior a su generación: se muestra como mucho el día de la generación."""
        generado = self.generado.date()
        if generado >= date.today():
            return referencias

        def acotar(m: re.Match) -> str:
            try:
                dia = datetime.strptime(m.group(2), "%d/%m/%Y").date()
            except ValueError:
                return m.group(0)
            return m.group(1) + cfg.fmt_fecha(min(dia, generado))

        return re.sub(r"(consultados por última vez el )(\d{2}/\d{2}/\d{4})", acotar, referencias)

    def pie_credito(self, info: dict) -> str:
        return (f"Fuente: {info['credito']} · Identificador {info['identificador']} · "
                f"Fecha de corte {self.fecha_corte_texto(info['fecha_corte'])}.")

    def seccion_fuentes(self) -> None:
        self.seccion("Fuentes y métodos")
        # Los dos párrafos van juntos con el título (KeepTogether de _emitir): así el aviso no se parte entre páginas
        self._emitir(Paragraph(self._t("Toda salida del SOME lleva el crédito y el identificador de la fuente "
                                       "(RF-12). Los métodos y periodos de referencia de cada cálculo se documentan "
                                       "a continuación (RNF-03)."), self.e["normal"]),
                     Paragraph(self._t(f"{cfg.AVISO_PROCESAMIENTO} La tabla nombra la licencia de cada fuente; su "
                                       "texto completo, con el enlace, va debajo de ella."), self.e["normal"]))
        filas = []
        replica = None
        textos = {"real": "datos reales", "simulado": "datos simulados", "mixto": "datos reales y simulados"}
        for entrada in self.fuentes.values():
            info = entrada["info"]
            procedencia = textos[_procedencia_info(info)]
            filas.append([f"{info['nombre']} ({procedencia})", info["identificador"], info["credito"],
                          _LICENCIA_CORTA.get(info["id"], info.get("licencia", "")),
                          self.fecha_corte_texto(info["fecha_corte"])])
            if info.get("usa_replica"):
                replica = entrada
        if replica is not None:
            # La réplica de dynamical.org redistribuye el producto del CHC con los valores redondeados: se cita
            # aparte, con su atribución literal (CC BY 4.0).
            filas.append(["Réplica de CHIRPS v3 en dynamical.org (datos reales, valores redondeados)",
                          "dynamical.org · ucsb-chc-chirps (Zarr/Icechunk)",
                          replica["info"].get("credito_replica") or "CHIRPS v3 procesado por dynamical.org",
                          "CC BY 4.0", self.fecha_corte_texto(_fecha_replica()) if _fecha_replica() else "—"])
        self.tabla(["Fuente", "Identificador", "Crédito", "Licencia", "Fecha de corte"],
                   filas, [3.0 * cm, 3.4 * cm, 5.0 * cm, 3.0 * cm, 2.4 * cm])
        # Texto completo de cada licencia a ancho completo: en la columna de 3 cm los enlaces se partían
        for entrada in self.fuentes.values():
            info = entrada["info"]
            if info.get("licencia"):
                self._emitir(Paragraph(f"<b>Licencia de {self._t(info['nombre_corto'])}:</b> "
                                       + self._t(info["licencia"]), self.e["pequeno"]), Spacer(1, 4))
        if replica is not None:
            self._emitir(Paragraph("<b>Licencia de la réplica de dynamical.org:</b> "
                                   + self._t("CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); valores "
                                             "redondeados por dynamical.org a 8 bits de mantisa para comprimir."),
                                   self.e["pequeno"]), Spacer(1, 4))
        for entrada in self.fuentes.values():
            info = entrada["info"]
            if info.get("datos_reales") and info.get("referencias"):
                self._emitir(Paragraph(f"<b>Referencias de {self._t(info['nombre_corto'])}:</b> "
                                       + self._t(self.referencias_texto(info["referencias"])), self.e["pequeno"]),
                             Spacer(1, 4))
        for entrada in self.fuentes.values():
            if entrada.get("origenes"):
                self._emitir(Paragraph(f"<b>Origen de los datos de {self._t(entrada['info']['nombre_corto'])}:</b> "
                                       + self._t("; ".join(_capitalizar(o) for o in entrada["origenes"])) + ".",
                                       self.e["pequeno"]), Spacer(1, 4))
        self.subseccion("Métodos empleados")
        for entrada in self.fuentes.values():
            for metodo in entrada["metodos"]:
                self._emitir(Paragraph(f"<b>{self._t(entrada['info']['nombre_corto'])}:</b> "
                                       f"{self._t(metodo)}", self.e["vineta"], bulletText="•"))
        self.nota(f"Reporte generado por {cfg.NOMBRE_SISTEMA} {cfg.VERSION} el "
                  f"{cfg.fmt_fecha_hora(self.generado)} para {self.usuario.get('nombre', '')} "
                  f"({self.usuario.get('departamento', '')}). {self.procedencia()['insignia']}.")

    # --- composición ------------------------------------------------------
    def _decorar(self, lienzo, doc, con_encabezado: bool) -> None:
        lienzo.saveState()
        ancho, alto = A4
        n, b = _FUENTES["normal"], _FUENTES["negrita"]
        if con_encabezado:
            izquierda = f"SOME · {self._insignia_documento}"
            lienzo.setFillColor(COLOR_PRIMARIO)
            lienzo.setFont(b, 8.5)
            lienzo.drawString(2 * cm, alto - 1.35 * cm, izquierda)
            # El título se acorta para no chocar con la insignia (la de datos mixtos es larga)
            disponible = ancho - 4 * cm - pdfmetrics.stringWidth(izquierda, b, 8.5) - 0.6 * cm
            titulo = self.titulo
            while titulo and pdfmetrics.stringWidth(titulo, n, 8.5) > disponible:
                titulo = titulo[:-2].rstrip() + "…" if len(titulo) > 2 else ""
            lienzo.setFillColor(COLOR_TEXTO_2)
            lienzo.setFont(n, 8.5)
            lienzo.drawRightString(ancho - 2 * cm, alto - 1.35 * cm, titulo)
            lienzo.setStrokeColor(COLOR_BORDE)
            lienzo.setLineWidth(0.6)
            lienzo.line(2 * cm, alto - 1.55 * cm, ancho - 2 * cm, alto - 1.55 * cm)
        lienzo.setStrokeColor(COLOR_BORDE)
        lienzo.line(2 * cm, 1.45 * cm, ancho - 2 * cm, 1.45 * cm)
        lienzo.setFillColor(COLOR_TEXTO_2)
        lienzo.setFont(n, 7.5)
        lienzo.drawString(2 * cm, 1.0 * cm, cfg.INSTITUCION)
        lienzo.drawRightString(ancho - 2 * cm, 1.0 * cm,
                               f"Generado el {cfg.fmt_fecha_hora(self.generado)} · Página {doc.page}")
        lienzo.restoreState()

    def construir(self, ruta: Path) -> int:
        """Escribe el PDF y devuelve el número de páginas."""
        doc = SimpleDocTemplate(str(ruta), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                                topMargin=2.1 * cm, bottomMargin=2.0 * cm,
                                title=self.titulo, author=self.autor, subject=self.subtitulo,
                                creator=f"{cfg.NOMBRE_SISTEMA} {cfg.VERSION}")
        if self._titulo_pendiente:
            self.elementos.extend(self._titulo_pendiente)
            self._titulo_pendiente = []
        # La carátula se compone al final, cuando ya se conoce la procedencia de todo el contenido
        self._insignia_documento = self.procedencia()["insignia"]
        self.elementos = self._portada() + self.elementos
        doc.build(self.elementos,
                  onFirstPage=lambda c, d: self._decorar(c, d, con_encabezado=False),
                  onLaterPages=lambda c, d: self._decorar(c, d, con_encabezado=True))
        return doc.page


# ---------------------------------------------------------------------------
# Lectura de parámetros
# ---------------------------------------------------------------------------
def _leer_periodo(parametros: dict, clave: str, por_defecto: dict, fuente_id: str | None = None) -> dict:
    """Lee un periodo de los parámetros y lo acota a la cobertura del sistema (desde el inicio de la demo
    hasta la fecha de corte del sistema, la de NOAA OISST) y, si se indica la fuente, a su fecha de corte
    (cada fuente real publica con su propio retraso). Si el periodo queda vacío responde 422 con un
    mensaje claro, en vez de crear un trabajo que fallará."""
    valor = parametros.get(clave) or por_defecto
    if isinstance(valor, str) and "," in valor:
        inicio, fin = [s.strip() for s in valor.split(",", 1)]
        valor = {"inicio": inicio, "fin": fin}
    if not isinstance(valor, dict) or "inicio" not in valor or "fin" not in valor:
        raise motor.ErrorConsulta(f"El parámetro «{clave}» debe ser un periodo {{inicio, fin}}.", 400)
    inicio, fin = motor._periodo(valor, clave)
    corte = motor.fecha_corte_sistema()
    nombre_fuente = "la demo"
    if fuente_id and catalogo.existe_fuente(fuente_id):
        conector = catalogo.conector(fuente_id)
        corte = min(corte, conector.fecha_corte)
        nombre_fuente = conector.nombre_corto or conector.nombre
    if inicio > corte:
        raise motor.ErrorConsulta(
            f"El periodo «{clave}» ({cfg.fmt_fecha(inicio)} – {cfg.fmt_fecha(fin)}) es posterior a la fecha de "
            f"corte de {nombre_fuente} ({cfg.fmt_fecha(corte)}); elija una fecha de inicio igual o anterior.", 422)
    if fin < cfg.FECHA_INICIO:
        raise motor.ErrorConsulta(
            f"El periodo «{clave}» ({cfg.fmt_fecha(inicio)} – {cfg.fmt_fecha(fin)}) es anterior al inicio de los "
            f"datos de la demo ({cfg.fmt_fecha(cfg.FECHA_INICIO)}).", 422)
    inicio = max(inicio, cfg.FECHA_INICIO)
    fin = min(fin, corte)
    return {"inicio": inicio.isoformat(), "fin": fin.isoformat()}


def _leer_recorte(parametros: dict, por_defecto: str = "nino12") -> dict:
    recorte_id = str(parametros.get("recorte") or por_defecto)
    r = catalogo.recorte(recorte_id)
    if r is None:
        raise motor.ErrorConsulta(f"Recorte desconocido: «{recorte_id}».", 404)
    return r


def _leer_booleano(valor, por_defecto: bool = True) -> bool:
    if valor is None or valor == "":
        return por_defecto
    if isinstance(valor, str):
        return valor.strip().lower() in ("1", "true", "si", "sí", "verdadero", "on")
    return bool(valor)


def _texto_periodo(p: dict) -> str:
    return f"{cfg.fmt_fecha(p['inicio'])} – {cfg.fmt_fecha(p['fin'])}"


def _dias(p: dict) -> int:
    return (date.fromisoformat(p["fin"]) - date.fromisoformat(p["inicio"])).days + 1


def _area(recorte: dict) -> dict:
    return {"tipo": "recorte", "recorte": recorte["id"]}


def _fila_estadisticas(etiqueta: str, est: dict, unidad: str, dec: int, anomalia: bool = False) -> list[str]:
    """Fila Media / Mínimo / Máximo / Celdas. Con `anomalia`, los valores llevan signo explícito y el
    signo menos tipográfico («+0.2 °C», «−0.4 °C»), como en el resto del documento."""
    fmt = cfg.fmt_anomalia if anomalia else cfg.fmt_valor
    return [etiqueta, fmt(est.get("media"), unidad, dec), fmt(est.get("minimo"), unidad, dec),
            fmt(est.get("maximo"), unidad, dec), cfg.fmt_num(est.get("celdas_validas"), 0)]


def _etapa(progreso, porcentaje: int, mensaje: str) -> None:
    progreso(porcentaje, mensaje)
    time.sleep(0.6)


def _descripcion_copernicus(var, profundidad: float | None, con_anomalias: bool) -> str:
    """Carátula de un documento de UNA variable de Copernicus Marine (informe por variable y comparativo):
    nombra la variable y el nivel que usa, no todas las del producto (ETQ-09)."""
    nombre = motor._nombre_para_frases(var)
    nivel = ""
    if var.tiene_profundidad:
        nivel = f" a {cfg.fmt_num(profundidad, 0)} m" if profundidad else " en superficie"
    return (f"{motor._articulo(nombre)} {nombre}{nivel}, del análisis diario de Copernicus Marine "
            "(GLOBAL_ANALYSISFORECAST_PHY_001_024; E.U. Copernicus Marine Service Information; nunca el pronóstico); "
            "los datos se llevaron a la malla de 0.25°" + (" y a los niveles de 0 a 100 m" if var.tiene_profundidad
                                                            else "") + " del prototipo"
            + ("; sus anomalías usan la climatología oficial GLORYS12, cuyo periodo efectivo indica cada método"
               if con_anomalias else ""))


def _descripcion_noaa(con_anomalias: bool, periodo_referencia: str | None = None) -> str:
    """Carátula de un documento de la SST de NOAA OISST sin el índice Niño 1+2 (informe por variable y
    comparativo). Solo la climatología 1991-2020 es de NOAA PSL: las demás las calcula el SOME y no pueden
    presentarse como productos de NOAA."""
    if not con_anomalias:
        anomalias = ""
    elif periodo_referencia == "1991-2020":
        anomalias = "; sus anomalías usan la climatología mensual 1991-2020 oficial de NOAA PSL"
    elif periodo_referencia:
        anomalias = (f"; sus anomalías usan una climatología mensual {periodo_referencia} calculada por el INOCAR "
                     "(SOME) con las medias mensuales de OISST de NOAA PSL")
    else:
        anomalias = ("; sus anomalías usan la climatología mensual del periodo de referencia que indica cada método "
                     "(la 1991-2020, oficial de NOAA PSL; las demás, calculadas por el INOCAR con OISST)")
    return ("la temperatura superficial del mar de NOAA OISST v2.1 (NOAA NCEI, obtenida de NOAA PSL o, de respaldo, "
            "de NOAA NCEI; los últimos ~15 días publicados son preliminares y el texto indica si este documento los "
            "incluye), llevada a la malla de 0.25° del prototipo" + anomalias)


def _nota_preliminares_noaa(*resultados) -> str:
    """«Incluye N días con datos preliminares de NOAA OISST (del … al …), que NOAA/NCEI reemplazará por la
    versión final…» con los días preliminares del resultado REAL que más tenga ("" si ninguno)."""
    reales = [x for x in resultados if x and x.get("procedencia") == "real"]
    r = max(reales, key=lambda x: int(x.get("dias_preliminares") or 0), default=None)
    n = int((r or {}).get("dias_preliminares") or 0)
    if not n:
        return ""
    tramo = ""
    if "/" in str(r.get("periodo_preliminar") or ""):
        desde, hasta = str(r["periodo_preliminar"]).split("/", 1)
        tramo = (f" ({cfg.fmt_fecha(hasta)})" if desde == hasta
                 else f" (del {cfg.fmt_fecha(desde)} al {cfg.fmt_fecha(hasta)})")
    return (f"Incluye {n} día{'s' if n != 1 else ''} con datos preliminares de NOAA OISST{tramo}, que NOAA/NCEI "
            "reemplazará por la versión final unas dos semanas después; sus valores pueden cambiar ligeramente.")


def _calificativo(*resultados) -> str:
    """«datos reales», «datos simulados para la demostración» o «datos reales y simulados» de los resultados
    (para calificar cada bloque del texto con su procedencia, como en el resumen del boletín)."""
    p = motor.combinar_procedencias(*resultados)["procedencia"]
    return {"real": "datos reales", "simulado": "datos simulados para la demostración"}.get(
        p, "datos reales y simulados")


def _superficie_copernicus(area: dict, periodo: dict, periodo_ref: str) -> dict | None:
    """Serie diaria de la temperatura del agua a 0 m de Copernicus Marine (promedio de área) para el resumen
    del boletín, o None si no es toda real (modo simulado, respaldo o climatología simulada) o la consulta
    falla (p. ej., 503 por una descarga en curso): es un complemento y nunca debe impedir el boletín."""
    try:
        r = motor.consulta_serie({"fuente": "copernicus_phy", "variable": "thetao", "area": area,
                                  "periodo": periodo, "profundidad": 0, "agregacion": "diaria",
                                  "periodo_referencia": periodo_ref})
    except Exception as e:  # noqa: BLE001 - el boletín sale sin esta frase
        log.warning("Boletín: sin la temperatura en superficie de Copernicus Marine (%s)", e)
        return None
    if r.get("procedencia") != "real" or not (r.get("resumen") or {}).get("n_datos"):
        return None
    return r


def _frase_superficie_copernicus(r: dict, recorte: dict, sst_simulada: bool) -> str:
    """Frase del resumen del boletín con la temperatura REAL del agua en superficie de Copernicus Marine en la
    región y el periodo, su anomalía y el periodo de referencia efectivo (GLORYS12 1993-2016). Si la SST de NOAA
    OISST del boletín es simulada (respaldo), lo advierte; si es real, explica por qué las dos pueden diferir."""
    validos = [(v, a) for v, a in zip(r["valores"], r["anomalias"]) if v is not None]
    anomalias = [a for _, a in validos if a is not None]
    anomalia = sum(anomalias) / len(anomalias) if anomalias else None
    n, total = len(validos), len(r["valores"])
    texto = (f"Según Copernicus Marine (datos reales del análisis diario), entre el "
             f"{cfg.fmt_fecha(r['periodo']['inicio'])} y el {cfg.fmt_fecha(r['periodo']['fin'])}"
             + (f", con datos de {n} de {total} días," if n < total else "")
             + f" la temperatura del agua en superficie en «{recorte['nombre']}» promedió "
             f"{cfg.fmt_valor(r['resumen']['media'], r['unidad'])}")
    if anomalia is not None:
        referencia = _periodo_ref(r, r.get("periodo_referencia") or "")
        if abs(anomalia) < 0.05:
            texto += f", un valor normal respecto de la climatología {referencia}"
        else:
            sentido = "por encima" if anomalia > 0 else "por debajo"
            texto += (f", es decir {cfg.fmt_valor(abs(anomalia), r['unidad'])} {sentido} de la climatología "
                      f"{referencia}")
    texto += "."
    if sst_simulada:
        texto += " Es un dato real; la SST de NOAA OISST de este boletín es simulada y puede diferir de él."
    else:
        texto += (" Es una estimación independiente de la de NOAA OISST (otro sistema de análisis y otra climatología "
                  "de referencia), por lo que sus valores y anomalías pueden diferir.")
    return texto


# ---------------------------------------------------------------------------
# Plantilla 1: boletín ERFEN
# ---------------------------------------------------------------------------
def _boletin_erfen(parametros: dict, usuario: dict, progreso, generado: datetime | None = None) -> _Documento:
    periodo = _leer_periodo(parametros, "periodo", _periodo_ultimos(15), "noaa_oisst")
    recorte = _leer_recorte(parametros, "nino12")
    incluir_precip = _leer_booleano(parametros.get("incluir_precipitacion"), True)
    autor = str(parametros.get("autor") or "").strip()
    fin = date.fromisoformat(periodo["fin"])
    area = _area(recorte)
    doc = _Documento("Boletín quincenal para el CN-ERFEN",
                     f"Condiciones oceanográficas en «{recorte['nombre']}» y precipitación costera",
                     _texto_periodo(periodo), autor, usuario, generado)

    _etapa(progreso, 8, "Consultando NOAA OISST…")
    mapa_sst = motor.consulta_mapa({"fuente": "noaa_oisst", "variable": "sst", "area": area, "periodo": periodo})
    periodo_12m = {"inicio": (fin - timedelta(days=364)).isoformat(), "fin": fin.isoformat()}
    serie_12m = motor.consulta_serie({"fuente": "noaa_oisst", "variable": "sst", "area": area,
                                      "periodo": periodo_12m, "agregacion": "diaria"})
    enso = motor.serie_indice_enso(fin - timedelta(days=364), fin)

    _etapa(progreso, 32, "Calculando anomalías…")
    mapa_anom = motor.consulta_mapa({"fuente": "noaa_oisst", "variable": "sst", "area": area, "periodo": periodo,
                                     "modo": "anomalia"})
    serie_periodo = motor.consulta_serie({"fuente": "noaa_oisst", "variable": "sst", "area": area,
                                          "periodo": periodo, "agregacion": "diaria"})
    progreso(38, "Consultando Copernicus Marine…")
    perfil = motor.consulta_perfil({"fuente": "copernicus_phy", "variable": "thetao",
                                    "lat": PUNTO_PERFIL["lat"], "lon": PUNTO_PERFIL["lon"], "fecha": periodo["fin"]})
    # El resumen añade la temperatura REAL del agua en superficie de Copernicus Marine en la misma región y
    # periodo: con NOAA OISST real es una segunda estimación independiente; si NOAA OISST respondió con su
    # simulador de respaldo, evita contradecir lo observado (p. ej., un evento cálido real). Sin dato real, se omite.
    superficie_cop = _superficie_copernicus(area, periodo, mapa_anom.get("periodo_referencia")
                                            or cfg.PERIODO_REFERENCIA_POR_DEFECTO)
    provincias: list[dict] = []
    mapa_lluvia = None
    errores_lluvia: list[str] = []         # motivos por los que CHIRPS no entregó datos (se citan en la sección 5)
    procedencia_lluvia: dict | None = None
    series_lluvia: list[dict] = []         # series por provincia (origen de los días y de la climatología)
    if incluir_precip:
        progreso(45, "Consultando CHIRPS v3…")
        try:
            mapa_lluvia = motor.consulta_mapa({"fuente": "chirps_v3", "variable": "precip",
                                               "area": {"tipo": "recorte", "recorte": "area_estudio"},
                                               "periodo": periodo, "incluir_acumulado": True})
            procedencia_lluvia = mapa_lluvia
        except motor.ErrorConsulta as e:
            mapa_lluvia = None
            errores_lluvia.append(e.detalle)
        for prov_id in catalogo.PROVINCIAS_COSTERAS:
            prov = catalogo.recorte(prov_id)
            try:
                s = motor.consulta_serie({"fuente": "chirps_v3", "variable": "precip",
                                          "area": {"tipo": "recorte", "recorte": prov_id},
                                          "periodo": periodo, "agregacion": "diaria"})
            except motor.ErrorConsulta as e:
                if e.detalle not in errores_lluvia:
                    errores_lluvia.append(e.detalle)
                continue
            procedencia_lluvia = procedencia_lluvia or s
            series_lluvia.append(s)
            a = motor.acumulado_y_normal(s["valores"], s["climatologia"])   # normal solo de los días con valor
            provincias.append({"id": prov_id, "nombre": prov["nombre"], "acumulado": a["acumulado"],
                               "normal": a["normal"] or 0.0, "anomalia": a["anomalia"] or 0.0, "dias": a["dias"],
                               "dias_sin_dato": a["dias_sin_dato"], "porcentaje": a["porcentaje"]})

    _etapa(progreso, 62, "Generando figuras…")
    credito_noaa = mapa_sst["fuente"]["credito"]
    fig_sst = graficos.figura_mapa(mapa_sst["lats"], mapa_sst["lons"], mapa_sst["valores"], mapa_sst["titulo"],
                                   mapa_sst["subtitulo"], mapa_sst["unidad"], mapa_sst["paleta"], mapa_sst["vmin"],
                                   mapa_sst["vmax"], credito_noaa, insignia=mapa_sst["insignia"])
    fig_anom = graficos.figura_mapa(mapa_anom["lats"], mapa_anom["lons"], mapa_anom["valores"], mapa_anom["titulo"],
                                    mapa_anom["subtitulo"], mapa_anom["unidad"], mapa_anom["paleta"],
                                    mapa_anom["vmin"], mapa_anom["vmax"], mapa_anom["fuente"]["credito"],
                                    insignia=mapa_anom["insignia"])
    fig_serie = graficos.figura_serie(serie_12m["fechas"], serie_12m["valores"],
                                      f"SST en «{recorte['nombre']}» · últimos 12 meses", "°C",
                                      serie_12m["fuente"]["credito"],
                                      serie_12m["climatologia"], serie_12m["anomalias"], etiqueta="SST diaria",
                                      subtitulo=f"{_texto_periodo(periodo_12m)} · referencia "
                                                f"{serie_12m['periodo_referencia']}", insignia=serie_12m["insignia"])
    fuente_enso = enso.get("fuente") or mapa_sst["fuente"]
    enso_real = enso.get("procedencia") == "real"
    if enso_real:
        # Índice real semanal (semanas de domingo a sábado etiquetadas con el miércoles, como NOAA CPC)
        semana = enso.get("semana_actual") or {}
        titulo_enso = "Índice Niño 1+2 semanal · NOAA OISST v2.1 (región 0–10° S, 90–80° O)"
        subtitulo_enso = (f"Semanas del {cfg.fmt_fecha(enso['fechas'][0])} al {cfg.fmt_fecha(enso['fechas'][-1])} "
                          f"(miércoles) · semana más reciente "
                          f"{cfg.fmt_fecha(semana.get('desde') or enso['fecha_actual'])} – "
                          f"{cfg.fmt_fecha(semana.get('hasta') or enso['fecha_actual'])}: "
                          f"{cfg.fmt_anomalia(enso['actual'], '°C', 2)}, {enso['fase']}")
    else:
        titulo_enso = "Índice Niño 1+2 simulado"
        subtitulo_enso = f"{_texto_periodo(periodo_12m)} · fase actual: {enso['fase']}"
    fig_enso = graficos.figura_indice_enso(enso["fechas"], enso["valores"], titulo_enso, subtitulo=subtitulo_enso,
                                           credito=fuente_enso["credito"], insignia=enso["insignia"],
                                           parciales=enso.get("parcial"), preliminares=enso.get("preliminar"))
    fig_perfil = graficos.figura_perfil(perfil["profundidades"], perfil["valores"],
                                        f"Perfil térmico en {cfg.fmt_coordenada(perfil['lat'], perfil['lon'])}",
                                        "°C", perfil["fuente"]["credito"], perfil["capa_mezcla_m"],
                                        subtitulo=f"{cfg.fmt_fecha(perfil['fecha'])} · Copernicus Marine",
                                        insignia=perfil["insignia"])
    fig_lluvia = None
    fig_barras = None
    if incluir_precip and mapa_lluvia is not None:
        # Suma de los días con dato, solo en las celdas que tienen TODOS esos días (no media × días:
        # eso extrapolaba lluvia a los días sin dato).
        acum = mapa_lluvia.get("acumulado") or {}
        n_dias = int(acum.get("dias_con_dato") or len(pd_rango(mapa_lluvia["periodo"])))
        acumulado = acum.get("valores") or [[None if v is None else v * n_dias for v in fila]
                                            for fila in mapa_lluvia["valores"]]
        finitos = [v for fila in acumulado for v in fila if v is not None]
        sin_dato = int(acum.get("dias_sin_dato") or 0)
        fig_lluvia = graficos.figura_mapa(mapa_lluvia["lats"], mapa_lluvia["lons"], acumulado,
                                          f"Precipitación acumulada en {n_dias} días · CHIRPS v3",
                                          f"{_texto_periodo(mapa_lluvia['periodo'])}"
                                          + (f" ({sin_dato} {'día' if sin_dato == 1 else 'días'} sin dato)"
                                             if sin_dato else "") + " · tierra continental y Galápagos",
                                          "mm", "YlGnBu", 0.0, max(finitos) if finitos else 1.0,
                                          mapa_lluvia["fuente"]["credito"], es_tierra=True,
                                          insignia=mapa_lluvia["insignia"])
    fuente_chirps_info = ((series_lluvia[0]["fuente"] if series_lluvia else None)
                          or (mapa_lluvia["fuente"] if mapa_lluvia is not None else None)
                          or motor._info_fuente(catalogo.conector("chirps_v3")))
    insignia_provincias = (procedencia_lluvia or {}).get("insignia")
    if provincias:
        fig_barras = graficos.figura_barras([p["nombre"] for p in provincias], [p["acumulado"] for p in provincias],
                                            "Precipitación acumulada por provincia costera · CHIRPS v3", "mm",
                                            fuente_chirps_info["credito"],
                                            subtitulo=f"{_texto_periodo(periodo)} · marcas: acumulado normal (1991-2020)",
                                            decimales=0, horizontal=True,
                                            valores_referencia=[p["normal"] for p in provincias],
                                            etiqueta_referencia="Normal", insignia=insignia_provincias)

    _etapa(progreso, 88, "Componiendo PDF…")
    # 1. Resumen ejecutivo: cada bloque dice de qué fuente sale y si sus datos son reales o simulados (con las
    # tres fuentes reales, todos «datos reales»; si una respondió con su simulador de respaldo, ese bloque lo dice)
    doc.seccion("1. Resumen ejecutivo")
    est = mapa_sst["estadisticas"]
    est_a = mapa_anom["estadisticas"]
    sentido = "por encima" if (est_a["media"] or 0) >= 0 else "por debajo"
    nombre_sst = mapa_sst["fuente"].get("nombre_corto") or mapa_sst["fuente"]["nombre"]
    # Del índice: la semana más reciente y, si lo hay, desde cuándo está más allá del umbral (sin categorías de
    # intensidad: el sistema solo calcula la anomalía y los umbrales de ±0.5 °C)
    frases_enso = [enso["texto"][0]] + [f for f in enso["texto"][1:] if f.startswith("El índice está")]
    doc.parrafo(f"Según {nombre_sst} ({_calificativo(mapa_sst, mapa_anom)}), entre el "
                f"{cfg.fmt_fecha(periodo['inicio'])} y el {cfg.fmt_fecha(periodo['fin'])}, la temperatura "
                f"superficial del mar en «{recorte['nombre']}» promedió {cfg.fmt_valor(est['media'], '°C')}, es decir "
                f"{cfg.fmt_valor(abs(est_a['media'] or 0), '°C')} {sentido} de la climatología "
                f"{_periodo_ref(mapa_anom)}. {' '.join(frases_enso)}")
    # La primera frase del resumen de la serie repite el promedio del párrafo anterior
    frases_sst = serie_periodo["resumen"]["texto"][1:]
    if frases_sst:
        doc.parrafo(f"Serie diaria de {nombre_sst} ({_calificativo(serie_periodo)}):")
        doc.frases(frases_sst)
    if superficie_cop is not None:
        doc.parrafo(_frase_superficie_copernicus(superficie_cop, recorte,
                                                 sst_simulada=serie_periodo.get("procedencia") != "real"))
        doc.avisar(superficie_cop)
    doc.parrafo(f"Perfil de Copernicus Marine ({_calificativo(perfil)}):")
    doc.frases(perfil["texto"][:2])
    if provincias:
        mas = max(provincias, key=lambda p: p["acumulado"])
        menos = min(provincias, key=lambda p: p["acumulado"])
        doc.parrafo(f"En precipitación (CHIRPS v3, {_calificativo(procedencia_lluvia)}), la provincia con mayor "
                    f"acumulado del periodo fue {mas['nombre']} "
                    f"({cfg.fmt_valor(mas['acumulado'], 'mm', 0)}, {cfg.fmt_anomalia(mas['anomalia'], 'mm', 0)} "
                    f"respecto a lo normal) y la de menor acumulado, {menos['nombre']} "
                    f"({cfg.fmt_valor(menos['acumulado'], 'mm', 0)}).")
    doc.registrar_fuente(mapa_sst["fuente"], mapa_sst["metodo"])
    doc.registrar_fuente(mapa_anom["fuente"], mapa_anom["metodo"])
    if superficie_cop is not None:
        doc.registrar_fuente(superficie_cop["fuente"], superficie_cop["metodo"])
    # Carátula: lo que el boletín usa de Copernicus Marine (no todas sus variables)
    usos_cop = ([f"la temperatura del agua en superficie promediada en «{recorte['nombre']}» (resumen ejecutivo)"]
                if superficie_cop is not None else [])
    usos_cop.append(f"el perfil vertical de temperatura con su capa de mezcla en "
                    f"{cfg.fmt_coordenada(perfil['lat'], perfil['lon'])}")
    doc.descripciones_reales["copernicus_phy"] = (
        f"{_unir(usos_cop)}, del análisis diario de Copernicus Marine (GLOBAL_ANALYSISFORECAST_PHY_001_024; "
        "E.U. Copernicus Marine Service Information; nunca el pronóstico), llevados a la malla de 0.25° y a los "
        "niveles de 0 a 100 m del prototipo"
        + ("; la anomalía en superficie usa la climatología oficial GLORYS12, cuyo periodo efectivo indica el texto"
           if superficie_cop is not None else ""))

    # 2. SST
    doc.seccion("2. Temperatura superficial del mar")
    doc.figura(fig_sst, f"Figura 1. {mapa_sst['titulo']}, {mapa_sst['subtitulo']}. {doc.pie_credito(mapa_sst['fuente'])}")
    doc.figura(fig_anom, f"Figura 2. {mapa_anom['titulo']}, {mapa_anom['subtitulo']}. {doc.pie_credito(mapa_anom['fuente'])}")
    doc.tabla(["Campo", "Media", "Mínimo", "Máximo", "Celdas válidas"],
              [_fila_estadisticas("SST promedio del periodo", est, "°C", 1),
               _fila_estadisticas("Anomalía de SST", est_a, "°C", 1, anomalia=True)],
              [5.5 * cm, 2.8 * cm, 2.8 * cm, 2.8 * cm, 2.6 * cm])
    nota_prelim = _nota_preliminares_noaa(mapa_sst, mapa_anom)
    if nota_prelim:
        doc.nota(nota_prelim)
    doc.avisar(mapa_sst, mapa_anom)

    # 3. Evolución
    doc.seccion("3. Evolución de los últimos 12 meses")
    doc.figura(fig_serie, f"Figura 3. Serie diaria de SST en «{recorte['nombre']}» con climatología mensual "
                          f"(línea discontinua) y anomalías. {doc.pie_credito(serie_12m['fuente'])}")
    doc.frases(serie_12m["resumen"]["texto"])
    doc.avisar(serie_12m)
    doc.registrar_fuente(serie_12m["fuente"], serie_12m["metodo"])
    doc.registrar_procedencia(enso)
    if enso_real:
        semana = enso.get("semana_actual") or {}
        estado_semana = [t for t, si in (("incompleta", semana.get("parcial")),
                                         ("con datos preliminares", semana.get("preliminar"))) if si]
        doc.figura(fig_enso, "Figura 4. Índice Niño 1+2 semanal (NOAA OISST v2.1, región 0–10° S, 90–80° O; anomalía "
                             "respecto de 1991-2020; semanas centradas en el miércoles). Franjas: El Niño (≥ +0.5 °C), "
                             "neutro, La Niña (≤ −0.5 °C); círculos: semanas incompletas o con días preliminares"
                             + (f" (la más reciente está {' y '.join(estado_semana)})" if estado_semana else "")
                             + f". {doc.pie_credito(fuente_enso)}")
        doc.frases(enso["texto"])
    else:
        doc.figura(fig_enso, f"Figura 4. Índice Niño 1+2 simulado (anomalía de SST). Franjas: El Niño (≥ +0.5 °C), "
                             f"neutro, La Niña (≤ −0.5 °C). {enso['metodo']} {doc.pie_credito(fuente_enso)}")
    doc.avisar(enso)
    # El método del índice va a «Fuentes y métodos» con NOAA OISST (si el índice salió del simulador de respaldo y
    # la SST es real, la fuente queda «mixta»: es lo que el documento usó)
    doc.registrar_fuente(fuente_enso, enso["metodo"])

    # 4. Perfil
    doc.seccion("4. Estructura térmica de la columna de agua")
    doc.figura(fig_perfil, f"Figura 5. Perfil vertical de temperatura (0 a 100 m) en "
                           f"{cfg.fmt_coordenada(perfil['lat'], perfil['lon'])} el {cfg.fmt_fecha(perfil['fecha'])}, "
                           f"con la capa de mezcla. {doc.pie_credito(perfil['fuente'])}", ancho=10.5 * cm)
    doc.frases(perfil["texto"])
    doc.tabla(["Profundidad (m)"] + [cfg.fmt_num(z, 0) for z in perfil["profundidades"]],
              [["Temperatura (°C)"] + [cfg.fmt_num(v, 1) for v in perfil["valores"]]],
              [3.2 * cm] + [1.6 * cm] * len(perfil["profundidades"]))
    if perfil.get("procedencia") == "real":
        doc.nota("El perfil proviene del análisis diario de Copernicus Marine (datos reales; E.U. Copernicus Marine "
                 "Service Information), llevado a la malla de 0.25° y a los niveles de 0 a 100 m del prototipo"
                 + (f"; origen: {perfil['origen_datos']}" if perfil.get("origen_datos") else "") + ".")
    doc.avisar(perfil)
    doc.registrar_fuente(perfil["fuente"], perfil["metodo"])

    # 5. Precipitación
    if incluir_precip:
        doc.seccion("5. Precipitación acumulada por provincia costera")
        if procedencia_lluvia is not None:
            real = procedencia_lluvia.get("procedencia") == "real"
            texto = ("La precipitación de esta sección son datos reales de CHIRPS v3 (Climate Hazards Center, "
                     "UC Santa Barbara; Funk et al., 2026), "
                     + _texto_origen_lluvia([x for x in [mapa_lluvia, *series_lluvia] if x]) if real else
                     "La precipitación de esta sección proviene de CHIRPS v3 (Climate Hazards Center, UC Santa "
                     "Barbara).")
            prelim = int(procedencia_lluvia.get("dias_preliminares") or 0)
            if prelim:
                texto += (f" Incluye {prelim} día{'s' if prelim != 1 else ''} con datos preliminares, que el CHC "
                          "reemplazará por la versión final; los valores de esos días pueden cambiar.")
            doc.parrafo(texto)
            doc.registrar_procedencia(procedencia_lluvia)
        if mapa_lluvia is not None and fig_lluvia is not None:
            doc.figura(fig_lluvia, f"Figura 6. Precipitación acumulada del periodo {_texto_periodo(mapa_lluvia['periodo'])} "
                                   f"(mm). {doc.pie_credito(mapa_lluvia['fuente'])}")
            acum = mapa_lluvia.get("acumulado") or {}
            excluidas = int(acum.get("celdas_excluidas") or 0)
            sin_dato = int(acum.get("dias_sin_dato") or 0)
            doc.registrar_fuente(
                mapa_lluvia["fuente"],
                "Acumulado = suma de la precipitación de los días con dato del periodo, solo en las celdas con "
                "dato en todos esos días"
                + (f" ({excluidas} {'celda' if excluidas == 1 else 'celdas'} sin dato algún día quedaron en blanco)"
                   if excluidas else "")
                + (f"; {sin_dato} {'día' if sin_dato == 1 else 'días'} sin dato no se suman" if sin_dato else "")
                + f". {mapa_lluvia['metodo']}")
        if provincias:
            doc.registrar_fuente(fuente_chirps_info, series_lluvia[0].get("metodo") if series_lluvia
                                 else (procedencia_lluvia or {}).get("metodo"))
            if fig_barras is not None:
                doc.figura(fig_barras, "Figura 7. Acumulado por provincia (barras) frente al acumulado normal "
                                       "1991-2020 (marcas). Recortes rectangulares aproximados. "
                                       f"{doc.pie_credito(fuente_chirps_info)}")
            # Con un acumulado normal de menos de 1 mm (p. ej., Galápagos en la estación seca) el «% de lo
            # normal» no tiene sentido (0 mm frente a 0 mm daría «215 %»): se muestra «—».
            doc.tabla(["Provincia", "Acumulado (mm)", "Normal (mm)", "Anomalía (mm)", "% de lo normal", "Días"],
                      [[p["nombre"], cfg.fmt_num(p["acumulado"], 0), cfg.fmt_num(p["normal"], 0),
                        cfg.fmt_anomalia(p["anomalia"], "mm", 0),
                        cfg.fmt_num(p["porcentaje"], 0) + " %"
                        if p["porcentaje"] is not None and (p["normal"] or 0) >= 1 else "—",
                        str(p["dias"])] for p in provincias],
                      [4.0 * cm, 2.6 * cm, 2.4 * cm, 2.6 * cm, 2.6 * cm, 1.4 * cm])
            fuente_chirps = catalogo.conector("chirps_v3")
            if fuente_chirps.fecha_corte < date.fromisoformat(periodo["fin"]):
                mensaje = (fuente_chirps.estado().mensaje or "").strip()
                if mensaje and not mensaje.endswith("."):
                    mensaje += "."
                doc.nota(f"Aviso: CHIRPS v3 tiene fecha de corte {cfg.fmt_fecha(fuente_chirps.fecha_corte)}; el "
                         f"acumulado cubre {provincias[0]['dias']} días del periodo solicitado. {mensaje}".strip())
        elif mapa_lluvia is not None:
            doc.parrafo("No fue posible calcular el acumulado por provincia costera en este periodo.")
        else:
            motivo = f" Motivo: {errores_lluvia[0]}" if errores_lluvia else ""
            doc.parrafo("No fue posible obtener la precipitación de CHIRPS v3 para este periodo, por lo que esta "
                        f"sección queda sin figuras ni tabla; el resto del boletín no se ve afectado.{motivo}")

    doc.seccion_fuentes()
    return doc


def pd_rango(periodo: dict) -> list[date]:
    inicio, fin = date.fromisoformat(periodo["inicio"]), date.fromisoformat(periodo["fin"])
    return [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)]


# ---------------------------------------------------------------------------
# Plantilla 2: informe de una variable
# ---------------------------------------------------------------------------
def _informe_variable(parametros: dict, usuario: dict, progreso, generado: datetime | None = None) -> _Documento:
    fuente_id = str(parametros.get("fuente") or "noaa_oisst")
    variable_id = str(parametros.get("variable") or "sst")
    recorte = _leer_recorte(parametros, "nino12")
    periodo = _leer_periodo(parametros, "periodo", _periodo_ultimos(30), fuente_id)
    periodo_ref = str(parametros.get("periodo_referencia") or cfg.PERIODO_REFERENCIA_POR_DEFECTO)
    profundidad = parametros.get("profundidad")
    profundidad = None if profundidad in (None, "") else float(profundidad)
    autor = str(parametros.get("autor") or "").strip()
    area = _area(recorte)
    conector, var = motor._fuente_y_variable(fuente_id, variable_id)
    z_texto = f" a {cfg.fmt_num(profundidad, 0)} m" if (profundidad and var.tiene_profundidad) else ""
    doc = _Documento(f"Informe de variable: {var.nombre}",
                     f"{conector.nombre} · «{recorte['nombre']}»{z_texto}", _texto_periodo(periodo), autor, usuario,
                     generado)
    base = {"fuente": fuente_id, "variable": variable_id, "area": area, "periodo_referencia": periodo_ref}
    if var.tiene_profundidad:
        base["profundidad"] = profundidad or 0
    if fuente_id == "copernicus_phy":
        doc.descripciones_reales[fuente_id] = _descripcion_copernicus(var, profundidad, con_anomalias=True)
    elif fuente_id == "noaa_oisst":
        doc.descripciones_reales[fuente_id] = _descripcion_noaa(con_anomalias=True, periodo_referencia=periodo_ref)

    _etapa(progreso, 8, f"Consultando {conector.nombre_corto or conector.nombre}…")
    mapa = motor.consulta_mapa(dict(base, periodo=periodo))
    agregacion = "diaria" if _dias(periodo) <= 400 else "semanal"
    serie = motor.consulta_serie(dict(base, periodo=periodo, agregacion=agregacion))

    _etapa(progreso, 32, "Calculando anomalías…")
    mapa_anom = motor.consulta_mapa(dict(base, periodo=periodo, modo="anomalia"))
    # La climatología de la sección 4 es la misma que usa la serie: si la serie salió del simulador de
    # respaldo, también la simulada (si no, el informe mezclaría la simulada 1991-2020 de las anomalías con
    # la real GLORYS12 1993-2016 y la carátula declararía real lo que no lo es).
    clim = motor.consulta_climatologia(dict(base, simulada=serie.get("procedencia") == "simulado"))

    _etapa(progreso, 62, "Generando figuras…")
    # Cada figura lleva el crédito de SU consulta: con CHIRPS, las que usan la climatología
    # (anomalía, serie, climatología) citan la réplica de dynamical.org si sus años salieron de ella.
    es_tierra = fuente_id == "chirps_v3"
    fig_mapa = graficos.figura_mapa(mapa["lats"], mapa["lons"], mapa["valores"], mapa["titulo"], mapa["subtitulo"],
                                    mapa["unidad"], mapa["paleta"], mapa["vmin"], mapa["vmax"], mapa["fuente"]["credito"],
                                    es_tierra=es_tierra, insignia=mapa["insignia"])
    fig_anom = graficos.figura_mapa(mapa_anom["lats"], mapa_anom["lons"], mapa_anom["valores"], mapa_anom["titulo"],
                                    mapa_anom["subtitulo"], mapa_anom["unidad"], mapa_anom["paleta"],
                                    mapa_anom["vmin"], mapa_anom["vmax"], mapa_anom["fuente"]["credito"],
                                    es_tierra=es_tierra, insignia=mapa_anom["insignia"])
    # Periodo de referencia: el pedido o, si la climatología de la fuente es de otro periodo (Copernicus
    # Marine: GLORYS12 1993-2016), el efectivo con el pedido entre paréntesis.
    referencia = _periodo_ref(serie, periodo_ref)
    referencia_clim = _periodo_ref(clim, periodo_ref)
    fig_serie = graficos.figura_serie(serie["fechas"], serie["valores"], f"{var.nombre} · «{recorte['nombre']}»",
                                      serie["unidad"], serie["fuente"]["credito"], serie["climatologia"], serie["anomalias"],
                                      etiqueta=var.nombre.split("(")[0].strip(),
                                      subtitulo=f"{_texto_periodo(serie['periodo'])} · agregación {agregacion} · "
                                                f"referencia {referencia}", decimales=var.decimales,
                                      insignia=serie["insignia"])
    fig_clim = graficos.figura_climatologia(clim["valores"], f"Climatología mensual {referencia_clim} · «{recorte['nombre']}»",
                                            clim["unidad"], clim["fuente"]["credito"], decimales=var.decimales,
                                            insignia=clim["insignia"])

    _etapa(progreso, 88, "Componiendo PDF…")
    res = serie["resumen"]
    doc.seccion("1. Resumen en lenguaje sencillo")
    doc.frases(res["texto"])
    if fuente_id == "noaa_oisst":
        nota_prelim = _nota_preliminares_noaa(serie, mapa)
        if nota_prelim:
            doc.nota(nota_prelim)
    doc.avisar(serie, mapa, mapa_anom, clim)
    doc.registrar_fuente(serie["fuente"], serie["metodo"])

    doc.seccion("2. Distribución espacial")
    doc.figura(fig_mapa, f"Figura 1. {mapa['titulo']}, {mapa['subtitulo']}. {doc.pie_credito(mapa['fuente'])}")
    doc.figura(fig_anom, f"Figura 2. {mapa_anom['titulo']}, {mapa_anom['subtitulo']}. {doc.pie_credito(mapa_anom['fuente'])}")
    doc.tabla(["Campo", "Media", "Mínimo", "Máximo", "Celdas válidas"],
              [_fila_estadisticas("Promedio del periodo", mapa["estadisticas"], mapa["unidad"], var.decimales),
               _fila_estadisticas("Anomalía", mapa_anom["estadisticas"], mapa["unidad"], var.decimales,
                                  anomalia=True)],
              [5.5 * cm, 2.8 * cm, 2.8 * cm, 2.8 * cm, 2.6 * cm])
    doc.registrar_fuente(mapa["fuente"], mapa["metodo"])
    doc.registrar_fuente(mapa_anom["fuente"], mapa_anom["metodo"])

    doc.seccion("3. Serie de tiempo")
    doc.figura(fig_serie, f"Figura 3. Serie {agregacion} del promedio de área con climatología mensual y anomalías. "
                          f"{doc.pie_credito(serie['fuente'])}")

    doc.seccion("4. Climatología mensual")
    doc.figura(fig_clim, f"Figura 4. Climatología mensual {referencia_clim} del promedio de área. "
                         f"{doc.pie_credito(clim['fuente'])}")
    efectivo_clim = clim.get("periodo_referencia_efectivo")
    if efectivo_clim and efectivo_clim != periodo_ref:
        # «la que se usa en este informe» solo si las anomalías de la serie usaron esa misma climatología
        alcance = ("la que se usa en este informe" if serie.get("periodo_referencia_efectivo") == efectivo_clim
                   else "la de esta figura")
        if fuente_id == "noaa_oisst":
            # NOAA OISST empieza en septiembre de 1981: 1981-2010 usa 1982-2010 en enero–agosto (calculada por el SOME)
            doc.nota(f"Se pidió el periodo de referencia {periodo_ref}; NOAA OISST empieza en septiembre de 1981, así "
                     f"que la climatología usa {efectivo_clim} y {periodo_ref} en los demás meses; es {alcance} "
                     "(ver «Fuentes y métodos»).")
        else:
            doc.nota(f"Se pidió el periodo de referencia {periodo_ref}; {conector.nombre_corto or conector.nombre} "
                     f"publica su climatología oficial para {efectivo_clim}, que es {alcance} (ver «Fuentes y "
                     "métodos»).")
    doc.tabla(["Mes"] + [m.capitalize() for m in clim["meses"]],
              [[clim["unidad"]] + [cfg.fmt_num(v, var.decimales) for v in clim["valores"]]],
              [1.6 * cm] + [1.25 * cm] * 12)
    doc.registrar_fuente(clim["fuente"], clim["metodo"])

    doc.seccion("5. Tabla de estadísticas")
    u, d = serie["unidad"], var.decimales
    filas = [
        ["Periodo analizado", _texto_periodo(serie["periodo"])],
        ["Datos en la serie", cfg.fmt_num(res.get("n_datos"), 0)],
        ["Media", cfg.fmt_valor(res.get("media"), u, d)],
        ["Desviación estándar", cfg.fmt_valor(res.get("desviacion"), u, d)],
        ["Mínimo", f"{cfg.fmt_valor(res['minimo']['valor'], u, d)} ({cfg.fmt_fecha(res['minimo']['fecha'])})" if res.get("minimo") else "—"],
        ["Máximo", f"{cfg.fmt_valor(res['maximo']['valor'], u, d)} ({cfg.fmt_fecha(res['maximo']['fecha'])})" if res.get("maximo") else "—"],
        ["Último dato", f"{cfg.fmt_valor(res['ultimo']['valor'], u, d)} ({cfg.fmt_fecha(res['ultimo']['fecha'])})" if res.get("ultimo") else "—"],
        ["Anomalía del último dato", cfg.fmt_anomalia(res["ultimo"].get("anomalia"), u, d) if res.get("ultimo") else "—"],
        ["Tendencia", f"{cfg.fmt_anomalia(res.get('tendencia_por_anio'), u, d)} por año" if res.get("tendencia_por_anio") is not None else "no se calcula (requiere al menos 2 años de datos)"],
        ["Periodo de referencia", referencia],
    ]
    doc.tabla(["Estadístico", "Valor"], filas, [6 * cm, 10.5 * cm])
    doc.seccion_fuentes()
    return doc


# ---------------------------------------------------------------------------
# Plantilla 3: comparativo de dos periodos
# ---------------------------------------------------------------------------
def _comparativo(parametros: dict, usuario: dict, progreso, generado: datetime | None = None) -> _Documento:
    fuente_id = str(parametros.get("fuente") or "noaa_oisst")
    variable_id = str(parametros.get("variable") or "sst")
    recorte = _leer_recorte(parametros, "nino12")
    corte = motor.fecha_corte_sistema()
    periodo_a = _leer_periodo(parametros, "periodo_a", _periodo_ultimos(30, _hace_un_anio(corte)), fuente_id)
    periodo_b = _leer_periodo(parametros, "periodo_b", _periodo_ultimos(30), fuente_id)
    autor = str(parametros.get("autor") or "").strip()
    area = _area(recorte)
    conector, var = motor._fuente_y_variable(fuente_id, variable_id)
    doc = _Documento(f"Comparativo de dos periodos: {var.nombre}",
                     f"{conector.nombre} · «{recorte['nombre']}»",
                     f"A: {_texto_periodo(periodo_a)} · B: {_texto_periodo(periodo_b)}", autor, usuario, generado)
    base = {"fuente": fuente_id, "variable": variable_id, "area": area}
    if var.tiene_profundidad:
        base["profundidad"] = 0
    if fuente_id == "copernicus_phy":
        # El comparativo no calcula anomalías: la carátula no menciona la climatología GLORYS12
        doc.descripciones_reales[fuente_id] = _descripcion_copernicus(var, 0, con_anomalias=False)
    elif fuente_id == "noaa_oisst":
        doc.descripciones_reales[fuente_id] = _descripcion_noaa(con_anomalias=False)

    _etapa(progreso, 8, f"Consultando {conector.nombre_corto or conector.nombre}…")
    mapa_a = motor.consulta_mapa(dict(base, periodo=periodo_a))
    mapa_b = motor.consulta_mapa(dict(base, periodo=periodo_b))

    _etapa(progreso, 32, "Calculando diferencias…")
    agregacion = "diaria" if max(_dias(periodo_a), _dias(periodo_b)) <= 400 else "semanal"
    comp = motor.consulta_comparar({"a": dict(base, periodo=periodo_a, etiqueta=f"Periodo A ({_texto_periodo(periodo_a)})"),
                                    "b": dict(base, periodo=periodo_b, etiqueta=f"Periodo B ({_texto_periodo(periodo_b)})"),
                                    "agregacion": agregacion})

    _etapa(progreso, 62, "Generando figuras…")
    es_tierra = fuente_id == "chirps_v3"
    vmin = min(mapa_a["vmin"], mapa_b["vmin"])
    vmax = max(mapa_a["vmax"], mapa_b["vmax"])
    fig_a = graficos.figura_mapa(mapa_a["lats"], mapa_a["lons"], mapa_a["valores"], f"Periodo A · {mapa_a['titulo']}",
                                 mapa_a["subtitulo"], mapa_a["unidad"], mapa_a["paleta"], vmin, vmax, mapa_a["fuente"]["credito"],
                                 es_tierra=es_tierra,
                                 insignia=mapa_a["insignia"])
    fig_b = graficos.figura_mapa(mapa_b["lats"], mapa_b["lons"], mapa_b["valores"], f"Periodo B · {mapa_b['titulo']}",
                                 mapa_b["subtitulo"], mapa_b["unidad"], mapa_b["paleta"], vmin, vmax, mapa_b["fuente"]["credito"],
                                 es_tierra=es_tierra,
                                 insignia=mapa_b["insignia"])
    fig_comp = graficos.figura_comparacion(comp["a"]["fechas"], comp["a"]["valores"], comp["a"]["etiqueta"],
                                           comp["b"]["fechas"], comp["b"]["valores"], comp["b"]["etiqueta"],
                                           f"{var.nombre} · «{recorte['nombre']}»", comp["unidad"], comp["fuentes"][0]["credito"],
                                           subtitulo=f"Agregación {agregacion}", alineacion=comp["alineacion"],
                                           decimales=var.decimales, insignia=comp["insignia"])

    _etapa(progreso, 88, "Componiendo PDF…")
    dif = comp["diferencia"]
    u, d = comp["unidad"], var.decimales
    doc.seccion("1. Resumen")
    doc.frases(comp["texto"])
    doc.avisar(comp, mapa_a, mapa_b)
    doc.parrafo(f"El periodo A ({_texto_periodo(periodo_a)}) promedió {cfg.fmt_valor(mapa_a['estadisticas']['media'], u, d)} "
                f"en «{recorte['nombre']}» y el periodo B ({_texto_periodo(periodo_b)}), "
                f"{cfg.fmt_valor(mapa_b['estadisticas']['media'], u, d)}.")
    if fuente_id == "noaa_oisst":
        nota_prelim = _nota_preliminares_noaa(mapa_a, mapa_b)
        if nota_prelim:
            doc.nota(nota_prelim)

    doc.seccion("2. Mapas promedio de cada periodo")
    doc.figura(fig_a, f"Figura 1. Promedio del periodo A, {mapa_a['subtitulo']}. {doc.pie_credito(mapa_a['fuente'])}")
    doc.figura(fig_b, f"Figura 2. Promedio del periodo B, {mapa_b['subtitulo']} (misma escala de color). "
                      f"{doc.pie_credito(mapa_b['fuente'])}")
    doc.tabla(["Periodo", "Media", "Mínimo", "Máximo", "Celdas válidas"],
              [_fila_estadisticas(f"A: {_texto_periodo(periodo_a)}", mapa_a["estadisticas"], u, d),
               _fila_estadisticas(f"B: {_texto_periodo(periodo_b)}", mapa_b["estadisticas"], u, d)],
              [5.5 * cm, 2.8 * cm, 2.8 * cm, 2.8 * cm, 2.6 * cm])
    doc.registrar_fuente(mapa_a["fuente"], mapa_a["metodo"])
    doc.registrar_fuente(mapa_b["fuente"])

    doc.seccion("3. Series superpuestas")
    doc.figura(fig_comp, f"Figura 3. Series del promedio de área de ambos periodos, alineadas por "
                         f"{'fecha' if comp['alineacion'] == 'fecha' else 'día relativo al inicio de cada periodo'}. "
                         f"{doc.pie_credito(comp['fuentes'][0])}")

    doc.seccion("4. Diferencias")
    doc.tabla(["Estadístico", "Valor"],
              [["Diferencia media (A − B)", cfg.fmt_anomalia(dif.get("media"), u, d)],
               ["Error cuadrático medio (RMSE)", cfg.fmt_valor(dif.get("rmse"), u, d)],
               ["Correlación de Pearson", cfg.fmt_num(dif.get("correlacion"), 2) if dif.get("correlacion") is not None else "no calculable"],
               ["Datos comparados", cfg.fmt_num(dif.get("n"), 0)],
               ["Alineación", "por fecha" if comp["alineacion"] == "fecha" else "por día relativo al inicio de cada periodo"]],
              [6 * cm, 10.5 * cm])
    doc.registrar_fuente(comp["fuentes"][0], comp["metodo"])
    doc.seccion_fuentes()
    return doc


# ---------------------------------------------------------------------------
# Punto de entrada para trabajos.py
# ---------------------------------------------------------------------------
_GENERADORES = {
    "boletin_erfen": _boletin_erfen,
    "informe_variable": _informe_variable,
    "comparativo": _comparativo,
}


def titulo_trabajo(plantilla_id: str, parametros: dict) -> str:
    """Título legible del trabajo en la cola."""
    p = plantilla(plantilla_id)
    nombre = p["nombre"] if p else plantilla_id
    detalles = []
    if parametros.get("variable") and parametros.get("fuente"):
        detalles.append(catalogo.nombre_variable(str(parametros["fuente"]), str(parametros["variable"])))
    r = catalogo.recorte(str(parametros.get("recorte") or "")) if parametros.get("recorte") else None
    if r:
        detalles.append(r["nombre"])
    return f"{nombre}" + (f" — {' · '.join(detalles)}" if detalles else "")


def validar_parametros(plantilla_id: str, parametros: dict) -> None:
    """Valida antes de encolar lo que haría fallar el trabajo: plantilla, periodos (acotados a la
    cobertura de la fuente), recorte y fuente/variable. Lanza `motor.ErrorConsulta` (400/404/422)."""
    p = parametros or {}
    if plantilla_id not in _GENERADORES:
        raise motor.ErrorConsulta(f"Plantilla desconocida: «{plantilla_id}».", 404)
    _leer_recorte(p, "nino12")
    if plantilla_id == "boletin_erfen":
        _leer_periodo(p, "periodo", _periodo_ultimos(15), "noaa_oisst")
        return
    fuente_id = str(p.get("fuente") or "noaa_oisst")
    variable_id = str(p.get("variable") or "sst")
    motor._fuente_y_variable(fuente_id, variable_id)
    if plantilla_id == "informe_variable":
        _leer_periodo(p, "periodo", _periodo_ultimos(30), fuente_id)
    else:
        corte = motor.fecha_corte_sistema()
        _leer_periodo(p, "periodo_a", _periodo_ultimos(30, _hace_un_anio(corte)), fuente_id)
        _leer_periodo(p, "periodo_b", _periodo_ultimos(30), fuente_id)


def generar(plantilla_id: str, parametros: dict, usuario: dict, trabajo_id: int, progreso,
            generado: datetime | None = None) -> dict:
    """Genera el PDF en `datos/reportes/<trabajo_id>.pdf` y devuelve los datos para el trabajo.

    `generado` (opcional) fija la fecha de generación que aparece en la portada, el pie de
    página y el nombre del archivo; los trabajos de ejemplo del arranque lo usan para que
    el PDF coincida con su fecha retrodatada en la lista de trabajos.
    """
    generador = _GENERADORES.get(plantilla_id)
    if generador is None:
        raise motor.ErrorConsulta(f"Plantilla desconocida: «{plantilla_id}».", 404)
    doc = generador(parametros or {}, usuario, progreso, generado)
    ruta = almacen.ruta_reportes() / f"{trabajo_id}.pdf"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    paginas = doc.construir(ruta)
    nombre = f"SOME_{plantilla_id}_{doc.generado.strftime('%Y%m%d')}.pdf"
    procedencia = doc.procedencia()
    return {
        "archivo": f"/api/reportes/{trabajo_id}/descargar",
        "ruta": str(ruta),
        "nombre_archivo": nombre,
        "mensaje": f"Reporte listo: {paginas} páginas, {ruta.stat().st_size // 1024} KB.",
        "procedencia": procedencia["procedencia"],
        "insignia": procedencia["insignia"],
    }
