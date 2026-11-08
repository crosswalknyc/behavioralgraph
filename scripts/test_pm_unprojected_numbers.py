#!/usr/bin/env python3
"""Raw, unprojected numbers on request (Jenna 2026-11-08).

Jenna, verbatim: "if someone asks for a raw or unprojected or not modeled
or panel size or something like that number you can give them whatever
the genpop projected number is if you unproject it".

Every projected US figure derives from the 10 million US consumers we
measure: Projection = Raw x 329,900,000 / 10,000,000. The lane
(prometheus.unprojected) brings the figure in hand back to that base with
the one factor in prometheus.projection (``unproject`` / ``project``).

Hermetic: a fake thread, a stub host, a stubbed catalog, no model, no S3.
Covered:
  1. "what's the raw number for that" after a read with a projected
     figure: 182,739 of the 10 million, the projected figure named
  2. "how many people in your data watched X" with a profile open: the
     profile's SAMPLE SIZE row, read directly
  3. "unprojected audience for Howie Mandel" by name: the SAMPLE SIZE row
  4. "not modeled number" answers without the word modeled (or
     synthetic, estimated, panel)
  5. no figure in context asks which number, in one line
  6. the arithmetic matches the canonical factor to the unit, both ways
  7. the lane sits ahead of the reader and the sizing lanes, right after
     number_provenance, never priced; service.ask answers from it with no
     core call on both client surfaces
  8. the asks that are not this lane's: where-does-it-come-from, the size
     of the measured set itself, a file ask, a size ask on a subject not
     on file (the sizing lane's priced read)
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

from prometheus import unprojected as U  # noqa: E402
from prometheus import projection as P  # noqa: E402
from prometheus import sizing_lane as SZ  # noqa: E402

BANNED = re.compile(r"\b(?:modell?ed|synthetic|estimated?|panel(?:ists?)?)\b", re.I)

READ = ('On Gilmore Girls: 6,028,562 US people watched Gilmore Girls on Netflix in the window, 1.8% of the US. '
        'Streaming mix, Nov 2025 to Nov 2026\n- Netflix: 100% share.\n\nWindow: Nov 8 2025 to Nov 8 2026.')
HISTORY = [
    {'role': 'user', 'text': 'how many people watched Gilmore Girls on Netflix last year'},
    {'role': 'agent', 'text': READ, 'meta': {'subject': 'Gilmore Girls', 'read_job_id': 'r1',
                                            'figures': [{'label': 'US viewers', 'value': 6028562, 'unit': 'people'},
                                                        {'label': 'share of the US', 'value': 1.8, 'unit': 'percent'}]}},
]
PAGE = {'primary': {'name': 'Gilmore Girls', 's3_key': 'Gilmore_Girls_10_21_2026_12_00.csv'}}

CATALOG = {
    'howie mandel': {'name': 'Howie Mandel', 'sample': 182739, 'us': 6028562, 'key': 'Howie_Mandel_10_01_2026_09_00.csv'},
    'gilmore girls': {'name': 'Gilmore Girls', 'sample': 54521, 'us': 1798648, 'key': 'Gilmore_Girls_10_21_2026_12_00.csv'},
}


def _fake_resolve(phrase, ctx=None):
    p = ' '.join(str(phrase or '').lower().split())
    for k, v in CATALOG.items():
        if k in p or p in k:
            return dict(v, matched=phrase)
    return None


def _no_dashes(tc, s):
    tc.assertNotIn('\u2014', s)
    tc.assertNotIn('\u2013', s)


class Factor(unittest.TestCase):
    def test_unproject_matches_the_canonical_factor_to_the_unit(self):
        for v in (6_028_562, 1_798_648, 329_900_000, 12_004_203, 51_131_927, 1_000):
            self.assertEqual(P.unproject(v), int(round(v * 10_000_000 / 329_900_000)), v)
        self.assertEqual(P.unproject(6_028_562), 182_739)
        self.assertEqual(P.unproject(329_900_000), 10_000_000)

    def test_project_is_the_pipeline_formula(self):
        for raw in (182_739, 54_521, 10_000_000, 1):
            self.assertEqual(P.project(raw), int(round(raw * 329_900_000 / 10_000_000)), raw)
        self.assertEqual(P.project(10_000_000), 329_900_000)
        # a round trip lands within one unit of the factor (the integer rounding)
        for v in (6_028_562, 1_798_648, 12_004_203):
            self.assertLessEqual(abs(P.project(P.unproject(v)) - v), 33, v)

    def test_the_factor_lives_in_one_place(self):
        self.assertEqual(P.US_POP, 329_900_000)
        self.assertEqual(P.PANEL_DENOMINATOR, 10_000_000)
        src = open(os.path.join(ROOT, 'prometheus', 'unprojected.py'), encoding='utf-8').read()
        code = src.split('"""', 2)[2]      # the module body, past the docstring that spells the formula out
        self.assertNotIn('329_900_000', code)
        self.assertNotIn('32.99', code)
        self.assertNotIn('10_000_000', code)
        self.assertIn('_pj.unproject(', code)
        self.assertIn('_pj.project(', code)


