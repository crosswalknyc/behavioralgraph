#!/usr/bin/env python3
"""Category reach: one rollup, the same figure on every surface (2026-11-11, Armine).

Armine, on the Wicked Tuna deck's four purchase categories: "it would be nice to also see the overall percentage
and index for each category ... something similar could be really useful on the Profiles page as well".

What holds here:
  * the estimator (migration/category_rollup.union_reach): never below the largest brand, never above the sum,
    100 or the unrelated-brands figure, never lowered by adding a brand, the same output for the same inputs in
    any order, well below the unrelated-brands figure, never 100.0 for a near-universal category, and the same
    function on the audience and on the US side
  * on two real profiles (extracts of Wicked Tuna Viewers and Chipotle, with the matching Gen Pop rows):
    demographics, metadata, location and the interest list are left out; the subject's own 100 row is left out
    of its category; a category's reach is never below any brand in it
  * the page payload, the profile digest every Prometheus read carries, a chat answer's bound figure and the
    deck card print the identical percent and index for the same profile and the same group (a whole category
    and a named list of brands)
  * a named group keeps the member list it was first given on a profile (the deck's group answers the chat)
  * the deck card carries the figure, the index and its plain comparison, and the slide says what they mean
  * nothing a reader sees carries a method word, a source name, a dash, or the word household

Run:  REGRESSION_TEST_MODE=1 python3 scripts/test_category_rollup.py
"""
from __future__ import annotations

import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ['PROMETHEUS_DECK_PHOTOS'] = '0'
os.environ['PM_DIGEST_SHARED'] = '0'
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402

from migration import category_rollup as cr  # noqa: E402
from prometheus import category_reach as cre  # noqa: E402
import prometheus_analysis as pma  # noqa: E402

FIX = json.loads(Path(HERE, 'fixtures', 'category_rollup_profiles.json').read_text(encoding='utf-8'))
BANNED = re.compile(
    r"\b(?:model(?:ed|led|s)?|estimat\w*|union|blend\w*|project(?:ed|ion)|synth\w*|panel|sample|calibrat\w*|"
    r"anchor\w*|deriv\w*|households?|HHs?|confidence|approximate\w*|pew|nielsen|statista|circana)\b|[\u2014\u2013]",
    re.I)


def _frame(name):
    doc = FIX[name]
    return pd.DataFrame(doc['rows'], columns=doc['columns']).fillna('')


