#!/usr/bin/env python3
"""The guard: what is on screen never decides the subject or supplies a
read's base (Jenna 2026-11-20, verbatim: "ive repeatedly said not to use
whats on screen for context to always ask").

One sweep over every read entry point, each with an open view of a
DIFFERENT subject (a profile, Example Network, carrying a BEAUTY row; a
valuation view, Other Talent x Other Brand) and asks that name no subject
and do not point at the screen. Each asserts the read's subject and base
come from the words or the thread, or a question is asked with the open
view as at most one tap, never from the view:

  * the door (service.ask) with the real analyze core behind it, on the
    analyze surface, across the lanes (sizing, where watched, brand
    coverage, unprojected, custom reads, a deck ask) and the Emma case
    without the named subject ("does this audience have interest in
    beauty?" with the profile open and no thread subject): a question;
  * the Understanding (no page referent, no subject);
  * the Reader's executors (exists, size, analyze with a refers_to_page
    flag the words do not back);
  * the read core's page path (the open-screen confirm skipped);
  * the deck builder's subject step;
  * the resume path (a pinned base stays; a job never pins the page);
  * the partner door (no screen; a view id alone is never a subject);
  * the positive controls: words that point at the screen ("this
    profile", "the one I have open", "on this page") use it; a thread
    whose last answer landed on another subject carries that subject.

Hermetic: no model, no S3, no mail, no app boot. The legacy chat module
is bound to the replay stub with a one-file library and the tiny profile
in the fake store.
"""
from __future__ import annotations

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
for _k in list(os.environ):
    if _k.startswith('PM_U_OFF_') or _k in ('PM_UNDERSTANDING', 'PM_SCREEN_SCOPE'):
        os.environ.pop(_k, None)

from pm_corpus import replay_corpus as RC  # noqa: E402
RC.install_isolation_light()
import prometheus.legacy as _legacy  # noqa: E402
from prometheus import screen_scope as SS  # noqa: E402
from prometheus import understanding as U  # noqa: E402
from prometheus import page_default as PD  # noqa: E402
from prometheus import read_recovery as RR  # noqa: E402

PAGE = 'Example Network'
PAGE_KEY = 'Example_Network_06_14_2026_01_37.csv'
PAGE_CTX = {'primary': {'name': PAGE, 's3_key': PAGE_KEY}, 'cuts': []}
TINY_PROFILE = (
    "Column,Value,Brand Penetration (Row),Category Share,Original Raw Numbers,US Gen Pop Projection\n"
    "BRAND INPUT,EXAMPLE NETWORK,100.0000%,100.0000%,1217543,40166793\n"
    "SAMPLE SIZE,SAMPLE SIZE (2025-01-01 TO 2025-12-31),100.0000%,100.0000%,1217543,40166793\n"
    "BRAND CATEGORY,SOCIAL MEDIA,100.0000%,100.0000%,1217543,40166793\n"
    "INTEREST,BEAUTY,73.9404%,12.1100%,900257,29700013\n"
    "MOST PURCHASED CATEGORIES,BEAUTY,73.9363%,9.8800%,900207,29698363\n"
    "MOST PURCHASED BRANDS,Northwind Grooming,4.1212%,0.8800%,50181,1655581\n"
    "GENDER,Female,72.6917%,72.6917%,885052,29198711\n"
    "GENDER,Male,27.3083%,27.3083%,332491,10968082\n"
)
OTHER_PAIR = 'Other Talent x Other Brand'
VIEW_CTX = {'primary': None, 'cuts': [], 'extras': [],
            'view_context': {'view_id': 'brandPartnershipIQ', 'view_title': OTHER_PAIR,
                             'data': {'title': OTHER_PAIR, 'total_brand_value': 912816, 'window': 'Oct 25 2026 to Nov 1 2026'}}}

