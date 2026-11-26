# Exportación por contacto e institución

Implementación para [la entrega de Freshdesk en Notion](https://app.notion.com/p/41d40e1f2a2c8180b8abd9c98f6db8de).
La herramienta está integrada en la pestaña **Exportación por institución/contacto**
del dashboard y utiliza los archivos locales existentes. No pide API key y no
consulta Freshdesk. Laura realiza el análisis de los datos disponibles.

## Desde el dashboard

1. Abrir **Exportación por institución/contacto**.
2. Los contactos e instituciones se cargan automáticamente desde los archivos del proyecto.
3. Elegir **Institución** o **Contacto** y seleccionar el registro por nombre e ID.
   **Contactos y pendientes de este grupo** muestra sus asociaciones, cruces
   provisionales, candidatos ambiguos y posibles duplicados. El selector incluye
   **Sin institución / pendientes de asignar** para consultar los contactos no resueltos.
   El botón **Descargar cruce de este grupo** prepara el cruce por identificadores.
4. Elegir fechas, producto y estados, o dejarlos vacíos para incluir el histórico.
   Las notas internas requieren marcar su opción explícita.
5. Pulsar **Consultar y preparar descarga**, revisar conteos y cobertura por contacto,
   y descargar CSV, JSON o ZIP. El ZIP incluye conversaciones, notas cuando se pidieron,
   histórico disponible y reportes de cobertura. Las referencias a respuestas sin
   cuerpo se identifican como texto no disponible, sin inventar conversaciones.

Los filtros laterales de actividades no afectan esta pestaña; también se puede usar
si esos filtros no dejan tickets visibles. Los resultados pertenecen a la última
consulta ejecutada: después de cambiar filtros se debe pulsar de nuevo **Consultar**.
Se mantienen solamente en la sesión y se preparan en memoria. Si cambian los archivos
de origen, se invalida la descarga anterior. **Borrar resultados de esta sesión**
permite quitarlos explícitamente.

### Fuentes y asociación local

- `3748365/Tickets*.json`: asunto, descripción, solicitante, agente, fechas, estado,
  producto, campos de institución y SLA disponibles. Se distingue `display_id`
  (número de ticket) de `id` (identificador interno).
- `actores/Users*.json` y `actores/AllAgents*.json`: nombres e IDs de personas.
- `activities_*.json`: cambios posteriores al respaldo e histórico disponible.
- `institution_directory.json`: nombres de instituciones, alias explícitos e IDs
  de asociaciones verificadas. No contiene credenciales ni textos de conversaciones.
- `actores/Companies*.json`, cuando existan: catálogo local adicional de instituciones.

Se une el solicitante por ID, nunca por coincidencia de nombre. Se prefieren
`company_id` y los cruces verificados del catálogo. Cuando un contacto sin asociación
aparece con una sola institución en sus tickets, se crea un cruce provisional
identificado como `provisional_from_ticket_institution`. Si aparecen varias, queda
como ambiguo. No se asigna por similitud de nombres o dominios de correo.

La consulta de institución reúne los tickets de sus contactos vinculados. Los
tickets que mencionan esa institución pero tienen un contacto sin asociación resuelta
se listan aparte y se incluyen en `institution_unresolved_tickets.csv`, sin mezclarlos
con los confirmados. Ese reporte es previo a filtros y sirve para revisar cobertura.
Los contactos sin tickets también aparecen en el reporte de cobertura.

Valores vacíos, `N/A`, `null`, `Sin institución` y equivalentes no cuentan como
instituciones ni generan falsos conflictos. Los posibles duplicados por nombre,
correo o ID externo se señalan dentro de sus grupos, sin fusionarlos ni trasladar
sus tickets a otro contacto. Un duplicado puede apuntar a un ID de otro grupo;
eso se muestra para revisión, sin incorporarlo automáticamente.

El ZIP contiene `contact_institution_mapping.csv` y `contacts_unassigned.csv`.
El cruce distingue `recorded` (asociación registrada en el respaldo), `verified`
(cruce verificado local), `provisional` (evidencia única en tickets) y `unassigned`.
Incluye IDs de contacto, institución, tickets de evidencia, candidatos e incidencias.
Cuando no hay ID de institución de Freshdesk se conserva una clave local estable
`name:...`, dejando `company_id` vacío; nunca se inventa un ID de Freshdesk.
La revisión refleja los archivos locales disponibles, no el estado actual de Freshdesk,
y no modifica la plataforma. Los cruces verificados se mantienen en el catálogo local.

Los nombres y correos de contactos, institución del contacto e institución del ticket
acompañan a los IDs en CSV/JSON. Las notas internas disponibles se guardan por separado
y solo si se marca su opción. La consulta no hereda el corte de abril del dashboard:
usa todo el histórico que contienen los archivos locales.

**Límite de los archivos actuales:** las actividades incluyen referencias a notas
(`id` y `type`), pero no el cuerpo de las respuestas. Las descripciones sí se
conservan completas cuando existen. La herramienta muestra cuántas referencias no
tienen texto; no certifica conversaciones completas ni datos posteriores al respaldo.
Actualizar los archivos del proyecto es lo que actualiza esta pestaña.

## Utilidad opcional por API, independiente del dashboard

Las secciones siguientes describen `export_freshdesk.ps1`, conservado como herramienta
de administración para consultar Freshdesk directamente. Esta utilidad sí necesita
credenciales; **la pestaña del dashboard no la ejecuta ni depende de ella**.

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
