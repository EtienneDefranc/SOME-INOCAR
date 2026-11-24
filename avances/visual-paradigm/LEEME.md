# Diagramas de SOME para Visual Paradigm

Scripts PlantUML de los diagramas del adelanto, para importarlos en Visual Paradigm y seguir editándolos ahí.

| Archivo | Diagrama |
|---|---|
| `CU-1-acceso-y-administracion.puml` | Casos de uso: acceso y administración |
| `CU-2-consulta-visualizacion-climatologias.puml` | Casos de uso: consulta, visualización y climatologías |
| `CU-3-reportes-exportacion-personalizacion.puml` | Casos de uso: reportes, exportación y personalización |
| `CU-4-lago-de-datos-y-escalamiento.puml` | Casos de uso: lago de datos (Fase 2) y escalamiento (Fase 3) |
| `AD-01` a `AD-07` | Diagramas de actividades, con un carril por participante |

## Cómo importarlos

1. En Visual Paradigm, abra o cree el proyecto de SOME.
2. Importe cada archivo con la opción de PlantUML (según las guías de Visual Paradigm, **Tools > Import > PlantUML**; el menú puede cambiar entre versiones y ediciones).
3. Si su versión no muestra esa opción, cree un diagrama nuevo del mismo tipo, abra su editor de PlantUML y pegue el contenido del archivo, desde `@startuml` hasta `@enduml`.
4. Visual Paradigm ordena los elementos de forma automática: después de importar, acomode el diagrama a mano para que no se crucen las líneas.

## Notas

- Los archivos están en UTF-8; si aparecen mal las tildes o la «ñ», vuelva a abrirlos con esa codificación.
- Los estereotipos `<<Fase 2>>`, `<<Fase 3>>` y `<<Por definir>>` marcan los casos de uso que no son de la Fase 1 o que todavía no tienen requerimiento.
- La vista general por áreas (figura 1 del documento) es un resumen de los cuatro diagramas de casos de uso y no tiene un script propio.
