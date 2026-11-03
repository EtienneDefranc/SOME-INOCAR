/*
 * SOME — Módulo «Series» (RF-07, RF-08, RF-15)
 *
 * Pestañas «Serie de tiempo», «Climatología mensual» y «Mismo mes cada año».
 * Gráfico Plotly con la serie, la climatología (línea discontinua) y un subgráfico
 * de anomalías en barras coloreadas; agregación diaria/semanal/mensual; tarjeta
 * «Resumen en lenguaje sencillo» con las frases del motor; tabla de estadísticas;
 * exportar CSV/PNG, guardar vista y generar reporte.
 */
(function () {
  'use strict';

  const SOME = window.SOME;
  if (!SOME) { console.error('SOME: app.js debe cargarse antes que series.js'); return; }
  const C = () => (SOME.componentes && SOME.componentes.comunes) || null;
  // Leyenda horizontal que crece hacia arriba desde el borde del área de trazado: con rótulos largos (p. ej.,
  // «Climatología 1981-2010 (1982-2010 en enero–agosto)») se parte en dos líneas sin tapar las series
  const LEYENDA_ARRIBA = { orientation: 'h', x: 0, y: 1.02, yanchor: 'bottom' };

  const PESTANAS = [
    { id: 'serie', texto: 'Serie de tiempo' },
    { id: 'climatologia', texto: 'Climatología mensual' },
    { id: 'mismo_mes', texto: 'Mismo mes cada año' },
  ];

  let estado = null;

  function estilos() {
    C().inyectarEstilos('estilos-modulo-series', `
      .sr-resultado { display:flex; flex-direction:column; gap:12px; min-width:0; }
      .sr-grafico { width:100%; min-height:420px; }
      .sr-grafico-caja { position:relative; }
      .sr-grafico-caja .insignia-simulado, .sr-grafico-caja .insignia[data-procedencia] { position:absolute; top:10px; right:12px; z-index:2; }
      .sr-resumen ul { margin:8px 0 0; padding-left:20px; line-height:1.6; }
      .sr-resumen li { margin-bottom:4px; }
      .sr-barra-herramientas { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
      .sr-barra-herramientas .espacio { flex:1; }
      .sr-mes { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:10px; }
      .sr-tabla td.num, .sr-tabla th.num { text-align:right; font-family:var(--mono, monospace); }
      /* La tarjeta de estadísticas ocupa media columna: con menos margen lateral (y, bajo 1366 px, el valor a
         12 px) el valor más largo («27/10/2025 – 26/10/2026», que no se parte) cabe también a 1280×720 */
      .sr-tabla th, .sr-tabla td { padding-left:8px; padding-right:8px; }
      @media (max-width: 1365px) { .sr-tabla td.num { font-size:12px; } }
    `);
  }

  // ---------------------------------------------------------------------------
  // Colores de las anomalías (± según la variable)
  // ---------------------------------------------------------------------------

  function coloresAnomalia(anomalias, fuenteId) {
    const c = C().coloresTema();
    const esLluvia = fuenteId === 'chirps_v3';
    const positivo = esLluvia ? c.exito : c.peligro;
    const negativo = esLluvia ? '#A0652D' : c.primario;
    return (anomalias || []).map((a) => (a == null ? 'rgba(0,0,0,0)' : (a >= 0 ? positivo : negativo)));
  }

  function etiquetaMes(i) {
    const m = C().MESES_CORTOS[i] || '';
    return m.charAt(0).toUpperCase() + m.slice(1);
  }

  /** «la temperatura superficial del mar», «el nivel del mar», «la precipitación». */
  function conArticulo(nombre) {
    const n = String(nombre || '').replace(/\s*\(.*\)\s*$/, '').trim();
    const primera = n.split(/\s+/)[0].toLowerCase();
    const femeninas = ['temperatura', 'salinidad', 'velocidad', 'rapidez', 'altura', 'profundidad', 'precipitación', 'anomalía', 'corriente', 'corrientes'];
    const articulo = femeninas.includes(primera) || /a$|ad$/.test(primera) ? 'la' : 'el';
    return `${articulo} ${n.charAt(0).toLowerCase()}${n.slice(1)}`;
  }

  /** «en la región Niño 1+2», «en el golfo de Guayaquil», «en Guayas», «en el rectángulo …». */
  function enArea(area) {
    const c = C();
    const texto = c.textoArea(area);
    if (!area) return `en ${texto}`;
    let articulo = 'el';
    if (area.tipo === 'recorte') {
      const r = c.recorte(area.recorte);
      const tipo = r ? r.tipo : '';
      if (tipo === 'provincia') return `en ${texto}`;
      articulo = (tipo === 'region_nino' || tipo === 'cuenca') ? 'la' : 'el';
    }
    return `en ${articulo} ${texto.charAt(0).toLowerCase()}${texto.slice(1)}`;
  }

  /** Rango del eje Y ajustado a los datos (con margen) para que el ciclo anual no quede aplanado. */
  function rangoY(valores, unidad) {
    const v = (valores || []).filter((x) => x != null && Number.isFinite(x));
    if (!v.length) return undefined;
    const min = Math.min(...v), max = Math.max(...v);
    const margen = Math.max((max - min) * 0.25, 0.2);
    let inferior = min - margen;
    if (unidad === 'mm/día' && min >= 0) inferior = Math.max(inferior, 0);
    return [inferior, max + margen];
  }

  // ---------------------------------------------------------------------------
  // Pestaña «Serie de tiempo»
  // ---------------------------------------------------------------------------

  function cuerpoSerie() {
    return C().cuerpoConsulta(estado.valores, { forzarPeriodo: true, agregacion: true });
  }

  async function cargarSerie(contenido) {
    const c = C();
    const cuerpo = cuerpoSerie();
    estado.cuerpoUltimo = cuerpo;
    const ticket = ++estado.ticket;
    c.mostrarCarga(contenido, c.mensajeConsultando(cuerpo.fuente));
    let r;
    try {
      r = await SOME.api.post('/api/consultas/serie', cuerpo);
    } catch (e) {
      if (ticket !== estado.ticket) return;
      c.mostrarError(contenido, e && e.message ? e.message : 'Error al consultar la serie', () => cargarSerie(contenido));
      return;
    }
    if (ticket !== estado.ticket || !estado) return;
    if (!r || !Array.isArray(r.fechas) || !r.fechas.length) {
      c.mostrarVacio(contenido, 'Sin datos para esta consulta', 'Pruebe con otro periodo, otra área o una variable distinta.');
      return;
    }
    estado.datos.serie = r;
    dibujarSerie(contenido, r);
  }

  function dibujarSerie(contenido, r) {
    const c = C();
    const v = estado.valores;
    const unidad = r.unidad || c.unidadVariable(v.fuente, v.variable);
    const nombre = r.nombre_variable || c.nombreVariable(v.fuente, v.variable);
    contenido.innerHTML = '';

    const cajaGrafico = c.crear(`<div class="tarjeta sr-grafico-caja"><div class="sr-grafico" role="img" aria-label="Serie de tiempo de ${c.esc(nombre)}"></div></div>`);
    cajaGrafico.appendChild(c.insigniaSimulado(r));
    contenido.appendChild(cajaGrafico);

    const resumen = r.resumen || {};
    const frases = Array.isArray(resumen.texto) ? resumen.texto : [];
    const tarjetas = c.crear(`<div class="cuadricula col-2" style="align-items:start">
      <div class="tarjeta sr-resumen"><h3 class="tarjeta-titulo">Resumen en lenguaje sencillo</h3>
        ${frases.length ? `<ul>${frases.map((f) => `<li>${c.esc(f)}</li>`).join('')}</ul>` : '<p class="texto-2">El motor no devolvió un resumen para esta consulta.</p>'}
        ${r.metodo ? `<p class="texto-2" style="font-size:12px;margin-top:10px"><strong>Método:</strong> ${c.esc(r.metodo)}</p>` : ''}
      </div>
      <div class="tarjeta"><h3 class="tarjeta-titulo">Estadísticas</h3><div class="sr-estadisticas"></div></div>
    </div>`);
    contenido.appendChild(tarjetas);
    tarjetas.querySelector('.sr-estadisticas').appendChild(tablaEstadisticas(r, unidad));

    const credito = c.crear('<div class="tarjeta" style="padding-top:8px;padding-bottom:8px"></div>');
    credito.appendChild(c.piezaCredito(r.fuente || c.fuente(v.fuente), null, r.fecha_corte));
    contenido.appendChild(credito);

    graficoSerie(cajaGrafico.querySelector('.sr-grafico'), r, unidad, nombre);
  }

  function graficoSerie(div, r, unidad, nombre) {
    const c = C();
    if (!c.plotlyDisponible()) { div.innerHTML = '<div class="vacio">No se pudo cargar la biblioteca de gráficos (Plotly).</div>'; return; }
    const col = c.coloresTema();
    const v = estado.valores;
    const tieneClim = Array.isArray(r.climatologia) && r.climatologia.some((x) => x != null);
    const tieneAnom = Array.isArray(r.anomalias) && r.anomalias.some((x) => x != null);
    const dec = unidad === 'm' ? 3 : 2;
    const trazas = [{
      type: 'scatter', mode: 'lines', name: nombre, x: r.fechas, y: r.valores,
      line: { color: col.primario, width: 2 }, connectgaps: false,
      hovertemplate: `%{y:.${dec}f} ${unidad}<extra>${nombre}</extra>`,
    }];
    if (tieneClim) {
      trazas.push({
        type: 'scatter', mode: 'lines', name: `Climatología ${c.textoReferencia(r.periodo_referencia || v.periodo_referencia, r.periodo_referencia_efectivo, { corto: true })}`.trim(), x: r.fechas, y: r.climatologia,
        line: { color: col.texto2, width: 1.5, dash: 'dash' },
        hovertemplate: `%{y:.${dec}f} ${unidad}<extra>Climatología</extra>`,
      });
    }
    if (tieneAnom) {
      trazas.push({
        type: 'bar', name: 'Anomalía', x: r.fechas, y: r.anomalias, yaxis: 'y2',
        marker: { color: coloresAnomalia(r.anomalias, v.fuente), line: { width: 0 } },
        // Plotly 2.35 no admite «+» en el formato de hovertemplate (deja el número crudo, sin signo):
        // el texto con signo y punto decimal se prepara aquí, con c.fmtAnomalia.
        customdata: r.anomalias.map((a) => c.fmtAnomalia(a, unidad, dec)),
        hovertemplate: '%{customdata}<extra>Anomalía</extra>',
      });
    }
    const layout = c.plotlyBase({
      title: { text: `${nombre} · ${c.textoArea(v.area)}`, font: { size: 15 }, x: 0, xanchor: 'left' },
      margin: { l: 60, r: 24, t: 90, b: 48 },
      legend: LEYENDA_ARRIBA,
      xaxis: { tickformat: '%d/%m/%Y', hoverformat: '%d/%m/%Y', gridcolor: col.borde, linecolor: col.borde },
      yaxis: { title: { text: unidad }, domain: tieneAnom ? [0.36, 1] : [0, 1], gridcolor: col.borde, zerolinecolor: col.borde },
      bargap: 0,
    });
    if (tieneAnom) {
      layout.yaxis2 = { title: { text: `Anomalía (${unidad})` }, domain: [0, 0.26], anchor: 'x', gridcolor: col.borde, zerolinecolor: col.texto2, zerolinewidth: 1 };
    }
    // Días preliminares (NOAA OISST o CHIRPS: «periodo_preliminar» = «aaaa-mm-dd/aaaa-mm-dd»): franja sombreada
    // con su rótulo; la fuente los reemplaza por la versión final y el valor puede cambiar ligeramente
    const prelim = String(r.dias_preliminares ? (r.periodo_preliminar || '') : '').split('/');
    if (prelim.length === 2 && c.esIso(prelim[0]) && c.esIso(prelim[1])) {
      const x0 = prelim[0] < r.fechas[0] ? r.fechas[0] : prelim[0];
      const x1 = prelim[1] > r.fechas[r.fechas.length - 1] ? r.fechas[r.fechas.length - 1] : prelim[1];
      if (x0 <= x1) {
        layout.shapes = [{ type: 'rect', xref: 'x', yref: 'paper', x0, x1, y0: 0, y1: 1, layer: 'below', line: { width: 0 }, fillcolor: col.texto2, opacity: 0.12 }];
        layout.annotations = [{
          xref: 'x', yref: 'paper', x: x1, xanchor: 'right', y: tieneAnom ? 0.36 : 0, yanchor: 'bottom', showarrow: false,
          text: `Preliminar (${r.dias_preliminares} ${r.dias_preliminares === 1 ? 'día' : 'días'})`, font: { size: 10.5, color: col.texto2 },
          bgcolor: col.superficie, borderpad: 2,  // opaco: con opacity 0.9 el rótulo bajaba a 4.18:1 en modo claro
          hovertext: `Del ${c.fmtFecha(prelim[0])} al ${c.fmtFecha(prelim[1])}: datos preliminares, que la fuente reemplazará por la versión final.`,
        }];
      }
    }
    window.Plotly.newPlot(div, trazas, layout, c.plotlyConfig).then(() => c.zoomAcotado(div));
    estado.graficos.push(div);
  }

  function tablaEstadisticas(r, unidad) {
    const c = C();
    const s = r.resumen || {};
    const dec = unidad === 'm' ? 3 : 2;
    const filas = [];
    const n = Array.isArray(r.valores) ? r.valores.filter((x) => x != null).length : 0;
    filas.push(['Periodo', `${c.fmtFecha(r.fechas[0])} – ${c.fmtFecha(r.fechas[r.fechas.length - 1])}`]);
    filas.push(['Datos válidos', `${c.fmtNum(n, 0)} (${{ diaria: 'diarios', semanal: 'semanales', mensual: 'mensuales' }[estado.valores.agregacion] || estado.valores.agregacion})`]);
    if (s.media != null) filas.push(['Media', `${c.fmtNum(s.media, dec)} ${unidad}`]);
    if (s.minimo && s.minimo.valor != null) filas.push(['Mínimo', `${c.fmtNum(s.minimo.valor, dec)} ${unidad} · ${c.fmtFecha(s.minimo.fecha)}`]);
    if (s.maximo && s.maximo.valor != null) filas.push(['Máximo', `${c.fmtNum(s.maximo.valor, dec)} ${unidad} · ${c.fmtFecha(s.maximo.fecha)}`]);
    if (s.tendencia_por_anio != null) filas.push(['Tendencia', `${c.fmtAnomalia(s.tendencia_por_anio, unidad)} por año`]);
    if (s.ultimo && s.ultimo.valor != null) {
      filas.push(['Último dato', `${c.fmtNum(s.ultimo.valor, dec)} ${unidad} · ${c.fmtFecha(s.ultimo.fecha)}`]);
      if (s.ultimo.anomalia != null) filas.push(['Anomalía del último dato', c.fmtAnomalia(s.ultimo.anomalia, unidad)]);
    }
    if (Array.isArray(r.anomalias)) {
      const an = r.anomalias.filter((x) => x != null);
      if (an.length) {
        const pos = an.filter((x) => x > 0).length;
        filas.push(['Días/periodos sobre lo normal', `${c.fmtNum(pos, 0)} de ${c.fmtNum(an.length, 0)} (${c.fmtNum((100 * pos) / an.length, 0)} %)`]);
      }
    }
    // Periodo efectivo: el de la climatología que realmente usó la fuente (p. ej., GLORYS12 1993-2016 en Copernicus).
    // «se pidió …» va en el rótulo (que se ajusta) y no en el valor (que no se parte), para no desbordar la tarjeta.
    // NOAA OISST 1981-2010 declara un efectivo parcial («1982-2010 en enero–agosto»): el valor es el pedido y la
    // excepción va en el rótulo.
    const refPedida = r.periodo_referencia || estado.valores.periodo_referencia;
    const refEfectiva = r.periodo_referencia_efectivo;
    const refParcial = refEfectiva && refPedida && refEfectiva !== refPedida && / en /.test(refEfectiva);
    if (refParcial) filas.push([`Periodo de referencia (${refEfectiva})`, refPedida]);
    else if (refPedida || refEfectiva) filas.push([refEfectiva && refPedida && refEfectiva !== refPedida ? `Periodo de referencia (se pidió ${refPedida})` : 'Periodo de referencia', refEfectiva || refPedida]);
    const tabla = c.crear(`<table class="tabla sr-tabla"><tbody>${filas.map(([k, val]) => `<tr><th scope="row" style="text-align:left">${c.esc(k)}</th><td class="num">${c.esc(val)}</td></tr>`).join('')}</tbody></table>`);
    return tabla;
  }

  // ---------------------------------------------------------------------------
  // Pestaña «Climatología mensual»
  // ---------------------------------------------------------------------------

  async function cargarClimatologia(contenido) {
    const c = C();
    const v = estado.valores;
    const cuerpo = c.cuerpoConsulta(v, { conPeriodo: false });
    cuerpo.periodo_referencia = v.periodo_referencia || '1991-2020';
    const ticket = ++estado.ticket;
    c.mostrarCarga(contenido, `Calculando la climatología ${cuerpo.periodo_referencia}…`);
    let r;
    try {
      r = await SOME.api.post('/api/consultas/climatologia', cuerpo);
    } catch (e) {
      if (ticket !== estado.ticket) return;
      c.mostrarError(contenido, e && e.message ? e.message : 'Error al consultar la climatología', () => cargarClimatologia(contenido));
      return;
    }
    if (ticket !== estado.ticket || !estado) return;
    if (!r || !Array.isArray(r.valores) || !r.valores.length) {
      c.mostrarVacio(contenido, 'Sin climatología para esta consulta', 'El recorte no tiene celdas válidas para la variable elegida.');
      return;
    }
    estado.datos.climatologia = r;
    dibujarClimatologia(contenido, r);
  }

  function dibujarClimatologia(contenido, r) {
    const c = C();
    const v = estado.valores;
    const unidad = r.unidad || c.unidadVariable(v.fuente, v.variable);
    const nombre = c.nombreVariable(v.fuente, v.variable);
    const meses = (r.meses || c.MESES_CORTOS).map((m) => m.charAt(0).toUpperCase() + m.slice(1));
    const dec = unidad === 'm' ? 3 : 2;
    contenido.innerHTML = '';
    const caja = c.crear(`<div class="tarjeta sr-grafico-caja"><div class="sr-grafico" role="img" aria-label="Climatología mensual de ${c.esc(nombre)}"></div></div>`);
    caja.appendChild(c.insigniaSimulado(r));
    contenido.appendChild(caja);

    const valores = r.valores;
    const maxI = valores.indexOf(Math.max(...valores.filter((x) => x != null)));
    const minI = valores.indexOf(Math.min(...valores.filter((x) => x != null)));
    const media = valores.filter((x) => x != null).reduce((a, b) => a + b, 0) / Math.max(1, valores.filter((x) => x != null).length);
    const tarjetas = c.crear(`<div class="cuadricula col-2" style="align-items:start">
      <div class="tarjeta sr-resumen"><h3 class="tarjeta-titulo">Lectura rápida</h3><ul>
        <li>En el periodo de referencia ${c.esc(c.textoReferencia(r.periodo_referencia || v.periodo_referencia, r.periodo_referencia_efectivo))}, ${c.esc(conArticulo(nombre))} ${c.esc(enArea(v.area))} promedia ${c.fmtNum(media, dec)} ${c.esc(unidad)} al año.</li>
        <li>El mes más alto es ${c.esc(c.MESES_LARGOS[maxI] || '')} (${c.fmtNum(valores[maxI], dec)} ${c.esc(unidad)}) y el más bajo, ${c.esc(c.MESES_LARGOS[minI] || '')} (${c.fmtNum(valores[minI], dec)} ${c.esc(unidad)}).</li>
        <li>La amplitud del ciclo anual es de ${c.fmtNum(valores[maxI] - valores[minI], dec)} ${c.esc(unidad)}.</li>
      </ul>${r.metodo ? `<p class="texto-2" style="font-size:12px;margin-top:10px"><strong>Método:</strong> ${c.esc(r.metodo)}</p>` : ''}</div>
      <div class="tarjeta"><h3 class="tarjeta-titulo">Valores mensuales</h3>
        <table class="tabla sr-tabla"><thead><tr><th>Mes</th><th class="num">${c.esc(unidad)}</th></tr></thead><tbody>
        ${meses.map((m, i) => `<tr><td>${c.esc(m)}</td><td class="num">${c.fmtNum(valores[i], dec)}</td></tr>`).join('')}</tbody></table></div>
    </div>`);
    contenido.appendChild(tarjetas);
    const credito = c.crear('<div class="tarjeta" style="padding-top:8px;padding-bottom:8px"></div>');
    credito.appendChild(c.piezaCredito(r.fuente || c.fuente(v.fuente), null, r.fecha_corte));
    contenido.appendChild(credito);

    if (!c.plotlyDisponible()) { caja.querySelector('.sr-grafico').innerHTML = '<div class="vacio">No se pudo cargar Plotly.</div>'; return; }
    const col = c.coloresTema();
    const trazas = [
      { type: 'bar', name: `Climatología ${c.textoReferencia(r.periodo_referencia || v.periodo_referencia, r.periodo_referencia_efectivo, { conPedido: false })}`.trim(), x: meses, y: valores, marker: { color: col.primario, opacity: 0.85 }, hovertemplate: `%{y:.${dec}f} ${unidad}<extra></extra>` },
      { type: 'scatter', mode: 'lines+markers', name: 'Ciclo anual', x: meses, y: valores, line: { color: col.acento, width: 2 }, marker: { size: 7 }, hoverinfo: 'skip' },
    ];
    // El eje Y se acota al rango de los datos: así se aprecia el ciclo anual (p. ej., 3 °C de amplitud)
    const layout = c.plotlyBase({
      title: { text: `Climatología mensual · ${nombre} · ${c.textoArea(v.area)}`, font: { size: 15 }, x: 0, xanchor: 'left' },
      margin: { l: 60, r: 24, t: 90, b: 48 },
      legend: LEYENDA_ARRIBA,
      yaxis: { title: { text: unidad }, gridcolor: col.borde, range: rangoY(valores, unidad) },
      xaxis: { gridcolor: col.borde },
      hovermode: 'x',
    });
    const div = caja.querySelector('.sr-grafico');
    window.Plotly.newPlot(div, trazas, layout, c.plotlyConfig).then(() => c.zoomAcotado(div));
    estado.graficos.push(div);
  }

  // ---------------------------------------------------------------------------
  // Pestaña «Mismo mes cada año»
  // ---------------------------------------------------------------------------

  async function cargarMismoMes(contenido) {
    const c = C();
    const v = estado.valores;
    const selector = c.crear(`<div class="tarjeta sr-mes"><label for="sr-sel-mes" style="font-weight:600">Mes</label><select id="sr-sel-mes" aria-label="Mes a comparar"></select>
      <span class="texto-2" style="font-size:12px">Se compara el promedio del mes elegido en cada año disponible con la climatología del mes.</span></div>`);
    const sel = selector.querySelector('select');
    c.MESES_LARGOS.forEach((m, i) => { const o = new Option(m.charAt(0).toUpperCase() + m.slice(1), String(i + 1)); if (i + 1 === estado.mes) o.selected = true; sel.appendChild(o); });
    sel.addEventListener('change', () => { estado.mes = Number(sel.value); cargarMismoMes(contenido); });
    contenido.innerHTML = '';
    contenido.appendChild(selector);
    const zona = c.crear('<div></div>');
    contenido.appendChild(zona);

    const cuerpo = c.cuerpoConsulta(v, { conPeriodo: false });
    cuerpo.mes = estado.mes;
    const ticket = ++estado.ticket;
    c.mostrarCarga(zona, c.mensajeConsultando(cuerpo.fuente));
    let r;
    try {
      r = await SOME.api.post('/api/consultas/mismo-mes', cuerpo);
    } catch (e) {
      if (ticket !== estado.ticket) return;
      c.mostrarError(zona, e && e.message ? e.message : 'Error al consultar «Mismo mes cada año»', () => cargarMismoMes(contenido));
      return;
    }
    if (ticket !== estado.ticket || !estado) return;
    if (!r || !Array.isArray(r.anios) || !r.anios.length) {
      c.mostrarVacio(zona, 'Sin datos para este mes', 'Ningún año del periodo disponible tiene datos para el mes elegido.');
      return;
    }
    estado.datos.mismo_mes = r;
    dibujarMismoMes(zona, r);
  }

  function dibujarMismoMes(zona, r) {
    const c = C();
    const v = estado.valores;
    const unidad = r.unidad || c.unidadVariable(v.fuente, v.variable);
    const nombre = c.nombreVariable(v.fuente, v.variable);
    const nombreMes = c.MESES_LARGOS[estado.mes - 1] || '';
    const dec = unidad === 'm' ? 3 : 2;
    zona.innerHTML = '';
    const caja = c.crear(`<div class="tarjeta sr-grafico-caja"><div class="sr-grafico" role="img" aria-label="${c.esc(nombre)} en ${c.esc(nombreMes)} de cada año"></div></div>`);
    caja.appendChild(c.insigniaSimulado(r));
    zona.appendChild(caja);

    const frases = Array.isArray(r.texto) ? r.texto : [];
    const tarjetas = c.crear(`<div class="cuadricula col-2" style="align-items:start">
      <div class="tarjeta sr-resumen"><h3 class="tarjeta-titulo">Resumen en lenguaje sencillo</h3>
        ${frases.length ? `<ul>${frases.map((f) => `<li>${c.esc(f)}</li>`).join('')}</ul>` : '<p class="texto-2">Sin resumen disponible.</p>'}
        ${r.metodo ? `<p class="texto-2" style="font-size:12px;margin-top:10px"><strong>Método:</strong> ${c.esc(r.metodo)}</p>` : ''}</div>
      <div class="tarjeta"><h3 class="tarjeta-titulo">${c.esc(nombreMes.charAt(0).toUpperCase() + nombreMes.slice(1))} de cada año</h3>
        <table class="tabla sr-tabla"><thead><tr><th>Año</th><th class="num">Valor (${c.esc(unidad)})</th><th class="num">Anomalía</th></tr></thead><tbody>
        ${r.anios.map((a, i) => `<tr><td>${c.esc(a)}</td><td class="num">${c.fmtNum(r.valores[i], dec)}</td><td class="num ${r.anomalias && r.anomalias[i] > 0 ? 'positivo' : 'negativo'}">${r.anomalias ? c.fmtAnomalia(r.anomalias[i], unidad) : '—'}</td></tr>`).join('')}
        <tr><th scope="row" style="text-align:left">Climatología del mes</th><td class="num">${c.fmtNum(r.climatologia_mes, dec)}</td><td></td></tr>
        </tbody></table></div>
    </div>`);
    zona.appendChild(tarjetas);
    const credito = c.crear('<div class="tarjeta" style="padding-top:8px;padding-bottom:8px"></div>');
    credito.appendChild(c.piezaCredito(r.fuente || c.fuente(v.fuente), null, r.fecha_corte));
    zona.appendChild(credito);

    if (!c.plotlyDisponible()) { caja.querySelector('.sr-grafico').innerHTML = '<div class="vacio">No se pudo cargar Plotly.</div>'; return; }
    const col = c.coloresTema();
    const anios = r.anios.map(String);
    const trazas = [{
      type: 'bar', name: `${nombre} en ${nombreMes}`, x: anios, y: r.valores,
      marker: { color: r.anomalias ? coloresAnomalia(r.anomalias, v.fuente) : col.primario },
      text: r.anomalias ? r.anomalias.map((a) => c.fmtAnomalia(a, unidad)) : undefined, cliponaxis: false,
      // Anomalía negativa: el rótulo va dentro de la barra, junto a su extremo; fuera quedaba tachado por la
      // línea de la climatología, que pasa justo encima de las barras por debajo de lo normal
      textposition: r.anomalias ? r.anomalias.map((a) => (a != null && a < 0 ? 'inside' : 'outside')) : 'outside',
      insidetextanchor: 'end',
      // Dentro de la barra, el color del texto de los botones primarios (blanco en claro, oscuro en oscuro) sobre
      // --primario (≥ 7:1); sobre la barra marrón de la lluvia, blanco. El automático de Plotly (#444) daba 4.2:1
      insidetextfont: { color: v.fuente === 'chirps_v3' ? '#FFFFFF'
        : ((getComputedStyle(document.documentElement).getPropertyValue('--primario-texto') || '').trim() || '#FFFFFF') },
      // El año va en la etiqueta principal: en <extra> Plotly lo pinta del color de la barra y no siempre contrasta
      hovertemplate: `%{x}: %{y:.${dec}f} ${unidad}<extra></extra>`,
    }];
    // La climatología del mes va como una serie en la leyenda (no como anotación sobre las barras)
    if (r.climatologia_mes != null) {
      trazas.push({
        type: 'scatter', mode: 'lines', x: anios, y: anios.map(() => r.climatologia_mes),
        name: `Climatología ${c.textoReferencia(r.periodo_referencia || v.periodo_referencia, r.periodo_referencia_efectivo, { conPedido: false, mes: estado.mes })}: ${c.fmtNum(r.climatologia_mes, dec)} ${unidad}`.replace(/\s+/g, ' '),
        line: { color: col.texto2, width: 1.5, dash: 'dash' }, hoverinfo: 'skip',
      });
    }
    const layout = c.plotlyBase({
      title: { text: `${nombre} · ${nombreMes} de cada año · ${c.textoArea(v.area)}`, font: { size: 15 }, x: 0, xanchor: 'left' },
      margin: { l: 60, r: 24, t: 90, b: 48 },
      legend: LEYENDA_ARRIBA,
      xaxis: { type: 'category', gridcolor: col.borde },
      // Eje Y acotado a los datos (incluida la climatología) con margen para las etiquetas de anomalía
      yaxis: { title: { text: unidad }, gridcolor: col.borde, range: rangoY([].concat(r.valores, r.climatologia_mes != null ? [r.climatologia_mes] : []), unidad) },
      hovermode: 'x',
      showlegend: true,
    });
    const div = caja.querySelector('.sr-grafico');
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
    const contenido = estado.nodos.contenido;
    if (estado.pestana === 'climatologia') cargarClimatologia(contenido);
    else if (estado.pestana === 'mismo_mes') cargarMismoMes(contenido);
    else cargarSerie(contenido);
    // La exportación (/api/exportar, clase «serie») solo cubre la serie de tiempo; en las otras
    // pestañas los botones se deshabilitan con una explicación en vez de exportar otra cosa.
    const exportable = estado.pestana === 'serie';
    (estado.nodos.botonesExportar || []).forEach((b) => {
      b.disabled = !exportable;
      b.title = exportable ? '' : 'Disponible en la pestaña «Serie de tiempo»';
    });
    actualizarHash();
  }

  function parametrosHash() {
    const c = C();
    const h = c.hashDesdeValores(estado.valores, '', { forzarPeriodo: true, incluirAgregacion: true });
    h.pestana = estado.pestana;
    if (estado.pestana === 'mismo_mes') h.mes = String(estado.mes);
    return h;
  }

  function actualizarHash() {
    try {
      const q = Object.entries(parametrosHash()).map(([k, val]) => `${encodeURIComponent(k)}=${encodeURIComponent(val)}`).join('&');
      history.replaceState(null, '', `#/series?${q}`);
    } catch (e) { /* sin importancia */ }
  }

  function exportarSerie(formato) {
    const c = C();
    const cuerpo = cuerpoSerie();
    c.exportar(formato, 'serie', cuerpo, c.nombreArchivo(estado.valores, formato, 'serie'));
  }

  async function guardarVista() {
    const c = C();
    const v = estado.valores;
    const prefijo = { serie: 'Serie de', climatologia: 'Climatología de', mismo_mes: `${(c.MESES_LARGOS[estado.mes - 1] || '').replace(/^./, (x) => x.toUpperCase())} de cada año —` }[estado.pestana];
    await c.guardarVista('series', parametrosHash(), `${prefijo} ${c.nombreVariable(v.fuente, v.variable)} — ${c.textoArea(v.area)}`);
  }

  function generarReporte() {
    const c = C();
    const v = estado.valores;
    const params = { plantilla: 'informe_variable', fuente: v.fuente, variable: v.variable, periodo: c.serializarPeriodo(v.periodo), referencia: v.periodo_referencia };
    if (v.area.tipo === 'recorte') params.recorte = v.area.recorte;
    else c.toast('El reporte usa recortes del catálogo; elija el recorte en el formulario.', 'info');
    const info = c.variable(v.fuente, v.variable);
    if (info && info.tiene_profundidad) params.profundidad = String(v.profundidad ?? 0);
    c.irA('reportes', params);
  }

  function redibujarPorTema() {
    if (!estado) return;
    const d = estado.datos[estado.pestana];
    if (!d) return;
    purgarGraficos();
    if (estado.pestana === 'serie') dibujarSerie(estado.nodos.contenido, d);
    else if (estado.pestana === 'climatologia') dibujarClimatologia(estado.nodos.contenido, d);
    else cargarMismoMes(estado.nodos.contenido);
  }

  // ---------------------------------------------------------------------------
  // Montaje
  // ---------------------------------------------------------------------------

  SOME.registrar('series', {
    titulo: 'Series',
    icono: 'series',
    grupo: 'Analizar',

    async montar(contenedor, params) {
      const c = C();
      if (!c) { contenedor.innerHTML = '<div class="alerta alerta-peligro">Falta consultas.js (componentes comunes).</div>'; return; }
      estilos();
      await c.asegurarCatalogo();
      contenedor.innerHTML = '';

      let valores = c.valoresDesdeHash(params);
      let p = params || {};
      let vistaCargada = null;
      if (p.vista) {
        try {
          vistaCargada = await c.cargarVista(p.vista);
          if (vistaCargada) { valores = c.valoresDesdeHash(vistaCargada.parametros); p = Object.assign({}, vistaCargada.parametros, { vista: p.vista }); }
        } catch (e) { c.toast('No se pudo cargar la vista indicada; se usan los parámetros por defecto.', 'error'); }
      }
      if (valores.tipoPeriodo === 'fecha') {
        valores.periodo = { inicio: c.sumarDias(valores.fecha, -89), fin: valores.fecha };
        valores.tipoPeriodo = 'rango';
      }
      const pestana = PESTANAS.find((x) => x.id === p.pestana) ? p.pestana : 'serie';
      const mesParam = Number(p.mes);
      const mes = mesParam >= 1 && mesParam <= 12 ? mesParam : Number(String(valores.periodo.fin).slice(5, 7));

      const pagina = c.crear(`<div class="panel-lateral modulo-series">
        <aside class="tarjeta parametros" aria-label="Parámetros de la serie">
          <h3 class="tarjeta-titulo" style="margin-top:0">Parámetros</h3>
          <div class="sr-constructor"></div>
        </aside>
        <section class="resultado sr-resultado">
          <div class="barra-herramientas sr-barra-herramientas tarjeta" style="padding:10px 14px">
            <span class="sr-vista-nombre" style="font-weight:600"></span>
            <span class="espacio"></span>
            <button type="button" class="boton boton-secundario" data-accion="csv">Exportar CSV</button>
            <button type="button" class="boton boton-secundario" data-accion="png">Exportar PNG</button>
            <button type="button" class="boton boton-secundario" data-accion="vista">Guardar vista</button>
            <button type="button" class="boton boton-primario" data-accion="reporte">Generar reporte</button>
          </div>
          <div class="pestanas" role="tablist">
            ${PESTANAS.map((t) => `<button type="button" class="pestana" role="tab" data-id="${t.id}" aria-selected="false">${t.texto}</button>`).join('')}
          </div>
          <div class="sr-contenido" role="tabpanel" style="display:flex;flex-direction:column;gap:12px"></div>
        </section>
      </div>`);
      contenedor.appendChild(pagina);

      estado = {
        valores, pestana, mes, ticket: 0, graficos: [], datos: {}, cuerpoUltimo: null, constructor: null,
        nodos: {
          pestanas: pagina.querySelector('.pestanas'), contenido: pagina.querySelector('.sr-contenido'), nombreVista: pagina.querySelector('.sr-vista-nombre'),
          botonesExportar: [pagina.querySelector('[data-accion="csv"]'), pagina.querySelector('[data-accion="png"]')],
        },
      };
      if (vistaCargada) estado.nodos.nombreVista.textContent = `Vista: ${vistaCargada.nombre}`;

      estado.nodos.pestanas.querySelectorAll('.pestana').forEach((b) => b.addEventListener('click', () => {
        if (estado.pestana === b.dataset.id) return;
        estado.pestana = b.dataset.id;
        cargarPestana();
      }));
      pagina.querySelector('[data-accion="csv"]').addEventListener('click', () => exportarSerie('csv'));
      pagina.querySelector('[data-accion="png"]').addEventListener('click', () => exportarSerie('png'));
      pagina.querySelector('[data-accion="vista"]').addEventListener('click', guardarVista);
      pagina.querySelector('[data-accion="reporte"]').addEventListener('click', generarReporte);

      estado.constructor = new SOME.componentes.ConstructorConsulta({
        compacto: true, valores, tipoPeriodo: 'rango', mostrarAgregacion: true, mostrarAcciones: false,
        textoEnviar: 'Consultar',
        ayuda: 'La serie mostrará el promedio de área (o el valor del punto) con su climatología y sus anomalías; las pestañas «Climatología mensual» y «Mismo mes cada año» usan la misma área y variable.',
        alEnviar: (v) => { estado.valores = v; estado.datos = {}; cargarPestana(); },
        alCambiar: (v) => {
          // La agregación y el periodo de referencia se aplican al instante
          if (estado.valores && v.fuente === estado.valores.fuente && v.variable === estado.valores.variable
            && JSON.stringify(v.area) === JSON.stringify(estado.valores.area) && JSON.stringify(v.periodo) === JSON.stringify(estado.valores.periodo)
            && v.profundidad === estado.valores.profundidad
            && (v.agregacion !== estado.valores.agregacion || v.periodo_referencia !== estado.valores.periodo_referencia)) {
            estado.valores = v; estado.datos = {}; cargarPestana();
          }
        },
      });
      estado.constructor.montar(pagina.querySelector('.sr-constructor'));

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