# asks that name no subject and do not point at the screen
NO_SUBJECT = (
    'does this audience have interest in beauty?',                     # Emma, without the named subject
    'is beauty a confirmed purchase for these people?',               # a word that is a row of the open file
    'how many people buy beauty products?',
    'what is the gender split?',
    'how big is the audience?',
    'how many people are in it?',
    'what do they buy?',
    'where do they watch?',
    'which brands index highest?',
    'rank the top retailers for these shoppers',
    'how many subscribers does it have?',
    'what is the unprojected number?',
    'who are they?',
    'what share are women?',
)
# asks that point at the screen: the open view is the subject
POINTED = (
    'does this profile skew female?',
    'what is the size of the one I have open?',
    'on this page, what share buy beauty?',
    'summarize the profile I have open',
)
THREAD_SUBJECT = 'Northwind Sports'
THREAD = [
    {'role': 'user', 'text': f'what do {THREAD_SUBJECT} fans buy'},
    {'role': 'agent', 'text': f'On {THREAD_SUBJECT}: 41.2% placed an order with a beauty or grooming item in the window.',
     'meta': {'subject': THREAD_SUBJECT, 'kind': 'read'}},
]


def _norm(s):
    if not s:
        return ''
    s = str(s).lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class _Harness:
    """The replay stub with the real analyze core and read core behind it."""
    stub = None
    C = None
    hints = None

    @classmethod
    def up(cls):
        if cls.stub is not None:
            return cls
        cls.stub = RC._Stub()
        cls.stub.bind()
        old = object.__getattribute__(_legacy.H, '_mod')
        names = dict(object.__getattribute__(old, '_names'))
        names['_normalize_for_match'] = _norm
        names['_profile_catalog_for_chat'] = lambda: [{'display_name': PAGE, 'subject': PAGE, 's3_key': PAGE_KEY, 'category': 'SOCIAL MEDIA'}]
        names['_require_profile_run_access'] = lambda k: (True, None)
        names['_pm_claude_json'] = lambda *a, **k: {'success': False, 'error': 'no model'}
        names['_pm_claude_data'] = lambda *a, **k: None
        _legacy.bind(RC._LegacyNS(names, object.__getattribute__(old, '_log')))
        from prometheus.legacy import chat as C
        from prometheus import host as H
        cls.C = C
        cls.hints = []
        orig = C._pm_ask_hint

        def _hint(**k):
            cls.hints.append(dict(k))
            return orig(**k)
        C._pm_ask_hint = _hint
        H.host.bind(analyze_core=C._pm_analyze_core, bpiq_intent=C._pm_bpiq_intent)
        RC._FAKE['s3'].objects[PAGE_KEY] = TINY_PROFILE.encode('utf-8')
        return cls

    @classmethod
    def ask(cls, text, history=(), ctx=None, reading=None, client='dashboard', **extra):
        from prometheus import service
        from flask import g
        cls.up()
        cls.stub.hints.clear()
        cls.stub.cores.clear()
        cls.stub.reading = reading
        cls.hints.clear()
        body = {'text': text, 'client': client, 'persist': False, 'surface': 'analyze', 'history': list(history),
                'context': ctx, 'thread_id': 'tguard'}
        body.update(extra)
        with cls.stub.app.test_request_context('/api/prometheus/v1/ask', method='POST',
                                                headers={'X-Prometheus-Caller': 'replay'}, json=body):
            env, status = service.ask(dict(RC.REPLAY_USER), body)
            u = U.current()
        raw = env.get('raw') if isinstance(env.get('raw'), dict) else (env.get('_raw') if isinstance(env.get('_raw'), dict) else {})
        return {'text': str(env.get('text') or ''), 'raw': raw or {}, 'options': list(env.get('options') or []),
                'hints': list(cls.hints), 'u': u, 'status': status, 'decision': env.get('decision') or {}}


def _subjects_in(hints):
    return [str(h.get('subject') or '') for h in hints if h.get('subject')]


def _is_question(out, page):
    """A short question that leaves the subject to the reader, with the
    open view as at most one tap."""
    text = out['text']
    raw = out['raw']
    asks = bool(raw.get('audience_confirm') or raw.get('memory_confirm') or raw.get('intake_resume')
                or re.search(r"\b(?:which (?:audience|subject|profile|campaign|one)|do you (?:want|mean)|should (?:i|this)|name (?:it|one|the))\b", text, re.I))
    return asks and '?' in text


