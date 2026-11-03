"""Prueba de humo del prototipo SOME: recorre todos los endpoints de la sección 7.

Uso (desde `prototipo-some/`): `python -m backend.pruebas.prueba_humo`

- Usa una carpeta temporal para los JSON de `datos/` (`SOME_RUTA_DATOS`: usuarios,
  vistas, trabajos y reportes), pero las cachés de NOAA OISST (`datos/noaa/`), de CHIRPS
  (`datos/chirps/`) y de Copernicus Marine (`datos/copernicus/`) son las REALES,
  compartidas con el servidor en marcha: `SOME_RUTA_DATOS` no las redirige. Para aislarlas
  (y siempre que se simulen fallas de red, un proxy roto o la falta de credenciales),
  apunte `SOME_RUTA_NOAA`, `SOME_RUTA_COPERNICUS` y `SOME_RUTA_CHIRPS` a una COPIA de esas
  carpetas: cada `estado.json` se comparte entre procesos y una caída registrada por la
  prueba dejaría al servidor real «sin conexión» (Copernicus, de 5 a 30 min; NOAA, en
  pausa 10 min).
- No arranca la sincronización de NOAA, CHIRPS ni Copernicus Marine
  (`SOME_NOAA_SIN_SINCRONIZACION=1`, `SOME_CHIRPS_SIN_SINCRONIZACION=1`,
  `SOME_COPERNICUS_SIN_SINCRONIZACION=1`) para no escribir verificaciones en el estado que
  muestra el servidor.
- NOAA OISST se lee por defecto SOLO de su caché (`SOME_NOAA_SIN_RED=1`, en solo lectura:
  no descarga ni registra nada en `datos/noaa/`): NOAA PSL limita el ritmo por IP (HTTP
  429) y ese límite lo comparten el servidor de la demostración y la preparación. Con la
  caché preparada (`python -m backend.herramientas.preparar_noaa`) todo es real; para
  probar con red, `SOME_NOAA_SIN_RED=0` (se acepta entonces el estado «degradada»).
- Emplea `fastapi.testclient.TestClient` si está disponible (requiere `httpx`,
  que no forma parte de las bibliotecas permitidas); si no, levanta uvicorn en
  un hilo del mismo proceso y usa `urllib` con la misma interfaz.
- Ingresa con cada usuario de demostración, recorre todos los endpoints con
  cuerpos válidos, genera un reporte de cada plantilla y espera a que esté
  listo, exporta CSV/PNG/NetCDF y verifica 401/403/422 en casos inválidos.
- Mide que un mapa de un día responda en < 2 s y una serie de 12 meses en < 3 s.
- Verifica la procedencia honesta de los datos: CHIRPS v3 es real (mapa, serie,
  exportaciones CSV/NetCDF y widgets de precipitación con procedencia «real»).
- NOAA OISST v2.1 se acepta real o simulado según su modo, que se lee de
  `GET /api/fuentes/estado` (bloque `noaa`: `modo`, `acceso.disponible` y la caché):
  · modo «real» (caché local o red; el normal): mapa, series, climatología (1981-2010 con
    periodo efectivo «1982-2010 en enero–agosto»), «mismo mes», exportaciones e índice
    Niño 1+2 semanal con procedencia «real», insignia «Datos reales · NOAA OISST» y el DOI
    10.25921/RE9P-PT57 en el crédito; la fecha de corte del sistema (catálogo, salud) es la
    de NOAA (el último día de su caché) y todas las fechas de NOAA son relativas a ella; con
    Copernicus también real, todo el sistema es «real» (`datos_simulados=false`), NOAA
    frente a Copernicus es «real» (con correlación alta) y el boletín ERFEN es «real». El
    caso «mixto» deliberado (SST frente a precipitación con NOAA simulado) se comprueba en
    un proceso aparte con `SOME_NOAA_MODO=simulado`;
  · modo «simulado» (`SOME_NOAA_MODO=simulado`, o sin red y sin caché): las expectativas
    anteriores (fecha de corte simulada 27/10/2026, NOAA «simulado», comparación con
    Copernicus y boletín «mixtos»);
  · modo «real» sin red y con la caché incompleta: lo que no sea «real» trae `avisos`.
- Copernicus Marine se acepta real o simulado según su modo, que se lee de
  `GET /api/fuentes/estado` (bloque `copernicus`: `modo` y `acceso.disponible`):
  · modo «simulado» (sin credenciales ni caché, o SOME_COPERNICUS_MODO=simulado): sus
    consultas son «simulado», su estado «sin conexión» con el motivo (u «operativa» si el
    modo simulado es forzado) y la única fuente real es CHIRPS;
  · modo «real» con acceso: sus consultas son «real» (crédito con el DOI 10.48670/moi-00016 y
    periodo de referencia efectivo en la serie y su CSV), el informe de Copernicus es «real» y
    las fuentes reales incluyen Copernicus;
  · modo «real» sin acceso (solo caché): cada respuesta es «real», «mixto» o «simulado», y si
    no es «real» trae `avisos` (nunca se etiqueta real un respaldo simulado).
  Las fechas de Copernicus son relativas a su fecha de corte C (perfil en C, cortina
  C−56…C, comparación C−364…C, informe C−29…C, rendimiento C−364…C).

CHIRPS v3 se sirve desde la caché local `datos/chirps/`. Para correr sin internet:
`SOME_CHIRPS_SIN_RED=1 python -m backend.pruebas.prueba_humo` (requiere haber preparado
la caché con `python -m backend.herramientas.preparar_chirps`). NOAA OISST: prepare su
caché con `python -m backend.herramientas.preparar_noaa` (unos 20 min con la caché vacía);
para las expectativas del modo simulado, `SOME_NOAA_MODO=simulado`. Copernicus Marine: con
credenciales, prepare antes su caché con `python -m backend.herramientas.preparar_copernicus`
(las consultas que deban descargar mucho pueden responder 503 «sigue descargándose»); sin
internet, `SOME_COPERNICUS_SIN_RED=1` sirve solo la caché.

Termina con código 0 si todo pasó y 1 si hubo fallas.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# La consola de Windows suele usar cp1252: forzamos UTF-8 para imprimir tildes y «→».
for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, "reconfigure"):
        try:
            _flujo.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, AttributeError):
            pass

_CARPETA_TEMPORAL = tempfile.mkdtemp(prefix="some_prueba_")
os.environ["SOME_RUTA_DATOS"] = _CARPETA_TEMPORAL
# La caché de CHIRPS (`datos/chirps/`) es la real y la comparte el servidor en marcha: sin esto, el
# ciclo de sincronización de la prueba registra una verificación del CHC (medida con el proceso
# cargado) en el estado que muestra el servidor. Las consultas siguen llenando bajo demanda.
os.environ.setdefault("SOME_CHIRPS_SIN_SINCRONIZACION", "1")
# Lo mismo con Copernicus Marine: la prueba no arranca su sincronización (las consultas siguen llenando
# su caché bajo demanda si hay acceso).
os.environ.setdefault("SOME_COPERNICUS_SIN_SINCRONIZACION", "1")
# NOAA OISST: sin sincronización y, por defecto, solo su caché (en solo lectura). NOAA PSL limita el ritmo por IP con
# HTTP 429 y ese límite lo comparten el servidor de la demostración y `preparar_noaa`; además, un fallo de una consulta
# dejaría a NOAA en pausa 10 min en el `estado.json` que comparte con el servidor. `SOME_NOAA_SIN_RED=0` prueba con red.
os.environ.setdefault("SOME_NOAA_SIN_SINCRONIZACION", "1")
os.environ.setdefault("SOME_NOAA_SIN_RED", "1")

from backend import almacen, cache, trabajos  # noqa: E402
from backend import configuracion as cfg  # noqa: E402
from backend.app import app  # noqa: E402

SIN_RED = os.environ.get("SOME_CHIRPS_SIN_RED", "").strip().lower() in ("1", "true", "si", "sí", "yes")
INSIGNIA_REALES_COPERNICUS = getattr(cfg, "INSIGNIA_REALES_COPERNICUS", "Datos reales · Copernicus Marine")
INSIGNIA_REALES_NOAA = getattr(cfg, "INSIGNIA_REALES_NOAA", "Datos reales · NOAA OISST")
FECHA_CORTE_SIMULADA = getattr(cfg, "FECHA_CORTE_SIMULADA", date(2026, 10, 27))
DOI_NOAA = "10.25921/RE9P-PT57"
PERIODO_EFECTIVO_1981_2010 = "1982-2010 en enero–agosto"     # OISST empieza en 09/1981


def insignia_de(procedencia: str | None, insignia_real: str) -> str:
    """Insignia que corresponde a la procedencia de una respuesta de una sola fuente."""
    return {"real": insignia_real, "simulado": cfg.INSIGNIA_SIMULADOS}.get(procedencia, cfg.INSIGNIA_MIXTOS)


class ModoNOAA:
    """Expectativas de las consultas de NOAA OISST según su modo (leído de /api/fuentes/estado, bloque «noaa»)."""

    def __init__(self, estado: dict):
        bloque = estado.get("noaa") if isinstance(estado.get("noaa"), dict) else {}
        self.bloque = bloque
        self.modo = bloque.get("modo") or ("real" if estado.get("datos_reales") else "simulado")
        # Sin bloque «noaa» (conector simulado anterior) equivale al modo simulado forzado
        self.configurado = bloque.get("modo_configurado") or ("auto" if bloque else "simulado")
        self.acceso = bool((bloque.get("acceso") or {}).get("disponible"))
        self.motivo = str((bloque.get("acceso") or {}).get("motivo") or "")
        self.sin_red = bool(estado.get("modo_sin_red"))
        self.corte = date.fromisoformat(estado["fecha_corte"])
        cache_noaa = bloque.get("cache") or {}
        clim = bloque.get("climatologia") or {}
        # Caché completa: todos los días hasta la fecha de corte, las tres climatologías mensuales y la diaria de la
        # región Niño 1+2. Con ella toda consulta dentro del periodo es «real» aunque no haya red.
        self.cache_completa = bool(cache_noaa.get("dias_finales")) and cache_noaa.get("dias_faltantes") == 0 \
            and len(clim.get("periodos_en_cache") or []) >= int(clim.get("total") or 3) \
            and bool(clim.get("ltm_diaria_nino12"))

    @property
    def real(self) -> bool:
        return self.modo == "real"

    def procedencias(self) -> tuple[str, ...]:
        """Procedencias aceptables de una consulta solo de NOAA."""
        if not self.real:
            return ("simulado",)
        if self.acceso or self.cache_completa:
            return ("real",)
        return ("real", "mixto", "simulado")       # caché incompleta y sin red: respaldo con aviso

    def coherente(self, r: dict) -> bool:
        """La procedencia de una respuesta de NOAA es la esperada y, si no es «real» en modo real, trae avisos."""
        p = r.get("procedencia")
        if p not in self.procedencias():
            return False
        if self.real and p != "real" and not r.get("avisos"):
            return False
        if p == "real":
            fuente = r.get("fuente") or {}
            return fuente.get("datos_reales") is True and DOI_NOAA in str(fuente.get("credito", ""))
        return True

    def describir(self) -> str:
        if not self.bloque:
            return "conector simulado (sin bloque «noaa» en el estado)"
        cache_noaa = self.bloque.get("cache") or {}
        return (f"modo «{self.modo}» (configurado «{self.configurado}»), acceso "
                f"{'disponible' if self.acceso else 'no disponible'}{f': {self.motivo}' if self.motivo else ''}; "
                f"corte {cfg.fmt_fecha(self.corte)}; caché {'completa' if self.cache_completa else 'incompleta'} "
                f"({cache_noaa.get('dias_finales')} días finales, {cache_noaa.get('dias_preliminares')} preliminares, "
                f"{cache_noaa.get('dias_faltantes')} faltantes)")


class ModoCopernicus:
    """Expectativas de las consultas de Copernicus Marine según su modo (leído de /api/fuentes/estado)."""

    def __init__(self, estado: dict):
        bloque = estado.get("copernicus") if isinstance(estado.get("copernicus"), dict) else {}
        self.bloque = bloque
        self.modo = bloque.get("modo") or ("real" if estado.get("datos_reales") else "simulado")
        # Sin bloque «copernicus» (conector simulado anterior) equivale al modo simulado forzado
        self.configurado = bloque.get("modo_configurado") or ("auto" if bloque else "simulado")
        self.acceso = bool((bloque.get("acceso") or {}).get("disponible"))
        self.motivo = str((bloque.get("acceso") or {}).get("motivo") or "")
        self.corte = date.fromisoformat(estado["fecha_corte"])

    @property
    def real(self) -> bool:
        return self.modo == "real"

    def procedencias(self) -> tuple[str, ...]:
        """Procedencias aceptables de una consulta solo de Copernicus."""
        if not self.real:
            return ("simulado",)
        if self.acceso:
            return ("real",)
        return ("real", "mixto", "simulado")       # solo caché: lo que no esté, respaldo con aviso

    def coherente(self, r: dict) -> bool:
        """La procedencia de una respuesta de Copernicus es la esperada y, si no es «real» en modo real,
        trae avisos (nunca se etiqueta real un respaldo simulado)."""
        p = r.get("procedencia")
        if p not in self.procedencias():
            return False
        if self.real and p != "real" and not r.get("avisos"):
            return False
        if p == "real":
            fuente = r.get("fuente") or {}
            return fuente.get("datos_reales") is True and "10.48670/moi-00016" in str(fuente.get("credito", ""))
        return True

    def describir(self) -> str:
        if not self.bloque:
            return "conector simulado (sin bloque «copernicus» en el estado)"
        return (f"modo «{self.modo}» (configurado «{self.configurado}»), acceso "
                f"{'disponible' if self.acceso else 'no disponible'}{f': {self.motivo}' if self.motivo else ''}; "
                f"corte {cfg.fmt_fecha(self.corte)}")


def fuentes_reales_esperadas(noaa: ModoNOAA, cop: ModoCopernicus) -> list[str]:
    """Fuentes con datos reales, en el orden del catálogo (NOAA OISST, Copernicus Marine, CHIRPS v3)."""
    return [f for f, real in (("noaa_oisst", noaa.real), ("copernicus_phy", cop.real), ("chirps_v3", True)) if real]


def un_anio_antes(d: date) -> date:
    """La misma fecha un año antes (el 29 de febrero pasa al 28)."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def inicio_12_meses(corte: date) -> date:
    """Primer día del mes 11 meses antes del de `corte`: la serie mensual hasta `corte` tiene 12 meses."""
    if corte.month == 12:
        return date(corte.year, 1, 1)
    return date(corte.year - 1, corte.month + 1, 1)


