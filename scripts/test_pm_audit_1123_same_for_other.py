""""Same thing but for X" carries the previous ask's frame (Nov 23 2026
ask audit, fix 7; Jenna 2026-11-23: platform scope, window, metric and
audience carry to the new subject unless the new words change them).

The live case, sanitized: a pasted ask sized one title's US audience on
YouTube's Free with ads shelf; "let's now do the same thing but for
<title> in the window of (...)" twice more carried the shelf while the
landed reads sat in the recent history; the third, after two held sends
and two re-sends, ran the new title on a service of the model's own
choosing. The same title read on YouTube in a fresh thread gave another
figure.

The rules under test:

* `read_frame.same_for_other` reads the same-again shape with a new
  subject (and its window); a window-only or platform-only "subject" is
  not one (those are the re-run's and the parameter change's).
* `continuation.classify` makes it `named_other` with `frame_carry`,
  never a parameter change on the old subject.
* `read_frame.root_question` / `carry`: the chain's first full question,
  rewritten for the new subject and window, with one plain line naming
  the frame it keeps (the platform, the metrics, the window).
* Through the service door, the read core is handed the carried question
  with the new subject bound; the route and outcome are what they were.

Pure tests on a stub host; no network, no model.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, 'pm_corpus'))

from prometheus import continuation as cont  # noqa: E402
from prometheus import read_frame as rf  # noqa: E402

ROOT_Q = ("We're looking to size the potential U.S. audience and viewership specifically on YouTube for the titles and availability "
          "windows below. These titles were available through YouTube's Movies & TV \u201cFree with ads\u201d section, similar to this "
          "example video. If possible, we'd like to understand title-level performance during it's specific window, including U.S. "
          "unique viewers, total views, and plays, as well as any other available metrics that can help us understand the size and "
          "level of engagement of the audience. The titles and window is Alpha Road (10/1/26-10/31/26)")
READ_1 = ("On Alpha Road:\n\nAlpha Road drew 247,316 unique US viewers on YouTube Free with ads across its Oct 1 to Oct 31 2026 window, "
          "against 331,874 plays and 13.5M watch minutes.\n\nUnique US viewers: 247,316, Total US views: 418,263, Total US plays: 331,874.")
READ_2 = ("Beta Lake drew 416,287 unique US viewers on YouTube Movies & TV Free with ads across Oct 1 to Nov 9 2026, against 566,151 plays "
          "and 26.3M watch minutes.")
CALM = "Working on it. Confirmed your task is in progress and I will email you when it completes."

# a second live shape, sanitized: a serialized-comics thread (one title read on its reading app, then an
# adaptation sign-up read on a video service), then one message with two parts: "Can we do this same
# analysis for <title two>? And <title three>?"
COMIC_Q1 = ("hello - I'm curious to understand more about Ember Vale the webcomic. do we know how passionate this audience is about the "
            "comic itself? can we see how many of these are active readers of Ember Vale vs. they only read it once in a session?")
COMIC_R1 = ("On Ember Vale: 809,080 people in this audience read on the comics app itself, 52.5% of the file and 16.3x the US rate, and "
            "268,415 of them sit in the Avid tier. Window: Nov 2 2025 to Nov 2 2026.")
COMIC_Q2 = ("ok - so how about this, how many of those avid Ember Vale readers have engaged with Prime Video in the trailing six months? "
            "and how many of them do you think would sign up for Prime Video to watch an adaptation?")
COMIC_R2 = ("On Ember Vale - Avid readers: 164,537 of the 268,415 Avid Ember Vale readers already ran Prime Video in the trailing six "
            "months, and an adaptation converts 10,183 new paid sign-ups out of the 103,878 who did not. Window: May 3 2026 to Nov 2 2026.")
COMIC_ASK = "Can we do this same analysis for Iron Orchard? And Night Market?"


def _comic_history():
    return [
        {"role": "user", "ts": "2026-11-03T15:02:11Z", "text": COMIC_Q1},
        {"role": "agent", "ts": "2026-11-03T15:04:40Z", "text": COMIC_R1, "meta": {"kind": "read", "read_job_id": "jobc1cccccccc", "subject": "Ember Vale"}},
        {"role": "user", "ts": "2026-11-03T15:09:02Z", "text": COMIC_Q2},
        {"role": "agent", "ts": "2026-11-03T15:11:31Z", "text": COMIC_R2, "meta": {"kind": "read", "read_job_id": "jobc2cccccccc", "subject": "Ember Vale - Avid readers"}},
    ]


def _history():
    return [
        {"role": "user", "ts": "2026-11-24T00:43:06Z", "text": ROOT_Q},
        {"role": "agent", "ts": "2026-11-24T00:43:27Z", "text": "Included with your plan. On it: reading Alpha Road and YouTube now. This one takes a real look at the data, so give me a moment - the read will land right here when it is ready."},
        {"role": "agent", "ts": "2026-11-24T00:45:10Z", "text": READ_1, "meta": {"kind": "read", "read_job_id": "job1aaaaaaaa", "subject": "Alpha Road"}},
        {"role": "user", "ts": "2026-11-24T00:45:37Z", "text": "let's now do the same thing but for Beta Lake in the window of (10/1/26-11/9/26)"},
        {"role": "agent", "ts": "2026-11-24T00:47:40Z", "text": READ_2, "meta": {"kind": "read", "read_job_id": "job2bbbbbbbb", "subject": "Beta Lake"}},
        {"role": "user", "ts": "2026-11-24T00:54:58Z", "text": "Email me this file"},
        {"role": "agent", "ts": "2026-11-24T00:55:10Z", "text": "Sent Beta Lake Week of availability window.csv to the address on your seat.", "meta": {"kind": "email_file"}},
        {"role": "user", "ts": "2026-11-24T01:02:23Z", "text": "Email me this file"},
        {"role": "agent", "ts": "2026-11-24T01:02:26Z", "text": CALM, "meta": {"kind": "calm"}},
        {"role": "user", "ts": "2026-11-24T01:06:23Z", "text": "Email me this file"},
        {"role": "agent", "ts": "2026-11-24T01:06:27Z", "text": CALM, "meta": {"kind": "calm"}},
    ]


ASK = "let's now do the same thing but for Delta Hour in the window of (11/1/26-11/9/26)"


class TheWords(unittest.TestCase):
    def test_same_for_other_reads_the_new_subject_and_its_window(self):
        r = rf.same_for_other(ASK)
        self.assertEqual(r['subject'], 'Delta Hour')
        self.assertEqual((r['window']['start'], r['window']['end']), ('2026-11-01', '2026-11-09'))
        self.assertEqual(rf.same_for_other("same thing but for Nike")['subject'], 'Nike')
        r2 = rf.same_for_other("same for Chicago PD for the year 2020")
        self.assertEqual((r2['subject'], r2['window']['start'], r2['window']['end']), ('Chicago PD', '2020-01-01', '2020-12-31'))
        r3 = rf.same_for_other("same thing for Nike in Q4 2025")
        self.assertEqual((r3['subject'], r3['window']['start']), ('Nike', '2025-10-01'))
        self.assertEqual(rf.same_for_other("same for Moonlight Road (10/1/26-11/9/26)")['subject'], 'Moonlight Road')

    def test_a_bare_name_after_a_same_again_turn_is_the_same_read_for_that_name(self):
        for t in ("And Night Market?", "what about Night Market", "and for Night Market too", "Night Market?"):
            self.assertEqual(rf.bare_name(t), 'Night Market', t)
        for t in ("And Hulu?", "Can we give some examples?", "and women 18-34?", "And the second one?", "same for 2023", "ok",
                  "Dogma (11/1/26-11/9/26)", "And Moonfall in the window of (11/1/26-11/9/26)"):
            self.assertEqual(rf.bare_name(t), '', t)
        hist = _comic_history() + [{"role": "user", "text": "Can we do this same analysis for Iron Orchard?"}]
        self.assertTrue(rf.follows_same_again(hist))
        self.assertFalse(rf.follows_same_again(_comic_history()))
        out = cont.classify("And Night Market?", hist, reading=None)
        self.assertEqual(out['kind'], 'named_other', out)
        self.assertEqual(out['params'].get('named_title'), 'Night Market', out)
        self.assertEqual(out['params'].get('was'), 'same_for_other', out)

    def test_a_window_or_a_platform_alone_is_not_a_new_subject(self):
        for t in ("run the same read again for Q4 2025", "the same for 2023", "same read for last 90 days", "do the same but on Hulu",
                  "the same table shows up twice", "what about Nike", "Same ten services and window (Nov 2025 through Oct 2026). Among subscribers who cancelled, split by billing path"):
            self.assertIsNone(rf.same_for_other(t), t)


class TheContinuation(unittest.TestCase):
    def test_named_other_with_the_frame(self):
        out = cont.classify(ASK, _history())
        self.assertEqual(out['kind'], 'named_other')
        self.assertEqual(out['subject'], 'Delta Hour')
        self.assertEqual(out['window'], ('2026-11-01', '2026-11-09'))
        self.assertTrue(out['params'].get('frame_carry'))
        self.assertEqual(out['params'].get('carried'), 'Beta Lake')

    def test_the_same_subject_again_is_not_named_other(self):
        out = cont.classify("same thing but for Beta Lake in Q4 2025", _history())
        self.assertNotEqual(out['kind'], 'named_other')
        out2 = cont.classify("same thing but for Beta Lake in Q4 2025", _history()[:5])
        self.assertNotEqual(out2['kind'], 'named_other')
        self.assertEqual(out2['subject'], 'Beta Lake')

    def test_a_rerun_on_a_window_keeps_its_kind(self):
        out = cont.classify("run the same read again for Q4 2025", _history()[:5])
        self.assertIn(out['kind'], ('rerun_window', 'parameter_change'))
        self.assertEqual(out['subject'], 'Beta Lake')


class TheFrame(unittest.TestCase):
    def test_root_question_walks_back_past_the_same_again_turns(self):
        root = rf.root_question(_history())
        self.assertEqual(root['question'], ROOT_Q)
        self.assertEqual(root['subject'], 'Alpha Road')
        self.assertEqual(root['platform'], 'YouTube')
        self.assertEqual(root['window']['start'], '2026-10-01')

    def test_carry_rewrites_the_root_for_the_new_subject_and_window(self):
        q = rf.carry(ASK, _history(), 'Delta Hour', rf.same_for_other(ASK)['window'])
        self.assertTrue(q.startswith("We're looking to size the potential U.S. audience and viewership specifically on YouTube"))
        self.assertIn('The titles and window is Delta Hour (11/1/26-11/9/26)', q)
        self.assertNotIn('Alpha Road (10/1/26-10/31/26)', q)
        self.assertIn('Same read as the one on Alpha Road, now for Delta Hour: the same platform (YouTube), the same metrics and definitions, the window Nov 1 2026 to Nov 9 2026.', q)
        self.assertIn('The subject of this read is Delta Hour on YouTube.', q)
        self.assertNotIn('\u2014', q)

    def test_carry_without_a_window_keeps_the_roots(self):
        q = rf.carry("same thing but for Delta Hour", _history(), 'Delta Hour', None)
        self.assertIn('Delta Hour (10/1/26-10/31/26)', q)
        self.assertIn('the window Oct 1 2026 to Oct 31 2026', q)

    def test_nothing_to_carry(self):
        self.assertEqual(rf.carry(ASK, [], 'Delta Hour', None), '')
        self.assertEqual(rf.carry(ASK, _history(), 'Alpha Road', None), '', 'the same subject as the root is not a carry')


class ThroughTheDoor(unittest.TestCase):
    def test_the_core_is_handed_the_carried_question(self):
        import replay_corpus as RC
        RC.install_isolation_light()
        case = {'id': 'f7', 'question': ASK, 'view': 'profileIQ', 'surface': 'analyze', 'subject': None, 'history': _history(),
                'reading': {"intent": "analyze", "confidence": 0.82, "subjects": [{"as_written": "Delta Hour", "catalog": "", "kind": "title"}],
                            "flags": ["precedent:Beta Lake", "custom_window"], "restate": "You want the same read for Delta Hour in its window."},
                'recorded': {}}
        stub = RC._Stub()
        stub.bind()
        live = RC._service_once(stub, case)
        self.assertEqual(live['bind'], 'Delta Hour', live)
        bodies = [b for n, b in stub.cores if n == 'analyze']
        self.assertTrue(bodies, stub.cores)
        # the reader's own words stay the ask (the lanes and the price saw these); the frame rides beside them
        self.assertEqual(bodies[-1].get('text'), ASK)
        frame = str(bodies[-1].get('frame_text') or '')
        self.assertIn('The subject of this read is Delta Hour on YouTube.', frame)
        self.assertIn('the same platform (YouTube)', frame)
        self.assertIn('Delta Hour (11/1/26-11/9/26)', frame)
        self.assertEqual(live['route'], 'page_analysis')

    def test_two_parts_name_two_subjects_and_both_carry_the_frame(self):
        # "Can we do this same analysis for Iron Orchard? And Night Market?": part two is Night Market, never Iron
        # Orchard twice; each part's core is handed the chain's root question rewritten for its own title
        import replay_corpus as RC
        RC.install_isolation_light()
        case = {'id': 'f7b', 'question': COMIC_ASK, 'view': 'profileIQ', 'surface': 'analyze', 'subject': None, 'history': _comic_history(),
                'reading': {"intent": "analyze", "confidence": 0.8, "subjects": [{"as_written": "Iron Orchard", "catalog": "", "kind": "title"},
                                                                                {"as_written": "Night Market", "catalog": "", "kind": "title"}],
                            "flags": ["precedent:Ember Vale", "multi"], "restate": "You want the same read for Iron Orchard and for Night Market."},
                'recorded': {}}
        stub = RC._Stub()
        stub.bind()
        live = RC._service_once(stub, case)
        reply = str(live.get('reply') or '')
        self.assertEqual(live['route'], 'multi_ask', live)
        self.assertIn('1) Can we do this same analysis for Iron Orchard?', reply)
        self.assertIn('2) And Night Market?', reply)
        bodies = [b for n, b in stub.cores if n == 'analyze']
        self.assertEqual(len(bodies), 2, stub.cores)
        self.assertEqual(bodies[0].get('bind_subject'), 'Iron Orchard', bodies[0])
        self.assertEqual(bodies[1].get('bind_subject'), 'Night Market', bodies[1])      # never Iron Orchard twice
        frames = [str(b.get('frame_text') or '') for b in bodies]
        self.assertIn('The subject of this read is Iron Orchard on Prime Video.', frames[0])
        self.assertIn('The subject of this read is Night Market on Prime Video.', frames[1])
        for fr in frames:
            # the chain's latest full question (the adaptation sign-up read), not its first, read past its "ok - so"
            self.assertTrue(fr.startswith('how many of those avid '), fr)
            self.assertIn('Prime Video in the trailing six months', fr)
            self.assertNotIn('Ember Vale readers', fr.split('\n\n')[0])

    def test_the_read_core_runs_on_the_frame_text(self):
        src = open(os.path.join(ROOT, 'prometheus', 'generated_read.py'), encoding='utf-8').read()
        self.assertIn("_fb.get('frame_text')", src)
        self.assertIn("_args = (job_id, _pm_user, _pm_read_extras, text_run,", src)
        self.assertIn("text=text_run, history=history, mr=mr, base=base,", src)
        self.assertIn("text=text_run, history=history, base=base,", src)
        sv = open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8').read()
        self.assertIn("'frame_text')", sv)


class FixturesCarryTheCase(unittest.TestCase):
    def test_the_live_shape_is_a_fixture(self):
        path = os.path.join(HERE, 'pm_corpus', 'fixtures', 'sanitized_cases.jsonl')
        ids = {}
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    d = json.loads(line)
                    ids[d['id']] = d
        self.assertIn('fx071', ids)
        self.assertIn('fx072', ids)
        self.assertEqual(ids['fx072']['question'], COMIC_ASK)
        self.assertEqual(ids['fx071']['question'], ASK)


if __name__ == '__main__':
    unittest.main(verbosity=2)
