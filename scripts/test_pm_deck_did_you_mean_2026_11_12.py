#!/usr/bin/env python3
"""A deck request on a profile we hold builds the deck (Jenna 2026-11-12:
"this cant happen").

Armine Meseldzhyan (Lionsgate), 21:20 UTC on 2026-11-12, with Mistletoe
Murders Viewers open on her screen and in the library: "summary deck for
mistletoe murders series viewers". The record of that ask
(system/usage/ask_log/2026-11-12/212006_9a72a0c813.json) reads surface
deck, outcome empty, gate held, and the reply on file is the calm line
("I did not land a clean answer on that one ..."). A two-hour promise
was opened over it (system/ops/held_replies/2026-11-12/
212006_38a0ec2727.json). The same reader drew the same record on
2026-10-31 ("can we build a deck on the demographics of the profile
wicked tuna viewers").

Two causes, both held here:

  1. The deck step's did-you-mean rode as a block
     ({'clarify': {'question', 'suggestions'}}) with no sentence of its
     own. The reply classifier read only sentences, so a question the
     page was drawing with its choices was "nothing the user could
     read": empty, held by the answer gate, logged and copied to the
     team as the calm line, and promised by email.
                                          -> TheDidYouMeanIsAQuestion
  2. "mistletoe murders series viewers" was read as a new subject,
     "Mistletoe Murders Series Viewers", because of the word "series",
     so the profile on her screen was not found and the did-you-mean
     was asked at all.            -> ATitleWithSeriesIsTheSameTitle

And one thing found on the way: the choice she tapped on the
did-you-mean was answered with a second question ("Do you want this on
Mistletoe Murders Viewers (open on your screen)?").
                                              -> AChoiceTappedIsTheAnswer

TheRealDoor drives her exact words and their close variants through the
real routes with the replay harness (the parent repo's
scripts/_pm_behavior.py) and skips when bg-webapp is checked out alone.

Hermetic: pure functions, the chat module read as source, and one
isolated child with a fake store. No model, no S3, no mail.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import flask  # noqa: E402

import prometheus_analysis as PMA  # noqa: E402
import prometheus_watch_notify as PWN  # noqa: E402
from prometheus import ask_outcome as AO  # noqa: E402
from prometheus import deck_pick as DP  # noqa: E402
from prometheus import deck_subject as DS  # noqa: E402
from prometheus import envelope as ENV  # noqa: E402
from prometheus import understand as UN  # noqa: E402

CHAT_SRC = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')

HER_ASK = 'summary deck for mistletoe murders series viewers'
CALM = 'I did not land a clean answer on that one'
TU_KEY = 'Mistletoe_Murders_Viewers_11_11_2026_22_26.csv'
AVID_KEY = 'Mistletoe Murders Viewers - Avid Fan.csv'
# the library rows as they stood (system/s3_cache.json, 2026-11-12)
CATALOG = [
    {'s3_key': TU_KEY, 'display_name': 'Mistletoe Murders Viewers', 'category': 'SERIES', 'subject': 'Mistletoe Murders Viewers'},
    {'s3_key': AVID_KEY, 'display_name': 'Mistletoe Murders Viewers - Avid Fan', 'category': 'SERIES',
     'subject': 'Mistletoe Murders Viewers - Avid Fan'},
    {'s3_key': 'Cruel_Summer_Viewers_11_11_2026_02_14.csv', 'display_name': 'Cruel Summer Viewers', 'category': 'SERIES',
     'subject': 'Cruel Summer Viewers'},
    {'s3_key': 'WICKED_TUNA_Viewers_10_28_2026_15_11.csv', 'display_name': 'Wicked TUNA Viewers', 'category': 'SERIES',
     'subject': 'Wicked TUNA Viewers'},
    {'s3_key': 'The_Rainmaker_TV_Series_10_26_2026_18_02.csv', 'display_name': 'The Rainmaker TV Series', 'category': 'SERIES',
     'subject': 'The Rainmaker TV Series'},
]
PAGE = {'primary': {'s3_key': TU_KEY, 'name': 'Mistletoe Murders Viewers'}, 'cuts': [], 'extras': [],
        'view_context': {'view_id': 'profileIQ', 'view_title': 'Mistletoe Murders Viewers', 'data': {}}}
OTHER = {'primary': {'s3_key': 'Cruel_Summer_Viewers_11_11_2026_02_14.csv', 'name': 'Cruel Summer Viewers'}, 'cuts': [], 'extras': [],
         'view_context': {'view_id': 'profileIQ', 'view_title': 'Cruel Summer Viewers', 'data': {}}}

# the did-you-mean exactly as the deck step sent it at 21:20:06 UTC (her thread 174b64903b keeps the question and its chips)
QUESTION = 'I could not find an exact match for "Mistletoe Murders Series Viewers". Did you mean one of these?'
SUGGESTIONS = [
    {'kind': 'set', 'label': 'Mistletoe Murders Viewers (all 2 profiles)', 'subtitle': '1 total + 1 Avid',
     'bind': {'primary': {'s3_key': TU_KEY, 'name': 'Mistletoe Murders Viewers'},
              'cuts': [{'s3_key': AVID_KEY, 'name': 'Mistletoe Murders Viewers - Avid Fan'}], 'extras': []}},
    {'kind': 'profile', 'label': 'Mistletoe Murders Viewers', 'subtitle': '',
     'bind': {'primary': {'s3_key': TU_KEY, 'name': 'Mistletoe Murders Viewers'},
              'cuts': [{'s3_key': AVID_KEY, 'name': 'Mistletoe Murders Viewers - Avid Fan'}], 'extras': []}},
    {'kind': 'profile', 'label': 'Mistletoe Murders Viewers - Avid Fan', 'subtitle': '',
     'bind': {'primary': {'s3_key': AVID_KEY, 'name': 'Mistletoe Murders Viewers - Avid Fan'}, 'cuts': [], 'extras': []}},
]
AS_SENT = {'success': True, 'clarify': {'question': QUESTION, 'suggestions': SUGGESTIONS}}     # the shape on 2026-11-12
BANNED = re.compile(r"\b(?:synth\w*|model(?:ed|led)?|estimat\w*|pipeline|hostmap|ledger|bucket|panel|queue|worker|lane|"
                    r"claude|anthropic|llm|prompt)\b|\u2014|\u2013", re.I)


def _chips(suggestions):
    """The options the page stores on the did-you-mean turn (_pmStartDeck)."""
    return [{'label': s['label'] + ('  (' + s['subtitle'] + ')' if s.get('subtitle') else ''), 'send': s['label'],
             'deckBind': s.get('bind'), 'deckKind': s.get('kind')} for s in suggestions]


def _thread_after(label):
    """Her thread at the moment a chip is tapped: the ask, the question with its chips, the chip's label."""
    return [{'role': 'user', 'text': HER_ASK, 'turn_id': 'u1'},
            {'role': 'agent', 'text': QUESTION, 'turn_id': 'a1', 'meta': {'options': _chips(SUGGESTIONS)}},
            {'role': 'user', 'text': label, 'turn_id': 'u2'}]


