# Exportación por contacto e institución

Implementación para [la entrega de Freshdesk en Notion](https://app.notion.com/p/41d40e1f2a2c8180b8abd9c98f6db8de).
La herramienta está integrada en la pestaña **Exportación por institución/contacto**
del dashboard. También se puede ejecutar en PowerShell mediante `export_freshdesk.ps1`.
Laura realiza el análisis; una persona autorizada
con credenciales de Freshdesk ejecuta la extracción.

## Desde el dashboard

1. Abrir **Exportación por institución/contacto**.
2. Introducir el dominio y la API key personal de Freshdesk, y pulsar **Cargar contactos e instituciones**.
   No se utiliza automáticamente la clave compartida del servidor. Cada sesión usa
   los permisos de su propia clave; no se guardan credenciales en las descargas.
3. Revisar **Revisar asociaciones de contactos** para ver contactos sin institución
   y posibles duplicados. Se puede descargar la auditoría desde ahí.
4. Elegir **Institución** o **Contacto** y seleccionar el registro por nombre e ID.
   Opcionalmente cargar el CSV de cruce provisional.
5. Elegir fechas, producto y estados, o dejarlos vacíos para incluir el histórico.
   Las notas internas requieren marcar su opción explícita.
6. Pulsar **Consultar y preparar descarga**, revisar conteos y cobertura por contacto,
   y descargar CSV, JSON o ZIP. El ZIP incluye conversaciones, notas cuando se pidieron,
   histórico disponible y reportes de cobertura. Los errores se señalan como resultado parcial.

Los filtros laterales de actividades no afectan esta pestaña; también se puede usar
si esos filtros no dejan tickets visibles. Los resultados pertenecen a la última
consulta ejecutada: después de cambiar filtros se debe pulsar de nuevo **Consultar**.
Se mantienen solamente en la sesión; los archivos temporales se eliminan al terminar
de preparar la descarga. Cambiar la clave o el dominio elimina los resultados de la
sesión. **Borrar resultados de esta sesión** permite quitarlos explícitamente.

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