def _gp_map():
    """The Gen Pop map exactly as Prometheus builds it."""
    g = _frame('gen_pop')
    gp = {}
    bp = pma._bp_col(g)
    for _, row in g.iterrows():
        cat = pma._norm_cat(row.get('Column'))
        if cat in pma.METADATA_COLS:
            continue
        v = pma._parse_bp(row.get(bp))
        if v is not None:
            gp[(cat, pma._norm_brand(row.get('Value')))] = v
    return gp


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError('NoSuchKey')
        return {'Body': io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')


class Estimator(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(20261111)

    def _sets(self, n=160):
        for _ in range(n):
            k = self.rng.randint(1, 40)
            yield [round(self.rng.choice((0.01, 0.4, 2.0, 9.0, 31.0, 62.0, 88.0)) * self.rng.random() + 0.01, 4)
                   for _ in range(k)]

    def test_bounds(self):
        for ps in self._sets():
            u = cr.union_reach(ps)
            self.assertGreaterEqual(u + 1e-9, max(ps), ps)
            self.assertLessEqual(u, min(100.0, sum(ps)) + 1e-9, ps)
            self.assertLessEqual(u, cr.unrelated_reach(ps) + 1e-9, ps)

    def test_adding_a_brand_never_lowers_it(self):
        for ps in self._sets():
            base = cr.union_reach(ps)
            for extra in (0.02, 1.5, 12.0, 47.0, 93.0):
                self.assertGreaterEqual(cr.union_reach(ps + [extra]) + 1e-9, base, (ps, extra))

    def test_deterministic_and_order_free(self):
        for ps in self._sets(60):
            a = cr.union_reach(ps)
            shuffled = list(ps)
            self.rng.shuffle(shuffled)
            self.assertEqual(a, cr.union_reach(list(ps)))
            self.assertAlmostEqual(a, cr.union_reach(shuffled), places=9)

    def test_one_brand_is_itself_and_nothing_is_zero(self):
        self.assertEqual(cr.union_reach([37.25]), 37.25)
        self.assertEqual(cr.union_reach([]), 0.0)
        self.assertEqual(cr.union_reach([0, None, 'x', float('nan')]), 0.0)

    def test_sits_well_below_the_unrelated_figure(self):
        trucks = [43.7, 36.2, 30.1, 23.7, 19.6]
        u, ceil = cr.union_reach(trucks), cr.unrelated_reach(trucks)
        self.assertGreater(u, max(trucks))
        self.assertLess(u, ceil - 15.0)          # low end, on purpose
        many = [5.0] * 60
        self.assertLess(cr.union_reach(many), cr.unrelated_reach(many) - 30.0)

    def test_near_universal_never_prints_100(self):
        search = [88.9, 61.0, 45.0, 30.0, 22.0] + [5.0] * 40
        self.assertLess(round(cr.union_reach(search), 1), 100.0)
        self.assertLessEqual(cr.union_reach([97.0, 96.0, 95.0, 90.0] * 10), cr.CAP)
        # a single brand above the cap still stands: the reach is never below a brand in it
        self.assertEqual(round(cr.union_reach([99.6, 80.0, 60.0]), 1), 99.6)

    def test_same_function_both_sides(self):
        pairs = [('A', 30.0, 30.0), ('B', 12.0, 12.0), ('C', 4.0, 4.0)]
        r = cr.rollup_pairs(pairs, 'same')
        self.assertEqual(r['reach'], r['genpop'])
        self.assertEqual(r['index'], 100)
        swapped = cr.rollup_pairs([(b, g, a) for b, a, g in [('A', 30.0, 11.0), ('B', 12.0, 7.0), ('C', 4.0, 9.0)]])
        straight = cr.rollup_pairs([('A', 30.0, 11.0), ('B', 12.0, 7.0), ('C', 4.0, 9.0)])
        self.assertEqual(swapped['reach'], straight['genpop'])
        self.assertEqual(swapped['genpop'], straight['reach'])

    def test_index_is_what_the_two_printed_figures_give(self):
        r = cr.rollup_pairs([('A', 27.9, 10.7), ('B', 10.4, 4.5), ('C', 5.3, 2.1)], 'workwear and tools')
        self.assertEqual(r['index'], int(round(r['reach'] / r['genpop'] * 100)))
        self.assertEqual(cr.times_of(212), '2.1x the US average')
        self.assertEqual(cr.times_of(101), 'in line with the US average')
        self.assertEqual(cr.times_of(63), '0.6x the US average')


class RealProfiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gp = _gp_map()
        cls.wt = _frame('wicked_tuna_viewers')
        cls.chip = _frame('chipotle')
        cls.wt_roll = cr.category_rollups(cls.wt, cls.gp)
        cls.chip_roll = cr.category_rollups(cls.chip, cls.gp)

    def test_non_behavioral_groups_are_left_out(self):
        for roll in (self.wt_roll, self.chip_roll):
            for skip in ('AGE', 'GENDER', 'INCOME', 'ETHNICITY', 'EDUCATION', 'OCCUPATION', 'RELATIONSHIP',
                         'PARENTAL STATUS', 'SEXUAL ORIENTATION', 'LOCATION', 'INTEREST', 'AVID FAN',
                         'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'SUBJECT'):
                self.assertNotIn(skip, roll, skip)
            self.assertIn('AUTOMOBILE', roll)
            self.assertIn('QSR', roll)

    def test_subject_self_pin_is_left_out(self):
        qsr = self.chip_roll['QSR']
        self.assertNotEqual(cr.norm_brand(qsr['top']), 'chipotle')
        self.assertLess(qsr['reach'], 100.0)
        self.assertLess(qsr['top_pct'], 99.0)
        rows = cr.profile_rows(self.chip)['QSR']
        self.assertNotIn('chipotle', {cr.norm_brand(b) for b, _ in rows})
        # the carrier at 99.6 on the Wicked Tuna file is a brand, not the subject: it stays
        st = self.wt_roll['STREAMING/PLATFORM']
        self.assertGreaterEqual(st['reach'], st['top_pct'])
        self.assertLess(st['reach'], 100.0)

    def test_a_display_name_never_changes_the_figure(self):
        for df in (self.wt, self.chip):
            base = cr.category_rollups(df, self.gp)
            for name in ('Ford', 'McDonalds', 'Chipotle - Avid Fan', 'Wicked Tuna Viewers', None):
                self.assertEqual(cr.category_rollups(df, self.gp, subject=name), base, name)

    def test_reach_is_never_below_a_brand_in_the_category(self):
        for df, roll in ((self.wt, self.wt_roll), (self.chip, self.chip_roll)):
            rows = cr.profile_rows(df)
            for col, r in roll.items():
                self.assertGreaterEqual(r['reach'] + 0.05, max(p for _, p in rows[col]), col)
                self.assertLessEqual(r['reach'], 99.6, col)
                if r['index'] is not None:
                    self.assertGreater(r['genpop'], 0)

    def test_named_group(self):
        r = cr.cluster_rollup(self.wt, self.gp, ['Carhartt', 'Craftsman', 'Milwaukee', 'Not A Brand'],
                              name='workwear and tools')
        self.assertEqual([cr.norm_brand(m) for m in r['members']], ['carhartt', 'craftsman', 'milwaukeetools'])
        self.assertEqual(r['missing'], ['Not A Brand'])
        self.assertGreaterEqual(r['reach'], r['top_pct'])
        solo = cr.cluster_rollup(self.wt, self.gp, ['Carhartt'], name='x')
        self.assertEqual(solo['reach'], solo['top_pct'])
        s = cr.sentence(r)
        self.assertRegex(s, r"^\d+\.\d% engage with workwear and tools brands, index \d+ \(\d+\.\dx the US average\)$")


class OneFigureEverywhere(unittest.TestCase):
    """The page payload, the digest, the chat block and the deck card."""

    @classmethod
    def setUpClass(cls):
        cls.gp = _gp_map()
        cls.profiles = {
            'Wicked Tuna Viewers': _frame('wicked_tuna_viewers'),
            'Chipotle': _frame('chipotle'),
        }

    def _page(self, df, col):
        # exactly what /api/profile-iq/category-reach returns for the column
        return cr.public(cr.category_rollups(df, self.gp)[col])

    def test_route_uses_the_shared_rollup(self):
        src = Path(ROOT, 'app.py').read_text(encoding='utf-8')
        i = src.index("@app.route('/api/profile-iq/category-reach/<path:s3_key>')")
        body = src[i:src.index("@app.route('/api/profile-run-metadata/<path:s3_key>')", i)]
        self.assertIn('_require_profile_run_access(s3_key)', body)
        self.assertIn('_cr.category_rollups(df, gp)', body)
        self.assertIn('_cr.public(r)', body)
        self.assertNotIn('request.args', body)          # no behavior flags on the route
        self.assertNotIn('request.json', body)

    def test_whole_category_same_on_page_digest_and_chat(self):
        for name, df in self.profiles.items():
            for col, ask in (('AUTOMOBILE', f"how does the automobile category index for {name}?"),
                             ('QSR', f"what percent of {name} engage with QSR brands")):
                page = self._page(df, col)
                fig = f"{page['reach']:.1f}"
                # the digest every read and every deck plan carries
                meta = pma._profile_meta(df, name)
                digest = pma.build_profile_digest(df, meta, self.gp)
                self.assertIn(f"{col} {fig} | {page['genpop']:.1f} | {page['index']}", digest, (name, col))
                # a chat answer's bound figure (through the hook the analyze paths call)
                block = pma.build_named_entity_rows(df, self.gp, ask)
                self.assertIn('CATEGORY REACH', block, (name, ask))
                self.assertIn(f"{fig}% of", block)
                self.assertIn(f"index {page['index']} ({page['times']})", block)
                self.assertIn(f"US {page['genpop']:.1f}%", block)
                # and the presentation string
                self.assertEqual(cr.sentence(cr.category_rollups(df, self.gp)[col], col.lower()),
                                 f"{fig}% engage with {col.lower()} brands, index {page['index']} ({page['times']})")

    def test_named_group_same_on_deck_and_chat(self):
        groups = {
            'Wicked Tuna Viewers': ('Workwear and tools.', ['Carhartt', 'Craftsman', 'Milwaukee Tools', 'Dickies'],
                                    'Carhartt reaches 27.9% at index 261, Craftsman 10.4% at index 229.'),
            'Chipotle': ('Athletic apparel.', ['Nike', 'Adidas', 'Lululemon', 'Under Armour'],
                         'Nike and Adidas lead the group.'),
        }
        for name, (head, brands, body) in groups.items():
            df = self.profiles[name]
            s3 = FakeS3()
            key = f"{name.replace(' ', '_')}.csv"
            orig = cre._s3
            cre._s3 = lambda: (s3, 'b')
            try:
                other = [('Trucks.', ['Ford', 'Chevrolet', 'Toyota'], 'Ford leads.'),
                         ('Fast food.', ['McDonalds', 'Starbucks', 'Taco Bell'], 'McDonalds leads.'),
                         ('Retail.', ['Amazon', 'Walmart', 'Target'], 'Amazon leads.')]
                plan = {'slides': [{'type': 'argument', 'ground': 'light', 'eyebrow': 'Buy',
                                    'title': 'Four categories where the file and the journeys agree.',
                                    'sub': 'Each group is carried by the file.',
                                    'cards': [{'head': head, 'body': body, 'brands': brands}] +
                                             [{'head': h, 'body': b, 'brands': br} for h, br, b in other]},
                                   {'type': 'argument', 'title': 'Four facts.', 'cards': [
                                       {'head': 'Older and male.', 'body': 'Men lead the file at 58.6%.'},
                                       {'head': 'Gear spend.', 'body': 'Led by YETI at index 395.'}]}]}
                out = cre.annotate_plan(plan, df, self.gp, s3_key=key, subject=name, expand=False)
                sl = out['slides'][0]
                self.assertTrue(sl.get('_reach'), name)
                card = sl['cards'][0]
                want = cr.cluster_rollup(df, self.gp, brands, name=cre.clean_label(head))
                self.assertEqual(card['reach'], f"{want['reach']:.1f}%")
                self.assertEqual(card['index'], want['index'])
                self.assertEqual(card['times'], want['times'])
                self.assertIn('with at least one brand in the group', sl['read'])
                self.assertEqual(sl['read'], cre.read_line(name))
                # a slide whose cards are not groups of brands is left exactly as it was
                self.assertNotIn('_reach', out['slides'][1])
                self.assertNotIn('reach', out['slides'][1]['cards'][0])
                # the chat: the same name on the same profile answers with the deck's group and figure
                label = cre.clean_label(head)
                block = cre.block(df, self.gp, f"what percent of {name} engage with {label} brands?",
                                  s3_key=key, subject=name,
                                  reason=lambda s_, u_: self.fail('a settled group is never picked again'))
                self.assertIn(f"{want['reach']:.1f}% of", block, name)
                self.assertIn(f"index {want['index']} ({want['times']})", block)
                for m in want['members']:
                    self.assertIn(m, block)
            finally:
                cre._s3 = orig

    def test_a_group_keeps_its_first_member_list(self):
        s3 = FakeS3()
        self.assertTrue(cre.save_group('k.csv', 'Workwear & Tools', ['CARHARTT', 'DICKIES'], by='deck', s3=s3, bucket='b'))
        self.assertFalse(cre.save_group('k.csv', 'tools and workwear brands', ['X', 'Y'], by='chat', s3=s3, bucket='b'))
        g = cre.load_groups('k.csv', s3, 'b')
        self.assertEqual(list(g), [cre.label_key('workwear and tools')])
        self.assertEqual(g[cre.label_key('Workwear & Tools')]['brands'], ['CARHARTT', 'DICKIES'])
        self.assertEqual(cre.load_groups('other.csv', s3, 'b'), {})

    def test_picker_adds_only_brands_on_the_profile_and_keeps_seeds(self):
        df = self.profiles['Wicked Tuna Viewers']
        cands = cre.candidates(df, 'Wicked Tuna Viewers')
        names = {b for b, _, _ in cands}
        self.assertIn('CARHARTT', names)
        self.assertFalse(any(c in ('TALENT', 'INTEREST', 'LOCATION', 'STREAMING/PLATFORM') for _, _, c in cands))
        reply = json.dumps({'groups': [{'n': 1, 'brands': ['DICKIES', 'Made Up Brand', 'DEWALT']}]})
        got = cre.pick_groups([('workwear and tools', ['Carhartt'])], cands, reason=lambda s_, u_: reply)[0]
        self.assertEqual(got[0], 'CARHARTT')
        self.assertIn('DICKIES', got)
        self.assertNotIn('Made Up Brand', got)
        # no picker (hermetic): the seeds stand alone
        self.assertEqual(cre.pick_groups([('workwear', ['Carhartt', 'Dickies'])], cands)[0], ['CARHARTT', 'DICKIES'])

    def test_ask_reading(self):
        yes = {
            "what percent of Wicked Tuna viewers engage with workwear brands": 'workwear',
            "how does the automobile category index for Wicked Tuna viewers": 'automobile',
            "What is the overall percentage for workwear & tools?": 'workwear & tools',
            "what share of this audience buys outdoor gear brands?": 'outdoor gear',
            "does the QSR category over-index": 'QSR',
        }
        for q, g in yes.items():
            self.assertEqual(cre.group_phrase(q), g, q)
        for q in ("what percent are male", "who are the top brands", "what percent buy Carhartt",
                  "build me a deck", "how many viewers does Wicked Tuna have", ""):
            self.assertIsNone(cre.group_phrase(q), q)
        cols = ['AUTOMOBILE', 'QSR', 'WHERE THEY SHOP', 'APPAREL/FOOTWEAR', 'STREAMING/PLATFORM']
        self.assertEqual(cre.column_for('automobile', cols), 'AUTOMOBILE')
        self.assertEqual(cre.column_for('car', cols), 'AUTOMOBILE')
        self.assertEqual(cre.column_for('fast food', cols), 'QSR')
        self.assertEqual(cre.column_for('apparel', cols), 'APPAREL/FOOTWEAR')
        self.assertIsNone(cre.column_for('workwear', cols))
        # one brand by name is a brand row, never a group
        df = self.profiles['Wicked Tuna Viewers']
        self.assertEqual(cre.block(df, self.gp, "what percent engage with the Carhartt brand category", subject='x'), '')

    def test_deck_prompt_carries_the_rule(self):
        p = pma.INSIGHTS_DECK_SYSTEM_PROMPT
        self.assertIn('CATEGORY FIGURES', p)
        self.assertIn('"brands"', p)
        self.assertIn('never the sum of brand rows', p)

    def test_deck_card_renders_the_figure(self):
        import deck_builder as db
        from pptx import Presentation
        name = 'Wicked Tuna Viewers'
        df = self.profiles[name]
        plan = {'title': 'x', 'slides': [
            {'type': 'cover', 'eyebrow': 'WICKED TUNA', 'title': 'This audience spends on gear.', 'intro': 'The read.',
             'stats': [{'big': '6.35M', 'label': 'US viewers'}]},
            {'type': 'argument', 'ground': 'light', 'eyebrow': 'Buy', 'title': 'Four categories carry the buy.',
             'sub': 'Each group is carried by purchases on the file.',
             'cards': [
                 {'head': 'Workwear and tools.', 'brands': ['Carhartt', 'Craftsman', 'Milwaukee Tools'],
                  'body': 'Carhartt reaches 27.9% at index 261, Craftsman 10.4% at index 229, Milwaukee 5.3% at index 249.'},
                 {'head': 'Trucks.', 'brands': ['Ford', 'Ram', 'GMC'],
                  'body': 'Ford 43.7% at index 189, Ram 23.7% at index 222, GMC 19.6% at index 201.'},
                 {'head': 'Marine and outdoor gear.', 'brands': ['YETI', 'Igloo'],
                  'body': 'YETI reaches 23.6% of viewers at index 395 and Igloo 5.1% at index 213.'},
                 {'head': 'Eyewear and optics.', 'brands': ['Costa', 'Smith Optics', 'Maui Jim'],
                  'body': 'Smith Optics 4.6% at index 337, Maui Jim 4.9% at index 233.'}]},
            {'type': 'close', 'title': 'A gear buyer at scale.', 'cards': [
                {'head': 'Scale is real.', 'body': 'The universe runs to 6.35M US people.'},
                {'head': 'The shape is specific.', 'body': 'Male 58.6% and 48.0% aged 55 or older.'}]}]}
        plan = cre.annotate_plan(plan, df, self.gp, subject=name, expand=False)
        cards = plan['slides'][1]['cards']
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'deck.pptx')
            n = db.render_insights_deck(plan, out, static_dir=os.path.join(ROOT, 'static'))
            self.assertEqual(n, 3)
            prs = Presentation(out)
            texts = [sh.text_frame.text.replace('\u00a0', ' ') for sh in prs.slides[1].shapes
                     if sh.has_text_frame and sh.text_frame.text.strip()]
            close = [sh.text_frame.text for sh in prs.slides[2].shapes if sh.has_text_frame]
        for c in cards:
            self.assertIn(c['reach'], texts)
            self.assertIn(f"Index {c['index']}", texts)
            self.assertIn(c['times'], texts)
            self.assertIn(c['body'], texts)             # the copy is whole, never trimmed
        self.assertIn('Each percent is the share of this audience that engages with at least one brand in the '
                      'group. An index of 100 is the US average.', texts)
        # every percent shares one precision, every index is a whole number
        for c in cards:
            self.assertRegex(c['reach'], r"^\d{1,2}\.\d%$")
            self.assertIsInstance(c['index'], int)
        # the numbered cards elsewhere keep their ordinals
        self.assertIn('01', close)
        rendered = ' '.join(texts)
        self.assertIsNone(BANNED.search(rendered), BANNED.search(rendered))
        self.assertNotRegex(rendered, r"\bdoors?\b")
        fin = plan.get('_finish') or {}
        if fin.get('ready') is not None:
            self.assertTrue(fin.get('ready'), fin)
            self.assertEqual((fin.get('counts') or {}).get('lint_fail', 0), 0, fin)


class ReaderCopy(unittest.TestCase):
    def test_no_banned_words_in_reader_strings(self):
        gp = _gp_map()
        df = _frame('wicked_tuna_viewers')
        roll = cr.category_rollups(df, gp)
        strings = [cr.sentence(r, c.lower()) for c, r in roll.items()]
        strings += [cr.figure(r) for r in roll.values()]
        strings += [cr.times_of(i) for i in (40, 100, 212, 990)]
        strings += [cre.read_line('Wicked Tuna Viewers'), cre.read_line('Nike'), cre.audience_phrase('Chipotle')]
        payload = json.dumps({c: cr.public(r) for c, r in roll.items()})
        for s in strings:
            self.assertIsNone(BANNED.search(s), s)
            self.assertNotIn('%%', s)
        self.assertNotIn('matched', payload)
        self.assertEqual(cre.audience_phrase('Wicked Tuna Viewers'), 'Wicked Tuna viewers')
        self.assertEqual(cre.audience_phrase('Nike'), 'the Nike audience')
        self.assertEqual(cre.read_line('Nike'), 'Each percent is the share of this audience that engages with at '
                         'least one brand in the group. An index of 100 is the US average.')
        src = Path(ROOT, 'app.py').read_text(encoding='utf-8')
        i = src.index("def profile_category_reach(s3_key):")
        body = src[i:i + 2200]
        for msg in re.findall(r"'error': '([^']+)'", body):
            self.assertIsNone(BANNED.search(msg), msg)

    def test_page_strings(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        html = Path(ROOT, 'templates', 'index.html').read_text(encoding='utf-8')

        def fn(name):
            i = html.index('function ' + name + '(')
            depth, j = 0, html.index('{', i)
            while True:
                ch = html[j]
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return html[i:j + 1]
                j += 1
        js = '\n'.join([
            "var window = { _piqCatReach: { cats: { 'AUTOMOBILE': { reach: 61.3, genpop: 51.3, index: 119, times: '1.2x the US average' },"
            " 'QSR': { reach: 74.4, genpop: 71.1, index: 105, times: 'in line with the US average' }, 'SERIES': { reach: 17.9, genpop: null, index: null, times: '' } } } };",
            fn('_piqCatNorm'), fn('_piqCatReachFor'), fn('_piqCatReachText'), fn('_piqCatReachHtml'),
            fn('_piqCatReachCsvCells'), fn('_piqCatReachOrder'),
            "var rows = [{cat:'A',ord:0,reach:10,index:300},{cat:'B',ord:1,reach:60,index:110},{cat:'C',ord:2,reach:null,index:null},{cat:'D',ord:3,reach:35,index:140}];",
            "console.log(JSON.stringify({",
            "  text: _piqCatReachText(_piqCatReachFor('automobile')), flat: _piqCatReachText(_piqCatReachFor('QSR')),",
            "  noidx: _piqCatReachText(_piqCatReachFor('Series')), html: _piqCatReachHtml(_piqCatReachFor('AUTOMOBILE')),",
            "  csv: _piqCatReachCsvCells('AUTOMOBILE'), nocsv: _piqCatReachCsvCells('NOPE'),",
            "  def: _piqCatReachOrder(rows, 'default').map(function(r){return r.cat;}).join(''),",
            "  pct: _piqCatReachOrder(rows, 'pct').map(function(r){return r.cat;}).join(''),",
            "  idx: _piqCatReachOrder(rows, 'index').map(function(r){return r.cat;}).join('') }));",
        ])
        out = subprocess.run([node, '-e', js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(got['text'], '61.3% engage, index 119 (1.2x the US average)')
        self.assertEqual(got['flat'], '74.4% engage, index 105 (in line with the US average)')
        self.assertEqual(got['noidx'], '17.9% engage')
        self.assertEqual(got['html'], '<b>61.3%</b> engage, index <b>119</b> (1.2x the US average)')
        self.assertEqual(got['csv'], ',"61.3% engage","index 119"')
        self.assertEqual(got['nocsv'], '')
        self.assertEqual(got['def'], 'ABCD')
        self.assertEqual(got['pct'], 'BDAC')
        self.assertEqual(got['idx'], 'ADBC')
        for s in (got['text'], got['flat'], got['noidx']):
            self.assertIsNone(BANNED.search(s), s)
        # the page reads the figures; it never computes them
        self.assertIn("fetch('/api/profile-iq/category-reach/'", html)
        seg = html[html.index('window._piqCatReachEnsure = function'):html.index("const BEH_INDEX_FILTER_LS_KEY")]
        self.assertNotIn('Math.pow', seg)
        self.assertNotIn('Math.exp', seg)
        self.assertNotIn('genPopPct', seg)


if __name__ == '__main__':
    unittest.main(verbosity=1)
