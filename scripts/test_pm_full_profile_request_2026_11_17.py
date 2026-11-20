#!/usr/bin/env python3
"""A request for the full profile builds the profile (Jenna 2026-11-17).

A guest working in Jenna's seat asked, in one thread (a2673cecd7), for
an audience of luxury resort engagers: people with a touchpoint to
Auberge Resorts, Discovery Land Company, Aman Resorts or Nobu Hotels,
household income $250k or more. Every ask was answered with a short
read, and the last one with a list of finished reads:

  23:48 UTC  "Please pull a group who has engaged with ..."     -> a read
  00:06 UTC  "please provide me a full profile IQ on users ..." -> "I read
             'this profile' as 2 Chainz from earlier in this chat."
  00:08 UTC  "No pull a new profile IQ for users who ..."       -> a read
             ("no pull" was taken as "no pull wanted")
  00:45 UTC  "its not there you need to pull the full profile"  -> the
             status rundown ("Your read for ... finished 35 minutes ago")

Jenna: "it shoudl create that profile and tell them where to find it (in
profile iq)".

What is held here, each from the words as they were typed:

  1. The follow-up routes to the build lane on the thread's subject,
     never the status answer.                 -> TheFollowUpBuilds
  2. Its paraphrases do too.                  -> Paraphrases
  3. The asks that came before it are orders. -> TheOriginalAsks
  4. A plain status question keeps the status answer, and a count
     question stays a question.               -> WhatDidNotChange
  5. The brief names the Total Universe cleanly and the income
     condition rides as a cut.                -> TheBrief
  6. The build step drafts the order as one build: no read in its
     place, no profile per brand.             -> TheBuildStepKeepsTheOrder
  7. The finished line says where the profile is: Profile IQ, the
     Select Profile dropdown, its name.       -> TheReadyLine

Hermetic: the stub host and in-memory store of
test_pm_new_chat_no_bleed; helpers read out of app.py by AST. No model,
no S3, no app module, no mail.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
import unittest
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
from _pm_legacy_view import legacy_view as _legacy_view, chat_py_lines as _chat_py_lines  # noqa: E402  (stage 5: chat.py plus its extracted cores)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location('pm_no_bleed_scaffold_fp', os.path.join(HERE, 'test_pm_new_chat_no_bleed.py'))
NB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NB)

from prometheus import build_lane as BL  # noqa: E402
from prometheus import build_ready as BR  # noqa: E402
from prometheus import orders as ORD  # noqa: E402
from prometheus import reader as RD  # noqa: E402
from prometheus import smarter as SM  # noqa: E402
from prometheus import status_lane as STL  # noqa: E402

RD.read = lambda *a, **k: None          # no model: the routing under test is the words

World, USER, THREADS = NB.World, NB.USER, NB.THREADS
TID = 'a2673cecd7'
PAGE = {'view': 'profile-iq', 'primary': {'s3_key': '2_CHAINZ_06_28_2026_13_25.csv', 'name': '2 Chainz'},
        'view_context': {'view_id': 'profileIQ', 'view_title': '2 Chainz', 'data': {}}}

# the guest's words, as typed
Q_GROUP = ("Please pull a group who has engaged with Auberge Resorts, Discovery Land Company, Aman Resorts, and Nobu Hotels over "
           "the last 12 months... also make sure to only look at those who have a household income of 250k+. So dates are nov 17 "
           "2025- nov 17 2026")
Q_GROUP_LINES = "Please pull a group who has engaged with Auberge Resorts\nDiscovery Land Company\nAman Resorts\nNobu Hotels"
Q_FILE = "so is this file i downloaded everyone who has any touchpoint to any of the hotels and only people with 250k+?"
Q_PROVIDE = ("please provide me a full profile IQ on users with at least one digital touchpoint with Auberge Resorts or Discovery "
             "Land Company or Aman Resorts or Nobu Hotels in the window.... also only include users with 250k+ household income in "
             "this profile IQ... I want all their behaviors")
Q_NO_PULL = ("No pull a new profile IQ for users who have a touchpoint to any of these four resorts and who have a household income "
             "of 250k+... the date range is nov 17 2025 - nov 17 2026 and the touchpoints are any touchpoint to one of these Auberge "
             "Resorts or Discovery Land Company or Aman Resorts or Nobu Hotels")
Q_WHERE = "Confused where is all the behavioral data I want all of it just how we get it in Profile IQ"
Q_FOLLOW = "its not there you need to pull the full profile"
Q_ROWS = "the csv has only four rows"
Q_TOP = "but show me their top behaviors in shopping streaming and all"

READ_1 = ("On those who have a Household Income of 250k+ in the US:\n\n183,427 US people with income of $250,000 or more engaged "
          "with all four of Auberge Resorts, Discovery Land Company, Aman Resorts and Nobu Hotels between Nov 17 2025 and Nov 17 2026.")
READ_2 = ("On new Profile IQ for users:\n\n1,284,963 US people with income of $250,000 or more carried at least one digital touchpoint "
          "with Auberge Resorts, Discovery Land Company, Aman Resorts or Nobu Hotels in the window.")
SUBJ_2 = 'Luxury resort engagers (Auberge Resorts, Discovery Land Company, Aman Resorts, Nobu Hotels), household income $250k or'
ON_IT = 'On it: reading Nobu Hotels, Auberge Resorts, Discovery Land Company and more now. The read will land right here when it is ready.'
WAYFIND = ("Open the SELECT PRODUCT dropdown at the top right of the dashboard, click Profile IQ, then the Behavioral tab across the "
           "top of the page. Pick the audience in the Select Profile dropdown; the Data Cuts list under it holds the cuts of that audience.")
STATUS_RUNDOWN = "Your read for Luxury resort engagers"

THREAD = [
    {'role': 'user', 'text': Q_GROUP, 'ts': '2026-11-17T23:48:11.461Z'},
    {'role': 'agent', 'text': 'Included with your plan. ' + ON_IT, 'ts': '2026-11-17T23:48:28.063Z'},
    {'role': 'agent', 'text': READ_1, 'ts': '2026-11-17T23:50:06.000Z',
     'meta': {'kind': 'read', 'read_job_id': '3a4015366ad0', 'landed_at': '2026-11-17T23:50:06.977Z',
              'subject': 'Auberge Resorts, Discovery Land Company, Aman Resorts and Nobu Hotels cross-engagers, income $250k+'}},
    {'role': 'user', 'text': Q_FILE, 'ts': '2026-11-18T00:03:43.746Z'},
    {'role': 'agent', 'text': 'Short answer: no on the first part, yes on the second.', 'ts': '2026-11-18T00:04:20.406Z'},
    {'role': 'user', 'text': Q_PROVIDE, 'ts': '2026-11-18T00:06:15.254Z'},
    {'role': 'agent', 'text': "I read 'this profile' as 2 Chainz from earlier in this chat. Go, or name another?",
     'ts': '2026-11-18T00:06:36.524Z'},
    {'role': 'user', 'text': Q_NO_PULL, 'ts': '2026-11-18T00:08:18.836Z'},
    {'role': 'agent', 'text': ON_IT, 'ts': '2026-11-18T00:08:41.570Z'},
    {'role': 'agent', 'text': READ_2, 'ts': '2026-11-18T00:10:49.000Z',
     'meta': {'kind': 'read', 'read_job_id': '8be6ff905665', 'landed_at': '2026-11-18T00:10:49.018Z', 'subject': SUBJ_2}},
    {'role': 'user', 'text': Q_WHERE, 'ts': '2026-11-18T00:42:52.861Z'},
    {'role': 'agent', 'text': WAYFIND, 'ts': '2026-11-18T00:42:54.794Z'},
]
# a thread that holds a read and no order: a question, its read, nothing else
Q_ASKED = 'What do luxury resort engagers with household income of $250k or more buy?'
READ_ONLY_THREAD = [
    {'role': 'user', 'text': Q_ASKED, 'ts': '2026-11-18T00:08:18.836Z'},
    {'role': 'agent', 'text': ON_IT, 'ts': '2026-11-18T00:08:41.570Z'},
    {'role': 'agent', 'text': READ_2.replace('On new Profile IQ for users:', 'On Luxury resort engagers, income $250k or more:'),
     'ts': '2026-11-18T00:10:49.000Z',
     'meta': {'kind': 'read', 'read_job_id': '8be6ff905665', 'landed_at': '2026-11-18T00:10:49.018Z',
              'subject': 'Luxury resort engagers, income $250k or more'}},
]
BANNED = ('synth', 'modeled', 'estimated', 'pipeline', 'queue', 'worker', 'hostmap', 'claude', '\u2014', '\u2013')


def _before(words, thread=THREAD):
    for i, t in enumerate(thread):
        if t.get('role') == 'user' and t.get('text') == words:
            return thread[:i]
    return list(thread)


class _Asks(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.w.new_thread(TID)

    def ask(self, words, history, *, page=PAGE, surface='analyze'):
        self.w.put(f'{THREADS}/{USER}/{TID}.json', history)
        self.w.hints.clear()
        env, _st, _seen = self.w.ask(words, thread_id=TID, history=history, page=page, surface=surface)
        return env, list(self.w.calls), list(self.w.hints)

    def routes(self, hints):
        return [str(h.get('route') or '') for h in hints]

    def assertBuildFlow(self, words, history, *, text_has=(), no_auto_start=None):
        """With a page open the widget sits on the answer surface: the ask is
        handed to its build flow (nothing is read), and the build step then
        gets the order."""
        env, calls, hints = self.ask(words, history, surface='analyze')
        self.assertEqual((env.get('decision') or {}).get('reason'), 'handoff_to_build_flow', words)
        self.assertEqual(calls, [], words)                                   # no read was started
        self.assertNotIn('status_lookup', self.routes(hints), words)
        env2, calls2, hints2 = self.ask(words, history, surface='interpret')
        self.assertEqual([c[0] for c in calls2], ['interpret'], words)
        self.assertNotIn('status_lookup', self.routes(hints2), words)
        sent = calls2[0][1]
        for part in text_has:
            self.assertIn(part.lower(), sent.lower(), words)
        self.assertTrue(ORD.is_order(sent), sent)
        if no_auto_start is not None:
            self.assertEqual(bool(calls2[0][2].get('_pm_no_auto_start')), no_auto_start, words)
        # and with no page open the same words go straight to the build step
        env3, calls3, _h3 = self.ask(words, history, page=None, surface=None)
        self.assertEqual([c[0] for c in calls3], ['interpret'], words)
        return sent


class TheFollowUpBuilds(_Asks):
    """00:45 UTC: 'its not there you need to pull the full profile'."""

    def test_it_is_the_order_for_the_thread_subject_not_the_status_answer(self):
        sent = self.assertBuildFlow(Q_FOLLOW, THREAD, text_has=(
            'pull a new profile IQ for users who have a touchpoint to any of these four resorts',
            'household income of 250k+', 'nov 17 2025 - nov 17 2026',
            'Auberge Resorts or Discovery Land Company or Aman Resorts or Nobu Hotels'), no_auto_start=False)
        self.assertFalse(sent.lower().startswith('no '))                     # the no answered the reply before it
        self.assertTrue(ORD.is_profile_order(sent))
        self.assertFalse(SM.negated_build(sent))

    def test_the_lane_is_named_on_the_log_and_the_reply_is_not_the_rundown(self):
        env, calls, hints = self.ask(Q_FOLLOW, THREAD)
        self.assertIn({'route': 'build_order', 'outcome': 'lane_full_profile'}, hints)
        self.assertNotIn(STATUS_RUNDOWN, str(env))
        self.assertNotIn('finished', str(env.get('text') or ''))

    def test_the_words_alone(self):
        self.assertEqual(BL.kind(Q_FOLLOW, THREAD), 'full_profile')
        out = BL.rewrite(Q_FOLLOW, THREAD, PAGE)
        self.assertEqual(out['source'], 'thread_order')
        self.assertNotIn('confirm', out)
        self.assertNotIn('2 Chainz', out['text'])                            # never the profile that happens to be open

    def test_a_thread_with_a_read_and_no_order_drafts_on_the_read_subject_and_waits(self):
        sent = self.assertBuildFlow(Q_FOLLOW, READ_ONLY_THREAD, text_has=('Luxury resort engagers', 'income $250k'),
                                    no_auto_start=True)
        self.assertTrue(sent.startswith('Please queue one Profile IQ: '))
        out = BL.rewrite(Q_FOLLOW, READ_ONLY_THREAD, PAGE)
        self.assertEqual((out['source'], out.get('confirm')), ('thread_read', True))

    def test_a_read_on_something_else_after_the_order_moves_the_subject(self):
        moved = THREAD + [
            {'role': 'user', 'text': 'Who watches Landman, and what do they buy?', 'ts': '2026-11-18T00:50:00.000Z'},
            {'role': 'agent', 'text': 'On people who watched a Landman title in the window:\n\n6,184,372 US viewers, half of them 35 to 54.',
             'ts': '2026-11-18T00:53:00.000Z', 'meta': {'kind': 'read', 'read_job_id': 'dcaec0862d75', 'subject': 'Landman'}}]
        out = BL.rewrite(Q_FOLLOW, moved, PAGE)
        self.assertEqual(out['source'], 'thread_read')
        self.assertIn('Landman', out['text'])
        self.assertTrue(out.get('confirm'))
        # a short remark on the read just delivered names nothing new: the order stands
        same = THREAD + [
            {'role': 'user', 'text': Q_ROWS, 'ts': '2026-11-18T00:51:13.543Z'},
            {'role': 'agent', 'text': 'On Luxury resort engagers, income $250k or more:\n\nThe four rows you have are the audience header '
                                      'of the read; the base is 1,284,963 US people.', 'ts': '2026-11-18T00:52:43.000Z',
             'meta': {'kind': 'read', 'read_job_id': 'e1182e341580', 'subject': 'Luxury resort engagers, income $250k or more'}}]
        self.assertEqual(BL.rewrite(Q_FOLLOW, same, PAGE)['source'], 'thread_order')

    def test_nothing_to_go_on_asks_for_the_subject_in_one_line(self):
        self.assertEqual(BL.rewrite(Q_FOLLOW, [], None), {'ask': BL.ASK_LINE, 'kind': 'full_profile'})
        for bad in BANNED:
            self.assertNotIn(bad, BL.ASK_LINE.lower())


class Paraphrases(_Asks):
    WORDS = ('pull the full profile', 'Pull the full profile please', 'I need the full profile', 'build the profile',
             'you need to pull the full profile', 'its not there. Pull the full profile', "It's not there, I want the full profile",
             'no you need to run the full profile iq', 'create the full profile', 'please build the full Profile IQ profile',
             'I need the whole profile', 'make me a full profile')

    def test_each_is_the_order_on_the_thread_subject(self):
        for words in self.WORDS:
            self.assertEqual(BL.kind(words, THREAD), 'full_profile', words)
            out = BL.rewrite(words, THREAD, PAGE)
            self.assertTrue(out and out.get('text'), words)
            self.assertIn('touchpoint to any of these four resorts', out['text'], words)
            self.assertFalse(STL.wants_status_fast(out['text']), words)

    def test_through_the_service(self):
        for words in self.WORDS + ("I don't see it, pull the full profile", 'it is not showing, build the full profile iq'):
            self.assertBuildFlow(words, THREAD, text_has=('touchpoint to any of these four resorts', '250k+'))

    def test_a_pointing_word_in_the_order_is_the_thread_read(self):
        # "for this" makes it an order the order gate already knows; its referent resolves to the read above
        for words in ('I need the full profile for this', 'pull a full profile on this audience', 'build a profile of them'):
            self.assertTrue(ORD.is_profile_order(words), words)
            self.assertTrue(ORD.referent(words), words)
            sent = self.assertBuildFlow(words, THREAD, text_has=('Luxury resort engagers', 'Auberge Resorts', 'Nobu Hotels'))
            self.assertTrue(sent.startswith('Please queue one Profile IQ: '), sent)
            self.assertNotIn('2 Chainz', sent)

    def test_asked_as_a_question_it_is_a_brief_that_waits(self):
        for words in ('its not there, can you pull the full profile?', 'could you build the full profile?'):
            out = BL.rewrite(words, THREAD, PAGE)
            self.assertEqual((out['kind'], out.get('confirm')), ('full_profile', True), words)
        self.assertBuildFlow('its not there, can you pull the full profile?', THREAD, text_has=('four resorts',), no_auto_start=True)

    def test_a_profile_named_in_the_words_is_built_as_named(self):
        named = ('create a profile of luxury resort engagers who engaged with Auberge Resorts, Discovery Land Company, Aman Resorts '
                 'or Nobu Hotels, household income $250k+')
        self.assertTrue(ORD.is_profile_order(named))
        self.assertEqual(BL.kind(named, THREAD), '')                         # the order gate has it; nothing to rewrite
        sent = self.assertBuildFlow(named, [], text_has=('luxury resort engagers', 'Nobu Hotels', '$250k+'), no_auto_start=False)
        self.assertEqual(sent, named)
        out = BL.rewrite('you need to build the full profile of Aman Resorts guests', THREAD, PAGE)
        self.assertEqual((out['source'], out['text']), ('words', 'Please queue one Profile IQ: Aman Resorts guests'))


class TheOriginalAsks(_Asks):
    """The asks that came before the follow-up were orders for the profile."""

    def test_pull_a_group_who_has_engaged_with(self):
        for words in (Q_GROUP, Q_GROUP_LINES):
            self.assertTrue(ORD.is_profile_order(words), words)
            self.assertTrue(ORD.defined_group(words), words)
            self.assertEqual(ORD.referent(words), '', words)
        # the brief is shown for the reader's yes: the words name an audience, not a product
        self.assertBuildFlow(Q_GROUP, [], text_has=('Auberge Resorts', 'household income of 250k+'), no_auto_start=True)

    def test_provide_me_a_full_profile_iq_on(self):
        self.assertTrue(ORD.is_profile_order(Q_PROVIDE))
        self.assertFalse(ORD.defined_group(Q_PROVIDE))
        self.assertEqual(ORD.referent(Q_PROVIDE), '')                        # 'this profile IQ' is the one being ordered, never the page
        sent = self.assertBuildFlow(Q_PROVIDE, _before(Q_PROVIDE), text_has=('Auberge Resorts or Discovery Land Company',),
                                    no_auto_start=False)
        self.assertNotIn('2 Chainz', sent)

    def test_no_pull_a_new_profile_iq(self):
        self.assertTrue(ORD.is_profile_order(Q_NO_PULL))
        self.assertFalse(SM.negated_build(Q_NO_PULL))
        env, calls, hints = self.ask(Q_NO_PULL, _before(Q_NO_PULL))
        self.assertNotEqual((env.get('decision') or {}).get('reason'), 'negated_build')
        self.assertBuildFlow(Q_NO_PULL, _before(Q_NO_PULL), text_has=('any of these four resorts',), no_auto_start=False)

    def test_a_build_that_is_forbidden_is_still_forbidden(self):
        for words in ("Don't build a new profile. Answer this from the existing ones: what share of them are women?",
                      'no new profile, just tell me the share of women', 'No build needed, answer from the existing profile',
                      'not a new pull, use what we have', 'do not pull a profile for this',
                      'no pull, just answer from the existing data'):
            self.assertTrue(SM.negated_build(words), words)
        self.assertFalse(SM.negated_build('No, run a profile on Mountain Dew instead'))
        self.assertFalse(SM.negated_build('Nope pull the Nike profile for me'))

    def test_the_asks_that_were_questions_stay_questions(self):
        for words in (Q_FILE, Q_ROWS, Q_TOP):
            self.assertFalse(ORD.is_order(words), words)
            self.assertEqual(BL.kind(words, _before(words)), '', words)
            env, calls, hints = self.ask(words, _before(words))
            self.assertEqual([c[0] for c in calls], ['analyze'], words)
        self.assertFalse(ORD.is_order(Q_WHERE))
        self.assertEqual(BL.kind(Q_WHERE, _before(Q_WHERE)), '')


class WhatDidNotChange(_Asks):
    STATUS = ('where is my read', 'where is my read?', 'is it done', 'is it done?', 'its not there', "it's not there yet",
              'still not there', "I don't see it", 'did it finish?', 'where is it', 'any update', 'status')

    def test_a_plain_status_question_gets_the_status_answer(self):
        for words in self.STATUS:
            self.assertEqual(BL.kind(words, THREAD), '', words)
            self.assertIsNone(BL.rewrite(words, THREAD, PAGE), words)
            self.assertFalse(ORD.is_order(words), words)
            self.assertTrue(STL.wants_status_fast(words), words)
        for words in ('where is my read', 'is it done', 'its not there'):
            env, calls, hints = self.ask(words, THREAD)
            self.assertEqual((env.get('decision') or {}).get('reason'), 'status_lookup', words)
            self.assertEqual(calls, [], words)
            self.assertIn('status_lookup', self.routes(hints), words)

    def test_a_question_about_the_profile_is_not_an_order_for_it(self):
        for words in ('did you pull the full profile?', 'where is the full profile', 'is the full profile ready?',
                      'when will you build the profile?', 'why did you not pull the full profile?', "don't pull the full profile",
                      'how long does it take to build a full profile?', 'open the profile', 'pull up the profile',
                      'send me the profile csv', 'email me the full profile file', 'make the profile picture bigger',
                      'build a profile', 'where is the profile tab'):
            self.assertEqual(BL.kind(words, THREAD), '', words)

    def test_a_count_question_is_not_an_order(self):
        # the next-door rule (2026-11-17): a count of viewers is a read, never a build
        for words in ('I need the Total Viewers to FBI Most Wanted and FBI International, All Seasons on Peacock. The window is '
                      'Nov 2025-Nov 2026. Need the viewers for each series reported independently.',
                      'provide the viewers for FBI Most Wanted on Peacock', 'pull the viewers to Landman on Paramount+',
                      'how many people watched Landman', 'give me the viewer count for Landman'):
            self.assertFalse(ORD.is_order(words), words)
            self.assertEqual(BL.kind(words, []), '', words)
        try:
            from prometheus import understand as U
            looks = getattr(U, 'looks_count_read', None)
        except Exception:
            looks = None
        if looks:                                                            # once the count rule has landed: both hold together
            self.assertTrue(looks('I need the Total Viewers to FBI Most Wanted and FBI International, All Seasons on Peacock.'))
            for words in (Q_GROUP, Q_PROVIDE, Q_NO_PULL):
                self.assertFalse(looks(words), words)

    def test_group_and_provide_did_not_widen_past_their_shapes(self):
        for words in ('Break this group out by DMA', 'run the group against Netflix', 'Cut the four-brand group to women',
                      'Provide the age split as a CSV', 'provide a breakdown by region', 'please provide the numbers',
                      'pull the group', 'show me this group by age and region'):
            self.assertFalse(ORD.is_profile_order(words), words)
        self.assertEqual(ORD.product_of('open a focus group who love Landman'), 'twin')
        for words in ('provide me a profile of Nike shoppers', 'please provide us with a full profile on Aman Resorts',
                      'provide a full profile iq on Chipotle', 'build a group of people who bought a Peloton and use Strava',
                      'create a group that watches both Landman and Yellowstone'):
            self.assertTrue(ORD.is_profile_order(words), words)


def _app_helpers():
    src = Path(ROOT, 'app.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    funcs = {'_embedded_income_floor', '_decompose_embedded_subject_cuts', '_normalize_cut_items', '_merge_cuts',
             '_v1_persona_income_cut_from_text', '_is_single_compound_audience', '_is_explicit_combined_request'}
    names = {'_V1_INCOME_BUCKETS', '_ADDON_AGE_BUCKET_SPANS', '_ADDON_AGE_BUCKETS', '_ADDON_CUT_CATALOG', 'ADDON_CUT_CREDITS',
             '_GENERIC_AUDIENCE_NOUN_RE'}
    pieces = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in funcs:
            pieces.append(ast.get_source_segment(src, node))
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            pieces.append(ast.get_source_segment(src, node))
    ns = {'_nielsen_dma_list': lambda: [], '_extract_geo_cut_from_text': lambda t: None,
          '_strip_geo_qualifier_span': lambda t, g: t, '_normalize_for_match': lambda s: str(s).lower(),
          '_pm_universe_phrase': lambda d: 'total universe'}
    exec('\n\n'.join(pieces), ns)                                            # noqa: S102 - the app's own helpers, read by AST
    return ns


class TheBrief(unittest.TestCase):
    """The Total Universe is named for the audience only; income $250k or
    more is a cut of it ('<Subject> - $250K+ Income'), never the universe."""

    ASK = Q_NO_PULL[len('No '):]

    @classmethod
    def setUpClass(cls):
        cls.app = _app_helpers()
        cls.decompose = staticmethod(cls.app['_decompose_embedded_subject_cuts'])
        cls.floor = staticmethod(cls.app['_embedded_income_floor'])

    def _draft(self, subject, decision='new_build'):
        return {'subject': subject, 'file_stem': re.sub(r'\W+', '_', subject), 'decision': decision, 'addon_cuts': [],
                'estimated_credits': 5, 'assumptions': [], 'run_avid': True}

    def assertIncomeCut(self, draft, subject, noted=True):
        self.assertEqual(draft['subject'], subject)
        for bad in ('$', '250', 'HHI', 'Income', 'income'):
            self.assertNotIn(bad, draft['subject'])
            self.assertNotIn(bad, draft['file_stem'])
        cuts = draft['addon_cuts']
        self.assertEqual(len(cuts), 1)
        c = cuts[0]
        self.assertEqual((c['kind'], c['pin_category'], c['pin_buckets'], c['name_label']),
                         ('income_band', 'INCOME', ['$250,000 or More'], '$250K+ Income'))
        self.assertEqual(f"{draft['subject']} - {c['name_label']}", f"{subject} - $250K+ Income")
        self.assertEqual(draft['estimated_credits'], 5 + 3)                   # the standing add-on cut price, unchanged
        note = ' '.join(draft['assumptions'])
        if noted:
            self.assertIn(subject, note)
            self.assertIn('$250K+ Income', note)
        for bad in BANNED:
            self.assertNotIn(bad, note.lower())

    def test_the_label_the_draft_came_back_with(self):
        # what the build step drafted for these words on 2026-11-18, before this change
        d = self._draft('Ultra-Luxury Resort Touchpoints $250K+ HHI')
        self.decompose(d, self.ASK)
        self.assertIncomeCut(d, 'Ultra-Luxury Resort Touchpoints')
        self.assertEqual(d['file_stem'], 'Ultra-Luxury_Resort_Touchpoints')

    def test_other_ways_the_label_can_carry_it(self):
        for label in ('Luxury Resort Engagers with Household Income $250K+', 'Luxury Resort Engagers ($250K+ HHI)',
                      'Luxury Resort Engagers, HHI $250,000 or more', 'Luxury Resort Engagers $250K+'):
            d = self._draft(label)
            self.decompose(d, self.ASK)
            self.assertIncomeCut(d, 'Luxury Resort Engagers')

    def test_a_clean_label_still_gets_the_cut_from_the_ask(self):
        d = self._draft('Luxury Resort Engagers')
        self.decompose(d, self.ASK)
        self.assertIncomeCut(d, 'Luxury Resort Engagers')
        self.assertTrue(d['addon_cuts'][0].get('from_prompt_qualifier'))
        for ask in (Q_GROUP, Q_PROVIDE, Q_NO_PULL):
            d = self._draft('Luxury Resort Engagers')
            self.decompose(d, ask)
            self.assertIncomeCut(d, 'Luxury Resort Engagers')

    def test_a_cut_the_draft_already_carries_is_not_doubled(self):
        d = self._draft('Luxury Resort Engagers')
        d['addon_cuts'] = [{'type': 'income_band', 'floor': 250000}]      # the draft step emitted it itself
        self.decompose(d, self.ASK)
        self.assertIncomeCut(d, 'Luxury Resort Engagers', noted=False)

    def test_what_is_not_an_income_floor(self):
        for text in ('Adults 55+', 'People who spent $100+ on Nike', 'Luxury Resort Engagers', 'Top 100 brands', '18-24', '35+'):
            self.assertEqual(self.floor(text, require_word=False), (None, None), text)
        self.assertEqual(self.floor('Nike shoppers $100K+', require_word=True), (None, None))     # an ask needs the income word
        self.assertEqual(self.floor('who have a household income of 250k+', require_word=True)[0], 250000)
        self.assertEqual(self.floor('with household income of $250,000 or more')[0], 250000)
        self.assertEqual(self.floor('$75K+ HHI')[0], 75000)
        # a note on what the file should carry is not a qualifier; a match on an existing profile is left as it is
        d = self._draft('Nike')
        self.decompose(d, 'Please queue one Profile IQ: Nike shoppers. Include the share with household income of $100k or more.')
        self.assertEqual(d['addon_cuts'], [])
        d = self._draft('Nike', decision='existing_match')
        self.decompose(d, 'Nike shoppers with household income of $100k+')
        self.assertEqual((d['subject'], d['addon_cuts']), ('Nike', []))
        d = self._draft('Taylor Swift')
        self.decompose(d, 'audience of Taylor Swift fans')
        self.assertEqual((d['subject'], d['addon_cuts'], d['estimated_credits']), ('Taylor Swift', [], 5))

    def test_the_rulebook_says_it_and_an_income_ask_reads_that_part(self):
        chat = _legacy_view()
        a = chat.index('"SUBJECT NAMING + EMBEDDED CUTS (MANDATORY):')
        rule = chat[a:chat.index('"ANY-OF / ALL-OF TITLE LISTS', a)]
        self.assertIn('income floor', rule)
        self.assertIn('all incomes', rule)
        self.assertIn('INCOME to one tier', rule)
        self.assertIn('{\\"type\\": \\"income_band\\", \\"floor\\": 250000}', chat)
        self.assertNotIn('\u2014', rule)
        self.assertNotIn('\u2013', rule)
        # that rule sits in the section the rulebook diet drops for an ask with no gender word; an income
        # condition keeps it, and keeps the add-on cut field in the answer shape
        keep = SM.PRUNABLE['GENDER SKEW VOCABULARY']
        for ask in (self.ASK, Q_GROUP, Q_PROVIDE, 'Hilton guests, $100K+ HHI', 'Nike shoppers with income over $150,000'):
            self.assertTrue(keep(ask, None), ask)
            self.assertNotIn('addon_cuts', SM._schema_fields_to_drop(ask, None, None, [], []), ask)
        self.assertFalse(keep('audience of Taylor Swift fans', None))
        self.assertIn('addon_cuts', SM._schema_fields_to_drop('audience of Taylor Swift fans', None, None, [], []))
        self.assertFalse(SM.INCOME_QUALIFIER_RX.search('run a profile on Nike for the last 12 months'))
        self.assertFalse(SM.INCOME_QUALIFIER_RX.search('people who spent $100+ on Nike'))


class TheBuildStepKeepsTheOrder(unittest.TestCase):
    """Once the order reaches the build step it is drafted as one build:
    not sent to a read by that step's own router, not split into one
    profile per brand."""

    @classmethod
    def setUpClass(cls):
        cls.single = staticmethod(_app_helpers()['_is_single_compound_audience'])

    def test_an_order_is_not_deflected_to_a_read(self):
        hints = []
        body = {'_pm_order': True, '_pm_order_text': Q_GROUP}
        self.assertEqual(BL.keep_order_route('generate', body, Q_GROUP, hint=lambda **k: hints.append(k)), 'build_interpret')
        self.assertEqual(hints, [{'route': 'build_order', 'outcome': 'kept_from_read'}])
        # a question that reached the build step is still answered as a read; every other route is left alone
        self.assertEqual(BL.keep_order_route('generate', {}, 'what do Landman viewers buy?'), 'generate')
        self.assertEqual(BL.keep_order_route('generate', {'_pm_order': True, '_pm_order_text': 'what do they buy?'}, 'x'), 'generate')
        for route in ('build_interpret', 'not_quantifiable', 'csv_download', 'search_demand', 'subiq', 'clarify', ''):
            self.assertEqual(BL.keep_order_route(route, body, Q_GROUP), route)
        self.assertEqual(BL.keep_order_route('generate', None, None), 'generate')

    def test_the_build_step_calls_it_and_its_file_did_not_grow(self):
        chat = _legacy_view()
        self.assertIn(".keep_order_route(str(_route_d.get('route') or ''), body, text, hint=_pm_ask_hint)", chat)
        self.assertLess(len(chat.splitlines()), 19900)                       # the line ratchet holds

    def test_the_brands_that_define_one_audience_are_one_build(self):
        named = ('create a profile of luxury resort engagers who engaged with Auberge Resorts, Discovery Land Company, Aman Resorts '
                 'or Nobu Hotels, household income $250k+')
        for words in (Q_GROUP, Q_GROUP_LINES, Q_PROVIDE, Q_NO_PULL, Q_NO_PULL[len('No '):], named,
                      'Please pull a group who has engaged with Auberge Resorts, Discovery Land Company, Aman Resorts, and Nobu Hotels'):
            self.assertTrue(self.single(words), words)
        # a list of separate subjects still fans out
        for words in ('Nike, Adidas, Puma', 'run profiles on VIZIO, Samsung, LG owners',
                      'run individual profiles for Nike, Adidas and Puma', 'build a group of profiles for Nike, Adidas, Puma'):
            self.assertFalse(self.single(words), words)


class TheReadyLine(unittest.TestCase):
    NAME = 'Luxury Resort Engagers'

    def test_the_line_in_the_thread_says_where(self):
        line = BR.ready_text(self.NAME)
        self.assertEqual(line, f"{self.NAME} is ready. It is in Profile IQ: open the Select Profile dropdown and pick {self.NAME}.")
        for part in ('Profile IQ', 'Select Profile', self.NAME):
            self.assertIn(part, line)
        for bad in BANNED:
            self.assertNotIn(bad, line.lower())
        turn = BR.ready_turn('RUN123', self.NAME)
        self.assertEqual(turn['text'], line)
        self.assertEqual(turn['meta']['options'], [{'label': f"Open {self.NAME}", 'send': f"Open {self.NAME}"}])
        self.assertEqual(turn['meta']['kind'], 'build_ready')
        # a Subscriber IQ build keeps its own line
        self.assertIn('Subscriber IQ tab', BR.ready_text(self.NAME, {'subiq_s3_key': 'x'}))

    def test_it_is_posted_once_into_the_launching_thread(self):
        threads = {('admin', TID): list(THREAD)}
        launch = BR.note_launch(lambda k, d: None, run_id='RUN123', user='admin', thread_id=TID, subject=self.NAME,
                                decision='new_build')
        doc = {'run_id': 'RUN123', 'status': 'complete', 'subject': self.NAME}
        args = dict(exists=lambda u, t: (u, t) in threads, load=lambda u, t: threads[(u, t)],
                    save=lambda u, t, turns: threads.update({(u, t): turns}))
        self.assertEqual(BR.post_ready(launch, doc, **args), 'posted')
        self.assertIn('It is in Profile IQ', threads[('admin', TID)][-1]['text'])
        self.assertEqual(BR.post_ready(launch, doc, **args), 'already')
        # the line an open tab draws for the same build is still recognized, old wording and new
        for tab in (f"{self.NAME} is finished and ready in the Select Profile dropdown.",
                    f"{self.NAME} is finished and ready in the Select Profile dropdown in Profile IQ."):
            self.assertTrue(BR._has_ready([{'role': 'agent', 'text': tab}], 'OTHER', self.NAME), tab)
            self.assertTrue(BL._BUILD_LINE_RX.search(tab), tab)

    def test_the_open_tab_says_profile_iq_too(self):
        js = Path(ROOT, 'templates', 'index.html').read_text(encoding='utf-8')
        self.assertEqual(js.count("' is finished and ready in the Select Profile dropdown in Profile IQ.'"), 2)
        self.assertNotIn("' is finished and ready in the Select Profile dropdown.'", js)

    def test_the_line_an_order_reads_when_it_starts(self):
        line = ORD.build_line(self.NAME, 'Nov 17, 2025 to Nov 17, 2026', avid=True, included=True)
        self.assertIn(f"Profile building: {self.NAME}", line)
        self.assertIn('Select Profile dropdown', line)
        self.assertIn('I will email you when it is complete', line)
        for bad in BANNED:
            self.assertNotIn(bad, line.lower())


if __name__ == '__main__':
    unittest.main(verbosity=1)
