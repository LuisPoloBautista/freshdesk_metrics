import unittest
import pandas as pd
from ticket_filters import attach_ticket_owners, filter_owners, survey_ticket_numbers, filter_participants


class OwnershipTests(unittest.TestCase):
    def test_participation_independent_of_assignment_and_matching_activity(self):
        df = pd.DataFrame([
            (1, 'a', 'user', 'Reply', 'b', 'c'),
            (1, 'b', 'user', 'Note', 'b', 'c'),
            (2, 'a', 'user', 'Note', 'a', 'd'),
            (2, 'b', 'user', 'Reply', 'a', 'd'),
            (3, 'a', 'system', 'Reply', '', 'c'),
        ], columns=['ticket_num', 'performer_id', 'performer_type',
                    'activity_type', 'assigned_agent_id', 'customer_id'])
        self.assertEqual(set(filter_participants(df, ['a']).ticket_num), {1, 2})
        selected = filter_participants(filter_owners(df, ['b'], ['c']), ['a'])
        self.assertEqual(selected.performer_id.tolist(), ['a', 'b'])
        replies = df[df.activity_type.eq('Reply')]
        self.assertEqual(set(filter_participants(replies, ['a']).ticket_num), {1})
        self.assertEqual(set(filter_participants(replies, ['a', 'b']).ticket_num), {1, 2})
        self.assertTrue(filter_participants(df, ['missing']).empty)
        pd.testing.assert_frame_equal(filter_participants(df), df)

    def test_survey_internal_ids_are_not_display_numbers(self):
        surveys = pd.DataFrame({'surveyable_id': [203008503168, 85]})
        numbers = survey_ticket_numbers(surveys, [{'id': 203008503168, 'display_id': 85}])
        self.assertEqual(numbers.iloc[0], '85')
        self.assertTrue(pd.isna(numbers.iloc[1]))

    def test_reassignment_unassignment_and_combined_filters(self):
        rows = [
            (1, 1, 'a', True, 'c', True, 'Asignación'),
            (1, 2, 'b', True, '', False, 'Asignación'),
            (1, 3, '', False, '', False, 'Nota'),
            (2, 1, 'a', True, 'd', True, 'Asignación'),
            (3, 1, 'a', True, 'c', True, 'Asignación'),
            (3, 2, '', True, '', False, 'Asignación'),
            (4, 1, '', False, 'c', True, 'Nota'),
        ]
        df = attach_ticket_owners(pd.DataFrame(rows, columns=[
            'ticket_num', 'timestamp', 'agent_id', 'has_agent_id',
            'requester_id', 'has_requester_id', 'activity_type']))
        self.assertEqual(set(filter_owners(df, ['a']).ticket_num), {2})
        self.assertEqual(set(filter_owners(df, ['b'], ['c']).ticket_num), {1})
        self.assertTrue(filter_owners(df, ['a'], ['c']).empty)
        self.assertEqual(set(filter_owners(df, ['']).ticket_num), {3, 4})
        selected = filter_owners(df, ['b'])
        selected = selected[selected.activity_type.eq('Asignación')]
        self.assertEqual(set(selected.ticket_num), {1})


if __name__ == '__main__':
    unittest.main()