# ============================================================================ 1
class TheDidYouMeanIsAQuestion(unittest.TestCase):
    """A reply that asks the reader to pick is a clarify, never an empty reply."""

    def test_the_reply_as_it_was_sent_reads_as_its_question(self):
        self.assertEqual(AO.reply_text(AS_SENT), QUESTION)
        outcome, _detail = AO.classify('deck', AS_SENT, 200, [], 'clarified')
        self.assertEqual(outcome, 'clarified')
        self.assertNotIn(outcome, AO.ALERT_OUTCOMES)          # no alert, no hold, no promise

    def test_every_shape_of_the_question_is_a_clarify(self):
        older = {'success': True, 'clarify': {'question': 'Which profile should the deck cover?',
                                              'options': ['Build the Mistletoe Murders Viewers insights deck',
                                                          'Build the Cruel Summer Viewers insights deck']}}
        closest = {'success': True, 'clarify': {'question': 'Here are the closest profiles in the library. Which should the deck cover?',
                                                'suggestions': SUGGESTIONS}}
        no_sentence = {'success': True, 'clarify': {'suggestions': SUGGESTIONS}}
        for payload in (AS_SENT, older, closest, no_sentence, DS.choice_payload(AS_SENT['clarify'])):
            for base in ('clarified', 'answered', None):
                outcome, _ = AO.classify('deck', payload, 200, [], base)
                self.assertEqual(outcome, 'clarified', (payload['clarify'].get('question'), base))
            self.assertTrue(AO.reply_text(payload).strip())
        self.assertEqual(AO.reply_text(no_sentence), AO.CHOICE_DEFAULT_QUESTION)   # the page's own default sentence
        self.assertEqual(AO.CHOICE_DEFAULT_QUESTION, DS.CHOICE_DEFAULT_QUESTION)

    def test_the_same_question_twice_is_still_caught(self):
        hist = [{'role': 'user', 'text': HER_ASK}, {'role': 'agent', 'text': QUESTION}]
        outcome, _ = AO.classify('deck', AS_SENT, 200, hist, 'clarified')
        self.assertEqual(outcome, 'clarified_repeat')

    def test_a_block_that_asks_nothing_is_still_empty(self):
        for payload in ({'success': True, 'clarify': {}}, {'success': True, 'clarify': {'question': '  '}},
                        {'success': True, 'clarify': True}, {'success': True}):
            self.assertEqual(AO.classify('deck', payload, 200, [], None)[0], 'empty', payload)
        self.assertEqual(AO.choice_block({'clarify': 'which one'}), ('', []))

    def test_the_deck_step_sends_the_question_as_its_own_sentence(self):
        out = DS.choice_payload(AS_SENT['clarify'])
        self.assertEqual(out['reply'], QUESTION)
        self.assertEqual(out['clarify']['suggestions'], SUGGESTIONS)      # the page still draws its chips from the block
        self.assertTrue(out['success'])
        self.assertEqual(DS.choice_payload({'options': ['Build the X insights deck']})['reply'], DS.CHOICE_DEFAULT_QUESTION)
        self.assertEqual(DS.choice_payload(None)['reply'], DS.CHOICE_DEFAULT_QUESTION)
        self.assertNotIn(CALM, out['reply'])
        self.assertIsNone(BANNED.search(out['reply']))

    def test_the_envelope_carries_the_question(self):
        app = flask.Flask('pm_deck_dym')
        with app.test_request_context('/api/prometheus/v1/ask', method='POST'):
            for payload in (dict(AS_SENT), DS.choice_payload(AS_SENT['clarify'])):
                env = ENV.wrap(payload, surface='deck', decision={'surface': 'deck'}, thread_id='174b64903b')
                self.assertEqual(env['kind'], 'clarify')
                self.assertEqual(env['text'], QUESTION)
                self.assertTrue(env['success'])

    def test_the_copy_the_team_reads_is_the_question_and_its_choices(self):
        for payload in (AS_SENT, DS.choice_payload(AS_SENT['clarify'])):
            copy = PWN.answer_text(payload)
            self.assertIn(QUESTION, copy)
            self.assertIn('Mistletoe Murders Viewers (all 2 profiles)', copy)
            self.assertNotIn(CALM, copy)
            self.assertNotIn('No written answer', copy)
        self.assertEqual(PWN.answer_text({'reply': 'Deck ready.'}), 'Deck ready.')      # a plain reply is untouched

    def test_the_deck_step_and_the_gate_are_wired_to_it(self):
        self.assertIn("return jsonify(_deck_subject.choice_payload(resolution['clarify']))", CHAT_SRC)
        self.assertNotIn("return jsonify({'success': True, 'clarify': resolution['clarify']})", CHAT_SRC)
        # the gate still holds only what the classifier calls empty or faulted
        self.assertIn("_PM_GATE_HELD = frozenset({'empty', 'faulted'})", CHAT_SRC)
        self.assertIn("held = result in _PM_GATE_HELD", CHAT_SRC)


