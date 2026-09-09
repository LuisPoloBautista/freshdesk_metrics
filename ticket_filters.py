"""Ticket ownership is independent of the author of an activity."""

from datetime import date


def filter_traceable_tickets(df, start_date=date(2026, 4, 15)):
    """Require a recorded creation on or after the reporting start date."""
    created = df['activity_type'].eq('Ticket Creado') & df['date'].ge(start_date)
    ticket_numbers = df.loc[created, 'ticket_num'].unique()
    return df[df['ticket_num'].isin(ticket_numbers) & df['date'].ge(start_date)].copy()

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
