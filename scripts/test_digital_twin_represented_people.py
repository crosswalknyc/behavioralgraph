#!/usr/bin/env python3
"""The people a room stands for when the audience was cut (Jenna, 2026-11-05).

"somewhere we need to show a total of what the audience in the focus group
or panel represents if the audience has been cut like in the ncis pilot. it
wouldn't project up to that full sample size because that sample size is
cumulative of all people not just the cuts I made ... so the room would
need to be sized accordingly. and if it's a show like ncis new york it could
even externally research on average how many people watch it to help figure
out where the size should land"

Covered:
  * a four-cut room stands for the union of its cuts, not the twin
  * cuts nested in other cuts are not double counted; disjoint cuts add up
  * no cuts = the twin
  * an aired title caps the figure at its audience times the cuts' share
    (the title's skews move the share), at the bottom third of the range
  * a title that has not aired keeps the cut figure; the research is
    cached per twin, title and day
  * every surface prints the represented figure: the pilot state, the
    room state, the seat bios, the headline people, the forecast, the
    report page, the notice bodies, the partner payload, the CSV
  * nothing rendered names a source or says calibrated

Hermetic: REGRESSION_TEST_MODE, a fake research model, no S3.

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_represented_people.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest

os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ.setdefault('CORPUS_CATALOG', '0')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from migration import digital_twin_panel as P          # noqa: E402
from migration import digital_twin_pilot as PI         # noqa: E402
from migration import digital_twin_sizing as SZ        # noqa: E402
from migration import digital_twin_forecast as F       # noqa: E402
from migration import digital_twin_room as ROOM        # noqa: E402
from migration import digital_twin_report as R         # noqa: E402
from migration.digital_twin_copy import calibration_hits   # noqa: E402
from migration.digital_twin_engine import human_people     # noqa: E402

TWIN_PEOPLE = 83_791_763
SOURCE_WORDS = re.compile(r"nielsen|samba|luminate|parrot|calibrat|anchor|benchmark|as aired|research|published|source|estimat|model", re.I)


def _twin():
    demo = {
        'gender': [{'label': 'Male', 'pct': 48.9}, {'label': 'Female', 'pct': 49.6}, {'label': 'Non-Binary', 'pct': 1.5}],
        'age': [{'label': '18-24', 'pct': 22.1}, {'label': '25-34', 'pct': 27.8}, {'label': '35-44', 'pct': 18.6}, {'label': '45-54', 'pct': 12.5},
                {'label': '55-64', 'pct': 10.8}, {'label': '65 or Older', 'pct': 8.2}],
        'ethnicity': [{'label': 'White', 'pct': 55.6}, {'label': 'Hispanic or Latino', 'pct': 18.0}, {'label': 'Black or African American', 'pct': 14.4},
                      {'label': 'Asian', 'pct': 9.5}, {'label': 'Another Race/Ethnicity', 'pct': 2.5}],
        'parental': [{'label': 'No Children', 'pct': 61.1}, {'label': 'Has Children', 'pct': 37.6}, {'label': 'Prefer Not to Say', 'pct': 1.3}],
        'income': [{'label': 'Less than $25,000', 'pct': 15.2}, {'label': '$25,000 - $49,999', 'pct': 21.0}, {'label': '$50,000 - $74,999', 'pct': 18.9},
                   {'label': '$75,000 - $99,999', 'pct': 14.4}, {'label': '$100,000 - $149,999', 'pct': 17.6}, {'label': '$150,000 - $249,999', 'pct': 10.4},
                   {'label': '$250,000 or More', 'pct': 2.5}],
        'education': [{'label': 'High School or Less', 'pct': 30.9}, {'label': 'Some College / Associate Degree', 'pct': 28.1},
                      {'label': 'Bachelors Degree', 'pct': 26.3}, {'label': 'Graduate or Professional Degree', 'pct': 14.7}],
    }
    return {'meta': {'key': 'digital-twins/t/paramount.json.gz', 'name': 'Paramount+', 'subject': 'Paramount+', 'size_us': TWIN_PEOPLE, 'platform_home': 'Paramount+'},
            'identity': {'demographics': demo, 'geography': [{'label': 'New York NY', 'pct': 13.2}]},
            'lifecycle': {'lapsing_pct': 18.0}, 'traits': {'franchise_loyalty': {'value': 55}, 'churn_propensity': {'value': 48}}}


OLD = ['45-54', '55-64', '65 or Older']
# the NCIS: New York room: four cuts, the Hispanic ones inside the gender ones
COHORTS = [{'name': 'Men 45 and older', 'demos': {'GENDER': ['Male'], 'AGE': OLD}, 'seats': 4},
           {'name': 'Women', 'demos': {'GENDER': ['Female'], 'AGE': OLD}, 'seats': 4},
           {'name': 'Hispanic Females 45 and older', 'demos': {'GENDER': ['Female'], 'AGE': OLD, 'ETHNICITY': ['Hispanic or Latino']}, 'seats': 4},
           {'name': 'Hispanic Men 45 and older', 'demos': {'GENDER': ['Male'], 'AGE': OLD, 'ETHNICITY': ['Hispanic or Latino']}, 'seats': 4}]

RESEARCH = {'released': True, 'title': 'NCIS: New York', 'released_on': '2026-10-06',
            'audience': {'low': 8_430_000, 'high': 10_250_000, 'typical': 9_340_000, 'measure': 'first-week audience per episode, every screen', 'window': 'first seven days per episode',
                         'as_of': '2026-10-20', 'source_class': 'Nielsen'},
            'platform_people': {'value': 84_000_000, 'measure': 'US people on Paramount+', 'as_of': '2026-09-30'},
            'skews': {'female_pct': 56.0, 'age_45_plus_pct': 68.0, 'age_under_35_pct': 11.0, 'hispanic_pct': 12.0, 'black_pct': None, 'median_age': 61},
            'note': 'Premiered October 6, 2026 on CBS and Paramount+.'}
NOT_AIRED = {'released': False, 'title': 'Second Helping', 'released_on': None, 'audience': {'low': None, 'high': None, 'typical': None, 'measure': '', 'window': ''},
             'platform_people': {'value': None}, 'skews': {}, 'note': 'Not yet released.'}

CALLS = []


def _fake(answer):
    def fn(role, system, user, **kw):
        CALLS.append({'role': role, 'user': json.loads(user) if user.startswith('{') else user})
        return dict(answer)
    return fn


def _segments(twin):
    return PI.cohort_segments(COHORTS, twin['meta']['key'])


def _pilot(twin, *, research=None, title='NCIS: New York Pilot', sid='ncis-pilot-1'):
    SZ.clear_cache()
    return PI.open_session([twin], segments=_segments(twin), in_room=16, title=title, session_id=sid, mode='pilot',
                           media={'title': title, 'runtime_s': 2591.0}, model_fn=(_fake(research) if research else None), research=bool(research))


class TheCuts(unittest.TestCase):
    def test_four_cuts_stand_for_their_union_not_the_twin(self):
        twin = _twin()
        segs = _segments(twin)
        g = P.build_group([twin], segments=segs, in_room=16, seed='t|ncis')
        men = next(s for s in g['segments'] if s['label'] == 'Men 45 and older')
        women = next(s for s in g['segments'] if s['label'] == 'Women')
        hisp = [s for s in g['segments'] if s['label'].startswith('Hispanic')]
        # the cut each segment holds in the twin stays on it; the room stands for the union of the four
        self.assertAlmostEqual(men['twin_people'], TWIN_PEOPLE * 0.489 * 0.315, delta=TWIN_PEOPLE * 0.002)
        self.assertLess(g['size_people'], TWIN_PEOPLE, 'never the whole twin')
        self.assertLess(g['size_people'], sum(s['twin_people'] for s in g['segments']), 'never the cuts added up')
        union = men['twin_people'] + women['twin_people']
        self.assertAlmostEqual(g['size_people'], union, delta=union * 0.002, msg='the Hispanic cuts sit inside the gender cuts: counted once')
        self.assertTrue(all(h['twin_people'] < men['twin_people'] for h in hisp))
        self.assertEqual(sum(s['people'] for s in g['segments']), g['size_people'], 'the segments partition what the room stands for')
        self.assertEqual(g['sizing']['people'], g['size_people'])
        self.assertEqual(g['sizing']['twin_people'], TWIN_PEOPLE)
        self.assertEqual(g['twin_people'], TWIN_PEOPLE)
        self.assertEqual(g['sizing']['cuts_n'], 4)
        self.assertTrue(g['sizing']['is_cut'])
        self.assertAlmostEqual(g['sizing']['share_of_twin'], g['size_people'] / TWIN_PEOPLE, places=5)
        self.assertNotEqual(g['size_people'] % 10, 0)
        self.assertEqual(g['sizing']['line'], f"Standing for the 4 groups you chose: {SZ.people_short(g['size_people'])} of the 83.8M people in this audience.")
        self.assertEqual(PI.represented_people(twin, segs), g['sizing']['footprint'])

    def test_overlapping_cuts_are_not_double_counted_and_disjoint_cuts_add_up(self):
        twin = _twin()
        key = twin['meta']['key']
        men = {'label': 'Men', 'twin': key, 'filter': {'gender': 'M'}}
        dads = {'label': 'Dads', 'twin': key, 'filter': {'gender': 'M', 'parent': True}}
        women = {'label': 'Women', 'twin': key, 'filter': {'gender': 'F'}}
        nested = PI.represented_sizing(twin, [men, dads], seed='t')
        men_only = PI.represented_sizing(twin, [men], seed='t')
        self.assertEqual(nested['people'], men_only['people'], 'dads sit inside men: the union is the men')
        self.assertEqual(nested['cuts_n'], 2)
        both = PI.represented_sizing(twin, [{**men, 'people': 40_974_173}, {**women, 'people': 41_560_715}], seed='t')
        self.assertEqual(both['people'], 40_974_173 + 41_560_715, 'cuts that cannot overlap add up exactly')
        self.assertTrue(PI.cuts_disjoint([PI.cut_of(men['filter']), PI.cut_of(women['filter'])]))
        self.assertFalse(PI.cuts_disjoint([PI.cut_of(men['filter']), PI.cut_of(dads['filter'])]))
        self.assertIsNone(PI.intersect_cuts(PI.cut_of({'gender': 'M'}), PI.cut_of({'gender': 'F'})))
        inter = PI.intersect_cuts(PI.cut_of({'gender': 'F', 'age': (45, 80)}), PI.cut_of({'demos': {'ETHNICITY': ['Hispanic or Latino']}}))
        self.assertEqual({k: sorted(v) for k, v in inter['demos'].items()}, {'GENDER': ['Female'], 'AGE': OLD, 'ETHNICITY': ['Hispanic or Latino']})
        # a room on two twins: each twin's cuts are a union inside that twin, then the twins add
        other = {'meta': {'key': 'digital-twins/t/other.json.gz', 'name': 'Other', 'subject': 'Other', 'size_us': 10_000_003}, 'identity': {'demographics': twin['identity']['demographics']}}
        two = PI.represented_sizing([twin, other], [men, dads, {'label': 'Other women', 'twin': other['meta']['key'], 'filter': {'gender': 'F'}}], seed='t')
        self.assertEqual(two['people'], two['by_twin'][key]['people'] + two['by_twin'][other['meta']['key']]['people'])
        self.assertEqual(two['twin_people'], TWIN_PEOPLE + 10_000_003)

    def test_no_cuts_is_the_twin(self):
        twin = _twin()
        g = P.build_group([twin], segments=None, in_room=12, seed='t|plain')
        self.assertEqual(g['size_people'], TWIN_PEOPLE)
        self.assertFalse(g['sizing']['is_cut'])
        self.assertEqual(g['sizing']['cuts_n'], 0)
        self.assertEqual(g['sizing']['line'], '', 'no line for a room on the whole audience')
        sess = PI.open_session([twin], title='Plain', in_room=12, session_id='plain-1')
        self.assertEqual(sess['group']['size_people'], TWIN_PEOPLE)
        self.assertEqual(PI.session_people(sess, twin), TWIN_PEOPLE)
        self.assertEqual(PI.represented_people(twin, [{'label': 'Paramount+', 'twin': twin['meta']['key'], 'filter': {}}]), TWIN_PEOPLE)


class TheTitle(unittest.TestCase):
    def test_an_aired_title_caps_the_room_at_its_audience_times_the_cuts_share(self):
        twin = _twin()
        CALLS.clear()
        sess = _pilot(twin, research=RESEARCH)
        g = sess['group']
        sz = g['sizing']
        self.assertEqual(sz['step'], 'title')
        self.assertEqual(sz['basis']['step'], 'title')
        footprint = sz['footprint']
        share = SZ.share_of_title([twin], g['segments'], SZ._normalize_research(RESEARCH, 'NCIS: New York'))
        # the twin's 45-and-older men and women are 22.6% of the twin; the title's audience is 68% 45 and older, so the
        # cuts' share of the title is about that 68% (the title's skews move the share), not the 22.6%
        self.assertGreater(share, 0.6)
        self.assertLess(share, 0.75)
        self.assertAlmostEqual(share, sz['basis']['share_of_title'], places=5)
        low_third = 8_430_000 + (10_250_000 - 8_430_000) / 6.0        # the bottom third of the researched range
        self.assertEqual(sz['basis']['title']['audience_low'], int(round(low_third)))
        want = low_third * share
        self.assertAlmostEqual(sz['people'], want, delta=want * 0.01)
        self.assertLess(sz['people'], footprint, 'the title holds fewer of these people than the cuts hold on the platform')
        self.assertEqual(g['size_people'], sz['people'])
        self.assertNotEqual(sz['people'] % 10, 0)
        self.assertEqual(sum(s['people'] for s in g['segments']), sz['people'])
        self.assertEqual(sum(c['people'] for c in sess['cohorts']), sz['people'])
        self.assertEqual(sz['line'], f"Standing for the 4 groups you chose: {SZ.people_short(sz['people'])} of the 83.8M people in this audience.")
        # one research call, on the research desk, about the title; cached for the day
        self.assertEqual([c['role'] for c in CALLS], ['research'])
        self.assertEqual(CALLS[0]['user']['title'], 'NCIS: New York')
        again = SZ.title_audience(twin, 'NCIS: New York', model_fn=_fake(RESEARCH))
        self.assertEqual(len(CALLS), 1, 'cached per twin, title and day')
        self.assertEqual(again['audience_low'], sz['basis']['title']['audience_low'])

    def test_a_whole_room_on_an_aired_title_stands_for_the_titles_audience(self):
        twin = _twin()
        SZ.clear_cache()
        sess = PI.open_session([twin], title='NCIS: New York Pilot', in_room=16, session_id='ncis-whole', mode='pilot', media={'title': 'NCIS: New York Pilot'},
                               model_fn=_fake(RESEARCH), research=True)
        sz = sess['group']['sizing']
        self.assertEqual(sz['step'], 'title')
        self.assertAlmostEqual(sz['basis']['share_of_title'], 1.0, places=5)
        self.assertAlmostEqual(sz['people'], 8_430_000 + (10_250_000 - 8_430_000) / 6.0, delta=90_000)
        self.assertEqual(sz['line'], f"Standing for: {SZ.people_short(sz['people'])} of the 83.8M people in this audience.")

    def test_a_title_that_has_not_aired_keeps_the_cut_figure(self):
        twin = _twin()
        CALLS.clear()
        sess = _pilot(twin, research=NOT_AIRED, title='Second Helping pilot', sid='sh-1')
        sz = sess['group']['sizing']
        self.assertEqual(sz['step'], 'cuts')
        self.assertEqual(sz['people'], sz['footprint'])
        self.assertIs(sz['basis']['title']['released'], False)
        self.assertNotIn('share_of_title', sz['basis'])
        self.assertEqual(len(CALLS), 1)
        # a trailer is not a title that can have aired; a room with the model off keeps step 1 without asking
        CALLS.clear()
        SZ.clear_cache()
        tr = PI.open_session([twin], segments=_segments(twin), in_room=16, title='Spot', session_id='tr-1', mode='trailer', model_fn=_fake(RESEARCH), research=True)
        self.assertEqual(tr['group']['sizing']['step'], 'cuts')
        self.assertEqual(CALLS, [])
        off = _pilot(twin, research=None, sid='off-1')
        self.assertEqual(off['group']['sizing']['step'], 'cuts')
        self.assertEqual(off['group']['size_people'], off['group']['sizing']['footprint'])

    def test_a_title_with_no_profile_borrows_its_franchises(self):
        twin = _twin()
        SZ.clear_cache()
        CALLS.clear()
        bare = dict(RESEARCH, skews={'female_pct': None, 'age_45_plus_pct': None, 'age_under_35_pct': None, 'hispanic_pct': None, 'black_pct': None, 'median_age': None})
        franchise = {'basis': 'the franchise NCIS', 'female_pct': 57.0, 'age_45_plus_pct': None, 'age_under_35_pct': None, 'hispanic_pct': None, 'black_pct': None, 'median_age': 61, 'note': 'NCIS median age 61.'}

        def fn(role, system, user, **kw):
            CALLS.append({'role': role, 'system': system[:40]})
            return dict(franchise if system.startswith('You look up the published audience profile') else bare)
        res = SZ.title_audience(twin, 'NCIS: New York', model_fn=fn)
        self.assertEqual(len(CALLS), 2, 'the title pass, then one narrow look-up for the franchise')
        self.assertEqual(res['skews']['female_pct'], 57.0)
        self.assertEqual(res['skews']['age_45_plus_pct'], 66.5, 'a median age of 61 puts about two thirds past 45, read low')
        self.assertEqual(res['skews_basis'], 'the franchise NCIS')
        again = SZ.title_audience(twin, 'NCIS: New York', model_fn=fn)
        self.assertEqual(len(CALLS), 2, 'cached with the borrowed profile')
        self.assertEqual(again['skews'], res['skews'])

    def test_skew_index_moves_a_cut_by_the_titles_published_shares(self):
        twin = _twin()
        res = SZ._normalize_research(RESEARCH, 'NCIS: New York')
        old = PI.cut_of({'demos': {'AGE': OLD}})
        self.assertAlmostEqual(SZ.skew_index(twin, old, res['skews']), 0.68 / 0.315, places=2)
        women = PI.cut_of({'gender': 'F'})
        self.assertAlmostEqual(SZ.skew_index(twin, women, res['skews']), 0.56 / 0.496, places=2)
        mid = PI.cut_of({'demos': {'AGE': ['35-44']}})
        self.assertAlmostEqual(SZ.skew_index(twin, mid, res['skews']), 0.21 / 0.186, places=2)
        partial = PI.cut_of({'demos': {'AGE': ['45-54']}})
        self.assertEqual(SZ.skew_index(twin, partial, res['skews']), 1.0, 'a bucket the research did not publish moves nothing')
        self.assertEqual(SZ.skew_index(twin, women, {}), 1.0)


class TheSurfaces(unittest.TestCase):
    def setUp(self):
        self.twin = _twin()
        self.sess = _pilot(self.twin, research=RESEARCH, sid='ncis-surf')
        self.people = self.sess['group']['size_people']
        self.line = self.sess['group']['sizing']['line']

    def _scored(self):
        PI.prepare_watch(self.sess, {self.twin['meta']['key']: self.twin}, duration_s=2591, title='NCIS: New York Pilot')
        return self.sess

    def test_pilot_state_prints_the_represented_figure_and_never_the_basis(self):
        pub = PI.public_session(self.sess)
        self.assertEqual(pub['group']['size_people'], self.people)
        self.assertEqual(pub['group']['twin_people'], TWIN_PEOPLE)
        self.assertEqual(pub['group']['sizing']['people'], self.people)
        self.assertEqual(pub['group']['sizing']['line'], self.line)
        self.assertNotIn('basis', pub['group']['sizing'])
        self.assertNotIn('step', pub['group']['sizing'])
        self.assertIn('basis', self.sess['group']['sizing'], 'the stored record keeps its basis')
        blob = json.dumps(pub)
        self.assertNotIn('Nielsen', blob)
        self.assertNotIn('audience_low', blob)
        self.assertEqual(PI.session_people(self.sess, self.twin), self.people)

    def test_bios_divide_the_represented_total_by_segment(self):
        g = self.sess['group']
        self.assertEqual(sum(r['people'] for r in g['roster']), self.people)
        self.assertAlmostEqual(sum(r['stands_for_people'] for r in g['roster']), self.people, delta=60)
        for r in g['roster']:
            self.assertAlmostEqual(r['stands_for_share'], r['stands_for_people'] / self.people * 100.0, delta=0.11)
        seg = next(s for s in g['segments'] if s['label'] == 'Men 45 and older')
        self.assertEqual(sum(r['people'] for r in g['roster'] if r['segment'] == 'Men 45 and older'), seg['people'])

    def test_room_state_headline_people_are_the_share_of_the_represented_total(self):
        room = P.open_session([self.twin], segments=_segments(self.twin), in_room=16, title='Room', session_id='room-1')
        g = room['group']
        self.assertLess(g['size_people'], TWIN_PEOPLE)
        self.assertEqual(sum(s['people'] for s in g['segments']), g['size_people'], 'what ask_room hands headline_for as the people the room stands for')
        self.assertEqual(g['sizing']['step'], 'cuts')
        self.assertTrue(g['sizing']['line'].startswith('Standing for the 4 groups you chose: '))
        total = g['size_people']
        opts = [{'id': 'c', 'label': 'Yes', 'lean': 'yes', 'pct': 5.4, 'people': int(round(total * 0.054)) | 1, 'seats': [1], 'seats_n': 1},
                {'id': 'a', 'label': 'No', 'lean': 'no', 'pct': 94.6, 'people': total - (int(round(total * 0.054)) | 1), 'seats': list(range(2, 17)), 'seats_n': 15}]
        text, people = ROOM.headline_for(opts, total, 16, 'Paramount+')
        self.assertEqual(people, opts[0]['people'])
        self.assertIn(human_people(total), text)
        self.assertNotIn(human_people(TWIN_PEOPLE), text)
        self.assertEqual(P.session_lines(room)[0], f"Room: {room['group']['in_room']} twins in the room standing for {total:,} people.")

    def test_forecast_projects_from_the_represented_audience(self):
        sess = self._scored()
        ledger = {'records': [{'show': 'NCIS: New York', 'show_key': 'ncis_new_york', 'episode': 1, 'session_id': 'x', 'platform': 'Paramount+', 'actual': 10_250_000, 'predicted_uncalibrated': 3_300_587, 'aired': '2026-10-06', 'measure': 'week-one'}]}
        unc = F.uncalibrated_week_one(self.twin, sess)
        self.assertEqual(unc['base_people'], self.people)
        self.assertEqual(unc['twin_people'], TWIN_PEOPLE)
        self.assertAlmostEqual(unc['people'], unc['whole'] * unc['title_share'], places=3)
        self.assertAlmostEqual(unc['title_share'], sess['group']['sizing']['basis']['share_of_title'], places=5)
        fc = F.forecast_for_session(self.twin, sess, ledger=ledger)
        self.assertEqual(fc['base_people'], self.people)
        whole = F.forecast_for_session(self.twin, dict(sess, group=dict(sess['group'], sizing=None)), ledger=ledger)
        self.assertEqual(whole['base_people'], TWIN_PEOPLE)
        self.assertLess(fc['episode']['week_one_audience'], whole['episode']['week_one_audience'], 'the cut room forecasts its own people, never the twin total')
        self.assertAlmostEqual(fc['episode']['week_one_audience'], whole['episode']['week_one_audience'] * unc['title_share'], delta=whole['episode']['week_one_audience'] * 0.01)
        self.assertLess(fc['subscriptions']['nonsubscribers_people'], whole['subscriptions']['nonsubscribers_people'])
        self.assertLess(fc['subscriptions']['dormant_people'], whole['subscriptions']['dormant_people'])
        # the ledger keeps the whole-twin prediction, so a show's calibration carries between a cut room and a whole room
        led = F.record_actual(None, self.twin, sess, people=10_250_000, measure='week-one', episode=1, ledger={'records': []}, save=False)
        self.assertEqual(led['records'][0]['predicted_uncalibrated'], int(round(unc['whole'])))
        pub = F.public_forecast(fc)
        self.assertNotIn('calibration', pub)
        self.assertEqual(calibration_hits(json.dumps(pub)), [])

    def test_report_page_csv_notice_and_partner_payload_print_the_represented_figure(self):
        sess = self._scored()
        pages = R.report_pages({'meta': self.twin['meta']}, sess, with_chart=False)
        room = next(p for p in pages if p['kind'] == 'roster')
        self.assertEqual(room['header'], f"The room seated 16 people, standing for {human_people(self.people)}.")
        self.assertIn(self.line, room['sub'])
        self.assertNotIn('drawn from an audience', room['sub'])
        self.assertNotIn(human_people(TWIN_PEOPLE), room['header'])
        tiles = next(p for p in pages if p['kind'] == 'tiles')
        self.assertIn(f"{human_people(self.people)} people", tiles['sub'])
        for p in pages:
            text = json.dumps(p)
            self.assertEqual(calibration_hits(text), [], p['kind'])
            self.assertFalse(SOURCE_WORDS.search(room['sub']), room['sub'])
        csv = PI.session_csv(sess, {'meta': self.twin['meta']})
        self.assertIn(str(self.people), csv.splitlines()[1])
        self.assertNotIn(str(TWIN_PEOPLE), csv.splitlines()[1])
        quads = sess['scorecard']['battery']['quadrants']
        self.assertEqual(next(q['people'] for q in quads if q['key'] == 'total'), self.people)
        self.assertEqual(sess['scorecard']['people'], self.people)
        self.assertEqual(sum(q['people'] for q in quads if q.get('group') == 'cohort'), self.people)
        from prometheus import twin_notify as TN
        st = {'name': 'Jenna Menking', 'email': 'jenna@crosswalknyc.com', 'company': 'Crosswalk'}
        body = TN._pilot_body({'meta': self.twin['meta']}, sess, st, 'dashboard')
        self.assertIn(f"Seats: 16, standing for {self.people:,} people", body)
        self.assertIn(self.line, body)
        self.assertNotIn(f"{TWIN_PEOPLE:,}", body.split(self.line)[0])
        self.assertEqual(calibration_hits(body), [])
        self.assertFalse(SOURCE_WORDS.search(self.line))
        room_sess = P.open_session([self.twin], segments=_segments(self.twin), in_room=16, title='Room', session_id='room-2')
        rbody = TN._room_body({'meta': self.twin['meta']}, room_sess, st, 'dashboard')
        self.assertIn(f"Seats: 16, standing for {room_sess['group']['size_people']:,} people", rbody)
        self.assertIn(room_sess['group']['sizing']['line'], rbody)
        import digital_twins_partner_api as PA
        out = PA._result(sess, 'Paramount+')
        self.assertEqual(out['people'], self.people)
        self.assertEqual(out['seats'], 16)

    def test_ensure_sizing_sizes_a_record_from_before_and_leaves_a_sized_one_alone(self):
        twin = self.twin
        old = PI.open_session([twin], segments=_segments(twin), in_room=16, title='Old', session_id='old-1', research=False)
        g = old['group']
        g.pop('sizing', None)
        g.pop('twin_people', None)
        for s in g['segments']:
            s['people'] = s.pop('twin_people')         # the record as it was: each cut's people in the twin, the twin as the total
        g['size_people'] = TWIN_PEOPLE
        self.assertTrue(SZ.ensure_sizing(twin, old))
        self.assertLess(g['size_people'], TWIN_PEOPLE)
        self.assertEqual(g['sizing']['step'], 'cuts')
        self.assertEqual(sum(s['people'] for s in g['segments']), g['size_people'])
        self.assertFalse(SZ.ensure_sizing(twin, old), 'a sized record is left alone')


if __name__ == '__main__':
    unittest.main(verbosity=1)
