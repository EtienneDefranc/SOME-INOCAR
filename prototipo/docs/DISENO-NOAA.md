# Diseño: NOAA OISST v2.1 con datos reales en el SOME

> Fecha: 30/10/2026 (demo: 31/10/2026). Patrón: el de CHIRPS v3 y Copernicus Marine (`backend/chirps/`,
> `backend/copernicus/`, `docs/DISENO-COPERNICUS.md`). Este paso crea SOLO archivos nuevos:
> `backend/noaa/` (paquete y conector), `backend/herramientas/preparar_noaa.py` y este documento. La
> integración (§5) toca los archivos existentes en otro paso.

## 0. Decisiones

1. **Producto**: NOAA 0.25° Daily OISST v2.1 (NOAA/NCEI, DOI 10.25921/RE9P-PT57), diario a 0.25°, centros nativos en x.125/x.375.
2. **Vía principal: NCSS de NOAA PSL** (THREDDS `Datasets/noaa.oisst.v2.highres`): subconjunto por área y fechas de `sst.day.mean.AAAA.nc`, NetCDF4 que xarray abre con h5netcdf (no hay netCDF4 ni pydap: OPeNDAP directo no sirve). **Respaldo: archivos diarios globales de NOAA NCEI** (~1.5 MB/día, `…AAAAMMDD.nc` final o `…_preliminary.nc`). Comprobado: PSL y NCEI coinciden valor a valor (final del 15/10 y preliminar del 29/10/2026: diferencia 0.0).
3. **Una ventana nativa para todo**: 65 × 70 celdas (−9.875…6.125, −95.125…−77.875) = área SOME + región Niño 1+2 oficial (0–10° S, 90–80° O, que se sale del dominio por el sur). Una sola petición sirve el mapa y el índice.
4. **Límites reales de PSL** (medidos hoy): el NCSS tarda ~0.3 s por día pedido (cualquier área); su proxy corta a los ~60 s (un año entero → HTTP 502). Limita el ritmo con **HTTP 429** (con peticiones cada 20 s, ~1 de cada 8 recibe 429). → tramos de **≤ 92 días de un mismo archivo anual** (~28 s), **pausa de 20 s tras cada petición a PSL**; ante un 429, la sincronización, `preparar_noaa` y las pruebas esperan 60, 120, 240 y 300 s y reintentan, y una **consulta no espera** (falla al instante, se sirve la caché y se avisa).
5. **Malla SOME**: cada centro SOME (múltiplo de 0.25°) es la esquina común de 4 celdas nativas: media de las válidas con **mínimo 2 de 4** (§3.3: coincide en el 99.3 % de las celdas con la máscara de Copernicus Marine; con 1 de 4, 98.7 % y la costa se corre mar adentro; con 3 de 4 se pierden celdas del golfo).
6. **Final / preliminar por día**: el listado de NCEI de cada mes dice qué días son finales (y cuándo se publicaron); PSL incorpora el final el mismo día (comprobado). Un día de PSL es **final** si NCEI publicó su final ≥ 12 h antes de la descarga; si no, **preliminar** (los últimos ~15 días). Sin listado: preliminares los últimos 15 días.
7. **Caché** `datos/noaa/` (o `SOME_RUTA_NOAA`) por mes, ya en la malla SOME, con las 40 × 40 celdas nativas de Niño 1+2, tipo (final/preliminar) y origen (PSL/NCEI) por día.
8. **Climatologías mensuales** (malla SOME): 1991-2020 **oficial** (`sst.mon.ltm.1991-2020.nc`); 1981-2010 y 1993-2022 **calculadas** con `sst.mon.mean.nc` (una petición, toda la serie desde 09/1981). OISST empieza en 09/1981: en 1981-2010 enero–agosto usan 1982-2010 → `periodo_efectivo = "1982-2010 en enero–agosto"` (como Copernicus).
9. **Índice Niño 1+2 REAL**: media de área (cos φ) de la región oficial menos la climatología **diaria** 1991-2020 de PSL (`sst.day.mean.ltm.1991-2020.nc`, 4 peticiones de ~90 días), semanas domingo–sábado etiquetadas con el miércoles (como NOAA CPC). Contrastado con `wksst9120.for` de CPC (§7).
10. **Procedencia honesta** (idéntica a Copernicus): Dataset real (`datos_reales=1`); días ausentes sin obtener → sin dato + `aviso`; ningún día real → respaldo simulado completo (`datos_reales=0`, `respaldo_simulado=1`, `aviso`); nunca se mezclan días simulados y reales en un Dataset. `SOME_NOAA_MODO=real` → ValueError (422) en vez de respaldo.
11. **Consultas**: solo piden a PSL los días AUSENTES, una petición como máximo (≤ 92 días, ~30 s) y sin esperar a otra descarga en curso (5 s); los preliminares los renueva la sincronización (cada 6 h). **Nada se cuelga**: un solo intento por servidor, 8 s para conectar y 45 s para recibir (`TIMEOUT_CONSULTA_S`); si el servidor no responde (o responde 5xx) queda en pausa al instante y las consultas siguientes sirven la caché sin esperarlo. Medido con NOAA inalcanzable y un día ausente: 17.8 s la primera consulta (PSL + respaldo NCEI) y 0 s las siguientes; con el nombre sin resolver, 1.3 s (antes de este ajuste: 151 s y 61 s).
12. **Acceso respetuoso**: ≤ 1 petición/s por servidor entre todos los procesos (turno en `.bloqueos/ritmo_<servidor>.txt`), User-Agent genérico ASCII `SOME-prototipo/0.1 (demostracion para el INOCAR)` (sin correo; sin tilde porque las cabeceras HTTP son ASCII), reintentos con espera en la sincronización y la preparación (red y 5xx: 5 y 15 s; 429: §0.4; las consultas, un solo intento: §0.11), pausa de 10 min tras 3 fallos seguidos o tras el primer fallo de una consulta (persistida; las consultas la respetan, la sincronización no).
13. **Fecha de corte = último día de la caché** (con la caché vacía y acceso, el último publicado por PSL). No se adelanta al día que PSL acaba de publicar hasta que la sincronización lo guarda: si no, entre la verificación y la descarga (20 s de pausa más la descarga; varios minutos si PSL responde 429, con el turno de descarga tomado) la fecha por defecto del sistema apuntaría a un día sin dato y el mapa de ese día saldría del respaldo simulado.
14. **Licencia y canales** (revisión del 01/11/2026; [CONDICIONES-USO-FUENTES.md](../../CONDICIONES-USO-FUENTES.md), §4).
    - **Uso:** OISST v2.1 son datos abiertos de NOAA sin restricciones de uso. El CDR Program lo declara así; en EE. UU. son de dominio público. Para los usuarios de otros países, NCEI dice que trabajará para que se aplique CC0 (NCEI-PD-10-2-02, 4.a); no hay una licencia CC0 declarada para OISST v2.1. El crédito lo pide NOAA, pero no es obligatorio.
    - **Prohibiciones:** solo tres. No insinuar aval de NOAA, no presentar datos modificados como originales de NOAA y no usar su emblema.
    - **Crédito** (corregido el 01/11/2026; `constantes.credito_para_origen`): «NOAA OISST v2.1 (Huang et al., 2020; doi:10.25921/RE9P-PT57), NOAA NCEI y CDR Program, vía NOAA PSL.».
      - Cita el conjunto con su año y reconoce al CDR Program.
      - El final dice de dónde salieron los días: «vía NOAA PSL», «vía NOAA NCEI» o «vía NOAA PSL y NOAA NCEI».
      - La `licencia` es `constantes.LICENCIA`: datos abiertos sin restricciones de uso, con las tres prohibiciones.
      - Las climatologías 1981-2010 y 1993-2022 las calcula el SOME, y su crédito lo dice (`climatologia.credito`).
      - El índice Niño 1+2 nombra el servidor de sus días y añade «Índice calculado por el INOCAR (SOME) con la climatología diaria 1991-2020 de PSL.»: no es el índice oficial de NOAA CPC.
      - Una consulta con varios Dataset diarios da un solo crédito con la suma de sus días por servidor (`motor._credito_combinado`).
      - El simulador de respaldo dice «Simulación del SOME con la forma de NOAA OISST v2.1; no son datos de NOAA.».
    - **Lago:** la historia completa se baja de NOAA Open Data Dissemination (S3 `noaa-cdr-sea-surface-temp-optimum-interpolation-pds`, us-east-1, anónimo; o Google Cloud), no de PSL. PSL queda para sus climatologías 1991-2020, descargadas una vez.
    - **NCEI:** su robots.txt excluye `/data*` para robots. El respaldo del prototipo pide listados y archivos con URL explícitas y a bajo volumen; eso es aceptable, pero no hay que rastrear directorios.

