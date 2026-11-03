"""Modelos pydantic de las peticiones y respuestas de la API del SOME (sección 7).

Los modelos de consulta son deliberadamente permisivos (`extra="allow"`): la
validación semántica detallada, con mensajes en español, la hace `motor.py`
(`ErrorConsulta`). Aquí se garantiza la forma básica del cuerpo y los tipos.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    def como_dict(self) -> dict:
        """Diccionario sin los campos en None (lo que espera el motor)."""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
class Credenciales(_Modelo):
    usuario: str = Field(..., min_length=1, max_length=60)
    clave: str = Field(..., min_length=1, max_length=200)


class UsuarioPublico(BaseModel):
    id: int
    usuario: str
    nombre: str
    rol: str
    departamento: str | None = ""
    correo: str | None = ""
    activo: bool | None = True
    creado: str | None = None


class Sesion(BaseModel):
    token: str
    usuario: UsuarioPublico


class Ok(BaseModel):
    ok: bool = True


class Salud(BaseModel):
    estado: str
    version: str
    fecha_corte: str
    datos_simulados: bool = True          # True mientras alguna fuente del catálogo sea simulada
    procedencia: str = "simulado"         # «real», «simulado» o «mixto»
    fuentes_reales: list[str] = []


class Error(BaseModel):
    detalle: str


# ---------------------------------------------------------------------------
# Consultas (sección 7.3)
# ---------------------------------------------------------------------------
class Periodo(_Modelo):
    inicio: str
    fin: str


class Area(_Modelo):
    tipo: Literal["bbox", "recorte", "punto"]
    bbox: list[float] | None = None
    recorte: str | None = None
    lat: float | None = None
    lon: float | None = None


class ConsultaBase(_Modelo):
    fuente: str
    variable: str
    area: Area | None = None
    profundidad: float | None = None
    periodo_referencia: str | None = None


class ConsultaMapa(ConsultaBase):
    fecha: str | None = None
    periodo: Periodo | None = None
    modo: Literal["valor", "anomalia"] = "valor"
    paso: int | None = None
    suavizado: bool = True     # interpolar entre centros de celda en el PNG del mapa
    isolineas: bool = False    # trazar líneas de igual valor sobre el campo


class ConsultaSerie(ConsultaBase):
    periodo: Periodo
    agregacion: Literal["diaria", "semanal", "mensual"] = "diaria"


class ConsultaClimatologia(ConsultaBase):
    periodo_referencia: str | None = None


class ConsultaMismoMes(ConsultaBase):
    mes: int = Field(..., ge=1, le=12)


class ConsultaPerfil(_Modelo):
    fuente: str = "copernicus_phy"
    variable: str = "thetao"
    lat: float
    lon: float
    fecha: str | None = None


class ConsultaCortina(_Modelo):
    fuente: str = "copernicus_phy"
    variable: str = "thetao"
    lat: float
    lon: float
    periodo: Periodo


class LadoComparacion(ConsultaBase):
    periodo: Periodo
    etiqueta: str | None = None


class ConsultaComparar(_Modelo):
    a: LadoComparacion
    b: LadoComparacion
    agregacion: Literal["diaria", "semanal", "mensual"] = "diaria"


class ConsultaVectores(_Modelo):
    fuente: str = "copernicus_phy"
    variable: str = "corrientes"
    area: Area | None = None
    fecha: str | None = None
    profundidad: float | None = None
    paso: int = Field(2, ge=1, le=10)


# ---------------------------------------------------------------------------
# Exportación (7.4), reportes y trabajos (7.5)
# ---------------------------------------------------------------------------
class PeticionExportar(_Modelo):
    formato: Literal["csv", "png", "netcdf"]
    clase: Literal["mapa", "serie", "perfil", "cortina"]
    consulta: dict[str, Any]


class PeticionReporte(_Modelo):
    plantilla: str
    parametros: dict[str, Any] = Field(default_factory=dict)


class TrabajoCreado(BaseModel):
    trabajo_id: int
    estado: str


# ---------------------------------------------------------------------------
# Panel personal (7.6)
# ---------------------------------------------------------------------------
class Widget(_Modelo):
    id: str
    tipo: str
    titulo: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    ancho: int = Field(1, ge=1, le=3)


class Panel(_Modelo):
    widgets: list[Widget] = Field(default_factory=list)


class DatosWidget(_Modelo):
    tipo: str
    config: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Mis vistas (7.7)
# ---------------------------------------------------------------------------
class VistaEntrada(_Modelo):
    nombre: str = Field(..., min_length=1, max_length=120)
    descripcion: str = Field("", max_length=500)
    modulo: Literal["mapa", "series", "perfiles", "comparar"]
    parametros: dict[str, Any] = Field(default_factory=dict)
    compartida: bool = False


# ---------------------------------------------------------------------------
# Administración (7.8)
# ---------------------------------------------------------------------------
class UsuarioEntrada(_Modelo):
    usuario: str = Field(..., min_length=2, max_length=30, pattern=r"^[a-zA-Z0-9._-]+$")
    nombre: str = Field(..., min_length=1, max_length=80)
    rol: Literal["oceanografo", "analista", "revisor", "administrador"]
    departamento: str = Field("", max_length=80)
    correo: str = Field("", max_length=100)
    activo: bool = True
    clave: str | None = Field(None, min_length=4, max_length=200)


class UsuarioEdicion(_Modelo):
    usuario: str | None = Field(None, min_length=2, max_length=30, pattern=r"^[a-zA-Z0-9._-]+$")
    nombre: str | None = Field(None, min_length=1, max_length=80)
    rol: Literal["oceanografo", "analista", "revisor", "administrador"] | None = None
    departamento: str | None = Field(None, max_length=80)
    correo: str | None = Field(None, max_length=100)
    activo: bool | None = None
    clave: str | None = Field(None, min_length=4, max_length=200)


class Habilitacion(_Modelo):
    habilitada: bool
