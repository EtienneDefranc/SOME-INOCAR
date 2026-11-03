# Condiciones de uso de las fuentes y lago local del SOME

Esta revisión, del 01/11/2026, cubre los términos de uso de Copernicus Marine, NOAA OISST v2.1, CHIRPS v3 y la réplica de dynamical.org. Responde a una pregunta: ¿va contra esos términos guardar su historia en un lago local del INOCAR para no consultar las API a cada rato ni agotar sus límites de ritmo?

- **Qué se leyó:** las licencias y políticas vigentes el 01/11/2026.
- **Copias secundarias:** algunas páginas solo se pudieron leer así.
  - La portada del repositorio del CHC, en una copia de Wayback del 04/10/2026.
  - Los reglamentos de la UE, en legislation.gov.uk y gesetze.legal, porque EUR-Lex bloqueó la lectura automática.
- **Verificación:** un verificador independiente revisó contra la fuente primaria los hallazgos de cada investigador.
  - Lo de NOAA PSL quedó sin verificar (§9).
  - Algunas precisiones añadidas al consolidar no pasaron por el verificador.
- **Notas de trabajo:** en el *scratchpad* de la sesión 846e3f5d, carpeta `licencias/`.

> **No es asesoría jurídica.** La asesoría jurídica del INOCAR debe validar dos cosas: quién acepta la licencia de Copernicus Marine en nombre de la institución (ley francesa, jurisdicción exclusiva e indemnización) y los créditos de los boletines públicos.

---

## 1. Respuesta corta

Un lago local con la historia de las tres fuentes **no va contra sus términos**.

- Las tres permiten guardar copias locales sin plazo, transformarlas y publicar productos derivados.
- Ninguna obliga a borrar.
- Los propios proveedores prevén las copias masivas:
  - Copernicus Marine declara que su Toolbox no tiene cuotas de volumen ni de ancho de banda, y ofrece una opción para sincronizar copias locales.
  - NOAA publica toda la historia de OISST en AWS y Google Cloud para las descargas masivas.
  - El CHC sugiere rsync a quien quiera replicar sus productos.

Lo que importa es **cómo se cargan** y **cómo se usan** los datos.

**Obligaciones:**
1. No sobrecargar los servidores ni eludir sus límites. Con UCSB, que aloja el CHC, es una obligación legal de sus términos de uso.
2. Dar el crédito. Copernicus y CHIRPS lo exigen; NOAA lo pide.
3. Declarar lo que el SOME modificó y no presentar los productos derivados como datos originales.
4. No sugerir que el proveedor avala el sistema ni sus boletines.
5. En Copernicus, además:
   - credenciales intransferibles;
   - registros de uso que puedan entregarse si los piden.

**Recomendaciones:**
- Cargar por los canales masivos de cada proveedor.
- Usar una cuenta de Copernicus Marine registrada por el INOCAR.

Conservar el análisis de Copernicus fuera de su ventana de dos años, borrar lo poco consultado o dejar de alimentar el lago son decisiones libres del INOCAR: ninguna licencia las impide.

---

## 2. Resumen por fuente

| | Copernicus Marine | NOAA OISST v2.1 | CHIRPS v3 (CHC) | Réplica de dynamical.org |
|---|---|---|---|---|
| **Licencia** | *Licence to use the Copernicus Marine Service Products* | Datos abiertos sin restricciones de uso. Son de dominio público en EE. UU. | CC BY 4.0. El CHC declara además CHIRPS3 de dominio público | CC BY 4.0 |
| **¿Permite el lago?** | Sí: copias para uso interno y respaldo, y obras derivadas para cualquier fin. La licencia es perpetua | Sí, sin restricciones | Sí. La licencia es irrevocable | Sí |
| **Crédito** | Obligatorio, con los DOI | Lo pide, pero no es obligatorio | Obligatorio al compartir: cita, enlace a la licencia y cambios hechos | Obligatorio, junto con el del CHC |
| **Carga histórica recomendada** | Toolbox `get` o `subset`, poca concurrencia y una sola cuenta | NOAA Open Data Dissemination (AWS S3 o Google Cloud) | rsync o FTP del CHC, o la réplica | S3 anónimo |
| **Lo que no se puede** | Compartir credenciales (SLA 2.7), omitir el crédito, sugerir aval, eludir bloqueos técnicos | Presentar derivados como originales, sugerir aval, usar el emblema de NOAA | Sobrecargar los servidores de UCSB, omitir el crédito, sugerir aval | Presentar los valores redondeados como originales del CHC |

