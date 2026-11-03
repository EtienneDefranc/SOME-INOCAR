"""Pruebas del acceso real a CHIRPS v3 (servidor del CHC y réplica de dynamical.org).

    python -m backend.chirps.prueba_chirps
    SOME_CHIRPS_SIN_RED=1 python -m backend.chirps.prueba_chirps    # solo caché local, sin red

Las pruebas de malla, TIFF mínimo, almacén, cortesía (limitador y disyuntor),
conmutación CHC ↔ réplica, estado RF-21 y llenado bajo demanda usan datos
sintéticos en carpetas temporales y simulan el CHC (sesión HTTP falsa) y la
réplica (módulo falso): no tocan la red real en ningún modo. Las del conector
usan la caché real `datos/chirps/` (si está vacía, se omiten con aviso). Con
red, además se lee un día real del CHC (si no está marcado caído) y se
consulta la cobertura real de la réplica.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from .. import configuracion as cfg

_resultados = {"ok": 0, "fallo": 0, "omitida": 0}


def verificar(condicion: bool, texto: str) -> None:
    if condicion:
        _resultados["ok"] += 1
        print(f"  OK     {texto}")
    else:
        _resultados["fallo"] += 1
        print(f"  FALLO  {texto}")


def omitir(texto: str) -> None:
    _resultados["omitida"] += 1
    print(f"  OMITE  {texto}")


def _dias(inicio: date, fin: date) -> list[date]:
    return [inicio + timedelta(days=i) for i in range((fin - inicio).days + 1)]


# ---------------------------------------------------------------------------
# Simulaciones: reloj, sesión HTTP del CHC y réplica
# ---------------------------------------------------------------------------
class RelojFalso:
    """Reloj monótono (y de pared) simulado: dormir() avanza el tiempo sin esperar de verdad."""

    def __init__(self):
        self.t = 1000.0
        self.dormido = 0.0
        self.origen_pared = datetime.now().replace(microsecond=0)

    def __call__(self) -> float:
        return self.t

    def pared(self) -> datetime:
        return self.origen_pared + timedelta(seconds=self.t - 1000.0)

    def dormir(self, s: float) -> None:
        self.t += s
        self.dormido += s

    def avanzar(self, s: float) -> None:
        self.t += s


class RespuestaFalsa:
    def __init__(self, codigo: int, contenido: bytes = b""):
        self.status_code = codigo
        self.content = contenido
        self.headers = {}


class SesionFalsa:
    """Sesión HTTP del CHC simulada: «caido» (sin conexión), «429» o «ok»."""

    def __init__(self, modo: str = "caido"):
        from .constantes import USER_AGENT
        self.modo = modo
        self.llamadas = 0
        self.headers = {"User-Agent": USER_AGENT}

    def get(self, url, headers=None, timeout=None):
        import requests
        self.llamadas += 1
        if self.modo == "caido":
            raise requests.ConnectionError("conexión rechazada (simulada)")
        if self.modo == "429":
            return RespuestaFalsa(429)
        return RespuestaFalsa(206, b"x" * 512)


class EspejoFalso:
    """Módulo `espejo` simulado con la interfaz del contrato."""

    def __init__(self, final: tuple[date, date], preliminar: tuple[date, date]):
        self.cob = {"final": final, "preliminar": preliminar}
        self.llamadas: list[tuple[date, date, str]] = []
        self.falla = False
        self.error = ""

    def disponible(self) -> bool:
        return True

    def cobertura(self, forzar: bool = False) -> dict:
        # «Ahora» real: el estado RF-21 marca el día de la consulta y exige que sea vigente
        # (una fecha fija hacía fallar la prueba al pasar la medianoche).
        consultado = datetime.now().isoformat(timespec="seconds")
        if self.error:
            return {"final": None, "preliminar": None, "consultado": consultado,
                    "latencia_ms": 0, "error": self.error}
        return {**self.cob, "consultado": consultado, "latencia_ms": 150, "error": ""}

    def leer_diario(self, inicio: date, fin: date, producto: str):
        from .constantes import ErrorCHIRPS
        self.llamadas.append((inicio, fin, producto))
        if self.falla:
            raise ErrorCHIRPS("réplica no disponible (simulada)")
        a, b = self.cob[producto]
        fechas = _dias(max(inicio, a), min(fin, b))
        v = np.full((len(fechas), 49, 69), 1.0 if producto == "final" else 0.5, dtype=np.float32)
        v[:, 0, 0] = np.nan                       # «mar»
        return fechas, v


@contextlib.contextmanager
def entorno_simulado():
    """Caché, estado y red aislados: carpeta temporal, sesión del CHC falsa, réplica falsa,
    reloj simulado y modo sin red desactivado solo aquí (nada sale a la red real)."""
    import requests

    from . import almacen, descarga, estado, red
    guardado = {
        "diario": almacen.RUTA_DIARIO, "mensual": almacen.RUTA_MENSUAL, "estado": estado.RUTA_ESTADO,
        "reloj": red._reloj, "pared": red._reloj_pared, "dormir": red._dormir, "sesion": red.sesion,
        "pausa": red.PAUSA_REINTENTO_S,
        "modulo_espejo": descarga.modulo_espejo, "descargar_dia": descarga.descargar_dia,
        "sin_red": os.environ.get("SOME_CHIRPS_SIN_RED"), "get": requests.Session.get,
    }
    reloj = RelojFalso()
    sesion_falsa = SesionFalsa("caido")

    def prohibido(*_a, **_k):
        raise AssertionError("una prueba simulada intentó usar la red real")

    with tempfile.TemporaryDirectory() as tmp:
        try:
            almacen.RUTA_DIARIO = Path(tmp) / "diario"
            almacen.RUTA_MENSUAL = Path(tmp) / "mensual"
            almacen._memoria.clear()
            almacen._ultimo_dia_cache = None
            almacen._origenes_cache = None
            estado.RUTA_ESTADO = Path(tmp) / "estado.json"
            estado._datos = None
            estado._mtime_cargado = -1
            estado.marcar_verificando(False)
            red._reloj, red._reloj_pared, red._dormir = reloj, reloj.pared, reloj.dormir
            red.PAUSA_REINTENTO_S = 0.0
            red._proxima_peticion = 0.0
            red.reiniciar_disyuntor()
            red.sesion = lambda: sesion_falsa
            requests.Session.get = prohibido
            descarga._disp_anios.clear()
            descarga._disp_cache = None
            descarga._limpiar_caida_espejo()
            os.environ.pop("SOME_CHIRPS_SIN_RED", None)
            yield {"reloj": reloj, "sesion": sesion_falsa, "tmp": Path(tmp)}
        finally:
            almacen.RUTA_DIARIO, almacen.RUTA_MENSUAL = guardado["diario"], guardado["mensual"]
            almacen._memoria.clear()
            almacen._ultimo_dia_cache = None
            almacen._origenes_cache = None
            estado.RUTA_ESTADO = guardado["estado"]
            estado._datos = None
            estado._mtime_cargado = -1
            red._reloj, red._dormir, red.sesion = guardado["reloj"], guardado["dormir"], guardado["sesion"]
            red._reloj_pared = guardado["pared"]
            red.PAUSA_REINTENTO_S = guardado["pausa"]
            red._proxima_peticion = 0.0
            red.reiniciar_disyuntor()
            requests.Session.get = guardado["get"]
            descarga.modulo_espejo = guardado["modulo_espejo"]
            descarga.descargar_dia = guardado["descargar_dia"]
            descarga._disp_anios.clear()
            descarga._disp_cache = None
            descarga._limpiar_caida_espejo()
            if guardado["sin_red"] is None:
                os.environ.pop("SOME_CHIRPS_SIN_RED", None)
            else:
                os.environ["SOME_CHIRPS_SIN_RED"] = guardado["sin_red"]


# ---------------------------------------------------------------------------
# Malla, TIFF y almacén
# ---------------------------------------------------------------------------
def prueba_malla() -> None:
    from .malla import TEXTO_PROCESAMIENTO, agregar_5x5, reproyectar_a_some
    print("Malla:")
    a = np.arange(25, dtype=np.float32).reshape(5, 5)
    verificar(abs(float(agregar_5x5(a)[0, 0]) - 12.0) < 1e-6, "media de un bloque 5×5")
    b = a.copy(); b.ravel()[:12] = np.nan
    verificar(np.isfinite(agregar_5x5(b)[0, 0]), "bloque con 13 de 25 válidas da valor")
    b.ravel()[12] = np.nan
    verificar(np.isnan(agregar_5x5(b)[0, 0]), "bloque con 12 de 25 válidas da NaN")

    nativo = np.full((50, 70), np.nan, dtype=np.float32)
    nativo[10, 20] = 4.0; nativo[11, 20] = 8.0
    r = reproyectar_a_some(nativo)
    verificar(r.shape == (49, 69), "forma de la malla SOME (49, 69)")
    # Celdas nativas (asc) 10 y 11 tienen centros −6.125+2.5 y −6.125+2.75 → esquina común lat −3.5
    j = int(np.argmin(abs(cfg.LAT + 3.5)))
    verificar(abs(float(r[10, 20]) - 6.0) < 1e-6 and j == 10, "centro SOME = media de 2 de 4 válidas")
    verificar(abs(float(r[10, 19]) - 6.0) < 1e-6, "la esquina vecina comparte las mismas 2 celdas")
    verificar(np.isnan(r[30, 40]), "esquina con 0 válidas da NaN")
    verificar(np.isnan(r[9, 19]), "esquina con 1 de 4 válidas da NaN")
    verificar("rangos HTTP" in TEXTO_PROCESAMIENTO and "Zarr/Icechunk" in TEXTO_PROCESAMIENTO
              and "8 bits de mantisa" in TEXTO_PROCESAMIENTO,
              "el texto de procesamiento describe los dos caminos (GeoTIFF del CHC y réplica)")


def prueba_tiff_minimo() -> None:
    from PIL import Image
    from . import lector_tiff
    print("TIFF mínimo y limpieza:")
    datos = np.array([[1.5, -9999, 2.0, -0.3], [3.0, 4.0, -5.0, 0.0]], dtype=np.float32)
    # Comprimir cada fila con Pillow (TIFF LZW de 1 fila) y reensamblar sus franjas
    franjas, cnts = b"", []
    for fila in datos:
        buf = io.BytesIO()
        Image.fromarray(fila[None, :].copy()).save(buf, format="TIFF", compression="tiff_lzw")
        crudo = buf.getvalue()
        with Image.open(io.BytesIO(crudo)) as img:
            (o,), (c,) = img.tag_v2[273], img.tag_v2[279]
        franjas += crudo[o:o + c]
        cnts.append(c)
    tiff = lector_tiff._tiff_minimo(4, np.asarray(cnts), franjas, 5)
    with Image.open(io.BytesIO(tiff)) as img:
        modo = img.mode
        arr = np.asarray(img, dtype=np.float32)
    verificar(modo == "F" and np.array_equal(arr, datos), "TIFF mínimo reconstruido idéntico (LZW)")
    limpio = lector_tiff.limpiar(datos)
    verificar(np.isnan(limpio[0, 1]) and np.isnan(limpio[1, 2]), "-9999 y < −1 pasan a NaN")
    verificar(limpio[0, 3] == 0.0 and np.nanmin(limpio) >= 0, "negativos pequeños pasan a 0")


def prueba_almacen() -> None:
    from . import almacen, constantes
    from .constantes import CHC, ESPEJO, FINAL, PRELIMINAR
    print("Almacén y origen por día (carpeta temporal):")
    with entorno_simulado() as ent:
        d = date(2026, 9, 30)
        campo = np.full((49, 69), 2.5, dtype=np.float32)
        almacen.guardar({d: (campo, constantes.FINAL)})
        almacen.guardar({d: (campo * 0 + 9.0, constantes.PRELIMINAR)})
        v, t = almacen.leer(d, d)
        verificar(t[0] == constantes.FINAL and float(v[0, 0, 0]) == 2.5,
                  "un día final no se sobrescribe con uno preliminar")
        d2 = date(2026, 10, 2)
        almacen.guardar({d2: (campo * 0 + 1.0, constantes.PRELIMINAR)})
        almacen.guardar({d2: (campo * 0 + 3.0, constantes.FINAL)})
        v, t = almacen.leer(d2, d2)
        verificar(t[0] == constantes.FINAL and float(v[0, 0, 0]) == 3.0, "un preliminar sí pasa a final")
        v, t = almacen.leer(date(2026, 9, 29), date(2026, 10, 3))
        verificar(v.shape == (5, 49, 69) and list(t) == [0, 2, 0, 2, 0], "lectura entre dos meses")
        verificar(almacen.ultimo_dia() == d2, "último día en caché")
        almacen._memoria.clear()
        v, t = almacen.leer(d2, d2)
        verificar(t[0] == constantes.FINAL, "relectura desde disco (npz)")
        verificar(not list((ent["tmp"] / "diario").glob("*.tmp")), "sin archivos temporales residuales")

        # Regla de origen
        d3, d4 = date(2026, 10, 5), date(2026, 10, 6)
        almacen.guardar({d3: (campo * 0 + 4.0, FINAL)}, origen=ESPEJO)
        n = almacen.guardar({d3: (campo * 0 + 4.1, FINAL)}, origen=CHC)
        verificar(n == 0 and almacen.tipos_y_origenes(d3, d3)[d3] == (FINAL, ESPEJO),
                  "mismo tipo de otra fuente: el día no se reescribe (mismo producto)")
        almacen.guardar({d4: (campo * 0 + 0.5, PRELIMINAR, ESPEJO)})
        almacen.guardar({d4: (campo * 0 + 0.7, FINAL, CHC)})
        verificar(almacen.tipos_y_origenes(d4, d4)[d4] == (FINAL, CHC),
                  "preliminar de la réplica → final del CHC cambia también el origen")
        c = almacen.conteo(date(2026, 10, 1), date(2026, 10, 31))
        verificar(c["por_origen"] == {CHC: 2, ESPEJO: 1}, f"conteo por origen del mes: {c['por_origen']}")
        verificar(almacen.conteo_origenes() == {CHC: 3, ESPEJO: 1}, "conteo de orígenes de toda la caché")
        with np.load(almacen.ruta_mes(2026, 10)) as z:
            verificar("origen" in z.files and int(z["origen"][4]) == constantes.ORIGEN_ESPEJO,
                      "el npz guarda el arreglo «origen»")
        try:
            almacen.guardar({d4: (campo, FINAL)}, origen="ftp")
            verificar(False, "origen desconocido rechazado")
        except ValueError:
            verificar(True, "origen desconocido rechazado")

        # npz antiguos (sin «origen») se leen como CHC
        n_dias = 31
        tipo = np.zeros(n_dias, np.int8); tipo[:3] = FINAL
        valores = np.full((n_dias, 49, 69), np.nan, np.float32); valores[:3] = 1.0
        ruta = almacen.ruta_mes(2023, 12)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(ruta, valores=valores, tipo=tipo, obtenido=np.array([""] * n_dias, dtype="U19"))
        almacen._memoria.clear()
        to = almacen.tipos_y_origenes(date(2023, 12, 1), date(2023, 12, 4))
        verificar(to[date(2023, 12, 1)] == (FINAL, CHC) and to[date(2023, 12, 4)] == (0, ""),
                  "npz antiguo sin «origen»: días con dato leídos como «chc»")
        almacen.guardar({date(2023, 12, 4): (campo, FINAL)}, origen=ESPEJO)
        to = almacen.tipos_y_origenes(date(2023, 12, 1), date(2023, 12, 4))
        verificar(to[date(2023, 12, 1)] == (FINAL, CHC) and to[date(2023, 12, 4)] == (FINAL, ESPEJO),
                  "al escribir sobre un npz antiguo se conserva «chc» en los días previos")


# ---------------------------------------------------------------------------
# Cortesía con el CHC: User-Agent, limitador global y disyuntor
# ---------------------------------------------------------------------------
def prueba_cortesia() -> None:
    from . import descarga, estado, red, sincronizacion
    from .constantes import (CONTACTO, DISYUNTOR_S, FALLOS_DISYUNTOR, PETICIONES_POR_SEGUNDO, TRABAJADORES,
                             USER_AGENT)
    print("Cortesía con el CHC (simulado, sin red):")
    # El contacto sale SOLO de SOME_CHIRPS_CONTACTO: sin esa variable no se envía ningún correo.
    if CONTACTO:
        verificar(USER_AGENT.startswith(f"SOME-prototipo-INOCAR/{cfg.VERSION} ") and "ESPOL-INOCAR" in USER_AGENT
                  and f"contacto: {CONTACTO}" in USER_AGENT, f"User-Agent con el contacto configurado: {USER_AGENT}")
    else:
        verificar(USER_AGENT.startswith(f"SOME-prototipo-INOCAR/{cfg.VERSION} ") and "ESPOL-INOCAR" in USER_AGENT
                  and "@" not in USER_AGENT and "SOME_CHIRPS_CONTACTO" in USER_AGENT,
                  f"User-Agent sin correo por defecto (pide definir SOME_CHIRPS_CONTACTO): {USER_AGENT}")
    try:
        USER_AGENT.encode("ascii")
        verificar(True, "User-Agent en ASCII (válido como cabecera HTTP)")
    except UnicodeEncodeError:
        verificar(False, "User-Agent en ASCII (válido como cabecera HTTP)")
    verificar(PETICIONES_POR_SEGUNDO <= 1.0 and TRABAJADORES == 1,
              "como máximo 1 petición/s y descargas del CHC en serie")

    with entorno_simulado() as ent:
        reloj, sesion = ent["reloj"], ent["sesion"]
        for _ in range(5):
            red._esperar_turno()
        verificar(abs(reloj.dormido - 4.0) < 1e-6, "limitador: 5 peticiones seguidas esperan 4 s en total")

        # Disyuntor
        try:
            red.obtener("https://chc.simulado/a")
            verificar(False, "CHC caído → ErrorConexion")
        except red.ErrorDisyuntor:
            verificar(False, "CHC caído → ErrorConexion (no disyuntor al primer pedido)")
        except red.ErrorConexion:
            verificar(sesion.llamadas == FALLOS_DISYUNTOR, f"CHC caído → ErrorConexion tras {sesion.llamadas} intentos")
        verificar(red.disyuntor_abierto(), f"disyuntor abierto tras {FALLOS_DISYUNTOR} fallos seguidos")
        try:
            red.obtener("https://chc.simulado/b")
            verificar(False, "con el disyuntor abierto se falla sin tocar la red")
        except red.ErrorDisyuntor:
            verificar(sesion.llamadas == FALLOS_DISYUNTOR, "con el disyuntor abierto se falla sin tocar la red")
        try:
            descarga.disponibilidad(forzar=True)
            verificar(False, "disponibilidad del CHC falla al instante con el disyuntor abierto")
        except red.ErrorConexion:
            verificar(sesion.llamadas == FALLOS_DISYUNTOR,
                      "disponibilidad del CHC falla al instante con el disyuntor abierto")
        n_verif = len(estado.datos()["verificaciones"])
        ok, _lat, err = sincronizacion.verificar_servidor()
        verificar(not ok and "caído" in err and sesion.llamadas == FALLOS_DISYUNTOR
                  and len(estado.datos()["verificaciones"]) == n_verif,
                  "la verificación no sondea el CHC mientras el disyuntor está abierto")
        dis = estado.datos().get("disyuntor_chc") or {}
        verificar(dis.get("abierto") is True and dis.get("hasta") and estado.chc_accesible() is False,
                  "apertura del disyuntor persistida en estado.json")
        # Otro proceso (sin disyuntor propio) respeta el disyuntor persistido
        guardado_proceso = (red._abierto_hasta, red._fallos_seguidos)
        red._abierto_hasta, red._fallos_seguidos = 0.0, 0
        try:
            red.obtener("https://chc.simulado/otro-proceso")
            verificar(False, "otro proceso respeta el disyuntor persistido (no toca la red)")
        except red.ErrorDisyuntor as ex:
            verificar(sesion.llamadas == FALLOS_DISYUNTOR and "otra ejecución" in str(ex),
                      "otro proceso respeta el disyuntor persistido (no toca la red)")
        red._abierto_hasta, red._fallos_seguidos = guardado_proceso

        reloj.avanzar(DISYUNTOR_S + 1)
        verificar(not red.disyuntor_abierto(), "a los 15 min se permite un intento de prueba")
        try:
            red.obtener("https://chc.simulado/c")
        except red.ErrorConexion:
            pass
        verificar(sesion.llamadas == FALLOS_DISYUNTOR + 1 and red.disyuntor_abierto(),
                  "si el intento de prueba falla, se vuelve a abrir de inmediato (1 sola petición)")
        # Otro proceso (servidor reiniciado, script, prueba) que encuentra el disyuntor persistido ya
        # vencido y sin éxito posterior: su primer intento es el de prueba; si falla, lo reabre sin
        # gastar los 3 intentos de un CHC «sano».
        reloj.avanzar(DISYUNTOR_S + 1)
        verificar(estado.disyuntor_en_prueba(red._reloj_pared()), "disyuntor persistido vencido → en prueba")
        red._abierto_hasta, red._fallos_seguidos = 0.0, 0
        antes = sesion.llamadas
        try:
            red.obtener("https://chc.simulado/prueba-otro-proceso")
        except red.ErrorConexion:
            pass
        verificar(sesion.llamadas == antes + 1 and red.disyuntor_abierto()
                  and not estado.disyuntor_en_prueba(red._reloj_pared()),
                  "otro proceso: el intento de prueba fallido reabre el disyuntor (1 sola petición)")
        reloj.avanzar(DISYUNTOR_S + 1)
        sesion.modo = "ok"
        contenido = red.obtener("https://chc.simulado/d")
        e = red.estado_disyuntor()
        verificar(len(contenido) == 512 and not e["abierto"] and e["fallos_seguidos"] == 0,
                  "cuando el CHC responde, el disyuntor se cierra y se olvidan los fallos")
        verificar((estado.datos().get("disyuntor_chc") or {}).get("abierto") is False
                  and estado.chc_accesible() is True, "cierre del disyuntor persistido")

        red.reiniciar_disyuntor()
        sesion.modo, sesion.llamadas = "429", 0
        try:
            red.obtener("https://chc.simulado/e")
        except red.ErrorConexion:
            pass
        verificar(sesion.llamadas == 1 and red.disyuntor_abierto(), "un HTTP 429 abre el disyuntor de inmediato")

    # Limitador real entre hilos (intervalo reducido para no alargar la prueba). Se comprueban los
    # turnos que reserva el limitador (lo que él controla) y que ningún hilo sale antes de su turno.
    # La hora real al despertar depende además del planificador: en un equipo con la CPU saturada un
    # hilo puede despertar tarde y quedar pegado al siguiente, así que no se exige su separación.
    original = red.PETICIONES_POR_SEGUNDO
    red.PETICIONES_POR_SEGUNDO = 20.0
    red._proxima_peticion = 0.0
    marcas: list[tuple[float, float]] = []      # (turno reservado, hora al despertar)
    bloqueo = threading.Lock()
    local = threading.local()
    pared_original, reservar_original = red._reloj_pared, red._reservar_turno_compartido

    def pared():
        local.ahora = pared_original()
        return local.ahora

    def reservar(espera_minima, intervalo):
        espera = reservar_original(espera_minima, intervalo)
        local.turno = local.ahora.timestamp() + espera          # lo que se escribe en ritmo_chc.txt
        return espera

    def trabajo():
        for _ in range(3):
            local.turno = float("nan")
            red._esperar_turno()
            with bloqueo:
                marcas.append((local.turno, time.time()))

    try:
        red._reloj_pared, red._reservar_turno_compartido = pared, reservar
        hilos = [threading.Thread(target=trabajo) for _ in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
    finally:
        red._reloj_pared, red._reservar_turno_compartido = pared_original, reservar_original
        red.PETICIONES_POR_SEGUNDO = original
        red._proxima_peticion = 0.0
    turnos = sorted(t for t, _ in marcas)
    validos = len(marcas) == 12 and all(t == t for t in turnos)
    separacion = min(b - a for a, b in zip(turnos, turnos[1:])) if validos else 0.0
    adelanto = max(t - d for t, d in marcas) if validos else 1.0
    verificar(validos and turnos[-1] - turnos[0] >= 11 * 0.05 - 0.001 and separacion >= 0.0495 and adelanto <= 0.002,
              f"limitador global entre 4 hilos: 12 turnos espaciados ≥ 0.05 s (mínimo {cfg.fmt_num(separacion, 3)} s) "
              "y ninguno sale antes de su turno")


# ---------------------------------------------------------------------------
# Conmutación entre el CHC y la réplica
# ---------------------------------------------------------------------------
_COB_FINAL = (date(2026, 1, 1), date(2026, 9, 30))
_COB_PRELIM = (date(2026, 1, 1), date(2026, 10, 20))


def _disp(finales: list[date], prelims: list[date]) -> dict:
    return {"final": set(finales), "preliminar": set(prelims),
            "ultimo_final": max(finales) if finales else None,
            "ultimo_preliminar": max(prelims) if prelims else None}


def prueba_conmutacion() -> None:
    from . import almacen, descarga, estado, resumen
    from .constantes import AUSENTE, CHC, ESPEJO, FINAL, PRELIMINAR
    print("Conmutación CHC ↔ réplica de dynamical.org (simulada, sin red):")
    with entorno_simulado() as ent:
        sesion = ent["sesion"]
        espejo = EspejoFalso(_COB_FINAL, _COB_PRELIM)
        descarga.modulo_espejo = lambda: espejo
        cob = descarga.cobertura_espejo(forzar=True)
        verificar(descarga.espejo_accesible(cob) and cob["final"] == _COB_FINAL,
                  "cobertura de la réplica normalizada y accesible")
        disp = _disp(_dias(date(2026, 9, 1), date(2026, 9, 30)), _dias(date(2026, 9, 15), date(2026, 10, 25)))
        ini, fin = date(2026, 9, 25), date(2026, 10, 25)

        p = descarga.plan_llenado(ini, fin, disp, cob, "auto")
        verificar(len(p) == 31 and p[date(2026, 9, 25)] == (FINAL, ESPEJO)
                  and p[date(2026, 10, 5)] == (PRELIMINAR, ESPEJO) and p[date(2026, 10, 22)] == (PRELIMINAR, CHC),
                  "regla: lo que cubre la réplica → réplica; lo más reciente publicado → CHC")
        p = descarga.plan_llenado(ini, fin, None, cob, "auto")
        verificar(len(p) == 26 and all(o == ESPEJO for _t, o in p.values()),
                  "CHC caído: todo lo que cubre la réplica sale de la réplica")
        cob_mal = {**cob, "error": "sin conexión (simulada)", "final": None, "preliminar": None}
        p = descarga.plan_llenado(ini, fin, disp, cob_mal, "auto")
        verificar(len(p) == 31 and all(o == CHC for _t, o in p.values()),
                  "réplica caída: lo que publica el CHC sale del CHC")
        verificar(descarga.plan_llenado(ini, fin, None, cob_mal, "auto") == {},
                  "ambas caídas: no se planifica nada (se sirve la caché)")
        verificar(all(o == CHC for _t, o in descarga.plan_llenado(ini, fin, disp, cob, CHC).values())
                  and all(o == ESPEJO for _t, o in descarga.plan_llenado(ini, fin, disp, cob, ESPEJO).values()),
                  "--fuente chc / espejo restringen la fuente")

        # Ejecución con el CHC cayéndose a mitad de camino
        r = descarga.ejecutar_plan(descarga.plan_llenado(ini, fin, disp, cob, "auto"), disp=disp, cob=cob)
        verificar(r["por_origen"][ESPEJO] == {"final": 6, "preliminar": 20} and len(espejo.llamadas) == 2,
                  "réplica: 26 días en 2 lecturas (final y preliminar, un fragmento cada una)")
        verificar(r["chc_fallo"] and r["interrumpida"] and sesion.llamadas == 3 and r["pospuestos_chc"] == 4,
                  "CHC caído: se interrumpe tras el primer día (3 intentos) y se pospone el resto")
        to = almacen.tipos_y_origenes(ini, fin)
        verificar(to[date(2026, 9, 25)] == (FINAL, ESPEJO) and to[date(2026, 10, 10)] == (PRELIMINAR, ESPEJO)
                  and to[date(2026, 10, 22)] == (AUSENTE, ""), "días guardados con su origen")

        # El CHC vuelve: descarga simulada de un día
        llamadas_chc: list[tuple[date, int]] = []

        def descargar_falso(d, t, espera_max=None):
            llamadas_chc.append((d, t))
            return np.full((49, 69), 7.0, dtype=np.float32)

        descarga.descargar_dia = descargar_falso
        from . import red
        red.reiniciar_disyuntor()
        p = descarga.plan_llenado(ini, fin, disp, cob, "auto")
        verificar(sorted(p) == _dias(date(2026, 10, 21), date(2026, 10, 25))
                  and all(v == (PRELIMINAR, CHC) for v in p.values()),
                  "los días ya guardados del mismo tipo no se vuelven a pedir")
        descarga.ejecutar_plan(p, disp=disp, cob=cob)
        verificar(almacen.tipos_y_origenes(date(2026, 10, 21), date(2026, 10, 21))[date(2026, 10, 21)]
                  == (PRELIMINAR, CHC), "lo más reciente se guarda con origen «chc»")

        # Preliminares → finales cuando cualquiera de las dos publica el final
        espejo.cob["final"] = (date(2026, 1, 1), date(2026, 10, 10))
        cob2 = descarga.cobertura_espejo(forzar=True)
        disp2 = _disp(_dias(date(2026, 9, 1), date(2026, 10, 21)), _dias(date(2026, 9, 15), date(2026, 10, 25)))
        p = descarga.plan_llenado(ini, fin, disp2, cob2, "auto")
        verificar(p.get(date(2026, 10, 1)) == (FINAL, ESPEJO) and p.get(date(2026, 10, 15)) == (FINAL, CHC)
                  and p.get(date(2026, 10, 21)) == (FINAL, CHC) and date(2026, 10, 22) not in p,
                  "preliminar → final desde la réplica o desde el CHC, según quién lo publique")
        n_chc = len(llamadas_chc)
        descarga.ejecutar_plan(p, disp=disp2, cob=cob2)
        to = almacen.tipos_y_origenes(date(2026, 10, 1), date(2026, 10, 22))
        verificar(to[date(2026, 10, 1)] == (FINAL, ESPEJO) and to[date(2026, 10, 15)] == (FINAL, CHC)
                  and to[date(2026, 10, 22)] == (PRELIMINAR, CHC) and len(llamadas_chc) - n_chc == 11,
                  "preliminares reemplazados por finales con su nuevo origen")

        # La réplica falla en un bloque → esos días pasan al CHC si los publica
        espejo.falla = True
        disp3 = _disp(_dias(date(2026, 8, 1), date(2026, 10, 21)), _dias(date(2026, 9, 15), date(2026, 10, 25)))
        p = descarga.plan_llenado(date(2026, 8, 1), date(2026, 8, 5), disp3, cob2, "auto")
        r = descarga.ejecutar_plan(p, disp=disp3, cob=cob2)
        verificar(r["espejo_fallo"] and r["por_origen"][CHC]["final"] == 5
                  and almacen.tipos_y_origenes(date(2026, 8, 3), date(2026, 8, 3))[date(2026, 8, 3)] == (FINAL, CHC),
                  "si la réplica falla, los días que publica el CHC salen del CHC")
        espejo.falla = False

        # Estado RF-21 según las fuentes
        estado.registrar_verificacion(False, 0, "tiempo de espera agotado (simulado)")
        descarga.cobertura_espejo(forzar=True)
        e = estado.estado_actual()
        verificar(e["estado"] == "degradada" and e["origen"] == ESPEJO
                  and "El servidor del CHC no responde" in e["mensaje"]
                  and "réplica de dynamical.org (final hasta 10/10/2026, preliminar hasta 20/10/2026)" in e["mensaje"]
                  and "preliminar" in e["mensaje"].split(").", 1)[-1],
                  "CHC caído + réplica accesible → «degradada» con la cobertura de la réplica y los preliminares")
        espejo.error = "la réplica no responde (simulado)"
        descarga.cobertura_espejo(forzar=True)
        e = estado.estado_actual()
        verificar(e["estado"] == "sin_conexion" and "ni con la réplica" in e["mensaje"] and e["origen"] == "cache",
                  "ninguna fuente accesible → «sin_conexion» (se sirve la caché)")
        espejo.error = ""
        descarga.cobertura_espejo(forzar=True)
        estado.registrar_sincronizacion({"fin": "2026-10-29T12:00:00", "errores": 0, "detalle_errores": [], "bytes": 0})
        estado.registrar_verificacion(True, 420)
        e = estado.estado_actual()
        verificar(e["estado"] == "operativa" and e["latencia_ms"] == 420 and "normalidad" in e["mensaje"]
                  and e["origen"] == "chc+espejo", "CHC accesible, rápido y sin errores → «operativa»")
        estado.registrar_verificacion(True, 3500)
        verificar(estado.estado_actual()["estado"] == "degradada", "CHC lento (≥ 3000 ms) → «degradada»")
        estado.registrar_verificacion(True, 400)
        estado.registrar_sincronizacion({"fin": "2026-10-29T12:05:00", "errores": 2,
                                         "detalle_errores": ["a", "b"], "bytes": 0})
        e = estado.estado_actual()
        verificar(e["estado"] == "degradada" and "2 errores" in e["mensaje"], "errores parciales → «degradada»")
        estado.marcar_verificando(True)
        verificar(estado.estado_actual()["mensaje"].startswith("Verificando"), "verificación en curso indicada")
        estado.marcar_verificando(False)
        h = estado.historial_estado(3)
        verificar(h[-1]["estado"] == "sin_conexion" and h[-1]["origen"] == "chc+espejo"
                  and h[0]["origen"] is None and h[-1]["origen_texto"],
                  "historial con el peor estado del día y las fuentes que respondieron")
        res = resumen()
        verificar(res["dias_por_origen"][ESPEJO] > 0 and res["dias_por_origen"][CHC] > 0
                  and {"chc", "espejo"} <= set(res["fuentes"]) and "abierto" in res["disyuntor_chc"]
                  and res["fuentes"]["espejo"]["accesible"] is True,
                  "resumen(): días por origen, estado de cada fuente y disyuntor del CHC")


def prueba_sincronizacion() -> None:
    from . import climatologia, descarga, estado, sincronizacion
    from .constantes import CHC, ESPEJO, INTERVALO_REINTENTO_S
    print("Ciclo de sincronización con el CHC caído (simulado, sin red):")
    faltantes_original = climatologia.anios_faltantes
    with entorno_simulado() as ent:
        sesion = ent["sesion"]
        espejo = EspejoFalso(_COB_FINAL, _COB_PRELIM)
        descarga.modulo_espejo = lambda: espejo
        climatologia.anios_faltantes = lambda *a, **k: []
        try:
            r = sincronizacion.ciclo()
            verificar(r is not None and r["por_origen"][ESPEJO] == {"final": 273, "preliminar": 20}
                      and r["por_origen"][CHC] == {"final": 0, "preliminar": 0} and not r["chc_accesible"],
                      "con el CHC caído, el ciclo llena desde la réplica todo lo que cubre (293 días)")
            verificar(sesion.llamadas == 3 and len(espejo.llamadas) == 2,
                      "al CHC solo se le pidió la verificación (3 intentos); a la réplica, 2 lecturas")
            verificar(sincronizacion.proxima_espera_s() == INTERVALO_REINTENTO_S,
                      "si una fuente falló, el próximo ciclo es a los 30 min")
            e = estado.estado_actual()
            verificar(e["estado"] == "degradada" and "réplica de dynamical.org" in e["mensaje"],
                      f"estado tras el ciclo: «{e['estado']}»")
            r2 = sincronizacion.ciclo()
            verificar(sesion.llamadas == 3 and r2["planificados"] == 0,
                      "segundo ciclo dentro de los 15 min: no se sondea el CHC ni se repite lo guardado")
            sincronizacion.detener_sincronizacion()
            os.environ["SOME_CHIRPS_SIN_SINCRONIZACION"] = "1"
            sincronizacion.iniciar_sincronizacion()
            verificar(not sincronizacion.sincronizando(),
                      "SOME_CHIRPS_SIN_SINCRONIZACION=1 no arranca el hilo de sincronización")
        finally:
            os.environ.pop("SOME_CHIRPS_SIN_SINCRONIZACION", None)
            climatologia.anios_faltantes = faltantes_original


def prueba_bajo_demanda() -> None:
    from ..conectores.base import ParametrosConsulta
    from ..conectores.chirps import ConectorCHIRPS
    from . import almacen, descarga, estado
    from .constantes import CHC, FINAL, PRELIMINAR
    print("Llenado bajo demanda del conector (simulado, sin red):")
    with entorno_simulado():
        espejo = EspejoFalso(_COB_FINAL, _COB_PRELIM)
        descarga.modulo_espejo = lambda: espejo
        llamadas_chc: list[date] = []

        def descargar_falso(d, t, espera_max=None):
            llamadas_chc.append(d)
            return np.full((49, 69), 7.0, dtype=np.float32)

        descarga.descargar_dia = descargar_falso
        campo = np.full((49, 69), 3.0, dtype=np.float32); campo[0, 0] = np.nan
        almacen.guardar({d: (campo, PRELIMINAR) for d in _dias(date(2026, 10, 21), date(2026, 10, 25))}, origen=CHC)
        estado.registrar_verificacion(False, 0, "CHC caído (simulado)")
        c = ConectorCHIRPS()
        ds = c.obtener(ParametrosConsulta("precip", (-81.5, -5.0, -75.0, 2.0), date(2026, 9, 10), date(2026, 10, 25)))
        a = ds.attrs
        verificar(ds.sizes["time"] == 46 and a["dias_faltantes"] == 0 and len(espejo.llamadas) == 2,
                  "días ausentes llenados desde la réplica (2 lecturas, ningún faltante)")
        verificar(not llamadas_chc, "con el CHC marcado caído la consulta no lo sondea")
        verificar(a["dias_origen_replica"] == 41 and a["dias_origen_chc"] == 5
                  and a["origen_datos"] == "servidor del CHC: 5 días; réplica dynamical.org: 41 días",
                  f"atributo origen_datos: «{a['origen_datos']}»")
        verificar("dynamical.org" in a.get("credito_replica", "") and "dynamical.org" in a["credito"]
                  and "CC BY 4.0" in a["credito"] and "Origen de los días de esta consulta" in a["procesamiento"]
                  and "dynamical.org" in a["source"], "crédito de la réplica y procesamiento con el origen")
        verificar(a["comment"] == c.insignia == cfg.INSIGNIA_REALES_CHIRPS and a["datos_reales"] == 1,
                  "la insignia sigue siendo la de datos reales de CHIRPS")
        verificar("dynamical.org" in c.credito, "el crédito del conector menciona la réplica cuando se usó")
        ds2 = c.obtener(ParametrosConsulta("precip", (-81.5, -5.0, -75.0, 2.0), date(2026, 10, 21), date(2026, 10, 25)))
        verificar(ds2.attrs["origen_datos"] == "servidor del CHC: 5 días" and "credito_replica" not in ds2.attrs
                  and "dynamical.org" not in ds2.attrs["credito"], "consulta solo con días del CHC: sin crédito de réplica")

        # CHC accesible: lo que la réplica no cubre se pide al CHC (en serie)
        almacen.guardar({date(2026, 10, 28): (campo, PRELIMINAR)}, origen=CHC)
        estado.registrar_verificacion(True, 300)
        descarga._disp_anios[2026] = (time.monotonic(), set(_dias(date(2026, 1, 1), date(2026, 9, 30))),
                                      set(_dias(date(2026, 1, 1), date(2026, 10, 28))))
        c.obtener(ParametrosConsulta("precip", (-81.5, -5.0, -75.0, 2.0), date(2026, 10, 20), date(2026, 10, 28)))
        verificar(sorted(llamadas_chc) == _dias(date(2026, 10, 26), date(2026, 10, 27))
                  and almacen.tipos(date(2026, 10, 26), date(2026, 10, 27))[date(2026, 10, 26)] == PRELIMINAR,
                  "días posteriores a la réplica: del CHC bajo demanda")
        verificar(almacen.tipos(date(2026, 9, 10), date(2026, 9, 10))[date(2026, 9, 10)] == FINAL,
                  "la caché conserva lo llenado")


# ---------------------------------------------------------------------------
# Correcciones de la revisión (sin red, carpetas temporales y procesos hijos)
# ---------------------------------------------------------------------------
_RAIZ = Path(__file__).resolve().parents[2]

_HIJO_ALMACEN = r"""
import sys
from datetime import date
from pathlib import Path
import numpy as np
from backend.chirps import almacen
almacen.RUTA_DIARIO = Path(sys.argv[1]) / "diario"
for d in range(int(sys.argv[2]), int(sys.argv[3]) + 1):
    almacen.guardar({date(2025, 3, d): (np.full((49, 69), float(d), np.float32), 2)}, origen="espejo")
