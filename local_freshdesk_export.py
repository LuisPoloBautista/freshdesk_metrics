"""Queries over repository snapshots only. No credentials or network requests."""
from collections import defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
from pathlib import Path
import unicodedata
import zipfile

ROOT = Path(__file__).resolve().parent
UNASSIGNED_GROUP = '__unassigned__'
MAPPING_FIELDS = ['contact_id', 'contact_name', 'email', 'institution_key', 'company_id',
                  'institution_name', 'status', 'source', 'evidence_ticket_ids',
                  'candidate_institution_keys', 'duplicate_contact_ids', 'issues']


def normalize(value):
    text = unicodedata.normalize('NFKD', str(value or '').strip().casefold())
    return ' '.join(''.join(c for c in text if not unicodedata.combining(c)).split())


def identifier(value):
    return str(value) if value not in (None, '', 0) else ''


def timestamp(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(str(value), '%d-%m-%Y %H:%M:%S %z').astimezone(timezone.utc)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)


def source_files(root=ROOT):
    root = Path(root)
    paths = set(root.glob('activities_*.json'))
    for folder, pattern in [('3748365', 'Tickets*.json'), ('actores', 'Users*.json'),
                            ('actores', 'AllAgents*.json'), ('actores', 'Companies*.json')]:
        paths.update((root / folder).glob(pattern))
    directory = root / 'institution_directory.json'
    if directory.exists():
        paths.add(directory)
    return sorted(paths)


