"""Exportación de consultas del SOME a CSV, PNG y NetCDF (RF-10, RF-18).

`exportar(formato, clase, consulta)` ejecuta la consulta con el motor y
devuelve `(bytes, nombre_archivo, tipo_mime)`:

- CSV estándar con `,` como separador y punto decimal (RNF-01), codificado en
  UTF-8 con BOM, con una cabecera de comentarios (`# Fuente:`, `# Crédito:`,
  `# Crédito de la réplica:` si se usó, `# Licencia:`, `# Procesamiento:` (cfg.AVISO_PROCESAMIENTO),
  `# Método:`, `# Fecha de corte:`, `# Periodo de referencia efectivo:` si la
  climatología usada es de otro periodo, `# Aviso:` si los hay y la insignia de
  procedencia, p. ej. `# Datos reales · NOAA OISST`, `# Datos reales · Copernicus Marine`,
  `# Datos reales: CHIRPS v3 (CHC, UCSB)` o, si una fuente cayó a su simulador,
  `# Datos simulados para demostración`). Sin fecha de corte en el resultado, la del sistema
  (`catalogo.fecha_corte_sistema()`).
- PNG: figura matplotlib completa (título, barra de color, crédito, aviso de procesamiento e insignia al pie).
- NetCDF: `xarray.Dataset.to_netcdf(engine="scipy")` (NetCDF3 clásico) con
  atributos CF, el crédito y la licencia de la fuente (también como ACDD `acknowledgement` y `license`),
  `aviso_procesamiento`, `comment` = insignia de procedencia y, con la réplica, `credito_replica`; y,
  si los hay, `periodo_referencia_efectivo` y `avisos`.

`exportar_con_procedencia` devuelve además la procedencia del resultado
(«real», «simulado» o «mixto») para la cabecera HTTP `X-SOME-Procedencia`.
"""

from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np
import pandas as pd
import xarray as xr

from . import catalogo
from . import configuracion as cfg
from . import graficos, motor

FORMATOS = ("csv", "png", "netcdf")
CLASES = ("mapa", "serie", "perfil", "cortina")
EXTENSIONES = {"csv": "csv", "png": "png", "netcdf": "nc"}
TIPOS_MIME = {"csv": "text/csv; charset=utf-8", "png": "image/png", "netcdf": "application/x-netcdf"}

