#!/usr/bin/env python3
"""Every export of a Pilot Testing test carries the same insight (Jenna,
2026-11-04: "confirming the deck will have all of this insight when I
click to export it in any format").

Before this change the plain PDF and DECK buttons ran an older, thinner
builder (the scorecard table with the genre in the sub line, the
questions in brief, the roster with portraits, the top moments) with no
"Compared to" norm line, no forecast, no "what the room knew / what
people were saying", no per-talent block and no norm columns, while
REPORT (DECK) and REPORT (PDF) ran the studio report. Now there is one
page list, ``digital_twin_report.report_pages``: the report deck, the
report PDF and the plain exports are all drawn from it (the room pages
the old export carried ride inside it), the CSV carries every block plus
the report page by page, and the test page offers one Report (deck), one
Report (PDF) and a CSV. A block another builder adds to ``report_pages``
lands in every format with no second list to update.

Hermetic: REGRESSION_TEST_MODE=1, no model, no network.

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_exports_parity.py
"""
from __future__ import annotations

import io
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

from migration import digital_twin_compare as C  # noqa: E402
from migration import digital_twin_exports as X  # noqa: E402
from migration import digital_twin_forecast as F  # noqa: E402
from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twin_report as R  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_modes import TRANSCRIPT  # noqa: E402
from test_digital_twin_pilot import _beals  # noqa: E402
from test_digital_twin_report import _voice  # noqa: E402

# the words no page of the report carries (the report module's own list, the dashes, the door metaphor)
BANNED = tuple(R.BANNED_WORDS) + ('\u2014', '\u2013', ' door')
# the stricter list for the pages this change adds (the room and the moments)
BANNED_NEW = BANNED + ('panel', 'sample', 'model', 'research', 'ledger')


def _jpeg(shade: int) -> bytes:
    from PIL import Image
    im = Image.new('RGB', (128, 128), (shade, 90, 140))
    buf = io.BytesIO()
    im.save(buf, 'JPEG')
    return buf.getvalue()


def _portrait_fn(h):
    """Three faces come back, one hash has no face, one read fails: none of it fails an export."""
    if h in ('face0', 'face1', 'face2'):
        return _jpeg(60 + 50 * int(h[-1]))
    if h == 'face3':
        return None
    raise RuntimeError('portrait store down')


