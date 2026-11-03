#!/usr/bin/env python3
"""Pathways, One viewer and Clickstream on every Digital Journey (Jenna 2026-11-03).

Offline: no model, no network. Covers the shape the tab reads, the
arithmetic the module forces, the retail clickstream family, the
fallbacks, the mirror into the blob, and the workbook sheets.
"""
import copy
import io
import json
import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from migration import journey_clickstream as JC   # noqa: E402
from migration import journey_paths as JP         # noqa: E402
from migration import journey_synthesis as JS     # noqa: E402
from migration import journey_iq as JI            # noqa: E402


def _soda():
    """A small purchase journey in the stored shape."""
    spine = [
        {'id': 'tam', 'label': 'US population', 'accounts': 329900000, 'where': 'United States', 'surface': '', 'kept': 100.0, 'dropped': 0, 'ofUs': 100.0},
        {'id': 'discovery', 'label': 'Saw soda content online', 'doing': 'Scrolled past a soda post', 'accounts': 178904778,
         'where': 'TikTok, Instagram, YouTube, Google, retailer apps', 'surface': 'Social feeds, search results'},
        {'id': 'engaged', 'label': 'Clicked or interacted with the content', 'doing': 'Tapped a post', 'accounts': 8408525,
         'where': 'Same platforms as discovery plus brand sites', 'surface': 'Post detail pages'},
        {'id': 'searched', 'label': 'Opened a soda listing or added soda to a cart', 'doing': 'Typed a soda query', 'accounts': 2304777,
         'where': 'Google, Amazon, Walmart.com, Instacart, brand sites', 'surface': 'Search results, product detail pages'},
        {'id': 'carted', 'label': 'Bought soda in an online order', 'doing': 'Checked out', 'accounts': 1309805,
         'where': 'Amazon, Walmart.com, Instacart, DoorDash, Uber Eats', 'surface': 'Shopping carts'},
    ]
    fork = [{'id': 'abandoned', 'label': 'Left without buying', 'accounts': 1050840}, {'id': 'retargeted', 'label': 'Saw a retarget', 'accounts': 417063},
            {'id': 'returned', 'label': 'Came back', 'accounts': 152645}, {'id': 'paid_return', 'label': 'Bought after coming back', 'accounts': 55868},
            {'id': 'paid_first', 'label': 'Bought in the same session', 'accounts': 1253937}]
    detours = [{'title': 'First soda content they saw', 'kind': 'partition', 'base': 178904778,
                'rows': [{'label': 'Creator taste test', 'accounts': 49306157, 'pct': 27.56}, {'label': 'Brand flavor drop post', 'accounts': 129598621, 'pct': 72.44}]}]
    return {
        'meta': {'story_mode': 'fragrance_shop_journey', 'target_name': 'Online Soda Shoppers', 'target_display': 'Online Soda Shoppers',
                 'project_name': 'Online Soda Shoppers', 'customer_brand': 'Online Soda Shoppers', 'product': 'soda',
                 'platform_label': 'Amazon, Walmart, Instacart, Uber Eats and DoorDash', 'start_date': '2025-10-01', 'end_date': '2026-10-20',
                 'target_type': 'purchase_journey', 'journey_kind': 'purchase', 'category': 'online soda purchase'},
        'fragrance_shop_journey': {'meta': {'window': '2025-10-01 to 2026-10-20', 'unit': 'unique US accounts', 'usGenPop': 329900000},
                                   'spine': spine, 'fork': fork, 'detours': detours, 'copy': {'titleHtml': 'Online Soda Shoppers.', 'clickSec': '04 Clickstream'}},
        'facts': [], 'kpis': {'total_users': 1309805, 'conversion_pct': 0.7321, 'share_of_us_pct': 0.397, 'also_watching': 3757006},
        'who_they_are': {'gender': [{'label': 'Female', 'pct': 53.27}, {'label': 'Male', 'pct': 46.73}],
                         'age': [{'label': '18 to 24', 'pct': 14.83}, {'label': '25 to 34', 'pct': 23.47}, {'label': '35 to 44', 'pct': 22.18}],
                         'income': [{'label': 'Under $35,000', 'pct': 18.63}, {'label': '$35,000 to $54,999', 'pct': 21.47}]},
        'the_read': {'cards': [], 'moves': []},
    }


