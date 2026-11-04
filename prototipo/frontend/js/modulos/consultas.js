/*
 * SOME — Módulo «Consultas» (constructor de consultas, RF-02)
 *
 * Expone dos cosas:
 *   1. window.SOME.componentes.ConstructorConsulta: formulario reutilizable
 *      (fuente/variable, área, periodo, profundidad, periodo de referencia)
 *      que usan también los paneles laterales de Mapa, Series, Perfiles y Comparar.
 *   2. window.SOME.componentes.comunes: utilidades compartidas por los módulos
 *      analíticos (serialización del hash, estados de carga/error/vacío,
 *      guardar vista, descargas, colores del tema para Plotly, etc.).
 *
 * Los demás módulos acceden a estos objetos en tiempo de montaje (no al cargar),
 * de modo que el orden de carga de los archivos no importa.
 */
(function () {
  'use strict';

  const SOME = window.SOME;
  if (!SOME) {
    console.error('SOME: app.js debe cargarse antes que consultas.js');
    return;
  }
  SOME.componentes = SOME.componentes || {};

  const util = () => window.util || {};

  // ---------------------------------------------------------------------------
  // Utilidades básicas con respaldo (por si util.js aún no está disponible)
  // ---------------------------------------------------------------------------

  function esc(texto) {
    return String(texto == null ? '' : texto)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function crear(html) {
    if (typeof util().el === 'function') return util().el(html);
    const plantilla = document.createElement('template');
    plantilla.innerHTML = html.trim();
    return plantilla.content.firstElementChild;
  }

  function fmtFecha(iso) {
    if (!iso) return '—';
    if (typeof util().fmtFecha === 'function') return util().fmtFecha(iso);
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
    return m ? `${m[3]}/${m[2]}/${m[1]}` : String(iso);
  }

  function fmtFechaHora(iso) {
    if (!iso) return '—';
    if (typeof util().fmtFechaHora === 'function') return util().fmtFechaHora(iso);
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    return `${dd}/${mm}/${d.getFullYear()} ${hh}:${mi}`;
  }

  function fmtNum(x, decimales = 1) {
    if (x == null || Number.isNaN(Number(x))) return '—';
    if (typeof util().fmtNum === 'function') return util().fmtNum(x, decimales);
    return Number(x).toFixed(decimales);
  }

  function fmtAnomalia(x, unidad, decimales) {
    if (x == null || Number.isNaN(Number(x))) return '—';
    if (typeof util().fmtAnomalia === 'function') return util().fmtAnomalia(x, unidad, decimales);
    const signo = x > 0 ? '+' : (x < 0 ? '−' : '');
    return `${signo}${fmtNum(Math.abs(x), decimales === undefined ? 1 : decimales)} ${unidad || ''}`.trim();
  }

  function toast(mensaje, tipo = 'info') {
    if (typeof util().toast === 'function') return util().toast(mensaje, tipo);
    console[tipo === 'error' ? 'error' : 'log']('SOME:', mensaje);
  }

  /** Cierra un modal abierto con util.modal, cualquiera que sea el manejador devuelto. */
  function cerrarModal(manejador) {
    try {
      if (manejador && typeof manejador.cerrar === 'function') return manejador.cerrar();
      if (manejador && typeof manejador.close === 'function') return manejador.close();
      if (typeof manejador === 'function') return manejador();
    } catch (e) { /* se intenta el respaldo */ }
    const nodo = document.querySelector('.modal-fondo, .modal, dialog[open]');
    if (nodo) nodo.remove();
    else document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  }

  function abrirModal(opciones) {
    if (typeof util().modal === 'function') return util().modal(opciones);
    // Respaldo mínimo si util.modal no existe
    const fondo = crear(`<div class="modal-fondo" style="position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:1000">
      <div class="modal tarjeta" role="dialog" aria-modal="true" style="max-width:520px;width:92%"><h3 class="tarjeta-titulo">${esc(opciones.titulo || '')}</h3></div></div>`);
    const caja = fondo.querySelector('.modal');
    if (opciones.contenido instanceof Node) caja.appendChild(opciones.contenido);
    else caja.insertAdjacentHTML('beforeend', opciones.contenido || '');
    const cerrar = () => { if (fondo.parentNode) { fondo.remove(); if (typeof opciones.alCerrar === 'function') opciones.alCerrar(); } };
    const zona = crear('<div class="fila" style="justify-content:flex-end;margin-top:12px"></div>');
    (opciones.acciones && opciones.acciones.length ? opciones.acciones : [{ texto: 'Cerrar', clase: 'boton-secundario' }]).forEach((a) => {
      const b = crear(`<button type="button" class="boton ${esc(a.clase || '')}">${esc(a.texto)}</button>`);
      b.addEventListener('click', async () => {
        if (typeof a.accion === 'function') {
          try { if ((await a.accion(cerrar)) === false) return; } catch (e) { toast(e && e.message ? e.message : 'Ocurrió un error', 'error'); return; }
        }
        if (a.cerrar !== false) cerrar();
      });
      zona.appendChild(b);
    });
    caja.appendChild(zona);
    fondo.addEventListener('click', (e) => { if (e.target === fondo) cerrar(); });
    document.body.appendChild(fondo);
    return { cerrar };
  }

  // ---------------------------------------------------------------------------
  // Fechas ISO (sin problemas de zona horaria)
  // ---------------------------------------------------------------------------

  function aUtc(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
    if (!m) return null;
    return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  }
  function aIso(fecha) {
    return fecha.toISOString().slice(0, 10);
  }
  function sumarDias(iso, n) {
    const d = aUtc(iso);
    if (!d) return iso;
    d.setUTCDate(d.getUTCDate() + n);
    return aIso(d);
  }
  function sumarMeses(iso, n) {
    const d = aUtc(iso);
    if (!d) return iso;
    d.setUTCMonth(d.getUTCMonth() + n);
    return aIso(d);
  }
  function diasEntre(inicioIso, finIso) {
    const a = aUtc(inicioIso), b = aUtc(finIso);
    if (!a || !b) return 0;
    return Math.round((b - a) / 86400000);
  }
  function minIso(a, b) { return a < b ? a : b; }
  function maxIso(a, b) { return a > b ? a : b; }
  /**
   * Periodo de referencia para mostrar a partir del pedido y del efectivo (periodo_referencia_efectivo).
   * - NOAA OISST declara un efectivo parcial («1982-2010 en enero–agosto»: OISST empieza en 09/1981) y los demás
   *   meses sí usan el pedido: «1981-2010 (1982-2010 en enero–agosto)».
   * - Copernicus Marine declara otro periodo (GLORYS12 «1993-2016»): «1993-2016 (se pidió 1991-2020)».
   * Opciones: conPedido = false omite «(se pidió …)»; corto = true da solo el periodo que rige (el efectivo si es
   * otro; el pedido si la excepción es parcial), para leyendas que no deben partirse; mes (1–12) fuera de los meses
   * de la excepción (p. ej., octubre): el periodo es el pedido.
   */
  function textoReferencia(pedido, efectivo, { conPedido = true, corto = false, mes = null } = {}) {
    const p = String(pedido || '');
    const e = String(efectivo || '');
    if (!e || e === p) return p || e;
    const parcial = / en ([a-záéíóúñ]+)[–-]([a-záéíóúñ]+)$/i.exec(e);
    if (p && parcial) {
      const i = MESES_LARGOS.indexOf(parcial[1].toLowerCase());
      const j = MESES_LARGOS.indexOf(parcial[2].toLowerCase());
      if (corto || (mes && i >= 0 && j >= 0 && (mes - 1 < i || mes - 1 > j))) return p;
      return `${p} (${e})`;
    }
    if (p && / en /.test(e)) return corto ? p : `${p} (${e})`;
    return conPedido && !corto && p ? `${e} (se pidió ${p})` : e;
  }

  function esIso(s) { return /^\d{4}-\d{2}-\d{2}$/.test(String(s || '')); }

  const MESES_LARGOS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
  const MESES_CORTOS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

  // ---------------------------------------------------------------------------
  // Paletas de respaldo (si la respuesta no trae paleta_rgb)
  // ---------------------------------------------------------------------------

  const PALETAS_RESPALDO = {
    viridis: [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]],
    cividis: [[0, 32, 77], [61, 78, 109], [124, 123, 120], [187, 175, 113], [255, 234, 70]],
    YlGnBu: [[255, 255, 217], [199, 233, 180], [65, 182, 196], [34, 94, 168], [8, 29, 88]],
    BrBG: [[84, 48, 5], [191, 129, 45], [245, 245, 245], [53, 151, 143], [0, 60, 48]],
    PuOr_r: [[45, 0, 75], [128, 115, 172], [247, 247, 247], [224, 130, 20], [127, 59, 8]],
  };

  function gradienteCss(paletaRgb, nombre) {
    let pasos = Array.isArray(paletaRgb) && paletaRgb.length >= 2 ? paletaRgb : PALETAS_RESPALDO[nombre] || PALETAS_RESPALDO.viridis;
    const colores = pasos.map((p) => `rgb(${p[0]},${p[1]},${p[2]})`);
    return `linear-gradient(to right, ${colores.join(', ')})`;
  }

  // ---------------------------------------------------------------------------
  // Catálogo
  // ---------------------------------------------------------------------------

  const CATALOGO_VACIO = {
    fuentes: [], recortes: [], periodos_referencia: ['1991-2020', '1981-2010', '1993-2022'],
    profundidades: [0, 5, 10, 20, 30, 50, 75, 100], fecha_inicio: '2023-10-01', fecha_corte: '2026-10-27',
    bbox_area: [-95, -6, -78, 6], datos_simulados: true, procedencia: 'mixto', fuentes_reales: ['chirps_v3'],
  };
  // (Respaldo conservador si el catálogo no carga: solo CHIRPS se declara real. Con el catálogo, las fuentes
  // reales y su procedencia vienen del servidor; Copernicus Marine puede ser real o simulado según su modo.)

  async function asegurarCatalogo() {
    if (!SOME.estado.catalogo && typeof SOME.recargarCatalogo === 'function') {
      try { await SOME.recargarCatalogo(); } catch (e) { console.warn('SOME: no se pudo cargar el catálogo', e); }
    }
    if (!SOME.estado.catalogo && SOME.api) {
      try { SOME.estado.catalogo = await SOME.api.get('/api/catalogo'); } catch (e) { /* se usa el respaldo */ }
    }
    return SOME.estado.catalogo || CATALOGO_VACIO;
  }
  function catalogo() { return SOME.estado.catalogo || CATALOGO_VACIO; }
  function fuente(id) { return (catalogo().fuentes || []).find((f) => f.id === id) || null; }
  function variable(fuenteId, variableId) {
    const f = fuente(fuenteId);
    return f ? (f.variables || []).find((v) => v.id === variableId) || null : null;
  }
  function recorte(id) {
    if (typeof SOME.recorte === 'function') { const r = SOME.recorte(id); if (r) return r; }
    return (catalogo().recortes || []).find((r) => r.id === id) || null;
  }
  function nombreFuente(id) {
    if (typeof SOME.nombreFuente === 'function') { const n = SOME.nombreFuente(id); if (n) return n; }
    const f = fuente(id);
    return f ? f.nombre : id;
  }
  function nombreVariable(fuenteId, variableId) {
    if (typeof SOME.nombreVariable === 'function') { const n = SOME.nombreVariable(fuenteId, variableId); if (n) return n; }
    const v = variable(fuenteId, variableId);
    return v ? v.nombre : variableId;
  }
  function unidadVariable(fuenteId, variableId) {
    const v = variable(fuenteId, variableId);
    return v ? v.unidad : '';
  }
  function fechaCorte() { return catalogo().fecha_corte || '2026-10-27'; }
  function fechaInicio() { return catalogo().fecha_inicio || '2023-10-01'; }

  function nombreCortoFuente(id) {
    return { noaa_oisst: 'NOAA OISST', copernicus_phy: 'Copernicus Marine', chirps_v3: 'CHIRPS' }[id] || nombreFuente(id);
  }
  function mensajeConsultando(fuenteId) {
    return `Consultando ${nombreCortoFuente(fuenteId)}…`;
  }

  const TEXTO_ESTADO = { operativa: 'Operativa', degradada: 'Degradada', sin_conexion: 'Sin conexión', sin_registro: 'Sin registro' };
  const CLASE_ESTADO = { operativa: 'verde', degradada: 'ambar', sin_conexion: 'rojo', sin_registro: 'gris' };

  // ---------------------------------------------------------------------------
  // Atajos de periodo (relativos a una fecha de corte: la de la fuente elegida o, si no, la del sistema,
  // que es el último día de NOAA OISST en la caché local)
  // ---------------------------------------------------------------------------

  function atajosPeriodo(corte = fechaCorte()) {
    const anio = corte.slice(0, 4);
    const finFebrero = sumarDias(`${anio}-03-01`, -1);
    return [
      { id: 'u15', texto: 'Últimos 15 días', periodo: { inicio: sumarDias(corte, -14), fin: corte } },
      { id: 'u30', texto: 'Últimos 30 días', periodo: { inicio: sumarDias(corte, -29), fin: corte } },
      { id: 'u12m', texto: 'Últimos 12 meses', periodo: { inicio: sumarDias(sumarMeses(corte, -12), 1), fin: corte } },
      { id: 'anio', texto: 'Este año', periodo: { inicio: `${anio}-01-01`, fin: corte } },
      { id: 'feb', texto: 'Febrero de este año', periodo: { inicio: `${anio}-02-01`, fin: minIso(finFebrero, corte) } },
      { id: 'oct', texto: 'Octubre de este año', periodo: { inicio: `${anio}-10-01`, fin: minIso(`${anio}-10-31`, corte) } },
    ];
  }
  function periodoPorDefecto() { return atajosPeriodo()[2].periodo; }

  // ---------------------------------------------------------------------------
  // Serialización legible del área y del periodo en el hash
  //   area=recorte:nino12 | area=bbox:-90,-6,-80,0 | area=punto:-2,-81
  //   periodo=2025-10-01,2026-10-27 ; fecha=2026-10-27
  // ---------------------------------------------------------------------------

  function redondear(x, dec) { const f = Math.pow(10, dec); return Math.round(Number(x) * f) / f; }

  function serializarArea(area) {
    if (!area) return '';
    if (area.tipo === 'recorte') return `recorte:${area.recorte}`;
    if (area.tipo === 'bbox' && Array.isArray(area.bbox)) return `bbox:${area.bbox.map((n) => redondear(n, 3)).join(',')}`;
    if (area.tipo === 'punto') return `punto:${redondear(area.lat, 3)},${redondear(area.lon, 3)}`;
    return '';
  }

  function analizarArea(texto) {
    if (!texto) return null;
    if (typeof texto === 'object') return texto;
    const s = String(texto);
    const dos = s.indexOf(':');
    const tipo = dos >= 0 ? s.slice(0, dos) : 'recorte';
    const resto = dos >= 0 ? s.slice(dos + 1) : s;
    if (tipo === 'recorte') return { tipo: 'recorte', recorte: resto };
    const nums = resto.split(',').map((n) => parseFloat(n));
    if (tipo === 'bbox' && nums.length === 4 && nums.every(Number.isFinite)) {
      return { tipo: 'bbox', bbox: [Math.min(nums[0], nums[2]), Math.min(nums[1], nums[3]), Math.max(nums[0], nums[2]), Math.max(nums[1], nums[3])] };
    }
    if (tipo === 'punto' && nums.length === 2 && nums.every(Number.isFinite)) return { tipo: 'punto', lat: nums[0], lon: nums[1] };
    return null;
  }

  function fmtCoord(valor, esLat) {
    const abs = Math.abs(valor);
    const hemisferio = esLat ? (valor < 0 ? 'S' : 'N') : (valor < 0 ? 'O' : 'E');
    return `${fmtNum(abs, 2)}° ${hemisferio}`;
  }

  // Lee un campo de coordenada (input de texto, ver panelPunto). Se muestra con punto decimal
  // (RNF-01), pero al leer se acepta también la coma. Devuelve NaN si el texto no es un número
  // o queda fuera de [data-min, data-max].
  function leerCoordenada(inp) {
    const t = String(inp.value || '').replace(/\s+/g, '').replace(/[−–]/g, '-').replace(',', '.');
    const n = /^[+-]?(\d+\.?\d*|\.\d+)$/.test(t) ? Number(t) : NaN;
    const min = Number(inp.dataset.min), max = Number(inp.dataset.max);
    return Number.isFinite(n) && !(n < min) && !(n > max) ? n : NaN;
  }
  /** Reescribe el campo con punto decimal (p. ej. «-2,5» → «-2.5») o lo marca como inválido. */
  function normalizarCoordenada(inp) {
    const n = leerCoordenada(inp);
    if (Number.isFinite(n)) { inp.value = String(n); inp.removeAttribute('aria-invalid'); }
    else inp.setAttribute('aria-invalid', 'true');
  }

  function textoArea(area) {
    if (!area) return '—';
    if (area.tipo === 'recorte') { const r = recorte(area.recorte); return r ? r.nombre : area.recorte; }
    if (area.tipo === 'bbox') {
      const b = area.bbox;
      return `Rectángulo ${fmtCoord(b[1], true)}–${fmtCoord(b[3], true)}, ${fmtCoord(b[0], false)}–${fmtCoord(b[2], false)}`;
    }
    if (area.tipo === 'punto') return `Punto ${fmtCoord(area.lat, true)}, ${fmtCoord(area.lon, false)}`;
    return '—';
  }

  function centroArea(area) {
    if (!area) return { lat: -2, lon: -81 };
    if (area.tipo === 'punto') return { lat: area.lat, lon: area.lon };
    let b = null;
    if (area.tipo === 'bbox') b = area.bbox;
    if (area.tipo === 'recorte') { const r = recorte(area.recorte); b = r ? r.bbox : null; }
    if (!b) return { lat: -2, lon: -85 };
    return { lat: redondear((b[1] + b[3]) / 2, 2), lon: redondear((b[0] + b[2]) / 2, 2) };
  }

  function bboxArea(area) {
    if (!area) return catalogo().bbox_area || [-95, -6, -78, 6];
    if (area.tipo === 'bbox') return area.bbox;
    if (area.tipo === 'recorte') { const r = recorte(area.recorte); return r ? r.bbox : (catalogo().bbox_area || [-95, -6, -78, 6]); }
    if (area.tipo === 'punto') return [area.lon - 0.5, area.lat - 0.5, area.lon + 0.5, area.lat + 0.5];
    return catalogo().bbox_area || [-95, -6, -78, 6];
  }

  function serializarPeriodo(periodo) {
    if (!periodo || !periodo.inicio || !periodo.fin) return '';
    return `${periodo.inicio},${periodo.fin}`;
  }
  function analizarPeriodo(texto) {
    if (!texto) return null;
    if (typeof texto === 'object' && texto.inicio) return { inicio: texto.inicio, fin: texto.fin };
    const partes = String(texto).split(',').map((s) => s.trim());
    if (partes.length === 2 && esIso(partes[0]) && esIso(partes[1])) {
      return { inicio: minIso(partes[0], partes[1]), fin: maxIso(partes[0], partes[1]) };
    }
    return null;
  }
  function textoPeriodo(periodo) {
    if (!periodo) return '—';
    return `${fmtFecha(periodo.inicio)} – ${fmtFecha(periodo.fin)}`;
  }

  // ---------------------------------------------------------------------------
  // Valores del constructor ⇄ parámetros del hash ⇄ cuerpo de la API
  // ---------------------------------------------------------------------------

  function valoresPorDefecto() {
    const f = (catalogo().fuentes || [])[0];
    return {
      fuente: f ? f.id : 'noaa_oisst',
      variable: f && f.variables && f.variables[0] ? f.variables[0].id : 'sst',
      area: { tipo: 'recorte', recorte: 'nino12' },
      tipoPeriodo: 'rango',
      periodo: periodoPorDefecto(),
      fecha: fechaCorte(),
      profundidad: 0,
      periodo_referencia: (catalogo().periodos_referencia || ['1991-2020'])[0],
      modo: 'valor',
      agregacion: 'diaria',
      vectores: false,
    };
  }

  function valoresDesdeHash(params, prefijo = '') {
    const p = params || {};
    const g = (k) => p[prefijo + k];
    const v = valoresPorDefecto();
    if (g('fuente')) v.fuente = String(g('fuente'));
    if (g('variable')) v.variable = String(g('variable'));
    const area = analizarArea(g('area')) || (g('recorte') ? { tipo: 'recorte', recorte: String(g('recorte')) } : null);
    if (area) v.area = area;
    let periodo = analizarPeriodo(g('periodo'));
    // «dias=N» (enlaces del panel): los últimos N días hasta la fecha de corte
    if (!periodo && Number(g('dias')) > 0) periodo = { inicio: sumarDias(fechaCorte(), -(Number(g('dias')) - 1)), fin: fechaCorte() };
    if (periodo) v.periodo = periodo;
    if (esIso(g('fecha'))) v.fecha = g('fecha');
    if (periodo) v.tipoPeriodo = 'rango';
    else if (esIso(g('fecha'))) v.tipoPeriodo = 'fecha';
    if (g('profundidad') != null && g('profundidad') !== '') v.profundidad = Number(g('profundidad'));
    if (g('referencia')) v.periodo_referencia = String(g('referencia'));
    if (g('periodo_referencia')) v.periodo_referencia = String(g('periodo_referencia'));
    if (g('modo') === 'anomalia' || g('modo') === 'valor') v.modo = g('modo');
    if (['diaria', 'semanal', 'mensual'].includes(g('agregacion'))) v.agregacion = g('agregacion');
    if (g('vectores') === '1' || g('vectores') === 'true' || g('vectores') === true) v.vectores = true;
    return v;
  }

  function hashDesdeValores(v, prefijo = '', opciones = {}) {
    const h = {};
    const k = (n) => prefijo + n;
    h[k('fuente')] = v.fuente;
    h[k('variable')] = v.variable;
    h[k('area')] = serializarArea(v.area);
    if (v.tipoPeriodo === 'fecha' && !opciones.forzarPeriodo) h[k('fecha')] = v.fecha;
    else {
      h[k('periodo')] = serializarPeriodo(v.periodo);
      if (opciones.incluirFecha && v.fecha) h[k('fecha')] = v.fecha;
    }
    const info = variable(v.fuente, v.variable);
    if ((info && info.tiene_profundidad) || opciones.incluirProfundidad) h[k('profundidad')] = String(v.profundidad ?? 0);
    if (opciones.incluirReferencia !== false && v.periodo_referencia) h[k('referencia')] = v.periodo_referencia;
    if (opciones.incluirModo) h[k('modo')] = v.modo || 'valor';
    if (opciones.incluirAgregacion) h[k('agregacion')] = v.agregacion || 'diaria';
    if (opciones.incluirVectores && v.vectores) h[k('vectores')] = '1';
    return h;
  }

  /** Cuerpo común de las consultas (/api/consultas/*). */
  function cuerpoConsulta(v, opciones = {}) {
    const cuerpo = { fuente: v.fuente, variable: v.variable, area: v.area };
    const info = variable(v.fuente, v.variable);
    if (info && info.tiene_profundidad && v.profundidad != null) cuerpo.profundidad = Number(v.profundidad);
    if (v.periodo_referencia) cuerpo.periodo_referencia = v.periodo_referencia;
    if (opciones.conPeriodo !== false) {
      if (opciones.fecha) cuerpo.fecha = opciones.fecha;
      else if (v.tipoPeriodo === 'fecha' && !opciones.forzarPeriodo) cuerpo.fecha = v.fecha;
      else cuerpo.periodo = { inicio: v.periodo.inicio, fin: v.periodo.fin };
    }
    if (opciones.modo) cuerpo.modo = v.modo || 'valor';
    if (opciones.agregacion) cuerpo.agregacion = v.agregacion || 'diaria';
    return cuerpo;
  }

  function nombreArchivo(v, ext, sufijo) {
    const fecha = (v.tipoPeriodo === 'fecha' ? v.fecha : (v.periodo && v.periodo.fin)) || fechaCorte();
    return `SOME_${v.fuente}_${v.variable}${sufijo ? '_' + sufijo : ''}_${String(fecha).replace(/-/g, '')}.${ext}`;
  }

  // ---------------------------------------------------------------------------
  // Navegación
  // ---------------------------------------------------------------------------

  function irA(modulo, params = {}) {
    const limpios = {};
    Object.keys(params).forEach((k) => {
      if (params[k] !== undefined && params[k] !== null && params[k] !== '') limpios[k] = String(params[k]);
    });
    if (typeof SOME.navegar === 'function') return SOME.navegar(modulo, limpios);
    const q = Object.keys(limpios).map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(limpios[k])}`).join('&');
    location.hash = `#/${modulo}${q ? '?' + q : ''}`;
  }

  // ---------------------------------------------------------------------------
  // Estados de la interfaz
  // ---------------------------------------------------------------------------

  function mostrarCarga(contenedor, mensaje) {
    if (!contenedor) return;
    if (typeof util().cargando === 'function') { contenedor.innerHTML = ''; util().cargando(contenedor, mensaje); return; }
    contenedor.innerHTML = `<div class="esqueleto" style="min-height:160px;display:flex;align-items:center;justify-content:center"><span class="texto-2">${esc(mensaje || 'Cargando…')}</span></div>`;
  }

  function mostrarError(contenedor, mensaje, reintentar) {
    if (!contenedor) return;
    contenedor.innerHTML = '';
    const nodo = crear(`<div class="alerta alerta-peligro" role="alert">
      <div style="flex:1"><strong>No se pudo completar la consulta.</strong><div>${esc(mensaje || 'Error desconocido')}</div></div>
      ${reintentar ? '<button type="button" class="boton boton-secundario">Reintentar</button>' : ''}
    </div>`);
    const boton = nodo.querySelector('button');
    if (boton && reintentar) boton.addEventListener('click', () => reintentar());
    contenedor.appendChild(nodo);
  }

  /** Icono de Flaticon UIcons (nombre sin el prefijo «fi-rr-»). */
  function iconoFi(nombre, clase) {
    return typeof util().iconoFi === 'function' ? util().iconoFi(nombre, clase) : '';
  }

  function mostrarVacio(contenedor, titulo, texto, icono = 'water') {
    if (!contenedor) return;
    contenedor.innerHTML = `<div class="vacio"><div class="vacio-icono" aria-hidden="true" style="font-size:32px">${iconoFi(icono)}</div>
      <h3>${esc(titulo || 'Sin datos')}</h3><p>${esc(texto || '')}</p></div>`;
  }

  /**
   * Insignia de procedencia según la respuesta de la API (o el catálogo): verde «Datos reales · CHIRPS v3» o
   * «Datos reales · Copernicus Marine», ámbar «Datos simulados para demostración» o ámbar «Datos mixtos…».
   * Sin argumento, «simulado». (Conserva su nombre histórico para no romper a los módulos que la usan.)
   */
  function insigniaSimulado(r) {
    return crear(SOME.insigniaProcedenciaHtml(r));
  }
  const insigniaProcedencia = insigniaSimulado;

  /**
   * Pie de crédito de una figura (RF-12): fuente, crédito, origen de los días y de la climatología, periodo
   * de referencia efectivo (si la climatología usada es de otro periodo, p. ej. GLORYS12 1993-2016 en
   * Copernicus Marine) y el aviso de la fuente (p. ej., respondió el simulador de respaldo).
   */
  function piezaCredito(f, metodo, fechaCorteFuente) {
    const info = f || {};
    const climatologia = info.origen_climatologia ? String(info.origen_climatologia).replace(/^Climatología\s+/, '') : '';
    const efectivo = info.periodo_referencia_efectivo && !climatologia.includes(info.periodo_referencia_efectivo)
      ? info.periodo_referencia_efectivo : '';
    return crear(`<div class="credito-fuente" style="display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;font-size:12px;color:var(--texto-2);padding:8px 0;border-top:1px solid var(--borde)">
      <span><strong>Fuente:</strong> ${esc(info.nombre || info.id || '—')}${info.identificador ? ` <code>${esc(info.identificador)}</code>` : ''}</span>
      ${info.credito ? `<span><strong>Crédito:</strong> ${esc(info.credito)}</span>` : ''}
      ${info.origen_datos ? `<span><strong>Origen de los días:</strong> ${esc(info.origen_datos)}</span>` : ''}
      ${climatologia ? `<span><strong>Climatología:</strong> ${esc(climatologia)}</span>` : ''}
      ${efectivo ? `<span><strong>Periodo de referencia efectivo:</strong> ${esc(efectivo)}</span>` : ''}
      ${fechaCorteFuente ? `<span><strong>Fecha de corte:</strong> ${esc(fmtFecha(fechaCorteFuente))}</span>` : ''}
      ${info.aviso ? `<span style="color:var(--texto)"><strong>Aviso:</strong> ${esc(info.aviso)}</span>` : ''}
      ${metodo ? `<span><strong>Método:</strong> ${esc(metodo)}</span>` : ''}
    </div>`);
  }

  // ---------------------------------------------------------------------------
  // Vistas guardadas (RF-16)
  // ---------------------------------------------------------------------------

  function guardarVista(modulo, parametros, nombreSugerido) {
    return new Promise((resolver) => {
      const contenido = crear(`<form class="formulario-vista" autocomplete="off">
        <div class="campo"><label for="vista-nombre">Nombre</label><input id="vista-nombre" name="nombre" type="text" required maxlength="120" value="${esc(nombreSugerido || '')}"></div>
        <div class="campo"><label for="vista-descripcion">Descripción</label><textarea id="vista-descripcion" name="descripcion" rows="3" maxlength="500" placeholder="¿Para qué sirve esta vista?"></textarea></div>
        <div class="campo"><label style="display:flex;align-items:center;gap:8px"><input type="checkbox" name="compartida"> Compartir con los demás usuarios (solo lectura)</label></div>
        <button type="submit" hidden aria-hidden="true" tabindex="-1"></button>
      </form>`);
      let resuelto = false;
      let enviando = false;
      const terminar = (valor) => { if (!resuelto) { resuelto = true; resolver(valor); } };
      // Acción «Guardar»: devuelve false para mantener el modal abierto (validación);
      // si la API falla, lanza y util.modal muestra el error sin cerrar.
      const guardar = async () => {
        if (enviando) return false;
        const datos = new FormData(contenido);
        const cuerpo = {
          nombre: String(datos.get('nombre') || '').trim(),
          descripcion: String(datos.get('descripcion') || '').trim(),
          modulo,
          parametros,
          compartida: datos.get('compartida') === 'on',
        };
        if (!cuerpo.nombre) {
          toast('Escriba un nombre para la vista.', 'aviso');
          const n = contenido.querySelector('input[name="nombre"]'); if (n) n.focus();
          return false;
        }
        enviando = true;
        try {
          const respuesta = await SOME.api.post('/api/vistas', cuerpo);
          toast(`Vista «${cuerpo.nombre}» guardada${cuerpo.compartida ? ' y compartida' : ''}.`, 'exito');
          terminar(respuesta);
          return true;
        } finally { enviando = false; }
      };
      let manejador = null;
      // Enter dentro del formulario equivale a pulsar «Guardar»
      contenido.addEventListener('submit', async (e) => {
        e.preventDefault();
        try { if ((await guardar()) !== false) cerrarModal(manejador); } catch (err) { toast(err && err.message ? err.message : 'No se pudo guardar la vista.', 'error'); }
      });
      manejador = abrirModal({
        titulo: 'Guardar vista',
        contenido,
        alCerrar: () => terminar(null),
        acciones: [
          { texto: 'Cancelar', clase: 'boton-secundario' },
          { texto: 'Guardar', clase: 'boton-primario', accion: guardar },
        ],
      });
      setTimeout(() => { const n = contenido.querySelector('input[name="nombre"]'); if (n) { n.focus(); n.select(); } }, 50);
    });
  }

  async function cargarVista(id) {
    const vista = await SOME.api.get(`/api/vistas/${encodeURIComponent(id)}`);
    return vista && vista.parametros ? vista : null;
  }

  // ---------------------------------------------------------------------------
  // Descargas (exportación y PDF)
  // ---------------------------------------------------------------------------

  function tokenSesion() {
    try { return localStorage.getItem('some_token') || ''; } catch (e) { return ''; }
  }

  async function descargarArchivo(ruta, cuerpo, nombre) {
    if (SOME.api && typeof SOME.api.descargar === 'function') {
      try {
        return await SOME.api.descargar(ruta, cuerpo, nombre);
      } catch (e) {
        if (cuerpo) throw e; // en los POST no hay respaldo razonable
      }
    }
    // Respaldo para descargas GET (p. ej., el PDF de un reporte)
    const respuesta = await fetch(ruta, { headers: { Authorization: `Bearer ${tokenSesion()}` } });
    if (!respuesta.ok) {
      let detalle = `Error ${respuesta.status}`;
      try { const j = await respuesta.json(); if (j && j.detalle) detalle = j.detalle; } catch (e) { /* sin detalle */ }
      throw new Error(detalle);
    }
    const blob = await respuesta.blob();
    if (typeof util().descargarBlob === 'function') util().descargarBlob(blob, nombre);
    else {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = nombre || 'archivo'; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    }
  }

  // Grafía visible de cada formato de exportación («NetCDF», no «NETCDF»)
  const ETIQUETA_FORMATO = { csv: 'CSV', png: 'PNG', netcdf: 'NetCDF' };
  function etiquetaFormato(formato) { return ETIQUETA_FORMATO[String(formato || '').toLowerCase()] || String(formato || '').toUpperCase(); }

  async function exportar(formato, clase, consulta, nombre) {
    const etiqueta = etiquetaFormato(formato);
    toast(`Preparando ${etiqueta}…`, 'info');
    try {
      await descargarArchivo('/api/exportar', { formato, clase, consulta }, nombre);
      toast(`Archivo ${etiqueta} descargado.`, 'exito');
    } catch (e) {
      toast(e && e.message ? e.message : `No se pudo exportar a ${etiqueta}.`, 'error');
    }
  }

  /**
   * Abre un PDF protegido en una pestaña nueva sin exponer el token en la URL:
   * se descarga con la cabecera Authorization y se abre como URL de objeto.
   */
  async function abrirPdfEnPestana(ruta) {
    // La pestaña se abre de inmediato (en el gesto del usuario) para que el navegador no la bloquee
    const ventana = window.open('about:blank', '_blank');
    try {
      const respuesta = await fetch(ruta, { headers: { Authorization: `Bearer ${tokenSesion()}` } });
      if (!respuesta.ok) {
        let detalle = `Error ${respuesta.status}`;
        try { const j = await respuesta.json(); if (j && j.detalle) detalle = j.detalle; } catch (e) { /* sin detalle */ }
        throw new Error(detalle);
      }
      const blob = await respuesta.blob();
      const url = URL.createObjectURL(blob.type === 'application/pdf' ? blob : new Blob([blob], { type: 'application/pdf' }));
      if (ventana && !ventana.closed) {
        ventana.location.replace(url);
      } else {
        toast('El navegador bloqueó la pestaña nueva; permita las ventanas emergentes para este sitio.', 'aviso');
        return;
      }
      setTimeout(() => URL.revokeObjectURL(url), 120000);
    } catch (e) {
      if (ventana && !ventana.closed) ventana.close();
      throw e;
    }
  }

  /**
   * Muestra junto a un <input type="date"> la fecha en dd/mm/aaaa (el control nativo
   * sigue el idioma del navegador, no el del SOME) y la mantiene al día.
   */
  function anotarFecha(input) {
    if (!input || input.dataset.anotada) return;
    input.dataset.anotada = '1';
    const nota = crear('<small class="cc-fecha-legible" aria-live="polite"></small>');
    input.insertAdjacentElement('afterend', nota);
    const refrescar = () => { nota.textContent = esIso(input.value) ? fmtFecha(input.value) : ''; };
    input.addEventListener('input', refrescar);
    input.addEventListener('change', refrescar);
    refrescar();
    return refrescar;
  }

  // ---------------------------------------------------------------------------
  // Tema y Plotly
  // ---------------------------------------------------------------------------

  function coloresTema() {
    const estilo = getComputedStyle(document.documentElement);
    const leer = (n, r) => (estilo.getPropertyValue(n) || '').trim() || r;
    return {
      texto: leer('--texto', '#132A3A'),
      texto2: leer('--texto-2', '#5B6B78'),
      acentoTexto: leer('--acento-texto', '#9A5218'),
      borde: leer('--borde', '#D9E1E8'),
      superficie: leer('--superficie', '#FFFFFF'),
      fondo: leer('--fondo', '#F3F6F9'),
      primario: leer('--primario', '#0B4F6C'),
      acento: leer('--acento', '#F4A261'),
      exito: leer('--exito', '#2A9D8F'),
      aviso: leer('--aviso', '#E9C46A'),
      peligro: leer('--peligro', '#C8553D'),
      fuente: leer('--fuente', "'Inter', 'Segoe UI', system-ui, sans-serif"),
    };
  }

  function plotlyBase(extra = {}) {
    const c = coloresTema();
    return Object.assign({
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: c.fuente, color: c.texto, size: 12 },
      separators: '. ',   // punto decimal (RNF-01) y espacio fino para los miles, como util.fmtNum
      margin: { l: 56, r: 24, t: 48, b: 48 },
      hovermode: 'x unified',
      // Sin esto la caja unificada es blanca con el texto claro del tema oscuro, y las etiquetas
      // por traza ponen #444 sobre el color de la traza: ambas ilegibles en modo oscuro.
      hoverlabel: { bgcolor: c.superficie, bordercolor: c.texto2, font: { family: c.fuente, color: c.texto, size: 12 } },
      // Con el fondo transparente Plotly pinta los iconos casi invisibles; vertical para no chocar con
      // título y leyenda (app.css la baja debajo de la insignia de procedencia)
      modebar: { orientation: 'v', bgcolor: 'rgba(0,0,0,0)', color: c.texto2, activecolor: c.primario },
      legend: { orientation: 'h', y: 1.08, x: 0 },
      xaxis: { gridcolor: c.borde, zerolinecolor: c.borde, linecolor: c.borde },
      yaxis: { gridcolor: c.borde, zerolinecolor: c.borde, linecolor: c.borde },
    }, extra);
  }

  const plotlyConfig = { responsive: true, displaylogo: false, locale: 'es', modeBarButtonsToRemove: ['lasso2d', 'select2d'] };

  function plotlyDisponible() { return typeof window.Plotly !== 'undefined'; }
  function leafletDisponible() { return typeof window.L !== 'undefined'; }

  function purgarPlotly(div) {
    if (div && div._zoomAcotado) div._zoomAcotado.quitar();
    if (div && plotlyDisponible()) { try { window.Plotly.purge(div); } catch (e) { /* nada */ } }
  }

  // Zoom acotado. Plotly acerca con un recuadro, pero para alejar solo había el doble clic y los botones
  // de la barra, que aparece al pasar el cursor; además «Alejar» se salía de los datos (profundidades
  // negativas, días sin datos). Con esto el zoom nunca pasa de la vista completa; mientras el gráfico está
  // acercado, la rueda del ratón acerca o aleja y aparece el botón «Ver todo». En la vista completa la
  // rueda desplaza la página, como siempre.
  function zoomAcotado(gd) {
    if (!gd || !gd._fullLayout || !plotlyDisponible()) return;
    if (gd._zoomAcotado) gd._zoomAcotado.quitar();
    const fl = gd._fullLayout;
    // Vista completa de cada eje, en unidades lineales (milisegundos en los ejes de fecha). En los ejes que
    // empiezan en autorange la decide Plotly: cambia con el ancho del gráfico (relleno de los marcadores) y
    // al ocultar una serie en la leyenda, así que se actualiza mientras el eje siga en autorange.
    const completa = {}, automatico = {};
    Object.keys(fl).filter((k) => /^[xy]axis\d*$/.test(k)).forEach((k) => {
      const ax = fl[k];
      if (!ax || ax.fixedrange || !Array.isArray(ax.range) || typeof ax.r2l !== 'function') return;
      const a = ax.r2l(ax.range[0]), b = ax.r2l(ax.range[1]);
      if (Number.isFinite(a) && Number.isFinite(b) && a !== b) { completa[k] = { a, b }; automatico[k] = !!ax.autorange; }
    });
    const nombres = Object.keys(completa);
    if (!nombres.some((k) => k[0] === 'x') || !nombres.some((k) => k[0] === 'y')) return;

    const contenedor = gd.parentElement;
    if (getComputedStyle(contenedor).position === 'static') contenedor.style.position = 'relative';
    const boton = crear(`<button type="button" class="boton boton-pequeno boton-ver-todo" hidden title="Volver a la vista completa (también con doble clic sobre el gráfico)">${iconoFi('zoom-out')} Ver todo</button>`);
    contenedor.appendChild(boton);
    let ajustando = false, rueda = 0, cursor = null, cuadro = 0, temporizador = 0, temporizadorEtiqueta = 0, ultimaRueda = 0;

    // Acota [r0, r1] a la vista completa del eje sin cambiar su ancho (salvo que la supere)
    function acotar(k, r0, r1) {
      const f = completa[k];
      const lo0 = Math.min(f.a, f.b), hi0 = Math.max(f.a, f.b), ancho0 = hi0 - lo0;
      let lo = Math.min(r0, r1), hi = Math.max(r0, r1);
      const ancho = hi - lo, acercado = ancho < ancho0 * (1 - 1e-6);
      if (!acercado) { lo = lo0; hi = hi0; }
      else if (lo < lo0) { lo = lo0; hi = lo0 + ancho; }
      else if (hi > hi0) { hi = hi0; lo = hi0 - ancho; }
      // Se conserva el sentido del eje: la profundidad crece hacia abajo
      return { rango: f.a > f.b ? [hi, lo] : [lo, hi], acercado };
    }

    // Cambio de relayout para un eje acotado: la vista completa de un eje automático vuelve a autorange
    function cambioEje(cambios, k, ax, n) {
      if (!n.acercado && automatico[k]) cambios[`${k}.autorange`] = completa[k].a > completa[k].b ? 'reversed' : true;
      else cambios[`${k}.range`] = n.rango.map((v) => ax.l2r(v));
    }

    function estado() {
      const cambios = {};
      let acercado = false;
      nombres.forEach((k) => {
        const ax = gd._fullLayout && gd._fullLayout[k];
        if (!ax) return;
        const r0 = ax.r2l(ax.range[0]), r1 = ax.r2l(ax.range[1]);
        if (automatico[k] && ax.autorange) { completa[k] = { a: r0, b: r1 }; return; }
        const n = acotar(k, r0, r1);
        acercado = acercado || n.acercado;
        const tol = Math.abs(completa[k].a - completa[k].b) * 1e-6;
        if ((!n.acercado && automatico[k]) || Math.abs(n.rango[0] - r0) > tol || Math.abs(n.rango[1] - r1) > tol) cambioEje(cambios, k, ax, n);
      });
      return { cambios, acercado };
    }

    // Lo que se ve de los datos, en píxeles del contenedor: puntos de las líneas (cada 4 px) y rectángulos
    // de las barras y de los mapas de calor (con su peso), cada traza recortada a su propio subgráfico
    function datosVisibles() {
      const fl2 = gd._fullLayout, puntos = [], rects = [];
      (gd.calcdata || []).forEach((cd) => {
        const t = cd && cd[0] && cd[0].trace;
        if (!t || t.visible !== true) return;
        const xa = fl2[`xaxis${(t.xaxis || 'x').slice(1)}`], ya = fl2[`yaxis${(t.yaxis || 'y').slice(1)}`];
        if (!xa || !ya) return;
        const z = { l: gd.offsetLeft + xa._offset, t: gd.offsetTop + ya._offset };
        z.r = z.l + xa._length;
        z.b = z.t + ya._length;
        if (t.type === 'heatmap' || t.type === 'contour') { rects.push(Object.assign({ peso: 1000 }, z)); return; }
        const px = (v) => z.l + xa.c2p(v), py = (v) => z.t + ya.c2p(v);
        const dentro = (q) => q[0] >= z.l && q[0] <= z.r && q[1] >= z.t && q[1] <= z.b;
        if (t.type === 'bar') {
          const horizontal = t.orientation === 'h';
          cd.forEach((d) => {
            if (!Number.isFinite(d.p0) || !Number.isFinite(d.p1) || !Number.isFinite(d.b) || !Number.isFinite(d.s)) return;
            const [xs, ys] = horizontal ? [[px(d.b), px(d.b + d.s)], [py(d.p0), py(d.p1)]] : [[px(d.p0), px(d.p1)], [py(d.b), py(d.b + d.s)]];
            rects.push({ l: Math.max(Math.min(...xs), z.l), r: Math.min(Math.max(...xs), z.r), t: Math.max(Math.min(...ys), z.t), b: Math.min(Math.max(...ys), z.b), peso: 50 });
          });
          return;
        }
        // Tramos enteramente a un lado del subgráfico (fuera del zoom): no se muestrean
        const fuera = (a, b) => (a[0] < z.l && b[0] < z.l) || (a[0] > z.r && b[0] > z.r) || (a[1] < z.t && b[1] < z.t) || (a[1] > z.b && b[1] > z.b);
        let ant = null;
        cd.forEach((d) => {
          const p = [px(d.x), py(d.y)];
          if (!Number.isFinite(p[0]) || !Number.isFinite(p[1])) { ant = null; return; }
          if (ant && !fuera(ant, p)) {
            const n = Math.min(500, Math.ceil(Math.hypot(p[0] - ant[0], p[1] - ant[1]) / 4));
            for (let i = 1; i < n; i++) { const q = [ant[0] + (p[0] - ant[0]) * i / n, ant[1] + (p[1] - ant[1]) * i / n]; if (dentro(q)) puntos.push(q); }
          }
          if (dentro(p)) puntos.push(p);
          ant = p;
        });
      });
      return { puntos, rects: rects.filter((r) => r.l < r.r && r.t < r.b) };
    }

    // El botón va en un hueco libre: sobre el área de trazado, a la derecha, o en una de sus esquinas, sin
    // tapar el título, la leyenda, los rótulos (de los ejes y de las barras), las anotaciones (p. ej. la capa
    // de mezcla) ni la insignia de procedencia, y tapando los menos datos posibles. Conserva su sitio mientras
    // siga siendo de los mejores, para no saltar de esquina en esquina al acercar. Se ancla por la izquierda,
    // como el gráfico (que no se redimensiona al plegar la barra lateral).
    function colocarBoton() {
      clearTimeout(temporizador);
      const s = gd._fullLayout && gd._fullLayout._size;
      if (!s || boton.hidden) return;
      const ancho = boton.offsetWidth, alto = boton.offsetHeight, m = 8;
      const x0 = gd.offsetLeft + s.l, y0 = gd.offsetTop + s.t, x1 = x0 + s.w, y1 = y0 + s.h;
      const candidatos = [
        [x1 - ancho, y0 - alto - 6], [x1 - ancho - m, y0 + m], [x0 + m, y0 + m], [x0 + m, y1 - alto - m], [x1 - ancho - m, y1 - alto - m],
      ];
      const base = contenedor.getBoundingClientRect();
      const bx = base.left + contenedor.clientLeft, by = base.top + contenedor.clientTop;
      const ocupado = [...gd.querySelectorAll('.g-gtitle, .legend, .annotation, .xtick, .ytick, .y2tick, .g-xtitle, .g-ytitle, .g-x2title, .g-y2title, .colorbar, .bartext, .textpoint')]
        .concat([...contenedor.querySelectorAll('.insignia, .insignia-simulado, .marca-simulado, .modebar')])
        .map((el) => el.getBoundingClientRect()).filter((r) => r.width > 0 && r.height > 0)
        .map((r) => ({ l: r.left - bx, t: r.top - by, r: r.right - bx, b: r.bottom - by }));
      const choca = (x, y, o, h) => x < o.r + h && x + ancho > o.l - h && y < o.b + h && y + alto > o.t - h;
      const libre = ([x, y]) => x >= 0 && y >= 0 && x + ancho <= contenedor.clientWidth && !ocupado.some((o) => choca(x, y, o, 4));
      // Cuánto tapa de los datos: 0 si nada queda a menos de 4 px; lo que queda debajo pesa más que lo que roza
      let datos = null;
      const tapa = ([x, y]) => {
        datos = datos || datosVisibles();
        let n = 0;
        datos.rects.forEach((o) => { if (choca(x, y, o, 2)) n += choca(x, y, o, 0) ? o.peso : 1; });
        datos.puntos.forEach(([px, py]) => {
          if (px > x - 4 && px < x + ancho + 4 && py > y - 4 && py < y + alto + 4) n += px >= x && px <= x + ancho && py >= y && py <= y + alto ? 10 : 1;
        });
        return n;
      };
      const libres = candidatos.map((c, i) => i).filter((i) => libre(candidatos[i]));
      const previo = libres.includes(Number(boton.dataset.hueco)) ? Number(boton.dataset.hueco) : -1;
      let elegido = 1;
      if (previo >= 0 && tapa(candidatos[previo]) === 0) elegido = previo;
      else if (libres.length) {
        const puntos = libres.map((i) => tapa(candidatos[i]));
        const minimo = Math.min(...puntos);
        elegido = previo >= 0 && tapa(candidatos[previo]) === minimo ? previo : libres[puntos.indexOf(minimo)];
      }
      boton.dataset.hueco = String(elegido);
      boton.style.left = `${Math.round(candidatos[elegido][0])}px`;
      boton.style.top = `${Math.round(candidatos[elegido][1])}px`;
      revisarEtiqueta();   // la etiqueta de Plotly se redibuja sin mover el cursor (p. ej. tras la rueda)
    }

    // Mientras se gira la rueda el botón no se mueve; se recoloca al terminar el gesto
    function programarColocacion(ms) {
      clearTimeout(temporizador);
      temporizador = setTimeout(colocarBoton, ms);
    }

    function alCambiarRangos() {
      if (ajustando || !gd._fullLayout) return;
      const { cambios, acercado } = estado();
      boton.hidden = !acercado;
      if (!acercado) { delete boton.dataset.hueco; boton.classList.remove('bajo-etiqueta'); }
      if (performance.now() - ultimaRueda < 250) programarColocacion(250);
      else colocarBoton();
      if (!Object.keys(cambios).length) return;
      ajustando = true;
      Promise.resolve(window.Plotly.relayout(gd, cambios)).catch(() => {}).then(() => { ajustando = false; alCambiarRangos(); });
    }

    function verTodo() {
      const cambios = {};
      nombres.forEach((k) => {
        const ax = gd._fullLayout && gd._fullLayout[k];
        if (ax) cambioEje(cambios, k, ax, { acercado: false, rango: [completa[k].a, completa[k].b] });
      });
      window.Plotly.relayout(gd, cambios);
    }

    function ejesBajoCursor(x, y) {
      const caja = gd.getBoundingClientRect();
      return nombres.filter((k) => {
        const ax = gd._fullLayout[k];
        if (!ax) return false;
        const p = (k[0] === 'x' ? x - caja.left : y - caja.top) - ax._offset;
        return p >= 0 && p <= ax._length;
      });
    }

    function alGirarRueda(e) {
      if (boton.hidden || e.ctrlKey || !gd._fullLayout) return;
      const ejes = ejesBajoCursor(e.clientX, e.clientY);
      if (!ejes.some((k) => k[0] === 'x') || !ejes.some((k) => k[0] === 'y')) return;
      e.preventDefault();
      ultimaRueda = performance.now();
      rueda += e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
      cursor = { x: e.clientX, y: e.clientY, ejes };
      if (!cuadro) cuadro = requestAnimationFrame(zoomConRueda);
    }

    // Un paso de rueda (100 px) acerca o aleja un 22 % alrededor del cursor
    function zoomConRueda() {
      cuadro = 0;
      const delta = rueda;
      rueda = 0;
      if (!delta || ajustando || !gd._fullLayout) return;
      const factor = Math.exp(Math.max(-1, Math.min(1, delta * 0.002)));
      const caja = gd.getBoundingClientRect();
      const cambios = {};
      cursor.ejes.forEach((k) => {
        const ax = gd._fullLayout[k];
        if (!ax) return;
        const p = ax.p2l((k[0] === 'x' ? cursor.x - caja.left : cursor.y - caja.top) - ax._offset);
        const r0 = ax.r2l(ax.range[0]), r1 = ax.r2l(ax.range[1]);
        const n0 = p + (r0 - p) * factor, n1 = p + (r1 - p) * factor;
        if (Math.abs(n1 - n0) < Math.abs(completa[k].a - completa[k].b) / 1000) return;
        cambioEje(cambios, k, ax, acotar(k, n0, n1));
      });
      if (Object.keys(cambios).length) window.Plotly.relayout(gd, cambios);
    }

    // La etiqueta de Plotly al pasar el cursor queda debajo del botón: mientras se cruzan, el botón se vuelve
    // transparente. Sigue pulsable: al llevar el cursor encima, la etiqueta se va y el botón reaparece.
    function revisarEtiqueta() {
      if (boton.hidden) return;
      const r = boton.getBoundingClientRect();
      const tapada = [...gd.querySelectorAll('.hoverlayer > :not(.spikeline)')].some((el) => {
        const o = el.getBoundingClientRect();
        return o.width > 0 && r.left < o.right && r.right > o.left && r.top < o.bottom && r.bottom > o.top;
      });
      boton.classList.toggle('bajo-etiqueta', tapada);
    }
    function alMoverCursor() {
      clearTimeout(temporizadorEtiqueta);
      temporizadorEtiqueta = setTimeout(revisarEtiqueta, 80);   // Plotly dibuja la etiqueta cada 50 ms como mucho
    }
    function alQuitarEtiqueta() {
      clearTimeout(temporizadorEtiqueta);
      boton.classList.remove('bajo-etiqueta');
    }

    // Al cambiar de tamaño el contenedor (zoom del navegador, barra lateral) el botón se recoloca cuando
    // Plotly ya se ha ajustado al ancho nuevo
    const observador = typeof ResizeObserver === 'function' ? new ResizeObserver(() => programarColocacion(300)) : null;

    boton.addEventListener('click', verTodo);
    gd.on('plotly_relayout', alCambiarRangos);
    gd.on('plotly_restyle', alCambiarRangos);   // series ocultas o mostradas desde la leyenda
    gd.on('plotly_unhover', alQuitarEtiqueta);
    gd.addEventListener('wheel', alGirarRueda, { passive: false });
    gd.addEventListener('mousemove', alMoverCursor);
    gd.addEventListener('mouseleave', alQuitarEtiqueta);
    if (observador) observador.observe(contenedor);
    gd._zoomAcotado = {
      quitar() {
        if (cuadro) cancelAnimationFrame(cuadro);
        clearTimeout(temporizador);
        clearTimeout(temporizadorEtiqueta);
        if (observador) observador.disconnect();
        if (typeof gd.removeListener === 'function') {
          gd.removeListener('plotly_relayout', alCambiarRangos);
          gd.removeListener('plotly_restyle', alCambiarRangos);
          gd.removeListener('plotly_unhover', alQuitarEtiqueta);
        }
        gd.removeEventListener('wheel', alGirarRueda);
        gd.removeEventListener('mousemove', alMoverCursor);
        gd.removeEventListener('mouseleave', alQuitarEtiqueta);
        boton.remove();
        delete gd._zoomAcotado;
      },
    };
  }

  // Estilos propios de los módulos analíticos (una sola vez)
  function inyectarEstilos(id, css) {
    if (document.getElementById(id)) return;
    const estilo = document.createElement('style');
    estilo.id = id;
    estilo.textContent = css;
    document.head.appendChild(estilo);
  }

  inyectarEstilos('estilos-constructor-consulta', `
    .cc-paso { margin-bottom: 18px; }
    .cc-paso-titulo { display:flex; align-items:center; gap:10px; font-size:15px; font-weight:600; margin:0 0 10px; }
    .cc-paso-numero { display:inline-flex; width:24px; height:24px; border-radius:50%; background:var(--primario); color:var(--primario-texto, #fff); align-items:center; justify-content:center; font-size:12px; }
    .cc-compacto .cc-paso { margin-bottom: 12px; }
    .cc-compacto .cc-paso-titulo { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--texto-2); }
    .cc-compacto .cc-paso-numero { display:none; }
    .cc-fuentes { display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap:8px; margin-bottom:10px; }
    .cc-fuente { text-align:left; border:1px solid var(--borde); border-radius:var(--radio-sm, 6px); padding:8px 10px; background:var(--superficie); color:var(--texto); cursor:pointer; font:inherit; display:flex; flex-direction:column; gap:4px; }
    .cc-fuente:hover { border-color: var(--primario); }
    .cc-fuente.activa { border-color: var(--primario); box-shadow: 0 0 0 2px color-mix(in srgb, var(--primario) 25%, transparent); }
    .cc-fuente[disabled] { opacity:.5; cursor:not-allowed; }
    .cc-fuente-nombre { font-weight:600; font-size:13px; }
    .cc-fuente-estado { display:flex; align-items:center; gap:6px; font-size:11px; color:var(--texto-2); }
    .cc-segmentado { display:inline-flex; border:1px solid var(--borde); border-radius:var(--radio-sm, 6px); overflow:hidden; margin-bottom:10px; }
    .cc-segmentado button { font:inherit; font-size:13px; padding:6px 12px; background:var(--superficie); color:var(--texto); border:0; cursor:pointer; }
    .cc-segmentado button + button { border-left:1px solid var(--borde); }
    .cc-segmentado button.activa { background:var(--primario); color:var(--primario-texto, #fff); }
    /* --insignia-texto: --texto-2 sobre --superficie-2 queda en 4.47:1 en claro */
    .cc-segmentado button:disabled { background:var(--superficie-2, var(--superficie)); color:var(--insignia-texto, var(--texto-2)); cursor:not-allowed; }
    /* isolation: las capas de Leaflet (z-index 400 a 1000) no pasan por encima de la cabecera fija al desplazar la página */
    .cc-mapa { height:220px; border-radius:var(--radio-sm, 6px); border:1px solid var(--borde); margin-top:8px; overflow:hidden; isolation:isolate; }
    .cc-compacto .cc-mapa { height:170px; }
    .cc-mapa.dibujando .leaflet-container, .cc-mapa.dibujando { cursor: crosshair; }
    .cc-ayuda { font-size:12px; color:var(--texto-2); margin-top:6px; }
    .cc-atajos { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
    .cc-atajos .boton { padding:4px 10px; font-size:12px; }
    .cc-numeros { display:grid; grid-template-columns: 1fr 1fr; gap:8px; }
    .cc-numeros input { font-variant-numeric: tabular-nums; }
    .cc-numeros input[aria-invalid="true"] { border-color: var(--peligro); }
    .cc-acciones { display:grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap:8px; }
    .cc-acciones .boton { justify-content:center; }
    .cc-resumen { font-size:12px; color:var(--texto-2); margin-top:8px; line-height:1.5; }
    .cc-fila-fechas { display:grid; grid-template-columns: 1fr 1fr; gap:8px; }
    .cc-fecha-legible, .cc-coord-legible { font-size:11.5px; color:var(--texto-2); font-variant-numeric: tabular-nums; min-height:14px; }
    .cc-sugerencia { justify-content:flex-start; text-align:left; font-size:13px; white-space:normal; height:auto; line-height:1.35; }
    .texto-2 { color: var(--texto-2); }
  `);

  // ---------------------------------------------------------------------------
  // Componente ConstructorConsulta
  // ---------------------------------------------------------------------------

  class ConstructorConsulta {
    /**
     * @param {object} opciones
     *  compacto: para paneles laterales; sinMapa: sin mini mapa; soloPunto: el área es siempre un punto;
     *  fuentes: lista de ids de fuentes permitidas; variablesPermitidas(variable, fuente) → bool;
     *  tipoPeriodo: 'ambos' | 'rango' | 'fecha'; mostrarReferencia; sinProfundidad;
     *  mostrarModo (valor/anomalía); mostrarVectores; mostrarAgregacion;
     *  permitirVectores: incluye las variables vectoriales («Corrientes (vectores)») en el selector (solo Mapas);
     *  mostrarAcciones (botones Mapa/Serie/Perfil/Comparar); acciones: subconjunto de ['mapa','series','perfiles','comparar'];
     *  textoEnviar: etiqueta de un botón primario que llama alEnviar(valores);
     *  ayuda: texto bajo los botones; {rango, fecha} o función(valores) → texto (por defecto, la ayuda del mapa);
     *  alCambiar(valores): se llama ante cualquier cambio; valores: valores iniciales.
     */
    constructor(opciones = {}) {
      this.op = Object.assign({
        compacto: false, sinMapa: false, soloPunto: false, fuentes: null, variablesPermitidas: null,
        tipoPeriodo: 'ambos', mostrarReferencia: true, sinProfundidad: false,
        mostrarModo: false, mostrarVectores: false, mostrarAgregacion: false, permitirVectores: false,
        mostrarAcciones: true, acciones: ['mapa', 'series', 'perfiles', 'comparar'],
        textoEnviar: null, ayuda: null, alCambiar: null, alEnviar: null, valores: {},
      }, opciones);
      this.v = Object.assign(valoresPorDefecto(), this.op.valores || {});
      if (this.op.soloPunto && this.v.area.tipo !== 'punto') this.v.area = Object.assign({ tipo: 'punto' }, centroArea(this.v.area));
      if (this.op.tipoPeriodo === 'rango') this.v.tipoPeriodo = 'rango';
      if (this.op.tipoPeriodo === 'fecha') this.v.tipoPeriodo = 'fecha';
      this.mapa = null;
      this.capaArea = null;
      this.inicioDibujo = null;
      this.rectTemporal = null;
      this.raiz = null;
      this._validarFuenteVariable();
    }

    // --- Estado -------------------------------------------------------------

    valores() {
      const v = JSON.parse(JSON.stringify(this.v));
      v.profundidad = Number(v.profundidad ?? 0);
      return v;
    }

    establecer(valores) {
      Object.assign(this.v, valores || {});
      this._validarFuenteVariable();
      if (this.raiz) this._renderizar();
    }

    aHash(opciones = {}) { return hashDesdeValores(this.valores(), '', opciones); }
    aCuerpo(opciones = {}) { return cuerpoConsulta(this.valores(), opciones); }

    _fuentesPermitidas() {
      let fuentes = catalogo().fuentes || [];
      if (Array.isArray(this.op.fuentes)) fuentes = fuentes.filter((f) => this.op.fuentes.includes(f.id));
      return fuentes;
    }

    _variablesPermitidas(f) {
      let vars = (f && f.variables) || [];
      // Las variables vectoriales («Corrientes (vectores)») solo se ofrecen donde se pueden dibujar (Mapas);
      // en los demás módulos los vectores se activan con el conmutador del mapa.
      if (!this.op.permitirVectores) vars = vars.filter((x) => !x.es_vector);
      if (typeof this.op.variablesPermitidas === 'function') vars = vars.filter((x) => this.op.variablesPermitidas(x, f));
      return vars;
    }

    _validarFuenteVariable() {
      const fuentes = this._fuentesPermitidas();
      if (!fuentes.length) return;
      let f = fuentes.find((x) => x.id === this.v.fuente);
      if (!f) { f = fuentes.find((x) => x.habilitada !== false) || fuentes[0]; this.v.fuente = f.id; }
      const vars = this._variablesPermitidas(f);
      if (vars.length && !vars.find((x) => x.id === this.v.variable)) {
        this.v.variable = (vars.find((x) => x.habilitada !== false) || vars[0]).id;
      }
    }

    _cambio() {
      if (this.raiz) this._actualizarResumen();
      if (typeof this.op.alCambiar === 'function') this.op.alCambiar(this.valores());
    }

    // --- Montaje --------------------------------------------------------------

    montar(contenedor) {
      this.contenedor = contenedor;
      this._renderizar();
      return this;
    }

    destruir() {
      if (this.mapa) { try { this.mapa.remove(); } catch (e) { /* nada */ } this.mapa = null; }
      if (this.raiz && this.raiz.parentNode) this.raiz.parentNode.removeChild(this.raiz);
      this.raiz = null;
    }

    _renderizar() {
      if (this.mapa) { try { this.mapa.remove(); } catch (e) { /* nada */ } this.mapa = null; }
      const anterior = this.raiz;
      this.raiz = crear(`<div class="constructor-consulta ${this.op.compacto ? 'cc-compacto' : ''}"></div>`);
      this._seccionFuente();
      this._seccionArea();
      this._seccionPeriodo();
      if (this.op.mostrarAcciones || this.op.textoEnviar) this._seccionAcciones();
      this.resumen = crear('<div class="cc-resumen" aria-live="polite"></div>');
      this.raiz.appendChild(this.resumen);
      if (anterior && anterior.parentNode) anterior.parentNode.replaceChild(this.raiz, anterior);
      else this.contenedor.appendChild(this.raiz);
      this._actualizarResumen();
      if (!this.op.sinMapa) setTimeout(() => this._crearMapa(), 30);
    }

    _paso(numero, titulo) {
      const s = crear(`<section class="cc-paso"><h3 class="cc-paso-titulo"><span class="cc-paso-numero">${numero}</span><span>${esc(titulo)}</span></h3></section>`);
      this.raiz.appendChild(s);
      return s;
    }

    // --- Paso 1: fuente y variable ------------------------------------------------

    _seccionFuente() {
      const s = this._paso(1, 'Fuente y variable');
      const fuentes = this._fuentesPermitidas();
      const lista = crear('<div class="cc-fuentes" role="radiogroup" aria-label="Fuente de datos"></div>');
      if (!fuentes.length) lista.innerHTML = '<div class="vacio">El catálogo aún no está disponible.</div>';
      fuentes.forEach((f) => {
        const estado = (f.estado && f.estado.estado) || 'operativa';
        const deshabilitada = f.habilitada === false;
        const b = crear(`<button type="button" class="cc-fuente ${f.id === this.v.fuente ? 'activa' : ''}" role="radio" aria-checked="${f.id === this.v.fuente}" ${deshabilitada ? 'disabled' : ''} title="${esc(f.producto || f.nombre)}">
          <span class="cc-fuente-nombre">${esc(nombreCortoFuente(f.id))}</span>
          <span class="cc-fuente-estado"><span class="semaforo ${CLASE_ESTADO[estado] || 'verde'}" aria-hidden="true"></span>${esc(deshabilitada ? 'Deshabilitada' : (TEXTO_ESTADO[estado] || estado))} · corte ${esc(fmtFecha((f.estado && f.estado.fecha_corte) || fechaCorte()))}</span>
        </button>`);
        b.addEventListener('click', () => {
          if (deshabilitada) return;
          this.v.fuente = f.id;
          this._validarFuenteVariable();
          this._renderizar();
          this._cambio();
        });
        lista.appendChild(b);
      });
      s.appendChild(lista);

      const f = fuentes.find((x) => x.id === this.v.fuente);
      if (f && f.estado && f.estado.estado && f.estado.estado !== 'operativa') {
        s.appendChild(crear(`<div class="alerta alerta-aviso" style="margin-bottom:10px;font-size:12px">${esc(f.estado.mensaje || `La fuente está ${TEXTO_ESTADO[f.estado.estado] || f.estado.estado}.`)}</div>`));
      }

      const vars = this._variablesPermitidas(f);
      const campoVar = crear(`<div class="campo"><label for="cc-variable-${this._uid()}">Variable</label><select></select></div>`);
      const sel = campoVar.querySelector('select');
      sel.id = campoVar.querySelector('label').getAttribute('for');
      vars.forEach((x) => {
        const o = document.createElement('option');
        o.value = x.id;
        o.textContent = `${x.nombre}${x.unidad ? ` (${x.unidad})` : ''}${x.habilitada === false ? ' — deshabilitada' : ''}`;
        o.disabled = x.habilitada === false;
        sel.appendChild(o);
      });
      if (!vars.length) sel.appendChild(new Option('Sin variables disponibles', ''));
      // La selección se fija después de poblar las opciones y se vuelve a sincronizar con el estado:
      // así el selector nunca queda vacío aunque el valor no coincida con ninguna opción.
      sel.value = String(this.v.variable || '');
      if (!sel.value && vars.length) {
        const primera = vars.find((x) => x.habilitada !== false) || vars[0];
        sel.value = primera.id;
        this.v.variable = primera.id;
      }
      // Ayuda de la variable: se actualiza al elegir otra (antes quedaba la de la primera variable)
      const ayudaVar = crear('<div class="cc-ayuda"></div>');
      const ponerAyuda = (info) => {
        ayudaVar.textContent = (info && info.descripcion) || '';
        ayudaVar.style.display = info && info.descripcion ? '' : 'none';
      };
      sel.addEventListener('change', () => {
        this.v.variable = sel.value;
        const infoNueva = variable(this.v.fuente, this.v.variable);
        ponerAyuda(infoNueva);
        if (infoNueva && infoNueva.es_vector && this.op.mostrarVectores) {
          // Al elegir «Corrientes (vectores)» se dibujan las flechas sobre la magnitud
          this.v.vectores = true;
          const casilla = this.raiz && this.raiz.querySelector('input[data-vectores]');
          if (casilla) casilla.checked = true;
        }
        this._actualizarProfundidad();
        this._actualizarModo();
        this._cambio();
      });
      s.appendChild(campoVar);

      ponerAyuda(variable(this.v.fuente, this.v.variable));
      s.appendChild(ayudaVar);

      // Profundidad (solo variables 3D)
      this.campoProfundidad = crear(`<div class="campo"><label>Profundidad</label><select aria-label="Profundidad en metros"></select></div>`);
      const selProf = this.campoProfundidad.querySelector('select');
      (catalogo().profundidades || [0]).forEach((z) => {
        const o = new Option(`${fmtNum(z, 0)} m${z === 0 ? ' (superficie)' : ''}`, String(z));
        if (Number(z) === Number(this.v.profundidad)) o.selected = true;
        selProf.appendChild(o);
      });
      selProf.addEventListener('change', () => { this.v.profundidad = Number(selProf.value); this._cambio(); });
      s.appendChild(this.campoProfundidad);
      this._actualizarProfundidad();

      if (this.op.mostrarModo) {
        const modo = crear(`<div class="campo"><label>Mostrar</label><div class="cc-segmentado" role="radiogroup" aria-label="Valor o anomalía">
          <button type="button" data-modo="valor">Valor</button>
          <button type="button" data-modo="anomalia">Anomalía</button></div></div>`);
        modo.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
          if (b.disabled) return;
          this.v.modo = b.dataset.modo;
          this._actualizarModo();
          this._cambio();
        }));
        s.appendChild(modo);
        this.campoModo = modo;
        this._actualizarModo();
      }

      if (this.op.mostrarVectores) {
        const esCopernicus = this.v.fuente === 'copernicus_phy';
        const vec = crear(`<div class="campo"><label style="display:flex;align-items:center;gap:8px;${esCopernicus ? '' : 'opacity:.55'}">
          <input type="checkbox" data-vectores ${this.v.vectores ? 'checked' : ''} ${esCopernicus ? '' : 'disabled'}> Mostrar vectores de corriente${esCopernicus ? '' : ' (solo Copernicus)'}</label></div>`);
        vec.querySelector('input').addEventListener('change', (e) => { this.v.vectores = e.target.checked; this._cambio(); });
        s.appendChild(vec);
      }

      if (this.op.mostrarAgregacion) {
        const agr = crear(`<div class="campo"><label>Agregación</label><select aria-label="Agregación temporal">
          <option value="diaria">Diaria</option><option value="semanal">Semanal</option><option value="mensual">Mensual</option></select></div>`);
        agr.querySelector('select').value = this.v.agregacion || 'diaria';
        agr.querySelector('select').addEventListener('change', (e) => { this.v.agregacion = e.target.value; this._cambio(); });
        s.appendChild(agr);
      }
    }

    _actualizarProfundidad() {
      if (!this.campoProfundidad) return;
      const info = variable(this.v.fuente, this.v.variable);
      const visible = !this.op.sinProfundidad && info && info.tiene_profundidad;
      this.campoProfundidad.style.display = visible ? '' : 'none';
    }

    /**
     * Conmutador Valor/Anomalía. Con una variable vectorial («Corrientes (vectores)») la anomalía no está
     * disponible (la climatología oficial publica las componentes medias u y v, no la rapidez media; el
     * servidor responde 422): el modo vuelve a «Valor» y el botón «Anomalía» queda deshabilitado.
     */
    _actualizarModo() {
      if (!this.op.mostrarModo) return;
      const info = variable(this.v.fuente, this.v.variable);
      const vectorial = Boolean(info && info.es_vector);
      if (vectorial && this.v.modo === 'anomalia') this.v.modo = 'valor';
      if (!this.campoModo) return;
      this.campoModo.querySelectorAll('[data-modo]').forEach((b) => {
        const activa = b.dataset.modo === (this.v.modo === 'anomalia' ? 'anomalia' : 'valor');
        b.classList.toggle('activa', activa);
        if (b.dataset.modo === 'anomalia') {
          b.disabled = vectorial;
          if (vectorial) b.title = 'No disponible para corrientes'; else b.removeAttribute('title');
        }
      });
    }

    _uid() { this._contador = (this._contador || 0) + 1; return `${Math.random().toString(36).slice(2, 7)}-${this._contador}`; }

    // --- Paso 2: área ---------------------------------------------------------------

    _seccionArea() {
      const s = this._paso(2, 'Área');
      const recortes = catalogo().recortes || [];
      if (!this.op.soloPunto) {
        const seg = crear(`<div class="cc-segmentado" role="radiogroup" aria-label="Tipo de área">
          <button type="button" data-tipo="recorte">Recorte</button>
          <button type="button" data-tipo="bbox">Rectángulo</button>
          <button type="button" data-tipo="punto">Punto</button></div>`);
        seg.querySelectorAll('button').forEach((b) => {
          b.classList.toggle('activa', b.dataset.tipo === this.v.area.tipo);
          b.addEventListener('click', () => this._cambiarTipoArea(b.dataset.tipo));
        });
        s.appendChild(seg);
      }

      // Recorte
      this.panelRecorte = crear('<div class="campo"><label>Recorte del catálogo</label><select aria-label="Recorte"></select></div>');
      const selRec = this.panelRecorte.querySelector('select');
      const grupos = {};
      const nombreTipo = { area: 'Áreas', region_nino: 'Regiones Niño', provincia: 'Provincias', cuenca: 'Cuencas' };
      recortes.forEach((r) => { (grupos[r.tipo] = grupos[r.tipo] || []).push(r); });
      Object.keys(grupos).forEach((t) => {
        const og = document.createElement('optgroup');
        og.label = nombreTipo[t] || t;
        grupos[t].forEach((r) => {
          const o = new Option(r.nombre, r.id);
          if (this.v.area.tipo === 'recorte' && this.v.area.recorte === r.id) o.selected = true;
          og.appendChild(o);
        });
        selRec.appendChild(og);
      });
      if (!recortes.length) selRec.appendChild(new Option('Región Niño 1+2', 'nino12'));
      selRec.addEventListener('change', () => { this.v.area = { tipo: 'recorte', recorte: selRec.value }; this._dibujarArea(); this._cambio(); });
      s.appendChild(this.panelRecorte);

      // Rectángulo
      const b = this.v.area.tipo === 'bbox' ? this.v.area.bbox : bboxArea(this.v.area);
      this.panelBbox = crear(`<div><div class="cc-numeros">
        <div class="campo"><label>Lon. mín. (°)</label><input type="text" inputmode="decimal" autocomplete="off" spellcheck="false" data-min="-180" data-max="180" data-i="0" value="${b[0]}"></div>
        <div class="campo"><label>Lat. mín. (°)</label><input type="text" inputmode="decimal" autocomplete="off" spellcheck="false" data-min="-90" data-max="90" data-i="1" value="${b[1]}"></div>
        <div class="campo"><label>Lon. máx. (°)</label><input type="text" inputmode="decimal" autocomplete="off" spellcheck="false" data-min="-180" data-max="180" data-i="2" value="${b[2]}"></div>
        <div class="campo"><label>Lat. máx. (°)</label><input type="text" inputmode="decimal" autocomplete="off" spellcheck="false" data-min="-90" data-max="90" data-i="3" value="${b[3]}"></div>
      </div><div class="cc-ayuda">Longitudes oeste y latitudes sur en negativo. ${this.op.sinMapa ? '' : 'También puede arrastrar sobre el mapa para dibujar el rectángulo.'}</div></div>`);
      this.panelBbox.querySelectorAll('input').forEach((inp) => inp.addEventListener('change', () => {
        normalizarCoordenada(inp);
        const nuevo = [...this.panelBbox.querySelectorAll('input')].map(leerCoordenada);
        if (nuevo.every(Number.isFinite)) {
          this.v.area = { tipo: 'bbox', bbox: [Math.min(nuevo[0], nuevo[2]), Math.min(nuevo[1], nuevo[3]), Math.max(nuevo[0], nuevo[2]), Math.max(nuevo[1], nuevo[3])] };
          this._dibujarArea(); this._cambio();
        }
      }));
      s.appendChild(this.panelBbox);

      // Punto
      const c = this.v.area.tipo === 'punto' ? this.v.area : centroArea(this.v.area);
      this.panelPunto = crear(`<div><div class="cc-numeros">
        <div class="campo"><label>Latitud (°)</label><input type="text" inputmode="decimal" autocomplete="off" spellcheck="false" data-min="-90" data-max="90" data-c="lat" value="${c.lat}"><small class="cc-coord-legible" data-leg="lat"></small></div>
        <div class="campo"><label>Longitud (°)</label><input type="text" inputmode="decimal" autocomplete="off" spellcheck="false" data-min="-180" data-max="180" data-c="lon" value="${c.lon}"><small class="cc-coord-legible" data-leg="lon"></small></div>
      </div><div class="cc-ayuda">${this.op.sinMapa ? 'Latitud sur y longitud oeste en negativo.' : 'Haga clic en el mapa para marcar el punto.'}</div></div>`);
      // Campos de texto y no type="number": Chromium muestra el control numérico nativo con el
      // separador decimal del idioma del navegador y de la configuración regional de Windows
      // (coma en es-EC por defecto) e ignora lang; RNF-01 exige el punto. leerCoordenada acepta
      // punto o coma. Al lado se muestra la coordenada con el formato del SOME («1.85° S»).
      this._legibleCoord = () => {
        const lat = leerCoordenada(this.panelPunto.querySelector('[data-c="lat"]'));
        const lon = leerCoordenada(this.panelPunto.querySelector('[data-c="lon"]'));
        this.panelPunto.querySelector('[data-leg="lat"]').textContent = Number.isFinite(lat) ? fmtCoord(lat, true) : '';
        this.panelPunto.querySelector('[data-leg="lon"]').textContent = Number.isFinite(lon) ? fmtCoord(lon, false) : '';
      };
      this.panelPunto.querySelectorAll('input').forEach((inp) => {
        inp.addEventListener('input', () => this._legibleCoord());
        inp.addEventListener('change', () => {
          normalizarCoordenada(inp);
          const lat = leerCoordenada(this.panelPunto.querySelector('[data-c="lat"]'));
          const lon = leerCoordenada(this.panelPunto.querySelector('[data-c="lon"]'));
          if (Number.isFinite(lat) && Number.isFinite(lon)) { this.v.area = { tipo: 'punto', lat, lon }; this._dibujarArea(); this._cambio(); }
        });
      });
      this._legibleCoord();
      s.appendChild(this.panelPunto);

      if (!this.op.sinMapa) {
        this.divMapa = crear('<div class="cc-mapa" role="application" aria-label="Mini mapa para elegir el área"></div>');
        s.appendChild(this.divMapa);
      }
      this._mostrarPanelArea();
    }

    _cambiarTipoArea(tipo) {
      if (tipo === this.v.area.tipo) return;
      if (tipo === 'recorte') {
        const sel = this.panelRecorte.querySelector('select');
        this.v.area = { tipo: 'recorte', recorte: sel.value || 'nino12' };
      } else if (tipo === 'bbox') {
        const nums = [...this.panelBbox.querySelectorAll('input')].map(leerCoordenada);
        this.v.area = { tipo: 'bbox', bbox: nums.every(Number.isFinite) ? nums : bboxArea(this.v.area) };
      } else {
        const lat = leerCoordenada(this.panelPunto.querySelector('[data-c="lat"]'));
        const lon = leerCoordenada(this.panelPunto.querySelector('[data-c="lon"]'));
        this.v.area = Number.isFinite(lat) && Number.isFinite(lon) ? { tipo: 'punto', lat, lon } : Object.assign({ tipo: 'punto' }, centroArea(this.v.area));
      }
      this.raiz.querySelectorAll('[data-tipo]').forEach((b) => b.classList.toggle('activa', b.dataset.tipo === tipo));
      this._mostrarPanelArea();
      this._dibujarArea();
      this._cambio();
    }

    _mostrarPanelArea() {
      const t = this.v.area.tipo;
      this.panelRecorte.style.display = t === 'recorte' ? '' : 'none';
      this.panelBbox.style.display = t === 'bbox' ? '' : 'none';
      this.panelPunto.style.display = t === 'punto' ? '' : 'none';
      if (this.divMapa) this.divMapa.classList.toggle('dibujando', t === 'bbox');
      if (this.mapa) {
        if (t === 'bbox') this.mapa.dragging.disable(); else this.mapa.dragging.enable();
      }
    }

    _crearMapa() {
      if (!this.divMapa || !this.divMapa.isConnected) return;
      if (!leafletDisponible()) {
        this.divMapa.innerHTML = '<div class="vacio" style="padding:12px;font-size:12px">El mini mapa no está disponible (no se cargó Leaflet).</div>';
        return;
      }
      const L = window.L;
      const area = catalogo().bbox_area || [-95, -6, -78, 6];
      this.mapa = L.map(this.divMapa, { zoomControl: !this.op.compacto, attributionControl: true, minZoom: 4, maxZoom: 9, scrollWheelZoom: false });
      this.mapa.attributionControl.setPrefix(false);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap', maxZoom: 19 }).addTo(this.mapa);
      this.mapa.fitBounds([[area[1], area[0]], [area[3], area[2]]], { padding: [4, 4] });
      this.capaArea = L.layerGroup().addTo(this.mapa);
      this._mostrarPanelArea();

      // Dibujo del rectángulo arrastrando (solo en modo «Rectángulo»)
      this.mapa.on('mousedown', (e) => {
        if (this.v.area.tipo !== 'bbox') return;
        this.inicioDibujo = e.latlng;
        if (this.rectTemporal) this.capaArea.removeLayer(this.rectTemporal);
        this.rectTemporal = L.rectangle(L.latLngBounds(e.latlng, e.latlng), { color: '#F4A261', weight: 2, dashArray: '4 3', fillOpacity: 0.1 }).addTo(this.capaArea);
        L.DomEvent.preventDefault(e.originalEvent);
      });
      this.mapa.on('mousemove', (e) => {
        if (!this.inicioDibujo || !this.rectTemporal) return;
        this.rectTemporal.setBounds(L.latLngBounds(this.inicioDibujo, e.latlng));
      });
      const terminar = (latlng) => {
        if (!this.inicioDibujo) return;
        const b = L.latLngBounds(this.inicioDibujo, latlng);
        this.inicioDibujo = null;
        if (this.rectTemporal) { this.capaArea.removeLayer(this.rectTemporal); this.rectTemporal = null; }
        if (Math.abs(b.getWest() - b.getEast()) < 0.2 || Math.abs(b.getSouth() - b.getNorth()) < 0.2) return;
        const r = (x) => Math.round(x * 4) / 4; // a la malla de 0.25°
        let bbox = [r(b.getWest()), r(b.getSouth()), r(b.getEast()), r(b.getNorth())];
        // El rectángulo se recorta al área de estudio (6° N a 6° S, 95° O a 78° O)
        const limite = catalogo().bbox_area || [-95, -6, -78, 6];
        const recortado = [Math.max(bbox[0], limite[0]), Math.max(bbox[1], limite[1]), Math.min(bbox[2], limite[2]), Math.min(bbox[3], limite[3])];
        if (recortado[0] >= recortado[2] || recortado[1] >= recortado[3]) { toast('El rectángulo queda fuera del área de estudio.', 'aviso'); return; }
        if (recortado.some((x, i) => x !== bbox[i])) toast('El rectángulo se ajustó al área de estudio.', 'info');
        bbox = recortado;
        this.v.area = { tipo: 'bbox', bbox };
        this.panelBbox.querySelectorAll('input').forEach((inp) => { inp.value = this.v.area.bbox[Number(inp.dataset.i)]; inp.removeAttribute('aria-invalid'); });
        this._dibujarArea();
        this._cambio();
      };
      this.mapa.on('mouseup', (e) => terminar(e.latlng));
      this.mapa.on('mouseout', (e) => { if (this.inicioDibujo) terminar(e.latlng); });

      // Clic para el punto
      this.mapa.on('click', (e) => {
        if (this.v.area.tipo !== 'punto') return;
        const lat = Math.round(e.latlng.lat * 20) / 20, lon = Math.round(e.latlng.lng * 20) / 20;
        this.v.area = { tipo: 'punto', lat, lon };
        this.panelPunto.querySelectorAll('input').forEach((inp) => { inp.value = inp.dataset.c === 'lat' ? lat : lon; inp.removeAttribute('aria-invalid'); });
        if (this._legibleCoord) this._legibleCoord();
        this._dibujarArea();
        this._cambio();
      });

      this._dibujarArea();
      setTimeout(() => { if (this.mapa) this.mapa.invalidateSize(); }, 150);
    }

    _dibujarArea() {
      if (!this.mapa || !this.capaArea) return;
      const L = window.L;
      this.capaArea.clearLayers();
      this.rectTemporal = null;
      const a = this.v.area;
      if (a.tipo === 'punto') {
        L.circleMarker([a.lat, a.lon], { radius: 7, color: '#F4A261', weight: 2, fillColor: '#F4A261', fillOpacity: 0.7 }).addTo(this.capaArea);
      } else {
        const b = bboxArea(a);
        L.rectangle([[b[1], b[0]], [b[3], b[2]]], { color: a.tipo === 'recorte' ? '#0B4F6C' : '#F4A261', weight: 2, fillOpacity: 0.08 }).addTo(this.capaArea);
      }
    }

    // --- Paso 3: periodo ------------------------------------------------------------

    _seccionPeriodo() {
      const s = this._paso(3, 'Periodo');
      if (this.op.tipoPeriodo === 'ambos') {
        const seg = crear(`<div class="cc-segmentado" role="radiogroup" aria-label="Tipo de periodo">
          <button type="button" data-tp="rango" class="${this.v.tipoPeriodo === 'rango' ? 'activa' : ''}">Rango de fechas</button>
          <button type="button" data-tp="fecha" class="${this.v.tipoPeriodo === 'fecha' ? 'activa' : ''}">Fecha única</button></div>`);
        seg.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
          this.v.tipoPeriodo = b.dataset.tp;
          seg.querySelectorAll('button').forEach((x) => x.classList.toggle('activa', x === b));
          this._mostrarPanelPeriodo();
          this._cambio();
        }));
        s.appendChild(seg);
      }
      const min = fechaInicio(), max = fechaCorte();
      // Los atajos («Últimos 30 días», «Último dato», «Hace 7 días»…) terminan en la fecha de corte de la
      // fuente elegida (NOAA OISST y CHIRPS: último día en caché; Copernicus Marine: último día de análisis),
      // no en la del sistema (la de NOAA OISST): así el mapa, su barra de tiempo y la fecha de las
      // estadísticas muestran el mismo día
      const corteFuente = this._corteFuente();
      this.panelRango = crear(`<div><div class="cc-fila-fechas">
        <div class="campo"><label>Desde</label><input type="date" data-f="inicio" min="${min}" max="${max}" value="${esc(this.v.periodo.inicio)}"></div>
        <div class="campo"><label>Hasta</label><input type="date" data-f="fin" min="${min}" max="${max}" value="${esc(this.v.periodo.fin)}"></div>
      </div><div class="cc-atajos" role="group" aria-label="Atajos de periodo"></div></div>`);
      // Fecha legible dd/mm/aaaa junto a cada control (el calendario nativo depende del idioma del navegador)
      const legibles = [...this.panelRango.querySelectorAll('input[type="date"]')].map((inp) => anotarFecha(inp));
      const refrescarLegibles = () => legibles.forEach((f) => { if (typeof f === 'function') f(); });
      this.panelRango.querySelectorAll('input').forEach((inp) => inp.addEventListener('change', () => {
        const ini = this.panelRango.querySelector('[data-f="inicio"]').value;
        const fin = this.panelRango.querySelector('[data-f="fin"]').value;
        if (esIso(ini) && esIso(fin)) { this.v.periodo = { inicio: minIso(ini, fin), fin: maxIso(ini, fin) }; this._cambio(); }
      }));
      const atajos = this.panelRango.querySelector('.cc-atajos');
      atajosPeriodo(corteFuente).forEach((a) => {
        const b = crear(`<button type="button" class="boton boton-secundario">${esc(a.texto)}</button>`);
        b.addEventListener('click', () => {
          this.v.periodo = Object.assign({}, a.periodo);
          this.v.tipoPeriodo = 'rango';
          this.panelRango.querySelector('[data-f="inicio"]').value = a.periodo.inicio;
          this.panelRango.querySelector('[data-f="fin"]').value = a.periodo.fin;
          refrescarLegibles();
          this.raiz.querySelectorAll('[data-tp]').forEach((x) => x.classList.toggle('activa', x.dataset.tp === 'rango'));
          this._mostrarPanelPeriodo();
          this._cambio();
        });
        atajos.appendChild(b);
      });
      s.appendChild(this.panelRango);

      this.panelFecha = crear(`<div><div class="campo"><label>Fecha</label><input type="date" min="${min}" max="${max}" value="${esc(this.v.fecha)}"></div>
        <div class="cc-atajos"><button type="button" class="boton boton-secundario" data-ff="corte">Último dato (${esc(fmtFecha(corteFuente))})</button>
        <button type="button" class="boton boton-secundario" data-ff="-7">Hace 7 días</button><button type="button" class="boton boton-secundario" data-ff="-30">Hace 30 días</button></div></div>`);
      const inpFecha = this.panelFecha.querySelector('input[type="date"]');
      const legibleFecha = anotarFecha(inpFecha);
      inpFecha.addEventListener('change', () => { if (esIso(inpFecha.value)) { this.v.fecha = inpFecha.value; this._cambio(); } });
      this.panelFecha.querySelectorAll('[data-ff]').forEach((b) => b.addEventListener('click', () => {
        this.v.fecha = b.dataset.ff === 'corte' ? corteFuente : sumarDias(corteFuente, Number(b.dataset.ff));
        inpFecha.value = this.v.fecha;
        if (typeof legibleFecha === 'function') legibleFecha();
        this._cambio();
      }));
      s.appendChild(this.panelFecha);
      this._mostrarPanelPeriodo();

      if (this.op.mostrarReferencia) {
        const ref = crear('<div class="campo"><label>Periodo de referencia (climatología)</label><select aria-label="Periodo de referencia"></select></div>');
        const sel = ref.querySelector('select');
        (catalogo().periodos_referencia || ['1991-2020']).forEach((p) => {
          const o = new Option(`${p}${p === '1991-2020' ? ' (estándar OMM)' : ''}`, p);
          if (p === this.v.periodo_referencia) o.selected = true;
          sel.appendChild(o);
        });
        sel.addEventListener('change', () => { this.v.periodo_referencia = sel.value; this._cambio(); });
        s.appendChild(ref);
      }
    }

    /** Fecha de corte de la fuente elegida (estado del catálogo), acotada al periodo de la demo. */
    _corteFuente() {
      const f = fuente(this.v.fuente);
      const corte = f && f.estado && f.estado.fecha_corte;
      return esIso(corte) && corte >= fechaInicio() ? minIso(corte, fechaCorte()) : fechaCorte();
    }

    _mostrarPanelPeriodo() {
      const esRango = this.v.tipoPeriodo !== 'fecha';
      this.panelRango.style.display = esRango ? '' : 'none';
      this.panelFecha.style.display = esRango ? 'none' : '';
    }

    // --- Paso 4: acciones -----------------------------------------------------------

    _seccionAcciones() {
      const s = this._paso(4, this.op.textoEnviar ? 'Consultar' : 'Qué quiero ver');
      const caja = crear('<div class="cc-acciones"></div>');
      if (this.op.textoEnviar) {
        const b = crear(`<button type="button" class="boton boton-primario">${esc(this.op.textoEnviar)}</button>`);
        b.addEventListener('click', () => { if (typeof this.op.alEnviar === 'function') this.op.alEnviar(this.valores()); });
        caja.appendChild(b);
      }
      if (this.op.mostrarAcciones) {
        const defs = {
          mapa: { texto: 'Mapa', clase: 'boton-primario' },
          series: { texto: 'Serie de tiempo', clase: 'boton-primario' },
          perfiles: { texto: 'Perfil / cortina', clase: 'boton-secundario' },
          comparar: { texto: 'Comparar', clase: 'boton-secundario' },
        };
        const icono = (id) => (typeof SOME.icono === 'function' ? SOME.icono(id) : '');
        (this.op.acciones || []).forEach((id) => {
          const d = defs[id];
          if (!d) return;
          const b = crear(`<button type="button" class="boton ${d.clase}" data-destino="${id}">${icono(id)}<span>${esc(d.texto)}</span></button>`);
          b.addEventListener('click', () => this.irA(id));
          caja.appendChild(b);
        });
      }
      s.appendChild(caja);
      this.ayudaAcciones = crear('<div class="cc-ayuda"></div>');
      s.appendChild(this.ayudaAcciones);
    }

    /** Navega al módulo destino con los parámetros serializados en el hash. */
    irA(destino) {
      const v = this.valores();
      if (destino === 'mapa') {
        // Con un rango, el mapa abre en el promedio del periodo (la fecha solo viaja con «Fecha única»)
        irA('mapa', hashDesdeValores(v, '', { incluirModo: true, incluirVectores: true }));
      } else if (destino === 'series') {
        irA('series', hashDesdeValores(v, '', { forzarPeriodo: true, incluirAgregacion: true }));
      } else if (destino === 'perfiles') {
        const c = centroArea(v.area);
        const esCop = v.fuente === 'copernicus_phy' && ['thetao', 'so'].includes(v.variable);
        if (!esCop) toast('Los perfiles usan temperatura o salinidad de Copernicus; se eligió temperatura del agua.', 'info');
        if (v.area.tipo !== 'punto') toast(`Se usará el centro del área: ${textoArea({ tipo: 'punto', lat: c.lat, lon: c.lon })}.`, 'info');
        irA('perfiles', {
          fuente: 'copernicus_phy', variable: esCop ? v.variable : 'thetao',
          area: serializarArea({ tipo: 'punto', lat: c.lat, lon: c.lon }),
          fecha: v.tipoPeriodo === 'fecha' ? v.fecha : v.periodo.fin,
          periodo: serializarPeriodo(v.tipoPeriodo === 'fecha' ? { inicio: sumarDias(v.fecha, -89), fin: v.fecha } : v.periodo),
          referencia: v.periodo_referencia,
        });
      } else if (destino === 'comparar') {
        irA('comparar', hashDesdeValores(v, 'a_', { forzarPeriodo: true, incluirReferencia: false, incluirProfundidad: true }));
      }
    }

    _actualizarResumen() {
      if (!this.resumen) return;
      const v = this.v;
      const info = variable(v.fuente, v.variable);
      const prof = info && info.tiene_profundidad && !this.op.sinProfundidad ? ` a ${fmtNum(v.profundidad, 0)} m` : '';
      const cuando = v.tipoPeriodo === 'fecha' ? `el ${fmtFecha(v.fecha)}` : `del ${fmtFecha(v.periodo.inicio)} al ${fmtFecha(v.periodo.fin)}`;
      this.resumen.textContent = `${nombreVariable(v.fuente, v.variable)}${prof} · ${nombreCortoFuente(v.fuente)} · ${textoArea(v.area)} · ${cuando}`;
      if (this.ayudaAcciones) {
        // La ayuda depende del módulo que incrusta el constructor (Mapas, Series, Perfiles…)
        const ayuda = this.op.ayuda;
        let texto;
        if (typeof ayuda === 'function') texto = ayuda(this.valores());
        else if (ayuda && typeof ayuda === 'object') texto = v.tipoPeriodo === 'fecha' ? (ayuda.fecha || ayuda.rango || '') : (ayuda.rango || '');
        else if (typeof ayuda === 'string') texto = ayuda;
        else {
          texto = v.tipoPeriodo === 'fecha'
            ? 'Con una fecha única, el mapa muestra ese día y la serie de tiempo, los 90 días anteriores.'
            : 'El mapa mostrará el promedio del periodo y una barra de tiempo para recorrerlo día a día.';
        }
        this.ayudaAcciones.textContent = texto || '';
        this.ayudaAcciones.style.display = texto ? '' : 'none';
      }
    }
  }

  SOME.componentes.ConstructorConsulta = ConstructorConsulta;

  SOME.componentes.comunes = {
    esc, crear, fmtFecha, fmtFechaHora, fmtNum, fmtAnomalia, toast, abrirModal, cerrarModal, iconoFi,
    aUtc, aIso, sumarDias, sumarMeses, diasEntre, minIso, maxIso, esIso, MESES_LARGOS, MESES_CORTOS,
    gradienteCss, PALETAS_RESPALDO,
    asegurarCatalogo, catalogo, fuente, variable, recorte, nombreFuente, nombreVariable, unidadVariable, nombreCortoFuente,
    fechaCorte, fechaInicio, mensajeConsultando, TEXTO_ESTADO, CLASE_ESTADO,
    atajosPeriodo, periodoPorDefecto,
    serializarArea, analizarArea, textoArea, centroArea, bboxArea, fmtCoord,
    serializarPeriodo, analizarPeriodo, textoPeriodo,
    valoresPorDefecto, valoresDesdeHash, hashDesdeValores, cuerpoConsulta, nombreArchivo,
    irA, mostrarCarga, mostrarError, mostrarVacio, insigniaSimulado, insigniaProcedencia, piezaCredito, textoReferencia,
    guardarVista, cargarVista, descargarArchivo, exportar, etiquetaFormato, abrirPdfEnPestana, anotarFecha, tokenSesion,
    coloresTema, plotlyBase, plotlyConfig, plotlyDisponible, leafletDisponible, purgarPlotly, zoomAcotado, inyectarEstilos,
  };

  // ---------------------------------------------------------------------------
  // Módulo «Consultas» (página completa del constructor)
  // ---------------------------------------------------------------------------

  let constructor = null;

  SOME.registrar('consultas', {
    titulo: 'Consultas',
    icono: 'consultas',
    grupo: 'Analizar',
    async montar(contenedor, params) {
      await asegurarCatalogo();
      contenedor.innerHTML = '';
      const pagina = crear(`<div class="modulo-consultas">
        <div class="cuadricula col-3" style="align-items:start">
          <div class="tarjeta" style="grid-column: span 2">
            <div class="fila" style="justify-content:space-between;align-items:center;margin-bottom:8px">
              <h2 class="tarjeta-titulo" style="margin:0">Constructor de consultas</h2>
            </div>
            <p class="texto-2" style="margin:0 0 14px;font-size:13px">Elija la fuente y la variable, el área, el periodo y qué desea ver. No hace falta programar: los parámetros se guardan en la dirección y puede compartirlos o guardarlos como vista.</p>
            <div class="cc-contenedor"></div>
          </div>
          <div style="display:flex;flex-direction:column;gap:12px">
            <div class="tarjeta">
              <h3 class="tarjeta-titulo">Fuentes disponibles</h3>
              <div class="cc-estado-fuentes" style="display:flex;flex-direction:column;gap:8px"></div>
            </div>
            <div class="tarjeta">
              <h3 class="tarjeta-titulo">Consultas sugeridas</h3>
              <div class="cc-sugerencias" style="display:flex;flex-direction:column;gap:6px"></div>
            </div>
          </div>
        </div>
      </div>`);
      contenedor.appendChild(pagina);
      pagina.querySelector('.tarjeta-titulo').insertAdjacentElement('afterend', insigniaSimulado(catalogo()));

      // Estado de fuentes
      const estadoCaja = pagina.querySelector('.cc-estado-fuentes');
      (catalogo().fuentes || []).forEach((f) => {
        const e = f.estado || {};
        const estado = e.estado || 'operativa';
        estadoCaja.appendChild(crear(`<div style="display:flex;gap:10px;align-items:flex-start;font-size:13px">
          <span class="semaforo ${CLASE_ESTADO[estado] || 'verde'}" aria-hidden="true" style="margin-top:4px"></span>
          <div style="min-width:0;overflow-wrap:anywhere"><div style="font-weight:600">${esc(f.nombre)} <span class="insignia ${f.datos_reales ? 'insignia-real' : 'insignia-simulado'}" style="font-size:10.5px;padding:0 7px" title="${esc(f.insignia || '')}">${f.datos_reales ? 'Real' : 'Simulada'}</span></div>
          <div class="texto-2" style="font-size:12px">${esc(TEXTO_ESTADO[estado] || estado)} · fecha de corte ${esc(fmtFecha(e.fecha_corte || fechaCorte()))}${e.latencia_ms ? ` · ${fmtNum(e.latencia_ms, 0)} ms` : ''}</div>
          ${e.mensaje && estado !== 'operativa' ? `<div class="texto-2" style="font-size:12px">${esc(e.mensaje)}</div>` : ''}</div></div>`));
      });
      if (!(catalogo().fuentes || []).length) estadoCaja.innerHTML = '<div class="vacio">Sin información del catálogo.</div>';

      // Sugerencias (consultas típicas del INOCAR)
      const corte = fechaCorte();
      const anio = corte.slice(0, 4);
      // Copernicus Marine tiene su propia fecha de corte (último día de análisis si es real)
      const fuenteCop = fuente('copernicus_phy');
      const corteCop = (fuenteCop && fuenteCop.estado && fuenteCop.estado.fecha_corte && fuenteCop.estado.fecha_corte >= fechaInicio())
        ? minIso(fuenteCop.estado.fecha_corte, corte) : corte;
      const sugerencias = [
        { texto: 'Anomalía de SST en Niño 1+2 — últimos 12 meses', modulo: 'mapa', params: { fuente: 'noaa_oisst', variable: 'sst', area: 'recorte:nino12', periodo: serializarPeriodo(periodoPorDefecto()), fecha: corte, modo: 'anomalia' } },
        { texto: 'Serie de SST en el golfo de Guayaquil — este año', modulo: 'series', params: { fuente: 'noaa_oisst', variable: 'sst', area: 'recorte:golfo_guayaquil', periodo: `${anio}-01-01,${corte}`, agregacion: 'diaria' } },
        { texto: 'Perfil térmico en 85° O, 2° S — último dato', modulo: 'perfiles', params: { fuente: 'copernicus_phy', variable: 'thetao', area: 'punto:-2,-85', fecha: corteCop, periodo: `${sumarDias(corteCop, -89)},${corteCop}` } },
        { texto: 'Lluvia acumulada en Guayas — febrero de este año', modulo: 'series', params: { fuente: 'chirps_v3', variable: 'precip', area: 'recorte:guayas', periodo: `${anio}-02-01,${minIso(sumarDias(`${anio}-03-01`, -1), corte)}`, agregacion: 'diaria' } },
        { texto: 'Corrientes superficiales de Copernicus — último dato', modulo: 'mapa', params: { fuente: 'copernicus_phy', variable: 'corrientes', area: 'recorte:area_estudio', fecha: corteCop, profundidad: '0', vectores: '1' } },
      ];
      const sugCaja = pagina.querySelector('.cc-sugerencias');
      sugerencias.forEach((s) => {
        const b = crear(`<button type="button" class="boton boton-secundario cc-sugerencia" title="${esc(s.texto)}">${esc(s.texto)}</button>`);
        b.addEventListener('click', () => irA(s.modulo, s.params));
        sugCaja.appendChild(b);
      });

      // Constructor
      let valores = valoresDesdeHash(params);
      if (params && params.vista) {
        try {
          const vista = await cargarVista(params.vista);
          if (vista) { valores = valoresDesdeHash(vista.parametros); toast(`Vista «${vista.nombre}» cargada.`, 'exito'); }
        } catch (e) { toast('No se pudo cargar la vista indicada.', 'error'); }
      }
      constructor = new ConstructorConsulta({ valores, tipoPeriodo: 'ambos', mostrarAcciones: true });
      constructor.montar(pagina.querySelector('.cc-contenedor'));
    },
    desmontar() {
      if (constructor) { constructor.destruir(); constructor = null; }
    },
  });
})();
