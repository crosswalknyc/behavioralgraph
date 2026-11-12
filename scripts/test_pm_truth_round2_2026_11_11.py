#!/usr/bin/env python3
"""What Prometheus says is true, round two (Jenna 2026-11-11: "these
errors cannot happen. fix and ensure itll be correct going forward").

Six more things found while the first seven were fixed. Every case is
built from the reader's own words and from the records as they stood
(scripts/fixtures/pm_truth_round2_2026_11_11.json).

  1. A part of a question that names nothing rides on the question it
     came with. It never matches a read stored under another subject on
     its words alone, and never a retired read.  -> APartRidesOnItsQuestion
  2. The subject a deck request names is the deck's subject: the name in
     "a deck for mistletoe murders", and the chip the reader taps, reach
     the deck step. Nothing held on it is said plainly; nothing is put
     in its place.                         -> TheDeckIsOnTheSubjectNamed
  3. A list of subjects is a list of names. The lines of a pasted read
     are details of one order, never builds.            -> AListIsNames
  4. A question about the reader's own read is answered about that read,
     never with a description of a product.   -> AQuestionAboutTheRead
  5. The word is banned from decks in its literal senses too, and the
     rewrite is plain correct English.      -> TheLiteralSensesReadPlain
  6. The key surface answers the question and says in one plain line
     that the deck was not built with it.       -> TheKeySurfaceSaysSo

Hermetic: the stub host and in-memory store of test_pm_new_chat_no_bleed.
No model, no S3, no app module, no mail.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import re
import sys
import unittest
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location('pm_no_bleed_scaffold_r2', os.path.join(HERE, 'test_pm_new_chat_no_bleed.py'))
NB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NB)

import insights_ledger as IL  # noqa: E402
import prometheus_analysis as PMA  # noqa: E402
from prometheus import deck_pick as DP  # noqa: E402
from prometheus import deck_subject as DS  # noqa: E402
from prometheus import door_words as DW  # noqa: E402
from prometheus import list_shape as LS  # noqa: E402
from prometheus import multi_ask as MA  # noqa: E402
from prometheus import own_read as OR  # noqa: E402
from prometheus import ride_along as RA  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus import smarter as SM  # noqa: E402
from prometheus.host import host  # noqa: E402

APP, World, USER = NB.APP, NB.World, NB.USER
FX = json.loads(Path(HERE, 'fixtures', 'pm_truth_round2_2026_11_11.json').read_text(encoding='utf-8'))
FX1 = json.loads(Path(HERE, 'fixtures', 'pm_truth_2026_11_11.json').read_text(encoding='utf-8'))
AD, AC, JB, LG = FX['armine_deck'], FX['armine_categories'], FX['jessie_batch'], FX['ledger']
MULTI = FX1['armine_words']['summarize_in_powerpoint']
CHAT_SRC = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
BANNED = re.compile(r"\b(?:synth\w*|model(?:ed|led)?|estimat\w*|pipeline|hostmap|ledger|bucket|panel|queue|worker|lane|"
                    r"claude|anthropic|llm|prompt)\b|\u2014", re.I)


def _ask(w, text, *, tid=None, history=None, page=None, surface='analyze', extra=None):
    w.calls.clear()
    body = {'text': text, 'client': 'dashboard', 'surface': surface, '_client_surface_sent': surface}
    if tid:
        body['thread_id'] = tid
    if history is not None:
        body['history'] = list(history)
    if page is not None:
        body['context'] = page
    body.update(extra or {})
    with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
        return S.ask(dict(NB.ARMINE), body)


# ============================================================================ 1
class APartRidesOnItsQuestion(unittest.TestCase):
    def setUp(self):
        self._load = IL._load_index
        self.doc = {'subjects': copy.deepcopy(LG['subjects'])}
        IL._load_index = lambda force=False: self.doc

    def tearDown(self):
        IL._load_index = self._load

    def test_the_six_words_named_nothing_and_came_back_with_another_audience(self):
        """The canary's third part, "how is the audience like?", with no thread: the lookup resolved the bucket
        filed under "The" (every question carries the word), found nothing there, scanned every subject for the
        same six words and handed back the Las Culturistas read, which had been retired for a wrong headline."""
        q = LG['question']
        self.assertTrue(IL.names_no_subject(q))
        stored = self.doc['subjects']['las_culturistas_culture_awards_viewers']['entries'][0]
        self.assertEqual(stored['qn'], IL.normalize_question(q))
        self.assertTrue(stored.get('retired'))                              # the record as it stood
        out = IL.consult(question=q)
        self.assertIsNone(out.get('exact'))
        self.assertIsNone(out.get('subject'))                               # nothing named, nothing resolved
        self.assertEqual(out.get('block'), '')
        # the bucket filed under an ordinary word is no one's subject
        self.assertIsNone(IL._resolve_subject(self.doc, question=q))
        self.assertIsNone(IL._resolve_subject(self.doc, question='what is the top brand for the Reba audience'))

    def test_a_retired_read_is_never_the_match_and_words_alone_never_cross_subjects(self):
        q = LG['question']
        live = copy.deepcopy(self.doc)
        live['subjects']['las_culturistas_culture_awards_viewers']['entries'][0].pop('retired')
        live['subjects']['cruel_summer_viewers'] = {'subject': 'Cruel Summer Viewers', 'entries': [
            {'k': 'cruel_summer_viewers|brands|any|any', 'subject': 'Cruel Summer Viewers', 'family': 'brands', 'q': 'top brands for cruel summer viewers',
             'qn': 'top brands for cruel summer viewers', 'ts': '2026-11-10T20:00:00Z', 'route': 'page_analysis', 'reply': 'Beauty leads.'}]}
        IL._load_index = lambda force=False: live
        # even live, the words alone never reach another subject's read: with no subject, and on another subject
        self.assertIsNone(IL.consult(question=q).get('exact'))
        other = IL.consult(question=q, subject='Cruel Summer Viewers')
        self.assertEqual(other.get('subject'), 'Cruel Summer Viewers')
        self.assertIsNone(other.get('exact'))
        # on its own subject the same question keeps its answer (one question, one read)
        same = IL.consult(question=q, subject='Las Culturistas Culture Awards Viewers')
        self.assertTrue(same.get('exact') and same['exact']['reply'].startswith('This is a young'))
        # retired again: not even on its own subject
        IL._load_index = lambda force=False: self.doc
        self.assertIsNone(IL.consult(question=q, subject='Las Culturistas Culture Awards Viewers').get('exact'))
        self.assertEqual(IL._find_exact_anywhere(self.doc['subjects'], q), (None, None))

    def test_a_question_that_names_its_own_audience_still_finds_its_read_under_any_filing(self):
        """The rule the cross-subject scan was written for (2026-08-27) holds: the toy question names its own
        audience and replays whatever name its read was filed under."""
        toy = 'what are parents of kids 4-7 buying in terms of toy categories'
        self.assertFalse(IL.names_no_subject(toy))
        doc = {'subjects': {'paw_patrol_series': {'subject': 'Paw Patrol Series', 'entries': [
            {'k': 'paw_patrol_series|toys|any|any', 'subject': 'Paw Patrol Series', 'family': 'toys', 'q': toy, 'qn': IL.normalize_question(toy),
             'ts': '2026-11-10T20:00:00Z', 'route': 'reasoned_metrics', 'reply': 'Building sets lead at 31.4%.'}]}}}
        IL._load_index = lambda force=False: doc
        hit = IL.consult(question=toy)
        self.assertEqual((hit.get('exact') or {}).get('reply'), 'Building sets lead at 31.4%.')
        for named in ('How big is the Reba audience?', 'what is the churn on Landman', 'top brands for Gilmore Girls viewers'):
            self.assertFalse(IL.names_no_subject(named), named)
        for bare in ('how is the audience like?', 'what does the audience look like?', 'who are they?', 'Can we give some examples?',
                     'what do they buy', 'where else do they watch'):
            self.assertTrue(IL.names_no_subject(bare), bare)

    def test_the_part_runs_on_the_subject_of_the_question_before_it_with_nothing_on_screen(self):
        """Armine's message through the door with no page and no thread (the canary's shape): the third part
        reaches the read step bound to the first question's subject, not left to find one."""
        w = World()
        question = RA.split(MULTI)['question']
        env, _st = _ask(w, question, history=[])
        parts = {c[1]: c[2] for c in w.calls if c[0] == 'analyze'}
        third = next(b for t, b in parts.items() if t.strip().lower().startswith('how is the audience like'))
        self.assertTrue(MA.points_back('how is the audience like?'))
        self.assertEqual(third.get('bind_subject'), 'Digital original content categories')
        self.assertIsNone(third.get('page_context'))
        first = next(b for t, b in parts.items() if 'What type of categories do best' in t)
        self.assertFalse(first.get('bind_subject'))                         # the question that names its subject is left alone

    def test_with_a_page_open_the_part_still_rides_on_its_question_never_on_the_page(self):
        w = World()
        question = RA.split(MULTI)['question']
        page = {'view': 'profile-iq', 'primary': {'s3_key': 'Las_Culturistas_Culture_Awards_Viewers_11_10_2026_20_01.csv',
                                                   'name': 'Las Culturistas Culture Awards Viewers'},
                'view_context': {'view_id': 'profileIQ', 'view_title': 'Las Culturistas Culture Awards Viewers', 'data': {}}}
        _ask(w, question, history=[], page=page)
        third = next(c[2] for c in w.calls if c[0] == 'analyze' and c[1].strip().lower().startswith('how is the audience like'))
        self.assertEqual(third.get('bind_subject'), 'Digital original content categories')
        self.assertFalse((third.get('page_context') or {}).get('primary'))