_CONSULTAS = {
    "mapa": motor.consulta_mapa,
    "serie": motor.consulta_serie,
    "perfil": motor.consulta_perfil,
    "cortina": motor.consulta_cortina,
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _fecha_para_nombre(r: dict) -> str:
    fecha = r.get("fecha")
    if not fecha and isinstance(r.get("periodo"), dict):
        fecha = r["periodo"].get("fin")
    if not fecha and r.get("fechas"):
        fecha = r["fechas"][-1]
    fecha = fecha or r.get("fecha_corte") or catalogo.fecha_corte_sistema().isoformat()
    return str(fecha)[:10].replace("-", "")


def nombre_archivo(r: dict, formato: str, clase: str) -> str:
    """`SOME_<fuente>_<variable>_<fecha>.<ext>` (con sufijo de clase cuando no es un mapa)."""
    fuente = r.get("fuente", {}).get("id", "fuente") if isinstance(r.get("fuente"), dict) else "fuente"
    variable = (r.get("variable") or {}).get("id") if isinstance(r.get("variable"), dict) else None
    variable = variable or r.get("nombre_variable") or "variable"
    partes = ["SOME", fuente, str(variable)]
    if clase != "mapa":
        partes.append(clase)
    elif r.get("modo") == "anomalia":
        partes.append("anomalia")
    partes.append(_fecha_para_nombre(r))
    return "_".join(partes) + "." + EXTENSIONES[formato]


def _num_csv(x, decimales: int) -> str:
    """Número con punto decimal y sin separador de miles; vacío si no hay dato."""
    if x is None:
        return ""
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return str(x)
    if math.isnan(xf) or math.isinf(xf):
        return ""
    return f"{xf:.{decimales}f}"


def _insignia(r: dict) -> str:
    """Insignia de procedencia del resultado (por defecto, la de datos simulados)."""
    return r.get("insignia") or cfg.INSIGNIA_SIMULADOS


def _es_real(r: dict) -> bool:
    return r.get("procedencia") == "real" or (r.get("datos_simulados") is False)


def _procedencia(r: dict) -> str:
    """«real», «simulado» o «mixto» del resultado."""
    p = r.get("procedencia")
    return p if p in ("real", "simulado", "mixto") else ("real" if _es_real(r) else "simulado")


def _periodo_referencia_texto(r: dict) -> str:
    """«1991-2020» o, si la climatología usada es de otro periodo, «1993-2016 (se pidió 1991-2020)»."""
    return motor.texto_periodo_referencia(r.get("periodo_referencia"), r.get("periodo_referencia_efectivo"))


def _avisos(r: dict) -> list[str]:
    """Avisos del resultado (respaldo simulado, datos parciales), sin repetir."""
    salida: list[str] = []
    for a in list(r.get("avisos") or []) + [(r.get("fuente") or {}).get("aviso")]:
        texto = str(a or "").strip()
        if texto and not any(texto in s or s in texto for s in salida):
            salida.append(texto)
    return salida


def _cabecera_csv(r: dict, clase: str) -> list[str]:
    fuente = r.get("fuente", {})
    variable = r.get("variable") or {}
    nombre_var = variable.get("nombre") or r.get("nombre_variable") or ""
    lineas = [
        f"# {cfg.NOMBRE_SISTEMA} — {cfg.NOMBRE_COMPLETO} · exportación de {clase}",
        f"# Fuente: {fuente.get('nombre', '')} ({fuente.get('identificador', '')})",
        f"# Crédito: {fuente.get('credito', '')}",
        f"# Licencia: {fuente.get('licencia', '')}",
        f"# Procesamiento: {cfg.AVISO_PROCESAMIENTO}",
        f"# Variable: {nombre_var} ({r.get('unidad', '')})",
    ]
    if fuente.get("credito_replica"):
        lineas.insert(3, f"# Crédito de la réplica: {fuente['credito_replica']}")
    area = r.get("area") or {}
    if area.get("nombre"):
        lineas.append(f"# Área: {area['nombre']}")
    if r.get("profundidad") is not None:
        lineas.append(f"# Profundidad: {cfg.fmt_num(r['profundidad'], 0)} m")
    if r.get("periodo_referencia"):
        lineas.append(f"# Periodo de referencia: {r['periodo_referencia']}")
    if r.get("periodo_referencia_efectivo"):
        lineas.append(f"# Periodo de referencia efectivo: {_periodo_referencia_texto(r)}")
    lineas.append(f"# Método: {r.get('metodo', '')}")
    lineas.append(f"# Fecha de corte: {cfg.fmt_fecha(r.get('fecha_corte') or catalogo.fecha_corte_sistema())}")
    lineas.append(f"# Generado: {cfg.fmt_fecha_hora(datetime.now())}")
    if fuente.get("origen_datos"):
        lineas.append(f"# Origen de los días: {fuente['origen_datos']}")
    if fuente.get("origen_climatologia"):
        lineas.append(f"# {fuente['origen_climatologia']}")
    if r.get("dias_preliminares"):
        n = int(r["dias_preliminares"])
        lineas.append(f"# Días preliminares: {n} (datos preliminares de la fuente; se reemplazarán por la versión final)")
    for aviso in _avisos(r):
        lineas.append(f"# Aviso: {aviso}")
    lineas.append(f"# {_insignia(r)}")
    return lineas


def _decimales(r: dict, extra: int = 1) -> int:
    base = r.get("decimales")
    if base is None:
        base = (r.get("variable") or {}).get("decimales", 1)
    return int(base) + extra


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def a_csv(r: dict, clase: str) -> bytes:
    dec = _decimales(r)
    filas = _cabecera_csv(r, clase)
    if clase == "mapa":
        filas.append("latitud,longitud,valor")
        for lat, fila in zip(r["lats"], r["valores"]):
            for lon, valor in zip(r["lons"], fila):
                filas.append(f"{_num_csv(lat, 3)},{_num_csv(lon, 3)},{_num_csv(valor, dec)}")
    elif clase == "serie":
        filas.append("fecha,valor,climatologia,anomalia")
        clim = r.get("climatologia") or [None] * len(r["fechas"])
        anom = r.get("anomalias") or [None] * len(r["fechas"])
        for fecha, valor, c, a in zip(r["fechas"], r["valores"], clim, anom):
            filas.append(f"{cfg.fmt_fecha(fecha)},{_num_csv(valor, dec)},{_num_csv(c, dec)},{_num_csv(a, dec)}")
    elif clase == "perfil":
        filas.append("profundidad_m,valor")
        for z, valor in zip(r["profundidades"], r["valores"]):
            filas.append(f"{_num_csv(z, 0)},{_num_csv(valor, dec)}")
        if r.get("capa_mezcla_m") is not None:
            filas.append(f"# Capa de mezcla (m): {_num_csv(r['capa_mezcla_m'], 1)}")
    elif clase == "cortina":
        filas.append("fecha,profundidad_m,valor")
        for iz, z in enumerate(r["profundidades"]):
            for it, fecha in enumerate(r["fechas"]):
                filas.append(f"{cfg.fmt_fecha(fecha)},{_num_csv(z, 0)},{_num_csv(r['valores'][iz][it], dec)}")
    return ("\r\n".join(filas) + "\r\n").encode("utf-8-sig")


# ---------------------------------------------------------------------------
# PNG
# ---------------------------------------------------------------------------
def a_png(r: dict, clase: str) -> bytes:
    credito = r["fuente"]["credito"]
    metodo = r.get("metodo")
    dec = int(r.get("decimales") or 1)
    # Crédito completo en el pie (RF-12): crédito, identificador y fecha de corte de la fuente,
    # con la insignia de procedencia del resultado (real, simulado o mixto)
    pie = {"identificador": r["fuente"].get("identificador"), "fecha_corte": r.get("fecha_corte"),
           "insignia": _insignia(r)}
    if clase == "mapa":
        return graficos.figura_mapa(r["lats"], r["lons"], r["valores"], r["titulo"], r["subtitulo"],
                                    r["unidad"], r["paleta"], r["vmin"], r["vmax"], credito,
                                    es_tierra=(r["fuente"]["id"] == "chirps_v3"), metodo=metodo, **pie)
    if clase == "serie":
        area = (r.get("area") or {}).get("nombre", "")
        titulo = f"{r.get('nombre_variable', '')} · {r['fuente']['nombre']}"
        subtitulo = (f"{area} · {cfg.fmt_fecha(r['periodo']['inicio'])} – {cfg.fmt_fecha(r['periodo']['fin'])}"
                     f" · agregación {r.get('agregacion', 'diaria')} · referencia {_periodo_referencia_texto(r)}")
        return graficos.figura_serie(r["fechas"], r["valores"], titulo, r["unidad"], credito,
                                     r.get("climatologia"), r.get("anomalias"), etiqueta=r.get("nombre_variable", "Valor"),
                                     subtitulo=subtitulo, decimales=dec, metodo=metodo, **pie)
    if clase == "perfil":
        titulo = f"Perfil de {r.get('nombre_variable', '').lower()} · {r['fuente']['nombre_corto'] if 'nombre_corto' in r['fuente'] else r['fuente']['nombre']}"
        subtitulo = f"{cfg.fmt_coordenada(r['lat'], r['lon'])} · {cfg.fmt_fecha(r['fecha'])}"
        return graficos.figura_perfil(r["profundidades"], r["valores"], titulo, r["unidad"], credito,
                                      r.get("capa_mezcla_m"), subtitulo=subtitulo, decimales=dec, metodo=metodo, **pie)
    if clase == "cortina":
        titulo = f"Cortina profundidad-tiempo de {r.get('nombre_variable', '').lower()} · {r['fuente']['nombre']}"
        subtitulo = (f"{cfg.fmt_coordenada(r['lat'], r['lon'])} · {cfg.fmt_fecha(r['fechas'][0])} – "
                     f"{cfg.fmt_fecha(r['fechas'][-1])}")
        return graficos.figura_cortina(r["fechas"], r["profundidades"], r["valores"], titulo, r["unidad"],
                                       credito, paleta=r.get("paleta", "viridis"), subtitulo=subtitulo, metodo=metodo,
                                       **pie)
    raise motor.ErrorConsulta(f"Clase de exportación desconocida: «{clase}».", 400)


# ---------------------------------------------------------------------------
# NetCDF (CF, NetCDF3 clásico vía scipy)
# ---------------------------------------------------------------------------
_ATRIBUTOS_LAT = {"standard_name": "latitude", "long_name": "Latitud", "units": "degrees_north", "axis": "Y"}
_ATRIBUTOS_LON = {"standard_name": "longitude", "long_name": "Longitud", "units": "degrees_east", "axis": "X"}
_ATRIBUTOS_DEPTH = {"standard_name": "depth", "long_name": "Profundidad", "units": "m", "positive": "down", "axis": "Z"}
_ATRIBUTOS_TIME = {"standard_name": "time", "long_name": "Tiempo", "axis": "T"}


def _atributos_variable(r: dict, anomalia: bool = False) -> tuple[str, dict]:
    fuente_id = r["fuente"]["id"]
    variable_id = (r.get("variable") or {}).get("id") or r.get("nombre_variable") or "valor"
    info = catalogo.variable(fuente_id, str(variable_id))
    nombre = variable_id if not anomalia else f"{variable_id}_anomalia"
    atributos = {
        "long_name": (info.long_name if info else str(variable_id)),
        "units": (info.unidades_cf if info else r.get("unidad", "")),
        "unidad_mostrada": r.get("unidad", ""),
        "comment": _insignia(r),
    }
    if info and not anomalia:
        atributos["standard_name"] = info.standard_name
    if anomalia:
        atributos["long_name"] = f"Anomalía de {atributos['long_name'].lower()}"
        atributos["cell_methods"] = f"time: mean (anomalía respecto a la climatología {_periodo_referencia_texto(r)})"
    return str(nombre), atributos


def _via_datos_reales(conector, info: dict) -> str:
    """Cómo se obtuvieron los datos reales de la fuente (para el atributo `source`)."""
    if conector.id == "copernicus_phy":
        return ("análisis diario (nunca el pronóstico) leído del Copernicus Marine Data Store (ARCO) con el "
                "Copernicus Marine Toolbox y guardado en la caché local del SOME")
    if conector.id == "noaa_oisst":
        return ("SST diaria leída del servicio NCSS del servidor THREDDS de NOAA PSL (respaldo: archivos diarios de "
                "NOAA NCEI) y guardada en la caché local del SOME; los últimos ~15 días son preliminares")
    if info.get("usa_replica"):
        return ("datos reales obtenidos del servidor del CHC o de su réplica pública en dynamical.org "
                "(Zarr/Icechunk, CC BY 4.0, con los valores redondeados a 8 bits de mantisa)")
    return "datos reales descargados del servidor de la fuente"


def _atributos_globales(r: dict, clase: str) -> dict:
    conector = catalogo.conector(r["fuente"]["id"])
    titulo = r.get("titulo") or f"{r.get('nombre_variable', '')} — {conector.nombre}"
    real = _es_real(r)
    procedencia = _procedencia(r)
    info = r.get("fuente") or {}
    if procedencia == "real":
        fuente_texto = (f"{conector.producto} ({_via_datos_reales(conector, info)}; llevado a la malla de 0.25° "
                        "del prototipo SOME)")
    elif procedencia == "mixto":
        fuente_texto = (f"{conector.producto} (combina datos reales y simulados: ver los atributos «procedencia», "
                        "«avisos» y «metodo»)")
    else:
        fuente_texto = f"{conector.producto} (simulación analítica del prototipo SOME)"
    atributos = {
        "Conventions": "CF-1.8",
        "title": f"{titulo} (exportación de {clase} del SOME)",
        "source": fuente_texto,
        "institution": conector.proveedor,
        "references": conector.referencias,
        "history": f"{datetime.now().isoformat(timespec='seconds')} SOME {cfg.VERSION}: exportado desde /api/exportar",
        "comment": _insignia(r),
        "procedencia": procedencia,
        "datos_reales": int(real),
        "credito": info.get("credito") or conector.credito,
        "identificador": conector.identificador,
        "licencia": conector.licencia,
        # Atributos ACDD que leen otras herramientas (los de arriba, en español, son los del SOME)
        "license": conector.licencia,
        "acknowledgement": info.get("credito") or conector.credito,
        "aviso_procesamiento": cfg.AVISO_PROCESAMIENTO,
        "fecha_corte": r.get("fecha_corte") or catalogo.fecha_corte_sistema().isoformat(),
        "metodo": r.get("metodo", ""),
    }
    for clave in ("origen_datos", "origen_climatologia", "credito_replica"):
        if info.get(clave):
            atributos[clave] = str(info[clave])
    if r.get("dias_preliminares") is not None:
        atributos["dias_preliminares"] = int(r.get("dias_preliminares") or 0)
        atributos["dias_faltantes"] = int(r.get("dias_faltantes") or 0)
    area = r.get("area") or {}
    if area.get("nombre"):
        atributos["area"] = area["nombre"]
    if r.get("periodo_referencia"):
        atributos["periodo_referencia"] = r["periodo_referencia"]
    if r.get("periodo_referencia_efectivo"):
        atributos["periodo_referencia_efectivo"] = str(r["periodo_referencia_efectivo"])
    avisos = _avisos(r)
    if avisos:
        atributos["avisos"] = " | ".join(avisos)       # NetCDF3: un atributo de texto, no una lista
    if r.get("profundidad") is not None:
        atributos["profundidad_m"] = float(r["profundidad"])
    return atributos


def _matriz(valores) -> np.ndarray:
    return np.array([[np.nan if v is None else v for v in fila] for fila in valores], dtype=np.float32)


def _vector(valores) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in valores], dtype=np.float32)


