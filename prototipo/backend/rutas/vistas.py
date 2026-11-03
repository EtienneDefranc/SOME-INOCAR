"""Rutas de «Mis vistas» (sección 7.7, RF-16): privadas por defecto, compartidas en solo lectura."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import almacen, auth
from ..esquemas import VistaEntrada

router = APIRouter(prefix="/vistas", tags=["Mis vistas"])


def _es_propietario(v: dict, usuario: dict) -> bool:
    return str(v.get("propietario_id")) == str(usuario["id"])


@router.get("")
def listar_vistas(usuario: dict = Depends(auth.usuario_actual)):
    """`{propias, compartidas}`: las del usuario y las que otros comparten."""
    propias, compartidas = [], []
    for v in sorted(almacen.listar_vistas(), key=lambda x: x.get("actualizada") or "", reverse=True):
        if _es_propietario(v, usuario):
            propias.append(almacen.vista_publica(v))
        elif v.get("compartida"):
            compartidas.append(almacen.vista_publica(v))
    return {"propias": propias, "compartidas": compartidas}


@router.post("", status_code=201)
def crear_vista(entrada: VistaEntrada, request: Request, usuario: dict = Depends(auth.usuario_actual)):
    v = almacen.crear_vista(usuario, entrada.model_dump())
    if v["compartida"]:
        almacen.registrar_auditoria(usuario["usuario"], "vista_compartida",
                                    f"Vista «{v['nombre']}» compartida", auth.ip_de(request))
    return almacen.vista_publica(v)


@router.get("/{vista_id}")
def obtener_vista(vista_id: int, usuario: dict = Depends(auth.usuario_actual)):
    """Solo el propietario o, si está compartida, cualquier usuario."""
    v = almacen.vista(vista_id)
    if v is None:
        raise HTTPException(status_code=404, detail=f"No existe la vista {vista_id}.")
    if not _es_propietario(v, usuario) and not v.get("compartida"):
        raise HTTPException(status_code=403, detail="Esta vista es privada de otro usuario.")
    return almacen.vista_publica(v)


@router.put("/{vista_id}")
def actualizar_vista(vista_id: int, entrada: VistaEntrada, request: Request,
                     usuario: dict = Depends(auth.usuario_actual)):
    """Solo el propietario puede editarla (las compartidas son de solo lectura para los demás)."""
    v = almacen.vista(vista_id)
    if v is None:
        raise HTTPException(status_code=404, detail=f"No existe la vista {vista_id}.")
    if not _es_propietario(v, usuario):
        raise HTTPException(status_code=403, detail="Solo el propietario puede modificar esta vista.")
    antes = bool(v.get("compartida"))
    v = almacen.actualizar_vista(vista_id, entrada.model_dump())
    if bool(v.get("compartida")) != antes:
        accion = "vista_compartida" if v["compartida"] else "vista_privada"
        detalle = (f"Vista «{v['nombre']}» compartida" if v["compartida"]
                   else f"Vista «{v['nombre']}» dejó de compartirse")
        almacen.registrar_auditoria(usuario["usuario"], accion, detalle, auth.ip_de(request))
    return almacen.vista_publica(v)


@router.delete("/{vista_id}")
def eliminar_vista(vista_id: int, usuario: dict = Depends(auth.usuario_actual)):
    v = almacen.vista(vista_id)
    if v is None:
        raise HTTPException(status_code=404, detail=f"No existe la vista {vista_id}.")
    if not _es_propietario(v, usuario):
        raise HTTPException(status_code=403, detail="Solo el propietario puede eliminar esta vista.")
    almacen.eliminar_vista(vista_id)
    return {"ok": True}
