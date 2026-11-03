# Estudio de bibliotecas de Python para el análisis oceanográfico del INOCAR

Proyecto SOME (Sistema de Observación del Mar Equinoccial) · Equipo T5 · ESPOL, FIEC, Ingeniería de Software I · Octubre de 2026

Este estudio complementa el Concepto de Operaciones (CONOPS) de SOME. Documenta las bibliotecas de Python que los oceanógrafos de la Dirección de Oceanografía y Meteorología Marina del INOCAR usan hoy en sus scripts, evalúa qué papel tendría cada una en el motor de procesamiento del sistema y propone las bibliotecas complementarias que harían falta para cubrir el alcance levantado con el cliente: consultas a las API de NOAA, Copernicus Marine y CHIRPS, visualización de mapas, series y perfiles, cálculo de climatologías y anomalías, exportación de reportes y un lago de datos en el centro de datos del instituto.

El estudio es documentación de la fase de requerimientos. No prescribe una arquitectura definitiva: identifica opciones, su madurez y su encaje con la forma de trabajo actual, para que la decisión técnica se tome con información en la fase de diseño.

## 1. Cómo trabajan hoy los oceanógrafos

Según el levantamiento del 17 de octubre de 2026, el personal de oceanografía no programa. Cuando necesita un análisis, pide un script a una herramienta de inteligencia artificial y lo adapta a mano: cambia las coordenadas del sitio, ajusta las fechas, cambia la variable y lo vuelve a ejecutar. Las consecuencias que el propio personal describe son las siguientes.

- **Trabajo manual y repetitivo.** Cada variable, sitio o periodo nuevo obliga a editar y ejecutar otra vez el script. No hay una interfaz para ingresar coordenadas ni un tablero donde ver las gráficas.
- **Código duplicado y frágil.** Cada script vuelve a escribir la descarga, el recorte, el cálculo y la gráfica, con pequeñas diferencias entre versiones que dificultan comparar resultados.
- **Volúmenes que las computadoras personales no soportan.** Unos 30 años de datos diarios, mensuales y anuales (varios terabytes) se descargan una y otra vez y no caben, ni se procesan con fluidez, en los equipos de escritorio.
- **Resultados poco reproducibles.** Las versiones de las bibliotecas y la configuración de cada computadora (por ejemplo, el idioma del sistema con `locale`) cambian el resultado o hacen fallar el script.
- **Informes manuales.** Las gráficas se pegan a mano en los informes, por ejemplo en los insumos del Comité Nacional para el Estudio Regional del Fenómeno El Niño (CN-ERFEN).

Formatos habituales: NetCDF (`.nc`) y CSV. El personal menciona también archivos en formato ORM, cuya estructura queda por confirmar con un archivo de muestra.

## 2. Bibliotecas que usan hoy los scripts

La tabla resume el listado recibido del cliente y el uso que se observa en los scripts.

