import io
import unittest
from openpyxl import load_workbook
import test_local_export
from export_identity import InstitutionIndex, contact_groups
from freshdesk_history_export import export_history
from freshdesk_excel import build_excel


class HistoryTests(unittest.TestCase):
    def setUp(self):
        helper = test_local_export.LocalExportTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.data = helper.load()

    def test_direct_institution_does_not_transfer_other_tickets(self):
        result = export_history(self.data, 'institution', 'company:7', '2026-08-01', '2026-08-01')
        self.assertEqual({t['id'] for t in result['tickets']}, {'1', '3', '4'})
        self.assertEqual(result['manifest']['included_by_ticket_institution'], ['4'])
        self.assertFalse(result['manifest']['extraction_complete'])

    def test_movement_range_keeps_full_history_and_all_statuses(self):
        self.data['tickets']['1']['updated_at'] = '2026-11-01T00:00:00Z'
        self.data['tickets']['1']['status'] = 5
        self.data['tickets']['1']['activity_history'] = [
            {'performed_at': '2026-08-01T23:59:59Z', 'activity': {'status': 5}},
            {'performed_at': '2026-07-01T00:00:00Z', 'activity': {'status': 2}}]
        r = export_history(self.data, 'contact', '10', '2026-08-01', '2026-08-01')
        self.assertEqual(len(r['tickets']), 1)
        self.assertEqual(len(r['tickets'][0]['activity_history']), 2)
        w = load_workbook(io.BytesIO(build_excel(r)))
        self.assertEqual(w['Cambios_estado'].max_row, 3)
        empty = export_history(self.data, 'contact', '10', '2026-09-01', '2026-09-02')
        self.assertEqual(empty['tickets'], [])

    def test_api_discovery_and_conversation_date_match(self):
        class Api:
            pages = []
            def listing(self, path, **params):
                if path == 'companies': return [{'id': 7, 'name': 'UTP'}]
                if path == 'contacts': return [{'id': 10, 'name': 'Same name', 'company_id': 7}]
                if path == 'tickets':
                    return [{'id': 99, 'requester_id': 10, 'company_id': 7, 'status': 5,
                             'created_at': '2025-01-01T00:00:00Z', 'updated_at': '2026-11-01T00:00:00Z',
                             'description_text': 'Complete description'}]
                return [{'id': 1001, 'private': False, 'created_at': '2026-09-15T00:00:00Z', 'body_text': 'Complete reply'}]
            def request(self, path, params):
                tid = int(path.split('/')[1])
                return {'id': tid, 'requester_id': 10, 'description_text': 'Existing'}
        r = export_history(self.data, 'contact', '10', '2026-09-15', '2026-09-15', Api())
        self.assertEqual({t['id'] for t in r['tickets']}, {'1', '99'})
        self.assertEqual(r['manifest']['new_ticket_ids_from_api'], ['99'])
        self.assertTrue(r['manifest']['extraction_complete'])
        self.assertEqual(r['tickets'][1]['conversations'][0]['body_text'], 'Complete reply')

    def test_excel_long_text_formula_and_private_separation(self):
        text = '=SUM(1,2)\n' + ('á😀\n' * 16000)
        t = self.data['tickets']['1']
        t['description_text'] = text
        t['subject'] = '=1+1'
        t['conversations'] = [{'id': 88, 'private': True, 'body_text': 'secret-note', 'text_available': True}]
        r = export_history(self.data, 'contact', '10', '2026-08-01', '2026-08-01', include_private=True)
        w = load_workbook(io.BytesIO(build_excel(r)))
        headers = [c.value for c in w['Tickets'][1]]
        subject = w['Tickets'].cell(2, headers.index('Asunto') + 1)
        self.assertEqual(subject.data_type, 's')
        rows = list(w['Textos_largos'].values)
        reconstructed = ''.join(row[-1] for row in rows[1:] if row[3] == 'Descripción completa')
        self.assertEqual(reconstructed, text)
        self.assertEqual(w['Notas_internas'].max_row, 2)
        self.assertEqual(w['Respuestas'].max_row, 1)
        r = export_history(self.data, 'contact', '10', '2026-08-01', '2026-08-01', include_private=False)
        w = load_workbook(io.BytesIO(build_excel(r)))
        self.assertNotIn('Notas_internas', w.sheetnames)
        self.assertNotIn('secret-note', str([list(s.values) for s in w]))


class IdentityTests(unittest.TestCase):
    def test_names_aliases_and_company_ids(self):
        i = InstitutionIndex({'7': {'id': 7, 'name': 'Universidad Á', 'aliases': ['UA'], 'id_aliases': [8]},
                              '8': {'id': 8, 'name': 'Otra variante'}})
        self.assertEqual(i.label(' UNIVERSIDAD A! '), i.company(8))
        self.assertEqual(i.add_remote_company({'id': 9, 'name': 'UA'}), i.company(7))

    def test_homonyms_are_separate_but_identity_is_grouped(self):
        c = {'1': {'name': 'Ana', 'email': 'a@example.com'}, '2': {'name': 'Ana', 'email': 'b@example.com'},
             '3': {'name': 'Ana M', 'email': 'A@example.com'}}
        g = contact_groups(c)
        self.assertEqual(len(g), 2)
        self.assertEqual(g['1']['contact_ids'], ['1', '3'])
