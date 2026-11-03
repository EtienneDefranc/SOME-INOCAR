"""Aplicación FastAPI del prototipo SOME.

Monta la API REST bajo `/api` (sección 7 de la especificación) y sirve la
interfaz (`frontend/`) como archivos estáticos en `/`; cualquier ruta que no
sea de la API devuelve `index.html` (aplicación de una sola página).

Arranque: `python -m uvicorn backend.app:app --host 127.0.0.1 --port 8050`
(o `ejecutar.ps1` / `ejecutar.bat`). Documentación interactiva en `/api/docs`.
"""

from __future__ import annotations

import sys
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import almacen, catalogo
from . import configuracion as cfg
from . import motor, reportes, trabajos
from .rutas import RUTAS

# Sincronización de CHIRPS v3, Copernicus Marine y NOAA OISST (conectores reales). Importación tolerante:
# si un paquete no está disponible, la aplicación arranca igual y el conector trabaja solo con lo que tenga
# (Copernicus Marine, sin su paquete, sirve el simulador y lo dice). Importar backend.copernicus no
# importa el toolbox copernicusmarine (tarda ~4 s): eso ocurre solo al descargar, en segundo plano.
# Importar backend.noaa no toca la red.
try:
    from . import chirps as _chirps
except ImportError as _e:  # pragma: no cover - depende de la instalación
    _chirps = None
    print(f"[SOME] Aviso: no se pudo importar backend.chirps ({_e}); "
          "la sincronización automática de CHIRPS v3 queda desactivada.")
try:
    from . import copernicus as _copernicus
except Exception as _e:  # noqa: BLE001 - pragma: no cover - depende de la instalación
    _copernicus = None
    print(f"[SOME] Aviso: no se pudo importar backend.copernicus ({_e}); "
          "la sincronización automática de Copernicus Marine queda desactivada.")
try:
    from . import noaa as _noaa
except Exception as _e:  # noqa: BLE001 - pragma: no cover - depende de la instalación
    _noaa = None
    print(f"[SOME] Aviso: no se pudo importar backend.noaa ({_e}); "
          "la sincronización automática de NOAA OISST queda desactivada.")


def _sincronizacion(paquete, nombre: str, accion: str) -> None:
    """Llama a iniciar_sincronizacion/detener_sincronizacion de un paquete sin tumbar la aplicación si fallan."""
    if paquete is None:
        return
    funcion = getattr(paquete, accion, None)
    if not callable(funcion):
        print(f"[SOME] Aviso: el paquete de {nombre} no expone {accion}().")
        return
    try:
        funcion()
    except Exception as e:  # noqa: BLE001 - la demo debe arrancar aunque la red falle
        print(f"[SOME] Aviso: {accion}() de {nombre} falló: {e}")


def _sincronizacion_chirps(accion: str) -> None:
    _sincronizacion(_chirps, "CHIRPS v3", accion)


def _sincronizacion_copernicus(accion: str) -> None:
    _sincronizacion(_copernicus, "Copernicus Marine", accion)


def _sincronizacion_noaa(accion: str) -> None:
    _sincronizacion(_noaa, "NOAA OISST", accion)


def _texto_copernicus() -> str:
    """Una línea para la consola: si Copernicus Marine sirve datos reales o el simulador, y por qué."""
    if _copernicus is None:
        return "Copernicus Marine: datos simulados (no se pudo importar backend.copernicus)."
    try:
        if _copernicus.modo() == "real":
            motivo = _copernicus.motivo_sin_acceso()
            corte = _copernicus.fecha_corte()
            # El motivo va tras dos puntos y no entre paréntesis: puede traer los suyos (ROB-03)
            return ("Copernicus Marine: datos reales"
                    + (f" (último día de análisis servible: {cfg.fmt_fecha(corte)})" if corte else "")
                    + (f"; sin acceso ahora, se sirve la caché local: {motivo}." if motivo else "."))
        motivo = _copernicus.motivo_sin_acceso() or "modo simulado"
        return f"Copernicus Marine: datos simulados; {motivo}."
    except Exception as e:  # noqa: BLE001 - solo informativo
        return f"Copernicus Marine: no se pudo leer su modo ({e})."


