#!/usr/bin/env python3
"""Three routing fixtures from 2026-11-20, fixed at the cause.

1. A subject the question names outranks the profile open on screen
   (Emma Crouchen, VICE: "I want to know if VICE Sports audience has
   interest in beauty and grooming products?" with Pinterest open read
   the Pinterest file, because the file carried a BEAUTY row and the
   page's "row named" signal let it stand). The Understanding carries
   the named subject (the binder reads "X audience" from the words, the
   Reader's kinds count, 'audience' included); the page never stands
   against it in the read core, the I6 check, the pin, or the memory
   ladder (the thread's own subject scopes what cross-session memory
   may offer). Flip ``named_subject``, switch PM_U_OFF_NAMED_SUBJECT=1.
2. A product or a tab is never a subject (Samantha Kolton: "Do you mean
   for 🤝 Brand Partnership IQ, or Anne Hathaway x Shiseido?", and the
   chip ran a read on the view). dashboard_map.is_product_label is the
   one rule: memory never offers or stores one, the thread never pins
   one, the binder never binds one, a bound subject that is one is
   dropped at the door. And a figure the thread itself delivered names
   the thread's valuation: "$102,145,967.00 total brand value" pasted
   back is answered as that valuation's figure, never a which-subject
   question (valuation_referent, kind 'figure').
3. A question about Prometheus or Crosswalk itself is product knowledge
   (Liz: "give me a 3-5 word description for CW prometheus versus AI"
   drew "Which subject should this be on?"). product_facts gains the
   positioning family with Jenna's own answer; the Understanding reads
   it as capability with product evidence and the lane is keyed on it.

Hermetic: no model, no S3, no mail, no app boot. The door runs through
service.ask on the stub host (scripts/pm_corpus/replay_corpus.py); the
read core runs through the legacy chat module bound to the same stub,
with a one-file library and a tiny profile in the fake store.
"""
from __future__ import annotations

import io
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ['PM_MAIL_OFF'] = '1'
os.environ.setdefault('CORPUS_CATALOG', '0')

from pm_corpus import replay_corpus as RC  # noqa: E402
RC.install_isolation_light()
from prometheus import understanding as U  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402
from prometheus import page_default as PD  # noqa: E402
from prometheus import referents as RF  # noqa: E402
from prometheus import thread_subject as TS  # noqa: E402
from prometheus import dashboard_map as DM  # noqa: E402
from prometheus import valuation_referent as VR  # noqa: E402
from prometheus import product_facts as PF  # noqa: E402
import prometheus_memory as PMM  # noqa: E402

PAGE = 'Example Network'
PAGE_KEY = 'Example_Network_06_14_2026_01_37.csv'
AUDIENCE_Q = ('I want to know if Example Sports audience has interest in beauty and grooming products? '
              'I need stats for a sales proposal for Northwind Grooming')
AUDIENCE_READING = {'intent': 'analyze', 'confidence': 0.76, 'flags': [],
                    'subjects': [{'as_written': 'Example Sports audience', 'catalog': '', 'kind': 'audience'},
                                 {'as_written': 'Northwind Grooming', 'catalog': '', 'kind': 'brand'}]}
PAGE_CTX = {'primary': {'name': PAGE, 's3_key': PAGE_KEY}, 'cuts': []}
TINY_PROFILE = (
    "Column,Value,Brand Penetration (Row),Category Share,Original Raw Numbers,US Gen Pop Projection\n"
    "BRAND INPUT,EXAMPLE NETWORK,100.0000%,100.0000%,1217543,40166793\n"
    "SAMPLE SIZE,SAMPLE SIZE (2025-01-01 TO 2025-12-31),100.0000%,100.0000%,1217543,40166793\n"
    "BRAND CATEGORY,SOCIAL MEDIA,100.0000%,100.0000%,1217543,40166793\n"
    "INTEREST,BEAUTY,73.9404%,12.1100%,900257,29700013\n"
    "MOST PURCHASED CATEGORIES,BEAUTY,73.9363%,9.8800%,900207,29698363\n"
    "GENDER,Female,72.6917%,72.6917%,885052,29198711\n"
    "GENDER,Male,27.3083%,27.3083%,332491,10968082\n"
)