# ============================================================================ 2
class ATitleWithSeriesIsTheSameTitle(unittest.TestCase):
    """The words a reader adds to a title (series, show, tv) do not make a new subject."""

    def test_plain_title(self):
        self.assertEqual(DS.plain_title('mistletoe murders series viewers'), 'mistletoe murders viewers')
        self.assertEqual(DS.plain_title('Mistletoe Murders TV Series Viewers'), 'Mistletoe Murders Viewers')
        self.assertEqual(DS.plain_title('mistletoe murders show'), 'mistletoe murders')
        self.assertEqual(DS.plain_title('mistletoe murders viewers'), '')        # nothing to take out
        self.assertEqual(DS.plain_title('tv series'), '')                        # nothing left
        self.assertEqual(DS.plain_title(''), '')

    def test_her_words_with_the_profile_open_name_the_page(self):
        # '' is "the page is what was named": the words and the page settle it, with no question
        self.assertEqual(DS.named(HER_ASK, PAGE, [], CATALOG), '')
        self.assertIsNone(DS.question({}, HER_ASK, PAGE, [], CATALOG))

    def test_her_words_with_nothing_open_or_another_profile_open_name_the_library_profile(self):
        for ctx in ({}, None, OTHER):
            self.assertEqual(DS.named(HER_ASK, ctx, [], CATALOG), 'Mistletoe Murders Viewers', ctx)
            self.assertEqual(DS.pick({}, HER_ASK, ctx, [], CATALOG), 'Mistletoe Murders Viewers')
        for text in ('Summary deck for Mistletoe Murders Series Viewers', 'summary deck for the mistletoe murders tv series',
                     'a deck for the mistletoe murders show viewers profile', 'slides for mistletoe murders series viewers please'):
            self.assertEqual(DS.named(text, OTHER, [], CATALOG), 'Mistletoe Murders Viewers', text)
            self.assertIsNone(DS.question({}, text, OTHER, [], CATALOG), text)

    def test_a_library_name_that_carries_the_word_wins_on_its_own_spelling(self):
        both = CATALOG + [{'s3_key': 'Mistletoe_Murders_Series_Viewers_11_12_2026_09_41.csv', 'category': 'SERIES',
                           'display_name': 'Mistletoe Murders Series Viewers', 'subject': 'Mistletoe Murders Series Viewers'}]
        self.assertEqual(DS.named(HER_ASK, OTHER, [], both), 'Mistletoe Murders Series Viewers')
        self.assertEqual(DS.named('summary deck for mistletoe murders viewers', OTHER, [], both), 'Mistletoe Murders Viewers')

    def test_a_name_we_do_not_hold_is_never_swapped_for_one_we_do(self):
        self.assertEqual(DS.named('summary deck for zzyzx road series viewers', PAGE, [], CATALOG), 'Zzyzx Road Series Viewers')
        self.assertEqual(DS.named('summary deck for murders series viewers', {}, [], CATALOG), 'Murders Series Viewers')

    def test_the_kind_of_deck_is_never_the_subject(self):
        for text in ('make me a summary deck for Mistletoe Murders Viewers', 'build a summary deck for wicked tuna profile',
                     'create an overview deck for Mistletoe Murders Viewers', 'make an executive summary deck'):
            self.assertNotIn(PMA.extract_deck_brief(text)['subject'].lower(),
                             ('summary', 'overview', 'executive summary', 'executive'), text)
        self.assertEqual(PMA.extract_deck_brief('deck on mistletoe murders series viewers')['subject'], 'mistletoe murders series viewers')
        self.assertEqual(PMA.extract_deck_brief('build the Paige Bueckers insights deck')['subject'], 'Paige Bueckers')

    def test_the_library_resolver_tries_the_plain_title_after_the_name_as_written(self):
        i = CHAT_SRC.index('def _pm_resolve_deck_subject(text, ctx):')
        body = CHAT_SRC[i:CHAT_SRC.index('\ndef ', i + 10)]
        first, plain = body.index('cands = _match(wanted)'), body.index('wanted, cands = _deck_subject.plain_match(wanted, _match)')
        self.assertLess(first, plain)
        self.assertIn('if not cands:', body[first:plain])
        seen = []
        hit = [{'display_name': 'Mistletoe Murders Viewers'}]
        self.assertEqual(DS.plain_match('mistletoe murders series viewers', lambda ph: seen.append(ph) or hit),
                         ('mistletoe murders viewers', hit))
        self.assertEqual(seen, ['mistletoe murders viewers'])
        self.assertEqual(DS.plain_match('zzyzx road series viewers', lambda ph: []), ('zzyzx road series viewers', []))
        self.assertEqual(DS.plain_match('cruel summer viewers', lambda ph: hit), ('cruel summer viewers', []))   # nothing to take out
        # the page named as "for <Profile>" is the subject, not the buyer
        self.assertIn("'partner': _deck_subject.buyer(resolved_partner, subject)", body)
        self.assertEqual(DS.buyer('Mistletoe Murders Viewers', 'Mistletoe Murders Viewers'), '')
        self.assertEqual(DS.buyer('Hallmark', 'Mistletoe Murders Viewers'), 'Hallmark')
        self.assertEqual(DS.buyer('', 'Mistletoe Murders Viewers'), '')
        self.assertLess(len(CHAT_SRC.splitlines()), 19900)        # the line ratchet holds

    def test_these_requests_are_deck_requests(self):
        for text in (HER_ASK, 'deck on mistletoe murders series viewers', 'make me a summary deck for Mistletoe Murders Viewers'):
            for has in (True, False):
                self.assertEqual((UN.decide(text, has_ctx=has, mode=None) or {}).get('surface'), 'deck', (text, has))

    def test_the_confirm_line_never_doubles_the_article(self):
        self.assertEqual(DP.confirm_line({'subject': 'Mistletoe Murders Viewers'}),
                         'Building the Mistletoe Murders Viewers insights deck. The download link lands here when it is ready.')
        self.assertTrue(DP.confirm_line({'subject': 'The Rainmaker TV Series'}).startswith('Building The Rainmaker TV Series insights deck.'))


