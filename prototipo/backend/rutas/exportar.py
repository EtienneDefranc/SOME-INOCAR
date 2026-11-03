"""Ruta de exportación (sección 7.4): CSV, PNG o NetCDF de una consulta."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response

from .. import almacen, auth, exportar
from ..esquemas import PeticionExportar

router = APIRouter(tags=["Exportación"])

# Grafía visible de cada formato (la auditoría la muestra tal cual)
ETIQUETA_FORMATO = {"csv": "CSV", "png": "PNG", "netcdf": "NetCDF"}


@router.post("/exportar")
def exportar_consulta(peticion: PeticionExportar, request: Request,
                      usuario: dict = Depends(auth.usuario_actual)):
    """Ejecuta la consulta indicada y devuelve el archivo con `Content-Disposition: attachment`."""
    contenido, nombre, tipo_mime, procedencia = exportar.exportar_con_procedencia(
        peticion.formato, peticion.clase, peticion.consulta)
    almacen.contar_consulta()
    etiqueta = ETIQUETA_FORMATO.get(peticion.formato, peticion.formato.upper())
    almacen.registrar_auditoria(
        usuario["usuario"], "exportacion",
        f"Exportación {etiqueta} de {peticion.clase}: {nombre}", auth.ip_de(request))
    cabeceras = {
        "Content-Disposition": f'attachment; filename="{nombre}"; filename*=UTF-8\'\'{quote(nombre)}',
        "X-SOME-Datos-Simulados": "true" if procedencia["datos_simulados"] else "false",
        "X-SOME-Procedencia": procedencia["procedencia"],
        "Cache-Control": "no-store",
    }
    return Response(content=contenido, media_type=tipo_mime, headers=cabeceras)