def _subject_from_words(out, page):
    """The lane settled a subject that is not the page (the words named a
    universe of their own, or the thread carried one)."""
    subs = [s for s in _subjects_in(out['hints']) if s and _norm(s) != _norm(page)]
    return bool(subs)


def _page_decided(out, page):
    """The page chosen as the subject or the base without a question."""
    text = out['text']
    if _is_question(out, page):
        return False
    subs = [s for s in _subjects_in(out['hints'])]
    if any(_norm(s) == _norm(page) for s in subs):
        return True
    if re.match(r"^\s*On " + re.escape(page), text) or re.search(r"\breading " + re.escape(page) + r"\b", text, re.I):
        return True
    if str(out['raw'].get('profile') or '').strip() == page or str(out['raw'].get('subject') or '').strip() == page:
        return True
    return False


class TheRule(unittest.TestCase):
    def test_pointers_and_non_pointers(self):
        for q in POINTED:
            self.assertTrue(SS.points_at_screen(q), q)
        for q in NO_SUBJECT:
            self.assertFalse(SS.points_at_screen(q), q)

    def test_page_is_subject_only_on_the_readers_own_words(self):
        for q in NO_SUBJECT:
            ok, why = SS.page_is_subject(q, [], PAGE)
            self.assertFalse(ok, (q, why))
        self.assertEqual(SS.page_is_subject('does this profile skew female?', [], PAGE)[1], 'pointed')
        self.assertEqual(SS.page_is_subject(f'what do {PAGE} users buy', [], PAGE)[1], 'named')
        self.assertEqual(SS.page_is_subject('what do they buy', [{'role': 'user', 'text': f'Yes, {PAGE}'}], PAGE)[1], 'thread on page')
        self.assertEqual(SS.page_is_subject('what do they buy', [{'role': 'agent', 'text': f'On {PAGE}: 4% buy beauty', 'meta': {'subject': PAGE}}], PAGE)[1],
                         'thread on page')
        self.assertFalse(SS.page_is_subject('what do they buy', THREAD, PAGE)[0])   # the thread is on another subject

    def test_a_view_is_named_by_what_it_is_about_never_by_a_shared_word(self):
        hyd = {'view_id': 'subscriberIQ', 'view_title': 'Subscriber IQ',
               'data': {'show': 'Landman', 'episodes': [{'title': 'The Death of Us', 'signups': 24138}, {'title': 'Sins of the Father', 'signups': 18211}]}}
        iiq = {'view_id': 'intentIQ', 'view_title': 'Attribution IQ', 'data': {'campaign': 'Lexus x Colin and Samir', 'steps': [{'label': 'Ticket checkout'}]}}
        self.assertTrue(SS.view_named('how did Sins of the Father do against Landman overall?', hyd))
        self.assertTrue(SS.view_named('what did the Lexus campaign convert?', iiq))
        for q in ('where is the fall-off from the ticket checkout page?', 'why do people abandon at checkout?', 'how are our competitors doing this quarter?',
                  'does this audience have interest in beauty?'):
            self.assertFalse(SS.view_named(q, hyd), q)
            self.assertFalse(SS.view_named(q, iiq), q)

    def test_a_correction_drops_the_screen_subject_even_typed_short(self):
        # the corpus case behind page_default's own note: a read started on the open LeBron James profile, "no! Lebron
        # james hs nothing to do with this" must drop LeBron, not pin him and not offer him again
        from prometheus import thread_subject as TS, subject_rules as SR
        self.assertEqual(TS.dropped_names('no! Lebron james hs nothing to do with this'), ['lebron james'])
        hist = [{'role': 'user', 'text': 'Which retailer is winning the small kitchen appliance purchase among Amazon, Walmart and Target cross-shoppers?'},
                {'role': 'agent', 'text': 'On it: reading LeBron James now. This one takes a real look at the data.'}]
        c = SR._current_continuation('no! Lebron james hs nothing to do with this', hist, 'LeBron James')
        self.assertEqual(c.get('kind'), 'correction')
        self.assertEqual(c.get('subject'), '')
        self.assertIn('lebron james', (c.get('params') or {}).get('negated') or [])


