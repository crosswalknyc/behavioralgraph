#!/usr/bin/env python3
"""Grace Yin's asks of 2026-11-16 (Amazon), as a standing regression set
(Jenna 2026-11-16: "check grace's queries today and make sure they are
being properly processed").

What went wrong that day, and what holds it now:

  * "Please run two individual talent profiles: Zac Efron and Dakota
    Johnson, US, calendar 2025." The library held both on exactly that
    window. Both were quoted as a refresh and rebuilt. A refresh of a
    window the matched file already holds in full is a reuse
    (prometheus.held_window).
  * "For each, I need: - Age x gender breakdown, with Women 18-34 as its
    own line - Index vs. the US population for each group. Please send
    them as CSVs" describes the file. "Women 18-34" was read as a cut and
    built as one. A requirements list under an order rides as deliverable
    notes (prometheus.orders.split).
  * The Approve request never named its thread, so no launch since
    2026-11-11 had one and no finished line was posted. The widget sends
    the thread, and the server falls back to the active thread for a
    dashboard Approve (prometheus.build_ready.active_thread).
  * "Two individual talent profiles" was asked "individual or combined?".
    Three years of one actor were listed as "Zac Efron, Zac Efron, Zac
    Efron" and offered as one combined profile. "Why do we have ... 7
    different names?" was sent off as a new ask. The widget shows the
    list with its windows, asks nothing the ask already answered, and
    answers a question about the list from the list.
  * An included seat was quoted "$400 ... Total cost: $800 on approval".
    An included seat reads "Included with your plan."
  * The 12:41 read printed "sits at [f3_na] 22,361,678". A figure token
    with a tail the registry never issued is scrubbed like any other.

Hermetic: no model, no S3, no app module. The widget checks run its own
functions under node when node is on the machine, else they are string
checks only.
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
from datetime import date

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class _NoAppModule:
    def find_spec(self, name, path=None, target=None):
        if name == 'app':
            raise ImportError('app is not importable inside this hermetic suite')
        return None


sys.meta_path.insert(0, _NoAppModule())

from prometheus import held_window as HW  # noqa: E402
from prometheus import orders as O  # noqa: E402
from prometheus import build_ready as BR  # noqa: E402

# her words, as typed (the bar character is what her paste left behind)
Q1 = "Can you run profiles on fan demographic of Dakota Johnson and Zac Efron?"
Q2 = ("We'd like a talent audience read on two actors, Zac Efron and Dakota Johnson, along with a few comparison "
      "talent so we can benchmark them.  Engaged definition: please use your standard talent-family approach and "
      "tell us what counts (watched their titles, searches, page visits, social). We'd like two cuts reported "
      "separately:\n 1. Watched any of their films or series\n 2. Social/search only (no viewing)\n Window: calendar "
      "2024, 2025 and 2026 YTD, each year separately.\n For each talent and year, please provide:\n - Total US "
      "engaged people and the panelist count behind it\u258e - Age \u00d7 gender split, with Women 18\u201334 as a "
      "single line\n\u258e - An index for each age/gender group vs. the US population baseline\n\u258e - Depth tiers "
      "(1 touch / 2\u20133 / 4+ avid), sp\n\u258e - Overlap: people engaged with both Efron and Johnson, by age "
      "\u00d7 gender                                             \n\u258e Also, as a validation read: the US audience "
      "for Ricky Stanicky (2024) in its first 28 days after release, by age \u00d7 gender.")
Q3 = ("why do we have \nZac Efron\n\u2022\nZac Efron\n\u2022\nZac Efron\n\u2022\nDakota Johnson\n\u2022\nDakota Johnson"
      "\n\u2022\nDakota Johnson\n\u2022\nRicky Stanicky 7 diferent names?")
Q4 = ("Please run two individual talent profiles: Zac Efron and Dakota Johnson, US, calendar 2025.\u258e\n For each, "
      "I need:\n- Age \u00d7 gender breakdown, with Women 18\u201334 as its own line\n - Index vs. the US population "
      "for each group\n\n Please send them as CSVs")

ZAC_KEY = 'Zac_Efron_06_06_2026_20_54.csv'
ZAC_HEAD = ("Column,Value,Brand Penetration (Row),Category Share,Original Raw Numbers,US Gen Pop Projection,"
            "Gen Pop Penetration,Index vs Gen Pop\nBRAND INPUT,ZAC EFRON,100.0,100.0,2838656,93647261,,\n"
            "SAMPLE SIZE,SAMPLE SIZE (2025-01-01 TO 2025-12-31) | BEHAVIOR STUDY (2025-01-01 TO 2025-12-31),"
            "100.0,,2838656,93647261,,\nBRAND CATEGORY,ACTOR,,,,,,\n")


def _reader(head=ZAC_HEAD):
    return lambda key: HW.file_window(head)


def _refresh(**kw):
    d = {'decision': 'time_shifted_refresh', 'subject': 'Zac Efron', 'existing_match_s3_key': ZAC_KEY,
         'date_range': {'start': '2025-01-01', 'end': '2025-12-31'}, 'engine_date_range': '2025-01-01 TO 2025-12-31',
         'refresh_row_hypothesis': 'Between the June 2026 parent file and the 2025 calendar window ...'}
    d.update(kw)
    return d


class HeldWindow(unittest.TestCase):
    def test_file_window_reads_the_sample_size_row(self):
        self.assertEqual(HW.file_window(ZAC_HEAD), (date(2025, 1, 1), date(2025, 12, 31)))
        self.assertEqual(HW.file_window(ZAC_HEAD.encode()), (date(2025, 1, 1), date(2025, 12, 31)))
        self.assertIsNone(HW.file_window('Column,Value\nBRAND INPUT,X\n'))

    def test_her_calendar_2025_ask_reuses_the_file_on_record(self):
        d = _refresh()
        self.assertTrue(HW.settle(d, _reader(), Q4))
        self.assertEqual(d['decision'], 'existing_match')
        self.assertEqual(d['existing_match_s3_key'], ZAC_KEY)
        self.assertNotIn('refresh_row_hypothesis', d)
        self.assertNotIn('engine_date_range', d)
        self.assertEqual(d['held_window']['label'], 'Jan 1 to Dec 31, 2025')
        self.assertIn('already covers Jan 1 to Dec 31, 2025', d['decision_reason'])
        for bad in ('synth', 'pipeline', 'modeled', '\u2014'):
            self.assertNotIn(bad, d['decision_reason'])

    def test_the_window_as_a_string_is_read_too(self):
        d = _refresh(date_range=None, engine_date_range='2025-01-01 TO 2025-12-31')
        self.assertTrue(HW.settle(d, _reader(), Q4))

    def test_a_different_window_stays_a_refresh(self):
        for s, e in (('2024-01-01', '2024-12-31'), ('2025-01-01', '2025-12-30'), ('2025-11-16', '2026-11-16'),
                     ('2026-01-01', '2026-11-16')):
            d = _refresh(date_range={'start': s, 'end': e}, engine_date_range=f'{s} TO {e}')
            self.assertFalse(HW.settle(d, _reader(), Q4), (s, e))
            self.assertEqual(d['decision'], 'time_shifted_refresh')
            self.assertIn('refresh_row_hypothesis', d)

    def test_a_file_built_before_its_window_closed_stays_a_refresh(self):
        head = ZAC_HEAD.replace('2025-01-01 TO 2025-12-31', '2025-07-01 TO 2026-06-30')
        d = _refresh(date_range={'start': '2025-07-01', 'end': '2026-06-30'},
                     engine_date_range='2025-07-01 TO 2026-06-30')
        self.assertFalse(HW.settle(d, _reader(head), 'run Zac Efron for July 2025 to June 2026'))
        d2 = _refresh(existing_match_s3_key='Zac Efron.csv')       # no build date in the name: no proof it is whole
        self.assertFalse(HW.settle(d2, _reader(), Q4))

    def test_the_readers_word_for_a_fresh_pull_stands(self):
        for words in ('Please refresh the Zac Efron profile for calendar 2025', 'rebuild Zac Efron 2025',
                      'I want an updated version of Zac Efron for 2025', 'pull it again for calendar 2025',
                      'run Zac Efron 2025 from scratch'):
            d = _refresh()
            self.assertFalse(HW.settle(d, _reader(), words), words)
        self.assertFalse(HW.wants_fresh(Q4))
        self.assertFalse(HW.wants_fresh('the current Zac Efron audience, calendar 2025'))

    def test_only_a_refresh_with_a_named_readable_file_is_touched(self):
        for dec in ('new_build', 'existing_match', 'derive_cut', 'cut_needs_parent', 'subscriber_iq'):
            d = _refresh(decision=dec)
            self.assertFalse(HW.settle(d, _reader(), Q4), dec)
            self.assertEqual(d['decision'], dec)
        self.assertFalse(HW.settle(_refresh(existing_match_s3_key=''), _reader(), Q4))
        self.assertFalse(HW.settle(_refresh(), lambda k: None, Q4))                  # the window could not be read
        self.assertFalse(HW.settle(_refresh(), None, Q4))
        self.assertFalse(HW.settle(_refresh(ask_existing_profile=True), _reader(), Q4))   # a question is already riding
        self.assertFalse(HW.settle(None, _reader(), Q4))
        self.assertFalse(HW.settle(_refresh(date_range={}, engine_date_range=''), _reader(), Q4))

    def test_on_the_host_the_cuts_derive_off_the_held_file_and_the_reuse_is_priced(self):
        calls = []

        class _S3:
            def get_object(self, Bucket, Key, Range):
                calls.append((Bucket, Key, Range))
                return {'Body': type('B', (), {'read': lambda self: ZAC_HEAD.encode()})()}

        class _H:
            s3_client, S3_BUCKET = _S3(), 'dashboard-inputs'
            _V1_CREDITS = {'existing_match': 0, 'time_shifted_refresh': 6, 'derive_cut': 2}

            @staticmethod
            def _maybe_promote_embedded_cuts_to_parent(draft, catalog):
                calls.append(('promote', draft['decision']))
                if draft.get('addon_cuts'):
                    draft['decision'], draft['derive_type'], draft['estimated_credits'] = 'derive_cut', 'addon_cuts', 2

            @staticmethod
            def _normalize_v1_decision(draft):
                return draft['decision'], draft.get('existing_match_s3_key'), draft.get('derive_type')

            @staticmethod
            def _apply_existing_match_retail_price(draft, user, username):
                calls.append(('retail', username))

            @staticmethod
            def _stamp_existing_match_age(draft):
                draft['existing_match_days_old'] = 163

        HW._WINDOW_CACHE.clear()
        d = _refresh(estimated_credits=6, estimated_run_minutes=20)
        self.assertEqual(HW.settle_on_host(d, Q4, _H, username='graceyin', log=None), 'existing_match')
        self.assertEqual((d['estimated_credits'], d['estimated_run_minutes']), (0, 0))
        self.assertEqual(calls[0], ('dashboard-inputs', ZAC_KEY, 'bytes=0-8191'))
        self.assertIn(('retail', 'graceyin'), calls)
        d2 = _refresh(addon_cuts=[{'cut_id': 'age_18_34', 'pin_category': 'AGE', 'pin_buckets': ['18-24', '25-34']}])
        self.assertEqual(HW.settle_on_host(d2, Q4, _H, log=None), 'derive_cut')
        self.assertEqual(d2['estimated_credits'], 2)
        d3 = _refresh(decision='new_build')
        self.assertEqual(HW.settle_on_host(d3, Q4, _H, log=None), '')

    def test_wired_into_every_interpret_path(self):
        chat = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        app = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
        self.assertEqual(chat.count(".settle_on_host(spec_draft, "), 2)             # the single path and the batch slice
        self.assertIn('_held_window.settle_on_host(', app)                          # the array path
        self.assertLess(app.index('_held_window.settle_on_host('),
                        app.index('_decompose_embedded_subject_cuts(spec_draft, prompt_text)'))


class RequirementsRideAsNotes(unittest.TestCase):
    def test_her_order_reads_as_the_order_alone(self):
        core, notes = O.split(Q4)
        self.assertEqual(core, 'Please run two individual talent profiles: Zac Efron and Dakota Johnson, US, '
                               'calendar 2025.')
        for piece in ('Women 18', 'Age', 'Index vs. the US population', 'as CSVs', 'For each, I need'):
            self.assertIn(piece, notes)
            self.assertNotIn(piece, core)
        self.assertNotIn('\u258e', core + notes)

    def test_the_file_line_still_tells_her_what_it_carries(self):
        line = O.deliverable_line(O.split(Q4)[1])
        self.assertIn('the age and gender split', line)
        self.assertIn('an index against the US average', line)

    def test_the_long_brief_keeps_its_requirement_list_out_of_the_subjects(self):
        core, notes = O.split(Q2)
        for piece in ('panelist count behind it', 'Women 18', 'Depth tiers', 'Overlap: people engaged with both'):
            self.assertIn(piece, notes)
            self.assertNotIn(piece, core)
        self.assertIn('Zac Efron and Dakota Johnson', core)

    def test_a_list_of_things_to_build_is_never_taken_for_notes(self):
        for text in ("run individual profiles for:\n- VIZIO\n- Samsung\n- LG",
                     "Please build profiles for these. I need:\n- VIZIO owners\n- Samsung owners",
                     "Please build profiles. I need:\n- a profile of VIZIO owners\n- a profile of Samsung owners"):
            core, notes = O.split(text)
            self.assertEqual(notes, '', text)
            self.assertIn('Samsung', core)

    def test_the_standing_orders_split_as_before(self):
        fallout = ("Please queue one Profile IQ: US people who watched Fallout Season 2 on Prime Video, Nov 1 2025 "
                   "\u2013 Oct 31 2026. Include reach, age and gender split, top other titles and genres they watch, "
                   "and their streaming platform mix. Deliver as a CSV with the unprojected panelist count for each "
                   "figure.")
        core, notes = O.split(fallout)
        self.assertTrue(core.endswith('Oct 31 2026.'))
        self.assertTrue(notes.startswith('Include reach'))
        self.assertEqual(O.split('Run a profile on Reba McEntire fans, split by age'),
                         ('Run a profile on Reba McEntire fans', 'Split by age'))
        self.assertEqual(O.split(Q1), (Q1, ''))
        self.assertEqual(O.split(Q3)[1], '')
        self.assertFalse(O.is_order(Q3))


class ApproveNamesItsThread(unittest.TestCase):
    def test_a_dashboard_approve_falls_back_to_the_active_thread(self):
        self.assertEqual(BR.active_thread(lambda: {'active': 'a9dc4257ad', 'threads': []}), 'a9dc4257ad')
        self.assertEqual(BR.active_thread(lambda: {'active': 'a9dc4257ad'}, api=True), '')
        self.assertEqual(BR.active_thread(lambda: None), '')
        self.assertEqual(BR.active_thread(lambda: 1 / 0), '')

    def test_a_launch_with_its_thread_posts_the_finished_line_there(self):
        saved = {}
        launch = BR.note_launch(lambda k, d: saved.update({k: d}), run_id='Ac6rgcCmsq_63w', user='graceyin',
                                thread_id=BR.active_thread(lambda: {'active': 'a9dc4257ad'}), subject='Zac Efron',
                                decision='time_shifted_refresh')
        self.assertEqual(launch['thread_id'], 'a9dc4257ad')
        threads = {('graceyin', 'a9dc4257ad'): [{'role': 'user', 'text': Q4}]}
        out = BR.post_ready(launch, {'run_id': 'Ac6rgcCmsq_63w', 'status': 'complete', 'subject': 'Zac Efron'},
                            exists=lambda u, t: (u, t) in threads, load=lambda u, t: threads[(u, t)],
                            save=lambda u, t, turns: threads.update({(u, t): turns}))
        self.assertEqual(out, 'posted')
        self.assertEqual(threads[('graceyin', 'a9dc4257ad')][-1]['text'],
                         'Zac Efron is ready. It is in Profile IQ: open the Select Profile dropdown and pick Zac Efron.')
        no_thread = dict(launch, thread_id='')
        self.assertEqual(BR.post_ready(no_thread, {'status': 'complete'}, exists=lambda u, t: True,
                                       load=lambda u, t: [], save=lambda *a: None), 'no_thread')

    def test_the_approve_core_and_the_widget_both_carry_it(self):
        chat = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        self.assertIn('_build_ready.active_thread(lambda: _load_threads_index(_approve_username)', chat)
        html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
        for m in re.finditer(r"fetch\('/api/brief-chat/approve'", html):
            body = html[m.start():m.start() + 1400]
            self.assertIn('thread_id:', body)
            self.assertIn('_pmThreadsIdx.active', body)


class NoTokenReachesAReader(unittest.TestCase):
    def test_a_token_with_a_tail_never_prints(self):
        from prometheus import figures as F
        out = F.Registry().render('Zac Efron - Avid Fan sits at [f3_na] 22,361,678 projected US people. '
                                  'Alone it reads [f7_x]. And [f99].')
        self.assertEqual(out, 'Zac Efron - Avid Fan sits at 22,361,678 projected US people. '
                              'Alone it reads that figure. And that figure.')
        self.assertNotIn('[f', out)


_JS_NAMES = ('_pmBatchName', '_pmBatchWindow', '_pmBatchNamesRepeat', '_pmBatchListLines', '_pmBatchSaysSeparate',
             '_pmAsksAboutBatchList', '_pmBatchExplain')


def _js_function(html, name):
    i = html.index('function ' + name + '(')
    depth, j = 0, html.index('{', i)
    while True:
        c = html[j]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return html[i:j + 1]
        j += 1


class TheWidgetList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()

    def test_the_pieces_are_on_the_page(self):
        for name in _JS_NAMES:
            self.assertIn('function ' + name + '(', self.html)
        self.assertIn('_pmBatchSaysSeparate(text) || _pmBatchNamesRepeat(data.spec_drafts)', self.html)
        self.assertIn('_pmAsksAboutBatchList(text)', self.html)
        self.assertIn('window.PM_SEAT_INCLUDED = {{ (seat_included|default(false))|tojson }};', self.html)
        self.assertIn("lines.push('Included with your plan.');", self.html)
        self.assertIn("facts.push('Cost: Included with your plan');", self.html)

    @unittest.skipUnless(shutil.which('node'), 'node is not on this machine')
    def test_her_words_through_the_widget_functions(self):
        fns = '\n'.join(_js_function(self.html, n) for n in _JS_NAMES)
        drafts = ([{'subject': 'Zac Efron', 'date_window_label': f'Jan 1 to Dec 31, {y}'} for y in (2024, 2025)]
                  + [{'subject': 'Zac Efron', 'date_window_label': 'Jan 1 to Nov 16, 2026 (year to date)'}]
                  + [{'subject': 'Dakota Johnson', 'date_window_label': f'Jan 1 to Dec 31, {y}'} for y in (2024, 2025)]
                  + [{'subject': 'Dakota Johnson', 'date_window_label': 'Jan 1 to Nov 16, 2026'}]
                  + [{'subject': 'Ricky Stanicky', 'date_window_label': 'Mar 7 to Apr 3, 2024'}])
        two = [{'subject': 'Zac Efron'}, {'subject': 'Dakota Johnson'}]
        script = fns + """
