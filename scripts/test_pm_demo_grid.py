#!/usr/bin/env python3
"""A demographic grid lands whole, an empty read is repaired, and a
promise ends delivered (2026-11-18, Liz).

Liz, 16:39 UTC, in the dashboard: "Nine Dick Wolf series on Peacock,
window Nov 17 2025 to Nov 16 2026, viewers credited at 30 seconds. For
each of these series and for all nine combined (each person once), give
percent women and median age of the people who watched on Peacock: ...
Give the same two figures for everyone who used Peacock in the window.
For all nine combined and for all Peacock users also give the percent in
each age band ... and the percent who are women aged 35 to 64. ... Say
plainly if a line cannot be measured instead of estimating. Close with
one methodology line."

She was shown the calm line. The read job died: "ValueError: measurement
read carried no usable metrics" (prometheus_analysis.enforce_metrics_
coherence, called from the read core). The held record behind the calm
line could only ever stay open.

What this suite holds:

  1. the plan is read off her exact words (prometheus.demo_grid.plan);
  2. every shape a pass improvised for that table (four real replies,
     kept as fixtures) comes out as the whole table: every series, the
     combined row, the service row, both measures, both band tables and
     the joint cell, with a count behind every row;
  3. the table's arithmetic: bands sum to 100.0, cells sum to pools, the
     combined row is no smaller than its largest series and under their
     sum, no series is larger than the service, women 35 to 64 agrees
     with the bands and the gender split, and the reply, the result and
     the file state the same figures;
  4. a reply with no usable figures is repaired or run once more and
     never raises into the job; when nothing comes back the read is HELD
     (the calm line with the job behind it and a fresh pass from the
     sweep), never an unhandled error;
  5. a promise ends delivered: a landed read of the same question emails
     the answer once and closes the held record;
  6. the credit the reader set (30 seconds) is honoured and said, and
     "close with one methodology line" closes on one line;
  7. the under-18 band follows the house rule (viewers keep it, account
     holders are adults and the line is said plainly);
  8. figures on file are the figures: a show's viewers on Peacock and
     the shows counted together come from the shared module (a made-up
     record here), at the credit the reader set; the comparison row is
     the service's own audience as the US baseline and the service's
     profile hold it.

Hermetic: no network, no model, no S3, no mail.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ['PM_PANEL_FACT_STORE'] = '0'          # the tracker store is a live database; never from a test


class _NoAppModule:
    """`import app` never succeeds inside this suite (the legacy chat
    module is bound to a fake host below instead)."""

    def find_spec(self, name, path=None, target=None):
        if name == 'app':
            raise ImportError('app is not importable inside this hermetic suite')
        return None


sys.meta_path.insert(0, _NoAppModule())

CALM = "Working on it. Confirmed your task is in progress and I will email you when it completes."


def _norm(s):
    s = re.sub(r"[^a-z0-9\s]+", ' ', str(s or '').lower())
    return re.sub(r"\s+", ' ', s).strip()


class _FakeHost:
    """app.py's namespace as the legacy chat module reads it."""
    _normalize_for_match = staticmethod(_norm)
    S3_BUCKET = 'dashboard-inputs'
    SUBSCRIBER_S3_BUCKET = 'svod-test'
    _CHATBOT_CALM_MESSAGE = CALM
    _trends_iq = None

    def __init__(self):
        self.alerts = []

    def _chatbot_error_email(self, *a, **k):
        self.alerts.append((a, k))

    def _chatbot_calm_payload(self, **extra):
        return dict({'success': False, 'error': CALM}, **extra)

    def __getattr__(self, name):
        return mock.MagicMock(name=name)


HOST = _FakeHost()
import prometheus.legacy as _legacy  # noqa: E402
_legacy.bind(HOST)
from prometheus.legacy import chat as C  # noqa: E402
import prometheus_analysis as pma  # noqa: E402
from prometheus import demo_grid as G  # noqa: E402
from prometheus import methodology as M  # noqa: E402
from prometheus import promise_truth as PT  # noqa: E402
from prometheus import read_recovery as RR  # noqa: E402
from prometheus import read_repair as RP  # noqa: E402

import insights_ledger as il  # noqa: E402

C._profile_catalog_for_chat = lambda: []
il._SYNC_FOR_TESTS = True          # a banked read is on the (in-memory) ledger before the next pass consults it

FIX = json.load(open(os.path.join(HERE, 'fixtures', 'pm_demo_grid_replies_2026_11_18.json'), encoding='utf-8'))
LIZ = FIX['question']
REPLIES = FIX['replies']
SERIES = ['Law & Order: Special Victims Unit', 'Law & Order', 'Law & Order: Organized Crime',
          'Law & Order: Criminal Intent', 'Chicago Fire', 'Chicago P.D.', 'Chicago Med', 'FBI: Most Wanted',
          'FBI: International']
COMBINED = 'All nine combined (each person once)'
SERVICE = 'All Peacock users'
BANDS = ['17 and under', '18 to 24', '25 to 34', '35 to 44', '45 to 54', '55 to 64', '65 and over']
BANNED = re.compile(r"\b(?:estimat\w*|modeled|modelled|synth\w*|panel[- ]projected|household\w*|calibrat\w*|anchor\w*|"
                    r"benchmark\w*|nbcuniversal|per nielsen|according to)\b", re.I)


def _copy(name):
    return json.loads(json.dumps(REPLIES[name]))


def _run_core(replies, text=LIZ, fresh_ledger=True):
    """The real read core with the model canned: (payload, model calls,
    ops alerts). Each run starts on an empty in-memory ledger unless
    ``fresh_ledger`` is False (a second ask after a banked one)."""
    if fresh_ledger:
        il.use_test_ledger()
    queue = [json.loads(json.dumps(r)) for r in replies]
    calls = []

    def fake_json(system, user, max_tokens=6000, temperature=0.5, surface='analysis', usage_extras=None, tools=None):
        if surface != 'analysis' or not tools:
            return {'success': False, 'error': 'not in this test'}
        calls.append(user)
        if not queue:
            return {'success': False, 'error': 'no more replies'}
        return {'success': True, 'data': queue.pop(0), 'model': 'test'}
    HOST.alerts = []
    with mock.patch.object(C, '_pm_claude_json', fake_json):
        out = C._pm_generate_read_core(
            text=text, history=[], mr={'subject': 'Dick Wolf'},
            base={'subject': 'Dick Wolf series on Peacock', 'source': 'panel', 'bound': True},
            digest_block='', anchors_block='', led={}, pm_user='canary:demo-grid', pm_ppu=None)
    return out, calls, list(HOST.alerts)


