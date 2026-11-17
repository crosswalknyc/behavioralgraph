#!/usr/bin/env python3
"""The kinds of people that make up a twin, and the room that seats one
person per kind (Jenna 2026-11-17).

Jenna, verbatim: "instead of splitting it into 12, or say 16 people
you're forced into it should split them based on archetypes within the
overall cohort that comprise it. so if the sample size for the overall
cohort is 1m it would then say what comprises this 1m in terms of
archetypes? there are 14 different overall groups of fish (archetypes)
that make up the 1m. 200k are archetype x so that'll be Harold, 54 from
NYC with that persona. 354k are y."

Held here:
  * the skeleton's cells reproduce the twin's own lifecycle and age
    margins exactly; the count lands between MIN_K and MAX_K; shares sum
    to 100.0; people are messy and sum to the twin; deterministic;
  * the desk's answer is applied within bounds (copy scrubbed, dial
    shifts capped, share nudges re-fitted to the margins, merges and
    splits honored) and never changes the audience's size;
  * a cohort filter conditions the kinds by their own profiles and the
    kinds' people sum to the cohort;
  * the roster: one seat per kind, each seat's people its kind's, the
    room's gender / orientation / age labels a people-weighted quota of
    the kinds (nine women of twelve for a 72% female audience; who is
    LGBTQ+ read inside each gender from its own kinds, never one gender
    filled first), names unique, ages inside the label;
  * a kind is who the desk says it is: a kind of gay men seats a gay
    man, a kind of trans women seats a trans woman and the bio says so;
    the other kinds are re-fitted so the audience's totals hold, and a
    kind can never hold more of a row than the audience has;
  * build_group seats by kinds by default, meets an explicit count by
    merging or splitting, falls back to quota seating on request or when
    DTIQ_ARCHETYPE_SEATS=0, and partitions a cohort room by kinds;
  * weigh: the largest kind's vote moves the count more than the
    smallest's; the published split still sums to 100 and the people to
    the room; every option carries the people its seats stand for;
  * the weighted mirror: the people behind the seats on each option land
    within one seat of the count, and the floor seats a side worth half
    the smallest seat;
  * the persona card and bio carry the kind (its label, its people, the
    desk's read) and pass the house scrub;
  * build_twin attaches the kinds; the twin page lines and the summary
    line read plainly; requested_seats is 0 unless set by hand.

Hermetic: REGRESSION_TEST_MODE never calls a model; a fake desk stands
in where one is needed. No S3.
    python3 scripts/test_digital_twin_archetypes.py
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import unittest
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_archetypes as A  # noqa: E402
from migration import digital_twin_panel as P  # noqa: E402
from migration import digital_twin_room as ROOM  # noqa: E402
from migration import digital_twins as DT  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_room_seats import _beals  # noqa: E402
from test_digital_twin_room_seats_mirror_count import _pvalley, fake_model, LABELS  # noqa: E402

BANNED = re.compile(r"\b(synthetic|synth|modell?ed|model|estimated|research|panel|sample|archetype|persona|segment|cohort|confidence|pipeline|corpus)\b", re.I)


def _twin(size=1_000_000, avid=24.3):
    t = _beals()
    t['meta']['size_us'] = size
    t['meta']['avid_share_pct'] = avid
    t['identity']['demographics']['age'] = [{'label': '18-24', 'pct': 9.4}, {'label': '25-34', 'pct': 24.1}, {'label': '35-44', 'pct': 26.3},
                                            {'label': '45-54', 'pct': 19.8}, {'label': '55-64', 'pct': 12.6}, {'label': '65+', 'pct': 7.8}]
    t['lifecycle'] = {'discovering_pct': 14.2, 'trying_pct': 13.1, 'habit_pct': 44.6, 'lapsing_pct': 16.4, 'returning_pct': 11.7}
    t['traits'] = {k: {'value': v} for k, v in {'discovery_social_vs_search': 58, 'binge_vs_appointment': 61, 'price_sensitivity': 49, 'churn_propensity': 42,
                                                 'franchise_loyalty': 66, 'merch_propensity': 38, 'second_screen': 57, 'live_events': 31, 'creator_follow': 47,
                                                 'ad_tolerance': 44, 'trial_speed': 53, 'word_of_mouth': 62}.items()}
    return t


def _margin_check(arch):
    life = {'young': 0.0, 'mid': 0.0, 'older': 0.0}
    st = {s: 0.0 for s in A.STAGES}
    for g in arch['groups']:
        for m, v in A._member_shares(g).items():
            stage, l_key = A._cell_key(m)
            st[stage] += v
            life[l_key] += v
    return life, st


class Skeleton(unittest.TestCase):
    def test_cells_rake_to_the_twins_own_margins(self):
        t = _twin()
        sk = A.skeleton(t)
        mg = sk['margins']
        self.assertAlmostEqual(sum(mg['lifecycle'].values()), 100.0, places=6)
        self.assertAlmostEqual(sum(mg['age'].values()), 100.0, places=6)
        self.assertAlmostEqual(mg['age']['young'], 9.4 + 24.1, places=4)
        self.assertAlmostEqual(mg['lifecycle']['habit'], 44.6, places=4)
        by_stage = {}
        by_life = {}
        for c in sk['cells']:
            by_stage[c['stage']] = by_stage.get(c['stage'], 0.0) + c['share_pct']
            by_life[c['life']] = by_life.get(c['life'], 0.0) + c['share_pct']
        for s in A.STAGES:
            self.assertAlmostEqual(by_stage[s], mg['lifecycle'][s], places=3, msg=s)
        for l_key in ('young', 'mid', 'older'):
            self.assertAlmostEqual(by_life[l_key], mg['age'][l_key], places=3, msg=l_key)
        self.assertEqual(len(sk['cells']), 18, 'five stages by three life stages, the habit cells split by intensity')
        habit_young = [c for c in sk['cells'] if c['stage'] == 'habit' and c['life'] == 'young']
        self.assertEqual({c['intensity'] for c in habit_young}, {'devoted', 'regular'})
        dev = next(c for c in habit_young if c['intensity'] == 'devoted')
        reg = next(c for c in habit_young if c['intensity'] == 'regular')
        self.assertGreater(dev['traits']['franchise_loyalty'], reg['traits']['franchise_loyalty'])
        self.assertGreater(dev['traits']['merch_propensity'], reg['traits']['merch_propensity'])
        young_new = next(c for c in sk['cells'] if c['stage'] == 'discovering' and c['life'] == 'young')
        older_new = next(c for c in sk['cells'] if c['stage'] == 'discovering' and c['life'] == 'older')
        self.assertGreater(young_new['traits']['discovery_social_vs_search'], older_new['traits']['discovery_social_vs_search'])
        self.assertTrue(all(3.0 <= v <= 97.0 for c in sk['cells'] for v in c['traits'].values()))

    def test_profiles_tilt_by_life_stage_and_renormalize(self):
        t = _twin()
        sk = A.skeleton(t)
        young = next(c for c in sk['cells'] if c['life'] == 'young' and c['stage'] == 'habit' and c['intensity'] == 'regular')
        mid = next(c for c in sk['cells'] if c['life'] == 'mid' and c['stage'] == 'habit' and c['intensity'] == 'regular')
        def parents(c):
            return sum(r['pct'] for r in c['profile']['parental'] if 'has' in r['label'].lower())
        self.assertGreater(parents(mid), parents(young))
        for c in sk['cells']:
            for cat, rows in c['profile'].items():
                self.assertAlmostEqual(sum(r['pct'] for r in rows), 100.0, places=2, msg=f"{c['id']} {cat}")
            self.assertTrue(all(A._age_band(r['label']) == c['life'] for r in c['profile']['age']), c['id'])


class Settle(unittest.TestCase):
    def test_kinds_sum_to_the_twin_and_are_deterministic(self):
        t = _twin()
        arch = A.derive_archetypes(t)
        self.assertEqual(arch['method'], 'deterministic')
        self.assertTrue(A.MIN_K <= arch['k'] <= A.MAX_K)
        self.assertEqual(arch['k'], len(arch['groups']))
        self.assertAlmostEqual(sum(g['share_pct'] for g in arch['groups']), 100.0, places=6)
        self.assertEqual(sum(g['people'] for g in arch['groups']), 1_000_000)
        self.assertTrue(all(g['people'] % 10 != 0 for g in arch['groups']), [g['people'] for g in arch['groups']])
        self.assertEqual(len({g['share_pct'] for g in arch['groups']}), arch['k'], 'no two kinds on the same tenth')
        self.assertEqual([g['share_pct'] for g in arch['groups']], sorted((g['share_pct'] for g in arch['groups']), reverse=True), 'largest first')
        life, st = _margin_check(arch)
        for k, v in arch['margins']['age'].items():
            self.assertAlmostEqual(life[k], v, delta=0.6, msg=k)      # the settled people move a share a few tenths at most
        for k, v in arch['margins']['lifecycle'].items():
            self.assertAlmostEqual(st[k], v, delta=0.6, msg=k)
        t2 = copy.deepcopy({k: v for k, v in t.items() if k != 'archetypes'})
        arch2 = A.derive_archetypes(t2)
        self.assertEqual([(g['id'], g['people'], g['representative']['name']) for g in arch['groups']],
                         [(g['id'], g['people'], g['representative']['name']) for g in arch2['groups']])
        self.assertIs(A.derive_archetypes(t), t['archetypes'], 'a second call returns the stored kinds')
        t['identity']['demographics']['gender'] = [{'label': 'Female', 'pct': 40.0}, {'label': 'Male', 'pct': 60.0}]
        self.assertNotEqual(A.fingerprint(t), arch['fingerprint'], 'a changed identity changes the fingerprint and the kinds re-derive')

    def test_every_kind_carries_a_representative_and_plain_copy(self):
        t = _twin()
        arch = A.derive_archetypes(t)
        names = [g['representative']['name'] for g in arch['groups']]
        self.assertEqual(len(set(names)), len(names), 'no two kinds share a name')
        for g in arch['groups']:
            rep = g['representative']
            self.assertTrue(18 <= rep['age'] <= 84)
            self.assertTrue(rep['city'])
            self.assertIn(rep['gender'], ('F', 'M', 'X'))
            self.assertTrue(g['label'] and g['who'] and g['relationship'] and g['decides'])
            for field in ('label', 'who', 'relationship', 'decides', 'moves_them', 'holds_them_back'):
                self.assertIsNone(BANNED.search(g[field]), (field, g[field]))
                self.assertNotIn('\u2014', g[field])
        lines = A.archetype_lines(t)
        self.assertEqual(len(lines), arch['k'])
        self.assertRegex(lines[0], r"^.+: [\d,]+ \w+ \(\d+\.\d%\)\. \w[\w .'-]*, \d+, .+\.$")
        summary = A.archetype_summary_line(t)
        self.assertIn('kinds of', summary)
        self.assertIn('1,000,000', summary)

    def test_a_small_audience_keeps_whole_people(self):
        t = _twin(size=340)
        arch = A.derive_archetypes(t)
        self.assertEqual(sum(g['people'] for g in arch['groups']), 340)
        self.assertTrue(all(g['people'] >= 1 for g in arch['groups']))


class Desk(unittest.TestCase):
    def _desk(self, data):
        return lambda system, user, max_tokens, temperature: data

    def test_the_desk_names_the_kinds_within_bounds(self):
        t = _twin()
        sk_cells = A.merge_small(A.skeleton(t)['cells'], A.MIN_SHARE_PCT)
        first, second = sk_cells[0]['id'], sk_cells[1]['id']
        data = {'groups': [{'id': c['id'], 'label': f"Weeknight regulars who found it on Hulu {i}", 'who': 'They keep the show on while cooking. The synthetic panel data says so.',
                            'relationship': 'They have watched since the original run.', 'decides': 'A friend or a clip tips them.',
                            'moves_them': 'More of the town', 'holds_them_back': 'Very little',
                            'share_adjust_pct': 400 if c['id'] == first else 0,
                            'trait_shifts': {'franchise_loyalty': 90, 'bogus': 5, 'merch_propensity': 10, 'second_screen': -5, 'churn_propensity': -3, 'ad_tolerance': 2, 'trial_speed': 1}}
                           for i, c in enumerate(sk_cells)],
                'notes': 'The archetypes split on how long they have watched, per our research.'}
        data['groups'][1]['merge_into'] = first
        data['groups'][0]['split_label'] = 'Parents who keep it on for the kids'     # a split of the largest kind survives the minimum share
        data['groups'][0]['split_share_pct'] = 40
        data['groups'][2]['split_label'] = 'Too small to stand alone'                 # a split half under MIN_SHARE_PCT folds back into its kind
        data['groups'][2]['split_share_pct'] = 40
        arch = A.derive_archetypes(t, call=self._desk(data))
        self.assertEqual(arch['method'], 'reasoned')
        self.assertEqual(arch['size_people'], 1_000_000)
        self.assertEqual(sum(g['people'] for g in arch['groups']), 1_000_000, 'the desk never changes the size of the audience')
        self.assertAlmostEqual(sum(g['share_pct'] for g in arch['groups']), 100.0, places=6)
        ids = [g['id'] for g in arch['groups']]
        self.assertNotIn(second, ids, 'the merged kind is gone')
        self.assertTrue(any(i.startswith(first + '~') for i in ids), 'the split kind is two')
        labels = [g['label'] for g in arch['groups']]
        self.assertTrue(any(l.startswith('Weeknight regulars') for l in labels))
        self.assertIn('Parents who keep it on for the kids', labels)
        self.assertNotIn('Too small to stand alone', labels, 'a split half under the minimum share folds back')
        self.assertTrue(all(g['share_pct'] >= A.MIN_SHARE_PCT - 0.2 for g in arch['groups']), [g['share_pct'] for g in arch['groups']])
        for g in arch['groups']:
            self.assertIsNone(BANNED.search(g['who']), g['who'])
            self.assertLessEqual(g['traits']['franchise_loyalty'], 97.0)
            self.assertNotIn('bogus', g['traits'])
        big = sum(g['share_pct'] for g in arch['groups'] if g['id'].startswith(first))
        self.assertLess(big, 60.0, 'a +400 nudge is capped at +30 and re-fitted to the margins')
        life, st = _margin_check(arch)
        for k, v in arch['margins']['age'].items():
            self.assertAlmostEqual(life[k], v, delta=0.6, msg=k)
        self.assertEqual(arch['trail']['desk']['applied'], True)
        self.assertNotIn('research', arch['trail']['desk']['notes'].lower())

    def _desk_with_queer_kinds(self, t):
        cells = A.merge_small(A.skeleton(t)['cells'], A.MIN_SHARE_PCT)
        data = {'groups': [{'id': c['id'], 'label': f"Kind {i}"} for i, c in enumerate(cells)]}
        data['groups'][0].update(label='Gay men who found her through Flashdance nights',
                                 profile_lean={'gender': {'Male': 95}, 'sexual_orientation': {'LGBTQ+': 95}})
        data['groups'][1].update(label='Trans women who see themselves in her roles',
                                 profile_lean={'gender': {'Trans Female': 90}, 'sexual_orientation': {'LGBTQ+': 85}})
        return data

    @staticmethod
    def _implied(arch, cat):
        out = {}
        for g in arch['groups']:
            for r in g['profile'].get(cat, []):
                out[r['label']] = out.get(r['label'], 0.0) + g['share_pct'] / 100.0 * r['pct']
        return out

    def test_a_kind_is_who_the_desk_says_it_is(self):
        # Jenna 2026-11-17: "why is filling with women a house rule? it should be based on the archetype. if it is gay men,
        # trans women, etc then that's what it should be."
        t = _beals()
        t['identity']['demographics']['gender'] = [{'label': 'Female', 'pct': 66.0}, {'label': 'Male', 'pct': 24.0}, {'label': 'Non-Binary', 'pct': 2.7},
                                                   {'label': 'Trans Male', 'pct': 0.7}, {'label': 'Trans Female', 'pct': 6.6}]
        arch = A.derive_archetypes(t, call=self._desk(self._desk_with_queer_kinds(t)))
        gay = next(g for g in arch['groups'] if g['label'].startswith('Gay men'))
        trans = next(g for g in arch['groups'] if g['label'].startswith('Trans women'))
        self.assertGreaterEqual(next(r['pct'] for r in gay['profile']['gender'] if r['label'] == 'Male'), 94.0)
        self.assertGreaterEqual(next(r['pct'] for r in trans['profile']['gender'] if r['label'] == 'Trans Female'), 89.0)
        for cat in ('gender', 'sexual_orientation', 'ethnicity', 'education'):
            want = {r['label']: r['pct'] for r in t['identity']['demographics'][cat]}
            got = self._implied(arch, cat)
            for lab, v in want.items():
                self.assertAlmostEqual(got.get(lab, 0.0), v, delta=0.05, msg=f"{cat} {lab}: the other kinds re-fit so the audience's totals hold")
        g = P.build_group([t], seed='beals')
        seats = {r['archetype']['label'][:9]: r for r in g['roster']}
        self.assertEqual((seats['Gay men w']['gender_label'], seats['Gay men w']['gender']), ('Male', 'M'))
        self.assertIn('LGBTQ', seats['Gay men w']['orientation'])
        self.assertEqual((seats['Trans wom']['gender_label'], seats['Trans wom']['gender']), ('Trans Female', 'F'))
        self.assertIn('LGBTQ', seats['Trans wom']['orientation'])
        queer = [r for r in g['roster'] if 'LGBTQ' in str(r.get('orientation') or '')]
        self.assertGreaterEqual(sum(1 for r in queer if r['gender'] == 'M'), 2, 'the gay men kind and the men the rows carry')
        bio_t = ROOM.persona_bio(ROOM.persona_card(t, seats['Trans wom'], 'x'), t, 'x')
        self.assertIn('She is a trans woman.', bio_t)
        self.assertNotIn('lesbian', bio_t, 'the bare LGBTQ+ row adds nothing once the seat is named')
        bio_g = ROOM.persona_bio(ROOM.persona_card(t, seats['Gay men w'], 'x'), t, 'x')
        self.assertIn('He is gay.', bio_g)
        self.assertNotIn('trans', bio_g)
        for r in g['roster']:
            band = A._age_band(r.get('age_label'))
            if band:
                lo, hi = next((a, z) for k, a, z in A.LIFE if k == band)
                self.assertTrue(lo <= r['age'] <= min(hi, 84), (r['archetype']['label'], r['age'], r['age_label']))

    def test_a_kind_never_holds_more_of_a_row_than_the_audience_has(self):
        t = _beals()          # 0.23% trans women: a 7% kind of trans women is more than the audience holds
        arch = A.derive_archetypes(t, call=self._desk(self._desk_with_queer_kinds(t)))
        trans = next(g for g in arch['groups'] if g['label'].startswith('Trans women'))
        self.assertLess(next(r['pct'] for r in trans['profile']['gender'] if r['label'] == 'Trans Female'), 5.0)
        self.assertAlmostEqual(sum(r['pct'] for r in trans['profile']['gender']), 100.0, places=2)
        want = {r['label']: r['pct'] for r in t['identity']['demographics']['gender']}
        got = self._implied(arch, 'gender')
        for lab, v in want.items():
            self.assertAlmostEqual(got.get(lab, 0.0), v, delta=0.05, msg=lab)
        gay = next(g for g in arch['groups'] if g['label'].startswith('Gay men'))
        self.assertGreaterEqual(next(r['pct'] for r in gay['profile']['gender'] if r['label'] == 'Male'), 94.0, 'the men the audience has are enough for a kind of gay men')

    def test_a_failed_desk_leaves_the_deterministic_kinds(self):
        t = _twin()

        def boom(system, user, max_tokens, temperature):
            raise RuntimeError('no model')
        arch = A.derive_archetypes(t, call=boom)
        self.assertEqual(arch['method'], 'deterministic')
        self.assertEqual(sum(g['people'] for g in arch['groups']), 1_000_000)
        arch2 = A.derive_archetypes(t, call=lambda *a: None)
        self.assertEqual(arch2['method'], 'deterministic')


class Cohorts(unittest.TestCase):
    def test_a_filter_conditions_the_kinds_and_sizes_them_to_the_cohort(self):
        t = _twin()
        arch = A.derive_archetypes(t)
        whole = {g['id']: g['share_pct'] for g in arch['groups']}
        kinds = A.seat_archetypes(t, arch, {'gender': 'F', 'age': (18, 34)}, 287_431, 'seed')
        self.assertEqual(sum(k['people'] for k in kinds), 287_431)
        self.assertTrue(all(k['people'] % 10 != 0 for k in kinds), [k['people'] for k in kinds])
        young = sum(k['share_pct'] for k in kinds if k['life'] == 'young' or 'young' in ''.join(k['members']))
        young_whole = sum(v for i, v in whole.items() if 'young' in i)
        self.assertGreater(young, young_whole, 'a cohort of 18 to 34 is made mostly of the younger kinds')
        self.assertTrue(all(A.MIN_SEGMENT_SHARE_PCT <= k['share_pct'] or len(kinds) <= A.MIN_K for k in kinds))

    def test_fit_count_merges_down_and_splits_up(self):
        t = _twin()
        arch = A.derive_archetypes(t)
        k6 = A.seat_archetypes(t, arch, {}, 1_000_000, 'seed', want=6)
        k20 = A.seat_archetypes(t, arch, {}, 1_000_000, 'seed', want=20)
        k40 = A.seat_archetypes(t, arch, {}, 1_000_000, 'seed', want=40)
        self.assertEqual((len(k6), len(k20), len(k40)), (6, 20, 40))
        for ks in (k6, k20, k40):
            self.assertEqual(sum(k['people'] for k in ks), 1_000_000)
            self.assertTrue(all(k['label'] for k in ks))
        self.assertTrue(any(': the women' in k['label'] or ': the men' in k['label'] or '(younger)' in k['label'] for k in k40), 'splits are named by their side')


class Roster(unittest.TestCase):
    def test_the_room_mirrors_the_audience_and_each_seat_is_its_kind(self):
        t = _beals()
        g = P.build_group([t], in_room=12, seed='beals')
        self.assertEqual(g['seating'], 'kinds')
        roster = g['roster']
        self.assertEqual(len(roster), 12)
        self.assertEqual(sum(1 for r in roster if r['gender'] == 'F'), 9, 'a 72% female audience seats nine women of twelve')
        queer = [r for r in roster if 'LGBTQ' in str(r.get('orientation') or '')]
        self.assertEqual(len(queer), 4)
        # who is LGBTQ+ follows the kinds inside each gender (Jenna 2026-11-17: "it should be based on the archetype"): the
        # kinds carry the audience's 33% on both sides, so three of the nine women and one of the three men
        self.assertEqual(Counter(r['gender'] for r in queer), Counter({'F': 3, 'M': 1}))
        self.assertFalse(any('prefer' in str(r.get('orientation') or '').lower() for r in roster))
        self.assertEqual(len({r['name'] for r in roster}), 12)
        seg = g['segments'][0]
        self.assertEqual(sum(r['people'] for r in roster), seg['people'])
        for r in roster:
            self.assertEqual(r['people'], r['archetype']['people'])
            self.assertTrue(r['archetype']['label'])
            self.assertEqual(r['stands_for_people'], r['people'])
            band = A._age_band(r.get('age_label'))
            if band:
                lo, hi = next((a, z) for k, a, z in A.LIFE if k == band)
                self.assertTrue(lo <= r['age'] <= min(hi, 84), (r['age'], r['age_label']))
        self.assertGreater(max(r['people'] for r in roster), min(r['people'] for r in roster) * 1.5)

    def test_no_count_seats_the_kinds_as_they_are(self):
        t = _twin()
        g = P.build_group([t], seed='kinds')
        self.assertEqual(g['seating'], 'kinds')
        self.assertEqual(len(g['roster']), t['archetypes']['k'])
        self.assertEqual(g['in_room'], len(g['roster']))
        self.assertEqual(sum(r['people'] for r in g['roster']), 1_000_000)

    def test_quota_seating_on_request_and_by_switch(self):
        t = _twin()
        g = P.build_group([t], in_room=12, seed='q', seating='quota')
        self.assertEqual(g['seating'], 'quota')
        self.assertEqual(len(g['roster']), 12)
        self.assertFalse(any(r.get('archetype') for r in g['roster']))
        os.environ['DTIQ_ARCHETYPE_SEATS'] = '0'
        try:
            g2 = P.build_group([t], in_room=12, seed='q2')
            self.assertEqual(g2['seating'], 'quota')
        finally:
            os.environ.pop('DTIQ_ARCHETYPE_SEATS', None)

    def test_a_cohort_room_partitions_each_cohort_by_kinds(self):
        t = _twin()
        segs = [{'label': 'Women 25 to 44', 'twin': 'jb', 'composition': 'women 25 to 44'}, {'label': 'Men', 'twin': 'jb', 'composition': 'men'}]
        g = P.build_group([t], segments=segs, seed='coh')
        self.assertEqual(g['seating'], 'kinds')
        for s in g['segments']:
            seats = [r for r in g['roster'] if r['segment'] == s['label']]
            self.assertEqual(sum(r['people'] for r in seats), s['people'], s['label'])
            self.assertEqual(len(seats), s['seats'])
        women = [r for r in g['roster'] if r['segment'] == 'Women 25 to 44']
        self.assertTrue(all(r['gender'] == 'F' for r in women))
        self.assertTrue(all(25 <= r['age'] <= 44 for r in women), [r['age'] for r in women])
        self.assertLessEqual(len(g['roster']), P.MAX_IN_ROOM)

    def test_reuse_matches_a_kinds_room_and_an_old_empty_room_reseats(self):
        t = _twin()
        sess = P.open_session([t], title='Kinds', session_id='k1')
        self.assertEqual(sess['group']['seating'], 'kinds')
        payload = {'panel_sessions': [sess]}
        self.assertIs(P.find_reusable_session(payload, [t], None, 0, created_by='prometheus', title='Kinds'), sess)
        self.assertIsNone(P.find_reusable_session(payload, [t], None, 7, created_by='prometheus', title='Kinds'), 'a room of seven is another room')
        old = {'id': 'old', 'questions': [], 'group': {'in_room': 12, 'roster': [{'seat': i + 1, 'name': 'X', 'gender': 'F', 'age': 40, 'city': 'Chicago', 'orientation': 'Straight', 'education': 'BA'} for i in range(12)]}}
        self.assertTrue(P.needs_reseat(old, [t]))
        P.rebuild_group(old, [t], seed='old')
        self.assertEqual(old['group']['seating'], 'kinds')
        asked = {'id': 'asked', 'questions': [{'id': 'q'}], 'group': {'in_room': 12, 'roster': [{'seat': 1, 'gender': 'M'}]}}
        self.assertFalse(P.needs_reseat(asked, [t]))


class Weighing(unittest.TestCase):
    def _room(self):
        t = _twin()
        g = P.build_group([t], seed='w')
        roster = g['roster']
        return t, g, roster

    def test_the_largest_kind_moves_the_count_more_than_the_smallest(self):
        t, g, roster = self._room()
        opts = [{'id': 'a', 'label': 'Yes', 'lean': 'yes'}, {'id': 'b', 'label': 'No', 'lean': 'no'}]
        big, small = max(roster, key=lambda r: r['people']), min(roster, key=lambda r: r['people'])
        def answers(yes_seat):
            return [{'seat': r['seat'], 'option': 'a' if r['seat'] == yes_seat else 'b', 'confidence': 5} for r in roster]
        prior = {g['segments'][0]['label']: {'a': 0.5, 'b': 0.5}}
        o_big, _ = ROOM.weigh(answers(big['seat']), opts, roster, g['segments'], prior, 'seed')
        o_small, _ = ROOM.weigh(answers(small['seat']), opts, roster, g['segments'], prior, 'seed')
        yes_big = next(o for o in o_big if o['id'] == 'a')
        yes_small = next(o for o in o_small if o['id'] == 'a')
        self.assertGreater(yes_big['pct'], yes_small['pct'])
        self.assertGreater(yes_big['people'], yes_small['people'])
        for out in (o_big, o_small):
            self.assertAlmostEqual(sum(o['pct'] for o in out), 100.0, places=6)
            self.assertEqual(sum(o['people'] for o in out), g['segments'][0]['people'])
            self.assertTrue(all('seat_people_pct' in o and 'seat_people' in o for o in out))
        self.assertEqual(yes_big['seat_people'], big['people'])

    def test_the_weighted_mirror_lands_the_people_behind_the_seats_on_the_count(self):
        t, g, roster = self._room()
        opts = [{'id': 'a', 'label': 'Yes', 'lean': 'yes'}, {'id': 'b', 'label': 'Maybe', 'lean': 'maybe'}, {'id': 'c', 'label': 'No', 'lean': 'no'}]
        answers = [{'seat': r['seat'], 'option': 'c', 'confidence': 4, 'line': 'no'} for r in roster]
        total = g['segments'][0]['people']
        opts_out = [{'id': 'a', 'label': 'Yes', 'lean': 'yes', 'pct': 38.0, 'people': int(total * 0.38), 'seats': []},
                    {'id': 'b', 'label': 'Maybe', 'lean': 'maybe', 'pct': 22.0, 'people': int(total * 0.22), 'seats': []},
                    {'id': 'c', 'label': 'No', 'lean': 'no', 'pct': 40.0, 'people': total - int(total * 0.38) - int(total * 0.22), 'seats': [r['seat'] for r in roster]}]
        by_segment = [{'segment': g['segments'][0]['label'], 'people': total, 'distribution': [{'label': o['label'], 'pct': o['pct'], 'people': o['people']} for o in opts_out]}]
        self.assertTrue(ROOM.weighted_room(roster))
        plan = ROOM.mirror_seats(opts_out, by_segment, answers, roster, g['segments'])
        self.assertTrue(plan.get('weighted'))
        ROOM.retally(opts_out, by_segment, answers, roster)
        biggest = max(r['people'] for r in roster)
        for o in opts_out:
            self.assertLessEqual(abs(o['seat_people'] - o['people']), biggest, (o['label'], o['seat_people'], o['people']))
            self.assertGreaterEqual(o['seats_n'], 1, 'every side worth a seat holds one')
        self.assertEqual(sum(o['seats_n'] for o in opts_out), len(roster))
        self.assertEqual(sum(plan['alloc'].values()), len(roster))
        moved = [a for a in answers if a.get('mirrored')]
        self.assertEqual(len(moved), len(plan['moved']))
        self.assertTrue(all(a['option'] in ('a', 'b') for a in moved))

    def test_an_equal_room_keeps_the_largest_remainder_mirror(self):
        t = _twin()
        g = P.build_group([t], in_room=12, seed='eq', seating='quota')
        self.assertFalse(ROOM.weighted_room(g['roster']))


class Personas(unittest.TestCase):
    def test_the_card_and_bio_carry_the_kind(self):
        t = _twin()
        g = P.build_group([t], seed='pc')
        seat = g['roster'][0]
        card = ROOM.persona_card(t, seat, 'pc-room')
        self.assertEqual(card['kind']['label'], seat['archetype']['label'])
        self.assertEqual(card['kind']['people'], seat['people'])
        self.assertEqual(card['lifecycle_stage'], seat['stage'])
        bio = card['bio']
        self.assertIn('stands for', bio)
        self.assertIn(f"{seat['people']:,}", bio)
        self.assertIsNone(BANNED.search(bio), bio)
        self.assertNotIn('\u2014', bio)
        # two seats of different kinds carry different dials, so their habits differ
        other = next(r for r in g['roster'] if r['archetype']['id'] != seat['archetype']['id'] and r['stage'] != seat['stage'])
        card2 = ROOM.persona_card(t, other, 'pc-room')
        self.assertNotEqual(card['habits'], card2['habits'])


class Room(unittest.TestCase):
    def test_a_question_lands_on_a_kinds_room_with_the_seats_mirroring_the_count(self):
        ROOM.clear_room_caches()
        twin = _pvalley()
        sess = P.open_session([twin], title='P-Valley', session_id='kinds-room-1')
        self.assertEqual(sess['group']['seating'], 'kinds')
        n = len(sess['group']['roster'])
        rec = ROOM.ask_room(sess, {twin['meta']['key']: twin}, 'are you currently watching swat exiles?', s3=None, twin_key='pv', run_async=False, model_fn=fake_model)
        self.assertEqual(rec['stage'], 'done')
        by = {o['id']: o for o in rec['options']}
        self.assertEqual(rec['anchor']['kind'], 'result')
        self.assertAlmostEqual(by['a']['pct'], 88.0, delta=1.0)
        self.assertEqual(sum(o['seats_n'] for o in rec['options']), n)
        total = sum(o['people'] for o in rec['options'])
        self.assertEqual(total, sess['group']['size_people'])
        biggest = max(r['people'] for r in sess['group']['roster'])
        for o in rec['options']:
            self.assertLessEqual(abs(o['seat_people'] - o['people']), biggest, (o['label'], o['seat_people'], o['people']))
            self.assertIsNotNone(o['seat_people_pct'])
        self.assertGreaterEqual(by['c']['seats_n'], 1, 'the one-in-twenty side holds a seat')
        self.assertEqual(rec['headline_people'], by['c']['people'], 'the headline people are the yes row\'s share, never the tally')
        self.assertTrue(rec['trail']['mirror']['alloc'])


class Build(unittest.TestCase):
    def test_build_twin_attaches_the_kinds(self):
        from migration import digital_twin_synthesis as SYN
        import test_digital_twin_builder as TB
        twin = SYN.build_twin({'source': 'demographics', 'demographics': {'gender': ['Women'], 'age': ['25-34', '35-44']}, 'panelists': 20, 'purpose': 'Pricing'}, None, s3=TB._s3(), with_research=False)
        self.assertIn('archetypes', twin)
        arch = twin['archetypes']
        self.assertEqual(sum(g['people'] for g in arch['groups']), int(twin['meta']['size_us']))
        self.assertEqual(twin['meta']['panelists'], 20)
        self.assertFalse(twin['meta']['panelists_explicit'])
        self.assertEqual(DT.requested_seats(twin), 0, 'the stored default never forces a count')
        twin['meta']['panelists_explicit'] = True
        self.assertEqual(DT.requested_seats(twin), 20)


if __name__ == '__main__':
    unittest.main(verbosity=2)