TALENT, BRAND = 'Example Talent', 'Northwind Beauty'
PAIR = f'{TALENT} x {BRAND}'
VALUATION_THREAD = [
    {'role': 'user', 'text': 'Value a brand partnership'},
    {'role': 'agent', 'text': 'Who is the property or talent?'},
    {'role': 'user', 'text': TALENT},
    {'role': 'agent', 'text': 'What campaign should I value?'},
    {'role': 'user', 'text': BRAND},
    {'role': 'agent', 'text': 'What are the campaign dates?'},
    {'role': 'user', 'text': '9/1/26-9/8/26'},
    {'role': 'agent', 'text': f'{TALENT}, {BRAND}: I read the campaign as September 1 to September 8, 2026. The standard window is the campaign '
                              'dates plus 30 days after it ends. Attribution window: September 1 to October 8, 2026.'},
    {'role': 'user', 'text': 'Accept'},
    {'role': 'agent', 'text': f"Here's what I'll run:\n{TALENT}\n{BRAND}\nCampaign dates: September 1 to September 8, 2026\nAttribution window: "
                              "September 1 to October 8, 2026\nControl group: yes\nI'll find the assets\nIncluded with your plan."},
    {'role': 'user', 'text': 'Accept'},
    {'role': 'agent', 'text': "It's running. Keep talking here as usual. I'll email you when it is done."},
    {'role': 'agent', 'text': f'Your valuation is ready: {PAIR}, total brand value $1,234,567.89. It is in the Brand Partnership tab.',
     'meta': {'bpiq_job_id': 'job-1', 'kind': 'bpiq', 'subject': PAIR, 's3_key': 'bpiq/example.json'}},
    {'role': 'user', 'text': 'why does this show a lower valuation than when i ran it for sep 1 to sep 8 2026?'},
    {'role': 'agent', 'text': 'Which figure do you mean? Pick one and I will define it and show how it is counted.',
     'meta': {'options': ['Define "partnership"', 'Define "window"']}},
]
FIGURE = '$1,234,567.89\ntotal brand value'
PRODUCT_CHIP = '🤝 Brand Partnership IQ'

LIZ_1 = 'give me a 3-5 word description for CW prometheus versus AI'
LIZ_2 = 'this is a Definition or Marketing headline on Prometheus... how would you define yourself.'


def _clear_flags():
    for k in list(os.environ):
        if k.startswith('PM_U_OFF_') or k == 'PM_UNDERSTANDING':
            os.environ.pop(k, None)


