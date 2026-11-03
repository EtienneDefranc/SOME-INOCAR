"""Figuras del SOME con matplotlib (sin cartopy) para la interfaz y los reportes.

- `mapa_png`: imagen PNG ligera con fondo transparente, sin ejes e
  interpolación «nearest», para superponerla en Leaflet.
- `paleta_rgb`: 16 pasos [r, g, b] de una paleta para dibujar la leyenda en la UI.
- Figuras completas (título, ejes, barra de color, crédito e insignia de
  procedencia: simulados, reales o mixtos, parámetro `insignia`) para la
  exportación PNG y los reportes PDF: mapa, serie, comparación, perfil,
  cortina, climatología, barras e índice Niño 1+2.

Todas las figuras devuelven los bytes del PNG; `a_data_uri` los convierte en
`data:image/png;base64,...`.
"""

from __future__ import annotations

import base64
import io
import textwrap
from datetime import date, datetime

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import colormaps  # noqa: E402
from matplotlib import dates as mdates  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402
from PIL import Image  # noqa: E402
from scipy import ndimage  # noqa: E402

from . import configuracion as cfg  # noqa: E402
from . import datos_simulados as sim  # noqa: E402

# Colores del sistema visual (coinciden con css/app.css)
COLOR_PRIMARIO = "#0B4F6C"
COLOR_ACENTO = "#F4A261"
COLOR_EXITO = "#2A9D8F"
COLOR_AVISO = "#E9C46A"
COLOR_PELIGRO = "#C8553D"
COLOR_TEXTO = "#132A3A"
COLOR_TEXTO_2 = "#5A6B78"
COLOR_TIERRA = "#E8E4DA"
COLOR_MAR = "#DCEBF5"
COLOR_COSTA = "#4A4A4A"

PALETAS_PERMITIDAS = {"viridis", "cividis", "YlGnBu", "BrBG", "PuOr_r", "RdBu_r",
                      "plasma", "magma", "inferno", "Blues", "Greens", "coolwarm",
                      "termico", "salino"}

# Paletas oceanográficas propias, inspiradas en «thermal» y «haline» de cmocean
# (perceptualmente uniformes y legibles con daltonismo): la térmica va de azul
# profundo (frío) a amarillo (cálido), como esperan los oceanógrafos; la salina,
# de azul violáceo (menos salino) a amarillo claro (más salino).
_PALETAS_PROPIAS = {
    "termico": ["#032333", "#0f3878", "#4a2f90", "#7c3391", "#aa3e7d", "#d05063",
                "#ed6d3c", "#f8951c", "#f6c23c", "#e9f95b"],
    "salino": ["#2a186c", "#213f90", "#0f667d", "#1f8d7e", "#45b07c", "#84cf69",
               "#c9e95c", "#fdee9e"],
}
for _nombre, _colores in _PALETAS_PROPIAS.items():
    if _nombre not in colormaps:
        colormaps.register(LinearSegmentedColormap.from_list(_nombre, _colores, N=256))

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.titlesize": 11,
    "axes.titleweight": "semibold",
    "axes.labelsize": 9,
    "axes.edgecolor": "#9AA7B2",
    "axes.labelcolor": COLOR_TEXTO,
    "xtick.color": COLOR_TEXTO_2,
    "ytick.color": COLOR_TEXTO_2,
    "axes.grid": True,
    "grid.color": "#E1E7EC",
    "grid.linewidth": 0.6,
    "legend.frameon": False,
    "legend.fontsize": 8,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
})


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _paleta(nombre: str):
    """Devuelve la paleta pedida; nunca `jet` (se reemplaza por viridis)."""
    if not nombre or nombre.lower() == "jet":
        nombre = "viridis"
    try:
        return colormaps[nombre]
    except KeyError:
        return colormaps["viridis"]


def paleta_rgb(paleta: str, pasos: int = 16) -> list[list[int]]:
    """Lista de `pasos` colores [r, g, b] (0–255) de la paleta, de vmin a vmax."""
    cmap = _paleta(paleta)
    salida = []
    for i in range(pasos):
        r, g, b, _ = cmap(i / max(pasos - 1, 1))
        salida.append([int(round(r * 255)), int(round(g * 255)), int(round(b * 255))])
    return salida


def a_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def figura_a_png(fig, dpi: int = 110) -> bytes:
    """Serializa la figura a PNG y la cierra."""
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=dpi, facecolor=fig.get_facecolor())
    plt.close(fig)
    return buffer.getvalue()


def _num(x, decimales: int = 1) -> str:
    """`cfg.fmt_num` con el signo menos tipográfico («−1.0», como `fmt_anomalia` y la interfaz) y sin «−0.0»
    (las marcas de las barras de color y de los ejes caen a menudo en −1e-17)."""
    texto = cfg.fmt_num(x, decimales).replace("-", "−")
    if texto.startswith("−") and not any(c in "123456789" for c in texto):
        texto = texto[1:]
    return texto


def _formateador(decimales: int = 1):
    return FuncFormatter(lambda x, _: _num(x, decimales))


def _decimales_para(vmin: float, vmax: float) -> int:
    rango = abs(float(vmax) - float(vmin))
    if rango == 0 or not np.isfinite(rango):
        return 1
    if rango >= 50:
        return 0
    if rango >= 5:
        return 1
    if rango >= 0.5:
        return 2
    return 3


def _a_fechas(fechas) -> list[datetime]:
    return [pd.Timestamp(f).to_pydatetime() for f in fechas]


