#!/usr/bin/env python3
"""No data can be pulled from before January 1, 2021 (Jenna 2026-11-18).

Jenna, verbatim: "It should always say no data can be pulled from before
jan 1 2021. Paula Vazquez". Paula (Dick Wolf Productions) asked for
viewing minutes on Prime Video for Psych "beginning 12/30/2019 through
12/14/2021", then for Special Victims Unit and Chicago P.D. "for the
year 2020", and was handed figures for 2019 and 2020.

What is held here, surface by surface, on her words and on the cases the
rule names:

  TheDateLivesInOnePlace    the literal sits in migration.event_window
                            and nowhere else in the app's code
  TheWords                  all before, across, a compare, "since",
                            "the last ten years", "all time"
  NeverTrippedBy            a year in a title or a name, a birth or
                            founding year, an older title's audience now
  HowFarBack                "at what point does the data begin"
  TheChatTurn               the sentence stops the turn before any lane;
                            a crossing ask runs on the corrected window
                            and the reply says so first; the default
                            window and every later window are as before
  TheBrief                  profile, tracker and the key surface, after
                            the binders; the approval; the brief summary
  PricedReads               nothing quoted or charged, an older quote too
  TheReadItself             the prompt rule and the check on the answer
  GuidedFlows               journeys, twins, valuations and their before
                            window, trackers and their look-back
  Forms                     the dashboard submit routes
  StoredRows                a banked row from before the floor is never
                            consulted (and is left as stored)
  Wording                   plain, no method words, no em dashes

Hermetic: pure modules, the service door over a stub host
(test_pm_new_chat_no_bleed.World), and functions lifted from source.
No app boot, no model, no S3, no mail.
"""
from __future__ import annotations

import ast
import datetime
import importlib.util
import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'AKIATESTNOTREAL000000')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'not-a-real-secret')
os.environ.setdefault('AWS_SHARED_CREDENTIALS_FILE', '/nonexistent/aws/credentials')
os.environ.setdefault('AWS_CONFIG_FILE', '/nonexistent/aws/config')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from migration import event_window as EW  # noqa: E402
from prometheus import data_floor as F  # noqa: E402

TODAY = datetime.date(2026, 11, 18)
YDAY = '2026-11-17'
SENT = "No data can be pulled from before January 1, 2021."

# Paula's own words, 2026-11-18
P_FIRST = "if i want viewershp minutes on amazon prime at what point does the data begin can i have 1/2021 through 12/2023?"
P_PSYCH = ("I want a report that tells me viewership information for the show Psych beginning 12/30/2019 through "
           "12/14/2021 but i'm not sure the data goes back that far.   does it?")
P_MONTHS = "Month by month Psych minutes across 2020 and 2021 on Prime Video"
P_SVU_RANGE = ("Just give me for the period 1/1/2020 to 12/31/2020 the total viewership minutes on the amazon prime platform "
               "and then give me the total viewership minutes of the special victims unit show and also chicago PD")
P_SVU_YEAR = "i need minutes viewed of special victims unit on amazon prime for the 2020 year"
P_ONE_YEAR = "i ony need one year - 2020"
P_SVU_YEAR2 = "special victims unit on amazon prime platform for the year 2020.  minutes viewed"
P_PD = "no i need CHICAGO PD show minutes viewed in 2020 on amazon prime"


def _read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as fh:
        return fh.read()


