#!/usr/bin/env python3
"""Focus groups calibrate to published performance and never say so (Jenna 2026-11-05).

Jenna, verbatim: "for focus groups. don;t ever say this on the output: Calibrated to NCIS: New York's first 2
episodes as aired. but if there is published data about something (like it already aired) you can calibrate to tht
just don't source it. so if I put in an episode of an old show and you can get neilsen on it grteat to calibrate to
but just dont say it does this make sense"

What holds here:
  * the guard (migration/digital_twin_copy): a calibrated / anchored / benchmarked / as aired / ratings-service
    sentence is dropped, a 'per Variety' or 'according to Nielsen' clause comes off and the fact stays, the user's
    own 'Compared to <norm>' line is untouched
  * a room read, a pilot scorecard (the page's view, the short read), a report page, the CSV export and a team
    notice each carry none of it on a fixture whose anchors are populated
  * the anchors stay in the stored record (the forecast's calibration block, its recorded actual, the ledger)
  * an old episode fed as a cut triggers the aired-performance anchor path: one research call, the audience on the
    ledger as the session's own record, the forecast calibrated to it, nothing rendered about it

Run:  REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_no_calibration_language.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from migration import digital_twin_copy as C  # noqa: E402
from migration import digital_twin_forecast as F  # noqa: E402
from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twin_report as R  # noqa: E402
from migration import digital_twin_room as ROOM  # noqa: E402
from prometheus import twin_notify as TN  # noqa: E402
from test_digital_twin_forecast import _S3, _sess, _twin  # noqa: E402
from test_twin_notify import Sender, _scored, _store  # noqa: E402

SENTENCE = "Calibrated to NCIS: New York's first 2 episodes as aired."
# what a reader must never meet: the sentence, its words, a ratings service, a publication as a source
BANNED_RX = re.compile(r"calibrat|anchored|benchmark|as aired|aired episode|actuals|nielsen|comscore|samba tv|luminate|parrot analytics|"
                       r"ratings service|published figures|published numbers|per variety|according to variety|per deadline|the ledger", re.I)


def _strings(o) -> list:
    """Every string in a nested page list (the chart bytes are not prose)."""
    if isinstance(o, str):
        return [o]
    if isinstance(o, (list, tuple)):
        return [s for x in o for s in _strings(x)]
    if isinstance(o, dict):
        return [s for v in o.values() for s in _strings(v)]
    return []


def _anchored_fixture():
    """A twin and a scored pilot on it with the ledger holding two aired episodes of the show, the forecast
    carrying the stale sentence in its notes and a recorded actual: the record as it stood before the rule."""
    s3 = _S3()
    tw = _twin()
    pilot = _sess('p1', 'NCIS: New York Pilot', 19.3, 1.6, 81.9)
    ep2 = _sess('e2', 'NCIS: New York Episode 2', 38.1, 30.0, 91.8, mode='episode', genre='')
    tw['pilot_sessions'] = [pilot, ep2]
    led = F.record_actual(s3, tw, pilot, people=10_250_000, measure='week-one audience, every screen', aired='2026-10-06', episode=1)
    led = F.record_actual(s3, tw, ep2, people=8_430_000, measure='week-one audience, every screen', aired='2026-10-13', episode=2, ledger=led)
    fc = F.forecast_for_session(tw, pilot, ledger=led, actual={'people': 10_250_000, 'measure': 'week-one audience, every screen', 'aired': '2026-10-06'},
                                notes=[SENTENCE])
    fc['board_notes'] = ['On our board it climbed.', SENTENCE]
    pilot['forecast'] = fc
    pilot['scorecard']['notes'] = ['The room had a handful of beats it would quote later.', SENTENCE]
    pilot['scorecard']['norm_lines'] = ['Compared to all pilots.', 'Against the all-pilots norm, total positive sits nine points high.']
    return s3, tw, pilot, led


class Guard(unittest.TestCase):
    def test_the_sentence_never_survives(self):
        self.assertEqual(C.strip_calibration_language(SENTENCE), '')
        self.assertEqual(C.strip_calibration_language('Week one drew 4.1M people. ' + SENTENCE + ' The season holds.'), 'Week one drew 4.1M people. The season holds.')
        for s in ('Calibrated to 2 aired episodes on Paramount+.', 'Calibrated to 7 aired episodes across the library.',
                  'No aired episode to compare against yet. The forecast sits at the low end and calibrates as actuals come in.',
                  'Our read is anchored to the first week.', 'Benchmarked to the premiere.', 'Week-one audience as aired: 10,250,000.',
                  'Nielsen had week one at 4.1M.', 'The ratings service put it at 3.9M.', 'The ledger carries two episodes.'):
            self.assertEqual(C.strip_calibration_language(s), '', s)
            self.assertTrue(C.calibration_hits(s), s)

    def test_a_source_clause_comes_off_and_the_fact_stays(self):
        self.assertEqual(C.strip_calibration_language('According to Nielsen, week one drew 4.1M viewers.'), 'Week one drew 4.1M viewers.')
        self.assertEqual(C.strip_calibration_language('Week one drew 4.1M viewers, per Variety.'), 'Week one drew 4.1M viewers.')
        self.assertEqual(C.strip_calibration_language('The record had week one at 4.1M (Nielsen).'), 'The record had week one at 4.1M.')
        self.assertEqual(C.strip_calibration_language('The published split sits on the count (Mamdani 50.4%, Cuomo 41.6%; called).'),
                         'The split sits on the count (Mamdani 50.4%, Cuomo 41.6%; called).')
        self.assertEqual(C.strip_calibration_language('The published split leans on the latest published numbers (46%, as of Nov 2) two to one over the seats.'),
                         'The split leans on the latest numbers (46%, as of Nov 2) two to one over the seats.')

    def test_the_users_own_comparison_and_plain_prose_stay(self):
        for s in ('Compared to procedural pilots.', 'Compared to all pilots.', 'Against the all-pilots norm, total positive sits nine points high.',
                  'The news anchor carried the show.', 'The numbers say the room liked it.', 'The episode aired on a Tuesday and the room watched it cold.',
                  'An early read on the title, at the low end of its range.'):
            self.assertEqual(C.strip_calibration_language(s), s, s)
            self.assertEqual(C.calibration_hits(s), [], s)

    def test_lines_and_nested_records(self):
        self.assertEqual(C.strip_calibration_lines(['Keep this.', SENTENCE, '', None, 'And this.']), ['Keep this.', 'And this.'])
        self.assertEqual(C.strip_calibration_language('Line one\n' + SENTENCE + '\nLine three'), 'Line one\n\nLine three')
        self.assertEqual(C.strip_calibration_in({'a': [SENTENCE, 'Fine.'], 'b': {'c': SENTENCE}}), {'a': ['Fine.'], 'b': {'c': ''}})


class Forecast(unittest.TestCase):
    def test_a_calibrated_forecast_carries_no_note_and_keeps_its_anchors(self):
        s3, tw, pilot, led = _anchored_fixture()
        fc = F.forecast_for_session(tw, pilot, ledger=led, actual={'people': 10_250_000, 'measure': 'week-one audience, every screen'})
        self.assertEqual(fc['calibration']['scope'], 'show')
        self.assertEqual(fc['calibration']['n'], 2)
        self.assertEqual(len(fc['calibration']['anchors']), 2, 'the anchors stay in the record')
        self.assertEqual(fc['actual']['week_one_audience'], 10_250_000, 'the recorded actual stays in the record')
        self.assertEqual(fc['notes'], [], 'nothing says what the forecast was set against')
        # a caller's own note with the sentence in it is stripped on the way in
        fc2 = F.forecast_for_session(tw, pilot, ledger=led, notes=[SENTENCE, 'Keep this line.'])
        self.assertEqual(fc2['notes'], ['Keep this line.'])
        none = F.forecast_for_session(tw, pilot, ledger=F._empty_ledger())
        self.assertEqual(none['notes'], [F.NO_ANCHOR_NOTE])
        self.assertFalse(BANNED_RX.search(F.NO_ANCHOR_NOTE))

    def test_public_forecast_drops_the_audit_and_cleans_the_notes(self):
        s3, tw, pilot, led = _anchored_fixture()
        pub = F.public_forecast(pilot['forecast'])
        self.assertNotIn('calibration', pub)
        self.assertNotIn('actual', pub)
        self.assertEqual(pub['board_notes'], ['On our board it climbed.'])
        self.assertFalse(BANNED_RX.search(json.dumps(pub)), json.dumps(pub)[:400])
        # a copy: the stored forecast keeps everything
        self.assertIn('calibration', pilot['forecast'])
        self.assertIn('actual', pilot['forecast'])
        self.assertIn(SENTENCE, pilot['forecast']['board_notes'])


class Surfaces(unittest.TestCase):
    """Each place a reader meets the forecast, on the anchored fixture."""

    def test_room_read_scrub_and_audit(self):
        read = 'Most of the room stays with it. ' + SENTENCE + ' The three who are not sure want one more scene.'
        out = ROOM.scrub(read)
        self.assertEqual(out, 'Most of the room stays with it. The three who are not sure want one more scene.')
        self.assertIn('Calibrated', ROOM.has_banned(read))
        self.assertEqual([h for h in ROOM.has_banned(out) if h != 'dash'], [])
        # the media desk's own anchor line, as the moderator might echo it: the split stays, the sourcing goes
        self.assertEqual(ROOM.scrub('The published split sits on the count (Yes 58.3%, No 16.3%; called), per Nielsen.'),
                         'The split sits on the count (Yes 58.3%, No 16.3%; called).')

    def test_pilot_page_view_short_read_and_banned_audit(self):
        s3, tw, pilot, led = _anchored_fixture()
        pub = PI.public_session(pilot)
        self.assertNotIn('calibration', pub['forecast'])
        self.assertNotIn('actual', pub['forecast'])
        self.assertEqual(pub['forecast']['notes'], [])
        self.assertEqual(pub['forecast']['board_notes'], ['On our board it climbed.'])
        self.assertEqual(pub['forecast']['episode']['week_one_audience'], pilot['forecast']['episode']['week_one_audience'], 'the numbers are untouched')
        # the stored session is not what the page got
        self.assertIn('calibration', pilot['forecast'])
        lines = PI.session_lines(pilot)
        self.assertTrue(any(l == 'Compared to all pilots.' for l in lines), lines)
        self.assertIn('The room had a handful of beats it would quote later.', lines)
        self.assertFalse(BANNED_RX.search('\n'.join(lines)), lines)
        self.assertIn('Calibrated', PI.has_banned_copy(SENTENCE))
        self.assertEqual([h for h in PI.has_banned_copy('\n'.join(lines)) if h != 'dash'], [])

    def test_csv_export(self):
        s3, tw, pilot, led = _anchored_fixture()
        csv = PI.session_csv(pilot, {'meta': tw['meta']})
        self.assertIn('Week-one audience if released', csv)
        self.assertIn('Compared to all pilots.', csv, 'the user\'s own comparison stays')
        self.assertNotIn('Week-one audience as aired', csv)
        self.assertNotIn('10250000', csv.replace(',', ''), 'the recorded actual is audit, never a row')
        self.assertFalse(BANNED_RX.search(csv), [l for l in csv.splitlines() if BANNED_RX.search(l)])

    def test_report_pages(self):
        s3, tw, pilot, led = _anchored_fixture()
        pages = R.report_pages({'meta': tw['meta']}, pilot)
        page = next(p for p in pages if p['kind'] == 'tiles' and 'first week' in p['header'])
        blob = '\n'.join(_strings(pages))
        self.assertFalse(BANNED_RX.search(blob), [l for l in blob.splitlines() if BANNED_RX.search(l)])
        self.assertNotIn('As aired', blob)
        self.assertTrue(any('Range' in b for b in page['body']), page['body'])
        # the report's own copy cleaner is the guard too
        self.assertEqual(R._clean('Week one drew 4.1M. ' + SENTENCE), 'Week one drew 4.1M.')

    def test_team_notice(self):
        s3 = _store()
        send = Sender()
        tw, sess = _scored(s3)
        led = F.record_actual(s3, tw, sess, people=10_250_000, measure='week-one audience, every screen', aired='2026-10-06', episode=1)
        fc = F.forecast_for_session(tw, sess, ledger=led, actual={'people': 10_250_000, 'measure': 'week-one audience, every screen', 'aired': '2026-10-06'})
        fc['notes'] = [SENTENCE]
        fc['board_notes'] = ['On our board it climbed. ' + SENTENCE]
        sess['forecast'] = fc
        self.assertEqual(fc['calibration']['scope'], 'show')
        rec = TN.notify_pilot_results(tw, sess, username='jenna', s3=s3, send=send, with_deck=False)
        self.assertTrue(rec['sent'], rec)
        mail = send.sent[-1]
        text = re.sub(r'https?://\S+', ' ', mail['subject'] + '\n' + mail['body'])
        self.assertIn('Week-one audience if released', text)
        self.assertNotIn('As aired', text)
        self.assertNotIn('10,250,000', text)
        self.assertFalse(BANNED_RX.search(text), [l for l in text.splitlines() if BANNED_RX.search(l)])
        self.assertEqual(TN.banned_tokens(mail['subject'] + '\n' + mail['body']), [])
        # the audit names the sentence when it is fed one, and the send path strips it before the audit runs
        self.assertIn('calibrated', TN.banned_tokens(SENTENCE))
        # the stored record still holds the anchors
        self.assertIn('calibration', sess['forecast'])
        self.assertEqual(sess['forecast']['actual']['week_one_audience'], 10_250_000)

    def test_partner_scrub(self):
        import digital_twins_partner_api as PAPI
        self.assertEqual(PAPI.scrub_text('Total positive sits nine points high. ' + SENTENCE), 'Total positive sits nine points high.')


class AiredCut(unittest.TestCase):
    """An old episode fed as a cut anchors to its aired performance, silently."""

    def setUp(self):
        ROOM.set_model_fn(None)

    def tearDown(self):
        ROOM.set_model_fn(None)

    def test_an_old_episode_cut_anchors_to_what_it_did(self):
        calls = []

        def fake(role, system, user, **kw):
            calls.append((role, json.loads(user)))
            return {'aired': True, 'aired_on': '2005-10-04', 'week_one_audience': 15_230_000, 'measure': 'viewers of the first broadcast, live plus seven',
                    'source_class': 'Nielsen', 'note': 'The episode first aired on CBS on October 4, 2005.'}
        s3 = _S3()
        tw = _twin(name='CBS')
        sess = _sess('old1', 'NCIS Season 3, Episode 2', 44.0, 31.0, 90.0, mode='episode', genre='procedural')
        sess['media'].update({'show': 'NCIS', 'season': 3, 'episode': 2})
        fc = F.attach_forecast(tw, sess, s3=s3, model_fn=fake)
        self.assertEqual(len(calls), 1, 'one research call')
        self.assertEqual(calls[0][0], 'research')
        self.assertEqual(calls[0][1]['title'], 'NCIS')
        self.assertEqual(calls[0][1]['episode'], 'Season 3, Episode 2')
        # the ledger holds the episode as this session's own anchor; the forecast calibrates to it
        led = F.load_ledger(s3)
        rec = next(r for r in led['records'] if r['session_id'] == 'old1')
        self.assertEqual(rec['actual'], 15_230_000)
        self.assertEqual(rec['aired'], '2005-10-04')
        self.assertEqual(fc['calibration']['scope'], 'show')
        self.assertEqual(fc['calibration']['n'], 1)
        self.assertEqual(fc['actual']['week_one_audience'], 15_230_000)
        self.assertLess(abs(fc['actual']['forecast_vs_actual_pct']), 1.0, 'a single anchor on the show itself lands the forecast on it')
        self.assertNotIn('uncalibrated', F.public_forecast(fc)['episode'])
        # nothing a reader sees says so
        self.assertEqual(fc['notes'], [])
        pub = F.public_forecast(fc)
        self.assertNotIn('actual', pub)
        self.assertNotIn('calibration', pub)
        self.assertFalse(BANNED_RX.search(json.dumps(pub)))
        self.assertFalse(BANNED_RX.search('\n'.join(PI.session_lines(sess))))
        # a re-read does not ask again: the ledger record is the session's anchor
        F.attach_forecast(tw, sess, s3=s3, model_fn=fake)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(F.load_ledger(s3)['records']), 1)

    def test_a_title_that_has_not_aired_is_remembered_and_the_forecast_stays_early(self):
        calls = []

        def fake(role, system, user, **kw):
            calls.append(role)
            return {'aired': False, 'aired_on': None, 'week_one_audience': None, 'measure': '', 'source_class': '', 'note': 'Not yet released.'}
        s3 = _S3()
        tw = _twin()
        sess = _sess('new1', 'Second Helping Pilot', 40.0, 30.0, 88.0)
        fc = F.attach_forecast(tw, sess, s3=s3, model_fn=fake)
        self.assertEqual(fc['calibration']['scope'], 'none')
        self.assertEqual(fc['notes'], [F.NO_ANCHOR_NOTE])
        led = F.load_ledger(s3)
        self.assertEqual(led['records'], [])
        self.assertIn('new1', led.get('checks') or {})
        self.assertIs(led['checks']['new1']['aired'], False)
        F.attach_forecast(tw, sess, s3=s3, model_fn=fake)
        self.assertEqual(calls, ['research'], 'asked once inside the week')
        # a trailer or a title card is not a title that can have aired
        sess2 = _sess('tr1', 'Second Helping teaser', 40.0, 30.0, 88.0, mode='trailer')
        F.attach_forecast(tw, sess2, s3=s3, model_fn=fake)
        self.assertEqual(calls, ['research'])

    def test_no_model_means_no_research_and_the_old_behaviour(self):
        s3 = _S3()
        tw = _twin()
        sess = _sess('h1', 'NCIS: New York Pilot', 19.3, 1.6, 81.9)
        fc = F.attach_forecast(tw, sess, s3=s3)
        self.assertEqual(fc['calibration']['scope'], 'none')
        self.assertNotIn(F.LEDGER_KEY, s3.store, 'a hermetic run writes nothing')


class Sources(unittest.TestCase):
    def test_no_producer_of_the_sentence_is_left_in_code(self):
        """The forecast module once wrote 'Calibrated to <show>'s first N episodes as aired.' into the notes; the
        renderers once printed 'As aired:' lines. Neither string is produced anywhere the reader could meet it."""
        rx = re.compile(r"Calibrated to \{|\"Calibrated to|'Calibrated to|As aired: |Week-one audience as aired", re.I)
        for rel in ('migration/digital_twin_forecast.py', 'migration/digital_twin_pilot.py', 'migration/digital_twin_report.py',
                    'migration/digital_twin_exports.py', 'migration/digital_twin_room.py', 'migration/digital_twin_norms.py',
                    'prometheus/twin_notify.py', 'digital_twins_api.py', 'digital_twins_partner_api.py'):
            with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
                src = fh.read()
            self.assertIsNone(rx.search(src), rel)
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            html = fh.read()
        self.assertNotIn("'As aired: <b>'", html)
        self.assertNotIn("', aired ' + esc(piPeopleShort(act.week_one_audience))", html)
        self.assertIn('function piReaderNotes(', html)
        self.assertIn('piReaderNotes(fc.notes)', html)


if __name__ == '__main__':
    unittest.main()
