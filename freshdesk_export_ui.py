"""Session-isolated Streamlit UI for full-text Freshdesk queries."""
import hashlib
import io
import json
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import streamlit as st

from scripts.export_freshdesk import (
    Client, DEFAULT_DOMAIN, audit_contacts, export_data, read_mapping,
    select_contacts, write_csv,
)


def package_downloads(folder):
    # Do not bundle unrelated contacts, credentials, or other institutions.
    names = ['tickets.csv', 'tickets.json', 'conversations_public.csv',
             'notes_private.csv', 'notes_private.json', 'coverage_by_contact.csv',
             'selected_contacts.json', 'activity_history_available.json',
             'company_tickets_unmapped.json', 'api_pages.json', 'manifest.json', 'mapping_used.json']
    files = {name: (folder / name).read_bytes() for name in names if (folder / name).exists()}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return files, buffer.getvalue()


def render_export_tab():
    st.subheader('Exportación por contacto e institución')
    st.caption('Consulta Freshdesk directamente. Los filtros laterales del dashboard no limitan esta consulta. '
               'Sin rango de fechas se incluye todo el histórico accesible.')
    st.info('Usa tu propia API key de Freshdesk. La consulta tendrá los permisos de tu cuenta. '
            'La clave se utiliza en esta sesión y no se guarda en los archivos descargados.')
    domain = st.text_input('Dominio de Freshdesk', value=DEFAULT_DOMAIN, key='export_domain')
    key = st.text_input('API key de Freshdesk', type='password', key='export_api_key')
    fingerprint = hashlib.sha256((domain + '\0' + key).encode()).hexdigest()
    if st.session_state.get('export_identity') != fingerprint:
        for name in ['export_catalog', 'export_result', 'export_error']:
            st.session_state.pop(name, None)
        st.session_state['export_identity'] = fingerprint
    if st.button('Cargar contactos e instituciones', key='export_connect', disabled=not key):
        st.session_state.pop('export_result', None)
        st.session_state.pop('export_catalog', None)
        try:
            with st.spinner('Consultando contactos e instituciones…'):
                api = Client(domain.strip(), key.strip())
                contacts = api.listing('contacts')
                companies = api.listing('companies')
                st.session_state['export_catalog'] = {'contacts': contacts, 'companies': companies}
        except Exception:
            st.error('No se pudo cargar el catálogo. Revisa el dominio, la API key y los permisos de tu cuenta.')
    catalog = st.session_state.get('export_catalog')
    if not catalog:
        return
    contacts, companies = catalog['contacts'], catalog['companies']
    audit, duplicates = audit_contacts(contacts, companies)
    unassigned = [r for r in audit if not r['company_ids']]
    with st.expander('Revisar asociaciones de contactos'):
        a, b, c = st.columns(3)
        a.metric('Contactos', len(contacts))
        b.metric('Sin institución', len(unassigned))
        c.metric('Posibles duplicados', len(duplicates))
        st.dataframe(pd.DataFrame(audit), hide_index=True, use_container_width=True)
        if duplicates:
            st.caption('Las coincidencias son casos para revisar; no se fusionan contactos automáticamente.')
            st.dataframe(pd.DataFrame(duplicates), hide_index=True, use_container_width=True)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'audit.csv'
            write_csv(path, audit, ['contact_id', 'name', 'email', 'unique_external_id', 'company_ids', 'company_names', 'issues'])
            st.download_button('Descargar auditoría de contactos', path.read_bytes(), 'auditoria_contactos.csv',
                               'text/csv', key='export_audit_download')
    mode = st.radio('Consultar por', ['Institución', 'Contacto'], horizontal=True, key='export_mode')
    options = companies if mode == 'Institución' else contacts
    labels = {r['id']: f"{r.get('name') or 'Sin nombre'} · {r['id']}" for r in options}
    if not labels:
        st.info('No hay registros disponibles para esta consulta.')
        return
    selected_id = st.selectbox(mode, sorted(labels, key=lambda i: labels[i].casefold()),
                               format_func=labels.get, key='export_selected_' + mode)
    st.caption('La consulta por contacto incluye los tickets donde figura como solicitante. '
               'La consulta por institución reúne los tickets de todos sus contactos, incluidas asociaciones secundarias.')
    with st.expander('Cruce provisional por identificadores'):
        st.caption('Opcional: CSV con contact_id, company_id, status y evidence. '
                   'status: verified o provisional. Se usa solo para esta consulta; no modifica Freshdesk.')
        mapping_file = st.file_uploader('Cruce contacto–institución', type=['csv'], key='export_mapping')
    with st.form('export_query_form'):
        date_filter = st.checkbox('Filtrar por fechas', value=False)
        left, right = st.columns(2)
        start = left.date_input('Desde (UTC)', value=date.today().replace(day=1))
        end = right.date_input('Hasta (UTC)', value=date.today())
        date_field = st.selectbox('Fecha del ticket', ['created_at', 'updated_at'],
                                  format_func=lambda v: 'Creación' if v == 'created_at' else 'Última actualización')
        product = st.text_input('Producto (opcional)', help='Nombre exacto del campo Producto; vacío incluye todos.')
        statuses = st.multiselect('Estado', [2, 3, 4, 5],
                                  format_func=lambda v: {2: 'Abierto', 3: 'Pendiente', 4: 'Resuelto', 5: 'Cerrado'}[v])
        include_private = st.checkbox('Incluir notas internas en archivos separados', value=False,
                                       help='Solo para personal autorizado a leer y recibir estas notas.')
        submitted = st.form_submit_button('Consultar y preparar descarga')
    if submitted:
        st.session_state.pop('export_result', None)
        if date_filter and start > end:
            st.error('La fecha inicial debe ser anterior o igual a la final.')
        else:
            try:
                with st.spinner('Recuperando tickets y todas las páginas de conversaciones…'):
                    api = Client(domain.strip(), key.strip())
                    # Refresh membership for every extraction; do not rely on a stale catalog.
                    current_contacts = api.listing('contacts')
                    current_companies = api.listing('companies')
                    st.session_state['export_catalog'] = {'contacts': current_contacts, 'companies': current_companies}
                    with tempfile.TemporaryDirectory() as temp:
                        folder = Path(temp)
                        mapping_path = None
                        if mapping_file is not None:
                            mapping_path = folder / 'input_mapping.csv'
                            mapping_path.write_bytes(mapping_file.getvalue())
                        mappings = read_mapping(mapping_path, current_contacts, current_companies)
                        if mapping_path is not None:
                            mapping_path.unlink()
                        args = SimpleNamespace(
                            company_id=selected_id if mode == 'Institución' else None,
                            contact_id=selected_id if mode == 'Contacto' else None,
                            start_date=start.isoformat() if date_filter else None,
                            end_date=end.isoformat() if date_filter else None,
                            date_field=date_field, status=statuses, product=product.strip() or None,
                            product_id=None, product_field='cf_producto', include_private_notes=include_private)
                        selected = select_contacts(current_contacts, mappings, args.contact_id, args.company_id)
                        if not selected:
                            raise ValueError('No hay contactos asociados a la selección. Revisa el cruce provisional.')
                        used_mappings = [m for m in mappings if m['contact_id'] in selected]
                        (folder / 'mapping_used.json').write_text(json.dumps(used_mappings, ensure_ascii=False), encoding='utf-8')
                        manifest = export_data(api, selected, args, folder)
                        files, archive = package_downloads(folder)
                        tickets = json.loads(files['tickets.json'])
                        preview = [{k: t.get(k) for k in ['id', 'requester_id', 'subject', 'status', 'created_at', 'updated_at']}
                                   for t in tickets]
                        st.session_state['export_result'] = {
                            'label': labels[selected_id], 'mode': mode, 'manifest': manifest,
                            'files': files, 'zip': archive, 'preview': preview,
                            'coverage': pd.read_csv(io.BytesIO(files['coverage_by_contact.csv'])),
                        }
            except ValueError as exc:
                st.error(f'No se pudo preparar la consulta: {exc}')
            except Exception:
                st.error('La consulta falló. Revisa permisos y conexión e inténtalo de nuevo. '
                         'No se presenta una descarga como completa si falla la recuperación.')
    result = st.session_state.get('export_result')
    if result:
        manifest = result['manifest']
        st.markdown(f"**Última consulta preparada:** {result['mode']} · {result['label']}")
        st.caption('Las descargas corresponden a esta consulta. Si cambias la selección o los filtros, vuelve a pulsar Consultar.')
        st.json(manifest['filters'], expanded=False)
        a, b, c = st.columns(3)
        a.metric('Contactos incluidos', manifest['contact_count'])
        b.metric('Tickets', manifest['tickets_exported'])
        c.metric('Conversaciones públicas', manifest['public_conversations'])
        if manifest['queries_completed']:
            st.success('Consulta terminada. Revisa la cobertura por contacto y los límites del histórico.')
        else:
            st.warning('Resultado parcial: hay datos que no se pudieron recuperar.')
            st.write(manifest['errors'])
        if manifest['company_query_unmapped_ids']:
            st.warning('Hay tickets de la institución cuyo solicitante no está asociado. Revisa el reporte de cobertura.')
        st.dataframe(pd.DataFrame(result['preview']), hide_index=True, use_container_width=True)
        st.dataframe(result['coverage'], hide_index=True, use_container_width=True)
        suffix = 'parcial' if not manifest['queries_completed'] else 'consulta'
        st.download_button('Descargar tickets CSV', result['files']['tickets.csv'], f'tickets_{suffix}.csv', 'text/csv')
        st.download_button('Descargar tickets JSON', result['files']['tickets.json'], f'tickets_{suffix}.json', 'application/json')
        st.download_button('Descargar paquete completo ZIP', result['zip'], f'freshdesk_{suffix}.zip', 'application/zip')
        with st.expander('Cobertura y límites de la consulta'):
            st.json(manifest)
        if st.button('Borrar resultados de esta sesión', key='export_clear'):
            st.session_state.pop('export_result', None)
            st.rerun()
