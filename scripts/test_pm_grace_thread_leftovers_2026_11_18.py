#!/usr/bin/env python3
"""The rest of Grace Yin's thread of 2026-11-18 (Amazon, thread
9f460c9c19), after the follow-up fix (test_pm_followup_window_refinement).

Four things in one thread, each a rule now:

  a. Her first message named a film and the service it is on. It was
     quoted on the service ("I can pull that read on Amazon Prime
     Video"): the Reader listed the film (not in the library) and the
     service (in the library), and the one library subject was handed
     over as the subject. A service the words name as where a title is
     watched is the scope; the title is the subject; with no title
     anyone can place, the reader is asked which title
     (prometheus.title_on_service).
  b. "wjat does Avid tier: ~3,837 mean", with a brief on her screen,
     drew a third brief. A question about a term or a line on the
     thread's own brief is answered from that brief in the product's
     own words; nothing is drafted, quoted or started
     (prometheus.brief_terms).
  c. Three briefs for one title, one service and one window showed
     three audience sizes. The first size drafted for a subject, its
     services and a window is held and carried by every later draft; a
     narrower draft is never larger (prometheus.brief_size_hold).
  d. Her build was refused ("We can't see Tony (2026) on Amazon at this
     time") although the film's own Amazon page had been found: the
     spec carried no kind, so the page was opened as a series and
     answered nothing. A film's spec says it is a film
     (prometheus.named_platforms.stamp_spec).

Hermetic: no model, no live S3 (the size holds run on an in-memory
store), no mail, no app boot. The door checks replay her words through
the recording host in a child process with the model canned, and are
skipped with a note when that host is not on the path.
"""
from __future__ import annotations

import datetime as dt
import io
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

from prometheus import brief_size_hold as HS  # noqa: E402
from prometheus import brief_terms as BT  # noqa: E402
from prometheus import named_platforms as NP  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus import reader as RD  # noqa: E402
from prometheus import title_on_service as TS  # noqa: E402
from migration import service_scope_sizing as SSS  # noqa: E402

FIRST = ('for customers who watched TONY,2026 coming-of-age dark comedy docudrama film Tony, starring Dominic Sessa as a '
         '19-year-old Anthony Bourdain as he stumbles into a chaotic Provincetown restaurant kitchen, is streaming on '
         'Amazon Prime Video (released on October 26, 2026). can you give me the demo breakdown of the audience>')
BRIEF_1 = ('New Tony Viewers on Amazon Prime Video profile, ready to run.\n\nSubject: Tony (2026), the coming-of-age dark comedy '
           'docudrama directed by Matt Johnson, starring Dominic Sessa as a young Anthony Bourdain, streaming on Amazon Prime '
           'Video; viewers on Amazon Prime Video only\nWindow: October 26, 2026 to November 18, 2026\nEstimated audience: about '
           '1,310,264 to 1,803,596 US people (0.470% incidence)\nAvid tier: ~14,574\nCost: Included with your plan\nBuild time: '
           '~20 min\n\nApprove to run, or send another message to adjust.')
NO_AVID = ("you don't need the Avid tier: ~14,574 for this pull, i just want the demo by age, gender for people who watched the "
           "title on prime video")
BRIEF_2 = ('New Tony 2026 film Viewers on Amazon Prime Video profile, ready to run.\n\nSubject: Tony is a 2026 A24 biographical '
           'coming-of-age film about a young Anthony Bourdain, directed by Matt Johnson and starring Dominic Sessa and Antonio '
           'Banderas; viewers on Amazon Prime Video only\nWindow: October 26, 2026 to November 18, 2026\nEstimated audience: '
           'about 493,695 to 670,093 US people (0.174% incidence)\nAvid tier: ~3,837\nCost: Included with your plan\nBuild time: '
           '~20 min\n\nApprove to run, or send another message to adjust.')
HERS = 'wjat does Avid tier: ~3,837 mean'


def thread():
    """Her thread as it stood at 01:24 UTC: the first brief, her note
    about the avid tier, the second brief."""
    return [{'role': 'user', 'text': 'Tony directed by Matt Johnson about a young Anthony Bourdain.'},
            {'role': 'agent', 'text': BRIEF_1},
            {'role': 'user', 'text': NO_AVID},
            {'role': 'agent', 'text': BRIEF_2}]


def reading(*subjects, **kw):
    return dict({'intent': 'analyze', 'confidence': 0.86, 'flags': [],
                 'subjects': [dict(zip(('as_written', 'catalog', 'kind'), s)) for s in subjects]}, **kw)


SERVICE = ('Amazon Prime Video', 'Amazon Prime Video', 'platform')
# what the Reader returned on the day: the film (not in the library) and the service (in it)
READING = reading(('TONY', '', 'title'), SERVICE)


def gate(text, rd=None):
    """The work gate as the door calls it: the Reader's steer first (it
    may hand over a bound subject), then the gate."""
    body = {'reading': rd} if rd else {}
    st = RD.steer(rd, {'surface': 'analyze', 'reason': 'question'}, text=text) if rd else None
    if st and st[1]:
        body['bind_subject'] = st[1]
    return PR.work_gate(text, [], None, {'username': 'probe'}, body, username='probe', consume=None), body