## 1. Arquitectura

### 1.1 Paquete `backend/noaa/`

| Módulo | Responsabilidad | Espejo |
|---|---|---|
| `__init__.py` | API pública (§2.1): modo, acceso, estado, resumen, firma, `llenar`, `climatologia_mensual`, `serie_indice_nino12`, `verificar`, `sincronizar`, `preparar`, `plan`, hilo. | `copernicus/__init__.py` |
| `constantes.py` | URLs, archivos, cita y DOI, User-Agent, ritmos y tiempos, geometría de la ventana, tipos y orígenes, periodos, rutas (`SOME_RUTA_NOAA`), interruptores de entorno (leídos en cada llamada), excepciones. | `copernicus/constantes.py` |
| `red.py` | Único módulo que toca la red: GET con ritmo por servidor entre procesos, pausa de 20 s tras PSL, reintentos, 429, pausa del servidor tras fallos, errores en español; URLs NCSS (datos, climatología diaria por días del año, `temporal=all`, `dataset.xml`), NCEI (listado, archivo diario), CPC. Transporte, reloj y espera reemplazables (pruebas). | `chirps/red.py` |
| `malla.py` | Abrir NetCDF4 en memoria (h5netcdf, `decode_times=False`), fechas, días del año 0001 de la climatología diaria, `ventana()` (selecciona los centros esperados uno a uno; lat asc/desc, lon 0–360 o ±180, `zlev`), `a_some()` (2 × 2, mínimo 2), región Niño 1+2, pesos y media de área; texto de procesamiento. | `chirps/malla.py` (replicada, sin depender de `chirps/constantes.py`) |
| `almacen.py` | Caché mensual (`diario/AAAA-MM.npz`), mensual nativa, climatologías, climatología diaria de Niño 1+2; reemplazo por día; escritura atómica con bloqueo entre procesos; memoria por *mtime*; marca de generación; conteos; `faltantes()`. | `copernicus/almacen.py` |
| `descarga.py` | Plan en tramos; `ejecutar_psl`, `ejecutar_ncei`; listado de NCEI → publicación; `llenar()` bajo el bloqueo `.bloqueos/descarga.lock`, con respaldo NCEI de los días recientes si PSL cae. | `copernicus/descarga.py` |
| `climatologia.py` | `preparar_mensual()` (2 peticiones), `preparar_ltm_diaria()` (4), `climatologia_mensual()`, periodo efectivo y textos de método y origen. | `copernicus/climatologia.py` |
| `indice.py` | Serie diaria y semanal del índice Niño 1+2, `resumen()` para panel y boletín, fase y semáforo, lectura de CPC y comparación. | — |
| `estado.py` | `estado.json`: servidores, pausas, publicación, historial de 14 días, verificaciones, sincronizaciones, descargas, errores; `tipo_publicado()`; `modo()`, `fecha_corte()`, `estado_actual()`, `historial_estado()`, `detalle()`; sin red, nunca lanzan. | `copernicus/estado.py` |
| `sincronizacion.py` | `verificar()` (PSL `dataset.xml` + listado NCEI), `sincronizar()`, hilo `noaa-sincronizacion` (30 s tras arrancar, luego 6 h; 30 min si falló). | `copernicus/sincronizacion.py` |
| `conector.py` | `ConectorNOAA(ConectorFuente)` real + `_SimuladorNOAA(ConectorSimulado)` (el simulador actual, idéntico). | `conectores/copernicus.py` |
| `prueba_noaa.py` | 11 bloques sin red con dobles + bloque real. | `copernicus/prueba_copernicus.py` |

Bloqueos entre procesos: `backend.chirps.bloqueo.bloqueo` (genérico). Mensajes: `chirps.estado.legible`/`acortar`.

### 1.2 Conector

`backend/noaa/conector.py::ConectorNOAA` conserva **exactamente** `id="noaa_oisst"`, `nombre="NOAA OISST v2.1"`, `nombre_corto="NOAA OISST"`, `proveedor`, `producto`, `identificador="NOAA-OISST-v2.1"`, `licencia` y la `VariableFuente` «sst» (nombre, unidad, paletas, `unidades_cf="degC"`, `decimales=1`) del conector simulado actual (la prueba 10 lo compara campo a campo). `datos_reales`, `insignia`, `credito`, `referencias`, `resolucion` y `cobertura_temporal` son propiedades dinámicas (`estado.modo()`).

### 1.3 Importación circular controlada

`ConectorNOAA` hereda de `backend.conectores.base` y, tras la integración, `backend/conectores/noaa.py` lo reexporta. Para que ningún orden de importación deje un módulo a medio cargar, `backend/noaa/__init__.py` importa primero `backend.conectores`. La prueba 11 lo verifica en subprocesos con un gancho que sustituye `conectores/noaa.py` por la reexportación (catálogo primero, conector primero, paquete primero).

## 2. Interfaces

### 2.1 API pública de `backend.noaa` (sin red salvo donde se indica)

