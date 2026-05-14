#!/usr/bin/env python3
"""
Freshdesk Ticket Intelligence Dashboard
Trazabilidad completa · SLA real · Cuellos de botella · Grafo de relaciones
Uso: streamlit run freshdesk_dashboard.py
"""

import streamlit as st
import pandas as pd
import json
import glob
import os
import hashlib
from datetime import datetime
import plotly.express as px
import plotly.graph_objects as go
import networkx as nx
from collections import defaultdict

# ─── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Freshdesk Monitor",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─── CUSTOM CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600;700&display=swap');

:root {
    --bg: #0a0e1a;
    --surface: #111827;
    --surface2: #1a2235;
    --border: #243044;
    --accent: #4f8ef7;
    --accent2: #38d9a9;
    --orange: #f59f00;
    --red: #f03e3e;
    --purple: #9775fa;
    --text: #e2e8f0;
    --muted: #64748b;
    --green: #40c057;
}

html, body, .stApp {
    background-color: var(--bg) !important;
    font-family: 'IBM Plex Sans', sans-serif;
    color: var(--text);
}

/* Sidebar */
[data-testid="stSidebar"] {
    background: var(--surface) !important;
    border-right: 1px solid var(--border);
}

/* Metric cards */
.metric-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 24px; }
.metric-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px 16px;
    text-align: center;
    position: relative;
    overflow: hidden;
}
.metric-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--accent), var(--accent2));
}
.metric-val {
    font-size: 2rem;
    font-weight: 700;
    font-family: 'IBM Plex Mono', monospace;
    color: var(--accent);
    line-height: 1.1;
}
.metric-val.warn { color: var(--orange); }
.metric-val.danger { color: var(--red); }
.metric-val.ok { color: var(--green); }
.metric-lbl {
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    color: var(--muted);
    margin-top: 6px;
}

/* Section header */
.sec-header {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: var(--accent);
    border-bottom: 1px solid var(--border);
    padding-bottom: 6px;
    margin: 20px 0 14px 0;
}

/* Timeline event */
.tl-event {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 8px 12px;
    border-radius: 6px;
    background: var(--surface2);
    margin-bottom: 6px;
    border-left: 3px solid var(--border);
    font-size: 0.83rem;
    font-family: 'IBM Plex Mono', monospace;
}
.tl-event.type-created { border-left-color: var(--green); }
.tl-event.type-public  { border-left-color: var(--accent); }
.tl-event.type-private { border-left-color: var(--purple); }
.tl-event.type-status  { border-left-color: var(--orange); }
.tl-event.type-auto    { border-left-color: var(--muted); }