| Biblioteca | Para qué la usan hoy | Categoría | Papel propuesto en SOME |
|---|---|---|---|
| `copernicusmarine` | Descargar productos del Copernicus Marine Service (`login`, `subset`, `get`) | Acceso a datos | Se conserva: es la herramienta oficial de ingesta de Copernicus |
| `requests` | Llamadas HTTP a servicios web, por ejemplo de la NOAA | Acceso a datos | Se reemplaza por `httpx` (reintentos, tiempos de espera, asincronía) o por el cliente propio de cada servicio |
| `xarray` | Abrir y recortar archivos NetCDF multidimensionales (tiempo, profundidad, latitud, longitud) | Datos científicos | Se conserva: base del procesamiento de mallas |
| `numpy` | Cálculo numérico sobre arreglos | Datos científicos | Se conserva |
| `pandas` | Tablas, series de tiempo y CSV | Datos científicos | Se conserva: series en estaciones y exportaciones |
| `scipy.ndimage` (`gaussian_filter`) | Suavizar campos antes de graficarlos | Datos científicos | Se conserva solo para suavizados explícitos y documentados; nunca altera el dato guardado |
| `gsw` | Ecuación de estado TEOS-10: densidad, salinidad absoluta, temperatura conservativa | Oceanografía | Se conserva: columna de agua y capa de mezcla |
| `ctd` | Leer perfiles CTD (`.cnv` de Sea-Bird) | Oceanografía | Se conserva para perfiles de cruceros (fase posterior) |
| `pyompa` | Análisis multiparamétrico óptimo de masas de agua | Oceanografía | Se conserva (fase posterior) |
| `matplotlib.pyplot` | Gráficas y mapas | Visualización | Se conserva para figuras estáticas de informes |
| `mpl_toolkits.axes_grid1` | Ubicar barras de color junto a los mapas | Visualización | Se conserva |
| `cartopy.crs`, `cartopy.feature` | Proyecciones, líneas de costa y fronteras | Visualización | Se conserva para mapas de informes |
| `pathlib`, `os`, `sys`, `re` | Rutas, sistema operativo, argumentos, expresiones regulares | Biblioteca estándar | Se conservan |
| `datetime`, `timedelta`, `calendar` | Fechas, intervalos y meses | Biblioteca estándar | Se conservan; el servicio trabaja siempre en UTC |
| `locale` | Nombres de meses en español en las gráficas | Biblioteca estándar | Se evita: depende de cada computadora; los textos en español los pone la aplicación |

Nota: en el listado aparece «caropy.crs»; se entiende como `cartopy.crs`. `netCDF4` no aparece, pero `xarray` lo necesita para abrir archivos `.nc`.

### Diagnóstico

Las bibliotecas actuales son las correctas para el dominio: `xarray`, `numpy`, `pandas`, `gsw`, `matplotlib` y `cartopy` son el estándar de facto en oceanografía con Python. El problema no es la elección de bibliotecas sino la forma de uso: cada cálculo vive en un script distinto, sin versiones fijadas, sin caché de datos y sin una capa que reciba parámetros (área, periodo, variable) desde una interfaz. SOME conserva estas bibliotecas dentro de un motor de procesamiento único y agrega las que faltan para el acceso a las tres fuentes, el manejo de volúmenes grandes, el lago de datos, la visualización interactiva y los reportes.

## 3. Bibliotecas complementarias recomendadas

Cada tabla indica la función que cubre, la biblioteca propuesta, por qué se propone y una alternativa cuando existe. La prioridad MoSCoW se refiere al prototipo (Fase 1) y al lago de datos (Fase 2) del CONOPS.

### 3.1 Acceso a las fuentes de datos

| Función | Biblioteca | Por qué | Prioridad |
|---|---|---|---|
| Copernicus Marine (análisis y pronóstico GLOBAL_ANALYSISFORECAST_PHY_001_024; reanálisis GLORYS12 GLOBAL_MULTIYEAR_PHY_001_030) | `copernicusmarine` (Copernicus Marine Toolbox, versión 2.x) | Cliente oficial; `subset` recorta por variables, área, periodo y profundidad y entrega NetCDF o Zarr; `open_dataset` permite carga perezosa con `xarray` | Must |
| NOAA OISST v2.1 (TSM diaria a 0,25° desde 1981 y su anomalía) y otros conjuntos de NOAA publicados en ERDDAP | `erddapy` | Construye las URL de ERDDAP (griddap y tabledap) y devuelve `pandas` o `xarray`; evita escribir las consultas a mano | Must |
| Acceso OPeNDAP a servidores THREDDS de NOAA PSL y NCEI | `xarray` + `pydap` (o `netCDF4` con soporte OPeNDAP) | Abre un conjunto remoto sin descargarlo completo y recorta solo el área de estudio | Should |
| CHIRPS v3 (precipitación terrestre a 0,05°, 1981 a la fecha) | `climateserv` (cliente de la API ClimateSERV de SERVIR) para series por área; `httpx` + `rioxarray` para los GeoTIFF y NetCDF del servidor de datos del Climate Hazards Center | ClimateSERV sirve bien para pocos puntos; la descarga directa de los archivos pentadales o mensuales es la vía para el lago de datos | Must (prototipo con ClimateSERV); Should (descarga masiva) |
| Lectura de GeoTIFF y rásteres | `rioxarray`, `rasterio` | CHIRPS se distribuye también en GeoTIFF; `rioxarray` lo integra en `xarray` con sistema de referencia | Must |
| HTTP con reintentos y tiempos de espera | `httpx` + `tenacity` | Reemplaza a `requests` en el servicio; reintenta ante fallos transitorios de las API externas | Must |
| Perfiles Argo cerca de Ecuador y Galápagos | `argopy` | Fuente adicional de perfiles de temperatura y salinidad para validar la columna de agua | Could |

