"""Excel workbook with complete text spillover, traceability and explicit coverage."""
import io
import json
import re
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from local_freshdesk_export import timestamp
from freshdesk_history_export import in_range


def text_parts(text, unit_limit=30000, newline_limit=240):
    """Respect Excel's UTF-16 cell limit AND line-feed limit; never truncate."""
    chunks, current, units, lines = [], [], 0, 0
    for char in text:
        size = 2 if ord(char) > 0xFFFF else 1
        newline = int(char == '\n')
        if current and (units + size > unit_limit or lines + newline > newline_limit):
            chunks.append(''.join(current))
            current, units, lines = [], 0, 0
        current.append(char)
        units += size
        lines += newline
    if current:
        chunks.append(''.join(current))
    return chunks or ['']


def activity_rows(result):
    rows = []
    start, end = result['manifest']['filters']['start'], result['manifest']['filters']['end']
    for ticket in result['tickets']:
        notes = {str(n.get('id')): n for n in ticket['conversations'] + ticket['private_notes']}
        previous_status = ''
        events = sorted(ticket['activity_history'], key=lambda a: timestamp(a.get('performed_at')))
        has_creation = any('new_ticket' in e.get('activity', {}) for e in events)
        if ticket.get('created_at') and not has_creation:
            rows.append({'Ticket': ticket['id'], 'Fecha UTC': timestamp(ticket['created_at']).isoformat(),
                         'Tipo': 'Creación', 'Contacto': ticket['contact_name'],
                         'Texto relacionado': ticket.get('description_text') or ticket.get('description_html'),
                         'En el rango': in_range(ticket['created_at'], start, end),
                         'Fuente': 'Campo created_at del ticket; no es un evento del archivo de actividades'})
        for event in events:
            act = event.get('activity') or {}
            types = []
            if 'new_ticket' in act:
                types.append('Creación')
            if 'status' in act:
                types.append('Cambio de estado')
            if 'note' in act:
                types.append({0: 'Respuesta', 1: 'Reenvío', 2: 'Respuesta a reenvío',
                              3: 'Nota interna', 4: 'Nota pública'}.get(act['note'].get('type'), 'Nota'))
            if 'agent_id' in act or 'group' in act:
                types.append('Asignación')
            if 'automation' in act:
                types.append('Automatización')
            note_id = str((act.get('note') or {}).get('id') or '')
            note = notes.get(note_id, {})
            actor = str(event.get('performer_id') or '')
            rows.append({
                'Ticket': ticket['id'], 'Fecha UTC': timestamp(event.get('performed_at')).isoformat(),
                'Tipo': ' / '.join(types) or 'Actualización', 'Contacto': ticket['contact_name'],
                'Actor ID': actor, 'Actor': result.get('people', {}).get(actor) or event.get('performer_type'),
                'Estado anterior observado': previous_status if 'status' in act else '',
                'Estado registrado': act.get('status'), 'Respuesta ID': note_id,
                'Texto relacionado': note.get('body_text') or note.get('body') or act.get('description'),
                'Detalle completo': act, 'En el rango': in_range(event.get('performed_at'), start, end),
                'Fuente': event.get('_source_file', 'Actividad local'),
            })
            if 'status' in act:
                previous_status = act['status']
    return sorted(rows, key=lambda r: (r['Fecha UTC'], int(r['Ticket'])))


