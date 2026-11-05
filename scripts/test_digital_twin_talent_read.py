#!/usr/bin/env python3
"""What the room said about the people named on the test (Jenna, 2026-11-04).

"on the report and in that section it should also list what they said/
thought about the talent that was called out during the data entry prior
to starting. like since I put those three talent it should say that they
felt jennifer was under used, shes the reason they would come back, scott
was gratuitous but they liked the relationship, etc"

The setup names the people (session['cast']); the read gathers the
seats' remarks with clocks, the per-seat ratings, the room's answers on
every question that bears on a person (by name, by character, or by a
role phrase the questions bind to them: 'the woman running the team' is
Jennifer Beals) and the dial in their scenes, and writes two or three
sentences per person with one seat's own line. It agrees with the
character metrics on the card: the person who leads 'follow into a
second episode' is never forgettable, the person at the bottom is never
the draw. The card, the report (deck and PDF) and the CSV carry it.

Hermetic: REGRESSION_TEST_MODE=1; a fake model where a model is wanted,
the deterministic read where it is off.

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_talent_read.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twin_report as R  # noqa: E402
from migration import digital_twin_room as ROOM  # noqa: E402
from migration import digital_twin_talent_read as TR  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_pilot import _beals  # noqa: E402

BANNED = ('synth', 'synthetic', 'modeled', 'estimated', 'research', 'pipeline', 'claude', 'hostmap', 'persona', 'panel', 'the actor', 'the actress', '\u2014', '\u2013', ' door')
CAST = ['LL Cool J', 'Scott Caan', 'Jennifer Beals as Lena']

REMARKS = [   # (seat, clock seconds, type, line); the woman running the team / Lena is Jennifer Beals, named by role or by character
    (13, 24.5, 'lean-in', 'LL Cool J alone, head down, no score under it. I moved up in my seat.'),
    (1, 225.0, 'love', "LL Cool J slamming him into that wall, I'd rewind that ten times."),
    (4, 2164.0, 'thought', "He looks like he's enjoying this way too much. Classic Scott Caan."),
    (11, 2164.0, 'thought', 'Scott Caan doing his Danny thing, confident, a little reckless, likeable.'),
    (10, 686.0, 'laugh', 'Scott Caan laughing at his own line in the car. Got a chuckle out of me.'),
    (9, 518.0, 'lean-in', 'The woman running the team has that look, like she knows something. I want more of her.'),
    (5, 1416.0, 'love', 'Lena with the glasses on, arms crossed, owning the room. Give me more of her.'),
    (7, 1511.0, 'thought', 'Lena and LL Cool J in the corridor, both tense. She held that scene.'),
]


def _question_more_of_her(roster, seats_yes):
    opts = [{'id': 'a', 'label': 'More of the woman running the team brings me back', 'lean': 'yes', 'pct': 58.7, 'people': 13_118_898, 'seats': seats_yes, 'seats_n': len(seats_yes), 'tally': f"{len(seats_yes)} of {len(roster)}", 'room_pct': 62.5},
            {'id': 'b', 'label': 'More quiet partner moments like the hand on the shoulder', 'lean': 'yes', 'pct': 27.9, 'people': 6_238_275, 'seats': [2, 6, 11, 13, 14], 'seats_n': 5, 'tally': f"5 of {len(roster)}", 'room_pct': 31.2},
            {'id': 'c', 'label': 'Only if the next case has a real hook', 'lean': 'maybe', 'pct': 13.4, 'people': 2_995_117, 'seats': [4], 'seats_n': 1, 'tally': f"1 of {len(roster)}", 'room_pct': 6.2}]
    answers = []
    for r in roster:
        sno = int(r['seat'])
        pick = 'a' if sno in seats_yes else ('c' if sno == 4 else 'b')
        line = {'a': 'She is the reason I press play on episode two, I wanted more of her running things, the pilot built toward her and then handed the ending to the two guys on the boat.',
                'b': 'Give me another quiet moment between those two and I am in.', 'c': 'Only if the case hooks me next time.'}[pick]
        answers.append({'seat': sno, 'name': r['name'], 'option': pick, 'option_label': next(o['label'] for o in opts if o['id'] == pick), 'line': line, 'confidence': 4, 'reason': 'as said', 'fallback': False})
    voices = [{'seat': a['seat'], 'name': a['name'], 'age': 50, 'city': 'New York', 'segment': 'Women', 'line': a['line']} for a in answers[:3]]
    q = {'id': 'q2', 'q': 'what would make you watch another episode? if you knew there was more jennifer beals?', 'kind': 'open', 'stage': 'done',
         'framing': 'At 43:13 the episode has just ended on black. What would get you to press play on episode two, and does knowing you would see more of the woman running the team change that?',
         'options': opts, 'option_labels': [o['label'] for o in opts], 'distribution': [{'label': o['label'], 'pct': o['pct'], 'people': o['people']} for o in opts],
         'tally': [{'label': o['label'], 'seats': o['seats_n'], 'of': len(roster), 'room_pct': o['room_pct']} for o in opts],
         'read': 'Ten of the sixteen named the woman running the team as what brings them back.', 'read_short': 'Ten of sixteen come back for her.',
         'answers': answers, 'voices': voices, 'facts_used': [], 'talk': [], 'seats_total': len(roster), 'asked_at': '2026-11-04T22:10:00Z', 'finished_at': '2026-11-04T22:11:00Z'}
    PI.stamp_question(q, 2593)
    return q


def _question_pair(roster):
    opts = [{'id': 'a', 'label': "The two of them together are why I'd keep watching", 'lean': 'yes', 'pct': 32.3, 'people': 7_210_004, 'seats': [1, 2, 11, 13, 14], 'seats_n': 5, 'tally': f"5 of {len(roster)}", 'room_pct': 31.2},
            {'id': 'b', 'label': 'The silent shoulder moment worked, need to hear them talk more', 'lean': 'maybe', 'pct': 31.1, 'people': 6_949_113, 'seats': [3, 5, 6, 7, 8], 'seats_n': 5, 'tally': f"5 of {len(roster)}", 'room_pct': 31.2},
            {'id': 'c', 'label': 'Scott Caan was laying it on, but I liked the partnership', 'lean': 'maybe', 'pct': 36.6, 'people': 8_176_211, 'seats': [4, 9, 10, 12, 15, 16], 'seats_n': 6, 'tally': f"6 of {len(roster)}", 'room_pct': 37.5}]
    answers = []
    for r in roster:
        sno = int(r['seat'])
        pick = 'a' if sno in opts[0]['seats'] else ('b' if sno in opts[1]['seats'] else 'c')
        line = {'a': 'Those two are the reason I would open the next episode, the shoulder moment told me everything.',
                'b': 'The hand on the shoulder worked, I just need to hear them actually talk.',
                'c': 'Scott Caan was laying it on a little thick for me, but I liked the partnership and the banter carried the middle.'}[pick]
        answers.append({'seat': sno, 'name': r['name'], 'option': pick, 'option_label': next(o['label'] for o in opts if o['id'] == pick), 'line': line, 'confidence': 3, 'reason': 'as said', 'fallback': False})
    q = {'id': 'q1', 'q': 'how did you feel about ll cool j and scott caan?', 'kind': 'open', 'stage': 'done', 'framing': 'Thinking back across the whole episode, how did the two agents land for you as a pair?',
         'options': opts, 'option_labels': [o['label'] for o in opts], 'distribution': [{'label': o['label'], 'pct': o['pct'], 'people': o['people']} for o in opts],
         'tally': [{'label': o['label'], 'seats': o['seats_n'], 'of': len(roster), 'room_pct': o['room_pct']} for o in opts],
         'read': 'The room split on the pair.', 'read_short': 'The room split on the pair.', 'answers': answers, 'voices': [], 'facts_used': [], 'talk': [], 'seats_total': len(roster),
         'asked_at': '2026-11-04T22:05:00Z', 'finished_at': '2026-11-04T22:06:00Z'}
    PI.stamp_question(q, 2593)
    return q


def _metrics(sess, beals_leads: bool):
    """The character rows on the card as the reconcile would leave them:
    Beals leading 'follow into a second episode' (the room said she is
    the draw) or at the bottom (the inferred scores alone)."""
    rows = [{'name': 'LL Cool J', 'role': 'lead', 'excellent_pct': 55.3, 'norm': 50.7, 'vs_norm_pp': 4.6, 'screen_clock': '6:50', 'time_vs_like': 'carries the time', 'share_pct': 46.4, 'likability': 63.8},
            {'name': 'Scott Caan', 'role': 'second lead', 'excellent_pct': 47.5, 'norm': 45.2, 'vs_norm_pp': 2.3, 'screen_clock': '6:25', 'time_vs_like': 'carries the time', 'share_pct': 43.5, 'likability': 61.4},
            {'name': 'Jennifer Beals', 'role': 'supporting', 'excellent_pct': 54.1 if beals_leads else 25.2, 'norm': 38.4, 'vs_norm_pp': 15.7 if beals_leads else -13.2, 'screen_clock': '1:30', 'time_vs_like': 'earns more time', 'share_pct': 10.1, 'likability': 54.9}]
    follow = ([{'name': 'Jennifer Beals', 'pct': 48.6, 'people': 10_871_002}, {'name': 'LL Cool J', 'pct': 33.1, 'people': 7_404_113}, {'name': 'Scott Caan', 'pct': 18.3, 'people': 4_093_337}] if beals_leads
              else [{'name': 'LL Cool J', 'pct': 53.3, 'people': 11_924_523}, {'name': 'Scott Caan', 'pct': 36.9, 'people': 8_242_111}, {'name': 'Jennifer Beals', 'pct': 9.8, 'people': 2_181_006}])
    roster = sess['group']['roster']
    by_seat = [{'seat': int(r['seat']), 'name': r['name'], 'ratings': {'LL Cool J': 'a', 'Scott Caan': 'a' if int(r['seat']) % 3 else 'c', 'Jennifer Beals': 'a' if (beals_leads or int(r['seat']) % 2) else 'c'}} for r in roster]
    sess.setdefault('diagnostics', {})['characters'] = {'characters': rows, 'relationship': {'pair': ['LL Cool J', 'Scott Caan'], 'scenes_together': 5, 'excellent_pct': 52.8}, 'follow': follow,
                                                         'read': f"LL Cool J rates 55.3% excellent against a lead norm of 51%. Asked who they would follow into a second episode, {follow[0]['pct']}% say {follow[0]['name']}.", 'by_seat': by_seat}


def _session(beals_leads=True, with_questions=True):
    twin = _beals()
    ROOM.set_model_fn(None)
    sess = PI.open_session([twin], title='NCIS: New York Pilot', in_room=16)
    sess['cast'] = list(CAST)
    PI.prepare_watch(sess, {'jb': twin}, duration_s=2600, title='NCIS: New York Pilot')
    PI.mark_play_paid(sess)
    PI.stop_session(sess)
    roster = sess['group']['roster']
    names = {int(r['seat']): r['name'] for r in roster}
    sess['events'] = [e for e in (sess.get('events') or []) if not re.search(r"cool j|caan|beals|lena", str(e.get('line') or ''), re.I)]
    thoughts = {}
    for seat, t, kind, line in REMARKS:
        if kind == 'thought':
            thoughts.setdefault(str(seat), []).append([t, line])
        else:
            sess['events'].append({'t': t, 'type': kind, 'seat': seat, 'name': names.get(seat, str(seat)), 'age': 50, 'city': 'New York', 'line': line, 'live': True})
    sess['events'].sort(key=lambda e: float(e.get('t') or 0))
    sess.setdefault('live', {})['thoughts'] = thoughts
    # the scenes carry Jennifer Beals' face at 1416 and 1511 so the dial in her scenes reads
    for sc in sess.get('scenes') or []:
        if 1400 <= float(sc.get('t_start') or 0) <= 1520:
            sc['caption'] = 'Jennifer Beals stands in a dim room, arms crossed, glasses on.'
    _metrics(sess, beals_leads)
    sess['questions'] = [_question_pair(roster), _question_more_of_her(roster, [1, 3, 5, 7, 8, 9, 10, 12, 15, 16])] if with_questions else []
    return twin, sess


def _blob(x) -> str:
    return json.dumps(x, default=str).lower()


def _pages(twin, sess):
    """The report's page list; the chart is skipped where the report
    offers the switch (an older report draws it; either way the talent
    page is the same)."""
    try:
        return R.report_pages({'meta': twin['meta']}, sess, with_chart=False)
    except TypeError:
        return R.report_pages({'meta': twin['meta']}, sess)


class NamedPeople(unittest.TestCase):
    def test_the_setup_names_the_people_in_order(self):
        twin, sess = _session()
        people = TR.named_people(sess)
        self.assertEqual([p['name'] for p in people], ['LL Cool J', 'Scott Caan', 'Jennifer Beals'])
        self.assertEqual(people[2]['character'], 'Lena')
        self.assertEqual(TR.named_people({'cast': []}), [])

    def test_role_phrases_bind_to_the_person_the_question_named(self):
        twin, sess = _session()
        phrases = TR.role_phrases(sess, TR.named_people(sess))
        self.assertIn('the woman running the team', phrases['Jennifer Beals'])
        self.assertIn('the woman', phrases['Jennifer Beals'])
        self.assertEqual(phrases['LL Cool J'], [])
        self.assertEqual(phrases['Scott Caan'], [])

    def test_evidence_resolves_role_phrases_and_character_names(self):
        twin, sess = _session()
        ev = {e['name']: e for e in TR.gather_evidence(sess)}
        beals = ev['Jennifer Beals']
        lines = ' | '.join(r['line'] for r in beals['remarks'])
        self.assertIn('The woman running the team has that look', lines, 'the role phrase resolves to her')
        self.assertIn('Lena with the glasses on', lines, 'the character name resolves to her')
        self.assertIn('Lena and LL Cool J in the corridor', lines)
        self.assertEqual([q['id'] for q in beals['questions']], ['q2'])
        self.assertEqual(len(beals['questions'][0]['answers']), 16, 'a question that named her carries every seat')
        self.assertEqual(beals['metrics']['follow_rank'], 1)
        self.assertTrue(beals['metrics']['follow_leads'])
        ll = ev['LL Cool J']
        self.assertTrue(all(not re.search(r"\bi'll\b", r['line'], re.I) or re.search(r"cool j", r['line'], re.I) for r in ll['remarks']), "I'll is not LL")
        self.assertEqual([q['id'] for q in ll['questions']], ['q1'])
        self.assertTrue(ll['metrics']['in_relationship'])
        self.assertEqual(ll['ratings']['seats'], 16)
        self.assertEqual(beals['dial'].get('scenes', 0) >= 1, True, beals['dial'])


class DeterministicRead(unittest.TestCase):
    def test_fallback_says_underused_and_the_draw_with_a_real_quote(self):
        twin, sess = _session(beals_leads=True)
        tr = TR.attach_talent_read(sess, twin, reason='test')
        self.assertEqual(tr['source'], 'deterministic')
        self.assertEqual([p['name'] for p in tr['people']], ['LL Cool J', 'Scott Caan', 'Jennifer Beals'])
        beals = tr['people'][2]
        said = beals['said']
        self.assertIn('wanted more of Jennifer Beals', said, said)
        self.assertIn('leads on who the room would follow into a second episode', said, said)
        self.assertIn('More of the woman running the team brings me back', said, said)
        self.assertIn('Jennifer Beals?"', said, 'the question reads back with her name as the setup spelled it')
        self.assertLessEqual(len(TR._sentences(said)), 3, said)
        # the quote is a line a seat actually said, from the evidence
        pool = [l for _s, _t, _k, l in REMARKS] + [a['line'] for q in sess['questions'] for a in q['answers']]
        self.assertTrue(any(p.startswith(beals['quote']['line'].rstrip('.')) or beals['quote']['line'] in p for p in pool), beals['quote'])
        self.assertIn(beals['quote']['seat'], [int(r['seat']) for r in sess['group']['roster']])
        self.assertTrue(beals['quote']['clock'])
        self.assertTrue(beals['clocks'])
        self.assertEqual(TR.breaks_constraints(said, next(e['metrics'] for e in TR.gather_evidence(sess) if e['name'] == 'Jennifer Beals')), [])
        for p in tr['people']:
            low = p['said'].lower() + ' ' + (p['quote'] or {}).get('line', '').lower()
            for w in BANNED:
                self.assertNotIn(w, low, (p['name'], w))
        # Scott: the partnership and the showing off both surface
        scott = tr['people'][1]['said']
        self.assertIn('partnership with LL Cool J', scott, scott)
        self.assertTrue('showing off' in scott or 'a little much' in scott, scott)
        # the rows on the card carry it
        rows = sess['diagnostics']['characters']['characters']
        self.assertEqual(rows[2]['room_said']['said'], said)
        self.assertEqual(rows[2]['room_said']['quote']['line'], beals['quote']['line'])

    def test_fallback_at_the_bottom_never_crowns_her(self):
        twin, sess = _session(beals_leads=False)
        tr = TR.attach_talent_read(sess, twin)
        said = tr['people'][2]['said']
        self.assertIn('sits third of 3', said, said)
        m = next(e['metrics'] for e in TR.gather_evidence(sess) if e['name'] == 'Jennifer Beals')
        self.assertTrue(m['follow_bottom'])
        self.assertEqual(TR.breaks_constraints(said, m), [])

    def test_no_cast_no_read(self):
        twin, sess = _session()
        sess['cast'] = []
        self.assertIsNone(TR.attach_talent_read(sess, twin))
        self.assertNotIn('talent_read', sess)


class ModelRead(unittest.TestCase):
    def _fake(self, beals_said, quote="She is the reason I press play on episode two, I wanted more of her running things, the pilot built toward her and then handed the ending to the two guys on the boat.", seat=1):
        calls = []

        def fn(role, system, user, **kw):
            calls.append((role, user))
            return {'people': [
                {'name': 'LL Cool J', 'said': 'The room sat up every time LL Cool J was on screen, from the bar in the cold open to the wall. Five of sixteen said the two of them together are why they would keep watching.', 'quote': "LL Cool J slamming him into that wall, I'd rewind that ten times.", 'quote_seat': 1},
                {'name': 'Scott Caan', 'said': 'Scott Caan read as laying it on to several seats, but they liked the partnership with LL Cool J and said the banter carried the middle.', 'quote': 'Scott Caan was laying it on a little thick for me, but I liked the partnership and the banter carried the middle.', 'quote_seat': 4},
                {'name': 'Jennifer Beals', 'said': beals_said, 'quote': quote, 'quote_seat': seat},
            ]}
        return fn, calls

    def test_model_read_lands_with_the_constraints_in_the_call(self):
        twin, sess = _session(beals_leads=True)
        fn, calls = self._fake('They felt Jennifer Beals was underused: she had ninety seconds and the room kept asking for more. She is the reason ten of sixteen said they would come back.')
        ROOM.set_model_fn(fn)
        try:
            tr = TR.attach_talent_read(sess, twin, reason='test')
        finally:
            ROOM.set_model_fn(None)
        self.assertEqual(len(calls), 1, 'one call for every person')
        role, user = calls[0]
        self.assertEqual(role, 'moderator')
        payload = json.loads(user)
        self.assertEqual([p['name'] for p in payload['people']], ['LL Cool J', 'Scott Caan', 'Jennifer Beals'])
        beals_in = payload['people'][2]
        self.assertTrue(beals_in['card']['leads_follow'])
        self.assertEqual(beals_in['card']['follow_rank_among_named'], 1)
        self.assertIn('the woman running the team', beals_in['role_phrases_the_room_used'])
        self.assertTrue(any('woman running the team' in r['line'] for r in beals_in['remarks_during_the_watch']))
        self.assertEqual(tr['source'], 'model')
        beals = tr['people'][2]
        self.assertTrue(beals['said'].startswith('They felt Jennifer Beals was underused'))
        self.assertEqual(beals['source'], 'model')
        self.assertEqual(beals['quote']['seat'], 1)
        self.assertEqual(beals['quote']['clock'], '43:13')
        self.assertTrue(beals['quote']['line'].startswith('She is the reason I press play on episode two'))
        self.assertEqual(tr['people'][0]['quote']['clock'], '3:45')
        # the card rows carry it and the read is not stale
        self.assertEqual(sess['diagnostics']['characters']['characters'][2]['room_said']['said'], beals['said'])
        self.assertFalse(TR.is_stale(sess))
        # unchanged metrics: no second call
        ROOM.set_model_fn(fn)
        try:
            TR.attach_talent_read(sess, twin)
        finally:
            ROOM.set_model_fn(None)
        self.assertEqual(len(calls), 1)

    def test_bottom_of_follow_written_as_the_draw_is_set_aside(self):
        twin, sess = _session(beals_leads=False)
        fn, calls = self._fake('They liked Jennifer Beals and wanted more. She is the reason they would come back.')
        ROOM.set_model_fn(fn)
        try:
            tr = TR.attach_talent_read(sess, twin)
        finally:
            ROOM.set_model_fn(None)
        beals = tr['people'][2]
        self.assertEqual(beals['source'], 'deterministic', 'the model read broke the constraint; the deterministic read ships')
        self.assertNotIn('reason they would come back', beals['said'])
        self.assertEqual(tr['people'][0]['source'], 'model', 'the others keep the model read')

    def test_leader_written_as_forgettable_is_set_aside(self):
        twin, sess = _session(beals_leads=True)
        fn, calls = self._fake('Jennifer Beals barely registered with the room.')
        ROOM.set_model_fn(fn)
        try:
            tr = TR.attach_talent_read(sess, twin)
        finally:
            ROOM.set_model_fn(None)
        self.assertEqual(tr['people'][2]['source'], 'deterministic')
        self.assertIn('leads on who the room would follow', tr['people'][2]['said'])

    def test_an_invented_quote_falls_back_to_a_real_line(self):
        twin, sess = _session(beals_leads=True)
        fn, calls = self._fake('They felt Jennifer Beals was underused and wanted more of her.', quote='I would watch a whole show about her alone, honestly.', seat=9)
        ROOM.set_model_fn(fn)
        try:
            tr = TR.attach_talent_read(sess, twin)
        finally:
            ROOM.set_model_fn(None)
        beals = tr['people'][2]
        self.assertEqual(beals['source'], 'model')
        pool = [l for _s, _t, _k, l in REMARKS] + [a['line'] for q in sess['questions'] for a in q['answers']]
        self.assertTrue(any(beals['quote']['line'].rstrip('.') in p for p in pool), beals['quote'])

    def test_the_read_follows_the_metrics_when_they_move(self):
        twin, sess = _session(beals_leads=False)
        tr0 = TR.attach_talent_read(sess, twin)
        self.assertIn('sits third of 3', tr0['people'][2]['said'])
        _metrics(sess, beals_leads=True)     # the reconcile lets the room's answer outrank the inferred score
        self.assertTrue(TR.is_stale(sess))
        tr1 = TR.attach_talent_read(sess, twin, reason='reconciled')
        self.assertIn('leads on who the room would follow', tr1['people'][2]['said'])
        self.assertEqual(tr1.get('reason'), 'reconciled')
        self.assertFalse(TR.is_stale(sess))

    def test_a_refresh_keeps_the_moderator_prose_for_people_the_card_did_not_move(self):
        twin, sess = _session(beals_leads=True)
        fn, calls = self._fake('They felt Jennifer Beals was underused and wanted more of her. She is the reason ten of sixteen said they would come back.')
        ROOM.set_model_fn(fn)
        try:
            TR.attach_talent_read(sess, twin)
        finally:
            ROOM.set_model_fn(None)
        rows = sess['diagnostics']['characters']['characters']
        rows[2]['excellent_pct'] = 58.4          # the reconcile moved her number; LL Cool J and Scott Caan did not move
        self.assertTrue(TR.is_stale(sess))
        tr = TR.fresh_read(sess)
        self.assertTrue(tr.get('refreshed'))
        self.assertEqual([p['source'] for p in tr['people']], ['model', 'model', 'deterministic'])
        self.assertTrue(tr['people'][0]['said'].startswith('The room sat up every time LL Cool J'))
        self.assertIn('58.4% excellent', tr['people'][2]['said'])
        self.assertEqual(tr['source'], 'model')

    def test_question_names_talent(self):
        twin, sess = _session()
        q_role = {'q': 'if the next episode gave her the final scene, would that change how you rate the show?', 'framing': 'Thinking about the woman running the team.', 'options': []}
        q_name = {'q': 'how did you feel about jennifer beals?', 'options': []}
        q_none = {'q': 'was the ending too long?', 'framing': 'The last minute on the boat deck.', 'options': [{'id': 'a', 'label': 'Yes'}]}
        self.assertTrue(TR.question_names_talent(sess, q_role))
        self.assertTrue(TR.question_names_talent(sess, q_name))
        self.assertFalse(TR.question_names_talent(sess, q_none))


class Surfaces(unittest.TestCase):
    def test_report_pages_carry_a_talent_page(self):
        twin, sess = _session(beals_leads=True)
        TR.attach_talent_read(sess, twin)
        pages = _pages(twin, sess)
        kinds = [p['kind'] for p in pages]
        self.assertIn('talent', kinds)
        tp = pages[kinds.index('talent')]
        self.assertEqual(tp['header'], 'What the room said about the people you named.')
        self.assertLessEqual(len(tp['header'].split()), 12)
        self.assertEqual([p['name'] for p in tp['people']], ['LL Cool J', 'Scott Caan', 'Jennifer Beals'])
        self.assertTrue(tp['people'][2]['said'])
        self.assertTrue(tp['people'][2]['quote'])
        self.assertRegex(tp['people'][2]['who'], r"seat \d+, \d+:\d\d")
        # right after the characters table
        chars_i = next(i for i, p in enumerate(pages) if p['kind'] == 'table' and p.get('head') == ('Character', 'Excellent', 'Norm and screen time'))
        self.assertEqual(kinds.index('talent'), chars_i + 1)
        blob = _blob(tp)
        for w in BANNED:
            self.assertNotIn(w, blob, w)
        for p in pages:
            self.assertLessEqual(len(str(p.get('header') or '').split()), 12, p.get('header'))

    def test_deck_and_pdf_build_with_the_page(self):
        twin, sess = _session(beals_leads=True)
        TR.attach_talent_read(sess, twin)
        pdf = R.build_report_pdf({'meta': twin['meta']}, sess)
        self.assertTrue(pdf.startswith(b'%PDF'))
        deck = R.build_report_deck({'meta': twin['meta']}, sess)
        self.assertEqual(deck[:2], b'PK')
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(deck)) as z:
            xml = ' '.join(z.read(n).decode('utf-8', 'ignore') for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml'))
        self.assertIn('What the room said about the people you named.', xml)
        self.assertIn('wanted more of Jennifer Beals', xml)

    def test_csv_carries_a_row_per_person(self):
        twin, sess = _session(beals_leads=True)
        TR.attach_talent_read(sess, twin)
        csv = PI.session_csv(sess)
        self.assertIn("What the room said,Role,In the room's words,A seat's line,Said by", csv)
        block = csv[csv.index('What the room said,Role'):]
        for n in ('LL Cool J', 'Scott Caan', 'Jennifer Beals'):
            self.assertIn(f"\n{n},", block)
        self.assertIn('wanted more of Jennifer Beals', block)
        low = block[:4000].lower()
        for w in ('synth', 'modeled', 'persona', 'panel', '\u2014'):
            self.assertNotIn(w, low)

    def test_public_session_keeps_the_read_for_the_card(self):
        twin, sess = _session(beals_leads=True)
        TR.attach_talent_read(sess, twin)
        pub = PI.public_session(sess)
        rows = pub['diagnostics']['characters']['characters']
        self.assertTrue(all(r.get('room_said', {}).get('said') for r in rows))
        self.assertEqual(len(pub['talent_read']['people']), 3)
        self.assertIs(pub['talent_read'], sess['talent_read'], 'a current read travels as stored')

    def test_a_stale_read_shows_against_the_current_card_without_a_model_call(self):
        twin, sess = _session(beals_leads=False)
        TR.attach_talent_read(sess, twin)
        stored = sess['talent_read']
        self.assertIn('sits third of 3', stored['people'][2]['said'])
        _metrics(sess, beals_leads=True)     # the reconcile moved the card; the stored read was built before it
        calls = []
        ROOM.set_model_fn(lambda *a, **k: calls.append(1) or None)
        try:
            pub = PI.public_session(sess)
            lines = TR.report_lines(sess)
            pages = _pages(twin, sess)
            csv = PI.session_csv(sess)
        finally:
            ROOM.set_model_fn(None)
        self.assertEqual(calls, [], 'no model call on a page load or an export')
        self.assertIn('leads on who the room would follow', pub['talent_read']['people'][2]['said'])
        self.assertTrue(pub['talent_read'].get('refreshed'))
        self.assertIn('leads on who the room would follow', pub['diagnostics']['characters']['characters'][2]['room_said']['said'])
        self.assertIn('sits third of 3', sess['talent_read']['people'][2]['said'], 'the stored read is not rewritten on a read')
        self.assertNotIn('room_said', sess['diagnostics']['characters']['characters'][2], 'the recomputed rows on the stored session are left as the reconcile wrote them')
        self.assertIn('leads on who the room would follow', lines[2]['said'])
        tp = next(p for p in pages if p['kind'] == 'talent')
        self.assertIn('leads on who the room would follow', tp['people'][2]['said'])
        self.assertIn('leads on who the room would follow', csv)


class PrepareHook(unittest.TestCase):
    def test_prepare_watch_attaches_the_read_when_the_setup_named_people(self):
        twin = _beals()
        ROOM.set_model_fn(None)
        sess = PI.open_session([twin], title='Hook', in_room=8)
        sess['cast'] = ['Mae', 'Ray']
        PI.prepare_watch(sess, {'jb': twin}, duration_s=300, title='Hook cut')
        self.assertIn('talent_read', sess)
        self.assertEqual(sess['talent_read']['source'], 'deterministic')
        self.assertEqual([p['name'] for p in sess['talent_read']['people']], ['Mae', 'Ray'])
        for p in sess['talent_read']['people']:
            self.assertTrue(p['said'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
