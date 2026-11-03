/* ============================================================
   SOME — app.js
   Estado global (window.SOME), enrutador por hash, registro de
   módulos, barra lateral, cabecera, tema claro/oscuro, insignias
   de procedencia (real / simulado / mixto) e indicador de estado
   de las fuentes.
   Contrato de módulos (sección 8.2 de la especificación):
     SOME.registrar(nombre, { titulo, icono, grupo, requiereRol?,
                              async montar(contenedor, params), desmontar?() })
   ============================================================ */
(function () {
  'use strict';

  const util = window.util;
  const api = window.api;

  /* ---------- Rutas y navegación (12 rutas, 4 grupos) ---------- */
  const RUTAS = [
    { ruta: 'panel', titulo: 'Panel personal', grupo: 'Inicio', icono: 'panel' },
    { ruta: 'consultas', titulo: 'Consultas', grupo: 'Analizar', icono: 'consultas' },
    { ruta: 'mapa', titulo: 'Mapas', grupo: 'Analizar', icono: 'mapa' },
    { ruta: 'series', titulo: 'Series', grupo: 'Analizar', icono: 'series' },
    { ruta: 'perfiles', titulo: 'Perfiles', grupo: 'Analizar', icono: 'perfiles' },
    { ruta: 'comparar', titulo: 'Comparar', grupo: 'Analizar', icono: 'comparar' },
    { ruta: 'reportes', titulo: 'Reportes', grupo: 'Productos', icono: 'reportes' },
    { ruta: 'vistas', titulo: 'Mis vistas', grupo: 'Productos', icono: 'vistas' },
    { ruta: 'fuentes', titulo: 'Estado de fuentes', grupo: 'Sistema', icono: 'fuentes' },
    { ruta: 'admin', titulo: 'Administración', grupo: 'Sistema', icono: 'admin', requiereRol: 'administrador' },
    { ruta: 'acerca', titulo: 'Acerca de', grupo: 'Sistema', icono: 'acerca' },
  ];
  const GRUPOS = ['Inicio', 'Analizar', 'Productos', 'Sistema'];
  // «acceso» es la ruta número 12: no aparece en la barra lateral.

  // Mismos nombres visibles que auth.NOMBRES_ROL en el backend (mensajes 403 y PDF)
  const ROLES = {
    oceanografo: 'Oceanógrafo/a',
    analista: 'Analista de datos',
    revisor: 'Jefe de departamento / revisor',
    administrador: 'Administrador de TI',
  };

  /* ---------- Iconos (SVG en línea, trazo 1.8) ---------- */
  const ICONOS = {
    panel: '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="5" rx="1.5"/><rect x="13" y="10" width="8" height="11" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/>',
    consultas: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/><path d="M8 11h6M11 8v6"/>',
    mapa: '<path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z"/><path d="M9 4v14M15 6v14"/>',
    series: '<path d="M3 20h18"/><path d="M4 16l4-5 4 3 4-7 4 4"/>',
    perfiles: '<path d="M4 4v16h16"/><path d="M8 6c3 2 3 5 5 7s4 2 6 5"/><path d="M8 4h12"/>',
    comparar: '<path d="M4 18V10M10 18V6M16 18v-4M20 18H3"/><path d="M14 8l3-3 3 3"/>',
    reportes: '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/><path d="M10 13h6M10 17h6"/>',
    vistas: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    fuentes: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    admin: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z"/>',
    acerca: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    salir: '<path d="M10 17l5-5-5-5"/><path d="M15 12H3"/><path d="M13 3h6v18h-6"/>',
    sol: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    luna: '<path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    lateral: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    mas: '<path d="M12 5v14M5 12h14"/>',
    puntos: '<circle cx="5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><circle cx="19" cy="12" r="1.6" fill="currentColor"/>',
    actualizar: '<path d="M21 12a9 9 0 11-2.6-6.4"/><path d="M21 4v5h-5"/>',
    guardar: '<path d="M5 3h11l3 3v15H5z"/><path d="M8 3v6h8V3"/><rect x="8" y="13" width="8" height="5"/>',
    descargar: '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 19h16"/>',
    enlace: '<path d="M14 3h7v7"/><path d="M21 3l-9 9"/><path d="M19 14v5a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h5"/>',
    compartir: '<circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="M8.2 10.8l7.6-4.6M8.2 13.2l7.6 4.6"/>',
    editar: '<path d="M4 20h4l11-11-4-4L4 16z"/><path d="M13 7l4 4"/>',
    eliminar: '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
    advertencia: '<path d="M12 3l9.5 17H2.5L12 3z"/><path d="M12 10v4M12 17h.01"/>',
    ok: '<circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/>',
    reloj: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    usuario: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/>',
    lluvia: '<path d="M7 15a5 5 0 01.5-10 6 6 0 0111.3 2A4 4 0 0117 15H7z"/><path d="M8 18l-1 3M12 18l-1 3M16 18l-1 3"/>',
    termometro: '<path d="M10 4a2 2 0 014 0v9.5a4 4 0 11-4 0z"/><path d="M12 9v6"/>',
    olas: '<path d="M3 8c3-3 6-3 9 0s6 3 9 0"/><path d="M3 13c3-3 6-3 9 0s6 3 9 0"/><path d="M3 18c3-3 6-3 9 0s6 3 9 0"/>',
    sistema: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    cerrar: '<path d="M18 6L6 18M6 6l12 12"/>',
    flecha_izq: '<path d="M15 6l-6 6 6 6"/>',
  };

  function icono(nombre, clase) {
    const d = ICONOS[nombre] || ICONOS.acerca;
    return `<svg class="icono ${clase || ''}" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  }

  /* ---------- Objeto global ---------- */
  const SOME = {
    estado: { usuario: null, catalogo: null, tema: 'claro', fuentesEstado: null },
    api,
    util,
    eventos: new EventTarget(),
    modulos: {},
    componentes: {},
    RUTAS,
    GRUPOS,
    ROLES,
    icono,
    version: '0.1.0-demo',
  };
  window.SOME = SOME;

  let moduloActivo = null;      // { nombre, modulo }
  let temporizadorFuentes = null;

  /* ---------- Registro y navegación ---------- */
  SOME.registrar = function registrar(nombre, modulo) {
    if (!nombre || !modulo || typeof modulo.montar !== 'function') {
      console.warn('[SOME] Módulo inválido:', nombre);
      return;
    }
    SOME.modulos[nombre] = modulo;
  };

  /** Convierte un objeto de parámetros en cadena de consulta para el hash. */
  function codificarParametros(params) {
    const partes = [];
    Object.keys(params || {}).forEach((k) => {
      const v = params[k];
      if (v === undefined || v === null || v === '') return;
      const texto = (typeof v === 'object') ? JSON.stringify(v) : String(v);
      partes.push(encodeURIComponent(k) + '=' + encodeURIComponent(texto));
    });
    return partes.length ? '?' + partes.join('&') : '';
  }

  SOME.navegar = function navegar(ruta, params) {
    ruta = String(ruta || 'panel').replace(/^#?\/?/, '');
    const nuevo = '#/' + ruta + codificarParametros(params || {});
    if (location.hash === nuevo) enrutar();
    else location.hash = nuevo;
  };

  /** Lee ruta y parámetros del hash actual. */
  function leerHash() {
    const h = location.hash.replace(/^#\/?/, '');
    const idx = h.indexOf('?');
    const ruta = (idx >= 0 ? h.slice(0, idx) : h) || 'panel';
    const params = {};
    if (idx >= 0) {
      h.slice(idx + 1).split('&').forEach((par) => {
        if (!par) return;
        const i = par.indexOf('=');
        const k = decodeURIComponent(i >= 0 ? par.slice(0, i) : par);
        let v = i >= 0 ? decodeURIComponent(par.slice(i + 1)) : '';
        if (/^[[{]/.test(v)) { try { v = JSON.parse(v); } catch (e) { /* cadena */ } }
        params[k] = v;
      });
    }
    return { ruta, params };
  }

  SOME.parametros = function parametros() { return leerHash().params; };
  SOME.rutaActual = function rutaActual() { return leerHash().ruta; };

  /* ---------- Catálogo y ayudas ---------- */
  SOME.recargarCatalogo = async function recargarCatalogo() {
    const catalogo = await api.get('/catalogo');
    SOME.estado.catalogo = catalogo || { fuentes: [], recortes: [] };
    actualizarInsigniaCabecera(catalogo);
    if (catalogo && Array.isArray(catalogo.fuentes)) {
      const estados = catalogo.fuentes
        .filter((f) => f && f.estado)
        .map((f) => Object.assign({ fuente: f.id, nombre: f.nombre }, f.estado));
      if (estados.length) actualizarIndicadorFuentes(estados);
    }
    SOME.eventos.dispatchEvent(new CustomEvent('catalogo', { detail: SOME.estado.catalogo }));
    return SOME.estado.catalogo;
  };

  SOME.fuente = function fuente(id) {
    const c = SOME.estado.catalogo;
    return (c && Array.isArray(c.fuentes) ? c.fuentes : []).find((f) => f.id === id) || null;
  };
  SOME.variable = function variable(fuenteId, variableId) {
    const f = SOME.fuente(fuenteId);
    return (f && Array.isArray(f.variables) ? f.variables : []).find((v) => v.id === variableId) || null;
  };
  SOME.nombreFuente = function nombreFuente(id) {
    const f = SOME.fuente(id);
    return f ? f.nombre : (id || '—');
  };
  SOME.nombreVariable = function nombreVariable(fuenteId, variableId) {
    const v = SOME.variable(fuenteId, variableId);
    return v ? v.nombre : (variableId || '—');
  };
  SOME.unidadVariable = function unidadVariable(fuenteId, variableId) {
    const v = SOME.variable(fuenteId, variableId);
    return v ? (v.unidad || '') : '';
  };
  SOME.recorte = function recorte(id) {
    const c = SOME.estado.catalogo;
    return (c && Array.isArray(c.recortes) ? c.recortes : []).find((r) => r.id === id) || null;
  };
  SOME.nombreRecorte = function nombreRecorte(id) {
    const r = SOME.recorte(id);
    return r ? r.nombre : (id || '—');
  };
  SOME.nombreRol = function nombreRol(rol) { return ROLES[rol] || rol || '—'; };
  SOME.esAdministrador = function esAdministrador() {
    return !!(SOME.estado.usuario && SOME.estado.usuario.rol === 'administrador');
  };

  /** Colores actuales del tema, leídos de las variables CSS (para Plotly y Leaflet). */
  SOME.colores = function colores() {
    const cs = getComputedStyle(document.documentElement);
    const leer = (n, defecto) => (cs.getPropertyValue(n) || defecto).trim();
    return {
      fondo: leer('--fondo', '#F3F6F9'),
      superficie: leer('--superficie', '#FFFFFF'),
      borde: leer('--borde', '#D9E1E8'),
      texto: leer('--texto', '#132A3A'),
      texto2: leer('--texto-2', '#5B7083'),
      primario: leer('--primario', '#0B4F6C'),
      primarioTexto: leer('--primario-texto', '#FFFFFF'),
      acento: leer('--acento', '#F4A261'),
      exito: leer('--exito', '#2A9D8F'),
      aviso: leer('--aviso', '#E9C46A'),
      peligro: leer('--peligro', '#C8553D'),
      info: leer('--info', '#3D8BB8'),
      fuente: leer('--fuente', 'Inter, system-ui, sans-serif'),
    };
  };

  /** Disposición base de Plotly coherente con el tema (los módulos pueden extenderla). */
  SOME.plotlyBase = function plotlyBase(extra) {
    const c = SOME.colores();
    const base = {
      margin: { l: 44, r: 12, t: 12, b: 34 },
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: c.fuente, size: 11.5, color: c.texto2 },
      separators: '. ',   // punto decimal (RNF-01) y espacio fino para los miles, como util.fmtNum
      hovermode: 'x unified',
      // Etiquetas de hover con los colores del tema (por defecto Plotly las deja ilegibles en oscuro)
      hoverlabel: { bgcolor: c.superficie, bordercolor: c.texto2, font: { family: c.fuente, color: c.texto, size: 12 } },
      showlegend: false,
      xaxis: { gridcolor: c.borde, zerolinecolor: c.borde, tickformat: '%d/%m/%Y', hoverformat: '%d/%m/%Y', nticks: 6 },
      yaxis: { gridcolor: c.borde, zerolinecolor: c.borde },
    };
    return Object.assign(base, extra || {});
  };
  SOME.plotlyConfig = { displayModeBar: false, responsive: true, locale: 'es' };

  /* ---------- Locale «es» de Plotly ----------
     plotly.min.js no incluye los locales; se registra uno propio para que la barra de
     herramientas, los nombres de meses y los formatos de fecha y número salgan en español. */
  (function registrarLocalePlotly() {
    if (!window.Plotly || typeof window.Plotly.register !== 'function') return;
    try {
      window.Plotly.register({
        moduleType: 'locale',
        name: 'es',
        dictionary: {
          Autoscale: 'Escala automática',
          'Box Select': 'Selección rectangular',
          'Click to enter Colorscale title': 'Clic para poner el título de la escala de color',
          'Click to enter Component A title': 'Clic para poner el título del componente A',
          'Click to enter Component B title': 'Clic para poner el título del componente B',
          'Click to enter Component C title': 'Clic para poner el título del componente C',
          'Click to enter Plot title': 'Clic para poner el título del gráfico',
          'Click to enter X axis title': 'Clic para poner el título del eje X',
          'Click to enter Y axis title': 'Clic para poner el título del eje Y',
          'Click to enter radial axis title': 'Clic para poner el título del eje radial',
          'Click to enter angular axis title': 'Clic para poner el título del eje angular',
          'Compare data on hover': 'Comparar datos al pasar el cursor',
          'Double-click on legend to isolate one trace': 'Doble clic en la leyenda para aislar una serie',
          'Double-click to zoom back out': 'Doble clic para volver a alejar',
          'Download plot as a png': 'Descargar el gráfico como PNG',
          'Download plot': 'Descargar el gráfico',
          'Draw circle': 'Dibujar un círculo',
          'Draw closed freeform': 'Dibujar una forma cerrada',
          'Draw line': 'Dibujar una línea',
          'Draw open freeform': 'Dibujar una forma abierta',
          'Draw rectangle': 'Dibujar un rectángulo',
          'Edit in Chart Studio': 'Editar en Chart Studio',
          'Erase active shape': 'Borrar la forma activa',
          'IE only supports svg.  Changing format to svg.': 'Internet Explorer solo admite SVG; se cambia el formato a SVG.',
          'Lasso Select': 'Selección con lazo',
          'Orbital rotation': 'Rotación orbital',
          Pan: 'Desplazar',
          'Produced with Plotly.js': 'Hecho con Plotly.js',
          Reset: 'Restablecer',
          'Reset axes': 'Restablecer los ejes',
          'Reset camera to default': 'Restablecer la cámara',
          'Reset camera to last save': 'Volver a la última cámara guardada',
          'Reset view': 'Restablecer la vista',
          'Reset views': 'Restablecer las vistas',
          'Show closest data on hover': 'Mostrar el dato más cercano al pasar el cursor',
          'Snapshot succeeded': 'Captura realizada',
          'Sorry, there was a problem downloading your snapshot!': 'No se pudo descargar la captura.',
          'Taking snapshot - this may take a few seconds': 'Tomando la captura; puede tardar unos segundos',
          'Toggle Spike Lines': 'Mostrar u ocultar las líneas guía',
          'Toggle show closest data on hover': 'Mostrar u ocultar el dato más cercano',
          'Turntable rotation': 'Rotación de plato giratorio',
          // El modo de recuadro solo acerca: para alejar están «Alejar», la rueda y «Ver todo» (zoomAcotado)
          Zoom: 'Acercar con un recuadro',
          'Zoom in': 'Acercar',
          'Zoom out': 'Alejar',
          'close:': 'cierre:',
          'high:': 'máximo:',
          'incoming flow count:': 'flujos de entrada:',
          'kde:': 'densidad:',
          'lat:': 'lat.:',
          'lon:': 'lon.:',
          'low:': 'mínimo:',
          'lower fence:': 'límite inferior:',
          'max:': 'máx.:',
          'mean ± σ:': 'media ± σ:',
          'mean:': 'media:',
          'median:': 'mediana:',
          'min:': 'mín.:',
          'new text': 'nuevo texto',
          'open:': 'apertura:',
          'outgoing flow count:': 'flujos de salida:',
          'q1:': 'q1:',
          'q3:': 'q3:',
          'source:': 'origen:',
          'target:': 'destino:',
          trace: 'serie',
          'upper fence:': 'límite superior:',
        },
        format: {
          days: ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'],
          shortDays: ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'],
          months: ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'],
          shortMonths: ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'],
          date: '%d/%m/%Y',
          decimal: '.',
          thousands: ' ',   // espacio fino, como layout.separators y util.fmtNum
        },
      });
    } catch (e) { console.warn('[SOME] No se pudo registrar el idioma español de Plotly', e); }
  })();

  /* ---------- Sesión ---------- */
  SOME.establecerSesion = function establecerSesion(token, usuario) {
    api.establecerToken(token);
    api.guardarUsuario(usuario);
    SOME.estado.usuario = usuario || null;
    SOME.eventos.dispatchEvent(new CustomEvent('sesion', { detail: SOME.estado.usuario }));
    pintarUsuario();
    pintarNavegacion();
    iniciarVigilanciaFuentes();
  };

  SOME.cerrarSesion = function cerrarSesion(opciones) {
    opciones = opciones || {};
    api.limpiarSesion();
    SOME.estado.usuario = null;
    detenerVigilanciaFuentes();
    SOME.eventos.dispatchEvent(new CustomEvent('sesion', { detail: null }));
    pintarUsuario();
    if (!opciones.silencioso) util.toast('Sesión cerrada.', 'info');
    else if (opciones.motivo === 'expirada') util.toast('La sesión ha expirado. Ingrese nuevamente.', 'aviso');
    SOME.navegar('acceso');
  };

  SOME.salir = async function salir() {
    try { await api.post('/auth/salir', {}); } catch (e) { /* la sesión se cierra igual */ }
    SOME.cerrarSesion();
  };

  /* ---------- Tema ---------- */
  SOME.aplicarTema = function aplicarTema(tema) {
    tema = tema === 'oscuro' ? 'oscuro' : 'claro';
    SOME.estado.tema = tema;
    document.documentElement.setAttribute('data-tema', tema);
    try { localStorage.setItem('some_tema', tema); } catch (e) { /* ignorar */ }
    const b = document.getElementById('boton-tema');
    if (b) {
      b.innerHTML = icono(tema === 'oscuro' ? 'sol' : 'luna');
      b.setAttribute('aria-label', tema === 'oscuro' ? 'Cambiar a modo claro' : 'Cambiar a modo oscuro');
      b.title = b.getAttribute('aria-label');
    }
    SOME.eventos.dispatchEvent(new CustomEvent('tema', { detail: tema }));
  };
  SOME.cambiarTema = function cambiarTema() {
    SOME.aplicarTema(SOME.estado.tema === 'oscuro' ? 'claro' : 'oscuro');
  };

  /* ---------- Barra lateral ---------- */
  function pintarNavegacion() {
    const nav = document.getElementById('lateral-nav');
    if (!nav) return;
    const usuario = SOME.estado.usuario;
    nav.innerHTML = '';
    GRUPOS.forEach((grupo) => {
      const items = RUTAS.filter((r) => r.grupo === grupo && (!r.requiereRol || (usuario && usuario.rol === r.requiereRol)));
      if (!items.length) return;
      const g = util.el(`<div class="nav-grupo"><div class="nav-grupo-titulo">${util.escapar(grupo)}</div></div>`);
      items.forEach((r) => {
        const registrado = !!SOME.modulos[r.ruta];
        const a = util.el(`
          <a class="nav-item ${registrado ? '' : 'pendiente'}" href="#/${r.ruta}" data-ruta="${r.ruta}" title="${util.escapar(r.titulo)}${registrado ? '' : ' (módulo no disponible)'}">
            ${icono(r.icono)}<span class="nav-texto">${util.escapar(r.titulo)}</span>
          </a>`);
        a.addEventListener('click', () => document.body.classList.remove('lateral-movil-abierta'));
        g.appendChild(a);
      });
      nav.appendChild(g);
    });
    marcarRutaActiva();
  }

  function marcarRutaActiva() {
    const actual = leerHash().ruta;
    document.querySelectorAll('#lateral-nav .nav-item').forEach((a) => {
      a.classList.toggle('activo', a.dataset.ruta === actual);
      if (a.dataset.ruta === actual) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
  }

  function pintarUsuario() {
    const pie = document.getElementById('lateral-pie');
    if (!pie) return;
    const u = SOME.estado.usuario;
    if (!u) { pie.innerHTML = ''; return; }
    pie.innerHTML = `
      <span class="avatar" title="${util.escapar(u.nombre || u.usuario || '')}">${util.escapar(util.iniciales(u.nombre || u.usuario))}</span>
      <div class="lateral-usuario">
        <strong title="${util.escapar(u.nombre || '')}">${util.escapar(u.nombre || u.usuario || '')}</strong>
        <small title="${util.escapar(SOME.nombreRol(u.rol))}">${util.escapar(SOME.nombreRol(u.rol))}</small>
      </div>
      <button type="button" class="boton boton-icono boton-fantasma" id="boton-salir" aria-label="Salir" title="Salir">${icono('salir')}</button>`;
    pie.querySelector('#boton-salir').addEventListener('click', () => SOME.salir());
  }

  function alternarLateral() {
    const body = document.body;
    const esMovil = window.matchMedia('(max-width: 760px)').matches;
    if (esMovil) { body.classList.toggle('lateral-movil-abierta'); return; }
    const estrecho = window.matchMedia('(max-width: 1099px)').matches;
    const actual = body.getAttribute('data-lateral');
    // Estado efectivo: en pantallas estrechas se colapsa por defecto
    const colapsadaAhora = actual === 'colapsada' || (estrecho && actual !== 'abierta');
    const nuevo = colapsadaAhora ? 'abierta' : 'colapsada';
    body.setAttribute('data-lateral', nuevo);
    try { localStorage.setItem('some_lateral', nuevo); } catch (e) { /* ignorar */ }
  }

  /* ---------- Cabecera ---------- */
  function pintarCabecera(nombreRuta, modulo) {
    const def = RUTAS.find((r) => r.ruta === nombreRuta);
    const titulo = (modulo && modulo.titulo) || (def && def.titulo) || 'SOME';
    const grupo = (modulo && modulo.grupo) || (def && def.grupo) || '';
    const h1 = document.getElementById('cabecera-titulo');
    const migas = document.getElementById('cabecera-migas');
    if (h1) h1.textContent = titulo;
    if (migas) migas.innerHTML = `<span>SOME</span>${grupo ? `<span>${util.escapar(grupo)}</span>` : ''}<span>${util.escapar(titulo)}</span>`;
    document.title = `${titulo} · SOME`;
  }

  /** Normaliza el estado de una fuente a verde/ámbar/rojo. */
  SOME.claseSemaforo = function claseSemaforo(estado) {
    if (estado === 'operativa') return 'verde';
    if (estado === 'degradada') return 'ambar';
    if (estado === 'sin_conexion') return 'rojo';
    if (estado === 'sin_registro') return 'gris';
    return '';
  };
  SOME.textoEstadoFuente = function textoEstadoFuente(estado) {
    return { operativa: 'Operativa', degradada: 'Degradada', sin_conexion: 'Sin conexión', sin_registro: 'Sin registro' }[estado] || (estado || 'Desconocido');
  };

  /* ---------- Procedencia de los datos (real / simulado / mixto) ---------- */
  const INSIGNIA_SIMULADOS = 'Datos simulados para demostración';
  const PROCEDENCIA_REAL = {
    noaa: {
      texto: 'Datos reales · NOAA OISST',
      titulo: 'Temperatura superficial del mar NOAA OISST v2.1 diaria a 0.25° (NOAA/NCEI; DOI 10.25921/RE9P-PT57), obtenida de NOAA PSL (con los archivos diarios de NOAA NCEI como respaldo) y llevada a la malla de 0.25° del prototipo. Los últimos ~15 días son preliminares: NOAA los reemplaza por la versión final.',
    },
    chirps: {
      texto: 'Datos reales · CHIRPS v3',
      titulo: 'Precipitación CHIRPS v3 del Climate Hazards Center (UC Santa Barbara), obtenida de su servidor o de la réplica pública de dynamical.org (el producto del CHC con los valores redondeados para comprimir) y llevada a la malla de 0.25° del prototipo.',
    },
    copernicus: {
      texto: 'Datos reales · Copernicus Marine',
      titulo: 'Análisis diario de Copernicus Marine (GLOBAL_ANALYSISFORECAST_PHY_001_024; E.U. Copernicus Marine Service Information), leído con el Copernicus Marine Toolbox y llevado a la malla de 0.25° y a los niveles de 0 a 100 m del prototipo. Nunca se sirve el pronóstico como dato observado.',
    },
    varias: {
      texto: 'Datos reales',
      titulo: 'Todos los valores de este resultado provienen de fuentes reales (NOAA OISST, Copernicus Marine o CHIRPS v3), llevados a la malla de 0.25° del prototipo.',
    },
  };
  /** «del 16/10/2026 al 29/10/2026» a partir de «aaaa-mm-dd/aaaa-mm-dd» (periodo_preliminar de NOAA OISST). */
  function textoPeriodoPreliminar(texto) {
    const partes = String(texto || '').split('/').filter(Boolean);
    if (!partes.length) return '';
    if (partes.length === 1 || partes[0] === partes[1]) return `el ${util.fmtFecha(partes[0])}`;
    return `del ${util.fmtFecha(partes[0])} al ${util.fmtFecha(partes[1])}`;
  }
  /** Avisos de una respuesta (respaldo simulado, datos parciales), como una frase. */
  function textoAvisos(d) {
    const avisos = Array.isArray(d.avisos) ? d.avisos : [];
    const extra = d.fuente && typeof d.fuente === 'object' && d.fuente.aviso ? [d.fuente.aviso] : [];
    const todos = avisos.concat(extra.filter((a) => !avisos.some((b) => b.includes(a) || a.includes(b))));
    return todos.length ? ` Aviso: ${todos.join(' ')}` : '';
  }
  /** Periodo de referencia efectivo (si la climatología usada es de otro periodo que el pedido). */
  function textoReferenciaEfectiva(d) {
    const ef = d.periodo_referencia_efectivo || (d.fuente && d.fuente.periodo_referencia_efectivo);
    if (!ef) return '';
    const pedido = d.periodo_referencia;
    // NOAA OISST 1981-2010 declara un efectivo parcial («1982-2010 en enero–agosto», porque OISST empieza en
    // 09/1981): la climatología la calcula el SOME y los demás meses usan el periodo pedido
    if (pedido && pedido !== ef && / en /.test(ef)) return ` Climatología de referencia: ${pedido} (${ef}).`;
    return pedido && pedido !== ef
      ? ` Climatología de referencia: ${ef} (se pidió ${pedido}; es la que publica la fuente).`
      : ` Climatología de referencia: ${ef}.`;
  }
  /**
   * Describe la procedencia de una respuesta de la API (o de una fuente del catálogo):
   * { procedencia, clase, texto, corto, titulo }. Sin información se asume «simulado».
   * La insignia de datos reales nombra la fuente (NOAA OISST, Copernicus Marine o CHIRPS v3) según la
   * insignia que envía el servidor; los avisos (p. ej., una fuente respondió con su simulador de respaldo)
   * y los días preliminares (con su periodo, si el servidor lo envía) van al título.
   */
  SOME.procedencia = function procedencia(r) {
    const d = r || {};
    let p = d.procedencia;
    if (!p && typeof d.datos_reales === 'boolean') p = d.datos_reales ? 'real' : 'simulado';
    if (!p && d.datos_simulados === false) p = 'real';
    if (!['real', 'simulado', 'mixto'].includes(p)) p = 'simulado';
    const avisos = textoAvisos(d);
    if (p === 'real') {
      const insignia = String(d.insignia || '');
      const fuenteId = d.id || (d.fuente && typeof d.fuente === 'object' ? d.fuente.id : d.fuente) || '';
      const clave = /copernicus/i.test(insignia) || (!insignia && /copernicus/.test(fuenteId)) ? 'copernicus'
        : (/chirps/i.test(insignia) || (!insignia && /chirps/.test(fuenteId)) ? 'chirps'
          : (/noaa|oisst/i.test(insignia) || (!insignia && /noaa/.test(fuenteId)) ? 'noaa' : 'varias'));
      const base = PROCEDENCIA_REAL[clave];
      const periodoPrelim = d.dias_preliminares && d.periodo_preliminar ? textoPeriodoPreliminar(d.periodo_preliminar) : '';
      return {
        procedencia: 'real', clase: 'insignia-real', texto: base.texto, corto: 'Datos reales',
        titulo: `${insignia || base.texto}. ${base.titulo}`
          + (d.origen_datos ? ` Origen de los días: ${d.origen_datos}.` : '')
          + (d.origen_climatologia ? ` ${String(d.origen_climatologia).replace(/\.?$/, '.')}` : '')
          + (d.origen_climatologia ? '' : textoReferenciaEfectiva(d))
          + (d.dias_preliminares ? ` Incluye ${d.dias_preliminares} día${d.dias_preliminares === 1 ? '' : 's'} con datos preliminares${periodoPrelim ? ` (${periodoPrelim})` : ''}, que se reemplazarán por la versión final.` : '')
          + avisos,
      };
    }
    if (p === 'mixto') {
      return {
        procedencia: 'mixto', clase: 'insignia-mixto', texto: d.insignia || 'Datos mixtos: reales y simulados', corto: 'Datos mixtos',
        titulo: 'Este resultado combina datos reales (NOAA OISST, Copernicus Marine o CHIRPS v3) con datos simulados de forma determinista (el simulador de respaldo de una fuente que no respondió y cuya caché local no tenía el dato); cada parte indica su procedencia.'
          + textoReferenciaEfectiva(d) + avisos,
      };
    }
    return {
      procedencia: 'simulado', clase: 'insignia-simulado', texto: INSIGNIA_SIMULADOS, corto: 'Datos simulados',
      titulo: 'Valores generados de forma determinista por el prototipo; no son observaciones reales.' + avisos,
    };
  };
  /** HTML de la insignia de procedencia (para gráficos, mapas y encabezados). */
  SOME.insigniaProcedenciaHtml = function insigniaProcedenciaHtml(r, extraClase) {
    const p = SOME.procedencia(r);
    return `<span class="insignia ${p.clase}${extraClase ? ' ' + extraClase : ''}" data-procedencia="${p.procedencia}" title="${util.escapar(p.titulo)}">${util.escapar(p.texto)}</span>`;
  };

  /**
   * Insignia global de la cabecera: honesta respecto a qué es real y qué es simulado. Los textos los
   * decide el servidor según el modo de cada fuente (insignia_cabecera, insignia_cabecera_corta y
   * titulo_cabecera): con las tres fuentes reales, verde y con su lista («Datos reales: NOAA OISST,
   * Copernicus Marine y CHIRPS v3»); si alguna responde con su simulador, ámbar. Con un servidor anterior
   * (que no envía esos textos o repite la insignia en el título) se usan los textos de respaldo.
   */
  function actualizarInsigniaCabecera(catalogo) {
    const nodo = document.getElementById('insignia-cabecera');
    if (!nodo || !catalogo) return;
    const p = SOME.procedencia(catalogo);
    const real = p.procedencia === 'real';
    // Respaldo genérico (no supone qué fuente es la real: con NOAA OISST real ya no es solo CHIRPS)
    const largo = catalogo.insignia_cabecera
      || (p.procedencia === 'mixto' ? 'Demostración: datos reales y simulados' : p.texto);
    const corto = catalogo.insignia_cabecera_corta || (p.procedencia === 'mixto' ? 'Datos mixtos' : p.corto);
    const tituloServidor = catalogo.titulo_cabecera && catalogo.titulo_cabecera !== catalogo.insignia_cabecera
      ? catalogo.titulo_cabecera : '';
    nodo.classList.remove('insignia-simulado', 'insignia-real', 'insignia-mixto');
    nodo.classList.add(real ? 'insignia-real' : 'insignia-simulado');
    nodo.dataset.procedencia = p.procedencia;
    nodo.title = tituloServidor || (p.procedencia === 'mixto'
      ? 'Algunas fuentes sirven datos reales y otras responden con su simulador de respaldo (sin acceso ni caché local); vea «Estado de fuentes».'
      : p.titulo);
    // Icono: marca de verificación con datos reales, triángulo de aviso si hay algo simulado
    const svg = nodo.querySelector('svg.icono');
    if (svg) svg.innerHTML = real ? ICONOS.ok : ICONOS.advertencia;
    const largoN = nodo.querySelector('.insignia-texto-largo');
    const cortoN = nodo.querySelector('.insignia-texto-corto');
    if (largoN) largoN.textContent = largo;
    if (cortoN) cortoN.textContent = corto;
  }

  function actualizarIndicadorFuentes(lista) {
    if (!Array.isArray(lista)) return;
    SOME.estado.fuentesEstado = lista;
    const ind = document.getElementById('indicador-fuentes');
    if (!ind) return;
    const degradadas = lista.filter((f) => f.estado === 'degradada').length;
    const caidas = lista.filter((f) => f.estado === 'sin_conexion').length;
    let clase = 'verde';
    let texto = `Fuentes operativas (${lista.length})`;
    if (caidas) { clase = 'rojo'; texto = `${caidas} fuente${caidas > 1 ? 's' : ''} sin conexión`; }
    else if (degradadas) { clase = 'ambar'; texto = `${degradadas} fuente${degradadas > 1 ? 's' : ''} degradada${degradadas > 1 ? 's' : ''}`; }
    ind.innerHTML = `<span class="semaforo ${clase}" aria-hidden="true"></span><span class="indicador-texto">${util.escapar(texto)}</span>`;
    ind.setAttribute('aria-label', `Estado de las fuentes: ${texto}`);
    ind.title = lista.map((f) => `${f.nombre || f.fuente}: ${SOME.textoEstadoFuente(f.estado)} (corte ${util.fmtFecha(f.fecha_corte)})`).join('\n');
    SOME.eventos.dispatchEvent(new CustomEvent('fuentes', { detail: lista }));
  }
  SOME.actualizarIndicadorFuentes = actualizarIndicadorFuentes;

  async function consultarEstadoFuentes() {
    if (!SOME.estado.usuario) return;
    try {
      const lista = await api.get('/fuentes/estado');
      if (Array.isArray(lista)) actualizarIndicadorFuentes(lista);
    } catch (e) { /* el indicador conserva el último valor */ }
  }
  function iniciarVigilanciaFuentes() {
    detenerVigilanciaFuentes();
    consultarEstadoFuentes();
    temporizadorFuentes = setInterval(consultarEstadoFuentes, 60000);
  }
  function detenerVigilanciaFuentes() {
    if (temporizadorFuentes) clearInterval(temporizadorFuentes);
    temporizadorFuentes = null;
  }

  /* ---------- Enrutador ---------- */
  let enrutando = false;
  let pendiente = false;

  async function enrutar() {
    if (enrutando) { pendiente = true; return; }
    enrutando = true;
    try {
      const { ruta, params } = leerHash();
      const contenedor = document.getElementById('contenido');
      const haySesion = !!SOME.estado.usuario;

      if (!haySesion && ruta !== 'acceso') {
        const destino = location.hash && location.hash !== '#/acceso' ? location.hash : '';
        try { if (destino) sessionStorage.setItem('some_destino', destino); } catch (e) { /* ignorar */ }
        location.hash = '#/acceso';
        return;
      }
      if (haySesion && ruta === 'acceso') { location.hash = '#/panel'; return; }

      const def = RUTAS.find((r) => r.ruta === ruta);
      const modulo = SOME.modulos[ruta];

      // Desmontar el módulo anterior
      if (moduloActivo && moduloActivo.modulo && typeof moduloActivo.modulo.desmontar === 'function') {
        try { moduloActivo.modulo.desmontar(); } catch (e) { console.warn('[SOME] Error al desmontar', moduloActivo.nombre, e); }
      }
      moduloActivo = null;

      document.body.classList.toggle('pantalla-acceso', ruta === 'acceso');
      document.body.classList.remove('lateral-movil-abierta');
      pintarCabecera(ruta, modulo);
      marcarRutaActiva();
      contenedor.innerHTML = '';
      contenedor.scrollTop = 0;
      window.scrollTo(0, 0);

      if (!def && ruta !== 'acceso') {
        contenedor.innerHTML = `<div class="vista">${util.vacioHtml('Página no encontrada', `La ruta «#/${ruta}» no existe en el SOME.`)}</div>`;
        agregarBotonVolver(contenedor);
        return;
      }
      if (def && def.requiereRol && (!SOME.estado.usuario || SOME.estado.usuario.rol !== def.requiereRol)) {
        contenedor.innerHTML = `<div class="vista">${util.vacioHtml('Acceso restringido', 'Esta sección está disponible solo para el administrador de TI.')}</div>`;
        agregarBotonVolver(contenedor);
        return;
      }
      if (!modulo) {
        contenedor.innerHTML = `<div class="vista">${util.vacioHtml('Módulo no disponible', `El módulo «${(def && def.titulo) || ruta}» aún no está cargado en este prototipo.`)}</div>`;
        agregarBotonVolver(contenedor);
        return;
      }

      const vista = document.createElement('div');
      vista.className = 'vista';
      vista.dataset.modulo = ruta;
      contenedor.appendChild(vista);
      moduloActivo = { nombre: ruta, modulo };
      try {
        await modulo.montar(vista, params);
      } catch (e) {
        console.error('[SOME] Error al montar el módulo', ruta, e);
        if (e && e.codigo === 401) return;
        vista.innerHTML = util.errorHtml(e && e.message ? e.message : 'Error desconocido', 'modulo');
        const b = vista.querySelector('[data-reintentar]');
        if (b) b.addEventListener('click', () => enrutar());
      }
    } finally {
      enrutando = false;
      if (pendiente) { pendiente = false; enrutar(); }
    }
  }

  function agregarBotonVolver(contenedor) {
    const v = contenedor.querySelector('.vacio');
    if (!v) return;
    const b = util.el('<button type="button" class="boton boton-secundario">Ir al panel personal</button>');
    b.addEventListener('click', () => SOME.navegar('panel'));
    v.appendChild(b);
  }

  /* ---------- Arranque ---------- */
  async function iniciar() {
    // Tema
    let tema = 'claro';
    try {
      tema = localStorage.getItem('some_tema') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'oscuro' : 'claro');
    } catch (e) { /* ignorar */ }
    SOME.aplicarTema(tema);

    // Barra lateral
    try {
      const lateral = localStorage.getItem('some_lateral');
      if (lateral) document.body.setAttribute('data-lateral', lateral);
    } catch (e) { /* ignorar */ }

    const botonTema = document.getElementById('boton-tema');
    if (botonTema) botonTema.addEventListener('click', SOME.cambiarTema);
    const botonLateral = document.getElementById('boton-lateral');
    if (botonLateral) botonLateral.addEventListener('click', alternarLateral);
    const sobre = document.getElementById('sobreposicion-movil');
    if (sobre) sobre.addEventListener('click', () => document.body.classList.remove('lateral-movil-abierta'));

    // Cerrar menús desplegables al hacer clic fuera
    document.addEventListener('click', (ev) => {
      document.querySelectorAll('.desplegable.abierto').forEach((d) => {
        if (!d.contains(ev.target)) d.classList.remove('abierto');
      });
    });

    // Sesión previa
    const t = api.token();
    if (t) {
      const contenedor = document.getElementById('contenido');
      util.cargando(contenedor, 'Recuperando la sesión…');
      try {
        const [usuario] = await Promise.all([api.get('/auth/yo'), SOME.recargarCatalogo()]);
        if (usuario && usuario.usuario) {
          SOME.establecerSesion(t, usuario);
        } else {
          api.limpiarSesion();
        }
      } catch (e) {
        api.limpiarSesion();
        SOME.estado.usuario = null;
      }
    }
    pintarNavegacion();
    pintarUsuario();

    window.addEventListener('hashchange', enrutar);
    if (!location.hash) location.hash = SOME.estado.usuario ? '#/panel' : '#/acceso';
    else enrutar();
  }

  /** Tras ingresar: carga el catálogo y vuelve a la ruta pendiente o al panel. */
  SOME.despuesDeIngresar = async function despuesDeIngresar(token, usuario) {
    SOME.establecerSesion(token, usuario);
    try { await SOME.recargarCatalogo(); } catch (e) { util.toast('No se pudo cargar el catálogo: ' + e.message, 'aviso'); }
    let destino = '';
    try { destino = sessionStorage.getItem('some_destino') || ''; sessionStorage.removeItem('some_destino'); } catch (e) { /* ignorar */ }
    if (destino && destino !== '#/acceso') location.hash = destino;
    else SOME.navegar('panel');
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
