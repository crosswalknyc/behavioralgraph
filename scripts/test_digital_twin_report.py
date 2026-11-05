#!/usr/bin/env python3
"""The studio report carries the room's questions (Jenna, 2026-11-04).

In Pilot Testing the moderator keeps asking the room at ASK THE ROOM after
the cut has played out (the test auto-stops at the runtime; a question
at 43:13 right after the CBS logo). Jenna: "if I am asking questions
here after the show wrapped and then if I click report pdf will these
be included?" Before this change the report never read
session['questions']. Now the report carries one page per answered
question (the question as asked and when, the framing, the split with
the seat tally, Prometheus's read, the voices, what the room knew and
what people were saying), an opener when there is more than one, a
TLDR line, and the pilot CSV carries the same block.

Hermetic: REGRESSION_TEST_MODE=1, no model, no network.

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_report.py
"""
from __future__ import annotations

import json
import os
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

sys.path.insert(0, HERE)
from test_digital_twin_pilot import _beals  # noqa: E402

# the words the page never carries (the user's list for this report, plus the module's own)
BANNED = ('panel', 'sample', 'model', 'synthetic', 'estimated', 'research', 'pipeline', 'ledger', 'synth', 'claude', 'hostmap', 'persona', '\u2014', '\u2013', ' door')


def _voice(roster, i, line):
    r = roster[i]
    return {'seat': r['seat'], 'name': r['name'], 'age': r['age'], 'city': r['city'], 'segment': r['segment'], 'line': line}


