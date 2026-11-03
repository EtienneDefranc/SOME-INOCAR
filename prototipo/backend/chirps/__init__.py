"""Acceso real a CHIRPS v3 (Climate Hazards Center, UCSB) para el prototipo SOME.

Dos fuentes del MISMO producto: el servidor del CHC (GeoTIFF por rangos HTTP,
con acceso cortés: 1 petición/s, un archivo a la vez, disyuntor de 15 min) y
la réplica pública de dynamical.org (Zarr/Icechunk, CC BY 4.0), que se usa
para todo lo que cubre y cuando el CHC no responde.

Interfaz pública:
* `iniciar_sincronizacion()` / `detener_sincronizacion()`: hilo daemon que mantiene
  la caché local al día (no bloquea; idempotente).
* `historial_estado(dias=14)`: estado diario (y fuentes que respondieron) a partir de
  verificaciones reales persistidas.
* `resumen()`: datos para administración (caché por origen, preliminares, estado de cada
  fuente, disyuntor del CHC, sincronización, errores).
* `estado_actual()`: estado RF-21 sin tocar la red.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .. import configuracion as cfg
from . import almacen
from .constantes import CHC, ESPEJO, sin_red
from .estado import datos as _datos_estado
from .estado import estado_actual, historial_estado, legible, resumen_fuentes
from .sincronizacion import detener_sincronizacion, iniciar_sincronizacion, proxima_espera_s, sincronizando

__all__ = ["iniciar_sincronizacion", "detener_sincronizacion", "historial_estado", "resumen",
           "estado_actual"]


def resumen() -> dict:
    """Resumen para administración: días en caché (por tipo y por origen), estado de cada fuente,
    disyuntor del CHC, sincronización, bytes y errores. No toca la red."""
    d = _datos_estado()
    corte = almacen.ultimo_dia()
    if corte is not None:
        c = almacen.conteo(cfg.FECHA_INICIO, corte)
    else:
        c = {"dias": 0, "final": 0, "preliminar": 0, "ausente": 0, "ultimo_final": None,
             "primer_preliminar": None, "ultimo_preliminar": None, "por_origen": {CHC: 0, ESPEJO: 0}}
    ultima_sinc = dict(d["sincronizaciones"][-1]) if d["sincronizaciones"] else None
    if ultima_sinc:
        ultima_sinc["detalle_errores"] = [legible(x, 400) for x in ultima_sinc.get("detalle_errores") or []]
        for k in ("error_chc", "error_espejo"):
            if ultima_sinc.get(k):
                ultima_sinc[k] = legible(ultima_sinc[k])
    ultima_verif = d["verificaciones"][-1] if d["verificaciones"] else None
    e = estado_actual()
    fuentes = resumen_fuentes()

    def iso(x):
        return x.isoformat() if x else None

    proxima = None
    if d.get("ultima_sincronizacion") and not sin_red():
        proxima = (datetime.fromisoformat(d["ultima_sincronizacion"])
                   + timedelta(seconds=proxima_espera_s())).isoformat(timespec="seconds")
    return {
        "fuente": "chirps_v3",
        "datos_reales": True,
        "modo_sin_red": sin_red(),
        "estado": e["estado"],
        "mensaje_estado": e["mensaje"],
        "origen_actual": e.get("origen"),
        "sincronizacion_activa": sincronizando(),
        "fecha_corte": iso(corte),
        "dias_en_cache": c["final"] + c["preliminar"],
        "dias_finales": c["final"],
        "dias_preliminares": c["preliminar"],
        "dias_faltantes": c["ausente"],
        "dias_por_origen": c["por_origen"],
        "dias_finales_provisionales": c.get("finales_provisionales", 0),
        "ultimo_final": iso(c["ultimo_final"]),
        "primer_preliminar": iso(c["primer_preliminar"]),
        "ultimo_preliminar": iso(c["ultimo_preliminar"]),
        "fuentes": fuentes,
        "disyuntor_chc": fuentes[CHC]["disyuntor"],
        "anios_climatologia": almacen.anios_mensuales(),
        "tamano_cache_bytes": almacen.tamano_bytes(),
        "bytes_descargados": int(d.get("bytes_totales", 0)),
        "ultima_sincronizacion": d.get("ultima_sincronizacion"),
        "resultado_ultima_sincronizacion": ultima_sinc,
        "ultima_verificacion": ultima_verif,
        "disponibilidad_servidor": d.get("disponibilidad", {}),
        "errores_recientes": [{**x, "mensaje": legible(x.get("mensaje"), 400)}
                              for x in d.get("errores_recientes", [])[-10:] if isinstance(x, dict)],
        "proxima_sincronizacion": proxima,
    }
