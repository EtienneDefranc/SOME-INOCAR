/*
 * SOME — Módulo «Comparar» (RF-14)
 *
 * Dos columnas de parámetros (A y B), precargadas con el ejemplo de la
 * especificación: SST de NOAA frente a thetao a 0 m de Copernicus en Niño 1+2,
 * últimos 12 meses. Gráfico superpuesto y tarjeta con diferencia media, RMSE,
 * correlación y las frases del motor. Con unidades distintas (p. ej. SST
 * frente a precipitación CHIRPS) solo se muestra la correlación, con dos ejes.
 */
(function () {
  'use strict';

  const SOME = window.SOME;
  if (!SOME) { console.error('SOME: app.js debe cargarse antes que comparar.js'); return; }
  const C = () => (SOME.componentes && SOME.componentes.comunes) || null;

  let estado = null;

  function estilos() {
    C().inyectarEstilos('estilos-modulo-comparar', `
      .cp-columnas { display:grid; grid-template-columns: 1fr 1fr; gap:12px; align-items:start; }
      @media (max-width: 900px) { .cp-columnas { grid-template-columns: 1fr; } }
      .cp-columna { position:relative; }
      .cp-letra { position:absolute; top:12px; right:14px; width:28px; height:28px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:700; }
      .cp-letra.a { background: var(--primario); color: var(--primario-texto); }
      .cp-letra.b { background: var(--acento); color: #3A2E00; }
      .cp-acciones { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
      .cp-acciones .espacio { flex:1; }
      .cp-grafico { width:100%; min-height:440px; }
      .cp-grafico-caja { position:relative; }
      .cp-grafico-caja .insignia-simulado, .cp-grafico-caja .insignia[data-procedencia] { position:absolute; top:10px; right:12px; z-index:2; }
      .cp-diferencia { display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap:10px; margin-bottom:10px; }
      .cp-texto ul { margin:8px 0 0; padding-left:20px; line-height:1.6; }
      .cp-nota { font-size:12px; color:var(--texto-2); }
    `);
  }

  function valoresPorDefecto() {
    const c = C();
    const periodo = c.periodoPorDefecto();
    const a = Object.assign(c.valoresPorDefecto(), {
      fuente: 'noaa_oisst', variable: 'sst', area: { tipo: 'recorte', recorte: 'nino12' }, tipoPeriodo: 'rango', periodo,
    });
    const b = Object.assign(c.valoresPorDefecto(), {
      fuente: 'copernicus_phy', variable: 'thetao', profundidad: 0, area: { tipo: 'recorte', recorte: 'nino12' }, tipoPeriodo: 'rango', periodo: Object.assign({}, periodo),
    });
    return { a, b };
  }

  function normalizar(v) {
    const c = C();
    if (v.tipoPeriodo === 'fecha') { v.periodo = { inicio: c.sumarDias(v.fecha, -89), fin: v.fecha }; }
    v.tipoPeriodo = 'rango';
    return v;
  }

  function ladoCuerpo(v) {
    const c = C();
    const cuerpo = { fuente: v.fuente, variable: v.variable, area: v.area, periodo: { inicio: v.periodo.inicio, fin: v.periodo.fin } };
    const info = c.variable(v.fuente, v.variable);
    if (info && info.tiene_profundidad) cuerpo.profundidad = Number(v.profundidad ?? 0);
    return cuerpo;
  }

  function etiqueta(v) {
    const c = C();
    const info = c.variable(v.fuente, v.variable);
    const prof = info && info.tiene_profundidad ? ` ${c.fmtNum(v.profundidad ?? 0, 0)} m` : '';
    return `${c.nombreVariable(v.fuente, v.variable)}${prof} · ${c.nombreCortoFuente(v.fuente)}`;
  }

  async function comparar() {
    const c = C();
    const a = normalizar(estado.cA.valores());
    const b = normalizar(estado.cB.valores());
    estado.a = a; estado.b = b;
    const cuerpo = { a: ladoCuerpo(a), b: ladoCuerpo(b), agregacion: estado.agregacion };
    estado.cuerpoUltimo = cuerpo;
    const zona = estado.nodos.resultado;
    const ticket = ++estado.ticket;
    c.mostrarCarga(zona, `${c.mensajeConsultando(a.fuente)} ${a.fuente !== b.fuente ? `y ${c.nombreCortoFuente(b.fuente)}…` : ''}`);
    actualizarHash();
    let r;
    try {
      r = await SOME.api.post('/api/consultas/comparar', cuerpo);
    } catch (e) {
      if (ticket !== estado.ticket) return;
      c.mostrarError(zona, e && e.message ? e.message : 'Error al comparar', () => comparar());
      return;
    }
    if (ticket !== estado.ticket || !estado) return;
    if (!r || !r.a || !r.b || !Array.isArray(r.a.valores) || !Array.isArray(r.b.valores) || (!r.a.valores.length && !r.b.valores.length)) {
      c.mostrarVacio(zona, 'Sin datos para comparar', 'Alguna de las dos consultas no devolvió valores. Revise el área y el periodo.');
      return;
    }
    estado.datos = r;
    dibujar(zona, r);
  }

  function dibujar(zona, r) {
    const c = C();
    const a = estado.a, b = estado.b;
    const etqA = r.a.etiqueta || `A: ${etiqueta(a)}`;
    const etqB = r.b.etiqueta || `B: ${etiqueta(b)}`;
    const uA = r.a.unidad || c.unidadVariable(a.fuente, a.variable);
    const uB = r.b.unidad || c.unidadVariable(b.fuente, b.variable);
    const mismasUnidades = uA === uB;
    // La alineación la decide el motor: por fecha cuando A y B pidieron el mismo periodo (aunque una
    // fuente se acorte por su fecha de corte) y por día relativo cuando los periodos difieren.
    const porFecha = r.alineacion ? r.alineacion === 'fecha'
      : (Array.isArray(r.a.fechas) && Array.isArray(r.b.fechas) && r.a.fechas[0] === r.b.fechas[0]);
    const d = r.diferencia || {};
    const frases = Array.isArray(r.texto) ? r.texto : [];
    zona.innerHTML = '';

    const caja = c.crear('<div class="tarjeta cp-grafico-caja"><div class="cp-grafico" role="img" aria-label="Series A y B superpuestas"></div></div>');
    caja.appendChild(c.insigniaSimulado(r));
    zona.appendChild(caja);

    const dec = mismasUnidades && uA === 'm' ? 3 : 2;
    const difTexto = (x) => (x == null ? '—' : (mismasUnidades ? c.fmtAnomalia(x, uA) : c.fmtNum(x, dec)));
    // Una sola convención de signo en toda la tarjeta: diferencia = A − B (como el motor)
    // Con unidades distintas (p. ej. SST frente a precipitación) el motor solo calcula la correlación.
    const soloCorrelacion = d.solo_correlacion === true || r.mismas_unidades === false || !mismasUnidades;
    const noAplica = '<span class="texto-2" style="font-size:16px" title="No se calcula con variables de unidades distintas">No aplica</span>';
    const tarjeta = c.crear(`<div class="tarjeta cp-texto"><h3 class="tarjeta-titulo">${soloCorrelacion ? 'Relación entre A y B' : 'Diferencia (A − B)'}</h3>
      <div class="cp-diferencia">
        <div class="kpi"><div class="kpi-etiqueta">Diferencia media (A − B)</div><div class="kpi-valor">${soloCorrelacion ? noAplica : c.esc(difTexto(d.media))}</div></div>
        <div class="kpi"><div class="kpi-etiqueta">RMSE</div><div class="kpi-valor">${soloCorrelacion ? noAplica : (d.rmse == null ? '—' : `${c.fmtNum(d.rmse, dec)} ${mismasUnidades ? c.esc(uA) : ''}`)}</div></div>
        <div class="kpi"><div class="kpi-etiqueta">Correlación</div><div class="kpi-valor">${d.correlacion == null ? '—' : c.fmtNum(d.correlacion, 2)}</div></div>
      </div>
      ${frases.length ? `<ul>${frases.map((f) => `<li>${c.esc(f)}</li>`).join('')}</ul>` : ''}
      ${soloCorrelacion ? `<p class="cp-nota">Las dos series tienen unidades distintas (${c.esc(uA)} y ${c.esc(uB)}): se dibujan con ejes separados y solo se calcula la correlación.</p>` : ''}
      ${!porFecha ? '<p class="cp-nota">Los periodos no coinciden: las series se alinean por día relativo al inicio de cada periodo.</p>' : ''}
      ${r.metodo ? `<p class="cp-nota"><strong>Método:</strong> ${c.esc(r.metodo)}</p>` : ''}
    </div>`);
    zona.appendChild(tarjeta);

    const credito = c.crear('<div class="tarjeta" style="padding-top:8px;padding-bottom:8px"></div>');
    const fuentes = Array.isArray(r.fuentes) && r.fuentes.length ? r.fuentes : [c.fuente(a.fuente), a.fuente !== b.fuente ? c.fuente(b.fuente) : null].filter(Boolean);
    fuentes.forEach((f) => credito.appendChild(c.piezaCredito(f, null, f && f.fecha_corte)));
    zona.appendChild(credito);

    const div = caja.querySelector('.cp-grafico');
    if (!c.plotlyDisponible()) { div.innerHTML = '<div class="vacio">No se pudo cargar la biblioteca de gráficos (Plotly).</div>'; return; }
    const col = c.coloresTema();
    const xA = porFecha ? r.a.fechas : r.a.valores.map((_, i) => i + 1);
    const xB = porFecha ? r.b.fechas : r.b.valores.map((_, i) => i + 1);
    const plantillaX = porFecha ? '%{x|%d/%m/%Y}' : 'Día %{x}';
    const trazas = [
      { type: 'scatter', mode: 'lines', name: etqA, x: xA, y: r.a.valores, line: { color: col.primario, width: 2 }, hovertemplate: `A · ${plantillaX}<br>%{y:.${dec}f} ${uA}<extra></extra>`,
        customdata: porFecha ? undefined : r.a.fechas },
      { type: 'scatter', mode: 'lines', name: etqB, x: xB, y: r.b.valores, line: { color: col.acento, width: 2 }, yaxis: mismasUnidades ? 'y' : 'y2', hovertemplate: `B · ${plantillaX}<br>%{y:.${dec}f} ${uB}<extra></extra>` },
    ];
    const layout = c.plotlyBase({
      title: { text: `Comparación · ${c.textoArea(a.area)}${JSON.stringify(a.area) !== JSON.stringify(b.area) ? ` frente a ${c.textoArea(b.area)}` : ''}`, font: { size: 15 }, x: 0, xanchor: 'left' },
      // Con segundo eje, margen derecho amplio: la barra de herramientas vertical tapaba su título
      // La leyenda (dos rótulos largos que se parten en dos líneas) crece hacia arriba desde el borde del
      // área de trazado: anclada arriba crecía hacia abajo y tapaba los máximos de las series
      margin: { l: 60, r: mismasUnidades ? 24 : 90, t: 100, b: 48 },
      legend: { orientation: 'h', x: 0, y: 1.02, yanchor: 'bottom' },
      xaxis: porFecha ? { tickformat: '%d/%m/%Y', hoverformat: '%d/%m/%Y', gridcolor: col.borde } : { title: { text: 'Día desde el inicio del periodo' }, gridcolor: col.borde },
      yaxis: { title: { text: mismasUnidades ? uA : `A (${uA})` }, gridcolor: col.borde },
      hovermode: porFecha ? 'x unified' : 'x',
    });
    if (!mismasUnidades) layout.yaxis2 = { title: { text: `B (${uB})` }, overlaying: 'y', side: 'right', showgrid: false };
    window.Plotly.newPlot(div, trazas, layout, c.plotlyConfig).then(() => c.zoomAcotado(div));
    estado.graficos.push(div);
  }

  // ---------------------------------------------------------------------------
  // Hash, vista y reporte
  // ---------------------------------------------------------------------------

  function parametrosHash() {
    const c = C();
    const a = normalizar(estado.cA.valores()), b = normalizar(estado.cB.valores());
    return Object.assign(
      c.hashDesdeValores(a, 'a_', { forzarPeriodo: true, incluirReferencia: false, incluirProfundidad: !!(c.variable(a.fuente, a.variable) || {}).tiene_profundidad }),
      c.hashDesdeValores(b, 'b_', { forzarPeriodo: true, incluirReferencia: false, incluirProfundidad: !!(c.variable(b.fuente, b.variable) || {}).tiene_profundidad }),
      { agregacion: estado.agregacion },
    );
  }

  function actualizarHash() {
    try {
      const q = Object.entries(parametrosHash()).map(([k, val]) => `${encodeURIComponent(k)}=${encodeURIComponent(val)}`).join('&');
      history.replaceState(null, '', `#/comparar?${q}`);
    } catch (e) { /* sin importancia */ }
  }

  async function guardarVista() {
    const c = C();
    const a = estado.cA.valores(), b = estado.cB.valores();
    await c.guardarVista('comparar', parametrosHash(), `${etiqueta(a)} frente a ${etiqueta(b)}`);
  }

  function generarReporte() {
    const c = C();
    const a = normalizar(estado.cA.valores()), b = normalizar(estado.cB.valores());
    if (a.fuente !== b.fuente || a.variable !== b.variable) {
      c.toast('El reporte comparativo compara dos periodos de la misma variable; se usará la variable de la columna A.', 'info');
    }
    const params = {
      plantilla: 'comparativo', fuente: a.fuente, variable: a.variable,
      periodo_a: c.serializarPeriodo(a.periodo), periodo_b: c.serializarPeriodo(b.periodo),
    };
    if (a.area.tipo === 'recorte') params.recorte = a.area.recorte;
    c.irA('reportes', params);
  }

  function redibujarPorTema() {
    if (!estado || !estado.datos) return;
    (estado.graficos || []).forEach((g) => C().purgarPlotly(g));
    estado.graficos = [];
    dibujar(estado.nodos.resultado, estado.datos);
  }

  // ---------------------------------------------------------------------------
  // Montaje
  // ---------------------------------------------------------------------------

  SOME.registrar('comparar', {
    titulo: 'Comparar',
    icono: 'comparar',
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
        } catch (e) { c.toast('No se pudo cargar la vista indicada; se usa el ejemplo por defecto.', 'error'); }
      }
      const defecto = valoresPorDefecto();
      const a = p.a_fuente ? normalizar(c.valoresDesdeHash(p, 'a_')) : defecto.a;
      let b;
      if (p.b_fuente) b = normalizar(c.valoresDesdeHash(p, 'b_'));
      else if (p.a_fuente) {
        // Solo llegó A (desde el constructor): B toma la misma área y periodo con la fuente alternativa
        b = Object.assign({}, defecto.b, { area: JSON.parse(JSON.stringify(a.area)), periodo: Object.assign({}, a.periodo) });
        if (a.fuente === 'copernicus_phy') b = Object.assign({}, defecto.a, { area: JSON.parse(JSON.stringify(a.area)), periodo: Object.assign({}, a.periodo) });
        if (a.fuente === 'chirps_v3') b = Object.assign({}, a, { periodo: { inicio: c.sumarMeses(a.periodo.inicio, -12), fin: c.sumarMeses(a.periodo.fin, -12) } });
      } else b = defecto.b;
      const agregacion = ['diaria', 'semanal', 'mensual'].includes(p.agregacion) ? p.agregacion : 'diaria';

      const pagina = c.crear(`<div class="modulo-comparar" style="display:flex;flex-direction:column;gap:12px">
        <div class="cp-columnas">
          <div class="tarjeta cp-columna"><span class="cp-letra a" aria-hidden="true">A</span><h3 class="tarjeta-titulo" style="margin-top:0">Consulta A</h3><div class="cp-constructor-a"></div></div>
          <div class="tarjeta cp-columna"><span class="cp-letra b" aria-hidden="true">B</span><h3 class="tarjeta-titulo" style="margin-top:0">Consulta B</h3><div class="cp-constructor-b"></div></div>
        </div>
        <div class="tarjeta cp-acciones barra-herramientas" style="padding:10px 14px">
          <span class="cp-vista-nombre" style="font-weight:600"></span>
          <label class="campo" style="display:flex;align-items:center;gap:8px;margin:0"><span>Agregación</span>
            <select aria-label="Agregación temporal"><option value="diaria">Diaria</option><option value="semanal">Semanal</option><option value="mensual">Mensual</option></select></label>
          <button type="button" class="boton boton-primario" data-accion="comparar">Comparar</button>
          <span class="espacio"></span>
          <button type="button" class="boton boton-secundario" data-accion="vista">Guardar vista</button>
          <button type="button" class="boton boton-secundario" data-accion="reporte">Generar reporte comparativo</button>
        </div>
        <div class="cp-resultado" style="display:flex;flex-direction:column;gap:12px"></div>
      </div>`);
      contenedor.appendChild(pagina);

      estado = {
        agregacion, ticket: 0, graficos: [], datos: null, a, b, cuerpoUltimo: null,
        nodos: { resultado: pagina.querySelector('.cp-resultado') },
      };
      if (vistaCargada) pagina.querySelector('.cp-vista-nombre').textContent = `Vista: ${vistaCargada.nombre}`;

      const opciones = { compacto: true, sinMapa: true, mostrarAcciones: false, tipoPeriodo: 'rango', mostrarReferencia: false };
      estado.cA = new SOME.componentes.ConstructorConsulta(Object.assign({ valores: a }, opciones)).montar(pagina.querySelector('.cp-constructor-a'));
      estado.cB = new SOME.componentes.ConstructorConsulta(Object.assign({ valores: b }, opciones)).montar(pagina.querySelector('.cp-constructor-b'));

      // Selector de agregación de la barra de acciones (no el primer <select> de la página,
      // que es el de «Variable» de la columna A: tomarlo dejaba ese selector vacío).
      const sel = pagina.querySelector('.cp-acciones select');
      sel.value = agregacion;
      sel.addEventListener('change', () => { estado.agregacion = sel.value; comparar(); });
      pagina.querySelector('[data-accion="comparar"]').addEventListener('click', comparar);
      pagina.querySelector('[data-accion="vista"]').addEventListener('click', guardarVista);
      pagina.querySelector('[data-accion="reporte"]').addEventListener('click', generarReporte);

      estado.alCambiarTema = () => redibujarPorTema();
      if (SOME.eventos) SOME.eventos.addEventListener('tema', estado.alCambiarTema);

      comparar();
    },

    desmontar() {
      if (!estado) return;
      estado.ticket++;
      if (SOME.eventos && estado.alCambiarTema) SOME.eventos.removeEventListener('tema', estado.alCambiarTema);
      (estado.graficos || []).forEach((g) => C().purgarPlotly(g));
      if (estado.cA) { try { estado.cA.destruir(); } catch (e) { /* nada */ } }
      if (estado.cB) { try { estado.cB.destruir(); } catch (e) { /* nada */ } }
      estado = null;
    },
  });
})();