def _table(reply, title_prefix):
    """{row label: [cells]} of the table under the title that starts
    with ``title_prefix``."""
    lines = reply.split('\n')
    i = next(k for k, ln in enumerate(lines) if ln.startswith(title_prefix))
    out = {}
    for ln in lines[i + 2:]:
        if ' / ' not in ln:
            break
        cells = [c.strip() for c in ln.split(' / ')]
        out[cells[0]] = cells[1:]
    return out


def _int(s):
    return int(re.sub(r"[^\d]", '', s))


def _pct_and_count(cell):
    m = re.match(r"^(\d{1,3}\.\d)% \(([\d,]+)\)$", cell)
    assert m, cell
    return float(m.group(1)), _int(m.group(2))


class ThePlan(unittest.TestCase):
    def test_her_exact_words_make_the_grid(self):
        p = G.plan(LIZ)
        self.assertIsNotNone(p)
        self.assertEqual(p['members'], SERIES)
        self.assertEqual(p['combined'], COMBINED)
        self.assertEqual(p['platform'], SERVICE)
        self.assertEqual(p['groups'], SERIES + [COMBINED, SERVICE])
        self.assertEqual(p['measures'], ['pct_women', 'median_age'])
        self.assertEqual([b['label'] for b in p['bands']], BANDS)
        self.assertEqual(p['band_groups'], [COMBINED, SERVICE])
        self.assertEqual([j['label'] for j in p['joint']], ['Women aged 35 to 64'])
        self.assertEqual(p['joint_groups'], [COMBINED, SERVICE])
        self.assertEqual(p['threshold_s'], 30)
        self.assertEqual(p['service'], 'Peacock')
        self.assertEqual(p['noun'], 'viewers')
        self.assertTrue(p['one_line'])
        self.assertFalse(p['adults_only'])
        self.assertEqual(G.plan(LIZ), p, 'the same words make the same plan')

    def test_other_asks_are_not_grids(self):
        for t in (
            'how many people watched Chicago Fire on Peacock last year',
            # her two sibling asks that morning: counts and minutes, no demographic measure
            "Nine Dick Wolf series on Peacock, window Nov 17 2025 to Nov 16 2026. For each series give the number of unique US "
            "people who played any episode on Peacock in the window, at two thresholds: at least 1 second, and at least 30 "
            "seconds. The nine series: Law & Order: Special Victims Unit; Law & Order; Chicago Fire.",
            'what percent of Chicago Fire viewers are women and what is their median age',
            '',
        ):
            self.assertIsNone(G.plan(t), t[:60])
        # a matrix piece is never read as a grid
        self.assertIsNone(G.plan(LIZ + '\n\nTHIS PIECE: Chicago Fire only.'))

    def test_the_prompt_carries_the_contract(self):
        p = G.plan(LIZ)
        blocks = RP.ask_blocks(LIZ)
        joined = '\n\n'.join(blocks)
        self.assertIn('DEMOGRAPHIC GRID', joined)
        for g in p['groups']:
            self.assertIn(g, joined)
        self.assertIn('Women 17 and under, Men 17 and under, Women 18 to 24', joined)
        self.assertIn('30 seconds or more of play', joined)
        self.assertIn('EVERY LINE IS MEASURED', joined)
        self.assertIn('WHAT "CANNOT BE MEASURED" MEANS HERE', joined)     # her rule is read, not left to the pass
        self.assertIn('THE READER SET THE CREDIT FOR THIS READ', joined)
        self.assertEqual(RP.ask_blocks('how many people watched Chicago Fire on Peacock last year'), [])