def _session(with_questions=True):
    twin = _beals()
    sess = PI.open_session([twin], title='Second Helping pilot', in_room=12)
    PI.prepare_watch(sess, {'jb': twin}, duration_s=600, title='A comedy pilot')
    PI.mark_play_paid(sess)
    PI.stop_session(sess)
    if not with_questions:
        return twin, sess
    roster = sess['group']['roster']
    # q1: on the clock during the cut, from the room engine, with everything on it
    q1 = {'id': 'q1', 'q': 'Would you keep watching after this scene?', 'kind': 'yes_no', 'stage': 'done',
          'framing': 'At 5:10 we just came off the diner fight. Tell me if you would stay with it for the next scene, or if this is where you would drift.',
          'options': [{'id': 'a', 'label': 'Yes', 'lean': 'yes', 'pct': 58.3, 'people': 4_812_311, 'seats': [1, 2, 3, 4, 5, 6, 7], 'seats_n': 7, 'tally': '7 of 12', 'room_pct': 58.3},
                      {'id': 'b', 'label': 'Not sure yet', 'lean': 'other', 'pct': 25.4, 'people': 2_097_114, 'seats': [8, 9, 10], 'seats_n': 3, 'tally': '3 of 12', 'room_pct': 25.0},
                      {'id': 'c', 'label': 'No', 'lean': 'no', 'pct': 16.3, 'people': 1_345_719, 'seats': [11, 12], 'seats_n': 2, 'tally': '2 of 12', 'room_pct': 16.7}],
          'distribution': [{'label': 'Yes', 'pct': 58.3, 'people': 4_812_311}, {'label': 'Not sure yet', 'pct': 25.4, 'people': 2_097_114}, {'label': 'No', 'pct': 16.3, 'people': 1_345_719}],
          'by_segment': [{'segment': 'Jennifer Beals', 'people': 8_255_144, 'distribution': [{'label': 'Yes', 'pct': 58.3, 'people': 4_812_311}, {'label': 'Not sure yet', 'pct': 25.4, 'people': 2_097_114}, {'label': 'No', 'pct': 16.3, 'people': 1_345_719}]}],
          'read': 'Most of the room stays. The fight gave them a reason to see what Mae does next, and the three who are not sure want one more scene with Ray before they decide.',
          'voices': [_voice(roster, 0, 'I want to see her win the town back.'), _voice(roster, 3, 'Give me one more scene with Ray.'),
                     _voice(roster, 10, 'The opening took too long to get going.'), _voice(roster, 5, 'A fourth voice that stays off the page.')],
          'facts_used': ['The show was ordered to series in May.', 'The lead has a following from a long-running cable drama.', 'The diner set is a real location in Ohio.',
                         'A soundtrack single came out last week.', 'A fifth fact that stays off the page.'],
          'talk': ['People online keep posting the diner fight clip.', 'The casting of the lead got a warm reception.', 'Some viewers compare it to a show from the same writer.',
                   'A fourth talk line that stays off the page.'],
          'stimulus': PI.ask_stimulus(sess, 310), 'asked_at': '2026-11-04T20:10:00Z', 'finished_at': '2026-11-04T20:11:00Z', 'seats_total': 12}
    # q2: after the wrap (the playhead at the runtime, asked after the stop), from the room engine, no facts or talk
    q2 = {'id': 'q2', 'q': 'For those who drifted during the silent ending, was it the silence itself or the length of the skyline shots that lost you?', 'kind': 'open', 'stage': 'done',
          'framing': '',
          'options': [{'id': 'a', 'label': 'The silence itself', 'lean': 'other', 'pct': 46.2, 'people': 3_812_204, 'seats': [1, 2, 3, 4, 5, 6], 'seats_n': 6, 'tally': '6 of 12'},
                      {'id': 'b', 'label': 'The length of the skyline shots', 'lean': 'other', 'pct': 31.1, 'people': 2_566_401, 'seats': [7, 8, 9, 10], 'seats_n': 4, 'tally': '4 of 12'},
                      {'id': 'c', 'label': 'Stayed with it', 'lean': 'other', 'pct': 22.7, 'people': 1_876_539, 'seats': [11, 12], 'seats_n': 2, 'tally': '2 of 12'}],
          'read': 'The silence, not the skyline, is what lost the drifters.',
          'voices': [_voice(roster, 1, 'The quiet went on a beat too long.'), _voice(roster, 7, 'I liked the skyline, I just wanted a line of dialogue.')],
          'facts_used': [], 'talk': [], 'stimulus': PI.ask_stimulus(sess, 600), 'asked_at': '2026-11-04T20:55:00Z', 'finished_at': '2026-11-04T20:56:00Z', 'seats_total': 12}
    # q3: still answering when the report is made
    q3 = {'id': 'q3', 'q': 'Would you tell a friend?', 'kind': 'yes_no', 'stage': 'researching', 'job_id': 'job0003', 'options': [], 'read': '', 'voices': [],
          'stimulus': PI.ask_stimulus(sess, 600), 'asked_at': '2026-11-04T20:58:00Z'}
    # q4: the deterministic engine's record (no stage, a distribution, options as labels), asked first
    q4 = {'id': 'q4', 'q': 'Did the opening hold you?', 'kind': 'yes_no', 'options': ['Yes', 'No'],
          'distribution': [{'label': 'Yes', 'pct': 61.4, 'people': 5_070_000}, {'label': 'No', 'pct': 38.6, 'people': 3_190_000}],
          'read': 'The opening held most of them.', 'voices': [_voice(roster, 2, 'The bell ringing got me.')], 'stimulus': PI.ask_stimulus(sess, 95), 'asked_at': '2026-11-04T20:05:00Z'}
    PI.stamp_question(q1, 310)
    PI.stamp_question(q2, 600)
    PI.stamp_question(q3, 600)
    PI.stamp_question(q4, 95)
    sess['questions'] = [q2, q1, q3, q4]     # out of order on purpose; the report orders by asked_at
    return twin, sess


def _blob(pages):
    return json.dumps([{k: v for k, v in p.items() if k != 'image'} for p in pages], default=str).lower()