def _session(scored: bool = True):
    """A scored test with everything a report reads: a cast (characters),
    quotas (the recruit), a forecast, a norm choice, three answered
    questions (one from the room engine with facts and talk, one after the
    wrap, one deterministic) and portrait hashes on five seats."""
    twin = _beals()
    twin['meta']['size_us'] = 8_255_144
    sess = PI.open_session([twin], title='Second Helping pilot', in_room=12)
    if scored:
        sess['cast'] = ['Mae', 'Ray']
        C.apply_quotas(sess, [twin], {'under_35_pct': 60, 'women_pct': 55, 'fans_of_lead_pct': 40, 'must_have_seen': 1, 'lead': 'Mae'})     # reseats the room
        PI.prepare_watch(sess, {'jb': twin}, duration_s=600, title='A comedy pilot', transcript=TRANSCRIPT)
        PI.mark_play_paid(sess)
        PI.stop_session(sess)
        F.attach_forecast(twin, sess, ledger=F._empty_ledger())
    for i, r in enumerate(sess['group']['roster'][:5]):
        r['portrait_hash'] = f"face{i}"
    if not scored:
        return twin, sess
    roster = sess['group']['roster']
    q1 = {'id': 'q1', 'q': 'Would you keep watching after this scene?', 'kind': 'yes_no', 'stage': 'done',
          'framing': 'At 5:10 we just came off the diner fight. Tell me if you would stay with it for the next scene.',
          'options': [{'id': 'a', 'label': 'Yes', 'lean': 'yes', 'pct': 58.3, 'people': 4_812_311, 'seats': [1, 2, 3, 4, 5, 6, 7], 'seats_n': 7, 'tally': '7 of 12'},
                      {'id': 'b', 'label': 'Not sure yet', 'lean': 'other', 'pct': 25.4, 'people': 2_097_114, 'seats': [8, 9, 10], 'seats_n': 3, 'tally': '3 of 12'},
                      {'id': 'c', 'label': 'No', 'lean': 'no', 'pct': 16.3, 'people': 1_345_719, 'seats': [11, 12], 'seats_n': 2, 'tally': '2 of 12'}],
          'read': 'Most of the room stays. The fight gave them a reason to see what Mae does next.',
          'voices': [_voice(roster, 0, 'I want to see her win the town back.'), _voice(roster, 3, 'Give me one more scene with Ray.')],
          'facts_used': ['The show was ordered to series in May.', 'The lead has a following from a long-running cable drama.'],
          'talk': ['People online keep posting the diner fight clip.'],
          'stimulus': PI.ask_stimulus(sess, 310), 'asked_at': '2026-11-04T20:10:00Z', 'finished_at': '2026-11-04T20:11:00Z', 'seats_total': 12}
    q2 = {'id': 'q2', 'q': 'Was it the silence itself or the length of the skyline shots that lost you?', 'kind': 'open', 'stage': 'done', 'framing': '',
          'options': [{'id': 'a', 'label': 'The silence itself', 'lean': 'other', 'pct': 46.2, 'people': 3_812_204, 'seats': [1, 2, 3, 4, 5, 6], 'seats_n': 6, 'tally': '6 of 12'},
                      {'id': 'b', 'label': 'The length of the skyline shots', 'lean': 'other', 'pct': 31.1, 'people': 2_566_401, 'seats': [7, 8, 9, 10], 'seats_n': 4, 'tally': '4 of 12'},
                      {'id': 'c', 'label': 'Stayed with it', 'lean': 'other', 'pct': 22.7, 'people': 1_876_539, 'seats': [11, 12], 'seats_n': 2, 'tally': '2 of 12'}],
          'read': 'The silence, not the skyline, is what lost the drifters.', 'voices': [_voice(roster, 1, 'The quiet went on a beat too long.')],
          'facts_used': [], 'talk': [], 'stimulus': PI.ask_stimulus(sess, 600), 'asked_at': '2026-11-04T20:55:00Z', 'finished_at': '2026-11-04T20:56:00Z', 'seats_total': 12}
    q4 = {'id': 'q4', 'q': 'Did the opening hold you?', 'kind': 'yes_no', 'options': ['Yes', 'No'],
          'distribution': [{'label': 'Yes', 'pct': 61.4, 'people': 5_070_000}, {'label': 'No', 'pct': 38.6, 'people': 3_190_000}],
          'read': 'The opening held most of them.', 'voices': [_voice(roster, 2, 'The bell ringing got me.')], 'stimulus': PI.ask_stimulus(sess, 95), 'asked_at': '2026-11-04T20:05:00Z'}
    PI.stamp_question(q1, 310)
    PI.stamp_question(q2, 600)
    PI.stamp_question(q4, 95)
    sess['questions'] = [q2, q1, q4]
    return twin, sess


def _norm(s) -> str:
    return ' '.join(str(s or '').split())


def _slides(deck: bytes) -> list:
    from pptx import Presentation
    prs = Presentation(io.BytesIO(deck))
    return [' '.join(sh.text_frame.text for sh in sl.shapes if sh.has_text_frame) for sl in prs.slides]


def _pdf_text(pdf: bytes) -> str:
    import pypdf
    rd = pypdf.PdfReader(io.BytesIO(pdf))
    return _norm(' '.join((pg.extract_text() or '') for pg in rd.pages))


def _pdf_pages(pdf: bytes) -> int:
    import pypdf
    return len(pypdf.PdfReader(io.BytesIO(pdf)).pages)


def _blob(pages) -> str:
    return json.dumps([{k: v for k, v in p.items() if k not in ('image', 'seats')} for p in pages], default=str).lower()