def _grab(src, name):
    """The source of one top-level function."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} not found")


def _gate(text, question=None):
    return F.gate(text, today=TODAY, question=question)


class TheDateLivesInOnePlace(unittest.TestCase):
    def test_the_constant_and_the_sentence(self):
        self.assertEqual(EW.DATA_FLOOR, '2021-01-01')
        self.assertEqual(EW.data_floor_label(), 'January 1, 2021')
        self.assertEqual(EW.data_floor_sentence(), SENT)
        self.assertEqual(F.FLOOR, EW.DATA_FLOOR)
        self.assertEqual(F.SENTENCE, SENT)

    def test_no_other_code_file_writes_the_date(self):
        """The ISO date, the tuple and the written date appear in code only
        in migration/event_window.py. Comments and docstrings may name the
        rule; a test may too."""
        rx = re.compile(r"2021-01-01|\(\s*2021\s*,\s*0?1\s*,\s*0?1\s*\)|January 1, 2021|Jan 1,? 2021")   # a reader's own words quoted as an example ('a start of 1/1/2021') are not the date
        offenders = []
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ('scripts', 'node_modules', '.git', '__pycache__', 'site', 'static', 'design', 'data', 'docs', 'tests')]
            for fn in files:
                if not fn.endswith('.py'):
                    continue
                rel = os.path.relpath(os.path.join(base, fn), ROOT)
                if rel == os.path.join('migration', 'event_window.py'):
                    continue
                try:
                    with open(os.path.join(base, fn), encoding='utf-8') as fh:
                        src = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
                if not rx.search(src):
                    continue
                try:
                    tree = ast.parse(src)
                except SyntaxError:
                    continue
                docs = set()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        d = ast.get_docstring(node, clean=False)
                        if d:
                            docs.add(d)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) and rx.search(node.value) \
                            and node.value not in docs:
                        offenders.append(f"{rel}:{node.lineno}")
                    if isinstance(node, ast.Call) and getattr(node.func, 'attr', getattr(node.func, 'id', '')) in ('date', 'datetime') \
                            and [getattr(a, 'value', None) for a in node.args[:3]] == [2021, 1, 1]:
                        offenders.append(f"{rel}:{node.lineno}")
        self.assertEqual(offenders, [], "the data floor is written in one place only")

    def test_statuses(self):
        self.assertEqual(EW.floor_status('2019-12-30', '2021-12-14'), 'straddle')
        self.assertEqual(EW.floor_status('2020-01-01', '2020-12-31'), 'before')
        self.assertEqual(EW.floor_status('2021-01-01', '2021-12-31'), 'ok')
        self.assertEqual(EW.floor_status('2020-12-31'), 'straddle')         # an open end
        self.assertEqual(EW.floor_status('not a date'), '')
        self.assertEqual(EW.clamp_to_floor('2019-12-30', '2021-12-14'), ('2021-01-01', '2021-12-14', 'straddle'))
        self.assertEqual(EW.clamp_to_floor('2022-03-01', '2022-04-01'), ('2022-03-01', '2022-04-01', 'ok'))
        self.assertEqual(EW.window_string_floor_status('2020-01-01 TO 2020-12-31'), 'before')
        self.assertEqual(EW.window_string_floor_status({'start': '2019-01-01', 'end': '2023-01-01'}), 'straddle')
        self.assertEqual(EW.window_string_floor_status(''), '')

    def test_the_standing_default_is_clear_of_the_floor(self):
        s, e = EW.default_window(TODAY)
        self.assertEqual(EW.floor_status(s, e), 'ok')
        self.assertEqual(EW.spec_floor_status({'date_range': EW.default_window_string(TODAY)}), 'ok')
        self.assertIsNone(EW.refuse_spec_before_floor({'date_range': EW.default_window_string(TODAY)}))

    def test_a_spec_before_the_floor_is_refused(self):
        for spec in ({'date_range': '2020-01-01 TO 2020-12-31'}, {'date_range': {'start': '2019-12-30', 'end': '2021-12-14'}},
                     {'cut_date_range': '2019-07-04 TO 2019-07-07'}, {'subiq': {'air_window': {'start': '2019-06-16', 'end': '2019-08-04'}}}):
            with self.assertRaises(EW.DataFloorRefusal) as cm:
                EW.refuse_spec_before_floor(spec, 'run-1')
            self.assertEqual(str(cm.exception), SENT)
            self.assertEqual(cm.exception.client_message, SENT)


class TheWords(unittest.TestCase):
    def _status(self, text):
        hit = F.read(text, today=TODAY)
        return hit['status'] if hit else None

    def test_all_of_it_before(self):
        for q in (P_SVU_RANGE, P_SVU_YEAR, P_ONE_YEAR, P_SVU_YEAR2, P_PD, "who watched The Office in 2018",
                  "Q3 2020 Hulu subscribers", "Netflix viewers December 2020", "Suits viewers from 1/1/20 to 12/31/20",
                  "Stranger Things viewers 7/1/2019 - 7/31/2019", "FY2020 Walmart shoppers", "Nike buyers 2020",
                  "Tiger King viewers in March 2020", "what did Yellowstone viewers buy during 2019 and 2020"):
            self.assertEqual(self._status(q), 'before', q)

    def test_it_crosses_the_floor(self):
        for q in (P_PSYCH, P_MONTHS, "from 2018 to 2022 Disney+ signups", "2019-2023 Peacock viewers",
                  "Netflix viewers from 12/15/2020 to 1/15/2021", "Peacock signups July 2020 through March 2021",
                  "Nike buyers between 2019 and 2023", "Netflix viewers since 2019", "show me Psych viewers from 1/1/2019 to now",
                  "Nike buyers over the last ten years", "Nike buyers over the last 10 years", "trailing 72 months Costco",
                  "the last decade of Marvel viewers", "all time viewers of Stranger Things",
                  "Stranger Things all-time viewership on Netflix", "show me the entire history of Costco shoppers"):
            self.assertEqual(self._status(q), 'straddle', q)

    def test_a_compare_with_one_side_before(self):
        for q in ("Costco shoppers 2020 vs 2025", "compare 2019 and 2024 for Target"):
            self.assertEqual(self._status(q), 'compare', q)

    def test_on_or_after_the_floor_reads_nothing(self):
        for q in ("calendar 2021 Netflix", "Netflix viewers January 2021", "Peacock viewers 2022 to 2024",
                  "build a profile of Nike buyers trailing 12 months", "Landman viewers last 90 days",
                  "Fast & Furious 9 viewers in the past 5 years", "Vin Diesel 2021-2026 YTD", "Reba 2023/2024 TU",
                  "Jan 19 to Feb 15 2024", "Q4 2025", "since March", "the 2025 calendar year", "Trailing 12 months", "Approve", "Yes",
                  "viewers of Suits in the summer of 2023", "Netflix viewers from 1/1/2021 to 12/31/2023"):
            self.assertIsNone(F.read(q, today=TODAY), q)

    def test_the_first_day_is_the_floor_itself(self):
        self.assertIsNone(F.read("Netflix viewers from 2021-01-01 to 2021-06-30", today=TODAY))
        self.assertEqual(self._status("Netflix viewers from 2020-12-31 to 2021-06-30"), 'straddle')


class NeverTrippedBy(unittest.TestCase):
    """The rule names these: a year that is part of a title or a name, a
    birth year, a founding year in passing, and a question about an older
    title's audience now."""

    def test_titles_names_and_passing_years(self):
        for q in ("who watches 1923 today", "Blade Runner 2049 audience", "Wonder Woman 1984 viewers",
                  "profile of 2020 Tokyo Olympics viewers", "Tokyo 2020 viewers", "Euro 2020 audience", "Class of 2019 audience",
                  "Taylor Swift, born in 1989, fans", "Levi's, founded in 1853, buyers this year", "Joker (2019) audience",
                  "the 2019 film Joker viewers", "audience of the film Tenet (released 2020)", "Friends (1994-2004) audience today",
                  "The Office 2005-2013 audience", "show me the Costco - 2020 Total Universe profile", "UFC 249 viewers",
                  "Super Bowl 2020 viewers", "who watched the 2020 election coverage"):
            self.assertIsNone(_gate(q), q)

    def test_an_older_titles_audience_now_is_a_normal_question(self):
        for q in ("who watches Friends today", "Friends premiered in 1994. who watches it now?", "viewers of Ozark season 1",
                  "viewers of Yellowstone since it premiered", "In 2019 Disney launched Disney+. Who subscribes now?",
                  "The show ended in 2019, who still watches it?", "The Office ran from 2005 to 2013. Who watches now?",
                  "viewers of Marvel movies released between 2015 and 2019", "The show was huge in 2019. Who watches it now?",
                  "Hamilton premiered on Disney+ in July 2020. Who watches it?", "build a profile of Psych viewers",
                  "Nike was founded in 1964. Who buys Nike today?"):
            self.assertIsNone(_gate(q), q)

    def test_a_reply_about_the_subject_is_not_a_window(self):
        q = "Cruel Summer has 2 seasons. Cover all seasons, the most recent season, or a specific season?"
        self.assertIsNone(_gate("it was in 2019, only 2 seasons", question=q))
        # to a date question the bare year IS the window, and it is before the floor
        out = _gate("2019", question="Which exact dates should this cover?")
        self.assertEqual(out['action'], 'stop')

    def test_a_full_ask_under_a_which_subject_question_is_still_an_ask(self):
        q = "Do you mean for The Amazon Prime Platform, or Psych on Amazon Prime Video?"
        for reply in (P_SVU_YEAR2, "chicago pd minutes viewed in 2020 on amazon prime"):
            out = _gate(reply, question=q)
            self.assertEqual(out['action'], 'stop', reply)
            self.assertTrue(out['payload']['reply'].startswith(SENT))
        self.assertIsNone(_gate("Psych on Amazon Prime Video", question=q))

    def test_a_long_pasted_message_is_left_to_the_read(self):
        self.assertIsNone(_gate("Nike buyers in 2019. " + ("lorem ipsum dolor " * 120)))


class HowFarBack(unittest.TestCase):
    def test_paulas_first_question(self):
        out = _gate(P_FIRST)
        self.assertEqual(out['action'], 'stop')
        self.assertTrue(out['payload']['reply'].startswith("The data begins January 1, 2021. " + SENT))

    def test_other_ways_to_ask(self):
        for q in ("how far back does your data go?", "what's the earliest date I can pull?", "does your data go back to 2019?"):
            out = _gate(q)
            self.assertEqual(out['status'], 'capability', q)
            self.assertIn(SENT, out['payload']['reply'])

    def test_a_question_about_the_audience_is_not_a_question_about_the_data(self):
        for q in ("what's the earliest date Landman viewers signed up for Paramount+", "who are the oldest viewers of Yellowstone",
                  "which month did Suits viewers peak", "when does the Landman audience start watching each night"):
            self.assertEqual(F.capability(q, today=TODAY), '', q)
            self.assertIsNone(_gate(q), q)

    def test_a_window_in_range_is_named(self):
        self.assertIn("is in range", F.capability("how far back does the data go? I want Netflix viewers in 2022", today=TODAY))

    def test_her_psych_question_is_answered_with_the_window_that_can_run(self):
        out = _gate(P_PSYCH)
        self.assertEqual(out['action'], 'stop')
        p = out['payload']
        self.assertEqual(p['reply'], SENT + " I can run this from January 1, 2021 to December 14, 2021.")
        self.assertEqual(p['options'][0]['label'], "Run it from January 1, 2021 to December 14, 2021")
        self.assertIn("Psych beginning January 1, 2021 through December 14, 2021", p['options'][0]['send'])
        self.assertIsNone(_gate(p['options'][0]['send']), "the chip's own text is clear of the floor")


