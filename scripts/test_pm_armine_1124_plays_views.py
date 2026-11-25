"""Plays, views and unique viewers on a title read (Armine at Lionsgate,
2026-11-24; Jenna: "check on armine's recent asks and make sure it's
working for her").

The live shapes, sanitized: six title reads on a free, ad-supported
shelf each carried unique US viewers, total views and total plays as
three free figures (five with views above plays, one the other way);
then "how is the play calculated", "what are scenarios if views is
higher than plays and plays is higher than views", "How is the watch
threshold for a qualified view defined?" and "why are the views higher
than plays" drew three different model-written definitions in one hour,
and the last one ran as a read on "Views vs Plays" whose metrics block
carried the same title's figures from another service under the
YouTube label, pinned "Views vs Plays" as the thread's subject and
quoted a $300 profile of it.

The rules under test:

* methodology_answers: those questions are one house answer
  (prometheus.methodology.PLAYS_VIEWS_PART), through the service door on
  the methodology lane: no model, no read, no quote. A question that
  asks for a figure, a split or a ranking stays with the lanes.
* viewing_counts.cohere: on a read with a unique-viewer count and a plays
  count, a views metric is the unique viewer count (views are people),
  plays sit at or above viewers, a 30-seconds-or-more figure at or below
  plays, and the ratios follow. A social read with no plays is untouched.
* figure_binding: a stored metric binds only on the same carrier.
* thread_subject: a pin that is only metric words never binds.
* methodology.MODEL_RULES carries the rule for the model.

Pure tests on a stub host; no network, no model.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, 'pm_corpus'))

from prometheus import figure_binding as fb  # noqa: E402
from prometheus import methodology as meth  # noqa: E402
from prometheus import methodology_answers as ma  # noqa: E402
from prometheus import thread_subject as ts  # noqa: E402
from prometheus import viewing_counts as vc  # noqa: E402

READ = ("On Including U.S. Unique viewers:\n\nHarbor Lights drew 29,463 unique US viewers on YouTube's Free with ads shelf between "
        "Nov 1 and Nov 9 2026, against 38,219 plays and a median completion of 31.4%.\n\n"
        "Unique US viewers, YouTube Free with ads: 29,463, Total US views, YouTube Free with ads: 46,137, Total US plays, YouTube "
        "Free with ads: 38,219.\n\nDay of availability mix, Nov 1 2026 to Nov 9 2026 (share of the window's 38,219 US plays on "
        "YouTube Free with ads)\n- Sun Nov 8 2026: 14.3% share. peak day of the window\n- Sat Nov 7 2026: 13.6% share.")
ASK_PLAY = "how is the play calculated"
ASK_WHY = "why are the views higher than plays"
ASK_SCEN = "what are scenarios if views is higher than plays and plays is higher than views"
ASK_THRESH = "How is the watch threshold for a qualified view defined?"


def _history():
    return [
        {"role": "user", "ts": "2026-11-24T02:10:00Z", "text": ("This is for the title Harbor Lights with the window of 11/1/26-11/9/26. We're looking to size "
                                                                 "the potential U.S. audience and viewership specifically on YouTube for the titles and availability windows below. "
                                                                 "These titles were available through YouTube's Movies & TV Free with ads section. If possible, we'd like to "
                                                                 "understand title-level performance during its specific window, including U.S. unique viewers, total views, and plays.")},
        {"role": "agent", "ts": "2026-11-24T02:10:27Z", "text": "Included with your plan. On it: reading Harbor Lights and YouTube now. This one takes a real look at the data, so give me a moment - the read will land right here when it is ready."},
        {"role": "agent", "ts": "2026-11-24T02:12:00Z", "text": READ, "meta": {"kind": "read", "read_job_id": "jobhhhh111111", "subject": "Harbor Lights"}},
        {"role": "user", "ts": "2026-11-24T02:12:11Z", "text": "Email me this file"},
        {"role": "agent", "ts": "2026-11-24T02:12:23Z", "text": "Sent Harbor Lights Day of availability.csv to the address on your seat.", "meta": {"kind": "email_file"}},
    ]


class TheQuestion(unittest.TestCase):
    def test_the_four_live_asks_are_one_house_answer(self):
        for q in (ASK_PLAY, ASK_WHY, ASK_SCEN, ASK_THRESH, "what is a play", "views vs plays, what's the difference",
                  "difference between unique viewers and views", "how do you count plays", "how can views be higher than plays?"):
            self.assertEqual(ma.shape_of(q), 'plays_views', q)
            a = ma.answer(q)
            self.assertTrue(a and a.get('methodology'), q)
            self.assertEqual(a['reply'], meth.PLAYS_VIEWS_PART, q)

    def test_a_figure_ask_stays_with_the_lanes(self):
        for q in ("how many plays did Harbor Lights get on YouTube", "break the plays out by day", "which title had the most plays",
                  "top 5 titles by plays", "why are the views higher for Moonfall than for Harbor Lights",
                  "can you pull plays and views for Harbor Lights on YouTube", "Build a profile of Harbor Lights viewers"):
            self.assertFalse(ma.is_plays_views_question(q), q)
            self.assertNotEqual(ma.shape_of(q), 'plays_views', q)
        self.assertEqual(ma.shape_of("how do you count a view"), 'view_definition')     # the Nielsen and Peacock part keeps its own shape

    def test_the_house_words(self):
        r = meth.PLAYS_VIEWS_PART
        self.assertIn('What we count as a play:', r)
        self.assertIn('What we count as a view:', r)
        self.assertIn('the same count as unique viewers', r)
        self.assertIn('plays are always at or above viewers', r)
        # the separate measure is plays of 30 seconds or more: a count of starts, never people, never views (Jenna 2026-11-24)
        self.assertIn('the plays of 30 seconds or more are shown beside it', r)
        self.assertIn('a count of starts', r)
        self.assertNotIn('people whose play', r)
        self.assertNotIn('30 seconds or more view', r)
        for banned in ('\u2014', '\u2013', 'synth', 'model', 'panel', 'estimate', 'sample', 'Nielsen', 'YouTube'):
            self.assertNotIn(banned, r, banned)
        self.assertIn('never report a separate views total', meth.MODEL_RULES)
        self.assertIn('plays are always at or above unique viewers', meth.MODEL_RULES)
        self.assertIn('the plays of 30 seconds or more (a count of starts, never people, never called views)', meth.MODEL_RULES)
        self.assertNotIn('people whose play ran 30 seconds', meth.MODEL_RULES)


class TheCounts(unittest.TestCase):
    def _mets(self, u, v, p, extra=()):
        out = [{'name': 'unique_us_viewers', 'label': 'Unique US viewers', 'value': u, 'unit': 'count'},
               {'name': 'total_us_views', 'label': 'Total US views', 'value': v, 'unit': 'count'},
               {'name': 'total_us_plays', 'label': 'Total US plays', 'value': p, 'unit': 'count'}]
        out.extend(extra)
        return out

    def test_views_are_the_unique_viewer_count(self):
        res = {'subject': 'Harbor Lights', 'metrics': self._mets(29463, 46137, 38219)}
        notes = vc.cohere(res)
        self.assertEqual(len(notes), 1, notes)
        vals = {m['name']: m['value'] for m in res['metrics']}
        self.assertEqual(vals['total_us_views'], 29463)
        self.assertEqual(vals['total_us_plays'], 38219)
        self.assertIn('same count as unique viewers', res['metrics'][1]['definition'])

    def test_plays_above_views_above_viewers_also_holds(self):
        extra = [{'name': 'plays_per_viewer', 'label': 'Plays per unique viewer', 'value': 2, 'unit': 'ratio'}]
        res = {'subject': 'Night Market', 'metrics': self._mets(310671, 449959, 484478, extra)}
        vc.cohere(res)
        vals = {m['name']: m['value'] for m in res['metrics']}
        self.assertEqual(vals['total_us_views'], 310671)
        self.assertEqual(vals['total_us_plays'], 484478)
        self.assertEqual(vals['plays_per_viewer'], 1.56)

    def test_a_thirty_second_figure_is_plays_never_views(self):
        extra = [{'name': 'qualified_views_30s', 'label': 'Qualified views (30 seconds or more)', 'value': 120000, 'unit': 'count'}]
        res = {'subject': 'Harbor Lights', 'metrics': self._mets(100000, 100000, 110000, extra)}
        notes = vc.cohere(res)
        th = res['metrics'][3]
        self.assertLessEqual(th['value'], 110000)
        self.assertEqual(th['label'], 'Plays (30 seconds or more)')
        self.assertIn('plays, not people', th['definition'])
        self.assertTrue(any('never views' in n for n in notes), notes)

    def test_plays_never_sit_under_the_viewers(self):
        extra = [{'name': 'plays_30s', 'label': 'Plays of 30 seconds or more', 'value': 95000, 'unit': 'count'}]
        res = {'subject': 'Iron Orchard', 'metrics': self._mets(100000, 100000, 90000, extra)}
        notes = vc.cohere(res)
        vals = {m['name']: m['value'] for m in res['metrics']}
        self.assertGreaterEqual(vals['total_us_plays'], 100000)
        self.assertNotEqual(vals['total_us_plays'] % 10, 0)
        self.assertLessEqual(vals['plays_30s'], vals['total_us_plays'])
        self.assertTrue(any('plays' in n for n in notes), notes)
        # deterministic
        res2 = {'subject': 'Iron Orchard', 'metrics': self._mets(100000, 100000, 90000, [dict(extra[0])])}
        vc.cohere(res2)
        self.assertEqual(vals['total_us_plays'], {m['name']: m['value'] for m in res2['metrics']}['total_us_plays'])

    def test_a_read_without_both_counts_is_untouched(self):
        res = {'subject': 'A creator', 'metrics': [{'name': 'video_views', 'label': 'Video views', 'value': 5000000, 'unit': 'count'},
                                                   {'name': 'unique_viewers', 'label': 'Unique viewers', 'value': 1200000, 'unit': 'count'}]}
        self.assertEqual(vc.cohere(res), [])
        self.assertEqual(res['metrics'][0]['value'], 5000000)
        self.assertEqual(vc.cohere({'metrics': []}), [])
        self.assertEqual(vc.cohere(None), [])

    def test_the_read_core_runs_the_pass_before_the_bind(self):
        src = open(os.path.join(ROOT, 'prometheus', 'generated_read.py'), encoding='utf-8').read()
        i = src.find('_vcounts.cohere(res, text')
        j = src.find('res, _bound = _fb.bind_to_stored(')
        self.assertGreater(i, 0)
        self.assertGreater(j, i)


class TheCarrier(unittest.TestCase):
    STORED = {'ts': '2026-11-24T02:06:41Z', 'subject': 'Harbor Lights', 'universe': 'Harbor Lights', 'cohort': '', 'ws': '2026-11-01', 'we': '2026-11-09',
              'reply': ("Harbor Lights drew 41,287 unique US viewers on FilmReel+ across Nov 1 to Nov 9 2026, against 51,342 plays and 3.6M watch minutes.\n\n"
                        "Unique US viewers: 41,287, Total US views: 63,718, Total US plays: 51,342."),
              'metrics': [{'name': 'unique_us_viewers', 'label': 'Unique US viewers', 'value': 41287},
                          {'name': 'total_us_views', 'label': 'Total US views', 'value': 63718},
                          {'name': 'total_us_plays', 'label': 'Total US plays', 'value': 51342}]}

    def test_carriers_are_read_from_labels_and_the_reply(self):
        self.assertEqual(fb.carriers_of("Total US views, YouTube Free with ads"), {'youtube'})
        self.assertEqual(fb.carriers_of(self.STORED['reply']), {'filmreel+'})
        self.assertEqual(fb.carriers_of("the Harbor Lights audience"), set())
        self.assertEqual(fb.carriers_of("watched on Sun Nov 8 2026"), set())
        self.assertIn('youtube', fb.carriers_of("Harbor Lights drew 29,463 unique US viewers on YouTube's Free with ads shelf"))

    def test_a_figure_on_another_service_never_binds(self):
        res = {'subject': 'Harbor Lights', 'universe': 'the Harbor Lights audience', 'cohort': '', 'window_start': '2026-11-01', 'window_end': '2026-11-09',
               'metrics': [{'name': 'total_us_views', 'label': 'Total US views, YouTube Free with ads', 'value': 46137},
                           {'name': 'total_us_plays', 'label': 'Total US plays, YouTube Free with ads', 'value': 38219}]}
        res, bound = fb.bind_to_stored(res, [dict(self.STORED)], ask_text=ASK_WHY)
        self.assertEqual(bound, [])
        self.assertEqual([m['value'] for m in res['metrics']], [46137, 38219])

    def test_the_same_service_still_binds(self):
        res = {'subject': 'Harbor Lights', 'universe': 'the Harbor Lights audience', 'cohort': '', 'window_start': '2026-11-01', 'window_end': '2026-11-09',
               'metrics': [{'name': 'total_us_plays', 'label': 'Total US plays on FilmReel+', 'value': 50000}]}
        res, bound = fb.bind_to_stored(res, [dict(self.STORED)], ask_text='plays on FilmReel+ again')
        self.assertEqual(len(bound), 1)
        self.assertEqual(res['metrics'][0]['value'], 51342)
        # no carrier named anywhere: the standing bind
        res = {'subject': 'Harbor Lights', 'universe': 'the Harbor Lights audience', 'cohort': '', 'window_start': '2026-11-01', 'window_end': '2026-11-09',
               'metrics': [{'name': 'total_us_plays', 'label': 'Total US plays', 'value': 50000}]}
        res, bound = fb.bind_to_stored(res, [dict(self.STORED)], ask_text='how many plays')
        self.assertEqual(len(bound), 1)


class ThePin(unittest.TestCase):
    def test_a_metric_phrase_is_never_a_subject(self):
        for n in ("Views vs Plays", "plays per viewer", "penetration and share", "unique US viewers", "Views"):
            self.assertTrue(ts.is_metric_phrase(n), n)
        for n in ("Harbor Lights", "Prime Video", "people that searched Harbor Lights in the US", "YouTube Free", "Night Market"):
            self.assertFalse(ts.is_metric_phrase(n), n)

    def test_a_pinned_metric_phrase_does_not_bind(self):
        pin = {'subject': 'Views vs Plays', 'source': 'offer', 'dropped': []}
        orig = ts.current
        ts.current = lambda: pin
        try:
            self.assertEqual(ts.pinned_subject(), '')
            pin = {'subject': 'Harbor Lights', 'source': 'offer', 'dropped': []}
            ts.current = lambda: pin
            self.assertEqual(ts.pinned_subject(), 'Harbor Lights')
        finally:
            ts.current = orig


class ThroughTheDoor(unittest.TestCase):
    def _run(self, q, reading):
        import replay_corpus as RC
        RC.install_isolation_light()
        case = {'id': 'pv', 'question': q, 'view': 'profileIQ', 'surface': 'analyze', 'subject': None, 'history': _history(),
                'reading': reading, 'recorded': {}}
        stub = RC._Stub()
        stub.bind()
        live = RC._service_once(stub, case)
        return live, stub

    def test_each_live_ask_lands_on_the_methodology_lane(self):
        readings = {
            ASK_PLAY: {"intent": "definition", "confidence": 0.93, "subjects": [{"as_written": "plays", "catalog": "", "kind": "unknown"}], "flags": [], "restate": "You want to know what a play means in this read."},
            ASK_WHY: {"intent": "definition", "confidence": 0.89, "subjects": [{"as_written": "views vs plays", "catalog": "", "kind": "title"}], "flags": [], "restate": "You want to know why total views sit above total plays."},
            ASK_SCEN: {"intent": "definition", "confidence": 0.89, "subjects": [], "flags": [], "restate": "You want to know what each case means."},
            ASK_THRESH: {"intent": "definition", "confidence": 0.95, "subjects": [{"as_written": "qualified view", "catalog": "", "kind": "unknown"}], "flags": [], "restate": "You want the definition of the watch threshold."},
        }
        for q, rd in readings.items():
            live, stub = self._run(q, rd)
            self.assertEqual(live['route'], 'methodology', (q, live))
            self.assertEqual(live['outcome'], 'answered', (q, live))
            self.assertFalse([n for n, b in stub.cores if n == 'analyze'], (q, stub.cores))     # no model core, no read, no quote
            self.assertIn('What we count as a play:', str(live.get('reply') or ''))


class FixturesCarryTheCase(unittest.TestCase):
    def test_the_live_shapes_are_fixtures(self):
        path = os.path.join(HERE, 'pm_corpus', 'fixtures', 'sanitized_cases.jsonl')
        ids = {}
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    d = json.loads(line)
                    ids[d['id']] = d
        for fid, q in (('fx074', ASK_PLAY), ('fx075', ASK_WHY), ('fx076', ASK_THRESH)):
            self.assertIn(fid, ids)
            self.assertEqual(ids[fid]['question'], q)
            self.assertEqual(ids[fid]['recorded'], {'route': 'methodology', 'outcome': 'answered'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
