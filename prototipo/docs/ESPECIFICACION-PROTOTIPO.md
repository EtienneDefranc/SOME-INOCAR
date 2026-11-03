# SOME — Especificación del prototipo demostrativo (Fase 1, demo local)

Prototipo local del **SOME, Sistema de Observación del Mar Equinoccial**, para mostrar al INOCAR cómo funcionará el sistema: inicio de sesión, panel personal, constructor de consultas, visor de mapas, series, perfiles, climatologías y anomalías, comparaciones, exportación, reportes PDF, «Mis vistas», estado de las fuentes y administración.

Este documento es el **contrato** entre las partes del prototipo (motor de datos, API, interfaz). Quien implemente una parte debe respetar exactamente los nombres, rutas y formatos aquí definidos.

> Todo el texto visible por el usuario, los comentarios de código, los nombres de archivos y los identificadores de la API van **en español del Ecuador**, con ortografía cuidada (el curso penaliza cada error). Fechas en pantalla **dd/mm/aaaa**; números con punto decimal y espacio fino como separador de miles (RNF-01 del CONOPS v1.3). Extranjerismos en cursiva en la documentación. Los nombres de variables de las fuentes (`sst`, `thetao`, `so`, `uo`, `vo`, `zos`, `mlotst`, `precip`) se conservan tal como los publican NOAA, Copernicus y CHIRPS.

---

## 1. Decisiones de la demo

| Tema | Decisión |
|---|---|
| Stack | Backend **Python 3.13 + FastAPI + uvicorn** (restricción del cliente: Python). Frontend **HTML + CSS + JavaScript puro** (sin bundler), servido como estáticos por el mismo FastAPI. Mapas con **Leaflet 1.9.4**, gráficos con **Plotly 2.35.2** (CDN cdnjs/jsdelivr). |
| Datos | **Las tres fuentes son reales.** **Temperatura superficial del mar real** de NOAA OISST v2.1 (NOAA/NCEI, DOI 10.25921/RE9P-PT57), servida por NOAA PSL (respaldo: archivos diarios de NOAA NCEI), con caché local, climatologías de los tres periodos y el **índice Niño 1+2 real** de la región oficial (ver §4.4). **Precipitación real** de CHIRPS v3 (Climate Hazards Center, UCSB), obtenida por dos caminos —el servidor del CHC y la réplica pública de dynamical.org, que publica el producto del CHC con los valores redondeados para comprimir— y llevada a la malla de 0.25° (ver §4.1). **Variables físicas del océano reales** de Copernicus Marine (análisis diario de GLOBAL_ANALYSISFORECAST_PHY_001_024 con el Copernicus Marine Toolbox, caché local y respaldo simulado señalado; ver §4.3). El generador determinista `datos_simulados.py` (climatología analítica realista: gradiente latitudinal, lengua fría ecuatorial, ciclo estacional, eventos El Niño/La Niña; ruido reproducible con semilla fija) queda solo como **respaldo señalado** de NOAA y de Copernicus (sin red, credenciales ni caché, o modo simulado forzado) y nunca se mezcla con datos reales en un mismo Dataset. Toda salida declara su **procedencia** («real», «simulado» o «mixto», §4.2) con su insignia: verde «Datos reales · NOAA OISST», «Datos reales: CHIRPS v3 (CHC, UCSB)» o «Datos reales · Copernicus Marine» («Datos reales» si son varias fuentes reales), ámbar «Datos simulados para demostración» o ámbar «Datos mixtos: reales y simulados». Todos los conectores tienen el **mismo contrato** (devuelven `xarray.Dataset` con metadatos CF), así que pasar una fuente a datos reales solo reemplazó su implementación interna. |
| Área | Área de estudio: 6° N a 6° S, 95° O a 78° O. Malla de **0.25°** para las tres fuentes en la demo (Copernicus se lee a 1/12° y se promedia en bloques de 3 × 3; CHIRPS se agrega desde 0.05°; se indica en «Acerca de»). |
| Periodo | Diario desde **01/10/2023** («últimos 3 años»): NOAA, hasta el **último día de su caché** (29/10/2026 con la caché preparada el 30/10/2026; avanza con la sincronización), que es además la **fecha de corte del sistema** (`catalogo.fecha_corte_sistema()`; en modo simulado, la simulada 27/10/2026); CHIRPS, hasta el último día que tiene la caché local (fecha de corte dinámica); Copernicus, hasta el **último día de análisis** (boletín semanal `W` − 2 días; nunca pronóstico) o, sin acceso, el último día en su caché (el simulador de respaldo llega al 26/10/2026). |
| Persistencia | Archivos JSON en `datos/` (usuarios, paneles, vistas, auditoría, trabajos) y PDFs en `datos/reportes/`. Sin base de datos externa (en el producto real: PostgreSQL/PostGIS). |
| Autenticación | Usuarios de demostración con contraseña `some2026`; *token* en memoria (cabecera `Authorization: Bearer <token>`). |
| Idioma de código | Identificadores en español (`consulta`, `recorte`, `fecha_corte`), salvo nombres de variables de las fuentes y palabras clave de las bibliotecas. |
| Dependencias Python | Solo las ya instaladas: `fastapi`, `uvicorn`, `numpy`, `pandas`, `xarray`, `scipy`, `matplotlib`, `reportlab`, `jinja2`, `pydantic`, `openpyxl`, `Pillow`; para CHIRPS, `requests` y `h5py` y, **opcionales** (`requirements-replica.txt`), `icechunk` y `zarr` para la réplica de dynamical.org; para Copernicus Marine, `copernicusmarine>=2.5,<3` (`requirements-copernicus.txt`; importación perezosa: sin el paquete, el conector usa el simulador y lo dice); para NOAA OISST, `h5netcdf>=1.3` (xarray abre con ella el NetCDF4 de NOAA PSL y de NOAA NCEI). **No** usar cartopy, netCDF4, pydap, weasyprint ni passlib. NetCDF se escribe con `xarray.Dataset.to_netcdf(..., engine="scipy")` (NetCDF3 clásico; se indica en la UI). Contraseñas con `hashlib.sha256` + sal (es una demo). |

---

## 2. Estructura de carpetas

```
prototipo-some/
  README.md                      cómo ejecutar, usuarios de demo, guion de demostración
  requirements.txt
  requirements-replica.txt       opcionales: icechunk y zarr (réplica dynamical.org de CHIRPS)
  requirements-copernicus.txt    Copernicus Marine Toolbox (copernicusmarine>=2.5,<3)
  ejecutar.ps1                   crea/activa .venv si hace falta, instala, lanza uvicorn en http://127.0.0.1:8050
  ejecutar.bat                   mismo propósito (doble clic)
  docs/ESPECIFICACION-PROTOTIPO.md   (este archivo)
  docs/DISENO-COPERNICUS.md      diseño del conector real de Copernicus Marine
  docs/DISENO-NOAA.md            diseño, mediciones y verificación del conector real de NOAA OISST
  backend/
    __init__.py
    app.py                       FastAPI: monta /api y sirve frontend/ en /
    configuracion.py             constantes: rutas, puerto, área, fechas, versión
    auth.py                      usuarios, roles, tokens, dependencia `usuario_actual`, `requiere_rol`
    almacen.py                   persistencia JSON (usuarios, paneles, vistas, auditoría, trabajos) con bloqueo
    catalogo.py                  fuentes, productos, variables, recortes, periodos de referencia, estado de fuentes
    datos_simulados.py           generador determinista de campos (clim. analítica + eventos + ruido)
    conectores/__init__.py
    conectores/base.py           clase abstracta ConectorFuente, tipos de parámetros y procedencia (procedencia_de,
                                 procedencia_de_datos, combinar_procedencias)
    conectores/noaa.py           conector REAL de NOAA OISST v2.1 (reexporta noaa/conector.py)
    conectores/copernicus.py     conector REAL de Copernicus Marine PHY (thetao, so, uo, vo, zos, mlotst, con
                                 profundidades): caché local, descarga bajo demanda y respaldo simulado señalado
    conectores/chirps.py         conector REAL de CHIRPS v3 (precipitación, solo tierra; sirve la caché local)
    chirps/                      acceso real a CHIRPS v3: red.py (cortesía y disyuntor con el CHC), descarga.py
                                 (plan de llenado réplica/CHC), espejo.py (réplica dynamical.org, Icechunk/Zarr),
                                 lector_tiff.py y malla.py (GeoTIFF por rangos, agregación 5×5, reproyección),
                                 almacen.py (caché con tipo y origen por día), climatologia.py, sincronizacion.py,
                                 estado.py (RF-21 real: caminos e historial), prueba_chirps.py, prueba_espejo.py
    copernicus/                  acceso real a Copernicus Marine: acceso.py (único que importa copernicusmarine;
                                 guardia anti-prompt), calendario.py (último día de análisis), malla.py (3 × 3),
                                 niveles.py (interpolación vertical), almacen.py, descarga.py (fragmentos ARCO),
                                 climatologia.py (GLORYS12 y periodo efectivo), estado.py (RF-21 real),
                                 sincronizacion.py, prueba_copernicus.py
    noaa/                        acceso real a NOAA OISST v2.1: constantes.py (URLs, ritmos, rutas, interruptores),
                                 red.py (único que toca la red: ritmo entre procesos, pausa tras PSL, HTTP 429,
                                 pausa del servidor), malla.py (ventana nativa y media 2 × 2 a la malla SOME),
                                 almacen.py (caché mensual con tipo y origen por día), descarga.py (tramos de PSL
                                 y respaldo NCEI), climatologia.py (tres periodos y periodo efectivo), indice.py
                                 (índice Niño 1+2 semanal), estado.py (RF-21 real), sincronizacion.py,
                                 conector.py (ConectorNOAA y simulador de respaldo), prueba_noaa.py
    herramientas/preparar_chirps.py   prepara la caché de CHIRPS (diario + climatología)
    herramientas/preparar_copernicus.py   prepara la caché de Copernicus (escenarios de la demostración)
    herramientas/preparar_noaa.py     prepara la caché de NOAA (diario, climatologías e índice Niño 1+2)
    motor.py                     recorte, promedios, climatología, anomalías, series, perfiles, cortina, comparación, resúmenes en lenguaje sencillo
    graficos.py                  figuras matplotlib (mapa PNG con paleta, serie, perfil, cortina) para la UI y los reportes
    exportar.py                  CSV, PNG, NetCDF
    reportes.py                  plantillas de reporte y generación PDF (reportlab + figuras)
    trabajos.py                  cola de trabajos en hilo de fondo (estado en_cola/procesando/listo/error)
    cache.py                     caché LRU en memoria para resultados de consultas
    esquemas.py                  modelos pydantic de peticiones y respuestas de la API
    rutas/__init__.py
    rutas/auth.py, catalogo.py, consultas.py, exportar.py, reportes.py, panel.py, vistas.py, admin.py
    pruebas/prueba_humo.py       recorre todos los endpoints con TestClient y falla si algo no responde
  frontend/
    index.html                   único HTML; carga css/app.css y los js en orden
    css/app.css                  variables de diseño, componentes, modo oscuro, impresión
    js/util.js                   formato es-EC, toast, modal, descargarBlob, debounce
    js/api.js                    cliente HTTP con token, manejo de errores
    js/app.js                    estado global SOME, enrutador por hash, registro de módulos, sidebar, cabecera, insignias de procedencia (SOME.procedencia)
    js/modulos/acceso.js         inicio de sesión
    js/modulos/panel.js          panel personal con widgets
    js/modulos/consultas.js      constructor de consultas (formulario reutilizable) → mapa / serie / perfil
    js/modulos/mapa.js           visor de mapas (Leaflet + superposición PNG + valor por celda + barra de tiempo + vectores)
    js/modulos/series.js         series de tiempo, climatología, anomalías, mismo mes cada año, resumen en lenguaje sencillo
    js/modulos/perfiles.js       perfil vertical y cortina profundidad-tiempo
    js/modulos/comparar.js       comparar dos fuentes o dos periodos
    js/modulos/reportes.js       plantillas, parámetros, cola de trabajos, descarga
    js/modulos/vistas.js         Mis vistas (privadas y compartidas)
    js/modulos/fuentes.js        estado y fecha de corte de cada fuente
    js/modulos/admin.js          usuarios, catálogo, auditoría, sistema
    js/modulos/acerca.js         acerca de, créditos de las fuentes, alcance de la demo
    img/logo-some.svg            logotipo sencillo (ola + texto SOME)
  datos/                         creado al arrancar si no existe; JSON + reportes/ y las cachés noaa/, chirps/ y
                                 copernicus/
```

Puerto: **8050**. URL de la demo: `http://127.0.0.1:8050/`.

---

## 3. Dominio y datos simulados (respaldo de NOAA OISST y de Copernicus Marine)

Las secciones 3.2 a 3.6 describen el **simulador**, que solo responde como respaldo señalado (sin acceso ni caché, o con `SOME_NOAA_MODO=simulado` / `SOME_COPERNICUS_MODO=simulado`); con datos reales, los valores, las climatologías y el índice Niño 1+2 son los de las fuentes (§4.1, §4.3 y §4.4).

### 3.1 Malla y tiempo