def _norm(s):
    if not s:
        return ''
    s = str(s).lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class NamedSubjectOutranksThePage(unittest.TestCase):
    """1. The subject the words name is the read's subject, never the open page."""

    def setUp(self):
        _clear_flags()

    def test_the_binder_reads_a_named_audience_from_the_words(self):
        s = SR.bind(AUDIENCE_Q, [], PAGE)
        self.assertTrue(s and s.named and s.source == 'words')
        self.assertEqual(s.kind, 'audience')
        self.assertEqual(s.display, 'Example Sports audience')
        s2 = SR.bind(AUDIENCE_Q, [], PAGE, AUDIENCE_READING)
        self.assertEqual(s2.display, 'Example Sports audience')

    def test_the_understanding_carries_the_named_subject(self):
        u = U.features(AUDIENCE_Q, PAGE_CTX, [], {})
        self.assertEqual([(s.as_written, s.kind) for s in u.subjects], [('Example Sports audience', 'audience')])
        self.assertIn('named:audience', u.evidence)
        self.assertEqual(U.named_subject(u, AUDIENCE_Q, PAGE), 'Example Sports audience')
        u2 = U.absorb_reading(U.features(AUDIENCE_Q, PAGE_CTX, [], {}), AUDIENCE_READING)
        self.assertEqual(U.named_subject(u2, AUDIENCE_Q, PAGE), 'Example Sports audience')

    def test_the_page_named_as_the_audience_is_the_page(self):
        q = f'how big is the {PAGE} audience'
        u = U.features(q, PAGE_CTX, [], {})
        self.assertEqual(U.named_subject(u, q, PAGE), '')
        for q in ('what do the men in this audience buy', 'Is the audience older than the US average?'):
            u = U.features(q, PAGE_CTX, [], {})
            self.assertEqual(U.named_subject(u, q, PAGE), '', q)

    def test_i6_counts_a_named_audience_and_the_understanding(self):
        self.assertTrue(SR.names_other_subject(AUDIENCE_Q, PAGE_CTX))
        self.assertFalse(SR.names_other_subject(f'how big is the {PAGE} audience', PAGE_CTX))
        self.assertFalse(SR.names_other_subject('what do they buy', PAGE_CTX))

    def test_the_page_never_stands_against_the_named_subject(self):
        stub = RC._Stub()
        stub.bind()
        from flask import g
        u = U.features(AUDIENCE_Q, PAGE_CTX, [], {})
        with stub.app.test_request_context('/api/prometheus/v1/ask', method='POST'):
            g._pm_understanding = u.to_dict()
            stands, why = PD.page_base_stands(AUDIENCE_Q, [], PAGE, PAGE_KEY, '', normalize=_norm, stop_tokens=frozenset(),
                                              brand_named=lambda t, k: 'BEAUTY', ask_user='')
            self.assertFalse(stands)
            self.assertIn('Example Sports audience', why)
            # the page still stands for a question whose words point at the screen, row named or not
            stands2, why2 = PD.page_base_stands('what do the people in this profile buy', [], PAGE, PAGE_KEY, '', normalize=_norm,
                                                stop_tokens=frozenset(), brand_named=lambda t, k: 'BEAUTY', ask_user='')
            self.assertTrue(stands2, why2)
            # "these people" with no thread is nobody in particular: the page never stands on it (Jenna 2026-11-20)
            stands2b, _ = PD.page_base_stands('what do these people buy', [], PAGE, PAGE_KEY, '', normalize=_norm, stop_tokens=frozenset(),
                                              brand_named=lambda t, k: 'BEAUTY', ask_user='')
            self.assertFalse(stands2b)
            # both switches restore the old reading: the row-named signal lets the page stand again
            os.environ['PM_U_OFF_NAMED_SUBJECT'] = '1'
            os.environ['PM_SCREEN_SCOPE'] = '0'
            try:
                stands3, why3 = PD.page_base_stands(AUDIENCE_Q, [], PAGE, PAGE_KEY, '', normalize=_norm, stop_tokens=frozenset(),
                                                    brand_named=lambda t, k: 'BEAUTY', ask_user='')
                self.assertTrue(stands3, why3)
            finally:
                os.environ.pop('PM_U_OFF_NAMED_SUBJECT', None)
                os.environ.pop('PM_SCREEN_SCOPE', None)

    def test_the_pin_yields_to_words_the_reader_read(self):
        stub = RC._Stub()
        stub.bind()
        from flask import g
        with stub.app.test_request_context('/api/prometheus/v1/ask', method='POST'):
            g._pm_thread_pin = {'subject': PAGE, 'source': 'offer', 'dropped': []}
            g._pm_reading = AUDIENCE_READING
            self.assertEqual(SR.pinned_bind_for(AUDIENCE_Q, [], PAGE), '')
            g._pm_reading = {'intent': 'analyze', 'confidence': 0.7, 'subjects': [], 'flags': ['refers_to_page']}
            self.assertEqual(SR.pinned_bind_for('what do they buy', [], PAGE), PAGE)

    def test_the_read_core_binds_the_named_subject_on_the_page_path(self):
        # the production path: the open-screen confirm skipped (a continuation on the request), the read core
        # resolves the base itself with the page open and a row of the page named in the words
        stub = RC._Stub()
        stub.bind()
        import prometheus.legacy as _legacy
        old = object.__getattribute__(_legacy.H, '_mod')
        names = dict(object.__getattribute__(old, '_names'))
        names['_normalize_for_match'] = _norm
        names['_profile_catalog_for_chat'] = lambda: [{'display_name': PAGE, 'subject': PAGE, 's3_key': PAGE_KEY}]
        names['_require_profile_run_access'] = lambda k: (True, None)
        names['_pm_claude_json'] = lambda *a, **k: {'success': False, 'error': 'no model'}
        names['_pm_claude_data'] = lambda *a, **k: None
        _legacy.bind(RC._LegacyNS(names, object.__getattribute__(old, '_log')))
        from prometheus.legacy import chat as C
        RC._FAKE['s3'].objects[PAGE_KEY] = TINY_PROFILE.encode('utf-8')
        hints = []
        orig = C._pm_ask_hint
        C._pm_ask_hint = lambda **k: (hints.append(k), orig(**k))[1]
        try:
            from flask import g
            u = U.absorb_reading(U.features(AUDIENCE_Q, PAGE_CTX, [], {}), AUDIENCE_READING)
            with stub.app.test_request_context('/api/prometheus/v1/ask', method='POST', json={'page_context': PAGE_CTX}):
                g._pm_understanding = u.to_dict()
                g._pm_reading = AUDIENCE_READING
                g._pm_continuation = {'kind': 'reply'}
                resp = C._pm_generate_metrics_response(dict(RC.REPLAY_USER), AUDIENCE_Q, [], ctx=PAGE_CTX, prefer_catalog=True)
                payload = resp.get_json() if hasattr(resp, 'get_json') else resp
        finally:
            C._pm_ask_hint = orig
        subjects = [h.get('subject') for h in hints if h.get('subject')]
        self.assertTrue(subjects, hints)
        self.assertNotIn(PAGE, subjects)
        self.assertTrue(any('Example Sports' in str(s) for s in subjects), subjects)
        self.assertNotIn(f'reading {PAGE}', str(payload.get('reply') or ''))

    def test_memory_offers_nothing_outside_the_threads_own_subject(self):
        hist = [{'role': 'user', 'text': AUDIENCE_Q},
                {'role': 'agent', 'text': 'On Example Sports: Beauty is a confirmed purchase for this audience.', 'meta': {'subject': 'Example Sports'}}]
        opts = [{'label': 'Example Sports', 'subject': 'Example Sports', 'cohort': None}, {'label': PAGE, 'subject': PAGE, 'cohort': None}]
        kept, bind = RF.scope_memory_options(opts, hist, '')
        self.assertEqual([o['subject'] for o in kept], ['Example Sports'])
        self.assertEqual(bind, 'Example Sports')
        kept2, bind2 = RF.scope_memory_options(opts, [], '')
        self.assertEqual(len(kept2), 2)
        self.assertEqual(bind2, '')


