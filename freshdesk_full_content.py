"""Retrieve full content for an explicitly selected set of local ticket numbers."""
from copy import deepcopy
from datetime import datetime, timezone

from scripts.export_freshdesk import ApiError


def fetch_full_content(result, api, include_private=False, progress=None, details=None):
    output = deepcopy(result)
    errors = []
    for number, ticket in enumerate(output['tickets'], 1):
        tid = str(ticket['id'])
        ticket['content_source'] = 'local_fallback'
        ticket['remote_content_complete'] = False
        try:
            remote = (details or {}).get(tid)
            if remote is None:
                remote = api.request(f'tickets/{tid}', {'include': 'stats'})
            if str(remote.get('id')) != tid or str(remote.get('requester_id') or '') != ticket['requester_id']:
                raise ApiError('El ID o solicitante cambió; revisar la asociación antes de exportar')
            if 'description' not in remote and 'description_text' not in remote:
                raise ApiError('La API no devolvió los campos de descripción')
            ticket.update(description_text=remote.get('description_text'), description_html=remote.get('description'),
                          subject=remote.get('subject'), content_updated_at=remote.get('updated_at'),
                          content_source='freshdesk', description_available=bool(remote.get('description_text') or remote.get('description')))
            for field in ('created_at', 'updated_at', 'status', 'stats', 'responder_id'):
                if field in remote:
                    ticket[field] = remote[field]
            if 'status' in remote:
                ticket['status_name'] = {2: 'Abierto', 3: 'Pendiente', 4: 'Resuelto', 5: 'Cerrado'}.get(remote['status'], str(remote['status']))
            ticket['sla_from_freshdesk'] = {key: remote.get(key) for key in (
                'due_by', 'fr_due_by', 'nr_due_by', 'is_escalated', 'fr_escalated', 'nr_escalated', 'stats')}
            conversations = api.listing(f'tickets/{tid}/conversations')
            ticket['_movement_dates'] = [n.get(field) for n in conversations for field in ('created_at', 'updated_at')]
            public, private = [], []
            for conversation in conversations:
                if conversation.get('private') and not include_private:
                    continue
                if 'private' not in conversation:
                    raise ApiError('Una conversación no indica si es privada; no se exporta como pública')
                note = {**conversation, 'text_available': 'body' in conversation or 'body_text' in conversation}
                (private if note['private'] else public).append(note)
            ticket['conversations'] = public
            ticket['private_notes'] = private
            ticket['conversation_text_missing'] = sum(not n['text_available'] for n in public + private)
            if ticket['conversation_text_missing']:
                raise ApiError('Hay conversaciones sin campos de texto en la respuesta de la API')
            ticket['remote_content_complete'] = True
        except (ApiError, OSError, ValueError, KeyError, TypeError) as exc:
            # ApiError is deliberately sanitized by the client; do not expose arbitrary HTTP data.
            reason = str(exc) if isinstance(exc, ApiError) else 'No se pudo recuperar el contenido; revisar conexión o respuesta de API'
            errors.append({'ticket_id': tid, 'reason': reason})
        if progress:
            progress(number, len(output['tickets']))
    manifest = output['manifest']
    manifest.update(
        source='local_selection_with_freshdesk_content',
        content_fetched_at_utc=datetime.now(timezone.utc).isoformat(),
        remote_content_errors=errors, remote_content_complete=not errors,
        remote_tickets_completed=sum(t['remote_content_complete'] for t in output['tickets']),
        conversation_text_missing=sum(t['conversation_text_missing'] for t in output['tickets']),
        tickets_without_description=[t['id'] for t in output['tickets'] if not t['description_available']],
        public_conversation_records=sum(len(t['conversations']) for t in output['tickets']),
        private_conversation_records=sum(len(t['private_notes']) for t in output['tickets']),
        api_pages=api.pages,
    )
    manifest['limitations'] = [
        'Los contactos, tickets y filtros se seleccionan del respaldo local; no se descubren tickets nuevos por API.',
        'Se recuperan descripciones y todas las páginas de conversaciones accesibles con la API key utilizada.',
        'Los fallos conservan datos locales identificados como respaldo; revisar remote_content_errors.',
        'El histórico de cambios sigue limitado a las actividades locales disponibles.',
        'Los adjuntos se conservan como metadatos y enlaces; no se descargan sus archivos binarios.',
    ]
    return output
