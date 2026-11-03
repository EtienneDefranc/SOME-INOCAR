/*
 * SOME — Módulo «Mapas» (RF-06, RF-12, RF-17, RF-19)
 *
 * Visor Leaflet con teselas OSM, superposición PNG del backend, barra de tiempo
 * (slider + día anterior/siguiente + Reproducir con caché por fecha), valor de la celda bajo el
 * cursor, popup «Ver serie aquí», leyenda con gradiente, conmutador valor/anomalía,
 * vectores de corriente, estadísticas del área, crédito siempre visible y barra
 * de herramientas (Exportar PNG/CSV/NetCDF, Guardar vista, Generar reporte).
 */
(function () {
  'use strict';

  const SOME = window.SOME;
  if (!SOME) { console.error('SOME: app.js debe cargarse antes que mapa.js'); return; }

  const C = () => (SOME.componentes && SOME.componentes.comunes) || null;

  const INTERVALO_REPRODUCCION_MS = 700;
  const MAX_DIAS_BARRA = 1500;

  let estado = null; // estado del módulo mientras está montado

  function estilos() {
    C().inyectarEstilos('estilos-modulo-mapa', `
      .mp-resultado { display:flex; flex-direction:column; gap:12px; min-width:0; }
      /* isolation: las capas de Leaflet (z-index 400 a 1000) y las cajas flotantes quedan dentro del mapa; sin ella, al desplazar la página pasaban por encima de la cabecera fija */
      .mp-mapa-caja { position:relative; isolation:isolate; border-radius:var(--radio, 10px); overflow:hidden; border:1px solid var(--borde); background:var(--superficie-2, var(--superficie)); }
      /* Altura según la ventana: en 1366×768 la barra de tiempo queda visible sin desplazarse */
      .mp-mapa { height: clamp(420px, calc(100vh - 300px), 640px); width:100%; }
      @media (max-width: 900px) { .mp-mapa { height: 400px; } }
      .mp-superpuesto { position:absolute; z-index:600; pointer-events:none; }
      /* En columna: con «Datos parciales» la segunda insignia va debajo (en fila tapaba el título) y «Estilo» baja */
      .mp-insignia { top:10px; right:10px; display:flex; flex-direction:column; align-items:flex-end; gap:4px; }
      .mp-insignia .insignia { pointer-events:auto; }
      /* Estilo de la capa de datos: suavizado, isolíneas y opacidad (preferencia local del usuario) */
      .mp-estilo { top:44px; right:10px; pointer-events:auto; background:color-mix(in srgb, var(--superficie) 92%, transparent); border:1px solid var(--borde); border-radius:var(--radio-sm, 6px); padding:8px 10px; font-size:12px; min-width:170px; box-shadow:var(--sombra, 0 1px 3px rgba(0,0,0,.15)); }
      .mp-mapa-caja.con-parcial .mp-estilo { top:72px; }
      .mp-estilo strong { display:block; font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--texto-2); margin-bottom:4px; }
      .mp-estilo label { display:flex; align-items:center; gap:6px; cursor:pointer; padding:2px 0; }
      .mp-estilo input[type="checkbox"] { accent-color: var(--primario); margin:0; }
      .mp-estilo input[type="range"] { width:100%; accent-color: var(--primario); margin:2px 0 0; }
      .mp-estilo .mp-opacidad { display:flex; justify-content:space-between; margin-top:4px; }
      .mp-estilo .mp-opacidad output { font-family:var(--mono, monospace); color:var(--texto-2); }
      .mp-titulo { top:10px; left:52px; background:color-mix(in srgb, var(--superficie) 90%, transparent); border:1px solid var(--borde); border-radius:var(--radio-sm, 6px); padding:6px 10px; font-size:13px; max-width:60%; box-shadow:var(--sombra, 0 1px 3px rgba(0,0,0,.15)); }
      .mp-titulo strong { display:block; font-size:14px; }
      .mp-celda { bottom:30px; left:10px; background:color-mix(in srgb, var(--superficie) 92%, transparent); border:1px solid var(--borde); border-radius:var(--radio-sm, 6px); padding:6px 10px; font-size:12px; font-family:var(--mono, monospace); min-width:200px; display:none; }
      .mp-celda.visible { display:block; }
      /* max-width: la nota de la anomalía se parte en líneas en vez de ensanchar la leyenda sobre el campo */
      .mp-leyenda { bottom:30px; right:10px; background:color-mix(in srgb, var(--superficie) 92%, transparent); border:1px solid var(--borde); border-radius:var(--radio-sm, 6px); padding:8px 10px; font-size:12px; min-width:220px; max-width:280px; pointer-events:auto; }
      .mp-leyenda-barra { height:12px; border-radius:3px; margin:6px 0 4px; border:1px solid var(--borde); }
      .mp-leyenda-ticks { display:flex; justify-content:space-between; font-family:var(--mono, monospace); font-size:11px; }
      .mp-carga { position:absolute; inset:0; z-index:650; display:flex; align-items:center; justify-content:center; background:color-mix(in srgb, var(--fondo) 55%, transparent); backdrop-filter: blur(1px); font-weight:600; }
      .mp-carga .insignia { font-size:13px; padding:8px 14px; background:var(--superficie); color:var(--texto); border:1px solid var(--borde); box-shadow:var(--sombra); }
      .mp-barra-tiempo { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
      .mp-barra-tiempo input[type="range"] { flex:1; min-width:200px; accent-color: var(--primario); }
      .mp-fecha-actual { font-family:var(--mono, monospace); font-size:13px; min-width:170px; }
      .mp-estadisticas { display:grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap:10px; }
      .mp-popup { font-size:13px; min-width:180px; }
      .mp-mapa-caja.con-popup .mp-titulo, .mp-mapa-caja.con-popup .mp-estilo,
      .mp-mapa-caja.con-popup .mp-leyenda, .mp-mapa-caja.con-popup .mp-celda { visibility:hidden; }
      /* Leaflet pinta el popup blanco con texto #333: se usan los tokens para que se lea en oscuro */
      .leaflet-popup-content-wrapper, .leaflet-popup-tip { background:var(--superficie); color:var(--texto); box-shadow:var(--sombra-alta); }
      .leaflet-popup-content-wrapper { border:1px solid var(--borde); }
      .leaflet-container a.leaflet-popup-close-button { color:var(--texto-2); }
      .leaflet-container a.leaflet-popup-close-button:hover, .leaflet-container a.leaflet-popup-close-button:focus { color:var(--texto); }
      .mp-popup .boton { margin-top:8px; width:100%; justify-content:center; }
      .mp-leyenda-vectores { display:flex; align-items:center; gap:6px; margin-top:6px; font-size:11px; color:var(--texto-2); }
      .leaflet-container { font-family: var(--fuente, inherit); }
      .mp-barra-herramientas { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
      .mp-barra-herramientas .espacio { flex:1; }
    `);
  }

  // ---------------------------------------------------------------------------
  // Utilidades de datos
  // ---------------------------------------------------------------------------

  function indiceMasCercano(arreglo, valor) {
    if (!Array.isArray(arreglo) || !arreglo.length) return -1;
    let mejor = 0, dist = Infinity;
    for (let i = 0; i < arreglo.length; i++) {
      const d = Math.abs(arreglo[i] - valor);
      if (d < dist) { dist = d; mejor = i; }
    }
    return mejor;
  }

  function pasoMalla(arreglo) {
    if (!Array.isArray(arreglo) || arreglo.length < 2) return 0.25;
    return Math.abs(arreglo[1] - arreglo[0]) || 0.25;
  }

  function listaFechas(periodo) {
    const c = C();
    const fechas = [];
    if (!periodo || !periodo.inicio || !periodo.fin) return fechas;
    const n = Math.min(c.diasEntre(periodo.inicio, periodo.fin), MAX_DIAS_BARRA);
    for (let i = 0; i <= n; i++) fechas.push(c.sumarDias(periodo.inicio, i));
    return fechas;
  }

  // ---------------------------------------------------------------------------
  // Consulta y dibujo
  // ---------------------------------------------------------------------------

  const CLAVE_ESTILO = 'some_mapa_estilo';
  const ESTILO_POR_DEFECTO = { suavizado: true, isolineas: false, opacidad: 0.9 };

  // Preferencia local del usuario (solo comodidad: si no se puede leer, se usa el valor por defecto)
  function cargarEstilo() {
    try {
      const guardado = JSON.parse(localStorage.getItem(CLAVE_ESTILO) || 'null');
      if (guardado && typeof guardado === 'object') {
        return {
          suavizado: guardado.suavizado !== false,
          isolineas: guardado.isolineas === true,
          opacidad: Math.min(1, Math.max(0.2, Number(guardado.opacidad) || ESTILO_POR_DEFECTO.opacidad)),
        };
      }
    } catch (e) { /* almacenamiento no disponible */ }
    return { ...ESTILO_POR_DEFECTO };
  }
  function guardarEstilo() {
    try { localStorage.setItem(CLAVE_ESTILO, JSON.stringify(estado.estilo)); } catch (e) { /* nada */ }
  }

  function cuerpoActual() {
    const c = C();
    const v = estado.valores;
    const opciones = { modo: true };
    if (estado.modoTiempo === 'dia') opciones.fecha = estado.fechas[estado.indice];
    else opciones.forzarPeriodo = v.tipoPeriodo !== 'fecha';
    const cuerpo = c.cuerpoConsulta(v, opciones);
    cuerpo.suavizado = estado.estilo.suavizado;
    cuerpo.isolineas = estado.estilo.suavizado && estado.estilo.isolineas;
    return cuerpo;
  }

  async function consultarMapa() {
    const c = C();
    if (!estado) return;
    const cuerpo = cuerpoActual();
    const clave = JSON.stringify(cuerpo);
    const ticket = ++estado.ticket;
    estado.cuerpoUltimo = cuerpo;

    let respuesta = estado.cache.get(clave);
    if (!respuesta) {
      mostrarCargaMapa(c.mensajeConsultando(cuerpo.fuente));
      estado.enVuelo = true;
      try {
        respuesta = await SOME.api.post('/api/consultas/mapa', cuerpo);
        if (!respuesta || !Array.isArray(respuesta.valores)) throw new Error('La respuesta del servidor no tiene el formato esperado.');
        estado.cache.set(clave, respuesta);
        if (estado.cache.size > 400) estado.cache.delete(estado.cache.keys().next().value);
      } catch (e) {
        estado.enVuelo = false;
        if (ticket !== estado.ticket || !estado) return;
        ocultarCargaMapa();
        detenerReproduccion();
        c.mostrarError(estado.nodos.errores, e && e.message ? e.message : 'Error al consultar el mapa', () => consultarMapa());
        return;
      }
      estado.enVuelo = false;
    }
    if (ticket !== estado.ticket || !estado) return;
    estado.nodos.errores.innerHTML = '';
    ocultarCargaMapa();
    dibujarRespuesta(respuesta);
    if (estado.valores.vectores && cuerpo.fuente === 'copernicus_phy') consultarVectores(cuerpo);
    else limpiarVectores();
  }

  function mostrarCargaMapa(mensaje) {
    const n = estado.nodos.carga;
    n.innerHTML = `<span class="insignia insignia-info">${C().esc(mensaje || 'Cargando…')}</span>`;
    n.style.display = 'flex';
  }
  function ocultarCargaMapa() { if (estado && estado.nodos.carga) estado.nodos.carga.style.display = 'none'; }

  function dibujarRespuesta(r) {
    const c = C();
    const L = window.L;
    // El valor del popup corresponde al campo anterior (otra fecha, área o variable): se cierra
    if (estado.mapa) estado.mapa.closePopup();
    estado.respuesta = r;
    const zonaInsignia = document.querySelector('.mp-insignia');
    if (zonaInsignia) { zonaInsignia.innerHTML = ''; zonaInsignia.appendChild(c.insigniaSimulado(r)); }
    // Avisos de la respuesta (p. ej., parte del área no está en la caché y no se pudo obtener): se ven sobre
    // el mapa y no solo en el crédito, fuera de la vista. Con datos reales, el único aviso posible es el de
    // cobertura parcial: se añade la insignia ámbar «Datos parciales» junto a la verde.
    const avisos = Array.isArray(r.avisos) ? r.avisos.filter(Boolean) : [];
    const parcial = avisos.length > 0 && (r.procedencia === 'real' || avisos.some((a) => /^Parte del área/i.test(String(a))));
    if (zonaInsignia) {
      if (parcial) zonaInsignia.insertAdjacentHTML('beforeend', `<span class="insignia insignia-aviso" title="${c.esc(avisos.join(' '))}">Datos parciales</span>`);
      zonaInsignia.closest('.mp-mapa-caja').classList.toggle('con-parcial', parcial);
    }
    if (avisos.length) estado.nodos.errores.innerHTML = `<div class="alerta alerta-aviso mb-0" role="status" style="padding:8px 10px">${c.esc(avisos.join(' '))}</div>`;
    const b = r.bbox || c.bboxArea(estado.valores.area);
    const limites = [[b[1], b[0]], [b[3], b[2]]];

    if (r.imagen_png) {
      if (estado.capaImagen) {
        estado.capaImagen.setUrl(r.imagen_png);
        estado.capaImagen.setBounds(L.latLngBounds(limites));
      } else {
        estado.capaImagen = L.imageOverlay(r.imagen_png, limites, { opacity: estado.estilo.opacidad, interactive: false, className: 'mp-capa-datos' }).addTo(estado.mapa);
      }
    } else if (estado.capaImagen) {
      estado.mapa.removeLayer(estado.capaImagen);
      estado.capaImagen = null;
    }
    // Título flotante
    estado.nodos.titulo.innerHTML = `<strong>${c.esc(r.titulo || c.nombreVariable(estado.valores.fuente, estado.valores.variable))}</strong><span class="texto-2">${c.esc(r.subtitulo || '')}</span>`;
    // El título termina 12 px antes de la insignia de procedencia (a 1280 px se montaba sobre ella); en
    // pantallas anchas sigue mandando el 60 % del mapa
    if (zonaInsignia && estado.nodos.titulo.offsetParent) {
      const libre = estado.nodos.titulo.offsetParent.clientWidth - estado.nodos.titulo.offsetLeft - zonaInsignia.offsetWidth - 10 - 12;
      estado.nodos.titulo.style.maxWidth = libre > 160 ? `min(60%, ${Math.round(libre)}px)` : '';
    }

    // Leyenda
    const esAnomalia = (estado.valores.modo === 'anomalia');
    const unidad = r.unidad || c.unidadVariable(estado.valores.fuente, estado.valores.variable);
    // Solo el periodo de referencia (el efectivo y, si difiere, el pedido): el método completo va en el
    // crédito bajo el mapa y, en la leyenda, la ensanchaba hasta tapar el campo a 1366×768.
    const refPedida = r.periodo_referencia || estado.valores.periodo_referencia || '1991-2020';
    // NOAA OISST 1981-2010: «1981-2010 (1982-2010 en enero–agosto)»; Copernicus: «1993-2016 (se pidió 1991-2020)»
    const notaAnomalia = esAnomalia
      ? `Referencia: climatología mensual ${c.textoReferencia(refPedida, r.periodo_referencia_efectivo)}.`
      : '';
    estado.nodos.leyenda.innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px"><strong>${esAnomalia ? 'Anomalía' : 'Valor'}</strong><span class="texto-2">${c.esc(unidad)}</span></div>
      <div class="mp-leyenda-barra" style="background:${c.gradienteCss(r.paleta_rgb, r.paleta)}"></div>
      <div class="mp-leyenda-ticks"><span>${c.fmtNum(r.vmin, decimalesPara(r))}</span><span>${c.fmtNum((Number(r.vmin) + Number(r.vmax)) / 2, decimalesPara(r))}</span><span>${c.fmtNum(r.vmax, decimalesPara(r))}</span></div>
      ${notaAnomalia ? `<div class="texto-2" style="font-size:11px;margin-top:4px">${c.esc(notaAnomalia)}</div>` : ''}
      <div class="mp-leyenda-vectores" style="display:${estado.valores.vectores ? 'flex' : 'none'}"><span style="display:inline-block;width:18px;border-top:2px solid var(--texto)"></span> Vectores de corriente (m/s)</div>`;

    // Estadísticas. Días preliminares (NOAA OISST o CHIRPS) del día o del periodo mostrado, bajo la fecha
    const s = r.estadisticas || {};
    const dec = decimalesPara(r);
    const nPrelim = Number(r.dias_preliminares) || 0;
    const notaPrelim = nPrelim
      ? `<div class="texto-2" style="font-size:11.5px;margin-top:2px" title="Los días preliminares se reemplazan por la versión final cuando la fuente la publica.">${estado.modoTiempo === 'dia' ? 'Dato preliminar' : `Incluye ${c.fmtNum(nPrelim, 0)} ${nPrelim === 1 ? 'día preliminar' : 'días preliminares'}`}</div>` : '';
    estado.nodos.estadisticas.innerHTML = [
      ['Media', s.media], ['Mínimo', s.minimo], ['Máximo', s.maximo],
    ].map(([e, v]) => `<div class="kpi"><div class="kpi-etiqueta">${e}</div><div class="kpi-valor">${c.fmtNum(v, dec)} <small>${c.esc(unidad)}</small></div></div>`).join('')
      + `<div class="kpi"><div class="kpi-etiqueta">Celdas válidas</div><div class="kpi-valor">${c.fmtNum(s.celdas_validas, 0)}</div></div>`
      + `<div class="kpi"><div class="kpi-etiqueta">${estado.modoTiempo === 'dia' ? 'Fecha' : 'Periodo'}</div><div class="kpi-valor" style="font-size:15px">${estado.modoTiempo === 'dia' ? c.fmtFecha(estado.fechas[estado.indice]) : c.textoPeriodo(estado.valores.tipoPeriodo === 'fecha' ? { inicio: estado.valores.fecha, fin: estado.valores.fecha } : estado.valores.periodo)}</div>${notaPrelim}</div>`;

    // Crédito y método (siempre visibles)
    estado.nodos.credito.innerHTML = '';
    estado.nodos.credito.appendChild(c.piezaCredito(r.fuente || c.fuente(estado.valores.fuente), r.metodo, r.fecha_corte));

    // Encuadre al final, con el título y la leyenda ya rellenos: el margen deja el campo libre de las cajas
    // flotantes (a 1366×768 la leyenda tapaba la franja sur). Se prueban dos márgenes y se usa el de mayor
    // zoom: el campo entre el título y la leyenda, o bajo el título y a la izquierda de la leyenda (la
    // columna derecha de insignia, «Estilo» y leyenda queda libre).
    if (!estado.encuadrado) {
      const arriba = estado.nodos.titulo.offsetHeight + 16;
      const opciones = [
        { paddingTopLeft: [20, arriba], paddingBottomRight: [20, estado.nodos.leyenda.offsetHeight + 16] },
        { paddingTopLeft: [20, arriba], paddingBottomRight: [estado.nodos.leyenda.offsetWidth + 16, 20] },
      ];
      const zoomCon = (o) => estado.mapa.getBoundsZoom(limites, false, L.point(o.paddingTopLeft).add(o.paddingBottomRight));
      estado.mapa.fitBounds(limites, opciones.reduce((a, o) => (zoomCon(o) > zoomCon(a) ? o : a)));
      estado.encuadrado = true;
    }
  }

  function decimalesPara(r) {
    const rango = Math.abs(Number(r.vmax) - Number(r.vmin));
    if (!Number.isFinite(rango) || rango === 0) return 2;
    if (rango < 1) return 3;
    if (rango < 10) return 2;
    return 1;
  }

  // Valor bajo el cursor y popup
  function valorEn(latlng) {
    const r = estado.respuesta;
    if (!r || !Array.isArray(r.lats) || !Array.isArray(r.lons)) return null;
    const b = r.bbox;
    const dl = pasoMalla(r.lats) / 2, dn = pasoMalla(r.lons) / 2;
    if (b && (latlng.lat < b[1] - dl || latlng.lat > b[3] + dl || latlng.lng < b[0] - dn || latlng.lng > b[2] + dn)) return null;
    const i = indiceMasCercano(r.lats, latlng.lat);
    const j = indiceMasCercano(r.lons, latlng.lng);
    if (i < 0 || j < 0 || !r.valores[i]) return null;
    const v = r.valores[i][j];
    return { lat: r.lats[i], lon: r.lons[j], valor: (v == null || Number.isNaN(Number(v))) ? null : Number(v) };
  }

  function alMoverCursor(e) {
    if (!estado) return;
    const c = C();
    const celda = valorEn(e.latlng);
    const n = estado.nodos.celda;
    if (!celda) { n.classList.remove('visible'); return; }
    const r = estado.respuesta;
    const unidad = r.unidad || '';
    const esAnomalia = estado.valores.modo === 'anomalia';
    const texto = celda.valor == null ? (estado.valores.fuente === 'chirps_v3' ? 'mar (sin dato)' : 'tierra (sin dato)') : `${esAnomalia ? `anomalía ${c.fmtAnomalia(celda.valor, unidad)}` : `${c.fmtNum(celda.valor, decimalesPara(r))} ${unidad}`}`;
    n.innerHTML = `${c.fmtCoord(celda.lat, true)}, ${c.fmtCoord(celda.lon, false)}<br><strong>${c.esc(texto)}</strong>`;
    n.classList.add('visible');
  }

  function alHacerClic(e) {
    if (!estado) return;
    const c = C();
    const L = window.L;
    const celda = valorEn(e.latlng);
    if (!celda) return;
    const r = estado.respuesta;
    const unidad = r.unidad || '';
    const esAnomalia = estado.valores.modo === 'anomalia';
    const nombreVar = c.nombreVariable(estado.valores.fuente, estado.valores.variable);
    // En modo «Anomalía» el valor es una diferencia respecto a la climatología: se dice explícitamente
    const titulo = esAnomalia ? `Anomalía de ${nombreVar.charAt(0).toLowerCase()}${nombreVar.slice(1)}` : nombreVar;
    // Con Copernicus Marine la climatología es la oficial de GLORYS12 (1993-2016): se dice el periodo efectivo;
    // con NOAA OISST 1981-2010, el pedido y su excepción («1982-2010 en enero–agosto»)
    const referencia = esAnomalia
      ? ` · respecto a ${c.textoReferencia(r.periodo_referencia || estado.valores.periodo_referencia || '1991-2020', r.periodo_referencia_efectivo, { conPedido: false })}`
      : '';
    const contenido = c.crear(`<div class="mp-popup">
      <div class="texto-2" style="font-size:12px">${c.fmtCoord(celda.lat, true)}, ${c.fmtCoord(celda.lon, false)}</div>
      <div style="font-weight:600;margin-top:2px">${c.esc(titulo)}</div>
      <div style="font-size:18px;font-weight:600">${celda.valor == null ? 'Sin dato' : c.esc(esAnomalia ? c.fmtAnomalia(celda.valor, unidad) : `${c.fmtNum(celda.valor, decimalesPara(r))} ${unidad}`)}</div>
      <div class="texto-2" style="font-size:12px">${estado.modoTiempo === 'dia' ? c.fmtFecha(estado.fechas[estado.indice]) : 'Promedio del periodo'}${c.esc(referencia)}</div>
      <button type="button" class="boton boton-primario">Ver serie aquí</button>
    </div>`);
    contenido.querySelector('button').addEventListener('click', () => {
      const v = estado.valores;
      c.irA('series', c.hashDesdeValores(Object.assign({}, v, {
        area: { tipo: 'punto', lat: celda.lat, lon: celda.lon },
        periodo: v.tipoPeriodo === 'fecha' ? { inicio: c.sumarDias(v.fecha, -89), fin: v.fecha } : v.periodo,
      }), '', { forzarPeriodo: true, incluirAgregacion: true }));
    });
    // Con el popup abierto se ocultan las cajas flotantes (evento popupopen en montar); el margen solo
    // aparta el popup de lo que sigue visible: zoom, insignia de procedencia, escala y atribución.
    const popup = L.popup({ maxWidth: 260, autoPanPaddingTopLeft: L.point(54, 50), autoPanPaddingBottomRight: L.point(10, 26) })
      .setLatLng([celda.lat, celda.lon]).setContent(contenido).openOn(estado.mapa);
    estado.popup = popup;
    contenido.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && estado && estado.mapa) estado.mapa.closePopup(); });
    // Leaflet pone «Close popup» en inglés al botón de cierre
    const cerrar = popup.getElement && popup.getElement() ? popup.getElement().querySelector('.leaflet-popup-close-button') : null;
    if (cerrar) { cerrar.setAttribute('aria-label', 'Cerrar'); cerrar.title = 'Cerrar'; }
  }

  // Vectores de corriente
  async function consultarVectores(cuerpoMapa) {
    const c = C();
    const L = window.L;
    const cuerpo = {
      fuente: 'copernicus_phy', variable: 'corrientes', area: cuerpoMapa.area,
      fecha: cuerpoMapa.fecha || (cuerpoMapa.periodo ? cuerpoMapa.periodo.fin : c.fechaCorte()),
      profundidad: cuerpoMapa.profundidad != null ? cuerpoMapa.profundidad : 0,
      paso: 2,
    };
    const clave = 'vec:' + JSON.stringify(cuerpo);
    const ticket = estado.ticket;
    let r = estado.cache.get(clave);
    if (!r) {
      try {
        r = await SOME.api.post('/api/consultas/vectores', cuerpo);
        estado.cache.set(clave, r);
      } catch (e) {
        c.toast(e && e.message ? e.message : 'No se pudieron obtener los vectores de corriente.', 'aviso');
        return;
      }
    }
    if (!estado || ticket !== estado.ticket || !r || !Array.isArray(r.lats)) return;
    limpiarVectores();
    const paso = pasoMalla(r.lats);
    let magMax = 0;
    (r.magnitud || []).forEach((fila) => fila.forEach((m) => { if (m != null && m > magMax) magMax = m; }));
    if (!magMax) {
      (r.u || []).forEach((fila, i) => fila.forEach((u, j) => { const v = r.v[i][j]; if (u != null && v != null) magMax = Math.max(magMax, Math.hypot(u, v)); }));
    }
    if (!magMax) return;
    const escala = (paso * 0.9) / magMax; // grados por (m/s): la flecha más larga ≈ una celda submuestreada
    const color = c.coloresTema().texto;
    const lineas = [];
    r.lats.forEach((lat, i) => {
      r.lons.forEach((lon, j) => {
        const u = r.u && r.u[i] ? r.u[i][j] : null, v = r.v && r.v[i] ? r.v[i][j] : null;
        if (u == null || v == null || Number.isNaN(u) || Number.isNaN(v)) return;
        const mag = Math.hypot(u, v);
        if (mag < 1e-4) return;
        const lat1 = lat + v * escala, lon1 = lon + u * escala;
        const ang = Math.atan2(lat1 - lat, lon1 - lon);
        const lp = Math.max(0.25 * Math.hypot(lat1 - lat, lon1 - lon), 0.04);
        const a1 = ang + Math.PI * 0.8, a2 = ang - Math.PI * 0.8;
        lineas.push(L.polyline([
          [lat, lon], [lat1, lon1],
          [lat1 + lp * Math.sin(a1), lon1 + lp * Math.cos(a1)], [lat1, lon1],
          [lat1 + lp * Math.sin(a2), lon1 + lp * Math.cos(a2)],
        ], { color, weight: 1.5, opacity: 0.9, interactive: false }));
      });
    });
    estado.capaVectores = L.layerGroup(lineas).addTo(estado.mapa);
    const ley = estado.nodos.leyenda.querySelector('.mp-leyenda-vectores');
    if (ley) { ley.style.display = 'flex'; ley.lastChild.textContent = ` Vectores de corriente · máx. ${c.fmtNum(magMax, 2)} ${r.unidad || 'm/s'}`; }
  }

  function limpiarVectores() {
    if (estado && estado.capaVectores) { estado.mapa.removeLayer(estado.capaVectores); estado.capaVectores = null; }
  }

  // ---------------------------------------------------------------------------
  // Barra de tiempo
  // ---------------------------------------------------------------------------

  function configurarBarraTiempo() {
    const c = C();
    const v = estado.valores;
    const periodo = v.tipoPeriodo === 'fecha' ? { inicio: c.sumarDias(v.fecha, -29), fin: v.fecha } : v.periodo;
    estado.fechas = listaFechas(periodo);
    const slider = estado.nodos.slider;
    slider.min = 0;
    slider.max = Math.max(estado.fechas.length - 1, 0);
    let indice = estado.fechas.length - 1;
    if (v.fecha) { const k = estado.fechas.indexOf(v.fecha); if (k >= 0) indice = k; }
    estado.indice = indice;
    slider.value = String(indice);
    actualizarEtiquetaTiempo();
  }

  function actualizarEtiquetaTiempo() {
    const c = C();
    const n = estado.nodos.fechaActual;
    if (estado.modoTiempo === 'promedio') {
      n.textContent = `Promedio · ${c.textoPeriodo({ inicio: estado.fechas[0], fin: estado.fechas[estado.fechas.length - 1] })}`;
    } else {
      n.textContent = `${c.fmtFecha(estado.fechas[estado.indice])} (${estado.indice + 1}/${estado.fechas.length})`;
    }
    estado.nodos.botonPromedio.classList.toggle('boton-primario', estado.modoTiempo === 'promedio');
    estado.nodos.botonPromedio.classList.toggle('boton-secundario', estado.modoTiempo !== 'promedio');
    estado.nodos.botonPromedio.disabled = estado.valores.tipoPeriodo === 'fecha';
  }

  function irAIndice(i, inmediato) {
    if (!estado || !estado.fechas.length) return;
    estado.indice = Math.max(0, Math.min(estado.fechas.length - 1, i));
    estado.modoTiempo = 'dia';
    estado.nodos.slider.value = String(estado.indice);
    actualizarEtiquetaTiempo();
    actualizarHash();
    if (inmediato) consultarMapa();
    else estado.consultaDiferida();
  }

  /** Refleja la fecha del slider y los parámetros en el hash sin disparar el enrutador. */
  function actualizarHash() {
    if (!estado) return;
    try {
      const q = Object.entries(parametrosHash()).map(([k, val]) => `${encodeURIComponent(k)}=${encodeURIComponent(val)}`).join('&');
      history.replaceState(null, '', `#/mapa?${q}`);
    } catch (e) { /* sin importancia */ }
  }

  function alternarReproduccion() {
    if (estado.reproduciendo) detenerReproduccion();
    else iniciarReproduccion();
  }

  function iniciarReproduccion() {
    if (!estado || estado.fechas.length < 2) return;
    estado.reproduciendo = true;
    estado.nodos.botonReproducir.innerHTML = `${C().iconoFi('pause')} Pausar`;
    estado.nodos.botonReproducir.setAttribute('aria-pressed', 'true');
    if (estado.modoTiempo !== 'dia' || estado.indice >= estado.fechas.length - 1) irAIndice(0, true);
    estado.temporizador = setInterval(() => {
      if (!estado) return;
      if (estado.enVuelo) return; // espera a que llegue el mapa anterior
      if (estado.indice >= estado.fechas.length - 1) { detenerReproduccion(); return; }
      irAIndice(estado.indice + 1, true);
    }, INTERVALO_REPRODUCCION_MS);
  }

  function detenerReproduccion() {
    if (!estado) return;
    if (estado.temporizador) { clearInterval(estado.temporizador); estado.temporizador = null; }
    estado.reproduciendo = false;
    if (estado.nodos && estado.nodos.botonReproducir) {
      estado.nodos.botonReproducir.innerHTML = `${C().iconoFi('play')} Reproducir`;
      estado.nodos.botonReproducir.setAttribute('aria-pressed', 'false');
    }
  }

  // ---------------------------------------------------------------------------
  // Acciones de la barra de herramientas
  // ---------------------------------------------------------------------------

  function parametrosHash() {
    const c = C();
    const h = c.hashDesdeValores(estado.valores, '', { incluirModo: true, incluirVectores: true, incluirFecha: true });
    if (estado.modoTiempo === 'dia' && estado.fechas.length) h.fecha = estado.fechas[estado.indice];
    return h;
  }

  function exportarMapa(formato) {
    const c = C();
    if (!estado.cuerpoUltimo) { c.toast('Primero consulte un mapa.', 'aviso'); return; }
    const ext = formato === 'netcdf' ? 'nc' : formato;
    c.exportar(formato, 'mapa', estado.cuerpoUltimo, c.nombreArchivo(estado.valores, ext, estado.valores.modo === 'anomalia' ? 'anomalia' : 'mapa'));
  }

  async function guardarVista() {
    const c = C();
    const v = estado.valores;
    const nombre = `${v.modo === 'anomalia' ? 'Anomalía de ' : ''}${c.nombreVariable(v.fuente, v.variable)} — ${c.textoArea(v.area)}`;
    await c.guardarVista('mapa', parametrosHash(), nombre);
  }

  function generarReporte() {
    const c = C();
    const v = estado.valores;
    const params = {
      plantilla: 'informe_variable', fuente: v.fuente, variable: v.variable,
      periodo: c.serializarPeriodo(v.tipoPeriodo === 'fecha' ? { inicio: c.sumarDias(v.fecha, -29), fin: v.fecha } : v.periodo),
      referencia: v.periodo_referencia,
    };
    if (v.area.tipo === 'recorte') params.recorte = v.area.recorte;
    else c.toast('El reporte usa recortes del catálogo; elija el recorte en el formulario.', 'info');
    const info = c.variable(v.fuente, v.variable);
    if (info && info.tiene_profundidad) params.profundidad = String(v.profundidad ?? 0);
    c.irA('reportes', params);
  }

  // ---------------------------------------------------------------------------
  // Montaje
  // ---------------------------------------------------------------------------

  function aplicarValores(valores, reencuadrar) {
    const c = C();
    estado.valores = valores;
    estado.modoTiempo = valores.tipoPeriodo === 'fecha' ? 'dia' : 'promedio';
    detenerReproduccion();
    configurarBarraTiempo();
    if (reencuadrar) estado.encuadrado = false;
    // Refleja los parámetros en el hash sin recargar el módulo
    actualizarHash();
    consultarMapa();
  }

  SOME.registrar('mapa', {
    titulo: 'Mapas',
    icono: 'mapa',
    grupo: 'Analizar',

    async montar(contenedor, params) {
      const c = C();
      if (!c) { contenedor.innerHTML = '<div class="alerta alerta-peligro">Falta consultas.js (componentes comunes).</div>'; return; }
      estilos();
      await c.asegurarCatalogo();
      contenedor.innerHTML = '';

      if (!c.leafletDisponible()) {
        c.mostrarError(contenedor, 'No se pudo cargar la biblioteca de mapas (Leaflet). Verifique la conexión a internet.', () => location.reload());
        return;
      }

      let valores = c.valoresDesdeHash(params);
      let vistaCargada = null;
      if (params && params.vista) {
        try {
          vistaCargada = await c.cargarVista(params.vista);
          if (vistaCargada) valores = c.valoresDesdeHash(vistaCargada.parametros);
        } catch (e) { c.toast('No se pudo cargar la vista indicada; se usan los parámetros por defecto.', 'error'); }
      }
      if (!params || (!params.fuente && !params.vista)) {
        // Entrada directa desde el menú: anomalía de SST en Niño 1+2, últimos 12 meses
        valores.modo = valores.modo || 'valor';
      }
      // Si la variable es vectorial («Corrientes (vectores)»), el mapa dibuja la magnitud
      // y las flechas, y el panel lo refleja con la casilla «Mostrar vectores» marcada.
      // La anomalía no existe para las corrientes (la climatología publica u y v medias, no la rapidez media):
      // un enlace o una vista con modo=anomalia y variable vectorial se abre en «Valor» en vez de dar un error.
      const infoInicial = c.variable(valores.fuente, valores.variable);
      if (infoInicial && infoInicial.es_vector) { valores.vectores = true; valores.modo = 'valor'; }

      const pagina = c.crear(`<div class="panel-lateral modulo-mapa">
        <aside class="tarjeta parametros mp-panel" aria-label="Parámetros del mapa">
          <h3 class="tarjeta-titulo" style="margin-top:0">Parámetros</h3>
          <div class="mp-constructor"></div>
        </aside>
        <section class="resultado mp-resultado">
          <div class="barra-herramientas mp-barra-herramientas tarjeta" style="padding:10px 14px">
            <span class="mp-vista-nombre" style="font-weight:600"></span>
            <span class="espacio"></span>
            <button type="button" class="boton boton-secundario" data-accion="png">Exportar PNG</button>
            <button type="button" class="boton boton-secundario" data-accion="csv">Exportar CSV</button>
            <button type="button" class="boton boton-secundario" data-accion="netcdf">Exportar NetCDF</button>
            <button type="button" class="boton boton-secundario" data-accion="vista">Guardar vista</button>
            <button type="button" class="boton boton-primario" data-accion="reporte">Generar reporte</button>
          </div>
          <div class="mp-errores"></div>
          <div class="mp-mapa-caja">
            <div class="mp-mapa" role="application" aria-label="Mapa de la variable"></div>
            <div class="mp-superpuesto mp-titulo"></div>
            <div class="mp-superpuesto mp-insignia"></div>
            <div class="mp-superpuesto mp-estilo" role="group" aria-label="Estilo de la capa de datos">
              <strong>Estilo</strong>
              <label><input type="checkbox" data-estilo="suavizado"> Suavizar el campo</label>
              <label><input type="checkbox" data-estilo="isolineas"> Isolíneas</label>
              <div class="mp-opacidad"><span>Opacidad</span><output data-estilo="opacidad-texto"></output></div>
              <input type="range" min="20" max="100" step="5" data-estilo="opacidad" aria-label="Opacidad de la capa de datos">
            </div>
            <div class="mp-superpuesto mp-celda" aria-live="polite"></div>
            <div class="mp-superpuesto mp-leyenda"></div>
            <div class="mp-carga" style="display:none"></div>
          </div>
          <div class="tarjeta mp-barra-tiempo" aria-label="Barra de tiempo">
            <button type="button" class="boton boton-secundario boton-icono" data-t="atras" aria-label="Día anterior" title="Día anterior">${c.iconoFi('caret-left')}</button>
            <button type="button" class="boton boton-secundario" data-t="reproducir" aria-pressed="false">${c.iconoFi('play')} Reproducir</button>
            <button type="button" class="boton boton-secundario boton-icono" data-t="adelante" aria-label="Día siguiente" title="Día siguiente">${c.iconoFi('caret-right')}</button>
            <input type="range" min="0" max="0" value="0" aria-label="Fecha del mapa">
            <span class="mp-fecha-actual"></span>
            <button type="button" class="boton boton-secundario" data-t="promedio" title="Volver al promedio del periodo">Promedio del periodo</button>
          </div>
          <div class="tarjeta"><h3 class="tarjeta-titulo">Estadísticas del área</h3><div class="mp-estadisticas"></div></div>
          <div class="tarjeta mp-credito" style="padding-top:8px;padding-bottom:8px"></div>
        </section>
      </div>`);
      contenedor.appendChild(pagina);
      // La insignia de procedencia (real, simulado o mixto) se pinta con cada respuesta (dibujarRespuesta)

      const L = window.L;
      const divMapa = pagina.querySelector('.mp-mapa');
      // Controles de Leaflet con textos en español (sin «Zoom in/out» ni el prefijo «Leaflet»)
      // zoomSnap 0.25: con el margen del título y la leyenda, el encuadre no baja un nivel entero de zoom
      const mapa = L.map(divMapa, { zoomControl: false, minZoom: 3, maxZoom: 10, worldCopyJump: false, zoomSnap: 0.25 });
      L.control.zoom({ zoomInTitle: 'Acercar', zoomOutTitle: 'Alejar' }).addTo(mapa);
      mapa.attributionControl.setPrefix(false);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© colaboradores de <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>', maxZoom: 19 }).addTo(mapa);
      const area = c.catalogo().bbox_area || [-95, -6, -78, 6];
      mapa.fitBounds([[area[1], area[0]], [area[3], area[2]]]);
      L.control.scale({ imperial: false }).addTo(mapa);

      estado = {
        valores, mapa, capaImagen: null, capaVectores: null, respuesta: null, cache: new Map(),
        estilo: cargarEstilo(),
        fechas: [], indice: 0, modoTiempo: 'promedio', reproduciendo: false, temporizador: null,
        ticket: 0, enVuelo: false, encuadrado: false, cuerpoUltimo: null, constructor: null,
        nodos: {
          errores: pagina.querySelector('.mp-errores'),
          titulo: pagina.querySelector('.mp-titulo'),
          celda: pagina.querySelector('.mp-celda'),
          leyenda: pagina.querySelector('.mp-leyenda'),
          carga: pagina.querySelector('.mp-carga'),
          estadisticas: pagina.querySelector('.mp-estadisticas'),
          credito: pagina.querySelector('.mp-credito'),
          // El primer range de la página es el de opacidad (caja «Estilo»): se busca el de la barra de tiempo
          slider: pagina.querySelector('.mp-barra-tiempo input[type="range"]'),
          fechaActual: pagina.querySelector('.mp-fecha-actual'),
          botonReproducir: pagina.querySelector('[data-t="reproducir"]'),
          botonPromedio: pagina.querySelector('[data-t="promedio"]'),
          nombreVista: pagina.querySelector('.mp-vista-nombre'),
        },
      };
      estado.consultaDiferida = (typeof window.util?.debounce === 'function')
        ? window.util.debounce(() => consultarMapa(), 180)
        : (() => { let t; return () => { clearTimeout(t); t = setTimeout(() => consultarMapa(), 180); }; })();

      if (vistaCargada) estado.nodos.nombreVista.textContent = `Vista: ${vistaCargada.nombre}`;

      // Estilo de la capa de datos
      const cajaEstilo = pagina.querySelector('.mp-estilo');
      const casillaSuavizado = cajaEstilo.querySelector('[data-estilo="suavizado"]');
      const casillaIsolineas = cajaEstilo.querySelector('[data-estilo="isolineas"]');
      const controlOpacidad = cajaEstilo.querySelector('[data-estilo="opacidad"]');
      const textoOpacidad = cajaEstilo.querySelector('[data-estilo="opacidad-texto"]');
      const reflejarEstilo = () => {
        casillaSuavizado.checked = estado.estilo.suavizado;
        casillaIsolineas.checked = estado.estilo.isolineas;
        casillaIsolineas.disabled = !estado.estilo.suavizado;   // las isolíneas requieren el campo suavizado
        controlOpacidad.value = String(Math.round(estado.estilo.opacidad * 100));
        textoOpacidad.textContent = `${Math.round(estado.estilo.opacidad * 100)} %`;
      };
      reflejarEstilo();
      casillaSuavizado.addEventListener('change', () => { estado.estilo.suavizado = casillaSuavizado.checked; reflejarEstilo(); guardarEstilo(); consultarMapa(); });
      casillaIsolineas.addEventListener('change', () => { estado.estilo.isolineas = casillaIsolineas.checked; guardarEstilo(); consultarMapa(); });
      controlOpacidad.addEventListener('input', () => {
        estado.estilo.opacidad = Number(controlOpacidad.value) / 100;
        textoOpacidad.textContent = `${controlOpacidad.value} %`;
        if (estado.capaImagen) estado.capaImagen.setOpacity(estado.estilo.opacidad);
      });
      controlOpacidad.addEventListener('change', guardarEstilo);
      // Evita que arrastrar el control de opacidad mueva el mapa
      if (L.DomEvent) { L.DomEvent.disableClickPropagation(cajaEstilo); L.DomEvent.disableScrollPropagation(cajaEstilo); }

      mapa.on('mousemove', alMoverCursor);
      mapa.on('mouseout', () => estado && estado.nodos.celda.classList.remove('visible'));
      mapa.on('click', alHacerClic);
      // Título, «Estilo», leyenda y valor bajo el cursor quedan por encima del popup (el panel del mapa de
      // Leaflet es otro contexto de apilamiento) y lo tapaban: se ocultan mientras el popup está abierto.
      const cajaMapa = pagina.querySelector('.mp-mapa-caja');
      mapa.on('popupopen', () => cajaMapa.classList.add('con-popup'));
      mapa.on('popupclose', () => cajaMapa.classList.remove('con-popup'));
      // Si el popup sale de la vista (arrastre, zoom) se cierra: si no, las cajas seguirían ocultas sin motivo visible
      mapa.on('moveend', () => {
        const p = estado && estado.popup;
        if (p && mapa.hasLayer(p) && !mapa.getBounds().contains(p.getLatLng())) mapa.closePopup();
      });

      // Barra de tiempo
      estado.nodos.slider.addEventListener('input', (e) => { detenerReproduccion(); irAIndice(Number(e.target.value), false); });
      pagina.querySelector('[data-t="atras"]').addEventListener('click', () => { detenerReproduccion(); irAIndice((estado.modoTiempo === 'dia' ? estado.indice : estado.fechas.length) - 1, true); });
      pagina.querySelector('[data-t="adelante"]').addEventListener('click', () => { detenerReproduccion(); irAIndice(estado.modoTiempo === 'dia' ? estado.indice + 1 : 0, true); });
      estado.nodos.botonReproducir.addEventListener('click', alternarReproduccion);
      estado.nodos.botonPromedio.addEventListener('click', () => {
        detenerReproduccion();
        estado.modoTiempo = 'promedio';
        actualizarEtiquetaTiempo();
        actualizarHash();
        consultarMapa();
      });

      // Barra de herramientas
      pagina.querySelector('[data-accion="png"]').addEventListener('click', () => exportarMapa('png'));
      pagina.querySelector('[data-accion="csv"]').addEventListener('click', () => exportarMapa('csv'));
      pagina.querySelector('[data-accion="netcdf"]').addEventListener('click', () => exportarMapa('netcdf'));
      pagina.querySelector('[data-accion="vista"]').addEventListener('click', guardarVista);
      pagina.querySelector('[data-accion="reporte"]').addEventListener('click', generarReporte);

      // Panel lateral con el constructor reutilizable
      estado.constructor = new SOME.componentes.ConstructorConsulta({
        compacto: true, valores, tipoPeriodo: 'ambos', mostrarModo: true, mostrarVectores: true, permitirVectores: true,
        mostrarAcciones: false, textoEnviar: 'Actualizar mapa',
        ayuda: {
          rango: 'El mapa mostrará el promedio del periodo y una barra de tiempo para recorrerlo día a día.',
          fecha: 'Con una fecha única, el mapa muestra ese día; la barra de tiempo recorre los 30 días anteriores.',
        },
        alEnviar: (v) => aplicarValores(v, true),
        alCambiar: (v) => {
          // Cambios ligeros (modo, vectores, periodo de referencia) se aplican al instante
          const cambioLigero = estado.valores && v.fuente === estado.valores.fuente && v.variable === estado.valores.variable
            && JSON.stringify(v.area) === JSON.stringify(estado.valores.area) && v.tipoPeriodo === estado.valores.tipoPeriodo
            && JSON.stringify(v.periodo) === JSON.stringify(estado.valores.periodo) && v.fecha === estado.valores.fecha
            && v.profundidad === estado.valores.profundidad;
          if (cambioLigero) {
            estado.valores = v;
            consultarMapa();
          }
        },
      });
      estado.constructor.montar(pagina.querySelector('.mp-constructor'));

      // Primera consulta
      estado.modoTiempo = valores.tipoPeriodo === 'fecha' ? 'dia' : 'promedio';
      if (valores.tipoPeriodo !== 'fecha' && params && params.fecha && c.esIso(params.fecha) && params.periodo) estado.modoTiempo = 'dia';
      configurarBarraTiempo();
      // El enlace pedía la anomalía de una variable vectorial: el hash refleja el modo con el que se abrió
      if (infoInicial && infoInicial.es_vector && params && params.modo === 'anomalia') actualizarHash();
      setTimeout(() => { if (estado) estado.mapa.invalidateSize(); }, 100);
      consultarMapa();

      // Redibuja los vectores al cambiar el tema (color del trazo)
      estado.alCambiarTema = () => { if (estado && estado.capaVectores && estado.cuerpoUltimo) consultarVectores(estado.cuerpoUltimo); };
      if (SOME.eventos) SOME.eventos.addEventListener('tema', estado.alCambiarTema);
    },

    desmontar() {
      if (!estado) return;
      detenerReproduccion();
      estado.ticket++;
      if (SOME.eventos && estado.alCambiarTema) SOME.eventos.removeEventListener('tema', estado.alCambiarTema);
      if (estado.constructor) { try { estado.constructor.destruir(); } catch (e) { /* nada */ } }
      if (estado.mapa) { try { estado.mapa.off(); estado.mapa.remove(); } catch (e) { /* nada */ } }
      estado = null;
    },
  });
})();