class TheGate(unittest.TestCase):
    def test_before_stops_with_the_sentence_and_the_offer(self):
        out = _gate(P_PD)
        self.assertEqual(out['action'], 'stop')
        p = out['payload']
        self.assertEqual(p['reply'], SENT + " I can run the same question from January 1, 2021 forward.")
        self.assertEqual(p['data_floor'], 'before')
        self.assertEqual(p['options'], [{'label': "Run it from January 1, 2021 forward",
                                         'send': "no i need CHICAGO PD show minutes viewed since January 1, 2021 on amazon prime"}])
        self.assertIsNone(_gate(p['options'][0]['send']))

    def test_a_bare_year_reply_gets_the_sentence_and_no_chip(self):
        p = _gate(P_ONE_YEAR)['payload']
        self.assertTrue(p['reply'].startswith(SENT))
        self.assertNotIn('options', p)

    def test_across_rewrites_onto_the_corrected_window(self):
        out = _gate(P_MONTHS)
        self.assertEqual(out['action'], 'rewrite')
        self.assertEqual(out['text'], "Month by month Psych minutes across January 1, 2021 through December 31, 2021 on Prime Video")
        self.assertEqual(out['note'], SENT + " The window starts January 1, 2021 and runs to December 31, 2021.")
        self.assertIsNone(_gate(out['text']))

    def test_open_ends_run_to_the_last_full_day(self):
        for q, want in (("Netflix viewers since 2019", "Netflix viewers since January 1, 2021"),
                        ("Nike buyers over the last ten years", "Nike buyers since January 1, 2021"),
                        ("all time viewers of Stranger Things", "viewers of Stranger Things since January 1, 2021")):
            out = _gate(q)
            self.assertEqual(out['action'], 'rewrite', q)
            self.assertEqual(out['text'], want)
            self.assertEqual(out['note'], SENT + " The window starts January 1, 2021 and runs to November 17, 2026.")

    def test_a_compare_names_the_side_that_can_run(self):
        p = _gate("Costco shoppers 2020 vs 2025")['payload']
        self.assertEqual(p['reply'], SENT + " I can run January 1, 2025 to December 31, 2025 on its own, or any comparison that "
                                            "starts on or after January 1, 2021.")

    def test_never_raises(self):
        for bad in (None, '', 0, [], {'a': 1}, 'x' * 5000):
            self.assertIsNone(F.gate(bad))


# ---------------------------------------------------------------- the service door
_nb_spec = importlib.util.spec_from_file_location('pm_nb_for_data_floor', os.path.join(HERE, 'test_pm_new_chat_no_bleed.py'))
NB = importlib.util.module_from_spec(_nb_spec)
_nb_spec.loader.exec_module(NB)


class TheChatTurn(unittest.TestCase):
    def setUp(self):
        self.w = NB.World()
        self.tid = self.w.new_thread()
        self._real_today = F._today
        F._today = lambda today=None: TODAY if today is None else self._real_today(today)

    def tearDown(self):
        F._today = self._real_today

    def test_paulas_2020_asks_stop_before_any_lane(self):
        for q in (P_SVU_RANGE, P_SVU_YEAR, P_SVU_YEAR2, P_PD, P_ONE_YEAR):
            env, st, _ = self.w.ask(q, thread_id=self.tid)
            self.assertEqual(st, 200)
            self.assertEqual(self.w.calls, [], f"nothing ran for {q!r}")
            self.assertTrue(env['text'].startswith(SENT), env['text'])
            self.assertEqual(env['decision'].get('reason'), 'data_floor')
            self.assertIsNone(env.get('job'))
            self.assertIsNone(env.get('draft'))
            self.assertNotIn('$', env['text'])

    def test_the_chip_reruns_from_the_floor(self):
        env, _, _ = self.w.ask(P_PD, thread_id=self.tid)
        chip = env['options'][0]
        self.assertEqual(chip['label'], "Run it from January 1, 2021 forward")
        env2, _, _ = self.w.ask(chip['send'], thread_id=self.tid)
        self.assertTrue(self.w.calls, "the rerun reaches a core")
        self.assertNotIn(SENT, env2['text'])

    def test_a_crossing_ask_runs_on_the_corrected_window_and_says_so_first(self):
        env, st, _ = self.w.ask(P_MONTHS, thread_id=self.tid, client='app')
        self.assertEqual(st, 200)
        self.assertTrue(self.w.calls, "the ask ran")
        surface, text, body, _seen = self.w.calls[0]
        self.assertIn("January 1, 2021 through December 31, 2021", text)
        self.assertNotIn("2020", text)
        stored = self.w.get(f'{NB.THREADS}/{NB.USER}/{self.tid}.json')
        self.assertEqual([t['text'] for t in stored if t.get('role') == 'user'], [P_MONTHS])   # the stored turn is what she typed
        self.assertTrue(next(t['text'] for t in stored if t.get('role') == 'agent').startswith(SENT))
        self.assertTrue(env['text'].startswith(SENT + " The window starts January 1, 2021 and runs to December 31, 2021."), env['text'])
        self.assertEqual(env['text'].count(SENT), 1)

    def test_the_note_never_leaks_into_the_next_ask(self):
        self.w.ask(P_MONTHS, thread_id=self.tid)
        env, _, _ = self.w.ask("who watches Landman", thread_id=self.tid)
        self.assertNotIn(SENT, env['text'])
        self.assertIsNone(F.pending_note())

    def test_the_default_and_later_windows_are_untouched(self):
        for q in ("who watches Landman", "Landman viewers last 90 days", "Netflix viewers in calendar 2022",
                  "who watches Friends today", "Blade Runner 2049 audience"):
            env, _, _ = self.w.ask(q, thread_id=self.tid)
            self.assertTrue(self.w.calls, q)
            self.assertEqual(self.w.calls[0][1], q)
            self.assertNotIn(SENT, env['text'])
            self.assertFalse(self.w.calls[0][2].get('_pm_data_floor'))

    def test_an_approve_of_an_older_quote_never_runs_a_2020_question(self):
        # the widget re-sends the question with the confirm; the sentence stops it before the charge
        env, _, _ = self.w.ask(P_SVU_RANGE, thread_id=self.tid,
                               extra={'panel_confirm': {'kind': 'viewership', 'subject': 'The Amazon Prime Platform', 'price_label': '$100'}})
        self.assertEqual(self.w.calls, [])
        self.assertTrue(env['text'].startswith(SENT))

    def test_the_key_surface_reads_the_same_sentence(self):
        env, st, _ = self.w.ask(P_PD, client='api')
        self.assertEqual(st, 200)
        self.assertEqual(self.w.calls, [])
        self.assertTrue(env['text'].startswith(SENT))

    def test_the_interpret_surface_gets_it_as_a_plain_turn(self):
        env, _, _ = self.w.ask("build a profile of Nike buyers in 2019", thread_id=self.tid, surface='interpret')
        self.assertEqual(self.w.calls, [])
        self.assertTrue(env['text'].startswith(SENT))
        self.assertIsNone(env.get('draft'))


