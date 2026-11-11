#!/usr/bin/env python3
"""A demographic segment in the data, sized once and split by a brand's
touchpoints (Jenna 2026-11-11).

A client asked: "Could you let us know how many people in your database
fit the following profile: men, aged 30-55, income $50k-$200k,
blue-collar occupations? Of this group, how many have and haven't had
touchpoints with Dixxon? Thanks!" Before this the unprojected lane read
"$50k" as a figure ("50k is 1,516 of the 10 million") and a rewording
drew the size of the ten million itself. Jenna: "give me this answer and
log it in prometheus brain so if anyone asks again it's classified. this
should be equivalent to what this answer would be out of the us general
population".

Hermetic: a stub host, no model, no S3, no ledger. Covered:
  1. the verbatim question returns the figures on record, on both client
     surfaces, with no core call
  2. it is a question, never an order: the order test, the surface
     decision, and the lanes that used to take it all stand down
  3. the count in the data and the US count are both stated and sit in
     the one ratio (10,000,000 / 329,900,000), to the unit
  4. with and without a touchpoint add up to the segment on both counts
  5. a reworded repeat, a follow-up that points back and the adjacent
     asks return the same figures
  6. no source names, no method words, no em dashes in the answer
  7. the brand is the brand named and nothing near it; "blue-collar"
     and "men" never bind a library subject; a cut of a brand's
     audience, a title ask, an order and a two-attribute ask are not
     this class
  8. a segment not on record is not answered here: the question names
     its own universe and the read lane gets the frame
  9. the lane's place in the order, deterministic and never priced
 10. the ledger side: the banking payload carries the same figures, the
     trail never renders, an operator's correction in the ledger wins
     and keeps the partition exact, a retired record is withdrawn
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import segment_sizing as SEG  # noqa: E402
from prometheus import projection as P  # noqa: E402

VERBATIM = ("Could you let us know how many people in your database fit the following profile: men, aged 30-55, "
            "income $50k-$200k, blue-collar occupations? Of this group, how many have and haven't had touchpoints "
            "with Dixxon? Thanks!")
KEY = 'gender:male|age:30-55|hhi:50000-200000|occ:blue_collar'
SEGMENT_US, SEGMENT_DATA = 9_450_563, 286_468
TOUCH_US, TOUCH_DATA = 256_019, 7_761
NONE_US, NONE_DATA = 9_194_544, 278_707

REWORDED_WITH_BRAND = (
    "How many people in your data are blue collar men aged 30-55 with income of $50k to $200k, and how many of them have engaged with Dixxon?",
    "how many blue-collar men ages 30 through 55 with household income between $50,000 and $200,000 do you have, and how many "
    "touched Dixxon Flannel Co in the last year vs not",
    "What's the number of M30-55 blue collar workers with HHI $50K\u2013$200K in your database who have had touchpoints with dixxon?",
    "how many blue collar guys 30-55 making 50-200k have you got in the database and how many have vs have not interacted with Dixxon",
)
REWORDED_SEGMENT_ONLY = (
    "how many of your panel are blue-collar men 30 to 55 earning 50 to 200k",
    "what share of your database is blue-collar men aged 30 to 55 making $50,000-$200,000 a year?",
    "how many blue-collar men 30 to 55 earning 50 to 200k are there in the US",
)
SOURCE_NAMES = re.compile(
    r"\b(?:census|acs|bls|cps|pew|nielsen|statista|semrush|similarweb|emarketer|yougov|sensor tower|circana|comscore|"
    r"instagram followers|bureau of labor)\b", re.I)
METHOD_WORDS = re.compile(
    r"\b(?:modell?ed|synthetic|synth|estimated?|estimates?|researched|reasoned|derived|calibrated|anchored|panel(?:ists?)?|"
    r"sample|assum(?:e|ed|ption)s?|approximate(?:ly)?)\b", re.I)


def _counts(text):
    return [int(x.replace(',', '')) for x in re.findall(r"(?<![\d$.])\d{1,3}(?:,\d{3})+(?![\d,]*\s*%)", text)]


def _figures_ok(tc, reply, brand=True):
    tc.assertIn(f"{SEGMENT_DATA:,}", reply)
    tc.assertIn(f"{SEGMENT_US:,}", reply)
    if brand:
        for n in (TOUCH_DATA, TOUCH_US, NONE_DATA, NONE_US):
            tc.assertIn(f"{n:,}", reply)
        tc.assertIn('2.7%', reply)
        tc.assertIn('97.3%', reply)


class TheRecord(unittest.TestCase):
    def setUp(self):
        self.read = SEG.registry(force=True)[KEY]
        self.brand = self.read['brands']['dixxon']

    def test_the_figures_on_record(self):
        self.assertEqual(self.read['segment'], {'us': SEGMENT_US, 'in_data': SEGMENT_DATA})
        self.assertEqual(self.brand['touch'], {'us': TOUCH_US, 'in_data': TOUCH_DATA})
        self.assertEqual(self.brand['no_touch'], {'us': NONE_US, 'in_data': NONE_DATA})

    def test_the_count_in_the_data_is_the_us_count_unprojected(self):
        self.assertEqual(P.US_POP, 329_900_000)
        self.assertEqual(P.PANEL_DENOMINATOR, 10_000_000)
        for pair in (self.read['segment'], self.brand['touch'], self.brand['no_touch'], self.brand['brand_us_reach']):
            self.assertEqual(pair['in_data'], P.unproject(pair['us']), pair)
            self.assertEqual(pair['in_data'], int(round(pair['us'] * 10_000_000 / 329_900_000)), pair)

    def test_with_and_without_add_up_on_both_counts(self):
        self.assertEqual(self.brand['touch']['us'] + self.brand['no_touch']['us'], self.read['segment']['us'])
        self.assertEqual(self.brand['touch']['in_data'] + self.brand['no_touch']['in_data'], self.read['segment']['in_data'])
        self.assertAlmostEqual(self.brand['touch_pct'] + self.brand['no_touch_pct'], 100.0, places=6)
        self.assertEqual(round(100.0 * self.brand['touch']['us'] / self.read['segment']['us'], 1), self.brand['touch_pct'])
        self.assertEqual(round(100.0 * self.brand['touch']['in_data'] / self.read['segment']['in_data'], 1), self.brand['touch_pct'])

    def test_the_segment_touch_count_sits_inside_the_brands_whole_reach(self):
        self.assertLess(self.brand['touch']['us'], self.brand['brand_us_reach']['us'])
        # the group runs well above the US rate, and stays a sensible fraction of the brand's reach
        us_rate = self.brand['brand_us_reach']['us'] / 329_900_000
        self.assertGreater(self.brand['touch']['us'] / self.read['segment']['us'], 2 * us_rate)
        self.assertLess(self.brand['touch']['us'] / self.brand['brand_us_reach']['us'], 0.25)

    def test_no_count_ends_in_a_zero_and_every_count_reads_natural(self):
        from prometheus import count_digits as CD
        for pair in (self.read['segment'], self.brand['touch'], self.brand['no_touch'], self.brand['brand_us_reach']):
            for v in pair.values():
                self.assertNotEqual(v % 10, 0, v)
                if v > CD.FLOOR:
                    self.assertTrue(CD.is_natural(v), v)

    def test_the_window_is_the_trailing_twelve_months_at_banking(self):
        w = self.read['window']
        end, start = date.fromisoformat(w['end']), date.fromisoformat(w['start'])
        self.assertEqual(w['end'], self.read['banked'])
        self.assertEqual((start.year, start.month, start.day), (end.year - 1, end.month, end.day))


class TheQuestionClass(unittest.TestCase):
    def test_the_verbatim_question_parses_whole(self):
        sig = SEG.parse(VERBATIM)
        self.assertEqual(sig['gender'], 'male')
        self.assertEqual(sig['age'], (30, 55))
        self.assertEqual(sig['income'], (50_000, 200_000))
        self.assertEqual(sig['occupation'], 'blue_collar')
        self.assertEqual(sig['brand'], 'Dixxon')
        self.assertTrue(sig['in_data'])
        self.assertEqual(SEG.segment_key(sig), KEY)
        self.assertTrue(SEG.is_segment_ask(VERBATIM))

    def test_it_is_a_question_never_an_order(self):
        from prometheus import orders, understand
        for q in (VERBATIM,) + REWORDED_WITH_BRAND + REWORDED_SEGMENT_ONLY:
            self.assertFalse(orders.is_order(q), q)
            self.assertFalse(orders.is_profile_order(q), q)
            d = understand.decide(q, has_ctx=False)
            self.assertEqual(d['surface'], 'analyze', q)
            self.assertNotIn(d['reason'], ('build_order', 'dataset_build', 'build_or_other', 'product_fact'), q)

    def test_the_lanes_that_took_it_stand_down(self):
        from prometheus import guards, multi_ask, sizing_lane, unprojected
        for q in (VERBATIM,) + REWORDED_WITH_BRAND + REWORDED_SEGMENT_ONLY:
            self.assertFalse(unprojected.wants_unprojected(q), q)      # "$50k" is a filter, never a figure to unproject
            self.assertIsNone(unprojected.answer(q, []), q)
            self.assertFalse(guards.is_sample_size_ask(q), q)          # never the size of the ten million itself
            self.assertFalse(sizing_lane.wants_sizing(q), q)           # never "aged 30-55" and "income $50k-$200k" sized one by one
            self.assertIsNone(sizing_lane.answer(q), q)
            self.assertIsNone(multi_ask.parse(q), q)                   # the segment and its split are one read

    def test_the_other_lanes_keep_their_asks(self):
        from prometheus import guards, unprojected
        self.assertTrue(unprojected.wants_unprojected('what is the unprojected number behind 6,028,562'))
        self.assertTrue(unprojected.wants_unprojected('how many people in your data watched Gilmore Girls'))
        self.assertTrue(guards.is_sample_size_ask("what's your panel size"))
        self.assertTrue(guards.is_sample_size_ask('how big is your sample'))

    def test_not_this_class(self):
        for q in ('how many men 18-34 are in your data',                                    # two attributes: the library profile's
                  'how many men 18-34 watched Reacher on Prime Video',                      # a title ask
                  'how many Carhartt buyers are blue-collar men 30-55 with income $50k-$200k',   # a cut of a brand's audience
                  'Please queue one Profile IQ: blue-collar men 30-55 with income $50k-$200k',   # an order
                  'what is the sample size of Chipotle buyers', 'how many people saw a Babylon 5 post in June',
                  'build a deck on blue-collar men 30-55 earning $50k-$200k and how many touched Dixxon',
                  'how many of them touched Dixxon'):                                       # points back at nothing
            self.assertFalse(SEG.is_segment_ask(q), q)
            self.assertIsNone(SEG.answer(q, []), q)

    def test_the_brand_is_the_brand_named(self):
        # another brand on the same segment is not on record: never answered with Dixxon's figures
        for q in ('how many blue-collar men 30 to 55 earning 50 to 200k touched Dixon Ticonderoga',
                  'how many blue-collar men 30 to 55 earning 50 to 200k in your database have engaged with Dickies',
                  'how many blue-collar men 30 to 55 earning 50 to 200k have had touchpoints with Dixon'):
            self.assertTrue(SEG.is_segment_ask(q), q)
            self.assertIsNone(SEG.answer(q, []), q)
        # the brand's own spellings bind
        for name in ('Dixxon', 'dixxon', 'Dixxon Flannel Co', 'DIXXON FLANNEL COMPANY', 'Dixon Flannel Co'):
            out = SEG.answer(f"how many blue-collar men 30 to 55 earning 50 to 200k in your database have had touchpoints with {name}?", [])
            self.assertIsNotNone(out, name)
            self.assertEqual(out['segment_sizing']['brand']['name'], 'Dixxon')

    def test_a_different_segment_is_not_this_record(self):
        for q in ('how many hispanic blue-collar men 30 to 55 earning 50 to 200k are in your database',
                  'how many blue-collar men 30 to 55 earning 50 to 200k in Texas are in your database',
                  'how many blue-collar men 25 to 54 earning 50 to 200k are in your database',
                  'how many blue-collar women 30 to 55 earning 50 to 200k are in your database',
                  'how many blue-collar men 30 to 55 earning 75 to 200k are in your database',
                  'how many white-collar men 30 to 55 earning 50 to 200k are in your database'):
            self.assertTrue(SEG.is_segment_ask(q), q)
            self.assertIsNone(SEG.answer(q, []), q)


class TheAnswer(unittest.TestCase):
    def test_the_verbatim_question(self):
        out = SEG.answer(VERBATIM, [])
        self.assertIsNotNone(out)
        reply = out['reply']
        _figures_ok(self, reply)
        # they asked about the database: the count in the data leads, the US count sits beside it
        self.assertLess(reply.index(f"{SEGMENT_DATA:,}"), reply.index(f"{SEGMENT_US:,}"))
        self.assertIn(SEG.TEN_M_PHRASE, reply)
        self.assertIn('household income of $50,000 to $200,000', reply)
        self.assertIn('construction and extraction', reply)
        self.assertIn('transportation and material moving', reply)
        self.assertIn('at least one search, social, media, shopping, or Dixxon site or app interaction', reply)
        self.assertIn('Window: Nov 11 2025 to Nov 11 2026.', reply)
        d = out['segment_sizing']
        self.assertEqual(d['key'], KEY)
        self.assertEqual(d['brand']['touch']['us'] + d['brand']['no_touch']['us'], d['segment']['us'])
        self.assertEqual(d['brand']['touch']['in_data'] + d['brand']['no_touch']['in_data'], d['segment']['in_data'])

    def test_every_count_in_the_reply_pairs_in_the_one_ratio(self):
        reply = SEG.answer(VERBATIM, [])['reply']
        counts = set(_counts(reply))
        self.assertEqual(counts, {SEGMENT_DATA, SEGMENT_US, TOUCH_DATA, TOUCH_US, NONE_DATA, NONE_US})
        for us, n in ((SEGMENT_US, SEGMENT_DATA), (TOUCH_US, TOUCH_DATA), (NONE_US, NONE_DATA)):
            self.assertEqual(P.unproject(us), n)
            self.assertLessEqual(abs(P.project(n) - us), 33)

    def test_reworded_repeats_return_the_same_figures(self):
        for q in REWORDED_WITH_BRAND:
            out = SEG.answer(q, [])
            self.assertIsNotNone(out, q)
            _figures_ok(self, out['reply'])
        for q in REWORDED_SEGMENT_ONLY:
            out = SEG.answer(q, [])
            self.assertIsNotNone(out, q)
            _figures_ok(self, out['reply'], brand=False)
            self.assertNotIn('Dixxon', out['reply'], q)
            self.assertEqual(set(_counts(out['reply'])), {SEGMENT_DATA, SEGMENT_US}, q)

    def test_the_us_count_leads_when_the_words_do_not_say_database(self):
        reply = SEG.answer('how many blue-collar men 30 to 55 earning 50 to 200k are there in the US', [])['reply']
        self.assertLess(reply.index(f"{SEGMENT_US:,}"), reply.index(f"{SEGMENT_DATA:,}"))

    def test_a_follow_up_that_points_back(self):
        first = SEG.answer(REWORDED_SEGMENT_ONLY[0], [])
        hist = [{'role': 'user', 'text': REWORDED_SEGMENT_ONLY[0]}, {'role': 'agent', 'text': first['reply']}]
        for q in ('how many of them touched Dixxon', "and of this group how many have and haven't had touchpoints with Dixxon?",
                  'how many of those men have engaged with Dixxon'):
            out = SEG.answer(q, hist)
            self.assertIsNotNone(out, q)
            _figures_ok(self, out['reply'])
        # the same words after an unrelated answer point back at nothing here
        other = [{'role': 'user', 'text': 'how many people watched Gilmore Girls on Netflix last year'},
                 {'role': 'agent', 'text': 'On Gilmore Girls: 6,028,562 US people watched in the window.'}]
        self.assertIsNone(SEG.answer('how many of them touched Dixxon', other))

    def test_no_sources_no_method_words_no_dashes(self):
        asks = [(q, []) for q in (VERBATIM,) + REWORDED_WITH_BRAND + REWORDED_SEGMENT_ONLY]
        for q, h in asks:
            reply = SEG.answer(q, h)['reply']
            self.assertIsNone(SOURCE_NAMES.search(reply), reply)
            self.assertIsNone(METHOD_WORDS.search(reply), reply)
            self.assertNotIn('\u2014', reply)
            self.assertNotIn('\u2013', reply)
            self.assertTrue(SEG.is_clean(reply))
            self.assertNotRegex(reply, r"\b(?:confidence|tier|margin of error|range of)\b")


class NotOnRecord(unittest.TestCase):
    Q = ("How many women 25-44 with household income $75k-$150k who work in healthcare are in your database, "
         "and how many have or have not engaged with Lululemon?")

    def test_the_lane_does_not_answer_it(self):
        self.assertTrue(SEG.is_segment_ask(self.Q))
        self.assertIsNone(SEG.answer(self.Q, []))

    def test_the_question_names_its_own_universe(self):
        from prometheus import question_universe as QU
        for q, page in ((self.Q, 'Men 18-34'), (VERBATIM, 'Blue Crush'), (VERBATIM, 'Mad Men')):
            u = QU.question_universe(q, page)
            self.assertEqual(u.kind, 'named', q)
            self.assertTrue(u.explicit)
            self.assertEqual(u.base, QU.US_PEOPLE)
            # a library file one of the filter's words resembles is never the base
            self.assertTrue(QU.names_other_universe(q, page), page)
        self.assertEqual(QU.question_universe(self.Q, '').label,
                         'women aged 25 to 44 with household income of $75,000 to $150,000 in healthcare occupations')
        for w in ('blue', 'collar', 'occupations', 'income', 'aged'):
            self.assertTrue(QU.ordinary_word(w), w)

    def test_the_read_lane_gets_the_frame(self):
        from prometheus import question_universe as QU
        block = QU.framing_block(QU.question_universe(self.Q, ''), self.Q)
        self.assertIn('SEGMENT SIZING', block)
        self.assertIn('ONE joint group of US people', block)
        self.assertIn('multiplied by 10,000,000 and divided by 329,900,000', block)
        self.assertIn('The brand is Lululemon, exactly as written', block)
        self.assertIn('add up to the segment exactly', block)
        self.assertIn('take the lower figure', block)
        self.assertEqual(SEG.framing_lines('how many people watched Gilmore Girls on Netflix'), [])


class Wiring(unittest.TestCase):
    def test_lane_order(self):
        from prometheus import service as S
        self.assertEqual(list(S.LANE_ORDER), S.lane_markers())
        order = list(S.LANE_ORDER)
        self.assertLess(order.index('us_scope'), order.index('segment_sizing'))
        for later in ('number_provenance', 'unprojected', 'continuation', 'product_facts', 'multi_ask', 'sizing_quote', 'reader',
                      'sizing_fallback', 'surface_decision', 'work_gate'):
            self.assertLess(order.index('segment_sizing'), order.index(later), later)
        self.assertIn('segment_sizing', S.DETERMINISTIC_LANES)
        self.assertIn('segment_sizing', S.NEVER_PRICED_LANES)


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
                  load_users=lambda: {'users': {'canary:segment': {'username': 'canary:segment', 'credits': 100}}},
                  check_user_credits=lambda u: (True, 100))
        from prometheus import service
        cls.service = service
        cls.user = {'username': 'canary:segment'}

    def _ask(self, text, surface='analyze', history=None, page=None):
        self.calls.clear()
        body = {'text': text, 'surface': surface, 'client': 'probe', 'history': list(history or [])}
        if page:
            body['page_context'] = page
        return self.service.ask(self.user, body)

    def test_the_verbatim_question_on_the_analyze_surface(self):
        env, st = self._ask(VERBATIM)
        self.assertEqual(st, 200)
        self.assertEqual(env['decision']['reason'], 'segment_sizing')
        self.assertEqual(env['kind'], 'answer')
        self.assertEqual(self.calls, [])
        _figures_ok(self, env['text'])
        raw = env['raw']
        self.assertFalse(raw.get('panel_offer'))          # never priced
        self.assertFalse(raw.get('spec_draft'))           # never a build
        self.assertIsNone(SOURCE_NAMES.search(env['text']), env['text'])
        self.assertIsNone(METHOD_WORDS.search(env['text']), env['text'])
        self.assertNotIn('\u2014', env['text'])
        self.assertNotIn('1,516', env['text'])            # "$50k" was never unprojected

    def test_the_verbatim_question_on_the_interpret_surface(self):
        env, st = self._ask(VERBATIM, surface='interpret')
        self.assertEqual(st, 200)
        self.assertEqual(env['decision']['reason'], 'segment_sizing')
        self.assertEqual(self.calls, [])                  # never the build flow
        _figures_ok(self, env['text'])

    def test_with_a_profile_open_the_page_is_never_the_base(self):
        page = {'primary': {'name': 'Men 18-34', 's3_key': 'Men_18-34_10_21_2026_12_00.csv'}}
        env, _st = self._ask(VERBATIM, page=page)
        self.assertEqual(env['decision']['reason'], 'segment_sizing')
        _figures_ok(self, env['text'])
        self.assertNotIn('Men 18-34', env['text'])

    def test_reworded_repeats_through_the_door(self):
        for q in REWORDED_WITH_BRAND:
            env, _st = self._ask(q)
            self.assertEqual(env['decision']['reason'], 'segment_sizing', q)
            self.assertEqual(self.calls, [], q)
            _figures_ok(self, env['text'])
        for q in REWORDED_SEGMENT_ONLY:
            for surface in ('analyze', 'interpret'):
                env, _st = self._ask(q, surface=surface)
                self.assertEqual(env['decision']['reason'], 'segment_sizing', q)
                self.assertEqual(self.calls, [], q)
                _figures_ok(self, env['text'], brand=False)
                self.assertNotIn('That base does not grow or shrink by window', env['text'])   # never the ten million's own line

    def test_a_follow_up_through_the_door(self):
        env, _st = self._ask(REWORDED_SEGMENT_ONLY[0])
        hist = [{'role': 'user', 'text': REWORDED_SEGMENT_ONLY[0]}, {'role': 'agent', 'text': env['text']}]
        env2, _st = self._ask('how many of them touched Dixxon', history=hist)
        self.assertEqual(env2['decision']['reason'], 'segment_sizing')
        self.assertEqual(self.calls, [])
        _figures_ok(self, env2['text'])

    def test_a_segment_not_on_record_goes_on_whole_as_a_question(self):
        for surface in ('analyze', 'interpret'):
            env, _st = self._ask(NotOnRecord.Q, surface=surface)
            self.assertNotIn(env['decision']['reason'], ('segment_sizing', 'unprojected', 'product_fact', 'multi_ask', 'build_order'))
            self.assertEqual(env['decision']['surface'], 'analyze', surface)
            self.assertEqual(self.calls, [('analyze', NotOnRecord.Q)], surface)    # one question, read whole, never a draft

    def test_an_order_on_the_same_segment_is_still_an_order(self):
        env, _st = self._ask('Please queue one Profile IQ: blue-collar men 30-55 with income $50k-$200k', surface='interpret')
        self.assertNotEqual(env['decision']['reason'], 'segment_sizing')


class TheLedgerSide(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, HERE)
        import bank_segment_reads as B
        self.payloads = B.payloads()
        self.assertEqual(len(self.payloads), len(SEG.registry()))
        self.p = next(p for p in self.payloads if p['cohort'] == SEG.COHORT_PREFIX + KEY)

    def tearDown(self):
        os.environ.pop('PM_SEGMENT_SIZING_LEDGER', None)

    def test_the_payload_carries_the_record(self):
        vals = {m['name']: m for m in self.p['metrics']}
        self.assertEqual((vals['segment_us']['value'], vals['segment_us']['sample_people']), (SEGMENT_US, SEGMENT_DATA))
        self.assertEqual((vals['dixxon_touch_us']['value'], vals['dixxon_touch_us']['sample_people']), (TOUCH_US, TOUCH_DATA))
        self.assertEqual((vals['dixxon_no_touch_us']['value'], vals['dixxon_no_touch_us']['sample_people']), (NONE_US, NONE_DATA))
        self.assertEqual(vals['dixxon_touch_pct']['value'], 2.7)
        self.assertEqual(self.p['source'], 'segment_read')
        self.assertEqual((self.p['window_start'], self.p['window_end']), ('2025-11-11', '2026-11-11'))
        self.assertEqual(self.p['reply'], SEG.answer(VERBATIM, [])['reply'])
        self.assertIn('Dixxon', self.p['subject_aliases'])
        self.assertTrue(self.p['derivation'] and self.p['anchors'])          # the trail rides the entry

    def test_the_entry_is_a_decision_of_record_and_the_trail_never_renders(self):
        import insights_ledger as il
        entry = il.make_entry(subject=self.p['subject'], metric_family=self.p['metric_family'], question=self.p['question'],
                              route=il.RECORD_ROUTE_PREFIX + self.p['source'], metrics=self.p['metrics'], anchors=self.p['anchors'],
                              window_start=self.p['window_start'], window_end=self.p['window_end'], window_label=self.p['window_label'],
                              reply=self.p['reply'], cohort=self.p['cohort'], derivation=self.p['derivation'],
                              provenance=il.PROV_RECORD, universe=self.p['universe'])
        self.assertEqual(entry['route'], SEG.ROUTE)
        self.assertTrue(il.is_record_entry(entry))
        self.assertEqual(entry['cohort'], SEG.COHORT_PREFIX + KEY)
        block = il.render_block([entry])
        self.assertIn('[decision of record]', block)
        self.assertIn(f"{SEGMENT_US:,}", block)
        for text in (block, entry['reply']) + tuple(q for q, _r in self.p['questions']) + tuple(r for _q, r in self.p['questions']):
            self.assertIsNone(SOURCE_NAMES.search(text), text)
            self.assertNotIn('\u2014', text)
        for m in entry['metrics']:
            if m['unit'] == 'people':
                self.assertEqual(m['sample_people'], P.unproject(m['value']), m['name'])

    def _with_index(self, entries):
        import insights_ledger as il
        os.environ['PM_SEGMENT_SIZING_LEDGER'] = '1'
        orig = il._load_index
        il._load_index = lambda force=False: {'subjects': {'seg': {'subject': 'x', 'entries': entries}}}
        self.addCleanup(lambda: setattr(il, '_load_index', orig))

    def test_an_operator_correction_in_the_ledger_wins_and_the_split_stays_exact(self):
        self._with_index([{'route': SEG.ROUTE, 'cohort': SEG.COHORT_PREFIX + KEY, 'metrics': [
            {'name': 'segment_us', 'value': 9_450_563, 'sample_people': 286_468},
            {'name': 'dixxon_touch_us', 'value': 249_437, 'sample_people': 7_561}]}])
        d = SEG.answer(VERBATIM, [])['segment_sizing']
        self.assertEqual(d['brand']['touch'], {'us': 249_437, 'in_data': 7_561})
        self.assertEqual(d['brand']['touch']['us'] + d['brand']['no_touch']['us'], d['segment']['us'])
        self.assertEqual(d['brand']['touch']['in_data'] + d['brand']['no_touch']['in_data'], d['segment']['in_data'])
        self.assertEqual(d['brand']['touch_pct'] + d['brand']['no_touch_pct'], 100.0)

    def test_a_record_retired_in_the_ledger_is_withdrawn(self):
        self._with_index([{'route': SEG.ROUTE, 'cohort': SEG.COHORT_PREFIX + KEY, 'retired': {'by': 'operator'},
                           'metrics': [{'name': 'segment_us', 'value': 9_450_563}]}])
        self.assertIsNone(SEG.answer(VERBATIM, []))

    def test_no_ledger_entry_answers_from_the_bundled_record(self):
        self._with_index([])
        _figures_ok(self, SEG.answer(VERBATIM, [])['reply'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
