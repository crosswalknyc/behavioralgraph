#!/usr/bin/env python3
"""Grace Yin's questions (Amazon, 2026-11-08 to 2026-11-10) as a standing
regression set (Jenna 2026-11-10: "it's still missing the mark on Grace
questions ... ensure that her questions are being accurately answered
from here out").

Every question she asked rides here with its page context, asserting the
lane or decision and the key shape of the answer:

  * an ORDER routes to the build flow with the explicit window and the
    deliverable notes (prometheus.orders); no keyword lane takes it, the
    unprojected reply never fires on it, and an unlimited seat reads
    "Included with your plan." with no chip (the build starts)
  * a count question answers with a count (a read), never a build
  * a methodology question answers directly (device coverage, viewing
    time, the source of age, how many men 18 to 34 are in the data)
  * a sign-up count never goes to the wizard
  * two figures that must agree are a reconciliation from the record,
    never "typed by the model"
  * a per quarter new versus returning split is a read, not a size quote

Hermetic: a stub host carrying Grace's seat (unlimited), stub cores that
record their calls, no model, no S3, no app module.
"""
from __future__ import annotations

import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class _NoAppModule:
    def find_spec(self, name, path=None, target=None):
        if name == 'app':
            raise ImportError('app is not importable inside this hermetic suite')
        return None


sys.meta_path.insert(0, _NoAppModule())

from prometheus import orders as O  # noqa: E402
from prometheus import understand as U  # noqa: E402
from prometheus import inline_dates as ID  # noqa: E402
from prometheus import unprojected as UNP  # noqa: E402
from prometheus import reconcile_lane as RL  # noqa: E402
from prometheus import number_provenance as NP  # noqa: E402
from prometheus import sizing_lane as SZ  # noqa: E402
from prometheus import methodology_answers as MA  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus import rate_card as RC  # noqa: E402
from prometheus import intake_guard as IG  # noqa: E402
from prometheus import data_capability as DC  # noqa: E402
import prometheus_analysis as pma  # noqa: E402

GRACE = {'username': 'graceyin', 'email': 'graceyin@amazon.com', 'company': 'Amazon', 'role': 'user',
         'credits': -1, 'unlimited': True, 'paying_customer': True}
PAGE = 'Hazbin Hotel Season 1 - Amazon Prime New Sign-Ups Viewers'
CTX = {'primary': {'name': PAGE, 's3_key': 'Hazbin_Hotel_Season_1_-_Amazon_Prime_New_Sign-Ups_Viewers_11_08_2026_17_09.csv'},
       'cuts': []}
BANNED_RX = re.compile(r"\b(?:panel|panelists?|sample|modell?ed|model|synth\w*|estimated?)\b", re.I)

# ---- her words, verbatim ----------------------------------------------------
ORDER = ("Please queue one Profile IQ: US people who watched Fallout Season 2 on Prime Video, Nov 1 2025 \u2013 Oct 31 2026. "
         "Include reach, age and gender split, top other titles and genres they watch, and their streaming platform mix. "
         "Deliver as a CSV with the unprojected panelist count for each figure.")
ORDER_SEVEN = (". This is the earlier 18\u201344 no-Prime-Video read, not the seven segments in my last message; please queue "
               "those as separate Profile IQs (US only, Nov 1 2025 \u2013 Oct 31 2026), starting with #1 and #7.\n"
               "\u258e On this read:\n\u258e 1. How does 123,003,743 people with no Prime Video plays reconcile with the ~137M US "
               "population aged 18\u201344, and with your earlier figure of 48.6% (~53.5M)?\n\u258e 2. You say eight services but show "
               "five. Please send all eight with penetration.\n\u258e 3. Please send the CSV with the unprojected panelist count "
               "behind each figure.")