def _watch():
    p = _soda()
    p['meta'].update({'target_name': 'Babylon 5 on Prime Video', 'target_display': 'Babylon 5 - Prime Video journey', 'project_name': 'Babylon 5 on Prime Video',
                      'customer_brand': 'Warner Bros', 'product': None, 'platform_label': 'Prime Video', 'target_type': 'watch_journey', 'journey_kind': 'watch'})
    sp = p['fragrance_shop_journey']['spine']
    sp[1].update({'label': 'Saw a social post about Babylon 5', 'where': 'TikTok, Instagram, YouTube'})
    sp[2].update({'label': 'Clicked through from the post to the title', 'where': 'Same platforms'})
    sp[3].update({'label': 'Opened the Babylon 5 page on Prime Video', 'where': 'Prime Video'})
    sp[4].update({'label': 'Watched an episode within 30 days of the post', 'where': 'Prime Video'})
    return p


class Clickstream(unittest.TestCase):
    def test_every_journey_carries_it_and_a_purchase_is_a_retail_read(self):
        self.assertTrue(JC.clickstream_allowed('Online Soda Shoppers', 'Amazon'))
        p = _soda()
        JC.attach_clickstream(p, {}, {}, check_urls=False)
        cs = p['fragrance_shop_journey']['clickstream']
        self.assertIs(cs, p['clickstream'])
        self.assertEqual(cs['family'], 'retail')
        self.assertTrue(cs.get('built_at'))
        steps = cs['steps']
        self.assertTrue(JC.MIN_STEPS <= len(steps) <= JC.MAX_STEPS)
        blob = json.dumps(steps).lower()
        self.assertIn('amazon.com/s?k=soda', blob)
        self.assertIn('walmart.com/search?q=soda', blob)
        for bad in ('imdb.com', 'fandango', 'gp/video', 'netflix.com', 'amctheatres'):
            self.assertNotIn(bad, blob, bad)
        # strictly decreasing people, 6 to 10 pages a step, never the same list twice, short surfaces
        people = [s['people'] for s in steps]
        self.assertEqual(people, sorted(people, reverse=True))
        self.assertEqual(len(set(people)), len(people))
        lists = set()
        for s in steps:
            self.assertTrue(JC.MIN_URLS <= len(s['urls']) <= JC.MAX_URLS, len(s['urls']))
            self.assertLessEqual(len(s['surface']), 28)
            lists.add(tuple(u['url'] for u in s['urls'][:4]))
            for u in s['urls']:
                self.assertTrue(JC.is_safe_url(u['url']))
                self.assertLessEqual(u['people'], s['people'])
            self.assertNotEqual(s['people'] % 10, 0)
        self.assertEqual(len(lists), len(steps))
        dates = [s['date'] for s in steps]
        self.assertEqual(dates, sorted(dates))
        self.assertTrue(all(re.match(r'^\d{4}-\d{2}-\d{2}$', d) for d in dates))
        self.assertTrue('2025-10-01' <= dates[0] and dates[-1] <= '2026-10-20')

    def test_watch_journey_keeps_the_title_catalog(self):
        p = _watch()
        JC.attach_clickstream(p, {}, {}, check_urls=False)
        cs = p['clickstream']
        self.assertNotIn('family', cs)
        self.assertTrue(cs['steps'])
        # the subject is the title, not the project name with the platform in it
        self.assertIn('babylon+5', cs['steps'][0]['urls'][0]['url'].lower())
        self.assertNotIn('babylon+5+on+prime', json.dumps(cs).lower())
        self.assertEqual(JC._subject_from_meta(p['meta']), 'Babylon 5')
        self.assertEqual(JC._subject_from_meta({'target_name': 'Online Soda Shoppers on Amazon', 'platform_label': 'Amazon'}), 'Online Soda Shoppers')

    def test_retail_context_is_scoped(self):
        with JC.retail_context('soda', 'Amazon, Walmart'):
            self.assertEqual(JC.step_family('Amazon', 'Opened a listing'), 'retail')
            rows = JC.retail_urls_for_step('soda', 'TikTok', 'Watched a taste test')
            self.assertIn('tiktok.com/search?q=soda', rows[0][0])
            rows2 = JC.retail_urls_for_step('soda', 'Walmart', 'Added soda to the cart')
            self.assertIn('walmart.com/search?q=soda', rows2[0][0])
        self.assertFalse(JC._CTX['retail'])
        self.assertNotEqual(JC.step_family('Amazon', 'Paid for the episode'), 'retail')

    def test_url_check_off_in_tests_and_drop_only_on_404(self):
        self.assertFalse(JC._url_check_enabled())
        cs = {'steps': [{'step': 1, 'people': 100, 'urls': [{'url': 'https://example.com/a'}]}]}
        self.assertIs(JC.prune_dead_urls(cs), cs)
        self.assertEqual(len(cs['steps'][0]['urls']), 1)