```python
modo() -> "real" | "simulado"          # simulado solo con SOME_NOAA_MODO=simulado o sin red ni caché
modo_configurado() -> "auto" | "simulado" | "real"
sin_red() / sin_sincronizacion() -> bool
acceso_disponible() -> bool            # red permitida, modo no simulado, PSL o NCEI fuera de pausa
motivo_sin_acceso() -> str             # "" o frase corta en español
fecha_corte() -> date | None           # último día de la caché (vacía y con acceso: el último publicado por PSL)
# estado.ultimo_publicado() -> date | None: último día publicado por PSL según la última verificación (plan, avisos)
estado_actual() -> {"estado", "mensaje", "latencia_ms", "ultimo_chequeo", "fecha_corte", "definitivo", "modo"}
historial_estado(dias=14) -> [{"fecha", "estado", "origen", "origen_texto"}]   # origen: psl+ncei|psl|ncei|cache|None
detalle_estado() -> {"modo_sin_red", "noaa": {...}}        # §2.4
resumen() -> {...}                     # tarjeta de Administración (§2.4)
firma_cache() -> str                   # cambia con cada escritura, modo, acceso y fecha de corte
llenar(inicio, fin, **kw) -> dict      # CON RED (descarga.llenar)
climatologia_mensual(periodo, llenar=False) -> ClimatologiaNOAA
serie_indice_nino12(inicio, fin) -> dict   # desde la caché (indice.resumen); ErrorSinDatos si no se puede
verificar() -> dict                    # CON RED, nunca lanza
sincronizar(completo=True) -> dict     # CON RED, nunca lanza
plan(desde, hasta) -> dict             # sin red (estimación)
preparar(desde, hasta, diario, climatologias, ltm_diaria, progreso) -> dict   # CON RED
iniciar_sincronizacion() / detener_sincronizacion() / sincronizando() / proxima_sincronizacion()
# Excepciones: ErrorNOAA ⊃ ErrorConexion, ErrorNoExiste, ErrorFormato, ErrorDescargaEnCurso, ErrorSinDatos
```

### 2.2 `ConectorNOAA`

```python
obtener(p: ParametrosConsulta) -> xr.Dataset            # (time, lat, lon), variable "sst"
climatologia(variable, periodo_referencia, bbox, profundidad=None, *, simulada=False) -> xr.Dataset   # (month, lat, lon)
estado() -> EstadoFuente                                 # nunca lanza
historial_estado(dias=14) -> list[dict]
detalle_estado() -> dict                                 # bloque "noaa" (no usa "caminos" ni "copernicus")
serie_indice_nino12(inicio=None, fin=None) -> dict       # índice semanal real o respaldo simulado señalado
fecha_corte_efectiva() -> date | None                    # staticmethod
```

- Fechas acotadas a `[cfg.FECHA_INICIO, corte]`; vacío → ValueError (mismo texto que hoy). `profundidad` se ignora (2D).
- `estado().fecha_corte`: real → `fecha_corte_efectiva()` = último día de la caché (con la caché vacía, el último publicado por PSL; si aún no se sabe nada, ayer; ≥ FECHA_INICIO); simulado → 27/10/2026.

### 2.3 Atributos de los Dataset

`obtener()` real: `Conventions`, `title` («Temperatura superficial del mar — NOAA OISST v2.1»), `source`, `institution`, `references` (URL del DOI), `history`, `comment` = `insignia` = **«Datos reales · NOAA OISST»** (`cfg.INSIGNIA_REALES_NOAA` si existe), `datos_reales=1`, `procedencia="real"`, `credito` = `credito_para_origen(dias_origen_psl, dias_origen_ncei)`: «NOAA OISST v2.1 (Huang et al., 2020; doi:10.25921/RE9P-PT57), NOAA NCEI y CDR Program, vía NOAA PSL.», con «vía NOAA NCEI» o «vía NOAA PSL y NOAA NCEI» según el origen de los días (§0.14; hasta el 01/11/2026 citaba Huang et al., 2021, y decía «Dominio público»), `identificador`, `licencia`, `fuente_id="noaa_oisst"`, `fecha_corte`, `resolucion`, `producto`, `doi`, `procesamiento` (… «Días de esta consulta: N finales y M preliminares.»), `origen_datos` («NOAA OISST v2.1: 16 días finales y 14 preliminares[; K de ellos de NOAA NCEI (respaldo)]»), `dias_finales`, `dias_preliminares`, `dias_faltantes`, `dias_origen_psl`, `dias_origen_ncei`, `cobertura`, y si hay preliminares `periodo_preliminar` («aaaa-mm-dd/aaaa-mm-dd») y `nota_preliminares`; si faltan días, `aviso`. Variable: `standard_name`, `long_name`, `units="degC"`, `unidad_mostrada`, `coordinates`, `comment`, `cell_methods = "time: mean (diario) area: mean (2 × 2 celdas nativas de 0.25°)"`.

Respaldo: el Dataset del simulador + `datos_reales=0`, `procedencia="simulado"`, `insignia=cfg.INSIGNIA_SIMULADOS`, `respaldo_simulado=1`, `aviso` («NOAA OISST no está disponible: {motivo}. Esta consulta usa datos SIMULADOS para demostración.»).

`climatologia()` real: `title` («Climatología mensual {efectivo} — …»), `periodo_referencia` (pedido), **`periodo_efectivo`** («1991-2020», «1993-2022» o «1982-2010 en enero–agosto»), `periodo_efectivo_por_mes` (12 «AAAA-AAAA»), `anios_por_mes`, `metodo`, `origen_climatologia`, `credito` (`climatologia.credito`: «Climatología 1991-2020 de NOAA PSL (OISST, doi:10.25921/RE9P-PT57).» o «Climatología <periodo> calculada por el INOCAR con OISST (doi:10.25921/RE9P-PT57).»), `fuente_climatologia` (`sst.mon.ltm.1991-2020.nc` | `sst.mon.mean.nc`), procedencia real. Respaldo: la del simulador + `aviso` «Climatología simulada: …», `respaldo_simulado=1`.

`serie_indice_nino12()` real: `fechas` (miércoles ISO), `valores` (anomalía semanal, 2 decimales; None si la semana tiene < 4 días con dato), `sst`, `dias`, `parcial`, `preliminar` (listas), `actual`, `sst_actual`, `fecha_actual` (miércoles), `semana_actual` ({desde, hasta, dias, parcial, preliminar}), `fase`, `semaforo` (rojo ≥ +0.5, ámbar ≤ −0.5, verde), `metodo`, `dias_finales`, `dias_preliminares`, `dias_faltantes`, `datos_reales=True`, `procedencia="real"`, `insignia`, `credito`, `fecha_corte`, `origen_datos`, `aviso` (si faltan días). Respaldo/simulado: `fechas`, `valores`, `actual`, `fecha_actual`, `fase`, `semaforo`, `metodo`, `datos_reales=False`, `procedencia="simulado"`, `insignia`, `credito`, `fecha_corte` y, si es respaldo, `aviso` y `respaldo_simulado=True`.

### 2.4 Estado, detalle y resumen

`detalle_estado()` → `{"modo_sin_red": bool, "noaa": {"modo", "modo_configurado", "texto_modo", "acceso": {"disponible", "motivo"}, "servidores": {"psl": S, "ncei": S}, "publicacion": {"ultimo_dia_psl", "ultimo_final", "ultimo_preliminar", "consultado_psl", "consultado_ncei", "texto"}, "climatologia": {"periodos_en_cache", "total": 3, "ltm_diaria_nino12", "fuente"}, "cache": {"fecha_corte", "ultimo_final", "dias_finales", "dias_preliminares", "dias_faltantes", "dias_por_origen", "tamano_bytes"}}}` con `S = {"estado": operativa|degradada|sin_conexion|sin_registro|pausa|sin_red|simulado, "texto_estado", "detalle", "latencia_ms", "ultimo_contacto", "servidor"}`.