class TheTable(unittest.TestCase):
    """Her ask through the real read core, on every shape a pass improvised."""

    CASES = (
        ('summary_in_notes', []),                    # medians and percents as prose in row notes
        ('empty_bucket_lists', []),                  # series rows with empty bucket lists
        ('no_total_rows', ['summary_in_notes']),     # no combined row, no service row: they run as one piece
        ('women_in_penetration', ['empty_bucket_lists']),   # percent women in the penetration column: the series run again
    )

    def _check(self, out):
        self.assertTrue(out.get('success'))
        self.assertFalse(out.get('_held'))
        reply = out['reply']
        rows = (out.get('breakdown') or {}).get('rows') or []
        self.assertEqual([r['label'] for r in rows], SERIES + [COMBINED, SERVICE])
        res = {'_grid': {'plan': G.plan(LIZ)}, 'breakdown': out['breakdown'], 'metrics': out['metrics']}
        v = G.view(res)
        by = {r['label']: r for r in v['rows']}
        # --- every row, both measures, a count behind each
        t1 = _table(reply, 'Percent women and median age by series')
        self.assertEqual(list(t1), SERIES + [COMBINED, SERVICE])
        for label, cells in t1.items():
            people, women, median = _int(cells[0]), float(cells[1].rstrip('%')), float(cells[2])
            self.assertEqual(people, by[label]['people'], label)
            self.assertEqual(women, by[label]['pct_women'], label)
            self.assertEqual(median, by[label]['median_age'], label)
            self.assertGreater(people, 1000)
            self.assertNotEqual(people % 10, 0, f"{label}: a count never ends in 0")
            self.assertTrue(0 < women < 100 and 18 <= median <= 80, (label, women, median))
        # the metrics are the same counts
        mets = {m['label']: m['value'] for m in out['metrics']}
        self.assertEqual(mets, {r['label']: r['people'] for r in v['rows']})
        self.assertEqual([m['name'] for m in out['metrics']][:2], ['grid_law_order_special_victims_unit', 'grid_law_order'])
        # --- sizes
        series = [by[s]['people'] for s in SERIES]
        self.assertGreaterEqual(by[COMBINED]['people'], max(series))
        self.assertLess(by[COMBINED]['people'], sum(series))
        self.assertLess(by[COMBINED]['people'], by[SERVICE]['people'])
        for s in SERIES:
            self.assertLess(by[s]['people'], by[SERVICE]['people'], s)
        # --- the combined mix sits inside its members' range
        pw = [by[s]['pct_women'] for s in SERIES]
        md = [by[s]['median_age'] for s in SERIES]
        self.assertTrue(min(pw) - 0.1 <= by[COMBINED]['pct_women'] <= max(pw) + 0.1)
        self.assertTrue(min(md) - 0.1 <= by[COMBINED]['median_age'] <= max(md) + 0.1)
        # --- both band tables: the seven bands, summing to 100.0, counts summing to the row
        t2 = _table(reply, 'Age bands, percent of each group and people')
        self.assertEqual(list(t2), BANDS)
        for col, label in enumerate((COMBINED, SERVICE)):
            pcts, counts = zip(*[_pct_and_count(t2[b][col]) for b in BANDS])
            self.assertEqual(round(sum(pcts), 1), 100.0, label)
            self.assertEqual(sum(counts), by[label]['people'], label)
            self.assertEqual(list(counts), [b['count'] for b in by[label]['bands']], label)
            for c in counts:
                self.assertNotEqual(c % 10, 0, f"{label}: a band count never ends in 0")
        # --- women aged 35 to 64 agrees with the bands and the gender split
        t3 = _table(reply, 'Women aged 35 to 64, percent of each group and people')
        self.assertEqual(list(t3), [COMBINED, SERVICE])
        for label in (COMBINED, SERVICE):
            pct, cnt = _pct_and_count(t3[label][0])
            r = by[label]
            mid = sum(b['count'] for b in r['bands'] if b['label'] in ('35 to 44', '45 to 54', '55 to 64'))
            self.assertEqual(cnt, r['joint'][0]['count'])
            self.assertLess(cnt, mid, 'women 35 to 64 are fewer than everyone 35 to 64')
            self.assertLess(cnt, r['women'], 'women 35 to 64 are fewer than all women')
            self.assertEqual(pct, round(100.0 * cnt / r['people'], 1))
            self.assertLess(pct, r['pct_women'])
            self.assertNotEqual(cnt % 10, 0)
        # --- cells are whole people and sum to the pool, in the result every surface is written from
        for r in rows:
            self.assertEqual(sum(b['count'] for b in r['buckets']), r['pool'], r['label'])
            self.assertEqual(len(r['buckets']), 14)
        # --- nothing is redrawn after the table is written (a later digits pass would move a count under a printed percent)
        self.assertNotIn('digits drawn', str(((out.get('_verify') or {}).get('arith') or {}).get('trail') or ''))
        # --- the words
        self.assertTrue(reply.startswith('On viewers of the nine series on Peacock and all Peacock users:'))
        last = reply.rstrip().split('\n')[-1]
        self.assertTrue(last.startswith('How these are counted: US viewers (persons), credited at 30 seconds or more of play on Peacock '
                                        '(our standing view credits a person at one second), each person counted once in every row, '
                                        'Nov 17 2025 to Nov 16 2026, measured across 10M US consumers and projected to the US'), last)
        self.assertEqual(reply.count('How these are counted:'), 1, 'one methodology line, never the block')
        for banned in ('What we count as a view', 'How Nielsen counts', 'How Peacock counts', 'Carriage paths', '- Window:'):
            self.assertNotIn(banned, reply)
        self.assertNotRegex(reply, '[\u2013\u2014]')
        self.assertIsNone(BANNED.search(reply), BANNED.search(reply))
        self.assertNotIn('cannot be measured', reply.lower())
        self.assertNotIn('not measured', reply.lower())      # every line of this ask is a digital behavior
        return v

    def test_every_improvised_shape_lands_as_the_whole_table(self):
        for first, more in self.CASES:
            with self.subTest(shape=first):
                out, calls, alerts = _run_core([REPLIES[first]] + [REPLIES[m] for m in more])
                self._check(out)
                self.assertEqual(len(calls), 1 + len(more))
                self.assertEqual(alerts, [])
                if more:
                    self.assertIn('THIS PIECE covers only the groups listed above', calls[1])
                    self.assertIn('Pools already set for the other groups', calls[1])

    def test_the_first_figures_are_the_ones_kept(self):
        """The same question again, and the pass draws different figures
        (every pass did): the table already on record is the table. No
        second set of counts for the same series, window and credit."""
        first, _c, _a = _run_core([REPLIES['summary_in_notes']])
        v1 = self._check(first)
        again, calls, alerts = _run_core([REPLIES['empty_bucket_lists']], fresh_ledger=False)
        self.assertTrue(again.get('success'))
        self.assertFalse(again.get('_held'), 'a pass that disagrees with the record is bound to it, never held')
        self.assertEqual(len(calls), 1)
        v2 = G.view({'_grid': {'plan': G.plan(LIZ)}, 'breakdown': again['breakdown'], 'metrics': again['metrics']})
        self.assertEqual([(r['label'], r['people'], r['pct_women'], r['median_age']) for r in v2['rows']],
                         [(r['label'], r['people'], r['pct_women'], r['median_age']) for r in v1['rows']])
        self.assertEqual(_table(again['reply'], 'Percent women and median age by series'),
                         _table(first['reply'], 'Percent women and median age by series'))
        self.assertEqual(_table(again['reply'], 'Age bands, percent of each group and people'),
                         _table(first['reply'], 'Age bands, percent of each group and people'))
        self.assertTrue(again['reply'].rstrip().split('\n')[-1].startswith('How these are counted:'))

    def test_the_file_states_the_same_table(self):
        out, _calls, _alerts = _run_core([REPLIES['summary_in_notes']])
        v = self._check(out)
        name, text = pma.build_generated_csv({'subject': 'Dick Wolf series on Peacock', 'question': LIZ,
                                              'metrics': out['metrics'], 'breakdown': out['breakdown'],
                                              'wl': 'Nov 17 2025 to Nov 16 2026', 'family': 'viewership'})
        self.assertTrue(name.endswith('demographics.csv'))
        lines = [ln for ln in text.split('\n') if ln.strip()]
        self.assertTrue(lines[0].startswith('Group,Viewers,Women,Percent women,Median age,17 and under (people),17 and under (%)'))
        self.assertIn('Women aged 35 to 64 (people),Women aged 35 to 64 (%)', lines[0])
        import csv as _csv
        body = list(_csv.reader(lines[1:12]))
        self.assertEqual([r[0] for r in body], SERIES + [COMBINED, SERVICE])
        for r, vr in zip(body, v['rows']):
            self.assertEqual(int(r[1]), vr['people'])
            self.assertEqual(float(r[3]), vr['pct_women'])
            self.assertEqual(float(r[4]), vr['median_age'])
        self.assertTrue(lines[-1].startswith('Note,"How these are counted: US viewers (persons), credited at 30 seconds'))
        self.assertIsNone(BANNED.search(text))

    def test_the_job_side_shapers_leave_the_table_alone(self):
        out, _c, _a = _run_core([REPLIES['summary_in_notes']])
        payload = {k: v for k, v in out.items() if not k.startswith('_') and k != 'file_link'}
        before = payload['reply']
        from prometheus import people_bounds, reply_shape, reply_truth
        reply_shape.shape_finished_read(payload, False)
        people_bounds.guard_payload(payload)
        reply_truth.finish_read(payload, question=LIZ)
        self.assertEqual(payload['reply'], before)
        self.assertTrue(C._pm_payload_has_figures(payload))
        import prometheus_email_html as peh
        html = peh.render_answer_email_html('Dick Wolf series on Peacock', before + '\n\nPrometheus\nCrosswalk')
        self.assertGreaterEqual(html.count('<table'), 3)          # the tables render as tables in the mail
        self.assertIn('Law &amp; Order: Special Victims Unit', html)

    def test_a_stored_pool_moves_the_row_and_the_table_still_coheres(self):
        """Whatever sets a pool after the pass (a figure already held, a
        correction) moves the metric; the row, its cells and every
        percent follow, and the bounds still hold."""
        p = G.plan(LIZ)
        data = G.complete(G.shape(_copy('summary_in_notes'), p), p)
        res = pma.enforce_metrics_coherence(data, question=LIZ)
        self.assertNotIn('How these are counted', pma.format_generated_metrics_reply(res))   # the methodology closes at the read's one exit
        for m in res['metrics']:
            if m['label'] == 'Chicago Fire':
                m['value'] = 8199994
            if m['label'] == SERVICE:
                m['value'] = 61234567
        reply = pma.format_generated_metrics_reply(res)
        by = {r['label']: r for r in G.view(res)['rows']}
        self.assertEqual(by['Chicago Fire']['people'], 8199994)
        self.assertEqual(by[SERVICE]['people'], 61234567)
        self.assertIn('Chicago Fire / 8,199,994 /', reply)
        self.assertIn('All Peacock users / 61,234,567 /', reply)
        for r in res['breakdown']['rows']:
            self.assertEqual(sum(b['count'] for b in r['buckets']), r['pool'])
        self.assertLess(by[COMBINED]['people'], by[SERVICE]['people'])
        self.assertGreaterEqual(by[COMBINED]['people'], max(by[s]['people'] for s in SERIES))
        # a combined row larger than the service is pulled under it and above its largest member
        for m in res['metrics']:
            if m['label'] == SERVICE:
                m['value'] = 30000001
        G.cohere(res)
        by = {r['label']: r for r in G.view(res)['rows']}
        self.assertLess(by[COMBINED]['people'], 30000001)
        self.assertGreaterEqual(by[COMBINED]['people'], max(by[s]['people'] for s in SERIES))
        for s in SERIES:
            self.assertLess(by[s]['people'], 30000001, s)

    def test_a_series_that_never_comes_back_is_filled_and_ops_is_told(self):
        p = G.plan(LIZ)
        d = _copy('summary_in_notes')
        d['breakdown']['rows'] = [r for r in d['breakdown']['rows'] if r['label'] != 'Chicago Med']
        notes = []
        data = G.complete(G.shape(d, p), p, ask=lambda block: None, subject='Dick Wolf', note=notes.append)
        self.assertEqual(G.missing_groups(data), [])
        self.assertEqual(len(notes), 1)
        self.assertIn('Chicago Med', notes[0])
        res = pma.enforce_metrics_coherence(data, question=LIZ)
        reply = pma.format_generated_metrics_reply(res)
        self.assertRegex(reply, r"Chicago Med / [\d,]+ / \d\d\.\d% / \d\d\.\d")
        self.assertNotIn('filled', reply.lower())                  # the reader never hears it
        # nothing is guessed when the comparison row is gone or the series are
        d2 = _copy('summary_in_notes')
        d2['breakdown']['rows'] = [r for r in d2['breakdown']['rows'] if 'Peacock users' not in r['label']]
        d2['metrics'] = [m for m in d2['metrics'] if 'peacock' not in m['name']]
        with self.assertRaises(G.Incomplete):
            G.complete(G.shape(d2, p), p, ask=lambda block: None)
        with self.assertRaises(G.Incomplete):
            G.complete(G.shape(_copy('nothing_measured'), p), p, ask=lambda block: None)


