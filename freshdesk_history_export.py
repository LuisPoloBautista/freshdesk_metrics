"""Reuse local traceability, reconcile scoped API tickets, then select by movement."""
from collections import defaultdict
from copy import deepcopy

from export_identity import contact_groups
from local_freshdesk_export import (
    UNASSIGNED_GROUP, contact_mapping, identifier, query_local, timestamp,
)
from scripts.export_freshdesk import ApiError, EPOCH, company_ids
from freshdesk_full_content import fetch_full_content


def scoped_contact_ids(dataset, mode, selected_id):
    if mode == 'contact':
        groups = dataset.get('contact_groups') or contact_groups(dataset['contacts'])
        for group in groups.values():
            if selected_id == group['id'] or selected_id in group['contact_ids']:
                return set(group['contact_ids'])
        return {selected_id} if selected_id in dataset['contacts'] else set()
    if selected_id == UNASSIGNED_GROUP:
        return {uid for uid, c in dataset['contacts'].items() if not c['institution_keys']}
    initial = {uid for uid, c in dataset['contacts'].items() if selected_id in c['institution_keys']}
    # Include all IDs of an identity established by email/external ID or an explicit rule.
    for group in dataset.get('contact_groups', {}).values():
        if initial.intersection(group['contact_ids']):
            initial.update(group['contact_ids'])
    return initial


def refresh_catalog(dataset, api):
    result = deepcopy(dataset)
    errors = []
    try:
        companies = api.listing('companies')
        for company in companies:
            result['institution_index'].add_remote_company(company)
            result['companies'][str(company['id'])] = company
    except (ApiError, OSError, ValueError) as exc:
        errors.append({'stage': 'institutions', 'reason': str(exc) if isinstance(exc, ApiError) else 'No se pudo actualizar el catálogo'})
    try:
        for remote in api.listing('contacts'):
            uid = str(remote['id'])
            local = result['contacts'].get(uid, {})
            current = {result['institution_index'].company(cid) for cid in company_ids(remote)}
            source = 'freshdesk_contact_company' if current else local.get('mapping_source', 'unassigned')
            if not current:
                current = set(local.get('institution_keys', []))
                if current and source in ('contact_company_id', 'freshdesk_contact_company'):
                    source = 'provisional_local_association'
            result['contacts'][uid] = {
                **local, **remote, 'id': uid, 'institution_keys': sorted(current),
                'institution_names': [result['institution_index'].names[k] for k in sorted(current)],
                'mapping_source': source, 'issues': list(local.get('issues', [])),
                'institution_candidates': local.get('institution_candidates', []),
                'duplicate_contact_ids': local.get('duplicate_contact_ids', []),
            }
            result['people'][uid] = remote.get('name') or uid
    except (ApiError, OSError, ValueError) as exc:
        errors.append({'stage': 'contacts', 'reason': str(exc) if isinstance(exc, ApiError) else 'No se pudo actualizar el catálogo'})
    result['institutions'] = result['institution_index'].names
    result['contact_groups'] = contact_groups(result['contacts'], result.get('verified_contact_groups', []))
    return result, errors


def merge_live_ticket(dataset, remote):
    tid, requester = str(remote['id']), identifier(remote.get('requester_id'))
    old = dataset['tickets'].get(tid, {})
    custom = remote.get('custom_fields') or {}
    index = dataset['institution_index']
    native_key = index.company(remote.get('company_id'))
    label_key = index.label(custom.get('cf_empresa'))
    if requester and requester not in dataset['contacts']:
        dataset['contacts'][requester] = {
            'id': requester, 'name': f'Contacto {requester}', 'institution_keys': [], 'institution_names': [],
            'mapping_source': 'unassigned', 'issues': ['contacto_no_disponible_en_catalogo'],
            'institution_candidates': [], 'duplicate_contact_ids': [],
        }
    contact = dataset['contacts'].get(requester, {})
    dataset['tickets'][tid] = {
        **old, 'id': tid, 'internal_id': old.get('internal_id', ''), 'requester_id': requester,
        'requester_id_at_snapshot': old.get('requester_id'),
        'contact_name': contact.get('name') or f'Contacto {requester}', 'contact_email': contact.get('email'),
        'contact_institutions': contact.get('institution_names', []),
        'mapping_source': contact.get('mapping_source', 'unassigned'),
        'subject': remote.get('subject'), 'description_text': remote.get('description_text'),
        'description_html': remote.get('description'), 'status': remote.get('status'),
        'status_name': {2: 'Abierto', 3: 'Pendiente', 4: 'Resuelto', 5: 'Cerrado'}.get(remote.get('status'), str(remote.get('status'))),
        'created_at': remote.get('created_at'), 'updated_at': remote.get('updated_at'),
        'responder_id': identifier(remote.get('responder_id')),
        'agent_name': dataset['people'].get(identifier(remote.get('responder_id')), old.get('agent_name')),
        'product': custom.get('cf_producto', old.get('product')), 'priority': remote.get('priority'),
        'institution_key': native_key or label_key, 'institution_label': custom.get('cf_empresa'),
        'institution_name': index.names.get(native_key or label_key, ''),
        'company_id': remote.get('company_id'), 'stats': remote.get('stats') or {},
        'sla': {k: remote.get(k) for k in ['due_by', 'fr_due_by', 'nr_due_by', 'is_escalated', 'fr_escalated', 'nr_escalated', 'stats']},
        'conversations': old.get('conversations', []), 'activity_history': old.get('activity_history', []),
        'source': 'freshdesk_catalog_with_local_history' if old else 'freshdesk_catalog',
    }


