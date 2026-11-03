"""Rutas del catálogo y del estado de las fuentes (sección 7.2) y `GET /api/salud` (pública)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import auth, catalogo
from .. import configuracion as cfg
from ..esquemas import Salud

router = APIRouter(tags=["Catálogo"])


@router.get("/catalogo")
def obtener_catalogo(usuario: dict = Depends(auth.usuario_actual)):
    """Fuentes, variables, recortes, periodos de referencia, profundidades y fechas de la demo."""
    return catalogo.catalogo_json()


@router.get("/fuentes/estado")
def estado_fuentes(usuario: dict = Depends(auth.usuario_actual)):
    """Estado, fecha de corte, latencia y historial de 14 días de cada fuente (RF-21).

    Para las tres fuentes reales el estado y el historial salen de las verificaciones reales persistidas;
    los días sin verificación llegan como «sin_registro». CHIRPS añade sus caminos (`caminos`, `cache`);
    Copernicus, el bloque `copernicus` (modo, acceso, credenciales, servicio, último día de análisis,
    climatología y caché); NOAA OISST, el bloque `noaa` (modo, acceso, servidores NOAA PSL y NOAA NCEI,
    publicación, climatologías y caché con días finales, preliminares y faltantes)."""
    return catalogo.estado_fuentes()


@router.get("/salud", response_model=Salud)
def salud():
    """Comprobación pública de vida del servicio, fecha de corte del sistema y procedencia de los datos del
    catálogo (`datos_simulados` es false con las tres fuentes reales)."""
    procedencia = catalogo.procedencia_catalogo()
    return {"estado": "ok", "version": cfg.VERSION, "fecha_corte": catalogo.fecha_corte_sistema().isoformat(),
            "datos_simulados": procedencia["datos_simulados"], "procedencia": procedencia["procedencia"],
            "fuentes_reales": catalogo.fuentes_reales()}