def _tiempos(fechas) -> np.ndarray:
    return pd.to_datetime(list(fechas)).values.astype("datetime64[ns]")


def a_netcdf(r: dict, clase: str) -> bytes:
    nombre, atributos = _atributos_variable(r, anomalia=(r.get("modo") == "anomalia"))
    codificacion = {}
    if clase == "mapa":
        ds = xr.Dataset(
            {nombre: (("lat", "lon"), _matriz(r["valores"]), atributos)},
            coords={"lat": ("lat", np.asarray(r["lats"], dtype=np.float64), _ATRIBUTOS_LAT),
                    "lon": ("lon", np.asarray(r["lons"], dtype=np.float64), _ATRIBUTOS_LON)},
        )
        if r.get("fecha"):
            ds.attrs["fecha"] = r["fecha"]
        elif isinstance(r.get("periodo"), dict):
            ds.attrs["periodo_inicio"] = r["periodo"]["inicio"]
            ds.attrs["periodo_fin"] = r["periodo"]["fin"]
    elif clase == "serie":
        tiempos = _tiempos(r["fechas"])
        atributos_clim = dict(atributos, long_name=f"Climatología mensual de {atributos['long_name'].lower()}",
                              cell_methods="time: mean within months")
        atributos_clim.pop("standard_name", None)
        atributos_anom = dict(atributos, long_name=f"Anomalía de {atributos['long_name'].lower()}")
        atributos_anom.pop("standard_name", None)
        ds = xr.Dataset(
            {nombre: (("time",), _vector(r["valores"]), atributos),
             "climatologia": (("time",), _vector(r.get("climatologia") or [None] * len(tiempos)), atributos_clim),
             "anomalia": (("time",), _vector(r.get("anomalias") or [None] * len(tiempos)), atributos_anom)},
            coords={"time": ("time", tiempos, _ATRIBUTOS_TIME)},
        )
        ds.attrs["agregacion"] = r.get("agregacion", "diaria")
        codificacion["time"] = {"units": "days since 1970-01-01 00:00:00", "calendar": "standard", "dtype": "float64"}
    elif clase == "perfil":
        ds = xr.Dataset(
            {nombre: (("depth",), _vector(r["valores"]), atributos)},
            coords={"depth": ("depth", np.asarray(r["profundidades"], dtype=np.float64), _ATRIBUTOS_DEPTH)},
        )
        ds.attrs.update({"fecha": r.get("fecha", ""), "latitud": float(r["lat"]), "longitud": float(r["lon"])})
        if r.get("capa_mezcla_m") is not None:
            ds.attrs["capa_mezcla_m"] = float(r["capa_mezcla_m"])
    elif clase == "cortina":
        tiempos = _tiempos(r["fechas"])
        ds = xr.Dataset(
            {nombre: (("depth", "time"), _matriz(r["valores"]), atributos)},
            coords={"depth": ("depth", np.asarray(r["profundidades"], dtype=np.float64), _ATRIBUTOS_DEPTH),
                    "time": ("time", tiempos, _ATRIBUTOS_TIME)},
        )
        ds.attrs.update({"latitud": float(r["lat"]), "longitud": float(r["lon"])})
        codificacion["time"] = {"units": "days since 1970-01-01 00:00:00", "calendar": "standard", "dtype": "float64"}
    else:
        raise motor.ErrorConsulta(f"Clase de exportación desconocida: «{clase}».", 400)

    ds.attrs.update(_atributos_globales(r, clase))
    contenido = ds.to_netcdf(engine="scipy", format="NETCDF3_CLASSIC", encoding=codificacion)
    return bytes(contenido)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------
