#!/usr/bin/env python3
"""The title the words name is the title the answer is about (Jenna
2026-11-19).

A Dick Wolf Productions seat asked for Chicago P.D. on 2026-11-18 and
was answered about Law & Order, twice:

    "can you do the same for chicago pd i only need for the year 2020
     viewership minutes on amazon prime"
        -> "I can run the Law & Order read again on January 1 to
            December 31, 2020. Pulling this data will cost $100."
    "no i need chicago pd"
        -> "On it: reading Law & Order: Special Victims Unit now."
    "no i need CHICAGO PD show minutes viewed in 2020 on amazon prime"
        -> "On it: reading Law & Order: Special Victims Unit now."

What this suite holds:

  1. The names: one title has one key however it is typed; two titles
     of one franchise are two titles (the Dick Wolf shows, NCIS, Power,
     Outlander, the Yellowstone family, a film and its series).
  2. The words: what an ask names ("the same for X", "what about X",
     "no i need X"), what it pushes away, what a list is, what points
     back at the subject in hand.
  3. The binder: words beat thread beat page, for a title typed in lower
     case that is not on the shelf too.
  4. The continuation: a re-run, another cut and a correction stand down
     for a title the words name; a landed read's header is read whole.
  5. The base of a read: a file about another title is never it, and a
     "<Title> on <Service>" file is never taken on the service's words.
  6. Stored figures: a record filed under another title is never used.
  7. The wiring: the splices in the chat module, the gate, the exit.
  8. Through the door, model canned: her three questions as she typed
     them and the same on a window the data covers; the families on a
     made-up shelf where the franchise mate is the one on the shelf.

Hermetic: no model, no live store, no mail, no app boot. The door
checks run in a child process on the recording host from the parent
repo and are skipped with a note when it is not on the path
(PM_DOOR_REQUIRED=1 makes that a failure; the parent repo's wrapper
sets it).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest

from _pm_legacy_view import legacy_view as _legacy_view, chat_py_lines as _chat_py_lines  # noqa: E402  (stage 5: chat.py plus its extracted cores)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import named_title as NT  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402
from prometheus import continuation as CONT  # noqa: E402

# her three questions, as she typed them
HERS_1 = "can you do the same for chicago pd i only need for the year 2020 viewership minutes on amazon prime"
HERS_2 = "no i need chicago pd"
HERS_3 = "no i need CHICAGO PD show minutes viewed in 2020 on amazon prime"
# what she was shown (the words only; the figures are not kept here)
SENT_1 = "I can run the Law & Order read again on January 1 to December 31, 2020. Pulling this data will cost $100."
SENT_2 = ("On it: reading Law & Order: Special Victims Unit now. This one takes a real look at the data, so give me a moment - "
          "the read will land right here when it is ready.")
SVU = 'Law & Order: Special Victims Unit'
SVU_READ_SUBJECT = 'Law & Order: Special Victims Unit on Amazon Prime Video'
PD = 'Chicago P.D.'
INTERNAL = ('synth', 'modeled', 'modelled', 'estimat', 'pipeline', 'hostmap', 'content map', 'confidence', '\u2014', '\u2013')


def _user(t):
    return {'role': 'user', 'text': t}


def _read(subject, year='2020', chips=()):
    """A landed read the way the thread stores one: the header on its own
    line, the subject on the turn's meta."""
    return {'role': 'agent',
            'text': (f"On {subject}:\n\nThe title reads as a deep catalog watch on the service across the year.\n\n"
                     f"Window: Jan 1 {year} to Dec 31 {year}."),
            'meta': {'kind': 'read', 'read_job_id': 'job0000svu', 'subject': subject,
                     'options': [{'label': c, 'send': c} for c in chips]}}


def her_thread(year='2020'):
    """The shape of her thread when she asked for Chicago P.D.: a read on
    Special Victims Unit on Amazon Prime had just landed."""
    return [_user(f"special victims unit on amazon prime platform for the year {year}.  minutes viewed"),
            {'role': 'agent', 'text': 'On it: reading Special Victims Unit now. This one takes a real look at the data, so give me a '
                                      'moment - the read will land right here when it is ready.'},
            _read(SVU_READ_SUBJECT, year, chips=(f'Month by month minutes for Special Victims Unit on Prime Video across {year}',
                                                 f'Same {year} read for Chicago P.D. on Prime Video', 'Email me this file'))]