---

## 3. Copernicus Marine Service

**Documento.** La licencia es el anexo *Licence to use the Copernicus Marine Service Products* de la página *Service Commitments and Licence*.
- Página: https://marine.copernicus.eu/user-corner/service-commitments-and-licence
- No tiene número de versión. Según los metadatos del sitio, se modificó por última vez el 03/10/2024.
- Normas aplicables:
  - el Reglamento Delegado (UE) 1159/2013, arts. 7 y 8, que la licencia invoca en la cláusula 2.3;
  - el Reglamento (UE) 2021/696, art. 53.

**Qué permite:**
- La licencia es mundial, no exclusiva, gratuita y perpetua (cláusula 2.2).
- Permite copias razonables para uso interno y para respaldo (2.2.a). Eso cubre la zona cruda, la zona curada y la copia de respaldo.
- Permite crear y distribuir obras derivadas para cualquier fin (2.2.b). Eso cubre el regrillado, el int16, las climatologías, las anomalías y los mapas de los boletines: el boletín océano-atmosférico (BOA) del INOCAR y el informe del CN-ERFEN.
- Permite redistribuir los originales por cualquier medio (2.2.c).
- Ninguna cláusula prohíbe el uso comercial, limita la redistribución, fija cuotas u obliga a borrar.

**Qué exige:**
- **Crédito** (2.3 y 2.4). Debe nombrar al originador y citar los DOI. La licencia recomienda una fórmula según el tipo de salida:
  - Obras derivadas: son casi todas las salidas del SOME, porque se regrillan a 0.25°, se interpolan o se convierten en anomalías o climatologías. Fórmula (2.4.a): «Generated using E.U. Copernicus Marine Service Information; https://doi.org/10.48670/moi-00016; https://doi.org/10.48670/moi-00021», citando solo los DOI de los productos usados.
  - Originales o recortes redistribuidos sin cambios, por ejemplo los crudos a 1/12° del lago. Fórmula (2.4.b): «E.U. Copernicus Marine Service Information; [DOI]».
  - Publicaciones. Fórmula (2.4.c): «This study has been conducted using E.U. Copernicus Marine Service Information; [DOI]».
- **Visibilidad del crédito** (2.4): debe verse claramente en la página de inicio o, como mínimo, en la página que da acceso a los productos. Ponerlo además en «Acerca de» y en cada exportación es una práctica del SOME.
- **Modificaciones y aval:** declarar las modificaciones y no dar la impresión de que la UE respalda el producto (Reglamento 1159/2013, art. 8).
- **Aceptación y personal:** el solo uso de los productos implica aceptar la licencia (aviso inicial). El INOCAR debe lograr que su personal, los estudiantes de la ESPOL y los contratistas la cumplan (2.5).
- **Registros de uso** (2.6): hay que llevarlos y entregarlos a Copernicus Marine cuando los pida con aviso razonable. La obligación pasa a quien reciba datos del INOCAR, por ejemplo las instituciones del CN-ERFEN.
- **Credenciales** (SLA 2.7): son personales del firmante e intransferibles.
- **Garantía y responsabilidad:**
  - no hay garantía (4.1);
  - Mercator Ocean International no responde por el uso (5.1);
  - el usuario indemniza al proveedor (6.3).
- **Retiro del derecho:** Mercator puede retirarlo si se incumple la licencia (6.1.a).
- **Ley:** rige la ley francesa, con jurisdicción exclusiva (7.1).
- **Revisión:** la licencia puede cambiar en cualquier momento (8.1).
- **Gratuidad:** la licencia y el SLA vigentes la garantizan por escrito hasta el 30/06/2028. Que será gratuita para siempre solo lo anuncia el Help Center.

**Ritmo y canales:**
- Según el Help Center, una cuenta no tiene cuotas de volumen ni de ancho de banda (artículo de introducción a la Toolbox, actualizado el 29/10/2026). Aun así puede haber bloqueos técnicos: el Reglamento 2021/696, art. 53, permite proteger el sistema de difusión. Este artículo se leyó en una copia no oficial.
- Para la carga histórica se recomienda:
  - `copernicusmarine get` (archivos originales) o `subset` (ARCO), por bloques de tiempo, área y profundidad;
  - `--dry-run` antes de cada lote grande;
  - entre 1 y 4 peticiones simultáneas, reintentos con espera y una sola cuenta;
  - `--netcdf-compression-level 1` en `subset`;
  - `--sync` para los incrementos, nunca `--sync-delete` sobre el archivo histórico.