CHIP_ORDER = "Queue a Profile IQ: US men aged 18 to 34, Nov 1 2025 to Oct 31 2026"
METHODOLOGY = ("1. Your panel is desktop software. How much of mobile viewing do you capture, especially TikTok and YouTube on "
               "phones and TVs? What share of 18\u201334 men's video time do you estimate you miss?\n"
               "2. How many US panelists are men aged 18\u201334? (You need about 1,000 or more for the cuts below to mean anything.)\n"
               "3. Can you measure watch duration per video, and the video's own length? Can you separate \"watched 80+ minutes in "
               "one sitting\" from \"opened an 80-minute video\"?\n"
               "4. Is age taken from the panelist's own profile, or is it a household-level guess? We saw minors counted as "
               "\"sign-ups,\" so it matters for an 18\u201334 cut.")
STATUS = ("did you answer the questions for 1. Men 18\u201334, all (baseline)\n2. Men 18\u201334 with 3+ YouTube viewing sessions of "
          "80+ minutes in a single month")
COUNT_18_44 = ("How many US people aged 18\u201344 used at least one paid streaming service between November 1, 2025 and "
               "October 31, 2026, but had no Prime Video plays in that period?")
SIGNUP_COUNT = ("How many US consumers signed up for Amazon Prime and Hazbin Hotel Season 1 was the first thing they watched on "
                "prime video after joining from January 19 and February 15, 2024  ?")
RECONCILE = ("Your January\u2013September 2026 count for Hazbin Hotel and Helluva Boss YouTube and social engagers is 4,287,316, "
             "but your quarterly readout showed 7.1M in Q1 2026 alone. Which is correct, and how do the two definitions differ?")
NEW_VS_RETURNING = ("For the combined Hazbin Hotel and Helluva Boss US audience on YouTube and social, how many people each "
                    "quarter from Q1 2024 to Q3 2026 were new (no engagement in any earlier quarter) vs returning?")
SERVICE_MIX = ("Audience: US people aged 18\u201344 who used a paid streaming service between Nov 1, 2025 and Oct 31, 2026 and had no "
               "Prime Video plays.\n\u258e What share used each of these services: Netflix, Disney+, Hulu, Max, Peacock, Paramount+, "
               "Apple TV+, Crunchyroll, YouTube?")
DOWNLOADABLE = "Please provide as a downloadable file, with panelist counts, ranges, and the index vs all US paid streamers aged 18\u201344."
UNPROJECTED_ONE = "what is the unprojected count behind the 6,195,357?"
PILOT_YT = "How many US people watched the Hazbin Hotel pilot on YouTube between January 19, 2023 and January 18, 2024?"
POINTS_AT_PAGE = ("inside a cohort of 1,368,062 US viewers in the January 19 to February 15, 2024 window. "
                  "are these people new sign ups for prime memberhsip")

HISTORY_TWO_READS = [
    {'role': 'user', 'text': 'How has the US audience for Hazbin Hotel and Helluva Boss content on YouTube and social platforms changed each quarter since January 2024?'},
    {'role': 'agent', 'text': ('On Hazbin Hotel and Helluva Boss (YouTube and social):\n\nThe combined Hazbin Hotel and Helluva Boss '
                               'audience on YouTube and social peaked at 8,917,432 US people in Q4 2025 on the Season 2 premiere, and '
                               'sits at 6,218,467 in Q3 2026.\n\nQuarter mix, Jan 1 2024 to Nov 8 2026, by quarter\n- Q1 2026: 10.2% '
                               'share, 69.6% penetration. Q1 2026 at 7,106,529 holds 79.7% of the premiere quarter\n\nHow these are '
                               'counted:\n- Window: Jan 1 2024 to Nov 8 2026, by quarter.\n- Base: the Hazbin Hotel and Helluva Boss '
                               '(YouTube and social) audience, measured across 10M US consumers and projected to the US.'),
     'meta': {'kind': 'read', 'read_job_id': 'r1', 'subject': 'Hazbin Hotel and Helluva Boss (YouTube and social)',
              'figures': [{'value': 7106529.0, 'kind': 'count', 'label': 'typed by the model', 'source': 'model_typed'}]}},
    {'role': 'user', 'text': 'Of US people who engaged with Hazbin Hotel or Helluva Boss on YouTube or social between January 1 and September 30, 2026, how many have not watched Hazbin Hotel on Prime Video?'},
    {'role': 'agent', 'text': ('1,750,475 of the 4,287,316 US people who engaged with Hazbin Hotel or Helluva Boss on YouTube or social '
                               'between Jan 1 and Sep 30 2026 have never played Hazbin Hotel on Prime Video.\n\nHow these are counted:\n'
                               '- Window: Jan 1 2026 to Sep 30 2026.\n- Base: US people who engaged on YouTube or social, measured across '
                               '10M US consumers and projected to the US.'),
     'meta': {'kind': 'read', 'read_job_id': 'r2', 'subject': 'Hazbin Hotel and Helluva Boss (YouTube and social engagers)',
              'figures': [{'value': 4287316.0, 'kind': 'count', 'label': 'typed by the model', 'source': 'model_typed'},
                          {'value': 1750475.0, 'kind': 'count', 'label': 'typed by the model', 'source': 'model_typed'}]}},
]

