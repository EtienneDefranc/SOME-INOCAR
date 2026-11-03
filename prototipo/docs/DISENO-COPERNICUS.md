# Diseño: Copernicus Marine con datos reales en el SOME

> Documento de diseño para la implementación en paralelo de dos agentes (A: núcleo; B: integración).
> Fecha: 30/10/2026. *Toolbox* comprobado: `copernicusmarine` 2.5.0 (pip, intérprete global).
> El patrón a seguir es el de CHIRPS v3 (`backend/chirps/` y `backend/conectores/chirps.py`).
> NOAA OISST sigue simulado y no se toca.

## 0. Decisiones (resumen)

1. **Producto diario**: GLOBAL_ANALYSISFORECAST_PHY_001_024 (ANFC, 1/12°, 50 niveles, DOI 10.48670/moi-00016), leído con `copernicusmarine.open_dataset` (perezoso, ARCO) recortando área, días y **solo los niveles necesarios**; se pasa `service=` explícito según un plan de fragmentos (§3.5).
2. **Solo análisis, nunca pronóstico**: la fecha de corte es el último día del análisis semanal NRT, `W − 2`, donde `W` es el miércoles del último boletín semanal publicado (regla del PUM 001-024, §3.2). Hoy (30/10/2026): `W` = 28/10/2026 → corte **26/10/2026**.
3. **Regrillado**: media de las 3 × 3 celdas de 1/12° cuyo centro cae en cada celda SOME de 0.25° (mínimo 5 de 9 válidas). **Profundidad**: interpolación lineal entre los dos niveles del modelo que rodean cada nivel SOME (0 m = nivel 0.494 m), 15 niveles nativos en total.
4. **Caché local** `datos/copernicus/` ya regrillada, por variable/nivel/mes, con **tipo por celda** (0 no obtenido, 1 análisis NRT, 2 mejor análisis) y boletín y momento de obtención por día.
5. **Climatología**: la oficial de GLORYS12 (`cmems_mod_glo_phy_my_0.083deg-climatology_P1M-m`, DOI 10.48670/moi-00021), cuyo periodo real es **1993-2016** (`admp_climatology_bounds` = 1993-01-01/2016-12-31); se lee `climatology_bounds` y el periodo efectivo se declara siempre (método, CSV, PDF, respuesta), junto con la diferencia de sistema (reanálisis frente a análisis operativo).
6. **Procedencia por Dataset**: cada `Dataset` declara `datos_reales` (1/0); el motor calcula real/simulado/mixto mirando los Dataset, no solo el conector (B cambia `base.py` y `motor.py`).
7. **Respaldo simulado honesto**: sin acceso (sin paquete, sin credenciales, credenciales inválidas, sin red o servicio caído) se sirve lo que haya en caché; si a una consulta no le queda **ningún** dato real, se responde con el simulador, etiquetado «simulado» y con un aviso que dice por qué. Nunca se mezclan días simulados con reales dentro de un mismo Dataset.
8. **Modos**: `SOME_COPERNICUS_MODO` = `auto` (por defecto) | `simulado` (simulador anterior, idéntico) | `real` (estricto: sin respaldo simulado, error si falta dato). `SOME_COPERNICUS_SIN_RED=1` (solo caché) y `SOME_COPERNICUS_SIN_SINCRONIZACION=1`.
9. **Estado RF-21** con verificaciones reales (credenciales con `login(check_credentials_valid=True)`, lectura de prueba de un fragmento con su latencia, metadatos con `describe`), historial de 14 días y mensajes claros («Sin credenciales de Copernicus Marine: …»).
10. **Credenciales**: el código nunca pide, escribe, imprime ni guarda usuario o contraseña; nunca llama a `login()` sin `check_credentials_valid=True`; instala una **guardia anti-*prompt*** que convierte cualquier intento del *toolbox* de preguntar en una excepción (§3.11).
11. **Sincronización liviana**: superficie (0 m) de las 6 variables del último día de análisis (área completa) y capa de mezcla del golfo de Guayaquil de 7 días; el resto, bajo demanda o con `preparar_copernicus`.
12. **Import perezoso**: importar `copernicusmarine` tarda ~3.8 s; nunca se importa en el arranque ni en una consulta servida desde la caché.
13. **Licencia** (revisión del 01/11/2026; [CONDICIONES-USO-FUENTES.md](../../CONDICIONES-USO-FUENTES.md), §3). La licencia de Copernicus Marine permite la caché del prototipo y el lago de la Fase 2: copias para uso interno y respaldo, y obras derivadas para cualquier fin, sin plazo. A cambio exige:
    - **Crédito** (2.3 y 2.4): visible, nombrando al originador y citando los DOI.
      - Para las obras derivadas, que son todas las salidas del prototipo (malla de 0.25°), la forma recomendada es «Generated using E.U. Copernicus Marine Service Information», con los DOI como enlaces (2.4.a).
      - En el lago, los recortes a 1/12° sin modificar se redistribuyen con la fórmula 2.4.b.
      - Desde el 01/11/2026 el código la usa: `CREDITO_DATOS` = «Generated using E.U. Copernicus Marine Service Information; análisis diario, https://doi.org/10.48670/moi-00016.», el crédito de la climatología es «Climatología GLORYS12, https://doi.org/10.48670/moi-00021.» y el motor antepone «Generated using …» si una consulta solo usó la climatología. `LICENCIA` resume estas obligaciones.
    - **Modificaciones y aval:** declarar lo que el SOME modificó y no sugerir aval de la UE, de Copernicus ni de Mercator.
    - **Registros de uso:** llevarlos y entregarlos si Copernicus Marine los pide (2.6).
    - **Credenciales:** son personales del firmante e intransferibles (SLA 2.7). La cuenta que se configure con `copernicusmarine login` en un equipo del INOCAR debe registrarla la institución. La cuenta personal de un integrante del equipo no debe entregarse con el sistema ni quedar en el servidor.
    - **Carga histórica del lago:** se hace con `get` o `subset`, de 1 a 4 peticiones simultáneas; `open_dataset` queda para recortes pequeños.

---

## 1. Arquitectura

### 1.1 Paquete `backend/copernicus/` (espejo de `backend/chirps/`)

| Módulo | Responsabilidad | Espejo en CHIRPS |
|---|---|---|
| `__init__.py` | API pública del núcleo (§2.1): modo, acceso, estado, resumen, firma, lecturas, sincronización. | `chirps/__init__.py` |
| `constantes.py` | Ids de productos y datasets, DOI, rutas de la caché, umbrales, geometría de fragmentos de respaldo, interruptores de entorno (leídos en cada llamada), tipos de dato y excepciones. | `chirps/constantes.py` |
| `acceso.py` | Único módulo que importa `copernicusmarine` (perezoso, una vez), fija `COPERNICUSMARINE_HTTPS_TIMEOUT=20` y `COPERNICUSMARINE_HTTPS_RETRIES=2` con `os.environ.setdefault` **antes** de importarlo, baja su *logger* a WARNING, instala la guardia anti-*prompt*; `paquete_instalado()`, `credenciales_presentes()`, `verificar_credenciales()`, `metadatos(dataset_id)` (con `describe`), `leer_ventana(...)` (con `open_dataset` + `.load()` en un hilo con tiempo máximo) y traducción de errores al español. | `chirps/red.py` + `chirps/espejo.py` |
| `calendario.py` | Boletín semanal `W`, último día de análisis, tipo de cada día (NRT / mejor análisis / excluido). Puro, sin red. | — |
| `malla.py` | Ventana nativa 147 × 207 de la malla SOME, validación de coordenadas, asignación de cada celda nativa a su celda SOME, media 3 × 3 (mínimo 5 válidas), lienzo para ventanas parciales. | `chirps/malla.py` |
| `niveles.py` | Tabla nivel SOME → (nivel inferior, nivel superior, peso); interpolación vectorizada; lista de niveles nativos necesarios. | — |
| `almacen.py` | Caché diaria (variable, nivel, mes) y climatológica; lectura con caché en memoria invalidada por *mtime*; escritura atómica con bloqueo entre procesos; regla de reemplazo; marca de generación (firma); conteos y fechas límite. | `chirps/almacen.py` |
| `descarga.py` | Plan de descarga (servicio, ventana alineada a fragmentos, días, niveles, costo en fragmentos) y su ejecución: leer → interpolar → regrillar → guardar. Bloqueo de descarga por dataset y registro de «tramos en curso». | `chirps/descarga.py` |
| `climatologia.py` | Descarga y caché de la climatología GLORYS12 por (variable, nivel); lectura de `climatology_bounds` y periodo efectivo; textos de método y origen. | `chirps/climatologia.py` |
| `estado.py` | `datos/copernicus/estado.json` (verificaciones, credenciales, metadatos, historial diario, sincronizaciones, errores) con transacciones bajo bloqueo entre procesos; `estado_actual()`, `historial_estado()`, `detalle()` y piezas del `resumen()`; sin red. Reutiliza `chirps.estado.legible`/`acortar` para los mensajes. | `chirps/estado.py` |
| `sincronizacion.py` | Hilo *daemon*: verificación + metadatos + conjunto liviano + paso de NRT a mejor análisis + climatología de superficie faltante; ciclo de 6 h (30 min si algo falló). | `chirps/sincronizacion.py` |
| `prueba_copernicus.py` | Pruebas con dobles (sin red) y bloque real opcional si hay credenciales. | `chirps/prueba_chirps.py` |

Bloqueos entre procesos: se reutiliza `backend.chirps.bloqueo.bloqueo` (genérico) con archivos en `datos/copernicus/.bloqueos/`. Ningún módulo del paquete toca la red ni importa `copernicusmarine` al importarse.

### 1.2 Reescritura de `backend/conectores/copernicus.py`

