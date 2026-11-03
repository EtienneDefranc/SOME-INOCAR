"""Plan y ejecución de las descargas diarias de NOAA OISST v2.1 (PSL como vía principal, NCEI de respaldo).

* `planificar(dias)`: agrupa los días que faltan en tramos de un mismo archivo anual
  de PSL (`sst.day.mean.AAAA.nc`) de hasta 92 días (el NCSS tarda ~0.3 s por día;
  el proxy de PSL corta a los 60 s). Los huecos dentro de un tramo se vuelven a
  pedir: cuesta menos que otra petición (cada una deja 20 s de pausa).
* `ejecutar_psl(tramo)`: una petición NCSS de la ventana SOME + Niño 1+2 → malla SOME
  y región Niño 1+2 → caché, con el tipo de cada día (final o preliminar según lo
  publicado por NCEI, ver `estado.tipo_publicado`).
* `ejecutar_ncei(dia)`: respaldo con el archivo diario global de NCEI (~1.5 MB): el
  nombre del archivo dice si el día es final o preliminar.
* `actualizar_publicacion()`: lee el listado de NCEI del mes actual y del anterior
  (qué días son finales, desde cuándo, y cuáles preliminares).
* `llenar(inicio, fin)`: lo anterior para los días que faltan (y los preliminares),
  bajo un bloqueo de descarga entre procesos; si PSL no responde, los días más
  recientes (hasta 45 por llamada) se piden a NCEI.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .. import configuracion as cfg
from ..chirps.bloqueo import TiempoAgotadoBloqueo
from ..chirps.bloqueo import bloqueo as _bloqueo_procesos
from . import almacen, estado, malla, red
from .constantes import (ARCHIVO_DIARIO, FINAL, MAX_DIAS_NCEI, MAX_DIAS_TRAMO, ORIGEN_NCEI, ORIGEN_PSL, PRELIMINAR,
                         SEGUNDOS_POR_DIA_PSL, ErrorConexion, ErrorDescargaEnCurso, ErrorFormato, ErrorNOAA,
                         ErrorNoExiste, ruta_bloqueos)

log = logging.getLogger("some.noaa")


@dataclass(frozen=True)
class Tramo:
    inicio: date
    fin: date

    @property
    def dias(self) -> int:
        return (self.fin - self.inicio).days + 1

    @property
    def archivo(self) -> str:
        return ARCHIVO_DIARIO.format(anio=self.inicio.year)

    @property
    def segundos_estimados(self) -> float:
        return round(self.dias * SEGUNDOS_POR_DIA_PSL + 2.0, 1)

    @property
    def mib_estimados(self) -> float:
        # ~4.6 KB por día comprimido (65 × 70 float32 con zlib; medido: 92 días = 0.67 MB)
        return round(self.dias * 0.0071, 2)

    def describir(self) -> str:
        return (f"NOAA PSL {self.archivo}: {cfg.fmt_fecha(self.inicio)} – {cfg.fmt_fecha(self.fin)} "
                f"({cfg.fmt_num(self.dias, 0)} {'día' if self.dias == 1 else 'días'})")


def planificar(dias, max_dias: int = MAX_DIAS_TRAMO) -> list[Tramo]:
    """Tramos (un año por tramo, ≤ max_dias) que cubren los días pedidos, en orden."""
    lista = sorted(set(dias))
    tramos: list[Tramo] = []
    inicio = ultimo = None
    for d in lista:
        if inicio is not None and d.year == inicio.year and (d - inicio).days < max_dias:
            ultimo = d
            continue
        if inicio is not None:
            tramos.append(Tramo(inicio, ultimo))
        inicio = ultimo = d
    if inicio is not None:
        tramos.append(Tramo(inicio, ultimo))
    return tramos


# ---------------------------------------------------------------------------
# Publicación (listado de NCEI)
# ---------------------------------------------------------------------------
_FILA_NCEI = re.compile(r'href="oisst-avhrr-v02r01\.(\d{8})(_preliminary)?\.nc">[^<]*</a>\s*</td>\s*'
                        r'<td[^>]*>\s*([^<]*?)\s*</td>')
_ARCHIVO_NCEI = re.compile(r'oisst-avhrr-v02r01\.(\d{8})(_preliminary)?\.nc')


def leer_listado(html: str) -> list[tuple[date, bool, datetime | None]]:
    """[(día, preliminar, publicado)] del listado HTML de un mes de NCEI (publicado: fecha y hora del listado)."""
    salida: dict[tuple[date, bool], datetime | None] = {}
    for m in _FILA_NCEI.finditer(html):
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        try:
            publicado = datetime.strptime(m.group(3).strip()[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            publicado = None
        salida[(d, bool(m.group(2)))] = publicado
    for m in _ARCHIVO_NCEI.finditer(html):           # filas sin fecha legible
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        salida.setdefault((d, bool(m.group(2))), None)
    return sorted(((d, p, t) for (d, p), t in salida.items()), key=lambda x: (x[0], x[1]))


def actualizar_publicacion(*, respetar_pausa: bool = True, hoy: date | None = None) -> dict:
    """Lee el listado de NCEI del mes actual y del anterior y lo registra. Devuelve la información registrada
    y la latencia. ErrorConexion si NCEI no responde."""
    hoy = hoy or date.today()
    meses = [(hoy.year, hoy.month)]
    anterior = hoy.replace(day=1) - timedelta(days=1)
    meses.insert(0, (anterior.year, anterior.month))
    filas: list[tuple[date, bool, datetime | None]] = []
    latencias = []
    for a, m in meses:
        try:
            contenido, lat = red.listado_ncei(a, m, respetar_pausa=respetar_pausa)
        except ErrorNoExiste:
            continue                       # el mes aún no tiene carpeta (primer día del mes)
        latencias.append(lat)
        filas += leer_listado(contenido.decode("utf-8", "replace"))
    finales = {d: t for d, p, t in filas if not p}
    preliminares = sorted(d for d, p, _t in filas if p and d not in finales)
    info = {"ncei_ultimo_final": max(finales).isoformat() if finales else None,
            "ncei_ultimo_preliminar": max(preliminares).isoformat() if preliminares else None,
            "finales": {d.isoformat(): t.isoformat(timespec="minutes") for d, t in finales.items() if t},
            "preliminares": [d.isoformat() for d in preliminares]}
    if finales or preliminares:
        estado.registrar_publicacion(info)
    info["latencia_ms"] = int(max(latencias)) if latencias else 0
    return info


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------
def _resumen_vacio() -> dict:
    return {"peticiones": 0, "tramos": 0, "dias_recibidos": 0, "dias_escritos": 0, "finales": 0, "preliminares": 0,
            "dias_ncei": 0, "bytes": 0, "segundos": 0.0, "errores": 0, "detalle_errores": [], "pendientes": 0,
            "ultimo_dia": None}


def ejecutar_psl(t: Tramo, *, respetar_pausa: bool = True) -> dict:
    """Una petición NCSS del tramo → caché. Devuelve el resumen del tramo."""
    r = _resumen_vacio()
    t0 = time.perf_counter()
    contenido, _lat = red.ncss(t.archivo, "sst", t.inicio, t.fin, respetar_pausa=respetar_pausa)
    r["peticiones"] = 1
    r["bytes"] = len(contenido)
    ds = malla.abrir_netcdf(contenido)
    fechas = malla.fechas_de(ds)
    nativo = malla.ventana(ds, "sst")
    if fechas and (min(fechas) < t.inicio or max(fechas) > t.fin):
        raise ErrorFormato(f"PSL devolvió días fuera del tramo pedido ({cfg.fmt_fecha(min(fechas))} – "
                           f"{cfg.fmt_fecha(max(fechas))}).")
    ahora = datetime.now()
    tipos = [estado.tipo_publicado(d, ahora) for d in fechas]
    r["dias_recibidos"] = len(fechas)
    if fechas:
        r["dias_escritos"] = almacen.guardar(fechas, malla.a_some(nativo), malla.region_nino12(nativo), tipos,
                                             ORIGEN_PSL)
        r["finales"] = sum(1 for x in tipos if x == FINAL)
        r["preliminares"] = sum(1 for x in tipos if x == PRELIMINAR)
        r["ultimo_dia"] = max(fechas).isoformat()
        # Si el tramo pedía más allá de lo publicado, lo recibido marca el último día de PSL
        if t.fin > max(fechas) or t.fin >= date.today() - timedelta(days=1):
            estado.registrar_publicacion({"psl_fin": max(fechas).isoformat()})
    r["tramos"] = 1
    r["segundos"] = round(time.perf_counter() - t0, 1)
    return r


def ejecutar_ncei(d: date, *, respetar_pausa: bool = True) -> dict:
    """El archivo diario de NCEI del día `d` (final si NCEI ya lo publicó; si no, preliminar) → caché."""
    r = _resumen_vacio()
    t0 = time.perf_counter()
    info = estado.datos().get("publicacion") or {}
    ultimo_final = info.get("ncei_ultimo_final")
    preliminar_primero = not (ultimo_final and d.isoformat() <= ultimo_final)
    contenido = None
    preliminar = preliminar_primero
    for intento_preliminar in (preliminar_primero, not preliminar_primero):
        try:
            contenido, _lat = red.archivo_ncei(d, intento_preliminar, respetar_pausa=respetar_pausa)
            preliminar = intento_preliminar
            r["peticiones"] += 1
            break
        except ErrorNoExiste:
            r["peticiones"] += 1
            continue
    if contenido is None:
        raise ErrorNoExiste(f"NOAA NCEI aún no publica el {cfg.fmt_fecha(d)}.")
    r["bytes"] = len(contenido)
    ds = malla.abrir_netcdf(contenido)
    fechas = malla.fechas_de(ds)
    if fechas != [d]:
        raise ErrorFormato(f"El archivo de NCEI del {cfg.fmt_fecha(d)} trae otro día.")
    nativo = malla.ventana(ds, "sst")
    tipo = PRELIMINAR if preliminar else FINAL
    r["dias_recibidos"] = 1
    r["dias_escritos"] = almacen.guardar([d], malla.a_some(nativo), malla.region_nino12(nativo), [tipo], ORIGEN_NCEI)
    r["finales"] = int(tipo == FINAL)
    r["preliminares"] = int(tipo == PRELIMINAR)
    r["dias_ncei"] = 1
    r["ultimo_dia"] = d.isoformat()
    r["segundos"] = round(time.perf_counter() - t0, 1)
    return r


def _sumar(total: dict, parcial: dict) -> None:
    for k in ("peticiones", "tramos", "dias_recibidos", "dias_escritos", "finales", "preliminares", "dias_ncei",
              "bytes", "errores"):
        total[k] += int(parcial.get(k) or 0)
    total["segundos"] = round(total["segundos"] + float(parcial.get("segundos") or 0.0), 1)
    if parcial.get("ultimo_dia") and (not total["ultimo_dia"] or parcial["ultimo_dia"] > total["ultimo_dia"]):
        total["ultimo_dia"] = parcial["ultimo_dia"]
    total["detalle_errores"] += list(parcial.get("detalle_errores") or [])


def llenar(inicio: date, fin: date, *, renovar_desde: date | None = None, max_peticiones: int | None = None,
           respetar_pausa: bool = True, usar_ncei: bool = True, max_dias_ncei: int = MAX_DIAS_NCEI,
           incluir_preliminares: bool = True, espera_bloqueo: float | None = None, detener=None,
           progreso=None) -> dict:
    """Descarga los días de [inicio, fin] que faltan (y los preliminares si `incluir_preliminares`; desde
    `renovar_desde`, todo lo no final).

    `max_peticiones`: tope de peticiones a PSL (las consultas piden 1); `max_dias_ncei`: días del respaldo NCEI
    si PSL no responde; `espera_bloqueo`: segundos máximos para tomar el turno de descarga (None = esperar; si
    no se obtiene, ErrorDescargaEnCurso). `progreso(hechos, total, texto)` en días. Cada tramo se guarda entero
    o no se guarda.
    """
    total = _resumen_vacio()
    dias = almacen.faltantes(inicio, fin, renovar_desde, incluir_preliminares=incluir_preliminares)
    if not dias:
        return total
    tramos = planificar(dias)
    if max_peticiones is not None and len(tramos) > max_peticiones:
        total["pendientes"] = sum(t.dias for t in tramos[max_peticiones:])
        tramos = tramos[:max_peticiones]
    try:
        with _bloqueo_procesos(ruta_bloqueos() / "descarga.lock", espera_bloqueo):
            psl_caido = ""
            hechos, n_total = 0, sum(t.dias for t in tramos) or 1
            for t in tramos:
                if detener is not None and detener.is_set():
                    break
                if progreso:
                    progreso(hechos, n_total, t.describir())
                if not psl_caido:
                    try:
                        _sumar(total, ejecutar_psl(t, respetar_pausa=respetar_pausa))
                        hechos += t.dias
                        continue
                    except ErrorConexion as e:
                        psl_caido = str(e)
                        total["errores"] += 1
                        total["detalle_errores"].append(f"{t.describir()}: {e}")
                    except ErrorNOAA as e:
                        total["errores"] += 1
                        total["detalle_errores"].append(f"{t.describir()}: {e}")
                        estado.registrar_error(f"Descarga de PSL ({t.describir()}): {e}")
                        hechos += t.dias
                        continue
                hechos += t.dias
            # Respaldo NCEI: los días más recientes que sigan faltando (PSL caído)
            if psl_caido and usar_ncei and not (detener is not None and detener.is_set()):
                pendientes = [d for d in almacen.faltantes(inicio, fin, renovar_desde,
                                                           incluir_preliminares=incluir_preliminares)
                              if d >= date.today() - timedelta(days=MAX_DIAS_NCEI)]
                for d in sorted(pendientes, reverse=True)[:max(0, int(max_dias_ncei))]:
                    if detener is not None and detener.is_set():
                        break
                    try:
                        _sumar(total, ejecutar_ncei(d, respetar_pausa=respetar_pausa))
                    except ErrorNoExiste:
                        continue
                    except ErrorConexion as e:
                        total["errores"] += 1
                        total["detalle_errores"].append(f"NOAA NCEI ({cfg.fmt_fecha(d)}): {e}")
                        break
                    except ErrorNOAA as e:
                        total["errores"] += 1
                        total["detalle_errores"].append(f"NOAA NCEI ({cfg.fmt_fecha(d)}): {e}")
            if progreso:
                progreso(n_total, n_total, "listo")
    except TiempoAgotadoBloqueo as e:
        raise ErrorDescargaEnCurso("Hay otra descarga de NOAA OISST en curso (sincronización o preparación); "
                                   "se completará en segundo plano.") from e
    total["psl_caido"] = psl_caido if tramos else ""
    return total


def dias_estimados(inicio: date, fin: date, renovar_desde: date | None = None, *,
                   incluir_preliminares: bool = True) -> dict:
    """Plan sin red: tramos, días, segundos y MiB estimados de lo que falta en [inicio, fin]."""
    tramos = planificar(almacen.faltantes(inicio, fin, renovar_desde, incluir_preliminares=incluir_preliminares))
    return {"tramos": tramos, "dias": sum(t.dias for t in tramos),
            "segundos": round(sum(t.segundos_estimados for t in tramos), 1),
            "mib": round(sum(t.mib_estimados for t in tramos), 2)}


__all__ = ["Tramo", "planificar", "leer_listado", "actualizar_publicacion", "ejecutar_psl", "ejecutar_ncei",
           "llenar", "dias_estimados"]