CALLS = []


def _bind_host():
    from prometheus.host import host

    def _analyze_core(user, body, text, history):
        CALLS.append(('analyze', text, dict(body)))
        return {'success': True, 'action': 'answer', 'read_job_id': 'j1',
                'reply': 'On it: reading now. The numbers land right here.', 'followups': []}

    def _interpret_core(user, body, text, history):
        CALLS.append(('interpret', text, dict(body)))
        return {'success': True, 'spec_draft': {'subject': 'x'}, 'estimated_credits': 5}

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
              load_users=lambda: {'users': {'graceyin': dict(GRACE)}},
              check_user_credits=lambda u: (True, -1), consume_credit=lambda *a, **k: True,
              pricing_question=lambda t: bool(re.search(r"\b(?:cost|costs|price|pricing|rate card|charge)\b", str(t or ''), re.I)),
              pricing_copy_for=lambda u: RC.render('$'), pricing_copy=RC.render('$'))
    return host


def _ask(text, *, ctx=None, surface=None, history=None):
    from prometheus import service
    CALLS.clear()
    body = {'text': text, 'client': 'dashboard', 'history': list(history or [])}
    if ctx is not None:
        body['context'] = ctx
    if surface:
        body['surface'] = surface
        body['_client_surface_sent'] = surface   # what the legacy route stamps from the widget's own call
    env, st = service.ask(dict(GRACE), body)
    return env, st, env.get('raw') or {}


