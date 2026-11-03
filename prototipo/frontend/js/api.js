/* ============================================================
   SOME — api.js
   Cliente HTTP de la API REST (/api). Agrega el token guardado en
   localStorage['some_token'], convierte los errores en Error(detalle)
   y, ante 401 fuera del ingreso, limpia la sesión y vuelve a #/acceso.
   Se expone como window.api y como SOME.api (lo hace app.js).
   ============================================================ */
(function () {
  'use strict';

  const BASE = '/api';
  const CLAVE_TOKEN = 'some_token';
  const CLAVE_USUARIO = 'some_usuario';

  function token() {
    try { return localStorage.getItem(CLAVE_TOKEN) || null; } catch (e) { return null; }
  }

  function establecerToken(t) {
    try {
      if (t) localStorage.setItem(CLAVE_TOKEN, t);
      else localStorage.removeItem(CLAVE_TOKEN);
    } catch (e) { /* almacenamiento no disponible */ }
  }

  function guardarUsuario(usuario) {
    try {
      if (usuario) localStorage.setItem(CLAVE_USUARIO, JSON.stringify(usuario));
      else localStorage.removeItem(CLAVE_USUARIO);
    } catch (e) { /* ignorar */ }
  }

  function usuarioGuardado() {
    try {
      const u = localStorage.getItem(CLAVE_USUARIO);
      return u ? JSON.parse(u) : null;
    } catch (e) { return null; }
  }

  function limpiarSesion() {
    establecerToken(null);
    guardarUsuario(null);
  }

  /** Normaliza la ruta: acepta «/auth/yo», «auth/yo» o «/api/auth/yo». */
  function url(ruta) {
    if (/^https?:\/\//i.test(ruta)) return ruta;
    if (ruta.startsWith(BASE + '/') || ruta === BASE) return ruta;
    return BASE + (ruta.startsWith('/') ? ruta : '/' + ruta);
  }

  function cabeceras(conCuerpo) {
    const h = { Accept: 'application/json' };
    if (conCuerpo) h['Content-Type'] = 'application/json; charset=utf-8';
    const t = token();
    if (t) h.Authorization = 'Bearer ' + t;
    return h;
  }

  /** Extrae el mensaje de error del cuerpo de la respuesta. */
  async function mensajeDeError(respuesta) {
    let detalle = '';
    try {
      const texto = await respuesta.text();
      if (texto) {
        try {
          const json = JSON.parse(texto);
          if (typeof json.detalle === 'string') detalle = json.detalle;
          else if (typeof json.detail === 'string') detalle = json.detail;
          else if (Array.isArray(json.detail)) detalle = json.detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
          else if (typeof json.mensaje === 'string') detalle = json.mensaje;
        } catch (e) {
          detalle = texto.length < 300 ? texto : '';
        }
      }
    } catch (e) { /* sin cuerpo */ }
    if (!detalle) {
      const porCodigo = {
        400: 'Solicitud incorrecta',
        401: 'No autorizado',
        403: 'No tiene permiso para esta acción',
        404: 'Recurso no encontrado',
        422: 'Los parámetros de la consulta no son válidos',
        500: 'Error interno del servidor',
        503: 'Servicio no disponible',
      };
      detalle = porCodigo[respuesta.status] || `Error ${respuesta.status}`;
    }
    return detalle;
  }

  function sesionExpirada() {
    limpiarSesion();
    if (window.SOME && typeof window.SOME.cerrarSesion === 'function') {
      window.SOME.cerrarSesion({ silencioso: true, motivo: 'expirada' });
    } else {
      location.hash = '#/acceso';
    }
  }

  /**
   * Petición genérica. Devuelve el JSON (o null si la respuesta está vacía).
   * Lanza Error(detalle) ante cualquier código distinto de 2xx.
   */
  async function peticion(metodo, ruta, cuerpo, opciones) {
    opciones = opciones || {};
    const config = { method: metodo, headers: cabeceras(cuerpo !== undefined && cuerpo !== null) };
    if (cuerpo !== undefined && cuerpo !== null) config.body = JSON.stringify(cuerpo);
    if (opciones.senal) config.signal = opciones.senal;

    let respuesta;
    try {
      respuesta = await fetch(url(ruta), config);
    } catch (e) {
      if (e && e.name === 'AbortError') throw e;
      throw new Error('No hay conexión con el servidor del SOME. Compruebe que el servicio esté en ejecución.');
    }

    if (respuesta.status === 401 && !/\/auth\/ingresar$/.test(url(ruta))) {
      const detalle = await mensajeDeError(respuesta);
      sesionExpirada();
      const err = new Error(detalle === 'No autorizado' ? 'La sesión ha expirado. Ingrese nuevamente.' : detalle);
      err.codigo = 401;
      throw err;
    }

    if (!respuesta.ok) {
      const err = new Error(await mensajeDeError(respuesta));
      err.codigo = respuesta.status;
      throw err;
    }

    if (respuesta.status === 204) return null;
    const texto = await respuesta.text();
    if (!texto) return null;
    try {
      return JSON.parse(texto);
    } catch (e) {
      return texto;
    }
  }

  function get(ruta, opciones) { return peticion('GET', ruta, null, opciones); }
  function post(ruta, cuerpo, opciones) { return peticion('POST', ruta, cuerpo === undefined ? {} : cuerpo, opciones); }
  function put(ruta, cuerpo, opciones) { return peticion('PUT', ruta, cuerpo === undefined ? {} : cuerpo, opciones); }
  function del(ruta, opciones) { return peticion('DELETE', ruta, null, opciones); }

  /** Nombre de archivo desde Content-Disposition, si existe. */
  function nombreDesdeCabecera(respuesta) {
    const cd = respuesta.headers.get('Content-Disposition') || '';
    const m2 = /filename\*=UTF-8''([^;]+)/i.exec(cd);
    if (m2) { try { return decodeURIComponent(m2[1].trim()); } catch (e) { /* seguir */ } }
    const m1 = /filename="?([^";]+)"?/i.exec(cd);
    return m1 ? m1[1].trim() : null;
  }

  /**
   * Descarga un archivo binario. Si `cuerpo` es null hace GET; si no, POST.
   * Usa util.descargarBlob y devuelve el nombre final.
   */
  async function descargar(ruta, cuerpo, nombreSugerido) {
    const metodo = cuerpo === undefined || cuerpo === null ? 'GET' : 'POST';
    const config = { method: metodo, headers: cabeceras(metodo === 'POST') };
    delete config.headers.Accept;
    if (metodo === 'POST') config.body = JSON.stringify(cuerpo);

    let respuesta;
    try {
      respuesta = await fetch(url(ruta), config);
    } catch (e) {
      throw new Error('No hay conexión con el servidor del SOME.');
    }
    if (respuesta.status === 401) {
      sesionExpirada();
      throw new Error('La sesión ha expirado. Ingrese nuevamente.');
    }
    if (!respuesta.ok) throw new Error(await mensajeDeError(respuesta));

    const blob = await respuesta.blob();
    const nombre = nombreDesdeCabecera(respuesta) || nombreSugerido || 'SOME_descarga';
    if (window.util && typeof window.util.descargarBlob === 'function') window.util.descargarBlob(blob, nombre);
    return nombre;
  }

  /** URL absoluta con token (para abrir un PDF en una pestaña nueva). */
  function urlConToken(ruta) {
    const u = url(ruta);
    const t = token();
    return t ? u + (u.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(t) : u;
  }

  window.api = {
    get,
    post,
    put,
    del,
    descargar,
    token,
    establecerToken,
    guardarUsuario,
    usuarioGuardado,
    limpiarSesion,
    url,
    urlConToken,
  };
})();