`resumen()` → `fuente`, `datos_reales`, `modo`, `modo_configurado`, `modo_sin_red`, `estado`, `mensaje_estado`, `sincronizacion_activa`, `fecha_corte`, `servidores`, `publicacion`, `dias_en_cache`, `dias_finales`, `dias_preliminares`, `dias_faltantes`, `dias_por_origen`, `ultimo_final`, `primer_preliminar`, `ultimo_preliminar`, `climatologia`, `conjunto_sincronizado`, `tamano_cache_bytes`, `bytes_descargados`, `peticiones`, `ultima_sincronizacion`, `resultado_ultima_sincronizacion`, `ultima_verificacion`, `proxima_sincronizacion`, `errores_recientes`.

Estados RF-21 (`estado_actual`): sin red → «sin_conexion»; sin verificar → «degradada» provisional; PSL y NCEI caídos → «sin_conexion»; PSL caído y NCEI no → «degradada» (respaldo NCEI); PSL limitó con 429 en la última hora, latencia ≥ 8 s, verificación de más de 13 h, NCEI caído (final/preliminar estimado), último día de PSL anterior a hoy − 3 o errores en la última sincronización → «degradada»; si no, «operativa». Modo simulado forzado → el estado simulado anterior (operativa, 820 ms, 27/10/2026). Mensajes en español con la caché y lo publicado («Último día publicado por PSL: 29/10/2026; finales hasta el 15/10/2026; preliminares hasta el 29/10/2026.»).

## 3. Flujo de datos y reglas

1. **Archivos de PSL** (comprobados): `sst.day.mean.AAAA.nc` (hasta el día anterior), `sst.mon.mean.nc` (09/1981–09/2026), `sst.mon.ltm.1991-2020.nc`, `sst.day.mean.ltm.1991-2020.nc` (365 días del año 0001; en su calendario «standard» el 0001-01-01 es −657073 días desde 1800-01-01, no el gregoriano proléptico). Hay también `sst.day.anom.AAAA.nc` (anomalía oficial) que el SOME no necesita.
2. **Recorte NCSS**: `north=6.2&south=-9.95&west=264.8&east=282.2&accept=netcdf4` (el NCSS devuelve las celdas que tocan el recuadro: se piden bordes dentro de las celdas extremas y luego `ventana()` toma los 65 × 70 centros esperados o lanza ErrorFormato).
3. **Malla y costa**: 2 de 4. Celdas de mar por recorte (29/10/2026): área 3 066 (Copernicus 3 043), Niño 1+2 954 (944), golfo de Guayaquil 40 (37), Guayas 12 (9), El Oro 4 (4), Manabí 26 (22); el rectángulo de tierra de la prueba de humo (−79.5…−78.5, −2.5…−1.5) queda sin celdas (422 como hoy).
4. **Caché**: `diario/AAAA-MM.npz` (`valores` 49 × 69, `nino12` 40 × 40, `tipo`, `origen`, `obtenido`), `mensual/sst_mon_mean.npz`, `climatologia/sst_<periodo>.npz`, `climatologia/ltm_diaria_nino12.npz`, `estado.json`, `generacion`, `.bloqueos/`. Reemplazo: final > preliminar > ausente; un preliminar más reciente reemplaza a otro; un día sin celdas válidas no se guarda.
5. **Consulta** (`obtener`): (1) si hay días AUSENTES y acceso, una petición a PSL (≤ 92 días; un solo intento, 8 s/45 s; si PSL no responde, hasta 5 días recientes de NCEI) sin esperar más de 5 s el turno de descarga; (2) lectura; (3) Dataset real con conteos y aviso, o respaldo (§0.10).
6. **Sincronización** (cada 6 h): verificación; `llenar(01/10/2023 → último día de PSL)` con los preliminares (con la caché al día: 1 petición de ~15–21 días); climatologías y climatología diaria que falten. La fecha de corte avanza cuando el día nuevo ya está en la caché (§0.13).
7. **Modos y entorno**: `SOME_NOAA_MODO` (`auto` | `simulado` | `real` estricto), `SOME_NOAA_SIN_RED=1` (solo caché), `SOME_NOAA_SIN_SINCRONIZACION=1` (sin hilo), `SOME_RUTA_NOAA` (carpeta de la caché, se lee al importar).

## 4. Herramienta `backend/herramientas/preparar_noaa.py`

`python -m backend.herramientas.preparar_noaa [--verificar | --estimar] [--solo-diario | --climatologia | --indice] [--desde aaaa-mm-dd] [--hasta aaaa-mm-dd]`. Verifica, muestra el plan (peticiones, MiB, minutos), descarga solo lo que falta con barra de progreso y termina con el índice Niño 1+2 de las últimas 8 semanas junto al de NOAA CPC. Salida: 0 bien; 1 errores; 2 sin red o modo simulado. **Para la demo**: ejecutarla esta noche sobre `datos/noaa/` (sin `SOME_RUTA_NOAA`), con el servidor del 8050 detenido o sin otro proceso descargando de PSL. Atajo: si ya hay una caché completa y verificada (la de las pruebas del 30/10/2026, §7.1), copiar su contenido (`diario/`, `mensual/`, `climatologia/`, `estado.json`, `generacion`) a `datos/noaa/` y ejecutar después la herramienta: solo pedirá los días nuevos y renovará los preliminares (~1–2 min en vez de ~20).

## 5. Integración: cambios exactos en archivos existentes (otro paso)

**`backend/conectores/noaa.py`** — reemplazar el cuerpo por la reexportación:
```python
"""Conector de NOAA OISST v2.1: el real de backend/noaa/conector.py (caché local, NOAA PSL y NOAA NCEI) con el
simulador anterior como respaldo señalado."""
from ..noaa.conector import ConectorNOAA, texto_origen  # noqa: F401
```
`crear_conectores()` no cambia. Docstrings de `conectores/__init__.py` y `conectores/base.py`: NOAA OISST es real.

**`backend/configuracion.py`**
- `INSIGNIA_REALES_NOAA = "Datos reales · NOAA OISST"`.
- `FECHA_CORTE_SIMULADA = date(2026, 10, 27)` y `FECHA_CORTE = FECHA_CORTE_SIMULADA` (compatibilidad: sigue siendo la del simulador; la del sistema pasa a ser dinámica, ver catálogo).
- Cabecera con las tres fuentes reales (`datos_simulados=false`): `INSIGNIA_CABECERA_REALES = "Datos reales: NOAA OISST, Copernicus Marine y CHIRPS v3"`, `INSIGNIA_CABECERA_REALES_CORTA = "Datos reales"`, `TITULO_CABECERA_REALES = "La temperatura superficial del mar (NOAA OISST v2.1, NOAA/NCEI), las variables físicas del océano (análisis diario de Copernicus Marine, E.U. Copernicus Marine Service Information) y la precipitación (CHIRPS v3, Climate Hazards Center, UC Santa Barbara) son datos reales; el índice Niño 1+2 se calcula con NOAA OISST en la región oficial. Los últimos ~15 días de NOAA OISST son preliminares. Si una fuente no responde y su caché local no tiene el dato, la respuesta usa el simulador y lo señala."`
- Mezclas: `INSIGNIA_CABECERA_NOAA = "Demostración: NOAA OISST y CHIRPS v3 con datos reales; Copernicus Marine simulado"` (+ `_CORTA = "Real: NOAA y CHIRPS"`, `TITULO_…`). Corregir `TITULO_CABECERA_COPERNICUS` / `INSIGNIA_CABECERA_COPERNICUS` (hoy dicen «NOAA OISST simulado»: solo valen con NOAA en modo simulado) y el comentario de procedencia.

