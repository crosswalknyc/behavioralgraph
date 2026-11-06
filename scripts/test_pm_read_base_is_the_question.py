#!/usr/bin/env python3
"""The base of a read is the universe the question names (Jenna 2026-11-06).

Jenna, with a profile open on her dashboard: "over the past three years
show me the fluctuation of when people start christmas holiday toy
shopping and give me a social media and paid search strategy to hit
potential paw patrol consumers." The reply opened "On Paw Patrol toy
buyers in the Starz audience:" with every figure a share of the Starz
profile. The question never mentioned Starz: the page (Mountain Dew) was
dropped, then the catalog's typo tier read the word "start" as a
one-letter misspelling of "Starz" and bound that file, and the read lane
had no rule that the question had already named its universe.
Jenna: "not sure why it is saying of the starz audience. had nothing to
do with that ... make sure that doesnt happen again."

The rule, shared by the sizing lane and the reasoned read lane through
prometheus.question_universe: the page's audience is the base only when
the question points at it ("this audience", "these viewers", "them",
"of those" after a page read, a bare "how many people") or names the
page's own subject. A question that names its own universe ("people who
...", "potential <brand> consumers", "<brand> buyers", "US households",
"when people start holiday toy shopping") is read on that universe, US
people: the page is ignored for the base, the framing and the "On
<subject>" header, the partial-name and typo tiers stand down, and a
reply framed on a dashboard audience the question never named never
ships. Hermetic: no network, no model, no S3.
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')


class _NoAppModule:
    """`import app` never succeeds inside this suite (the legacy chat module
    is bound to a permissive fake host below instead)."""

    def find_spec(self, name, path=None, target=None):
        if name == 'app':
            raise ImportError('app is not importable inside this hermetic suite')
        return None


sys.meta_path.insert(0, _NoAppModule())


def _norm(s):
    if not s:
        return ''
    s = str(s).lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class _FakeHost:
    """app.py's namespace as the legacy chat module reads it: the real
    normalizer, a bucket name, and a mock for everything else."""
    _normalize_for_match = staticmethod(_norm)
    S3_BUCKET = 'dashboard-inputs'

    def __getattr__(self, name):
        return mock.MagicMock(name=name)


import prometheus.legacy as _legacy  # noqa: E402
_legacy.bind(_FakeHost())
from prometheus.legacy import chat as C  # noqa: E402
from prometheus import named_subject as NS  # noqa: E402
from prometheus import priced_read as P  # noqa: E402
from prometheus import question_universe as QU  # noqa: E402
from prometheus import sizing_lane as SZ  # noqa: E402

JENNA = ("over the past three years show me the fluctuation of when people start christmas holiday toy shopping "
         "and give me a social media and paid search strategy to hit potential paw patrol consumers.")
HINT = 'When People Start Christmas Holiday Toy Shopping'   # the router's paraphrase, as logged
STARZ_KEY = 'Starz_10_01_2026_17_33.csv'
STARZ = {'view': 'profile-iq', 'primary': {'name': 'Starz', 's3_key': STARZ_KEY}}
TWIN = {'view': 'digitalTwinsIQ', 'primary': {'name': 'Starz', 's3_key': STARZ_KEY},
        'view_context': {'view_id': 'digitalTwinsIQ', 'view_title': 'Starz', 'data': {'subject': 'Starz'}}}
DEW = {'view': 'profile-iq', 'primary': {'name': 'Mountain Dew', 's3_key': 'Mountain_Dew_10_01_2026.csv'}}
CATALOG = [
    {'s3_key': STARZ_KEY, 'display_name': 'Starz', 'subject': 'Starz', 'category': 'STREAMING/PLATFORM'},
    {'s3_key': 'Starz - Avid Fan.csv', 'display_name': 'Starz - Avid Fan', 'subject': 'Starz', 'category': 'STREAMING/PLATFORM'},
    {'s3_key': 'Paw_Patrol_Ticket_Purchasers_09_25_2026_00_17.csv', 'display_name': 'Paw Patrol Ticket Purchasers',
     'subject': 'Paw Patrol Ticket Purchasers', 'category': 'TICKETING'},
    {'s3_key': 'Mountain_Dew_10_01_2026.csv', 'display_name': 'Mountain Dew', 'subject': 'Mountain Dew', 'category': 'BEVERAGE'},
    {'s3_key': 'Godzilla_Minus_One_01.csv', 'display_name': 'Godzilla Minus One', 'subject': 'Godzilla Minus One', 'category': 'MOVIE'},
    {'s3_key': 'Costco_01.csv', 'display_name': 'Costco', 'subject': 'Costco', 'category': 'RETAILERS'},
    {'s3_key': 'Howie_Mandel_06_09_2026_12_11.csv', 'display_name': 'Howie Mandel', 'subject': 'Howie Mandel', 'category': 'COMEDIAN'},
]
C._profile_catalog_for_chat = lambda: CATALOG


def _base(hint, text, ctx=None, **kw):
    return C._pm_generation_base(hint, text, ctx=ctx, **kw)


class TheReading(unittest.TestCase):
    """question_universe: the one reading both lanes take."""

    def test_jennas_question_names_its_own_universe_whatever_is_open(self):
        for page in ('Starz', 'Mountain Dew', ''):
            u = QU.question_universe(JENNA, page)
            self.assertEqual(u.kind, 'named', page)
            self.assertTrue(u.explicit)
            self.assertEqual(u.label, 'potential Paw Patrol consumers')
            self.assertEqual(u.base, QU.US_PEOPLE)
            self.assertNotIn('starz', ' '.join(u.phrases).lower())
        self.assertTrue(QU.names_other_universe(JENNA, 'Starz', page='Starz'))
        self.assertTrue(QU.names_other_universe(JENNA, 'Mountain Dew', page='Mountain Dew'))

    def test_pointing_at_the_page_binds_the_page(self):
        for t in ('how many of this audience bought Paw Patrol toys', 'of those, how many bought paw patrol toys',
                  'how many of them bought Paw Patrol toys', 'what share of these viewers bought a Paw Patrol toy',
                  'how many people', 'how big is it', 'give me a social media strategy for this audience',
                  'how do I reach them on tiktok'):
            u = QU.question_universe(t, 'Starz')
            self.assertEqual((u.kind, u.label), ('page', 'Starz'), t)
            self.assertFalse(QU.names_other_universe(t, 'Starz', page='Starz'), t)

    def test_naming_the_pages_own_subject_binds_the_page(self):
        for t in ('how many Starz subscribers bought Paw Patrol toys', "what do Starz's viewers buy for the holidays",
                  'when do Starz subscribers start holiday toy shopping'):
            u = QU.question_universe(t, 'Starz')
            self.assertEqual((u.kind, u.label), ('page', 'Starz'), t)

    def test_other_named_universes(self):
        cases = {
            'what share of US households bought a toy online last year': ('US households', QU.US_PEOPLE, True),
            'what do Costco members buy for the holidays': ('Costco members', 'Costco members', True),
            'what are parents of kids 4-7 buying in terms of toy categories': ('parents of kids 4-7', QU.US_PEOPLE, True),
            'what percentage of people who watched fightland on starz then proceeded to watch swat exiles directly after':
                ('people who watched Fightland on Starz', QU.US_PEOPLE, True),
            'when do people start holiday shopping': (QU.US_PEOPLE, QU.US_PEOPLE, False),
            'how many people watched Carrie on Prime Video': (QU.US_PEOPLE, QU.US_PEOPLE, False),
        }
        for t, (label, base, explicit) in cases.items():
            u = QU.question_universe(t, 'Starz')
            self.assertEqual(u.kind, 'named', t)
            self.assertEqual((u.label, u.base, u.explicit), (label, base, explicit), t)

    def test_nothing_named_nothing_pointed_at_is_none(self):
        for t in ('what are the top 10 brands', 'what is the median age', 'run a profile on mountain dew',
                  'Within Profiles IQ', 'what are they googling'):
            self.assertEqual(QU.question_universe(t, '').kind, 'none', t)

    def test_the_subject_a_no_base_read_runs_on(self):
        u = QU.question_universe(JENNA, 'Starz')
        self.assertEqual(QU.subject_for(u, HINT, JENNA), 'potential Paw Patrol consumers')
        u2 = QU.question_universe('how many people watched Carrie on Prime Video', '')
        self.assertEqual(QU.subject_for(u2, 'Carrie', 'how many people watched Carrie on Prime Video'), 'Carrie')
        u3 = QU.question_universe('when do people start holiday shopping', '')
        self.assertEqual(QU.subject_for(u3, 'Holiday Shopping', 'when do people start holiday shopping'), QU.EVERYONE)
        self.assertEqual(QU.panel_base(u), {'subject': 'potential Paw Patrol consumers', 's3_key': '', 'source': 'panel',
                                            'universe': QU.US_PEOPLE})
        self.assertEqual(QU.universe_label(u), 'potential Paw Patrol consumers in the US')
        block = QU.framing_block(u, JENNA)
        self.assertIn('potential Paw Patrol consumers', block)
        self.assertIn(QU.US_PEOPLE, block)
        self.assertNotIn('Starz', block)


class TheReadLaneBase(unittest.TestCase):
    """_pm_generation_base: the page and the guessed tiers stand down."""

    def test_jennas_question_binds_no_page_and_no_guessed_file(self):
        self.assertIsNone(_base(HINT, JENNA, ctx=STARZ))
        self.assertIsNone(_base(HINT, JENNA, ctx=TWIN))
        self.assertIsNone(_base(HINT, JENNA, ctx=DEW))
        self.assertIsNone(_base(HINT, JENNA, ctx=None))
        self.assertIsNone(_base('', JENNA, ctx=STARZ, prefer_catalog=True))

    def test_start_is_never_a_typo_of_starz(self):
        self.assertTrue(QU.ordinary_word('start'))
        self.assertFalse(QU.ordinary_word('starz'))
        self.assertFalse(QU.ordinary_word('godzila'))
        self.assertIsNone(NS.fuzzy_catalog_subject(JENNA, CATALOG, normalize=_norm, typo_eq=C._pm_token_typo_eq,
                                                   expand_alias=lambda t, r: set(t), generic_tokens=C._PM_BASE_GENERIC_TOKENS))
        self.assertIsNone(NS.fuzzy_catalog_subject('when do people start holiday shopping', CATALOG, normalize=_norm,
                                                   typo_eq=C._pm_token_typo_eq, expand_alias=lambda t, r: set(t),
                                                   generic_tokens=C._PM_BASE_GENERIC_TOKENS))
        # a real typo of a title still binds
        fz = NS.fuzzy_catalog_subject('how many people watched godzila minus one', CATALOG, normalize=_norm,
                                      typo_eq=C._pm_token_typo_eq, expand_alias=lambda t, r: set(t),
                                      generic_tokens=C._PM_BASE_GENERIC_TOKENS)
        self.assertEqual((fz or {}).get('subject'), 'Godzilla Minus One')

    def test_the_page_binds_when_pointed_at_or_named(self):
        for t in ('how many of this audience bought Paw Patrol toys', 'of those, how many bought paw patrol toys',
                  'how many people are in this audience'):
            b = _base('', t, ctx=STARZ)
            self.assertEqual((b or {}).get('source'), 'page', t)
            self.assertEqual((b or {}).get('subject'), 'Starz', t)
        b = _base('Starz', 'how many Starz subscribers bought Paw Patrol toys', ctx=STARZ)
        self.assertEqual(((b or {}).get('subject'), (b or {}).get('source')), ('Starz', 'page'))

    def test_a_chip_or_an_approved_quote_still_binds_its_subject(self):
        b = _base('Starz', 'Starz', ctx=STARZ, prefer_catalog=True, bound=True)
        self.assertEqual(((b or {}).get('subject'), (b or {}).get('s3_key')), ('Starz', STARZ_KEY))

    def test_a_population_behavior_keeps_the_title_it_nearly_names(self):
        b = _base('', 'how many people watched godzila minus one', ctx=STARZ)
        self.assertEqual((b or {}).get('subject'), 'Godzilla Minus One')
        self.assertFalse(QU.names_other_universe('how many people watched godzila minus one', 'Godzilla Minus One'))

    def test_a_named_audience_binds_its_own_brand_even_misspelled_never_another(self):
        self.assertEqual((_base('Costco', 'what do Costco members buy for the holidays', ctx=STARZ) or {}).get('subject'), 'Costco')
        self.assertEqual((_base('', 'what do cosco members buy for the holidays', ctx=STARZ) or {}).get('subject'), 'Costco')
        self.assertFalse(QU.names_other_universe('what do cosco members buy for the holidays', 'Costco'))
        self.assertTrue(QU.names_other_universe('what do cosco members buy for the holidays', 'Starz', page='Starz'))


class TheOutputGuard(unittest.TestCase):
    def test_a_reply_framed_on_the_dropped_base_is_a_leak(self):
        reply = ("On Paw Patrol toy buyers in the Starz audience:\n\nThe median first holiday toy order from this audience "
                 "has moved 8 days earlier in three seasons ...")
        res = {'subject': 'Paw Patrol toy buyers', 'universe': 'Paw Patrol toy buyers in the Starz audience'}
        self.assertEqual(QU.base_leak(reply, res, ['Mountain Dew', 'Starz'], text=JENNA), 'Starz')
        self.assertEqual(QU.base_leak("On US people:\n\n33.6% of Paw Patrol toy buyers in the Starz audience start before November.",
                                      {'subject': 'potential Paw Patrol consumers'}, ['Starz'], text=JENNA), 'Starz')

    def test_a_universe_in_the_models_own_words_is_not_a_leak(self):
        res = {'subject': 'potential Paw Patrol consumers', 'universe': 'US parents of young kids'}
        self.assertEqual(QU.base_leak("On US parents of young kids:\n\nThe median ...", res, ['Mountain Dew', 'Starz'], text=JENNA), '')
        # a base the question itself named is never a leak
        self.assertEqual(QU.base_leak("On Costco members:\n\n...", {'subject': 'Costco members'}, ['Costco'],
                                      text='what do Costco members buy for the holidays'), '')

    def test_the_read_core_re_reads_on_the_named_universe(self):
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        i = src.index('def _pm_generate_read_core(')
        body = src[i:src.index('\ndef _pm_persist_identity', i)]
        self.assertIn('universe_guard=True', body)
        self.assertIn('_question_universe.base_leak(reply, res, _qu_dash, text=text)', body)
        self.assertIn('base=_question_universe.panel_base(_qu)', body)
        self.assertIn('universe_guard=False', body)
        self.assertLess(body.index('_question_universe.base_leak('), body.index('Verification pass'),
                        'the guard runs before the verification pass spends on a leaked base')
        swap = body.index("reading on {_qu.base!r}")
        self.assertLess(swap, body.index('build_reasoned_metrics_user_prompt'), 'a leaked base is swapped before the prompt is built')

    def test_the_generation_pass_drops_a_base_the_question_never_named(self):
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        i = src.index('def _pm_generate_metrics_response(')
        body = src[i:src.index('\ndef ', i + 10)]
        self.assertIn("_question_universe.names_other_universe(text, base.get('subject'), page=_qu_page)", body)
        self.assertIn("subj_name = _question_universe.subject_for(_qu, subj_name, text)", body)
        self.assertLess(body.index('names_other_universe('), body.index('RESOLUTION LADDER, last rung'))
        self.assertIn("base['universe'] = _qu.base", body)

    def test_cross_session_memory_never_names_a_universe_the_question_named(self):
        """The second run of Jenna's question bound Starz again: memory
        remembered subject Starz with the cohort 'Paw Patrol toy buyers in
        the Starz audience', and the cohort's words matched the question.
        The ladder binds a remembered SUBJECT the words name, never a
        cohort, and never runs when the question names its own universe."""
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        i = src.index('def _pm_generate_metrics_response(')
        body = src[i:src.index('\ndef ', i + 10)]
        ladder = body[body.index('RESOLUTION LADDER, last rung'):body.index('TWO-BASE READS')]
        self.assertIn("if _qu.kind == 'named' and _qu.explicit:\n                _named = _named or _qu.label", ladder)
        self.assertIn("text, o['subject'], _H._normalize_for_match", ladder)
        self.assertNotIn("text, o['label'], _H._normalize_for_match", ladder)
        from prometheus import named_subject as NS
        label = 'Starz (Paw Patrol toy buyers in the Starz audience)'
        self.assertTrue(NS.page_named(JENNA, label, _norm, C._PM_CLARIFY_STOP_TOKENS))      # the cohort's words are in the question
        self.assertFalse(NS.page_named(JENNA, 'Starz', _norm, C._PM_CLARIFY_STOP_TOKENS))   # the subject is not


class SizingLaneStillHolds(unittest.TestCase):
    """The sizing lane imports the shared pointer; its cases still pass."""
    JENNA_SIZING = ("how many power raising kanan season 5 viewers autoplay'ed into fightland  and then of those "
                    "what percent completed that entire season of fightland")
    PAGE = {'view': 'profile-iq', 'primary': {'name': 'Howie Mandel', 's3_key': 'Howie_Mandel_06_09_2026_12_11.csv'}}

    def test_one_copy_of_the_pointer(self):
        self.assertIs(SZ.points_at_page, QU.points_at_page)

    def test_the_sizing_cases(self):
        for t in ('how many people are in this audience', 'how big is it', 'how big is this one',
                  "what's the size of this audience", 'size this'):
            self.assertTrue(SZ.points_at_page(t), t)
            self.assertEqual(SZ.subjects(t, self.PAGE), ['Howie Mandel'], t)
            self.assertTrue(P.sizes_open_page(t, self.PAGE), t)
        for t in (self.JENNA_SIZING, 'how big is that season of Fightland', 'how many people watched that show'):
            self.assertFalse(SZ.points_at_page(t), t)
            self.assertNotIn('Howie Mandel', SZ.subjects(t, self.PAGE), t)
        self.assertEqual(SZ.subjects('how big is the FanDuel audience', self.PAGE), ['FanDuel'])
        self.assertTrue(SZ.names_other_subject(self.JENNA_SIZING, ['Howie Mandel'], self.PAGE))
        self.assertFalse(SZ.names_other_subject('how many people are in this audience', ['Howie Mandel'], self.PAGE))

    def test_jennas_toy_question_is_not_a_sizing(self):
        self.assertFalse(SZ.wants_sizing(JENNA))
        self.assertFalse(P.sizes_open_page(JENNA, STARZ))
        self.assertIsNone(P.classify(JENNA, [], STARZ))


class ThePlanHalf(unittest.TestCase):
    """The second half of Jenna's question is a plan: the read answers the
    figures, then the plan as Prometheus's own reasoning on them."""

    def test_a_plan_asked_beside_the_figures_gets_the_playbook(self):
        import prometheus_analysis as PMA
        self.assertTrue(PMA.is_media_plan_ask(JENNA))
        self.assertTrue(PMA.is_media_plan_ask('how do we reach them on tiktok'))
        for t in ('how many people watched Carrie on Prime Video', 'run a profile on mountain dew',
                  'build me a deck with a social media strategy'):
            self.assertFalse(PMA.is_media_plan_ask(t), t)
        for word in ('WHO', 'WHERE', 'WHEN', 'WHAT TO SAY', 'imperative verb', 'no source names', 'no em dashes'):
            self.assertIn(word, PMA.MEDIA_PLAN_GUIDANCE)
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        self.assertIn('pma.is_media_plan_ask(text)', src)
        self.assertIn('extra_blocks.append(pma.MEDIA_PLAN_GUIDANCE)', src)