# ============================================================================ 3
class AChoiceTappedIsTheAnswer(unittest.TestCase):
    """The chip the reader taps on the did-you-mean starts the deck; the page is not confirmed again."""

    def _ctx(self, suggestion):
        b = suggestion['bind']
        return {'primary': dict(b['primary']), 'cuts': list(b.get('cuts') or []), 'extras': list(b.get('extras') or []), 'view_context': None}

    def test_each_chip_as_the_page_sends_it(self):
        for s, chip in zip(SUGGESTIONS, _chips(SUGGESTIONS)):
            self.assertTrue(DS.choice_picked(_thread_after(chip['label']), self._ctx(s)), chip['label'])

    def test_what_she_tapped_at_21_25(self):
        # her thread: the user turn reads "Mistletoe Murders Viewers", the request carried that profile
        self.assertTrue(DS.choice_picked(_thread_after('Mistletoe Murders Viewers'), self._ctx(SUGGESTIONS[1])))

    def test_a_typed_order_is_not_a_tap(self):
        self.assertFalse(DS.choice_picked(_thread_after('Build a deck from this'), PAGE))
        self.assertFalse(DS.choice_picked(_thread_after('no, the other one'), PAGE))

    def test_a_profile_the_question_did_not_offer_is_not_a_pick(self):
        self.assertFalse(DS.choice_picked(_thread_after('Mistletoe Murders Viewers'), OTHER))
        self.assertFalse(DS.choice_picked(_thread_after('Cruel Summer Viewers'), OTHER))

    def test_no_question_no_pick(self):
        self.assertFalse(DS.choice_picked([], PAGE))
        self.assertFalse(DS.choice_picked([{'role': 'user', 'text': 'Mistletoe Murders Viewers'}], PAGE))
        hist = [{'role': 'user', 'text': 'how big is the audience'},
                {'role': 'agent', 'text': 'Mistletoe Murders viewers project to 1,204,387 people in the US.'},
                {'role': 'user', 'text': 'Mistletoe Murders Viewers'}]
        self.assertFalse(DS.choice_picked(hist, PAGE))
        self.assertFalse(DS.choice_picked(_thread_after('Mistletoe Murders Viewers'), None))
        # the question a turn back, with something said since, is not the question being answered
        later = _thread_after('thanks') + [{'role': 'user', 'text': 'Mistletoe Murders Viewers'}]
        self.assertFalse(DS.choice_picked(later, PAGE))

    def test_a_thread_that_kept_only_the_words(self):
        hist = [{'role': 'user', 'text': HER_ASK}, {'role': 'agent', 'text': QUESTION},
                {'role': 'user', 'text': 'Mistletoe Murders Viewers (all 2 profiles)  (1 total + 1 Avid)'}]
        self.assertTrue(DS.choice_picked(hist, PAGE))
        hist[-1] = {'role': 'user', 'text': 'Cruel Summer Viewers'}
        self.assertFalse(DS.choice_picked(hist, PAGE))

    def test_the_deck_step_reads_the_pick(self):
        i = CHAT_SRC.index('def _pm_deck_core(user, body):')
        body = CHAT_SRC[i:CHAT_SRC.index("\n@_H.app.route('/api/brief-chat/pay-per-use'", i)]
        self.assertIn("_deck_subject.choice_picked(history, resolution.get('ctx'))", body)
        named = body.index('_page_named = bool(')
        self.assertLess(named, body.index("and not body.get('confirm_open_screen') and not _page_named"))


