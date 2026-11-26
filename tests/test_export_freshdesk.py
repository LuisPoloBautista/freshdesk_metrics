import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.export_freshdesk import (
    ApiError, Client, audit_contacts, company_ids, export_data, matches,
    read_mapping, select_contacts, write_csv,
)


class ExportTests(unittest.TestCase):
    def test_pagination_continues_after_short_page(self):
        client = Client('example.freshdesk.com', 'test')
        with patch.object(client, 'request', side_effect=[[{'id': 1}], [{'id': 2}], []]) as request:
            self.assertEqual([r['id'] for r in client.listing('tickets')], [1, 2])
            self.assertEqual(request.call_count, 3)

    def test_repeated_page_is_error(self):
        client = Client('example.freshdesk.com', 'test')
        with patch.object(client, 'request', return_value=[{'id': 1}]):
            with self.assertRaises(ApiError):
                client.listing('contacts')

    def test_secondary_company_and_no_name_based_join(self):
        contacts = [{'id': 1, 'name': 'Same', 'company_id': 7},
                    {'id': 2, 'name': 'Same', 'company_id': None},
                    {'id': 3, 'other_companies': [7, {'company_id': 8}]}]
        self.assertEqual(company_ids(contacts[2]), {7, 8})
        self.assertEqual(set(select_contacts(contacts, [], company_id=7)), {1, 3})
        rows, duplicates = audit_contacts(contacts, [{'id': 7}, {'id': 8}])
        self.assertEqual(len(duplicates), 1)
        self.assertIn('sin_institucion', rows[1]['issues'])
        self.assertIn('multiples_instituciones', rows[2]['issues'])

    def test_mapping_conflict_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'map.csv'
            path.write_text('contact_id,company_id,status,evidence\n1,8,verified,Confirmed\n')
            with self.assertRaises(ValueError):
                read_mapping(path, [{'id': 1, 'company_id': 7}], [{'id': 8}])

    def test_csv_preserves_multiline_and_escapes_formula(self):
        text = 'first\n"quoted", second\r\n' + ('Long text ' * 8000)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'data.csv'
            write_csv(path, [{'body': text, 'subject': '=1+1'}], ['body', 'subject'])
            with path.open(encoding='utf-8-sig', newline='') as f:
                row = next(csv.DictReader(f))
            self.assertEqual(row['body'], text)
            self.assertEqual(row['subject'], "'=1+1")

    def args(self, **overrides):
        values = dict(company_id=7, contact_id=None, start_date=None, end_date=None,
                      date_field='created_at', status=None, product=None, product_id=None,
                      product_field='cf_producto', include_private_notes=False)
        return SimpleNamespace(**{**values, **overrides})

    def test_filters_include_end_date_and_match_custom_product(self):
        ticket = {'created_at': '2026-08-31T23:59:59Z', 'status': 4,
                  'custom_fields': {'cf_producto': 'Koha'}}
        args = self.args(start_date='2026-08-01', end_date='2026-08-31', status=[4], product='koha')
        self.assertTrue(matches(ticket, args))
        self.assertFalse(matches(ticket, self.args(end_date='2026-08-30')))

    def test_company_export_includes_requester_ticket_without_company(self):
        ticket = {'id': 411, 'requester_id': 1, 'company_id': None,
                  'description_text': 'Full\ntext', 'description': '<p>Full</p>text',
                  'created_at': '2020-01-01T00:00:00Z', 'status': 5}

        class FakeApi:
            pages = []

            def listing(self, path, **params):
                if path == 'tickets':
                    return [dict(ticket)] if params.get('requester_id') == 1 else []
                if path.endswith('/conversations'):
                    return [{'id': 1, 'body_text': 'Public\nreply', 'private': False},
                            {'id': 2, 'body_text': 'Internal', 'private': True}]
                return []

        selected = {1: {'id': 1, 'company_id': 7, '_mapping_source': 'freshdesk'},
                    2: {'id': 2, 'company_id': 7, '_mapping_source': 'freshdesk'}}
        with tempfile.TemporaryDirectory() as temp, patch('scripts.export_freshdesk.ROOT', Path(temp)):
            out = Path(temp) / 'output'
            out.mkdir()
            manifest = export_data(FakeApi(), selected, self.args(), out)
            self.assertEqual(manifest['tickets_exported'], 1)
            self.assertEqual(manifest['requester_ticket_ids_absent_from_company_query'], [411])
            self.assertEqual(manifest['private_notes_omitted'], 1)
            exported = json.loads((out / 'tickets.json').read_text(encoding='utf-8'))
            self.assertEqual(exported[0]['description_text'], ticket['description_text'])
            self.assertEqual(len(exported[0]['conversations']), 1)
            self.assertFalse((out / 'notes_private.json').exists())
            with (out / 'coverage_by_contact.csv').open(encoding='utf-8-sig', newline='') as f:
                self.assertEqual(list(csv.DictReader(f))[1]['exported_count'], '0')

    def test_failed_conversations_are_not_reported_complete(self):
        class FakeApi:
            pages = []

            def listing(self, path, **params):
                if path == 'tickets':
                    return [{'id': 1, 'requester_id': 2}] if 'requester_id' in params else []
                if 'conversations' in path:
                    raise ApiError('HTTP 403')
                return []

        with tempfile.TemporaryDirectory() as temp, patch('scripts.export_freshdesk.ROOT', Path(temp)):
            result = export_data(FakeApi(), {2: {'id': 2, '_mapping_source': 'freshdesk'}}, self.args(), Path(temp))
            self.assertFalse(result['queries_completed'])
            self.assertEqual(result['missing_conversation_ticket_ids'], [1])

    def test_provisional_mapping_is_explicit_and_does_not_mutate_contact(self):
        contact = {'id': 1, 'company_id': None}
        mapping = {'contact_id': 1, 'company_id': 7, 'status': 'provisional', 'evidence': 'Review pending'}
        selected = select_contacts([contact], [mapping], company_id=7)
        self.assertEqual(selected[1]['_mapping_source'], 'provisional')
        self.assertIsNone(contact['company_id'])

    def test_secondary_email_detects_possible_duplicate(self):
        contacts = [{'id': 1, 'email': 'one@example.com', 'other_emails': ['shared@example.com']},
                    {'id': 2, 'email': 'SHARED@example.com'}]
        _, duplicates = audit_contacts(contacts, [])
        self.assertEqual(duplicates[0]['contact_ids'], [1, 2])

    def test_private_notes_are_separate_when_enabled(self):
        class FakeApi:
            pages = []

            def listing(self, path, **params):
                if path == 'tickets':
                    return [{'id': 1, 'requester_id': 2}] if 'requester_id' in params else []
                if 'conversations' in path:
                    return [{'id': 3, 'private': True, 'body_text': 'Private\ncontent'}]
                return []

        with tempfile.TemporaryDirectory() as temp, patch('scripts.export_freshdesk.ROOT', Path(temp)):
            result = export_data(FakeApi(), {2: {'id': 2, '_mapping_source': 'freshdesk'}},
                                 self.args(include_private_notes=True), Path(temp))
            self.assertEqual(result['private_notes_exported'], 1)
            self.assertEqual(result['private_notes_omitted'], 0)
            tickets = json.loads((Path(temp) / 'tickets.json').read_text(encoding='utf-8'))
            self.assertEqual(tickets[0]['conversations'], [])
            notes = json.loads((Path(temp) / 'notes_private.json').read_text(encoding='utf-8'))
            self.assertEqual(notes[0]['body_text'], 'Private\ncontent')


if __name__ == '__main__':
    unittest.main()
