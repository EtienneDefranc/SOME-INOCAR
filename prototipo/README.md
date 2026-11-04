# SOME — Sistema de Observación del Mar Equinoccial · Prototipo demostrativo

Prototipo local (Fase 1) del **SOME** para el INOCAR (Instituto Oceanográfico y Antártico de la Armada), Dirección de Oceanografía y Meteorología Marina. Lo construye el **equipo T5** del curso Ingeniería de Software I (ESPOL, FIEC) a partir del CONOPS v1.3.

> **Las tres fuentes son reales; el simulador solo es un respaldo señalado.** La **temperatura superficial del mar es real**: proviene de **NOAA OISST v2.1** (NOAA/NCEI, DOI 10.25921/RE9P-PT57), servida por **NOAA PSL** (respaldo de los días recientes: archivos diarios de **NOAA NCEI**) y guardada en una caché local; con ella se calcula el **índice Niño 1+2 real** en la región oficial (0–10° S, 90–80° O). La **precipitación es real**: proviene de **CHIRPS v3** (Climate Hazards Center, UC Santa Barbara), obtenida del servidor del CHC o de su réplica pública en **dynamical.org** (el producto del CHC con los valores redondeados para comprimir; cada día guarda de dónde salió y toda salida cita la réplica cuando se usó). Las **variables físicas del océano son reales**: temperatura y salinidad por profundidad, corrientes, nivel del mar y capa de mezcla salen del **análisis diario de Copernicus Marine** (GLOBAL_ANALYSISFORECAST_PHY_001_024; «E.U. Copernicus Marine Service Information»), leído con el Copernicus Marine Toolbox oficial y guardado en una caché local. Si NOAA o Copernicus no responden (o faltan las credenciales de Copernicus) y su caché no tiene el dato, el conector responde con el simulador y **lo dice**: nunca se mezclan días simulados y reales en un mismo resultado de una fuente. Cada gráfico, mapa, exportación y PDF lleva su insignia: verde «Datos reales · NOAA OISST», «Datos reales · Copernicus Marine», «Datos reales: CHIRPS v3 (CHC, UCSB)» o «Datos reales» (varias fuentes reales, p. ej. el boletín ERFEN), ámbar «Datos simulados para demostración» o ámbar «Datos mixtos: reales y simulados» si un respaldo simulado se combina con datos reales. Con las tres fuentes reales, la cabecera dice «Datos reales: NOAA OISST, Copernicus Marine y CHIRPS v3» y `datos_simulados` es `false` en todo el sistema; si una fuente cae a su simulador, la cabecera lo nombra (p. ej., «Demostración: CHIRPS v3 y Copernicus Marine con datos reales; NOAA OISST simulado»).

---

## 1. Propósito

Mostrar al INOCAR, de punta a punta y sin conexión a internet (salvo los mapas de fondo y la sincronización de NOAA OISST, CHIRPS y Copernicus Marine, que también pueden trabajar solo con su caché local), cómo funcionará el sistema: inicio de sesión por roles, panel personal, constructor de consultas sin programar, visor de mapas con barra de tiempo, series con climatología y anomalías, perfiles verticales y cortinas, comparaciones, exportación CSV/PNG/NetCDF, reportes PDF (incluido el boletín quincenal para el CN‑ERFEN), vistas guardadas y compartidas, estado de las fuentes y administración.

La interfaz, la API, el motor de cálculo y los reportes son **reales**, y los tres conectores (**NOAA OISST v2.1**, **Copernicus Marine** y **CHIRPS v3**) ya son reales. Lo que falta reemplazar en el producto final es la persistencia (archivos JSON → PostgreSQL/PostGIS) y las cachés locales (→ lago de datos de la Fase 2).

## 2. Requisitos