def discover_tickets(dataset, api, mode, selected_id):
    errors, details = [], {}
    ids = scoped_contact_ids(dataset, mode, selected_id)
    requests = [('requester_id', uid) for uid in sorted(ids)]
    if mode == 'institution' and selected_id != UNASSIGNED_GROUP:
        requests += [('company_id', cid) for cid, key in dataset['institution_index'].by_id.items() if key == selected_id]
    for field, value in requests:
        try:
            rows = api.listing('tickets', **{field: value}, updated_since=EPOCH, include='description,stats')
            for ticket in rows:
                if str(ticket.get(field)) != str(value):
                    raise ApiError(f'Filtro {field} no respetado por la API')
                tid = str(ticket['id'])
                details[tid] = ticket
                merge_live_ticket(dataset, ticket)
        except (ApiError, OSError, ValueError) as exc:
            errors.append({'stage': 'tickets', 'filter': field, 'id': value,
                           'reason': str(exc) if isinstance(exc, ApiError) else 'No se pudo reconciliar la lista de tickets'})
    return details, errors


def _prepare_ticket(ticket, include_private):
    # Use the established local serializer, without adding unrelated contact tickets.
    notes = [n for n in ticket.get('conversations', []) if not n.get('private') or include_private]
    history = [a for a in ticket.get('activity_history', []) if include_private or
               (not a.get('activity', {}).get('note') or a['activity']['note'].get('type') in (0, 4))]
    return {**deepcopy(ticket), 'conversations': [n for n in notes if not n.get('private')],
            'private_notes': [n for n in notes if n.get('private')], 'activity_history': history,
            'conversation_text_missing': sum(not n.get('text_available') for n in notes),
            'description_available': bool(ticket.get('description_text') or ticket.get('description_html'))}


def initial_result(dataset, mode, selected_id, include_private):
    ids = scoped_contact_ids(dataset, mode, selected_id)
    # A ticket-level institution can be relevant even if the requester is a shared help desk.
    # Include ONLY those tickets; never transfer that contact's other tickets to the institution.
    selected = []
    direct_requesters = set()
    for ticket in dataset['tickets'].values():
        by_contact = ticket['requester_id'] in ids
        by_ticket = mode == 'institution' and selected_id != UNASSIGNED_GROUP and ticket['institution_key'] == selected_id
        if by_contact or by_ticket:
            item = _prepare_ticket(ticket, include_private)
            item['selection_reason'] = 'contacto_vinculado' if by_contact else 'institucion_registrada_en_ticket'
            selected.append(item)
            if not by_contact and ticket['requester_id']:
                direct_requesters.add(ticket['requester_id'])
    # Reuse the standard manifest and crosswalk structure expected by CSV/JSON downloads.
    seed = query_local(dataset, mode, selected_id, include_private=include_private)
    seed.update(tickets=selected, mapping=contact_mapping(dataset, ids | direct_requesters))
    seed['manifest']['tickets_before_filters'] = len(selected)
    seed['manifest']['tickets_exported'] = len(selected)
    seed['manifest']['private_notes_included'] = include_private
    seed['manifest']['selection_contact_ids'] = sorted(ids)
    return seed


def in_range(value, start, end):
    dt = timestamp(value)
    return dt.year > 1 and start <= dt.date().isoformat() <= end


def movement_reasons(ticket, start, end):
    reasons = []
    for key, label in [('created_at', 'Creación'), ('updated_at', 'Actualización')]:
        if in_range(ticket.get(key), start, end):
            reasons.append(label)
    if any(in_range(a.get('performed_at'), start, end) for a in ticket.get('activity_history', [])):
        reasons.append('Actividad local')
    if any(in_range(n.get('created_at'), start, end) or in_range(n.get('updated_at'), start, end)
           for n in ticket.get('conversations', []) + ticket.get('private_notes', [])):
        reasons.append('Respuesta o nota')
    if any(in_range(v, start, end) for k, v in (ticket.get('stats') or {}).items()
           if k in ('resolved_at', 'closed_at', 'first_responded_at', 'status_updated_at')):
        reasons.append('Resolución / respuesta registrada')
    return reasons


