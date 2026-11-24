"""A pronoun points back at the thread; a platform in "reading X and Y
now" is where, not a second subject; a question with no subject of its
own continues the thread's last read (Nov 23 2026 ask audit, fix 2).

Three misses from one afternoon:

* "Did his engagement go down in October and November?" right after a
  read on a creator: the Reader handed "his" back as a subject, the
  promise line read "On it: reading His now", and the next question,
  "Why has his engagement decreased?", drew "Do you mean for <creator>,
  or His?".
* "Which videos drove the April and September reach spikes?" right
  after the same creator read drew "Which subject should this be on?
  Name the show, brand or person".
* "what platforms is the audience from" with a skater's profile open,
  two turns after "On it: reading <skater> and YouTube now", drew
  "Which subject should this be on: YouTube or <skater>?".

The rules:

* `reader._normalize` drops a personal pronoun or a bare audience noun
  from the subjects and flags the reading `refers_to_thread`; the
  binder then takes the thread's own subject.
* `priced_read.looks_like_subject` reads a pronoun as no subject, a
  pronoun with no audience of its own named is a follow-up on the last
  read, and `offer_or_charge` continues the thread's last delivered read
  when the words name nothing of their own, asking only when there is
  nothing to continue.
* `subject_rules.thread_subjects` keeps the platform out of a
  two-part promise when the other part is not a platform, and never
  records a pronoun as a subject.

Pure tests, no network, no app.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from prometheus import priced_read as pr  # noqa: E402
from prometheus import reader as rd  # noqa: E402
from prometheus import subject_rules as sr  # noqa: E402

_READ = ("On Example Creator (examplecreator on TikTok) - US general population reach (followers and non-followers):\n\n"
         "General US reach for examplecreator went from 1,203,418 people in December 2025 to a peak of 2,891,204 in "
         "September 2026, then 1,744,331 in October.")


def _creator_history():
    return [
        {"role": "user", "ts": "2026-11-23T23:37:00Z", "text": "I want to understand how the general US pop engages with him over time, not just his audience"},
        {"role": "agent", "ts": "2026-11-23T23:37:21Z", "text": "On it: reading him (Example Creator, examplecreator on TikTok) now. This one takes a real look at the data, so give me a moment - the read will land right here when it is ready."},
        {"role": "agent", "ts": "2026-11-23T23:42:06Z", "text": _READ, "meta": {"kind": "read", "read_job_id": "jobaaaa111111", "subject": "Example Creator (examplecreator on TikTok)"}},
    ]


def _skater_history():
    return [
        {"role": "user", "ts": "2026-11-23T17:53:08Z", "text": "Example Skater profile, for the US audience how much is it coming from YouTube"},
        {"role": "agent", "ts": "2026-11-23T17:53:30Z", "text": "On it: reading Example Skater and YouTube now. This one takes a real look at the data, so give me a moment - the read will land right here when it is ready."},
        {"role": "agent", "ts": "2026-11-23T17:56:20Z", "text": "YouTube carries 2,667,703 of the 2,851,821 US people in the Example Skater audience, 93.5% of it, at index 114 vs the average American.", "meta": {"kind": "read", "read_job_id": "jobcccc333333", "subject": "Example Skater"}},
        {"role": "user", "ts": "2026-11-23T17:58:03Z", "text": "what is the full breakdown of 2,851,821 audience. Where is the audience from also"},
        {"role": "agent", "ts": "2026-11-23T17:58:06Z", "text": "2,851,821 is the US projection of the Example Skater profile in Profile IQ, an audience read for Nov 17 2025 to Nov 17 2026."},
    ]


class ReaderDropsPointers(unittest.TestCase):
    def _norm(self, subjects, text):
        return rd._normalize({'intent': 'analyze', 'confidence': 0.84, 'subjects': subjects, 'flags': ['typo']}, text, [])

    def test_a_pronoun_is_not_a_subject(self):
        for w in ('his', 'His', 'her', 'him', 'they', 'them', 'their', 'the audience', 'the same'):
            r = self._norm([{'as_written': w, 'catalog': '', 'kind': 'person'}], f"did {w} engagement go down")
            self.assertEqual(r['subjects'], [], w)
            self.assertIn('refers_to_thread', r['flags'], w)
            self.assertIn('typo', r['flags'], w)

    def test_a_name_still_rides(self):
        r = self._norm([{'as_written': 'Example Creator', 'catalog': '', 'kind': 'person'}], "did Example Creator's engagement go down")
        self.assertEqual([s['as_written'] for s in r['subjects']], ['Example Creator'])
        self.assertNotIn('refers_to_thread', r['flags'])

    def test_a_catalog_match_is_never_dropped(self):
        cands = [{'subject': 'Her'}]       # a title that happens to be a pronoun, in the library
        r = rd._normalize({'intent': 'analyze', 'confidence': 0.8, 'subjects': [{'as_written': 'Her', 'catalog': 'Her', 'kind': 'title'}]},
                          "who watched Her", cands)
        self.assertEqual([s['catalog'] for s in r['subjects']], ['Her'])

    def test_the_binder_takes_the_thread_subject(self):
        raw = {'intent': 'analyze', 'confidence': 0.84, 'subjects': [{'as_written': 'his', 'catalog': '', 'kind': 'person'}], 'flags': []}
        q = "Did his engagement go down in Octobe and November?"
        reading = rd._normalize(json.loads(json.dumps(raw)), q, [])
        s = sr.bind(q, _creator_history(), '', reading)
        self.assertIsNotNone(s)
        self.assertEqual(s.name, 'Example Creator (examplecreator on TikTok)')
        self.assertNotEqual(s.display.lower(), 'his')


class NoSubjectContinuesTheRead(unittest.TestCase):
    def test_pronouns_are_not_subjects(self):
        for s in ('His', 'his', 'her', 'them', 'their'):
            self.assertFalse(pr.looks_like_subject(s), s)
        for s in ('Example Creator (examplecreator on TikTok)', 'YouTube', 'Example Skater'):
            self.assertTrue(pr.looks_like_subject(s), s)

    def test_a_pronoun_ask_is_a_followup_on_the_last_read(self):
        h = _creator_history()
        self.assertTrue(pr.is_followup_on_last_read("Did his engagement go down in October and November?", h, 'His'))
        self.assertTrue(pr.is_followup_on_last_read("Why has her reach fallen?", h, ''))
        self.assertFalse(pr.is_followup_on_last_read("how many people watch Example Brand", h, 'Example Brand'))
        # a pronoun whose audience is named in the same sentence is a new ask, not a follow-up
        self.assertFalse(pr.is_followup_on_last_read("For people who bought Example Game in the first 48 hours, how did their streaming hours change?",
                                                     h, 'people who bought Example Game in the first 48 hours'))

    def test_thread_read_subject_reads_the_last_read(self):
        self.assertEqual(pr.thread_read_subject(_creator_history()), 'Example Creator (examplecreator on TikTok)')

    def test_no_subject_of_its_own_never_asks_when_a_read_is_in_the_thread(self):
        # the gate's own clarify path: a question fragment as the subject, a read in the thread
        kind, payload = pr.offer_or_charge(text="Which videos drove the April and September reach spikes?",
                                           subject="Which videos drove the April", kind='sizing', panel_confirm=None,
                                           username='fx', user={'username': 'fx'}, history=_creator_history(), host=None)
        reply = str((payload or {}).get('reply') or '')
        self.assertNotIn('Which subject should this be on', reply, (kind, reply))
        self.assertFalse((payload or {}).get('subject_unclear'), (kind, reply))

    def test_nothing_to_continue_still_asks(self):
        kind, payload = pr.offer_or_charge(text="Which videos drove the April and September reach spikes?",
                                           subject="Which videos drove the April", kind='sizing', panel_confirm=None,
                                           username='fx', user={'username': 'fx'}, history=[], host=None)
        self.assertEqual(kind, 'offer')
        self.assertTrue(payload.get('subject_unclear'))


class PlatformsAndPronounsInThreadSubjects(unittest.TestCase):
    def test_a_platform_beside_a_subject_is_where(self):
        h = _skater_history()
        self.assertEqual(sr.thread_subjects(h), ['Example Skater'])
        self.assertEqual(sr.thread_subjects(h, 'Example Skater'), [])
        self.assertIsNone(sr.open_screen_question("what platforms is the audience from", 'Example Skater', h))

    def test_a_platform_alone_is_the_subject(self):
        h = [{"role": "user", "text": "how big is the YouTube audience in the US"},
             {"role": "agent", "text": "On it: reading YouTube now. The read will land right here."}]
        self.assertEqual(sr.thread_subjects(h), ['YouTube'])

    def test_two_real_subjects_both_ride(self):
        h = [{"role": "user", "text": "compare Example Brand and Northwind Brand"},
             {"role": "agent", "text": "On it: reading Example Brand and Northwind Brand now. The read will land right here."}]
        self.assertEqual(sr.thread_subjects(h), ['Example Brand', 'Northwind Brand'])

    def test_a_pronoun_promise_names_nobody(self):
        h = _creator_history() + [
            {"role": "user", "text": "Did his engagement go down in October and November?"},
            {"role": "agent", "text": "Included with your plan. On it: reading His now. This one takes a real look at the data."},
        ]
        subs = sr.thread_subjects(h)
        self.assertEqual(subs, ['Example Creator (examplecreator on TikTok)'])
        self.assertNotIn('His', subs)


class FixturesCarryTheCases(unittest.TestCase):
    def test_the_live_shapes_are_fixtures(self):
        path = os.path.join(HERE, 'pm_corpus', 'fixtures', 'sanitized_cases.jsonl')
        ids = {}
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    d = json.loads(line)
                    ids[d['id']] = d
        for fid in ('fx062', 'fx063', 'fx064'):
            self.assertIn(fid, ids)
            self.assertNotEqual(ids[fid]['recorded']['route'], 'open_screen_confirm', fid)


if __name__ == '__main__':
    unittest.main(verbosity=2)