def _texto_noaa() -> str:
    """Una línea para la consola: si NOAA OISST sirve datos reales (hasta qué día y desde cuándo son
    preliminares) o el simulador, y por qué."""
    if _noaa is None:
        return "NOAA OISST: datos simulados (no se pudo importar backend.noaa)."
    try:
        if _noaa.modo() == "real":
            motivo = _noaa.motivo_sin_acceso()
            corte = _noaa.fecha_corte()
            preliminares = ""
            if corte:
                from .noaa import almacen as _almacen_noaa
                primer = _almacen_noaa.conteo().get("primer_preliminar")
                if primer:
                    preliminares = f" (preliminares desde el {cfg.fmt_fecha(primer)})"
            # El motivo va tras dos puntos y no entre paréntesis: puede traer los suyos (ROB-03)
            if _noaa.sin_red():
                cola = "; modo sin red (SOME_NOAA_SIN_RED=1): solo la caché local."
            elif motivo:
                cola = f"; sin acceso ahora, se sirve la caché local: {motivo}."
            elif _noaa.sin_sincronizacion():
                cola = "; sincronización en segundo plano desactivada (SOME_NOAA_SIN_SINCRONIZACION=1)."
            else:
                cola = "; se sincroniza con NOAA PSL cada 6 h."
            return ("NOAA OISST: datos reales"
                    + (f" hasta el {cfg.fmt_fecha(corte)}{preliminares}" if corte else "") + cola)
        motivo = _noaa.motivo_sin_acceso() or "sin red ni caché local"
        return f"NOAA OISST: datos simulados; {motivo}."
    except Exception as e:  # noqa: BLE001 - solo informativo
        return f"NOAA OISST: no se pudo leer su modo ({e})."


_NOMBRES_CAMPO = {
    "missing": "es obligatorio",
    "string_type": "debe ser un texto",
    "float_type": "debe ser un número",
    "float_parsing": "debe ser un número",
    "int_type": "debe ser un entero",
    "int_parsing": "debe ser un entero",
    "bool_type": "debe ser verdadero o falso",
    "dict_type": "debe ser un objeto",
    "list_type": "debe ser una lista",
    "literal_error": "tiene un valor no permitido",
    "json_invalid": "el cuerpo no es JSON válido",
    "model_attributes_type": "debe ser un objeto",
}


# ---------------------------------------------------------------------------
# Ciclo de vida: datos iniciales y trabajos de ejemplo (sección 7.9)
# ---------------------------------------------------------------------------
# Horas fijas (creado, terminado) de los trabajos de ejemplo: los reconocen en instalaciones anteriores a la
# marca «ejemplo» de trabajos.encolar.
_HORAS_EJEMPLO = {"boletin_erfen": ("09:40:00", "09:41:12"), "informe_variable": ("11:50:00", "11:50:58")}


