"""Rutas de consultas (sección 7.3). Todas `POST` con cuerpo JSON.

Cada consulta se cuenta para las métricas del sistema y su resultado se guarda
en `cache.cache_consultas` (clave = cuerpo normalizado más, si usa CHIRPS v3,
la firma de su caché local; ver `cache.consultar`), de modo que repetir una
consulta responde al instante y deja de hacerlo en cuanto cambian los datos. Los errores de validación llegan como
`motor.ErrorConsulta` y el manejador global de `app.py` los convierte en
`{"detalle": ...}` con el código adecuado.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends

from .. import almacen, auth, cache, motor
from ..esquemas import (ConsultaClimatologia, ConsultaComparar, ConsultaCortina, ConsultaMapa, ConsultaMismoMes,
                        ConsultaPerfil, ConsultaSerie, ConsultaVectores)

router = APIRouter(prefix="/consultas", tags=["Consultas"],
                   dependencies=[Depends(auth.usuario_actual)])


def ejecutar(nombre: str, funcion: Callable[[dict], dict], cuerpo: dict) -> dict:
    """Cuenta la consulta y la resuelve desde la caché o con el motor."""
    almacen.contar_consulta()
    return cache.consultar(nombre, cuerpo, lambda: funcion(cuerpo))


@router.post("/mapa")
def mapa(c: ConsultaMapa):
    """Campo de un día o promedio de un periodo, en valor o anomalía, con PNG para Leaflet."""
    return ejecutar("mapa", motor.consulta_mapa, c.como_dict())


@router.post("/serie")
def serie(c: ConsultaSerie):
    """Serie del promedio de área con climatología, anomalías y resumen en lenguaje sencillo."""
    return ejecutar("serie", motor.consulta_serie, c.como_dict())


@router.post("/climatologia")
def climatologia(c: ConsultaClimatologia):
    """Climatología mensual (12 valores) del promedio de área."""
    return ejecutar("climatologia", motor.consulta_climatologia, c.como_dict())


@router.post("/mismo-mes")
def mismo_mes(c: ConsultaMismoMes):
    """Promedio del mismo mes en cada año con datos, frente a la climatología del mes."""
    return ejecutar("mismo-mes", motor.consulta_mismo_mes, c.como_dict())


@router.post("/perfil")
def perfil(c: ConsultaPerfil):
    """Perfil vertical (0–100 m) en un punto y una fecha."""
    return ejecutar("perfil", motor.consulta_perfil, c.como_dict())


@router.post("/cortina")
def cortina(c: ConsultaCortina):
    """Cortina profundidad-tiempo en un punto."""
    return ejecutar("cortina", motor.consulta_cortina, c.como_dict())


@router.post("/comparar")
def comparar(c: ConsultaComparar):
    """Dos fuentes o dos periodos: series, diferencia media, RMSE y correlación."""
    return ejecutar("comparar", motor.consulta_comparar, c.como_dict())


@router.post("/vectores")
def vectores(c: ConsultaVectores):
    """Componentes u, v y magnitud de la corriente, submuestreadas para dibujar flechas."""
    return ejecutar("vectores", motor.consulta_vectores, c.como_dict())