class Pathways(unittest.TestCase):
    def test_fallback_paths_hold_the_arithmetic(self):
        p = _soda()
        JC.attach_clickstream(p, {}, {}, check_urls=False)
        JP.attach_paths(p, None, {})
        pw = p['fragrance_shop_journey']['pathways']
        self.assertEqual(pw, p['pathways'])
        self.assertEqual(pw['base'], 1309805)
        self.assertEqual(pw['base_label'], 'Buyers')
        self.assertEqual(pw['depth_label'], 'ordered again')
        seqs = pw['sequences']
        self.assertTrue(JP.MIN_PATHS <= len(seqs) <= JP.MAX_PATHS)
        counts = [s['viewers'] for s in seqs]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(len(set(counts)), len(counts))
        self.assertEqual(sum(counts) + pw['other'], pw['base'])
        self.assertTrue(JP.COVERED_LO - 1 <= pw['covered_pct'] <= JP.COVERED_HI + 1, pw['covered_pct'])
        self.assertGreater(pw['other'], 0)
        depths, days = set(), set()
        for s in seqs:
            self.assertEqual(len(s['steps']), len(s['surfaces']))
            self.assertEqual(len(s['steps']), len(s['kinds']))
            self.assertEqual(len(s['between']), len(s['steps']) - 1)
            self.assertEqual(s['kinds'][-1], 'end')
            self.assertEqual(s['steps'][-1], 'Bought soda in an online order')
            self.assertNotIn(int(round(s['depth_pct'] * 10)) % 10, (0, 5))
            self.assertNotIn(int(round(s['avg_days'] * 10)) % 10, (0, 5))
            depths.add(s['depth_pct'])
            days.add(s['avg_days'])
            self.assertNotEqual(s['viewers'] % 10, 0)
            for b in s['between']:
                self.assertTrue(b['apps'])
                self.assertTrue(b['gap'])
            self.assertNotIn('Web', s['surfaces'])
        self.assertEqual(len(depths), len(seqs))
        self.assertEqual(len(days), len(seqs))
        # the leave-and-return path is there
        self.assertTrue(any('Came back' in ' '.join(s['steps']) for s in seqs))

    def test_model_paths_are_normalized_not_trusted(self):
        p = _soda()
        raw = {'sequences': [
            {'rank': 1, 'share_of_base_pct': 70.0, 'depth_pct': 40.0, 'avg_days': 5.0,
             'steps': ['Saw a taste test', 'Searched Amazon', 'Bought a 12-pack'], 'surfaces': ['TikTok', 'Amazon', 'Amazon'],
             'between': [{'days': 1, 'apps': ['Gmail']}, {'days': 2, 'apps': ['Venmo', 'Maps']}], 'note': 'Main route.'},
            {'rank': 2, 'share_of_base_pct': 70.0, 'depth_pct': 40.0, 'steps': ['Saw a deal post', 'Opened Walmart', 'Bought'], 'surfaces': ['Instagram', 'Walmart', 'Walmart']},
            {'rank': 3, 'share_of_base_pct': 30.0, 'steps': ['Searched soda', 'Opened Instacart', 'Bought'], 'surfaces': ['Google', 'Instacart', 'Instacart']},
            {'rank': 4, 'share_of_base_pct': 20.0, 'steps': ['Saw it on Reddit', 'Opened Amazon', 'Bought'], 'surfaces': ['Reddit', 'Amazon', 'Amazon']},
            {'rank': 5, 'share_of_base_pct': 1.0, 'steps': ['two', 'steps']},          # too short: dropped
        ], 'other_note': 'Smaller routes.'}
        pw = JP.normalize_pathways(raw, p, {}, 'seed')
        self.assertEqual(len(pw['sequences']), 4)
        counts = [s['viewers'] for s in pw['sequences']]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertLess(sum(counts), pw['base'])
        self.assertTrue(JP.COVERED_LO - 1 <= pw['covered_pct'] <= JP.COVERED_HI + 1)
        self.assertEqual(len({s['depth_pct'] for s in pw['sequences']}), 4)      # two 40.0s became distinct messy rates
        self.assertEqual(pw['sequences'][0]['between'][1]['apps'], ['Venmo', 'Maps'])
        self.assertEqual(len(pw['sequences'][1]['between']), 2)                  # missing between filled
        self.assertEqual(pw['note'], 'Smaller routes.')
        self.assertEqual(JP.normalize_pathways({'sequences': raw['sequences'][:2]}, p, {}, 'seed'), {})   # under MIN_PATHS: derive instead