# ============================================================================ 4
REPLAY_CHILD = r'''
import json, os, sys
sys.path.insert(0, __WEBAPP__)
os.environ['PM_MAIL_OFF'] = '1'
from prometheus import replay
replay.install_isolation()
h = replay.harness()
u = dict(replay.REPLAY_USER)
S = {'username': u['username']}
CATALOG = json.loads(__CATALOG__)
for row in CATALOG:
    h.s3.put_object(Bucket='dashboard-inputs', Key=row['s3_key'], Body=replay._profile_csv(row['display_name'].split(' - ')[0]))
h.host.set('s3_cache', {'jobs': [dict(r, status='cached', source='s3', last_modified='2026-11-12T06:40:14+00:00') for r in CATALOG]})
h.host.set('_admin_hidden_profile_keys', lambda *a, **k: set())
h.host.set('maybe_refresh_persisted_cache_if_changed', lambda *a, **k: None)
h.host.set('_profile_age_days', lambda *a, **k: 1)
PAGES = json.loads(__PAGES__)

def post(text, page, tid, history=None, extra=None):
    n0 = len(replay._STATE['captured'])
    e0 = len(getattr(h.host, 'error_emails', []) or [])
    body = {'text': text, 'history': list(history or []), 'client': 'dashboard', 'persist': False, 'surface': 'deck', 'thread_id': tid}
    if page is not None:
        body['page_context'] = page
    body.update(extra or {})
    st, pl = h.post(replay.DOOR_PATH, body, session=S)
    raw = pl.get('raw') if isinstance(pl.get('raw'), dict) else pl
    recs = replay._STATE['captured'][n0:]
    cl = raw.get('clarify') if isinstance(raw.get('clarify'), dict) else {}
    return {'status': st, 'kind': pl.get('kind'), 'text': pl.get('text'), 'reply': raw.get('reply'), 'error': raw.get('error'),
            'gate': raw.get('gate'), 'held': raw.get('held'), 'job': bool(raw.get('job_id')), 'subject': raw.get('subject'),
            'confirm_line': raw.get('confirm_line'), 'question': cl.get('question'), 'suggestions': cl.get('suggestions') or [],
            'followups': raw.get('followups'), 'memory_confirm': bool(raw.get('memory_confirm')),
            'log': [{'route': r.get('route'), 'outcome': r.get('outcome'), 'reply': r.get('reply'),
                     'result': (r.get('extra') or {}).get('result'), 'gate': (r.get('extra') or {}).get('gate')} for r in recs],
            # the ops alerts raised while this ask ran that are about its reply: the classifier's own
            # ('ask outcome empty') and anything the deck step raised. A background thread's note is not this ask's.
            'alerts': [str(x)[:300] for x in (getattr(h.host, 'error_emails', []) or [])[e0:]
                       if 'ask outcome' in str(x) or 'deck' in str(x[0] if isinstance(x, tuple) and x else x).lower()]}

def fresh():
    st, d = h.post('/api/brief-chat/threads/new', {}, session=S)
    return d.get('active')

out = {'asks': [], 'taps': []}
for text, page in json.loads(__ASKS__):
    r = post(text, PAGES.get(page), fresh())
    r.update({'ask': text, 'page': page})
    out['asks'].append(r)

def tap(first, page, index, typed=None):
    tid = fresh()
    r1 = post(first, PAGES.get(page), tid)
    opts = [{'label': s['label'] + ('  (' + s['subtitle'] + ')' if s.get('subtitle') else ''), 'send': s['label'],
             'deckBind': s.get('bind'), 'deckKind': s.get('kind')} for s in r1['suggestions']]
    hist = [{'role': 'user', 'text': first, 'turn_id': 'u1'},
            {'role': 'agent', 'text': r1['question'] or '', 'turn_id': 'a1', 'meta': {'options': opts}}]
    if not opts:
        return {'first': r1, 'second': None}
    o = opts[min(index, len(opts) - 1)]
    b = o['deckBind']
    ctx = {'primary': dict(b['primary']), 'cuts': list(b.get('cuts') or []), 'extras': list(b.get('extras') or []), 'view_context': None}
    if typed:
        r2 = post(typed, PAGES.get(page) or ctx, tid, hist + [{'role': 'user', 'text': typed, 'turn_id': 'u2'}])
    else:
        r2 = post('Build a deck from this', ctx, tid, hist + [{'role': 'user', 'text': o['label'], 'turn_id': 'u2'}], {'angle': first})
    return {'first': r1, 'second': r2, 'tapped': o['label']}

TYPO = 'summary deck for misletoe murder viewers'
out['taps'] = [tap(TYPO, 'none', 1), tap(TYPO, 'none', 0), tap(TYPO, 'other', 1)]
out['typed_after'] = tap(TYPO, 'open', 1, typed='Build a deck from this')
_res = json.dumps(out, default=str)
_path = os.environ.get('PM_DECK_DYM_RESULT')
with open(_path + '.part', 'w', encoding='utf-8') as _fh:
    _fh.write(_res)
os.replace(_path + '.part', _path)
'''

