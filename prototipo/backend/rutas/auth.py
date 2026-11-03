"""Rutas de autenticación (sección 7.1): ingresar, salir y «yo»."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .. import almacen, auth
from ..esquemas import Credenciales, Ok, Sesion, UsuarioPublico

router = APIRouter(prefix="/auth", tags=["Autenticación"])


@router.post("/ingresar", response_model=Sesion)
def ingresar(credenciales: Credenciales, request: Request):
    """Valida usuario y clave; devuelve el *token* y el usuario. 401 si fallan."""
    ip = auth.ip_de(request)
    try:
        token, usuario = auth.ingresar(credenciales.usuario, credenciales.clave)
    except Exception:
        almacen.registrar_auditoria(credenciales.usuario, "ingreso_fallido",
                                    "Intento de inicio de sesión rechazado", ip)
        raise
    almacen.registrar_auditoria(usuario["usuario"], "ingreso", "Inicio de sesión correcto", ip)
    return {"token": token, "usuario": usuario}


@router.post("/salir", response_model=Ok)
def salir(request: Request, usuario: dict = Depends(auth.usuario_actual)):
    """Invalida el *token* actual."""
    auth.salir(getattr(request.state, "token", None))
    almacen.registrar_auditoria(usuario["usuario"], "salida", "Cierre de sesión", auth.ip_de(request))
    return {"ok": True}


@router.get("/yo", response_model=UsuarioPublico)
def yo(usuario: dict = Depends(auth.usuario_actual)):
    """Usuario de la sesión actual."""
    return almacen.publico(usuario)