def _eje_fechas(ax, fechas: list[datetime]) -> None:
    if not fechas:
        return
    span = (max(fechas) - min(fechas)).days if len(fechas) > 1 else 0
    formato = "%m/%Y" if span > 400 else "%d/%m/%Y"
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    ax.xaxis.set_major_formatter(mdates.DateFormatter(formato))
    for etiqueta in ax.get_xticklabels():
        etiqueta.set_rotation(20)
        etiqueta.set_horizontalalignment("right")


_TAMANO_PIE = 6.5          # puntos
_ANCHO_CARACTER_PIE = 3.7  # puntos aproximados por carácter en DejaVu Sans a 6.5 pt


COLOR_REAL_FONDO = "#D3EEEA"   # verde suave (datos reales); los simulados y mixtos van en ámbar
COLOR_REAL_TEXTO = "#145A52"


def es_insignia_real(insignia: str | None) -> bool:
    """True si el texto de procedencia corresponde a datos reales (no simulados ni mixtos)."""
    return bool(insignia) and str(insignia).startswith("Datos reales")


def _pie(fig, credito: str, metodo: str | None = None, identificador: str | None = None,
         fecha_corte=None, insignia: str | None = None) -> None:
    """Pie de figura: crédito (con identificador y fecha de corte, RF-12) y método, con la
    insignia de procedencia en una línea propia al pie derecho: «Datos simulados para
    demostración» (por defecto, en ámbar), la de datos reales (NOAA OISST, Copernicus Marine o CHIRPS v3,
    en verde) o la de datos mixtos (en ámbar).

    El texto se parte por caracteres según el ancho real de la figura para que
    nunca invada la insignia, y la insignia queda debajo del bloque de texto.
    """
    insignia = insignia or cfg.INSIGNIA_SIMULADOS
    real = es_insignia_real(insignia)
    ancho_pt = fig.get_figwidth() * 72
    alto_pt = fig.get_figheight() * 72
    max_car = max(40, int(ancho_pt * 0.96 / _ANCHO_CARACTER_PIE))
    lineas: list[str] = []
    if credito:
        fuente = f"Fuente: {credito.rstrip('.') if (identificador or fecha_corte) else credito}"
        if identificador:
            fuente += f" · Identificador {identificador}"
        if fecha_corte:
            fuente += f" · Fecha de corte {cfg.fmt_fecha(fecha_corte)}"
        lineas += textwrap.wrap(fuente, max_car) or [fuente]
    if metodo:
        lineas += textwrap.wrap(f"Método: {metodo}", max_car) or [f"Método: {metodo}"]
    alto_insignia_pt = _TAMANO_PIE + 7   # texto + relleno del recuadro
    y_texto = (6 + alto_insignia_pt + 3) / alto_pt
    if lineas:
        fig.text(0.012, y_texto, "\n".join(lineas), fontsize=_TAMANO_PIE, color=COLOR_TEXTO_2,
                 ha="left", va="bottom", linespacing=1.25)
    texto_insignia = fig.text(0.988, 6 / alto_pt, insignia, fontsize=_TAMANO_PIE,
                              color=COLOR_REAL_TEXTO if real else COLOR_TEXTO, ha="right", va="bottom",
                              bbox=dict(boxstyle="round,pad=0.3", facecolor=COLOR_REAL_FONDO if real else COLOR_AVISO,
                                        edgecolor="none"))
    aviso = _aviso_pie(credito, insignia)
    if aviso:
        # A la izquierda de la insignia, en su misma fila: hasta dos líneas caben bajo el bloque de texto
        ancho_insignia = texto_insignia.get_window_extent(renderer=fig.canvas.get_renderer()).width * 72 / fig.dpi
        disponible = ancho_pt * 0.976 - ancho_insignia - 16
        lineas_aviso = _envolver(fig, aviso, disponible, fontsize=_TAMANO_PIE)
        if len(lineas_aviso) > 2:
            lineas_aviso = _envolver(fig, _AVISO_PIE_CORTO, disponible, fontsize=_TAMANO_PIE)[:2]
        fig.text(0.012, 6 / alto_pt, "\n".join(lineas_aviso), fontsize=_TAMANO_PIE, color=COLOR_TEXTO_2,
                 ha="left", va="bottom", linespacing=1.25)


# Lo que piden las licencias en toda figura que sale del sistema (CONDICIONES-USO-FUENTES.md): declarar que el
# INOCAR procesó los datos y no sugerir aval del proveedor; con CHIRPS (CC BY 4.0), el enlace a la licencia y,
# si se usó la réplica de dynamical.org, que sus valores vienen redondeados. Solo en figuras con datos reales.
_AVISO_PIE = "Procesado por el INOCAR (SOME); no es un dato original del proveedor ni cuenta con su aval."
_AVISO_PIE_CORTO = "Procesado por el INOCAR; sin aval del proveedor."


def _aviso_pie(credito: str | None, insignia: str) -> str:
    if not (es_insignia_real(insignia) or insignia == cfg.INSIGNIA_MIXTOS):
        return ""
    aviso = _AVISO_PIE
    if credito and "dynamical.org" in credito:
        aviso += " Réplica con valores redondeados por dynamical.org."
    if credito and "CC BY 4.0" in credito:
        aviso += " CC BY 4.0: https://creativecommons.org/licenses/by/4.0/"
    return aviso