def export_history(dataset, mode, selected_id, start, end, api=None, include_private=True, progress=None):
    if start > end:
        raise ValueError('La fecha inicial debe ser anterior o igual a la final.')
    work = deepcopy(dataset)
    discovery_errors, details = [], {}
    local_ticket_ids = set(work['tickets'])
    if api:
        work, discovery_errors = refresh_catalog(work, api)
        details, list_errors = discover_tickets(work, api, mode, selected_id)
        discovery_errors.extend(list_errors)
    result = initial_result(work, mode, selected_id, include_private)
    candidate_count = len(result['tickets'])
    if api:
        result = fetch_full_content(result, api, include_private, progress, details=details)
    all_candidates = result['tickets']
    selected = []
    for t in all_candidates:
        reasons = movement_reasons(t, start, end)
        if any(in_range(value, start, end) for value in t.pop('_movement_dates', [])) and 'Respuesta o nota' not in reasons:
            reasons.append('Respuesta o nota')
        if any(in_range(a.get('performed_at'), start, end) for a in work['tickets'][t['id']].get('activity_history', [])) and 'Actividad local' not in reasons:
            reasons.append('Actividad local')
        # Do not silently discard an inaccessible ticket whose date scope cannot be verified.
        uncertain = api and not t.get('remote_content_complete') and not reasons
        if reasons or uncertain:
            t['range_match'] = reasons or ['Fecha no verificable por error de API']
            selected.append(t)
    result['tickets'] = sorted(selected, key=lambda t: int(t['id']))
    exported_ids = {t['id'] for t in selected}
    for row in result['unresolved']:
        row['included_in_export'] = row['id'] in exported_ids
    coverage = []
    grouped_ids = set(result['manifest']['selection_contact_ids']) | {t['requester_id'] for t in selected if t['requester_id']}
    for uid in sorted(grouped_ids):
        c = work['contacts'][uid]
        tids = [t['id'] for t in selected if t['requester_id'] == uid]
        coverage.append({'contact_id': uid, 'contact_name': c.get('name'), 'institution_names': c['institution_names'],
                         'mapping_source': c['mapping_source'], 'tickets_before_filters': sum(t['requester_id'] == uid for t in all_candidates),
                         'tickets_exported': len(tids), 'ticket_ids': tids})
    result['coverage'] = coverage
    result['mapping'] = contact_mapping(work, grouped_ids | {r['contact_id'] for r in result['unresolved'] if r['contact_id']})
    manifest = result['manifest']
    manifest.update(
        filters={'start': start, 'end': end, 'date_field': 'created_or_movement', 'statuses': 'Todos'},
        contacts=len(coverage), tickets_before_filters=candidate_count, tickets_exported=len(selected),
        discovery_errors=discovery_errors, history_coverage=work.get('history_coverage', {}),
        new_ticket_ids_from_api=sorted({t['id'] for t in selected} - local_ticket_ids),
        included_by_ticket_institution=[t['id'] for t in selected if t['selection_reason'] == 'institucion_registrada_en_ticket'],
        conversation_text_missing=sum(t['conversation_text_missing'] for t in selected),
        public_conversation_records=sum(len(t['conversations']) for t in selected),
        private_conversation_records=sum(len(t['private_notes']) for t in selected),
        local_activity_count=sum(len(t['activity_history']) for t in selected),
    )
    manifest['extraction_complete'] = not discovery_errors and not manifest.get('errors') and bool(api) and manifest.get('remote_content_complete', False)
    manifest['source'] = 'local_history_with_api_reconciliation' if api else 'local_files_only'
    manifest['tickets_without_description'] = [t['id'] for t in selected if not t['description_available']]
    manifest['provisional_contact_ids'] = sorted({r['contact_id'] for r in result['mapping'] if r['status'] == 'provisional'})
    manifest['limitations'] = [
        'Selección por creación, actualización, respuestas o actividades en el rango UTC; todos los estados.',
        'Para cada ticket seleccionado se conserva su conversación e histórico local disponibles completos, también fuera del rango.',
        'La API reconcilia tickets por IDs de contacto e institución; no garantiza descubrir contactos sin asociación ni tickets invisibles por permisos.',
        'No se reconstruyen cambios anteriores al inicio de recolección ni actividades de archivos ausentes.',
        'No se certifican eliminados, spam, fusionados o archivados no accesibles; adjuntos como enlaces y metadatos.',
    ]
    result['people'] = work.get('people', {})
    related_keys = {k for uid in grouped_ids for k in work['contacts'][uid]['institution_keys']}
    if mode == 'institution':
        related_keys.add(selected_id)
    result['normalization'] = [
        {'Nombre normalizado de origen': name, 'Institución agrupada': work['institution_index'].names[key], 'Clave': key}
        for name, keys in sorted(work['institution_index'].aliases.items()) for key in sorted(keys) if key in related_keys
    ]
    return result