# ---------------------------------------------------------------- briefs
class TheBrief(unittest.TestCase):
    def _guard(self, draft, text='', allow_ask=True, decision='new_build'):
        routed, echoed = [], []
        out = F.guard_draft(draft, text, decision=decision, allow_ask=allow_ask, today=TODAY,
                            route=lambda d, dec, s, e, wl, dl: routed.append((dec, s, e, wl)),
                            echo=lambda d, clause: echoed.append(clause))
        return out, routed, echoed

    def test_a_window_before_the_floor_asks_on_the_dashboard(self):
        draft = {'subject': 'Law & Order: Special Victims Unit', 'date_range': {'start': '2020-01-01', 'end': '2020-12-31'},
                 'date_range_explicit': True}
        out, routed, _ = self._guard(draft, P_SVU_YEAR)
        self.assertEqual(out, {'question': F.brief_stop_line()})
        self.assertTrue(out['question'].startswith(SENT))
        self.assertEqual(routed, [])
        self.assertNotIn('_v1_guard_refusal', draft)

    def test_a_window_before_the_floor_refuses_on_a_one_shot_surface(self):
        draft = {'subject': 'Nike', 'date_range': {'start': '2019-01-01', 'end': '2019-12-31'}}
        out, _, _ = self._guard(draft, "Nike buyers in 2019", allow_ask=False)
        self.assertIsNone(out)
        self.assertEqual(draft['_v1_guard_refusal'], {'code': 'window_before_data',
                                                      'message': SENT + " Submit a window that starts on or after January 1, 2021."})

    def test_a_crossing_window_starts_on_the_floor_and_the_card_says_so(self):
        draft = {'subject': 'Psych', 'decision': 'new_build', 'date_range': {'start': '2019-12-30', 'end': '2021-12-14'},
                 'date_range_explicit': True, 'engine_date_range': '2019-12-30 TO 2021-12-14'}
        out, routed, echoed = self._guard(draft, "Psych viewers 12/30/2019 through 12/14/2021")
        self.assertIsNone(out)
        self.assertEqual(draft['date_range'], {'start': '2021-01-01', 'end': '2021-12-14'})
        self.assertEqual(draft['engine_date_range'], '2021-01-01 TO 2021-12-14')
        self.assertEqual(draft['date_window_label'], 'Jan 1 to Dec 14, 2021')
        line = SENT + " The window starts January 1, 2021 and runs to December 14, 2021."
        self.assertEqual(draft['data_floor_note'], line)
        self.assertEqual(echoed, [line])
        self.assertEqual(routed[0][:3], ('new_build', '2021-01-01', '2021-12-14'))
        self.assertEqual(F.approve_stop(draft), '')

    def test_the_card_carries_it_on_the_subject_line_with_no_host_helper(self):
        draft = {'subject': 'Psych', 'resolved_identity': 'Psych, the USA Network comedy.', 'date_range': {'start': '2019-12-30', 'end': '2021-12-14'}}
        self.assertIsNone(F.guard_draft(draft, '', decision='new_build', allow_ask=True, today=TODAY))
        self.assertEqual(draft['resolved_identity'], "Psych, the USA Network comedy; " + SENT
                         + " The window starts January 1, 2021 and runs to December 14, 2021.")
        again = dict(draft)
        F.guard_draft(again, '', decision='new_build', allow_ask=True, today=TODAY)
        self.assertEqual(again['resolved_identity'].count(SENT), 1)

    def test_words_no_binder_wrote_onto_the_draft_still_bind(self):
        s, e = EW.default_window(TODAY)
        draft = {'subject': 'Stranger Things', 'date_range': {'start': s, 'end': e}}
        out, _, _ = self._guard(draft, "build a profile of all time viewers of Stranger Things", allow_ask=False)
        self.assertIsNone(out)
        self.assertEqual(draft['date_range'], {'start': '2021-01-01', 'end': YDAY})
        self.assertTrue(draft['data_floor_note'].startswith(SENT))

    def test_a_cut_window_and_an_event_window(self):
        draft = {'subject': 'Joker - Opening Weekend', 'cut_date_range': '2019-10-04 TO 2019-10-06'}
        out, _, _ = self._guard(draft, '', decision='derive_cut')
        self.assertTrue(out['question'].startswith(SENT))
        # an event that took place before the floor: the reader is told, and is not asked for other dates
        draft = {'subject': 'Game of Thrones finale viewers', 'date_range': {'start': '2019-05-19', 'end': '2019-05-20'},
                 'event_window': {'start': '2019-05-19', 'end': '2019-05-20', 'confident': True}}
        out, _, _ = self._guard(draft, 'viewers of the Game of Thrones finale')
        self.assertEqual(out['question'], SENT + " That event took place before that date, so it cannot be read. I can run the same "
                                                 "subject on any window from January 1, 2021 forward.")
        draft = {'subject': 'X', 'event_window': {'start': '2020-11-15', 'end': '2021-02-01', 'confident': True}}
        out, _, _ = self._guard(draft)
        self.assertIsNone(out)
        self.assertEqual(draft['event_window']['start'], '2021-01-01')
        self.assertIn("runs to February 1, 2021", draft['data_floor_note'])

    def test_a_tracker_on_a_title_that_aired_before_the_floor(self):
        draft = {'subject': 'Psych', 'decision': 'subscriber_iq', 'subiq': {'title': 'Psych', 'air_window': {'start': '2006-07-07', 'end': '2014-03-26'}},
                 'subiq_window_data': {'air_window': {'start': '2006-07-07', 'end': '2014-03-26'}}}
        out, _, _ = self._guard(draft, 'pull Subscriber IQ for Psych', decision='subscriber_iq')
        self.assertEqual(out['question'], SENT + " Psych aired before that date. Reply with the window to measure, starting on or "
                                                 "after January 1, 2021, and I will line it up.")
        self.assertEqual(out['chip'], "pull Subscriber IQ for Psych from January 1, 2021 to today")
        self.assertIsNone(_gate(out['chip']))

    def test_a_library_title_is_still_tracked_on_a_window_of_the_readers_own(self):
        """The air dates are never run; a window on or after the floor that the reader gives is the one that counts,
        and the brief no longer offers the air dates as a choice (no loop back to the refusal)."""
        air = {'start': '2006-07-07', 'end': '2014-03-26'}
        mine = {'start': '2021-01-01', 'end': YDAY}
        draft = {'subject': 'Psych', 'decision': 'subscriber_iq', 'date_range': dict(mine), 'date_range_explicit': True,
                 'subiq': {'title': 'Psych', 'air_window': dict(air), 'season_to_date': True, 'through_date': '2014-03-26'},
                 'subiq_window_data': {'kind': 'conflict', 'title': 'Psych', 'air_window': dict(air), 'user_window': dict(mine)}}
        out, _, _ = self._guard(draft, 'pull Subscriber IQ for Psych from January 1, 2021 to today', decision='subscriber_iq')
        self.assertIsNone(out)
        self.assertEqual(draft['subiq']['air_window'], mine)
        self.assertEqual(draft['subiq_window_data']['air_window'], mine)
        self.assertEqual(draft['subiq_window_data']['kind'], 'confirm')
        self.assertIsNone(draft['subiq_window_data']['user_window'])
        self.assertNotIn('through_date', draft['subiq'])
        self.assertEqual(draft['date_range'], mine)
        self.assertEqual(F.approve_stop(draft), '')
        # a window of their own that is also before the floor is refused like any other
        bad = {'subject': 'Psych', 'decision': 'subscriber_iq', 'date_range': {'start': '2019-01-01', 'end': '2019-12-31'},
               'subiq': {'title': 'Psych', 'air_window': dict(air)},
               'subiq_window_data': {'kind': 'conflict', 'air_window': dict(air), 'user_window': {'start': '2019-01-01', 'end': '2019-12-31'}}}
        out, _, _ = self._guard(bad, '', decision='subscriber_iq')
        self.assertTrue(out['question'].startswith(SENT))
        # a season that aired across the floor starts on it, and the card says so
        cross = {'start': '2020-11-15', 'end': '2021-02-14'}
        d3 = {'subject': 'Some Show', 'decision': 'subscriber_iq', 'date_range': dict(cross), 'date_range_explicit': True,
              'subiq': {'title': 'Some Show', 'air_window': dict(cross)}, 'subiq_window_data': {'kind': 'confirm', 'air_window': dict(cross)}}
        out, _, _ = self._guard(d3, 'pull Subscriber IQ for Some Show', decision='subscriber_iq')
        self.assertIsNone(out)
        self.assertEqual(d3['subiq']['air_window']['start'], '2021-01-01')
        self.assertEqual(d3['subiq_window_data']['air_window']['start'], '2021-01-01')
        self.assertEqual(d3['data_floor_note'], SENT + " The window starts January 1, 2021 and runs to February 14, 2021.")

    def test_the_default_window_and_a_later_window_are_left_alone(self):
        s, e = EW.default_window(TODAY)
        for dr in ({'start': s, 'end': e}, {'start': '2021-01-01', 'end': '2021-12-31'}, {'start': '2024-01-19', 'end': '2024-02-15'}):
            draft = {'subject': 'Nike', 'date_range': dict(dr)}
            before = dict(draft)
            out, routed, echoed = self._guard(draft, 'build a profile of Nike buyers')
            self.assertIsNone(out)
            self.assertEqual(draft, before)
            self.assertEqual((routed, echoed), ([], []))

    def test_an_approval_never_runs_a_window_before_the_floor(self):
        self.assertTrue(F.approve_stop({'date_range': {'start': '2020-01-01', 'end': '2020-12-31'}}).startswith(SENT))
        self.assertTrue(F.approve_stop({'engine_date_range': '2019-12-30 TO 2021-12-14'}).startswith(SENT))
        self.assertTrue(F.approve_stop({'subiq': {'air_window': {'start': '2019-06-16', 'end': '2019-08-04'}}}).startswith(SENT))
        self.assertEqual(F.approve_stop({'date_range': {'start': '2025-11-18', 'end': '2026-11-18'}}), '')
        self.assertEqual(F.approve_stop({}), '')
        self.assertEqual(F.approve_stop(None), '')

    def test_it_is_wired_on_every_interpret_path_and_the_approval(self):
        app, chat = _read('app.py'), _read('prometheus/legacy/chat.py')
        guard = _grab(app, '_guard_data_floor')
        self.assertIn('guard_draft', guard)
        self.assertIn('route=_route_window_fields', guard)
        self.assertIn('echo=_append_identity_echo', guard)
        # batch elements and the key surface, each right after the future-window guard
        self.assertRegex(app, r"_guard_future_window\(spec_draft, decision=_dn,\s+allow_ask=False\)\n(?:\s+#[^\n]*\n)+\s+_df_guard = globals\(\)\.get\('_guard_data_floor'\)\n"
                              r"\s+if callable\(_df_guard\):\n\s+_df_guard\(spec_draft, prompt_text, decision=_dn,\s+allow_ask=False\)")
        self.assertRegex(app, r"_guard_future_window\(draft, decision=decision, allow_ask=False\)\n(?:\s+#[^\n]*\n)+\s+_df_guard = globals\(\)\.get\('_guard_data_floor'\)\n"
                              r"\s+if callable\(_df_guard\):\n\s+_df_guard\(draft, prompt, decision=decision, allow_ask=False\)")
        # the dashboard brief: on the module, with the host's own window router, right after the future-window guard
        self.assertRegex(chat, r"'error': _fw_ask\['question'\],\n\s+\}\)\n\s+#[^\n]*\n\s+_df_ask = __import__\('prometheus\.data_floor', fromlist=\['gate'\]\)"
                               r"\.guard_draft\(spec_draft, text, decision=_dec_norm, allow_ask=True, route=_H\._route_window_fields\)")
        core = _grab(chat, '_pm_approve_core')
        self.assertLess(core.index('.approve_stop(draft)'), core.index('spec = _spec_from_draft(draft)'))
        self.assertIn("draft.get('data_floor_note')", _grab(app, '_v1_conclude'))

    def test_the_key_surface_contract_is_unchanged(self):
        """No new request field and no override: the refusal rides the
        existing refusal_code / refusal_message pair."""
        app = _read('app.py')
        conclude = _grab(app, '_v1_conclude')
        self.assertIn("_g_ref = draft.pop('_v1_guard_refusal', None)", conclude)
        self.assertNotRegex(app, r"request\.(?:json|get_json\(\))\.get\(['\"](?:force_|allow_|bypass_|override_)[a-z_]*(?:floor|window|2021)")
        self.assertNotIn("'ignore_data_floor'", app)


