#!/usr/bin/env python3
"""US only or global is a definition question, and purchased brands are
confirmed orders (Jenna 2026-11-04, Jay Patel at Amazon, Paramount+ open).

Jay asked "are these numbers U.S only On the current window, 51,131,927 US
people use both HBO Max and Paramount+, 27.0% of the HBO Max audience and
54.6% of the Paramount+ audience, up 5,918,373 people on the 2025 read. or
global" and drew an interest-mix read of the open profile, three times.
Jenna: "wrong answer please correct it in chat saying our panel is us only"
and "most purchased brands IS confirmed by an order confirmation so it is
brands they purchased".

Hermetic: a fake thread, a stub host, no model, no S3. Covered:
  1. Jay's exact message routes to the definition answer through
     service.ask: no read, no offer, no core call; the reply carries his
     figures and the US-only line, on both client surfaces
  2. "global or US?" with no figures still answers from the last read
  3. a real new question ("how many global subscribers does Netflix
     report") is not caught, nor are orders and sizing asks
  4. the lane sits ahead of number_provenance, the continuation gate, the
     Reader, the sizing lanes and the open-profile binding; the
     continuation gate and the sizing lane know the shape too
  5. Most Purchased Brands reads as confirmed orders everywhere Prometheus
     explains it: the glossary, the counting block, the analysis prompt,
     the purchase context, the panel fact, the pull discussion
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

from prometheus import us_scope as U  # noqa: E402
from prometheus import continuation as C  # noqa: E402
from prometheus import sizing_lane as SZ  # noqa: E402
from prometheus import kpi_definitions as K  # noqa: E402
from prometheus import methodology as M  # noqa: E402
from prometheus import pull_discussion as PD  # noqa: E402
import prometheus_analysis as PA  # noqa: E402

JAY_Q = ('are these numbers U.S only On the current window, 51,131,927 US people use both HBO Max and Paramount+, '
         '27.0% of the HBO Max audience and 54.6% of the Paramount+ audience, up 5,918,373 people on the 2025 read. or global')
JAY_Q2 = ('On the current window, 51,131,927 US people use both HBO Max and Paramount+, 27.0% of the HBO Max audience and '
          '54.6% of the Paramount+ audience, up 5,918,373 people on the 2025 read. are these numbers U.S only')
JAY_Q3 = ('\u2022\nBase: the Paramount+ audience, measured across 10M US consumers and projected to the US. '
          'is the 51 million U.S or global?')

COUNTING = ('How these are counted:\n- Window: {w}.\n- Base: {b}, measured across 10M US consumers and projected to the US.\n'
            '- What we count as a view: A view is one person, on any screen, who played a title for at least one second '
            'inside the window, counted once however many devices or sessions they used. Autoplayed previews and trailers '
            'are not views. Counts are US viewers (persons), not accounts, so they are not comparable to a service\'s '
            'reported subscriber count, to TV ratings, or to platform view counts that tally plays instead of people.')

READ_2025 = ('45,213,554 US people use both HBO Max and Paramount+, 24.2% of the HBO Max audience and 54.0% of the '
             'Paramount+ audience.\n\nHBO Max and Paramount+ overlap: 45,213,554, Overlap as a share of the HBO Max audience: '
             '24.2%, Overlap as a share of the Paramount+ audience: 54.0%.\n\n- Where this shared audience buys: Amazon '
             '84.1189% (157,128,599 people), Walmart 70.5129% (131,713,499) and Target 50.5931% (94,504,606) carry the '
             'reach, and the steep leans are Urban Outfitters 28.2242% (index 863) and Depop 22.6892% (809).\n\n'
             + COUNTING.format(w='Jan 1 2025 to Dec 31 2025', b='the HBO Max audience'))
READ_2026 = ('On the current window, 51,131,927 US people use both HBO Max and Paramount+, 27.0% of the HBO Max audience '
             'and 54.6% of the Paramount+ audience, up 5,918,373 people on the 2025 read.\n\nHBO Max and Paramount+ overlap: '
             '51,131,927, Overlap as a share of the HBO Max audience: 27.0%, Overlap as a share of the Paramount+ audience: '
             '54.6%.\n\nStreaming platform mix, Jan 1 2026 to Nov 4 2026\n- NETFLIX: 28.2% share, 77.4% penetration. index '
             '112, still the default second service\n\n' + COUNTING.format(w='Jan 1 2026 to Nov 4 2026', b='the HBO Max audience'))
READ_SERIES = ('On HBO Max and Paramount+ overlap:\n\nThe HBO Max and Paramount+ shared audience has climbed every month of '
               'the window, from 45,213,554 people in November 2025 to 51,131,927 at the November 4 2026 read, a gain of '
               '5,918,373 people.\n\nShared audience, current read: 51,131,927, Shared audience, November 2025: 45,213,554, '
               'Twelve-month gain: 5,918,373.\n\nMonth mix, Nov 2025 to Nov 4 2026\n- Nov 2026 (through Nov 4): 8.1% share, '
               '27.0% penetration. current read')

HISTORY = [
    {'role': 'user', 'text': "what's the HBO and Paramount+ customer overlap in the U.S? give me the percents"},
    {'role': 'agent', 'text': 'I can pull that read on HBO Max. Pulling this data will cost $100.', 'meta': {'options': []}},
    {'role': 'user', 'text': 'Approve ($100, not billed to a staff account)'},
    {'role': 'agent', 'text': READ_2025, 'meta': {'read_job_id': 'f111'}},
    {'role': 'user', 'text': 'can you also pull 2026 data or the latest data'},
    {'role': 'agent', 'text': READ_2026, 'meta': {'read_job_id': 'f222'}},
    {'role': 'user', 'text': 'the overlap for hbo and paramount+'},
    {'role': 'agent', 'text': READ_SERIES, 'meta': {'read_job_id': 'f333'}},
]
PAGE = {'primary': {'name': 'Paramount+', 's3_key': 'Paramount_Plus_10_21_2026_12_00.csv'}}

BANNED = re.compile(r"\b(?:panel(?:ist)?s?|samples?|model(?:ed|led|s)?|synthetic|synth|estimated?|research(?:ed)?|pipeline|ledger)\b", re.I)


def _no_dashes(tc, s):
    tc.assertNotIn('\u2014', s)
    tc.assertNotIn('\u2013', s)


class Detection(unittest.TestCase):
    def test_jays_three_phrasings(self):
        for q in (JAY_Q, JAY_Q2, JAY_Q3):
            self.assertTrue(U.wants_us_scope(q), q[:60])

    def test_other_shapes(self):
        for q in ('Are these numbers US only or global?', 'is this US or worldwide?', 'is that the US figure?',
                  'global or domestic?', 'global or US?', 'US only?', 'is that global?',
                  'are these figures worldwide or just the US', 'which is it, US or global?'):
            self.assertTrue(U.wants_us_scope(q), q)

    def test_a_new_question_is_not_caught(self):
        for q in ('how many global subscribers does Netflix report', 'size the US audience for Paramount+',
                  'how big is the US audience for HBO Max', 'build a profile on US Netflix subscribers',
                  'what share of the global audience is US', 'compare US vs global for Netflix',
                  'how many US only subscribers vs global does Netflix have', 'can you pull global data?',
                  'do you have data outside the US?', 'is that domestic box office or worldwide?',
                  'can you give us the global number', 'list the US only titles', 'what does that number mean',
                  'where does the 51,131,927 come from',
                  "what's the HBO and Paramount+ customer overlap in the U.S? give me the percents"):
            self.assertFalse(U.wants_us_scope(q), q)

    def test_quoted_figures_skip_the_sample_mention(self):
        self.assertEqual([f['raw'] for f in U.quoted_figures(JAY_Q)], ['51,131,927', '27.0%', '54.6%', '5,918,373'])
        self.assertEqual([f['raw'] for f in U.quoted_figures(JAY_Q3)], ['51 million'])

    def test_kill_switch(self):
        os.environ['PM_US_SCOPE'] = '0'
        try:
            self.assertFalse(U.wants_us_scope(JAY_Q))
        finally:
            os.environ.pop('PM_US_SCOPE', None)


class Answer(unittest.TestCase):
    def test_jays_figures_come_back_with_the_us_line(self):
        out = U.answer(JAY_Q, HISTORY)
        r = out['reply']
        self.assertTrue(r.startswith(U.OPENER))
        for fig in ('51,131,927', '27.0%', '54.6%', '5,918,373'):
            self.assertIn(fig, r)
        self.assertIn(U.SCOPE_LINE, r)
        self.assertIn('Window: Jan 1 2026 to Nov 4 2026.', r)
        self.assertEqual(out['followups'], [])
        self.assertFalse(out.get('panel_offer'))
        self.assertEqual(out['us_scope']['turn'], 5)
        self.assertIsNone(BANNED.search(r), BANNED.search(r) and BANNED.search(r).group(0))
        _no_dashes(self, r)
        self.assertNotIn('Horror', r)

    def test_abbreviated_figure_matches_the_read(self):
        out = U.answer(JAY_Q3, HISTORY)
        self.assertIn('51 million', out['reply'])
        self.assertIn('the read on HBO Max and Paramount+ overlap', out['reply'])
        self.assertNotIn('10M', out['reply'])

    def test_no_figures_still_answers_from_the_last_read(self):
        out = U.answer('global or US?', HISTORY)
        r = out['reply']
        self.assertTrue(r.startswith(U.OPENER))
        self.assertIn('51,131,927', r)
        self.assertIn('the read on HBO Max and Paramount+ overlap', r)
        self.assertIn(U.SCOPE_LINE, r)
        self.assertNotIn('Window: November 2025', r)   # a month inside the prose is never the window

    def test_empty_thread_still_answers(self):
        out = U.answer('is this US or worldwide?', [])
        self.assertEqual(out['reply'], f"{U.OPENER} {U.SCOPE_LINE}")

    def test_a_figure_not_in_the_thread_is_named(self):
        out = U.answer('is the 99,123,456 US or global?', HISTORY)
        self.assertIn('I do not see 99,123,456 in anything delivered in this thread', out['reply'])
        self.assertIn(U.SCOPE_LINE, out['reply'])

    def test_a_repeat_ignores_its_own_earlier_reply(self):
        first = U.answer(JAY_Q, HISTORY)
        hist = HISTORY + [{'role': 'user', 'text': JAY_Q}, {'role': 'agent', 'text': first['reply']}]
        again = U.answer(JAY_Q2, hist)
        self.assertEqual(again['us_scope']['turn'], 5)


class Wiring(unittest.TestCase):
    def test_lane_order(self):
        from prometheus import service as S
        self.assertEqual(list(S.LANE_ORDER), S.lane_markers())
        order = list(S.LANE_ORDER)
        for later in ('number_provenance', 'continuation', 'reader', 'sizing_quote', 'sizing_fallback', 'surface_decision', 'work_gate'):
            self.assertLess(order.index('us_scope'), order.index(later), later)
        self.assertLess(order.index('reconcile'), order.index('us_scope'))
        self.assertIn('us_scope', S.DETERMINISTIC_LANES)
        self.assertIn('us_scope', S.NEVER_PRICED_LANES)
        with open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertLess(src.index('# lane: us_scope'), src.index('# lane: number_provenance'))
        self.assertIn("_uss.answer(text, history, ctx=ctx) if _uss.wants_us_scope(text) else None", src)

    def test_continuation_reads_it_as_a_question_about_the_answer(self):
        out = C.classify(JAY_Q, HISTORY, None, 'Paramount+')
        self.assertEqual(out['kind'], 'question_about_answer')
        self.assertTrue(out['params'].get('us_scope'))
        new = C.classify('how many global subscribers does Netflix report', HISTORY, None, 'Paramount+')
        self.assertEqual(new['kind'], 'new_ask')

    def test_sizing_declines_a_scope_ask(self):
        self.assertTrue(SZ.is_scope_ask('is that the US audience size or global?'))
        self.assertFalse(SZ.wants_sizing('is that the US audience size or global?'))
        self.assertIsNone(SZ.answer('is that the US audience size or global?', PAGE, subjects=['US audience']))
        self.assertTrue(SZ.wants_sizing('how big is the FanDuel audience?'))


class ServiceEndToEnd(unittest.TestCase):
    """service.ask with a stub host: the lane answers before any core, on
    both client surfaces; a real question still reaches the cores."""

    @classmethod
    def setUpClass(cls):
        from prometheus.host import host
        cls.calls = []

        def _analyze_core(user, body, text, history):
            cls.calls.append(('analyze', text))
            return {'success': True, 'reply': 'Horror is where Paramount+ sits deepest: 41.3% of the US horror audience.',
                    'followups': []}

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
                  job_owner_ok=lambda a, b: True,
                  claude_data=lambda *a, **k: {})   # the Reader never reaches a model here
        from prometheus import service
        cls.service = service
        cls.user = {'username': 'jaypatel14'}

    def _ask(self, text, surface='analyze'):
        self.calls.clear()
        env, st = self.service.ask(self.user, {'text': text, 'surface': surface, 'client': 'dashboard',
                                               'history': list(HISTORY), 'page_context': PAGE})
        return env, st

    def test_jays_question_on_the_analyze_surface(self):
        env, st = self._ask(JAY_Q)
        self.assertEqual(st, 200)
        self.assertEqual(env['decision']['reason'], 'us_scope')
        self.assertEqual(self.calls, [])
        raw = env['raw']
        self.assertTrue(raw.get('success'))
        for fig in ('51,131,927', '27.0%', '54.6%'):
            self.assertIn(fig, raw['reply'])
        self.assertIn(U.SCOPE_LINE, raw['reply'])
        self.assertNotIn('Horror', raw['reply'])
        self.assertFalse(raw.get('panel_offer'))
        self.assertEqual(raw.get('followups'), [])

    def test_the_other_two_phrasings_land_the_same_way(self):
        for q in (JAY_Q2, JAY_Q3):
            env, _st = self._ask(q)
            self.assertEqual(env['decision']['reason'], 'us_scope', q[:40])
            self.assertEqual(self.calls, [])

    def test_the_interpret_surface_gets_the_guidance_shape(self):
        env, st = self._ask(JAY_Q, surface='interpret')
        self.assertEqual(st, 200)
        self.assertEqual(env['decision']['reason'], 'us_scope')
        self.assertEqual(self.calls, [])
        raw = env['raw']
        self.assertTrue(raw.get('guidance'))
        self.assertIn('51,131,927', str(raw.get('error') or ''))
        self.assertIn(U.SCOPE_LINE, str(raw.get('error') or ''))

    def test_no_figures_still_answers(self):
        env, _st = self._ask('global or US?')
        self.assertEqual(env['decision']['reason'], 'us_scope')
        self.assertIn('51,131,927', env['raw']['reply'])

    def test_a_real_new_question_is_not_caught(self):
        env, _st = self._ask('how many global subscribers does Netflix report')
        self.assertNotEqual(env['decision']['reason'], 'us_scope')
        self.assertNotIn(U.OPENER, str(env.get('text') or ''))


class PurchasedBrandsAreConfirmedOrders(unittest.TestCase):
    def test_glossary(self):
        for q in ('what counts as a purchase?', 'what is most purchased brands', 'define where they shop',
                  'what does CPG mean', 'how are purchased brands counted?'):
            d = K.find_definition(q)
            self.assertIsNotNone(d, q)
            self.assertEqual(d['id'], 'purchased_brands', q)
        d = K.find_definition('what counts as a purchase?')
        reply = K.definition_reply(d)
        self.assertIn('confirmed by an order confirmation', reply)
        self.assertIn('actually purchased online in the window', reply)
        self.assertIn('at least one confirmed order that included the brand', reply)
        self.assertIn('Browsing, consideration and visits to the brand are not purchases', reply)
        self.assertNotIn('touchpoint', reply)
        _no_dashes(self, reply)
        pen = K.find_definition('how is penetration calculated?')
        self.assertEqual(pen['id'], 'penetration')
        self.assertIn('confirmed order that included the brand', pen['computed'])
        self.assertIn('For Most Purchased Brands, active means a confirmed order', pen['definition'])

    def test_counting_block_carries_the_purchase_line(self):
        self.assertTrue(M.mentions_purchase(READ_2025))
        self.assertTrue(M.carries_purchase_figure(READ_2025))
        out = M.ensure(READ_2025, {'window_label': 'Jan 1 2025 to Dec 31 2025', 'subject': 'HBO Max'}, question='overlap')
        self.assertIn(f"- {M.PURCHASE_HEADER} {M.PURCHASE_DEFINITION}", out)
        self.assertEqual(out.count(M.PURCHASE_HEADER), 1)
        self.assertEqual(M.ensure(out, {}, question=''), out)            # idempotent
        self.assertEqual(M.ensure(READ_SERIES, {}, question=''), READ_SERIES)   # no purchase figure, nothing added
        plain = M.ensure('Where this audience buys: Amazon 84.1% and Walmart 70.5%.', {}, question='where do they shop')
        self.assertTrue(plain.endswith(f"{M.PURCHASE_HEADER} {M.PURCHASE_DEFINITION}"))
        self.assertIn('confirmed by an order confirmation', M.PURCHASE_DEFINITION)
        self.assertIsNone(BANNED.search(M.PURCHASE_DEFINITION))
        _no_dashes(self, M.PURCHASE_DEFINITION)
        self.assertIn('confirmed purchases: an order confirmation that included the brand', M.MODEL_RULES)
        self.assertIn('never describe these rows as browsing, consideration, shopping interest, reach or a touchpoint', M.MODEL_RULES)

    def test_analysis_prompt_and_purchase_context(self):
        self.assertIn('a purchased brand is confirmed by an order confirmation', PA.ANALYSIS_SYSTEM_PROMPT)
        self.assertIn('never describe these rows as browsing, consideration, shopping interest, reach or a touchpoint',
                      PA.ANALYSIS_SYSTEM_PROMPT)
        self.assertIn('is a confirmed purchase: the share of the audience with at least one confirmed online order that '
                      'included the brand in the window, never browsing, consideration or a touchpoint', PA._ENTITY_ROWS_HEADER)
        with open(os.path.join(ROOT, 'prometheus_analysis.py'), encoding='utf-8') as fh:
            src = fh.read()
        src = re.sub(r"'\s*\n\s*'", '', src)   # adjacent string literals read as one
        self.assertNotIn('largest retail doors by reach', src)
        self.assertNotIn('strongest leans with real reach', src)
        self.assertIn('WHERE THIS AUDIENCE BUYS (confirmed orders), largest retail doors by share of the audience that bought there:', src)
        self.assertIn('Every purchase-family row is a confirmed purchase', src)
        self.assertIn('The one measurement behind the purchase family is a confirmed purchase', src)
        self.assertTrue(PA.is_confirmed_order_col('MOST PURCHASED BRANDS'))
        self.assertTrue(PA.is_confirmed_order_col('Where They Shop'))
        self.assertFalse(PA.is_confirmed_order_col('STREAMING/PLATFORM'))
        self.assertFalse(PA.is_confirmed_order_col('QSR'))

    def test_panel_fact_says_bought_for_a_purchase_row(self):
        import pandas as pd
        BP = 'Brand Penetration (Row)'
        df = pd.DataFrame([
            {'Column': 'BRAND INPUT', 'Value': 'HBO MAX', BP: '100.0000%', 'Raw': '84213', 'Projection': '2778387'},
            {'Column': 'SUBJECT', 'Value': 'HBO Max', BP: '', 'Raw': '', 'Projection': ''},
            {'Column': 'SAMPLE SIZE', 'Value': '07/01/2025 - 06/30/2026', BP: '100.0000%', 'Raw': '84213', 'Projection': '2778387'},
            {'Column': 'MOST PURCHASED BRANDS', 'Value': 'Amazon', BP: '84.1189%', 'Raw': '70838', 'Projection': '2337187'},
            {'Column': 'MOST PURCHASED BRANDS', 'Value': 'Walmart', BP: '70.5129%', 'Raw': '59381', 'Projection': '1959141'},
            {'Column': 'MOST PURCHASED BRANDS', 'Value': 'Urban Outfitters', BP: '28.2242%', 'Raw': '23769', 'Projection': '784195'},
            {'Column': 'STREAMING/PLATFORM', 'Value': 'Netflix', BP: '77.4321%', 'Raw': '65208', 'Projection': '2151445'},
        ])
        meta = PA._profile_meta(df, 'HBO Max')
        a = PA.answer_panel_fact({'kind': 'brand', 'brand': 'Walmart'}, df, meta, {})
        self.assertIsNotNone(a)
        self.assertIn('70.5% of the HBO Max audience bought Walmart in the window (a confirmed order that included the brand).', a['reply'])
        self.assertNotIn('reaches', a['reply'])
        self.assertEqual(a['metrics'][0]['label'], 'Walmart buyers')
        self.assertIn('confirmed online order', a['metrics'][0]['definition'])
        self.assertEqual(a['breakdown']['share_basis'], 'share of the audience that bought (confirmed orders)')
        top = PA.answer_panel_fact({'kind': 'top', 'cat_hint': 'brand', 'n': 2}, df, meta, {})
        self.assertIn('each a confirmed order', top['reply'])
        self.assertIn('Amazon 84.1%', top['reply'])
        n = PA.answer_panel_fact({'kind': 'brand', 'brand': 'Netflix'}, df, meta, {})
        self.assertIn('Netflix reaches 77.4% of the HBO Max audience.', n['reply'])   # a platform row is still reach

    def test_pull_discussion_names_the_brands_bought(self):
        text = PD.pull_summary('Nike', {}) if hasattr(PD, 'pull_summary') else ''
        if not text:
            with open(os.path.join(ROOT, 'prometheus', 'pull_discussion.py'), encoding='utf-8') as fh:
                text = fh.read()
        self.assertIn('the brands this audience bought (each a confirmed order, never a browse)', text)
        self.assertNotIn('each with its reach and its index', text)


if __name__ == '__main__':
    unittest.main(verbosity=1)