def _ancho_texto_pt(fig, texto: str, **propiedades) -> float:
    """Ancho real (en puntos) de una línea de texto con esas propiedades, medido con el renderizador."""
    t = fig.text(0, 0, texto, **propiedades)
    ancho = t.get_window_extent(renderer=fig.canvas.get_renderer()).width * 72 / fig.dpi
    t.remove()
    return ancho


def _envolver(fig, texto: str, disponible_pt: float, **propiedades) -> list[str]:
    """Parte el texto en líneas que caben en `disponible_pt`, midiendo cada una (la estimación por
    caracteres sola dejaba títulos largos cortados a la derecha, p. ej. el de la cortina de Copernicus)."""
    tamano = float(propiedades.get("fontsize", 10))
    max_car = max(20, int(disponible_pt / (tamano * 0.56)))   # estimación inicial (≈ 0.56 em por carácter)
    while True:
        lineas = textwrap.wrap(texto, max_car) or [texto]
        if max_car <= 20 or all(_ancho_texto_pt(fig, ln, **propiedades) <= disponible_pt for ln in lineas):
            return lineas
        max_car -= 3


def _titulos(fig, titulo: str, subtitulo: str = "") -> None:
    """Título y subtítulo arriba a la izquierda. El título se parte en líneas que caben en el ancho de la
    figura (medidas, no estimadas); si necesitara más de dos, se reduce el tamaño de letra. El subtítulo
    va en una línea y, si no cabe, se reduce su tamaño."""
    ancho_pt = fig.get_figwidth() * 72
    alto_pt = fig.get_figheight() * 72
    disponible = ancho_pt * 0.97          # desde x = 0.012 hasta casi el borde derecho
    tamano = 11.5
    for tamano in (11.5, 10.5, 9.5):
        lineas = _envolver(fig, titulo, disponible, fontsize=tamano, fontweight="semibold")
        if len(lineas) <= 2:
            break
    y_titulo = 1 - 7 / alto_pt
    fig.text(0.012, y_titulo, "\n".join(lineas), fontsize=tamano, fontweight="semibold", color=COLOR_TEXTO,
             ha="left", va="top", linespacing=1.15)
    if subtitulo:
        tamano_sub = 8.5
        while tamano_sub > 7 and _ancho_texto_pt(fig, subtitulo, fontsize=tamano_sub) > disponible:
            tamano_sub -= 0.5
        y_sub = y_titulo - (len(lineas) * tamano * 1.15 + 6) / alto_pt
        fig.text(0.012, y_sub, subtitulo, fontsize=tamano_sub, color=COLOR_TEXTO_2, ha="left", va="top")


def _acortar(texto: str, maximo: int = 150) -> str:
    """Acorta un texto largo con puntos suspensivos (los créditos, ≤ 150 caracteres, nunca se cortan)."""
    return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"


_MAX_METODO = 260   # el método puede partirse en dos o tres líneas del pie
# Crédito: el de CHIRPS cabe en 150 caracteres; el de Copernicus Marine con climatología (datos del análisis
# ANFC + GLORYS12, con sus dos DOI, ≈ 190) no debe cortarse: con identificador y fecha de corte sigue en dos
# líneas del pie en las figuras de 8.4 pulgadas.
_MAX_CREDITO = 200


def _dibujar_costa(ax, lon_min: float, lon_max: float, lat_min: float, lat_max: float,
                   fondo_mar: bool = False) -> None:
    """Dibuja la línea de costa interpolada, rellena la tierra y marca Galápagos."""
    if fondo_mar:
        ax.set_facecolor(COLOR_MAR)
    lat_fina = np.linspace(lat_min, lat_max, 241)
    lon_costa = sim.costa(lat_fina)
    ax.fill_betweenx(lat_fina, lon_costa, np.full_like(lon_costa, lon_max + 1),
                     color=COLOR_TIERRA, zorder=0.5, linewidth=0)
    ax.plot(lon_costa, lat_fina, color=COLOR_COSTA, linewidth=0.8, zorder=3)
    for lat0, lat1, lon0, lon1 in sim._CAJAS_GALAPAGOS:
        ax.add_patch(Rectangle((lon0 - 0.125, lat0 - 0.125), (lon1 - lon0) + 0.25,
                               (lat1 - lat0) + 0.25, facecolor=COLOR_TIERRA,
                               edgecolor=COLOR_COSTA, linewidth=0.6, zorder=3))
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)


def _formato_grados(eje: str):
    def _f(x, _):
        if eje == "lon":
            return f"{cfg.fmt_num(abs(x), 1)}° {'E' if x > 0 else 'O'}" if x != 0 else "0°"
        return f"{cfg.fmt_num(abs(x), 1)}° {'N' if x > 0 else 'S'}" if x != 0 else "0°"
    return FuncFormatter(_f)