class OnePageList(unittest.TestCase):
    def test_the_plain_exports_are_the_report(self):
        twin, sess = _session()
        payload = {'meta': twin['meta']}
        for pf in (None, _portrait_fn):
            plain_deck, report_deck = X.build_pilot_deck(payload, sess, portrait_fn=pf), R.build_report_deck(payload, sess, portrait_fn=pf)
            self.assertEqual(_slides(plain_deck), _slides(report_deck))
            plain_pdf, report_pdf = X.build_pilot_pdf(payload, sess, portrait_fn=pf), R.build_report_pdf(payload, sess, portrait_fn=pf)
            self.assertTrue(plain_pdf[:4] == b'%PDF' and report_pdf[:4] == b'%PDF')
            self.assertEqual(_pdf_pages(plain_pdf), _pdf_pages(report_pdf))
            self.assertEqual(_pdf_text(plain_pdf), _pdf_text(report_pdf))

    def test_the_plain_builders_delegate_to_the_report_builders(self):
        twin, sess = _session()
        payload = {'meta': twin['meta']}
        seen = {}
        orig_deck, orig_pdf = R.build_report_deck, R.build_report_pdf
        try:
            R.build_report_deck = lambda p, s, portrait_fn=None: seen.setdefault('deck', (p, s, portrait_fn)) and b'DECK'
            R.build_report_pdf = lambda p, s, portrait_fn=None: seen.setdefault('pdf', (p, s, portrait_fn)) and b'PDF'
            self.assertEqual(X.build_pilot_deck(payload, sess, portrait_fn=_portrait_fn), b'DECK')
            self.assertEqual(X.build_pilot_pdf(payload, sess, portrait_fn=_portrait_fn), b'PDF')
        finally:
            R.build_report_deck, R.build_report_pdf = orig_deck, orig_pdf
        self.assertIs(seen['deck'][2], _portrait_fn)
        self.assertIs(seen['pdf'][2], _portrait_fn)

    def test_every_page_header_lands_in_every_format(self):
        twin, sess = _session()
        payload = {'meta': twin['meta']}
        pages = R.report_pages(payload, sess, portrait_fn=_portrait_fn)
        headers = [_norm(p['header']) for p in pages]
        self.assertGreaterEqual(len(headers), 16)
        deck_blob = _norm(' '.join(_slides(X.build_pilot_deck(payload, sess, portrait_fn=_portrait_fn))))
        pdf_blob = _pdf_text(X.build_pilot_pdf(payload, sess, portrait_fn=_portrait_fn))
        for h in headers:
            self.assertIn(h, deck_blob, h)
            self.assertIn(h, pdf_blob, h)
        # the slide count and the page count are the page list
        self.assertEqual(len(_slides(R.build_report_deck(payload, sess, portrait_fn=_portrait_fn))), len(pages))
        self.assertEqual(_pdf_pages(R.build_report_pdf(payload, sess, portrait_fn=_portrait_fn)), len(pages))

    def test_the_report_carries_what_the_old_export_had_and_what_it_lacked(self):
        twin, sess = _session()
        pages = R.report_pages({'meta': twin['meta']}, sess, portrait_fn=_portrait_fn)
        kinds = [p['kind'] for p in pages]
        heads = [p['header'] for p in pages]
        # what the old room export carried: the roster (faces, names, segments) and the strongest moments seat by seat
        ro = next(p for p in pages if p['kind'] == 'roster')
        self.assertEqual(ro['header'], 'The room seated 12 people, standing for 6.72M.')
        self.assertEqual(len(ro['seats']), 12)
        self.assertEqual([s['name'] for s in ro['seats']], [r['name'] for r in sess['group']['roster']])
        self.assertEqual(sum(1 for s in ro['seats'] if s['portrait']), 3, 'three faces came back; a missing face and a failed read stand in silently')
        self.assertEqual(ro['head'], ('Segment', 'Share of room', 'People and seats'))
        # the quota cells, with the share of the room each held as seats (a cell stores 100 for its own sub-room)
        self.assertEqual([r[0] for r in ro['rows']], ['Women under 35', 'Women 35 and over', 'Men under 35', 'Men 35 and over'])
        self.assertEqual(ro['rows'][0][1:], ('33.3%', '1.10M people, 4 seats'))
        self.assertEqual(sum(float(r[1].rstrip('%')) for r in ro['rows']), 100.0)
        self.assertEqual(X.segment_shares({'group': {'segments': [{'label': 'All', 'share_pct': 100.0, 'people': 10}]}}), [('All', 100.0, 10, 0)], 'no seats recorded: the stored share stands')
        self.assertTrue(ro['body'][0].startswith('In the room: ' + sess['group']['roster'][0]['name'] + ' ('), ro['body'][0])
        self.assertGreater(kinds.index('roster'), heads.index(next(h for h in heads if h.startswith('Recruited to'))), 'the room follows the recruit page')
        mo = next(p for p in pages if p['header'] == 'The moments that moved the room, seat by seat.')
        self.assertEqual(mo['head'], ('Clock and reaction', 'Who', 'In their words'))
        self.assertEqual(len(mo['rows']), 10)
        self.assertRegex(mo['rows'][0][0], r'^\d+:\d\d (Loved it|Laughed|Leaned in|Lost|Checked out|Wow)$')
        self.assertRegex(mo['rows'][0][1], r'^[A-Za-z]+, \d+$')
        self.assertEqual(mo['body'], list(sess['scorecard']['notes'])[:2], 'the scorecard notes the old export printed')
        self.assertIn('The room had a handful of beats it would quote later.', mo['body'])
        self.assertEqual(pages.index(mo), kinds.index('image') + 1, 'right after the dial page')
        # what the old export lacked
        card = next(p for p in pages if p['header'] == 'Every question on the card, next to its norm.')
        # the norm named for what it is: no comedy pilot norm yet and the pilot column is still the feature recruited-screening card
        # standing in (Liz Huszarik 2026-11-05), so the card is compared to feature screenings and says so once
        self.assertEqual(card['sub'], 'Compared to feature screenings.')
        self.assertTrue(any(r[2].startswith('Feature screening norm ') for r in card['rows']), 'the norm beside the number, named')
        self.assertEqual(card['body'][-1], 'No comedy norm yet; it fills as tests are filed under comedy, 0 of 5 so far.')
        self.assertTrue(any(h.startswith('If released, ') for h in heads), 'the forecast')
        q1 = next(p for p in pages if p['kind'] == 'question' and 'keep watching' in p['sub'])
        self.assertEqual(q1['knew'][0], 'The show was ordered to series in May.')
        self.assertEqual(q1['talk'][0], 'People online keep posting the diner fight clip.')
        self.assertTrue(any(h.startswith('Mae rates ') or 'Mae' in h for h in heads), 'the characters')
        self.assertEqual(heads[-1], 'Key conclusions and what to do next.')
        self.assertEqual(heads[1], 'What we found, and what to do.')
        tiles = pages[2]
        self.assertTrue(tiles['sub'].startswith('Shares are of the ') and 'people the 12 seats stand for' in tiles['sub'], tiles['sub'])   # what the shares are of, stated (Liz Huszarik 2026-11-05)
        self.assertNotIn('People in the room', tiles['sub'])
        blob = _blob(pages)
        for tok in BANNED:
            self.assertNotIn(tok, blob, tok)
        for tok in BANNED_NEW:
            self.assertNotIn(tok, _blob([ro, mo]), tok)
        for p in pages:
            self.assertLessEqual(len(p['header'].split()), 16, p['header'])
            self.assertTrue(p['header'].endswith('.') or p['kind'] == 'cover', p['header'])

    def test_the_room_page_renders_with_and_without_portraits(self):
        from pptx.enum.shapes import MSO_SHAPE
        twin, sess = _session()
        payload = {'meta': twin['meta']}
        pages = R.report_pages(payload, sess)
        ri = [p['kind'] for p in pages].index('roster')
        from pptx import Presentation
        with_faces = Presentation(io.BytesIO(X.build_pilot_deck(payload, sess, portrait_fn=_portrait_fn)))
        pics = [sh for sh in list(with_faces.slides)[ri].shapes if sh.shape_type == 13]
        self.assertEqual(len(pics), 4, 'the logo and the three faces')
        self.assertEqual(sum(1 for sh in pics if sh.auto_shape_type == MSO_SHAPE.OVAL), 3, 'each face is cropped to a circle')
        no_faces = Presentation(io.BytesIO(X.build_pilot_deck(payload, sess)))
        self.assertEqual(len([sh for sh in list(no_faces.slides)[ri].shapes if sh.shape_type == 13]), 1, 'the logo only; a disc with the initials stands in')
        slide_text = ' '.join(sh.text_frame.text for sh in list(no_faces.slides)[ri].shapes if sh.has_text_frame)
        for r in sess['group']['roster']:
            self.assertIn(r['name'], slide_text)
            self.assertIn(f"{r['age']}, {r['city']}", slide_text)
        self.assertIn('SEGMENT', slide_text)
        self.assertIn('Women under 35 33.3% 1.10M people, 4 seats', slide_text)
        pdf_faces, pdf_plain = X.build_pilot_pdf(payload, sess, portrait_fn=_portrait_fn), X.build_pilot_pdf(payload, sess)
        self.assertEqual(_pdf_pages(pdf_faces), _pdf_pages(pdf_plain))
        self.assertGreater(len(pdf_faces), len(pdf_plain), 'the faces are in the file')
        txt = _pdf_text(pdf_plain)
        self.assertIn('The room seated 12 people, standing for 6.72M.', txt)
        self.assertIn(sess['group']['roster'][0]['name'], txt)
        # a reader that always fails never fails the export
        angry = lambda h: (_ for _ in ()).throw(RuntimeError('down'))  # noqa: E731
        self.assertTrue(X.build_pilot_pdf(payload, sess, portrait_fn=angry)[:4] == b'%PDF')
        self.assertGreater(len(X.build_pilot_deck(payload, sess, portrait_fn=angry)), 20000)

    def test_an_unscored_test_still_exports_in_every_format(self):
        twin, sess = _session(scored=False)
        payload = {'meta': twin['meta']}
        self.assertFalse(sess.get('scorecard'))
        pages = R.report_pages(payload, sess, portrait_fn=_portrait_fn)
        kinds = [p['kind'] for p in pages]
        self.assertEqual(kinds[:2], ['cover', 'bullets'])
        self.assertIn('roster', kinds)
        self.assertNotIn('image', kinds)
        self.assertEqual(len(_slides(X.build_pilot_deck(payload, sess, portrait_fn=_portrait_fn))), len(pages))
        self.assertEqual(_pdf_pages(X.build_pilot_pdf(payload, sess)), len(pages))
        csv = PI.session_csv(sess, payload)
        self.assertIn('Report page,Line,Value,Note', csv)
        self.assertIn('Seat,Name,Age,City,Gender,Segment', csv)

    def test_a_big_room_continues_on_the_next_page(self):
        twin = _beals()
        sess = PI.open_session([twin], title='Big room', in_room=40)
        self.assertEqual(len(sess['group']['roster']), 40)
        payload = {'meta': twin['meta']}
        rp = [p for p in R.report_pages(payload, sess) if p['kind'] == 'roster']
        self.assertEqual([len(p['seats']) for p in rp], [16, 16, 8])
        self.assertRegex(rp[0]['header'], r'^The room seated 40 people, standing for [\d.]+[KM]\.$')
        self.assertTrue(rp[0]['sub'].endswith('Seats 1 to 16 of 40; the rest follow.'), rp[0]['sub'])
        self.assertEqual([p['header'] for p in rp[1:]], ['The room, continued.', 'The room, continued.'])
        self.assertEqual([p['sub'] for p in rp[1:]], ['Seats 17 to 32 of 40.', 'Seats 33 to 40 of 40.'])
        self.assertEqual([s['name'] for p in rp for s in p['seats']], [r['name'] for r in sess['group']['roster']], 'every seat gets a face and a name')
        self.assertTrue(rp[0]['body'][0].endswith(').'), 'the roster line names everyone')
        self.assertEqual(rp[0]['body'][0].count('('), 40)
        slides = _slides(X.build_pilot_deck(payload, sess))
        self.assertEqual(sum(1 for s in slides if 'The room, continued.' in s), 2)
        txt = _pdf_text(X.build_pilot_pdf(payload, sess))
        for r in sess['group']['roster']:
            self.assertIn(r['name'], txt)

    def test_the_deck_passes_the_brand_lint(self):
        try:
            from migration import design_skill as ds
        except Exception:
            self.skipTest('design skill not on this machine')
        if ds.scripts_dir() is None:
            self.skipTest('design pack scripts not on this machine')
        import tempfile
        twin, sess = _session()
        p = os.path.join(tempfile.mkdtemp(), 'report.pptx')
        with open(p, 'wb') as fh:
            fh.write(X.build_pilot_deck({'meta': twin['meta']}, sess, portrait_fn=_portrait_fn))
        rep = ds.brand_lint(p)
        step = next((s for s in rep.steps if s[0] == 'brand_lint'), None)
        self.assertIsNotNone(step)
        self.assertTrue(step[1], step[2][-1500:])


