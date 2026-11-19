#!/usr/bin/env python3
"""The Attribution IQ read accepts only known request values (Jenna
2026-11-19: "only accept the known values for each filter and refuse
anything else").

What this suite holds:

  1. The value checks themselves: a campaign id, a date, a Source value,
     a phase or an asset type from the campaign's own list. Every
     legitimate value passes; mixed case and padded values pass and come
     back in the stored spelling; nothing given means no filter; a value
     that is not on any list is refused with a plain line that never
     repeats what was sent.
  2. The asset read: the statement it builds is the same text whatever
     the filters hold. The values travel beside it as bound parameters.
     A refused value never appears in any statement or parameter, and
     the read itself is never built.
  3. The in-flight read and the as-of day; every window the page offers.
  4. The campaign id on every read: only a well-formed id is used, and
     it is bound, never written in.
  5. Questions and the touchpoint read.
  6. The module source: no request value is written into a statement.
  7. The routes, lifted from app.py and run against the real read with a
     stand-in store: the one check every /api/intent/<campaign>/ route
     passes, and a refused value answering 400.
  8. The page: every value each control can send passes.

Hermetic: a recording stand-in for the store, a made-up campaign, no
network, no live store, no mail, no app boot.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

import logging  # noqa: E402

logging.disable(logging.CRITICAL)

import intent_iq as IQ  # noqa: E402

PASS = 0
FAIL: list[str] = []


def check(name, cond, detail=''):
    global PASS
    if cond:
        PASS += 1
        print(f'  ok   {name}')
    else:
        FAIL.append(name)
        print(f'  FAIL {name} {str(detail)[:500]}')


# --------------------------------------------------------------- the made-up campaign
SLUG = 'zz_campaign_77'
PHASES = ['Trailer Launch', 'Branding (T-1)', 'Opening Weekend (T-0)', 'Bridge Campaign',
          "Founder's Day \u2013 Push", 'Q&A: Round 2 / Finale + Extras']
TYPES = ['Official Trailer', 'Trailer 2', 'AMC Ticketing', 'Fandango Checkout', 'Paid Digital',
         'Influencer Social', 'Google Search', 'Wikipedia Entry']
ASSETS = [
    {'asset_id': 'a1', 'phase_name': 'Trailer Launch', 'asset_type': 'Official Trailer', 'paid_or_organic': 'organic',
     'posted_date': '2026-02-01', 'ext_view_count': 900, 'ext_engagement_count': 90, 'action_label': 'Trailer', 'url': 'https://example.com/a1'},
    {'asset_id': 'a2', 'phase_name': 'Branding (T-1)', 'asset_type': 'Paid Digital', 'paid_or_organic': 'paid',
     'posted_date': '2026-03-01', 'ext_view_count': 700, 'ext_engagement_count': 70, 'action_label': 'Spot', 'url': 'https://example.com/a2'},
    {'asset_id': 'a3', 'phase_name': "Founder's Day \u2013 Push", 'asset_type': 'Influencer Social', 'paid_or_organic': 'earned',
     'posted_date': '2026-04-01', 'ext_view_count': 500, 'ext_engagement_count': 50, 'action_label': 'Post', 'url': 'https://example.com/a3'},
    {'asset_id': 'a4', 'phase_name': 'Q&A: Round 2 / Finale + Extras', 'asset_type': 'AMC Ticketing', 'paid_or_organic': 'official',
     'posted_date': '2026-05-01', 'ext_view_count': 300, 'ext_engagement_count': 30, 'action_label': 'Tickets', 'url': 'https://example.com/a4'},
]
SNAP = {'title': {'display_name': 'ZZ Campaign', 'title_type': 'brand'},
        'phases': [{'phase_name': p, 'phase_order': i} for i, p in enumerate(PHASES)],
        'assets': ASSETS}

# Values that are on no list. Quotes, statement breaks, comment marks,
# joined reads, stacked statements, encoded forms, placeholder text,
# control characters, look-alike characters, an overlong value.
NOT_ON_ANY_LIST = [
    "x' OR '1'='1",
    "Trailer Launch' --",
    "Trailer Launch'/*",
    "paid'; DROP TABLE intent.campaign_assets; --",
    "' UNION SELECT 1 --",
    "paid UNION ALL SELECT name FROM system.tables",
    "paid) OR (1=1",
    "%27%20OR%201%3D1",
    "%2527%2520OR%25201%253D1",
    "\\' OR 1=1 #",
    "paid\x00",
    "paid\n; SELECT 1",
    "\uff07 OR 1=1",
    "0x27206f7220313d31",
    "paid`",
    "{slug:String}",
    "{phase:String}' OR 1=1",
    "Trailer Launch\" OR \"\"=\"",
    "Trailer Launch; SELECT 1",
    "a" * 500,
]
NOT_A_CAMPAIGN = ["goat' OR '1'='1", "goat'--", "goat; SELECT 1", "../registry", "goat/../../system", "goat%27", "go at",
                  "{slug:String}", "go\nat", "goat\x00", "", "   ", "-goat", "a" * 121, None, 7, ["goat"], "goat UNION SELECT 1"]
NOT_A_DAY = ["2026-01-30' OR '1'='1", "2026-01-30'--", "2026-01-30; SELECT 1", "2026-13-45", "2026-02-30", "01/30/2026",
             "2026-1-5", "20260130", "2026-01-30T00:00:00", "today()", "{as_of:String}", "2026-01-30)", 20260130]


class Store:
    """A recording stand-in for the store. Answers the fixed look-ups for
    the made-up campaign; everything else comes back empty unless rows
    are queued for it."""

    def __init__(self, phases=PHASES, types=TYPES, rows=None):
        self.calls = []
        self.phases, self.types = list(phases), list(types)
        self.rows = rows

    def query(self, sql, parameters=None):
        self.calls.append((sql, dict(parameters or {})))

        class R:
            pass
        r = R()
        if 'SELECT DISTINCT phase_name FROM intent.campaign_phases' in sql:
            r.result_rows = [(p,) for p in self.phases]
        elif 'SELECT DISTINCT phase_name, asset_type' in sql:
            n = max(len(self.phases), len(self.types))
            r.result_rows = [(self.phases[i % len(self.phases)] if self.phases else '',
                              self.types[i % len(self.types)] if self.types else '') for i in range(n)]
        else:
            r.result_rows = list(self.rows or [])
        return r

    # helpers for the checks
    def statements(self):
        return [s for s, _ in self.calls]

    def reads(self):
        """Statements other than the two fixed look-ups."""
        return [(s, p) for s, p in self.calls if 'SELECT DISTINCT phase_name' not in s]

    def everything(self):
        return json.dumps(self.calls, default=str, ensure_ascii=False)


LOOKUP_1 = "SELECT DISTINCT phase_name FROM intent.campaign_phases FINAL WHERE title_slug = {slug:String}"
LOOKUP_2 = "SELECT DISTINCT phase_name, asset_type FROM intent.campaign_assets FINAL WHERE title_slug = {slug:String}"


class Patched:
    """The read on a stand-in store and a stand-in snapshot."""

    def __init__(self, store=None, snap=None, down=False):
        self.store = store if store is not None else Store()
        self.snap, self.down = snap, down
        self.clients = 0
        self.snaps = 0

    def __enter__(self):
        self._c, self._s, self._r = IQ._ch_client, IQ._load_normalized_snapshot, IQ._load_registry
        me = self

        def client():
            me.clients += 1
            return None if me.down else me.store

        def snapshot(slug):
            me.snaps += 1
            return json.loads(json.dumps(me.snap)) if (me.snap and IQ.slug_ok(slug)) else None
        IQ._ch_client, IQ._load_normalized_snapshot = client, snapshot
        IQ._load_registry = lambda: {'titles': []}
        return self

    def __exit__(self, *a):
        IQ._ch_client, IQ._load_normalized_snapshot, IQ._load_registry = self._c, self._s, self._r


def refused_with(res, line):
    return isinstance(res, dict) and res.get('success') is False and res.get('refused') is True and res.get('error') == line


def says_nothing_of(res, sent):
    return str(sent) not in json.dumps(res, default=str, ensure_ascii=False) if isinstance(sent, str) and sent.strip() else True


def padded_and_cased(v):
    return [v, v.upper(), v.lower(), v.swapcase(), '  ' + v + '  ', '\t' + v.title() + ' ', v.replace(' ', '   ')]


# --------------------------------------------------------------- 1
def section_values():
    print('1. The value checks')
    for s in ('goat', 'chime_financial_mypay', 'the_influencer_project_hades', 'starz_blood_of_my_blood_finale_campaign',
              'usa_today_anthem_sponsor_stories', 'mario_bros', 'sonic_3', 'spiderverse_atsv', 'be-good-2', 'Goat', 'x', 'a' * 120):
        check(f'campaign id {s[:30]!r} passes as written', IQ.slug_ok(s) and IQ.clean_slug(s) == s)
    check('a padded campaign id passes without the padding', IQ.clean_slug('  goat ') == 'goat')
    for bad in NOT_A_CAMPAIGN:
        try:
            IQ.clean_slug(bad)
            ok = False
        except IQ.RequestRefused as e:
            ok = str(e) == IQ.CAMPAIGN_REFUSAL
        check(f'campaign id {str(bad)[:24]!r} is refused', ok and not IQ.slug_ok(bad))

    for d in ('2021-01-01', '2026-01-30', '2024-02-29', ' 2026-11-19 '):
        check(f'day {d!r} passes', IQ.clean_day(d) == d.strip())
    check('no day given is no day', IQ.clean_day(None) is None and IQ.clean_day('') is None and IQ.clean_day('   ') is None)
    for bad in NOT_A_DAY:
        try:
            IQ.clean_day(bad)
            ok = False
        except IQ.RequestRefused as e:
            ok = str(e) == IQ.DATE_REFUSAL
        check(f'day {str(bad)[:24]!r} is refused', ok)

    check('the Source list is the six stored and offered values',
          IQ.SOURCE_VALUES == ('paid', 'organic', 'earned', 'official', 'natural', 'unknown'))
    for v in IQ.SOURCE_VALUES:
        for form in padded_and_cased(v):
            check(f'Source {form!r} passes as {v!r}', IQ.clean_choice(form, IQ.SOURCE_VALUES) == v)
    for v in PHASES:
        for form in padded_and_cased(v):
            check(f'phase {form[:26]!r} passes in its stored spelling', IQ.clean_choice(form, PHASES) == v)
    for v in TYPES:
        for form in padded_and_cased(v):
            check(f'type {form[:26]!r} passes in its stored spelling', IQ.clean_choice(form, TYPES) == v)
    for none in (None, '', '   ', '\t'):
        check(f'{none!r} is no filter', IQ.clean_choice(none, PHASES) is None and IQ.clean_choice(none, IQ.SOURCE_VALUES) is None)
    for known, label in ((IQ.SOURCE_VALUES, 'Source'), (PHASES, 'phase'), (TYPES, 'type'), ([], 'empty list'), (None, 'no list')):
        for bad in NOT_ON_ANY_LIST + ['Paid Media', 'Trailer', 'organic paid', 7, 3.5, ['paid'], {'a': 1}, True]:
            try:
                IQ.clean_choice(bad, known)
                ok = False
            except IQ.RequestRefused as e:
                ok = str(e) == IQ.FILTER_REFUSAL
            check(f'{label}: {str(bad)[:26]!r} is refused', ok)
    check('a stored value wins over a look-alike in another case', IQ.clean_choice('trailer', ['Trailer', 'trailer']) == 'trailer'
          and IQ.clean_choice('TRAILER', ['Trailer', 'trailer']) == 'Trailer')
    lines = [IQ.FILTER_REFUSAL, IQ.CAMPAIGN_REFUSAL, IQ.DATE_REFUSAL, IQ.QUESTION_REFUSAL, IQ.ASSET_REFUSAL]
    check('the filter line is the one asked for', IQ.FILTER_REFUSAL == 'That filter could not be read. Choose one from the list.')
    check('the date line is the one the dated routes already use', IQ.DATE_REFUSAL == 'That date could not be read. Choose a date from the calendar.')
    check('every line is plain: two short sentences, no dash, no internal word',
          all(x.count('.') == 2 and len(x.split()) <= 12 for x in lines)
          and not re.search(r'[\u2013\u2014]|sql|query|param|invalid|inject|whitelist|allow.?list|sanit|escape|slug|bound|server|store',
                            ' '.join(lines), re.I), lines)


# --------------------------------------------------------------- 2
def assets_statement(store):
    reads = store.reads()
    return reads[-1] if reads else (None, None)


def section_assets():
    print('2. The asset read')
    with Patched() as p:
        base = IQ.get_assets(SLUG, window='all')
        base_sql, base_params = assets_statement(p.store)
    check('no filter: one read, the campaign id bound, nothing looked up',
          len(p.store.calls) == 1 and base_params == {'slug': SLUG} and base.get('success') is not None, p.store.calls)
    check('the campaign id is never written into the statement', SLUG not in base_sql and 'a.title_slug = {slug:String}' in base_sql)
    check('no filter means no filter clause', '{phase:String}' not in base_sql and '{asset_type:String}' not in base_sql
          and '{source:String}' not in base_sql)

    # every legitimate value: bound, in its stored spelling, the statement text the same whatever the value
    for kind, values, key, mark in (('phase', PHASES, 'phase', 'a.phase_name = {phase:String}'),
                                    ('asset_type', TYPES, 'asset_type', 'a.asset_type = {asset_type:String}'),
                                    ('paid_or_organic', list(IQ.SOURCE_VALUES), 'source', 'a.paid_or_organic = {source:String}')):
        texts = set()
        for v in values:
            for form in padded_and_cased(v):
                with Patched() as p:
                    res = IQ.get_assets(SLUG, window='all', **{kind: form})
                    sql, params = assets_statement(p.store)
                ok = (sql is not None and mark in sql and params.get(key) == v and params.get('slug') == SLUG
                      and not res.get('refused') and set(params) == {'slug', key})
                # the value is only ever beside the statement, never in it
                # (Source values are also words in a column name, so those are looked for between quotes)
                if kind == 'paid_or_organic':
                    ok = ok and all(f"'{v}'" not in s and f"'{form.strip()}'" not in s for s in p.store.statements())
                else:
                    ok = ok and all(v not in s and form.strip() not in s for s in p.store.statements())
                texts.add(sql)
                check(f'{kind} {form[:24]!r}: runs, bound as {v[:24]!r}, not in the statement', ok, (sql, params, res))
        check(f'{kind}: one statement text for every value', len(texts) == 1, len(texts))
        if kind != 'paid_or_organic':
            check(f'{kind}: the only other statements are the two fixed look-ups, with only the campaign id',
                  [c for c in p.store.calls if 'SELECT DISTINCT' in c[0]] == [(LOOKUP_1, {'slug': SLUG}), (LOOKUP_2, {'slug': SLUG})],
                  p.store.calls[:2])
        else:
            check('Source: nothing is looked up at all', len(p.store.calls) == 1)

    with Patched() as p:
        IQ.get_assets(SLUG, phase=' trailer launch ', asset_type='OFFICIAL TRAILER', paid_or_organic='Paid',
                      window='custom', from_date='2026-01-01', to_date='2026-06-30')
        sql, params = assets_statement(p.store)
    check('all filters and a custom range together: every value bound',
          params == {'slug': SLUG, 'phase': 'Trailer Launch', 'asset_type': 'Official Trailer', 'source': 'paid',
                     'lo': '2026-01-01', 'hi': '2026-06-30'}, params)
    check('the range is bound too', 'date >= toDate({lo:String})' in sql and 'date <= toDate({hi:String})' in sql
          and '2026-01-01' not in sql and '2026-06-30' not in sql)
    with Patched() as p:
        IQ.get_assets(SLUG, window='custom', from_date='2025-03-03', to_date='2025-09-09')
        sql2, _ = assets_statement(p.store)
        IQ.get_assets(SLUG, window='custom', from_date='2026-01-01', to_date='2026-06-30')
        sql3, _ = assets_statement(p.store)
    check('one statement text for every range', sql2 == sql3)

    # values on no list: refused, the read never built, the value in no statement and no parameter
    for kind in ('phase', 'asset_type', 'paid_or_organic'):
        for bad in NOT_ON_ANY_LIST:
            with Patched(snap=SNAP) as p:
                res = IQ.get_assets(SLUG, window='all', **{kind: bad})
            everything = p.store.everything()
            ok = refused_with(res, IQ.FILTER_REFUSAL) and says_nothing_of(res, bad)
            ok = ok and p.store.reads() == []                       # the read itself was never built
            if bad == '{slug:String}':
                # this one spells the look-up's own placeholder, so it is looked for among the parameters
                ok = ok and all(bad not in json.dumps(prm) for _, prm in p.store.calls)
            else:
                ok = ok and bad not in everything and json.dumps(bad)[1:-1] not in everything
            ok = ok and all(s in (LOOKUP_1, LOOKUP_2) and prm == {'slug': SLUG} for s, prm in p.store.calls)
            if kind == 'paid_or_organic':
                ok = ok and p.store.calls == [] and p.clients == 0  # a fixed list: nothing is asked of the store
            check(f'{kind} {bad[:22]!r}: refused, the read never built, the value nowhere', ok, (res, p.store.calls[:3]))
    for kind in ('phase', 'asset_type'):
        for bad in ("paid\x00", "paid\n; SELECT 1", "a" * 500, 7, ['Trailer Launch'], {'x': 1}):
            with Patched() as p:
                res = IQ.get_assets(SLUG, window='all', **{kind: bad})
            check(f'{kind} {str(bad)[:18]!r}: refused on its shape, nothing asked of the store',
                  refused_with(res, IQ.FILTER_REFUSAL) and p.store.calls == [] and p.clients == 0, (res, p.store.calls))
    for bad in ('Paid Media', 'Trailer', 'Launch', 'Branding', 'Official'):
        with Patched() as p:
            res = IQ.get_assets(SLUG, window='all', phase=bad)
        check(f'phase {bad!r}: a well-formed value that is not this campaign\'s is refused too',
              refused_with(res, IQ.FILTER_REFUSAL) and p.store.reads() == [], res)
    with Patched() as p:
        res = IQ.get_assets(SLUG, window='all', phase='Trailer Launch', asset_type="x' OR '1'='1")
    check('one good filter does not carry a bad one', refused_with(res, IQ.FILTER_REFUSAL) and p.store.reads() == [])
    with Patched(store=Store(phases=[], types=[])) as p:
        res = IQ.get_assets(SLUG, window='all', phase='Trailer Launch')
    check('a campaign with no stored values accepts no phase', refused_with(res, IQ.FILTER_REFUSAL) and p.store.reads() == [])

    # nothing given keeps today's behaviour
    for none in (None, '', '   '):
        with Patched() as p:
            res = IQ.get_assets(SLUG, window='all', phase=none, asset_type=none, paid_or_organic=none)
            sql, params = assets_statement(p.store)
        check(f'{none!r} for every filter: the unfiltered read, as before', sql == base_sql and params == {'slug': SLUG} and not res.get('refused'))

    # dates and the campaign id
    for bad in NOT_A_DAY:
        with Patched() as p:
            r1 = IQ.get_assets(SLUG, window='custom', from_date=bad)
            r2 = IQ.get_assets(SLUG, window='custom', from_date='2026-01-01', to_date=bad)
        check(f'range day {str(bad)[:22]!r}: refused, nothing asked of the store',
              refused_with(r1, IQ.DATE_REFUSAL) and refused_with(r2, IQ.DATE_REFUSAL) and p.store.calls == [] and p.clients == 0
              and says_nothing_of(r1, bad), (r1, p.store.calls))
    for bad in NOT_A_CAMPAIGN:
        with Patched(snap=SNAP) as p:
            res = IQ.get_assets(bad, window='all')
        check(f'campaign id {str(bad)[:22]!r}: refused, nothing asked of the store or the snapshot',
              refused_with(res, IQ.CAMPAIGN_REFUSAL) and p.store.calls == [] and p.clients == 0 and p.snaps == 0
              and says_nothing_of(res, bad), (res, p.store.calls))

    # a campaign held on the snapshot only (the store has no rows for it)
    with Patched(store=Store(phases=[], types=[]), snap=SNAP) as p:
        for v in PHASES:
            res = IQ.get_assets(SLUG, window='all', phase=v.upper())
            want = [a['asset_id'] for a in ASSETS if a['phase_name'] == v]
            check(f'snapshot campaign, phase {v[:22]!r}: its own cards', not res.get('refused')
                  and [c['asset_id'] for c in res.get('cards', [])] == want, res)
        res = IQ.get_assets(SLUG, window='all', asset_type='paid digital', paid_or_organic='PAID')
        check('snapshot campaign, type and Source together', [c['asset_id'] for c in res.get('cards', [])] == ['a2'], res)
        res = IQ.get_assets(SLUG, window='all', paid_or_organic='official')
        check('snapshot campaign, Source "official"', [c['asset_id'] for c in res.get('cards', [])] == ['a4'], res)
        res = IQ.get_assets(SLUG, window='all')
        check('snapshot campaign, no filter: every card', len(res.get('cards', [])) == 4, res)
        for bad in NOT_ON_ANY_LIST[:8]:
            res = IQ.get_assets(SLUG, window='all', asset_type=bad)
            check(f'snapshot campaign, type {bad[:20]!r}: refused', refused_with(res, IQ.FILTER_REFUSAL) and 'cards' not in res, res)
    with Patched(snap=SNAP, down=True) as p:
        res = IQ.get_assets(SLUG, window='all', phase='branding (t-1)')
        check('store unreachable: the snapshot still answers, held to its own values',
              [c['asset_id'] for c in res.get('cards', [])] == ['a2'], res)
        res = IQ.get_assets(SLUG, window='all', phase="Branding (T-1)' --")
        check('store unreachable: a value on no list is still refused', refused_with(res, IQ.FILTER_REFUSAL))


# --------------------------------------------------------------- 3
def section_in_flight():
    print('3. The in-flight read')
    texts = {}
    for w in (None, 'ytd', '14d', '30d', '90d', '365d', 'all'):
        with Patched() as p:
            res = IQ.get_in_flight(SLUG, window=w)
            a = p.store.calls[0]
        with Patched() as p:
            res2 = IQ.get_in_flight(SLUG, as_of='2026-02-20', window=w)
            b = p.store.calls[0]
        with Patched() as p:
            IQ.get_in_flight(SLUG, as_of='2025-08-08', window=w)
            c = p.store.calls[0]
        texts[w] = (a[0], b[0])
        check(f'window {w!r}: runs, the campaign id bound and not written in',
              not res.get('refused') and a[1] == {'slug': SLUG} and SLUG not in a[0] and 'a.title_slug = {slug:String}' in a[0], a)
        check(f'window {w!r} with an as-of day: the day bound and not written in',
              not res2.get('refused') and b[1] == {'slug': SLUG, 'as_of': '2026-02-20'} and '2026-02-20' not in b[0]
              and 'a.posted_date <= toDate({as_of:String})' in b[0], b)
        if w in ('ytd', None):
            # the year-to-date start is worked out from the as-of year (a whole number), so the text differs only there
            check(f'window {w!r}: the as-of year sets the start, nothing else of the day is written in',
                  "toDate('2026-01-01')" in b[0] and "toDate('2025-01-01')" in c[0] and '2025-08-08' not in c[0])
        else:
            check(f'window {w!r}: one statement text for every as-of day', b[0] == c[0])
    check('the rolling windows count back from the bound day', 'addDays(toDate({as_of:String}), -14)' in texts['14d'][1]
          and 'addDays(today(), -14)' in texts['14d'][0])
    with Patched() as p:
        IQ.get_in_flight(SLUG, window="14d' OR '1'='1")
        sql, params = p.store.calls[0]
    check('a window that is not on the list falls back to year to date, as before, and is never written in',
          "OR '1'='1" not in sql and params == {'slug': SLUG} and 'a.posted_date >= toDate(' in sql)
    for bad in NOT_A_DAY:
        with Patched(snap=SNAP) as p:
            res = IQ.get_in_flight(SLUG, as_of=bad, window='ytd')
        check(f'as-of {str(bad)[:22]!r}: refused, nothing asked of the store',
              refused_with(res, IQ.DATE_REFUSAL) and p.store.calls == [] and p.clients == 0 and says_nothing_of(res, bad), res)
    for none in (None, '', '  '):
        with Patched() as p:
            res = IQ.get_in_flight(SLUG, as_of=none)
        check(f'as-of {none!r}: today, as before', not res.get('refused') and p.store.calls[0][1] == {'slug': SLUG})


# --------------------------------------------------------------- 4
READS = [
    ('get_overview', lambda s: IQ.get_overview(s)),
    ('get_assets', lambda s: IQ.get_assets(s, window='all')),
    ('get_asset_timeseries', lambda s: IQ.get_asset_timeseries(s, 5, window='ytd')),
    ('get_audiences', lambda s: IQ.get_audiences(s)),
    ('get_cohorts', lambda s: IQ.get_cohorts(s)),
    ('get_demographics', lambda s: IQ.get_demographics(s)),
    ('get_in_flight', lambda s: IQ.get_in_flight(s)),
    ('question q1', lambda s: IQ.answer_question(s, 'q1')),
    ('question q2', lambda s: IQ.answer_question(s, 'q2')),
    ('question q3', lambda s: IQ.answer_question(s, 'q3')),
    ('question q4', lambda s: IQ.answer_question(s, 'q4')),
    ('question q5', lambda s: IQ.answer_question(s, 'q5')),
    ('question q6', lambda s: IQ.answer_question(s, 'q6')),
]


def section_campaign_id():
    print('4. The campaign id on every read')
    real_demo = IQ._load_gen_pop_demographics
    IQ._load_gen_pop_demographics = lambda: {}
    try:
        for name, run in READS:
            # q6 only reaches its last statement when the store names cohorts
            rows = [('weekly', 'Weekly', 10, 0.1)] if name == 'question q6' else None
            with Patched(store=Store(rows=rows), snap=SNAP) as p:
                res = run(SLUG)
            used = [c for c in p.store.calls if '{slug:String}' in c[0]]
            check(f'{name}: the campaign id is bound wherever it is used, never written in',
                  not (isinstance(res, dict) and res.get('refused'))
                  and all(SLUG not in s and not re.search(r"title_slug\s*=\s*'", s) for s in p.store.statements())
                  and all(prm.get('slug') == SLUG for s, prm in used)
                  and all('{slug:String}' in s for s, prm in p.store.calls if prm)
                  and (name == 'get_demographics' or len(used) >= 1), (res if not used else used[0]))
            bad_ids = ["goat' OR '1'='1", "goat'--", "../registry", "goat; SELECT 1", "{slug:String}", "goat UNION SELECT 1"]
            if name != 'get_cohorts':        # the cohort list may be asked for with no campaign at all
                bad_ids.append("")
            for bad in bad_ids:
                with Patched(snap=SNAP) as p:
                    res = run(bad)
                check(f'{name}: campaign id {bad[:18]!r} refused, nothing asked of the store or the snapshot',
                      refused_with(res, IQ.CAMPAIGN_REFUSAL) and p.store.calls == [] and p.clients == 0 and p.snaps == 0
                      and says_nothing_of(res, bad), (res, p.store.calls[:1]))
        with Patched(store=Store(rows=[('weekly', 'Weekly', 10, 0.1)])) as p:
            IQ.answer_question(SLUG, 'q6')
            last_sql, last_params = p.store.calls[-1]
        check('q6: the cohort list is bound as a list, never written in',
              'cohort_slug IN {cohorts:Array(String)}' in last_sql and last_params.get('cohorts') == ['weekly']
              and "'weekly'" not in last_sql, (last_sql[-200:], last_params))
        with Patched() as p:
            res = IQ.get_cohorts(None)
        check('the cohort list with no campaign still answers', res.get('success') is True and not res.get('refused'))
    finally:
        IQ._load_gen_pop_demographics = real_demo
    real_s3, keys = IQ._s3, []

    class S3:
        def get_object(self, Bucket=None, Key=None):
            keys.append(Key)
            raise KeyError('none')
    IQ._s3 = lambda: S3()
    try:
        for bad in NOT_A_CAMPAIGN:
            IQ._load_normalized_snapshot(bad)
        check('the snapshot is never looked up for a campaign id that is not well formed', keys == [], keys)
        IQ._load_normalized_snapshot(' zz_campaign_77 ')
        check('a well-formed id names exactly its own snapshot', keys == ['intent/zz_campaign_77/source/normalized_assets.json'], keys)
    finally:
        IQ._s3 = real_s3


# --------------------------------------------------------------- 5
def section_questions_and_touchpoints():
    print('5. Questions and the touchpoint read')
    for q in list(IQ.QUESTIONS) + ['Q1', ' q2 ', 'Q6']:
        with Patched() as p:
            res = IQ.answer_question(SLUG, q)
        check(f'question {q!r} is answered', not res.get('refused') and res.get('question_id') == q.strip().lower(), res)
    for bad in ("q1' OR '1'='1", 'q7', 'q1; SELECT 1', '', None, 7, '{slug:String}', 'audiences', "q1'--"):
        with Patched() as p:
            res = IQ.answer_question(SLUG, bad)
        check(f'question {str(bad)[:18]!r}: refused, nothing asked of the store',
              refused_with(res, IQ.QUESTION_REFUSAL) and p.store.calls == [] and says_nothing_of(res, bad)
              and res.get('available') == list(IQ.QUESTIONS), res)
    for bad in ('abc', None, 'x' * 30, "5 OR 1=1", [5]):
        with Patched() as p:
            res = IQ.get_asset_timeseries(SLUG, bad)
        check(f'asset id {str(bad)[:14]!r}: refused, nothing asked of the store',
              refused_with(res, IQ.ASSET_REFUSAL) and p.store.calls == [] and says_nothing_of(res, bad), res)
    with Patched() as p:
        IQ.get_asset_timeseries(SLUG, '42')
    check('an asset id is used as a whole number only', any('asset_id = 42' in s for s in p.store.statements()))

    try:
        import mta_iq as MTA
    except Exception as e:  # noqa: BLE001
        print(f'  note the touchpoint read could not be loaded here ({type(e).__name__}); its checks were skipped')
        return
    real_load, real_ov, seen = MTA._load_cached, MTA._intent_iq.get_overview, []
    MTA._load_cached = lambda slug, as_of: (seen.append((slug, as_of)) or {'success': True, 'kept': True})
    MTA._intent_iq.get_overview = lambda slug: (seen.append(('overview', slug)) or {'success': False})
    try:
        for bad in ("goat' OR '1'='1", '../registry', 'goat/../../x', 'goat; SELECT 1', '{slug:String}'):
            del seen[:]
            res = MTA.compute_mta_coefficients(bad)
            check(f'touchpoints: campaign id {bad[:18]!r} refused before anything is looked up',
                  refused_with(res, IQ.CAMPAIGN_REFUSAL) and seen == [] and says_nothing_of(res, bad), (res, seen))
        for bad in ('../../x', '2026-13-45', '01/30/2026', 'today()', "x' OR 1=1", '2026/01/30', '{as_of:String}'):
            del seen[:]
            res = MTA.compute_mta_coefficients(SLUG, as_of=bad)
            check(f'touchpoints: as-of {bad[:14]!r} refused before anything is looked up',
                  refused_with(res, IQ.DATE_REFUSAL) and seen == [], (res, seen))
        del seen[:]
        res = MTA.compute_mta_coefficients(SLUG, as_of='2026-01-30')
        check('touchpoints: a campaign id and a day read their own kept answer', seen == [(SLUG, '2026-01-30')] and res.get('kept'), seen)
        del seen[:]
        MTA.compute_mta_coefficients(SLUG, as_of='2026-01-30T08:00:00')
        check('touchpoints: a day with a time after it is read by its day, as before', seen == [(SLUG, '2026-01-30')], seen)
        del seen[:]
        MTA.compute_mta_coefficients(SLUG)
        check('touchpoints: no as-of is today, as before', len(seen) == 1 and seen[0][0] == SLUG and IQ.clean_day(seen[0][1]) == seen[0][1], seen)
    finally:
        MTA._load_cached, MTA._intent_iq.get_overview = real_load, real_ov


# --------------------------------------------------------------- 6
def section_source():
    print('6. The module source')
    src = open(os.path.join(ROOT, 'intent_iq.py'), encoding='utf-8').read()
    for name in ('title_slug', 'phase', 'asset_type', 'paid_or_organic', 'as_of', 'from_date', 'to_date', 'qid', 'window',
                 'slugs_in', 'slug', 'audience'):
        check(f'{name} is never written into a statement between quotes',
              not re.search(r"'\{" + name + r"(?:![rs])?\}'", src) and not re.search(r'"\{' + name + r'(?:![rs])?\}"', src))
    check('no list of ids is joined into a statement', 'slugs_in' not in src and '",".join("\'"' not in src)
    # every statement that names the campaign binds it
    stmts = re.findall(r'title_slug\s*=\s*(\S+)', '\n'.join(l for l in src.splitlines() if 'title_slug =' in l and ('WHERE' in l or 'a.title_slug' in l)))
    check('every statement that names the campaign binds it',
          len(stmts) >= 20 and all(s.startswith('{slug:String}') or s.startswith('{{slug:String}}') for s in stmts), stmts[:30])
    q = len(re.findall(r'\bch\.query\(', src))
    bound = len(re.findall(r'parameters\s*=', src))
    check('the read hands its values to the store as parameters', bound >= 22 and q >= 27, (q, bound))
    check('the lines never repeat what was sent', 'Unknown question: {' not in src and 'Invalid asset_id: {' not in src)
    msrc = open(os.path.join(ROOT, 'mta_iq.py'), encoding='utf-8').read()
    head = msrc[msrc.index('def compute_mta_coefficients('):]
    check('the touchpoint read checks the campaign id and the day before it looks anything up',
          0 < head.index('_intent_iq.slug_ok(slug)') < head.index('_load_cached(slug, as_of_iso)')
          and head.index('_intent_iq.clean_day(') < head.index('_load_cached(slug, as_of_iso)'))


# --------------------------------------------------------------- 7
def route_source(app_src, name):
    m = re.search(r'\ndef ' + re.escape(name) + r'\(.*?(?=\n@app\.route|\n@app\.before_request|\ndef [a-zA-Z_])', app_src, re.S)
    return m.group(0) if m else ''


def section_routes():
    print('7. The routes')
    app_src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
    gate = route_source(app_src, '_require_intent_iq')
    check('one check for every campaign route, ahead of the allow-list',
          '_intent_iq.slug_ok(title_slug)' in gate and gate.index('slug_ok(') < gate.index('_user_intent_iq_title_access(')
          and "_intent_iq.CAMPAIGN_REFUSAL}), 400)" in gate)
    routes = re.findall(r"@app\.route\('(/api/intent/<title_slug>[^']*)'[^\n]*\n(?:@[^\n]+\n)*def (\w+)\(", app_src)
    check('every campaign route is found', len(routes) >= 11, routes)
    for path, fn in routes:
        body = route_source(app_src, fn)
        check(f'{path} passes the check before it does anything',
              '_require_intent_iq(title_slug)' in body
              and all(body.index('_require_intent_iq(title_slug)') < body.index(w)
                      for w in ('_intent_iq.get_', '_intent_iq.answer_', '_mta_iq.compute', 'build_workbook(', 'build_weekly_pdf(',
                                '_load_normalized_snapshot(') if w in body), body[:200])
    for fn in ('api_intent_overview', 'api_intent_assets', 'api_intent_asset_timeseries', 'api_intent_audiences', 'api_intent_cohorts',
               'api_intent_question', 'api_intent_demographics', 'api_intent_in_flight', 'api_intent_mta'):
        body = route_source(app_src, fn)
        check(f'{fn} answers 400 for a refused value', '_intent_iq_json(' in body and 'return jsonify(_intent_iq.' not in body)
    try:
        import flask
    except ImportError:
        print('  note flask is not on this machine: the route runs were skipped')
        return
    import contextlib
    import traceback as _tb
    fapp = flask.Flask('attribution_request_values')
    user = {'role': 'user'}
    ns = {'request': flask.request, 'jsonify': flask.jsonify, 'Response': flask.Response, 'traceback': _tb, 're': re,
          '_intent_iq': IQ, '_mta_iq': None,
          'get_current_user': lambda: user, '_normalize_role': lambda r: r,
          'compute_product_access_flags': lambda u, r: {'has_intent_iq_access': True},
          'apply_cloak_product_access_overrides': lambda a: a,
          '_user_intent_iq_title_access': lambda u: (False, True, set()),
          '_dated_request_refused': lambda **k: None}
    for name in ('_intent_iq_json', '_require_intent_iq', 'api_intent_overview', 'api_intent_assets', 'api_intent_in_flight',
                 'api_intent_question', 'api_intent_audiences', 'api_intent_demographics', 'api_intent_cohorts'):
        exec(route_source(app_src, name), ns)

    def hit(fn, path, **kw):
        with fapp.test_request_context(path), contextlib.redirect_stderr(io.StringIO()):
            out = ns[fn](**kw)
        resp, code = out if isinstance(out, tuple) else (out, 200)
        return code, (resp.get_json() or {})

    from urllib.parse import quote
    with Patched(snap=SNAP) as p:
        for bad in ("goat' OR '1'='1", "goat'--", 'goat; SELECT 1', '{slug:String}', 'go at', 'goat UNION SELECT 1'):
            for fn in ('api_intent_overview', 'api_intent_assets', 'api_intent_in_flight', 'api_intent_audiences',
                       'api_intent_demographics', 'api_intent_cohorts'):
                code, js = hit(fn, '/x', title_slug=bad)
                check(f'{fn}: campaign id {bad[:16]!r} answers 400 with the plain line, nothing asked of the store',
                      (code, js.get('error'), js.get('refused')) == (400, IQ.CAMPAIGN_REFUSAL, True) and p.store.calls == []
                      and p.clients == 0 and bad not in json.dumps(js), (code, js))
        for kind in ('phase', 'asset_type', 'paid_or_organic'):
            for bad in NOT_ON_ANY_LIST[:10]:
                del p.store.calls[:]
                code, js = hit('api_intent_assets', f'/x?window=all&{kind}={quote(bad)}', title_slug=SLUG)
                check(f'asset route, {kind} {bad[:18]!r}: 400, the plain line, the read never built',
                      (code, js.get('error')) == (400, IQ.FILTER_REFUSAL) and p.store.reads() == []
                      and bad not in p.store.everything() and bad not in json.dumps(js), (code, js, p.store.calls[:2]))
        del p.store.calls[:]
        code, js = hit('api_intent_assets', '/x?window=all&phase=' + quote('trailer launch') + '&paid_or_organic=Organic', title_slug=SLUG)
        sql, params = assets_statement(p.store)
        check('asset route, known values in another case: 200, bound in their stored spelling',
              code == 200 and params == {'slug': SLUG, 'phase': 'Trailer Launch', 'source': 'organic'}, (code, params))
        del p.store.calls[:]
        code, js = hit('api_intent_assets', '/x?window=all', title_slug=SLUG)
        check('asset route, no filter: 200, as before', code == 200 and assets_statement(p.store)[1] == {'slug': SLUG})
        code, js = hit('api_intent_question', '/x', title_slug=SLUG, qid="q1' OR '1'='1")
        check('question route: a question on no list answers 400', (code, js.get('error')) == (400, IQ.QUESTION_REFUSAL), (code, js))
        code, js = hit('api_intent_question', '/x', title_slug=SLUG, qid='q1')
        check('question route: a known question answers 200', code == 200 and js.get('question_id') == 'q1', (code, js))
    # a seat held to its own campaigns is still told so for a well-formed id it does not hold
    ns['_user_intent_iq_title_access'] = lambda u: (False, False, {'goat'})
    code, js = hit('api_intent_overview', '/x', title_slug='chime')
    check('the allow-list still answers 403 for a campaign a seat does not hold', code == 403, (code, js))
    code, js = hit('api_intent_overview', '/x', title_slug="chime' OR '1'='1")
    check('and the shape check comes first', (code, js.get('error')) == (400, IQ.CAMPAIGN_REFUSAL), (code, js))


# --------------------------------------------------------------- 8
def section_page():
    print('8. The page')
    html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()

    def options(select_id):
        m = re.search(r'<select id="' + select_id + r'"[^>]*>(.*?)</select>', html, re.S)
        return re.findall(r'<option value="([^"]*)"', m.group(1)) if m else None

    src_opts = options('iiqAssetFilterPaid')
    check('the Source control is found', src_opts and src_opts[0] == '', src_opts)
    for v in src_opts or []:
        if v:
            check(f'Source option {v!r} passes', IQ.clean_choice(v, IQ.SOURCE_VALUES) == v)
        else:
            check('Source "All" is no filter', IQ.clean_choice(v, IQ.SOURCE_VALUES) is None)
    win = options('iiqAssetWindow')
    check('the asset Window control is found', win == ['ytd', '14d', '30d', '90d', '365d', 'all', 'custom'], win)
    for w in win or []:
        with Patched(snap=SNAP) as p:
            res = IQ.get_assets(SLUG, window=w, from_date='2026-01-01' if w == 'custom' else None)
        check(f'asset Window {w!r} runs', res.get('success') is True and res.get('window') == w, res)
    fwin = options('iiqInFlightWindow')
    check('the in-flight Window control is found', fwin == ['ytd', '14d', '30d', '90d', '365d', 'all'], fwin)
    for w in fwin or []:
        with Patched() as p:
            res = IQ.get_in_flight(SLUG, window=w)
        check(f'in-flight Window {w!r} runs', not res.get('refused'))
    check('the Phase control is filled from the campaign\'s own phases',
          "(ov.phases || []).forEach(function(p) { phaseSel.appendChild(new Option(p.phase_name, p.phase_name)); });" in html)
    check('the Type control is filled from the asset types the read sent back',
          "cards.map(function(c) { return c.asset_type; })" in html and "typeSel.appendChild(new Option(t, t))" in html)
    # so: every phase the overview lists, and every type the read sends back, passes
    with Patched(snap=SNAP) as p:
        ov = IQ.get_overview(SLUG)
    with Patched(store=Store(phases=[], types=[]), snap=SNAP) as p:
        cards = IQ.get_assets(SLUG, window='all').get('cards', [])
        for ph in [x['phase_name'] for x in ov.get('phases', [])]:
            res = IQ.get_assets(SLUG, window='all', phase=ph)
            check(f'every phase the page lists passes: {ph[:24]!r}', not res.get('refused'), res)
        for t in sorted({c['asset_type'] for c in cards}):
            res = IQ.get_assets(SLUG, window='all', asset_type=t)
            check(f'every type the page lists passes: {t[:24]!r}', not res.get('refused') and len(res.get('cards', [])) >= 1, res)
    check('the page shows the read\'s own line when a filter is refused',
          "document.getElementById('iiqAssetMeta').textContent = 'Failed: ' + (e.message || e);" in html
          and "if (!d.success) throw new Error(d.error || 'Failed');" in html)
    qids = set(re.findall(r"iiqLoadQuestion\('(q\d)'", html)) | set(re.findall(r"data-qid=\"(q\d)\"", html))
    check('every question the page can ask is one the read knows', qids <= set(IQ.QUESTIONS), qids)


def main():
    section_values()
    section_assets()
    section_in_flight()
    section_campaign_id()
    section_questions_and_touchpoints()
    section_source()
    section_routes()
    section_page()
    print(f'\n{PASS} passed, {len(FAIL)} failed')
    for f in FAIL:
        print('  FAILED: ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