# ---------------------------------------------------------------------------
# Mapa PNG para Leaflet
# ---------------------------------------------------------------------------
def _campo_suavizado(matriz: np.ndarray, factor: int) -> tuple[np.ndarray, np.ndarray]:
    """Amplía el campo `factor` veces con interpolación bilineal y lo suaviza.

    Las celdas sin dato se rellenan antes con el valor válido más cercano para que
    la interpolación no arrastre NaN hacia el mar; la máscara de validez se amplía
    por separado y define qué píxeles quedan transparentes (borde a media celda).
    Devuelve (campo ampliado, máscara de validez ampliada).
    """
    valido = np.isfinite(matriz)
    if valido.all():
        relleno = matriz
    elif valido.any():
        indices = ndimage.distance_transform_edt(~valido, return_distances=False, return_indices=True)
        relleno = matriz[tuple(indices)]
    else:
        return np.zeros((matriz.shape[0] * factor, matriz.shape[1] * factor)), \
            np.zeros((matriz.shape[0] * factor, matriz.shape[1] * factor), dtype=bool)
    # Se replican los bordes antes de ampliar para que los píxeles exteriores de la
    # celda del borde conserven su valor (zoom interpola entre centros de celda).
    acolchado = np.pad(relleno, 1, mode="edge")
    ampliado = ndimage.zoom(acolchado, factor, order=1, mode="nearest")
    ampliado = ampliado[factor:-factor, factor:-factor]
    ampliado = ndimage.gaussian_filter(ampliado, sigma=max(factor * 0.45, 1.0), mode="nearest")
    mascara = ndimage.zoom(np.pad(valido.astype(float), 1, mode="edge"), factor, order=1, mode="nearest")
    mascara = mascara[factor:-factor, factor:-factor] >= 0.5
    return ampliado, mascara


def _niveles_isolineas(vmin: float, vmax: float, maximo: int = 8) -> np.ndarray:
    """Niveles «redondos» para las isolíneas dentro de [vmin, vmax]."""
    niveles = MaxNLocator(nbins=maximo, steps=[1, 2, 2.5, 5, 10]).tick_values(vmin, vmax)
    return niveles[(niveles > vmin) & (niveles < vmax)]


def mapa_png(valores, bbox, paleta: str, vmin: float, vmax: float,
             pixeles_por_celda: int = 8, suavizado: bool = True,
             isolineas: bool = False, decimales: int = 1) -> str:
    """PNG transparente del campo (lat creciente hacia el norte) como data URI.

    `valores` es una matriz [lat][lon] con NaN o None donde no hay dato
    (tierra para variables marinas, mar para la precipitación). Sin ejes y con
    tamaño proporcional al número de celdas del bbox, para
    `L.imageOverlay(imagen_png, [[lat_min, lon_min], [lat_max, lon_max]])`.

    Con `suavizado` el campo se interpola entre los centros de celda (aspecto
    continuo, como un mapa de análisis); sin él se pinta celda a celda. Con
    `isolineas` se trazan y rotulan líneas de igual valor sobre el campo. El
    valor que la interfaz muestra por celda se toma siempre de la malla original,
    nunca del PNG.
    """
    matriz = np.array([[np.nan if v is None else v for v in fila] for fila in valores], dtype=float) \
        if not isinstance(valores, np.ndarray) else valores.astype(float)
    if matriz.ndim != 2 or matriz.size == 0:
        matriz = np.full((1, 1), np.nan)
    if vmin is None or vmax is None or not np.isfinite([vmin, vmax]).all() or vmax <= vmin:
        finitos = matriz[np.isfinite(matriz)]
        vmin = float(finitos.min()) if finitos.size else 0.0
        vmax = float(finitos.max()) if finitos.size else 1.0
        if vmax <= vmin:
            vmin, vmax = vmin - 0.5, vmax + 0.5

    cmap = _paleta(paleta)
    norma = Normalize(vmin=vmin, vmax=vmax, clip=True)
    ancho = max(1, matriz.shape[1]) * pixeles_por_celda
    alto = max(1, matriz.shape[0]) * pixeles_por_celda

    if suavizado and matriz.shape[0] > 1 and matriz.shape[1] > 1:
        campo, valido = _campo_suavizado(matriz, pixeles_por_celda)
    else:
        campo = np.repeat(np.repeat(np.nan_to_num(matriz, nan=vmin), pixeles_por_celda, 0), pixeles_por_celda, 1)
        valido = np.repeat(np.repeat(np.isfinite(matriz), pixeles_por_celda, 0), pixeles_por_celda, 1)

    rgba = cmap(norma(campo))                                      # (alto, ancho, 4) en 0–1
    rgba[..., 3] = np.where(valido, 1.0, 0.0)                      # transparente sin dato
    rgba8 = (rgba * 255).round().astype(np.uint8)

    if isolineas and suavizado and valido.any():
        niveles = _niveles_isolineas(vmin, vmax)
        if niveles.size:
            fig = plt.figure(figsize=(ancho / 100, alto / 100), dpi=100)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.axis("off")
            ax.grid(False)
            ax.imshow(rgba8, origin="lower", extent=[0, ancho, 0, alto], interpolation="nearest", aspect="auto")
            enmascarado = np.ma.masked_where(~valido, campo)
            contorno = ax.contour(np.arange(ancho) + 0.5, np.arange(alto) + 0.5, enmascarado,
                                  levels=niveles, colors="#1b1b1b", linewidths=0.9, alpha=0.75)
            ax.clabel(contorno, inline=True, fontsize=7.5, colors="#1b1b1b",
                      fmt=lambda x: _num(x, decimales), inline_spacing=3)
            ax.set_xlim(0, ancho)
            ax.set_ylim(0, alto)
            buffer = io.BytesIO()
            fig.savefig(buffer, format="png", dpi=100, transparent=True, pad_inches=0)
            plt.close(fig)
            return a_data_uri(buffer.getvalue())

    imagen = Image.fromarray(rgba8, mode="RGBA")
    imagen = imagen.transpose(Image.FLIP_TOP_BOTTOM)              # fila 0 = norte
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG", optimize=True)
    return a_data_uri(buffer.getvalue())