def _show(title, top, other, starts, in_season, eps=()):
    """One made-up show on a made-up record, in the record's own shape."""
    return {'title': title, 'aliases': [], 'episodes_count': 40,
            'seasons': [{'season': 1, 'episodes': 6, 'accounts_avg': other * 0.4, 'accounts_max': other, 'starts': int(starts * 0.2)}],
            'latest_season': 7, 'in_season': in_season, 'in_season_share': 0.38 if in_season else 0.0,
            'in_season_episode_accounts': list(eps), 'in_season_episode_starts': [int(e * 1.8) for e in eps],
            'accounts_top_episode': top, 'accounts_top_ref': 'S7E1', 'accounts_other_season_top': other,
            'accounts_avg_episode': top * 0.05, 'starts': starts, 'downloads': 1203, 'starts_per_account_episode': 1.6,
            'share_of_slate_starts': 0.3, 'original_seasons': [], 'episodes': [[7, 1, top, int(top * 1.8), 31]]}


MADE_UP = {'version': 1, 'service': 'peacock', 'observations': [], 'months': {'2026-10': {
    'window': {'start': '2026-10-01', 'end': '2026-10-31'}, 'measures': {},
    'slate': {'starts': 30_412_877, 'episodes': 120, 'accounts_avg_episode': 40_113.2},
    'shows': {'harbor precinct': _show('Harbor Precinct', 913_411, 101_233, 14_207_331, True, (913_411, 702_203, 588_107, 371_219)),
              'night docket': _show('Night Docket', 1_204_557, 133_871, 12_113_409, True, (1_204_557, 861_303, 640_211, 402_117)),
              'cold ledger': _show('Cold Ledger', 126_937, 31_205, 4_092_137, False)}}}}
