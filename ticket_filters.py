"""Ticket ownership is independent of the author of an activity."""

from datetime import date
import pandas as pd


def apply_ticket_snapshot(df, tickets):
    """Use exported ticket fields, preserving field changes newer than the export."""
    result = df.copy()
    latest = {}
    for ticket in tickets:
        key = normalize_id(ticket['display_id'])
        if key not in latest or ticket['updated_at'] > latest[key]['updated_at']:
            latest[key] = ticket
    for number, group in result.groupby('ticket_num'):
        ticket = latest.get(normalize_id(number))
        if ticket is None:
            continue
        exported_at = pd.to_datetime(ticket['updated_at'], utc=True)
        fields = [
            ('responder_id', 'assigned_agent_id', 'has_agent_id', normalize_id),
            ('requester_id', 'customer_id', 'has_requester_id', normalize_id),
        ]
        for source, target, flag, convert in fields:
            changes = group.loc[group[flag], 'timestamp']
            if source in ticket and not (pd.to_datetime(changes, utc=True) > exported_at).any():
                result.loc[group.index, target] = convert(ticket[source])
        custom = ticket.get('custom_field') or {}
        product_key = 'cf_producto_3748365'
        changes = group.loc[group['producto'].notna(), 'timestamp']
        if product_key in custom and not (pd.to_datetime(changes, utc=True) > exported_at).any():
            result.loc[group.index, 'ticket_product'] = custom[product_key] or 'Sin producto'
    return result


def filter_traceable_tickets(df, start_date=date(2026, 4, 15)):
    """Require a recorded creation on or after the reporting start date."""
    created = df['activity_type'].eq('Ticket Creado') & df['date'].ge(start_date)
    ticket_numbers = df.loc[created, 'ticket_num'].unique()
    return df[df['ticket_num'].isin(ticket_numbers) & df['date'].ge(start_date)].copy()


def include_exported_tickets(df, tickets, start_date=date(2026, 4, 15)):
    """Include exported tickets by creation date, with explicit inventory-only rows."""
    exports = {normalize_id(t['display_id']): t for t in tickets}
    eligible = {key for key, t in exports.items()
                if date.fromisoformat(t['created_at'][:10]) >= start_date}
    if df.empty:
        result = df.copy()
    else:
        fallback = set(filter_traceable_tickets(df, start_date)['ticket_num'].map(normalize_id))
        allowed = eligible | (fallback - exports.keys())
        result = df[df['ticket_num'].map(normalize_id).isin(allowed)].copy()
    result['inventory_only'] = False
    present = set(result['ticket_num'].map(normalize_id)) if not result.empty else set()
    records = []
    for key in sorted(eligible - present):
        t = exports[key]
        dt = pd.to_datetime(t['created_at'], utc=True)
        records.append(dict(
            ticket_num=int(key), ticket_id=f'#{key}', timestamp=dt,
            date=dt.date(), hour=dt.hour, weekday=dt.strftime('%A'),
            agent_id='', has_agent_id=False, requester_id='', has_requester_id=False,
            priority={1: 'Baja', 2: 'Media', 3: 'Alta', 4: 'Urgente'}.get(t.get('priority'), 'Media'),
            producto=(t.get('custom_field') or {}).get('cf_producto_3748365'),
            ticket_type=t.get('ticket_type'), performer_type='system', performer_id='inventory',
            performer_name='Sin historial disponible', activity_type='Sin historial disponible',
            detail='Ticket de la exportación; no hay actividades descargadas.',
            status_change=None, raw='', inventory_only=True))
    if records:
        result = pd.concat([result, pd.DataFrame(records)], ignore_index=True)
    if not result.empty:
        result = result.sort_values('timestamp', kind='stable').reset_index(drop=True)
    return result

def normalize_id(value):
    if value is None or str(value).strip() in ('', '0', 'None', 'nan'):
        return ''
    return str(int(value))


def survey_ticket_numbers(surveys, tickets):
    """Translate export internal IDs to the ticket numbers used by activities."""
    mapping = {normalize_id(t['id']): normalize_id(t['display_id']) for t in tickets}
    return surveys['surveyable_id'].map(normalize_id).map(mapping)


def attach_ticket_owners(df):
    result = df.copy()
    for field, target in [('agent_id', 'assigned_agent_id'),
                          ('requester_id', 'customer_id')]:
        # Keep explicit nulls: an unassignment must replace the previous owner.
        changes = df[df['has_' + field]].sort_values('timestamp', kind='stable')
        latest = changes.drop_duplicates('ticket_num', keep='last').set_index('ticket_num')[field]
        result[target] = result['ticket_num'].map(latest).fillna('')
    return result


def filter_owners(df, agents=(), customers=()):
    if agents:
        df = df[df['assigned_agent_id'].isin(agents)]
    if customers:
        df = df[df['customer_id'].isin(customers)]
    return df.copy()


def filter_participants(df, participants=()):
    """Keep tickets with a matching human activity in the filtered event set."""
    if not participants:
        return df.copy()
    matching = df['performer_id'].isin(participants) & df['performer_type'].ne('system')
    tickets = df.loc[matching, 'ticket_num'].unique()
    return df[df['ticket_num'].isin(tickets)].copy()
