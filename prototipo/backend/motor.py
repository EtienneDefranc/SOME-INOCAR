"""Motor de procesamiento del SOME: funciones puras sobre `xarray.Dataset`.

Recorte, promedios, series, climatología mensual, anomalías, perfiles,
cortinas, «mismo mes cada año», comparación, vectores y resúmenes en lenguaje
sencillo (sección 6 de la especificación). Además, las funciones
`consulta_*` arman las respuestas completas de `/api/consultas/*`
(sección 7.3): resuelven el área (bbox, recorte del catálogo o punto),
validan parámetros, documentan el `metodo` y la `fecha_corte` de la fuente y
devuelven diccionarios listos para serializar en JSON.

Cada respuesta con datos declara su procedencia (`datos_simulados`,
`procedencia` = «real» | «simulado» | «mixto» e `insignia`) a partir de los
Dataset que realmente la formaron (`procedencia_de_datos`): NOAA OISST v2.1,
Copernicus Marine y CHIRPS v3 son reales (una fuente real puede responder con su
simulador si no hay acceso ni caché: ese Dataset declara `datos_reales=0` y un
`aviso`; nunca se mezclan días simulados y reales en un mismo Dataset); una
comparación que mezcla datos reales y simulados es mixta. El índice Niño 1+2
(`serie_indice_enso`) es el real semanal que calcula el conector de NOAA OISST en
la región oficial. Las respuestas añaden `avisos` (si algún Dataset trae uno) y
`periodo_referencia_efectivo` (si la climatología usada declara otro periodo que
el pedido, p. ej. la oficial de Copernicus Marine, 1993-2016, o la de NOAA OISST
1981-2010, que en enero–agosto empieza en 1982).

Los errores de validación se lanzan como `ErrorConsulta(detalle, codigo)`
para que la API responda `{ "detalle": "..." }` con el código adecuado.

Al final hay un autodiagnóstico: `python -m backend.motor`.
"""

from __future__ import annotations

import calendar
import inspect
import math
import time
import warnings
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import xarray as xr

from . import catalogo
from . import configuracion as cfg
from . import datos_simulados as sim
from . import graficos
from .conectores import (ConectorFuente, ParametrosConsulta, VariableFuente,  # noqa: F401 (reexportadas)
                         combinar_procedencias, procedencia_de)
from .conectores.base import dataset_es_real, procedencia_de_datos

_TOL = 1e-6
MIN_FRACCION_DIAS_CELDA = 0.8     # mapa de un periodo: días con dato exigidos a cada celda (como la climatología)
MIN_DIAS_TENDENCIA = 730          # una tendencia «por año» exige al menos ~2 años de datos
AGREGACIONES = {"diaria": None, "semanal": "7D", "mensual": "MS"}
NOMBRE_AGREGACION = {"diaria": "diaria", "semanal": "semanal (promedio de 7 días)",
                     "mensual": "mensual (promedio del mes)"}
NOTA_SIMULACION = "Datos simulados para la demostración del prototipo SOME."


class ErrorConsulta(Exception):
    """Error de validación o de datos; `codigo` es el HTTP sugerido (400/404/422)."""

    def __init__(self, detalle: str, codigo: int = 422):
        super().__init__(detalle)
        self.detalle = detalle
        self.codigo = codigo


# ---------------------------------------------------------------------------
# Conversión y validación de parámetros
# ---------------------------------------------------------------------------
def _a_fecha(valor, nombre: str = "fecha") -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, np.datetime64):
        return pd.Timestamp(valor).date()
    try:
        return date.fromisoformat(str(valor)[:10])
    except (TypeError, ValueError):
        raise ErrorConsulta(f"El campo «{nombre}» debe ser una fecha ISO (aaaa-mm-dd); "
                            f"se recibió «{valor}».", 400) from None


def _periodo(periodo, nombre: str = "periodo") -> tuple[date, date]:
    if not isinstance(periodo, dict) or "inicio" not in periodo or "fin" not in periodo:
        raise ErrorConsulta(f"El campo «{nombre}» debe tener la forma "
                            "{\"inicio\": \"aaaa-mm-dd\", \"fin\": \"aaaa-mm-dd\"}.", 400)
    inicio = _a_fecha(periodo["inicio"], f"{nombre}.inicio")
    fin = _a_fecha(periodo["fin"], f"{nombre}.fin")
    if fin < inicio:
        raise ErrorConsulta(f"La fecha de fin ({cfg.fmt_fecha(fin)}) es anterior a la de inicio "
                            f"({cfg.fmt_fecha(inicio)}).", 400)
    return inicio, fin


def _flotante(x) -> float | None:
    """float o None (para NaN/inf), apto para JSON."""
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    return xf if math.isfinite(xf) else None


def _json_lista(arr) -> list:
    """Convierte un arreglo numpy (1D o 2D) en listas con None donde no hay dato."""
    arr = np.asarray(arr, dtype=float)
    if arr.ndim == 1:
        return [None if not math.isfinite(v) else round(float(v), 4) for v in arr]
    return [_json_lista(fila) for fila in arr]


def _redondear(x, decimales: int = 3) -> float | None:
    v = _flotante(x)
    return None if v is None else round(v, decimales)