def _read_src(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


# ============================================================================ 1. the names
class TheNames(unittest.TestCase):
    def test_one_title_one_key_however_it_is_typed(self):
        for group in (('Chicago PD', 'Chicago P.D.', 'chicago pd', 'CHICAGO PD', 'Chicago P. D.', 'Chicago P.D', 'chicago p.d.',
                       'Chicago P.D. on Amazon Prime Video', 'Chicago P.D. Viewers on Peacock - Avid Fan'),
                      ('SVU', 'svu', 'Special Victims Unit', 'special victims unit', 'Law & Order: SVU', 'law and order svu',
                       'Law & Order: Special Victims Unit', SVU_READ_SUBJECT, 'Law & Order Special Victims Unit Viewers on Peacock'),
                      ("NCIS: Hawai'i", 'ncis hawaii', 'NCIS Hawaii viewers', "NCIS: Hawai\u2019i on Paramount+"),
                      ('Power Book II: Ghost', 'power book ii', 'Power Book 2', 'Power Ghost viewers on Starz'),
                      ('Power Book III: Raising Kanan', 'raising kanan', 'Power Raising Kanan Season 5 viewers'),
                      ('Outlander: Blood of My Blood', 'blood of my blood', 'Outlander Blood of My Blood Viewers on Starz'),
                      ('FBI: Most Wanted', 'FBI Most Wanted', 'fbi most wanted viewers'),
                      ('Power', 'Power On Amazon Prime', 'Power Starz', 'Power ON Amazon Prime - Avid Fan'),
                      ('Landman', 'Landman Viewers on Paramount+', 'landman', 'Landman Season 2 viewers')):
            keys = {NT.title_key(x) for x in group}
            self.assertEqual(len(keys), 1, (group, keys))
            for a in group:
                self.assertTrue(NT.same_title(a, group[0]), (a, group[0]))

    def test_two_titles_of_one_franchise_are_two_titles(self):
        mates = [('Law & Order', SVU), ('Law & Order', 'Law & Order: Organized Crime'), (SVU, 'Law & Order: Organized Crime'),
                 ('Chicago P.D.', 'Chicago Fire'), ('Chicago Fire', 'Chicago Med'), ('FBI', 'FBI: Most Wanted'),
                 ('FBI: Most Wanted', 'FBI: International'), ('NCIS', 'NCIS: Sydney'), ('NCIS', "NCIS: Hawai'i"),
                 ('NCIS: Los Angeles', 'NCIS: New Orleans'), ('NCIS', 'NCIS: Origins'), ('Power', 'Power Book II: Ghost'),
                 ('Power Book II: Ghost', 'Power Book III: Raising Kanan'), ('Power On Amazon Prime', 'Power Book IV: Force'),
                 ('Outlander', 'Outlander: Blood of My Blood'), ('Yellowstone', '1883'), ('Yellowstone', '1923'), ('1883', '1923'),
                 ('Dune', 'Dune: Prophecy'), ('Top Gun', 'Top Gun: Maverick'), ('The Walking Dead', 'The Walking Dead: Dead City')]
        for a, b in mates:
            self.assertFalse(NT.same_title(a, b), (a, b))
            self.assertTrue(NT.siblings(a, b), (a, b))
            self.assertTrue(NT.siblings(b, a), (b, a))
        # two titles that share nothing are neither
        for a, b in ((SVU, PD), ('Landman', 'Tulsa King'), ('Psych', 'Power')):
            self.assertFalse(NT.same_title(a, b))
            self.assertFalse(NT.siblings(a, b))
        # a film and a series of one name, when both labels say which
        self.assertFalse(NT.same_title('Fallout (2026 Film)', 'Fallout series'))
        self.assertTrue(NT.same_title('Fallout Season 2 Viewers On Prime Video', 'Fallout'))

    def test_the_longest_name_wins_and_ordinary_words_are_not_titles(self):
        got = lambda s: [h.display for h in NT.known(s)]                      # noqa: E731
        self.assertEqual(got('Law & Order: Special Victims Unit on Amazon Prime Video'), [SVU])
        self.assertEqual(got('ncis sydney vs ncis'), ['NCIS: Sydney', 'NCIS'])
        self.assertEqual(got('FBI: Most Wanted and FBI International'), ['FBI: Most Wanted', 'FBI: International'])
        self.assertEqual(got("I need svu and chicago pd"), [SVU, PD])
        self.assertEqual(got('give me for Special Victims uni the Total'), [SVU])           # as she typed it
        for plain in ('viewers of organized crime documentaries', 'power users on amazon', 'the fbi director said',
                      'mitsubishi outlander buyers', 'Yellowstone national park visitors', 'Chicago Fire FC fans', 'in 1923',
                      'from 1/1883', 'the most wanted list'):
            self.assertEqual(got(plain), [], plain)

    def test_a_label_down_to_its_title(self):
        for label, want in ((SVU_READ_SUBJECT, SVU), ('Chicago P.D. on Amazon Prime Video', 'Chicago P.D.'),
                            ('Power On Amazon Prime', 'Power'), ('Fallout Season 2 Viewers On Prime Video', 'Fallout'),
                            ('Tony (2026 Film) Viewers on Amazon Prime Video', 'Tony'), ('Amazon Prime Video', 'Amazon Prime Video'),
                            ('Amazon Prime Video - TVOD Renters', 'Amazon Prime Video'), ('The Morning Show', 'The Morning Show'),
                            ('Jet Lag: The Game', 'Jet Lag: The Game'), ('Psych on Amazon Prime Video', 'Psych')):
            self.assertEqual(NT.core(label), want, label)

    def test_a_title_keeps_its_dots_and_the_name_it_was_typed_under(self):
        for typed in ('chicago pd', 'Chicago P.D', 'CHICAGO PD', 'Chicago PD'):
            self.assertEqual(SR.clean_display(typed), 'Chicago P.D.', typed)
        self.assertEqual(SR.clean_display('Chicago P.D.'), 'Chicago P.D.')
        # typed another way, a title is left as it was typed (Liz 2026-11-17: a title keeps its name)
        for typed in ('FBI Most Wanted', 'SVU', 'Special Victims Unit', 'Landman'):
            self.assertEqual(SR.clean_display(typed), typed)
        self.assertEqual(NT.tidy('I can pull that read on Chicago P.D.. Pulling this data will cost $100.'),
                         'I can pull that read on Chicago P.D. Pulling this data will cost $100.')
        self.assertEqual(NT.tidy('Wait... what'), 'Wait... what')


# ============================================================================ 2. the words
class TheWords(unittest.TestCase):
    def test_the_name_in_her_three_questions(self):
        for q in (HERS_1, HERS_2, HERS_3, HERS_1.replace('2020', '2023'), HERS_3.replace('2020', '2023')):
            self.assertEqual(NT.slot_title(q), PD, q)
            self.assertEqual([h.display for h in NT.asked(q)], [PD], q)
            for carried in (SVU_READ_SUBJECT, SVU, 'Law & Order', 'Psych on Amazon Prime Video', 'Amazon Prime Video', 'Power On Amazon Prime'):
                self.assertEqual(NT.other_than(q, carried), PD, (q, carried))

    def test_the_slot_takes_a_name_and_nothing_else(self):
        yes = {"same for Chicago Fire": 'Chicago Fire', "what about Chicago Med?": 'Chicago Med', "now do NCIS: Sydney": 'NCIS: Sydney',
               "no i meant Tulsa King": 'Tulsa King', "same for The Bear": 'The Bear', "do that for Only Murders in the Building":
               'Only Murders in the Building', "no i asked for Landman": 'Landman', "what about power book ii": 'Power Book II: Ghost',
               "same for the walking dead dead city": 'The Walking Dead: Dead City', "no I need the show Bosch: Legacy": 'Bosch: Legacy',
               "what about Slow Horses on Apple TV+": 'Slow Horses', "now do Reacher on Prime Video for 2024": 'Reacher',
               "no do this analysis for unOrdinary the WEBTOON": 'unOrdinary', "do this for unOrdinary": 'unOrdinary',
               "what about Squid Game": 'Squid Game', "same for The Morning Show": 'The Morning Show'}
        for q, want in yes.items():
            self.assertEqual(NT.slot_title(q), want, q)
        no = ["what about the men?", "how about women 25-54", "same for 2023", "do the same for Hulu", "the same for California",
              "no i need the monthly numbers", "no i need it by month", "no i need help", "no, minutes not viewers",
              "same thing for the avid fans", "what about last year", "can you do the same for the year 2022", "no that's wrong",
              "No.", "no", "no thanks", "no i need one year - 2023", "what about Peacock", "how about q4", "what about by platform",
              "now do the top 10", "no not that one", "what about gen z", "same for texas", "what about their income",
              "no i want the csv", "what about it", "now do the same for seasons 1 through 8", "what about Chicago",
              "what about New York viewers", "no i need more detail", "what about the cost", "what about a deck", "no the other one",
              "same for everyone", "no i need a breakdown by age", "what about mobile", "now do it for peacock", "no I need minutes",
              "no the whole year", "no, all of 2023", "no i need to know how many", "no I don't want that", "what about streaming",
              "what about competitors", "what about social media", "no i need the same thing", "what about spanish language viewers",
              "No, run what I asked", "no, build fresh", "no a pdf was created. where is it", "do the same for retail brands for 2023"]
        for q in no:
            self.assertEqual(NT.slot_title(q), '', q)
            self.assertEqual(NT.slot_title(q, strict=False), '', q)

    def test_a_name_typed_small_counts_where_the_shape_swaps_the_subject(self):
        # the binder asks for a capital, a kind word or the thread's own spelling; a re-run "for X" and a
        # correction "no i need X" take the name as typed
        for q, want in (("same for reacher", 'Reacher'), ("no i need reacher", 'Reacher'), ("no i need nike", 'Nike'),
                        ("same for the bear", 'The Bear')):
            self.assertEqual(NT.slot_title(q), '', q)
            self.assertEqual(NT.slot_title(q, strict=False), want, q)
        thread = [_read('Law & Order: SVU', chips=('Same 2023 read for Reacher on Prime Video',))]
        self.assertEqual(NT.slot_title("same for reacher", thread), 'Reacher')
        self.assertEqual(NT.slot_title("same for the show reacher"), 'Reacher')

    def test_a_title_pushed_away_is_not_asked_for(self):
        for q in ("I want to know about Colin and Samir, not NCIS.", "why is this coming back as NCIS?",
                  "why are you showing me NCIS data?", "stop bringing up Yellowstone", "this has nothing to do with Outlander"):
            self.assertEqual(NT.asked(q), [], q)
            self.assertEqual(NT.other_than(q, 'Colin And Samir'), '', q)
        self.assertEqual([h.display for h in NT.asked("not NCIS, I want NCIS: Sydney")], ['NCIS: Sydney'])
        self.assertEqual([h.display for h in NT.asked("why did SVU viewers drop in March")], [SVU])

    def test_a_list_or_a_long_brief_is_not_one_subject(self):
        nine = ("Nine Dick Wolf series on Peacock, window Nov 17 2025 to Nov 16 2026: Law & Order: Special Victims Unit; Law & Order; "
                "Law & Order: Organized Crime; Chicago Fire; Chicago Med; Chicago P.D.; FBI; FBI: Most Wanted; FBI: International.")
        self.assertTrue(NT.is_list(nine))
        for carried in ('Dick Wolf', 'Dick Wolf series on Peacock', 'Peacock'):
            self.assertEqual(NT.other_than(nine, carried), '')
        self.assertIsNone(NT.bound_words(nine, None, '', SR.Subject()))
        long_ask = ("Is the year over year streaming lift specific to Abbott Elementary or shared across ABC series? " * 4
                    + "Compare it with 9-1-1.")
        self.assertTrue(NT.is_list(long_ask))
        self.assertEqual(NT.other_than(long_ask, 'Abbott Elementary'), '')
        self.assertFalse(NT.is_list("I need svu and chicago pd"))

    def test_words_that_point_back_keep_the_subject_in_hand(self):
        for q in ("what share of them also watch Chicago P.D.?", "do these viewers watch NCIS: Sydney", "how many of this audience watch SVU",
                  "do they also watch Chicago Fire"):
            self.assertTrue(NT.points_back(q), q)
            self.assertEqual(NT.other_than(q, 'NCIS: Sydney on Paramount+'), '', q)
        self.assertFalse(NT.points_back(HERS_1))

    def test_what_is_and_is_not_another_title(self):
        other = [("how many watched NCIS: Sydney", 'NCIS', 'NCIS: Sydney'), ("how many watched ncis sydney", 'NCIS Viewers on Paramount+', 'NCIS: Sydney'),
                 ("Power Book II: Ghost viewers", 'Power On Amazon Prime', 'Power Book II: Ghost'), ("power book ii", 'Power', 'Power Book II: Ghost'),
                 ("Outlander: Blood of My Blood", 'Outlander', 'Outlander: Blood of My Blood'), ("blood of my blood minutes", 'Outlander on Starz', 'Outlander: Blood of My Blood'),
                 ("1883 viewers", 'Yellowstone', '1883'), ("yellowstone viewers", '1883', 'Yellowstone'), ("FBI: Most Wanted", 'FBI', 'FBI: Most Wanted'),
                 ("FBI viewers", 'FBI: Most Wanted Viewers on Paramount+', 'FBI'), ("Chicago Fire", 'Chicago P.D. on Amazon Prime Video', 'Chicago Fire'),
                 ("what about Law & Order", SVU_READ_SUBJECT, 'Law & Order'), ("what about law and order organized crime", SVU_READ_SUBJECT, 'Law & Order: Organized Crime'),
                 ("Top Gun: Maverick viewers", 'Top Gun', 'Top Gun: Maverick'), ("how many people watched Dune 2", 'Dune', 'Dune: Part Two'),
                 ("svu on amazon prime 2023 minutes", 'Amazon Prime Video', SVU), ("special victims unit on amazon prime platform", 'Power On Amazon Prime', SVU),
                 ("same for Tulsa King", 'Landman Viewers on Paramount+', 'Tulsa King'), ("how many people watched Psych on Hulu in 2024", 'Amazon Prime Video', 'Psych')]
        for q, held, want in other:
            self.assertEqual(NT.other_than(q, held), want, (q, held))
        same = [("break that out by month", SVU_READ_SUBJECT), ("month by month minutes for Special Victims Unit on Prime Video across 2023", SVU_READ_SUBJECT),
                ("compare svu and chicago pd", SVU_READ_SUBJECT), ("re-run for 2023", SVU_READ_SUBJECT), ("same for women", SVU_READ_SUBJECT),
                ("what about hulu", SVU_READ_SUBJECT), ("i ony need one year - 2023", SVU_READ_SUBJECT), ("ncis viewers by age", 'NCIS'),
                ("NCIS season 3 viewers", 'NCIS'), ("outlander season 7", 'Outlander'), ("Power viewers on Starz", 'Power On Starz'),
                ("what do these viewers buy", 'Yellowstone'), ("what share of Amazon Prime Video viewers watch SVU", 'Amazon Prime Video'),
                ("what about Landman season 2", 'Landman Viewers on Paramount+'), ("ncis sydney vs ncis", 'NCIS'), ("month by month psych minutes", 'Psych on Amazon Prime Video'),
                ("top brands here", 'Amazon Prime Video'), ("Build a new profile on JET LAG: THE GAME", 'Jet Lag: The Game'),
                ("Abbott Elementary: Season 6 (premiere week)", 'Abbott Elementary Season 6'), ("Claire's 6 stores", "Claire's"),
                ("Gemini - They are the best", 'Gemini')]
        for q, held in same:
            self.assertEqual(NT.other_than(q, held), '', (q, held))


# ============================================================================ 3. the binder
class TheBinder(unittest.TestCase):
    def test_words_beat_thread_for_a_title_that_is_not_on_the_shelf(self):
        for year in ('2020', '2023'):
            thread = her_thread(year)
            for q in (HERS_1.replace('2020', year), HERS_2, HERS_3.replace('2020', year)):
                s = SR.bind(q, thread, 'Amazon Prime Video')
                self.assertEqual((s.source, s.kind, s.name, s.display), ('words', 'title', PD, PD), q)
                self.assertTrue(s.named)
                self.assertEqual(SR.pinned_bind_for(q, thread, 'Amazon Prime Video'), '')
        # before: the words named nothing, so the thread answered
        self.assertEqual(SR._bind_any(HERS_2, her_thread(), 'Amazon Prime Video').source, 'thread')

    def test_the_thread_still_answers_when_no_title_is_named(self):
        thread = her_thread('2023')
        for q in ("break that out by month", "what about the men?", "same for 2022", "how about women 25-54", "what about competitors",
                  "no i need the monthly numbers"):
            s = SR.bind(q, thread, '')
            self.assertEqual(s.source, 'thread', q)
            self.assertTrue(NT.same_title(s.display, SVU), (q, s.display))     # and it is the title, not the franchise
        self.assertEqual(SR.thread_subjects(thread, '')[0], SVU_READ_SUBJECT)

    def test_the_page_still_answers_when_the_words_point_at_it(self):
        for q in ("what share of this profile is female", "what do the viewers on this page buy", "do the viewers in the open profile watch Chicago P.D."):
            s = SR.bind(q, None, 'NCIS')
            self.assertEqual((s.source, s.display), ('page', 'NCIS'), q)
        # "this audience" / "these viewers" with no thread is nobody in particular: never the page (Jenna 2026-11-20)
        for q in ("what share of this audience is female", "what do these viewers buy", "do these viewers watch Chicago P.D."):
            s = SR.bind(q, None, 'NCIS')
            self.assertNotEqual(s.source, 'page', q)

    def test_one_name_for_one_title(self):
        for q, want in (("how many people watched Chicago P.D. on Prime Video in 2023", 'Chicago P.D.'),      # was 'Chicago P'
                        ("chicago pd minutes viewed in 2023", 'Chicago P.D.'), ("svu minutes viewed in 2023", SVU),
                        ("ncis sydney viewers by age", 'NCIS: Sydney'),
                        ("Power Book II: Ghost minutes viewed in 2024 on Starz", 'Power Book II: Ghost')):
            s = SR.bind(q, None, '')
            self.assertEqual(s.source, 'words', q)
            self.assertEqual(s.name, want, q)
        # typed another way, the title keeps the name it was typed under and is still that title
        for q, want in (("blood of my blood viewers in 2025", 'Outlander: Blood of My Blood'), ("What do FBI Most Wanted viewers buy?", 'FBI: Most Wanted'),
                        ("special victims unit minutes on peacock in 2024", SVU)):
            s = SR.bind(q, None, '')
            self.assertEqual(s.source, 'words', q)
            self.assertTrue(NT.same_title(s.name, want), (q, s.name))

    def test_a_mate_on_the_screen_is_not_the_family(self):
        norm = lambda x: re.sub(r"[^a-z0-9]+", "", str(x or '').lower())      # noqa: E731
        for a, b in (('NCIS: Sydney', 'NCIS'), ('Power Book II: Ghost', 'Power'), ('Outlander: Blood of My Blood viewers', 'Outlander'),
                     (SVU, 'Law & Order'), ('FBI: Most Wanted', 'FBI'), ('Dune: Prophecy', 'Dune')):
            self.assertFalse(SR._same_family(a, b, norm), (a, b))
        for a, b in (('Landman Viewers on Paramount+', 'Landman'), ('Peacock - Avid Fan', 'Peacock'), ('Prime Video', 'Amazon Prime Video')):
            self.assertTrue(SR._same_family(a, b, norm), (a, b))
        from prometheus import named_subject as NS
        n2 = lambda x: re.sub(r"[^a-z0-9]+", " ", str(x or '').lower()).strip()   # noqa: E731
        self.assertFalse(NS.page_named("FBI: Most Wanted viewers in 2024", 'FBI', n2, set()))
        self.assertFalse(NS.page_named_outright("ncis sydney viewers by age", 'NCIS', n2, set()))
        self.assertFalse(NS.page_mentioned("Power Book II: Ghost minutes", 'Power', n2, set()))
        self.assertTrue(NS.page_named("FBI viewers in 2024", 'FBI', n2, set()))
        self.assertTrue(NS.page_named_outright("ncis viewers by age", 'NCIS', n2, set()))
        self.assertTrue(NS.page_named("compare ncis and ncis sydney", 'NCIS', n2, set()))

    def test_a_list_is_left_to_the_lanes(self):
        nine = ("Share of Peacock streaming minutes for Law & Order: Special Victims Unit; Law & Order; Chicago Fire; Chicago Med; "
                "Chicago P.D.")
        self.assertEqual(SR.bind(nine, None, '').source, SR._bind_any(nine, None, '').source)
        self.assertEqual(SR.bind(nine, None, '').name, SR._bind_any(nine, None, '').name)


# ============================================================================ 4. the continuation
class TheContinuation(unittest.TestCase):
    def test_a_landed_read_is_carried_under_its_whole_title(self):
        for subject in (SVU_READ_SUBJECT, "NCIS: Hawai'i on Paramount+", 'Power Book II: Ghost', 'Outlander: Blood of My Blood on Starz',
                        'FBI: Most Wanted', 'Chicago P.D. on Amazon Prime Video', 'Psych on Amazon Prime Video'):
            thread = [_user('how many people watched it'), _read(subject, '2023')]
            self.assertEqual(NT.read_head(thread[-1]['text']), subject)
            d = CONT.previous_deliverable(thread, '')
            self.assertEqual(d['kind'], 'read')
            self.assertTrue(NT.same_title(d['subject'], subject), (subject, d['subject']))
            self.assertEqual(SR.thread_subjects([{'role': 'agent', 'text': thread[-1]['text']}], '')[0], subject)
        # a reply that is not a header is not read as one
        for text in ("On it: reading X now.", "On Landman: 44% female. More: text", "On Reba McEntire: Leans female: 62%"):
            self.assertEqual(NT.read_head(text), '')

    def test_her_first_question_is_not_a_rerun_of_the_last_read(self):
        for year in ('2020', '2023'):
            q = HERS_1.replace('2020', year)
            before = CONT._classify(q, her_thread(year), None, 'Amazon Prime Video')
            self.assertEqual(before['kind'], 'rerun_window')                   # what it was read as
            c = CONT.classify(q, her_thread(year), None, 'Amazon Prime Video')
            self.assertEqual((c['kind'], c['subject']), ('named_other', PD))
            self.assertEqual(c['params']['named_title'], PD)
            self.assertTrue(NT.same_title(c['params']['carried'], SVU))
            self.assertIn('named_other', CONT.KINDS)

    def test_her_corrections_continue_on_the_title_she_named(self):
        offer = {'role': 'agent', 'text': SENT_1, 'meta': {'options': [{'label': 'Approve', 'send': 'Approve'}, {'label': 'Cancel', 'send': 'Cancel'}]}}
        thread = her_thread() + [_user(HERS_1), offer]
        c = CONT.classify(HERS_2, thread, None, 'Amazon Prime Video')
        self.assertEqual((c['kind'], c['subject']), ('correction', PD))
        thread2 = thread + [_user(HERS_2), {'role': 'agent', 'text': SENT_2}]
        c = CONT.classify(HERS_3, thread2, None, 'Amazon Prime Video')
        self.assertEqual((c['kind'], c['subject']), ('correction', PD))

    def test_the_same_for_other_families(self):
        cases = [('NCIS: Sydney on Paramount+', "do the same for ncis hawaii for the year 2023", "NCIS: Hawai'i"),
                 ('Power On Starz', "run it again for Power Book II: Ghost in 2024", 'Power Book II: Ghost'),
                 ('Outlander on Starz', "can you do the same for blood of my blood for 2025", 'Outlander: Blood of My Blood'),
                 ('Yellowstone', "do the same for 1883 for the year 2023", '1883'),
                 ('Chicago P.D. on Amazon Prime Video', "do the same for chicago fire for the year 2023", 'Chicago Fire'),
                 ('Landman Viewers on Paramount+', "re-run for Tulsa King viewers in September 2026", 'Tulsa King'),
                 ('Lore Olympus', "do that again for unOrdinary for 2025", 'unOrdinary')]
        for subject, q, want in cases:
            thread = [_user('how many people watched it in 2024'), _read(subject, '2024')]
            c = CONT.classify(q, thread, None, '')
            self.assertEqual((c['kind'], c['subject']), ('named_other', want), q)
        for subject, q, want in (('NCIS: Sydney on Paramount+', "no i need ncis origins", 'NCIS: Origins'),
                                 ('Lore Olympus', "no do this analysis for unOrdinary the WEBTOON", 'unOrdinary'),
                                 ('Power On Starz', "no, raising kanan", 'Power Book III: Raising Kanan'),
                                 ('Yellowstone', "no i meant 1923", '1923')):
            thread = [_user('how many people watched it in 2024'), _read(subject, '2024')]
            c = CONT.classify(q, thread, None, '')
            self.assertEqual((c['kind'], c['subject']), ('correction', want), q)

    def test_a_turn_that_names_no_title_is_what_it_was(self):
        thread = her_thread('2023')
        for q in ("re-run for 2022", "run it again for January 2024", "same read for Q4 2025", "no that is wrong", "no i need the monthly numbers",
                  "No, run what I asked", "no, build fresh", "no a pdf was created. where is it", "break that out by month", "yes", "Approve"):
            a, b = CONT._classify(q, thread, None, ''), CONT.classify(q, thread, None, '')
            self.assertEqual((a['kind'], a['subject']), (b['kind'], b['subject']), q)
        c = CONT.classify("re-run for 2022", thread, None, '')
        self.assertEqual(c['kind'], 'rerun_window')
        self.assertTrue(NT.same_title(c['subject'], SVU), c['subject'])        # the title, never the franchise

    def test_a_rerun_is_quoted_under_the_title_it_read(self):
        thread = [_user('how many people watched NCIS: Sydney on Paramount+ in 2024'), _read('NCIS: Sydney on Paramount+', '2024')]
        c = CONT.classify("re-run for 2023", thread, None, '')
        raw = CONT.rerun_confirm(c['deliverable'], c['window'], '$100')
        self.assertIn('NCIS: Sydney', raw['reply'])
        self.assertNotIn('the NCIS read', raw['reply'])


# ============================================================================ 5. the base of a read
class TheBase(unittest.TestCase):
    def _b(self, subject, source='catalog', key=''):
        return {'subject': subject, 's3_key': key or (re.sub(r'[^A-Za-z0-9]+', '_', subject) + '.csv'), 'source': source}

    def test_a_file_about_another_title_is_no_base(self):
        drops = [('Power On Amazon Prime', '', "special victims unit on amazon prime platform for the year 2023. minutes viewed"),
                 ('Power On Amazon Prime', '', HERS_3.replace('2020', '2023')), ('Power On Amazon Prime', '', "reacher minutes on amazon prime in 2024"),
                 ('NCIS', 'NCIS', "ncis sydney viewers by age"), ('Power', '', "Power Book II: Ghost minutes viewed in 2024 on Starz"),
                 ('Outlander', '', "blood of my blood viewers in 2025"), ('Yellowstone', '', "how many people watched 1883 in 2023"),
                 ('Law & Order', '', "svu minutes viewed in 2023"), ('Chicago Fire', '', "chicago pd minutes viewed in 2023"),
                 ('FBI', '', "FBI: Most Wanted viewers in 2024"), ('Amazon Prime Video', '', "chicago pd minutes viewed in 2023 on amazon prime"),
                 ('Dune (2021 Film)', '', "how many people watched the Dune: Prophecy series in 2025")]
        for subject, hint, text in drops:
            for source in ('catalog', 'page'):
                if source == 'page' and 'reacher' in text:
                    continue        # a title that is not listed: the shelf rule (the service's words alone) is the one that holds
                ok, why = NT.base_fits(self._b(subject, source), hint, text)
                self.assertFalse(ok, (subject, source, text))
                self.assertTrue(why)
        ok, _ = NT.base_fits(self._b('Power', 'subscriber_iq', 'subiq:power'), '', "Power Book II: Ghost viewers on Starz")
        self.assertFalse(ok)                                                    # a tracker is held to the rule too

    def test_a_file_the_words_name_or_point_at_stands(self):
        keeps = [('NCIS', 'catalog', '', "ncis viewers by age"), ('NCIS', 'page', '', "what do these viewers buy"),
                 ('Power On Amazon Prime', 'catalog', '', "power viewers on amazon prime by age"), ('Amazon Prime Video', 'page', '', "top brands here"),
                 ('Amazon Prime Video', 'catalog', '', "what share of Amazon Prime Video viewers watch SVU"),
                 ('Yellowstone', 'catalog', '', "who watches Yellowstone"), ('NCIS', 'page', '', "compare ncis and ncis sydney"),
                 ('Landman', 'page', '', "what about Landman season 2 viewers"), ('Psych', 'catalog', 'Psych', "month by month psych minutes in 2023"),
                 ('NCIS', 'page', '', "do these viewers also watch NCIS: Sydney"), ('Peacock', 'page', '', "what share of this audience is female")]
        for subject, source, hint, text in keeps:
            ok, why = NT.base_fits(self._b(subject, source), hint, text)
            self.assertTrue(ok, (subject, source, text, why))

    def test_the_resolver_wrapper_asks_once_more_for_the_named_title(self):
        shelf = {'ncis': self._b('NCIS'), 'ncis: sydney': self._b('NCIS: Sydney Viewers')}
        calls = []

        def resolver(hint, text, ctx=None, prefer_catalog=False, bound=False):
            calls.append((hint, text, bound))
            if bound and hint == 'NCIS: Sydney':
                return shelf.get('ncis: sydney') if self.own else None
            return shelf['ncis']
        held = NT.held(resolver)
        self.own = True
        got = held('', "ncis sydney viewers by age")
        self.assertEqual(got['subject'], 'NCIS: Sydney Viewers')                # its own file when there is one
        self.own = False
        self.assertIsNone(held('', "ncis sydney viewers by age"))               # else nothing: never the mate
        self.assertEqual(held('', "ncis viewers by age")['subject'], 'NCIS')    # the title itself is untouched
        self.assertEqual(held.__wrapped__, resolver)

    def test_a_subject_a_lane_bound_is_the_title_the_words_name(self):
        self.assertEqual(NT.rebound('Power', "Power Book II: Ghost minutes viewed in 2024 on Starz"), 'Power Book II: Ghost')
        self.assertEqual(NT.rebound('NCIS', "ncis sydney viewers by age"), 'NCIS: Sydney')
        self.assertEqual(NT.rebound('Amazon Prime Video', "chicago pd minutes viewed in 2023 on amazon prime"), PD)
        self.assertEqual(NT.rebound('NCIS', "ncis viewers by age"), 'NCIS')
        self.assertEqual(NT.rebound('NCIS', "what share of NCIS viewers also watch NCIS: Sydney"), 'NCIS')
        self.assertEqual(NT.rebound(PD, HERS_2), PD)
        self.assertEqual(NT.rebound('Power', "Power Book II: Ghost minutes", {'kind': 'report'}), 'Power')   # an approved quote stands
        self.assertEqual(NT.rebound('', HERS_2), '')

    def test_a_calendar_year_is_not_a_count_of_years(self):
        from prometheus.legacy import rankers as RK
        self.assertIsNone(RK._pm_detect_multi_year_ask("i need minutes viewed of special victims unit on amazon prime for the 2020 year"))
        self.assertIsNone(RK._pm_detect_multi_year_ask("for the 2023 year"))
        self.assertEqual(len(RK._pm_detect_multi_year_ask("a 5 year analysis of Will and Grace")), 5)
        self.assertEqual(len(RK._pm_detect_multi_year_ask("4-5 year trend")), 5)


# ============================================================================ 6. stored figures
class StoredFigures(unittest.TestCase):
    def setUp(self):
        import insights_ledger as IL
        self.IL = IL
        names = {'ncis': 'NCIS', 'power': 'Power', 'outlander': 'Outlander', 'law_order': 'Law & Order', 'amazon_prime_video': 'Amazon Prime Video',
                 'law_order_special_victims_unit': SVU_READ_SUBJECT, 'yellowstone': 'Yellowstone', 'landman': 'Landman', 'paramount': 'Paramount+',
                 'chicago_p_d': 'Chicago P.D. on Amazon Prime Video', 'dune': 'Dune', 'psych': 'Psych'}
        self.doc = {'subjects': {k: {'subject': v, 'entries': [{'q': 'x'}]} for k, v in names.items()}}

    def _r(self, subject, question):
        return self.IL._resolve_subject(self.doc, subject=subject, question=question)

    def test_a_record_under_another_title_is_never_used(self):
        for subject, question in (("NCIS: Sydney on Paramount+", "how many people watched NCIS: Sydney"), ("Power Book II: Ghost", "minutes viewed"),
                                  ("Outlander: Blood of My Blood", "viewers in 2025"), (None, "how many watched outlander blood of my blood"),
                                  ("Chicago Fire", "chicago fire minutes on amazon prime video in 2023"), (None, "chicago fire minutes on amazon prime video in 2023"),
                                  ("1883", "1883 viewers"), ("Dune: Prophecy", "viewers"), (None, "law and order organized crime on peacock"),
                                  ("Law & Order: Organized Crime", "minutes on peacock")):
            self.assertIsNone(self._r(subject, question), (subject, question))

    def test_one_title_one_record_however_it_is_typed(self):
        for subject, question, want in (("SVU", "svu minutes on amazon prime", 'law_order_special_victims_unit'), ("Special Victims Unit", "x", 'law_order_special_victims_unit'),
                                        (None, "svu on amazon prime", 'law_order_special_victims_unit'), ("Chicago PD", "chicago pd minutes on amazon prime video", 'chicago_p_d'),
                                        ("chicago pd", "minutes", 'chicago_p_d'), ("Chicago P.D.", HERS_2, 'chicago_p_d'), ("NCIS", "ncis viewers by age", 'ncis'),
                                        ("Power", "power viewers", 'power'), (None, "outlander season 7 viewers", 'outlander'), ("Law & Order", "minutes", 'law_order'),
                                        ("Landman Viewers on Paramount+", "landman by age", 'landman'), (None, "what do amazon prime video viewers watch", 'amazon_prime_video'),
                                        ("Amazon Prime Video", "total minutes", 'amazon_prime_video'), ("Parents of NCIS viewers", "what do they buy", 'ncis')):
            self.assertEqual(self._r(subject, question), want, (subject, question))

    def test_a_new_figure_files_where_the_title_already_is(self):
        self.assertEqual(self.IL.bucket_key_for(self.doc, 'Chicago PD on Amazon Prime Video'), 'chicago_p_d')
        self.assertEqual(self.IL.bucket_key_for(self.doc, 'chicago pd'), 'chicago_p_d')
        self.assertEqual(self.IL.bucket_key_for(self.doc, 'Chicago Fire'), 'chicago_fire')
        self.assertEqual(self.IL.bucket_key_for(self.doc, 'NCIS: Sydney'), 'ncis_sydney')


# ============================================================================ 7. the wiring
class TheWiring(unittest.TestCase):
    def test_the_splices_in_the_chat_module(self):
        chat = _legacy_view()
        held = "_pm_generation_base = __import__('prometheus.named_title', fromlist=['held']).held(_pm_generation_base)"
        self.assertEqual(chat.count(held), 1)
        self.assertLess(chat.index("_pm_generation_base = _primary_profile.held(_pm_generation_base"), chat.index(held))   # outermost
        rebound = "bind_subject = __import__('prometheus.named_title', fromlist=['rebound']).rebound(bind_subject, text, panel_confirm)"
        self.assertEqual(chat.count(rebound), 1)
        self.assertLess(chat.index("bind_subject = bind_subject or _subject_rules.pinned_bind_for(text, history,"), chat.index(rebound))
        years = "if _my_subj and not __import__('prometheus.named_title', fromlist=['other_than']).other_than(text, _my_subj):"
        self.assertEqual(chat.count(years), 1)
        self.assertLess(len(chat.splitlines()), 19900)                          # the line ratchet holds

    def test_the_gate_and_the_exit(self):
        gate = _read_src('prometheus', 'turn_gate.py')
        at = gate.index("if kind == 'named_other':")
        self.assertLess(gate.index("if kind == 'new_ask':\n        return None"), at)
        self.assertLess(at, gate.index("if kind == 'rerun_window':"))            # ahead of the re-run card
        branch = gate[at:gate.index("# ---- a re-run in a window")]
        self.assertIn("body['bind_subject'] = body.get('bind_subject') or title", branch)
        for banned in ('consume', 'charge', 'send_email', 'put_object'):
            self.assertNotIn(banned, branch)
        guards = _read_src('prometheus', 'guards.py')
        self.assertIn('_nt.say_left_out(raw)', guards)
        self.assertIn('_nt.tidy(v)', guards)
        self.assertIn("_nt.stand_down(out, text, history, page, understanding_title=u_title)", _read_src('prometheus', 'continuation.py'))   # the Understanding's title rides in (2026-11-20)
        self.assertIn("_nt.bound_words(text, history, page, s", _read_src('prometheus', 'subject_rules.py'))
        self.assertIn("_nt.bucket_fits(question, held, subject)", _read_src('insights_ledger.py'))

    def test_the_module_keeps_to_itself(self):
        src = _read_src('prometheus', 'named_title.py')
        for banned in ('send_email', 'send_raw_email', 'boto3', "client('ses'", '@crosswalknyc.com', 'request.json', 'request.get_json',
                       'put_object', 'consume_credit'):
            self.assertNotIn(banned, src, banned)
        self.assertNotIn('\u2014', src)
        self.assertNotIn('\u2013', src)
        low = NT.NOT_HELD.lower()
        for w in INTERNAL:
            self.assertNotIn(w, low)

    def test_the_kill_switch(self):
        os.environ['PM_NAMED_TITLE'] = '0'
        try:
            self.assertEqual(NT.other_than(HERS_2, SVU_READ_SUBJECT), '')
            self.assertEqual(NT.slot_title(HERS_2), '')
            self.assertEqual(CONT.classify(HERS_1.replace('2020', '2023'), her_thread('2023'), None, '')['kind'], 'rerun_window')
            self.assertTrue(NT.base_fits({'subject': 'NCIS', 's3_key': 'x.csv', 'source': 'catalog'}, '', 'ncis sydney')[0])
        finally:
            os.environ.pop('PM_NAMED_TITLE', None)
        self.assertEqual(NT.other_than(HERS_2, SVU_READ_SUBJECT), PD)


# ============================================================================ 8. the door
_CHILD = r'''
import json, os, re, sys, tempfile
sys.path.insert(0, sys.argv[1])
SHELF = ['Amazon Prime Video', 'Peacock', 'Paramount+', 'Starz', 'NCIS', 'Power', 'Power On Amazon Prime', 'Outlander', 'Yellowstone',
         'Law & Order', 'Chicago Fire', 'FBI', 'Dune (2021 Film)', 'Landman', 'Psych']
def _key(n):
    return re.sub(r'[^A-Za-z0-9]+', '_', n).strip('_') + '_09_01_2026_10_00.csv'
cat = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
json.dump({'jobs': [{'display_name': n, 's3_key': _key(n), 'category': 'GENERAL', 'status': 'cached', 'source': 's3'} for n in SHELF],
           'genpop_csv': ''}, cat)
cat.close()
os.environ['PM_REPLAY_CATALOG'] = cat.name
from prometheus import replay
replay.install_isolation()
h = replay.harness()
h.host.set('_neutralize_data_delimiters', lambda s, *a, **k: str(s or ''))
from prometheus.host import host as PH
for n in SHELF:
    h.s3.put_object(Bucket='dashboard-inputs', Key=_key(n), Body=replay._profile_csv(n))
replay._STATE['baseline'] = dict(h.s3.objects)
out = []
for it in json.load(sys.stdin):
    replay._restore_baseline(h)
    replay._STATE['question'] = it['question']
    page = it.get('page') or ''
    replay._canned_dual(h, page or None, None)
    canned = replay._canned_for('analyze', page or 'This audience', None)
    calls = []
    def _json(system, user, *a, _c=canned, **k):
        calls.append('json')
        return {'success': True, 'data': json.loads(json.dumps(_c)), 'model': 'replay'}
    h.patch('_pm_claude_json', _json)
    def _data(system, user, _r=it.get('reading'), _c=canned, **k):
        calls.append(str(k.get('surface')))
        if k.get('surface') == 'reader':
            return dict(_r) if _r else {}
        return dict(_c)
    PH.bind(claude_data=_data, can_open_profile=lambda u, k: True)
    pc = {}
    if page:
        pc = {'primary': {'s3_key': _key(page), 'name': page},
              'view_context': {'view_id': it.get('view') or 'profileIQ', 'view_title': it.get('title') or page, 'data': it.get('data') or {}}}
    body = {'text': it['question'], 'history': it.get('history') or [], 'page_context': pc, 'client': 'dashboard', 'persist': False,
            'surface': 'analyze'}
    n0 = len(replay._STATE['captured'])
    status, payload = h.post(replay._STATE.get('door') or '/api/brief-chat/analyze', body, session={'username': replay.REPLAY_USER['username']})
    rec = (replay._STATE['captured'][n0:] or [{}])[-1]
    raw = payload.get('raw') if isinstance(payload, dict) and isinstance(payload.get('raw'), dict) else payload
    raw = raw if isinstance(raw, dict) else {}
    out.append({'route': rec.get('route'), 'outcome': rec.get('outcome'), 'subject': rec.get('subject'),
                'reply': replay._reply_text(raw) or str((payload or {}).get('text') or ''),
                'offer': bool(raw.get('panel_offer')), 'job': bool(raw.get('read_job_id')),
                'chips': [str(c) for c in (raw.get('followups') or [])], 'calls': calls,
                'reason': ((payload or {}).get('decision') or {}).get('reason') if isinstance(payload, dict) else None})
os.unlink(cat.name)
print('@@@' + json.dumps(out))
'''

RANKERS = {'page': 'Amazon Prime Video', 'view': 'cultureRankerIQ', 'title': 'Rankers',
           'data': {'filters': {'geo_type': 'National', 'historic': True, 'asof': '2023-12-31', 'lookback_days': 731}, 'board': 'streaming'}}


def _her_items(year):
    q1, q3 = HERS_1.replace('2020', year), HERS_3.replace('2020', year)
    t0 = her_thread(year)
    offer = {'role': 'agent', 'text': SENT_1.replace('2020', year),
             'meta': {'options': [{'label': 'Approve', 'send': 'Approve'}, {'label': 'Cancel', 'send': 'Cancel'}]}}
    t1 = t0 + [_user(q1), offer]
    t2 = t1 + [_user(HERS_2), {'role': 'agent', 'text': SENT_2}]
    return [dict(RANKERS, question=q1, history=t0), dict(RANKERS, question=HERS_2, history=t1), dict(RANKERS, question=q3, history=t2)]


def _mate_thread(subject, ask):
    return [_user(ask), _read(subject, '2024')]


SYD = _mate_thread('NCIS: Sydney on Paramount+', "how many people watched NCIS: Sydney on Paramount+ in 2024")
PDT = _mate_thread('Chicago P.D. on Amazon Prime Video', "chicago pd minutes viewed in 2023 on amazon prime")

# the franchise mate is the one on the shelf, open on the screen: (page, question, the title asked for, the mate)
ON_SCREEN = [('NCIS', "how many people watched NCIS: Sydney in 2024", 'NCIS: Sydney', 'NCIS'),
             ('NCIS', "ncis sydney viewers by age", 'NCIS: Sydney', 'NCIS'),
             ('Power', "Power Book II: Ghost minutes viewed in 2024 on Starz", 'Power Book II: Ghost', 'Power'),
             ('Outlander', "how many people watched Outlander: Blood of My Blood in 2025", 'Blood of My Blood', 'Outlander'),
             ('Outlander', "blood of my blood viewers in 2025", 'Blood of My Blood', 'Outlander'),
             ('Yellowstone', "how many people watched 1883 in 2023", '1883', 'Yellowstone'),
             ('Law & Order', "svu minutes viewed in 2023", 'Special Victims Unit', 'Law & Order'),
             ('Chicago Fire', "chicago pd minutes viewed in 2023", 'Chicago P.D.', 'Chicago Fire'),
             ('FBI', "FBI: Most Wanted viewers in 2024", 'FBI: Most Wanted', 'FBI'),
             ('Dune (2021 Film)', "how many people watched the Dune: Prophecy series in 2025", 'Dune: Prophecy', 'Dune (2021 Film)'),
             ('Amazon Prime Video', "special victims unit on amazon prime platform for the year 2023. minutes viewed", 'Special Victims Unit', 'Amazon Prime Video'),
             ('Amazon Prime Video', "chicago pd minutes viewed in 2023 on amazon prime", 'Chicago P.D.', 'Amazon Prime Video')]
# the same with nothing open: the mate is only on the shelf
ON_SHELF = [(q, want, mate) for _p, q, want, mate in ON_SCREEN if 'by age' not in q]
# the thread carries a title: (history, question, what the reply must name, what it must not)
IN_THREAD = [(SYD, "break that out by month", 'NCIS: Sydney', 'reading NCIS now'),
             (SYD, "what about NCIS: Hawai'i", "NCIS: Hawai'i", 'reading NCIS now'),
             (SYD, "re-run for 2023", 'NCIS: Sydney', 'the NCIS read'),
             (SYD, "what share of them also watch Chicago P.D.?", 'NCIS: Sydney', 'reading Chicago'),
             (PDT, "what about Chicago Med", 'Chicago Med', 'reading Chicago P.D'),
             (PDT, "no i need chicago med", 'Chicago Med', 'reading Chicago P.D'),
             (PDT, "no i need the monthly numbers", 'Chicago P.D.', 'Chicago Med'),
             (PDT, "now do Law & Order: Organized Crime", 'Law & Order: Organized Crime', 'reading Law & Order now'),
             (PDT, "do the same for chicago fire for the year 2023", 'Chicago Fire', 'Chicago P.D'),
             (SYD, "same for ncis", 'NCIS', 'NCIS: Sydney')]
# the named title is the one held, or the words point at the screen: nothing changes
HELD = [('NCIS', "ncis viewers by age", 'NCIS'), ('', "who watches Yellowstone", 'Yellowstone'), ('Yellowstone', "what do these viewers buy", 'Yellowstone'),
        ('NCIS', "compare ncis and ncis sydney", 'NCIS'), ('Landman', "what about Landman season 2 viewers", 'Landman'),
        ('Psych', "month by month psych minutes in 2023", 'Psych')]
SPELLINGS = ["Chicago PD viewers in 2023", "Chicago P.D. viewers in 2023", "chicago pd viewers in 2023",
             "how many people watched Chicago P.D. on Prime Video in 2023"]

DOOR_ITEMS = (
    _her_items('2020') + _her_items('2023')                                                         # 0-5
    + [dict(page=p, question=q) for p, q, _w, _m in ON_SCREEN]                                      # 6-17
    + [dict(question=q) for q, _w, _m in ON_SHELF]                                                  # 18-28
    + [dict(question=q, history=h) for h, q, _w, _n in IN_THREAD]                                   # 29-38
    + [dict(page=p, question=q) for p, q, _w in HELD]                                               # 39-44
    + [dict(question=q) for q in SPELLINGS]                                                         # 45-48
    # 49-50: her earlier turns the same night, on a window the data covers
    + [dict(RANKERS, question="i need minutes viewed of special victims unit on amazon prime for the 2023 year",
            reading={'intent': 'analyze', 'confidence': 0.82, 'subjects': [{'as_written': 'special victims unit', 'catalog': '', 'kind': 'title'}],
                     'flags': ['custom_window']}),
       dict(RANKERS, question="special victims unit on amazon prime platform for the year 2023.  minutes viewed")]
)


def _door(items):
    env = dict(os.environ, REGRESSION_TEST_MODE='1')
    try:
        p = subprocess.run([sys.executable, '-c', _CHILD, ROOT], input=json.dumps(items), capture_output=True, text=True,
                           env=env, timeout=900)
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
    """The words through the door as the dashboard sends them, on a shelf
    where the franchise mate is the title that is held. The model is
    canned, so a reply that names the mate is the defect showing."""

    @classmethod
    def setUpClass(cls):
        cls.res = _door(list(DOOR_ITEMS))

    def setUp(self):
        if not self.res:
            if os.environ.get('PM_DOOR_REQUIRED') == '1':
                self.fail('the door checks did not run: the recording host is required here')
            self.skipTest('the recording host (scripts/_pm_behavior.py in the parent repo) is not on the path here')
        self.assertEqual(len(self.res), len(DOOR_ITEMS))

    def _plain(self, reply):
        low = reply.lower()
        for w in INTERNAL:
            self.assertNotIn(w, low, reply[:200])

    def test_her_questions_as_she_typed_them(self):
        first, second, third = self.res[0:3]
        for r in (first, second, third):
            self.assertNotIn('Law & Order', r['reply'], r['reply'][:200])
            self.assertNotIn('Special Victims Unit', r['reply'])
            self.assertNotIn('Power', r['reply'])
            self.assertFalse(r['job'], 'nothing is started on another title')
            self.assertNotEqual(r['route'], 'rerun_window')
            self._plain(r['reply'])
        # her window was 2020: the standing line about the first day of the data answers the two that state it
        for r in (first, third):
            self.assertEqual((r['route'], r['outcome']), ('data_floor', 'before'))
            self.assertIn('January 1, 2021', r['reply'])
        # the one that states no window is about the title she named
        self.assertIn('Chicago P.D.', second['reply'])
        self.assertIn('Chicago P.D.', str(second['subject']))

    def test_her_questions_on_a_window_the_data_covers(self):
        for r in self.res[3:6]:
            self.assertEqual(r['route'], 'reasoned_metrics', r['reply'][:160])
            self.assertEqual(str(r['subject']), 'Chicago P.D.')
            self.assertIn('Chicago P.D.', r['reply'])
            self.assertNotIn('Chicago P.D..', r['reply'])
            for other in ('Law & Order', 'Special Victims Unit', 'Power', 'Psych'):
                self.assertNotIn(other, r['reply'], r['reply'][:200])
            self.assertTrue(r['offer'] or r['job'])
            self.assertEqual(r['calls'], [], 'the words settle it; no model is asked which title')
            self._plain(r['reply'])
        self.assertTrue(self.res[3]['reply'].startswith('I can pull how many people in the US watched Chicago P.D. across one year.'))
        self.assertEqual(self.res[3]['chips'], ['Approve', 'Cancel'])

    def test_a_mate_open_on_the_screen_never_answers(self):
        for (page, q, want, mate), r in zip(ON_SCREEN, self.res[6:18]):
            self.assertIn(want, r['reply'], (q, r['reply'][:200]))
            self.assertNotEqual(r['route'], 'page_analysis', q)
            self.assertNotIn('open on your screen', r['reply'], q)
            self.assertNotIn(f'reading {mate} now', r['reply'], q)
            self.assertNotEqual(str(r['subject']), mate, q)
            self.assertFalse(r['job'], q)                                       # nothing is held for it: nothing is read from the mate
            self.assertTrue(r['offer'], q)
            self._plain(r['reply'])

    def test_a_mate_on_the_shelf_never_answers(self):
        for (q, want, mate), r in zip(ON_SHELF, self.res[18:29]):
            self.assertIn(want, r['reply'], (q, r['reply'][:200]))
            self.assertNotIn(f'reading {mate} now', r['reply'], q)
            self.assertNotIn('reading Power On Amazon Prime now', r['reply'], q)
            self.assertNotIn('reading Starz now', r['reply'], q)
            self.assertNotEqual(str(r['subject']), mate, q)
            self.assertFalse(r['job'], q)
            self._plain(r['reply'])

    def test_the_mate_left_out_is_said_once_in_plain_words(self):
        said = [r for r in self.res[6:29] if 'is a different title, so I have left it out.' in r['reply']]
        self.assertGreaterEqual(len(said), 6)
        for r in said:
            self.assertTrue(r['reply'].startswith('There is nothing on '), r['reply'][:120])
            self.assertEqual(r['reply'].count('is a different title'), 1)
            self.assertIn(' on the dashboard yet. ', r['reply'])
            self.assertIn('Pulling this data will cost', r['reply'])            # then the standing offer, on the title asked for
        # a title carried from the turn before is not announced again
        self.assertNotIn('is a different title', self.res[29]['reply'])

    def test_a_title_carried_by_the_thread(self):
        for (hist, q, want, never), r in zip(IN_THREAD, self.res[29:39]):
            self.assertIn(want, r['reply'], (q, r['reply'][:200]))
            self.assertNotIn(never, r['reply'], (q, r['reply'][:200]))
            self._plain(r['reply'])
        rerun = self.res[31]
        self.assertEqual((rerun['route'], rerun['outcome']), ('rerun_window', 'asked_confirm'))   # a re-run is still a re-run
        self.assertTrue(rerun['reply'].startswith('I can run the NCIS: Sydney read again on January 1 to December 31, 2023.'))
        held = self.res[38]                                                      # "same for ncis": NCIS is on the shelf, so it is read
        self.assertTrue(held['job'])
        self.assertIn('reading NCIS now', held['reply'])

    def test_the_title_that_is_held_is_answered_as_before(self):
        for (page, q, want), r in zip(HELD, self.res[39:45]):
            self.assertIn(want, r['reply'] + ' ' + str(r['subject']), q)
            self.assertNotIn('is a different title', r['reply'], q)
            self.assertFalse(r['offer'] and 'There is nothing on' in r['reply'], q)

    def test_one_title_under_every_spelling(self):
        for q, r in zip(SPELLINGS, self.res[45:49]):
            self.assertIn('Chicago P.D.', r['reply'], q)
            self.assertIn('Chicago P.D.', str(r['subject']), q)
            self.assertNotIn('Chicago Pd', r['reply'] + str(r['subject']), q)
            self.assertNotIn('Chicago P across', r['reply'], q)

    def test_her_earlier_turns_the_same_night(self):
        package, bare = self.res[49:51]
        for r in (package, bare):
            self.assertIn('Special Victims Unit', r['reply'], r['reply'][:200])
            self.assertNotIn('year profiles', r['reply'])                       # "the 2023 year" is one year, and not the open profile's
            self.assertNotIn('Power', r['reply'])
            self.assertNotEqual(r['route'], 'page_analysis')
            self.assertNotEqual(str(r['subject']), 'Amazon Prime Video')
            self.assertFalse(r['job'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