# ---------------------------------------------------------------------------
# Figuras completas (exportación y reportes)
# ---------------------------------------------------------------------------
def figura_mapa(lats, lons, valores, titulo: str, subtitulo: str, unidad: str, paleta: str,
                vmin: float | None, vmax: float | None, credito: str,
                es_tierra: bool = False, metodo: str | None = None,
                u=None, v=None, paso_vectores: int = 2,
                identificador: str | None = None, fecha_corte=None,
                insignia: str | None = None) -> bytes:
    """Mapa completo: campo, costa, ejes en grados, barra de color, crédito e insignia."""
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    matriz = np.array([[np.nan if x is None else x for x in fila] for fila in valores], dtype=float) \
        if not isinstance(valores, np.ndarray) else valores.astype(float)
    finitos = matriz[np.isfinite(matriz)]
    if vmin is None or vmax is None:
        vmin = float(finitos.min()) if finitos.size else 0.0
        vmax = float(finitos.max()) if finitos.size else 1.0
    if vmax <= vmin:
        vmin, vmax = vmin - 0.5, vmax + 0.5

    medio = cfg.RESOLUCION_GRADOS / 2
    lon_min, lon_max = lons.min() - medio, lons.max() + medio
    lat_min, lat_max = lats.min() - medio, lats.max() + medio
    relacion = (lat_max - lat_min) / max(lon_max - lon_min, 1e-6)
    ancho_fig = 8.4
    alto_mapa = min(max(ancho_fig * 0.78 * relacion, 2.2), 7.0)
    alto_pie = 1.55   # pulgadas reservadas bajo los ejes para etiquetas, crédito, método e insignia
    fig = plt.figure(figsize=(ancho_fig, alto_mapa + alto_pie + 0.75))
    alto_total = alto_mapa + alto_pie + 0.75
    ax = fig.add_axes([0.08, alto_pie / alto_total, 0.72, alto_mapa / alto_total])
    ax.grid(False)

    _dibujar_costa(ax, lon_min, lon_max, lat_min, lat_max, fondo_mar=es_tierra)
    # Mismo suavizado que el mapa en pantalla, para que la exportación se vea igual
    if matriz.shape[0] > 1 and matriz.shape[1] > 1:
        campo, valido = _campo_suavizado(matriz, 6)
        enmascarada = np.ma.masked_where(~valido, campo)
    else:
        enmascarada = np.ma.masked_invalid(matriz)
    imagen = ax.imshow(enmascarada, origin="lower", extent=[lon_min, lon_max, lat_min, lat_max],
                       cmap=_paleta(paleta), vmin=vmin, vmax=vmax, interpolation="nearest",
                       aspect="auto", zorder=2)
    if u is not None and v is not None:
        um = np.asarray(u, dtype=float)
        vm = np.asarray(v, dtype=float)
        paso = max(1, int(paso_vectores))
        malla_lon, malla_lat = np.meshgrid(lons, lats)
        ax.quiver(malla_lon[::paso, ::paso], malla_lat[::paso, ::paso],
                  um[::paso, ::paso], vm[::paso, ::paso], color="#222222", scale=6,
                  width=0.0025, zorder=4)

    ax.xaxis.set_major_formatter(_formato_grados("lon"))
    ax.yaxis.set_major_formatter(_formato_grados("lat"))
    ax.tick_params(labelsize=7.5)
    ax.set_xlabel("Longitud")
    ax.set_ylabel("Latitud")

    eje_barra = fig.add_axes([0.83, alto_pie / alto_total, 0.025, alto_mapa / alto_total])
    barra = fig.colorbar(imagen, cax=eje_barra)
    barra.ax.yaxis.set_major_formatter(_formateador(_decimales_para(vmin, vmax)))
    barra.ax.tick_params(labelsize=7.5)
    barra.set_label(unidad, fontsize=8.5)

    _titulos(fig, titulo, subtitulo)
    _pie(fig, _acortar(credito, _MAX_CREDITO), _acortar(metodo, _MAX_METODO) if metodo else None, identificador, fecha_corte, insignia)
    return figura_a_png(fig)


def _leyenda_sobre_ejes(ax, ncol: int = 2) -> None:
    """Leyenda fuera del área de datos (justo encima de los ejes), para no tapar las curvas."""
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=ncol, borderaxespad=0.2, frameon=False)