def _nanmean(arreglo: np.ndarray, axis=None) -> np.ndarray:
    """nanmean sin el aviso «Mean of empty slice» en las celdas sin dato."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(arreglo, axis=axis)


def resolver_area(area) -> dict:
    """Normaliza el objeto `area` de la API a {tipo, bbox, nombre, descripcion, [lat, lon]}.

    - {"tipo": "bbox", "bbox": [lon_min, lat_min, lon_max, lat_max]} (se recorta al área de estudio)
    - {"tipo": "recorte", "recorte": "nino12"}
    - {"tipo": "punto", "lat": -2.0, "lon": -81.0}  → celda más cercana
    """
    if area is None:
        r = catalogo.recorte("area_estudio")
        return {"tipo": "recorte", "recorte": r["id"], "bbox": list(r["bbox"]),
                "nombre": r["nombre"], "descripcion": r["descripcion"]}
    if not isinstance(area, dict) or "tipo" not in area:
        raise ErrorConsulta("El campo «area» debe ser un objeto con «tipo» bbox, recorte o punto.", 400)
    tipo = area["tipo"]
    lon_a, lat_a, lon_b, lat_b = cfg.BBOX_AREA

    if tipo == "recorte":
        r = catalogo.recorte(str(area.get("recorte", "")))
        if r is None:
            raise ErrorConsulta(f"Recorte desconocido: «{area.get('recorte')}».", 404)
        return {"tipo": "recorte", "recorte": r["id"], "bbox": list(r["bbox"]),
                "nombre": r["nombre"], "descripcion": r["descripcion"], "tipo_recorte": r["tipo"]}

    if tipo == "bbox":
        bbox = area.get("bbox")
        try:
            lon_min, lat_min, lon_max, lat_max = [float(v) for v in bbox]
        except (TypeError, ValueError):
            raise ErrorConsulta("El «bbox» debe tener cuatro números: [lon_min, lat_min, lon_max, lat_max].",
                                400) from None
        if lon_min > lon_max:
            lon_min, lon_max = lon_max, lon_min
        if lat_min > lat_max:
            lat_min, lat_max = lat_max, lat_min
        lon_min, lon_max = max(lon_min, lon_a), min(lon_max, lon_b)
        lat_min, lat_max = max(lat_min, lat_a), min(lat_max, lat_b)
        if lon_min > lon_max or lat_min > lat_max:
            raise ErrorConsulta("El rectángulo está fuera del área de estudio "
                                "(6° N a 6° S, 95° O a 78° O).", 422)
        nombre = (f"Rectángulo {cfg.fmt_coordenada(lat_min, lon_min, 2)} – "
                  f"{cfg.fmt_coordenada(lat_max, lon_max, 2)}")
        return {"tipo": "bbox", "bbox": [lon_min, lat_min, lon_max, lat_max], "nombre": nombre,
                "descripcion": "Rectángulo definido por el usuario."}

    if tipo == "punto":
        try:
            lat = float(area["lat"])
            lon = float(area["lon"])
        except (KeyError, TypeError, ValueError):
            raise ErrorConsulta("El punto debe tener «lat» y «lon» numéricos.", 400) from None
        if not (lat_a - _TOL <= lat <= lat_b + _TOL and lon_a - _TOL <= lon <= lon_b + _TOL):
            raise ErrorConsulta(f"El punto {cfg.fmt_coordenada(lat, lon)} está fuera del área de "
                                "estudio (6° N a 6° S, 95° O a 78° O).", 422)
        lat_c, lon_c = sim.celda_mas_cercana(lat, lon)
        return {"tipo": "punto", "lat": lat_c, "lon": lon_c, "lat_pedida": lat, "lon_pedida": lon,
                "bbox": [lon_c, lat_c, lon_c, lat_c],
                "nombre": f"Punto {cfg.fmt_coordenada(lat_c, lon_c)}",
                "descripcion": "Celda de 0.25° más cercana al punto indicado."}

    raise ErrorConsulta(f"Tipo de área desconocido: «{tipo}». Use bbox, recorte o punto.", 400)


def _fuente_y_variable(fuente_id, variable_id) -> tuple[ConectorFuente, VariableFuente]:
    if not fuente_id or not catalogo.existe_fuente(str(fuente_id)):
        raise ErrorConsulta(f"Fuente desconocida: «{fuente_id}». "
                            f"Disponibles: {', '.join(catalogo.CONECTORES)}.", 404)
    conector = catalogo.conector(str(fuente_id))
    if not conector.habilitada:
        raise ErrorConsulta(f"La fuente {conector.nombre} está deshabilitada en el catálogo.", 422)
    var = conector.variable(str(variable_id)) if variable_id else None
    if var is None:
        raise ErrorConsulta(f"La fuente {conector.nombre} no publica la variable «{variable_id}». "
                            f"Disponibles: {', '.join(v.id for v in conector.variables)}.", 404)
    if not var.habilitada:
        raise ErrorConsulta(f"La variable «{var.nombre}» está deshabilitada en el catálogo.", 422)
    return conector, var


_CLAVES_CHIRPS = ("dias_origen_chc", "dias_origen_replica", "credito_replica")


def _ds_de(conector: ConectorFuente, ds_lista) -> list[xr.Dataset]:
    """Los Dataset no nulos de `ds_lista` que pertenecen al conector (por `fuente_id`; sin él, se asumen suyos)."""
    return [ds for ds in (ds_lista or []) if ds is not None
            and str(ds.attrs.get("fuente_id") or conector.id) == conector.id]


def _ds_real(ds: xr.Dataset | None, conector: ConectorFuente | None = None) -> bool:
    """True si el Dataset es de datos reales (atributo `datos_reales`; sin él, según su conector)."""
    return ds is not None and dataset_es_real(ds, conector)


def _es_dataset_chirps(conector: ConectorFuente, ds_lista) -> bool:
    return conector.id == "chirps_v3" or any(any(k in ds.attrs for k in _CLAVES_CHIRPS) for ds in ds_lista)


def _texto_dias(n: int) -> str:
    return f"{cfg.fmt_num(n, 0)} {'día' if n == 1 else 'días'}"


def _clave_tiempo(ds: xr.Dataset):
    """Identifica el tramo de días de un Dataset diario (para no contar dos veces los mismos días de dos
    variables de una misma consulta, p. ej. thetao y mlotst del perfil)."""
    if "time" not in ds.coords or ds.sizes.get("time", 0) == 0:
        return None
    t = ds["time"].values
    return (str(t[0])[:10], str(t[-1])[:10], int(t.size))


def _es_dataset_noaa(atributos) -> bool:
    """True si los atributos son de un Dataset de NOAA OISST real (cuenta días finales y preliminares por día;
    CHIRPS también declara `dias_finales`, así que se mira la fuente o el origen por servidor de NOAA)."""
    return atributos.get("fuente_id") == "noaa_oisst" or "dias_origen_psl" in atributos


def origen_datos_generico(ds_lista) -> dict:
    """Origen declarado por los Dataset reales de las fuentes que no son CHIRPS (Copernicus Marine y NOAA OISST).

    Devuelve {"origen_datos", "dias_nrt", "dias_definitivos", "dias_finales", "dias_preliminares_noaa",
    "origen_climatologia", "periodo_referencia_efectivo", "nota_sistema"} (solo las claves con valor). Los
    Dataset diarios con conteos se suman por fuente y por tramo de días distinto (un mismo tramo cuenta una vez:
    p. ej. thetao y mlotst del perfil): Copernicus Marine (`dias_nrt`, `dias_definitivos`) y NOAA OISST
    (`dias_finales`, `dias_preliminares`, `dias_origen_ncei`). Con un solo Dataset de una fuente se respeta el
    texto de su conector; con varios (p. ej., «Mismo mes cada año», cuatro octubres) el texto se rehace con la
    misma redacción. Con varias fuentes (p. ej., la comparación NOAA OISST frente a Copernicus Marine), sus
    textos se unen con «; » en el orden en que aparecen.
    """
    r: dict = {}
    grupos: list[str] = []              # «copernicus», «noaa» o el texto de un Dataset sin conteos, en orden
    textos_cop: list[str] = []
    textos_noaa: list[str] = []
    tramos_cop: dict = {}
    tramos_noaa: dict = {}
    for ds in ds_lista or []:
        a = ds.attrs
        if a.get("origen_datos"):
            texto = str(a["origen_datos"])
            clave = _clave_tiempo(ds) or id(ds)
            if _es_dataset_noaa(a) and ("dias_finales" in a or "dias_preliminares" in a):
                grupo = "noaa"
                if texto not in textos_noaa:
                    textos_noaa.append(texto)
                try:
                    tramos_noaa.setdefault(clave, (int(a.get("dias_finales") or 0),
                                                   int(a.get("dias_preliminares") or 0),
                                                   int(a.get("dias_origen_ncei") or 0)))
                except (TypeError, ValueError):
                    pass
            elif "dias_nrt" in a or "dias_definitivos" in a:
                grupo = "copernicus"
                if texto not in textos_cop:
                    textos_cop.append(texto)
                try:
                    tramos_cop.setdefault(clave, (int(a.get("dias_definitivos") or 0), int(a.get("dias_nrt") or 0)))
                except (TypeError, ValueError):
                    pass
            else:
                grupo = texto
            if grupo not in grupos:
                grupos.append(grupo)
        for clave in ("origen_climatologia", "nota_sistema"):
            if a.get(clave) and not r.get(clave):
                r[clave] = str(a[clave])
        if a.get("periodo_efectivo") and not r.get("periodo_referencia_efectivo"):
            r["periodo_referencia_efectivo"] = str(a["periodo_efectivo"])
    if tramos_cop:
        r["dias_definitivos"] = sum(v[0] for v in tramos_cop.values())
        r["dias_nrt"] = sum(v[1] for v in tramos_cop.values())
    if tramos_noaa:
        r["dias_finales"] = sum(v[0] for v in tramos_noaa.values())
        r["dias_preliminares_noaa"] = sum(v[1] for v in tramos_noaa.values())
    partes: list[str] = []
    for grupo in grupos:
        if grupo == "copernicus":
            if len(textos_cop) == 1 and len(tramos_cop) <= 1:
                partes.append(textos_cop[0])
            elif tramos_cop:
                # Varios tramos de días (p. ej., «Mismo mes cada año»): se suman aunque sus textos coincidan («30
                # días de mejor análisis» en cada año), con la misma redacción que el conector, sin partes en cero
                from .conectores.copernicus import texto_origen  # import local para evitar ciclos
                partes.append(texto_origen(r["dias_definitivos"], r["dias_nrt"]))
            else:
                partes.extend(textos_cop)
        elif grupo == "noaa":
            if len(textos_noaa) == 1 and len(tramos_noaa) <= 1:
                partes.append(textos_noaa[0])
            elif tramos_noaa:
                # Igual que Copernicus: «31 días finales» de cada octubre se suman («124 días finales»)
                from .noaa.conector import texto_origen as texto_origen_noaa  # import local para evitar ciclos
                partes.append(texto_origen_noaa(r["dias_finales"], r["dias_preliminares_noaa"],
                                                sum(v[2] for v in tramos_noaa.values())))
            else:
                partes.extend(textos_noaa)
        else:
            partes.append(grupo)
    if partes:
        r["origen_datos"] = "; ".join(partes)
    return r


def _credito_combinado(ds_lista, conector: ConectorFuente | None = None) -> dict:
    """Créditos distintos de los Dataset (datos y climatología), unidos con un espacio.

    Si algún Dataset es real, se omiten los créditos de los simulados: no son datos del proveedor, y la insignia
    «mixto» y el aviso ya lo señalan. Los Dataset diarios de NOAA OISST dan un solo crédito con la suma de sus
    días por servidor («vía NOAA PSL», «vía NOAA NCEI» o los dos), en lugar de uno por Dataset.
    Devuelve {"credito"} y, con NOAA, {"dias_origen_psl", "dias_origen_ncei", "creditos_climatologia"}, que
    reportes.registrar_fuente usa para rehacer el crédito de un documento con varias consultas.
    """
    lista = [ds for ds in ds_lista or [] if ds is not None]
    if any(_ds_real(ds, conector) for ds in lista):
        lista = [ds for ds in lista if _ds_real(ds, conector)]
    creditos: list[str] = []
    r: dict = {}
    diarios_noaa = [ds for ds in lista if "dias_origen_psl" in ds.attrs or "dias_origen_ncei" in ds.attrs]
    if diarios_noaa:
        from .noaa.constantes import credito_para_origen  # import local para evitar ciclos

        def dias(ds, clave: str) -> int:
            try:
                return int(ds.attrs.get(clave) or 0)
            except (TypeError, ValueError):
                return 0
        r["dias_origen_psl"] = sum(dias(ds, "dias_origen_psl") for ds in diarios_noaa)
        r["dias_origen_ncei"] = sum(dias(ds, "dias_origen_ncei") for ds in diarios_noaa)
        creditos.append(credito_para_origen(r["dias_origen_psl"], r["dias_origen_ncei"]))
        r["creditos_climatologia"] = []
    for ds in lista:
        texto = str(ds.attrs.get("credito") or "").strip()
        if any(ds is d for d in diarios_noaa) or not texto or texto in creditos:
            continue
        creditos.append(texto)
        if diarios_noaa:
            r["creditos_climatologia"].append(_con_punto(texto))
    r["credito"] = " ".join(_con_punto(c) for c in creditos)
    return r


def _info_fuente(conector: ConectorFuente, ds_lista=()) -> dict:
    """Datos de la fuente para el crédito de la respuesta.

    `datos_reales`, `procedencia` e `insignia` salen de los Dataset de la consulta (`ds_lista`: datos
    diarios y, si se usó, la climatología) y no solo del conector: un conector real puede haber
    respondido con su simulador de respaldo. Con datos reales:
    - CHIRPS: el crédito es el de ESA consulta (cita la réplica de dynamical.org solo si alguno de
      esos Dataset la usó) y se añade el origen de los días y de la climatología;
    - otras fuentes (Copernicus Marine, NOAA OISST): el crédito une los créditos distintos de los Dataset
      (datos y climatología: GLORYS12 en Copernicus, NOAA PSL en NOAA) y se copian `origen_datos`,
      `origen_climatologia` y el periodo de referencia efectivo.
    Si algún Dataset trae un `aviso` (respaldo simulado o datos parciales), se copia en `aviso`.
    """
    estado = conector.estado()
    propios = _ds_de(conector, ds_lista)
    proc = procedencia_de_datos([conector], propios)
    real = proc["procedencia"] != "simulado"          # «real» o «mixto»: hay datos reales de la fuente
    info = {"id": conector.id, "nombre": conector.nombre,
            "nombre_corto": conector.nombre_corto or conector.nombre,
            "identificador": conector.identificador,
            "credito": conector.credito, "licencia": conector.licencia,
            "fecha_corte": estado.fecha_corte, "estado": estado.estado,
            "datos_reales": real, "procedencia": proc["procedencia"],
            "referencias": getattr(conector, "referencias", "") if real else "",
            "insignia": proc["insignia"]}
    if propios and _es_dataset_chirps(conector, propios):
        if real:
            origen = origen_real(propios)
            if origen.get("credito"):
                info["credito"] = origen["credito"]
            for clave in ("origen_datos", "origen_climatologia", "usa_replica", "credito_replica",
                          "dias_origen_chc", "dias_origen_replica", "climatologia_replica"):
                if origen.get(clave) not in (None, ""):
                    info[clave] = origen[clave]
    elif propios:
        combinado = _credito_combinado(propios, conector)
        if combinado["credito"]:
            info["credito"] = combinado["credito"]
        for clave in ("dias_origen_psl", "dias_origen_ncei", "creditos_climatologia"):
            if clave in combinado:
                info[clave] = combinado[clave]
        # La licencia de Copernicus Marine (cláusula 2.4) exige nombrar al originador y citar los DOI en toda
        # salida; para obras derivadas, como las del SOME, recomienda «Generated using E.U. Copernicus Marine
        # Service Information; [DOI]». El crédito de la climatología GLORYS12 sola no lo trae (con datos del
        # análisis, sí): se antepone sin repetirlo.
        actual = str(info.get("credito") or "")
        if conector.id == "copernicus_phy" and real and "Copernicus Marine Service Information" not in actual:
            originador = "Generated using E.U. Copernicus Marine Service Information"
            info["credito"] = f"{originador}; {actual[:1].lower()}{actual[1:]}" if actual else f"{originador}."
        reales = [ds for ds in propios if _ds_real(ds, conector)]
        origen = origen_datos_generico(reales)
        for clave in ("origen_datos", "origen_climatologia", "periodo_referencia_efectivo", "nota_sistema"):
            if origen.get(clave):
                info[clave] = origen[clave]
    if proc["avisos"]:
        info["aviso"] = " ".join(_con_punto(a) for a in proc["avisos"])
    return info


def _texto_origen_dias(n_chc: int, n_replica: int) -> str:
    """Misma redacción que `conectores.chirps.texto_origen` (para sumar varios Dataset)."""
    partes = []
    if n_chc:
        partes.append(f"servidor del CHC: {cfg.fmt_num(n_chc, 0)} {'día' if n_chc == 1 else 'días'}")
    if n_replica:
        partes.append(f"réplica dynamical.org: {cfg.fmt_num(n_replica, 0)} {'día' if n_replica == 1 else 'días'}")
    return "; ".join(partes)


def origen_real(ds_lista) -> dict:
    """Origen declarado por los Dataset de una fuente real (atributos del conector CHIRPS).

    Devuelve {"usa_replica", "credito", "credito_replica", "origen_datos",
    "dias_origen_chc", "dias_origen_replica", "climatologia_replica", "origen_climatologia"} (solo las claves
    con valor).
    Suma los días por servidor de varios Dataset diarios (p. ej., «mismo mes cada año»).
    """
    n_chc = n_rep = 0
    hay_dias = False
    clim_replica = False
    credito = credito_con_replica = credito_replica = clim = ""
    for ds in ds_lista or []:
        if ds is None:
            continue
        a = ds.attrs
        es_diario = "dias_origen_chc" in a or "dias_origen_replica" in a
        if es_diario:
            hay_dias = True
            try:
                n_chc += int(a.get("dias_origen_chc") or 0)
                n_rep += int(a.get("dias_origen_replica") or 0)
            except (TypeError, ValueError):
                pass
        elif a.get("origen_climatologia") and a.get("credito_replica"):
            clim_replica = True
        if a.get("credito_replica"):
            credito_replica = credito_replica or str(a["credito_replica"])
            credito_con_replica = credito_con_replica or str(a.get("credito") or "")
        elif a.get("credito"):
            credito = credito or str(a["credito"])
        if a.get("origen_climatologia"):
            clim = clim or str(a["origen_climatologia"])
    r: dict = {"usa_replica": bool(credito_replica)}
    if clim_replica:
        r["climatologia_replica"] = True
    if credito_con_replica or credito:
        r["credito"] = credito_con_replica or credito
    if hay_dias:
        # El crédito se recalcula con la suma de TODOS los Dataset (no se copia el del primero que citaba
        # la réplica): «vía la réplica» solo si todos los días vienen de ella; «parte» si se mezclan.
        from .conectores.chirps import credito_para_origen
        r["credito"], r["usa_replica"] = credito_para_origen(n_chc, n_rep, clim_replica)
    if credito_replica and r["usa_replica"]:
        r["credito_replica"] = credito_replica
    if hay_dias:
        r["origen_datos"] = _texto_origen_dias(n_chc, n_rep) or "sin días con dato"
        r["dias_origen_chc"] = n_chc
        r["dias_origen_replica"] = n_rep
    if clim:
        r["origen_climatologia"] = clim
    return r


def nota_climatologia(conector: ConectorFuente, ds_clim: xr.Dataset | None) -> str:
    """Frase con el origen de la climatología de una fuente real («Climatología 1991-2020
    calculada con la réplica dynamical.org…», «Climatología oficial de Copernicus Marine (reanálisis
    GLORYS12, periodo 1993-2016). Se pidió 1991-2020…»); para la climatología simulada de respaldo de
    una fuente real, « Climatología simulada: {aviso}»; vacía para las fuentes simuladas."""
    if ds_clim is None:
        return ""
    if not _ds_real(ds_clim, conector):
        aviso = str(ds_clim.attrs.get("aviso") or "").strip()
        if not aviso:
            return ""
        return f" {_con_punto(aviso)}" if aviso.lower().startswith("climatología simulada") else \
            f" Climatología simulada: {_con_punto(aviso)}"
    texto = str(ds_clim.attrs.get("origen_climatologia") or "").strip()
    if not texto:
        return ""
    return f" {_con_punto(texto)}"


def _periodo_efectivo(ds_clim: xr.Dataset | None) -> str | None:
    """Periodo de la climatología realmente usada si el conector lo declara (`periodo_efectivo`)."""
    if ds_clim is None:
        return None
    valor = str(ds_clim.attrs.get("periodo_efectivo") or "").strip()
    return valor or None


def texto_periodo_referencia(periodo_referencia: str | None, efectivo: str | None = None) -> str:
    """«1991-2020» o, si la climatología usada es de otro periodo, «1993-2016 (se pidió 1991-2020)».

    Si el periodo efectivo solo difiere en algunos meses (NOAA OISST empieza en septiembre de 1981: su
    1981-2010 usa 1982-2010 en enero–agosto), el pedido sigue siendo el periodo y los meses distintos van entre
    paréntesis: «1981-2010 (1982-2010 en enero–agosto)»; «1982-2010 en enero–agosto (se pidió 1981-2010)» haría
    pensar que octubre también usa 1982-2010."""
    if efectivo and periodo_referencia and efectivo != periodo_referencia:
        if " en " in efectivo:
            return f"{periodo_referencia} ({efectivo})"
        return f"{efectivo} (se pidió {periodo_referencia})"
    return str(efectivo or periodo_referencia or "")


def _referencia_en_parentesis(texto: str | None) -> str:
    """Para ir dentro de un paréntesis: «1993-2016 (se pidió 1991-2020)» → «1993-2016; se pidió 1991-2020» y
    «1981-2010 (1982-2010 en enero–agosto)» → «1981-2010; 1982-2010 en enero–agosto» (evita
    «(periodo de referencia 1993-2016 (se pidió 1991-2020))»)."""
    t = str(texto or "")
    if " (" in t and t.endswith(")"):
        return t[:-1].replace(" (", "; ", 1)
    return t


def _es_real(conector: ConectorFuente | None) -> bool:
    return bool(conector is not None and getattr(conector, "datos_reales", False))


def _con_punto(texto) -> str:
    texto = str(texto or "").strip()
    # Un paréntesis final no cierra la oración: «… (CC BY 4.0)» + «Recorte…» necesita el punto
    return texto if not texto or texto.endswith((".", "!", "?", "»")) else texto + "."


def _dias_meta(ds_lista) -> tuple[int, int]:
    """(días preliminares, días faltantes) declarados por los Dataset de conectores reales."""
    prelim = falt = 0
    for ds in ds_lista or []:
        if ds is None:
            continue
        try:
            prelim += int(ds.attrs.get("dias_preliminares") or 0)
            falt += int(ds.attrs.get("dias_faltantes") or 0)
        except (TypeError, ValueError):
            continue
    return prelim, falt


def periodo_preliminar(ds_lista) -> str | None:
    """«aaaa-mm-dd/aaaa-mm-dd»: del primer al último día preliminar que declaran los Dataset (`periodo_preliminar`
    de NOAA OISST y CHIRPS v3) de UNA misma fuente; None si ninguno lo declara o si son de fuentes distintas (un
    tramo común de dos fuentes no diría nada cierto de ninguna)."""
    tramos = [(str(ds.attrs.get("fuente_id") or ""), str(ds.attrs["periodo_preliminar"]))
              for ds in ds_lista or [] if ds is not None and ds.attrs.get("periodo_preliminar")]
    if not tramos or len({f for f, _t in tramos}) > 1:
        return None
    pares = [t.split("/", 1) for _f, t in tramos if "/" in t]
    if not pares:
        return None
    return f"{min(a[:10] for a, _b in pares)}/{max(b[:10] for _a, b in pares)}"


def _avisos(ds_lista) -> list[str]:
    """Avisos distintos (`aviso`) declarados por los Dataset, en orden."""
    salida: list[str] = []
    for ds in ds_lista or []:
        if ds is None:
            continue
        aviso = str(ds.attrs.get("aviso") or "").strip()
        if aviso and aviso not in salida:
            salida.append(aviso)
    return salida


def nota_datos(conector: ConectorFuente, ds: xr.Dataset | None = None, prelim: int | None = None,
               faltantes: int | None = None, ds_lista=None) -> str:
    """Frase final del `metodo`: aviso de simulación o, para datos reales, su procesamiento
    (remuestreo a la malla del prototipo) y los días preliminares o faltantes.

    La procedencia se decide con el Dataset (`ds`) o, si no se da, con `ds_lista` (p. ej. «mismo mes»,
    varios Dataset); solo sin ninguno se usa el conector. Un Dataset del simulador de respaldo de una
    fuente real lleva la nota de simulación y su aviso (por qué se usó el simulador)."""
    lista = [ds] if ds is not None else [d for d in (ds_lista or []) if d is not None]
    lista = _ds_de(conector, lista) or lista
    reales = [d for d in lista if _ds_real(d, conector)] if lista else []
    avisos = _avisos(lista)
    if (lista and not reales) or (not lista and not _es_real(conector)):
        return " ".join([NOTA_SIMULACION] + [_con_punto(a) for a in avisos])
    # El procesamiento de un Dataset termina con el origen de SUS días: con varios (p. ej. «mismo mes»),
    # se usa la frase general para no presentar los días de uno como los de todos.
    atributos = reales[0].attrs if len(reales) == 1 else {}
    nombre = conector.nombre_corto or conector.nombre
    partes = [_con_punto(atributos.get("procesamiento")) or
              f"Datos reales de {nombre} llevados a la malla de 0.25° del prototipo."]
    p, f = _dias_meta(reales)
    prelim = p if prelim is None else prelim
    faltantes = f if faltantes is None else faltantes
    if prelim:
        # Cada fuente nombra a su proveedor: NOAA OISST trae su propia nota (`nota_preliminares`) y el tramo de
        # días preliminares (`periodo_preliminar`); sin ella, la de CHIRPS (Climate Hazards Center).
        nota_prelim = next((str(d.attrs["nota_preliminares"]) for d in reales if d.attrs.get("nota_preliminares")), "")
        if nota_prelim:
            periodo_prelim = periodo_preliminar(reales)
            tramo = ""
            if periodo_prelim:
                desde, hasta = periodo_prelim.split("/", 1)
                tramo = (f" ({cfg.fmt_fecha(desde)})" if desde == hasta
                         else f" (del {cfg.fmt_fecha(desde)} al {cfg.fmt_fecha(hasta)})")
            frase = f"Incluye {prelim} día{'s' if prelim != 1 else ''} con datos preliminares de {nombre}{tramo}."
            # Con un solo Dataset, su procesamiento ya explica los preliminares: la nota no se repite
            if "preliminar" not in str(atributos.get("procesamiento") or ""):
                frase += f" {_con_punto(nota_prelim)}"
            partes.append(frase)
        else:
            partes.append(f"Incluye {prelim} día{'s' if prelim != 1 else ''} con datos preliminares de {nombre}, "
                          "que el Climate Hazards Center reemplazará por la versión final.")
    if faltantes:
        partes.append(f"{faltantes} día{'s' if faltantes != 1 else ''} del periodo sin dato (no publicado por la "
                      "fuente o aún no obtenido); no se rellenan.")
    partes += [_con_punto(a) for a in avisos]
    return " ".join(partes)


def procedencia_resultado(conectores, ds_lista=()) -> dict:
    """Campos de procedencia de una respuesta, mirando los Dataset que la formaron: datos_simulados,
    procedencia e insignia; `avisos` si algún Dataset trae uno; `periodo_referencia_efectivo` si la
    climatología usada lo declara; y, si interviene una fuente real, los días preliminares y faltantes
    (con `periodo_preliminar`, «aaaa-mm-dd/aaaa-mm-dd», si los preliminares son de una sola fuente) y el
    origen de los datos."""
    datasets = [ds for ds in (ds_lista or []) if ds is not None]
    r = procedencia_de_datos(conectores, datasets)
    avisos = r.pop("avisos", [])
    if avisos:
        r["avisos"] = avisos
    efectivo = next((p for p in (_periodo_efectivo(ds) for ds in datasets) if p), None)
    if efectivo:
        r["periodo_referencia_efectivo"] = efectivo
    reales = [ds for ds in datasets if dataset_es_real(ds, _conector_de(ds, conectores))]
    if reales or (not datasets and any(_es_real(c) for c in conectores or [])):
        prelim, falt = _dias_meta(reales)
        r["dias_preliminares"] = prelim
        r["dias_faltantes"] = falt
        tramo_prelim = periodo_preliminar(reales) if prelim else None
        if tramo_prelim:
            r["periodo_preliminar"] = tramo_prelim       # «aaaa-mm-dd/aaaa-mm-dd» (la interfaz lo muestra)
        chirps = [ds for ds in reales if any(k in ds.attrs for k in _CLAVES_CHIRPS)
                  or ds.attrs.get("fuente_id") == "chirps_v3"]
        ids_chirps = {id(ds) for ds in chirps}       # (comparar Dataset con «in» evaluaría ds == otro)
        origen = origen_real(chirps)
        r["usa_replica"] = origen["usa_replica"]
        otros = origen_datos_generico([ds for ds in reales if id(ds) not in ids_chirps])
        for clave in ("origen_datos", "origen_climatologia"):
            textos = [t for t in (origen.get(clave), otros.get(clave)) if t]
            if textos:
                r[clave] = "; ".join(textos) if clave == "origen_datos" else " ".join(_con_punto(t) for t in textos)
    return r


def _conector_de(ds: xr.Dataset, conectores) -> ConectorFuente | None:
    """El conector de la lista al que pertenece el Dataset (por `fuente_id`; con uno solo, ese)."""
    lista = [c for c in (conectores or []) if c is not None]
    fuente_id = ds.attrs.get("fuente_id")
    for c in lista:
        if c.id == fuente_id:
            return c
    return lista[0] if len(lista) == 1 else None


def _fecha_corte(conector: ConectorFuente) -> str:
    return conector.estado().fecha_corte


def fecha_corte_sistema() -> date:
    """Fecha de corte del sistema: `catalogo.fecha_corte_sistema()` (con NOAA OISST real, el último día de su
    caché, que siempre tiene dato; si no, la simulada). Si el catálogo no la ofreciera, la simulada de la
    configuración (`cfg.FECHA_CORTE`), para que los valores por defecto nunca fallen."""
    funcion = getattr(catalogo, "fecha_corte_sistema", None)
    return funcion() if callable(funcion) else cfg.FECHA_CORTE


def _corte_conocido(conector: ConectorFuente) -> date | None:
    """Fecha de corte de la fuente, o None si una fuente real aún no tiene ningún dato servible (caché
    vacía y nada verificado: su fecha de corte queda antes del inicio de la demo). En ese caso no se
    rechaza la consulta: el conector intenta el llenado bajo demanda o explica que se está llenando."""
    corte = conector.fecha_corte
    if _es_real(conector) and corte < cfg.FECHA_INICIO:
        return None
    return corte


def _ajustar_fecha(fecha: date, conector: ConectorFuente) -> tuple[date, str]:
    """Acota una fecha a la cobertura de la fuente; devuelve (fecha, nota)."""
    corte = _corte_conocido(conector) or date.today()
    if fecha > corte:
        return corte, (f" La fecha pedida ({cfg.fmt_fecha(fecha)}) es posterior a la fecha de corte "
                       f"de la fuente; se usó la última disponible ({cfg.fmt_fecha(corte)}).")
    if fecha < cfg.FECHA_INICIO:
        return cfg.FECHA_INICIO, (f" La fecha pedida ({cfg.fmt_fecha(fecha)}) es anterior al inicio de "
                                  f"la demo; se usó {cfg.fmt_fecha(cfg.FECHA_INICIO)}.")
    return fecha, ""


def _acotar_periodo(inicio: date, fin: date, conector: ConectorFuente) -> tuple[date, date, str]:
    """Acota un periodo a la cobertura de la fuente; devuelve (inicio, fin, nota).

    Si todo el periodo cae después de la fecha de corte de la fuente (o antes del
    inicio de la demo) lanza un ErrorConsulta con un mensaje claro, en lugar de
    dejar que el conector reciba fin < inicio.
    """
    corte = _corte_conocido(conector)
    if corte is None:                     # fuente real sin datos todavía: que el conector lo intente
        corte = min(max(fin, cfg.FECHA_INICIO), date.today())
    if inicio > corte:
        raise ErrorConsulta(
            f"El periodo pedido ({cfg.fmt_fecha(inicio)} – {cfg.fmt_fecha(fin)}) es posterior a la fecha de "
            f"corte de {conector.nombre_corto or conector.nombre} ({cfg.fmt_fecha(corte)}); elija una fecha de "
            f"inicio igual o anterior a esa fecha.", 422)
    if fin < cfg.FECHA_INICIO:
        raise ErrorConsulta(
            f"El periodo pedido ({cfg.fmt_fecha(inicio)} – {cfg.fmt_fecha(fin)}) es anterior al inicio de los "
            f"datos de la demo ({cfg.fmt_fecha(cfg.FECHA_INICIO)}); elija una fecha de fin igual o posterior.", 422)
    nota = ""
    inicio_aj, fin_aj = inicio, fin
    if inicio < cfg.FECHA_INICIO:
        inicio_aj = cfg.FECHA_INICIO
        nota += f" El periodo se acotó al inicio de la demo ({cfg.fmt_fecha(cfg.FECHA_INICIO)})."
    if fin > corte:
        fin_aj = corte
        nota += f" El periodo se acotó a la fecha de corte de la fuente ({cfg.fmt_fecha(corte)})."
    return inicio_aj, fin_aj, nota


def _profundidad(valor, var: VariableFuente) -> float | None:
    if not var.tiene_profundidad:
        return None
    if valor is None:
        return 0.0
    try:
        z = float(valor)
    except (TypeError, ValueError):
        raise ErrorConsulta(f"La profundidad debe ser numérica; se recibió «{valor}».", 400) from None
    if z < 0 or z > max(cfg.PROFUNDIDADES):
        raise ErrorConsulta(f"La profundidad {cfg.fmt_num(z, 0)} m está fuera del rango 0 – "
                            f"{max(cfg.PROFUNDIDADES):.0f} m.", 422)
    return z


def _texto_profundidad(z: float | None) -> str:
    if z is None:
        return ""
    return " en superficie" if z == 0 else f" a {cfg.fmt_num(z, 0)} m de profundidad"


def _nombre_para_frases(var: VariableFuente) -> str:
    """Nombre de la variable en minúsculas, apto para «la …»."""
    if var.es_vector:
        return "rapidez de la corriente"
    nombre = var.nombre
    if "(" in nombre:
        nombre = nombre.split("(")[0].strip()
    return nombre[0].lower() + nombre[1:]


def _articulo(nombre: str) -> str:
    primera = nombre.split()[0].lower() if nombre else ""
    femeninas = ("temperatura", "salinidad", "velocidad", "rapidez", "altura", "profundidad",
                 "precipitación", "anomalía", "corriente")
    return "la" if primera in femeninas or primera.endswith("a") or primera.endswith("ad") else "el"


def _es_error_de_fuente(e: BaseException) -> bool:
    """True si la excepción viene del acceso a una fuente real (paquetes `backend.copernicus`,
    `backend.chirps` o `backend.noaa`): aunque el conector esté en ese momento en modo simulado, es un 503
    explicado y no un error interno del servidor."""
    return type(e).__module__.startswith(("backend.copernicus", "backend.chirps", "backend.noaa"))


def obtener_dataset(fuente_id: str, variable_id: str, bbox, inicio: date, fin: date,
                    profundidad: float | None = None, profundidades: list[float] | None = None,
                    nombre_area: str = "") -> tuple[xr.Dataset, ConectorFuente, VariableFuente]:
    """Consulta el conector y valida que el área tenga celdas con dato para la variable."""
    conector, var = _fuente_y_variable(fuente_id, variable_id)
    p = ParametrosConsulta(variable=var.id, bbox=tuple(float(v) for v in bbox),
                           fecha_inicio=inicio, fecha_fin=fin,
                           profundidad=profundidad, profundidades=profundidades)
    try:
        ds = conector.obtener(p)
    except ValueError as e:
        raise ErrorConsulta(str(e), 422) from None
    except Exception as e:  # noqa: BLE001 - una fuente real puede fallar por red o por archivos
        if not (_es_real(conector) or _es_error_de_fuente(e)):
            raise
        raise ErrorConsulta(f"No fue posible obtener datos de {conector.nombre_corto or conector.nombre}: {e}",
                            503) from None

    if var.es_vector and var.componentes:
        u, v = var.componentes
        rapidez = np.hypot(ds[u], ds[v])
        rapidez.attrs = {"standard_name": var.standard_name, "long_name": var.long_name,
                         "units": var.unidades_cf, "unidad_mostrada": var.unidad,
                         "comment": ds.attrs.get("comment") or conector.insignia}
        ds[var.id] = rapidez

    if not np.isfinite(ds[var.id].values).any():
        area = f"«{nombre_area}»" if nombre_area else "indicada"
        lon_min, lat_min, lon_max, lat_max = [float(x) for x in bbox]
        es_punto = abs(lon_max - lon_min) < _TOL and abs(lat_max - lat_min) < _TOL
        n_dias = int(ds.sizes.get("time", 0))
        sin_descarga = (_ds_real(ds, conector) and n_dias > 0
                        and int(ds.attrs.get("dias_faltantes") or 0) >= n_dias)
        if sin_descarga:
            detalle = (f"{conector.nombre_corto or conector.nombre} no tiene datos disponibles para el periodo "
                       f"{cfg.fmt_fecha(inicio)} – {cfg.fmt_fecha(fin)}: no están en la caché local y no se "
                       "pudieron descargar. Revise el estado de la fuente o elija otro periodo.")
        elif conector.id == "chirps_v3" and es_punto:
            detalle = (f"CHIRPS solo tiene datos sobre tierra: el punto {cfg.fmt_coordenada(lat_min, lon_min)} "
                       "cae en una celda de 0.25° sin dato (mar). Elija un punto en el continente o en "
                       "Galápagos.")
        elif conector.id == "chirps_v3":
            detalle = (f"El área {area} no contiene celdas de tierra: CHIRPS solo tiene datos sobre tierra "
                       "(continente y Galápagos). Elija una provincia, una cuenca o un rectángulo que "
                       "incluya tierra.")
        elif es_punto:
            # Perfiles, cortinas y series en un punto: el consejo de las áreas (recorte o rectángulo) no aplica.
            detalle = (f"El punto {cfg.fmt_coordenada(lat_min, lon_min)} cae en una celda de 0.25° sin dato de mar "
                       f"en {conector.nombre_corto or conector.nombre} (costa o tierra). Elija un punto más mar "
                       "adentro.")
        else:
            detalle = (f"El área {area} no contiene celdas marinas válidas para «{var.nombre}». "
                       "Elija un recorte marino (área, región Niño) o un rectángulo sobre el mar.")
        raise ErrorConsulta(detalle, 422)
    return ds, conector, var


# ---------------------------------------------------------------------------
# Funciones puras
# ---------------------------------------------------------------------------
def recortar(ds: xr.Dataset, bbox) -> xr.Dataset:
    """Recorta por bbox (lon_min, lat_min, lon_max, lat_max), inclusivo en los centros de celda."""
    lon_min, lat_min, lon_max, lat_max = [float(v) for v in bbox]
    return ds.sel(lat=slice(lat_min - _TOL, lat_max + _TOL), lon=slice(lon_min - _TOL, lon_max + _TOL))


def promedio_area(objeto):
    """Promedio espacial simple (ignora NaN) sobre lat y lon."""
    dims = [d for d in ("lat", "lon") if d in objeto.dims]
    return objeto.mean(dim=dims, skipna=True) if dims else objeto


def promedio_tiempo(objeto):
    """Promedio temporal (ignora NaN) sobre time."""
    return objeto.mean(dim="time", skipna=True) if "time" in objeto.dims else objeto


def _sin_profundidad(da: xr.DataArray) -> xr.DataArray:
    if "depth" in da.dims:
        return da.isel(depth=0) if da.sizes["depth"] == 1 else da.mean(dim="depth", skipna=True)
    return da


def serie(ds: xr.Dataset, variable: str, agregacion: str = "diaria") -> pd.Series:
    """Serie temporal del promedio de área: diaria, semanal (7 días) o mensual."""
    if agregacion not in AGREGACIONES:
        raise ErrorConsulta(f"Agregación desconocida: «{agregacion}». Use diaria, semanal o mensual.", 400)
    da = _sin_profundidad(ds[variable])
    s = promedio_area(da).to_series().astype(float)
    s.index = pd.DatetimeIndex(s.index)
    s.name = variable
    regla = AGREGACIONES[agregacion]
    if regla:
        s = s.resample(regla).mean()
    return s


def _periodos_agregados(s_diaria: pd.Series, agregacion: str) -> list[dict]:
    """Para cada punto de la serie agregada, las fechas reales que cubre: [{inicio, fin, dias, parcial}].

    Con agregación diaria cada punto cubre un día. Con semanal o mensual, pandas
    etiqueta el contenedor con su inicio nominal; aquí se informa el primer y el
    último día con dato y si el contenedor quedó incompleto (p. ej., un mes
    con solo 8 días por el periodo pedido o la fecha de corte).
    """
    regla = AGREGACIONES.get(agregacion)
    indice = pd.DatetimeIndex(s_diaria.index)
    if not regla:
        return [{"inicio": cfg.iso_fecha(t), "fin": cfg.iso_fecha(t), "dias": 1, "parcial": False} for t in indice]
    salida = []
    for etiqueta, grupo in s_diaria.resample(regla):
        if grupo.empty:
            continue
        g = pd.DatetimeIndex(grupo.index)
        primero, ultimo = g.min(), g.max()
        if agregacion == "mensual":
            esperados = calendar.monthrange(etiqueta.year, etiqueta.month)[1]
        else:
            esperados = 7
        salida.append({"inicio": cfg.iso_fecha(primero), "fin": cfg.iso_fecha(ultimo), "dias": int(len(g)),
                       "parcial": bool(len(g) < esperados)})
    return salida


_ACEPTA_SIMULADA: dict[type, bool] = {}


def _acepta_simulada(conector: ConectorFuente) -> bool:
    """True si `conector.climatologia` acepta el argumento `simulada` (conectores con respaldo simulado)."""
    tipo = type(conector)
    if tipo not in _ACEPTA_SIMULADA:
        try:
            _ACEPTA_SIMULADA[tipo] = "simulada" in inspect.signature(conector.climatologia).parameters
        except (TypeError, ValueError):
            _ACEPTA_SIMULADA[tipo] = False
    return _ACEPTA_SIMULADA[tipo]


def _climatologia_ds(conector: ConectorFuente, variable: str, periodo_referencia: str, bbox,
                     profundidad: float | None = None, ds_datos=None,
                     forzar_simulada: bool = False) -> xr.Dataset:
    """Climatología del conector con sus errores convertidos en ErrorConsulta.

    `ds_datos` (Dataset o lista) son los datos de la misma consulta: si TODOS salieron del simulador
    de respaldo de una fuente real (`datos_reales == 0`), la climatología se pide también simulada
    (`simulada=True`), para que la anomalía sea coherente (simulado menos climatología simulada).
    `forzar_simulada` la pide simulada sin mirar datos: la usa quien ya sabe que los datos que la
    acompañan son simulados (p. ej., el informe por variable cuando su serie salió del respaldo)."""
    datos = ds_datos if isinstance(ds_datos, (list, tuple)) else [ds_datos]
    datos = [d for d in datos if d is not None]
    simulada = forzar_simulada or (bool(datos) and not any(_ds_real(d, conector) for d in datos))
    try:
        if simulada and _acepta_simulada(conector):
            return conector.climatologia(variable, periodo_referencia, bbox, profundidad, simulada=True)
        return conector.climatologia(variable, periodo_referencia, bbox, profundidad)
    except ValueError as e:
        raise ErrorConsulta(str(e), 422) from None
    except Exception as e:  # noqa: BLE001 - una fuente real puede fallar por red o por archivos
        if not (_es_real(conector) or _es_error_de_fuente(e)):
            raise
        raise ErrorConsulta(f"No fue posible obtener la climatología de "
                            f"{conector.nombre_corto or conector.nombre}: {e}", 503) from None


def climatologia_mensual(conector: ConectorFuente, variable: str, bbox, periodo_referencia: str,
                         profundidad: float | None = None, ds_clim: xr.Dataset | None = None) -> np.ndarray:
    """Climatología mensual (12 valores, promedio de área) del conector (o de `ds_clim` si ya se leyó)."""
    if ds_clim is None:
        ds_clim = _climatologia_ds(conector, variable, periodo_referencia, bbox, profundidad)
    var = conector.variable(variable)
    if var is not None and var.es_vector and var.componentes:
        u, v = var.componentes
        campo = np.hypot(ds_clim[u], ds_clim[v])
    else:
        campo = ds_clim[variable]
    return np.asarray(promedio_area(campo).values, dtype=float)


def texto_metodo_anomalia(periodo_referencia: str, efectivo: str | None = None, por_celda: bool = False) -> str:
    """«Anomalía = valor − climatología mensual 1991-2020; …» o, si la climatología usada es de otro
    periodo, «Anomalía = valor − climatología mensual 1993-2016 (se pidió 1991-2020); …».

    `por_celda` (mapas): la anomalía se calcula celda por celda, no con el promedio de área de las series.
    El cierre no lleva ningún punto interno, para que la oración se pueda extraer entera hasta el primer
    punto (la leyenda del mapa ya no la copia: muestra solo el periodo de referencia)."""
    cierre = ("en cada celda se resta la climatología de esa misma celda y del mismo mes" if por_celda
              else "climatología calculada por promedio de área")
    return (f"Anomalía = valor − climatología mensual {texto_periodo_referencia(periodo_referencia, efectivo)}; "
            f"{cierre}.")


def anomalia(objeto, climatologia_mensual_12):
    """Resta a cada dato la climatología del mes correspondiente (Series, DataArray o Dataset)."""
    clim = np.asarray(climatologia_mensual_12, dtype=float)
    if isinstance(objeto, pd.Series):
        meses = pd.DatetimeIndex(objeto.index).month.values
        return pd.Series(objeto.values - clim[meses - 1], index=objeto.index, name="anomalia")
    meses = objeto["time"].dt.month.values
    clim_t = xr.DataArray(clim[meses - 1], dims=("time",), coords={"time": objeto["time"]})
    return objeto - clim_t


def tendencia_por_anio(s: pd.Series) -> float | None:
    """Pendiente lineal (unidades por año) si la serie cubre al menos ~2 años (`MIN_DIAS_TENDENCIA`).

    Con menos, extrapolar la pendiente de unas semanas a «por año» no tiene sentido físico (p. ej.
    la precipitación diaria de 90 días daba «+45.9 mm/día por año»): se devuelve None.
    """
    s = s.dropna()
    if len(s) < 3:
        return None
    dias = (s.index - s.index[0]).days.values.astype(float)
    if dias[-1] < MIN_DIAS_TENDENCIA:
        return None
    pendiente_por_dia = np.polyfit(dias, s.values, 1)[0]
    return float(pendiente_por_dia * 365.25)


def _etiqueta_periodo(fecha, agregacion: str, cobertura: dict | None = None) -> str:
    """«octubre de 2026», «octubre de 2026 (8 días)», «semana del 23/10/2026 (5 días)» o «27/10/2026»."""
    fecha = pd.Timestamp(fecha)
    if agregacion == "mensual":
        texto = cfg.fmt_mes_anio(fecha.to_pydatetime())
    elif agregacion == "semanal":
        texto = f"semana del {cfg.fmt_fecha(cobertura['inicio'] if cobertura else fecha)}"
    else:
        return cfg.fmt_fecha(fecha)
    if cobertura and cobertura.get("parcial"):
        dias = int(cobertura.get("dias", 0))
        texto += f" ({dias} día{'s' if dias != 1 else ''})"
    return texto


def _describir_fecha(fecha, agregacion: str, cobertura: dict | None = None) -> str:
    etiqueta = _etiqueta_periodo(fecha, agregacion, cobertura)
    if agregacion == "mensual":
        return f"en {etiqueta}"
    if agregacion == "semanal":
        return f"la {etiqueta}"
    return f"el {etiqueta}"


def resumen_lenguaje_sencillo(s: pd.Series, unidad: str, nombre_variable: str,
                              climatologia=None, anomalias=None, periodo_referencia: str | None = None,
                              agregacion: str = "diaria", decimales: int = 1,
                              inicio: date | None = None, fin: date | None = None,
                              periodos_agregados: list[dict] | None = None) -> dict:
    """Estadísticas de la serie y frases en español sencillo (RF-07).

    La tendencia se calcula sobre las anomalías cuando se entregan (así no la
    domina el ciclo estacional); si no, sobre los valores. `periodos_agregados`
    (ver `_periodos_agregados`) permite informar las fechas reales que cubre
    cada punto cuando la agregación es semanal o mensual: el «último dato» se
    fecha con el último día real y los contenedores parciales se señalan.
    """
    s = s.astype(float)
    validos = s.dropna()
    cobertura: dict[pd.Timestamp, dict] = {}
    if periodos_agregados and len(periodos_agregados) == len(s):
        cobertura = {pd.Timestamp(t): p for t, p in zip(s.index, periodos_agregados)}

    def fecha_real(indice, extremo: str):
        p = cobertura.get(pd.Timestamp(indice))
        return p[extremo] if p else cfg.iso_fecha(indice)
    nombre = nombre_variable[0].lower() + nombre_variable[1:] if nombre_variable else "la variable"
    if "(" in nombre:
        nombre = nombre.split("(")[0].strip()
    articulo = _articulo(nombre)
    if validos.empty:
        return {"media": None, "minimo": None, "maximo": None, "tendencia_por_anio": None,
                "ultimo": None, "n_datos": 0,
                "texto": [f"No hay datos de {articulo} {nombre} en el periodo consultado."]}

    media = float(validos.mean())
    i_min, i_max = validos.idxmin(), validos.idxmax()
    minimo = {"valor": round(float(validos[i_min]), 4), "fecha": fecha_real(i_min, "inicio")}
    maximo = {"valor": round(float(validos[i_max]), 4), "fecha": fecha_real(i_max, "inicio")}
    tendencia_sobre = "valores"
    if anomalias is not None and len(anomalias) == len(s):
        tendencia = tendencia_por_anio(pd.Series(np.asarray(anomalias, dtype=float), index=s.index))
        tendencia_sobre = "anomalías"
    else:
        tendencia = tendencia_por_anio(validos)
    ultimo_i = validos.index[-1]
    ultimo = {"valor": round(float(validos.iloc[-1]), 4), "fecha": fecha_real(ultimo_i, "fin"),
              "anomalia": None, "climatologia": None}
    if cobertura.get(pd.Timestamp(ultimo_i)):
        ultimo["periodo"] = cobertura[pd.Timestamp(ultimo_i)]
    if anomalias is not None and len(anomalias) == len(s):
        a = pd.Series(np.asarray(anomalias, dtype=float), index=s.index)
        ultimo["anomalia"] = _redondear(a.loc[ultimo_i], 4)
    if climatologia is not None and len(climatologia) == len(s):
        c = pd.Series(np.asarray(climatologia, dtype=float), index=s.index)
        ultimo["climatologia"] = _redondear(c.loc[ultimo_i], 4)

    texto: list[str] = []
    inicio = inicio or s.index[0]
    fin = fin or s.index[-1]
    # Con cobertura parcial (p. ej., caché incompleta sin acceso a la fuente), la frase dice cuántos datos
    # promedia: «promedió» a secas haría pasar uno o dos días por el periodo entero.
    n, total = int(len(validos)), int(len(s))
    if n < total:
        unidad_periodo = {"semanal": "semanas", "mensual": "meses"}.get(agregacion, "días")
        texto.append(f"Con datos de {cfg.fmt_num(n, 0)} de {cfg.fmt_num(total, 0)} {unidad_periodo} entre el "
                     f"{cfg.fmt_fecha(inicio)} y el {cfg.fmt_fecha(fin)}, {articulo} {nombre} "
                     f"promedió {cfg.fmt_valor(media, unidad, decimales)}.")
    else:
        texto.append(f"Entre el {cfg.fmt_fecha(inicio)} y el {cfg.fmt_fecha(fin)} {articulo} {nombre} "
                     f"promedió {cfg.fmt_valor(media, unidad, decimales)}.")
    if n > 1:                     # con un solo dato, «el más alto» y «el más bajo» serían el mismo valor
        texto.append(f"El valor más alto, {cfg.fmt_valor(maximo['valor'], unidad, decimales)}, se registró "
                     f"{_describir_fecha(i_max, agregacion, cobertura.get(pd.Timestamp(i_max)))}; el más bajo, "
                     f"{cfg.fmt_valor(minimo['valor'], unidad, decimales)}, "
                     f"{_describir_fecha(i_min, agregacion, cobertura.get(pd.Timestamp(i_min)))}.")
    # Dentro del paréntesis, el contenedor parcial se indica con coma: «(octubre de 2026, 8 días)»
    etiqueta_ultimo = (_etiqueta_periodo(ultimo_i, agregacion, cobertura.get(pd.Timestamp(ultimo_i)))
                       .replace(" (", ", ").rstrip(")"))
    frase_ultimo = (f"El último dato disponible ({etiqueta_ultimo}) es "
                    f"{cfg.fmt_valor(ultimo['valor'], unidad, decimales)}")
    if ultimo["anomalia"] is not None:
        mes = cfg.MESES_LARGOS[pd.Timestamp(ultimo_i).month - 1]
        referencia = (f" (periodo de referencia {_referencia_en_parentesis(periodo_referencia)})"
                      if periodo_referencia else "")
        umbral = 0.5 * 10 ** (-decimales)
        if abs(ultimo["anomalia"]) < umbral:
            frase_ultimo += f", un valor normal para {mes}{referencia}."
        else:
            sentido = "por encima" if ultimo["anomalia"] > 0 else "por debajo"
            frase_ultimo += (f", es decir {cfg.fmt_valor(abs(ultimo['anomalia']), unidad, decimales)} "
                             f"{sentido} de lo normal para {mes}{referencia}.")
    else:
        frase_ultimo += "."
    texto.append(frase_ultimo)
    if tendencia is not None:
        sujeto = "de las anomalías del periodo" if tendencia_sobre == "anomalías" else "del periodo"
        texto.append(f"La tendencia {sujeto} es de {cfg.fmt_anomalia(tendencia, unidad, decimales)} por año.")
    if anomalias is not None and len(anomalias) == len(s):
        a_validas = pd.Series(np.asarray(anomalias, dtype=float), index=s.index).dropna()
        if len(a_validas) >= 10:
            fraccion = float((a_validas > 0).mean())
            texto.append(f"El {cfg.fmt_num(fraccion * 100, 0)} % de los datos del periodo estuvo por "
                         "encima de la climatología.")

    return {
        "media": round(media, 4),
        "minimo": minimo,
        "maximo": maximo,
        "tendencia_por_anio": _redondear(tendencia, 4),
        "tendencia_sobre": tendencia_sobre,
        "ultimo": ultimo,
        "desviacion": _redondear(validos.std(ddof=0), 4),
        "n_datos": int(len(validos)),
        "texto": texto,
        # La frase del último dato por separado: su posición en `texto` cambia si se omite la de máximo y
        # mínimo (un solo dato); el indicador del panel debe tomarla de aquí y no por índice.
        "frase_ultimo": frase_ultimo,
    }


def perfil(ds: xr.Dataset, variable: str | None = None) -> dict:
    """Valores por profundidad en un punto y una fecha: {profundidades, valores}."""
    nombre = variable or next(v for v in ds.data_vars if "depth" in ds[v].dims)
    da = ds[nombre]
    if "time" in da.dims:
        da = da.isel(time=0) if da.sizes["time"] == 1 else promedio_tiempo(da)
    da = promedio_area(da)
    return {"profundidades": [float(z) for z in da["depth"].values],
            "valores": _json_lista(da.values)}


def cortina(ds: xr.Dataset, variable: str | None = None) -> dict:
    """Matriz profundidad × tiempo en un punto: {fechas, profundidades, valores[depth][time]}."""
    nombre = variable or next(v for v in ds.data_vars if "depth" in ds[v].dims)
    da = promedio_area(ds[nombre]).transpose("depth", "time")
    return {"fechas": [cfg.iso_fecha(t) for t in da["time"].values],
            "profundidades": [float(z) for z in da["depth"].values],
            "valores": _json_lista(da.values)}


def mismo_mes_cada_anio(conector: ConectorFuente, variable: str, bbox, mes: int, anios,
                        profundidad: float | None = None,
                        periodo_referencia: str = cfg.PERIODO_REFERENCIA_POR_DEFECTO,
                        nombre_area: str = "") -> dict:
    """Promedio del mes `mes` en cada año con datos, más la climatología de ese mes."""
    if not 1 <= int(mes) <= 12:
        raise ErrorConsulta("El mes debe estar entre 1 y 12.", 400)
    mes = int(mes)
    corte = _corte_conocido(conector) or date.today()
    anios_validos, valores, parciales, inicios, fines, con_dato = [], [], [], [], [], []
    datasets: list[xr.Dataset] = []
    for anio in anios:
        inicio = date(anio, mes, 1)
        fin = date(anio, mes, calendar.monthrange(anio, mes)[1])
        inicio_c, fin_c = max(inicio, cfg.FECHA_INICIO), min(fin, corte)
        if fin_c < inicio_c:
            continue
        ds, _, var = obtener_dataset(conector.id, variable, bbox, inicio_c, fin_c, profundidad,
                                     nombre_area=nombre_area)
        s = serie(ds, var.id, "diaria")
        datasets.append(ds)
        anios_validos.append(anio)
        valores.append(float(s.mean()))
        parciales.append((inicio_c, fin_c) != (inicio, fin))
        inicios.append(inicio_c)
        fines.append(fin_c)
        con_dato.append(int(s.notna().sum()))     # días con dato (caché incompleta sin acceso a la fuente)
    # Nunca se mezclan años simulados con reales: si una fuente real respondió con su simulador de respaldo
    # solo en algunos años (sin acceso y fuera de la caché local), esos años se omiten y se avisa. Si TODOS
    # son simulados, se conservan (la climatología también se pide simulada y la anomalía es coherente).
    reales = [i for i, d in enumerate(datasets) if _ds_real(d, conector)]
    omitidos: list[int] = []
    if reales and len(reales) < len(datasets):
        omitidos = [anios_validos[i] for i in range(len(datasets)) if i not in reales]
        anios_validos, valores, parciales, inicios, fines, con_dato, datasets = [
            [x[i] for i in reales] for x in (anios_validos, valores, parciales, inicios, fines, con_dato, datasets)]
    if not anios_validos:
        raise ErrorConsulta(f"No hay datos de {cfg.MESES_LARGOS[mes - 1]} en el periodo de la demo.", 422)
    ds_clim = _climatologia_ds(conector, variable, periodo_referencia, bbox, profundidad, ds_datos=datasets)
    clim12 = climatologia_mensual(conector, variable, bbox, periodo_referencia, profundidad, ds_clim=ds_clim)
    clim_mes = float(clim12[mes - 1])
    return {
        "anios": anios_validos,
        "valores": [round(v, 4) for v in valores],
        "climatologia_mes": round(clim_mes, 4),
        "anomalias": [round(v - clim_mes, 4) for v in valores],
        "parciales": parciales,
        "periodos": [{"inicio": i.isoformat(), "fin": f.isoformat()} for i, f in zip(inicios, fines)],
        "dias_con_dato": con_dato,
        "datasets": datasets,
        "ds_clim": ds_clim,
        "anios_omitidos": omitidos,
    }


def acumulado_y_normal(valores, climatologia) -> dict:
    """Acumulado de una serie diaria frente a lo normal, contando lo normal SOLO de los días con valor.

    Devuelve {"acumulado", "normal", "anomalia", "porcentaje", "dias", "dias_sin_dato"}. Si un día no
    tiene valor, tampoco suma su climatología: si no, la anomalía quedaría corta en lo normal de ese día.
    """
    pares = [(float(v), c) for v, c in zip(valores or [], climatologia or []) if v is not None]
    acumulado = float(sum(v for v, _c in pares))
    normal = float(sum(float(c) for _v, c in pares if c is not None))
    con_normal = sum(1 for _v, c in pares if c is not None)
    return {"acumulado": acumulado, "normal": normal if con_normal else None,
            "anomalia": (acumulado - normal) if con_normal else None,
            "porcentaje": (acumulado / normal * 100) if con_normal and normal > 0 else None,
            "dias": len(pares), "dias_sin_dato": len(valores or []) - len(pares)}


def _calificar_correlacion(r: float | None) -> str:
    if r is None:
        return "no calculable"
    a = abs(r)
    if a >= 0.9:
        grado = "muy alta"
    elif a >= 0.7:
        grado = "alta"
    elif a >= 0.5:
        grado = "moderada"
    elif a >= 0.3:
        grado = "baja"
    else:
        grado = "muy baja"
    return grado if r >= 0 else f"{grado} e inversa"


def comparar(serie_a: pd.Series, serie_b: pd.Series, etiqueta_a: str = "A", etiqueta_b: str = "B",
             unidad: str = "", decimales: int = 1, alineacion: str = "fecha",
             solo_correlacion: bool = False) -> dict:
    """Diferencia media (A − B), RMSE, correlación y frases.

    Con `solo_correlacion` (variables de unidades distintas, p. ej. SST frente a
    precipitación) no se calculan la diferencia ni el RMSE, que no tienen sentido
    físico: solo la correlación de Pearson sobre los datos coincidentes.
    """
    if alineacion == "fecha":
        comun = serie_a.index.intersection(serie_b.index)
        a = serie_a.loc[comun].values.astype(float)
        b = serie_b.loc[comun].values.astype(float)
    else:
        n = min(len(serie_a), len(serie_b))
        a = serie_a.values[:n].astype(float)
        b = serie_b.values[:n].astype(float)
    mascara = np.isfinite(a) & np.isfinite(b)
    a, b = a[mascara], b[mascara]
    if a.size == 0:
        return {"media": None, "rmse": None, "correlacion": None, "n": 0,
                "texto": ["Las dos series no tienen fechas en común con datos."]}
    correlacion = float(np.corrcoef(a, b)[0, 1]) if a.size > 2 and a.std() > 0 and b.std() > 0 else None
    if solo_correlacion:
        texto = ["Las dos variables se miden en unidades distintas: no se calculan la diferencia media ni el "
                 "error cuadrático medio, solo la correlación entre ambas series."]
        if correlacion is not None:
            texto.append(f"La correlación entre «{etiqueta_a}» y «{etiqueta_b}» es {cfg.fmt_num(correlacion, 2)} "
                         f"({_calificar_correlacion(correlacion)}) sobre {int(a.size)} datos coincidentes; "
                         "una correlación no implica una relación de causa y efecto.")
        else:
            texto.append("La correlación no se puede calcular con tan pocos datos coincidentes.")
        if alineacion != "fecha":
            texto.append("Los periodos difieren, por lo que las series se alinearon por día relativo "
                         "al inicio de cada periodo.")
        return {"media": None, "rmse": None, "correlacion": _redondear(correlacion, 4), "n": int(a.size),
                "solo_correlacion": True, "texto": texto}
    diferencia = a - b
    media = float(diferencia.mean())
    rmse = float(np.sqrt(np.mean(diferencia ** 2)))

    texto = []
    umbral = 0.5 * 10 ** (-decimales)
    if abs(media) < umbral:
        texto.append(f"En promedio, «{etiqueta_a}» y «{etiqueta_b}» coinciden "
                     f"(diferencia menor que {cfg.fmt_valor(umbral, unidad, decimales)}).")
    elif media > 0:
        texto.append(f"En promedio, «{etiqueta_a}» superó a «{etiqueta_b}» en "
                     f"{cfg.fmt_valor(media, unidad, decimales)}.")
    else:
        texto.append(f"En promedio, «{etiqueta_a}» fue inferior a «{etiqueta_b}» en "
                     f"{cfg.fmt_valor(abs(media), unidad, decimales)}.")
    if correlacion is not None:
        texto.append(f"La correlación entre ambas series es {cfg.fmt_num(correlacion, 2)} "
                     f"({_calificar_correlacion(correlacion)}); el error cuadrático medio es "
                     f"{cfg.fmt_valor(rmse, unidad, decimales + 1)}.")
    else:
        texto.append(f"El error cuadrático medio es {cfg.fmt_valor(rmse, unidad, decimales)}; "
                     "la correlación no se puede calcular con tan pocos datos.")
    if alineacion != "fecha":
        texto.append("Los periodos difieren, por lo que las series se alinearon por día relativo "
                     "al inicio de cada periodo.")
    return {"media": round(media, 4), "rmse": round(rmse, 4),
            "correlacion": _redondear(correlacion, 4), "n": int(a.size), "texto": texto}


def vectores(ds_u, ds_v, paso: int = 2) -> dict:
    """Submuestrea u y v (DataArray lat × lon) para dibujar flechas: {lats, lons, u, v, magnitud}."""
    paso = max(1, int(paso))
    u = _sin_profundidad(ds_u)
    v = _sin_profundidad(ds_v)
    if "time" in u.dims:
        u = u.isel(time=0) if u.sizes["time"] == 1 else promedio_tiempo(u)
        v = v.isel(time=0) if v.sizes["time"] == 1 else promedio_tiempo(v)
    u = u.transpose("lat", "lon").isel(lat=slice(None, None, paso), lon=slice(None, None, paso))
    v = v.transpose("lat", "lon").isel(lat=slice(None, None, paso), lon=slice(None, None, paso))
    magnitud = np.hypot(u.values, v.values)
    return {"lats": [float(x) for x in u["lat"].values], "lons": [float(x) for x in u["lon"].values],
            "u": _json_lista(u.values), "v": _json_lista(v.values), "magnitud": _json_lista(magnitud),
            "paso": paso}


# ---------------------------------------------------------------------------
# Escala de colores y estadísticas de un campo
# ---------------------------------------------------------------------------
def _escala(campo: np.ndarray, decimales: int, simetrica: bool = False) -> tuple[float, float]:
    finitos = campo[np.isfinite(campo)]
    if finitos.size == 0:
        return (-1.0, 1.0) if simetrica else (0.0, 1.0)
    factor = 10 ** decimales
    if simetrica:
        m = float(np.nanpercentile(np.abs(finitos), 99))
        m = math.ceil(m * factor) / factor if m > 0 else 1.0 / factor
        return -m, m
    vmin = math.floor(float(np.nanpercentile(finitos, 1)) * factor) / factor
    vmax = math.ceil(float(np.nanpercentile(finitos, 99)) * factor) / factor
    if vmax <= vmin:
        vmin, vmax = vmin - 1.0 / factor, vmax + 1.0 / factor
    return vmin, vmax


def estadisticas_campo(campo: np.ndarray) -> dict:
    finitos = campo[np.isfinite(campo)]
    if finitos.size == 0:
        return {"media": None, "minimo": None, "maximo": None, "desviacion": None,
                "celdas_validas": 0, "celdas_total": int(campo.size)}
    return {"media": round(float(finitos.mean()), 4), "minimo": round(float(finitos.min()), 4),
            "maximo": round(float(finitos.max()), 4), "desviacion": round(float(finitos.std()), 4),
            "celdas_validas": int(finitos.size), "celdas_total": int(campo.size)}


def _bbox_bordes(ds: xr.Dataset) -> list[float]:
    """bbox de los bordes de las celdas (para L.imageOverlay)."""
    medio = cfg.RESOLUCION_GRADOS / 2
    lats, lons = ds["lat"].values, ds["lon"].values
    return [float(lons.min() - medio), float(lats.min() - medio),
            float(lons.max() + medio), float(lats.max() + medio)]


def _texto_metodo_area(area: dict) -> str:
    if area["tipo"] == "punto":
        return f"Valor de la celda de 0.25° más cercana al punto ({area['nombre']})."
    return f"Promedio espacial simple de las celdas válidas de «{area['nombre']}» (malla de 0.25°)."


def _rechazar_anomalia_vectorial(conector: ConectorFuente, var: VariableFuente, consulta: str) -> None:
    """422 claro si la consulta usa la anomalía de una variable vectorial (corrientes).

    La climatología mensual de GLORYS12 publica las componentes medias u y v, no la rapidez media; la
    rapidez del vector medio siempre es menor o igual que la rapidez media, así que «rapidez − rapidez
    del vector medio» sale sesgada hacia valores positivos. Antes que presentar ese sesgo como anomalía,
    se rechaza la consulta y se indican las componentes, cuya anomalía sí es correcta. `consulta` es
    «mapa», «serie» o «mismo_mes» (la serie y «mismo mes» siempre comparan con la climatología)."""
    if not (var.es_vector and var.componentes):
        return
    componentes = [conector.variable(cid) for cid in var.componentes]
    nombres = [f"«{v.nombre}»" for v in componentes if v is not None] or ["sus componentes"]
    motivo = ("de GLORYS12 publica las componentes medias u y v, no la rapidez media, y restar la rapidez del "
              "vector medio sobrestimaría la anomalía")
    if consulta == "serie":
        detalle = (f"La serie de las corrientes no está disponible: incluye la anomalía, y la climatología {motivo}. "
                   f"Consulte la serie de {' o de '.join(nombres)}.")
    elif consulta == "mismo_mes":
        detalle = (f"«Mismo mes cada año» no está disponible para las corrientes: compara cada año con la "
                   f"climatología, y la {motivo}. Use {' o '.join(nombres)}.")
    else:
        detalle = (f"La anomalía de las corrientes no está disponible: la climatología {motivo}. Use «Valor», o la "
                   f"anomalía de {' o de '.join(nombres)}.")
    raise ErrorConsulta(detalle, 422)


# ---------------------------------------------------------------------------
# Consultas completas (respuestas de /api/consultas/*)
# ---------------------------------------------------------------------------
def consulta_mapa(c: dict) -> dict:
    """`/api/consultas/mapa`: campo de un día o promedio de un periodo, en valor o anomalía."""
    conector, var = _fuente_y_variable(c.get("fuente"), c.get("variable"))
    area = resolver_area(c.get("area"))
    modo = c.get("modo") or "valor"
    if modo not in ("valor", "anomalia"):
        raise ErrorConsulta(f"Modo desconocido: «{modo}». Use valor o anomalia.", 400)
    periodo_ref = c.get("periodo_referencia") or cfg.PERIODO_REFERENCIA_POR_DEFECTO
    z = _profundidad(c.get("profundidad"), var)
    if modo == "anomalia":
        _rechazar_anomalia_vectorial(conector, var, "mapa")
    nota = ""
    if c.get("periodo"):
        inicio, fin = _periodo(c["periodo"])
        # Acota a la cobertura de la fuente (inicio de la demo y fecha de corte) y
        # documenta el ajuste en el método; el subtítulo y la clave «periodo» de la
        # respuesta usan siempre el periodo efectivamente consultado.
        inicio, fin, nota = _acotar_periodo(inicio, fin, conector)
        fecha_unica = None
    else:
        fecha_unica, nota = _ajustar_fecha(_a_fecha(c.get("fecha") or _corte_conocido(conector)
                                                    or date.today()), conector)
        inicio = fin = fecha_unica

    ds, conector, var = obtener_dataset(conector.id, var.id, area["bbox"], inicio, fin, z,
                                        nombre_area=area["nombre"])
    da = _sin_profundidad(ds[var.id])
    n_dias = int(da.sizes["time"])
    descripcion_tiempo = (cfg.fmt_fecha(fecha_unica) if fecha_unica
                          else f"{cfg.fmt_fecha(inicio)} – {cfg.fmt_fecha(fin)} ({n_dias} días)")

    nota_fuente = nota_datos(conector, ds)
    clim_ds = None
    efectivo = None
    if modo == "anomalia":
        clim_ds = _climatologia_ds(conector, var.id, periodo_ref, area["bbox"], z, ds_datos=ds)
        efectivo = _periodo_efectivo(clim_ds)
        if var.es_vector and var.componentes:
            clim_campo = np.hypot(clim_ds[var.componentes[0]], clim_ds[var.componentes[1]]).values
        else:
            clim_campo = clim_ds[var.id].values                      # (12, lat, lon)
        meses = da["time"].dt.month.values
        campo = _nanmean(da.values - clim_campo[meses - 1], axis=0)
        paleta = var.paleta_anomalia
        titulo = f"Anomalía de {_nombre_para_frases(var)} · {conector.nombre}"
        metodo = (f"{'Campo diario' if fecha_unica else 'Promedio de los campos diarios'} de "
                  f"{descripcion_tiempo}{_texto_profundidad(z)}. "
                  f"{texto_metodo_anomalia(periodo_ref, efectivo, por_celda=True)}"
                  f"{nota_climatologia(conector, clim_ds)}{nota}")
    else:
        campo = da.values[0] if n_dias == 1 else _nanmean(da.values, axis=0)
        paleta = var.paleta
        titulo = f"{var.nombre} · {conector.nombre}"
        metodo = (f"{'Campo diario' if fecha_unica else 'Promedio de los campos diarios'} de "
                  f"{descripcion_tiempo}{_texto_profundidad(z)}, malla de 0.25°; celdas sin dato "
                  f"(tierra o mar según la variable) en transparente.{nota}")
    acumulado_info = None
    if n_dias > 1:
        # Cada celda debe tener dato en al menos el 80 % de los días con dato del periodo: si no, su
        # «promedio» sería el de unos pocos días presentado como el del periodo entero.
        valores_t = da.values if modo != "anomalia" else None
        validos = np.isfinite(da.values).sum(axis=0)
        dias_con_dato = int(np.isfinite(da.values).any(axis=tuple(range(1, da.values.ndim))).sum())
        minimo = math.ceil(MIN_FRACCION_DIAS_CELDA * dias_con_dato) if dias_con_dato else 1
        incompletas = (validos > 0) & (validos < minimo)
        n_incompletas = int(incompletas.sum())
        if n_incompletas:
            campo = np.where(incompletas, np.nan, campo)
            metodo += (f" {n_incompletas} {'celda quedó' if n_incompletas == 1 else 'celdas quedaron'} sin dato "
                       f"por tener valores en menos del {cfg.fmt_num(MIN_FRACCION_DIAS_CELDA * 100, 0)} % de los "
                       f"{dias_con_dato} días con dato del periodo.")
        if c.get("incluir_acumulado") and valores_t is not None:
            # Acumulado: suma de los días con dato, solo en las celdas que tienen TODOS esos días.
            completas = validos == dias_con_dato
            suma = np.where(completas, np.nansum(valores_t, axis=0), np.nan)
            acumulado_info = {"valores": _json_lista(suma), "dias_con_dato": dias_con_dato,
                              "dias_sin_dato": n_dias - dias_con_dato,
                              "celdas_excluidas": int(((validos > 0) & ~completas).sum())}
    vmin, vmax = _escala(campo, var.decimales, simetrica=(modo == "anomalia"))
    metodo = f"{metodo} {nota_fuente}"

    subtitulo = f"{descripcion_tiempo} · {area['nombre']}"
    if z is not None:
        subtitulo += f" · {cfg.fmt_num(z, 0)} m"
    if modo == "anomalia":
        subtitulo += f" · referencia {texto_periodo_referencia(periodo_ref, efectivo)}"
    suavizado = bool(c.get("suavizado", True))
    isolineas = bool(c.get("isolineas", False))
    if suavizado:
        metodo += (" Visualización suavizada por interpolación entre centros de celda"
                   f"{' con isolíneas' if isolineas else ''}; el valor de cada celda se consulta sin interpolar.")
    bbox_bordes = _bbox_bordes(ds)
    respuesta = {
        "lats": [float(x) for x in ds["lat"].values],
        "lons": [float(x) for x in ds["lon"].values],
        "valores": _json_lista(campo),
        "bbox": bbox_bordes,
        "bbox_celdas": [float(ds["lon"].min()), float(ds["lat"].min()),
                        float(ds["lon"].max()), float(ds["lat"].max())],
        "unidad": var.unidad,
        "vmin": vmin, "vmax": vmax,
        "paleta": paleta,
        "paleta_rgb": graficos.paleta_rgb(paleta, 16),
        "imagen_png": graficos.mapa_png(campo, bbox_bordes, paleta, vmin, vmax,
                                        suavizado=suavizado, isolineas=isolineas,
                                        decimales=var.decimales),
        "estilo": {"suavizado": suavizado, "isolineas": isolineas},
        "titulo": titulo,
        "subtitulo": subtitulo,
        "fuente": _info_fuente(conector, [ds, clim_ds]),
        "variable": var.a_dict(),
        "modo": modo,
        "periodo_referencia": periodo_ref if modo == "anomalia" else None,
        "profundidad": z,
        "fecha": fecha_unica.isoformat() if fecha_unica else None,
        "periodo": {"inicio": inicio.isoformat(), "fin": fin.isoformat()} if not fecha_unica else None,
        "area": area,
        "metodo": metodo,
        "fecha_corte": _fecha_corte(conector),
        "estadisticas": estadisticas_campo(campo),
        **procedencia_resultado([conector], [ds, clim_ds]),
    }
    if acumulado_info is not None:
        respuesta["acumulado"] = acumulado_info
    if var.es_vector and var.componentes:
        respuesta["vectores"] = vectores(ds[var.componentes[0]], ds[var.componentes[1]],
                                         int(c.get("paso") or 2))
    return respuesta


def consulta_serie(c: dict) -> dict:
    """`/api/consultas/serie`: serie del promedio de área con climatología, anomalías y resumen."""
    conector, var = _fuente_y_variable(c.get("fuente"), c.get("variable"))
    area = resolver_area(c.get("area"))
    inicio, fin = _periodo(c.get("periodo"))
    agregacion = c.get("agregacion") or "diaria"
    if agregacion not in AGREGACIONES:
        raise ErrorConsulta(f"Agregación desconocida: «{agregacion}». Use diaria, semanal o mensual.", 400)
    periodo_ref = c.get("periodo_referencia") or cfg.PERIODO_REFERENCIA_POR_DEFECTO
    z = _profundidad(c.get("profundidad"), var)
    _rechazar_anomalia_vectorial(conector, var, "serie")

    inicio_real, fin_real, nota = _acotar_periodo(inicio, fin, conector)
    ds, conector, var = obtener_dataset(conector.id, var.id, area["bbox"], inicio_real, fin_real, z,
                                        nombre_area=area["nombre"])
    s_diaria = serie(ds, var.id, "diaria")
    s = serie(ds, var.id, agregacion)
    periodos_agregados = _periodos_agregados(s_diaria, agregacion)
    ds_clim = _climatologia_ds(conector, var.id, periodo_ref, area["bbox"], z, ds_datos=ds)
    efectivo = _periodo_efectivo(ds_clim)
    clim12 = climatologia_mensual(conector, var.id, area["bbox"], periodo_ref, z, ds_clim=ds_clim)
    clim_serie = clim12[pd.DatetimeIndex(s.index).month.values - 1]
    anomalias = s.values - clim_serie
    nombre = var.long_name if var.es_vector else var.nombre
    resumen = resumen_lenguaje_sencillo(s, var.unidad, nombre, clim_serie, anomalias,
                                        texto_periodo_referencia(periodo_ref, efectivo),
                                        agregacion, var.decimales, inicio_real, fin_real,
                                        periodos_agregados=periodos_agregados)
    metodo = (f"{_texto_metodo_area(area)} Agregación {NOMBRE_AGREGACION[agregacion]}"
              f"{_texto_profundidad(z)}. {texto_metodo_anomalia(periodo_ref, efectivo)}"
              f"{nota_climatologia(conector, ds_clim)}{nota} {nota_datos(conector, ds)}")
    return {
        "fechas": [cfg.iso_fecha(t) for t in s.index],
        "valores": _json_lista(s.values),
        "climatologia": _json_lista(clim_serie),
        "anomalias": _json_lista(anomalias),
        "climatologia_mensual": _json_lista(clim12),
        # Fechas reales cubiertas por cada punto de la serie (útil con agregación semanal o mensual)
        "periodos_agregados": periodos_agregados,
        "unidad": var.unidad,
        "decimales": var.decimales,
        "nombre_variable": nombre,
        "agregacion": agregacion,
        "periodo_referencia": periodo_ref,
        "profundidad": z,
        "periodo": {"inicio": inicio_real.isoformat(), "fin": fin_real.isoformat()},
        "area": area,
        "resumen": resumen,
        "fuente": _info_fuente(conector, [ds, ds_clim]),
        "variable": var.a_dict(),
        "metodo": metodo,
        "fecha_corte": _fecha_corte(conector),
        **procedencia_resultado([conector], [ds, ds_clim]),
    }


def consulta_climatologia(c: dict) -> dict:
    """`/api/consultas/climatologia`: 12 valores mensuales (promedio de área).

    Con `c["simulada"]` verdadero se usa la climatología simulada de una fuente real con respaldo (p. ej.,
    el informe por variable cuya serie salió del simulador: así no mezcla dos climatologías)."""
    conector, var = _fuente_y_variable(c.get("fuente"), c.get("variable"))
    area = resolver_area(c.get("area"))
    periodo_ref = c.get("periodo_referencia") or cfg.PERIODO_REFERENCIA_POR_DEFECTO
    z = _profundidad(c.get("profundidad"), var)
    ds_clim = _climatologia_ds(conector, var.id, periodo_ref, area["bbox"], z,
                               forzar_simulada=bool(c.get("simulada")))
    clim12 = climatologia_mensual(conector, var.id, area["bbox"], periodo_ref, z, ds_clim=ds_clim)
    if not np.isfinite(clim12).any():
        # Reutiliza el mensaje claro de obtener_dataset
        obtener_dataset(conector.id, var.id, area["bbox"], conector.fecha_corte, conector.fecha_corte,
                        z, nombre_area=area["nombre"])
    # Sin el punto final del texto del área, para que la profundidad cierre la misma oración:
    # «… (malla de 0.25°) a 20 m de profundidad.»
    texto_area = _texto_metodo_area(area).rstrip(".")
    if _ds_real(ds_clim, conector):
        base = _con_punto(ds_clim.attrs.get("metodo")) or (
            f"Climatología mensual {periodo_ref} calculada con los datos de "
            f"{conector.nombre_corto or conector.nombre}.")
        # Fuentes cuya climatología es de otro periodo o sistema (Copernicus Marine: GLORYS12 1993-2016):
        # el origen de la climatología lo dice (periodo efectivo y diferencia de sistema).
        extra = nota_climatologia(conector, ds_clim) if _periodo_efectivo(ds_clim) else ""
        metodo = (f"{base}{extra} {texto_area}{_texto_profundidad(z)}. {nota_datos(conector, ds_clim)}")
    else:
        metodo = (f"Climatología mensual {periodo_ref}: promedio de la climatología analítica simulada "
                  f"sobre los días de cada mes, sin eventos interanuales ni ruido; "
                  f"{texto_area[0].lower()}{texto_area[1:]}"
                  f"{_texto_profundidad(z)}. {nota_datos(conector, ds_clim)}")
    return {
        "meses": list(cfg.MESES_CORTOS),
        "meses_largos": list(cfg.MESES_LARGOS),
        "valores": _json_lista(clim12),
        "unidad": var.unidad,
        "decimales": var.decimales,
        "nombre_variable": var.long_name if var.es_vector else var.nombre,
        "periodo_referencia": periodo_ref,
        "profundidad": z,
        "area": area,
        "fuente": _info_fuente(conector, [ds_clim]),
        "metodo": metodo,
        "fecha_corte": _fecha_corte(conector),
        **procedencia_resultado([conector], [ds_clim]),
    }


def consulta_mismo_mes(c: dict) -> dict:
    """`/api/consultas/mismo-mes`: promedio del mes en cada año y frases."""
    conector, var = _fuente_y_variable(c.get("fuente"), c.get("variable"))
    area = resolver_area(c.get("area"))
    try:
        mes = int(c.get("mes"))
    except (TypeError, ValueError):
        raise ErrorConsulta("El campo «mes» debe ser un entero entre 1 y 12.", 400) from None
    periodo_ref = c.get("periodo_referencia") or cfg.PERIODO_REFERENCIA_POR_DEFECTO
    z = _profundidad(c.get("profundidad"), var)
    _rechazar_anomalia_vectorial(conector, var, "mismo_mes")
    anios = list(range(cfg.FECHA_INICIO.year, (_corte_conocido(conector) or date.today()).year + 1))
    r = mismo_mes_cada_anio(conector, var.id, area["bbox"], mes, anios, z, periodo_ref, area["nombre"])

    nombre = _nombre_para_frases(var)
    articulo = _articulo(nombre)
    nombre_mes = cfg.MESES_LARGOS[mes - 1]
    dec = var.decimales
    efectivo = _periodo_efectivo(r["ds_clim"])
    partes = []
    for anio, valor, parcial, per, n_dato in zip(r["anios"], r["valores"], r["parciales"], r["periodos"],
                                                 r["dias_con_dato"]):
        parte = f"{cfg.fmt_valor(valor, var.unidad, dec)} en {anio}"
        detalle = [f"hasta el {cfg.fmt_fecha(per['fin'])}"] if parcial else []
        # Con la caché incompleta y sin acceso, un año puede tener solo algunos días con dato: se dice
        # cuántos (como en el resumen de las series), para no hacer pasar su media por la del mes entero.
        n_periodo = (date.fromisoformat(per["fin"]) - date.fromisoformat(per["inicio"])).days + 1
        if n_dato < n_periodo:
            detalle.append(f"con datos de {cfg.fmt_num(n_dato, 0)} de {cfg.fmt_num(n_periodo, 0)} días")
        if detalle:
            parte += f" ({'; '.join(detalle)})"
        partes.append(parte)
    lista = ", ".join(partes[:-1]) + (" y " if len(partes) > 1 else "") + partes[-1]
    texto = [f"En {nombre_mes}, {articulo} {nombre}{_texto_profundidad(z)} en «{area['nombre']}» "
             f"promedió {lista}.",
             f"Lo normal para {nombre_mes} (periodo de referencia "
             f"{_referencia_en_parentesis(texto_periodo_referencia(periodo_ref, efectivo))}) es "
             f"{cfg.fmt_valor(r['climatologia_mes'], var.unidad, dec)}."]
    if len(r["anios"]) > 1:
        if var.unidad == "°C":
            alto, bajo = "cálido", "frío"
        elif var.unidad == "mm/día":
            alto, bajo = "lluvioso", "seco"
        else:
            alto, bajo = "alto", "bajo"
        i_max = int(np.argmax(r["valores"]))
        i_min = int(np.argmin(r["valores"]))
        texto.append(f"El {nombre_mes} más {alto} fue el de {r['anios'][i_max]} "
                     f"({cfg.fmt_anomalia(r['anomalias'][i_max], var.unidad, dec)} respecto a lo normal) "
                     f"y el más {bajo}, el de {r['anios'][i_min]} "
                     f"({cfg.fmt_anomalia(r['anomalias'][i_min], var.unidad, dec)}).")
    texto_area = _texto_metodo_area(area)
    metodo = (f"Promedio de los días disponibles de {nombre_mes} de cada año; "
              f"{texto_area[0].lower()}{texto_area[1:]} "
              f"{texto_metodo_anomalia(periodo_ref, efectivo)}{nota_climatologia(conector, r['ds_clim'])} "
              f"{nota_datos(conector, None, ds_lista=r['datasets'])}")
    aviso_omitidos = ""
    if r["anios_omitidos"]:
        # Años que respondió el simulador de respaldo mientras los demás son reales: se omitieron (nunca se
        # mezclan). El aviso se añade a mano porque sus Dataset ya no están en la respuesta; además, con
        # avisos la respuesta no se guarda en la caché de consultas y se reintenta cuando vuelva la fuente.
        omitidos = [str(a) for a in r["anios_omitidos"]]
        lista_omitidos = ", ".join(omitidos[:-1]) + (" y " if len(omitidos) > 1 else "") + omitidos[-1]
        plural = len(omitidos) > 1
        aviso_omitidos = (f"{'Se omiten los años' if plural else 'Se omite el año'} {lista_omitidos}: "
                          f"{'no están' if plural else 'no está'} en la caché local de "
                          f"{conector.nombre_corto or conector.nombre} y no se {'pudieron' if plural else 'pudo'} "
                          "obtener ahora; el SOME no mezcla años simulados con años reales.")
        texto.append(aviso_omitidos)
    salida = {
        "mes": mes, "nombre_mes": nombre_mes,
        "anios": r["anios"], "valores": r["valores"], "climatologia_mes": r["climatologia_mes"],
        "anomalias": r["anomalias"], "parciales": r["parciales"], "periodos": r["periodos"],
        "unidad": var.unidad, "decimales": dec, "nombre_variable": var.nombre,
        "periodo_referencia": periodo_ref, "profundidad": z, "area": area,
        "texto": texto, "fuente": _info_fuente(conector, r["datasets"] + [r["ds_clim"]]), "metodo": metodo,
        "fecha_corte": _fecha_corte(conector), **procedencia_resultado([conector], r["datasets"] + [r["ds_clim"]]),
    }
    if aviso_omitidos:
        salida["avisos"] = list(salida.get("avisos") or []) + [aviso_omitidos]
        previo = str(salida["fuente"].get("aviso") or "").strip()
        salida["fuente"]["aviso"] = f"{previo} {aviso_omitidos}".strip()
    return salida


MARGEN_TIERRA_ADENTRO = 0.5   # grados: con una fuente real, solo se rechaza de antemano un punto claramente en tierra


def _rechazar_punto_en_tierra(conector: ConectorFuente, area: dict) -> None:
    """422 «El punto … está en tierra» antes de consultar la fuente.

    Con una fuente simulada se usa su máscara tierra/mar. Con una fuente real, la máscara sintética es
    solo una aproximación de la costa: se rechaza el punto únicamente si también lo son todos los puntos
    a ±0.5° (claramente tierra adentro), para no descargar datos de Copernicus Marine que serían todos
    NaN; un punto costero se consulta y, si no tiene dato, lo detecta obtener_dataset con un mensaje claro."""
    lat, lon = float(area["lat"]), float(area["lon"])
    if _es_real(conector):
        m = MARGEN_TIERRA_ADENTRO
        tierra = bool(sim.mascara_tierra([lat - m, lat, lat + m], [lon - m, lon, lon + m]).all())
    else:
        tierra = bool(sim.mascara_tierra([lat], [lon])[0, 0])
    if tierra:
        raise ErrorConsulta(f"El punto {cfg.fmt_coordenada(lat, lon)} está en tierra; elija un punto en el mar.", 422)


def consulta_perfil(c: dict) -> dict:
    """`/api/consultas/perfil`: perfil vertical (0–100 m) de thetao o so en un punto y fecha."""
    fuente_id = c.get("fuente") or "copernicus_phy"
    conector, var = _fuente_y_variable(fuente_id, c.get("variable") or "thetao")
    if not var.tiene_profundidad or var.es_vector:
        raise ErrorConsulta(f"La variable «{var.nombre}» no tiene perfil vertical; use thetao o so.", 422)
    area = resolver_area({"tipo": "punto", "lat": c.get("lat"), "lon": c.get("lon")})
    fecha, nota = _ajustar_fecha(_a_fecha(c.get("fecha") or conector.fecha_corte), conector)
    _rechazar_punto_en_tierra(conector, area)

    ds, conector, var = obtener_dataset(conector.id, var.id, area["bbox"], fecha, fecha,
                                        profundidades=list(cfg.PROFUNDIDADES), nombre_area=area["nombre"])
    p = perfil(ds, var.id)
    capa_mezcla = None
    ds_cm = None
    if conector.variable("mlotst") is not None:
        ds_cm, _, _ = obtener_dataset(conector.id, "mlotst", area["bbox"], fecha, fecha,
                                      nombre_area=area["nombre"])
        capa_mezcla = _redondear(float(ds_cm["mlotst"].values.ravel()[0]), 1)

    dec = var.decimales
    nombre = _nombre_para_frases(var)
    valores = p["valores"]
    texto = [f"En el punto {cfg.fmt_coordenada(area['lat'], area['lon'])}, el {cfg.fmt_fecha(fecha)} "
             f"{_articulo(nombre)} {nombre} fue {cfg.fmt_valor(valores[0], var.unidad, dec)} en superficie y "
             f"{cfg.fmt_valor(valores[-1], var.unidad, dec)} a {cfg.fmt_num(p['profundidades'][-1], 0)} m."]
    if capa_mezcla is not None:
        # La frase se ajusta a los datos reales del perfil: durante El Niño la anomalía
        # cálida se atenúa con la profundidad y la temperatura ya desciende dentro de
        # la capa de mezcla, así que no siempre es «casi uniforme».
        dentro = [v for z_i, v in zip(p["profundidades"], valores)
                  if v is not None and z_i <= capa_mezcla + _TOL]
        cambio_capa = abs(dentro[0] - dentro[-1]) if len(dentro) > 1 else 0.0
        umbral = 0.5 if var.unidad == "°C" else 0.1
        frase = f"La capa de mezcla alcanzó {cfg.fmt_num(capa_mezcla, 0)} m; "
        if cambio_capa < umbral:
            frase += (f"por encima de esa profundidad {_articulo(nombre)} {nombre} es casi uniforme y "
                      f"debajo cambia con rapidez.")
        else:
            sentido = "descenso" if dentro[0] > dentro[-1] else "aumento"
            frase += (f"por encima de esa profundidad el {sentido} de {_articulo(nombre)} {nombre} es moderado "
                      f"({cfg.fmt_valor(cambio_capa, var.unidad, dec)} hasta los "
                      f"{cfg.fmt_num(max(z_i for z_i in p['profundidades'] if z_i <= capa_mezcla + _TOL), 0)} m) "
                      f"y debajo se acelera.")
        texto.append(frase)
    if len(valores) > 1 and valores[0] is not None and valores[-1] is not None:
        delta = valores[0] - valores[-1]
        verbo = "disminuye" if delta > 0 else "aumenta"
        texto.append(f"Entre la superficie y los {cfg.fmt_num(p['profundidades'][-1], 0)} m, "
                     f"{_articulo(nombre)} {nombre} {verbo} {cfg.fmt_valor(abs(delta), var.unidad, dec)}.")
    metodo = (f"Perfil vertical en la celda de 0.25° más cercana al punto, campo diario del "
              f"{cfg.fmt_fecha(fecha)}, niveles {', '.join(cfg.fmt_num(z, 0) for z in p['profundidades'])} m. "
              f"La capa de mezcla proviene de la variable mlotst.{nota} {nota_datos(conector, ds)}")
    return {
        "profundidades": p["profundidades"], "valores": valores, "unidad": var.unidad,
        "decimales": dec, "nombre_variable": var.nombre, "capa_mezcla_m": capa_mezcla,
        "fecha": fecha.isoformat(), "lat": area["lat"], "lon": area["lon"], "area": area,
        "texto": texto, "fuente": _info_fuente(conector, [ds, ds_cm]), "variable": var.a_dict(),
        "metodo": metodo, "fecha_corte": _fecha_corte(conector),
        **procedencia_resultado([conector], [ds, ds_cm]),
    }


def consulta_cortina(c: dict) -> dict:
    """`/api/consultas/cortina`: matriz profundidad × tiempo en un punto."""
    fuente_id = c.get("fuente") or "copernicus_phy"
    conector, var = _fuente_y_variable(fuente_id, c.get("variable") or "thetao")
    if not var.tiene_profundidad or var.es_vector:
        raise ErrorConsulta(f"La variable «{var.nombre}» no tiene dimensión vertical; use thetao o so.", 422)
    area = resolver_area({"tipo": "punto", "lat": c.get("lat"), "lon": c.get("lon")})
    inicio, fin = _periodo(c.get("periodo"))
    _rechazar_punto_en_tierra(conector, area)
    inicio, fin, nota = _acotar_periodo(inicio, fin, conector)
    ds, conector, var = obtener_dataset(conector.id, var.id, area["bbox"], inicio, fin,
                                        profundidades=list(cfg.PROFUNDIDADES), nombre_area=area["nombre"])
    r = cortina(ds, var.id)
    metodo = (f"Cortina profundidad-tiempo en la celda de 0.25° más cercana al punto; campos diarios "
              f"entre {cfg.fmt_fecha(r['fechas'][0])} y {cfg.fmt_fecha(r['fechas'][-1])} en los niveles "
              f"{', '.join(cfg.fmt_num(z, 0) for z in r['profundidades'])} m.{nota} {nota_datos(conector, ds)}")
    return {
        "fechas": r["fechas"], "profundidades": r["profundidades"], "valores": r["valores"],
        "unidad": var.unidad, "decimales": var.decimales, "nombre_variable": var.nombre,
        "paleta": var.paleta, "lat": area["lat"], "lon": area["lon"], "area": area,
        "fuente": _info_fuente(conector, [ds]), "variable": var.a_dict(), "metodo": metodo,
        "fecha_corte": _fecha_corte(conector), **procedencia_resultado([conector], [ds]),
    }


def _serie_para_comparar(lado: dict, agregacion: str, nombre_lado: str) -> dict:
    if not isinstance(lado, dict):
        raise ErrorConsulta(f"El lado «{nombre_lado}» de la comparación debe ser un objeto.", 400)
    conector, var = _fuente_y_variable(lado.get("fuente"), lado.get("variable"))
    area = resolver_area(lado.get("area"))
    inicio, fin = _periodo(lado.get("periodo"), f"{nombre_lado}.periodo")
    z = _profundidad(lado.get("profundidad"), var)
    inicio_real, fin_real, _ = _acotar_periodo(inicio, fin, conector)
    ds, conector, var = obtener_dataset(conector.id, var.id, area["bbox"], inicio_real, fin_real, z,
                                        nombre_area=area["nombre"])
    s = serie(ds, var.id, agregacion)
    nombre_var = var.nombre.split("(")[0].strip()
    etiqueta = lado.get("etiqueta") or (
        f"{nombre_var}{(' ' + cfg.fmt_num(z, 0) + ' m') if z is not None and var.tiene_profundidad else ''} · "
        f"{conector.nombre_corto or conector.nombre} · {area['nombre']} "
        f"({cfg.fmt_fecha(inicio_real)} – {cfg.fmt_fecha(fin_real)})")
    return {"serie": s, "conector": conector, "variable": var, "area": area, "inicio": inicio_real,
            "fin": fin_real, "profundidad": z, "etiqueta": etiqueta, "ds": ds,
            # Periodo tal como lo pidió el usuario (sin acotar por la fecha de corte de la fuente)
            "periodo_pedido": (inicio, fin)}


def consulta_comparar(c: dict) -> dict:
    """`/api/consultas/comparar`: dos fuentes o dos periodos, alineados por fecha o día relativo."""
    agregacion = c.get("agregacion") or "diaria"
    if agregacion not in AGREGACIONES:
        raise ErrorConsulta(f"Agregación desconocida: «{agregacion}». Use diaria, semanal o mensual.", 400)
    a = _serie_para_comparar(c.get("a"), agregacion, "a")
    b = _serie_para_comparar(c.get("b"), agregacion, "b")
    # Con unidades distintas (p. ej. SST frente a precipitación) restar no tiene sentido físico
    # («superó en 24.0 °C» a una lluvia): solo se calcula la correlación y cada serie conserva
    # su unidad (el gráfico usa dos ejes).
    mismas_unidades = a["variable"].unidad == b["variable"].unidad
    # Si el usuario pidió el mismo periodo para A y B, se alinea por fecha aunque una fuente
    # tenga una fecha de corte anterior (la comparación usa solo las fechas coincidentes).
    mismo_periodo = a["periodo_pedido"] == b["periodo_pedido"]
    alineacion = "fecha" if mismo_periodo else "dia_relativo"
    unidad = a["variable"].unidad if mismas_unidades else None
    dec = max(a["variable"].decimales, b["variable"].decimales)
    diferencia = comparar(a["serie"], b["serie"], a["etiqueta"], b["etiqueta"], unidad or "", dec, alineacion,
                          solo_correlacion=not mismas_unidades)
    texto = list(diferencia.pop("texto"))

    def _lado(d: dict) -> dict:
        s = d["serie"]
        return {"etiqueta": d["etiqueta"], "fechas": [cfg.iso_fecha(t) for t in s.index],
                "valores": _json_lista(s.values), "unidad": d["variable"].unidad,
                "decimales": d["variable"].decimales, "fuente": _info_fuente(d["conector"], [d["ds"]]),
                "variable": d["variable"].id, "nombre_variable": d["variable"].nombre,
                "area": d["area"], "profundidad": d["profundidad"],
                "periodo": {"inicio": d["inicio"].isoformat(), "fin": d["fin"].isoformat()},
                "dias_relativos": list(range(len(s))),
                **procedencia_resultado([d["conector"]], [d["ds"]])}

    conectores = [a["conector"]]
    if b["conector"].id != a["conector"].id:
        conectores.append(b["conector"])
    procedencia = procedencia_resultado(conectores, [a["ds"], b["ds"]])
    if len(conectores) == 1:
        nota_fuente = nota_datos(a["conector"], None, procedencia.get("dias_preliminares"),
                                 procedencia.get("dias_faltantes"), ds_lista=[a["ds"], b["ds"]])
    elif procedencia["procedencia"] == "mixto":
        reales = {id(d["ds"]): _ds_real(d["ds"], d["conector"]) for d in (a, b)}
        nota_fuente = ("Procedencia mixta: "
                       + "; ".join(f"«{d['etiqueta']}» con {'datos reales' if reales[id(d['ds'])] else 'datos simulados'}"
                                   for d in (a, b)) + ". "
                       + " ".join(nota_datos(d["conector"], d["ds"]) for d in (a, b)
                                  if reales[id(d["ds"])] or d["ds"].attrs.get("aviso")))
    else:
        nota_fuente = (" ".join([NOTA_SIMULACION] + [_con_punto(x) for x in procedencia.get("avisos", [])])
                       if procedencia["procedencia"] == "simulado" else
                       " ".join(nota_datos(d["conector"], d["ds"]) for d in (a, b)))
    texto_alineacion = "fecha" if alineacion == "fecha" else "día relativo al inicio de cada periodo"
    if mismas_unidades:
        metodo = (f"Series del promedio de área con agregación {NOMBRE_AGREGACION[agregacion]}; "
                  f"diferencia = A − B; alineación por {texto_alineacion}; "
                  f"correlación de Pearson y RMSE sobre los datos coincidentes. {nota_fuente}")
    else:
        metodo = (f"Series del promedio de área con agregación {NOMBRE_AGREGACION[agregacion]}; unidades distintas "
                  f"({a['variable'].unidad} y {b['variable'].unidad}): solo correlación de Pearson sobre los datos "
                  f"coincidentes, sin diferencia ni RMSE; alineación por {texto_alineacion}. {nota_fuente}")
    fuentes = [_info_fuente(con, [d["ds"] for d in (a, b) if d["conector"].id == con.id]) for con in conectores]
    return {
        "a": _lado(a), "b": _lado(b), "diferencia": diferencia, "texto": texto,
        "alineacion": alineacion, "agregacion": agregacion, "unidad": unidad, "decimales": dec,
        "mismas_unidades": mismas_unidades,
        "fuentes": fuentes, "metodo": metodo,
        "fecha_corte": max(_fecha_corte(a["conector"]), _fecha_corte(b["conector"])),
        **procedencia,
    }


def consulta_vectores(c: dict) -> dict:
    """`/api/consultas/vectores`: u, v y magnitud submuestreados para dibujar flechas."""
    fuente_id = c.get("fuente") or "copernicus_phy"
    conector, var = _fuente_y_variable(fuente_id, c.get("variable") or "corrientes")
    if not var.es_vector or not var.componentes:
        raise ErrorConsulta(f"La variable «{var.nombre}» no es vectorial; use «corrientes».", 422)
    area = resolver_area(c.get("area"))
    z = _profundidad(c.get("profundidad"), var)
    fecha, nota = _ajustar_fecha(_a_fecha(c.get("fecha") or conector.fecha_corte), conector)
    paso = int(c.get("paso") or 2)
    ds, conector, var = obtener_dataset(conector.id, var.id, area["bbox"], fecha, fecha, z,
                                        nombre_area=area["nombre"])
    r = vectores(ds[var.componentes[0]], ds[var.componentes[1]], paso)
    r.update({
        "unidad": var.unidad, "fecha": fecha.isoformat(), "profundidad": z, "area": area,
        "fuente": _info_fuente(conector, [ds]),
        "metodo": (f"Componentes uo y vo del campo diario del {cfg.fmt_fecha(fecha)}"
                   f"{_texto_profundidad(z)}, submuestreadas cada {paso} celdas.{nota} {nota_datos(conector, ds)}"),
        "fecha_corte": _fecha_corte(conector), **procedencia_resultado([conector], [ds]),
    })
    return r


UMBRAL_ENSO = 0.5    # °C: El Niño (fase cálida) ≥ +0.5; La Niña (fase fría) ≤ −0.5 (los del conector y el panel)


def _frases_indice_real(r: dict) -> list[str]:
    """Frases del índice Niño 1+2 REAL: la semana más reciente (incompleta o con días preliminares, si lo es), la
    anterior y, en fase cálida o fría, desde qué semana el índice se mantiene más allá del umbral. Solo describe
    lo que el sistema calcula (anomalía semanal y umbrales de ±0.5 °C), sin categorías de intensidad."""
    sem = r.get("semana_actual") or {}
    detalles = []
    if sem.get("parcial"):
        detalles.append(f"semana incompleta: {_texto_dias(int(sem.get('dias') or 0))} con dato")
    if sem.get("preliminar"):
        detalles.append("datos preliminares")
    semana = (f"la semana del {cfg.fmt_fecha(sem['desde'])} al {cfg.fmt_fecha(sem['hasta'])}" if sem.get("desde")
              else f"la semana centrada en el {cfg.fmt_fecha(r['fecha_actual'])}")
    if detalles:
        semana += f" ({'; '.join(detalles)})"
    frases = [f"El índice Niño 1+2 (NOAA OISST, región 0–10° S, 90–80° O) es "
              f"{cfg.fmt_anomalia(r['actual'], '°C', 2)} en {semana}: condición {r['fase']}."]
    fechas, valores = list(r.get("fechas") or []), list(r.get("valores") or [])
    preliminar = list(r.get("preliminar") or [False] * len(fechas))
    validos = [k for k, v in enumerate(valores) if v is not None]
    if not validos:
        return frases
    k = validos[-1]
    if k >= 1 and valores[k - 1] is not None:
        miercoles = date.fromisoformat(str(fechas[k - 1])[:10])
        desde, hasta = miercoles - timedelta(days=3), miercoles + timedelta(days=3)
        frases.append(f"La semana anterior (del {cfg.fmt_fecha(desde)} al {cfg.fmt_fecha(hasta)}"
                      f"{'; datos preliminares' if k - 1 < len(preliminar) and preliminar[k - 1] else ''}) fue "
                      f"{cfg.fmt_anomalia(valores[k - 1], '°C', 2)}.")
    actual = valores[k]
    if abs(actual) >= UMBRAL_ENSO:
        calida = actual > 0
        racha = 0
        for v in reversed(valores[:k + 1]):
            if v is None or (v < UMBRAL_ENSO if calida else v > -UMBRAL_ENSO):
                break
            racha += 1
        primera = k - racha + 1
        domingo = date.fromisoformat(str(fechas[primera])[:10]) - timedelta(days=3)
        fase_texto = "en la fase cálida (≥ +0.5 °C)" if calida else "en la fase fría (≤ −0.5 °C)"
        if primera == 0:
            frases.append(f"El índice está {fase_texto} en todas las semanas de la serie, desde la del "
                          f"{cfg.fmt_fecha(domingo)} ({cfg.fmt_num(racha, 0)} "
                          f"{'semana' if racha == 1 else 'semanas'}).")
        else:
            frases.append(f"El índice está {fase_texto} desde la semana del {cfg.fmt_fecha(domingo)}: "
                          f"{cfg.fmt_num(racha, 0)} {'semana' if racha == 1 else 'semanas consecutivas'}.")
    return frases


def serie_indice_enso(inicio: date | None = None, fin: date | None = None, paso_dias: int = 7) -> dict:
    """Serie semanal del índice Niño 1+2 (widget «indice_enso» del panel y figura 4 del boletín ERFEN).

    Con NOAA OISST real es el índice REAL que calcula su conector (`serie_indice_nino12`): anomalía de la SST en
    la región Niño 1+2 oficial (0–10° S, 90–80° O) respecto de la climatología diaria 1991-2020 de NOAA PSL, en
    semanas de domingo a sábado etiquetadas con el miércoles, como NOAA CPC. `valores` lleva None en las semanas
    con menos de 4 días con dato y la semana más reciente se señala si está incompleta o tiene días preliminares
    (`semana_actual`, `parcial`, `preliminar`, `dias`). Si el conector no puede calcularlo (p. ej., falta la
    climatología diaria en la caché local) responde su índice simulado, señalado (`procedencia` «simulado»,
    `respaldo_simulado` y `avisos`); nunca se mezclan semanas reales y simuladas. `paso_dias` solo se usa sin
    conector con índice real (el generador simulado del prototipo, un valor cada `paso_dias` días)."""
    inicio = inicio or cfg.FECHA_INICIO
    fin = fin or fecha_corte_sistema()
    conector = catalogo.conector("noaa_oisst") if catalogo.existe_fuente("noaa_oisst") else None
    if conector is None or not hasattr(conector, "serie_indice_nino12"):
        return _indice_enso_simulado(conector, inicio, fin, paso_dias)
    try:
        r = conector.serie_indice_nino12(inicio, fin)
    except ValueError as e:                  # SOME_NOAA_MODO=real sin datos: 422 claro, sin respaldo simulado
        raise ErrorConsulta(str(e), 422) from None
    except Exception as e:  # noqa: BLE001 - el índice nunca debe tumbar el panel ni el boletín
        raise ErrorConsulta(f"No fue posible calcular el índice Niño 1+2 con NOAA OISST: {e}", 503) from None

    real = bool(r.get("datos_reales")) and r.get("procedencia") == "real"
    procedencia = "real" if real else "simulado"
    insignia = str(r.get("insignia") or (conector.insignia if real else cfg.INSIGNIA_SIMULADOS))
    avisos = [str(r["aviso"]).strip()] if str(r.get("aviso") or "").strip() else []
    # El índice se deriva de la SST de NOAA OISST en la región Niño 1+2: lleva su crédito (RF-12) y la procedencia
    # del ÍNDICE (el conector puede ser real y haber respondido con su índice simulado de respaldo)
    fuente = _info_fuente(conector)
    fuente.update({"datos_reales": real, "procedencia": procedencia, "insignia": insignia,
                   "credito": str(r.get("credito") or fuente["credito"])})
    if real and r.get("origen_datos"):
        fuente["origen_datos"] = str(r["origen_datos"])
    if not real:
        fuente["referencias"] = ""
    fuente.pop("aviso", None)
    if avisos:
        fuente["aviso"] = " ".join(_con_punto(a) for a in avisos)

    valores = [None if v is None else _redondear(v, 2) for v in r.get("valores") or []]
    actual = _redondear(r.get("actual"), 2)
    if real:
        texto = _frases_indice_real(dict(r, valores=valores, actual=actual))
        metodo = f"{_con_punto(r.get('metodo'))} {_con_punto(r.get('origen_datos'))}".strip()
        if int(r.get("dias_preliminares") or 0):
            metodo += (" Los días preliminares de NOAA OISST (los últimos ~15) los reemplaza NOAA/NCEI por la versión "
                       "final unas dos semanas después: el valor de las semanas recientes puede cambiar ligeramente.")
        if int(r.get("dias_faltantes") or 0):
            metodo += (f" {_texto_dias(int(r['dias_faltantes']))} del periodo sin dato en la caché local: cada semana "
                       "usa sus días disponibles (al menos 4; con menos, la semana queda sin valor).")
    else:
        fecha_actual = r.get("fecha_actual") or r.get("fecha_corte") or fin.isoformat()
        respaldo = " (respaldo: el índice real no se pudo calcular, ver el aviso)" if r.get("respaldo_simulado") else ""
        texto = [f"El índice Niño 1+2 simulado{respaldo} es {cfg.fmt_anomalia(actual, '°C')} al "
                 f"{cfg.fmt_fecha(fecha_actual)}: condición {r.get('fase')}."]
        metodo = " ".join([_con_punto(r.get("metodo")), NOTA_SIMULACION] + [_con_punto(a) for a in avisos]).strip()
    salida = {
        "fechas": [str(f)[:10] for f in r.get("fechas") or []], "valores": valores, "unidad": "°C",
        "actual": actual, "fecha_actual": r.get("fecha_actual"), "fase": r.get("fase"),
        "semaforo": r.get("semaforo"), "texto": texto, "metodo": metodo,
        "fuente": fuente, "credito": fuente["credito"],
        "fecha_corte": str(r.get("fecha_corte") or fin.isoformat())[:10],
        "datos_simulados": not real, "procedencia": procedencia, "insignia": insignia,
    }
    for clave in ("semana_actual", "sst_actual", "sst", "dias", "parcial", "preliminar", "dias_finales",
                  "dias_preliminares", "dias_faltantes", "origen_datos"):
        if clave in r:
            salida[clave] = r[clave]
    if avisos:
        salida["avisos"] = avisos
    if r.get("respaldo_simulado"):
        salida["respaldo_simulado"] = True
    return salida


def _indice_enso_simulado(conector: ConectorFuente | None, inicio: date, fin: date, paso_dias: int = 7) -> dict:
    """Índice Niño 1+2 del generador simulado del prototipo, un valor cada `paso_dias` días (solo si el conector
    de NOAA OISST no calcula el índice real)."""
    fechas = pd.date_range(inicio, fin, freq=f"{int(paso_dias)}D")
    if fechas[-1].date() != fin:
        fechas = fechas.append(pd.DatetimeIndex([pd.Timestamp(fin)]))
    valores = sim.indice_enso(fechas)
    actual = float(valores[-1])
    if actual >= 0.5:
        fase, semaforo = "El Niño (fase cálida)", "rojo"
    elif actual <= -0.5:
        fase, semaforo = "La Niña (fase fría)", "ambar"
    else:
        fase, semaforo = "neutra", "verde"
    # El índice se deriva de la SST de NOAA OISST en la región Niño 1+2: lleva su crédito (RF-12); es simulado
    fuente = _info_fuente(conector) if conector is not None else None
    if fuente is not None:
        fuente.update({"datos_reales": False, "procedencia": "simulado", "insignia": cfg.INSIGNIA_SIMULADOS,
                       "referencias": ""})
    return {
        "fechas": [cfg.iso_fecha(t) for t in fechas], "valores": _json_lista(valores),
        "unidad": "°C", "actual": round(actual, 2), "fase": fase, "semaforo": semaforo,
        "texto": [f"El índice Niño 1+2 simulado es {cfg.fmt_anomalia(actual, '°C')} al "
                  f"{cfg.fmt_fecha(fin)}: condición {fase}."],
        "metodo": ("Índice Niño 1+2 simulado: anomalía media de SST (NOAA OISST) de la región Niño 1+2 "
                   "interpolada entre nodos mensuales. " + NOTA_SIMULACION),
        "fuente": fuente,
        "credito": fuente["credito"] if fuente else "",
        # Derivado de la SST simulada: siempre «simulado»
        "fecha_corte": fin.isoformat(), **procedencia_de([]),
    }


# ---------------------------------------------------------------------------
# Autodiagnóstico: python -m backend.motor
# ---------------------------------------------------------------------------
def _autodiagnostico() -> int:  # pragma: no cover - utilidad manual
    import base64
    import io

    from PIL import Image

    from . import cache

    errores: list[str] = []
    tiempos: dict[str, float] = {}

    def verificar(condicion: bool, mensaje: str) -> None:
        estado = "OK " if condicion else "FALLA"
        print(f"   [{estado}] {mensaje}")
        if not condicion:
            errores.append(mensaje)

    def cronometrar(nombre: str, funcion):
        t0 = time.perf_counter()
        resultado = funcion()
        tiempos[nombre] = time.perf_counter() - t0
        print(f"-> {nombre}: {tiempos[nombre] * 1000:.0f} ms")
        return resultado

    print(f"Autodiagnóstico del motor SOME {cfg.VERSION} — {catalogo.insignias_cabecera()['insignia_cabecera']}\n")
    # NOAA OISST y Copernicus Marine pueden ser reales (caché local o acceso) o simulados: con datos reales, las
    # verificaciones de sus valores usan rangos físicos plausibles del Pacífico ecuatorial oriental y no los del
    # simulador (p. ej., la anomalía de octubre de 2026 del simulador, +1.8 °C, no es la observada).
    cop_real = bool(catalogo.conector("copernicus_phy").datos_reales)
    noaa_real = bool(catalogo.conector("noaa_oisst").datos_reales)
    corte_sistema = fecha_corte_sistema()
    print(f"NOAA OISST: {'datos REALES' if noaa_real else 'datos simulados'} · Copernicus Marine: "
          f"{'datos REALES' if cop_real else 'datos simulados'} · fecha de corte del sistema "
          f"{cfg.fmt_fecha(corte_sistema)}\n")

    # 0. Rendimiento del generador simulado (respaldo de las fuentes reales): 3 años diarios del área completa
    cache.limpiar_todo()
    fechas_todas = pd.date_range(cfg.FECHA_INICIO, corte_sistema, freq="D")
    campos = cronometrar(f"Generar SST de {len(fechas_todas)} días del área completa",
                         lambda: sim.generar_campos("sst", fechas_todas))
    verificar(tiempos[next(iter(tiempos))] < 3.0, "generación de 3 años < 3 s")
    finitos = campos[np.isfinite(campos)]
    print(f"   SST global: mín {cfg.fmt_num(finitos.min())} °C, máx {cfg.fmt_num(finitos.max())} °C, "
          f"media {cfg.fmt_num(finitos.mean())} °C")
    verificar(18.5 <= finitos.min() and finitos.max() <= 31.0,
              "SST entre 19 y ~31 °C en todo el periodo (tope del termostato tropical)")
    verificar(sim.mascara_tierra(cfg.LAT, cfg.LON).sum() > 50, "la máscara tiene celdas de tierra")
    verificar(np.isnan(campos[0][sim.mascara_tierra(cfg.LAT, cfg.LON)]).all(), "SST es NaN en tierra")

    # 1. Mapa SST de una fecha
    mapa = cronometrar("Mapa SST 27/10/2026 (área de estudio)", lambda: consulta_mapa({
        "fuente": "noaa_oisst", "variable": "sst", "area": {"tipo": "recorte", "recorte": "area_estudio"},
        "fecha": "2026-10-27", "modo": "valor"}))
    est = mapa["estadisticas"]
    print(f"   {mapa['titulo']} | {mapa['subtitulo']} | media {cfg.fmt_num(est['media'])} °C "
          f"({est['celdas_validas']} celdas válidas)")
    # Datos reales: durante El Niño costero de 2026 la SST del área supera los 30 °C en el norte (30.2 °C el 29/10)
    sst_max = 31 if noaa_real else 30
    verificar(19 <= est["minimo"] and est["maximo"] <= sst_max, f"SST del mapa entre 19 y {sst_max} °C"
              f"{' (datos reales)' if noaa_real else ''}")
    if noaa_real:
        verificar(mapa.get("procedencia") == "real"
                  and mapa.get("insignia") == catalogo.conector("noaa_oisst").insignia,
                  f"mapa de NOAA OISST real («{mapa.get('procedencia')}», «{mapa.get('insignia')}»)")
    verificar(mapa["imagen_png"].startswith("data:image/png;base64,"), "imagen_png es un data URI PNG")
    png = base64.b64decode(mapa["imagen_png"].split(",", 1)[1])
    imagen = Image.open(io.BytesIO(png))
    verificar(imagen.format == "PNG" and imagen.mode == "RGBA", f"PNG válido {imagen.size} RGBA")
    verificar(len(mapa["paleta_rgb"]) == 16, "paleta_rgb de 16 pasos")
    verificar(len(mapa["valores"]) == 49 and len(mapa["valores"][0]) == 69, "matriz 49 × 69")

    mapa_anom = cronometrar("Mapa anomalía SST 27/10/2026", lambda: consulta_mapa({
        "fuente": "noaa_oisst", "variable": "sst", "area": {"tipo": "recorte", "recorte": "nino12"},
        "fecha": "2026-10-27", "modo": "anomalia"}))
    print(f"   anomalía media Niño 1+2: {cfg.fmt_anomalia(mapa_anom['estadisticas']['media'], '°C')}")

    # 2. Serie Niño 1+2 de 12 meses con resumen
    serie_r = cronometrar("Serie SST Niño 1+2, 12 meses, mensual", lambda: consulta_serie({
        "fuente": "noaa_oisst", "variable": "sst", "area": {"tipo": "recorte", "recorte": "nino12"},
        "periodo": {"inicio": "2025-11-01", "fin": "2026-10-27"}, "agregacion": "mensual"}))
    for frase in serie_r["resumen"]["texto"]:
        print(f"   · {frase}")
    anom_oct_2026 = serie_r["anomalias"][-1]
    if noaa_real:
        # Observado: El Niño costero de 2026, anomalía de octubre en el recorte 0–6° S, 90–80° O de ~+5.7 °C
        verificar(0.5 <= anom_oct_2026 <= 8.0, f"anomalía 10/2026 real en fase cálida y físicamente plausible "
                  f"(+0.5 a +8 °C; obtenida {cfg.fmt_anomalia(anom_oct_2026, '°C', 2)})")
    else:
        verificar(1.5 <= anom_oct_2026 <= 2.1,
                  f"anomalía 10/2026 ≈ +1.8 °C (obtenida {cfg.fmt_anomalia(anom_oct_2026, '°C', 2)})")

    serie_larga = cronometrar("Serie SST Niño 1+2 completa (3 años, mensual)", lambda: consulta_serie({
        "fuente": "noaa_oisst", "variable": "sst", "area": {"tipo": "recorte", "recorte": "nino12"},
        "periodo": {"inicio": "2023-10-01", "fin": "2026-10-27"}, "agregacion": "mensual"}))
    indice_dic = serie_larga["fechas"].index("2023-12-01")
    anom_dic_2023 = serie_larga["anomalias"][indice_dic]
    if noaa_real:
        verificar(-4.0 <= anom_dic_2023 <= 6.0, f"anomalía 12/2023 real físicamente plausible (−4 a +6 °C; "
                  f"obtenida {cfg.fmt_anomalia(anom_dic_2023, '°C', 2)})")
    else:
        verificar(2.5 <= anom_dic_2023 <= 3.1,
                  f"anomalía 12/2023 ≈ +2.8 °C (obtenida {cfg.fmt_anomalia(anom_dic_2023, '°C', 2)})")
    vals = [v for v in serie_larga["valores"] if v is not None]
    print(f"   SST mensual Niño 1+2: mín {cfg.fmt_num(min(vals))} °C, máx {cfg.fmt_num(max(vals))} °C "
          f"(máximo en {cfg.fmt_mes_anio(serie_larga['fechas'][int(np.argmax(vals))])})")
    verificar(19 <= min(vals) and max(vals) <= sst_max, f"SST mensual Niño 1+2 entre 19 y ~{sst_max} °C")
    if noaa_real:
        verificar(serie_larga.get("procedencia") == "real" and serie_larga.get("datos_simulados") is False,
                  f"serie de 3 años de NOAA OISST real («{serie_larga.get('procedencia')}»)")

    # 2b. Índice Niño 1+2 semanal (real con NOAA OISST real: región oficial 0–10° S, 90–80° O)
    enso = cronometrar("Índice Niño 1+2 semanal, 12 meses", lambda: serie_indice_enso(
        corte_sistema - pd.Timedelta(days=364), corte_sistema))
    for frase in enso["texto"]:
        print(f"   · {frase}")
    con_valor = [v for v in enso["valores"] if v is not None]
    verificar(bool(con_valor) and enso["actual"] is not None, f"índice con {len(con_valor)} semanas con valor")
    if noaa_real:
        verificar(enso["procedencia"] == "real" and enso["datos_simulados"] is False
                  and enso["insignia"] == catalogo.conector("noaa_oisst").insignia,
                  f"índice Niño 1+2 real («{enso['procedencia']}», «{enso['insignia']}»)")
        verificar(48 <= len(enso["fechas"]) <= 54 and all(date.fromisoformat(f).weekday() == 2 for f in enso["fechas"]),
                  f"{len(enso['fechas'])} semanas etiquetadas con el miércoles (como NOAA CPC)")
        verificar(-5.0 <= enso["actual"] <= 8.0, f"índice actual físicamente plausible "
                  f"({cfg.fmt_anomalia(enso['actual'], '°C', 2)})")
    fase_esperada = ("El Niño (fase cálida)" if enso["actual"] >= UMBRAL_ENSO else
                     "La Niña (fase fría)" if enso["actual"] <= -UMBRAL_ENSO else "neutra")
    verificar(enso["fase"] == fase_esperada, f"fase coherente con el índice actual ({enso['fase']})")

    serie_diaria = cronometrar("Serie SST diaria en un punto (2° S, 85° O), 90 días", lambda: consulta_serie({
        "fuente": "noaa_oisst", "variable": "sst", "area": {"tipo": "punto", "lat": -2.0, "lon": -85.0},
        "periodo": {"inicio": "2026-07-30", "fin": "2026-10-27"}, "agregacion": "diaria"}))
    verificar(len(serie_diaria["fechas"]) == 90, "90 valores diarios en el punto")

    # 3. Perfil
    perfil_r = cronometrar("Perfil thetao 85° O, 2° S, 27/10/2026", lambda: consulta_perfil({
        "fuente": "copernicus_phy", "variable": "thetao", "lat": -2.0, "lon": -85.0, "fecha": "2026-10-27"}))
    for frase in perfil_r["texto"]:
        print(f"   · {frase}")
    v = perfil_r["valores"]
    verificar(all(v[i] >= v[i + 1] - 0.05 for i in range(len(v) - 1)), "perfil decreciente con la profundidad")
    t100_min, t100_max = (10, 28) if cop_real else (14, 20)
    verificar(t100_min <= v[-1] <= t100_max, f"temperatura a 100 m creíble ({cfg.fmt_num(v[-1])} °C; "
              f"rango {t100_min}–{t100_max} °C{' para datos reales' if cop_real else ''})")
    if cop_real:
        verificar(perfil_r.get("procedencia") == "real", f"perfil de Copernicus real («{perfil_r.get('procedencia')}»)")
    verificar(perfil_r["capa_mezcla_m"] is not None and 5 <= perfil_r["capa_mezcla_m"] <= 80,
              f"capa de mezcla creíble ({cfg.fmt_num(perfil_r['capa_mezcla_m'], 0)} m)")

    perfil_so = cronometrar("Perfil salinidad 85° O, 2° S", lambda: consulta_perfil({
        "fuente": "copernicus_phy", "variable": "so", "lat": -2.0, "lon": -85.0, "fecha": "2026-10-26"}))
    vs = perfil_so["valores"]
    verificar(32 <= min(vs) and max(vs) <= 35.5, f"salinidad entre 32 y 35.5 PSU ({cfg.fmt_num(min(vs), 2)}–{cfg.fmt_num(max(vs), 2)})")

    # 4. Cortina
    cortina_r = cronometrar("Cortina thetao 85° O, 2° S, 12 meses", lambda: consulta_cortina({
        "fuente": "copernicus_phy", "variable": "thetao", "lat": -2.0, "lon": -85.0,
        "periodo": {"inicio": "2025-10-27", "fin": "2026-10-26"}}))
    verificar(len(cortina_r["valores"]) == 8 and len(cortina_r["valores"][0]) == len(cortina_r["fechas"]),
              f"matriz [8 profundidades][{len(cortina_r['fechas'])} fechas]")

    # 5. Climatología
    clim_r = cronometrar("Climatología SST Niño 1+2 (1991-2020)", lambda: consulta_climatologia({
        "fuente": "noaa_oisst", "variable": "sst", "area": {"tipo": "recorte", "recorte": "nino12"},
        "periodo_referencia": "1991-2020"}))
    print("   " + ", ".join(f"{m} {cfg.fmt_num(v)}" for m, v in zip(clim_r["meses"], clim_r["valores"])))
    verificar(len(clim_r["valores"]) == 12 and all(v is not None for v in clim_r["valores"]), "12 valores")
    verificar(int(np.argmax(clim_r["valores"])) in (1, 2, 3), "máximo climatológico entre febrero y abril")

    # 6. Mismo mes
    mismo = cronometrar("Mismo mes (octubre) SST Niño 1+2", lambda: consulta_mismo_mes({
        "fuente": "noaa_oisst", "variable": "sst", "area": {"tipo": "recorte", "recorte": "nino12"}, "mes": 10}))
    for frase in mismo["texto"]:
        print(f"   · {frase}")
    verificar(mismo["anios"] == [2023, 2024, 2025, 2026], "años 2023–2026 con datos de octubre")

    # 7. Comparación
    comp = cronometrar("Comparar SST NOAA vs. thetao 0 m Copernicus (Niño 1+2, 12 meses)", lambda: consulta_comparar({
        "a": {"fuente": "noaa_oisst", "variable": "sst", "area": {"tipo": "recorte", "recorte": "nino12"},
              "periodo": {"inicio": "2025-10-27", "fin": "2026-10-26"}},
        "b": {"fuente": "copernicus_phy", "variable": "thetao", "area": {"tipo": "recorte", "recorte": "nino12"},
              "periodo": {"inicio": "2025-10-27", "fin": "2026-10-26"}, "profundidad": 0},
        "agregacion": "semanal"}))
    for frase in comp["texto"]:
        print(f"   · {frase}")
    correlacion = comp["diferencia"]["correlacion"]
    if cop_real != noaa_real:
        # Una fuente real frente a otra simulada: la comparación es «mixta» y su correlación no se exige
        verificar(correlacion is not None and comp.get("procedencia") == "mixto",
                  f"comparación NOAA ({'real' if noaa_real else 'simulado'}) frente a Copernicus "
                  f"({'real' if cop_real else 'simulado'}) «mixta» con correlación calculada "
                  f"({cfg.fmt_num(correlacion or 0, 2)})")
    else:
        # Las dos reales (o las dos simuladas): dos estimaciones de la misma SST, la correlación debe ser alta
        procedencia_comp = "real" if noaa_real else "simulado"
        verificar(correlacion is not None and correlacion > 0.8 and comp.get("procedencia") == procedencia_comp,
                  f"correlación alta entre SST NOAA y thetao 0 m ({cfg.fmt_num(correlacion or 0, 2)}; "
                  f"comparación «{comp.get('procedencia')}»)")

    comp_periodos = cronometrar("Comparar dos periodos (feb. 2024 vs. feb. 2026)", lambda: consulta_comparar({
        "a": {"fuente": "noaa_oisst", "variable": "sst", "area": {"tipo": "recorte", "recorte": "nino12"},
              "periodo": {"inicio": "2024-02-01", "fin": "2024-02-29"}},
        "b": {"fuente": "noaa_oisst", "variable": "sst", "area": {"tipo": "recorte", "recorte": "nino12"},
              "periodo": {"inicio": "2026-02-01", "fin": "2026-02-28"}},
        "agregacion": "diaria"}))
    verificar(comp_periodos["alineacion"] == "dia_relativo", "periodos distintos alineados por día relativo")

    # 8. Precipitación Guayas
    lluvia = cronometrar("Serie precipitación Guayas, 12 meses, diaria", lambda: consulta_serie({
        "fuente": "chirps_v3", "variable": "precip", "area": {"tipo": "recorte", "recorte": "guayas"},
        "periodo": {"inicio": "2025-10-21", "fin": "2026-10-27"}, "agregacion": "diaria"}))
    for frase in lluvia["resumen"]["texto"]:
        print(f"   · {frase}")
    vals_lluvia = [x for x in lluvia["valores"] if x is not None]
    corte_lluvia = catalogo.conector("chirps_v3").fecha_corte
    print(f"   procedencia de la precipitación: {lluvia['procedencia']} ({lluvia['insignia']})")
    verificar(0 <= min(vals_lluvia) and max(vals_lluvia) <= 150,
              f"precipitación media del área entre 0 y 150 mm/día (máx. {cfg.fmt_num(max(vals_lluvia))})")
    verificar(lluvia["fecha_corte"] == corte_lluvia.isoformat() and lluvia["fechas"][-1] <= corte_lluvia.isoformat(),
              f"serie CHIRPS acotada a su fecha de corte ({cfg.fmt_fecha(corte_lluvia)})")
    campo_lluvia = sim.generar_campos("precip", pd.date_range("2024-02-01", "2024-03-31"))
    finitos_lluvia = campo_lluvia[np.isfinite(campo_lluvia)]
    print(f"   generador simulado de lluvia, feb.-mar. 2024: máx {cfg.fmt_num(finitos_lluvia.max())} mm/día, "
          f"días secos {cfg.fmt_num((finitos_lluvia == 0).mean() * 100, 0)} %")
    verificar(finitos_lluvia.max() <= 75, "el generador simulado no supera 75 mm/día en ninguna celda")

    periodo_lluvia = {"inicio": (corte_lluvia - pd.Timedelta(days=14)).isoformat(), "fin": corte_lluvia.isoformat()}
    mapa_lluvia = cronometrar("Mapa precipitación acumulada 15 días (cuenca del Guayas)", lambda: consulta_mapa({
        "fuente": "chirps_v3", "variable": "precip", "area": {"tipo": "recorte", "recorte": "cuenca_guayas"},
        "periodo": periodo_lluvia, "modo": "valor"}))
    verificar(mapa_lluvia["estadisticas"]["celdas_validas"] > 0, "mapa de lluvia con celdas de tierra")

    # 9. Vectores y variables restantes
    vec = cronometrar("Vectores de corriente 26/10/2026 (paso 2)", lambda: consulta_vectores({
        "fuente": "copernicus_phy", "variable": "corrientes", "area": {"tipo": "recorte", "recorte": "area_estudio"},
        "fecha": "2026-10-26", "paso": 2}))
    verificar(len(vec["lats"]) == 25 and len(vec["lons"]) == 35, "vectores submuestreados 25 × 35")
    for variable in ("so", "uo", "vo", "zos", "mlotst", "corrientes"):
        r = consulta_mapa({"fuente": "copernicus_phy", "variable": variable,
                           "area": {"tipo": "recorte", "recorte": "area_estudio"}, "fecha": "2026-10-26",
                           "profundidad": 0})
        e = r["estadisticas"]
        print(f"   {variable:10s} mín {cfg.fmt_num(e['minimo'], 3)}  máx {cfg.fmt_num(e['maximo'], 3)}  {r['unidad']}")
    mapa_so = consulta_mapa({"fuente": "copernicus_phy", "variable": "so",
                             "area": {"tipo": "recorte", "recorte": "area_estudio"}, "fecha": "2026-10-26"})
    # Datos reales: las plumas de los ríos (golfo de Guayaquil, costa del Pacífico colombiano) bajan la
    # salinidad superficial costera de 30 PSU; el simulador no las reproduce.
    so_min, so_max = (25.0, 37.0) if cop_real else (32.0, 35.5)
    verificar(so_min <= mapa_so["estadisticas"]["minimo"] and mapa_so["estadisticas"]["maximo"] <= so_max,
              f"salinidad superficial entre {cfg.fmt_num(so_min, 1)} y {cfg.fmt_num(so_max, 1)} PSU "
              f"({cfg.fmt_num(mapa_so['estadisticas']['minimo'], 2)}–{cfg.fmt_num(mapa_so['estadisticas']['maximo'], 2)})")

    # 10. Errores esperados
    # El recorte «Guayas» incluye celdas del golfo, así que SST allí es válido; se prueba
    # con un rectángulo interior (solo tierra) y con otro de mar abierto (sin tierra).
    try:
        consulta_serie({"fuente": "noaa_oisst", "variable": "sst",
                        "area": {"tipo": "bbox", "bbox": [-79.5, -2.5, -78.5, -1.5]},
                        "periodo": {"inicio": "2026-10-01", "fin": "2026-10-27"}})
        verificar(False, "SST en un rectángulo interior debería fallar con 422")
    except ErrorConsulta as e:
        verificar(e.codigo == 422, f"SST en rectángulo interior → 422: {e.detalle}")
    try:
        consulta_serie({"fuente": "chirps_v3", "variable": "precip",
                        "area": {"tipo": "bbox", "bbox": [-90, -5, -85, -3]},
                        "periodo": {"inicio": "2026-10-01", "fin": "2026-10-27"}})
        verificar(False, "precipitación en mar abierto debería fallar con 422")
    except ErrorConsulta as e:
        verificar(e.codigo == 422, f"precipitación en mar abierto → 422: {e.detalle}")
    try:
        consulta_mapa({"fuente": "noaa_oisst", "variable": "sst", "area": {"tipo": "recorte", "recorte": "no_existe"},
                       "fecha": "2026-10-27"})
        verificar(False, "recorte inexistente debería fallar con 404")
    except ErrorConsulta as e:
        verificar(e.codigo == 404, f"recorte inexistente → 404: {e.detalle}")
    try:
        consulta_perfil({"fuente": "copernicus_phy", "variable": "thetao", "lat": -1.0, "lon": -78.5,
                         "fecha": "2026-10-26"})
        verificar(False, "perfil en tierra debería fallar con 422")
    except ErrorConsulta as e:
        verificar(e.codigo == 422, f"perfil en tierra → 422: {e.detalle}")
    sst_guayas = consulta_serie({"fuente": "noaa_oisst", "variable": "sst",
                                 "area": {"tipo": "recorte", "recorte": "guayas"},
                                 "periodo": {"inicio": "2026-10-01", "fin": "2026-10-27"}})
    verificar(sst_guayas["valores"][0] is not None, "SST en «Guayas» usa solo las celdas del golfo")

    # 11. Figuras completas
    figuras = cronometrar("Figuras completas (mapa, serie, perfil, cortina, climatología, barras, índice)", lambda: [
        graficos.figura_indice_enso(enso["fechas"], enso["valores"], "Índice Niño 1+2 semanal",
                                    credito=enso["credito"], insignia=enso["insignia"],
                                    parciales=enso.get("parcial"), preliminares=enso.get("preliminar")),
        graficos.figura_mapa(mapa["lats"], mapa["lons"], mapa["valores"], mapa["titulo"], mapa["subtitulo"],
                             mapa["unidad"], mapa["paleta"], mapa["vmin"], mapa["vmax"], mapa["fuente"]["credito"]),
        graficos.figura_serie(serie_r["fechas"], serie_r["valores"], "Serie SST Niño 1+2", "°C",
                              serie_r["fuente"]["credito"], serie_r["climatologia"], serie_r["anomalias"]),
        graficos.figura_perfil(perfil_r["profundidades"], perfil_r["valores"], "Perfil térmico", "°C",
                               perfil_r["fuente"]["credito"], perfil_r["capa_mezcla_m"]),
        graficos.figura_cortina(cortina_r["fechas"], cortina_r["profundidades"], cortina_r["valores"],
                                "Cortina térmica", "°C", cortina_r["fuente"]["credito"]),
        graficos.figura_climatologia(clim_r["valores"], "Climatología SST Niño 1+2", "°C", clim_r["fuente"]["credito"]),
        graficos.figura_barras(["Esmeraldas", "Manabí", "Guayas"], [12.3, 8.1, 5.4], "Lluvia por provincia",
                               "mm", lluvia["fuente"]["credito"]),
        graficos.figura_comparacion(comp["a"]["fechas"], comp["a"]["valores"], "A", comp["b"]["fechas"],
                                    comp["b"]["valores"], "B", "Comparación", "°C", comp["fuentes"][0]["credito"]),
    ])
    verificar(all(f[:8] == b"\x89PNG\r\n\x1a\n" for f in figuras), f"{len(figuras)} figuras PNG válidas")

    # 12. Caché
    t0 = time.perf_counter()
    consulta_mapa({"fuente": "noaa_oisst", "variable": "sst", "area": {"tipo": "recorte", "recorte": "area_estudio"},
                   "fecha": "2026-10-27", "modo": "valor"})
    print(f"-> Mapa SST repetido (campos en caché): {(time.perf_counter() - t0) * 1000:.0f} ms")
    print(f"   caché de campos: {cache.cache_campos.estadisticas()}")

    print(f"\nTiempo total medido: {sum(tiempos.values()):.2f} s")
    if errores:
        print(f"\nAutodiagnóstico con {len(errores)} falla(s):")
        for e in errores:
            print(f" - {e}")
        return 1
    print("\nAutodiagnóstico correcto: todas las verificaciones pasaron.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_autodiagnostico())