# ---------------------------------------------------------------- the key surfaces' own window fields
class KeySurfaceWindows(unittest.TestCase):
    def test_a_bulk_list_is_never_started_on_a_window_before_the_floor(self):
        from prometheus import partner_bulk as PB
        line = SENT + " Submit a window that starts on or after January 1, 2021."
        for dr in ({'start': '2019-01-01', 'end': '2019-12-31'}, {'start': '2020-06-01', 'end': '2021-06-01'}):
            parsed, err = PB.parse_body({'subjects': ['Nike', 'Adidas'], 'date_range': dr})
            self.assertIsNone(parsed)
            self.assertEqual(err, line)
        parsed, err = PB.parse_body({'subjects': ['Nike'], 'window': {'start': '2018-03-01', 'end': '2018-03-31'}})
        self.assertEqual(err, line)
        for dr in ({'start': '2021-01-01', 'end': '2021-12-31'}, {'start': '2025-11-05', 'end': '2026-11-05'}):
            parsed, err = PB.parse_body({'subjects': ['Nike'], 'date_range': dr})
            self.assertIsNone(err)
            self.assertEqual((parsed['window']['start'], parsed['window']['end']), (dr['start'], dr['end']))
        parsed, err = PB.parse_body({'subjects': ['Nike']})
        self.assertIsNone(err)
        self.assertIsNone(parsed['window'])                       # the trailing 12 months default, as before
        self.assertEqual(PB.KNOWN_FIELDS, ('subjects', 'run_avid', 'date_range', 'window'))   # no new request field

    def test_a_pinned_window_on_an_ask_is_declined_with_the_sentence(self):
        src = _read('prometheus/partner_api.py')
        self.assertRegex(src, r"if _dfl\.window_error\(win\.get\('start'\), win\.get\('end'\)\):\n\s+return _out\(C\.declined\('unsupported', _dfl\.api_refusal_line\(\), conversation_id=cid\)\)")
        self.assertLess(src.index("_dfl.window_error(win.get('start'), win.get('end'))"), src.index("ctx['window'] = win"))


