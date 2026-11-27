"""Simple institution/contact Excel download using local history and Freshdesk."""
from datetime import date
import hashlib
import streamlit as st
from local_freshdesk_export import ROOT, load_local_dataset, source_signature, UNASSIGNED_GROUP
from export_identity import contact_groups
from freshdesk_history_export import export_history, refresh_catalog
from freshdesk_excel import build_excel
from scripts.export_freshdesk import Client, DEFAULT_DOMAIN


@st.cache_data(show_spinner=False)
def cached_dataset(root, signature):
    return load_local_dataset(root)


def excel_download(dataset, mode, selected, start, end, api_key):
    """Deferred download: no Streamlit/session mutations in the worker thread."""
    result = export_history(dataset, mode, selected, start.isoformat(), end.isoformat(),
                            Client(DEFAULT_DOMAIN, api_key), include_private=False)
    return build_excel(result)


def render_export_tab():
    st.subheader('Exportación por contacto e institución')
    api_key = st.text_input('API key de Freshdesk', type='password', key='full_content_api_key').strip()
    signature = 'simple-excel-v2:' + source_signature(ROOT)
    identity = hashlib.sha256((signature + '\0' + api_key).encode()).hexdigest()
    if st.session_state.get('full_content_identity') != identity:
        st.session_state.pop('export_result', None)
        st.session_state.pop('export_catalog', None)
        st.session_state.pop('export_catalog_errors', None)
        st.session_state['full_content_identity'] = identity
    dataset = st.session_state.get('export_catalog')
    if dataset is None:
        dataset = cached_dataset(str(ROOT), signature)
        if api_key:
            try:
                with st.spinner('Cargando contactos e instituciones…'):
                    dataset, errors = refresh_catalog(dataset, Client(DEFAULT_DOMAIN, api_key))
                st.session_state['export_catalog'] = dataset
                st.session_state['export_catalog_errors'] = errors
            except ValueError as exc:
                st.error(str(exc))
                return
    if api_key and st.session_state.get('export_catalog_errors'):
        st.warning('No se pudo actualizar todo el catálogo. La descarga indicará las incidencias.')
    mode = st.radio('Consultar por', ['Institución', 'Contacto'], horizontal=True, key='export_mode')
    if mode == 'Institución':
        labels = {**dataset['institutions'], UNASSIGNED_GROUP: 'Sin institución'}
    else:
        groups = dataset.get('contact_groups') or contact_groups(dataset['contacts'])
        labels = {key: f"{g['name']} · {', '.join(g['emails']) or 'ID ' + key}" for key, g in groups.items()}
    if not labels:
        st.info('No hay contactos o instituciones disponibles.')
        return
    selected = st.selectbox(mode, sorted(labels, key=lambda k: (k == UNASSIGNED_GROUP, labels[k].casefold())),
                            format_func=labels.get, key='export_selected_' + mode)
    first = dataset.get('history_coverage', {}).get('first_activity_utc')
    default_start = date.fromisoformat(first[:10]) if first else date(2026, 4, 15)
    left, right = st.columns(2)
    start = left.date_input('Desde (UTC)', value=default_start, key='export_start')
    end = right.date_input('Hasta (UTC)', value=date.today(), key='export_end')
    if start > end:
        st.error('La fecha inicial debe ser anterior o igual a la final.')
    st.caption('Incluye tickets creados o con movimientos en el rango, en todos los estados, y sus respuestas públicas. La cobertura e incidencias se detallan en el Excel.')
    st.download_button('Descargar data',
        data=lambda: excel_download(dataset, 'institution' if mode == 'Institución' else 'contact', selected, start, end, api_key),
        file_name='freshdesk.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        disabled=not api_key or start > end, on_click='ignore', type='primary')
