# Exportación por contacto e institución

## Desde el dashboard

1. Abrir **Exportación por institución/contacto**. Elegir **Texto completo desde Freshdesk** e introducir dominio y API key en el campo protegido.
2. Pulsar **Actualizar lista de contactos e instituciones** para incorporar el catálogo actual. También se reconcilia al preparar la descarga.
3. Elegir **Institución** o **Contacto**, el nombre agrupado y las fechas **Desde / Hasta (UTC)**.
4. Marcar **Incluir notas internas accesibles** si se necesitan.
5. Pulsar **Consultar y preparar descarga**, revisar cobertura e incidencias y **Descargar Excel**. JSON y ZIP son complementarios; el ZIP incluye Excel y los archivos CSV/JSON.

Se incluyen tickets creados **o con movimientos en el rango**, sin excluir abiertos, pendientes, resueltos ni cerrados. Los movimientos se detectan por actividades locales, fechas de actualización, conversaciones y estadísticas disponibles. Para cada ticket seleccionado se conserva todo su histórico disponible; las respuestas y actividades indican si ocurrieron dentro del rango. Los filtros laterales no afectan esta consulta. Tras cambiar la selección, hay que preparar nuevamente la descarga; se identifica la última consulta preparada.

## Agrupación y contactos pendientes

Las instituciones se normalizan por mayúsculas, acentos, puntuación y espacios, y por alias explícitos en `institution_directory.json`. `id_aliases` permite reconocer IDs equivalentes de empresa. Las abreviaturas o equivalencias adicionales requieren una regla explícita, no una coincidencia aproximada automática.

Los contactos se agrupan por correo exacto normalizado, identificador externo o `verified_contact_groups`. Cada ticket mantiene el ID real del solicitante. Dos personas con el mismo nombre y distintos identificadores permanecen separadas. Los buzones compartidos deben revisarse como posibles casos ambiguos.

**Contactos y pendientes de este grupo** muestra las asociaciones de los contactos de la consulta:

- **Registrada:** institución del contacto en el catálogo disponible.
- **Verificada:** cruce explícito mantenido en el directorio local.
- **Provisional:** evidencia única de institución en tickets, o asociación del respaldo que no está completa en el catálogo actual.
- **Sin asignar:** no se ha resuelto la asociación. Se conservan los candidatos e incidencias.
- **Posibles duplicados:** registros que requieren revisión; compartir nombre no provoca una fusión.

Cuando un ticket identifica explícitamente la institución pero su contacto no está asociado, se incluye **ese ticket** con su motivo de selección. No se incorporan automáticamente los demás tickets del mismo contacto. El reporte de pendientes indica `included_in_export` para distinguir los incluidos. La funcionalidad no modifica asociaciones en Freshdesk.

## Contenido del Excel

- **Resumen:** consulta, fechas, cobertura y límites.
- **Tickets:** IDs, asunto, descripción completa y HTML, contacto, institución, estado, fechas, agente, producto y SLA disponible.
- **Respuestas** y, si se solicitaron, **Notas_internas:** texto completo, HTML, autor, fechas y adjuntos como enlaces/metadatos.
- **Actividades / Cambios_estado:** eventos locales completos, actores, fecha y archivo de origen. El estado anterior se muestra solo cuando existe una observación previa. Una creación deducida de `created_at` está identificada como tal.
- **Contactos / Cobertura / Normalizacion / Incidencias:** IDs, asociaciones, conteos, alias, posibles duplicados, errores y días sin archivo.
- **Textos_largos:** contenido que supera el límite de una celda, dividido sin recortes en partes numeradas. Para reconstruirlo, concatenar `Texto` por `Referencia` y `Parte`, sin añadir separadores. Caracteres incompatibles con XML se codifican como cadena JSON y se indica su codificación.

Los textos se escriben como texto, no como fórmulas de Excel. La clave no se guarda en archivos, descargas ni caché compartida. Las respuestas remotas y descargas se mantienen en la sesión; cambiar dominio, clave o modo invalida los resultados anteriores.

## Fuentes y cobertura

Se reutilizan `3748365/Tickets*.json`, `actores/Users*.json`, `actores/AllAgents*.json`, `actores/Companies*.json` cuando existan, `institution_directory.json` y `activities_*.json`. Los eventos repetidos se deduplican y mantienen su archivo de origen.

La primera actividad de los datos revisados es **2026-04-15**. La interfaz y el Excel calculan la fecha inicial/final real y los días sin archivo. Puede haber tickets creados antes, pero no se reconstruyen cambios anteriores al inicio de recolección ni eventos de archivos ausentes. Un día sin archivo tampoco demuestra por sí solo que hubo movimientos perdidos.

