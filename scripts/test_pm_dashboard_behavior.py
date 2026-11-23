#!/usr/bin/env python3
"""Prometheus answers what was asked; the open profile is context
(Jenna 2026-11-18).

Jenna, verbatim, on a question copy from a Dick Wolf Productions seat:
"this is wrong its still trying to snap to the open profile instead of
listenint to what the user is asking. no need to do this for hwr but
needs to be fixed for the future".

The message was about a date field on the page ("when i try to put a
start of 1/1/2021 it reverts it to 1/1/2026 but it lets me ass an end
date of 12/31/2023"). A streaming service had been named a turn earlier
and its profile sat in the picker, so a read of that profile ran: the
reply led with the profile's name, listed its audience figures, said
why the field did what it did ("the run window asserting itself, not a
bug in the field") and promised a "back-window build".

What this suite holds:

  1. The detector: a message about how the dashboard itself is behaving
     for the person typing is one; a data question in the same words is
     not; a message that also asks a question of its own keeps that
     question's lane.
  2. The reply: fixed words, no figures, no profile's name, nothing
     about why a control did what it did, what the person can do now,
     and the note raised on the held record that already exists.
  3. The scrub: the made-up words of that reply cannot leave on any
     path; ordinary reads come back byte for byte.
  4. The prompts and the Reader: the same rule where a model answers.
  5. The wiring: the lane's place at the door, the two hooks in the
     chat module, the routing table row, the line ratchet.
  6. Through the door, model canned: her message as it was sent; the
     same class in other words on other pages (profile, tracker,
     journey, valuation, twins, boards, nothing open); questions that
     name another subject than the open profile; questions that name
     none; questions that point at the screen (these still use it).

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

from _pm_legacy_view import legacy_view as _legacy_view, chat_py_lines as _chat_py_lines, legacy_lines as _legacy_lines, LEGACY_CEILING as _LEGACY_CEILING  # noqa: E402  (stage 5: chat.py plus its extracted cores; stage 7: the ratchet counts the legacy code)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import dashboard_behavior as DB  # noqa: E402

HERS = "when i try to put a start of 1/1/2021 it reverts it to 1/1/2026 but it lets me ass an end date of 12/31/2023"
PAGE = 'Amazon Prime Video'
HER_THREAD = [
    {'role': 'user', 'text': 'if i want viewershp minutes on amazon prime at what point does the data begin can i have 1/2021 through 12/2023?'},
    {'role': 'agent', 'text': 'Yes. An answer in the chat reads the library and what we know and answers right here, with the figures. '
                              'Tell me the subject and the window and I will line it up; it comes back as a brief to approve before '
                              'anything is charged.'}]
# what the Reader said about her message that night (the ask log): a subject her words never named
HER_READING = {'intent': 'capability', 'confidence': 0.72, 'subjects': [{'as_written': 'amazon prime', 'catalog': '', 'kind': 'platform'}],
               'flags': ['custom_window', 'typo'], 'restate': 'You want to know why the start date reverts to 1/1/2026.',
               'wants': 'They want to know why the start date field reverts.', 'answer_shape': 'explanation',
               'window': '1/1/2021 to 12/31/2023', 'platform': ''}
# the reply that went out, in its own words
SENT = (
    "On Amazon Prime Video:\n\n"
    "The start field snaps to 1/1/2026 because the Prime Video set open on your screen is keyed to its own run window, and inside "
    "that window the audience is 128,116,302 US viewers; a 1/2021 to 12/2023 minutes read runs as a back-window build, quoted first. "
    "MEASURED READ (Jan 1 2025 to Dec 31 2025 (run window on the open set))\n"
    "- Projected US Prime Video audience, run window: 128,116,302\n"
    "- Prime Video TVOD renters: 24,342,463\n"
    "- Prime Video EST buyers: 15,378,519\n"
    "- Unique people per day, latest tracked day: 7,641\n"
    "- The behavior you are seeing is the run window asserting itself, not a bug in the field: the end date accepts 12/31/2023 because "
    "it is a free field, while the start resets to the live window the open set is keyed to.\n"
    "- Minutes for 1/2021 through 12/2023 come back as a back-window build on the same bases and the same partitions as the set you "
    "have open, so the 2021 to 2023 figures nest cleanly under the 128,116,302 audience and the TVOD and EST rows rather than running "
    "as a parallel line. It ships quoted first, confirmed, then produced.\n"
    "- For a minutes read specifically, the useful shape leans toward total viewing minutes, minutes per viewer, and share of minutes "
    "by title tier, cut monthly across the 36 months.\n\n"
    "How these are counted:\n"
    "- 128,116,302: Distinct US people with at least one Prime Video stream in the Jan 1 2025 to Dec 31 2025 run window.\n"
    "- Window: Jan 1 2025 to Dec 31 2025 (run window on the open set).\n"
    "- Base: the Amazon Prime Video audience, measured across 10M US consumers and projected to the US.\n")
SENT_CHIPS = ['Quote the 1/2021 to 12/2023 Prime Video viewing minutes build',
              'Show monthly US viewing minutes for Prime Video across the live run window',
              'Minutes per viewer by title tier on Prime Video', 'Email me this file', 'Line up the back-window build']
# words that reached her and may reach nobody
MADE_UP = ('run window', 'open set', 'set open on your screen', 'set you have open', 'keyed to', 'back-window', 'back window',
           'partitions', 'the same bases', 'not a bug', 'free field', 'asserting itself', 'quoted first, confirmed')
INTERNAL = ('synth', 'modeled', 'modelled', 'estimat', 'panel', 'pipeline', 'hostmap', 'measured read')

ABOUT_THE_DASHBOARD = [
    HERS,
    "the date picker won't let me choose anything before 2026",
    "why does the start date keep resetting",
    "the export button doesn't do anything when i click it",
    "my download keeps failing",
    "the page is blank after i pick a profile",
    "the dropdown is greyed out and i can't select Rankers",
    "it wont let me type in the search box",
    "the filter keeps snapping back to National",
    "the chart is cut off on the right side",
    "why is the compare tab not loading",
    "the Apply button is not working",
    "I get an error message when I open the journey",
    "the twin page keeps spinning and never loads",
    "every time I change the end date it jumps back",
    "the valuation screen froze",
    "I clicked download and nothing happened",
    "Why can't I scroll the table",
    "is there a bug with the date range? it keeps changing what I enter",
    "The calendar is stuck on this year",
    "it only lets me pick dates in 2026 but not earlier",
    "the tracker page looks wrong",
    "when I click the Focus Group IQ tab nothing happens",
    "the end date field doesnt accept 2023",
    "the start date keeps going back to january",
    "I can't change the window on this screen, it just resets",
    "The dashboard is frozen",
    "the csv export is not downloading",
    "I'm on Rankers. The start date keeps resetting.",
]
SELF_SERVE = ["i want to run the report myself", "how do I pull this myself", "can I do this on my own?",
              "we want to get these numbers ourselves", "let me run it myself"]
NOT_ABOUT_THE_DASHBOARD = [
    "what share of these viewers use Hulu",
    "how many people watched Psych in 2021",
    "why did engagement drop in March",
    "which button gets the most clicks on the Nike product page",
    "download the csv",
    "email me this file",
    "filter this by women 25 to 34",
    "where is the export button",
    "can I change the date range on a profile?",
    "why is Netflix not showing in the streaming list",
    "please remove the clickstream tab",
    "add a user to my account",
    "why is my build stuck",
    "that's not what I asked",
    "you keep giving me the wrong show",
    "Month by month Psych minutes across 2020 and 2021 on Prime Video",
    "what is the start date of the data for Amazon Prime",
    "how many users churned after the app kept crashing",
    "Who is rising fastest on this board?",
    "what does this page tell me about the audience",
    "how big is this audience",
    "compare this profile to Netflix",
    "run a profile on Mountain Dew",
    "is the Gunna profile done?",
    "my deck failed",
    "give me the top 10 brands for this audience",
    "what dates does this profile cover",
    "set the window to 2024 and rerun",
    "the read looks wrong, Netflix should be higher",
    "which page converts best in this journey",
    "how many people clicked the download link in the email",
    "what happens to viewers when a show resets its season",
    "do viewers go back to Netflix after cancelling",
    "this number seems too high",
    "analyze the data on this screen",
    "Why is Reba ranked this high?",
    "people who tried to sign in and failed, how many are there",
    "can you give me viewership minutes for the show psych on amazon prime...by year from information available",
    "no i need CHICAGO PD show minutes viewed in 2020 on amazon prime",
    # a real ask (2026-10): a figure question with a remark about an export in it; the figures are what was asked
    "For the INfluencer Project, what is the Info-Seek % and ACtion Page % for @roomforscream as it did not export in the excel",
    "are you not working?",
    "was the profile run on all 49 of these inputs or did you just add them to the BRAND field?",
]
# a message that also asks a question of its own is that question's
ALSO_ASKS = [
    "when i try to put 2021 in the start date it reverts. how many people watched Psych on Hulu in 2024?",
    "the date field won't take 2021. what do Taylor Swift fans buy?",
    "the date field won't take 2021. Please pull Psych minutes for 2021 on Prime Video",
    "the date field won't take 2021, so how many people watched Psych on Hulu in 2024?",
    "i want to run the report myself. how many people watched Psych?",
]


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


def _clean_copy(test, text, page=PAGE):
    low = str(text).lower()
    for w in MADE_UP + INTERNAL:
        test.assertNotIn(w, low, f"{w!r} in {text[:140]!r}")
    test.assertNotIn('\u2014', text)
    test.assertNotIn('\u2013', text)
    test.assertFalse(re.search(r"(?m)^\s*On [A-Z][^\n]{1,80}:\s*$", text), text[:120])
    if page:
        test.assertNotIn(page.lower(), low)
        test.assertNotIn('prime video', low)


class TheDetector(unittest.TestCase):
    def test_a_message_about_the_dashboard_is_one(self):
        for q in ABOUT_THE_DASHBOARD:
            self.assertTrue(DB.is_dashboard_question(q), q)
            self.assertIsNotNone(DB.detect(q), q)

    def test_her_message_is_a_date_field(self):
        hit = DB.detect(HERS)
        self.assertEqual((hit['family'], hit['thing'], hit['why']), ('date', 'date field', 'first_person'))

    def test_doing_it_yourself_is_about_the_dashboard(self):
        for q in SELF_SERVE:
            self.assertTrue(DB.self_serve(q), q)
            self.assertTrue(DB.is_dashboard_question(q), q)
        for q in ("I'd rather build a profile myself", "what do people who cook for themselves buy", "who watches by themselves"):
            self.assertFalse(DB.self_serve(q) and not DB.steps_aside(q), q)

    def test_a_data_question_in_the_same_words_is_not(self):
        for q in NOT_ABOUT_THE_DASHBOARD:
            self.assertFalse(DB.is_dashboard_question(q), q)

    def test_a_message_that_also_asks_keeps_its_own_lane(self):
        for q in ALSO_ASKS:
            self.assertFalse(DB.is_dashboard_question(q), q)

    def test_the_asks_other_lanes_own_stay_with_them(self):
        for q in ("where is the export button", "please remove the clickstream tab", "add a user to my account",
                  "run a profile on Mountain Dew", "email me this file"):
            self.assertTrue(DB.steps_aside(q) or DB.detect(q) is None, q)

    def test_the_switch(self):
        os.environ['PM_DASHBOARD_BEHAVIOR'] = '0'
        try:
            self.assertIsNone(DB.detect(HERS))
            self.assertIsNone(DB.answer(HERS))
        finally:
            os.environ.pop('PM_DASHBOARD_BEHAVIOR', None)
        self.assertIsNotNone(DB.detect(HERS))


class TheReply(unittest.TestCase):
    def test_her_message(self):
        held = []
        raw = DB.answer(HERS, uname='reader', ctx={'primary': {'name': PAGE, 's3_key': 'k.csv'},
                                                   'view_context': {'view_id': 'cultureRankerIQ', 'view_title': 'Rankers'}},
                        history=HER_THREAD, thread_id='t1',
                        hold=lambda uname, q, **kw: held.append((uname, q, kw)) or 'system/ops/held_replies/2026-11-19/x.json')
        reply = raw['reply']
        self.assertEqual(reply, (
            "That is a question about the dashboard itself, not about the data. I cannot see what that date field is doing from "
            "here, so I will not guess at why it did that.\n\n"
            "What you can do right now: tell me what you want to see and the dates in words, and I will take it from there.\n\n"
            "I have passed your note to the Crosswalk team so they can look at it."))
        _clean_copy(self, reply)
        self.assertFalse(re.search(r"\d", reply))                       # no figure of any kind, hers or the profile's
        self.assertEqual(raw['followups'], [])
        self.assertFalse(raw.get('panel_offer'))
        self.assertFalse(raw.get('read_job_id'))
        self.assertEqual(raw['dashboard_behavior'], {'family': 'date', 'thing': 'date field', 'why': 'first_person', 'raised': True})
        # raised once, on the record that already exists, with the page for the team and never in the reply
        self.assertEqual(len(held), 1)
        uname, q, kw = held[0]
        self.assertEqual((uname, q, kw['kind'], kw['thread_id'], kw['told']), ('reader', HERS, 'dashboard_behavior', 't1', reply))
        self.assertIn('on Rankers', kw['reason'])
        self.assertIn('nothing was run and nothing was charged', kw['reason'])
        self.assertEqual(raw['held_ref'], 'system/ops/held_replies/2026-11-19/x.json')

    def test_a_test_process_writes_no_record(self):
        raw = DB.answer(HERS, uname='reader')                           # the house rule (promise_truth): a probe or a test writes none
        self.assertIsNotNone(raw)
        self.assertNotIn('held_ref', raw)

    def test_every_family_says_what_to_do_now_and_never_why(self):
        seen = set()
        for q in ABOUT_THE_DASHBOARD:
            raw = DB.answer(q, hold=lambda *a, **k: '')
            self.assertIsNotNone(raw, q)
            reply = raw['reply']
            seen.add(raw['dashboard_behavior']['family'])
            self.assertIn('I cannot see what that ', reply)
            self.assertIn('What you can do right now: ', reply)
            self.assertTrue(reply.endswith('I have passed your note to the Crosswalk team so they can look at it.'))
            self.assertFalse(re.search(r"\bbecause\b", reply, re.I), reply)      # never a reason for what the control did
            self.assertFalse(re.search(r"\d", reply), reply)
            self.assertFalse(DB.invented(reply))
            _clean_copy(self, reply)
            for s in re.split(r"(?<=[.!?])\s+", reply):
                self.assertLessEqual(len(s.split()), 30, s)
        self.assertEqual(seen, {'date', 'download', 'page', 'control'})

    def test_doing_it_yourself_is_answered_from_the_map(self):
        from prometheus import dashboard_map as M
        raw = DB.answer("i want to run the report myself",
                        user={'username': 'reader', 'has_profile_iq_access': True, 'has_rankers_iq_access': True})
        reply = raw['reply']
        self.assertTrue(reply.startswith('You can do a lot of it yourself. Every product is in ' + M.DROPDOWN_WHERE))
        self.assertIn('Profile IQ (Select Profile)', reply)
        self.assertIn('Rankers', reply)
        self.assertNotIn('Subscriber IQ', reply)                         # only what this seat can open
        self.assertNotIn(M.RAIL['line'], reply)
        self.assertIn('I cannot see the controls on a page, so I will not tell you which field or filter to set.', reply)
        self.assertFalse(raw['dashboard_behavior']['raised'])
        self.assertNotIn('held_ref', raw)
        self.assertFalse(DB.invented(reply))
        for w in ('historic mode', 'lookback', 'as-of', 'back-window', 'run window'):
            self.assertNotIn(w, reply.lower())

    def test_the_reader_s_verdict_still_has_to_fit_the_words(self):
        self.assertIsNotNone(DB.answer("the timeline thing at the top is acting up again", force=True, hold=lambda *a, **k: ''))
        for q in ("how many people watched Psych in 2021", "run a profile on Mountain Dew", "why is my build stuck"):
            self.assertIsNone(DB.answer(q, force=True, hold=lambda *a, **k: ''), q)

    def test_a_decline_from_the_reasoned_read(self):
        hints = []
        raw = DB.from_decline({'action': 'decline', 'decline_reason': 'dashboard'}, "the timeline thing is acting up", 'reader',
                              lambda **kw: hints.append(kw))
        self.assertTrue(raw['reply'].startswith('That is a question about the dashboard itself'))
        self.assertEqual(hints, [{'route': 'dashboard_behavior', 'outcome': 'raised'}])
        self.assertIsNone(DB.from_decline({'action': 'decline', 'decline_reason': 'not_digital'}, 'x', 'reader'))

    def test_the_core_hook_stands_aside_for_a_step(self):
        self.assertIsNotNone(DB.core_hook(HERS, {'page_context': {}}, [], {'username': 'reader'}))
        for k in ('panel_confirm', 'bind_subject', 'mode', 'jiq_confirm', '_pm_order'):
            self.assertIsNone(DB.core_hook(HERS, {k: {'x': 1}}, [], {'username': 'reader'}), k)
        self.assertIsNone(DB.core_hook("what share of this audience is female", {}, [], {'username': 'reader'}))


class TheScrub(unittest.TestCase):
    def test_the_reply_that_went_out_cannot_go_out_again(self):
        import prometheus_analysis as pma
        for out in (DB.scrub(SENT), pma.scrub_user_text(SENT)):
            low = out.lower()
            for w in MADE_UP:
                self.assertNotIn(w, low, w)
            self.assertNotIn('The start field snaps', out)
            self.assertNotIn('The behavior you are seeing', out)
            self.assertFalse(DB.invented(out))
            # the read's own lines and the standing counting block are untouched
            self.assertIn('- Prime Video TVOD renters: 24,342,463', out)
            self.assertIn('- Window: Jan 1 2025 to Dec 31 2025.', out)
            self.assertIn('- Base: the Amazon Prime Video audience', out)
        self.assertEqual(DB.scrub(DB.scrub(SENT)), DB.scrub(SENT))

    def test_the_chips(self):
        self.assertEqual(DB.scrub_chips(SENT_CHIPS), [
            'Quote the 1/2021 to 12/2023 Prime Video viewing minutes build',
            'Show monthly US viewing minutes for Prime Video across the window',
            'Minutes per viewer by title tier on Prime Video', 'Email me this file'])

    def test_the_exit_every_reply_passes(self):
        from prometheus import guards as G
        raw = {'success': True, 'reply': SENT, 'followups': list(SENT_CHIPS)}
        G.scrub_method_language_payload(raw)
        self.assertFalse(DB.invented(raw['reply']))
        self.assertFalse(any(DB.invented(c) for c in raw['followups']))
        self.assertIn('reply:dashboard', raw['_scrubbed'])
        self.assertIn('followups:dashboard', raw['_scrubbed'])

    def test_an_ordinary_read_comes_back_byte_for_byte(self):
        for legit in (
                "In a crowded field, Netflix holds 31.4% of viewing.",
                "The audience resets each season and Hulu accepts fewer new viewers.",
                "Window: Jan 1 2025 to Dec 31 2025.",
                "Base: the Amazon Prime Video audience, measured across 10M US consumers and projected to the US.",
                "An open set of questions remains for the fall slate.",
                "Psych drew 2,845,057,463 streaming minutes from 4,186,942 US viewers on Prime Video.",
                "On Law & Order: Special Victims Unit on Amazon Prime Video:\n\nWindow: Jan 1 2020 to Dec 31 2020.\n- Total minutes viewed: 1,418,723,107",
                "Pick the audience in the Select Profile dropdown; the Data Cuts list under it holds the cuts of that audience."):
            self.assertFalse(DB.invented(legit), legit)
            self.assertEqual(DB.scrub(legit), legit)

    def test_the_lane_s_own_words_pass_the_house_scrub(self):
        import prometheus_analysis as pma
        from prometheus import guards as G
        for q in (HERS, "my download keeps failing", "the page is blank after i pick a profile", "i want to run the report myself"):
            reply = DB.answer(q, hold=lambda *a, **k: '')['reply']
            self.assertEqual(' '.join(pma.scrub_user_text(reply).split()), ' '.join(reply.split()), q)
            self.assertFalse(G.method_language(reply), q)


class ThePromptsAndTheReader(unittest.TestCase):
    def test_the_two_model_passes_carry_the_rule(self):
        import prometheus_analysis as pma
        self.assertIn(DB.PROMPT_RULE, pma.ANALYSIS_SYSTEM_PROMPT)
        self.assertIn(DB.PROMPT_RULE_SHORT, pma._REASONED_METRICS_SYSTEM_PROMPT_T)
        self.assertIn('"decline_reason": "not_digital" | "dashboard" | null', pma._REASONED_METRICS_SYSTEM_PROMPT_T)
        for rule in (DB.PROMPT_RULE, DB.PROMPT_RULE_SHORT):
            self.assertIn('Never name a product, build or deliverable this prompt does not name', rule.replace('never name a', 'Never name a'))
            self.assertNotIn('\u2014', rule)
        # the rule sits right under the line it is the exception to
        a = pma.ANALYSIS_SYSTEM_PROMPT
        self.assertLess(a.index('- NEVER DECLINE:'), a.index("- THE DASHBOARD'S OWN CONTROLS:"))
        self.assertLess(a.index("- THE DASHBOARD'S OWN CONTROLS:") - a.index('- NEVER DECLINE:'), 400)

    def test_the_help_writer_never_explains_a_control(self):
        from prometheus import help_lane as H
        self.assertIn('never explain why a control did something', H._SYSTEM)

    def test_the_reader_knows_the_intent(self):
        from prometheus import reader as R
        self.assertIn('dashboard_issue', R.INTENTS)
        self.assertIn('dashboard_issue', R.EXECUTABLE)
        self.assertEqual(R.ROUTE_FAMILY['dashboard_issue'], ('dashboard_behavior',))
        self.assertIn('dashboard_issue, subjects empty; never analyze, analyze_page, size or capability', R.CHARTER)
        seeds = [e for e in R.SEED_EXAMPLES if e['intent'] == 'dashboard_issue']
        self.assertTrue(any(e['text'] == HERS and e['subjects'] == [] for e in seeds))
        self.assertTrue(all(e['intent'] in R.INTENTS for e in R.SEED_EXAMPLES))
        near = R.nearest_examples(HERS, bank={'examples': R.SEED_EXAMPLES}, k=3)
        self.assertEqual(near[0]['intent'], 'dashboard_issue')           # her message is the nearest example for its like

    def test_the_budgets_hold(self):
        from prometheus import prompts as P
        for name in ('analysis.system', 'metrics.system', 'reader.charter'):
            r = P.check(name)
            self.assertTrue(r['ok'], r)


class TheWiring(unittest.TestCase):
    def test_the_lane_s_place_at_the_door(self):
        src = _read('prometheus', 'service.py')
        at = src.index('# lane: dashboard_behavior')
        self.assertLess(src.index('# lane: wayfinding'), at)             # a where-is question keeps its directions
        for later in ('# lane: study_record', '# lane: offer_acceptance', '# lane: multi_ask', '# lane: sizing_quote',
                      '# lane: reader', '# lane: surface_decision', '# lane: work_gate'):
            self.assertLess(at, src.index(later), later)                 # ahead of every lane that can quote, read or draft
        lane = src[at:src.index('# The catalog lane\'s own "Open <profile>" chip')]
        self.assertIn('if not _armed(body) and not _is_order and not body.get(\'_meta_replay\'):', lane)
        self.assertIn('_dbq.is_dashboard_question(text)', lane)
        self.assertIn("'reason': 'dashboard_behavior'", lane)
        self.assertNotIn('consume_credit', lane)
        self.assertNotIn('claude', lane.lower())
        from prometheus import service as S
        self.assertEqual(S.lane_markers(), list(S.LANE_ORDER))            # the markers and the declared order agree
        self.assertEqual(S.LANE_ORDER[S.LANE_ORDER.index('wayfinding') + 1], 'dashboard_behavior')
        self.assertIn('dashboard_behavior', S.DETERMINISTIC_LANES)
        self.assertIn('dashboard_behavior', S.NEVER_PRICED_LANES)
        ex = src[src.index('def _execute_reading('):src.index('def _restate_reads_clean(')]
        self.assertIn("elif intent == 'dashboard_issue':", ex)
        self.assertIn('force=True', ex)

    def test_the_two_hooks_in_the_chat_module(self):
        chat = _legacy_view()
        hook = "__import__('prometheus.dashboard_behavior', fromlist=['core_hook']).core_hook(text, body, history, user, _pm_ask_hint)"
        self.assertEqual(chat.count(hook), 1)
        self.assertLess(chat.index(hook), chat.index('# ------------------ SINGLE ROUTER (2026-08-28) ------------------'))
        self.assertLess(chat.index("return jsonify(_pm_intake_turn('digital_journey', text, body, history, user, _pm_ppu, first=True))"),
                        chat.index(hook))                               # an open intake has its say first
        dec = "fromlist=['from_decline']).from_decline(data, text, '', _pm_ask_hint)"
        self.assertEqual(chat.count(dec), 1)
        self.assertLess(chat.index(dec), chat.index("gate = {'domain': 'model_decline',"))
        self.assertLess(_legacy_lines(), _LEGACY_CEILING)                   # the line ratchet holds

    def test_the_routing_table_and_the_host_record(self):
        from prometheus import routing_table as RT
        row = RT.row_for('dashboard_behavior')
        self.assertIsNotNone(row)
        self.assertTrue(row.get('host_only'))
        from prometheus import host as PH
        self.assertIn('record_held', PH._APP_BINDINGS)                   # the record the note is raised on already exists

    def test_no_new_mail_and_no_request_field(self):
        src = _read('prometheus', 'dashboard_behavior.py')
        for banned in ('send_email', 'send_raw_email', 'boto3', "client('ses'", '@crosswalknyc.com', 'request.json', 'request.get_json'):
            self.assertNotIn(banned, src, banned)
        self.assertNotIn('\u2014', src)
        self.assertNotIn('\u2013', src)


# ============================================================================ the door
_CHILD = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from prometheus import replay
replay.install_isolation()
h = replay.harness()
h.host.set('_neutralize_data_delimiters', lambda s, *a, **k: str(s or ''))
from prometheus.host import host as PH
PAGE = 'Amazon Prime Video'
KEY = 'Amazon_Prime_Video_09_01_2026_10_00.csv'
h.s3.put_object(Bucket='dashboard-inputs', Key=KEY, Body=replay._profile_csv(PAGE))
replay._STATE['baseline'] = dict(h.s3.objects)
out = []
for it in json.load(sys.stdin):
    replay._restore_baseline(h)
    replay._STATE['question'] = it['question']
    replay._canned_dual(h, PAGE, None)
    canned = replay._canned_for('analyze', PAGE, None)
    calls = []
    def _json(system, user, *a, _c=canned, **k):
        calls.append({'fn': 'json', 'surface': k.get('surface'), 'page_rows': 'Dr Pepper' in (str(system) + str(user)),
                      'rule': "THE DASHBOARD'S OWN CONTROLS" in str(system)})
        return {'success': True, 'data': json.loads(json.dumps(_c)), 'model': 'replay'}
    h.patch('_pm_claude_json', _json)
    def _data(system, user, _r=it.get('reading'), _c=canned, **k):
        calls.append({'fn': 'data', 'surface': k.get('surface'), 'page_rows': 'Dr Pepper' in (str(system) + str(user))})
        if k.get('surface') == 'reader':
            return dict(_r) if _r else {}
        return dict(_c)
    PH.bind(claude_data=_data, can_open_profile=lambda u, k: True)
    view = it.get('view')
    pc = {}
    if view:
        pc['view_context'] = {'view_id': view, 'view_title': it.get('title') or (PAGE if view == 'profileIQ' else view), 'data': it.get('data') or {}}
        if it.get('primary', True):
            pc['primary'] = {'s3_key': KEY, 'name': PAGE}
    legacy = bool(it.get('legacy'))
    path = '/api/brief-chat/analyze' if legacy else (replay._STATE.get('door') or '/api/brief-chat/analyze')
    body = {'text': it['question'], 'history': it.get('history') or [], 'page_context': pc}
    if not legacy:
        body.update({'client': 'dashboard', 'persist': False, 'surface': it.get('surface') or 'analyze'})
    n0 = len(replay._STATE['captured'])
    status, payload = h.post(path, body, session={'username': replay.REPLAY_USER['username']})
    rec = (replay._STATE['captured'][n0:] or [{}])[-1]
    raw = payload.get('raw') if isinstance(payload, dict) and isinstance(payload.get('raw'), dict) else payload
    raw = raw if isinstance(raw, dict) else {}
    out.append({'route': rec.get('route'), 'outcome': rec.get('outcome'), 'subject': rec.get('subject'),
                'reply': replay._reply_text(raw) or str((payload or {}).get('text') or ''),
                'offer': bool(raw.get('panel_offer')), 'draft': isinstance(raw.get('spec_draft'), dict),
                'jobs': [j for j in [raw.get('read_job_id'), raw.get('run_id')] if j],
                'chips': [str(c) for c in (raw.get('followups') or [])], 'calls': calls,
                'reason': ((payload or {}).get('decision') or {}).get('reason') if isinstance(payload, dict) else None})
print('@@@' + json.dumps(out))
'''

RANKERS = {'view': 'cultureRankerIQ', 'title': 'Rankers',
           'data': {'filters': {'geo_type': 'National', 'historic': True, 'asof': '2023-12-31', 'lookback_days': 731}, 'board': 'streaming'}}
PROFILE = {'view': 'profileIQ'}
TRACKER = {'view': 'subscriberIQ', 'title': 'Landman', 'primary': False, 'data': {'show': 'Landman', 'platform': 'Paramount+'}}
JOURNEY = {'view': 'journeyIQ', 'title': 'Babylon 5 on Prime Video', 'primary': False, 'data': {'subject': 'Babylon 5'}}
VALUATION = {'view': 'brandPartnershipIQ', 'title': 'Stanley x Target', 'primary': False, 'data': {'talent': 'Stanley', 'brand': 'Target'}}
TWINS = {'view': 'digitalTwinsIQ', 'title': 'Gilmore Girls twin', 'primary': False, 'data': {'twin': 'Gilmore Girls'}}
TRENDS = {'view': 'trendsIQ', 'title': 'Trends'}

DOOR_ITEMS = (
    # 0-2: her message as it was sent, and with less around it
    [dict(RANKERS, question=HERS, history=HER_THREAD, reading=HER_READING),
     dict(PROFILE, question=HERS, history=HER_THREAD),
     dict(question=HERS)]
    # 3-18: the same class in other words on other pages
    + [dict(PROFILE, question="the date picker won't let me choose anything before 2026"),
       dict(PROFILE, question="why does the start date keep resetting", history=HER_THREAD),
       dict(TRACKER, question="the export button doesn't do anything when i click it"),
       dict(TRACKER, question="my download keeps failing"),
       dict(JOURNEY, question="the page is blank after i pick a journey"),
       dict(JOURNEY, question="I clicked download and nothing happened"),
       dict(VALUATION, question="the filter keeps snapping back to National"),
       dict(VALUATION, question="the valuation screen froze"),
       dict(TWINS, question="it wont let me type in the search box"),
       dict(TWINS, question="when I click the Focus Group IQ tab nothing happens"),
       dict(RANKERS, question="every time I change the end date it jumps back", history=HER_THREAD),
       dict(RANKERS, question="The calendar is stuck on this year"),
       dict(question="the dropdown is greyed out and i can't select Rankers"),
       dict(PROFILE, question="the Apply button is not working", surface='interpret'),
       dict(PROFILE, question="is there a bug with the date range? it keeps changing what I enter"),
       dict(TRENDS, question="the chart is cut off on the right side")]
    # 19: a wording the detector does not know, read by the Reader as about the dashboard
    + [dict(PROFILE, question="the timeline thing at the top is acting up again", history=HER_THREAD,
            reading={'intent': 'dashboard_issue', 'confidence': 0.8, 'subjects': [{'as_written': 'amazon prime', 'catalog': '', 'kind': 'platform'}],
                     'flags': [], 'restate': 'You are reporting that the timeline control is not behaving.'})]
    # 20: doing it yourself, the turn after hers
    + [dict(RANKERS, question="i want to run the report myself", history=HER_THREAD)]
    # 21: the older route, straight to the core
    + [dict(PROFILE, question=HERS, legacy=True)]
    # 22-26: the words name another subject than the open profile
    + [dict(PROFILE, question="how many people watched Psych on Hulu in 2024"),
       dict(PROFILE, question="what do Taylor Swift fans buy"),
       dict(PROFILE, question="who watches Yellowstone"),
       dict(RANKERS, question="how many people watched Psych on Hulu in 2024"),
       dict(PROFILE, question="when i try to put 2021 in the start date it reverts. how many people watched Psych on Hulu in 2024?")]
    # 27-29: the words name no subject and do not point at the screen
    + [dict(PROFILE, question="what are the biggest streaming trends right now"),
       dict(PROFILE, question="what should I know before a pitch meeting"),
       dict(PROFILE, question="what is the median age")]
    # 30-34: the words point at the screen, or name the open profile
    + [dict(PROFILE, question="what share of this profile is female"),
       dict(PROFILE, question="what do the viewers in this profile buy"),
       dict(PROFILE, question="top brands here"),
       dict(PROFILE, question="summarize this profile"),
       dict(PROFILE, question="what share of Amazon Prime Video viewers also use Hulu")]
    # 35-36: an audience noun with no thread is nobody in particular; the screen is never taken (Jenna 2026-11-20)
    + [dict(PROFILE, question="what share of this audience is female"),
       dict(PROFILE, question="what do these viewers buy")]
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
    """The words through the door as the dashboard sends them. The model
    is canned to answer anything that reaches it with a read of the open
    profile, so that read in a reply is the defect showing."""

    @classmethod
    def setUpClass(cls):
        cls.res = _door(list(DOOR_ITEMS))

    def setUp(self):
        if not self.res:
            if os.environ.get('PM_DOOR_REQUIRED') == '1':
                self.fail('the door checks did not run: the recording host is required here')
            self.skipTest('the recording host (scripts/_pm_behavior.py in the parent repo) is not on the path here')
        self.assertEqual(len(self.res), len(DOOR_ITEMS))

    def _is_the_lane(self, r, outcome='raised'):
        self.assertEqual((r['route'], r['outcome']), ('dashboard_behavior', outcome), r['reply'][:160])
        self.assertIsNone(r['subject'])
        self.assertEqual(r['jobs'], [])
        self.assertFalse(r['offer'])
        self.assertFalse(r['draft'])
        self.assertEqual(r['calls'], [], 'no model is asked')
        self.assertNotIn('$', r['reply'])
        _clean_copy(self, r['reply'])

    def test_her_message_as_it_was_sent(self):
        for r in self.res[0:3]:
            self._is_the_lane(r)
            self.assertTrue(r['reply'].startswith('That is a question about the dashboard itself, not about the data.'))
            self.assertIn('I cannot see what that date field is doing from here', r['reply'])
            self.assertIn('tell me what you want to see and the dates in words', r['reply'])
            self.assertFalse(re.search(r"\d", r['reply']))               # none of the profile's figures, none of her dates read back as fact
            self.assertNotIn('On it', r['reply'])
            self.assertEqual(r['chips'], [])
        self.assertEqual(self.res[0]['reason'], 'dashboard_behavior')

    def test_the_same_class_on_every_page(self):
        for it, r in zip(DOOR_ITEMS[3:19], self.res[3:19]):
            self._is_the_lane(r)
            self.assertIn('I cannot see what that ', r['reply'], it['question'])
            for name in ('Landman', 'Babylon 5', 'Stanley', 'Target', 'Gilmore Girls', 'Paramount'):
                self.assertNotIn(name, r['reply'], it['question'])       # whatever is open is never named

    def test_the_reader_s_verdict_takes_a_wording_the_test_does_not_know(self):
        r = self.res[19]
        self.assertEqual((r['route'], r['outcome']), ('dashboard_behavior', 'raised'))
        self.assertEqual(r['jobs'], [])
        self.assertFalse(r['offer'])
        _clean_copy(self, r['reply'])
        self.assertEqual([c['surface'] for c in r['calls']], ['reader'])     # the Reader, and nothing after it

    def test_doing_it_yourself_is_directions_never_a_read(self):
        r = self.res[20]
        self._is_the_lane(r, outcome='answered')
        self.assertTrue(r['reply'].startswith('You can do a lot of it yourself. Every product is in the SELECT PRODUCT dropdown'))
        for w in ('historic mode', 'lookback', 'as-of', 'On it'):
            self.assertNotIn(w, r['reply'])

    def test_the_older_route_is_covered_at_the_core(self):
        r = self.res[21]
        self.assertEqual((r['route'], r['outcome']), ('dashboard_behavior', 'raised'))
        self.assertEqual(r['calls'], [])
        _clean_copy(self, r['reply'])

    def test_another_subject_is_never_answered_from_the_open_profile(self):
        want = ('Psych', 'Taylor Swift', 'Yellowstone', 'Psych', 'Psych')
        for it, r, name in zip(DOOR_ITEMS[22:27], self.res[22:27], want):
            self.assertNotEqual(r['route'], 'dashboard_behavior', it['question'])
            self.assertIn(name, str(r['subject']), it['question'])
            self.assertNotIn(PAGE, str(r['subject']))
            self.assertIn(name, r['reply'])
            self.assertNotIn(PAGE, r['reply'])
            self.assertFalse(any(c.get('page_rows') for c in r['calls']), 'the open profile was handed to a model')

    def test_no_subject_is_asked_never_assumed(self):
        for it, r in zip(DOOR_ITEMS[27:30], self.res[27:30]):
            self.assertIn((r['route'], r['outcome']), (('reasoned_metrics', 'asked_audience'), ('open_screen_confirm', 'clarified')),
                          it['question'])
            self.assertEqual(r['jobs'], [])
            self.assertFalse(r['offer'])
            self.assertIn('open on your screen', r['reply'])
            self.assertTrue(r['reply'].rstrip().endswith(('?', 'audience.')), r['reply'])
            self.assertFalse(re.search(r"\d", r['reply']), 'a figure from the open profile was volunteered')
            self.assertFalse(any(c.get('page_rows') for c in r['calls']))
            self.assertTrue(any(PAGE in c for c in r['chips']))          # the profile is offered, one tap, never taken

    def test_the_screen_is_used_when_the_words_point_at_it(self):
        female, buy, brands, summary, named = self.res[30:35]
        self.assertEqual((female['route'], female['outcome'], female['subject']), ('panel_fact', 'answered', PAGE))
        self.assertIn('53.2% female', female['reply'])
        for r in (buy, brands):
            self.assertEqual((r['route'], r['subject']), ('page_analysis', PAGE))
            self.assertTrue(any(c.get('page_rows') for c in r['calls']), 'the page analyst reads the open profile')
            self.assertTrue(any(c.get('rule') for c in r['calls']), 'and carries the rule about the dashboard\'s own controls')
        self.assertEqual(summary['route'], 'reasoned_metrics')
        self.assertTrue(summary['jobs'])
        self.assertIn('reading Amazon Prime Video now', summary['reply'])
        self.assertIn(PAGE, str(named['subject']))

    def test_an_audience_noun_alone_never_takes_the_screen(self):
        for r in self.res[35:37]:
            self.assertNotEqual((r['route'], r['outcome']), ('panel_fact', 'answered'), r['reply'][:120])
            self.assertNotEqual(r['route'], 'page_analysis', r['reply'][:120])
            self.assertTrue(r['reply'].rstrip().endswith(('?', 'audience.')), r['reply'])
            self.assertFalse(re.search(r"\d", r['reply']), 'a figure from the open profile was volunteered')
            self.assertTrue(any(PAGE in c for c in r['chips']), 'the profile is offered, one tap, never taken')


if __name__ == '__main__':
    unittest.main(verbosity=1)
