#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="/home/luis-roberto-polo-bautista/Escritorio/freshdesk dash"
PYTHON="$PROJECT_DIR/venv/bin/python"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/fetch_daily.log"
LOCK_FILE="/tmp/freshdesk_fetch.lock"

mkdir -p "$LOG_DIR"

exec 9>"$LOCK_FILE"

if ! flock -n 9; then
    echo "$(date '+%F %T') - Ya existe una ejecución activa." >> "$LOG_FILE"
    exit 0
fi

cd "$PROJECT_DIR"

if [[ ! -d ".git" ]]; then
    echo "$(date '+%F %T') - ERROR: no es un repositorio Git." >> "$LOG_FILE"
    exit 1
fi

PROCESS_DATE="$(date -d 'yesterday' '+%F')"

echo "========================================" >> "$LOG_FILE"
echo "$(date '+%F %T') - Procesando $PROCESS_DATE" >> "$LOG_FILE"

# Actualizar primero desde GitHub.
git pull --rebase origin main >> "$LOG_FILE" 2>&1

# Descargar las actividades de ayer.
"$PYTHON" scripts/fetch_ticket_activities.py \
    --date "$PROCESS_DATE" >> "$LOG_FILE" 2>&1

# Agregar únicamente archivos de actividades.
git add -- 'activities_'*.json

if git diff --cached --quiet; then
    echo "$(date '+%F %T') - No hay cambios para subir." >> "$LOG_FILE"
else
    git commit \
        -m "Freshdesk activities $PROCESS_DATE" \
        >> "$LOG_FILE" 2>&1

    git push origin main >> "$LOG_FILE" 2>&1

    echo "$(date '+%F %T') - Cambios publicados en GitHub." >> "$LOG_FILE"
fi

echo "$(date '+%F %T') - Proceso finalizado." >> "$LOG_FILE"