class Person(unittest.TestCase):
    def test_fallback_walk(self):
        p = _soda()
        JC.attach_clickstream(p, {}, {}, check_urls=False)
        JP.attach_paths(p, None, {})
        w = p['fragrance_shop_journey']['individual_journey']
        self.assertEqual(w, p['individual_journey'])
        self.assertEqual(w['section_label'], 'One shopper')
        self.assertTrue(w['label'].startswith('One shopper, '))
        steps = w['steps']
        self.assertTrue(JP.MIN_WALK <= len(steps) <= JP.MAX_WALK)
        self.assertEqual([s['kind'] for s in steps].count('key'), 1)
        self.assertEqual(steps[-1]['kind'], 'end')
        self.assertEqual(steps[w['key_step'] - 1]['kind'], 'key')
        iso = [s['date_iso'] for s in steps]
        self.assertEqual(iso, sorted(iso))
        self.assertTrue('2025-10-01' <= iso[0] and iso[-1] <= '2026-10-20')
        for s in steps:
            self.assertTrue(re.match(r'^[A-Z][a-z]{2} \d{1,2}, \d{4}$', s['date']), s['date'])
            for c in s['clips']:
                self.assertTrue(JC.is_safe_url(c['url']))
        # everyday apps link to their own home; a retailer step to the product's listing search
        venmo = [s for s in steps if s['surface'] == 'Venmo']
        if venmo:
            self.assertEqual(venmo[0]['clips'][0]['url'], 'https://venmo.com/')
        amazon = [s for s in steps if s['surface'] == 'Amazon']
        self.assertTrue(amazon)
        self.assertIn('amazon.com/s?k=soda', amazon[0]['clips'][0]['url'])
        self.assertEqual(w['demographics']['gender'], 'Female')
        self.assertTrue(18 <= w['demographics']['age'] <= 80)
        self.assertEqual(len(w['person_steps']), len(steps))

    def test_model_walk_dates_outside_the_window_are_rebuilt_and_links_cleaned(self):
        p = _soda()
        raw = {'label': 'One shopper, Online Soda Shoppers', 'demographics': {'age': 31, 'gender': 'Female', 'location': 'Tampa', 'income': '$55K to $75K', 'household': 'Couple, no children'},
               'outcome': 'Bought a 12-pack and came back for a second.', 'spend_usd': 18.0,
               'steps': [{'date': '2024-01-01', 'surface': 'TikTok', 'action': 'Watched a ranking clip', 'links': [{'label': 'TikTok', 'url': 'https://www.tiktok.com/@someone/video/7123456789'}]},
                         {'date': '2024-01-02', 'surface': 'Amazon', 'action': 'Searched soda on Amazon', 'links': [{'label': 'Amazon', 'url': 'https://www.amazon.com/s?k=soda'}]},
                         {'date': 'bad', 'surface': 'Amazon', 'action': 'Opened a listing'},
                         {'date': '2024-01-04', 'surface': 'Amazon', 'action': 'Bought a 12-pack', 'kind': 'key'},
                         {'date': '2024-01-05', 'surface': 'Gmail', 'action': 'Opened the confirmation'},
                         {'date': '2024-01-20', 'surface': 'Amazon', 'action': 'Ordered again'}]}
        w = JP.normalize_person(raw, p, {}, 'seed')
        self.assertEqual(len(w['steps']), 6)
        iso = [s['date_iso'] for s in w['steps']]
        self.assertEqual(iso, sorted(iso))
        self.assertTrue(all('2025-10-01' <= d <= '2026-10-20' for d in iso))
        self.assertEqual(w['key_step'], 4)
        self.assertEqual(w['steps'][-1]['kind'], 'end')
        first_links = [c['url'] for c in w['steps'][0]['clips']]
        self.assertFalse(any('/video/' in u for u in first_links))      # an invented TikTok video id never ships
        self.assertTrue(first_links)                                     # replaced by a public page
        self.assertNotEqual(w['spend_usd'], 18.0)                        # a round dollar figure gets its cents
        self.assertEqual(JP.normalize_person({'steps': [{'action': 'x'}] * 3}, p, {}, 'seed'), {})


