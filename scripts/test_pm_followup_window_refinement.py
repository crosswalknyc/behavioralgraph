#!/usr/bin/env python3
"""A follow-up is the ask before it with one thing changed (Grace Yin,
Amazon, 2026-11-18 01:08 UTC; Jenna: "incorrectly answering").

Her thread:

    "for customers who watched TONY,2026 coming-of-age dark comedy
     docudrama film Tony, starring Dominic Sessa as a 19-year-old
     Anthony Bourdain ... is streaming on Amazon Prime Video (released
     on October 26, 2026). can you give me the demo breakdown of the
     audience>"
        "A 6-year read of Amazon Prime Video needs one profile per
         year ..."                      (the 19 in "19-year-old")
    "but the title just launched SVOD on Prime video in 2026 so i just
     want recent data after it launched"
        "I could not find But The Title Just Launched Svod as a brand,
         person, or title, so I have not set up a build for it."

What went wrong on the second turn, in order: the draft step read the
thread and drafted Tony; the identity backstop saw a name that was not
in THAT turn's words, called it a lookalike and reset it to the head of
her sentence with capitals on; the name check looked the sentence up
and refused with the sentence in the reply; the copy of the question
that goes to the team carried the sentence as its subject.

What holds it now (prometheus.followup_refine):

  * a turn that points back at the thread names nothing itself: the
    thread's subject carries over, with the window, platform or cut she
    changed;
  * "after it launched" is the launch day she gave in the thread
    through the last full day, echoed on the brief; with no day given
    and none resolved, one short question for the date;
  * the identity backstop reads her earlier words in the thread as her
    own words, and never takes a sentence for a title;
  * the name check never looks a sentence up and never says one back;
    with nothing in the thread to point at it asks which title;
  * an age is never a span of years.

Hermetic: no model, no S3, no mail, no app boot. The module checks are
pure; the identity backstop runs from app.py's own source with its
research call stubbed; the door check replays her thread through the
recording host in a child process (prometheus.replay) with the model
canned, and is skipped with a note when that host is not on the path.
The decision is read from the thread's own turns: nothing here, and
nothing in the code under test, reads a flag from the request.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import followup_refine as F  # noqa: E402
from prometheus import match_question as MQ  # noqa: E402
from prometheus import named_subject as NS  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402

FIRST = ('for customers who watched TONY,2026 coming-of-age dark comedy docudrama film Tony, starring Dominic Sessa as a '
         '19-year-old Anthony Bourdain as he stumbles into a chaotic Provincetown restaurant kitchen, is streaming on '
         'Amazon Prime Video (released on October 26, 2026). can you give me the demo breakdown of the audience>')
PACKAGE = ('Included with your plan. A 6-year read of Amazon Prime Video needs one profile per year - each year\'s audience '
           'is measured in its own window, and the file on hand covers a single window only. I will not stretch one window '
           'across 6 years.\n\nThe package: 5 year profiles (2021, 2022, 2023, 2024 and 2025) at $300 each.\nAlready on the '
           'shelf: 2026 - those years ride free.')
HERS = 'but the title just launched SVOD on Prime video in 2026 so i just want recent data after it launched'
MINTED = 'But The Title Just Launched Svod'
REFUSAL = 'as a brand, person, or title'
TODAY = dt.date(2026, 11, 18)


def thread(first=FIRST, reply=PACKAGE):
    """Her thread as the dashboard sent it: its own turns, no meta."""
    return [{'role': 'user', 'text': first, 'ts': '2026-11-18T01:07:29.839Z'},
            {'role': 'agent', 'text': reply, 'ts': '2026-11-18T01:07:43.156Z'}]


def binder(draft, text, chat_history=None, decision=None, question=None):
    """The host's inline window binder, the part this needs: the window
    the words state, written on the draft and marked explicit."""
    from prometheus import inline_dates as I
    f = I.parse_inline_window(text, today=TODAY)
    if not f:
        return False
    draft['date_range'] = {'start': f['start'], 'end': f['end']}
    draft['date_range_explicit'] = True
    draft['date_window_label'] = I.window_label(f['start'], f['end'])
    return True


class HerSentence(unittest.TestCase):
    def test_it_refines_the_title_she_asked_about(self):
        fu = F.resolve(HERS, thread(), today=TODAY)
        self.assertIsNotNone(fu)
        self.assertEqual(fu['kind'], 'refinement')
        self.assertEqual(fu['subject'], 'Tony')                      # typed TONY and Tony: shown as Tony
        self.assertEqual(fu['platform'], 'Amazon Prime Video')       # carried over, not changed
        self.assertEqual(fu['question'], FIRST)                      # the ask it refines
        self.assertEqual(fu['change']['window_kind'], 'launch')
        self.assertEqual(fu['change']['platform'], '')               # Prime Video said again changes nothing
        self.assertEqual(fu['launch_date'], dt.date(2026, 10, 26))   # "released on October 26, 2026", her words
        self.assertFalse(fu['needs_launch_date'])

    def test_the_window_is_the_launch_day_through_the_last_full_day(self):
        fu = F.resolve(HERS, thread(), today=TODAY)
        draft = {'subject': 'Tony (2026)', 'decision': 'new_build'}
        state, ask = F.settle_window(draft, fu, '', bind=binder, decision='new_build', today=TODAY)
        self.assertEqual((state, ask), ('confident', None))
        self.assertEqual(draft['date_range'], {'start': '2026-10-26', 'end': '2026-11-17'})
        self.assertTrue(draft['date_range_explicit'])                # the standing default can never write over it
        self.assertIn('Oct 26', draft['date_window_label'])          # echoed on the brief

    def test_the_window_holds_when_the_binder_is_missing_or_wrong(self):
        fu = F.resolve(HERS, thread(), today=TODAY)
        for bind in (None, lambda *a, **k: None, lambda *a, **k: object()):
            draft = {'subject': 'Tony (2026)', 'decision': 'new_build'}
            state, ask = F.settle_window(draft, fu, 'ask', bind=bind, decision='new_build', today=TODAY)
            self.assertEqual((state, ask), ('confident', None))
            self.assertEqual(draft['date_range'], {'start': '2026-10-26', 'end': '2026-11-17'})

    def test_no_day_in_the_thread_is_one_short_question_never_a_refusal(self):
        first = FIRST.replace(' (released on October 26, 2026)', '')
        fu = F.resolve(HERS, thread(first), today=TODAY)
        self.assertEqual((fu['kind'], fu['subject'], fu['launch_date'], fu['needs_launch_date']),
                         ('refinement', 'Tony', None, True))
        draft = {'subject': 'Tony', 'decision': 'new_build', 'platform': 'Amazon Prime Video'}
        state, ask = F.settle_window(draft, fu, '', bind=binder, decision='new_build', today=TODAY)
        self.assertEqual(ask, 'Happy to. What date did Tony go live on Amazon Prime Video? '
                              'I will read from that day forward, through the most recent full day.')
        self.assertNotIn('date_range', draft)
        # a window the draft step resolved with confidence stands: nothing is asked
        self.assertEqual(F.settle_window(dict(draft), fu, 'confident', bind=binder, decision='new_build', today=TODAY),
                         ('confident', None))

    def test_her_reply_to_that_question_is_the_launch_day(self):
        first = FIRST.replace(' (released on October 26, 2026)', '')
        h = thread(first) + [{'role': 'user', 'text': HERS},
                             {'role': 'agent', 'text': F.launch_question('Tony', 'Amazon Prime Video')}]
        for reply in ('October 26, 2026', 'it went live Oct 26', '10/26/2026'):
            fu = F.resolve(reply, h, today=TODAY)
            self.assertEqual((fu['kind'], fu['subject'], fu['launch_date']), ('refinement', 'Tony', dt.date(2026, 10, 26)), reply)
        self.assertIsNone(F.resolve('not sure, late October I think', h, today=TODAY))   # no day: the draft step reads it

    def test_the_reader_agreeing_changes_nothing(self):
        # production's Reader found TONY for this turn from the thread; the name is not in these words
        fu = F.resolve(HERS, thread(), today=TODAY, reading={'subjects': [{'as_written': 'TONY', 'kind': 'title'}]})
        self.assertEqual((fu['kind'], fu['subject']), ('refinement', 'Tony'))

    def test_the_title_said_again_is_still_the_same_ask(self):
        fu = F.resolve('but Tony just launched on Prime Video so I only want data after it launched', thread(), today=TODAY)
        self.assertEqual((fu['kind'], fu['subject'], fu['change']['window_kind']), ('refinement', 'Tony', 'launch'))


class OtherFollowUps(unittest.TestCase):
    def test_same_thing_but_for_2025(self):
        fu = F.resolve('same thing but for 2025', thread(), today=TODAY)
        self.assertEqual((fu['kind'], fu['subject'], fu['platform']), ('refinement', 'Tony', 'Amazon Prime Video'))
        self.assertEqual(fu['change']['window'], ('2025-01-01', '2025-12-31'))

    def test_and_what_about_on_netflix(self):
        fu = F.resolve('and what about on Netflix', thread(), today=TODAY)
        self.assertEqual((fu['kind'], fu['subject']), ('refinement', 'Tony'))
        self.assertEqual((fu['change']['platform'], fu['platform']), ('Netflix', 'Netflix'))     # the one thing changed
        self.assertIsNone(fu['change']['window'])                                                  # the window carries over

    def test_just_the_last_90_days(self):
        fu = F.resolve('just the last 90 days', thread(), today=TODAY)
        self.assertEqual((fu['kind'], fu['subject'], fu['platform']), ('refinement', 'Tony', 'Amazon Prime Video'))
        a, b = fu['change']['window']
        self.assertEqual((dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days, 90)

    def test_more_of_the_class(self):
        for q, key, want in (('for Q4 2025 instead', 'window', ('2025-10-01', '2025-12-31')),
                             ('actually make it the last 30 days', 'window', ('2026-10-19', '2026-11-18')),
                             ('and on Hulu?', 'platform', 'Hulu'), ('and the same for Hulu', 'platform', 'Hulu'),
                             ('only women', 'cut', 'women')):
            fu = F.resolve(q, thread(), today=TODAY)
            self.assertIsNotNone(fu, q)
            self.assertEqual((fu['kind'], fu['subject'], fu['change'][key]), ('refinement', 'Tony', want), q)

    def test_an_earlier_follow_up_is_passed_over_for_the_ask_it_refined(self):
        h = thread() + [{'role': 'user', 'text': HERS}, {'role': 'agent', 'text': 'New Tony Viewers on Amazon Prime Video profile, ready to run.'}]
        fu = F.resolve('and what about on Netflix', h, today=TODAY)
        self.assertEqual((fu['subject'], fu['question']), ('Tony', FIRST))


class ANewSubjectIsANewAsk(unittest.TestCase):
    def test_a_named_subject_routes_as_new(self):
        for q in ('now run a profile on Severance', 'and what about Severance', 'what about Netflix',
                  'what about the last of us', 'What about The Last of Us on Netflix', 'but what about Severance on Apple TV+',
                  'can you give me the demo breakdown of people who watched Severance on Apple TV+',
                  'Tony directed by Matt Johnson about a young Anthony Bourdain.',
                  'so how do Hazbin Hotel viewers compare'):
            self.assertIsNone(F.resolve(q, thread(), today=TODAY), q)

    def test_a_name_the_reader_found_in_these_words_routes_as_new(self):
        r = {'subjects': [{'as_written': 'the last of us', 'kind': 'title'}]}
        self.assertIsNone(F.resolve('and on netflix the last of us', thread(), today=TODAY, reading=r))

    def test_the_other_lanes_keep_their_turns(self):
        # approvals, orders, questions about the answer and plain questions are not this module's
        for q in ('yes', 'approve', 'run it', 'ok queue it', 'thanks', 'how do you count a view', 'what is the sample size',
                  'is that US only', 'so what does that mean'):
            self.assertIsNone(F.resolve(q, thread(), today=TODAY), q)


class NothingToPointAt(unittest.TestCase):
    def test_her_sentence_alone_asks_which_title(self):
        fu = F.resolve(HERS, [], today=TODAY)
        self.assertEqual(fu['kind'], 'needs_title')
        got, early = F.at_interpret(HERS, [], {}, None)
        self.assertEqual(early['error'], F.WHICH_TITLE)
        self.assertTrue(early['guidance'])
        for bad in (MINTED, MINTED.lower(), REFUSAL, 'Just Launched'):
            self.assertNotIn(bad.lower(), early['error'].lower())

    def test_an_open_page_answers_for_itself(self):
        body = {'page_context': {'primary': {'name': 'Hazbin Hotel Season 1', 's3_key': 'Hazbin.csv'}}}
        self.assertEqual(F.at_interpret(HERS, [], body, None), (None, None))

    def test_a_bare_change_with_nothing_before_it_is_left_alone(self):
        for q in ('just the last 90 days', 'and what about on Netflix', 'for 2025'):
            self.assertIsNone(F.resolve(q, [], today=TODAY), q)

    def test_a_thread_of_small_talk_holds_no_subject(self):
        h = [{'role': 'user', 'text': 'hello'}, {'role': 'agent', 'text': 'Hi. What would you like to look at?'}]
        self.assertEqual(F.resolve(HERS, h, today=TODAY)['kind'], 'needs_title')


class ASentenceIsNeverAName(unittest.TestCase):
    SENTENCES = (MINTED, 'I Just Want Recent Data', 'Same Thing But For 2025', 'Just Launched SVOD',
                 'The Show Just Premiered On Netflix', 'So I Just Want Recent Data After It Launched')
    NAMES = ('Tony (2026)', 'Tony Viewers on Amazon Prime Video', 'Everybody Loves Raymond', 'And Just Like That',
             "But I'm a Cheerleader", 'The Last of Us', 'How I Met Your Mother', 'This Is Us', 'It Ends With Us',
             'So I Married an Axe Murderer', 'The Show Must Go On', 'The One That Got Away', 'That 70s Show',
             'Just the Two of Us', 'Since You Been Gone', 'What About Bob', 'Euphoria US', 'Now You See Me')

    def test_sentences(self):
        for c in self.SENTENCES:
            self.assertTrue(F.reads_as_sentence(c, HERS), c)

    def test_names_stay_names(self):
        for c in self.NAMES:
            self.assertFalse(F.reads_as_sentence(c, c), c)
            self.assertFalse(F.reads_as_sentence(c, f'run a profile on {c}'), c)
            self.assertIsNone(F.guard_subject(c, f'run a profile on {c}', thread()), c)

    def test_the_name_check_carries_on_with_the_threads_subject(self):
        fu = F.resolve(HERS, thread(), today=TODAY)
        self.assertEqual(F.guard_subject(MINTED, HERS, thread(), fu), {'adopt': 'Tony'})
        self.assertEqual(F.guard_subject(MINTED, HERS, thread(), None), {'adopt': 'Tony'})     # the thread alone is enough
        draft = {'subject': MINTED, 'name': MINTED, 'decision': 'new_build', 'resolved_title': MINTED,
                 'ask_identity': True, 'identity_data': {'title': MINTED}}
        self.assertIsNone(F.settle_subject(draft, HERS, thread(), fu))
        self.assertEqual((draft['subject'], draft['name']), ('Tony', 'Tony'))
        for k in ('resolved_title', 'ask_identity', 'identity_data'):
            self.assertNotIn(k, draft)                                # nothing drafted about the sentence survives

    def test_a_piece_of_a_follow_up_is_not_a_name_either(self):
        q = 'and what about on Netflix'
        fu = F.resolve(q, thread(), today=TODAY)
        self.assertEqual(F.guard_subject('What About On Netflix', q, thread(), fu), {'adopt': 'Tony'})

    def test_with_no_thread_it_asks_and_never_echoes(self):
        ask = F.guard_subject(MINTED, HERS, [], F.resolve(HERS, [], today=TODAY))
        self.assertEqual(ask, {'ask': F.WHICH_TITLE})
        draft = {'subject': MINTED, 'decision': 'new_build'}
        payload = F.settle_subject(draft, HERS, [], None)
        self.assertEqual(payload['error'], F.WHICH_TITLE)
        self.assertNotIn('Launched', payload['error'])

    def test_a_real_name_is_looked_up_as_before(self):
        fu = F.resolve(HERS, thread(), today=TODAY)
        draft = {'subject': 'Tony (2026)', 'decision': 'new_build'}
        self.assertIsNone(F.settle_subject(draft, HERS, thread(), fu))
        self.assertEqual(draft['subject'], 'Tony (2026)')


class TheRefusalNeverSaysASentenceBack(unittest.TestCase):
    def test_her_sentence(self):
        un = MQ.unresolved(MINTED, None, HERS)
        self.assertNotIn(MINTED, un['copy'])
        self.assertNotIn('Launched', un['copy'])
        self.assertEqual(un['chips'], [])

    def test_no_sentence_of_any_length(self):
        asks = {'I Just Want Recent Data': 'i just want recent data', 'Same Thing But For 2025': 'same thing but for 2025',
                'Just The Last 90 Days': 'just the last 90 days', 'The Show Just Premiered On Netflix': 'the show just premiered on netflix',
                MINTED: HERS, 'So I Just Want Recent Data After It Launched': HERS,
                'Can You Give Me The Demo Breakdown Of The Audience Of That Show Please And Thanks': 'x'}
        for cand, ask in asks.items():
            un = MQ.unresolved(cand, None, ask)
            self.assertNotIn(cand, un['copy'], cand)
            self.assertNotIn(REFUSAL + ', so I have not set up a build for it', un['copy'], cand)

    def test_the_formatted_refusal_only_ever_holds_a_name(self):
        # the property: whenever the copy says "<X> as a brand, person, or title", X does not read as a sentence
        for cand in ASentenceIsNeverAName.SENTENCES + ASentenceIsNeverAName.NAMES + ('Zzyzx Road Show', 'Qwertyflix'):
            copy = MQ.unresolved(cand, None, HERS if cand in ASentenceIsNeverAName.SENTENCES else f'run a profile on {cand}')['copy']
            m = re.search(r"I could not find (.+?) as a brand, person, or title", copy)
            if m:
                self.assertFalse(F.reads_as_sentence(m.group(1), HERS), m.group(1))

    def test_a_genuine_unknown_name_is_still_refused_by_name(self):
        un = MQ.unresolved('Qwertyflix', None, 'run a profile on Qwertyflix')
        self.assertTrue(un['copy'].startswith('I could not find Qwertyflix as a brand, person, or title'))
        un = MQ.unresolved('Nikee', 'Nike', 'run a profile on Nikee')
        self.assertIn('Did you mean Nike?', un['copy'])
        self.assertEqual(un['chips'], ['Build a profile for Nike'])
        # the 2026-11-17 rule stands: a suggestion that is the name typed is taken
        self.assertEqual(MQ.unresolved('Euphoria US', 'Euphoria', 'Run a profile for Euphoria'), {'adopt': 'Euphoria'})


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


def _app_source():
    return _read('app.py')


def _identity_backstop(verify=None):
    """app._resolve_subject_identity from app.py's own source, with the
    host helpers it calls bound to plain stand-ins. The research call
    comes back empty: the title did not verify, which is the path her
    turn took."""
    src = _app_source()
    a = src.index('_IDENTITY_MEDIUM_WORDS = {')
    b = src.index('_INTERNAL_FIELD_NAMES_IN_PROSE = (', a)
    c = src.index('def _resolve_subject_identity(')
    d = src.index('\n# ----', c)

    def _norm(s):
        return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9\s]+', ' ', str(s or '').lower())).strip()

    ns = {'re': re, '_normalize_for_match': _norm, '_collapse_for_match': lambda s: _norm(s).replace(' ', ''),
          '_normalize_identity_versions': lambda v: [], '_set_resolved_identity_line': lambda d_: None,
          '_draft_is_ip_content': lambda d_: True, '_guard_short_name_identity': lambda *a_, **k_: None,
          '_verify_content_identity': verify or (lambda draft, prompt, sig: None),
          '_scrub_identity_prose': lambda *a_, **k_: None, '_scrub_identity_dashes': lambda s: str(s or ''),
          '_apply_identity_version': lambda *a_, **k_: None}
    exec(src[a:b], ns)
    exec(src[c:d], ns)
    return ns['_resolve_subject_identity']


class TheIdentityBackstop(unittest.TestCase):
    def draft(self, subject='Tony (2026)'):
        return {'subject': subject, 'name': subject, 'decision': 'new_build', 'medium': 'movie', 'platform': 'Prime Video',
                'identity_confident': False, 'brand_category': 'MOVIE'}

    def test_her_turn_keeps_the_title_she_named_earlier(self):
        seen = {}

        def verify(draft, prompt, sig):
            seen.update(prompt=prompt, sig=dict(sig))
            return None

        resolve = _identity_backstop(verify)
        fu = F.resolve(HERS, thread(), today=TODAY)
        d = resolve(self.draft(), HERS, allow_ask=True, **F.identity_kwargs(fu))
        self.assertEqual(d['subject'], 'Tony (2026)')
        self.assertNotIn('Launched', json.dumps(d))
        if seen:      # when the title is checked at all, the checker reads her whole ask and no minted title
            self.assertIn('Anthony Bourdain', seen['prompt'])
            self.assertIsNone(seen['sig'].get('title_guess'))
            self.assertEqual(seen['sig'].get('medium'), 'movie')        # "film", from the turn that named it

    def test_the_turn_alone_is_never_taken_for_a_title(self):
        # the call as it was before 2026-11-18 (no thread words): the sentence is still not a title
        d = _identity_backstop()(self.draft(), HERS, allow_ask=True)
        self.assertEqual(d['subject'], 'Tony (2026)')
        self.assertNotIn('Launched', json.dumps(d))

    def test_a_lookalike_is_still_put_back_to_the_words_typed(self):
        d = _identity_backstop()(self.draft('Fast & Furious'), 'run one on furious show on hulu', allow_ask=True)
        self.assertEqual(d['subject'], 'Furious')
        self.assertTrue(d.get('ask_identity'))

    def test_a_lookalike_on_a_follow_up_is_asked_about_never_minted(self):
        h = thread('run one on furious show on hulu', 'New Furious profile, ready to run.')
        q = 'but the show just launched on hulu so i just want recent data after it launched'
        fu = F.resolve(q, h, today=TODAY)
        self.assertEqual(fu['kind'], 'refinement')
        d = _identity_backstop()(self.draft('Fast & Furious'), q, allow_ask=True, **F.identity_kwargs(fu))
        self.assertTrue(d.get('ask_identity'))                       # "fast" is in nobody's words: the reader confirms
        self.assertNotIn('Just Launched', json.dumps(d))
        self.assertFalse(F.reads_as_sentence(d['subject'], q))

    def test_the_call_is_unchanged_for_any_other_turn(self):
        self.assertEqual(F.identity_kwargs(None), {})
        self.assertEqual(F.identity_kwargs(F.resolve('now run a profile on Severance', thread(), today=TODAY)), {})


class TheBinderDoesNotMintOneEither(unittest.TestCase):
    def test_a_predicate_after_the_kind_is_not_a_name(self):
        for q in (HERS, 'the show has just premiered on netflix', 'the film came out last week',
                  'the title launched on Prime Video', 'the series only recently debuted'):
            self.assertEqual(NS.type_led_subject(q), ('', ''), q)
        b = SR.bind(HERS, None, '')
        self.assertFalse(b and b.source == 'words' and 'launched' in str(b.display).lower())

    def test_type_led_names_are_still_names(self):
        for q, want in (('where people watched the film mutiny', 'Mutiny'), ('that show gilmore girls', 'Gilmore Girls'),
                        ('the show just shoot me', 'Just Shoot Me'), ('the movie hit man', 'Hit Man'),
                        ('the book the kiss quotient', 'The Kiss Quotient')):
            self.assertEqual(NS.type_led_subject(q)[0], want, q)


class TheCopyThatGoesToTheTeam(unittest.TestCase):
    def test_a_sentence_is_not_shown_as_the_subject(self):
        import prometheus_watch_notify as W
        self.assertEqual(W._subject_line(MINTED, HERS), '')
        self.assertEqual(W._subject_line('Tony Viewers on Amazon Prime Video', HERS), 'Tony Viewers on Amazon Prime Video')
        self.assertEqual(W._subject_line('Hazbin Hotel Season 1 - Amazon Prime New Sign-Ups Viewers', 'are these people new sign ups'),
                         'Hazbin Hotel Season 1 - Amazon Prime New Sign-Ups Viewers')
        self.assertEqual(W._subject_line('', HERS), '')

    def test_the_threads_subject_stands_in_when_the_thread_is_known(self):
        self.assertEqual(F.subject_line(MINTED, HERS, thread()), 'Tony')

    def test_the_build_flow_records_the_threads_subject(self):
        hints = []
        fu, early = F.at_interpret(HERS, thread(), {}, lambda **kw: hints.append(kw))
        self.assertIsNone(early)
        self.assertIn({'subject': 'Tony'}, hints)                     # what the ask log and the copy carry

    def test_both_send_paths_use_it(self):
        src = _read('prometheus_watch_notify.py')
        self.assertEqual(src.count('subj_line = _subject_line('), 2)
        self.assertNotIn("subj_line = str(subject or '').strip()", src)


class AnAgeIsNotASpanOfYears(unittest.TestCase):
    """Her first turn: "a 19-year-old Anthony Bourdain" drew a six year
    package on Amazon Prime Video. The detector, from the module's own
    source (the module itself needs the app host)."""

    @classmethod
    def setUpClass(cls):
        src = _read('prometheus', 'legacy', 'rankers.py')
        i = src.index('_PM_MY_RANGE_RE = re.compile(')
        j = src.index('def _pm_year_files_for_subject(', i)
        ns = {'re': re, 'datetime': dt.datetime}
        exec(src[i:j], ns)
        cls.detect = staticmethod(ns['_pm_detect_multi_year_ask'])

    def test_her_first_turn_names_no_span(self):
        self.assertIsNone(self.detect(FIRST))
        for t in ('a 19 year old', '10-year-olds', '5 year olds', 'kids who are 4-5 years old', '4 or 5 year old kids',
                  'what do 6-year-olds watch on Netflix'):
            self.assertIsNone(self.detect(t), t)

    def test_a_span_is_still_a_span(self):
        for t, n in (('a 4-5 year analysis of Will & Grace', 5), ('the past 3 years of Will & Grace', 3),
                     ('a 5-year trend for Will & Grace', 5), ('a 3 year read on Will & Grace', 3),
                     ('a 4 to 5 year look at Will & Grace', 5)):
            years = self.detect(t)
            self.assertTrue(years and len(years) == n, (t, years))


class Wiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = _read('prometheus', 'legacy', 'chat.py')
        cls.app = _app_source()
        cls.mod = _read('prometheus', 'followup_refine.py')

    def test_the_build_intake_reads_the_turn_against_its_thread(self):
        core = self.chat[self.chat.index('def _pm_interpret_core('):]
        core = core[:core.index('\ndef ', 10)]
        at = core.index('_fur.at_interpret(text, history, body, _pm_ask_hint)')
        self.assertLess(at, core.index('_H._run_nflx_claude_agent('))                      # before any draft
        self.assertIn('**_fur.identity_kwargs(_fu)', core)                                  # the identity backstop
        self.assertIn('history=history, followup=_fu, preverify=_preverify)', core)         # the name check
        self.assertIn('_fur.settle_window_on_host(spec_draft, _fu, _H._apply_event_window_to_draft(', core)
        self.assertLess(core.index('_fur.settle_window_on_host('), core.index('_H._bind_explicit_windows('))

    def test_the_name_check_settles_the_subject_before_it_looks_anything_up(self):
        fn = self.chat[self.chat.index('def _pm_chat_subject_verify('):]
        fn = fn[:fn.index('\ndef ', 10)]
        self.assertIn('history=None, followup=None', fn[:200])
        self.assertLess(fn.index('.settle_subject(spec_draft, text, history, followup, _pm_ask_hint)'),
                        fn.index('_pm_verify_cached(subj, candidates)'))
        self.assertLess(fn.index('.settle_subject('), fn.index('_mq.unresolved(subj, suggest, text)'))

    def test_the_identity_backstop_takes_the_thread_words(self):
        fn = self.app[self.app.index('def _resolve_subject_identity('):]
        fn = fn[:fn.index('\n# ----')]
        self.assertIn("thread_words=''", fn[:120])
        self.assertIn('_identity_tokens_missing_from_prompt(subject_entity, _own_words)', fn)
        self.assertIn('_verify_content_identity(draft, _own_words, sig)', fn)
        self.assertIn("sig['title_guess'] = None", fn)

    def test_the_thread_decides_never_a_flag_on_the_request(self):
        self.assertNotIn("'_pm_", self.mod)
        self.assertNotIn('body.get(\'followup', self.chat)
        self.assertNotIn("body.get('_pm_followup", self.chat)
        self.assertNotIn('request.', self.mod)

    def test_the_line_ratchet_holds(self):
        self.assertLess(len(self.chat.splitlines()), 19900)

    def test_no_dashes_in_the_copy(self):
        for copy in (F.WHICH_TITLE, F.which_title_copy('it'), F.launch_question('Tony', 'Amazon Prime Video')):
            self.assertNotIn('\u2014', copy)
            self.assertNotIn('\u2013', copy)
            for word in ('synth', 'model', 'estimat', 'pipeline', 'hostmap', 'content map', 'confidence'):
                self.assertNotIn(word, copy.lower())


_CHILD = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from prometheus import replay
replay.install_isolation()
h = replay.harness()
h.host.set('_neutralize_data_delimiters', lambda s, *a, **k: str(s or ''))

def _bind(draft, text, chat_history=None, decision=None, question=None):
    from prometheus import inline_dates as I
    f = I.parse_inline_window(str(text or ''))
    if not f:
        return False
    draft['date_range'] = {'start': f['start'], 'end': f['end']}
    draft['date_range_explicit'] = True
    draft['date_window_label'] = I.window_label(f['start'], f['end'])
    return True
h.host.set('_bind_inline_date_window', _bind)
out = []
for it in json.load(sys.stdin):
    replay._restore_baseline(h)
    replay._STATE['question'] = it['question']
    replay._canned_dual(h, it.get('draft_subject') or '', None)
    door = replay._STATE.get('door') or '/api/brief-chat/interpret'
    body = {'text': it['question'], 'history': it.get('history') or [], 'page_context': {},
            'client': 'dashboard', 'persist': False, 'surface': 'interpret'}
    n0 = len(replay._STATE['captured'])
    status, payload = h.post(door, body, session={'username': replay.REPLAY_USER['username']})
    rec = (replay._STATE['captured'][n0:] or [{}])[-1]
    raw = payload.get('raw') if isinstance(payload, dict) and isinstance(payload.get('raw'), dict) else payload
    raw = raw if isinstance(raw, dict) else {}
    draft = raw.get('spec_draft') if isinstance(raw.get('spec_draft'), dict) else {}
    out.append({'route': rec.get('route'), 'outcome': rec.get('outcome'), 'subject': rec.get('subject'),
                'reply': replay._reply_text(raw) or str((payload or {}).get('text') or ''),
                'draft_subject': draft.get('subject'), 'date_range': draft.get('date_range')})
print('@@@' + json.dumps(out))
'''


def _door(items):
    """Replay asks through the build flow's door in a child process:
    the recording host, a fake store, the model canned to draft the
    subject named. None when the recording host is not on the path."""
    env = dict(os.environ, REGRESSION_TEST_MODE='1')
    try:
        p = subprocess.run([sys.executable, '-c', _CHILD, ROOT], input=json.dumps(items), capture_output=True,
                           text=True, env=env, timeout=600)
    except Exception:
        return None
    i = p.stdout.rfind('@@@')
    if i < 0:
        return None
    try:
        return json.loads(p.stdout[i + 3:])
    except Exception:
        return None


class ThroughTheDoor(unittest.TestCase):
    """Her thread, her words, through the build flow as the dashboard
    sends them. The model is canned to draft exactly what went out on
    the day (the sentence), so the checks hold whatever a model does."""

    @classmethod
    def setUpClass(cls):
        cls.items = [
            {'question': HERS, 'draft_subject': MINTED, 'history': thread()},
            {'question': HERS, 'draft_subject': 'Tony (2026)', 'history': thread()},
            {'question': HERS, 'draft_subject': MINTED, 'history': []},
            {'question': 'now run a profile on Severance', 'draft_subject': 'Severance', 'history': thread()},
            {'question': 'and what about on Netflix', 'draft_subject': 'What About On Netflix', 'history': thread()},
            {'question': HERS, 'draft_subject': 'Tony', 'history': thread(FIRST.replace(' (released on October 26, 2026)', ''))},
        ]
        cls.res = _door(cls.items)

    def setUp(self):
        if not self.res:
            self.skipTest('the recording host (scripts/_pm_behavior.py in the parent repo) is not on the path here')

    def test_her_turn_is_never_refused_with_a_sentence(self):
        for r in self.res:
            self.assertNotIn(REFUSAL, r['reply'])
            self.assertNotIn('Launched Svod', r['reply'])
            self.assertNotIn(MINTED, json.dumps(r))

    def test_the_sentence_the_draft_step_minted_becomes_her_title(self):
        r = self.res[0]
        self.assertEqual((r['route'], r['outcome']), ('profile_build', 'new_build'))
        self.assertEqual(r['draft_subject'], 'Tony')
        self.assertEqual(r['subject'], 'Tony')                        # the ask log, and the copy to the team
        self.assertEqual((r['date_range'] or {}).get('start'), '2026-10-26')

    def test_a_good_draft_goes_through_on_her_launch_window(self):
        r = self.res[1]
        self.assertEqual((r['route'], r['outcome'], r['draft_subject']), ('profile_build', 'new_build', 'Tony (2026)'))
        self.assertEqual((r['date_range'] or {}).get('start'), '2026-10-26')
        self.assertLess((r['date_range'] or {}).get('end'), dt.datetime.now(dt.timezone.utc).date().isoformat())

    def test_with_no_thread_it_asks_which_title(self):
        r = self.res[2]
        self.assertEqual((r['route'], r['outcome']), ('followup', 'asked_which_title'))
        self.assertEqual(r['reply'], F.WHICH_TITLE)
        self.assertIsNone(r['subject'])

    def test_a_new_subject_in_her_thread_is_a_new_build(self):
        r = self.res[3]
        self.assertEqual((r['route'], r['outcome'], r['draft_subject']), ('profile_build', 'new_build', 'Severance'))
        self.assertIsNone(r['date_range'])                            # nothing of Tony's rides along

    def test_a_platform_change_keeps_her_title(self):
        r = self.res[4]
        self.assertEqual((r['route'], r['draft_subject']), ('profile_build', 'Tony'))

    def test_no_launch_day_anywhere_is_one_short_question(self):
        r = self.res[5]
        self.assertEqual((r['route'], r['outcome']), ('followup', 'asked_launch_date'))
        self.assertEqual(r['reply'], F.launch_question('Tony', 'Amazon Prime Video'))


if __name__ == '__main__':
    unittest.main(verbosity=1)
