"""Private, repeatable contact/company audit and full-text Freshdesk export.

Only ``apply-mapping`` writes to Freshdesk. Exports never commit or upload data.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from .fetch_ticket_activities import load_env_file, DEFAULT_ENV_FILE, DEFAULT_DOMAIN, auth_header
except ImportError:
    from fetch_ticket_activities import load_env_file, DEFAULT_ENV_FILE, DEFAULT_DOMAIN, auth_header

ROOT = Path(__file__).resolve().parents[1]
EPOCH = '1970-01-01T00:00:00Z'


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def write_csv(path, rows, fields):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            # Preserve full text, but prevent formula evaluation when opening in Excel.
            safe = {}
            for key in fields:
                value = row.get(key)
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                    value = "'" + value
                safe[key] = value
            writer.writerow(safe)


class ApiError(RuntimeError):
    pass


class Client:
    def __init__(self, domain, key):
        if not re.fullmatch(r'[A-Za-z0-9-]+\.freshdesk\.com', domain):
            raise ValueError('FRESHDESK_DOMAIN debe ser un dominio *.freshdesk.com sin ruta.')
        self.base = 'https://' + domain + '/api/v2/'
        self.auth = auth_header(key)
        self.requests = 0
        self.pages = []

    def request(self, path, params=None, payload=None):
        url = self.base + path + ('?' + urlencode(params) if params else '')
        data = json.dumps(payload).encode() if payload is not None else None
        for attempt in range(5):
            request = Request(url, data=data, method='PUT' if payload is not None else 'GET',
                              headers={'Authorization': self.auth, 'Content-Type': 'application/json'})
            try:
                self.requests += 1
                with urlopen(request, timeout=60) as response:
                    result = json.load(response)
                time.sleep(0.15)
                return result
            except HTTPError as exc:
                if exc.code == 429 or (exc.code >= 500 and payload is None):
                    wait = min(60, max(1, int(exc.headers.get('Retry-After', 2 ** attempt))))
                    print(f'HTTP {exc.code}; reintento en {wait}s', flush=True)
                    time.sleep(wait)
                    continue
                # Do not expose API keys, signed URLs or response bodies in logs.
                raise ApiError(f'{path}: HTTP {exc.code}') from None
        raise ApiError(f'{path}: agotados los reintentos')

    def listing(self, path, **params):
        rows, seen = [], set()
        # Request an empty terminal page: some endpoints ignore per_page.
        limit = 300 if path == 'tickets' else 10000
        for page in range(1, limit + 1):
            batch = self.request(path, {**params, 'per_page': 100, 'page': page})
            if not isinstance(batch, list):
                raise ApiError(f'{path}: respuesta no es una lista')
            self.pages.append({'path': path, 'params': params, 'page': page, 'rows': len(batch)})
            if not batch:
                return rows
            for item in batch:
                if item['id'] in seen:
                    raise ApiError(f'{path}: ID repetido entre paginas; repetir con datos estables')
                seen.add(item['id'])
                rows.append(item)
        raise ApiError(f'{path}: limite de paginacion alcanzado; no se certifica integridad')


def company_ids(contact):
    ids = {contact['company_id']} if contact.get('company_id') else set()
    for other in contact.get('other_companies') or []:
        value = other.get('company_id') if isinstance(other, dict) else other
        if value:
            ids.add(int(value))
    return ids


def audit_contacts(contacts, companies):
    company_map = {c['id']: c for c in companies}
    indices = {key: defaultdict(set) for key in ('email', 'unique_external_id', 'name')}
    for c in contacts:
        for key in indices:
            values = [c.get(key)]
            if key == 'email':
                values += c.get('other_emails') or []
            for value in values:
                if value and str(value).strip():
                    indices[key][str(value).strip().casefold()].add(c['id'])
    duplicates = [dict(field=key, value=value, contact_ids=sorted(ids),
                       classification='posible_duplicado_revisar')
                  for key, index in indices.items() for value, ids in index.items() if len(ids) > 1]
    rows = []
    for c in contacts:
        ids = company_ids(c)
        flags = []
        if not ids:
            flags.append('sin_institucion')
        if len(ids) > 1:
            flags.append('multiples_instituciones')
        if ids - company_map.keys():
            flags.append('institucion_no_disponible')
        if any(c['id'] in d['contact_ids'] for d in duplicates):
            flags.append('posible_duplicado')
        rows.append({'contact_id': c['id'], 'name': c.get('name'), 'email': c.get('email'),
                     'unique_external_id': c.get('unique_external_id'), 'company_ids': sorted(ids),
                     'company_names': [company_map.get(i, {}).get('name', '') for i in sorted(ids)],
                     'issues': flags})
    return rows, duplicates


def read_mapping(path, contacts, companies):
    if not path:
        return []
    contact_map = {c['id']: c for c in contacts}
    company_set = {c['id'] for c in companies}
    result, seen = [], set()
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            cid, coid = int(row['contact_id']), int(row['company_id'])
            if cid in seen or cid not in contact_map or coid not in company_set:
                raise ValueError('Cruce contiene ID repetido o inexistente')
            if row.get('status') not in ('verified', 'provisional') or not row.get('evidence', '').strip():
                raise ValueError('Cruce requiere status verified/provisional y evidence')
            existing = company_ids(contact_map[cid])
            if existing and coid not in existing:
                raise ValueError(f'Cruce contradictorio para contacto {cid}; resolver manualmente')
            seen.add(cid)
            result.append({**row, 'contact_id': cid, 'company_id': coid})
    return result


def select_contacts(contacts, mappings, contact_id=None, company_id=None):
    selected = {}
    for c in contacts:
        if c['id'] == contact_id or (company_id and company_id in company_ids(c)):
            selected[c['id']] = {**c, '_mapping_source': 'freshdesk'}
    for m in mappings:
        if m['company_id'] == company_id:
            c = next(c for c in contacts if c['id'] == m['contact_id'])
            if c['id'] not in selected:
                selected[c['id']] = {**c, '_mapping_source': m['status'], '_mapping_evidence': m['evidence']}
    return selected


def matches(ticket, args):
    day = (ticket.get(args.date_field) or '')[:10]
    if (args.start_date or args.end_date) and not day:
        return False
    if args.start_date and day < args.start_date:
        return False
    if args.end_date and day > args.end_date:
        return False
    if args.status and ticket.get('status') not in args.status:
        return False
    product = (ticket.get('custom_fields') or {}).get(args.product_field)
    if args.product and args.product.casefold() != str(product or '').casefold():
        return False
    if args.product_id and ticket.get('product_id') != args.product_id:
        return False
    return True


def local_ticket_ids(contact_ids):
    ids = set()
    for path in (ROOT / '3748365').glob('Tickets*.json'):
        for wrapper in json.loads(path.read_text(encoding='utf-8')):
            ticket = wrapper.get('helpdesk_ticket', {})
            if ticket.get('requester_id') in contact_ids and ticket.get('display_id'):
                ids.add(int(ticket['display_id']))
    return ids


def export_data(api, selected, args, out):
    tickets, coverage, errors = {}, [], []
    for cid in sorted(selected):
        try:
            found = api.listing('tickets', requester_id=cid, updated_since=EPOCH, include='stats,description')
            if any(t['requester_id'] != cid for t in found):
                raise ApiError(f'Filtro requester_id no respetado: {cid}')
            tickets.update({t['id']: t for t in found})
            coverage.append({'contact_id': cid, 'source': selected[cid]['_mapping_source'],
                             'ticket_ids': sorted(t['id'] for t in found), 'listed_count': len(found)})
        except ApiError as exc:
            errors.append(str(exc))
    company_tickets = []
    if args.company_id:
        try:
            company_tickets = api.listing('tickets', company_id=args.company_id,
                                          updated_since=EPOCH, include='stats,description')
        except ApiError as exc:
            errors.append(str(exc))
    company_outliers = [t for t in company_tickets if t['requester_id'] not in selected]
    company_missing = [t['id'] for t in company_tickets
                       if t['requester_id'] in selected and t['id'] not in tickets]
    if company_missing:
        errors.append('Tickets por institucion ausentes en consulta por contacto: ' + str(company_missing))
    for t in company_tickets:
        if t['requester_id'] in selected:
            tickets.setdefault(t['id'], t)
    # An independent local account snapshot can reveal older/archived tickets.
    snapshot_ids = local_ticket_ids(set(selected))
    recovered = []
    for tid in sorted(snapshot_ids - tickets.keys()):
        try:
            t = api.request(f'tickets/{tid}', {'include': 'stats'})
            if t['requester_id'] not in selected:
                errors.append(f'Ticket {tid}: solicitante cambio respecto al respaldo')
                continue
            tickets[tid] = t
            recovered.append(tid)
        except ApiError as exc:
            errors.append(str(exc))
    before_filters = set(tickets)
    tickets = {tid: t for tid, t in tickets.items() if matches(t, args)}
    public, private, missing_conversations = [], [], []
    for number, (tid, t) in enumerate(sorted(tickets.items()), 1):
        try:
            conversations = api.listing(f'tickets/{tid}/conversations')
            t['conversations'] = [c for c in conversations if not c.get('private')]
            t['private_conversations_count'] = sum(bool(c.get('private')) for c in conversations)
            t['conversations_complete'] = True
            for c in conversations:
                row = {**c, 'ticket_id': tid}
                if c.get('private'):
                    if args.include_private_notes:
                        private.append(row)
                else:
                    public.append(row)
        except ApiError as exc:
            errors.append(str(exc))
            missing_conversations.append(tid)
            t['conversations'] = []
            t['conversations_complete'] = False
        if number % 10 == 0:
            print(f'Conversaciones: {number}/{len(tickets)} tickets', flush=True)
    activity_rows = []
    for path in sorted(ROOT.glob('activities_*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        for activity in data.get('activities_data', []):
            # Keep original events and provenance; no fabricated events for missing days.
            raw_id = str(activity.get('ticket_id', ''))
            tid = int(raw_id) if raw_id.isdigit() else None
            if tid in tickets:
                activity_rows.append({'source_file': path.name, 'activity': activity})
    try:
        agents = api.listing('agents')
    except ApiError as exc:
        agents = []
        errors.append('Catalogo agentes: ' + str(exc))
    agent_map = {a['id']: (a.get('contact') or {}).get('name') for a in agents}
    rows = []
    for tid, t in sorted(tickets.items()):
        contact = selected[t['requester_id']]
        rows.append({
            'id': tid, 'requester_id': t['requester_id'], 'contact_name': contact.get('name'),
            'contact_email': contact.get('email'), 'contact_company_ids': sorted(company_ids(contact)),
            'export_company_id': args.company_id, 'ticket_company_id': t.get('company_id'),
            'mapping_source': contact['_mapping_source'], 'subject': t.get('subject'),
            'description_text': t.get('description_text'), 'description_html': t.get('description'),
            'conversations': t['conversations'], 'conversations_complete': t['conversations_complete'],
            'private_conversations_count': t.get('private_conversations_count'),
            'status': t.get('status'), 'priority': t.get('priority'),
            'created_at': t.get('created_at'), 'updated_at': t.get('updated_at'),
            'responder_id': t.get('responder_id'), 'agent_name': agent_map.get(t.get('responder_id')),
            'product_id': t.get('product_id'), 'product': (t.get('custom_fields') or {}).get(args.product_field),
            'due_by': t.get('due_by'), 'fr_due_by': t.get('fr_due_by'), 'nr_due_by': t.get('nr_due_by'),
            'is_escalated': t.get('is_escalated'), 'fr_escalated': t.get('fr_escalated'),
            'nr_escalated': t.get('nr_escalated'), 'stats': t.get('stats'),
            'custom_fields': t.get('custom_fields'),
        })
    write_json(out / 'tickets.json', list(tickets.values()))
    ticket_fields = list(rows[0]) if rows else ['id', 'requester_id', 'subject', 'description_text', 'conversations']
    write_csv(out / 'tickets.csv', rows, ticket_fields)
    conv_fields = ['ticket_id', 'id', 'user_id', 'private', 'incoming', 'source', 'created_at', 'updated_at',
                   'body_text', 'body', 'from_email', 'to_emails', 'cc_emails', 'attachments']
    write_csv(out / 'conversations_public.csv', public, conv_fields)
    if args.include_private_notes:
        write_json(out / 'notes_private.json', private)
        write_csv(out / 'notes_private.csv', private, conv_fields)
    write_json(out / 'activity_history_available.json', activity_rows)
    write_json(out / 'selected_contacts.json', list(selected.values()))
    write_json(out / 'company_tickets_unmapped.json', company_outliers)
    for c in coverage:
        c['exported_count'] = sum(t['requester_id'] == c['contact_id'] for t in tickets.values())
    write_csv(out / 'coverage_by_contact.csv', coverage,
              ['contact_id', 'source', 'listed_count', 'exported_count', 'ticket_ids'])
    manifest = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'company_id': args.company_id, 'contact_id': args.contact_id,
        'filters': {k: getattr(args, k) for k in ['start_date', 'end_date', 'date_field', 'status', 'product', 'product_id', 'product_field']},
        'contact_count': len(selected), 'tickets_before_filters': len(before_filters), 'tickets_exported': len(tickets),
        'company_query_count': len(company_tickets), 'company_query_unmapped_ids': [t['id'] for t in company_outliers],
        'requester_ticket_ids_absent_from_company_query': sorted(before_filters - {t['id'] for t in company_tickets}) if args.company_id else [],
        'snapshot_ticket_count': len(snapshot_ids), 'snapshot_recovered_ids': recovered,
        'public_conversations': len(public), 'private_notes_exported': len(private),
        'private_notes_omitted': sum(t.get('private_conversations_count', 0) for t in tickets.values()) - len(private),
        'missing_conversation_ticket_ids': missing_conversations, 'activity_events_available': len(activity_rows),
        'errors': errors, 'queries_completed': not errors,
        'limitations': [
            'Cobertura de tickets accesibles como solicitante; ser CC/participante no equivale a requester_id.',
            'Asociaciones actuales; no reconstruye pertenencia historica de cada contacto.',
            'API no garantiza recuperar eliminados, spam, fusionados o todos los archivados; respaldo local es parcial.',
            'Historial de cambios limitado a archivos activities locales disponibles; no se certifica completo.',
            'Adjuntos conservados como metadatos/URL; no se descargan binarios.',
            'CSV conserva multilinea; celdas con prefijo de formula llevan apostrofo. JSON conserva valores originales.',
            'Resultados sujetos a permisos de la API; validar alcance de tickets con administrador.',
        ],
    }
    write_json(out / 'api_pages.json', api.pages)
    manifest['sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file() and p.name != 'manifest.json'}
    write_json(out / 'manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['audit', 'export', 'apply-mapping'])
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument('--company-id', type=int)
    selector.add_argument('--contact-id', type=int)
    parser.add_argument('--mapping', type=Path, help='CSV contact_id,company_id,status,evidence')
    parser.add_argument('--output', type=Path, required=True, help='Carpeta nueva, privada, dentro de exports/')
    parser.add_argument('--start-date')
    parser.add_argument('--end-date')
    parser.add_argument('--date-field', choices=['created_at', 'updated_at'], default='created_at')
    parser.add_argument('--status', type=int, action='append')
    parser.add_argument('--product')
    parser.add_argument('--product-id', type=int)
    parser.add_argument('--product-field', default='cf_producto')
    parser.add_argument('--include-private-notes', action='store_true')
    args = parser.parse_args()
    if args.command == 'export' and not (args.company_id or args.contact_id):
        parser.error('export requiere --company-id o --contact-id')
    for value in [args.start_date, args.end_date]:
        if value:
            datetime.strptime(value, '%Y-%m-%d')
    if args.start_date and args.end_date and args.start_date > args.end_date:
        parser.error('rango de fechas invertido')
    out = args.output.resolve()
    if not out.is_relative_to(ROOT / 'exports'):
        parser.error('Guarda los resultados en exports/ (excluida de Git).')
    if out.exists() and any(out.iterdir()):
        parser.error('La carpeta de salida debe ser nueva o vacia para no mezclar ejecuciones.')
    load_env_file(DEFAULT_ENV_FILE)
    key = os.getenv('FRESHDESK_API_KEY')
    if not key:
        parser.error('Configura FRESHDESK_API_KEY en .env')
    api = Client(os.getenv('FRESHDESK_DOMAIN', DEFAULT_DOMAIN), key)
    out.mkdir(parents=True, exist_ok=True)
    contacts, companies = api.listing('contacts'), api.listing('companies')
    rows, duplicates = audit_contacts(contacts, companies)
    write_json(out / 'contacts.json', contacts)
    write_json(out / 'companies.json', companies)
    write_csv(out / 'contact_institution_audit.csv', rows,
              ['contact_id', 'name', 'email', 'unique_external_id', 'company_ids', 'company_names', 'issues'])
    write_csv(out / 'contacts_unassigned.csv', [r for r in rows if not r['company_ids']],
              ['contact_id', 'name', 'email', 'issues'])
    write_csv(out / 'possible_duplicates.csv', duplicates, ['field', 'value', 'contact_ids', 'classification'])
    mappings = read_mapping(args.mapping, contacts, companies)
    write_json(out / 'mapping_used.json', mappings)
    if args.command == 'apply-mapping':
        if not mappings or any(m['status'] != 'verified' for m in mappings):
            raise ValueError('apply-mapping requiere cruce verified con evidencia para cada fila')
        # Preflight all rows before the first write; never overwrite another association.
        planned = []
        for m in mappings:
            current = api.request(f"contacts/{m['contact_id']}")
            ids = company_ids(current)
            if ids and m['company_id'] not in ids:
                raise ValueError(f"Contacto {m['contact_id']} tiene otra institucion; no se modifica")
            if m['company_id'] not in ids:
                planned.append({'mapping': m, 'before': current})
        write_json(out / 'mapping_changes_planned.json', planned)
        applied = []
        for p in planned:
            m = p['mapping']
            # Re-read immediately before mutation to detect concurrent edits.
            current = api.request(f"contacts/{m['contact_id']}")
            if company_ids(current):
                raise ValueError('Asociacion cambio durante la ejecucion; revisar registro')
            api.request(f"contacts/{m['contact_id']}", payload={'company_id': m['company_id']})
            after = api.request(f"contacts/{m['contact_id']}")
            applied.append({**p, 'after': after, 'verified': m['company_id'] in company_ids(after)})
            write_json(out / 'mapping_changes_applied.json', applied)
            if not applied[-1]['verified']:
                raise ApiError('No se pudo verificar la asociacion escrita')
        print(f'Asociaciones actualizadas y verificadas: {len(applied)}')
    elif args.command == 'export':
        selected = select_contacts(contacts, mappings, args.contact_id, args.company_id)
        if not selected:
            raise ValueError('No hay contactos asociados; revisar IDs y cruce provisional')
        manifest = export_data(api, selected, args, out)
        print(json.dumps({k: manifest[k] for k in ['contact_count', 'tickets_exported', 'public_conversations', 'queries_completed', 'errors']}))
        return 0 if manifest['queries_completed'] else 2
    else:
        print(json.dumps({'contacts': len(contacts), 'unassigned': sum(not company_ids(c) for c in contacts),
                          'possible_duplicate_groups': len(duplicates)}))
    write_json(out / 'api_pages.json', api.pages)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ApiError, ValueError, OSError) as exc:
        print(f'ERROR: {exc}')
        raise SystemExit(1)
