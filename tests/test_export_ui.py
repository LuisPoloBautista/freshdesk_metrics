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

    def test_local_query_without_credentials_or_network(self):
        with patch('freshdesk_export_ui.cached_dataset', return_value=fixture()), \
                patch('urllib.request.urlopen', side_effect=AssertionError('Network not allowed')):
            app = self.app().run()
            app.radio(key='full_content_mode').set_value('Solo datos locales').run()
            self.assertFalse(app.exception)
            self.assertFalse(app.text_input)
            self.assertEqual(app.selectbox(key='export_selected_Institución').value, 'company:7')
            next(b for b in app.button if b.label == 'Consultar y preparar descarga').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['export_result']['manifest']['tickets_exported'], 1)
            self.assertEqual(len(app.get('download_button')), 3)
            app.radio(key='export_mode').set_value('Contacto').run()
            self.assertEqual(app.selectbox(key='export_selected_Contacto').value, '10')
            next(b for b in app.button if b.label == 'Consultar y preparar descarga').click().run()
            self.assertEqual(app.session_state['export_result']['manifest']['selection']['mode'], 'contact')

    def test_source_changes_clear_old_downloads(self):
        with patch('freshdesk_export_ui.cached_dataset', return_value=fixture()), \
                patch('freshdesk_export_ui.source_signature', return_value='first') as signature:
            app = self.app().run()
            app.radio(key='full_content_mode').set_value('Solo datos locales').run()
            next(b for b in app.button if b.label == 'Consultar y preparar descarga').click().run()
            signature.return_value = 'updated'
            app.run()
            self.assertNotIn('export_result', app.session_state)
            self.assertEqual(len(app.get('download_button')), 0)

    def test_full_content_requires_key_and_clears_download_on_key_change(self):
        class Api:
            pages = []

            def __init__(self, domain, key):
                pass

            def request(self, path, params):
                return {'id': 1, 'requester_id': 10, 'subject': 'Live', 'description_text': 'Full description'}

            def listing(self, path, **params):
                if path == 'companies':
                    return [{'id': 7, 'name': 'UTP'}]
                if path == 'contacts':
                    return [{'id': 10, 'name': 'Contact A', 'company_id': 7}]
                if path == 'tickets':
                    return []
                return [{'id': 2, 'private': False, 'body_text': 'Full reply'}]

        with patch('freshdesk_export_ui.cached_dataset', return_value=fixture()), patch('freshdesk_export_ui.Client', Api):
            app = self.app().run()
            submit = next(b for b in app.button if b.label == 'Consultar y preparar descarga')
            self.assertTrue(submit.disabled)
            app.text_input(key='full_content_api_key').set_value('fake-key').run()
            next(b for b in app.button if b.label == 'Consultar y preparar descarga').click().run()
            self.assertFalse(app.exception)
            result = app.session_state['export_result']
            self.assertTrue(result['manifest']['remote_content_complete'])
            self.assertIn(b'Full reply', result['files']['tickets.json'])
            self.assertNotIn(b'fake-key', result['zip'])
            app.text_input(key='full_content_api_key').set_value('other-key').run()
            self.assertNotIn('export_result', app.session_state)


if __name__ == '__main__':
    unittest.main()
