"""Conector REAL de NOAA OISST v2.1 (temperatura superficial del mar diaria) con respaldo simulado.

Mismos `id` («noaa_oisst»), nombres, variable («sst») y contrato que el conector
simulado de `backend/conectores/noaa.py`, al que reemplaza en la integración. Los
datos salen de la caché local `datos/noaa/` (malla SOME de 0.25°), que llenan la
sincronización en segundo plano (cada 6 h), la herramienta `preparar_noaa` y, en el
momento, las consultas (solo los días AUSENTES, una petición a PSL como máximo; los
preliminares los renueva la sincronización).

Procedencia honesta (como Copernicus Marine, §3.8 de su diseño):
* modo «real» (hay caché o red): Dataset real (`datos_reales=1`); los días que no
  estén en la caché y no se pudieron obtener quedan sin dato y el Dataset lo dice
  (`aviso`);
* si a una consulta no le queda NINGÚN dato real: respaldo con el simulador,
  etiquetado «simulado» (`datos_reales=0`, `respaldo_simulado=1`) y con un aviso
  que dice por qué (con `SOME_NOAA_MODO=real`, ValueError → 422);
* modo «simulado» (`SOME_NOAA_MODO=simulado`, o sin red ni caché): el simulador
  anterior, idéntico.
Nunca se mezclan días simulados con reales dentro de un mismo Dataset.

Fecha de corte: el último día disponible, es decir, el último de la caché (con la caché
vacía y acceso, el último publicado por PSL): la fecha por defecto del sistema siempre
tiene dato. Los últimos ~15 días son preliminares y el Dataset los cuenta
(`dias_preliminares`, `periodo_preliminar`, `nota_preliminares`).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import xarray as xr

from .. import configuracion as cfg
from .. import datos_simulados as sim
from ..conectores.base import (_ATRIBUTOS_LAT, _ATRIBUTOS_LON, _ATRIBUTOS_MONTH, _ATRIBUTOS_TIME, ConectorFuente,
                               ConectorSimulado, EstadoFuente, ParametrosConsulta, VariableFuente)
from . import almacen, climatologia as clim_noaa, descarga, estado, indice
from .constantes import (CITA_ARTICULO, CITA_OFICIAL, CREDITO, DOI, ESPERA_DESCARGA_BAJO_DEMANDA_S,
                         FECHA_CORTE_SIMULADA, FINAL, LATENCIA_SIMULADA_MS, LICENCIA, MAX_PETICIONES_BAJO_DEMANDA,
                         ORIGEN_NCEI, ORIGEN_PSL, PRELIMINAR, PRODUCTO, URL_DOI, ErrorDescargaEnCurso, ErrorNOAA,
                         ErrorSinDatos, credito_para_origen, modo_configurado)
from .malla import TEXTO_PROCESAMIENTO

log = logging.getLogger("some.noaa")

INSIGNIA_REALES = getattr(cfg, "INSIGNIA_REALES_NOAA", "Datos reales · NOAA OISST")
# El simulador imita la forma de NOAA OISST, pero sus valores no son de NOAA: el crédito no se los atribuye
# (la licencia de NOAA prohíbe presentar como suyos datos que no lo son).
CREDITO_SIMULADO = "Simulación del SOME con la forma de NOAA OISST v2.1; no son datos de NOAA."
REFERENCIAS_SIMULADO = CITA_ARTICULO
CELL_METHODS_REAL = "time: mean (diario) area: mean (2 × 2 celdas nativas de 0.25°)"
NOTA_PRELIMINARES = ("Los días preliminares de NOAA OISST (los últimos ~15) los reemplaza NOAA/NCEI por la versión "
                     "final unas dos semanas después; el SOME los vuelve a pedir hasta que lo son.")


def _variables() -> list[VariableFuente]:
    """La variable de la fuente (id, unidades, paletas y decimales de siempre: la interfaz depende de ellos)."""
    return [
        VariableFuente(
            id="sst",
            nombre="Temperatura superficial del mar",
            unidad="°C",
            descripcion="Temperatura de la superficie del mar interpolada de forma óptima "
                        "a partir de satélite, boyas y barcos (OISST v2.1).",
            paleta="termico",
            paleta_anomalia="PuOr_r",
            standard_name="sea_surface_temperature",
            long_name="Temperatura superficial del mar",
            unidades_cf="degC",
            decimales=1,
        ),
    ]


class _SimuladorNOAA(ConectorSimulado):
    """El simulador anterior de NOAA OISST (modo simulado y respaldo), idéntico al de siempre."""

    id = "noaa_oisst"
    nombre = "NOAA OISST v2.1"
    nombre_corto = "NOAA OISST"
    proveedor = "NOAA/NCEI (National Centers for Environmental Information)"
    producto = "NOAA Optimum Interpolation Sea Surface Temperature (OISST) v2.1"
    identificador = "NOAA-OISST-v2.1"
    credito = CREDITO_SIMULADO
    licencia = LICENCIA
    referencias = REFERENCIAS_SIMULADO
    resolucion = "0.25° diario"
    cobertura_temporal = "Desde septiembre de 1981 (en la demo: 01/10/2023 – 27/10/2026)"

    estado_simulado = "operativa"
    fecha_corte_simulada = FECHA_CORTE_SIMULADA
    latencia_simulada_ms = LATENCIA_SIMULADA_MS
    mensaje_estado = "Servicio ERDDAP de NOAA/NCEI respondiendo con normalidad."

    def __init__(self, variables: list[VariableFuente]):
        super().__init__()
        self.variables = variables          # la MISMA lista que el conector (habilitaciones compartidas)


def _texto_dias(n: int) -> str:
    return f"{cfg.fmt_num(n, 0)} {'día' if n == 1 else 'días'}"


def texto_origen(finales: int, preliminares: int, ncei: int = 0) -> str:
    """«NOAA OISST v2.1: 16 días finales y 14 preliminares» (sin partes en cero) y, si alguno vino del respaldo,
    «; 3 de ellos de NOAA NCEI»."""
    if finales and preliminares:
        detalle = f"{_texto_dias(finales)} {'final' if finales == 1 else 'finales'} y {cfg.fmt_num(preliminares, 0)} " \
                  f"{'preliminar' if preliminares == 1 else 'preliminares'}"
    elif preliminares:
        detalle = f"{_texto_dias(preliminares)} {'preliminar' if preliminares == 1 else 'preliminares'}"
    elif finales:
        detalle = f"{_texto_dias(finales)} {'final' if finales == 1 else 'finales'}"
    else:
        detalle = "sin días con dato"
    if ncei:
        detalle += f"; {cfg.fmt_num(ncei, 0)} de ellos de NOAA NCEI (respaldo)"
    return f"NOAA OISST v2.1: {detalle}"


class ConectorNOAA(ConectorFuente):
    id = "noaa_oisst"
    nombre = "NOAA OISST v2.1"
    nombre_corto = "NOAA OISST"
    proveedor = "NOAA/NCEI (National Centers for Environmental Information)"
    producto = "NOAA Optimum Interpolation Sea Surface Temperature (OISST) v2.1"
    identificador = "NOAA-OISST-v2.1"
    licencia = LICENCIA

    def __init__(self):
        self.habilitada = True
        self.variables = _variables()
        self._sim = _SimuladorNOAA(self.variables)
        self._sim_respaldo = _SimuladorNOAA(self.variables)

    # ------------------------------------------------------------ procedencia dinámica
    @property
    def datos_reales(self) -> bool:
        try:
            return estado.modo() == "real"
        except Exception:  # noqa: BLE001
            return False

    @property
    def insignia(self) -> str:
        return INSIGNIA_REALES if self.datos_reales else cfg.INSIGNIA_SIMULADOS

    @property
    def credito(self) -> str:
        return CREDITO if self.datos_reales else CREDITO_SIMULADO

    @property
    def referencias(self) -> str:
        if not self.datos_reales:
            return REFERENCIAS_SIMULADO
        try:
            acceso = almacen.ultima_obtencion()
        except Exception:  # noqa: BLE001
            acceso = None
        base = f"{CITA_OFICIAL} — {CITA_ARTICULO}"
        return base + (f" — Datos consultados por última vez el {cfg.fmt_fecha(acceso)}." if acceso else "")

    @property
    def resolucion(self) -> str:
        if self.datos_reales:
            return "0.25° diario (en SOME: malla de 0.25° con centros en múltiplos de 0.25°)"
        return self._sim.resolucion

    @property
    def cobertura_temporal(self) -> str:
        if self.datos_reales:
            return ("Desde septiembre de 1981; en SOME desde 01/10/2023 hasta el último día publicado (los últimos "
                    "~15 días, preliminares)")
        return self._sim.cobertura_temporal

    # ------------------------------------------------------------ estado (nunca lanza)
    @staticmethod
    def fecha_corte_efectiva() -> date | None:
        """Último día disponible: el último de la caché (None en modo simulado o si aún no se sabe)."""
        try:
            return estado.fecha_corte()
        except Exception:  # noqa: BLE001
            return None

    def _corte(self) -> date:
        if not self.datos_reales:
            return self._sim.fecha_corte_simulada
        corte = self.fecha_corte_efectiva()
        if corte is None:
            # Caché vacía y nada verificado: PSL publica el día D el D+1; la consulta intentará obtenerlo
            corte = date.today() - timedelta(days=1)
        return max(corte, cfg.FECHA_INICIO)

    def estado(self) -> EstadoFuente:
        try:
            e = estado.estado_actual()
            return EstadoFuente(fuente=self.id, nombre=self.nombre, estado=e["estado"],
                                fecha_corte=self._corte().isoformat(), latencia_ms=int(e["latencia_ms"]),
                                ultimo_chequeo=e["ultimo_chequeo"], mensaje=e["mensaje"])
        except Exception as ex:  # noqa: BLE001 — el estado nunca tumba la API
            log.exception("NOAA: error en estado()")
            return EstadoFuente(fuente=self.id, nombre=self.nombre, estado="sin_conexion",
                                fecha_corte=self._sim.fecha_corte_simulada.isoformat(), latencia_ms=0,
                                ultimo_chequeo=datetime.now().isoformat(timespec="seconds"),
                                mensaje=f"No se pudo evaluar el estado de NOAA OISST: {ex}")

    def historial_estado(self, dias: int = 14) -> list[dict]:
        return estado.historial_estado(dias)

    def detalle_estado(self) -> dict:
        return estado.detalle()

    # ------------------------------------------------------------ contrato
    def obtener(self, p: ParametrosConsulta) -> xr.Dataset:
        var = self._variable_valida(p.variable)
        if not self.datos_reales:
            return self._marcar_simulado(self._sim.obtener(p))
        ilat, ilon = sim.indices_bbox(p.bbox)
        if ilat.size == 0 or ilon.size == 0:
            raise ValueError("El área solicitada no contiene ninguna celda de la malla de 0.25°.")
        corte = self._corte()
        inicio = max(p.fecha_inicio, cfg.FECHA_INICIO)
        fin = min(p.fecha_fin, corte)
        if fin < inicio:
            raise ValueError(
                f"El periodo solicitado ({cfg.fmt_fecha(p.fecha_inicio)} – {cfg.fmt_fecha(p.fecha_fin)}) "
                f"está fuera de la cobertura de {self.nombre} "
                f"({cfg.fmt_fecha(cfg.FECHA_INICIO)} – {cfg.fmt_fecha(corte)}).")

        # 1) Llenado bajo demanda de los días AUSENTES (una petición a PSL como máximo)
        motivo = self._llenar_bajo_demanda(inicio, fin)

        # 2) Lectura de la caché
        lec = almacen.leer(inicio, fin, ilat, ilon)
        obtenidos = lec.tipo > 0
        if not obtenidos.any():
            motivo = motivo or "los días pedidos no están en la caché local"
            if modo_configurado() == "real":
                raise ValueError(
                    f"No hay datos de NOAA OISST entre el {cfg.fmt_fecha(inicio)} y el {cfg.fmt_fecha(fin)} en la "
                    f"caché local y no se pudieron obtener: {estado.inicial_minuscula(motivo.rstrip('.'))}. Ejecute "
                    "«python -m backend.herramientas.preparar_noaa» o revise «Estado de fuentes».")
            return self._respaldo(p, motivo, corte)

        # 3) Dataset real
        finales = int((lec.tipo == FINAL).sum())
        preliminares = int((lec.tipo == PRELIMINAR).sum())
        faltantes = int((~obtenidos).sum())
        ncei = int(((lec.origen == ORIGEN_NCEI) & obtenidos).sum())
        psl = int(((lec.origen == ORIGEN_PSL) & obtenidos).sum())
        fechas = pd.date_range(inicio, fin, freq="D")
        variables_datos = {var.id: (("time", "lat", "lon"), lec.valores.astype(np.float32),
                                    self._atributos_variable(var, real=True))}
        coords = {"time": ("time", fechas.values.astype("datetime64[ns]"), _ATRIBUTOS_TIME),
                  "lat": ("lat", cfg.LAT[ilat], _ATRIBUTOS_LAT),
                  "lon": ("lon", cfg.LON[ilon], _ATRIBUTOS_LON)}
        attrs = self._atributos_reales(var, corte)
        partes = [t for n, t in ((finales, f"{cfg.fmt_num(finales, 0)} {'final' if finales == 1 else 'finales'}"),
                                 (preliminares, f"{cfg.fmt_num(preliminares, 0)} "
                                                f"{'preliminar' if preliminares == 1 else 'preliminares'}")) if n]
        attrs.update({
            "procesamiento": f"{TEXTO_PROCESAMIENTO} Días de esta consulta: {' y '.join(partes) or 'ninguno con dato'}.",
            "origen_datos": texto_origen(finales, preliminares, ncei),
            "dias_faltantes": faltantes, "dias_preliminares": preliminares, "dias_finales": finales,
            "dias_origen_psl": psl, "dias_origen_ncei": ncei,
            "credito": credito_para_origen(psl, ncei),
            "cobertura": round(float(obtenidos.mean()), 4),
        })
        if preliminares:
            idx = np.nonzero(lec.tipo == PRELIMINAR)[0]
            attrs["periodo_preliminar"] = (f"{(inicio + timedelta(days=int(idx[0]))).isoformat()}/"
                                           f"{(inicio + timedelta(days=int(idx[-1]))).isoformat()}")
            attrs["nota_preliminares"] = NOTA_PRELIMINARES
        if faltantes:
            attrs["aviso"] = (f"{_texto_dias(faltantes)} del periodo no {'está' if faltantes == 1 else 'están'} en la "
                              f"caché local y no se {'pudo' if faltantes == 1 else 'pudieron'} obtener ahora: "
                              f"{estado.inicial_minuscula(motivo.rstrip('.')) or 'sin detalle'}. "
                              f"{'Ese día queda' if faltantes == 1 else 'Esos días quedan'} sin dato.")
        return xr.Dataset(variables_datos, coords=coords, attrs=attrs)

    def climatologia(self, variable: str, periodo_referencia: str, bbox, profundidad: float | None = None, *,
                     simulada: bool = False) -> xr.Dataset:
        var = self._variable_valida(variable)
        if periodo_referencia not in cfg.PERIODOS_REFERENCIA:
            raise ValueError(f"Periodo de referencia desconocido: «{periodo_referencia}». "
                             f"Disponibles: {', '.join(cfg.PERIODOS_REFERENCIA)}.")
        if simulada or not self.datos_reales:
            return self._marcar_simulado(self._sim.climatologia(variable, periodo_referencia, bbox, profundidad))
        ilat, ilon = sim.indices_bbox(bbox)
        if ilat.size == 0 or ilon.size == 0:
            raise ValueError("El área solicitada no contiene ninguna celda de la malla de 0.25°.")
        try:
            c = clim_noaa.climatologia_mensual(periodo_referencia, llenar=estado.acceso_disponible())
        except ErrorNOAA as e:
            motivo = estado.inicial_minuscula(estado.legible(str(e), 240).rstrip("."))
            if modo_configurado() == "real":
                raise ValueError(f"La climatología {periodo_referencia} de NOAA OISST no está disponible: {motivo}.") from e
            ds = self._marcar_simulado(self._sim.climatologia(variable, periodo_referencia, bbox, profundidad))
            ds.attrs["aviso"] = f"Climatología simulada: {motivo}."
            ds.attrs["respaldo_simulado"] = 1
            ds.attrs["periodo_referencia"] = periodo_referencia
            return ds
        atributos = self._atributos_variable(var, real=True)
        atributos["cell_methods"] = "time: mean within months time: mean over years"
        valores = c.valores[:, ilat[:, None], ilon[None, :]]
        coords = {"month": ("month", np.arange(1, 13), _ATRIBUTOS_MONTH),
                  "lat": ("lat", cfg.LAT[ilat], _ATRIBUTOS_LAT),
                  "lon": ("lon", cfg.LON[ilon], _ATRIBUTOS_LON)}
        attrs = {
            "Conventions": "CF-1.8",
            "title": f"Climatología mensual {c.periodo_efectivo} — {var.nombre} ({self.nombre})",
            "source": (f"{PRODUCTO}: {c.fuente} del servidor THREDDS de NOAA PSL"
                       + ("" if c.fuente == clim_noaa.FUENTE_OFICIAL else " (climatología calculada por el SOME)")),
            "institution": self.proveedor,
            "references": URL_DOI,
            "history": (f"{datetime.now().isoformat(timespec='seconds')} SOME {cfg.VERSION}: leída de la caché local "
                        "datos/noaa/climatologia y llevada a la malla SOME de 0.25°"),
            "comment": INSIGNIA_REALES, "insignia": INSIGNIA_REALES, "datos_reales": 1, "procedencia": "real",
            "periodo_referencia": periodo_referencia, "periodo_efectivo": c.periodo_efectivo,
            "periodo_efectivo_por_mes": c.por_mes, "anios_por_mes": [int(x) for x in c.anios],
            "metodo": clim_noaa.texto_metodo(c),
            "origen_climatologia": clim_noaa.texto_origen(c, periodo_referencia),
            "credito": clim_noaa.credito(c), "fuente_climatologia": c.fuente,
            "fuente_id": self.id, "identificador": self.identificador, "licencia": self.licencia,
            "fecha_corte": self._corte().isoformat(), "resolucion": self.resolucion, "doi": DOI,
        }
        return xr.Dataset({var.id: (("month", "lat", "lon"), valores.astype(np.float32), atributos)},
                          coords=coords, attrs=attrs)

    # ------------------------------------------------------------ índice Niño 1+2
    def serie_indice_nino12(self, inicio: date | None = None, fin: date | None = None) -> dict:
        """Índice Niño 1+2 semanal REAL (región oficial 0–10° S, 90–80° O) o, si no se puede, el simulado señalado.

        Devuelve el resumen de `indice.resumen()` más {"datos_reales", "procedencia", "insignia", "credito",
        "origen_datos", "fecha_corte", "aviso" (si es respaldo)}; con el simulado, {"fechas", "valores", "actual",
        "fecha_actual", "fase", "semaforo", "metodo"} del generador del prototipo, cada 7 días hasta `fin`."""
        corte = self._corte()
        fin = min(fin or corte, corte)
        inicio = max(inicio or cfg.FECHA_INICIO, cfg.FECHA_INICIO)
        motivo = ""
        if self.datos_reales:
            try:
                # La climatología diaria (4 peticiones, ~2 min) no se descarga dentro de una consulta: la
                # obtienen la sincronización y preparar_noaa; mientras falte, el índice es el simulado señalado.
                r = indice.resumen(inicio, fin)
                n = r["dias_finales"] + r["dias_preliminares"]
                # El índice lo calcula el SOME (no es el índice oficial de NOAA CPC): el crédito lo dice, nombra
                # el servidor del que salieron los días y la climatología diaria de PSL (≤ 195 caracteres).
                credito_indice = (f"{credito_para_origen(r['dias_origen_psl'], r['dias_origen_ncei'])} Índice "
                                  "calculado por el INOCAR (SOME) con la climatología diaria 1991-2020 de PSL.")
                r.update({"datos_reales": True, "procedencia": "real", "insignia": INSIGNIA_REALES,
                          "credito": credito_indice, "fecha_corte": fin.isoformat(),
                          "origen_datos": texto_origen(r["dias_finales"], r["dias_preliminares"],
                                                       r["dias_origen_ncei"]),
                          "dias_con_dato": n})
                if r["dias_faltantes"]:
                    r["aviso"] = (f"{_texto_dias(r['dias_faltantes'])} del periodo sin dato en la caché local: el "
                                  "índice de las semanas afectadas usa los días disponibles.")
                return r
            except ErrorNOAA as e:
                motivo = estado.legible(str(e), 240).rstrip(".")
                if modo_configurado() == "real":
                    raise ValueError(f"No se pudo calcular el índice Niño 1+2 con NOAA OISST: {motivo}.") from e
        # Respaldo simulado (o modo simulado): el índice del generador del prototipo, señalado
        fin_sim = min(fin, max(FECHA_CORTE_SIMULADA, fin))
        fechas = pd.date_range(inicio, fin_sim, freq="7D")
        if len(fechas) == 0 or fechas[-1].date() != fin_sim:
            fechas = fechas.append(pd.DatetimeIndex([pd.Timestamp(fin_sim)]))
        valores = sim.indice_enso(fechas)
        actual = float(valores[-1])
        f, semaforo = indice.fase(actual)
        r = {"fechas": [t.date().isoformat() for t in fechas], "valores": [round(float(v), 2) for v in valores],
             "actual": round(actual, 2), "fecha_actual": fin_sim.isoformat(), "fase": f, "semaforo": semaforo,
             "metodo": ("Índice Niño 1+2 simulado: anomalía media de SST (NOAA OISST simulado) de la región Niño 1+2 "
                        "interpolada entre nodos mensuales."),
             "datos_reales": False, "procedencia": "simulado", "insignia": cfg.INSIGNIA_SIMULADOS,
             "credito": CREDITO_SIMULADO, "fecha_corte": fin_sim.isoformat()}
        if motivo:
            r["aviso"] = f"Índice Niño 1+2 simulado: {estado.inicial_minuscula(motivo)}."
            r["respaldo_simulado"] = True
        return r

    # ------------------------------------------------------------ auxiliares
    def _llenar_bajo_demanda(self, inicio: date, fin: date) -> str:
        """Pide a PSL (una petición como máximo) los días AUSENTES de [inicio, fin]; devuelve el motivo si quedó
        algo sin obtener ("" si no). Nunca lanza."""
        if not almacen.faltantes(inicio, fin, incluir_preliminares=False):
            return ""
        if not estado.acceso_disponible():
            return estado.motivo_sin_acceso() or "sin acceso a NOAA"
        try:
            r = descarga.llenar(inicio, fin, max_peticiones=MAX_PETICIONES_BAJO_DEMANDA, incluir_preliminares=False,
                                max_dias_ncei=5, espera_bloqueo=ESPERA_DESCARGA_BAJO_DEMANDA_S)
        except ErrorDescargaEnCurso:
            return "la caché local se está completando en segundo plano"
        except ErrorNOAA as e:
            return estado.legible(str(e), 240)
        except Exception as e:  # noqa: BLE001 — se sirve lo que haya y se dice
            log.exception("NOAA: error inesperado al completar la caché")
            return f"error inesperado ({type(e).__name__})"
        if r.get("psl_caido"):
            return estado.legible(r["psl_caido"], 240)
        if r.get("pendientes"):
            return "el periodo es largo: lo que falta se completará en segundo plano"
        if r.get("detalle_errores"):
            return estado.legible(r["detalle_errores"][0], 240)
        return "NOAA aún no publica esos días"

    def _variable_valida(self, variable_id: str) -> VariableFuente:
        var = self.variable(variable_id)
        if var is None:
            disponibles = ", ".join(v.id for v in self.variables)
            raise ValueError(f"La fuente {self.nombre} no publica la variable «{variable_id}». "
                             f"Disponibles: {disponibles}.")
        return var

    def _atributos_variable(self, var: VariableFuente, real: bool) -> dict:
        atributos = {
            "standard_name": var.standard_name,
            "long_name": var.long_name,
            "units": var.unidades_cf,
            "unidad_mostrada": var.unidad,
            "coordinates": "time lat lon",
            "comment": INSIGNIA_REALES if real else cfg.INSIGNIA_SIMULADOS,
        }
        if real:
            atributos["cell_methods"] = CELL_METHODS_REAL
        return atributos

    def _atributos_reales(self, var: VariableFuente, corte: date) -> dict:
        return {
            "Conventions": "CF-1.8",
            "title": f"{var.nombre} — {self.nombre}",
            "source": (f"{PRODUCTO}: sst.day.mean.AAAA.nc del servidor THREDDS de NOAA PSL (subconjunto NCSS) y, de "
                       "respaldo, archivos diarios de NOAA NCEI"),
            "institution": self.proveedor,
            "references": URL_DOI,
            "history": (f"{datetime.now().isoformat(timespec='seconds')} SOME {cfg.VERSION}: leído de la caché local "
                        "datos/noaa (llenada desde NOAA PSL y NOAA NCEI) y llevado a la malla SOME de 0.25°"),
            "comment": INSIGNIA_REALES, "insignia": INSIGNIA_REALES, "datos_reales": 1, "procedencia": "real",
            "credito": CREDITO, "identificador": self.identificador, "licencia": self.licencia,
            "fuente_id": self.id, "fecha_corte": corte.isoformat(), "resolucion": self.resolucion,
            "producto": PRODUCTO, "doi": DOI,
        }

    @staticmethod
    def _marcar_simulado(ds: xr.Dataset) -> xr.Dataset:
        ds.attrs.update({"datos_reales": 0, "procedencia": "simulado", "insignia": cfg.INSIGNIA_SIMULADOS})
        return ds

    def _respaldo(self, p: ParametrosConsulta, motivo: str, corte: date) -> xr.Dataset:
        """Respaldo simulado honesto: todo el Dataset del simulador, etiquetado y con el motivo."""
        self._sim_respaldo.fecha_corte_simulada = max(FECHA_CORTE_SIMULADA, corte)
        ds = self._marcar_simulado(self._sim_respaldo.obtener(p))
        ds.attrs["respaldo_simulado"] = 1
        ds.attrs["aviso"] = (f"NOAA OISST no está disponible: "
                             f"{estado.inicial_minuscula(motivo.rstrip('.')) or 'sin acceso'}. Esta consulta usa datos "
                             "SIMULADOS para demostración.")
        return ds


__all__ = ["ConectorNOAA", "texto_origen", "INSIGNIA_REALES", "CREDITO", "ErrorSinDatos"]
