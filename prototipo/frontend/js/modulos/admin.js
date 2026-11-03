/* ============================================================
   SOME — módulo Administración (#/admin), solo rol «administrador»
   Pestañas: Usuarios, Catálogo (RF-20), Auditoría, Sistema (con la
   caché y la sincronización de NOAA OISST, Copernicus Marine y CHIRPS v3).
   ============================================================ */
(function () {
  'use strict';
  const SOME = window.SOME;
  const util = window.util;
  const api = window.api;

  const PESTANAS = [
    { id: 'usuarios', titulo: 'Usuarios' },
    { id: 'catalogo', titulo: 'Catálogo' },
    { id: 'auditoria', titulo: 'Auditoría' },
    { id: 'sistema', titulo: 'Sistema' },
  ];
  const ROLES = ['oceanografo', 'analista', 'revisor', 'administrador'];
  const DEPARTAMENTOS = ['Oceanografía y Meteorología Marina', 'Tecnologías de la Información', 'Hidrografía', 'Ciencias del Mar', 'Dirección'];
  // Etiquetas en español de las acciones de la auditoría (el backend guarda identificadores internos)
  const ETIQUETAS_ACCION = {
    ingreso: 'Ingreso', salida: 'Salida', ingreso_fallido: 'Ingreso fallido',
    usuario_creado: 'Usuario creado', usuario_editado: 'Usuario editado', usuario_eliminado: 'Usuario eliminado',
    catalogo_cambiado: 'Catálogo cambiado', vista_compartida: 'Vista compartida', vista_privada: 'Vista privada',
    vista_creada: 'Vista creada', vista_eliminada: 'Vista eliminada', vista_editada: 'Vista editada',
    reporte_generado: 'Reporte generado', exportacion: 'Exportación', panel_guardado: 'Panel guardado',
  };
  function etiquetaAccion(accion) {
    const a = String(accion || '');
    if (ETIQUETAS_ACCION[a]) return ETIQUETAS_ACCION[a];
    const texto = a.replace(/_/g, ' ').trim();
    return texto ? texto.charAt(0).toUpperCase() + texto.slice(1) : '';
  }

  let estado = null;

  async function montar(contenedor, params) {
    estado = { contenedor, pestana: PESTANAS.some((p) => p.id === params.pestana) ? params.pestana : 'usuarios', usuarios: [], auditoria: [] };
    contenedor.innerHTML = `
      <div class="encabezado-pagina">
        <div>
          <h2>Administración</h2>
          <p>Usuarios, catálogo de fuentes y variables, auditoría y estado del sistema.</p>
        </div>
      </div>
      <div class="pestanas" role="tablist" id="admin-pestanas">
        ${PESTANAS.map((p) => `<button type="button" role="tab" class="pestana ${p.id === estado.pestana ? 'activa' : ''}" data-pestana="${p.id}">${p.titulo}</button>`).join('')}
      </div>
      <div id="admin-contenido"></div>`;
    contenedor.querySelectorAll('.pestana').forEach((b) => b.addEventListener('click', () => {
      estado.pestana = b.dataset.pestana;
      contenedor.querySelectorAll('.pestana').forEach((x) => x.classList.toggle('activa', x === b));
      history.replaceState(null, '', `#/admin?pestana=${estado.pestana}`);
      renderPestana();
    }));
    await renderPestana();
  }

  function desmontar() { estado = null; }

  function zona() { return estado.contenedor.querySelector('#admin-contenido'); }

  async function renderPestana() {
    if (!estado) return;
    const z = zona();
    z.innerHTML = '';
    const fn = { usuarios: renderUsuarios, catalogo: renderCatalogo, auditoria: renderAuditoria, sistema: renderSistema }[estado.pestana];
    if (fn) await fn(z);
  }

  function manejarError(z, e, reintentar) {
    if (!estado || e.codigo === 401) return;
    z.innerHTML = util.errorHtml(e.message, 'admin');
    const b = z.querySelector('[data-reintentar]');
    if (b) b.addEventListener('click', reintentar);
  }

  /* ---------- Usuarios ---------- */
  async function renderUsuarios(z) {
    util.cargando(z, 'Cargando los usuarios…');
    try {
      const r = await api.get('/admin/usuarios');
      if (!estado) return;
      estado.usuarios = Array.isArray(r) ? r : (r && Array.isArray(r.usuarios) ? r.usuarios : []);
    } catch (e) { manejarError(z, e, () => renderUsuarios(z)); return; }

    z.innerHTML = `
      <div class="barra-herramientas">
        <input type="search" id="usuarios-filtro" placeholder="Buscar por nombre, usuario o correo…" style="max-width:320px" aria-label="Buscar usuarios">
        <span class="espacio"></span>
        <span class="texto-pequeno texto-2">${estado.usuarios.length} usuario${estado.usuarios.length === 1 ? '' : 's'}</span>
        <button type="button" class="boton boton-primario" id="usuarios-nuevo">${SOME.icono('mas')} Nuevo usuario</button>
      </div>
      <div class="tarjeta" style="padding:0;overflow:hidden">
        <div class="tabla-contenedor" style="border:0;border-radius:0">
          <table class="tabla">
            <thead><tr><th>Usuario</th><th>Nombre</th><th>Rol</th><th>Departamento</th><th>Correo</th><th>Estado</th><th class="acciones">Acciones</th></tr></thead>
            <tbody id="usuarios-cuerpo"></tbody>
          </table>
        </div>
      </div>`;
    z.querySelector('#usuarios-nuevo').addEventListener('click', () => editarUsuario(null));
    z.querySelector('#usuarios-filtro').addEventListener('input', util.debounce(pintarUsuarios, 150));
    pintarUsuarios();
  }

  function pintarUsuarios() {
    const z = zona();
    const cuerpo = z.querySelector('#usuarios-cuerpo');
    if (!cuerpo) return;
    const filtro = (z.querySelector('#usuarios-filtro').value || '').toLowerCase().trim();
    const items = estado.usuarios.filter((u) => !filtro || [u.usuario, u.nombre, u.correo, u.departamento].some((c) => String(c || '').toLowerCase().includes(filtro)));
    const yo = SOME.estado.usuario || {};
    cuerpo.innerHTML = items.length ? items.map((u) => `
      <tr data-id="${util.escapar(u.id)}">
        <td><code>${util.escapar(u.usuario)}</code></td>
        <td>${util.escapar(u.nombre || '')}</td>
        <td>${util.escapar(SOME.nombreRol(u.rol))}</td>
        <td>${util.escapar(u.departamento || '')}</td>
        <td><a href="mailto:${util.escapar(u.correo || '')}">${util.escapar(u.correo || '')}</a></td>
        <td>${u.activo === false ? '<span class="insignia">Inactivo</span>' : '<span class="insignia insignia-exito">Activo</span>'}</td>
        <td class="acciones">
          <button type="button" class="boton boton-icono boton-pequeno" data-accion="editar" aria-label="Editar" title="Editar">${SOME.icono('editar')}</button>
          <button type="button" class="boton boton-pequeno" data-accion="activar" ${u.usuario === yo.usuario ? 'disabled title="No puede desactivar su propia cuenta"' : ''}>${u.activo === false ? 'Activar' : 'Desactivar'}</button>
          <button type="button" class="boton boton-icono boton-pequeno boton-peligro" data-accion="eliminar" aria-label="Eliminar" title="Eliminar" ${u.usuario === yo.usuario ? 'disabled' : ''}>${SOME.icono('eliminar')}</button>
        </td>
      </tr>`).join('') : `<tr><td colspan="7" class="centrado texto-2" style="padding:24px">No hay usuarios que coincidan con la búsqueda.</td></tr>`;
    cuerpo.querySelectorAll('[data-accion]').forEach((b) => b.addEventListener('click', () => {
      const id = b.closest('tr').dataset.id;
      const u = estado.usuarios.find((x) => String(x.id) === id);
      if (!u) return;
      if (b.dataset.accion === 'editar') editarUsuario(u);
      else if (b.dataset.accion === 'activar') alternarActivo(u);
      else if (b.dataset.accion === 'eliminar') eliminarUsuario(u);
    }));
  }

  function editarUsuario(u) {
    const nuevo = !u;
    u = u || { usuario: '', nombre: '', rol: 'oceanografo', departamento: DEPARTAMENTOS[0], correo: '', activo: true };
    const form = util.el(`
      <form novalidate>
        <div class="formulario-cuadricula">
          <div class="campo"><label for="u-usuario">Usuario</label><input id="u-usuario" type="text" autocomplete="username" autocapitalize="off" spellcheck="false" maxlength="30" value="${util.escapar(u.usuario)}" ${nuevo ? '' : 'readonly'} required></div>
          <div class="campo"><label for="u-nombre">Nombre completo</label><input id="u-nombre" type="text" maxlength="80" value="${util.escapar(u.nombre || '')}" required></div>
          <div class="campo"><label for="u-rol">Rol</label><select id="u-rol">${ROLES.map((r) => `<option value="${r}" ${u.rol === r ? 'selected' : ''}>${util.escapar(SOME.nombreRol(r))}</option>`).join('')}</select></div>
          <div class="campo"><label for="u-departamento">Departamento</label><input id="u-departamento" type="text" list="u-departamentos" maxlength="80" value="${util.escapar(u.departamento || '')}"><datalist id="u-departamentos">${DEPARTAMENTOS.map((d) => `<option value="${util.escapar(d)}">`).join('')}</datalist></div>
          <div class="campo"><label for="u-correo">Correo</label><input id="u-correo" type="email" maxlength="100" value="${util.escapar(u.correo || '')}" placeholder="usuario@inocar.mil.ec"></div>
          <div class="campo"><label for="u-clave">${nuevo ? 'Contraseña' : 'Nueva contraseña (opcional)'}</label><input id="u-clave" type="password" autocomplete="new-password" minlength="6" ${nuevo ? 'required' : ''} placeholder="${nuevo ? 'mínimo 6 caracteres' : 'dejar vacío para no cambiarla'}"></div>
        </div>
        <label class="conmutador mt-8"><input id="u-activo" type="checkbox" ${u.activo !== false ? 'checked' : ''}><span class="pista"></span><span>Cuenta activa</span></label>
      </form>`);
    const m = util.modal({
      titulo: nuevo ? 'Nuevo usuario' : `Editar usuario: ${u.usuario}`,
      contenido: form,
      acciones: [
        { texto: 'Cancelar', clase: 'boton-secundario' },
        {
          texto: nuevo ? 'Crear' : 'Guardar', clase: 'boton-primario',
          accion: async () => {
            const cuerpo = {
              usuario: form.querySelector('#u-usuario').value.trim(),
              nombre: form.querySelector('#u-nombre').value.trim(),
              rol: form.querySelector('#u-rol').value,
              departamento: form.querySelector('#u-departamento').value.trim(),
              correo: form.querySelector('#u-correo').value.trim(),
              activo: form.querySelector('#u-activo').checked,
            };
            const clave = form.querySelector('#u-clave').value;
            if (!cuerpo.usuario || !cuerpo.nombre) { util.toast('El usuario y el nombre son obligatorios.', 'aviso'); return false; }
            if (nuevo && clave.length < 6) { util.toast('La contraseña debe tener al menos 6 caracteres.', 'aviso'); return false; }
            if (clave) cuerpo.clave = clave;
            if (nuevo) await api.post('/admin/usuarios', cuerpo);
            else await api.put(`/admin/usuarios/${encodeURIComponent(u.id)}`, cuerpo);
            util.toast(nuevo ? `Usuario «${cuerpo.usuario}» creado.` : 'Usuario actualizado.', 'exito');
            renderUsuarios(zona());
            return true;
          },
        },
      ],
    });
    form.addEventListener('submit', (ev) => { ev.preventDefault(); m.elemento.querySelector('.modal-acciones .boton-primario').click(); });
  }

  async function alternarActivo(u) {
    const activar = u.activo === false;
    if (!activar) {
      const ok = await util.confirmar(`¿Desactivar la cuenta de «${u.nombre || u.usuario}»? No podrá ingresar hasta que se la active de nuevo.`, { aceptar: 'Desactivar', peligro: true });
      if (!ok) return;
    }
    try {
      await api.put(`/admin/usuarios/${encodeURIComponent(u.id)}`, { usuario: u.usuario, nombre: u.nombre, rol: u.rol, departamento: u.departamento, correo: u.correo, activo: activar });
      util.toast(activar ? 'Cuenta activada.' : 'Cuenta desactivada.', 'exito');
      renderUsuarios(zona());
    } catch (e) { util.toast(e.message, 'error'); }
  }

  async function eliminarUsuario(u) {
    const ok = await util.confirmar(`¿Eliminar al usuario «${u.nombre || u.usuario}»? Sus paneles y vistas privadas dejarán de estar disponibles.`, { aceptar: 'Eliminar', peligro: true });
    if (!ok) return;
    try {
      await api.del(`/admin/usuarios/${encodeURIComponent(u.id)}`);
      util.toast('Usuario eliminado.', 'exito');
      renderUsuarios(zona());
    } catch (e) { util.toast(e.message, 'error'); }
  }

  /* ---------- Catálogo ---------- */
  async function renderCatalogo(z) {
    util.cargando(z, 'Cargando el catálogo…');
    try { await SOME.recargarCatalogo(); } catch (e) { manejarError(z, e, () => renderCatalogo(z)); return; }
    if (!estado) return;
    const fuentes = (SOME.estado.catalogo && SOME.estado.catalogo.fuentes) || [];
    z.innerHTML = `
      <div class="alerta alerta-info">${SOME.icono('acerca')}<div><strong>Habilitar productos y variables sin código (RF-20)</strong>Las fuentes o variables deshabilitadas dejan de aparecer en el constructor de consultas y en los widgets. Los cambios se aplican de inmediato a todos los usuarios y quedan registrados en la auditoría.</div></div>
      <div class="cuadricula col-3" id="catalogo-fuentes"></div>`;
    const cont = z.querySelector('#catalogo-fuentes');
    if (!fuentes.length) { cont.className = ''; cont.innerHTML = util.vacioHtml('Sin fuentes', 'El catálogo está vacío.'); return; }
    fuentes.forEach((f) => {
      // Título con el nombre corto: el completo se recortaba con elipsis; va en el title y el identificador se ve debajo
      const t = util.el(`
        <article class="tarjeta">
          <div class="fila fila-entre fila-arriba mb-12">
            <div style="min-width:0">
              <h3 class="recortar" title="${util.escapar(f.nombre)}">${util.escapar(f.nombre_corto || f.nombre)}</h3>
              <small class="texto-2">${util.escapar(f.proveedor || '')}${f.identificador ? ` · <code>${util.escapar(f.identificador)}</code>` : ''}</small>
            </div>
            <label class="conmutador" title="Habilitar o deshabilitar la fuente"><input type="checkbox" data-fuente="${util.escapar(f.id)}" ${f.habilitada !== false ? 'checked' : ''}><span class="pista"></span><span class="visualmente-oculto">Fuente habilitada</span></label>
          </div>
          <ul class="lista" style="opacity:${f.habilitada !== false ? 1 : 0.55}">
            ${(f.variables || []).map((v) => `
              <li>
                <span class="lista-texto"><strong>${util.escapar(v.nombre)}</strong><small><code>${util.escapar(v.id)}</code>${v.unidad ? ` · ${util.escapar(v.unidad)}` : ''}${v.tiene_profundidad ? ' · con profundidad' : ''}${v.es_vector ? ' · vectorial' : ''}</small></span>
                <label class="conmutador" title="Habilitar o deshabilitar la variable"><input type="checkbox" data-fuente="${util.escapar(f.id)}" data-variable="${util.escapar(v.id)}" ${v.habilitada !== false ? 'checked' : ''} ${f.habilitada === false ? 'disabled' : ''}><span class="pista"></span><span class="visualmente-oculto">Variable habilitada</span></label>
              </li>`).join('')}
          </ul>
          ${f.estado ? `<div class="tarjeta-pie fila" style="gap:6px"><span class="semaforo ${SOME.claseSemaforo(f.estado.estado)}"></span>${util.escapar(SOME.textoEstadoFuente(f.estado.estado))} · corte ${util.fmtFecha(f.estado.fecha_corte)}</div>` : ''}
        </article>`);
      cont.appendChild(t);
    });
    cont.querySelectorAll('input[type="checkbox"]').forEach((c) => c.addEventListener('change', async () => {
      const habilitada = c.checked;
      c.disabled = true;
      try {
        if (c.dataset.variable) await api.put(`/admin/catalogo/variables/${encodeURIComponent(c.dataset.fuente)}/${encodeURIComponent(c.dataset.variable)}`, { habilitada });
        else await api.put(`/admin/catalogo/fuentes/${encodeURIComponent(c.dataset.fuente)}`, { habilitada });
        util.toast(`${c.dataset.variable ? 'Variable' : 'Fuente'} ${habilitada ? 'habilitada' : 'deshabilitada'}.`, 'exito');
        await renderCatalogo(z);
      } catch (e) {
        c.checked = !habilitada;
        c.disabled = false;
        util.toast(e.message, 'error');
      }
    }));
  }

  /* ---------- Auditoría ---------- */
  async function renderAuditoria(z) {
    util.cargando(z, 'Cargando la auditoría…');
    try {
      const r = await api.get('/admin/auditoria?limite=200');
      if (!estado) return;
      estado.auditoria = Array.isArray(r) ? r : (r && Array.isArray(r.entradas) ? r.entradas : []);
    } catch (e) { manejarError(z, e, () => renderAuditoria(z)); return; }
    const usuarios = Array.from(new Set(estado.auditoria.map((a) => a.usuario).filter(Boolean))).sort();
    const acciones = Array.from(new Set(estado.auditoria.map((a) => a.accion).filter(Boolean))).sort();
    z.innerHTML = `
      <div class="barra-herramientas">
        <select id="aud-usuario" aria-label="Filtrar por usuario" style="max-width:220px"><option value="">Todos los usuarios</option>${usuarios.map((u) => `<option value="${util.escapar(u)}">${util.escapar(u)}</option>`).join('')}</select>
        <select id="aud-accion" aria-label="Filtrar por acción" style="max-width:220px"><option value="">Todas las acciones</option>${acciones.map((a) => `<option value="${util.escapar(a)}">${util.escapar(etiquetaAccion(a))}</option>`).join('')}</select>
        <input type="search" id="aud-texto" placeholder="Buscar en el detalle…" aria-label="Buscar en el detalle" style="max-width:280px">
        <span class="espacio"></span>
        <span class="texto-pequeno texto-2" id="aud-total"></span>
        <button type="button" class="boton" id="aud-actualizar">${SOME.icono('actualizar')} Actualizar</button>
      </div>
      <div class="tarjeta" style="padding:0;overflow:hidden">
        <div class="tabla-contenedor" style="border:0;border-radius:0">
          <table class="tabla">
            <thead><tr><th style="width:150px">Fecha y hora</th><th>Usuario</th><th>Acción</th><th>Detalle</th><th>IP</th></tr></thead>
            <tbody id="aud-cuerpo"></tbody>
          </table>
        </div>
      </div>`;
    const pintar = () => {
      const fu = z.querySelector('#aud-usuario').value;
      const fa = z.querySelector('#aud-accion').value;
      const ft = z.querySelector('#aud-texto').value.toLowerCase().trim();
      const items = estado.auditoria.filter((a) => (!fu || a.usuario === fu) && (!fa || a.accion === fa) && (!ft || String(a.detalle || '').toLowerCase().includes(ft)));
      z.querySelector('#aud-total').textContent = `${items.length} de ${estado.auditoria.length} registros`;
      z.querySelector('#aud-cuerpo').innerHTML = items.length ? items.map((a) => `
        <tr>
          <td class="tabular">${util.fmtFechaHora(a.fecha)}</td>
          <td><code>${util.escapar(a.usuario || '')}</code></td>
          <td><span class="insignia ${claseAccion(a.accion)}" title="${util.escapar(a.accion || '')}">${util.escapar(etiquetaAccion(a.accion))}</span></td>
          <td>${util.escapar(a.detalle || '')}</td>
          <td class="mono">${util.escapar(a.ip || '')}</td>
        </tr>`).join('') : '<tr><td colspan="5" class="centrado texto-2" style="padding:24px">No hay registros que coincidan con el filtro.</td></tr>';
    };
    z.querySelector('#aud-usuario').addEventListener('change', pintar);
    z.querySelector('#aud-accion').addEventListener('change', pintar);
    z.querySelector('#aud-texto').addEventListener('input', util.debounce(pintar, 150));
    z.querySelector('#aud-actualizar').addEventListener('click', () => renderAuditoria(z));
    pintar();
  }

  function claseAccion(accion) {
    const a = String(accion || '').toLowerCase();
    if (/elimin|borr|desactiv/.test(a)) return 'insignia-peligro';
    if (/cre|alta|ingres/.test(a)) return 'insignia-exito';
    if (/edit|modific|cambi|catalogo|catálogo|compart/.test(a)) return 'insignia-info';
    return '';
  }

  /* ---------- Sistema ---------- */
  async function renderSistema(z) {
    util.cargando(z, 'Consultando el estado del sistema…');
    let s;
    try { s = await api.get('/admin/sistema'); } catch (e) { manejarError(z, e, () => renderSistema(z)); return; }
    if (!estado) return;
    s = s || {};
    const cache = s.cache || {};
    const trabajos = s.trabajos || {};
    const total = (cache.aciertos || 0) + (cache.fallos || 0);
    const tasa = total ? (100 * (cache.aciertos || 0) / total) : null;
    z.innerHTML = `
      <div class="cuadricula col-4 mb-16">
        ${kpi('Versión', util.escapar(s.version || SOME.version), 'Prototipo demostrativo')}
        ${kpi('Tiempo activo', fmtDuracion(s.tiempo_activo_s), s.inicio ? `Desde ${util.fmtFechaHora(s.inicio)}` : '')}
        ${kpi('Usuarios activos', util.fmtNum(s.usuarios_activos, 0), 'Con sesión vigente')}
        ${kpi('Consultas hoy', util.fmtNum(s.consultas_hoy, 0), `${util.fmtNum(s.consultas_total, 0)} en total`)}
      </div>
      <div class="cuadricula col-2">
        <div class="tarjeta">
          <div class="tarjeta-titulo">Caché de consultas</div>
          <div class="cuadricula col-3" style="gap:8px">
            ${kpiPequeno('Entradas', util.fmtNum(cache.entradas, 0))}
            ${kpiPequeno('Aciertos', util.fmtNum(cache.aciertos, 0))}
            ${kpiPequeno('Fallos', util.fmtNum(cache.fallos, 0))}
          </div>
          ${tasa !== null ? `<div class="mt-12"><div class="fila fila-entre texto-pequeno texto-2"><span>Tasa de aciertos</span><span>${util.fmtNum(tasa, 0)} %</span></div><div class="progreso mt-8"><span style="width:${Math.round(tasa)}%"></span></div></div>` : ''}
        </div>
        <div class="tarjeta">
          <div class="tarjeta-titulo">Cola de trabajos</div>
          <div class="cuadricula col-4" style="gap:8px">
            ${kpiPequeno('En cola', util.fmtNum(trabajos.en_cola, 0))}
            ${kpiPequeno('Procesando', util.fmtNum(trabajos.procesando, 0))}
            ${kpiPequeno('Listos', util.fmtNum(trabajos.listos, 0))}
            ${kpiPequeno('Con error', util.fmtNum(trabajos.error, 0))}
          </div>
        </div>
      </div>
      ${tarjetaNoaa(s.noaa)}
      ${tarjetaCopernicus(s.copernicus)}
      ${tarjetaChirps(s.chirps)}
      <div class="alerta ${todasReales(s) ? 'alerta-info' : 'alerta-aviso'} mt-16">${SOME.icono(todasReales(s) ? 'acerca' : 'advertencia')}<div><strong>Procedencia de los datos</strong>${textoProcedencia(s)} En el producto real esta pantalla mostrará además el estado del lago de datos y de la base PostgreSQL/PostGIS.</div></div>`;
  }

  /** Fuentes reales que informa el servidor (con un servidor anterior, solo CHIRPS v3). */
  function fuentesReales(s) {
    return Array.isArray(s.fuentes_reales) ? s.fuentes_reales : ['chirps_v3'];
  }
  function todasReales(s) {
    const reales = fuentesReales(s);
    return ['noaa_oisst', 'copernicus_phy', 'chirps_v3'].every((id) => reales.includes(id));
  }

  /** Qué es real y qué responde con su simulador en este momento, según las fuentes reales que informa el servidor. */
  function textoProcedencia(s) {
    const reales = fuentesReales(s);
    return [
      reales.includes('noaa_oisst')
        ? 'La temperatura superficial del mar proviene de NOAA OISST v2.1 (datos reales de NOAA/NCEI, servidos por NOAA PSL; los últimos ~15 días son preliminares) y el índice Niño 1+2 se calcula con ella en la región oficial (0–10° S, 90–80° O).'
        : (s.noaa
          ? 'NOAA OISST y el índice Niño 1+2 responden ahora con su simulador (sin acceso ni datos en la caché local; vea la tarjeta de NOAA OISST).'
          // Servidor anterior a la integración de NOAA OISST real (sin bloque «noaa»): su conector es el simulado
          : 'NOAA OISST y el índice Niño 1+2 se simulan de forma determinista.'),
      reales.includes('copernicus_phy')
        ? 'Las variables físicas del océano provienen del análisis diario de Copernicus Marine (datos reales; E.U. Copernicus Marine Service Information).'
        : 'Copernicus Marine responde ahora con su simulador (sin acceso ni datos en la caché local; vea la tarjeta de Copernicus Marine).',
      reales.includes('chirps_v3')
        ? 'La precipitación proviene de CHIRPS v3 (datos reales del Climate Hazards Center, UC Santa Barbara).'
        : 'CHIRPS v3 responde ahora con su simulador (sin acceso ni datos en la caché local).',
      'Lo que no esté en la caché local de una fuente y no se pueda descargar se responde con su simulador, señalado como tal.',
    ].join(' ');
  }

  /**
   * Tarjeta de la caché y la sincronización de NOAA OISST v2.1 (fuente real), si el servidor la informa: modo,
   * estado, fecha de corte, días finales, preliminares y faltantes (y de qué servidor salió cada uno), lo que
   * publica NOAA, los servidores NOAA PSL y NOAA NCEI, climatologías, tamaño, descargas, sincronización y errores.
   */
  function tarjetaNoaa(nb) {
    if (!nb || typeof nb !== 'object') return '';
    if (nb.error) return `<div class="alerta alerta-aviso mt-16">${SOME.icono('advertencia')}<div><strong>NOAA OISST</strong>${util.escapar(nb.error)}</div></div>`;
    const fecha = (x) => (x ? util.fmtFecha(x) : '—');
    const fechaHora = (x) => (x ? util.fmtFechaHora(x) : '—');
    const mb = util.esNumero(nb.tamano_cache_bytes) ? nb.tamano_cache_bytes / 1048576 : null;
    const descargados = util.esNumero(nb.bytes_descargados) ? nb.bytes_descargados / 1048576 : null;
    const errores = Array.isArray(nb.errores_recientes) ? nb.errores_recientes.slice(-5).reverse() : [];
    const sv = nb.servidores || {};
    const pub = nb.publicacion || {};
    const cl = nb.climatologia || {};
    const real = nb.datos_reales === true;
    const forzado = nb.modo_configurado === 'simulado';
    const insignia = forzado
      ? '<span class="insignia insignia-simulado" title="SOME_NOAA_MODO=simulado: no se consulta NOAA">Modo simulado</span>'
      : `<span class="insignia insignia-conexion" title="Estado y verificaciones reales de NOAA PSL y NOAA NCEI">Conexión real</span>${real ? '' : ' <span class="insignia insignia-simulado" title="Sin acceso ni datos en la caché local: las consultas usan el simulador y lo señalan">Datos simulados</span>'}`;
    const textoModo = { auto: 'automático', real: 'real (estricto, sin respaldo simulado)', simulado: 'simulado forzado' }[nb.modo_configurado] || (nb.modo_configurado || '—');
    const dias = (n) => `${util.fmtNum(n || 0, 0)} ${n === 1 ? 'día' : 'días'}`;
    const po = nb.dias_por_origen || {};
    const textoOrigenes = [po.psl ? `NOAA PSL: ${util.fmtNum(po.psl, 0)}` : '', po.ncei ? `NOAA NCEI (respaldo): ${util.fmtNum(po.ncei, 0)}` : '']
      .filter(Boolean).join(' · ') || '—';
    const textoServidor = (s) => {
      s = s || {};
      const lat = util.esNumero(s.latencia_ms) && s.latencia_ms > 0 ? ` · verificación en ${util.fmtNum(s.latencia_ms, 0)} ms` : '';
      return `${util.escapar(s.texto_estado || SOME.textoEstadoFuente(s.estado))}${lat}${s.ultimo_contacto ? ` · último contacto: ${fechaHora(s.ultimo_contacto)}` : ''}`;
    };
    const v = nb.ultima_verificacion && typeof nb.ultima_verificacion === 'object' ? nb.ultima_verificacion : null;
    const respondio = (x) => (x && x.ok ? 'respondió' : 'no respondió');
    const textoVerificacion = v ? `${fechaHora(v.momento)} · PSL ${respondio(v.psl)} · NCEI ${respondio(v.ncei)}` : 'Aún no se verifica';
    const r = nb.resultado_ultima_sincronizacion && typeof nb.resultado_ultima_sincronizacion === 'object' ? nb.resultado_ultima_sincronizacion : null;
    const textoSinc = r
      ? (r.omitida
        ? `omitida (${r.omitida})`
        : `${dias(r.dias_escritos)} ${r.dias_escritos === 1 ? 'escrito' : 'escritos'} (${util.fmtNum(r.finales || 0, 0)} finales y ${util.fmtNum(r.preliminares || 0, 0)} preliminares${r.dias_ncei ? `; ${util.fmtNum(r.dias_ncei, 0)} de NOAA NCEI` : ''}) en ${util.fmtNum(r.peticiones || 0, 0)} ${r.peticiones === 1 ? 'petición' : 'peticiones'}${r.errores ? ` · ${util.fmtNum(r.errores, 0)} ${r.errores === 1 ? 'error' : 'errores'}` : ''}`)
      : '';
    const periodos = Array.isArray(cl.periodos_en_cache) ? cl.periodos_en_cache : [];
    return `
      <div class="tarjeta mt-16">
        <div class="fila fila-entre fila-arriba mb-8">
          <div class="tarjeta-titulo mb-0">NOAA OISST v2.1 · caché y sincronización</div>
          <span>${insignia}</span>
        </div>
        ${nb.mensaje_estado ? `<div class="alerta ${nb.estado === 'operativa' ? 'alerta-info' : (nb.estado === 'degradada' ? 'alerta-aviso' : 'alerta-peligro')} mb-8" style="padding:8px 10px">${util.escapar(nb.mensaje_estado)}</div>` : ''}
        <div class="cuadricula col-4" style="gap:8px">
          ${kpiPequeno('Días en caché', util.fmtNum(nb.dias_en_cache, 0))}
          ${kpiPequeno('Días preliminares', util.fmtNum(nb.dias_preliminares, 0))}
          ${kpiPequeno('Días faltantes', util.fmtNum(nb.dias_faltantes, 0))}
          ${kpiPequeno('Fecha de corte', fecha(nb.fecha_corte))}
        </div>
        <ul class="lista mt-12">
          <li><span class="lista-texto"><small>Estado (RF-21)</small></span><strong>${util.escapar(SOME.textoEstadoFuente(nb.estado))}</strong></li>
          <li><span class="lista-texto"><small>Modo (SOME_NOAA_MODO)</small></span><strong>${util.escapar(textoModo)}${nb.modo_sin_red ? ' · modo sin red activo' : ''}</strong></li>
          <li><span class="lista-texto"><small>En caché: último final / preliminares</small></span><strong>${fecha(nb.ultimo_final)} / ${nb.primer_preliminar ? `${fecha(nb.primer_preliminar)} – ${fecha(nb.ultimo_preliminar)}` : '—'}</strong></li>
          <li><span class="lista-texto"><small>Días en caché por origen</small></span><strong>${util.escapar(textoOrigenes)}</strong></li>
          <li><span class="lista-texto"><small>Publicado por NOAA (último día en PSL · finales · preliminares)</small></span><strong>${fecha(pub.ultimo_dia_psl)} · hasta ${fecha(pub.ultimo_final)} · hasta ${fecha(pub.ultimo_preliminar)}</strong></li>
          <li><span class="lista-texto"><small>NOAA PSL (THREDDS, subconjuntos NCSS)</small></span><strong style="white-space:normal;text-align:right">${textoServidor(sv.psl)}</strong></li>
          <li><span class="lista-texto"><small>NOAA NCEI (listado y archivos diarios de respaldo)</small></span><strong style="white-space:normal;text-align:right">${textoServidor(sv.ncei)}</strong></li>
          <li><span class="lista-texto"><small>Última verificación</small></span><strong>${util.escapar(textoVerificacion)}</strong></li>
          <li style="display:block"><small class="texto-2" style="display:block;font-size:12px">Conjunto que mantiene la sincronización</small><strong style="display:block;white-space:normal;margin-top:2px">${util.escapar(nb.conjunto_sincronizado || '—')}</strong></li>
          <li><span class="lista-texto"><small>Última sincronización</small></span><strong style="white-space:normal;text-align:right">${nb.ultima_sincronizacion ? fechaHora(nb.ultima_sincronizacion) : 'Aún no se sincroniza'}${textoSinc ? ` · ${util.escapar(textoSinc)}` : ''}</strong></li>
          <li><span class="lista-texto"><small>Próxima sincronización</small></span><strong>${nb.modo_sin_red ? 'Modo sin red activo' : (nb.proxima_sincronizacion ? fechaHora(nb.proxima_sincronizacion) : '—')}</strong></li>
          <li><span class="lista-texto"><small>Sincronización en segundo plano (cada 6 horas)</small></span><strong>${nb.sincronizacion_activa ? 'Activa' : 'Detenida'}</strong></li>
          <li><span class="lista-texto"><small>Climatologías en caché</small></span><strong>${util.fmtNum(periodos.length, 0)} de ${util.fmtNum(cl.total || 3, 0)}${periodos.length ? ` (${util.escapar(periodos.join(', '))})` : ''} · diaria de Niño 1+2: ${cl.ltm_diaria_nino12 ? 'sí' : 'no'}</strong></li>
          <li><span class="lista-texto"><small>Tamaño de la caché / descargado de NOAA</small></span><strong>${mb !== null ? util.fmtNum(mb, 1) + ' MB' : '—'} / ${descargados !== null ? util.fmtNum(descargados, 1) + ' MB' : '—'} en ${util.fmtNum(nb.peticiones || 0, 0)} ${nb.peticiones === 1 ? 'petición' : 'peticiones'}</strong></li>
        </ul>
        ${errores.length ? `<div class="texto-pequeno texto-2 mt-12 mb-8">Errores recientes</div><ul class="lista">${errores.map((e) => `<li><span class="lista-texto"><small style="display:block">${util.escapar(e && e.momento ? util.fmtFechaHora(e.momento) : '')}</small><span style="white-space:normal">${util.escapar(e && typeof e === 'object' ? (e.mensaje || '') : String(e))}</span></span></li>`).join('')}</ul>` : ''}
        <p class="texto-pequeno texto-2 mt-12 mb-0">Para llenar la caché antes de la demostración: <code>python -m backend.herramientas.preparar_noaa</code> (NOAA PSL limita el ritmo de peticiones: con la caché vacía tarda unos 20 minutos; después solo pide los días nuevos y renueva los preliminares).</p>
      </div>`;
  }

  /**
   * Tarjeta de la caché y la sincronización de Copernicus Marine (fuente real), si el servidor la informa:
   * modo, estado, último día de análisis, credenciales (solo su estado), conjuntos en caché, climatología
   * GLORYS12, tamaño, descargas, sincronización y errores recientes.
   */
  function tarjetaCopernicus(cp) {
    if (!cp || typeof cp !== 'object') return '';
    if (cp.error) return `<div class="alerta alerta-aviso mt-16">${SOME.icono('advertencia')}<div><strong>Copernicus Marine</strong>${util.escapar(cp.error)}</div></div>`;
    const fecha = (x) => (x ? util.fmtFecha(x) : '—');
    const fechaHora = (x) => (x ? util.fmtFechaHora(x) : '—');
    const mb = util.esNumero(cp.tamano_cache_bytes) ? cp.tamano_cache_bytes / 1048576 : null;
    const errores = Array.isArray(cp.errores_recientes) ? cp.errores_recientes.slice(-5).reverse() : [];
    const cr = cp.credenciales || {};
    const pq = cp.paquete || {};
    const sv = cp.servicio || {};
    const cl = cp.climatologia || {};
    const conjuntos = Array.isArray(cp.conjuntos) ? cp.conjuntos : [];
    const real = cp.datos_reales === true;
    const forzado = cp.modo_configurado === 'simulado';
    const insignia = forzado
      ? '<span class="insignia insignia-simulado" title="SOME_COPERNICUS_MODO=simulado: no se consulta Copernicus Marine">Modo simulado</span>'
      : `<span class="insignia insignia-conexion" title="Estado y verificaciones reales del Copernicus Marine Data Store">Conexión real</span>${real ? '' : ' <span class="insignia insignia-simulado" title="Sin acceso ni datos en la caché local: las consultas usan el simulador y lo señalan">Datos simulados</span>'}`;
    const textoModo = { auto: 'automático', real: 'real (estricto, sin respaldo simulado)', simulado: 'simulado forzado' }[cp.modo_configurado] || (cp.modo_configurado || '—');
    // cr.texto ya trae la fecha si son válidas («Válidas (verificadas el …).»): no se repite al lado
    const textoCred = cr.texto || { validas: 'válidas', invalidas: 'no válidas', ausentes: 'no configuradas', error: 'error al verificar', sin_verificar: 'sin verificar' }[cr.estado] || '—';
    const ultimaSinc = cp.resultado_ultima_sincronizacion && typeof cp.resultado_ultima_sincronizacion === 'object'
      ? (cp.resultado_ultima_sincronizacion.texto || cp.resultado_ultima_sincronizacion.mensaje || '') : '';
    const dias = (n) => `${util.fmtNum(n || 0, 0)} ${n === 1 ? 'día' : 'días'}`;
    const filasConjuntos = conjuntos.slice(0, 12).map((c) => `
          <li><span class="lista-texto"><small>${util.escapar(c.variable || '')}${c.nivel === null || c.nivel === undefined ? '' : ` · ${util.fmtNum(c.nivel, 0)} m`}</small></span><strong>${dias(c.dias)} (${c.desde === c.hasta ? fecha(c.desde) : `${fecha(c.desde)} – ${fecha(c.hasta)}`})${c.dias_nrt ? ` · ${util.fmtNum(c.dias_nrt, 0)} NRT` : ''}${util.esNumero(c.dias_completos) && c.dias_completos !== c.dias ? ` · ${dias(c.dias_completos)} con el área completa` : ''}</strong></li>`).join('');
    return `
      <div class="tarjeta mt-16">
        <div class="fila fila-entre fila-arriba mb-8">
          <div class="tarjeta-titulo mb-0">Copernicus Marine · caché y sincronización</div>
          <span>${insignia}</span>
        </div>
        ${cp.mensaje_estado ? `<div class="alerta ${cp.estado === 'operativa' ? 'alerta-info' : (cp.estado === 'degradada' ? 'alerta-aviso' : 'alerta-peligro')} mb-8" style="padding:8px 10px">${util.escapar(cp.mensaje_estado)}</div>` : ''}
        <div class="cuadricula col-4" style="gap:8px">
          ${kpiPequeno('Días en caché', util.fmtNum(cp.dias_en_cache, 0))}
          ${kpiPequeno('Días NRT', util.fmtNum(cp.dias_nrt, 0))}
          ${kpiPequeno('Último día de análisis', fecha(cp.ultimo_dia_analisis))}
          ${kpiPequeno('Fecha de corte', fecha(cp.fecha_corte))}
        </div>
        <ul class="lista mt-12">
          <li><span class="lista-texto"><small>Estado (RF-21)</small></span><strong>${util.escapar(SOME.textoEstadoFuente(cp.estado))}${util.esNumero(sv.latencia_ms) && sv.latencia_ms > 0 ? ` · lectura de prueba en ${util.fmtNum(sv.latencia_ms, 0)} ms` : ''}</strong></li>
          <li><span class="lista-texto"><small>Modo (SOME_COPERNICUS_MODO)</small></span><strong>${util.escapar(textoModo)}${cp.modo_sin_red ? ' · modo sin red activo' : ''}</strong></li>
          <li><span class="lista-texto"><small>Credenciales (copernicusmarine login)</small></span><strong>${util.escapar(textoCred)}${cr.verificado && !/verificad/i.test(textoCred) ? ` · ${fechaHora(cr.verificado)}` : ''}</strong></li>
          <li><span class="lista-texto"><small>Paquete copernicusmarine</small></span><strong>${pq.instalado === false ? 'no instalado (pip install -r requirements-copernicus.txt)' : (pq.version ? `versión ${util.escapar(pq.version)}` : 'instalado')}</strong></li>
          <li><span class="lista-texto"><small>Boletín semanal del análisis</small></span><strong>${fecha(cp.boletin_semanal)}</strong></li>
          <li><span class="lista-texto"><small>Mejor análisis en caché</small></span><strong>${dias(cp.dias_definitivos)}</strong></li>
          <li><span class="lista-texto"><small>Conjunto que mantiene la sincronización</small></span><strong style="white-space:normal">${util.escapar(cp.conjunto_sincronizado || '—')}</strong></li>
          <li><span class="lista-texto"><small>Última sincronización</small></span><strong>${cp.ultima_sincronizacion ? fechaHora(cp.ultima_sincronizacion) : 'Aún no se sincroniza'}${ultimaSinc ? ` · ${util.escapar(ultimaSinc)}` : ''}</strong></li>
          <li><span class="lista-texto"><small>Próxima sincronización</small></span><strong>${cp.modo_sin_red ? 'Modo sin red activo' : (cp.proxima_sincronizacion ? fechaHora(cp.proxima_sincronizacion) : '—')}</strong></li>
          <li><span class="lista-texto"><small>Sincronización en segundo plano</small></span><strong>${cp.sincronizacion_activa ? 'Activa' : 'Detenida'}</strong></li>
          <li><span class="lista-texto"><small>Climatología GLORYS12 en caché</small></span><strong>${util.fmtNum(cl.en_cache || 0, 0)} de ${util.fmtNum(cl.total || 34, 0)} · periodo ${util.escapar(cl.periodo || '—')}</strong></li>
          <li><span class="lista-texto"><small>Tamaño de la caché / descargas</small></span><strong>${mb !== null ? util.fmtNum(mb, 1) + ' MB' : '—'} / ${util.fmtNum(cp.fragmentos_descargados || 0, 0)} fragmentos en ${util.fmtNum(cp.segundos_descarga || 0, 0)} s</strong></li>
        </ul>
        ${filasConjuntos ? `<div class="texto-pequeno texto-2 mt-12 mb-8">Conjuntos en caché (variable y nivel)</div><ul class="lista">${filasConjuntos}</ul>` : ''}
        ${errores.length ? `<div class="texto-pequeno texto-2 mt-12 mb-8">Errores recientes</div><ul class="lista">${errores.map((e) => `<li><span class="lista-texto"><small style="display:block">${util.escapar(e && e.momento ? util.fmtFechaHora(e.momento) : '')}</small><span style="white-space:normal">${util.escapar(e && typeof e === 'object' ? (e.mensaje || '') : String(e))}</span></span></li>`).join('')}</ul>` : ''}
        <p class="texto-pequeno texto-2 mt-12 mb-0">Para llenar la caché antes de la demostración: <code>python -m backend.herramientas.preparar_copernicus</code> (requiere haber ejecutado <code>copernicusmarine login</code> en este equipo).</p>
      </div>`;
  }

  /** Tarjeta de la caché y la sincronización de CHIRPS v3 (fuente real), si el servidor la informa. */
  function tarjetaChirps(ch) {
    if (!ch || typeof ch !== 'object') return '';
    if (ch.error) return `<div class="alerta alerta-aviso mt-16">${SOME.icono('advertencia')}<div><strong>CHIRPS v3</strong>${util.escapar(ch.error)}</div></div>`;
    const mb = util.esNumero(ch.tamano_cache_bytes) ? ch.tamano_cache_bytes / 1048576 : null;
    const descargados = util.esNumero(ch.bytes_descargados) ? ch.bytes_descargados / 1048576 : null;
    const errores = Array.isArray(ch.errores_recientes) ? ch.errores_recientes.slice(-5).reverse() : [];
    const anios = Array.isArray(ch.anios_climatologia) ? ch.anios_climatologia : [];
    const textoAnios = anios.length ? `${anios[0]}–${anios[anios.length - 1]} (${anios.length} años)` : 'sin datos mensuales';
    const po = ch.dias_por_origen || {};
    const textoOrigenes = [po.chc ? `servidor del CHC: ${util.fmtNum(po.chc, 0)}` : '', po.espejo ? `réplica dynamical.org: ${util.fmtNum(po.espejo, 0)}` : '']
      .filter(Boolean).join(' · ') || '—';
    const fu = ch.fuentes || {};
    const chc = fu.chc || {};
    const esp = fu.espejo || {};
    const dis = ch.disyuntor_chc || chc.disyuntor || {};
    const txtAcc = (a) => (a === true ? 'responde' : (a === false ? 'no responde' : 'sin verificar'));
    const fecha = (x) => (x ? util.fmtFecha(x) : '—');
    const hasta = (iv) => (Array.isArray(iv) && iv[1] ? util.fmtFecha(iv[1]) : '—');
    const filasFuentes = ch.modo_sin_red ? '' : `
          <li><span class="lista-texto"><small>Servidor del CHC (data.chc.ucsb.edu)</small></span><strong>${util.escapar(txtAcc(chc.accesible))}${chc.ultimo_contacto ? ` · ${util.fmtFechaHora(chc.ultimo_contacto)}` : ''} · publica final hasta ${fecha(chc.ultimo_final_publicado)}, preliminar hasta ${fecha(chc.ultimo_preliminar_publicado)}</strong></li>
          <li><span class="lista-texto"><small>Disyuntor del CHC (cortesía: 1 petición/s, un archivo a la vez)</small></span><strong>${dis.abierto ? `abierto hasta ${util.fmtFechaHora(dis.hasta)}` : 'cerrado'}${dis.aperturas ? ` · ${util.fmtNum(dis.aperturas, 0)} ${dis.aperturas === 1 ? 'apertura' : 'aperturas'}` : ''}</strong></li>
          <li><span class="lista-texto"><small>Réplica dynamical.org (CC BY 4.0)</small></span><strong>${esp.instalado === false ? 'no instalada (faltan icechunk y zarr)' : util.escapar(txtAcc(esp.accesible))}${esp.consultado ? ` · ${util.fmtFechaHora(esp.consultado)}` : ''} · final hasta ${hasta(esp.final)}, preliminar hasta ${hasta(esp.preliminar)}</strong></li>`;
    return `
      <div class="tarjeta mt-16">
        <div class="fila fila-entre fila-arriba mb-8">
          <div class="tarjeta-titulo mb-0">CHIRPS v3 · caché y sincronización</div>
          <span class="insignia insignia-real" title="Datos reales de CHIRPS v3, obtenidos del servidor del Climate Hazards Center o de la réplica pública de dynamical.org (el producto del CHC con los valores redondeados para comprimir)">Conexión real</span>
        </div>
        <div class="cuadricula col-4" style="gap:8px">
          ${kpiPequeno('Días en caché', util.fmtNum(ch.dias_en_cache, 0))}
          ${kpiPequeno('Días preliminares', util.fmtNum(ch.dias_preliminares, 0))}
          ${kpiPequeno('Días faltantes', util.fmtNum(ch.dias_faltantes, 0))}
          ${kpiPequeno('Fecha de corte', util.fmtFecha(ch.fecha_corte))}
        </div>
        <ul class="lista mt-12">
          <li><span class="lista-texto"><small>Última sincronización</small></span><strong>${ch.ultima_sincronizacion ? util.fmtFechaHora(ch.ultima_sincronizacion) : 'Aún no se sincroniza'}</strong></li>
          <li><span class="lista-texto"><small>Próxima sincronización</small></span><strong>${ch.modo_sin_red ? 'Modo sin red activo' : (ch.proxima_sincronizacion ? util.fmtFechaHora(ch.proxima_sincronizacion) : '—')}</strong></li>
          <li><span class="lista-texto"><small>Sincronización en segundo plano</small></span><strong>${ch.sincronizacion_activa ? 'Activa' : 'Detenida'}</strong></li>
          <li><span class="lista-texto"><small>Último dato final / preliminar</small></span><strong>${util.fmtFecha(ch.ultimo_final)} / ${ch.ultimo_preliminar ? util.fmtFecha(ch.ultimo_preliminar) : '—'}</strong></li>
          <li><span class="lista-texto"><small>Días en caché por origen</small></span><strong>${util.escapar(textoOrigenes)}</strong></li>
          ${filasFuentes}
          <li><span class="lista-texto"><small>Climatología mensual en caché</small></span><strong>${util.escapar(textoAnios)}</strong></li>
          <li><span class="lista-texto"><small>Tamaño de la caché / descargado del CHC</small></span><strong>${mb !== null ? util.fmtNum(mb, 1) + ' MB' : '—'} / ${descargados !== null ? util.fmtNum(descargados, 1) + ' MB' : '—'}</strong></li>
        </ul>
        ${errores.length ? `<div class="texto-pequeno texto-2 mt-12 mb-8">Errores recientes</div><ul class="lista">${errores.map((e) => `<li><span class="lista-texto"><small style="display:block">${util.escapar(e && e.momento ? util.fmtFechaHora(e.momento) : '')}</small><span style="white-space:normal">${util.escapar(e && typeof e === 'object' ? (e.mensaje || '') : String(e))}</span></span></li>`).join('')}</ul>` : ''}
      </div>`;
  }

  function kpi(etiqueta, valor, texto) {
    return `<div class="tarjeta kpi"><span class="kpi-etiqueta">${etiqueta}</span><span class="kpi-valor" style="font-size:26px">${valor}</span>${texto ? `<span class="kpi-texto">${texto}</span>` : ''}</div>`;
  }
  function kpiPequeno(etiqueta, valor) {
    return `<div class="kpi"><span class="kpi-etiqueta">${etiqueta}</span><span class="kpi-valor" style="font-size:20px">${valor}</span></div>`;
  }
  function fmtDuracion(s) {
    if (!util.esNumero(s)) return '—';
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h >= 24) return `${Math.floor(h / 24)} d ${h % 24} h`;
    return `${h} h ${m} min`;
  }

  SOME.registrar('admin', {
    titulo: 'Administración',
    icono: 'admin',
    grupo: 'Sistema',
    requiereRol: 'administrador',
    montar,
    desmontar,
  });
})();
