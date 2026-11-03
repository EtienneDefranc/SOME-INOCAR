"""Bloqueo exclusivo entre procesos (y entre hilos) con un archivo de bloqueo.

Varios procesos del SOME pueden tocar la misma caché de CHIRPS a la vez: el
servidor (con su hilo de sincronización), el script de preparación
(`preparar_chirps`), las pruebas o un segundo servidor. Este módulo da un
bloqueo exclusivo por nombre de archivo que vale para todos ellos:

* en Windows, `msvcrt.locking` sobre el primer byte del archivo de bloqueo;
* en POSIX, `fcntl.flock`.

Además lleva un `threading.Lock` por ruta (los hilos del mismo proceso esperan
ahí sin sondear) y es **reentrante por hilo**: si el hilo ya tiene el bloqueo,
volver a pedirlo no lo bloquea (lo usa, p. ej., `red.un_archivo()` cuando una
descarga ya tomada llama a otra función que también lo toma).

Los archivos de bloqueo son vacíos y se dejan en su sitio (crearlos y borrarlos
en cada uso abriría carreras); viven en una carpeta `.bloqueos/`.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from pathlib import Path

if os.name == "nt":                     # pragma: no cover - depende del sistema
    import msvcrt
else:                                   # pragma: no cover
    import fcntl


class TiempoAgotadoBloqueo(TimeoutError):
    """No se obtuvo el bloqueo dentro del tiempo de espera indicado."""


_bloqueo_tabla = threading.Lock()
_hilos: dict[str, threading.Lock] = {}
_local = threading.local()


def _lock_hilos(clave: str) -> threading.Lock:
    with _bloqueo_tabla:
        if clave not in _hilos:
            _hilos[clave] = threading.Lock()
        return _hilos[clave]


def _profundidades() -> dict[str, int]:
    d = getattr(_local, "profundidad", None)
    if d is None:
        d = _local.profundidad = {}
    return d


def _intentar(fd: int) -> bool:
    """Un intento no bloqueante de tomar el bloqueo del archivo abierto en `fd`."""
    try:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _soltar(fd: int) -> None:
    try:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def bloqueo(ruta: Path | str, espera: float | None = None):
    """Bloqueo exclusivo entre procesos e hilos asociado al archivo `ruta`.

    `espera`: segundos máximos para obtenerlo (None = sin límite). Si no se
    obtiene a tiempo lanza `TiempoAgotadoBloqueo`. Reentrante por hilo.
    """
    ruta = Path(ruta)
    clave = str(ruta.resolve()) if ruta.is_absolute() else str(ruta.absolute())
    prof = _profundidades()
    if prof.get(clave, 0) > 0:          # este hilo ya lo tiene
        prof[clave] += 1
        try:
            yield
        finally:
            prof[clave] -= 1
        return

    limite = None if espera is None else time.monotonic() + max(0.0, espera)
    hilos = _lock_hilos(clave)
    if not hilos.acquire(timeout=-1 if limite is None else max(0.0, limite - time.monotonic())):
        raise TiempoAgotadoBloqueo(f"No se obtuvo el bloqueo {ruta.name} a tiempo (otro hilo lo tiene).")
    fd = None
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(ruta), os.O_RDWR | os.O_CREAT, 0o644)
        pausa = 0.005
        while not _intentar(fd):
            if limite is not None and time.monotonic() >= limite:
                raise TiempoAgotadoBloqueo(f"No se obtuvo el bloqueo {ruta.name} a tiempo (otro proceso lo tiene).")
            time.sleep(pausa)
            pausa = min(0.05, pausa * 2)
        prof[clave] = 1
        try:
            yield
        finally:
            prof[clave] = 0
            _soltar(fd)
    finally:
        if fd is not None:
            os.close(fd)
        hilos.release()
