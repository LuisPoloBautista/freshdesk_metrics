import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from freshdesk_export_ui import package_downloads


class FakeApi:
    calls = []

    def __init__(self, domain, key):
        self.pages = []

    def listing(self, path, **params):
        self.calls.append((path, params))
        if path == 'contacts':
            return [{'id': 10, 'name': 'Contact A', 'company_id': 7}]
        if path == 'companies':
            return [{'id': 7, 'name': 'Institution A'}]
        if path == 'tickets':
            return [{'id': 411, 'requester_id': 10, 'company_id': 7, 'subject': 'Example',
                     'description_text': 'Full\ntext', 'created_at': '2026-08-01T00:00:00Z', 'status': 4}]
        if path.endswith('/conversations'):
            return [{'id': 1, 'private': False, 'body_text': 'Full reply'}]
        return []


class ExportUiTests(unittest.TestCase):
    def app(self):
        return AppTest.from_string('from freshdesk_export_ui import render_export_tab\nrender_export_tab()')

    def test_no_api_request_without_session_credential(self):
        with patch('freshdesk_export_ui.Client') as client:
            app = self.app().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.button(key='export_connect').disabled)
            client.assert_not_called()

    def test_query_download_and_credential_change_clear_results(self):
        with tempfile.TemporaryDirectory() as temp, patch('scripts.export_freshdesk.ROOT', Path(temp)), \
                patch('freshdesk_export_ui.Client', FakeApi):
            app = self.app().run()
            app.text_input(key='export_api_key').set_value('fake-key').run()
            app.button(key='export_connect').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key='export_selected_Institución').value, 7)
            next(b for b in app.button if b.label == 'Consultar y preparar descarga').click().run(timeout=20)
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['export_result']['manifest']['tickets_exported'], 1)
            self.assertEqual(len(app.get('download_button')), 4)
            app.text_input(key='export_api_key').set_value('different-key').run()
            self.assertFalse(app.exception)
            self.assertNotIn('export_result', app.session_state)
            self.assertNotIn('export_catalog', app.session_state)

    def test_zip_allowlist_excludes_input_and_unrelated_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for name in ['tickets.csv', 'contacts.json', 'input_mapping.csv', '.env']:
                (folder / name).write_text('test')
            files, zipped = package_downloads(folder)
            self.assertEqual(set(files), {'tickets.csv'})
            with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
                self.assertEqual(archive.namelist(), ['tickets.csv'])


if __name__ == '__main__':
    unittest.main()