**`backend/catalogo.py`**
- `fecha_corte_sistema() -> date`: `date.fromisoformat(conector("noaa_oisst").estado().fecha_corte)` si NOAA es real (= último día de la caché de NOAA: siempre tiene dato, §0.13); si no, `cfg.FECHA_CORTE_SIMULADA` (nunca lanza). `catalogo_json()["fecha_corte"] = fecha_corte_sistema().isoformat()`.
- `noaa_real()` (como `copernicus_real()`); `insignias_cabecera()`: procedencia «real» (las tres reales) → `INSIGNIA_CABECERA_REALES`/`_CORTA`/`TITULO_CABECERA_REALES`; «mixto»: Copernicus real y NOAA no → textos COPERNICUS actuales; NOAA real y Copernicus no → textos NOAA; ninguno → los de hoy. `datos_simulados` ya sale `False` solo (procedencia «real»).
- Docstrings de `estado_fuentes()`/`fuentes_reales()`: NOAA añade el bloque `noaa`. `_HISTORIAL_SIMULADO["noaa_oisst"]` se queda (no se usa: el conector tiene `historial_estado`).

**`backend/app.py`**
- Importación tolerante `from . import noaa as _noaa` (como `_copernicus`), `_sincronizacion_noaa("iniciar_sincronizacion")` al arrancar y `("detener_sincronizacion")` al cerrar; `_texto_noaa()` para la consola («NOAA OISST: datos reales hasta el 29/10/2026 (preliminares desde el 16/10/2026)…»).
- `_encolar_trabajos_iniciales()`: `cfg.FECHA_CORTE` → `catalogo.fecha_corte_sistema()`.
- `description` de la API: NOAA OISST real (NOAA PSL/NCEI, caché local, respaldo simulado señalado).

**`backend/cache.py`**: `firma_noaa()` = `backend.noaa.firma_cache()` (importación tolerante) y en `consultar()`: `if "noaa_oisst" in clave: firmas["firma_noaa"] = firma_noaa()` (al pasar un preliminar a final o llegar un día nuevo, la respuesta cacheada deja de servirse).

**`backend/motor.py`**
- `serie_indice_enso(inicio=None, fin=None, paso_dias=7)`: `fin = fin or catalogo.fecha_corte_sistema()`; si el conector «noaa_oisst» tiene `serie_indice_nino12`, `r = conector.serie_indice_nino12(inicio, fin)`; devolver `fechas`, `valores` (puede traer None), `unidad "°C"`, `actual`, `fase`, `semaforo`, `texto` = [«El índice Niño 1+2 (NOAA OISST, región 0–10° S, 90–80° O) es +5.23 °C en la semana del 25/10/2026 (5 días, preliminares): condición El Niño (fase cálida).»] (con «simulado» y la fecha si `procedencia != "real"`), `metodo` = `r["metodo"]` (+ `NOTA_SIMULACION` y aviso si es simulado), `fuente` (`_info_fuente(conector)` con `datos_reales`/`procedencia`/`insignia`/`credito` de `r`), `credito`, `fecha_corte`, `semana_actual`, `sst_actual`, `datos_simulados = not r["datos_reales"]`, `procedencia`, `insignia` y `avisos=[r["aviso"]]` si lo hay. `paso_dias` se ignora con datos reales (semanas CPC).
- `nota_datos()`: la frase de días preliminares nombra al CHC; usar `nota_preliminares` del Dataset si existe: «Incluye N días con datos preliminares de NOAA OISST. Los días preliminares… (nota)»; si no, la de CHIRPS.
- `origen_datos_generico()`: con varios Dataset NOAA (p. ej. «mismo mes cada año»), sumar `dias_finales`/`dias_preliminares`/`dias_origen_ncei` por tramo de días y rehacer el texto con `backend.noaa.conector.texto_origen` (hoy solo suma los conteos NRT de Copernicus y, si no, deduplica textos iguales: «31 días finales» una vez para 4 años).
- Autodiagnóstico (`python -m backend.motor`): `cfg.FECHA_CORTE` → `catalogo.fecha_corte_sistema()`; con NOAA real, la comparación NOAA vs. Copernicus es «real» y se exige correlación alta; los rangos de SST Niño 1+2 con datos reales: 19–31 °C.
- Comentarios/docstring: NOAA ya no es simulado.

**`backend/graficos.py`**: `figura_indice_enso()` debe aceptar `None` en `valores` (semanas sin dato → NaN; `np.nanmax`/`np.nanmin` en las franjas) y el título por defecto no debe decir «simulado» (lo pasa quien llama).

**`backend/reportes.py`**
- Fechas por defecto `cfg.FECHA_CORTE` (≈ líneas 70, 89, 661, 1179, 1302) → `catalogo.fecha_corte_sistema()`.
- `_DESCRIPCION_REAL["noaa_oisst"]` = «la temperatura superficial del mar de NOAA OISST v2.1 (NOAA/NCEI, servida por NOAA PSL; los últimos ~15 días son preliminares) y el índice Niño 1+2 calculado con ella en la región oficial (0–10° S, 90–80° O)»; `_DESCRIPCION_RESPALDO`, `_DESCRIPCION_MIXTA` y `_DESCRIPCION_SIMULADA_MIXTA` para «noaa_oisst» (como Copernicus). `_DESCRIPCION_SIMULADA["noaa_oisst"]` se queda para el modo simulado.
- Boletín ERFEN: pie de la figura 4 según `enso["procedencia"]`: real → «Figura 4. Índice Niño 1+2 semanal (NOAA OISST v2.1, región 0–10° S, 90–80° O; anomalía respecto de 1991-2020; semanas centradas en el miércoles).»; simulado → el actual. El resumen ya usa `enso['texto'][0]` y `_frase_superficie_copernicus(..., sst_simulada=...)` deja de decir «la SST de NOAA OISST de este boletín es simulada» por sí solo. Actualizar los comentarios «la SST de NOAA OISST es simulada».
- `informe_variable`/`comparativo` con NOAA: nada más (la procedencia sale de los Dataset); el periodo de referencia efectivo de 1981-2010 aparece como en Copernicus.

**`backend/rutas/panel.py`**: `_indice_enso()`: `corte = catalogo.fecha_corte_sistema()`, `inicio = max(cfg.FECHA_INICIO, corte − meses·30.44 días)`, `r = motor.serie_indice_enso(inicio, corte)`; `_fecha_corte_de()` cae en `fecha_corte_sistema()` en vez de `cfg.FECHA_CORTE`. `_procedencia(r)` ya copia `avisos`.

**`backend/rutas/catalogo.py`**: `/api/salud` → `"fecha_corte": catalogo.fecha_corte_sistema().isoformat()`; docstring de `/fuentes/estado` (bloque `noaa`).

