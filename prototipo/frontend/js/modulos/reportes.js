/*
 * SOME — Módulo «Reportes» (RF-09)
 *
 * Tarjetas de plantillas (GET /api/reportes/plantillas), formulario generado a
 * partir de `parametros` (tipos texto/fecha/periodo/recorte/opcion/booleano),
 * envío con POST /api/reportes, lista de trabajos con barra de progreso y mensaje
 * actualizada cada 1.5 s mientras haya trabajos activos, «Descargar PDF» y «Ver».
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
  const ICONO_PLANTILLA = { boletin_erfen: 'newspaper', informe_variable: 'chart-histogram', comparativo: 'balance-scale-left' };

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
  }

  function elegirPlantilla(id, esInicial) {
    const c = C();
    const pl = (estado.plantillas || []).find((x) => x.id === id);
    if (!pl) return;
    estado.plantilla = pl;
    estado.nodos.plantillas.querySelectorAll('.rp-plantilla').forEach((t) => {
      const activa = t.dataset.id === id;
      t.classList.toggle('activa', activa);
      t.setAttribute('aria-pressed', activa ? 'true' : 'false');
    });
    construirFormulario(pl, esInicial ? estado.prellenado : {});
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
    caja.appendChild(form);
  }

  async function enviar(pl, controles, form) {
    const c = C();
    const parametros = {};
    for (const param of pl.parametros || []) {
      const leer = controles[param.id];
      if (!leer) continue;
      const val = leer();
      if ((val == null || val === '') && param.tipo !== 'booleano' && param.tipo !== 'texto') {
        c.toast(`Complete el campo «${param.etiqueta}».`, 'aviso');
        return;
      }
      parametros[param.id] = val;
    }
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
        trabajos: null, temporizador: null, sondeando: false,
        nodos: {
          plantillas: pagina.querySelector('.rp-plantillas'),
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