def figura_serie(fechas, valores, titulo: str, unidad: str, credito: str,
                 climatologia=None, anomalias=None, etiqueta: str = "Valor",
                 subtitulo: str = "", colores_anomalia: tuple[str, str] | None = None,
                 decimales: int = 1, metodo: str | None = None,
                 identificador: str | None = None, fecha_corte=None,
                insignia: str | None = None) -> bytes:
    """Serie de tiempo con climatología (línea discontinua) y panel inferior de anomalías."""
    fechas_dt = _a_fechas(fechas)
    valores = np.asarray([np.nan if x is None else x for x in valores], dtype=float)
    tiene_anomalias = anomalias is not None and len(anomalias) == len(fechas_dt)
    alto = 6.1 if tiene_anomalias else 4.7
    fig = plt.figure(figsize=(8.4, alto))
    if tiene_anomalias:
        ax = fig.add_axes([0.09, 0.50, 0.88, 0.35])
        ax2 = fig.add_axes([0.09, 0.235, 0.88, 0.22], sharex=ax)
    else:
        ax = fig.add_axes([0.09, 0.29, 0.88, 0.56])
        ax2 = None

    ax.plot(fechas_dt, valores, color=COLOR_PRIMARIO, linewidth=1.4, label=etiqueta)
    if climatologia is not None and len(climatologia) == len(fechas_dt):
        clim = np.asarray([np.nan if x is None else x for x in climatologia], dtype=float)
        ax.plot(fechas_dt, clim, color=COLOR_ACENTO, linewidth=1.2, linestyle="--",
                label="Climatología mensual")
    ax.set_ylabel(unidad)
    ax.yaxis.set_major_formatter(_formateador(decimales))
    _leyenda_sobre_ejes(ax, ncol=2)
    if ax2 is not None:
        anom = np.asarray([np.nan if x is None else x for x in anomalias], dtype=float)
        positivo, negativo = colores_anomalia or (COLOR_PELIGRO, COLOR_PRIMARIO)
        colores = np.where(anom >= 0, positivo, negativo)
        ancho = 0.8
        if len(fechas_dt) > 1:
            dias = np.diff(mdates.date2num(fechas_dt))
            ancho = max(float(np.median(dias)) * 0.8, 0.8)
        ax2.bar(fechas_dt, anom, width=ancho, color=colores, linewidth=0, align="edge")
        ax2.axhline(0, color=COLOR_TEXTO_2, linewidth=0.8)
        ax2.set_ylabel(f"Anomalía ({unidad})")
        ax2.yaxis.set_major_formatter(_formateador(decimales))
        plt.setp(ax.get_xticklabels(), visible=False)
        _eje_fechas(ax2, fechas_dt)
    else:
        _eje_fechas(ax, fechas_dt)

    _titulos(fig, titulo, subtitulo)
    _pie(fig, _acortar(credito, _MAX_CREDITO), _acortar(metodo, _MAX_METODO) if metodo else None, identificador, fecha_corte, insignia)
    return figura_a_png(fig)


def figura_comparacion(fechas_a, valores_a, etiqueta_a: str, fechas_b, valores_b, etiqueta_b: str,
                       titulo: str, unidad: str, credito: str, subtitulo: str = "",
                       alineacion: str = "fecha", decimales: int = 1,
                       metodo: str | None = None,
                       identificador: str | None = None, fecha_corte=None,
                insignia: str | None = None) -> bytes:
    """Dos series superpuestas (misma fecha o día relativo al inicio de cada periodo)."""
    va = np.asarray([np.nan if x is None else x for x in valores_a], dtype=float)
    vb = np.asarray([np.nan if x is None else x for x in valores_b], dtype=float)
    fig = plt.figure(figsize=(8.4, 4.9))
    ax = fig.add_axes([0.09, 0.28, 0.88, 0.55])
    if alineacion == "fecha":
        fa, fb = _a_fechas(fechas_a), _a_fechas(fechas_b)
        ax.plot(fa, va, color=COLOR_PRIMARIO, linewidth=1.4, label=etiqueta_a)
        ax.plot(fb, vb, color=COLOR_ACENTO, linewidth=1.4, label=etiqueta_b)
        _eje_fechas(ax, fa + fb)
    else:
        ax.plot(np.arange(len(va)), va, color=COLOR_PRIMARIO, linewidth=1.4, label=etiqueta_a)
        ax.plot(np.arange(len(vb)), vb, color=COLOR_ACENTO, linewidth=1.4, label=etiqueta_b)
        ax.set_xlabel("Día relativo al inicio de cada periodo")
    ax.set_ylabel(unidad)
    ax.yaxis.set_major_formatter(_formateador(decimales))
    _leyenda_sobre_ejes(ax, ncol=1)
    _titulos(fig, titulo, subtitulo)
    _pie(fig, _acortar(credito, _MAX_CREDITO), _acortar(metodo, _MAX_METODO) if metodo else None, identificador, fecha_corte, insignia)
    return figura_a_png(fig)


def figura_perfil(profundidades, valores, titulo: str, unidad: str, credito: str,
                  capa_mezcla_m: float | None = None, subtitulo: str = "",
                  decimales: int = 1, metodo: str | None = None,
                  identificador: str | None = None, fecha_corte=None,
                insignia: str | None = None) -> bytes:
    """Perfil vertical (profundidad en Y invertido) con marca de la capa de mezcla."""
    z = np.asarray(profundidades, dtype=float)
    v = np.asarray([np.nan if x is None else x for x in valores], dtype=float)
    fig = plt.figure(figsize=(5.6, 6.4))
    ax = fig.add_axes([0.17, 0.27, 0.78, 0.58])
    ax.plot(v, z, color=COLOR_PRIMARIO, linewidth=1.6, marker="o", markersize=4)
    if capa_mezcla_m is not None and np.isfinite(capa_mezcla_m):
        ax.axhline(capa_mezcla_m, color=COLOR_ACENTO, linestyle="--", linewidth=1.2)
        ax.text(0.98, capa_mezcla_m, f"Capa de mezcla: {cfg.fmt_num(capa_mezcla_m, 0)} m",
                transform=ax.get_yaxis_transform(), ha="right", va="bottom", fontsize=8,
                color=COLOR_TEXTO)
    ax.invert_yaxis()
    ax.set_ylabel("Profundidad (m)")
    ax.set_xlabel(unidad)
    ax.xaxis.set_major_formatter(_formateador(decimales))
    ax.yaxis.set_major_formatter(_formateador(0))
    _titulos(fig, titulo, subtitulo)
    # El crédito completo (con el DOI de Copernicus) no se acorta: el pie lo parte en líneas
    _pie(fig, _acortar(credito, _MAX_CREDITO), _acortar(metodo, _MAX_METODO) if metodo else None, identificador, fecha_corte, insignia)
    return figura_a_png(fig)


