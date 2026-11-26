import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_freshdesk_export import build_downloads, load_local_dataset, query_local


class LocalExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'actores').mkdir()
        (self.root / '3748365').mkdir()
        self.write('institution_directory.json', {
            'companies': [{'id': 7, 'name': 'UTP', 'aliases': ['Universidad UTP']}, {'id': 8, 'name': 'Other'}],
            'verified_contact_links': [{'contact_id': 12, 'company_id': 7}],
        })
        self.write('actores/Users0.json', [{'user': c} for c in [
            {'id': 10, 'name': 'Same name', 'company_id': 7},
            {'id': 11, 'name': 'Same name', 'company_id': 8},
            {'id': 12, 'name': 'Confirmed locally', 'company_id': None},
            {'id': 13, 'name': 'Ambiguous', 'company_id': None},
        ]])
        self.tickets = [self.ticket(1, 10, None), self.ticket(2, 11, 'Other'),
                        self.ticket(3, 12, None), self.ticket(4, 13, 'UTP'), self.ticket(5, 13, 'Other')]
        self.save_tickets()

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def ticket(self, tid, requester, label):
        return {'id': 100000 + tid, 'display_id': tid, 'requester_id': requester,
                'subject': f'Ticket {tid}', 'description': 'Full\n"quoted", text',
                'created_at': '2025-01-01T00:00:00Z', 'updated_at': '2026-08-01T00:00:00Z',
                'status': 4, 'custom_field': {'cf_empresa_3748365': label, 'cf_producto_3748365': 'Koha'}}

    def save_tickets(self):
        self.write('3748365/Tickets0.json', [{'helpdesk_ticket': t} for t in self.tickets])

    def load(self):
        return load_local_dataset(self.root)

    def test_no_network_and_stable_contact_ids(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('Network not allowed')):
            data = self.load()
            result = query_local(data, 'institution', 'company:7')
        self.assertEqual({t['id'] for t in result['tickets']}, {'1', '3'})
        self.assertEqual({r['contact_id'] for r in result['coverage']}, {'10', '12'})
        self.assertEqual(len(data['duplicates']), 1)
        self.assertEqual(query_local(data, 'contact', '11')['tickets'][0]['id'], '2')

    def test_ambiguous_institution_is_reported_not_automatically_assigned(self):
        data = self.load()
        self.assertEqual(data['contacts']['13']['institution_keys'], [])
        result = query_local(data, 'institution', 'company:7')
        self.assertEqual([t['id'] for t in result['unresolved']], ['4'])

    def test_case_and_alias_normalization_with_provisional_provenance(self):
        self.tickets = [self.ticket(1, 99, ' universidad utp ')]
        self.save_tickets()
        data = self.load()
        self.assertEqual(data['contacts']['99']['institution_names'], ['UTP'])
        self.assertEqual(data['contacts']['99']['mapping_source'], 'provisional_from_ticket_institution')

    def test_activity_ids_and_timestamps_preserve_snapshot_and_newer_changes(self):
        self.write('activities_test.json', {'activities_data': [
            {'ticket_id': 1, 'performed_at': '2026-07-01T00:00:00Z', 'activity': {'requester_id': 11}},
            {'ticket_id': 1, 'performed_at': '2026-09-01T00:00:00Z', 'activity': {'Producto': 'Dspace'}},
            {'ticket_id': 6, 'performed_at': '2026-09-01T00:00:00Z',
             'activity': {'requester_id': 10, 'new_ticket': True, 'subject': 'Activity only'}},
        ]})
        data = self.load()
        self.assertEqual(data['tickets']['1']['requester_id'], '10')
        self.assertEqual(data['tickets']['1']['internal_id'], '100001')
        self.assertEqual(data['tickets']['1']['product'], 'Dspace')
        result = query_local(data, 'institution', 'company:7')
        self.assertEqual(set(t['id'] for t in result['tickets']), {'1', '3', '6'})
        self.assertEqual(result['manifest']['tickets_without_description'], ['6'])

    def test_missing_note_body_is_not_fabricated_and_private_stays_separate(self):
        events = [{'ticket_id': 1, 'performed_at': '2026-09-01T00:00:00Z',
                   'activity': {'note': {'id': 77, 'type': 0}}}]
        self.write('activities_test.json', {'activities_data': events + events})
        self.tickets[0]['notes'] = [{'id': 88, 'type': 3, 'body': 'Private text'}]
        self.save_tickets()
        result = query_local(self.load(), 'contact', '10')
        self.assertEqual(result['manifest']['conversation_text_missing'], 1)
        files, _ = build_downloads(result)
        self.assertNotIn('Private text', files['tickets.json'].decode())
        self.assertNotIn('notes_private.json', files)
        result = query_local(self.load(), 'contact', '10', include_private=True)
        files, _ = build_downloads(result)
        self.assertIn('Private text', files['notes_private.json'].decode())
        self.assertNotIn('Private text', files['tickets.json'].decode())

    def test_filters_full_text_and_zero_ticket_contact_coverage(self):
        result = query_local(self.load(), 'institution', 'company:7', '2025-01-01', '2025-01-01',
                             products=['Koha'], statuses=[4])
        files, _ = build_downloads(result)
        rows = list(csv.DictReader(io.StringIO(files['tickets.csv'].decode('utf-8-sig'), newline='')))
        self.assertEqual(rows[0]['description_text'], 'Full\n"quoted", text')
        self.assertEqual(rows[0]['contact_name'], 'Same name')
        self.assertEqual(rows[0]['contact_institutions'], '["UTP"]')
        empty = query_local(self.load(), 'institution', 'company:7', products=['Not present'])
        self.assertEqual(empty['tickets'], [])
        self.assertEqual(len(empty['coverage']), 2)

    def test_corrupt_file_reported(self):
        (self.root / 'activities_corrupt.json').write_text('not json')
        self.assertTrue(self.load()['errors'])

    def test_freshdesk_day_first_activity_dates_update_and_filter_correctly(self):
        self.write('activities_test.json', {'activities_data': [
            {'ticket_id': 6, 'performed_at': '26-11-2026 02:00:00 +0000',
             'activity': {'requester_id': 10, 'new_ticket': True, 'Producto': 'Dspace'}},
            {'ticket_id': 1, 'performed_at': '01-09-2026 02:00:00 +0000', 'activity': {'Producto': 'Dspace'}},
        ]})
        data = self.load()
        self.assertEqual(data['tickets']['6']['created_at'], '2026-11-26T02:00:00+00:00')
        self.assertEqual(data['tickets']['1']['product'], 'Dspace')
        result = query_local(data, 'contact', '10', '2026-11-26', '2026-11-26')
        self.assertEqual([t['id'] for t in result['tickets']], ['6'])


if __name__ == '__main__':
    unittest.main()
