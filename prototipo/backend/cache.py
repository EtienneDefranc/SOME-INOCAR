"""Caché LRU en memoria para el prototipo SOME.

Dos usos:

- `cache_consultas`: resultados de consultas de la API (clave = cuerpo JSON
  normalizado y, si la consulta usa una fuente real —CHIRPS v3, Copernicus
  Marine o NOAA OISST—, la firma de su caché local: ver `consultar`), para responder al
  instante consultas repetidas. Una respuesta con días faltantes o con avisos
  (respaldo simulado o datos parciales) no se guarda (se reintentará), y varias
  peticiones idénticas simultáneas se calculan una sola vez (las demás esperan
  el resultado de la primera).
- `cache_campos`: campos ya generados por (fuente, variable, fecha,
  profundidad), en float32, para no volver a calcular los mismos días.
  `campos_en_cache` reúne un lote de fechas: toma del caché las que ya
  existen y genera de una sola vez (vectorizado) las que faltan.

Las dos cachés son seguras para hilos (bloqueo simple) y exponen
estadísticas (entradas, aciertos, fallos) para `GET /api/admin/sistema`.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from datetime import date
from typing import Any, Callable, Hashable, Iterable

import numpy as np


class CacheLRU:
    """Caché de reemplazo «menos usado recientemente» con capacidad fija."""

    def __init__(self, capacidad: int = 256, nombre: str = "cache"):
        self.capacidad = int(capacidad)
        self.nombre = nombre
        self._datos: OrderedDict[Hashable, Any] = OrderedDict()
        self._bloqueo = threading.Lock()
        self._en_curso: dict[Hashable, list] = {}     # clave → [Event, resultado]
        self.aciertos = 0
        self.fallos = 0

    def obtener(self, clave: Hashable, por_defecto: Any = None) -> Any:
        """Devuelve el valor guardado o `por_defecto`; cuenta acierto o fallo."""
        with self._bloqueo:
            if clave in self._datos:
                self._datos.move_to_end(clave)
                self.aciertos += 1
                return self._datos[clave]
            self.fallos += 1
            return por_defecto

    def contiene(self, clave: Hashable) -> bool:
        with self._bloqueo:
            return clave in self._datos

    def guardar(self, clave: Hashable, valor: Any) -> Any:
        """Guarda el valor y expulsa los menos usados si se supera la capacidad."""
        with self._bloqueo:
            self._datos[clave] = valor
            self._datos.move_to_end(clave)
            while len(self._datos) > self.capacidad:
                self._datos.popitem(last=False)
        return valor

    def obtener_o_calcular(self, clave: Hashable, calcular: Callable[[], Any],
                           guardar_si: Callable[[Any], bool] | None = None,
                           espera_max: float = 600.0) -> Any:
        """Devuelve el valor en caché o lo calcula, lo guarda (si `guardar_si(valor)` lo permite) y lo
        devuelve. Si otra petición idéntica ya lo está calculando, espera su resultado (o su error) en
        lugar de repetir el trabajo."""
        centinela = object()
        valor = self.obtener(clave, centinela)
        if valor is not centinela:
            return valor
        with self._bloqueo:
            curso = self._en_curso.get(clave)
            lider = curso is None
            if lider:
                curso = self._en_curso[clave] = [threading.Event(), None]
        if not lider:
            if curso[0].wait(timeout=espera_max) and curso[1] is not None:
                tipo, resultado = curso[1]
                if tipo == "error":
                    raise resultado
                return resultado
            return calcular()                 # la primera no terminó a tiempo: se calcula aquí
        try:
            valor = calcular()
        except BaseException as e:
            curso[1] = ("error", e)
            raise
        else:
            curso[1] = ("ok", valor)
            if guardar_si is None or guardar_si(valor):
                self.guardar(clave, valor)
            return valor
        finally:
            with self._bloqueo:
                self._en_curso.pop(clave, None)
            curso[0].set()

    def limpiar(self) -> None:
        with self._bloqueo:
            self._datos.clear()
            self.aciertos = 0
            self.fallos = 0

    def __len__(self) -> int:
        with self._bloqueo:
            return len(self._datos)

    def estadisticas(self) -> dict:
        """{entradas, aciertos, fallos, capacidad, nombre} para la pantalla de sistema."""
        with self._bloqueo:
            return {
                "nombre": self.nombre,
                "entradas": len(self._datos),
                "aciertos": self.aciertos,
                "fallos": self.fallos,
                "capacidad": self.capacidad,
            }


def clave_desde(objeto: Any) -> str:
    """Clave estable (JSON ordenado) a partir de un cuerpo de consulta o cualquier objeto."""
    return json.dumps(objeto, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def firma_fuentes_reales() -> str:
    """Firma de la caché local de CHIRPS v3 (días y climatología, de este u otro proceso) y de su fecha
    de corte efectiva: cambia en cuanto se guarda un día, se reemplaza un preliminar o llega un año de
    climatología, de modo que una respuesta calculada antes deja de servirse."""
    try:
        from .chirps import almacen as almacen_chirps
        from .conectores.chirps import ConectorCHIRPS
        return f"{almacen_chirps.firma_cache()}|{ConectorCHIRPS.fecha_corte_efectiva()}"
    except Exception:  # noqa: BLE001 — sin firma, la clave sigue siendo válida para fuentes simuladas
        return ""


def firma_copernicus() -> str:
    """Firma de la caché local de Copernicus Marine (`backend.copernicus.firma_cache()`): cambia con cada
    escritura de la caché, con el modo (real/simulado), el acceso y la fecha de corte, de modo que una
    respuesta calculada antes (p. ej., con el respaldo simulado) deja de servirse. Importación tolerante:
    sin el paquete, cadena vacía."""
    try:
        from . import copernicus as _copernicus
        return str(_copernicus.firma_cache())
    except Exception:  # noqa: BLE001 — sin firma, la clave sigue siendo válida
        return ""


def firma_noaa() -> str:
    """Firma de la caché local de NOAA OISST (`backend.noaa.firma_cache()`): cambia con cada escritura de la
    caché (un día nuevo, un preliminar que pasa a final, una climatología), con el modo (real/simulado), el
    acceso y la fecha de corte, de modo que una respuesta calculada antes deja de servirse. Importación
    tolerante: sin el paquete, cadena vacía."""
    try:
        from . import noaa as _noaa
        return str(_noaa.firma_cache())
    except Exception:  # noqa: BLE001 — sin firma, la clave sigue siendo válida
        return ""


def _completa(valor: Any) -> bool:
    """Una respuesta con días faltantes no se guarda: la próxima petición vuelve a intentar llenarlos.
    Tampoco una con avisos (respaldo simulado de una fuente real o datos parciales): en cuanto la fuente
    vuelva, la misma consulta debe dar los datos reales."""
    if isinstance(valor, dict):
        if valor.get("avisos"):
            return False
        try:
            return int(valor.get("dias_faltantes") or 0) == 0
        except (TypeError, ValueError):
            return True
    return True


def consultar(nombre: str, cuerpo: dict, calcular: Callable[[], Any]) -> Any:
    """Resuelve una consulta de la API con `cache_consultas` (clave = nombre + cuerpo y, si el cuerpo
    menciona CHIRPS v3, Copernicus Marine o NOAA OISST, la firma de su caché local)."""
    base = {"consulta": nombre, "cuerpo": cuerpo}
    clave = clave_desde(base)
    firmas = {}
    if "chirps_v3" in clave:
        firmas["firma_chirps"] = firma_fuentes_reales()
    if "copernicus_phy" in clave:
        firmas["firma_copernicus"] = firma_copernicus()
    if "noaa_oisst" in clave:
        firmas["firma_noaa"] = firma_noaa()
    if firmas:
        base.update(firmas)
        clave = clave_desde(base)
    return cache_consultas.obtener_o_calcular(clave, calcular, guardar_si=_completa)


# ---------------------------------------------------------------------------
# Instancias compartidas
# ---------------------------------------------------------------------------
# Resultados de consultas de la API (diccionarios listos para serializar)
cache_consultas = CacheLRU(capacidad=512, nombre="consultas")

# Campos generados: clave (fuente, variable, fecha, profundidad) → float32[lat, lon]
# 12 000 campos de 49 × 69 celdas en float32 ocupan unos 160 MB como máximo.
cache_campos = CacheLRU(capacidad=12_000, nombre="campos")


def campos_en_cache(fuente: str, variable: str, fechas: Iterable[date],
                    profundidad: float | None,
                    generador: Callable[[list[date]], np.ndarray]) -> np.ndarray:
    """Devuelve los campos (T, lat, lon) de un lote de fechas usando el caché.

    `generador(fechas_faltantes)` debe devolver un arreglo (n, lat, lon) con los
    campos de las fechas que no estaban en caché, en el mismo orden.
    """
    fechas = list(fechas)
    z = None if profundidad is None else float(profundidad)
    claves = [(fuente, variable, f, z) for f in fechas]

    salida: list[np.ndarray | None] = []
    faltantes: list[int] = []
    for i, clave in enumerate(claves):
        campo = cache_campos.obtener(clave)
        salida.append(campo)
        if campo is None:
            faltantes.append(i)

    if faltantes:
        nuevos = np.asarray(generador([fechas[i] for i in faltantes]), dtype=np.float32)
        for j, i in enumerate(faltantes):
            salida[i] = cache_campos.guardar(claves[i], nuevos[j])

    if not salida:
        return np.empty((0, 0, 0), dtype=np.float32)
    return np.stack(salida, axis=0)


def estadisticas() -> dict:
    """Estadísticas agregadas de las dos cachés (para `GET /api/admin/sistema`)."""
    consultas = cache_consultas.estadisticas()
    campos = cache_campos.estadisticas()
    return {
        "entradas": consultas["entradas"] + campos["entradas"],
        "aciertos": consultas["aciertos"] + campos["aciertos"],
        "fallos": consultas["fallos"] + campos["fallos"],
        "consultas": consultas,
        "campos": campos,
    }


def limpiar_todo() -> None:
    cache_consultas.limpiar()
    cache_campos.limpiar()