### 3.2 Procesamiento, cálculo y volúmenes grandes

| Función | Biblioteca | Por qué | Prioridad |
|---|---|---|---|
| Datos más grandes que la memoria | `dask` (con `distributed`) | Permite procesar décadas de mallas por bloques sin cargarlas completas; se integra de forma nativa con `xarray` | Must (Fase 2) |
| Agrupaciones rápidas (climatología por mes, por año) | `flox` | Acelera `groupby` de `xarray` sobre `dask`; es la vía recomendada para climatologías | Should |
| Almacenamiento por bloques | `zarr`, `numcodecs` | Formato nativo para el lago de datos: lectura parcial, compresión y escritura concurrente | Must (Fase 2) |
| Lectura y escritura NetCDF | `netCDF4`, `h5netcdf` | `h5netcdf` es más rápido con `dask`; NetCDF sigue siendo el formato de intercambio con el personal | Must |
| Metadatos CF y unidades | `cf_xarray`, `pint-xarray` | Identifica latitud, longitud, tiempo y profundidad por sus atributos CF sin depender de los nombres de cada producto; convierte unidades (por ejemplo, mm/pentada a mm/día) | Should |
| Regrillado entre mallas distintas | `xesmf` | Lleva NOAA (0,25°), Copernicus (0,083°) y CHIRPS (0,05°) a una malla común para comparar celda a celda | Should |
| Índices climáticos y anomalías | `xclim` | Funciones probadas para climatologías, anomalías, percentiles y umbrales, con manejo de unidades y calendarios | Should |
| Olas de calor marinas | `xmhw` (o `marineHeatWaves`) | Detecta eventos cálidos sobre series de TSM con el método de Hobday et al., útil para el seguimiento de El Niño costero | Could |
| Análisis de modos (EOF) | `xeofs` | Funciones ortogonales empíricas integradas con `xarray` y `dask` para análisis de variabilidad | Could |
| Verificación de pronósticos | `xskillscore` | Métricas de comparación entre fuentes o entre pronóstico y observación (sesgo, RMSE, correlación) | Could |
| Capa de mezcla y propiedades del agua de mar | `gsw` | Cálculo de densidad potencial a partir de temperatura y salinidad para derivar la profundidad de la capa de mezcla; complementa la variable `mlotst` de Copernicus | Must |
| Interpolación en mallas y series | `pyinterp` o `scipy.interpolate` | Valores en un punto exacto (estación) a partir de la malla; interpolación vertical en perfiles | Should |

### 3.3 Lago de datos, catálogo y servicio

| Función | Biblioteca | Por qué | Prioridad |
|---|---|---|---|
| Almacenamiento de objetos en el centro de datos | `fsspec`, `s3fs` (cliente S3 compatible con MinIO) | `xarray` y `zarr` leen y escriben directamente en MinIO o Ceph a través de `fsspec` | Must (Fase 2) |
| Catálogo de conjuntos de datos | `pystac` (+ `pystac-client`) | Describe cada conjunto (fuente, producto, variable, periodo, resolución, licencia) en el estándar STAC, consultable desde la API | Should |
| Catálogos de acceso para analistas | `intake` + `intake-xarray` | Un archivo de catálogo que abre cualquier conjunto del lago con una línea, sin conocer rutas | Could |
| Servicio interno y validación de solicitudes | `FastAPI`, `pydantic`, `uvicorn` | El backend expone consultas (área, periodo, variable, profundidad) y valida cada solicitud y respuesta | Must |
| Cola de trabajos y tareas programadas | `celery` o `arq` con Redis; `APScheduler` para la ingesta nocturna | Ingesta diaria, reportes grandes y climatologías se ejecutan en segundo plano | Should |
| Base de datos de metadatos y usuarios | `SQLAlchemy` + `psycopg` (PostgreSQL con PostGIS) | Usuarios, roles, departamentos, paneles personales, ejecuciones de ingesta y catálogo | Must |
| Caché de resultados | `redis` | Evita recalcular consultas frecuentes (por ejemplo, el mapa de la fecha más reciente) | Should |