function _synthChatFormatRange(r) { return (r && r.start && r.end) ? (r.start + ' to ' + r.end) : ''; }
var IN = %s;
console.log(JSON.stringify({
  sep_q4: _pmBatchSaysSeparate(IN.q4), sep_q1: _pmBatchSaysSeparate(IN.q1), sep_q2: _pmBatchSaysSeparate(IN.q2),
  sep_combined: _pmBatchSaysSeparate('one combined profile for Zac Efron and Dakota Johnson, not individual ones'),
  rep7: _pmBatchNamesRepeat(IN.seven), rep2: _pmBatchNamesRepeat(IN.two),
  lines7: _pmBatchListLines(IN.seven), lines2: _pmBatchListLines(IN.two),
  about_q3: _pmAsksAboutBatchList(IN.q3), about_choice: _pmAsksAboutBatchList('7 individual profiles'),
  about_new: _pmAsksAboutBatchList('How many people watched Fallout on Prime Video?'),
  explain: _pmBatchExplain(IN.seven)
}));
""" % json.dumps({'q1': Q1, 'q2': Q2, 'q3': Q3, 'q4': Q4, 'seven': drafts, 'two': two})
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
            f.write(script)
            path = f.name
        try:
            out = json.loads(subprocess.run(['node', path], capture_output=True, text=True, timeout=60, check=True).stdout)
        finally:
            os.unlink(path)
        self.assertTrue(out['sep_q4'])                       # "two individual talent profiles": nothing to ask
        self.assertFalse(out['sep_q1'])                      # "run profiles on ... A and B" still asks
        self.assertFalse(out['sep_combined'])
        self.assertTrue(out['rep7'])                         # one name on several windows is never offered combined
        self.assertFalse(out['rep2'])
        self.assertEqual(out['lines2'], ['Zac Efron', 'Dakota Johnson'])
        self.assertEqual(out['lines7'][:3], ['Zac Efron, Jan 1 to Dec 31, 2024', 'Zac Efron, Jan 1 to Dec 31, 2025',
                                             'Zac Efron, Jan 1 to Nov 16, 2026'])
        self.assertEqual(len(set(out['lines7'])), 7)         # no two lines read the same
        self.assertTrue(out['about_q3'])
        self.assertFalse(out['about_choice'])
        self.assertFalse(out['about_new'])
        ex = out['explain']
        self.assertIn('Zac Efron is listed 3 times', ex)
        self.assertIn('Jan 1 to Dec 31, 2024; Jan 1 to Dec 31, 2025; Jan 1 to Nov 16, 2026', ex)
        self.assertIn('Ricky Stanicky, Mar 7 to Apr 3, 2024, once.', ex)
        for bad in ('\u2014', 'synth', 'pipeline', 'modeled', 'undefined', 'null'):
            self.assertNotIn(bad, ex)


class TheFinishedLineIsSaidOnce(unittest.TestCase):
    """Her thread, 2:40 PM: the server said 'Dakota Johnson is ready.' and
    the tab she had left open said it again two minutes later."""

    SERVER = {'role': 'agent', 'ts': '2026-11-16T22:40:22.855Z', 'turn_id': 'ready-MbzHk_VWxW_T5g',
              'text': 'Dakota Johnson is ready. It is in the Select Profile dropdown under Dakota Johnson.',
              'meta': {'kind': 'build_ready', 'build_run_id': 'MbzHk_VWxW_T5g', 'server': True,
                       'landed_at': '2026-11-16T22:40:22.855Z', 'subject': 'Dakota Johnson'}}
    TAB = {'role': 'agent', 'ts': '2026-11-16T22:42:34.083Z', 'turn_id': 'tmwjumsir2a01hz',
           'text': 'Dakota Johnson is finished and ready in the Select Profile dropdown.\n'
                   'In Select Profile as: Dakota Johnson\nAvid Fan cut: Dakota Johnson - Avid Fan'}
    STAGE = {'role': 'agent', 'ts': '2026-11-16T22:11:37.785Z', 'turn_id': 'tmwjtj06xte2kby',
             'text': 'Dakota Johnson: building the add-on cuts.'}

    def setUp(self):
        from prometheus import thread_merge as TM
        self.TM = TM

    def test_the_tab_line_after_the_server_line_is_not_stored(self):
        out = self.TM.merge_turns([self.STAGE, self.SERVER], [self.STAGE, self.TAB])
        self.assertEqual([t['turn_id'] for t in out], ['tmwjtj06xte2kby', 'ready-MbzHk_VWxW_T5g'])
        self.assertTrue(self.TM.differs([self.STAGE, self.TAB], out))

    def test_the_tab_line_alone_is_kept(self):
        out = self.TM.merge_turns([self.STAGE], [self.STAGE, self.TAB])
        self.assertEqual(out[-1]['turn_id'], 'tmwjumsir2a01hz')

    def test_another_subject_is_not_dropped(self):
        other = dict(self.TAB, turn_id='zac1', text='Zac Efron is finished and ready in the Select Profile dropdown.')
        out = self.TM.merge_turns([self.SERVER], [other])
        self.assertIn('zac1', [t.get('turn_id') for t in out])

    def test_a_stage_line_and_a_later_build_are_kept(self):
        late = dict(self.TAB, turn_id='late1', ts='2026-11-18T22:42:34.083Z')
        out = self.TM.merge_turns([self.SERVER], [self.STAGE, late])
        ids = [t.get('turn_id') for t in out]
        self.assertIn('tmwjtj06xte2kby', ids)
        self.assertIn('late1', ids)

    def test_nothing_already_stored_is_removed(self):
        out = self.TM.merge_turns([self.SERVER, self.TAB], [self.TAB])
        self.assertEqual(len(out), 2)


if __name__ == '__main__':
    unittest.main(verbosity=1)