def _encolar_trabajos_iniciales(regenerar: dict[str, int] | None = None) -> None:
    """Dos trabajos «listos» para que la demo no empiece vacía: se generan de verdad en segundo plano.

    Con `regenerar` ({plantilla: id}) solo se generan esos, con el mismo id (el registro y el PDF se reemplazan)."""
    mvera = almacen.usuario_por_nombre("mvera")
    if mvera is None:
        return
    usuario = almacen.publico(mvera)
    regenerar_ids = regenerar or {}
    # Los dos trabajos se retrodatan; el PDF se genera con esa misma fecha (portada, pie y nombre
    # del archivo) para que la lista de trabajos y el documento sean coherentes. Sus datos terminan el
    # día anterior al de su generación (NOAA publica el día D el D+1), sin pasar de la fecha de corte del
    # sistema (con NOAA OISST real, el último día de su caché): así el periodo siempre tiene dato real.
    corte = catalogo.fecha_corte_sistema()
    if regenerar is None or "boletin_erfen" in regenerar_ids:
        hace_15 = datetime.now() - timedelta(days=15)
        fin_boletin = max(cfg.FECHA_INICIO + timedelta(days=14), min(corte, hace_15.date() - timedelta(days=1)))
        parametros_boletin = {
            "periodo": {"inicio": (fin_boletin - timedelta(days=14)).isoformat(), "fin": fin_boletin.isoformat()},
            "recorte": "nino12", "incluir_precipitacion": True, "autor": usuario["nombre"],
        }
        terminado_boletin = hace_15.replace(hour=9, minute=41, second=12, microsecond=0)
        trabajos.encolar(
            "reporte", reportes.titulo_trabajo("boletin_erfen", parametros_boletin), mvera,
            lambda tid, prog: reportes.generar("boletin_erfen", parametros_boletin, usuario, tid, prog,
                                               generado=terminado_boletin),
            plantilla="boletin_erfen", parametros=parametros_boletin,
            creado=hace_15.replace(hour=9, minute=40, second=0, microsecond=0).isoformat(timespec="seconds"),
            terminado=terminado_boletin.isoformat(timespec="seconds"),
            trabajo_id=regenerar_ids.get("boletin_erfen"), ejemplo=True)

    if regenerar is None or "informe_variable" in regenerar_ids:
        hace_3 = datetime.now() - timedelta(days=3)
        fin_informe = max(cfg.FECHA_INICIO + timedelta(days=29), min(corte, hace_3.date() - timedelta(days=1)))
        parametros_informe = {
            "fuente": "noaa_oisst", "variable": "sst", "recorte": "nino12",
            "periodo": {"inicio": (fin_informe - timedelta(days=29)).isoformat(),
                        "fin": fin_informe.isoformat()},
            "profundidad": "0", "periodo_referencia": "1991-2020", "autor": usuario["nombre"],
        }
        terminado_informe = hace_3.replace(hour=11, minute=50, second=58, microsecond=0)
        trabajos.encolar(
            "reporte", reportes.titulo_trabajo("informe_variable", parametros_informe), mvera,
            lambda tid, prog: reportes.generar("informe_variable", parametros_informe, usuario, tid, prog,
                                               generado=terminado_informe),
            plantilla="informe_variable", parametros=parametros_informe,
            creado=hace_3.replace(hour=11, minute=50, second=0, microsecond=0).isoformat(timespec="seconds"),
            terminado=terminado_informe.isoformat(timespec="seconds"),
            trabajo_id=regenerar_ids.get("informe_variable"), ejemplo=True)


def _es_trabajo_de_ejemplo(t: dict) -> bool:
    """True si el trabajo es uno de los dos de ejemplo del arranque (marca «ejemplo» o, en instalaciones
    anteriores a ella, usuario mvera con las horas fijas de `_encolar_trabajos_iniciales`)."""
    if t.get("ejemplo"):
        return True
    horas = _HORAS_EJEMPLO.get(t.get("plantilla") or "")
    return bool(horas and t.get("usuario") == "mvera" and str(t.get("creado") or "")[11:19] == horas[0]
                and str(t.get("terminado") or "")[11:19] == horas[1])


def _ejemplos_a_regenerar() -> dict[str, int]:
    """{plantilla: id} de los trabajos de ejemplo que se generaron cuando alguna fuente era simulada (o que
    fallaron), si ahora las tres fuentes son reales: se regeneran con el mismo id para que la demo los muestre
    con datos reales y fechas coherentes con la fecha de corte del sistema. Vacío si no hay nada que hacer."""
    try:
        if catalogo.procedencia_catalogo()["procedencia"] != "real":
            return {}
        salida: dict[str, int] = {}
        for t in sorted(almacen.listar_trabajos(), key=lambda t: int(t.get("id") or 0)):
            plantilla = t.get("plantilla") or ""
            if plantilla in salida or not _es_trabajo_de_ejemplo(t):
                continue
            if t.get("estado") == "error" or (t.get("estado") == "listo" and t.get("procedencia") != "real"):
                salida[plantilla] = int(t["id"])
        return salida
    except Exception as e:  # noqa: BLE001 - la demo debe arrancar aunque esto falle
        print(f"[SOME] Aviso: no se pudo revisar los trabajos de ejemplo: {e}")
        return {}