- `ConectorCopernicus(ConectorFuente)` (ya no hereda de `ConectorSimulado`) con **los mismos** `id = "copernicus_phy"`, `nombre`, `nombre_corto`, `identificador = "CMEMS-GLOBAL_ANALYSISFORECAST_PHY_001_024"` y la **misma lista de 7 `VariableFuente`** (ids, nombres, unidades, paletas, `paleta_anomalia`, `unidades_cf`, `decimales`, `tiene_profundidad`, `es_vector`, `componentes=["uo", "vo"]` de «corrientes»): la interfaz y los reportes dependen de ellos.
- Contiene un simulador interno `_SimuladorCopernicus(ConectorSimulado)` (el cuerpo actual de la clase: `fecha_corte_simulada = 26/10/2026`, latencia 1 450 ms) que **comparte** la lista `variables` del conector, para el modo `simulado` y el respaldo.
- `datos_reales` e `insignia` pasan a ser propiedades dinámicas (`modo() == "real"`).
- `obtener()` y `climatologia()` siguen la tabla de decisión de §3.8; `estado()`, `historial_estado()` y `detalle_estado()` delegan en `backend.copernicus` y **nunca lanzan**.

---

## 2. Interfaces exactas

### 2.1 API pública del paquete `backend.copernicus` (A la expone; B la consume)

```python
# backend/copernicus/__init__.py
from datetime import date
import numpy as np

# --- Modo y acceso: sin red y baratos (se llaman en cada petición; resultados en memoria por mtime) ---
def modo() -> str: ...               # "real" | "simulado"  (regla en §3.12)
def modo_configurado() -> str: ...   # "auto" | "simulado" | "real"  (SOME_COPERNICUS_MODO; valor desconocido → "auto")
def sin_red() -> bool: ...           # SOME_COPERNICUS_SIN_RED activo
def sin_sincronizacion() -> bool: ...# SOME_COPERNICUS_SIN_SINCRONIZACION activo
def paquete_instalado() -> bool: ... # importlib.util.find_spec("copernicusmarine") is not None (sin importarlo)
def acceso_disponible() -> bool: ... # se puede descargar AHORA (§3.8): paquete, red, credenciales no inválidas, sin caída reciente
def motivo_sin_acceso() -> str: ...  # "" si hay acceso; si no, frase en español (§3.10)
def fecha_corte() -> date | None: ...# fecha de corte efectiva (§3.2); None solo en modo "simulado"
def ultimo_dia_analisis() -> date: ...  # W − 2 según metadatos vigentes o calendario; sin red

# --- Estado RF-21: sin red, nunca lanzan (ante un error devuelven sin_conexion con el motivo) ---
def estado_actual() -> dict: ...     # forma en §2.4
def historial_estado(dias: int = 14) -> list[dict]: ...
def detalle_estado() -> dict: ...    # bloque que se fusiona en GET /api/fuentes/estado (§2.4)
def resumen() -> dict: ...           # tarjeta «Copernicus Marine · caché y sincronización» (§2.4)
def firma_cache() -> str: ...        # cambia con cada escritura de la caché, con el modo, el acceso y la fecha de corte

# --- Datos (leen la caché; con llenar=True y acceso, descargan lo que falte dentro de espera_s) ---
VARIABLES: tuple[str, ...] = ("thetao", "so", "uo", "vo", "zos", "mlotst")   # «corrientes» no se guarda: son uo y vo

def campo(variable: str, fecha: date, profundidad: float | None = None, *, llenar: bool = True) -> np.ndarray: ...
    # (49, 69) float32, NaN = tierra/fondo o no obtenido. ErrorSinDatos si la celda entera del día no está obtenida.
def campos(variable: str, inicio: date, fin: date, profundidad: float | None = None, *,
           ilat: np.ndarray | None = None, ilon: np.ndarray | None = None,
           llenar: bool = True, espera_s: float | None = None) -> "Lectura": ...
def climatologia_mensual(variable: str, profundidad: float | None = None, *,
                         llenar: bool = True, espera_s: float | None = None) -> "ClimatologiaCMEMS": ...
def origen_dias(variable: str, profundidad: float | None, inicio: date, fin: date) -> dict[date, str]: ...
    # {día: "definitivo" | "nrt" | "parcial" | "ausente"} sobre el área completa

# --- Red: solo para el hilo de fondo, la herramienta y las pruebas ---
def verificar() -> dict: ...         # verificación real completa (§3.10); registra en estado.json; nunca lanza
def sincronizar(dias: int | None = None) -> dict: ...   # un ciclo del conjunto liviano (§3.9); resumen del ciclo
def preparar_escenarios(nombres: list[str] | None = None, *, climatologia_completa: bool = False,
                        progreso=None) -> dict: ...     # §3.13; progreso(hechos, total, texto)
def iniciar_sincronizacion() -> None: ...   # no bloquea, idempotente; no arranca con SIN_RED, SIN_SINCRONIZACION o modo "simulado"
def detener_sincronizacion() -> None: ...
def sincronizando() -> bool: ...
```

Tipos de retorno (en `backend/copernicus/almacen.py` y `climatologia.py`, reexportados en `__init__`):

```python
@dataclass(frozen=True)
class Lectura:
    variable: str
    profundidad: float | None          # nivel SOME (0, 5, …, 100) o None para variables 2D
    fechas: list[date]                 # n días consecutivos
    valores: np.ndarray                # (n, nlat, nlon) float32
    tipo: np.ndarray                   # (n, nlat, nlon) int8: 0 no obtenido, 1 análisis NRT, 2 mejor análisis
    boletin: list[str]                 # n fechas ISO del boletín semanal W con que se obtuvo cada día ("" si ninguno)
    @property
    def cobertura(self) -> float: ...  # fracción de celdas-día con tipo > 0 (1.0 si n == 0)

@dataclass(frozen=True)
class ClimatologiaCMEMS:
    variable: str
    profundidad: float | None
    valores: np.ndarray                # (12, 49, 69) float32, mes 1..12
    periodo: str                       # "1993-2016"
    anio_inicio: int                   # 1993
    anio_fin: int                      # 2016
    fuente_periodo: str                # "climatology_bounds" | "metadatos" | "respaldo"
    limites: list[tuple[str, str]]     # 12 pares ISO leídos de climatology_bounds ([] si no se pudieron leer)
    obtenido: str                      # ISO del momento de descarga
```

Excepciones (`backend/copernicus/constantes.py`, reexportadas):

```python
class ErrorCopernicus(RuntimeError): ...          # base; desde un conector real el motor la convierte en 503
class ErrorSinAcceso(ErrorCopernicus): ...        # sin paquete, sin red, sin credenciales, credenciales inválidas, servicio caído
class ErrorSinCredenciales(ErrorSinAcceso): ...   # también la lanza la guardia anti-prompt
class ErrorDescargaEnCurso(ErrorCopernicus): ...  # la descarga bajo demanda superó espera_s y sigue en segundo plano
class ErrorFormato(ErrorCopernicus): ...          # malla, niveles, tiempo o unidades distintos de lo esperado
class ErrorSinDatos(ErrorCopernicus): ...         # campo(): el día no está en la caché y no se pudo obtener
```

### 2.2 `ConectorCopernicus` (A)

```python
class ConectorCopernicus(ConectorFuente):
    id = "copernicus_phy"
    nombre = "Copernicus Marine GLOBAL_ANALYSISFORECAST_PHY_001_024"
    nombre_corto = "Copernicus Marine"
    proveedor = "E.U. Copernicus Marine Service (Mercator Ocean International)"
    producto = "GLOBAL_ANALYSISFORECAST_PHY_001_024 (análisis y pronóstico físico global; en SOME solo el análisis)"
    identificador = "CMEMS-GLOBAL_ANALYSISFORECAST_PHY_001_024"
    licencia = LICENCIA                           # licencia de uso del Copernicus Marine Service y sus obligaciones (§0.13)
    variables: list[VariableFuente]               # las 7 actuales, sin cambios

    @property
    def datos_reales(self) -> bool: ...           # backend.copernicus.modo() == "real"
    @property
    def insignia(self) -> str: ...                # real: getattr(cfg, "INSIGNIA_REALES_COPERNICUS", "Datos reales · Copernicus Marine")
                                                  # simulado: cfg.INSIGNIA_SIMULADOS
    @property
    def credito(self) -> str: ...                 # CREDITO (abajo), en ambos modos
    @property
    def referencias(self) -> str: ...             # "https://doi.org/10.48670/moi-00016 — https://doi.org/10.48670/moi-00021"
                                                  # + « — Datos consultados por última vez el dd/mm/aaaa.» si hay descargas
    @property
    def resolucion(self) -> str: ...              # real: "1/12° diario, 50 niveles (en SOME: 0.25° y 8 niveles de 0 a 100 m, interpolados)"
    @property
    def cobertura_temporal(self) -> str: ...      # real: "Análisis diario desde 01/06/2022; en SOME desde 01/10/2023 hasta el último día de análisis"

    @staticmethod
    def fecha_corte_efectiva() -> date | None: ...   # = backend.copernicus.fecha_corte()
    def obtener(self, p: ParametrosConsulta) -> xr.Dataset: ...
    def climatologia(self, variable: str, periodo_referencia: str, bbox,
                     profundidad: float | None = None, *, simulada: bool = False) -> xr.Dataset: ...
    def estado(self) -> EstadoFuente: ...         # nunca lanza
    def historial_estado(self, dias: int = 14) -> list[dict]: ...
    def detalle_estado(self) -> dict: ...
```

`CREDITO = "Generated using E.U. Copernicus Marine Service Information; análisis diario, https://doi.org/10.48670/moi-00016; climatología GLORYS12, https://doi.org/10.48670/moi-00021."` (forma 2.4.a de la licencia, §0.13; antes del 01/11/2026 usaba la fórmula de redistribución)