### 3.4 Visualización e informes

| Función | Biblioteca | Por qué | Prioridad |
|---|---|---|---|
| Figuras estáticas para informes | `matplotlib`, `cartopy` | Ya se usan; garantizan figuras idénticas a las que el personal conoce | Must |
| Paletas oceanográficas | `cmocean` | Paletas perceptualmente uniformes y estandarizadas para temperatura (`thermal`), salinidad (`haline`), anomalías (`balance`) y precipitación (`rain`); también legibles para personas con daltonismo | Should |
| Gráficas interactivas en el servidor | `hvplot` + `holoviews` + `geoviews` (con `bokeh`) o `plotly` | Mapas y series interactivas sobre `xarray` con zoom, selección de celda y barra de tiempo; `plotly` exporta a JSON para el frontend | Should |
| Datos vectoriales (costa, provincias, cuencas) | `geopandas`, `shapely` | Recortar CHIRPS por provincia o cuenca y dibujar límites | Should |
| Reportes PDF | `jinja2` + `weasyprint` (HTML a PDF) o `reportlab` | Plantillas en español con figuras, tablas, fuentes y créditos; generación a pedido o programada | Must |
| Exportación a Excel | `openpyxl` (vía `pandas`) | Archivos `.xlsx` con hoja de datos y hoja de fuentes y créditos | Should |

### 3.5 Calidad, reproducibilidad y operación

| Función | Biblioteca o herramienta | Por qué | Prioridad |
|---|---|---|---|
| Entornos reproducibles | `uv` o `conda-lock` con un `environment.yml` fijado | Elimina las diferencias entre computadoras; `cartopy` y `xesmf` se instalan mejor con conda | Must |
| Pruebas automáticas | `pytest`, `hypothesis` | Pruebas contra resultados de referencia de los scripts validados por el personal | Must |
| Estilo y tipos | `ruff`, `mypy` | Código uniforme y verificable | Should |
| Registro y trazabilidad | `structlog` | Cada resultado registra fuente, producto, versión, periodo de referencia y crédito | Should |
| Validación de datos | `pandera` (tablas) y comprobaciones CF con `cf_xarray` | Rechaza ingestas incompletas o fuera de rango antes de escribirlas en el lago | Could |

## 4. Variables del levantamiento y bibliotecas que las cubren

| Variable o producto pedido por los oceanógrafos | Fuente principal | Bibliotecas implicadas |
|---|---|---|
| Temperatura superficial del mar y su anomalía (diaria) | NOAA OISST v2.1 (ERDDAP) y Copernicus `thetao` | `erddapy`, `copernicusmarine`, `xarray`, `xclim` |
| Salinidad superficial y en profundidad | Copernicus `so` (GLORYS12, análisis-pronóstico) | `copernicusmarine`, `xarray`, `gsw` |
| Profundidad de la capa de mezcla | Copernicus `mlotst`; cálculo propio desde perfiles | `copernicusmarine`, `gsw`, `numpy` |
| Corrientes y viento (vectores) | Copernicus `uo`, `vo` y productos de viento | `xarray`, `matplotlib` (`quiver`), `hvplot` |
| Nivel del mar y su anomalía | Copernicus `zos` | `copernicusmarine`, `xarray` |
| Precipitación terrestre | CHIRPS v3 (pentadal, mensual, diaria) | `climateserv`, `rioxarray`, `geopandas`, `xclim` |
| Climatología mensual y «el mismo mes cada año» | Derivado de cualquier fuente | `xarray`, `flox`, `dask`, `xclim` |
| Perfiles verticales (primeros 100 m) | Copernicus en 50 niveles; Argo; CTD | `xarray`, `argopy`, `ctd`, `gsw`, `pyinterp` |
| Validación de los datos de las boyas del INOCAR frente a datos satelitales (Fase 3) | Boyas del INOCAR; NOAA OISST; Copernicus | `pandas`, `xarray`, `pyinterp`, `xskillscore` (sesgo, RMSE, correlación) |