# every ask that names the profile we hold: her words, then the close variants, with the profile open, nothing open, another open
ON_THE_PROFILE = [
    (HER_ASK, 'open'), (HER_ASK, 'none'), (HER_ASK, 'other'),
    ('Summary deck for Mistletoe Murders series viewers', 'open'),
    ('Summary deck for Mistletoe Murders Series Viewers', 'other'),
    ('deck on mistletoe murders series viewers', 'open'), ('deck on mistletoe murders series viewers', 'none'),
    ('deck on mistletoe murders series viewers', 'other'),
    ('make me a summary deck for Mistletoe Murders Viewers', 'open'), ('make me a summary deck for Mistletoe Murders Viewers', 'none'),
    ('make me a summary deck for Mistletoe Murders Viewers', 'other'),
    ('make me a summary deck for mistletoe murders viewers', 'open'), ('make me a summary deck for mistletoe murders viewers', 'none'),
    ('summary deck for mistletoe murders', 'open'), ('summary deck for the mistletoe murders tv series', 'none'),
    ('build a summary deck for the mistletoe murders show viewers profile', 'other'),
]
OWN_SPELLING = [('summary deck for the rainmaker tv series', 'none'), ('summary deck for the rainmaker tv series', 'other')]
NOT_HELD = [('summary deck for zzyzx road series viewers', 'none'), ('summary deck for zzyzx road series viewers', 'open')]
MISSPELLED = [('summary deck for misletoe murder viewers', 'none')]
OPEN_PAGE_ONLY = [('Build a deck from this', 'open')]