Reglas del contrato que B puede dar por seguras:

- `obtener()` devuelve dimensiones `(time, depth, lat, lon)` para variables 3D (con `depth` = los niveles pedidos, en el orden pedido) y `(time, lat, lon)` para `zos` y `mlotst`; para «corrientes» trae las variables `uo` y `vo` (el motor calcula la rapidez). `lat`/`lon` son `cfg.LAT[ilat]`/`cfg.LON[ilon]` de `sim.indices_bbox(bbox)`, como hoy.
- Profundidades: en modo real solo se aceptan los niveles de `cfg.PROFUNDIDADES` (tolerancia 1e-6); otro valor → `ValueError` («Copernicus Marine se sirve en los niveles 0, 5, 10, 20, 30, 50, 75 y 100 m…», 422). En modo simulado, como hoy.
- Fechas: se acotan a `[cfg.FECHA_INICIO, fecha_corte]`; si el periodo queda vacío → `ValueError` con el mismo texto que hoy.
- `estado().fecha_corte`: en modo real, `fecha_corte_efectiva()` (siempre ≥ `cfg.FECHA_INICIO`); en modo simulado, la del simulador (26/10/2026).
- `climatologia(..., simulada=True)` devuelve SIEMPRE la climatología del simulador (atributos de §2.3, `datos_reales=0`); el motor la pide así cuando los datos de la misma consulta salieron del respaldo simulado.

### 2.3 Contrato de los `Dataset` (A produce, B consume)

Atributos globales de `obtener()`:

