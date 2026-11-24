""""Full breakdown" gets a real breakdown (Nov 23 2026 ask audit, fix 8;
Jenna 2026-11-23: when the reader asks for the full breakdown of a figure
or a read, the answer lists its parts, by the dimension the read carries,
with figures that add up, instead of the one-line provenance answer).

The live case, sanitized: a Profile IQ page; a read that said "YouTube
carries 2,667,703 of the 2,851,821 US people in the <subject> audience";
then "what is the full breakdown of 2,851,821 audience. Where is the
audience from also". The reply said only where the figure came from.

The rules under test:

* `figure_breakdown.wants` reads a breakdown ask about a figure or a read
  in hand (and a where-from ask); a breakdown of a NAMED audience is not
  one; plain provenance words are not one.
* The parts come from the read's own record when it carries a breakdown
  table, else from the profile the figure is the US projection of: gender
  and age (each adds up to the figure), the top markets plus everything
  else (adds up), and the services (overlapping, said so). A figure the
  thread already stated for a service stands.
* A figure that is not the profile's whole audience is never split by the
  profile's rows.
* "break that down" with no figure points at the latest read's headline
  figure and answers only when the parts can be built.
* Through the gate the route and outcome stay figure_provenance /
  answered; the Alexia provenance case keeps its reply.

Pure tests on a stub host; no network, no model.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, 'pm_corpus'))

import pandas as pd  # noqa: E402

from prometheus import brand_coverage as bc  # noqa: E402
from prometheus import figure_breakdown as fb  # noqa: E402
from prometheus import figure_provenance as fp  # noqa: E402

KEY = 'Example_Skater_11_17_2026_09_15.csv'
ASK = "what is the full breakdown of 2,851,821 audience. Where is the audience from also"
READ = ("YouTube carries 2,667,703 of the 2,851,821 US people in the Example Skater audience, 93.5% of it, at index 114 vs the "
        "average American.\n\nWindow: Nov 17 2025 to Nov 17 2026.\n- YouTube reach inside the audience: 2,667,703 (projected US people).")


def _history():
    return [
        {"role": "user", "ts": "2026-11-23T17:53:08Z", "text": "Example Skater profile, for the US audience how much is it coming from YouTube"},
        {"role": "agent", "ts": "2026-11-23T17:53:30Z", "text": "On it: reading Example Skater and YouTube now. This one takes a real look at the data, so give me a moment - the read will land right here when it is ready."},
        {"role": "agent", "ts": "2026-11-23T17:56:20Z", "text": READ, "meta": {"kind": "read", "read_job_id": "jobcccc333333", "subject": "Example Skater"}},
    ]


def _frame():
    rows = [('BRAND INPUT', 'Example Skater', 100.0, 2851821), ('SAMPLE SIZE', 'Example Skater', 100.0, 2851821),
            ('GENDER', 'MALE', 64.6213, 0), ('GENDER', 'FEMALE', 34.7118, 0), ('GENDER', 'NON-BINARY', 0.6669, 0),
            ('AGE', '25-34', 31.2087, 0), ('AGE', '18-24', 22.4113, 0), ('AGE', '35-44', 21.9034, 0), ('AGE', '45-54', 13.0211, 0),
            ('AGE', '55-64', 7.8317, 0), ('AGE', '65+', 3.6238, 0),
            ('LOCATION', 'Los Angeles Ca', 10.9113, 0), ('LOCATION', 'New York Ny', 7.2231, 0), ('LOCATION', 'Chicago Il', 3.9117, 0),
            ('SOCIAL MEDIA', 'YouTube', 93.5446, 0), ('SOCIAL MEDIA', 'Instagram', 71.2113, 0),
            ('STREAMING/PLATFORM', 'Netflix', 61.0213, 0), ('STREAMING/PLATFORM', 'YouTube', 93.5446, 0)]
    return pd.DataFrame(rows, columns=['Column', 'Value', 'Brand Penetration (Row)', 'Gen Pop Projection'])


def _ctx():
    return {'primary': {'name': 'Example Skater', 's3_key': KEY}, 'view_context': {'view_id': 'profileIQ'}}


class _Host:
    bucket = 'dashboard-inputs'


class TheWords(unittest.TestCase):
    def test_breakdown_and_where_from(self):
        self.assertEqual(fb.wants(ASK), {'breakdown': True, 'geo': True})
        self.assertEqual(fb.wants("what is the full breakdown of 2,851,821 audience"), {'breakdown': True, 'geo': False})
        self.assertEqual(fb.wants("can you break that down?"), {'breakdown': True, 'geo': False})
        self.assertEqual(fb.wants("what makes up the 2,851,821?"), {'breakdown': True, 'geo': False})
        self.assertEqual(fb.wants("where is the audience from?"), {'breakdown': False, 'geo': True})

    def test_not_a_breakdown_of_a_figure_in_hand(self):
        self.assertIsNone(fb.wants("give me a demographic breakdown of Yellowstone viewers"))
        self.assertIsNone(fb.wants("where does the 2,851,821 number come from? I only see 6.6M"))
        self.assertIsNone(fb.wants("how many people watched Dogma on YouTube"))
        self.assertIsNone(fb.wants("Email me this file"))

    def test_the_implied_figure_is_the_latest_reads_headline(self):
        imp = fb.implied_figure("break that down", _history())
        self.assertEqual((imp or {}).get('digits'), '2851821', imp)
        self.assertIsNone(fb.implied_figure("break down the 2,851,821", _history()))     # quoted: not implied
        self.assertIsNone(fb.implied_figure("how many watched it", _history()))


class TheParts(unittest.TestCase):
    def setUp(self):
        bc._CACHE[KEY] = (time.time(), _frame())

    def tearDown(self):
        bc._CACHE.pop(KEY, None)

    def test_the_profile_split_adds_up_and_keeps_the_stated_figure(self):
        bd = fb.build('2851821', record=None, ctx=_ctx(), host=_Host(), text=ASK, history=_history())
        self.assertIsNotNone(bd)
        self.assertEqual(bd['source'], 'profile')
        dims = [s['dimension'] for s in bd['sections']]
        self.assertEqual(dims, ['By gender', 'By age', 'Where they live (top markets)', 'Where they are online'], dims)
        for s in bd['sections'][:3]:
            self.assertTrue(s['adds'])
            self.assertEqual(sum(p['people'] for p in s['parts']), 2851821, s)
        gender = bd['sections'][0]['parts']
        self.assertEqual([p['label'] for p in gender], ['Male', 'Female', 'Non-binary'])
        age = bd['sections'][1]['parts']
        self.assertEqual([p['label'] for p in age][:3], ['18-24', '25-34', '35-44'])      # in band order, not by size
        geo = bd['sections'][2]['parts']
        self.assertEqual(geo[-1]['label'], 'All other markets')
        online = bd['sections'][3]
        self.assertFalse(online['adds'])
        yt = next(p for p in online['parts'] if p['label'] == 'YouTube')
        self.assertEqual(yt['people'], 2667703)        # the figure the read already stated, not a re-rounding
        self.assertEqual(len([p for p in online['parts'] if p['label'] == 'YouTube']), 1)

    def test_a_figure_that_is_not_the_whole_audience_is_not_split_by_the_profile(self):
        bd = fb.build('2667703', record=None, ctx=_ctx(), host=_Host(), text="what is the full breakdown of the 2,667,703", history=_history())
        self.assertIsNone(bd)

    def test_the_records_own_table_comes_first(self):
        rec = {'base': KEY, 'entry': {'breakdown': {'dimension': 'Title', 'share_basis': 'share of the combined viewers',
                                                   'rows': [{'label': 'Alpha Road', 'share_pct': 60.0, 'pool': 1711093},
                                                            {'label': 'Beta Lake', 'share_pct': 40.0, 'pool': 1140728}]}}}
        bd = fb.build('2851821', record=rec, ctx=_ctx(), host=_Host(), text="break down the 2,851,821 by title", history=_history())
        self.assertEqual(bd['source'], 'record')
        self.assertEqual(bd['sections'][0]['dimension'], 'Title')
        self.assertEqual(sum(p['people'] for p in bd['sections'][0]['parts']), 2851821)

    def test_the_reply_leads_with_the_profile_and_lists_the_parts(self):
        a = fp.answer(ASK, _history(), _ctx(), host=_Host())
        self.assertIsNotNone(a)
        r = a['reply']
        self.assertTrue(r.startswith('2,851,821 is the US projection of the Example Skater profile in Profile IQ, an audience read for Nov 17 2025 to Nov 17 2026.'), r)
        self.assertNotIn('not from the data on your screen', r)
        self.assertIn('By gender: Male 1,842,884 (64.6%); Female 989,918 (34.7%); Non-binary 19,019 (0.7%). These add up to 2,851,821.', r)
        self.assertIn('Where they live (top markets):', r)
        self.assertIn('Where they are online: YouTube 2,667,703 (93.5%)', r)
        self.assertIn('do not add to the total', r)
        for banned in ('synth', 'model', 'estimat', 'ledger', 'csv', 'frame', 'panel', '\u2014', '\u2013'):
            self.assertNotIn(banned, r.lower(), banned)

    def test_break_that_down_with_no_figure(self):
        imp = fb.implied_figure("break that down", _history())
        a = fp.answer("break that down", _history(), _ctx(), host=_Host(), implied=imp)
        self.assertIsNotNone(a)
        self.assertIn('By gender:', a['reply'])

    def test_without_the_profile_the_provenance_line_stands_and_says_so(self):
        bc._CACHE.pop(KEY, None)
        a = fp.answer(ASK, _history(), _ctx(), host=_Host())
        self.assertIn('2,851,821 came from the earlier read on Example Skater in this thread', a['reply'])
        self.assertIn('I do not hold a split of that figure on file.', a['reply'])
        self.assertIsNone(fp.answer("break that down", _history(), _ctx(), host=_Host(), implied=fb.implied_figure("break that down", _history())))

    def test_the_alexia_case_keeps_its_reply(self):
        hist = [{"role": "user", "text": "how many potential ticket buyers are there"},
                {"role": "agent", "text": "On The Example Film: 12,004,203 potential ticket buyers sit in the audience. Window: trailing year.", "meta": {"kind": "read", "read_job_id": "jobdddd444444", "subject": "The Example Film"}}]
        a = fp.answer("where does the 12,004,203 potential ticket buyers number come from? I only see 6.6M that were exposed", hist, {'primary': {}}, host=_Host())
        self.assertEqual(a['reply'], "12,004,203 came from the earlier read on The Example Film in this thread, not from the data on your screen.")
        self.assertIsNone(a.get('breakdown'))


class ThroughTheDoor(unittest.TestCase):
    def test_the_route_is_the_free_provenance_lane(self):
        import replay_corpus as RC
        RC.install_isolation_light()
        case = {'id': 'f8', 'question': ASK, 'view': 'profileIQ', 'surface': 'analyze', 'subject': 'Example Skater', 'history': _history(),
                'reading': {"intent": "analyze_page", "confidence": 0.9, "subjects": [{"as_written": "the audience", "catalog": "", "kind": "person"}],
                            "flags": ["refers_to_page", "quotes_figure"], "restate": "You want the parts of the 2,851,821 audience and where it is from."},
                'recorded': {}}
        stub = RC._Stub()
        stub.bind()
        live = RC._service_once(stub, case)
        self.assertEqual(live['route'], 'figure_provenance', live)
        self.assertEqual(live['outcome'], 'answered', live)
        self.assertFalse([n for n, b in stub.cores if n == 'analyze'], stub.cores)     # no model core, no charge


class FixturesCarryTheCase(unittest.TestCase):
    def test_the_live_shape_is_a_fixture(self):
        path = os.path.join(HERE, 'pm_corpus', 'fixtures', 'sanitized_cases.jsonl')
        ids = {}
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    d = json.loads(line)
                    ids[d['id']] = d
        self.assertIn('fx073', ids)
        self.assertEqual(ids['fx073']['question'], ASK)
        self.assertEqual(ids['fx073']['recorded'], {'route': 'figure_provenance', 'outcome': 'answered'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