- `open_dataset`, como en el prototipo, sirve solo para recortes pequeños y comprobaciones.
- MOTU, OPeNDAP, FTP y los demás servicios antiguos ya no existen. WMTS solo sirve para ver mapas.

**El análisis fuera de su ventana de dos años:**
- La licencia permite archivarlo (2.2.a; es perpetua).
- Solo se garantizan dos años en línea. Hoy el catálogo muestra datos desde el 01/11/2020, más de lo garantizado; conviene comprobarlo con `describe` antes de planificar.
- Según el PUM 001-024 (Issue 2.4, julio de 2026), los datos GLO12 desde el 05/10/2016 están disponibles bajo pedido. El PUM también remite a un artículo del Help Center sobre un archivo de análisis y pronóstico, pero no se pudo leer. Faltan las condiciones y los plazos.
- No es estrictamente irrecuperable, pero no está garantizado: conviene archivarlo ya.

**Lo que sí iría contra los términos:**
- Compartir el usuario y la contraseña con los usuarios del SOME o con terceros, o traspasar la cuenta. Por ejemplo, dejar en producción la cuenta personal de un estudiante.
- Eludir bloqueos técnicos con varias cuentas, rotación de IP o *proxies*, o perturbar el servicio con demasiada concurrencia.
  - La licencia no lo menciona de forma expresa: se apoya en el art. 53 del Reglamento 2021/696 y en el retiro del derecho de uso por incumplimiento (6.1).
  - Con una cuenta federada del Copernicus Data Space Ecosystem, la cláusula 9 de sus términos sí prohíbe abrir varias cuentas.
- Publicar boletines, PDF, PNG, CSV o NetCDF sin el crédito y los DOI, o con el crédito donde no se vea.
- Presentar datos regrillados, en int16, climatologías o anomalías como si fueran el producto original de Copernicus.
- Sugerir aval, certificación o afiliación de la UE, Copernicus o Mercator.
- Usar sus logotipos sin aceptar la licencia de marcas y sin enviar su formulario de registro. La versión vigente de esa licencia no se verificó.
- No llevar registros de uso, o negarse a entregarlos.
- Copiar textos o figuras del sitio marine.copernicus.eu sin permiso. Esto se refiere al sitio web, no a los datos.

---

## 4. NOAA OISST v2.1

**Documentos:**
- Use Agreement del NOAA Climate Data Record (CDR) Program (UseAgreement_01B-09, 2014): https://www.ncei.noaa.gov/pub/data/sds/cdr/CDRs/Sea_Surface_Temperature_Optimum_Interpolation/UseAgreement_01B-09.pdf
- Política de datos abiertos de NESDIS y NCEI, NCEI-PD-10-2-02 (14/12/2022).
- Licencia de NOAA para NOAA Open Data Dissemination (NODD) en el Registry of Open Data on AWS (13/03/2025): https://registry.opendata.aws/noaa-cdr-oceanic/
- Registro ISO 19115-2 C01606 de NCEI.
- 17 U.S.C. § 105.

**Qué permite:**
- El CDR Program declara que sus conjuntos, OISST incluido, no tienen restricciones de uso.
- Los datos son de dominio público en EE. UU. Para los usuarios de otros países, NCEI dice que trabajará para que se aplique CC0 (NCEI-PD-10-2-02, 4.a). No hay una licencia CC0 declarada para OISST v2.1.
- Por NODD se pueden usar como se quiera, sin registro.

**Qué prohíbe.** Solo tres cosas:
- insinuar aval o afiliación de NOAA;
- presentar datos modificados como datos originales de NOAA;
- usar el emblema de NOAA sin permiso.

Además, el INOCAR no debe reclamar derechos de autor ni poner una licencia restrictiva sobre los datos de NOAA: solo puede licenciar su propio trabajo derivado. Si un boletín con aviso de derechos de autor se compone sobre todo de material de NOAA, hay que identificar ese material (17 U.S.C. § 403).