# ============================================================================ 2
def _resolver(library):
    """The deck step's library resolver, in its own order: a subject the words name, else the page."""
    def resolve(text, ctx):
        wanted = (PMA.extract_deck_brief(text) or {}).get('subject') or ''
        fold = lambda s: re.sub(r'[^a-z0-9]+', '', str(s or '').lower())  # noqa: E731
        if wanted:
            hit = next((e for e in library if ' - ' not in e['display_name'] and fold(e['display_name']) == fold(wanted)), None)
            if hit:
                return {'ctx': {'primary': {'s3_key': hit['s3_key'], 'name': hit['display_name']}}, 'subject': hit['subject'],
                        'partner': '', 'clarify': None}, None
        if ctx and ctx.get('primary'):
            return {'ctx': ctx, 'subject': ctx['primary']['name'], 'partner': '', 'clarify': None, 'used_open_page': True}, None
        return None, ({'success': False, 'error': 'no subject'}, 400)
    return resolve


class TheDeckIsOnTheSubjectNamed(unittest.TestCase):
    def test_her_words_named_the_profile_built_in_her_own_thread(self):
        """23:23:38 UTC, Cruel Summer Viewers on screen, Mistletoe Murders Viewers finished in this thread at
        23:06: "can we build a deck for mistletoe murders". The old reading found no subject in those words."""
        words, hist, page = AD['words'], AD['thread_before'], AD['page']
        self.assertEqual(words, 'can we build a deck for mistletoe murders')
        self.assertEqual(PMA.extract_deck_brief(words), {'subject': '', 'partner': ''})     # the cause: the name was never read
        self.assertIn('Which subject should the deck be on', AD['asked_back'])
        self.assertIn('Cruel Summer Viewers', AD['confirm_that_went_out'])                  # what she was told after tapping Mistletoe
        self.assertEqual(DS.for_name(words), 'mistletoe murders')
        self.assertEqual(DS.named(words, page, hist, []), 'Mistletoe Murders Viewers')      # the thread alone settles it
        self.assertEqual(DS.named(words, page, [], AD['library']), 'Mistletoe Murders Viewers')   # so does the library alone
        self.assertEqual(DS.pick({}, words, page, hist, AD['library']), 'Mistletoe Murders Viewers')
        self.assertIsNone(DS.question({}, words, page, hist, AD['library']))                # nothing to ask

    def test_the_deck_step_builds_on_that_profile_and_never_on_the_screen(self):
        words, hist, page = AD['words'], AD['thread_before'], AD['page']
        pick = DS.pick({}, words, page, hist, AD['library'])
        res, err, msg = DP.settle(pick, words, page, hist, _resolver(AD['library']))
        self.assertFalse(err or msg)
        self.assertEqual(res['subject'], 'Mistletoe Murders Viewers')
        self.assertFalse(res.get('used_open_page'))
        self.assertEqual(res['ctx']['primary']['s3_key'], 'Mistletoe_Murders_Viewers_11_11_2026_22_44.csv')
        self.assertEqual(DP.confirm_line(res), 'Building the Mistletoe Murders Viewers insights deck. The download link lands here when it is ready.')
        # before the fix the same step, given no pick, resolved to the page
        old, _e, _m = DP.settle('', words, page, hist, _resolver(AD['library']))
        self.assertTrue(old.get('used_open_page') and old['subject'] == 'Cruel Summer Viewers')

    def test_the_chip_she_tapped_reaches_the_deck_step_through_the_door(self):
        """23:23:45 UTC: she tapped "On Mistletoe Murders Viewers". The widget sent that subject with the confirm;
        the door passed the confirm and dropped the subject, and the deck step took the page."""
        self.assertEqual(AD['tap'], 'On Mistletoe Murders Viewers')
        w = World()
        tid = w.new_thread()
        env, _st = _ask(w, AD['words'], tid=tid, history=AD['thread_before'][-6:], page=AD['page'], surface='deck',
                        extra={'bind_subject': 'Mistletoe Murders Viewers', 'confirm_open_screen': True, 'angle': AD['words']})
        deck = [c for c in w.calls if c[0] == 'deck']
        self.assertEqual(len(deck), 1)
        body = deck[0][2]
        self.assertEqual(body.get('bind_subject'), 'Mistletoe Murders Viewers')
        self.assertTrue(body.get('confirm_open_screen'))
        self.assertEqual(DS.pick(body, AD['words'], AD['page'], AD['thread_before'], AD['library']), 'Mistletoe Murders Viewers')
        # and "Yes, Cruel Summer Viewers" still means the page
        self.assertEqual(DS.pick({'bind_subject': 'Cruel Summer Viewers', 'confirm_open_screen': True}, 'Build a deck from this data',
                                 AD['page'], AD['thread_before'], AD['library']), 'Cruel Summer Viewers')

    def test_nothing_held_on_the_name_is_said_plainly_and_nothing_is_put_in_its_place(self):
        words, page = AD['words'], AD['page']
        lib = [e for e in AD['library'] if not e['display_name'].startswith('Mistletoe')]
        pick = DS.pick({}, words, page, [], lib)
        self.assertEqual(pick, 'Mistletoe Murders')
        res, err, msg = DP.settle(pick, words, page, [], _resolver(lib))
        self.assertIsNone(res)
        self.assertIn('I do not have a profile or a finished read on Mistletoe Murders', msg)
        self.assertNotIn('Cruel Summer', msg)
        self.assertIsNone(BANNED.search(msg))
        # written as a name, with another audience open and nothing held: one short question, the chips carry the subject
        hints = []
        q = DS.question({}, 'can we build a deck for Mistletoe Murders', page, [], lib, hint=lambda **k: hints.append(k))
        self.assertEqual(hints, [{'route': 'deck', 'outcome': 'asked_deck_for_whom', 'subject': 'Cruel Summer Viewers'}])
        self.assertIn('I do not hold a profile or a finished read on Mistletoe Murders', q['reply'])
        self.assertEqual([o['subject'] for o in q['memory_confirm']['options']], ['Cruel Summer Viewers', 'Mistletoe Murders'])
        self.assertTrue(all(o.get('deck') for o in q['memory_confirm']['options']))
        self.assertIsNone(BANNED.search(q['reply']))
        self.assertIsNone(DS.question({'confirm_open_screen': True, 'bind_subject': 'Cruel Summer Viewers'},
                                      'can we build a deck for Mistletoe Murders', page, [], lib))      # asked once, never twice

    def test_a_buyer_a_recipient_and_a_pointer_are_not_the_subject(self):
        page, lib = AD['page'], AD['library']
        # a brand with another audience open is who the deck is made for, as it always was
        self.assertEqual(DS.named('Build a deck for Nike', page, [], lib), '')
        self.assertIsNone(DS.question({}, 'Build a deck for Nike', page, [], lib))
        # with nothing on screen the same words name the Nike profile; a title or a person is always the subject
        self.assertEqual(DS.named('Build a deck for Nike', None, [], lib), 'Nike')
        self.assertEqual(DS.named('Build a deck for Noah Wyle', page, [], lib), 'Noah Wyle')
        for words in ('build a deck for my boss', 'make slides for the Monday meeting', 'build a deck for this audience',
                      'Build a deck from this data', 'build a deck for new business', 'a deck for advertisers', 'Summarize in powerpoint',
                      'build a deck for cruel summer'):
            self.assertEqual(DS.named(words, page, AD['thread_before'], lib), '', words)
            self.assertIsNone(DS.question({}, words, page, AD['thread_before'], lib), words)

    def test_the_deck_step_and_the_door_are_wired(self):
        i_chip, i_pick, i_ask, i_settle = (CHAT_SRC.find(s) for s in (
            "if _bind_pick and body.get('confirm_open_screen'):", '_deck_subject.pick_or_ask(body, text, ctx, history',
            'return jsonify(_dp_q)', '_deck_pick.settle(\n                _dp_pick'))
        self.assertTrue(0 < i_chip < i_pick < i_ask < i_settle, (i_chip, i_pick, i_ask, i_settle))
        hints = []
        self.assertEqual(DS.pick_or_ask({}, AD['words'], AD['page'], AD['thread_before'], AD['library'], hint=lambda **k: hints.append(k)),
                         ('Mistletoe Murders Viewers', None))
        pick, ask = DS.pick_or_ask({}, 'can we build a deck for Mistletoe Murders', AD['page'], [], [], hint=lambda **k: hints.append(k))
        self.assertTrue(pick == '' and ask and len(hints) == 1)
        self.assertIn("for k in ('angle', 'confirm_open_screen', 'bind_subject'):", Path(ROOT, 'prometheus', 'service.py').read_text(encoding='utf-8'))
        self.assertLess(len(CHAT_SRC.splitlines()), 19900)                                  # the line ratchet holds


