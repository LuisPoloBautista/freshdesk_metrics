import unittest
from freshdesk_full_content import fetch_full_content
from scripts.export_freshdesk import ApiError, Client
from unittest.mock import patch


class FullContentTests(unittest.TestCase):
    def result(self):
        return {'tickets': [{'id': '42', 'requester_id': '10', 'description_text': 'Local',
                             'description_available': True, 'conversations': [], 'private_notes': [],
                             'conversation_text_missing': 1}], 'manifest': {}}

    def client(self):
        class Api:
            pages = []

            def request(self, path, params):
                return {'id': 42, 'requester_id': 10, 'description_text': 'Long\n' * 20000,
                        'description': '<p>Full HTML</p>', 'subject': 'Live subject'}

            def listing(self, path):
                return [{'id': n, 'private': False, 'body_text': f'Full response {n}\nSecond line'} for n in range(35)] + [
                    {'id': 99, 'private': True, 'body_text': 'Secret internal note'}]
        return Api()

    def test_full_text_not_truncated_and_private_notes_opt_in(self):
        original = self.result()
        fetched = fetch_full_content(original, self.client())
        ticket = fetched['tickets'][0]
        self.assertEqual(len(ticket['description_text']), 100000)
        self.assertEqual(len(ticket['conversations']), 35)
        self.assertEqual(ticket['private_notes'], [])
        self.assertTrue(fetched['manifest']['remote_content_complete'])
        self.assertEqual(original['tickets'][0]['description_text'], 'Local')
        fetched = fetch_full_content(original, self.client(), include_private=True)
        self.assertEqual(fetched['tickets'][0]['private_notes'][0]['body_text'], 'Secret internal note')

    def test_conversation_failure_reports_partial_and_preserves_available_description(self):
        api = self.client()
        with patch.object(api, 'listing', side_effect=ApiError('HTTP 403')):
            fetched = fetch_full_content(self.result(), api)
        self.assertFalse(fetched['manifest']['remote_content_complete'])
        self.assertEqual(fetched['manifest']['remote_content_errors'][0]['ticket_id'], '42')
        self.assertEqual(fetched['tickets'][0]['content_source'], 'freshdesk')

    def test_changed_requester_does_not_import_other_contact_content(self):
        api = self.client()
        with patch.object(api, 'request', return_value={'id': 42, 'requester_id': 99, 'description_text': 'Other'}):
            fetched = fetch_full_content(self.result(), api)
        self.assertFalse(fetched['manifest']['remote_content_complete'])
        self.assertEqual(fetched['tickets'][0]['description_text'], 'Local')

    def test_actual_client_paginates_conversations_to_empty_page(self):
        api = Client('example.freshdesk.com', 'fake-key')
        ticket = {'id': 42, 'requester_id': 10, 'description_text': 'Full'}
        with patch.object(api, 'request', side_effect=[ticket,
                [{'id': 1, 'private': False, 'body_text': 'First page'}],
                [{'id': 2, 'private': False, 'body_text': 'Second page'}], []]) as request:
            result = fetch_full_content(self.result(), api)
        self.assertTrue(result['manifest']['remote_content_complete'])
        self.assertEqual(len(result['tickets'][0]['conversations']), 2)
        self.assertEqual(request.call_count, 4)


if __name__ == '__main__':
    unittest.main()