**Crédito.** NOAA lo pide, pero no es obligatorio. El Use Agreement pide además reconocer al CDR Program.
- **Forma corta** para figuras y exportaciones: «NOAA OISST v2.1 (Huang et al., 2020; NOAA NCEI, doi:10.25921/RE9P-PT57), NOAA Climate Data Record Program. Datos abiertos de NOAA, sin restricciones de uso.» Después se agrega el canal: «vía NOAA PSL» o «vía NOAA Open Data Dissemination (AWS)», según de dónde venga cada día.
- **En los derivados:** «Procesado por el INOCAR (SOME); no es un dato original de NOAA ni cuenta con su aval.»
- **Cita del conjunto** según NCEI: Huang, B.; Liu, C.; Banzon, V. F.; Freeman, E.; Graham, G.; Hankins, W.; Smith, T. M.; Zhang, H.-M. (2020). *NOAA 0.25-degree Daily Optimum Interpolation Sea Surface Temperature (OISST), Version 2.1*. NOAA National Centers for Environmental Information. https://doi.org/10.25921/RE9P-PT57
- **Artículos** para documentos técnicos:
  - Huang et al. (2021), *Journal of Climate* 34, 2923-2939, doi:10.1175/JCLI-D-20-0166.1. NCEI fecha este artículo en 2020; en las figuras conviene citar siempre el conjunto con su DOI, para no confundirlos.
  - Banzon et al. (2016), *Earth System Science Data* 8, 165-176, doi:10.5194/essd-8-165-2016.
- **«Dominio público»:** sin más, solo es exacto en EE. UU. En el Ecuador conviene decir «Datos abiertos de NOAA, sin restricciones de uso».
- **Productos calculados por el SOME** (las climatologías 1981-2010 y 1993-2022, las anomalías y el índice Niño 1+2): llevan el rótulo «calculado por el INOCAR (SOME)», no solo el crédito de NOAA o PSL.
- **PSL:** pide reconocimiento a quien use sus productos. Ese texto no se pudo verificar en la fuente, porque el proyecto no hace peticiones a psl.noaa.gov: falta revisarlo a mano.

**Ritmo y canales recomendados:**
- **Historia completa:** NODD.
  - Canal principal: S3 `noaa-cdr-sea-surface-temp-optimum-interpolation-pds` (us-east-1, acceso anónimo), ruta `data/v2.1/avhrr/AAAAMM/oisst-avhrr-v02r01.AAAAMMDD[_preliminary].nc`.
  - Alternativa: el bucket anónimo de Google Cloud Storage `noaa-cdr-sea-surface-temp-optimum-interpolation`. Earth Engine no sirve como canal, porque tiene términos propios de Google.
- **Incremento diario:** NODD o el HTTPS de NCEI, con URL explícitas y una petición a la vez. El robots.txt de NCEI excluye `/data*` para robots. No es un término legal, pero no hay que rastrear directorios.
- **PSL:** solo para sus climatologías 1991-2020, descargadas una vez y con pausas.
  - PSL limita por IP con HTTP 429; lo midió el equipo.
  - Bajar 45 años desde su NCSS, teniendo NODD, no es razonable.
- **Ante un HTTP 429 o 503:** leer `Retry-After` y esperar cada vez más, sin conexiones paralelas al mismo servidor.

**Descargo.** NCEI solo certifica que lo que distribuye es una copia auténtica. No da garantía sobre los datos, y el usuario responde de su uso. Las copias del lago no deben describirse como certificadas por NOAA.

---

## 5. CHIRPS v3 (Climate Hazards Center, UC Santa Barbara)

En esta sección, los § entre paréntesis son cláusulas de CC BY 4.0, no secciones de este documento.

**Documentos:**
- Página oficial de CHIRPS v3: https://www.chc.ucsb.edu/data/chirps3
- Funk, C., Peterson, P., Harrison, L., et al. (2026), *Scientific Data* 13, 718, https://doi.org/10.1038/s41597-026-07096-4
- Repositorio de datos del CHC (2025): https://doi.org/10.15780/G2JQ0P
- CC BY 4.0: https://creativecommons.org/licenses/by/4.0/legalcode
- Términos de uso de UCSB: https://www.ucsb.edu/terms-of-use

El README-CHIRPSv3.0.txt **no trae ninguna licencia**, y el registro DataCite del repositorio tampoco declara una. La licencia está en la página oficial y en el artículo.

**Licencia.** La página oficial dice a la vez que CHIRPS3 es de dominio público y que se publica con CC BY 4.0.
- Cumplir CC BY 4.0 cubre las dos lecturas.
- CC BY 4.0 es irrevocable, y sigue vigente aunque el CHC deje de distribuir los datos (§6.c). Eso respalda que el lago conserve lo que ya descargó.
- Sin un permiso explícito, los términos de UCSB solo permiten una copia para uso personal no comercial. El lago se apoya en la licencia que declara la página chirps3, así que hay que archivarla con fecha como prueba del permiso.

**Qué permite.** Guardar, recomprimir, cambiar la malla, cuantizar y derivar productos, sin límite de tiempo y sin obligación de borrar.