# ============================================================================ a
class TheTitleIsTheSubject(unittest.TestCase):
    def test_her_first_message_is_quoted_on_the_film_with_its_scope(self):
        for rd in (READING, reading(SERVICE), reading(('TONY', '', 'title'), platform='Amazon Prime Video'), None):
            payload, body = gate(FIRST, rd)
            self.assertIsInstance(payload, dict)
            self.assertEqual(payload.get('subject'), 'Tony viewers on Amazon Prime Video')
            self.assertIn('I can pull that read on Tony viewers on Amazon Prime Video.', payload['reply'])
            self.assertNotIn('read on Amazon Prime Video.', payload['reply'])
            self.assertNotEqual(body.get('bind_subject'), 'Amazon Prime Video')

    def test_the_service_is_never_handed_over_as_the_subject(self):
        self.assertEqual(RD._single_library_subject(READING, FIRST), '')
        self.assertIsNone(RD.steer(READING, {'surface': 'analyze', 'reason': 'question'}, text=FIRST))
        for q, rd in (('how many people watched Severance on Apple TV+', reading(('Severance', '', 'title'), ('Apple TV+', 'Apple TV+', 'platform'))),
                      ('demo breakdown of the audience that watched Fallout on Prime Video', reading(('Fallout', '', 'title'), SERVICE)),
                      ('who watched the film Tony on Prime Video', reading(SERVICE)),
                      ('age and gender of the people streaming The Bear on Hulu', reading(('Hulu', 'Hulu', 'platform')))):
            self.assertEqual(RD._single_library_subject(rd, q), '', q)

    def test_a_question_about_the_service_itself_is_still_the_services(self):
        for q in ('what do Amazon Prime Video viewers watch', 'demographics of the Amazon Prime Video audience',
                  'what films do Prime Video viewers watch most', 'total hours by month for 2025 for Prime Video'):
            self.assertEqual(RD._single_library_subject(reading(SERVICE), q), 'Amazon Prime Video', q)
        self.assertEqual(RD._single_library_subject(reading(('Netflix', 'Netflix', 'platform')), 'who uses Netflix'), 'Netflix')
        self.assertEqual(RD._single_library_subject(reading(('Paramount+', 'Paramount+', 'platform')), 'show me the age breakdown for Paramount+'),
                         'Paramount+')

    def test_a_library_subject_that_is_not_a_service_is_handed_over_as_before(self):
        rd = reading(('Paw Patrol', 'Paw Patrol', 'title'))
        self.assertEqual(RD._single_library_subject(rd, 'what toys do Paw Patrol viewers buy'), 'Paw Patrol')
        self.assertFalse(TS.service_is_scope('Paw Patrol', rd, 'what toys do Paw Patrol viewers buy'))

    def test_the_label_carries_one_service_and_the_title_as_typed(self):
        self.assertEqual(TS.scoped_label('TONY', 'viewers', FIRST), 'Tony viewers on Amazon Prime Video')
        self.assertEqual(TS.scoped_label('Fallout', 'viewers', 'who watched Fallout on Prime Video', 'Prime Video'), 'Fallout viewers on Prime Video')
        self.assertEqual(TS.scoped_label('Landman', 'viewers', 'what did Landman viewers watch next'), 'Landman viewers')
        # two services named: no single scope on the label
        self.assertEqual(TS.scoped_label('Wuthering Heights', 'viewers', 'people who watched Wuthering Heights on Hulu or Netflix'),
                         'Wuthering Heights viewers')
        self.assertEqual(PR.bound_subject('how many people watched Severance on Apple TV+', [], None), 'Severance viewers on Apple TV+')

    def test_one_work_the_words_do_not_name_is_asked_about(self):
        q = 'demo breakdown of the audience for the new Bourdain film that is streaming on Amazon Prime Video'
        self.assertTrue(TS.describes_a_title(q))
        ask = TS.unnamed_title(q, [], None, reading(SERVICE))
        self.assertEqual(ask['reply'], 'Which title do you mean? Give me its name and I will read its viewers on Amazon Prime Video, '
                                       'not everyone who uses Amazon Prime Video.')
        self.assertTrue(ask['audience_confirm'])                       # no price, nothing started, the door's own which-one shape
        self.assertEqual(ask['followups'], [])
        self.assertNotIn('panel_offer', ask)
        # a viewing metric of the film they did not name is never read on the whole service either
        payload, _body = gate('how many hours did people spend watching the new Bourdain film on Amazon Prime Video last month', reading(SERVICE))
        if payload is not None:
            self.assertNotIn('panel_offer', payload)
            self.assertIn('Which title do you mean?', payload['reply'])

    def test_a_title_the_words_or_the_reader_name_is_never_asked_about(self):
        self.assertIsNone(TS.unnamed_title(FIRST, [], None, READING))
        self.assertIsNone(TS.unnamed_title(FIRST, [], None, reading(SERVICE)))            # the words name it by rule
        self.assertIsNone(TS.unnamed_title('the new film on Prime Video', [], None, reading(('Tony', '', 'title'), SERVICE)))
        for q in ('what do Amazon Prime Video viewers watch', 'who uses Netflix', 'what films do Prime Video viewers watch most',
                  'total hours by month for 2025 for Prime Video', 'run a profile on Severance'):
            self.assertIsNone(TS.unnamed_title(q, [], None, reading(SERVICE)), q)

    def test_the_page_or_the_thread_answers_for_a_pointer(self):
        ctx = {'primary': {'name': 'Hazbin Hotel Season 1', 's3_key': 'Hazbin.csv'}}
        self.assertIsNone(TS.unnamed_title('how does this show do on Prime Video', [], ctx, reading(SERVICE)))


