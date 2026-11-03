"""Rutas de la API REST del SOME (prefijo `/api`), una por módulo de la sección 7."""

from fastapi import APIRouter

from . import admin, auth, catalogo, consultas, exportar, panel, reportes, vistas

RUTAS: list[APIRouter] = [
    auth.router,
    catalogo.router,
    consultas.router,
    exportar.router,
    reportes.router,
    panel.router,
    vistas.router,
    admin.router,
]

__all__ = ["RUTAS"]