class Wiring(unittest.TestCase):
    def test_reconcile_mirrors_the_blocks_and_the_workbook_has_the_sheets(self):
        p = _soda()
        JC.attach_clickstream(p, {}, {}, check_urls=False)
        JP.attach_paths(p, None, {})
        p['pathways']['note'] = 'changed on top'
        JS.reconcile_payload(p)
        self.assertEqual(p['fragrance_shop_journey']['pathways']['note'], 'changed on top')
        self.assertEqual(p['fragrance_shop_journey']['individual_journey'], p['individual_journey'])
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(JI.breakdown_xlsx(p)))
        self.assertEqual(wb.sheetnames, ['Breakdown', 'About', 'Pathways', 'One shopper', 'Clickstream'])
        self.assertEqual(wb['Pathways']['A1'].value, 'Path')
        self.assertEqual(wb['Pathways'].max_row, len(p['pathways']['sequences']) + 2)
        self.assertEqual(wb['Clickstream'].max_row, 1 + sum(len(s['urls']) for s in p['clickstream']['steps']))
        about = ' '.join(str(c.value or '') for row in wb['About'].iter_rows() for c in row)
        self.assertIn('Pathways', about)
        self.assertIn('One viewer', about)

    def test_attach_is_idempotent_unless_forced(self):
        p = _soda()
        JP.attach_paths(p, None, {})
        before = copy.deepcopy(p['pathways'])
        JP.attach_paths(p, None, {})
        self.assertEqual(p['pathways'], before)
        self.assertTrue(JP.has_paths(p))

    def test_prompt_input_is_compact_and_names_the_read(self):
        p = _soda()
        pi = JP.prompt_input(p, {'subject': 'Online Soda Shoppers', 'platform': 'Amazon'})
        self.assertEqual(pi['journey_kind'], 'purchase')
        self.assertEqual(pi['base_label'], 'buyers')
        self.assertEqual(pi['depth_label'], 'ordered again')
        self.assertEqual(pi['end_event'], 'Bought soda in an online order')
        self.assertEqual(len(pi['spine']), 4)
        self.assertEqual(pi['window'], {'start': '2025-10-01', 'end': '2026-10-20'})

    def test_a_fresh_build_carries_all_three_blocks(self):
        os.environ['JOURNEY_CORPUS_ANCHORS'] = '0'
        os.environ['JOURNEY_NORMS'] = '0'
        prim = {
            'customer_brand': 'Ghost', 'product': 'Ghost Energy Drink', 'category': 'Energy drinks', 'title_status': 'catalog',
            'audience_age_skew': 'younger', 'post_type': 'mixed', 'following': 'mainstream', 'facts': [{'label': 'Window', 'value': 'x'}],
            'nest': [
                {'id': 'saw', 'label': 'Saw Ghost Energy content in feed', 'doing': 'Scrolled past a clip', 'where': 'TikTok, Instagram, YouTube', 'share_of_tam_pct': 7.2},
                {'id': 'engaged', 'label': 'Engaged with the post', 'doing': 'Tapped it', 'where': 'Same platforms', 'kept_of_prior_pct': 4.3},
                {'id': 'searched', 'label': 'Looked Ghost up by name', 'doing': 'Typed Ghost', 'where': 'Google, Amazon, Walmart', 'kept_of_prior_pct': 33.8},
                {'id': 'cart', 'label': 'Added Ghost to an online cart', 'doing': 'Carted it', 'where': 'Amazon, Walmart, Instacart', 'kept_of_prior_pct': 28.3},
                {'id': 'paid', 'label': 'Paid for Ghost in an online grocery order', 'doing': 'Checked out', 'where': 'Amazon, Walmart, ghostlifestyle.com', 'kept_of_prior_pct': 56.4},
            ],
            'fork': {'enabled': True, 'abandoned_of_stage_pct': 44.7, 'retargeted_of_abandoned_pct': 54.2, 'returned_of_retargeted_pct': 26.4,
                     'paid_return_of_returned_pct': 17.7, 'surfaces': {}, 'timings': {}},
            'detours': [{'title': 'First Ghost content they saw', 'kind': 'partition', 'of': 'stage:saw',
                         'rows': [{'label': 'Flavor clip', 'pct': 61.3, 'doing': ''}, {'label': 'Collab post', 'pct': 38.7, 'doing': ''}]}],
            'anchors_note': '', 'cover_title': 'Ghost Energy Drink',
        }
        calls = []

        def fake(system, user, **kw):
            calls.append(kw.get('surface'))
            return prim if kw.get('surface') in ('journey_synthesis', 'journey_synthesis_retry') else {}
        inputs = {'subject': 'Ghost Energy Drink', 'platform': 'online grocery', 'journey_kind': 'purchase', 'start_date': '2025-10-16', 'end_date': '2026-10-16'}
        p = JS.synthesize(inputs, fake, tools=None, created_by='test')
        b = p['fragrance_shop_journey']
        self.assertIn('journey_paths', calls)
        self.assertEqual((b['clickstream'] or {}).get('family'), 'retail')
        self.assertTrue(JP.MIN_PATHS <= len(b['pathways']['sequences']) <= JP.MAX_PATHS)
        self.assertTrue(JP.MIN_WALK <= len(b['individual_journey']['steps']) <= JP.MAX_WALK)
        self.assertEqual(b['individual_journey']['section_label'], 'One shopper')
        self.assertEqual(b['pathways']['end_short'], 'the order')
        self.assertEqual(p['pathways'], b['pathways'])
        end = b['spine'][-1]['accounts']
        self.assertEqual(b['pathways']['base'], end)
        self.assertIn(end, [s['people'] for s in b['clickstream']['steps']])   # a spine step keeps its count on the clickstream

    def test_no_method_words_reach_the_reader(self):
        p = _soda()
        JC.attach_clickstream(p, {}, {}, check_urls=False)
        JP.attach_paths(p, None, {})
        JS.scrub_payload_text(p)
        blob = json.dumps({'a': p['pathways'], 'b': p['individual_journey']}).lower()
        for bad in ('synth', 'modeled', 'estimate', 'panel', 'cohort', 'partition', 'fallback', 'derived'):
            self.assertNotIn(bad, blob, bad)
        self.assertNotIn('\u2014', blob)


if __name__ == '__main__':
    unittest.main(verbosity=1)