class Detection(unittest.TestCase):
    def test_the_raw_asks(self):
        for q in ("what's the raw number for that", 'give me the not modeled number', 'unprojected audience for Howie Mandel',
                  'how many people in your data watched Gilmore Girls', 'what is the number before projection',
                  'how many of the 10 million is that', 'panel size for Howie Mandel', 'raw count please',
                  'what is the unprojected number behind 6,028,562', 'how many people are actually in your dataset for this',
                  'what is the measured count behind that', 'what is the actual count, not the projected one'):
            self.assertTrue(U.wants_unprojected(q), q)

    def test_not_this_lane(self):
        for q in ('where does the 6,028,562 come from', 'how big is your sample', "what's your panel size",
                  'what is the 10 million sample you keep mentioning', 'send me the raw data', 'can I get the raw csv',
                  'sample size of Chipotle buyers', 'how many Chipotle buyers are in your dataset',
                  'how many people watched Gilmore Girls on Netflix', 'top 10 QSRs for Gilmore Girls viewers',
                  'is that the US figure or global',
                  'How is a Hazbin play detected, and what is the raw panel count before projection?'):
            self.assertFalse(U.wants_unprojected(q), q)

    def test_quoted_figures_skip_percents_and_years(self):
        self.assertEqual([v for _t, v in U.quoted_figures('is 6,028,562 the 2026 figure, 1.8% of the US, 182k raw')],
                         [6028562, 182000])
        self.assertEqual(U.quoted_figures('27.0% of the HBO Max audience'), [])

    def test_kill_switch(self):
        os.environ['PM_UNPROJECTED'] = '0'
        try:
            self.assertFalse(U.wants_unprojected("what's the raw number for that"))
        finally:
            os.environ.pop('PM_UNPROJECTED', None)