**`backend/rutas/admin.py`**: `"fecha_corte": catalogo.fecha_corte_sistema().isoformat()` y `"noaa": _resumen_noaa()` (importación tolerante de `backend.noaa`, como `_resumen_copernicus`).

**`backend/exportar.py`** (línea ~56) y **`backend/almacen.py`** (vistas de ejemplo, línea ~490): `cfg.FECHA_CORTE` → `catalogo.fecha_corte_sistema()` (importación local para no crear ciclos).

**`backend/pruebas/prueba_humo.py`** (hecho; lo que cambió y los resultados, en §9) — con NOAA real (`backend.noaa.modo() == "real"`), lo que debe esperar:
- Catálogo: `fuentes_reales` incluye «noaa_oisst»; con Copernicus real, `datos_simulados is False`, `procedencia == "real"`, `insignia == cfg.INSIGNIA_REALES` y `insignia_cabecera == cfg.INSIGNIA_CABECERA_REALES`; `fuentes_cat["noaa_oisst"]`: `datos_reales is True`, `insignia == "Datos reales · NOAA OISST"`; `fecha_corte` del catálogo y de `/api/salud` = la de NOAA (no 27/10/2026).
- Estado: `estados["noaa_oisst"]["estado"] in ("operativa", "degradada")` (o «sin_conexion» con `SOME_NOAA_SIN_RED=1`), con bloque `noaa` y sin `caminos`; historial de 14 días con `origen`.
- Mapa/serie/climatología/mismo mes de NOAA: `procedencia == "real"`, `datos_simulados is False`, `insignia == "Datos reales · NOAA OISST"`; SST de Niño 1+2 en rango físico (19–31 °C); climatología 1981-2010 con `periodo_referencia_efectivo == "1982-2010 en enero–agosto"`; «SST en Guayas» con valor (12 celdas de mar); el rectángulo de tierra sigue 422; mismo mes octubre con 2023–2026.
- Comparación NOAA vs. Copernicus: «real» con Copernicus real (correlación alta exigible); el caso «mixto» deliberado debe usar otra pareja o forzar `SOME_NOAA_MODO=simulado` en un proceso aparte.
- CSV/NetCDF de SST: insignia real, `X-SOME-Datos-Simulados: false`, `comment` real.
- Reportes: boletín ERFEN «real» con las tres fuentes reales (si no, «mixto»); comparativo de NOAA «real».
- Widget `indice_enso`: `procedencia == "real"`, `valores` semanales, `fecha_corte` = la de NOAA, `fase` coherente con `actual` (hoy El Niño).
- Sistema: `datos_simulados is False` con las tres reales; `"noaa"` en el resumen de administración.
- Correrla con la caché preparada y `SOME_NOAA_SIN_RED=1` para que sea rápida y determinista (o con red, aceptando «degradada»).

**Frontend** (`frontend/js/modulos/…`):
- `fuentes.js`: dibujar el bloque `noaa` (servidores PSL y NCEI con estado, detalle y latencia; publicación; caché con días finales/preliminares/faltantes; climatologías), como el de `copernicus`.
- `admin.js`: `tarjetaNoaa(s.noaa)` (estado, fecha de corte, días finales/preliminares, por origen, servidores, climatologías, tamaño, última sincronización, errores, y «Para llenar la caché: `python -m backend.herramientas.preparar_noaa`»).
- `acerca.js`: quitar «NOAA hasta el … (fecha de corte simulada)» → «NOAA OISST hasta el último día publicado (los últimos ~15 días son preliminares)»; insignia «Ahora» también para NOAA.
- `panel.js`: saludo «Fecha de corte: dd/mm/aaaa» (sin «de la demo»); widget del índice: eje semanal (miércoles), valores `null` como huecos, marca de semana incompleta/preliminar y el método; `consultas.js`/`mapa.js`/`series.js`: mostrar `dias_preliminares` y `periodo_preliminar` de NOAA como ya se hace con CHIRPS.
- Cabecera: los textos nuevos de la insignia llegan del catálogo.

**`README.md`**: sección NOAA OISST real (fuentes, caché, variables de entorno, `preparar_noaa`, tiempos de §7). **`requirements.txt`**: añadir `h5netcdf>=1.3` (ya instalado; xarray lo usa para abrir el NetCDF4 de PSL y NCEI). **`ejecutar.ps1`/`ejecutar.bat`**: opcional, `python -m backend.herramientas.preparar_noaa` antes de arrancar si la caché está vacía (sin correo en ninguna variable de NOAA).

## 6. Pruebas

`python -m backend.noaa.prueba_noaa` (con `SOME_NOAA_SIN_RED=1`, sin el bloque real): 1 malla, 2 plan, 3 almacén, 4 red (ritmo, 429, reintentos, pausa, 404, sin red, User-Agent), 5 final/preliminar, 6 descarga PSL y respaldo NCEI, 7 climatologías y periodo efectivo, 8 índice, 9 estado RF-21, 10 conector (contrato idéntico, procedencia, respaldo, modo estricto, bajo demanda, climatología, índice, modo simulado), 11 importación circular en subprocesos, 12 bloque real (PSL vs NCEI valor a valor, valores de control, índice frente a CPC, conector sobre la caché).

Resultado del 30/10/2026 (tras la verificación independiente, §7.1, que añadió 4 comprobaciones: consulta con un solo intento, tiempos cortos y pausa inmediata; la consulta siguiente no espera; la sincronización conserva sus tiempos; la fecha de corte no se adelanta a la caché): sin red **89 correctas, 0 fallidas** (bloque real omitido); con red **97 correctas, 0 fallidas, 0 omitidas** en 1 min 58 s (bloque real: 101 s, 5 peticiones a PSL, 1 archivo de NCEI, CPC). Una ejecución anterior falló en el bloque real porque PSL respondió tres 429 seguidos tras ~20 peticiones en 15 min: de ahí la espera de 60/120/240/300 s fuera de las consultas y que la prueba marque «OMITE» (no «FALLO») si PSL sigue limitando tras esas esperas.

## 7. Mediciones (30/10/2026, desde el equipo de la demo)

**Servidores**: `dataset.xml` de PSL 0.4–2.0 s; listado de NCEI 0.6–0.9 s; NCSS de PSL: 30 días ≈ 10 s y 0.24 MB, 92 días ≈ 28 s y 0.67 MB (un año entero: HTTP 502 a los 60 s); archivo diario de NCEI 1.5 MB en ~1 s; `sst.mon.mean.nc` entera (541 meses) 5.5 MB en ~25 s; `sst.mon.ltm.1991-2020.nc` 0.15 MB en ~5 s; climatología diaria (31 días de la región) ~10 s. PSL respondió **HTTP 429 cinco veces** en ~25 peticiones (con 20 s de pausa); todas se recuperaron con la espera de 60/120 s.

