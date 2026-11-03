/* ============================================================
   SOME — módulo Acerca de (#/acerca)
   Qué es el SOME, fases, alcance de la demostración (qué es real: las
   tres fuentes —la temperatura superficial del mar de NOAA OISST v2.1 y
   el índice Niño 1+2 calculado con ella, las variables físicas del
   océano del análisis diario de Copernicus Marine y la precipitación de
   CHIRPS v3— y qué es respaldo: el simulador del prototipo, señalado,
   cuando una fuente no responde y su caché local no tiene el dato),
   créditos de las fuentes, equipo T5 y cliente.
   ============================================================ */
(function () {
  'use strict';
  const SOME = window.SOME;
  const util = window.util;
  const api = window.api;

  const EQUIPO = [
    { nombre: 'Francisco Etienne Defranc Rojas', rol: 'Líder del equipo y analista de requerimientos' },
    { nombre: 'Christian David Olmedo Borbor', rol: 'Arquitecto de software' },
    { nombre: 'Niurka María Roca Chévez', rol: 'Desarrolladora y responsable de datos' },
    { nombre: 'Samuel Alejandro Villagómez Rodríguez', rol: 'Responsable de calidad y pruebas' },
  ];

  // Respaldo si el catálogo no llegó. «nombre_corto» titula la tarjeta de créditos: el nombre completo
  // se recortaba con elipsis y va en el title
  const CREDITOS_RESPALDO = [
    { nombre: 'NOAA OISST v2.1', nombre_corto: 'NOAA OISST', identificador: 'NOAA-OISST-v2.1', datos_reales: true, credito: 'NOAA OISST v2.1 (Huang et al., 2020; doi:10.25921/RE9P-PT57), NOAA NCEI y CDR Program, vía NOAA PSL.', licencia: 'Datos abiertos de NOAA sin restricciones de uso (NOAA CDR Program; dominio público en EE. UU.). No se puede insinuar aval de NOAA, presentar datos modificados como originales de NOAA ni usar su emblema.', real: 'Conectado de verdad: NOAA OISST v2.1 diario a 0.25° (NOAA/NCEI), descargado de NOAA PSL (subconjuntos NCSS de THREDDS del área y de la región Niño 1+2), con los archivos diarios de NOAA NCEI como respaldo de los días recientes; llevado a la malla de 0.25° del SOME (media de las 2 × 2 celdas nativas, con al menos 2 válidas). Los últimos ~15 días son preliminares.' },
    { nombre: 'Copernicus Marine GLOBAL_ANALYSISFORECAST_PHY_001_024', nombre_corto: 'Copernicus Marine', identificador: 'CMEMS-GLOBAL_ANALYSISFORECAST_PHY_001_024', datos_reales: true, credito: 'Generated using E.U. Copernicus Marine Service Information; análisis diario, https://doi.org/10.48670/moi-00016; climatología GLORYS12, https://doi.org/10.48670/moi-00021.', licencia: 'Licencia de uso de los productos del Copernicus Marine Service (https://marine.copernicus.eu/user-corner/service-commitments-and-licence): gratuita, mundial y perpetua; exige el crédito con los DOI, declarar las modificaciones y no sugerir aval de la UE. Sin garantía.', real: 'Conectado de verdad con el Copernicus Marine Toolbox (copernicusmarine, acceso ARCO con cuenta gratuita): análisis diario a 1/12° y 50 niveles, llevado a la malla de 0.25° (media de 3 × 3 celdas) y a los 8 niveles de 0 a 100 m (interpolación lineal); climatología oficial GLORYS12 1993-2016.' },
    { nombre: 'CHIRPS v3 (Climate Hazards Center, UCSB)', nombre_corto: 'CHIRPS v3', identificador: 'CHC-CHIRPS-v3.0', datos_reales: true, credito: 'CHIRPS v3.0, CHC, UC Santa Barbara (Funk et al., 2026; doi:10.15780/G2JQ0P), CC BY 4.0.', licencia: 'CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), según la página oficial del CHC (https://www.chc.ucsb.edu/data/chirps3), que además declara CHIRPS3 de dominio público. Al compartir hay que citar la fuente, enlazar la licencia e indicar los cambios. La réplica de dynamical.org publica el producto con los valores redondeados, con licencia CC BY 4.0 y atribución propia.', real: 'Conectado de verdad en esta demostración por dos caminos al producto del CHC: el servidor del CHC (data.chc.ucsb.edu) y la réplica pública de dynamical.org (Zarr/Icechunk, CC BY 4.0, con los valores redondeados a 8 bits de mantisa para comprimir), llevados a la malla de 0.25°.' },
  ];

  async function montar(contenedor) {
    const cat = SOME.estado.catalogo || {};
    const fuentes = Array.isArray(cat.fuentes) && cat.fuentes.length
      ? cat.fuentes.map((f) => Object.assign({}, CREDITOS_RESPALDO.find((c) => c.identificador === f.identificador) || {}, f))
      : CREDITOS_RESPALDO;
    // Modo actual de cada fuente (real con caché o acceso; si no, su simulador, señalado) y hasta qué día llega
    const delCatalogo = (id) => (Array.isArray(cat.fuentes) ? cat.fuentes : []).find((f) => f.id === id);
    const ahora = (id, nombre, hasta) => {
      const f = delCatalogo(id);
      if (!f) return '';
      const e = f.estado || null;
      return f.datos_reales === true
        ? `<p class="texto-pequeno mt-8 mb-0"><span class="insignia insignia-real">Ahora</span> ${nombre} sirve datos reales${e && e.fecha_corte ? ` hasta el ${util.fmtFecha(e.fecha_corte)} (${hasta})` : ''}.</p>`
        : `<p class="texto-pequeno mt-8 mb-0"><span class="insignia insignia-simulado">Ahora</span> ${nombre} responde con su simulador: ${util.escapar((e && e.mensaje) || 'no hay acceso ni datos en la caché local.')}</p>`;
    };
    const ahoraFuentes = [
      ahora('noaa_oisst', 'NOAA OISST', 'último día en la caché local; los últimos ~15 días son preliminares'),
      ahora('copernicus_phy', 'Copernicus Marine', 'último día de análisis servible'),
      ahora('chirps_v3', 'CHIRPS v3', 'último día en la caché local'),
    ].join('');
    const corteNoaa = (delCatalogo('noaa_oisst') || {}).estado;

    contenedor.innerHTML = `
      <div class="encabezado-pagina">
        <div>
          <h2>Acerca del SOME</h2>
          <p>Sistema de Observación del Mar Equinoccial · Prototipo demostrativo (Fase 1) · <span id="acerca-version">versión ${util.escapar(SOME.version)}</span></p>
        </div>
        ${SOME.insigniaProcedenciaHtml(Object.keys(cat).length ? cat : { procedencia: 'mixto' })}
      </div>

      <div class="cuadricula col-2 mb-16">
        <section class="tarjeta">
          <div class="tarjeta-titulo">¿Qué es el SOME?</div>
          <p>Plataforma web interna del <strong>INOCAR</strong> (Instituto Oceanográfico y Antártico de la Armada) para que los oceanógrafos de la Dirección de Oceanografía y Meteorología Marina consulten, sin programar, las variables que necesitan a diario: temperatura superficial y por profundidad, salinidad, corrientes, nivel del mar, capa de mezcla y precipitación, provenientes de <strong>NOAA</strong>, <strong>Copernicus Marine Service</strong> y <strong>CHIRPS</strong>.</p>
          <p>Produce mapas, series de tiempo, perfiles verticales, climatologías y anomalías con su periodo de referencia, comparaciones, exportaciones (CSV, PNG, NetCDF) y reportes PDF en español, como el boletín quincenal para el <strong>CN‑ERFEN</strong>. Cada usuario tiene un panel personal y puede guardar y compartir vistas.</p>
          <p class="mb-0">Todo cálculo documenta su método y periodo de referencia; toda salida lleva el crédito y el identificador de la fuente; los datos originales nunca se modifican.</p>
        </section>
        <section class="tarjeta">
          <div class="tarjeta-titulo">Fases del proyecto</div>
          <ol style="padding-left:20px">
            <li class="mb-8"><strong>Prototipo</strong> (este entregable): consulta en vivo a NOAA OISST, Copernicus Marine y CHIRPS v3 (datos reales), con el mismo contrato de conector para las tres; el simulador queda solo como respaldo señalado. Datos recientes (hasta 3 años), visualización, anomalías, índice Niño 1+2, reportes y personalización. Su validación con los oceanógrafos da luz verde a la fase siguiente.</li>
            <li class="mb-8"><strong>Lago de datos</strong>: ingesta histórica programada e incremental (desde 1981 para OISST y CHIRPS, desde 1993 para GLORYS12) en el centro de datos del INOCAR, catálogo STAC y archivos propios.</li>
            <li class="mb-0"><strong>Escalamiento</strong>: otros departamentos, alertas y reportes programados, aplicación móvil o PWA y validación de los datos de las boyas del INOCAR frente a datos satelitales.</li>
          </ol>
          <p class="texto-pequeno texto-2 mt-12 mb-0">Fuera del alcance: modelos numéricos propios, alerta de tsunamis, telemetría de boyas, tablas de mareas, portal público de datos abiertos.</p>
        </section>
      </div>

      <section class="tarjeta mb-16">
        <div class="tarjeta-titulo">Qué es real y qué es respaldo en esta demostración</div>
        <div class="cuadricula col-2">
          <div>
            <h4 style="color:var(--exito-texto)">${SOME.icono('ok')} Real</h4>
            <ul class="mb-0">
              <li>La interfaz completa, los flujos de trabajo y la navegación.</li>
              <li>La API REST, la autenticación por roles, el panel personal, las vistas, la auditoría y la cola de trabajos.</li>
              <li>El motor de cálculo: recortes, promedios, climatologías, anomalías, series, perfiles, comparaciones y resúmenes en lenguaje sencillo.</li>
              <li>La generación de reportes PDF y las exportaciones CSV, PNG y NetCDF (con metadatos CF).</li>
              <li>El contrato de los conectores: las tres fuentes usan conectores reales con el mismo contrato que el simulado.</li>
              <li><span class="insignia insignia-real">Datos reales · NOAA OISST</span> La <strong>temperatura superficial del mar</strong> diaria proviene de NOAA OISST v2.1 (NOAA NCEI y CDR Program; Huang et al., 2020; doi:10.25921/RE9P-PT57): el servidor descarga de <strong>NOAA PSL</strong> (THREDDS) solo el área y la región Niño 1+2, con los archivos diarios de <strong>NOAA NCEI</strong> como respaldo de los días recientes, lo lleva a la malla de 0.25° (media de las 2 × 2 celdas nativas, con al menos 2 válidas) y lo guarda en caché. El listado de NCEI dice qué días son <strong>finales</strong> y cuáles <strong>preliminares</strong> (los últimos ~15): los preliminares se señalan y la sincronización los reemplaza por los finales. Las anomalías usan la climatología mensual 1991-2020 oficial de PSL, o 1981-2010 y 1993-2022 calculadas con sus medias mensuales (OISST empieza en septiembre de 1981: en 1981-2010, enero a agosto usan 1982-2010 y la respuesta lo dice). El <strong>índice Niño 1+2</strong> es la anomalía media (pesos según el coseno de la latitud) de la región oficial (0–10° S, 90–80° O) respecto de la climatología diaria 1991-2020 de PSL, en semanas de domingo a sábado como las de NOAA CPC; las semanas con días preliminares se marcan.</li>
              <li><span class="insignia insignia-real">Datos reales · CHIRPS v3</span> La <strong>precipitación</strong> diaria proviene de CHIRPS v3 (Climate Hazards Center, UC Santa Barbara; Funk et al., 2026): el servidor obtiene los diarios finales (disgregación con ERA5) y, para los días más recientes, los preliminares (disgregación con IMERG), los lleva a la malla de 0.25° y los guarda en caché. Hay dos caminos al producto del CHC: su servidor (leído por rangos HTTP, con acceso cortés: una petición por segundo y un archivo a la vez) y la réplica pública de <strong>dynamical.org</strong> (CC BY 4.0, con los valores redondeados a 8 bits de mantisa para comprimir; tercero sin garantía de servicio), que se usa para todo lo que cubre y cuando el CHC no responde. Cada día guarda de dónde salió y las figuras, exportaciones y reportes citan la réplica cuando se usó. La climatología mensual (1981-2022) se calcula, año por año, con la réplica de dynamical.org o, si no, con el NetCDF mensual del CHC; cada respuesta dice cuál se usó. Los días preliminares se señalan y se reemplazan por los finales cuando se publican. CHIRPS solo tiene datos sobre tierra.</li>
              <li><span class="insignia insignia-real">Datos reales · Copernicus Marine</span> La <strong>temperatura y la salinidad por profundidad, las corrientes, el nivel del mar y la capa de mezcla</strong> provienen del análisis diario de Copernicus Marine (GLOBAL_ANALYSISFORECAST_PHY_001_024; Generated using E.U. Copernicus Marine Service Information), leído con el Copernicus Marine Toolbox oficial solo en el área, los días y los niveles necesarios. Se lleva de 1/12° a la malla de 0.25° (media de las 3 × 3 celdas nativas, con al menos 5 válidas) y a los niveles de 0 a 100 m (interpolación lineal entre los dos niveles vecinos del modelo; 0 m = nivel de 0.494 m), y se guarda en una caché local. Solo se sirve el <strong>análisis</strong> (el último día es el del boletín semanal menos dos días); el pronóstico nunca se presenta como dato observado. Los días de análisis «NRT» se señalan: Mercator los reemplaza la semana siguiente. Las anomalías usan la climatología mensual oficial <strong>GLORYS12</strong> (GLOBAL_MULTIYEAR_PHY_001_030), cuyo periodo real es 1993-2016: si se pide 1991-2020 u otro periodo, la respuesta, el método, el CSV y el PDF lo dicen, junto con la diferencia entre reanálisis y análisis operativo.</li>
              <li>El estado de las tres fuentes en «Estado de fuentes», con historiales de verificaciones reales: en NOAA OISST, NOAA PSL y NOAA NCEI con su latencia, lo publicado (finales y preliminares) y lo que sirve la caché; en Copernicus, credenciales, latencia de una lectura de prueba, último día de análisis y caché; en CHIRPS, cada camino (servidor del CHC y réplica).</li>
            </ul>
            ${ahoraFuentes}
          </div>
          <div>
            <h4 style="color:var(--acento-texto)">${SOME.icono('advertencia')} Respaldo y simplificaciones</h4>
            <ul class="mb-0">
              <li><strong>Respaldo simulado</strong>: si una fuente no responde (sin red; en NOAA, PSL limitando el ritmo de peticiones; en Copernicus, sin credenciales «copernicusmarine login» o sin el paquete del toolbox), se sirve lo que haya en su caché local; lo que no esté en ella se responde con el generador determinista del prototipo (climatología analítica realista, eventos El Niño/La Niña y ruido reproducible), con la insignia de datos simulados y un aviso que dice por qué. Con las cachés preparadas para la demostración, el respaldo no debería intervenir.</li>
              <li>Una consulta nunca mezcla días simulados con reales de una misma fuente: en «Mismo mes cada año», los años sin dato real se omiten y se avisa cuáles; si un resultado combina procedencias (p. ej., datos reales con una climatología simulada), lleva la insignia «Datos mixtos» y un aviso.</li>
              <li>Malla única de 0.25° para las tres fuentes (NOAA OISST se promedia de 2 × 2 celdas nativas de 0.25° desplazadas; Copernicus se lee a 1/12° y se promedia; CHIRPS se agrega desde 0.05°).</li>
              <li>Periodo: diario desde el ${util.fmtFecha(cat.fecha_inicio || '2023-10-01')}; NOAA OISST hasta el último día publicado que tenga la caché local (los últimos ~15 días son preliminares)${corteNoaa && corteNoaa.fecha_corte ? `, hoy el ${util.fmtFecha(corteNoaa.fecha_corte)}` : ''}; CHIRPS hasta el último día publicado que tenga la caché local; Copernicus hasta el último día de análisis. La fecha de corte del sistema es la de NOAA OISST${cat.fecha_corte ? ` (${util.fmtFecha(cat.fecha_corte)})` : ''}.</li>
              <li>Área de estudio: 6° N a 6° S, 95° O a 78° O. En mapas y series la región Niño 1+2 se recorta al área (0–6° S); el índice Niño 1+2 usa la región oficial completa (0–10° S, 90–80° O).</li>
              <li>La persistencia en archivos JSON (en el producto real: PostgreSQL/PostGIS).</li>
            </ul>
          </div>
        </div>
        <p class="texto-pequeno texto-2 mt-12 mb-0">Cada gráfico, mapa, exportación y reporte lleva su insignia: <span class="insignia insignia-real">Datos reales · NOAA OISST</span>, <span class="insignia insignia-real">Datos reales · Copernicus Marine</span> y <span class="insignia insignia-real">Datos reales · CHIRPS v3</span> en verde (<span class="insignia insignia-real">Datos reales</span> si combina varias fuentes reales, como la comparación de NOAA con Copernicus o el boletín ERFEN), <span class="insignia insignia-simulado">Datos simulados para demostración</span> en ámbar si respondió el simulador de respaldo y <span class="insignia insignia-mixto">Datos mixtos</span> en ámbar cuando un resultado combina ambas procedencias.</p>
        <p class="texto-pequeno texto-2 mt-12 mb-0"><strong>Pendiente para el producto real</strong> (en el CONOPS, fuera de esta demostración): vectores de viento (RF‑19; el catálogo de la demo solo trae corrientes), consulta por lista de coordenadas (RF‑02; el constructor ofrece recorte, rectángulo y punto) y el formato ORM de los oceanógrafos, cuya estructura está por confirmar.</p>
      </section>

      <section class="tarjeta mb-16">
        <div class="tarjeta-titulo">Fuentes de datos y créditos <small>Obligatorios en toda salida (RF‑12)</small></div>
        <div class="cuadricula col-3">
          ${fuentes.map((f) => `
            <div style="min-width:0">
              <h4 class="recortar" title="${util.escapar(f.nombre)}">${util.escapar(f.nombre_corto || f.nombre)}</h4>
              <p class="mb-8"><span class="insignia ${f.datos_reales ? 'insignia-real' : 'insignia-simulado'}">${f.datos_reales ? 'Datos reales' : 'Datos simulados'}</span></p>
              <p class="texto-pequeno mb-8"><code>${util.escapar(f.identificador || '')}</code></p>
              <p class="texto-2 mb-8">${util.escapar(f.credito || '')}</p>
              ${f.licencia ? `<p class="texto-pequeno texto-2 mb-8">Licencia: ${util.escapar(f.licencia)}</p>` : ''}
              ${f.real ? `<p class="texto-pequeno texto-2 mb-0">${f.datos_reales ? '' : 'Con acceso o caché local (ahora responde el simulador): '}${util.escapar(f.real)}</p>` : (f.resolucion ? `<p class="texto-pequeno texto-2 mb-0">Resolución en la demo: ${util.escapar(f.resolucion)}</p>` : '')}
            </div>`).join('')}
        </div>
        <p class="texto-pequeno texto-2 mt-12 mb-0">Datos procesados por el INOCAR con el SOME (recorte, malla común de 0.25°, niveles de profundidad, climatologías y anomalías): no son datos originales de los proveedores ni cuentan con su aval, y los proveedores los entregan sin garantía. Los créditos van en cada figura, exportación y reporte; las licencias, en las exportaciones CSV y NetCDF, en la sección «Fuentes y métodos» de los reportes y en esta página. Las figuras con CHIRPS enlazan además su licencia CC BY 4.0 en el pie.</p>
        <p class="texto-pequeno texto-2 mt-12 mb-0">Mapas de fondo: © colaboradores de OpenStreetMap. Iconos: <a href="https://www.flaticon.com/uicons" target="_blank" rel="noopener">Uicons de Flaticon</a>. Paletas seguras para daltonismo (<em>viridis</em>, <em>cividis</em>, <em>YlGnBu</em>; anomalías con <em>PuOr</em> y <em>BrBG</em>).</p>
      </section>

      <div class="cuadricula col-2">
        <section class="tarjeta">
          <div class="tarjeta-titulo">Equipo T5 <small>ESPOL · FIEC · Ingeniería de Software I</small></div>
          <ul class="lista">
            ${EQUIPO.map((p) => `<li><span class="avatar">${util.escapar(util.iniciales(p.nombre))}</span><span class="lista-texto"><strong>${util.escapar(p.nombre)}</strong><small>${util.escapar(p.rol)}</small></span></li>`).join('')}
          </ul>
          <p class="texto-pequeno texto-2 mt-12 mb-0">Docente: Mónica Katiuska Villavicencio Cabezas.</p>
        </section>
        <section class="tarjeta">
          <div class="tarjeta-titulo">Cliente</div>
          <p><strong>INOCAR</strong> — Instituto Oceanográfico y Antártico de la Armada, Guayaquil.<br>Dirección de Oceanografía y Meteorología Marina.</p>
          <p class="mb-0">Representante del cliente: CPNV (SP) Freddy Vergara, gerente del proyecto.</p>
          <hr>
          <div class="tarjeta-titulo">Tecnología</div>
          <p class="texto-2 mb-0">Backend en Python 3.13 con FastAPI; motor con numpy, pandas, xarray y scipy; figuras con matplotlib; reportes con reportlab. NOAA OISST: NetCDF4 por subconjuntos NCSS de THREDDS (NOAA PSL) y archivos diarios de NOAA NCEI, leídos con xarray y h5netcdf. CHIRPS: GeoTIFF por rangos HTTP (servidor del CHC) y, opcionalmente, icechunk y zarr para la réplica de dynamical.org. Copernicus Marine: Copernicus Marine Toolbox (copernicusmarine) con acceso perezoso a ARCO. Interfaz en HTML, CSS y JavaScript puro con Leaflet 1.9.4, Plotly 2.35.2 e iconos Flaticon UIcons 3.0.0. Restricción del cliente: backend y motor en Python.</p>
        </section>
      </div>`;

    try {
      const salud = await api.get('/salud');
      if (salud && salud.version) {
        const v = contenedor.querySelector('#acerca-version');
        if (v) v.textContent = `versión ${salud.version}${salud.fecha_corte ? ` · corte ${util.fmtFecha(salud.fecha_corte)}` : ''}`;
      }
    } catch (e) { /* la versión local basta */ }
  }

  SOME.registrar('acerca', {
    titulo: 'Acerca de',
    icono: 'acerca',
    grupo: 'Sistema',
    montar,
  });
})();
