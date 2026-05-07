# 📡 Freshdesk Intelligence Dashboard

Dashboard de análisis completo para actividades exportadas de Freshdesk.

## Instalación

```bash
pip install -r requirements.txt
```

## Uso

1. Coloca tus archivos `activities_*.json` en una carpeta (o en el mismo directorio).
2. Ejecuta:

```bash
streamlit run freshdesk_dashboard.py
```

3. En el panel lateral, ajusta la ruta al directorio de archivos JSON.
4. Opcionalmente, mapea los IDs de agentes a nombres reales.

## Estructura de archivos esperada

Los archivos deben seguir el patrón:
```
activities_<ticket_group_id>_<DD>_<M>_<YYYY>.json
```

Por ejemplo:
```
activities_3748365_15_4_2026.json
activities_3748365_16_4_2026.json
activities_3748365_17_4_2026.json
...
```

El dashboard detecta automáticamente todos los archivos que coincidan con ese patrón.
Cuando se añadan nuevos archivos, presiona **"Recargar archivos"** en el sidebar.

## Funcionalidades

| Tab | Contenido |
|-----|-----------|
| 📈 Overview | KPIs globales, actividad diaria, mapa de calor horario, distribución de tipos |
| 🔍 Trazabilidad | Timeline por ticket, brechas entre eventos, log de auditoría descargable |
| ⏱️ SLA & Tiempos | TTFR, tiempo de resolución, brecha máxima, tabla SLA con highlighting |
| 👥 Agentes | Carga por agente, tipos de actividad, score de influencia ponderado |
| ⚠️ Cuellos de Botella | Score de problema por ticket, top brechas, alertas automáticas |
| 🕸️ Grafo | Grafo Agente↔Ticket o Agente→Acción→Ticket con centralidad de nodos |
| 📋 Audit Log | Log completo filtrable y exportable a CSV |

## Tipos de actividad detectados

- `Ticket Creado` — nuevo ticket abierto
- `Respuesta Pública` — nota tipo 0 (visible al cliente)
- `Nota Privada` — nota tipo 4 (solo agentes)
- `Reenvío` — nota tipo 3
- `Cambio de Estado` — Open, Pending, Closed, Esperando por cliente...
- `Automatización` — reglas de automatización del sistema
- `Campo Actualizado` — Producto, Prioridad, ticket_type, SLA, etc.
- `Asignación` — cambio de agente o grupo
- `Etiqueta Añadida` — tags añadidos
- `Fecha Límite` — cambio en due_by

## Score de influencia de agentes (ponderación)

```
score = actividades×1 + tickets_únicos×2 + respuestas_públicas×3 +
        cambios_estado×2 + notas_privadas×1.5 + reenvíos×2
```

## Score de problema de tickets

```
+3  SLA de primera respuesta incumplido
+3  SLA de resolución incumplido
+2  Brecha máxima entre eventos supera umbral
+3  Ticket reabierto
+1  Más de 5 intercambios
+1  Más de 2 agentes involucrados
+1  Sin resolver
```
