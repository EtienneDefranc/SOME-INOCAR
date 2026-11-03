"""Autenticación y autorización del prototipo SOME (RF-01, RNF-05).

Usuarios de demostración con contraseña `some2026`, contraseñas con
`hashlib.sha256` + sal (es una demo), *tokens* opacos guardados en memoria y
las dependencias de FastAPI `usuario_actual` y `requiere_rol(...)`.

El *token* se lee de la cabecera `Authorization: Bearer <token>` y, como
alternativa (para abrir un PDF en una pestaña nueva), del parámetro de
consulta `?token=`.
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime

from fastapi import Depends, HTTPException, Request

from . import almacen

ROLES = ("oceanografo", "analista", "revisor", "administrador")
NOMBRES_ROL = {
    "oceanografo": "Oceanógrafo/a",
    "analista": "Analista de datos",
    "revisor": "Jefe de departamento / revisor",
    "administrador": "Administrador de TI",
}

# token → {usuario_id, usuario, creado, ultimo_uso}
_TOKENS: dict[str, dict] = {}
_BLOQUEO = threading.Lock()


# ---------------------------------------------------------------------------
# Ingreso y salida
# ---------------------------------------------------------------------------
def verificar_clave(usuario: dict, clave: str) -> bool:
    return almacen.hash_clave(clave or "", usuario.get("sal", "")) == usuario.get("clave_hash")


def ingresar(nombre_usuario: str, clave: str) -> tuple[str, dict]:
    """Valida las credenciales y devuelve (token, usuario público). 401 si fallan."""
    usuario = almacen.usuario_por_nombre(nombre_usuario)
    if usuario is None or not verificar_clave(usuario, clave):
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    if not usuario.get("activo", True):
        raise HTTPException(status_code=403, detail="La cuenta está desactivada. Comuníquese con el administrador.")
    token = secrets.token_urlsafe(32)
    ahora = datetime.now().isoformat(timespec="seconds")
    with _BLOQUEO:
        _TOKENS[token] = {"usuario_id": usuario["id"], "usuario": usuario["usuario"],
                          "creado": ahora, "ultimo_uso": ahora}
    return token, almacen.publico(usuario)


def salir(token: str | None) -> None:
    if not token:
        return
    with _BLOQUEO:
        _TOKENS.pop(token, None)


def cerrar_sesiones_de(usuario_id) -> None:
    """Invalida todos los *tokens* de un usuario (al desactivarlo o eliminarlo)."""
    with _BLOQUEO:
        for token in [t for t, s in _TOKENS.items() if str(s["usuario_id"]) == str(usuario_id)]:
            del _TOKENS[token]


def usuarios_activos() -> int:
    """Usuarios distintos con sesión vigente."""
    with _BLOQUEO:
        return len({s["usuario_id"] for s in _TOKENS.values()})


def sesiones_activas() -> int:
    with _BLOQUEO:
        return len(_TOKENS)


# ---------------------------------------------------------------------------
# Dependencias de FastAPI
# ---------------------------------------------------------------------------
def token_de(request: Request) -> str | None:
    """Token de la cabecera Authorization (Bearer) o del parámetro ?token=."""
    cabecera = request.headers.get("authorization") or ""
    if cabecera.lower().startswith("bearer "):
        valor = cabecera[7:].strip()
        if valor:
            return valor
    return request.query_params.get("token") or None


def ip_de(request: Request) -> str:
    cliente = request.client
    return cliente.host if cliente else "127.0.0.1"


def usuario_actual(request: Request) -> dict:
    """Usuario autenticado (diccionario completo del almacén). 401 si no hay sesión."""
    token = token_de(request)
    if not token:
        raise HTTPException(status_code=401, detail="Se requiere iniciar sesión (cabecera Authorization: Bearer <token>).")
    with _BLOQUEO:
        sesion = _TOKENS.get(token)
        if sesion is not None:
            sesion["ultimo_uso"] = datetime.now().isoformat(timespec="seconds")
    if sesion is None:
        raise HTTPException(status_code=401, detail="La sesión no es válida o ha expirado. Ingrese nuevamente.")
    usuario = almacen.usuario_por_id(sesion["usuario_id"])
    if usuario is None or not usuario.get("activo", True):
        salir(token)
        raise HTTPException(status_code=401, detail="La cuenta ya no está disponible. Ingrese nuevamente.")
    request.state.token = token
    request.state.usuario = usuario
    return usuario


def requiere_rol(*roles: str):
    """Dependencia que exige uno de los roles indicados (403 si no lo tiene)."""
    permitidos = set(roles)

    def _dependencia(usuario: dict = Depends(usuario_actual)) -> dict:
        if usuario.get("rol") not in permitidos:
            nombres = ", ".join(NOMBRES_ROL.get(r, r) for r in roles)
            raise HTTPException(status_code=403,
                                detail=f"Esta acción requiere el rol: {nombres}.")
        return usuario

    return _dependencia


requiere_administrador = requiere_rol("administrador")