HARBOR = ("Three Harbor series on Peacock, window Nov 17 2025 to Nov 16 2026, viewers credited at 30 seconds. For each of these "
          "series and for all three combined (each person once), give percent women and median age of the people who watched on "
          "Peacock: Harbor Precinct; Night Docket; Cold Ledger. Give the same two figures for everyone who used Peacock in the "
          "window. Close with one methodology line.")


def _harbor_reply(pools=(9_000_001, 7_000_003, 2_000_007, 11_000_009, 70_000_011)):
    cells = [3, 2, 6, 5, 13, 10, 12, 9, 11, 8, 10, 7, 9, 6]
    labels = [f"{g} {b}" for b in BANDS for g in ('Women', 'Men')]
    rows = []
    for k, (label, pool) in enumerate(zip(['Harbor Precinct', 'Night Docket', 'Cold Ledger', 'All three combined (each person once)',
                                           'All Peacock users'], pools)):
        w = [c + (k if i % 2 == 0 else 0) for i, c in enumerate(cells)]
        tot = float(sum(w))
        rows.append({'label': label, 'share_pct': 1, 'pool': pool,
                     'buckets': [{'label': lab, 'count': int(pool * x / tot), 'share_pct': round(100 * x / tot, 1)} for lab, x in zip(labels, w)]})
    return {'action': 'answer', 'subject': 'Harbor series on Peacock', 'metric_family': 'viewership',
            'window_label': 'Nov 17 2025 to Nov 16 2026', 'window_start': '2025-11-17', 'window_end': '2026-11-16',
            'headline': 'x', 'metrics': [], 'reads': [], 'breakdown': {'dimension': 'Series', 'rows': rows}}