"""

_HIJO_ESTADO = r"""
import sys
from pathlib import Path
from backend.chirps import estado
estado.RUTA_ESTADO = Path(sys.argv[1]) / "estado.json"
for _ in range(int(sys.argv[2])):
    estado.sumar_bytes(1)
"""

_HIJO_RITMO = r"""
import json, sys, time
from pathlib import Path
from backend.chirps import estado, red
estado.RUTA_ESTADO = Path(sys.argv[1]) / "estado.json"
red.PETICIONES_POR_SEGUNDO = 5.0
pared, reservar, ultimo = red._reloj_pared, red._reservar_turno_compartido, {}
def _pared():
    ultimo["ahora"] = pared()
    return ultimo["ahora"]
def _reservar(espera_minima, intervalo):
    espera = reservar(espera_minima, intervalo)
    ultimo["turno"] = ultimo["ahora"].timestamp() + espera    # lo que se escribe en ritmo_chc.txt
    return espera
red._reloj_pared, red._reservar_turno_compartido = _pared, _reservar
marcas = []
for _ in range(5):
    ultimo["turno"] = float("nan")
    red._esperar_turno()
    marcas.append([ultimo["turno"], time.time()])               # (turno reservado, hora al despertar)
print(json.dumps(marcas))
"""

_HIJO_ARCHIVO = r"""
import sys, time
from pathlib import Path
from backend.chirps import estado, red
estado.RUTA_ESTADO = Path(sys.argv[1]) / "estado.json"
with red.un_archivo():
    print("dentro", flush=True)
    time.sleep(float(sys.argv[2]))