class AProductIsNeverASubject(unittest.TestCase):
    """2. A product or a tab is a place on the dashboard; a figure the thread delivered names its valuation."""

    def setUp(self):
        _clear_flags()

    def test_the_one_rule(self):
        for s in (PRODUCT_CHIP, 'Brand Partnership IQ', 'Partnership IQ', 'journeyIQ', '🗺️ Digital Journey IQ', 'Profile IQ', 'Focus Group IQ',
                  'Prometheus', 'CW Prometheus', 'Twins', 'Insights'):
            self.assertTrue(DM.is_product_label(s), s)
        for s in ('Anne Hathaway x Shiseido', 'Pinterest', 'VICE Sports', 'Nike', 'The Office', 'Just for Men', 'Example Talent'):
            self.assertFalse(DM.is_product_label(s), s)

    def test_memory_never_offers_or_keeps_one(self):
        asks = [{'subject': PRODUCT_CHIP, 'cohort': None, 'question': 'x', 'ts': '2026-11-20T19:00:00Z'},
                {'subject': PAIR, 'cohort': None, 'question': 'y', 'ts': '2026-11-20T18:00:00Z'}]
        old_recall, old_mine = PMM.recall, PMM._this_thread
        PMM.recall = lambda user, k=0: list(asks)
        PMM._this_thread = lambda a: list(a)
        try:
            refs = PMM.recent_referents('seat', k=2)
        finally:
            PMM.recall, PMM._this_thread = old_recall, old_mine
        self.assertEqual([r['subject'] for r in refs], [PAIR])
        opts = [{'label': PRODUCT_CHIP, 'subject': PRODUCT_CHIP, 'cohort': None}, {'label': PAIR, 'subject': PAIR, 'cohort': None}]
        pay = RF.memory_confirm_payload('x', opts, [])
        self.assertNotIn('Brand Partnership IQ', pay['reply'])
        self.assertEqual(pay['followups'], [PAIR, 'Something else'])
        self.assertFalse(RF.plausible_subject(PRODUCT_CHIP))

    def test_the_thread_never_pins_a_product_or_a_question(self):
        self.assertFalse(TS.pinnable(PRODUCT_CHIP))
        self.assertFalse(TS.pinnable(LIZ_2))
        self.assertFalse(TS.pinnable('what audience are you pulling this from'))
        self.assertTrue(TS.pinnable(PAIR))
        self.assertTrue(TS.pinnable('University of Virginia'))
        calls = []

        class _Host:
            def load_threads_index(self, uname):
                return {'threads': [{'id': 't1'}]}

            def save_threads_index(self, uname, idx):
                calls.append(idx)
        self.assertIsNone(TS.pin(_Host(), 'seat', 't1', PRODUCT_CHIP, source='offer'))
        self.assertIsNone(TS.pin(_Host(), 'seat', 't1', LIZ_2, source='offer'))
        self.assertEqual(calls, [])

    def test_a_pasted_figure_names_the_threads_valuation(self):
        for q in (FIGURE, '1,234,567.89', 'total brand value $1,234,567.89', 'what is $1,234,567.89'):
            hit = VR.read(q, VALUATION_THREAD)
            self.assertIsNotNone(hit, q)
            self.assertEqual(hit['kind'], 'figure')
            self.assertEqual(hit['subject'], PAIR)
            self.assertEqual((hit['start'], hit['end']), ('2026-09-01', '2026-09-08'))
        self.assertIsNone(VR.read('$999,999 total brand value', VALUATION_THREAD))      # not a figure this thread carries
        self.assertIsNone(VR.read('how many people watched in 2025', VALUATION_THREAD))
        u = U.features(FIGURE, {}, VALUATION_THREAD, {})
        self.assertEqual((u.intent, u.referent), ('definition', 'last_valuation'))
        self.assertEqual([s.as_written for s in u.subjects], [PAIR])
        reply = VR.figure_payload(VR.read(FIGURE, VALUATION_THREAD))['reply']
        self.assertIn(PAIR, reply)
        self.assertIn('September 1 to September 8, 2026', reply)
        self.assertNotIn('Do you mean', reply)