class OrdersAreOrders(unittest.TestCase):
    def test_her_order_and_its_cousins(self):
        for t in (ORDER, ORDER_SEVEN, CHIP_ORDER, 'run a profile on Fallout Season 2 viewers on Prime Video',
                  'build a twin of Fallout Season 2 viewers', 'Pull Subscriber IQ for Dark Matter on Apple TV+',
                  'set up attribution tracking for the Fallout Season 2 campaign', 'open a focus group on the Fallout pilot',
                  'can you build me a Nike profile', 'please run a profile on Taylor Swift and reconcile it with the unprojected count',
                  'I need a profile of Chipotle buyers with the raw numbers'):
            self.assertTrue(O.is_order(t), t)
        self.assertEqual(O.product_of(ORDER), 'profile')
        self.assertEqual(O.product_of('Pull Subscriber IQ for Dark Matter on Apple TV+'), 'subscriber_iq')
        self.assertEqual(O.product_of('build a twin of Fallout Season 2 viewers'), 'twin')
        self.assertTrue(O.is_profile_order(ORDER))
        self.assertTrue(O.is_profile_order(ORDER_SEVEN))
        self.assertFalse(O.is_profile_order('Pull Subscriber IQ for Dark Matter on Apple TV+'))

    def test_her_questions_are_not_orders(self):
        for t in (METHODOLOGY, STATUS, COUNT_18_44, SIGNUP_COUNT, RECONCILE, NEW_VS_RETURNING, SERVICE_MIX, DOWNLOADABLE,
                  UNPROJECTED_ONE, PILOT_YT, POINTS_AT_PAGE, 'did you queue the Fallout profile?', 'what does a profile cost?',
                  'how long does a build take', "Don't build a new profile. Answer this from the open one",
                  'why do you keep building profiles', 'I already pulled the profile', 'is the build done?',
                  'can you build profiles?', 'cancel the build'):
            self.assertFalse(O.is_order(t), t)

    def test_the_deliverable_notes_ride_apart_from_the_order(self):
        core, notes = O.split(ORDER)
        self.assertEqual(core, 'Please queue one Profile IQ: US people who watched Fallout Season 2 on Prime Video, Nov 1 2025 \u2013 Oct 31 2026.')
        self.assertIn('Include reach, age and gender split', notes)
        self.assertIn('Deliver as a CSV with the unprojected panelist count for each figure', notes)
        self.assertNotIn('Fallout', notes)
        # a question is handed back whole
        self.assertEqual(O.split(COUNT_18_44), (COUNT_18_44, ''))

    def test_the_en_dash_window_binds(self):
        for phrase in ('Nov 1 2025 \u2013 Oct 31 2026', 'Nov 1 2025 to Oct 31 2026', 'Nov 1, 2025 - Oct 31, 2026',
                       'November 1 2025 \u2014 October 31 2026'):
            w = ID.parse_inline_window(f'US people who watched Fallout Season 2 on Prime Video, {phrase}.')
            self.assertIsNotNone(w, phrase)
            self.assertEqual((w['start'], w['end']), ('2025-11-01', '2026-10-31'), phrase)
            self.assertFalse(w.get('coarse'))
        w = ID.parse_inline_window(ORDER)
        self.assertEqual((w['start'], w['end']), ('2025-11-01', '2026-10-31'))
        self.assertEqual(ID.window_label(w['start'], w['end']), 'Nov 1 2025 to Oct 31 2026')

    def test_the_build_line_for_her_seat(self):
        line = O.build_line('Fallout Season 2 Viewers on Prime Video', 'Nov 1 2025 to Oct 31 2026', notes=O.deliverable_notes(ORDER))
        self.assertTrue(line.startswith('Included with your plan. Profile building: Fallout Season 2 Viewers on Prime Video, Nov 1 2025 to Oct 31 2026'))
        self.assertIn('Avid Fan tier', line)
        self.assertIn('the age and gender split', line)
        self.assertIn('streaming platform mix', line)
        self.assertIn('the count inside the 10 million US consumers we measure (the Raw column)', line)
        self.assertIn('unprojected count for each figure is already in the file', line)
        self.assertIsNone(BANNED_RX.search(line.replace('unprojected', '')), line)
        self.assertNotIn('\u2014', line)
        self.assertNotIn('\u2013', line)


class TheSurfaceReadsAnOrderAsABuild(unittest.TestCase):
    def test_decide(self):
        for t in (ORDER, ORDER_SEVEN, CHIP_ORDER):
            for ctx in (False, True):
                d = U.decide(t, has_ctx=ctx)
                self.assertEqual((d['surface'], d['reason']), ('interpret', 'build_order'), (t[:60], ctx, d))
        for t in (COUNT_18_44, RECONCILE, UNPROJECTED_ONE, METHODOLOGY, 'did you queue the Fallout profile?',
                  'what does a profile cost?'):
            d = U.decide(t, has_ctx=False)
            self.assertNotEqual(d['reason'], 'build_order', (t[:60], d))

    def test_routing_row(self):
        from prometheus import routing_table as rt
        row = rt.row_for('build_order')
        self.assertIsNotNone(row)
        self.assertEqual(row['surface'], 'interpret')


class ServiceEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _bind_host()

    def test_the_order_goes_to_the_build_flow_not_the_unprojected_lane(self):
        env, st, raw = _ask(ORDER)
        self.assertEqual(st, 200)
        self.assertEqual(env['decision']['reason'], 'build_order', env['decision'])
        self.assertEqual(env['decision']['surface'], 'interpret')
        self.assertEqual([c[0] for c in CALLS], ['interpret'])
        self.assertNotIn('unproject', str(raw.get('reply') or ''))
        self.assertNotIn('run a profile on', str(raw.get('reply') or ''))
        body = CALLS[0][2]
        self.assertTrue(body.get('_pm_order'))
        self.assertEqual(body.get('_pm_order_text'), ORDER)

    def test_the_order_on_the_analyze_surface_hands_off_to_the_build_flow(self):
        env, st, raw = _ask(ORDER, surface='analyze')
        self.assertEqual(env['decision']['reason'], 'handoff_to_build_flow', env['decision'])
        self.assertEqual(raw.get('action'), 'build_profile')
        self.assertEqual(CALLS, [])
        # with the sign-ups profile open too: the order is the order
        env2, _st2, raw2 = _ask(ORDER, ctx=CTX, surface='analyze')
        self.assertEqual(env2['decision']['reason'], 'handoff_to_build_flow', env2['decision'])

    def test_the_seven_segment_order_is_an_order(self):
        env, st, raw = _ask(ORDER_SEVEN)
        self.assertEqual(env['decision']['reason'], 'build_order', env['decision'])
        self.assertNotIn('US only. Every figure', str(raw.get('reply') or ''))

    def test_a_chip_order_builds(self):
        env, st, raw = _ask(CHIP_ORDER)
        self.assertEqual(env['decision']['reason'], 'build_order', env['decision'])
        self.assertEqual([c[0] for c in CALLS], ['interpret'])

    def test_methodology_questions_answer_directly(self):
        env, st, raw = _ask(METHODOLOGY)
        self.assertEqual(env['decision']['reason'], 'standing', env['decision'])
        self.assertEqual(CALLS, [])
        reply = str(raw.get('reply') or '')
        self.assertIn('Taking your 4 questions in order.', reply)
        self.assertIn('of the 10 million US consumers we measure', reply)
        self.assertIn('Men aged 18 to 34 are 1,', reply)
        self.assertIn('never a household guess', reply)
        self.assertIn('phones, computers and connected TVs', reply)
        self.assertIsNone(BANNED_RX.search(reply), reply)
        self.assertEqual(raw.get('followups') or [], [])

    def test_a_count_question_is_a_read_never_a_build(self):
        for t in (COUNT_18_44, PILOT_YT):
            env, st, raw = _ask(t)
            self.assertEqual(env['decision']['surface'], 'analyze', (t, env['decision']))
            self.assertEqual([c[0] for c in CALLS], ['analyze'], t)
            reply = str(raw.get('reply') or '')
            self.assertTrue(reply.startswith('Included with your plan.') or reply.startswith('On it'), raw)
            self.assertEqual(raw.get('followups') or [], [])
            self.assertFalse(raw.get('panel_offer'))
            self.assertNotIn('$', reply)

    def test_the_sign_up_count_never_goes_to_the_wizard(self):
        self.assertTrue(pma.is_signup_count_ask(SIGNUP_COUNT))
        env, st, raw = _ask(SIGNUP_COUNT, ctx=CTX)
        self.assertNotEqual(env['decision']['surface'], 'interpret', env['decision'])
        self.assertNotIn('interpret', [c[0] for c in CALLS])

    def test_two_figures_that_must_agree_are_a_reconciliation(self):
        self.assertTrue(RL.wants_reconcile(RECONCILE))
        env, st, raw = _ask(RECONCILE, history=HISTORY_TWO_READS)
        self.assertEqual(env['decision']['reason'], 'reconcile', env['decision'])
        reply = str(raw.get('reply') or '')
        self.assertNotIn('typed by the model', reply)
        self.assertIsNone(re.search(r"\bmodel\b", reply, re.I), reply)
        self.assertIn('4,287,316', reply)

    def test_number_provenance_never_says_typed_by_the_model(self):
        out = NP.answer('where does the 4,287,316 come from?', HISTORY_TWO_READS)
        self.assertIsNotNone(out)
        self.assertNotIn('typed by the model', out['reply'])
        self.assertIsNone(re.search(r"\bmodel\b", out['reply'], re.I), out['reply'])
        self.assertIn('4,287,316', out['reply'])

    def test_new_versus_returning_per_quarter_is_a_read_not_a_size(self):
        self.assertFalse(SZ.wants_sizing(NEW_VS_RETURNING))
        env, st, raw = _ask(NEW_VS_RETURNING)
        self.assertNotIn(env['decision']['reason'], ('sizing', 'sizing_quote', 'sizing_fallback'), env['decision'])
        self.assertEqual(env['decision']['surface'], 'analyze')

    def test_the_unprojected_lane_still_answers_one_figure(self):
        env, st, raw = _ask(UNPROJECTED_ONE)
        self.assertEqual(env['decision']['reason'], 'unprojected')
        self.assertIn('187,795 of the 10 million US consumers we measure', raw['reply'])

    def test_a_status_question_is_not_an_order(self):
        self.assertFalse(O.is_order(STATUS))
        d = U.decide(STATUS, has_ctx=False)
        self.assertNotEqual(d['reason'], 'build_order')


