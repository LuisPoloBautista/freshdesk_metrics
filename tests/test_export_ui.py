import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest


def fixture():
    return {
        'signature': 'test', 'sources': [], 'errors': [], 'duplicates': [],
        'institutions': {'company:7': 'UTP'},
        'contacts': {'10': {'id': '10', 'name': 'Contact A', 'institution_keys': ['company:7'],
                            'institution_names': ['UTP'], 'mapping_source': 'contact_company_id', 'issues': []}},
        'tickets': {'1': {'id': '1', 'requester_id': '10', 'contact_name': 'Contact A',
                          'contact_institutions': ['UTP'], 'institution_key': '', 'institution_name': '',
                          'subject': 'Example', 'created_at': '2025-01-01T00:00:00Z',
                          'description_text': 'Full\ntext', 'product': 'Koha', 'status': 4,
                          'mapping_source': 'contact_company_id', 'activity_history': [], 'conversations': []}},
    }


class ExportUiTests(unittest.TestCase):
    def app(self):
        return AppTest.from_string('from freshdesk_export_ui import render_export_tab\nrender_export_tab()')

    def test_local_query_without_credentials_or_network(self):
        with patch('freshdesk_export_ui.cached_dataset', return_value=fixture()), \
                patch('urllib.request.urlopen', side_effect=AssertionError('Network not allowed')):
            app = self.app().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.text_input)
            self.assertEqual(app.selectbox(key='export_selected_Institución').value, 'company:7')
            next(b for b in app.button if b.label == 'Consultar y preparar descarga').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['export_result']['manifest']['tickets_exported'], 1)
            self.assertEqual(len(app.get('download_button')), 4)
            app.radio(key='export_mode').set_value('Contacto').run()
            self.assertEqual(app.selectbox(key='export_selected_Contacto').value, '10')
            next(b for b in app.button if b.label == 'Consultar y preparar descarga').click().run()
            self.assertEqual(app.session_state['export_result']['manifest']['selection']['mode'], 'contact')

    def test_source_changes_clear_old_downloads(self):
        with patch('freshdesk_export_ui.cached_dataset', return_value=fixture()), \
                patch('freshdesk_export_ui.source_signature', return_value='first') as signature:
            app = self.app().run()
            next(b for b in app.button if b.label == 'Consultar y preparar descarga').click().run()
            signature.return_value = 'updated'
            app.run()
            self.assertNotIn('export_result', app.session_state)
            self.assertEqual(len(app.get('download_button')), 1)


if __name__ == '__main__':
    unittest.main()
