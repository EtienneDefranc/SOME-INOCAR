"""Espejo de la carpeta de datos del SOME en una base PostgreSQL (p. ej., Supabase), para servidores sin disco
persistente como el plan gratuito de Render.

El prototipo guarda todo en archivos bajo `datos/`: usuarios, paneles, vistas, plantillas, trabajos y auditoría
(JSON), los PDF de los reportes y las cachés de NOAA OISST, Copernicus Marine y CHIRPS (`.npz` y `estado.json`).
Este módulo no cambia esa forma de trabajar: copia cada archivo a una fila de la tabla
`some_prototipo.archivos` y, al arrancar, los devuelve al disco. Así, lo que un usuario guarda y los días que
una consulta descarga sobreviven a los reinicios del servidor.

Se activa solo si la variable de entorno `SOME_BD_URL` tiene una cadena de conexión de PostgreSQL. Sin ella,
el SOME funciona como siempre, solo con archivos.

* `restaurar()`: baja de la base los archivos que faltan en el disco o son más antiguos (al arrancar, antes
  de que la aplicación lea nada).
* `Sincronizador`: hilo que cada `SOME_BD_INTERVALO_S` segundos (30 por defecto) sube los archivos nuevos o
  cambiados y borra de la base los que se borraron del disco; hace una última pasada al detenerse.
* `python -m backend.espejo_bd subir`: sube la carpeta local completa (para sembrar la base una vez).
  `python -m backend.espejo_bd restaurar` y `... estado` para revisar.

La tabla va en el esquema `some_prototipo`, no en `public`: en Supabase la API de datos solo expone `public`,
así que los hashes de las contraseñas y los datos no quedan accesibles con la clave anónima. Además se activa
la seguridad por filas (RLS) sin políticas, por si alguien expusiera el esquema.

No se copian los temporales (`*.tmp`) ni los bloqueos (`*.lock`, carpetas `.bloqueos`), que solo tienen
sentido dentro de un proceso.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ESQUEMA = "some_prototipo"
TABLA = f"{ESQUEMA}.archivos"
TAMANIO_MAXIMO = 50 * 1024 * 1024          # un archivo más grande no se copia (se avisa)
_EXCLUIR_SUFIJOS = (".tmp", ".lock")
_EXCLUIR_CARPETAS = {".bloqueos", "__pycache__"}


def url() -> str:
    return os.environ.get("SOME_BD_URL", "").strip()


def activo() -> bool:
    return bool(url())


def raiz() -> Path:
    """Carpeta de datos: la misma que usa `almacen.ruta_datos()` (SOME_RUTA_DATOS o `datos/`)."""
    personalizada = os.environ.get("SOME_RUTA_DATOS")
    if personalizada:
        return Path(personalizada).resolve()
    return Path(__file__).resolve().parent.parent / "datos"


def _log(texto: str) -> None:
    print(f"[SOME] Espejo en la base de datos: {texto}", flush=True)


def _conectar():
    import psycopg
    # prepare_threshold=None: compatible con los «poolers» de Supabase (PgBouncer en modo transacción)
    return psycopg.connect(url(), autocommit=True, prepare_threshold=None, connect_timeout=15)


def _preparar(conexion) -> None:
    with conexion.cursor() as c:
        c.execute(f"CREATE SCHEMA IF NOT EXISTS {ESQUEMA}")
        c.execute(f"""CREATE TABLE IF NOT EXISTS {TABLA} (
            ruta text PRIMARY KEY,
            contenido bytea NOT NULL,
            tamanio bigint NOT NULL,
            mtime_ns bigint NOT NULL,
            actualizado timestamptz NOT NULL DEFAULT now())""")
        c.execute(f"ALTER TABLE {TABLA} ENABLE ROW LEVEL SECURITY")


def _incluir(ruta_rel: Path) -> bool:
    if any(p in _EXCLUIR_CARPETAS for p in ruta_rel.parts):
        return False
    nombre = ruta_rel.name
    return not nombre.endswith(_EXCLUIR_SUFIJOS) and not nombre.startswith(".~")


def _inventario_local(base: Path) -> dict[str, tuple[int, int]]:
    """{ruta relativa con «/»: (tamaño, mtime_ns)} de los archivos que se copian."""
    salida: dict[str, tuple[int, int]] = {}
    if not base.exists():
        return salida
    for p in base.rglob("*"):
        try:
            if not p.is_file():
                continue
            rel = p.relative_to(base)
            if not _incluir(rel):
                continue
            st = p.stat()
            salida[rel.as_posix()] = (st.st_size, st.st_mtime_ns)
        except OSError:
            continue        # el archivo desapareció mientras se recorría
    return salida


def _inventario_bd(conexion) -> dict[str, tuple[int, int]]:
    with conexion.cursor() as c:
        c.execute(f"SELECT ruta, tamanio, mtime_ns FROM {TABLA}")
        return {r: (int(t), int(m)) for r, t, m in c.fetchall()}


def _escribir_local(base: Path, ruta: str, contenido: bytes, mtime_ns: int) -> None:
    destino = base / ruta
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(destino.name + ".restaurando.tmp")
    temporal.write_bytes(contenido)
    os.utime(temporal, ns=(mtime_ns, mtime_ns))
    os.replace(temporal, destino)


def restaurar(base: Path | None = None) -> int:
    """Baja de la base los archivos que faltan en el disco o que allí son más nuevos. Devuelve cuántos."""
    if not activo():
        return 0
    base = base or raiz()
    base.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    with _conectar() as conexion:
        _preparar(conexion)
        remoto = _inventario_bd(conexion)
        local = _inventario_local(base)
        pendientes = [r for r, (t, m) in remoto.items() if r not in local or local[r][1] < m or
                      (local[r][1] == m and local[r][0] != t)]
        total = 0
        with conexion.cursor() as c:
            for i in range(0, len(pendientes), 50):
                lote = pendientes[i:i + 50]
                c.execute(f"SELECT ruta, contenido, mtime_ns FROM {TABLA} WHERE ruta = ANY(%s)", (lote,))
                for ruta, contenido, mtime_ns in c.fetchall():
                    _escribir_local(base, ruta, bytes(contenido), int(mtime_ns))
                    total += len(contenido)
    _log(f"restaurados {len(pendientes)} de {len(remoto)} archivos ({total / 1e6:.1f} MB) en "
         f"{time.perf_counter() - t0:.1f} s.")
    return len(pendientes)


def sincronizar(base: Path | None = None, conocido: dict[str, tuple[int, int]] | None = None,
                borrar: bool = True) -> dict[str, tuple[int, int]]:
    """Sube lo nuevo o cambiado respecto de `conocido` (o de la base si es None) y, si `borrar`, quita de la
    base lo que ya no está en el disco. Devuelve el inventario que quedó en la base."""
    base = base or raiz()
    local = _inventario_local(base)
    with _conectar() as conexion:
        if conocido is None:
            _preparar(conexion)
            conocido = _inventario_bd(conexion)
        cambiados = [r for r, v in local.items() if conocido.get(r) != v]
        borrados = [r for r in conocido if r not in local] if borrar else []
        subidos = 0
        with conexion.cursor() as c:
            for ruta in cambiados:
                p = base / ruta
                try:
                    st = p.stat()
                    if st.st_size > TAMANIO_MAXIMO:
                        _log(f"no se copia {ruta} ({st.st_size / 1e6:.0f} MB, más que el máximo).")
                        continue
                    contenido = p.read_bytes()
                except OSError:
                    continue
                c.execute(
                    f"""INSERT INTO {TABLA} (ruta, contenido, tamanio, mtime_ns, actualizado)
                        VALUES (%s, %s, %s, %s, now())
                        ON CONFLICT (ruta) DO UPDATE SET contenido = EXCLUDED.contenido,
                          tamanio = EXCLUDED.tamanio, mtime_ns = EXCLUDED.mtime_ns, actualizado = now()""",
                    (ruta, contenido, len(contenido), st.st_mtime_ns))
                conocido[ruta] = (len(contenido), st.st_mtime_ns)
                subidos += 1
            if borrados:
                c.execute(f"DELETE FROM {TABLA} WHERE ruta = ANY(%s)", (borrados,))
                for r in borrados:
                    conocido.pop(r, None)
    if subidos or borrados:
        _log(f"{subidos} archivos subidos y {len(borrados)} borrados.")
    return conocido


class Sincronizador:
    """Hilo que mantiene la base al día con la carpeta de datos."""

    def __init__(self, intervalo_s: float | None = None):
        self.intervalo = float(intervalo_s or os.environ.get("SOME_BD_INTERVALO_S", 30))
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._conocido: dict[str, tuple[int, int]] | None = None
        self._bloqueo = threading.Lock()

    def _pasada(self) -> None:
        with self._bloqueo:
            try:
                self._conocido = sincronizar(conocido=self._conocido)
            except Exception as e:  # noqa: BLE001 - la base nunca debe tumbar la aplicación
                self._conocido = None   # en la próxima pasada se vuelve a leer el inventario de la base
                _log(f"no se pudo sincronizar ({type(e).__name__}: {e}); se reintenta en {self.intervalo:.0f} s.")

    def _bucle(self) -> None:
        while not self._parar.wait(self.intervalo):
            self._pasada()

    def iniciar(self) -> None:
        if not activo() or self._hilo is not None:
            return
        self._pasada()
        self._hilo = threading.Thread(target=self._bucle, name="some-espejo-bd", daemon=True)
        self._hilo.start()
        _log(f"activo, cada {self.intervalo:.0f} s.")

    def detener(self) -> None:
        if self._hilo is None:
            return
        self._parar.set()
        self._hilo.join(timeout=10)
        self._pasada()          # última pasada: lo guardado justo antes de apagar no se pierde
        self._hilo = None


def _estado() -> None:
    with _conectar() as conexion:
        _preparar(conexion)
        with conexion.cursor() as c:
            c.execute(f"SELECT count(*), coalesce(sum(tamanio), 0), max(actualizado) FROM {TABLA}")
            n, total, ultimo = c.fetchone()
    print(f"{n} archivos, {int(total) / 1e6:.1f} MB; última actualización: {ultimo}")


if __name__ == "__main__":
    orden = sys.argv[1] if len(sys.argv) > 1 else "estado"
    if not activo():
        if orden == "restaurar":       # en el arranque sin base de datos no hay nada que restaurar
            _log("SOME_BD_URL no está definida; se usan solo los archivos locales.")
            sys.exit(0)
        sys.exit("Defina SOME_BD_URL con la cadena de conexión de PostgreSQL.")
    if orden == "subir":
        sincronizar(borrar=False)
        _estado()
    elif orden == "restaurar":
        restaurar()
    elif orden == "estado":
        _estado()
    else:
        sys.exit("Uso: python -m backend.espejo_bd [subir | restaurar | estado]")
