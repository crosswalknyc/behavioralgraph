#!/usr/bin/env python3
"""The Build a twin walkthrough's back end (2026-10-27, Jenna): a group
shaped by demographics is Gen Pop cut to the picks, a pinned profile is
the one the twin reads, panelists past twelve are priced at $550 each,
and Prometheus is the moderator. Hermetic: a fake S3, no model.
"""
from __future__ import annotations

import csv
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_synthesis as SYN  # noqa: E402
from migration import digital_twins as DT  # noqa: E402
from migration import digital_twin_room as ROOM  # noqa: E402


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise KeyError(Key)
        return {'Body': _Body(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')


GEN_POP = [('BRAND INPUT', 'GEN POP', '100.0', '10000000', '10000000'), ('SAMPLE SIZE', '10000000', '100.0', '10000000', '10000000'),
           ('GENDER', 'FEMALE', '49.5723', '', ''), ('GENDER', 'MALE', '47.8859', '', ''), ('GENDER', 'NON-BINARY', '1.4953', '', ''),
           ('AGE', '17 AND UNDER', '19.3339', '', ''), ('AGE', '65 OR OLDER', '19.0991', '', ''), ('AGE', '35-44', '13.4853', '', ''), ('AGE', '25-34', '13.2166', '', ''),
           ('AGE', '45-54', '12.7705', '', ''), ('AGE', '55-64', '12.3847', '', ''), ('AGE', '18-24', '9.7099', '', ''),
           ('INCOME', '$25,000 - $49,999', '17.8174', '', ''), ('INCOME', '$100,000 - $149,999', '16.8173', '', ''), ('INCOME', '$50,000 - $74,999', '16.4186', '', ''),
           ('INCOME', 'LESS THAN $25,000', '14.6183', '', ''), ('INCOME', '$150,000 - $249,999', '13.6182', '', ''), ('INCOME', '$75,000 - $99,999', '13.2184', '', ''), ('INCOME', '$250,000 OR MORE', '7.4918', '', ''),
           ('ETHNICITY', 'WHITE', '57.7959', '', ''), ('ETHNICITY', 'HISPANIC OR LATINO', '17.9796', '', ''), ('ETHNICITY', 'BLACK OR AFRICAN AMERICAN', '11.9178', '', ''), ('ETHNICITY', 'ASIAN', '6.2158', '', ''), ('ETHNICITY', 'ANOTHER RACE/ETHNICITY', '6.0908', '', ''),
           ('EDUCATION', 'HIGH SCHOOL OR LESS', '37.4027', '', ''), ('EDUCATION', 'BACHELORS DEGREE', '22.4579', '', ''), ('EDUCATION', 'GRADUATE OR PROFESSIONAL DEGREE', '17.2844', '', ''),
           ('PARENTAL_STATUS', 'NO CHILDREN', '56.5786', '', ''), ('PARENTAL_STATUS', 'HAS CHILDREN', '43.3973', '', ''),
           ('LOCATION', 'New York Ny', '6.1862', '', ''), ('LOCATION', 'Los Angeles Ca', '4.6154', '', ''), ('LOCATION', 'Dallas Ft Worth Tx', '2.5667', '', ''), ('LOCATION', 'Houston Tx', '2.2467', '', ''),
           ('STREAMING/PLATFORM', 'NETFLIX', '68.4', '', ''), ('STREAMING/PLATFORM', 'HULU', '36.7', '', ''), ('SOCIAL MEDIA', 'TIKTOK', '41.2', '', ''), ('SOCIAL MEDIA', 'FACEBOOK', '66.1', '', ''),
           ('MOST PURCHASED BRANDS', 'AMAZON', '79.1', '', ''), ('WHERE THEY SHOP', 'AMAZON', '79.1', '', ''), ('MOST PURCHASED BRANDS', 'TARGET', '38.2', '', '')]


def _csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Column', 'Value', 'Brand Penetration (Row)', 'Original Raw Numbers', 'US Gen Pop Projection'])
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def _s3():
    s3 = FakeS3()
    s3.put_object(Bucket='b', Key='Gen_Pop_2026.csv', Body=_csv(GEN_POP))
    SYN.__dict__.pop('_GP_ROWS', None)      # the per-process cache must read this fake
    return s3


class Spec(unittest.TestCase):
    def test_cleans_the_picks(self):
        spec = SYN.demographic_spec({'gender': ['Women', 'Women', 'everyone'], 'age': '25-34, 35-44', 'markets': 'Dallas; Los Angeles, national', 'interests': ' shop at Target ', 'bogus': ['x']})
        self.assertEqual(spec, {'gender': ['Women'], 'age': ['25-34', '35-44'], 'markets': ['Dallas', 'Los Angeles'], 'interests': 'shop at Target'})
        self.assertEqual(SYN.demographic_spec(None), {})
        self.assertEqual(SYN.demographic_spec({'gender': ['everyone']}), {})

    def test_names_read_as_a_person_would_say_them(self):
        self.assertEqual(SYN.demographic_name({'gender': ['Women'], 'age': ['25-34', '35-44'], 'income': ['$100,000 - $149,999', '$150,000 - $249,999', '$250,000 or more'], 'markets': ['Dallas', 'Los Angeles'], 'interests': 'shop at Target'}),
                         'Women 25 to 44, $100,000 or more, in Dallas and Los Angeles, who shop at Target')
        self.assertEqual(SYN.demographic_name({'gender': ['Men'], 'age': ['18-24', '25-34', '45-54'], 'income': ['Less than $25,000', '$25,000 - $49,999']}), 'Men 18 to 34, 45 to 54, under $50,000')
        self.assertEqual(SYN.demographic_name({'age': ['65+']}), '65+')
        self.assertEqual(SYN.demographic_name({}), 'US adults')
        self.assertLessEqual(len(SYN.demographic_name({'ethnicity': ['Hispanic or Latino'], 'markets': ['Miami', 'Houston', 'Phoenix', 'San Antonio', 'Atlanta', 'Dallas'], 'interests': 'follow the Dallas Cowboys and shop at H-E-B every week of the year without fail'})), 120)


class Identity(unittest.TestCase):
    def test_gen_pop_cut_to_the_picks(self):
        ident = SYN.demographic_identity(_s3(), {'gender': ['Women'], 'age': ['25-34', '35-44'], 'income': ['$100,000 - $149,999', '$150,000 - $249,999', '$250,000 or more'], 'markets': ['Dallas', 'Los Angeles']}, 'seed')
        d = ident['demographics']
        self.assertEqual([r['label'] for r in d['gender']], ['Women'])
        self.assertEqual(sorted(r['label'] for r in d['age']), ['25-34', '35-44'])
        self.assertEqual([r['label'] for r in d['income']], ['$100,000 - $149,999', '$150,000 - $249,999', '$250,000 or More'])
        for cat, rows in d.items():
            self.assertAlmostEqual(sum(r['pct'] for r in rows), 100.0, places=3, msg=cat)
        self.assertEqual([g['label'] for g in ident['geography']], ['Dallas Ft Worth Tx', 'Los Angeles Ca'])
        # women x 25 to 44 x the income band x the two markets, of the US
        want = 329_900_000 * 0.495723 * ((13.4853 + 13.2166) / 100) * ((16.8173 + 13.6182 + 7.4918) / 100) * ((2.5667 + 4.6154) / 100)
        self.assertLess(abs(ident['size_us'] - want) / want, 0.02)
        self.assertNotEqual(ident['size_us'] % 10, 0)

    def test_no_picks_is_the_adult_us(self):
        ident = SYN.demographic_identity(_s3(), {}, 'seed')
        labels = [r['label'] for r in ident['demographics']['age']]
        self.assertNotIn('17 and Under', labels)
        self.assertIn('65+', labels)
        self.assertEqual([r['label'] for r in ident['demographics']['parental']], ['No Children', 'Has Children'])
        self.assertLess(abs(ident['size_us'] / 329_900_000 - (1 - 0.193339)), 0.01)

    def test_a_brand_in_two_purchase_columns_is_one_brand(self):
        ident = SYN.demographic_identity(_s3(), {'gender': ['Men']}, 'seed')
        self.assertEqual([p['label'] for p in ident['purchase']].count('Amazon'), 1)

    def test_labels_read_for_a_person(self):
        self.assertEqual(SYN._demo_pretty('HIGH SCHOOL OR LESS'), 'High School or Less')
        self.assertEqual(SYN._demo_pretty('High School OR Less'), 'High School or Less')
        self.assertEqual(SYN._demo_pretty('$250,000 OR MORE'), '$250,000 or More')
        self.assertEqual(SYN._demo_pretty('ANOTHER RACE/ETHNICITY'), 'Another Race/Ethnicity')
        self.assertEqual(SYN._demo_pretty('$25,000 - $49,999'), '$25,000 - $49,999')


class Build(unittest.TestCase):
    def test_demographics_door(self):
        twin = SYN.build_twin({'source': 'demographics', 'demographics': {'gender': ['Women'], 'age': ['25-34']}, 'panelists': 20, 'purpose': 'Pricing'}, None, s3=_s3(), with_research=False)
        m = twin['meta']
        self.assertEqual(m['source'], 'demographics')
        self.assertEqual(m['name'], 'Women 25 to 34')
        self.assertEqual(m['panelists'], 20)
        self.assertFalse(m['ip_related'])
        self.assertNotIn('ip_lifetime_value', twin)
        self.assertEqual([r['kind'] for r in twin['runs']], ['greenlight', 'pricing', 'platform_move'])
        entry = SYN.index_entry('k', twin)
        self.assertEqual((entry['source'], entry['panelists']), ('demographics', 20))
        self.assertNotIn('ltv_5y', entry)
        self.assertNotIn('has_ltv', entry)

    def test_pinned_profile_is_the_one_read(self):
        s3 = _s3()
        rows = [('BRAND INPUT', 'Gilmore Girls', '100.0', '193413', '6380694'), ('SAMPLE SIZE', '2025-10-26 to 2026-10-26', '100.0', '193413', '6380694'),
                ('BRAND CATEGORY', 'SERIES - DRAMA', '', '', ''), ('SUBJECT', 'Gilmore Girls', '100', '', ''), ('GENDER', 'Female', '71.2134', '', ''), ('GENDER', 'Male', '28.7866', '', ''),
                ('AGE', '25-34', '60.0', '', ''), ('AGE', '35-44', '40.0', '', ''), ('STREAMING/PLATFORM', 'Hulu', '88.3', '', '')]
        s3.put_object(Bucket='b', Key='Picked_Profile_01_01_2026_00_00.csv', Body=_csv(rows))
        twin = SYN.build_twin({'subject': 'Something Else Entirely', 'source': 'profile', 'profile_key': 'Picked_Profile_01_01_2026_00_00.csv', 'panelists': 12}, None, s3=s3, with_research=False)
        m = twin['meta']
        self.assertEqual((m['source'], m['profile_key'], m['panelists']), ('profile', 'Picked_Profile_01_01_2026_00_00.csv', 12))
        fem = next(r['pct'] for r in twin['identity']['demographics']['gender'] if r['label'].lower().startswith('f'))
        self.assertLess(abs(fem - 71.2), 1.0)
        self.assertEqual(m['size_us'], 6380694)
        self.assertNotIn('ip_lifetime_value', twin)

    def test_research_shape_when_no_profile(self):
        demo = SYN.research_demographics({'demographics': {'gender': {'Women': 64, 'Men': 36}, 'age': {'18-24': 20, '25-34': 30, '35-44': 25, '45-54': 15, '55-64': 6, '65+': 4}, 'income': None}}, 'seed')
        self.assertEqual(set(demo), {'gender', 'age'})
        self.assertAlmostEqual(sum(r['pct'] for r in demo['gender']), 100.0, places=3)
        self.assertEqual(SYN.research_demographics({}, 'seed'), {})


class Panelists(unittest.TestCase):
    def test_seats_past_twelve_are_priced(self):
        self.assertEqual(DT.extra_panelists(12), 0)
        self.assertEqual(DT.extra_panelists(16), 4)
        self.assertEqual(DT.extra_panelists(2), 0)
        self.assertEqual(DT.extra_panelists(99), DT.PANEL_SEATS_MAX - 12)
        self.assertEqual(DT.extra_panelists('x'), 0)
        self.assertEqual(DT.PRICING['digital_twin_panelist'], 550.0)
        self.assertEqual(DT.PULL_TYPES['digital_twin_panelist'], 'Digital Twins IQ Focus Group Panelist')

    def test_rooms_open_on_the_twins_panelists(self):
        self.assertEqual(DT.panelists_for({'meta': {'panelists': 16}}), 16)
        self.assertEqual(DT.panelists_for({'meta': {}}), 12)
        self.assertEqual(DT.panelists_for({'meta': {'panelists': 1}}), 2)
        self.assertEqual(DT.panelists_for(None), 12)

    def test_pricing_lines_and_how_to_say_it(self):
        lines = DT.pricing_lines()
        self.assertTrue(any(l.startswith('Extra panelist - $550') and '12' in l for l in lines))
        self.assertTrue(any(l.startswith('Prometheus usage - metered') for l in lines))
        doc = DT.how_to()
        panel = next(it for it in doc['items'] if it['id'] == 'panel')
        self.assertIn('Prometheus moderates', panel['answers'])
        self.assertIn('$550', panel['cost'])
        twin = next(it for it in doc['items'] if it['id'] == 'twin')
        self.assertIn('walks you through it', twin['put_in'])
        self.assertIn('metered', twin['cost'])


class Moderator(unittest.TestCase):
    def test_prometheus_is_the_moderator(self):
        self.assertEqual(ROOM.STAGE_LABEL['moderator is reading'], 'Prometheus is reading the room')
        self.assertIn('You are Prometheus, a seasoned focus group moderator', ROOM.OPTIONS_SYSTEM)
        self.assertIn('You are Prometheus, the moderator', ROOM.READ_SYSTEM)
        self.assertIn('The moderator is Prometheus', ROOM.PERSONA_SYSTEM)
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            page = fh.read()
        self.assertNotIn('Moderator note', page)
        self.assertIn('Prometheus is reading the room', page)
        self.assertIn('id="dtwBuildDrawer"', page)
        # Cover tiles and the twin page name the group, not SERIES /
        # NETFLIX or IP-related. The twins home page does not carry the
        # viewer-style hero card. A twin can be any audience.
        self.assertNotIn("esc(kind + home)", page)
        self.assertNotIn('<div class="kind">', page)
        self.assertNotIn('IP-related', page)
        self.assertNotIn("cap(m.format)", page)
        self.assertNotIn("esc(ident.read", page)
        self.assertNotIn('new season clears', page)
        self.assertNotIn("<em>The twin</em>", page)
        self.assertNotIn('Home <b>', page)
        self.assertIn("fold('identity', 'Identity', 'who they are', idb, false)", page)
        self.assertIn("fold('traits', 'Traits', 'how they respond', tr, false)", page)
        self.assertIn("fold('lifecycle', 'Lifecycle', 'where they sit', lcb, false)", page)
        self.assertIn("fold('runs', 'Runs', (t.runs || []).length + ' on this twin', rb, false)", page)
        self.assertNotIn('IP lifetime value', page)
        self.assertNotIn('IP, 5 years', page)
        self.assertNotIn('function renderLtv', page)


class LifetimeValueGone(unittest.TestCase):
    def test_strip_drops_stored_lifetime_value(self):
        payload = {
            'ip_lifetime_value': {'total': {'5': {'expected': 1843849793.41}}},
            'the_read': {'tldr': ['They are fans.', 'As IP it is worth $1.84B over five years and $2.1B over ten.']},
        }
        SYN.strip_ip_lifetime_value(payload)
        self.assertNotIn('ip_lifetime_value', payload)
        self.assertEqual(payload['the_read']['tldr'], ['They are fans.'])

    def test_how_to_does_not_name_lifetime_value(self):
        doc = DT.how_to()
        twin = next(it for it in doc['items'] if it['id'] == 'twin')
        blob = ' '.join(str(v) for v in twin.values()).lower()
        self.assertNotIn('lifetime value', blob)
        self.assertNotIn('worth over five', blob)


if __name__ == '__main__':
    unittest.main(verbosity=1)
