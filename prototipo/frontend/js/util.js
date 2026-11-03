/* ============================================================
   SOME — util.js
   Utilidades compartidas: formato es-EC, avisos (toast), modales,
   descarga de archivos, debounce, creación de nodos y estados de carga.
   Se expone como window.util y también como SOME.util (lo hace app.js).
   ============================================================ */
(function () {
  'use strict';

  const ESPACIO_FINO = ' '; // espacio fino para separar miles

  /** Devuelve true si el valor es un número finito. */
  function esNumero(x) {
    return typeof x === 'number' && Number.isFinite(x);
  }

  /** Convierte una cadena ISO (aaaa-mm-dd o aaaa-mm-ddThh:mm:ss) en Date sin corrimiento de zona. */
  function aFecha(iso) {
    if (iso instanceof Date) return Number.isNaN(iso.getTime()) ? null : iso;
    if (typeof iso !== 'string' || !iso) return null;
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim());
    if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function dosDigitos(n) {
    return String(n).padStart(2, '0');
  }

  /** dd/mm/aaaa */
  function fmtFecha(iso) {
    const d = aFecha(iso);
    if (!d) return '—';
    return `${dosDigitos(d.getDate())}/${dosDigitos(d.getMonth() + 1)}/${d.getFullYear()}`;
  }

  /** dd/mm/aaaa HH:mm */
  function fmtFechaHora(iso) {
    const d = aFecha(iso);
    if (!d) return '—';
    return `${fmtFecha(d)} ${dosDigitos(d.getHours())}:${dosDigitos(d.getMinutes())}`;
  }

  /** Fecha a cadena ISO corta (aaaa-mm-dd) para enviar a la API. */
  function aIso(fecha) {
    const d = aFecha(fecha) || new Date();
    return `${d.getFullYear()}-${dosDigitos(d.getMonth() + 1)}-${dosDigitos(d.getDate())}`;
  }

  /** Número con punto decimal (RNF-01) y espacio fino para miles. null/NaN → «—». */
  function fmtNum(x, decimales) {
    if (decimales === undefined || decimales === null) decimales = 1;
    if (typeof x === 'string' && x.trim() !== '' && !Number.isNaN(Number(x))) x = Number(x);
    if (!esNumero(x)) return '—';
    const negativo = x < 0;
    const fijo = Math.abs(x).toFixed(decimales);
    let [entero, dec] = fijo.split('.');
    entero = entero.replace(/\B(?=(\d{3})+(?!\d))/g, ESPACIO_FINO);
    return (negativo ? '−' : '') + entero + (dec ? '.' + dec : '');
  }

  /** Anomalía con signo explícito: «+1.8 °C», «−0.4 PSU», «0.0 mm/día». */
  function fmtAnomalia(x, unidad, decimales) {
    if (!esNumero(x)) return '—';
    const d = decimales === undefined ? 1 : decimales;
    const cuerpo = fmtNum(Math.abs(x), d);
    const cero = Number(Math.abs(x).toFixed(d)) === 0;
    const signo = cero ? '' : (x > 0 ? '+' : '−');
    return `${signo}${cuerpo}${unidad ? ' ' + unidad : ''}`;
  }

  /** Escapa texto para insertarlo en HTML. */
  function escapar(texto) {
    if (texto === null || texto === undefined) return '';
    return String(texto)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /** Crea un nodo DOM a partir de una plantilla de cadena. Devuelve el primer elemento. */
  function el(html) {
    const plantilla = document.createElement('template');
    plantilla.innerHTML = String(html).trim();
    return plantilla.content.firstElementChild;
  }

  /** Iconos SVG sencillos para avisos y estados. */
  const ICONOS = {
    info: '<svg class="icono" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    exito: '<svg class="icono" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5 4.5-5"/></svg>',
    error: '<svg class="icono" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/></svg>',
    aviso: '<svg class="icono" viewBox="0 0 24 24"><path d="M12 3l9.5 17H2.5L12 3z"/><path d="M12 10v4M12 17h.01"/></svg>',
    vacio: '<svg class="icono" viewBox="0 0 24 24"><path d="M3 7l9-4 9 4-9 4-9-4z"/><path d="M3 7v10l9 4 9-4V7"/><path d="M12 11v10"/></svg>',
  };

  /* ---------- Toast ---------- */
  function contenedorToasts() {
    let c = document.getElementById('toasts');
    if (!c) {
      c = document.createElement('div');
      c.id = 'toasts';
      c.className = 'toasts';
      c.setAttribute('aria-live', 'polite');
      document.body.appendChild(c);
    }
    return c;
  }

  /**
   * Muestra un aviso breve.
   * @param {string} mensaje
   * @param {'info'|'exito'|'error'|'aviso'} tipo
   */
  function toast(mensaje, tipo) {
    tipo = ['info', 'exito', 'error', 'aviso'].includes(tipo) ? tipo : 'info';
    const nodo = el(`
      <div class="toast toast-${tipo}" role="${tipo === 'error' ? 'alert' : 'status'}">
        ${ICONOS[tipo]}
        <div class="toast-texto">${escapar(mensaje)}</div>
        <button class="cerrar" type="button" aria-label="Cerrar aviso">×</button>
      </div>`);
    const cerrar = () => { if (nodo.parentNode) nodo.parentNode.removeChild(nodo); };
    nodo.querySelector('.cerrar').addEventListener('click', cerrar);
    contenedorToasts().appendChild(nodo);
    setTimeout(cerrar, tipo === 'error' ? 7000 : 4000);
    return nodo;
  }

  /* ---------- Modal ---------- */
  /**
   * Abre un modal.
   * @param {{titulo:string, contenido:string|Node, acciones?:Array<{texto:string, clase?:string, accion?:function, cerrar?:boolean}>, ancho?:'normal'|'ancho', alCerrar?:function}} opciones
   * @returns {{cerrar:function, elemento:HTMLElement, cuerpo:HTMLElement}}
   */
  function modal(opciones) {
    opciones = opciones || {};
    const fondo = el(`
      <div class="modal-fondo" role="dialog" aria-modal="true" aria-label="${escapar(opciones.titulo || 'Diálogo')}">
        <div class="modal ${opciones.ancho === 'ancho' ? 'modal-ancho' : ''}">
          <div class="modal-cabecera">
            <h3>${escapar(opciones.titulo || '')}</h3>
            <button class="boton boton-icono boton-fantasma" type="button" data-cerrar aria-label="Cerrar">
              <svg class="icono" viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="modal-cuerpo"></div>
          <div class="modal-acciones"></div>
        </div>
      </div>`);
    const cuerpo = fondo.querySelector('.modal-cuerpo');
    const zonaAcciones = fondo.querySelector('.modal-acciones');

    if (opciones.contenido instanceof Node) cuerpo.appendChild(opciones.contenido);
    else cuerpo.innerHTML = opciones.contenido || '';

    let cerrado = false;
    function cerrar() {
      if (cerrado) return;
      cerrado = true;
      document.removeEventListener('keydown', alTeclear);
      if (fondo.parentNode) fondo.parentNode.removeChild(fondo);
      if (typeof opciones.alCerrar === 'function') opciones.alCerrar();
    }
    function alTeclear(ev) {
      if (ev.key === 'Escape') { ev.preventDefault(); cerrar(); }
    }

    const acciones = Array.isArray(opciones.acciones) && opciones.acciones.length
      ? opciones.acciones
      : [{ texto: 'Cerrar', clase: 'boton-secundario', cerrar: true }];

    acciones.forEach((a) => {
      const b = el(`<button type="button" class="boton ${a.clase || ''}">${escapar(a.texto)}</button>`);
      b.addEventListener('click', async () => {
        if (typeof a.accion === 'function') {
          b.disabled = true;
          try {
            const r = await a.accion(cerrar);
            if (r === false) { b.disabled = false; return; }
          } catch (e) {
            b.disabled = false;
            toast(e && e.message ? e.message : 'Ocurrió un error', 'error');
            return;
          }
        }
        if (a.cerrar !== false) cerrar();
      });
      zonaAcciones.appendChild(b);
    });
    if (!acciones.length) zonaAcciones.remove();

    fondo.querySelector('[data-cerrar]').addEventListener('click', cerrar);
    fondo.addEventListener('mousedown', (ev) => { if (ev.target === fondo) cerrar(); });
    document.addEventListener('keydown', alTeclear);
    document.body.appendChild(fondo);

    // Enfoca el primer control del cuerpo o el primer botón
    setTimeout(() => {
      const primero = cuerpo.querySelector('input, select, textarea, button') || zonaAcciones.querySelector('button');
      if (primero) primero.focus();
    }, 20);

    return { cerrar, elemento: fondo, cuerpo };
  }

  /** Confirmación sencilla. Devuelve una promesa booleana. */
  function confirmar(mensaje, opciones) {
    opciones = opciones || {};
    return new Promise((resolver) => {
      let respondido = false;
      modal({
        titulo: opciones.titulo || 'Confirmar',
        contenido: `<p>${escapar(mensaje)}</p>`,
        alCerrar: () => { if (!respondido) { respondido = true; resolver(false); } },
        acciones: [
          { texto: opciones.cancelar || 'Cancelar', clase: 'boton-secundario', accion: () => { respondido = true; resolver(false); } },
          { texto: opciones.aceptar || 'Aceptar', clase: opciones.peligro ? 'boton-peligro' : 'boton-primario', accion: () => { respondido = true; resolver(true); } },
        ],
      });
    });
  }

  /** Descarga un Blob con el nombre indicado. */
  function descargarBlob(blob, nombre) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = nombre || 'descarga';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }, 400);
  }

  /** Retrasa la ejecución hasta que pasen `ms` sin nuevas llamadas. */
  function debounce(fn, ms) {
    let temporizador = null;
    return function () {
      const args = arguments;
      const contexto = this;
      clearTimeout(temporizador);
      temporizador = setTimeout(() => fn.apply(contexto, args), ms || 250);
    };
  }

  /** Reemplaza el contenido del contenedor por un estado de carga. */
  function cargando(contenedor, mensaje) {
    if (!contenedor) return;
    contenedor.innerHTML = `
      <div class="cargando" role="status" aria-live="polite">
        <span class="anillo" aria-hidden="true"></span>
        <span>${escapar(mensaje || 'Cargando…')}</span>
      </div>`;
  }

  /** Icono de Flaticon UIcons, estilo regular redondeado («fi-rr-…»); decorativo, oculto a lectores de pantalla. */
  function iconoFi(nombre, clase) {
    return `<i class="fi fi-rr-${nombre}${clase ? ' ' + clase : ''}" aria-hidden="true"></i>`;
  }

  /** Estado vacío estándar (icono + título + texto). Devuelve HTML. */
  function vacioHtml(titulo, texto, icono) {
    return `
      <div class="vacio">
        ${ICONOS[icono] || ICONOS.vacio}
        <strong>${escapar(titulo || 'Sin datos')}</strong>
        ${texto ? `<p>${escapar(texto)}</p>` : ''}
      </div>`;
  }

  /** Estado de error con botón de reintento. */
  function errorHtml(mensaje, idReintento) {
    return `
      <div class="vacio">
        ${ICONOS.error}
        <strong>No se pudo cargar la información</strong>
        <p>${escapar(mensaje || 'Ocurrió un error inesperado.')}</p>
        ${idReintento ? `<button type="button" class="boton boton-secundario" data-reintentar="${escapar(idReintento)}">Reintentar</button>` : ''}
      </div>`;
  }

  /** Iniciales de un nombre («Dra. María Vera» → «MV»). */
  function iniciales(nombre) {
    if (!nombre) return '?';
    const partes = String(nombre)
      .replace(/\b(Dra?|Ing|Lic|CPFG|CPNV|MSc|PhD)\.?\s*/gi, '')
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    if (!partes.length) return String(nombre).slice(0, 2).toUpperCase();
    return (partes[0][0] + (partes[1] ? partes[1][0] : '')).toUpperCase();
  }

  /** Nombre del mes en español (1..12), opcionalmente abreviado. */
  const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
  function nombreMes(n, corto) {
    const m = MESES[(Number(n) - 1 + 12) % 12] || '';
    return corto ? m.slice(0, 3) : m;
  }

  /** Suma días a una fecha y devuelve ISO corto. */
  function sumarDias(fecha, dias) {
    const d = aFecha(fecha) || new Date();
    const r = new Date(d.getFullYear(), d.getMonth(), d.getDate() + Number(dias || 0));
    return aIso(r);
  }

  window.util = {
    fmtFecha,
    fmtFechaHora,
    fmtNum,
    fmtAnomalia,
    toast,
    modal,
    confirmar,
    descargarBlob,
    debounce,
    el,
    cargando,
    // extras de apoyo
    escapar,
    aFecha,
    aIso,
    sumarDias,
    iconoFi,
    vacioHtml,
    errorHtml,
    iniciales,
    nombreMes,
    esNumero,
    ICONOS,
  };
})();
