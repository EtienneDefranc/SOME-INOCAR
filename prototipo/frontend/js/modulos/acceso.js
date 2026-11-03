/* ============================================================
   SOME — módulo Acceso (#/acceso)
   Inicio de sesión con usuarios de demostración.
   ============================================================ */
(function () {
  'use strict';
  const SOME = window.SOME;
  const util = window.util;

  // «enfoque» resume lo que cada rol muestra en la demostración
  const USUARIOS_DEMO = [
    { usuario: 'mvera', nombre: 'Dra. María Vera', rol: 'oceanografo', departamento: 'Oceanografía y Meteorología Marina', enfoque: 'Consultas, mapas y reportes' },
    { usuario: 'jloor', nombre: 'Ing. Jorge Loor', rol: 'analista', departamento: 'Oceanografía y Meteorología Marina', enfoque: 'Series, comparaciones y exportación' },
    { usuario: 'rcastro', nombre: 'CPFG Ricardo Castro', rol: 'revisor', departamento: 'Oceanografía y Meteorología Marina', enfoque: 'Boletines y vistas compartidas' },
    { usuario: 'admin', nombre: 'Administrador de TI', rol: 'administrador', departamento: 'Tecnologías de la Información', enfoque: 'Usuarios, catálogo y auditoría' },
  ];
  const CLAVE_DEMO = 'some2026';

  async function montar(contenedor) {
    contenedor.innerHTML = `
      <div class="acceso">
        <div class="tarjeta acceso-tarjeta">
          <div class="acceso-logo">
            <img src="img/logo-some.svg" alt="">
            <div>
              <h1>SOME</h1>
              <small>Sistema de Observación del Mar Equinoccial</small>
            </div>
          </div>
          <p class="texto-2 mb-16">Ingrese con su usuario institucional para consultar las fuentes oceanográficas y meteorológicas del INOCAR.</p>
          <div class="mb-16"><span class="insignia" id="acceso-insignia" title="Consultando al servidor qué fuentes sirven datos reales y cuáles responden con su simulador de respaldo.">Prototipo demostrativo: verificando la procedencia de los datos…</span></div>

          <section class="ingreso-rapido" aria-labelledby="ingreso-rapido-titulo">
            <h2 id="ingreso-rapido-titulo">Ingreso directo por tipo de usuario</h2>
            <div class="usuarios-demo" id="usuarios-demo"></div>
          </section>

          <div class="separador-texto"><span>o ingrese con usuario y contraseña</span></div>

          <form id="formulario-acceso" novalidate>
            <div class="campo">
              <label for="acceso-usuario">Usuario</label>
              <input id="acceso-usuario" name="usuario" type="text" autocomplete="username" autocapitalize="off" spellcheck="false" required placeholder="p. ej. mvera">
            </div>
            <div class="campo">
              <label for="acceso-clave">Contraseña</label>
              <input id="acceso-clave" name="clave" type="password" autocomplete="current-password" required placeholder="••••••••">
            </div>
            <div id="acceso-error" class="alerta alerta-peligro oculto" role="alert"></div>
            <button type="submit" class="boton boton-primario" id="acceso-ingresar" style="width:100%">Ingresar</button>
          </form>
          <p class="texto-2 texto-pequeno mt-8 mb-0">Contraseña de los usuarios de demostración: <code>${CLAVE_DEMO}</code>.</p>

          <div class="acceso-pie">INOCAR · ESPOL · Equipo T5</div>
        </div>
      </div>`;

    const form = contenedor.querySelector('#formulario-acceso');
    const campoUsuario = contenedor.querySelector('#acceso-usuario');
    const campoClave = contenedor.querySelector('#acceso-clave');
    const error = contenedor.querySelector('#acceso-error');
    const boton = contenedor.querySelector('#acceso-ingresar');
    const lista = contenedor.querySelector('#usuarios-demo');

    // Envía las credenciales; mientras tanto deshabilita todos los botones de ingreso
    // y muestra el anillo de carga en el que se pulsó.
    async function ingresar(usuario, clave, botonPulsado) {
      const botones = contenedor.querySelectorAll('#acceso-ingresar, .usuario-demo');
      const contenidoOriginal = botonPulsado.innerHTML;
      botones.forEach((b) => { b.disabled = true; });
      botonPulsado.classList.add('ingresando');
      if (botonPulsado === boton) botonPulsado.innerHTML = '<span class="anillo" aria-hidden="true"></span> Ingresando…';
      else botonPulsado.querySelector('.usuario-demo-flecha').innerHTML = '<span class="anillo"></span>';
      error.classList.add('oculto');
      try {
        const r = await SOME.api.post('/auth/ingresar', { usuario, clave });
        if (!r || !r.token || !r.usuario) throw new Error('Respuesta inesperada del servidor.');
        util.toast(`Le damos la bienvenida, ${r.usuario.nombre || r.usuario.usuario}.`, 'exito');
        await SOME.despuesDeIngresar(r.token, r.usuario);
      } catch (e) {
        error.textContent = e && e.message ? e.message : 'No se pudo iniciar la sesión.';
        error.classList.remove('oculto');
        if (botonPulsado === boton) campoClave.select();
      } finally {
        botones.forEach((b) => { b.disabled = false; });
        botonPulsado.classList.remove('ingresando');
        botonPulsado.innerHTML = contenidoOriginal;
      }
    }

    USUARIOS_DEMO.forEach((u) => {
      const b = util.el(`
        <button type="button" class="usuario-demo" data-usuario="${u.usuario}"
                aria-label="Ingresar como ${util.escapar(SOME.nombreRol(u.rol))}: ${util.escapar(u.nombre)}">
          <span class="avatar">${util.escapar(util.iniciales(u.nombre))}</span>
          <span class="usuario-demo-texto">
            <strong>${util.escapar(SOME.nombreRol(u.rol))}</strong>
            <small>${util.escapar(u.nombre)} · <code>${u.usuario}</code></small>
            <small class="usuario-demo-enfoque">${util.escapar(u.enfoque)}</small>
          </span>
          <span class="usuario-demo-flecha" aria-hidden="true">${util.iconoFi('arrow-small-right')}</span>
        </button>`);
      b.addEventListener('click', () => {
        campoUsuario.value = u.usuario;
        campoClave.value = CLAVE_DEMO;
        ingresar(u.usuario, CLAVE_DEMO, b);
      });
      lista.appendChild(b);
    });

    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const usuario = campoUsuario.value.trim();
      const clave = campoClave.value;
      if (!usuario || !clave) {
        error.textContent = 'Ingrese el usuario y la contraseña.';
        error.classList.remove('oculto');
        return;
      }
      ingresar(usuario, clave, boton);
    });

    setTimeout(() => campoUsuario.focus(), 30);
    actualizarInsignia(contenedor);
  }

  // Fuentes del SOME en el orden del catálogo, con su nombre corto y lo que aporta (para el title)
  const FUENTES = [
    { id: 'noaa_oisst', nombre: 'NOAA OISST', aporte: 'la temperatura superficial del mar (NOAA OISST v2.1, NOAA/NCEI)' },
    { id: 'copernicus_phy', nombre: 'Copernicus Marine', aporte: 'las variables físicas del océano (análisis diario de Copernicus Marine)' },
    { id: 'chirps_v3', nombre: 'CHIRPS v3', aporte: 'la precipitación (CHIRPS v3, Climate Hazards Center, UC Santa Barbara)' },
  ];
  function unirY(lista) {
    return lista.length > 1 ? `${lista.slice(0, -1).join(', ')} y ${lista[lista.length - 1]}` : (lista[0] || '');
  }

  /**
   * La insignia de procedencia de la pantalla de acceso se ajusta a las fuentes reales del momento
   * (`GET /api/salud`, pública): con las tres reales, verde («Datos reales: NOAA OISST, Copernicus Marine y
   * CHIRPS v3»); si alguna responde con su simulador, ámbar y nombra cuáles. Si la consulta falla, queda el
   * texto neutro inicial. El texto visible es corto para que quepa en la tarjeta (a 1280 × 720, en una
   * línea); el detalle va en el title.
   */
  async function actualizarInsignia(contenedor) {
    try {
      const salud = await SOME.api.get('/salud');
      const nodo = contenedor.querySelector('#acceso-insignia');
      if (!nodo || !salud || !Array.isArray(salud.fuentes_reales)) return;
      const reales = FUENTES.filter((f) => salud.fuentes_reales.includes(f.id));
      const simuladas = FUENTES.filter((f) => !salud.fuentes_reales.includes(f.id));
      nodo.classList.remove('insignia-real', 'insignia-simulado');
      if (!simuladas.length) {
        nodo.classList.add('insignia-real');
        nodo.textContent = `Datos reales: ${unirY(reales.map((f) => f.nombre))}`;
        nodo.title = `${unirY(reales.map((f) => f.aporte)).replace(/^./, (c) => c.toUpperCase())} son datos reales; el índice Niño 1+2 se calcula con NOAA OISST en la región oficial (0–10° S, 90–80° O). Si una fuente no responde y su caché local no tiene el dato, la respuesta usa el simulador del prototipo y lo señala.`;
        return;
      }
      nodo.classList.add('insignia-simulado');
      nodo.textContent = reales.length
        ? `Demostración: ${unirY(reales.map((f) => f.nombre))} ${reales.length === 1 ? 'real' : 'reales'}; ${unirY(simuladas.map((f) => f.nombre))} ${simuladas.length === 1 ? 'simulado' : 'simulados'}`
        : 'Demostración: datos simulados';
      nodo.title = `${reales.length ? `${unirY(reales.map((f) => f.aporte)).replace(/^./, (c) => c.toUpperCase())} ${reales.length === 1 ? 'es dato real' : 'son datos reales'}; ` : ''}${unirY(simuladas.map((f) => f.aporte))} ${simuladas.length === 1 ? 'responde' : 'responden'} ahora con el simulador del prototipo (sin acceso ni datos en la caché local), señalado como tal.`;
    } catch (e) { /* se conserva el texto neutro inicial */ }
  }

  SOME.registrar('acceso', {
    titulo: 'Ingresar',
    icono: 'usuario',
    grupo: '',
    montar,
  });
})();