# ---------------------------------------------------------------- priced reads
class PricedReads(unittest.TestCase):
    def test_nothing_is_quoted_or_charged_before_the_floor(self):
        from prometheus import priced_read as PR
        charged = []
        state, payload = PR.offer_or_charge(text=P_SVU_RANGE, subject='The Amazon Prime Platform', kind='viewership', panel_confirm=None,
                                            username='paula', consume=lambda *a, **k: charged.append(a) or True)
        self.assertEqual(state, 'offer')
        self.assertTrue(payload['reply'].startswith(SENT))
        self.assertNotIn('panel_offer', payload)
        self.assertNotIn('cost', payload['reply'].lower())
        self.assertEqual(charged, [])

    def test_an_older_quote_is_not_charged_on_its_approve(self):
        from prometheus import priced_read as PR
        charged = []
        state, payload = PR.offer_or_charge(text='Approve', subject='Special Victims Unit', kind='report',
                                            panel_confirm={'kind': 'report', 'subject': 'Special Victims Unit', 'question': P_SVU_YEAR},
                                            username='paula', consume=lambda *a, **k: charged.append(a) or True)
        self.assertEqual(state, 'offer')
        self.assertTrue(payload['reply'].startswith(SENT))
        self.assertEqual(charged, [])

    def test_a_crossing_question_is_offered_on_the_corrected_window(self):
        p = F.stop_payload("Psych minutes on Prime Video from 12/30/2019 to 12/14/2021", today=TODAY)
        self.assertEqual(p['reply'], SENT + " I can run this from January 1, 2021 to December 14, 2021.")
        self.assertIn("January 1, 2021 through December 14, 2021", p['options'][0]['send'])
        self.assertIsNone(F.stop_payload("Psych minutes on Prime Video in 2023", today=TODAY))
        self.assertIsNone(F.stop_payload("how far back does the data go", today=TODAY))


# ---------------------------------------------------------------- the read itself
class TheReadItself(unittest.TestCase):
    def test_the_prompt_carries_the_rule(self):
        note = F.read_note()
        self.assertIn('DATA FLOOR (HARD RULE)', note)
        self.assertIn('2021-01-01', note)
        self.assertIn('before_data_floor', note)
        self.assertIn('floor_applied', note)
        pma = _read('prometheus_analysis.py')
        self.assertIn('_dfl.read_note() + window_txt', _grab(pma, 'build_reasoned_metrics_user_prompt'))
        self.assertIn('_dfl.study_note()', _grab(pma, 'build_search_demand_user_prompt'))
        self.assertIn(SENT, F.study_note())
        self.assertIn('_dfl.analysis_note()', _grab(pma, 'build_analysis_user_prompt'))
        self.assertIn(SENT, F.analysis_note())
        for note in (F.read_note(), F.study_note(), F.analysis_note()):
            self.assertNotIn('\u2014', note)

    def test_a_read_that_states_a_window_before_the_floor_never_lands(self):
        psych = {'action': 'answer', 'subject': 'Psych on Amazon Prime Video', 'window_start': '2019-12-30', 'window_end': '2021-12-14',
                 'headline': 'Psych drew 2,845,057,463 streaming minutes', 'metrics': [{'name': 'm', 'value': 2845057463}]}
        svu = {'action': 'answer', 'window_start': '2020-01-01', 'window_end': '2020-12-31', 'headline': '1,418,723,107 minutes viewed'}
        for data in (psych, svu, {'action': 'decline', 'decline_reason': 'before_data_floor'}):
            out = F.hold_read(dict(data), 'q')
            self.assertEqual(out['reply'], SENT + " I can run the same question from January 1, 2021 forward.")
            self.assertEqual(out['data_floor'], 'before')
            self.assertTrue(out['not_quantifiable'])             # a stated reason, so it lands as this line
            self.assertNotRegex(out['reply'], r"\d{1,3},\d{3}")   # no figure

    def test_a_time_series_row_before_the_floor_never_lands(self):
        data = {'window_start': '2021-01-01', 'window_end': '2021-12-14', 'headline': 'x',
                'breakdown': {'dimension': 'Month', 'rows': [{'label': 'Apr 2020', 'share_pct': 5.8}, {'label': 'Jan 2021', 'share_pct': 4.4}]}}
        self.assertIsNotNone(F.hold_read(data, 'q'))
        year = {'window_start': '2021-01-01', 'window_end': '2021-12-14', 'headline': 'x',
                'breakdown': {'dimension': 'Year', 'rows': [{'label': '2020 (Jan 1 to Dec 31)'}, {'label': '2021 (Jan 1 to Dec 14)'}]}}
        self.assertIsNotNone(F.hold_read(year, 'q'))
        # a breakdown by something that is not time is not a time series
        by_release = {'window_start': '2025-11-18', 'window_end': '2026-11-17', 'headline': 'x',
                      'breakdown': {'dimension': 'Release year of the films they watch', 'rows': [{'label': '2019'}, {'label': '2020'}]}}
        self.assertIsNone(F.hold_read(by_release, 'q'))

    def test_a_read_moved_onto_the_floor_says_so_first(self):
        data = {'window_start': '2021-01-01', 'window_end': YDAY, 'headline': 'Yellowstone drew 31,204,117 viewers.', 'floor_applied': True}
        self.assertIsNone(F.hold_read(data, 'viewers of Yellowstone since it premiered'))
        self.assertEqual(data['headline'], SENT + ' Yellowstone drew 31,204,117 viewers.')
        again = {'window_start': '2021-01-01', 'window_end': YDAY, 'headline': 'X drew 9 viewers.'}
        self.assertIsNone(F.hold_read(again, 'all-time viewers of X'))
        self.assertTrue(again['headline'].startswith(SENT))

    def test_a_normal_read_is_untouched(self):
        data = {'window_start': '2025-11-18', 'window_end': '2026-11-17', 'headline': 'Landman drew 8,437,219 viewers.',
                'breakdown': {'dimension': 'Month', 'rows': [{'label': 'Dec 2025'}, {'label': 'Jan 2026'}]}}
        before = repr(data)
        self.assertIsNone(F.hold_read(data, 'who watches Landman'))
        self.assertEqual(repr(data), before)
        on_floor = {'window_start': '2021-01-01', 'window_end': '2021-12-31', 'headline': 'Calendar 2021 read.'}
        self.assertIsNone(F.hold_read(on_floor, 'Netflix viewers in calendar 2021'))
        self.assertEqual(on_floor['headline'], 'Calendar 2021 read.')

    def test_the_check_is_wired_and_a_charge_is_returned(self):
        chat = _read('prometheus/legacy/chat.py')
        self.assertIn(".hold_read(data, text)", chat)
        self.assertIn("or payload.get('data_floor') == 'before'):", chat)
        self.assertIn("if panel_charge and not held and payload.get('data_floor') == 'before':", chat)
        self.assertLess(chat.count('\n') + 1, 19900, "chat.py stays under 19,900 lines")