class QuestionPages(unittest.TestCase):
    def test_one_page_per_answered_question_in_asked_order(self):
        twin, sess = _session()
        pages = R.report_pages({'meta': twin['meta']}, sess)
        qp = [p for p in pages if p['kind'] == 'question']
        self.assertEqual(len(qp), 3, 'three answered; the one still answering has no page')
        self.assertEqual([p['sub'].split('. ', 1)[1][:24] for p in qp], ['Did the opening hold you', 'Would you keep watching ', 'For those who drifted du'])
        # the section sits after the dial and scene pages and before the recruit, forecast and conclusions pages
        kinds = [p['kind'] for p in pages]
        first_q = kinds.index('question')
        self.assertGreater(first_q, kinds.index('image'))
        self.assertLess(first_q, len(pages) - 1)
        self.assertEqual(pages[-1]['header'], 'Key conclusions and what to do next.')
        after = [p['header'] for p in pages[first_q + 3:]]
        self.assertTrue(any(h.startswith('Recruited to') for h in after), after)
        self.assertTrue(all(p['kind'] != 'question' for p in pages[first_q + 3:]))

    def test_each_page_carries_the_fields(self):
        twin, sess = _session()
        pages = R.report_pages({'meta': twin['meta']}, sess)
        q1 = next(p for p in pages if p['kind'] == 'question' and 'keep watching' in p['sub'])
        self.assertEqual(q1['header'], 'Yes took 58.3% of the room.')
        self.assertTrue(q1['sub'].startswith('At 5:10'), q1['sub'])
        self.assertIn('Would you keep watching after this scene?', q1['sub'])
        self.assertTrue(q1['framing'].startswith('At 5:10 we just came off the diner fight'))
        self.assertEqual(q1['head'], ('Answer', 'Share', 'People and seats'))
        self.assertEqual(q1['rows'][0], ('Yes', '58.3%', '4.81M people, 7 of 12 in the room'))
        self.assertEqual([r[0] for r in q1['rows']], ['Yes', 'Not sure yet', 'No'])
        self.assertTrue(q1['read'].startswith('Most of the room stays.'))
        self.assertEqual(len(q1['voices']), 3, 'two or three voices, never the fourth')
        self.assertTrue(q1['voices'][0].endswith(': I want to see her win the town back.'))
        self.assertRegex(q1['voices'][0], r'^[A-Za-z]+, \d+, [A-Za-z .]+: ')
        self.assertEqual(len(q1['knew']), 4)
        self.assertEqual(q1['knew'][0], 'The show was ordered to series in May.')
        self.assertEqual(len(q1['talk']), 3)
        self.assertEqual(q1['talk'][0], 'People online keep posting the diner fight clip.')
        self.assertNotIn('fifth fact', _blob([q1]))
        self.assertNotIn('fourth talk', _blob([q1]))

    def test_a_question_after_the_wrap_says_so(self):
        twin, sess = _session()
        pages = R.report_pages({'meta': twin['meta']}, sess)
        q2 = next(p for p in pages if p['kind'] == 'question' and 'silent ending' in p['sub'])
        self.assertEqual(q2['header'], 'The silence itself took 46.2% of the room.')
        self.assertTrue(q2['sub'].startswith('After the wrap, at 10:00'), q2['sub'])
        self.assertEqual(q2['rows'][1], ('The length of the skyline shots', '31.1%', '2.57M people, 4 of 12 in the room'))
        self.assertEqual(q2['framing'], '')
        self.assertEqual(q2['knew'], [])
        self.assertEqual(q2['talk'], [])
        self.assertEqual(len(q2['voices']), 2)

    def test_the_deterministic_record_renders_from_its_distribution(self):
        twin, sess = _session()
        pages = R.report_pages({'meta': twin['meta']}, sess)
        q4 = next(p for p in pages if p['kind'] == 'question' and 'opening hold' in p['sub'])
        self.assertEqual(q4['header'], 'Yes took 61.4% of the room.')
        self.assertEqual(q4['rows'], [('Yes', '61.4%', '5.07M people'), ('No', '38.6%', '3.19M people')])
        self.assertTrue(q4['sub'].startswith('At 1:35'))

    def test_opener_lists_every_question_and_names_the_one_still_answering(self):
        twin, sess = _session()
        pages = R.report_pages({'meta': twin['meta']}, sess)
        op = next(p for p in pages if p['header'].startswith('The room answered'))
        self.assertEqual(op['kind'], 'table')
        self.assertEqual(op['header'], 'The room answered 3 questions, on the clock and after the wrap.')
        self.assertEqual(pages[pages.index(op) + 1]['kind'], 'question', 'the opener leads straight into the question pages')
        self.assertEqual(len(op['rows']), 3)
        self.assertEqual(op['rows'][0][0], '1:35  Did the opening hold you?')
        self.assertEqual(op['rows'][2][1:], ('46.2%', 'The silence itself (after the wrap)'))
        self.assertEqual(len(op['body']), 1)
        self.assertIn('still answering when this report was made', op['body'][0])
        self.assertIn('Would you tell a friend?', op['body'][0])

    def test_headers_obey_the_twelve_word_rule_and_no_banned_words(self):
        twin, sess = _session()
        pages = R.report_pages({'meta': twin['meta']}, sess)
        for p in pages:
            if p['kind'] in ('question',) or p['header'].startswith('The room answered'):
                words = p['header'].split()
                self.assertLessEqual(len(words), 12, p['header'])
                self.assertTrue(p['header'].endswith('.'), p['header'])
                self.assertTrue(p['header'][0].isupper(), p['header'])
        blob = _blob([p for p in pages if p['kind'] == 'question' or p['header'].startswith('The room answered')] + [pages[1]])
        for tok in BANNED:
            self.assertNotIn(tok, blob, tok)

    def test_tldr_counts_the_questions_and_names_the_last_lead(self):
        twin, sess = _session()
        pages = R.report_pages({'meta': twin['meta']}, sess)
        tldr = pages[1]
        self.assertEqual(tldr['kind'], 'bullets')
        self.assertLessEqual(len(tldr['body']), 5)
        self.assertEqual(tldr['body'][-1], 'The room answered 3 questions; on the last, 46.2% went with "The silence itself".')

    def test_a_failed_question_is_noted_not_paged(self):
        twin, sess = _session()
        sess['questions'][2]['stage'] = 'failed'
        pages = R.report_pages({'meta': twin['meta']}, sess)
        op = next(p for p in pages if p['header'].startswith('The room answered'))
        self.assertIn('did not finish one question', op['body'][0])
        self.assertEqual(len([p for p in pages if p['kind'] == 'question']), 3)

    def test_a_single_answered_question_gets_its_page_and_no_opener(self):
        twin, sess = _session()
        sess['questions'] = [sess['questions'][1]]     # q1 alone
        pages = R.report_pages({'meta': twin['meta']}, sess)
        self.assertEqual(len([p for p in pages if p['kind'] == 'question']), 1)
        self.assertFalse(any(p['header'].startswith('The room answered') for p in pages))
        self.assertEqual(pages[1]['body'][-1], 'The room answered 1 question; on the last, 58.3% went with "Yes".')

    def test_no_questions_renders_as_before(self):
        twin, sess = _session(with_questions=False)
        pages = R.report_pages({'meta': twin['meta']}, sess)
        self.assertEqual(sess.get('questions'), [])
        self.assertFalse(any(p['kind'] == 'question' for p in pages))
        self.assertFalse(any('answered' in p['header'] for p in pages))
        self.assertFalse(any('answered' in b for b in pages[1]['body']))
        kinds = [p['kind'] for p in pages]
        self.assertEqual(kinds[:4], ['cover', 'bullets', 'tiles', 'table'])
        self.assertIn('image', kinds)
        self.assertEqual(pages[-1]['header'], 'Key conclusions and what to do next.')
        sess['questions'] = None
        self.assertEqual(_blob(R.report_pages({'meta': twin['meta']}, sess)), _blob(pages))