**`preparar_noaa` completa** (caché vacía en `scratchpad/noaa/cache`, `SOME_RUTA_NOAA`): verificación 3 s; SST diaria 01/10/2023–29/10/2026 = 13 peticiones, 1 125 días (1 110 finales y 15 preliminares, 0 faltantes), 8.1 MB; climatologías = 2 peticiones, 5.5 MB (1991-2020 oficial = calculada: diferencia máxima 0.000 °C); climatología diaria de Niño 1+2 = 4 peticiones, 1.4 MB. **Tiempo: 13 min 32 s** (diario y climatologías) **+ 6 min 42 s** (climatología diaria, con dos esperas por 429). **Descargado: ~15.3 MB. Caché en disco: 17.9 MB** (diario 9.9, mensual nativa 6.3, climatologías 2.0). Con la caché al día, un ciclo de sincronización es 1 `dataset.xml` + 2 listados de NCEI + 1 NCSS de ~15 días (~1 min con la pausa).

**Lecturas desde la caché** (conector, sin red): mapa del área completa de un día 14 ms; serie de 3 años del área completa (1 125 × 49 × 69) 0.54 s; primera consulta del proceso ~1.5 s.

**Valores de control del 29/10/2026** (día preliminar):

| Magnitud | Valor |
|---|---|
| SST media, región Niño 1+2 oficial (0–10° S, 90–80° O; 1 531 de 1 600 celdas nativas, pesos cos φ) | **26.51 °C** (= 26.50 comprobado con PSL y NCEI) |
| Anomalía diaria de la región oficial (climatología diaria 1991-2020 de PSL) | **+5.19 °C** (anomalía oficial `anom` de NCEI: +5.25 °C) |
| SST media, recorte Niño 1+2 del SOME (0–6° S, 90–80° O, malla SOME, 954 celdas) | **27.29 °C** (las celdas nativas de 0–6° S dan 27.29; el 27.24 del sondeo previo incluía las filas y columnas de borde que devuelve el NCSS: −6.125° y −90.125°/−79.875°) |
| Anomalía del recorte respecto de octubre 1991-2020 (climatología mensual oficial) | **+5.67 °C** (1981-2010 con periodo efectivo «1982-2010 en enero–agosto»: +5.67; 1993-2022: +5.76) |
| Rango de SST del área SOME | 25.28 – 30.22 °C |

**Índice Niño 1+2 semanal del SOME frente a NOAA CPC (`wksst9120.for`, base 1991-2020)**:

| Semana (miércoles) | SOME SST / anomalía | CPC SST / anomalía |
|---|---|---|
| 09/09/2026 | 25.25 / +4.60 | 25.2 / +4.6 |
| 16/09/2026 | 25.42 / +4.83 | 25.4 / +4.7 |
| 23/09/2026 | 25.59 / +4.89 | 25.6 / +4.9 |
| 30/09/2026 | 26.05 / +5.32 | 26.0 / +5.3 |
| 07/10/2026 | 26.11 / +5.33 | 26.1 / +5.2 |
| 14/10/2026 | 26.19 / +5.29 | 26.2 / +5.2 |
| 21/10/2026 | 26.30 / +5.17 | 26.3 / +5.2 |
| 28/10/2026 (25–29/10, 5 días, preliminar) | 26.62 / **+5.34** | — (CPC aún no la publica) |

Diferencia en las 10 semanas completas comparables: **máxima 0.13 °C, media 0.05 °C** (criterio: ≤ 0.3 °C).

### 7.1 Verificación numérica independiente (30/10/2026)

Código escrito aparte, sin importar `backend.noaa`, con descargas propias (4 archivos diarios de NCEI, listados de NCEI de septiembre y octubre, `wksst9120.for` de CPC y 3 subconjuntos NCSS de PSL), comparado con los `.npz` de la caché de prueba y con `ConectorNOAA.obtener()`/`climatologia()`/`serie_indice_nino12()` sobre una copia de esa caché (sin red).

| Comparación | Máx. \|dif\| | Media \|dif\| | Notas |
|---|---|---|---|
| Malla SOME del 29/02/2024 (final) vs. NCEI (media de las 4 nativas, ≥ 2 válidas) | 2.4e-6 °C | 6.9e-7 °C | 3 066 celdas de mar; máscara idéntica (315 sin dato = 294 con 0 válidas + 21 con 1) |
| Malla SOME del 14/10/2026 (final) | 2.4e-6 °C | 7.0e-7 °C | ídem |
| Malla SOME del 15/10/2026 (preliminar en la caché; final en NCEI) | 2.5e-6 °C | 7.1e-7 °C | el valor de PSL ya era el final |
| Malla SOME del 29/10/2026 (preliminar) | 2.4e-6 °C | 6.9e-7 °C | ídem |
| Celdas costeras (2 o 3 nativas válidas, 47) en los 4 días | 2.5e-6 °C | 7e-7 °C | misma regla y misma máscara |
| Región Niño 1+2 nativa (40 × 40, 1 531 de mar) en los 4 días | 0.0 | 0.0 | filas −9.875…−0.125, columnas −89.875…−80.125 |
| Región Niño 1+2 de la caché vs. NCSS propio de PSL, 02/08–29/10/2026 (89 días) | 0.0 | 0.0 | — |
| `ConectorNOAA.obtener()` (Dataset, por coordenadas) vs. referencia, 4 días | 2.5e-6 °C | 7e-7 °C | lat ascendente −6…6, lon −95…−78 |
| Climatología mensual (12 meses × 3 periodos) recalculada con `sst.mon.mean.nc` | 1e-5 °C | 1e-6 °C | 1991-2020 de la caché = oficial; 1981-2010: ene–ago 1982-2010 (29 años), sep–dic 1981-2010 (30) |
| Climatología diaria 1991-2020 de la región (días 214–302) vs. NCSS propio | 0.0 | 0.0 | día del año desde el calendario juliano del archivo |
| Índice semanal, 12 semanas (05/08–21/10/2026) vs. NOAA CPC (anomalía) | 0.13 °C | 0.06 °C | ninguna > 0.2; SST semanal ≤ 0.05 °C (redondeo de CPC) |
| Índice del conector vs. índice independiente | ≤ 0.005 °C | — | redondeo a 2 decimales |

Controles de alineación del 29/10/2026 (máx./media frente a la caché): media celda nativa corrida 0.35/0.045 °C; una celda SOME en latitud 0.60/0.072 °C y en longitud 0.43/0.052 °C; latitud invertida 4.31/1.59 °C; longitud con el signo cambiado 4.30/1.35 °C. La alineación usada es la única que da diferencias de 1e-6.

Anomalías del recorte 0–6° S, 90–80° O del 29/10/2026 con las climatologías recalculadas: +5.674 °C (1991-2020), +5.669 °C (1981-2010) y +5.756 °C (1993-2022), como §7.

Estados por día frente al listado de NCEI (01/09–29/10/2026): todos coinciden salvo el 15/10, final en NCEI desde el 30/10 a las 15:15 (hora del listado) y preliminar en la caché (descargado a las 17:14, < 12 h): es el margen de seguridad previsto, el valor ya es el final y la sincronización lo renueva.

Modos (carpetas aisladas): `SOME_NOAA_SIN_RED=1` sin caché → modo simulado como Copernicus (insignia «Datos simulados para demostración», fecha de corte 27/10/2026, el motivo en «Estado de fuentes»; el Dataset no lleva `aviso`, igual que Copernicus); `SOME_NOAA_MODO=real` sin caché → ValueError claro en `obtener`, `climatologia` e índice; `SOME_NOAA_MODO=simulado` → el simulador; sin red con la caché completa → real desde la caché en 0.4 s.

