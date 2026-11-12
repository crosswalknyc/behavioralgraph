#!/usr/bin/env python3
"""The subject of a quote is decided by rule (Jenna 2026-11-11: "these
errors cannot happen. fix and ensure itll be correct going forward").

Armine asked, in a fresh chat: "Looking at YouTube. What type of
categories do best for digital original content: Comedy, lifestyle/arts
& culture, Faith, Young Adult, Drama, and Horror do in this space.?"
Five replays in a row on prod came back "I can pull that read on
Comedy. $100."; with Horror written first, three of five quoted the
Horror Streaming Enthusiasts profile and two quoted "Horror". The
Reader lists the six categories as six subjects (sometimes matching one
to a library profile with the word in its name) and the one binder took
the first of them.

  1. Whatever the Reader makes of the list, the subject is the subject
     on record; with none on record the reader is asked.
                                         -> TheSubjectIsDecidedByRule
  2. The quote is written properly for one, two and several parts, says
     which parts are included, says the deck comes once every part has
     landed, and names a stored subject plainly.    -> TheQuoteReadsPlain
  3. A test run never rewrites the tracked users.json.
                                             -> TheUsersFileIsLeftAlone

Hermetic: the stub host and in-memory store of test_pm_new_chat_no_bleed.
No model, no S3, no app module, no mail.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location('pm_no_bleed_scaffold_r4', os.path.join(HERE, 'test_pm_new_chat_no_bleed.py'))
NB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NB)

import insights_ledger as IL  # noqa: E402
import users_mirror as UM  # noqa: E402
from prometheus import cut_list as CL  # noqa: E402
from prometheus import multi_ask as MA  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus import reader as RD  # noqa: E402
from prometheus import ride_along as RA  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402
from prometheus.host import host  # noqa: E402

APP, World, USER, THREADS = NB.APP, NB.World, NB.USER, NB.THREADS
FX1 = json.loads(Path(HERE, 'fixtures', 'pm_truth_2026_11_11.json').read_text(encoding='utf-8'))
MULTI = FX1['armine_words']['summarize_in_powerpoint']
P1 = ("Looking at YouTube. What type of categories do best for digital original content: Comedy, lifestyle/arts & culture, Faith, "
      "Young Adult, Drama, and Horror do in this space.?")
assert RA.split(MULTI)['question'].startswith(P1)                      # her verbatim first question
HORROR_FIRST = P1.replace('Comedy, lifestyle/arts & culture, Faith, Young Adult, Drama, and Horror',
                          'Horror, Comedy, lifestyle/arts & culture, Faith, Young Adult, and Drama')
# the other worker's replay words (scripts/test_pm_platform_scopes_subject.py, ARMINE)
_src = Path(HERE, 'test_pm_platform_scopes_subject.py').read_text(encoding='utf-8')
_ns = {}
exec(_src[_src.index('ARMINE = ('):_src.index('ARMINE_2 =')], _ns)
OTHER = _ns['ARMINE']
HELD = 'Digital original content categories on YouTube (Comedy, Lifestyle/Arts & Culture, Drama, Horror, Young Adult, Faith)'
HELD_KEY = 'digital_original_content_categories_on_youtube_comedy_lifestyle_arts_culture_dra'
HELD_ALL = 'Digital original content categories (Comedy, Lifestyle/Arts & Culture, Faith, Young Adult, Horror)'
CLEAN = 'Digital original content on YouTube, by category (Comedy, Lifestyle/Arts & Culture, Drama, Horror, Young Adult and Faith)'


def _row(subject, key):
    return {'subject': subject, 'entries': [{'k': key + '|viewership|any|any', 'subject': subject, 'q': 'delivered anchors', 'qn': 'delivered anchors',
                                             'ts': '2026-11-12T02:19:33Z', 'route': 'delivered_deck', 'reply': ''}]}


LEDGER = {'subjects': {
    'youtube': _row('youtube', 'youtube'),
    HELD_KEY: _row(HELD, HELD_KEY),
    'digital_original_content_categories_comedy_lifestyle_arts_culture_faith_young_ad': _row(HELD_ALL, 'doc_all')}}

_SIX = ['Comedy', 'lifestyle/arts & culture', 'Faith', 'Young Adult', 'Drama', 'Horror']


def _reading(intent, first=None, platform='YouTube', extra=None, order=None):
    subs = [{'as_written': n, 'catalog': '', 'kind': 'audience'} for n in (order or _SIX)]
    if first:
        subs = [first] + [x for x in subs if x['as_written'] != first.get('as_written')]
    return {'intent': intent, 'platform': platform, 'flags': ['multiple_subjects'], 'confidence': 0.62, 'subjects': subs + list(extra or []),
            'candidates': ['Pop Culture Jeopardy', 'Las Culturistas Culture Awards Viewers', 'Nimrods A Green DAY Comedy',
                           'Elevated Horror And Psychological Thriller Viewers', 'Horror Streaming Enthusiasts']}


# every reading the model has been seen to give for her words (prod, 2026-11-12), and the ones it could
READINGS = {
    'the six categories as six audiences, a comparison': _reading('compare'),
    'the six categories as six audiences, an analysis': _reading('analyze'),
    'the platform first': _reading('analyze', first={'as_written': 'YouTube', 'catalog': 'YouTube', 'kind': 'platform'}),
    'Horror matched to a library profile': _reading('compare', first={'as_written': 'Horror', 'catalog': 'Horror Streaming Enthusiasts', 'kind': 'audience'}),
    'Horror first, as written': _reading('compare', first={'as_written': 'Horror', 'catalog': '', 'kind': 'audience'}),
    'Comedy matched to a library profile': _reading('analyze', first={'as_written': 'Comedy', 'catalog': 'Nimrods A Green DAY Comedy', 'kind': 'audience'}),
    'a piece of the stored name': _reading('analyze', first={'as_written': 'digital original content', 'catalog': '', 'kind': 'audience'}),
    'no reading at all': None,
}
# what a lane could be holding when it reaches the quote
PAYER = {'username': 'payer_r4', 'email': 'payer@example.com', 'role': 'user', 'credits': 12}
CANDIDATES = ('Comedy', 'Horror', 'YouTube', 'Horror Streaming Enthusiasts', 'Nimrods A Green DAY Comedy', 'lifestyle/arts & culture',
              'Lifestyle', 'Young Adult', 'Digital Original Content', '', HELD)


class _WithLedger(unittest.TestCase):
    ledger = LEDGER

    def setUp(self):
        self._load, self._incl, self._price = IL._load_index, PR.included_with_plan, PR.custom_price
        IL._load_index = lambda force=False: self.ledger
        # the seat's plan and price as the test states them, whatever an earlier case left bound on the host
        PR.included_with_plan = lambda user, username=None, host=None: bool((user or {}).get('unlimited'))
        PR.custom_price = lambda username, history, subject: {'label': '$100', 'credits': 1, 'pull_type': 'Panel Report',
                                                              'tool_key': 'panel_report', 'usd': 100.0, 'followup': False, 'metered': False}

    def tearDown(self):
        IL._load_index, PR.included_with_plan, PR.custom_price = self._load, self._incl, self._price


# ============================================================================ 1
class TheSubjectIsDecidedByRule(_WithLedger):
    def test_the_words_list_categories_and_ordinary_lists_do_not(self):
        self.assertEqual(CL.items(P1), _SIX)
        self.assertEqual(CL.items(HORROR_FIRST)[0], 'Horror')
        self.assertEqual(CL.items(OTHER), ['Comedy', 'Lifestyle/Arts & Culture', 'Faith', 'Young Adult', 'Horror'])
        self.assertEqual(CL.items('Looking at Netflix, which genres do best: Comedy and Drama?'), ['Comedy', 'Drama'])
        for words in ('Compare Nike, Adidas and Puma buyers', 'How do Comedy fans, Drama fans and Horror fans differ on Netflix?',
                      'Which does better on Netflix, Comedy or Drama?', 'What do Faith Hill fans, Reba McEntire fans and Dolly Parton fans watch?',
                      'who watches Comedy Central, Horror Streaming Enthusiasts and Drama Queens', 'Can we give some examples?',
                      'how is the audience like?', 'What age groups are Reba McEntire fans?'):
            self.assertEqual(CL.items(words), [], words)

    def test_a_category_is_a_cut_and_so_is_a_profile_reached_through_one(self):
        for name in ('Comedy', 'Horror', 'lifestyle/arts & culture', 'Lifestyle', 'Young Adult', 'Horror Streaming Enthusiasts',
                     'Nimrods A Green DAY Comedy', 'Elevated Horror And Psychological Thriller Viewers'):
            self.assertTrue(CL.is_cut(name, P1), name)
        for name in ('Nike', 'YouTube', 'Digital Original Content', HELD, HELD_ALL, 'Mistletoe Murders Viewers'):
            self.assertFalse(CL.is_cut(name, P1), name)
        # a profile the words name outright is named, not reached
        self.assertFalse(CL.is_cut('Horror Streaming Enthusiasts', 'For Horror Streaming Enthusiasts, which categories do best: Comedy, Drama and Faith?'))

    def test_every_reading_binds_the_subject_on_record(self):
        """The one binder took the first subject the Reader listed. Each reading the model can give for her
        words, the Horror-first words and the other worker's words now binds the same subject."""
        for words in (P1, HORROR_FIRST, OTHER):
            for label, rd in READINGS.items():
                s = SR.bind(words, [], '', reading=rd if rd is not None else {})
                self.assertEqual((s.source, s.name), ('words', HELD), (words[:30], label))
                self.assertTrue(s.qualifiers.get('on_record'), label)
                self.assertEqual(PR.bound_subject(words, [], None, reading=rd if rd is not None else {}), HELD, label)
                # the read step's last line before the quote asked the binder again and put "Comedy" back
                for held in (HELD, 'Comedy', 'Horror Streaming Enthusiasts'):
                    self.assertEqual(SR.display_for(held, words, reading=rd if rd is not None else {}), HELD, (label, held))

    def test_every_quote_names_the_subject_on_record_whatever_the_lane_was_holding(self):
        """`offer_or_charge` is the one place a quote is made, on every chat path."""
        for words in (P1, HORROR_FIRST, OTHER):
            for cand in CANDIDATES:
                state, pl = PR.offer_or_charge(text=words, subject=cand, kind='report', panel_confirm=None, username='payer_r4',
                                               user=dict(PAYER), history=[])
                self.assertEqual(state, 'offer', cand)
                self.assertEqual(pl['panel_offer']['subject'], HELD, cand)
                self.assertEqual(pl['subject'], HELD, cand)
                self.assertTrue(pl['reply'].startswith(f'I can pull that read on {CLEAN}.'), pl['reply'])
                self.assertIn('$100', pl['reply'])                           # the price is what it was
                self.assertNotRegex(pl['reply'], r"read on (?:Comedy|Horror|YouTube)\b")
                # a seat with reads included is never quoted: the read that starts is on the same subject
                state, pl = PR.offer_or_charge(text=words, subject=cand, kind='report', panel_confirm=None, username=USER,
                                               user=dict(NB.ARMINE), history=[])
                self.assertEqual((state, pl['subject']), ('charged', HELD), cand)

    def test_on_the_approve_the_charge_is_on_the_subject_that_was_quoted(self):
        used = []

        def consume(username, **kw):
            used.append(kw)
            return True
        pc = {'subject': HELD, 'kind': 'report', 'question': P1}
        for cand in ('Comedy', 'Horror Streaming Enthusiasts', HELD):
            state, pl = PR.offer_or_charge(text=P1, subject=cand, kind='report', panel_confirm=dict(pc), username='payer_r4',
                                           user=dict(PAYER), history=[], consume=consume)
            self.assertEqual((state, pl['subject']), ('charged', HELD), cand)
        self.assertEqual(len(used), 3)
        self.assertTrue(all('Comedy.' not in str(u.get('description') or '').replace('(Comedy', '') for u in used))

    def test_through_the_door_every_reading_runs_on_the_subject_on_record(self):
        orig = RD.read
        try:
            for words in (P1, HORROR_FIRST, OTHER):
                for label, rd in READINGS.items():
                    w = World()
                    w.calls.clear()
                    RD.read = (lambda *a, _rd=rd, **k: dict(_rd, text=a[0]) if _rd else None)
                    with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
                        env, _st = S.ask(dict(NB.ARMINE), {'text': words, 'client': 'dashboard', 'surface': 'analyze',
                                                           '_client_surface_sent': 'analyze', 'history': []})
                    seen = [c for c in w.calls if c[0] == 'analyze']
                    self.assertEqual(len(seen), 1, (words[:30], label, env.get('text')))
                    self.assertEqual(seen[0][2].get('bind_subject'), HELD, (words[:30], label))
                    self.assertFalse(seen[0][2].get('bind_cohort'))              # the stored name already says where
        finally:
            RD.read = orig

    def test_with_no_subject_on_record_it_asks_and_never_quotes_a_category(self):
        self.ledger = {'subjects': {'youtube': _row('youtube', 'youtube')}}
        IL._load_index = lambda force=False: self.ledger
        orig = RD.read
        try:
            for label, rd in READINGS.items():
                if label == 'a piece of the stored name':
                    continue                 # with nothing on record "digital original content" is a name of its own
                self.assertEqual(SR.bind(P1, [], '', reading=rd if rd is not None else {}).source, 'none', label)
                w = World()
                w.calls.clear()
                RD.read = (lambda *a, _rd=rd, **k: dict(_rd, text=a[0]) if _rd else None)
                with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
                    env, _st = S.ask(dict(NB.ARMINE), {'text': P1, 'client': 'dashboard', 'surface': 'analyze',
                                                       '_client_surface_sent': 'analyze', 'history': []})
                self.assertTrue(env['text'].startswith('Which audience should this run on?'), (label, env['text'][:120]))
                self.assertEqual([c for c in w.calls if c[0] in ('analyze', 'interpret')], [], label)     # no lane ran
                self.assertNotIn('$', env['text'])
        finally:
            RD.read = orig
        for cand in CANDIDATES[:-1]:
            if cand == 'Digital Original Content':
                continue                                                         # a name of its own when nothing is on record
            state, pl = PR.offer_or_charge(text=P1, subject=cand, kind='report', panel_confirm=None, username='payer_r4',
                                           user=dict(PAYER), history=[])
            self.assertEqual(state, 'offer', cand)
            self.assertTrue(pl.get('audience_confirm'), cand)
            self.assertNotIn('panel_offer', pl)

    def test_the_thread_and_the_page_the_words_point_at(self):
        self.ledger = {'subjects': {}}
        IL._load_index = lambda force=False: self.ledger
        thread = [{'role': 'user', 'text': 'who watches Mistletoe Murders'},
                  {'role': 'agent', 'text': 'On Mistletoe Murders Viewers:\n\n1,284,113 US viewers.',
                   'meta': {'kind': 'read', 'read_job_id': 'bf26a1511274', 'subject': 'Mistletoe Murders Viewers', 'landed_at': '2026-11-11T22:07:53Z'}}]
        q = 'Looking at YouTube, which genres do best: Comedy, Drama and Horror?'
        self.assertEqual(CL.gate(q, thread, None, {}, reading=READINGS['Horror matched to a library profile']),
                         {'bind': 'Mistletoe Murders Viewers', 'cohort': 'on YouTube', 'source': 'record'})
        page = {'primary': {'name': 'Cruel Summer Viewers', 's3_key': 'Cruel_Summer_Viewers_11_11_2026_02_14.csv'}}
        got = CL.gate('For this audience, which genres do best: Comedy, Drama and Horror?', [], page, {}, reading=None)
        self.assertEqual((got['bind'], got['source']), ('Cruel Summer Viewers', 'page'))
        # the open page is never the default: with nothing pointing at it the reader is asked, the page a chip
        self.assertEqual(CL.gate(q, [], page, {}, reading=None), {'ask': True})
        self.assertIn('Cruel Summer Viewers (open on your screen)', PR.which_audience(q, page)['followups'])

    def test_a_subject_of_its_own_and_a_chosen_subject_are_left_alone(self):
        nike = 'For Nike buyers, which categories do best on YouTube: Comedy, Drama and Horror?'
        rd = {'intent': 'analyze', 'subjects': [{'as_written': 'Comedy', 'catalog': '', 'kind': 'audience'},
                                                {'as_written': 'Nike buyers', 'catalog': 'Nike', 'kind': 'brand'}]}
        s = SR.bind(nike, [], '', reading=rd)
        self.assertEqual(s.source, 'words')
        self.assertIn('Nike', s.name)
        self.assertIsNone(CL.gate(nike, [], None, {}, reading=rd))
        self.assertEqual(CL.settle(nike, [], 'Nike')[0], 'Nike')
        self.assertIsNone(CL.gate(P1, [], None, {'bind_subject': 'Reba McEntire'}))
        self.assertIsNone(CL.gate(P1, [], None, {'panel_confirm': {'subject': HELD}}))
        self.assertIsNone(CL.gate('What age groups are Reba McEntire fans?', [], None, {}))
        # words that list nothing are bound exactly as before
        rd2 = {'intent': 'analyze', 'subjects': [{'as_written': 'Horror', 'catalog': 'Horror Streaming Enthusiasts', 'kind': 'audience'}]}
        self.assertEqual(SR.bind('what do Horror viewers buy', [], '', reading=rd2).name,
                         SR._bind_words('what do Horror viewers buy', [], '', rd2).name)

    def test_the_wiring(self):
        chat = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
        self.assertIn("subj_hint = _cut_list.hint(text, mr.get('subject'))", chat)
        self.assertLess(len(chat.splitlines()), 19900)
        self.assertEqual(CL.hint(P1, 'Comedy'), '')
        self.assertEqual(CL.hint(P1, 'Horror Streaming Enthusiasts'), '')
        self.assertEqual(CL.hint(P1, 'Nike'), 'Nike')
        self.assertEqual(CL.hint('what do Horror viewers buy', 'Horror'), 'Horror')
        svc = Path(ROOT, 'prometheus', 'service.py').read_text(encoding='utf-8')
        # ahead of everything that acts on the reading: the reader's executor, the work gate, the lanes
        self.assertLess(svc.find('_cutl.gate(text, history, ctx, body'), svc.find('_rx = None if _is_brief else _execute_reading('))
        self.assertLess(svc.find('_cutl.gate(text, history, ctx, body'), svc.find('# lane: work_gate'))
        pr = Path(ROOT, 'prometheus', 'priced_read.py').read_text(encoding='utf-8')
        i = pr.find('def offer_or_charge(')
        self.assertIn('_cl.settle(text, history', pr[i:i + 2600])
        # the door step is not a lane: the lane order is what it was
        self.assertEqual(list(S.LANE_ORDER), list(S.lane_markers()))