class PositioningIsProductKnowledge(unittest.TestCase):
    """3. A question about Prometheus itself is answered from product knowledge."""

    def setUp(self):
        _clear_flags()

    def test_the_family(self):
        for q in (LIZ_1, LIZ_2, 'how is Prometheus different from ChatGPT', 'what is Prometheus?', 'what are you', 'write a tagline for Crosswalk',
                  'Prometheus vs AI', 'how would you describe yourself', 'why use Prometheus over a regular AI tool', 'are you just an AI',
                  'what makes Crosswalk different', 'give me an elevator pitch for Prometheus'):
            self.assertEqual(PF.family_of(q), 'positioning', q)
        for q in ('what is the Landman audience like', 'give me a description of the Nike audience', 'how is Nike different from Adidas',
                  'is Prometheus the titan a popular film', 'what is the value of the Gunna x Nike partnership', 'describe the Pinterest audience',
                  'who is the audience for The Office', 'what is Nike', 'can you describe what people who watch Landman buy'):
            self.assertNotEqual(PF.family_of(q), 'positioning', q)

    def test_the_answer_is_jennas(self):
        a = PF.answer(LIZ_1)
        self.assertEqual(a['family'], 'positioning')
        for line in PF.POSITIONING_LINES:
            self.assertIn(line, a['reply'])
        self.assertIn('My pick is the first', a['reply'])
        b = PF.answer('how is Prometheus different from ChatGPT')
        self.assertIn('actually do', b['reply'])
        self.assertNotIn('1.', b['reply'])
        for text in (a['reply'], b['reply']):
            low = text.lower()
            for w in ('synth', 'modeled', 'pipeline', 'claude', 'anthropic', 'llm'):
                self.assertNotIn(w, low)
            self.assertNotIn('\u2014', text)

    def test_the_understanding_reads_it_as_the_product(self):
        u = U.features(LIZ_1, {}, [], {})
        self.assertEqual((u.intent, u.action), ('capability', 'answer'))
        self.assertTrue(any(str(e).startswith('product:positioning') for e in u.evidence))
        u = U.absorb_reading(u, {'intent': 'analyze', 'confidence': 0.9, 'subjects': [{'as_written': 'CW', 'kind': 'brand'}]})
        self.assertEqual(u.intent, 'capability')
        self.assertFalse(RF.plausible_subject('CW Prometheus'))