class Csv(unittest.TestCase):
    def test_the_csv_is_a_complete_record_of_the_test(self):
        twin, sess = _session()
        payload = {'meta': twin['meta']}
        csv = PI.session_csv(sess, payload)
        lines = csv.splitlines()
        # the test line names the norm it is compared to
        self.assertTrue(lines[0].endswith(',Favorite moment,Dead spot,Compared to,Norm note,Shares are of'), lines[0])
        self.assertIn(',Feature screenings,"No pilot norm yet; it fills as pilot tests are filed, 0 of 5 so far. No comedy norm yet; it fills as tests are filed under comedy, 0 of 5 so far.",', lines[1])
        self.assertIn(',Seats,People,', lines[0])
        self.assertRegex(lines[1], r',\d+ of 12,')        # the seat tally beside the shares
        # the scorecard: every measure with the norm it is read against (the feature screening card, until pilot tests accumulate) and the chosen norm beside it
        self.assertIn('Question,Answer,Share %,People,In the room,Feature screening norm %,Comedy pilots norm %', lines)
        self.assertTrue(any(l.startswith('Overall rating,Excellent,') and l.count(',') == 6 and l.split(',')[5] == '30.0' for l in lines), 'the excellent row with its norm columns')
        self.assertTrue(any(',Total positive,' in l and ',65.0,' in l for l in lines), 'total positive against the 65 norm')
        import csv as _csv
        rows = [row for row in _csv.reader(io.StringIO(csv)) if row]
        self.assertTrue(any(row[0] == 'Note' and row[1].startswith('Definitely recommend ') and 'norm of 50%' in row[1] for row in rows), 'the norm lines')
        # the characters
        self.assertIn('Character,Role,Excellent %,Total positive %,Feature screening norm %,Vs norm pp,On screen,Screen time against the rating,Would follow into a second episode %', lines)
        self.assertTrue(any(l.startswith('Mae,lead,') for l in lines))
        self.assertTrue(any(l.startswith('Ray,second lead,') for l in lines))
        self.assertTrue(any(l.startswith('Mae and Ray,relationship,') for l in lines))
        self.assertTrue(any(l.startswith('Characters read,') for l in lines))
        # the components, pace and scenes
        self.assertIn('Component,Excellent %,Feature screening norm %,Comedy pilots norm %,Vs norm pp', lines)
        self.assertTrue(any(l.startswith('Humor,') for l in lines))
        self.assertIn('Stretch,From,To,Too slow %,Just right %,Too fast %,Excellent %,Line', lines)
        self.assertTrue(any(l.startswith('Beginning,0:00,') for l in lines))
        self.assertTrue(any(l.startswith('Confused somewhere,') for l in lines))
        self.assertIn('Scenes,Clock,On screen,Mentions,People,In their words', lines)
        self.assertTrue(any(l.startswith('Liked best,') for l in lines))
        self.assertTrue(any(l.startswith('Liked least,') for l in lines))
        # the forecast tiles, the recruit and the room
        self.assertIn('Forecast,Value,Low,High,Definition', lines)
        self.assertTrue(any(l.startswith('Week-one audience if released,') and l.split(',')[1].isdigit() for l in lines))
        self.assertTrue(any(l.startswith('Season completion %,') for l in lines))
        self.assertIn('Recruit,Value', lines)
        self.assertTrue(any(l.startswith('Recruited to,') for l in lines))
        self.assertIn('Walked out,0', lines)
        self.assertTrue(any(l.startswith('Quota: women,') for l in lines))
        self.assertIn('Segment,Share of room %,People,Seats', lines)
        self.assertTrue(any(l.startswith('Women under 35,33.3,') and l.endswith(',4') for l in lines), 'the share of the room as seats')
        self.assertIn('Seat,Name,Age,City,Gender,Segment', lines)
        r0 = sess['group']['roster'][0]
        self.assertIn(f"1,{r0['name']},{r0['age']},{r0['city']},{r0['gender_label']},{r0['segment']}", lines)
        # the questions: the read, the voices, the split, what the room knew and what people were saying
        self.assertIn('Clock,Question,Segment,Answer,Share %,People,In the room', lines)
        self.assertIn('5:10,Would you keep watching after this scene?,Whole room,Yes,58.3,4812311,7 of 12', lines)
        self.assertIn('5:10,Would you keep watching after this scene?,What the room knew,The show was ordered to series in May.', lines)
        self.assertIn('5:10,Would you keep watching after this scene?,What people were saying,People online keep posting the diner fight clip.', lines)
        self.assertTrue(any(',I want to see her win the town back.' in l for l in lines))
        # the report, page by page: every page header, the TLDR lines and the conclusions
        self.assertIn('Report page,Line,Value,Note', lines)
        pages = R.report_pages(payload, sess, with_chart=False)
        first_cells = [row[0] for row in rows]
        for p in pages[1:]:
            self.assertIn(p['header'], first_cells, p['header'])
        seconds = [row[1] for row in rows if len(row) > 1]
        for b in pages[1]['body']:
            self.assertIn(b, seconds, b)
        for b in pages[-1]['body']:
            self.assertIn(b, seconds, b)
        self.assertIn('Compared to feature screenings.', seconds)
        self.assertIn('Every question on the card, next to its norm.', first_cells)
        self.assertTrue(any(c.startswith('If released, ') for c in first_cells), 'the forecast page')
        self.assertIn('The moments that moved the room, seat by seat.', first_cells)
        self.assertIn('The room seated 12 people, standing for 6.72M.', first_cells)
        # the dial and every reaction still ride along; nothing a reader should not see
        self.assertIn('Clock,Seconds,Type,Twin,Age,City,Line,Intensity', lines)
        low = csv.lower()
        for tok in BANNED:
            self.assertNotIn(tok, low, tok)

    def test_the_csv_without_a_payload_names_the_twin_from_the_room(self):
        twin, sess = _session()
        csv = PI.session_csv(sess)
        self.assertIn('Report page,Line,Value,Note', csv)
        self.assertIn('The room seated 12 people, standing for 6.72M.', csv)
        self.assertEqual(PI._csv_payload_for(sess)['meta']['name'], 'Jennifer Beals')

    def test_the_report_block_follows_the_page_list(self):
        """A page another builder adds to report_pages lands in the CSV with no change here."""
        twin, sess = _session()
        payload = {'meta': twin['meta']}
        orig = R.report_pages

        def with_extra(p, s, portrait_fn=None, with_chart=True):
            pages = orig(p, s, portrait_fn=portrait_fn, with_chart=with_chart)
            pages.insert(-1, {'kind': 'table', 'header': 'What the room said about each name on the card.', 'sub': 'One line per person.',
                              'head': ('Person', 'Excellent', 'In the room'), 'rows': [('Mae', '59.0%', 'carries the hour')], 'body': ['A line the builder added.'], 'dark': True})
            return pages
        try:
            R.report_pages = with_extra
            csv = PI.session_csv(sess, payload)
        finally:
            R.report_pages = orig
        self.assertIn('What the room said about each name on the card.,One line per person.,,', csv)
        self.assertIn(',Person,Excellent,In the room', csv)
        self.assertIn(',Mae,59.0%,carries the hour', csv)
        self.assertIn(',A line the builder added.,,', csv)


