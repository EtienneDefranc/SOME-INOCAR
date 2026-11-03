/* ============================================================
   SOME — módulo Estado de fuentes (#/fuentes)
   Semáforo, fecha de corte, latencia, historial de 14 días,
   crédito e identificador de cada fuente (RF-21, RF-12).
   Las tres fuentes tienen conexión real (estado e historial de
   verificaciones reales). NOAA OISST (bloque «noaa» del estado): NOAA PSL
   (vía principal) y NOAA NCEI (listado de días finales y preliminares;
   archivos diarios de respaldo) con su estado y latencia, lo publicado
   por NOAA, las climatologías y lo que sirve la caché local. CHIRPS v3,
   por dos caminos al producto del CHC: su servidor y la réplica de
   dynamical.org; la tarjeta muestra el estado de cada camino y hasta qué
   fecha llega cada producto. Copernicus Marine (bloque «copernicus»):
   servicio ARCO con su latencia, credenciales, paquete del toolbox, último
   día de análisis, climatología GLORYS12 y lo que sirve la caché local.
   Si una fuente no tiene acceso ni caché, sus datos son simulados y la
   tarjeta lo dice. La página se actualiza sola con la vigilancia de
   fuentes de app.js (evento «fuentes», cada minuto), sin recargarla.
   ============================================================ */
