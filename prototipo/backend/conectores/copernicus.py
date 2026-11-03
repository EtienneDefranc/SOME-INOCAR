"""Conector de Copernicus Marine GLOBAL_ANALYSISFORECAST_PHY_001_024 (datos reales con respaldo simulado).

Variables físicas con profundidad (thetao, so, uo, vo), zos y mlotst en
superficie y el vector «corrientes» (uo, vo). Los datos salen de la caché local
`datos/copernicus/` (malla SOME de 0.25°, 8 niveles de 0 a 100 m), que llenan
la sincronización en segundo plano, la herramienta `preparar_copernicus` y, en
el momento, las consultas (ver `backend/copernicus/`). Solo se sirve ANÁLISIS;
la fecha de corte es el último día de análisis (`W − 2`).

Procedencia honesta (§3.8 del diseño):
* modo «real» (hay caché o acceso): Dataset real (`datos_reales=1`); si parte
  de lo pedido no está en la caché y no se pudo obtener, esas celdas quedan sin
  dato y el Dataset lo dice (`aviso`);
* si a una consulta no le queda NINGÚN dato real: respaldo con el simulador,
  etiquetado «simulado» (`datos_reales=0`, `respaldo_simulado=1`) y con un
  aviso que dice por qué (en modo `SOME_COPERNICUS_MODO=real`, error 422);
* modo «simulado» (sin caché ni acceso, o `SOME_COPERNICUS_MODO=simulado`): el
  simulador anterior, idéntico.
Nunca se mezclan días simulados con reales dentro de un mismo Dataset.

Climatología: la oficial de GLORYS12 (1993-2016, según `climatology_bounds`),
declarada siempre como periodo efectivo junto con el pedido.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import xarray as xr

from .. import configuracion as cfg
from .. import copernicus as nucleo
from .. import datos_simulados as sim
from ..copernicus import almacen as almacen_cmems
from ..copernicus import climatologia as clim_cmems
from ..copernicus import estado as estado_cmems
from ..copernicus.constantes import (DATASET_POR_VARIABLE, DOI_ANFC, DOI_GLORYS, PRODUCTO_ANFC, PRODUCTO_GLORYS,
                                     NIVELES_SOME, ErrorCopernicus, ErrorDescargaEnCurso, espera_bajo_demanda_s,
                                     modo_configurado)
from ..copernicus.malla import TEXTO_PROCESAMIENTO
from .base import (_ATRIBUTOS_DEPTH, _ATRIBUTOS_LAT, _ATRIBUTOS_LON, _ATRIBUTOS_MONTH, _ATRIBUTOS_TIME,
                   ConectorFuente, ConectorSimulado, EstadoFuente, ParametrosConsulta, VariableFuente)

log = logging.getLogger("some.copernicus")

INSIGNIA_REALES = getattr(cfg, "INSIGNIA_REALES_COPERNICUS", "Datos reales · Copernicus Marine")
# Las salidas del SOME son obras derivadas (malla de 0.25°, interpolación vertical, anomalías): la licencia de
# Copernicus Marine (cláusula 2.4.a) pide nombrar al originador y citar los DOI como enlaces, con la forma
# recomendada «Generated using E.U. Copernicus Marine Service Information; [DOI]». Ver CONDICIONES-USO-FUENTES.md.
ORIGINADOR = "Generated using E.U. Copernicus Marine Service Information"
CREDITO = (f"{ORIGINADOR}; análisis diario, https://doi.org/{DOI_ANFC}; climatología GLORYS12, "
           f"https://doi.org/{DOI_GLORYS}.")
CREDITO_DATOS = f"{ORIGINADOR}; análisis diario, https://doi.org/{DOI_ANFC}."
LICENCIA = ("Licencia de uso de los productos del Copernicus Marine Service "
            "(https://marine.copernicus.eu/user-corner/service-commitments-and-licence): gratuita, mundial y "
            "perpetua; exige el crédito con los DOI, declarar las modificaciones y no sugerir aval de la UE. "
            "Sin garantía.")
REFERENCIAS = f"https://doi.org/{DOI_ANFC} — https://doi.org/{DOI_GLORYS}"
FECHA_CORTE_SIMULADA = date(2026, 10, 26)
CELL_METHODS_REAL = "time: mean (diario) area: mean (3 × 3 celdas de 1/12°)"


def _variables() -> list[VariableFuente]:
    """Las 7 variables de la fuente (ids, unidades, paletas y decimales de siempre: la interfaz depende de ellos)."""
    return [
        VariableFuente(
            id="thetao",
            nombre="Temperatura del agua (por profundidad)",
            unidad="°C",
            descripcion="Temperatura potencial del agua de mar en cada nivel de profundidad.",
            paleta="termico", paleta_anomalia="PuOr_r",
            standard_name="sea_water_potential_temperature",
            long_name="Temperatura potencial del agua de mar",
            unidades_cf="degC", tiene_profundidad=True, decimales=1,
        ),
        VariableFuente(
            id="so",
            nombre="Salinidad",
            unidad="PSU",
            descripcion="Salinidad práctica del agua de mar en cada nivel de profundidad.",
            paleta="salino", paleta_anomalia="PuOr_r",
            standard_name="sea_water_salinity",
            long_name="Salinidad del agua de mar",
            unidades_cf="1e-3", tiene_profundidad=True, decimales=2,
        ),
        VariableFuente(
            id="uo",
            nombre="Velocidad zonal",
            unidad="m/s",
            descripcion="Componente este-oeste de la corriente (positiva hacia el este).",
            paleta="RdBu_r", paleta_anomalia="PuOr_r",
            standard_name="eastward_sea_water_velocity",
            long_name="Velocidad zonal del agua de mar",
            unidades_cf="m s-1", tiene_profundidad=True, decimales=2,
        ),
        VariableFuente(
            id="vo",
            nombre="Velocidad meridional",
            unidad="m/s",
            descripcion="Componente norte-sur de la corriente (positiva hacia el norte).",
            paleta="RdBu_r", paleta_anomalia="PuOr_r",
            standard_name="northward_sea_water_velocity",
            long_name="Velocidad meridional del agua de mar",
            unidades_cf="m s-1", tiene_profundidad=True, decimales=2,
        ),
        VariableFuente(
            id="corrientes",
            nombre="Corrientes (vectores)",
            unidad="m/s",
            descripcion="Vectores de corriente (uo, vo) y su rapidez.",
            paleta="viridis", paleta_anomalia="PuOr_r",
            standard_name="sea_water_speed",
            long_name="Rapidez de la corriente marina",
            unidades_cf="m s-1", tiene_profundidad=True, es_vector=True,
            componentes=["uo", "vo"], decimales=2,
        ),
        VariableFuente(
            id="zos",
            nombre="Altura del nivel del mar",
            unidad="m",
            descripcion="Altura de la superficie del mar sobre el geoide.",
            paleta="viridis", paleta_anomalia="PuOr_r",
            standard_name="sea_surface_height_above_geoid",
            long_name="Altura de la superficie del mar sobre el geoide",
            unidades_cf="m", decimales=3,
        ),
        VariableFuente(
            id="mlotst",
            nombre="Profundidad de la capa de mezcla",
            unidad="m",
            descripcion="Espesor de la capa de mezcla oceánica definido por sigma-theta.",
            paleta="cividis", paleta_anomalia="PuOr_r",
            standard_name="ocean_mixed_layer_thickness_defined_by_sigma_theta",
            long_name="Profundidad de la capa de mezcla (sigma-theta)",
            unidades_cf="m", decimales=0,
        ),
    ]


class _SimuladorCopernicus(ConectorSimulado):
    """El simulador anterior de Copernicus Marine (modo simulado y respaldo), idéntico al de siempre."""

    id = "copernicus_phy"
    nombre = "Copernicus Marine GLOBAL_ANALYSISFORECAST_PHY_001_024"
    nombre_corto = "Copernicus Marine"
    proveedor = "E.U. Copernicus Marine Service (Mercator Ocean International)"
    producto = "GLOBAL_ANALYSISFORECAST_PHY_001_024 (análisis y pronóstico físico global)"
    identificador = "CMEMS-GLOBAL_ANALYSISFORECAST_PHY_001_024"
    # El simulador imita la forma del producto, pero sus valores no son de Copernicus: el crédito no se los atribuye
    credito = ("Simulación del SOME con la forma de Copernicus Marine GLOBAL_ANALYSISFORECAST_PHY_001_024; "
               "no son datos de Copernicus.")
    licencia = LICENCIA
    referencias = "https://doi.org/10.48670/moi-00016"
    resolucion = "1/12° diario, 50 niveles (en la demo: 0.25° y 8 niveles hasta 100 m)"
    cobertura_temporal = "Desde noviembre de 2020 (en la demo: 01/10/2023 – 26/10/2026)"

    estado_simulado = "operativa"
    fecha_corte_simulada = FECHA_CORTE_SIMULADA
    latencia_simulada_ms = 1450
    mensaje_estado = "Copernicus Marine Toolbox autenticado; análisis diario disponible."

    def __init__(self, variables: list[VariableFuente]):
        super().__init__()
        self.variables = variables          # la MISMA lista que el conector (habilitaciones compartidas)


def _texto_dias(n: int) -> str:
    return f"{cfg.fmt_num(n, 0)} {'día' if n == 1 else 'días'}"


def texto_origen(definitivos: int, nrt: int) -> str:
    """«Copernicus Marine (análisis ANFC): 358 días de mejor análisis y 7 de análisis NRT» (sin partes en cero)."""
    if definitivos and nrt:
        detalle = f"{_texto_dias(definitivos)} de mejor análisis y {cfg.fmt_num(nrt, 0)} de análisis NRT"
    elif nrt:
        detalle = f"{_texto_dias(nrt)} de análisis NRT"
    elif definitivos:
        detalle = f"{_texto_dias(definitivos)} de mejor análisis"
    else:
        detalle = "sin días con dato"
    return f"Copernicus Marine (análisis ANFC): {detalle}"


class ConectorCopernicus(ConectorFuente):
    id = "copernicus_phy"
    nombre = "Copernicus Marine GLOBAL_ANALYSISFORECAST_PHY_001_024"
    nombre_corto = "Copernicus Marine"
    proveedor = "E.U. Copernicus Marine Service (Mercator Ocean International)"
    producto = "GLOBAL_ANALYSISFORECAST_PHY_001_024 (análisis y pronóstico físico global; en SOME solo el análisis)"
    identificador = "CMEMS-GLOBAL_ANALYSISFORECAST_PHY_001_024"
    licencia = LICENCIA

    def __init__(self):
        self.habilitada = True
        self.variables = _variables()
        self._sim = _SimuladorCopernicus(self.variables)
        self._sim_respaldo = _SimuladorCopernicus(self.variables)

    # ------------------------------------------------------------ procedencia dinámica
    @property
    def datos_reales(self) -> bool:
        try:
            return nucleo.modo() == "real"
        except Exception:  # noqa: BLE001
            return False

    @property
    def insignia(self) -> str:
        return INSIGNIA_REALES if self.datos_reales else cfg.INSIGNIA_SIMULADOS

    @property
    def credito(self) -> str:
        return CREDITO

    @property
    def referencias(self) -> str:
        try:
            acceso = almacen_cmems.ultima_obtencion()
        except Exception:  # noqa: BLE001
            acceso = None
        if acceso is None:
            return REFERENCIAS
        return f"{REFERENCIAS} — Datos consultados por última vez el {cfg.fmt_fecha(acceso)}."

    @property
    def resolucion(self) -> str:
        if self.datos_reales:
            return "1/12° diario, 50 niveles (en SOME: 0.25° y 8 niveles de 0 a 100 m, interpolados)"
        return self._sim.resolucion

    @property
    def cobertura_temporal(self) -> str:
        if self.datos_reales:
            return "Análisis diario desde 01/06/2022; en SOME desde 01/10/2023 hasta el último día de análisis"
        return self._sim.cobertura_temporal

    # ------------------------------------------------------------ estado (nunca lanza)
    @staticmethod
    def fecha_corte_efectiva() -> date | None:
        """Último día de análisis servible (None en modo simulado)."""
        try:
            return nucleo.fecha_corte()
        except Exception:  # noqa: BLE001
            return None

    def _corte(self) -> date:
        corte = self.fecha_corte_efectiva() if self.datos_reales else None
        if corte is None:
            return self._sim.fecha_corte_simulada
        return max(corte, cfg.FECHA_INICIO)

    def estado(self) -> EstadoFuente:
        try:
            e = nucleo.estado_actual()
            return EstadoFuente(fuente=self.id, nombre=self.nombre, estado=e["estado"],
                                fecha_corte=self._corte().isoformat(), latencia_ms=int(e["latencia_ms"]),
                                ultimo_chequeo=e["ultimo_chequeo"], mensaje=e["mensaje"])
        except Exception as ex:  # noqa: BLE001 — el estado nunca tumba la API
            log.exception("Copernicus: error en estado()")
            return EstadoFuente(fuente=self.id, nombre=self.nombre, estado="sin_conexion",
                                fecha_corte=self._sim.fecha_corte_simulada.isoformat(), latencia_ms=0,
                                ultimo_chequeo=datetime.now().isoformat(timespec="seconds"),
                                mensaje=f"No se pudo evaluar el estado de Copernicus Marine: {ex}")

    def historial_estado(self, dias: int = 14) -> list[dict]:
        return nucleo.historial_estado(dias)

    def detalle_estado(self) -> dict:
        return nucleo.detalle_estado()

    # ------------------------------------------------------------ contrato
    def obtener(self, p: ParametrosConsulta) -> xr.Dataset:
        var = self._variable_valida(p.variable)
        if not self.datos_reales:
            return self._marcar_simulado(self._sim.obtener(p))
        ilat, ilon = sim.indices_bbox(p.bbox)
        if ilat.size == 0 or ilon.size == 0:
            raise ValueError("El área solicitada no contiene ninguna celda de la malla de 0.25°.")
        profundidades = self._profundidades(var, p)
        corte = self._corte()
        inicio = max(p.fecha_inicio, cfg.FECHA_INICIO)
        fin = min(p.fecha_fin, corte)
        if fin < inicio:
            raise ValueError(
                f"El periodo solicitado ({cfg.fmt_fecha(p.fecha_inicio)} – {cfg.fmt_fecha(p.fecha_fin)}) "
                f"está fuera de la cobertura de {self.nombre} "
                f"({cfg.fmt_fecha(cfg.FECHA_INICIO)} – {cfg.fmt_fecha(corte)}).")
        componentes = var.componentes or [var.id]
        niveles = [int(z) for z in profundidades] if profundidades is not None else None

        # 1) Llenado bajo demanda de lo que falte (ValueError → 422; ErrorDescargaEnCurso → 503)
        motivo = ""
        if nucleo.acceso_disponible():
            try:
                nucleo.llenar_cache(componentes, niveles, inicio, fin, ilat, ilon)
            except (ValueError, ErrorDescargaEnCurso):
                raise
            except ErrorCopernicus as e:
                motivo = estado_cmems.legible(str(e), 240).rstrip(".")
            except Exception as e:  # noqa: BLE001 — se sirve lo que haya y se dice
                log.exception("Copernicus: error inesperado al completar la caché")
                motivo = estado_cmems.legible(f"error inesperado ({type(e).__name__})", 240).rstrip(".")
        else:
            motivo = nucleo.motivo_sin_acceso()

        # 2) Lectura de la caché
        lecturas = {comp: [almacen_cmems.leer(comp, n, inicio, fin, ilat, ilon) for n in (niveles or [None])]
                    for comp in componentes}
        tipos = np.stack([lec.tipo for lista in lecturas.values() for lec in lista])   # (k, n, lat, lon)
        cobertura = float((tipos > 0).mean()) if tipos.size else 0.0
        if cobertura == 0.0:
            motivo = motivo or "los datos pedidos no están en la caché local"
            if modo_configurado() == "real":
                raise ValueError(
                    f"No hay datos de Copernicus Marine para {var.nombre} entre el {cfg.fmt_fecha(inicio)} y el "
                    f"{cfg.fmt_fecha(fin)} en la caché local y no se pudieron obtener: "
                    f"{estado_cmems.inicial_minuscula(motivo.rstrip('.'))}. Ejecute «python -m "
                    "backend.herramientas.preparar_copernicus» o revise «Estado de fuentes».")
            return self._respaldo(p, motivo, corte)

        # 3) Dataset real
        obtenido_dia = (tipos > 0).any(axis=(0, 2, 3))
        nrt_dia = (tipos == 1).any(axis=(0, 2, 3))
        dias_faltantes = int((~obtenido_dia).sum())
        dias_nrt = int((obtenido_dia & nrt_dia).sum())
        dias_definitivos = int((obtenido_dia & ~nrt_dia).sum())
        fechas = pd.date_range(inicio, fin, freq="D")
        variables_datos = {}
        for comp in componentes:
            comp_var = self.variable(comp) or var
            if niveles is not None:
                datos = np.stack([lec.valores for lec in lecturas[comp]], axis=1)      # (time, depth, lat, lon)
                dims = ("time", "depth", "lat", "lon")
            else:
                datos = lecturas[comp][0].valores                                       # (time, lat, lon)
                dims = ("time", "lat", "lon")
            variables_datos[comp] = (dims, datos.astype(np.float32), self._atributos_variable(comp_var, real=True))
        coords = {"time": ("time", fechas.values.astype("datetime64[ns]"), _ATRIBUTOS_TIME),
                  "lat": ("lat", cfg.LAT[ilat], _ATRIBUTOS_LAT),
                  "lon": ("lon", cfg.LON[ilon], _ATRIBUTOS_LON)}
        if niveles is not None:
            coords["depth"] = ("depth", np.array(profundidades, dtype=float), _ATRIBUTOS_DEPTH)
        attrs = self._atributos_reales(var, corte, componentes)
        # Sin partes en cero, como texto_origen: «31 de mejor análisis», no «31 de mejor análisis y 0 de análisis NRT»
        partes_dias = [texto for n, texto in (
            (dias_definitivos, f"{cfg.fmt_num(dias_definitivos, 0)} de mejor análisis"),
            (dias_nrt, f"{cfg.fmt_num(dias_nrt, 0)} de análisis NRT")) if n]
        attrs.update({
            "procesamiento": (f"{TEXTO_PROCESAMIENTO} Días de esta consulta: "
                              f"{' y '.join(partes_dias) or 'ninguno con dato'}."),
            "origen_datos": texto_origen(dias_definitivos, dias_nrt),
            "dias_faltantes": dias_faltantes, "dias_preliminares": 0, "dias_nrt": dias_nrt,
            "dias_definitivos": dias_definitivos, "cobertura": round(cobertura, 4),
        })
        if dias_nrt:
            idx = np.nonzero(obtenido_dia & nrt_dia)[0]
            attrs["periodo_nrt"] = (f"{(inicio + timedelta(days=int(idx[0]))).isoformat()}/"
                                    f"{(inicio + timedelta(days=int(idx[-1]))).isoformat()}")
        if cobertura < 1.0:
            # Sin paréntesis alrededor del motivo: puede traer los suyos y anidados se vuelven ilegibles
            attrs["aviso"] = (f"Parte del área o del periodo no está en la caché local y no se pudo obtener ahora: "
                              f"{estado_cmems.inicial_minuscula(motivo.rstrip('.')) or 'sin detalle'}. Esas celdas "
                              "quedan sin dato.")
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
        z = None
        if var.tiene_profundidad:
            z = float(profundidad or 0.0)
            self._validar_nivel(z)
        componentes = var.componentes or [var.id]
        try:
            disponible = nucleo.acceso_disponible()
            espera = espera_bajo_demanda_s()
            climas = {comp: nucleo.climatologia_mensual(comp, z, llenar=disponible, espera_s=espera)
                      for comp in componentes}
        except (ValueError, ErrorDescargaEnCurso):
            raise
        except ErrorCopernicus as e:
            motivo = estado_cmems.inicial_minuscula(estado_cmems.legible(str(e), 240).rstrip("."))
            if modo_configurado() == "real":
                raise ValueError(f"La climatología GLORYS12 de {var.nombre} no está disponible: {motivo}.") from e
            ds = self._marcar_simulado(self._sim.climatologia(variable, periodo_referencia, bbox, profundidad))
            ds.attrs["aviso"] = (f"Climatología simulada: la climatología GLORYS12 de {var.nombre} no está en la caché "
                                 f"local y no se pudo obtener: {motivo}.")
            ds.attrs["respaldo_simulado"] = 1
            ds.attrs["periodo_referencia"] = periodo_referencia
            return ds

        primera = climas[componentes[0]]
        variables_datos = {}
        for comp in componentes:
            comp_var = self.variable(comp) or var
            atributos = self._atributos_variable(comp_var, real=True)
            atributos["cell_methods"] = "time: mean within months time: mean over years"
            valores = climas[comp].valores[:, ilat[:, None], ilon[None, :]]
            variables_datos[comp] = (("month", "lat", "lon"), valores.astype(np.float32), atributos)
        coords = {"month": ("month", np.arange(1, 13), _ATRIBUTOS_MONTH),
                  "lat": ("lat", cfg.LAT[ilat], _ATRIBUTOS_LAT),
                  "lon": ("lon", cfg.LON[ilon], _ATRIBUTOS_LON)}
        attrs = {
            "Conventions": "CF-1.8",
            "title": f"Climatología mensual {primera.periodo} (GLORYS12) — {var.nombre}",
            "source": (f"{PRODUCTO_GLORYS}: cmems_mod_glo_phy_my_0.083deg-climatology_P1M-m (climatología mensual "
                       "oficial del reanálisis GLORYS12), servicio ARCO de Copernicus Marine"),
            "institution": self.proveedor,
            "references": f"https://doi.org/{DOI_GLORYS}",
            "history": (f"{datetime.now().isoformat(timespec='seconds')} SOME {cfg.VERSION}: leída de la caché local "
                        "datos/copernicus/climatologia (llenada con copernicusmarine.open_dataset) y llevada a la "
                        "malla SOME de 0.25°"),
            "comment": INSIGNIA_REALES, "insignia": INSIGNIA_REALES, "datos_reales": 1, "procedencia": "real",
            "periodo_referencia": periodo_referencia, "periodo_efectivo": primera.periodo,
            "periodo_fuente": primera.fuente_periodo,
            "metodo": clim_cmems.texto_metodo(primera),
            "origen_climatologia": clim_cmems.texto_origen(primera, periodo_referencia, variable),
            "nota_sistema": clim_cmems.NOTA_SISTEMA,
            "credito": clim_cmems.CREDITO,
            "producto_climatologia": PRODUCTO_GLORYS, "doi_climatologia": DOI_GLORYS,
            "fuente_id": self.id, "identificador": self.identificador, "licencia": self.licencia,
            "fecha_corte": self._corte().isoformat(), "resolucion": self.resolucion,
        }
        if z is not None:
            attrs["profundidad_m"] = z
        return xr.Dataset(variables_datos, coords=coords, attrs=attrs)

    # ------------------------------------------------------------ auxiliares
    def _variable_valida(self, variable_id: str) -> VariableFuente:
        var = self.variable(variable_id)
        if var is None:
            disponibles = ", ".join(v.id for v in self.variables)
            raise ValueError(f"La fuente {self.nombre} no publica la variable «{variable_id}». "
                             f"Disponibles: {disponibles}.")
        return var

    @staticmethod
    def _validar_nivel(z: float) -> None:
        if not any(abs(z - n) <= 1e-6 for n in NIVELES_SOME):
            raise ValueError(f"Copernicus Marine se sirve en los niveles {', '.join(map(str, NIVELES_SOME[:-1]))} y "
                             f"{NIVELES_SOME[-1]} m; {cfg.fmt_num(z, 1)} m no es uno de ellos.")

    def _profundidades(self, var: VariableFuente, p: ParametrosConsulta) -> list[float] | None:
        if not var.tiene_profundidad:
            return None
        if p.profundidades:
            lista = [float(z) for z in p.profundidades]
        else:
            lista = [float(p.profundidad if p.profundidad is not None else 0.0)]
        for z in lista:
            self._validar_nivel(z)
        return lista

    def _atributos_variable(self, var: VariableFuente, real: bool) -> dict:
        atributos = {
            "standard_name": var.standard_name,
            "long_name": var.long_name,
            "units": var.unidades_cf,
            "unidad_mostrada": var.unidad,
            "coordinates": "time lat lon" + (" depth" if var.tiene_profundidad else ""),
            "comment": INSIGNIA_REALES if real else cfg.INSIGNIA_SIMULADOS,
        }
        if real:
            atributos["cell_methods"] = CELL_METHODS_REAL
        return atributos

    def _atributos_reales(self, var: VariableFuente, corte: date, componentes) -> dict:
        datasets = sorted({DATASET_POR_VARIABLE[c] for c in componentes if c in DATASET_POR_VARIABLE})
        version = estado_cmems.version_producto()
        paquete = estado_cmems.bloque_paquete().get("version")
        return {
            "Conventions": "CF-1.8",
            "title": f"{var.nombre} — {self.nombre}",
            "source": (f"{PRODUCTO_ANFC}: {', '.join(datasets)}" + (f" (versión {version})" if version else "")
                       + ", servicio ARCO de Copernicus Marine, leído con el Copernicus Marine Toolbox"
                       + (f" {paquete}" if paquete else "")),
            "institution": self.proveedor,
            "references": f"https://doi.org/{DOI_ANFC}",
            "history": (f"{datetime.now().isoformat(timespec='seconds')} SOME {cfg.VERSION}: leído de la caché local "
                        "datos/copernicus (llenada con copernicusmarine.open_dataset) y llevado a la malla SOME de "
                        "0.25°"),
            "comment": INSIGNIA_REALES, "insignia": INSIGNIA_REALES, "datos_reales": 1, "procedencia": "real",
            "credito": CREDITO_DATOS, "identificador": self.identificador, "licencia": self.licencia,
            "fuente_id": self.id, "fecha_corte": corte.isoformat(), "resolucion": self.resolucion,
            "producto": PRODUCTO_ANFC, "doi": DOI_ANFC,
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
        ds.attrs["aviso"] = (f"Copernicus Marine no está disponible: "
                             f"{estado_cmems.inicial_minuscula(motivo.rstrip('.')) or 'sin acceso'}. Esta consulta usa "
                             "datos SIMULADOS para demostración.")
        return ds