**Qué exige.** Las obligaciones nacen al compartir con el público: el BOA, el informe del CN-ERFEN y los archivos que salen del INOCAR. El uso interno no las activa, pero conviene dar el crédito siempre. Al compartir hay que:
- conservar la identificación del creador y, si el licenciante los da, los avisos de licencia y de exención de garantías y la URI del material (§3.a.1.A);
- indicar los cambios y conservar los anteriores, como el redondeo de la réplica (§3.a.1.B);
- enlazar la licencia (§3.a.1.C).

Puede ser razonable cumplir con un enlace a una página de fuentes del INOCAR que tenga la cita completa, la licencia y los cambios (§3.a.2). Si el licenciante lo pide, hay que retirar la atribución (§3.a.3).

Además:
- No se puede insinuar aval del CHC ni de UCSB (§2.a.6), ni usar sus logotipos sin permiso escrito.
- No se pueden imponer términos ni medidas técnicas que restrinjan a quienes reciban los datos de CHIRPS que redistribuya el INOCAR (§2.a.5.B).
- Si el INOCAR pone una licencia propia a sus derivados, esa licencia no debe impedir que quienes los reciban cumplan CC BY 4.0 (§3.a.4).
- Un incumplimiento termina la licencia automáticamente. Se restituye si se corrige dentro de 30 días desde que se descubre (§6.a y §6.b).
- No se pueden copiar textos ni figuras de chc.ucsb.edu en los boletines (términos de UCSB).

**Crédito:**
- **Completo:** «Precipitación: CHIRPS v3.0, Climate Hazards Center, UC Santa Barbara. CHIRPS3 Data Repository, https://doi.org/10.15780/G2JQ0P (2025), datos consultados el [fecha]; Funk et al. (2026), *Scientific Data* 13, 718, https://doi.org/10.1038/s41597-026-07096-4. Licencia CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Procesado por el INOCAR (SOME): recorte y malla de 0.25° [y lo que corresponda]. No es un producto del CHC ni está avalado por él.»
- **Corto,** para el pie de los mapas: «Fuente: CHIRPS v3.0 (CHC, UC Santa Barbara; doi:10.15780/G2JQ0P; Funk et al., 2026), CC BY 4.0; procesado por el INOCAR.» Debe enlazar a la página de fuentes.

**Servidores del CHC:**
- **Obligación legal:** los términos de uso de UCSB prohíben usar sus servicios de un modo que sobrecargue o perjudique sus servidores, y prohíben el acceso no autorizado. Esos términos cambian sin aviso: hay que revisarlos con la frecuencia que indica el §7 de este documento.
- **Pedido del CHC:** que los *scripts* descarguen por FTP y no por HTTPS, y que quien quiera replicar mire su servicio rsync. HTTPS es la vía menos preferida.
- **Carga recomendada para el lago:**
  - rsync o FTP pasivo, con una conexión, de forma incremental y siguiendo el calendario de publicación, o la réplica de dynamical.org;
  - nunca pedir de nuevo lo que ya está en el lago;
  - la ruta rsync de la versión 3.0 está por confirmar, porque el ejemplo publicado es de CHIRPS 2.0.
- **Prohibido:** esquivar bloqueos rotando IP, falseando el User-Agent o repartiendo la descarga entre varios equipos.

**CHIRPS v2.** Si se guarda, tiene su propia renuncia de derechos y su propia cita (USGS DS 832). No hay que mezclarlo con la versión 3 sin rotularlo.

---

## 6. Réplica de dynamical.org

**Documentos:**
- Página del conjunto: https://dynamical.org/catalog/ucsb-chc-chirps-analysis-final/
- Licencia: https://dynamical.org/license/
- Acuerdo de servicio: https://dynamical.org/sla/

**Licencia:**
- El conjunto de datos tiene licencia CC BY 4.0.
- Los textos y figuras del sitio, como los informes de validación, tienen CC BY-NC-SA 4.0.
- Algunos conjuntos de dynamical.org llevan términos adicionales del proveedor, enlazados en su página. Hoy la de CHIRPS no enlaza ninguno, pero hay que revisarlo antes de cada carga masiva.
- AWS no es el proveedor de los datos. La lectura anónima no usa una cuenta de AWS.

