#!/usr/bin/env python3
"""The board guard: no room ever publishes the same answer on every row.

Jenna 2026-11-05, with the Good Influence room's export in hand
(Question 10, 'Overall what issues mattered to you the most?': five
options at 3.2% / 279,764 people each and 'Other' at 84.0%, lead answer
'Other'): "It cant ever have all the same answers on something in
digital twins focus group."

Held here (``migration/digital_twin_board.py``, run by
``digital_twin_room.run_question`` before anything is published):
  * a uniform five-option board is rejected and re-coded: the seats that
    answered beside the board are coded onto a board built from what
    they named, and the named lead carries the read;
  * coded free-text answers land on named options with distinct shares;
  * Other holds at most 15% of the seats and never leads, with or
    without a model (the deterministic coder names the missing option
    from the seats' own words);
  * ties break deterministically, the row with more seats stays ahead,
    people figures are distinct and never end in 0, segments stay the
    exact parts of the room;
  * yes / no boards never print equal shares (a true 50 / 50 is
    50.1 / 49.9);
  * the guard runs inside run_question before the read is published, and
    the export page reflects the corrected board.

Hermetic: a fake model answers every desk; no S3, no network.

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_board_guard.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_board as B      # noqa: E402
from migration import digital_twin_panel as P      # noqa: E402
from migration import digital_twin_room as ROOM    # noqa: E402

Q = 'overall what issues mattered to you the most?'
BOARD = [{'id': 'a', 'label': 'Cost of living: groceries, rent, utilities', 'lean': 'other'}, {'id': 'b', 'label': 'Health care costs and coverage', 'lean': 'other'},
         {'id': 'c', 'label': 'The state of democracy and voting rights', 'lean': 'other'}, {'id': 'd', 'label': 'Corruption and who the system actually works for', 'lean': 'other'},
         {'id': 'e', 'label': 'Something else I cared about more', 'lean': 'other'}]
PAID = 'Who was paying for the content I watched'
# ten seats beside the board, in their own words (the live room's shape), seat 4 on a, seat 6 on b
SAID = {1: 'who paid for the posts', 2: 'knowing who is paying for the content', 3: 'the paid tag up front', 5: 'disclosure of who funded it', 7: 'who was behind the content',
        8: 'who paid for what I saw', 9: 'paid content without a label', 10: 'who is funding the creators', 11: 'who pays before I share', 12: 'a paid tag on every post'}


def _voters():
    return {
        'meta': {'name': 'Good Influence voters', 'key': 'gi', 'subject': 'The Good Influence', 'subject_kind': 'audience', 'format': 'audience', 'size_us': 8_742_613,
                 'profile_key': 'Good_Influence_Voters_11_04_2026_19_21.csv'},
        'identity': {'base': {'kind': 'voters'}, 'read': 'Voters who engaged with organic social media content from the network\'s creators.',
                     'demographics': {
                         'gender': [{'label': 'Female', 'pct': 57.3}, {'label': 'Male', 'pct': 42.7}],
                         'age': [{'label': '18-24', 'pct': 18.1}, {'label': '25-34', 'pct': 31.4}, {'label': '35-44', 'pct': 22.2}, {'label': '45-54', 'pct': 14.1},
                                 {'label': '55-64', 'pct': 9.6}, {'label': '65+', 'pct': 4.6}],
                         'education': [{'label': 'Bachelors Degree', 'pct': 34.3}, {'label': 'Some College', 'pct': 30.2}, {'label': 'High School or Less', 'pct': 20.1},
                                       {'label': 'Graduate or Professional Degree', 'pct': 15.4}],
                         'occupation': [{'label': 'Management/Business/Professional', 'pct': 31.1}, {'label': 'Service', 'pct': 22.6}, {'label': 'Sales', 'pct': 12.1},
                                        {'label': 'Healthcare', 'pct': 16.2}, {'label': 'Education/Library', 'pct': 10.4}],
                     }},
        'traits': {'franchise_loyalty': {'value': 52.0, 'label': 'loyalty', 'read': ''}, 'second_screen': {'value': 65.0, 'label': 'second', 'read': ''},
                   'binge_vs_appointment': {'value': 48.0, 'label': 'binge', 'read': ''}, 'word_of_mouth': {'value': 61.0, 'label': 'wom', 'read': ''},
                   'trial_speed': {'value': 44.0, 'label': 'trial', 'read': ''}},
        'lifecycle': {'habit_pct': 45.0, 'discovering_pct': 15.0, 'trying_pct': 15.0, 'lapsing_pct': 15.0, 'returning_pct': 10.0},
    }


FACTS = {'subject_status': {'line': 'The midterms were November 3; both chambers changed hands.'}, 'facts': [{'fact': 'More than 124 million ballots were cast.', 'source_class': 'official', 'topic': 'subject'}],
         'not_found': [], 'implications': ['The room speaks from after the vote.']}
MEDIA = {'event': {'what': '', 'date': '', 'status': 'none', 'outcome': '', 'results': [], 'turnout_pct': None}, 'polls': [], 'coverage': [],
         'talk': [{'line': 'I want to know who is paying before I share anything', 'who': 'most of the audience', 'loudness': 'dominant', 'feeling': 'angry'}], 'numbers': [], 'local': [], 'not_found': []}

SEEN = {'coding': [], 'read': [], 'persona': [], 'mirror': []}


def _fake(coder='model'):
    """The room's desks. ``coder``: 'model' returns a coded board, 'none'
    answers nothing to the coder (the deterministic coder runs),
    'lazy' leaves too many seats on Other (the cap forces the rest)."""
    def fake(role, system, user, **kw):
        d = json.loads(user)
        if role == 'research':
            return dict(FACTS)
        if role == 'media':
            return json.loads(json.dumps(MEDIA))
        if role == 'moderator' and 'mode' in d and 'seats' in d:
            SEEN['coding'].append(d)
            if coder == 'none':
                return None
            beside = [s['seat'] for s in d['seats'] if s['picked'] == 'other']
            if coder == 'lazy':
                return {'options': d['board'][:4] + [{'id': 'f', 'label': PAID, 'lean': 'other'}], 'multi': False,
                        'coding': {str(s): {'primary': 'f', 'all': ['f']} for s in beside[:3]}, 'note': 'half done'}
            return {'options': [{'id': 'a', 'label': 'Cost of living: groceries, rent, utilities', 'lean': 'other'}, {'id': 'b', 'label': 'Health care costs and coverage', 'lean': 'other'},
                                {'id': 'c', 'label': 'The state of democracy and voting rights', 'lean': 'other'}, {'id': 'f', 'label': PAID, 'lean': 'other'}],
                    'multi': False, 'coding': {str(s): {'primary': 'f', 'all': ['f']} for s in beside}, 'note': 'The room named who paid for the content; the board lacked it.'}
        if role == 'moderator' and 'supplied_options' in d:
            return {'options': [dict(o) for o in BOARD], 'framing': 'Thinking back over the run-up to November 3, what mattered most when you decided your vote?',
                    'listen_for': ['grocery, rent, premium', 'whether a creator connected the issue']}
        if role == 'moderator':
            SEEN['read'].append(d)
            lead = max(d['options'], key=lambda o: float(o['published_pct']))
            return {'read': f"{lead['published_in_words'].capitalize()} of these voters land on {lead['label'].lower()}, and the seats said why in their own words. "
                            "The loudest line was about the paid tag. That is the room.",
                    'chat': f"{lead['published_in_words'].capitalize()} land on {lead['label'].lower()}. The paid tag carried it.",
                    'voices': [1, 4, 6], 'probes_next': ['Why?'], 'facts_used': ['More than 124 million ballots were cast.']}
        if role == 'persona' and 'your_answer' in d:
            SEEN['mirror'].append(d)
            return {'line': f"Honestly that is me: {d['your_answer']['label']}.", 'confidence': 4, 'reason': 'the receipts'}
        if role == 'persona':
            SEEN['persona'].append(d)
            seat = int(d['you'].split('seat ')[1].split(',')[0]) if 'seat ' in d['you'] else len(SEEN['persona'])
            if seat == 4:
                return {'option': 'a', 'line': 'Groceries and rent, that is what I voted on.', 'confidence': 4, 'reason': 'the checkout line'}
            if seat == 6:
                return {'option': 'b', 'line': 'My premiums went up again, that was the whole thing for me.', 'confidence': 4, 'reason': 'the premium'}
            return {'option': 'other', 'other_label': SAID.get(seat, 'who paid for it'), 'line': 'What stuck with me was finding out who was paying for the posts I had been sharing all cycle.',
                    'confidence': 4, 'reason': 'the paid tag'}
        return None
    return fake


def _ask(coder='model', q=Q):
    twin = _voters()
    sess = P.open_session([twin], in_room=12, title='Campaign Recap', session_id='room-board-1')
    for k in SEEN:
        SEEN[k].clear()
    ROOM.clear_room_caches()   # each test's room starts from nothing (the day caches would serve another test's board or plan)
    pubs = []
    real = ROOM._publish

    def spy(ctx, stage, status='running', extra=None, payload_too=False):
        pubs.append((stage, [round(float(o.get('pct') or 0.0), 1) for o in ctx['rec'].get('options') or []], [o.get('label') for o in ctx['rec'].get('options') or []]))
        return real(ctx, stage, status, extra, payload_too)

    ROOM._publish = spy
    try:
        rec = ROOM.ask_room(sess, {twin['meta']['key']: twin}, q, s3=None, twin_key='gi', run_async=False, model_fn=_fake(coder))
    finally:
        ROOM._publish = real
    return sess, rec, pubs


def _distinct(rows):
    pcts = [round(float(r['pct']), 1) for r in rows]
    people = [int(r['people']) for r in rows]
    return len(set(pcts)) == len(pcts) and len(set(people)) == len(people)


class Signatures(unittest.TestCase):
    def test_the_live_board_is_read_as_uncoded_and_other_led(self):
        rows = [dict(o, pct=3.2, people=279_764) for o in BOARD] + [{'id': 'other', 'label': 'Other', 'lean': 'other', 'pct': 84.0, 'people': 7_343_793}]
        self.assertTrue(B.is_uncoded(rows))
        self.assertTrue(B.other_leads(rows))
        self.assertIn('5 options share a share', B.board_flags(rows))
        self.assertIn('uniform named board', B.board_flags(rows))
        self.assertTrue(any(f.startswith('Other leads') for f in B.board_flags(rows)))
        self.assertTrue(B.is_other_label('Something else I cared about more'))
        self.assertTrue(B.is_other_label('None of these'))
        self.assertFalse(B.is_other_label('Not sure yet'))
        self.assertFalse(B.is_other_label('I do not watch Y&R, or nothing stood out'), 'a named stance is not a catch-all')
        self.assertEqual(B.board_flags([{'label': 'Yes', 'pct': 58.3, 'people': 1_001}, {'label': 'No', 'pct': 41.7, 'people': 717}]), [])


class Distinct(unittest.TestCase):
    def _rows(self):
        return [dict(o, pct=3.2, people=279_764, seats=([4] if o['id'] == 'a' else [6] if o['id'] == 'b' else []), seats_n=(1 if o['id'] in ('a', 'b') else 0)) for o in BOARD] + \
               [{'id': 'other', 'label': 'Other', 'lean': 'other', 'pct': 84.0, 'people': 7_343_793, 'seats': [1, 2, 3, 5, 7, 8, 9, 10, 11, 12], 'seats_n': 10}]

    def test_ties_break_deterministically_and_keep_the_seats_order(self):
        rows = self._rows()
        rep = B.enforce_distinct(rows, None, 8_742_613, 12, 'seed-1', priority=B.seat_priority(rows))
        self.assertTrue(rep['changed'])
        self.assertTrue(_distinct(rows), rows)
        self.assertAlmostEqual(sum(float(r['pct']) for r in rows), 100.0, places=6)
        self.assertEqual(sum(int(r['people']) for r in rows), 8_742_613)
        by = {r['id']: r for r in rows}
        self.assertGreater(by['a']['pct'], by['c']['pct'], 'the row holding a seat stays ahead of the rows holding none')
        self.assertGreater(by['b']['pct'], by['d']['pct'])
        self.assertTrue(all(int(r['people']) % 10 != 0 for r in rows), 'no people figure ends in 0')
        self.assertTrue(all(float(r['pct']) >= 0.1 for r in rows))
        again = self._rows()
        B.enforce_distinct(again, None, 8_742_613, 12, 'seed-1', priority=B.seat_priority(again))
        self.assertEqual([(r['pct'], r['people']) for r in again], [(r['pct'], r['people']) for r in rows], 'the same seed lands the same board')
        other = self._rows()
        B.enforce_distinct(other, None, 8_742_613, 12, 'seed-2', priority=B.seat_priority(other))
        self.assertTrue(_distinct(other))

    def test_a_two_way_tail_tie_and_the_segments_follow(self):
        rows = [{'id': 'a', 'label': 'Yes, voted early', 'lean': 'yes', 'pct': 48.9, 'people': 4_275_138, 'seats_n': 6}, {'id': 'b', 'label': 'Yes, in person', 'lean': 'yes', 'pct': 47.7, 'people': 4_170_226, 'seats_n': 6},
                {'id': 'c', 'label': 'No, ran out of time', 'lean': 'no', 'pct': 1.6, 'people': 139_882, 'seats_n': 0}, {'id': 'd', 'label': 'No, sat this one out', 'lean': 'no', 'pct': 1.6, 'people': 139_882, 'seats_n': 0},
                {'id': 'e', 'label': 'Could not vote', 'lean': 'no', 'pct': 0.2, 'people': 17_485, 'seats_n': 0}]
        segs = [{'segment': 'X', 'people': 5_245_568, 'distribution': [{'label': r['label'], 'pct': r['pct'], 'people': int(r['people'] * 0.6)} for r in rows]},
                {'segment': 'Y', 'people': 3_497_045, 'distribution': [{'label': r['label'], 'pct': r['pct'], 'people': r['people'] - int(r['people'] * 0.6)} for r in rows]}]
        B.enforce_distinct(rows, segs, 8_742_613, 12, 'seed-3')
        self.assertTrue(_distinct(rows), rows)
        self.assertEqual(sum(int(r['people']) for r in rows), 8_742_613)
        for i, r in enumerate(rows):
            self.assertEqual(int(r['people']), sum(int(s['distribution'][i]['people']) for s in segs), 'the room is the exact sum of its segments')
        for s in segs:
            self.assertEqual(sum(int(x['people']) for x in s['distribution']), int(s['people']), 'each segment keeps its own total')
            self.assertAlmostEqual(sum(float(x['pct']) for x in s['distribution']), 100.0, places=6)

    def test_a_yes_no_board_never_prints_equal_shares(self):
        rows = [{'id': 'a', 'label': 'Yes', 'lean': 'yes', 'pct': 50.0, 'people': 500_000, 'seats_n': 6}, {'id': 'b', 'label': 'No', 'lean': 'no', 'pct': 50.0, 'people': 500_000, 'seats_n': 6}]
        B.enforce_distinct(rows, None, 1_000_000, 12, 'seed-4')
        self.assertNotEqual(rows[0]['pct'], rows[1]['pct'])
        self.assertAlmostEqual(abs(rows[0]['pct'] - rows[1]['pct']), 0.2, places=6, msg='a genuine 50 / 50 is a tenth apart on each side')
        self.assertEqual(sum(int(r['people']) for r in rows), 1_000_000)
        self.assertNotEqual(rows[0]['people'], rows[1]['people'])
        small = [{'id': 'a', 'label': 'Yes', 'lean': 'yes', 'pct': 50.0, 'people': 617, 'seats_n': 6}, {'id': 'b', 'label': 'No', 'lean': 'no', 'pct': 50.0, 'people': 617, 'seats_n': 6}]
        B.enforce_distinct(small, None, 1_234, 12, 'seed-5')
        self.assertNotEqual(small[0]['pct'], small[1]['pct'])

    def test_a_board_already_distinct_is_left_alone(self):
        rows = [{'id': 'a', 'label': 'Yes', 'lean': 'yes', 'pct': 58.3, 'people': 583_001, 'seats_n': 7}, {'id': 'b', 'label': 'No', 'lean': 'no', 'pct': 41.7, 'people': 416_999, 'seats_n': 5}]
        rep = B.enforce_distinct(rows, None, 1_000_000, 12, 'seed-6')
        self.assertFalse(rep['changed'])
        self.assertEqual([(r['pct'], r['people']) for r in rows], [(58.3, 583_001), (41.7, 416_999)])


class OtherNeverLeads(unittest.TestCase):
    def test_the_guard_demotes_an_other_lead_without_a_coder(self):
        rows = [dict(o, pct=3.2, people=279_764, seats_n=(1 if o['id'] in ('a', 'b') else 0)) for o in BOARD] + \
               [{'id': 'other', 'label': 'Other', 'lean': 'other', 'pct': 84.0, 'people': 7_343_793, 'seats_n': 10}]
        answers = [{'seat': s, 'option': 'other', 'option_label': SAID[s], 'line': 'the paid tag', 'reason': ''} for s in SAID] + \
                  [{'seat': 4, 'option': 'a', 'option_label': BOARD[0]['label'], 'line': 'groceries and rent', 'reason': ''}, {'seat': 6, 'option': 'b', 'option_label': BOARD[1]['label'], 'line': 'premiums', 'reason': ''}]
        rep = B.guard_board(rows, None, 8_742_613, 12, 'seed-7', priority=B.seat_priority(rows), answers=answers)
        self.assertTrue(rep['other_led'] and rep['uncoded'] and rep['changed'])
        self.assertFalse(B.other_leads(rows))
        self.assertFalse(B.is_other_label(rep['lead']))
        self.assertLessEqual(B.other_share(rows), 15.0)
        self.assertTrue(_distinct(rows), rows)
        self.assertEqual(sum(int(r['people']) for r in rows), 8_742_613)
        self.assertEqual(rep['before'][0][1], 3.2, 'the trail keeps the board as it came in')


class Coder(unittest.TestCase):
    def _answers(self):
        out = []
        for s in range(1, 13):
            if s == 4:
                out.append({'seat': s, 'name': f'n{s}', 'option': 'a', 'option_label': BOARD[0]['label'], 'line': 'Groceries and rent.', 'confidence': 4, 'reason': 'the checkout line'})
            elif s == 6:
                out.append({'seat': s, 'name': f'n{s}', 'option': 'b', 'option_label': BOARD[1]['label'], 'line': 'My premiums.', 'confidence': 4, 'reason': 'the premium'})
            else:
                out.append({'seat': s, 'name': f'n{s}', 'option': 'other', 'option_label': SAID[s], 'line': 'Finding out who was paying for the posts I shared.', 'confidence': 4, 'reason': 'the paid tag'})
        return out

    def test_needs_coding(self):
        self.assertEqual(B.needs_coding('open', BOARD, self._answers()), 'open')
        self.assertEqual(B.needs_coding('yes_no', BOARD, self._answers()), 'cap')
        self.assertEqual(B.needs_coding('rating', BOARD, self._answers(), fixed=True), 'fixed')
        on_board = [dict(a, option='a', option_label=BOARD[0]['label']) for a in self._answers()]
        self.assertIsNone(B.needs_coding('open', BOARD, on_board), 'a board every seat used stands')
        one = [dict(a, option='a', option_label=BOARD[0]['label']) if a['seat'] != 1 else a for a in self._answers()]
        self.assertIsNone(B.needs_coding('yes_no', BOARD, one), 'one of twelve beside a coded board is within the cap')
        self.assertFalse(B.multi_select_question(Q), "'the most' asks for one")
        self.assertTrue(B.multi_select_question('What issues are most important to you?'))
        self.assertTrue(B.multi_select_question('which brands do you buy?'))

    def test_the_deterministic_coder_names_the_missing_option_from_the_seats_words(self):
        answers = self._answers()
        out = B.code_board(None, Q, 'open', '', BOARD, answers, {}, 'seed-8')
        self.assertTrue(out['changed'] and out['fallback'])
        labels = [o['label'] for o in out['options']]
        self.assertTrue(any('paid' in l.lower() or 'paying' in l.lower() or 'funded' in l.lower() or 'funding' in l.lower() for l in labels), labels)
        self.assertLessEqual(len(out['options']), B.MAX_CODED_OPTIONS)
        ids = {o['id'] for o in out['options']}
        beside = [a for a in answers if a['option'] not in ids]
        self.assertLessEqual(len(beside) / 12.0, B.OTHER_CAP)
        self.assertEqual(answers[3]['option'], 'a', 'a seat already on a named option keeps it')
        self.assertEqual(answers[5]['option'], 'b')
        self.assertTrue(all(a.get('said_it_was') for a in answers if a.get('coded')), 'the label a seat gave beside the board rides on the trail')
        self.assertEqual(out['trail']['mode'], 'open')

    def test_the_model_coder_is_validated_and_the_cap_is_forced(self):
        answers = self._answers()
        models = ROOM.Models(model_fn=_fake('lazy'))
        out = B.code_board(models, Q, 'open', '', BOARD, answers, {}, 'seed-9')
        ids = {o['id'] for o in out['options']}
        self.assertIn(PAID, [o['label'] for o in out['options']])
        beside = [a for a in answers if a['option'] not in ids]
        self.assertLessEqual(len(beside) / 12.0, B.OTHER_CAP, 'a coder that leaves too many on Other is finished by the deterministic coder')
        self.assertTrue(out['trail']['forced_cap'])
        self.assertFalse(out['fallback'])

    def test_a_fixed_board_is_never_widened(self):
        stars = [{'id': chr(97 + i), 'label': f"{s} star{'s' if s != 1 else ''}", 'lean': ('yes' if s >= 4 else 'maybe' if s == 3 else 'no')} for i, s in enumerate(range(1, 6))]
        answers = [{'seat': s, 'name': f'n{s}', 'option': ('d' if s % 3 else 'other'), 'option_label': ('4 stars' if s % 3 else 'Changes both, not just her rating'),
                    'line': ('Four stars, it lifts the whole show.' if s % 3 else 'It would change how I rate the whole show, not just her.'), 'confidence': 3, 'reason': ''} for s in range(1, 17)]
        out = B.code_board(None, 'would that change how you rate the show?', 'rating', '', stars, answers, {}, 'seed-10', fixed=True)
        self.assertEqual([o['label'] for o in out['options']], [o['label'] for o in stars], 'the star scale stands')
        self.assertTrue(all(a['option'] in {o['id'] for o in stars} for a in answers), 'every seat beside the scale is coded onto a star')
        self.assertEqual(out['trail']['mode'], 'fixed')


class InTheRoom(unittest.TestCase):
    def test_a_uniform_board_is_recoded_and_the_named_lead_carries_the_read(self):
        sess, rec, pubs = _ask('model')
        self.assertEqual(rec['stage'], 'done')
        self.assertEqual(SEEN['coding'] and SEEN['coding'][0]['mode'], 'open', 'the coder ran on an open question the seats answered beside the board')
        labels = [o['label'] for o in rec['options']]
        self.assertIn(PAID, labels, 'the board carries what the room named')
        self.assertTrue(_distinct(rec['options']), [(o['label'], o['pct'], o['people']) for o in rec['options']])
        self.assertTrue(_distinct(rec['distribution']))
        lead = max(rec['options'], key=lambda o: float(o['pct']))
        self.assertEqual(lead['label'], PAID)
        self.assertFalse(B.other_leads(rec['options']))
        self.assertLessEqual(B.other_share(rec['options']), 15.0)
        self.assertFalse(B.is_uncoded(rec['options']))
        self.assertEqual(sum(int(o['people']) for o in rec['options']), 8_742_613)
        self.assertEqual(sum(t['seats'] for t in rec['tally']), 12)
        self.assertGreaterEqual(lead['seats_n'], 8, 'the seats mirror the guarded count')
        self.assertIn(PAID.lower()[:20], rec['read'].lower(), rec['read'])
        self.assertIn(PAID.lower()[:20], rec['headline'].lower(), rec['headline'])
        self.assertNotIn('"Other"', rec['headline'])
        self.assertEqual(rec['trail']['coding']['mode'], 'open')
        self.assertIn('board_guard', rec['trail'])
        self.assertEqual(rec['trail']['board_guard']['version'], B.BOARD_VERSION)
        self.assertEqual([f for f in rec['trail']['fallbacks'] if f not in ('read',)], [])
        # the guard ran before the read was published: every publish from the read on carried a distinct board
        reading = [p for p in pubs if p[0] in ('moderator is reading', 'done')]
        self.assertTrue(reading)
        for stage, pcts, labs in reading:
            self.assertEqual(len(set(pcts)), len(pcts), (stage, labs, pcts))
        self.assertTrue(all(a.get('option') in {o['id'] for o in rec['options']} for a in rec['answers']), 'every seat sits on the published board')
        self.assertEqual(ROOM.has_banned(rec['read']), [])

    def test_without_a_coder_answer_the_room_still_never_publishes_alike_rows(self):
        sess, rec, pubs = _ask('none')
        self.assertEqual(rec['stage'], 'done')
        self.assertTrue(_distinct(rec['options']), [(o['label'], o['pct'], o['people']) for o in rec['options']])
        self.assertFalse(B.other_leads(rec['options']))
        self.assertFalse(B.is_other_label(max(rec['options'], key=lambda o: float(o['pct']))['label']))
        self.assertLessEqual(B.other_share(rec['options']), 15.0)
        self.assertIn('coding', rec['trail']['fallbacks'])

    def test_the_export_page_reflects_the_corrected_board(self):
        from migration import digital_twin_exports as X
        sess, rec, pubs = _ask('model')
        line = X.question_lead_line(rec['distribution'])
        self.assertTrue(line.startswith(f"Lead answer: {PAID}"), line)
        pdf = X.build_panel_pdf({'meta': {'name': 'Good Influence voters', 'subject': 'The Good Influence'}}, sess)
        import pypdf
        text = ' '.join(' '.join((p.extract_text() or '').split()) for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
        self.assertIn(f"Lead answer: {PAID}", text)
        pcts = [f"{o['pct']}%" for o in rec['options']]
        self.assertTrue(all(p in text for p in pcts), 'every row of the corrected board is on the page')
        self.assertEqual(max(Counter(pcts).values()), 1)
        self.assertNotIn('Lead answer: Other', text)

    def test_a_question_that_asks_for_several_things_carries_the_mentions(self):
        sess, rec, pubs = _ask('model', q='What issues mattered to you?')
        self.assertEqual(rec['stage'], 'done')
        self.assertTrue(_distinct(rec['options']))
        if rec.get('multi_select'):
            self.assertTrue(rec.get('mentions'))
            self.assertEqual(len({m['pct'] for m in rec['mentions']}), len(rec['mentions']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