Con API se actualizan empresas/contactos y se consultan los tickets por IDs de solicitante y empresa desde el inicio permitido, recorriendo las páginas. Se reutilizan los datos locales y se agregan tickets nuevos descubiertos; después se recuperan todas las páginas de conversaciones accesibles y se aplica el rango. Véase la [documentación oficial de Freshdesk](https://developer.freshdesk.com/api/).

**Solo datos locales** funciona sin clave, pero no garantiza el texto completo: muchas actividades contienen únicamente IDs de notas. Fallos de API o de archivos se señalan como resultado parcial, conservando el respaldo disponible. Un ticket inaccesible cuya fecha no pueda comprobarse se conserva marcado como fecha no verificable para revisión.

Una consulta sin errores no certifica todos los tickets de contactos desconocidos o sin asociación ni tickets ocultos por permisos, eliminados, spam, fusionados o archivados no accesibles. La API no reconstruye el histórico de cambios ausente; los adjuntos se conservan como enlaces, no como archivos binarios.

## Validación técnica

Ejecutar `.\.venv\Scripts\python.exe -m unittest discover -s tests -q`. Las pruebas cubren agrupación por identificadores, separación de homónimos, selección por movimientos, incorporación de tickets por API, aislamiento de tickets de otra institución, privacidad, textos extensos y funcionamiento de la interfaz.

## Utilidad opcional por API, independiente del dashboard

Las secciones siguientes describen `export_freshdesk.ps1`, conservado como herramienta
de administración para consultar Freshdesk directamente. Esta utilidad sí necesita
credenciales. El dashboard no ejecuta el comando PowerShell; reutiliza el cliente
de API cuando se elige descargar el contenido completo.

## Preparación y permisos

Usar Python de `.venv` generado por `setup_windows.ps1` y la configuración local
`.env` (`FRESHDESK_DOMAIN`, `FRESHDESK_API_KEY`). Se necesitan permisos para leer
contactos, instituciones, tickets, conversaciones y agentes. El alcance de tickets
de esa cuenta debe permitir ver toda la institución, no solamente tickets asignados
al agente. La API no permite demostrar por sí sola que no hay datos ocultos por permisos.

Los archivos se guardan en `exports/`, excluida de Git. Esto evita su publicación
accidental en el repositorio, pero no es un control de acceso del sistema operativo.
Compartir solo con personal autorizado mediante una ubicación con permisos
restringidos. No usar el script de actualización diaria para publicar estos archivos.

## Consultas demostrables

Todos los tickets accesibles cuyo solicitante es uno de los contactos de UTP:

```powershell
cd "C:\Users\oswal\Desktop\freshdesk_metrics-main"
.\export_freshdesk.ps1 -CompanyId 203000530476
```

Todos los tickets accesibles de un contacto, por ID estable:

```powershell
.\export_freshdesk.ps1 -ContactId 203009343356
```

Agregar `-IncludePrivateNotes` únicamente para una entrega a personal autorizado
a leer notas internas. Se guardan en archivos separados, nunca en el CSV de respuestas públicas.

Filtros combinables; las fechas se aplican al ticket y son inclusivas en UTC.
La exportación conserva todas las conversaciones del ticket seleccionado, aunque
una conversación quede fuera del rango. Sin fechas se consulta todo el histórico
accesible desde 1970, no solo los últimos 30 días.

```powershell
.\export_freshdesk.ps1 -CompanyId 203000530476 -StartDate "2026-08-01" -EndDate "2026-08-31" -DateField created_at
.\export_freshdesk.ps1 -CompanyId 203000530476 -Product "Koha" -Status 4,5
```

`-Product` compara el valor exacto (sin distinguir mayúsculas) de `cf_producto`.
`-ProductId` filtra el campo nativo `product_id`; son campos diferentes.
Estados estándar: 2 abierto, 3 pendiente, 4 resuelto, 5 cerrado; se pueden pasar
otros códigos configurados en la cuenta.

Para la extracción semanal usar `-DateField updated_at` con el rango de la semana,
de modo que entren tickets antiguos que recibieron cambios. Se exporta el contenido
completo de cada ticket que cumpla el filtro, no solamente los mensajes de esa semana.
Por ejemplo, si la primera semana operativa se acuerda del 4 al 10 de enero de 2027:

```powershell
.\export_freshdesk.ps1 -CompanyId 203000530476 -StartDate "2027-01-04" -EndDate "2027-01-10" -DateField updated_at
```

La capacidad manual está disponible; no se registra una tarea programada ni se
supone una fecha operativa sin confirmación del equipo.

## Auditoría y cruce provisional

```powershell
.\export_freshdesk.ps1 -Action audit
```

Se descargan todas las páginas de contactos e instituciones. Se identifican
contactos sin institución, asociaciones múltiples, instituciones no disponibles y
posibles duplicados por correo (incluye correos secundarios), identificador externo
o nombre. Coincidir por nombre no basta para fusionar contactos.

El cruce usa `contact.id` con `company.id`; los tickets se unen por `requester_id`.
Incluye instituciones secundarias de `other_companies`. No une por nombre ni correo.
Una asociación actual no reconstruye cambios de institución a lo largo del tiempo.

Cuando Freshdesk aún no tenga la asociación, guardar un CSV privado en `exports/`
con columnas `contact_id,company_id,status,evidence`. `status` debe ser `verified`
o `provisional`; la evidencia debe explicar el origen. Ejemplo de estructura con
IDs ficticios (reemplazarlos por IDs existentes):

```csv
contact_id,company_id,status,evidence
123,456,provisional,"Pendiente de confirmación; referencia al padrón autorizado"
```

```powershell
.\export_freshdesk.ps1 -CompanyId 203000530476 -Mapping .\exports\cruce.csv
```

Las filas provisionales se identifican en `mapping_source` y `mapping_used.json`;
no modifican Freshdesk. El reporte global `contacts_unassigned.csv` conserva los
contactos aún sin institución. No se completan asociaciones por intuición.

Para aplicar asociaciones ya verificadas:

```powershell
.\export_freshdesk.ps1 -Action apply-mapping -Mapping .\exports\cruce_verificado.csv
```

Este comando es el único que escribe en Freshdesk. Requiere todas las filas con
`status=verified`, valida los IDs, rechaza asociaciones contradictorias, guarda el
estado previo y lee de nuevo cada contacto después de actualizarlo. No fusiona
contactos, no cambia tickets ni concede acceso para ver todos los tickets.
Si una ejecución falla a medias, revisar `mapping_changes_applied.json` antes de
reintentar; los cambios ya verificados permanecen aplicados.

## Archivos y validación

- `tickets.csv` y `tickets.json`: ID, asunto, descripción completa, conversaciones
  públicas, solicitante, institución, estado, prioridad, fechas, agente, producto,
  campos personalizados, plazos SLA, escalaciones y estadísticas que entregue la API.
- `conversations_public.csv`: una fila por conversación pública; incluye respuesta
  o nota pública, autor, dirección entrante/saliente, fechas y texto/HTML completo.
- `notes_private.csv` y `.json`: notas privadas, solo con la opción explícita.
- `activity_history_available.json`: eventos originales de los archivos de
  actividades presentes localmente con el nombre de archivo de origen.
- `coverage_by_contact.csv`: tickets por contacto antes/después de filtros e IDs;
  incluye contactos con cero tickets.
- `company_tickets_unmapped.json`: tickets de la consulta de institución cuyo
  solicitante no está en el cruce; no se atribuyen automáticamente a un contacto.
- `contact_institution_audit.csv`, `contacts_unassigned.csv`, `possible_duplicates.csv`:
  revisión de asociaciones y casos pendientes.
- `manifest.json`: conteos, diferencias entre consultas, errores, límites y SHA-256.
- `api_pages.json`: páginas y cantidad de registros recibidos para comprobar paginación.

Comparar la unión de los IDs por contacto con los IDs del archivo de institución.
Revisar `requester_ticket_ids_absent_from_company_query`: la institución del ticket
puede seguir vacía después de asociar al contacto; por eso la extracción usa la
unión por solicitantes. Revisar también `company_query_unmapped_ids` y el respaldo
local. Un error de paginación o conversación produce salida incompleta y código 2;
no entregar como completa sin resolverlo.

El CSV conserva saltos de línea y comillas mediante escape estándar. Excel puede
limitar la visualización de celdas largas; el JSON es el complemento para conservar
el texto íntegro. Para evitar fórmulas al abrir CSV, los valores con prefijo de
fórmula llevan apóstrofo; los originales permanecen en JSON. Los hashes verifican
integridad de archivos, no la cobertura de información inaccesible en Freshdesk.

## Límites que deben acompañar la entrega

La consulta por contacto significa **solicitante del ticket**, no toda persona
mencionada, copiada o participante en una conversación. La extracción respeta los
permisos de la API. No certifica recuperar tickets eliminados, spam, fusionados o
todos los archivados. Usa los IDs de tickets del respaldo local como contraste y
trata de recuperar los que no aparecen en la lista, señalando fallos.

La API de conversaciones conserva los mensajes disponibles, pero el histórico
de cambios de estado/asignación depende de los archivos diarios locales; no está
certificado completo. Los días faltantes no se interpretan como días sin actividad.
Los adjuntos se conservan como metadatos y enlaces, sin descargar sus binarios.
Los SLA se exportan tal como los entrega Freshdesk, sin recalcularlos con las
reglas aproximadas del dashboard.

Documentación técnica de referencia: [API oficial de Freshdesk](https://developers.freshdesk.com/api/).

## Verificación del código

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Antes de considerar cerrada la entrega, guardar el paquete en el repositorio de
archivos privado acordado, verificar que Laura puede abrir CSV/JSON y registrar
ese enlace en la tarea. No afirmar que el equipo tiene acceso hasta comprobarlo.