## 5. Buenas prácticas que el motor debe adoptar

- Un solo lugar para cada cálculo: descarga, recorte, climatología y anomalía se escriben una vez y se reutilizan en vistas, exportaciones e informes.
- Versiones fijadas de todas las bibliotecas, para que un cálculo dé el mismo resultado en cada instalación.
- Pruebas contra resultados de referencia, por ejemplo los scripts validados por el personal o los informes del CN-ERFEN.
- Datos originales intactos: ningún suavizado, conversión o agregado modifica el dato guardado en la zona cruda del lago.
- Trazabilidad y crédito: cada resultado indica fuente, producto, versión, periodo de referencia y la cita exigida por el proveedor (Copernicus Marine Service; NOAA/NCEI OISST; Climate Hazards Center, CHIRPS v3, DOI 10.15780/G2JQ0P).
- Datos dentro del instituto: lo que el motor guarda permanece en el centro de datos del INOCAR; las API externas se consultan solo para ingerir.
- Tiempo siempre en UTC en el almacenamiento; la conversión a hora de Ecuador la hace la interfaz.

## 6. Entorno de referencia propuesto (para la fase de diseño)

```text
# núcleo científico
python>=3.11  xarray  numpy  pandas  scipy  netCDF4  h5netcdf  dask  distributed  flox  zarr  numcodecs
# fuentes
copernicusmarine  erddapy  pydap  climateserv  rioxarray  rasterio  httpx  tenacity  argopy
# oceanografía y clima
gsw  ctd  xclim  xesmf  cf_xarray  pint-xarray  xmhw  xeofs  xskillscore  pyinterp
# lago de datos y servicio
fsspec  s3fs  pystac  intake  intake-xarray  fastapi  pydantic  uvicorn  sqlalchemy  psycopg  redis  apscheduler
# visualización e informes
matplotlib  cartopy  cmocean  hvplot  holoviews  geoviews  bokeh  plotly  geopandas  shapely  jinja2  weasyprint  openpyxl
# calidad
pytest  hypothesis  ruff  mypy  structlog  pandera
```

La instalación recomendada es con `conda`/`mamba` (canal conda-forge) y un archivo de bloqueo, porque `cartopy`, `xesmf` y `pyinterp` dependen de bibliotecas nativas (PROJ, GEOS, ESMF).

## 7. Fuentes consultadas

- Listado de bibliotecas y contexto del cliente recibidos el 17 de octubre de 2026 (`bibliotecas-python-scripts-oceanografos.md`).
- Copernicus Marine Toolbox, documentación oficial: https://toolbox-docs.marine.copernicus.eu (versión 2.x; comandos `get` y `subset`).
- Copernicus Marine Data Store: GLOBAL_ANALYSISFORECAST_PHY_001_024 y GLOBAL_MULTIYEAR_PHY_001_030 (malla de 1/12°, 50 niveles).
- NOAA NCEI, OISST v2.1 (0,25°, diario, 1981 a la fecha) y servidores ERDDAP de NOAA CoastWatch y GEO-IDE UAF.
- Climate Hazards Center, CHIRPS v3 (operativo desde enero de 2025; 0,05°, 60°N a 60°S, 1981 a la fecha): https://www.chc.ucsb.edu/data; cliente ClimateSERV: https://github.com/SERVIR/ClimateSERVpy.
- Ecosistema de xarray: https://docs.xarray.dev/en/stable/user-guide/ecosystem.html (xclim, xESMF, xskillscore, xeofs, cf_xarray).
- xmhw, documentación: https://xmhw.readthedocs.io.
- argopy (Maze y Balem, 2020, Journal of Open Source Software): https://argopy.readthedocs.io.
