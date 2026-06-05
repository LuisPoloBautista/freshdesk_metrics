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
    page_icon="",
    layout="wide"
)

# ─── CUSTOM CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
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

.stApp header { background: transparent !important; }
.stApp .main .block-container { padding-top: 1rem !important; }

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

.sec-header {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: var(--accent);
    border-bottom: 1px solid var(--border);
    padding-bottom: 6px;
    margin: 20px 0 14px 0;
}

.stTabs [data-baseweb="tab-list"] { gap: 32px; border-bottom: 1px solid var(--border); justify-content: center; }
.stTabs [data-baseweb="tab"] { border-radius: 8px 8px 0 0; font-weight: 600; color: var(--muted); }
.stTabs [aria-selected="true"] { color: var(--accent) !important; border-bottom-color: var(--accent) !important; }

.stDataFrame { border-radius: 8px; border: 1px solid var(--border); }
div[data-testid="stExpander"] { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }

.stMetric {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
}
.stMetric label { color: var(--muted) !important; font-weight: 600 !important; }
.stMetric [data-testid="stMetricValue"] { color: var(--text) !important; font-weight: 700 !important; }

.stSelectbox label, .stMultiselect label, .stDateInput label { color: var(--text) !important; font-weight: 600 !important; }
.stCaption, .stApp caption, .caption { color: var(--muted) !important; }
.stMarkdown p, .stMarkdown li, .stMarkdown span { color: var(--text); }
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4, .stMarkdown h5, .stMarkdown h6 { color: var(--text) !important; font-weight: 700 !important; }
.stAlert { border-radius: 8px; border-left: 4px solid; }
.stAlert p { color: var(--text); }
.stRadio label { color: var(--text) !important; font-weight: 500 !important; }

