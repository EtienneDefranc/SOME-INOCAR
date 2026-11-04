/*
 * SOME — Módulo «Reportes» (RF-09)
 *
 * Tarjetas de plantillas (GET /api/reportes/plantillas), formulario generado a
 * partir de `parametros` (tipos texto/fecha/periodo/recorte/opcion/booleano),
 * envío con POST /api/reportes, lista de trabajos con barra de progreso y mensaje
 * actualizada cada 1.5 s mientras haya trabajos activos, «Descargar PDF» y «Ver».
 *
 * Reporte personalizado (RF-09.3): el parámetro de tipo «secciones» se edita con
 * una lista de secciones (texto, mapa, serie, perfil, comparación y tabla) que se
 * agregan, ordenan y quitan; «Guardar como plantilla» lo guarda en
 * /api/reportes/mis-plantillas (privada o compartida con el departamento) y
 * «Mis plantillas» lo vuelve a abrir con sus secciones para cambiar solo el periodo.
 */
(function () {
  'use strict';

  const SOME = window.SOME;
  if (!SOME) { console.error('SOME: app.js debe cargarse antes que reportes.js'); return; }
  const C = () => (SOME.componentes && SOME.componentes.comunes) || null;

  const INTERVALO_SONDEO_MS = 1500;
  const ESTADOS_ACTIVOS = ['en_cola', 'procesando'];
  const TEXTO_ESTADO = { en_cola: 'En cola', procesando: 'Procesando', listo: 'Listo', error: 'Error' };
  const CLASE_ESTADO = { en_cola: 'insignia-info', procesando: 'insignia-aviso', listo: 'insignia-exito', error: 'insignia-peligro' };
  // Iconos de Flaticon UIcons (sin el prefijo «fi-rr-»)
  const ICONO_PLANTILLA = { boletin_erfen: 'newspaper', informe_variable: 'chart-histogram', comparativo: 'balance-scale-left', personalizado: 'edit' };

  let estado = null;

  function estilos() {
    C().inyectarEstilos('estilos-modulo-reportes', `
      .rp-plantillas { display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:12px; }
      .rp-plantilla { cursor:pointer; border:2px solid transparent; transition: border-color .15s; text-align:left; font:inherit; color:inherit; display:flex; flex-direction:column; gap:6px; }
      .rp-plantilla:hover { border-color: var(--borde); }
      .rp-plantilla.activa { border-color: var(--primario); }
      .rp-plantilla .rp-icono { font-size:26px; color:var(--primario); display:inline-flex; }
      .rp-plantilla h3 { margin:0; font-size:15px; }
      .rp-plantilla p { margin:0; font-size:13px; color:var(--texto-2); line-height:1.45; }
      .rp-cuerpo { display:grid; grid-template-columns: minmax(320px, 2fr) minmax(320px, 3fr); gap:12px; align-items:start; }
      @media (max-width: 1000px) { .rp-cuerpo { grid-template-columns: 1fr; } }
      .rp-formulario .campo { margin-bottom:10px; }
      .rp-periodo { display:grid; grid-template-columns: 1fr 1fr; gap:8px; }
      .rp-trabajo { border:1px solid var(--borde); border-radius:var(--radio-sm, 6px); padding:10px 12px; display:flex; flex-direction:column; gap:6px; background:var(--superficie); }
      .rp-trabajo-cabecera { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }
      .rp-trabajo-titulo { font-weight:600; }
      .rp-progreso { height:8px; border-radius:4px; background:var(--superficie-2, var(--borde)); overflow:hidden; }
      .rp-progreso > div { height:100%; background:var(--primario); transition: width .4s ease; }
      .rp-progreso.listo > div { background: var(--exito); }
      .rp-progreso.error > div { background: var(--peligro); }
      .rp-trabajo-meta { font-size:12px; color:var(--texto-2); display:flex; flex-wrap:wrap; gap:4px 14px; }
      .rp-trabajo-acciones { display:flex; gap:8px; flex-wrap:wrap; }
      .rp-trabajos-lista { display:flex; flex-direction:column; gap:10px; max-height:720px; overflow:auto; padding-right:2px; }
      .rp-sondeo { font-size:12px; color:var(--texto-2); display:flex; align-items:center; gap:6px; }
      .rp-punto { width:8px; height:8px; border-radius:50%; background:var(--exito); display:inline-block; animation: rp-latido 1.5s infinite; }
      @keyframes rp-latido { 0%,100% { opacity:1 } 50% { opacity:.3 } }
      .rp-secciones { display:flex; flex-direction:column; gap:8px; }
      .rp-seccion { border:1px solid var(--borde); border-radius:var(--radio-sm, 6px); padding:8px 10px; background:var(--superficie-2, var(--superficie)); }
      .rp-seccion-cabecera { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:6px; }
      .rp-seccion-acciones { display:flex; gap:4px; }
      .rp-seccion-campos { display:grid; grid-template-columns: 1fr 1fr; gap:6px 8px; }
      .rp-seccion-campos .campo { margin:0; }
      .rp-seccion-campos .rp-ancho { grid-column: 1 / -1; }
      .rp-seccion-campos textarea { width:100%; min-height:72px; resize:vertical; font:inherit; }
      .rp-plantilla .rp-meta { font-size:12px; color:var(--texto-2); display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
      .rp-plantilla-guardada { position:relative; }
      .rp-plantilla-guardada .rp-eliminar { position:absolute; top:8px; right:8px; }
    `);
  }

  // ---------------------------------------------------------------------------
  // Plantillas y formulario
  // ---------------------------------------------------------------------------

  async function cargarPlantillas() {
    const c = C();
    const zona = estado.nodos.plantillas;
    c.mostrarCarga(zona, 'Cargando plantillas…');
    let plantillas;
    try {
      plantillas = await SOME.api.get('/api/reportes/plantillas');
    } catch (e) {
      c.mostrarError(zona, e && e.message ? e.message : 'No se pudieron cargar las plantillas', () => cargarPlantillas());
      return;
    }
    if (!estado) return;
    if (!Array.isArray(plantillas) || !plantillas.length) {
      c.mostrarVacio(zona, 'No hay plantillas de reporte', 'El administrador aún no ha configurado plantillas.', 'file-pdf');
      return;
    }
    estado.plantillas = plantillas;
    zona.innerHTML = '';
    plantillas.forEach((pl) => {
      const tarjeta = c.crear(`<button type="button" class="tarjeta rp-plantilla" data-id="${c.esc(pl.id)}" aria-pressed="false">
        <span class="rp-icono" aria-hidden="true">${c.iconoFi(ICONO_PLANTILLA[pl.id] || 'file-pdf')}</span>
        <h3>${c.esc(pl.nombre)}</h3>
        <p>${c.esc(pl.descripcion || '')}</p>
        <span class="texto-2" style="font-size:12px">${(pl.parametros || []).length} parámetro${(pl.parametros || []).length === 1 ? '' : 's'}</span>
      </button>`);
      tarjeta.addEventListener('click', () => elegirPlantilla(pl.id));
      zona.appendChild(tarjeta);
    });
    const inicial = plantillas.find((x) => x.id === estado.plantillaInicial) ? estado.plantillaInicial : plantillas[0].id;
    elegirPlantilla(inicial, true);
    cargarMisPlantillas();
  }

  // Plantillas guardadas del reporte personalizado: las propias y las compartidas con el departamento
  async function cargarMisPlantillas() {
    const c = C();
    const caja = estado.nodos.misPlantillasCaja;
    let guardadas;
    try {
      guardadas = await SOME.api.get('/api/reportes/mis-plantillas');
    } catch (e) {
      return;   // sin esta lista el módulo sigue funcionando con las plantillas predefinidas
    }
    if (!estado) return;
    estado.guardadas = Array.isArray(guardadas) ? guardadas : [];
    const zona = estado.nodos.misPlantillas;
    zona.innerHTML = '';
    caja.hidden = !estado.guardadas.length;
    estado.guardadas.forEach((g) => {
      const n = ((g.parametros || {}).secciones || []).length;
      const origen = g.propia ? (g.compartida ? 'Compartida con su departamento' : 'Privada') : `De ${g.propietario && g.propietario.nombre || 'otro usuario'}`;
      const tarjeta = c.crear(`<div class="tarjeta rp-plantilla rp-plantilla-guardada" role="button" tabindex="0" data-guardada="${c.esc(g.id)}">
        <span class="rp-icono" aria-hidden="true">${c.iconoFi('document')}</span>
        <h3>${c.esc(g.nombre)}</h3>
        <p>${c.esc(g.descripcion || (g.parametros || {}).titulo || '')}</p>
        <span class="rp-meta"><span class="insignia ${g.compartida ? 'insignia-info' : ''}">${c.esc(origen)}</span>${n} sección${n === 1 ? '' : 'es'}</span>
      </div>`);
      const abrir = () => {
        const pre = { ...(g.parametros || {}) };
        delete pre.periodo;   // se propone el periodo actual: la plantilla se repite cambiando solo el periodo
        elegirPlantilla('personalizado', false, pre, g.id);
      };
      tarjeta.addEventListener('click', abrir);
      tarjeta.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); abrir(); } });
      if (g.propia) {
        const quitar = c.crear(`<button type="button" class="boton boton-icono rp-eliminar" aria-label="Eliminar la plantilla «${c.esc(g.nombre)}»" title="Eliminar la plantilla">${c.iconoFi('trash')}</button>`);
        quitar.addEventListener('click', async (e) => {
          e.stopPropagation();
          const ok = await window.util.confirmar(`¿Eliminar la plantilla «${g.nombre}»? Los reportes ya generados no se borran.`,
            { aceptar: 'Eliminar', peligro: true });
          if (!ok) return;
          try {
            await SOME.api.del(`/api/reportes/mis-plantillas/${g.id}`);
            c.toast('Plantilla eliminada.', 'exito');
            cargarMisPlantillas();
          } catch (err) {
            c.toast(err && err.message ? err.message : 'No se pudo eliminar la plantilla.', 'error');
          }
        });
        tarjeta.appendChild(quitar);
      }
      zona.appendChild(tarjeta);
    });
  }

  function elegirPlantilla(id, esInicial, prellenado, guardadaId) {
    const c = C();
    const pl = (estado.plantillas || []).find((x) => x.id === id);
    if (!pl) return;
    estado.plantilla = pl;
    estado.nodos.plantillas.querySelectorAll('.rp-plantilla').forEach((t) => {
      const activa = t.dataset.id === id && guardadaId == null;
      t.classList.toggle('activa', activa);
      t.setAttribute('aria-pressed', activa ? 'true' : 'false');
    });
    estado.nodos.misPlantillas.querySelectorAll('.rp-plantilla').forEach((t) => {
      t.classList.toggle('activa', guardadaId != null && t.dataset.guardada === String(guardadaId));
    });
    construirFormulario(pl, prellenado || (esInicial ? estado.prellenado : {}));
    if (!esInicial) estado.nodos.formularioCaja.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function normalizarOpciones(opciones) {
    return (opciones || []).map((o) => {
      if (o == null) return null;
      if (typeof o === 'object') {
        const valor = o.valor ?? o.id ?? o.value;
        return { valor: String(valor), etiqueta: String(o.etiqueta ?? o.nombre ?? o.label ?? valor) };
      }
      return { valor: String(o), etiqueta: String(o) };
    }).filter(Boolean);
  }

  function opcionesCatalogo(param, valoresActuales) {
    const c = C();
    const cat = c.catalogo();
    if (param.id === 'fuente') return (cat.fuentes || []).filter((f) => f.habilitada !== false).map((f) => ({ valor: f.id, etiqueta: f.nombre }));
    if (param.id === 'variable') {
      const fuenteId = valoresActuales.fuente || (cat.fuentes[0] || {}).id;
      const f = c.fuente(fuenteId);
      return f ? (f.variables || []).filter((v) => v.habilitada !== false && !v.es_vector).map((v) => ({ valor: v.id, etiqueta: `${v.nombre} (${v.unidad})` })) : [];
    }
    if (param.id === 'profundidad') return (cat.profundidades || [0]).map((z) => ({ valor: String(z), etiqueta: `${c.fmtNum(z, 0)} m` }));
    if (param.id === 'periodo_referencia') return (cat.periodos_referencia || ['1991-2020']).map((p) => ({ valor: p, etiqueta: p }));
    return null;
  }

  // Editor del parámetro «secciones» del reporte personalizado (RF-09.3)
  function editorSecciones(pl, param, inicial, id) {
    const c = C();
    const tipos = pl.tipos_seccion || [];
    const maximo = pl.maximo_secciones || 12;
    const cat = c.catalogo();
    const fuentes = (cat.fuentes || []).filter((f) => f.habilitada !== false);
    const variablesDe = (fid) => {
      const f = c.fuente(fid);
      return f ? (f.variables || []).filter((v) => v.habilitada !== false && !v.es_vector) : [];
    };
    let secciones = (Array.isArray(inicial) ? inicial : []).map((s) => ({ ...s }));
    const campo = c.crear(`<fieldset class="campo" style="border:0;padding:0;margin:0 0 10px">
      <legend style="font-weight:600;margin-bottom:4px">${c.esc(param.etiqueta)}</legend>
      <p class="texto-2" style="font-size:12px;margin:0 0 8px">Cada sección usa el periodo y el periodo de referencia del reporte. Ordénelas con las flechas.</p>
      <div class="rp-secciones"></div>
      <div class="fila" style="gap:8px;margin-top:8px;align-items:flex-end">
        <div class="campo" style="margin:0;flex:1"><label for="${id}-nueva">Agregar sección</label><select id="${id}-nueva"></select></div>
        <button type="button" class="boton boton-secundario" data-accion="agregar">${c.iconoFi('plus-small')} Agregar</button>
      </div></fieldset>`);
    const lista = campo.querySelector('.rp-secciones');
    const selNueva = campo.querySelector(`#${id}-nueva`);
    const botonAgregar = campo.querySelector('[data-accion="agregar"]');
    tipos.forEach((t) => selNueva.appendChild(new Option(t.nombre, t.id)));

    const nuevaSeccion = (tipo) => {
      if (tipo === 'texto') return { tipo, titulo: '', texto: '' };
      if (tipo === 'perfil') return { tipo, titulo: '', variable: 'thetao', lat: -2, lon: -85 };
      const f = fuentes[0] || {};
      const v = variablesDe(f.id)[0] || {};
      const s = { tipo, titulo: '', fuente: f.id, variable: v.id, recorte: 'nino12' };
      if (tipo === 'mapa') s.modo = 'valor';
      return s;
    };

    const selectRecorte = (idc, valor) => {
      const sel = c.crear(`<select id="${idc}"></select>`);
      const nombreTipo = { area: 'Áreas', region_nino: 'Regiones Niño', provincia: 'Provincias', cuenca: 'Cuencas' };
      const grupos = {};
      (cat.recortes || []).forEach((r) => { (grupos[r.tipo] = grupos[r.tipo] || []).push(r); });
      Object.keys(grupos).forEach((t) => {
        const og = document.createElement('optgroup'); og.label = nombreTipo[t] || t;
        grupos[t].forEach((r) => og.appendChild(new Option(r.nombre, r.id)));
        sel.appendChild(og);
      });
      if (valor && [...sel.options].some((o) => o.value === valor)) sel.value = valor;
      return sel;
    };

    function tarjeta(s, i) {
      const tipo = tipos.find((t) => t.id === s.tipo) || { nombre: s.tipo, campos: [], descripcion: '' };
      const pre = `${id}-s${i}`;
      const nodo = c.crear(`<div class="rp-seccion">
        <div class="rp-seccion-cabecera"><span><strong>${i + 1}. ${c.esc(tipo.nombre)}</strong>
          <span class="texto-2" style="font-size:12px"> · ${c.esc(tipo.descripcion || '')}</span></span>
          <span class="rp-seccion-acciones">
            <button type="button" class="boton boton-icono" data-mover="-1" aria-label="Subir la sección ${i + 1}" title="Subir" ${i === 0 ? 'disabled' : ''}>${c.iconoFi('arrow-small-up')}</button>
            <button type="button" class="boton boton-icono" data-mover="1" aria-label="Bajar la sección ${i + 1}" title="Bajar" ${i === secciones.length - 1 ? 'disabled' : ''}>${c.iconoFi('arrow-small-down')}</button>
            <button type="button" class="boton boton-icono" data-quitar aria-label="Quitar la sección ${i + 1}" title="Quitar">${c.iconoFi('trash')}</button>
          </span></div>
        <div class="rp-seccion-campos"></div></div>`);
      const zona = nodo.querySelector('.rp-seccion-campos');
      const agregarCampo = (etiqueta, control, ancho) => {
        const envoltura = c.crear(`<div class="campo${ancho ? ' rp-ancho' : ''}"><label for="${control.id}">${c.esc(etiqueta)}</label></div>`);
        envoltura.appendChild(control);
        zona.appendChild(envoltura);
      };
      const titulo = c.crear(`<input id="${pre}-titulo" type="text" maxlength="120" placeholder="Si lo deja vacío, se usa uno automático">`);
      titulo.value = s.titulo || '';
      titulo.addEventListener('input', () => { s.titulo = titulo.value; });
      agregarCampo('Título de la sección', titulo, true);

      if (s.tipo === 'texto') {
        const texto = c.crear(`<textarea id="${pre}-texto" maxlength="4000" placeholder="Separe los párrafos con una línea en blanco"></textarea>`);
        texto.value = s.texto || '';
        texto.addEventListener('input', () => { s.texto = texto.value; });
        agregarCampo('Texto', texto, true);
      } else if (s.tipo === 'perfil') {
        const variable = c.crear(`<select id="${pre}-variable"><option value="thetao">Temperatura del agua</option><option value="so">Salinidad</option></select>`);
        variable.value = s.variable || 'thetao';
        variable.addEventListener('change', () => { s.variable = variable.value; });
        agregarCampo('Variable (Copernicus Marine)', variable, true);
        const lat = c.crear(`<input id="${pre}-lat" type="number" step="0.01" min="-90" max="90">`);
        const lon = c.crear(`<input id="${pre}-lon" type="number" step="0.01" min="-180" max="180">`);
        lat.value = s.lat != null ? s.lat : -2; lon.value = s.lon != null ? s.lon : -85;
        lat.addEventListener('input', () => { s.lat = lat.value === '' ? null : Number(lat.value); });
        lon.addEventListener('input', () => { s.lon = lon.value === '' ? null : Number(lon.value); });
        agregarCampo('Latitud (°, negativa al sur)', lat);
        agregarCampo('Longitud (°, negativa al oeste)', lon);
      } else {
        const fuente = c.crear(`<select id="${pre}-fuente"></select>`);
        fuentes.forEach((f) => fuente.appendChild(new Option(f.nombre, f.id)));
        if ([...fuente.options].some((o) => o.value === s.fuente)) fuente.value = s.fuente; else s.fuente = fuente.value;
        const variable = c.crear(`<select id="${pre}-variable"></select>`);
        const llenarVariables = () => {
          variable.innerHTML = '';
          variablesDe(fuente.value).forEach((v) => variable.appendChild(new Option(`${v.nombre} (${v.unidad})`, v.id)));
          if ([...variable.options].some((o) => o.value === s.variable)) variable.value = s.variable; else s.variable = variable.value;
        };
        llenarVariables();
        fuente.addEventListener('change', () => { s.fuente = fuente.value; llenarVariables(); });
        variable.addEventListener('change', () => { s.variable = variable.value; });
        agregarCampo('Fuente', fuente);
        agregarCampo('Variable', variable);
        const recorte = selectRecorte(`${pre}-recorte`, s.recorte);
        s.recorte = recorte.value;
        recorte.addEventListener('change', () => { s.recorte = recorte.value; });
        agregarCampo('Área (recorte)', recorte, s.tipo !== 'mapa');
        if (s.tipo === 'mapa') {
          const modo = c.crear(`<select id="${pre}-modo"><option value="valor">Promedio del periodo</option><option value="anomalia">Anomalía</option></select>`);
          modo.value = s.modo === 'anomalia' ? 'anomalia' : 'valor';
          modo.addEventListener('change', () => { s.modo = modo.value; });
          agregarCampo('Mostrar', modo);
        }
      }
      nodo.querySelectorAll('[data-mover]').forEach((b) => b.addEventListener('click', () => {
        const j = i + Number(b.dataset.mover);
        if (j < 0 || j >= secciones.length) return;
        [secciones[i], secciones[j]] = [secciones[j], secciones[i]];
        dibujar();
      }));
      nodo.querySelector('[data-quitar]').addEventListener('click', () => { secciones.splice(i, 1); dibujar(); });
      return nodo;
    }

    function dibujar() {
      lista.innerHTML = '';
      if (!secciones.length) lista.appendChild(c.crear('<p class="texto-2" style="font-size:13px;margin:0">Agregue al menos una sección.</p>'));
      secciones.forEach((s, i) => lista.appendChild(tarjeta(s, i)));
      botonAgregar.disabled = secciones.length >= maximo;
      botonAgregar.title = botonAgregar.disabled ? `Máximo ${maximo} secciones` : '';
    }
    botonAgregar.addEventListener('click', () => {
      if (secciones.length >= maximo) return;
      secciones.push(nuevaSeccion(selNueva.value));
      dibujar();
      const ultima = lista.lastElementChild;
      if (ultima) ultima.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
    dibujar();
    return { campo, leer: () => secciones.map((s) => ({ ...s })) };
  }

  function periodoDesde(valor) {
    const c = C();
    if (!valor) return null;
    if (typeof valor === 'object' && valor.inicio) return { inicio: valor.inicio, fin: valor.fin };
    return c.analizarPeriodo(valor);
  }

  function construirFormulario(pl, prellenado) {
    const c = C();
    const caja = estado.nodos.formularioCaja;
    caja.innerHTML = '';
    const usuario = SOME.estado.usuario || {};
    const corte = c.fechaCorte();
    const form = c.crear(`<form class="rp-formulario" autocomplete="off" novalidate>
      <h3 class="tarjeta-titulo" style="margin-top:0">${c.esc(pl.nombre)}</h3>
      <p class="texto-2" style="font-size:13px;margin:0 0 12px">${c.esc(pl.descripcion || '')}</p>
      <div class="rp-campos"></div>
      <div class="fila" style="justify-content:flex-end;margin-top:12px;align-items:center;gap:10px">
        <span class="texto-2" style="font-size:12px;flex:1">El PDF se genera en segundo plano; puede seguir trabajando mientras tanto.</span>
        ${pl.id === 'personalizado' ? '<button type="button" class="boton boton-secundario" data-accion="guardar-plantilla">Guardar como plantilla</button>' : ''}
        <button type="submit" class="boton boton-primario">Generar</button>
      </div>
    </form>`);
    const campos = form.querySelector('.rp-campos');
    const controles = {};
    const valoresActuales = {};

    const valorInicial = (param) => {
      const pre = prellenado || {};
      if (param.id in pre && pre[param.id] !== '') return pre[param.id];
      if (param.id === 'periodo_referencia' && pre.referencia) return pre.referencia;
      if (param.por_defecto != null && param.por_defecto !== '') return param.por_defecto;
      if (param.id === 'autor') return usuario.nombre || '';
      if (param.tipo === 'periodo') return { inicio: c.sumarDias(corte, -14), fin: corte };
      if (param.tipo === 'fecha') return corte;
      if (param.tipo === 'recorte') return 'nino12';
      if (param.tipo === 'booleano') return true;
      return '';
    };

    (pl.parametros || []).forEach((param) => {
      const id = `rp-${pl.id}-${param.id}`;
      const inicial = valorInicial(param);
      let campo;
      if (param.tipo === 'periodo') {
        const per = periodoDesde(inicial) || { inicio: c.sumarDias(corte, -14), fin: corte };
        campo = c.crear(`<fieldset class="campo" style="border:0;padding:0;margin:0 0 10px"><legend style="font-weight:600;margin-bottom:4px">${c.esc(param.etiqueta)}</legend>
          <div class="rp-periodo">
            <div class="campo" style="margin:0"><label for="${id}-inicio">Desde</label><input id="${id}-inicio" type="date" min="${c.fechaInicio()}" max="${corte}" value="${c.esc(per.inicio)}" required></div>
            <div class="campo" style="margin:0"><label for="${id}-fin">Hasta</label><input id="${id}-fin" type="date" min="${c.fechaInicio()}" max="${corte}" value="${c.esc(per.fin)}" required></div>
          </div></fieldset>`);
        campo.querySelectorAll('input[type="date"]').forEach((inp) => c.anotarFecha(inp));
        controles[param.id] = () => {
          const i = campo.querySelector(`#${id}-inicio`).value, f = campo.querySelector(`#${id}-fin`).value;
          if (!c.esIso(i) || !c.esIso(f)) return null;
          return { inicio: c.minIso(i, f), fin: c.maxIso(i, f) };
        };
      } else if (param.tipo === 'fecha') {
        campo = c.crear(`<div class="campo"><label for="${id}">${c.esc(param.etiqueta)}</label><input id="${id}" type="date" min="${c.fechaInicio()}" max="${corte}" value="${c.esc(inicial)}" required></div>`);
        c.anotarFecha(campo.querySelector('input'));
        controles[param.id] = () => campo.querySelector('input').value || null;
      } else if (param.tipo === 'recorte') {
        campo = c.crear(`<div class="campo"><label for="${id}">${c.esc(param.etiqueta)}</label><select id="${id}"></select></div>`);
        const sel = campo.querySelector('select');
        const recortes = c.catalogo().recortes || [];
        const grupos = {};
        const nombreTipo = { area: 'Áreas', region_nino: 'Regiones Niño', provincia: 'Provincias', cuenca: 'Cuencas' };
        recortes.forEach((r) => { (grupos[r.tipo] = grupos[r.tipo] || []).push(r); });
        Object.keys(grupos).forEach((t) => {
          const og = document.createElement('optgroup'); og.label = nombreTipo[t] || t;
          grupos[t].forEach((r) => og.appendChild(new Option(r.nombre, r.id)));
          sel.appendChild(og);
        });
        normalizarOpciones(param.opciones).forEach((o) => { if (!recortes.find((r) => r.id === o.valor)) sel.appendChild(new Option(o.etiqueta, o.valor)); });
        if (inicial && [...sel.options].some((o) => o.value === String(inicial))) sel.value = String(inicial);
        controles[param.id] = () => sel.value || null;
      } else if (param.tipo === 'opcion') {
        campo = c.crear(`<div class="campo"><label for="${id}">${c.esc(param.etiqueta)}</label><select id="${id}"></select></div>`);
        const sel = campo.querySelector('select');
        const llenar = () => {
          const actual = sel.value;
          sel.innerHTML = '';
          let ops = normalizarOpciones(param.opciones);
          const delCatalogo = opcionesCatalogo(param, valoresActuales);
          if (delCatalogo && (param.id === 'variable' || !ops.length)) ops = delCatalogo;
          ops.forEach((o) => sel.appendChild(new Option(o.etiqueta, o.valor)));
          const deseado = sel.dataset.inicial != null ? sel.dataset.inicial : actual;
          if (deseado && [...sel.options].some((o) => o.value === String(deseado))) sel.value = String(deseado);
          delete sel.dataset.inicial;
          valoresActuales[param.id] = sel.value;
        };
        if (inicial != null && inicial !== '') sel.dataset.inicial = String(inicial);
        llenar();
        sel.addEventListener('change', () => {
          valoresActuales[param.id] = sel.value;
          if (param.id === 'fuente' && controles.__variable_llenar) controles.__variable_llenar();
        });
        if (param.id === 'variable') controles.__variable_llenar = llenar;
        controles[param.id] = () => {
          const val = sel.value;
          if (param.id === 'profundidad' && val !== '' && Number.isFinite(Number(val))) return Number(val);
          return val === '' ? null : val;
        };
      } else if (param.tipo === 'secciones') {
        const editor = editorSecciones(pl, param, inicial, id);
        campo = editor.campo;
        controles[param.id] = editor.leer;
      } else if (param.tipo === 'booleano') {
        const marcado = inicial === true || inicial === 'true' || inicial === '1' || inicial === 'si' || inicial === 'sí';
        campo = c.crear(`<div class="campo"><label for="${id}" style="display:flex;align-items:center;gap:8px"><input id="${id}" type="checkbox" ${marcado ? 'checked' : ''}> ${c.esc(param.etiqueta)}</label></div>`);
        controles[param.id] = () => campo.querySelector('input').checked;
      } else {
        campo = c.crear(`<div class="campo"><label for="${id}">${c.esc(param.etiqueta)}</label><input id="${id}" type="text" maxlength="200" value="${c.esc(inicial)}" ${param.id === 'autor' ? 'placeholder="Nombre de quien firma el reporte"' : ''}></div>`);
        controles[param.id] = () => campo.querySelector('input').value.trim();
      }
      campos.appendChild(campo);
    });
    // Si «variable» se construyó antes que «fuente», se vuelve a llenar con la fuente definitiva
    if (controles.__variable_llenar) controles.__variable_llenar();

    form.addEventListener('submit', (e) => { e.preventDefault(); enviar(pl, controles, form); });
    const guardar = form.querySelector('[data-accion="guardar-plantilla"]');
    if (guardar) guardar.addEventListener('click', () => guardarComoPlantilla(pl, controles));
    caja.appendChild(form);
  }

  // Lee los parámetros del formulario; null (con un aviso) si falta alguno obligatorio
  function leerParametros(pl, controles) {
    const c = C();
    const parametros = {};
    for (const param of pl.parametros || []) {
      const leer = controles[param.id];
      if (!leer) continue;
      const val = leer();
      const vacio = val == null || val === '' || (Array.isArray(val) && !val.length);
      if (vacio && param.tipo !== 'booleano' && param.tipo !== 'texto') {
        c.toast(param.tipo === 'secciones' ? 'Agregue al menos una sección al reporte.' : `Complete el campo «${param.etiqueta}».`, 'aviso');
        return null;
      }
      parametros[param.id] = val;
    }
    return parametros;
  }

  function guardarComoPlantilla(pl, controles) {
    const c = C();
    const parametros = leerParametros(pl, controles);
    if (!parametros) return;
    const contenido = c.crear(`<div>
      <div class="campo"><label for="rp-gp-nombre">Nombre de la plantilla</label><input id="rp-gp-nombre" type="text" maxlength="120"></div>
      <div class="campo"><label for="rp-gp-descripcion">Descripción (opcional)</label><input id="rp-gp-descripcion" type="text" maxlength="400" placeholder="Para qué sirve o a quién va dirigido"></div>
      <div class="campo"><label for="rp-gp-compartida" style="display:flex;align-items:center;gap:8px"><input id="rp-gp-compartida" type="checkbox"> Compartir con mi departamento</label></div>
      <p class="texto-2" style="font-size:12px;margin:0">Se guardan el título, el destinatario y las secciones. Al abrirla, solo hay que elegir el periodo.</p>
    </div>`);
    contenido.querySelector('#rp-gp-nombre').value = parametros.titulo || '';
    c.abrirModal({
      titulo: 'Guardar como plantilla',
      contenido,
      acciones: [
        { texto: 'Cancelar', clase: 'boton-secundario' },
        {
          texto: 'Guardar', clase: 'boton-primario',
          accion: async () => {
            const nombre = contenido.querySelector('#rp-gp-nombre').value.trim();
            if (!nombre) { c.toast('Escriba un nombre para la plantilla.', 'aviso'); return false; }
            await SOME.api.post('/api/reportes/mis-plantillas', {
              nombre,
              descripcion: contenido.querySelector('#rp-gp-descripcion').value.trim(),
              compartida: contenido.querySelector('#rp-gp-compartida').checked,
              parametros,
            });
            c.toast(`Plantilla «${nombre}» guardada.`, 'exito');
            cargarMisPlantillas();
            return true;
          },
        },
      ],
    });
    setTimeout(() => { const n = document.getElementById('rp-gp-nombre'); if (n) n.focus(); }, 50);
  }

  async function enviar(pl, controles, form) {
    const c = C();
    const parametros = leerParametros(pl, controles);
    if (!parametros) return;
    const boton = form.querySelector('button[type="submit"]');
    boton.disabled = true;
    boton.textContent = 'Enviando…';
    try {
      const r = await SOME.api.post('/api/reportes', { plantilla: pl.id, parametros });
      c.toast(`Reporte «${pl.nombre}» en cola${r && r.trabajo_id ? ` (trabajo ${r.trabajo_id})` : ''}.`, 'exito');
      await cargarTrabajos();
      estado.nodos.trabajosCaja.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (e) {
      c.toast(e && e.message ? e.message : 'No se pudo encolar el reporte.', 'error');
    } finally {
      if (boton.isConnected) { boton.disabled = false; boton.textContent = 'Generar'; }
    }
  }

  // ---------------------------------------------------------------------------
  // Trabajos
  // ---------------------------------------------------------------------------

  async function cargarTrabajos(silencioso) {
    const c = C();
    if (!estado) return;
    const zona = estado.nodos.trabajos;
    if (!silencioso && !estado.trabajos) c.mostrarCarga(zona, 'Cargando trabajos…');
    let trabajos;
    try {
      trabajos = await SOME.api.get('/api/trabajos');
    } catch (e) {
      if (!estado) return;
      if (!estado.trabajos) c.mostrarError(zona, e && e.message ? e.message : 'No se pudieron cargar los trabajos', () => cargarTrabajos());
      else c.toast('No se pudo actualizar la lista de trabajos.', 'aviso');
      detenerSondeo();
      return;
    }
    if (!estado) return;
    estado.trabajos = Array.isArray(trabajos) ? trabajos : [];
    dibujarTrabajos();
    const hayActivos = estado.trabajos.some((t) => ESTADOS_ACTIVOS.includes(t.estado));
    if (hayActivos) iniciarSondeo(); else detenerSondeo();
  }

  function dibujarTrabajos() {
    const c = C();
    const zona = estado.nodos.trabajos;
    const lista = [...estado.trabajos].sort((x, y) => String(y.creado || '').localeCompare(String(x.creado || '')));
    if (!lista.length) {
      c.mostrarVacio(zona, 'Aún no hay reportes', 'Elija una plantilla, complete los parámetros y pulse «Generar».', 'file-pdf');
      return;
    }
    zona.innerHTML = '';
    const contenedor = c.crear('<div class="rp-trabajos-lista"></div>');
    lista.forEach((t) => contenedor.appendChild(tarjetaTrabajo(t)));
    zona.appendChild(contenedor);
    const activos = lista.filter((t) => ESTADOS_ACTIVOS.includes(t.estado)).length;
    estado.nodos.sondeo.innerHTML = activos
      ? `<span class="rp-punto" aria-hidden="true"></span> ${activos} trabajo${activos === 1 ? '' : 's'} en curso · se actualiza cada 1.5 s`
      : `${lista.length} trabajo${lista.length === 1 ? '' : 's'}`;
  }

  function tarjetaTrabajo(t) {
    const c = C();
    const estadoT = t.estado || 'en_cola';
    const progreso = Math.max(0, Math.min(100, Number(t.progreso) || (estadoT === 'listo' ? 100 : 0)));
    const archivo = t.archivo || (t.tipo === 'reporte' && estadoT === 'listo' ? `/api/reportes/${t.id}/descargar` : null);
    const nodo = c.crear(`<article class="rp-trabajo" data-id="${c.esc(t.id)}">
      <div class="rp-trabajo-cabecera">
        <div><div class="rp-trabajo-titulo">${c.esc(t.titulo || `Trabajo ${t.id}`)}</div>
          <div class="texto-2" style="font-size:12px">${c.esc(t.tipo === 'exportacion' ? 'Exportación' : 'Reporte PDF')} · n.º ${c.esc(t.id)}</div></div>
        <span class="insignia ${CLASE_ESTADO[estadoT] || ''}">${c.esc(TEXTO_ESTADO[estadoT] || estadoT)}</span>
      </div>
      <div class="rp-progreso ${estadoT}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progreso}" aria-label="Progreso"><div style="width:${progreso}%"></div></div>
      <div class="texto-2" style="font-size:13px">${c.esc(t.mensaje || (estadoT === 'en_cola' ? 'Esperando turno…' : ''))}${ESTADOS_ACTIVOS.includes(estadoT) ? ` <span style="font-family:var(--mono, monospace)">${c.fmtNum(progreso, 0)} %</span>` : ''}</div>
      <div class="rp-trabajo-meta">
        ${t.procedencia && estadoT === 'listo' ? SOME.insigniaProcedenciaHtml(t) : ''}
        <span>Creado: ${c.esc(c.fmtFechaHora(t.creado))}</span>
        ${t.terminado ? `<span>Terminado: ${c.esc(c.fmtFechaHora(t.terminado))}</span>` : ''}
      </div>
      <div class="rp-trabajo-acciones"></div>
    </article>`);
    const acciones = nodo.querySelector('.rp-trabajo-acciones');
    if (estadoT === 'listo' && archivo) {
      const descargar = c.crear('<button type="button" class="boton boton-primario">Descargar PDF</button>');
      descargar.addEventListener('click', async () => {
        descargar.disabled = true;
        try {
          await c.descargarArchivo(archivo, null, nombrePdf(t));
          c.toast('PDF descargado.', 'exito');
        } catch (e) {
          c.toast(e && e.message ? e.message : 'No se pudo descargar el PDF.', 'error');
        } finally { descargar.disabled = false; }
      });
      const ver = c.crear('<button type="button" class="boton boton-secundario">Ver</button>');
      ver.addEventListener('click', async () => {
        // El PDF se descarga con la cabecera Authorization y se abre como URL de objeto:
        // el token de sesión nunca viaja en la dirección (ni al historial ni a los registros).
        ver.disabled = true;
        try {
          await c.abrirPdfEnPestana(archivo);
        } catch (e) {
          c.toast(e && e.message ? e.message : 'No se pudo abrir el PDF.', 'error');
        } finally { ver.disabled = false; }
      });
      acciones.appendChild(descargar);
      acciones.appendChild(ver);
    } else if (estadoT === 'error') {
      acciones.appendChild(c.crear(`<span class="texto-2" style="font-size:12px">${c.esc(t.mensaje ? '' : 'El trabajo terminó con error. Vuelva a generarlo con otros parámetros.')}</span>`));
    }
    return nodo;
  }

  function nombrePdf(t) {
    const fecha = String(t.terminado || t.creado || C().fechaCorte()).slice(0, 10).replace(/-/g, '');
    const base = String(t.titulo || `reporte_${t.id}`).toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 40);
    return `SOME_${base || 'reporte'}_${fecha}.pdf`;
  }

  function iniciarSondeo() {
    if (!estado || estado.temporizador) return;
    estado.temporizador = setInterval(() => {
      if (!estado) return;
      if (estado.sondeando) return; // evita solapar peticiones lentas
      estado.sondeando = true;
      cargarTrabajos(true).finally(() => { if (estado) estado.sondeando = false; });
    }, INTERVALO_SONDEO_MS);
  }

  function detenerSondeo() {
    if (estado && estado.temporizador) { clearInterval(estado.temporizador); estado.temporizador = null; }
  }

  // ---------------------------------------------------------------------------
  // Montaje
  // ---------------------------------------------------------------------------

  function prellenadoDesdeHash(p) {
    const c = C();
    const pre = {};
    if (!p) return pre;
    ['fuente', 'variable', 'recorte', 'autor', 'profundidad', 'periodo_referencia', 'incluir_precipitacion'].forEach((k) => { if (p[k] != null && p[k] !== '') pre[k] = p[k]; });
    if (p.referencia) pre.periodo_referencia = p.referencia;
    ['periodo', 'periodo_a', 'periodo_b'].forEach((k) => { const per = c.analizarPeriodo(p[k]); if (per) pre[k] = per; });
    if (!pre.recorte && p.area) { const a = c.analizarArea(p.area); if (a && a.tipo === 'recorte') pre.recorte = a.recorte; }
    return pre;
  }

  SOME.registrar('reportes', {
    titulo: 'Reportes',
    icono: 'reportes',
    grupo: 'Productos',

    async montar(contenedor, params) {
      const c = C();
      if (!c) { contenedor.innerHTML = '<div class="alerta alerta-peligro">Falta consultas.js (componentes comunes).</div>'; return; }
      estilos();
      await c.asegurarCatalogo();
      contenedor.innerHTML = '';
      const p = params || {};

      const pagina = c.crear(`<div class="modulo-reportes" style="display:flex;flex-direction:column;gap:12px">
        <div>
          <div class="fila" style="justify-content:space-between;align-items:center;margin-bottom:8px">
            <h2 style="margin:0;font-size:18px">Plantillas de reporte</h2>
          </div>
          <div class="rp-plantillas"></div>
        </div>
        <div class="rp-mis-plantillas-caja" hidden>
          <h2 style="margin:0 0 8px;font-size:16px">Mis plantillas</h2>
          <div class="rp-plantillas rp-mis-plantillas"></div>
        </div>
        <div class="rp-cuerpo">
          <div class="tarjeta rp-formulario-caja"><div class="esqueleto" style="min-height:120px"></div></div>
          <div class="tarjeta rp-trabajos-caja">
            <div class="fila" style="justify-content:space-between;align-items:center;margin-bottom:10px">
              <h3 class="tarjeta-titulo" style="margin:0">Trabajos</h3>
              <div class="fila" style="align-items:center;gap:10px">
                <span class="rp-sondeo"></span>
                <button type="button" class="boton boton-secundario" data-accion="actualizar">Actualizar</button>
              </div>
            </div>
            <div class="rp-trabajos"></div>
          </div>
        </div>
      </div>`);
      contenedor.appendChild(pagina);
      pagina.querySelector('h2').insertAdjacentElement('afterend', c.insigniaSimulado(SOME.estado.catalogo));

      estado = {
        plantillas: [], plantilla: null, plantillaInicial: p.plantilla || null, prellenado: prellenadoDesdeHash(p),
        trabajos: null, temporizador: null, sondeando: false, guardadas: [],
        nodos: {
          plantillas: pagina.querySelector('.rp-plantillas'),
          misPlantillasCaja: pagina.querySelector('.rp-mis-plantillas-caja'),
          misPlantillas: pagina.querySelector('.rp-mis-plantillas'),
          formularioCaja: pagina.querySelector('.rp-formulario-caja'),
          trabajosCaja: pagina.querySelector('.rp-trabajos-caja'),
          trabajos: pagina.querySelector('.rp-trabajos'),
          sondeo: pagina.querySelector('.rp-sondeo'),
        },
      };
      pagina.querySelector('[data-accion="actualizar"]').addEventListener('click', () => cargarTrabajos());

      await Promise.all([cargarPlantillas(), cargarTrabajos()]);
    },

    desmontar() {
      detenerSondeo();
      estado = null;
    },
  });
})();
