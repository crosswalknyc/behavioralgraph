#!/usr/bin/env python3
"""A numbered list of asks is quoted as all of them (Jenna 2026-11-04).

Jay Patel (Amazon) sent seven numbered, quoted asks on Jason Statham
and got "3 priced reads here": the sentence splitter cut at the question
marks inside items, dropped the chunk that opened with "Describe", and
read "Break this down by age, gender, ethnicity, household income and
region" as a geography split. Jenna: "this is more than 3 reads ... just
so next time it realizes it's all of those".

Hermetic: run_one is a stub standing in for the lanes; no model call.
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import multi_ask as M  # noqa: E402
from prometheus import pending_offers as PO  # noqa: E402
from prometheus import referents as R  # noqa: E402

JAY = (
    '1. "Using only data available as of [June 2023]: among US viewers of Jason Statham films on any streaming '
    'service or free TV, what percentage did not watch Prime Video? Break this down by age, gender, ethnicity, '
    'household income and region."\n'
    '2. "As of [June 2023]: which audience segments watched action thrillers such as John Wick, Nobody and '
    'Extraction on Netflix or YouTube but rarely or never watched Prime Video? Rank the segments by size."\n'
    '3. "As of [June 2023]: for viewers of Extraction on Netflix, what share also watched Prime Video, and what '
    'share were exclusive to other services?"\n'
    '4. "As of [June 2023]: which action films released in 2021\u20132023 brought the most new subscribers to the '
    'service they premiered on? Describe those new subscribers by demographics and what they watched before."\n'
    '5. "As of [June 2023]: what Amazon product categories do viewers of Jason Statham and action-thriller films '
    'buy, compared with the average US adult?"\n'
    '6. "As of [June 2023]: rank these titles by the size of their audience that does not watch Prime Video: '
    '[The Beekeeper comparable set], [a title WWSTA passed on], [a title that underperformed]."\n'
    '7. "For each answer above, show the sample size and say whether the viewing data is measured or modeled."'
)

BANNED_RX = re.compile(
    r"\b(?:synthetic|model(?:ed|led)?|estimated|panel|pipeline|research\w*|ledger|lane|cohort|placeholders?)\b"
    r"|\bsample\b", re.I)


def _offer(subject, label, lead, kind='report', dimension='', question=''):
    return {'success': True, 'action': 'answer', 'reply': f"{lead} Pulling this data will cost {label}.",
            'followups': ['Approve', 'Cancel'],
            'panel_offer': {'question': question, 'subject': subject, 'kind': kind, 'frame': '',
                            'price_label': label, 'dimension': dimension}}


def _jay_run_one(calls):
    """The lanes as they read Jay's items: a derived read on Jason Statham
    (the lane picked 'geography' for five dimensions), a custom read on the
    action-thriller set, an overlap on Extraction, the Subscriber IQ
    library answering item 4 from what we hold, and a second custom read
    on Jason Statham for item 5."""
    def run_one(q, pc, prior=None):
        calls.append((q, pc, list(prior or [])))
        low = q.lower()
        if pc:
            return ({'raw': {'success': True, 'action': 'answer', 'reply': f'Done: {q[:40]}',
                             'read_job_id': 'job-%d' % len(calls), 'subject': pc.get('subject')}}, 200)
        if 'jason statham films on any streaming' in low:
            return ({'raw': _offer('Jason Statham', '$100', 'I can split the figures for Jason Statham films by geography.',
                                   kind='derived', dimension='geography', question=q)}, 200)
        if 'audience segments' in low:
            return ({'raw': _offer('John Wick, Nobody and Extraction viewers', '$100',
                                   'I can pull that read on John Wick, Nobody and Extraction viewers.', question=q)}, 200)
        if 'viewers of extraction on netflix' in low:
            return ({'raw': _offer('Extraction viewers on Netflix', '$100', 'I can pull the overlap for Extraction viewers on Netflix.',
                                   kind='overlap', question=q)}, 200)
        if 'new subscribers' in low:
            return ({'raw': {'success': True, 'action': 'answer',
                             'reply': 'Across the Subscriber IQ library, the action films with the most new subscribers: Extraction 2 on Netflix, ...',
                             'followups': ['CSV']}}, 200)
        if 'amazon product categories' in low:
            return ({'raw': _offer('Jason Statham', '$100', 'I can pull that read on Jason Statham.', question=q)}, 200)
        raise AssertionError('a part the lanes never saw: ' + q)
    return run_one


class Parse(unittest.TestCase):
    def test_seven_items_parsed_whole(self):
        items = M.numbered_items(JAY)
        self.assertEqual(len(items), 7)
        # every item is whole: the second sentence rides its item, the quotes are gone
        self.assertTrue(items[0].startswith('Using only data available'))
        self.assertTrue(items[0].endswith('household income and region.'))
        self.assertTrue(items[1].endswith('Rank the segments by size.'))
        self.assertTrue(items[3].endswith('what they watched before.'))
        self.assertTrue(items[6].startswith('For each answer above'))
        self.assertFalse(any('"' in it for it in items))

    def test_item_seven_is_a_rider_on_all(self):
        self.assertTrue(M.is_rider(M.numbered_items(JAY)[6]))
        plan = M.parse(JAY)
        self.assertTrue(plan['numbered'])
        self.assertEqual(len(plan['items']), 6)
        self.assertEqual(plan['numbers'], [1, 2, 3, 4, 5, 6])
        self.assertTrue(plan['rider'])
        self.assertNotIn('model', plan['rider'].lower())
        self.assertEqual(plan['left'], 0)
        # the other six are never riders
        self.assertFalse(any(M.is_rider(it) for it in M.numbered_items(JAY)[:6]))

    def test_split_compat_returns_the_six_asks(self):
        qs = M.split(JAY)
        self.assertEqual(len(qs), 6)
        self.assertTrue(qs[0].startswith('Using only data available as of June 2023:'))

    def test_bracketed_date_is_a_window_not_a_placeholder(self):
        self.assertTrue(R.is_window_text('June 2023'))
        self.assertTrue(R.is_window_text('2021\u20132023'))
        self.assertTrue(R.is_window_text('Q2 2024'))
        self.assertTrue(R.is_window_text('trailing 12 months'))
        self.assertFalse(R.is_window_text('The Beekeeper comparable set'))
        self.assertFalse(R.is_window_text('a title WWSTA passed on'))
        self.assertEqual(R.placeholders('as of [June 2023]: rank [A], [B]'), ['[A]', '[B]'])
        self.assertEqual(M.normalize_item('"As of [June 2023]: which films?"'), 'As of June 2023: which films?')
        item6 = M.parse(JAY)['items'][5]
        self.assertEqual(M.describe(item6)['placeholders'],
                         ['[The Beekeeper comparable set]', '[a title WWSTA passed on]', '[a title that underperformed]'])

    def test_whole_message_is_not_one_referent_question(self):
        # the referent gate at the door leaves a numbered list to the lane;
        # item 6 alone is the which-titles question
        self.assertIsNone(R.detect_unresolved(JAY))
        ref = R.detect_unresolved(M.parse(JAY)['items'][5])
        self.assertEqual((ref['noun'], ref['count']), ('titles', 3))
        self.assertEqual(ref['phrase'], '[The Beekeeper comparable set], [a title WWSTA passed on], [a title that underperformed]')


class Describe(unittest.TestCase):
    def setUp(self):
        self.items = M.parse(JAY)['items']
        self.lines = [M.describe(q)['line'] for q in self.items]

    def test_item_one_names_the_five_dimensions_and_the_window(self):
        d = M.describe(self.items[0])
        self.assertEqual(d['dimensions'], ['age', 'gender', 'ethnicity', 'household income', 'region'])
        self.assertEqual(d['window'], 'as of June 2023')
        self.assertEqual(d['line'],
                         'The percentage of US viewers of Jason Statham films on any streaming service or free TV who did not '
                         'watch Prime Video, by age, gender, ethnicity, household income and region, as of June 2023.')
        self.assertNotIn('geography', d['line'])

    def test_each_line_carries_its_measure_subject_and_window(self):
        self.assertEqual(self.lines[1], 'Which audience segments watched action thrillers such as John Wick, Nobody and Extraction '
                                        'on Netflix or YouTube but rarely or never watched Prime Video, ranked by size, as of June 2023.')
        self.assertEqual(self.lines[2], 'The share of viewers of Extraction on Netflix who also watched Prime Video, and the share who '
                                        'were exclusive to other services, as of June 2023.')
        self.assertEqual(self.lines[3], 'Which action films released in 2021-2023 brought the most new subscribers to the service they '
                                        'premiered on, with those new subscribers described by demographics and what they watched '
                                        'before, as of June 2023.')
        self.assertEqual(self.lines[4], 'The Amazon product categories viewers of Jason Statham and action-thriller films buy, compared '
                                        'with the average US adult, as of June 2023.')
        self.assertEqual(self.lines[5], 'These titles ranked by the size of their audience that does not watch Prime Video, as of June 2023.')
        for line in self.lines:
            self.assertTrue(line.endswith('as of June 2023.'), line)
            self.assertNotIn('\u2014', line)
            self.assertNotIn('\u2013', line)


class Offer(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.out = M.handle(JAY, {'text': JAY}, _jay_run_one(self.calls))
        self.reply = self.out['reply']

    def test_six_listed_one_rider_acknowledged(self):
        self.assertEqual(self.out['multi_ask']['count'], 6)
        for no in range(1, 7):
            self.assertIn(f"\n{no}. ", '\n' + self.reply)
        self.assertNotIn('7.', self.reply)
        self.assertTrue(self.out['multi_ask']['rider'])
        self.assertIn('Each answer will state the people in the data behind it and what it counts.', self.reply)
        # the rider's own words never come back ("measured or ...")
        self.assertNotIn('measured', self.reply.lower())
        self.assertIn('6 asks here, quoted together and run together; 3 carry a price:', self.reply)

    def test_item_one_line_in_the_offer(self):
        self.assertIn('1. The percentage of US viewers of Jason Statham films on any streaming service or free TV who did not '
                      'watch Prime Video, by age, gender, ethnicity, household income and region, as of June 2023. $100.', self.reply)
        self.assertNotIn('by geography', self.reply)

    def test_same_subject_priced_once_and_total_is_the_priced_lines(self):
        self.assertIn('5. The Amazon product categories viewers of Jason Statham and action-thriller films buy, compared with the '
                      'average US adult, as of June 2023. Included with 1 at no extra charge.', self.reply)
        self.assertEqual(self.reply.count('$100.'), 3)
        self.assertEqual(self.out['panel_offer']['price_label'], '$300')
        self.assertIn('for $300 in all', self.reply)
        statuses = [(p['no'], p['status']) for p in self.out['panel_offer']['parts']]
        self.assertEqual(statuses, [(1, 'priced'), (2, 'priced'), (3, 'priced'), (5, 'included'), (6, 'held')])
        inc = [p for p in self.out['panel_offer']['parts'] if p['status'] == 'included'][0]
        self.assertEqual((inc['subject'], inc['included_with']), ('Jason Statham', 1))

    def test_item_four_answered_from_what_we_hold(self):
        self.assertIn('4. Which action films released in 2021-2023 brought the most new subscribers to the service they premiered '
                      'on, with those new subscribers described by demographics and what they watched before, as of June 2023. '
                      'Answered from what we hold, no charge:\nAcross the Subscriber IQ library', self.reply)
        self.assertEqual(self.out['multi_ask']['answered'], 1)

    def test_item_six_waits_for_its_names(self):
        self.assertIn('6. These titles ranked by the size of their audience that does not watch Prime Video, as of June 2023. '
                      'The three in brackets are not named yet: name the titles and it runs with the rest.', self.reply)
        held = [p for p in self.out['panel_offer']['parts'] if p['status'] == 'held'][0]
        self.assertEqual(held['placeholders'], ['[The Beekeeper comparable set]', '[a title WWSTA passed on]', '[a title that underperformed]'])
        # never run blind: five items reached the lanes, the held one did not
        self.assertEqual(len(self.calls), 5)
        self.assertFalse(any('Beekeeper' in q for q, _, _ in self.calls))

    def test_approve_tail_and_chips(self):
        self.assertIn('Approve runs 1, 2, 3, 5 and 6 for $300 in all (6 as soon as you name the titles); Cancel runs none of them.', self.reply)
        self.assertEqual(self.out['followups'], ['Approve', 'Cancel'])
        self.assertEqual(self.out['panel_offer']['kind'], 'multi')

    def test_each_part_ran_with_its_own_window_and_the_rider(self):
        for q, pc, prior in self.calls:
            self.assertIsNone(pc)
            self.assertIn('as of june 2023', q.lower())
            self.assertNotIn('[June 2023]', q)
            self.assertIn('State the people in the data behind the figures', q)
        # a later part reads with the earlier ones behind it
        self.assertEqual(len(self.calls[4][2]), 4)
        # the stored part text is the item without the rider; the rider rides the part on its own
        for p in self.out['panel_offer']['parts']:
            self.assertNotIn('State the people', p['text'])
            self.assertEqual(p['rider'], M.RIDER_INSTRUCTION)

    def test_dimension_on_the_derived_part_is_the_full_list(self):
        p1 = self.out['panel_offer']['parts'][0]
        self.assertEqual(p1['kind'], 'derived')
        self.assertEqual(p1['dimension'], 'age, gender, ethnicity, household income and region')

    def test_no_banned_words_no_dashes(self):
        self.assertEqual(BANNED_RX.findall(self.reply), [])
        self.assertNotIn('\u2014', self.reply)
        self.assertNotIn('\u2013', self.reply)
        self.assertNotIn('modeled', self.reply.lower())


class Approve(unittest.TestCase):
    def test_approve_runs_every_listed_item_in_order_each_on_its_own_subject(self):
        calls = []
        run_one = _jay_run_one(calls)
        out = M.handle(JAY, {'text': JAY}, run_one)
        pc = {'subject': out['panel_offer']['subject'], 'kind': 'multi', 'parts': out['panel_offer']['parts']}
        calls.clear()
        out2 = M.handle(JAY, {'text': JAY, 'panel_confirm': pc}, run_one)
        self.assertTrue(out2['multi_ask']['confirmed'])
        # four runs (1, 2, 3, 5) in order, each bound to its own subject; 6 is held for its names
        self.assertEqual([c[1]['subject'] for c in calls],
                         ['Jason Statham', 'John Wick, Nobody and Extraction viewers', 'Extraction viewers on Netflix', 'Jason Statham'])
        self.assertEqual([c[1]['kind'] for c in calls], ['derived', 'report', 'overlap', 'report'])
        self.assertEqual(calls[0][1]['dimension'], 'age, gender, ethnicity, household income and region')
        for q, c_pc, prior in calls:
            self.assertIn('as of june 2023', q.lower())
            self.assertIn('State the people in the data behind the figures', q)
            self.assertNotIn('rider', c_pc)
            self.assertNotIn('no', c_pc)
        # the included part carries a metered resume charge; the priced ones charge through the gate
        self.assertNotIn('resume_charge', calls[0][1])
        self.assertNotIn('resume_charge', calls[1][1])
        self.assertNotIn('resume_charge', calls[2][1])
        rc = calls[3][1]['resume_charge']
        self.assertEqual((rc['credits'], rc['metered'], rc['included_with'], rc['label']), (0, True, 1, 'metered'))
        self.assertEqual(out2['read_job_ids'], ['job-1', 'job-2', 'job-3', 'job-4'])
        # the reader's own numbers head the blocks
        for no in (1, 2, 3, 5, 6):
            self.assertIn(f"\n{no}) ", '\n' + out2['reply'])
        self.assertNotIn('\n4) ', '\n' + out2['reply'])

    def test_held_item_asks_for_its_names_and_the_names_merge_back(self):
        calls = []
        run_one = _jay_run_one(calls)
        out = M.handle(JAY, {'text': JAY}, run_one)
        pc = {'subject': out['panel_offer']['subject'], 'kind': 'multi', 'parts': out['panel_offer']['parts']}
        out2 = M.handle(JAY, {'text': JAY, 'panel_confirm': pc}, run_one)
        self.assertIn('6) As of June 2023: rank these titles', out2['reply'])
        self.assertIn('Which three titles? The three in brackets ([The Beekeeper comparable set], [a title WWSTA passed on], '
                      '[a title that underperformed]) are not named yet. Name them and I\'ll run the ranking on exactly those.',
                      out2['reply'])
        rc = out2['referent_clarify']
        self.assertEqual((rc['noun'], rc['count']), ('titles', 3))
        self.assertEqual(rc['append'], M.RIDER_INSTRUCTION)
        # the names land on the next turn, even with a delivered read in between
        hist = [{'role': 'user', 'text': JAY},
                {'role': 'agent', 'text': out['reply'], 'meta': {'panel_offer': out['panel_offer']}},
                {'role': 'user', 'text': 'Approve'},
                {'role': 'agent', 'text': out2['reply'], 'meta': {'referent_clarify': rc}},
                {'role': 'agent', 'text': 'On Jason Statham: 61.2% did not watch Prime Video ...'}]
        merged, label = R.answer_merge(hist, 'The Beekeeper, Expend4bles, Operation Fortune')
        self.assertEqual(label, 'The Beekeeper, Expend4bles and Operation Fortune')
        self.assertTrue(merged.startswith('As of June 2023: rank these titles by the size of their audience that does not watch '
                                          'Prime Video: The Beekeeper, Expend4bles and Operation Fortune.'))
        self.assertNotIn('[', merged)
        self.assertTrue(merged.endswith(M.RIDER_INSTRUCTION))
        self.assertEqual(BANNED_RX.findall(out2['reply']), [])
        self.assertNotIn('\u2014', out2['reply'])

    def test_pending_offer_store_keeps_every_part(self):
        calls = []
        out = M.handle(JAY, {'text': JAY}, _jay_run_one(calls))
        body = PO.confirm_body({'subject': 'Jason Statham', 'kind': 'multi', 'parts': out['panel_offer']['parts'], 'id': 'x1'})
        self.assertEqual(len(body['parts']), 5)
        self.assertEqual(len(M.confirm_parts(body)), 5)
        self.assertGreaterEqual(PO.MAX_PARTS, M.MAX_ITEMS)
        # a quote read back from the thread keeps its parts
        hist = [{'role': 'user', 'text': JAY, 'ts': '2026-11-04T17:38:00Z'},
                {'role': 'agent', 'text': out['reply'], 'ts': '2026-11-04T17:38:30Z',
                 'meta': {'panel_offer': out['panel_offer'], 'options': ['Approve', 'Cancel']}}]
        from datetime import datetime, timezone
        found = PO.open_in_history(hist, None, now=datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc))
        self.assertIsNotNone(found)
        self.assertEqual(len(found['parts']), 5)
        self.assertEqual(found['kind'], 'multi')


class SmallerLists(unittest.TestCase):
    def test_two_numbered_items_quoted_together(self):
        text = '1. How big is the Reba audience?\n2. What are the top five QSRs for Gilmore Girls viewers?'
        def run_one(q, pc, prior=None):
            if 'reba' in q.lower():
                return ({'raw': _offer('Reba McEntire', '$100', 'I can size Reba McEntire: US people in the window.', kind='sizing')}, 200)
            return ({'raw': _offer('Gilmore Girls viewers', '$100', 'I can rank the QSRs Gilmore Girls viewers use most.')}, 200)
        out = M.handle(text, {'text': text}, run_one)
        self.assertIn('Two priced reads here, quoted together and run together:', out['reply'])
        self.assertIn('1. The size of the Reba audience. $100.', out['reply'])
        self.assertIn('2. The top five QSRs for Gilmore Girls viewers. $100.', out['reply'])
        self.assertIn('Approve runs both for $200 in all; Cancel runs none of them.', out['reply'])
        self.assertEqual(out['panel_offer']['price_label'], '$200')
        self.assertNotIn('Each answer will state', out['reply'])

    def test_three_numbered_items_one_answered(self):
        text = ('1) How many people watched Landman on Paramount+ in 2025?\n'
                '2) Of those, what share also watched Yellowstone?\n'
                '3) What does the conversion rate mean here?')
        def run_one(q, pc, prior=None):
            if 'landman' in q.lower() and 'yellowstone' not in q.lower():
                return ({'raw': _offer('Landman on Paramount+', '$100', 'I can pull how many people in the US watched Landman on Paramount+ across one year.', kind='viewership')}, 200)
            if 'yellowstone' in q.lower():
                return ({'raw': _offer('Landman on Paramount+', '$100', 'I can pull the overlap for Landman viewers on Paramount+.', kind='overlap')}, 200)
            return ({'raw': {'success': True, 'action': 'answer', 'reply': 'The conversion rate is new signups divided by watchers.', 'followups': []}}, 200)
        out = M.handle(text, {'text': text}, run_one)
        self.assertIn('3 asks here, quoted together and run together; 1 carries a price:', out['reply'])
        self.assertIn('1. How many people watched Landman on Paramount+ in 2025. $100.', out['reply'])
        self.assertIn('2. The share of those who also watched Yellowstone. Included with 1 at no extra charge.', out['reply'])
        self.assertIn('3. What the conversion rate means here. Answered from what we hold, no charge:\nThe conversion rate is', out['reply'])
        self.assertIn('Approve runs 1 and 2 for $100 in all; Cancel runs none of them.', out['reply'])
        self.assertEqual(out['panel_offer']['price_label'], '$100')

    def test_past_the_cap_the_rest_are_named_not_dropped(self):
        text = '\n'.join(f'{i}. How big is the audience for Title {chr(64 + i)}?' for i in range(1, 14))
        def run_one(q, pc, prior=None):
            n = re.search(r'Title ([A-Z])', q).group(1)
            return ({'raw': _offer(f'Title {n}', '$100', f'I can size Title {n}: US people in the window.', kind='sizing')}, 200)
        out = M.handle(text, {'text': text}, run_one)
        self.assertEqual(out['multi_ask']['count'], M.MAX_ITEMS)
        self.assertEqual(out['multi_ask']['left'], 3)
        self.assertIn('That leaves 3 more (11 to 13); send them next and I take them the same way.', out['reply'])
        self.assertIn(f'Approve runs all {M.MAX_ITEMS} for $1,000 in all; Cancel runs none of them.', out['reply'])

    def test_one_question_and_sentence_pairs_still_single_or_split_as_before(self):
        self.assertEqual(M.numbered_items('How big is the Reba audience?'), [])
        self.assertEqual(M.split('How big is the Reba audience?'), [])
        self.assertEqual(len(M.split('How big is the Reba audience, and what are the top five QSRs for Gilmore Girls viewers?')), 2)
        # a year is not a list number; a single numbered line is not a list
        self.assertEqual(M.numbered_items('In 2021. 2023 was better. How many watched?'), [])
        self.assertEqual(M.numbered_items('1. How big is the Reba audience?'), [])
        # one ask plus a rider is the single path
        self.assertIsNone(M.parse('1. How big is the Reba audience?\n2. For each answer, show the sample size.'))


if __name__ == '__main__':
    unittest.main(verbosity=1)