class TheUnderstanding(unittest.TestCase):
    def test_no_page_referent_without_a_pointer(self):
        for q in NO_SUBJECT:
            u = U.features(q, PAGE_CTX, [], {})
            self.assertNotEqual(u.referent, 'page', q)
            self.assertNotEqual(_norm(U.named_subject(u, q, PAGE)), _norm(PAGE), q)
            self.assertFalse(any(s.as_written == PAGE for s in u.subjects), q)
        for q in POINTED:
            u = U.features(q, PAGE_CTX, [], {})
            self.assertEqual(u.referent, 'page', q)


class TheReadCorePagePath(unittest.TestCase):
    """The read core with the open-screen confirm skipped (a continuation on
    the request): the page must still not stand."""

    def setUp(self):
        _Harness.up()

    def _core(self, text, history=(), ctx=PAGE_CTX, **kw):
        from flask import g
        C = _Harness.C
        _Harness.hints.clear()
        with _Harness.stub.app.test_request_context('/api/prometheus/v1/ask', method='POST', json={'page_context': ctx}):
            g._pm_understanding = U.features(text, ctx, list(history), {}).to_dict()
            g._pm_continuation = {'kind': 'reply'}
            resp = C._pm_generate_metrics_response(dict(RC.REPLAY_USER), text, list(history), ctx=ctx, prefer_catalog=True, **kw)
        raw = resp.get_json() if hasattr(resp, 'get_json') else resp
        return {'text': str(raw.get('reply') or raw.get('error') or ''), 'raw': raw, 'options': raw.get('followups') or [],
                'hints': list(_Harness.hints)}

    def test_the_page_never_stands_without_a_pointer(self):
        for q in NO_SUBJECT:
            stands, why = PD.page_base_stands(q, [], PAGE, PAGE_KEY, '', normalize=_norm, stop_tokens=frozenset(),
                                              brand_named=lambda t, k: 'BEAUTY', ask_user='seat')
            self.assertFalse(stands, (q, why))
        for q in POINTED:
            stands, why = PD.page_base_stands(q, [], PAGE, PAGE_KEY, '', normalize=_norm, stop_tokens=frozenset(), brand_named=None, ask_user='')
            self.assertTrue(stands, (q, why))

    def test_a_no_subject_ask_is_a_question_never_a_read_on_the_page(self):
        for q in NO_SUBJECT:
            out = self._core(q)
            self.assertFalse(_page_decided(out, PAGE), (q, out['text'][:160], out['hints'][-2:]))
            self.assertTrue(_is_question(out, PAGE) or _subject_from_words(out, PAGE), (q, out['text'][:160]))
            if _is_question(out, PAGE):
                self.assertTrue(any(PAGE in str(o) for o in out['options']) or PAGE in out['text'], (q, out['options']))   # the page is one tap

    def test_the_thread_subject_carries_forward(self):
        out = self._core('what do they purchase online?', THREAD)
        self.assertFalse(_page_decided(out, PAGE), out['text'][:200])
        subs = _subjects_in(out['hints'])
        self.assertTrue(any(THREAD_SUBJECT in s for s in subs) or THREAD_SUBJECT in out['text'], (subs, out['text'][:200]))

    def test_a_pointer_uses_the_page(self):
        out = self._core('does this profile skew female?')
        self.assertTrue(_page_decided(out, PAGE) or PAGE in out['text'], (out['text'][:200], out['hints'][-3:]))

    def test_a_resumed_read_keeps_its_pinned_base(self):
        rec = RR.resume_record(text='what do they buy?', history=[], base={'subject': 'Example Sports audience', 's3_key': '', 'source': 'panel'})
        out = self._core('what do they buy?', resume_base=rec['base'])
        subs = _subjects_in(out['hints'])
        self.assertFalse(any(_norm(s) == _norm(PAGE) for s in subs), subs)
        self.assertNotIn(f'reading {PAGE}', out['text'])


