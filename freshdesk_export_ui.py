"""Dashboard queries using the same local snapshots as the analytics tabs."""
from datetime import date
import pandas as pd
import streamlit as st
from local_freshdesk_export import ROOT, build_downloads, load_local_dataset, query_local, source_signature
from local_freshdesk_export import UNASSIGNED_GROUP, MAPPING_FIELDS, institution_group, csv_bytes

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
    signature = 'grouped-associations-v2:' + source_signature(ROOT)
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
    mode = st.radio('Consultar por', ['Institución', 'Contacto'], horizontal=True, key='export_mode')
    if mode == 'Institución':
        labels = {**dataset['institutions'], UNASSIGNED_GROUP: 'Sin institución / pendientes de asignar'}
    else:
        labels = {uid: f"{c.get('name') or 'Sin nombre'} · {' / '.join(c['institution_names']) or 'Sin institución'} · ID {uid}"
                  for uid, c in contacts.items()}
    if not labels:
        st.info('No hay registros identificados para esta consulta.')
        return
    selected_id = st.selectbox(mode, sorted(labels, key=lambda key: (key == UNASSIGNED_GROUP, labels[key].casefold())),
                               format_func=labels.get, key='export_selected_' + mode)
    group = institution_group(dataset, 'institution' if mode == 'Institución' else 'contact', selected_id)
    with st.expander('Contactos y pendientes de este grupo', expanded=True):
        assigned, provisional, pending = st.columns(3)
        assigned.metric('Contactos del grupo', len(group['assigned_ids']))
        provisional.metric('Cruces provisionales', len({r['contact_id'] for r in group['mapping'] if r['status'] == 'provisional'}))
        pending.metric('Pendientes de asociación', len({r['contact_id'] for r in group['mapping'] if r['status'] == 'unassigned'}))
        records = [{'ID contacto': r['contact_id'], 'Contacto': r['contact_name'],
                    'Institución': r['institution_name'] or 'Sin asignar',
                    'Asociación': ASSOCIATION_LABELS[r['source']],
                    'Revisión': ', '.join(r['issues']),
                    'Posibles duplicados (IDs)': ', '.join(r['duplicate_contact_ids'])}
                   for r in group['mapping']]
        st.dataframe(pd.DataFrame(records), hide_index=True, use_container_width=True)
        if group['pending_ids']:
            st.info('Los contactos pendientes aparecen por evidencia en sus tickets, pero no se incorporan '
                    'automáticamente a esta institución. El cruce conserva sus candidatos e incidencias.')
        if group['duplicates']:
            st.warning('Hay posibles duplicados relacionados con este grupo. Se conservan sus IDs y tickets por separado.')
            st.dataframe(pd.DataFrame(group['duplicates']).rename(columns={
                'field': 'Coincidencia en', 'value': 'Valor', 'contact_ids': 'IDs relacionados'}),
                hide_index=True, use_container_width=True)
        st.download_button('Descargar cruce de este grupo', csv_bytes(group['mapping'], MAPPING_FIELDS),
                           'cruce_contactos_institucion.csv', 'text/csv', key='export_group_mapping')
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
