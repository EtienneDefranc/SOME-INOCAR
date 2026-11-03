"""Persistencia JSON del prototipo SOME (usuarios, paneles, vistas, auditoría, trabajos).

Todo se guarda en archivos JSON dentro de `datos/` (o de la carpeta indicada
en la variable de entorno `SOME_RUTA_DATOS`, que usan las pruebas). Un bloqueo
reentrante protege las escrituras, que se hacen de forma atómica (archivo
temporal + reemplazo). En el producto real esta capa se reemplaza por
PostgreSQL/PostGIS sin tocar las rutas de la API.

`inicializar()` crea la carpeta, los cuatro usuarios de demostración, un panel
por defecto por usuario, las tres vistas de ejemplo y unas veinte entradas de
auditoría (sección 7.9 de la especificación). Devuelve `True` la primera vez,
para que `app.py` encole los dos trabajos iniciales.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import configuracion as cfg

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
INICIO_SERVIDOR = datetime.now()

COLECCIONES = ("usuarios", "paneles", "vistas", "auditoria", "trabajos", "catalogo", "contadores")
_LISTAS = {"usuarios", "vistas", "auditoria", "trabajos"}
MAXIMO_AUDITORIA = 5000

_BLOQUEO = threading.RLock()
_MEMORIA: dict[str, Any] = {}


def ruta_datos() -> Path:
    """Carpeta de datos: `SOME_RUTA_DATOS` si está definida; si no, `datos/`."""
    personalizada = os.environ.get("SOME_RUTA_DATOS")
    return Path(personalizada).resolve() if personalizada else cfg.RUTA_DATOS


def ruta_reportes() -> Path:
    return ruta_datos() / "reportes"


def _archivo(coleccion: str) -> Path:
    if coleccion not in COLECCIONES:
        raise KeyError(f"Colección desconocida: «{coleccion}».")
    return ruta_datos() / f"{coleccion}.json"


def ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Lectura y escritura genéricas
# ---------------------------------------------------------------------------
def cargar(coleccion: str):
    """Devuelve la colección (lista o diccionario) desde memoria o desde el disco."""
    with _BLOQUEO:
        if coleccion in _MEMORIA:
            return _MEMORIA[coleccion]
        archivo = _archivo(coleccion)
        por_defecto: Any = [] if coleccion in _LISTAS else {}
        if archivo.exists():
            try:
                with archivo.open("r", encoding="utf-8") as f:
                    datos = json.load(f)
            except (OSError, json.JSONDecodeError):
                datos = por_defecto
        else:
            datos = por_defecto
        _MEMORIA[coleccion] = datos
        return datos


def guardar(coleccion: str, datos) -> None:
    """Guarda la colección en memoria y en el disco (escritura atómica, UTF-8)."""
    with _BLOQUEO:
        _MEMORIA[coleccion] = datos
        archivo = _archivo(coleccion)
        archivo.parent.mkdir(parents=True, exist_ok=True)
        temporal = archivo.with_suffix(".json.tmp")
        with temporal.open("w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=1, default=str)
        os.replace(temporal, archivo)


def siguiente_id(coleccion: str) -> int:
    """Identificador entero consecutivo por colección (persistido en contadores.json)."""
    with _BLOQUEO:
        contadores = cargar("contadores")
        valor = int(contadores.get(coleccion, 0)) + 1
        contadores[coleccion] = valor
        guardar("contadores", contadores)
        return valor


def reiniciar_contador(coleccion: str) -> None:
    """Vuelve a empezar la numeración de una colección (p. ej., al regenerar los trabajos de ejemplo)."""
    with _BLOQUEO:
        contadores = cargar("contadores")
        contadores[coleccion] = 0
        guardar("contadores", contadores)


def olvidar_memoria() -> None:
    """Vacía la copia en memoria (útil en pruebas)."""
    with _BLOQUEO:
        _MEMORIA.clear()


# ---------------------------------------------------------------------------
# Contraseñas (demo: sha256 + sal)
# ---------------------------------------------------------------------------
def nueva_sal() -> str:
    return secrets.token_hex(8)


def hash_clave(clave: str, sal: str) -> str:
    return hashlib.sha256((sal + clave).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
CAMPOS_PUBLICOS_USUARIO = ("id", "usuario", "nombre", "rol", "departamento", "correo", "activo", "creado")


def publico(usuario: dict | None) -> dict | None:
    """Copia del usuario sin la sal ni el hash de la clave."""
    if usuario is None:
        return None
    return {k: usuario.get(k) for k in CAMPOS_PUBLICOS_USUARIO}


def listar_usuarios() -> list[dict]:
    return list(cargar("usuarios"))


def usuario_por_id(usuario_id) -> dict | None:
    for u in cargar("usuarios"):
        if str(u["id"]) == str(usuario_id):
            return u
    return None


def usuario_por_nombre(nombre_usuario: str) -> dict | None:
    nombre_usuario = (nombre_usuario or "").strip().lower()
    for u in cargar("usuarios"):
        if u["usuario"].lower() == nombre_usuario:
            return u
    return None


def crear_usuario(datos: dict, clave: str) -> dict:
    with _BLOQUEO:
        usuarios = cargar("usuarios")
        sal = nueva_sal()
        nuevo = {
            "id": siguiente_id("usuarios"),
            "usuario": datos["usuario"].strip(),
            "nombre": datos.get("nombre", "").strip(),
            "rol": datos.get("rol", "oceanografo"),
            "departamento": datos.get("departamento", ""),
            "correo": datos.get("correo", ""),
            "activo": bool(datos.get("activo", True)),
            "creado": ahora_iso(),
            "sal": sal,
            "clave_hash": hash_clave(clave, sal),
        }
        usuarios.append(nuevo)
        guardar("usuarios", usuarios)
        return nuevo


def actualizar_usuario(usuario_id, datos: dict, clave: str | None = None) -> dict | None:
    with _BLOQUEO:
        usuarios = cargar("usuarios")
        for u in usuarios:
            if str(u["id"]) == str(usuario_id):
                for campo in ("usuario", "nombre", "rol", "departamento", "correo"):
                    if datos.get(campo) is not None:
                        u[campo] = str(datos[campo]).strip()
                if datos.get("activo") is not None:
                    u["activo"] = bool(datos["activo"])
                if clave:
                    u["sal"] = nueva_sal()
                    u["clave_hash"] = hash_clave(clave, u["sal"])
                guardar("usuarios", usuarios)
                return u
        return None


def eliminar_usuario(usuario_id) -> bool:
    with _BLOQUEO:
        usuarios = cargar("usuarios")
        restantes = [u for u in usuarios if str(u["id"]) != str(usuario_id)]
        if len(restantes) == len(usuarios):
            return False
        guardar("usuarios", restantes)
        paneles = cargar("paneles")
        if str(usuario_id) in paneles:
            del paneles[str(usuario_id)]
            guardar("paneles", paneles)
        vistas = [v for v in cargar("vistas") if str(v.get("propietario_id")) != str(usuario_id)]
        guardar("vistas", vistas)
        return True


# ---------------------------------------------------------------------------
# Paneles (RF-11)
# ---------------------------------------------------------------------------
def _widget(id_: str, tipo: str, titulo: str, config: dict | None = None, ancho: int = 1) -> dict:
    return {"id": id_, "tipo": tipo, "titulo": titulo, "config": config or {}, "ancho": ancho}


def panel_por_defecto(rol: str) -> dict:
    """Panel inicial según el rol (sección 7.6)."""
    if rol == "administrador":
        widgets = [
            _widget("w1", "estado_fuentes", "Estado de las fuentes", {}, 1),
            _widget("w2", "trabajos_recientes", "Trabajos recientes", {"limite": 6}, 1),
            _widget("w3", "kpi_sistema", "Indicadores del sistema", {}, 1),
        ]
    elif rol == "revisor":
        widgets = [
            _widget("w1", "indice_enso", "Índice Niño 1+2", {"meses": 12}, 2),
            _widget("w2", "mini_mapa", "Anomalía de SST (última fecha)",
                    {"fuente": "noaa_oisst", "variable": "sst", "recorte": "area_estudio", "modo": "anomalia"}, 1),
            _widget("w3", "precipitacion_provincias", "Precipitación por provincia (7 días)", {"dias": 7}, 2),
            _widget("w4", "trabajos_recientes", "Trabajos recientes", {"limite": 5}, 1),
            _widget("w5", "mis_vistas", "Vistas compartidas conmigo", {"solo_compartidas": True}, 3),
        ]
    else:  # oceanógrafo y analista
        widgets = [
            _widget("w1", "kpi_variable", "SST Niño 1+2",
                    {"fuente": "noaa_oisst", "variable": "sst", "recorte": "nino12", "dias": 7}, 1),
            _widget("w2", "kpi_variable", "Precipitación Guayas (7 días)",
                    {"fuente": "chirps_v3", "variable": "precip", "recorte": "guayas", "dias": 7}, 1),
            _widget("w3", "kpi_variable", "Capa de mezcla, golfo de Guayaquil",
                    {"fuente": "copernicus_phy", "variable": "mlotst", "recorte": "golfo_guayaquil", "dias": 7}, 1),
            _widget("w4", "indice_enso", "Índice Niño 1+2", {"meses": 12}, 2),
            _widget("w5", "mini_mapa", "Anomalía de SST (última fecha)",
                    {"fuente": "noaa_oisst", "variable": "sst", "recorte": "area_estudio", "modo": "anomalia"}, 1),
            _widget("w6", "mini_serie", "SST Niño 1+2 — últimos 90 días",
                    {"fuente": "noaa_oisst", "variable": "sst", "recorte": "nino12", "dias": 90}, 2),
            _widget("w7", "estado_fuentes", "Estado de las fuentes", {}, 1),
            _widget("w8", "trabajos_recientes", "Trabajos recientes", {"limite": 5}, 3),
        ]
    return {"widgets": widgets, "actualizado": ahora_iso()}


def panel_de(usuario_id) -> dict | None:
    return cargar("paneles").get(str(usuario_id))


def guardar_panel(usuario_id, panel: dict) -> dict:
    with _BLOQUEO:
        paneles = cargar("paneles")
        panel = {"widgets": list(panel.get("widgets", [])), "actualizado": ahora_iso()}
        paneles[str(usuario_id)] = panel
        guardar("paneles", paneles)
        return panel


def eliminar_panel(usuario_id) -> None:
    with _BLOQUEO:
        paneles = cargar("paneles")
        if str(usuario_id) in paneles:
            del paneles[str(usuario_id)]
            guardar("paneles", paneles)


# ---------------------------------------------------------------------------
# Vistas (RF-16)
# ---------------------------------------------------------------------------
def listar_vistas() -> list[dict]:
    return list(cargar("vistas"))


def vista(vista_id) -> dict | None:
    for v in cargar("vistas"):
        if str(v["id"]) == str(vista_id):
            return v
    return None


def crear_vista(propietario: dict, datos: dict, creada: str | None = None) -> dict:
    with _BLOQUEO:
        vistas = cargar("vistas")
        momento = creada or ahora_iso()
        nueva = {
            "id": siguiente_id("vistas"),
            "nombre": datos.get("nombre", "").strip() or "Vista sin nombre",
            "descripcion": datos.get("descripcion", "") or "",
            "modulo": datos.get("modulo", "mapa"),
            "parametros": datos.get("parametros") or {},
            "compartida": bool(datos.get("compartida", False)),
            "propietario_id": propietario["id"],
            "propietario_usuario": propietario["usuario"],
            "propietario_nombre": propietario.get("nombre", propietario["usuario"]),
            "creada": momento,
            "actualizada": momento,
        }
        vistas.append(nueva)
        guardar("vistas", vistas)
        return nueva


def actualizar_vista(vista_id, datos: dict) -> dict | None:
    with _BLOQUEO:
        vistas = cargar("vistas")
        for v in vistas:
            if str(v["id"]) == str(vista_id):
                if datos.get("nombre"):
                    v["nombre"] = str(datos["nombre"]).strip()
                if datos.get("descripcion") is not None:
                    v["descripcion"] = str(datos["descripcion"])
                if datos.get("modulo"):
                    v["modulo"] = datos["modulo"]
                if datos.get("parametros") is not None:
                    v["parametros"] = datos["parametros"]
                if datos.get("compartida") is not None:
                    v["compartida"] = bool(datos["compartida"])
                v["actualizada"] = ahora_iso()
                guardar("vistas", vistas)
                return v
        return None


def eliminar_vista(vista_id) -> bool:
    with _BLOQUEO:
        vistas = cargar("vistas")
        restantes = [v for v in vistas if str(v["id"]) != str(vista_id)]
        if len(restantes) == len(vistas):
            return False
        guardar("vistas", restantes)
        return True


def vista_publica(v: dict) -> dict:
    """Formato de la API (sección 7.7)."""
    return {
        "id": v["id"],
        "nombre": v["nombre"],
        "descripcion": v.get("descripcion", ""),
        "modulo": v.get("modulo", "mapa"),
        "parametros": v.get("parametros") or {},
        "compartida": bool(v.get("compartida")),
        "propietario": {"usuario": v.get("propietario_usuario"), "nombre": v.get("propietario_nombre")},
        "creada": v.get("creada"),
        "actualizada": v.get("actualizada"),
    }


# ---------------------------------------------------------------------------
# Auditoría (RNF-05)
# ---------------------------------------------------------------------------
def registrar_auditoria(usuario: str, accion: str, detalle: str = "", ip: str = "",
                        fecha: str | None = None) -> dict:
    with _BLOQUEO:
        auditoria = cargar("auditoria")
        entrada = {"fecha": fecha or ahora_iso(), "usuario": usuario, "accion": accion,
                   "detalle": detalle, "ip": ip or "127.0.0.1"}
        auditoria.append(entrada)
        if len(auditoria) > MAXIMO_AUDITORIA:
            del auditoria[: len(auditoria) - MAXIMO_AUDITORIA]
        guardar("auditoria", auditoria)
        return entrada


def listar_auditoria(limite: int = 200) -> list[dict]:
    """Las entradas más recientes primero."""
    entradas = sorted(cargar("auditoria"), key=lambda e: e.get("fecha", ""), reverse=True)
    return entradas[: max(1, int(limite))]


# ---------------------------------------------------------------------------
# Trabajos (la lógica de ejecución está en trabajos.py)
# ---------------------------------------------------------------------------
def listar_trabajos() -> list[dict]:
    return list(cargar("trabajos"))


def trabajo(trabajo_id) -> dict | None:
    for t in cargar("trabajos"):
        if str(t["id"]) == str(trabajo_id):
            return t
    return None


def guardar_trabajo(t: dict) -> dict:
    """Inserta o reemplaza el trabajo con el mismo id."""
    with _BLOQUEO:
        trabajos = cargar("trabajos")
        for i, existente in enumerate(trabajos):
            if str(existente["id"]) == str(t["id"]):
                trabajos[i] = t
                break
        else:
            trabajos.append(t)
        guardar("trabajos", trabajos)
        return t


def actualizar_trabajo(trabajo_id, **campos) -> dict | None:
    with _BLOQUEO:
        trabajos = cargar("trabajos")
        for t in trabajos:
            if str(t["id"]) == str(trabajo_id):
                t.update(campos)
                guardar("trabajos", trabajos)
                return t
        return None


# ---------------------------------------------------------------------------
# Catálogo: habilitaciones persistidas (RF-20)
# ---------------------------------------------------------------------------
def habilitaciones() -> dict:
    datos = cargar("catalogo")
    datos.setdefault("fuentes", {})
    datos.setdefault("variables", {})
    return datos


def guardar_habilitacion_fuente(fuente_id: str, habilitada: bool) -> None:
    with _BLOQUEO:
        datos = habilitaciones()
        datos["fuentes"][fuente_id] = bool(habilitada)
        guardar("catalogo", datos)


def guardar_habilitacion_variable(fuente_id: str, variable_id: str, habilitada: bool) -> None:
    with _BLOQUEO:
        datos = habilitaciones()
        datos["variables"][f"{fuente_id}/{variable_id}"] = bool(habilitada)
        guardar("catalogo", datos)


# ---------------------------------------------------------------------------
# Métricas de consultas (para GET /api/admin/sistema)
# ---------------------------------------------------------------------------
def contar_consulta() -> None:
    with _BLOQUEO:
        contadores = cargar("contadores")
        hoy = date.today().isoformat()
        contadores["consultas_total"] = int(contadores.get("consultas_total", 0)) + 1
        por_dia = contadores.setdefault("consultas_por_dia", {})
        por_dia[hoy] = int(por_dia.get(hoy, 0)) + 1
        # Se conservan solo los últimos 30 días
        for clave in sorted(por_dia)[:-30]:
            del por_dia[clave]
        guardar("contadores", contadores)


def metricas_consultas() -> dict:
    contadores = cargar("contadores")
    hoy = date.today().isoformat()
    return {
        "consultas_total": int(contadores.get("consultas_total", 0)),
        "consultas_hoy": int(contadores.get("consultas_por_dia", {}).get(hoy, 0)),
    }


# ---------------------------------------------------------------------------
# Datos iniciales (sección 7.9)
# ---------------------------------------------------------------------------
USUARIOS_DEMO = [
    {"usuario": "mvera", "nombre": "Dra. María Vera", "rol": "oceanografo",
     "departamento": "Oceanografía y Meteorología Marina", "correo": "mvera@inocar.mil.ec"},
    {"usuario": "jloor", "nombre": "Ing. Jorge Loor", "rol": "analista",
     "departamento": "Oceanografía y Meteorología Marina", "correo": "jloor@inocar.mil.ec"},
    {"usuario": "rcastro", "nombre": "CPFG Ricardo Castro", "rol": "revisor",
     "departamento": "Oceanografía y Meteorología Marina", "correo": "rcastro@inocar.mil.ec"},
    {"usuario": "admin", "nombre": "Administrador de TI", "rol": "administrador",
     "departamento": "Tecnologías de la Información", "correo": "admin@inocar.mil.ec"},
]
CLAVE_DEMO = "some2026"


def _vistas_de_ejemplo(usuarios: dict[str, dict]) -> None:
    from . import catalogo   # importación local: el catálogo carga los conectores (sin ciclos al importar)
    corte = catalogo.fecha_corte_sistema()
    hace = lambda dias, hora: (datetime.now() - timedelta(days=dias)).replace(  # noqa: E731
        hour=hora, minute=12, second=0, microsecond=0).isoformat(timespec="seconds")
    inicio_12m = (corte - timedelta(days=364)).isoformat()
    crear_vista(usuarios["mvera"], {
        "nombre": "Anomalía SST Niño 1+2 — últimos 12 meses",
        "descripcion": "Mapa de anomalía de la temperatura superficial del mar en la región Niño 1+2 "
                       "respecto a la climatología 1991-2020, para el boletín del CN-ERFEN.",
        "modulo": "mapa",
        "parametros": {"fuente": "noaa_oisst", "variable": "sst", "area": "recorte:nino12",
                       "periodo": f"{inicio_12m},{corte.isoformat()}", "fecha": corte.isoformat(),
                       "modo": "anomalia", "referencia": "1991-2020"},
        "compartida": True,
    }, creada=hace(6, 9))
    crear_vista(usuarios["mvera"], {
        "nombre": "Perfil térmico 85° O, 2° S",
        "descripcion": "Perfil vertical de temperatura (0 a 100 m) frente a la costa, con la capa de mezcla.",
        "modulo": "perfiles",
        "parametros": {"fuente": "copernicus_phy", "variable": "thetao", "area": "punto:-2,-85",
                       "fecha": "2026-10-26", "periodo": "2025-10-27,2026-10-26", "pestana": "perfil"},
        "compartida": False,
    }, creada=hace(4, 15))
    crear_vista(usuarios["jloor"], {
        "nombre": "Lluvia acumulada Guayas — estación lluviosa 2026",
        "descripcion": "Serie semanal de precipitación CHIRPS en la provincia del Guayas entre enero y mayo de 2026.",
        "modulo": "series",
        "parametros": {"fuente": "chirps_v3", "variable": "precip", "area": "recorte:guayas",
                       "periodo": "2026-01-01,2026-05-31", "agregacion": "semanal",
                       "referencia": "1991-2020", "pestana": "serie"},
        "compartida": True,
    }, creada=hace(2, 11))


def _auditoria_de_ejemplo() -> None:
    ahora = datetime.now()

    def momento(dias: int, hora: int, minuto: int) -> str:
        return (ahora - timedelta(days=dias)).replace(hour=hora, minute=minuto, second=0,
                                                       microsecond=0).isoformat(timespec="seconds")

    eventos = [
        (7, 8, 5, "admin", "ingreso", "Inicio de sesión correcto", "10.10.2.15"),
        (7, 8, 12, "admin", "usuario_creado", "Se creó el usuario «jloor» (analista)", "10.10.2.15"),
        (7, 8, 20, "admin", "catalogo_cambiado", "Variable copernicus_phy/corrientes habilitada", "10.10.2.15"),
        (7, 8, 31, "admin", "salida", "Cierre de sesión", "10.10.2.15"),
        (6, 9, 2, "mvera", "ingreso", "Inicio de sesión correcto", "10.10.3.41"),
        (6, 9, 14, "mvera", "vista_compartida", "Vista «Anomalía SST Niño 1+2 — últimos 12 meses» compartida", "10.10.3.41"),
        (6, 9, 40, "mvera", "reporte_generado", "Boletín quincenal para el CN-ERFEN (Región Niño 1+2)", "10.10.3.41"),
        (6, 10, 5, "mvera", "salida", "Cierre de sesión", "10.10.3.41"),
        (5, 14, 22, "jloor", "ingreso", "Inicio de sesión correcto", "10.10.3.52"),
        (5, 14, 48, "jloor", "exportacion", "Exportación CSV de la serie de precipitación en Guayas", "10.10.3.52"),
        (5, 15, 10, "jloor", "salida", "Cierre de sesión", "10.10.3.52"),
        (4, 10, 0, "rcastro", "ingreso", "Inicio de sesión correcto", "10.10.1.7"),
        (4, 10, 25, "rcastro", "salida", "Cierre de sesión", "10.10.1.7"),
        (3, 11, 15, "mvera", "ingreso", "Inicio de sesión correcto", "10.10.3.41"),
        (3, 11, 50, "mvera", "reporte_generado", "Informe de una variable: Temperatura superficial del mar", "10.10.3.41"),
        (3, 12, 30, "mvera", "salida", "Cierre de sesión", "10.10.3.41"),
        (2, 9, 45, "jloor", "ingreso", "Inicio de sesión correcto", "10.10.3.52"),
        (2, 10, 12, "jloor", "vista_compartida", "Vista «Lluvia acumulada Guayas — estación lluviosa 2026» compartida", "10.10.3.52"),
        (2, 10, 40, "jloor", "salida", "Cierre de sesión", "10.10.3.52"),
        (1, 16, 5, "admin", "ingreso", "Inicio de sesión correcto", "10.10.2.15"),
        (1, 16, 9, "admin", "usuario_editado", "Se actualizó el correo de «rcastro»", "10.10.2.15"),
        (1, 16, 20, "admin", "salida", "Cierre de sesión", "10.10.2.15"),
    ]
    for dias, hora, minuto, usuario, accion, detalle, ip in eventos:
        registrar_auditoria(usuario, accion, detalle, ip, fecha=momento(dias, hora, minuto))


def inicializar() -> bool:
    """Crea la carpeta `datos/` y los datos de ejemplo si no existen. Devuelve True la primera vez."""
    with _BLOQUEO:
        ruta_datos().mkdir(parents=True, exist_ok=True)
        ruta_reportes().mkdir(parents=True, exist_ok=True)
        primera_vez = not _archivo("usuarios").exists()
        if primera_vez:
            olvidar_memoria()
            usuarios: dict[str, dict] = {}
            for datos in USUARIOS_DEMO:
                u = crear_usuario(datos, CLAVE_DEMO)
                usuarios[u["usuario"]] = u
                guardar_panel(u["id"], panel_por_defecto(u["rol"]))
            _vistas_de_ejemplo(usuarios)
            _auditoria_de_ejemplo()
            guardar("trabajos", [])
        else:
            # Garantiza que cada usuario tenga su panel (p. ej., si se borró paneles.json)
            paneles = cargar("paneles")
            for u in cargar("usuarios"):
                if str(u["id"]) not in paneles:
                    guardar_panel(u["id"], panel_por_defecto(u["rol"]))
        return primera_vez