class TheDoor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _Harness.up()

    def test_no_subject_asks_are_questions_with_the_page_as_one_tap(self):
        for q in NO_SUBJECT:
            out = _Harness.ask(q, ctx=PAGE_CTX)
            self.assertFalse(_page_decided(out, PAGE), (q, out['text'][:160], out['hints'][-3:]))
            self.assertTrue(_is_question(out, PAGE) or _subject_from_words(out, PAGE), (q, out['text'][:160], out['hints'][-3:]))

    def test_the_emma_case_without_the_named_subject(self):
        q = 'does this audience have interest in beauty?'
        rd = {'intent': 'analyze', 'confidence': 0.74, 'flags': ['refers_to_page'],
              'subjects': [{'as_written': 'this audience', 'catalog': '', 'kind': 'audience'}]}
        out = _Harness.ask(q, ctx=PAGE_CTX, reading=rd)
        self.assertTrue(_is_question(out, PAGE), (out['text'][:200], out['hints'][-3:]))
        self.assertFalse(_page_decided(out, PAGE), out['text'][:200])
        self.assertTrue(any(PAGE in str(o) for o in out['options']) or PAGE in out['text'], out['options'])

    def test_the_readers_executors_never_take_the_page_on_a_flag(self):
        for intent in ('exists', 'size', 'analyze'):
            rd = {'intent': intent, 'confidence': 0.9, 'flags': ['refers_to_page'], 'subjects': []}
            out = _Harness.ask('how many people are in it?' if intent == 'size' else 'do we have it?' if intent == 'exists' else 'what do they buy?',
                               ctx=PAGE_CTX, reading=rd)
            self.assertFalse(_page_decided(out, PAGE), (intent, out['text'][:160], out['hints'][-3:]))

    def test_a_valuation_view_never_answers_for_itself_without_a_pointer(self):
        for q in ('why is it this size?', 'how many people did it reach?', 'what did the campaign earn in media?'):
            out = _Harness.ask(q, ctx=VIEW_CTX)
            self.assertFalse(_page_decided(out, OTHER_PAIR), (q, out['text'][:160], out['hints'][-3:]))
            self.assertNotIn('912,816', out['text'])                      # the view's own figure never rides an answer
            self.assertNotIn('912816', out['text'])
            if not _is_question(out, OTHER_PAIR):
                self.assertNotIn(OTHER_PAIR, out['text'], (q, out['text'][:160]))
        out = _Harness.ask('what is the total brand value?', ctx=VIEW_CTX)   # a definition of the metric is product knowledge, not the view's figure
        self.assertNotIn('912,816', out['text'])
        self.assertFalse(_page_decided(out, OTHER_PAIR), out['text'][:160])

    def test_pointers_use_the_page_and_a_thread_subject_carries(self):
        for q in POINTED:
            out = _Harness.ask(q, ctx=PAGE_CTX)
            self.assertFalse(_is_question(out, PAGE) and 'Which audience' in out['text'], (q, out['text'][:160]))
        out = _Harness.ask('what do they purchase online?', history=THREAD, ctx=PAGE_CTX)
        self.assertFalse(_page_decided(out, PAGE), out['text'][:200])

    def test_the_deck_ask_never_builds_on_the_page(self):
        out = _Harness.ask('build a deck on the beauty finding', ctx=PAGE_CTX)
        self.assertFalse(_page_decided(out, PAGE), (out['text'][:200], out['hints'][-3:]))

    def test_the_partner_door_has_no_screen(self):
        out = _Harness.ask('what do they buy?', ctx={'api': True, 'view_id': 'profileIQ'}, client='api')
        self.assertFalse(_page_decided(out, PAGE), out['text'][:200])
        src = open(os.path.join(ROOT, 'prometheus', 'partner_api.py'), encoding='utf-8').read()
        self.assertNotIn("'primary'", src)
        self.assertNotIn('page_context', src)


if __name__ == '__main__':
    unittest.main(verbosity=1)