def source_signature(root=ROOT):
    digest = hashlib.sha256()
    for path in source_files(root):
        stat = path.stat()
        digest.update(f'{path.name}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
    return digest.hexdigest()


def load_local_dataset(root=ROOT):
    root = Path(root)
    users, agents, snapshots, companies, activities = {}, {}, {}, {}, []
    links, errors, sources = [], [], []
    for path in source_files(root):
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if path.name == 'institution_directory.json':
                companies.update({identifier(c['id']): c for c in data.get('companies', [])})
                links = data.get('verified_contact_links', [])
            elif path.name.startswith(('Users', 'AllAgents')):
                for wrapper in data:
                    user = wrapper.get('user', wrapper)
                    uid = identifier(user['id'])
                    if path.name.startswith('AllAgents') or user.get('helpdesk_agent'):
                        agents[uid] = user
                    if uid not in users or timestamp(user.get('updated_at')) > timestamp(users[uid].get('updated_at')):
                        users[uid] = user
            elif path.name.startswith('Companies'):
                for wrapper in data:
                    company = wrapper.get('company', wrapper)
                    cid = identifier(company['id'])
                    companies[cid] = {**companies.get(cid, {}), **company}
            elif path.name.startswith('Tickets'):
                for wrapper in data:
                    ticket = wrapper.get('helpdesk_ticket', wrapper)
                    tid = identifier(ticket.get('display_id'))
                    if not tid:
                        raise ValueError('Ticket sin display_id; no se confunde con el ID interno')
                    if tid not in snapshots or timestamp(ticket.get('updated_at')) > timestamp(snapshots[tid].get('updated_at')):
                        snapshots[tid] = ticket
            else:
                for activity in data.get('activities_data', []):
                    activities.append({**activity, '_source_file': path.name})
            sources.append(str(path.relative_to(root)))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            errors.append(f'{path.name}: {type(exc).__name__}; revisar archivo')

    institutions = {f'company:{cid}': c['name'] for cid, c in companies.items()}
    aliases = defaultdict(set)
    for cid, c in companies.items():
        for name in [c['name'], *(c.get('aliases') or [])]:
            aliases[normalize(name)].add(f'company:{cid}')

    def institution_for_label(label):
        if normalize(label) in ('', 'n/a', 'na', 'n.a.', 'none', 'null', '-', 'sin institucion', 'sin empresa', 'no aplica'):
            return ''
        candidates = aliases[normalize(label)]
        if len(candidates) == 1:
            return next(iter(candidates))
        # Ambiguous aliases remain distinct; no fuzzy matching of institutions.
        key = 'name:' + normalize(label)
        institutions.setdefault(key, str(label).strip())
        return key

    tickets = {}
    for tid, original in snapshots.items():
        custom = original.get('custom_field') or original.get('custom_fields') or {}
        ticket = {
            'id': tid, 'internal_id': identifier(original.get('id')),
            'requester_id': identifier(original.get('requester_id')),
            'requester_name': original.get('requester_name'),
            'subject': original.get('subject'), 'description_text': original.get('description'),
            'description_html': original.get('description_html'),
            'status': original.get('status'), 'status_name': original.get('status_name'),
            'created_at': original.get('created_at'), 'updated_at': original.get('updated_at'),
            'responder_id': identifier(original.get('responder_id')), 'agent_name': original.get('responder_name'),
            'product': custom.get('cf_producto_3748365', custom.get('cf_producto')),
            'institution_label': custom.get('cf_empresa_3748365', custom.get('cf_empresa')),
            'priority': original.get('priority'), 'priority_name': original.get('priority_name'),
            'sla': {key: value for key, value in original.items() if key in (
                'due_by', 'frDueBy', 'nr_due_by', 'isescalated', 'fr_escalated', 'nr_escalated',
                'sla_state', 'sl_sla_policy_id', 'stats')},
            'conversations': [], 'activity_history': [], 'source': 'ticket_snapshot',
            'snapshot_updated_at': original.get('updated_at'),
        }
        for note in original.get('conversations', original.get('notes', [])) or []:
            note = note.get('note', note)
            private = note.get('private', note.get('type') not in (0, 4))
            ticket['conversations'].append({**note, 'private': private,
                                            'text_available': bool(note.get('body_text') or note.get('body'))})
        tickets[tid] = ticket

    seen_activities = set()
    for event in sorted(activities, key=lambda e: timestamp(e.get('performed_at'))):
        tid = identifier(event.get('ticket_id'))
        if not tid:
            continue
        original_event = {k: v for k, v in event.items() if k != '_source_file'}
        signature = json.dumps(original_event, sort_keys=True, ensure_ascii=False)
        if signature in seen_activities:
            continue
        seen_activities.add(signature)
        ticket = tickets.setdefault(tid, {'id': tid, 'internal_id': '', 'requester_id': '',
                                         'conversations': [], 'activity_history': [], 'sla': {},
                                         'source': 'activity_only'})
        ticket['activity_history'].append(event)
        act = event.get('activity') or {}
        when = event.get('performed_at')
        if not ticket.get('created_at') and act.get('new_ticket'):
            ticket['created_at'] = when
        # Snapshot wins over older events; explicit null updates still clear fields.
        if timestamp(when) > timestamp(ticket.get('snapshot_updated_at')):
            for source, target in [('requester_id', 'requester_id'), ('agent_id', 'responder_id'),
                                   ('subject', 'subject'), ('description', 'description_text'),
                                   ('Producto', 'product'), ('cf_producto_3748365', 'product'),
                                   ('Empresa', 'institution_label'), ('cf_empresa_3748365', 'institution_label')]:
                if source in act:
                    ticket[target] = identifier(act[source]) if target.endswith('_id') else act[source]
            if 'status' in act:
                status = act['status']
                ticket['status'] = {'Open': 2, 'Pending': 3, 'Resolved': 4, 'Closed': 5}.get(status, status)
                ticket['status_name'] = str(status)
            for field in ['due_by', 'sla_policy', 'Primer tiempo de respuesta', 'Tiempos SLA']:
                if field in act:
                    ticket['sla'][field] = act[field]
            ticket['updated_at'] = when
        if 'note' in act:
            note = act['note']
            existing = {identifier(c.get('id')) for c in ticket['conversations']}
            if identifier(note.get('id')) not in existing:
                ticket['conversations'].append({
                    'id': note.get('id'), 'type': note.get('type'), 'private': note.get('type') not in (0, 4),
                    'created_at': when, 'user_id': event.get('performer_id'),
                    'body_text': note.get('body_text'), 'body': note.get('body'),
                    'text_available': bool(note.get('body_text') or note.get('body')),
                })

    contacts = {uid: {**u, 'id': uid} for uid, u in users.items() if not u.get('helpdesk_agent')}
    for t in tickets.values():
        rid = t['requester_id']
        if rid and rid not in contacts:
            contacts[rid] = {**users.get(rid, {}), 'id': rid, 'name': t.get('requester_name') or f'Contacto {rid}'}
        t['institution_key'] = institution_for_label(t.get('institution_label'))
        t['agent_name'] = agents.get(t.get('responder_id'), {}).get('name') or t.get('agent_name')
    links_by_id = {identifier(link['contact_id']): link for link in links}
    for uid, contact in contacts.items():
        ids = {identifier(contact['company_id'])} if contact.get('company_id') else set()
        for c in contact.get('other_companies') or []:
            ids.add(identifier(c.get('company_id') if isinstance(c, dict) else c))
        keys = {f'company:{cid}' for cid in ids if cid}
        source = 'contact_company_id' if keys else 'unassigned'
        issues = []
        link = links_by_id.get(uid)
        if link:
            linked_key = f"company:{link['company_id']}"
            if keys and linked_key not in keys:
                issues.append('conflicto_con_cruce_verificado')
            else:
                keys.add(linked_key)
                source = 'verified_local_mapping'
        evidence = {t['institution_key'] for t in tickets.values() if t['requester_id'] == uid and t['institution_key']}
        if not keys and len(evidence) == 1:
            keys = evidence
            source = 'provisional_from_ticket_institution'
        elif not keys and len(evidence) > 1:
            issues.append('institucion_ambigua')
        if not keys:
            issues.append('sin_institucion_asignada')
        if len(keys) > 1:
            issues.append('multiples_instituciones')
        if evidence - keys and keys:
            issues.append('institucion_del_ticket_difiere_del_contacto')
        for key in keys:
            institutions.setdefault(key, 'Institución ID ' + key.removeprefix('company:'))
        contact.update(institution_keys=sorted(keys), mapping_source=source, issues=issues,
                       institution_candidates=sorted(evidence),
                       institution_names=[institutions[k] for k in sorted(keys)])

    duplicates = []
    for field in ['email', 'name', 'unique_external_id']:
        index = defaultdict(set)
        for uid, contact in contacts.items():
            values = [contact.get(field)] + ((contact.get('other_emails') or []) if field == 'email' else [])
            for value in values:
                if normalize(value):
                    index[normalize(value)].add(uid)
        duplicates.extend({'field': field, 'value': value, 'contact_ids': sorted(ids)}
                          for value, ids in index.items() if len(ids) > 1)
    for uid, contact in contacts.items():
        contact['duplicate_contact_ids'] = sorted({other for group in duplicates if uid in group['contact_ids']
                                                   for other in group['contact_ids'] if other != uid})
        if contact['duplicate_contact_ids']:
            contact['issues'].append('posible_duplicado')
    for t in tickets.values():
        contact = contacts.get(t['requester_id'], {})
        for field in ['created_at', 'updated_at']:
            if t.get(field):
                parsed = timestamp(t[field])
                t[field] = parsed.isoformat() if parsed.year > 1 else None
        t['contact_name'] = contact.get('name') or t.get('requester_name') or 'Sin contacto identificado'
        t['contact_email'] = contact.get('email')
        t['contact_institutions'] = contact.get('institution_names', [])
        t['institution_name'] = institutions.get(t['institution_key'], '')
        t['mapping_source'] = contact.get('mapping_source', 'unassigned')
    return {'tickets': tickets, 'contacts': contacts, 'institutions': institutions,
            'duplicates': duplicates, 'errors': errors, 'sources': sources,
            'signature': source_signature(root)}


def contact_mapping(dataset, contact_ids=None):
    """A reproducible crosswalk; local institution keys are never Freshdesk IDs."""
    rows = []
    for uid, contact in sorted(dataset['contacts'].items()):
        if contact_ids is not None and uid not in contact_ids:
            continue
        for key in contact['institution_keys'] or ['']:
            source = contact['mapping_source']
            status = ('provisional' if source == 'provisional_from_ticket_institution' else
                      'unassigned' if not key else 'verified' if source == 'verified_local_mapping' else 'recorded')
            rows.append({
                'contact_id': uid, 'contact_name': contact.get('name'), 'email': contact.get('email'),
                'institution_key': key, 'company_id': key.split(':', 1)[1] if key.startswith('company:') else '',
                'institution_name': dataset['institutions'].get(key, ''), 'status': status, 'source': source,
                'evidence_ticket_ids': sorted(t['id'] for t in dataset['tickets'].values()
                                              if t['requester_id'] == uid and key and t['institution_key'] == key),
                'candidate_institution_keys': contact.get('institution_candidates', []),
                'duplicate_contact_ids': contact.get('duplicate_contact_ids', []),
                'issues': contact['issues'],
            })
    return rows


def institution_group(dataset, mode, selected_id):
    contacts = dataset['contacts']
    if mode == 'contact':
        assigned = {selected_id} if selected_id in contacts else set()
    elif selected_id == UNASSIGNED_GROUP:
        assigned = {uid for uid, c in contacts.items() if not c['institution_keys']}
    else:
        assigned = {uid for uid, c in contacts.items() if selected_id in c['institution_keys']}
    pending = {uid for uid, c in contacts.items() if mode == 'institution' and uid not in assigned
               and selected_id in c.get('institution_candidates', [])}
    ids = assigned | pending
    return {'assigned_ids': assigned, 'pending_ids': pending, 'mapping': contact_mapping(dataset, ids),
            'duplicates': [g for g in dataset['duplicates'] if set(g['contact_ids']) & ids]}


def csv_bytes(rows, fields):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        safe = {}
        for field in fields:
            value = row.get(field)
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)
            if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                value = "'" + value
            safe[field] = value
        writer.writerow(safe)
    return stream.getvalue().encode('utf-8-sig')


