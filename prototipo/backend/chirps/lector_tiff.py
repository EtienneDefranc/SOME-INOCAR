"""Lector de GeoTIFF de CHIRPS v3 por rangos HTTP.

Lee solo lo necesario de un GeoTIFF remoto (sin descargar el archivo completo):

1. Encabezado de 8 bytes (orden de bytes, número mágico y desplazamiento del IFD).
2. El IFD y sus valores fuera de línea (StripOffsets/StripByteCounts, escala y
   punto de amarre GeoTIFF, GDAL_NODATA).
3. En UNA sola petición Range, el bloque contiguo de franjas de las filas
   necesarias.

Las franjas se decodifican armando en memoria un TIFF mínimo válido (encabezado
+ IFD + franjas con desplazamientos reubicados) que Pillow abre en modo «F».
Solo se aceptan franjas de una fila, float32, sin predictor y compresión LZW o
ninguna; cualquier otra estructura produce un error explícito.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass

import numpy as np
from PIL import Image

from . import red
from .constantes import ErrorCHIRPS

# Tipos TIFF → (código struct, tamaño)
_TIPOS = {1: ("B", 1), 2: ("c", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8),
          11: ("f", 4), 12: ("d", 8), 16: ("Q", 8)}
_VENTANA_IFD = 64 * 1024


@dataclass
class InfoTIFF:
    url: str
    orden: str                     # "<" o ">"
    ancho: int
    alto: int
    compresion: int
    predictor: int
    filas_por_franja: int
    bits: int
    formato_muestra: int
    muestras: int
    offsets: np.ndarray            # int64 por franja
    conteos: np.ndarray            # int64 por franja
    x0: float                      # borde oeste
    y0: float                      # borde norte
    dx: float
    dy: float
    sin_dato: float

    def ventana(self, lat_norte: float, lat_sur: float, lon_oeste: float, lon_este: float):
        """Filas y columnas [f0, f1) × [c0, c1) cuyos bordes coinciden con la caja dada."""
        f0 = int(round((self.y0 - lat_norte) / self.dy))
        f1 = int(round((self.y0 - lat_sur) / self.dy))
        c0 = int(round((lon_oeste - self.x0) / self.dx))
        c1 = int(round((lon_este - self.x0) / self.dx))
        if not (0 <= f0 < f1 <= self.alto and 0 <= c0 < c1 <= self.ancho):
            raise ErrorCHIRPS(f"La ventana pedida queda fuera de la malla del GeoTIFF {self.url}")
        return f0, f1, c0, c1


def _leer_valor(datos: bytes, base: int, orden: str, tipo: int, cuenta: int, campo: bytes,
                pedir) -> tuple:
    """Valores de una entrada del IFD (en línea o fuera de línea)."""
    if tipo not in _TIPOS:
        raise ErrorCHIRPS(f"Tipo TIFF no soportado ({tipo})")
    codigo, tam = _TIPOS[tipo]
    total = tam * cuenta
    if total <= 4:
        crudo = campo[:total]
    else:
        desplazamiento = struct.unpack(orden + "I", campo)[0]
        ini = desplazamiento - base
        if 0 <= ini and ini + total <= len(datos):
            crudo = datos[ini:ini + total]
        else:
            crudo = pedir(desplazamiento, desplazamiento + total - 1)
    if tipo == 2:
        return (crudo.rstrip(b"\x00").decode("latin-1", "replace"),)
    return struct.unpack(orden + codigo * cuenta, crudo)


def leer_info(url: str) -> InfoTIFF:
    """Lee encabezado e IFD del GeoTIFF remoto."""
    cabecera = red.rango(url, 0, 7)
    if len(cabecera) < 8:
        raise ErrorCHIRPS(f"Encabezado TIFF incompleto en {url}")
    if cabecera[:2] == b"II":
        orden = "<"
    elif cabecera[:2] == b"MM":
        orden = ">"
    else:
        raise ErrorCHIRPS(f"{url} no es un TIFF (orden de bytes desconocido)")
    magico, off_ifd = struct.unpack(orden + "HI", cabecera[2:8])
    if magico != 42:
        raise ErrorCHIRPS(f"{url}: solo se admite TIFF clásico (número mágico {magico}; BigTIFF no soportado)")

    datos = red.rango(url, off_ifd, off_ifd + _VENTANA_IFD - 1)
    base = off_ifd
    n = struct.unpack(orden + "H", datos[:2])[0]
    if 2 + 12 * n > len(datos):
        datos = red.rango(url, off_ifd, off_ifd + 2 + 12 * n + 3)

    def pedir(a, b):
        return red.rango(url, a, b)

    etiquetas: dict[int, tuple] = {}
    for i in range(n):
        p = 2 + 12 * i
        etiqueta, tipo, cuenta = struct.unpack(orden + "HHI", datos[p:p + 8])
        campo = datos[p + 8:p + 12]
        if etiqueta in (256, 257, 258, 259, 273, 277, 278, 279, 284, 317, 339, 33550, 33922, 42113):
            etiquetas[etiqueta] = _leer_valor(datos, base, orden, tipo, cuenta, campo, pedir)

    def uno(tag, defecto=None):
        v = etiquetas.get(tag)
        return v[0] if v else defecto

    if 33550 not in etiquetas or 33922 not in etiquetas:
        raise ErrorCHIRPS(f"{url}: faltan las etiquetas GeoTIFF ModelPixelScale/ModelTiepoint")
    sx, sy = etiquetas[33550][0], etiquetas[33550][1]
    ti, tj, _tk, tx, ty = etiquetas[33922][:5]
    sin_dato = -9999.0
    if 42113 in etiquetas:
        try:
            sin_dato = float(str(etiquetas[42113][0]).strip())
        except ValueError:
            pass

    info = InfoTIFF(
        url=url, orden=orden, ancho=int(uno(256)), alto=int(uno(257)),
        compresion=int(uno(259, 1)), predictor=int(uno(317, 1)),
        filas_por_franja=int(uno(278, uno(257))), bits=int(uno(258, 1)),
        formato_muestra=int(uno(339, 1)), muestras=int(uno(277, 1)),
        offsets=np.asarray(etiquetas.get(273, ()), dtype=np.int64),
        conteos=np.asarray(etiquetas.get(279, ()), dtype=np.int64),
        x0=float(tx - ti * sx), y0=float(ty + tj * sy), dx=float(sx), dy=float(sy),
        sin_dato=sin_dato,
    )
    _validar(info)
    return info


def _validar(info: InfoTIFF) -> None:
    problemas = []
    if info.orden != "<":
        problemas.append("orden de bytes big-endian (solo se admite little-endian «II»)")
    if info.filas_por_franja != 1:
        problemas.append(f"RowsPerStrip={info.filas_por_franja} (se esperaba 1 fila por franja)")
    if info.compresion not in (1, 5):
        problemas.append(f"compresión {info.compresion} (solo LZW=5 o ninguna=1)")
    if info.predictor != 1:
        problemas.append(f"predictor {info.predictor} (solo sin predictor)")
    if info.bits != 32 or info.formato_muestra != 3 or info.muestras != 1:
        problemas.append("tipo de pixel distinto de float32 de una banda")
    if len(info.offsets) != info.alto or len(info.conteos) != info.alto:
        problemas.append("StripOffsets/StripByteCounts no tienen una entrada por fila")
    if problemas:
        raise ErrorCHIRPS(f"Estructura TIFF no soportada en {info.url}: " + "; ".join(problemas))


def _tiff_minimo(ancho: int, conteos: np.ndarray, franjas: bytes, compresion: int) -> bytes:
    """TIFF little-endian mínimo con `len(conteos)` franjas de una fila (float32)."""
    n = len(conteos)
    entradas = [
        (256, 4, 1, ancho), (257, 4, 1, n), (258, 3, 1, 32), (259, 3, 1, compresion),
        (262, 3, 1, 1), (273, 4, n, None), (277, 3, 1, 1), (278, 4, 1, 1),
        (279, 4, n, None), (284, 3, 1, 1), (339, 3, 1, 3),
    ]
    tam_ifd = 2 + 12 * len(entradas) + 4
    off_offsets = 8 + tam_ifd
    off_conteos = off_offsets + 4 * n
    off_datos = off_conteos + 4 * n
    offsets = off_datos + np.concatenate([[0], np.cumsum(conteos)[:-1]]).astype(np.int64)

    ifd = bytearray(struct.pack("<H", len(entradas)))
    for tag, tipo, cuenta, valor in entradas:
        if tag == 273:
            valor = int(offsets[0]) if n == 1 else off_offsets
        elif tag == 279:
            valor = int(conteos[0]) if n == 1 else off_conteos
        if tipo == 3:
            ifd += struct.pack("<HHIHH", tag, tipo, cuenta, valor, 0)
        else:
            ifd += struct.pack("<HHII", tag, tipo, cuenta, valor)
    ifd += struct.pack("<I", 0)
    return (b"II*\x00" + struct.pack("<I", 8) + bytes(ifd)
            + offsets.astype("<u4").tobytes() + np.asarray(conteos).astype("<u4").tobytes()
            + franjas)


def leer_ventana(info: InfoTIFF, f0: int, f1: int, c0: int, c1: int) -> np.ndarray:
    """Valores float32 de las filas [f0, f1) y columnas [c0, c1), con NaN donde no hay dato.

    Pide en una sola petición Range el bloque contiguo de franjas de esas filas.
    """
    offs = info.offsets[f0:f1]
    cnts = info.conteos[f0:f1]
    ini = int(offs.min())
    fin = int((offs + cnts).max()) - 1
    if fin - ini + 1 > 4 * int(cnts.sum()) + 1_000_000:
        raise ErrorCHIRPS(f"{info.url}: las franjas de las filas pedidas no son contiguas")
    bloque = red.rango(info.url, ini, fin)
    if len(bloque) != fin - ini + 1:
        raise ErrorCHIRPS(f"{info.url}: respuesta parcial ({len(bloque)} de {fin - ini + 1} bytes)")
    franjas = b"".join(bloque[int(o) - ini:int(o) - ini + int(c)] for o, c in zip(offs, cnts))

    tiff = _tiff_minimo(info.ancho, cnts, franjas, info.compresion)
    try:
        with Image.open(io.BytesIO(tiff)) as img:
            if img.mode != "F":
                raise ErrorCHIRPS(f"{info.url}: Pillow abrió el recorte en modo {img.mode}, no «F»")
            arr = np.asarray(img, dtype=np.float32)
    except ErrorCHIRPS:
        raise
    except Exception as e:  # noqa: BLE001 — errores de decodificación de Pillow
        raise ErrorCHIRPS(f"No se pudo decodificar el recorte de {info.url}: {e}") from e
    arr = arr[:, c0:c1].astype(np.float32, copy=True)
    return limpiar(arr, info.sin_dato)


def limpiar(arr: np.ndarray, sin_dato: float = -9999.0, copiar: bool = True) -> np.ndarray:
    """-9999 y valores < −1 → NaN; negativos pequeños (−1 ≤ v < 0) → 0.

    Con `copiar=False` modifica `arr` en el sitio si ya es float32 y escribible (ahorra una copia
    completa en las lecturas grandes de la réplica).
    """
    arr = np.asarray(arr, dtype=np.float32)
    if copiar or not arr.flags.writeable:
        arr = arr.copy()
    with np.errstate(invalid="ignore"):
        arr[(arr == sin_dato) | (arr < -1) | ~np.isfinite(arr)] = np.nan
        arr[arr < 0] = 0.0
    return arr