# ============================================================================ b
class ATermOnTheBriefIsAnswered(unittest.TestCase):
    def test_her_question(self):
        a = BT.answer(HERS, thread())
        self.assertEqual((a['term'], a['number']), ('avid', '3837'))
        self.assertEqual(a['reply'],
                         'The Avid tier is the people who watched it four or more times, on Amazon Prime Video. '
                         'The ~3,837 on your brief is how many of those people are in our data for Tony 2026 film Viewers on '
                         'Amazon Prime Video, October 26, 2026 to November 18, 2026. The audience line above it (493,695 to '
                         '670,093 US people) is the full audience as a national count; the Avid tier sits inside it, not on top '
                         'of it.\n\nYour brief is unchanged and nothing has started.')
        self.assertEqual(a['followups'], [])

    def test_the_wording_is_the_products_own(self):
        from prometheus import platform_cuts as PC
        src = open(PC.__file__, encoding='utf-8').read()
        self.assertIn('The Avid tier is the people who watched it four or more times', src)      # the approved viewers line
        a = BT.answer('what is the avid tier', [{'role': 'agent', 'text': BRIEF_2.replace(
            'Tony 2026 film Viewers on Amazon Prime Video', 'Nike').replace('; viewers on Amazon Prime Video only', '')}])
        from prometheus import kpi_definitions as KD
        glossary = next(d['definition'] for d in KD.DEFINITIONS if d['id'] == 'avid_fan')
        self.assertTrue(a['reply'].startswith(glossary))               # a brand's brief reads the glossary's own entry

    def test_typos_and_other_ways_to_ask(self):
        for q in ('what is the avid tier', 'what does ~3,837 refer to', 'waht does avid tier mean', 'whta is avid tier?',
                  'wat does the Avid tier mean', 'define avid tier', 'avid tier means?', 'not sure what avid tier means',
                  "what's the avid tier on the brief", 'and what does avid tier: ~3,837 stand for'):
            a = BT.answer(q, thread())
            self.assertIsNotNone(a, q)
            self.assertEqual(a['term'], 'avid', q)
            self.assertTrue(a['reply'].startswith('The Avid tier is the people who watched it four or more times'), q)

    def test_the_number_she_quotes_picks_the_brief_it_is_on(self):
        a = BT.answer('what does the ~14,574 mean', thread())
        self.assertIn('The ~14,574 on your brief', a['reply'])
        self.assertIn('Tony Viewers on Amazon Prime Video', a['reply'])
        self.assertIn('1,310,264 to 1,803,596 US people', a['reply'])
        self.assertNotIn('3,837', a['reply'])

    def test_the_other_lines_of_the_brief(self):
        for q, key, has in (('what does 0.174% incidence mean', 'incidence', 'The 0.174% on your brief'),
                            ("what's incidence", 'incidence', 'Incidence is the share of all US people who are in this audience.'),
                            ('what does estimated audience mean', 'audience', 'about 493,695 to 670,093 US people'),
                            ('what is Total Universe', 'total_universe', 'The Total Universe is everyone who watched it on Amazon Prime Video in the window.'),
                            ('waht does included with your plan mean', 'cost', 'Approving it adds no charge.'),
                            ('whats the build time', 'build_time', '~20 min'),
                            ('what does the window mean', 'window', 'October 26, 2026 to November 18, 2026')):
            a = BT.answer(q, thread())
            self.assertIsNotNone(a, q)
            self.assertEqual(a['term'], key, q)
            self.assertIn(has, a['reply'], q)
            self.assertTrue(a['reply'].endswith('Your brief is unchanged and nothing has started.'), q)

    def test_it_is_plain_and_says_nothing_about_how(self):
        for q in (HERS, 'what does 0.174% incidence mean', 'what does estimated audience mean', 'what is Total Universe',
                  'waht does included with your plan mean'):
            r = BT.answer(q, thread())['reply']
            self.assertNotIn('\u2014', r)
            self.assertNotIn('\u2013', r)
            for word in ('synth', 'model', 'estimat' if 'estimated audience' not in q else 'synth', 'pipeline', 'hostmap',
                         'content map', 'panel', 'sample', 'confidence', 'projected' if 'estimated audience' not in q else 'synth',
                         '$', 'credit'):
                self.assertNotIn(word, r.lower(), (q, word))

    def test_an_order_an_adjustment_or_another_question_is_not_this(self):
        for q in (NO_AVID, 'remove the avid tier', 'run it', 'approve', 'what does it cost to add an avid tier',
                  'what is the audience of Severance', 'what is Nike', 'what does this profile tell me', 'what is the demo breakdown',
                  'what is the avid tier for Nike', 'what does penetration mean', 'what is the index',
                  'build a profile on Severance viewers on Apple TV+', 'change the window to the last 90 days',
                  'what does the Avid tier look like by age and gender for this film'):
            self.assertIsNone(BT.answer(q, thread()), q)

    def test_with_no_brief_in_the_thread_the_other_lanes_keep_it(self):
        self.assertIsNone(BT.answer(HERS, []))
        self.assertIsNone(BT.answer(HERS, [{'role': 'user', 'text': FIRST}, {'role': 'agent', 'text': 'On it: reading Tony now.'}]))

    def test_the_build_intake_answers_instead_of_drafting(self):
        from prometheus import followup_refine as F
        hints = []
        fu, early = F.at_interpret(HERS, thread(), {}, lambda **kw: hints.append(kw))
        self.assertIsNone(fu)
        self.assertTrue(early['guidance'])
        self.assertTrue(early['error'].startswith('The Avid tier is the people who watched it four or more times'))
        self.assertIn({'route': 'brief_terms', 'outcome': 'answered', 'subject': 'Tony 2026 film Viewers on Amazon Prime Video'}, hints)
        # a build order with a brief in the thread is still a build order
        self.assertEqual(F.at_interpret('build a profile on Severance viewers on Apple TV+', thread(), {}, None), (None, None))


