#!/usr/bin/env python3
"""A count of viewers is a read, never two profile briefs; a seat the
plan covers reads no price on a brief; the Prometheus app finishes what
it can in its own window (Jenna 2026-11-17).

Liz Huszarik (Crosswalk), in the Prometheus app, 2026-11-18 00:20 UTC:

    "I need the Total Viewers to FBI Most Wanted and FBI International,
    All Seasons on Peacock.  The window is Nov 2025-Nov 2026. Need the
    viewers for each series reported independently."
    -> "Brief drafted: FBI Most Wanted Viewers (new build, $300); Brief
       drafted: FBI International Viewers (new build, $300)"
       and one chip, "Open the dashboard to build it".

Four minutes earlier, the same ask in other words ("pull the total
viewers to FBI Most Wanted and FBI International independently on
Peacock, all seasons across November 2025 - 2026.") drew a package of
two year profiles "at $300 each", under "Included with your plan."

Held here:

  TheLane        a viewer count stated as a wish or an instruction is a
                 question (understand.looks_count_read); naming who to
                 build is still a build; a bare audience name still is
  TheSubject     the read is on the titles the count names, never on a
                 run of words from the next sentence, never on the
                 service when no reading is in hand
  OneWindow      "November 2025 - 2026" is one window, month to month,
                 never a two-year study
  ThroughTheDoor her words through service.ask as the app sends them:
                 the read core once, with both titles and her own
                 sentence, never the build core; the reply is a read in
                 the app's own window
  ThePrice       the brief line, the key contract, the question copy and
                 the year package print no price for a seat the plan
                 covers and read "Included with your plan."; a paying
                 seat and a probe still read the price
  TheApp         a drafted build is a brief card in the app: a covered
                 seat starts it there, the chip sends the order through
                 the same conversation; nothing tells that seat to open
                 the dashboard; every chip's action survives its quotes

Hermetic: the stub host of test_pm_new_chat_no_bleed, the page's own
script run in Node when Node is on the path. No model, no S3, no mail.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location('pm_nb_for_viewers_count', os.path.join(HERE, 'test_pm_new_chat_no_bleed.py'))
NB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NB)

import flask  # noqa: E402

from prometheus import ask_outcome as AO  # noqa: E402
from prometheus import contract_v1 as C  # noqa: E402
from prometheus import envelope as ENV  # noqa: E402
from prometheus import orders as ORD  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus import reader as RDR  # noqa: E402
from prometheus import routing_table as RT  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402
from prometheus import understand as U  # noqa: E402
from prometheus.host import host  # noqa: E402
import prometheus_watch_notify as WN  # noqa: E402

APP = NB.APP

LIZ = ("I need the Total Viewers to FBI Most Wanted and FBI International, All Seasons on Peacock.  "
       "The window is Nov 2025-Nov 2026. Need the viewers for each series reported independently.")
LIZ_FIRST = ("pull the total viewers to FBI Most Wanted and FBI International independently on Peacock, "
             "all seasons across November 2025 - 2026.")
COUNTS = (
    LIZ,
    LIZ_FIRST,
    'how many people watched FBI Most Wanted on Peacock in the last year',
    'total viewers for FBI Most Wanted and FBI International, each separately',
    'need the viewers for FBI Most Wanted all seasons',
    'viewer count for FBI International on Peacock, Nov 2025 to Nov 2026',
    'number of viewers of The Bear on Hulu in 2025',
    'Landman viewership on Paramount+ last year',
)
BUILDS = (
    'build a profile of FBI Most Wanted viewers',
    'I need an audience profile for FBI International viewers',
    'Run a profile for FBI Most Wanted viewers on Peacock',
    'pull an audience of FBI Most Wanted viewers',
    'I need a profile of the total viewers of FBI Most Wanted',
    'Build a cut of FBI Most Wanted viewers who also watch Chicago PD',
    'Pull Subscriber IQ for FBI Most Wanted on Peacock',
)
BARE = (
    'FBI Most Wanted viewers on Peacock',
    'Everybody Loves Raymond viewers on Amazon Prime Video, September 2026',
    'Reba McEntire avid fans',
    'Nike',
)
# what the Reader made of her words on the day (the ask log, 0.62: under the bar that picks a surface)
READING = {'intent': 'analyze', 'confidence': 0.62,
           'subjects': [{'as_written': 'FBI Most Wanted', 'catalog': '', 'kind': 'title'},
                        {'as_written': 'FBI International, All Seasons', 'catalog': '', 'kind': 'title'}],
           'platform': 'Peacock', 'window': 'Nov 2025-Nov 2026', 'flags': ['multiple_subjects', 'custom_window']}

PRICE_TABLE = {'new_build': 300.0, 'time_shifted_refresh': 300.0, 'cut_needs_parent': 400.0, 'addon_cut': 100.0}
DRAFTS = {'success': True, 'batch': True, 'spec_drafts': [
    {'subject': 'FBI Most Wanted Viewers', 'decision': 'new_build', 'price_table': dict(PRICE_TABLE),
     'date_window': 'Nov 1, 2025 to Nov 17, 2026'},
    {'subject': 'FBI International Viewers', 'decision': 'new_build', 'price_table': dict(PRICE_TABLE),
     'date_window': 'Nov 1, 2025 to Nov 17, 2026'}]}
ONE_DRAFT = {'success': True, 'spec_draft': {'subject': 'FBI Most Wanted Viewers', 'decision': 'new_build',
                                             'price_table': dict(PRICE_TABLE)}, 'date_window': 'Nov 1, 2025 to Nov 17, 2026'}

STAFF = {'username': 'liz', 'email': 'liz@crosswalknyc.com', 'company': 'Crosswalk', 'role': 'super_admin', 'credits': -1,
         'has_chatbot_profile_iq_access': True}
PAYER = {'username': 'payer', 'email': 'pat@example.com', 'company': 'Acme Studios', 'role': 'user', 'credits': 12,
         'has_chatbot_profile_iq_access': True}
PROBE = {'username': 'canary:golden', 'email': '', 'company': 'Crosswalk', 'role': 'super_admin', 'unlimited': True}
USERS = {'users': {'liz': STAFF, 'payer': PAYER, 'canary:golden': PROBE, NB.USER: dict(NB.ARMINE)}}


def _copy(obj):
    return json.loads(json.dumps(obj))


def _bind_seats(world=None):
    """The three seats on the bound host: a staff seat the plan covers, a
    paying seat with a balance, a probe."""
    host.bind(load_users=lambda: USERS,
              check_user_credits=lambda u: (True, int((USERS['users'].get(u) or {}).get('credits', 0) or 0)))


def _as(uname):
    """A request in flight for one seat, the way the door stamps it."""
    ctx = APP.test_request_context('/api/prometheus/v1/ask', method='POST')
    ctx.push()
    flask.g._pm_ask_user = uname
    return ctx


def _read(name):
    with open(os.path.join(ROOT, *name.split('/')), encoding='utf-8') as fh:
        return fh.read()


def _grab(src, name):
    i = src.index(f"def {name}(")
    j = src.index("\ndef ", i + 1)
    return src[i:j]


# ============================================================================ 1
class TheLane(unittest.TestCase):
    def test_a_viewer_count_is_a_question_with_nothing_open(self):
        for q in COUNTS:
            d = U.decide(q, has_ctx=False)
            self.assertEqual((d['surface'], d['reason']), ('analyze', 'question'), (q, d))
            self.assertEqual(U.decide(q, has_ctx=True)['surface'], 'analyze', q)

    def test_the_statement_forms_are_the_new_rule(self):
        for q in (LIZ, LIZ_FIRST, COUNTS[3], COUNTS[4], COUNTS[5]):
            self.assertTrue(U.looks_count_read(q), q)
            self.assertFalse(U._QUESTION_OPEN_RX.search(q) or U._QUESTION_MARK_RX.search(q), q)   # no question word carried it

    def test_naming_who_to_build_is_still_a_build(self):
        for q in BUILDS:
            d = U.decide(q, has_ctx=False)
            self.assertEqual(d['surface'], 'interpret', (q, d))
            self.assertFalse(U.looks_count_read(q), q)
        for q in BUILDS[:5]:
            self.assertEqual(U.decide(q, has_ctx=False)['reason'], 'build_order', q)
            self.assertTrue(ORD.is_profile_order(q), q)

    def test_a_bare_audience_name_is_still_a_brief(self):
        for q in BARE:
            d = U.decide(q, has_ctx=False)
            self.assertEqual((d['surface'], d['reason']), ('interpret', 'build_or_other'), (q, d))
            self.assertFalse(U.looks_count_read(q), q)

    def test_the_house_already_priced_it_as_a_viewership_read(self):
        for q in (LIZ, LIZ_FIRST):
            self.assertEqual((PR.classify(q) or {}).get('kind'), 'viewership', q)

    def test_the_table_carries_her_words(self):
        row = RT.row_for('question')
        self.assertIn((LIZ, False), row['examples'])
        self.assertIn(('FBI Most Wanted viewers on Peacock', False), row['counter'])

    def test_a_client_sitting_in_its_build_flow_is_released(self):
        src = _read('prometheus/service.py')
        blk = src[src.index("if forced == 'interpret' and referent_decision is None"):]
        self.assertIn("'question'", blk[:700])      # the reason a count read lands on releases the forced surface


# ============================================================================ 2
class TheSubject(unittest.TestCase):
    def test_a_name_never_runs_across_the_end_of_a_sentence(self):
        self.assertEqual(SR.sentence_audience(LIZ), ('', ''))
        self.assertEqual(SR.sentence_audience('I watched it on Peacock. Need the viewers for the series'), ('', ''))
        self.assertEqual(SR.sentence_audience('Compare 2026. Mad Men viewers skew older')[0], 'Mad Men')

    def test_names_that_carry_a_stop_keep_it(self):
        self.assertEqual(SR.sentence_audience('Dr. Phil viewers by age')[0], 'Dr. Phil')
        self.assertEqual(SR.sentence_audience('S.W.A.T. Exiles viewers on Starz')[0], 'S.W.A.T. Exiles')
        self.assertEqual(SR.sentence_audience('Mr. Robot viewers and St. Elsewhere viewers')[0], 'Mr. Robot')
        self.assertEqual(SR.sentence_audience('What did Landman viewers watch next')[0], 'Landman')
        self.assertEqual(SR.sentence_audience('What do The Rainmaker viewers buy')[0], 'The Rainmaker')
        self.assertEqual(SR.sentence_audience('How many Get Out viewers are there')[0], 'Get Out')

    def test_the_count_names_its_titles(self):
        self.assertEqual(SR.counted_title(LIZ), 'FBI Most Wanted and FBI International')
        self.assertEqual(SR.counted_title(LIZ_FIRST), 'FBI Most Wanted and FBI International')
        self.assertEqual(SR.counted_title('need the viewers for FBI Most Wanted all seasons'), 'FBI Most Wanted')
        self.assertEqual(SR.counted_title('number of viewers of The Bear on Hulu in 2025'), 'The Bear')
        self.assertEqual(SR.counted_title('total viewers of Will and Grace on Hulu'), 'Will and Grace')
        self.assertEqual(SR.counted_title('I need total viewers for All American. Thanks'), 'All American')
        self.assertEqual(SR.counted_title('viewer count for FBI International on Peacock'), 'FBI International')

    def test_a_mention_is_not_a_count(self):
        for q in ('What other titles did viewers of Mad Men watch', 'how many viewers of this profile are women',
                  'Need the viewers for each series reported independently.', 'total viewers of All Seasons',
                  'total viewers for November 2025', 'number of viewers for Q4 2025', 'total viewers for Season 2',
                  'how many viewers of US streaming services are there'):
            self.assertEqual(SR.counted_title(q), '', q)

    def test_the_read_is_quoted_on_the_titles_with_or_without_a_reading(self):
        for rd in (None, READING):
            self.assertEqual(PR.bound_subject(LIZ, [], None, reading=rd), 'FBI Most Wanted and FBI International', rd)
        self.assertNotIn('Need the', PR.bound_subject(LIZ, [], None))
        self.assertNotIn('2026', PR.bound_subject(LIZ, [], None))


# ============================================================================ 3
class OneWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = _read('prometheus/legacy/rankers.py')
        ns = {'re': re, 'datetime': datetime}
        a = src.index('_PM_MY_RANGE_RE = re.compile(')
        b = src.index('def _pm_year_files_for_subject(')
        exec(src[a:b], ns)
        cls.detect = staticmethod(ns['_pm_detect_multi_year_ask'])
        cls.src = src

    def test_month_to_month_is_one_window(self):
        for q in (LIZ_FIRST, 'viewers of Landman, Nov 2025 - 2026', 'Total viewers of Suits from March 2025 to 2026',
                  'pull viewers across November 15, 2025 - 2026'):
            self.assertIsNone(self.detect(q), q)
        self.assertIsNone(self.detect(LIZ))

    def test_a_study_across_years_is_still_one(self):
        self.assertEqual(self.detect('Will and Grace viewers 2024 - 2026'), [2024, 2025, 2026])
        self.assertEqual(self.detect('compare the audience 2025 - 2026'), [2025, 2026])
        self.assertEqual(self.detect('January 2024 - 2026 trend for Suits'), [2024, 2025, 2026])
        self.assertEqual(len(self.detect('year over year, November 2025 - 2026') or []), 2)
        self.assertTrue(self.detect('a 4-5 year analysis of Will and Grace'))

    def test_the_package_prints_no_price_for_a_covered_seat(self):
        _bind_seats()
        ns = {'_C': types.SimpleNamespace(_pm_usd_label=lambda v, *a, **k: f"${float(v):,.0f}"),
              '_H': types.SimpleNamespace(_v1_price_usd_for=lambda *a, **k: 300.0)}
        exec(_grab(self.src, '_pm_year_package_reply'), ns)
        fn = ns['_pm_year_package_reply']
        ctx = _as('liz')
        try:
            reply, _chips = fn('FBI International', [2025, 2026], [], [2025, 2026])
        finally:
            ctx.pop()
        self.assertNotIn('$', reply)
        self.assertIn('included with your plan.', reply)
        ctx = _as('payer')
        try:
            reply, _chips = fn('FBI International', [2025, 2026], [], [2025, 2026])
        finally:
            ctx.pop()
        self.assertIn('at $300 each.', reply)


# ============================================================================ 4
class ThroughTheDoor(unittest.TestCase):
    def setUp(self):
        self.w = NB.World()
        self._read_fn = RDR.read

    def tearDown(self):
        RDR.read = self._read_fn

    def _ask(self, text, *, client='api', via='api_key', surface=None, reading=None):
        RDR.read = (lambda *a, **k: _copy(reading)) if reading else (lambda *a, **k: None)
        self.w.calls.clear()
        body = {'text': text, 'client': client, 'history': [], 'persist': False}
        if surface:
            body.update(surface=surface, _client_surface_sent=surface)
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
            flask.g._pm_ask_user = NB.USER
            env, _st = S.ask(dict(NB.ARMINE), body, via=via)
            v1 = C.to_v1(env, env.get('_raw')) if via == 'api_key' else None
        return env, v1

    def test_her_words_from_the_app_are_one_read_on_both_series(self):
        for reading in (None, READING):
            env, v1 = self._ask(LIZ, reading=reading)
            self.assertEqual([c[0] for c in self.w.calls], ['analyze'], (reading, self.w.calls))   # the read core, once
            _surface, text, body, _seen = self.w.calls[0]
            self.assertEqual(text, LIZ)                                     # her own sentence, both series, each on its own
            self.assertIn('each series reported independently', text)
            self.assertEqual(body.get('bind_subject'), 'FBI Most Wanted and FBI International')
            self.assertEqual(env['kind'], 'job')
            self.assertTrue(env['text'].startswith(PR.INCLUDED_LINE), env['text'])   # a covered seat: no quote, the read starts
            self.assertNotIn('$', env['text'])
            self.assertEqual((env.get('decision') or {}).get('reason'), 'question')
            self.assertEqual(v1['kind'], 'job')                             # a read in the app's own window
            self.assertNotIn('declined', v1)
            self.assertNotIn('Brief drafted', json.dumps(v1))

    def test_the_other_wordings_never_reach_the_build_core(self):
        for q in COUNTS:
            for kw in ({}, {'client': 'dashboard', 'via': 'session', 'surface': 'interpret'}):
                env, _v1 = self._ask(q, **kw)
                self.assertNotIn('interpret', [c[0] for c in self.w.calls], (q, kw))
                self.assertNotEqual(env['kind'], 'draft', (q, kw))
                self.assertFalse((env.get('raw') or env.get('_raw') or {}).get('spec_draft'), (q, kw))

    def test_an_order_to_build_still_drafts_the_brief(self):
        for q in BUILDS[:4]:
            env, v1 = self._ask(q)
            self.assertEqual([c[0] for c in self.w.calls], ['interpret'], q)
            self.assertEqual(env['kind'], 'draft', q)
            self.assertEqual(v1['declined']['reason'], 'build_required', q)

    def test_nothing_in_the_read_reply_sends_her_to_the_dashboard(self):
        _env, v1 = self._ask(LIZ, reading=READING)
        blob = json.dumps(v1).lower()
        for banned in ('open the dashboard', 'dashboard to build', 'approve in the dashboard'):
            self.assertNotIn(banned, blob)
        self.assertNotIn('$', json.dumps(v1.get('job') or {}))


# ============================================================================ 5
class ThePrice(unittest.TestCase):
    def setUp(self):
        NB.World()
        _bind_seats()

    def _line(self, uname, payload):
        ctx = _as(uname)
        try:
            return AO.reply_text(_copy(payload))
        finally:
            ctx.pop()

    def test_the_brief_line_for_a_covered_seat_carries_no_price(self):
        line = self._line('liz', DRAFTS)
        self.assertEqual(line, 'Brief drafted: FBI Most Wanted Viewers (new build); Brief drafted: FBI International Viewers '
                               '(new build). Included with your plan.')
        self.assertNotIn('$', line)
        self.assertTrue(line.endswith(PR.INCLUDED_LINE))
        one = self._line('liz', ONE_DRAFT)
        self.assertEqual(one, 'Brief drafted: FBI Most Wanted Viewers (new build). Included with your plan.')

    def test_a_paying_seat_still_reads_its_price(self):
        line = self._line('payer', DRAFTS)
        self.assertEqual(line, 'Brief drafted: FBI Most Wanted Viewers (new build, $300); Brief drafted: FBI International '
                               'Viewers (new build, $300)')
        self.assertNotIn('Included', line)

    def test_a_probe_reads_the_paying_shape(self):
        self.assertIn('$300', self._line('canary:golden', DRAFTS))

    def test_with_nobody_to_name_the_line_is_as_it_was(self):
        self.assertIn('$300', AO.reply_text(_copy(DRAFTS)))                 # outside any request
        self.assertFalse(AO.seat_included(_copy(DRAFTS)))

    def test_it_is_the_one_shared_check(self):
        calls = []
        real = PR.included_with_plan
        PR.included_with_plan = lambda user, username=None, host=None, amount_usd=None: (calls.append(username), real(user, username, host))[1]
        try:
            self._line('liz', DRAFTS)
            self._line('payer', DRAFTS)
        finally:
            PR.included_with_plan = real
        self.assertEqual(calls, ['liz', 'payer'])
        self.assertEqual(AO.INCLUDED_LINE, PR.INCLUDED_LINE)

    def test_the_mark_rides_the_payload_once_stamped(self):
        ctx = _as('liz')
        try:
            raw = _copy(DRAFTS)
            env = ENV.wrap(raw, surface='interpret', decision={'reason': 'build_or_other'}, via='api_key')
        finally:
            ctx.pop()
        self.assertIs(raw.get('included_with_plan'), True)
        self.assertNotIn('$', env['text'])
        self.assertIn(PR.INCLUDED_LINE, env['text'])
        # later readers of the payload need no request to agree
        self.assertNotIn('$', AO.reply_text(raw))
        ctx = _as('payer')
        try:
            raw = _copy(DRAFTS)
            env = ENV.wrap(raw, surface='interpret', decision={'reason': 'build_or_other'}, via='api_key')
        finally:
            ctx.pop()
        self.assertNotIn('included_with_plan', raw)
        self.assertIn('$300', env['text'])

    def test_a_reply_that_is_not_a_brief_is_never_marked(self):
        ctx = _as('liz')
        try:
            raw = {'success': True, 'action': 'answer', 'reply': 'On it.', 'followups': []}
            ENV.wrap(raw, surface='analyze', decision={}, via='api_key')
        finally:
            ctx.pop()
        self.assertNotIn('included_with_plan', raw)

    def test_the_key_contract_carries_the_briefs_without_a_price(self):
        ctx = _as('liz')
        try:
            raw = _copy(DRAFTS)
            env = ENV.wrap(raw, surface='interpret', decision={'reason': 'build_or_other'}, via='api_key')
            v1 = C.to_v1(env, env.get('_raw'))
        finally:
            ctx.pop()
        d = v1['declined']
        self.assertEqual(d['reason'], 'build_required')
        self.assertEqual(d['next'], {'action': 'build', 'endpoint': C.BUILD_ENDPOINT})    # the partner contract is as it was
        self.assertIs(d.get('included'), True)
        self.assertEqual([b['subject'] for b in d['briefs']], ['FBI Most Wanted Viewers', 'FBI International Viewers'])
        self.assertTrue(all(b['price_usd'] is None and b['included'] for b in d['briefs']))
        self.assertNotIn('$', json.dumps(d))
        self.assertEqual(d['briefs'][0]['order'], 'Run a profile on FBI Most Wanted Viewers for Nov 1, 2025 to Nov 17, 2026')
        self.assertTrue(all(ORD.is_profile_order(b['order']) for b in d['briefs']))
        # the partner belt over every string leaves the card intact
        from prometheus import guards as G
        before = _copy(v1['declined'])
        G.scrub_partner_payload(v1)
        self.assertEqual(v1['declined'], before)

    def test_the_key_contract_for_a_paying_seat_keeps_the_price(self):
        ctx = _as('payer')
        try:
            raw = _copy(DRAFTS)
            env = ENV.wrap(raw, surface='interpret', decision={'reason': 'build_or_other'}, via='api_key')
            v1 = C.to_v1(env, env.get('_raw'))
        finally:
            ctx.pop()
        d = v1['declined']
        self.assertNotIn('included', d)
        self.assertEqual([b['price_usd'] for b in d['briefs']], [300.0, 300.0])
        self.assertIn('$300', d['text'])

    def test_the_question_copy_prints_what_the_reader_saw(self):
        ctx = _as('liz')
        try:
            copy = WN.answer_text(_copy(ONE_DRAFT))
        finally:
            ctx.pop()
        self.assertIn('Included with your plan.', copy)
        self.assertNotIn('$', copy)
        self.assertNotIn('Cost on approval', copy)
        ctx = _as('payer')
        try:
            copy = WN.answer_text(_copy(ONE_DRAFT))
        finally:
            ctx.pop()
        self.assertIn('Cost on approval: $300', copy)

    def test_no_price_is_changed(self):
        self.assertEqual(AO.draft_price_usd(DRAFTS['spec_drafts'][0]), 300.0)
        self.assertEqual(AO.draft_price_usd(dict(DRAFTS['spec_drafts'][0], addon_cuts=[{}, {}])), 500.0)


# ============================================================================ 6
PAGE = Path(ROOT) / 'prometheus' / 'desktop_ui' / 'index.html'
NODE = shutil.which('node')


def _page_script():
    html = PAGE.read_text(encoding='utf-8')
    return html[html.index('<script>') + len('<script>'):html.rindex('</script>')]


def _run_page(calls_js):
    """The page's own script in Node with the least a browser gives it,
    then `calls_js`, which prints one JSON document."""
    stub = ("const _el = () => ({ innerHTML: '', textContent: '', value: '', style: {}, classList: { add(){}, remove(){} }, "
            "querySelector(){ return null; }, appendChild(){}, focus(){}, remove(){} });\n"
            "const document = { getElementById: () => _el(), createElement: () => _el() };\n"
            "const window = { addEventListener(){}, pywebview: null };\n")
    out = subprocess.run([NODE, '-e', stub + _page_script() + '\n' + calls_js], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise AssertionError(out.stderr[-2000:])
    return json.loads(out.stdout)


def _onclicks(html):
    """Every onclick as the browser reads it: a double-quoted attribute
    ends at the next double quote, entities decoded."""
    import html as _h
    return [_h.unescape(m.group(2)) for m in re.finditer(r"""onclick=(["'])(.*?)\1""", html, re.S)]