class Files(unittest.TestCase):
    def test_pdf_and_deck_carry_the_question_pages(self):
        twin, sess = _session()
        pdf = R.build_report_pdf({'meta': twin['meta']}, sess)
        self.assertTrue(pdf[:4] == b'%PDF')
        deck = R.build_report_deck({'meta': twin['meta']}, sess)
        self.assertGreater(len(deck), 20000)
        try:
            from pptx import Presentation
        except Exception:
            self.skipTest('python-pptx not installed')
        import io
        prs = Presentation(io.BytesIO(deck))
        slides = [' '.join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame) for sl in prs.slides]
        q1 = next(s for s in slides if 'Yes took 58.3% of the room.' in s)
        self.assertIn('Would you keep watching after this scene?', q1)
        for tok in ("PROMETHEUS'S READ", 'Most of the room stays.', 'IN THEIR WORDS', 'I want to see her win the town back.',
                    'WHAT THE ROOM KNEW', 'The show was ordered to series in May.', 'WHAT PEOPLE WERE SAYING', 'People online keep posting the diner fight clip.', '7 of 12 in the room'):
            self.assertIn(tok, q1, tok)
        q2 = next(s for s in slides if 'The silence itself took 46.2% of the room.' in s)
        self.assertIn('After the wrap, at 10:00', q2)
        self.assertNotIn('WHAT THE ROOM KNEW', q2)
        blob = ' '.join(s for s in slides if 'took ' in s and 'of the room.' in s or 'The room answered' in s).lower()
        for tok in BANNED:
            self.assertNotIn(tok, blob, tok)
        try:
            from migration import design_skill as ds
        except Exception:
            return
        if ds.scripts_dir() is not None:
            import tempfile
            p = os.path.join(tempfile.mkdtemp(), 'report.pptx')
            with open(p, 'wb') as fh:
                fh.write(deck)
            rep = ds.brand_lint(p)
            step = next((s for s in rep.steps if s[0] == 'brand_lint'), None)
            self.assertIsNotNone(step)
            self.assertTrue(step[1], step[2][-1200:])