**Crédito:**
- **Literal:** «UCSB Climate Hazards Center CHIRPS version 3.0 data processed by dynamical.org from the Climate Hazards Center data archive.»
- **Cita:** Funk et al. (2026), *Scientific Data* 13, 718, data https://doi.org/10.15780/G2JQ0P.
- **Forma corta aceptada:** «UCSB CHC CHIRPS from dynamical.org».
- Siempre se agregan «CC BY 4.0», el aviso de que dynamical.org redondea los valores y el crédito del CHC. En el SOME:
  - la cita completa de la réplica va en el CSV, el NetCDF y «Fuentes y métodos» del PDF;
  - el pie de las figuras lleva el crédito corto («…, vía la réplica de dynamical.org; CC BY 4.0.») y, al lado, el aviso de redondeo y el enlace a la licencia;
  - «Acerca de» explica el redondeo.

**Modificación previa:**
- La réplica redondea los valores en su representación binaria para comprimir, y publica en kg m⁻² s⁻¹.
- Su página no dice cuántos bits redondea y remite a su código, que es público. Allí, la precipitación conserva 8 bits de mantisa (`keep_mantissa_bits=8` en `src/reformatters/ucsb_chc/chirps/template_config.py` del repositorio dynamical-org/reformatters, leído el 01/11/2026). Eso da un error relativo de hasta 0.2 %.
- CC BY 4.0 obliga a conservar esa indicación (§3.a.1.B). No hay que presentar esos valores como el archivo original del CHC.

**Servicio:**
- El acceso gratuito es a mejor esfuerzo y no tiene acuerdo de servicio. El lago debe guardar su propia copia y no depender de la réplica para operar.
- Por cada día hay que registrar la versión del almacén (hoy v0.1.0) y el *snapshot* de Icechunk.

**Almacenes:**
- `s3://dynamical-ucsb-chc-chirps/ucsb-chc-chirps-analysis-final/v0.1.0.icechunk/`: final rnl desde el 01/01/1981.
- `.../ucsb-chc-chirps-analysis-preliminary/v0.1.0.icechunk/`: preliminar desde el 01/01/2025.
- Los dos están en us-west-2, con acceso anónimo.

---

## 7. Condiciones para el lago

**Ingesta:**
- [ ] Canales masivos:
  - OISST desde NODD (S3 o GCS);
  - CHIRPS por rsync o FTP del CHC, o por la réplica;
  - Copernicus con `get` o `subset`, de 1 a 4 peticiones simultáneas, en horas valle.
- [ ] Una sola cuenta de Copernicus Marine:
  - registrada por un funcionario responsable del INOCAR y custodiada por el área de tecnología como secreto;
  - la usa solo el *backend* (ingesta o consultas en vivo), nunca los usuarios del SOME;
  - la cuenta personal de un integrante del equipo no queda en el servidor del INOCAR después de la entrega.
- [ ] Ante HTTP 429 o 503: leer `Retry-After`, esperar cada vez más y pausar tras fallos seguidos.
- [ ] Nada de conexiones paralelas contra PSL, NCEI o el CHC, ni de rotar IP o cuentas.
- [ ] User-Agent con un contacto institucional del INOCAR, nunca personal. Es cortesía: ningún término publicado lo exige.

**Procedencia:**
- [ ] Por cada archivo ingerido, registrar:
  - fuente, producto, `dataset_id` y versión;
  - canal y cuenta de servicio (sin la contraseña);
  - fecha y suma de verificación;
  - si es preliminar o final.
- [ ] Por cada reporte, registrar qué productos usó. En Copernicus, este registro cumple la cláusula 2.6.
- [ ] Conservar los atributos `credit` y `licence` de los originales de Copernicus y la versión de código de OISST (v2.1, v2.1a o v2.1b).
- [ ] Volver a ingerir los últimos días que el proveedor sobrescribe: en Copernicus, 11 a 15 días; en OISST, los preliminares hasta que salgan los finales.
- [ ] Guardar en el catálogo una copia fechada de cada licencia y término de uso, en especial la página chirps3. Revisarlos al menos una vez al año y en cada versión del lago.

**Salidas:**
- [ ] Crédito de cada fuente en toda salida: pantalla, PDF, PNG, CSV, NetCDF, Zarr curado y figuras de los boletines.
- [ ] Declarar las modificaciones: «procesado por el INOCAR (SOME): …».
- [ ] No sugerir aval. Para cumplirlo, el SOME dice de forma expresa que el producto no está avalado por la UE, NOAA, el CHC ni dynamical.org. Los términos no exigen esa frase, pero es la forma más simple de cumplir.
- [ ] Una página de fuentes enlazada desde los pies, con las citas completas, las licencias (CC BY 4.0 para CHIRPS) y los cambios.
- [ ] En los NetCDF, los atributos ACDD `license` y `acknowledgement`, además de los atributos en español.
- [ ] Las exenciones de garantía de Copernicus, NOAA y NCEI en la sección «Fuentes y métodos» de los reportes.
- [ ] Solo crédito en texto: nada de logotipos de los proveedores.

