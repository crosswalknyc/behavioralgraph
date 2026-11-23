#!/usr/bin/env python3
"""The surface and the analyze route decided from the Understanding
(prometheus.routing, rebuild step 3, Jenna 2026-11-20).

- One decider (2026-11-23): the older readers are gone. understand.decide
  is a thin wrapper over routing.surface; prometheus_router.route_ask
  carries only the rules; there is no switch, no model classifier and no
  steer on the reading in the surface_decision lane. An ask with no
  Understanding on it is read from its words.
- The rules that are not intents keep their rows: a dimension split, a
  cross of behaviors, a bare reply, a capability question, a product fact,
  a library lookup, the dashboard pickers, where a title is watched; a
  short bare subject is still the build flow's.
- The surface: an explicit mode or step is a decision already; a product
  intake lives on the analyze surface; a deck reading or the deck rule is
  the deck; a Profile IQ order, a dataset on an audience or a build
  reading that names its subject is the interpret surface; a Subscriber
  IQ pull that is not a question is the build flow's; everything else is
  answered on the analyze surface with the named mode riding along.
- The analyze route: the rules that are not intents (the Subscriber IQ
  family, the churn fork, a CSV export, the quantifiability gate, search
  demand) still decide first; with data open an analysis-shaped ask or a
  subject that is not the page is the measured read and the rest is the
  page analysis; with nothing open a build is the build flow, a data ask
  or a confident read is the read, a remembered referent the grounded
  confirm, the rest the clarify. No model call anywhere.
- The ask log row vocabulary is the older reasons' rows.

Hermetic: no model, no S3, no app boot.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import routing as R  # noqa: E402
sys.path.insert(0, HERE)
from _pm_legacy_view import legacy_view as _legacy_view  # noqa: E402  (the legacy chat text, wherever its blocks live now)
from prometheus import understand as UD  # noqa: E402
from prometheus import understanding as U  # noqa: E402
import prometheus_router as PR  # noqa: E402


def _u(text, ctx=None, history=None, reading=None, **kw):
    u = U.features(text, ctx, history or [], {}, **kw)
    if reading:
        u = U.absorb_reading(u, reading)
    return u


class OneDecider(unittest.TestCase):
    def test_the_switch_and_the_older_readers_are_gone(self):
        self.assertFalse(hasattr(R, 'legacy_routing'))
        self.assertFalse(hasattr(PR, '_classify'))
        self.assertFalse(hasattr(UD, '_decide'))
        from prometheus import reader as RD
        self.assertFalse(hasattr(RD, 'steer'))
        src = open(os.path.join(ROOT, 'prometheus', 'routing.py'), encoding='utf-8').read()
        self.assertNotIn("environ.get('PM_LEGACY_ROUTING'", src)
        # the whole legacy chat text (chat.py with every extracted block spliced back, the two
        # cores among them; scripts/_pm_legacy_view) and the service
        for rel, body in (('legacy view', _legacy_view()), ('prometheus/service.py', open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8').read())):
            self.assertNotIn('classify_ask_semantic', body, rel)
            self.assertNotIn('classify_fn', body, rel)
            self.assertNotIn('.steer(', body, rel)

    def test_decide_is_the_surface_over_the_words(self):
        for q, ctx in (('who watches Harbor Lights', False), ('what stands out here', True), ('run a profile on Harbor Lights', False), ('Nike', False)):
            self.assertEqual(UD.decide(q, has_ctx=ctx), R.surface(None, q, has_ctx=ctx), q)
            self.assertEqual(R.surface(None, q, has_ctx=ctx), R.surface(_u(q, {'has_ctx': True} if ctx else None), q, has_ctx=ctx), q)

    def test_an_ask_with_no_understanding_is_read_from_its_words(self):
        q = 'who watches Harbor Lights'
        self.assertEqual(R.surface(None, q, has_ctx=False)['reason'], 'question')
        self.assertEqual(R.analyze_route(None, q, surface='analyze', has_ctx=True)['route'], 'analysis')
        self.assertEqual(R.analyze_route(None, 'run a profile on Harbor Lights', surface='analyze', has_ctx=False)['route'], 'build_interpret')

    def test_the_rules_that_are_not_intents_keep_their_rows(self):
        cases = (('how big is your panel', True, 'analyze', 'product_fact', None),
                 ('analyze this data', True, 'analyze', 'analyze_menu', 'analyze_menu'),
                 ('compare with the open tabs', True, 'analyze', 'compare_open_tabs', 'compare_open_tabs'),
                 ('compare this against other profiles', True, 'analyze', 'compare_others', 'compare_picker'),
                 ('yes', False, 'interpret', 'short_reply:affirm', None),
                 ('yes', True, 'analyze', 'short_reply:affirm', None),
                 ('Pull Subscriber IQ for Starz', False, 'interpret', 'subiq', None),
                 ('how many Peacock subscribers churned in August?', False, 'analyze', 'question', None),
                 ('Nike', False, 'interpret', 'build_or_other', None),
                 ('', False, 'interpret', 'empty', None))
        for q, ctx, surf, reason, hint in cases:
            d = UD.decide(q, has_ctx=ctx, open_tabs=2)
            self.assertEqual((d['surface'], d['reason'], d['client_hint']), (surf, reason, hint), (q, d))
            self.assertIsNotNone(d['row'], q)

    def test_a_confident_reading_that_is_not_a_build_answers_where_the_ladder_fell_to_interpret(self):
        q = 'wjat does Avid tier mean'
        self.assertEqual(UD.decide(q, has_ctx=False)['reason'], 'build_or_other')
        u = _u(q, reading={'intent': 'definition', 'confidence': 0.93, 'subjects': [], 'flags': []})
        d = R.surface(u, q, has_ctx=False)
        self.assertEqual((d['surface'], d['reason']), ('analyze', 'question'))
        u = _u('Harbor Lights fans', reading={'intent': 'build_profile', 'confidence': 0.9, 'subjects': [{'as_written': 'Harbor Lights', 'kind': 'title'}], 'flags': []})
        self.assertEqual(R.surface(u, 'Harbor Lights fans', has_ctx=False)['surface'], 'interpret')


class TheSurface(unittest.TestCase):
    def test_explicit_mode_and_step_decide(self):
        q = 'anything'
        self.assertEqual(R.surface(_u(q), q, mode='compare')['reason'], 'explicit_mode')
        self.assertEqual(R.surface(_u(q), q, extra={'panel_confirm': {'x': 1}})['reason'], 'explicit_step')

    def test_a_profile_order_is_the_interpret_surface(self):
        q = 'Please queue one Profile IQ: US people who watched Harbor Lights on Prime Video, Nov 1 2025 to Oct 31 2026'
        d = R.surface(_u(q, is_order=True), q, has_ctx=True)
        self.assertEqual((d['surface'], d['reason'], d['row']), ('interpret', 'build_order', 'build_order'))

    def test_an_order_for_something_else_stays_on_analyze(self):
        q = 'Please run one read for a single window, January 1 to December 31, 2022. The audience is all US people.'
        d = R.surface(_u(q, is_order=True), q, has_ctx=False)
        self.assertEqual(d['surface'], 'analyze')

    def test_a_question_is_the_analyze_surface_with_a_row(self):
        q = 'who watches Harbor Lights and what do they buy?'
        d = R.surface(_u(q), q, has_ctx=False)
        self.assertEqual((d['surface'], d['reason'], d['row']), ('analyze', 'question', 'question'))
        self.assertEqual(R.surface(_u(q), q, has_ctx=True)['row'], 'data_open')

    def test_a_deck_reading_is_the_deck_surface(self):
        q = 'build a deck on Harbor Lights viewers'
        d = R.surface(_u(q, reading={'intent': 'deck', 'confidence': 0.9, 'subjects': []}), q)
        self.assertEqual((d['surface'], d['row']), ('deck', 'deck_ask'))

    def test_no_build_never_reaches_the_build_surface(self):
        q = 'Please queue one Profile IQ on Harbor Lights viewers, but do not build anything'
        u = _u(q, is_order=True, no_build=True)
        self.assertNotEqual(u.action, 'build')


class TheAnalyzeRoute(unittest.TestCase):
    def test_the_rules_that_are_not_intents_still_decide_first(self):
        q = 'export the last read as a csv'
        d = R.analyze_route(_u(q), q, surface='analyze', has_ctx=True)
        self.assertEqual(d['route'], PR.route_ask(q, surface='analyze', has_ctx=True)['route'])

    def test_open_data_is_decisive_as_the_router_had_it(self):
        q = 'what share of this audience is female'
        d = R.analyze_route(_u(q, ctx={'primary': {'name': 'Harbor Lights'}}), q, surface='analyze', has_ctx=True)
        self.assertIn(d['route'], ('analysis', 'generate'))
        self.assertNotIn('used_classifier', d)
        other = R.analyze_route(_u(q), q, surface='analyze', has_ctx=True, other_subject=True)
        self.assertEqual(other['route'], 'generate')                    # an analysis-shaped ask, or a named other subject: the read
        plain = 'tell me about the people here'
        o2 = R.analyze_route(_u(plain), plain, surface='analyze', has_ctx=True, other_subject=True)
        self.assertEqual((o2['route'], o2['why']), ('generate', 'named_other_subject'))
        page = R.analyze_route(_u(plain), plain, surface='analyze', has_ctx=True, other_subject=False)
        self.assertEqual((page['route'], page['why']), ('analysis', 'open_data_default'))

    def test_nothing_open_a_build_is_the_build_flow_and_a_default_reading_is_the_clarify(self):
        q = 'Please queue one Profile IQ on Harbor Lights viewers'
        d = R.analyze_route(_u(q, is_order=True), q, surface='analyze', has_ctx=False)
        self.assertEqual(d['route'], 'build_interpret')
        w = 'something about nothing in particular'
        d2 = R.analyze_route(_u(w), w, surface='analyze', has_ctx=False)
        self.assertEqual((d2['route'], d2['why']), ('clarify', 'no_context'))
        self.assertNotIn('used_classifier', d2)

    def test_a_confident_read_with_nothing_open_is_the_read(self):
        q = 'tell me about Harbor Lights viewers'
        d = R.analyze_route(_u(q, reading={'intent': 'analyze', 'confidence': 0.9, 'subjects': [{'as_written': 'Harbor Lights', 'kind': 'title'}]}),
                            q, surface='analyze', has_ctx=False)
        self.assertEqual((d['route'], d['why']), ('generate', 'understanding:read'))

    def test_no_model_is_called_to_route(self):
        import inspect
        self.assertNotIn('classify_fn', inspect.signature(R.analyze_route).parameters)
        self.assertNotIn('classify_fn', inspect.signature(PR.route_ask).parameters)
        q = 'is Harbor Lights popular with younger viewers'
        self.assertEqual(R.analyze_route(_u(q), q, surface='analyze', has_ctx=False, has_base=True)['route'], 'generate')
        self.assertEqual(R.analyze_route(_u(q), q, surface='interpret', has_ctx=False, has_base=True)['route'], 'generate')
        self.assertEqual(R.analyze_route(_u(q), q, surface='interpret', has_ctx=False, has_base=False)['route'], 'build_interpret')


class TheWiring(unittest.TestCase):
    def test_the_call_sites_read_the_routing_module(self):
        svc = open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8').read()
        core = interp = _legacy_view()     # the analyze and interpret cores read through the view (stage 7 moved them)
        self.assertIn('_routing_s.surface(', svc)
        self.assertIn('_routing_f.surface(', svc)        # the forced-interpret release reads the ask's own Understanding
        self.assertNotIn('understand.decide(', svc)
        self.assertIn('_routing_c.analyze_route(', core)
        self.assertIn('_routing_i.analyze_route(', interp)
        self.assertNotIn('_pmr.route_ask(', core)
        self.assertNotIn('_pmr.route_ask(', interp)
        src = open(os.path.join(ROOT, 'prometheus', 'routing.py'), encoding='utf-8').read()
        self.assertNotIn('request.json', src)
        self.assertNotIn('\u2014', src)


if __name__ == '__main__':
    unittest.main(verbosity=1)
