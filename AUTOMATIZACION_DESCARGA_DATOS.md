# Automatizacion de descarga de datos Freshdesk

## Windows

Ejecuta `Set-ExecutionPolicy -Scope Process Bypass` y luego
`.\setup_windows.ps1`. Copia `.env.example` como `.env` y coloca la API key
real. Para probar sin publicar usa `.\update_freshdesk.ps1 -NoPull -NoPush`.
Con GitHub ya configurado, `.\update_freshdesk.ps1` descarga el dia anterior,
hace commit y push. `.\register_scheduled_task.ps1` registra el proceso diario
a las 07:00; acepta otra hora con `-At "08:30"`. El log queda en
`logs/fetch_daily.log`.

Este documento explica como quedo automatizada la descarga diaria de actividades
de tickets de Freshdesk y como ejecutarla manualmente o de forma periodica.

## Que se automatizo

Antes el flujo era manual:

1. Cambiar la fecha en la API de Freshdesk.
2. Copiar la URL temporal que devolvia Freshdesk.
3. Pegar esa URL en Power BI o descargarla manualmente.
4. Guardar el JSON final en la carpeta del dashboard.

Ahora el script `scripts/fetch_ticket_activities.py` hace esos pasos por ti:

1. Consulta el endpoint:

```text
https://igniteonline.freshdesk.com/api/v2/export/ticket_activities?created_at=YYYY-MM-DD
```

2. Lee la URL temporal de S3 que devuelve Freshdesk.
3. Descarga el JSON final con la llave `activities_data`.
4. Guarda el archivo en la raiz del proyecto con el formato que ya usa el dashboard:

```text
activities_3748365_D_M_YYYY.json
```

Por ejemplo:

```text
activities_3748365_8_7_2026.json
```

El dashboard Streamlit detecta esos archivos automaticamente porque carga todos
los archivos que cumplen el patron `activities_*.json`.

## Archivos agregados

```text
scripts/fetch_ticket_activities.py
.env.example
.gitignore
```

`scripts/fetch_ticket_activities.py` es el script principal.

`.env.example` sirve como plantilla para configurar credenciales.

`.gitignore` evita guardar `.env`, `__pycache__/` y archivos `.pyc`.

## Configuracion inicial

Entra a la carpeta del proyecto:

```bash
cd "/home/luis-roberto-polo-bautista/Escritorio/freshdesk dash"
```

Crea el archivo `.env` desde la plantilla:

```bash
cp .env.example .env
```

Edita `.env`:

```bash
nano .env
```

Debe quedar asi:

```bash
FRESHDESK_DOMAIN=igniteonline.freshdesk.com
FRESHDESK_API_KEY=TU_API_KEY_REAL_DE_FRESHDESK
```

Guarda en nano con `Ctrl + O`, Enter, y sal con `Ctrl + X`.

Importante: usa la API key de **API para emision de tickets**, no la API key de
CRM/Sales y no la contrasena de login.


## Arrancar la app

source venv/bin/activate
streamlit run freshdesk_dashboard.py 


## Ejecutar una descarga manual

Para descargar un dia:

```bash
python scripts/fetch_ticket_activities.py --date 2026-07-08
```

Salida esperada:

```text
[OK] 2026-07-08 -> activities_3748365_8_7_2026.json (27 actividades)
```

Si el archivo ya existe, el script lo salta:

```text
[SKIP] 2026-07-08 -> activities_3748365_8_7_2026.json ya existe
```

Para forzar la descarga y sobrescribir el archivo:

```bash
python scripts/fetch_ticket_activities.py --date 2026-07-08 --overwrite
```

## Descargar un rango de fechas

```bash
python scripts/fetch_ticket_activities.py --start-date 2026-06-20 --end-date 2026-07-08
```

El script descarga solo los dias que no existan localmente. Los dias ya
descargados aparecen como `[SKIP]`.

## Recargar el dashboard

Despues de descargar datos, ejecuta la app:

```bash
streamlit run freshdesk_dashboard.py
```