.js-plotly-plot .gtitle { fill: var(--text) !important; font-weight: 600 !important; }
.js-plotly-plot .xtick text, .js-plotly-plot .ytick text { fill: var(--muted) !important; }
.js-plotly-plot .legendtext { fill: var(--text) !important; }
</style>
""", unsafe_allow_html=True)

# ─── HELPERS ──────────────────────────────────────────────────────────────────

PRIORITY_MAP = {
    'Bajo': 'Baja', 'Alto': 'Alta', 'Medio': 'Media', 'Urgente': 'Urgente',
    1: 'Baja', 2: 'Media', 3: 'Alta', 4: 'Urgente',
}

def normalize_priority(v):
    if v is None:
        return None
    return PRIORITY_MAP.get(v, str(v) if isinstance(v, str) else None)

def parse_dt(s: str):
    try:
        return datetime.strptime(s, "%d-%m-%Y %H:%M:%S %z")
    except Exception:
        return None

def classify_act(act: dict) -> str:
    if 'new_ticket' in act:
        return 'Ticket Creado'
    if 'note' in act:
        return {
            0: 'Respuesta Pública',
            1: 'Reenvío',
            2: 'Respuesta a reenvío',
            3: 'Nota privada',
            4: 'Nota pública',
            5: 'Nota telefónica',
            6: 'Nota de difusión',
        }.get(act['note'].get('type', -1), 'Nota')
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
    NOTE_LABELS = {
        0: 'Respuesta',
        1: 'Reenvío',
        2: 'Respuesta a reenvío',
        3: 'Nota privada',
        4: 'Nota pública',
        5: 'Nota telefónica',
        6: 'Nota de difusión',
    }
    SOURCES = {1: 'Portal', 2: 'Email', 3: 'Teléfono', 4: 'Chat',
               5: 'Twitter', 6: 'Facebook', 7: 'API'}
    parts = []
    if 'status'       in act: parts.append(f"→ {act['status']}")
    if 'note'         in act:
        nt = act['note'].get('type', -1)
        label = NOTE_LABELS.get(nt, f'tipo {nt}')
        parts.append(f"{label} #{act['note'].get('id', '')}")
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

        priority = normalize_priority(act.get('Prioridad') or act.get('priority'))
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
                                 ['Respuesta Pública', 'Reenvío', 'Respuesta a reenvío',
                                  'Nota pública', 'Nota privada', 'Nota telefónica'])]),
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

def load_names():
    names = {}
    for fp in ["actores/AllAgents0.json", "actores/Users0.json"]:
        try:
            with open(fp, encoding='utf-8') as f:
                for entry in json.load(f):
                    u = entry['user']
                    names[str(u['id'])] = u['name']
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    return names


@st.cache_data(show_spinner="⏳ Cargando encuestas de satisfacción...")
def load_survey():
    try:
        with open("encuesta/Surveys0.json", encoding='utf-8') as f:
            data = json.load(f)
        results = data[0]['survey'].get('survey_results', [])
        return pd.DataFrame(results)
    except (FileNotFoundError, json.JSONDecodeError, IndexError, KeyError):
        return pd.DataFrame()

data_dir = "."
agent_names = load_names()
survey_raw = load_survey()
tz_offset = -6
sla_resp_h = 8
sla_all_resp_h = 24
sla_res_h = 360
gap_th_h = 24

# ─── LOAD DATA ────────────────────────────────────────────────────────────────
files_found = sorted(glob.glob(os.path.join(data_dir, "activities_*.json")))

if not files_found:
    st.error(f"⚠️ No se encontraron archivos `activities_*.json` en `{os.path.abspath(data_dir)}`")
    st.info("Ajusta la variable `data_dir` en el código o coloca los archivos en el directorio raíz.")
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
reload_cols = st.columns([9, 1])
with reload_cols[1]:
    st.caption("")
    if st.button("↻", help="Recargar archivos"):
        st.cache_data.clear()
        st.rerun()

st.markdown("#  Freshdesk IGNITE Dashboard")

meta_cols = st.columns(4)
meta_cols[0].caption(f"🗀 {len(files_found)} archivos cargados")
meta_cols[1].caption(f"✉︎ {df_raw['ticket_num'].nunique()} tickets")
meta_cols[2].caption(f"⌨ {len(df_raw)} actividades")
meta_cols[3].caption(f"🗓 {df_raw['date'].min()} → {df_raw['date'].max()}" if not df_raw.empty else "")

fc1, fc2, fc3 = st.columns(3)
with fc1:
    sel_tickets = st.multiselect("Tickets", sorted(df_raw['ticket_id'].unique()), placeholder="Todos")
with fc2:
    human_agents = sorted(a for a in df_raw['performer_name'].unique() if '⚙️' not in a)
    sel_agents = st.multiselect("Agentes y clientes", human_agents, placeholder="Todos")
with fc3:
    sel_types = st.multiselect("Tipo actividad", sorted(df_raw['activity_type'].unique()), placeholder="Todos")

fc4, fc5 = st.columns(2)
with fc4:
    date_vals = sorted(df_raw['date'].dropna().unique())
    if len(date_vals) >= 2:
        date_range = st.date_input("Rango fechas", value=(date_vals[0], date_vals[-1]),
                                   min_value=date_vals[0], max_value=date_vals[-1])
    else:
        date_range = None
with fc5:
    productos = sorted(df_raw['ticket_product'].unique())
    sel_productos = st.multiselect("Producto", productos, placeholder="Todos")

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
    "𓃑 Overview",
    "🔍︎ Trazabilidad",
    "⌛︎ SLA y tiempos",
    "𖨆 Agentes y clientes",
    "⚠ Cuellos de botella",
    "🖧 Grafo de relaciones",
    "</> Datos en bruto",
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
        (f"{avg_ttfr:.1f}h" if avg_ttfr else "—", "Tiempo de 1ra res Promedio (todos)", "warn"),
        (n_alerts,   " Alertas",      "danger" if n_alerts > 0 else "ok"),
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
    prod_counts = sla_df.groupby(['producto', 'last_status']).size().reset_index(name='count')
    prod_order = sorted(sla_df['producto'].unique())
    fig = px.bar(
        prod_counts,
        x='producto', y='count', color='last_status',
        title='Distribución de tickets por producto y estado',
        color_discrete_map={'Open': '#f03e3e', 'Pending': '#f59f00', 'Resolved': '#40c057', 'Closed': '#94a3b8'},
        category_orders={'producto': prod_order, 'last_status': ['Open', 'Pending', 'Resolved', 'Closed']},
        labels={'producto': 'Producto', 'count': 'Tickets', 'last_status': 'Estado'},
        barmode='group',
    )
    apply_theme(fig, height=350)
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("☰ Ver listado de tickets por producto"):
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
                    'last_status': 'Estado', 'ttfr_h': 'Tiempo de 1ra res',
                    'resolution_h': 'Resolución', 'n_agents': '# Agentes y clientes',
                }),
                use_container_width=True, hide_index=True,
            )

    # ── SLA compliance summary ──
    st.markdown("<div class='sec-header'>CUMPLIMIENTO SLA</div>", unsafe_allow_html=True)
    total_tickets = len(sla_df)
    any_breach = sla_df['ttfr_breach'] | sla_df['all_resp_breach'] | sla_df['ttr_breach']
    n_breach = any_breach.sum()
    n_resolved_ok = sla_df[sla_df['is_resolved'] & ~sla_df['ttr_breach']].shape[0]
    breach_ttfr = sla_df[sla_df['ttfr_breach']]
    breach_res = sla_df[sla_df['ttr_breach']]
    avg_excess_ttfr = (breach_ttfr['ttfr_h'] - breach_ttfr['ttfr_sla']).mean() if len(breach_ttfr) else None
    avg_excess_res = (breach_res['resolution_h'] - breach_res['res_sla']).mean() if len(breach_res) else None
    if avg_excess_ttfr is not None and avg_excess_res is not None:
        excess_str = f"+{avg_excess_ttfr:.1f}h Tiempo de 1ra res  ·  +{avg_excess_res:.1f}h Resol"
    elif avg_excess_ttfr is not None:
        excess_str = f"+{avg_excess_ttfr:.1f}h Tiempo de 1ra res"
    elif avg_excess_res is not None:
        excess_str = f"+{avg_excess_res:.1f}h Resol"
    else:
        excess_str = "—"

    sc1, sc2, sc3, sc4, sc5 = st.columns(5)
    sc1.metric("Total tickets", total_tickets)
    sc2.metric("Incumplieron SLA", f"{n_breach} ({n_breach/total_tickets*100:.0f}%)" if total_tickets else "0", delta_color="inverse")
    sc3.metric("Resueltos sin incumplir", f"{n_resolved_ok} ({n_resolved_ok/total_tickets*100:.0f}%)" if total_tickets else "0")
    sc4.metric("Tiempo promedio de 1ra respuesta", excess_str)
    sc5.metric("Reabiertos", sla_df['reopened'].sum())

    st.caption(
        f"**{n_breach}** incumplieron SLA  ·  **{n_resolved_ok}** resueltos sin incumplir  ·  "
        f"**{len(breach_ttfr)}** con Tiempo de 1ra res incumplido  ·  "
        f"**{len(breach_res)}** con resolución incumplida  ·  "
        f"**{sla_df['reopened'].sum()}** reabiertos"
    )

    c1, c2 = st.columns(2)
    with c1:
        daily = dff.groupby('date').size().reset_index(name='actividades')
        fig = px.bar(daily, x='date', y='actividades', title='Actividad diaria',
                     color_discrete_sequence=['#4f8ef7'])
        apply_theme(fig)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        type_counts = dff['activity_type'].value_counts().reset_index()
        type_counts.columns = ['tipo', 'count']
        fig = px.pie(type_counts, values='count', names='tipo',
                     title='Distribución de tipos de actividad',
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
                        color_continuous_scale='Blues', aspect='auto')
        apply_theme(fig, height=280)
        st.plotly_chart(fig, use_container_width=True)

    with c4:
        ta = dff.groupby('ticket_id').size().reset_index(name='actividades').sort_values('actividades', ascending=False)
        fig = px.bar(ta, x='ticket_id', y='actividades',
                     title='Actividades por ticket',
                     color='actividades', color_continuous_scale='Blues')
        apply_theme(fig, height=280)
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    # Status distribution
    c5, c6 = st.columns(2)
    with c5:
        status_dist = sla_df['last_status'].value_counts().reset_index()
        status_dist.columns = ['estado', 'count']
        fig = px.bar(status_dist, x='estado', y='count', title='Estado actual de tickets',
                     color='estado', color_discrete_sequence=COLOR_SEQ)
        apply_theme(fig, height=260)
        st.plotly_chart(fig, use_container_width=True)

    with c6:
        # Activity over time (line per ticket)
        ticket_activity = dff.groupby('ticket_id').size().reset_index(name='total_acts').sort_values('total_acts', ascending=False)
        top_tickets = ticket_activity.head(10)['ticket_id'].tolist()
        sel_line_tickets = st.multiselect(
            "Tickets a mostrar en línea",
            options=sorted(dff['ticket_id'].unique()),
            default=top_tickets,
            key="line_tickets"
        )
        if not sel_line_tickets:
            sel_line_tickets = top_tickets
        act_ts = dff[dff['ticket_id'].isin(sel_line_tickets)].groupby(['date', 'ticket_id']).size().reset_index(name='count')
        fig = px.line(act_ts, x='date', y='count', color='ticket_id',
                      title='Actividad diaria por ticket',
                      color_discrete_sequence=COLOR_SEQ)
        apply_theme(fig, height=260)
        st.plotly_chart(fig, use_container_width=True)

    # ── Download filtered tickets ──
    st.markdown("<div class='sec-header'>DESCARGAR TICKETS FILTRADOS</div>", unsafe_allow_html=True)
    status_opts = sorted(sla_df['last_status'].unique())
    prod_opts = sorted(sla_df['producto'].unique())
    filt_status = st.multiselect("Filtrar por estado", status_opts, default=list(status_opts), key="dl_status")
    filt_prod = st.multiselect("Filtrar por producto", prod_opts, default=list(prod_opts), key="dl_prod")
    filtered = sla_df[sla_df['last_status'].isin(filt_status) & sla_df['producto'].isin(filt_prod)]
    dl_cols = ['ticket_id', 'priority', 'last_status', 'producto', 'ttfr_h', 'resolution_h', 'n_agents', 'n_activities']
    dl_tbl = filtered[dl_cols].copy()
    dl_tbl['ttfr_h'] = dl_tbl['ttfr_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
    dl_tbl['resolution_h'] = dl_tbl['resolution_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
    dl_tbl.columns = ['Ticket', 'Prioridad', 'Estado', 'Producto', 'Tiempo 1ra res', 'Resolución', '# Agentes', '# Actividades']
    dl_tbl = dl_tbl.reset_index(drop=True)
    st.dataframe(dl_tbl, use_container_width=True, hide_index=True)
    csv_bytes = dl_tbl.to_csv(index=False).encode('utf-8')
    st.download_button("⬇️ Descargar CSV", csv_bytes, file_name="tickets_filtrados.csv", mime="text/csv")

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
        m5.metric("Tiempo de 1ra res",   f"{s['ttfr_h']}h"       if s['ttfr_h']      else "—")
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
        'Nota pública':        '#9775fa',
        'Nota privada':        '#7950f2',
        'Nota telefónica':     '#cc5de8',
        'Nota de difusión':    '#b197fc',
        'Reenvío':             '#f03e3e',
        'Respuesta a reenvío': '#ff6b6b',
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
    st.markdown("<div class='sec-header'>ACTIVIDAD COMPLETA</div>", unsafe_allow_html=True)
    audit = t_df[['timestamp_local', 'performer_name', 'activity_type', 'detail']].copy()
    audit['timestamp_local'] = audit['timestamp_local'].apply(
        lambda x: x.strftime('%d/%m/%Y %H:%M:%S') if x else '—')
    audit.columns = [' Timestamp', ' Actor', ' Tipo', ' Detalle']

    csv_bytes = audit.to_csv(index=False).encode('utf-8')
    st.download_button("⬇ Descargar actividad en CSV", csv_bytes,
                       file_name=f"ticket_{sel_t}_audit.csv", mime='text/csv')
    st.dataframe(audit, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — SLA & TIEMPOS
# ══════════════════════════════════════════════════════════════════════════════
with T[2]:
    st.markdown("<div class='sec-header'>SLA </div>", unsafe_allow_html=True)

    # ── SLA targets reference ──
    st.markdown("#### Objetivos SLA por prioridad")
    sla_ref = pd.DataFrame([
        {
            'Prioridad': prio,
            '1.ª Respuesta': fmt_hours(cfg['resp']),
            'Todas respuestas': fmt_hours(cfg['all_resp']),
            'Resolución': fmt_hours(cfg['res']),
        }
        for prio, cfg in SLA_CONFIG.items()
    ])
    st.dataframe(sla_ref, use_container_width=True, hide_index=True)

    prio_counts = sla_df['priority'].value_counts()
    prio_cards_html = "<div class='metric-grid' style='grid-template-columns: repeat(4, 1fr)'>"
    for prio, emoji, cls in [
        ('Urgente', '', 'danger'),
        ('Alta', '', 'warn'),
        ('Media', '', ''),
        ('Baja', '', ''),
    ]:
        cnt = prio_counts.get(prio, 0)
        prio_cards_html += f"""
        <div class='metric-card'>
            <div class='metric-val {cls}'>{cnt}</div>
            <div class='metric-lbl'>{emoji} {prio}</div>
        </div>"""
    prio_cards_html += "</div>"
    st.markdown(prio_cards_html, unsafe_allow_html=True)

    for prio in ['Urgente', 'Alta', 'Media', 'Baja']:
        subset = sla_df[sla_df['priority'] == prio]
        if subset.empty:
            continue
        with st.expander(f"☰ Tickets {prio} ({len(subset)})"):
            cols = ['ticket_id', 'last_status', 'ttfr_h', 'resolution_h', 'max_gap_h', 'n_agents']
            tbl = subset[cols].copy()
            tbl['ttfr_h'] = tbl['ttfr_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
            tbl['resolution_h'] = tbl['resolution_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
            tbl['max_gap_h'] = tbl['max_gap_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
            st.dataframe(
                tbl.rename(columns={
                    'ticket_id': 'Ticket', 'last_status': 'Estado',
                    'ttfr_h': 'Tiempo de 1ra res', 'resolution_h': 'Resolución',
                    'max_gap_h': 'Max Brecha', 'n_agents': '# Agentes y clientes',
                }),
                use_container_width=True, hide_index=True,
            )

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

    PRIO_COLORS = {'Urgente': '#f03e3e', 'Alta': '#f59f00', 'Media': '#4f8ef7', 'Baja': '#94a3b8'}

    def add_prio_lines(fig, field, sla_subset, sla_prio):
        if sla_prio == "Todas":
            for prio, cfg in SLA_CONFIG.items():
                val = cfg[field]
                fig.add_hline(y=val, line_dash='dash', line_color=PRIO_COLORS[prio],
                              annotation_text=f"{prio} {fmt_hours(val)}", annotation_font_size=10)
        else:
            val = SLA_CONFIG[sla_prio][field]
            fig.add_hline(y=val, line_dash='dash', line_color='#f59f00',
                          annotation_text=f"SLA {fmt_hours(val)}", annotation_font_size=11)

    # ── Chart row 1: Tiempo de 1ra res & Resolution ──
    c1, c2 = st.columns(2)
    with c1:
        ttfr = sla_subset[sla_subset['ttfr_h'].notna()].copy()
        if not ttfr.empty:
            if sla_prio != "Todas":
                ttfr['sla_ok'] = ttfr['ttfr_h'] <= t_resp
                color_cfg = dict(color='sla_ok', color_discrete_map={True: '#40c057', False: '#f03e3e'})
                hover_extra = {'sla_ok': False}
            else:
                ttfr['sla_ok'] = True
                color_cfg = dict(color_discrete_sequence=['#4f8ef7'])
                hover_extra = {}
            fig = px.bar(
                ttfr.sort_values('ttfr_h'),
                x='ticket_id', y='ttfr_h',
                title=f'Tiempo a Primera Respuesta — objetivo {fmt_hours(t_resp) if sla_prio != "Todas" else "por prioridad"}',
                hover_data={'ttfr_h': ':.1f', 'agents': True, **hover_extra},
                labels={'ttfr_h': 'Tiempo de 1ra res (h)', 'ticket_id': 'Ticket', 'sla_ok': 'Cumple SLA'},
                **color_cfg,
            )
            add_prio_lines(fig, 'resp', sla_subset, sla_prio)
            fig.update_traces(hovertemplate=(
                '<b>%{x}</b><br>'
                'Tiempo de 1ra res: %{y:.1f}h (%{customdata[0]:.1f}d)<br>'
                'Agentes: %{customdata[1]}<br>'
                '<extra></extra>'
            ))
            apply_theme(fig, height=320)
            fig.update_yaxes(title_text='Horas')
            st.plotly_chart(fig, use_container_width=True)

    with c2:
        res = sla_subset[sla_subset['resolution_h'].notna()].copy()
        if not res.empty:
            if sla_prio != "Todas":
                res['sla_ok'] = res['resolution_h'] <= t_res
                color_cfg = dict(color='sla_ok', color_discrete_map={True: '#40c057', False: '#f03e3e'})
                hover_extra = {'sla_ok': False}
            else:
                res['sla_ok'] = True
                color_cfg = dict(color_discrete_sequence=['#4f8ef7'])
                hover_extra = {}
            fig = px.bar(
                res.sort_values('resolution_h'),
                x='ticket_id', y='resolution_h',
                title=f'Tiempo hasta Resolución — objetivo {fmt_hours(t_res) if sla_prio != "Todas" else "por prioridad"}',
                hover_data={'resolution_h': ':.1f', **hover_extra},
                labels={'resolution_h': 'Resolución (h)', 'ticket_id': 'Ticket', 'sla_ok': 'Cumple SLA'},
                **color_cfg,
            )
            add_prio_lines(fig, 'res', sla_subset, sla_prio)
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
                'n_agents': '# Agentes y clientes',
            },
        )
        fig.update_traces(hovertemplate=(
            '<b>%{hovertext}</b><br>'
            'Intercambios: %{x}<br>'
            'Resolución: %{y:.1f}h (%{customdata[0]:.1f}d)<br>'
            'Agentes y clientes: %{marker.color:.0f}<br>'
            '<extra></extra>'
        ))
        apply_theme(fig, height=280)
        st.plotly_chart(fig, use_container_width=True)

    # ── Ticket detail expanders for each compliance category ──
    st.markdown("<div class='sec-header'>DESGLOSE POR CATEGORÍA SLA</div>", unsafe_allow_html=True)

    any_breach = sla_df['ttfr_breach'] | sla_df['all_resp_breach'] | sla_df['ttr_breach']
    n_breach = any_breach.sum()
    n_resolved_ok = sla_df[sla_df['is_resolved'] & ~sla_df['ttr_breach']].shape[0]
    breach_ttfr = sla_df[sla_df['ttfr_breach']]
    breach_res = sla_df[sla_df['ttr_breach']]
    reopened_df = sla_df[sla_df['reopened']]

    def fmt_tbl(df):
        cols = ['ticket_id', 'priority', 'last_status', 'ttfr_h', 'resolution_h', 'max_gap_h', 'n_agents']
        out = df[cols].copy()
        out['ttfr_h'] = out['ttfr_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
        out['resolution_h'] = out['resolution_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
        out['max_gap_h'] = out['max_gap_h'].apply(lambda x: f"{x:.1f}h ({x/24:.1f}d)" if pd.notna(x) else '—')
        return out.rename(columns={
            'ticket_id': 'Ticket', 'priority': 'Prioridad',
            'last_status': 'Estado', 'ttfr_h': 'Tiempo de 1ra res',
            'resolution_h': 'Resolución', 'max_gap_h': 'Max Brecha',
            'n_agents': '# Agentes y clientes',
        })

    with st.expander(f"⁴⁰⁴ Tickets que incumplieron SLA ({n_breach})"):
        if n_breach:
            st.dataframe(fmt_tbl(sla_df[any_breach]), use_container_width=True, hide_index=True)
        else:
            st.caption("Ninguno")

    with st.expander(f"✓ Tickets resueltos sin incumplir SLA ({n_resolved_ok})"):
        if n_resolved_ok:
            st.dataframe(fmt_tbl(sla_df[sla_df['is_resolved'] & ~sla_df['ttr_breach']]), use_container_width=True, hide_index=True)
        else:
            st.caption("Ninguno")

    with st.expander(f"◴ Tickets con Tiempo de 1ra res incumplido ({len(breach_ttfr)})"):
        if len(breach_ttfr):
            st.dataframe(fmt_tbl(breach_ttfr), use_container_width=True, hide_index=True)
        else:
            st.caption("Ninguno")

    with st.expander(f"⌛︎ Tickets con resolución incumplida ({len(breach_res)})"):
        if len(breach_res):
            st.dataframe(fmt_tbl(breach_res), use_container_width=True, hide_index=True)
        else:
            st.caption("Ninguno")

    with st.expander(f"↪ Tickets reabiertos ({len(reopened_df)})"):
        if len(reopened_df):
            st.dataframe(fmt_tbl(reopened_df), use_container_width=True, hide_index=True)
        else:
            st.caption("Ninguno")

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

        st.caption(f" {len(inactive_tickets)} tickets con inactividad > {fmt_hours(threshold_h)} ({inactive_days} días).")
    else:
        st.caption(f" No se encontraron tickets con inactividad > {inactive_days} días.")

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
            'ttfr_h':       st.column_config.NumberColumn('Tiempo de 1ra res (h)', format="%.1f"),
            'ttfr_d':       st.column_config.NumberColumn('Tiempo de 1ra res (d)', format="%.1f"),
            'resolution_h': st.column_config.NumberColumn('Resol. (h)', format="%.1f"),
            'resolution_d': st.column_config.NumberColumn('Resol. (d)', format="%.1f"),
            'max_gap_h':    st.column_config.NumberColumn('Max Brecha (h)', format="%.1f"),
            'avg_gap_h':    st.column_config.NumberColumn('Avg Brecha (h)', format="%.1f"),
            'n_agents':     '# Agentes y clientes',
            'reopen_count': ' Reabiertos',
            'ttfr_breach':  ' Tiempo de 1ra res breach',
            'ttr_breach':   ' TTR breach',
        }
    )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — AGENTES
# ══════════════════════════════════════════════════════════════════════════════
with T[3]:
    st.markdown("<div class='sec-header'>ANÁLISIS DE AGENTES Y CLIENTES</div>", unsafe_allow_html=True)

    human = dff[dff['performer_type'] == 'user'].copy()

    c1, c2 = st.columns(2)
    with c1:
        aa = human.groupby('performer_name').size().reset_index(name='actividades').sort_values('actividades')
        fig = px.bar(aa, y='performer_name', x='actividades', orientation='h',
                     title='Actividades totales',
                     color='actividades', color_continuous_scale='Blues')
        apply_theme(fig, height=350)
        fig.update_coloraxes(showscale=False)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        at = human.groupby(['performer_name', 'activity_type']).size().reset_index(name='count')
        fig = px.bar(at, x='performer_name', y='count', color='activity_type',
                     title='Tipos de actividad', barmode='stack',
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
        fig = px.imshow(piv, title=f'Actividad por hora (UTC{tz_offset:+d})',
                        color_continuous_scale='Blues', aspect='auto')
        apply_theme(fig, height=280)
        st.plotly_chart(fig, use_container_width=True)

    # Influence score
    st.markdown("<div class='sec-header'>SCORE DE INFLUENCIA</div>", unsafe_allow_html=True)
    

    infl = human.groupby('performer_name').agg(
        actividades    = ('activity_type', 'count'),
        tickets_únicos = ('ticket_id', 'nunique'),
        respuestas     = ('activity_type', lambda x: (x == 'Respuesta Pública').sum()),
        cambios_estado = ('activity_type', lambda x: (x == 'Cambio de Estado').sum()),
        notas_privadas = ('activity_type', lambda x: x.isin(['Nota privada', 'Nota pública', 'Nota telefónica']).sum()),
        reenvíos       = ('activity_type', lambda x: x.isin(['Reenvío', 'Respuesta a reenvío']).sum()),
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
                 title=' Score',
                 hover_data=['actividades', 'tickets_únicos', 'respuestas', 'cambios_estado'])
    apply_theme(fig, height=300)
    fig.update_coloraxes(showscale=False)
    fig.update_layout(xaxis_tickangle=-30)
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(infl, use_container_width=True, hide_index=True)

    # ── SATISFACCIÓN ──
    st.markdown("<div class='sec-header'>SATISFACCIÓN DE CLIENTES</div>", unsafe_allow_html=True)

    if not survey_raw.empty:
        survey = survey_raw.copy()
        survey['agent_name'] = survey['agent_id'].apply(
            lambda x: agent_names.get(str(int(x)), 'No asignado') if pd.notna(x) else 'No asignado')
        survey['customer_name'] = survey['customer_id'].apply(
            lambda x: agent_names.get(str(int(x)), str(x)) if pd.notna(x) else '—')
        survey['rating_label'] = survey['rating'].map({1: '😊 Feliz', 2: '😐 Neutral', 3: '☹️ Insatisfecho'})
        survey['rating_num'] = survey['rating']

        total_ratings = len(survey)
        avg_rating = survey['rating_num'].mean()
        pct_happy = (survey['rating_num'] == 1).mean() * 100
        pct_unhappy = (survey['rating_num'] == 3).mean() * 100

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Encuestas respondidas", total_ratings)
        s2.metric("Calificación promedio", f"{avg_rating:.2f} / 3")
        s3.metric("😊 Satisfechos", f"{pct_happy:.0f}%")
        s4.metric("☹️ Insatisfechos", f"{pct_unhappy:.0f}%")

        # Distribution pie
        dist = survey['rating_label'].value_counts().reset_index()
        dist.columns = ['rating', 'count']
        fig = px.pie(dist, values='count', names='rating',
                     title='Distribución de Satisfacción',
                     color='rating',
                     color_discrete_map={
                         '😊 Feliz': '#40c057', '😐 Neutral': '#f59f00', '☹️ Insatisfecho': '#f03e3e',
                     },
                     hole=0.45)
        apply_theme(fig, height=280)
        st.plotly_chart(fig, use_container_width=True)

        # Satisfaction by agent
        st.markdown("#### Satisfacción por agente")
        agent_sat = survey[survey['agent_name'] != 'No asignado'].groupby('agent_name').agg(
            total=('rating_num', 'count'),
            feliz=('rating_num', lambda x: (x == 1).sum()),
            neutral=('rating_num', lambda x: (x == 2).sum()),
            insatisfecho=('rating_num', lambda x: (x == 3).sum()),
        ).reset_index()
        agent_sat['promedio'] = agent_sat.apply(
            lambda r: round((r['feliz'] * 1 + r['neutral'] * 2 + r['insatisfecho'] * 3) / r['total'], 2), axis=1)

        if not agent_sat.empty:
            fig = px.bar(
                agent_sat.melt(id_vars='agent_name', value_vars=['feliz', 'neutral', 'insatisfecho'],
                               var_name='rating', value_name='count'),
                x='agent_name', y='count', color='rating', barmode='group',
                title='Ratings por agente',
                color_discrete_map={'feliz': '#40c057', 'neutral': '#f59f00', 'insatisfecho': '#f03e3e'},
                labels={'agent_name': 'Agente', 'count': 'Encuestas', 'rating': ''},
                category_orders={'rating': ['feliz', 'neutral', 'insatisfecho']},
            )
            apply_theme(fig, height=300)
            st.plotly_chart(fig, use_container_width=True)

            st.dataframe(
                agent_sat.rename(columns={
                    'agent_name': 'Agente', 'total': 'Total', 'feliz': '😊 Feliz',
                    'neutral': '😐 Neutral', 'insatisfecho': '☹️ Insatisfecho', 'promedio': 'Promedio',
                }),
                use_container_width=True, hide_index=True,
            )
        else:
            st.caption("Sin datos de satisfacción por agente.")

        # Satisfaction by customer
        st.markdown("#### Satisfacción por cliente")
        cust_sat = survey.groupby('customer_name').agg(
            total=('rating_num', 'count'),
            feliz=('rating_num', lambda x: (x == 1).sum()),
            neutral=('rating_num', lambda x: (x == 2).sum()),
            insatisfecho=('rating_num', lambda x: (x == 3).sum()),
        ).reset_index()
        cust_sat = cust_sat.sort_values('total', ascending=False)
        cust_sat['promedio'] = cust_sat.apply(
            lambda r: round((r['feliz'] * 1 + r['neutral'] * 2 + r['insatisfecho'] * 3) / r['total'], 2), axis=1)

        fig = px.bar(
            cust_sat.sort_values('total'),
            y='customer_name', x='total', orientation='h',
            title='Encuestas respondidas por cliente',
            color_discrete_sequence=['#4f8ef7'],
            labels={'customer_name': 'Cliente', 'total': 'Encuestas'},
        )
        apply_theme(fig, height=400)
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(
            cust_sat.rename(columns={
                'customer_name': 'Cliente', 'total': 'Total', 'feliz': '😊 Feliz',
                'neutral': '😐 Neutral', 'insatisfecho': '☹️ Insatisfecho', 'promedio': 'Promedio',
            }),
            use_container_width=True, hide_index=True,
        )

        # Tickets by satisfaction rating
        st.markdown("#### Tickets por satisfacción")
        ticket_map = {1: '😊 Feliz', 2: '😐 Neutral', 3: '☹️ Insatisfecho'}
        survey['rating_display'] = survey['rating'].map(ticket_map)

        ticket_tbl = survey[['customer_name', 'agent_name', 'rating_display', 'created_at']].copy()
        ticket_tbl.columns = ['Cliente', 'Agente', 'Calificación', 'Fecha']
        ticket_tbl['Fecha'] = pd.to_datetime(ticket_tbl['Fecha']).dt.strftime('%d/%m/%Y')

        for rlabel, emoji, color in [
            ('😊 Feliz', '😊', '#40c057'),
            ('😐 Neutral', '😐', '#f59f00'),
            ('☹️ Insatisfecho', '☹️', '#f03e3e'),
        ]:
            subset = ticket_tbl[ticket_tbl['Calificación'] == rlabel]
            if not subset.empty:
                with st.expander(f"{emoji} {rlabel} — {len(subset)} ticket(s)"):
                    st.dataframe(subset, use_container_width=True, hide_index=True)
    else:
        st.caption("ℹ️ No se encontraron datos de encuestas de satisfacción en `encuesta/Surveys0.json`.")

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
        if row['ttfr_breach']:   a.append(f"⏰ Tiempo de 1ra res {row['ttfr_h']}h>{sla_resp_h}h")
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

    for _, row in dff.iterrows():
        actor  = row['performer_name']
        ticket = row['ticket_id']
        act    = row['activity_type']

        if graph_mode == "Agente ↔ Ticket":
            key = (actor, ticket)
            edge_weights[key] += 1
        else:
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

    # Node traces by type (sqrt normalization to avoid extreme sizes)
    def node_style(n):
        if n.startswith('#'):   return '#4f8ef7', 10 + (node_counts_tick.get(n, 1) ** 0.5) * 7, 'ticket'
        if n.startswith('['):   return '#f59f00', 8 + (node_counts_type.get(n[1:-1], 1) ** 0.5) * 5, 'action'
        if '⚙️' in n:          return '#64748b', 12, 'system'
        return '#40c057', 10 + (node_counts_acts.get(n, 1) ** 0.5) * 6, 'agent'

    for ntype, label, color in [
        ('ticket', 'Ticket', '#4f8ef7'),
        ('agent',  'Agentes y clientes', '#40c057'),
        ('system', 'Sistema','#64748b'),
        ('action', 'Acción', '#f59f00'),
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