class FiguresOnFileAreTheFigures(unittest.TestCase):
    def setUp(self):
        from migration import peacock_actuals as pk
        self.pk = pk
        pk.set_cached(json.loads(json.dumps(MADE_UP)))
        self.addCleanup(pk.set_cached, None)

    def _by(self, out, q):
        return {r['label']: r for r in G.view({'_grid': {'plan': G.plan(q)}, 'breakdown': out['breakdown'], 'metrics': out['metrics']})['rows']}

    def test_a_shows_viewers_and_the_shows_together_come_from_the_shared_module(self):
        p = G.plan(HARBOR)
        self.assertEqual(p['window'], ['2025-11-17', '2026-11-16'])
        out, calls, _a = _run_core([_harbor_reply()], text=HARBOR)
        self.assertTrue(out.get('success') and not out.get('_held'))
        by = self._by(out, HARBOR)
        one = {t: self.pk.viewers(t, '2025-11-17', '2026-11-16')['viewers'] for t in ('Harbor Precinct', 'Night Docket', 'Cold Ledger')}
        factors = set()
        for t, v in one.items():
            got = by[t]['people']
            self.assertNotEqual(got, {'Harbor Precinct': 9_000_001, 'Night Docket': 7_000_003, 'Cold Ledger': 2_000_007}[t], 'the pass\'s own pool is not the figure')
            self.assertTrue(0.94 * v <= got <= 0.975 * v, (t, got, v))     # thirty seconds sits a few percent under one second
            factors.add(round(got / v, 4))
            self.assertEqual(got, G.file_pools(p)[t])
            self.assertIn(f"{t} / {got:,} /", out['reply'])
        self.assertEqual(len(factors), 3, 'no two shows share a factor')
        together = self.pk.combined(['Harbor Precinct', 'Night Docket', 'Cold Ledger'], '2025-11-17', '2026-11-16')['viewers']
        c = by['All three combined (each person once)']['people']
        self.assertTrue(0.94 * together <= c <= 0.975 * together, (c, together))
        self.assertGreaterEqual(c, max(by[t]['people'] for t in one))
        self.assertLess(c, sum(by[t]['people'] for t in one))
        self.assertAlmostEqual(by['All Peacock users']['people'] / 70_000_011.0, 1.0, places=2)     # not on the record: the row keeps its own figure
        for r in out['breakdown']['rows']:
            self.assertEqual(sum(b['count'] for b in r['buckets']), r['pool'])
        # nothing a reader sees says where a figure came from
        self.assertIsNone(BANNED.search(out['reply']))
        self.assertNotRegex(out['reply'], r"(?i)on file|on record|first[- ]party|account")
        self.assertIn('PEACOCK TITLE FIGURES', calls[0])       # the pass is handed the same figures (the calibration's own wire point)

    def test_one_second_is_the_figure_itself_and_another_service_is_untouched(self):
        q1 = HARBOR.replace(', viewers credited at 30 seconds', '')
        out, _c, _a = _run_core([_harbor_reply()], text=q1)
        by = self._by(out, q1)
        self.assertEqual(by['Night Docket']['people'], self.pk.viewers('Night Docket', '2025-11-17', '2026-11-16')['viewers'])
        qn = HARBOR.replace('Peacock', 'Netflix')
        self.assertEqual(G.file_pools(G.plan(qn)), {})
        out2, _c, _a = _run_core([json.loads(json.dumps(_harbor_reply()).replace('Peacock', 'Netflix'))], text=qn)
        by2 = self._by(out2, qn)
        self.assertAlmostEqual(by2['Harbor Precinct']['people'] / 9_000_001.0, 1.0, places=2)
        self.pk.set_cached(None)
        self.assertEqual(G.file_pools(G.plan(HARBOR)), {}, 'no record: every caller behaves as before')

    def test_the_comparison_row_is_the_services_own_audience(self):
        import pandas as pd
        cat = [{'s3_key': 'Peacock_06.csv', 'display_name': 'Peacock', 'category': 'STREAMING/PLATFORM', 'last_modified': '2026-06-16'},
               {'s3_key': 'PEACOCK - Avid Fan.csv', 'display_name': 'Peacock - Avid Fan', 'category': 'STREAMING/PLATFORM', 'last_modified': '2026-07-01'}]
        df = pd.DataFrame(
            [['AGE', '35-44', '22.0%'], ['AGE', '25-34', '20.0%'], ['AGE', '45-54', '16.0%'], ['AGE', '55-64', '14.0%'], ['AGE', '18-24', '10.0%'],
             ['AGE', '17 AND UNDER', '9.0%'], ['AGE', '65 OR OLDER', '9.0%'], ['GENDER', 'MALE', '49.6%'], ['GENDER', 'FEMALE', '49.4%'],
             ['GENDER', 'NON-BINARY', '1.0%'], ['QSR', 'Starbucks', '31.0%']],
            columns=['Column', 'Value', 'Brand Penetration (Row)'])
        from prometheus import grid_facts as GF
        p = G.plan(LIZ)
        with mock.patch.object(pma, 'load_genpop_map', lambda s3, b: {('STREAMING/PLATFORM', pma._norm_brand('Peacock')): 37.0123}), \
                mock.patch.object(pma, 'load_profile_df', lambda s3, b, k: (df, 'etag') if k == 'Peacock_06.csv' else (_ for _ in ()).throw(KeyError(k))):
            facts = GF.platform_facts(p, object(), 'bucket', cat, pma)
        self.assertEqual(facts['people'], 122_103_578)                       # the US baseline's own row for the service
        self.assertEqual(facts['pct_women'], 49.4)
        self.assertAlmostEqual(facts['ages'][0], 0.09)
        self.assertAlmostEqual(facts['ages'][3], 0.22)
        self.assertEqual(facts['profile'], 'Peacock')                        # the base profile, never a cut of it
        self.assertIsNone(GF.platform_facts(p, object(), 'bucket', [], mock.MagicMock(load_genpop_map=lambda *a: {})))
        # the row takes them; the series rows are untouched
        self.pk.set_cached(None)
        self.addCleanup(G.set_facts, p, None)
        with mock.patch.object(GF, 'platform_facts', lambda *a, **k: facts):
            out, _c, _a = _run_core([REPLIES['summary_in_notes']])
        by = self._by(out, LIZ)
        row = by[SERVICE]
        self.assertEqual(row['people'], 122_103_578)
        self.assertEqual(row['pct_women'], 49.4)
        self.assertEqual([b['pct'] for b in row['bands']], [9.0, 10.0, 20.0, 22.0, 16.0, 14.0, 9.0])
        self.assertEqual(row['median_age'], 40.0)
        self.assertIn('All Peacock users / 122,103,578 / 49.4% / 40.0', out['reply'])
        self.assertEqual(by['Chicago Med']['pct_women'], 64.2)
        self.assertLess(row['joint'][0]['pct'], row['pct_women'])
        # the read core hands the facts in from the files
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        self.assertIn('facts=lambda _gp: _grid_facts.platform_facts(_gp, _H.s3_client, _H.S3_BUCKET, _profile_catalog_for_chat(), pma)', src)