# ============================================================================ c
class FakeS3:
    """The store the holds live in, in memory (get / conditional put)."""

    def __init__(self):
        self.objects, self.puts = {}, 0

    def get_object(self, Bucket, Key, **_kw):
        from botocore.exceptions import ClientError
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(self.objects[Key]), 'ETag': '"%d"' % self.puts}

    def put_object(self, Bucket, Key, Body, **_kw):
        self.objects[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')
        self.puts += 1
        return {'ETag': '"%d"' % self.puts}

    def holds(self):
        return json.loads(self.objects[HS.STORE_KEY])['holds'] if HS.STORE_KEY in self.objects else []


def draft(subject, show, tu, whole, win=('2026-10-26', '2026-11-18'), named=('Amazon Prime Video',), decision='new_build', **kw):
    """A draft as the interpret model hands it over, sized by its own
    reading of the whole, then by the scoped rule when it names services."""
    d = {'subject': subject, 'name': subject, 'content_show': show, 'seed_source': 'content_map', 'audience_type': 'viewers',
         'brand_category': 'MOVIE', 'consumer_verb': 'viewers', 'named_platforms': list(named),
         'date_range': {'start': win[0], 'end': win[1]}, 'subject_raw_tu': tu, 'subject_raw_avid': int(tu * 0.31),
         'universe_anchor': whole, 'engaged_share': 0.9, 'decision': decision}
    m = re.search(r"\((\d{4})\)", show)
    d['resolved_identity'] = f"{show.split(' (')[0]} ({m.group(1) if m else '2026'}), the film; viewers on the named service only"
    d.update(kw)
    return SSS.apply_to_draft(d)


def spec_stage(d):
    """The size the spec carries on approval: the scoped rule again, on
    the draft as the brief showed it."""
    view = {'name': d['subject'], 'content_show': d['content_show'], 'seed_source': 'content_map', 'audience_type': 'viewers',
            'content_seed_platforms_named': d['named_platforms'],
            'date_range': f"{d['date_range']['start']} TO {d['date_range']['end']}", 'subject_raw_tu': d['subject_raw_tu'],
            'subject_raw_avid': d['subject_raw_avid'], 'universe_anchor': d['universe_anchor'], 'engaged_share': d['engaged_share']}
    SSS.apply_to_spec(view, view['name'], stage='spec', log=None)
    return view['subject_raw_tu']


NOW = dt.datetime(2026, 11, 18, 1, 12, tzinfo=dt.timezone.utc)


class TheFirstSizeHeldStands(unittest.TestCase):
    def her_three(self):
        # her three briefs: one film, one service, one window, three readings of the whole
        return (draft('Tony Viewers on Amazon Prime Video', 'Tony', 120000, 4200000),
                draft('Tony 2026 film Viewers on Amazon Prime Video', 'Tony (2026)', 50000, 1650000),
                draft('Tony (2026 film) Viewers on Amazon Prime Video', 'Tony (2026)', 60000, 1980000))

    def test_without_the_hold_her_three_briefs_were_three_sizes(self):
        self.assertEqual(len({d['subject_raw_tu'] for d in self.her_three()}), 3)

    def test_her_three_briefs_show_one_size(self):
        s3 = FakeS3()
        a, b, c = self.her_three()
        first = a['subject_raw_tu']
        for i, d in enumerate((a, b, c)):
            HS.settle(d, s3=s3, now=NOW + dt.timedelta(minutes=11 * i), who='graceyin', thread='9f460c9c19')
        self.assertEqual([a['subject_raw_tu'], b['subject_raw_tu'], c['subject_raw_tu']], [first] * 3)
        self.assertEqual(len({a['subject_raw_avid'], b['subject_raw_avid'], c['subject_raw_avid']}), 1)      # the Avid tier too
        self.assertFalse(a.get('sample_size_held'))
        self.assertTrue(b.get('sample_size_held') and c.get('sample_size_held'))
        self.assertEqual(len(s3.holds()), 1)                                                               # one hold, the first
        self.assertEqual(s3.holds()[0]['tu'], first)
        self.assertEqual((s3.holds()[0]['by'], s3.holds()[0]['thread']), ('graceyin', '9f460c9c19'))

    def test_the_size_she_approves_is_the_size_the_spec_carries(self):
        s3 = FakeS3()
        a, b, c = self.her_three()
        for d in (a, b, c):
            HS.settle(d, s3=s3, now=NOW)
            # the scoped rule, run again on approval, lands on the held size (to within a few people: the house salt)
            self.assertLessEqual(abs(spec_stage(d) - a['subject_raw_tu']), 25, d['subject'])

    def test_another_thread_another_reader_the_same_size(self):
        s3 = FakeS3()
        a = draft('Tony Viewers on Amazon Prime Video', 'Tony', 120000, 4200000)
        HS.settle(a, s3=s3, now=NOW, who='graceyin', thread='9f460c9c19')
        later = draft('Tony (2026) Viewers on Amazon Prime Video', 'Tony (2026)', 81000, 2700000, win=('2026-10-26', '2026-11-20'))
        HS.settle(later, s3=s3, now=NOW + dt.timedelta(days=2), who='someone_else', thread='another')
        self.assertEqual(later['subject_raw_tu'], a['subject_raw_tu'])           # two days on, the window two days longer: one window
        self.assertEqual(len(s3.holds()), 1)

    def test_a_narrower_window_is_never_larger(self):
        s3 = FakeS3()
        a = draft('Tony Viewers on Amazon Prime Video', 'Tony', 120000, 4200000)
        HS.settle(a, s3=s3, now=NOW)
        narrow = draft('Tony (2026) Viewers on Amazon Prime Video', 'Tony (2026)', 400000, 13000000, win=('2026-11-01', '2026-11-10'))
        self.assertGreater(narrow['subject_raw_tu'], a['subject_raw_tu'])        # drafted larger than the whole window's brief
        HS.settle(narrow, s3=s3, now=NOW)
        self.assertLessEqual(narrow['subject_raw_tu'], a['subject_raw_tu'])
        self.assertLess(narrow['subject_raw_avid'], narrow['subject_raw_tu'])
        smaller = draft('Tony (2026) Viewers on Amazon Prime Video', 'Tony (2026)', 30000, 990000, win=('2026-11-03', '2026-11-09'))
        before = smaller['subject_raw_tu']
        HS.settle(smaller, s3=s3, now=NOW)
        self.assertEqual(smaller['subject_raw_tu'], before)                      # already smaller: its own figure stands, and is held

    def test_a_narrower_scope_is_never_larger(self):
        s3 = FakeS3()
        whole = draft('Tony (2026) Viewers', 'Tony (2026)', 150000, 4950000, named=())
        HS.settle(whole, s3=s3, now=NOW)                                         # every service
        one = draft('Tony (2026) Viewers on Amazon Prime Video', 'Tony (2026)', 500000, 16500000)
        HS.settle(one, s3=s3, now=NOW)                                           # one service of it, drafted larger
        self.assertLessEqual(one['subject_raw_tu'], whole['subject_raw_tu'])

    def test_a_different_subject_service_or_window_is_its_own(self):
        s3 = FakeS3()
        a = draft('Tony Viewers on Amazon Prime Video', 'Tony', 120000, 4200000)
        HS.settle(a, s3=s3, now=NOW)
        for d in (draft('Tony Bennett Viewers on Amazon Prime Video', 'Tony Bennett', 70000, 2500000),          # another subject
                  draft('Tony (2026) Viewers on Netflix', 'Tony (2026)', 70000, 2500000, named=('Netflix',)),     # another service
                  draft('Tony (2026) Viewers on Amazon Prime Video', 'Tony (2026)', 70000, 2500000, win=('2025-11-18', '2026-11-18')),   # a longer window
                  draft('Tony (2009) Viewers on Amazon Prime Video', 'Tony (2009)', 9000, 330000)):               # another film of the name
            own = d['subject_raw_tu']
            HS.settle(d, s3=s3, now=NOW)
            self.assertEqual(d['subject_raw_tu'], own, d['subject'])
            self.assertFalse(d.get('sample_size_held'), d['subject'])
        self.assertEqual(len(s3.holds()), 5)

    def test_a_brand_or_a_person_holds_the_same_way(self):
        s3 = FakeS3()

        def nike(tu, whole):
            return {'subject': 'Nike', 'name': 'Nike', 'brand_category': 'ACTIVEWEAR', 'decision': 'new_build',
                    'date_range': {'start': '2025-11-18', 'end': '2026-11-18'}, 'subject_raw_tu': tu, 'subject_raw_avid': int(tu * 0.27),
                    'universe_anchor': whole, 'engaged_share': 0.41, 'anchor_source': 'US people who shopped Nike online in the window'}
        a, b = nike(1256321, 101000000), nike(873911, 70300000)
        HS.settle(a, s3=s3, now=NOW)
        HS.settle(b, s3=s3, now=NOW + dt.timedelta(hours=3))
        self.assertEqual((b['subject_raw_tu'], b['subject_raw_avid'], b['universe_anchor'], b['engaged_share']),
                         (1256321, int(1256321 * 0.27), 101000000, 0.41))        # the whole it was read against comes with it

    def test_it_lasts_ninety_days(self):
        s3 = FakeS3()
        a = draft('Tony Viewers on Amazon Prime Video', 'Tony', 120000, 4200000)
        HS.settle(a, s3=s3, now=NOW)
        b = draft('Tony (2026) Viewers on Amazon Prime Video', 'Tony (2026)', 50000, 1650000)
        HS.settle(b, s3=s3, now=NOW + dt.timedelta(days=89))
        self.assertEqual(b['subject_raw_tu'], a['subject_raw_tu'])
        c = draft('Tony (2026) Viewers on Amazon Prime Video', 'Tony (2026)', 50000, 1650000)
        own = c['subject_raw_tu']
        HS.settle(c, s3=s3, now=NOW + dt.timedelta(days=91))
        self.assertEqual(c['subject_raw_tu'], own)                                # lapsed: this draft is the new first
        self.assertEqual(HS.HOLD_DAYS, 90)
        self.assertEqual((HS.STORE_BUCKET, HS.STORE_KEY), ('dashboard-inputs', 'system/pm_brief_size_holds.json'))

    def test_what_is_already_fixed_is_left_alone(self):
        s3 = FakeS3()
        a = draft('Tony Viewers on Amazon Prime Video', 'Tony', 120000, 4200000)
        HS.settle(a, s3=s3, now=NOW)
        locked = draft('Tony (2026) Viewers on Amazon Prime Video', 'Tony (2026)', 50000, 1650000, sample_size_locked=True)
        own = locked['subject_raw_tu']
        HS.settle(locked, s3=s3, now=NOW)
        self.assertEqual(locked['subject_raw_tu'], own)                           # a size locked from a sample check
        shipped = draft('Tony (2026) Viewers on Amazon Prime Video', 'Tony (2026)', 50000, 1650000)
        shipped['service_scope_sizing'] = {'basis': 'anchor', 'notes': ['settled amazon build 1,550,827 x window 1.0000']}
        own = shipped['subject_raw_tu']
        HS.settle(shipped, s3=s3, now=NOW)
        self.assertEqual(shipped['subject_raw_tu'], own)                          # a build that shipped is the standing figure
        for dec in ('existing_match', 'derive_cut', 'time_shifted_refresh', 'subscriber_iq'):
            d = draft('Tony (2026) Viewers on Amazon Prime Video', 'Tony (2026)', 50000, 1650000, decision=dec)
            own = d['subject_raw_tu']
            HS.settle(d, s3=s3, now=NOW)
            self.assertEqual(d['subject_raw_tu'], own, dec)

    def test_a_test_run_and_a_probe_never_touch_the_live_store(self):
        d = draft('Tony Viewers on Amazon Prime Video', 'Tony', 120000, 4200000)
        own = d['subject_raw_tu']
        self.assertIs(HS.settle(d), d)                                            # no store handed in: nothing read, nothing written
        self.assertEqual(d['subject_raw_tu'], own)
        self.assertEqual(HS._load(), [])
        self.assertFalse(HS._save({'base': 'x'}))
        src = open(HS.__file__, encoding='utf-8').read()
        self.assertIn('_is_probe()', src[src.index('def _save'):src.index('def _int')])
        for banned in ('request.', 'body.get(', 'get_json', 'locked_sample'):
            self.assertFalse(banned in src, banned)                               # server side: no field of the request is read

    def test_names_that_differ_only_by_year_kind_or_tail_are_one_subject(self):
        keys = {HS.subject_key(d)[0] for d in (
            {'subject': 'Tony Viewers on Amazon Prime Video', 'content_show': 'Tony'},
            {'subject': 'Tony 2026 film Viewers on Amazon Prime Video', 'content_show': 'Tony (2026)'},
            {'subject': 'Tony (2026 film) Viewers on Amazon Prime Video', 'content_show': 'Tony (2026)'},
            {'subject': 'Tony (2026 Film) Viewers on Amazon Prime Video'})}
        self.assertEqual(keys, {'tony'})
        self.assertNotEqual(HS.subject_key({'subject': 'Tony Bennett Viewers'})[0], 'tony')
        self.assertEqual(HS.subject_key({'subject': 'The Office (US) Viewers on Peacock', 'content_show': 'The Office (US)'})[0], 'office us')
        self.assertEqual(HS.scope_of({'subject': 'Tony Viewers on Amazon Prime Video', 'named_platforms': ['Amazon Prime Video']}), ['amazon'])


# ============================================================================ d
class AFilmIsToldAsAFilm(unittest.TestCase):
    def spec(self):
        return {'name': 'Tony (2026 Film) Viewers on Amazon Prime Video', 'content_show': 'Tony (2026)', 'seed_source': 'content_map',
                'brand_category': 'MOVIE', 'content_seed_platforms_named': ['Amazon Prime Video']}

    def test_her_spec_now_says_it_is_a_film(self):
        # the spec of run ay1i2xrrxKirZg carried brand_category MOVIE and no kind
        spec = NP.stamp_spec(self.spec(), {'brand_category': 'MOVIE'}, lambda v, **k: str(v), 'Tony (2026 Film) Viewers on Amazon Prime Video')
        self.assertEqual(spec['medium'], 'movie')
        for d in ({'medium': 'movie'}, {'medium': 'Film'}, {'viewer_scope': {'kind': 'standalone_film'}}):
            s = dict(self.spec(), brand_category='SERIES')
            self.assertEqual(NP.stamp_spec(s, d, lambda v, **k: str(v), 'x').get('medium'), 'movie', d)

    def test_a_kind_the_reader_named_wins_and_a_series_is_not_a_film(self):
        s = NP.stamp_spec(self.spec(), {'content_kind': 'special', 'brand_category': 'MOVIE'}, lambda v, **k: str(v), 'x')
        self.assertEqual(s['medium'], 'special')
        s = NP.stamp_spec(dict(self.spec(), brand_category='SERIES - DRAMA'), {'brand_category': 'SERIES - DRAMA'}, lambda v, **k: str(v), 'x')
        self.assertNotIn('medium', s)
        s = NP.stamp_spec({'name': 'Nike', 'brand_category': 'MOVIE'}, {'brand_category': 'MOVIE'}, lambda v, **k: str(v), 'Nike')
        self.assertNotIn('medium', s)                                              # not a consumption universe: untouched


# ============================================================================ wiring
def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


class Wiring(unittest.TestCase):
    def test_the_lane_is_in_the_order_and_never_priced(self):
        from prometheus import service as S
        self.assertEqual(tuple(S.lane_markers()), S.LANE_ORDER)
        i = S.LANE_ORDER.index('brief_terms')
        self.assertLess(i, S.LANE_ORDER.index('product_facts'))
        self.assertLess(i, S.LANE_ORDER.index('reader'))                # before any model reads the turn
        self.assertLess(i, S.LANE_ORDER.index('surface_decision'))      # before the build flow can take it
        self.assertIn('brief_terms', S.DETERMINISTIC_LANES)
        self.assertIn('brief_terms', S.NEVER_PRICED_LANES)

    def test_a_brief_on_screen_does_not_hold_the_lane_back(self):
        from prometheus import service as S
        self.assertFalse(S._in_a_wizard({'draft': {'subject': 'x'}, 'step': 'identity'}))     # the build flow's own state
        self.assertTrue(S._in_a_wizard({'extra': {'bpiq_inputs': {'a': 1}}}))
        self.assertTrue(S._in_a_wizard({'panel_confirm': {'subject': 'x'}}))
        src = _read('prometheus', 'service.py')
        lane = src[src.index('# lane: brief_terms'):src.index('# lane: product_facts')]
        self.assertIn('not _in_a_wizard(body)', lane)
        self.assertNotIn('not _armed(body)', lane)
        self.assertIn("route='brief_terms', outcome='answered'", lane)

    def test_the_which_title_question_sits_at_the_work_gate(self):
        src = _read('prometheus', 'service.py')
        lane = src[src.index('# lane: work_gate'):]
        self.assertLess(lane.index('_tos.unnamed_title(text, history, ctx,'), lane.index('_prw.work_gate(text, history, ctx, user, body'))

    def test_the_size_is_settled_where_the_brief_reads_it(self):
        chat = _read('prometheus', 'legacy', 'chat.py')
        at = chat.index("__import__('prometheus.brief_size_hold', fromlist=['ranged']).ranged(_H._estimated_audience_range, spec_draft,")
        rule = chat.index("fromlist=['apply_to_draft']).apply_to_draft(spec_draft).get('subject_raw_tu'))")
        self.assertLess(at, rule)                                       # the scoped rule sizes the draft, then the hold settles it
        self.assertLess(rule, chat.index("spec_draft['estimated_audience_low'] = _est_rng['low']"))
        seen = []
        d = {'decision': 'existing_match', 'subject_raw_tu': 20727}
        self.assertEqual(HS.ranged(lambda tu: seen.append(tu) or {'low': 1, 'high': 2}, d, 20727), {'low': 1, 'high': 2})
        self.assertEqual(seen, [20727])                                 # the band is read from the size that stands
        self.assertLess(len(chat.splitlines()), 19900)                  # the line ratchet holds

    def test_no_dashes_and_no_internal_words_in_the_copy(self):
        for copy in (TS.WHICH_TITLE, TS.which_title('x')['reply'], BT._CLOSE):
            self.assertNotIn('\u2014', copy)
            self.assertNotIn('\u2013', copy)
            for word in ('synth', 'model', 'estimat', 'pipeline', 'hostmap', 'content map', 'confidence', 'panel'):
                self.assertNotIn(word, copy.lower())


# ============================================================================ the door
_CHILD = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from prometheus import replay
replay.install_isolation()
h = replay.harness()
h.host.set('_neutralize_data_delimiters', lambda s, *a, **k: str(s or ''))
out = []
for it in json.load(sys.stdin):
    replay._restore_baseline(h)
    replay._STATE['question'] = it['question']
    replay._canned_dual(h, it.get('draft_subject') or '', None)
    door = replay._STATE.get('door') or '/api/brief-chat/interpret'
    body = {'text': it['question'], 'history': it.get('history') or [], 'page_context': {},
            'client': 'dashboard', 'persist': False, 'surface': it.get('surface') or 'interpret'}
    body.update(it.get('body') or {})
    n0 = len(replay._STATE['captured'])
    status, payload = h.post(door, body, session={'username': replay.REPLAY_USER['username']})
    rec = (replay._STATE['captured'][n0:] or [{}])[-1]
    raw = payload.get('raw') if isinstance(payload, dict) and isinstance(payload.get('raw'), dict) else payload
    raw = raw if isinstance(raw, dict) else {}
    out.append({'route': rec.get('route'), 'outcome': rec.get('outcome'), 'subject': rec.get('subject'),
                'reply': replay._reply_text(raw) or str((payload or {}).get('text') or ''),
                'has_draft': isinstance(raw.get('spec_draft'), dict), 'has_offer': bool(raw.get('panel_offer')),
                'jobs': [j for j in [raw.get('read_job_id'), raw.get('run_id')] if j]})
print('@@@' + json.dumps(out))
'''


def _door(items):
    env = dict(os.environ, REGRESSION_TEST_MODE='1')
    try:
        p = subprocess.run([sys.executable, '-c', _CHILD, ROOT], input=json.dumps(items), capture_output=True, text=True,
                           env=env, timeout=600)
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
    """Her words through the door as the dashboard sent them. The model
    is canned to draft a brief for anything that reaches the build
    intake, so a brief in the reply is the defect showing."""

    @classmethod
    def setUpClass(cls):
        cls.items = [
            {'question': HERS, 'draft_subject': 'Tony (2026 film) Viewers on Amazon Prime Video', 'history': thread()},
            {'question': HERS, 'draft_subject': 'Tony (2026 film) Viewers on Amazon Prime Video', 'history': thread(),
             'body': {'draft': {'subject': 'Tony 2026 film Viewers on Amazon Prime Video', 'decision': 'new_build'}}},
            {'question': 'what is the avid tier', 'draft_subject': 'Tony', 'history': thread(), 'surface': 'analyze'},
            {'question': FIRST, 'draft_subject': '', 'history': [], 'surface': 'analyze'},
            {'question': 'build a profile on Severance viewers on Apple TV+', 'draft_subject': 'Severance Viewers on Apple TV+',
             'history': thread()},
        ]
        cls.res = _door(cls.items)

    def setUp(self):
        if not self.res:
            self.skipTest('the recording host (scripts/_pm_behavior.py in the parent repo) is not on the path here')

    def test_her_question_is_answered_and_nothing_is_drafted(self):
        for r in self.res[:3]:
            self.assertEqual((r['route'], r['outcome']), ('brief_terms', 'answered'))
            self.assertTrue(r['reply'].startswith('The Avid tier is the people who watched it four or more times, on Amazon Prime Video.'))
            self.assertFalse(r['has_draft'])
            self.assertFalse(r['has_offer'])
            self.assertEqual(r['jobs'], [])
            self.assertNotIn('ready to run', r['reply'])
            self.assertNotIn('$', r['reply'])

    def test_her_first_message_names_the_film_and_its_scope(self):
        r = self.res[3]
        self.assertIn('Tony viewers on Amazon Prime Video', r['reply'])
        self.assertNotIn('read on Amazon Prime Video.', r['reply'])
        self.assertEqual(r['subject'], 'Tony viewers on Amazon Prime Video')

    def test_a_build_order_in_her_thread_is_still_a_brief(self):
        r = self.res[4]
        self.assertEqual((r['route'], r['outcome']), ('profile_build', 'new_build'))
        self.assertTrue(r['has_draft'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