class TheSideLanesStepAsideForAnOrder(unittest.TestCase):
    def test_wants(self):
        self.assertFalse(UNP.wants_unprojected(ORDER))
        self.assertFalse(UNP.wants_unprojected(ORDER_SEVEN))
        self.assertFalse(RL.wants_reconcile(ORDER_SEVEN))
        self.assertFalse(RL.wants_reconcile(ORDER))
        self.assertEqual(NP.detect('run a profile on Taylor Swift and tell me where the 6,195,357 comes from'), '')
        self.assertFalse(SZ.wants_sizing('run a profile on Fallout Season 2 viewers and tell me how many people are in it'))
        self.assertIsNone(DC.answer('can you build me a Nike profile and tell me if your data can see purchases'))
        from prometheus import us_scope as US
        self.assertFalse(US.wants_us_scope(ORDER_SEVEN))
        self.assertTrue(IG.is_build_order(ORDER))
        # the single-figure, no-order asks keep their lanes
        self.assertTrue(UNP.wants_unprojected(UNPROJECTED_ONE))
        self.assertTrue(RL.wants_reconcile(RECONCILE))

    def test_the_no_figure_reply_never_tells_the_reader_to_repeat_herself(self):
        out = UNP.answer('unprojected audience for Fallout Season 2', [], None)
        if out is not None:
            self.assertNotIn("say 'run a profile", out['reply'])
            self.assertNotIn('run a profile on', out['reply'])
            self.assertTrue(out['reply'].count('?') <= 1)


class IncludedWithYourPlan(unittest.TestCase):
    def test_her_seat_is_covered(self):
        self.assertTrue(PR.included_with_plan(dict(GRACE), 'graceyin', None))
        self.assertTrue(O.build_line('X', 'Nov 1 2025 to Oct 31 2026').startswith(PR.INCLUDED_LINE))
        self.assertEqual(O.INCLUDED_LINE, PR.INCLUDED_LINE)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


class Wiring(unittest.TestCase):
    def test_service_gates_and_build_flow(self):
        svc = _read('prometheus', 'service.py')
        self.assertGreaterEqual(svc.count('not _is_order'), 10)
        for lane in ('# lane: reconcile', '# lane: us_scope', '# lane: number_provenance', '# lane: unprojected',
                     '# lane: standing', '# lane: multi_ask', '# lane: sizing_quote', '# lane: sizing_fallback',
                     '# lane: definitions', '# lane: capability'):
            i = svc.index(lane)
            self.assertIn('not _is_order', svc[i:i + 2500], lane)
        self.assertIn('methodology_answers', svc)
        chat = _read('prometheus', 'legacy', 'chat.py')
        self.assertIn('def _pm_approve_core(user, body):', chat)
        self.assertIn('def _pm_auto_start_order(', chat)
        self.assertIn("_oc, _order_notes = _orders.split(text)", chat)
        self.assertIn("spec_draft['deliverable_notes'] = str(_order_notes)[:600]", chat)
        self.assertIn("spec['deliverable_notes'] = _scrub(_dn, field='deliverable_notes'", chat)
        html = _read('templates', 'index.html')
        self.assertIn('if (data.auto_started && data.run_id) {', html)
        und = _read('prometheus', 'understand.py')
        self.assertIn("is_profile_order(t)", und)
        for path in ('prometheus/orders.py', 'prometheus/methodology_answers.py'):
            src = _read(*path.split('/'))
            self.assertNotIn('\u2014', src, path)


if __name__ == '__main__':
    unittest.main(verbosity=1)