def build_excel(result):
    workbook = Workbook()
    workbook.remove(workbook.active)
    overflow = []

    def cell_value(value, sheet, record, field):
        if isinstance(value, (dict, list, tuple, set)):
            value = json.dumps(list(value) if isinstance(value, set) else value, ensure_ascii=False)
        if value is None:
            return ''
        if not isinstance(value, str):
            return value
        encoding = 'texto'
        if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', value):
            value = json.dumps(value, ensure_ascii=True)
            encoding = 'cadena JSON; decodificar para recuperar caracteres de control'
        chunks = text_parts(value)
        if len(chunks) > 1 or encoding != 'texto':
            ref = f'{sheet}:{record}:{field}'
            overflow.extend({'Referencia': ref, 'Hoja': sheet, 'Registro': str(record), 'Campo': field,
                             'Parte': i, 'Total partes': len(chunks), 'Codificación': encoding, 'Texto': chunk}
                            for i, chunk in enumerate(chunks, 1))
            return f'Ver Textos_largos: {ref} ({len(chunks)} partes)'
        return value

    def sheet(name, rows, fields=None, spill=True):
        rows = list(rows)
        fields = fields or list(dict.fromkeys(key for row in rows for key in row)) or ['Sin registros']
        if len(rows) > 1048575:
            raise ValueError(f'La hoja {name} supera el límite de filas de Excel. Reduce el rango.')
        ws = workbook.create_sheet(name)
        ws.freeze_panes = 'A2'
        ws.append(fields)
        ws.row_dimensions[1].height = 32
        for cell in ws[1]:
            cell.font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='214E76')
            cell.alignment = Alignment(wrap_text=True, vertical='center')
        for row_number, row in enumerate(rows, 2):
            values = [cell_value(row.get(field), name, row_number, field) if spill else row.get(field, '') for field in fields]
            ws.append(values)
            for cell in ws[row_number]:
                # Prevent user-provided text being interpreted as Excel formulas.
                if isinstance(cell.value, str):
                    cell.data_type = 's'
                cell.font = Font(name='Calibri', size=11)
                cell.alignment = Alignment(vertical='top', wrap_text=True)
            ws.row_dimensions[row_number].height = 45
        for col, field in enumerate(fields, 1):
            ws.column_dimensions[get_column_letter(col)].width = 60 if any(w in field for w in ['Texto', 'Descripción', 'HTML', 'Detalle', 'Valor']) else 25
        ws.auto_filter.ref = ws.dimensions
        return ws

    manifest = result['manifest']
    summary = [
        {'Concepto': 'Selección', 'Valor': result.get('label') or manifest.get('selection')},
        {'Concepto': 'Rango UTC', 'Valor': manifest['filters']},
        {'Concepto': 'Regla de selección', 'Valor': 'Tickets creados o con movimientos/respuestas en el rango, en todos los estados. Se incluye su historial disponible completo.'},
        {'Concepto': 'Tickets incluidos', 'Valor': len(result['tickets'])},
        {'Concepto': 'Resultado', 'Valor': 'Sin errores de consulta detectados' if manifest.get('extraction_complete') else 'Parcial o solo datos locales; revisar Incidencias'},
        {'Concepto': 'Inicio de actividades locales UTC', 'Valor': manifest.get('history_coverage', {}).get('first_activity_utc')},
        {'Concepto': 'Última actividad local UTC', 'Valor': manifest.get('history_coverage', {}).get('last_activity_utc')},
        {'Concepto': 'Textos extensos', 'Valor': 'Excel limita el tamaño de celda. Textos_largos conserva el contenido en partes numeradas; concatenar Texto por Referencia y Parte sin añadir separadores.'},
        {'Concepto': 'Fechas y estados', 'Valor': 'UTC. Los estados anteriores solo se muestran cuando se observaron en actividades locales; una fecha de creación tomada del ticket está identificada como tal.'},
    ] + [{'Concepto': 'Límite', 'Valor': value} for value in manifest.get('limitations', [])]
    sheet('Resumen', summary, ['Concepto', 'Valor'])
    ticket_rows = []
    public, private = [], []
    start, end = manifest['filters']['start'], manifest['filters']['end']
    for t in result['tickets']:
        ticket_rows.append({
            'Ticket ID': str(t['id']), 'Contacto ID': t['requester_id'], 'Contacto': t['contact_name'],
            'Instituciones del contacto': t['contact_institutions'], 'Institución del ticket': t['institution_name'],
            'Incluido por': t.get('selection_reason'), 'Coincidencia de fechas': t.get('range_match'),
            'Asunto': t.get('subject'), 'Descripción completa': t.get('description_text'), 'Descripción HTML': t.get('description_html'),
            'Estado': t.get('status_name') or t.get('status'), 'Estado ID': t.get('status'),
            'Creado UTC': t.get('created_at'), 'Actualizado UTC': t.get('updated_at'),
            'Agente ID': t.get('responder_id'), 'Agente': t.get('agent_name'), 'Producto': t.get('product'),
            'Prioridad': t.get('priority'), 'SLA disponible': t.get('sla_from_freshdesk', t.get('sla')),
            'Contenido completo recuperado': t.get('remote_content_complete', False), 'Fuente': t.get('content_source', t.get('source')),
        })
        for is_private, notes in [(False, t['conversations']), (True, t['private_notes'])]:
            for note in notes:
                uid = str(note.get('user_id') or '')
                row = {'Ticket ID': t['id'], 'Respuesta ID': str(note.get('id') or ''),
                       'Contacto': t['contact_name'], 'Autor ID': uid, 'Autor': result.get('people', {}).get(uid, uid),
                       'Creada UTC': note.get('created_at'), 'Actualizada UTC': note.get('updated_at'),
                       'Entrante': note.get('incoming'), 'Tipo / origen': note.get('source', note.get('type')),
                       'Texto completo': note.get('body_text'), 'HTML completo': note.get('body'),
                       'Adjuntos (enlaces)': note.get('attachments'),
                       'En el rango': in_range(note.get('created_at'), start, end) or in_range(note.get('updated_at'), start, end)}
                (private if is_private else public).append(row)
    sheet('Tickets', ticket_rows, list(ticket_rows[0]) if ticket_rows else ['Ticket ID', 'Contacto', 'Asunto'])
    note_fields = list(public[0] if public else private[0]) if public or private else ['Ticket ID', 'Respuesta ID', 'Texto completo']
    sheet('Respuestas', public, note_fields)
    if manifest['private_notes_included']:
        sheet('Notas_internas', private, note_fields)
    activities = activity_rows(result)
    fields = ['Ticket', 'Fecha UTC', 'Tipo', 'Contacto', 'Actor ID', 'Actor', 'Estado anterior observado',
              'Estado registrado', 'Respuesta ID', 'Texto relacionado', 'Detalle completo', 'En el rango', 'Fuente']
    sheet('Actividades', activities, fields)
    sheet('Cambios_estado', [row for row in activities if row.get('Estado registrado') is not None], fields)
    sheet('Contactos', result['mapping'])
    sheet('Cobertura', result['coverage'])
    sheet('Normalizacion', result.get('normalization', []), ['Nombre normalizado de origen', 'Institución agrupada', 'Clave'])
    issues = []
    for key in ['errors', 'discovery_errors', 'remote_content_errors', 'possible_duplicate_groups']:
        issues.extend({'Tipo': key, 'Detalle': value} for value in manifest.get(key, []))
    issues.extend({'Tipo': 'Contacto / institución por revisar', 'Detalle': row} for row in result.get('unresolved', []))
    issues.extend({'Tipo': 'Asociación provisional o pendiente', 'Detalle': row} for row in result['mapping']
                  if row['status'] in ('provisional', 'unassigned') or row['issues'])
    issues.extend({'Tipo': 'Día sin archivo de actividades', 'Detalle': day}
                  for day in manifest.get('history_coverage', {}).get('missing_file_dates', []))
    sheet('Incidencias', issues, ['Tipo', 'Detalle'])
    sheet('Textos_largos', overflow, ['Referencia', 'Hoja', 'Registro', 'Campo', 'Parte', 'Total partes', 'Codificación', 'Texto'], spill=False)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