class AnEmptyReadIsRepairedNeverThrown(unittest.TestCase):
    def test_her_failure_reproduces_on_the_bare_coherence_pass(self):
        with self.assertRaises(ValueError) as cm:
            pma.enforce_metrics_coherence(_copy('nothing_measured'))
        self.assertIn('no usable metrics', str(cm.exception))

    def test_an_empty_reply_is_run_once_more_and_the_table_lands(self):
        out, calls, alerts = _run_core([REPLIES['nothing_measured'], REPLIES['summary_in_notes']])
        self.assertTrue(out.get('success'))
        self.assertFalse(out.get('_held'))
        self.assertEqual(len(calls), 2)
        self.assertEqual(alerts, [])
        self.assertIn('All nine combined (each person once) / ', out['reply'])
        self.assertNotIn(CALM, out['reply'])

    def test_nothing_at_all_is_held_never_an_error(self):
        out, calls, alerts = _run_core([REPLIES['nothing_measured']] * 5)
        self.assertEqual(len(calls), 3, 'the pass, one piece, one second pass: three at most')
        self.assertIn('YOUR LAST REPLY COULD NOT BE USED', calls[2])
        # held: a success payload with the calm line and the held mark, so the job reads 'held' and the sweep
        # gives it a fresh pass; never the failed payload that ends the job in an error
        self.assertTrue(out.get('success'))
        self.assertTrue(out.get('_held'))
        self.assertEqual(out.get('reply'), CALM)
        self.assertNotIn('error', out)
        self.assertEqual(len(alerts), 1)
        self.assertIn('held for a fresh pass', str(alerts[0][0][1]))
        self.assertEqual(alerts[0][0][0], 'brief-chat/analyze')

    def test_a_held_read_gets_a_fresh_pass_and_the_email(self):
        import time as _t
        status = {'job_id': 'j1', 'user': 'liz', 'status': 'held', 'finished_at': _t.time() - 400,
                  'resume': {'text': LIZ}}
        self.assertTrue(RR.is_retryable_held(status))
        self.assertFalse(RR.is_retryable_held(dict(status, status='error')), 'an errored job is never retried: that was her job')
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        job = src[src.index('def _pm_run_read_job('):src.index("@_H.app.route('/api/brief-chat/notify-when-done'")]
        self.assertIn("'status': 'held' if held else 'done'", job)
        self.assertIn('_rr.promised_email(job_id)', job)           # a retry that lands is emailed
        self.assertIn('_PT.keep(pm_user, text, job_id=job_id', job)  # and keeps any open promise on the question

    def test_the_core_wires_the_repair(self):
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        i = src.index('def _pm_generate_read_core(')
        body = src[i:src.index('\ndef _pm_name_two_audiences', i)]
        self.assertIn('extra_blocks.extend(_read_repair.ask_blocks(text))', body)
        self.assertIn('res, data = _read_repair.coherent(', body)
        self.assertIn('except _read_repair.EmptyRead as e:', body)
        self.assertLess(body.index('except _read_repair.EmptyRead as e:'), body.index("_H._chatbot_error_email('brief-chat/analyze', e)"))
        held = body[body.index('except _read_repair.EmptyRead as e:'):body.index("_H._chatbot_error_email('brief-chat/analyze', e)")]
        self.assertIn("'_held': True", held)
        self.assertNotIn('_chatbot_calm_payload()', held)
        self.assertLess(len(src.splitlines()), 19900)

    def test_figures_in_another_shape_are_moved_into_the_contract(self):
        data = {'action': 'answer', 'subject': 'Chicago Fire', 'metric_family': 'viewership', 'headline': 'x',
                'metrics': {'unique_viewers': '8,199,994 viewers', 'completion': '76.8%',
                            'note': 'this line cannot be measured'}}
        with self.assertRaises(ValueError):
            pma.enforce_metrics_coherence(data)
        calls = []
        res, used = RP.coherent(data, enforce=pma.enforce_metrics_coherence, again=lambda b: calls.append(b),
                                question='how many people watched Chicago Fire on Peacock')
        self.assertEqual(calls, [], 'a reply that can be read is never run again')
        got = {m['name']: m['value'] for m in res['metrics']}
        self.assertEqual(got['completion'], 76.8)
        self.assertEqual(got['unique_viewers'] // 100000, 81)
        self.assertNotIn('note', got)
        # a table of counts with no shares takes its shares from the counts
        t = {'subject': 'X', 'metrics': [], 'table': [{'name': 'A', 'count': 500001}, {'name': 'B', 'count': 300001},
                                                         {'name': 'C', 'count': 200001}]}
        res2, _ = RP.coherent(t, enforce=pma.enforce_metrics_coherence, question='split it')
        self.assertEqual([r['label'] for r in res2['breakdown']['rows']], ['A', 'B', 'C'])
        self.assertAlmostEqual(sum(r['share_pct'] for r in res2['breakdown']['rows']), 100.0, places=2)
        # nothing to move and no second pass: the one signal the core holds on
        with self.assertRaises(RP.EmptyRead):
            RP.coherent({'subject': 'X', 'metrics': [{'name': 'a', 'value': None}]}, enforce=pma.enforce_metrics_coherence,
                        question='how many')
        # any other failure is still the caller's
        with self.assertRaises(KeyError):
            RP.coherent({'subject': 'X'}, enforce=lambda d: {}['boom'], question='how many')


class APromiseEndsDelivered(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.lister = lambda prefix: [k for k in self.store if k.startswith(prefix)]
        self.getter = lambda key: json.loads(json.dumps(self.store[key]))
        self.writer = lambda key, doc: self.store.__setitem__(key, json.loads(json.dumps(doc)))
        self.kw = {'lister': self.lister, 'getter': self.getter, 'writer': self.writer}

    def test_her_morning_end_to_end(self):
        # 16:40 the job died: the calm line lands on its progress turn with the held record behind it
        landed = []
        key = PT.job_failed('read', 'liz', '9b9bf258a990', LIZ, land=lambda p: landed.append(p) or 'filled', writer=self.writer)
        self.assertTrue(key)
        self.assertEqual(self.store[key]['status'], 'open')
        self.assertEqual(landed[0]['reply'], CALM)
        self.assertEqual([k for k, _d in PT.open_for('liz', LIZ, **{k: self.kw[k] for k in ('lister', 'getter')})], [key])
        # another reader, another question: not hers
        self.assertEqual(PT.open_for('armine', LIZ, lister=self.lister, getter=self.getter), [])
        self.assertEqual(PT.open_for('liz', LIZ.replace('Nine Dick Wolf', 'Eight Dick Wolf'), lister=self.lister, getter=self.getter), [])
        # a read of the same question lands: the answer is emailed once and the record closes as delivered
        sent = []
        closed = PT.keep('liz', LIZ, job_id='f252dcc9c9aa', emailed=False, send=lambda: sent.append(1) or True,
                         thread_id='api-8b09f13b-wolf-peacock-demos-a', **self.kw)
        self.assertEqual(closed, [key])
        self.assertEqual(sent, [1])
        doc = self.store[key]
        self.assertEqual(doc['status'], 'delivered')
        self.assertTrue(doc['delivered']['emailed'])
        self.assertEqual(doc['delivered']['job_id'], 'f252dcc9c9aa')
        self.assertTrue(doc['delivered_at'])
        # kept once: a second landing closes nothing and mails nothing
        self.assertEqual(PT.keep('liz', LIZ, job_id='x', send=lambda: sent.append(2) or True, **self.kw), [])
        self.assertEqual(sent, [1])
        self.assertEqual(PT.open_for('liz', LIZ, lister=self.lister, getter=self.getter), [])

    def test_an_answer_already_emailed_is_not_mailed_twice(self):
        key = PT.hold('liz', LIZ, kind='read_failed', job_id='j', writer=self.writer)
        sent = []
        self.assertEqual(PT.keep('liz', LIZ, job_id='k', emailed=True, send=lambda: sent.append(1), **self.kw), [key])
        self.assertEqual(sent, [])
        self.assertTrue(self.store[key]['delivered']['emailed'])

    def test_a_person_can_close_it_and_a_closed_record_stays_closed(self):
        key = PT.hold('liz', LIZ, kind='read_failed', job_id='j', writer=self.writer)
        doc = PT.close(key, how='answered by email', by='prometheus', emailed=True, thread_id='t', getter=self.getter, writer=self.writer)
        self.assertEqual(doc['status'], 'delivered')
        self.assertEqual(doc['delivered']['by'], 'prometheus')
        self.assertIsNone(PT.close(key, how='again', getter=self.getter, writer=self.writer))
        self.assertEqual(self.store[key]['delivered']['how'], 'answered by email')

    def test_a_probe_or_a_test_process_keeps_nothing_on_its_own(self):
        # no store handed in, inside a test process: nothing is listed, written or mailed
        sent = []
        self.assertEqual(PT.keep('liz', LIZ, job_id='k', send=lambda: sent.append(1)), [])
        self.assertEqual(sent, [])

    def test_the_watch_reads_open_records_only(self):
        src = open(os.path.join(ROOT, 'migration', 'ops_status.py'), encoding='utf-8').read()
        self.assertIn("doc.get('status', 'open') == 'open'", src)
        dig = open(os.path.join(ROOT, 'prometheus', 'daily_digest.py'), encoding='utf-8').read()
        self.assertIn("str(h.get('status') or 'open') == 'open'", dig)


class TheCreditAndTheOneLine(unittest.TestCase):
    RES = {'subject': 'Chicago Fire', 'metric_family': 'viewership', 'window_label': 'Nov 17 2025 to Nov 16 2026',
           'metrics': [{'name': 'v', 'label': 'Unique US viewers', 'value': 8199994, 'unit': 'viewers'}]}
    REPLY = 'On Chicago Fire:\n\n8,199,994 US viewers watched Chicago Fire on Peacock in the window.'

    def test_the_credit_the_question_sets(self):
        self.assertEqual(M.stated_credit(LIZ), 30)
        self.assertEqual(M.stated_credit('viewers credited at 2 minutes'), 120)
        self.assertEqual(M.stated_credit('people with at least 30 seconds of viewing'), 30)
        self.assertEqual(M.stated_credit('how many watched Chicago Fire'), 0)
        self.assertEqual(M.stated_credit('at two thresholds: viewers at 1 second, and viewers at 30 seconds'), 0)   # both are reported
        self.assertEqual(M.stated_credit('aged at least 30'), 0)
        self.assertIn('30 seconds or more of play', M.credit_rule(LIZ))
        self.assertEqual(M.credit_rule('how many watched Chicago Fire'), '')

    def test_one_methodology_line_when_one_is_asked_for(self):
        q = 'How many people watched Chicago Fire on Peacock, viewers credited at 30 seconds? Close with one methodology line.'
        out = M.ensure(self.REPLY, dict(self.RES), question=q, base_label='Chicago Fire')
        tail = out[len(self.REPLY):].strip()
        self.assertEqual(tail.count('\n'), 0, 'one line')
        self.assertTrue(tail.startswith('How these are counted: US viewers (persons), credited at 30 seconds or more of play on Peacock '
                                        '(our standing view credits a person at one second), each person counted once inside the window, '
                                        'Nov 17 2025 to Nov 16 2026, measured across 10M US consumers and projected to the US'))
        self.assertNotIn('Nielsen', out)
        self.assertNotIn('What we count as a view', out)
        self.assertEqual(M.ensure(out, dict(self.RES), question=q, base_label='Chicago Fire'), out, 'idempotent')
        # the mail door and a file leave the one line as the close: no view paragraph is added under it
        self.assertTrue(M.carries_definition(out))
        self.assertEqual(M.attach_view_definition(out), out)
        self.assertFalse(M.carries_one_line(self.REPLY))

    def test_the_block_says_the_credit_and_is_otherwise_as_before(self):
        q = 'How many people watched Chicago Fire on Peacock, viewers credited at 30 seconds?'
        out = M.ensure(self.REPLY, dict(self.RES), question=q, base_label='Chicago Fire')
        self.assertIn('What we count as a view', out)
        self.assertIn('- Credit for this read: a person counts with 30 seconds or more of play, the credit the question set; '
                      'our standing view credits a person at one second.', out)
        plain = M.ensure(self.REPLY, dict(self.RES), question='How many people watched Chicago Fire on Peacock?', base_label='Chicago Fire')
        self.assertIn('What we count as a view', plain)
        self.assertIn('How Peacock counts', plain)
        self.assertNotIn('Credit for this read', plain)


class TheUnderEighteenBand(unittest.TestCase):
    def test_viewers_keep_the_band(self):
        p = G.plan(LIZ)
        self.assertEqual(p['bands'][0]['label'], '17 and under')
        self.assertEqual(p['not_measured'], [])

    def test_account_holders_are_adults_and_the_line_is_said_plainly(self):
        q = LIZ.replace('everyone who used Peacock in the window', 'all Peacock subscribers who used Peacock in the window')
        p = G.plan(q)
        self.assertTrue(p['adults_only'])
        self.assertEqual([b['label'] for b in p['bands']], BANDS[1:])
        self.assertEqual([n['line'] for n in p['not_measured']], ['17 and under'])
        d = _copy('summary_in_notes')
        data = G.complete(G.shape(d, p), p)
        res = pma.enforce_metrics_coherence(data, question=q)
        reply = pma.format_generated_metrics_reply(res)
        self.assertIn('17 and under: not measured. This audience is account holders, who are adults, so the age bands start at 18.', reply)
        t2 = _table(reply, 'Age bands, percent of each group and people')
        self.assertEqual(list(t2), BANDS[1:])
        pcts = [_pct_and_count(t2[b][0])[0] for b in BANDS[1:]]
        self.assertEqual(round(sum(pcts), 1), 100.0)


if __name__ == '__main__':
    unittest.main(verbosity=1)