class EveryPage(unittest.TestCase):
    def test_no_page_and_no_forecast_note_carries_an_internal_word(self):
        """The forecast note once read 'No aired episode on the ledger yet'
        and reached the report page body (flagged 2026-11-04). Every page
        of the report (header, sub, body, tiles, rows, the question
        blocks), the note the test card shows and the CSV are plain
        English; the internal words live in code and keys only."""
        twin, sess = _session()
        fc = sess.get('forecast') or {}
        self.assertEqual((fc.get('calibration') or {}).get('scope'), 'none', 'a hermetic test has no aired episode to compare against')
        self.assertEqual(fc.get('notes'), ['No aired episode to compare against yet. The forecast sits at the low end and calibrates as actuals come in.'])
        pages = R.report_pages({'meta': twin['meta']}, sess)
        forecast_page = next(p for p in pages if p['kind'] == 'tiles' and 'first week' in p['header'])
        self.assertIn(fc['notes'][0], forecast_page['body'], 'the note reaches the report page, in plain words')
        blob = _blob(pages)
        csv = PI.session_csv(sess, {'meta': twin['meta']}).lower()
        for tok in BANNED + ('anchor',):
            self.assertNotIn(tok, blob, tok)
            self.assertNotIn(tok, csv, tok)


class PeopleAndCopy(unittest.TestCase):
    """The scorecard page, the whole-room quadrant and the room page carry
    one people figure (the room's segments), and the small copy slips
    found on the NCIS: New York Pilot report (2026-11-04) stay fixed."""

    def test_scorecard_page_quadrant_total_and_room_page_agree(self):
        twin, sess = _session(with_questions=False)
        segs = sess['group']['segments']
        people = sum(int(s['people']) for s in segs)
        total = next(q['people'] for q in sess['scorecard']['battery']['quadrants'] if q['key'] == 'total')
        self.assertEqual(people, total)
        pages = R.report_pages({'meta': twin['meta']}, sess, with_chart=False)
        tiles = next(p for p in pages if p['kind'] == 'tiles')
        room = next(p for p in pages if p['kind'] == 'roster')
        shown = R.human_people(people)
        self.assertTrue(tiles['sub'].startswith(f"The room seated 12 people, with {shown} behind them. Pilot"), tiles['sub'])
        self.assertEqual(room['header'], f"The room seated 12 people, standing for {shown}.")
        self.assertEqual(room['sub'], 'Who was seated, and the share of the room each group held.', 'a plain room is the audience; nothing to add')
        # a stored stand-in on the card (sixteen seats times 800, the old fallback) never reaches the page
        sess['scorecard']['people'] = 9_613
        pages = R.report_pages({'meta': twin['meta']}, sess, with_chart=False)
        self.assertNotIn('9.61K', next(p for p in pages if p['kind'] == 'tiles')['sub'])
        header, first = PI.session_csv(sess, {'meta': twin['meta']}).splitlines()[:2]
        self.assertEqual(header.split(',')[6], 'People')
        self.assertEqual(first.split(',')[6], str(people), 'the CSV carries the same figure, never the stored stand-in')

    def test_a_cohort_room_names_the_audience_it_was_drawn_from(self):
        twin, sess = _session(with_questions=False)
        g = sess['group']
        g['size_people'] = 83_791_763          # the cohorts were cut from a wider audience
        pages = R.report_pages({'meta': twin['meta']}, sess, with_chart=False)
        room = next(p for p in pages if p['kind'] == 'roster')
        shown = R.human_people(sum(int(s['people']) for s in g['segments']))
        self.assertEqual(room['header'], f"The room seated 12 people, standing for {shown}.")
        self.assertTrue(room['sub'].endswith('The 12 were drawn from an audience of 83.79M.'), room['sub'])
        self.assertIn(f"with {shown} behind them", next(p for p in pages if p['kind'] == 'tiles')['sub'])

    def test_no_source_prints_no_count(self):
        twin, sess = _session(with_questions=False)
        for s in sess['group']['segments']:
            s.pop('people', None)
        for r in sess['group']['roster']:
            r.pop('people', None)
        sess['scorecard']['battery']['quadrants'] = []
        sess['group']['size_people'] = 0
        pages = R.report_pages({'meta': twin['meta']}, sess, with_chart=False)
        self.assertTrue(next(p for p in pages if p['kind'] == 'tiles')['sub'].startswith('The room seated 12 people. Pilot'))
        self.assertEqual(next(p for p in pages if p['kind'] == 'roster')['header'], 'The room seated 12 people.')

    def test_recruit_page_prints_seated_once_and_no_stand_in_ratio(self):
        twin, sess = _session(with_questions=False)
        rec = sess['recruit']
        self.assertIsNone(rec.get('invited'))
        pages = R.report_pages({'meta': twin['meta']}, sess, with_chart=False)
        pg = next(p for p in pages if p['header'].startswith('Recruited to'))
        labels = [lab for _v, lab in pg['tiles']]
        self.assertEqual(labels.count('Seated'), 1, labels)
        self.assertNotIn('Invite ratio', labels)
        self.assertNotIn('Invited', labels)
        self.assertIn('Walked out', labels)
        rec['invited'] = 41
        rec['invite_ratio_n'] = 3
        pg = next(p for p in R.report_pages({'meta': twin['meta']}, sess, with_chart=False) if p['header'].startswith('Recruited to'))
        self.assertEqual([lab for _v, lab in pg['tiles']], ['Seated', 'Invited', 'Walked out', 'Invite ratio'])
        self.assertEqual(pg['tiles'][3][0], '3 : 1')

    def test_after_the_wrap_keeps_an_acronym_in_the_frame(self):
        twin, sess = _session()
        q = next(x for x in sess['questions'] if x['id'] == 'q2')
        q['stimulus']['window']['frame'] = 'CBS logo animation glows on a black screen, marking the end of the episode'
        pages = R.report_pages({'meta': twin['meta']}, sess, with_chart=False)
        pg = next(p for p in pages if p['kind'] == 'question' and 'silent ending' in p['sub'])
        self.assertTrue(pg['sub'].startswith('After the wrap, CBS logo animation glows'), pg['sub'])
        q['stimulus']['window']['frame'] = 'The CBS logo comes up'
        pg = next(p for p in R.report_pages({'meta': twin['meta']}, sess, with_chart=False) if p['kind'] == 'question' and 'silent ending' in p['sub'])
        self.assertTrue(pg['sub'].startswith('After the wrap, the CBS logo comes up'), pg['sub'])

    def test_forecast_tile_label_fits_and_the_base_is_named_under_it(self):
        twin, sess = _session(with_questions=False)
        pages = R.report_pages({'meta': twin['meta']}, sess, with_chart=False)
        fc = next(p for p in pages if p['kind'] == 'tiles' and 'first week' in p['header'])
        self.assertEqual(fc['tiles'][3][1], 'Would subscribe for it')
        self.assertTrue(any('% of people without Jennifer Beals, about ' in b and 'would subscribe for it' in b for b in fc['body']), fc['body'])

    def test_a_confusion_quote_rides_after_a_colon_in_quotes(self):
        twin, sess = _session(with_questions=False)
        pace = sess['diagnostics']['pace']
        pace['confusion_pct'] = 56.1
        pace['confusions'] = [{'line': 'Three missed calls from Jack Halloway. I have no idea who that is', 'pct': 56.1}]
        pages = R.report_pages({'meta': twin['meta']}, sess, with_chart=False)
        line = next(b for b in pages[-1]['body'] if b.startswith('Clear up'))
        self.assertEqual(line, 'Clear up what lost 56.1% of the room: "Three missed calls from Jack Halloway. I have no idea who that is."')

    def test_attach_cell_keeps_to_the_shares_that_fit(self):
        rec = {'talent': 'Jennifer Beals', 'role': 'supporting', 'outputs': {'reach_lift_pct': 12.1, 'expected': 2_112_345, 'room': {'parts': {'follow': 60.1, 'excellent': 57.2}, 'is_draw': True, 'verdict_pct': 59.7}},
               'room': {'seen': True, 'draw_seats': 10, 'seats': 16}, 'read': 'Jennifer Beals (billed supporting) lifts first-month reach 12.1%; in the room she was the draw (10 of 16 named her as the reason to come back).'}
        self.assertEqual(R._attach_room_line(rec, cell=True), '60.1% would follow, 57.2% excellent')
        self.assertEqual(R._attach_room_line(rec), '60.1% would follow, 57.2% excellent, 10 of 16 the reason to come back')
        pg = R._attach_pages({'talent_attach': [rec]})[0]
        self.assertEqual(pg['rows'][0], ('Jennifer Beals (billed supporting)', '+12.1%', '2.11M more people; 60.1% would follow, 57.2% excellent'))
        self.assertIn('10 of 16 named her as the reason to come back', pg['body'][0])