class ThroughTheDoor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _clear_flags()
        cls.stub = RC._Stub()
        cls.stub.bind()

    def _ask(self, text, history, reading=None, subject='', view='', **extra):
        case = {'id': 'x', 'question': text, 'view': view, 'surface': 'analyze', 'subject': subject or None,
                'history': list(history), 'reading': reading}
        case.update(extra)
        return RC._service_once(self.stub, case)

    def test_the_audience_question_carries_its_subject_past_the_page(self):
        live = self._ask(AUDIENCE_Q, [], reading=AUDIENCE_READING, subject=PAGE)
        self.assertEqual(live['route'], 'page_analysis', live['reply'][:200])
        self.assertIn('named:audience', live['u'])
        self.assertNotIn('memory_confirm', live['outcome'])

    def test_the_pasted_figure_is_the_threads_valuation(self):
        live = self._ask(FIGURE, VALUATION_THREAD, view='brandPartnershipIQ',
                         reading={'intent': 'definition', 'confidence': 0.79, 'flags': ['refers_to_page'],
                                  'subjects': [{'as_written': 'total brand value', 'catalog': '', 'kind': 'title'}]})
        self.assertEqual((live['route'], live['outcome']), ('valuation_figure', 'figure'), live['reply'][:200])
        self.assertIn(PAIR, live['reply'])
        self.assertNotIn('Do you mean for', live['reply'])
        self.assertNotIn('Brand Partnership IQ', live['reply'])
        self.assertIn('flip=1', live['u'])

    def test_a_product_chip_is_never_a_bound_subject(self):
        live = self._ask(FIGURE, VALUATION_THREAD, view='brandPartnershipIQ', bind_subject=PRODUCT_CHIP)
        self.assertEqual((live['route'], live['outcome']), ('valuation_figure', 'figure'), live['reply'][:200])
        self.assertNotIn('armed', live['u'])
        self.assertEqual(self.stub.charges, [])

    def test_the_positioning_question_is_answered_from_product_knowledge(self):
        for q, rd in ((LIZ_1, {'intent': 'chat', 'confidence': 0.62, 'subjects': [], 'flags': []}), (LIZ_2, None)):
            live = self._ask(q, [], reading=rd)
            self.assertEqual(live['route'], 'product_facts', live['reply'][:200])
            self.assertNotIn('Which subject', live['reply'])
            self.assertIn('Answers from real consumer behavior', live['reply'])
            self.assertIn('intent=capability', live['u'])
            self.assertIn('flip=1', live['u'])

    def test_the_switch_keeps_the_lane_on_its_own_predicate(self):
        os.environ['PM_U_OFF_CAPABILITY'] = '1'
        try:
            live = self._ask(LIZ_1, [], reading={'intent': 'chat', 'confidence': 0.62, 'subjects': [], 'flags': []})
            self.assertEqual(live['route'], 'product_facts')
            self.assertNotIn('flip=1', live['u'])
        finally:
            os.environ.pop('PM_U_OFF_CAPABILITY', None)


if __name__ == '__main__':
    unittest.main(verbosity=1)