def query_local(dataset, mode, selected_id, start=None, end=None, date_field='created_at',
                products=(), statuses=(), include_private=False):
    if start and end and start > end:
        raise ValueError('La fecha inicial debe ser anterior o igual a la final.')
    contacts = dataset['contacts']
    group = institution_group(dataset, mode, selected_id)
    contact_ids = group['assigned_ids']
    candidates = [t for t in dataset['tickets'].values() if t['requester_id'] in contact_ids]
    unresolved = [{'id': t['id'], 'contact_id': t['requester_id'], 'contact_name': t['contact_name'],
                   'subject': t.get('subject'), 'institution_label': t.get('institution_label'),
                   'reason': 'Institución del ticket sin asociación confirmada del contacto'}
                  for t in dataset['tickets'].values() if mode == 'institution'
                  and t['institution_key'] == selected_id and t['requester_id'] not in contact_ids]
    chosen = []
    for t in candidates:
        day = timestamp(t.get(date_field)).date().isoformat() if t.get(date_field) else ''
        if (start or end) and not day:
            continue
        if start and day < start or end and day > end:
            continue
        if products and t.get('product') not in products:
            continue
        if statuses and t.get('status') not in statuses:
            continue
        notes = [n for n in t['conversations'] if not n['private'] or include_private]
        history = [a for a in t['activity_history'] if include_private or
                   (not a.get('activity', {}).get('note') or a['activity']['note'].get('type') in (0, 4))]
        chosen.append({**t, 'conversations': [n for n in notes if not n['private']],
                       'private_notes': [n for n in notes if n['private']], 'activity_history': history,
                       'conversation_text_missing': sum(not n['text_available'] for n in notes),
                       'description_available': bool(t.get('description_text') or t.get('description_html'))})
    chosen.sort(key=lambda t: int(t['id']))
    coverage = []
    for uid in sorted(contact_ids | {t['requester_id'] for t in chosen if t['requester_id']}):
        ids = [t['id'] for t in chosen if t['requester_id'] == uid]
        coverage.append({'contact_id': uid, 'contact_name': contacts[uid].get('name'),
                         'institution_names': contacts[uid]['institution_names'],
                         'mapping_source': contacts[uid]['mapping_source'],
                         'tickets_before_filters': sum(t['requester_id'] == uid for t in candidates),
                         'tickets_exported': len(ids), 'ticket_ids': ids})
    manifest = {
        'source': 'local_files_only', 'source_signature': dataset['signature'],
        'selection': {'mode': mode, 'id': selected_id},
        'filters': {'start': start, 'end': end, 'date_field': date_field, 'products': list(products), 'statuses': list(statuses)},
        'contacts': len(coverage), 'tickets_before_filters': len(candidates), 'tickets_exported': len(chosen),
        'unresolved_institution_ticket_ids_before_filters': [t['id'] for t in unresolved],
        'provisional_contact_ids': [uid for uid in sorted(contact_ids)
                                    if contacts[uid]['mapping_source'] == 'provisional_from_ticket_institution'],
        'conversation_text_missing': sum(t['conversation_text_missing'] for t in chosen),
        'tickets_without_description': [t['id'] for t in chosen if not t['description_available']],
        'tickets_without_requester': [t['id'] for t in chosen if not t['requester_id']],
        'public_conversation_records': sum(len(t['conversations']) for t in chosen),
        'private_notes_included': include_private, 'errors': dataset['errors'],
        'limitations': [
            'Solo archivos locales disponibles: no consulta Freshdesk ni certifica el histórico completo.',
            'Las actividades con ID de nota sin cuerpo no contienen el texto de esa respuesta.',
            'Las asociaciones deducidas del campo institución de tickets son provisionales y se señalan.',
            'No se unen contactos por nombre; cada contacto mantiene su ID de origen.',
        ],
    }
    manifest['unassigned_contact_ids_in_group'] = sorted({r['contact_id'] for r in group['mapping'] if r['status'] == 'unassigned'})
    manifest['possible_duplicate_groups'] = group['duplicates']
    return {'tickets': chosen, 'coverage': coverage, 'manifest': manifest, 'unresolved': unresolved,
            'mapping': group['mapping']}


