"""Institution/contact exports combining local history and Freshdesk content."""
from datetime import date
import hashlib
import io
import zipfile
import streamlit as st
from local_freshdesk_export import ROOT, load_local_dataset, source_signature, build_downloads, UNASSIGNED_GROUP
from export_identity import contact_groups
from freshdesk_history_export import export_history, refresh_catalog
from freshdesk_excel import build_excel
from scripts.export_freshdesk import Client, DEFAULT_DOMAIN


@st.cache_data(show_spinner=False)
def cached_dataset(root, signature):
    return load_local_dataset(root)


def render_export_tab():
    st.subheader('Exportación por contacto e institución')
    st.caption('Selecciona un nombre y un rango: tickets creados o con movimientos en esas fechas, en todos los estados. Los filtros laterales no limitan esta consulta.')
    signature = 'history-excel-v1:' + source_signature(ROOT)
    content_mode = st.radio('Contenido de la descarga', ['Texto completo desde Freshdesk', 'Solo datos locales'], key='full_content_mode', horizontal=True)
    use_api = content_mode == 'Texto completo desde Freshdesk'
    domain, api_key = DEFAULT_DOMAIN, ''
    if use_api:
        domain = st.text_input('Dominio de Freshdesk', value=DEFAULT_DOMAIN, key='full_content_domain')
        api_key = st.text_input('API key de Freshdesk', type='password', key='full_content_api_key')
        st.caption('La API permite actualizar las listas, encontrar tickets adicionales y recuperar descripciones y respuestas completas. La clave no se incluye en las descargas.')
    identity = hashlib.sha256((signature + content_mode + domain + '\0' + api_key).encode()).hexdigest()
    if st.session_state.get('full_content_identity') != identity:
        st.session_state.pop('export_result', None)
        st.session_state.pop('export_catalog', None)
        st.session_state['full_content_identity'] = identity
    dataset = st.session_state.get('export_catalog') or cached_dataset(str(ROOT), signature)
    if use_api and st.button('Actualizar lista de contactos e instituciones', disabled=not api_key.strip()):
        try:
            with st.spinner('Actualizando catálogos…'):
                dataset, errors = refresh_catalog(dataset, Client(domain.strip(), api_key.strip()))
            st.session_state['export_catalog'] = dataset
            if errors:
                st.warning('Catálogo parcial; se conservan las referencias locales.')
                st.dataframe(errors)
            else:
                st.success('Listas actualizadas y agrupadas.')
        except ValueError as exc:
            st.error(str(exc))
    coverage = dataset.get('history_coverage', {})
    st.caption(f"Histórico local: {coverage.get('first_activity_utc') or 'sin fecha disponible'} → {coverage.get('last_activity_utc') or 'sin fecha disponible'}. Días sin archivo: {len(coverage.get('missing_file_dates', []))}.")
    if dataset['errors']:
        st.warning('Hay archivos locales ilegibles. Revisa la cobertura de la descarga.')
    mode = st.radio('Consultar por', ['Institución', 'Contacto'], horizontal=True, key='export_mode')
    if mode == 'Institución':
        labels = {**dataset['institutions'], UNASSIGNED_GROUP: 'Sin institución / pendientes de asignar'}
    else:
        groups = dataset.get('contact_groups') or contact_groups(dataset['contacts'])
        labels = {key: f"{g['name']} · {', '.join(g['emails']) or 'sin correo'} · IDs {', '.join(g['contact_ids'])}" for key, g in groups.items()}
    if not labels:
        st.info('Actualiza el catálogo con la API para disponer de nombres.')
        return
    selected = st.selectbox(mode, sorted(labels, key=lambda k: (k == UNASSIGNED_GROUP, labels[k].casefold())), format_func=labels.get, key='export_selected_' + mode)
    st.caption('Se agrupan variantes de institución mediante nombres normalizados y alias. Los contactos se agrupan por correo, identificador externo o equivalencia verificada; compartir nombre no basta.')
    first = coverage.get('first_activity_utc')
    default_start = date.fromisoformat(first[:10]) if first else date(2026, 4, 15)
    with st.form('history_export_form'):
        start = st.date_input('Desde (UTC)', value=default_start, key='export_start')
        end = st.date_input('Hasta (UTC)', value=date.today(), key='export_end')
        private = st.checkbox('Incluir notas internas accesibles', value=False)
        submitted = st.form_submit_button('Consultar y preparar descarga', disabled=use_api and not api_key.strip())
    if submitted:
        st.session_state.pop('export_result', None)
        if start > end:
            st.error('La fecha inicial debe ser anterior o igual a la final.')
        else:
            try:
                indicator = st.progress(0, text='Reconciliando contactos y tickets…')
                try:
                    result = export_history(dataset, 'institution' if mode == 'Institución' else 'contact', selected,
                        start.isoformat(), end.isoformat(), Client(domain.strip(), api_key.strip()) if use_api else None,
                        private, lambda done, total: indicator.progress(done / max(total, 1), text=f'Recuperando contenido: {done}/{total}'))
                    result.update(label=labels[selected], mode=mode)
                    excel = build_excel(result)
                    files, archive = build_downloads(result)
                    buffer = io.BytesIO(archive)
                    with zipfile.ZipFile(buffer, 'a', zipfile.ZIP_DEFLATED) as package:
                        package.writestr('freshdesk.xlsx', excel)
                    st.session_state['export_result'] = {**result, 'excel': excel, 'files': files, 'zip': buffer.getvalue()}
                finally:
                    indicator.empty()
            except ValueError as exc:
                st.error(str(exc))
    result = st.session_state.get('export_result')
    if not result:
        return
    manifest = result['manifest']
    st.markdown(f"**Consulta preparada:** {result['mode']} · {result['label']}")
    st.caption(f"Rango UTC: {manifest['filters']['start']} → {manifest['filters']['end']}. Si cambias la selección, vuelve a preparar la descarga.")
    a, b, c = st.columns(3)
    a.metric('Contactos incluidos', manifest['contacts'])
    b.metric('Tickets', manifest['tickets_exported'])
    c.metric('Actividades locales', manifest['local_activity_count'])
    if manifest.get('extraction_complete'):
        st.success('Descripciones y páginas de respuestas accesibles recuperadas; revisa los límites de cobertura del histórico.')
    else:
        st.warning('Resultado parcial o local. Consulta Incidencias y Resumen en el Excel; los datos locales no garantizan textos completos.')
    if manifest['included_by_ticket_institution']:
        st.info('Se incluyen tickets con esta institución registrada aunque el contacto esté sin resolver. Esto no asigna a la institución los demás tickets de ese contacto.')
    st.caption('Excel incluye tickets, respuestas, actividades, cambios de estado, asociaciones y cobertura. Conserva el historial disponible de los tickets seleccionados, incluso fuera del rango, con una marca de fecha. Textos extensos se dividen sin recortes en Textos_largos.')
    with st.expander('Contactos y pendientes de este grupo'):
        st.dataframe(result['mapping'], hide_index=True)
        st.caption('Provisional: asociación deducida o conservada del respaldo. Sin asignar: no hay una asociación resuelta. Los posibles duplicados mantienen sus IDs.')
    with st.expander('Cobertura por contacto'):
        st.dataframe(result['coverage'], hide_index=True)
    st.download_button('Descargar Excel', result['excel'], 'freshdesk.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    st.download_button('Descargar tickets JSON', result['files']['tickets.json'], 'tickets.json', 'application/json')
    st.download_button('Descargar paquete ZIP (Excel y datos)', result['zip'], 'freshdesk.zip', 'application/zip')
    with st.expander('Cobertura y límites de los datos'):
        st.json(manifest)
    if st.button('Borrar resultados de esta sesión', key='export_clear'):
        st.session_state.pop('export_result', None)
        st.rerun()