class TheApp(unittest.TestCase):
    def test_the_page_has_no_dashboard_chip_for_a_brief(self):
        src = PAGE.read_text(encoding='utf-8')
        self.assertNotIn('Open the dashboard to build it', src)
        self.assertIn("d.reason === 'build_required'", src)
        self.assertIn('function briefHtml(', src)

    def test_the_contract_schema_still_holds_the_brief(self):
        schema = json.loads(_read('prometheus/schemas/ask.v1.json'))
        props = schema['properties']['declined']['properties']
        self.assertIn('build_required', props['reason']['enum'])
        for k in ('briefs', 'included', 'next', 'text'):
            self.assertIn(k, props)
        self.assertEqual(schema['properties']['declined']['required'], ['reason', 'text'])   # nothing new is required of a partner

    @unittest.skipUnless(NODE, 'node is not on the path')
    def test_a_covered_seat_starts_the_build_in_the_app(self):
        NB.World()
        _bind_seats()
        ctx = _as('liz')
        try:
            env = ENV.wrap(_copy(DRAFTS), surface='interpret', decision={'reason': 'build_or_other'}, via='api_key')
            v1 = C.to_v1(env, env.get('_raw'))
        finally:
            ctx.pop()
        out = _run_page('console.log(JSON.stringify({html: briefHtml(' + json.dumps(v1['declined']) + ')}));')
        html = out['html']
        self.assertNotIn('dashboard', html.lower())                          # nothing sends this seat out of the app
        self.assertNotIn('$', html)
        self.assertIn('Included with your plan', html)
        clicks = _onclicks(html)
        self.assertEqual(clicks, ['sendText("Run a profile on FBI Most Wanted Viewers for Nov 1, 2025 to Nov 17, 2026")',
                                  'sendText("Run a profile on FBI International Viewers for Nov 1, 2025 to Nov 17, 2026")'])
        self.assertIn('Build FBI Most Wanted Viewers', html)

    @unittest.skipUnless(NODE, 'node is not on the path')
    def test_a_paying_seat_reads_its_price_and_the_one_step_the_app_cannot_take(self):
        NB.World()
        _bind_seats()
        ctx = _as('payer')
        try:
            env = ENV.wrap(_copy(DRAFTS), surface='interpret', decision={'reason': 'build_or_other'}, via='api_key')
            v1 = C.to_v1(env, env.get('_raw'))
        finally:
            ctx.pop()
        html = _run_page('console.log(JSON.stringify({html: briefHtml(' + json.dumps(v1['declined']) + ')}));')['html']
        self.assertIn('$300', html)
        self.assertNotIn('Included with your plan', html)
        self.assertNotIn('sendText(', html)                                  # no chip that would only draft it again
        self.assertIn('Nothing is charged until you approve.', html)

    @unittest.skipUnless(NODE, 'node is not on the path')
    def test_every_chip_survives_its_quotes(self):
        doc = {'kind': 'answer', 'answer': {'text': 'x', 'facts': [], 'followups': ['Demo profile of the "both" viewers', "Liz's next ask"]}}
        html = _run_page('console.log(JSON.stringify({html: answerHtml("q", ' + json.dumps(doc) + ')}));')['html']
        clicks = [c for c in _onclicks(html) if c.startswith('sendText(')]
        self.assertEqual(clicks, ['sendText("Demo profile of the \\"both\\" viewers")', 'sendText("Liz\'s next ask")'])

    @unittest.skipUnless(NODE, 'node is not on the path')
    def test_saving_an_answer_with_an_apostrophe_still_works(self):
        text = "Counts are US viewers, so they are not comparable to a service's reported \"subscriber\" count."
        doc = {'kind': 'answer', 'conversation_id': 'c1', 'answer': {'text': text, 'facts': [], 'followups': []}}
        html = _run_page('console.log(JSON.stringify({html: answerHtml("Liz\'s question", ' + json.dumps(doc) + ')}));')['html']
        saves = [c for c in _onclicks(html) if c.startswith('saveAnswer(')]
        self.assertEqual(len(saves), 2)
        for c, kind in zip(saves, ('csv', 'pdf')):
            m = re.match(r'^saveAnswer\(this, "(csv|pdf)", (.*)\)$', c, re.S)
            self.assertTrue(m, c)
            self.assertEqual(m.group(1), kind)
            payload = json.loads(json.loads(m.group(2)))                     # the argument is whole: a JSON string of the payload
            self.assertEqual(payload['answer']['text'], text)
            self.assertEqual(payload['question'], "Liz's question")

    @unittest.skipUnless(NODE, 'node is not on the path')
    def test_her_read_renders_as_a_read_in_the_app(self):
        # the reply her question now draws, as the page draws it: a running read, no way out of the window
        w = NB.World()
        keep = RDR.read
        RDR.read = lambda *a, **k: _copy(READING)
        try:
            with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
                flask.g._pm_ask_user = NB.USER
                env, _st = S.ask(dict(NB.ARMINE), {'text': LIZ, 'client': 'api', 'history': [], 'persist': False}, via='api_key')
                v1 = C.to_v1(env, env.get('_raw'))
        finally:
            RDR.read = keep
        self.assertEqual([c[0] for c in w.calls], ['analyze'])
        js = ("let shown = ''; const _b = { innerHTML: '' };\n"
              "bubble = () => _b; pollJob = () => {}; scrollDown = () => {};\n"
              "render('m1', " + json.dumps(LIZ) + ", " + json.dumps(v1) + ");\n"
              "console.log(JSON.stringify({html: _b.innerHTML}));")
        html = _run_page(js)['html']
        self.assertIn('class="job"', html)
        self.assertNotIn('open_dashboard', html)
        self.assertNotIn('Open the dashboard', html)
        self.assertNotIn('$', html)


if __name__ == '__main__':
    unittest.main(verbosity=1)