## 8. Riesgos y mitigación

| Riesgo | Mitigación |
|---|---|
| PSL limita con 429 o su proxy corta (502) durante la demo (medido: tras ~20 peticiones en 15 min, tres 429 seguidos; en la verificación independiente, 2 de 5 peticiones espaciadas 25 s) | Caché preparada esta noche; las consultas solo piden días ausentes (1 petición, un solo intento) y ante un 429 no esperan; la sincronización espera hasta ~12 min sin mover la fecha de corte (§0.13); pausa de 10 min tras 3 fallos (o al primer fallo de una consulta); respaldo NCEI para los días recientes; si no hay nada, respaldo simulado señalado. |
| Red del local que descarta paquetes (cortafuegos) o sin DNS | Consultas: un solo intento con 8 s para conectar y pausa inmediata (§0.11: 17.8 s la primera, 0 s las siguientes); con la caché completa, ninguna consulta toca la red. |
| Sincronización al arrancar compite con la demo | Con la caché al día, el ciclo es 1 verificación + 1 petición (~20 s), en segundo plano; la fecha de corte solo avanza cuando el día nuevo está guardado (§0.13); `SOME_NOAA_SIN_SINCRONIZACION=1` si se prefiere congelar. |
| Días preliminares cambian (≤ décimas de °C) | Se marcan (`dias_preliminares`, `periodo_preliminar`, nota) y se renuevan cada 6 h; la firma de la caché invalida respuestas. |
| Índice del SOME ≠ CPC por décimas | Se documenta el método (diario 0.25° vs semanal 1°); diferencia medida en §7. |
| Importación circular conectores ↔ noaa | §1.3 + prueba 11. |
| Fecha de corte del sistema cambia con los días | `catalogo.fecha_corte_sistema()` único punto; el frontend ya la lee del catálogo. |
| Disco | ~18 MB la caché completa (§7). |
| Preparar la caché esta noche tarda ~20 min (PSL limita el ritmo) | Lanzar `preparar_noaa` cuanto antes; se puede interrumpir y reanudar (solo pide lo que falta); `--estimar` muestra lo pendiente. |
| Una prueba con red compite con la demo por el límite de PSL o deja a PSL en pausa en el `estado.json` compartido | La prueba de humo lee NOAA solo de la caché por defecto (`SOME_NOAA_SIN_RED=1`, §9); para probar con red, usar una copia (`SOME_RUTA_NOAA`). |

## 9. Integración (30/10/2026): anotaciones

- **Prueba de humo** (`backend/pruebas/prueba_humo.py`), adaptada según §5. Lee el modo de NOAA del bloque `noaa` de `/api/fuentes/estado` (`ModoNOAA`, como `ModoCopernicus`). Con NOAA real exige: procedencia «real», insignia «Datos reales · NOAA OISST» y el DOI en el crédito (mapa del último día, anomalía de 15 días, serie de 12 meses, punto, Guayas con valores, climatología 1981-2010 con `periodo_referencia_efectivo == "1982-2010 en enero–agosto"`, «mismo mes», dos periodos, CSV con `X-SOME-Datos-Simulados: false`, NetCDF con `comment` real, comparativo y widget del índice); SST de Niño 1+2 entre 19 y 31 °C; fecha de corte del sistema (catálogo, salud y sistema) = la de NOAA = último día de su caché, y todas las fechas de NOAA relativas a ella (los nombres de los archivos exportados llevan esa fecha); NOAA frente a Copernicus «real» con correlación ≥ 0.9 y |diferencia media| < 1 °C; SST frente a precipitación «real»; boletín ERFEN «real» con la insignia «Datos reales»; widget del índice con semanas en miércoles, `fecha_corte` de NOAA, fase y semáforo coherentes con el valor y un texto sin categorías de intensidad; `datos_simulados=false` y el resumen `noaa` en el sistema. El caso «mixto» deliberado se comprueba en un subproceso con `SOME_NOAA_MODO=simulado` (`motor.consulta_comparar`, SST frente a precipitación): el interruptor es del proceso entero y forzarlo en el de la prueba afectaría a los reportes en curso. Con `SOME_NOAA_MODO=simulado` (o sin red y sin caché) vuelven las expectativas anteriores (27/10/2026, «simulado», comparación y boletín «mixtos»).
- **Cambio respecto de §5**: la prueba fija por defecto (`setdefault`) `SOME_NOAA_SIN_RED=1` y `SOME_NOAA_SIN_SINCRONIZACION=1`: lee la caché real `datos/noaa/` en solo lectura (en modo sin red el estado no escribe nada: el historial marca «sin conexión» al leerlo), sin competir con la demo por el límite de PSL ni dejar a PSL en pausa en el `estado.json` compartido. `SOME_NOAA_SIN_RED=0` prueba con red (se acepta «degradada»). Si la caché tiene días faltantes o le falta alguna climatología, las expectativas se relajan como en Copernicus sin acceso («real», «mixto» o «simulado», y avisos si no es «real»).
- **Resultados** (copias de las tres cachés con `SOME_RUTA_NOAA`, `SOME_RUTA_COPERNICUS` y `SOME_RUTA_CHIRPS`; `SOME_CHIRPS_SIN_RED=1`; Copernicus con red y acceso): todo real, 281/281 en 31 s; `SOME_NOAA_MODO=simulado`, 274/274; caché de NOAA vacía (modo simulado automático, «sin conexión»), 274/274; `SOME_COPERNICUS_MODO=simulado` (cabecera «Demostración: NOAA OISST y CHIRPS v3 con datos reales; Copernicus Marine simulado»), 277/277.
- **Valores observados con el sistema integrado** (caché del 30/10/2026): NOAA frente a Copernicus (0 m) en Niño 1+2, último año en semanas: correlación 0.9954 y diferencia media −0.26 °C. Anomalía del recorte Niño 1+2 del 15 al 29/10/2026 respecto de 1991-2020: media +5.51 °C, celdas de +3.2 a +7.1 °C (área completa: +4.01 °C de media). Anomalía mensual del recorte: −0.54 °C en 11/2025 y +5.37 °C en 10/2026; octubre de 2026 promedia 27.0 °C frente a 21.6 °C normales (2023: +2.2 °C). Boletín del 11 al 25/10/2026: «real», 13 páginas; el índice está en fase cálida desde la semana del 01/02/2026 (38 semanas seguidas).
- **Textos del sistema integrado**: el índice se nombra por el tramo de la semana («la semana del 18/10/2026 al 24/10/2026», miércoles 21/10) y la semana en curso se marca incompleta y preliminar; el periodo efectivo de 1981-2010 se escribe «1981-2010 (1982-2010 en enero–agosto)» y no «… (se pidió 1981-2010)», porque solo cambian esos meses.
- **Documentación**: README §3.3 (NOAA real: fuentes, caché, `preparar_noaa`, límite de PSL, variables, climatologías, índice), §5 (guion con el evento cálido real y «La noche anterior»), §6 (todo real), §7–§10 (estructura, conectores, RF-03 real, limitaciones); especificación §1, §2, §3, §4.2, §4.4 (contrato de NOAA, RF-03), §5, §6, §7, §8 y §9.