# ---------------------------------------------------------------- guided flows
class GuidedFlows(unittest.TestCase):
    def test_a_journey_or_twin_brief(self):
        self.assertIsNone(F.intake({'subject': 'Landman', 'start_date': '2025-11-18', 'end_date': '2026-11-17'}))
        self.assertIsNone(F.intake({'subject': 'Landman'}))
        out = F.intake({'subject': 'Psych', 'start_date': '2019-01-01', 'end_date': '2019-12-31'})
        self.assertTrue(out['refuse'].startswith(SENT))
        parsed = {'subject': 'Psych', 'start_date': '2019-12-30', 'end_date': '2021-12-14'}
        out = F.intake(parsed)
        self.assertEqual(parsed['start_date'], '2021-01-01')
        self.assertEqual(out['note'], SENT + " The window starts January 1, 2021 and runs to December 14, 2021.")
        twin = {'subject': 'X', 'window': {'start': '2020-06-01', 'end': '2021-06-01', 'label': 'custom'}}
        self.assertTrue(F.intake(twin)['note'].startswith(SENT))
        self.assertEqual(twin['window']['start'], '2021-01-01')

    def test_a_valuation_and_its_before_window(self):
        from prometheus import bpiq_wizard as W
        base = {'talent': 'Vin Diesel', 'campaign': 'Vin Diesel x Dodge', 'brand_partner': 'Dodge', 'read_checked': True,
                'control_group': False, 'asset_mode': 'find', 'attr_ok': True}
        # ended before the floor: the dates are asked for again, nothing is priced
        d = dict(base, campaign_start='2019-05-01', campaign_end='2019-06-30', attr_start='2019-05-01', attr_end='2019-07-30', step='charge')
        reply, chips = W.step_prompt(d, today=TODAY)
        self.assertEqual(reply, SENT + " Which campaign dates on or after January 1, 2021 should I use?")
        self.assertNotIn('campaign_start', d)
        self.assertEqual(d['step'], W.STEP_DATES)
        self.assertNotIn('$', reply)
        # crosses the floor: it starts on it and the recap says so before the price
        d = dict(base, campaign_start='2020-11-01', campaign_end='2021-03-31', attr_start='2020-11-01', attr_end='2021-04-30', step='charge')
        reply, _ = W.step_prompt(d, today=TODAY)
        self.assertTrue(reply.startswith(SENT + " The campaign window starts January 1, 2021. The before window starts January 1, 2021. Here's what I'll run:"))
        self.assertEqual((d['campaign_start'], d['attr_start']), ('2021-01-01', '2021-01-01'))
        self.assertEqual(W.engine_inputs(d)['event_start'], '2021-01-01')
        # in range, but its matched before window would reach behind the floor
        d = dict(base, campaign_start='2021-02-01', campaign_end='2021-04-30', attr_start='2021-02-01', attr_end='2021-05-30', step='charge')
        reply, _ = W.step_prompt(d, today=TODAY)
        self.assertTrue(reply.startswith(SENT + " The before window starts January 1, 2021. Here's what I'll run:"))
        # well clear of the floor: the recap as it always was
        d = dict(base, campaign_start='2026-05-08', campaign_end='2026-10-31', attr_start='2026-05-08', attr_end='2026-11-17', step='charge')
        reply, _ = W.step_prompt(d, today=TODAY)
        self.assertTrue(reply.startswith("Here's what I'll run:"))
        self.assertNotIn(SENT, reply)

    def test_the_valuations_own_windows_start_on_the_floor(self):
        from migration import bpiq_synthesis as BS
        w = BS._dates({'event_start': '2021-02-01', 'event_end': '2021-04-30'})
        self.assertEqual(w['pre']['start'], '2021-01-01')
        self.assertEqual(w['baseline']['start'], '2021-01-01')
        self.assertTrue(w['floor_clipped'])
        self.assertFalse(w['matched'])
        w = BS._dates({'event_start': '2026-05-08', 'event_end': '2026-10-31'})
        self.assertFalse(w['floor_clipped'])
        self.assertTrue(w['matched'])
        self.assertEqual(w['pre'], {'start': '2025-11-12', 'end': '2026-05-07', 'days': 177})

    def test_a_tracker_window_and_its_look_back(self):
        out = F.tracker_window('2019-06-16', '2019-08-04')
        self.assertTrue(out['refuse'].startswith(SENT))
        out = F.tracker_window('2020-11-15', '2021-02-14')
        self.assertEqual(out['start'], '2021-01-01')
        self.assertEqual(out['lead'], SENT + " The window starts January 1, 2021. ")
        self.assertEqual(out['lookback'], " That look-back starts January 1, 2021 as well.")
        out = F.tracker_window('2021-03-01', '2021-05-01')
        self.assertEqual((out['start'], out['lead']), ('2021-03-01', ''))
        self.assertEqual(out['lookback'], " " + SENT + " That look-back starts January 1, 2021.")
        self.assertEqual(F.tracker_window('2025-11-16', '2026-01-11'), {'start': '2025-11-16', 'lead': '', 'lookback': ''})
        chat = _read('prometheus/legacy/chat.py')
        self.assertIn(".tracker_window(start, subiq.get('through_date') or end)", chat)
        self.assertIn(".intake(parsed) or {}", chat)

    def test_the_flywheel_card(self):
        from prometheus import flywheel_wizard as FW
        d = {'ecosystem': 'Amazon', 'entry_point': 'watch Gilmore Girls on Prime Video', 'start_date': '2021-01-10', 'end_date': '2021-06-30'}
        self.assertIn(SENT + " The days before an entry start January 1, 2021 at the earliest", FW.confirm_reply(d, today=TODAY))
        d2 = dict(d, start_date='2025-11-18', end_date='2026-11-17')
        self.assertNotIn(SENT, FW.confirm_reply(d2, today=TODAY))

    def test_a_twin_is_never_built_on_a_window_before_the_floor(self):
        self.assertEqual(F.window_error('2019-01-01', '2019-12-31'), F.form_line())
        self.assertEqual(F.window_error('2020-06-01', '2021-06-01'), F.form_line())
        self.assertEqual(F.window_error('2021-01-01', '2021-06-01'), '')
        self.assertEqual(F.window_error('', ''), '')
        api = _read('digital_twins_api.py')
        self.assertEqual(api.count('_dfl.window_error('), 3)