**Gobierno:**
- [ ] La asesoría jurídica del INOCAR valida:
  - la aceptación de la licencia de Copernicus (ley francesa, jurisdicción exclusiva, indemnización);
  - la redacción de los créditos de los boletines.
- [ ] Confirmar por escrito con el soporte de Copernicus Marine:
  - si un servicio automático puede usar la cuenta registrada por un funcionario;
  - si hay cuentas institucionales;
  - en qué formato esperan los registros de uso.
- [ ] Confirmar por escrito con el CHC si rige la renuncia de dominio público o CC BY 4.0, y la ruta rsync de la versión 3.0.

---

## 8. Ajustes en el prototipo

Las rutas son relativas a `prototipo-some/`.

**Qué ya es obligatorio y qué no:**
- El crédito de Copernicus (2.4) es obligatorio desde ya.
- Las obligaciones de CC BY 4.0 nacen al compartir con el público. El uso interno del INOCAR no cuenta, pero se activan en cuanto el prototipo o sus figuras salen del equipo y del INOCAR: un repositorio público, una presentación o un boletín.

**Corregido el 01/11/2026** (los textos están en `docs/ESPECIFICACION-PROTOTIPO.md`, §5):
1. **Copernicus:**
   - El crédito usa la forma de obras derivadas, «Generated using E.U. Copernicus Marine Service Information; análisis diario, https://doi.org/10.48670/moi-00016.», y la climatología añade «Climatología GLORYS12, https://doi.org/10.48670/moi-00021.».
   - El motor antepone «Generated using …» sin repetirlo, y solo con datos reales.
   - El simulador de respaldo ya no se presenta como dato de Copernicus.
   - El crédito combinado mide 171 caracteres, dentro del tope de 200 de `backend/graficos.py`.
2. **NOAA:**
   - Cita el conjunto con su año (2020) y reconoce al NOAA CDR Program.
   - Las consultas y el índice Niño 1+2 dicen «vía NOAA PSL», «vía NOAA NCEI» o «vía NOAA PSL y NOAA NCEI» según de dónde salieron los días. Una consulta con varios tramos da un solo crédito. El catálogo dice «vía NOAA PSL».
   - El índice Niño 1+2 dice que lo calcula el INOCAR (SOME) con la climatología diaria de PSL.
   - El simulador de respaldo ya no se presenta como dato de NOAA.
   - La licencia ya no dice «Dominio público» sin más.
   - Las climatologías que calcula el SOME lo dicen.
3. **CHIRPS:**
   - El crédito lleva el DOI de los datos y «CC BY 4.0»; la licencia lleva su enlace.
   - El crédito de la réplica es el literal de dynamical.org, con el aviso de que redondea los valores a 8 bits de mantisa.
   - Los textos de la interfaz y de los reportes ya no llaman a la réplica «el mismo producto».
4. **Todas las fuentes:** las salidas que salen del sistema declaran que el INOCAR procesó los datos, que no son originales ni cuentan con el aval de los proveedores, y que se entregan sin garantía (`cfg.AVISO_PROCESAMIENTO`). Lo hacen:
   - el CSV, en la línea `# Procesamiento:`;
   - el NetCDF, en el atributo `aviso_procesamiento`;
   - el PDF, en «Fuentes y métodos»;
   - el pie de las figuras (PNG, reportes y pantalla), en forma corta y solo con datos reales o mixtos;
   - la pantalla «Acerca de».
5. **Licencia en las salidas:**
   - El NetCDF lleva los atributos ACDD `license` y `acknowledgement`.
   - El CSV con días de la réplica cita su atribución.
   - Las figuras llevan el crédito; con CHIRPS, además, el enlace a CC BY 4.0 en el pie.
   - El texto completo de cada licencia está en el CSV, en el NetCDF, bajo la tabla de «Fuentes y métodos» y en «Acerca de».
6. **Créditos de los reportes:** la tabla de «Fuentes y métodos» rehace el crédito de NOAA y el de CHIRPS con lo que usaron todas las consultas del documento. Las figuras del boletín llevan el crédito de su propia consulta.

**Pendiente:**

