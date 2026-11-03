"""Rutas del panel personal (sección 7.6, RF-11).

Además de las rutas del contrato (`GET/PUT /panel`, `GET /panel/tipos`,
`POST /panel/widget/datos`), se exponen `POST /panel/restablecer` y
`DELETE /panel`, que la interfaz usa para volver al panel por defecto.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException

from .. import almacen, auth, cache, catalogo
from .. import configuracion as cfg
from .. import motor, trabajos
from ..esquemas import DatosWidget, Panel

router = APIRouter(prefix="/panel", tags=["Panel personal"])

_CAMPO_FUENTE = {"id": "fuente", "etiqueta": "Fuente", "tipo": "fuente", "por_defecto": "noaa_oisst"}
_CAMPO_VARIABLE = {"id": "variable", "etiqueta": "Variable", "tipo": "variable", "por_defecto": "sst"}
_CAMPO_RECORTE = {"id": "recorte", "etiqueta": "Recorte", "tipo": "recorte", "por_defecto": "nino12"}
_CAMPO_PROFUNDIDAD = {"id": "profundidad", "etiqueta": "Profundidad (m, solo Copernicus)", "tipo": "profundidad",
                      "por_defecto": 0}

TIPOS_WIDGET: list[dict] = [
    {"id": "kpi_variable", "nombre": "Indicador de una variable", "icono": "termometro", "ancho": 1,
     "descripcion": "Valor actual de una variable en un recorte, con su anomalía y una frase.",
     "campos": [_CAMPO_FUENTE, _CAMPO_VARIABLE, _CAMPO_RECORTE, _CAMPO_PROFUNDIDAD,
                {"id": "dias", "etiqueta": "Días acumulados (precipitación)", "tipo": "numero", "por_defecto": 7}]},
    {"id": "mini_serie", "nombre": "Serie de 90 días", "icono": "series", "ancho": 2,
     "descripcion": "Serie diaria de los últimos 90 días con su climatología.",
     "campos": [_CAMPO_FUENTE, _CAMPO_VARIABLE, _CAMPO_RECORTE, _CAMPO_PROFUNDIDAD,
                {"id": "dias", "etiqueta": "Días", "tipo": "numero", "por_defecto": 90}]},
    {"id": "mini_mapa", "nombre": "Mini mapa", "icono": "mapa", "ancho": 1,
     "descripcion": "Mapa de la última fecha disponible (valor o anomalía).",
     "campos": [_CAMPO_FUENTE, _CAMPO_VARIABLE, _CAMPO_RECORTE,
                {"id": "modo", "etiqueta": "Modo", "tipo": "opcion", "por_defecto": "anomalia",
                 "opciones": [{"valor": "valor", "etiqueta": "Valor"}, {"valor": "anomalia", "etiqueta": "Anomalía"}]},
                _CAMPO_PROFUNDIDAD]},
    {"id": "estado_fuentes", "nombre": "Estado de fuentes", "icono": "fuentes", "ancho": 1,
     "descripcion": "Semáforo y fecha de corte de cada fuente.", "campos": []},
    {"id": "trabajos_recientes", "nombre": "Trabajos recientes", "icono": "reportes", "ancho": 1,
     "descripcion": "Últimos reportes y exportaciones.",
     "campos": [{"id": "limite", "etiqueta": "Cantidad", "tipo": "numero", "por_defecto": 5}]},
    {"id": "mis_vistas", "nombre": "Mis vistas", "icono": "vistas", "ancho": 1,
     "descripcion": "Accesos rápidos a las vistas guardadas o compartidas.",
     "campos": [{"id": "solo_compartidas", "etiqueta": "Solo las compartidas conmigo", "tipo": "booleano",
                 "por_defecto": False}]},
    {"id": "indice_enso", "nombre": "Índice Niño 1+2", "icono": "olas", "ancho": 2,
     "descripcion": "Anomalía de SST en la región Niño 1+2 con semáforo de fase.",
     "campos": [{"id": "meses", "etiqueta": "Meses", "tipo": "numero", "por_defecto": 12}]},
    {"id": "precipitacion_provincias", "nombre": "Precipitación por provincia", "icono": "lluvia", "ancho": 2,
     "descripcion": "Lluvia acumulada de los últimos días por provincia costera.",
     "campos": [{"id": "dias", "etiqueta": "Días acumulados", "tipo": "numero", "por_defecto": 7}]},
    {"id": "kpi_sistema", "nombre": "Indicadores del sistema", "icono": "sistema", "ancho": 1,
     "descripcion": "Usuarios, consultas de hoy y caché (administración).", "campos": [],
     "requiere_rol": "administrador"},
]
_IDS_TIPOS = {t["id"] for t in TIPOS_WIDGET}


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------
def _normalizar(panel: dict) -> dict:
    widgets = []
    for i, w in enumerate(panel.get("widgets", [])):
        if w.get("tipo") not in _IDS_TIPOS:
            continue
        widgets.append({"id": str(w.get("id") or f"w{i + 1}"), "tipo": w["tipo"], "titulo": w.get("titulo", ""),
                        "config": w.get("config") or {}, "ancho": int(w.get("ancho") or 1)})
    return {"widgets": widgets, "actualizado": panel.get("actualizado")}


@router.get("")
def obtener_panel(usuario: dict = Depends(auth.usuario_actual)):
    """Panel del usuario; si no tiene, se crea el panel por defecto de su rol."""
    panel = almacen.panel_de(usuario["id"])
    if panel is None:
        panel = almacen.guardar_panel(usuario["id"], almacen.panel_por_defecto(usuario["rol"]))
    return _normalizar(panel)


@router.put("")
def guardar_panel(panel: Panel, usuario: dict = Depends(auth.usuario_actual)):
    """Guarda el panel (solo afecta al usuario actual)."""
    datos = _normalizar({"widgets": [w.model_dump() for w in panel.widgets]})
    if len(datos["widgets"]) > 24:
        raise HTTPException(status_code=422, detail="El panel admite hasta 24 widgets.")
    return _normalizar(almacen.guardar_panel(usuario["id"], datos))


@router.post("/restablecer")
def restablecer_panel(usuario: dict = Depends(auth.usuario_actual)):
    """Vuelve al panel por defecto del rol."""
    return _normalizar(almacen.guardar_panel(usuario["id"], almacen.panel_por_defecto(usuario["rol"])))


@router.delete("")
def eliminar_panel(usuario: dict = Depends(auth.usuario_actual)):
    """Borra el panel guardado; la próxima lectura crea el panel por defecto."""
    almacen.eliminar_panel(usuario["id"])
    return {"ok": True}


@router.get("/tipos")
def tipos_widget(usuario: dict = Depends(auth.usuario_actual)):
    """Tipos de widget disponibles con el esquema de su configuración."""
    return [t for t in TIPOS_WIDGET if not t.get("requiere_rol") or t["requiere_rol"] == usuario.get("rol")]


# ---------------------------------------------------------------------------
# Datos de los widgets
# ---------------------------------------------------------------------------
def _entero(valor, por_defecto: int, minimo: int = 1, maximo: int = 400) -> int:
    try:
        return max(minimo, min(maximo, int(valor)))
    except (TypeError, ValueError):
        return por_defecto


def _area(config: dict) -> dict:
    if config.get("area") and isinstance(config["area"], dict):
        return config["area"]
    recorte = str(config.get("recorte") or "nino12")
    if catalogo.recorte(recorte) is None:
        raise motor.ErrorConsulta(f"Recorte desconocido: «{recorte}».", 404)
    return {"tipo": "recorte", "recorte": recorte}


def _consulta_cacheada(nombre: str, funcion, cuerpo: dict) -> dict:
    almacen.contar_consulta()
    return cache.consultar(nombre, cuerpo, lambda: funcion(cuerpo))


def _base(config: dict) -> dict:
    fuente = str(config.get("fuente") or "noaa_oisst")
    variable = str(config.get("variable") or "sst")
    cuerpo = {"fuente": fuente, "variable": variable, "area": _area(config)}
    info = catalogo.variable(fuente, variable)
    if info is not None and info.tiene_profundidad:
        cuerpo["profundidad"] = float(config.get("profundidad") or 0)
    return cuerpo


def _fecha_corte_de(fuente_id: str) -> date:
    """Fecha de corte de la fuente (la suya: CHIRPS, Copernicus y NOAA tienen cada una la suya); la del sistema
    si la fuente no existe."""
    return (catalogo.conector(fuente_id).fecha_corte if catalogo.existe_fuente(fuente_id)
            else catalogo.fecha_corte_sistema())


def _procedencia(r: dict) -> dict:
    """Campos de procedencia de una respuesta del motor (real, simulado o mixto), con sus avisos y el
    periodo de referencia efectivo de la climatología si lo hay."""
    claves = ("datos_simulados", "procedencia", "insignia", "dias_preliminares", "dias_faltantes",
              "periodo_preliminar", "usa_replica", "origen_datos", "origen_climatologia", "avisos",
              "periodo_referencia_efectivo")
    return {k: r[k] for k in claves if k in r}


def _kpi_variable(config: dict) -> dict:
    base = _base(config)
    corte = _fecha_corte_de(base["fuente"])
    es_lluvia = base["fuente"] == "chirps_v3"
    dias = _entero(config.get("dias"), 7, 1, 60) if es_lluvia else 7
    inicio = corte - timedelta(days=dias - 1)
    s = _consulta_cacheada("serie", motor.consulta_serie,
                           dict(base, periodo={"inicio": inicio.isoformat(), "fin": corte.isoformat()},
                                agregacion="diaria"))
    recorte = catalogo.recorte(base["area"].get("recorte", "")) if base["area"].get("tipo") == "recorte" else None
    nombre_area = recorte["nombre"] if recorte else s["area"]["nombre"]
    nombre_var = s["nombre_variable"].split("(")[0].strip()
    if es_lluvia:
        # Lo normal se suma SOLO en los días con valor (si no, la anomalía queda sesgada).
        a = motor.acumulado_y_normal(s["valores"], s["climatologia"])
        acumulado, anomalia = a["acumulado"], a["anomalia"] or 0.0
        sentido = "más" if anomalia >= 0 else "menos"
        n_sd = a["dias_sin_dato"]
        sin_dato = ((" (1 día sin dato no se cuenta" if n_sd == 1 else f" ({n_sd} días sin dato no se cuentan")
                    + " ni en el acumulado ni en lo normal)") if n_sd else ""
        return {"valor": round(acumulado, 1), "unidad": "mm", "anomalia": round(anomalia, 1), "decimales": 0,
                "fecha": s["fechas"][-1], "etiqueta": f"Precipitación acumulada {a['dias']} días · {nombre_area}",
                "texto": [f"Entre el {cfg.fmt_fecha(s['fechas'][0])} y el {cfg.fmt_fecha(s['fechas'][-1])} llovió "
                          f"{cfg.fmt_valor(acumulado, 'mm', 0)} en {nombre_area}, "
                          f"{cfg.fmt_valor(abs(anomalia), 'mm', 0)} {sentido} que lo normal{sin_dato}."],
                "fuente": s["fuente"], "fecha_corte": s["fecha_corte"], "metodo": s["metodo"], **_procedencia(s)}
    ultimo = s["resumen"]["ultimo"] or {}
    # Por clave y no por índice: con un solo dato el resumen omite la frase del máximo y el mínimo
    frase = s["resumen"].get("frase_ultimo") or ""
    return {"valor": ultimo.get("valor"), "unidad": s["unidad"], "anomalia": ultimo.get("anomalia"),
            "decimales": s.get("decimales", 1), "fecha": ultimo.get("fecha"),
            "etiqueta": f"{nombre_var} · {nombre_area}", "texto": [frase] if frase else [],
            "fuente": s["fuente"], "fecha_corte": s["fecha_corte"], "metodo": s["metodo"], **_procedencia(s)}


def _mini_serie(config: dict) -> dict:
    base = _base(config)
    corte = _fecha_corte_de(base["fuente"])
    dias = _entero(config.get("dias"), 90, 7, 400)
    inicio = corte - timedelta(days=dias - 1)
    s = _consulta_cacheada("serie", motor.consulta_serie,
                           dict(base, periodo={"inicio": inicio.isoformat(), "fin": corte.isoformat()},
                                agregacion="diaria"))
    ultimo = s["resumen"]["ultimo"] or {}
    return {"fechas": s["fechas"], "valores": s["valores"], "climatologia": s["climatologia"],
            "anomalias": s["anomalias"], "anomalia": ultimo.get("anomalia"), "unidad": s["unidad"],
            "decimales": s.get("decimales", 1), "nombre_variable": s["nombre_variable"],
            "texto": s["resumen"]["texto"][:1], "fuente": s["fuente"], "fecha_corte": s["fecha_corte"],
            "metodo": s["metodo"], **_procedencia(s)}


def _mini_mapa(config: dict) -> dict:
    base = _base(config)
    corte = _fecha_corte_de(base["fuente"])
    modo = config.get("modo") or "anomalia"
    info = catalogo.variable(base["fuente"], base["variable"])
    if info is not None and info.es_vector:
        modo = "valor"   # la anomalía de las corrientes no está disponible (motor._rechazar_anomalia_vectorial)
    cuerpo = dict(base, fecha=corte.isoformat(), modo=modo)
    if config.get("periodo_referencia"):
        cuerpo["periodo_referencia"] = config["periodo_referencia"]
    m = _consulta_cacheada("mapa", motor.consulta_mapa, cuerpo)
    est = m["estadisticas"]
    texto = (f"Media del área: {cfg.fmt_anomalia(est['media'], m['unidad'], m['variable']['decimales'])}"
             if modo == "anomalia" else f"Media del área: {cfg.fmt_valor(est['media'], m['unidad'], m['variable']['decimales'])}")
    return {"imagen_png": m["imagen_png"], "titulo": m["titulo"], "subtitulo": m["subtitulo"], "vmin": m["vmin"],
            "vmax": m["vmax"], "unidad": m["unidad"], "paleta": m["paleta"], "paleta_rgb": m["paleta_rgb"],
            "bbox": m["bbox"], "fecha": m["fecha"], "modo": modo, "texto": [texto],
            "fuente": m["fuente"], "fecha_corte": m["fecha_corte"], "metodo": m["metodo"], **_procedencia(m)}


def _indice_enso(config: dict) -> dict:
    """Índice Niño 1+2 de los últimos `meses` hasta la fecha de corte del sistema: con NOAA OISST real, semanal
    (semanas de domingo a sábado centradas en el miércoles, como NOAA CPC; `paso_dias` se ignora) y con huecos
    (None) en las semanas sin dato; si no, el simulado señalado."""
    meses = _entero(config.get("meses"), 12, 3, 37)
    corte = catalogo.fecha_corte_sistema()
    inicio = max(cfg.FECHA_INICIO, corte - timedelta(days=int(meses * 30.44)))
    r = motor.serie_indice_enso(inicio, corte, paso_dias=7)
    r["valor"] = r["actual"]
    r["meses"] = meses
    return r


def _precipitacion_provincias(config: dict) -> dict:
    dias = _entero(config.get("dias"), 7, 1, 60)
    corte = _fecha_corte_de("chirps_v3")
    inicio = corte - timedelta(days=dias - 1)
    periodo = {"inicio": inicio.isoformat(), "fin": corte.isoformat()}
    provincias, fuente, metodo, procedencia = [], None, "", {}
    for prov_id in catalogo.PROVINCIAS_COSTERAS:
        prov = catalogo.recorte(prov_id)
        try:
            s = _consulta_cacheada("serie", motor.consulta_serie,
                                   {"fuente": "chirps_v3", "variable": "precip",
                                    "area": {"tipo": "recorte", "recorte": prov_id},
                                    "periodo": periodo, "agregacion": "diaria"})
        except motor.ErrorConsulta:
            continue
        fuente, metodo, procedencia = s["fuente"], s["metodo"], _procedencia(s)
        a = motor.acumulado_y_normal(s["valores"], s["climatologia"])   # normal solo de los días con valor
        acumulado, normal = a["acumulado"], a["normal"] or 0.0
        provincias.append({"id": prov_id, "nombre": prov["nombre"], "valor": round(acumulado, 1),
                           "normal": round(normal, 1), "anomalia": round(acumulado - normal, 1),
                           "dias": a["dias"], "dias_sin_dato": a["dias_sin_dato"]})
    if not provincias:
        return {"error": "No fue posible obtener la precipitación por provincia."}
    mas = max(provincias, key=lambda p: p["valor"])
    return {"provincias": provincias, "etiquetas": [p["nombre"] for p in provincias],
            "valores": [p["valor"] for p in provincias], "anomalias": [p["anomalia"] for p in provincias],
            "unidad": "mm", "dias": dias, "periodo": periodo,
            "texto": [f"La provincia con mayor acumulado en {dias} días fue {mas['nombre']}: "
                      f"{cfg.fmt_valor(mas['valor'], 'mm', 0)} ({cfg.fmt_anomalia(mas['anomalia'], 'mm', 0)} "
                      f"respecto a lo normal)."],
            "fuente": fuente, "fecha_corte": corte.isoformat(), "metodo": metodo, **procedencia}


def _kpi_sistema() -> dict:
    metricas = almacen.metricas_consultas()
    estadisticas = cache.estadisticas()
    return {"usuarios": len(almacen.listar_usuarios()), "usuarios_activos": auth.usuarios_activos(),
            "consultas_hoy": metricas["consultas_hoy"], "consultas_total": metricas["consultas_total"],
            "cache": {"entradas": estadisticas["entradas"], "aciertos": estadisticas["aciertos"],
                      "fallos": estadisticas["fallos"]},
            "trabajos": trabajos.resumen(),
            "texto": [f"{auth.usuarios_activos()} usuario(s) con sesión activa; "
                      f"{metricas['consultas_hoy']} consultas hoy."]}


@router.post("/widget/datos")
def datos_widget(peticion: DatosWidget, usuario: dict = Depends(auth.usuario_actual)):
    """Datos listos para dibujar un widget. Los errores de datos se devuelven como `{error}`."""
    tipo, config = peticion.tipo, peticion.config or {}
    if tipo not in _IDS_TIPOS:
        raise HTTPException(status_code=422, detail=f"Tipo de widget desconocido: «{tipo}». "
                                                    f"Disponibles: {', '.join(sorted(_IDS_TIPOS))}.")
    if tipo == "kpi_sistema" and usuario.get("rol") != "administrador":
        raise HTTPException(status_code=403, detail="Los indicadores del sistema son solo para administradores.")
    try:
        if tipo == "kpi_variable":
            return _kpi_variable(config)
        if tipo == "mini_serie":
            return _mini_serie(config)
        if tipo == "mini_mapa":
            return _mini_mapa(config)
        if tipo == "estado_fuentes":
            return {"fuentes": catalogo.estado_fuentes(), **catalogo.procedencia_catalogo()}
        if tipo == "trabajos_recientes":
            limite = _entero(config.get("limite"), 5, 1, 20)
            return {"trabajos": trabajos.listar(usuario, todos=(usuario.get("rol") == "administrador"),
                                                limite=limite)}
        if tipo == "mis_vistas":
            solo_compartidas = bool(config.get("solo_compartidas"))
            vistas = []
            for v in almacen.listar_vistas():
                propia = str(v.get("propietario_id")) == str(usuario["id"])
                if solo_compartidas:
                    if v.get("compartida") and not propia:
                        vistas.append(almacen.vista_publica(v))
                elif propia or v.get("compartida"):
                    vistas.append(almacen.vista_publica(v))
            vistas.sort(key=lambda v: v.get("actualizada") or "", reverse=True)
            return {"vistas": vistas}
        if tipo == "indice_enso":
            return _indice_enso(config)
        if tipo == "precipitacion_provincias":
            return _precipitacion_provincias(config)
        if tipo == "kpi_sistema":
            return _kpi_sistema()
    except motor.ErrorConsulta as e:
        return {"error": e.detalle}
    raise HTTPException(status_code=422, detail=f"Tipo de widget no implementado: «{tipo}».")