(function () {
  'use strict';
  const SOME = window.SOME;
  const util = window.util;
  const api = window.api;

  let estado = null;

  async function montar(contenedor) {
    estado = { contenedor };
    contenedor.innerHTML = `
      <div class="encabezado-pagina">
        <div>
          <h2>Estado de fuentes</h2>
          <p>Disponibilidad, fecha de corte y latencia de las fuentes externas. Se actualiza sola cada minuto.</p>
        </div>
        <div class="fila">
          <span class="texto-pequeno texto-2" id="fuentes-hora"></span>
          <button type="button" class="boton" id="fuentes-actualizar">${SOME.icono('actualizar')} Actualizar</button>
        </div>
      </div>
      <div id="fuentes-aviso"></div>
      <div id="fuentes-lista" class="cuadricula col-3"></div>
      <div class="alerta alerta-info mt-16">
        ${SOME.icono('acerca')}
        <div><strong>Cómo se evalúa el estado</strong>Las tres fuentes tienen <em>conexión real</em>: el servidor del SOME las verifica en segundo plano, mide la latencia y guarda cada verificación; el historial muestra esas verificaciones (el peor estado de cada día; los días sin ninguna aparecen como «Sin registro»).
          <ul class="fuentes-criterios">
            <li><strong>NOAA OISST v2.1</strong>: consulta NOAA PSL (THREDDS; de ahí descarga los subconjuntos del área y de la región Niño 1+2) y el listado de NOAA NCEI (qué días son finales y cuáles preliminares; sus archivos diarios son el respaldo de los días recientes). «Operativa» si ambos responden a tiempo; «Degradada» si PSL limita el ritmo de peticiones (HTTP 429), responde con retraso, no tiene días recientes o solo responde NCEI; «Sin conexión» si no responde ninguno o el modo sin red está activo. Los últimos ~15 días son preliminares: la sincronización (cada 6 horas) los reemplaza por los finales.</li>
            <li><strong>Copernicus Marine</strong>: comprueba las credenciales (guardadas con «copernicusmarine login», sin mostrarlas nunca), hace una lectura de prueba en el Copernicus Marine Data Store, mide su latencia y calcula el último día de <em>análisis</em> publicado (el pronóstico nunca se sirve como dato observado). «Operativa» si todo responde a tiempo, «Degradada» si responde con retraso o con errores y «Sin conexión» sin credenciales, sin red o sin el paquete del toolbox.</li>
            <li><strong>CHIRPS v3</strong>: dos caminos al producto del CHC, el servidor del Climate Hazards Center (data.chc.ucsb.edu, UC Santa Barbara) y la réplica pública de dynamical.org (CC BY 4.0), con hasta qué fecha publica cada uno los productos final y preliminar. «Operativa» si responde el CHC, «Degradada» si solo responde la réplica y «Sin conexión» si no responde ninguno o el modo sin red está activo.</li>
          </ul>
          Si una fuente no responde, se sirve lo que ya está en su caché local; lo que no esté en ella se responde con el simulador del prototipo, señalado como datos simulados.</div>
      </div>`;
    contenedor.querySelector('#fuentes-actualizar').addEventListener('click', cargar);
    // La vigilancia de fuentes de app.js consulta /fuentes/estado cada minuto y emite «fuentes»:
    // se vuelve a dibujar sin recargar la página (p. ej., cuando el CHC cae o vuelve).
    estado.alActualizar = (ev) => {
      if (!estado || !Array.isArray(ev.detail) || estado.cargando) return;
      render(ev.detail);
      horaConsulta();
    };
    if (SOME.eventos) SOME.eventos.addEventListener('fuentes', estado.alActualizar);
    await cargar();
  }

  function desmontar() {
    if (estado && estado.alActualizar && SOME.eventos) SOME.eventos.removeEventListener('fuentes', estado.alActualizar);
    estado = null;
  }

  function horaConsulta() {
    const nodo = estado && estado.contenedor.querySelector('#fuentes-hora');
    if (nodo) nodo.textContent = `Consultado a las ${util.fmtFechaHora(new Date()).slice(11)}`;
  }

  async function cargar() {
    if (!estado) return;
    const zona = estado.contenedor.querySelector('#fuentes-lista');
    zona.innerHTML = [1, 2, 3].map(() => '<div class="tarjeta"><div class="esqueleto esqueleto-linea" style="width:60%"></div><div class="esqueleto esqueleto-linea" style="width:40%"></div><div class="esqueleto esqueleto-bloque mt-12"></div></div>').join('');
    estado.cargando = true;
    try {
      const lista = await api.get('/fuentes/estado');
      if (!estado) return;
      const items = Array.isArray(lista) ? lista : (lista && Array.isArray(lista.fuentes) ? lista.fuentes : []);
      render(items);
      estado.cargando = false;
      SOME.actualizarIndicadorFuentes(items);
      horaConsulta();
    } catch (e) {
      if (estado) estado.cargando = false;
      if (!estado || e.codigo === 401) return;
      zona.className = '';
      zona.innerHTML = util.errorHtml(e.message, 'fuentes');
      zona.querySelector('[data-reintentar]').addEventListener('click', () => { zona.className = 'cuadricula col-3'; cargar(); });
    }
  }

  function render(items) {
    const zona = estado.contenedor.querySelector('#fuentes-lista');
    const aviso = estado.contenedor.querySelector('#fuentes-aviso');
    zona.innerHTML = '';
    aviso.innerHTML = '';
    if (!items.length) { zona.className = ''; zona.innerHTML = util.vacioHtml('Sin fuentes', 'El catálogo no tiene fuentes registradas.'); return; }
    zona.className = 'cuadricula col-3';

    const degradadas = items.filter((f) => f.estado === 'degradada');
    const caidas = items.filter((f) => f.estado === 'sin_conexion');
    if (caidas.length) {
      aviso.innerHTML = `<div class="alerta alerta-peligro">${SOME.icono('advertencia')}<div><strong>${caidas.length === 1 ? 'Una fuente no responde' : `${caidas.length} fuentes no responden`}</strong>${caidas.map((f) => util.escapar(f.nombre || f.fuente)).join(', ')}. Las consultas a esta fuente pueden fallar; los datos mostrados corresponden a la última fecha de corte disponible.</div></div>`;
    } else if (degradadas.length) {
      aviso.innerHTML = `<div class="alerta alerta-aviso">${SOME.icono('advertencia')}<div><strong>${degradadas.length === 1 ? 'Una fuente está degradada' : `${degradadas.length} fuentes están degradadas`}</strong>${degradadas.map((f) => `${util.escapar(f.nombre || f.fuente)}: ${util.escapar(f.mensaje || 'responde con retraso')}`).join(' · ')}</div></div>`;
    }

    items.forEach((f) => {
      const cat = SOME.fuente(f.fuente || f.id) || {};
      const clase = SOME.claseSemaforo(f.estado);
      const historial = Array.isArray(f.historial) ? f.historial.slice(-14) : [];
      const real = f.datos_reales === true || (f.datos_reales === undefined && cat.datos_reales === true);
      const cp = f.copernicus && typeof f.copernicus === 'object' ? f.copernicus : null;
      const nb = f.noaa && typeof f.noaa === 'object' ? f.noaa : null;
      // Bloque de detalle de Copernicus Marine o de NOAA OISST (mismo esquema: modo, texto_modo, acceso)
      const det = cp || nb;
      // Estado de conexión real: la fuente sirve datos reales, o es Copernicus Marine o NOAA OISST fuera del
      // modo simulado forzado (sus verificaciones son reales aunque, sin acceso ni caché, sus datos sean simulados).
      const conexionReal = real || (det !== null && det.modo_configurado !== 'simulado');
      const claseInsignia = { verde: 'insignia-exito', ambar: 'insignia-aviso', rojo: 'insignia-peligro' }[clase] || '';
      let etiquetaConexion = conexionReal
        ? `<span class="insignia insignia-conexion" title="${util.escapar(f.insignia || cat.insignia || 'Datos reales')}: estado, latencia e historial provienen de verificaciones reales del servidor de la fuente.">Conexión real</span>`
        : `<span class="insignia insignia-simulado" title="${util.escapar(det && det.texto_modo ? det.texto_modo : 'Estado, latencia e historial simulados para la demostración; los datos de esta fuente también son simulados.')}">Simulada</span>`;
      if (det && conexionReal && !real) {
        etiquetaConexion += ` <span class="insignia insignia-simulado" title="${util.escapar(det.texto_modo || 'Sin acceso a la fuente ni datos en la caché local: las consultas usan el simulador y lo señalan.')}">Datos simulados</span>`;
      }
      const hayLeyendaGris = historial.some((h) => h.estado === 'sin_registro');
      // Título con el nombre corto («Copernicus Marine», «CHIRPS v3»): el completo se recortaba con elipsis
      // en el proyector; va en el title y el identificador del producto ya se ve debajo
      const nombreCompleto = f.nombre || cat.nombre || f.fuente;
      const t = util.el(`
        <article class="tarjeta" style="display:flex;flex-direction:column;gap:10px">
          <div class="fila fila-entre fila-arriba">
            <div style="min-width:0">
              <h3 class="recortar" title="${util.escapar(nombreCompleto)}">${util.escapar(cat.nombre_corto || nombreCompleto)}</h3>
              <small class="texto-2">${util.escapar(cat.proveedor || '')}${cat.identificador ? ` · <code>${util.escapar(cat.identificador)}</code>` : ''}</small>
              <div class="mt-8">${etiquetaConexion}</div>
            </div>
            <span class="insignia ${claseInsignia}"><span class="semaforo ${clase}"></span>${util.escapar(SOME.textoEstadoFuente(f.estado))}</span>
          </div>
          <div class="cuadricula col-3 fuentes-kpis" style="gap:8px">
            <div class="kpi"><span class="kpi-etiqueta">Fecha de corte</span><span class="kpi-valor" style="font-size:15px">${util.fmtFecha(f.fecha_corte)}</span></div>
            <div class="kpi"><span class="kpi-etiqueta">Latencia</span><span class="kpi-valor" style="font-size:15px"${conexionReal && !f.latencia_ms ? ' title="Sin medición: el servidor de la fuente no respondió en la última verificación"' : ''}>${util.esNumero(f.latencia_ms) && !(conexionReal && !f.latencia_ms) ? util.fmtNum(f.latencia_ms, 0) + ' <small>ms</small>' : '—'}</span></div>
            <div class="kpi"><span class="kpi-etiqueta">Último chequeo</span><span class="kpi-valor" style="font-size:15px">${f.ultimo_chequeo ? util.fmtFechaHora(f.ultimo_chequeo).slice(11) : '—'}</span></div>
          </div>
          ${f.mensaje ? `<div class="alerta ${clase === 'verde' || clase === 'gris' || !clase ? 'alerta-info' : (clase === 'ambar' ? 'alerta-aviso' : 'alerta-peligro')} mb-0" style="padding:8px 10px">${util.escapar(f.mensaje)}</div>` : ''}
          <div>
            <div class="texto-pequeno texto-2 mb-8">Últimos 14 días</div>
            <div class="historial" aria-label="Historial de estado de los últimos 14 días">
              ${historial.length ? historial.map((h) => `<span class="${SOME.claseSemaforo(h.estado)}" title="${util.fmtFecha(h.fecha)}: ${util.escapar(SOME.textoEstadoFuente(h.estado))}${h.origen_texto && h.origen ? ` · respondió: ${util.escapar(h.origen_texto)}` : ''}"></span>`).join('') : '<span class="texto-pequeno texto-2">Sin historial</span>'}
            </div>
            ${historial.length ? `<div class="historial-leyenda texto-pequeno texto-2 mt-8" style="display:flex;flex-wrap:wrap;gap:4px 12px">
              <span><span class="semaforo verde"></span> Operativa</span><span><span class="semaforo ambar"></span> Degradada</span><span><span class="semaforo rojo"></span> Sin conexión</span>${conexionReal || hayLeyendaGris ? '<span><span class="semaforo gris"></span> Sin registro</span>' : ''}
            </div>` : ''}
            ${conexionReal ? `<div class="texto-pequeno texto-2 mt-8">Historial de verificaciones reales${Array.isArray(f.caminos) ? ' del servidor del CHC y de la réplica dynamical.org (el peor estado de cada día)' : (cp ? ' del Copernicus Marine Data Store: credenciales, lectura de prueba y último día de análisis (el peor estado de cada día)' : (nb ? ' de NOAA PSL y NOAA NCEI (el peor estado de cada día; al pasar el cursor, qué servidor respondió)' : ''))}.</div>` : '<div class="texto-pequeno texto-2 mt-8">Historial simulado para la demostración.</div>'}
          </div>
          ${Array.isArray(f.caminos) && f.caminos.length ? caminosHtml(f) : ''}
          ${cp ? copernicusHtml(f, cp) : ''}
          ${nb ? noaaHtml(f, nb) : ''}
          <div class="tarjeta-pie" style="margin-top:auto">
            ${cat.resolucion ? `<div>Resolución: ${util.escapar(cat.resolucion)}${cat.cobertura_temporal ? ` · Cobertura: ${util.escapar(cat.cobertura_temporal)}` : ''}</div>` : ''}
            ${(f.credito || cat.credito) ? `<div class="mt-8">${util.escapar(f.credito || cat.credito)}</div>` : ''}
            ${cat.licencia ? `<div class="mt-8">Licencia: ${util.escapar(cat.licencia)}</div>` : ''}
          </div>
        </article>`);
      zona.appendChild(t);
    });
  }

  /**
   * Caminos de una fuente real con varios accesos a su producto (CHIRPS v3: servidor del CHC y
   * réplica dynamical.org): estado de cada uno, hasta qué fecha llega cada producto y qué sirve la caché.
   */
  function caminosHtml(f) {
    const fecha = (x) => (x ? util.fmtFecha(x) : '—');
    const filas = f.caminos.map((c) => {
      const clase = { operativa: 'verde', degradada: 'ambar', sin_conexion: 'rojo' }[c.estado] || 'gris';
      const prod = c.productos || {};
      const consultado = c.productos_consultado ? util.fmtFechaHora(c.productos_consultado) : '';
      const origenFechas = c.id === 'chc'
        ? (consultado ? `Fechas según su índice consultado el ${consultado}` : 'Aún no se ha consultado su índice')
        : (consultado ? `Fechas según la cobertura consultada el ${consultado}` : 'Cobertura aún no consultada');
      return `
        <li class="camino-fuente" data-camino="${util.escapar(c.id)}" data-estado="${util.escapar(c.estado)}">
          <div class="fila fila-entre camino-cabecera">
            <strong>${util.escapar(c.nombre)}</strong>
            <span class="camino-estado"><span class="semaforo ${clase}" aria-hidden="true"></span>${util.escapar(c.texto_estado || SOME.textoEstadoFuente(c.estado))}</span>
          </div>
          <div class="texto-pequeno texto-2">${util.escapar(c.servidor || '')}${c.detalle ? ` · ${util.escapar(c.detalle)}` : ''}</div>
          <div class="texto-pequeno camino-productos">Final hasta <strong>${fecha(prod.final)}</strong> · Preliminar hasta <strong>${fecha(prod.preliminar)}</strong></div>
          <div class="texto-pequeno texto-2">${util.escapar(origenFechas)}</div>
        </li>`;
    }).join('');
    const ca = f.cache || {};
    const po = ca.dias_por_origen || {};
    const partesOrigen = [];
    if (po.chc) partesOrigen.push(`servidor del CHC: ${util.fmtNum(po.chc, 0)}`);
    if (po.espejo) partesOrigen.push(`réplica dynamical.org: ${util.fmtNum(po.espejo, 0)}`);
    const cache = f.cache ? `
        <li class="camino-fuente" data-camino="cache">
          <div class="fila fila-entre camino-cabecera"><strong>Caché local del SOME</strong><span class="texto-pequeno texto-2">lo que se sirve</span></div>
          <div class="texto-pequeno camino-productos">Final hasta <strong>${fecha(ca.ultimo_final)}</strong> · Preliminar hasta <strong>${fecha(ca.ultimo_preliminar)}</strong></div>
          <div class="texto-pequeno texto-2">${util.fmtNum((ca.dias_finales || 0) + (ca.dias_preliminares || 0), 0)} días${partesOrigen.length ? ` (${partesOrigen.join('; ')})` : ''}${ca.dias_faltantes ? ` · ${util.fmtNum(ca.dias_faltantes, 0)} sin dato` : ''}</div>
        </li>` : '';
    return `
      <div class="caminos-fuente">
        <div class="texto-pequeno texto-2 mb-8">Caminos de acceso${f.modo_sin_red ? ' · modo sin red activo' : ''}</div>
        <ul class="lista lista-caminos">${filas}${cache}</ul>
      </div>`;
  }

  /** Fila de la lista de detalle de una fuente: nombre, semáforo con su estado corto y líneas de texto. */
  function filaDetalle(id, nombre, clase, estadoTexto, lineas) {
    return `
        <li class="camino-fuente" data-camino="${id}">
          <div class="fila fila-entre camino-cabecera">
            <strong>${util.escapar(nombre)}</strong>
            ${estadoTexto ? `<span class="camino-estado">${clase ? `<span class="semaforo ${clase}" aria-hidden="true"></span>` : ''}${util.escapar(estadoTexto)}</span>` : ''}
          </div>
          ${lineas.filter(Boolean).join('')}
        </li>`;
  }

  /**
   * Motivo por el que no se puede descargar ahora, si el mensaje del estado o el texto del modo no lo dicen
   * ya (sin distinguir mayúsculas: el motivo llega como «sin credenciales…» y el mensaje como «Sin credenciales…»).
   */
  function motivoSinAccesoHtml(f, det) {
    const acceso = det.acceso || {};
    const motivoTexto = String(acceso.motivo || '');
    const yaDicho = `${f.mensaje || ''} ${det.texto_modo || ''}`.toLowerCase();
    return !acceso.disponible && motivoTexto && !yaDicho.includes(motivoTexto.slice(0, 40).toLowerCase())
      ? `<div class="alerta alerta-aviso mb-8" style="padding:8px 10px;font-size:12px">${util.escapar(motivoTexto)}</div>` : '';
  }

  /**
   * Detalle de Copernicus Marine (bloque «copernicus» de /fuentes/estado, sin red): servicio ARCO con su
   * latencia, credenciales (solo su estado, nunca su contenido), paquete del toolbox, último día de
   * análisis, climatología GLORYS12 y lo que sirve la caché local del SOME.
   */
  function copernicusHtml(f, cp) {
    const fecha = (x) => (x ? util.fmtFecha(x) : '—');
    const fechaHora = (x) => (x ? util.fmtFechaHora(x) : '');
    const sv = cp.servicio || {};
    const cr = cp.credenciales || {};
    const pq = cp.paquete || {};
    const an = cp.analisis || {};
    const cl = cp.climatologia || {};
    const ca = cp.cache || {};
    const claseServicio = { operativa: 'verde', degradada: 'ambar', sin_conexion: 'rojo', no_instalado: 'rojo', sin_red: 'gris', simulado: 'gris' }[sv.estado] || 'gris';
    const claseCredenciales = { validas: 'verde', invalidas: 'rojo', ausentes: 'rojo', error: 'ambar' }[cr.estado] || 'gris';
    // Junto al semáforo, siempre la etiqueta corta (.camino-estado no se parte: la frase completa desbordaba
    // la tarjeta); la frase del estado, si no son válidas, va en la línea de abajo
    const textoCredenciales = { validas: 'Válidas', invalidas: 'No válidas', ausentes: 'No configuradas', error: 'Error al verificar', sin_verificar: 'Sin verificar' }[cr.estado] || '—';
    const fraseCredenciales = cr.estado !== 'validas' && cr.texto ? `${cr.texto} ` : '';
    const cuandoCredenciales = cr.verificado
      ? (cr.estado === 'validas' ? `Verificadas el ${fechaHora(cr.verificado)}` : `Última comprobación: ${fechaHora(cr.verificado)}`)
      : (fraseCredenciales ? 'El SOME nunca las pide ni las muestra' : 'Se guardan con «copernicusmarine login» en este equipo; el SOME nunca las pide ni las muestra');
    const fila = filaDetalle;
    const latencia = util.esNumero(sv.latencia_ms) && sv.latencia_ms > 0 ? `lectura de prueba en ${util.fmtNum(sv.latencia_ms, 0)} ms` : '';
    const servicio = fila('servicio', 'Copernicus Marine Data Store (ARCO)', claseServicio,
      sv.texto_estado || SOME.textoEstadoFuente(sv.estado), [
        `<div class="texto-pequeno texto-2">${util.escapar([sv.servidor, latencia, sv.ultimo_contacto ? `último contacto: ${fechaHora(sv.ultimo_contacto)}` : ''].filter(Boolean).join(' · '))}</div>`,
        // Con latencia medida, el detalle («Responde (lectura de prueba en … ms).») repetía la línea de arriba
        sv.detalle && !latencia ? `<div class="texto-pequeno texto-2">${util.escapar(sv.detalle)}</div>` : '',
      ]);
    const credenciales = fila('credenciales', 'Credenciales de Copernicus Marine', claseCredenciales, textoCredenciales, [
      `<div class="texto-pequeno texto-2">${util.escapar(fraseCredenciales + cuandoCredenciales)}${pq.instalado === false ? ' · paquete copernicusmarine no instalado' : (pq.version ? ` · toolbox copernicusmarine ${util.escapar(pq.version)}` : '')}</div>`,
    ]);
    // Las fechas ya están en la línea de productos: la frase del estado (que las repite) solo se usa si faltan
    const conFechasAnalisis = Boolean(an.ultimo_dia || an.ultimo_definitivo);
    const textoAnalisis = conFechasAnalisis
      ? `${an.boletin_semanal ? `Boletín semanal del ${fecha(an.boletin_semanal)}; el` : 'El'} pronóstico no se sirve como dato observado`
      : (an.texto || 'El pronóstico no se sirve como dato observado.');
    const analisis = fila('analisis', 'Análisis diario GLOBAL_ANALYSISFORECAST_PHY_001_024', '', '', [
      `<div class="texto-pequeno camino-productos">Análisis NRT hasta <strong>${fecha(an.ultimo_dia)}</strong> · Mejor análisis hasta <strong>${fecha(an.ultimo_definitivo)}</strong></div>`,
      `<div class="texto-pequeno texto-2">${util.escapar(textoAnalisis)}${an.consultado ? ` · consultado el ${util.escapar(fechaHora(an.consultado))}` : (an.regla === 'calendario' ? ' · según el calendario semanal del producto' : '')}</div>`,
    ]);
    const climatologia = fila('climatologia', 'Climatología GLORYS12 (GLOBAL_MULTIYEAR_PHY_001_030)', '', '', [
      `<div class="texto-pequeno camino-productos">Periodo efectivo <strong>${util.escapar(cl.periodo || '—')}</strong> · ${util.fmtNum(cl.en_cache || 0, 0)} de ${util.fmtNum(cl.total || 34, 0)} combinaciones de variable y nivel en caché</div>`,
      `<div class="texto-pequeno texto-2">Climatología mensual oficial (DOI ${util.escapar(cl.doi || '10.48670/moi-00021')}); los periodos 1991-2020, 1981-2010 y 1993-2022 usan este periodo y las respuestas lo indican.</div>`,
    ]);
    const mb = util.esNumero(ca.tamano_bytes) ? ca.tamano_bytes / 1048576 : null;
    const cache = fila('cache', 'Caché local del SOME', '', 'lo que se sirve', [
      `<div class="texto-pequeno camino-productos">Mejor análisis hasta <strong>${fecha(ca.ultimo_definitivo)}</strong> · Análisis NRT hasta <strong>${fecha(ca.ultimo_nrt)}</strong></div>`,
      `<div class="texto-pequeno texto-2">${util.fmtNum(ca.dias_con_dato || 0, 0)} ${ca.dias_con_dato === 1 ? 'día' : 'días'} con dato${ca.dias_nrt ? ` (${util.fmtNum(ca.dias_nrt, 0)} de análisis NRT, que Mercator reemplaza la semana siguiente)` : ''}${mb !== null ? ` · ${util.fmtNum(mb, 1)} MB` : ''}</div>`,
    ]);
    // Por qué no se puede descargar ahora (si el mensaje del estado o el texto del modo no lo dicen ya)
    const motivo = motivoSinAccesoHtml(f, cp);
    return `
      <div class="caminos-fuente">
        <div class="texto-pequeno texto-2 mb-8">${util.escapar(cp.texto_modo || (cp.modo === 'real' ? 'Datos reales' : 'Datos simulados'))}${f.modo_sin_red ? ' · modo sin red activo' : ''}</div>
        ${motivo}
        <ul class="lista lista-caminos">${servicio}${credenciales}${analisis}${climatologia}${cache}</ul>
      </div>`;
  }

  /**
   * Detalle de NOAA OISST v2.1 (bloque «noaa» de /fuentes/estado, sin red): NOAA PSL (vía principal:
   * subconjuntos NCSS del área y de la región Niño 1+2) y NOAA NCEI (listado de días finales y preliminares;
   * archivos diarios de respaldo) con su estado y latencia, lo que publica NOAA, las climatologías y lo que
   * sirve la caché local del SOME (días finales, preliminares y faltantes, y de qué servidor salió cada uno).
   */
  function noaaHtml(f, nb) {
    const fecha = (x) => (x ? util.fmtFecha(x) : '—');
    const fechaHora = (x) => (x ? util.fmtFechaHora(x) : '');
    const sv = nb.servidores || {};
    const pub = nb.publicacion || {};
    const cl = nb.climatologia || {};
    const ca = nb.cache || {};
    // Junto al semáforo, la etiqueta corta (.camino-estado no se parte); la frase completa va debajo
    const CLASE = { operativa: 'verde', degradada: 'ambar', sin_conexion: 'rojo', pausa: 'rojo', sin_registro: 'gris', sin_red: 'gris', simulado: 'gris' };
    const CORTO = { operativa: 'Operativa', degradada: 'Degradada', sin_conexion: 'Sin conexión', pausa: 'En pausa', sin_registro: 'Sin verificar', sin_red: 'No se consulta', simulado: 'No se consulta' };
    const servidor = (id, s, papel) => {
      s = s || {};
      const nombre = String(s.servidor || id).split(' (')[0];
      const latencia = util.esNumero(s.latencia_ms) && s.latencia_ms > 0 ? `verificación en ${util.fmtNum(s.latencia_ms, 0)} ms` : '';
      // Con latencia medida, el detalle «Responde (verificación en … ms).» repetiría la línea de arriba
      const detalle = s.detalle && !(latencia && /^Responde \(/.test(s.detalle)) ? s.detalle : '';
      return filaDetalle(id, nombre, CLASE[s.estado] || 'gris', CORTO[s.estado] || s.texto_estado || '—', [
        `<div class="texto-pequeno texto-2">${util.escapar([papel, latencia, s.ultimo_contacto ? `último contacto: ${fechaHora(s.ultimo_contacto)}` : ''].filter(Boolean).join(' · '))}</div>`,
        detalle ? `<div class="texto-pequeno texto-2">${util.escapar(detalle)}</div>` : '',
      ]);
    };
    const psl = servidor('psl', sv.psl, 'Vía principal: subconjuntos NCSS (THREDDS) del área y de la región Niño 1+2');
    const ncei = servidor('ncei', sv.ncei, 'Listado de días finales y preliminares; archivos diarios de respaldo');
    const consultas = [
      pub.consultado_psl ? `PSL consultado el ${fechaHora(pub.consultado_psl)}` : '',
      pub.consultado_ncei ? `listado de NCEI el ${fechaHora(pub.consultado_ncei)}` : '',
    ].filter(Boolean).join(' · ');
    const publicacion = filaDetalle('publicacion', 'Publicado por NOAA', '', '', [
      `<div class="texto-pequeno camino-productos">Último día en PSL <strong>${fecha(pub.ultimo_dia_psl)}</strong> · Finales hasta <strong>${fecha(pub.ultimo_final)}</strong> · Preliminares hasta <strong>${fecha(pub.ultimo_preliminar)}</strong></div>`,
      `<div class="texto-pequeno texto-2">${util.escapar(consultas || (pub.texto || 'Aún no se ha consultado qué días publicó NOAA.'))}</div>`,
    ]);
    const periodos = Array.isArray(cl.periodos_en_cache) ? cl.periodos_en_cache : [];
    const climatologia = filaDetalle('climatologia', 'Climatologías (NOAA PSL)', '', '', [
      `<div class="texto-pequeno camino-productos"><strong>${util.fmtNum(periodos.length, 0)} de ${util.fmtNum(cl.total || 3, 0)}</strong> periodos mensuales en caché${periodos.length ? ` (${util.escapar(periodos.join(', '))})` : ''} · climatología diaria de Niño 1+2: <strong>${cl.ltm_diaria_nino12 ? 'sí' : 'no'}</strong></div>`,
      '<div class="texto-pequeno texto-2">1991-2020 es la oficial de NOAA PSL; 1981-2010 y 1993-2022 se calculan con sus medias mensuales. OISST empieza en septiembre de 1981: en 1981-2010, enero a agosto usan 1982-2010 y las respuestas lo indican.</div>',
    ]);
    const po = ca.dias_por_origen || {};
    const total = (ca.dias_finales || 0) + (ca.dias_preliminares || 0);
    const partes = [
      `${util.fmtNum(ca.dias_finales || 0, 0)} ${ca.dias_finales === 1 ? 'final' : 'finales'} y ${util.fmtNum(ca.dias_preliminares || 0, 0)} ${ca.dias_preliminares === 1 ? 'preliminar' : 'preliminares'}`,
      po.psl ? `NOAA PSL: ${util.fmtNum(po.psl, 0)}` : '',
      po.ncei ? `NOAA NCEI (respaldo): ${util.fmtNum(po.ncei, 0)}` : '',
    ].filter(Boolean);
    const mb = util.esNumero(ca.tamano_bytes) ? ca.tamano_bytes / 1048576 : null;
    const cache = filaDetalle('cache', 'Caché local del SOME', '', 'lo que se sirve', [
      `<div class="texto-pequeno camino-productos">Hasta <strong>${fecha(ca.fecha_corte)}</strong> · Finales hasta <strong>${fecha(ca.ultimo_final)}</strong>${ca.dias_preliminares ? ` · <strong>${util.fmtNum(ca.dias_preliminares, 0)}</strong> ${ca.dias_preliminares === 1 ? 'día preliminar' : 'días preliminares'}` : ''}</div>`,
      `<div class="texto-pequeno texto-2">${util.fmtNum(total, 0)} ${total === 1 ? 'día' : 'días'} (${util.escapar(partes.join('; '))})${ca.dias_faltantes ? ` · ${util.fmtNum(ca.dias_faltantes, 0)} sin dato` : ''}${mb !== null ? ` · ${util.fmtNum(mb, 1)} MB` : ''}</div>`,
    ]);
    const textoModo = nb.texto_modo || (nb.modo === 'real' ? 'Datos reales' : 'Datos simulados');
    return `
      <div class="caminos-fuente">
        <div class="texto-pequeno texto-2 mb-8">${util.escapar(textoModo)}${f.modo_sin_red && !/sin red/i.test(textoModo) ? ' · modo sin red activo' : ''}</div>
        ${motivoSinAccesoHtml(f, nb)}
        <ul class="lista lista-caminos">${psl}${ncei}${publicacion}${climatologia}${cache}</ul>
      </div>`;
  }

  SOME.registrar('fuentes', {
    titulo: 'Estado de fuentes',
    icono: 'fuentes',
    grupo: 'Sistema',
    montar,
    desmontar,
  });
})();
