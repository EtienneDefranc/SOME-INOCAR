"""Rutas de reportes y trabajos (sección 7.5)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .. import almacen, auth, reportes, trabajos
from ..esquemas import PeticionPlantillaReporte, PeticionReporte, TrabajoCreado

router = APIRouter(tags=["Reportes y trabajos"])


@router.get("/reportes/plantillas")
def listar_plantillas(usuario: dict = Depends(auth.usuario_actual)):
    """Plantillas disponibles con sus parámetros."""
    return reportes.plantillas()


@router.get("/reportes/mis-plantillas")
def listar_mis_plantillas(usuario: dict = Depends(auth.usuario_actual)):
    """Plantillas de reporte personalizado del usuario y las compartidas con su departamento (RF-09.3)."""
    return [almacen.plantilla_reporte_publica(p, usuario) for p in almacen.listar_plantillas_reporte(usuario)]


@router.post("/reportes/mis-plantillas", status_code=201)
def guardar_mis_plantilla(peticion: PeticionPlantillaReporte, request: Request,
                          usuario: dict = Depends(auth.usuario_actual)):
    """Guarda las secciones y los parámetros de un reporte personalizado para volver a usarlos."""
    parametros = dict(peticion.parametros or {})
    reportes.validar_parametros("personalizado", parametros)
    nueva = almacen.crear_plantilla_reporte(usuario, {"nombre": peticion.nombre, "descripcion": peticion.descripcion,
                                                      "compartida": peticion.compartida, "parametros": parametros})
    almacen.registrar_auditoria(usuario["usuario"], "plantilla_reporte_creada",
                                f"«{nueva['nombre']}» (plantilla {nueva['id']}"
                                f"{', compartida con su departamento' if nueva['compartida'] else ''})",
                                auth.ip_de(request))
    return almacen.plantilla_reporte_publica(nueva, usuario)


@router.delete("/reportes/mis-plantillas/{plantilla_id}")
def eliminar_mis_plantilla(plantilla_id: int, request: Request, usuario: dict = Depends(auth.usuario_actual)):
    p = almacen.plantilla_reporte(plantilla_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"No existe la plantilla {plantilla_id}.")
    if str(p.get("propietario_id")) != str(usuario["id"]) and usuario.get("rol") != "administrador":
        raise HTTPException(status_code=403, detail="Solo quien creó la plantilla puede eliminarla.")
    almacen.eliminar_plantilla_reporte(plantilla_id)
    almacen.registrar_auditoria(usuario["usuario"], "plantilla_reporte_eliminada",
                                f"«{p['nombre']}» (plantilla {plantilla_id})", auth.ip_de(request))
    return {"eliminada": True}


@router.post("/reportes", response_model=TrabajoCreado, status_code=202)
def generar_reporte(peticion: PeticionReporte, request: Request, usuario: dict = Depends(auth.usuario_actual)):
    """Encola la generación del PDF en el hilo de trabajos; devuelve el id del trabajo."""
    plantilla = reportes.plantilla(peticion.plantilla)
    if plantilla is None:
        disponibles = ", ".join(p["id"] for p in reportes.plantillas())
        raise HTTPException(status_code=404, detail=f"Plantilla desconocida: «{peticion.plantilla}». "
                                                    f"Disponibles: {disponibles}.")
    parametros = dict(peticion.parametros or {})
    # Valida los parámetros antes de encolar: un periodo fuera de la cobertura de la fuente
    # responde 422 aquí en vez de crear un trabajo que terminará en error.
    reportes.validar_parametros(plantilla["id"], parametros)
    titulo = reportes.titulo_trabajo(plantilla["id"], parametros)
    usuario_pub = almacen.publico(usuario)

    def _generar(trabajo_id, progreso):
        return reportes.generar(plantilla["id"], parametros, usuario_pub, trabajo_id, progreso)

    trabajo = trabajos.encolar("reporte", titulo, usuario, _generar, plantilla=plantilla["id"],
                               parametros=parametros)
    almacen.registrar_auditoria(usuario["usuario"], "reporte_generado",
                                f"{titulo} (trabajo {trabajo['id']})", auth.ip_de(request))
    return {"trabajo_id": trabajo["id"], "estado": trabajo["estado"]}


@router.get("/trabajos")
def listar_trabajos(usuario: dict = Depends(auth.usuario_actual)):
    """Trabajos del usuario (los administradores ven todos), del más reciente al más antiguo."""
    return trabajos.listar(usuario, todos=(usuario.get("rol") == "administrador"))


def _trabajo_autorizado(trabajo_id: int, usuario: dict) -> dict:
    t = trabajos.obtener(trabajo_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"No existe el trabajo {trabajo_id}.")
    if usuario.get("rol") != "administrador" and str(t.get("usuario_id")) != str(usuario["id"]):
        raise HTTPException(status_code=403, detail="Este trabajo pertenece a otro usuario.")
    return t


@router.get("/trabajos/{trabajo_id}")
def obtener_trabajo(trabajo_id: int, usuario: dict = Depends(auth.usuario_actual)):
    return trabajos.publico(_trabajo_autorizado(trabajo_id, usuario))


@router.get("/reportes/{trabajo_id}/descargar")
def descargar_reporte(trabajo_id: int, request: Request, usuario: dict = Depends(auth.usuario_actual)):
    """PDF del reporte. Acepta el *token* en la cabecera o como `?token=` (abrir en pestaña nueva)."""
    t = _trabajo_autorizado(trabajo_id, usuario)
    if t.get("estado") != "listo" or not t.get("ruta"):
        raise HTTPException(status_code=404, detail=f"El reporte del trabajo {trabajo_id} aún no está disponible "
                                                    f"(estado: {t.get('estado')}).")
    ruta = Path(t["ruta"])
    if not ruta.exists():
        raise HTTPException(status_code=404, detail="El archivo PDF ya no existe en el servidor.")
    nombre = t.get("nombre_archivo") or f"SOME_{t.get('plantilla', 'reporte')}_{str(t.get('terminado', ''))[:10].replace('-', '')}.pdf"
    # Si el token llegó por la URL (botón «Ver»), se muestra en la pestaña; si no, se descarga.
    disposicion = "inline" if request.query_params.get("token") else "attachment"
    cabeceras = {"Content-Disposition": f"{disposicion}; filename=\"{nombre}\"; filename*=UTF-8''{quote(nombre)}",
                 "Cache-Control": "no-store"}
    return FileResponse(str(ruta), media_type="application/pdf", headers=cabeceras)