/* Alert badge */
.alert-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 600;
    margin-right: 4px;
}
.badge-red   { background: rgba(240,62,62,0.2); color: #ff6b6b; }
.badge-orange{ background: rgba(245,159,0,0.2);  color: #ffd43b; }
.badge-green { background: rgba(64,192,87,0.15); color: #69db7c; }
</style>
""", unsafe_allow_html=True)

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def parse_dt(s: str):
    try:
        return datetime.strptime(s, "%d-%m-%Y %H:%M:%S %z")
    except Exception:
        return None

def classify_act(act: dict) -> str:
    if 'new_ticket' in act:
        return 'Ticket Creado'
    if 'note' in act:
        return {0: 'Respuesta Pública', 3: 'Reenvío', 4: 'Nota privada'}.get(
            act['note'].get('type', 0), 'Nota')
    if 'status' in act:
        return 'Cambio de Estado'
    if 'automation' in act:
        return 'Automatización'
    known = ['Producto', 'Prioridad', 'Tiempos SLA', 'ticket_type', 'Empresa',
             'Primer tiempo de respuesta', 'Link o dirección asociada']
    if any(k in act for k in known):
        return 'Campo Actualizado'
    if 'added_tags' in act:
        return 'Etiqueta Añadida'
    if 'agent_id' in act or 'group' in act:
        return 'Asignación'
    if 'due_by' in act:
        return 'Fecha Límite'
    if 'added_watcher' in act:
        return 'Observador Añadido'
    if 'send_reply_email' in act or 'send_email' in act:
        return 'Email Enviado'
    return 'Actualización'

def get_detail(act: dict) -> str:
    SOURCES = {1: 'Portal', 2: 'Email', 3: 'Teléfono', 4: 'Chat',
               5: 'Twitter', 6: 'Facebook', 7: 'API'}
    parts = []
    if 'status'       in act: parts.append(f"→ {act['status']}")
    if 'note'         in act: parts.append(f"Nota #{act['note'].get('id', '')}")
    if 'automation'   in act: parts.append(f"Regla: {act['automation'].get('rule', '')}")
    if 'added_tags'   in act: parts.append(f"Tags: {', '.join(act['added_tags'])}")
    if 'Producto'     in act: parts.append(f"Producto: {act['Producto']}")
    if 'Prioridad'    in act: parts.append(f"Prioridad: {act['Prioridad']}")
    if 'ticket_type'  in act: parts.append(f"Tipo: {act['ticket_type']}")
    if 'group'        in act: parts.append(f"Grupo: {act['group']}")
    if 'due_by'       in act: parts.append(f"Due: {str(act['due_by'])[:10]}")
    if 'source'       in act: parts.append(f"Fuente: {SOURCES.get(act['source'], '?')}")
    if 'Primer tiempo de respuesta' in act:
        parts.append(f"SLA: {act['Primer tiempo de respuesta']}")
    return " │ ".join(parts) if parts else "—"

def fmt_hours(h: float) -> str:
    if pd.isna(h) or h is None:
        return '—'
    if h < 1:
        return f"{h*60:.0f}m"
    if h < 24:
        return f"{h:.0f}h"
    days = int(h // 24)
    rem = h % 24
    if rem == 0:
        return f"{h:.0f}h ({days}d)"
    return f"{h:.0f}h ({days}d {rem:.0f}h)"

def dir_hash(directory: str) -> str:
    h = hashlib.md5()
    for f in sorted(glob.glob(os.path.join(directory, "activities_*.json"))):
        stat = os.stat(f)
        h.update(f"{f}{stat.st_size}{stat.st_mtime}".encode())
    return h.hexdigest()

# ─── DATA LOADING ─────────────────────────────────────────────────────────────

@st.cache_data(show_spinner="⏳ Cargando actividades...")
def load_df(directory: str, _file_hash: str, agent_names_tuple: tuple) -> pd.DataFrame:
    agent_names = dict(agent_names_tuple)
    files = sorted(glob.glob(os.path.join(directory, "activities_*.json")))
    raw = []
    for fp in files:
        with open(fp, encoding='utf-8') as f:
            raw.extend(json.load(f).get('activities_data', []))

    rows = []
    for a in raw:
        dt = parse_dt(a['performed_at'])
        pid = str(a.get('performer_id', 'system'))
        ptype = a['performer_type']
        act = a['activity']

        if ptype == 'system':
            pname = '⚙️ Sistema'
        else:
            pname = agent_names.get(pid, f"Agente …{pid[-4:]}")

        priority = act.get('Prioridad', None)
        producto = act.get('Producto', None)
        
        rows.append({
            'timestamp':      dt,
            'date':           dt.date() if dt else None,
            'hour':           dt.hour if dt else None,
            'weekday':        dt.strftime('%A') if dt else None,
            'ticket_id':      f"#{a['ticket_id']}",
            'ticket_num':     a['ticket_id'],
            'priority':       priority,
            'producto':       producto,
            'performer_type': ptype,
            'performer_id':   pid,
            'performer_name': pname,
            'activity_type':  classify_act(act),
            'detail':         get_detail(act),
            'status_change':  act.get('status') if 'new_ticket' not in act else None,
            'raw':            json.dumps(act, ensure_ascii=False)[:300],
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values('timestamp').reset_index(drop=True)
    return df

SLA_CONFIG = {
    'Urgente':  {'resp': 1,   'all_resp': 1,   'res': 24,   'hours': 24},
    'Alta':     {'resp': 4,   'all_resp': 6,   'res': 168,  'hours': 24},
    'Media':    {'resp': 8,   'all_resp': 24,  'res': 360,  'hours': 24},
    'Baja':     {'resp': 24,  'all_resp': 48,  'res': 360,  'hours': 24},
}


def compute_sla(df: pd.DataFrame, sla_resp_h: int, sla_res_h: int) -> pd.DataFrame:
    rows = []
    for tnum, grp in df.groupby('ticket_num'):
        grp = grp.sort_values('timestamp')
        times = grp['timestamp'].dropna().tolist()
        
        priority_vals = grp['priority'].dropna().unique()
        ticket_priority = priority_vals[0] if len(priority_vals) > 0 else 'Media'
        if ticket_priority not in SLA_CONFIG:
            ticket_priority = 'Media'
        
        sla_cfg = SLA_CONFIG[ticket_priority]
        
        used_resp = sla_cfg['resp']
        used_res = sla_cfg['res']

        # Creation event
        c_rows = grp[grp['activity_type'] == 'Ticket Creado']
        created_at = c_rows['timestamp'].iloc[0] if len(c_rows) else None

        # First public response by a human
        pub = grp[(grp['activity_type'] == 'Respuesta Pública') & (grp['performer_type'] == 'user')]
        first_resp = pub['timestamp'].iloc[0] if len(pub) else None

        # All public responses
        all_pub = grp[(grp['activity_type'] == 'Respuesta Pública') & (grp['performer_type'] == 'user')]
        all_resp_times = all_pub['timestamp'].tolist() if len(all_pub) else []
        
        # Last public response (to consider all response times)
        last_resp = all_resp_times[-1] if all_resp_times else None

        # Resolution (status → Closed)
        closed = grp[grp['status_change'].isin(['Closed'])]
        resolved_at = closed['timestamp'].iloc[0] if len(closed) else None

        # Last known status
        sc = grp[grp['status_change'].notna()]
        last_status = sc['status_change'].iloc[-1] if len(sc) else ('Closed' if resolved_at else 'Open')

        # Reopens: Closed then Open
        reopen_count = 0
        last_was_closed = False
        for s in grp['status_change'].dropna():
            if s == 'Closed':
                last_was_closed = True
            elif s in ('Open',) and last_was_closed:
                reopen_count += 1
                last_was_closed = False

        # Gap analysis
        gaps_h = [(times[i+1] - times[i]).total_seconds() / 3600
                  for i in range(len(times) - 1)] if len(times) > 1 else []

        ttfr = (first_resp - created_at).total_seconds() / 3600 if (first_resp and created_at) else None
        ttr  = (resolved_at - created_at).total_seconds() / 3600 if (resolved_at and created_at) else None
        
        max_resp_gap = 0
        if len(all_resp_times) > 1:
            max_resp_gap = max([(all_resp_times[i+1] - all_resp_times[i]).total_seconds() / 3600 
                                for i in range(len(all_resp_times) - 1)])

        # SLA flags
        ttfr_breach = ttfr is not None and ttfr > used_resp
        all_resp_breach = max_resp_gap > sla_cfg['all_resp'] if max_resp_gap > 0 else False
        ttr_breach  = ttr  is not None and ttr  > used_res
        gap_max     = round(max(gaps_h), 2) if gaps_h else 0

        agents = grp[grp['performer_type'] == 'user']['performer_name'].unique().tolist()

        rows.append({
            'ticket_id':      f"#{tnum}",
            'ticket_num':     tnum,
            'priority':      ticket_priority,
            'created_at':    created_at,
            'first_resp_at':  first_resp,
            'resolved_at':   resolved_at,
            'ttfr_h':        round(ttfr, 2) if ttfr is not None else None,
            'ttfr_sla':      used_resp,
            'all_resp_h':    round(max_resp_gap, 2) if max_resp_gap > 0 else None,
            'all_resp_sla':  sla_cfg['all_resp'],
            'resolution_h':  round(ttr, 2)  if ttr  is not None else None,
            'res_sla':       used_res,
            'n_activities':  len(grp),
            'n_exchanges':   len(grp[grp['activity_type'].isin(
                                 ['Respuesta Pública', 'Nota privada', 'Reenvío'])]),
            'max_gap_h':     gap_max,
            'avg_gap_h':     round(sum(gaps_h) / len(gaps_h), 2) if gaps_h else 0,
            'n_agents':      len(agents),
            'agents':        ', '.join(agents),
            'last_status':   last_status,
            'reopen_count':  reopen_count,
            'reopened':      reopen_count > 0,
            'ttfr_breach':   ttfr_breach,
            'all_resp_breach': all_resp_breach,
            'ttr_breach':    ttr_breach,
            'is_resolved':   resolved_at is not None,
        })

    return pd.DataFrame(rows)

# ─── PLOTLY THEME ─────────────────────────────────────────────────────────────
PLOT_CFG = dict(
    template='plotly_dark',
    plot_bgcolor='#111827',
    paper_bgcolor='#111827',
    font_color='#e2e8f0',
    margin=dict(t=40, b=25, l=25, r=25),
)
COLOR_SEQ = ['#4f8ef7', '#38d9a9', '#f59f00', '#f03e3e', '#9775fa',
             '#ffd43b', '#40c057', '#74c0fc', '#ff8787', '#cc5de8']

def apply_theme(fig, height=300):
    fig.update_layout(**PLOT_CFG, height=height)
    return fig

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📡 Freshdesk Dashboard")
    st.caption("Dashboard de trazabilidad completa")
    st.divider()

    data_dir = st.text_input(
        "📁 Directorio de archivos JSON",
        value=".",
        help="Carpeta con archivos activities_*.json"
    )

    st.markdown("**👤 Mapeo de Agentes**")
    st.caption("Formato → ID:Nombre (una por línea)")
    agent_names_raw = st.text_area(
        "Agentes",
        value=(
            "203002881655:Laura Martinez\n"
            "203009116427:Alejandro Pachón\n"
            "203005313986:Michael Bocanegra\n"
            "203005585239:Martha Lopez\n"
            "203009113981:Natalia. herra (526)\n"
            "203006755018:Elvira Guevara (529)\n"
            "203006973781:Eibar Amaya (527)\n"
            "203006672935:Biblioteca Enrique Uribe Pagés (530)\n"
            "203007825932:Jocselyn Perera (528)\n"
            "203006282173:Lisa Balmaceda (471)\n"
            "203008515473:Paula Medina (474)\n"
            "203008647011:Sandra Elizabeth Beltrán Castro(484)\n"
            "203006281946:Santiago Castro (443)\n"
        ),
        height=230,
        label_visibility="collapsed"
    )
    agent_names = {}
    for line in agent_names_raw.strip().splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            agent_names[k.strip()] = v.strip()

    st.divider()
    st.markdown("**🎚️ Umbrales SLA por Prioridad**")
    
    SLA_CONFIG = {
        'Urgente':  {'resp': 1,   'all_resp': 1,   'res': 24,   'hours': 24},
        'Alta':     {'resp': 4,   'all_resp': 6,   'res': 168,  'hours': 24},
        'Media':    {'resp': 8,   'all_resp': 24,  'res': 360,  'hours': 24},
        'Baja':     {'resp': 24,  'all_resp': 48,  'res': 360,  'hours': 24},
    }
    
    col1, col2 = st.columns(2)
    with col1:
        st.caption("Prioridad del ticket:")
        priority_filter = st.selectbox("Filtrar por prioridad", ["Todas", "Urgente", "Alta", "Media", "Baja"], index=0)
    with col2:
        st.caption("Mostrar SLA de:")
        sla_view = st.radio("Ver SLA", ["Primera respuesta", "Todas las respuestas", "Resolución"], index=0, horizontal=True)
    
    used_sla = SLA_CONFIG.get(priority_filter if priority_filter != "Todas" else "Media", SLA_CONFIG['Media'])
    
    sla_resp_h = used_sla['resp']
    sla_all_resp_h = used_sla['all_resp']
    sla_res_h = used_sla['res']
    gap_th_h = 24
    
    view_sla_h = sla_resp_h
    if sla_view == "Todas las respuestas":
        view_sla_h = sla_all_resp_h
    elif sla_view == "Resolución":
        view_sla_h = sla_res_h
    
    st.session_state['view_sla_h'] = view_sla_h
    gap_th_h = 24

    st.divider()
    st.markdown("**🕐 Zona horaria**")
    tz_offset = st.number_input("UTC offset (h)", value=-6, min_value=-12, max_value=14)

    st.divider()
    if st.button("🔄 Recargar archivos", use_container_width=True, type="primary"):
        st.cache_data.clear()
        st.rerun()

# ─── LOAD DATA ────────────────────────────────────────────────────────────────
files_found = sorted(glob.glob(os.path.join(data_dir, "activities_*.json")))

if not files_found:
    st.error(f"⚠️ No se encontraron archivos `activities_*.json` en `{os.path.abspath(data_dir)}`")
    st.info("Ajusta el **Directorio de archivos JSON** en el panel lateral para apuntar a la carpeta correcta.")
    st.stop()

_hash = dir_hash(data_dir)
df_raw = load_df(data_dir, _hash, tuple(sorted(agent_names.items())))

# Apply timezone offset to timestamps for display
if not df_raw.empty and 'timestamp' in df_raw.columns and df_raw['timestamp'].notna().any():
    from datetime import timedelta
    df_raw = df_raw.copy()
    df_raw['timestamp_local'] = df_raw['timestamp'].apply(
        lambda x: x + timedelta(hours=tz_offset) if x is not None else None)
    df_raw['hour_local'] = df_raw['timestamp_local'].apply(
        lambda x: x.hour if x is not None else None)
else:
    if 'timestamp' in df_raw.columns:
        df_raw['timestamp_local'] = df_raw['timestamp']
    if 'hour' in df_raw.columns:
        df_raw['hour_local'] = df_raw['hour']

# ── Ticket product mapping ──
tmp_prod = df_raw.dropna(subset=['producto']).sort_values('timestamp')
ticket_prod_map = tmp_prod.groupby('ticket_num')['producto'].last()
df_raw['ticket_product'] = df_raw['ticket_num'].map(ticket_prod_map).fillna('Sin producto')

sla_full = compute_sla(df_raw, sla_resp_h, sla_res_h)
sla_full['producto'] = sla_full['ticket_num'].map(ticket_prod_map).fillna('Sin producto')

# ─── TOP FILTERS ──────────────────────────────────────────────────────────────
st.markdown("# 📡 Freshdesk Dashboard")

meta_cols = st.columns(4)
meta_cols[0].caption(f"📂 {len(files_found)} archivos cargados")
meta_cols[1].caption(f"🎫 {df_raw['ticket_num'].nunique()} tickets")
meta_cols[2].caption(f"⚡ {len(df_raw)} actividades")
meta_cols[3].caption(f"📅 {df_raw['date'].min()} → {df_raw['date'].max()}" if not df_raw.empty else "")

fc1, fc2, fc3 = st.columns(3)
with fc1:
    sel_tickets = st.multiselect("🎫 Tickets", sorted(df_raw['ticket_id'].unique()), placeholder="Todos")
with fc2:
    human_agents = sorted(a for a in df_raw['performer_name'].unique() if '⚙️' not in a)
    sel_agents = st.multiselect("👤 Agentes y clientes", human_agents, placeholder="Todos")
with fc3:
    sel_types = st.multiselect("🏷️ Tipo actividad", sorted(df_raw['activity_type'].unique()), placeholder="Todos")

fc4, fc5 = st.columns(2)
with fc4:
    date_vals = sorted(df_raw['date'].dropna().unique())
    if len(date_vals) >= 2:
        date_range = st.date_input("📅 Rango fechas", value=(date_vals[0], date_vals[-1]),
                                   min_value=date_vals[0], max_value=date_vals[-1])
    else:
        date_range = None
with fc5:
    productos = sorted(df_raw['ticket_product'].unique())
    sel_productos = st.multiselect("🏷️ Producto", productos, placeholder="Todos")

# Apply filters
dff = df_raw.copy()
if sel_tickets: dff = dff[dff['ticket_id'].isin(sel_tickets)]
if sel_agents:  dff = dff[dff['performer_name'].isin(sel_agents)]
if sel_types:   dff = dff[dff['activity_type'].isin(sel_types)]
if date_range and len(date_range) == 2:
    dff = dff[(dff['date'] >= date_range[0]) & (dff['date'] <= date_range[1])]
if sel_productos:
    dff = dff[dff['ticket_product'].isin(sel_productos)]

# SLA filtered to visible tickets
visible_tickets = dff['ticket_id'].unique()
sla_df = sla_full[sla_full['ticket_id'].isin(visible_tickets)]

# ─── TABS ─────────────────────────────────────────────────────────────────────
T = st.tabs([
    "📈 Overview",
    "🔍 Trazabilidad",
    "⏱️ SLA & Tiempos",
    "👥 Agentes y clientes",
    "⚠️ Cuellos de Botella",
    "🕸️ Grafo de Relaciones",
    "📋 Audit Log"
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
with T[0]:
    n_tickets   = dff['ticket_num'].nunique()
    n_acts      = len(dff)
    n_agents    = dff[dff['performer_type'] == 'user']['performer_id'].nunique()
    n_resolved  = sla_df['is_resolved'].sum()
    avg_ttfr    = sla_df['ttfr_h'].dropna().mean()
    n_alerts    = ((sla_df['ttfr_h'].fillna(0) > sla_resp_h) |
                   (sla_df['resolution_h'].fillna(0) > sla_res_h) |
                   (sla_df['max_gap_h'] > gap_th_h) |
                   sla_df['reopened']).sum()

    kpi_data = [
        (n_tickets,  "Tickets",         ""),
        (n_acts,     "Actividades",     ""),
        (n_agents,   "Agentes y clientes activos", ""),
        (n_resolved, "Resueltos",       "ok"),
        (f"{avg_ttfr:.1f}h" if avg_ttfr else "—", "TTFR Promedio (todos)", "warn"),
        (n_alerts,   "⚠️ Alertas",      "danger" if n_alerts > 0 else "ok"),
    ]
    kpi_html = "<div class='metric-grid'>"
    for val, lbl, cls in kpi_data:
        kpi_html += f"""
        <div class='metric-card'>
            <div class='metric-val {cls}'>{val}</div>
            <div class='metric-lbl'>{lbl}</div>
        </div>"""
    kpi_html += "</div>"
    st.markdown(kpi_html, unsafe_allow_html=True)

    # ── Product breakdown ──
    st.markdown("<div class='sec-header'>TICKETS POR PRODUCTO Y ESTADO</div>", unsafe_allow_html=True)
    prod_pivot = (
        sla_df.groupby(['producto', 'last_status'])
        .size()
        .unstack(fill_value=0)
    )
    for s in ['Open', 'Pending', 'Resolved', 'Closed']:
        if s not in prod_pivot.columns:
            prod_pivot[s] = 0
    prod_pivot = prod_pivot[['Open', 'Pending', 'Resolved', 'Closed']]
    prod_pivot.columns = ['🔴 Abiertos', '🟡 Pendientes', '🟢 Resueltos', '⚪ Cerrados']
    prod_pivot['Total'] = prod_pivot.sum(axis=1)
    prod_pivot = prod_pivot.reset_index()
    prod_pivot = prod_pivot.rename(columns={'producto': 'Producto'})
    st.dataframe(prod_pivot, use_container_width=True, hide_index=True)

    with st.expander("📋 Ver listado de tickets por producto"):
        for prod in sorted(sla_df['producto'].unique()):
            subt = sla_df[sla_df['producto'] == prod]
            st.markdown(f"**{prod}** — {len(subt)} tickets")
            cols = ['ticket_id', 'priority', 'last_status', 'ttfr_h', 'resolution_h', 'n_agents']
            tbl = subt[cols].copy()
            tbl['ttfr_h'] = tbl['ttfr_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
            tbl['resolution_h'] = tbl['resolution_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
            st.dataframe(
                tbl.rename(columns={
                    'ticket_id': 'Ticket', 'priority': 'Prioridad',
                    'last_status': 'Estado', 'ttfr_h': 'TTFR',
                    'resolution_h': 'Resolución', 'n_agents': '# Agentes y clientes',
                }),
                use_container_width=True, hide_index=True,
            )

    # ── SLA compliance summary ──
    st.markdown("<div class='sec-header'>CUMPLIMIENTO SLA — PERIODO ACTUAL</div>", unsafe_allow_html=True)
    total_tickets = len(sla_df)
    any_breach = sla_df['ttfr_breach'] | sla_df['all_resp_breach'] | sla_df['ttr_breach']
    n_breach = any_breach.sum()
    n_resolved_ok = sla_df[sla_df['is_resolved'] & ~sla_df['ttr_breach']].shape[0]
    breach_ttfr = sla_df[sla_df['ttfr_breach']]
    breach_res = sla_df[sla_df['ttr_breach']]
    avg_excess_ttfr = (breach_ttfr['ttfr_h'] - breach_ttfr['ttfr_sla']).mean() if len(breach_ttfr) else None
    avg_excess_res = (breach_res['resolution_h'] - breach_res['res_sla']).mean() if len(breach_res) else None
    if avg_excess_ttfr is not None and avg_excess_res is not None:
        excess_str = f"+{avg_excess_ttfr:.1f}h TTFR  ·  +{avg_excess_res:.1f}h Resol"
    elif avg_excess_ttfr is not None:
        excess_str = f"+{avg_excess_ttfr:.1f}h TTFR"
    elif avg_excess_res is not None:
        excess_str = f"+{avg_excess_res:.1f}h Resol"
    else:
        excess_str = "—"
    pct_breach = f"{n_breach/total_tickets*100:.0f}%" if total_tickets else "0%"
    pct_ok = f"{n_resolved_ok/total_tickets*100:.0f}%" if total_tickets else "0%"

    sla_cards = [
        (total_tickets,         "Total Tickets",            ""),
        (f"{n_breach}",        f"Incumplieron SLA ({pct_breach})", "danger" if n_breach > 0 else "ok"),
        (f"{n_resolved_ok}",   f"Resueltos sin incumplir SLA ({pct_ok})", "ok" if n_resolved_ok else ""),
        (excess_str,           "Exceso promedio (solo incumplidos)", "warn" if avg_excess_ttfr or avg_excess_res else ""),
        (sla_df['reopened'].sum(), "Reabiertos",            ""),
    ]
    kpi2_html = "<div class='metric-grid'>"
    for val, lbl, cls in sla_cards:
        kpi2_html += f"""<div class='metric-card'><div class='metric-val {cls}'>{val}</div><div class='metric-lbl'>{lbl}</div></div>"""
    kpi2_html += "</div>"
    st.markdown(kpi2_html, unsafe_allow_html=True)

    # ── Ticket detail expanders for each compliance category ──
    def fmt_tbl(df):
        cols = ['ticket_id', 'priority', 'last_status', 'ttfr_h', 'resolution_h', 'max_gap_h', 'n_agents']
        out = df[cols].copy()
        out['ttfr_h'] = out['ttfr_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
        out['resolution_h'] = out['resolution_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
        out['max_gap_h'] = out['max_gap_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
        return out.rename(columns={
            'ticket_id': 'Ticket', 'priority': 'Prioridad',
            'last_status': 'Estado', 'ttfr_h': 'TTFR',
            'resolution_h': 'Resolución', 'max_gap_h': 'Max Brecha',
            'n_agents': '# Agentes y clientes',
        })

    with st.expander(f"📋 Tickets que incumplieron SLA ({n_breach})"):
        if n_breach:
            st.dataframe(fmt_tbl(sla_df[any_breach]), use_container_width=True, hide_index=True)
        else:
            st.caption("Ninguno")

    with st.expander(f"✅ Tickets resueltos sin incumplir SLA ({n_resolved_ok})"):
        if n_resolved_ok:
            st.dataframe(fmt_tbl(sla_df[sla_df['is_resolved'] & ~sla_df['ttr_breach']]), use_container_width=True, hide_index=True)
        else:
            st.caption("Ninguno")

    with st.expander(f"⏰ Tickets con TTFR incumplido ({len(breach_ttfr)})"):
        if len(breach_ttfr):
            st.dataframe(fmt_tbl(breach_ttfr), use_container_width=True, hide_index=True)
        else:
            st.caption("Ninguno")

    with st.expander(f"⌛ Tickets con resolución incumplida ({len(breach_res)})"):
        if len(breach_res):
            st.dataframe(fmt_tbl(breach_res), use_container_width=True, hide_index=True)
        else:
            st.caption("Ninguno")

    reopened_df = sla_df[sla_df['reopened']]
    with st.expander(f"🔁 Tickets reabiertos ({len(reopened_df)})"):
        if len(reopened_df):
            st.dataframe(fmt_tbl(reopened_df), use_container_width=True, hide_index=True)
        else:
            st.caption("Ninguno")

    c1, c2 = st.columns(2)
    with c1:
        daily = dff.groupby('date').size().reset_index(name='actividades')
        fig = px.bar(daily, x='date', y='actividades', title='Actividad Diaria',
                     color_discrete_sequence=['#4f8ef7'])
        apply_theme(fig)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        type_counts = dff['activity_type'].value_counts().reset_index()
        type_counts.columns = ['tipo', 'count']
        fig = px.pie(type_counts, values='count', names='tipo',
                     title='Distribución de Tipos de Actividad',
                     color_discrete_sequence=COLOR_SEQ, hole=0.45)
        apply_theme(fig)
        st.plotly_chart(fig, use_container_width=True)

    c3, c4 = st.columns(2)
    with c3:
        # Heatmap hora vs día
        hm = dff[dff['hour_local'].notna()].groupby(['weekday', 'hour_local']).size().reset_index(name='count')
        day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        day_es    = {'Monday':'Lun','Tuesday':'Mar','Wednesday':'Mié',
                     'Thursday':'Jue','Friday':'Vie','Saturday':'Sáb','Sunday':'Dom'}
        hm['dia'] = hm['weekday'].map(day_es)
        pivot = hm.pivot(index='weekday', columns='hour_local', values='count').fillna(0)
        pivot = pivot.reindex([d for d in day_order if d in pivot.index])
        pivot.index = [day_es.get(d, d) for d in pivot.index]
        fig = px.imshow(pivot, title=f'Mayor actividad (UTC{tz_offset:+d})',
                        color_continuous_scale='Blues', aspect='auto', text_auto=True)
        apply_theme(fig, height=280)
        st.plotly_chart(fig, use_container_width=True)

    with c4:
        ta = dff.groupby('ticket_id').size().reset_index(name='actividades').sort_values('actividades', ascending=False)
        fig = px.bar(ta, x='ticket_id', y='actividades',
                     title='Actividades por Ticket',
                     color='actividades', color_continuous_scale='Blues')
        apply_theme(fig, height=280)
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    # Status distribution
    c5, c6 = st.columns(2)
    with c5:
        status_dist = sla_df['last_status'].value_counts().reset_index()
        status_dist.columns = ['estado', 'count']
        fig = px.bar(status_dist, x='estado', y='count', title='Estado Actual de Tickets',
                     color='estado', color_discrete_sequence=COLOR_SEQ)
        apply_theme(fig, height=260)
        st.plotly_chart(fig, use_container_width=True)

    with c6:
        # Activity over time (line per ticket)
        act_ts = dff.groupby(['date', 'ticket_id']).size().reset_index(name='count')
        fig = px.line(act_ts, x='date', y='count', color='ticket_id',
                      title='Actividad Diaria por Ticket',
                      color_discrete_sequence=COLOR_SEQ)
        apply_theme(fig, height=260)
        st.plotly_chart(fig, use_container_width=True)

    # ── Ticket list by status and product ──
    st.markdown("<div class='sec-header'>LISTA DE TICKETS POR ESTADO Y PRODUCTO</div>", unsafe_allow_html=True)
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        sel_status = st.multiselect(
            "Estado", ['Open', 'Pending', 'Resolved', 'Closed'],
            default=['Open', 'Pending'],
            key="status_filter"
        )
    with col_s2:
        prods_in_view = sorted(sla_df['producto'].unique())
        sel_prod_local = st.multiselect(
            "Producto", prods_in_view,
            default=prods_in_view,
            key="product_filter_local"
        )

    tickets_filt = sla_df[
        sla_df['last_status'].isin(sel_status) &
        sla_df['producto'].isin(sel_prod_local)
    ]
    if not tickets_filt.empty:
        tbl = tickets_filt[['ticket_id', 'priority', 'producto', 'last_status',
                            'ttfr_h', 'resolution_h', 'max_gap_h', 'n_agents']].copy()
        tbl['ttfr_h'] = tbl['ttfr_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
        tbl['resolution_h'] = tbl['resolution_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
        tbl['max_gap_h'] = tbl['max_gap_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
        st.dataframe(
            tbl.rename(columns={
                'ticket_id': 'Ticket', 'priority': 'Prioridad',
                'producto': 'Producto', 'last_status': 'Estado',
                'ttfr_h': 'TTFR', 'resolution_h': 'Resolución',
                'max_gap_h': 'Max Brecha', 'n_agents': '# Agentes y clientes',
            }),
            use_container_width=True, hide_index=True,
        )
        st.caption(f"Mostrando {len(tickets_filt)} tickets")
    else:
        st.caption("No hay tickets con los filtros seleccionados")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TRAZABILIDAD
# ══════════════════════════════════════════════════════════════════════════════
with T[1]:
    st.markdown("<div class='sec-header'>TRAZABILIDAD COMPLETA POR TICKET</div>", unsafe_allow_html=True)

    sel_t = st.selectbox("Selecciona un ticket", sorted(dff['ticket_num'].unique()))
    t_df  = dff[dff['ticket_num'] == sel_t].sort_values('timestamp')
    sla_r = sla_df[sla_df['ticket_num'] == sel_t]

    if not sla_r.empty:
        s = sla_r.iloc[0]
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Estado actual",      s['last_status'])
        m2.metric("Actividades",        s['n_activities'])
        m3.metric("Intercambios",       s['n_exchanges'])
        m4.metric("Agentes y clientes", s['n_agents'])
        m5.metric("TTFR",   f"{s['ttfr_h']}h"       if s['ttfr_h']      else "—")
        m6.metric("Resolución", f"{s['resolution_h']}h" if s['resolution_h'] else "Pendiente")

        if s['reopened']:
            st.warning(f"🔁 Este ticket fue reabierto **{s['reopen_count']} vez/veces**")
        if s['ttfr_h'] is not None and s['ttfr_breach']:
            st.error(f"🚨 SLA de primera respuesta incumplido: {s['ttfr_h']}h > {sla_resp_h}h")
        if s['ttr_breach']:
            st.error(f"🚨 SLA de resolución incumplido: {s['resolution_h']}h > {sla_res_h}h")

    # Timeline chart
    COLOR_MAP = {
        'Ticket Creado':       '#40c057',
        'Respuesta Pública':   '#4f8ef7',
        'Nota privada':'#9775fa',
        'Reenvío':             '#f03e3e',
        'Cambio de Estado':    '#f59f00',
        'Automatización':      '#64748b',
        'Campo Actualizado':   '#ff9e64',
        'Asignación':          '#38d9a9',
        'Etiqueta Añadida':    '#a9e34b',
        'Fecha Límite':        '#74c0fc',
        'Nota':                '#a5d8ff',
        'Email Enviado':       '#ff8787',
        'Observador Añadido':  '#ffd43b',
        'Actualización':       '#adb5bd',
    }

    # Agrupar por tipo de actividad para mostrar leyenda en el gráfico
    fig = go.Figure()
    for act_type, grp in t_df.groupby('activity_type'):
        x_vals, y_vals, hover_texts = [], [], []
        for _, row in grp.iterrows():
            if row['timestamp'] is None:
                continue
            ts_disp = row['timestamp_local'] if 'timestamp_local' in row and row['timestamp_local'] else row['timestamp']
            x_vals.append(ts_disp)
            y_vals.append(row['activity_type'])
            hover_texts.append(
                f"<b>{row['activity_type']}</b><br>"
                f"🕐 {ts_disp.strftime('%d/%m/%Y %H:%M') if ts_disp else '—'}<br>"
                f"👤 {row['performer_name']}<br>"
                f"📝 {row['detail']}"
            )
        color = COLOR_MAP.get(act_type, '#94a3b8')
        fig.add_trace(go.Scatter(
            x=x_vals,
            y=y_vals,
            mode='markers',
            marker=dict(size=14, color=color, line=dict(width=1.5, color='#0a0e1a'),
                        symbol='circle'),
            text=hover_texts,
            hovertemplate="%{text}<br><extra></extra>",
            name=act_type,
            showlegend=True,
        ))

    fig.update_layout(
        **PLOT_CFG,
        height=420,
        title=f"Línea de tiempo — Ticket #{sel_t}",
        xaxis_title=f"Tiempo (UTC{tz_offset:+d})",
        yaxis_title="Tipo de Evento",
        xaxis=dict(showgrid=True, gridcolor='#243044'),
        yaxis=dict(showgrid=True, gridcolor='#243044'),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Gap visualization between events
    t_valid = t_df.dropna(subset=['timestamp_local']).copy()
    if len(t_valid) > 1:
        gap_data = []
        for i in range(len(t_valid) - 1):
            ts_from = t_valid.iloc[i]['timestamp_local']
            ts_to   = t_valid.iloc[i+1]['timestamp_local']
            gap_h   = (ts_to - ts_from).total_seconds() / 3600
            gap_data.append({
                'Desde':           ts_from.strftime('%d/%m %H:%M'),
                'Hasta':           ts_to.strftime('%d/%m %H:%M'),
                'Brecha (h)':      round(gap_h, 2),
                'Evento siguiente': t_valid.iloc[i+1]['activity_type'],
                'Actor siguiente':  t_valid.iloc[i+1]['performer_name'],
            })
        gap_tbl = pd.DataFrame(gap_data)

        fig_gap = px.bar(gap_tbl, x='Desde', y='Brecha (h)',
                          title='Brechas entre eventos',
                          hover_data=['Evento siguiente', 'Actor siguiente'],
                          color_discrete_sequence=['#4f8ef7'])
        fig_gap.add_hline(y=gap_th_h, line_dash='dash', line_color='#f03e3e',
                           annotation_text=f"Umbral {gap_th_h}h")
        apply_theme(fig_gap, height=260)
        fig_gap.update_layout(showlegend=False)
        st.plotly_chart(fig_gap, use_container_width=True)

    # Full log table
    st.markdown("<div class='sec-header'>LOG COMPLETO</div>", unsafe_allow_html=True)
    audit = t_df[['timestamp_local', 'performer_name', 'activity_type', 'detail']].copy()
    audit['timestamp_local'] = audit['timestamp_local'].apply(
        lambda x: x.strftime('%d/%m/%Y %H:%M:%S') if x else '—')
    audit.columns = ['⏰ Timestamp', '👤 Actor', '🏷️ Tipo', '📝 Detalle']

    csv_bytes = audit.to_csv(index=False).encode('utf-8')
    st.download_button("⬇️ Descargar log CSV", csv_bytes,
                       file_name=f"ticket_{sel_t}_audit.csv", mime='text/csv')
    st.dataframe(audit, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — SLA & TIEMPOS
# ══════════════════════════════════════════════════════════════════════════════
with T[2]:
    st.markdown("<div class='sec-header'>SLA  — TIEMPO ENTRE EVENTOS</div>", unsafe_allow_html=True)

    # ── SLA targets reference ──
    st.markdown("#### Objetivos SLA por Prioridad")
    sla_ref = pd.DataFrame([
        {
            'Prioridad': prio,
            '1.ª Respuesta': fmt_hours(cfg['resp']),
            'Todas Respuestas': fmt_hours(cfg['all_resp']),
            'Resolución': fmt_hours(cfg['res']),
            'Horas Operación': f"{cfg['hours']}h",
        }
        for prio, cfg in SLA_CONFIG.items()
    ])
    st.dataframe(sla_ref, use_container_width=True, hide_index=True)

    # ── Priority filter within tab ──
    sla_prio = st.selectbox(
        "Filtrar gráficos por prioridad",
        ["Todas", "Urgente", "Alta", "Media", "Baja"],
        index=0, key="sla_tab_priority"
    )

    sla_subset = sla_df if sla_prio == "Todas" else sla_df[sla_df['priority'] == sla_prio]

    if sla_prio != "Todas":
        cfg = SLA_CONFIG[sla_prio]
        t_resp, t_all, t_res = cfg['resp'], cfg['all_resp'], cfg['res']
    else:
        t_resp, t_all, t_res = sla_resp_h, sla_all_resp_h, sla_res_h

    # ── Chart row 1: TTFR & Resolution ──
    c1, c2 = st.columns(2)
    with c1:
        ttfr = sla_subset[sla_subset['ttfr_h'].notna()].copy()
        if not ttfr.empty:
            ttfr['sla_ok'] = ttfr['ttfr_h'] <= t_resp
            fig = px.bar(
                ttfr.sort_values('ttfr_h'),
                x='ticket_id', y='ttfr_h',
                color='sla_ok',
                color_discrete_map={True: '#40c057', False: '#f03e3e'},
                title=f'Tiempo a Primera Respuesta — objetivo {fmt_hours(t_resp)}',
                hover_data={'ttfr_h': ':.1f', 'agents': True, 'sla_ok': False},
                labels={'ttfr_h': 'TTFR (h)', 'ticket_id': 'Ticket', 'sla_ok': 'Cumple SLA'},
            )
            fig.add_hline(y=t_resp, line_dash='dash', line_color='#f59f00',
                          annotation_text=f"SLA {fmt_hours(t_resp)}", annotation_font_size=11)
            fig.update_traces(hovertemplate=(
                '<b>%{x}</b><br>'
                'TTFR: %{y:.1f}h (%{customdata[0]:.1f}d)<br>'
                'Agentes: %{customdata[1]}<br>'
                '<extra></extra>'
            ))
            apply_theme(fig, height=320)
            fig.update_yaxes(title_text='Horas')
            st.plotly_chart(fig, use_container_width=True)

    with c2:
        res = sla_subset[sla_subset['resolution_h'].notna()].copy()
        if not res.empty:
            res['sla_ok'] = res['resolution_h'] <= t_res
            fig = px.bar(
                res.sort_values('resolution_h'),
                x='ticket_id', y='resolution_h',
                color='sla_ok',
                color_discrete_map={True: '#40c057', False: '#f03e3e'},
                title=f'Tiempo hasta Resolución — objetivo {fmt_hours(t_res)}',
                hover_data={'resolution_h': ':.1f', 'sla_ok': False},
                labels={'resolution_h': 'Resolución (h)', 'ticket_id': 'Ticket', 'sla_ok': 'Cumple SLA'},
            )
            fig.add_hline(y=t_res, line_dash='dash', line_color='#f59f00',
                          annotation_text=f"SLA {fmt_hours(t_res)}", annotation_font_size=11)
            fig.update_traces(hovertemplate=(
                '<b>%{x}</b><br>'
                'Resolución: %{y:.1f}h (%{customdata[0]:.1f}d)<br>'
                '<extra></extra>'
            ))
            apply_theme(fig, height=320)
            fig.update_yaxes(title_text='Horas')
            st.plotly_chart(fig, use_container_width=True)

    # ── Chart row 2: Gap & Intercambios vs Resolución ──
    c3, c4 = st.columns(2)
    with c3:
        if not sla_subset.empty:
            fig = px.bar(
                sla_subset.sort_values('max_gap_h', ascending=False),
                x='ticket_id', y='max_gap_h',
                title=f'Brecha Máxima entre Eventos — umbral {fmt_hours(gap_th_h)}',
                color='max_gap_h', color_continuous_scale='RdYlGn_r',
                hover_data={
                    'ticket_id': True,
                    'max_gap_h': ':.1f',
                    'avg_gap_h': ':.1f',
                    'n_activities': True,
                },
                labels={'max_gap_h': 'Brecha (h)', 'ticket_id': 'Ticket'},
            )
            fig.add_hline(y=gap_th_h, line_dash='dash', line_color='#f03e3e',
                          annotation_text=f"Umbral {fmt_hours(gap_th_h)}")
            fig.update_traces(hovertemplate=(
                '<b>%{x}</b><br>'
                'Brecha máx: %{y:.1f}h (%{customdata[0]:.1f}d)<br>'
                'Brecha prom: %{customdata[1]:.1f}h<br>'
                'Actividades: %{customdata[2]}<br>'
                '<extra></extra>'
            ))
            apply_theme(fig, height=280)
            fig.update_coloraxes(showscale=False)
            fig.update_yaxes(title_text='Horas')
            st.plotly_chart(fig, use_container_width=True)

    with c4:
        fig = px.scatter(
            sla_subset,
            x='n_exchanges', y='resolution_h',
            size='n_activities', color='n_agents',
            hover_name='ticket_id',
            title='Intercambios vs Tiempo de Resolución',
            color_continuous_scale='Blues',
            labels={
                'n_exchanges': '# Intercambios',
                'resolution_h': 'Resolución (h)',
                'n_agents': '# Agentes',
            },
        )
        fig.update_traces(hovertemplate=(
            '<b>%{hovertext}</b><br>'
            'Intercambios: %{x}<br>'
            'Resolución: %{y:.1f}h (%{customdata[0]:.1f}d)<br>'
            'Agentes: %{marker.color:.0f}<br>'
            '<extra></extra>'
        ))
        apply_theme(fig, height=280)
        st.plotly_chart(fig, use_container_width=True)

    # ── Escalation analysis ──
    st.markdown("<div class='sec-header'>ANÁLISIS DE ESCALAMIENTO SLA</div>", unsafe_allow_html=True)

    total = len(sla_df)
    if total == 0:
        st.caption("No hay datos para analizar.")
    else:
        esc_rows = []
        for label, col in [
            ('1.ª Respuesta (TTFR)', 'ttfr_breach'),
            ('Tiempo entre respuestas', 'all_resp_breach'),
            ('Resolución', 'ttr_breach'),
            ('Reapertura', 'reopened'),
        ]:
            count = int(sla_df[col].sum())
            stuck = int(sla_df[sla_df[col] & ~sla_df['is_resolved']].shape[0]) if col != 'reopened' else 0
            esc_rows.append({
                'Nivel': label,
                'Tickets': count,
                '% del Total': round(count / total * 100, 1),
                'Estancados': stuck,
            })

        esc_df = pd.DataFrame(esc_rows)
        esc_df['Nivel'] = pd.Categorical(
            esc_df['Nivel'],
            categories=['1.ª Respuesta (TTFR)', 'Tiempo entre respuestas', 'Resolución', 'Reapertura'],
            ordered=True,
        )

        c_esc1, c_esc2 = st.columns([3, 2])
        with c_esc1:
            fig = px.bar(
                esc_df.sort_values('Nivel'),
                y='Nivel', x='Tickets',
                orientation='h',
                text='Tickets',
                color='Tickets',
                color_continuous_scale='RdYlGn_r',
                title='Tickets por nivel de escalamiento SLA',
                hover_data={'Nivel': False, 'Tickets': True, '% del Total': True, 'Estancados': True},
            )
            fig.update_traces(
                textposition='outside',
                hovertemplate=(
                    '<b>%{y}</b><br>'
                    'Tickets: %{x} (%{customdata[0]:.1f}%%)<br>'
                    'Estancados (sin resolver): %{customdata[1]}<br>'
                    '<extra></extra>'
                ),
            )
            fig.update_layout(yaxis=dict(autorange='reversed'), height=320)
            apply_theme(fig, height=320)
            fig.update_coloraxes(showscale=False)
            st.plotly_chart(fig, use_container_width=True)

        with c_esc2:
            st.caption("**Nivel** = etapa del SLA que se incumple · **Estancados** = tickets que incumplen y siguen sin resolver")
            st.dataframe(
                esc_df,
                use_container_width=True, hide_index=True,
                column_config={
                    'Nivel': 'Nivel',
                    'Tickets': st.column_config.NumberColumn('Tickets Afectados'),
                    '% del Total': st.column_config.NumberColumn('% del Total', format="%.1f%%"),
                    'Estancados': st.column_config.NumberColumn('Estancados'),
                },
            )

        # Ticket-level breakdown per escalation level
        st.caption("**Tickets que escalan a cada nivel (detalle)**")
        for label, col in [
            ('1.ª Respuesta (TTFR)', 'ttfr_breach'),
            ('Tiempo entre respuestas', 'all_resp_breach'),
            ('Resolución', 'ttr_breach'),
            ('Reapertura', 'reopened'),
        ]:
            subset = sla_df[sla_df[col]]
            if subset.empty:
                continue
            reason = (
                'TTFR excedido' if col == 'ttfr_breach' else
                'Brecha entre respuestas excede SLA' if col == 'all_resp_breach' else
                'Tiempo de resolución excedido' if col == 'ttr_breach' else
                'Ticket reabierto'
            )
            with st.expander(f"**{label}** — {len(subset)} tickets — {reason}"):
                show_cols = ['ticket_id', 'priority', 'last_status', 'ttfr_h', 'resolution_h',
                             'max_gap_h', 'n_agents', 'is_resolved']
                tbl = subset[show_cols].copy()
                tbl['ttfr_h'] = tbl['ttfr_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
                tbl['resolution_h'] = tbl['resolution_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
                tbl['max_gap_h'] = tbl['max_gap_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
                st.dataframe(tbl, use_container_width=True, hide_index=True,
                             column_config={
                                 'ticket_id': 'Ticket',
                                 'priority': 'Prioridad',
                                 'last_status': 'Estado',
                                 'ttfr_h': 'TTFR',
                                 'resolution_h': 'Resolución',
                                 'max_gap_h': 'Max Brecha',
'n_agents': '# Agentes y clientes',
                                  'is_resolved': st.column_config.CheckboxColumn('Resuelto'),
                             })

    # ── Inactivity detection ──
    st.markdown("<div class='sec-header'>TICKETS SIN ACTIVIDAD (INACTIVIDAD)</div>", unsafe_allow_html=True)

    inactive_days = st.selectbox("Mostrar tickets sin actividad por (días):", [1, 2, 3, 4, 5], index=0)
    threshold_h = inactive_days * 24

    inactive_tickets = sla_df[sla_df['max_gap_h'] > threshold_h].copy()

    if not inactive_tickets.empty:
        fig = px.bar(
            inactive_tickets.sort_values('max_gap_h', ascending=False),
            x='ticket_id',
            y='max_gap_h',
            title=f'Tickets con inactividad > {inactive_days} día(s) — umbral {fmt_hours(threshold_h)}',
            hover_data=['n_activities', 'last_status', 'n_agents'],
            color_discrete_sequence=['#f59f00'],
            labels={'max_gap_h': 'Brecha (h)', 'ticket_id': 'Ticket'},
        )
        fig.add_hline(y=threshold_h, line_dash='dash', line_color='#f03e3e',
                      annotation_text=f"Umbral {fmt_hours(threshold_h)} ({inactive_days}d)")
        fig.update_traces(hovertemplate=(
            '<b>%{x}</b><br>'
            'Inactividad: %{y:.1f}h (%{customdata[0]:.1f}d)<br>'
            'Actividades: %{customdata[1]}<br>'
            'Estado: %{customdata[2]}<br>'
            'Agentes: %{customdata[3]}<br>'
            '<extra></extra>'
        ))
        apply_theme(fig, height=300)
        fig.update_yaxes(title_text='Horas')
        st.plotly_chart(fig, use_container_width=True)

        st.caption(f"⚠️ {len(inactive_tickets)} tickets con inactividad > {fmt_hours(threshold_h)} ({inactive_days} días).")
    else:
        st.caption(f"ℹ️ No se encontraron tickets con inactividad > {inactive_days} días.")

    # ── Full SLA table with highlighting ──
    st.markdown("<div class='sec-header'>TABLA SLA COMPLETA</div>", unsafe_allow_html=True)

    def highlight_row(row):
        styles = [''] * len(row)
        idx_map = {c: i for i, c in enumerate(row.index)}
        if row.get('ttfr_breach'): styles[idx_map.get('ttfr_h', 0)] = 'background-color:rgba(240,62,62,.25)'
        if row.get('ttr_breach'):  styles[idx_map.get('resolution_h', 0)] = 'background-color:rgba(240,62,62,.25)'
        if row.get('max_gap_h', 0) > gap_th_h: styles[idx_map.get('max_gap_h', 0)] = 'background-color:rgba(245,159,0,.25)'
        if row.get('reopened'): styles[idx_map.get('reopen_count', 0)] = 'background-color:rgba(151,117,250,.25)'
        return styles

    tbl = sla_df[[
        'ticket_id', 'priority', 'last_status', 'n_activities', 'n_exchanges',
        'ttfr_h', 'resolution_h', 'max_gap_h', 'avg_gap_h',
        'n_agents', 'reopen_count', 'ttfr_breach', 'ttr_breach'
    ]].copy()
    tbl['ttfr_d'] = (tbl['ttfr_h'] / 24).round(1)
    tbl['resolution_d'] = (tbl['resolution_h'] / 24).round(1)

    st.dataframe(
        tbl.style.apply(highlight_row, axis=1),
        use_container_width=True, hide_index=True,
        column_config={
            'ticket_id':    'Ticket',
            'priority':     'Prioridad',
            'last_status':  'Estado',
            'n_activities': '# Acts',
            'n_exchanges':  '# Resp.',
            'ttfr_h':       st.column_config.NumberColumn('TTFR (h)', format="%.1f"),
            'ttfr_d':       st.column_config.NumberColumn('TTFR (d)', format="%.1f"),
            'resolution_h': st.column_config.NumberColumn('Resol. (h)', format="%.1f"),
            'resolution_d': st.column_config.NumberColumn('Resol. (d)', format="%.1f"),
            'max_gap_h':    st.column_config.NumberColumn('Max Brecha (h)', format="%.1f"),
            'avg_gap_h':    st.column_config.NumberColumn('Avg Brecha (h)', format="%.1f"),
            'n_agents':     '# Agentes',
            'reopen_count': '🔁 Reabiertos',
            'ttfr_breach':  '🔴 TTFR breach',
            'ttr_breach':   '🔴 TTR breach',
        }
    )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — AGENTES
# ══════════════════════════════════════════════════════════════════════════════
with T[3]:
    st.markdown("<div class='sec-header'>ANÁLISIS DE AGENTES</div>", unsafe_allow_html=True)

    human = dff[dff['performer_type'] == 'user'].copy()

    c1, c2 = st.columns(2)
    with c1:
        aa = human.groupby('performer_name').size().reset_index(name='actividades').sort_values('actividades')
        fig = px.bar(aa, y='performer_name', x='actividades', orientation='h',
                     title='Actividades Totales',
                     color='actividades', color_continuous_scale='Blues')
        apply_theme(fig, height=350)
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        at = human.groupby(['performer_name', 'activity_type']).size().reset_index(name='count')
        fig = px.bar(at, x='performer_name', y='count', color='activity_type',
                     title='Tipos de Actividad', barmode='stack',
                     color_discrete_sequence=COLOR_SEQ)
        apply_theme(fig, height=350)
        fig.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig, use_container_width=True)

    c3, c4 = st.columns(2)
    with c3:
        tp = human.groupby('performer_name')['ticket_id'].nunique().reset_index(name='tickets')
        fig = px.bar(tp, x='performer_name', y='tickets', title='Tickets únicos atendidos',
                     color='tickets', color_continuous_scale='Greens')
        apply_theme(fig, height=280)
        fig.update_coloraxes(showscale=False)
        fig.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(fig, use_container_width=True)

    with c4:
        ah = human.groupby(['performer_name', 'hour_local']).size().reset_index(name='count')
        piv = ah.pivot(index='performer_name', columns='hour_local', values='count').fillna(0)
        fig = px.imshow(piv, title=f'Actividad por Hora (UTC{tz_offset:+d})',
                        color_continuous_scale='Blues', aspect='auto')
        apply_theme(fig, height=280)
        st.plotly_chart(fig, use_container_width=True)

    # Influence score
    st.markdown("<div class='sec-header'>SCORE DE INFLUENCIA</div>", unsafe_allow_html=True)
    st.caption("Ponderado: Respuestas ×3 · Tickets únicos ×2 · Cambios de estado ×2 · Notas privadas ×1.5 · Resto ×1")

    infl = human.groupby('performer_name').agg(
        actividades    = ('activity_type', 'count'),
        tickets_únicos = ('ticket_id', 'nunique'),
        respuestas     = ('activity_type', lambda x: (x == 'Respuesta Pública').sum()),
        cambios_estado = ('activity_type', lambda x: (x == 'Cambio de Estado').sum()),
        notas_privadas = ('activity_type', lambda x: (x == 'Nota privada').sum()),
        reenvíos       = ('activity_type', lambda x: (x == 'Reenvío').sum()),
    ).reset_index()
    infl['score'] = (
        infl['actividades']    * 1.0 +
        infl['tickets_únicos'] * 2.0 +
        infl['respuestas']     * 3.0 +
        infl['cambios_estado'] * 2.0 +
        infl['notas_privadas'] * 1.5 +
        infl['reenvíos']       * 2.0
    ).round(1)
    infl = infl.sort_values('score', ascending=False)

    fig = px.bar(infl, x='performer_name', y='score', color='score',
                 color_continuous_scale='Plasma',
                 title='🏆 Score',
                 hover_data=['actividades', 'tickets_únicos', 'respuestas', 'cambios_estado'])
    apply_theme(fig, height=300)
    fig.update_coloraxes(showscale=False)
    fig.update_layout(xaxis_tickangle=-30)
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(infl, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — CUELLOS DE BOTELLA
# ══════════════════════════════════════════════════════════════════════════════
with T[4]:
    st.markdown("<div class='sec-header'>DETECCIÓN AUTOMÁTICA DE CUELLOS DE BOTELLA</div>",
                unsafe_allow_html=True)

    probs = sla_df.copy()
    probs['score'] = 0
    probs.loc[probs['ttfr_h'].fillna(0)      > sla_resp_h,  'score'] += 3
    probs.loc[probs['resolution_h'].fillna(0) > sla_res_h,  'score'] += 3
    probs.loc[probs['max_gap_h']             > gap_th_h,    'score'] += 2
    probs.loc[probs['reopened'],                             'score'] += 3
    probs.loc[probs['n_exchanges']           > 5,           'score'] += 1
    probs.loc[probs['n_agents']              > 2,           'score'] += 1
    probs.loc[~probs['is_resolved'],                         'score'] += 1

    def build_alerts(row):
        a = []
        if row['ttfr_breach']:   a.append(f"⏰ TTFR {row['ttfr_h']}h>{sla_resp_h}h")
        if row['ttr_breach']:    a.append(f"⌛ Res {row['resolution_h']}h>{sla_res_h}h")
        if row['max_gap_h'] > gap_th_h: a.append(f"⛔ Brecha {row['max_gap_h']}h")
        if row['reopened']:      a.append(f"🔁 Reabierto {row['reopen_count']}x")
        if not row['is_resolved']: a.append("🔴 Sin resolver")
        return " | ".join(a) if a else "✅ OK"

    probs['alertas'] = probs.apply(build_alerts, axis=1)
    probs_sorted = probs.sort_values('score', ascending=False)

    c1, c2 = st.columns([3, 2])
    with c1:
        fig = px.bar(probs_sorted.head(10), x='ticket_id', y='score',
                     color='score', color_continuous_scale='RdYlGn_r',
                     title='🚨 Top Tickets por Score de Problema',
                     hover_data=['alertas', 'ttfr_h', 'resolution_h', 'max_gap_h'])
        apply_theme(fig, height=320)
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        cats = {
            'SLA Respuesta': probs['ttfr_breach'].sum(),
            'SLA Resolución': probs['ttr_breach'].sum(),
            'Brecha Larga': (probs['max_gap_h'] > gap_th_h).sum(),
            'Reabiertos': probs['reopened'].sum(),
            'Sin Resolver': (~probs['is_resolved']).sum(),
            'Múltiples Agentes y clientes': (probs['n_agents'] > 2).sum(),
        }
        fig = px.bar(x=list(cats.keys()), y=list(cats.values()),
                     title='Categorías de Problemas',
                     color=list(cats.values()),
                     color_continuous_scale='Reds',
                     labels={'x': 'Categoría', 'y': 'Tickets afectados'})
        apply_theme(fig, height=320)
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    # Gap waterfall per ticket
    st.markdown("<div class='sec-header'>BRECHAS DETECTADAS (todas las transiciones)</div>",
                unsafe_allow_html=True)
    gap_rows = []
    for tnum, grp in dff.groupby('ticket_num'):
        grp = grp.sort_values('timestamp')
        tl = grp[['timestamp_local', 'performer_name', 'activity_type']].dropna(subset=['timestamp_local'])
        tl_list = tl.values.tolist()
        for i in range(len(tl_list) - 1):
            gap = (tl_list[i+1][0] - tl_list[i][0]).total_seconds() / 3600
            if gap > 0:
                gap_rows.append({
                    'ticket_id': f"#{tnum}",
                    'gap_h': round(gap, 2),
                    'desde_actor': tl_list[i][1],
                    'desde_evento': tl_list[i][2],
                    'hasta_evento': tl_list[i+1][2],
                })

    if gap_rows:
        gdf = pd.DataFrame(gap_rows)
        top15 = gdf.sort_values('gap_h', ascending=False).head(15)
        fig = px.bar(top15, x='gap_h', y='ticket_id', orientation='h',
                     color='gap_h', color_continuous_scale='RdYlGn_r',
                     title='Top 15 Brechas más largas entre eventos',
                     hover_data=['desde_actor', 'desde_evento', 'hasta_evento'])
        fig.add_vline(x=gap_th_h, line_dash='dash', line_color='#f03e3e',
                      annotation_text=f"Umbral {gap_th_h}h")
        apply_theme(fig, height=420)
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    # Problem tickets table
    st.markdown("<div class='sec-header'>TICKETS PROBLEMÁTICOS</div>", unsafe_allow_html=True)
    problem_tbl = probs_sorted[probs_sorted['score'] > 0][
        ['ticket_id', 'score', 'alertas', 'last_status', 'n_activities',
         'n_exchanges', 'max_gap_h', 'reopen_count']
    ]
    st.dataframe(problem_tbl, use_container_width=True, hide_index=True,
                 column_config={
                     'ticket_id':    'Ticket',
                     'score':        '🎯 Score',
                     'alertas':      '⚠️ Alertas',
                     'last_status':  'Estado',
                     'n_activities': '# Acts',
                     'n_exchanges':  '# Resp.',
                     'max_gap_h':    st.column_config.NumberColumn('Max Brecha (h)', format="%.1f"),
                     'reopen_count': '🔁 Reabiertos',
                 })

# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — GRAFO DE RELACIONES
# ══════════════════════════════════════════════════════════════════════════════
with T[5]:
    st.markdown("<div class='sec-header'>GRAFO: ACTOR → TICKET → TIPO DE ACCIÓN</div>",
                unsafe_allow_html=True)

    graph_mode = st.radio("Tipo de grafo",
                          ["Agente ↔ Ticket", "Agente → Acción → Ticket"],
                          horizontal=True)

    G = nx.DiGraph()
    edge_weights: dict = defaultdict(int)
    edge_labels: dict  = {}

    for _, row in dff.iterrows():
        actor  = row['performer_name']
        ticket = row['ticket_id']
        act    = row['activity_type']

        if graph_mode == "Agente ↔ Ticket":
            key = (actor, ticket)
            edge_weights[key] += 1
            edge_labels[key]   = act
        else:
            # actor → action_type → ticket
            act_node = f"[{act}]"
            edge_weights[(actor, act_node)]   += 1
            edge_weights[(act_node, ticket)]  += 1

    node_counts_acts = dff.groupby('performer_name').size().to_dict()
    node_counts_tick = dff.groupby('ticket_id').size().to_dict()
    node_counts_type = dff.groupby('activity_type').size().to_dict()

    for (src, tgt), w in edge_weights.items():
        if not G.has_node(src):
            G.add_node(src)
        if not G.has_node(tgt):
            G.add_node(tgt)
        G.add_edge(src, tgt, weight=w)

    pos = nx.spring_layout(G, k=2.5, seed=42, iterations=60)

    # Build edge traces
    edge_x, edge_y = [], []
    for (src, tgt) in G.edges():
        x0, y0 = pos[src]
        x1, y1 = pos[tgt]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=edge_x, y=edge_y, mode='lines',
        line=dict(width=1, color='rgba(79,142,247,0.25)'),
        hoverinfo='none', showlegend=False))

    # Node traces by type
    def node_style(n):
        if n.startswith('#'):   return '#4f8ef7', 10 + node_counts_tick.get(n, 1) * 2.5, 'ticket'
        if n.startswith('['):   return '#f59f00', 8 + node_counts_type.get(n[1:-1], 1) * 1.5, 'action'
        if '⚙️' in n:          return '#64748b', 12, 'system'
        return '#40c057', 10 + node_counts_acts.get(n, 1) * 2, 'agent'

    for ntype, label, color in [
        ('ticket', '🎫 Ticket', '#4f8ef7'),
        ('agent',  '👤 Agentes y clientes', '#40c057'),
        ('system', '⚙️ Sistema','#64748b'),
        ('action', '🏷️ Acción', '#f59f00'),
    ]:
        nx_list = [n for n in G.nodes() if node_style(n)[2] == ntype]
        if not nx_list:
            continue
        nx_arr = [pos[n] for n in nx_list]
        sizes  = [node_style(n)[1] for n in nx_list]
        fig.add_trace(go.Scatter(
            x=[p[0] for p in nx_arr],
            y=[p[1] for p in nx_arr],
            mode='markers+text',
            marker=dict(size=sizes, color=color,
                        line=dict(width=1.5, color='rgba(255,255,255,.15)')),
            text=nx_list,
            textposition='top center',
            textfont=dict(size=9, color='#e2e8f0'),
            name=label,
            hovertemplate='<b>%{text}</b><extra></extra>',
        ))

    fig.update_layout(
        **PLOT_CFG,
        height=580,
        title='Grafo de Relaciones',
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        legend=dict(orientation='h', yanchor='bottom', y=-0.08),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Degree centrality table
    st.markdown("<div class='sec-header'>CENTRALIDAD DE NODOS</div>", unsafe_allow_html=True)
    deg  = nx.degree_centrality(G)
    betw = nx.betweenness_centrality(G)
    cent_df = pd.DataFrame([{
        'Nodo': n,
        'Tipo': 'Ticket' if n.startswith('#') else ('Acción' if n.startswith('[') else ('Sistema' if '⚙️' in n else 'Agente')),
        'Grado': G.degree(n),
        'Centralidad Grado': round(deg[n], 4),
        'Centralidad Intermediación': round(betw[n], 4),
    } for n in G.nodes()]).sort_values('Grado', ascending=False)
    st.dataframe(cent_df, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — AUDIT LOG COMPLETO
# ══════════════════════════════════════════════════════════════════════════════
with T[6]:
    st.markdown("<div class='sec-header'>AUDIT LOG COMPLETO — TODAS LAS ACTIVIDADES</div>",
                unsafe_allow_html=True)

    log = dff[['timestamp_local', 'ticket_id', 'performer_name',
               'performer_type', 'activity_type', 'detail']].copy()
    log['timestamp_local'] = log['timestamp_local'].apply(
        lambda x: x.strftime('%d/%m/%Y %H:%M:%S') if x else '—')
    log.columns = ['⏰ Timestamp', '🎫 Ticket', '👤 Actor', 'Tipo Actor',
                   '🏷️ Actividad', '📝 Detalle']

    st.write(f"**{len(log)}** actividades en el rango seleccionado")

    csv_all = log.to_csv(index=False).encode('utf-8')
    st.download_button("⬇️ Exportar todo a CSV", csv_all,
                       file_name="freshdesk_audit_log.csv", mime='text/csv')

    # Search
    search = st.text_input("🔎 Buscar en el log", placeholder="ticket, agente, detalle...")
    if search:
        mask = log.apply(lambda r: search.lower() in str(r).lower(), axis=1)
        log = log[mask]
        st.caption(f"{len(log)} resultados para «{search}»")

    st.dataframe(log, use_container_width=True, hide_index=True,
                 column_config={
                     '⏰ Timestamp': st.column_config.TextColumn(width='medium'),
                     '📝 Detalle':   st.column_config.TextColumn(width='large'),
                 })