- `LAT = np.arange(-6, 6.001, 0.25)` (49 valores), `LON = np.arange(-95, -77.999, 0.25)` (69 valores). Coordenadas CF: `lat` (`degrees_north`), `lon` (`degrees_east`), `time` (datetime64[ns] diario), `depth` (m, positivo hacia abajo).
- Fechas: `FECHA_INICIO = 2023-10-01`; `FECHA_CORTE_SIMULADA = 2026-10-27` (fecha de corte del simulador de NOAA; `FECHA_CORTE` es un alias por compatibilidad). La **fecha de corte del sistema** es dinámica: `catalogo.fecha_corte_sistema()` = la de NOAA OISST real (el último día de su caché) o, en modo simulado, `FECHA_CORTE_SIMULADA`; nunca lanza.
- Profundidades Copernicus: `[0, 5, 10, 20, 30, 50, 75, 100]` m.

### 3.2 Máscara tierra/mar

`mascara_tierra(lat, lon) -> bool[lat, lon]`. Tierra donde `lon > costa(lat)`, con `costa` interpolada linealmente entre los puntos (lat, lon): (6, −77.4), (4, −77.5), (2, −78.8), (1, −79.7), (0, −80.1), (−1, −80.8), (−2.2, −81.0), (−3.0, −80.0), (−3.5, −80.3), (−4.5, −81.2), (−5.5, −81.0), (−6, −80.9). Además islas Galápagos: celdas con lat ∈ [−1.0, 0.0] y lon ∈ [−91.5, −90.75] (Isabela/Fernandina) y lat ∈ [−0.75, −0.5], lon ∈ [−90.5, −90.25] (Santa Cruz). Las variables marinas (simuladas) son `NaN` en tierra. La precipitación CHIRPS, que es real, no usa esta máscara: es `NaN` donde el CHC no publica dato (el mar), y el motor no aplica la máscara sintética a las fuentes reales.

### 3.3 Climatología analítica (valores creíbles)

Con `d` = día del año, `fase = 2π·(d − 75)/365.25` (máximo de temperatura en marzo):

- **SST (°C)**: base `27.0 − 0.12·(lat + 2)² ·0.35`; lengua fría: `− 2.2·exp(−((lat + 0.5)/1.6)²)·clip((lon + 78)/−17, 0, 1)` (más fría hacia el oeste); afloramiento costero peruano: `− 1.8·exp(−((lon − costa(lat) + 1)/1.5)²)·clip(−lat/6, 0, 1)`; estacional: `+ 1.3·cos(fase)·(1 + 0.4·clip(−lat/6, 0, 1))`. Rango resultante aproximado 19–28 °C.
- **Temperatura por profundidad (`thetao`)**: `sst − perfil(z)` con `perfil(z) = 0` si `z ≤ capa_mezcla`, y `(z − capa_mezcla)·0.11` acotado a 12 °C por debajo; `capa_mezcla` climatológica = `25 + 10·clip(-lat/6,0,1) + 8·cos(fase + π)` m (más somera en el afloramiento). Resultado a 100 m ≈ 15–17 °C.
- **Salinidad `so` (PSU)**: `34.2 + 0.6·clip((lon + 95)/17, 0, 1) − 1.2·exp(−((lat − 3)/2)²)·clip((lon + 82)/4, 0, 1)` (Panamá, más dulce) `− 0.8·exp(−((lat + 3)/1.2)²)·exp(−((lon + 80.5)/1.0)²)` (golfo de Guayaquil) `+ 0.004·z`.
- **Corrientes `uo`, `vo` (m/s)**: `uo = −0.25 − 0.2·exp(−((lat)/1.5)²)` (Corriente Sur Ecuatorial hacia el oeste) `+ 0.35·exp(−((lat − 4.5)/1.2)²)` (Contracorriente hacia el este); `vo = 0.08·clip(−lat/6,0,1)·exp(−((lon − costa(lat) + 1.5)/2)²)` (Humboldt hacia el norte). Decaen con `exp(−z/80)`.
- **Nivel del mar `zos` (m)**: `0.15·exp(−((lat − 3)/3)²) − 0.05·clip(−lat/6,0,1) + 0.03·cos(fase)`.
- **Capa de mezcla `mlotst` (m)**: la `capa_mezcla` climatológica.
- **Precipitación `precip` (mm/día)**: estacional costera: `pico = 1 + cos(2π·(d − 60)/365.25)` (máximo en marzo); `precip = (0.5 + 6·pico)·tierra`; más lluviosa al norte (Esmeraldas): `× (1 + 0.6·clip((lat + 1)/5, 0, 1))`; más seca al sur de −3 (Santa Elena, El Oro, Perú): `× (0.35 + 0.65·clip((lat + 5)/3, 0, 1))`; Galápagos `× 0.4`. Los días secos se modelan con un umbral sobre el ruido para que la lluvia sea intermitente.

### 3.4 Eventos interanuales (anomalías)

`indice_enso(fecha)` en °C para Niño 1+2, interpolado linealmente entre nodos mensuales: 10/2023 +2.4; 12/2023 +2.8; 02/2024 +2.2; 04/2024 +1.2; 06/2024 +0.2; 09/2024 −0.6; 12/2024 −0.9; 03/2025 −0.4; 06/2025 0.0; 09/2025 +0.3; 12/2025 +0.7; 03/2026 +1.4; 06/2026 +1.1; 09/2026 +1.6; 10/2026 +1.8. La anomalía de SST en cada celda = `indice_enso · peso(lat, lon)`, con `peso = 0.4 + 0.6·exp(−((lat + 4)/5)²)·clip((lon + 95)/10, 0, 1)` (mayor en Niño 1+2). La precipitación costera se multiplica por `(1 + 0.45·indice_enso)` acotado a ≥ 0.2. La capa de mezcla crece `+ 6·indice_enso` m; `zos` sube `+ 0.06·indice_enso` m; `thetao` hereda la anomalía de SST atenuada con `exp(−z/60)`.

### 3.5 Ruido reproducible

Para cada fecha, `rng = np.random.default_rng(semilla_base + ordinal(fecha))`; ruido espacial suavizado (ruido blanco → filtro gaussiano `scipy.ndimage.gaussian_filter(sigma=2)`) con amplitud 0.35 °C para SST, 0.08 PSU para salinidad, 0.04 m/s para corrientes, 0.01 m para `zos`, 4 m para `mlotst`; la precipitación usa ruido log-normal y un umbral que deja ~55 % de días secos fuera de la estación lluviosa.

### 3.6 Climatología de referencia

Como la climatología es analítica, el conector ofrece `climatologia(variable, periodo_referencia, profundidad)` que devuelve la climatología mensual (12 meses) calculada **promediando la función analítica** sobre cada mes (sin eventos ni ruido). Periodos de referencia disponibles en el catálogo: `1991-2020` (por defecto, estándar OMM), `1981-2010`, `1993-2022` (Copernicus). Con el simulador todos devuelven la misma climatología analítica; el motor documenta en `metodo` cuál se usó. (NOAA real usa la climatología de cada periodo —1991-2020 oficial de NOAA PSL; 1981-2010 y 1993-2022 calculadas, con el periodo efectivo «1982-2010 en enero–agosto» en 1981-2010—, §4.4; Copernicus real usa la climatología oficial de GLORYS12, 1993-2016, para cualquier periodo pedido, y lo declara: §4.3.)

---

## 4. Contrato de los conectores (`backend/conectores/base.py`)

```python
@dataclass
class ParametrosConsulta:
    variable: str                       # p. ej. "sst", "thetao", "precip"
    bbox: tuple[float, float, float, float]   # (lon_min, lat_min, lon_max, lat_max)
    fecha_inicio: date
    fecha_fin: date                     # inclusivo
    profundidad: float | None = None    # m; None = superficie o variable 2D
    profundidades: list[float] | None = None  # para perfiles/cortinas

class ConectorFuente(ABC):
    id: str; nombre: str; proveedor: str; producto: str; credito: str; licencia: str
    resolucion: str; cobertura_temporal: str; variables: list[VariableFuente]
    datos_reales: bool = False          # True para conectores con datos reales (CHIRPS v3; Copernicus y NOAA en modo real: propiedad dinámica)
    insignia: str = cfg.INSIGNIA_SIMULADOS   # texto corto de procedencia; CHIRPS: cfg.INSIGNIA_REALES_CHIRPS; Copernicus: cfg.INSIGNIA_REALES_COPERNICUS; NOAA: cfg.INSIGNIA_REALES_NOAA
    def obtener(self, p: ParametrosConsulta) -> xr.Dataset: ...   # dims (time, [depth], lat, lon), attrs CF: title, source, institution, references, history, comment=self.insignia
    def climatologia(self, variable, periodo_referencia, bbox, profundidad=None) -> xr.Dataset: ...  # dim month (1..12)
    def estado(self) -> EstadoFuente: ...   # estado: "operativa"|"degradada"|"sin_conexion"; fecha_corte; latencia_ms; ultimo_chequeo; mensaje
```

Atributos CF por variable (`standard_name`, `long_name` en español, `units`): sst/thetao → `sea_surface_temperature`/`sea_water_potential_temperature`, `degC`; so → `sea_water_salinity`, `1e-3` (mostrar «PSU»); uo/vo → `eastward_sea_water_velocity`/`northward_sea_water_velocity`, `m s-1`; zos → `sea_surface_height_above_geoid`, `m`; mlotst → `ocean_mixed_layer_thickness_defined_by_sigma_theta`, `m`; precip → `precipitation_flux` (como `lwe_precipitation_rate`), `mm day-1`.

Cada `Dataset` declara además su procedencia en los atributos `datos_reales` (1/0) y, si corresponde, `aviso` (los de `ConectorSimulado` llevan `datos_reales=0`, `procedencia="simulado"` e `insignia`). El **estado de las tres fuentes es real** (§4.1, §4.3 y §4.4); con `SOME_NOAA_MODO=simulado`, NOAA vuelve al estado simulado anterior (`operativa`, fecha de corte 27/10/2026, latencia 820 ms), y con `SOME_COPERNICUS_MODO=simulado`, Copernicus (`operativa`, 26/10/2026, 1 450 ms).

### 4.1 Conector real de CHIRPS v3 (`conectores/chirps.py` + paquete `chirps/`)

- **Herencia**: `ConectorCHIRPS(ConectorFuente)` (no hereda de `ConectorSimulado`), `datos_reales = True`, `insignia = cfg.INSIGNIA_REALES_CHIRPS` («Datos reales: CHIRPS v3 (CHC, UCSB)»). Mismo contrato: `obtener`, `climatologia`, `estado`, más `historial_estado(dias)` y `detalle_estado()` (caminos, §7.2).
- **Dos caminos a CHIRPS v3** (la réplica publica el producto del CHC con los valores redondeados para comprimir; cada día guarda de cuál salió: `origen` 1 = CHC, 2 = réplica, 3 = final provisional «p25» de versiones anteriores, en `datos/chirps/diario/AAAA-MM.npz`):
  1. **Servidor del CHC** (`https://data.chc.ucsb.edu/products/CHIRPS/v3.0/`): diarios **finales** a 0.05° (`daily/final/rnl/AAAA/chirps-v3.0.rnl.AAAA.MM.DD.tif`, disgregación con ERA5), diarios **preliminares** a 0.05° (`daily/prelim/sat/AAAA/chirps-v3.0.prelim.AAAA.MM.DD.tif`, disgregación con IMERG) y **mensuales** globales a 0.05° (`monthly/global/netcdf/by_year/chirps-v3.0.AAAA.monthly.nc`). No se usa el final ya agregado a 0.25° (`daily/final/rnl/p25/`): su máscara costera difiere. Los GeoTIFF y los NetCDF4 se leen por rangos HTTP (solo el recorte del área de estudio), con Pillow, `requests` y `h5py`; los metadatos HDF5 se leen con un objeto archivo cuyas lecturas son `red.rango` (sin `fsspec`), así que también pasan por el ritmo, el disyuntor y el User-Agent.
  2. **Réplica de dynamical.org** (`chirps/espejo.py`; almacenes Icechunk/Zarr en S3 `dynamical-ucsb-chc-chirps`, `us-west-2`, acceso anónimo; CC BY 4.0; tercero sin garantía de servicio): final «rnl» desde 01/01/1981 y preliminar, a 0.05°, en kg m⁻² s⁻¹ (× 86 400 → mm/día), valores redondeados a 8 bits de mantisa para comprimir (`keep_mantissa_bits=8` en `reformatters/ucsb_chc/chirps/template_config.py` del código público de dynamical.org; error ≤ 0.2 %). Se lee por tramos alineados con sus fragmentos de 365 días. Requiere `icechunk` y `zarr` (opcionales); `espejo.disponible()` no importa nada y nunca lanza.