# ============================================================================ 3
def _app_function(name, extra=()):
    """One of the app's list helpers, run from its source (the app module is never imported here)."""
    src = Path(ROOT, 'app.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    nodes = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    assigns = {n.targets[0].id: n for n in tree.body
               if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
    ns = {'re': re, 'SYNTH_CHAT_BATCH_MAX': 25}

    def load(nm):
        node = nodes.get(nm) or assigns.get(nm)
        if node is None:
            raise KeyError(nm)
        exec(compile(ast.Module([node], []), 'app.py', 'exec'), ns)

    def call(*a):
        for _ in range(60):
            try:
                if name not in ns:
                    load(name)
                return ns[name](*a)
            except NameError as e:
                load(re.search(r"name '(\w+)' is not defined", str(e)).group(1))
        raise AssertionError('could not load ' + name)
    return call


class AListIsNames(unittest.TestCase):
    detect = staticmethod(_app_function('_detect_batch_subjects'))

    def test_jessies_pasted_order_was_drafted_as_two_builds(self):
        """22:12 UTC: one order, with the window and the placement count of the read above pasted under it. The
        reply was "Brief drafted: Window: Oct 25, 2024 to Nov 10, 2026 (new build); Brief drafted: Paramount+
        Placements Carried Into the Build: 49 (new build)"."""
        words = JB['words']
        self.assertTrue(words.startswith('run a NEW profile IQ profile on this audience: On Georgie & Mandy'))
        split = self.detect(words)
        self.assertEqual(split, JB['drafted'])                              # the cause: every capitalized line was a subject
        self.assertTrue(all(LS.detail_line(s) for s in split))
        out = LS.settle(words, split)
        self.assertEqual(out['subjects'], [])                               # no list: the single brief reads the order whole
        self.assertIsNone(out['ask'])
        self.assertEqual(out['dropped'], JB['drafted'])
        self.assertTrue(LS.asks_for_one(words))

    def test_a_real_list_is_untouched(self):
        for words in ('run profiles on vizio, samsung tv and lg tv owners',
                      'I need 3 profiles. they are for:\n1. Reba McEntire\n2. Dolly Parton\n3. Shania Twain',
                      'run profiles on:\nMission: Impossible\nAvatar: The Last Airbender\n9-1-1: Lone Star\n1883'):
            split = self.detect(words)
            self.assertGreaterEqual(len(split), 3, words)
            out = LS.settle(words, split)
            self.assertEqual(out['subjects'], split, words)
            self.assertIsNone(out['ask'])
        for name in ('Mission: Impossible', 'Star Trek: Picard', '2001: A Space Odyssey', 'Catch-22', 'The 100', '1883', 'Total Recall',
                     'Source Code', 'Notes on a Scandal', "Georgie & Mandy's First Marriage Viewers - Paramount+"):
            self.assertFalse(LS.detail_line(name), name)
        for detail in ('Window: Oct 25, 2024 to Nov 10, 2026.', 'Paramount+ placements carried into the build: 49', 'Cost: $300 on approval',
                       'Estimated audience: about 4,644,134 to 6,511,764 US people (1.70% incidence)', 'Build time: ~20 min', 'Total: 49',
                       'Unique US viewers, all seasons: 5,612,483', 'Subject: Mistletoe Murders, the series', '\u2022'):
            self.assertTrue(LS.detail_line(detail), detail)

    def test_a_list_with_a_window_under_it_keeps_its_names_and_drops_the_window(self):
        words = 'run profiles on:\nNike\nAdidas\nPuma\nWindow: Jan 1, 2025 to Dec 31, 2025'
        out = LS.settle(words, ['Nike', 'Adidas', 'Puma', 'Window: Jan 1, 2025 to Dec 31, 2025'])
        self.assertEqual(out['subjects'], ['Nike', 'Adidas', 'Puma'])
        self.assertIsNone(out['ask'])

    def test_when_it_cannot_tell_it_asks_once_and_drafts_nothing(self):
        words = 'run a profile on this audience:\nNike\nAdidas\nWindow: Jan 1, 2025 to Dec 31, 2025'
        out = LS.settle(words, ['Nike', 'Adidas', 'Window: Jan 1, 2025 to Dec 31, 2025'])
        self.assertEqual(out['subjects'], [])
        ask = out['ask']
        self.assertTrue(ask['guidance'] and not ask['success'])
        self.assertTrue(ask['error'].startswith('Is this one profile or 2 separate profiles?'))
        self.assertEqual(len(ask['followups']), 2)
        self.assertIsNone(BANNED.search(ask['error']))

    def test_the_build_step_holds_the_splitter_to_it(self):
        i = CHAT_SRC.find('_list_shape.names(text, [] if _single_compound else _H._detect_batch_subjects(text))')
        j = CHAT_SRC.find('return _synth_chat_interpret_batch(', i)
        self.assertTrue(0 < i < j)                                          # the list is settled before any batch is drafted
        self.assertIn("return jsonify(_ls_ask)", CHAT_SRC[i:j])
        self.assertEqual(LS.names(JB['words'], JB['drafted']), ([], None))
        self.assertEqual(CHAT_SRC.count('_H._detect_batch_subjects(text)'), 1)             # no path around it


# ============================================================================ 4
class AQuestionAboutTheRead(unittest.TestCase):
    def test_her_question_pointed_at_the_read_and_got_a_product_description(self):
        """21:05 UTC, one turn after a read on the categories: "Can we have all these categories Comedy,
        lifestyle/Arts & Culture, Young Adult, Horror, Faith, Drama. Also list example sof each"."""
        words, hist = AC['words'], AC['thread_before']
        self.assertTrue(AC['reply_that_went_out'].startswith('Yes. A Profile IQ profile builds the audience'))
        cap = SM.capability_ask(words)
        self.assertTrue(cap and cap['reply'] == AC['reply_that_went_out'])                  # the cause: can-we with no product named
        self.assertFalse(OR.names_a_product(words))
        self.assertTrue(OR.points_at_thread(words))
        self.assertTrue(OR.about_thread(words, hist))

    def test_through_the_door_it_reaches_the_answer_step_not_the_description(self):
        w = World()
        tid = w.new_thread()
        env, _st = _ask(w, AC['words'], tid=tid, history=AC['thread_before'], page=None)
        self.assertNotIn('A Profile IQ profile builds', env.get('text') or '')
        self.assertNotEqual((env.get('decision') or {}).get('reason'), 'capability')
        self.assertNotEqual((env.get('decision') or {}).get('reason'), 'capability_question')
        self.assertTrue([c for c in w.calls if c[0] == 'analyze'])                          # the read step was asked

    def test_what_points_at_the_thread_and_what_asks_about_a_product(self):
        hist = AC['thread_before']
        for words in ('can you do the same for TikTok?', 'Can we get examples of each?', 'can we see all of them by age?',
                      'could you list those with their top titles', 'can we see this by age?'):
            self.assertTrue(OR.about_thread(words, hist), words)
        # a question about a product is still a question about a product, read in the thread or not
        for words in ('Can we track a campaign?', 'is there a way to see churn?', 'can we build a profile for a tour?'):
            self.assertFalse(OR.about_thread(words, hist), words)
            self.assertTrue(SM.capability_ask(words), words)
        # with nothing in the thread there is nothing to point at
        self.assertFalse(OR.about_thread(AC['words'], []))
        self.assertFalse(OR.about_thread('Can we have all these categories?', [{'role': 'user', 'text': 'hi'}]))


# ============================================================================ 5
class TheLiteralSensesReadPlain(unittest.TestCase):
    def test_deck_copy_says_the_plain_thing_the_word_meant(self):
        """A Get Real deck read "the widest front store": every leftover use of the word became "store"."""
        for src, want in (
                ("The profile's widest front door is Walmart.", "The profile's widest entrance is Walmart."),
                ('Target leads on door count.', 'Target leads on store count.'),
                ('The brand is in 4,213 doors nationwide.', 'The brand is in 4,213 stores nationwide.'),
                ('Sold in 312 retail doors.', 'Sold in 312 retail stores.'),
                ('a three-door audience', 'a three-store audience'),
                ('Next door neighbors', 'Nearby neighbors'),
                ('It gives the brand a door into the category.', 'It gives the brand a way into the category.'),
                ('Her posts opened the door to new viewers.', 'Her posts brought new viewers.')):
            got = DW.scrub_read(src)
            self.assertEqual(got, want)
            self.assertFalse(DW.has_door(got))
        for never in ('front store', 'Next store', 'store-to-store', 'garage store'):
            for src in ('the widest front door', 'Next door neighbors', 'A door-to-door sales push', 'The garage door category grew.'):
                self.assertNotIn(never, DW.scrub_read(src))

    def test_a_name_is_still_held_aside_and_a_chat_reply_is_still_untouched(self):
        for name in ('AD Open Door reaches 4,213 stores.', 'The Doors and Three Doors Down index high.', 'Door County tourism',
                     'Meet at The Door NYC after the show.'):
            self.assertEqual(DW.scrub_read(name), name)
        self.assertEqual(DW.scrub_read('AD Open Door reaches 4,213 doors.'), 'AD Open Door reaches 4,213 stores.')
        self.assertEqual(DW.scrub_filename('AD_Open_Door_viewers.csv'), 'AD_Open_Door_viewers.csv')
        self.assertEqual(DW.scrub_filename('Retail_door_mix.csv'), 'Retail_store_mix.csv')
        chat = {'success': True, 'action': 'answer', 'reply': 'Walmart is the front door for this audience, with 4,213 doors.'}
        self.assertEqual(DW.scrub_payload(dict(chat))['reply'], chat['reply'])              # the rule is a deck rule


# ============================================================================ 6
class TheKeySurfaceSaysSo(unittest.TestCase):
    def test_the_question_is_answered_and_the_line_is_plain(self):
        q, kinds = RA.key_split(MULTI)
        self.assertEqual(kinds, ['deck'])
        self.assertNotRegex(q, r"(?i)power\s?point|summari[sz]e")
        self.assertIn('how is the audience like?', q)
        line = RA.key_line(kinds, 'Digital original content categories')
        self.assertEqual(line, 'The PowerPoint was not built with this answer. Send "Build a deck on Digital original content categories" '
                               'as its own request and it comes back as a job with the file.')
        self.assertIsNone(BANNED.search(line))
        self.assertIsNone(BANNED.search(RA.key_line(['deck', 'email'])))
        self.assertIn('Nothing was emailed', RA.key_line(['email']))
        v1 = {'kind': 'answer', 'answer': {'text': 'Comedy leads at 4,713,882 US viewers.', 'facts': [], 'followups': []}}
        out = RA.key_note(copy.deepcopy(v1), kinds, 'Digital original content categories')
        self.assertEqual(set(out), set(v1))                                 # no field is added to the response
        self.assertEqual(set(out['answer']), set(v1['answer']))
        self.assertTrue(out['answer']['text'].startswith('Comedy leads at 4,713,882 US viewers.'))
        self.assertTrue(out['answer']['text'].endswith(line))
        self.assertEqual(RA.key_note(copy.deepcopy(out), kinds, 'Digital original content categories')['answer']['text'], out['answer']['text'])

    def test_a_read_still_running_carries_the_line_when_it_finishes(self):
        w = World()
        job = {'kind': 'job', 'job': {'id': 'a1b2c3d4e5f6', 'status': 'working'}}
        self.assertEqual(RA.key_note(copy.deepcopy(job), ['deck'], 'Reba McEntire'), job)   # a job is returned as it is
        RA.key_hold('a1b2c3d4e5f6', ['deck'], 'Reba McEntire')
        held = RA.key_held('a1b2c3d4e5f6')
        self.assertEqual((held['kinds'], held['subject']), (['deck'], 'Reba McEntire'))
        self.assertTrue(w.get(RA.KEY_PREFIX + 'a1b2c3d4e5f6.json'))
        done = RA.key_note({'kind': 'answer', 'answer': {'text': 'Reba reaches 3,214,907 US people.'}}, held['kinds'], held['subject'])
        self.assertIn('Send "Build a deck on Reba McEntire" as its own request', done['answer']['text'])
        self.assertIsNone(RA.key_held('ffffffffffff'))

    def test_a_deck_asked_on_its_own_and_a_csv_are_left_alone(self):
        for alone in ('Build a deck on Nike', 'Summarize in powerpoint', 'How big is the Reba audience?',
                      'How big is the Reba audience? Send it as a CSV.'):
            self.assertEqual(RA.key_split(alone), (alone, []), alone)
        self.assertEqual(RA.key_line(['csv']), '')

    def test_the_key_door_takes_no_new_field(self):
        src = Path(ROOT, 'prometheus', 'partner_api.py').read_text(encoding='utf-8')
        self.assertIn('text, _ride_kinds = _ride.key_split(text)', src)
        self.assertIn('_ride.key_held(job_id)', src)
        # the request is read exactly as before: the same fields, nothing about decks or deliverables
        fields = set(re.findall(r"body\.get\('([a-z_]+)'\)", src))
        self.assertFalse({f for f in fields if re.search(r"deck|deliver|pptx|ride|force|override|mode_", f)}, fields)


if __name__ == '__main__':
    unittest.main(verbosity=1)
