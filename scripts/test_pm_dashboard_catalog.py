#!/usr/bin/env python3
"""A question about the dashboard itself is answered from the dashboard
map (Jenna 2026-11-23: "should list what products we have within these as
shes asking").

A staff seat asked Prometheus to list every Ranker in the Rankers product
and what each one ranks, whether the dashboard has a Share of Time view,
and which social platforms the Social feed in Trends covers. The message
carried three question marks, so multi_ask split it into three parts; the
first drew "Which audience should I read for this?" and the other two the
stock read line. The product facts had no family for the dashboard's own
products, tabs, views and feeds, and the map's Rankers entry was a
hand-typed list of five tabs when the page had six.

Covers, hermetically (no model, fake S3, no mail):
  * trends_tabs is the one definition: the page's Rankers strip
    (_rankersOrder, the tab labels), the Trends strip and app.py's two
    grant catalogs all read or match it;
  * the dashboard map reads every fact from its own place: the tabs from
    trends_tabs, the social platforms from trends_iq.SOCIAL_PLATFORMS, the
    services inside a tab from trends_iq's platform lists (and the live
    store for Music and Podcasts when a host is bound), the views from the
    map; Share of Time is not a view today; the Rankers entry carries the
    six tabs;
  * the dashboard family fires on the exact words and the variants and
    never on a where-is, a data question, an order or a title;
  * the answer lists each Ranker with what it ranks, says no to a view
    that does not exist, names the social platforms, places a Trends tab
    the words took for a Ranker; no internal word, no dash;
  * multi_ask never splits a dashboard question; the Understanding reads
    it as capability with product evidence and the cue words;
  * through the service door, with the Understanding stamped as the log
    had it (multi_ask) and without, the reply is the answer, the route is
    product_facts, no part is split and no read starts; a where-is stays
    wayfinding; the replay fixtures carry the case.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

import trends_tabs as TT  # noqa: E402
import trends_iq as TIQ  # noqa: E402
from prometheus import dashboard_map as M  # noqa: E402
from prometheus import multi_ask as MA  # noqa: E402
from prometheus import product_facts as PF  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus import understanding as U  # noqa: E402
from prometheus.host import host  # noqa: E402
from scripts._index_page import text as _index_text  # noqa: E402

LIZ = ("This is a question about the Crosswalk dashboard itself, not about a title. Please list every Ranker available in the "
       "dashboard's Rankers product today (each tab or list by name, for example FAST, Streaming or SVOD, Music, Podcasts, Gaming, "
       "Books, YouTube) and what each one ranks. Also say whether the dashboard has a Share of Time view, and which social platforms "
       "the Social feed in Trends covers. If you cannot see the dashboard's menus, say so plainly.")
BANNED = ('synth', 'model', 'panel', 'sample', 'pipeline', 'queue', 'claude', 'hostmap', 'ledger', 'anthropic', 'estimated',
          'scraper', 'snapshot', 'justwatch', 'working on it', 'which audience', '\u2014', '\u2013')

HITS = (
    LIZ, 'what rankers do you have?', 'list every ranker in the dashboard', 'which rankers are in the Rankers product',
    'what tabs does Rankers have', 'what does each ranker rank?', 'what does the FAST ranker rank', 'What boards does Rankers have today?',
    'what are the tabs in the rankers product', 'does the dashboard have a Share of Time view?', 'is there a share of time view',
    'is there a Books ranker?', 'do we have a Gaming board?', 'does the dashboard have a podcasts tab',
    'which social platforms does the Social feed in Trends cover?', 'what platforms does the social tab cover', 'Is there still a Social feed in Trends?',
    'what tabs are in Trends', 'what products are in the SELECT PRODUCT dropdown', 'which products do you have in the dashboard',
    'does crosswalk have a share of time product',
)
MISSES = (
    'where is the rankers tab', 'where do I find trends', 'open rankers', 'show me the books tab',
    'what are the top 10 shows on Netflix this week', 'what is trending on the FAST ranker today', 'what is the number one podcast right now',
    'how big is the Netflix audience', 'how many people watched the Super Bowl', 'pull a profile of Example Talent',
    'value the Example Talent x Northwind partnership', 'what can you do?', 'how far back can we pull a window', 'is Example Series in the library',
    'what does the data show for Example Service churn', 'which platforms do Example Series viewers use most', 'what social platforms do Example Talent fans use',
    'does the audience index high on Hulu', 'how is my list going', 'what is a view',
)


def _clean(tc, text):
    low = text.lower()
    for bad in BANNED:
        tc.assertNotIn(bad, low, (bad, text))


# ------------------------------------------------------------- one definition

class OneDefinition(unittest.TestCase):
    def test_rankers_strip_on_the_page_matches_the_catalog(self):
        page = _index_text(ROOT)
        m = re.search(r"var _rankersOrder = \[([^\]]*)\];", page)
        self.assertIsNotNone(m, 'the Rankers strip order on the page')
        order = re.findall(r"'([a-z_]+)'", m.group(1))
        self.assertEqual(tuple(order), TT.RANKERS_TAB_KEYS)
        i = page.index('var headlineCount = (counts.headlines || 0)')
        block = page[i:page.index('tabs.forEach(function(t) {', i)]      # the strip builder's own tabs array
        labels = dict(re.findall(r"\{ key: '([a-z_]+)',\s+label: '([^']+)'", block))
        for key, label, _ranks in TT.RANKERS_TABS:
            self.assertEqual(labels.get(key), label, key)
        for key, label, _ranks in TT.TRENDS_TABS:
            self.assertEqual(labels.get(key), label, key)
        # the page's rankers set and the catalog agree
        m2 = re.search(r"var _rankersSet = \{([^}]*)\};", page)
        self.assertIsNotNone(m2)
        self.assertEqual(set(re.findall(r"([a-z_]+): 1", m2.group(1))), set(TT.RANKERS_TAB_KEYS))
        # every tab key on the page strip is in one catalog or the other (the Social tab came off the strip 2026-08-20)
        strip_keys = set(labels)
        self.assertEqual(strip_keys, set(TT.RANKERS_TAB_KEYS) | set(TT.TRENDS_TAB_KEYS))
        self.assertNotIn('social', strip_keys)

    def test_app_grant_catalogs_read_the_module(self):
        src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
        self.assertIn('from trends_tabs import TRENDS_TAB_KEYS as _TIQ_TRENDS_TAB_KEYS, RANKERS_TAB_KEYS as _TIQ_RANKERS_TAB_KEYS', src)
        self.assertNotRegex(src, r"_TIQ_RANKERS_TAB_KEYS\s*=\s*\(")
        self.assertNotRegex(src, r"_TIQ_TRENDS_TAB_KEYS\s*=\s*\(")
        # the per-tab card keys app.py strips for a denied tab cover every catalog key
        m = re.search(r"_TIQ_TAB_CARD_KEYS = \{(.*?)\n\}", src, re.S)
        self.assertIsNotNone(m)
        card_keys = set(re.findall(r"^\s*'([a-z_]+)':", m.group(1), re.M))
        self.assertEqual(card_keys | {'watchlist'}, set(TT.RANKERS_TAB_KEYS) | set(TT.TRENDS_TAB_KEYS))   # the watchlist is the reader's own stars, no card group

    def test_the_map_reads_the_catalog_not_a_copy(self):
        self.assertEqual([t['label'] for t in M.rankers_tabs()], [l for _k, l, _r in TT.RANKERS_TABS])
        self.assertEqual([t['label'] for t in M.trends_tabs()], [l for _k, l, _r in TT.TRENDS_TABS])
        rk = M.product('cultureRankerIQ')
        self.assertEqual(tuple(rk['tabs']), tuple(l for _k, l, _r in TT.RANKERS_TABS))
        self.assertIn('YouTube', rk['picker_line'])
        self.assertIn('YouTube', rk['blurb'])
        self.assertEqual(tuple(M.product('trendsIQ')['tabs']), tuple(l for _k, l, _r in TT.TRENDS_TABS))
        src = open(os.path.join(ROOT, 'prometheus', 'dashboard_map.py'), encoding='utf-8').read()
        self.assertNotIn("('FAST', 'Music', 'Podcasts', 'Streaming', 'Gaming')", src)
        self.assertNotIn('FAST, Music, Podcasts, Streaming and Gaming', src)
        self.assertEqual(M.social_platforms(), [l for _s, l, *_r in TIQ.SOCIAL_PLATFORMS])
        self.assertEqual(M.section_services('fast'), [l for _s, l, *_r in TIQ.FAST_PLATFORMS])
        self.assertEqual(M.section_services('streaming'), [l for _s, l, *_r in TIQ.STREAMING_PLATFORMS])
        self.assertIn('Steam most played', M.section_services('gaming'))
        self.assertIn('Meta Quest top free', M.section_services('gaming'))
        self.assertEqual(M.section_services('music'), [])            # the store panel, unbound here
        self.assertFalse(M.social_tab_live())

    def test_share_of_time_is_not_a_view_today(self):
        page = _index_text(ROOT)
        # the option is inside a Jinja comment: the dropdown does not carry it (2026-08-20)
        i = page.index('id="viewNavDropdown"')
        dropdown = page[i:page.index('</select>', i)]
        live = re.sub(r"\{#.*?#\}", '', dropdown, flags=re.S)
        self.assertNotIn('<option value="shareOfTimeIQ"', live)
        self.assertIn('<option value="shareOfTimeIQ"', dropdown)
        self.assertIsNone(M.view_lookup('Share of Time'))
        self.assertIsNone(M.find_product('share of time'))
        for p in M.PRODUCTS:
            self.assertNotEqual(p['value'], 'shareOfTimeIQ')
        # every visible option on the page is on the map, so the map is the dropdown as the page shows it
        for val in re.findall(r'<option value="([A-Za-z]+)"', live):
            self.assertIsNotNone(M.product(val), val)

    def test_view_lookup_knows_tabs_products_and_controls(self):
        self.assertEqual(M.view_lookup('FAST')['product'], 'Rankers')
        self.assertEqual(M.view_lookup('Books')['product'], 'Trends')
        self.assertEqual(M.view_lookup('Pilot Testing')['kind'], 'tab')
        self.assertEqual(M.view_lookup('Rankers')['kind'], 'product')
        self.assertEqual(M.view_lookup('Avid Fan')['kind'], 'feature')
        self.assertIsNone(M.view_lookup('Talent Fit'))


# ------------------------------------------------------------------ the family

class Family(unittest.TestCase):
    def test_fires_on_the_exact_words_and_the_variants(self):
        for t in HITS:
            self.assertEqual(PF.family_of(t), 'dashboard', t)

    def test_never_fires_on_where_is_data_orders_or_titles(self):
        for t in MISSES:
            self.assertNotEqual(PF.family_of(t), 'dashboard', t)

    def test_cues_are_the_words_the_brief_names(self):
        cues = PF.dashboard_cues(LIZ)
        for c in ('dashboard itself', 'list every', 'tabs', 'Rankers product', 'Social feed', 'view'):
            self.assertIn(c, cues)

    def test_multi_ask_never_splits_a_dashboard_question(self):
        self.assertEqual(MA.split(LIZ), [])
        self.assertEqual(MA.split('what rankers do you have? and does the dashboard have a share of time view?'), [])
        # two real asks still split
        self.assertGreaterEqual(len(MA.split('How big is the Example Brand audience, and what are the top five QSRs for Example Series viewers?')), 2)

    def test_understanding_reads_capability_with_product_evidence(self):
        u = U.features(LIZ, None, [], {'text': LIZ})
        self.assertEqual(u.intent, 'capability')
        self.assertGreaterEqual(float(u.confidence), 0.85)
        self.assertIn('product:dashboard', u.evidence)
        self.assertTrue(any(str(e).startswith('dash:') for e in u.evidence), u.evidence)
        # the reader's own reading (multi_ask at 0.75, as the log had it) never displaces the product reading
        u2 = U.absorb_reading(u, {'intent': 'multi_ask', 'confidence': 0.75, 'subjects': []})
        self.assertEqual(u2.intent, 'capability')
        # and the multi_ask lane stands down on it while the product facts lane claims it
        body = U.attach({'text': LIZ}, u2)
        self.assertTrue(U.claims(body, 'capability', lambda: PF.looks_like(LIZ)))
        self.assertFalse(U.claims(body, 'multi_ask', lambda: True))


# ------------------------------------------------------------------ the answer

class Answer(unittest.TestCase):
    def test_the_exact_words(self):
        out = PF.answer(LIZ)
        self.assertEqual(out['family'], 'dashboard')
        reply = out['reply']
        _clean(self, reply)
        self.assertTrue(reply.startswith('Rankers opens from the SELECT PRODUCT dropdown and has 6 tabs across the top.'), reply)
        for key, label, ranks in TT.RANKERS_TABS:
            self.assertIn(f"- {label}: {ranks}.", reply)
        self.assertIn('The services are Netflix, Disney+, Hulu, HBO Max, Prime Video', reply)
        self.assertIn('The services are The Roku Channel, Tubi, Pluto TV', reply)
        self.assertIn('Books is a tab in Trends, not in Rankers.', reply)
        self.assertIn('Share of Time: no. The dashboard does not have a Share of Time view today.', reply)
        self.assertIn('The Social feed: Trends has no Social tab today. Reddit, YouTube and TikTok still feed the Trending people and Movers tabs', reply)
        self.assertNotIn('dropdown holds', reply)          # the products list was not asked for

    def test_a_view_that_does_not_exist_and_one_that_does(self):
        self.assertEqual(PF.answer('does the dashboard have a Share of Time view?')['reply'],
                         'Share of Time: no. The dashboard does not have a Share of Time view today.')
        r = PF.answer('is there a Books ranker?')['reply']
        self.assertTrue(r.startswith('Books: yes. It is a tab in Trends: the best-selling and trending books.'), r)
        self.assertIn('The Rankers tabs are FAST, Music, Podcasts, Streaming, Gaming and YouTube.', r)
        r2 = PF.answer('do we have a Share of Time view or a Talent Fit page?')['reply']
        self.assertIn('Share of Time: no.', r2)
        self.assertIn('Talent Fit: no. The dashboard does not have a Talent Fit page today.', r2)

    def test_the_social_feed_and_the_trends_tabs(self):
        r = PF.answer('which social platforms does the Social feed in Trends cover?')['reply']
        self.assertEqual(r, 'The Social feed: Trends has no Social tab today. Reddit, YouTube and TikTok still feed the Trending people and Movers tabs behind the scenes.')
        r2 = PF.answer('what tabs are in Trends')['reply']
        self.assertTrue(r2.startswith('Trends has 9 tabs across the top.'), r2)
        for _k, label, ranks in TT.TRENDS_TABS:
            self.assertIn(f"- {label}: {ranks}.", r2)

    def test_music_and_podcast_services_come_from_the_live_store_when_bound(self):
        class _H:
            def has(self, name):
                return name == 's3_json'

            def s3_json(self, key, default=None):
                if key.endswith('music_charts.json'):
                    return {'sources': {'spotify': {'label': 'Spotify Daily Top 200 (US)', 'items': [1]},
                                        'apple': {'label': 'Apple Music Top 100 (US)', 'items': [1]},
                                        'empty': {'label': 'Nothing', 'items': []}}}
                return default
        self.assertEqual(M.section_services('music', host=_H()), ['Spotify Daily Top 200 (US)', 'Apple Music Top 100 (US)'])
        r = PF.dashboard_answer('what rankers do you have', host=_H())
        self.assertIn('- Music: ' + TT.tab('music')['ranks'] + '. The services are Spotify Daily Top 200 (US) and Apple Music Top 100 (US).', r)

    def test_products_on_the_dropdown_for_the_seat(self):
        r = PF.answer('what products are in the SELECT PRODUCT dropdown', user={'username': 'x', 'role': 'super_admin'})['reply']
        self.assertTrue(r.startswith('The SELECT PRODUCT dropdown holds Profile IQ, Subscriber IQ'), r)
        self.assertIn('Rankers', r)
        self.assertNotIn('Share of Time', r)


# ------------------------------------------------------------------ the door

class _S3Error(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.response = {'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': status}}


class FakeS3:
    def __init__(self):
        self.objs = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise _S3Error('NoSuchKey', 404)
        return {'Body': io.BytesIO(self.objs[Key]), 'ETag': '"x"'}

    def put_object(self, Bucket, Key, Body, ContentType=None, **kw):
        self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')

    def delete_object(self, Bucket, Key):
        self.objs.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix='', MaxKeys=1000, ContinuationToken=None):
        keys = sorted(k for k in self.objs if k.startswith(Prefix))
        return {'Contents': [{'Key': k, 'ETag': '"x"'} for k in keys], 'IsTruncated': False}


USER = 'staff_seat'
THREADS = 'system/synth_chat_threads'
IDX = {'active': 'thr1', 'threads': [{'id': 'thr1', 'title': 'New chat', 'created': 'x', 'updated': 'x', 'turns': 0}]}


def _bind(s3):
    def s3_json(key, default=None):
        try:
            return json.loads(s3.get_object(Bucket='b', Key=key)['Body'].read().decode('utf-8'))
        except Exception:
            return default

    def s3_put_json(key, obj):
        s3.put_object(Bucket='b', Key=key, Body=json.dumps(obj).encode('utf-8'))

    hints = []
    host.bind(s3_client=s3, bucket='b', s3_json=s3_json, s3_put_json=s3_put_json,
              thread_key=lambda u, tid: f"{THREADS}/{u}/{tid}.json",
              threads_index_key=lambda u: f"{THREADS}/{u}/index.json",
              load_threads_index=lambda u: IDX, thread_title_from=lambda h: 'New chat',
              ask_hint=lambda **kw: hints.append(kw), max_threads=40, read_prefix='system/prometheus_reads/')
    return hints


def _user():
    return {'username': USER, 'role': 'super_admin', 'company': 'Crosswalk', 'has_rankers_iq_access': True, 'has_trends_iq_access': True}


class Door(unittest.TestCase):
    def setUp(self):
        self.hints = _bind(FakeS3())
        self._persist = S._persist_turn
        S._persist_turn = lambda *a, **k: None

    def tearDown(self):
        S._persist_turn = self._persist

    def _ask(self, text, reading=None, **extra):
        body = dict({'text': text, 'client': 'dashboard', 'surface': 'analyze', 'thread_id': 'thr1', 'history': []}, **extra)
        if reading is not None:
            body = U.attach(body, U.Understanding(intent=reading['intent'], confidence=reading['confidence'],
                                                  source=reading.get('source', 'features'), action='answer',
                                                  evidence=list(reading.get('evidence') or []), text_key=U.text_key(text)))
        return S.ask(_user(), body)

    def _is_the_answer(self, env, status):
        self.assertEqual(status, 200)
        self.assertEqual((env.get('decision') or {}).get('reason'), 'product_facts', env.get('decision'))
        raw = env['raw']
        self.assertEqual(raw.get('action'), 'answer')
        _clean(self, raw['reply'])
        self.assertNotIn('parts', raw)
        self.assertNotIn('read_job_id', raw)
        self.assertFalse(raw.get('held'))
        routes = [(h.get('route'), h.get('outcome')) for h in self.hints]
        self.assertIn(('product_facts', 'answered'), routes)
        for r, _o in routes:
            self.assertNotIn(r, ('multi_ask', 'reasoned_metrics'))
        return raw['reply']

    def test_the_exact_words_as_the_log_read_them(self):
        # the Understanding as it stood on the live ask: multi_ask, 0.75, features
        env, status = self._ask(LIZ, reading={'intent': 'multi_ask', 'confidence': 0.75, 'evidence': ['multi']})
        reply = self._is_the_answer(env, status)
        self.assertIn('- YouTube:', reply)
        self.assertIn('Share of Time: no.', reply)
        self.assertIn('Reddit, YouTube and TikTok', reply)
        self.assertNotIn('1)', reply)

    def test_the_exact_words_with_no_understanding(self):
        env, status = self._ask(LIZ)
        reply = self._is_the_answer(env, status)
        self.assertIn('- FAST:', reply)

    def test_variants_through_the_door(self):
        for t in ('what rankers do you have and what does each one rank?', 'does the dashboard have a Share of Time view?',
                  'which social platforms does the Social feed in Trends cover?', 'is there a Books ranker?'):
            env, status = self._ask(t)
            self._is_the_answer(env, status)
        env, status = self._ask('what rankers do you have?', context={'primary': {'name': 'Example Brand', 'category': 'BRAND'}})
        self._is_the_answer(env, status)

    def test_a_where_is_stays_wayfinding(self):
        env, status = self._ask('where is the rankers tab')
        self.assertEqual(status, 200)
        self.assertEqual((env.get('decision') or {}).get('reason'), 'wayfinding')

    def test_the_sanitized_fixtures_carry_the_case(self):
        path = os.path.join(HERE, 'pm_corpus', 'fixtures', 'sanitized_cases.jsonl')
        cases = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip() and not l.startswith('#')]
        qs = {c['question']: c for c in cases}
        self.assertIn(LIZ, qs)
        self.assertEqual(qs[LIZ]['recorded'], {'route': 'product_facts', 'outcome': 'answered'})
        self.assertIn('does the dashboard have a Share of Time view?', qs)
        for c in cases:
            blob = json.dumps(c).lower()
            self.assertNotIn('liz@', blob)
            self.assertNotIn('horizon-sow', blob)


if __name__ == '__main__':
    unittest.main(verbosity=1)