- **Agregación común** (`chirps/malla.py`): lo que llega a 0.05° se agrega a 0.25° (bloques 5×5, mínimo 13 de 25 válidas) y se reproyecta a la malla SOME (cada centro, media de las 4 celdas nativas que lo rodean, mínimo 2). Todo el producto (final y preliminar del CHC, réplica y climatología) pasa por esta misma agregación: el final del CHC a 0.05° coincide con la réplica a < 0.1 % con las mismas celdas. Los días finales que versiones anteriores tomaron del producto `p25` del CHC (archivos sin el campo `formato`) se leen como «finales provisionales» (`origen` 3): cuentan como días del CHC, el `procesamiento` de la consulta lo advierte y la sincronización los reemplaza por el final a 0.05° en cuanto la réplica o el CHC lo cubren.
- **Conmutación** (`chirps/descarga.py`, `plan_llenado` y `ejecutar_plan`): en modo `auto`, la réplica aporta todo lo que cubre (es más rápida y no carga al CHC) y el CHC, lo más reciente; si la réplica falla o no tiene un día que el CHC publica, ese día pasa a la cola del CHC. Un día se reescribe solo si mejora (ausente → preliminar → final; un final provisional «p25» → final a 0.05°). Una consulta con días ausentes los pide en el momento a la réplica (ampliados a los fragmentos completos de 365 días que toca) y después al CHC (como máximo 45 días y 90 s), nunca a un CHC marcado caído ni a una réplica cuya lectura falló hace menos de 5 minutos (`descarga.marcar_espejo_caido`: además invalida la cobertura en memoria y lo registra en el estado); una consulta no espera más de 5 s a otra lectura de la réplica en curso. Antes de bajar cada día del CHC se vuelve a mirar la caché bajo el bloqueo de archivo y el día se guarda en cuanto llega. **Fecha de corte efectiva** (`ConectorCHIRPS.fecha_corte_efectiva`): el último día en caché o, si es posterior, lo último que publica la réplica cuando responde (con la caché vacía, también lo que lista el CHC); con la caché vacía y nada verificado, la fecha de corte queda antes de `cfg.FECHA_INICIO` y el motor no rechaza la consulta: `obtener()` intenta el llenado bajo demanda o responde que la caché se está llenando. `obtener()` llena ANTES de recortar a la caché.
- **Cortesía con el CHC** (`chirps/red.py`): como máximo **1 petición por segundo entre todos los procesos del SOME del equipo** (el turno se reserva bajo un bloqueo entre procesos en `datos/chirps/.bloqueos/ritmo_chc.txt`; si ese archivo no se puede usar, al menos el limitador del proceso), **un archivo a la vez** (`red.un_archivo()`, bloqueo entre hilos y procesos, reentrante; lo toman la descarga diaria y la del NetCDF mensual de la climatología), tiempos de espera de 10 s (conexión) y 30 s (respuesta), 3 intentos por petición y User‑Agent `SOME-prototipo-INOCAR/<versión> (proyecto academico ESPOL-INOCAR; contacto: …)` con el contacto de `SOME_CHIRPS_CONTACTO` (sin valor por defecto; si falta, el User‑Agent lo indica y no lleva ningún correo). **Disyuntor**: 3 fallos seguidos (sin conexión, tiempo agotado, HTTP 403/429/5xx) o un HTTP 429 lo abren 15 minutos, durante los que no se toca el CHC; vencido el plazo se permite un solo intento de prueba (si falla, se reabre). Se persiste en `estado.json` y lo respetan los demás procesos (servidor, `preparar_chirps`, pruebas); el primer intento de otro proceso tras el vencimiento cuenta como intento de prueba. Cualquier fallo al abrir el NetCDF mensual del CHC cuenta para el disyuntor, y la climatología no le pide nada a un CHC marcado caído (disyuntor, fallo reciente o último contacto fallido); en `preparar_anios`, el primer fallo del CHC detiene el resto de los años. Los mensajes de error se guardan y se muestran en español, cortos y sin cortar palabras (`estado.legible`, `red.texto_error`); el texto técnico crudo queda en el log.
- **Concurrencia entre procesos** (`chirps/bloqueo.py`): `almacen.guardar` toma un bloqueo por mes entre procesos, relee el mes del disco dentro del bloqueo, aplica la regla de reemplazo y escribe (temporal con `fsync` + `os.replace`); `estado.py` hace cada escritura como una transacción bajo `.bloqueos/estado.lock` (releer, modificar, guardar con reintentos); `climatologia` prepara cada año bajo un bloqueo por año que vuelve a mirar la caché al entrar y protege `origen.json`. Un `.npz` dañado (truncado, vacío o con otra forma) se aparta como `*.danado` y se trata como ausente.
- **Caché**: `datos/chirps/` en la malla del prototipo (`cfg.LAT` × `cfg.LON`): `diario/AAAA-MM.npz` (valores, tipo, origen y fecha de obtención por día), `mensual/AAAA.npz` y `mensual/origen.json` (origen de cada año de la climatología), `estado.json`. `python -m backend.herramientas.preparar_chirps [--fuente auto|chc|espejo] [--solo-diario|--climatologia] [--desde --hasta]` la prepara; un hilo daemon (`chirps.iniciar_sincronizacion()`, llamado en el arranque de `app.py` sin bloquearlo y detenido al cerrar; se desactiva con `SOME_CHIRPS_SIN_SINCRONIZACION=1`) revisa los dos caminos cada 6 horas (30 minutos si alguno falló), descarga lo nuevo y reemplaza los preliminares por los finales cuando se publican.
- **Climatología** (`chirps/climatologia.py`): media mensual 1981-2022 por año, primero desde la réplica (suma de los días con dato / días con dato, mínimo 80 % del mes) y, si no, desde el NetCDF mensual del CHC (÷ días del mes); ambas vías son intercambiables y `origen.json` registra cuál se usó. Una consulta prepara en el momento como mucho 3 años; si faltan más, lanza la preparación en segundo plano y responde 503 «la climatología … se está preparando…; inténtelo de nuevo en unos minutos». La sincronización la prepara de a un año para no disparar la memoria.
- **Dataset** devuelto: dims `(time, lat, lon)`; atributos globales `Conventions="CF-1.8"`, `comment=cfg.INSIGNIA_REALES_CHIRPS`, `datos_reales=1`, `dias_preliminares`, `dias_faltantes`, `dias_finales`, **`origen_datos`** (p. ej. «servidor del CHC: 5 días; réplica dynamical.org: 41 días»), **`dias_origen_chc`**, **`dias_origen_replica`**, `procesamiento` (texto en español que termina con el origen de los días), `source` (cita la réplica si se usó), `references` (Funk et al., 2026, *Scientific Data* 13, 718, DOI 10.1038/s41597-026-07096-4; repositorio de datos CHIRPS3 del CHC, DOI 10.15780/G2JQ0P, con la fecha de la última descarga), `history`, **`credito`** (el de esa consulta, `credito_para_origen`: «…(Funk et al., 2026; doi:10.15780/G2JQ0P), vía la réplica de dynamical.org; CC BY 4.0.» solo si TODOS los días vienen de la réplica, «…; parte de los días vía la réplica de dynamical.org; CC BY 4.0.» si se mezclan con días del CHC; ≤ 150 caracteres para que quepa en el pie de las figuras; `motor.origen_real` lo recalcula con la suma de todos los Dataset de la consulta, p. ej. en «Mismo mes cada año» o «Comparar»), **`credito_replica`** (la atribución literal de dynamical.org, con su cita y el aviso del redondeo; solo si se usó), `identificador`, `licencia`, `fuente_id`, `fecha_corte`, `resolucion` y, si hay finales provisionales, `dias_finales_provisionales`; variable `precip` con `standard_name="lwe_precipitation_rate"`, `units="mm day-1"`, `unidad_mostrada="mm/día"`, `comment=cfg.INSIGNIA_REALES_CHIRPS`. `NaN` en el mar. La climatología lleva `origen_climatologia`, `metodo` según el origen real de sus años y `credito` «…; climatología calculada con la réplica de dynamical.org; CC BY 4.0.» cuando corresponde.
- **Estado (RF-21) real** (`chirps/estado.py`, nunca toca la red): `operativa` si el CHC responde con latencia < 3000 ms, verificación vigente (< 13 h) y sin errores en la última sincronización; `degradada` si solo responde la réplica, el CHC va lento o hubo errores parciales; `sin_conexion` si no responde ninguno o el modo sin red está activo (se sirve la caché local). El mensaje dice qué camino se usa, la cobertura de la réplica, hasta cuándo no se reintenta el CHC y los días preliminares. `historial_estado(dias=14)` devuelve `[{fecha, estado, origen, origen_texto}]` del más antiguo al más reciente: el peor estado de cada día y las fuentes que respondieron (`chc`, `espejo`, `chc+espejo`, `cache`); los días sin verificación son `"sin_registro"`.
- **Modo sin red**: con `SOME_CHIRPS_SIN_RED=1` el conector no toca la red (ni el CHC ni la réplica); sirve solo la caché local, su estado es `sin_conexion` con un mensaje claro y los caminos figuran como `sin_red`. Todas las pruebas pasan así si la caché existe.
- **Administración**: `chirps.resumen()` (días en caché por tipo y por origen —`dias_por_origen`—, preliminares y faltantes, `fuentes` con el estado de cada camino, `disyuntor_chc`, última y próxima sincronización, bytes descargados del CHC, errores recientes) se publica en `GET /api/admin/sistema` como `chirps`.

### 4.2 Procedencia de los resultados (real / simulado / mixto)

`conectores.procedencia_de(conectores) -> {datos_simulados, procedencia, insignia}` (nivel catálogo): «real» si todos los conectores implicados tienen `datos_reales` (insignia del conector si es uno solo, `cfg.INSIGNIA_REALES` si son varios; `datos_simulados=false`), «simulado» si ninguno, «mixto» si se combinan (`cfg.INSIGNIA_MIXTOS`, «Datos mixtos: reales y simulados»). La procedencia de cada **resultado** la calcula `conectores.base.procedencia_de_datos(conectores, ds_lista) -> {datos_simulados, procedencia, insignia, avisos}` mirando los `Dataset` que realmente lo formaron: cada uno cuenta como real si `attrs["datos_reales"] == 1` y como simulado si `== 0` (sin el atributo, según su conector); un conector sin Dataset cuenta con su `datos_reales`. Así, un respaldo simulado de NOAA o de Copernicus nunca se etiqueta como real, y datos reales con climatología simulada dan «mixto». Con las tres fuentes reales, `procedencia_de` del catálogo es «real» (`datos_simulados=false`, insignia «Datos reales»). El índice Niño 1+2 tiene la procedencia de la SST de NOAA de la que se calcula («real» con NOAA real; «simulado» con su respaldo o su modo simulado), y todo lo derivado de fuentes simuladas es «simulado». Toda respuesta con datos de `/api/consultas/*` y de `/api/panel/widget/datos` incluye `datos_simulados`, `procedencia` e `insignia`, `avisos` (lista, si algún Dataset trae `aviso`) y `periodo_referencia_efectivo` (si la climatología usada declara `periodo_efectivo`, p. ej. «1993-2016» en Copernicus) (y, si interviene una fuente real, `dias_preliminares`, `dias_faltantes`, `usa_replica`, `origen_datos` y `origen_climatologia`, calculados por `motor.origen_real` a partir de los `Dataset` de la consulta: los diarios y, si se usó, la climatología); `fuente` incluye `datos_reales` e `insignia` y, para CHIRPS, el `credito` de esa consulta (cita la réplica solo si se usó), `credito_replica`, `origen_datos` y `origen_climatologia`. En la interfaz, el pie de crédito de cada figura muestra «Origen de los días» y «Climatología»; las figuras PNG y los PDF usan el crédito de su propia consulta. El `metodo` termina con el aviso de simulación o, para datos reales, con el procesamiento y los días preliminares. En el mapa de un periodo, una celda con dato en menos del 80 % de los días con dato queda sin dato (se dice cuántas en el `metodo`); con `incluir_acumulado` la respuesta trae `acumulado` (suma de los días con dato solo en las celdas que los tienen todos), que usa el boletín. Los acumulados «frente a lo normal» (widgets y boletín, `motor.acumulado_y_normal`) suman lo normal solo de los días con valor. La «tendencia por año» solo se calcula con al menos 2 años de datos. La caché de respuestas (`cache.consultar`) incluye en la clave la firma de la caché de CHIRPS y su fecha de corte efectiva y, si la consulta usa Copernicus o NOAA, `copernicus.firma_cache()` o `noaa.firma_cache()` (cambian con cada escritura de su caché —un día nuevo, un preliminar que pasa a final—, con el modo, el acceso y la fecha de corte); no guarda respuestas con días faltantes ni con `avisos`, y calcula una sola vez las peticiones idénticas simultáneas. En consultas puntuales la máscara tierra/mar sintética se aplica a las fuentes simuladas; con una fuente real (Copernicus) un perfil o una cortina solo se rechaza de antemano si el punto está claramente tierra adentro (también lo es todo a ±0.5°); si la serie de un punto de CHIRPS es toda `NaN`, la respuesta es 422 «CHIRPS solo tiene datos sobre tierra…». Si una fuente real falla por red, 503 con el motivo; una descarga de Copernicus que supera la espera responde 503 «…sigue descargándose en segundo plano; vuelva a consultar en unos segundos». Para anomalías con datos del respaldo simulado, el motor pide la climatología con `simulada=True` (anomalía coherente). El `metodo` dice el periodo efectivo («Anomalía = valor − climatología mensual 1993-2016 (se pidió 1991-2020); …» en Copernicus; «1981-2010 (1982-2010 en enero–agosto)» en NOAA, porque solo cambian esos meses), igual que el subtítulo y el resumen; con NOAA real, el método nombra además los días preliminares de NOAA OISST (`nota_preliminares`); el crédito de `fuente` une los créditos distintos de los Dataset (análisis ANFC y climatología GLORYS12) y `fuente.aviso` repite los avisos.

### 4.3 Conector real de Copernicus Marine (`conectores/copernicus.py` + paquete `copernicus/`)

Diseño detallado en `docs/DISENO-COPERNICUS.md`. Resumen del contrato que usan el motor, la API y la interfaz:

- **Clase**: `ConectorCopernicus(ConectorFuente)` con los mismos `id = "copernicus_phy"`, nombres, unidades, paletas, decimales y las 7 variables (incluida «corrientes» con `componentes=["uo","vo"]`). `datos_reales` e `insignia` son **propiedades dinámicas**: real (`cfg.INSIGNIA_REALES_COPERNICUS`, «Datos reales · Copernicus Marine») si `copernicus.modo() == "real"` (hay caché local o acceso: paquete, red, credenciales presentes y no inválidas); si no, simulado. Expone además `historial_estado(dias)` y `detalle_estado()` (bloque `copernicus`, §7.2); `estado()`, `historial_estado()` y `detalle_estado()` nunca lanzan.
- **Producto**: análisis diario de GLOBAL_ANALYSISFORECAST_PHY_001_024 (1/12°, 50 niveles; DOI 10.48670/moi-00016) con `copernicusmarine.open_dataset` (ARCO, perezoso), recortando área, días y **solo los niveles necesarios**. Regrillado a 0.25°: media de las 3 × 3 celdas nativas de cada celda SOME (mínimo 5 válidas). Profundidad: interpolación lineal entre los dos niveles del modelo que rodean cada nivel SOME (0 m = 0.494 m). Unidades CF `degC`, `1e-3`, `m s-1`, `m`.
- **Fecha de corte**: último día de **análisis** = boletín semanal `W` (miércoles) − 2 días; los días `W−8 … W−2` son análisis NRT y los anteriores, mejor análisis; el pronóstico nunca se sirve. Sin acceso, el último día de la caché.
- **Caché** `datos/copernicus/` (por variable, nivel y mes, ya regrillada, con el tipo por celda y el boletín de cada día) llenada bajo demanda (espera máxima 40 s; si se agota, 503 «sigue descargándose») y por un hilo de sincronización liviano; `python -m backend.herramientas.preparar_copernicus` llena los escenarios de la demostración.
- **Dataset** de `obtener()`: dims `(time, [depth], lat, lon)`; atributos `datos_reales=1`, `procedencia="real"`, `insignia`, `comment`, `credito` (ANFC con su DOI), `procesamiento` (regrillado, interpolación y días NRT/mejor análisis de la consulta), `origen_datos` («Copernicus Marine (análisis ANFC): N días de mejor análisis y M de análisis NRT»), `dias_nrt`, `dias_definitivos`, `dias_faltantes`, `dias_preliminares=0`, `cobertura` y, si la cobertura es parcial, `aviso`. **Respaldo** (sin ningún dato real para la consulta): el Dataset del simulador con `datos_reales=0`, `respaldo_simulado=1` y `aviso` («Copernicus Marine no está disponible: {motivo}. Esta consulta usa datos SIMULADOS para demostración.»). Nunca se mezclan días simulados y reales en un mismo Dataset.
- **Climatología**: la oficial de GLORYS12 (`cmems_mod_glo_phy_my_0.083deg-climatology_P1M-m`, GLOBAL_MULTIYEAR_PHY_001_030, DOI 10.48670/moi-00021), cuyo periodo real (`climatology_bounds`) es **1993-2016**. Atributos `periodo_referencia` (el pedido), `periodo_efectivo` («1993-2016»), `metodo`, `origen_climatologia` (dice el periodo efectivo, que se pidió otro si es el caso y la diferencia de sistema reanálisis/análisis operativo), `nota_sistema` y `credito` de GLORYS12. `climatologia(..., simulada=True)` devuelve siempre la del simulador.
- **Modos**: `SOME_COPERNICUS_MODO=auto|simulado|real`, `SOME_COPERNICUS_SIN_RED=1`, `SOME_COPERNICUS_SIN_SINCRONIZACION=1`. Credenciales: las guarda el usuario con `copernicusmarine login`; el código nunca las pide, muestra ni guarda y nunca llama a `login()` sin `check_credentials_valid=True` (guardia anti-*prompt*).
- **Estado (RF-21) real**: `operativa` (credenciales válidas, lectura de prueba < 20 s, verificación vigente, metadatos al día), `degradada` (lenta, con errores, sin verificar aún o metadatos atrasados) o `sin_conexion` (sin red, sin paquete, sin credenciales o no válidas, sin respuesta); historial de 14 días con `sin_registro`.
- **Administración**: `copernicus.resumen()` se publica en `GET /api/admin/sistema` como `copernicus`.

### 4.4 Conector real de NOAA OISST v2.1 (`conectores/noaa.py` + paquete `noaa/`) — RF-03

Diseño detallado, mediciones y verificación independiente en `docs/DISENO-NOAA.md`. Resumen del contrato:

- **Clase**: `ConectorNOAA(ConectorFuente)` (`backend/noaa/conector.py`, reexportado por `backend/conectores/noaa.py`) con exactamente los mismos `id = "noaa_oisst"`, `nombre`, `nombre_corto`, `proveedor`, `producto`, `identificador = "NOAA-OISST-v2.1"`, `licencia` y la variable `sst` (nombre, unidad, paletas, `unidades_cf = "degC"`, 1 decimal) del conector simulado anterior. `datos_reales`, `insignia` (`cfg.INSIGNIA_REALES_NOAA`, «Datos reales · NOAA OISST»), `credito`, `referencias`, `resolucion` y `cobertura_temporal` son **propiedades dinámicas** (`noaa.modo()`: «real» si hay caché local o acceso; «simulado» con `SOME_NOAA_MODO=simulado` o sin red ni caché). Expone además `historial_estado(dias)`, `detalle_estado()` (bloque `noaa`, §7.2), `serie_indice_nino12(inicio, fin)` y `fecha_corte_efectiva()`; `estado()`, `historial_estado()` y `detalle_estado()` nunca lanzan.
- **Producto y acceso**: NOAA 0.25° Daily OISST v2.1 (NOAA/NCEI, DOI 10.25921/RE9P-PT57). Vía principal: servicio NCSS del THREDDS de **NOAA PSL** (`sst.day.mean.AAAA.nc`; recorte `north=6.2&south=-9.95&west=264.8&east=282.2&accept=netcdf4`, NetCDF4 abierto con h5netcdf). Respaldo de los días recientes: archivos diarios globales de **NOAA NCEI** (final o `_preliminary`). Una ventana nativa de 65 × 70 celdas cubre el área SOME y la región Niño 1+2 oficial (0–10° S, 90–80° O). Sin credenciales; User-Agent `SOME-prototipo/0.1 (demostracion para el INOCAR)` (sin correo).
- **Malla**: cada centro SOME (múltiplo de 0.25°) es la esquina común de 4 celdas nativas (centros en x.125/x.375): media de las válidas con mínimo 2 de 4. Celdas de mar del 29/10/2026: área 3 066, Niño 1+2 954, golfo de Guayaquil 40, Guayas 12, El Oro 4, Manabí 26; el rectángulo solo de tierra (−79.5…−78.5, −2.5…−1.5) no tiene celdas (422).
- **Finales y preliminares**: un día de PSL es final si NCEI publicó su final al menos 12 h antes de la descarga; si no, preliminar (sin el listado de NCEI, los últimos 15 días). Los últimos ~15 días son preliminares y la sincronización los renueva.
- **Fecha de corte**: el último día de la caché (con la caché vacía y acceso, el último publicado por PSL); no se adelanta al día que PSL acaba de publicar hasta que la sincronización lo guarda. Es la fecha de corte del sistema (§3.1). En modo simulado, 27/10/2026.
- **Ritmo y límites de PSL**: tramos de ≤ 92 días de un mismo archivo anual (~28 s; un año entero da HTTP 502 a los ~60 s), pausa de 20 s tras cada petición a PSL, ≤ 1 petición/s por servidor entre todos los procesos (turno en `.bloqueos/ritmo_<servidor>.txt`). PSL limita por IP con **HTTP 429**: la sincronización y `preparar_noaa` esperan 60, 120, 240 y 300 s y reintentan; una consulta no espera (sirve la caché y avisa). Tras 3 fallos seguidos (o el primero de una consulta) el servidor queda en pausa 10 min, persistida en `estado.json`.
- **Consultas** (`obtener`): fechas acotadas a `[FECHA_INICIO, corte]`; si faltan días y hay acceso, como máximo una petición a PSL (≤ 92 días, un solo intento, 8 s para conectar y 45 s para recibir) y, si PSL no responde, hasta 5 días recientes de NCEI; nunca esperan más de 5 s a otra descarga en curso.
- **Caché** `datos/noaa/` (o `SOME_RUTA_NOAA`): `diario/AAAA-MM.npz` (`valores` 49 × 69, `nino12` 40 × 40 nativas, `tipo`, `origen`, `obtenido`), `mensual/sst_mon_mean.npz`, `climatologia/sst_<periodo>.npz`, `climatologia/ltm_diaria_nino12.npz`, `estado.json`, `generacion`, `.bloqueos/`. Reemplazo: final > preliminar > ausente. `noaa.firma_cache()` cambia con cada escritura, el modo, el acceso y la fecha de corte, y entra en la clave de la caché de consultas.
- **Dataset** de `obtener()`: dims `(time, lat, lon)`; atributos `datos_reales=1`, `procedencia="real"`, `insignia`, `comment` («Datos reales · NOAA OISST»), `credito` («NOAA OISST v2.1 (Huang et al., 2020; doi:10.25921/RE9P-PT57), NOAA NCEI y CDR Program, vía NOAA PSL.», con «vía» según el origen de los días), `references`, `doi`, `procesamiento`, `origen_datos` («NOAA OISST v2.1: N días finales y M preliminares[; K de ellos de NOAA NCEI (respaldo)]»), `dias_finales`, `dias_preliminares`, `dias_faltantes`, `dias_origen_psl`, `dias_origen_ncei`, `cobertura`, `fecha_corte` y, si hay preliminares, `periodo_preliminar` y `nota_preliminares`; si faltan días, `aviso`. Variable `sst`: `units="degC"`, `cell_methods = "time: mean (diario) area: mean (2 × 2 celdas nativas de 0.25°)"`. **Respaldo** (sin ningún día real para la consulta): el Dataset del simulador con `datos_reales=0`, `respaldo_simulado=1` y `aviso` («NOAA OISST no está disponible: {motivo}. Esta consulta usa datos SIMULADOS para demostración.»). Nunca se mezclan días simulados y reales en un mismo Dataset. Con `SOME_NOAA_MODO=real` (estricto), en lugar del respaldo, error 422.
- **Climatología**: 1991-2020 **oficial** (`sst.mon.ltm.1991-2020.nc`); 1981-2010 y 1993-2022 **calculadas** con `sst.mon.mean.nc`. OISST empieza en 09/1981: en 1981-2010, enero a agosto usan 1982-2010. Atributos `periodo_referencia` (el pedido), **`periodo_efectivo`** («1991-2020», «1993-2022» o «1982-2010 en enero–agosto»), `periodo_efectivo_por_mes`, `anios_por_mes`, `metodo`, `origen_climatologia`, `fuente_climatologia` y `credito` («Climatología 1991-2020 de NOAA PSL (OISST, doi:10.25921/RE9P-PT57).» o, en las calculadas, «Climatología <periodo> calculada por el INOCAR con OISST (doi:10.25921/RE9P-PT57).»). El motor lleva `periodo_efectivo` a `periodo_referencia_efectivo`, al método, al subtítulo y a las exportaciones.
- **Índice Niño 1+2 real** (`serie_indice_nino12`): media de área (pesos cos φ) de la región oficial (0–10° S, 90–80° O; celdas nativas) menos la climatología **diaria** 1991-2020 de PSL (`sst.day.mean.ltm.1991-2020.nc`); semanas domingo–sábado etiquetadas con el miércoles (como NOAA CPC); una semana con menos de 4 días con dato vale `None`. Devuelve `fechas` (miércoles ISO), `valores` (2 decimales), `sst`, `dias`, `parcial`, `preliminar`, `actual`, `sst_actual`, `fecha_actual`, `semana_actual` ({desde, hasta, dias, parcial, preliminar}), `fase` («El Niño (fase cálida)» ≥ +0.5 °C, «La Niña (fase fría)» ≤ −0.5 °C, «neutra»), `semaforo` (rojo, ámbar, verde), `metodo`, conteos de días, `datos_reales`, `procedencia`, `insignia`, `credito`, `fecha_corte`, `origen_datos` y `aviso` si faltan días. El sistema no calcula categorías de intensidad. Contrastado con `wksst9120.for` de NOAA CPC: diferencia máxima 0.13 °C y media 0.05 °C en 10 semanas completas. Valores de control del 29/10/2026: SST de la región oficial 26.51 °C, anomalía diaria +5.19 °C; semana del 18 al 24/10/2026 (miércoles 21/10) +5.17 °C (CPC +5.2 °C).
- **Modos y entorno**: `SOME_NOAA_MODO=auto|simulado|real`, `SOME_NOAA_SIN_RED=1` (solo la caché; estado `sin_conexion`), `SOME_NOAA_SIN_SINCRONIZACION=1` (sin hilo), `SOME_RUTA_NOAA` (carpeta de la caché, se lee al importar). Se leen en cada llamada, salvo la ruta.
- **Estado (RF-21) real** (`noaa/estado.py`, sin red, nunca lanza): `operativa`; `degradada` si PSL limitó con 429 en la última hora, la latencia de la verificación es ≥ 8 s, la verificación tiene más de 13 h, NCEI no responde (final/preliminar estimado), el último día de PSL es anterior a hoy − 3, hubo errores en la última sincronización o solo responde NCEI; `sin_conexion` si no responde ninguno o en modo sin red. Historial de 14 días con `origen` (`psl+ncei`, `psl`, `ncei`, `cache`) y `sin_registro`. Sincronización (`noaa-sincronizacion`): 30 s tras arrancar, luego cada 6 h (30 min si falló).
- **Herramienta**: `python -m backend.herramientas.preparar_noaa [--verificar | --estimar] [--solo-diario | --climatologia | --indice] [--desde aaaa-mm-dd] [--hasta aaaa-mm-dd]`; con la caché vacía, unos 20 min (medido: 13 min 32 s + 6 min 42 s; ~15.3 MB descargados, ~18 MB en disco). Salida 0 bien, 1 errores, 2 sin red o modo simulado.
- **Administración**: `noaa.resumen()` se publica en `GET /api/admin/sistema` como `noaa`.

