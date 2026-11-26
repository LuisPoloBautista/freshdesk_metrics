"""Dashboard queries using the same local snapshots as the analytics tabs."""
from datetime import date
import pandas as pd
import streamlit as st
from local_freshdesk_export import ROOT, build_downloads, csv_bytes, load_local_dataset, query_local, source_signature

ASSOCIATION_LABELS = {
    'contact_company_id': 'Institución del contacto',
    'verified_local_mapping': 'Asociación verificada',
    'provisional_from_ticket_institution': 'Provisional: institución en tickets',
    'unassigned': 'Sin asignar',
}


@st.cache_data(show_spinner=False)
def cached_dataset(root, signature):
    return load_local_dataset(root)


def render_export_tab():
    st.subheader('Exportación por contacto e institución')
    st.caption('Consulta los archivos cargados en el dashboard, sin API key. '
               'Los filtros laterales no limitan esta consulta; sin fechas se incluye todo el histórico local disponible.')
    for key in ['export_api_key', 'export_identity', 'export_catalog', 'export_domain']:
        st.session_state.pop(key, None)
    signature = source_signature(ROOT)
    if st.session_state.get('export_local_signature') != signature:
        st.session_state.pop('export_result', None)
        st.session_state['export_local_signature'] = signature
    dataset = cached_dataset(str(ROOT), signature)
    if dataset['errors']:
        st.warning('Algunos archivos no se pudieron leer. La cobertura puede estar incompleta.')
        st.write(dataset['errors'])
    if not dataset['tickets']:
        st.info('No hay tickets disponibles en los archivos locales.')
        return
    contacts = dataset['contacts']
    audit = [{'contact_id': uid, 'name': c.get('name'), 'email': c.get('email'),
              'institutions': c['institution_names'], 'mapping_source': ASSOCIATION_LABELS[c['mapping_source']],
              'issues': c['issues']} for uid, c in contacts.items()]
    with st.expander('Revisar asociaciones de contactos'):
        a, b, c = st.columns(3)
        a.metric('Contactos', len(contacts))
        b.metric('Sin institución asignada', sum(not c['institution_keys'] for c in contacts.values()))
        c.metric('Posibles duplicados', len(dataset['duplicates']))
        st.caption('Las asociaciones por ID y los cruces verificados tienen prioridad. '
                   'Las deducidas de una única institución en los tickets se señalan como provisionales. '
                   'Los casos con varias instituciones posibles quedan para revisión.')
        st.dataframe(pd.DataFrame(audit).rename(columns={
            'contact_id': 'ID contacto', 'name': 'Contacto', 'email': 'Correo',
            'institutions': 'Instituciones', 'mapping_source': 'Asociación', 'issues': 'Pendientes'}),
            hide_index=True, use_container_width=True)
        if dataset['duplicates']:
            st.dataframe(pd.DataFrame(dataset['duplicates']), hide_index=True, use_container_width=True)
        st.download_button('Descargar auditoría de contactos',
                           csv_bytes(audit, ['contact_id', 'name', 'email', 'institutions', 'mapping_source', 'issues']),
                           'auditoria_contactos.csv', 'text/csv', key='export_audit_download')
    mode = st.radio('Consultar por', ['Institución', 'Contacto'], horizontal=True, key='export_mode')
    if mode == 'Institución':
        labels = dataset['institutions']
    else:
        labels = {uid: f"{c.get('name') or 'Sin nombre'} · {' / '.join(c['institution_names']) or 'Sin institución'} · ID {uid}"
                  for uid, c in contacts.items()}
    if not labels:
        st.info('No hay registros identificados para esta consulta.')
        return
    selected_id = st.selectbox(mode, sorted(labels, key=lambda key: labels[key].casefold()),
                               format_func=labels.get, key='export_selected_' + mode)
    st.caption('Por institución se incluyen todos los tickets de sus contactos vinculados. '
               'Los tickets con institución registrada pero contacto sin asociación resuelta se señalan por separado.')
    available_dates = sorted(t['created_at'][:10] for t in dataset['tickets'].values() if t.get('created_at'))
    products = sorted({t['product'] for t in dataset['tickets'].values() if t.get('product')})
    status_labels = {2: 'Abierto', 3: 'Pendiente', 4: 'Resuelto', 5: 'Cerrado'}
    states = sorted({t['status'] for t in dataset['tickets'].values() if t.get('status') is not None}, key=str)
    with st.form('export_query_form'):
        date_filter = st.checkbox('Filtrar por fechas', value=False)
        left, right = st.columns(2)
        start = left.date_input('Desde (UTC)', value=date.fromisoformat(available_dates[0]) if available_dates else date.today())
        end = right.date_input('Hasta (UTC)', value=date.fromisoformat(available_dates[-1]) if available_dates else date.today())
        date_field = st.selectbox('Fecha del ticket', ['created_at', 'updated_at'],
                                  format_func=lambda value: 'Creación' if value == 'created_at' else 'Última actualización')
        selected_products = st.multiselect('Producto', products, placeholder='Todos')
        statuses = st.multiselect('Estado', states, format_func=lambda value: status_labels.get(value, str(value)))
        include_private = st.checkbox('Incluir notas internas disponibles en archivos separados', value=False)
        submitted = st.form_submit_button('Consultar y preparar descarga')
    if submitted:
        st.session_state.pop('export_result', None)
        try:
            result = query_local(dataset, 'institution' if mode == 'Institución' else 'contact', selected_id,
                                 start.isoformat() if date_filter else None, end.isoformat() if date_filter else None,
                                 date_field, selected_products, statuses, include_private)
            files, archive = build_downloads(result)
            st.session_state['export_result'] = {**result, 'files': files, 'zip': archive,
                                                  'label': labels[selected_id], 'mode': mode}
        except ValueError as exc:
            st.error(str(exc))
    result = st.session_state.get('export_result')
    if not result:
        return
    manifest = result['manifest']
    st.markdown(f"**Última consulta preparada:** {result['mode']} · {result['label']}")
    st.caption('Si cambias la selección o los filtros, vuelve a pulsar Consultar para actualizar las descargas.')
    st.json(manifest['filters'], expanded=False)
    a, b, c = st.columns(3)
    a.metric('Contactos incluidos', manifest['contacts'])
    b.metric('Tickets', manifest['tickets_exported'])
    c.metric('Respuestas sin texto disponible', manifest['conversation_text_missing'])
    if manifest['conversation_text_missing']:
        st.warning('Los archivos de actividades contienen referencias a respuestas, pero no su texto completo. '
                   'La descarga identifica estas referencias; no equivale a una exportación completa de conversaciones.')
    st.caption('Se conservan las descripciones y los textos completos que sí estén presentes en los archivos locales.')
    if result['unresolved']:
        st.warning('Hay tickets que mencionan esta institución, pero la asociación del contacto es ambigua '
                   'o está sin resolver. No se mezclan con los tickets asociados; se incluyen en un reporte separado.')
        st.dataframe(pd.DataFrame(result['unresolved']), hide_index=True, use_container_width=True)
    preview = [{'Ticket': t['id'], 'Contacto': t['contact_name'],
                'Institución del contacto': ', '.join(t['contact_institutions']),
                'Institución del ticket': t['institution_name'], 'Asunto': t.get('subject'),
                'Producto': t.get('product'), 'Estado': status_labels.get(t.get('status'), t.get('status_name')),
                'Creado': t.get('created_at'), 'Asociación': ASSOCIATION_LABELS[t['mapping_source']]} for t in result['tickets']]
    st.dataframe(pd.DataFrame(preview), hide_index=True, use_container_width=True)
    with st.expander('Cobertura por contacto', expanded=True):
        coverage = pd.DataFrame(result['coverage'])
        if not coverage.empty:
            coverage['mapping_source'] = coverage['mapping_source'].map(ASSOCIATION_LABELS)
        st.dataframe(coverage.rename(columns={'contact_id': 'ID contacto', 'contact_name': 'Contacto',
                     'institution_names': 'Instituciones', 'mapping_source': 'Asociación',
                     'tickets_before_filters': 'Tickets antes de filtros', 'tickets_exported': 'Tickets incluidos',
                     'ticket_ids': 'IDs de tickets'}), hide_index=True, use_container_width=True)
    st.download_button('Descargar tickets CSV', result['files']['tickets.csv'], 'tickets_locales.csv', 'text/csv')
    st.download_button('Descargar tickets JSON', result['files']['tickets.json'], 'tickets_locales.json', 'application/json')
    st.download_button('Descargar paquete completo ZIP', result['zip'], 'freshdesk_local.zip', 'application/zip')
    with st.expander('Cobertura y límites de los datos locales'):
        st.json(manifest)
    if st.button('Borrar resultados de esta sesión', key='export_clear'):
        st.session_state.pop('export_result', None)
        st.rerun()
