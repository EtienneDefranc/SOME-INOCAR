"""Rutas de administración (sección 7.8); exigen el rol `administrador`.

`GET /admin/sistema` incluye el resumen de la caché y la sincronización de las fuentes reales:
`chirps` (CHIRPS v3), `copernicus` (Copernicus Marine) y `noaa` (NOAA OISST), cada uno None si su paquete
no está disponible, y la fecha de corte del sistema (`catalogo.fecha_corte_sistema()`).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import almacen, auth, cache, catalogo
from .. import configuracion as cfg
from .. import trabajos
from ..esquemas import Habilitacion, UsuarioEdicion, UsuarioEntrada

router = APIRouter(prefix="/admin", tags=["Administración"],
                   dependencies=[Depends(auth.requiere_administrador)])


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
@router.get("/usuarios")
def listar_usuarios():
    return [almacen.publico(u) for u in almacen.listar_usuarios()]


@router.post("/usuarios", status_code=201)
def crear_usuario(entrada: UsuarioEntrada, request: Request, admin: dict = Depends(auth.requiere_administrador)):
    if almacen.usuario_por_nombre(entrada.usuario) is not None:
        raise HTTPException(status_code=422, detail=f"Ya existe un usuario con el nombre «{entrada.usuario}».")
    if not entrada.clave:
        raise HTTPException(status_code=422, detail="Indique una clave inicial para el nuevo usuario.")
    datos = entrada.model_dump(exclude={"clave"})
    u = almacen.crear_usuario(datos, entrada.clave)
    almacen.registrar_auditoria(admin["usuario"], "usuario_creado",
                                f"Se creó el usuario «{u['usuario']}» ({auth.NOMBRES_ROL.get(u['rol'], u['rol'])})",
                                auth.ip_de(request))
    return almacen.publico(u)


@router.put("/usuarios/{usuario_id}")
def editar_usuario(usuario_id: int, entrada: UsuarioEdicion, request: Request,
                   admin: dict = Depends(auth.requiere_administrador)):
    existente = almacen.usuario_por_id(usuario_id)
    if existente is None:
        raise HTTPException(status_code=404, detail=f"No existe el usuario {usuario_id}.")
    if entrada.usuario and entrada.usuario.lower() != existente["usuario"].lower():
        if almacen.usuario_por_nombre(entrada.usuario) is not None:
            raise HTTPException(status_code=422, detail=f"Ya existe un usuario con el nombre «{entrada.usuario}».")
    if str(existente["id"]) == str(admin["id"]):
        if entrada.activo is False:
            raise HTTPException(status_code=400, detail="No puede desactivar su propia cuenta.")
        if entrada.rol and entrada.rol != "administrador":
            raise HTTPException(status_code=400, detail="No puede quitarse a sí mismo el rol de administrador.")
    datos = entrada.model_dump(exclude={"clave"}, exclude_none=True)
    # Solo se anotan en la auditoría los campos que realmente cambiaron (el formulario envía todos)
    cambiados = sorted(k for k, v in datos.items() if existente.get(k) != v)
    if entrada.clave:
        cambiados.append("clave")
    u = almacen.actualizar_usuario(usuario_id, datos, entrada.clave)
    if entrada.activo is False:
        auth.cerrar_sesiones_de(usuario_id)
    if cambiados == ["activo"]:
        detalle = f"Se {'desactivó' if entrada.activo is False else 'activó'} la cuenta de «{u['usuario']}»"
    elif cambiados:
        detalle = f"Se actualizó el usuario «{u['usuario']}» ({', '.join(cambiados)})"
    else:
        detalle = f"Se guardó el usuario «{u['usuario']}» sin cambios"
    almacen.registrar_auditoria(admin["usuario"], "usuario_editado", detalle, auth.ip_de(request))
    return almacen.publico(u)


@router.delete("/usuarios/{usuario_id}")
def eliminar_usuario(usuario_id: int, request: Request, admin: dict = Depends(auth.requiere_administrador)):
    existente = almacen.usuario_por_id(usuario_id)
    if existente is None:
        raise HTTPException(status_code=404, detail=f"No existe el usuario {usuario_id}.")
    if str(existente["id"]) == str(admin["id"]):
        raise HTTPException(status_code=400, detail="No puede eliminar su propia cuenta de administrador.")
    auth.cerrar_sesiones_de(usuario_id)
    almacen.eliminar_usuario(usuario_id)
    almacen.registrar_auditoria(admin["usuario"], "usuario_eliminado",
                                f"Se eliminó el usuario «{existente['usuario']}»", auth.ip_de(request))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Catálogo (RF-20)
# ---------------------------------------------------------------------------
@router.put("/catalogo/fuentes/{fuente}")
def habilitar_fuente(fuente: str, entrada: Habilitacion, request: Request,
                     admin: dict = Depends(auth.requiere_administrador)):
    if not catalogo.existe_fuente(fuente):
        raise HTTPException(status_code=404, detail=f"Fuente desconocida: «{fuente}».")
    resultado = catalogo.establecer_fuente_habilitada(fuente, entrada.habilitada)
    almacen.guardar_habilitacion_fuente(fuente, entrada.habilitada)
    cache.cache_consultas.limpiar()
    almacen.registrar_auditoria(admin["usuario"], "catalogo_cambiado",
                                f"Fuente {catalogo.nombre_fuente(fuente)} "
                                f"{'habilitada' if entrada.habilitada else 'deshabilitada'}", auth.ip_de(request))
    return resultado


@router.put("/catalogo/variables/{fuente}/{variable}")
def habilitar_variable(fuente: str, variable: str, entrada: Habilitacion, request: Request,
                       admin: dict = Depends(auth.requiere_administrador)):
    if not catalogo.existe_fuente(fuente):
        raise HTTPException(status_code=404, detail=f"Fuente desconocida: «{fuente}».")
    try:
        resultado = catalogo.establecer_variable_habilitada(fuente, variable, entrada.habilitada)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e.args[0]) if e.args else "Variable desconocida.") from None
    almacen.guardar_habilitacion_variable(fuente, variable, entrada.habilitada)
    cache.cache_consultas.limpiar()
    almacen.registrar_auditoria(admin["usuario"], "catalogo_cambiado",
                                f"Variable {fuente}/{variable} "
                                f"{'habilitada' if entrada.habilitada else 'deshabilitada'}", auth.ip_de(request))
    return resultado


# ---------------------------------------------------------------------------
# Auditoría y sistema
# ---------------------------------------------------------------------------
@router.get("/auditoria")
def auditoria(limite: int = Query(200, ge=1, le=5000)):
    return almacen.listar_auditoria(limite)


@router.get("/sistema")
def sistema():
    metricas = almacen.metricas_consultas()
    estadisticas = cache.estadisticas()
    inicio = almacen.INICIO_SERVIDOR
    procedencia = catalogo.procedencia_catalogo()
    return {
        "version": cfg.VERSION,
        "inicio": inicio.isoformat(timespec="seconds"),
        "tiempo_activo_s": int((datetime.now() - inicio).total_seconds()),
        "usuarios_activos": auth.usuarios_activos(),
        "sesiones_activas": auth.sesiones_activas(),
        "usuarios_registrados": len(almacen.listar_usuarios()),
        "consultas_total": metricas["consultas_total"],
        "consultas_hoy": metricas["consultas_hoy"],
        "cache": {"entradas": estadisticas["entradas"], "aciertos": estadisticas["aciertos"],
                  "fallos": estadisticas["fallos"], "consultas": estadisticas["consultas"],
                  "campos": estadisticas["campos"]},
        "trabajos": trabajos.resumen(),
        "ruta_datos": str(almacen.ruta_datos()),
        "fecha_corte": catalogo.fecha_corte_sistema().isoformat(),
        **procedencia,
        **catalogo.insignias_cabecera(procedencia),
        "fuentes_reales": catalogo.fuentes_reales(),
        "chirps": _resumen_chirps(),
        "copernicus": _resumen_copernicus(),
        "noaa": _resumen_noaa(),
    }


def _resumen_chirps() -> dict | None:
    """Resumen de la caché y la sincronización de CHIRPS v3 (None si el paquete no está disponible)."""
    try:
        from .. import chirps  # importación tolerante: el conector real puede no estar instalado
    except ImportError:
        return None
    resumen = getattr(chirps, "resumen", None)
    if not callable(resumen):
        return None
    try:
        return resumen()
    except Exception as e:  # noqa: BLE001 - la pantalla de sistema no debe fallar por esto
        return {"error": f"No se pudo leer el resumen de CHIRPS: {e}"}


def _resumen_copernicus() -> dict | None:
    """Resumen de la caché y la sincronización de Copernicus Marine (tarjeta «Copernicus Marine · caché y
    sincronización»); None si el paquete backend.copernicus no está disponible. No toca la red."""
    try:
        from .. import copernicus  # importación tolerante (no importa el toolbox copernicusmarine)
    except Exception:  # noqa: BLE001
        return None
    resumen = getattr(copernicus, "resumen", None)
    if not callable(resumen):
        return None
    try:
        return resumen()
    except Exception as e:  # noqa: BLE001 - la pantalla de sistema no debe fallar por esto
        return {"error": f"No se pudo leer el resumen de Copernicus Marine: {e}"}


def _resumen_noaa() -> dict | None:
    """Resumen de la caché y la sincronización de NOAA OISST (tarjeta «NOAA OISST · caché y sincronización»:
    modo, estado, fecha de corte, días finales, preliminares y faltantes, servidores PSL y NCEI, climatologías,
    tamaño, última sincronización y errores); None si el paquete backend.noaa no está disponible. No toca la red."""
    try:
        from .. import noaa  # importación tolerante
    except Exception:  # noqa: BLE001
        return None
    resumen = getattr(noaa, "resumen", None)
    if not callable(resumen):
        return None
    try:
        return resumen()
    except Exception as e:  # noqa: BLE001 - la pantalla de sistema no debe fallar por esto
        return {"error": f"No se pudo leer el resumen de NOAA OISST: {e}"}