---

## 5. Catálogo (`catalogo.py`)

### Fuentes y variables

| fuente.id | nombre | variables (id → nombre, unidad, paleta) |
|---|---|---|
| `noaa_oisst` | NOAA OISST v2.1 | `sst` → Temperatura superficial del mar, °C, `viridis` |
| `copernicus_phy` | Copernicus Marine GLOBAL_ANALYSISFORECAST_PHY_001_024 | `thetao` → Temperatura del agua (por profundidad), °C, `viridis`; `so` → Salinidad, PSU, `cividis`; `uo` → Velocidad zonal, m/s; `vo` → Velocidad meridional, m/s; `corrientes` → Corrientes (vectores), m/s; `zos` → Altura del nivel del mar, m, `viridis`; `mlotst` → Profundidad de la capa de mezcla, m, `cividis` |
| `chirps_v3` | CHIRPS v3 (Climate Hazards Center, UCSB) | `precip` → Precipitación, mm/día, `YlGnBu` |

Créditos (campo `credito`, obligatorio en toda salida, RF-12). Siguen las licencias revisadas el 01/11/2026 ([CONDICIONES-USO-FUENTES.md](../../CONDICIONES-USO-FUENTES.md)):
- **NOAA:** «NOAA OISST v2.1 (Huang et al., 2020; doi:10.25921/RE9P-PT57), NOAA NCEI y CDR Program, vía NOAA PSL.»
  - El final cambia según de dónde salieron los días de la consulta: «vía NOAA PSL», «vía NOAA NCEI» o «vía NOAA PSL y NOAA NCEI» (`noaa.constantes.credito_para_origen`).
  - Se cita el conjunto con su año, 2020; el artículo de 2021 es otra referencia. Se reconoce al CDR Program, como pide su Use Agreement.
  - Identificador `NOAA-OISST-v2.1`. Licencia: «Datos abiertos de NOAA sin restricciones de uso…», con las tres prohibiciones de NOAA.
  - **Datos reales** en modo real. Si no, responde el simulador de respaldo, señalado, con el crédito «Simulación del SOME con la forma de NOAA OISST v2.1; no son datos de NOAA.».
  - Una consulta con varios Dataset diarios (p. ej., «Mismo mes cada año» o Comparar) da un solo crédito con la suma de sus días por servidor (`motor._credito_combinado`).
  - Índice Niño 1+2: «<crédito de NOAA según el origen de sus días> Índice calculado por el INOCAR (SOME) con la climatología diaria 1991-2020 de PSL.» (≤ 195 caracteres): no es el índice oficial de NOAA CPC.
  - Climatologías: la 1991-2020 cita «Climatología 1991-2020 de NOAA PSL (OISST, doi:10.25921/RE9P-PT57).». La 1981-2010 y la 1993-2022, «Climatología <periodo> calculada por el INOCAR con OISST (doi:10.25921/RE9P-PT57).»: no son productos de NOAA.
- **Copernicus:** «Generated using E.U. Copernicus Marine Service Information; análisis diario, https://doi.org/10.48670/moi-00016.», que es la forma que recomienda la cláusula 2.4.a de su licencia para obras derivadas.
  - Con anomalías se añade «Climatología GLORYS12, https://doi.org/10.48670/moi-00021.». Si una consulta solo usó la climatología, el motor antepone «Generated using E.U. Copernicus Marine Service Information; » sin repetirlo.
  - El crédito del catálogo nombra los dos DOI.
  - Identificador `CMEMS-GLOBAL_ANALYSISFORECAST_PHY_001_024`. Licencia: «Licencia de uso de los productos del Copernicus Marine Service (…): gratuita, mundial y perpetua; exige el crédito con los DOI, declarar las modificaciones y no sugerir aval de la UE. Sin garantía.».
  - **Datos reales** en modo real. Si no, responde el simulador de respaldo, señalado, con el crédito «Simulación del SOME con la forma de Copernicus Marine GLOBAL_ANALYSISFORECAST_PHY_001_024; no son datos de Copernicus.».
  - Si una consulta combina datos reales y simulados, el crédito solo nombra los reales: la insignia «mixto» y el aviso señalan el resto.