class Answer(unittest.TestCase):
    def setUp(self):
        self._resolve = SZ.resolve
        SZ.resolve = _fake_resolve

    def tearDown(self):
        SZ.resolve = self._resolve

    def test_the_raw_number_for_the_last_read(self):
        out = U.answer("what's the raw number for that", HISTORY)
        r = out['reply']
        self.assertEqual(r, 'Unprojected, the US viewers on Gilmore Girls is 182,739 of the 10 million US consumers we measure '
                            '(the projected US figure is 6,028,562).')
        self.assertEqual(out['unprojected'], {'basis': 'last_read', 'projected': 6028562, 'raw': 182739,
                                              'subject': 'Gilmore Girls', 'found': True})
        self.assertEqual(out['followups'], [])
        self.assertFalse(out.get('panel_offer'))
        self.assertIsNone(BANNED.search(r), r)
        _no_dashes(self, r)

    def test_a_read_with_no_figures_ledger_uses_its_first_count(self):
        hist = [{'role': 'user', 'text': 'q'}, {'role': 'agent', 'text': READ}]
        out = U.answer('unprojected number please', hist)
        self.assertIn('182,739 of the 10 million US consumers we measure', out['reply'])
        self.assertIn('(the projected US figure is 6,028,562)', out['reply'])
        self.assertEqual(out['unprojected']['subject'], 'Gilmore Girls')

    def test_how_many_in_your_data_with_a_profile_open_reads_the_sample_size_row(self):
        out = U.answer('how many people in your data watched Gilmore Girls', [], ctx=PAGE)
        self.assertEqual(out['reply'], 'Unprojected, the Gilmore Girls audience is 54,521 of the 10 million US consumers we measure '
                                       '(the projected US figure is 1,798,648).')
        self.assertEqual(out['unprojected']['basis'], 'sample_size_row')
        self.assertEqual(out['unprojected']['raw'], 54521)
        # the open profile with nothing named
        out2 = U.answer('how many people are in your data for this profile', [], ctx=PAGE)
        self.assertEqual(out2['unprojected']['basis'], 'open_profile')
        self.assertIn('54,521', out2['reply'])

    def test_by_name_uses_the_sample_size_row(self):
        for q in ('unprojected audience for Howie Mandel', 'panel size for Howie Mandel', "what's the raw audience for Howie Mandel"):
            out = U.answer(q, HISTORY, ctx=PAGE)
            self.assertEqual(out['reply'], 'Unprojected, the Howie Mandel audience is 182,739 of the 10 million US consumers we measure '
                                           '(the projected US figure is 6,028,562).', q)
            self.assertEqual(out['unprojected']['basis'], 'sample_size_row', q)
            self.assertEqual(out['unprojected']['subject'], 'Howie Mandel', q)

    def test_not_modeled_number_never_says_modeled(self):
        out = U.answer('give me the not modeled number', HISTORY)
        self.assertTrue(out['unprojected']['found'])
        self.assertIsNone(BANNED.search(out['reply']), out['reply'])
        self.assertIn('the 10 million US consumers we measure', out['reply'])
        self.assertTrue(U.is_clean(out['reply']))

    def test_a_quoted_figure_is_unprojected_as_written(self):
        out = U.answer('what is the unprojected number behind 6,028,562', [])
        self.assertEqual(out['reply'], 'Unprojected, 6,028,562 is 182,739 of the 10 million US consumers we measure '
                                       '(the projected US figure is 6,028,562).')
        self.assertEqual(out['unprojected']['basis'], 'quoted')

    def test_no_figure_asks_which_number(self):
        out = U.answer("what's the raw number for that", [])
        self.assertEqual(out['reply'], U.WHICH_NUMBER)
        self.assertFalse(out['unprojected']['found'])
        self.assertEqual(out['reply'].count('?'), 1)
        self.assertEqual(out['followups'], [])

    def test_a_named_subject_not_on_file(self):
        out = U.answer('unprojected audience for Beyonce', [])
        self.assertFalse(out['unprojected']['found'])
        self.assertIn('I do not hold a profile on Beyonce yet', out['reply'])
        self.assertIsNone(BANNED.search(out['reply']), out['reply'])
        # the softer shape on a subject not on file is the sizing lane's priced read
        self.assertIsNone(U.answer('how many people in your data watched Beyonce', []))

    def test_an_offer_turn_is_never_the_figure(self):
        hist = [{'role': 'user', 'text': 'q'},
                {'role': 'agent', 'text': READ, 'meta': {'subject': 'Gilmore Girls'}},
                {'role': 'user', 'text': 'top 10 QSRs'},
                {'role': 'agent', 'text': 'I can pull the ranked list on Gilmore Girls. Pulling this data will cost $100.'}]
        out = U.answer("what's the raw number for that", hist)
        self.assertEqual(out['unprojected']['projected'], 6028562)


class Wiring(unittest.TestCase):
    def test_lane_order(self):
        from prometheus import service as S
        self.assertEqual(list(S.LANE_ORDER), S.lane_markers())
        order = list(S.LANE_ORDER)
        self.assertEqual(order.index('unprojected'), order.index('number_provenance') + 1)
        for later in ('continuation', 'reader', 'sizing_quote', 'sizing_fallback', 'surface_decision', 'work_gate'):
            self.assertLess(order.index('unprojected'), order.index(later), later)
        self.assertIn('unprojected', S.DETERMINISTIC_LANES)
        self.assertIn('unprojected', S.NEVER_PRICED_LANES)
        with open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertLess(src.index('# lane: number_provenance'), src.index('# lane: unprojected'))
        self.assertLess(src.index('# lane: unprojected'), src.index('# lane: reader'))
        self.assertIn("_unp.answer(text, history, ctx=ctx) if _unp.wants_unprojected(text) else None", src)


