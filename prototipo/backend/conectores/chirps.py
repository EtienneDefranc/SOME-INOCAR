"""Conector REAL de CHIRPS v3 (precipitación diaria sobre tierra, Climate Hazards Center, UCSB).

Sirve los datos desde la caché local `datos/chirps/` (malla SOME de 0.25°),
que mantiene al día el hilo de sincronización de `backend/chirps/`. Si en una
consulta faltan días y hay red, los obtiene en el momento: primero de la
réplica de dynamical.org (Zarr/Icechunk; se leen los fragmentos completos que
toca la consulta, que cuestan lo mismo que una parte) y lo que siga faltando,
del servidor del CHC (GeoTIFF por rangos HTTP, en serie, con un tope de días y
de tiempo). Ambas publican el producto del CHC (la réplica, con los valores
redondeados a 8 bits de mantisa para comprimir); cada día guarda su origen
y el Dataset lo declara (`origen_datos`, crédito según la mezcla real).

Fecha de corte efectiva (`fecha_corte_efectiva`): el último día en caché o, si
es posterior, lo último publicado por una fuente que responde ahora (réplica o
CHC, según lo verificado); con la caché vacía y sin nada verificado todavía no
hay fecha de corte y la consulta intenta el llenado bajo demanda (o explica que
la caché se está llenando).

Con `SOME_CHIRPS_SIN_RED=1` no toca la red: sirve solo la caché local.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import xarray as xr

from .. import configuracion as cfg
from .. import datos_simulados as sim
from ..chirps import almacen, climatologia as clim, descarga, red
from ..chirps import estado as estado_chirps
from ..chirps.constantes import (AUSENTE, CHC, CITA_REPOSITORIO, CREDITO_ESPEJO, DOI_REPOSITORIO, ESPEJO,
                                 ESPERA_ESPEJO_BAJO_DEMANDA_S, FINAL, MAX_DIAS_BAJO_DEMANDA, MAX_SEGUNDOS_BAJO_DEMANDA, MAX_SEGUNDOS_ESPEJO_BAJO_DEMANDA,
                                 ORIGEN_CHC, ORIGEN_CHC_P25, ORIGEN_ESPEJO, PRELIMINAR, sin_red)
from ..chirps.malla import TEXTO_PROCESAMIENTO
from .base import (_ATRIBUTOS_LAT, _ATRIBUTOS_LON, _ATRIBUTOS_MONTH, _ATRIBUTOS_TIME, ConectorFuente,
                   EstadoFuente, ParametrosConsulta, VariableFuente)

INSIGNIA_REALES = getattr(cfg, "INSIGNIA_REALES_CHIRPS", "Datos reales: CHIRPS v3 (CHC, UCSB)")

_REF_V3 = ("Funk, C., Peterson, P., Harrison, L., et al. (2026). The Climate Hazards Center Infrared "
           "Precipitation with Stations, Version 3. Scientific Data, 13, 718. "
           "https://doi.org/10.1038/s41597-026-07096-4")
_REF_2015 = ("Funk, C. et al. (2015). The climate hazards infrared precipitation with stations — a new "
             "environmental record for monitoring extremes. Scientific Data, 2, 150066. "
             "https://doi.org/10.1038/sdata.2015.66")
# CHIRPS3 se publica con CC BY 4.0 (el CHC lo declara además de dominio público): al compartir, el crédito
# debe citar la fuente, nombrar la licencia y enlazarla. El DOI del repositorio de datos y «CC BY 4.0» van en
# cada crédito; el enlace a la licencia, en `licencia` y en «Fuentes y métodos».
_CITA_CHC = f"CHIRPS v3.0, CHC, UC Santa Barbara (Funk et al., 2026; doi:{DOI_REPOSITORIO.removeprefix('https://doi.org/')})"
_LICENCIA_CC_BY = "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)"
_CREDITO_CHC = f"{_CITA_CHC}, CC BY 4.0."
# Créditos de una consulta que usó la réplica (≤ 150 caracteres: caben enteros en el pie de las
# figuras). La cita completa de la réplica va en `credito_replica` (CREDITO_ESPEJO).
CREDITO_REPLICA = {
    "todos": f"{_CITA_CHC}, vía la réplica de dynamical.org; CC BY 4.0.",
    "parte": f"{_CITA_CHC}; parte de los días vía la réplica de dynamical.org; CC BY 4.0.",
    "climatologia": f"{_CITA_CHC}; climatología calculada con la réplica de dynamical.org; CC BY 4.0.",
}


def credito_para_origen(n_chc: int, n_replica: int, climatologia_replica: bool = False) -> tuple[str, bool]:
    """(crédito, usa_replica) de una consulta según de dónde salieron REALMENTE sus datos.

    «vía la réplica» solo si todos los días con dato vienen de ella; «parte de los días» si se
    mezclan; «climatología calculada con la réplica» si ningún día viene de la réplica pero la
    climatología sí; si no, el crédito del CHC.
    """
    if n_replica and n_chc:
        return CREDITO_REPLICA["parte"], True
    if n_replica:
        return CREDITO_REPLICA["todos"], True
    if climatologia_replica:
        return CREDITO_REPLICA["climatologia"], True
    return _CREDITO_CHC, False


def _dias(n: int) -> str:
    return f"{cfg.fmt_num(n, 0)} {'día' if n == 1 else 'días'}"


def texto_origen(n_chc: int, n_espejo: int) -> str:
    """«servidor del CHC: 25 días; réplica dynamical.org: 340 días» (solo las fuentes que aportaron días)."""
    partes = []
    if n_chc:
        partes.append(f"servidor del CHC: {_dias(n_chc)}")
    if n_espejo:
        partes.append(f"réplica dynamical.org: {_dias(n_espejo)}")
    return "; ".join(partes) or "sin días con dato"


class ConectorCHIRPS(ConectorFuente):
    id = "chirps_v3"
    nombre = "CHIRPS v3 (Climate Hazards Center, UCSB)"
    nombre_corto = "CHIRPS v3"
    proveedor = "Climate Hazards Center, University of California, Santa Barbara"
    producto = "CHIRPS v3 — Climate Hazards Center InfraRed Precipitation with Stations, versión 3.0"
    identificador = "CHC-CHIRPS-v3.0"
    licencia = (f"{_LICENCIA_CC_BY}, según la página oficial del CHC (https://www.chc.ucsb.edu/data/chirps3), "
                "que además declara CHIRPS3 de dominio público. Al compartir hay que citar la fuente, enlazar la "
                "licencia e indicar los cambios. La réplica de dynamical.org publica el producto con los valores "
                "redondeados, con licencia CC BY 4.0 y atribución propia.")
    _referencias_base = f"{_REF_V3} — {CITA_REPOSITORIO} — {_REF_2015}"
    resolucion = "0.05° y 0.25° diario (en SOME: 0.25°)"
    cobertura_temporal = ("Desde enero de 1981; en SOME desde 01/10/2023 hasta el último día publicado")
    datos_reales = True
    insignia = INSIGNIA_REALES

    def __init__(self):
        self.habilitada = True
        self.variables = [
            VariableFuente(
                id="precip",
                nombre="Precipitación",
                unidad="mm/día",
                descripcion="Precipitación diaria acumulada sobre tierra (satélite infrarrojo más "
                            "estaciones, CHIRPS v3; los días se obtienen disgregando el total pentadal).",
                paleta="YlGnBu",
                paleta_anomalia="BrBG",
                standard_name="lwe_precipitation_rate",
                long_name="Precipitación (tasa equivalente de agua líquida)",
                unidades_cf="mm day-1",
                decimales=1,
            ),
        ]

    @property
    def referencias(self) -> str:
        """Artículo de Funk et al. (2026), repositorio de datos del CHC (DOI 10.15780/G2JQ0P) con la fecha
        de acceso (última descarga guardada en la caché local) y el artículo de 2015."""
        try:
            acceso = almacen.ultima_obtencion()
        except Exception:  # noqa: BLE001
            acceso = None
        if acceso is None:
            return self._referencias_base
        return f"{self._referencias_base} — Datos consultados por última vez el {cfg.fmt_fecha(acceso)}."

    @property
    def credito(self) -> str:
        """Crédito del CHC y, si la caché tiene días obtenidos de la réplica, también el de dynamical.org."""
        try:
            usa_replica = almacen.conteo_origenes().get(ESPEJO, 0) > 0
        except Exception:  # noqa: BLE001
            usa_replica = False
        return (f"{_CREDITO_CHC} Parte de los datos de la caché local proviene de la réplica de dynamical.org."
                if usa_replica else _CREDITO_CHC)

    # ------------------------------------------------------------------ contrato
    @staticmethod
    def fecha_corte_efectiva() -> date | None:
        """Último día que se puede servir, sin red: el último en caché o, si es posterior, lo último que
        publica la réplica de dynamical.org si responde ahora (llenarlo bajo demanda tarda unos segundos).
        Con la caché vacía cuenta también lo último que lista el CHC si responde. Los días que solo tiene
        el CHC (más recientes que la réplica) los trae la sincronización en segundo plano, a 1 pet./s:
        bajarlos dentro de una consulta la haría tardar ~4 s por día. En modo sin red, solo la caché.
        None si la caché está vacía y aún no se conoce nada publicado."""
        corte = almacen.ultimo_dia()
        if sin_red():
            return corte
        try:
            publicado = estado_chirps.ultima_publicada_accesible(incluir_chc=corte is None)
        except Exception:  # noqa: BLE001
            publicado = None
        fechas = [f for f in (corte, publicado) if f is not None]
        return max(fechas) if fechas else None

    def estado(self) -> EstadoFuente:
        e = estado_chirps.estado_actual()
        # Sin ningún día servible (caché vacía, nada verificado) la fecha de corte queda ANTES del inicio
        # de la demo: el motor lo entiende como «la fuente aún no tiene datos» y deja que obtener()
        # intente el llenado bajo demanda o explique que la caché se está llenando.
        corte = self.fecha_corte_efectiva() or (cfg.FECHA_INICIO - timedelta(days=1))
        return EstadoFuente(fuente=self.id, nombre=self.nombre, estado=e["estado"],
                            fecha_corte=corte.isoformat(), latencia_ms=int(e["latencia_ms"]),
                            ultimo_chequeo=e["ultimo_chequeo"], mensaje=e["mensaje"])

    def historial_estado(self, dias: int = 14) -> list[dict]:
        return estado_chirps.historial_estado(dias)

    def detalle_estado(self) -> dict:
        """Estado de cada camino (servidor del CHC / réplica dynamical.org), hasta dónde llega cada
        producto en cada uno y qué guarda la caché local, sin tocar la red (tarjeta de «Fuentes»)."""
        return estado_chirps.caminos()

    def obtener(self, p: ParametrosConsulta) -> xr.Dataset:
        var = self._variable_valida(p.variable)
        ilat, ilon = sim.indices_bbox(p.bbox)
        if ilat.size == 0 or ilon.size == 0:
            raise ValueError("El área solicitada no contiene ninguna celda de la malla de 0.25°.")
        inicio = max(p.fecha_inicio, cfg.FECHA_INICIO)
        # 1) Llenado bajo demanda ANTES de recortar a la caché: hasta lo último que se puede servir
        #    (o, sin nada verificado todavía, hasta el fin pedido sin pasar de hoy).
        if not sin_red():
            limite = self.fecha_corte_efectiva() or min(p.fecha_fin, date.today())
            fin_llenado = min(p.fecha_fin, limite, date.today())
            if fin_llenado >= inicio:
                if AUSENTE in almacen.tipos(inicio, fin_llenado).values():
                    self._completar(inicio, fin_llenado)
        corte = almacen.ultimo_dia()
        if corte is None:
            raise ValueError(
                "La caché local de CHIRPS v3 del SOME está vacía. "
                + ("Active la conexión a internet o ejecute «python -m backend.herramientas.preparar_chirps»."
                   if sin_red() else
                   "La sincronización la está llenando y en este momento no se pudo obtener ningún día de la "
                   "réplica de dynamical.org ni del CHC; inténtelo de nuevo en unos segundos."))
        fin = min(p.fecha_fin, corte)
        if fin < inicio:
            raise ValueError(
                f"El periodo solicitado ({cfg.fmt_fecha(p.fecha_inicio)} – {cfg.fmt_fecha(p.fecha_fin)}) "
                f"está fuera de la cobertura de {self.nombre} "
                f"({cfg.fmt_fecha(cfg.FECHA_INICIO)} – {cfg.fmt_fecha(corte)}).")

        valores, tipos, origenes = almacen.leer_con_origen(inicio, fin)
        if not (tipos > 0).any():
            raise ValueError(
                f"No hay datos CHIRPS v3 para el periodo {cfg.fmt_fecha(inicio)} – {cfg.fmt_fecha(fin)} "
                "en la caché local y no se pudieron obtener ni de la réplica de dynamical.org ni del CHC.")

        fechas = pd.date_range(inicio, fin, freq="D")
        datos = valores[:, ilat[:, None], ilon[None, :]].astype(np.float32)
        dias_prelim = int((tipos == PRELIMINAR).sum())
        dias_faltantes = int((tipos == AUSENTE).sum())
        con_dato = tipos > AUSENTE
        n_chc = int((con_dato & ((origenes == ORIGEN_CHC) | (origenes == ORIGEN_CHC_P25))).sum())
        n_espejo = int((con_dato & (origenes == ORIGEN_ESPEJO)).sum())
        n_provisionales = int(((tipos == FINAL) & (origenes == ORIGEN_CHC_P25)).sum())

        atributos = self._atributos_globales(var, corte)
        atributos["title"] = f"{var.nombre} — {self.nombre}"
        atributos["dias_preliminares"] = dias_prelim
        atributos["dias_faltantes"] = dias_faltantes
        atributos["dias_finales"] = int((tipos == FINAL).sum())
        atributos["origen_datos"] = texto_origen(n_chc, n_espejo)
        atributos["dias_origen_chc"] = n_chc
        atributos["dias_origen_replica"] = n_espejo
        atributos["procesamiento"] = (f"{TEXTO_PROCESAMIENTO} Origen de los días de esta consulta: "
                                      f"{atributos['origen_datos']}.")
        if n_provisionales:
            atributos["dias_finales_provisionales"] = n_provisionales
            atributos["procesamiento"] += (
                f" {n_provisionales} {'día final se tomó' if n_provisionales == 1 else 'días finales se tomaron'} "
                "del producto a 0.25° del CHC (versión anterior del SOME), cuya agregación difiere en la costa "
                "(otra máscara de tierra y hasta ~30 % en celdas costeras); la sincronización los reemplazará "
                "por el final a 0.05° agregado 5×5.")
        atributos["credito"], usa_replica = credito_para_origen(n_chc, n_espejo)
        if usa_replica:
            atributos["credito_replica"] = CREDITO_ESPEJO
            atributos["source"] += ("; réplica pública de dynamical.org (Zarr/Icechunk en S3, CC BY 4.0, "
                                    "valores redondeados a 8 bits de mantisa)")
        coords = {
            "time": ("time", fechas.values.astype("datetime64[ns]"), _ATRIBUTOS_TIME),
            "lat": ("lat", cfg.LAT[ilat], _ATRIBUTOS_LAT),
            "lon": ("lon", cfg.LON[ilon], _ATRIBUTOS_LON),
        }
        if dias_prelim:
            idx = np.nonzero(tipos == PRELIMINAR)[0]
            atributos["periodo_preliminar"] = (f"{(inicio + timedelta(days=int(idx[0]))).isoformat()}/"
                                               f"{(inicio + timedelta(days=int(idx[-1]))).isoformat()}")
        return xr.Dataset({var.id: (("time", "lat", "lon"), datos, self._atributos_variable(var))},
                          coords=coords, attrs=atributos)

    def climatologia(self, variable: str, periodo_referencia: str, bbox,
                     profundidad: float | None = None) -> xr.Dataset:
        var = self._variable_valida(variable)
        if periodo_referencia not in cfg.PERIODOS_REFERENCIA:
            raise ValueError(f"Periodo de referencia desconocido: «{periodo_referencia}». "
                             f"Disponibles: {', '.join(cfg.PERIODOS_REFERENCIA)}.")
        ilat, ilon = sim.indices_bbox(bbox)
        if ilat.size == 0 or ilon.size == 0:
            raise ValueError("El área solicitada no contiene ninguna celda de la malla de 0.25°.")
        a0, a1 = (int(x) for x in periodo_referencia.split("-"))
        campo = clim.climatologia_periodo(a0, a1)[:, ilat[:, None], ilon[None, :]]

        atributos_var = self._atributos_variable(var)
        atributos_var["cell_methods"] = "time: mean within months time: mean over years"
        coords = {
            "month": ("month", np.arange(1, 13), _ATRIBUTOS_MONTH),
            "lat": ("lat", cfg.LAT[ilat], _ATRIBUTOS_LAT),
            "lon": ("lon", cfg.LON[ilon], _ATRIBUTOS_LON),
        }
        atributos = self._atributos_globales(var, almacen.ultimo_dia())
        atributos["title"] = f"Climatología mensual {periodo_referencia} — {var.nombre} ({self.nombre})"
        atributos["periodo_referencia"] = periodo_referencia
        # Origen real de los años del periodo (réplica dynamical.org y/o NetCDF mensual del CHC).
        resumen = clim.resumen_origen(a0, a1)
        origenes = [o for o in (clim.ORIGEN_REPLICA, clim.ORIGEN_CHC) if resumen[o]]
        etiqueta = {clim.ORIGEN_REPLICA: "años de la réplica dynamical.org",
                    clim.ORIGEN_CHC: "años del NetCDF del CHC"}
        if len(origenes) == 1:
            metodo_anual = clim.TEXTO_METODO[origenes[0]]
        else:
            metodo_anual = "; ".join(f"{etiqueta[o]}: {clim.TEXTO_METODO[o]}" for o in origenes)
        atributos["metodo"] = (f"{clim.texto_origen_periodo(a0, a1)}. Para cada año {a0}-{a1}: {metodo_anual}; "
                               "después se promediaron los años mes a mes.")
        paso_1 = {clim.ORIGEN_REPLICA: ("diario final 0.05° de la réplica dynamical.org → media mensual por celda "
                                        "(días con dato, mínimo 80 %)"),
                  clim.ORIGEN_CHC: "total mensual global 0.05° del NetCDF del CHC (÷ días del mes)"}
        atributos["procesamiento"] = (
            "CHIRPS v3 mensual: " + " o ".join(paso_1[o] for o in origenes or [clim.ORIGEN_REPLICA])
            + " → bloques de 5×5 (mínimo 13 de 25 válidas) → malla SOME (media de 4 celdas, mínimo 2) "
            "→ promedio de los años.")
        if resumen[clim.ORIGEN_REPLICA]:
            atributos["credito"] = CREDITO_REPLICA["climatologia"]
            atributos["credito_replica"] = CREDITO_ESPEJO
            atributos["source"] = ("CHIRPS v3.0 del Climate Hazards Center (UCSB), diario final «rnl» leído de la "
                                   "réplica pública de dynamical.org (Zarr/Icechunk en S3, CC BY 4.0, valores "
                                   "redondeados a 8 bits de mantisa)"
                                   + ("; NetCDF mensual del CHC (https://data.chc.ucsb.edu/products/CHIRPS/v3.0/)"
                                      if resumen[clim.ORIGEN_CHC] else ""))
        else:
            atributos["credito"] = _CREDITO_CHC
            atributos["source"] = ("CHIRPS v3.0 mensual del Climate Hazards Center (UCSB), NetCDF global; "
                                   "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/")
        atributos["origen_climatologia"] = clim.texto_origen_periodo(a0, a1)
        return xr.Dataset({var.id: (("month", "lat", "lon"), campo.astype(np.float32), atributos_var)},
                          coords=coords, attrs=atributos)

    # ---------------------------------------------------------------- auxiliares
    def _completar(self, inicio: date, fin: date) -> None:
        """Obtiene en el momento los días AUSENTES de la consulta.

        1. Réplica de dynamical.org: todo lo que cubra, ampliado a los fragmentos completos que
           toca la consulta (cada lectura descarga el fragmento entero de 365 días). No se intenta
           si una lectura de la réplica falló hace poco (`descarga.espejo_caido_reciente`) y no se
           espera más de `ESPERA_ESPEJO_BAJO_DEMANDA_S` a otra lectura en curso.
        2. Servidor del CHC: lo que siga faltando y esté publicado, en serie (1 pet./s),
           como máximo los `MAX_DIAS_BAJO_DEMANDA` días más recientes y
           `MAX_SEGUNDOS_BAJO_DEMANDA` segundos; no se intenta si el CHC está marcado caído.
        Los reemplazos de preliminar por final los hace la sincronización.
        """
        # 1) Réplica
        try:
            cob = None if descarga.espejo_caido_reciente() else descarga.cobertura_espejo()
            if descarga.espejo_accesible(cob):
                plan: dict = {}
                for a, b in descarga.ampliar_a_fragmentos(inicio, fin, cob, minimo=cfg.FECHA_INICIO):
                    plan.update(descarga.plan_llenado(a, b, None, cob, ESPEJO, solo_ausentes=True))
                if plan:
                    # Con un presupuesto de tiempo: si la réplica se cuelga, la consulta sirve lo que haya
                    # y la lectura sigue en segundo plano (si falla, marca la réplica caída).
                    resultado: dict = {}

                    def leer():
                        try:
                            resultado["r"] = descarga.ejecutar_plan(plan, fuente=ESPEJO, cob=cob,
                                                                    espera_espejo=ESPERA_ESPEJO_BAJO_DEMANDA_S)
                            for e in resultado["r"]["detalle_errores"][:3]:
                                estado_chirps.registrar_error(f"Réplica bajo demanda: {e}")
                        except Exception as e:  # noqa: BLE001
                            estado_chirps.registrar_error(f"Réplica bajo demanda: {e}")

                    hilo = threading.Thread(target=leer, name="chirps-replica-bajo-demanda", daemon=True)
                    hilo.start()
                    hilo.join(MAX_SEGUNDOS_ESPEJO_BAJO_DEMANDA)
        except Exception as e:  # noqa: BLE001 — se sirve lo que haya
            estado_chirps.registrar_error(f"Réplica bajo demanda: {e}")

        # 2) CHC
        faltan = [d for d, t in almacen.tipos(inicio, fin).items() if t == AUSENTE]
        if not faltan or self._chc_caido_hace_poco():
            return
        try:
            disp = descarga.disponibilidad(anios={d.year for d in faltan})
        except Exception as e:  # noqa: BLE001 — CHC caído: se sirve lo que haya
            estado_chirps.registrar_error(f"Consulta de disponibilidad del CHC: {e}")
            return
        plan = descarga.plan_llenado(inicio, fin, disp, None, CHC, solo_ausentes=True)
        if plan:
            r = descarga.ejecutar_plan(plan, disp=disp, fuente=CHC, max_dias_chc=MAX_DIAS_BAJO_DEMANDA,
                                       max_segundos_chc=MAX_SEGUNDOS_BAJO_DEMANDA, mas_recientes_primero=True,
                                       espera_archivo=30)
            estado_chirps.sumar_bytes(r["bytes"])
            for e in r["detalle_errores"][:3]:
                estado_chirps.registrar_error(f"Descarga bajo demanda del CHC: {e}")

    @staticmethod
    def _chc_caido_hace_poco() -> bool:
        """True si el disyuntor está abierto o el último contacto conocido con el CHC (de este u otro
        proceso) falló. Una consulta nunca sondea un CHC caído: eso lo hace la sincronización en
        segundo plano (cada 30 min mientras falle), que es la que registra su recuperación."""
        if red.servidor_caido_reciente():
            return True
        try:
            return estado_chirps.chc_accesible() is False
        except Exception:  # noqa: BLE001
            return False

    def _variable_valida(self, variable_id: str) -> VariableFuente:
        var = self.variable(variable_id)
        if var is None:
            disponibles = ", ".join(v.id for v in self.variables)
            raise ValueError(f"La fuente {self.nombre} no publica la variable «{variable_id}». "
                             f"Disponibles: {disponibles}.")
        return var

    def _atributos_variable(self, var: VariableFuente) -> dict:
        return {
            "standard_name": var.standard_name,
            "long_name": var.long_name,
            "units": var.unidades_cf,
            "unidad_mostrada": var.unidad,
            "coordinates": "time lat lon",
            "comment": self.insignia,
        }

    def _atributos_globales(self, var: VariableFuente, corte: date | None) -> dict:
        return {
            "Conventions": "CF-1.8",
            "title": f"{var.nombre} — {self.nombre}",
            "source": ("CHIRPS v3.0 diario del Climate Hazards Center (UCSB): final «rnl» "
                       "(disgregación ERA5) y preliminar «sat» (disgregación IMERG); "
                       "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/"),
            "institution": self.proveedor,
            "references": self.referencias,
            "history": (f"{datetime.now().isoformat(timespec='seconds')} SOME {cfg.VERSION}: leído de la "
                        "caché local datos/chirps (llenada desde el servidor del CHC por rangos HTTP o "
                        "desde la réplica Zarr/Icechunk de dynamical.org) y reproyectado a la malla SOME "
                        "de 0.25°"),
            "comment": self.insignia,
            "datos_reales": 1,
            "procesamiento": TEXTO_PROCESAMIENTO,
            "credito": self.credito,
            "identificador": self.identificador,
            "licencia": self.licencia,
            "fuente_id": self.id,
            "fecha_corte": (corte or cfg.FECHA_INICIO).isoformat(),
            "resolucion": self.resolucion,
        }