def comparacion_mixta_aparte(cuerpo: dict) -> dict:
    """Ejecuta `motor.consulta_comparar(cuerpo)` en un proceso aparte con `SOME_NOAA_MODO=simulado` (el interruptor
    es del proceso entero: aquí no puede forzarse sin afectar a los reportes en curso) y devuelve su procedencia."""
    codigo_py = (
        "import json, sys\n"
        "from backend import motor\n"
        "r = motor.consulta_comparar(json.loads(sys.argv[1]))\n"
        "print(json.dumps({'procedencia': r.get('procedencia'), 'datos_simulados': r.get('datos_simulados'),\n"
        "                  'mismas_unidades': r.get('mismas_unidades'),\n"
        "                  'solo_correlacion': (r.get('diferencia') or {}).get('solo_correlacion'),\n"
        "                  'a': (r.get('a') or {}).get('procedencia'), 'b': (r.get('b') or {}).get('procedencia')}))\n")
    entorno = dict(os.environ, SOME_NOAA_MODO="simulado", PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run([sys.executable, "-c", codigo_py, json.dumps(cuerpo)], cwd=str(RAIZ), env=entorno,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": str(e)}
    lineas = [ln for ln in (p.stdout or "").splitlines() if ln.startswith("{")]
    if p.returncode != 0 or not lineas:
        return {"error": f"código {p.returncode}: {(p.stderr or p.stdout or '').strip()[-300:]}"}
    return json.loads(lineas[-1])

CLAVE = "some2026"
USUARIOS = ["mvera", "jloor", "rcastro", "admin"]


# ---------------------------------------------------------------------------
# Cliente HTTP con interfaz única (TestClient o urllib + uvicorn en hilo)
# ---------------------------------------------------------------------------
class Respuesta:
    def __init__(self, status_code: int, headers: dict, content: bytes):
        self.status_code = status_code
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.content = content

    def json(self):
        return json.loads(self.content.decode("utf-8")) if self.content else None

    @property
    def detalle(self) -> str:
        try:
            d = self.json()
            return d.get("detalle", "") if isinstance(d, dict) else ""
        except (ValueError, AttributeError):
            return ""


class Cliente:
    """get/post/put/delete(ruta, json=None, headers=None) → Respuesta."""

    def __init__(self):
        self._tc = None
        self._servidor = None
        self._hilo = None
        self._base = None
        try:
            from fastapi.testclient import TestClient
            self._tc = TestClient(app)
            self._tc.__enter__()
            self.modo = "TestClient"
        except (ImportError, RuntimeError):
            # starlette.testclient exige httpx (no está en la lista de bibliotecas permitidas)
            self._arrancar_uvicorn()
            self.modo = "uvicorn+urllib (sin httpx)"

    def _arrancar_uvicorn(self) -> None:
        import uvicorn
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            puerto = s.getsockname()[1]
        config = uvicorn.Config(app, host="127.0.0.1", port=puerto, log_level="warning")
        self._servidor = uvicorn.Server(config)
        self._hilo = threading.Thread(target=self._servidor.run, daemon=True)
        self._hilo.start()
        limite = time.time() + 30
        while not getattr(self._servidor, "started", False) and time.time() < limite:
            time.sleep(0.05)
        if not self._servidor.started:
            raise RuntimeError("uvicorn no arrancó a tiempo.")
        self._base = f"http://127.0.0.1:{puerto}"

    def cerrar(self) -> None:
        if self._tc is not None:
            self._tc.__exit__(None, None, None)
        if self._servidor is not None:
            self._servidor.should_exit = True
            self._hilo.join(timeout=10)

    def peticion(self, metodo: str, ruta: str, json_cuerpo=None, headers: dict | None = None) -> Respuesta:
        headers = dict(headers or {})
        if self._tc is not None:
            r = self._tc.request(metodo, ruta, json=json_cuerpo, headers=headers)
            return Respuesta(r.status_code, dict(r.headers), r.content)
        datos = None
        if json_cuerpo is not None:
            datos = json.dumps(json_cuerpo, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(self._base + ruta, data=datos, method=metodo, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return Respuesta(resp.status, dict(resp.headers), resp.read())
        except urllib.error.HTTPError as e:
            return Respuesta(e.code, dict(e.headers), e.read())

    def get(self, ruta, headers=None):
        return self.peticion("GET", ruta, None, headers)

    def post(self, ruta, json=None, headers=None):  # noqa: A002
        return self.peticion("POST", ruta, json if json is not None else {}, headers)

    def put(self, ruta, json=None, headers=None):  # noqa: A002
        return self.peticion("PUT", ruta, json if json is not None else {}, headers)

    def delete(self, ruta, headers=None):
        return self.peticion("DELETE", ruta, None, headers)


# ---------------------------------------------------------------------------
# Utilidades de verificación
# ---------------------------------------------------------------------------
FALLAS: list[str] = []
TOTAL = 0


def verificar(condicion: bool, mensaje: str) -> bool:
    global TOTAL
    TOTAL += 1
    print(f"   [{'OK ' if condicion else 'FALLA'}] {mensaje}")
    if not condicion:
        FALLAS.append(mensaje)
    return bool(condicion)


def codigo(r: Respuesta, esperado: int, mensaje: str) -> bool:
    extra = "" if r.status_code == esperado else f" (se obtuvo {r.status_code}: {r.detalle or r.content[:120]!r})"
    return verificar(r.status_code == esperado, f"{mensaje} → {esperado}{extra}")


def cab(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def seccion(titulo: str) -> None:
    print(f"\n== {titulo} ==")


def _atributos_netcdf(contenido: bytes, variable: str) -> tuple:
    """(comment global, comment de la variable, procedencia, datos_reales) de un NetCDF exportado."""
    try:
        import xarray as xr
        with xr.open_dataset(io.BytesIO(contenido), engine="scipy") as ds:
            return (ds.attrs.get("comment"), ds[variable].attrs.get("comment"), ds.attrs.get("procedencia"),
                    int(ds.attrs.get("datos_reales", -1)))
    except Exception as e:  # noqa: BLE001
        print(f"   (no se pudo abrir el NetCDF exportado: {e})")
        return (None, None, None, None)


# ---------------------------------------------------------------------------
# Prueba
# ---------------------------------------------------------------------------
def ejecutar() -> int:
    inicio_total = time.perf_counter()
    c = Cliente()
    print(f"Prueba de humo del SOME {cfg.VERSION} — cliente: {c.modo} — datos en {_CARPETA_TEMPORAL}")
    print(f"CHIRPS v3 (real): {'modo sin red, solo caché local' if SIN_RED else 'con acceso a la red'}")
    try:
        from backend import chirps as _chirps
        _r = _chirps.resumen()
        print(f"   caché CHIRPS: {_r.get('dias_en_cache')} días ({_r.get('dias_preliminares')} preliminares), "
              f"corte {_r.get('fecha_corte')}, climatología: {len(_r.get('anios_climatologia') or [])} años")
        if not _r.get("dias_en_cache"):
            print("   AVISO: la caché de CHIRPS está vacía; prepare los datos con "
                  "«python -m backend.herramientas.preparar_chirps».")
    except Exception as e:  # noqa: BLE001 - solo informativo
        print(f"   (no se pudo leer el resumen de CHIRPS: {e})")
    try:
        from backend import copernicus as _copernicus
        _rc = _copernicus.resumen()
        print(f"Copernicus Marine: modo «{_rc.get('modo')}», estado «{_rc.get('estado')}», "
              f"{_rc.get('dias_en_cache')} días en caché, corte {_rc.get('fecha_corte')}")
    except Exception as e:  # noqa: BLE001 - solo informativo
        print(f"   (no se pudo leer el resumen de Copernicus Marine: {e})")
    try:
        from backend import noaa as _noaa
        _rn = _noaa.resumen()
        print(f"NOAA OISST: modo «{_rn.get('modo')}», estado «{_rn.get('estado')}», {_rn.get('dias_en_cache')} días en "
              f"caché ({_rn.get('dias_preliminares')} preliminares, {_rn.get('dias_faltantes')} faltantes), corte "
              f"{_rn.get('fecha_corte')}, caché en {_noaa.raiz()}")
        if _rn.get("modo") == "real" and not _rn.get("dias_en_cache"):
            print("   AVISO: la caché de NOAA está vacía; prepare los datos con "
                  "«python -m backend.herramientas.preparar_noaa».")
    except Exception as e:  # noqa: BLE001 - solo informativo
        print(f"   (no se pudo leer el resumen de NOAA OISST: {e})")
    tokens: dict[str, str] = {}
    try:
        # --- salud y autenticación -------------------------------------------
        seccion("Salud y autenticación")
        r = c.get("/api/salud")
        codigo(r, 200, "GET /api/salud (pública)")
        salud = r.json() or {}
        verificar(salud.get("estado") == "ok" and bool(salud.get("fecha_corte")),
                  "salud devuelve estado ok y la fecha de corte (se compara con la de NOAA en «Catálogo y fuentes»)")
        codigo(c.get("/api/catalogo"), 401, "GET /api/catalogo sin token")
        codigo(c.get("/api/auth/yo", headers=cab("token-falso")), 401, "GET /api/auth/yo con token inválido")
        codigo(c.post("/api/auth/ingresar", {"usuario": "mvera", "clave": "incorrecta"}), 401,
               "ingreso con clave incorrecta")
        codigo(c.post("/api/auth/ingresar", {"usuario": "mvera"}), 422, "ingreso sin clave (cuerpo inválido)")
        for u in USUARIOS:
            r = c.post("/api/auth/ingresar", {"usuario": u, "clave": CLAVE})
            if codigo(r, 200, f"POST /api/auth/ingresar «{u}»"):
                d = r.json()
                tokens[u] = d["token"]
                verificar(d["usuario"]["usuario"] == u and d["usuario"]["rol"] in
                          ("oceanografo", "analista", "revisor", "administrador"),
                          f"«{u}» tiene rol {d['usuario']['rol']} y departamento «{d['usuario']['departamento']}»")
        if len(tokens) < 4:
            print("No fue posible ingresar con todos los usuarios; se detiene la prueba.")
            return 1
        T = cab(tokens["mvera"])
        r = c.get("/api/auth/yo", headers=T)
        codigo(r, 200, "GET /api/auth/yo")
        verificar(r.json().get("nombre") == "Dra. María Vera", "yo → Dra. María Vera")

        # --- catálogo ----------------------------------------------------------
        seccion("Catálogo y fuentes")
        r = c.get("/api/catalogo", headers=T)
        codigo(r, 200, "GET /api/catalogo")
        cat = r.json()
        verificar([f["id"] for f in cat["fuentes"]] == ["noaa_oisst", "copernicus_phy", "chirps_v3"],
                  "tres fuentes en el orden esperado")
        verificar(len(cat["recortes"]) == 12 and cat["periodos_referencia"] == ["1991-2020", "1981-2010", "1993-2022"],
                  "12 recortes y 3 periodos de referencia")
        verificar(cat["bbox_area"] == [-95, -6, -78, 6], "bbox del área")
        fuentes_cat = {f["id"]: f for f in cat["fuentes"]}
        r = c.get("/api/fuentes/estado", headers=T)
        codigo(r, 200, "GET /api/fuentes/estado")
        estados = {e["fuente"]: e for e in r.json()}
        COP = ModoCopernicus(estados["copernicus_phy"])
        NOAA = ModoNOAA(estados["noaa_oisst"])
        print(f"   NOAA OISST: {NOAA.describir()}")
        print(f"   Copernicus Marine: {COP.describir()}")
        CORTE_NOAA = NOAA.corte
        corte_noaa = CORTE_NOAA.isoformat()
        fecha_archivo = CORTE_NOAA.strftime("%Y%m%d")
        reales_esperadas = fuentes_reales_esperadas(NOAA, COP)
        todas_reales = NOAA.real and COP.real
        proc_sistema = "real" if todas_reales else "mixto"
        # La fecha de corte del sistema es la de NOAA OISST: con datos reales, el último día de su caché; en modo
        # simulado, la fecha de corte simulada (27/10/2026)
        if NOAA.real:
            corte_cache_noaa = (NOAA.bloque.get("cache") or {}).get("fecha_corte")
            verificar(corte_cache_noaa == corte_noaa,
                      f"fecha de corte de NOAA real = último día de su caché ({cfg.fmt_fecha(CORTE_NOAA)}; "
                      f"caché: {corte_cache_noaa})")
        else:
            verificar(CORTE_NOAA == FECHA_CORTE_SIMULADA,
                      f"NOAA simulado con la fecha de corte simulada {cfg.fmt_fecha(FECHA_CORTE_SIMULADA)}")
        verificar(cat.get("fecha_corte") == corte_noaa and salud.get("fecha_corte") == corte_noaa,
                  f"fecha de corte del sistema (catálogo y salud) = la de NOAA OISST, {cfg.fmt_fecha(CORTE_NOAA)} "
                  f"(catálogo {cat.get('fecha_corte')}, salud {salud.get('fecha_corte')})")
        verificar(salud.get("procedencia") == proc_sistema and salud.get("fuentes_reales") == reales_esperadas
                  and salud.get("datos_simulados") is (not todas_reales),
                  f"salud declara procedencia «{salud.get('procedencia')}» y las fuentes reales "
                  f"{salud.get('fuentes_reales')} (esperadas: «{proc_sistema}», {reales_esperadas})")
        verificar(cat.get("datos_simulados") is (not todas_reales) and cat.get("fuentes_reales") == reales_esperadas
                  and cat.get("procedencia") == proc_sistema
                  and cat.get("insignia") == (cfg.INSIGNIA_REALES if todas_reales else cfg.INSIGNIA_MIXTOS),
                  f"el catálogo marca como reales {reales_esperadas} (procedencia «{proc_sistema}», "
                  f"datos_simulados {not todas_reales})")
        verificar(fuentes_cat["chirps_v3"].get("datos_reales") is True
                  and fuentes_cat["chirps_v3"].get("insignia") == cfg.INSIGNIA_REALES_CHIRPS
                  and fuentes_cat["noaa_oisst"].get("datos_reales") is NOAA.real
                  and fuentes_cat["noaa_oisst"].get("insignia") == (INSIGNIA_REALES_NOAA if NOAA.real
                                                                    else cfg.INSIGNIA_SIMULADOS)
                  and fuentes_cat["copernicus_phy"].get("datos_reales") is COP.real
                  and fuentes_cat["copernicus_phy"].get("insignia") == (INSIGNIA_REALES_COPERNICUS if COP.real
                                                                        else cfg.INSIGNIA_SIMULADOS),
                  f"insignias por fuente: CHIRPS real; NOAA {'real' if NOAA.real else 'simulado'}; "
                  f"Copernicus {'real' if COP.real else 'simulado'}")
        if todas_reales:
            cabecera_esperada = getattr(cfg, "INSIGNIA_CABECERA_REALES", cfg.INSIGNIA_REALES)
        elif COP.real:
            cabecera_esperada = getattr(cfg, "INSIGNIA_CABECERA_COPERNICUS", None)
        elif NOAA.real:
            cabecera_esperada = getattr(cfg, "INSIGNIA_CABECERA_NOAA", None)
        else:
            cabecera_esperada = cfg.INSIGNIA_CABECERA
        titulo_cabecera = str(cat.get("titulo_cabecera") or "")
        verificar(cat.get("insignia_cabecera") == cabecera_esperada and bool(titulo_cabecera)
                  and (not todas_reales or all(n in titulo_cabecera for n in ("NOAA OISST", "Copernicus", "CHIRPS"))),
                  f"insignia de la cabecera según los modos de NOAA y Copernicus: «{cat.get('insignia_cabecera')}»")
        chirps_estado = estados["chirps_v3"]
        corte_chirps = chirps_estado["fecha_corte"]
        historial_chirps = chirps_estado["historial"]
        verificar(chirps_estado["estado"] in ("operativa", "degradada", "sin_conexion")
                  and len(historial_chirps) == 14 and chirps_estado.get("datos_reales") is True
                  and all(h["estado"] in ("operativa", "degradada", "sin_conexion", "sin_registro")
                          for h in historial_chirps)
                  and historial_chirps[-1]["fecha"] > historial_chirps[0]["fecha"],
                  f"CHIRPS con estado real «{chirps_estado['estado']}» e historial de 14 días "
                  f"(corte {corte_chirps})")
        if SIN_RED:
            verificar(chirps_estado["estado"] == "sin_conexion" and "sin red" in chirps_estado["mensaje"].lower(),
                      "con SOME_CHIRPS_SIN_RED=1, CHIRPS figura «sin conexión» con un mensaje claro")
        caminos = {k["id"]: k for k in chirps_estado.get("caminos") or []}
        cache_chirps = chirps_estado.get("cache") or {}
        verificar(set(caminos) == {"chc", "espejo"}
                  and all(set((k.get("productos") or {})) == {"final", "preliminar"} and k.get("texto_estado")
                          for k in caminos.values())
                  and cache_chirps.get("fecha_corte") and cache_chirps["fecha_corte"] <= corte_chirps
                  and set(cache_chirps.get("dias_por_origen") or {}) <= {"chc", "espejo"},
                  "estado de CHIRPS con los dos caminos (servidor del CHC y réplica), fechas por producto y caché "
                  f"(CHC: {caminos.get('chc', {}).get('texto_estado')}; réplica: "
                  f"{caminos.get('espejo', {}).get('texto_estado')})")
        if SIN_RED:
            verificar(all(k.get("estado") == "sin_red" for k in caminos.values()) and chirps_estado.get("modo_sin_red"),
                      "en modo sin red ningún camino se consulta («No se consulta (modo sin red)»)")
        else:
            e_chc = caminos.get("chc", {}).get("estado")
            e_rep = caminos.get("espejo", {}).get("estado")
            if e_chc in ("operativa", "degradada"):
                esperados = ("operativa", "degradada")
            elif e_rep == "operativa":
                esperados = ("degradada",)
            elif e_chc == "sin_conexion" and e_rep in ("sin_conexion", "no_instalada"):
                esperados = ("sin_conexion",)
            else:
                esperados = ("degradada", "sin_conexion")
            verificar(chirps_estado["estado"] in esperados,
                      f"estado global «{chirps_estado['estado']}» coherente con los caminos (CHC «{e_chc}», "
                      f"réplica «{e_rep}»)")
        noaa_estado = estados["noaa_oisst"]
        historial_noaa = noaa_estado.get("historial") or []
        if NOAA.configurado == "simulado":
            verificar(noaa_estado["estado"] == "operativa",
                      "NOAA en modo simulado forzado (o conector simulado): estado simulado «operativa»")
        elif not NOAA.acceso:
            verificar(noaa_estado["estado"] == "sin_conexion" and bool(noaa_estado.get("mensaje")),
                      f"NOAA sin acceso ({NOAA.motivo}): «sin conexión» con el motivo "
                      f"({noaa_estado.get('mensaje', '')[:90]}…)")
        else:
            verificar(noaa_estado["estado"] in ("operativa", "degradada", "sin_conexion")
                      and (noaa_estado["estado"] == "operativa" or bool(noaa_estado.get("mensaje"))),
                      f"NOAA con acceso: estado real «{noaa_estado['estado']}» con mensaje")
        if NOAA.bloque:
            verificar(len(historial_noaa) == 14
                      and all(h["estado"] in ("operativa", "degradada", "sin_conexion", "sin_registro")
                              and "origen" in h for h in historial_noaa)
                      and {"modo", "modo_configurado", "texto_modo", "acceso", "servidores", "publicacion",
                           "climatologia", "cache"} <= set(NOAA.bloque)
                      and set(NOAA.bloque.get("servidores") or {}) == {"psl", "ncei"}
                      and "caminos" not in noaa_estado and "copernicus" not in noaa_estado,
                      "estado de NOAA con historial de 14 días (con origen) y el bloque «noaa» completo "
                      "(servidores PSL y NCEI, publicación, climatología y caché)")
        cop_estado = estados["copernicus_phy"]
        historial_cop = cop_estado.get("historial") or []
        if COP.configurado == "simulado":
            verificar(cop_estado["estado"] == "operativa",
                      "Copernicus en modo simulado forzado (o conector simulado): estado simulado «operativa»")
        elif not COP.acceso:
            verificar(cop_estado["estado"] == "sin_conexion" and bool(cop_estado.get("mensaje")),
                      f"Copernicus sin acceso: «sin conexión» con el motivo ({cop_estado.get('mensaje', '')[:90]}…)")
        else:
            verificar(cop_estado["estado"] in ("operativa", "degradada", "sin_conexion")
                      and (cop_estado["estado"] == "operativa" or bool(cop_estado.get("mensaje"))),
                      f"Copernicus con acceso: estado real «{cop_estado['estado']}» con mensaje")
        if COP.bloque:
            verificar(len(historial_cop) == 14 and all(h["estado"] in ("operativa", "degradada", "sin_conexion",
                                                                        "sin_registro") for h in historial_cop)
                      and {"modo", "acceso", "credenciales", "servicio", "analisis", "climatologia", "cache"}
                      <= set(COP.bloque),
                      "estado de Copernicus con historial de 14 días y el bloque «copernicus» completo")
            claves_credenciales = set((COP.bloque.get("credenciales") or {}).keys())
            verificar(not claves_credenciales & {"usuario", "username", "password", "clave", "contrasena", "token"},
                      f"el estado de Copernicus solo informa el estado de las credenciales ({sorted(claves_credenciales)})")
        CORTE_COP = COP.corte
        corte_cop = CORTE_COP.isoformat()
        corte_d = date.fromisoformat(corte_chirps)

        # --- consultas ---------------------------------------------------------
        seccion("Consultas")
        nino12 = {"tipo": "recorte", "recorte": "nino12"}
        r = c.post("/api/consultas/mapa", {"fuente": "noaa_oisst", "variable": "sst", "area": nino12,
                                           "fecha": corte_noaa, "modo": "valor"}, headers=T)
        codigo(r, 200, f"POST /api/consultas/mapa (un día: {cfg.fmt_fecha(CORTE_NOAA)}, corte de NOAA)")
        mapa = r.json() if r.status_code == 200 else {"imagen_png": "", "paleta_rgb": [], "fuente": {}}
        verificar(mapa["imagen_png"].startswith("data:image/png;base64,") and len(mapa["paleta_rgb"]) == 16
                  and mapa["fuente"].get("credito"), "mapa con PNG, paleta y crédito")
        proc_mapa = mapa.get("procedencia")
        verificar(NOAA.coherente(mapa) and mapa.get("datos_simulados") is (proc_mapa != "real")
                  and mapa.get("insignia") == insignia_de(proc_mapa, INSIGNIA_REALES_NOAA),
                  f"mapa de SST (NOAA) con procedencia «{proc_mapa}» e insignia «{mapa.get('insignia')}» "
                  f"(esperada: {' o '.join(NOAA.procedencias())})")
        est_mapa = mapa.get("estadisticas") or {}
        verificar((est_mapa.get("celdas_validas") or 0) > 0 and est_mapa.get("minimo") is not None
                  and 19.0 <= est_mapa["minimo"] and est_mapa.get("maximo") is not None and est_mapa["maximo"] <= 31.0,
                  f"SST de la región Niño 1+2 en rango físico (19–31 °C): {est_mapa.get('minimo')} a "
                  f"{est_mapa.get('maximo')} °C, media {est_mapa.get('media')} °C en {est_mapa.get('celdas_validas')} "
                  "celdas")
        r = c.post("/api/consultas/mapa", {"fuente": "chirps_v3", "variable": "precip",
                                           "area": {"tipo": "recorte", "recorte": "cuenca_guayas"},
                                           "periodo": {"inicio": (corte_d - timedelta(days=6)).isoformat(),
                                                       "fin": corte_chirps}}, headers=T)
        codigo(r, 200, "POST /api/consultas/mapa (precipitación CHIRPS, últimos 7 días)")
        mapa_lluvia = r.json() if r.status_code == 200 else {}
        verificar(mapa_lluvia.get("procedencia") == "real" and mapa_lluvia.get("datos_simulados") is False
                  and mapa_lluvia.get("insignia") == cfg.INSIGNIA_REALES_CHIRPS
                  and (mapa_lluvia.get("estadisticas") or {}).get("celdas_validas", 0) > 0
                  and isinstance(mapa_lluvia.get("dias_preliminares"), int),
                  f"mapa de precipitación con procedencia «real» y celdas con dato "
                  f"({mapa_lluvia.get('dias_preliminares')} días preliminares)")
        r = c.post("/api/consultas/mapa", {"fuente": "noaa_oisst", "variable": "sst", "area": nino12,
                                           "periodo": {"inicio": (CORTE_NOAA - timedelta(days=14)).isoformat(),
                                                       "fin": corte_noaa},
                                           "modo": "anomalia", "periodo_referencia": "1991-2020"}, headers=T)
        codigo(r, 200, "POST /api/consultas/mapa (anomalía de los últimos 15 días de NOAA)")
        mapa_anom = r.json() if r.status_code == 200 else {}
        verificar(mapa_anom.get("paleta") == "PuOr_r", "anomalía con paleta PuOr_r")
        verificar(NOAA.coherente(mapa_anom),
                  f"anomalía de SST (NOAA) con procedencia «{mapa_anom.get('procedencia')}»; anomalía media "
                  f"{(mapa_anom.get('estadisticas') or {}).get('media')} °C respecto de 1991-2020")
        r = c.post("/api/consultas/mapa", {"fuente": "copernicus_phy", "variable": "corrientes",
                                           "area": {"tipo": "recorte", "recorte": "area_estudio"},
                                           "fecha": corte_cop, "profundidad": 0, "paso": 2}, headers=T)
        codigo(r, 200, f"POST /api/consultas/mapa (corrientes con vectores, {cfg.fmt_fecha(CORTE_COP)})")
        corrientes = r.json() if r.status_code == 200 else {}
        verificar("vectores" in corrientes, "mapa de corrientes incluye vectores")
        verificar(COP.coherente(corrientes),
                  f"corrientes de Copernicus con procedencia «{corrientes.get('procedencia')}» "
                  f"(esperada: {' o '.join(COP.procedencias())})")

        r = c.post("/api/consultas/serie", {"fuente": "noaa_oisst", "variable": "sst", "area": nino12,
                                            "periodo": {"inicio": inicio_12_meses(CORTE_NOAA).isoformat(),
                                                        "fin": corte_noaa},
                                            "agregacion": "mensual"}, headers=T)
        codigo(r, 200, "POST /api/consultas/serie (12 meses, mensual)")
        serie = r.json() if r.status_code == 200 else {"fechas": [], "climatologia": [], "anomalias": [],
                                                       "resumen": {"texto": []}}
        verificar(len(serie["fechas"]) == 12 and len(serie["climatologia"]) == 12 and len(serie["anomalias"]) == 12
                  and len(serie["resumen"]["texto"]) >= 3, "serie con 12 valores, climatología, anomalías y resumen")
        verificar(NOAA.coherente(serie) and serie.get("fecha_corte") == corte_noaa
                  and (serie.get("fechas") or ["9999"])[-1] <= corte_noaa,
                  f"serie de SST (NOAA) con procedencia «{serie.get('procedencia')}», acotada a su fecha de corte "
                  f"({serie.get('fecha_corte')})")
        if serie.get("procedencia") == "real":
            fuente_noaa = serie.get("fuente") or {}
            verificar("NOAA OISST" in str(fuente_noaa.get("origen_datos", ""))
                      and "1991-2020" in serie.get("metodo", ""),
                      f"serie real de NOAA con el origen de los días ({fuente_noaa.get('origen_datos')}) y el periodo "
                      "de referencia en el método")
        r = c.post("/api/consultas/serie", {"fuente": "chirps_v3", "variable": "precip",
                                            "area": {"tipo": "recorte", "recorte": "guayas"},
                                            "periodo": {"inicio": "2026-09-01", "fin": "2026-10-27"},
                                            "agregacion": "diaria"}, headers=T)
        codigo(r, 200, "POST /api/consultas/serie (precipitación Guayas)")
        serie_lluvia = r.json() if r.status_code == 200 else {}
        verificar(serie_lluvia.get("fecha_corte") == corte_chirps
                  and (serie_lluvia.get("fechas") or ["9999"])[-1] <= corte_chirps,
                  f"serie CHIRPS acotada a su fecha de corte ({corte_chirps})")
        verificar(serie_lluvia.get("procedencia") == "real" and serie_lluvia.get("datos_simulados") is False
                  and any(v is not None for v in serie_lluvia.get("valores") or [])
                  and "CHIRPS" in (serie_lluvia.get("fuente") or {}).get("credito", ""),
                  "serie de precipitación con procedencia «real», valores y crédito de CHIRPS")
        fuente_serie = serie_lluvia.get("fuente") or {}
        verificar(bool(fuente_serie.get("origen_datos")) and bool(fuente_serie.get("origen_climatologia"))
                  and (("dynamical.org" in fuente_serie.get("credito", "")) == bool(fuente_serie.get("usa_replica")))
                  and (not serie_lluvia.get("usa_replica") or "dynamical.org" in fuente_serie.get("credito_replica", ""))
                  and len(fuente_serie.get("credito", "")) <= 150,
                  f"serie CHIRPS con origen de los días ({fuente_serie.get('origen_datos')}), de la climatología y "
                  "crédito que cita la réplica solo si se usó (≤ 150 caracteres para el pie de figura)")
        r = c.post("/api/consultas/serie", {"fuente": "chirps_v3", "variable": "precip",
                                            "area": {"tipo": "punto", "lat": -2.0, "lon": -85.0},
                                            "periodo": {"inicio": "2026-09-01", "fin": "2026-09-30"}}, headers=T)
        codigo(r, 422, "serie CHIRPS en un punto en el mar")
        verificar("CHIRPS solo tiene datos sobre tierra" in r.detalle, "mensaje claro para un punto en el mar")
        r = c.post("/api/consultas/serie", {"fuente": "noaa_oisst", "variable": "sst",
                                            "area": {"tipo": "punto", "lat": -2.0, "lon": -85.0},
                                            "periodo": {"inicio": (CORTE_NOAA - timedelta(days=26)).isoformat(),
                                                        "fin": corte_noaa},
                                            "agregacion": "semanal"}, headers=T)
        codigo(r, 200, "POST /api/consultas/serie (punto, semanal)")
        verificar(r.status_code == 200 and NOAA.coherente(r.json()),
                  f"serie de SST en un punto con procedencia «{(r.json() or {}).get('procedencia')}»")
        # Guayas tiene celdas de mar en la malla de 0.25° (12 con NOAA OISST real: golfo y costa)
        r = c.post("/api/consultas/serie", {"fuente": "noaa_oisst", "variable": "sst",
                                            "area": {"tipo": "recorte", "recorte": "guayas"},
                                            "periodo": {"inicio": (CORTE_NOAA - timedelta(days=29)).isoformat(),
                                                        "fin": corte_noaa}}, headers=T)
        if NOAA.real:
            serie_guayas = r.json() if r.status_code == 200 else {}
            verificar(r.status_code == 200 and any(v is not None for v in serie_guayas.get("valores") or [])
                      and NOAA.coherente(serie_guayas),
                      f"SST real en Guayas con valores (celdas de mar del golfo) → {r.status_code}")
        else:
            verificar(r.status_code in (200, 422), f"SST en Guayas con el simulador → {r.status_code}")

        r = c.post("/api/consultas/climatologia", {"fuente": "noaa_oisst", "variable": "sst", "area": nino12,
                                                   "periodo_referencia": "1981-2010"}, headers=T)
        codigo(r, 200, "POST /api/consultas/climatologia")
        clim_noaa = r.json() if r.status_code == 200 else {"valores": [], "meses": [""]}
        verificar(len(clim_noaa["valores"]) == 12 and clim_noaa["meses"][0] == "ene"
                  and all(v is not None and 19.0 <= v <= 31.0 for v in clim_noaa["valores"]),
                  "12 meses de climatología en rango físico (19–31 °C)")
        verificar(NOAA.coherente(clim_noaa), f"climatología de NOAA con procedencia «{clim_noaa.get('procedencia')}»")
        if clim_noaa.get("procedencia") == "real":
            efectivo = clim_noaa.get("periodo_referencia_efectivo")
            verificar(efectivo == PERIODO_EFECTIVO_1981_2010 and clim_noaa.get("periodo_referencia") == "1981-2010",
                      f"climatología real 1981-2010 con periodo de referencia efectivo «{efectivo}» (OISST empieza "
                      "en 09/1981)")

        r = c.post("/api/consultas/mismo-mes", {"fuente": "noaa_oisst", "variable": "sst", "area": nino12, "mes": 10},
                   headers=T)
        codigo(r, 200, "POST /api/consultas/mismo-mes")
        mismo_mes = r.json() if r.status_code == 200 else {"anios": [], "texto": []}
        verificar(mismo_mes["anios"] == [2023, 2024, 2025, 2026] and len(mismo_mes["texto"]) >= 2,
                  "mismo mes con los años 2023–2026 y frases")
        verificar(NOAA.coherente(mismo_mes),
                  f"«mismo mes cada año» de NOAA con procedencia «{mismo_mes.get('procedencia')}» "
                  f"({(mismo_mes.get('fuente') or {}).get('origen_datos')})")
        # CHIRPS, octubre en Guayas: suma días de varios años (y de los dos servidores si se mezclan); el
        # crédito debe corresponder a la mezcla REAL de «Origen de los días».
        r = c.post("/api/consultas/mismo-mes", {"fuente": "chirps_v3", "variable": "precip",
                                                 "area": {"tipo": "recorte", "recorte": "guayas"}, "mes": 10},
                   headers=T)
        codigo(r, 200, "POST /api/consultas/mismo-mes (CHIRPS, octubre en Guayas)")
        f_mm = r.json().get("fuente") or {}
        origen_mm, credito_mm = f_mm.get("origen_datos") or "", f_mm.get("credito") or ""
        con_chc, con_rep = "servidor del CHC" in origen_mm, "réplica dynamical.org" in origen_mm
        coherente = (("parte de los días vía la réplica" in credito_mm) if (con_chc and con_rep) else
                     ("vía la réplica de dynamical.org" in credito_mm and "parte" not in credito_mm) if con_rep else
                     ("dynamical.org" not in credito_mm or "climatología" in credito_mm))
        verificar(coherente, f"crédito de «mismo mes» coherente con el origen de los días ({origen_mm})")

        r = c.post("/api/consultas/perfil", {"fuente": "copernicus_phy", "variable": "thetao", "lat": -2.0,
                                             "lon": -85.0, "fecha": corte_cop}, headers=T)
        codigo(r, 200, f"POST /api/consultas/perfil ({cfg.fmt_fecha(CORTE_COP)})")
        perfil = r.json() if r.status_code == 200 else {"profundidades": [], "capa_mezcla_m": None}
        verificar(len(perfil["profundidades"]) == 8 and perfil["capa_mezcla_m"] is not None,
                  "perfil con 8 niveles y capa de mezcla")
        verificar(COP.coherente(perfil), f"perfil de Copernicus con procedencia «{perfil.get('procedencia')}»"
                                         f"{' y avisos' if perfil.get('avisos') else ''}")
        r = c.post("/api/consultas/perfil", {"fuente": "copernicus_phy", "variable": "so", "lat": -2.0,
                                             "lon": -85.0, "fecha": corte_cop}, headers=T)
        codigo(r, 200, "POST /api/consultas/perfil (salinidad)")

        inicio_cortina = (CORTE_COP - timedelta(days=56)).isoformat()
        r = c.post("/api/consultas/cortina", {"fuente": "copernicus_phy", "variable": "thetao", "lat": -2.0,
                                              "lon": -85.0, "periodo": {"inicio": inicio_cortina, "fin": corte_cop}},
                   headers=T)
        codigo(r, 200, "POST /api/consultas/cortina (57 días hasta el corte de Copernicus)")
        cortina = r.json() if r.status_code == 200 else {"valores": [[]], "fechas": [None]}
        verificar(len(cortina["valores"]) == 8 and len(cortina["valores"][0]) == len(cortina["fechas"]),
                  "cortina [8 profundidades][fechas]")
        verificar(COP.coherente(cortina), f"cortina de Copernicus con procedencia «{cortina.get('procedencia')}»")

        inicio_anio_cop = (CORTE_COP - timedelta(days=364)).isoformat()
        r = c.post("/api/consultas/comparar", {
            "a": {"fuente": "noaa_oisst", "variable": "sst", "area": nino12,
                  "periodo": {"inicio": inicio_anio_cop, "fin": corte_cop}},
            "b": {"fuente": "copernicus_phy", "variable": "thetao", "area": nino12,
                  "periodo": {"inicio": inicio_anio_cop, "fin": corte_cop}, "profundidad": 0},
            "agregacion": "semanal"}, headers=T)
        codigo(r, 200, "POST /api/consultas/comparar (dos fuentes, último año de Copernicus)")
        comp = r.json() if r.status_code == 200 else {"diferencia": {}, "fuentes": [], "texto": [], "b": {}}
        verificar(comp["diferencia"].get("correlacion") is not None and len(comp["fuentes"]) == 2 and comp["texto"],
                  "comparación con correlación, dos fuentes y frases")
        # La procedencia de la comparación combina la de cada lado: «real» solo si NOAA y Copernicus lo son
        proc_a = (comp.get("a") or {}).get("procedencia")
        proc_b = (comp.get("b") or {}).get("procedencia")
        esperada_comp = proc_a if proc_a == proc_b else "mixto"
        verificar(proc_a in NOAA.procedencias() and COP.coherente(comp.get("b") or {})
                  and comp.get("procedencia") == esperada_comp
                  and comp.get("datos_simulados") is (esperada_comp != "real"),
                  f"comparación NOAA vs. Copernicus con procedencia «{comp.get('procedencia')}» "
                  f"(NOAA «{proc_a}», Copernicus «{proc_b}»)")
        if NOAA.procedencias() == ("real",) and COP.procedencias() == ("real",):
            corr = comp["diferencia"].get("correlacion")
            dif_media = comp["diferencia"].get("media")
            verificar(comp.get("procedencia") == "real" and corr is not None and corr >= 0.9
                      and dif_media is not None and abs(dif_media) < 1.0,
                      f"NOAA OISST frente a Copernicus Marine (0 m), real frente a real: correlación {corr} (≥ 0.9) y "
                      f"diferencia media {dif_media} °C (< 1 °C)")
        r = c.post("/api/consultas/comparar", {
            "a": {"fuente": "chirps_v3", "variable": "precip", "area": {"tipo": "recorte", "recorte": "guayas"},
                  "periodo": {"inicio": "2025-09-01", "fin": "2025-09-30"}},
            "b": {"fuente": "chirps_v3", "variable": "precip", "area": {"tipo": "recorte", "recorte": "guayas"},
                  "periodo": {"inicio": "2026-09-01", "fin": "2026-09-30"}},
            "agregacion": "diaria"}, headers=T)
        codigo(r, 200, "POST /api/consultas/comparar (precipitación, dos periodos)")
        verificar(r.status_code == 200 and r.json()["procedencia"] == "real",
                  "comparación de dos periodos de CHIRPS con procedencia «real»")
        cuerpo_sst_lluvia = {
            "a": {"fuente": "noaa_oisst", "variable": "sst", "area": nino12,
                  "periodo": {"inicio": "2026-09-01", "fin": "2026-09-30"}},
            "b": {"fuente": "chirps_v3", "variable": "precip", "area": {"tipo": "recorte", "recorte": "guayas"},
                  "periodo": {"inicio": "2026-09-01", "fin": "2026-09-30"}},
            "agregacion": "diaria"}
        r = c.post("/api/consultas/comparar", cuerpo_sst_lluvia, headers=T)
        # Unidades distintas: solo correlación (sin diferencia ni RMSE). Procedencia: «real» con NOAA real (SST y
        # precipitación reales); «mixto» con NOAA simulado.
        codigo(r, 200, "comparar SST con precipitación (unidades distintas)")
        comp_mixta = r.json() if r.status_code == 200 else {}
        dif_mixta = comp_mixta.get("diferencia") or {}
        proc_sst = comp_mixta.get("a", {}).get("procedencia")
        esperada_sst_lluvia = "real" if proc_sst == "real" else "mixto"
        verificar(proc_sst in NOAA.procedencias() and comp_mixta.get("procedencia") == esperada_sst_lluvia
                  and comp_mixta.get("mismas_unidades") is False
                  and dif_mixta.get("media") is None and dif_mixta.get("rmse") is None
                  and dif_mixta.get("solo_correlacion") is True
                  and comp_mixta.get("b", {}).get("procedencia") == "real"
                  and "unidades distintas" in comp_mixta.get("metodo", ""),
                  f"SST frente a precipitación: procedencia «{comp_mixta.get('procedencia')}» (SST «{proc_sst}», "
                  "precipitación «real»), solo correlación (sin diferencia ni RMSE)")
        if NOAA.real:
            # El caso «mixto» deliberado: la misma comparación con NOAA simulado, en un proceso aparte
            m = comparacion_mixta_aparte(cuerpo_sst_lluvia)
            verificar(m.get("procedencia") == "mixto" and m.get("a") == "simulado" and m.get("b") == "real"
                      and m.get("datos_simulados") is True and m.get("solo_correlacion") is True,
                      f"caso «mixto» en un proceso aparte con SOME_NOAA_MODO=simulado: SST «{m.get('a')}» frente a "
                      f"precipitación «{m.get('b')}» → «{m.get('procedencia')}»"
                      f"{' (' + str(m['error'])[:200] + ')' if m.get('error') else ''}")
        r = c.post("/api/consultas/comparar", {
            "a": {"fuente": "noaa_oisst", "variable": "sst", "area": nino12,
                  "periodo": {"inicio": "2024-02-01", "fin": "2024-02-29"}},
            "b": {"fuente": "noaa_oisst", "variable": "sst", "area": nino12,
                  "periodo": {"inicio": "2026-02-01", "fin": "2026-02-28"}},
            "agregacion": "diaria"}, headers=T)
        codigo(r, 200, "POST /api/consultas/comparar (dos periodos)")
        dos_periodos = r.json() if r.status_code == 200 else {}
        verificar(dos_periodos.get("alineacion") == "dia_relativo"
                  and dos_periodos.get("procedencia") in NOAA.procedencias(),
                  f"periodos distintos alineados por día relativo (procedencia «{dos_periodos.get('procedencia')}»)")

        r = c.post("/api/consultas/vectores", {"fuente": "copernicus_phy", "variable": "corrientes",
                                               "area": {"tipo": "recorte", "recorte": "area_estudio"},
                                               "fecha": corte_cop, "paso": 2}, headers=T)
        codigo(r, 200, "POST /api/consultas/vectores")
        vec = r.json() if r.status_code == 200 else {"lats": [], "u": []}
        verificar(len(vec["lats"]) == 25 and len(vec["u"]) == 25, "vectores submuestreados 25 filas")
        verificar(COP.coherente(vec), f"vectores de Copernicus con procedencia «{vec.get('procedencia')}»")

        # Serie de Copernicus con anomalías: periodo de referencia efectivo de la climatología (GLORYS12)
        r = c.post("/api/consultas/serie", {"fuente": "copernicus_phy", "variable": "thetao",
                                            "area": {"tipo": "recorte", "recorte": "golfo_guayaquil"},
                                            "periodo": {"inicio": (CORTE_COP - timedelta(days=29)).isoformat(),
                                                        "fin": corte_cop},
                                            "profundidad": 20, "periodo_referencia": "1991-2020"}, headers=T)
        codigo(r, 200, "POST /api/consultas/serie (Copernicus, thetao a 20 m en el golfo, 30 días)")
        serie_cop = r.json() if r.status_code == 200 else {}
        verificar(COP.coherente(serie_cop), f"serie de Copernicus con procedencia «{serie_cop.get('procedencia')}»")
        if serie_cop.get("procedencia") == "real":
            efectivo = serie_cop.get("periodo_referencia_efectivo")
            verificar(bool(efectivo) and efectivo in serie_cop.get("metodo", "")
                      and serie_cop.get("periodo_referencia") == "1991-2020",
                      f"serie real de Copernicus con periodo de referencia efectivo «{efectivo}» (se pidió 1991-2020) "
                      "en la respuesta y en el método")
        elif serie_cop.get("procedencia") == "simulado" and COP.real:
            verificar(bool(serie_cop.get("avisos")), "serie de Copernicus con respaldo simulado señalado con avisos")

        seccion("Consultas inválidas")
        codigo(c.post("/api/consultas/mapa", {"fuente": "noaa_oisst", "variable": "sst", "fecha": "2026-10-27"}),
               401, "consulta sin token")
        codigo(c.post("/api/consultas/mapa", {"variable": "sst", "fecha": "2026-10-27"}, headers=T), 422,
               "mapa sin «fuente»")
        codigo(c.post("/api/consultas/serie", {"fuente": "noaa_oisst", "variable": "sst", "area": nino12,
                                               "agregacion": "diaria"}, headers=T), 422, "serie sin «periodo»")
        codigo(c.post("/api/consultas/serie", {"fuente": "noaa_oisst", "variable": "sst",
                                               "area": {"tipo": "bbox", "bbox": [-79.5, -2.5, -78.5, -1.5]},
                                               "periodo": {"inicio": "2026-10-01", "fin": "2026-10-27"}},
                      headers=T), 422, "SST en un rectángulo solo de tierra")
        codigo(c.post("/api/consultas/serie", {"fuente": "chirps_v3", "variable": "precip",
                                               "area": {"tipo": "bbox", "bbox": [-90, -5, -85, -3]},
                                               "periodo": {"inicio": "2026-10-01", "fin": "2026-10-27"}},
                      headers=T), 422, "precipitación en mar abierto")
        codigo(c.post("/api/consultas/mapa", {"fuente": "noaa_oisst", "variable": "sst",
                                              "area": {"tipo": "recorte", "recorte": "no_existe"},
                                              "fecha": "2026-10-27"}, headers=T), 404, "recorte inexistente")
        codigo(c.post("/api/consultas/mapa", {"fuente": "no_existe", "variable": "sst", "fecha": "2026-10-27"},
                      headers=T), 404, "fuente inexistente")
        codigo(c.post("/api/consultas/mismo-mes", {"fuente": "noaa_oisst", "variable": "sst", "area": nino12,
                                                   "mes": 13}, headers=T), 422, "mes 13")
        codigo(c.post("/api/consultas/perfil", {"fuente": "copernicus_phy", "variable": "thetao", "lat": -1.0,
                                                "lon": -78.5, "fecha": corte_cop}, headers=T), 422,
               "perfil en tierra")
        codigo(c.post("/api/consultas/perfil", {"fuente": "copernicus_phy", "variable": "thetao", "lat": -2.0,
                                                "lon": -85.0, "fecha": corte_cop}, headers=T), 200,
               "perfil en el mar (para comparar con el de tierra)")
        codigo(c.post("/api/consultas/mapa", {"fuente": "noaa_oisst", "variable": "sst", "area": nino12,
                                              "fecha": "2026-10-27", "modo": "otro"}, headers=T), 422,
               "modo no permitido")
        codigo(c.post("/api/consultas/perfil", {"fuente": "copernicus_phy", "variable": "thetao", "lat": -2.0,
                                                "lon": -85.0, "fecha": "no-es-fecha"}, headers=T), 400,
               "fecha mal formada")

        # --- exportación -------------------------------------------------------
        seccion("Exportación")
        consulta_mapa = {"fuente": "noaa_oisst", "variable": "sst", "area": nino12, "fecha": corte_noaa}
        for formato, magia, ext in (("csv", b"\xef\xbb\xbf# SOME", "csv"), ("png", b"\x89PNG", "png"),
                                    ("netcdf", b"CDF", "nc")):
            r = c.post("/api/exportar", {"formato": formato, "clase": "mapa", "consulta": consulta_mapa}, headers=T)
            codigo(r, 200, f"POST /api/exportar {formato} de un mapa")
            disp = r.headers.get("content-disposition", "")
            verificar(r.content.startswith(magia) and f"SOME_noaa_oisst_sst_{fecha_archivo}.{ext}" in disp,
                      f"{formato}: contenido válido y nombre SOME_noaa_oisst_sst_{fecha_archivo}.{ext}")
        consulta_serie = {"fuente": "noaa_oisst", "variable": "sst", "area": nino12,
                          "periodo": {"inicio": (CORTE_NOAA - timedelta(days=29)).isoformat(), "fin": corte_noaa},
                          "agregacion": "diaria"}
        r = c.post("/api/exportar", {"formato": "csv", "clase": "serie", "consulta": consulta_serie}, headers=T)
        codigo(r, 200, "exportar csv de una serie")
        texto_csv = r.content.decode("utf-8-sig")
        proc_csv_sst = r.headers.get("x-some-procedencia")
        # Filas de datos (sin las líneas de comentario «# …», cuyo texto de método puede llevar «;»)
        datos_csv = [ln for ln in texto_csv.splitlines() if ln and not ln.startswith("#")]
        ultima_csv = datos_csv[-1].split(",") if datos_csv else []
        valor_csv = ultima_csv[1] if len(ultima_csv) == 4 else ""
        verificar("# Crédito:" in texto_csv and "# Método:" in texto_csv and "# Fecha de corte:" in texto_csv
                  and datos_csv[:1] == ["fecha,valor,climatologia,anomalia"] and len(ultima_csv) == 4
                  and "." in valor_csv and valor_csv.lstrip("-").replace(".", "", 1).isdigit()
                  and not any(";" in ln for ln in datos_csv),
                  "CSV estándar con cabecera de comentarios, separador «,» y punto decimal")
        insignia_csv_sst = insignia_de(proc_csv_sst, INSIGNIA_REALES_NOAA)
        verificar(proc_csv_sst in NOAA.procedencias() and f"# {insignia_csv_sst}" in texto_csv
                  and r.headers.get("x-some-datos-simulados") == ("false" if proc_csv_sst == "real" else "true")
                  and (proc_csv_sst != "real" or (DOI_NOAA in texto_csv and cfg.INSIGNIA_SIMULADOS not in texto_csv)),
                  f"CSV de SST «{proc_csv_sst}» con la insignia «{insignia_csv_sst}» y cabeceras X-SOME-* coherentes"
                  f"{' (crédito con el DOI de NOAA OISST)' if proc_csv_sst == 'real' else ''}")
        codigo(c.post("/api/exportar", {"formato": "png", "clase": "serie", "consulta": consulta_serie}, headers=T),
               200, "exportar png de una serie")
        consulta_lluvia = {"fuente": "chirps_v3", "variable": "precip", "area": {"tipo": "recorte", "recorte": "guayas"},
                           "periodo": {"inicio": (corte_d - timedelta(days=29)).isoformat(), "fin": corte_chirps},
                           "agregacion": "diaria"}
        r = c.post("/api/exportar", {"formato": "csv", "clase": "serie", "consulta": consulta_lluvia}, headers=T)
        codigo(r, 200, "exportar csv de una serie de precipitación")
        texto_lluvia = r.content.decode("utf-8-sig") if r.status_code == 200 else ""
        verificar(f"# {cfg.INSIGNIA_REALES_CHIRPS}" in texto_lluvia and cfg.INSIGNIA_SIMULADOS not in texto_lluvia
                  and r.headers.get("x-some-procedencia") == "real"
                  and r.headers.get("x-some-datos-simulados") == "false",
                  "CSV de precipitación con la insignia de datos reales y cabeceras X-SOME-* coherentes")
        codigo(c.post("/api/exportar", {"formato": "png", "clase": "serie", "consulta": consulta_lluvia}, headers=T),
               200, "exportar png de una serie de precipitación")
        r = c.post("/api/exportar", {"formato": "netcdf", "clase": "mapa",
                                     "consulta": {"fuente": "chirps_v3", "variable": "precip",
                                                  "area": {"tipo": "recorte", "recorte": "guayas"},
                                                  "fecha": corte_chirps}}, headers=T)
        codigo(r, 200, "exportar netcdf de un mapa de precipitación")
        verificar(_atributos_netcdf(r.content, "precip") == (cfg.INSIGNIA_REALES_CHIRPS, cfg.INSIGNIA_REALES_CHIRPS,
                                                             "real", 1),
                  "NetCDF de precipitación con comment = insignia de datos reales (global y de la variable)")
        r = c.post("/api/exportar", {"formato": "netcdf", "clase": "mapa", "consulta": consulta_mapa}, headers=T)
        proc_nc_sst = r.headers.get("x-some-procedencia")
        insignia_nc_sst = insignia_de(proc_nc_sst, INSIGNIA_REALES_NOAA)
        atributos_nc_sst = _atributos_netcdf(r.content, "sst")
        verificar(proc_nc_sst in NOAA.procedencias()
                  and atributos_nc_sst[:3] == (insignia_nc_sst, insignia_nc_sst, proc_nc_sst)
                  and (proc_nc_sst != "real" or atributos_nc_sst[3] == 1),
                  f"NetCDF de SST «{proc_nc_sst}» con comment = «{insignia_nc_sst}» (global y de la variable)")
        codigo(c.post("/api/exportar", {"formato": "netcdf", "clase": "serie", "consulta": consulta_serie},
                      headers=T), 200, "exportar netcdf de una serie")
        consulta_perfil = {"fuente": "copernicus_phy", "variable": "thetao", "lat": -2.0, "lon": -85.0,
                           "fecha": corte_cop}
        for formato in ("csv", "png", "netcdf"):
            r = c.post("/api/exportar", {"formato": formato, "clase": "perfil", "consulta": consulta_perfil}, headers=T)
            codigo(r, 200, f"exportar {formato} de un perfil")
            if r.status_code == 200:
                proc = r.headers.get("x-some-procedencia")
                verificar(proc in COP.procedencias(),
                          f"exportación {formato} del perfil con X-SOME-Procedencia «{proc}» coherente con el modo")
        consulta_cortina = {"fuente": "copernicus_phy", "variable": "thetao", "lat": -2.0, "lon": -85.0,
                            "periodo": {"inicio": (CORTE_COP - timedelta(days=56)).isoformat(), "fin": corte_cop}}
        for formato in ("csv", "png", "netcdf"):
            codigo(c.post("/api/exportar", {"formato": formato, "clase": "cortina", "consulta": consulta_cortina},
                          headers=T), 200, f"exportar {formato} de una cortina")
        # CSV de una serie de Copernicus: periodo de referencia efectivo (si es real) o aviso (si es respaldo)
        consulta_serie_cop = {"fuente": "copernicus_phy", "variable": "thetao",
                              "area": {"tipo": "recorte", "recorte": "golfo_guayaquil"}, "profundidad": 20,
                              "periodo": {"inicio": (CORTE_COP - timedelta(days=29)).isoformat(), "fin": corte_cop},
                              "periodo_referencia": "1991-2020"}
        r = c.post("/api/exportar", {"formato": "csv", "clase": "serie", "consulta": consulta_serie_cop}, headers=T)
        codigo(r, 200, "exportar csv de una serie de Copernicus")
        texto_cop = r.content.decode("utf-8-sig") if r.status_code == 200 else ""
        proc_cop = r.headers.get("x-some-procedencia")
        if proc_cop == "real":
            verificar("# Periodo de referencia efectivo:" in texto_cop and f"# {INSIGNIA_REALES_COPERNICUS}" in texto_cop
                      and "10.48670/moi-00016" in texto_cop,
                      "CSV de Copernicus real con periodo de referencia efectivo, crédito con DOI e insignia real")
        else:
            verificar(proc_cop in COP.procedencias() and (not COP.real or "# Aviso:" in texto_cop)
                      and "# Datos reales · Copernicus Marine" not in texto_cop,
                      f"CSV de Copernicus «{proc_cop}»: sin insignia real{' y con aviso' if COP.real else ''}")
        codigo(c.post("/api/exportar", {"formato": "xlsx", "clase": "mapa", "consulta": consulta_mapa}, headers=T),
               422, "formato no permitido")
        codigo(c.post("/api/exportar", {"formato": "csv", "clase": "mapa", "consulta": consulta_mapa}), 401,
               "exportar sin token")

        # --- reportes y trabajos --------------------------------------------------
        seccion("Reportes y trabajos")
        r = c.get("/api/reportes/plantillas", headers=T)
        codigo(r, 200, "GET /api/reportes/plantillas")
        plantillas = r.json()
        verificar([p["id"] for p in plantillas] == ["boletin_erfen", "informe_variable", "comparativo"],
                  "tres plantillas en el orden del contrato")
        verificar(all(p["parametros"] and all({"id", "etiqueta", "tipo"} <= set(par) for par in p["parametros"])
                      for p in plantillas), "cada parámetro tiene id, etiqueta y tipo")
        codigo(c.post("/api/reportes", {"plantilla": "no_existe", "parametros": {}}, headers=T), 404,
               "plantilla inexistente")
        codigo(c.post("/api/reportes", {"parametros": {}}, headers=T), 422, "reporte sin plantilla")

        # El boletín termina en la fecha de corte de CHIRPS (si es anterior a la de NOAA) para que incluya
        # precipitación real; el comparativo de NOAA, en su fecha de corte (los 30 días hasta el corte y los mismos
        # días un año antes)
        fin_boletin = min(corte_d, CORTE_NOAA)
        inicio_b = CORTE_NOAA - timedelta(days=29)
        parametros = {
            "boletin_erfen": {"periodo": {"inicio": (fin_boletin - timedelta(days=14)).isoformat(),
                                          "fin": fin_boletin.isoformat()},
                              "recorte": "nino12", "incluir_precipitacion": True, "autor": "Dra. María Vera"},
            "informe_variable": {"fuente": "copernicus_phy", "variable": "thetao", "recorte": "golfo_guayaquil",
                                 "periodo": {"inicio": (CORTE_COP - timedelta(days=29)).isoformat(), "fin": corte_cop},
                                 "profundidad": "20", "periodo_referencia": "1993-2022", "autor": "Dra. María Vera"},
            "comparativo": {"fuente": "noaa_oisst", "variable": "sst", "recorte": "nino12",
                            "periodo_a": {"inicio": un_anio_antes(inicio_b).isoformat(),
                                          "fin": un_anio_antes(CORTE_NOAA).isoformat()},
                            "periodo_b": {"inicio": inicio_b.isoformat(), "fin": corte_noaa},
                            "autor": "Dra. María Vera"},
        }
        # Boletín ERFEN: SST e índice de NOAA + perfil de Copernicus + lluvia de CHIRPS (real): «real» si NOAA y
        # Copernicus son reales (y sin respaldo posible), «mixto» si alguno es simulado; con solo caché, cualquiera de
        # los dos. Informe de Copernicus y comparativo de NOAA: según el modo de cada uno.
        if NOAA.procedencias() == ("real",) and COP.procedencias() == ("real",):
            esperadas_boletin = ("real",)
        elif not (NOAA.real and COP.real):
            esperadas_boletin = ("mixto",)
        else:
            esperadas_boletin = ("real", "mixto")
        ids_trabajo: dict[str, int] = {}
        for plantilla, params in parametros.items():
            r = c.post("/api/reportes", {"plantilla": plantilla, "parametros": params}, headers=T)
            if codigo(r, 202, f"POST /api/reportes «{plantilla}»"):
                verificar(r.json()["estado"] == "en_cola", f"«{plantilla}» queda en_cola")
                ids_trabajo[plantilla] = r.json()["trabajo_id"]

        print("   … esperando a que terminen los trabajos (incluidos los dos iniciales de la demo)")
        inicio_espera = time.perf_counter()
        for plantilla, tid in ids_trabajo.items():
            t = trabajos.esperar(tid, 240)
            r = c.get(f"/api/trabajos/{tid}", headers=T)
            codigo(r, 200, f"GET /api/trabajos/{tid}")
            d = r.json()
            verificar(d["estado"] == "listo" and d["progreso"] == 100 and d["archivo"] == f"/api/reportes/{tid}/descargar",
                      f"«{plantilla}» listo con archivo ({d.get('mensaje')})")
            esperadas = {"boletin_erfen": esperadas_boletin, "informe_variable": COP.procedencias(),
                         "comparativo": NOAA.procedencias()}[plantilla]
            verificar(d.get("procedencia") in esperadas,
                      f"«{plantilla}» con procedencia «{' o '.join(esperadas)}» "
                      f"(se obtuvo «{d.get('procedencia')}»: {d.get('insignia')})")
            if plantilla == "boletin_erfen" and d.get("procedencia") == "real":
                verificar(d.get("insignia") == cfg.INSIGNIA_REALES,
                          f"el boletín ERFEN real (NOAA OISST, Copernicus Marine y CHIRPS v3) lleva la insignia "
                          f"«{cfg.INSIGNIA_REALES}» (se obtuvo «{d.get('insignia')}»)")
            if plantilla == "comparativo" and d.get("procedencia") == "real":
                verificar(d.get("insignia") == INSIGNIA_REALES_NOAA,
                          f"el comparativo de NOAA real lleva la insignia «{INSIGNIA_REALES_NOAA}»")
            if plantilla == "informe_variable" and d.get("procedencia") == "real":
                verificar(d.get("insignia") == INSIGNIA_REALES_COPERNICUS,
                          "el informe de Copernicus real lleva la insignia «Datos reales · Copernicus Marine»")
            r = c.get(f"/api/reportes/{tid}/descargar", headers=T)
            codigo(r, 200, f"GET /api/reportes/{tid}/descargar (cabecera)")
            verificar(r.content[:5] == b"%PDF-" and r.headers.get("content-type", "").startswith("application/pdf")
                      and f"SOME_{plantilla}_" in r.headers.get("content-disposition", ""),
                      f"PDF válido de {len(r.content) // 1024} KB con nombre SOME_{plantilla}_<aaaammdd>.pdf")
            r = c.get(f"/api/reportes/{tid}/descargar?token={tokens['mvera']}")
            codigo(r, 200, f"GET /api/reportes/{tid}/descargar?token= (sin cabecera)")
            verificar("inline" in r.headers.get("content-disposition", ""), "con ?token= se muestra en línea")
        print(f"   tiempo de espera de los reportes: {time.perf_counter() - inicio_espera:.1f} s")
        for t in almacen.listar_trabajos():
            if t.get("usuario") == "mvera" and t["id"] not in ids_trabajo.values():
                trabajos.esperar(t["id"], 240)
        r = c.get("/api/trabajos", headers=T)
        codigo(r, 200, "GET /api/trabajos (mvera)")
        lista = r.json()
        verificar(len(lista) >= 5 and all(t["estado"] == "listo" for t in lista),
                  f"mvera ve {len(lista)} trabajos y todos están listos (2 iniciales + 3 nuevos)")
        verificar(any("Boletín" in t["titulo"] for t in lista) and all(t["archivo"] for t in lista),
                  "los trabajos iniciales (boletín e informe) se generaron de verdad")
        r = c.get("/api/trabajos", headers=cab(tokens["jloor"]))
        codigo(r, 200, "GET /api/trabajos (jloor)")
        verificar(len(r.json()) == 0, "jloor no ve los trabajos de mvera")
        codigo(c.get(f"/api/reportes/{ids_trabajo['comparativo']}/descargar", headers=cab(tokens["jloor"])), 403,
               "jloor no puede descargar un reporte de mvera")
        r = c.get("/api/trabajos", headers=cab(tokens["admin"]))
        verificar(r.status_code == 200 and len(r.json()) >= 5, "el administrador ve todos los trabajos")
        codigo(c.get("/api/trabajos/99999", headers=T), 404, "trabajo inexistente")

        # --- panel -------------------------------------------------------------
        seccion("Panel personal")
        for u, esperado in (("mvera", 8), ("rcastro", 5), ("admin", 3)):
            r = c.get("/api/panel", headers=cab(tokens[u]))
            codigo(r, 200, f"GET /api/panel «{u}»")
            verificar(len(r.json()["widgets"]) == esperado, f"panel por defecto de «{u}» con {esperado} widgets")
        r = c.get("/api/panel/tipos", headers=T)
        codigo(r, 200, "GET /api/panel/tipos")
        tipos = [t["id"] for t in r.json()]
        verificar({"kpi_variable", "mini_serie", "mini_mapa", "estado_fuentes", "trabajos_recientes", "mis_vistas",
                   "indice_enso", "precipitacion_provincias"} <= set(tipos) and "kpi_sistema" not in tipos,
                  "tipos de widget para oceanógrafa (sin kpi_sistema)")
        panel = c.get("/api/panel", headers=T).json()
        for w in panel["widgets"]:
            r = c.post("/api/panel/widget/datos", {"tipo": w["tipo"], "config": w["config"]}, headers=T)
            ok = codigo(r, 200, f"POST /api/panel/widget/datos «{w['tipo']}»")
            if ok:
                d = r.json()
                verificar("error" not in d, f"«{w['tipo']}» devuelve datos ({', '.join(list(d)[:5])}…)")
        r = c.post("/api/panel/widget/datos", {"tipo": "precipitacion_provincias", "config": {"dias": 7}}, headers=T)
        n_prov = len(r.json().get("provincias", [])) if r.status_code == 200 else 0
        verificar(r.status_code == 200 and n_prov >= 5,
                  f"precipitación por provincia con {n_prov} provincias "
                  "(Galápagos puede quedar sin celdas de tierra con dato)")
        verificar(r.status_code == 200 and r.json().get("procedencia") == "real",
                  "widget de precipitación por provincia con procedencia «real»")
        r = c.post("/api/panel/widget/datos", {"tipo": "indice_enso", "config": {"meses": 12}}, headers=T)
        enso = r.json() if r.status_code == 200 else {}
        proc_enso = enso.get("procedencia")
        verificar(r.status_code == 200 and proc_enso in NOAA.procedencias()
                  and (proc_enso == "real" or not NOAA.real or bool(enso.get("avisos"))),
                  f"índice Niño 1+2 (calculado con la SST de NOAA) con procedencia «{proc_enso}» "
                  f"(esperada: {' o '.join(NOAA.procedencias())})")
        if proc_enso == "real":
            fechas_enso = enso.get("fechas") or []
            verificar(len(fechas_enso) >= 40 and all(date.fromisoformat(f).weekday() == 2 for f in fechas_enso)
                      and enso.get("fecha_corte") == corte_noaa and enso.get("datos_simulados") is False
                      and enso.get("insignia") == INSIGNIA_REALES_NOAA,
                      f"índice Niño 1+2 real: {len(fechas_enso)} semanas centradas en el miércoles (como NOAA CPC), "
                      f"fecha de corte de NOAA ({enso.get('fecha_corte')}) e insignia «{enso.get('insignia')}»")
        actual_enso = enso.get("actual")
        if isinstance(actual_enso, (int, float)):
            fase_esperada, semaforo_esperado = (("El Niño", "rojo") if actual_enso >= 0.5 else
                                                ("La Niña", "ambar") if actual_enso <= -0.5 else ("neutra", "verde"))
            texto_enso = " ".join(enso.get("texto") or [])
            # El texto describe la fase que calcula el sistema (umbral ±0.5 °C) sin inventar categorías de intensidad
            verificar(fase_esperada in str(enso.get("fase", "")) and enso.get("semaforo") == semaforo_esperado
                      and fase_esperada in texto_enso
                      and not any(p in texto_enso.lower() for p in ("débil", "moderad", "fuerte", "extraordinari")),
                      f"fase del índice coherente con su valor ({cfg.fmt_anomalia(actual_enso, '°C')}: "
                      f"«{enso.get('fase')}», semáforo {enso.get('semaforo')}): «{texto_enso[:160]}»")
        else:
            verificar(False, f"índice Niño 1+2 sin valor actual ({actual_enso!r})")
        r = c.post("/api/panel/widget/datos", {"tipo": "kpi_variable",
                                               "config": {"fuente": "chirps_v3", "variable": "precip",
                                                          "recorte": "guayas", "dias": 7}}, headers=T)
        verificar(r.status_code == 200 and r.json().get("procedencia") == "real" and r.json().get("unidad") == "mm",
                  "indicador de lluvia acumulada (Guayas) con procedencia «real»")
        r = c.post("/api/panel/widget/datos", {"tipo": "kpi_sistema", "config": {}}, headers=cab(tokens["admin"]))
        verificar(r.status_code == 200 and "consultas_hoy" in r.json(), "kpi_sistema para el administrador")
        codigo(c.post("/api/panel/widget/datos", {"tipo": "kpi_sistema", "config": {}}, headers=T), 403,
               "kpi_sistema prohibido para la oceanógrafa")
        codigo(c.post("/api/panel/widget/datos", {"tipo": "no_existe", "config": {}}, headers=T), 422,
               "tipo de widget desconocido")
        r = c.post("/api/panel/widget/datos", {"tipo": "kpi_variable",
                                               "config": {"fuente": "chirps_v3", "variable": "precip",
                                                          "area": {"tipo": "bbox", "bbox": [-90, -5, -85, -3]}}},
                   headers=T)
        verificar(r.status_code == 200 and "error" in r.json(), "widget con área sin datos devuelve {error}")
        nuevo_panel = {"widgets": panel["widgets"][:3] + [{"id": "w99", "tipo": "mis_vistas", "titulo": "Vistas",
                                                           "config": {}, "ancho": 2}]}
        r = c.put("/api/panel", nuevo_panel, headers=T)
        codigo(r, 200, "PUT /api/panel")
        verificar(len(c.get("/api/panel", headers=T).json()["widgets"]) == 4, "el panel guardado tiene 4 widgets")
        r = c.post("/api/panel/restablecer", headers=T)
        codigo(r, 200, "POST /api/panel/restablecer")
        verificar(len(r.json()["widgets"]) == 8, "restablecer vuelve a los 8 widgets por defecto")
        codigo(c.delete("/api/panel", headers=T), 200, "DELETE /api/panel")
        verificar(len(c.get("/api/panel", headers=T).json()["widgets"]) == 8, "tras borrar se recrea el panel por defecto")

        # --- vistas ------------------------------------------------------------
        seccion("Mis vistas")
        r = c.get("/api/vistas", headers=T)
        codigo(r, 200, "GET /api/vistas (mvera)")
        v = r.json()
        verificar(len(v["propias"]) == 2 and len(v["compartidas"]) == 1, "mvera: 2 propias y 1 compartida (de jloor)")
        privada_mvera = next(x for x in v["propias"] if not x["compartida"])
        r = c.get("/api/vistas", headers=cab(tokens["jloor"]))
        verificar(r.status_code == 200 and len(r.json()["propias"]) == 1 and len(r.json()["compartidas"]) == 1,
                  "jloor: 1 propia y 1 compartida (de mvera)")
        codigo(c.get(f"/api/vistas/{privada_mvera['id']}", headers=cab(tokens["jloor"])), 403,
               "jloor no puede abrir la vista privada de mvera")
        compartida = v["compartidas"][0]
        codigo(c.get(f"/api/vistas/{compartida['id']}", headers=T), 200, "mvera abre la vista compartida de jloor")
        codigo(c.put(f"/api/vistas/{compartida['id']}", {"nombre": "x", "modulo": "series", "parametros": {},
                                                         "compartida": True}, headers=T), 403,
               "mvera no puede editar la vista de jloor")
        codigo(c.delete(f"/api/vistas/{compartida['id']}", headers=T), 403, "mvera no puede eliminar la vista de jloor")
        r = c.post("/api/vistas", {"nombre": "Prueba de humo", "descripcion": "Vista creada por la prueba",
                                   "modulo": "mapa", "parametros": {"fuente": "noaa_oisst", "variable": "sst",
                                                                    "area": "recorte:nino12"},
                                   "compartida": False}, headers=T)
        codigo(r, 201, "POST /api/vistas")
        nueva = r.json()
        verificar(nueva["propietario"]["usuario"] == "mvera" and nueva["creada"], "la vista nueva es de mvera")
        codigo(c.post("/api/vistas", {"nombre": "Sin módulo", "parametros": {}}, headers=T), 422,
               "vista sin módulo")
        codigo(c.post("/api/vistas", {"nombre": "Módulo malo", "modulo": "otro", "parametros": {}}, headers=T), 422,
               "vista con módulo no permitido")
        r = c.put(f"/api/vistas/{nueva['id']}", {"nombre": "Prueba de humo (compartida)", "descripcion": "",
                                                 "modulo": "mapa", "parametros": nueva["parametros"],
                                                 "compartida": True}, headers=T)
        codigo(r, 200, "PUT /api/vistas/{id} (compartir)")
        verificar(r.json()["compartida"] is True, "la vista quedó compartida")
        verificar(c.get(f"/api/vistas/{nueva['id']}", headers=cab(tokens["jloor"])).status_code == 200,
                  "jloor ya puede abrir la vista compartida")
        codigo(c.delete(f"/api/vistas/{nueva['id']}", headers=T), 200, "DELETE /api/vistas/{id}")
        codigo(c.get(f"/api/vistas/{nueva['id']}", headers=T), 404, "la vista eliminada ya no existe")

        # --- administración -------------------------------------------------------
        seccion("Administración")
        A = cab(tokens["admin"])
        for u in ("mvera", "jloor", "rcastro"):
            codigo(c.get("/api/admin/usuarios", headers=cab(tokens[u])), 403, f"GET /api/admin/usuarios como «{u}»")
        codigo(c.get("/api/admin/usuarios"), 401, "GET /api/admin/usuarios sin token")
        r = c.get("/api/admin/usuarios", headers=A)
        codigo(r, 200, "GET /api/admin/usuarios")
        usuarios = r.json()
        verificar(len(usuarios) == 4 and all("clave_hash" not in u and "sal" not in u for u in usuarios),
                  "4 usuarios sin datos secretos")
        admin_id = next(u["id"] for u in usuarios if u["usuario"] == "admin")
        r = c.post("/api/admin/usuarios", {"usuario": "prueba", "nombre": "Usuario de Prueba", "rol": "analista",
                                           "departamento": "Hidrografía", "correo": "prueba@inocar.mil.ec",
                                           "activo": True, "clave": "clave1234"}, headers=A)
        codigo(r, 201, "POST /api/admin/usuarios")
        nuevo_id = r.json().get("id")
        codigo(c.post("/api/admin/usuarios", {"usuario": "prueba", "nombre": "Repetido", "rol": "analista",
                                              "clave": "clave1234"}, headers=A), 422, "usuario repetido")
        codigo(c.post("/api/admin/usuarios", {"usuario": "otro", "nombre": "Sin rol válido", "rol": "jefe",
                                              "clave": "clave1234"}, headers=A), 422, "rol no permitido")
        r = c.post("/api/auth/ingresar", {"usuario": "prueba", "clave": "clave1234"})
        codigo(r, 200, "el usuario nuevo puede ingresar")
        token_prueba = r.json()["token"]
        r = c.put(f"/api/admin/usuarios/{nuevo_id}", {"usuario": "prueba", "nombre": "Usuario de Prueba",
                                                      "rol": "analista", "departamento": "Hidrografía",
                                                      "correo": "prueba@inocar.mil.ec", "activo": False}, headers=A)
        codigo(r, 200, "PUT /api/admin/usuarios/{id} (desactivar)")
        codigo(c.get("/api/auth/yo", headers=cab(token_prueba)), 401, "el usuario desactivado pierde la sesión")
        codigo(c.post("/api/auth/ingresar", {"usuario": "prueba", "clave": "clave1234"}), 403,
               "el usuario desactivado no puede ingresar")
        codigo(c.delete(f"/api/admin/usuarios/{admin_id}", headers=A), 400, "no se puede eliminar al propio administrador")
        codigo(c.delete(f"/api/admin/usuarios/{nuevo_id}", headers=A), 200, "DELETE /api/admin/usuarios/{id}")
        codigo(c.delete(f"/api/admin/usuarios/{nuevo_id}", headers=A), 404, "eliminar dos veces → 404")

        r = c.put("/api/admin/catalogo/fuentes/chirps_v3", {"habilitada": False}, headers=A)
        codigo(r, 200, "PUT /api/admin/catalogo/fuentes/chirps_v3 (deshabilitar)")
        codigo(c.post("/api/consultas/serie", {"fuente": "chirps_v3", "variable": "precip",
                                               "area": {"tipo": "recorte", "recorte": "guayas"},
                                               "periodo": {"inicio": "2026-10-01", "fin": "2026-10-20"}}, headers=T),
               422, "consulta a una fuente deshabilitada")
        codigo(c.put("/api/admin/catalogo/fuentes/chirps_v3", {"habilitada": True}, headers=A), 200,
               "volver a habilitar CHIRPS")
        codigo(c.put("/api/admin/catalogo/variables/copernicus_phy/zos", {"habilitada": False}, headers=A), 200,
               "PUT /api/admin/catalogo/variables/copernicus_phy/zos (deshabilitar)")
        cat = c.get("/api/catalogo", headers=T).json()
        zos = next(v for f in cat["fuentes"] if f["id"] == "copernicus_phy" for v in f["variables"] if v["id"] == "zos")
        verificar(zos["habilitada"] is False, "el catálogo refleja la variable deshabilitada")
        codigo(c.put("/api/admin/catalogo/variables/copernicus_phy/zos", {"habilitada": True}, headers=A), 200,
               "volver a habilitar zos")
        codigo(c.put("/api/admin/catalogo/variables/copernicus_phy/no_existe", {"habilitada": True}, headers=A), 404,
               "variable inexistente")
        codigo(c.put("/api/admin/catalogo/fuentes/chirps_v3", {"habilitada": False}, headers=T), 403,
               "la oceanógrafa no puede cambiar el catálogo")
        r = c.get("/api/admin/auditoria?limite=200", headers=A)
        codigo(r, 200, "GET /api/admin/auditoria")
        auditoria = r.json()
        acciones = {a["accion"] for a in auditoria}
        verificar(len(auditoria) >= 30 and {"ingreso", "salida", "usuario_creado", "usuario_eliminado",
                                            "catalogo_cambiado", "vista_compartida", "reporte_generado"} <= acciones,
                  f"auditoría con {len(auditoria)} entradas y las acciones exigidas")
        verificar(all({"fecha", "usuario", "accion", "detalle", "ip"} <= set(a) for a in auditoria),
                  "cada entrada de auditoría tiene fecha, usuario, acción, detalle e ip")
        r = c.get("/api/admin/sistema", headers=A)
        codigo(r, 200, "GET /api/admin/sistema")
        s = r.json()
        verificar(s["version"] == cfg.VERSION and s["consultas_total"] > 20 and s["consultas_hoy"] > 20
                  and s["usuarios_activos"] >= 4 and {"entradas", "aciertos", "fallos"} <= set(s["cache"])
                  and s["trabajos"]["listos"] >= 5 and s["datos_simulados"] is (not todas_reales),
                  f"sistema con métricas coherentes (datos_simulados {s.get('datos_simulados')})")
        verificar(s.get("procedencia") == proc_sistema and s.get("fuentes_reales") == reales_esperadas
                  and s.get("fecha_corte", corte_noaa) == corte_noaa
                  and isinstance(s.get("chirps"), dict) and s["chirps"].get("datos_reales") is True
                  and "dias_en_cache" in s["chirps"],
                  f"sistema con procedencia «{s.get('procedencia')}», la fecha de corte de NOAA y resumen de la "
                  "caché de CHIRPS")
        rn = s.get("noaa")
        if NOAA.bloque:
            verificar(isinstance(rn, dict) and "error" not in rn and rn.get("fuente") == "noaa_oisst"
                      and rn.get("datos_reales") is NOAA.real and "dias_en_cache" in rn and "climatologia" in rn
                      and (os.environ.get("SOME_NOAA_SIN_SINCRONIZACION") != "1"
                           or rn.get("sincronizacion_activa") is False),
                      f"sistema con el resumen de NOAA OISST (modo «{(rn or {}).get('modo')}», "
                      f"{(rn or {}).get('dias_en_cache')} días en caché, {(rn or {}).get('dias_preliminares')} "
                      "preliminares, sincronización detenida en la prueba)")
        else:
            verificar(rn is None or isinstance(rn, dict), "sistema tolera la falta del paquete de NOAA")
        rc = s.get("copernicus")
        if COP.bloque:
            verificar(isinstance(rc, dict) and "error" not in rc and rc.get("fuente") == "copernicus_phy"
                      and rc.get("datos_reales") is COP.real and "dias_en_cache" in rc and "climatologia" in rc
                      and (os.environ.get("SOME_COPERNICUS_SIN_SINCRONIZACION") != "1"
                           or rc.get("sincronizacion_activa") is False),
                      f"sistema con el resumen de Copernicus Marine (modo «{(rc or {}).get('modo')}», "
                      f"{(rc or {}).get('dias_en_cache')} días en caché, sincronización detenida en la prueba)")
        else:
            verificar(rc is None or isinstance(rc, dict), "sistema tolera la falta del paquete de Copernicus")

        # --- interfaz y rutas inexistentes --------------------------------------
        seccion("Interfaz estática")
        r = c.get("/")
        codigo(r, 200, "GET / (index.html)")
        verificar(b"<title>SOME" in r.content, "index.html contiene el título del SOME")
        r = c.get("/panel")
        verificar(r.status_code == 200 and b"<title>SOME" in r.content, "ruta no-API devuelve index.html")
        codigo(c.get("/js/app.js"), 200, "GET /js/app.js")
        r = c.get("/api/no/existe", headers=T)
        codigo(r, 404, "ruta de la API inexistente")
        verificar("detalle" in (r.json() or {}), "el 404 de la API usa {detalle}")

        # --- rendimiento ---------------------------------------------------------
        seccion("Rendimiento (sin caché de consultas)")
        t0 = time.perf_counter()
        r = c.post("/api/consultas/mapa", {"fuente": "noaa_oisst", "variable": "sst",
                                           "area": {"tipo": "recorte", "recorte": "area_estudio"},
                                           "fecha": "2026-03-15", "modo": "anomalia"}, headers=T)
        t_mapa = time.perf_counter() - t0
        verificar(r.status_code == 200 and t_mapa < 2.0, f"mapa de un día en {t_mapa * 1000:.0f} ms (< 2 s)")
        cuerpo_serie_rend = {"fuente": "copernicus_phy", "variable": "so",
                             "area": {"tipo": "recorte", "recorte": "golfo_guayaquil"},
                             "periodo": {"inicio": (CORTE_COP - timedelta(days=364)).isoformat(), "fin": corte_cop},
                             "agregacion": "mensual", "profundidad": 10}
        if COP.real:
            # Con Copernicus real, una consulta de calentamiento sin cronometrar llena la caché local (si falta algo
            # y hay acceso, puede descargar: si responde 503 «sigue descargándose», se reintenta) y después se
            # vacía la caché de consultas: el tiempo medido es el de una lectura de la caché local, como en la demo.
            for intento in range(6):
                r = c.post("/api/consultas/serie", cuerpo_serie_rend, headers=T)
                if r.status_code != 503:
                    break
                print(f"   (calentamiento {intento + 1}: {r.detalle[:100]}…; se reintenta en 15 s)")
                time.sleep(15)
            print(f"   calentamiento de la serie de Copernicus: {r.status_code} «{(r.json() or {}).get('procedencia')}»")
        cache.cache_consultas.limpiar()
        t0 = time.perf_counter()
        r = c.post("/api/consultas/serie", cuerpo_serie_rend, headers=T)
        t_serie = time.perf_counter() - t0
        verificar(r.status_code == 200 and t_serie < 3.0, f"serie de 12 meses en {t_serie * 1000:.0f} ms (< 3 s)")
        if r.status_code == 200:
            verificar(COP.coherente(r.json()), f"serie de 12 meses de Copernicus con procedencia «{r.json().get('procedencia')}»")
        t0 = time.perf_counter()
        c.post("/api/consultas/serie", cuerpo_serie_rend, headers=T)
        print(f"   la misma serie repetida (caché de consultas): {(time.perf_counter() - t0) * 1000:.0f} ms")

        # --- salida ----------------------------------------------------------------
        seccion("Salida")
        codigo(c.post("/api/auth/salir", headers=T), 200, "POST /api/auth/salir")
        codigo(c.get("/api/auth/yo", headers=T), 401, "el token ya no sirve tras salir")
        for u in ("jloor", "rcastro", "admin"):
            codigo(c.post("/api/auth/salir", headers=cab(tokens[u])), 200, f"salir «{u}»")
    finally:
        c.cerrar()
        trabajos.detener()
        almacen.olvidar_memoria()
        shutil.rmtree(_CARPETA_TEMPORAL, ignore_errors=True)

    print(f"\nTiempo total: {time.perf_counter() - inicio_total:.1f} s · {TOTAL - len(FALLAS)}/{TOTAL} verificaciones correctas")
    if FALLAS:
        print(f"\nPrueba de humo con {len(FALLAS)} falla(s):")
        for f in FALLAS:
            print(f" - {f}")
        return 1
    print("\nPrueba de humo correcta: todos los endpoints respondieron como se esperaba.")
    return 0


if __name__ == "__main__":
    raise SystemExit(ejecutar())