class TheRetiredTurn(unittest.TestCase):
    """Re-asking the question on the thread runs it again: the promise line
    is not the earlier answer, and a retired read is never handed back."""

    THREAD = [
        {'role': 'user', 'ts': '2026-11-06T17:22:31.967Z', 'text': JENNA, 'turn_id': 'u1'},
        {'role': 'agent', 'ts': '2026-11-06T17:22:47.593Z', 'turn_id': 'a1',
         'text': ('On it: reading When People Start Christmas Holiday Toy Shopping and Potential Paw Patrol Consumers now. '
                  'This one takes a real look at the data, so give me a moment - the read will land right here when it is ready.')},
        {'role': 'agent', 'ts': '2026-11-06T17:33:21.000Z',
         'text': 'This read was retired. The read below is on the US audience.',
         'meta': {'kind': 'read', 'read_job_id': '7c623cbc5192', 'landed_at': '2026-11-06T17:33:21.019Z',
                  'corrected_at': '2026-11-06T18:33:45Z', 'retired': 'read again on the US audience', 'options': [], 'figures': []}},
    ]

    def test_the_promise_is_never_the_earlier_answer(self):
        from prometheus import smarter as SM
        self.assertIsNone(SM.thread_replay(JENNA, self.THREAD))
        self.assertIsNone(SM.thread_replay(JENNA, self.THREAD[:2]))

    def test_a_landed_read_still_replays_when_it_was_not_retired(self):
        from prometheus import smarter as SM
        landed = [dict(self.THREAD[0]), dict(self.THREAD[1]),
                  {'role': 'agent', 'ts': '2026-11-06T17:33:21.000Z',
                   'text': 'On potential Paw Patrol consumers in the US:\n\nThe median first holiday toy order moved earlier ...',
                   'meta': {'kind': 'read', 'read_job_id': 'abc123', 'landed_at': '2026-11-06T17:33:21.019Z'}}]
        hit = SM.thread_replay(JENNA, landed)
        self.assertTrue(hit and 'potential Paw Patrol consumers' in hit['reply'])
        self.assertNotIn('On it:', hit['reply'])


class TheReaderPrompt(unittest.TestCase):
    def test_the_reader_carries_the_rule(self):
        from prometheus import reader as R
        src = open(R.__file__, encoding='utf-8').read()
        self.assertIn('names its own universe', src)
        self.assertIn('never analyze_page, never refers_to_page', src)


if __name__ == '__main__':
    unittest.main(verbosity=1)