def _anfitrion_y_puerto() -> str:
    """Anfitrión y puerto reales del arranque (`--host`/`--port` de uvicorn) o los de configuración."""
    argumentos = sys.argv[1:]

    def valor(opcion: str, por_defecto: str) -> str:
        for i, a in enumerate(argumentos):
            if a == opcion and i + 1 < len(argumentos):
                return argumentos[i + 1]
            if a.startswith(opcion + "="):
                return a.split("=", 1)[1]
        return por_defecto
    return f"{valor('--host', str(cfg.HOST))}:{valor('--port', str(cfg.PUERTO))}"


@asynccontextmanager
async def ciclo_de_vida(aplicacion: FastAPI):
    primera_vez = almacen.inicializar()
    habilitaciones = almacen.habilitaciones()
    catalogo.aplicar_habilitaciones(habilitaciones.get("fuentes"), habilitaciones.get("variables"))
    trabajos.iniciar()
    # Los dos trabajos de ejemplo se crean al primer arranque y también si la lista quedó vacía
    # (p. ej., al borrar datos/trabajos.json y datos/reportes/ para regenerar los PDF de muestra).
    if primera_vez or not almacen.listar_trabajos():
        if not primera_vez:
            almacen.reiniciar_contador("trabajos")
        _encolar_trabajos_iniciales()
    else:
        # Si los de ejemplo se generaron con alguna fuente simulada y ahora las tres son reales, se regeneran
        regenerar = _ejemplos_a_regenerar()
        if regenerar:
            print(f"[SOME] Los trabajos de ejemplo {' y '.join(str(i) for i in sorted(regenerar.values()))} se "
                  "generaron con datos simulados o mixtos (o fallaron): se regeneran ahora con datos reales.")
            _encolar_trabajos_iniciales(regenerar)
    _sincronizacion_chirps("iniciar_sincronizacion")
    _sincronizacion_copernicus("iniciar_sincronizacion")
    _sincronizacion_noaa("iniciar_sincronizacion")
    texto = catalogo.insignias_cabecera()["insignia_cabecera"]
    print(f"SOME {cfg.VERSION} listo en http://{_anfitrion_y_puerto()}/ — {texto}. "
          f"Fecha de corte: {cfg.fmt_fecha(catalogo.fecha_corte_sistema())}. Datos en {almacen.ruta_datos()}")
    print(f"[SOME] {_texto_noaa()}")
    print(f"[SOME] {_texto_copernicus()}")
    yield
    _sincronizacion_noaa("detener_sincronizacion")
    _sincronizacion_copernicus("detener_sincronizacion")
    _sincronizacion_chirps("detener_sincronizacion")
    trabajos.detener()