class Buttons(unittest.TestCase):
    def _index(self) -> str:
        with open(os.path.join(ROOT, 'templates', 'index.html'), 'r', encoding='utf-8') as fh:
            return fh.read()

    def test_the_test_page_offers_one_report_per_format(self):
        src = self._index()
        row = next(l for l in src.splitlines() if 'dtwPilotDuplicate()" title="Duplicate this test">Duplicate this test</button>' in l and '/report.pptx' in l)
        for frag in ("'/pilot/' + esc(s.id) + '/report.pptx\"", "'/pilot/' + esc(s.id) + '/report.pdf\"", "'/pilot/' + esc(s.id) + '/export.csv\""):
            self.assertIn(frag, row, frag)
        self.assertIn('>Report (deck)</a>', row)
        self.assertIn('>Report (PDF)</a>', row)
        self.assertIn('>CSV</a>', row)
        self.assertNotIn("s.scorecard ?", row, 'the report is there for every test, as the older export was')
        # the older PDF and Deck buttons that pointed at a thinner export are gone from the test page
        self.assertNotIn("'/pilot/' + esc(s.id) + '/export.pdf'", src)
        self.assertNotIn("'/pilot/' + esc(s.id) + '/export.pptx'", src)
        self.assertNotIn('/export.pdf">PDF</a><a class="dtw-btn" href="/api/digital-twins/\' + esc(S.key) + \'/pilot/', src)

    def test_the_routes_pass_the_faces_and_the_payload(self):
        with open(os.path.join(ROOT, 'digital_twins_api.py'), 'r', encoding='utf-8') as fh:
            api = fh.read()
        self.assertIn('X.build_pilot_pdf(payload, sess, portrait_fn=_pilot_portrait_fn())', api)
        self.assertIn('X.build_pilot_deck(payload, sess, portrait_fn=_pilot_portrait_fn())', api)
        self.assertIn('R.build_report_pdf(payload, sess, portrait_fn=portrait_fn)', api)
        self.assertIn('R.build_report_deck(payload, sess, portrait_fn=portrait_fn)', api)
        self.assertIn('PI.session_csv(sess, payload)', api)

    def test_the_partner_route_passes_the_faces(self):
        """A key caller's report (/api/v1/screenings/<id>/report.pdf | .pptx)
        showed initials on the room page: the route built the report with
        no portrait reader (flagged 2026-11-04). It now passes the same
        reader the dashboard route uses, with the same fallback to
        initials when a face is missing."""
        with open(os.path.join(ROOT, 'digital_twins_partner_api.py'), 'r', encoding='utf-8') as fh:
            api = fh.read()
        self.assertIn('portrait_fn = _portrait_fn()', api)
        self.assertIn('R.build_report_pdf(payload, sess, portrait_fn=portrait_fn)', api)
        self.assertIn('R.build_report_deck(payload, sess, portrait_fn=portrait_fn)', api)
        self.assertNotIn('R.build_report_pdf(payload, sess)', api)
        self.assertNotIn('R.build_report_deck(payload, sess)', api)
        try:
            import digital_twins_api as A
            import digital_twins_partner_api as P
        except Exception as e:
            self.skipTest(f'the api modules need flask: {e}')
        faces = {'face0': _jpeg(60)}
        reads = []

        class FakePT:
            THUMB_SIZE = 128
            OUT_SIZE = 512

            @staticmethod
            def read_portrait(s3, h, size=512):
                reads.append((s3, h, size))
                return faces.get(h)
        s3_marker = object()
        keep = (A._portraits, A._s3)
        try:
            A._portraits, A._s3 = (lambda: FakePT), (lambda: s3_marker)
            fn = P._portrait_fn()
            self.assertTrue(callable(fn), 'the partner route hands the builders a portrait reader')
            self.assertEqual(fn('face0'), faces['face0'])
            self.assertIsNone(fn('face9'), 'a seat with no face comes back empty, and the room page draws its initials')
            self.assertEqual(reads, [(s3_marker, 'face0', 128), (s3_marker, 'face9', 128)], 'the thumb from the store, through the same reader the dashboard uses')
            A._portraits = lambda: None
            self.assertIsNone(P._portrait_fn(), 'portraits off: the builders get None and draw initials')
        finally:
            A._portraits, A._s3 = keep


if __name__ == '__main__':
    unittest.main(verbosity=2)