- **CHIRPS:** «CHIRPS v3.0, CHC, UC Santa Barbara (Funk et al., 2026; doi:10.15780/G2JQ0P), CC BY 4.0.».
  - Con días o climatología de la réplica: «…, vía la réplica de dynamical.org; CC BY 4.0.», «…; parte de los días vía la réplica de dynamical.org; CC BY 4.0.» o «…; climatología calculada con la réplica de dynamical.org; CC BY 4.0.». Miden ≤ 150 caracteres.
  - `credito_replica` lleva la atribución literal de dynamical.org («… processed by dynamical.org from the Climate Hazards Center data archive»), su cita y el aviso de que redondea los valores a 8 bits de mantisa.
  - Identificador `CHC-CHIRPS-v3.0`. Licencia: CC BY 4.0, con su enlace, según la página oficial del CHC (https://www.chc.ucsb.edu/data/chirps3, consultada el 01/11/2026) y Funk et al. (2026); el CHC declara además CHIRPS3 de dominio público. El README-CHIRPSv3.0.txt no trae licencia.
  - En el catálogo («Acerca de», «Estado de fuentes»), si la caché tiene días de la réplica, el crédito añade «Parte de los datos de la caché local proviene de la réplica de dynamical.org.» (165 caracteres; no va en el pie de las figuras, que usa el de la consulta).
  - **Datos reales.**
- **Todas las fuentes:** las exportaciones CSV (línea `# Procesamiento:`) y NetCDF (atributo `aviso_procesamiento`), la sección «Fuentes y métodos» de los PDF y «Acerca de» llevan `cfg.AVISO_PROCESAMIENTO`.
  - El aviso dice que el INOCAR procesó los datos con el SOME, que no son datos originales de los proveedores ni cuentan con su aval, y que se entregan sin garantía.
  - El NetCDF lleva además los atributos ACDD `license` y `acknowledgement`.
  - El CSV con días de la réplica añade `# Crédito de la réplica:`.
  - El pie de las figuras (PNG exportado, figuras de los reportes y de pantalla) lleva, a la izquierda de la insignia y solo con datos reales o mixtos, «Procesado por el INOCAR (SOME); no es un dato original del proveedor ni cuenta con su aval.»; si el crédito es de CHIRPS, añade el enlace a CC BY 4.0 y, si se usó la réplica, que sus valores vienen redondeados (`graficos._aviso_pie`).
  - En «Fuentes y métodos» del PDF, la tabla nombra la licencia de cada fuente en corto y su texto completo va debajo, a ancho completo. El crédito de NOAA y el de CHIRPS se rehacen con lo que usaron todas las consultas del documento (servidores de NOAA y climatologías; días del CHC y de la réplica).
  - «Acerca de» repite el aviso en `frontend/js/modulos/acerca.js`: si cambia `cfg.AVISO_PROCESAMIENTO`, hay que cambiarlo también allí.

Paleta de anomalías: `BrBG` para precipitación (marrón seco / verde húmedo) y `PuOr_r` para las demás (seguras para daltonismo). Nunca usar `jet`. Las paletas `termico` (temperaturas) y `salino` (salinidad) son propias de `graficos.py`, inspiradas en «thermal» y «haline» de cmocean: de azul profundo a amarillo, como esperan los oceanógrafos.

Dibujo del campo en pantalla: por defecto el PNG se **suaviza** (interpolación bilineal entre centros de celda más un filtro gaussiano ligero, con la tierra enmascarada a media celda), de modo que el mapa se ve como un análisis continuo y no como celdas de 0.25°; el valor que se muestra bajo el cursor sale siempre de la malla original. Opcionalmente se trazan **isolíneas** rotuladas (niveles «redondos» con `MaxNLocator`). La consulta de mapa admite `suavizado` (true por defecto) e `isolineas` (false), y la interfaz guarda la preferencia del usuario (junto con la opacidad de la capa) en `localStorage`.

### Recortes (RF-17) — `{id, nombre, tipo, bbox}` con bbox = [lon_min, lat_min, lon_max, lat_max]

- `area_estudio` «Área de estudio» tipo `area` [−95, −6, −78, 6]
- `nino12` «Región Niño 1+2» tipo `region_nino` [−90, −6, −80, 0] (la región real llega a 10° S; en la demo se recorta al área)
- `nino3_parcial` «Región Niño 3 (parte en el área)» tipo `region_nino` [−95, −5, −90, 5]
- `golfo_guayaquil` «Golfo de Guayaquil» tipo `area` [−81.5, −3.6, −79.8, −2.0]
- `galapagos` «Galápagos» tipo `provincia` [−92.5, −1.8, −89.0, 1.0]
- `esmeraldas` «Esmeraldas» tipo `provincia` [−80.2, 0.3, −78.4, 1.5]
- `manabi` «Manabí» tipo `provincia` [−81.0, −1.9, −79.6, 0.4]
- `santa_elena` «Santa Elena» tipo `provincia` [−81.1, −2.7, −80.1, −1.9]
- `guayas` «Guayas» tipo `provincia` [−80.7, −3.3, −79.3, −1.3]
- `el_oro` «El Oro» tipo `provincia` [−80.5, −4.0, −79.3, −3.1]
- `cuenca_guayas` «Cuenca del río Guayas» tipo `cuenca` [−80.5, −3.0, −78.8, 0.5]
- `cuenca_esmeraldas` «Cuenca del río Esmeraldas» tipo `cuenca` [−79.9, −0.6, −78.3, 1.3]

Los recortes de tierra (provincias, cuencas) sirven para CHIRPS; los marinos para NOAA/Copernicus. El motor aplica la máscara correspondiente y, si el recorte no tiene celdas válidas para la variable, responde 422 con un mensaje claro.

---

## 6. Motor (`motor.py`) — funciones puras sobre `xr.Dataset`

- `recortar(ds, bbox)`; `promedio_area(ds)`; `promedio_tiempo(ds)`.
- `serie(ds, variable, agregacion)` → `pd.Series` (diaria/semanal/mensual).
- `climatologia_mensual(conector, variable, bbox, periodo_referencia, profundidad)` → 12 valores (promedio de área).
- `anomalia(ds_o_serie, climatologia_mensual)` → resta la climatología del mes correspondiente (documenta `metodo`: «Anomalía = valor − climatología mensual {periodo}; climatología calculada por promedio de área»).
- `resumen_lenguaje_sencillo(serie, unidad, nombre_variable, climatologia=None, anomalias=None)` → `dict` con `media`, `minimo{valor,fecha}`, `maximo{valor,fecha}`, `tendencia_por_anio`, `ultimo{valor,fecha,anomalia}` y `texto: list[str]` de frases como: «Entre el 01/10/2025 y el 27/10/2026 la temperatura superficial del mar promedió 24.6 °C.», «El valor más alto, 27.9 °C, se registró el 12/03/2026; el más bajo, 21.3 °C, el 18/09/2026.», «El último dato disponible (27/10/2026) es 25.1 °C, es decir 1.8 °C por encima de lo normal para octubre (periodo de referencia 1991‑2020).», «La tendencia del periodo es de +0.6 °C por año.» Formatear con punto decimal y dd/mm/aaaa.
- `perfil(ds)` → valores por profundidad en un punto y fecha; `cortina(ds)` → matriz (depth × time).
- `mismo_mes_cada_anio(conector, variable, bbox, mes, anios, profundidad)` → valor promedio del mes por año y la climatología del mes.
- `comparar(serie_a, serie_b)` → diferencia media, correlación, RMSE, texto.
- `vectores(ds_u, ds_v, paso)` → submuestreo para flechas.
- `serie_indice_enso(inicio=None, fin=None)` → índice Niño 1+2 semanal para el widget `indice_enso` y la figura 4 del boletín: con NOAA real, el de `ConectorNOAA.serie_indice_nino12` (semanas centradas en el miércoles; `valores` puede traer `None`), con `fase`, `semaforo`, `texto` («El índice Niño 1+2 (NOAA OISST, región 0–10° S, 90–80° O) es +5.17 °C en la semana del 18/10/2026 al 24/10/2026 (datos preliminares): condición El Niño (fase cálida).»), `metodo`, `semana_actual`, `sst_actual`, `fecha_corte` y la procedencia; con NOAA simulado, el índice simulado (señalado). `fin` por defecto: la fecha de corte del sistema.

Toda respuesta del motor incluye `metodo` (texto) y `fecha_corte` de la fuente.

---

## 7. API REST (prefijo `/api`, JSON, UTF‑8)

Errores: `{ "detalle": "mensaje en español" }` con códigos 400/401/403/404/422/500. Autenticación por cabecera `Authorization: Bearer <token>`; sin token → 401. Roles: `oceanografo`, `analista`, `revisor`, `administrador`. Las rutas `/api/admin/*` exigen `administrador`.

### 7.1 Autenticación

- `POST /api/auth/ingresar` `{usuario, clave}` → `{token, usuario:{id, usuario, nombre, rol, departamento, correo}}`; 401 «Usuario o contraseña incorrectos».
- `POST /api/auth/salir` → `{ok:true}`.
- `GET /api/auth/yo` → usuario actual.

Usuarios de demo (clave `some2026` para todos): `mvera` «Dra. María Vera», oceanógrafa, Oceanografía y Meteorología Marina; `jloor` «Ing. Jorge Loor», analista, Oceanografía y Meteorología Marina; `rcastro` «CPFG Ricardo Castro», revisor (jefe de departamento); `admin` «Administrador de TI», administrador, Tecnologías de la Información.

### 7.2 Catálogo y fuentes

- `GET /api/catalogo` → `{fuentes:[{id, nombre, proveedor, producto, identificador, credito, licencia, resolucion, cobertura_temporal, habilitada, variables:[{id, nombre, unidad, descripcion, paleta, paleta_anomalia, tiene_profundidad, es_vector, habilitada}], estado:{...}}], recortes:[...], periodos_referencia:["1991-2020","1981-2010","1993-2022"], profundidades:[0,5,10,20,30,50,75,100], fecha_inicio:"2023-10-01", fecha_corte:"2026-10-29", bbox_area:[-95,-6,-78,6], datos_simulados:false, procedencia:"real", insignia:"Datos reales", insignia_cabecera, insignia_cabecera_corta, titulo_cabecera, fuentes_reales:["noaa_oisst","copernicus_phy","chirps_v3"]}`. `fecha_corte` es la del sistema (`catalogo.fecha_corte_sistema()`: el último día de la caché de NOAA OISST; 27/10/2026 en modo simulado). Cada fuente figura en `fuentes_reales` solo en modo real. `insignia_cabecera` (`catalogo.insignias_cabecera()`): con las tres reales, «Datos reales: NOAA OISST, Copernicus Marine y CHIRPS v3» (corta «Datos reales»); con Copernicus real y NOAA simulado, «Demostración: CHIRPS v3 y Copernicus Marine con datos reales; NOAA OISST simulado»; con NOAA real y Copernicus simulado, «Demostración: NOAA OISST y CHIRPS v3 con datos reales; Copernicus Marine simulado»; con ambos simulados, «Demostración: datos simulados, salvo precipitación CHIRPS (real)». Cada fuente incluye además `datos_reales` e `insignia`. `datos_simulados` es `true` solo mientras alguna fuente sea simulada (con las tres reales, `false` y procedencia «real»; si no, «mixto»).
- `GET /api/fuentes/estado` → `[{fuente, nombre, estado, fecha_corte, latencia_ms, ultimo_chequeo, mensaje, habilitada, identificador, credito, datos_reales, insignia, historial:[{fecha, estado, origen?, origen_texto?}] (14 días; estados operativa | degradada | sin_conexion | sin_registro)}]`. El historial de CHIRPS, Copernicus y NOAA sale de `conector.historial_estado` (verificaciones reales); el de una fuente sin ese método, de un patrón determinista. NOAA añade (de `conector.detalle_estado()`, sin red) `modo_sin_red` y `noaa:{modo:"real"|"simulado", modo_configurado:"auto"|"simulado"|"real", texto_modo, acceso:{disponible, motivo}, servidores:{psl:S, ncei:S}, publicacion:{ultimo_dia_psl, ultimo_final, ultimo_preliminar, consultado_psl, consultado_ncei, texto}, climatologia:{periodos_en_cache, total:3, ltm_diaria_nino12, fuente}, cache:{fecha_corte, ultimo_final, dias_finales, dias_preliminares, dias_faltantes, dias_por_origen:{psl, ncei}, tamano_bytes}}` con `S = {estado:"operativa"|"degradada"|"sin_conexion"|"sin_registro"|"pausa"|"sin_red"|"simulado", texto_estado, detalle, latencia_ms, ultimo_contacto, servidor}`; su historial trae `origen` (`psl+ncei`, `psl`, `ncei`, `cache` o `null`) y `origen_texto`. CHIRPS añade (de `conector.detalle_estado()`, sin red) `modo_sin_red`, `caminos:[{id:"chc"|"espejo", nombre, servidor, estado:"operativa"|"degradada"|"sin_conexion"|"sin_registro"|"sin_red"|"no_instalada", texto_estado, detalle, ultimo_contacto, latencia_ms, productos:{final, preliminar} (último día publicado por ese camino según lo último consultado), productos_consultado, disyuntor_hasta?}]` y `cache:{fecha_corte, ultimo_final, ultimo_preliminar, dias_finales, dias_preliminares, dias_faltantes, dias_por_origen:{chc, espejo}}`. Copernicus añade (de `conector.detalle_estado()`, sin red) `modo_sin_red` y `copernicus:{modo:"real"|"simulado", modo_configurado:"auto"|"simulado"|"real", texto_modo, acceso:{disponible, motivo}, paquete:{instalado, version}, credenciales:{estado:"validas"|"invalidas"|"ausentes"|"sin_verificar"|"error", texto, verificado} (nunca el usuario ni la contraseña), servicio:{estado, texto_estado, detalle, latencia_ms, ultimo_contacto, servidor}, analisis:{ultimo_dia, ultimo_definitivo, boletin_semanal, fin_pronostico, regla, consultado, texto}, climatologia:{producto, doi, periodo, en_cache, total}, cache:{fecha_corte, ultimo_definitivo, ultimo_nrt, dias_con_dato, dias_nrt, tamano_bytes}}`; su historial sale de `conector.historial_estado` (verificaciones reales). Si un conector fallara al dar su estado, la fuente figura «sin_conexion» con el motivo (el estado nunca tumba la API).
- `GET /api/salud` (pública) → `{estado, version, fecha_corte, datos_simulados, procedencia, fuentes_reales}` (`fecha_corte`: la del sistema).

### 7.3 Consultas (todas `POST`, cuerpo JSON)

Objeto **área** común: `{"tipo":"bbox","bbox":[lon_min,lat_min,lon_max,lat_max]}` | `{"tipo":"recorte","recorte":"nino12"}` | `{"tipo":"punto","lat":-2.0,"lon":-81.0}`.

- `/api/consultas/mapa` `{fuente, variable, area, fecha?, periodo?:{inicio,fin}, profundidad?, modo:"valor"|"anomalia", periodo_referencia?}` (si hay `periodo`, es el promedio del periodo; si `fecha`, un día). Respuesta: `{lats:[..], lons:[..], valores:[[..]] (null en tierra), bbox, unidad, vmin, vmax, paleta, imagen_png:"data:image/png;base64,...", titulo, subtitulo, fuente:{id, nombre, identificador, credito, datos_reales, insignia}, metodo, fecha_corte, estadisticas:{media, minimo, maximo, celdas_validas}, datos_simulados, procedencia, insignia, dias_preliminares?, dias_faltantes?}`. La imagen PNG la genera `graficos.mapa_png(valores, bbox, paleta, vmin, vmax)` con fondo transparente, sin ejes, `interpolation="nearest"`, tamaño proporcional al bbox, para superponerla en Leaflet con `L.imageOverlay(imagen_png, [[lat_min, lon_min],[lat_max, lon_max]])`.
- `/api/consultas/serie` `{fuente, variable, area, periodo:{inicio,fin}, profundidad?, agregacion:"diaria"|"semanal"|"mensual", periodo_referencia?}` → `{fechas:[iso], valores:[..], climatologia:[..] (misma longitud, valor del mes), anomalias:[..], unidad, nombre_variable, resumen:{...}, fuente, metodo, fecha_corte, datos_simulados, procedencia, insignia}`. Todas las consultas de esta sección devuelven los campos de procedencia (§4.2); la comparación es «mixta» si A y B tienen procedencias distintas.
- `/api/consultas/climatologia` `{fuente, variable, area, periodo_referencia, profundidad?}` → `{meses:["ene",…,"dic"], valores:[12], unidad, periodo_referencia, metodo, fuente}`
- `/api/consultas/mismo-mes` `{fuente, variable, area, mes:1..12, profundidad?, periodo_referencia?}` → `{anios:[2023?,2024,2025,2026], valores:[..], climatologia_mes, anomalias:[..], unidad, texto:[..], fuente, metodo}` (solo los años con datos para ese mes).
- `/api/consultas/perfil` `{fuente:"copernicus_phy", variable:"thetao"|"so", lat, lon, fecha}` → `{profundidades:[..], valores:[..], unidad, capa_mezcla_m, texto:[..], fuente, metodo}`
- `/api/consultas/cortina` `{fuente, variable, lat, lon, periodo}` → `{fechas:[..], profundidades:[..], valores:[[depth][time]], unidad, fuente, metodo}`
- `/api/consultas/comparar` `{a:{fuente, variable, area, periodo, profundidad?}, b:{…}, agregacion}` → `{a:{etiqueta, fechas, valores, unidad, procedencia…}, b:{…}, diferencia:{media, rmse, correlacion, n, solo_correlacion?}, mismas_unidades, texto:[..], fuentes:[..], metodo, procedencia…}` (si los periodos difieren, alinear por día relativo al inicio y mostrarlo así). Con **unidades distintas** (p. ej. SST frente a precipitación) no se calculan la diferencia ni el RMSE (`null`, `solo_correlacion: true`), solo la correlación de Pearson; la interfaz dibuja dos ejes y muestra «No aplica». La procedencia es «mixta» si A y B la tienen distinta.
- `/api/consultas/vectores` `{fuente:"copernicus_phy", variable:"corrientes", area, fecha, profundidad?, paso:2}` → `{lats, lons, u:[[..]], v:[[..]], magnitud:[[..]], unidad, fuente}`

### 7.4 Exportación (RF-10, RF-18)

- `POST /api/exportar` `{formato:"csv"|"png"|"netcdf", clase:"mapa"|"serie"|"perfil"|"cortina", consulta:{…mismo cuerpo que la consulta…}}` → archivo binario con `Content-Disposition: attachment; filename="SOME_<fuente>_<variable>_<fecha>.<ext>"`. CSV estándar con `,` como separador y punto decimal (RNF-01), con una cabecera de líneas de comentario `#`: `# Fuente:`, `# Crédito:` (y `# Crédito de la réplica:` si se usó), `# Licencia:`, `# Procesamiento:`, `# Método:`, `# Fecha de corte:` y la insignia de procedencia (`# Datos reales · NOAA OISST`, `# Datos reales: CHIRPS v3 (CHC, UCSB)`, `# Datos reales · Copernicus Marine` o `# Datos simulados para demostración`; con CHIRPS y NOAA, `# Días preliminares:` si los hay). El nombre del archivo lleva la fecha consultada (para un mapa de NOAA del último día, la fecha de corte de NOAA: `SOME_noaa_oisst_sst_20261029.csv`) y, sin fecha, la fecha de corte del sistema. Con fuentes reales, además, `# Origen de los días: …` y la línea de la climatología. PNG: figura matplotlib completa (título, barra de color, crédito de la consulta —cita la réplica si se usó—, aviso de procesamiento a la izquierda de la insignia —«Procesado por el INOCAR (SOME); no es un dato original del proveedor ni cuenta con su aval.», con el enlace a CC BY 4.0 si el crédito es de CHIRPS y el aviso de redondeo si se usó la réplica; solo con datos reales o mixtos— e insignia al pie: verde para datos reales, ámbar para simulados o mixtos). NetCDF: `to_netcdf(engine="scipy")` con atributos CF, `comment=<insignia>`, `procedencia`, `datos_reales`, `credito` de la consulta, `identificador`, `licencia`, los atributos ACDD `license` y `acknowledgement`, `aviso_procesamiento` (cfg.AVISO_PROCESAMIENTO) y, con fuentes reales, `origen_datos` y `origen_climatologia` (con la réplica de CHIRPS, también `credito_replica`). Cabeceras HTTP `X-SOME-Procedencia` y `X-SOME-Datos-Simulados`.

### 7.5 Reportes (RF-09) y trabajos

- `GET /api/reportes/plantillas` → `[{id, nombre, descripcion, parametros:[{id, etiqueta, tipo:"texto"|"fecha"|"periodo"|"recorte"|"opcion"|"booleano", opciones?, por_defecto}]}]`. Plantillas:
  1. `boletin_erfen` «Boletín quincenal para el CN‑ERFEN»: parámetros `periodo` (por defecto los últimos 15 días), `recorte` (por defecto `nino12`), `incluir_precipitacion` (sí), `autor`. Contenido: carátula, resumen ejecutivo generado (texto del motor), mapa de SST y de anomalía, serie de Niño 1+2 de los últimos 12 meses con climatología, perfil térmico en 85° O/2° S, mapa de precipitación acumulada 15 días por provincia costera con tabla, créditos y método, fecha de corte de cada fuente.
  2. `informe_variable` «Informe de una variable»: `fuente`, `variable`, `recorte`, `periodo`, `profundidad`, `periodo_referencia`, `autor`. Mapa promedio, mapa de anomalía, serie con resumen, climatología mensual, tabla de estadísticas.
  3. `comparativo` «Comparativo de dos periodos»: `fuente`, `variable`, `recorte`, `periodo_a`, `periodo_b`, `autor`. Dos mapas, series superpuestas, diferencias.
- `POST /api/reportes` `{plantilla, parametros}` → `{trabajo_id, estado:"en_cola"}`; el trabajo lo ejecuta `trabajos.py` en un hilo y guarda el PDF en `datos/reportes/<trabajo_id>.pdf`.
- `GET /api/trabajos` → trabajos del usuario (los administradores ven todos) `[{id, tipo:"reporte"|"exportacion", titulo, estado:"en_cola"|"procesando"|"listo"|"error", progreso:0‑100, mensaje, creado, terminado, archivo:"/api/reportes/<id>/descargar"|null}]`; `GET /api/trabajos/{id}`.
- `GET /api/reportes/{id}/descargar` → PDF (`application/pdf`, nombre `SOME_<plantilla>_<aaaammdd>.pdf`).

PDF: A4 vertical, carátula con logotipo SOME (texto), «INOCAR — Dirección de Oceanografía y Meteorología Marina», título, periodo, autor, fecha de generación; encabezado/pie en cada página con «SOME · <insignia de procedencia del documento>» y número de página; cada figura con su crédito y su insignia; sección final «Fuentes y métodos» con la procedencia de cada fuente, identificador, crédito, licencia, fecha de corte y método. La procedencia del documento se calcula con las fuentes que realmente usó: con las tres fuentes reales, el boletín ERFEN (SST, anomalía e índice Niño 1+2 de NOAA, perfil de Copernicus y lluvia de CHIRPS) es «real» con la insignia «Datos reales», y un informe o un comparativo de NOAA, CHIRPS o Copernicus es «real» con la insignia de su fuente; si alguna fuente respondió con su simulador, el documento sale «mixto» (o «simulado» si todo lo es) y lo explica. En el boletín, la figura 4 (índice Niño 1+2 semanal) lleva el pie «Índice Niño 1+2 semanal (NOAA OISST v2.1, región 0–10° S, 90–80° O; anomalía respecto de 1991-2020; semanas centradas en el miércoles)», el resumen ejecutivo usa el texto del índice (fase cálida, fría o neutra, sin categorías de intensidad) y, con días preliminares de NOAA, se indica cuáles. La sección de precipitación del boletín cita CHIRPS v3 real, dice de qué camino salieron los días (servidor del CHC y/o réplica de dynamical.org) y con qué se calculó el acumulado normal, indica los días preliminares y, si CHIRPS no tiene datos del periodo, lo explica sin impedir que el boletín se genere. Cuando el documento usó la réplica (días o climatología), «Fuentes y métodos» añade una fila «Réplica de CHIRPS v3 en dynamical.org» (crédito, CC BY 4.0, último día publicado) y un párrafo con el origen de los datos. `GET /api/trabajos` incluye `procedencia` e `insignia` de cada reporte listo. Todo en español, fechas dd/mm/aaaa. Para que la demo sea visible, el trabajo simula progreso (p. ej., 4 etapas con `time.sleep(0.6)` entre ellas) y registra mensajes como «Consultando NOAA OISST…», «Calculando anomalías…», «Generando figuras…», «Componiendo PDF…».

### 7.6 Panel personal (RF-11)

- `GET /api/panel` → `{widgets:[{id, tipo, titulo, config:{…}, ancho:1|2|3}]}` (del usuario actual; si no tiene, se crea un panel por defecto según su rol).
- `PUT /api/panel` cuerpo igual → guarda.
- `GET /api/panel/tipos` → tipos disponibles: `kpi_variable` (valor actual de una variable en un recorte, con anomalía y flecha), `mini_serie` (serie de 90 días), `mini_mapa` (imagen PNG de la última fecha), `estado_fuentes`, `trabajos_recientes`, `mis_vistas`, `indice_enso` (serie semanal del índice Niño 1+2 con semáforo: con NOAA real, el índice real de la región oficial 0–10° S, 90–80° O, semanas centradas en el miércoles, de los últimos `meses` hasta la fecha de corte del sistema; las semanas sin dato vienen `null` y la semana en curso se marca incompleta y preliminar), `precipitacion_provincias` (barras por provincia de los últimos 7 días), cada uno con su esquema de `config`.
- `POST /api/panel/widget/datos` `{tipo, config}` → datos listos para dibujar (`{valor, unidad, anomalia, texto, fechas?, valores?, imagen_png?, …}`).

Panel por defecto del oceanógrafo: KPI SST Niño 1+2, KPI precipitación Guayas 7 días, KPI capa de mezcla golfo de Guayaquil, índice ENSO, mini mapa anomalía SST, mini serie SST Niño 1+2, estado de fuentes, trabajos recientes. El revisor: índice ENSO, mini mapa, precipitación por provincias, trabajos recientes, mis vistas (compartidas). El administrador: estado de fuentes, trabajos recientes, y un `kpi_sistema` (usuarios, consultas hoy, tamaño de la caché).

### 7.7 Mis vistas (RF-16)

- `GET /api/vistas` → `{propias:[…], compartidas:[…]}` con `{id, nombre, descripcion, modulo:"mapa"|"series"|"perfiles"|"comparar", parametros:{…}, compartida:bool, propietario:{usuario, nombre}, creada, actualizada}`.
- `POST /api/vistas` `{nombre, descripcion, modulo, parametros, compartida}`; `PUT /api/vistas/{id}` (solo el propietario); `DELETE /api/vistas/{id}` (solo el propietario); `GET /api/vistas/{id}` (propietario o compartida).

### 7.8 Administración (solo `administrador`)

- `GET/POST /api/admin/usuarios`, `PUT/DELETE /api/admin/usuarios/{id}` (`{usuario, nombre, rol, departamento, correo, activo, clave?}`); no se puede eliminar al propio administrador.
- `PUT /api/admin/catalogo/fuentes/{fuente}` `{habilitada}`; `PUT /api/admin/catalogo/variables/{fuente}/{variable}` `{habilitada}` (RF-20).
- `GET /api/admin/auditoria?limite=200` → `[{fecha, usuario, accion, detalle, ip}]`. Se registran: ingresos y salidas, creación/edición/eliminación de usuarios, cambios del catálogo, vistas compartidas, reportes generados.
- `GET /api/admin/sistema` → `{version, inicio, tiempo_activo_s, usuarios_activos, consultas_total, consultas_hoy, cache:{entradas, aciertos, fallos}, trabajos:{en_cola, procesando, listos, error}, datos_simulados, procedencia, insignia, insignia_cabecera, fuentes_reales, fecha_corte (la del sistema), chirps:{…resumen de la caché y la sincronización de CHIRPS…}, copernicus:{…`copernicus.resumen()`: modo, estado, último día de análisis, credenciales (solo su estado), conjuntos en caché, climatología, tamaño, descargas, sincronización y errores recientes…}, noaa:{…`noaa.resumen()`: modo, estado, fecha de corte, servidores PSL y NCEI, publicación, días en caché (finales, preliminares, faltantes y por origen), climatologías, tamaño, bytes descargados, peticiones, última y próxima sincronización y errores recientes…}}` (cada resumen es `null` si su paquete no está disponible).
- `GET /api/salud` (pública) → `{estado:"ok", version, fecha_corte, datos_simulados, procedencia, fuentes_reales}` (§7.2).

### 7.9 Vistas iniciales y datos de ejemplo

Al arrancar sin `datos/`, `almacen.py` crea los 4 usuarios, un panel por defecto por usuario, **tres vistas de ejemplo** (una compartida por `mvera`: «Anomalía SST Niño 1+2 — últimos 12 meses»; una privada de `mvera`: «Perfil térmico 85° O, 2° S»; una compartida por `jloor`: «Lluvia acumulada Guayas — estación lluviosa 2026»), y **dos trabajos** previos ya «listos» (un boletín ERFEN de hace 15 días, generado realmente al arrancar en segundo plano, y un informe de variable), para que la demo no empiece vacía. Auditoría con ~20 entradas de ejemplo de los últimos días.

---

## 8. Interfaz (frontend)

### 8.1 Estructura y navegación

- *Single page* con enrutador por `location.hash`: `#/acceso`, `#/panel`, `#/consultas`, `#/mapa`, `#/series`, `#/perfiles`, `#/comparar`, `#/reportes`, `#/vistas`, `#/fuentes`, `#/admin`, `#/acerca`. Parámetros en el hash tras `?` (p. ej. `#/mapa?vista=3`).
- **Barra lateral** (240 px, colapsable a iconos en < 1100 px): logotipo SOME, grupos «Inicio» (Panel personal), «Analizar» (Consultas, Mapas, Series, Perfiles, Comparar), «Productos» (Reportes, Mis vistas), «Sistema» (Estado de fuentes, Administración [solo admin], Acerca de). Al pie: usuario, rol y botón «Salir».
- **Cabecera**: título del módulo, migas, insignia con el texto del catálogo (`insignia_cabecera`, §7.2): verde **«Datos reales: NOAA OISST, Copernicus Marine y CHIRPS v3»** con las tres fuentes reales; ámbar si alguna cae a su simulador (p. ej. **«Demostración: CHIRPS v3 y Copernicus Marine con datos reales; NOAA OISST simulado»**). Se actualiza con el catálogo. Indicador de fuentes (verde/ámbar/rojo) que enlaza a `#/fuentes`, botón de modo oscuro. El saludo del panel muestra la fecha de corte del sistema.
- Sin sesión, cualquier ruta redirige a `#/acceso`.

### 8.2 Contrato de módulos (`js/app.js`)

```js
window.SOME = {
  estado: { usuario: null, catalogo: null, tema: 'claro' },
  api,                         // de api.js
  registrar(nombre, modulo),   // modulo = { titulo, icono, grupo, requiereRol?, async montar(contenedor, params), desmontar?() }
  navegar(ruta, params = {}),  // cambia el hash
  parametros(),                // params del hash actual
  recargarCatalogo(),
  nombreVariable(fuenteId, variableId), nombreFuente(id), recorte(id),
  eventos: new EventTarget(),  // 'sesion', 'catalogo', 'tema', 'fuentes' (estado de fuentes cada minuto)
};
```

`api.js`: `SOME.api.get(ruta)`, `.post(ruta, cuerpo)`, `.put`, `.del`, `.descargar(ruta, cuerpo, nombreSugerido)`; agrega el *token* desde `localStorage['some_token']`; ante 401 limpia la sesión y navega a `#/acceso`; ante otros errores lanza `Error(detalle)` y la UI muestra `util.toast(mensaje, 'error')`.

`util.js`: `fmtFecha(iso)` → `dd/mm/aaaa`; `fmtFechaHora(iso)`; `fmtNum(x, decimales=1)` (punto decimal, espacio fino para miles); `fmtAnomalia(x, unidad)` → «+1.8 °C»; `toast(mensaje, tipo='info'|'exito'|'error'|'aviso')`; `modal({titulo, contenido, acciones})`; `confirmar(mensaje)`; `descargarBlob(blob, nombre)`; `debounce(fn, ms)`; `el(html)` (crea un nodo desde una plantilla de cadena); `cargando(contenedor, mensaje)`.

### 8.3 Sistema visual (`css/app.css`)

Variables en `:root` y redefinidas en `[data-tema="oscuro"]`:

```
--fondo, --superficie, --superficie-2, --borde, --texto, --texto-2, --primario (#0B4F6C claro / #5BB5D5 oscuro), --primario-texto, --acento (#F4A261), --exito (#2A9D8F), --aviso (#E9C46A), --peligro (#C8553D), --sombra, --radio (10px), --radio-sm (6px), --fuente ('Inter', 'Segoe UI', system-ui, sans-serif), --mono
```

Claro: fondo `#F3F6F9`, superficie `#FFFFFF`, texto `#132A3A`, borde `#D9E1E8`. Oscuro: fondo `#0F1B24`, superficie `#172633`, texto `#E6EEF3`, borde `#27394A`.

Clases de componentes que **todos los módulos usan** (las define `app.css`): `.tarjeta` (superficie, radio, sombra suave, `padding: 16px`), `.tarjeta-titulo`, `.cuadricula` (+ `.col-2`, `.col-3`, `.col-4` → grid responsivo), `.fila` (flex, gap 12), `.boton`, `.boton-primario`, `.boton-secundario`, `.boton-peligro`, `.boton-icono`, `.campo` (etiqueta + control), `.campo label`, `input/select/textarea`, `.insignia` (+ `.insignia-aviso`, `.insignia-exito`, `.insignia-peligro`, `.insignia-info`), `.alerta` (+ variantes), `.tabla` (cebra, cabecera fija), `.kpi` (`.kpi-valor`, `.kpi-etiqueta`, `.kpi-delta` con `.positivo/.negativo`), `.pestanas` + `.pestana.activa`, `.vacio` (estado vacío con icono y texto), `.esqueleto` (carga), `.semaforo` (`.verde/.ambar/.rojo`), `.panel-lateral` (formulario de parámetros a la izquierda, 320 px, con `.resultado` a la derecha), `.barra-herramientas`. Tipografía: 14 px base, títulos 20/24 px, `font-weight` 600. Accesibilidad: foco visible, contraste AA, `aria-label` en botones de icono.

### 8.4 Módulos (comportamiento mínimo)

- **Acceso**: tarjeta centrada con logotipo; sección «Ingreso directo por tipo de usuario» con un botón por cada uno de los 4 roles (rol, nombre, usuario y qué muestra; un clic inicia la sesión con la clave de demostración); debajo, campos usuario/clave y botón «Ingresar»; pie «INOCAR · ESPOL · Equipo T5». Al ingresar: guarda *token*, carga catálogo, navega a `#/panel`.
- **Panel personal**: cuadrícula de widgets (ancho 1‑3 columnas), cada uno con menú (⋯) → «Configurar», «Quitar», «Ancho»; botón «Agregar widget» (modal con los tipos y su configuración); «Guardar panel» persiste con `PUT /api/panel`; «Restablecer» vuelve al panel por defecto. Los KPI muestran valor, unidad, anomalía con flecha y color, y una frase. Botón «Actualizar». Enlaces desde cada widget al módulo correspondiente con los mismos parámetros.
- **Consultas** (constructor, RF-02): formulario con pasos visuales: 1) Fuente y variable (tarjetas por fuente con estado), 2) Área (recorte del catálogo, rectángulo con 4 números, punto lat/lon; **mini mapa Leaflet** donde se puede dibujar el rectángulo arrastrando o hacer clic para el punto), 3) Periodo (fecha única o rango con atajos: «Últimos 15 días», «Últimos 30 días», «Últimos 12 meses», «Este año», «Febrero de este año», «Octubre de este año»), profundidad (si aplica), 4) Qué quiero ver: botones «Mapa», «Serie de tiempo», «Perfil / cortina», «Comparar». Al elegir, navega al módulo con los parámetros en el hash. El mismo formulario se reutiliza (componente `ConstructorConsulta` exportado en `window.SOME.componentes.ConstructorConsulta`) en los paneles laterales de Mapa, Series y Perfiles.
- **Mapa** (RF-06, RF-17, RF-19): panel lateral con los parámetros + conmutador «Valor / Anomalía» + periodo de referencia + «Mostrar vectores de corriente» (solo Copernicus). Mapa Leaflet con teselas OSM (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`, atribución), superposición PNG, **barra de tiempo** (slider de fechas del periodo + botones de día anterior y siguiente y «Reproducir» que avanza cada 700 ms, con iconos de Flaticon UIcons; al mover la barra se pide el mapa de ese día; caché en el cliente por fecha), **valor de la celda** bajo el cursor (lat, lon, valor, unidad) en una caja flotante y al hacer clic, popup con el valor y botón «Ver serie aquí» (navega a `#/series?…punto`). Leyenda con barra de color (gradiente CSS generado desde la paleta: el backend devuelve `paleta_rgb: [[r,g,b]...]` de 16 pasos en la respuesta del mapa), vmin/vmax, unidad. Vectores con `L.polyline` + punta de flecha simple, submuestreados. Barra de herramientas: «Exportar PNG», «Exportar CSV», «Exportar NetCDF», «Guardar vista», «Generar reporte». Panel de estadísticas del área (media, mín, máx, celdas) y crédito de la fuente al pie **siempre visible**.
- **Series** (RF-07, RF-08, RF-15): pestañas «Serie de tiempo», «Climatología mensual», «Mismo mes cada año». Gráfico Plotly con la serie, la climatología (línea discontinua) y, en un panel inferior, las anomalías (barras ± coloreadas). Agregación diaria/semanal/mensual. **Resumen en lenguaje sencillo** en una tarjeta con las frases del motor. Tabla de estadísticas. Exportar CSV/PNG, guardar vista, generar reporte.
- **Perfiles** (RF-13): punto (lat, lon, con mini mapa), fecha, variable `thetao`/`so`; perfil vertical (profundidad en el eje Y invertido) con marca de la capa de mezcla; pestaña «Cortina profundidad‑tiempo» (heatmap Plotly, eje Y invertido) para un periodo.
- **Comparar** (RF-14): dos columnas de parámetros A y B (precargadas con un ejemplo: SST NOAA vs. thetao 0 m Copernicus en Niño 1+2, últimos 12 meses); gráfico superpuesto, tarjeta con diferencia media, RMSE, correlación y frases.
- **Reportes** (RF-09): tarjetas de plantillas → formulario de parámetros generado desde `parametros` → «Generar»; lista de trabajos con estado, barra de progreso y mensaje, actualizada cada 1.5 s mientras haya trabajos activos; botones «Descargar PDF» y «Ver» (abre el PDF en una pestaña nueva); los terminados muestran la fecha.
- **Mis vistas** (RF-16): dos listas (propias / compartidas conmigo) con tarjetas: nombre, módulo, descripción, propietario, fecha, insignia «Compartida»; «Abrir» navega al módulo con los parámetros; «Compartir/Dejar de compartir», «Renombrar», «Eliminar» (confirmar). El botón «Guardar vista» de los módulos abre un modal (nombre, descripción, compartir).
- **Estado de fuentes** (RF-21): tarjeta por fuente con semáforo, estado, fecha de corte, latencia, último chequeo, mensaje, mini historial de 14 días (cuadritos coloreados, con leyenda; «Sin registro» en gris para los días sin verificación; el `title` de cada día dice qué camino respondió), crédito e identificador. La tarjeta de CHIRPS lleva la etiqueta «Conexión real», su historial proviene de verificaciones reales y una lista «Caminos de acceso» con el estado de cada camino (servidor del CHC y réplica dynamical.org), la fecha hasta la que llega cada producto (final y preliminar) en cada uno y lo que sirve la caché local (fechas y días por origen); la de Copernicus también lleva «Conexión real» (y «Datos simulados» si, sin acceso ni caché, sus datos son del simulador) y una lista con el servicio ARCO (estado, latencia de la lectura de prueba, último contacto), las credenciales (solo su estado), el análisis (NRT y mejor análisis hasta…; el pronóstico no se sirve), la climatología GLORYS12 (periodo efectivo y combinaciones en caché) y lo que sirve la caché local; la de NOAA también lleva «Conexión real» (y «Datos simulados» si está en modo simulado) y una lista con NOAA PSL y NOAA NCEI (estado, detalle, latencia, último contacto; «En pausa» tras fallos o HTTP 429), lo publicado por NOAA (último día de PSL, finales y preliminares hasta…), las climatologías (1991-2020 oficial; 1981-2010 y 1993-2022 calculadas; periodo efectivo de 1981-2010) y lo que sirve la caché local (días finales, preliminares, faltantes y por origen). Aviso ámbar global si alguna está degradada. La página se vuelve a dibujar sola cada minuto con el evento `fuentes` de `app.js` (sin recargarla).
- **Administración**: pestañas «Usuarios» (tabla + alta/edición en modal + activar/desactivar), «Catálogo» (conmutadores por fuente y variable, RF-20), «Auditoría» (tabla con filtro por usuario/acción), «Sistema» (KPIs de `GET /api/admin/sistema`, con la tarjeta de CHIRPS: días por origen, estado de cada camino, disyuntor del CHC y sincronización; la tarjeta «Copernicus Marine · caché y sincronización»: modo, estado, último día de análisis, credenciales, conjuntos en caché, climatología, tamaño, descargas, sincronización y errores; y la tarjeta «NOAA OISST v2.1 · caché y sincronización»: modo, estado, fecha de corte, días finales y preliminares, días por origen (PSL o NCEI), servidores, climatologías, tamaño, última sincronización, errores y el comando `python -m backend.herramientas.preparar_noaa` para llenar la caché).
- **Acerca de**: qué es SOME, fases, qué es real (todo: la temperatura superficial de NOAA OISST v2.1 hasta el último día publicado —los últimos ~15 días son preliminares— y el índice Niño 1+2 calculado con ella en la región oficial; la precipitación de CHIRPS v3, con sus dos caminos: servidor del CHC y réplica de dynamical.org; las variables físicas del análisis diario de Copernicus Marine, con su regrillado, la regla del último día de análisis y la climatología GLORYS12 1993-2016) y cuándo se usa el simulador (solo como respaldo señalado de NOAA y Copernicus, o con su modo simulado forzado), el modo actual de NOAA y de Copernicus, créditos de las fuentes con su procedencia, equipo T5 y cliente, versión.

### 8.5 Estados y detalles de calidad

- Siempre: estado de carga (esqueleto o «Consultando NOAA OISST…»), estado vacío, error con reintento.
- La insignia de procedencia aparece en la cabecera, en cada gráfico/mapa (esquina) según la respuesta (`SOME.procedencia(r)`: verde «Datos reales · NOAA OISST», «Datos reales · CHIRPS v3», «Datos reales · Copernicus Marine» o «Datos reales» según la insignia de la respuesta, ámbar «Datos simulados para demostración» o ámbar «Datos mixtos…», con un `title` explicativo que incluye los avisos y el periodo de referencia efectivo; el pie de crédito de cada figura muestra además «Periodo de referencia efectivo» y «Aviso» cuando los hay), en los widgets del panel (marca y etiqueta breve en el pie), en los trabajos de reporte y en los PDF.
- Zoom de los gráficos Plotly (Series, Perfiles, Comparar y widgets del panel; `zoomAcotado` en `consultas.js`): se acerca arrastrando un recuadro y nunca se aleja más allá de la vista completa (ni días sin datos ni profundidades negativas). Mientras el gráfico está acercado, la rueda del ratón acerca o aleja alrededor del cursor y aparece el botón «Ver todo» en un hueco libre (sin tapar título, leyenda, rótulos, anotaciones, la insignia ni, si se puede, los datos); en la vista completa la rueda desplaza la página. El doble clic y «Restablecer los ejes» también vuelven a la vista completa.
- Teclado: Enter envía formularios; Esc cierra modales.
- Nada de `alert()`; usar `toast`/`modal`.
- Textos sin anglicismos innecesarios: «Ingresar», «Salir», «Guardar», «Descargar», «Generar», «Exportar», «Quitar», «Agregar».

---

## 9. Ejecución

`ejecutar.ps1`:
1. Si no existe `.venv`, `python -m venv .venv`; activar; `pip install -r requirements.txt` (silencioso si ya está) y, sin detenerse si falla, `pip install -r requirements-replica.txt` y `pip install -r requirements-copernicus.txt` (opcionales).
2. Si la caché de NOAA OISST está vacía (y no se pidió `SOME_NOAA_SIN_RED=1` ni `SOME_NOAA_MODO=simulado`), `python -m backend.herramientas.preparar_noaa` (unos 20 minutos); si falla o se interrumpe, se sigue: el servidor la completa en segundo plano.
3. `python -m uvicorn backend.app:app --host 127.0.0.1 --port 8050` desde `prototipo-some/`.
4. Imprimir la URL y los usuarios de demo.

`requirements.txt`: `fastapi>=0.116`, `uvicorn>=0.35`, `numpy>=2.2`, `pandas>=2.2`, `xarray>=2025.1`, `scipy>=1.14`, `matplotlib>=3.10`, `reportlab>=4.4`, `jinja2>=3.1`, `pydantic>=2.11`, `Pillow>=11`, más `requests` y `h5py` para el conector real de CHIRPS y `h5netcdf>=1.3` para el de NOAA OISST. `requirements-replica.txt` (opcional): `icechunk>=2.3,<3`, `zarr>=3.4,<4`. `requirements-copernicus.txt`: `copernicusmarine>=2.5,<3`.

El servidor responde en menos de 5 s desde el lanzamiento aunque la sincronización de CHIRPS, Copernicus y NOAA corra: el ciclo de vida solo arranca los hilos (no espera a la red; el de NOAA hace su primer ciclo a los 30 s) y `requests` y `scipy.stats` se importan al primer uso.

Antes de la primera demostración (con internet): `python -m backend.herramientas.preparar_noaa` (unos 20 minutos con la caché vacía, por el límite de ritmo de NOAA PSL; sin otro proceso descargando de PSL a la vez), `python -m backend.herramientas.preparar_chirps` y, tras `copernicusmarine login` (lo ejecuta el usuario), `python -m backend.herramientas.preparar_copernicus`. Sin internet: `SOME_NOAA_SIN_RED=1`, `SOME_CHIRPS_SIN_RED=1` y `SOME_COPERNICUS_SIN_RED=1`. Pruebas: `python -m backend.pruebas.prueba_humo` (acepta NOAA y Copernicus reales o simulados según su modo; lee NOAA solo de su caché salvo `SOME_NOAA_SIN_RED=0`; con `SOME_NOAA_MODO=simulado`, las expectativas del modo simulado), `python -m backend.noaa.prueba_noaa` (con `SOME_NOAA_SIN_RED=1`), `python -m backend.chirps.prueba_chirps`, `python -m backend.chirps.prueba_espejo` y `python -m backend.copernicus.prueba_copernicus`, con y sin `SOME_CHIRPS_SIN_RED=1`.

`README.md`: propósito, requisitos, cómo ejecutar (PowerShell y doble clic), usuarios de demo, **guion de demostración de 10 minutos** (paso a paso por módulo con qué resaltar: el evento cálido real de NOAA OISST, la precipitación real de CHIRPS, NOAA frente a Copernicus real frente a real, el boletín real) y los riesgos de la noche anterior, qué es real y cuándo se usa el simulador de respaldo, cómo preparar las cachés de NOAA, CHIRPS y Copernicus y el modo sin red, estructura del código, cómo reemplazar los conectores simulados por los reales, correspondencia módulo ↔ requerimientos (RF/RNF), limitaciones.
