# Bibliotecas de Python en los scripts de los oceanógrafos del INOCAR

Registro del proyecto OCEANIS · 22 de octubre de 2026 · Proyecto cancelado el 21/10/2026

Este documento lista las bibliotecas de Python que usan hoy los scripts de análisis de los
oceanógrafos del INOCAR, según el listado recibido el 17 de octubre de 2026 con el contexto del
cliente, y resume qué habría hecho con cada una el motor de procesamiento propuesto para OCEANIS.
Está en español porque deriva del documento complementario del curso de ESPOL
(`OCEANIS-bibliotecas-python.docx`, excepción documentada D-37).

## 1. Cómo trabajan hoy

Los oceanógrafos no programan. Piden los scripts a una herramienta de inteligencia artificial y
los adaptan a mano: cambian las coordenadas de cada sitio, ajustan las fechas y los vuelven a
ejecutar. Consecuencias observadas:

- **Código repetido.** Cada script vuelve a escribir la descarga, el recorte por coordenadas, los
  cálculos y los gráficos, con pequeñas diferencias entre versiones.
- **Trabajo manual.** No hay una interfaz para ingresar coordenadas ni un tablero para ver las
  gráficas; los informes se preparan a mano a partir de los resultados.
- **Grandes volúmenes en cada computadora.** Unos 30 años de datos diarios, mensuales y anuales se
  descargan una y otra vez, y las computadoras personales no los manejan con fluidez.
- **Resultados difíciles de reproducir.** Las versiones de las bibliotecas y la configuración de
  cada computadora (por ejemplo, el idioma del sistema) cambian el resultado o hacen fallar el
  script.

Formatos de archivo habituales: `.nc` y `.csv`. El formato `.orm` que mencionan quedó por
confirmar con un archivo de muestra; podría tratarse de perfiles CTD (`.cnv` de Sea-Bird) o del
formato del sistema de boyas de 2007.

## 2. Bibliotecas que usan los scripts actuales

| Biblioteca | Para qué la usan hoy | Tipo |
|---|---|---|
| `copernicusmarine` | Descargar productos del Copernicus Marine Service (Copernicus Marine Toolbox: `login`, `subset`, `get`) | Acceso a datos |
| `requests` | Llamadas HTTP a servicios web, por ejemplo de la NOAA | Acceso a datos |
| `xarray` | Abrir y recortar archivos NetCDF multidimensionales (tiempo, profundidad, latitud, longitud) | Datos científicos |
| `numpy` | Cálculo numérico sobre arreglos | Datos científicos |
| `pandas` | Tablas, series de tiempo y archivos CSV | Datos científicos |
| `scipy.ndimage` (`gaussian_filter`) | Suavizar campos antes de graficarlos | Datos científicos |
| `gsw` | Ecuación de estado del agua de mar TEOS-10: densidad, salinidad absoluta, temperatura conservativa | Oceanografía |
| `ctd` | Leer perfiles de CTD (archivos `.cnv` de Sea-Bird) | Oceanografía |
| `pyompa` (`OMPAProblem`, `ConvertedParamGroup`) | Análisis multiparamétrico óptimo de masas de agua | Oceanografía |
| `matplotlib.pyplot` | Gráficas y mapas | Visualización |
| `mpl_toolkits.axes_grid1` (`make_axes_locatable`) | Ubicar las barras de color junto a los mapas | Visualización |
| `cartopy.crs`, `cartopy.feature` | Proyecciones cartográficas, líneas de costa y fronteras | Visualización |
| `pathlib`, `os`, `sys` | Rutas de archivos, sistema operativo y argumentos | Biblioteca estándar |
| `re` | Expresiones regulares para leer nombres y textos | Biblioteca estándar |
| `datetime`, `timedelta`, `calendar` | Fechas, intervalos y meses del año | Biblioteca estándar |
| `locale` | Nombres de meses en español en las gráficas | Biblioteca estándar |

Nota: en el listado recibido aparece «caropy.crs»; se entiende como `cartopy.crs`.

### Paquetes a instalar

Los módulos de la biblioteca estándar (`pathlib`, `os`, `sys`, `re`, `datetime`, `calendar`,
`locale`) vienen con Python. El resto se instala con `pip` o `conda`; `cartopy` suele instalarse
mejor con `conda`.

```text
copernicusmarine
requests
xarray
netCDF4
numpy
pandas
scipy
gsw
ctd
pyompa
matplotlib
cartopy
```

`netCDF4` no aparece en el listado, pero `xarray` lo necesita para abrir archivos `.nc`.

