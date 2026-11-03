/*
 * SOME — Módulo «Perfiles» (RF-13)
 *
 * Perfil vertical de temperatura o salinidad de Copernicus en un punto y una fecha
 * (eje Y invertido, marca de la capa de mezcla) y cortina profundidad-tiempo
 * (heatmap Plotly con el eje Y invertido) para un periodo.
 */
(function () {
  'use strict';

  const SOME = window.SOME;
  if (!SOME) { console.error('SOME: app.js debe cargarse antes que perfiles.js'); return; }
  const C = () => (SOME.componentes && SOME.componentes.comunes) || null;

  const PESTANAS = [
    { id: 'perfil', texto: 'Perfil vertical' },
    { id: 'cortina', texto: 'Cortina profundidad-tiempo' },
  ];
  const ESCALA_COLOR = { thetao: 'Viridis', so: 'Cividis' };

  let estado = null;

  function estilos() {
    C().inyectarEstilos('estilos-modulo-perfiles', `
      .pf-resultado { display:flex; flex-direction:column; gap:12px; min-width:0; }
      .pf-grafico { width:100%; min-height:460px; }
      .pf-grafico-caja { position:relative; }
      .pf-grafico-caja .insignia-simulado, .pf-grafico-caja .insignia[data-procedencia] { position:absolute; top:10px; right:12px; z-index:2; }
      .pf-barra-herramientas { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
      .pf-barra-herramientas .espacio { flex:1; }
      .pf-tabla td.num, .pf-tabla th.num { text-align:right; font-family:var(--mono, monospace); }
      .pf-texto ul { margin:8px 0 0; padding-left:20px; line-height:1.6; }
    `);
  }

  function punto() {
    const c = C();
    const a = estado.valores.area;
    return a && a.tipo === 'punto' ? { lat: a.lat, lon: a.lon } : c.centroArea(a);
  }

  function fechaPerfil() {
    const v = estado.valores;
    return v.tipoPeriodo === 'fecha' ? v.fecha : (v.periodo && v.periodo.fin) || C().fechaCorte();
  }

  function periodoCortina() {
    const c = C();
    const v = estado.valores;
    if (v.tipoPeriodo !== 'fecha' && v.periodo) return v.periodo;
    return { inicio: c.sumarDias(v.fecha, -89), fin: v.fecha };
  }

  // ---------------------------------------------------------------------------
  // Perfil vertical
  // ---------------------------------------------------------------------------

  function cuerpoPerfil() {
    const p = punto();
    return { fuente: 'copernicus_phy', variable: estado.valores.variable, lat: p.lat, lon: p.lon, fecha: fechaPerfil() };
  }

  async function cargarPerfil(contenido) {
    const c = C();
    const cuerpo = cuerpoPerfil();
    const ticket = ++estado.ticket;
    c.mostrarCarga(contenido, c.mensajeConsultando(cuerpo.fuente));
    let r;
    try {
      r = await SOME.api.post('/api/consultas/perfil', cuerpo);
    } catch (e) {
      if (ticket !== estado.ticket) return;
      c.mostrarError(contenido, e && e.message ? e.message : 'Error al consultar el perfil', () => cargarPerfil(contenido));
      return;
    }
    if (ticket !== estado.ticket || !estado) return;
    if (!r || !Array.isArray(r.profundidades) || !r.profundidades.length || !Array.isArray(r.valores) || r.valores.every((x) => x == null)) {
      c.mostrarVacio(contenido, 'Sin datos en este punto', 'El punto elegido está en tierra o fuera del área de estudio. Pruebe con otro punto en el mar.');
      return;
    }
    estado.datos.perfil = r;
    dibujarPerfil(contenido, r);
  }

  function dibujarPerfil(contenido, r) {
    const c = C();
    const v = estado.valores;
    const p = punto();
    const unidad = r.unidad || c.unidadVariable('copernicus_phy', v.variable);
    const nombre = c.nombreVariable('copernicus_phy', v.variable);
    const dec = v.variable === 'so' ? 2 : 2;
    contenido.innerHTML = '';
    const caja = c.crear(`<div class="tarjeta pf-grafico-caja"><div class="pf-grafico" role="img" aria-label="Perfil vertical de ${c.esc(nombre)}"></div></div>`);
    caja.appendChild(c.insigniaSimulado(r));
    contenido.appendChild(caja);

    const frases = Array.isArray(r.texto) ? r.texto : [];
    const superficie = r.valores[0], fondo = r.valores[r.valores.length - 1];
    const tarjetas = c.crear(`<div class="cuadricula col-2" style="align-items:start">
      <div class="tarjeta pf-texto"><h3 class="tarjeta-titulo">Resumen en lenguaje sencillo</h3>
        <ul>${frases.length ? frases.map((f) => `<li>${c.esc(f)}</li>`).join('') : `
          <li>En ${c.esc(c.fmtCoord(p.lat, true))}, ${c.esc(c.fmtCoord(p.lon, false))}, el ${c.esc(c.fmtFecha(r.fecha || fechaPerfil()))}, ${c.esc(nombre.toLowerCase())} va de ${c.fmtNum(superficie, dec)} ${c.esc(unidad)} en superficie a ${c.fmtNum(fondo, dec)} ${c.esc(unidad)} a ${c.fmtNum(r.profundidades[r.profundidades.length - 1], 0)} m.</li>
          ${r.capa_mezcla_m != null ? `<li>La capa de mezcla llega hasta unos ${c.fmtNum(r.capa_mezcla_m, 0)} m.</li>` : ''}`}</ul>
        ${r.metodo ? `<p class="texto-2" style="font-size:12px;margin-top:10px"><strong>Método:</strong> ${c.esc(r.metodo)}</p>` : ''}
      </div>
      <div class="tarjeta"><h3 class="tarjeta-titulo">Valores por profundidad</h3>
        <table class="tabla pf-tabla"><thead><tr><th>Profundidad (m)</th><th class="num">${c.esc(nombre)} (${c.esc(unidad)})</th></tr></thead><tbody>
        ${r.profundidades.map((z, i) => `<tr><td>${c.fmtNum(z, 0)}</td><td class="num">${c.fmtNum(r.valores[i], dec)}</td></tr>`).join('')}</tbody></table>
        ${r.capa_mezcla_m != null ? `<p class="texto-2" style="font-size:12px;margin-top:8px">Capa de mezcla: ${c.fmtNum(r.capa_mezcla_m, 0)} m</p>` : ''}
      </div>
    </div>`);
    contenido.appendChild(tarjetas);
    const credito = c.crear('<div class="tarjeta" style="padding-top:8px;padding-bottom:8px"></div>');
    credito.appendChild(c.piezaCredito(r.fuente || c.fuente('copernicus_phy'), null, r.fecha_corte));
    contenido.appendChild(credito);

    const div = caja.querySelector('.pf-grafico');
    if (!c.plotlyDisponible()) { div.innerHTML = '<div class="vacio">No se pudo cargar la biblioteca de gráficos (Plotly).</div>'; return; }
    const col = c.coloresTema();
    const trazas = [{
      type: 'scatter', mode: 'lines+markers', name: nombre, x: r.valores, y: r.profundidades,
      line: { color: col.primario, width: 2.5 }, marker: { size: 8, color: col.primario },
      hovertemplate: `%{x:.${dec}f} ${unidad} a %{y} m<extra></extra>`,
    }];
    const formas = [], anotaciones = [];
    if (r.capa_mezcla_m != null) {
      formas.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: r.capa_mezcla_m, y1: r.capa_mezcla_m, line: { color: col.acento, width: 2, dash: 'dot' } });
      formas.push({ type: 'rect', xref: 'paper', x0: 0, x1: 1, y0: 0, y1: r.capa_mezcla_m, fillcolor: col.acento, opacity: 0.08, line: { width: 0 } });
      anotaciones.push({ xref: 'paper', x: 1, y: r.capa_mezcla_m, xanchor: 'right', yanchor: 'bottom', showarrow: false, text: `Capa de mezcla ≈ ${c.fmtNum(r.capa_mezcla_m, 0)} m`, font: { size: 11, color: col.acentoTexto } });
    }
    // Fecha efectiva del dato (el motor acota a la fecha de corte de Copernicus), no la pedida
    const fechaDato = r.fecha && c.esIso(r.fecha) ? r.fecha : fechaPerfil();
    const layout = c.plotlyBase({
      // En dos líneas: en una sola, la insignia de procedencia (arriba a la derecha) tapaba la fecha
      title: { text: `Perfil de ${nombre.toLowerCase()}<br>${c.fmtCoord(p.lat, true)}, ${c.fmtCoord(p.lon, false)} · ${c.fmtFecha(fechaDato)}`, font: { size: 15 }, x: 0, xanchor: 'left', y: 0.955, yanchor: 'top' },
      // Margen superior amplio: el título del eje X va arriba y no debe pisar el título del gráfico
      margin: { l: 70, r: 24, t: 110, b: 56 },
      xaxis: { title: { text: `${nombre} (${unidad})`, standoff: 8 }, gridcolor: col.borde, side: 'top' },
      yaxis: { title: { text: 'Profundidad (m)' }, autorange: 'reversed', gridcolor: col.borde, rangemode: 'tozero' },
      hovermode: 'closest', showlegend: false, shapes: formas, annotations: anotaciones,
    });
    window.Plotly.newPlot(div, trazas, layout, c.plotlyConfig).then(() => c.zoomAcotado(div));
    estado.graficos.push(div);
  }

  // ---------------------------------------------------------------------------
  // Cortina profundidad-tiempo
  // ---------------------------------------------------------------------------

  function cuerpoCortina() {
    const p = punto();
    const per = periodoCortina();
    return { fuente: 'copernicus_phy', variable: estado.valores.variable, lat: p.lat, lon: p.lon, periodo: { inicio: per.inicio, fin: per.fin } };
  }

  async function cargarCortina(contenido) {
    const c = C();
    const cuerpo = cuerpoCortina();
    const ticket = ++estado.ticket;
    c.mostrarCarga(contenido, `${c.mensajeConsultando(cuerpo.fuente)} (${c.diasEntre(cuerpo.periodo.inicio, cuerpo.periodo.fin) + 1} días × ${(c.catalogo().profundidades || []).length || 8} niveles)`);
    let r;
    try {
      r = await SOME.api.post('/api/consultas/cortina', cuerpo);
    } catch (e) {
      if (ticket !== estado.ticket) return;
      c.mostrarError(contenido, e && e.message ? e.message : 'Error al consultar la cortina', () => cargarCortina(contenido));
      return;
    }
    if (ticket !== estado.ticket || !estado) return;
    if (!r || !Array.isArray(r.fechas) || !r.fechas.length || !Array.isArray(r.valores) || !r.valores.length) {
      c.mostrarVacio(contenido, 'Sin datos en este punto', 'El punto elegido está en tierra o fuera del área de estudio. Pruebe con otro punto en el mar.');
      return;
    }
    estado.datos.cortina = r;
    dibujarCortina(contenido, r);
  }

  function dibujarCortina(contenido, r) {
    const c = C();
    const v = estado.valores;
    const p = punto();
    const unidad = r.unidad || c.unidadVariable('copernicus_phy', v.variable);
    const nombre = c.nombreVariable('copernicus_phy', v.variable);
    const per = periodoCortina();
    contenido.innerHTML = '';
    const caja = c.crear(`<div class="tarjeta pf-grafico-caja"><div class="pf-grafico" role="img" aria-label="Cortina profundidad-tiempo de ${c.esc(nombre)}"></div></div>`);
    caja.appendChild(c.insigniaSimulado(r));
    contenido.appendChild(caja);

    // Lectura rápida: superficie frente al nivel más profundo
    const sup = (r.valores[0] || []).filter((x) => x != null);
    const prof = (r.valores[r.valores.length - 1] || []).filter((x) => x != null);
    const media = (arr) => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null);
    const texto = c.crear(`<div class="tarjeta pf-texto"><h3 class="tarjeta-titulo">Lectura rápida</h3><ul>
      <li>Cortina de ${c.esc(nombre.toLowerCase())} en ${c.esc(c.fmtCoord(p.lat, true))}, ${c.esc(c.fmtCoord(p.lon, false))}, del ${c.esc(c.fmtFecha(per.inicio))} al ${c.esc(c.fmtFecha(per.fin))} (${r.fechas.length} días, ${r.profundidades.length} niveles hasta ${c.fmtNum(r.profundidades[r.profundidades.length - 1], 0)} m).</li>
      ${media(sup) != null ? `<li>En superficie el promedio del periodo es ${c.fmtNum(media(sup), 2)} ${c.esc(unidad)}; a ${c.fmtNum(r.profundidades[r.profundidades.length - 1], 0)} m, ${c.fmtNum(media(prof), 2)} ${c.esc(unidad)}.</li>` : ''}
      <li>Cada columna es un día; cada fila, un nivel de profundidad. Pase el cursor para leer el valor.</li>
    </ul>${r.metodo ? `<p class="texto-2" style="font-size:12px;margin-top:10px"><strong>Método:</strong> ${c.esc(r.metodo)}</p>` : ''}</div>`);
    contenido.appendChild(texto);
    const credito = c.crear('<div class="tarjeta" style="padding-top:8px;padding-bottom:8px"></div>');
    credito.appendChild(c.piezaCredito(r.fuente || c.fuente('copernicus_phy'), null, r.fecha_corte));
    contenido.appendChild(credito);

    const div = caja.querySelector('.pf-grafico');
    if (!c.plotlyDisponible()) { div.innerHTML = '<div class="vacio">No se pudo cargar la biblioteca de gráficos (Plotly).</div>'; return; }
    const col = c.coloresTema();
    const trazas = [{
      type: 'heatmap', x: r.fechas, y: r.profundidades, z: r.valores,
      colorscale: ESCALA_COLOR[v.variable] || 'Viridis', zsmooth: false, connectgaps: false,
      colorbar: { title: { text: unidad, side: 'right' }, thickness: 14, len: 0.9 },
      hovertemplate: `%{x|%d/%m/%Y} · %{y} m<br>%{z:.2f} ${unidad}<extra></extra>`,
    }];
    const layout = c.plotlyBase({
      title: { text: `Cortina de ${nombre.toLowerCase()} · ${c.fmtCoord(p.lat, true)}, ${c.fmtCoord(p.lon, false)}`, font: { size: 15 }, x: 0, xanchor: 'left' },
      margin: { l: 70, r: 24, t: 70, b: 56 },
      xaxis: { title: { text: 'Fecha' }, tickformat: '%d/%m/%Y', hoverformat: '%d/%m/%Y', gridcolor: col.borde },
      yaxis: { title: { text: 'Profundidad (m)' }, autorange: 'reversed', gridcolor: col.borde },
      hovermode: 'closest',
    });
    window.Plotly.newPlot(div, trazas, layout, c.plotlyConfig).then(() => c.zoomAcotado(div));
    estado.graficos.push(div);
  }

  // ---------------------------------------------------------------------------
  // Pestañas y acciones
  // ---------------------------------------------------------------------------

  function purgarGraficos() {
    const c = C();
    (estado.graficos || []).forEach((g) => c.purgarPlotly(g));
    estado.graficos = [];
  }

  function cargarPestana() {
    purgarGraficos();
    estado.nodos.pestanas.querySelectorAll('.pestana').forEach((p) => {
      const activa = p.dataset.id === estado.pestana;
      p.classList.toggle('activa', activa);
      p.setAttribute('aria-selected', activa ? 'true' : 'false');
    });
    if (estado.pestana === 'cortina') cargarCortina(estado.nodos.contenido);
    else cargarPerfil(estado.nodos.contenido);
    actualizarHash();
  }

  function parametrosHash() {
    const c = C();
    const p = punto();
    const per = periodoCortina();
    return {
      fuente: 'copernicus_phy', variable: estado.valores.variable,
      area: c.serializarArea({ tipo: 'punto', lat: p.lat, lon: p.lon }),
      fecha: fechaPerfil(), periodo: c.serializarPeriodo(per),
      pestana: estado.pestana,
    };
  }

  function actualizarHash() {
    try {
      const q = Object.entries(parametrosHash()).map(([k, val]) => `${encodeURIComponent(k)}=${encodeURIComponent(val)}`).join('&');
      history.replaceState(null, '', `#/perfiles?${q}`);
    } catch (e) { /* sin importancia */ }
  }

  function exportar(formato) {
    const c = C();
    const esCortina = estado.pestana === 'cortina';
    const cuerpo = esCortina ? cuerpoCortina() : cuerpoPerfil();
    const fecha = (esCortina ? cuerpo.periodo.fin : cuerpo.fecha).replace(/-/g, '');
    c.exportar(formato, esCortina ? 'cortina' : 'perfil', cuerpo, `SOME_copernicus_phy_${estado.valores.variable}_${esCortina ? 'cortina' : 'perfil'}_${fecha}.${formato}`);
  }

  async function guardarVista() {
    const c = C();
    const p = punto();
    const nombre = `${estado.pestana === 'cortina' ? 'Cortina' : 'Perfil'} de ${c.nombreVariable('copernicus_phy', estado.valores.variable).toLowerCase()} — ${c.fmtCoord(p.lat, true)}, ${c.fmtCoord(p.lon, false)}`;
    await c.guardarVista('perfiles', parametrosHash(), nombre);
  }

  function redibujarPorTema() {
    if (!estado) return;
    const d = estado.datos[estado.pestana];
    if (!d) return;
    purgarGraficos();
    if (estado.pestana === 'cortina') dibujarCortina(estado.nodos.contenido, d);
    else dibujarPerfil(estado.nodos.contenido, d);
  }

  // ---------------------------------------------------------------------------
  // Montaje
  // ---------------------------------------------------------------------------

  SOME.registrar('perfiles', {
    titulo: 'Perfiles',
    icono: 'perfiles',
    grupo: 'Analizar',

    async montar(contenedor, params) {
      const c = C();
      if (!c) { contenedor.innerHTML = '<div class="alerta alerta-peligro">Falta consultas.js (componentes comunes).</div>'; return; }
      estilos();
      await c.asegurarCatalogo();
      contenedor.innerHTML = '';

      let p = Object.assign({}, params || {});
      let vistaCargada = null;
      if (p.vista) {
        try {
          vistaCargada = await c.cargarVista(p.vista);
          if (vistaCargada) p = Object.assign({}, vistaCargada.parametros, { vista: p.vista });
        } catch (e) { c.toast('No se pudo cargar la vista indicada; se usan los parámetros por defecto.', 'error'); }
      }
      const valores = c.valoresDesdeHash(p);
      valores.fuente = 'copernicus_phy';
      if (!['thetao', 'so'].includes(valores.variable)) valores.variable = 'thetao';
      if (!valores.area || valores.area.tipo !== 'punto') valores.area = p.area ? Object.assign({ tipo: 'punto' }, c.centroArea(valores.area)) : { tipo: 'punto', lat: -2, lon: -85 };
      // Si llegaron ambas cosas, el perfil usa la fecha y la cortina el periodo
      if (p.fecha && c.esIso(p.fecha) && !p.periodo) valores.tipoPeriodo = 'fecha';
      const pestana = PESTANAS.find((x) => x.id === p.pestana) ? p.pestana : 'perfil';
      if (pestana === 'cortina' && p.periodo) valores.tipoPeriodo = 'rango';
      if (pestana === 'perfil' && p.fecha && c.esIso(p.fecha)) valores.tipoPeriodo = 'fecha';

      const pagina = c.crear(`<div class="panel-lateral modulo-perfiles">
        <aside class="tarjeta parametros" aria-label="Parámetros del perfil">
          <h3 class="tarjeta-titulo" style="margin-top:0">Parámetros</h3>
          <p class="texto-2" style="font-size:12px;margin:0 0 10px">Los perfiles usan la temperatura y la salinidad por profundidad de Copernicus Marine (0 a 100 m). El perfil vertical corresponde a una fecha; la cortina, a un periodo.</p>
          <div class="pf-constructor"></div>
        </aside>
        <section class="resultado pf-resultado">
          <div class="barra-herramientas pf-barra-herramientas tarjeta" style="padding:10px 14px">
            <span class="pf-vista-nombre" style="font-weight:600"></span>
            <span class="espacio"></span>
            <button type="button" class="boton boton-secundario" data-accion="csv">Exportar CSV</button>
            <button type="button" class="boton boton-secundario" data-accion="png">Exportar PNG</button>
            <button type="button" class="boton boton-primario" data-accion="vista">Guardar vista</button>
          </div>
          <div class="pestanas" role="tablist">
            ${PESTANAS.map((t) => `<button type="button" class="pestana" role="tab" data-id="${t.id}" aria-selected="false">${t.texto}</button>`).join('')}
          </div>
          <div class="pf-contenido" role="tabpanel" style="display:flex;flex-direction:column;gap:12px"></div>
        </section>
      </div>`);
      contenedor.appendChild(pagina);

      estado = {
        valores, pestana, ticket: 0, graficos: [], datos: {}, constructor: null,
        nodos: { pestanas: pagina.querySelector('.pestanas'), contenido: pagina.querySelector('.pf-contenido') },
      };
      if (vistaCargada) pagina.querySelector('.pf-vista-nombre').textContent = `Vista: ${vistaCargada.nombre}`;

      estado.nodos.pestanas.querySelectorAll('.pestana').forEach((b) => b.addEventListener('click', () => {
        if (estado.pestana === b.dataset.id) return;
        estado.pestana = b.dataset.id;
        cargarPestana();
      }));
      pagina.querySelector('[data-accion="csv"]').addEventListener('click', () => exportar('csv'));
      pagina.querySelector('[data-accion="png"]').addEventListener('click', () => exportar('png'));
      pagina.querySelector('[data-accion="vista"]').addEventListener('click', guardarVista);

      estado.constructor = new SOME.componentes.ConstructorConsulta({
        compacto: true, valores, soloPunto: true, fuentes: ['copernicus_phy'],
        variablesPermitidas: (x) => ['thetao', 'so'].includes(x.id),
        tipoPeriodo: 'ambos', sinProfundidad: true, mostrarReferencia: false, mostrarAcciones: false,
        textoEnviar: 'Consultar',
        ayuda: {
          rango: 'El perfil vertical corresponde a la última fecha del periodo; la cortina, a todo el periodo.',
          fecha: 'El perfil corresponde a la fecha indicada; la cortina, a los 90 días anteriores.',
        },
        alEnviar: (v) => { estado.valores = v; estado.datos = {}; cargarPestana(); },
      });
      estado.constructor.montar(pagina.querySelector('.pf-constructor'));

      estado.alCambiarTema = () => redibujarPorTema();
      if (SOME.eventos) SOME.eventos.addEventListener('tema', estado.alCambiarTema);

      cargarPestana();
    },

    desmontar() {
      if (!estado) return;
      estado.ticket++;
      if (SOME.eventos && estado.alCambiarTema) SOME.eventos.removeEventListener('tema', estado.alCambiarTema);
      purgarGraficos();
      if (estado.constructor) { try { estado.constructor.destruir(); } catch (e) { /* nada */ } }
      estado = null;
    },
  });
})();
