/* ============================================================
   SOME — módulo Mis vistas (#/vistas)
   Vistas privadas y compartidas (RF-16). Expone además
   SOME.componentes.GuardarVista(modulo, parametros) para el botón
   «Guardar vista» de Mapas, Series, Perfiles y Comparar.
   ============================================================ */
(function () {
  'use strict';
  const SOME = window.SOME;
  const util = window.util;
  const api = window.api;

  const NOMBRE_MODULO = { mapa: 'Mapas', series: 'Series', perfiles: 'Perfiles', comparar: 'Comparar' };
  const ICONO_MODULO = { mapa: 'mapa', series: 'series', perfiles: 'perfiles', comparar: 'comparar' };

  let estado = null;

  function esPropietario(v) {
    const u = SOME.estado.usuario;
    if (!u) return false;
    if (v.propietario && v.propietario.usuario) return v.propietario.usuario === u.usuario;
    return !!v.propia;
  }

  function aplanar(p) {
    const salida = {};
    if (!p || typeof p !== 'object') return salida;
    Object.keys(p).forEach((k) => { if (p[k] !== undefined && p[k] !== null) salida[k] = p[k]; });
    return salida;
  }

  function abrirVista(v) {
    const modulo = NOMBRE_MODULO[v.modulo] ? v.modulo : 'mapa';
    SOME.navegar(modulo, Object.assign({ vista: v.id }, aplanar(v.parametros)));
  }

  /* ---------- Modal reutilizable «Guardar vista» ---------- */
  /**
   * Abre el modal para guardar una vista. Devuelve la vista creada (o null si se canceló).
   * @param {'mapa'|'series'|'perfiles'|'comparar'} modulo
   * @param {object} parametros
   * @param {{nombre?:string, descripcion?:string}} sugerido
   */
  function GuardarVista(modulo, parametros, sugerido) {
    sugerido = sugerido || {};
    return new Promise((resolver) => {
      let resuelto = false;
      const form = util.el(`
        <form novalidate>
          <div class="campo"><label for="vista-nombre">Nombre</label><input id="vista-nombre" type="text" maxlength="80" required value="${util.escapar(sugerido.nombre || '')}" placeholder="p. ej. Anomalía SST Niño 1+2 — últimos 12 meses"></div>
          <div class="campo"><label for="vista-descripcion">Descripción</label><textarea id="vista-descripcion" maxlength="300" placeholder="¿Para qué sirve esta vista?">${util.escapar(sugerido.descripcion || '')}</textarea></div>
          <label class="conmutador"><input id="vista-compartida" type="checkbox"><span class="pista"></span><span>Compartir con los demás usuarios (solo lectura)</span></label>
          <p class="texto-pequeno texto-2 mt-12 mb-0">Módulo: <strong>${util.escapar(NOMBRE_MODULO[modulo] || modulo)}</strong>. Las vistas son privadas por defecto.</p>
        </form>`);
      const m = util.modal({
        titulo: 'Guardar vista',
        contenido: form,
        alCerrar: () => { if (!resuelto) { resuelto = true; resolver(null); } },
        acciones: [
          { texto: 'Cancelar', clase: 'boton-secundario' },
          {
            texto: 'Guardar', clase: 'boton-primario',
            accion: async () => {
              const nombre = form.querySelector('#vista-nombre').value.trim();
              if (!nombre) { util.toast('Escriba un nombre para la vista.', 'aviso'); form.querySelector('#vista-nombre').focus(); return false; }
              const cuerpo = {
                nombre,
                descripcion: form.querySelector('#vista-descripcion').value.trim(),
                modulo,
                parametros: parametros || {},
                compartida: form.querySelector('#vista-compartida').checked,
              };
              const creada = await api.post('/vistas', cuerpo);
              util.toast(`Vista «${nombre}» guardada.`, 'exito');
              resuelto = true;
              resolver(creada || cuerpo);
              return true;
            },
          },
        ],
      });
      form.addEventListener('submit', (ev) => { ev.preventDefault(); m.elemento.querySelector('.modal-acciones .boton-primario').click(); });
    });
  }
  SOME.componentes.GuardarVista = GuardarVista;

  /* ---------- Página ---------- */
  async function montar(contenedor) {
    estado = { contenedor, propias: [], compartidas: [] };
    contenedor.innerHTML = `
      <div class="encabezado-pagina">
        <div>
          <h2>Mis vistas</h2>
          <p>Configuraciones guardadas de mapas, series, perfiles y comparaciones. Las compartidas son de solo lectura para los demás.</p>
        </div>
        <div class="fila">
          <button type="button" class="boton" id="vistas-actualizar">${SOME.icono('actualizar')} Actualizar</button>
        </div>
      </div>
      <div class="pestanas" role="tablist">
        <button type="button" class="pestana activa" role="tab" data-pestana="propias">Mis vistas <span class="insignia" id="n-propias">0</span></button>
        <button type="button" class="pestana" role="tab" data-pestana="compartidas">Compartidas conmigo <span class="insignia" id="n-compartidas">0</span></button>
      </div>
      <div id="vistas-lista"></div>`;

    contenedor.querySelector('#vistas-actualizar').addEventListener('click', cargar);
    contenedor.querySelectorAll('.pestana').forEach((p) => p.addEventListener('click', () => {
      contenedor.querySelectorAll('.pestana').forEach((x) => x.classList.toggle('activa', x === p));
      estado.pestana = p.dataset.pestana;
      render();
    }));
    estado.pestana = 'propias';
    await cargar();
  }

  function desmontar() { estado = null; }

  async function cargar() {
    if (!estado) return;
    const zona = estado.contenedor.querySelector('#vistas-lista');
    util.cargando(zona, 'Cargando las vistas…');
    try {
      const r = await api.get('/vistas');
      if (!estado) return;
      estado.propias = (r && Array.isArray(r.propias)) ? r.propias : (Array.isArray(r) ? r.filter(esPropietario) : []);
      estado.compartidas = (r && Array.isArray(r.compartidas)) ? r.compartidas : (Array.isArray(r) ? r.filter((v) => !esPropietario(v)) : []);
      estado.contenedor.querySelector('#n-propias').textContent = estado.propias.length;
      estado.contenedor.querySelector('#n-compartidas').textContent = estado.compartidas.length;
      render();
    } catch (e) {
      if (!estado || e.codigo === 401) return;
      zona.innerHTML = util.errorHtml(e.message, 'vistas');
      zona.querySelector('[data-reintentar]').addEventListener('click', cargar);
    }
  }

  function render() {
    const zona = estado.contenedor.querySelector('#vistas-lista');
    const propias = estado.pestana === 'propias';
    const items = propias ? estado.propias : estado.compartidas;
    zona.innerHTML = '';
    if (!items.length) {
      zona.innerHTML = propias
        ? util.vacioHtml('Aún no tiene vistas guardadas', 'Use el botón «Guardar vista» en Mapas, Series, Perfiles o Comparar para conservar una configuración y volver a ella con un clic.')
        : util.vacioHtml('Nadie ha compartido vistas con usted', 'Cuando otro usuario comparta una vista aparecerá aquí en modo de solo lectura.');
      return;
    }
    const cuadricula = util.el('<div class="cuadricula col-3"></div>');
    items.forEach((v) => cuadricula.appendChild(tarjetaVista(v, propias)));
    zona.appendChild(cuadricula);
  }

  function resumenParametros(p) {
    if (!p || typeof p !== 'object') return '';
    const partes = [];
    if (p.fuente) partes.push(SOME.nombreFuente(p.fuente));
    if (p.variable) partes.push(SOME.nombreVariable(p.fuente, p.variable));
    if (p.recorte) partes.push(SOME.nombreRecorte(p.recorte));
    if (p.area && typeof p.area === 'object') {
      if (p.area.tipo === 'recorte') partes.push(SOME.nombreRecorte(p.area.recorte));
      else if (p.area.tipo === 'punto') partes.push(`Punto ${util.fmtNum(p.area.lat, 2)}°, ${util.fmtNum(p.area.lon, 2)}°`);
      else if (p.area.tipo === 'bbox') partes.push('Rectángulo');
    }
    if (p.periodo && p.periodo.inicio) partes.push(`${util.fmtFecha(p.periodo.inicio)} – ${util.fmtFecha(p.periodo.fin)}`);
    else if (p.fecha) partes.push(util.fmtFecha(p.fecha));
    if (p.modo === 'anomalia') partes.push('anomalía');
    return partes.join(' · ');
  }

  function tarjetaVista(v, propia) {
    const t = util.el(`
      <article class="tarjeta" style="display:flex;flex-direction:column;gap:8px">
        <div class="fila fila-entre fila-arriba">
          <div class="fila" style="gap:8px;min-width:0;flex:1;flex-wrap:nowrap">
            ${SOME.icono(ICONO_MODULO[v.modulo] || 'vistas')}
            <strong class="recortar" style="flex:1;min-width:0" title="${util.escapar(v.nombre || '')}">${util.escapar(v.nombre || 'Vista sin nombre')}</strong>
          </div>
          <div class="fila" style="gap:4px;flex-wrap:nowrap">
            <span class="insignia insignia-info">${util.escapar(NOMBRE_MODULO[v.modulo] || v.modulo || '')}</span>
            ${v.compartida ? `<span class="insignia insignia-exito">${SOME.icono('compartir')} Compartida</span>` : ''}
          </div>
        </div>
        <p class="texto-2 mb-0" style="min-height:20px">${util.escapar(v.descripcion || 'Sin descripción.')}</p>
        <p class="texto-pequeno texto-2 mb-0 recortar" title="${util.escapar(resumenParametros(v.parametros))}">${util.escapar(resumenParametros(v.parametros))}</p>
        <div class="tarjeta-pie fila fila-entre" style="margin-top:auto">
          <span>${v.propietario && v.propietario.nombre ? util.escapar(v.propietario.nombre) + ' · ' : ''}${util.fmtFecha(v.actualizada || v.creada)}</span>
          <div class="fila" style="gap:4px;flex-wrap:nowrap">
            <button type="button" class="boton boton-primario boton-pequeno" data-accion="abrir">Abrir</button>
            ${propia ? `
            <div class="desplegable">
              <button type="button" class="boton boton-icono boton-pequeno" aria-label="Más acciones" aria-haspopup="menu">${SOME.icono('puntos')}</button>
              <div class="desplegable-menu" role="menu">
                <button type="button" role="menuitem" data-accion="compartir">${SOME.icono('compartir')} ${v.compartida ? 'Dejar de compartir' : 'Compartir'}</button>
                <button type="button" role="menuitem" data-accion="renombrar">${SOME.icono('editar')} Renombrar</button>
                <hr>
                <button type="button" role="menuitem" class="peligro" data-accion="eliminar">${SOME.icono('eliminar')} Eliminar</button>
              </div>
            </div>` : ''}
          </div>
        </div>
      </article>`);

    const desplegable = t.querySelector('.desplegable');
    if (desplegable) {
      desplegable.querySelector('button').addEventListener('click', (ev) => {
        ev.stopPropagation();
        document.querySelectorAll('.desplegable.abierto').forEach((d) => { if (d !== desplegable) d.classList.remove('abierto'); });
        desplegable.classList.toggle('abierto');
      });
    }
    t.querySelectorAll('[data-accion]').forEach((b) => b.addEventListener('click', () => {
      if (desplegable) desplegable.classList.remove('abierto');
      const a = b.dataset.accion;
      if (a === 'abrir') abrirVista(v);
      else if (a === 'compartir') alternarCompartir(v);
      else if (a === 'renombrar') renombrar(v);
      else if (a === 'eliminar') eliminar(v);
    }));
    return t;
  }

  async function alternarCompartir(v) {
    try {
      await api.put(`/vistas/${encodeURIComponent(v.id)}`, { nombre: v.nombre, descripcion: v.descripcion || '', modulo: v.modulo, parametros: v.parametros || {}, compartida: !v.compartida });
      util.toast(!v.compartida ? `La vista «${v.nombre}» ahora es compartida.` : `La vista «${v.nombre}» ya no se comparte.`, 'exito');
      await cargar();
    } catch (e) { util.toast(e.message, 'error'); }
  }

  function renombrar(v) {
    const form = util.el(`
      <form novalidate>
        <div class="campo"><label for="ren-nombre">Nombre</label><input id="ren-nombre" type="text" maxlength="80" value="${util.escapar(v.nombre || '')}"></div>
        <div class="campo"><label for="ren-desc">Descripción</label><textarea id="ren-desc" maxlength="300">${util.escapar(v.descripcion || '')}</textarea></div>
      </form>`);
    const m = util.modal({
      titulo: 'Renombrar vista',
      contenido: form,
      acciones: [
        { texto: 'Cancelar', clase: 'boton-secundario' },
        {
          texto: 'Guardar', clase: 'boton-primario',
          accion: async () => {
            const nombre = form.querySelector('#ren-nombre').value.trim();
            if (!nombre) { util.toast('El nombre no puede quedar vacío.', 'aviso'); return false; }
            await api.put(`/vistas/${encodeURIComponent(v.id)}`, { nombre, descripcion: form.querySelector('#ren-desc').value.trim(), modulo: v.modulo, parametros: v.parametros || {}, compartida: !!v.compartida });
            util.toast('Vista actualizada.', 'exito');
            cargar();
            return true;
          },
        },
      ],
    });
    form.addEventListener('submit', (ev) => { ev.preventDefault(); m.elemento.querySelector('.modal-acciones .boton-primario').click(); });
  }

  async function eliminar(v) {
    const ok = await util.confirmar(`¿Eliminar la vista «${v.nombre}»? Esta acción no se puede deshacer.`, { aceptar: 'Eliminar', peligro: true });
    if (!ok) return;
    try {
      await api.del(`/vistas/${encodeURIComponent(v.id)}`);
      util.toast('Vista eliminada.', 'exito');
      await cargar();
    } catch (e) { util.toast(e.message, 'error'); }
  }

  SOME.registrar('vistas', {
    titulo: 'Mis vistas',
    icono: 'vistas',
    grupo: 'Productos',
    montar,
    desmontar,
  });
})();