7. **Red:**
   - Ningún conector lee `Retry-After`.
   - El User-Agent de NOAA no tiene contacto.
   - El User-Agent de CHIRPS lleva el contacto que definen los lanzadores, que hoy es personal. Antes de la entrega debe cambiarse por un contacto institucional del INOCAR.
   - El conector de CHIRPS lee el CHC por rangos HTTPS, y el respaldo de NOAA lee los listados de `/data/` de NCEI. Las dos cosas son aceptables en el prototipo por su bajo volumen y sus URL explícitas, pero el lago debe usar los canales del §7.
8. **Credenciales de Copernicus:** el prototipo usa la cuenta personal de un integrante del equipo. No debe entregarse con el sistema ni quedar en el servidor del INOCAR.

---

## 9. Pendientes por confirmar

- **PSL:** la redacción vigente del reconocimiento que pide en la página de OISST. Hay que revisarla a mano en un navegador.
- **EUR-Lex:**
  - el texto oficial del Reglamento (UE) 2021/696, art. 53, y la vigencia del Reglamento Delegado 1159/2013;
  - EUR-Lex bloqueó la lectura automática, así que se usaron copias de legislation.gov.uk y gesetze.legal.
- **Copernicus:**
  - las condiciones y el plazo para pedir el análisis GLO12 anterior a la ventana de dos años;
  - si una cuenta federada del Copernicus Data Space Ecosystem sujetaría la ingesta a sus cuotas. Se recomienda una cuenta propia de Copernicus Marine;
  - la versión vigente de la licencia de marcas. Solo importa si se usan logotipos.
- **AWS:** qué términos regirían si la ingesta usara una cuenta de AWS. Con la lectura anónima solo rigen los del proveedor de los datos.
- **NOAA:**
  - si NCEI confirmaría por escrito que aplica CC0 a OISST v2.1 para usuarios fuera de EE. UU.;
  - si hay un *Use Agreement* del CDR más reciente: el de 2014 todavía habla de OISST v2 y de NCDC;
  - que la asesoría jurídica del INOCAR revise la lectura de la normativa de exportaciones de EE. UU. (EAR, 15 CFR 734) para datos públicos de NOAA.
- **Insumos de CHIRPS:** las licencias de los insumos del reparto diario (ERA5 de Copernicus C3S para rnl, IMERG de NASA para sat). La declaración del CHC cubre CHIRPS3 como producto.

---

## 10. Referencias (consultadas el 01/11/2026)

- Copernicus Marine Service. *Service Commitments and Licence* (licencia y SLA). https://marine.copernicus.eu/user-corner/service-commitments-and-licence
- Copernicus Marine Help Center. *Copernicus Marine Toolbox – Introduction*. https://help.marine.copernicus.eu/en/articles/7949409-copernicus-marine-client-introduction
- Copernicus Marine. *Product User Manual GLOBAL_ANALYSISFORECAST_PHY_001_024*, Issue 2.4. https://documentation.marine.copernicus.eu/PUM/CMEMS-GLO-PUM-001-024.pdf
- Reglamento Delegado (UE) 1159/2013, arts. 7 y 8, y Reglamento (UE) 2021/696, art. 53.
- NOAA CDR Program. *Fair Use of NOAA's CDR Data Sets* (UseAgreement_01B-09). https://www.ncei.noaa.gov/pub/data/sds/cdr/CDRs/Sea_Surface_Temperature_Optimum_Interpolation/UseAgreement_01B-09.pdf
- NOAA NESDIS/NCEI. *Open Data Policy* NCEI-PD-10-2-02 (2022). https://www.ncei.noaa.gov/sites/default/files/2023-12/NCEI%20PD-10-2-02%20-%20Open%20Data%20Policy%20Signed.pdf
- NOAA Open Data Dissemination en el Registry of Open Data on AWS. https://registry.opendata.aws/noaa-cdr-oceanic/
- NCEI. Registro ISO C01606 de OISST v2.1. https://data.noaa.gov/waf/NOAA/NESDIS/ncei/aim/iso/xml/C01606.xml
- Climate Hazards Center. *CHIRPS3*. https://www.chc.ucsb.edu/data/chirps3
- Creative Commons. *Attribution 4.0 International*. https://creativecommons.org/licenses/by/4.0/legalcode
- University of California, Santa Barbara. *Terms of Use*. https://www.ucsb.edu/terms-of-use
- dynamical.org. *UCSB CHC CHIRPS analysis, final* y *License*. https://dynamical.org/catalog/ucsb-chc-chirps-analysis-final/ y https://dynamical.org/license/