# ============================================================================ 2
class TheQuoteReadsPlain(_WithLedger):
    def test_the_head_is_written_for_one_two_and_several(self):
        want = {
            (1, 0, 0, False): 'One priced read here:',
            (1, 0, 0, True): 'One of these is a priced read:',
            (2, 0, 0, False): 'Two priced reads here, quoted together and run together:',
            (2, 0, 0, True): 'Two of these are priced reads, quoted together and run together:',
            (3, 0, 0, False): '3 priced reads here, quoted together and run together:',
            (1, 1, 0, False): 'Two reads here, quoted together and run together. One carries a price; the other is included at no extra charge:',
            (1, 2, 0, False): 'Three reads here, quoted together and run together. One carries a price; the other two are included at no extra charge:',
            (2, 3, 0, False): 'Five reads here, quoted together and run together. Two carry a price; the other three are included at no extra charge:',
            (1, 2, 0, True): 'Three of these are reads, quoted together and run together. One carries a price; the other two are included at no extra charge:',
        }
        for args, line in want.items():
            self.assertEqual(MA._quote_head(*args), line, args)
        for args in want:
            self.assertNotRegex(MA._quote_head(*args), r"^1 priced reads|\b1 of these are\b")

    def _offer(self, text, subject, label='$100'):
        return {'success': True, 'action': 'answer', 'reply': f'{PR.lead_for("report", subject)} Pulling this data will cost {label}.',
                'followups': ['Approve', 'Cancel'], 'subject': subject,
                'panel_offer': {'question': text, 'subject': subject, 'kind': 'report', 'frame': '', 'price_label': label}}

    def test_her_three_parts_one_priced_two_included(self):
        """Prod, 2026-11-12: "1 priced reads here, quoted together and run together:" over one priced part
        and two included ones, and the closing line "Approve runs all 3 for $100 in all"."""
        parts = MA.split(RA.split(MULTI)['question'])
        out = MA.combined_offer(RA.split(MULTI)['question'], parts, [self._offer(p, HELD) for p in parts])
        lines = out['reply'].split('\n\n')
        self.assertEqual(lines[0], 'Three reads here, quoted together and run together. One carries a price; the other two are '
                                   'included at no extra charge:')
        self.assertEqual(lines[1], f'1. I can pull that read on {CLEAN}. $100.')
        self.assertEqual(lines[2], f'2. I can pull that read on {CLEAN}. Included with 1 at no extra charge.')
        self.assertEqual(lines[-1], 'Approve runs all three for $100 in all (2 and 3 are included at no charge); Cancel runs none of them.')
        # what Approve carries back is the stored subject and the price it had
        po = out['panel_offer']
        self.assertEqual([p['subject'] for p in po['parts']], [HELD] * 3)
        self.assertEqual([p['status'] for p in po['parts']], ['priced', 'included', 'included'])
        self.assertEqual(po['price_label'], '$100')
        # one priced part beside an answered one
        one = MA.combined_offer('a. b.', ['how big is X?', 'how big is Y?'],
                                [{'success': True, 'reply': 'X is 3,412,907 people.'}, self._offer('how big is Y?', 'Y Viewers')])
        self.assertIn('One of these is a priced read:', one['reply'])
        self.assertIn('Approve runs it for $100; Cancel runs none of them.', one['reply'])
        self.assertNotIn('all 1', one['reply'])

    def test_the_deck_line_when_every_part_is_quoted(self):
        """It read "once the read behind it lands, I build the deck from it" over three quoted parts."""
        w = World()
        tid = w.new_thread()
        parts = MA.split(RA.split(MULTI)['question'])
        raw = MA.combined_offer(RA.split(MULTI)['question'], parts, [self._offer(p, HELD) for p in parts])
        plan = {'kinds': ['deck'], 'words': 'Summarize in powerpoint', 'question': RA.split(MULTI)['question']}
        with APP.test_request_context('/'):
            env = RA._carry({'kind': 'answer', 'thread_id': tid, 'text': raw['reply'], 'raw': raw}, plan, dict(NB.ARMINE),
                            {'text': MULTI, 'client': 'dashboard', 'thread_id': tid}, 'session')
        self.assertTrue(env['text'].rstrip().endswith(RA.DECK_ALL_PARTS_LINE))
        self.assertIn('when every part has landed', RA.DECK_ALL_PARTS_LINE)
        rec = w.get(RA._key(USER, tid))
        self.assertEqual((rec['state'], rec['waits_on_quote'], len(rec['quoted'])), ('awaiting_read', True, 3))
        # one quoted read, as before
        w2 = World()
        tid2 = w2.new_thread()
        with APP.test_request_context('/'):
            env2 = RA._carry({'kind': 'answer', 'thread_id': tid2, 'text': 'x', 'raw': self._offer(P1, HELD)}, plan, dict(NB.ARMINE),
                             {'text': MULTI, 'client': 'dashboard', 'thread_id': tid2}, 'session')
        self.assertTrue(env2['text'].rstrip().endswith(RA.DECK_AFTER_APPROVAL_LINE))

    def test_a_stored_subject_is_named_plainly_and_priced_as_it_was(self):
        self.assertEqual(CL.quote_name(HELD), CLEAN)
        self.assertEqual(CL.quote_name(HELD_ALL),
                         'Digital original content, by category (Comedy, Lifestyle/Arts & Culture, Faith, Young Adult and Horror)')
        for name in ('Mistletoe Murders Viewers (all seasons)', "Georgie & Mandy's First Marriage Viewers - Paramount+", 'Reba McEntire',
                     'YouTube original content categories (US)', 'The Love Hypothesis (Ali Hazelwood) vs The Map That Leads To You (J.P. Monninger)'):
            self.assertEqual(CL.quote_name(name), name)
        # the viewership lead no longer reads "watched Digital original content categories on YouTube (... Drama ...)"
        lead = PR.lead_for('viewership', HELD, verb='watched', span='one year')
        self.assertNotIn('categories on YouTube (', lead)
        self.assertEqual(lead.count('YouTube'), 1)
        self.assertEqual(lead.count('Comedy'), 1)
        state, pl = PR.offer_or_charge(text=P1, subject=HELD, kind='report', panel_confirm=None, username='payer_r4',
                                       user=dict(PAYER), history=[])
        self.assertEqual((pl['panel_offer']['subject'], pl['panel_offer']['price_label']), (HELD, '$100'))