def build_downloads(result):
    tickets = result['tickets']
    public = [{**n, 'ticket_id': t['id'], 'contact_name': t['contact_name']} for t in tickets for n in t['conversations']]
    private = [{**n, 'ticket_id': t['id']} for t in tickets for n in t['private_notes']]
    def json_bytes(value):
        return json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
    fields = ['id', 'internal_id', 'subject', 'description_text', 'description_html', 'requester_id',
              'contact_name', 'contact_email', 'contact_institutions', 'institution_name', 'mapping_source',
              'status', 'status_name', 'created_at', 'updated_at', 'responder_id', 'agent_name', 'product',
              'priority', 'sla', 'description_available', 'conversation_text_missing', 'conversations']
    # Private text remains in separate files, even when explicitly included.
    exported = [{k: v for k, v in t.items() if k not in ('private_notes', 'activity_history')} for t in tickets]
    files = {'tickets.csv': csv_bytes(tickets, fields), 'tickets.json': json_bytes(exported),
             'contact_institution_mapping.csv': csv_bytes(result['mapping'], MAPPING_FIELDS),
             'contacts_unassigned.csv': csv_bytes([r for r in result['mapping'] if r['status'] == 'unassigned'], MAPPING_FIELDS),
             'conversations_public.csv': csv_bytes(public, ['ticket_id', 'id', 'contact_name', 'created_at', 'user_id',
                                                          'type', 'body_text', 'body', 'text_available']),
             'coverage_by_contact.csv': csv_bytes(result['coverage'], ['contact_id', 'contact_name', 'institution_names',
                                                                      'mapping_source', 'tickets_before_filters', 'tickets_exported', 'ticket_ids']),
             'activity_history_available.json': json_bytes([{'ticket_id': t['id'], 'activities': t['activity_history']} for t in tickets]),
             'institution_unresolved_tickets.csv': csv_bytes(result['unresolved'],
                 ['id', 'contact_id', 'contact_name', 'subject', 'institution_label', 'reason']),
             'manifest.json': json_bytes(result['manifest'])}
    if result['manifest']['private_notes_included']:
        files['notes_private.json'] = json_bytes(private)
        files['notes_private.csv'] = csv_bytes(private, ['ticket_id', 'id', 'created_at', 'user_id', 'body_text', 'body', 'text_available'])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return files, buffer.getvalue()