class Csv(unittest.TestCase):
    def test_pilot_csv_carries_the_questions_block(self):
        twin, sess = _session()
        csv = PI.session_csv(sess)
        lines = csv.splitlines()
        self.assertIn('Clock,Question,Segment,Answer,Share %,People,In the room', lines)
        self.assertIn('5:10,Would you keep watching after this scene?,Whole room,Yes,58.3,4812311,7 of 12', lines)
        self.assertIn('5:10,Would you keep watching after this scene?,Jennifer Beals,Yes,58.3,4812311,', lines)
        self.assertIn('1:35,Did the opening hold you?,Whole room,Yes,61.4,5070000,', lines)
        self.assertIn('Clock,Question,Block,Line', lines)
        self.assertIn('5:10,Would you keep watching after this scene?,What the room knew,The show was ordered to series in May.', lines)
        self.assertIn('5:10,Would you keep watching after this scene?,What people were saying,People online keep posting the diner fight clip.', lines)
        self.assertTrue(any(l.startswith('5:10,Would you keep watching after this scene?,How it was put to the room,"At 5:10 we just came off') for l in lines))
        self.assertTrue(any(l.startswith('5:10,Would you keep watching after this scene?,yes_no,"Most of the room stays.') for l in lines), 'the read row')
        self.assertTrue(any(',I want to see her win the town back.' in l for l in lines), 'the voice row')
        # the one after the wrap rides along with its clock
        self.assertTrue(any(l.startswith('10:00,"For those who drifted') and 'Whole room,The silence itself,46.2,3812204,6 of 12' in l for l in lines))

    def test_pilot_csv_without_questions_has_no_block_rows(self):
        twin, sess = _session(with_questions=False)
        csv = PI.session_csv(sess)
        self.assertNotIn('What the room knew', csv)
        self.assertIn('Clock,Question,Segment,Answer,Share %,People,In the room', csv)


if __name__ == '__main__':
    unittest.main(verbosity=2)