- Windows 10/11 (también funciona en Linux o macOS con los mismos comandos).
- **Python 3.13** en el PATH (`python --version`).
- Navegador moderno: Chrome, Edge o Firefox. Para que los selectores de fecha nativos se muestren en **dd/mm/aaaa**, el navegador debe estar en español (la interfaz muestra, además, la fecha legible junto a cada selector).
- Conexión a internet para las teselas de OpenStreetMap, la tipografía Inter, las bibliotecas Leaflet 1.9.4 y Plotly 2.35.2, los iconos Flaticon UIcons 3.0.0 (desde CDN; uso gratuito con crédito, «Uicons by Flaticon», que figura en «Acerca de»), NOAA OISST v2.1 (NOAA PSL, `psl.noaa.gov`, y NOAA NCEI, `www.ncei.noaa.gov`; sin credenciales) y CHIRPS v3, que llega por dos caminos: el servidor del CHC (`data.chc.ucsb.edu`) y la réplica de dynamical.org (S3 de AWS, `us-west-2`, acceso anónimo). Sin conexión, todo lo demás funciona y NOAA, CHIRPS y Copernicus sirven lo que tengan en su caché local (ver «Modo sin red»).
- Dependencias Python (`requirements.txt`): fastapi, uvicorn, numpy, pandas, xarray, scipy, matplotlib, reportlab, jinja2, pydantic, Pillow, más las que usa el conector de CHIRPS con el servidor del CHC (requests y h5py para leer por rangos HTTP los GeoTIFF diarios y los NetCDF4 mensuales) y **h5netcdf** (xarray la usa para abrir los NetCDF4 de NOAA PSL y NOAA NCEI). **No** usa cartopy, rasterio, netCDF4, pydap, weasyprint ni passlib.
- **Opcional** (`requirements-replica.txt`): **icechunk** y **zarr**, para leer la réplica de dynamical.org. `ejecutar.bat` y `ejecutar.ps1` intentan instalarlas y siguen aunque falle; sin ellas el SOME usa solo el servidor del CHC y la caché local, y «Estado de fuentes» muestra la réplica como «No instalada en este equipo».
- **Copernicus Marine** (`requirements-copernicus.txt`): el **Copernicus Marine Toolbox** (`copernicusmarine` 2.5 o posterior, menor que 3). Los lanzadores lo instalan igual que la réplica (si falla, avisan y siguen). Leer datos exige una **cuenta gratuita** de Copernicus Marine: el usuario guarda sus credenciales **él mismo**, una vez, con `copernicusmarine login` en una consola de este equipo (quedan en `C:\Users\<usuario>\.copernicusmarine\`). El SOME **nunca** pide, muestra, escribe ni guarda el usuario o la contraseña. Sin el paquete, sin credenciales o sin red, se sirve la caché local `datos/copernicus/` y lo que no esté en ella se responde con el simulador, señalado como datos simulados. Necesita internet hacia el Copernicus Marine Data Store (ARCO, `s3.waw3-1.cloudferro.com`, y la autenticación de Copernicus Marine).

## 3. Cómo ejecutar

**Doble clic (Windows):** `ejecutar.bat`. Crea `.venv` si no existe, instala las dependencias y arranca el servidor. Si la caché de NOAA OISST (`datos/noaa/`) está vacía, antes de arrancar la llena con `preparar_noaa` (unos 20 minutos, §3.3); si se interrumpe o falla, el servidor arranca igual y la completa en segundo plano.

**PowerShell:**

```powershell
cd prototipo-some
.\ejecutar.ps1
```

**A mano (cualquier sistema):**

```bash
python -m venv .venv
.venv\Scripts\activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8050
```

Abra **http://127.0.0.1:8050/**. La documentación interactiva de la API está en `http://127.0.0.1:8050/api/docs`.

Al primer arranque se crea la carpeta `datos/` con los usuarios, los paneles por defecto, tres vistas de ejemplo, unas 20 entradas de auditoría y **dos reportes ya «listos»** (un boletín ERFEN de hace 15 días y un informe de variable), que se generan de verdad en segundo plano durante el primer minuto. Para empezar de cero, detenga el servidor y borre todo el contenido de `datos/` **salvo las carpetas `datos/noaa/`, `datos/chirps/` y `datos/copernicus/`**: ahí están las cachés de datos reales (SST diaria, climatologías e índice Niño 1+2 de NOAA OISST; precipitación y climatología 1981‑2022 de CHIRPS; análisis diario y climatología GLORYS12 de Copernicus), que se preparan con internet (§3.1, §3.2 y §3.3). Para regenerar solo los reportes de ejemplo basta con borrar `datos/trabajos.json` y `datos/reportes/`. Si borra `datos/noaa/`, `datos/chirps/` o `datos/copernicus/`, vuelva a ejecutar `python -m backend.herramientas.preparar_noaa`, `python -m backend.herramientas.preparar_chirps` o `python -m backend.herramientas.preparar_copernicus` con conexión antes de la demostración (la de NOAA tarda unos 20 minutos).

### 3.1 Preparar la caché de CHIRPS v3 (datos reales)

La precipitación se guarda, ya llevada a la malla de 0.25° del prototipo, en `datos/chirps/` (unos 2.7 MB: `diario/AAAA-MM.npz` con el valor, el tipo —final o preliminar— y el **origen** de cada día; `mensual/AAAA.npz` con la climatología y `mensual/origen.json`; `estado.json` con las verificaciones). **Antes de la primera demostración** (con internet) prepare la caché completa:

```bash
python -m backend.herramientas.preparar_chirps                    # días desde el 01/10/2023 + climatología mensual
python -m backend.herramientas.preparar_chirps --solo-diario      # solo los días que faltan
python -m backend.herramientas.preparar_chirps --climatologia     # solo la climatología mensual 1981-2022
python -m backend.herramientas.preparar_chirps --desde 2026-09-01 --hasta 2026-09-30
python -m backend.herramientas.preparar_chirps --fuente espejo    # forzar un camino: auto (por defecto), chc o espejo
```

Con la réplica disponible, el llenado completo tarda segundos (966 días finales en 7.7 s) y la climatología 1981‑2022, alrededor de 1.5 minutos (42 años). El código de salida es 1 si el camino elegido no responde.

**Dos caminos a CHIRPS v3.** CHIRPS v3 se obtiene por uno de estos caminos; la réplica redistribuye el producto del CHC con los valores redondeados para comprimir:

| Camino | Qué entrega | Cómo se usa |
|---|---|---|
| **Servidor del CHC** (`data.chc.ucsb.edu`) | Diarios finales a 0.05° (`daily/final/rnl/AAAA/`, disgregación con ERA5), preliminares a 0.05° (`daily/prelim/sat/AAAA/`, disgregación con IMERG) y NetCDF mensual global. El SOME **no** usa el final ya agregado por el CHC a 0.25° (`daily/final/rnl/p25/`): su máscara costera es otra. | GeoTIFF leídos por rangos HTTP (solo el recorte del área) y agregados 5×5 igual que la réplica. **Acceso cortés**: como máximo 1 petición por segundo **entre todos los procesos del SOME del equipo** (servidor, script de preparación, pruebas; el turno se comparte con un archivo de bloqueo en `datos/chirps/.bloqueos/`), un archivo a la vez (también entre procesos y también para la climatología), User‑Agent que identifica al proyecto y **disyuntor** (tras 3 fallos seguidos, o un HTTP 429, no se le pide nada durante 15 minutos; luego se permite un solo intento de prueba, también desde otro proceso). |
| **Réplica de dynamical.org** (Zarr/Icechunk en S3, CC BY 4.0) | Final («rnl», desde 01/01/1981) y preliminar a 0.05° (con unos días de retraso respecto del CHC), valores redondeados a 8 bits de mantisa para comprimir (`keep_mantissa_bits=8` en el código público de dynamical.org; error ≤ 0.2 %). | Se usa para todo lo que cubre (es más rápida y no carga al CHC) y cuando el CHC no responde. Es un tercero **sin garantía de servicio**; requiere las dependencias opcionales. |

La **regla de llenado** (modo `auto`) toma de la réplica todo lo que cubre y del CHC lo más reciente; si la réplica falla, los días publicados por el CHC salen del CHC. Un día solo se reescribe si mejora (ausente → preliminar → final). Los **preliminares** se señalan (`dias_preliminares` en cada respuesta, aviso en métodos y reportes) y se **reemplazan por los finales** en cuanto se publican. La **climatología mensual** (1981‑2022) se calcula primero desde la réplica (media mensual del diario final a 0.05°) y, si no está disponible, desde el NetCDF mensual del CHC; `mensual/origen.json` guarda el origen de cada año. Sirve para las anomalías y los acumulados «normales» con los periodos de referencia 1991‑2020, 1981‑2010 y 1993‑2022.

**Sincronización y estado.** Con el servidor en marcha, un **hilo de sincronización** (no retrasa el arranque) revisa cada 6 horas los dos caminos (cada 30 minutos si alguno falló), descarga lo nuevo y guarda cada verificación. De ahí salen el estado y el historial real de 14 días de «Estado de fuentes» (RF‑21): **Operativa** si responde el CHC, **Degradada** si solo responde la réplica (o el CHC va lento) y **Sin conexión** si no responde ninguno; la tarjeta de CHIRPS muestra además el estado de cada camino y hasta qué fecha llega cada producto en cada uno. Una consulta a la que le falten días los pide en el momento: primero a la réplica (se leen los fragmentos completos de 365 días que toca la consulta, que cuestan lo mismo que una parte) y luego al CHC (como máximo 45 días y 90 segundos), nunca a un CHC marcado caído ni a una réplica que falló hace menos de 5 minutos. La **fecha de corte** es el último día en caché o, si es posterior, lo último que publica la réplica cuando responde; los días más recientes que solo tiene el CHC los trae la sincronización (bajarlos dentro de una consulta la haría tardar unos 4 s por día). Las respuestas se guardan en una caché en memoria que se invalida sola en cuanto cambia la caché de CHIRPS (un día nuevo, un preliminar que pasa a final, un año de climatología); una respuesta con días faltantes no se guarda. Si a una consulta de anomalía le faltan más de 3 años de climatología, la consulta responde que se está preparando (en segundo plano) en lugar de quedarse esperando.

**Varios procesos a la vez.** El servidor, `preparar_chirps` y las pruebas pueden convivir: cada mes de la caché diaria, el archivo `estado.json`, el registro de origen de la climatología y cada año de climatología se escriben bajo un bloqueo entre procesos que relee el archivo antes de modificarlo, así que nadie pierde lo que escribió otro. Un archivo de la caché dañado (por ejemplo, tras un corte de luz) se aparta como `*.danado` y se vuelve a obtener.

**Variables de entorno de CHIRPS:**

| Variable | Efecto |
|---|---|
| `SOME_CHIRPS_SIN_RED=1` | Modo sin red: no se contacta ni el CHC ni la réplica; se sirve solo la caché local. |
| `SOME_CHIRPS_SIN_SINCRONIZACION=1` | No arranca el hilo de sincronización (pruebas y desarrollo); las consultas siguen llenando bajo demanda lo que falte. |
| `SOME_RUTA_CHIRPS=<carpeta>` | Usa esa carpeta como caché de CHIRPS (diario, mensual, `estado.json` y bloqueos) en lugar de `datos/chirps/`, para servidores o pruebas aislados (ver «Cachés compartidas y aislamiento»). En otra carpeta, el turno de cortesía con el CHC ya no se comparte con el servidor principal: úsela con `SOME_CHIRPS_SIN_RED=1`. |
| `SOME_CHIRPS_CONTACTO=<correo o URL>` | Contacto que va en el User‑Agent de las peticiones al CHC, por cortesía con el CHC (ningún término publicado lo exige; para el INOCAR debe ser un contacto institucional). El código no tiene valor por defecto (el User‑Agent nunca lleva un correo que no se haya configurado); en esta copia pública, los lanzadores no lo definen: hay que definirlo antes de ejecutar. Para otra instalación, defina el contacto institucional de su responsable. |

**Modo sin red.** Con `SOME_CHIRPS_SIN_RED=1` el conector no toca internet: sirve solo la caché local, su estado es «Sin conexión» con un mensaje claro y en «Estado de fuentes» los dos caminos figuran como «No se consulta (modo sin red)», con las últimas fechas conocidas. Úselo para demostrar sin conexión o para la prueba de humo:

```powershell
$env:SOME_CHIRPS_SIN_RED = "1"; python -m uvicorn backend.app:app --host 127.0.0.1 --port 8050
```

**Pruebas** (todas pasan con y sin `SOME_CHIRPS_SIN_RED=1`):

```bash
python -m backend.pruebas.prueba_humo        # recorre toda la API, genera cuatro reportes (uno personalizado) y verifica la procedencia
python -m backend.chirps.prueba_chirps       # paquete CHIRPS: malla, caché, cortesía, disyuntor, conmutación, estado
python -m backend.chirps.prueba_espejo       # réplica dynamical.org (con red: cobertura real y comparación con el CHC)
python -m backend.copernicus.prueba_copernicus   # paquete Copernicus (sin red, con dobles; bloque real si hay credenciales)
SOME_NOAA_SIN_RED=1 python -m backend.noaa.prueba_noaa   # paquete NOAA (sin red, con dobles; sin el bloque real)
SOME_CHIRPS_SIN_RED=1 python -m backend.pruebas.prueba_humo     # sin internet (requiere las cachés preparadas)
SOME_NOAA_MODO=simulado python -m backend.pruebas.prueba_humo   # expectativas del modo simulado de NOAA
```

La prueba de humo acepta NOAA OISST y Copernicus Marine **reales o simulados** según su modo (lo lee de `GET /api/fuentes/estado`, bloques `noaa` y `copernicus`). Con las cachés preparadas exige que todo sea «real»: SST, climatología (1981‑2010 con periodo efectivo «1982-2010 en enero–agosto»), «mismo mes», exportaciones e índice Niño 1+2 semanal de NOAA con la insignia «Datos reales · NOAA OISST» y el DOI 10.25921/RE9P-PT57; la fecha de corte del sistema igual a la de NOAA (y los nombres de los archivos exportados con esa fecha); NOAA frente a Copernicus «real» con correlación ≥ 0.9; el boletín ERFEN «real»; `datos_simulados=false` en el catálogo, la salud y el sistema. El caso «mixto» (SST simulada frente a precipitación real) lo comprueba en un proceso aparte con `SOME_NOAA_MODO=simulado`, y con `SOME_NOAA_MODO=simulado` toda la prueba vuelve a las expectativas anteriores (fecha de corte simulada 27/10/2026, NOAA «simulado», comparación y boletín «mixtos»). De Copernicus, con credenciales y acceso exige que sus consultas sean «real» (con el DOI 10.48670/moi-00016 y el periodo de referencia efectivo en la serie y su CSV); sin acceso ni caché, que sean «simulado»; con solo caché, que todo lo que no sea «real» traiga avisos. Con credenciales, prepare antes la caché (§3.2): una consulta que deba descargar mucho responde 503 «sigue descargándose en segundo plano». Resultado del 04/11/2026, con el reporte personalizado y sus plantillas guardadas (RF-09.3): 304/304 verificaciones correctas en 26 s. Resultado del 30/10/2026 con copias de las tres cachés: 281/281 verificaciones correctas en 31 s (todo real); 274/274 con `SOME_NOAA_MODO=simulado` y con la caché de NOAA vacía; 277/277 con `SOME_COPERNICUS_MODO=simulado`.

**Cachés compartidas y aislamiento.** La prueba de humo guarda sus JSON (usuarios, vistas, trabajos y reportes) en una carpeta temporal (`SOME_RUTA_DATOS`), pero usa a propósito las cachés reales `datos/noaa/`, `datos/chirps/` y `datos/copernicus/`, las mismas del servidor: `SOME_RUTA_DATOS` no las redirige. No arranca ningún hilo de sincronización y lee NOAA **solo de su caché** (fija `SOME_NOAA_SIN_RED=1` si no está definida: NOAA PSL limita el ritmo por IP y ese límite lo comparte con el servidor; `SOME_NOAA_SIN_RED=0` la prueba con red y acepta «Degradada»). Para un servidor o una prueba aislados, y sobre todo para simular una caída de red, un proxy roto o la falta de credenciales, apunte `SOME_RUTA_NOAA`, `SOME_RUTA_COPERNICUS` y `SOME_RUTA_CHIRPS` a una **copia** de esas carpetas. Los archivos `estado.json` se comparten entre procesos: una caída registrada por un proceso de prueba dejaría al servidor real «Sin conexión» (Copernicus, sirviendo con datos simulados lo que no esté en la caché, de 5 a 30 minutos; NOAA, con sus servidores en pausa 10 minutos).

```powershell
New-Item -ItemType Directory -Force C:\temp\copia-some | Out-Null
Copy-Item -Recurse datos\noaa, datos\copernicus, datos\chirps C:\temp\copia-some
$env:SOME_RUTA_NOAA = "C:\temp\copia-some\noaa"; $env:SOME_NOAA_SIN_RED = "1"
$env:SOME_RUTA_COPERNICUS = "C:\temp\copia-some\copernicus"; $env:SOME_RUTA_CHIRPS = "C:\temp\copia-some\chirps"
$env:SOME_CHIRPS_SIN_RED = "1"; python -m uvicorn backend.app:app --host 127.0.0.1 --port 8051
```

En Windows, si la consola no muestra bien las tildes, anteponga `PYTHONIOENCODING=utf-8` (o `$env:PYTHONIOENCODING = "utf-8"`).

**Autodiagnóstico del motor** (rendimiento y verosimilitud de los datos; incluye una serie y un mapa de CHIRPS reales y, con NOAA OISST y Copernicus Marine reales, comprueba sus valores con rangos físicos plausibles del Pacífico ecuatorial oriental —SST de 19 a 31 °C— en lugar de los del simulador):

```bash
python -m backend.motor
```

### 3.2 Copernicus Marine con datos reales

**Qué se lee.** El análisis diario de **GLOBAL_ANALYSISFORECAST_PHY_001_024** (1/12°, 50 niveles; DOI [10.48670/moi-00016](https://doi.org/10.48670/moi-00016)): `thetao` (°C), `so` (PSU), `uo` y `vo` (m/s; el vector «corrientes» y su rapidez), `zos` y `mlotst` (m). Se usa `copernicusmarine.open_dataset` (lectura perezosa sobre ARCO) recortando el área, los días y **solo los niveles necesarios**. Cada valor se lleva a la malla SOME de 0.25° como la **media de las 3 × 3 celdas nativas** cuyo centro cae en la celda SOME (con al menos 5 de 9 válidas) y a cada nivel SOME (0, 5, 10, 20, 30, 50, 75 y 100 m) por **interpolación lineal entre los dos niveles del modelo** que lo rodean (0 m = nivel de 0.494 m). Unidades CF: `degC`, `1e-3`, `m s-1`, `m`.

**Solo análisis, nunca pronóstico.** Mercator publica cada jueves el análisis semanal del boletín del miércoles `W`: el último día servible es `W − 2` (hoy, 30/10/2026: boletín del 28/10 → corte **26/10/2026**). Los días `W−8 … W−2` son análisis «NRT» (provisionales: Mercator los reemplaza la semana siguiente; se señalan) y los anteriores, «mejor análisis». El pronóstico nunca se presenta como dato observado.

**Climatología y anomalías.** Se usa la climatología mensual oficial de **GLORYS12** (`cmems_mod_glo_phy_my_0.083deg-climatology_P1M-m`, GLOBAL_MULTIYEAR_PHY_001_030, DOI [10.48670/moi-00021](https://doi.org/10.48670/moi-00021)), llevada a la malla SOME y guardada en caché. Su periodo real se lee de `climatology_bounds`: **1993‑2016**. Si se pide 1991‑2020 (u otro de los tres periodos del catálogo), la respuesta trae `periodo_referencia` (el pedido) y `periodo_referencia_efectivo` («1993-2016»), y el método, el subtítulo, el resumen, el CSV (`# Periodo de referencia efectivo: …`), el NetCDF y el PDF lo dicen, junto con la diferencia de sistema: la climatología es de un **reanálisis** y los datos diarios, del **análisis operativo**; parte de la anomalía puede deberse a esa diferencia (en `zos`, además, a referencias distintas del nivel medio).

**Preparación (la noche anterior a la demostración, con internet):**

```powershell
copernicusmarine login                                              # lo ejecuta el usuario, una sola vez (cuenta gratuita)
python -m backend.herramientas.preparar_copernicus --verificar     # credenciales, metadatos y latencia
python -m backend.herramientas.preparar_copernicus --estimar       # fragmentos y MiB por escenario, sin descargar
python -m backend.herramientas.preparar_copernicus                 # verificación + escenarios de la demostración
python -m backend.herramientas.preparar_copernicus --escenarios perfil,comparacion
python -m backend.herramientas.preparar_copernicus --climatologia-completa
```

Los escenarios llenan lo que usa la demostración: las 6 variables de superficie (0 m) de los 30 días que recorre la barra de tiempo del mapa en toda el área (así «Reproducir» no descarga fotograma a fotograma con ninguna variable), perfil y cortina en 85° O / 2° S (90 días), comparación con NOAA en Niño 1+2 (último año), capa de mezcla del golfo de Guayaquil (widget del panel), las consultas de la prueba de humo y la climatología de superficie. La herramienta nunca pregunta credenciales: sin ellas termina con código 2 y el mensaje «Sin credenciales de Copernicus Marine: ejecute «copernicusmarine login»…».

Medido el 30/10/2026 con la caché vacía y el escenario «superficie» anterior (thetao, so, zos y mlotst solo del último día; corrientes de 30 días): unos 270 fragmentos ARCO en 3 a 4 minutos y del orden de 400 MB recibidos por la red (medidos en todo el equipo); en disco, la caché ocupa unos 10 MB. El escenario actual, con las 6 variables de 30 días, suma con la caché vacía unos 230 fragmentos más según la estimación; con la caché ya preparada solo descarga lo que falta, y `--estimar` lo dice antes de descargar. Las lecturas usan `chunk_size_limit=1` para que el *toolbox* descargue solo los fragmentos de los niveles pedidos (con su valor por defecto agrupa hasta 10 niveles y, al pedir uno, bajaba los 10: un día de `thetao` a 0 m en toda el área costaba 46 MB en lugar de unos 5 MB). Las mismas consultas servidas desde la caché tardan milisegundos.

**Caché local** `datos/copernicus/`: `diario/<variable>/<nivel>/AAAA-MM.npz` (ya en la malla de 0.25°, con el tipo de cada celda y día —NRT o mejor análisis—, el boletín y el momento de obtención), `climatologia/<variable>_<nivel>.npz` y `estado.json` (verificaciones, historial y errores). Una consulta lee la caché (un mapa de un día, en milisegundos) y, si falta algo y hay acceso, lo descarga en el momento con una espera máxima de 40 s (si se agota, responde 503 «sigue descargándose en segundo plano; vuelva a consultar en unos segundos» y la descarga continúa); un pedido demasiado grande responde 422 y sugiere `preparar_copernicus`.

**Sincronización y estado (RF‑21).** Un hilo en segundo plano (no retrasa el arranque; primer ciclo a los 20 s, luego cada 6 horas, o cada 30 minutos si algo falló o faltan credenciales) verifica las credenciales con `copernicusmarine.login(check_credentials_valid=True)` (nunca pregunta), hace una lectura de prueba pequeña y mide su latencia, consulta el último día de análisis, mantiene la superficie del último día de análisis y la capa de mezcla del golfo de 7 días, y pasa los días NRT a mejor análisis. «Estado de fuentes» muestra para Copernicus la etiqueta «Conexión real», el estado (**Operativa**, **Degradada** o **Sin conexión**), el último día de análisis, la latencia, las credenciales (solo su estado), la climatología en caché, lo que sirve la caché y el historial de 14 días; «Administración › Sistema» tiene la tarjeta «Copernicus Marine · caché y sincronización».

**Respaldo simulado honesto.** Sin el paquete, sin credenciales (o no válidas), sin red o con el servicio caído, se sirve lo que haya en la caché; si a una consulta no le queda ningún dato real, responde el simulador con `datos_reales=0`, procedencia «simulado» y un aviso que dice por qué («Copernicus Marine no está disponible: *motivo*. Esta consulta usa datos SIMULADOS para demostración.»). Un mapa, una serie, un perfil o una cortina de Copernicus nunca mezclan días simulados con reales: si solo falta una parte del área o del periodo, esas celdas quedan sin dato y la respuesta lo avisa; en «Mismo mes cada año», los años que no están en la caché y no se pudieron obtener se omiten, con un aviso. Un resultado que combina dos procedencias (una comparación entre fuentes o variables, o datos reales con la climatología simulada de respaldo) sale «mixto» y lo dice. Una respuesta con avisos no se guarda en la caché de consultas (en cuanto vuelva la fuente, la misma consulta trae los datos reales).

**Variables de entorno de Copernicus Marine:**

| Variable | Efecto |
|---|---|
| `SOME_COPERNICUS_MODO` | `auto` (por defecto: real si hay caché o acceso; si no, simulado), `simulado` (el simulador anterior, sin red) o `real` (estricto: sin respaldo simulado; error 422 si falta el dato). |
| `SOME_COPERNICUS_SIN_RED=1` | No toca la red: solo la caché local; estado «Sin conexión»; sin sincronización. |
| `SOME_COPERNICUS_SIN_SINCRONIZACION=1` | No arranca el hilo de sincronización (la prueba de humo lo fija); las consultas siguen llenando bajo demanda. |
| `SOME_COPERNICUS_ESPERA_S` | Espera máxima de una descarga dentro de una consulta (por defecto 40 s). |
| `SOME_COPERNICUS_DIAS_SINCRONIZACION` | Días de superficie que mantiene la sincronización (por defecto 1; 0 = solo verificar). |
| `SOME_RUTA_COPERNICUS=<carpeta>` | Usa esa carpeta como caché de Copernicus (diario, climatología, `estado.json` y bloqueos) en lugar de `datos/copernicus/`. Obligatoria, apuntando a una copia, para cualquier servidor o prueba que simule fallas de red o falta de credenciales (ver «Cachés compartidas y aislamiento», §3.1). |

**Crédito y licencia.** Originador: E.U. Copernicus Marine Service; productos GLOBAL_ANALYSISFORECAST_PHY_001_024 (DOI 10.48670/moi-00016) y GLORYS12, GLOBAL_MULTIYEAR_PHY_001_030 (DOI 10.48670/moi-00021). Licencia: gratuita, mundial y perpetua; exige el crédito con los DOI, declarar las modificaciones y no sugerir aval de la UE. Desde el 01/11/2026 el crédito usa la forma que la licencia de uso del Copernicus Marine Service recomienda para obras derivadas: «Generated using E.U. Copernicus Marine Service Information; análisis diario, https://doi.org/10.48670/moi-00016.», más «Climatología GLORYS12, https://doi.org/10.48670/moi-00021.» si se usaron anomalías. Cada figura, CSV, NetCDF y PDF lleva el crédito de los productos que realmente usó. Ver [CONDICIONES-USO-FUENTES.md](../CONDICIONES-USO-FUENTES.md), §3.

### 3.3 NOAA OISST v2.1 con datos reales

Diseño detallado y mediciones en `docs/DISENO-NOAA.md`.

**Qué se lee.** El producto **NOAA 0.25° Daily OISST v2.1** (NOAA/NCEI, DOI [10.25921/RE9P-PT57](https://doi.org/10.25921/RE9P-PT57)): temperatura superficial del mar diaria a 0.25°, desde septiembre de 1981, sin credenciales. Vía principal: **NOAA PSL** (servidor THREDDS, servicio NCSS: subconjunto por área y fechas de los archivos anuales `sst.day.mean.AAAA.nc`, NetCDF4 que xarray abre con h5netcdf). Respaldo de los días recientes: los archivos diarios globales de **NOAA NCEI** (unos 1.5 MB por día), cuyo listado mensual dice además qué días son finales y cuáles preliminares. Comprobado el 30/10/2026: PSL y NCEI coinciden valor a valor. Una sola ventana nativa de 65 × 70 celdas cubre el área de estudio y la **región Niño 1+2 oficial** (0–10° S, 90–80° O, que se sale del área por el sur): una misma petición sirve el mapa y el índice.

**Malla.** Los centros nativos de OISST caen en x.125 y x.375; cada centro SOME (múltiplo de 0.25°) es la esquina común de 4 celdas nativas y su valor es la media de las válidas, con un mínimo de 2 de 4 (coincide con la máscara de Copernicus en el 99.3 % de las celdas; Guayas conserva 12 celdas de mar y El Oro 4). Una verificación independiente con archivos de NCEI dio diferencias máximas de 2.5e-6 °C.

**Finales y preliminares.** NOAA publica el día D el D+1 como **preliminar** y lo reemplaza por el **final** unas dos semanas después: los **últimos ~15 días son preliminares** y se señalan (`dias_preliminares`, `periodo_preliminar` y una nota en el método, que también llevan el CSV y el PDF); la sincronización los renueva. Un día de PSL cuenta como final si NCEI publicó su final al menos 12 horas antes de la descarga.

**Fecha de corte del sistema.** Es la de NOAA OISST: el **último día de su caché** (29/10/2026 con la caché preparada el 30/10/2026). No se adelanta al día que PSL acaba de publicar hasta que la sincronización lo guarda, para que la fecha por defecto siempre tenga dato. El catálogo, `/api/salud`, el panel (saludo e índice), los reportes, las exportaciones y las vistas de ejemplo la toman de `catalogo.fecha_corte_sistema()`; en modo simulado vuelve a ser la simulada, 27/10/2026. CHIRPS y Copernicus conservan su propia fecha de corte.

**Climatologías y periodo efectivo.** Mensuales, en la malla SOME: **1991‑2020 oficial** de PSL (`sst.mon.ltm.1991-2020.nc`); **1981‑2010** y **1993‑2022 calculadas** con las medias mensuales de PSL (`sst.mon.mean.nc`; la 1991‑2020 calculada así coincide con la oficial). OISST empieza en septiembre de 1981: en 1981‑2010, de enero a agosto se usan los años 1982‑2010, y la respuesta lo declara (`periodo_referencia_efectivo = "1982-2010 en enero–agosto"`, también en el método, el CSV, el NetCDF y el PDF), como hace Copernicus con su 1993‑2016.

**Índice Niño 1+2 real.** Media de área (pesos según el coseno de la latitud) de la anomalía de la SST en la región oficial (0–10° S, 90–80° O, celdas nativas de 0.25°) respecto de la climatología **diaria** 1991‑2020 de PSL; semanas de domingo a sábado etiquetadas con el miércoles central, como las de NOAA CPC (una semana con menos de 4 días con dato queda sin valor; la semana en curso se marca incompleta y preliminar). Fase y semáforo: El Niño (fase cálida) desde +0.5 °C, La Niña (fase fría) desde −0.5 °C, neutra entre ambos; el sistema **no** calcula categorías de intensidad (débil, moderado, fuerte…), que corresponden a las instancias oficiales. Frente al índice semanal de NOAA CPC (`wksst9120.for`, base 1991‑2020), en 10 semanas completas: diferencia máxima 0.13 °C, media 0.05 °C. **Valores de control del 29/10/2026** (día preliminar): SST media de la región oficial 26.51 °C y anomalía diaria +5.19 °C; índice de la semana del 18 al 24/10/2026 (miércoles 21/10) +5.17 °C (CPC: +5.2 °C); semana en curso, del 25 al 29/10/2026 (miércoles 28/10; 5 días preliminares), +5.34 °C. El índice está en la fase cálida desde la semana del 01/02/2026. Es decir, un **evento cálido real muy intenso** frente a las costas del Ecuador y el Perú: el recorte Niño 1+2 del SOME (0–6° S) está 5.67 °C por encima de lo normal para octubre (1991‑2020).

**Preparación (la noche anterior a la demostración, con internet):**

```powershell
python -m backend.herramientas.preparar_noaa --verificar      # PSL y NCEI: último día publicado, finales, latencia
python -m backend.herramientas.preparar_noaa --estimar        # peticiones, MiB y minutos, sin descargar
python -m backend.herramientas.preparar_noaa                  # verificación + todo lo que falte
python -m backend.herramientas.preparar_noaa --solo-diario    # solo la SST diaria
python -m backend.herramientas.preparar_noaa --climatologia   # solo las climatologías mensuales
python -m backend.herramientas.preparar_noaa --indice         # solo la climatología diaria de Niño 1+2 y el índice
```

Con la caché vacía tarda **unos 20 minutos** (medido el 30/10/2026: 13 min 32 s la SST diaria del 01/10/2023 al 29/10/2026 —13 peticiones, 1 125 días: 1 110 finales y 15 preliminares— y las climatologías mensuales —2 peticiones—, más 6 min 42 s la climatología diaria de Niño 1+2 —4 peticiones, con dos esperas por HTTP 429—). Descarga unos 15.3 MB y la caché ocupa unos 18 MB. Solo pide lo que falta: se puede interrumpir y reanudar, y con la caché al día solo trae los días nuevos y renueva los preliminares (1 a 2 minutos). Termina mostrando el índice Niño 1+2 de las últimas semanas junto al de NOAA CPC. Códigos de salida: 0 bien; 1 hubo errores; 2 modo sin red o simulado (no descargó nada). Ejecútela sin otro proceso descargando de PSL (por ejemplo, con el servidor detenido o recién arrancado y ya sincronizado).

**Límite de ritmo de NOAA PSL.** PSL limita el ritmo **por IP** con HTTP 429 (medido: con 20 s entre peticiones, alrededor de 1 de cada 8 recibe 429; tras unas 20 peticiones en 15 minutos, tres 429 seguidos) y su proxy corta a los ~60 s (un año entero de una vez → HTTP 502). Por eso el SOME pide tramos de hasta 92 días de un mismo archivo anual (~28 s cada uno), hace una pausa de 20 s tras cada petición, no pide más de 1 petición por segundo a cada servidor entre todos sus procesos (turno compartido en `datos/noaa/.bloqueos/`) y se identifica con el User‑Agent `SOME-prototipo/0.1 (demostracion para el INOCAR)` (sin correo). Ante un 429, la sincronización y `preparar_noaa` esperan 60, 120, 240 y 300 s y reintentan; **una consulta no espera**: sirve la caché y avisa. Tras 3 fallos seguidos (o el primero de una consulta) el servidor queda en pausa 10 minutos, también para los demás procesos. El límite lo comparten el servidor de la demostración, `preparar_noaa` y las pruebas del mismo equipo: no ejecute dos descargas a la vez.

**Caché local** `datos/noaa/`: `diario/AAAA-MM.npz` (SST en la malla SOME de 49 × 69, las 40 × 40 celdas nativas de la región Niño 1+2, y el tipo —final o preliminar—, el origen —PSL o NCEI— y el momento de obtención de cada día), `mensual/sst_mon_mean.npz`, `climatologia/sst_<periodo>.npz`, `climatologia/ltm_diaria_nino12.npz`, `estado.json` (verificaciones, publicación, pausas, historial y errores), `generacion` y `.bloqueos/`. Desde la caché, el mapa de un día del área completa se lee en unos 14 ms y 3 años del área completa en 0.5 s. Si a una consulta le faltan días y hay acceso, hace **una** petición a PSL como máximo (hasta 92 días, un solo intento, 8 s para conectar y 45 s para recibir) y, si PSL no responde, trae hasta 5 días recientes de NCEI; nunca espera más de 5 s a otra descarga en curso. Las respuestas cacheadas se invalidan solas cuando cambia la caché de NOAA (un día nuevo, un preliminar que pasa a final).

**Sincronización y estado (RF‑21).** Un hilo en segundo plano (primer ciclo 30 s después de arrancar, luego cada 6 horas, o cada 30 minutos si algo falló) verifica PSL (último día publicado) y el listado de NCEI (finales y preliminares), descarga lo nuevo —con la caché al día, una petición de 15 a 21 días, alrededor de un minuto con la pausa—, renueva los preliminares y completa las climatologías que falten. «Estado de fuentes» muestra para NOAA la etiqueta «Conexión real», el estado (**Operativa**; **Degradada** si PSL limitó con 429 en la última hora, va lento, está atrasado más de 3 días, solo responde NCEI, la verificación tiene más de 13 horas o hubo errores; **Sin conexión** si no responde ninguno o el modo sin red está activo), NOAA PSL y NOAA NCEI con su estado y latencia, lo publicado (último día, finales y preliminares hasta…), las climatologías, lo que sirve la caché (días finales, preliminares, faltantes y por origen) y el historial de 14 días; «Administración › Sistema» tiene la tarjeta «NOAA OISST v2.1 · caché y sincronización».

**Respaldo simulado honesto.** Como en Copernicus: si a una consulta le faltan algunos días y no se pueden obtener, esos días quedan sin dato y la respuesta lo avisa; si no le queda ningún día real, responde el simulador con `datos_reales=0`, procedencia «simulado» y el aviso «NOAA OISST no está disponible: *motivo*. Esta consulta usa datos SIMULADOS para demostración.». Nunca se mezclan días simulados y reales en un mismo resultado de NOAA. Sin red y sin caché (o con `SOME_NOAA_MODO=simulado`) NOAA vuelve al simulador anterior: fecha de corte 27/10/2026, índice Niño 1+2 simulado y la cabecera lo dice.

**Variables de entorno de NOAA OISST:**

| Variable | Efecto |
|---|---|
| `SOME_NOAA_MODO` | `auto` (por defecto: real si hay caché o acceso; si no, simulado), `simulado` (el simulador anterior, sin red; fecha de corte 27/10/2026) o `real` (estricto: sin respaldo simulado; error 422 si falta el dato). |
| `SOME_NOAA_SIN_RED=1` | No toca la red: solo la caché local; estado «Sin conexión»; sin descargas. La prueba de humo lo fija por defecto. |
| `SOME_NOAA_SIN_SINCRONIZACION=1` | No arranca el hilo de sincronización (la prueba de humo lo fija); las consultas siguen pidiendo los días ausentes si hay red. Úselo para congelar la fecha de corte durante la demostración. |
| `SOME_RUTA_NOAA=<carpeta>` | Usa esa carpeta como caché de NOAA (diario, mensual, climatología, `estado.json` y bloqueos) en lugar de `datos/noaa/`; se lee al importar. En otra carpeta, el turno con PSL ya no se comparte con el servidor principal: úsela con `SOME_NOAA_SIN_RED=1` (ver «Cachés compartidas y aislamiento», §3.1). |

**Crédito y licencia.** «NOAA OISST v2.1 (Huang et al., 2020; doi:10.25921/RE9P-PT57), NOAA NCEI y CDR Program, vía NOAA PSL.», con «vía NOAA NCEI» o «vía NOAA PSL y NOAA NCEI» si los días salieron del respaldo. Licencia: datos abiertos de NOAA sin restricciones de uso (dominio público en EE. UU.); no se puede insinuar aval de NOAA, presentar datos modificados como originales de NOAA ni usar su emblema. Ver [CONDICIONES-USO-FUENTES.md](../CONDICIONES-USO-FUENTES.md), §4. Cita del conjunto de datos: Huang, B., C. Liu, V. F. Banzon, E. Freeman, G. Graham, W. Hankins, T. M. Smith y H.-M. Zhang (2020), *NOAA 0.25-degree Daily Optimum Interpolation Sea Surface Temperature (OISST), Version 2.1*, NOAA National Centers for Environmental Information, https://doi.org/10.25921/RE9P-PT57; artículo: Huang, B. et al. (2021), *J. Climate* 34(8), 2923–2939, https://doi.org/10.1175/JCLI-D-20-0166.1. La climatología 1991-2020 cita «Climatología 1991-2020 de NOAA PSL (OISST, doi:10.25921/RE9P-PT57).»; la 1981-2010 y la 1993-2022, «Climatología <periodo> calculada por el INOCAR con OISST (doi:10.25921/RE9P-PT57).».

## 4. Usuarios de demostración

Contraseña para todos: **`some2026`**. La pantalla de acceso tiene un botón de **ingreso directo por tipo de usuario** (oceanógrafo/a, analista, revisor, administrador): un clic inicia la sesión sin escribir credenciales. El formulario de usuario y contraseña sigue disponible debajo.

| Usuario | Nombre | Rol | Departamento |
|---|---|---|---|
| `mvera` | Dra. María Vera | Oceanógrafo/a | Oceanografía y Meteorología Marina |
| `jloor` | Ing. Jorge Loor | Analista de datos | Oceanografía y Meteorología Marina |
| `rcastro` | CPFG Ricardo Castro | Jefe de departamento / revisor | Oceanografía y Meteorología Marina |
| `admin` | Administrador de TI | Administrador de TI | Tecnologías de la Información |

Solo `admin` ve el módulo **Administración**. Las vistas y los paneles son privados por defecto (RNF‑05).

## 5. Guion de demostración (10 minutos)

Las tres fuentes son reales y cada una tiene su fecha de corte. **NOAA OISST** marca la del sistema: el último día de su caché (**29/10/2026** con la caché preparada el 30/10/2026; si la sincronización trae días nuevos antes de la demostración, avanza sola: el 30/10 lo publica PSL el 31/10). **CHIRPS v3**: el último día que tiene su caché local (los últimos días suelen ser preliminares). **Copernicus Marine**: el **último día de análisis** (26/10/2026 con el boletín semanal del 28/10/2026). El estado de las tres es el real (operativa, degradada o sin conexión; «Sin conexión» en modo sin red). Antes de la demostración: `python -m backend.herramientas.preparar_noaa` (§3.3), `copernicusmarine login` (una vez) y `python -m backend.herramientas.preparar_copernicus` (§3.2); ver además «La noche anterior» al final de esta sección.

**El dato del día:** hay un **evento cálido real muy intenso** en la región Niño 1+2. El índice semanal calculado por el SOME es +5.17 °C en la semana del 18 al 24/10/2026 (NOAA CPC publica +5.2 °C para la semana del miércoles 21/10) y +5.34 °C en la semana en curso (25 al 29/10, 5 días preliminares); está en la fase cálida desde la semana del 01/02/2026. El 29/10/2026 la SST media de la región oficial es 26.51 °C, 5.19 °C por encima de lo normal. El sistema lo describe como «El Niño (fase cálida)» con semáforo rojo y no le asigna categorías de intensidad.

| Min. | Paso | Qué resaltar |
|---|---|---|
| 0–1 | Ingresar con el botón directo «Oceanógrafo(a)» (`mvera`). | Insignia verde de la cabecera «Datos reales: NOAA OISST, Copernicus Marine y CHIRPS v3» (si una fuente cayó a su simulador, la cabecera lo nombra, p. ej. «Demostración: CHIRPS v3 y Copernicus Marine con datos reales; NOAA OISST simulado»), saludo con la fecha de corte del sistema, indicador de fuentes, rol en la barra lateral, modo oscuro. |
| 1–2 | **Panel personal**: KPI de SST en Niño 1+2 con anomalía, **índice Niño 1+2** con semáforo, mini mapa de anomalía, precipitación por provincia, estado de fuentes. Menú ⋯ → «Configurar», «Ancho»; «Agregar widget»; «Guardar panel». | Cada widget lleva fuente, identificador, fecha de corte y su procedencia (RF‑11, RF‑12): marcas verdes «Datos reales · NOAA OISST» en la SST y el índice, «Datos reales · CHIRPS v3» en la lluvia. El índice es **semanal** (semanas centradas en el miércoles, como NOAA CPC), sale de la región oficial 0–10° S, 90–80° O y hoy marca El Niño (fase cálida), semáforo rojo, alrededor de +5 °C; la última semana está incompleta y es preliminar. El panel no afecta a otros usuarios. |
| 2–3 | **Consultas**: fuente NOAA, variable SST, recorte «Región Niño 1+2», atajo «Últimos 12 meses» → botón **Mapa**. | No hace falta programar (RNF‑02): cuatro pasos y un botón. Las fuentes muestran su estado y su fecha de corte. |
| 3–5 | **Mapas**: conmutar «Valor / Anomalía», periodo de referencia, mover la **barra de tiempo** y pulsar «Reproducir», pasar el cursor por una celda y hacer clic → «Ver serie aquí»; en la caja «Estilo» activar «Isolíneas» y bajar la opacidad para ver la costa. Luego Copernicus → «Corrientes (vectores)» con «Mostrar vectores» (último día de análisis). «Exportar PNG». | El mapa de NOAA muestra el **evento cálido real**: en «Anomalía» (1991‑2020), la región Niño 1+2 frente al Ecuador y el norte del Perú está de 3 a 7 °C por encima de lo normal (media +5.5 °C del 15 al 29/10/2026); moviendo la barra de tiempo hacia atrás se ve que el calentamiento empezó en febrero de 2026. Insignia verde «Datos reales · NOAA OISST», crédito con el DOI 10.25921/RE9P-PT57 y método con el promedio de 4 celdas nativas y los días preliminares (los últimos ~15). Paleta segura para daltonismo, valor por celda, vectores (RF‑06, RF‑19), crédito y método siempre visibles, PNG con crédito, identificador y fecha de corte. Las corrientes llevan la insignia verde «Datos reales · Copernicus Marine» y el crédito «Generated using E.U. Copernicus Marine Service Information; análisis diario, https://doi.org/10.48670/moi-00016.»; el método explica el promedio 3 × 3 de 1/12° a 0.25° y los días NRT. |
| 5–6 | **Series**: serie con climatología (línea discontinua) y anomalías; tarjeta «Resumen en lenguaje sencillo»; agregación mensual; pestañas «Climatología mensual» (probar 1981‑2010) y «Mismo mes cada año» (octubre). | Frases del motor sobre datos reales (RF‑07): en Niño 1+2 la anomalía mensual pasa de −0.5 °C (noviembre de 2025) a +5.4 °C (octubre de 2026, «5.4 °C por encima de lo normal para octubre»). Periodo de referencia (RF‑08): con 1981‑2010 la respuesta declara el periodo efectivo «1982-2010 en enero–agosto» (OISST empieza en 09/1981). «El mismo mes cada año» (RF‑15): octubre de 2026 promedia 27.0 °C, frente a 21.6 °C normales y +2.2 °C de anomalía en octubre de 2023. |
| 6 | **Precipitación real**: (1) en **Mapas**, CHIRPS v3 → «Precipitación», recorte «Área de estudio», últimos 30 días → Consultar, y conmutar a «Anomalía» con referencia 1991‑2020 y luego 1981‑2010; (2) en **Series**, punto en Guayaquil (2.19° S; 79.89° O), desde el 01/01/2026, agregación mensual; (3) exportar CSV; (4) un punto en el mar (2° S; 85° O). | Insignia verde «Datos reales · CHIRPS v3». Bajo cada figura, el crédito Funk et al. (2026) y, en la misma línea, **«Origen de los días»** (p. ej., «servidor del CHC: 31 días; réplica dynamical.org: 267 días») y **«Climatología»** (calculada con la réplica): el crédito cita la réplica de dynamical.org solo cuando se usó. Método con el remuestreo a 0.25° y los días preliminares; la anomalía cambia con el periodo de referencia. El CSV lleva `# Datos reales: CHIRPS v3 (CHC, UCSB)` y `# Origen de los días: …`. El punto en el mar responde «CHIRPS solo tiene datos sobre tierra…». |
| 6–7 | **Perfiles**: perfil térmico en 85° O, 2° S con la capa de mezcla (último día de análisis); pestaña «Cortina profundidad‑tiempo». | 0 a 100 m de Copernicus (RF‑13) con datos reales: cada nivel SOME interpolado entre los dos niveles del modelo; textos coherentes con los datos. |
| 7–8 | **Comparar**: SST de NOAA frente a temperatura a 0 m de Copernicus en Niño 1+2 (precargado) → «Comparar». Luego B = CHIRPS «Precipitación» en «Guayas», mismo periodo. | Diferencia (A − B), RMSE, correlación y frases (RF‑14); **real frente a real**: dos productos distintos (el análisis de observaciones de satélite e in situ de NOAA frente al modelo oceánico con asimilación de datos de Mercator) que coinciden (medido el 30/10/2026 con el último año en semanas: correlación 0.995 y diferencia media −0.26 °C). Insignia verde «Datos reales». Con unidades distintas (SST frente a precipitación) solo se calcula la **correlación**, con dos ejes, «No aplica» en diferencia y RMSE; también es real frente a real. |
| 8–9 | **Reportes**: «Boletín quincenal para el CN‑ERFEN» → «Generar»; ver la cola con progreso («Consultando NOAA OISST…», «Generando figuras…»); «Ver» el PDF del boletín recién generado (los dos reportes de ejemplo del primer arranque también se generan con datos reales). | PDF en español con la insignia verde «Datos reales» en la carátula y en cada página: SST, anomalía e índice Niño 1+2 de NOAA OISST, perfil de Copernicus y lluvia de CHIRPS, todos reales; resumen ejecutivo con el índice de la última semana y su fase (El Niño, fase cálida), figuras con crédito e insignia propia, la figura del índice con su método (región 0–10° S, 90–80° O; anomalía respecto de 1991‑2020; semanas centradas en el miércoles) y la tabla «Fuentes y métodos» con la procedencia de cada fuente y su fecha de corte (RF‑09, RF‑12). Si CHIRPS no tiene datos del periodo, la sección de precipitación lo explica y el boletín se genera igual; si una fuente respondió con su simulador, el boletín sale «Datos mixtos» y lo dice. |
| 9–10 | **Mis vistas** (compartidas por `mvera` y `jloor`), **Estado de fuentes** (las tres con la etiqueta «Conexión real»: en NOAA, NOAA PSL y NOAA NCEI con su estado y latencia, el último día publicado, hasta dónde llegan los finales y los preliminares, las tres climatologías y lo que sirve la caché local; en CHIRPS, el estado de cada camino —servidor del CHC y réplica dynamical.org— con la fecha hasta la que llega cada producto; en Copernicus, el servicio ARCO con la latencia de la lectura de prueba, las credenciales, el último día de análisis, la climatología GLORYS12 y lo que sirve la caché local; historiales de verificaciones reales, «Sin registro» en gris para los días sin verificación; la página se actualiza sola cada minuto) y, como `admin`, **Administración** → Catálogo (deshabilitar una variable), Auditoría y Sistema (tarjetas «NOAA OISST v2.1 · caché y sincronización», «Copernicus Marine · caché y sincronización» y «CHIRPS v3 · caché y sincronización»). | Vistas compartidas en solo lectura (RF‑16), estado real de las tres fuentes (RF‑21), catálogo sin código (RF‑20), registro de acciones (RNF‑05). «Acerca de» resume qué es real (todo) y cuándo se usa el simulador de respaldo. |

**La noche anterior (riesgos y qué hacer).**

1. **Caché de NOAA completa.** `python -m backend.herramientas.preparar_noaa --estimar` debe decir que no falta nada (o pocos días). Si falta mucho, ejecútela completa (unos 20 minutos con la caché vacía) **sin otro proceso descargando de PSL**: el límite de ritmo es por IP y lo comparten el servidor, `preparar_noaa` y las pruebas. Si se interrumpe, se reanuda donde quedó.
2. **No usar la red de NOAA para probar.** Las pruebas (`prueba_humo`, `prueba_noaa`) leen NOAA solo de su caché (`SOME_NOAA_SIN_RED=1`); no ejecute el bloque real de `prueba_noaa` ni otra `preparar_noaa` con el servidor de la demostración sincronizando.
3. **PSL limita (HTTP 429) o no responde durante la demostración.** Las consultas no esperan: sirven la caché y, solo si faltara un día, lo avisan; el estado pasa a «Degradada» o «Sin conexión» y lo explica. Con la caché completa ninguna consulta toca la red. Si se prefiere congelar todo, arranque con `SOME_NOAA_SIN_SINCRONIZACION=1` (la fecha de corte no avanza) o con `SOME_NOAA_SIN_RED=1` (estado «Sin conexión», datos reales de la caché).
4. **La fecha de corte puede avanzar sola** (la sincronización corre 30 s después de arrancar y luego cada 6 horas): el guion usa «último día» y «últimos 12 meses», no fechas fijas. El día nuevo llega como preliminar y la semana en curso del índice queda incompleta.
5. **Los últimos ~15 días de NOAA son preliminares** (pueden cambiar en décimas de grado cuando llegue el final): el método y el índice lo dicen; explíquelo si preguntan por qué el valor de hoy puede cambiar.
6. **El índice del SOME no es el oficial de NOAA CPC**: se calcula con el diario de 0.25° y difiere del semanal de CPC en décimas como máximo (0.13 °C en las semanas comparadas). Para declaraciones oficiales, el CN‑ERFEN y NOAA CPC.
7. **Copernicus Marine**: credenciales con `copernicusmarine login` (las guarda el usuario) y `preparar_copernicus`; sin ellas, sus consultas fuera de la caché caen al simulador y la cabecera lo dice.
8. **Prueba final**: con el servidor detenido o con copias de las cachés (§3.1), `python -m backend.pruebas.prueba_humo` debe terminar «Prueba de humo correcta».

## 6. Qué es real y qué es simulado

**Todo lo que se muestra es real** cuando las tres fuentes responden o tienen el dato en su caché (lo normal con las cachés preparadas): `datos_simulados` es `false` en el catálogo, la salud y el sistema, y la cabecera dice «Datos reales: NOAA OISST, Copernicus Marine y CHIRPS v3». El simulador solo queda como **respaldo señalado** (ver abajo).

**Real (se conserva en el producto final):**

- La interfaz completa, los flujos de trabajo, la navegación y los textos en español del Ecuador.
- La API REST (`/api`), la autenticación por roles, el panel personal, las vistas, la auditoría y la cola de trabajos.
- El motor de cálculo: recortes, promedios, climatología mensual, anomalías con periodo de referencia, series, perfiles, cortinas, «mismo mes cada año», comparaciones, vectores y resúmenes en lenguaje sencillo. Cada respuesta documenta su `metodo` y la `fecha_corte` de la fuente.
- Los reportes PDF (reportlab + matplotlib) y las exportaciones CSV, PNG y NetCDF con metadatos CF.
- El **contrato de los conectores** (`backend/conectores/base.py`): `obtener(ParametrosConsulta) -> xarray.Dataset` con atributos CF, `climatologia(...)` y `estado()`, más la procedencia (`datos_reales`, `insignia`).
- **La temperatura superficial del mar de NOAA OISST v2.1** (`backend/conectores/noaa.py`, que reexporta `backend/noaa/conector.py`, y `backend/noaa/`): SST diaria a 0.25° de NOAA/NCEI obtenida de NOAA PSL (respaldo: NOAA NCEI), llevada a la malla SOME (media de las 4 celdas nativas que rodean cada centro, mínimo 2), con el tipo (final o preliminar) y el origen de cada día; climatologías 1991‑2020 (oficial), 1981‑2010 (periodo efectivo «1982-2010 en enero–agosto») y 1993‑2022; el **índice Niño 1+2 semanal** de la región oficial 0–10° S, 90–80° O (contrastado con NOAA CPC); estado e historial de 14 días a partir de verificaciones reales de PSL y NCEI. Crédito: NOAA OISST v2.1 (Huang et al., 2020; doi:10.25921/RE9P-PT57), NOAA NCEI y CDR Program, vía NOAA PSL; datos abiertos de NOAA (detalles en §3.3).
- **La precipitación de CHIRPS v3** (`backend/conectores/chirps.py` y `backend/chirps/`): diarios finales y preliminares llevados a 0.25°, obtenidos del servidor del CHC o de la réplica de dynamical.org (el origen de cada día queda guardado); climatología mensual 1981‑2022 calculada con el mismo producto; estado e historial de 14 días a partir de verificaciones reales de los dos caminos. Solo hay datos sobre tierra. Crédito: CHIRPS v3.0, Climate Hazards Center, UC Santa Barbara; Funk, C., Peterson, P., Harrison, L., et al. (2026), *Scientific Data* 13, 718, https://doi.org/10.1038/s41597-026-07096-4. En el pie de las figuras: «CHIRPS v3.0, CHC, UC Santa Barbara (Funk et al., 2026; doi:10.15780/G2JQ0P), CC BY 4.0.». Licencia (según https://www.chc.ucsb.edu/data/chirps3 y Funk et al., 2026): CC BY 4.0, que exige citar, enlazar la licencia e indicar los cambios al compartir; el CHC declara además CHIRPS3 de dominio público. El README-CHIRPSv3.0.txt no trae licencia. Condiciones de uso de las fuentes y de la réplica: [CONDICIONES-USO-FUENTES.md](../CONDICIONES-USO-FUENTES.md), §5 y §6. Repositorio de datos (lo piden el CHC y dynamical.org): Climate Hazards Center (2025), *Climate Hazards Center Infrared Precipitation with Stations version 3 (CHIRPS3) Data Repository*, https://doi.org/10.15780/G2JQ0P; las exportaciones NetCDF y la sección «Fuentes y métodos» de los PDF lo citan con la fecha de la última descarga. Cuando se usa la réplica se cita además la atribución literal de dynamical.org («UCSB Climate Hazards Center CHIRPS version 3.0 data processed by dynamical.org from the Climate Hazards Center data archive»), su cita, CC BY 4.0 y el aviso de que redondea los valores a 8 bits de mantisa. El crédito de cada consulta dice «vía la réplica» solo si todos sus días vienen de ella y «parte de los días vía la réplica» si se mezclan con días del CHC.
- **Las variables físicas del océano de Copernicus Marine** (`backend/conectores/copernicus.py` y `backend/copernicus/`): análisis diario de GLOBAL_ANALYSISFORECAST_PHY_001_024 leído con el Copernicus Marine Toolbox, llevado a 0.25° (media 3 × 3 de 1/12°) y a los 8 niveles de 0 a 100 m (interpolación lineal), solo hasta el último día de análisis; climatología oficial GLORYS12 1993‑2016 (el periodo efectivo se declara siempre); estado e historial de 14 días a partir de verificaciones reales (credenciales, lectura de prueba, último día de análisis). Crédito: «Generated using E.U. Copernicus Marine Service Information», https://doi.org/10.48670/moi-00016 y, para la climatología, https://doi.org/10.48670/moi-00021 (detalles en §3.2).

**Simulado (solo como respaldo señalado, o si se fuerza):**

- El **respaldo de NOAA OISST y de Copernicus Marine**: si una fuente no tiene acceso (red, servicio o, en Copernicus, paquete o credenciales) y su caché local no tiene el dato, el generador determinista (`backend/datos_simulados.py`: gradiente latitudinal, lengua fría ecuatorial, afloramiento costero, ciclo estacional, eventos El Niño/La Niña y ruido reproducible con semilla fija) responde con `datos_reales=0`, procedencia «simulado» y un aviso que dice por qué. Nunca se mezclan días simulados y reales en un mismo Dataset; si se combinan con datos reales de otra fuente, el resultado sale «mixto» y lo dice. Sin red y sin caché, NOAA queda en modo simulado (fecha de corte 27/10/2026 e índice Niño 1+2 simulado, de +1.8 °C en octubre de 2026); `SOME_NOAA_MODO=simulado` y `SOME_COPERNICUS_MODO=simulado` fuerzan el simulador anterior.
- La climatología del simulador es analítica: con él, los tres periodos (1991‑2020, 1981‑2010, 1993‑2022) devuelven los mismos valores y el método lo indica. Con datos reales, la de NOAA es la de cada periodo (1981‑2010 con su periodo efectivo), la de CHIRPS se calcula con los datos mensuales reales de cada periodo y la de Copernicus es la oficial de GLORYS12 (1993‑2016) para cualquier periodo pedido; la respuesta lo dice.

**Propio de la demo (no es un dato simulado, pero cambia en el producto final):**

- Malla única de 0.25° para las tres fuentes; profundidades 0 a 100 m en 8 niveles.
- Periodo diario desde el 01/10/2023 («últimos 3 años») hasta la fecha de corte de cada fuente. Área de estudio 6° N a 6° S, 95° O a 78° O; los mapas y series de la región Niño 1+2 se recortan al área (0–6° S), mientras que el índice Niño 1+2 usa la región oficial completa (0–10° S).
- Las cachés locales (`datos/noaa/`, `datos/chirps/`, `datos/copernicus/`) en lugar del lago de datos de la Fase 2.
- La persistencia en archivos JSON de `datos/` y los *tokens* de sesión en memoria (en el producto real: PostgreSQL/PostGIS y sesiones persistentes).

**Cómo se señala la procedencia.** La procedencia se calcula con los **Dataset** que realmente formaron cada resultado (cada uno declara `datos_reales` 1/0), no solo con el conector: un conector real que respondió con su simulador cuenta como simulado. Cada respuesta de la API con datos trae `datos_simulados` (false solo si todo es real), `procedencia` («real», «simulado» o «mixto») e `insignia` (texto para mostrar); `avisos` (lista) si algún Dataset trae uno (respaldo simulado, datos parciales) y `periodo_referencia_efectivo` si la climatología usada es de otro periodo que el pedido; con fuentes reales, además, `dias_preliminares`, `dias_faltantes`, `usa_replica`, `origen_datos` (NOAA: días finales y preliminares, p. ej. «NOAA OISST v2.1: 348 días finales y 15 preliminares», y cuántos vinieron de NOAA NCEI si se usó el respaldo; CHIRPS: días por camino, p. ej. «servidor del CHC: 31 días; réplica dynamical.org: 24 días»; Copernicus: «Copernicus Marine (análisis ANFC): N días de mejor análisis y M de análisis NRT») y `origen_climatologia`. El `fuente.credito` de cada respuesta es el de **esa** consulta: en NOAA, el de OISST v2.1 con su DOI y, si hubo anomalías, el de su climatología; en CHIRPS cita la réplica de dynamical.org solo si sus días o su climatología salieron de ella (y `fuente.credito_replica` trae la cita completa); en Copernicus une el crédito del análisis y, si hubo anomalías, el de GLORYS12. El catálogo (`GET /api/catalogo`) trae `fecha_corte` (la del sistema: la de NOAA), `fuentes_reales`, `insignia_cabecera` (según los modos de NOAA y Copernicus) y, por fuente, `datos_reales` e `insignia`; `GET /api/fuentes/estado` trae, para NOAA, el bloque `noaa` (modo, acceso, servidores PSL y NCEI, publicación, climatologías y caché); para CHIRPS, `caminos` (estado de cada camino y `productos.final`/`productos.preliminar`) y `cache` (fechas y días por origen), y para Copernicus el bloque `copernicus` (modo, acceso, paquete, credenciales —solo su estado—, servicio, análisis, climatología y caché). Las figuras PNG llevan el crédito y la insignia al pie (verde para datos reales, ámbar para simulados o mixtos); el CSV, las líneas `# Crédito:`, `# Licencia:`, `# Procesamiento:` (datos procesados por el INOCAR, no originales ni avalados por los proveedores, sin garantía), `# Crédito de la réplica:` si se usó, `# <insignia>`, `# Origen de los días: …`, la de la climatología, `# Periodo de referencia efectivo: …` y `# Aviso: …` cuando corresponde; el NetCDF, `comment = <insignia>`, `procedencia`, `credito`, `licencia`, los atributos ACDD `license` y `acknowledgement`, `aviso_procesamiento`, `credito_replica` si se usó, `origen_datos`, `origen_climatologia`, `periodo_referencia_efectivo` y `avisos`; los PNG, el aviso de procesamiento al pie (con CHIRPS, también el enlace a CC BY 4.0); los PDF, la insignia en la carátula y en el encabezado de cada página y, en «Fuentes y métodos», el aviso de procesamiento, la procedencia de cada fuente, una fila propia para la réplica de dynamical.org cuando se usó, la licencia completa de cada fuente bajo la tabla y el origen de los datos. La exportación responde además con las cabeceras `X-SOME-Procedencia` y `X-SOME-Datos-Simulados`.

## 7. Estructura del código

```
prototipo-some/
  README.md                        este archivo
  requirements.txt                 dependencias Python
  requirements-replica.txt         opcionales: icechunk y zarr (réplica dynamical.org de CHIRPS)
  requirements-copernicus.txt      Copernicus Marine Toolbox (copernicusmarine>=2.5,<3)
  ejecutar.ps1 / ejecutar.bat      crean .venv, instalan y arrancan uvicorn en el puerto 8050
  docs/ESPECIFICACION-PROTOTIPO.md contrato entre motor, API e interfaz (nombres, rutas, formatos)
  docs/DISENO-COPERNICUS.md        diseño del conector real de Copernicus Marine
  docs/DISENO-NOAA.md              diseño, mediciones y verificación del conector real de NOAA OISST
  docs/capturas/                   capturas de pantalla de referencia
  docs/capturas/plan-b/            capturas de los seis pasos de la demostración con CHIRPS real (30/10/2026),
                                   reunidas en 0-plan-b-capturas.pdf, más el CSV y el boletín PDF exportados
  backend/
    app.py                         FastAPI: monta /api, sirve frontend/ y crea los trabajos de ejemplo
    configuracion.py               rutas, puerto, área, malla, fechas, formato es-EC
    auth.py                        usuarios, roles, tokens, dependencias usuario_actual / requiere_rol
    almacen.py                     persistencia JSON con bloqueo y datos iniciales
    catalogo.py                    fuentes, variables, recortes, periodos de referencia, estado
    datos_simulados.py             generador determinista de campos
    conectores/base.py             contrato ConectorFuente, ParametrosConsulta, procedencia_de (catálogo) y
                                   procedencia_de_datos (por Dataset: real/simulado/mixto y avisos)
    conectores/noaa.py             conector REAL de NOAA OISST v2.1 (reexporta backend/noaa/conector.py)
    conectores/copernicus.py       conector REAL de Copernicus Marine (caché local, descarga bajo demanda y
                                   respaldo simulado señalado)
    conectores/chirps.py           conector REAL de CHIRPS v3 (sirve la caché local; obtiene lo que falte)
    noaa/                          acceso real a NOAA OISST v2.1:
      conector.py                  ConectorNOAA (caché local, días ausentes bajo demanda, respaldo simulado señalado)
      red.py                       único módulo que toca la red: ritmo entre procesos, pausa tras PSL, 429, pausas
      malla.py, almacen.py         ventana nativa, media 2 × 2 a la malla SOME; caché mensual con tipo y origen
      descarga.py                  tramos de PSL (≤ 92 días) y respaldo NCEI; final o preliminar por día
      climatologia.py, indice.py   climatologías y periodo efectivo; índice Niño 1+2 semanal (región oficial)
      sincronizacion.py, estado.py hilo de sincronización y estado RF-21 real (PSL, NCEI e historial)
      prueba_noaa.py               pruebas del paquete (con dobles, sin red; bloque real opcional)
    copernicus/                    acceso real a Copernicus Marine:
      acceso.py                    único módulo que importa copernicusmarine (perezoso; guardia anti-prompt)
      calendario.py                boletín semanal y último día de análisis (nunca pronóstico)
      malla.py, niveles.py         media 3 × 3 de 1/12° a 0.25° e interpolación vertical a los 8 niveles
      almacen.py, descarga.py      caché por variable/nivel/mes y plan de descarga por fragmentos ARCO
      climatologia.py              climatología GLORYS12 y su periodo efectivo (climatology_bounds)
      sincronizacion.py, estado.py hilo de sincronización y estado RF-21 real (historial de 14 días)
      prueba_copernicus.py         pruebas del paquete (con dobles, sin red; bloque real opcional)
    chirps/                        acceso real a CHIRPS v3 por dos caminos:
      red.py, descarga.py          servidor del CHC (cortesía, disyuntor) y plan de llenado réplica/CHC
      espejo.py                    réplica dynamical.org (Icechunk/Zarr en S3; opcional)
      lector_tiff.py, malla.py     GeoTIFF por rangos HTTP; agregación 5×5 y reproyección a la malla SOME
      almacen.py, climatologia.py  caché diaria (valor, tipo y origen por día) y climatología mensual
      sincronizacion.py, estado.py hilo de sincronización y estado RF-21 real (caminos e historial)
      prueba_chirps.py, prueba_espejo.py   pruebas del paquete
    herramientas/preparar_chirps.py   prepara la caché de CHIRPS (diario y climatología)
    herramientas/preparar_copernicus.py   prepara la caché de Copernicus (escenarios de la demostración)
    herramientas/preparar_noaa.py  prepara la caché de NOAA (diario, climatologías e índice Niño 1+2)
    motor.py                       funciones puras sobre xarray y armado de las respuestas /api/consultas/*
    graficos.py                    figuras matplotlib (PNG de mapa para Leaflet, figuras completas)
    exportar.py                    CSV, PNG y NetCDF
    reportes.py                    plantillas y generación de PDF
    trabajos.py                    cola de trabajos en un hilo de fondo (resistente a fallos)
    cache.py                       caché LRU de campos y consultas
    esquemas.py                    modelos pydantic
    rutas/                         auth, catalogo, consultas, exportar, reportes, panel, vistas, admin
    pruebas/prueba_humo.py         recorre toda la API
  frontend/
    index.html, css/app.css        única página y sistema visual (modo claro/oscuro, impresión)
    js/util.js, api.js, app.js     utilidades es-EC, cliente HTTP, enrutador por hash y contrato de módulos
    js/modulos/*.js                acceso, panel, consultas (constructor reutilizable), mapa, series,
                                   perfiles, comparar, reportes, vistas, fuentes, admin, acerca
    img/logo-some.svg
  datos/                           se crea al arrancar: JSON (usuarios, paneles, vistas, auditoría,
                                   trabajos, catálogo) y reportes/<id>.pdf
  datos/noaa/                      caché de NOAA OISST (diario, Niño 1+2 nativo, climatologías) y estado.json
  datos/chirps/                    caché de CHIRPS v3 en la malla de 0.25° y registro de verificaciones
  datos/copernicus/                caché de Copernicus Marine (diario y climatología, ya en 0.25°) y estado.json
```

## 8. Cómo reemplazar los conectores simulados por los reales

Los tres conectores ya siguieron este camino; queda descrito para agregar fuentes nuevas (p. ej., viento) y para el producto final. CHIRPS v3 fue el primero: `ConectorCHIRPS` hereda de `ConectorFuente` (no de `ConectorSimulado`), declara `datos_reales = True` y su `insignia`, e implementa además `historial_estado(dias)` para que «Estado de fuentes» muestre verificaciones reales y `detalle_estado()` para mostrar sus dos caminos. Sus `Dataset` declaran en los atributos el origen de los días (`origen_datos`, `dias_origen_chc`, `dias_origen_replica`), el crédito de la consulta (`credito`, `credito_replica`) y el origen de la climatología (`origen_climatologia`); el motor los lleva a `fuente` y a la procedencia de cada respuesta. El motor, la API, la interfaz y los reportes solo pasaron a leer esa procedencia.

Copernicus Marine siguió el mismo camino con dos agregados: `datos_reales` e `insignia` son **dinámicos** (real si hay caché o acceso) y cada `Dataset` declara su propia procedencia (`datos_reales` 1/0, `aviso`, `respaldo_simulado`), porque el conector puede responder con su simulador de respaldo; el motor calcula la procedencia con `procedencia_de_datos` mirando los Dataset. Su climatología declara `periodo_efectivo` (1993‑2016) y el motor lo lleva a `periodo_referencia_efectivo`, al método, al subtítulo y a las exportaciones. `climatologia(..., simulada=True)` devuelve la del simulador: el motor la pide así cuando los datos de la misma consulta salieron del respaldo, para que la anomalía sea coherente.

NOAA OISST v2.1 siguió el patrón de Copernicus: `ConectorNOAA` (`backend/noaa/conector.py`, reexportado por `backend/conectores/noaa.py`) conserva exactamente el `id`, los nombres y la variable `sst` del conector simulado anterior; `datos_reales`, `insignia`, `credito`, `resolucion` y `cobertura_temporal` son dinámicos, y cada Dataset declara su procedencia, sus días finales y preliminares (`dias_finales`, `dias_preliminares`, `periodo_preliminar`, `origen_datos`) y, en la climatología, `periodo_efectivo`. Agrega `serie_indice_nino12()` para el índice Niño 1+2 semanal real, que el motor usa en el panel y el boletín. Además, la **fecha de corte del sistema** pasó a ser dinámica (`catalogo.fecha_corte_sistema()`: la de NOAA) y, con las tres fuentes reales, `datos_simulados` es `false` en todo el sistema y la cabecera dice «Datos reales: NOAA OISST, Copernicus Marine y CHIRPS v3», sin tocar el resto del código.

Cada conector hereda de `ConectorFuente` (`backend/conectores/base.py`) y solo debe cumplir tres métodos; el motor, la API, la interfaz y los reportes no cambian.

1. **`obtener(p: ParametrosConsulta) -> xr.Dataset`**: devuelve las variables pedidas con dimensiones `(time, [depth], lat, lon)`, coordenadas CF (`lat` en `degrees_north`, `lon` en `degrees_east`, `time` diario, `depth` en metros positivos hacia abajo) y atributos `standard_name`, `long_name` (en español) y `units`. En el producto real:
   - **NOAA OISST v2.1** (ya real): subconjuntos NCSS del servidor THREDDS de NOAA PSL (`sst.day.mean.AAAA.nc`, NetCDF4 leído con h5netcdf; el OPeNDAP directo exige netCDF4 o pydap, que el prototipo no usa) y, de respaldo, los archivos diarios de NOAA NCEI; sin credenciales.
   - **Copernicus Marine** (ya real): `GLOBAL_ANALYSISFORECAST_PHY_001_024` con `copernicusmarine.open_dataset(...)` (lectura perezosa sobre ARCO, solo área, días y niveles necesarios) y la climatología de `GLOBAL_MULTIYEAR_PHY_001_030`; en el producto final, con la cuenta institucional.
   - **CHIRPS v3** (ya real): GeoTIFF diarios del servidor del CHC (`data.chc.ucsb.edu/products/CHIRPS/v3.0/`), leídos por rangos HTTP, o la réplica Zarr/Icechunk de dynamical.org, y guardados en caché.
2. **`climatologia(variable, periodo_referencia, bbox, profundidad)`**: climatología mensual (dimensión `month` 1..12) del periodo de referencia; en la Fase 2 se precalcula en el lago de datos.
3. **`estado() -> EstadoFuente`**: comprueba la fuente y devuelve `operativa | degradada | sin_conexion`, la última fecha publicada (`fecha_corte`), la latencia y un mensaje.

Además, cada conector declara `id`, `nombre`, `identificador`, `credito`, `licencia`, `resolucion`, `cobertura_temporal`, su procedencia (`datos_reales`, `insignia`) y su lista de `VariableFuente` (con `tiene_profundidad`, `es_vector` y `componentes` para `uo`/`vo`). Registre la nueva clase en `backend/conectores/__init__.py` (`crear_conectores`). Un conector real y uno simulado conviven, porque el catálogo los trata igual; la procedencia de cada resultado se calcula sola (`procedencia_de_datos`).

Resoluciones distintas (1/12°, 0.05°) no requieren cambios en el motor: trabaja con las coordenadas que entrega el conector. Solo `_bbox_bordes` y la máscara tierra/mar del generador (`datos_simulados.mascara_tierra`) son propios de la demo; esta última ya no se aplica como tal a las fuentes reales (para CHIRPS, una celda sin dato es mar y el motor lo explica; para Copernicus, solo se rechaza de antemano un perfil o cortina claramente tierra adentro —también lo es todo a ±0.5°— para no descargar datos que serían todos NaN; un punto costero se consulta).

## 9. Correspondencia módulo ↔ requerimientos (CONOPS v1.3)

| Módulo o componente | Requerimientos |
|---|---|
| Acceso (`acceso.js`, `auth.py`) | RF‑01, RNF‑05 |
| Panel personal (`panel.js`, `rutas/panel.py`) | RF‑11, RF‑12 |
| Constructor de consultas (`consultas.js`) | RF‑02 (recorte, rectángulo, punto), RF‑17, RNF‑02 |
| Conectores (`conectores/`, `noaa/`, `chirps/`, `copernicus/`) | RF‑03 (NOAA OISST, **real**; índice Niño 1+2 real), RF‑04 (Copernicus, **real**), RF‑05 (CHIRPS, **real**), RNF‑08, RNF‑09 |
| Visor de mapas (`mapa.js`) | RF‑06, RF‑12, RF‑17, RF‑19 (corrientes), RNF‑10 |
| Series (`series.js`) | RF‑07, RF‑08, RF‑15 |
| Perfiles (`perfiles.js`) | RF‑13 |
| Comparar (`comparar.js`) | RF‑14 |
| Exportación (`exportar.py`) | RF‑10 (CSV, PNG), RF‑18 (NetCDF con metadatos CF) |
| Reportes y cola de trabajos (`reportes.py`, `trabajos.py`, `reportes.js`) | RF‑09, RF‑12, RNF‑03 |
| Mis vistas (`vistas.js`, `rutas/vistas.py`) | RF‑16, RNF‑05 |
| Estado de fuentes (`fuentes.js`, `catalogo.py`, `noaa/estado.py`, `chirps/estado.py`, `copernicus/estado.py`) | RF‑21 (real para las tres fuentes) |
| Administración (`admin.js`, `rutas/admin.py`) | RF‑20, RNF‑05, RNF‑09 |
| Motor (`motor.py`) | RF‑06 a RF‑08, RF‑13 a RF‑15, RF‑19, RNF‑03, RNF‑06 |
| Interfaz y textos | RNF‑01 (español del Ecuador, dd/mm/aaaa, punto decimal), RNF‑10 (paletas seguras, foco visible, contraste AA) |
| Caché (`cache.py`) | RNF‑06, RNF‑07 |

Fuera de la Fase 1 (no se implementan en el prototipo): RF‑22 a RF‑24 (lago de datos, STAC, carga de archivos propios), RF‑25 a RF‑27 (alertas, aplicación móvil, validación de boyas), RF‑28 (modelos propios, *won't*), RNF‑04, RNF‑11 y RNF‑12 (despliegue en el centro de datos y contenedores).

## 10. Limitaciones del prototipo

- **Respaldo simulado** (NOAA OISST o Copernicus Marine sin acceso ni caché, o con su modo simulado forzado): esos valores no sirven para análisis real, solo para mostrar el funcionamiento; siempre llevan la insignia ámbar (y un aviso con el motivo cuando son un respaldo). La precipitación de CHIRPS v3 es real, pero llevada a la malla de 0.25° del prototipo (la resolución nativa del preliminar es 0.05°) y con los últimos días preliminares.
- **NOAA OISST preliminar en los últimos ~15 días**: NOAA/NCEI los reemplaza por el final unas dos semanas después (cambios de décimas de grado); se señalan y la sincronización los renueva cada 6 horas. Un día de PSL se toma como final solo si NCEI publicó su final al menos 12 horas antes (margen de seguridad: un día ya final puede figurar como preliminar unas horas).
- **NOAA OISST a la malla SOME**: los centros nativos (x.125/x.375) no coinciden con los de la malla SOME (múltiplos de 0.25°); cada celda SOME es la media de las 4 nativas que la rodean (mínimo 2), así que la costa queda suavizada media celda.
- **Índice Niño 1+2 del SOME**: se calcula con el diario a 0.25° y difiere en décimas como máximo del semanal oficial de NOAA CPC (0.13 °C en las semanas comparadas el 30/10/2026); la semana en curso es incompleta y preliminar. El sistema da la fase (El Niño, La Niña o neutra, con umbral ±0.5 °C) pero no categorías de intensidad: las declaraciones oficiales corresponden al CN‑ERFEN y a NOAA CPC.
- **Ritmo de NOAA PSL**: limita por IP (HTTP 429) y corta las peticiones de más de ~60 s; preparar la caché vacía tarda unos 20 minutos y no conviene ejecutar dos descargas a la vez. Una consulta con días ausentes hace una sola petición (hasta ~30 s) y, si PSL no responde, sirve lo que haya y lo avisa.
- **Copernicus Marine a 0.25°**: el análisis nativo es de 1/12°; el prototipo lo promedia en bloques de 3 × 3 (mínimo 5 celdas válidas, por lo que algunas celdas costeras quedan sin dato) e interpola en la vertical a 8 niveles hasta 100 m. Los últimos 7 días de análisis son «NRT» y Mercator los reemplaza la semana siguiente; la sincronización renueva los de superficie y el resto se sirve como NRT (se dice).
- **Climatología de Copernicus de otro periodo y otro sistema**: la oficial de GLORYS12 es de 1993‑2016 (reanálisis), mientras que los datos diarios son del análisis operativo; parte de una anomalía puede deberse a esa diferencia (en `zos`, a referencias distintas del nivel medio). Calcular una climatología propia 1991‑2020 con GLORYS12 queda para la Fase 2 (lago de datos).
- **Descargas de Copernicus bajo demanda**: una consulta que no esté en la caché descarga en el momento (fragmentos ARCO de varios MiB; la apertura de cada dataset tarda unos segundos) con una espera máxima de 40 s y un tope de tamaño; por eso conviene preparar la caché con `preparar_copernicus` la noche anterior. El archivo móvil del análisis cubre unos dos años: lo ya guardado se conserva.
- **Credenciales de Copernicus**: las guarda el usuario con `copernicusmarine login` en el equipo que ejecuta el servidor; sin ellas Copernicus funciona solo con la caché y el respaldo simulado.
- **CHIRPS solo cubre tierra**: los recortes marinos y los puntos en el mar no tienen precipitación; Galápagos queda con pocas celdas de tierra a 0.25°.
- **Primera preparación de CHIRPS**: prepárela con internet antes de la demostración. Con la réplica de dynamical.org la climatología 1981‑2022 tarda alrededor de 1.5 minutos; solo con el CHC se calcula con sus NetCDF globales (unos 190 MB por año, de los que se leen por rangos solo el recorte del área) y tarda mucho más.
- **Réplica de dynamical.org**: es un tercero sin garantía de servicio y publica el preliminar con unos días de retraso; los días más recientes solo los tiene el servidor del CHC. Si el CHC no responde, esos días quedan sin dato hasta que vuelva (el estado lo explica).
- **Una sola agregación para todo el producto**: el final y el preliminar del CHC se leen a 0.05° y se agregan 5×5 en el SOME, igual que la réplica y la climatología (validado: el final del CHC a 0.05° coincide con la réplica a 0.07 %, con las mismas 407 celdas). Versiones anteriores tomaban el final ya agregado por el CHC a 0.25° (`p25`): con otra máscara costera, daba 15 celdas costeras sin climatología y diferencias de hasta ~30 % en celdas comunes de la costa (p. ej., 3.1 frente a 4.3 mm/día en 1.5° S; 81° O el 27/09/2026). Esos días se marcan al leerlos como «finales provisionales» y la sincronización los reemplaza por el final a 0.05° en cuanto la réplica o el CHC lo cubren; mientras tanto el método de la consulta lo advierte.
- **Vectores de viento** (RF‑19): el catálogo de la demo solo trae corrientes (`uo`, `vo`, reales de Copernicus); el viento se incorporará con otro producto de Copernicus o de NOAA.
- **Lista de coordenadas** (RF‑02): el constructor ofrece recorte, rectángulo y punto; la consulta por lista de puntos queda para el producto real.
- **Formato ORM** de los oceanógrafos: su estructura está por confirmar con una muestra; no se exporta.
- NetCDF en formato clásico (NetCDF3 vía `scipy`), sin compresión; el producto real usará NetCDF4 o Zarr.
- Un solo hilo de trabajos: los reportes se generan en orden. Si se cierra el servidor con trabajos pendientes, al arrancar se marcan con error y se vuelven a generar a pedido.
- Las sesiones viven en memoria: al reiniciar el servidor hay que ingresar de nuevo.
- Los mapas de fondo (OpenStreetMap), las bibliotecas Leaflet y Plotly y los iconos de Flaticon se cargan desde internet; sin conexión el mapa muestra solo la capa de datos y los botones de icono quedan sin dibujo (conservan su texto o su etiqueta accesible).
- La región Niño 1+2 real llega a 10° S; los mapas y las series de la demo la recortan al área de estudio (0–6° S), mientras que el índice Niño 1+2 usa la región oficial completa (0–10° S, 90–80° O).
- Los selectores de fecha nativos muestran el formato del idioma del navegador; junto a cada uno se muestra la fecha en dd/mm/aaaa.

## 11. Del prototipo al producto (flujo propuesto)

El prototipo se parece al producto, pero el destino es el flujo del CONOPS (sección 7.3, figura 5; el apéndice A.1 justifica la elección del lago): la ingesta programada alimenta un lago de datos en el centro de datos del INOCAR y la API del SOME lee **solo** de ese lago. El prototipo toma atajos para poder correr en un portátil con unos tres años de datos:

| Capa | Prototipo (Fase 1) | Producto (flujo propuesto) |
|---|---|---|
| Fuentes | NOAA OISST, Copernicus Marine y CHIRPS v3, reales | Las mismas |
| Ingesta | Hilo de sincronización cada 6 h que trae solo lo nuevo, y `preparar_*` para llenar la caché; historia desde el 01/10/2023 | Tareas programadas: carga histórica inicial (~30 años) y luego actualización incremental según publica cada fuente |
| Consultas | Si falta un día o un área, la piden a la fuente en ese momento y la guardan | Leen solo del lago; nunca salen a internet |
| Zona cruda | No existe: se guarda ya llevado a la malla de 0.25° | Archivos originales, tal como llegan |
| Zona curada | Archivos `.npz` mensuales en `datos/<fuente>/`; climatologías descargadas (NOAA PSL 1991‑2020, GLORYS12) o calculadas; anomalías calculadas en cada consulta | Zarr con climatologías y anomalías precalculadas |
| Catálogo y base tabular | Archivos JSON en `datos/` (usuarios, paneles, vistas, auditoría, trabajos, catálogo) | Catálogo STAC y PostgreSQL/PostGIS |
| API local | FastAPI, caché de consultas en memoria y reportes PDF; autenticación de demostración | Se reutiliza, con autenticación y sesiones reales |
| Aplicación web | HTML, CSS y JavaScript | Se reutiliza |
| Dónde corre | Un portátil | Centro de datos del INOCAR |

Se reutilizan los conectores (descarga, conversión a la malla, paso de preliminar a definitivo, ritmo de peticiones y credenciales), el motor (climatologías, anomalías, índice Niño 1+2), la API, la interfaz y los reportes. Cambian el almacenamiento (el `almacen.py` de cada fuente y el `almacen.py` general), la ingesta (de un hilo a tareas programadas con carga histórica) y las consultas, que dejan de descargar.

### 11.1 Límites de ritmo de las API

El lago no se alimenta de forma continua: cada fuente publica a su ritmo y la ingesta incremental solo trae lo nuevo.

- **NOAA OISST**: un día nuevo por día (el archivo diario de NCEI pesa unos 1.5 MB) y el reemplazo de los preliminares por los finales, unos 15 días después.
- **Copernicus Marine**: el análisis, una vez por semana (los jueves), con el reemplazo de los 7 días NRT de la semana anterior.
- **CHIRPS v3**: el preliminar, con unos días de retraso, y el final, una vez al mes.

Son pocas peticiones al día, lejos de los límites observados. Además, como las consultas leen solo del lago, el número de usuarios no cambia lo que se pide afuera. En el prototipo sí lo cambia: cada consulta de un periodo que no está en la caché genera una descarga.

El riesgo está en la **carga histórica inicial**, que se hace una vez. Lo observado en el prototipo:

- NOAA PSL limita por IP con HTTP 429 a quien pide seguido (con una petición cada 20 s, aproximadamente 1 de cada 8 recibe 429) y corta las peticiones de más de ~60 s.
- El servidor del CHC también respondió HTTP 429 el 30/10/2026 durante unos 45 minutos, incluso a intentos aislados tras cada pausa de 15 minutos: no basta con pedir más despacio. El prototipo lo dejó en pausa y sirvió la réplica.
- Copernicus Marine pide cuenta; en el prototipo no se ha alcanzado ningún límite, pero su carga histórica (GLORYS12 desde 1993, por profundidad) es la más voluminosa.

Cómo se evita. Las cinco primeras medidas ya están en los conectores del prototipo y pasan a la ingesta (con el ajuste que se indica en la segunda):

1. **Ritmo propio**: pausa de 20 s tras cada petición a NOAA PSL y como máximo 1 petición por segundo al CHC.
2. **Esperas y pausas**: tras un 429 de NOAA, la sincronización y `preparar_noaa` esperan 1, 2, 4 y 5 min antes de reintentar; al CHC no se le pide nada durante 15 min tras un HTTP 429 o tras 3 fallos seguidos, en todos los procesos. NOAA queda 10 min en pausa tras 3 fallos seguidos, pero hoy esa pausa solo frena a las consultas (la sincronización y `preparar_noaa` no la respetan): la ingesta tendrá que respetarla.
3. **Tramos acotados** (hasta 92 días por petición a PSL).
4. **Reanudación**: solo se pide lo que falta, así que una carga interrumpida sigue donde quedó.
5. **Caminos alternativos** para cada fuente: NOAA PSL o NOAA NCEI; servidor del CHC o réplica de dynamical.org (que redondea los valores para comprimir).
6. **Para la carga histórica** (recomendación): repartirla en varias madrugadas y preferir los archivos masivos (anuales, o la réplica de CHIRPS en Zarr) a muchas peticiones pequeñas.
7. **Seguimiento**: el estado de cada fuente (RF‑21), el registro de cada ingesta en el catálogo (RF‑23) y la ingesta recuperable (RNF‑11). Si una fuente limita o se cae, solo se atrasan los días nuevos: lo que ya está en el lago se sigue consultando.

---

INOCAR · ESPOL · Equipo T5 — Francisco Etienne Defranc Rojas, Christian David Olmedo Borbor, Niurka María Roca Chévez, Samuel Alejandro Villagómez Rodríguez. Docente: Mónica Katiuska Villavicencio Cabezas. Versión 0.1.0-demo.