# ---------------------------------------------------------------- forms
class Forms(unittest.TestCase):
    def test_a_submit_with_a_start_before_the_floor(self):
        line = SENT + " Choose a start date on or after January 1, 2021."
        for path, body in (('/api/submit', {'start_date': '2019-07-01', 'end_date': '2020-06-30'}),
                           ('/api/submit', {'sample_start': '2020-07-01', 'sample_end': '2021-06-30'}),
                           ('/api/submit-rerun', {'behavior_start': '2020-12-31'}),
                           ('/api/incidence-check', {'start_date': '2018-01-01', 'end_date': '2018-12-31'}),
                           ('/api/attribution/svod-acquisition', {'campaign_start': '2019-06-16', 'campaign_end': '2019-08-04'}),
                           ('/api/attribution/flywheel-conversion', {'start_date': '2020-01-01', 'end_date': '2021-01-31'}),
                           ('/api/brand-partnership-iq/submit', {'start_date': '2020-11-01', 'end_date': '2021-03-31'}),
                           ('/api/journey-iq/submit', {'start_date': '2019-01-01', 'end_date': '2019-03-01'}),
                           ('/api/share-of-time/analyze', {'start_date': '2020-06-01', 'end_date': '2020-06-30'}),
                           ('/api/talent-fit/assess', {'start_date': '2020-06-01', 'end_date': '2021-06-30'})):
            self.assertEqual(F.form_error(path, 'POST', body), line, path)

    def test_everything_else_passes(self):
        ok = {'start_date': '2025-11-18', 'end_date': '2026-11-18'}
        self.assertEqual(F.form_error('/api/submit', 'POST', ok), '')
        self.assertEqual(F.form_error('/api/submit', 'POST', {'start_date': '2021-01-01', 'end_date': '2021-12-31'}), '')
        self.assertEqual(F.form_error('/api/submit', 'GET', {'start_date': '2019-01-01'}), '')
        self.assertEqual(F.form_error('/api/submit', 'POST', None), '')
        self.assertEqual(F.form_error('/api/submit', 'POST', {'start_date': 'soon'}), '')
        # routes that are not a pull: an episode's air date, a birthday, an admin date are never read
        for path in ('/api/subscriber/lookup_episode_dates', '/api/admin/users', '/api/prometheus/v1/ask', '/api/v1/profiles/run',
                     '/api/trends-iq/data', '/api/synth-chat/approve'):
            self.assertEqual(F.form_error(path, 'POST', {'start_date': '2019-01-01', 'campaign_start': '2015-01-01'}), '', path)

    def test_a_journeys_look_back(self):
        self.assertEqual(F.form_error('/api/journey-iq/submit', 'POST', {'start_date': '2021-01-05', 'end_date': '2021-03-01', 'lookback_days': 14}),
                         SENT + " With a 14-day look-back, choose a start date on or after January 15, 2021.")
        self.assertEqual(F.form_error('/api/journey-iq/submit', 'POST', {'start_date': '2021-01-15', 'end_date': '2021-03-01', 'lookback_days': 14}), '')

    def test_the_guard_is_registered_and_fails_open(self):
        app = _read('app.py')
        fn = _grab(app, 'refuse_form_window_before_data_floor')
        self.assertIn("@app.before_request\ndef refuse_form_window_before_data_floor", app)
        self.assertIn("return jsonify({'success': False, 'error': _why, 'data_floor': _dfl_form.FLOOR}), 400", fn)
        self.assertIn("except Exception:\n        return None", fn)


# ---------------------------------------------------------------- stored rows
class StoredRows(unittest.TestCase):
    def test_a_banked_row_from_before_the_floor_is_never_consulted(self):
        import insights_ledger as IL
        self.assertTrue(IL.before_data_floor({'ws': '2020-01-01', 'we': '2020-12-31'}))
        self.assertTrue(IL.before_data_floor({'ws': '2019-12-30', 'we': '2021-12-14'}))
        self.assertFalse(IL.before_data_floor({'ws': '2021-01-01', 'we': '2021-12-31'}))
        self.assertFalse(IL.before_data_floor({'ws': '', 'we': ''}))
        self.assertFalse(IL.before_data_floor({}))
        self.assertFalse(IL.before_data_floor(None))
        src = _read('insights_ledger.py')
        self.assertEqual(src.count("and not e.get('retired') and not before_data_floor(e)]"), 4)
        # read side only: the row is not stamped, rewritten or removed
        self.assertNotIn("before_data_floor(e):\n                    e['retired']", src)


# ---------------------------------------------------------------- wording
class Wording(unittest.TestCase):
    BANNED = re.compile(r"\b(?:panel|panelist|clickstream|pipeline|synth\w*|modeled|modelled|estimated?|estimates|hostmap|"
                        r"queue|worker|engine|vendor|nielsen|source|sourced|methodology|coverage gap)\b", re.I)

    def _lines(self):
        yield EW.data_floor_sentence()
        yield EW.floor_refusal_line()
        yield EW.floor_window_line('2021-12-14')
        yield EW.floor_window_line(None, 'read')
        yield F.brief_stop_line()
        yield F.api_refusal_line()
        yield F.form_line()
        yield F.capability(P_FIRST, today=TODAY)
        for q in (P_PSYCH, P_PD, P_MONTHS, "Costco shoppers 2020 vs 2025", "all time viewers of Stranger Things", P_ONE_YEAR):
            out = _gate(q)
            yield out['payload']['reply'] if out['action'] == 'stop' else out['note']
        yield F.intake({'start_date': '2019-01-01', 'end_date': '2019-12-31'})['refuse']
        yield F.valuation({'campaign_start': '2019-05-01', 'campaign_end': '2019-06-30'})[1]
        yield F.valuation({'campaign_start': '2020-11-01', 'campaign_end': '2021-03-31'})[1]
        yield F.tracker_window('2019-06-16', '2019-08-04')['refuse']
        yield F.tracker_window('2021-03-01', '2021-05-01')['lookback']
        yield F.hold_read({'window_start': '2020-01-01', 'window_end': '2020-12-31'}, 'q')['reply']
        yield F.form_error('/api/journey-iq/submit', 'POST', {'start_date': '2021-01-05', 'lookback_days': 14})
        air = {'start': '2006-07-07', 'end': '2014-03-26'}
        yield F.guard_draft({'subject': 'Psych', 'subiq': {'title': 'Psych', 'air_window': dict(air)},
                             'subiq_window_data': {'kind': 'confirm', 'title': 'Psych', 'air_window': dict(air)}},
                            'pull Subscriber IQ for Psych', decision='subscriber_iq', today=TODAY)['question']
        yield F.guard_draft({'subject': 'X', 'event_window': {'start': '2019-05-19', 'end': '2019-05-20', 'confident': True}},
                            '', today=TODAY)['question']
        yield F.guard_draft({'subject': 'X', 'date_range': {'start': '2020-01-01', 'end': '2020-12-31'}}, '', today=TODAY)['question']

    def test_every_line_a_person_reads(self):
        for line in self._lines():
            self.assertTrue(line and line.strip(), 'a line is empty')
            self.assertIn(SENT, line)
            self.assertNotIn('\u2014', line)
            self.assertNotIn('\u2013', line)
            self.assertIsNone(self.BANNED.search(line), line)
            self.assertNotRegex(line, r"(?i)\b(?:sorry|apolog|unfortunately|i'?m afraid)\b", line)
            self.assertNotRegex(line, r"(?i)\bbecause\b|\bdue to\b|\bour (?:system|data set|records)\b", line)

    def test_the_sentence_is_jennas(self):
        self.assertEqual(SENT.lower().rstrip('.'), "no data can be pulled from before january 1, 2021")


if __name__ == '__main__':
    unittest.main(verbosity=1)
