"""Contrato de los conectores de fuentes del SOME.

Define los tipos de parámetros (`ParametrosConsulta`, `VariableFuente`,
`EstadoFuente`) y la clase abstracta `ConectorFuente`, que es el mismo
contrato que cumplen los conectores reales (CHIRPS v3, Copernicus Marine y
NOAA OISST) y su respaldo simulado: cada consulta devuelve un `xarray.Dataset`
con dimensiones (time, [depth], lat, lon) y metadatos CF.

Cada conector declara su procedencia con `datos_reales` (bool) e `insignia`
(texto corto que acompaña a toda salida); en Copernicus Marine y NOAA OISST
ambos son dinámicos (real si hay caché o acceso; si no, simulado). Cada Dataset declara
además la suya con el atributo `datos_reales` (1/0): un conector real puede
devolver un Dataset del simulador como respaldo, y eso debe verse.

- `procedencia_de(conectores)`: procedencia a nivel de catálogo (solo conectores).
- `procedencia_de_datos(conectores, ds_lista)`: procedencia de un resultado mirando
  los Dataset que realmente lo formaron: «real», «simulado» o «mixto», con su
  insignia, el indicador `datos_simulados` y los avisos de los Dataset.
- `combinar_procedencias(...)`: combina procedencias ya calculadas (reportes).

También incluye `ConectorSimulado`, una implementación común que obtiene los
campos de `datos_simulados.py` (con caché) y arma el Dataset con los
atributos CF. Para pasar una fuente a datos reales solo se reemplaza esa
implementación interna; el motor y la API no cambian.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd
import xarray as xr

from .. import cache
from .. import configuracion as cfg
from .. import datos_simulados as sim


# ---------------------------------------------------------------------------
# Tipos de parámetros
# ---------------------------------------------------------------------------
@dataclass
class ParametrosConsulta:
    """Parámetros de una consulta a un conector."""
    variable: str                                    # p. ej. "sst", "thetao", "precip"
    bbox: tuple[float, float, float, float]          # (lon_min, lat_min, lon_max, lat_max)
    fecha_inicio: date
    fecha_fin: date                                  # inclusivo
    profundidad: float | None = None                 # m; None = superficie o variable 2D
    profundidades: list[float] | None = None         # para perfiles y cortinas


@dataclass
class VariableFuente:
    """Descripción de una variable publicada por una fuente."""
    id: str
    nombre: str
    unidad: str                       # unidad para mostrar («°C», «PSU», «mm/día»)
    descripcion: str
    paleta: str                       # paleta matplotlib para valores
    paleta_anomalia: str              # paleta para anomalías (PuOr_r o BrBG)
    standard_name: str                # nombre estándar CF
    long_name: str                    # nombre largo CF en español
    unidades_cf: str                  # unidades CF («degC», «1e-3», «m s-1», «mm day-1»)
    tiene_profundidad: bool = False
    es_vector: bool = False
    componentes: list[str] | None = None   # para vectores: ["uo", "vo"]
    decimales: int = 1                     # decimales sugeridos al mostrar
    habilitada: bool = True

    def a_dict(self) -> dict:
        return {
            "id": self.id,
            "nombre": self.nombre,
            "unidad": self.unidad,
            "descripcion": self.descripcion,
            "paleta": self.paleta,
            "paleta_anomalia": self.paleta_anomalia,
            "tiene_profundidad": self.tiene_profundidad,
            "es_vector": self.es_vector,
            "componentes": self.componentes,
            "decimales": self.decimales,
            "habilitada": self.habilitada,
        }


@dataclass
class EstadoFuente:
    """Estado operativo de una fuente (RF-21)."""
    fuente: str
    nombre: str
    estado: str                       # "operativa" | "degradada" | "sin_conexion"
    fecha_corte: str                  # ISO aaaa-mm-dd
    latencia_ms: int
    ultimo_chequeo: str               # ISO aaaa-mm-ddThh:mm:ss
    mensaje: str = ""

    def a_dict(self) -> dict:
        return {
            "fuente": self.fuente,
            "nombre": self.nombre,
            "estado": self.estado,
            "fecha_corte": self.fecha_corte,
            "latencia_ms": self.latencia_ms,
            "ultimo_chequeo": self.ultimo_chequeo,
            "mensaje": self.mensaje,
        }


# ---------------------------------------------------------------------------
# Clase abstracta
# ---------------------------------------------------------------------------
class ConectorFuente(ABC):
    """Contrato común de los conectores de fuentes externas."""

    id: str
    nombre: str
    nombre_corto: str = ""            # nombre breve para etiquetas («NOAA OISST»)
    proveedor: str
    producto: str
    identificador: str
    credito: str
    licencia: str
    referencias: str
    resolucion: str
    cobertura_temporal: str
    variables: list[VariableFuente]
    habilitada: bool = True
    # Procedencia: False = simulada por el prototipo; True = descargada de la fuente real
    datos_reales: bool = False
    insignia: str = cfg.INSIGNIA_SIMULADOS

    @abstractmethod
    def obtener(self, p: ParametrosConsulta) -> xr.Dataset:
        """Datos de la variable en el área y periodo pedidos.

        Dimensiones (time, [depth], lat, lon); atributos CF: title, source,
        institution, references, history y comment = `self.insignia`
        («Datos simulados para demostración» o la insignia de datos reales).
        """

    @abstractmethod
    def climatologia(self, variable: str, periodo_referencia: str, bbox,
                     profundidad: float | None = None) -> xr.Dataset:
        """Climatología mensual (dim `month` 1..12) de la variable en el área."""

    @abstractmethod
    def estado(self) -> EstadoFuente:
        """Estado operativo, fecha de corte, latencia, último chequeo y mensaje."""

    # Utilidades comunes -----------------------------------------------------
    def variable(self, variable_id: str) -> VariableFuente | None:
        for v in self.variables:
            if v.id == variable_id:
                return v
        return None

    @property
    def fecha_corte(self) -> date:
        return date.fromisoformat(self.estado().fecha_corte)

    def a_dict(self) -> dict:
        """Representación para `GET /api/catalogo`."""
        return {
            "id": self.id,
            "nombre": self.nombre,
            "nombre_corto": self.nombre_corto or self.nombre,
            "proveedor": self.proveedor,
            "producto": self.producto,
            "identificador": self.identificador,
            "credito": self.credito,
            "licencia": self.licencia,
            "referencias": self.referencias,
            "resolucion": self.resolucion,
            "cobertura_temporal": self.cobertura_temporal,
            "habilitada": self.habilitada,
            "datos_reales": bool(self.datos_reales),
            "insignia": self.insignia,
            "variables": [v.a_dict() for v in self.variables],
            "estado": self.estado().a_dict(),
        }


# ---------------------------------------------------------------------------
# Procedencia de un resultado (real / simulado / mixto)
# ---------------------------------------------------------------------------
PROCEDENCIAS = ("real", "simulado", "mixto")


def _resumen_procedencia(procedencia: str, insignia_real: str | None = None) -> dict:
    """{"datos_simulados", "procedencia", "insignia"}. Un resultado «real» lleva la insignia de su única
    fuente real (`insignia_real`) o, si no se indica (varias fuentes reales), la genérica «Datos reales»."""
    insignias = {"real": insignia_real or getattr(cfg, "INSIGNIA_REALES", "Datos reales"),
                 "simulado": cfg.INSIGNIA_SIMULADOS,
                 "mixto": cfg.INSIGNIA_MIXTOS}
    return {"datos_simulados": procedencia != "real", "procedencia": procedencia,
            "insignia": insignias[procedencia]}


def _insignia_unica(insignias) -> str | None:
    """La insignia si todas las partes reales comparten una sola; None si hay varias (o ninguna)."""
    distintas = {i for i in insignias if i}
    return distintas.pop() if len(distintas) == 1 else None


def procedencia_de(conectores) -> dict:
    """{"datos_simulados", "procedencia", "insignia"} de un resultado según sus conectores (nivel catálogo).

    - todos reales → «real» (insignia del conector real si solo hay uno) y datos_simulados False;
    - todos simulados (o ninguno) → «simulado»;
    - una mezcla → «mixto».
    """
    lista = [c for c in (conectores or []) if c is not None]
    if not lista:
        return _resumen_procedencia("simulado")
    reales = [c for c in lista if getattr(c, "datos_reales", False)]
    if len(reales) == len(lista):
        return _resumen_procedencia("real", _insignia_unica(getattr(c, "insignia", "") for c in reales))
    return _resumen_procedencia("mixto" if reales else "simulado")


def dataset_es_real(ds, conector=None) -> bool:
    """True si el Dataset declara `datos_reales == 1`; False si declara 0. Sin el atributo, lo que diga su
    conector (o False si no se conoce: nunca se etiqueta como real algo de procedencia desconocida)."""
    atributos = getattr(ds, "attrs", None) or {}
    valor = atributos.get("datos_reales")
    if valor is not None:
        try:
            return int(valor) == 1
        except (TypeError, ValueError):
            return str(valor).strip().lower() in ("true", "si", "sí")
    return bool(conector is not None and getattr(conector, "datos_reales", False))


def procedencia_de_datos(conectores, ds_lista=()) -> dict:
    """{"datos_simulados", "procedencia", "insignia", "avisos": list[str]} mirando los Dataset.

    Cada Dataset no nulo cuenta como real si attrs["datos_reales"] == 1 y como simulado si == 0; si no
    trae el atributo, cuenta lo que diga su conector (por attrs["fuente_id"]; con un solo conector en la
    lista, ese). Un conector de la lista sin ningún Dataset cuenta con su atributo datos_reales. Todo
    real → «real» (insignia del Dataset o del conector si es una sola; si hay varias, cfg.INSIGNIA_REALES);
    todo simulado → «simulado»; si no → «mixto». «avisos»: los attrs["aviso"] distintos, en orden.
    """
    lista = [c for c in (conectores or []) if c is not None]
    por_id = {getattr(c, "id", None): c for c in lista}
    partes: list[tuple[bool, str]] = []          # (real, insignia de la parte real)
    avisos: list[str] = []
    fuentes_con_datos: set = set()
    for ds in ds_lista or ():
        if ds is None:
            continue
        atributos = getattr(ds, "attrs", None) or {}
        fuente_id = atributos.get("fuente_id")
        conector = por_id.get(fuente_id) if fuente_id in por_id else (lista[0] if len(lista) == 1 else None)
        if conector is not None:
            fuentes_con_datos.add(getattr(conector, "id", None))
        real = dataset_es_real(ds, conector)
        insignia = ""
        if real:
            # La insignia del Dataset solo si es de datos reales (una heredada del simulador no vale)
            insignia = str(atributos.get("insignia") or "")
            if not insignia.startswith("Datos reales"):
                insignia = str(getattr(conector, "insignia", "") or "")
        partes.append((real, insignia))
        aviso = str(atributos.get("aviso") or "").strip()
        if aviso and aviso not in avisos:
            avisos.append(aviso)
    for c in lista:
        if getattr(c, "id", None) not in fuentes_con_datos:
            real = bool(getattr(c, "datos_reales", False))
            partes.append((real, str(getattr(c, "insignia", "") or "") if real else ""))
    if not partes:
        r = _resumen_procedencia("simulado")
    elif all(real for real, _ in partes):
        r = _resumen_procedencia("real", _insignia_unica(i for _, i in partes))
    elif any(real for real, _ in partes):
        r = _resumen_procedencia("mixto")
    else:
        r = _resumen_procedencia("simulado")
    r["avisos"] = avisos
    return r


def combinar_procedencias(*resultados) -> dict:
    """Combina procedencias ya calculadas (dicts con «procedencia» o cadenas «real»/«simulado»/«mixto»).

    Los elementos None se ignoran; sin elementos el resultado es «simulado». Si todo es real, la insignia
    es la de la única fuente real o, si hay varias, la genérica «Datos reales».
    """
    valores, insignias_reales = set(), []
    for r in resultados:
        if r is None:
            continue
        if isinstance(r, dict):
            p = r.get("procedencia") or ("simulado" if r.get("datos_simulados", True) else "real")
            if p == "real":
                insignias_reales.append(r.get("insignia") or "")
        else:
            p = str(r)
        valores.add(p if p in PROCEDENCIAS else "simulado")
    if not valores:
        return _resumen_procedencia("simulado")
    if valores == {"real"}:
        return _resumen_procedencia("real", _insignia_unica(insignias_reales))
    if valores == {"simulado"}:
        return _resumen_procedencia("simulado")
    return _resumen_procedencia("mixto")


# ---------------------------------------------------------------------------
# Implementación simulada común
# ---------------------------------------------------------------------------
_ATRIBUTOS_LAT = {"standard_name": "latitude", "long_name": "Latitud",
                  "units": "degrees_north", "axis": "Y"}
_ATRIBUTOS_LON = {"standard_name": "longitude", "long_name": "Longitud",
                  "units": "degrees_east", "axis": "X"}
_ATRIBUTOS_DEPTH = {"standard_name": "depth", "long_name": "Profundidad",
                    "units": "m", "positive": "down", "axis": "Z"}
_ATRIBUTOS_TIME = {"standard_name": "time", "long_name": "Tiempo", "axis": "T"}
_ATRIBUTOS_MONTH = {"long_name": "Mes del año", "units": "1"}


class ConectorSimulado(ConectorFuente):
    """Conector que genera los datos con `datos_simulados.py` respetando el contrato."""

    # Estado simulado (lo fijan las subclases)
    estado_simulado: str = "operativa"
    fecha_corte_simulada: date = cfg.FECHA_CORTE
    latencia_simulada_ms: int = 800
    mensaje_estado: str = ""

    def __init__(self):
        self.habilitada = True

    # --- contrato ------------------------------------------------------------
    def obtener(self, p: ParametrosConsulta) -> xr.Dataset:
        var = self._variable_valida(p.variable)
        inicio = max(p.fecha_inicio, cfg.FECHA_INICIO)
        fin = min(p.fecha_fin, self.fecha_corte_simulada)
        if fin < inicio:
            raise ValueError(
                f"El periodo solicitado ({cfg.fmt_fecha(p.fecha_inicio)} – {cfg.fmt_fecha(p.fecha_fin)}) "
                f"está fuera de la cobertura de {self.nombre} "
                f"({cfg.fmt_fecha(cfg.FECHA_INICIO)} – {cfg.fmt_fecha(self.fecha_corte_simulada)}).")
        fechas = pd.date_range(inicio, fin, freq="D")
        fechas_date = [f.date() for f in fechas]

        ilat, ilon = sim.indices_bbox(p.bbox)
        if ilat.size == 0 or ilon.size == 0:
            raise ValueError("El área solicitada no contiene ninguna celda de la malla de 0.25°.")

        profundidades: list[float] | None = None
        if var.tiene_profundidad:
            if p.profundidades:
                profundidades = [float(z) for z in p.profundidades]
            else:
                profundidades = [float(p.profundidad if p.profundidad is not None else 0.0)]
            for z in profundidades:
                if z < 0 or z > max(cfg.PROFUNDIDADES):
                    raise ValueError(f"La profundidad {cfg.fmt_num(z, 0)} m está fuera del rango "
                                     f"disponible (0 – {max(cfg.PROFUNDIDADES):.0f} m).")

        componentes = var.componentes or [var.id]
        variables_datos = {}
        for comp in componentes:
            comp_var = self.variable(comp) or var
            if profundidades is not None:
                capas = []
                for z in profundidades:
                    campos = cache.campos_en_cache(
                        self.id, comp, fechas_date, z,
                        lambda faltantes, z=z, comp=comp: sim.generar_campos(comp, faltantes, z))
                    capas.append(campos[:, ilat[:, None], ilon[None, :]])
                datos = np.stack(capas, axis=1)              # (time, depth, lat, lon)
                dims = ("time", "depth", "lat", "lon")
            else:
                campos = cache.campos_en_cache(
                    self.id, comp, fechas_date, None,
                    lambda faltantes, comp=comp: sim.generar_campos(comp, faltantes, None))
                datos = campos[:, ilat[:, None], ilon[None, :]]   # (time, lat, lon)
                dims = ("time", "lat", "lon")
            variables_datos[comp] = (dims, datos.astype(np.float32), self._atributos_variable(comp_var))

        coords = {
            "time": ("time", fechas.values.astype("datetime64[ns]"), _ATRIBUTOS_TIME),
            "lat": ("lat", cfg.LAT[ilat], _ATRIBUTOS_LAT),
            "lon": ("lon", cfg.LON[ilon], _ATRIBUTOS_LON),
        }
        if profundidades is not None:
            coords["depth"] = ("depth", np.array(profundidades, dtype=float), _ATRIBUTOS_DEPTH)

        ds = xr.Dataset(variables_datos, coords=coords, attrs=self._atributos_globales(var))
        return ds

    def climatologia(self, variable: str, periodo_referencia: str, bbox,
                     profundidad: float | None = None) -> xr.Dataset:
        var = self._variable_valida(variable)
        if periodo_referencia not in cfg.PERIODOS_REFERENCIA:
            raise ValueError(f"Periodo de referencia desconocido: «{periodo_referencia}». "
                             f"Disponibles: {', '.join(cfg.PERIODOS_REFERENCIA)}.")
        ilat, ilon = sim.indices_bbox(bbox)
        if ilat.size == 0 or ilon.size == 0:
            raise ValueError("El área solicitada no contiene ninguna celda de la malla de 0.25°.")
        z = float(profundidad or 0.0) if var.tiene_profundidad else None

        componentes = var.componentes or [var.id]
        variables_datos = {}
        for comp in componentes:
            comp_var = self.variable(comp) or var
            clim = sim.climatologia_mensual(comp, z)[:, ilat[:, None], ilon[None, :]]
            atributos = self._atributos_variable(comp_var)
            atributos["cell_methods"] = "time: mean within months"
            variables_datos[comp] = (("month", "lat", "lon"), clim.astype(np.float32), atributos)

        coords = {
            "month": ("month", np.arange(1, 13), _ATRIBUTOS_MONTH),
            "lat": ("lat", cfg.LAT[ilat], _ATRIBUTOS_LAT),
            "lon": ("lon", cfg.LON[ilon], _ATRIBUTOS_LON),
        }
        atributos_globales = self._atributos_globales(var)
        atributos_globales["title"] = f"Climatología mensual {periodo_referencia} — {var.nombre} ({self.nombre})"
        atributos_globales["periodo_referencia"] = periodo_referencia
        atributos_globales["metodo"] = (
            f"Climatología mensual {periodo_referencia}: promedio de la climatología analítica "
            f"simulada sobre los días de cada mes (sin eventos interanuales ni ruido).")
        if z is not None:
            atributos_globales["profundidad_m"] = z
        return xr.Dataset(variables_datos, coords=coords, attrs=atributos_globales)

    def estado(self) -> EstadoFuente:
        return EstadoFuente(
            fuente=self.id,
            nombre=self.nombre,
            estado=self.estado_simulado,
            fecha_corte=self.fecha_corte_simulada.isoformat(),
            latencia_ms=self.latencia_simulada_ms,
            ultimo_chequeo=datetime.now().isoformat(timespec="seconds"),
            mensaje=self.mensaje_estado,
        )

    # --- auxiliares ----------------------------------------------------------
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
            "coordinates": "time lat lon" + (" depth" if var.tiene_profundidad else ""),
            "comment": cfg.INSIGNIA_SIMULADOS,
        }

    def _atributos_globales(self, var: VariableFuente) -> dict:
        return {
            "Conventions": "CF-1.8",
            "title": f"{var.nombre} — {self.nombre} (simulado)",
            "source": f"{self.producto} (simulación analítica del prototipo SOME)",
            "institution": self.proveedor,
            "references": self.referencias,
            "history": (f"{datetime.now().isoformat(timespec='seconds')} "
                        f"SOME {cfg.VERSION}: generado por backend/datos_simulados.py"),
            "comment": cfg.INSIGNIA_SIMULADOS,
            # Procedencia del Dataset (la lee procedencia_de_datos): siempre simulado
            "datos_reales": 0,
            "procedencia": "simulado",
            "insignia": cfg.INSIGNIA_SIMULADOS,
            "credito": self.credito,
            "identificador": self.identificador,
            "licencia": self.licencia,
            "fuente_id": self.id,
            "fecha_corte": self.fecha_corte_simulada.isoformat(),
            "resolucion": self.resolucion,
        }
