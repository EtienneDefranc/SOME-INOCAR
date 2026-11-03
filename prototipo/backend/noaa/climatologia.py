"""Climatologías de NOAA OISST v2.1 para el SOME: mensual (tres periodos) y diaria 1991-2020 de la región Niño 1+2.

Mensual, en la malla SOME (anomalías de los mapas y series):

* **1991-2020**: la oficial de NOAA PSL, `sst.mon.ltm.1991-2020.nc` (media de las
  medias mensuales de 1991 a 2020); si no se pudo obtener, calculada igual con
  `sst.mon.mean.nc`.
* **1981-2010** y **1993-2022**: calculadas por el SOME con las medias mensuales
  `sst.mon.mean.nc` (una sola petición NCSS de toda la serie, ~5.6 MB, ~25 s).
  OISST empieza en septiembre de 1981: en 1981-2010, enero a agosto usan
  1982-2010 (29 años) y septiembre a diciembre 1981-2010. Cada climatología guarda
  el primer y el último año y el número de años de cada mes, y el texto del periodo
  EFECTIVO lo declara (como `periodo_efectivo` de Copernicus Marine).

Diaria 1991-2020 de la región Niño 1+2 oficial (para el índice): `sst.day.mean.ltm.1991-2020.nc`
(365 días del año 0001), solo las 40 × 40 celdas de la región, en 4 peticiones de ~90 días
(el archivo entero supera el tiempo máximo del proxy de PSL).

Todo se pasa a la malla SOME con la misma regla que los datos diarios (`malla.a_some`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .. import configuracion as cfg
from . import almacen, estado, malla, red
from .constantes import (ARCHIVO_LTM_DIARIA, ARCHIVO_LTM_MENSUAL, ARCHIVO_MENSUAL, DIAS_TRIMESTRE_LTM, DOI, PERIODOS,
                         PRIMER_MES_OISST, ErrorConexion, ErrorFormato, ErrorNOAA, ErrorSinDatos)

log = logging.getLogger("some.noaa")

FUENTE_OFICIAL = ARCHIVO_LTM_MENSUAL
FUENTE_CALCULADA = ARCHIVO_MENSUAL
def credito(c: "ClimatologiaNOAA") -> str:
    """Crédito de una climatología: la oficial es de NOAA PSL; las demás las calcula el SOME y no son productos
    de NOAA (su licencia prohíbe presentar datos modificados como originales de NOAA). Lleva el DOI del conjunto,
    que toda respuesta real de NOAA cita; con el crédito de los datos no pasa de 195 caracteres."""
    if c.fuente == FUENTE_OFICIAL:
        return f"Climatología {c.periodo} de NOAA PSL (OISST, doi:{DOI})."
    return f"Climatología {c.periodo} calculada por el INOCAR con OISST (doi:{DOI})."


@dataclass(frozen=True)
class ClimatologiaNOAA:
    periodo: str                       # el pedido («1981-2010»)
    valores: np.ndarray                # (12, 49, 69) float32
    anio_inicio: list                  # 12 primeros años efectivos
    anio_fin: list                     # 12 últimos años efectivos
    anios: list                        # 12 números de años
    fuente: str                        # sst.mon.ltm.1991-2020.nc | sst.mon.mean.nc
    obtenido: str = ""
    nino12: np.ndarray | None = field(default=None, compare=False)

    @property
    def por_mes(self) -> list[str]:
        return [f"{a}-{b}" for a, b in zip(self.anio_inicio, self.anio_fin)]

    @property
    def completa(self) -> bool:
        a0, a1 = PERIODOS[self.periodo]
        return all(a == a0 and b == a1 for a, b in zip(self.anio_inicio, self.anio_fin))

    @property
    def periodo_efectivo(self) -> str:
        return texto_periodo_efectivo(self.periodo, self.anio_inicio, self.anio_fin)


# ---------------------------------------------------------------------------
# Textos
# ---------------------------------------------------------------------------
def _rango_meses(meses: list[int]) -> str:
    """[1..8] → «enero–agosto»; [9] → «septiembre»; [1, 3] → «enero y marzo»."""
    if not meses:
        return ""
    if len(meses) > 1 and meses == list(range(meses[0], meses[-1] + 1)):
        return f"{cfg.MESES_LARGOS[meses[0] - 1]}–{cfg.MESES_LARGOS[meses[-1] - 1]}"
    nombres = [cfg.MESES_LARGOS[m - 1] for m in meses]
    return ", ".join(nombres[:-1]) + (" y " if len(nombres) > 1 else "") + nombres[-1]


def texto_periodo_efectivo(periodo: str, anio_inicio, anio_fin) -> str:
    """El periodo pedido si todos los meses lo cubren; si no, los meses que no: «1982-2010 en enero–agosto».

    El motor lo muestra como «1981-2010 (1982-2010 en enero–agosto)»: los meses no nombrados usan el
    periodo pedido."""
    a0, a1 = PERIODOS[periodo]
    grupos: dict[tuple[int, int], list[int]] = {}
    for m, (a, b) in enumerate(zip(anio_inicio, anio_fin), start=1):
        if (int(a), int(b)) != (a0, a1):
            grupos.setdefault((int(a), int(b)), []).append(m)
    if not grupos:
        return periodo
    return "; ".join(f"{a}-{b} en {_rango_meses(meses)}" for (a, b), meses in sorted(grupos.items()))


def texto_metodo(c: ClimatologiaNOAA) -> str:
    regla = ("llevada a la malla SOME con la media de las celdas nativas válidas de cada 2 × 2 (al menos 2 de 4)")
    if c.fuente == FUENTE_OFICIAL:
        return (f"Climatología mensual {c.periodo} oficial de NOAA PSL (sst.mon.ltm.1991-2020.nc: media de las "
                f"medias mensuales de OISST v2.1 de 1991 a 2020), {regla}.")
    # El periodo efectivo (OISST empieza en septiembre de 1981) lo explica `texto_origen`, que acompaña
    # siempre a este método: aquí no se repite.
    return (f"Climatología mensual {c.periodo} calculada por el SOME con las medias mensuales de NOAA OISST v2.1 "
            f"(sst.mon.mean.nc de NOAA PSL): para cada mes, promedio de los años del periodo con dato, {regla}.")


def texto_origen(c: ClimatologiaNOAA, pedido: str | None = None) -> str:
    pedido = pedido or c.periodo
    texto = f"Climatología de NOAA OISST v2.1 (NOAA PSL), periodo {c.periodo}"
    texto += " (oficial de NOAA)." if c.fuente == FUENTE_OFICIAL else " (calculada por el SOME con las medias mensuales)."
    if not c.completa:
        faltan = [m for m, (a, b) in enumerate(zip(c.anio_inicio, c.anio_fin), start=1)
                  if (a, b) != PERIODOS[c.periodo]]
        texto += (f" OISST empieza en septiembre de 1981, así que {_rango_meses(faltan)} usan "
                  f"{texto_periodo_efectivo(c.periodo, c.anio_inicio, c.anio_fin).split(' en ')[0]} "
                  f"({min(c.anios)} años).")
    return texto


# ---------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------
def calcular(meses_iso: list[str], nativo: np.ndarray, periodo: str) -> tuple[np.ndarray, list, list, list]:
    """(clim nativa (12, 65, 70), anio_inicio, anio_fin, anios) del periodo con las medias mensuales nativas."""
    a0, a1 = PERIODOS[periodo]
    anios_mes = np.array([int(x[:4]) for x in meses_iso])
    mes_mes = np.array([int(x[5:7]) for x in meses_iso])
    clim = np.full((12,) + nativo.shape[1:], np.nan, dtype=np.float32)
    ini, fin, n = [], [], []
    for m in range(1, 13):
        sel = (mes_mes == m) & (anios_mes >= a0) & (anios_mes <= a1)
        if not sel.any():
            raise ErrorSinDatos(f"Las medias mensuales no cubren {cfg.MESES_LARGOS[m - 1]} de {periodo}.")
        with np.errstate(invalid="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                clim[m - 1] = np.nanmean(nativo[sel], axis=0)
        anios = anios_mes[sel]
        ini.append(int(anios.min()))
        fin.append(int(anios.max()))
        n.append(int(anios.size))
    return clim, ini, fin, n


def _guardar_periodo(periodo: str, clim_nativa: np.ndarray, ini, fin, n, fuente: str) -> None:
    almacen.guardar_climatologia(periodo, malla.a_some(clim_nativa), ini, fin, n, fuente,
                                 nino12=malla.region_nino12(clim_nativa))


def preparar_mensual(*, respetar_pausa: bool = True) -> dict:
    """Descarga las medias mensuales y la climatología oficial 1991-2020 (2 peticiones a PSL) y guarda las tres
    climatologías. Devuelve {"peticiones", "bytes", "periodos", "dif_1991_2020", "meses"}."""
    r = {"peticiones": 0, "bytes": 0, "periodos": [], "dif_1991_2020": None, "meses": 0}
    contenido, _lat = red.ncss_todo(ARCHIVO_MENSUAL, respetar_pausa=respetar_pausa)
    r["peticiones"] += 1
    r["bytes"] += len(contenido)
    ds = malla.abrir_netcdf(contenido)
    fechas = malla.fechas_de(ds)
    nativo = malla.ventana(ds, "sst")
    meses_iso = [f"{d.year:04d}-{d.month:02d}" for d in fechas]
    if not meses_iso or meses_iso[0] != f"{PRIMER_MES_OISST[0]:04d}-{PRIMER_MES_OISST[1]:02d}":
        raise ErrorFormato(f"La serie mensual empieza en {meses_iso[0] if meses_iso else '—'}, no en 1981-09.")
    almacen.guardar_mensual_nativo(meses_iso, nativo)
    r["meses"] = len(meses_iso)
    calculadas = {}
    for periodo in PERIODOS:
        calculadas[periodo] = calcular(meses_iso, nativo, periodo)
    # 1991-2020 oficial (si falla, queda la calculada)
    oficial = None
    try:
        contenido, _lat = red.ncss_todo(ARCHIVO_LTM_MENSUAL, respetar_pausa=respetar_pausa)
        r["peticiones"] += 1
        r["bytes"] += len(contenido)
        ds = malla.abrir_netcdf(contenido)
        oficial = malla.ventana(ds, "sst")
        if oficial.shape[0] != 12:
            raise ErrorFormato(f"La climatología mensual oficial trae {oficial.shape[0]} meses.")
    except ErrorNOAA as e:
        log.warning("NOAA: no se pudo obtener %s (%s); se usa la climatología 1991-2020 calculada.",
                    ARCHIVO_LTM_MENSUAL, e)
        estado.registrar_error(f"Climatología mensual oficial 1991-2020: {e}")
        oficial = None
    for periodo, (clim, ini, fin, n) in calculadas.items():
        if periodo == "1991-2020" and oficial is not None:
            with np.errstate(invalid="ignore"):
                dif = np.abs(oficial - clim)
            r["dif_1991_2020"] = round(float(np.nanmax(dif)), 4) if np.isfinite(dif).any() else None
            _guardar_periodo(periodo, oficial, [1991] * 12, [2020] * 12, [30] * 12, FUENTE_OFICIAL)
        else:
            _guardar_periodo(periodo, clim, ini, fin, n, FUENTE_CALCULADA)
        r["periodos"].append(periodo)
    return r


def preparar_ltm_diaria(*, respetar_pausa: bool = True, progreso=None) -> dict:
    """Climatología diaria 1991-2020 de la región Niño 1+2 (4 peticiones a PSL). {"peticiones", "bytes"}."""
    r = {"peticiones": 0, "bytes": 0}
    valores = np.full((365, 40, 40), np.nan, dtype=np.float32)
    vistos = np.zeros(365, dtype=bool)
    for k, (d0, d1) in enumerate(DIAS_TRIMESTRE_LTM):
        if progreso:
            progreso(k, len(DIAS_TRIMESTRE_LTM), f"climatología diaria, días {d0}–{d1}")
        contenido, _lat = red.ncss_ltm_diaria(d0, d1, respetar_pausa=respetar_pausa)
        r["peticiones"] += 1
        r["bytes"] += len(contenido)
        ds = malla.abrir_netcdf(contenido)
        dias = malla.dias_del_anio_ltm(ds)
        campos = malla.ventana(ds, "sst", malla.lat_nino12(), malla.lon_nino12())
        for i, dia in enumerate(dias):
            valores[dia - 1] = campos[i]
            vistos[dia - 1] = True
    if not vistos.all():
        raise ErrorFormato(f"La climatología diaria quedó incompleta ({int(vistos.sum())} de 365 días).")
    almacen.guardar_ltm_diaria(valores, ARCHIVO_LTM_DIARIA)
    if progreso:
        progreso(len(DIAS_TRIMESTRE_LTM), len(DIAS_TRIMESTRE_LTM), "listo")
    return r


def climatologia_mensual(periodo: str, *, llenar: bool = False) -> ClimatologiaNOAA:
    """Climatología mensual del periodo desde la caché (con `llenar`, la descarga si falta y hay acceso).

    ValueError si el periodo no existe; ErrorSinDatos si no está en la caché y no se pudo obtener."""
    if periodo not in PERIODOS:
        raise ValueError(f"Periodo de referencia desconocido: «{periodo}». Disponibles: {', '.join(PERIODOS)}.")
    d = almacen.leer_climatologia(periodo)
    if d is None and llenar and estado.acceso_disponible():
        try:
            preparar_mensual()
        except ErrorConexion as e:
            raise ErrorSinDatos(f"la climatología {periodo} no está en la caché local y NOAA PSL no respondió: "
                                f"{estado.inicial_minuscula(str(e).rstrip('.'))}") from e
        except ErrorNOAA as e:
            raise ErrorSinDatos(f"la climatología {periodo} no está en la caché local y no se pudo obtener: "
                                f"{estado.inicial_minuscula(str(e).rstrip('.'))}") from e
        d = almacen.leer_climatologia(periodo)
    if d is None:
        motivo = estado.motivo_sin_acceso()
        raise ErrorSinDatos(f"la climatología {periodo} de NOAA OISST no está en la caché local"
                            + (f" y no se puede obtener: {motivo}" if motivo else
                               " (ejecute python -m backend.herramientas.preparar_noaa --climatologia)"))
    return ClimatologiaNOAA(periodo=periodo, valores=d["valores"], anio_inicio=d["anio_inicio"],
                            anio_fin=d["anio_fin"], anios=d["anios"], fuente=d["fuente"], obtenido=d["obtenido"],
                            nino12=d["nino12"])


def periodos_faltantes() -> list[str]:
    en_cache = set(almacen.climatologias_en_cache())
    return [p for p in PERIODOS if p not in en_cache]
