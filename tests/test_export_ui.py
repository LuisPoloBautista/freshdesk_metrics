import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from export_identity import InstitutionIndex


def fixture():
    return {
        'signature': 'test', 'sources': [], 'errors': [], 'duplicates': [],
        'institution_index': InstitutionIndex({'7': {'id': 7, 'name': 'UTP'}}),
        'companies': {'7': {'id': 7, 'name': 'UTP'}}, 'people': {},
        'institutions': {'company:7': 'UTP'},
        'contacts': {'10': {'id': '10', 'name': 'Contact A', 'institution_keys': ['company:7'],
                            'institution_names': ['UTP'], 'mapping_source': 'contact_company_id', 'issues': []}},
        'tickets': {'1': {'id': '1', 'requester_id': '10', 'contact_name': 'Contact A',
                          'contact_institutions': ['UTP'], 'institution_key': '', 'institution_name': '',
                          'subject': 'Example', 'created_at': '2026-08-01T00:00:00Z',
                          'description_text': 'Full\ntext', 'product': 'Koha', 'status': 4,
                          'mapping_source': 'contact_company_id', 'activity_history': [], 'conversations': []}},
    }


class ExportUiTests(unittest.TestCase):
    def app(self):
        return AppTest.from_string('from freshdesk_export_ui import render_export_tab\nrender_export_tab()')

    def test_only_dates_and_single_download_after_selection(self):
        with patch('freshdesk_export_ui.cached_dataset', return_value=fixture()):
            app = self.app().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.date_input), 2)
            self.assertEqual(len(app.selectbox), 1)
            self.assertEqual(len(app.radio), 1)
            self.assertFalse(app.checkbox)
            self.assertFalse(app.multiselect)
            self.assertFalse(app.expander)
            self.assertFalse(app.button)
            self.assertEqual(len(app.get('download_button')), 1)
            self.assertTrue(app.get('download_button')[0].proto.disabled)

    def test_credentials_refresh_catalog_and_range_controls_download(self):
        with patch('freshdesk_export_ui.cached_dataset', return_value=fixture()), \
             patch('freshdesk_export_ui.refresh_catalog', return_value=(fixture(), [])) as refresh:
            app = self.app().run()
            app.text_input[0].set_value('fake-key').run()
            self.assertFalse(app.exception)
            self.assertEqual(refresh.call_count, 1)
            self.assertFalse(app.get('download_button')[0].proto.disabled)
            app.run()
            self.assertEqual(refresh.call_count, 1)
            from datetime import date
            app.date_input(key='export_start').set_value(date(2030, 1, 1)).run()
            self.assertTrue(app.get('download_button')[0].proto.disabled)
            app.text_input[0].set_value('different-key').run()
            self.assertEqual(refresh.call_count, 2)

    def test_deferred_download_produces_excel_without_private_notes(self):
        from datetime import date
        from io import BytesIO
        from openpyxl import load_workbook
        from freshdesk_export_ui import excel_download
        class Api:
            pages = []
            def __init__(self, domain, key): pass
            def request(self, path, params):
                return {'id': 1, 'requester_id': 10, 'description_text': 'Full description'}
            def listing(self, path, **params):
                if path == 'companies': return [{'id': 7, 'name': 'UTP'}]
                if path == 'contacts': return [{'id': 10, 'name': 'Contact A', 'company_id': 7}]
                if path == 'tickets': return []
                return [{'id': 2, 'private': False, 'body_text': 'Full reply'},
                        {'id': 3, 'private': True, 'body_text': 'Internal secret'}]
        with patch('freshdesk_export_ui.Client', Api):
            content = excel_download(fixture(), 'institution', 'company:7', date(2026, 8, 1), date(2026, 8, 1), 'fake-key')
        workbook = load_workbook(BytesIO(content))
        self.assertEqual(workbook['Tickets'].max_row, 2)
        values = str([list(sheet.values) for sheet in workbook])
        self.assertIn('Full reply', values)
        self.assertNotIn('Internal secret', values)
        self.assertNotIn('fake-key', values)
        self.assertNotIn('Notas_internas', workbook.sheetnames)