class TheRealDoor(unittest.TestCase):
    """Her words and their close variants through the real routes, the deck step and the answer gate."""

    @classmethod
    def setUpClass(cls):
        parent_scripts = os.path.join(os.path.dirname(ROOT), 'scripts')
        if not os.path.exists(os.path.join(parent_scripts, '_pm_behavior.py')):
            raise unittest.SkipTest('the replay harness lives in the parent repo; not on this path')
        asks = ON_THE_PROFILE + OWN_SPELLING + NOT_HELD + MISSPELLED + OPEN_PAGE_ONLY
        child = (REPLAY_CHILD.replace('__WEBAPP__', repr(ROOT)).replace('__CATALOG__', repr(json.dumps(CATALOG)))
                 .replace('__PAGES__', repr(json.dumps({'open': PAGE, 'other': OTHER, 'none': None})))
                 .replace('__ASKS__', repr(json.dumps(asks))))
        cls._work = tempfile.mkdtemp(prefix='pm_deck_dym_')
        res_path = os.path.join(cls._work, 'result.json')
        env = dict(os.environ, PM_REPLAY_CHILD='1', PM_DECK_DYM_RESULT=res_path, TMPDIR=cls._work)
        for k in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'ANTHROPIC_API_KEY'):
            env.pop(k, None)
        proc = subprocess.run([sys.executable, '-c', child], capture_output=True, text=True, timeout=600, env=env)
        if not os.path.exists(res_path):
            raise AssertionError('the replay child produced no result\n' + proc.stdout[-2000:] + '\n' + proc.stderr[-3000:])
        with open(res_path, encoding='utf-8') as fh:
            cls.out = json.load(fh)
        cls.by = {(r['ask'], r['page']): r for r in cls.out['asks']}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, '_work', '') or os.devnull, ignore_errors=True)

    def _never_the_calm_line(self, r):
        blob = json.dumps(r, default=str)
        self.assertNotIn(CALM, blob, r)
        self.assertNotIn(r.get('gate'), ('held', 'repeat'), r)
        self.assertFalse(r.get('held'), r)
        self.assertEqual(r['alerts'], [], r)                      # nothing went to ops as a failed reply
        for rec in r['log']:
            self.assertNotIn(rec['outcome'], ('empty', 'faulted', 'error', 'held', 'clarified_repeat'), r)
            self.assertNotIn(rec['result'], ('empty', 'faulted'), r)
            self.assertNotEqual(rec['gate'], 'held', r)

    def test_her_exact_ask_starts_the_deck_on_the_profile_on_her_screen(self):
        r = self.by[(HER_ASK, 'open')]
        self._never_the_calm_line(r)
        self.assertEqual(r['status'], 200, r)
        self.assertTrue(r['job'], r)
        self.assertEqual(r['subject'], 'Mistletoe Murders Viewers', r)
        self.assertEqual(r['confirm_line'], 'Building the Mistletoe Murders Viewers insights deck. The download link lands here when it is ready.')
        self.assertEqual([(x['route'], x['outcome']) for x in r['log']], [('deck', 'queued')], r)
        self.assertFalse(r['suggestions'], r)                    # no did-you-mean about the profile on her screen
        self.assertFalse(r['memory_confirm'], r)                 # and no "do you want this on ..." either

    def test_every_variant_that_names_the_profile_starts_its_deck(self):
        for ask in ON_THE_PROFILE:
            r = self.by[ask]
            self._never_the_calm_line(r)
            self.assertTrue(r['job'], (ask, r))
            self.assertEqual(r['subject'], 'Mistletoe Murders Viewers', (ask, r))
            self.assertEqual(r['kind'], 'job', (ask, r))
            self.assertEqual(r['log'][-1]['outcome'], 'queued', (ask, r))
            self.assertIsNone(BANNED.search(str(r['confirm_line'])), (ask, r))

    def test_a_library_name_that_carries_the_word_keeps_its_own_profile(self):
        for ask in OWN_SPELLING:
            r = self.by[ask]
            self._never_the_calm_line(r)
            self.assertTrue(r['job'], (ask, r))
            self.assertEqual(r['subject'], 'The Rainmaker TV Series', (ask, r))
            self.assertTrue(str(r['confirm_line']).startswith('Building The Rainmaker TV Series insights deck.'), (ask, r))

    def test_a_subject_we_do_not_hold_gets_one_plain_line(self):
        for ask in NOT_HELD:
            r = self.by[ask]
            self._never_the_calm_line(r)
            self.assertFalse(r['job'], (ask, r))
            said = str(r['error'] or r['reply'] or '')
            self.assertIn('Zzyzx Road Series Viewers', said, (ask, r))
            self.assertIn('I do not have a profile or a finished read on', said, (ask, r))
            self.assertNotIn('Mistletoe', said, (ask, r))         # never another subject in its place
            self.assertEqual(r['log'][-1]['outcome'], 'declined', (ask, r))

    def test_a_misspelled_name_gets_the_did_you_mean_as_a_question(self):
        r = self.by[MISSPELLED[0]]
        self._never_the_calm_line(r)
        self.assertEqual(r['kind'], 'clarify', r)
        self.assertTrue(str(r['question']).startswith('I could not find an exact match for'), r)
        self.assertEqual(r['reply'], r['question'], r)           # the reply is the question
        self.assertEqual(r['text'], r['question'], r)            # and so is the envelope's text
        self.assertIn('Mistletoe Murders Viewers', [s['label'] for s in r['suggestions']], r)
        self.assertEqual(r['log'][-1]['outcome'], 'clarified', r)
        self.assertEqual(r['log'][-1]['reply'], r['question'], r)   # the ask log keeps what she read

    def test_a_tapped_choice_starts_the_deck_with_no_second_question(self):
        for t in self.out['taps']:
            self._never_the_calm_line(t['first'])
            r = t['second']
            self.assertIsNotNone(r, t)
            self._never_the_calm_line(r)
            self.assertTrue(r['job'], t)
            self.assertEqual(r['subject'], 'Mistletoe Murders Viewers', t)
            self.assertFalse(r['memory_confirm'], t)
            self.assertNotIn('open on your screen', json.dumps(r), t)

    def test_an_order_that_names_nothing_still_asks_about_the_page_once(self):
        for r in (self.by[OPEN_PAGE_ONLY[0]], self.out['typed_after']['second']):
            self._never_the_calm_line(r)
            self.assertFalse(r['job'], r)
            self.assertTrue(r['memory_confirm'], r)
            self.assertEqual(r['reply'], 'Do you want this on Mistletoe Murders Viewers (open on your screen)?', r)


if __name__ == '__main__':
    unittest.main(verbosity=1)
