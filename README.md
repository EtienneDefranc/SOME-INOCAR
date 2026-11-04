# SOME · Sistema de Observación del Mar Equinoccial

Repositorio del equipo T5 de Ingeniería de Software I (FIEC, ESPOL) para el Instituto Oceanográfico y Antártico de la Armada (INOCAR).

SOME es una plataforma web interna, en español, para que los oceanógrafos del INOCAR consulten, visualicen, analicen, exporten y reporten, sin escribir código, los datos de tres fuentes externas:

- la temperatura superficial del mar de la NOAA (OISST v2.1);
- las variables físicas del océano de Copernicus Marine Service;
- la precipitación de CHIRPS v3.

Este repositorio reúne dos cosas: el prototipo demostrativo del sistema y las evidencias de las técnicas de levantamiento de requerimientos y de la metodología seguida.

## Contenido

| Carpeta o archivo | Qué contiene |
|---|---|
| `SOME-Especificacion-de-Requerimientos-v1.0.pdf` | Especificación de Requerimientos de Software (SRS) v1.0 |
| `prototipo/` | Código del prototipo demostrativo, su especificación, los diseños de los conectores y las capturas de pantalla |
| `prototipo/docs/SOME-Navegacion-del-Oceanografo.pdf` | Recorrido paso a paso de un oceanógrafo por el prototipo, con capturas anotadas |
| `evidencias/` | Evidencias del levantamiento y de la metodología, ordenadas por técnica (ver `evidencias/README.md`) |

## Ejecutar el prototipo

Requiere Python 3.12 o posterior en Windows. Las instrucciones completas, las variables de entorno y los usuarios de demostración están en `prototipo/README.md`. En resumen:

1. Instalar las dependencias: `pip install -r prototipo/requirements.txt` (y, para las fuentes reales, `requirements-copernicus.txt` y `requirements-replica.txt`).
2. Para Copernicus Marine, registrar una cuenta gratuita y guardar las credenciales con `copernicusmarine login`.
3. Definir `SOME_CHIRPS_CONTACTO` con un correo o una URL de contacto, que viaja en el agente de usuario de las peticiones al Climate Hazards Center.
4. Ejecutar `prototipo/ejecutar.bat` y abrir http://localhost:8050.

Las cachés de datos no se incluyen: el prototipo las descarga la primera vez, respetando los límites de ritmo de cada proveedor.

## Créditos de los datos

- NOAA OISST v2.1: Huang et al. (2021), doi:10.25921/RE9P-PT57, NOAA NCEI y NOAA PSL.
- Copernicus Marine: «Generated using E.U. Copernicus Marine Service Information», productos GLOBAL_ANALYSISFORECAST_PHY_001_024 y GLOBAL_MULTIYEAR_PHY_001_030.
- CHIRPS v3: Climate Hazards Center, UC Santa Barbara (Funk et al., 2026), doi:10.15780/G2JQ0P, licencia CC BY 4.0; réplica de dynamical.org.

El detalle de las licencias y las condiciones de uso está en `evidencias/05-investigacion/CONDICIONES-USO-FUENTES.md`.

## Equipo T5

Francisco Etienne Defranc Rojas, Christian David Olmedo Borbor, Niurka María Roca Chévez y Samuel Alejandro Villagómez Rodríguez.

Los documentos publicados aquí tienen tachados los datos de contacto personales (teléfonos, correos y matrículas). Los usuarios del prototipo son ficticios.
