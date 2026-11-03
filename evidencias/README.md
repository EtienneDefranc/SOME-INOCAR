# Evidencias de levantamiento de requerimientos y metodología

El equipo siguió el proceso de ingeniería de requerimientos de ISO/IEC/IEEE 29148 en ciclos cortos, en este orden:

1. Levantamiento con el cliente.
2. Concepto de Operaciones (CONOPS), versiones 1.0 a 1.3.
3. Prototipo demostrativo para validar lo entendido.
4. Especificación de Requerimientos de Software (SRS).

La sección 5 del SRS describe cada técnica.

| Técnica | Carpeta | Qué hay |
|---|---|---|
| Entrevista y reunión de levantamiento | `01-levantamiento/` | Resumen de la reunión del 17/10/2026 con los oceanógrafos de la Dirección de Oceanografía y Meteorología Marina |
| Análisis de documentos y artefactos | `02-analisis-documentos/` | Análisis de las bibliotecas de los scripts de Python de los oceanógrafos y estudio de bibliotecas para el motor |
| Escenarios operacionales, modelado de características y priorización MoSCoW | `03-conops/` | CONOPS v1.3 (escenarios, capacidades, modelo de características y requerimientos priorizados) y sus diagramas |
| Inspección de requerimientos | `04-inspeccion/` | Documento de requerimientos y formulario R02 entregados a los inspectores en el taller del curso |
| Investigación técnica | `05-investigacion/` | Condiciones de uso y licencias de las fuentes; los diseños de los conectores están en `../prototipo/docs/` |
| Consultas y decisiones del cliente | `06-decisiones/` | Decisiones del INOCAR que cambiaron requerimientos |
| Prototipado | `../prototipo/` | Prototipo demostrativo con datos reales de las tres fuentes |
