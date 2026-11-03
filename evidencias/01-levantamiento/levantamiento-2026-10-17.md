# Reunión de levantamiento del 17/10/2026

**Técnica:** entrevista y reunión de levantamiento.

**Participantes:**
- Del INOCAR: oceanógrafos de la Dirección de Oceanografía y Meteorología Marina y representantes de otros departamentos.
- Del curso: el equipo T5.

Este es el resumen que el equipo elaboró con sus notas de la reunión. El acta, las notas originales y las grabaciones, si existen, se agregan en esta carpeta.

## Situación actual

- Los oceanógrafos usan a diario temperatura, salinidad, anomalías, capa de mezcla, corrientes, viento y precipitación de tres fuentes: NOAA, Copernicus Marine Service y CHIRPS.
- No programan. Piden scripts de Python a una herramienta de inteligencia artificial y los editan a mano para cada sitio, variable y periodo.
- El resultado depende de quién ejecuta el script y de la configuración de cada computadora.
- Las descargas históricas se repiten en cada equipo, y las series de unos 30 años no caben en las computadoras personales.

## Datos útiles

- **Formatos habituales:** NetCDF (`.nc`) y CSV. También usan archivos en formato ORM; su estructura queda por confirmar con una muestra.
- **Meses más críticos:** febrero y octubre, por el inicio de la estación lluviosa y la evolución de El Niño.
- **Destino principal de los productos:** los informes quincenales del CN-ERFEN (Comité Nacional para el Estudio Regional del Fenómeno El Niño).
- **Otros departamentos:** quieren centralizar sus datos en la misma plataforma.

## Necesidades expresadas

1. Consultar las tres fuentes sin programar, para cualquier área, periodo, variable y profundidad.
2. Visualizar con rapidez:
   - mapas;
   - series por píxel o por área;
   - perfiles verticales;
   - comparaciones;
   - climatologías y anomalías.
3. Generar reportes en PDF y exportaciones (CSV, NetCDF e imagen) en minutos.
4. Trabajar de forma personalizada: panel propio, áreas favoritas y vistas guardadas.
5. Disponer de la historia completa dentro del instituto, sin descargarla en cada computadora.
6. Crecer sin rehacer el sistema: más fuentes, variables, departamentos y usuarios.

## Restricciones del cliente

- El backend y el motor de procesamiento deben estar en Python, para reutilizar las bibliotecas que el personal ya conoce.
- Los datos deben permanecer en el centro de datos del INOCAR.
- La interfaz, los reportes y la ayuda deben estar en español.

## Dónde quedó cada punto

| Punto | Documento |
|---|---|
| Necesidades | CONOPS v1.3, secciones 2 y 3 |
| Escenarios | CONOPS v1.3, sección 5.8 |
| Requerimientos | SRS v1.0, sección 3 |