## 3. Qué habría hecho OCEANIS con cada una

El motor de procesamiento propuesto (decisión D-39, ADR 0007) era un servicio interno en Python
para todo el trabajo con mallas de datos. Conservaba lo que ya funcionaba bien para que los
resultados fueran comparables con los que el personal obtiene hoy.

| Biblioteca actual | Decisión | Motivo |
|---|---|---|
| `copernicusmarine` | Se conserva | Herramienta oficial para la ingesta de Copernicus |
| `xarray`, `numpy`, `pandas` | Se conservan | Base del procesamiento de mallas, series y tablas |
| `scipy.ndimage` | Se conserva | Solo para suavizados explícitos y documentados; nunca altera el dato guardado |
| `gsw`, `ctd`, `pyompa` | Se conservan | Columna de agua, perfiles de CTD y masas de agua (este último en una fase posterior) |
| `matplotlib`, `cartopy`, `make_axes_locatable` | Se conservan | Figuras estáticas de informes y vistas rápidas; las gráficas interactivas viven en la interfaz web |
| `requests` | Se reemplaza | Por `httpx`, con reintentos y tiempos de espera, o por el cliente propio de cada servicio |
| `pathlib`, `os`, `sys`, `re`, `datetime`, `calendar` | Se conservan | Biblioteca estándar; el servicio trabaja siempre en UTC |
| `locale` | Se evita | Depende de la configuración de cada computadora; los textos en español los pone la interfaz web |

### Bibliotecas que el motor habría agregado

| Función | Bibliotecas | Uso en OCEANIS |
|---|---|---|
| Servicio y validación | `FastAPI`, `Pydantic`, `Uvicorn` | Servicio interno que la aplicación web consulta; valida cada solicitud y respuesta |
| Archivo de datos | `zarr`, `numcodecs`, `netCDF4` o `h5netcdf` | Mallas por bloques en Zarr; lectura y escritura de NetCDF |
| Datos más grandes que la memoria | `dask`, `flox` | Procesar décadas de datos sin cargarlos completos; agrupar por mes para la climatología |
| Almacenamiento de objetos | `fsspec`, `s3fs` (o el cliente de MinIO) | Leer y escribir en el lago de datos del centro de datos del INOCAR |
| Catálogo de datos | `pystac` | Describir cada conjunto de datos (fuente, variable, periodo, resolución) |
| Fuentes externas | `copernicusmarine`, `erddapy` o `xarray` con OPeNDAP, `rioxarray`, `rasterio` | Copernicus; NOAA OISST desde ERDDAP u OPeNDAP; CHIRPS en GeoTIFF o NetCDF |
| Mallas distintas | `xesmf` (opcional) | Llevar NOAA y Copernicus a una malla común para comparar celda a celda |
| Base de datos y cola de trabajos | `SQLAlchemy` con `psycopg`; una cola sobre PostgreSQL | Metadatos, ejecuciones de ingesta y trabajos pendientes |
| Figuras de informes | `matplotlib`, `cartopy` | Imágenes estáticas para los informes PDF y las vistas rápidas |
| Calidad del código | `pytest`, `ruff`, `mypy`, `uv` | Pruebas automáticas, estilo y tipos, versiones exactas de cada biblioteca |

### Buenas prácticas que el motor adoptaba

- Un solo lugar para cada cálculo: descarga, recorte, climatología y anomalías se escriben una vez
  y se reutilizan en vistas, exportaciones e informes.
- Versiones fijadas, para que un cálculo dé el mismo resultado en cada instalación.
- Pruebas contra resultados de referencia (por ejemplo, los del entorno R o los scripts validados
  por el personal).
- Datos originales intactos: ningún suavizado, conversión o agregado modifica el dato guardado.
- Trazabilidad: cada resultado indica su fuente, su producto y el crédito correspondiente.
- Datos dentro del instituto: lo que el motor guarda permanece en el centro de datos del INOCAR.

## Fuentes

- `docs/input/client-context-2026-10-17.md`: el contexto del cliente, verbatim.
- `docs/input/client-context-2026-10-17-pitch.md`, sección 8b: el listado de bibliotecas y la
  propuesta del motor.
- `docs/espol/OCEANIS-bibliotecas-python.docx` (y `.pdf`), generado por `build_libraries_doc.py`:
  el documento complementario del curso, del que este markdown es la versión en texto.
- `docs/adr/0007-python-processing-engine-for-gridded-data.md`: la decisión de arquitectura.
