"""Backend del prototipo SOME (Sistema de Observación del Mar Equinoccial).

Paquete principal del servidor: configuración, conectores de fuentes,
catálogo, motor de procesamiento, gráficos y caché. Las tres fuentes son
reales: NOAA OISST v2.1 (`backend.noaa`), Copernicus Marine
(`backend.copernicus`) y CHIRPS v3 (`backend.chirps`). Si una fuente no
responde y su caché local no tiene el dato, la respuesta usa el simulador
anterior como respaldo señalado, nunca mezclado con datos reales.
"""

from .configuracion import VERSION

__all__ = ["VERSION"]