def figura_cortina(fechas, profundidades, valores, titulo: str, unidad: str, credito: str,
                   paleta: str = "viridis", subtitulo: str = "", metodo: str | None = None,
                   identificador: str | None = None, fecha_corte=None,
                insignia: str | None = None) -> bytes:
    """Cortina profundidad-tiempo (matriz [depth][time]) con eje Y invertido."""
    fechas_dt = _a_fechas(fechas)
    z = np.asarray(profundidades, dtype=float)
    matriz = np.array([[np.nan if x is None else x for x in fila] for fila in valores], dtype=float)
    fig = plt.figure(figsize=(8.4, 5.1))
    ax = fig.add_axes([0.09, 0.28, 0.78, 0.56])
    x = mdates.date2num(fechas_dt)
    if len(x) > 1:
        bordes_x = np.concatenate([[x[0] - (x[1] - x[0]) / 2], (x[:-1] + x[1:]) / 2,
                                   [x[-1] + (x[-1] - x[-2]) / 2]])
    else:
        bordes_x = np.array([x[0] - 0.5, x[0] + 0.5])
    if len(z) > 1:
        bordes_z = np.concatenate([[z[0] - (z[1] - z[0]) / 2], (z[:-1] + z[1:]) / 2,
                                   [z[-1] + (z[-1] - z[-2]) / 2]])
        bordes_z[0] = max(bordes_z[0], 0.0)
    else:
        bordes_z = np.array([z[0] - 1, z[0] + 1])
    malla = ax.pcolormesh(bordes_x, bordes_z, np.ma.masked_invalid(matriz), cmap=_paleta(paleta),
                          shading="flat")
    ax.invert_yaxis()
    ax.xaxis_date()
    _eje_fechas(ax, fechas_dt)
    ax.set_ylabel("Profundidad (m)")
    ax.grid(False)
    eje_barra = fig.add_axes([0.89, 0.28, 0.025, 0.56])
    barra = fig.colorbar(malla, cax=eje_barra)
    finitos = matriz[np.isfinite(matriz)]
    if finitos.size:
        barra.ax.yaxis.set_major_formatter(_formateador(_decimales_para(finitos.min(), finitos.max())))
    barra.set_label(unidad, fontsize=8.5)
    barra.ax.tick_params(labelsize=7.5)
    _titulos(fig, titulo, subtitulo)
    _pie(fig, _acortar(credito, _MAX_CREDITO), _acortar(metodo, _MAX_METODO) if metodo else None, identificador, fecha_corte, insignia)
    return figura_a_png(fig)


def figura_climatologia(valores, titulo: str, unidad: str, credito: str,
                        valores_anios: dict[str, list] | None = None, subtitulo: str = "",
                        decimales: int = 1, metodo: str | None = None,
                        identificador: str | None = None, fecha_corte=None,
                insignia: str | None = None) -> bytes:
    """Climatología mensual (12 barras) y, opcionalmente, los valores de cada año como líneas."""
    v = np.asarray([np.nan if x is None else x for x in valores], dtype=float)
    fig = plt.figure(figsize=(8.4, 4.9))
    ax = fig.add_axes([0.09, 0.27, 0.88, 0.57])
    x = np.arange(12)
    ax.bar(x, v, color=COLOR_PRIMARIO, alpha=0.75, width=0.7, label="Climatología mensual")
    if valores_anios:
        colores = [COLOR_ACENTO, COLOR_EXITO, COLOR_PELIGRO, "#7B61FF", "#8D99AE"]
        for i, (anio, serie_anio) in enumerate(valores_anios.items()):
            sa = np.asarray([np.nan if s is None else s for s in serie_anio], dtype=float)
            ax.plot(x[: len(sa)], sa, marker="o", markersize=3.5, linewidth=1.3,
                    color=colores[i % len(colores)], label=str(anio))
    ax.set_xticks(x)
    ax.set_xticklabels([m.capitalize() for m in cfg.MESES_CORTOS])
    ax.set_ylabel(unidad)
    ax.yaxis.set_major_formatter(_formateador(decimales))
    _leyenda_sobre_ejes(ax, ncol=3)
    _titulos(fig, titulo, subtitulo)
    _pie(fig, _acortar(credito, _MAX_CREDITO), _acortar(metodo, _MAX_METODO) if metodo else None, identificador, fecha_corte, insignia)
    return figura_a_png(fig)