"""


def _hijo(codigo: str, *args) -> subprocess.Popen:
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.Popen([sys.executable, "-c", codigo, *map(str, args)], cwd=_RAIZ, env=entorno,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")


def prueba_correcciones() -> None:
    from .. import cache, motor
    from ..conectores.base import ParametrosConsulta
    from ..conectores.chirps import CREDITO_REPLICA, ConectorCHIRPS, credito_para_origen
    from . import almacen, climatologia, constantes, descarga, estado, red
    from .constantes import AUSENTE, CHC, ESPEJO, FINAL, PRELIMINAR
    print("Correcciones de la revisión (sin red):")
    campo = np.full((49, 69), 2.0, dtype=np.float32); campo[0, 0] = np.nan

    with entorno_simulado() as ent:
        tmp = ent["tmp"]
        # --- Finales «p25» de versiones anteriores: provisionales y reemplazables ---
        n = 30
        tipo = np.zeros(n, np.int8); tipo[:5] = FINAL
        valores = np.full((n, 49, 69), np.nan, np.float32); valores[:5] = 9.0
        ruta = almacen.ruta_mes(2026, 9)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(ruta, valores=valores, tipo=tipo, obtenido=np.array([""] * n, dtype="U19"),
                            origen=np.where(tipo > 0, constantes.ORIGEN_CHC, 0).astype(np.int8))
        almacen._memoria.clear()
        tc = almacen.tipos_y_codigos(date(2026, 9, 1), date(2026, 9, 1))[date(2026, 9, 1)]
        verificar(tc == (FINAL, constantes.ORIGEN_CHC_P25)
                  and almacen.tipos_y_origenes(date(2026, 9, 1), date(2026, 9, 1))[date(2026, 9, 1)] == (FINAL, CHC)
                  and almacen.conteo(date(2026, 9, 1), date(2026, 9, 30))["finales_provisionales"] == 5,
                  "final del CHC en un npz anterior (sin «formato»): final provisional «p25», contado como CHC")
        cob = {"final": (date(2026, 1, 1), date(2026, 9, 30)), "preliminar": None, "error": ""}
        plan = descarga.plan_llenado(date(2026, 9, 1), date(2026, 9, 5), None, cob, ESPEJO)
        verificar(plan == {date(2026, 9, d): (FINAL, ESPEJO) for d in range(1, 6)},
                  "la sincronización planifica reemplazar los finales provisionales por el final de la réplica")
        verificar(not almacen.mejora_dia((FINAL, constantes.ORIGEN_ESPEJO), FINAL, constantes.ORIGEN_CHC)
                  and almacen.mejora_dia((FINAL, constantes.ORIGEN_CHC_P25), FINAL, constantes.ORIGEN_CHC)
                  and not almacen.mejora_dia((FINAL, constantes.ORIGEN_CHC_P25), PRELIMINAR, constantes.ORIGEN_CHC),
                  "regla: un final provisional solo se reemplaza por otro final (réplica o CHC a 0.05°)")
        almacen.guardar({date(2026, 9, 1): (campo, FINAL)}, origen=ESPEJO)
        tc = almacen.tipos_y_codigos(date(2026, 9, 1), date(2026, 9, 2))
        with np.load(ruta) as z:
            formato = int(z["formato"]) if "formato" in z.files else None
        verificar(tc[date(2026, 9, 1)] == (FINAL, constantes.ORIGEN_ESPEJO)
                  and tc[date(2026, 9, 2)] == (FINAL, constantes.ORIGEN_CHC_P25) and formato == 2,
                  "reemplazado desde la réplica; los demás siguen provisionales al reescribir el mes (formato 2)")
        verificar(constantes.URL_FINAL_ANIO.endswith("/daily/final/rnl/{anio:04d}/") and "p25" not in
                  constantes.url_final(date(2026, 9, 1)),
                  "el final del CHC se lee a 0.05° (no del producto p25)")

        # --- Archivos dañados ---
        dañado = almacen.ruta_mes(2025, 6)
        dañado.write_bytes(b"PK\x03\x04 truncado")
        almacen._memoria.clear()
        v, t = almacen.leer(date(2025, 6, 1), date(2025, 6, 3))
        verificar(list(t) == [0, 0, 0] and not dañado.exists()
                  and (dañado.parent / "2025-06.npz.danado").exists(),
                  "npz diario truncado: se lee como ausente y se aparta como .danado")
        verificar(almacen.guardar({date(2025, 6, 2): (campo, FINAL)}, origen=ESPEJO) == 1
                  and almacen.tipos(date(2025, 6, 2), date(2025, 6, 2))[date(2025, 6, 2)] == FINAL,
                  "el mes dañado se puede volver a llenar")
        vacio = almacen.ruta_mes(2025, 7)
        vacio.write_bytes(b"")
        almacen._memoria.clear()
        verificar(almacen.tipos(date(2025, 7, 1), date(2025, 7, 1))[date(2025, 7, 1)] == AUSENTE,
                  "npz diario de 0 bytes: ausente, sin excepción")
        try:
            descarga.plan_llenado(cfg.FECHA_INICIO, date(2026, 10, 1), None, cob, ESPEJO)
            verificar(True, "plan_llenado de toda la demo no se cae por un mes dañado")
        except Exception as e:  # noqa: BLE001
            verificar(False, f"plan_llenado de toda la demo no se cae por un mes dañado ({e})")
        almacen.RUTA_MENSUAL.mkdir(parents=True, exist_ok=True)
        almacen.guardar_mensual(1995, np.full((12, 49, 69), 3.0, np.float32))
        almacen.ruta_anio_mensual(1996).write_bytes(b"\x00" * 64)
        almacen._memoria_mensual.clear()
        verificar(climatologia.anios_faltantes(1995, 1996) == [1996] and almacen.leer_mensual(1996) is None,
                  "año de climatología ilegible: cuenta como faltante (no rompe np.stack)")

        # --- Mensajes en español ---
        crudo = ("No se pudo conectar con el servidor del CHC (https://data.chc.ucsb.edu/x): HTTPSConnectionPool("
                 "host='data.chc.ucsb.edu', port=443): Max retries exceeded (Caused by ConnectTimeoutError("
                 "<urllib3.connection.HTTPSConnection object at 0x00000146D72ECCD0>, 'timed out'))")
        verificar(estado.legible(crudo) == "No se pudo conectar con el servidor del CHC: tiempo de espera agotado "
                                           "al conectar.", f"mensaje técnico traducido: «{estado.legible(crudo)}»")
        t = estado.legible("Réplica: No se pudieron leer los datos de la réplica de CHIRPS (producto final): "
                           "ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)")
        verificar("ExceptionGroup" not in t and t.endswith("sin conexión."), f"error de icechunk legible: «{t}»")
        verificar(estado.acortar("palabra " * 40, 50).endswith("palabra…"), "acortar no corta palabras")

        # --- Crédito según la mezcla real de orígenes ---
        verificar(credito_para_origen(56, 62)[0] == CREDITO_REPLICA["parte"]
                  and credito_para_origen(0, 62)[0] == CREDITO_REPLICA["todos"]
                  and credito_para_origen(5, 0, True)[0] == CREDITO_REPLICA["climatologia"]
                  and not credito_para_origen(5, 0)[1], "crédito: todos / parte / climatología / CHC")
        import xarray as xr
        ds_rep = xr.Dataset(attrs={"dias_origen_chc": 0, "dias_origen_replica": 31,
                                   "credito": CREDITO_REPLICA["todos"], "credito_replica": "réplica"})
        ds_chc = xr.Dataset(attrs={"dias_origen_chc": 25, "dias_origen_replica": 0, "credito": "CHC"})
        o = motor.origen_real([ds_rep, ds_chc])
        verificar(o["credito"] == CREDITO_REPLICA["parte"] and o["origen_datos"].startswith("servidor del CHC: 25"),
                  "origen_real recalcula el crédito con la suma de los Dataset (mezcla → «parte de los días»)")

        # --- Acumulado frente a lo normal: lo normal solo de los días con valor ---
        a = motor.acumulado_y_normal([10.0, None, 5.0], [2.0, 4.4, 3.0])
        verificar(a["acumulado"] == 15.0 and a["normal"] == 5.0 and a["anomalia"] == 10.0 and a["dias"] == 2
                  and a["dias_sin_dato"] == 1, "acumulado_y_normal no suma lo normal del día sin dato")
        verificar(motor.tendencia_por_anio(pd_serie(90)) is None and motor.tendencia_por_anio(pd_serie(800)) is not None,
                  "tendencia «por año» solo con al menos 2 años de datos")

        # --- Mapa de un periodo: celdas incompletas y acumulado ---
        dias_oct = _dias(date(2026, 10, 1), date(2026, 10, 10))
        lote = {}
        for k, d in enumerate(dias_oct):
            c2 = campo.copy()
            if k >= 2:
                c2[20, 40] = np.nan                 # celda con dato solo 2 de 10 días
            lote[d] = (c2, FINAL, ESPEJO)
        almacen.guardar(lote)
        m = motor.consulta_mapa({"fuente": "chirps_v3", "variable": "precip",
                                 "area": {"tipo": "bbox", "bbox": [-85.0, -5.0, -78.0, 1.0]},
                                 "periodo": {"inicio": "2026-10-01", "fin": "2026-10-10"},
                                 "incluir_acumulado": True})
        i, j = m["lats"].index(float(cfg.LAT[20])), m["lons"].index(float(cfg.LON[40]))
        verificar(m["valores"][i][j] is None and "1 celda quedó sin dato" in m["metodo"],
                  "mapa de un periodo: una celda con 2 de 10 días queda sin dato (y el método lo dice)")
        acum = m.get("acumulado") or {}
        verificar(acum.get("dias_con_dato") == 10 and acum["valores"][i][j] is None
                  and abs(acum["valores"][i][j + 1] - 20.0) < 1e-4,
                  "acumulado = suma de los días con dato, solo en celdas completas (no media × días)")

        # --- Caché de respuestas: firma de CHIRPS, sin días faltantes y una sola vez ---
        cache.cache_consultas.limpiar()
        llamadas = []

        def calcular():
            llamadas.append(1)
            time.sleep(0.2)
            return {"x": len(llamadas), "dias_faltantes": 0}

        hilos = [threading.Thread(target=lambda: cache.consultar("prueba", {"fuente": "chirps_v3"}, calcular))
                 for _ in range(4)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        verificar(len(llamadas) == 1, f"4 peticiones idénticas simultáneas: se calcula {len(llamadas)} vez")
        cache.consultar("prueba", {"fuente": "chirps_v3"}, calcular)
        f1 = cache.firma_fuentes_reales()
        almacen.guardar({date(2026, 10, 11): (campo, PRELIMINAR)}, origen=CHC)
        verificar(cache.firma_fuentes_reales() != f1, "la firma de CHIRPS cambia al guardar un día")
        cache.consultar("prueba", {"fuente": "chirps_v3"}, calcular)
        verificar(len(llamadas) == 2, "tras guardar un día, la misma consulta se recalcula (no se sirve rancia)")
        cache.consultar("prueba2", {"fuente": "chirps_v3"}, lambda: {"dias_faltantes": 3})
        verificar(cache.cache_consultas.estadisticas()["entradas"] == 2,
                  "una respuesta con días faltantes no se guarda en la caché")
        cache.cache_consultas.limpiar()

        # --- Réplica que falla al leer: marcada caída unos minutos ---
        espejo = EspejoFalso(_COB_FINAL, _COB_PRELIM)
        descarga.modulo_espejo = lambda: espejo
        cob_e = descarga.cobertura_espejo(forzar=True)
        espejo.falla = True
        descarga.ejecutar_plan({date(2026, 5, 3): (FINAL, ESPEJO)}, fuente=ESPEJO, cob=cob_e)
        verificar(descarga.espejo_caido_reciente() and estado.datos()["espejo"]["accesible"] is False,
                  "un fallo de lectura marca la réplica caída y lo registra en el estado")
        n_antes = len(espejo.llamadas)
        ConectorCHIRPS()._completar(date(2026, 5, 1), date(2026, 5, 5))
        verificar(len(espejo.llamadas) == n_antes, "mientras está marcada caída, las consultas no la reintentan")
        espejo.falla = False
        descarga.cobertura_espejo(forzar=True)
        verificar(not descarga.espejo_caido_reciente(), "una cobertura forzada que responde limpia la marca")

        # --- Bajo demanda: fragmentos completos de la réplica ---
        tramos = descarga.ampliar_a_fragmentos(date(2026, 5, 1), date(2026, 5, 5), cob_e, minimo=cfg.FECHA_INICIO)
        verificar(tramos == [(date(2026, 1, 1), date(2026, 9, 30))],
                  f"la consulta de 5 días lee el fragmento completo de la réplica: {tramos}")

        # --- CHC: dos ejecuciones simultáneas del mismo plan no piden dos veces el mismo día ---
        pedidos: list[date] = []

        def descargar_falso(d, t, espera_max=None):
            pedidos.append(d)
            time.sleep(0.05)
            return np.full((49, 69), 7.0, dtype=np.float32)

        descarga.descargar_dia = descargar_falso
        plan_chc = {d: (PRELIMINAR, CHC) for d in _dias(date(2026, 10, 26), date(2026, 10, 28))}
        disp = _disp([], _dias(date(2026, 10, 1), date(2026, 10, 28)))
        hilos = [threading.Thread(target=descarga.ejecutar_plan, args=(plan_chc,),
                                  kwargs={"disp": disp, "fuente": CHC}) for _ in range(2)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        verificar(sorted(pedidos) == sorted(plan_chc), f"cada GeoTIFF del CHC se pidió una vez ({len(pedidos)} de 3)")

        # --- Climatología: un bloqueo por año y nada de 30 años dentro de una consulta ---
        llamadas_anio: list[int] = []
        original = climatologia.espejo.leer_mensual_anio, climatologia.espejo.disponible, climatologia.espejo.cobertura

        def leer_anio(a):
            llamadas_anio.append(a)
            time.sleep(0.2)
            return np.full((12, 49, 69), 4.0, dtype=np.float32)

        climatologia.espejo.leer_mensual_anio = leer_anio
        climatologia.espejo.disponible = lambda: True
        climatologia.espejo.cobertura = lambda forzar=False: {"final": (date(1981, 1, 1), date(2026, 9, 30)),
                                                              "preliminar": None, "error": ""}
        try:
            hilos = [threading.Thread(target=climatologia.preparar_anio_replica, args=(1990,)) for _ in range(3)]
            for h in hilos:
                h.start()
            for h in hilos:
                h.join()
            verificar(llamadas_anio == [1990], f"3 preparaciones simultáneas del mismo año: {len(llamadas_anio)} lectura")
            try:
                climatologia.climatologia_periodo(1960, 1989)
                verificar(False, "con 30 años faltantes la consulta no los prepara en el momento")
            except climatologia.ClimatologiaEnPreparacion as e:
                verificar("se está preparando" in str(e), "con 30 años faltantes: «se está preparando» (503) y en "
                          "segundo plano")
            if climatologia._hilo_fondo is not None:
                climatologia._hilo_fondo.join(timeout=30)
            verificar(climatologia.anios_faltantes(1960, 1989) == [], "la preparación en segundo plano los completó")
        finally:
            (climatologia.espejo.leer_mensual_anio, climatologia.espejo.disponible,
             climatologia.espejo.cobertura) = original

        # --- Procesos: almacén, estado, ritmo y un archivo a la vez ---
        hijos = [_hijo(_HIJO_ALMACEN, tmp, 1, 15), _hijo(_HIJO_ALMACEN, tmp, 16, 31)]
        errores = [h.communicate(timeout=120)[1] for h in hijos]
        almacen._memoria.clear()
        t = almacen.tipos(date(2025, 3, 1), date(2025, 3, 31))
        verificar(sum(1 for x in t.values() if x == FINAL) == 31 and all(h.returncode == 0 for h in hijos),
                  f"dos procesos escriben el mismo mes: 31 de 31 días guardados {errores if any(errores) else ''}")
        estado.recargar()
        b0 = int(estado.datos().get("bytes_totales", 0))
        hijos = [_hijo(_HIJO_ESTADO, tmp, 60) for _ in range(2)]
        for h in hijos:
            h.communicate(timeout=120)
        estado.recargar()
        verificar(int(estado.datos().get("bytes_totales", 0)) - b0 == 120
                  and not list(tmp.glob("estado.*.tmp")),
                  "dos procesos suman bytes en estado.json: no se pierde ninguno ni quedan temporales")
        abierto = threading.Event()

        def lector():                     # otro lector tiene estado.json abierto unos instantes
            with open(estado.RUTA_ESTADO, encoding="utf-8"):
                abierto.set()
                time.sleep(0.4)

        h = threading.Thread(target=lector)
        h.start()
        abierto.wait(5)
        estado.registrar_disyuntor(True, datetime.now() + timedelta(minutes=15), "prueba con el archivo abierto")
        h.join()
        estado.recargar()
        verificar(estado.disyuntor_vigente()[0], "la apertura del disyuntor llega al disco aunque otro lo tenga abierto")
        hijos = [_hijo(_HIJO_RITMO, tmp) for _ in range(2)]
        # Turnos reservados (lo que controla el limitador) y nadie antes de su turno; la hora al despertar
        # depende además del planificador del sistema (ver el limitador entre hilos).
        marcas = [m for h in hijos for m in json.loads(h.communicate(timeout=120)[0].strip() or "[]")]
        turnos = sorted(t for t, _ in marcas)
        validos = len(marcas) == 10 and all(t == t for t in turnos)
        sep = min((b - a for a, b in zip(turnos, turnos[1:])), default=0) if validos else 0.0
        adelanto = max((t - d for t, d in marcas), default=1.0) if validos else 1.0
        verificar(validos and sep >= 0.199 and adelanto <= 0.002,
                  f"ritmo compartido entre 2 procesos (5 pet./s): turnos separados ≥ 0.2 s (mínimo "
                  f"{cfg.fmt_num(sep, 3)} s) y ninguno sale antes de su turno")
        hijo = _hijo(_HIJO_ARCHIVO, tmp, 2.0)
        hijo.stdout.readline()
        try:
            with red.un_archivo(espera=0.3):
                verificar(False, "un archivo del CHC a la vez también entre procesos")
        except constantes.ErrorCHIRPS:
            verificar(True, "un archivo del CHC a la vez también entre procesos")
        hijo.communicate(timeout=60)

    # --- Caché vacía: no se inventa una fecha de corte ni se rechaza la consulta ---
    with entorno_simulado():
        espejo = EspejoFalso(_COB_FINAL, _COB_PRELIM)
        descarga.modulo_espejo = lambda: espejo
        c = ConectorCHIRPS()
        verificar(c.fecha_corte_efectiva() is None and c.fecha_corte < cfg.FECHA_INICIO,
                  "caché vacía y nada verificado: sin fecha de corte servible")
        ini, fin, _nota = motor._acotar_periodo(date(2026, 9, 1), date(2026, 9, 30), c)
        verificar((ini, fin) == (date(2026, 9, 1), date(2026, 9, 30)), "el motor no rechaza el periodo (no cita 30/09/2023)")
        ds = c.obtener(ParametrosConsulta("precip", (-81.5, -5.0, -75.0, 2.0), ini, fin))
        verificar(ds.sizes["time"] == 30 and ds.attrs["dias_faltantes"] == 0,
                  "obtener() llena bajo demanda desde la réplica antes de recortar a la caché")


def pd_serie(n: int):
    import pandas as pd
    return pd.Series(np.linspace(0, 1, n), index=pd.date_range("2024-01-01", periods=n, freq="D"))


# ---------------------------------------------------------------------------
# Caché real y red real
# ---------------------------------------------------------------------------
def prueba_conector() -> None:
    from ..conectores.base import ParametrosConsulta
    from ..conectores.chirps import ConectorCHIRPS
    from . import almacen, historial_estado, resumen
    from .constantes import sin_red
    print(f"Conector con la caché real ({'sin red' if sin_red() else 'con red'}):")
    c = ConectorCHIRPS()
    verificar(c.datos_reales is True and "CHIRPS v3" in c.insignia, "conector declarado con datos reales")
    e = c.estado()
    verificar(e.estado in ("operativa", "degradada", "sin_conexion"), f"estado RF-21: {e.estado}")
    if sin_red():
        verificar(e.estado == "sin_conexion" and "sin red" in e.mensaje.lower(),
                  "modo sin red → «sin_conexion» con mensaje claro")
    h = historial_estado(14)
    verificar(len(h) == 14 and h[0]["fecha"] < h[-1]["fecha"]
              and all(x["estado"] in ("operativa", "degradada", "sin_conexion", "sin_registro") for x in h)
              and all("origen" in x for x in h),
              "historial de 14 días ordenado y con origen")
    verificar(len(c.historial_estado(7)) == 7, "historial_estado del conector delega en el paquete")
    res = resumen()
    verificar({"dias_en_cache", "dias_preliminares", "ultima_sincronizacion", "bytes_descargados",
               "errores_recientes", "dias_por_origen", "fuentes", "disyuntor_chc"} <= set(res),
              "resumen() con los campos de administración")

    corte = almacen.ultimo_dia()
    if corte is None:
        omitir("caché diaria vacía: se omiten las consultas del conector")
        return
    fin = corte
    ini = max(cfg.FECHA_INICIO, fin - timedelta(days=40))
    p = ParametrosConsulta(variable="precip", bbox=(-81.5, -5.0, -75.0, 2.0), fecha_inicio=ini, fecha_fin=fin)
    ds = c.obtener(p)
    v = ds["precip"].values
    verificar(ds["precip"].dims == ("time", "lat", "lon") and v.shape[0] == (fin - ini).days + 1,
              "Dataset (time, lat, lon) con un paso por día")
    verificar(ds.attrs.get("datos_reales") == 1 and ds.attrs.get("comment") == c.insignia
              and ds["precip"].attrs.get("units") == "mm day-1"
              and ds["precip"].attrs.get("standard_name") == "lwe_precipitation_rate",
              "atributos CF y de procedencia real")
    verificar(all(k in ds.attrs for k in ("dias_preliminares", "dias_faltantes", "procesamiento",
                                          "references", "licencia", "fecha_corte", "origen_datos")),
              f"atributos de días, procesamiento, licencia y origen («{ds.attrs.get('origen_datos')}»)")
    verificar(np.nanmin(v) >= 0, "ningún valor negativo")
    verificar(np.isnan(v[:, 0, 0]).all(), "mar abierto sin dato (NaN)")
    verificar(np.isfinite(v).any(), "hay celdas de tierra con dato")

    try:
        c.obtener(ParametrosConsulta("precip", (-81, -3, -79, -1), date(2020, 1, 1), date(2020, 2, 1)))
        verificar(False, "periodo fuera de cobertura rechazado")
    except ValueError as ex:
        verificar("fuera de la cobertura" in str(ex), "periodo fuera de cobertura rechazado")
    try:
        c.obtener(ParametrosConsulta("sst", (-81, -3, -79, -1), ini, fin))
        verificar(False, "variable desconocida rechazada")
    except ValueError as ex:
        verificar("no publica la variable" in str(ex), "variable desconocida rechazada")

    if 1991 in almacen.anios_mensuales() and len(almacen.anios_mensuales()) >= 30:
        cl = c.climatologia("precip", "1991-2020", (-81.5, -5.0, -75.0, 2.0))
        verificar(cl["precip"].dims == ("month", "lat", "lon") and cl.sizes["month"] == 12,
                  "climatología (month, lat, lon)")
        verificar("limatología" in cl.attrs.get("metodo", ""), "método de la climatología")
    else:
        omitir("climatología no preparada en la caché")


def prueba_red() -> None:
    from . import descarga, estado, red
    from .constantes import FINAL, sin_red
    print("Red real (servidor del CHC y réplica):")
    if sin_red():
        omitir("modo sin red: no se consulta el CHC ni la réplica")
        return
    if red.disyuntor_abierto() or estado.chc_accesible() is False:
        omitir("el CHC está marcado caído: no se le insiste (lo sondea la sincronización cada 30 min)")
    else:
        try:
            campo = descarga.descargar_dia(date(2026, 9, 15), FINAL)
            verificar(campo.shape == (49, 69) and np.nanmin(campo) >= 0 and np.isfinite(campo).sum() > 200,
                      "día final real leído del CHC por rangos HTTP (0.05° agregado 5×5)")
            from . import almacen
            guardado, tipo, origen = almacen.leer_con_origen(date(2026, 9, 15), date(2026, 9, 15))
            if tipo[0] == FINAL:
                ref = guardado[0]
                misma = np.array_equal(np.isfinite(ref), np.isfinite(campo))
                m = np.isfinite(ref) & (np.abs(ref) >= 0.1)
                rel = float(np.max(np.abs(campo[m] - ref[m]) / np.abs(ref[m]))) if m.any() else 0.0
                verificar(misma and rel < 0.005, f"el final del CHC coincide con el de la caché: misma máscara, "
                          f"error máximo {cfg.fmt_num(rel * 100, 3)} %")
        except Exception as e:  # noqa: BLE001
            omitir(f"no se pudo leer un día real del CHC ({e})")
    if not descarga.espejo_instalado():
        omitir("réplica no instalada (faltan backend/chirps/espejo.py o icechunk/zarr)")
        return
    cob = descarga.cobertura_espejo()
    if descarga.espejo_accesible(cob):
        verificar(cob["final"] is not None and cob["final"][0] <= date(1981, 1, 1),
                  f"cobertura real de la réplica: final hasta {cfg.fmt_fecha(cob['final'][1])}, "
                  f"preliminar hasta {cfg.fmt_fecha(cob['preliminar'][1]) if cob['preliminar'] else '—'}")
    else:
        omitir(f"la réplica no responde ({cob['error']})")


def main() -> int:
    # La consola de Windows suele usar cp1252 y la salida redirigida falla con «→»: forzamos UTF-8.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    prueba_malla()
    prueba_tiff_minimo()
    prueba_almacen()
    prueba_cortesia()
    prueba_conmutacion()
    prueba_sincronizacion()
    prueba_bajo_demanda()
    prueba_correcciones()
    prueba_conector()
    prueba_red()
    r = _resultados
    print(f"\nResultado: {r['ok']} correctas, {r['fallo']} fallidas, {r['omitida']} omitidas.")
    return 1 if r["fallo"] else 0


if __name__ == "__main__":
    sys.exit(main())
