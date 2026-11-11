#!/usr/bin/env python3
"""The add-as order shapes and their referents (2026-11-10, the Sony
Television seat).

"Can you look into My Life With the Walter Boys viewers? I want to add
them as a profile" (sonytv, 19:28 UTC) ran a read instead of the build:
the second sentence is an order the gate did not read, and "them" is the
audience the first sentence named. This suite holds:

  * every add-as phrasing is an order for the right product: add them /
    this / X as a profile, make a profile of / for, save this / them as
    a profile, turn this into a profile, I want a profile of / on, can I
    get a profile of, profile this audience, create a twin of, add it to
    Digital Journey IQ;
  * a referent in the subject slot is read (orders.referent) and
    resolves the way the thread's pronouns resolve: the subject named
    earlier in the same message, then the newest landed read in the
    thread (with the consumer noun, platform and window the reader's own
    words carried), then the audience on screen (order_referent);
  * the ask service hands the build flow the resolved order (the
    interpret core receives "Please queue one Profile IQ: <subject> ...")
    and the unprojected / sizing / wayfinding lanes never take it;
  * the questions, corrections and pushbacks that look alike stay
    non-orders (Cara Dorfman's "can I get the hours by month for 2025",
    "did you add this as a digital journey iq profile?").

Hermetic: a stub host carrying the Sony seat (unlimited), stub cores that
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
from prometheus import order_referent as R  # noqa: E402
from prometheus import understand as U  # noqa: E402
from prometheus import rate_card as RC  # noqa: E402

SONY = {'username': 'sonytv', 'email': 'isa_foundations@spe.sony.com', 'company': 'Sony Television Group', 'role': 'user',
        'credits': -1, 'unlimited': True, 'paying_customer': True, 'has_chatbot_profile_iq_access': True}

SONY_SENTENCE = "Can you look into My Life With the Walter Boys viewers? I want to add them as a profile"

WALTER_READ = (
    "My Life with the Walter Boys viewers project to 13,742,586 people in the US, with 5,318,473 of them Avid at four or more "
    "episode plays.\n\nProjected US audience: 13,742,586, Avid tier viewers: 5,318,473, Season 3 viewers to date: 8,146,329.\n\n"
    "Age band mix, Dec 7 2023 to Nov 5 2026 (share of the 13,742,586 projected US viewers)\n- 25-34: 26.8% share. 3,683,013 "
    "viewers, the single largest band\n\nHow these are counted:\n- Window: Dec 7 2023 to Nov 5 2026.\n- Base: Walter boys viewers, "
    "measured across 10M US consumers and projected to the US.")
# the Sony thread as the widget sent it (turns 0 to 2 of sonytv/3df066a21c)
SONY_THREAD = [
    {'role': 'user', 'text': SONY_SENTENCE, 'ts': '2026-11-10T19:28:48.489Z'},
    {'role': 'agent', 'text': ('Included with your plan. On it: reading My Life With the Walter Boys viewers now. This one takes a '
                               'real look at the data, so give me a moment - the read will land right here when it is ready.'),
     'ts': '2026-11-10T19:29:03.013Z'},
    {'role': 'agent', 'text': WALTER_READ, 'ts': '2026-11-10T19:30:37.000Z',
     'meta': {'kind': 'read', 'read_job_id': '823ed776639d', 'subject': 'My Life with the Walter Boys',
              'landed_at': '2026-11-10T19:30:37.935Z',
              'figures': [{'value': 13742586, 'kind': 'count', 'label': 'typed by the model', 'source': 'model_typed'}]}},
]
HULU_THREAD = [
    {'role': 'user', 'text': 'who watched Gilmore Girls on Hulu, Nov 1 2025 to Oct 31 2026?'},
    {'role': 'agent', 'text': 'On it: reading Gilmore Girls now. This one takes a real look at the data, so give me a moment - '
                              'the read will land right here when it is ready.'},
    {'role': 'agent', 'text': 'On Gilmore Girls - viewers:\n\nGilmore Girls viewers on Hulu project to 9,412,331 people in the US, '
                              'with 2,104,587 of them Avid at four or more episode plays.\n\nHow these are counted:\n- Window: Nov 1 '
                              '2025 to Oct 31 2026.',
     'meta': {'kind': 'read', 'read_job_id': 'gg1', 'subject': 'Gilmore Girls', 'landed_at': '2026-11-10T18:00:00Z'}},
]

PHRASINGS = {
    'add them as a profile': ('profile', 'them'),
    'add this as a profile': ('profile', 'this'),
    'add My Life with the Walter Boys viewers as a profile': ('profile', ''),
    'I want to add them as a profile': ('profile', 'them'),
    'make a profile of the Walter Boys viewers': ('profile', ''),
    'make a profile for this audience': ('profile', 'this audience'),
    'save this as a profile': ('profile', 'this'),
    'save them as a profile': ('profile', 'them'),
    'turn this into a profile': ('profile', 'this'),
    'I want a profile of the Season 3 watchers': ('profile', ''),
    'I want a profile on these viewers': ('profile', 'these viewers'),
    'can I get a profile of Gilmore Girls viewers on Hulu': ('profile', ''),
    'can I get a profile of them': ('profile', 'them'),
    'profile this audience': ('profile', 'this audience'),
    'profile these viewers please': ('profile', 'these viewers'),
    'create a twin of the Walter Boys viewers': ('twin', ''),
    'create a twin of them': ('twin', 'them'),
    'add it to Digital Journey IQ': ('journey', 'it'),
    'Add My Life with the Walter Boys to Digital Journey IQ': ('journey', ''),
    'put this in Profile IQ': ('profile', 'this'),
    'add them to the dashboard': ('profile', 'them'),
}
NOT_ORDERS = (
    'did you add this as a digital journey iq profile?',
    'can I get the hours by month for 2025',
    'can I get usage by month for 2025 on Netflix?',
    'can I get the overlap of Netflix and Hulu viewers for 2025',
    'what does a profile cost',
    "don't build a new profile",
    'do we have a profile for Ms. Rachel?',
    'can you build profiles?',
    'why do you keep building a profile',
    'I already pulled a profile on them',
    'add the brand to the profile',
    'add Nike to the list',
    'add a cut for Gen Z to the profile',
    'where is the digital journey in the dashboard?',
    'is the profile done',
    'but i want it in the dashboard where it says "select a journey"',
)

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
              load_users=lambda: {'users': {'sonytv': dict(SONY)}},
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
        body['_client_surface_sent'] = surface
    env, st = service.ask(dict(SONY), body)
    return env, st, env.get('raw') or {}


class TheGateReadsTheAddAsShapes(unittest.TestCase):
    def test_each_phrasing_is_an_order_for_its_product(self):
        for text, (fam, ref) in PHRASINGS.items():
            self.assertTrue(O.is_order(text), text)
            self.assertEqual(O.product_of(text), fam, text)
            self.assertEqual(O.referent(text), ref, text)
        self.assertTrue(O.is_profile_order('add them as a profile'))
        self.assertFalse(O.is_profile_order('add it to Digital Journey IQ'))

    def test_the_sony_sentence(self):
        self.assertTrue(O.is_order(SONY_SENTENCE))
        self.assertTrue(O.is_profile_order(SONY_SENTENCE))
        self.assertEqual(O.referent(SONY_SENTENCE), 'them')
        d = U.decide(SONY_SENTENCE, has_ctx=False)
        self.assertEqual((d['surface'], d['reason']), ('interpret', 'build_order'), d)

    def test_the_lookalikes_stay_questions(self):
        for t in NOT_ORDERS:
            self.assertFalse(O.is_order(t), t)
            self.assertEqual(O.referent(t), '', t)

    def test_a_named_subject_has_no_referent(self):
        self.assertEqual(O.referent('run it on Mountain Dew'), '')
        self.assertEqual(O.referent('run a profile on Fallout Season 2 viewers on Prime Video'), '')
        self.assertEqual(O.referent('ok run it'), 'it')


class TheReferentResolves(unittest.TestCase):
    def test_the_same_message_names_them(self):
        r = R.resolve(SONY_SENTENCE, history=[])
        self.assertIsNotNone(r)
        self.assertEqual(r['subject'], 'My Life With the Walter Boys viewers')
        self.assertEqual(r['source'], 'message')
        self.assertEqual(r['referent'], 'them')
        self.assertEqual(r['platform'], '')    # the reader named no platform; nothing is invented
        self.assertEqual(r['window'], '')      # and no window: the standing default applies
        self.assertEqual(R.rewrite(SONY_SENTENCE, history=[]),
                         'Please queue one Profile IQ: My Life With the Walter Boys viewers')

    def test_the_thread_s_last_read_names_them(self):
        for t in ('add them as a profile', 'I want to add them as a profile', 'save this as a profile', 'turn this into a profile',
                  'profile this audience', 'I want a profile of these viewers', 'can I get a profile of them',
                  'make a profile for this audience'):
            r = R.resolve(t, history=SONY_THREAD)
            self.assertIsNotNone(r, t)
            self.assertEqual(r['subject'], 'My Life with the Walter Boys viewers', t)
            self.assertEqual(r['source'], 'thread', t)
            self.assertEqual(R.rewrite(t, history=SONY_THREAD), 'Please queue one Profile IQ: My Life with the Walter Boys viewers', t)

    def test_the_platform_and_window_the_reader_said_ride_along(self):
        r = R.resolve('add them as a profile', history=HULU_THREAD)
        self.assertEqual((r['subject'], r['platform'], r['window']), ('Gilmore Girls viewers', 'Hulu', 'Nov 1 2025 to Oct 31 2026'))
        self.assertEqual(R.rewrite('add them as a profile', history=HULU_THREAD),
                         'Please queue one Profile IQ: Gilmore Girls viewers on Hulu, Nov 1 2025 to Oct 31 2026')
        self.assertEqual(R.rewrite('create a twin of them', history=HULU_THREAD),
                         'Create a Digital Twin of Gilmore Girls viewers on Hulu, Nov 1 2025 to Oct 31 2026')
        self.assertEqual(R.rewrite('add it to Digital Journey IQ', history=HULU_THREAD),
                         'Build a Digital Journey for Gilmore Girls viewers on Hulu, Nov 1 2025 to Oct 31 2026')
        # the order's own words win over the thread's
        self.assertEqual(R.rewrite('add them as a profile for 2024', history=HULU_THREAD),
                         'Please queue one Profile IQ: Gilmore Girls viewers on Hulu, Jan 1 2024 to Dec 31 2024')

    def test_the_deliverable_notes_ride_the_rewrite(self):
        out = R.rewrite('profile this audience. Include reach and the age split. Deliver as a CSV.', history=HULU_THREAD)
        self.assertTrue(out.startswith('Please queue one Profile IQ: Gilmore Girls viewers on Hulu, Nov 1 2025 to Oct 31 2026'), out)
        self.assertIn('Include reach and the age split', out)
        self.assertIn('Deliver as a CSV', out)
        self.assertTrue(O.is_order(out))

    def test_the_screen_is_the_last_resort(self):
        ctx = {'primary': {'name': 'NCIS New York Viewers', 's3_key': 'NCIS_New_York_Viewers_10_20_2026_00_29.csv'}}
        r = R.resolve('add them as a profile', history=[], ctx=ctx)
        self.assertEqual((r['subject'], r['source']), ('NCIS New York Viewers', 'screen'))
        self.assertIsNone(R.resolve('add them as a profile', history=[]))
        self.assertIsNone(R.rewrite('add them as a profile', history=[]))

    def test_a_named_order_is_left_alone(self):
        self.assertIsNone(R.rewrite('add Nike runners as a profile', history=HULU_THREAD))
        self.assertIsNone(R.rewrite('run a profile on Fallout Season 2 viewers on Prime Video', history=HULU_THREAD))
        self.assertIsNone(R.rewrite('can I get the hours by month for 2025', history=HULU_THREAD))

    def test_platform_and_noun_readers(self):
        self.assertEqual(R.platform_in('Gilmore Girls Viewers ON HULU'), 'Hulu')
        self.assertEqual(R.platform_in('US people who watched Fallout Season 2 on Prime Video'), 'Prime Video')
        self.assertEqual(R.platform_in('My Life with the Walter Boys'), '')
        self.assertEqual(R.consumer_noun('who watched Gilmore Girls on Hulu?'), 'viewers')
        self.assertEqual(R.consumer_noun('The Daily listeners'), 'listeners')
        self.assertEqual(R.consumer_noun('My Life with the Walter Boys'), '')


class TheServiceHandsTheBuildFlowTheResolvedOrder(unittest.TestCase):
    def setUp(self):
        _bind_host()

    def test_the_sony_sentence_reaches_the_interpret_core_resolved(self):
        env, st, raw = _ask(SONY_SENTENCE, history=[])
        self.assertEqual(st, 200)
        self.assertEqual(env.get('surface'), 'interpret', env.get('decision'))
        kinds = [c[0] for c in CALLS]
        self.assertIn('interpret', kinds, (kinds, raw))
        text_seen = [c[1] for c in CALLS if c[0] == 'interpret'][0]
        self.assertEqual(text_seen, 'Please queue one Profile IQ: My Life With the Walter Boys viewers')
        body_seen = [c[2] for c in CALLS if c[0] == 'interpret'][0]
        self.assertTrue(body_seen.get('_pm_order'))
        self.assertTrue(body_seen.get('_pm_order_referent'))
        self.assertEqual(body_seen.get('_pm_order_text'), text_seen)
        self.assertNotIn('analyze', kinds)

    def test_a_pronoun_order_after_the_read_resolves_from_the_thread(self):
        for t in ('I want to add them as a profile', 'save this as a profile', 'profile this audience'):
            env, st, raw = _ask(t, history=SONY_THREAD)
            self.assertEqual(st, 200, t)
            self.assertEqual(env.get('surface'), 'interpret', (t, env.get('decision'), raw))
            seen = [c for c in CALLS if c[0] == 'interpret']
            self.assertTrue(seen, (t, CALLS, raw))
            self.assertEqual(seen[0][1], 'Please queue one Profile IQ: My Life with the Walter Boys viewers', t)

    def test_the_thread_s_platform_and_window_ride_into_the_build(self):
        env, st, raw = _ask('add them as a profile', history=HULU_THREAD)
        self.assertEqual(env.get('surface'), 'interpret', (env.get('decision'), raw))
        seen = [c for c in CALLS if c[0] == 'interpret']
        self.assertEqual(seen[0][1], 'Please queue one Profile IQ: Gilmore Girls viewers on Hulu, Nov 1 2025 to Oct 31 2026')

    def test_add_it_to_digital_journey_iq_is_the_journey_build(self):
        env, st, raw = _ask('add it to Digital Journey IQ', history=SONY_THREAD)
        self.assertEqual(st, 200)
        # the journey order carries the resolved subject; the wayfinding chip's own words
        texts = [c[1] for c in CALLS] + [str(raw.get('rewrite_text') or ''), str(raw.get('reply') or '')]
        self.assertTrue(any('Build a Digital Journey for My Life with the Walter Boys' in t for t in texts), (CALLS, raw))

    def test_an_unresolved_referent_still_goes_to_the_build_flow(self):
        env, st, raw = _ask('add them as a profile', history=[])
        self.assertEqual(st, 200)
        self.assertEqual(env.get('surface'), 'interpret', (env.get('decision'), raw))
        seen = [c for c in CALLS if c[0] == 'interpret']
        self.assertTrue(seen, (CALLS, raw))
        self.assertEqual(seen[0][1], 'add them as a profile')      # the words as typed; the flow's own clarifier asks

    def test_cara_s_metric_ask_stays_a_read(self):
        env, st, raw = _ask('can I get the hours by month for 2025', history=SONY_THREAD, ctx={'primary': {'name': 'My Life with the Walter Boys Viewers'}})
        self.assertEqual(st, 200)
        self.assertNotEqual(env.get('surface'), 'interpret', (env.get('decision'), raw))
        self.assertNotIn('interpret', [c[0] for c in CALLS])


if __name__ == '__main__':
    unittest.main(verbosity=1)
