#!/usr/bin/env python3
"""The counting block and the pool noun fit the read's family
(2026-11-06). Hermetic: no model, no S3, no mail.

A holiday toy shopping read (admin, thread 2b1e503248, job 4706e7014b72)
shipped with 'What we count as a view', 'Threshold', 'Carriage paths' and
'Sessions' under it, and every pool in its table read 'viewers'. The only
viewing word in the read was that pool noun.

Covered here:
  1. the family map: the lane's metric_family / the ledger family picks
     the block; 'strategy' and an empty stamp leave the words to decide
  2. a viewing read keeps the view, threshold, carriage and session lines
  3. a shopping read carries the order definition, never the view lines;
     a sessions mention keeps the shared Sessions line
  4. an engagement read carries the engagement definition
  5. a sizing read carries the people, window and base lines only
  6. unstamped replies keep the old trigger (a viewing figure draws the
     viewing block; nothing else draws a block)
  7. pool_noun: viewers / people / buyers / shoppers / listeners /
     readers / players / subscribers; the toy breakdown says buyers and
     people, a streaming breakdown still says viewers, a podcast read says
     listeners; the ledger block and the CSV header carry the same noun
  8. the reasoning client's credentials line is one plain sentence with
     no product or client names, and the old line is gone
  9. the error alert body names the request's origin; a sessionless
     alert says operator process; the old line never appears
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'AKIATESTNOTREAL000000')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'not-a-real-secret-for-the-counting-family-test')
os.environ.setdefault('AWS_SHARED_CREDENTIALS_FILE', '/nonexistent/aws/credentials')
os.environ.setdefault('AWS_CONFIG_FILE', '/nonexistent/aws/config')

from prometheus import methodology as M  # noqa: E402
import prometheus_analysis as pma  # noqa: E402
import insights_ledger as IL  # noqa: E402

OLD_LINE = 'Reasoning service is not enabled on this server'
NEW_LINE = 'Reasoning credentials are not configured in this process.'
BANNED = re.compile(r"\b(?:synthetic|model(?:ed)?|estimated|panel|sample|pipeline|research(?:ed)?|ledger|lane)\b", re.I)
WIN = 'Nov 1 2025 to Nov 1 2026'

TOY_Q = 'What toy categories do parents of Paw Patrol viewers aged 4 to 7 buy for the holidays, by price band?'


def toy_read():
    return {
        'subject': 'Paw Patrol', 'cohort': 'parents of viewers aged 4 to 7', 'metric_family': 'purchases',
        'window_label': WIN, 'window_start': '2025-11-01', 'window_end': '2026-11-01',
        'headline': 'Holiday toy buying among parents of Paw Patrol viewers leans to building sets.',
        'metrics': [{'name': 'holiday_toy_buyers', 'label': 'Holiday toy buyers', 'value': 1234567, 'unit': 'people',
                     'definition': 'US parents of Paw Patrol viewers aged 4 to 7 with at least one toy order in the window'}],
        'breakdown': {'dimension': 'Toy category', 'share_basis': 'share of holiday toy buyers', 'rows': [
            {'label': 'Building sets', 'share_pct': 31.2, 'pool': 385123,
             'buckets': [{'label': 'Under $25', 'count': 200001, 'share_pct': 51.9}, {'label': '$25 or more', 'count': 185122, 'share_pct': 48.1}]},
            {'label': 'Dolls and figures', 'share_pct': 24.1, 'pool': 297531,
             'buckets': [{'label': 'Under $25', 'count': 180003, 'share_pct': 60.5}, {'label': '$25 or more', 'count': 117528, 'share_pct': 39.5}]},
            {'label': 'Vehicles', 'share_pct': 20.3, 'pool': 250617,
             'buckets': [{'label': 'Under $25', 'count': 150001, 'share_pct': 59.9}, {'label': '$25 or more', 'count': 100616, 'share_pct': 40.1}]},
            {'label': 'Games and puzzles', 'share_pct': 24.4, 'pool': 301296,
             'buckets': [{'label': 'Under $25', 'count': 200003, 'share_pct': 66.4}, {'label': '$25 or more', 'count': 101293, 'share_pct': 33.6}]},
        ]},
        'reads': ['Building sets carry the biggest share of holiday toy orders.'],
    }


def streaming_read():
    return {
        'subject': 'Yellowstone', 'cohort': '', 'metric_family': 'viewership',
        'window_label': WIN, 'window_start': '2025-11-01', 'window_end': '2026-11-01',
        'headline': 'Yellowstone viewers on Paramount+ split by how they found the title.',
        'metrics': [{'name': 'us_viewers', 'label': 'US viewers of Yellowstone on Paramount+', 'value': 8437219, 'unit': 'viewers',
                     'definition': 'distinct US people with at least one play in the window'}],
        'breakdown': {'dimension': 'Discovery path', 'share_basis': 'share of Yellowstone viewers', 'rows': [
            {'label': 'Home screen row', 'share_pct': 41.3, 'pool': 3484571,
             'buckets': [{'label': 'Started a title', 'count': 2984571, 'share_pct': 85.7}, {'label': 'Browse only', 'count': 500000, 'share_pct': 14.3}]},
            {'label': 'Search', 'share_pct': 33.6, 'pool': 2834906,
             'buckets': [{'label': 'Started a title', 'count': 2534906, 'share_pct': 89.4}, {'label': 'Browse only', 'count': 300000, 'share_pct': 10.6}]},
            {'label': 'Autoplay after another title', 'share_pct': 25.1, 'pool': 2117742,
             'buckets': [{'label': 'Started a title', 'count': 1917742, 'share_pct': 90.6}, {'label': 'Browse only', 'count': 200000, 'share_pct': 9.4}]},
        ]},
    }


def podcast_read():
    return {
        'subject': 'Call Her Daddy', 'cohort': '', 'metric_family': 'viewership',
        'window_label': WIN, 'window_start': '2025-11-01', 'window_end': '2026-11-01',
        'headline': 'Call Her Daddy listeners split by where they listen.',
        'metrics': [{'name': 'us_listeners', 'label': 'US listeners of Call Her Daddy', 'value': 5214877, 'unit': 'listeners',
                     'definition': 'distinct US people with at least one episode play in the window'}],
        'breakdown': {'dimension': 'Listening app', 'share_basis': 'share of listeners', 'rows': [
            {'label': 'Spotify', 'share_pct': 58.2, 'pool': 3035058,
             'buckets': [{'label': 'Weekly', 'count': 2035058, 'share_pct': 67.1}, {'label': 'Less often', 'count': 1000000, 'share_pct': 32.9}]},
            {'label': 'Apple Podcasts', 'share_pct': 30.1, 'pool': 1569678,
             'buckets': [{'label': 'Weekly', 'count': 1000001, 'share_pct': 63.7}, {'label': 'Less often', 'count': 569677, 'share_pct': 36.3}]},
            {'label': 'YouTube', 'share_pct': 11.7, 'pool': 610141,
             'buckets': [{'label': 'Weekly', 'count': 400001, 'share_pct': 65.6}, {'label': 'Less often', 'count': 210140, 'share_pct': 34.4}]},
        ]},
    }


def engagement_read():
    return {
        'subject': 'Nike', 'cohort': '', 'metric_family': 'search',
        'window_label': WIN, 'window_start': '2025-11-01', 'window_end': '2026-11-01',
        'headline': '21,406,311 US people searched for Nike or touched it on social in the window.',
        'metrics': [{'name': 'engaged', 'label': 'US people with a Nike search or social touch', 'value': 21406311, 'unit': 'people',
                     'definition': 'distinct US people with at least one Nike search or social touch in the window'},
                    {'name': 'share', 'label': 'Share of US adults', 'value': 8.2, 'unit': 'pct'}],
    }


def sizing_read():
    return {
        'subject': 'Jason Statham', 'cohort': '', 'metric_family': 'audience',
        'window_label': WIN, 'window_start': '2025-11-01', 'window_end': '2026-11-01',
        'headline': 'The Jason Statham audience runs 99,115,849 US people in the window.',
        'metrics': [{'name': 'audience', 'label': 'Jason Statham audience', 'value': 99115849, 'unit': 'people',
                     'definition': 'US people with at least one digital touchpoint with Jason Statham in the window'}],
    }


def _reply(res):
    return pma.format_generated_metrics_reply(pma.enforce_metrics_coherence(res))


def _block_of(out):
    return out[out.index(M.HEADER):] if M.HEADER in out else ''


class FamilyMap(unittest.TestCase):
    def test_lane_and_ledger_families(self):
        for raw, want in (('viewership', M.VIEWING), ('consumption', M.VIEWING), ('journey', M.VIEWING), ('streaming', M.VIEWING),
                          ('purchases', M.PURCHASE), ('shopping', M.PURCHASE), ('revenue', M.PURCHASE),
                          ('search', M.ENGAGEMENT), ('engagement', M.ENGAGEMENT), ('social', M.ENGAGEMENT),
                          ('audience', M.SIZING), ('sizing', M.SIZING), ('subscribers', M.SIZING), ('profile_read', M.SIZING),
                          ('dimension split', M.SIZING), ('cohort churn', M.SIZING), ('subscriber_acquisition', M.SIZING),
                          ('brand_partnership', M.SIZING), (M.VIEWING, M.VIEWING), (M.PURCHASE, M.PURCHASE)):
            self.assertEqual(M.canon_counting_family(raw), want, raw)
        for raw in ('', None, 'strategy'):
            self.assertEqual(M.canon_counting_family(raw), '', repr(raw))
        self.assertEqual(IL.canon_family('purchases'), 'purchases')
        self.assertEqual(M.stamped_family({'family': IL.canon_family('shop')}), M.PURCHASE)
        self.assertEqual(M.stamped_family({'metric_family': 'viewership'}), M.VIEWING)
        self.assertEqual(M.stamped_family({'_family': 'strategy'}), '')
        self.assertEqual(M.stamped_family({}), '')

    def test_read_family_falls_to_the_words_only_when_nothing_is_stamped(self):
        self.assertEqual(M.read_family(toy_read(), 'Paw Patrol viewers: 1,234,567 bought toys', TOY_Q), M.PURCHASE)
        self.assertEqual(M.read_family({}, '8,437,219 US viewers of Yellowstone on Paramount+ in the window.', ''), M.VIEWING)
        self.assertEqual(M.read_family({}, 'Where this audience buys: Amazon 84.1% and Walmart 70.5%.', 'where do they shop'), M.PURCHASE)
        self.assertEqual(M.read_family({}, 'The shared audience is 51,131,927 people.', 'overlap'), M.SIZING)


class ViewingRead(unittest.TestCase):
    def test_the_block_keeps_the_viewing_lines(self):
        res = pma.enforce_metrics_coherence(streaming_read())
        reply = pma.format_generated_metrics_reply(res)
        out = M.ensure(reply, res, question='How did Yellowstone viewers on Paramount+ find the title?')
        blk = _block_of(out)
        self.assertIn(f"- Window: {WIN}.", blk)
        self.assertIn('- Base: ', blk)
        self.assertIn(f"- {M.VIEW_HEADER} {M.VIEW_DEFINITION}", blk)
        self.assertIn(f"- Threshold: {M.THRESHOLD}", blk, 'browse-only buckets depend on the title-start threshold')
        self.assertIn(f"- {M.CARRIAGE}", blk)
        self.assertIn(f"- Sessions: {M.SESSION}", blk)
        self.assertNotIn(M.ORDER_HEADER, blk)
        self.assertNotIn(M.ENGAGEMENT_HEADER, blk)
        self.assertNotIn('- People:', blk)
        self.assertIn('Pool 3,484,571 viewers', reply)
        self.assertEqual(M.ensure(out, res, question='x'), out, 'idempotent')

    def test_block_with_no_family_is_the_viewing_block_as_before(self):
        lines = M.block(window_label=WIN, base_label='P-Valley viewers', base_people=4812337, text='who started a title')
        self.assertEqual(lines[0], M.HEADER)
        self.assertIn(f"- Base: P-Valley viewers (4,812,337 US viewers), {M.BASE_SAMPLE}.", lines)
        self.assertIn(f"- {M.VIEW_HEADER} {M.VIEW_DEFINITION}", lines)
        self.assertIn(f"- Threshold: {M.THRESHOLD}", lines)
        self.assertIn(f"- {M.CARRIAGE}", lines)
        self.assertIn(f"- Sessions: {M.SESSION}", lines)


class ShoppingRead(unittest.TestCase):
    def setUp(self):
        self.res = pma.enforce_metrics_coherence(toy_read())
        self.reply = pma.format_generated_metrics_reply(self.res)
        self.out = M.ensure(self.reply, self.res, question=TOY_Q)
        self.blk = _block_of(self.out)

    def test_the_block_carries_the_order_definition_and_no_viewing_line(self):
        self.assertTrue(self.blk, self.out)
        self.assertIn(f"- Window: {WIN}.", self.blk)
        self.assertIn('- Base: ', self.blk)
        self.assertIn(f"- {M.ORDER_HEADER} {M.ORDER_DEFINITION}", self.blk)
        for gone in (M.VIEW_HEADER, 'Threshold:', 'Carriage paths', 'Sessions:', 'Autoplayed previews', 'title start',
                     M.ENGAGEMENT_HEADER, '- People:'):
            self.assertNotIn(gone, self.blk, gone)
        self.assertEqual(self.blk.count(M.ORDER_HEADER), 1)
        self.assertEqual(M.ensure(self.out, self.res, question=TOY_Q), self.out, 'idempotent')
        self.assertIsNone(BANNED.search(self.blk), self.blk)
        self.assertNotIn('\u2014', self.out)

    def test_the_order_definition_says_what_an_order_is(self):
        d = M.ORDER_DEFINITION
        self.assertIn('one person with at least one checkout', d)
        self.assertIn('named the brand or category inside the window', d)
        self.assertIn('counted once', d)
        self.assertIn('without a checkout is not an order', d)
        self.assertIn('Marketplaces and brand sites both count where the brand is attributable', d)
        self.assertIsNone(BANNED.search(d))
        self.assertNotIn('\u2014', d)
        # the order line already says orders are confirmed, so the Most
        # Purchased Brands purchase line is not stacked under it
        self.assertTrue(M.carries_purchase_definition(f"- {M.ORDER_HEADER} {M.ORDER_DEFINITION}"))

    def test_the_toy_breakdown_says_buyers_and_people_never_viewers(self):
        self.assertIn('Pool 385,123 buyers', self.reply)
        self.assertNotIn('viewers', self.reply.replace('parents of viewers aged 4 to 7', '').replace('Paw Patrol viewers', ''))
        self.assertNotRegex(self.reply, r'Pool [\d,]+ viewers')
        self.assertEqual(pma.pool_noun_for_read(self.res, TOY_Q), 'buyers')
        self.assertEqual(M.pool_noun('purchases', 'share of holiday toy buyers'), 'buyers')
        self.assertEqual(M.pool_noun('audience', 'how many parents of Paw Patrol viewers aged 4 to 7 are there'), 'people')
        # the Base line's count noun follows the family too
        lines = M.block(window_label=WIN, base_label='parents of Paw Patrol viewers', base_people=1234567,
                        text=self.reply, family='purchases', noun=pma.pool_noun_for_read(self.res, TOY_Q))
        self.assertIn(f"- Base: parents of Paw Patrol viewers (1,234,567 US buyers), {M.BASE_SAMPLE}.", lines)

    def test_the_sessions_line_rides_only_where_sessions_are_counted(self):
        plain = M.block(window_label=WIN, base_label='toy buyers', text='Pool 385,123 buyers. Under $25: 200,001 (51.9%)', family='purchases')
        self.assertFalse(any(ln.startswith('- Sessions:') for ln in plain))
        with_sessions = M.block(window_label=WIN, base_label='toy buyers', text='2,406,790 checkout sessions in the window', family='purchases')
        self.assertIn(f"- Sessions: {M.SESSION}", with_sessions)

    def test_the_csv_header_and_note_follow_the_family(self):
        entry = {'subject': 'Paw Patrol', 'cohort': 'parents of viewers aged 4 to 7', 'question': TOY_Q, 'family': 'purchases',
                 'metrics': self.res.get('metrics'), 'breakdown': self.res.get('breakdown'), 'ws': '2025-11-01', 'we': '2026-11-01', 'wl': WIN}
        fname, text = pma.build_generated_csv(entry)
        head = text.splitlines()[0]
        self.assertIn('Group pool (buyers)', head)
        self.assertNotIn('Group pool (viewers)', head)
        self.assertNotIn('pool,', head, 'the pool column always names its noun')
        self.assertIn(M.ORDER_HEADER, text)
        self.assertNotIn(M.VIEW_HEADER, text)
        # the stored entry's prompt block reads back with the same noun
        e = IL.make_entry(subject='Paw Patrol', metric_family='purchases', question=TOY_Q, route='reasoned_metrics',
                          metrics=self.res.get('metrics'), window_start='2025-11-01', window_end='2026-11-01', window_label=WIN,
                          reply=self.out, breakdown=self.res.get('breakdown'), cohort='parents of viewers aged 4 to 7')
        blk = IL.render_block([e])
        self.assertIn('(pool 385,123 buyers)', blk)
        self.assertNotIn('viewers)', blk)

    def test_shoppers_when_the_pool_browsed(self):
        self.assertEqual(M.pool_noun('purchases', 'people who browsed toy aisles without a checkout'), 'shoppers')
        self.assertEqual(M.pool_noun('purchases', 'people who browsed toys and then ordered'), 'buyers')
        self.assertEqual(M.pool_noun('purchases', ''), 'buyers')


class EngagementRead(unittest.TestCase):
    def test_the_block_carries_the_engagement_definition(self):
        res = pma.enforce_metrics_coherence(engagement_read())
        reply = pma.format_generated_metrics_reply(res)
        out = M.ensure(reply, res, question='How many people searched for Nike or touched it on social?')
        blk = _block_of(out)
        self.assertTrue(blk, out)
        self.assertIn(f"- Window: {WIN}.", blk)
        self.assertIn('- Base: ', blk)
        self.assertIn(f"- {M.ENGAGEMENT_HEADER} {M.ENGAGEMENT_DEFINITION}", blk)
        self.assertIn('one person with at least one search or social touch', M.ENGAGEMENT_DEFINITION)
        for gone in (M.VIEW_HEADER, 'Threshold:', 'Carriage paths', 'Sessions:', M.ORDER_HEADER, '- People:'):
            self.assertNotIn(gone, blk, gone)
        self.assertEqual(pma.pool_noun_for_read(res), 'people')
        self.assertIsNone(BANNED.search(blk), blk)
        self.assertNotIn('\u2014', out)


class SizingRead(unittest.TestCase):
    def test_the_block_carries_people_window_and_base_only(self):
        res = pma.enforce_metrics_coherence(sizing_read())
        reply = pma.format_generated_metrics_reply(res)
        out = M.ensure(reply, res, question='how big is the Jason Statham audience', base_people=3004421)
        blk = _block_of(out)
        self.assertTrue(blk, out)
        lines = [ln for ln in blk.splitlines() if ln.startswith('- ')]
        self.assertEqual([ln.split(':', 1)[0] for ln in lines], ['- Window', '- Base', '- People'])
        self.assertIn(f"- People: {M.PEOPLE}", blk)
        self.assertIn('(3,004,421 US people)', blk)
        for gone in (M.VIEW_HEADER, 'Threshold:', 'Carriage paths', 'Sessions:', M.ORDER_HEADER, M.ENGAGEMENT_HEADER):
            self.assertNotIn(gone, blk, gone)
        self.assertIsNone(BANNED.search(blk), blk)
        self.assertNotIn('\u2014', out)
        self.assertEqual(M.ensure(out, res, question='x'), out, 'idempotent')

    def test_a_stamped_read_with_no_figure_gets_no_block(self):
        res = {'subject': 'Jason Statham', 'metric_family': 'audience'}
        self.assertEqual(M.ensure('Nothing to size yet.', res, question='q'), 'Nothing to size yet.')


class UnstampedReplies(unittest.TestCase):
    SERIES = ('On HBO Max and Paramount+ overlap:\n\nThe HBO Max and Paramount+ shared audience has climbed every month of '
              'the window, from 45,213,554 people in November 2025 to 51,131,927 at the November 4 2026 read.')

    def test_only_a_viewing_figure_draws_the_viewing_block(self):
        self.assertEqual(M.ensure(self.SERIES, {}, question=''), self.SERIES)
        out = M.ensure('8,437,219 US viewers watched Yellowstone on Paramount+ in the window.', {}, question='')
        self.assertIn(M.VIEW_HEADER, out)
        self.assertIn('Carriage paths', out)
        plain = M.ensure('Where this audience buys: Amazon 84.1% and Walmart 70.5%.', {}, question='where do they shop')
        self.assertTrue(plain.endswith(f"{M.PURCHASE_HEADER} {M.PURCHASE_DEFINITION}"))
        self.assertNotIn(M.HEADER, plain)


class PoolNouns(unittest.TestCase):
    def test_the_noun_per_family_and_medium(self):
        self.assertEqual(M.pool_noun('viewership', 'Yellowstone viewers on Paramount+'), 'viewers')
        self.assertEqual(M.pool_noun('viewership', 'Jeopardy game show viewers'), 'viewers')
        self.assertEqual(M.pool_noun('audience', 'how big is the Jason Statham audience'), 'people')
        self.assertEqual(M.pool_noun('engagement', 'people who searched for Nike'), 'people')
        self.assertEqual(M.pool_noun('purchases', 'holiday toy buyers by category'), 'buyers')
        self.assertEqual(M.pool_noun('viewership', 'Call Her Daddy podcast listeners'), 'listeners')
        self.assertEqual(M.pool_noun('audience', 'readers of the Five Brothers book'), 'readers')
        self.assertEqual(M.pool_noun('viewership', 'Fortnite players by platform'), 'players')
        self.assertEqual(M.pool_noun('subscribers', 'Netflix subscribers who churned'), 'subscribers')
        self.assertEqual(M.pool_noun('audience', 'Paramount+ subscribers by plan'), 'subscribers')
        self.assertEqual(M.pool_noun('', ''), 'people')
        self.assertEqual(M.pool_noun(None, 'Pool of viewers who started a title'), 'viewers')
        self.assertEqual(M.pool_noun('strategy', 'what should we do'), 'people')
        # a bare book, song or game never moves a viewing pool
        self.assertEqual(M.pool_noun('viewership', 'people who watched The Love Hypothesis, the film of the book, on Prime Video'), 'viewers')
        self.assertEqual(M.pool_noun('viewership', 'people who watched the game on Peacock'), 'viewers')
        self.assertEqual(M.pool_noun('viewership', 'Music to Long Form: film viewers who opened the song first'), 'viewers')
        self.assertEqual(M.pool_noun('audience', 'people who read the Five Brothers book'), 'readers')
        self.assertEqual(M.pool_noun('purchases', 'people who bought the book'), 'buyers')
        # a metric unit that names the people is the lane's own word
        self.assertEqual(M.pool_noun_for({'metric_family': 'viewership', 'subject': 'The Love Hypothesis (book)',
                                          'metrics': [{'label': 'US viewers', 'unit': 'viewers', 'value': 1}]}), 'viewers')
        self.assertEqual(M.pool_noun_for({'metric_family': 'audience', 'subject': 'Paramount+',
                                          'metrics': [{'label': 'Subscribers', 'unit': 'subscribers', 'value': 1}]}), 'subscribers')
        self.assertEqual(M.pool_noun_for({'metric_family': 'purchases', 'subject': 'Paw Patrol',
                                          'metrics': [{'label': 'Buyers', 'unit': 'viewers', 'value': 1}]}), 'buyers')

    def test_breakdowns_carry_the_noun(self):
        streaming = _reply(streaming_read())
        self.assertIn('Pool 3,484,571 viewers', streaming)
        podcast = _reply(podcast_read())
        self.assertIn('Pool 3,035,058 listeners', podcast)
        self.assertNotIn('viewers', podcast)
        toy = _reply(toy_read())
        self.assertIn('Pool 385,123 buyers', toy)
        self.assertNotRegex(toy, r'Pool [\d,]+ viewers')

    def test_the_chart_and_the_ledger_use_the_one_helper(self):
        with open(os.path.join(ROOT, 'prometheus', 'charts.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn('pool_noun = _meth.pool_noun_for(res, question)', src)
        self.assertIn('pool_noun=pool_noun)', src)
        self.assertNotIn("} viewers\"", src)
        with open(os.path.join(ROOT, 'insights_ledger.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn('_meth.pool_noun_for(entry', src)
        with open(os.path.join(ROOT, 'prometheus_analysis.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertNotIn('} viewers"', src.split('def format_generated_metrics_reply')[1].split('def missing_pieces_line')[0])
        self.assertIn('return _meth.pool_noun_for(res, question)', src)
        # the read core hands the file builder the family
        with open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("'family': res.get('metric_family')}", src)
        self.assertIn("'family': fam}", src)


class CredentialsLine(unittest.TestCase):
    def test_the_old_line_is_gone_and_the_new_one_names_no_product(self):
        hits = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in ('.git', 'node_modules', '__pycache__', 'static')]
            for fn in filenames:
                if not fn.endswith(('.py', '.html', '.js')) or fn == os.path.basename(__file__):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    with open(p, encoding='utf-8', errors='replace') as fh:
                        s = fh.read()
                except Exception:
                    continue
                if OLD_LINE in s or 'to activate the Netflix ad-sales agents' in s:
                    hits.append(os.path.relpath(p, ROOT))
        self.assertEqual(hits, [])
        with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("_REASONING_CREDENTIALS_MESSAGE = ('Reasoning credentials are not configured '", src)
        self.assertIn("'error': _REASONING_CREDENTIALS_MESSAGE}", src)
        self.assertNotIn('Netflix', NEW_LINE)
        self.assertNotIn('\u2014', NEW_LINE)


class ErrorAlertOrigin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as A  # noqa: WPS433
        cls.A = A

    def setUp(self):
        # the alert dedupes per route + signature for 15 minutes through a
        # module dict and a /tmp stamp file; each test starts clean
        import tempfile
        self._stamp_saved = self.A._CHATBOT_ERROR_STAMP_FILE
        self.A._CHATBOT_ERROR_STAMP_FILE = os.path.join(tempfile.mkdtemp(prefix='pm_alert_'), 'stamps.json')
        self.A._chatbot_error_email_stamps.clear()

    def tearDown(self):
        self.A._CHATBOT_ERROR_STAMP_FILE = self._stamp_saved
        self.A._chatbot_error_email_stamps.clear()
        self.A._CHATBOT_ERROR_EMAIL_CAPTURE = None

    def test_the_client_returns_the_new_line_when_credentials_are_missing(self):
        A = self.A
        self.assertEqual(A._REASONING_CREDENTIALS_MESSAGE, NEW_LINE)
        saved = {k: os.environ.get(k) for k in ('USE_CLAUDE_REASONING', 'ANTHROPIC_API_KEY')}
        try:
            os.environ['USE_CLAUDE_REASONING'] = '0'
            os.environ.pop('ANTHROPIC_API_KEY', None)
            out = A._run_nflx_claude_agent(system_prompt='x', user_prompt='y')
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertFalse(out.get('success'))
        self.assertEqual(out.get('status'), 503)
        self.assertEqual(out.get('error'), NEW_LINE)

    def test_origins(self):
        A = self.A
        saved = os.environ.pop('PM_READ_WORKER', None)
        try:
            self.assertTrue(A._chatbot_error_origin('').startswith('operator process ('))
            self.assertIn(', a job for jenna@crosswalknyc.com', A._chatbot_error_origin('jenna@crosswalknyc.com'))
            self.assertNotIn('a job for', A._chatbot_error_origin('canary:smoke'))
            os.environ['PM_READ_WORKER'] = '1'
            self.assertEqual(A._chatbot_error_origin(''), 'operator process (read worker)')
        finally:
            os.environ.pop('PM_READ_WORKER', None)
            if saved is not None:
                os.environ['PM_READ_WORKER'] = saved
        with A.app.test_request_context('/api/brief-chat/analyze', method='POST', json={'message': 'hi'}):
            self.assertEqual(A._chatbot_error_origin(''), 'web request without a session')
            A.session['username'] = 'jay'
            self.assertEqual(A._chatbot_error_origin('jay'), 'dashboard session')
        with A.app.test_request_context('/api/brief-chat/analyze', method='POST', json={'message': 'hi'},
                                        headers={'X-Crosswalk-API-Key': 'cw_test_key'}):
            self.assertEqual(A._chatbot_error_origin(''), 'partner API')
        with A.app.test_request_context('/api/v1/profiles/run', method='POST', json={'prompt': 'hi'}):
            self.assertEqual(A._chatbot_error_origin(''), 'partner API')

    def test_a_sessionless_alert_names_the_origin_and_never_the_old_line(self):
        A = self.A
        captured = []
        A._CHATBOT_ERROR_EMAIL_CAPTURE = captured
        saved = os.environ.pop('PM_READ_WORKER', None)
        try:
            os.environ['PM_READ_WORKER'] = '1'
            ok = A._chatbot_error_email('brief-chat/analyze', RuntimeError(NEW_LINE), user_email='',
                                        payload={'message': 'which toys do Paw Patrol parents buy'}, tb='Traceback: none')
        finally:
            A._CHATBOT_ERROR_EMAIL_CAPTURE = None
            os.environ.pop('PM_READ_WORKER', None)
            if saved is not None:
                os.environ['PM_READ_WORKER'] = saved
        self.assertTrue(ok)
        self.assertEqual(len(captured), 1)
        body = captured[0]['body']
        self.assertIn('Origin: operator process (read worker)\n', body)
        self.assertLess(body.index('Origin:'), body.index('Question:'))
        self.assertIn('User: (unknown)', body)
        self.assertIn(f"Error: RuntimeError: {NEW_LINE}", body)
        self.assertNotIn(OLD_LINE, body)
        self.assertNotIn('Netflix', body)
        self.assertEqual(captured[0]['to'], list(A._CHATBOT_ERROR_EMAIL_TO))
        self.assertNotIn('\u2014', body)

    def test_a_session_alert_says_dashboard_session(self):
        A = self.A
        captured = []
        A._CHATBOT_ERROR_EMAIL_CAPTURE = captured
        try:
            with A.app.test_request_context('/api/brief-chat/analyze', method='POST', json={'message': 'how many watched'}):
                A.session['username'] = 'jay'
                ok = A._chatbot_error_email('brief-chat/analyze', ValueError('a different failure for the dedupe'))
        finally:
            A._CHATBOT_ERROR_EMAIL_CAPTURE = None
        self.assertTrue(ok)
        self.assertIn('Origin: dashboard session\n', captured[0]['body'])
        self.assertIn('User: jay', captured[0]['body'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