def figura_barras(etiquetas, valores, titulo: str, unidad: str, credito: str,
                  colores=None, subtitulo: str = "", decimales: int = 1,
                  horizontal: bool = False, metodo: str | None = None,
                  valores_referencia=None, etiqueta_referencia: str = "Normal",
                  identificador: str | None = None, fecha_corte=None,
                insignia: str | None = None) -> bytes:
    """Barras por categoría (p. ej., precipitación por provincia) con etiquetas de valor."""
    v = np.asarray([np.nan if x is None else x for x in valores], dtype=float)
    n = len(v)
    alto = 4.9 if not horizontal else 0.9 + 0.45 * n + 2.1
    fig = plt.figure(figsize=(8.4, alto))
    abajo = 1.35 / alto
    ax = fig.add_axes([0.22 if horizontal else 0.09, abajo, 0.74 if horizontal else 0.88, 0.85 - abajo])
    color = colores if colores is not None else COLOR_PRIMARIO
    x = np.arange(n)
    if horizontal:
        ax.barh(x, v, color=color, height=0.6)
        if valores_referencia is not None:
            ax.plot(np.asarray(valores_referencia, dtype=float), x, linestyle="none", marker="|",
                    markersize=14, color=COLOR_ACENTO, label=etiqueta_referencia)
            ax.legend(loc="lower right")
        ax.set_yticks(x)
        ax.set_yticklabels(etiquetas)
        ax.invert_yaxis()
        ax.set_xlabel(unidad)
        ax.xaxis.set_major_formatter(_formateador(decimales))
        for xi, vi in zip(x, v):
            if np.isfinite(vi):
                ax.text(vi, xi, " " + _num(vi, decimales), va="center", fontsize=8)
    else:
        ax.bar(x, v, color=color, width=0.65)
        if valores_referencia is not None:
            ax.plot(x, np.asarray(valores_referencia, dtype=float), linestyle="none", marker="_",
                    markersize=18, color=COLOR_ACENTO, label=etiqueta_referencia)
            ax.legend(loc="upper right")
        ax.set_xticks(x)
        ax.set_xticklabels(etiquetas, rotation=15 if n > 6 else 0,
                           ha="right" if n > 6 else "center")
        ax.set_ylabel(unidad)
        ax.yaxis.set_major_formatter(_formateador(decimales))
        for xi, vi in zip(x, v):
            if np.isfinite(vi):
                ax.text(xi, vi, _num(vi, decimales), ha="center", va="bottom", fontsize=8)
    _titulos(fig, titulo, subtitulo)
    _pie(fig, _acortar(credito, _MAX_CREDITO), _acortar(metodo, _MAX_METODO) if metodo else None, identificador, fecha_corte, insignia)
    return figura_a_png(fig)


def figura_indice_enso(fechas, valores, titulo: str = "Índice Niño 1+2",
                       subtitulo: str = "", credito: str = "", metodo: str | None = None,
                       identificador: str | None = None, fecha_corte=None,
                insignia: str | None = None, parciales=None, preliminares=None) -> bytes:
    """Serie del índice ENSO con franjas de semáforo (El Niño / neutro / La Niña).

    `valores` admite None (semanas sin dato del índice real: hueco en la línea). `parciales` y `preliminares`
    (listas de booleanos, opcionales, como las del índice real de NOAA OISST) marcan con un círculo hueco las
    semanas incompletas o con días preliminares, y el último valor se rotula. El título por defecto no presume
    la procedencia: la indican quien llama (p. ej., «Índice Niño 1+2 simulado») y la insignia del pie."""
    fechas_dt = _a_fechas(fechas)
    v = np.asarray([np.nan if x is None else x for x in valores], dtype=float)
    finitos = v[np.isfinite(v)]
    v_max = float(finitos.max()) if finitos.size else 0.0
    v_min = float(finitos.min()) if finitos.size else 0.0
    fig = plt.figure(figsize=(8.4, 4.3))
    ax = fig.add_axes([0.09, 0.28, 0.88, 0.56])
    ax.axhspan(0.5, max(v_max + 0.5, 1.0), color=COLOR_PELIGRO, alpha=0.08)
    ax.axhspan(-0.5, 0.5, color=COLOR_EXITO, alpha=0.08)
    ax.axhspan(min(v_min - 0.5, -1.0), -0.5, color=COLOR_PRIMARIO, alpha=0.08)
    marcas = np.zeros(len(v), dtype=bool)
    for lista in (parciales, preliminares):
        if lista is not None and len(lista) == len(v):
            marcas |= np.asarray([bool(x) for x in lista])
    marcas &= np.isfinite(v)
    ax.plot(fechas_dt, v, color=COLOR_TEXTO, linewidth=1.5, label="Anomalía semanal" if marcas.any() else None)
    if marcas.any():
        ax.plot([f for f, m in zip(fechas_dt, marcas) if m], v[marcas], linestyle="none", marker="o",
                markersize=5, markerfacecolor="white", markeredgecolor=COLOR_TEXTO, markeredgewidth=1.2,
                label="Semana incompleta o con días preliminares")
        _leyenda_sobre_ejes(ax, ncol=2)
    if finitos.size:
        ultimo = int(np.nonzero(np.isfinite(v))[0][-1])
        x = float(v[ultimo])
        ax.annotate(f"{'+' if x > 0 else ''}{_num(x, 2)} °C", (fechas_dt[ultimo], x), xytext=(-6, 7),
                    textcoords="offset points", ha="right", va="bottom", fontsize=8, color=COLOR_TEXTO,
                    fontweight="semibold")
    ax.axhline(0, color=COLOR_TEXTO_2, linewidth=0.8)
    ax.set_ylabel("Anomalía (°C)")
    ax.yaxis.set_major_formatter(_formateador(1))
    _eje_fechas(ax, fechas_dt)
    _titulos(fig, titulo, subtitulo)
    _pie(fig, _acortar(credito or "Índice Niño 1+2: anomalía de la temperatura superficial del mar en la región "
                                  "Niño 1+2", _MAX_CREDITO),
         _acortar(metodo, _MAX_METODO) if metodo else None, identificador, fecha_corte, insignia)
    return figura_a_png(fig)
