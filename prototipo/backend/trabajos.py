"""Cola de trabajos del prototipo SOME (reportes y exportaciones largas).

Un hilo de fondo toma los trabajos en orden y ejecuta su función
`funcion(trabajo_id, progreso)`, donde `progreso(porcentaje, mensaje)` actualiza
el estado visible en la interfaz. Estados: `en_cola` → `procesando` →
`listo` | `error`. El estado se persiste en `datos/trabajos.json` mediante
`almacen.py`, así que la lista sobrevive a los reinicios.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from datetime import datetime
from typing import Callable

from . import almacen

ESTADOS = ("en_cola", "procesando", "listo", "error")
ESTADOS_ACTIVOS = ("en_cola", "procesando")

MENSAJE_REINICIO = "El servidor se reinició antes de terminar el trabajo. Vuelva a generarlo."
MENSAJE_INTERRUPCION = ("El procesamiento se interrumpió por un error interno del servidor; "
                        "el detalle quedó en la consola. Vuelva a generar el trabajo.")
MENSAJE_PERSISTENCIA = ("No se pudo guardar el estado del trabajo en datos/trabajos.json (archivo bloqueado "
                        "o sin permisos). Vuelva a generarlo.")

_REINTENTOS_PERSISTENCIA = 3
_PAUSA_REINTENTO_S = 0.2

_COLA: "queue.Queue[tuple[int, Callable, str | None]]" = queue.Queue()
_HILO: threading.Thread | None = None
_BLOQUEO = threading.Lock()
_DETENER = threading.Event()
_LISTO: dict[int, threading.Event] = {}
_EN_PROCESO: dict[str, int | None] = {"id": None}   # trabajo que el hilo está ejecutando ahora


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _actualizar(trabajo_id: int, **campos) -> bool:
    """Persiste el estado con reintentos (Windows puede bloquear el JSON un instante). True si se guardó."""
    ultimo_error: Exception | None = None
    for intento in range(_REINTENTOS_PERSISTENCIA):
        try:
            almacen.actualizar_trabajo(trabajo_id, **campos)
            return True
        except Exception as e:  # noqa: BLE001 - PermissionError, OSError, JSON corrupto…
            ultimo_error = e
            if intento < _REINTENTOS_PERSISTENCIA - 1:
                time.sleep(_PAUSA_REINTENTO_S)
    print(f"[SOME] No se pudo guardar el estado del trabajo {trabajo_id} tras "
          f"{_REINTENTOS_PERSISTENCIA} intentos: {ultimo_error!r}")
    return False


# ---------------------------------------------------------------------------
# Ciclo de vida del hilo
# ---------------------------------------------------------------------------
def _arrancar_hilo() -> None:
    """Crea el hilo de fondo si no existe o si murió. Debe llamarse con _BLOQUEO tomado."""
    global _HILO
    if _HILO is not None and _HILO.is_alive():
        return
    _DETENER.clear()
    _HILO = threading.Thread(target=_trabajador, name="some-trabajos", daemon=True)
    _HILO.start()


def iniciar() -> None:
    """Arranque del servidor: cierra los trabajos huérfanos del proceso anterior y lanza el hilo (idempotente)."""
    with _BLOQUEO:
        for t in almacen.listar_trabajos():
            if t.get("estado") in ESTADOS_ACTIVOS:
                _actualizar(t["id"], estado="error", progreso=0, terminado=_ahora(), mensaje=MENSAJE_REINICIO)
        _arrancar_hilo()


def asegurar_hilo() -> bool:
    """Vigilante: si el hilo murió en pleno servicio, marca con error solo el trabajo que estaba
    procesando (los que siguen en la cola se atienden al relanzar el hilo). Devuelve True si tuvo que relanzarlo."""
    with _BLOQUEO:
        if _HILO is not None and _HILO.is_alive():
            return False
        if _HILO is not None:
            print("[SOME] El hilo de trabajos no estaba vivo; se relanza.")
            tid = _EN_PROCESO.get("id")
            if tid is not None:
                _actualizar(tid, estado="error", progreso=0, terminado=_ahora(),
                            mensaje=MENSAJE_INTERRUPCION, error="hilo de trabajos interrumpido")
                evento = _LISTO.get(tid)
                if evento is not None:
                    evento.set()
                _EN_PROCESO["id"] = None
        _arrancar_hilo()
        return True


def detener(espera_s: float = 2.0) -> None:
    _DETENER.set()
    _COLA.put((-1, None, None))
    if _HILO is not None:
        _HILO.join(timeout=espera_s)


def _trabajador() -> None:
    """Bucle del hilo: ninguna excepción de un trabajo (ni de la persistencia) debe matar el hilo."""
    while not _DETENER.is_set():
        try:
            trabajo_id, funcion, terminado_fijo = _COLA.get()
        except Exception:  # noqa: BLE001 - defensa ante un fallo inesperado de la cola
            traceback.print_exc()
            time.sleep(_PAUSA_REINTENTO_S)
            continue
        if trabajo_id == -1 or funcion is None:
            continue
        try:
            _ejecutar(trabajo_id, funcion, terminado_fijo)
        except Exception:  # noqa: BLE001 - se registra y el hilo sigue con el siguiente trabajo
            print(f"[SOME] Error no controlado al ejecutar el trabajo {trabajo_id}:")
            traceback.print_exc()
            _actualizar(trabajo_id, estado="error", progreso=0, terminado=_ahora(),
                        mensaje=MENSAJE_INTERRUPCION, error="error no controlado")
        finally:
            _EN_PROCESO["id"] = None
            evento = _LISTO.get(trabajo_id)
            if evento is not None:
                evento.set()


def _ejecutar(trabajo_id: int, funcion: Callable, terminado_fijo: str | None) -> None:
    _EN_PROCESO["id"] = trabajo_id

    def progreso(porcentaje: float, mensaje: str = "") -> None:
        campos = {"progreso": int(max(0, min(99, porcentaje)))}
        if mensaje:
            campos["mensaje"] = mensaje
        # Un fallo al guardar el progreso no interrumpe la generación del reporte
        _actualizar(trabajo_id, **campos)

    try:
        if not _actualizar(trabajo_id, estado="procesando", progreso=2, mensaje="Iniciando…", iniciado=_ahora()):
            raise OSError(MENSAJE_PERSISTENCIA)
        resultado = funcion(trabajo_id, progreso) or {}
        guardado = _actualizar(
            trabajo_id, estado="listo", progreso=100,
            mensaje=resultado.get("mensaje") or "Trabajo terminado.",
            archivo=resultado.get("archivo"), ruta=resultado.get("ruta"),
            nombre_archivo=resultado.get("nombre_archivo"),
            procedencia=resultado.get("procedencia"), insignia=resultado.get("insignia"),
            terminado=terminado_fijo or _ahora())
        if not guardado:
            raise OSError(MENSAJE_PERSISTENCIA)
    except Exception as e:  # noqa: BLE001 - se informa al usuario y se registra
        traceback.print_exc()
        detalle = getattr(e, "detalle", None) or str(e) or e.__class__.__name__
        _actualizar(trabajo_id, estado="error", progreso=0, mensaje=f"Error: {detalle}", error=detalle,
                    terminado=_ahora())


# ---------------------------------------------------------------------------
# API interna
# ---------------------------------------------------------------------------
def encolar(tipo: str, titulo: str, usuario: dict, funcion: Callable, *, plantilla: str | None = None,
            parametros: dict | None = None, creado: str | None = None,
            terminado: str | None = None, trabajo_id: int | None = None, ejemplo: bool = False) -> dict:
    """Registra el trabajo con estado `en_cola` y lo entrega al hilo de fondo.

    `funcion(trabajo_id, progreso)` debe devolver `{"archivo": ruta_api, "ruta": ruta_disco,
    "nombre_archivo": ..., "mensaje": ...}`. `creado`/`terminado` permiten
    retrodatar los trabajos de ejemplo del arranque; `ejemplo` los marca como tales y
    `trabajo_id` reutiliza el id de uno existente para regenerarlo (el registro y su PDF
    se reemplazan).
    """
    asegurar_hilo()
    trabajo = {
        "id": int(trabajo_id) if trabajo_id is not None else almacen.siguiente_id("trabajos"),
        "tipo": tipo,
        "titulo": titulo,
        "estado": "en_cola",
        "progreso": 0,
        "mensaje": "En cola, esperando turno…",
        "creado": creado or _ahora(),
        "iniciado": None,
        "terminado": None,
        "archivo": None,
        "ruta": None,
        "nombre_archivo": None,
        "usuario": usuario["usuario"],
        "usuario_id": usuario["id"],
        "plantilla": plantilla,
        "parametros": parametros or {},
        "error": None,
    }
    if ejemplo:
        trabajo["ejemplo"] = True
    almacen.guardar_trabajo(trabajo)
    _LISTO[trabajo["id"]] = threading.Event()
    _COLA.put((trabajo["id"], funcion, terminado))
    return publico(trabajo)


def publico(t: dict) -> dict:
    """Formato de `GET /api/trabajos` (sección 7.5).

    Un reporte listo sin `procedencia` es anterior al conector real de CHIRPS (el campo nació con él):
    entonces todos sus datos eran simulados y así se declara, en lugar de dejarlo sin insignia."""
    procedencia, insignia = t.get("procedencia"), t.get("insignia")
    if procedencia is None and t.get("estado") == "listo" and t.get("tipo", "reporte") == "reporte":
        from . import configuracion as cfg
        procedencia, insignia = "simulado", cfg.INSIGNIA_SIMULADOS
    return {
        "id": t["id"],
        "tipo": t.get("tipo", "reporte"),
        "titulo": t.get("titulo", ""),
        "estado": t.get("estado", "en_cola"),
        "progreso": int(t.get("progreso", 0)),
        "mensaje": t.get("mensaje", ""),
        "creado": t.get("creado"),
        "terminado": t.get("terminado"),
        "archivo": t.get("archivo") if t.get("estado") == "listo" else None,
        "nombre_archivo": t.get("nombre_archivo"),
        "usuario": t.get("usuario"),
        "plantilla": t.get("plantilla"),
        "parametros": t.get("parametros") or {},
        # Procedencia del contenido del reporte («real», «simulado» o «mixto»), cuando ya está listo
        "procedencia": procedencia,
        "insignia": insignia,
    }


def listar(usuario: dict | None = None, todos: bool = False, limite: int | None = None) -> list[dict]:
    """Trabajos del usuario (o todos), del más reciente al más antiguo. De paso vigila que el hilo siga vivo."""
    asegurar_hilo()
    trabajos = almacen.listar_trabajos()
    if not todos and usuario is not None:
        trabajos = [t for t in trabajos if str(t.get("usuario_id")) == str(usuario["id"])]
    trabajos.sort(key=lambda t: t.get("creado") or "", reverse=True)
    if limite:
        trabajos = trabajos[: int(limite)]
    return [publico(t) for t in trabajos]


def obtener(trabajo_id) -> dict | None:
    t = almacen.trabajo(trabajo_id)
    return t if t is not None else None


def resumen() -> dict:
    """{en_cola, procesando, listos, error} para `GET /api/admin/sistema`."""
    conteo = {"en_cola": 0, "procesando": 0, "listos": 0, "error": 0}
    for t in almacen.listar_trabajos():
        estado = t.get("estado")
        if estado == "listo":
            conteo["listos"] += 1
        elif estado in conteo:
            conteo[estado] += 1
    return conteo


def esperar(trabajo_id, tiempo_max_s: float = 120.0) -> dict | None:
    """Bloquea hasta que el trabajo termine (listo o error) o se agote el tiempo. Para pruebas."""
    evento = _LISTO.get(int(trabajo_id))
    if evento is not None:
        evento.wait(timeout=tiempo_max_s)
    return almacen.trabajo(trabajo_id)