# ============================================================================ 3
class TheUsersFileIsLeftAlone(unittest.TestCase):
    def test_a_test_process_mirrors_to_a_path_of_its_own(self):
        """Every suite that boots the app in a child loaded the live users document and the app mirrored it over
        the tracked users.json: a 5 MB change in the working tree after each run."""
        tracked = os.path.join(ROOT, 'users.json')
        for env in ({'REGRESSION_TEST_MODE': '1'}, {'PM_REPLAY_CHILD': '1'}, {'PYTEST_CURRENT_TEST': 'x'}):
            got = UM.path_for_process(tracked, environ=env)
            self.assertNotEqual(os.path.abspath(got), os.path.abspath(tracked))
            self.assertFalse(os.path.abspath(got).startswith(os.path.abspath(ROOT) + os.sep))
        self.assertEqual(UM.path_for_process(tracked, environ={}), tracked)          # the app, outside a test, as before
        self.assertEqual(UM.path_for_process(tracked, environ={'REGRESSION_TEST_MODE': '0'}), tracked)

    def test_the_app_takes_its_path_from_there_and_a_child_leaves_the_tracked_file_alone(self):
        src = Path(ROOT, 'app.py').read_text(encoding='utf-8')
        i = src.find("USERS_FILE = os.path.join(os.path.dirname(__file__), 'users.json')")
        self.assertTrue(i > 0)
        self.assertIn('USERS_FILE = _users_mirror_path(USERS_FILE)', src[i:i + 700])
        self.assertEqual(src.count("'users.json')"), src[:i + 700].count("'users.json')") + src[i + 700:].count("'users.json')"))
        # a child that writes its mirror, the way load_users does, never touches the tracked file
        tracked = os.path.join(ROOT, 'users.json')
        before = (os.path.getsize(tracked), os.path.getmtime(tracked)) if os.path.exists(tracked) else None
        code = ("import json, os, sys; sys.path.insert(0, %r); import users_mirror as um; "
                "p = um.path_for_process(%r); open(p, 'w').write(json.dumps({'users': {}})); print(p)") % (ROOT, tracked)
        out = subprocess.run([sys.executable, '-c', code], env=dict(os.environ, PM_REPLAY_CHILD='1'), capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-400:])
        private = out.stdout.strip().splitlines()[-1]
        self.assertNotEqual(os.path.abspath(private), os.path.abspath(tracked))
        self.assertFalse(os.path.exists(private))                                    # removed when the child ended
        after = (os.path.getsize(tracked), os.path.getmtime(tracked)) if os.path.exists(tracked) else None
        self.assertEqual(before, after)

    def test_the_commit_hook_refuses_a_staged_users_file(self):
        hook = Path(ROOT, '.githooks', 'pre-commit').read_text(encoding='utf-8')
        self.assertIn("grep -qE '^users\\.json$'", hook)
        self.assertIn('USERS_JSON_COMMIT', hook)
        self.assertLess(hook.find('users.json is staged'), hook.find('TWIN_GEN='))


if __name__ == '__main__':
    unittest.main(verbosity=1)