app = FastAPI(
    title="SOME — Sistema de Observación del Mar Equinoccial (prototipo)",
    description=("API REST del prototipo demostrativo para el INOCAR. Las tres fuentes son reales. La temperatura "
                 "superficial del mar proviene de NOAA OISST v2.1 (NOAA/NCEI), leída del servicio NCSS de NOAA PSL "
                 "(respaldo: archivos diarios de NOAA NCEI) y guardada en una caché local; con ella se calcula el "
                 "índice Niño 1+2 en la región oficial, y sus últimos ~15 días son preliminares. Las variables "
                 "físicas del océano provienen del análisis diario de Copernicus Marine (E.U. Copernicus Marine "
                 "Service Information), leído con el toolbox oficial y guardado en una caché local. La "
                 "precipitación proviene de CHIRPS v3 (Climate Hazards Center, UCSB). Sin acceso a NOAA o a "
                 "Copernicus Marine y sin el dato en su caché, el conector responde con el simulador y lo señala; "
                 "nunca mezcla datos simulados y reales en un mismo conjunto. Cada respuesta con datos indica su "
                 "procedencia (real, simulado o mixto) y, si corresponde, sus avisos."),
    version=cfg.VERSION,
    lifespan=ciclo_de_vida,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

for ruta in RUTAS:
    app.include_router(ruta, prefix="/api")


# ---------------------------------------------------------------------------
# Manejadores de errores: siempre {"detalle": "..."} en español
# ---------------------------------------------------------------------------
def _respuesta_error(codigo: int, detalle: str) -> JSONResponse:
    return JSONResponse(status_code=codigo, content={"detalle": detalle})


@app.exception_handler(motor.ErrorConsulta)
async def _error_consulta(request: Request, e: motor.ErrorConsulta):
    return _respuesta_error(e.codigo, e.detalle)


@app.exception_handler(StarletteHTTPException)
async def _error_http(request: Request, e: StarletteHTTPException):
    detalle = e.detail if isinstance(e.detail, str) else str(e.detail)
    if e.status_code == 404 and detalle == "Not Found":
        detalle = "Ruta no encontrada."
    elif e.status_code == 405:
        detalle = "Método no permitido para esta ruta."
    cabeceras = dict(e.headers) if getattr(e, "headers", None) else None
    return JSONResponse(status_code=e.status_code, content={"detalle": detalle}, headers=cabeceras)


@app.exception_handler(RequestValidationError)
async def _error_validacion(request: Request, e: RequestValidationError):
    mensajes = []
    for error in e.errors()[:6]:
        ubicacion = [str(p) for p in error.get("loc", []) if p not in ("body", "query", "path")]
        campo = ".".join(ubicacion) or "cuerpo"
        tipo = error.get("tipo") or error.get("type", "")
        texto = _NOMBRES_CAMPO.get(tipo)
        if texto is None:
            texto = "no es válido"
            contexto = error.get("ctx") or {}
            if "expected" in contexto:
                texto = f"debe ser uno de: {contexto['expected']}"
        mensajes.append(f"el campo «{campo}» {texto}")
    detalle = "Parámetros no válidos: " + "; ".join(mensajes) + "." if mensajes else "Parámetros no válidos."
    return _respuesta_error(422, detalle)


@app.exception_handler(KeyError)
async def _error_clave(request: Request, e: KeyError):
    traceback.print_exc()
    detalle = str(e.args[0]) if e.args else "Elemento no encontrado."
    return _respuesta_error(404, detalle)


@app.exception_handler(Exception)
async def _error_general(request: Request, e: Exception):
    print(f"[SOME] Error no controlado en {request.method} {request.url.path}:")
    traceback.print_exception(type(e), e, e.__traceback__)
    return _respuesta_error(500, "Ocurrió un error interno en el servidor. El detalle quedó registrado en la consola.")


# ---------------------------------------------------------------------------
# Interfaz: archivos estáticos y aplicación de una sola página
# ---------------------------------------------------------------------------
_FRONTEND = cfg.RUTA_FRONTEND.resolve()
_INDEX = _FRONTEND / "index.html"
_TIPOS_MIME = {".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
               ".svg": "image/svg+xml", ".html": "text/html; charset=utf-8", ".json": "application/json",
               ".png": "image/png", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json"}


def _archivo_estatico(ruta: str) -> Path | None:
    """Archivo dentro de `frontend/` o None (sin salir de la carpeta)."""
    if not ruta:
        return None
    candidato = (_FRONTEND / ruta).resolve()
    try:
        candidato.relative_to(_FRONTEND)
    except ValueError:
        return None
    return candidato if candidato.is_file() else None


@app.get("/", include_in_schema=False)
async def indice():
    if not _INDEX.exists():
        raise HTTPException(status_code=404, detail="No se encontró la interfaz (frontend/index.html).")
    return FileResponse(str(_INDEX), media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/{ruta:path}", include_in_schema=False)
async def estaticos(ruta: str):
    if ruta == "api" or ruta.startswith("api/"):
        raise HTTPException(status_code=404, detail=f"Ruta de la API no encontrada: /{ruta}.")
    archivo = _archivo_estatico(ruta)
    if archivo is not None:
        tipo = _TIPOS_MIME.get(archivo.suffix.lower())
        return FileResponse(str(archivo), media_type=tipo, headers={"Cache-Control": "no-cache"})
    return await indice()