Si la app ya estaba abierta, presiona **Recargar datos** en el sidebar.

## Ejecucion periodica con cron

Cron es la opcion mas simple para ejecutar la descarga automaticamente.

Abre el editor de cron:

```bash
crontab -e
```

Agrega esta linea para descargar todos los dias a las 7:00 AM:

```cron
0 7 * * * cd "/home/luis-roberto-polo-bautista/Escritorio/freshdesk dash" && /usr/bin/python3 scripts/fetch_ticket_activities.py --date "$(date +\%F)" >> logs/freshdesk_download.log 2>&1
```

Antes de usar cron, crea la carpeta de logs:

```bash
mkdir -p logs
```

Nota: cron usa un entorno limitado. Por eso el script lee `.env` directamente
desde la carpeta del proyecto.

### Descargar el dia anterior

Si Freshdesk tarda en consolidar datos del dia, puede convenir descargar siempre
el dia anterior:

```cron
0 7 * * * cd "/home/luis-roberto-polo-bautista/Escritorio/freshdesk dash" && /usr/bin/python3 scripts/fetch_ticket_activities.py --date "$(date -d yesterday +\%F)" >> logs/freshdesk_download.log 2>&1
```

## Ejecucion periodica con systemd timer

Otra opcion en Ubuntu es usar `systemd`, aunque para este caso cron es mas
simple porque permite calcular facilmente la fecha con `date`.

Si quieres usar `systemd`, crea primero un script auxiliar:

```bash
nano run_freshdesk_download.sh
```

Contenido:

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "/home/luis-roberto-polo-bautista/Escritorio/freshdesk dash"
/usr/bin/python3 scripts/fetch_ticket_activities.py --date "$(date -d yesterday +%F)"
```

Dale permisos:

```bash
chmod +x run_freshdesk_download.sh
```

Crea la carpeta de unidades de usuario:

```bash
mkdir -p ~/.config/systemd/user
```

Crea el servicio:

```bash
nano ~/.config/systemd/user/freshdesk-download.service
```

Contenido:

```ini
[Unit]
Description=Descarga diaria actividades Freshdesk

[Service]
Type=oneshot
WorkingDirectory=/home/luis-roberto-polo-bautista/Escritorio/freshdesk dash
ExecStart=/home/luis-roberto-polo-bautista/Escritorio/freshdesk dash/run_freshdesk_download.sh
```

Crea el timer:

```bash
nano ~/.config/systemd/user/freshdesk-download.timer
```

Contenido:

```ini
[Unit]
Description=Ejecuta descarga diaria Freshdesk

[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Activalo:

```bash
systemctl --user daemon-reload
systemctl --user enable --now freshdesk-download.timer
```

Verifica su estado:

```bash
systemctl --user list-timers freshdesk-download.timer
```

## Diagnostico de errores comunes

### HTTP 401 Unauthorized

Significa que Freshdesk rechazo la autenticacion.

Revisa:

1. Que `.env` exista en la raiz del proyecto.
2. Que `FRESHDESK_API_KEY` sea la API key de tickets.
3. Que `FRESHDESK_DOMAIN` sea `igniteonline.freshdesk.com`.
4. Que no estes usando la API key de CRM/Sales.
5. Que la key no tenga espacios, comillas extra o texto placeholder.

### El archivo ya existe

Si ves `[SKIP]`, no es error. El archivo ya estaba descargado.

Usa `--overwrite` si quieres reemplazarlo.

### El dashboard no muestra datos nuevos

Presiona **Recargar datos** en Streamlit. Si sigue igual, verifica que el archivo
descargado este en la raiz del proyecto y empiece con `activities_`.

## Recomendacion de seguridad

No guardes contrasenas de Freshdesk en el repositorio. Usa solo `.env` para la
API key y manten `.env` ignorado por git.

Si una API key fue compartida accidentalmente en chat, correo o capturas, lo
mejor es regenerarla desde Freshdesk y actualizar `.env`.
