/* ============================================================
   SOME — módulo Panel personal (#/panel)
   Cuadrícula de widgets configurables: agregar, configurar, quitar,
   cambiar ancho, guardar (PUT /api/panel) y restablecer.
   Los datos de cada widget se piden con POST /api/panel/widget/datos.
   ============================================================ */
(function () {
  'use strict';
  const SOME = window.SOME;
  const util = window.util;
  const api = window.api;

  /* ---------- Catálogo local de tipos (respaldo si el servidor no describe el esquema) ---------- */
  const CAMPO_FUENTE = { id: 'fuente', etiqueta: 'Fuente', tipo: 'fuente', por_defecto: 'noaa_oisst' };
  const CAMPO_VARIABLE = { id: 'variable', etiqueta: 'Variable', tipo: 'variable', por_defecto: 'sst' };
  const CAMPO_RECORTE = { id: 'recorte', etiqueta: 'Recorte', tipo: 'recorte', por_defecto: 'nino12' };
  const CAMPO_PROFUNDIDAD = { id: 'profundidad', etiqueta: 'Profundidad (m, solo Copernicus)', tipo: 'profundidad', por_defecto: 0 };

  const TIPOS_LOCALES = {
    kpi_variable: {
      nombre: 'Indicador de una variable', icono: 'termometro', ancho: 1,
      descripcion: 'Valor actual de una variable en un recorte, con su anomalía respecto a la climatología.',
      campos: [CAMPO_FUENTE, CAMPO_VARIABLE, CAMPO_RECORTE, CAMPO_PROFUNDIDAD,
        { id: 'dias', etiqueta: 'Días acumulados (precipitación)', tipo: 'numero', por_defecto: 7 }],
    },
    mini_serie: {
      nombre: 'Serie de 90 días', icono: 'series', ancho: 2,
      descripcion: 'Serie diaria de los últimos 90 días con su climatología.',
      campos: [CAMPO_FUENTE, CAMPO_VARIABLE, CAMPO_RECORTE, CAMPO_PROFUNDIDAD,
        { id: 'dias', etiqueta: 'Días', tipo: 'numero', por_defecto: 90 }],
    },
    mini_mapa: {
      nombre: 'Mini mapa', icono: 'mapa', ancho: 1,
      descripcion: 'Mapa de la última fecha disponible (valor o anomalía).',
      campos: [CAMPO_FUENTE, CAMPO_VARIABLE, CAMPO_RECORTE,
        { id: 'modo', etiqueta: 'Modo', tipo: 'opcion', opciones: [{ valor: 'valor', etiqueta: 'Valor' }, { valor: 'anomalia', etiqueta: 'Anomalía' }], por_defecto: 'anomalia' },
        CAMPO_PROFUNDIDAD],
    },
    estado_fuentes: { nombre: 'Estado de fuentes', icono: 'fuentes', ancho: 1, descripcion: 'Semáforo y fecha de corte de cada fuente.', campos: [] },
    trabajos_recientes: {
      nombre: 'Trabajos recientes', icono: 'reportes', ancho: 1, descripcion: 'Últimos reportes y exportaciones.',
      campos: [{ id: 'limite', etiqueta: 'Cantidad', tipo: 'numero', por_defecto: 5 }],
    },
    mis_vistas: {
      nombre: 'Mis vistas', icono: 'vistas', ancho: 1, descripcion: 'Accesos rápidos a las vistas guardadas o compartidas.',
      campos: [{ id: 'solo_compartidas', etiqueta: 'Solo las compartidas conmigo', tipo: 'booleano', por_defecto: false }],
    },
    indice_enso: {
      nombre: 'Índice Niño 1+2', icono: 'olas', ancho: 2, descripcion: 'Anomalía de SST en la región Niño 1+2 con semáforo de fase.',
      campos: [{ id: 'meses', etiqueta: 'Meses', tipo: 'numero', por_defecto: 12 }],
    },
    precipitacion_provincias: {
      nombre: 'Precipitación por provincia', icono: 'lluvia', ancho: 2, descripcion: 'Lluvia acumulada de los últimos días por provincia costera.',
      campos: [{ id: 'dias', etiqueta: 'Días acumulados', tipo: 'numero', por_defecto: 7 }],
    },
    kpi_sistema: { nombre: 'Indicadores del sistema', icono: 'sistema', ancho: 1, descripcion: 'Usuarios, consultas de hoy y caché (administración).', campos: [] },
  };

  const PERIODOS_REF = ['1991-2020', '1981-2010', '1993-2022'];

  let estado = null;

  /* ---------- Utilidades ---------- */
  function lista(d, claves) {
    if (Array.isArray(d)) return d;
    if (!d || typeof d !== 'object') return [];
    for (const k of claves) if (Array.isArray(d[k])) return d[k];
    return [];
  }
  function catalogo() { return SOME.estado.catalogo || { fuentes: [], recortes: [] }; }
  function fuentes() { return lista(catalogo(), ['fuentes']).filter((f) => f.habilitada !== false); }
  function recortes() { return lista(catalogo(), ['recortes']); }
  function profundidades() {
    const p = catalogo().profundidades;
    return Array.isArray(p) && p.length ? p : [0, 5, 10, 20, 30, 50, 75, 100];
  }
  function nuevoId() { return 'w_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

  /** Normaliza la descripción de un tipo (servidor + respaldo local). */
  function describirTipo(id) {
    const local = TIPOS_LOCALES[id] || { nombre: id, descripcion: '', icono: 'panel', ancho: 1, campos: [] };
    const servidor = (estado && estado.tipos || []).find((t) => t && (t.id === id || t.tipo === id)) || {};
    let campos = null;
    const fuenteEsquema = servidor.campos || servidor.parametros || servidor.esquema || servidor.config || servidor.config_esquema;
    if (Array.isArray(fuenteEsquema)) {
      campos = fuenteEsquema.map((c) => (typeof c === 'string' ? { id: c, etiqueta: c, tipo: inferirTipoCampo(c) } : Object.assign({ etiqueta: c.id, tipo: inferirTipoCampo(c.id) }, c)));
    } else if (fuenteEsquema && typeof fuenteEsquema === 'object' && Object.keys(fuenteEsquema).length) {
      campos = Object.keys(fuenteEsquema).map((k) => {
        const v = fuenteEsquema[k];
        if (v && typeof v === 'object') return Object.assign({ id: k, etiqueta: k, tipo: inferirTipoCampo(k) }, v);
        return { id: k, etiqueta: k, tipo: inferirTipoCampo(k), por_defecto: v };
      });
    }
    return {
      id,
      nombre: servidor.nombre || local.nombre,
      descripcion: servidor.descripcion || local.descripcion,
      icono: local.icono || 'panel',
      ancho: servidor.ancho || local.ancho || 1,
      campos: campos && campos.length ? campos : local.campos,
    };
  }
  function inferirTipoCampo(id) {
    if (/^fuente/.test(id)) return 'fuente';
    if (/^variable/.test(id)) return 'variable';
    if (/^recorte/.test(id)) return 'recorte';
    if (/^profundidad/.test(id)) return 'profundidad';
    if (/periodo_referencia/.test(id)) return 'periodo_referencia';
    if (/^(dias|meses|limite|cantidad|anio)/.test(id)) return 'numero';
    if (/^(solo_|incluir_|mostrar_)/.test(id)) return 'booleano';
    if (/^modo$/.test(id)) return 'opcion';
    return 'texto';
  }
  function tiposDisponibles() {
    const ids = new Set(Object.keys(TIPOS_LOCALES));
    (estado.tipos || []).forEach((t) => { if (t && (t.id || t.tipo)) ids.add(t.id || t.tipo); });
    if (!SOME.esAdministrador()) ids.delete('kpi_sistema');
    return Array.from(ids).map(describirTipo);
  }
  function configPorDefecto(desc) {
    const c = {};
    (desc.campos || []).forEach((campo) => { if (campo.por_defecto !== undefined) c[campo.id] = campo.por_defecto; });
    return c;
  }

  /* ---------- Formulario de configuración ---------- */
  function construirFormulario(desc, config, conTitulo) {
    config = config || {};
    const form = util.el('<form class="formulario-widget" novalidate></form>');
    const controles = {};

    if (conTitulo) {
      const campoTitulo = util.el(`<div class="campo"><label for="cfg-titulo">Título del widget</label><input id="cfg-titulo" type="text" maxlength="60" value="${util.escapar(config.__titulo || '')}" placeholder="${util.escapar(desc.nombre)}"></div>`);
      form.appendChild(campoTitulo);
      controles.__titulo = campoTitulo.querySelector('input');
    }

    const cuadricula = util.el('<div class="formulario-cuadricula"></div>');
    form.appendChild(cuadricula);

    (desc.campos || []).forEach((campo) => {
      const idDom = 'cfg-' + campo.id;
      const valor = config[campo.id] !== undefined ? config[campo.id] : campo.por_defecto;
      let nodo;
      switch (campo.tipo) {
        case 'fuente': {
          nodo = util.el(`<div class="campo"><label for="${idDom}">${util.escapar(campo.etiqueta || 'Fuente')}</label><select id="${idDom}"></select></div>`);
          const sel = nodo.querySelector('select');
          fuentes().forEach((f) => sel.appendChild(util.el(`<option value="${util.escapar(f.id)}">${util.escapar(f.nombre)}</option>`)));
          if (!sel.options.length) sel.appendChild(util.el(`<option value="${util.escapar(valor || '')}">${util.escapar(valor || '—')}</option>`));
          if (valor !== undefined) sel.value = String(valor);
          if (!sel.value && sel.options.length) sel.selectedIndex = 0;
          break;
        }
        case 'variable': {
          nodo = util.el(`<div class="campo"><label for="${idDom}">${util.escapar(campo.etiqueta || 'Variable')}</label><select id="${idDom}"></select></div>`);
          break;
        }
        case 'recorte': {
          nodo = util.el(`<div class="campo"><label for="${idDom}">${util.escapar(campo.etiqueta || 'Recorte')}</label><select id="${idDom}"></select></div>`);
          const sel = nodo.querySelector('select');
          const grupos = {};
          recortes().forEach((r) => { (grupos[r.tipo || 'otro'] = grupos[r.tipo || 'otro'] || []).push(r); });
          const nombresTipo = { area: 'Áreas', region_nino: 'Regiones Niño', provincia: 'Provincias', cuenca: 'Cuencas', otro: 'Otros' };
          Object.keys(grupos).forEach((t) => {
            const og = util.el(`<optgroup label="${util.escapar(nombresTipo[t] || t)}"></optgroup>`);
            grupos[t].forEach((r) => og.appendChild(util.el(`<option value="${util.escapar(r.id)}">${util.escapar(r.nombre)}</option>`)));
            sel.appendChild(og);
          });
          if (!sel.options.length) sel.appendChild(util.el(`<option value="${util.escapar(valor || '')}">${util.escapar(valor || '—')}</option>`));
          if (valor !== undefined) sel.value = String(valor);
          break;
        }
        case 'profundidad': {
          nodo = util.el(`<div class="campo"><label for="${idDom}">${util.escapar(campo.etiqueta || 'Profundidad (m)')}</label><select id="${idDom}"></select></div>`);
          const sel = nodo.querySelector('select');
          profundidades().forEach((p) => sel.appendChild(util.el(`<option value="${p}">${util.fmtNum(p, 0)} m</option>`)));
          sel.value = String(valor !== undefined && valor !== null ? valor : 0);
          break;
        }
        case 'periodo_referencia': {
          nodo = util.el(`<div class="campo"><label for="${idDom}">${util.escapar(campo.etiqueta || 'Periodo de referencia')}</label><select id="${idDom}"></select></div>`);
          const sel = nodo.querySelector('select');
          const periodos = Array.isArray(catalogo().periodos_referencia) && catalogo().periodos_referencia.length ? catalogo().periodos_referencia : PERIODOS_REF;
          periodos.forEach((p) => sel.appendChild(util.el(`<option value="${util.escapar(p)}">${util.escapar(p)}</option>`)));
          sel.value = String(valor || periodos[0]);
          break;
        }
        case 'opcion': {
          nodo = util.el(`<div class="campo"><label for="${idDom}">${util.escapar(campo.etiqueta || campo.id)}</label><select id="${idDom}"></select></div>`);
          const sel = nodo.querySelector('select');
          (campo.opciones || []).forEach((o) => {
            const v = typeof o === 'object' ? (o.valor !== undefined ? o.valor : o.id) : o;
            const e = typeof o === 'object' ? (o.etiqueta || o.nombre || v) : o;
            sel.appendChild(util.el(`<option value="${util.escapar(v)}">${util.escapar(e)}</option>`));
          });
          if (valor !== undefined) sel.value = String(valor);
          break;
        }
        case 'booleano': {
          nodo = util.el(`<div class="campo campo-inline"><input id="${idDom}" type="checkbox" ${valor ? 'checked' : ''}><label for="${idDom}">${util.escapar(campo.etiqueta || campo.id)}</label></div>`);
          break;
        }
        case 'numero': {
          nodo = util.el(`<div class="campo"><label for="${idDom}">${util.escapar(campo.etiqueta || campo.id)}</label><input id="${idDom}" type="number" ${campo.min !== undefined ? `min="${campo.min}"` : 'min="1"'} ${campo.max !== undefined ? `max="${campo.max}"` : ''} value="${valor !== undefined && valor !== null ? util.escapar(valor) : ''}"></div>`);
          break;
        }
        case 'fecha': {
          nodo = util.el(`<div class="campo"><label for="${idDom}">${util.escapar(campo.etiqueta || campo.id)}</label><input id="${idDom}" type="date" value="${util.escapar(valor || '')}"></div>`);
          break;
        }
        default: {
          nodo = util.el(`<div class="campo"><label for="${idDom}">${util.escapar(campo.etiqueta || campo.id)}</label><input id="${idDom}" type="text" value="${util.escapar(valor === undefined || valor === null ? '' : valor)}"></div>`);
        }
      }
      controles[campo.id] = nodo.querySelector('input, select');
      controles[campo.id].dataset.tipo = campo.tipo;
      cuadricula.appendChild(nodo);
    });

    // La lista de variables depende de la fuente elegida
    function llenarVariables() {
      const selVar = controles.variable;
      if (!selVar) return;
      const idFuente = controles.fuente ? controles.fuente.value : null;
      const f = idFuente ? SOME.fuente(idFuente) : null;
      const anterior = selVar.value || (config.variable !== undefined ? config.variable : undefined);
      selVar.innerHTML = '';
      // Sin variables vectoriales en los widgets de serie: la serie siempre lleva la anomalía, que no existe
      // para las corrientes (el servidor responde 422); el mini mapa sí las admite (se dibuja en «Valor»)
      const sinVectores = desc.id === 'kpi_variable' || desc.id === 'mini_serie';
      const vars = f && Array.isArray(f.variables) ? f.variables.filter((v) => v.habilitada !== false && !(sinVectores && v.es_vector)) : [];
      vars.forEach((v) => selVar.appendChild(util.el(`<option value="${util.escapar(v.id)}">${util.escapar(v.nombre)}${v.unidad ? ` (${util.escapar(v.unidad)})` : ''}</option>`)));
      if (!vars.length) selVar.appendChild(util.el(`<option value="${util.escapar(anterior || '')}">${util.escapar(anterior || '—')}</option>`));
      if (anterior && Array.from(selVar.options).some((o) => o.value === String(anterior))) selVar.value = String(anterior);
      // Profundidad solo si la variable la tiene
      if (controles.profundidad) {
        const v = f ? SOME.variable(f.id, selVar.value) : null;
        const tiene = v ? !!v.tiene_profundidad : (idFuente === 'copernicus_phy');
        controles.profundidad.closest('.campo').classList.toggle('oculto', !tiene);
      }
    }
    if (controles.fuente) {
      controles.fuente.addEventListener('change', () => { if (controles.variable) controles.variable.value = ''; llenarVariables(); });
    }
    if (controles.variable) controles.variable.addEventListener('change', llenarVariables);
    llenarVariables();

    function leer() {
      const salida = {};
      Object.keys(controles).forEach((k) => {
        const c = controles[k];
        if (k === '__titulo') return;
        if (c.closest('.campo') && c.closest('.campo').classList.contains('oculto')) return;
        if (c.type === 'checkbox') salida[k] = c.checked;
        else if (c.type === 'number' || c.dataset.tipo === 'profundidad') salida[k] = c.value === '' ? null : Number(c.value);
        else salida[k] = c.value;
      });
      return salida;
    }
    function leerTitulo() { return controles.__titulo ? controles.__titulo.value.trim() : ''; }
    return { elemento: form, leer, leerTitulo };
  }

  /* ---------- Enlaces a los módulos ---------- */
  function enlaceWidget(w) {
    const c = w.config || {};
    // Los módulos analíticos leen el área del hash como «area=recorte:<id>» (ver consultas.js).
    const corte = (SOME.estado.catalogo || {}).fecha_corte || '2026-10-27';
    const periodoDias = (dias) => `${util.sumarDias(corte, -(Number(dias || 90) - 1))},${corte}`;
    const base = { fuente: c.fuente, variable: c.variable, area: c.recorte ? `recorte:${c.recorte}` : undefined, profundidad: c.profundidad };
    switch (w.tipo) {
      case 'kpi_variable': return { ruta: 'series', params: Object.assign({}, base, { periodo: periodoDias(365) }), texto: 'Ver serie' };
      case 'mini_serie': return { ruta: 'series', params: Object.assign({}, base, { periodo: periodoDias(c.dias || 90) }), texto: 'Ver serie completa' };
      case 'mini_mapa': return { ruta: 'mapa', params: Object.assign({}, base, { modo: c.modo || 'anomalia', fecha: corte }), texto: 'Abrir en Mapas' };
      case 'indice_enso': return { ruta: 'series', params: { fuente: 'noaa_oisst', variable: 'sst', area: 'recorte:nino12', periodo: periodoDias(365) }, texto: 'Ver serie Niño 1+2' };
      case 'precipitacion_provincias': return { ruta: 'mapa', params: { fuente: 'chirps_v3', variable: 'precip', area: 'recorte:area_estudio', periodo: periodoDias(c.dias || 7) }, texto: 'Abrir en Mapas' };
      case 'estado_fuentes': return { ruta: 'fuentes', params: {}, texto: 'Estado de fuentes' };
      case 'trabajos_recientes': return { ruta: 'reportes', params: {}, texto: 'Ir a Reportes' };
      case 'mis_vistas': return { ruta: 'vistas', params: {}, texto: 'Ir a Mis vistas' };
      case 'kpi_sistema': return SOME.esAdministrador() ? { ruta: 'admin', params: { pestana: 'sistema' }, texto: 'Administración' } : null;
      default: return null;
    }
  }

  /* ---------- Render de la página ---------- */
  async function montar(contenedor) {
    estado = { panel: { widgets: [] }, tipos: [], sucio: false, graficos: [], contenedor, cargas: new Map() };
    contenedor.innerHTML = `
      <div class="encabezado-pagina">
        <div>
          <h2>Panel personal</h2>
          <p id="panel-saludo"></p>
        </div>
        <div class="fila" id="panel-acciones">
          <span class="insignia insignia-aviso oculto" id="panel-sucio">Cambios sin guardar</span>
          <button type="button" class="boton" id="panel-actualizar">${SOME.icono('actualizar')} Actualizar</button>
          <button type="button" class="boton" id="panel-agregar">${SOME.icono('mas')} Agregar widget</button>
          <button type="button" class="boton" id="panel-restablecer">Restablecer</button>
          <button type="button" class="boton boton-primario" id="panel-guardar">${SOME.icono('guardar')} Guardar panel</button>
        </div>
      </div>
      <div id="panel-cuadricula" class="panel-cuadricula"></div>`;

    const u = SOME.estado.usuario || {};
    const hora = new Date().getHours();
    const saludo = hora < 12 ? 'Buenos días' : (hora < 19 ? 'Buenas tardes' : 'Buenas noches');
    // Fecha de corte del sistema: el último día de NOAA OISST en la caché local (la envía el catálogo)
    const corteSistema = (SOME.estado.catalogo || {}).fecha_corte;
    contenedor.querySelector('#panel-saludo').textContent = `${saludo}, ${u.nombre || u.usuario || ''}.${corteSistema ? ` Fecha de corte: ${util.fmtFecha(corteSistema)}.` : ''}`;

    contenedor.querySelector('#panel-actualizar').addEventListener('click', () => cargarDatosDeTodos(true));
    contenedor.querySelector('#panel-agregar').addEventListener('click', abrirAgregar);
    contenedor.querySelector('#panel-guardar').addEventListener('click', guardarPanel);
    contenedor.querySelector('#panel-restablecer').addEventListener('click', restablecerPanel);

    estado.alCambiarTema = () => { estado.panel.widgets.forEach((w) => { if (estado.cargas.has(w.id)) renderDatos(w, estado.cargas.get(w.id)); }); };
    SOME.eventos.addEventListener('tema', estado.alCambiarTema);

    const cuadricula = contenedor.querySelector('#panel-cuadricula');
    cuadricula.innerHTML = [1, 2, 3, 4, 5, 6].map(() => '<div class="tarjeta widget"><div class="esqueleto esqueleto-linea" style="width:50%"></div><div class="esqueleto esqueleto-bloque mt-12"></div></div>').join('');

    try {
      const [panel, tipos] = await Promise.all([
        api.get('/panel'),
        api.get('/panel/tipos').catch(() => []),
      ]);
      estado.tipos = lista(tipos, ['tipos']);
      estado.panel = normalizarPanel(panel);
    } catch (e) {
      cuadricula.innerHTML = '';
      cuadricula.appendChild(util.el(`<div class="widget ancho-3">${util.errorHtml(e.message, 'panel')}</div>`));
      cuadricula.querySelector('[data-reintentar]').addEventListener('click', () => montar(contenedor));
      return;
    }
    renderCuadricula();
    cargarDatosDeTodos(false);
  }

  function desmontar() {
    if (!estado) return;
    SOME.eventos.removeEventListener('tema', estado.alCambiarTema);
    purgarGraficos();
    estado = null;
  }

  function purgarGrafico(g) {
    if (g._zoomAcotado) g._zoomAcotado.quitar();
    if (window.Plotly) { try { window.Plotly.purge(g); } catch (e) { /* ignorar */ } }
  }

  function purgarGraficos() {
    if (estado) estado.graficos.forEach(purgarGrafico);
    if (estado) estado.graficos = [];
  }

  function normalizarPanel(panel) {
    const widgets = lista(panel, ['widgets']).filter((w) => w && w.tipo).map((w) => ({
      id: w.id || nuevoId(),
      tipo: w.tipo,
      titulo: w.titulo || describirTipo(w.tipo).nombre,
      config: (w.config && typeof w.config === 'object') ? w.config : {},
      ancho: [1, 2, 3].includes(Number(w.ancho)) ? Number(w.ancho) : (describirTipo(w.tipo).ancho || 1),
    }));
    return { widgets };
  }

  function marcarSucio(valor) {
    estado.sucio = valor;
    const i = estado.contenedor.querySelector('#panel-sucio');
    if (i) i.classList.toggle('oculto', !valor);
  }

  function renderCuadricula() {
    const cuadricula = estado.contenedor.querySelector('#panel-cuadricula');
    purgarGraficos();
    cuadricula.innerHTML = '';
    if (!estado.panel.widgets.length) {
      const v = util.el(`<div class="widget ancho-3">${util.vacioHtml('Su panel está vacío', 'Agregue widgets para ver indicadores, series, mapas y el estado de las fuentes al ingresar.')}</div>`);
      const b = util.el(`<button type="button" class="boton boton-primario">${SOME.icono('mas')} Agregar widget</button>`);
      b.addEventListener('click', abrirAgregar);
      v.querySelector('.vacio').appendChild(b);
      cuadricula.appendChild(v);
      return;
    }
    estado.panel.widgets.forEach((w) => cuadricula.appendChild(crearTarjeta(w)));
  }

  function crearTarjeta(w) {
    const desc = describirTipo(w.tipo);
    const enlace = enlaceWidget(w);
    const tarjeta = util.el(`
      <section class="tarjeta widget ancho-${w.ancho}" data-id="${util.escapar(w.id)}" aria-label="${util.escapar(w.titulo)}">
        <div class="widget-cabecera">
          <h3 title="${util.escapar(w.titulo)}">${util.escapar(w.titulo)}</h3>
          <div class="fila" style="gap:2px">
            ${enlace ? `<a class="boton boton-icono boton-fantasma boton-pequeno" href="#/${enlace.ruta}${codificar(enlace.params)}" aria-label="${util.escapar(enlace.texto)}" title="${util.escapar(enlace.texto)}">${SOME.icono('enlace')}</a>` : ''}
            <div class="desplegable">
              <button type="button" class="boton boton-icono boton-fantasma boton-pequeno" aria-label="Opciones del widget" aria-haspopup="menu">${SOME.icono('puntos')}</button>
              <div class="desplegable-menu" role="menu">
                <button type="button" role="menuitem" data-accion="configurar" ${desc.campos.length ? '' : 'disabled'}>${SOME.icono('editar')} Configurar</button>
                <button type="button" role="menuitem" data-accion="recargar">${SOME.icono('actualizar')} Actualizar</button>
                <hr>
                <div class="submenu-titulo">Ancho</div>
                <button type="button" role="menuitem" data-accion="ancho" data-ancho="1">${marcaAncho(w.ancho === 1)} 1 columna</button>
                <button type="button" role="menuitem" data-accion="ancho" data-ancho="2">${marcaAncho(w.ancho === 2)} 2 columnas</button>
                <button type="button" role="menuitem" data-accion="ancho" data-ancho="3">${marcaAncho(w.ancho === 3)} 3 columnas</button>
                <hr>
                <button type="button" role="menuitem" class="peligro" data-accion="quitar">${SOME.icono('eliminar')} Quitar</button>
              </div>
            </div>
          </div>
        </div>
        <div class="widget-cuerpo">
          <div class="esqueleto esqueleto-linea" style="width:60%"></div>
          <div class="esqueleto esqueleto-bloque mt-8"></div>
        </div>
      </section>`);

    const desplegable = tarjeta.querySelector('.desplegable');
    desplegable.querySelector('button').addEventListener('click', (ev) => {
      ev.stopPropagation();
      document.querySelectorAll('.desplegable.abierto').forEach((d) => { if (d !== desplegable) d.classList.remove('abierto'); });
      desplegable.classList.toggle('abierto');
    });
    desplegable.querySelectorAll('[data-accion]').forEach((b) => {
      b.addEventListener('click', () => {
        desplegable.classList.remove('abierto');
        const accion = b.dataset.accion;
        if (accion === 'configurar') abrirConfigurar(w);
        else if (accion === 'recargar') cargarDatos(w, true);
        else if (accion === 'ancho') cambiarAncho(w, Number(b.dataset.ancho));
        else if (accion === 'quitar') quitarWidget(w);
      });
    });
    return tarjeta;
  }

  function codificar(params) {
    const partes = Object.keys(params || {}).filter((k) => params[k] !== undefined && params[k] !== null && params[k] !== '')
      .map((k) => encodeURIComponent(k) + '=' + encodeURIComponent(typeof params[k] === 'object' ? JSON.stringify(params[k]) : params[k]));
    return partes.length ? '?' + partes.join('&') : '';
  }

  function tarjetaDe(w) { return estado.contenedor.querySelector(`.widget[data-id="${w.id}"]`); }

  /* ---------- Acciones sobre widgets ---------- */
  function cambiarAncho(w, ancho) {
    w.ancho = ancho;
    const t = tarjetaDe(w);
    if (t) {
      t.classList.remove('ancho-1', 'ancho-2', 'ancho-3');
      t.classList.add('ancho-' + ancho);
      t.querySelectorAll('[data-accion="ancho"]').forEach((b) => { b.innerHTML = `${marcaAncho(Number(b.dataset.ancho) === ancho)} ${b.dataset.ancho} columna${b.dataset.ancho === '1' ? '' : 's'}`; });
      if (window.Plotly) t.querySelectorAll('.js-plotly-plot, .widget-grafico-lienzo').forEach((g) => { try { window.Plotly.Plots.resize(g); } catch (e) { /* ignorar */ } });
    }
    marcarSucio(true);
  }

  async function quitarWidget(w) {
    const ok = await util.confirmar(`¿Quitar el widget «${w.titulo}» del panel?`, { aceptar: 'Quitar', peligro: true });
    if (!ok) return;
    estado.panel.widgets = estado.panel.widgets.filter((x) => x.id !== w.id);
    estado.cargas.delete(w.id);
    renderCuadricula();
    estado.panel.widgets.forEach((x) => { if (estado.cargas.has(x.id)) renderDatos(x, estado.cargas.get(x.id)); else cargarDatos(x, false); });
    marcarSucio(true);
  }

  function abrirConfigurar(w) {
    const desc = describirTipo(w.tipo);
    const form = construirFormulario(desc, Object.assign({ __titulo: w.titulo }, w.config), true);
    util.modal({
      titulo: `Configurar: ${desc.nombre}`,
      contenido: form.elemento,
      acciones: [
        { texto: 'Cancelar', clase: 'boton-secundario' },
        {
          texto: 'Aplicar', clase: 'boton-primario',
          accion: () => {
            w.config = form.leer();
            w.titulo = form.leerTitulo() || desc.nombre;
            const t = tarjetaDe(w);
            if (t) {
              const nuevo = crearTarjeta(w);
              t.replaceWith(nuevo);
            }
            marcarSucio(true);
            cargarDatos(w, true);
          },
        },
      ],
    });
    form.elemento.addEventListener('submit', (ev) => { ev.preventDefault(); form.elemento.closest('.modal').querySelector('.modal-acciones .boton-primario').click(); });
  }

  function abrirAgregar() {
    const tipos = tiposDisponibles();
    const contenido = util.el('<div class="cuadricula col-2"></div>');
    tipos.forEach((t) => {
      const b = util.el(`
        <button type="button" class="tipo-widget">
          ${SOME.icono(t.icono)}
          <span><strong>${util.escapar(t.nombre)}</strong><small>${util.escapar(t.descripcion || '')}</small></span>
        </button>`);
      b.addEventListener('click', () => { m.cerrar(); configurarNuevo(t); });
      contenido.appendChild(b);
    });
    const m = util.modal({ titulo: 'Agregar widget', contenido, ancho: 'ancho', acciones: [{ texto: 'Cancelar', clase: 'boton-secundario' }] });
  }

  function configurarNuevo(desc) {
    const config = configPorDefecto(desc);
    const agregar = (cfg, titulo) => {
      const w = { id: nuevoId(), tipo: desc.id, titulo: titulo || desc.nombre, config: cfg, ancho: desc.ancho || 1 };
      estado.panel.widgets.push(w);
      const cuadricula = estado.contenedor.querySelector('#panel-cuadricula');
      if (estado.panel.widgets.length === 1) renderCuadricula();
      else cuadricula.appendChild(crearTarjeta(w));
      marcarSucio(true);
      cargarDatos(w, false);
      util.toast(`Widget «${w.titulo}» agregado. Recuerde guardar el panel.`, 'exito');
    };
    if (!desc.campos.length) { agregar(config, desc.nombre); return; }
    const form = construirFormulario(desc, Object.assign({ __titulo: '' }, config), true);
    util.modal({
      titulo: `Nuevo widget: ${desc.nombre}`,
      contenido: form.elemento,
      acciones: [
        { texto: 'Cancelar', clase: 'boton-secundario' },
        { texto: 'Agregar', clase: 'boton-primario', accion: () => agregar(form.leer(), form.leerTitulo()) },
      ],
    });
    form.elemento.addEventListener('submit', (ev) => { ev.preventDefault(); form.elemento.closest('.modal').querySelector('.modal-acciones .boton-primario').click(); });
  }

  async function guardarPanel() {
    const b = estado.contenedor.querySelector('#panel-guardar');
    b.disabled = true;
    try {
      const cuerpo = { widgets: estado.panel.widgets.map((w) => ({ id: w.id, tipo: w.tipo, titulo: w.titulo, config: w.config, ancho: w.ancho })) };
      const r = await api.put('/panel', cuerpo);
      if (r && Array.isArray(r.widgets)) estado.panel = normalizarPanel(r);
      marcarSucio(false);
      util.toast('Panel guardado.', 'exito');
    } catch (e) {
      util.toast('No se pudo guardar el panel: ' + e.message, 'error');
    } finally {
      b.disabled = false;
    }
  }

  async function restablecerPanel() {
    const ok = await util.confirmar('Se reemplazará su panel por el panel por defecto de su rol. ¿Desea continuar?', { aceptar: 'Restablecer', peligro: true });
    if (!ok) return;
    const cuadricula = estado.contenedor.querySelector('#panel-cuadricula');
    util.cargando(cuadricula, 'Restableciendo el panel…');
    let panel = null;
    // El contrato no define una ruta explícita: se intenta POST /panel/restablecer,
    // luego DELETE /panel y finalmente GET /panel (que crea el panel por defecto si no existe).
    try { panel = await api.post('/panel/restablecer', {}); } catch (e) { panel = null; }
    if (!panel || !Array.isArray(panel.widgets)) {
      try { await api.del('/panel'); } catch (e) { /* puede no existir */ }
      try { panel = await api.get('/panel'); } catch (e) { util.toast('No se pudo restablecer el panel: ' + e.message, 'error'); renderCuadricula(); cargarDatosDeTodos(false); return; }
    }
    estado.panel = normalizarPanel(panel);
    estado.cargas.clear();
    marcarSucio(false);
    renderCuadricula();
    cargarDatosDeTodos(false);
    util.toast('Panel restablecido al valor por defecto.', 'exito');
  }

  /* ---------- Carga de datos ---------- */
  function cargarDatosDeTodos(forzar) {
    if (forzar) estado.cargas.clear();
    estado.panel.widgets.forEach((w) => cargarDatos(w, forzar));
  }

  async function cargarDatos(w, forzar) {
    const t = tarjetaDe(w);
    if (!t) return;
    const cuerpo = t.querySelector('.widget-cuerpo');
    if (!forzar && estado.cargas.has(w.id)) { renderDatos(w, estado.cargas.get(w.id)); return; }
    cuerpo.innerHTML = '<div class="esqueleto esqueleto-linea" style="width:60%"></div><div class="esqueleto esqueleto-bloque mt-8"></div>';
    try {
      const datos = await api.post('/panel/widget/datos', { tipo: w.tipo, config: w.config || {} });
      if (!estado) return;
      estado.cargas.set(w.id, datos || {});
      renderDatos(w, datos || {});
    } catch (e) {
      if (!estado || e.codigo === 401) return;
      cuerpo.innerHTML = `
        <div class="vacio" style="padding:16px">
          ${util.ICONOS.error}
          <p>${util.escapar(e.message || 'No se pudieron obtener los datos.')}</p>
          <button type="button" class="boton boton-secundario boton-pequeno">Reintentar</button>
        </div>`;
      cuerpo.querySelector('button').addEventListener('click', () => cargarDatos(w, true));
    }
  }

  function renderDatos(w, d) {
    const t = tarjetaDe(w);
    if (!t) return;
    const cuerpo = t.querySelector('.widget-cuerpo');
    t.querySelectorAll('.js-plotly-plot, .widget-grafico-lienzo').forEach((g) => { purgarGrafico(g); estado.graficos = estado.graficos.filter((x) => x !== g); });
    cuerpo.innerHTML = '';
    if (d && typeof d === 'object' && d.error) { cuerpo.innerHTML = util.vacioHtml('Sin datos', d.error, 'aviso'); return; }
    const render = RENDER[w.tipo] || renderGenerico;
    try {
      render(w, d || {}, cuerpo);
    } catch (e) {
      console.error('[SOME] Error al dibujar el widget', w.tipo, e);
      cuerpo.innerHTML = util.vacioHtml('No se pudo dibujar el widget', e.message, 'error');
    }
    if (d && d.fecha_corte) {
      // Pie con crédito e identificador visibles (RF-12): fuente corta + identificador + fecha de corte,
      // y el crédito completo en una segunda línea pequeña (también como título emergente).
      const f = d.fuente && typeof d.fuente === 'object' ? d.fuente : (typeof d.fuente === 'string' ? { nombre: d.fuente } : null);
      const nombreCorto = f ? (f.nombre_corto || f.nombre || '') : '';
      const proc = d.procedencia ? SOME.procedencia(d) : null;
      const etiquetaProc = proc ? ` <span class="etiqueta-procedencia${proc.procedencia === 'real' ? ' real' : ''}" title="${util.escapar(proc.titulo)}">${util.escapar({ real: 'Real', simulado: 'Simulado', mixto: 'Mixto' }[proc.procedencia])}</span>` : '';
      const pie = util.el(`<div class="widget-pie"><span>Corte: ${util.fmtFecha(d.fecha_corte)}${etiquetaProc}</span>${f ? `<span class="recortar" title="${util.escapar(f.credito || '')}">${f.identificador ? `<code>${util.escapar(f.identificador)}</code> ` : ''}${util.escapar(nombreCorto)}</span>` : ''}</div>`);
      cuerpo.appendChild(pie);
      if (f && f.credito) cuerpo.appendChild(util.el(`<div class="widget-credito" title="${util.escapar(f.credito)}">${util.escapar(f.credito)}</div>`));
    }
  }

  /** Marca de opción del menú «Ancho» (círculo con punto = elegida). */
  function marcaAncho(elegida) {
    return util.iconoFi(elegida ? 'dot-circle' : 'circle');
  }

  function deltaHtml(anomalia, unidad, etiqueta, decimales) {
    if (!util.esNumero(anomalia)) return '';
    const cero = Math.abs(anomalia) < 0.05;
    const clase = cero ? '' : (anomalia > 0 ? 'positivo' : 'negativo');
    const flecha = util.iconoFi(cero ? 'arrow-small-right' : (anomalia > 0 ? 'caret-up' : 'caret-down'));
    // Los mismos decimales que el valor del KPI (p. ej., 0 para la precipitación acumulada)
    const dec = util.esNumero(decimales) ? decimales : 1;
    return `<span class="kpi-delta ${clase}" title="Anomalía respecto a la climatología">${flecha} ${util.fmtAnomalia(anomalia, unidad, dec)}${etiqueta ? ` <span class="texto-2" style="font-weight:400">${util.escapar(etiqueta)}</span>` : ''}</span>`;
  }

  /* Latencia de una fuente en el widget «Estado de las fuentes». Misma regla que «Estado de fuentes»:
     en una fuente real, 0 o null significa que no hubo medición (el servidor no respondió o el modo
     sin red está activo), no «0 ms». */
  function latenciaFuenteHtml(f) {
    // Copernicus Marine y NOAA OISST fuera del modo simulado forzado tienen verificaciones reales aunque sus
    // datos sean simulados
    const real = f.datos_reales === true || !!(f.copernicus && f.copernicus.modo_configurado !== 'simulado')
      || !!(f.noaa && f.noaa.modo_configurado !== 'simulado');
    if (util.esNumero(f.latencia_ms) && !(real && !f.latencia_ms)) {
      return `<span class="texto-pequeno texto-2 tabular">${util.fmtNum(f.latencia_ms, 0)} ms</span>`;
    }
    return real ? '<span class="texto-pequeno texto-2 tabular" title="Sin medición: el servidor de la fuente no respondió en la última verificación">—</span>' : '';
  }

  function textoHtml(texto) {
    if (!texto) return '';
    const frases = Array.isArray(texto) ? texto : [texto];
    return `<div class="kpi-texto">${frases.map((f) => util.escapar(f)).join(' ')}</div>`;
  }

  // Insignia de procedencia (sección 8.5) en cada gráfico y mini mapa del panel, según la respuesta:
  // verde «Datos reales · CHIRPS v3» o «Datos reales · Copernicus Marine», ámbar «Datos simulados para
  // demostración» o ámbar «Datos mixtos…» (los avisos, p. ej. el respaldo simulado, van en el título)
  function marcaSimulado(d) {
    const p = SOME.procedencia(d);
    return `<span class="marca-simulado${p.procedencia === 'real' ? ' marca-real' : ''}" data-procedencia="${p.procedencia}" title="${util.escapar(p.titulo)}">${util.escapar(p.texto)}</span>`;
  }

  function graficoPlotly(cuerpo, trazas, layout, altura, d) {
    const div = util.el(`<div class="widget-grafico" style="min-height:${altura || 150}px"></div>`);
    cuerpo.appendChild(div);
    // La marca va dentro del contenedor del gráfico (esquina superior derecha), no sobre el pie del widget
    div.appendChild(util.el(marcaSimulado(d)));
    if (!window.Plotly) { div.innerHTML = util.vacioHtml('Gráficos no disponibles', 'No se pudo cargar la biblioteca Plotly.'); return; }
    const lienzo = util.el('<div class="widget-grafico-lienzo"></div>');
    div.insertBefore(lienzo, div.firstChild);
    // Zoom acotado como en los módulos de análisis (consultas.js): sin barra de Plotly, alejar era solo el doble clic
    window.Plotly.newPlot(lienzo, trazas, SOME.plotlyBase(Object.assign({ height: altura || 150 }, layout || {})), SOME.plotlyConfig)
      .then(() => { const c = SOME.componentes && SOME.componentes.comunes; if (c && c.zoomAcotado) c.zoomAcotado(lienzo); });
    estado.graficos.push(lienzo);
  }

  /* ---------- Renderizadores por tipo ---------- */
  const RENDER = {
    kpi_variable(w, d, cuerpo) {
      const unidad = d.unidad || SOME.unidadVariable(w.config.fuente, w.config.variable);
      const etiqueta = d.etiqueta || `${SOME.nombreVariable(w.config.fuente, w.config.variable)} · ${SOME.nombreRecorte(w.config.recorte)}`;
      cuerpo.innerHTML = `
        <div class="kpi">
          <span class="kpi-etiqueta recortar" title="${util.escapar(etiqueta)}">${util.escapar(etiqueta)}</span>
          <span class="kpi-valor">${util.fmtNum(d.valor, d.decimales !== undefined ? d.decimales : 1)}<small>${util.escapar(unidad)}</small></span>
          ${deltaHtml(d.anomalia, unidad, d.fecha ? `(${util.fmtFecha(d.fecha)})` : '', d.decimales !== undefined ? d.decimales : 1)}
          ${textoHtml(d.texto)}
        </div>`;
    },
    kpi_sistema(w, d, cuerpo) {
      const items = [
        { e: 'Usuarios', v: d.usuarios !== undefined ? d.usuarios : (d.usuarios_activos !== undefined ? d.usuarios_activos : null), dec: 0 },
        { e: 'Consultas hoy', v: d.consultas_hoy, dec: 0 },
        { e: 'Caché (entradas)', v: d.cache && typeof d.cache === 'object' ? d.cache.entradas : (d.cache_entradas !== undefined ? d.cache_entradas : d.cache), dec: 0 },
      ].filter((i) => i.v !== undefined && i.v !== null);
      if (!items.length) { renderGenerico(w, d, cuerpo); return; }
      cuerpo.innerHTML = `<div class="cuadricula" style="grid-template-columns:repeat(${items.length},1fr);gap:8px">${items.map((i) => `
        <div class="kpi"><span class="kpi-etiqueta">${util.escapar(i.e)}</span><span class="kpi-valor" style="font-size:24px">${util.fmtNum(i.v, i.dec)}</span></div>`).join('')}</div>${textoHtml(d.texto)}`;
    },
    mini_serie(w, d, cuerpo) {
      const fechas = d.fechas || [];
      const valores = d.valores || [];
      if (!fechas.length) { cuerpo.innerHTML = util.vacioHtml('Sin datos', 'La serie no devolvió valores.'); return; }
      const unidad = d.unidad || SOME.unidadVariable(w.config.fuente, w.config.variable);
      const ultimo = valores.length ? valores[valores.length - 1] : null;
      const c = SOME.colores();
      const decSerie = d.decimales !== undefined ? d.decimales : 1;
      cuerpo.innerHTML = `<div class="fila fila-entre mb-8"><span class="kpi-valor" style="font-size:22px">${util.fmtNum(ultimo, decSerie)}<small>${util.escapar(unidad)}</small></span>${deltaHtml(d.anomalia, unidad, '', decSerie)}</div>`;
      const trazas = [{ x: fechas, y: valores, type: 'scatter', mode: 'lines', name: d.nombre_variable || 'Valor', line: { color: c.primario, width: 2 }, hovertemplate: '%{y:.2f} ' + unidad + '<extra></extra>' }];
      if (Array.isArray(d.climatologia) && d.climatologia.length) trazas.push({ x: fechas, y: d.climatologia, type: 'scatter', mode: 'lines', name: 'Climatología', line: { color: c.texto2, width: 1.5, dash: 'dash' }, hovertemplate: 'Clim. %{y:.2f} ' + unidad + '<extra></extra>' });
      graficoPlotly(cuerpo, trazas, { yaxis: { gridcolor: c.borde, title: { text: unidad, font: { size: 10 } } } }, 150, d);
      if (d.texto) cuerpo.appendChild(util.el(textoHtml(d.texto)));
    },
    mini_mapa(w, d, cuerpo) {
      if (!d.imagen_png) { cuerpo.innerHTML = util.vacioHtml('Sin imagen', 'El servidor no devolvió el mapa.'); return; }
      const unidad = d.unidad || SOME.unidadVariable(w.config.fuente, w.config.variable);
      cuerpo.innerHTML = `
        ${d.titulo || d.subtitulo ? `<div class="texto-pequeno texto-2 mb-8 recortar" title="${util.escapar(d.titulo || '')} ${util.escapar(d.subtitulo || '')}">${util.escapar(d.titulo || '')}${d.subtitulo ? ` · ${util.escapar(d.subtitulo)}` : ''}</div>` : ''}
        <div class="widget-mapa">
          <img src="${d.imagen_png}" alt="${util.escapar(d.titulo || 'Mapa')}">
          ${marcaSimulado(d)}
        </div>
        ${util.esNumero(d.vmin) && util.esNumero(d.vmax) ? `<div class="fila fila-entre texto-pequeno texto-2 mt-8"><span>${util.fmtNum(d.vmin)} ${util.escapar(unidad)}</span><span>${d.fecha ? util.fmtFecha(d.fecha) : ''}</span><span>${util.fmtNum(d.vmax)} ${util.escapar(unidad)}</span></div>` : ''}
        ${textoHtml(d.texto)}`;
    },
    estado_fuentes(w, d, cuerpo) {
      const items = lista(d, ['fuentes', 'estado', 'valores']);
      if (!items.length) { cuerpo.innerHTML = util.vacioHtml('Sin información', 'No hay estado de fuentes.'); return; }
      SOME.actualizarIndicadorFuentes(items.map((f) => ({ fuente: f.fuente || f.id, nombre: f.nombre, estado: f.estado, fecha_corte: f.fecha_corte })));
      // Nombre corto del catálogo («Copernicus Marine»): el completo se recortaba con elipsis; va en el title
      const nombreCorto = (f) => ((SOME.fuente(f.fuente || f.id) || {}).nombre_corto || f.nombre || SOME.nombreFuente(f.fuente));
      cuerpo.innerHTML = `<ul class="lista">${items.map((f) => `
        <li>
          <span class="semaforo ${SOME.claseSemaforo(f.estado)}"></span>
          <span class="lista-texto"><strong title="${util.escapar(f.nombre || SOME.nombreFuente(f.fuente) || '')}">${util.escapar(nombreCorto(f))}</strong><small>${util.escapar(SOME.textoEstadoFuente(f.estado))} · corte ${util.fmtFecha(f.fecha_corte)}</small></span>
          ${latenciaFuenteHtml(f)}
        </li>`).join('')}</ul>`;
    },
    trabajos_recientes(w, d, cuerpo) {
      const items = lista(d, ['trabajos', 'valores']);
      if (!items.length) { cuerpo.innerHTML = util.vacioHtml('Sin trabajos', 'Aún no ha generado reportes ni exportaciones.'); return; }
      const clase = { listo: 'insignia-exito', error: 'insignia-peligro', procesando: 'insignia-info', en_cola: '' };
      const texto = { listo: 'Listo', error: 'Error', procesando: 'Procesando', en_cola: 'En cola' };
      cuerpo.innerHTML = `<ul class="lista">${items.map((t) => `
        <li>
          <span class="lista-texto"><strong title="${util.escapar(t.titulo || '')}">${util.escapar(t.titulo || t.tipo || 'Trabajo')}</strong><small>${util.fmtFechaHora(t.terminado || t.creado)}${t.mensaje && t.estado !== 'listo' ? ` · ${util.escapar(t.mensaje)}` : ''}</small></span>
          <span class="insignia ${clase[t.estado] || ''}">${util.escapar(texto[t.estado] || t.estado || '')}</span>
          ${t.archivo && t.estado === 'listo' ? `<button type="button" class="boton boton-icono boton-fantasma boton-pequeno" data-archivo="${util.escapar(t.archivo)}" aria-label="Descargar PDF" title="Descargar PDF">${SOME.icono('descargar')}</button>` : ''}
        </li>`).join('')}</ul>`;
      cuerpo.querySelectorAll('[data-archivo]').forEach((b) => b.addEventListener('click', async () => {
        b.disabled = true;
        try { await api.descargar(b.dataset.archivo, null, 'SOME_reporte.pdf'); } catch (e) { util.toast(e.message, 'error'); } finally { b.disabled = false; }
      }));
    },
    mis_vistas(w, d, cuerpo) {
      let items = lista(d, ['vistas', 'valores']);
      if (!items.length && d && (Array.isArray(d.propias) || Array.isArray(d.compartidas))) items = [].concat(d.propias || [], d.compartidas || []);
      if (!items.length) { cuerpo.innerHTML = util.vacioHtml('Sin vistas', 'Guarde una vista desde Mapas, Series o Perfiles para verla aquí.'); return; }
      cuerpo.innerHTML = `<ul class="lista">${items.slice(0, 8).map((v) => `
        <li>
          ${SOME.icono(v.modulo === 'mapa' ? 'mapa' : (v.modulo === 'perfiles' ? 'perfiles' : (v.modulo === 'comparar' ? 'comparar' : 'series')))}
          <span class="lista-texto"><strong title="${util.escapar(v.nombre || '')}">${util.escapar(v.nombre || 'Vista')}</strong><small>${v.propietario && v.propietario.nombre ? util.escapar(v.propietario.nombre) + ' · ' : ''}${util.fmtFecha(v.actualizada || v.creada)}${v.compartida ? ' · compartida' : ''}</small></span>
          <button type="button" class="boton boton-pequeno" data-abrir="${util.escapar(v.id)}" data-modulo="${util.escapar(v.modulo || 'mapa')}">Abrir</button>
        </li>`).join('')}</ul>`;
      cuerpo.querySelectorAll('[data-abrir]').forEach((b) => b.addEventListener('click', () => {
        const v = items.find((x) => String(x.id) === b.dataset.abrir);
        const params = Object.assign({ vista: b.dataset.abrir }, aplanar(v && v.parametros));
        SOME.navegar(b.dataset.modulo, params);
      }));
    },
    /* Índice Niño 1+2. Con NOAA OISST real: anomalía semanal (domingo a sábado, etiquetada con el miércoles,
       como NOAA CPC) en la región oficial 0–10° S, 90–80° O; las semanas sin dato (menos de 4 días) llegan como
       null y quedan como huecos; las semanas con días preliminares van rayadas y la última, si está incompleta,
       dice cuántos días tiene. Con el índice simulado (respaldo señalado) llega sin esas listas. */
    indice_enso(w, d, cuerpo) {
      const fechas = d.fechas || [];
      const valores = (d.valores || []).map((v) => (util.esNumero(v) ? v : null));
      const ultimoValido = valores.filter((v) => v !== null).pop();
      const actual = util.esNumero(d.valor) ? d.valor : (util.esNumero(d.actual) ? d.actual : (ultimoValido !== undefined ? ultimoValido : null));
      let fase = d.fase || d.texto_fase || null;
      let clase = d.semaforo || null;
      if (fase === 'neutra') fase = 'Condiciones neutras';
      else if (fase === 'sin dato') fase = 'Sin dato';
      if (!fase && util.esNumero(actual)) fase = actual >= 0.5 ? 'Condiciones cálidas (El Niño)' : (actual <= -0.5 ? 'Condiciones frías (La Niña)' : 'Condiciones neutras');
      if (!clase && util.esNumero(actual)) clase = actual >= 0.5 ? 'rojo' : (actual <= -0.5 ? 'ambar' : 'verde');
      if (clase === 'amarillo') clase = 'ambar';
      const claseFase = { rojo: 'insignia-peligro', ambar: 'insignia-info', verde: 'insignia-exito' }[clase] || '';
      // Semana del valor actual (índice real): fechas, SST media, días con dato y si es preliminar
      const sa = d.semana_actual && typeof d.semana_actual === 'object' ? d.semana_actual : null;
      const detalleSemana = sa ? [
        sa.desde && sa.hasta ? `Semana del ${util.fmtFecha(sa.desde)} al ${util.fmtFecha(sa.hasta)}` : '',
        util.esNumero(d.sst_actual) ? `SST media ${util.fmtNum(d.sst_actual, 2)} °C` : '',
        sa.parcial && util.esNumero(sa.dias) ? `${util.fmtNum(sa.dias, 0)} de 7 días` : '',
        sa.preliminar ? 'datos preliminares' : '',
      ].filter(Boolean).join(' · ') : '';
      cuerpo.innerHTML = `
        <div class="fila fila-entre fila-arriba mb-8" style="gap:8px">
          <div class="kpi" style="min-width:0">
            <span class="kpi-etiqueta">${sa ? 'Anomalía semanal de SST en Niño 1+2' : 'Anomalía de SST en Niño 1+2'}</span>
            <span class="kpi-valor" style="font-size:24px">${util.fmtAnomalia(actual, d.unidad || '°C', 2)}</span>
            ${detalleSemana ? `<span class="kpi-texto mt-0">${util.escapar(detalleSemana)}</span>` : ''}
          </div>
          <span class="insignia ${claseFase}" style="flex-shrink:0"><span class="semaforo ${clase || ''}"></span>${util.escapar(fase || '')}</span>
        </div>`;
      if (!fechas.length) { cuerpo.appendChild(util.el(util.vacioHtml('Sin serie', 'No se recibieron valores.'))); return; }
      const c = SOME.colores();
      const n = fechas.length;
      const lista = (clave) => (Array.isArray(d[clave]) && d[clave].length === n ? d[clave] : null);
      const prelim = lista('preliminar');
      const parcial = lista('parcial');
      const dias = lista('dias');
      const sst = lista('sst');
      const semanal = Boolean(prelim || sa);
      // La última semana es preliminar o parcial según semana_actual si el servidor no envía las listas
      const esPrelim = (i) => (prelim ? prelim[i] === true : Boolean(sa && sa.preliminar && i === n - 1));
      const esParcial = (i) => (parcial ? parcial[i] === true : Boolean(sa && sa.parcial && i === n - 1));
      const corte = d.fecha_corte || (sa && sa.hasta) || '';
      const textoHover = valores.map((v, i) => {
        const partes = [];
        if (semanal) {
          const desde = util.sumarDias(fechas[i], -3);
          let hasta = util.sumarDias(fechas[i], 3);
          if (corte && hasta > corte) hasta = corte;
          partes.push(`Semana del ${util.fmtFecha(desde)} al ${util.fmtFecha(hasta)}`);
        } else {
          partes.push(util.fmtFecha(fechas[i]));
        }
        partes.push(v === null ? 'Sin dato (menos de 4 días con dato)' : `Anomalía ${util.fmtAnomalia(v, '°C', 2)}${sst && util.esNumero(sst[i]) ? ` · SST ${util.fmtNum(sst[i], 2)} °C` : ''}`);
        const notas = [esParcial(i) && dias ? `${dias[i]} de 7 días` : (esParcial(i) ? 'semana incompleta' : ''), esPrelim(i) ? 'con días preliminares' : ''].filter(Boolean);
        if (notas.length) partes.push(notas.join(' · '));
        return partes.join('<br>');
      });
      const hayPrelim = valores.some((v, i) => v !== null && esPrelim(i));
      const hayHuecos = valores.some((v) => v === null);
      const iParcial = valores.map((v, i) => i).filter((i) => esParcial(i) && valores[i] !== null).pop();
      graficoPlotly(cuerpo, [
        // Plotly no admite «+» en el formato de hovertemplate: el texto con signo y punto decimal va en customdata.
        // Ancho fijo de 5 días (semanas a 7 días): sin él, Plotly dejaba las barras en 2 px y sin hover.
        // Las semanas con días preliminares van rayadas (patrón sobre el color de la barra).
        {
          x: fechas, y: valores, type: 'bar', width: 5 * 86400000,
          marker: {
            color: valores.map((v) => (v !== null && v >= 0 ? c.peligro : c.info)),
            pattern: { shape: valores.map((v, i) => (esPrelim(i) ? '/' : '')), fillmode: 'overlay', fgcolor: c.superficie, fgopacity: 0.75, size: 5, solidity: 0.35 },
          },
          customdata: textoHover, hovertemplate: '%{customdata}<extra></extra>',
        },
      ], {
        hovermode: 'closest',
        yaxis: { gridcolor: c.borde, zerolinecolor: c.texto2, title: { text: '°C', font: { size: 10 } } },
        shapes: [0.5, -0.5].map((y) => ({ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: y, y1: y, line: { color: c.texto2, width: 1, dash: 'dot' } })),
        // Última semana incompleta: cuántos días tiene, sobre su barra
        annotations: iParcial !== undefined && dias ? [{ x: fechas[iParcial], y: valores[iParcial], text: `${dias[iParcial]} d`, showarrow: false, yshift: valores[iParcial] >= 0 ? 9 : -9, font: { size: 9.5, color: c.texto2 } }] : [],
      }, 160, d);
      if (semanal) {
        const leyenda = [
          '<span><span class="indice-muestra" aria-hidden="true"></span>Barras: anomalía semanal (domingo a sábado, fechada en el miércoles, como NOAA CPC)</span>',
          hayPrelim ? '<span><span class="indice-muestra rayada" aria-hidden="true"></span>Rayadas: semanas con días preliminares</span>' : '',
          iParcial !== undefined ? '<span>«d»: días de la última semana, aún incompleta</span>' : '',
          hayHuecos ? '<span>Huecos: semanas con menos de 4 días con dato</span>' : '',
          '<span>Líneas punteadas: ±0.5 °C</span>',
        ].filter(Boolean).join('');
        cuerpo.appendChild(util.el(`<div class="indice-leyenda texto-pequeno texto-2">${leyenda}</div>`));
      }
      if (d.texto) cuerpo.appendChild(util.el(textoHtml(d.texto)));
      if (d.metodo) cuerpo.appendChild(util.el(`<details class="widget-metodo"><summary>Método</summary><p>${util.escapar(d.metodo)}</p></details>`));
    },
    precipitacion_provincias(w, d, cuerpo) {
      let etiquetas = d.provincias || d.etiquetas || d.nombres || [];
      let valores = d.valores || [];
      if (etiquetas.length && typeof etiquetas[0] === 'object') {
        valores = etiquetas.map((p) => p.valor);
        etiquetas = etiquetas.map((p) => p.nombre || p.provincia || p.id);
      }
      if (!etiquetas.length) { cuerpo.innerHTML = util.vacioHtml('Sin datos', 'No hay precipitación por provincia.'); return; }
      const unidad = d.unidad || 'mm';
      const c = SOME.colores();
      cuerpo.innerHTML = `<div class="texto-pequeno texto-2 mb-8">Acumulado de ${util.escapar(String(w.config.dias || d.dias || 7))} días${d.periodo && d.periodo.inicio ? ` (${util.fmtFecha(d.periodo.inicio)} – ${util.fmtFecha(d.periodo.fin)})` : ''}</div>`;
      graficoPlotly(cuerpo, [{
        x: valores, y: etiquetas, type: 'bar', orientation: 'h',
        marker: { color: c.primario },
        // Sin color fijo, Plotly pone #444 dentro de la barra clara del tema oscuro (4.2:1)
        text: valores.map((v) => util.fmtNum(v, 0) + ' ' + unidad), textposition: 'auto', textfont: { size: 10 },
        insidetextfont: { size: 10, color: c.primarioTexto }, outsidetextfont: { size: 10, color: c.texto2 },
        hovertemplate: '%{y}: %{x:.1f} ' + unidad + '<extra></extra>',
      }], {
        margin: { l: 90, r: 12, t: 6, b: 30 },
        xaxis: { gridcolor: c.borde, title: { text: unidad, font: { size: 10 } } },
        yaxis: { automargin: true, autorange: 'reversed' },
        hovermode: 'closest',
      }, Math.max(150, 22 * etiquetas.length + 40), d);
      if (Array.isArray(d.anomalias) && d.anomalias.length) cuerpo.appendChild(util.el(`<div class="kpi-texto">Anomalía media: ${util.fmtAnomalia(d.anomalias.reduce((a, b) => a + b, 0) / d.anomalias.length, unidad)}</div>`));
      if (d.texto) cuerpo.appendChild(util.el(textoHtml(d.texto)));
    },
  };

  /** Render genérico: texto y pares clave/valor escalares. */
  function renderGenerico(w, d, cuerpo) {
    const pares = Object.keys(d || {}).filter((k) => ['string', 'number', 'boolean'].includes(typeof d[k]) && !['imagen_png', 'texto', 'datos_simulados', 'procedencia', 'insignia'].includes(k));
    if (!pares.length && !d.texto) { cuerpo.innerHTML = util.vacioHtml('Sin datos', 'El widget no devolvió información.'); return; }
    cuerpo.innerHTML = `<ul class="lista">${pares.map((k) => `<li><span class="lista-texto"><small>${util.escapar(k)}</small></span><strong class="tabular">${typeof d[k] === 'number' ? util.fmtNum(d[k], Number.isInteger(d[k]) ? 0 : 2) : util.escapar(String(d[k]))}</strong></li>`).join('')}</ul>${textoHtml(d.texto)}`;
  }

  /** Aplana parámetros de una vista para el hash. */
  function aplanar(p) {
    const salida = {};
    if (!p || typeof p !== 'object') return salida;
    Object.keys(p).forEach((k) => { if (p[k] !== undefined && p[k] !== null) salida[k] = p[k]; });
    return salida;
  }

  SOME.registrar('panel', {
    titulo: 'Panel personal',
    icono: 'panel',
    grupo: 'Inicio',
    montar,
    desmontar,
  });
})();