def exportar(formato: str, clase: str, consulta: dict) -> tuple[bytes, str, str]:
    """Ejecuta la consulta y la serializa. Devuelve (bytes, nombre_archivo, tipo_mime)."""
    contenido, nombre, tipo_mime, _ = exportar_con_procedencia(formato, clase, consulta)
    return contenido, nombre, tipo_mime


def exportar_con_procedencia(formato: str, clase: str, consulta: dict) -> tuple[bytes, str, str, dict]:
    """Como `exportar`, más {"datos_simulados", "procedencia", "insignia"} del resultado."""
    if formato not in FORMATOS:
        raise motor.ErrorConsulta(f"Formato desconocido: «{formato}». Use csv, png o netcdf.", 400)
    if clase not in CLASES:
        raise motor.ErrorConsulta(f"Clase desconocida: «{clase}». Use mapa, serie, perfil o cortina.", 400)
    if not isinstance(consulta, dict):
        raise motor.ErrorConsulta("El campo «consulta» debe ser el cuerpo de la consulta a exportar.", 400)
    r = _CONSULTAS[clase](consulta)
    if formato == "csv":
        contenido = a_csv(r, clase)
    elif formato == "png":
        contenido = a_png(r, clase)
    else:
        contenido = a_netcdf(r, clase)
    procedencia = {"datos_simulados": not _es_real(r),
                   "procedencia": r.get("procedencia") or ("real" if _es_real(r) else "simulado"),
                   "insignia": _insignia(r)}
    return contenido, nombre_archivo(r, formato, clase), TIPOS_MIME[formato], procedencia