| Atributo | Real | Respaldo simulado |
|---|---|---|
| `Conventions` | `"CF-1.8"` | igual |
| `title` | `"{var.nombre} — {nombre}"` | el del simulador |
| `source` | producto, ids de los datasets usados, versión, servicio ARCO y versión del *toolbox* | el del simulador |
| `institution`, `references`, `history` | proveedor; DOI ANFC; «leído de la caché local datos/copernicus (llenada con copernicusmarine.open_dataset) y llevado a la malla SOME de 0.25°» | los del simulador |
| `comment` | `INSIGNIA_REALES_COPERNICUS` | `cfg.INSIGNIA_SIMULADOS` |
| `datos_reales` | `1` (int) | `0` (int) |
| `procedencia` | `"real"` | `"simulado"` |
| `insignia` | `INSIGNIA_REALES_COPERNICUS` | `cfg.INSIGNIA_SIMULADOS` |
| `respaldo_simulado` | — | `1` |
| `aviso` | solo si la cobertura < 1: «Parte del área o del periodo no está en la caché local y no se pudo obtener ahora: {motivo}. Esas celdas quedan sin dato.» | siempre: «Copernicus Marine no está disponible: {motivo}. Esta consulta usa datos SIMULADOS para demostración.» |
| `credito` | `CREDITO_DATOS` («Generated using E.U. Copernicus Marine Service Information; análisis diario, https://doi.org/10.48670/moi-00016.») | el crédito simulado actual |
| `identificador`, `licencia`, `fuente_id`, `fecha_corte`, `resolucion` | del conector; `fuente_id = "copernicus_phy"`; `fecha_corte` ISO | igual (fecha del simulador) |
| `procesamiento` | texto de §3.3–3.4 que termina con «Días de esta consulta: N de mejor análisis y M de análisis NRT.» (sin las partes en cero) | — |
| `origen_datos` | «Copernicus Marine (análisis ANFC): N días de mejor análisis y M de análisis NRT» | — |
| `producto`, `doi` | `"GLOBAL_ANALYSISFORECAST_PHY_001_024"`, `"10.48670/moi-00016"` | — |
| `dias_faltantes` | días sin ninguna celda obtenida (int) | — |
| `dias_preliminares` | `0` siempre (concepto de CHIRPS; los NRT van en `dias_nrt`) | — |
| `dias_nrt`, `dias_definitivos` | int | — |
| `cobertura` | float 0–1 | — |

Atributos de cada variable: `standard_name`, `long_name`, `units` (= `unidades_cf`: `degC`, `1e-3`, `m s-1`, `m`), `unidad_mostrada`, `coordinates`, `comment` (= insignia del Dataset) y, si es real, `cell_methods = "time: mean (diario) area: mean (3 × 3 celdas de 1/12°)"`.

Atributos globales de `climatologia()` (real), dimensiones `(month, lat, lon)` como hoy:

| Atributo | Valor |
|---|---|
| `title` | `"Climatología mensual 1993-2016 (GLORYS12) — {var.nombre}"` |
| `periodo_referencia` | el pedido (p. ej. `"1991-2020"`) |
| `periodo_efectivo` | `"1993-2016"` (de `climatology_bounds`) |
| `periodo_fuente` | `"climatology_bounds"` \| `"metadatos"` \| `"respaldo"` |
| `metodo` | «Climatología mensual oficial de GLORYS12 (GLOBAL_MULTIYEAR_PHY_001_030), periodo 1993-2016: media de cada mes del reanálisis a 1/12°, interpolada a {z} m entre los niveles de {a} y {b} m y promediada en bloques de 3 × 3 celdas (mínimo 5 válidas).» |
| `origen_climatologia` | «Climatología oficial de Copernicus Marine (reanálisis GLORYS12, periodo 1993-2016). Se pidió 1991-2020, que Copernicus Marine no publica: se usa 1993-2016. Es otro sistema que el análisis operativo de los datos diarios: parte de la anomalía puede deberse a diferencias entre ambos sistemas.» (sin la frase «Se pidió…» si coinciden; para `zos` añade: «El nivel del mar de ambos sistemas puede tener referencias distintas: la anomalía de zos puede llevar un desplazamiento constante.») |
| `nota_sistema` | la frase de la diferencia de sistema, sola |
| `credito` | `"Climatología GLORYS12, https://doi.org/10.48670/moi-00021."` |
| `producto_climatologia`, `doi_climatologia` | `"GLOBAL_MULTIYEAR_PHY_001_030"`, `"10.48670/moi-00021"` |
| `datos_reales`, `procedencia`, `insignia`, `comment`, `fuente_id`, `profundidad_m` | como en `obtener()` |

Variables: `cell_methods = "time: mean within months time: mean over years"`. Climatología de respaldo: la del simulador más `datos_reales=0`, `procedencia="simulado"`, `insignia`, `aviso` («Climatología simulada: … ({motivo})») y `periodo_referencia` = el pedido (sin `periodo_efectivo`).

### 2.4 Formas de estado, historial, detalle y resumen

`estado_actual() -> dict`:
`{"estado": "operativa"|"degradada"|"sin_conexion", "mensaje": str, "latencia_ms": int, "ultimo_chequeo": str ISO, "fecha_corte": date | None, "definitivo": bool, "modo": "real"|"simulado"}`. En modo `simulado` configurado devuelve el estado simulado anterior (`operativa`, 1 450 ms, 26/10/2026) con el mensaje «Modo simulado (SOME_COPERNICUS_MODO=simulado): no se consulta Copernicus Marine; los datos de esta fuente son simulados.»

`historial_estado(dias)` → lista del más antiguo al más reciente de `{"fecha": "aaaa-mm-dd", "estado": "operativa"|"degradada"|"sin_conexion"|"sin_registro", "origen": "copernicus"|"cache"|None, "origen_texto": str}` (`origen_texto`: «Copernicus Marine (ARCO)», «Caché local», «Sin registro»). En modo `simulado` configurado: el patrón determinista actual (operativa salvo «degradada» hace 5 días) con `origen = None`.

`detalle_estado()` (se fusiona tal cual en el elemento de `GET /api/fuentes/estado`; **no** usa la clave `caminos`, que `fuentes.js` dibuja con textos de CHIRPS):

```python
{
  "modo_sin_red": bool,
  "copernicus": {
    "modo": "real" | "simulado",
    "modo_configurado": "auto" | "simulado" | "real",
    "texto_modo": str,     # «Datos reales (caché local y Copernicus Marine)», «Datos simulados: sin credenciales de Copernicus Marine», «Modo simulado forzado (SOME_COPERNICUS_MODO=simulado)»
    "acceso": {"disponible": bool, "motivo": str},
    "paquete": {"instalado": bool, "version": str | None},
    "credenciales": {"estado": "validas"|"invalidas"|"ausentes"|"sin_verificar"|"error", "texto": str, "verificado": str | None},
    "servicio": {"estado": "operativa"|"degradada"|"sin_conexion"|"sin_registro"|"sin_red"|"no_instalado"|"simulado",
                 "texto_estado": str, "detalle": str, "latencia_ms": int | None, "ultimo_contacto": str | None,
                 "servidor": "Copernicus Marine Data Store (ARCO, s3.waw3-1.cloudferro.com)"},
    "analisis": {"ultimo_dia": str | None, "ultimo_definitivo": str | None, "boletin_semanal": str | None,
                 "fin_pronostico": str | None, "regla": "metadatos" | "calendario", "consultado": str | None,
                 "texto": str},    # «Análisis NRT hasta el 26/10/2026 (boletín del 28/10/2026); mejor análisis hasta el 19/10/2026; el pronóstico no se sirve.»
    "climatologia": {"producto": "GLOBAL_MULTIYEAR_PHY_001_030", "doi": "10.48670/moi-00021",
                     "periodo": str | None, "en_cache": int, "total": 34},   # 34 = 4 variables 3D × 8 niveles + zos + mlotst
    "cache": {"fecha_corte": str | None, "ultimo_definitivo": str | None, "ultimo_nrt": str | None,
              "dias_con_dato": int, "dias_nrt": int, "tamano_bytes": int},
  },
}
```

`resumen() -> dict` (tarjeta de Administración › Sistema):

```python
{
  "fuente": "copernicus_phy", "datos_reales": bool, "modo": str, "modo_configurado": str, "modo_sin_red": bool,
  "estado": str, "mensaje_estado": str, "sincronizacion_activa": bool,
  "fecha_corte": str | None, "ultimo_dia_analisis": str | None, "boletin_semanal": str | None,
  "paquete": {...}, "credenciales": {...}, "servicio": {...},          # mismas formas que en detalle_estado()
  "dias_en_cache": int, "dias_nrt": int, "dias_definitivos": int,
  "conjuntos": [{"variable": str, "nivel": int | None, "dias": int, "desde": str | None, "hasta": str | None,
                 "dias_nrt": int, "dias_completos": int}],             # dias_completos: con el área entera obtenida
  "climatologia": {"periodo": str | None, "en_cache": int, "total": 34,
                   "pares": [{"variable": str, "nivel": int | None}]},
  "conjunto_sincronizado": str,    # «superficie (0 m) de thetao, so, uo, vo, zos y mlotst del último día de análisis; mlotst del golfo de Guayaquil, 7 días»
  "tamano_cache_bytes": int, "segundos_descarga": float, "fragmentos_descargados": int,
  "ultima_sincronizacion": str | None, "resultado_ultima_sincronizacion": dict | None,
  "ultima_verificacion": dict | None, "proxima_sincronizacion": str | None,
  "errores_recientes": [{"momento": str, "mensaje": str}],   # últimos 10, en español (legible)
}
```

Los conteos (`dias_en_cache`, `conjuntos`, `ultimo_*`) se calculan una vez por valor de la marca de generación (§3.6), no en cada llamada.

### 2.5 Cambios de contrato que hace B (y que A da por hechos)

**`backend/configuracion.py`**

```python
INSIGNIA_REALES_COPERNICUS = "Datos reales · Copernicus Marine"
INSIGNIA_REALES = "Datos reales"                      # resultado real con varias fuentes reales
INSIGNIA_MIXTOS = "Datos mixtos: reales y simulados"  # ya no menciona solo CHIRPS
INSIGNIA_CABECERA = "Demostración: datos simulados, salvo precipitación CHIRPS (real)"          # Copernicus simulado
INSIGNIA_CABECERA_COPERNICUS = "Demostración: CHIRPS v3 y Copernicus Marine con datos reales; NOAA OISST simulado"
```

**`backend/conectores/base.py`** (A no lo modifica; usa `ConectorFuente`, `ConectorSimulado`, `EstadoFuente`, `ParametrosConsulta`, `VariableFuente`, `_ATRIBUTOS_*` con esos nombres):

```python
def procedencia_de_datos(conectores, ds_lista=()) -> dict:
    """{"datos_simulados", "procedencia", "insignia", "avisos": list[str]} mirando los Dataset.

    Cada Dataset no nulo cuenta como real si attrs["datos_reales"] == 1 y como simulado si == 0; si no
    trae el atributo, cuenta lo que diga su conector (por attrs["fuente_id"]). Un conector de la lista
    sin ningún Dataset cuenta con su atributo datos_reales. Todo real → «real» (insignia del Dataset o
    del conector si es una sola; si hay varias, cfg.INSIGNIA_REALES); todo simulado → «simulado»;
    si no → «mixto». «avisos»: los attrs["aviso"] distintos, en orden."""
```

- `ConectorSimulado._atributos_globales` añade `"datos_reales": 0` y `"procedencia": "simulado"`.
- `_resumen_procedencia("real", None)` usa `cfg.INSIGNIA_REALES` (no la de CHIRPS).
- `procedencia_de(conectores)` sigue igual (nivel catálogo).

**`backend/motor.py`**

- `procedencia_resultado(conectores, ds_lista)` usa `procedencia_de_datos` y añade a la respuesta `"avisos"` (solo si no está vacío) y `"periodo_referencia_efectivo"` (si algún Dataset de climatología trae `periodo_efectivo`).
- `_info_fuente(conector, ds_lista)`: `datos_reales` e `insignia` según los Dataset; `credito` = créditos distintos de los Dataset unidos con un espacio (datos ANFC + climatología GLORYS12), sin los de Dataset simulados si hay alguno real; si el resultado es real y el crédito no nombra «Copernicus Marine Service Information» (solo la climatología), se antepone «Generated using E.U. Copernicus Marine Service Information; » (decisión 13); copia `origen_datos`, `origen_climatologia`, `aviso` si existen (aunque no haya claves de CHIRPS). `consulta_perfil`, `consulta_cortina` y `consulta_vectores` pasan `[ds]` (hoy no pasan Dataset).
- `nota_datos(conector, ds)`: si `ds.attrs.get("datos_reales") == 0` → `NOTA_SIMULACION` + `aviso`; si es real, como hoy (usa `procesamiento`) y añade `aviso` si lo hay. La frase de días preliminares con el Climate Hazards Center solo para `dias_preliminares > 0` (Copernicus manda 0).
- `nota_climatologia`: para un Dataset simulado de respaldo, « Climatología simulada: {aviso}».
- `_climatologia_ds(conector, variable, periodo, bbox, z, ds_datos=None)`: si `ds_datos.attrs.get("datos_reales") == 0` y `climatologia` acepta `simulada`, la pide con `simulada=True` (anomalías coherentes: simulado menos climatología simulada). Lo usan `consulta_mapa` (anomalía), `consulta_serie` y `mismo_mes_cada_anio`.
- `texto_metodo_anomalia(periodo_ref, efectivo=None)`: «Anomalía = valor − climatología mensual 1993-2016 (se pidió 1991-2020); …». El `subtitulo` («referencia …») y la frase del resumen («periodo de referencia …») usan el periodo efectivo cuando existe.
- La conversión de errores no cambia: `ValueError` → 422; otra excepción de un conector real → 503 «No fue posible obtener datos de Copernicus Marine: {e}».

**`backend/cache.py`**: si la clave contiene `"copernicus_phy"`, se añade `base["firma_copernicus"] = backend.copernicus.firma_cache()` (importación tolerante); `_completa(valor)` devuelve `False` también si `valor.get("avisos")` no está vacío (no se guardan respaldos ni respuestas parciales).

**Respuestas** (`/api/consultas/*`, `/api/panel/widget/datos`, exportaciones): nuevas claves opcionales `avisos: list[str]` y `periodo_referencia_efectivo: str`; `rutas/panel.py::_procedencia` las copia; CSV (`# Periodo de referencia efectivo: …`, `# Aviso: …`), NetCDF (`periodo_referencia_efectivo`, `avisos`) y PDF las muestran.

**Catálogo** (`catalogo.catalogo_json`): `insignia_cabecera`, más `insignia_cabecera_corta` y `titulo_cabecera`, según `catalogo.conector("copernicus_phy").datos_reales` (real → `INSIGNIA_CABECERA_COPERNICUS`, «Real: CHIRPS y Copernicus»; simulado → los textos actuales). `fuentes_reales()` ya es dinámica.

### 2.6 Excepciones y códigos HTTP

| Situación | Lanza | El motor responde |
|---|---|---|
| Nivel no permitido, periodo fuera de cobertura, plan mayor que `MAX_FRAGMENTOS_BAJO_DEMANDA`, modo `real` estricto sin ningún dato | `ValueError` | 422 con el mensaje |
| Descarga bajo demanda que supera `ESPERA_BAJO_DEMANDA_S` | `ErrorDescargaEnCurso` | 503 «…sigue descargándose en segundo plano; vuelva a consultar en unos segundos.» |
| Sin acceso, red caída, credenciales inválidas, formato inesperado | (capturadas dentro del conector) | 200 con respaldo o datos parciales y `avisos` |
| Error inesperado en modo real | `ErrorCopernicus` | 503 |

`estado()`, `historial_estado()`, `detalle_estado()`, `resumen()` y `firma_cache()` nunca lanzan.

### 2.7 Interfaces internas del núcleo (solo A; las usan las pruebas)

```python
# calendario.py (puro)
def boletin_semanal(ahora_utc: datetime, fin_tiempo_arco: date | None = None) -> date: ...
def ultimo_dia_analisis(ahora_utc: datetime, fin_tiempo_arco: date | None = None) -> date: ...   # boletin − 2 días
def tipo_dia(dia: date, boletin: date) -> int: ...   # 2 si dia ≤ W−9; 1 si W−8 ≤ dia ≤ W−2; 0 si dia ≥ W−1 (no se sirve)

# niveles.py
NIVELES_NATIVOS: tuple[float, ...]          # los 15 niveles de §3.4 (valores exactos de describe)
def vecinos(z_some: float) -> tuple[float, float, float]: ...   # (nivel_a, nivel_b, peso_b); z=0 → (0.494, 0.494, 0.0)
def niveles_necesarios(niveles_some: list[float]) -> list[float]: ...
def interpolar(datos: np.ndarray, niveles: np.ndarray, z_some: list[float], eje: int = 1) -> np.ndarray: ...

# malla.py
VENTANA = {"lat": (-6.125, 6.125), "lon": (-95.125, -77.875)}   # bordes de la malla SOME
FILAS_NATIVAS, COLUMNAS_NATIVAS = 147, 207
def indices_nativos(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]: ...  # valida la malla 1/12°; ErrorFormato si no
def agregar_3x3(nativo: np.ndarray, cubierto: np.ndarray) -> tuple[np.ndarray, np.ndarray]: ...
    # (…, 147, 207) → (…, 49, 69) valores y (49, 69) bool «celda SOME obtenida» (sus 9 celdas nativas en la ventana leída)

# almacen.py
def leer(variable: str, nivel: int | None, inicio: date, fin: date, ilat=None, ilon=None) -> Lectura: ...
def guardar(variable: str, nivel: int | None, fechas: list[date], valores: np.ndarray, cubiertas: np.ndarray,
            tipos: list[int], boletin: date) -> int: ...   # celdas-día escritas
def leer_climatologia(variable: str, nivel: int | None) -> ClimatologiaCMEMS | None: ...
def guardar_climatologia(c: ClimatologiaCMEMS) -> None: ...
def ultimo_dia() -> date | None: ...       # último día con alguna celda obtenida (cualquier variable/nivel)
def hay_datos() -> bool: ...
def marca() -> int: ...                    # mtime_ns de la marca de generación

# descarga.py
@dataclass(frozen=True)
class Tramo:
    dataset_id: str; variables: tuple[str, ...]; servicio: str   # "arco-geo-series" | "arco-time-series"
    lat: tuple[float, float]; lon: tuple[float, float]; inicio: date; fin: date
    niveles_nativos: tuple[float, ...] | None; niveles_some: tuple[int, ...] | None; fragmentos: int
def planificar(variables: list[str], niveles_some: list[int] | None, inicio: date, fin: date,
               ilat: np.ndarray, ilon: np.ndarray, solo_faltantes: bool = True) -> list[Tramo]: ...
def ejecutar(tramos: list[Tramo], espera_s: float | None = None) -> dict: ...
```

---

## 3. Flujo de datos y reglas

### 3.1 Productos y datasets (comprobados con `describe` el 30/10/2026)

| Uso | Dataset (versión) | Variables | Fragmentos ARCO |
|---|---|---|---|
| Diario 3D | `cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m` (202406) | `thetao` (`degrees_C`) | geo: tiempo 1, prof. 1, lat 512, lon 2048 · serie: tiempo 134, prof. 2, lat 32, lon 64 |
| Diario 3D | `cmems_mod_glo_phy-so_anfc_0.083deg_P1D-m` (202406) | `so` (`1e-3`) | igual |
| Diario 3D | `cmems_mod_glo_phy-cur_anfc_0.083deg_P1D-m` (202406) | `uo`, `vo` (`m s-1`) | igual |
| Diario 2D | `cmems_mod_glo_phy_anfc_0.083deg_P1D-m` (202406) | `zos`, `mlotst` (`m`) | geo: 1, 512, 2048 · serie: 134, 32, 32 |
| Climatología | `cmems_mod_glo_phy_my_0.083deg-climatology_P1M-m` (202311) | `thetao`, `so`, `uo`, `vo`, `zos`, `mlotst`, `climatology_bounds` | geo: 1, 1, 512, 2048 · serie: 12, prof. 4, 128, 128 |
| Opcional (máscara) | `cmems_mod_glo_phy_anfc_0.083deg_static`, parte `bathy` | `deptho` | static: lat 512, lon 1024 |

Eje de tiempo de ANFC: desde 01/06/2022 hasta hoy + 9 días (hoy: 08/11/2026); origen de los bloques de 134 días: 01/06/2022 (un bloque empieza justo el 26/10/2026). Unidades: `degrees_C` = `degC` CF; no hay conversiones. A toma la geometría de fragmentos y el origen de `describe` (`chunking_length`, `minimum_value`) y usa estos valores como respaldo si no hay red.

### 3.2 Fecha de corte: solo análisis

Calendario del PUM 001-024 (§«Processing information» y «Catalogue updating»): cada día se publica un pronóstico de 10 días (base 00 UTC, días `B … B+9`) y un día de «simulación» sin asimilación (`B−1`); **cada jueves** se publica el análisis semanal con asimilación del boletín del **miércoles** `W`: análisis NRT de −192 a −24 h (días `W−8 … W−2`) y mejor análisis de −360 a −192 h (días `W−15 … W−9`), que ya no cambia.

- **Boletín semanal `W`** con metadatos vigentes (≤ 13 h): el miércoles más reciente con `W + 10 días ≤ fin_tiempo_arco` (el pronóstico del jueves `W+1` llega hasta `W+10`; el análisis se publica junto con él). Sin metadatos: el miércoles más reciente cuyo jueves `W+1` a las 18:00 UTC ya pasó.
- **Último día de análisis** = `W − 2`. **Tipo de día**: `≤ W−9` → 2 «mejor análisis» (definitivo); `W−8 … W−2` → 1 «análisis NRT» (provisional: Mercator lo reemplaza la semana siguiente); `≥ W−1` → 0, no se sirve (simulación o pronóstico).
- Ejemplos: 30/10/2026 con `fin_tiempo_arco` = 08/11 → `W` = 28/10 → corte **26/10/2026**, NRT 20/10–26/10, mejor análisis hasta 19/10. Jueves 05/11 antes de la actualización (`fin` = 13/11) → sigue 26/10; después (`fin` = 14/11) → `W` = 04/11 → corte 02/11.
- **Fecha de corte efectiva** (`fecha_corte()`): con acceso disponible, `ultimo_dia_analisis()`; sin acceso, `almacen.ultimo_dia()`; en modo simulado, None (el conector usa la del simulador).
- El planificador **nunca** pide días posteriores al corte y nunca guarda días posteriores aunque lleguen dentro de un fragmento de 134 días.

### 3.3 Regrillado 1/12° → 0.25°

- Malla nativa: centros en `−80 + k/12` (lat) y `−180 + k/12` (lon), ambos ascendentes. Cada celda SOME (centro múltiplo de 0.25°, bordes a ±0.125°) contiene exactamente **3 × 3 = 9** centros nativos (ningún centro cae en un borde: distan ≥ 1/24°).
- Ventana del área: bordes 6.125° S–6.125° N y 95.125° O–77.875° O → **147 × 207** celdas nativas (centros −6.0833 … 6.0833 y −95.0833 … −77.9167); filas nativas 887–1033 y columnas 1019–1225 contando desde −80° y −180°.
- `open_dataset(..., minimum_latitude, maximum_latitude, minimum_longitude, maximum_longitude, coordinates_selection_method="inside")` con la ventana pedida; luego se **validan** las coordenadas (redondeo a 1/12°, contigüidad; si no, `ErrorFormato`), no se suponen.
- Valor SOME = media de las celdas nativas válidas de su bloque 3 × 3 si hay **al menos 5 de 9** (mayoría, como los 13 de 25 de CHIRPS); si no, NaN. Primero se interpola en la vertical en cada columna nativa (§3.4) y después se promedia en la horizontal.
- Ventanas parciales (servicio de series): los datos se colocan en un lienzo 147 × 207 con máscara «leído»; solo se guardan las celdas SOME cuyos 9 centros nativos se leyeron.

### 3.4 Profundidad

Niveles SOME `cfg.PROFUNDIDADES` desde los niveles del modelo (comunes a ANFC y GLORYS12), interpolación lineal `v(z) = v_a + (v_b − v_a)·(z − a)/(b − a)`, con dato solo si los dos niveles vecinos tienen dato (si no, NaN: fondo o tierra):

| SOME | Nivel inferior (peso) | Nivel superior (peso) |
|---|---|---|
| 0 m | 0.494 m (único: 0 m = nivel de 0.494 m) | — |
| 5 m | 3.819 m (0.0621) | 5.078 m (0.9379) |
| 10 m | 9.573 m (0.7669) | 11.405 m (0.2331) |
| 20 m | 18.496 m (0.5152) | 21.599 m (0.4848) |
| 30 m | 29.445 m (0.8887) | 34.434 m (0.1113) |
| 50 m | 47.374 m (0.6870) | 55.764 m (0.3130) |
| 75 m | 65.807 m (0.2369) | 77.854 m (0.7631) |
| 100 m | 92.326 m (0.5591) | 109.729 m (0.4409) |

Niveles nativos necesarios: 15. Se abre el rango 0–110 m y se seleccionan solo los necesarios **antes** de `.load()` (`.sel(depth=…, method="nearest", tolerance=0.01)`): en el servicio geo (1 nivel por fragmento) se leen solo esos niveles; en el de series (2 niveles por fragmento, pares contados desde el fondo) los 8 niveles tocan 12 pares; en la climatología (4 niveles por fragmento), 7 cuartetos.

### 3.5 Plan de descarga

Para cada variable y conjunto de niveles faltantes se calcula el costo en fragmentos de las dos opciones y se elige el menor (empate → geo), con `service=` explícito:

- **geo** (`arco-geo-series`): ventana = área SOME completa (2 fragmentos de 512 × 2048 cubren las 147 filas, porque hay un borde de fragmento en 5.333° N); días exactos. Costo = días × niveles nativos × 2.
- **serie** (`arco-time-series`): ventana = celdas pedidas **ampliada a los bordes de fragmento** (32 × 64 en 3D, 32 × 32 en 2D, 128 × 128 en la climatología; recortada a la ventana SOME) y días ampliados a los bloques de 134 días (recortados a `[FECHA_INICIO, corte]`): lo leído de más no cuesta más y se guarda. Costo = bloques × fragmentos de profundidad × fragmentos espaciales.

Costos de referencia (fragmento sin comprimir: geo 4 MiB; serie 3D 2.1 MiB; serie 2D 0.55 MiB; climatología 3 MiB; comprimidos suelen ser bastante menos, A lo mide con credenciales):

| Consulta | Servicio | Fragmentos |
|---|---|---|
| Mapa de un día a 0 m (`thetao`), área completa | geo | 2 |
| Corrientes de un día a 0 m (`uo` + `vo`) | geo | 4 |
| Perfil de un día en (−2, −85), 8 niveles | serie | 12 por variable (y trae el bloque de 134 días de esa ventana) |
| Cortina de 90 días en el mismo punto | serie | 24 por variable |
| Serie de 12 meses de `thetao` 0 m en Niño 1+2 | serie | 48 |
| Serie de 12 meses de `so` 10 m en el golfo de Guayaquil | serie | 16 |
| Climatología de una variable 3D en un nivel SOME | serie | 9 a 18 |
| Lectura de prueba (`zos`, un punto, un día) | serie | 1 |

Ejecución (`descarga.ejecutar`): un bloqueo de descarga por `dataset_id` (en el proceso); tras tomarlo se vuelve a mirar la caché; la lectura corre en un hilo con tiempo máximo y, si una consulta deja de esperar, el hilo sigue y guarda lo que llegue. Un fallo de red marca el servicio «caído» durante `CAIDO_S` (las consultas no reintentan; la sincronización sí).

### 3.6 Caché local `datos/copernicus/`

```
datos/copernicus/
  diario/<variable>/<nivel>/AAAA-MM.npz   variable ∈ thetao, so, uo, vo, zos, mlotst; nivel ∈ z000 z005 z010 z020 z030 z050 z075 z100 | sup
  climatologia/<variable>_<nivel>.npz
  mascara.npz                             (opcional, §6)
  estado.json
  generacion                              marca vacía: se reescribe en cada escritura (firma de la caché)
  .bloqueos/
```

- Diario `AAAA-MM.npz`: `valores` (días_del_mes, 49, 69) float32 (NaN = tierra/fondo o no obtenido); `tipo` (días, 49, 69) int8 (0 no obtenido, 1 NRT, 2 mejor análisis); `obtenido` (días,) `U19`; `boletin` (días,) `U10`; `dataset_id`, `version` (escalares de texto); `formato` int8 = 1.
- Climatología `<variable>_<nivel>.npz`: `valores` (12, 49, 69) float32; `periodo` («1993-2016»), `anio_inicio`, `anio_fin`, `fuente_periodo`, `limites` (12, 2) `U10`, `obtenido`, `dataset_id`, `version`.
- **Regla de reemplazo** por celda: se escribe si `tipo_nuevo > tipo_guardado`, o si ambos son NRT y el boletín nuevo es posterior; un mejor análisis nunca se pisa con un NRT.
- Escritura atómica (temporal + `fsync` + `os.replace` con reintentos), bloqueo por archivo entre procesos (`.bloqueos/<variable>-<nivel>-AAAA-MM.lock`) releyendo del disco dentro del bloqueo, caché en memoria por *mtime*, archivos dañados apartados como `.danado` (igual que `chirps/almacen.py`).
- `firma_cache()` = `f"{marca()}:{modo()}:{int(acceso_disponible())}:{fecha_corte()}"` (sin recorrer los archivos).
- Rendimiento: un mapa de un día lee 1 archivo mensual por variable y nivel; una serie de 12 meses, 12 o 13; con la caché en memoria, muy por debajo de los 2 s y 3 s de la prueba de humo.

### 3.7 Climatología GLORYS12 y periodo efectivo

- Por (variable, nivel SOME): se leen los niveles nativos necesarios (servicio de series), se interpola, se regrilla y se guarda una vez (no cambia).
- `climatology_bounds` (horas desde 1950-01-01, forma 12 × 2): `anio_inicio = min(inicio).year`; `anio_fin = max(fin).year` (o el año anterior si el fin es exactamente el 1 de enero). Si no viene en el Dataset abierto, se abre aparte (sin recorte espacial); si eso falla, se usa `admp_climatology_bounds` de los metadatos STAC del dataset (`fuente_periodo = "metadatos"`); como último recurso, `PERIODO_CLIMATOLOGIA_RESPALDO = "1993-2016"` (`"respaldo"`, verificado el 30/10/2026).
- Los tres periodos del catálogo (1991-2020, 1981-2010, 1993-2022) devuelven la misma climatología oficial y la respuesta lo dice: `periodo_referencia` (pedido) y `periodo_referencia_efectivo` = «1993-2016», en método, subtítulo, resumen, CSV, NetCDF y PDF.
- No se calcula una climatología propia con los mensuales de GLORYS12 (demasiado volumen para mañana); queda como mejora.

### 3.8 Consulta: llenado bajo demanda y respaldo simulado

`acceso_disponible()` = paquete instalado ∧ ¬`sin_red()` ∧ modo configurado ≠ `simulado` ∧ credenciales presentes ∧ credenciales no verificadas como inválidas ∧ sin caída en los últimos `CAIDO_S`. Si las credenciales están presentes pero sin verificar, la primera descarga verifica en línea (~1 s, más ~3.8 s del primer `import`).

`obtener(p)` en modo real:

1. Valida variable, niveles y área; acota fechas a `[FECHA_INICIO, fecha_corte()]`.
2. Lee la caché de cada componente y nivel; calcula lo que falta en las celdas pedidas.
3. Si falta algo y hay acceso: planifica (§3.5). Si el plan supera `MAX_FRAGMENTOS_BAJO_DEMANDA` → `ValueError` (422: «…requiere descargar demasiados datos para hacerlo en el momento; acote el periodo o ejecute python -m backend.herramientas.preparar_copernicus…»). Si no, ejecuta con `espera_s = ESPERA_BAJO_DEMANDA_S`; si se agota → `ErrorDescargaEnCurso` (503); si falla por acceso, red o formato → registra el error y sigue con el motivo.
4. Relee la caché y decide:

| Cobertura de lo pedido | Modo `auto` | Modo `real` (estricto) |
|---|---|---|
| 100 % | Dataset real | Dataset real |
| > 0 % y < 100 % | Dataset real con NaN donde falta, `dias_faltantes` y `aviso` | igual |
| 0 % | **Respaldo**: Dataset del simulador con `datos_reales=0`, `respaldo_simulado=1` y `aviso` | `ValueError` (422) con el motivo |

`climatologia()` sigue la misma lógica por componente (100 % o nada: la climatología se descarga entera); en modo `auto`, sin climatología real disponible → climatología simulada con `aviso` (el resultado queda «mixto» y el método lo dice). En modo simulado (configurado o automático), todo sale del simulador como hoy, con `datos_reales=0`.

### 3.9 Sincronización en segundo plano

Hilo *daemon* `copernicus-sincronizacion` (no arranca con `SOME_COPERNICUS_SIN_RED=1`, `SOME_COPERNICUS_SIN_SINCRONIZACION=1` o modo `simulado`); primer ciclo a los `ESPERA_INICIAL_S` = 20 s del arranque; luego cada 6 h, o cada 30 min si algo falló o faltan credenciales (detectar un `copernicusmarine login` hecho con el servidor en marcha no exige reiniciarlo). Cada ciclo, con `try/except` y registro en `estado.json`:

1. Metadatos (`describe` del dataset 2D, público, ~3.5 s): fin del eje de tiempo, `arco_updated_date`, `arco_updating_start_date`, geometría de fragmentos.
2. Si no hay paquete o credenciales: registra la verificación (`sin_conexion` + motivo) y termina.
3. Credenciales: `copernicusmarine.login(check_credentials_valid=True)` (nunca pregunta; ver §3.11).
4. Lectura de prueba: `zos` en la celda de (−2, −85) del último día de análisis (1 fragmento de series) → latencia total (autenticación, metadatos y lectura).
5. **Conjunto liviano**: superficie (0 m) de `thetao`, `so`, `uo`, `vo`, `zos` y `mlotst` del último día de análisis en el área completa (12 fragmentos geo) y `mlotst` del golfo de Guayaquil de los últimos 7 días (≈ 4 fragmentos 2D); solo lo que falte. `SOME_COPERNICUS_DIAS_SINCRONIZACION=N` amplía la superficie a N días (0 = solo verificar).
6. Paso de NRT a mejor análisis: vuelve a pedir los días del conjunto liviano en caché como NRT cuyo día ya es `≤ W−9`. El resto de los NRT en caché se sirve como NRT (se dice) hasta que una consulta o `preparar_copernicus` lo renueve.
7. Climatología de superficie faltante (6 variables a 0 m).
8. `registrar_sincronizacion(resumen)`.

Los análisis nuevos llegan una vez por semana (jueves): en un día normal el ciclo solo verifica.

### 3.10 Verificaciones y estados RF-21

| Estado | Condición |
|---|---|
| `operativa` | Paquete instalado, credenciales válidas, lectura de prueba correcta con latencia < `LATENCIA_ALTA_MS` (20 000 ms), verificación vigente (≤ 13 h), metadatos sin atraso (`W` de metadatos ≥ `W` de calendario) y última sincronización sin errores. |
| `degradada` | Credenciales válidas pero: lectura lenta (≥ 20 000 ms) o fallida con red, metadatos atrasados o dataset «actualizándose», errores en la última sincronización, verificación no vigente o en curso (no definitivo). |
| `sin_conexion` | `SOME_COPERNICUS_SIN_RED=1`, paquete no instalado, sin credenciales, credenciales inválidas, sin conexión con la autenticación o con ARCO. Se sirve la caché; lo demás, con respaldo simulado señalado. |

Mensajes (se completan con la fecha de corte de la caché):

- «Sin credenciales de Copernicus Marine: ejecute «copernicusmarine login» en una consola de este equipo (la cuenta es gratuita). Mientras tanto se sirve la caché local (hasta el 26/10/2026); lo que no esté en ella queda sin dato o, si falta todo, se responde con datos simulados, señalados como tales.»
- «Las credenciales de Copernicus Marine no son válidas (verificado el 30/10/2026 10:15): vuelva a ejecutar «copernicusmarine login --force-overwrite»; …»
- «El paquete copernicusmarine no está instalado: pip install -r requirements-copernicus.txt; …»
- «Modo sin red (SOME_COPERNICUS_SIN_RED=1): no se consulta Copernicus Marine; se sirve la caché local hasta el …»
- «Copernicus Marine responde con normalidad (lectura de prueba en 6 200 ms). Último día de análisis: 26/10/2026 (boletín semanal del 28/10/2026).»
- «Copernicus Marine responde con retraso (lectura de prueba en 24 500 ms); …» · «No se pudo conectar con Copernicus Marine (último intento: …): {motivo}; …»

Historial de 14 días: el peor estado de cada día según las verificaciones persistidas (`sin_registro` si no hubo ninguna); en modo sin red, el día de hoy figura `sin_conexion` sin persistirlo (como CHIRPS).

### 3.11 Credenciales: reglas duras y guardia anti-*prompt*

- El código **nunca** pide, escribe, imprime, registra ni guarda usuario o contraseña, ni los pasa como argumento (`username`/`password` siempre `None`); el usuario los guarda él mismo con `copernicusmarine login` (archivo en `C:\Users\<usuario>\.copernicusmarine\`).
- `credenciales_presentes()` (sin red, sin leer contenidos): variables de entorno `COPERNICUSMARINE_SERVICE_USERNAME` y `…_PASSWORD` definidas, o existencia de `<COPERNICUSMARINE_CREDENTIALS_DIRECTORY o ~>/.copernicusmarine/.copernicusmarine-credentials`, `~/motuclient/motuclient-python.ini` o `~/_netrc`.
- `verificar_credenciales()` usa solo `copernicusmarine.login(check_credentials_valid=True)` (no pregunta nunca: comprobado en el código del *toolbox*); devuelve `(True | False | None, latencia_ms, error)` y nunca registra el nombre de usuario.
- **Guardia anti-*prompt***: al importar el *toolbox*, `acceso.importar()` reemplaza `copernicusmarine.core_functions.credentials_utils._retrieve_credential_from_prompt` por una función que lanza `ErrorSinCredenciales`. Sin ella, `open_dataset` sin credenciales llamaría a `click.prompt` y dejaría colgado un hilo del servidor. Si el atributo no existiera (otra versión), se registra un aviso y las descargas exigen credenciales verificadas como válidas.
- Nunca se llama a `login()` sin `check_credentials_valid=True` ni a `get`/`subset` con `request_file` o confirmaciones.

### 3.12 Modos y variables de entorno

`modo()`: `simulado` si `SOME_COPERNICUS_MODO=simulado`; `real` si `SOME_COPERNICUS_MODO=real`; en `auto`, `real` si la caché tiene datos **o** el acceso es posible (paquete, red, credenciales presentes y no inválidas); si no, `simulado`.

| Variable | Efecto |
|---|---|
| `SOME_COPERNICUS_MODO` | `auto` (por defecto), `simulado` (el simulador anterior, sin red, estado e historial simulados) o `real` (estricto, sin respaldo simulado). |
| `SOME_COPERNICUS_SIN_RED=1` | No toca la red: solo caché; estado `sin_conexion`; sin sincronización. |
| `SOME_COPERNICUS_SIN_SINCRONIZACION=1` | No arranca el hilo; las consultas siguen llenando bajo demanda. |
| `SOME_COPERNICUS_ESPERA_S` | Espera máxima de una descarga dentro de una consulta (por defecto 40). |
| `SOME_COPERNICUS_DIAS_SINCRONIZACION` | Días de superficie del conjunto liviano (por defecto 1). |
| `COPERNICUSMARINE_HTTPS_TIMEOUT` / `…_RETRIES` | Del *toolbox*; A fija 20 s y 2 reintentos si no están definidas (por defecto el *toolbox* usa 60 s y 5). |

Constantes (`constantes.py`): `LATENCIA_ALTA_MS = 20000`, `VERIFICACION_VIGENTE_H = 13`, `METADATOS_VIGENTES_H = 13`, `CAIDO_S = 300`, `ESPERA_BAJO_DEMANDA_S = 40`, `MAX_FRAGMENTOS_BAJO_DEMANDA = 150`, `MIN_VALIDAS_3X3 = 5`, `INTERVALO_SINCRONIZACION_S = 6 h`, `INTERVALO_REINTENTO_S = 30 min`, `ESPERA_INICIAL_S = 20`, `PERIODO_CLIMATOLOGIA_RESPALDO = "1993-2016"`.

### 3.13 Herramienta `backend/herramientas/preparar_copernicus.py`

```
python -m backend.herramientas.preparar_copernicus                    # verificación + escenarios de la demo
python -m backend.herramientas.preparar_copernicus --verificar        # solo credenciales, metadatos y latencia
python -m backend.herramientas.preparar_copernicus --estimar          # fragmentos y MiB por escenario, sin descargar
python -m backend.herramientas.preparar_copernicus --escenarios perfil,comparacion
python -m backend.herramientas.preparar_copernicus --climatologia-completa
python -m backend.herramientas.preparar_copernicus --variables thetao,so --profundidades 0,10 --recorte nino12 --desde 2026-10-01 --hasta 2026-10-26
```

Nunca pregunta credenciales: sin ellas termina con código 2 y el mensaje «Sin credenciales de Copernicus Marine: ejecute «copernicusmarine login»…». Código 1 si hubo errores; 0 si todo bien. Barra de progreso y resumen por escenario (fragmentos, segundos, días por tipo). Escenarios (`C` = último día de análisis):

| Escenario | Contenido | Fragmentos aprox. |
|---|---|---|
| `superficie` | 6 variables a 0 m, área completa, de `C−29` a `C` (los 30 días de la barra de tiempo) | ≈ 360 con la caché vacía |
| `perfil` | `thetao` y `so`, 8 niveles, punto 85° O / 2° S (ventana de fragmento), `C−89` a `C` (perfil, cortina y boletín ERFEN); `mlotst` en el punto | 48 + 2 (serie) |
| `comparacion` | `thetao` 0 m, Niño 1+2, `C−364` a `C` (comparación con NOAA) | 48 (serie) |
| `capa_mezcla` | `mlotst`, golfo de Guayaquil, `C−89` a `C` (widget del panel y series) | ≈ 8 (serie 2D) |
| `humo` | `so` 10 m golfo `C−364` a `C` (prueba de rendimiento); `thetao` 20 m golfo `C−29` a `C` (informe de la prueba de humo) | 16 + 4 (serie) |
| `climatologia` | 6 variables a 0 m, `so` 10 m y `thetao` 20 m | ≈ 72 (serie) |
| `--climatologia-completa` | 4 variables 3D × 8 niveles + `zos` + `mlotst` | ≈ 270 (serie) |

Todo el conjunto por defecto, con la caché vacía: unos 560 fragmentos (~1.9 GiB sin comprimir; comprimidos, bastante menos), de los que unos 360 son del escenario `superficie`; con la caché ya preparada solo se descarga lo que falta. Se ejecuta la noche anterior a la demo; `--estimar` muestra el cálculo antes de descargar.

---

## 4. Archivos por agente (sin solapes)

**Agente A — núcleo**

| Archivo | Acción |
|---|---|
| `backend/copernicus/__init__.py`, `constantes.py`, `acceso.py`, `calendario.py`, `malla.py`, `niveles.py`, `almacen.py`, `descarga.py`, `climatologia.py`, `estado.py`, `sincronizacion.py`, `prueba_copernicus.py` | nuevos |
| `backend/conectores/copernicus.py` | reescrito (§1.2, §2.2) |
| `backend/conectores/__init__.py` | solo la documentación (Copernicus real) |
| `backend/herramientas/preparar_copernicus.py` | nuevo |
| `requirements-copernicus.txt` | nuevo: `copernicusmarine>=2.5,<3` con comentarios como `requirements-replica.txt` |
| `requirements.txt` | una línea de comentario que remite al opcional |
| `ejecutar.bat`, `ejecutar.ps1` | instalan `requirements-copernicus.txt` como la réplica (si falla, aviso y siguen); el texto de bienvenida dice qué es real y recuerda «copernicusmarine login» |

**Agente B — integración**

| Archivo | Acción |
|---|---|
| `backend/configuracion.py` | insignias de §2.5 |
| `backend/conectores/base.py` | `procedencia_de_datos`, atributos del simulador, insignia real genérica |
| `backend/motor.py` | §2.5 |
| `backend/cache.py` | firma de Copernicus y regla de `_completa` |
| `backend/catalogo.py` | cabecera dinámica, documentación |
| `backend/app.py` | importación tolerante de `backend.copernicus`, `iniciar_sincronizacion`/`detener_sincronizacion` (como CHIRPS), descripción de la API y mensaje de arranque |
| `backend/rutas/admin.py` | `"copernicus": _resumen_copernicus()` (tolerante, como `_resumen_chirps`) |
| `backend/rutas/catalogo.py`, `backend/rutas/panel.py` | documentación; `_procedencia` copia `avisos` y `periodo_referencia_efectivo` |
| `backend/exportar.py`, `backend/reportes.py` (y `graficos.py` si hace falta) | periodo efectivo, avisos, crédito combinado, textos sin «Copernicus simulado» |
| `frontend/js/app.js`, `frontend/index.html`, `frontend/js/modulos/fuentes.js`, `admin.js`, `acerca.js`, `acceso.js`, `consultas.js` (y `panel.js`, `perfiles.js`, `comparar.js` si muestran textos de procedencia) | insignias y textos dinámicos; tarjeta de Copernicus con `f.copernicus` en «Estado de fuentes» («Conexión real», estado, último día de análisis, latencia, historial, caché); tarjeta «Copernicus Marine · caché y sincronización» en Sistema; «Acerca de» con Copernicus real (crédito «E.U. Copernicus Marine Service Information», productos y DOI 10.48670/moi-00016 y 10.48670/moi-00021) |
| `README.md`, `docs/ESPECIFICACION-PROTOTIPO.md` | preparación (login, `preparar_copernicus`), variables de entorno, qué es real, contrato, estado, limitaciones |
| `backend/pruebas/prueba_humo.py` | §5.2 |

Lo que cada uno espera del otro: B programa contra §2.1–2.4 y la tabla de §2.3 (puede probar con `SOME_COPERNICUS_MODO=simulado` y con un Dataset falso con `datos_reales=0` mientras A termina). A programa contra los nombres actuales de `base.py` y lee las insignias con `getattr(cfg, …, valor_por_defecto)`; los Dataset de A ya traen `datos_reales`, así que con el `base.py` actual el respaldo se vería «real» solo hasta que B integre `procedencia_de_datos` (por eso B lo hace primero).

---

## 5. Plan de pruebas

### 5.1 `python -m backend.copernicus.prueba_copernicus` (A)

Sin red, con carpetas temporales (redirigiendo las rutas de `constantes`/`almacen`/`estado` como `prueba_chirps`) y un módulo `copernicusmarine` falso inyectado en `acceso` (registra cada llamada; `login` devuelve True/False o lanza; `open_dataset` devuelve un Dataset sintético en la malla nativa con `time`, `depth`, `latitude`, `longitude`):

1. **Calendario**: `W` y corte para fechas fijas (30/10/2026 → 26/10; jueves 29/10 10:00 UTC sin metadatos → 19/10; con `fin` 08/11, 13/11, 14/11); `tipo_dia` en los bordes `W−9`, `W−8`, `W−2`, `W−1`.
2. **Malla**: ventana 147 × 207; media 3 × 3 con patrón conocido; mínimo 5 de 9; lienzo parcial; `ErrorFormato` con coordenadas desplazadas.
3. **Niveles**: pesos de la tabla de §3.4; 0 m = 0.494 m; NaN si falta un vecino; forma (t, z, y, x).
4. **Almacén**: escritura y lectura por celda; reemplazo 1 → 2, nunca 2 → 1, NRT con boletín posterior; varios meses; archivo dañado apartado; la marca cambia al escribir; escritura concurrente desde un subproceso.
5. **Plan**: geo para el mapa de un día, serie para el perfil y la serie de 12 meses; ventanas alineadas a fragmentos; nunca días > corte; `MAX_FRAGMENTOS_BAJO_DEMANDA` → `ValueError`.
6. **Acceso y credenciales**: la guardia anti-*prompt* queda instalada (llamar a la función de *prompt* lanza `ErrorSinCredenciales`); `open_dataset` recibe recorte, días, rango de profundidad y `service` correctos y nunca `username`/`password`; con `SOME_COPERNICUS_SIN_RED=1` el módulo falso no recibe ninguna llamada.
7. **Conector**: Dataset real con dimensiones, coordenadas y atributos de §2.3 (incluida «corrientes» con `uo` y `vo`); cobertura parcial → NaN + `aviso`; sin acceso y sin caché → respaldo con `datos_reales=0`; modo `real` estricto → `ValueError`; modo `simulado` → mismos valores que el simulador anterior; nivel 15 m → `ValueError`; `datos_reales`/`insignia` dinámicos.
8. **Estado**: sin credenciales → `sin_conexion` con «Sin credenciales de Copernicus Marine»; lectura rápida → `operativa`; lenta → `degradada`; error de red → `sin_conexion`; modo sin red; historial de 14 días con `sin_registro`; `estado()`/`resumen()` no lanzan con `estado.json` dañado.
9. **Climatología**: `climatology_bounds` 1993-01-01/2016-12-31 → «1993-2016»; pedido 1991-2020 → atributos de §2.3; respaldo y `simulada=True`.
10. **Procedencia**: si existe `base.procedencia_de_datos`, real/simulado/mixto con Dataset reales y de respaldo; si no, «OMITE».
11. **Bloque real opcional** (solo si `login(check_credentials_valid=True)` es True y no hay modo sin red): lectura de prueba, mapa de un día de `thetao` 0 m (15–31 °C), perfil en (−2, −85) decreciente, `so` 30–36, corte = `W−2` según metadatos; imprime tiempos. Sin credenciales: «OMITE» con el motivo.

### 5.2 `python -m backend.pruebas.prueba_humo` (B)

- `setdefault("SOME_COPERNICUS_SIN_SINCRONIZACION", "1")` como CHIRPS; lee `copernicus.modo` y `copernicus.acceso.disponible` de `GET /api/fuentes/estado` y fija las expectativas:
  - **modo `simulado`**: consultas de Copernicus «simulado»; estado `sin_conexion` con el motivo (o `operativa` si `SOME_COPERNICUS_MODO=simulado`); `fuentes_reales == ["chirps_v3"]`.
  - **modo `real` con acceso**: consultas de Copernicus «real» con `fuente.datos_reales` True y crédito con DOI 10.48670/moi-00016; NOAA frente a Copernicus «mixto»; informe de Copernicus «real»; boletín ERFEN «mixto»; `fuentes_reales == ["copernicus_phy", "chirps_v3"]`; insignia del catálogo «mixto» (NOAA simulado).
  - **modo `real` sin acceso** (solo caché): cada respuesta «real» o «simulado»; si «simulado», `avisos` no vacío; nunca «real» con un Dataset de respaldo.
- Fechas de Copernicus relativas a su fecha de corte `C` (como ya se hace con CHIRPS), dentro de los escenarios de §3.13: perfil en `C`, cortina `C−56…C`, comparación `C−364…C`, informe `thetao` 20 m golfo `C−29…C`, rendimiento `so` 10 m golfo `C−364…C`.
- Periodo de referencia efectivo presente en una serie de Copernicus real y en su CSV.
- Rendimiento: con Copernicus real, una consulta de calentamiento sin cronometrar (llena la caché) y `cache.cache_consultas.limpiar()` antes de medir; los umbrales siguen siendo < 2 s y < 3 s (no se debilitan).
- Sin cambios en el resto de las verificaciones.

### 5.3 Pruebas existentes y de punta a punta

- Deben seguir pasando: `python -m backend.pruebas.prueba_humo`, `python -m backend.chirps.prueba_chirps`, `python -m backend.chirps.prueba_espejo` (CHIRPS no se modifica; ojo con el cambio de la insignia real por defecto en `base.py`).
- Punta a punta en el puerto **8061** (`python -m uvicorn backend.app:app --host 127.0.0.1 --port 8061`), apagándolo al terminar. **No** detener ni reiniciar el servidor del usuario en el 8050. Temporales en `…\scratchpad\copernicus\<subcarpeta>`. No borrar `datos/` ni `datos/chirps/`.
- Hoy no hay credenciales (no existe `~\.copernicusmarine`): las pruebas reales quedan «OMITE» hasta que el usuario ejecute él mismo `copernicusmarine login`.

---

## 6. Riesgos para la demo de mañana y mitigación

| Riesgo | Mitigación |
|---|---|
| El usuario no alcanza a hacer `copernicusmarine login` | Modo `auto`: con la caché vacía Copernicus sigue simulado y lo dice en «Estado de fuentes», cabecera e insignias; nada se rompe. |
| Red lenta o caída en la sala | `preparar_copernicus` la noche anterior (escenarios de §3.13); `SOME_COPERNICUS_SIN_RED=1` sirve la caché; lo que falte, respaldo simulado señalado. |
| Un hilo del servidor se queda esperando una contraseña | Guardia anti-*prompt* + descargas solo con credenciales presentes y no inválidas. |
| Descargas lentas (fragmentos de 4 MiB, ~3.5 s de metadatos y ~1 s de autenticación por apertura) | Plan por número de fragmentos, ventanas alineadas, espera de 40 s con descarga que sigue en segundo plano (503 claro), tope de 150 fragmentos, sincronización liviana y escenarios precargados. |
| `import copernicusmarine` tarda ~3.8 s | Importación perezosa: nunca en el arranque ni en lecturas de la caché. |
| Tiempos de espera del *toolbox* de 60 s × 5 reintentos | `COPERNICUSMARINE_HTTPS_TIMEOUT=20` y `…_RETRIES=2`; lectura en hilo con tiempo máximo; caída recordada 5 min. |
| Servir pronóstico como dato observado | Corte `W−2` por metadatos o calendario; el planificador y el almacén descartan todo día > corte. Riesgo residual: si el análisis semanal llegara a ARCO después que el pronóstico del jueves, unas horas de diferencia; mitigable exigiendo `W + 11 ≤ fin` (constante). |
| Climatología de otro periodo y de otro sistema | Se declara siempre el periodo efectivo (1993-2016) y la diferencia reanálisis/análisis; nota específica para `zos` (referencias distintas del nivel medio). |
| Días NRT que Mercator reemplaza | Tipo por celda; la sincronización renueva el conjunto liviano; el resto se sirve como NRT y se dice. |
| Celdas costeras con pocas celdas nativas de mar | Mínimo 5 de 9; el método lo documenta; opcional: `mascara.npz` desde `deptho` (4 fragmentos, una vez) para no descargar puntos en tierra (p. ej. el perfil en tierra de la prueba de humo). |
| Puntos en tierra o recortes sin mar | El motor ya responde 422 cuando todo es NaN. |
| Ancho de banda de la sincronización durante la demo | Los análisis nuevos llegan solo los jueves: tras preparar la caché, el sábado la sincronización solo verifica. |
| Cambio de versión del dataset o del *toolbox* | Versión no fijada pero registrada; validación de malla y niveles (`ErrorFormato` → respaldo señalado); `copernicusmarine>=2.5,<3`. |
| El archivo móvil de ANFC (~2 años) deja de cubrir el 01/10/2023 | Lo ya guardado se conserva; lo no disponible, sin dato o respaldo con aviso. |
| Cambios de A y B que no encajan | Interfaces y atributos fijados en §2; B integra primero `procedencia_de_datos`; ambos corren las cuatro pruebas antes de entregar. |