class ServiceEndToEnd(unittest.TestCase):
    """service.ask with a stub host: the lane answers before any core."""

    @classmethod
    def setUpClass(cls):
        from prometheus.host import host
        cls.calls = []

        def _analyze_core(user, body, text, history):
            cls.calls.append(('analyze', text))
            return {'success': True, 'reply': 'Horror is where the audience sits deepest.', 'followups': []}

        def _interpret_core(user, body, text, history):
            cls.calls.append(('interpret', text))
            return {'success': True, 'reply': 'draft', 'draft': {'subject': 'x'}}

        host.bind(analyze_core=_analyze_core, interpret_core=_interpret_core,
                  deck_core=lambda *a: {'success': True, 'reply': 'deck'},
                  ask_hint=lambda **k: None, gate_analyze=lambda u: True, gate_pull=lambda u: True,
                  funds_gate=lambda u: None, gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
                  calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a, **k: None,
                  load_threads_index=lambda u: {'threads': []}, max_threads=50,
                  s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
                  thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u,
                  thread_title_from=lambda t: t[:30], bucket='x', s3_client=None,
                  job_owner_ok=lambda a, b: True, claude_data=lambda *a, **k: {},
                  load_users=lambda: {'users': {'jaypatel14': {'username': 'jaypatel14', 'credits': 100}}},
                  check_user_credits=lambda u: (True, 100))
        from prometheus import service
        cls.service = service
        cls.user = {'username': 'jaypatel14'}
        cls._resolve = SZ.resolve
        SZ.resolve = _fake_resolve

    @classmethod
    def tearDownClass(cls):
        SZ.resolve = cls._resolve

    def _ask(self, text, surface='analyze', history=None, page=PAGE):
        self.calls.clear()
        body = {'text': text, 'surface': surface, 'client': 'dashboard', 'history': list(HISTORY if history is None else history)}
        if page:
            body['page_context'] = page
        return self.service.ask(self.user, body)

    def test_the_raw_number_for_that_on_the_analyze_surface(self):
        env, st = self._ask("what's the raw number for that")
        self.assertEqual(st, 200)
        self.assertEqual(env['decision']['reason'], 'unprojected')
        self.assertEqual(self.calls, [])
        raw = env['raw']
        self.assertIn('182,739 of the 10 million US consumers we measure', raw['reply'])
        self.assertIn('6,028,562', raw['reply'])
        self.assertEqual(raw.get('followups'), [])
        self.assertFalse(raw.get('panel_offer'))
        self.assertIsNone(BANNED.search(raw['reply']), raw['reply'])

    def test_how_many_in_your_data_with_the_profile_open(self):
        env, _st = self._ask('how many people in your data watched Gilmore Girls', history=[])
        self.assertEqual(env['decision']['reason'], 'unprojected')
        self.assertEqual(self.calls, [])
        self.assertIn('54,521 of the 10 million US consumers we measure', env['raw']['reply'])

    def test_by_name_on_the_interpret_surface(self):
        env, st = self._ask('unprojected audience for Howie Mandel', surface='interpret')
        self.assertEqual(st, 200)
        self.assertEqual(env['decision']['reason'], 'unprojected')
        self.assertEqual(self.calls, [])
        raw = env['raw']
        self.assertTrue(raw.get('guidance'))
        self.assertIn('182,739 of the 10 million US consumers we measure', str(raw.get('error') or ''))

    def test_no_figure_asks_which_number(self):
        env, _st = self._ask('what is the unprojected number', history=[], page=None)
        self.assertEqual(env['decision']['reason'], 'unprojected')
        self.assertEqual(env['raw']['reply'], U.WHICH_NUMBER)

    def test_where_does_it_come_from_stays_provenance(self):
        env, _st = self._ask('where does the 6,028,562 come from')
        self.assertNotEqual(env['decision']['reason'], 'unprojected')


if __name__ == '__main__':
    unittest.main(verbosity=2)
