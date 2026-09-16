"""Side-by-side comparison of the two IQ Rankers scorings.

Reads the clickstream-era daily metrics that are already stored in
`reference.v_iq_daily_metrics` and the scraped-signal metrics in
`reference.profile_iq_daily_signal_metrics`, and reports whether the new
board is recognisably the same product.

No clickstream query is issued. The old numbers come from the rankers'
own metrics table, which is where the nightly pass wrote them at the
time; nothing here re-derives them from events.

Output is markdown on stdout:

  * top 25 under each scoring over the window
  * per-category Spearman rank correlation on the shared population
  * the entities that move most in either direction, with the signal
    behind the move
  * match rate and no-signal population per category

Usage:

    python3 -m scripts.trends_scrapers.compare_ranker_scorings \
        --start 2026-09-08 --end 2026-09-14
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_BGWEBAPP = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _BGWEBAPP not in sys.path:
    sys.path.insert(0, _BGWEBAPP)

CH_HOST = os.environ.get('CLICKHOUSE_HOST', '168.119.215.48')
CH_PORT = os.environ.get('CLICKHOUSE_PORT', '8123')
CH_USER = os.environ.get('CLICKHOUSE_USER', 'bgapp')
CH_PASS = os.environ.get('CLICKHOUSE_PASSWORD', '')
# Read through the view, never the table: a ReplacingMergeTree shows
# both rows for a re-run day until its parts merge, which doubles every
# sum. The view picks the latest write per (day, entity).
NEW_VIEW = 'reference.v_iq_daily_signal_metrics'
OLD_VIEW = 'reference.v_iq_daily_metrics'


def ch(sql: str) -> list[list[str]]:
    qs = urllib.parse.urlencode({'user': CH_USER, 'password': CH_PASS})
    req = urllib.request.Request(f'http://{CH_HOST}:{CH_PORT}/?{qs}',
                                 data=sql.encode('utf-8'), method='POST')
    with urllib.request.urlopen(req, timeout=300) as resp:
        text = resp.read().decode('utf-8', errors='ignore')
    return [ln.split('\t') for ln in text.splitlines() if ln]


# The leaderboard's own exclusions, so the comparison population is the
# population a user actually sees.
_EXCLUDE = (
    "positionCaseInsensitive(profile_subject, 'avid fan') = 0 "
    "AND positionCaseInsensitive(project_name, 'avid fan') = 0 "
    "AND positionCaseInsensitive(profile_subject, '.bak') = 0 "
    "AND positionCaseInsensitive(profile_subject, 'prepatch') = 0"
)


def old_window(start: str, end: str) -> dict[str, dict]:
    """Activity-weighted score per entity, the way the leaderboard rolls
    a window up today."""
    rows = ch(f"""
        SELECT profile_subject, anyHeavy(project_name), anyHeavy(category),
               if(sum(mentions) > 0,
                  round(sum(cw_iq_score * mentions) / sum(mentions), 2),
                  round(avg(cw_iq_score), 2)),
               sum(mentions), sum(unique_uids)
        FROM {OLD_VIEW}
        WHERE snapshot_date BETWEEN toDate('{start}') AND toDate('{end}')
          AND {_EXCLUDE}
        GROUP BY profile_subject FORMAT TSV""")
    return {r[0]: {'name': r[1], 'category': r[2], 'score': float(r[3] or 0),
                   'volume': float(r[4] or 0), 'reach': float(r[5] or 0)}
            for r in rows if len(r) >= 6}


def new_window(start: str, end: str) -> dict[str, dict]:
    """Window score as the plain average of the daily scores.

    The leaderboard weights a window by that day's activity, because on
    the clickstream path a thin-panel day was a less reliable
    measurement and deserved less weight. On this path a quiet day is
    not an unreliable measurement, it IS the measurement, and weighting
    by activity hands the whole window to a single spike day: an entity
    seen once at 100 and absent the other six days would score 100 for
    the week. Averaging the days treats the quiet ones as the real
    observations they are.
    """
    rows = ch(f"""
        SELECT profile_subject, anyHeavy(project_name), anyHeavy(category),
               round(avg(cw_iq_score), 2),
               sum(signal_volume), max(signal_reach),
               max(has_signal), sum(matched_items),
               arrayStringConcat(arrayDistinct(arrayFlatten(groupArray(surfaces))), ',')
        FROM {NEW_VIEW}
        WHERE snapshot_date BETWEEN toDate('{start}') AND toDate('{end}')
          AND {_EXCLUDE}
        GROUP BY profile_subject FORMAT TSV""")
    out = {}
    for r in rows:
        if len(r) < 9:
            continue
        score = None if r[3] in ('', '\\N', 'nan') else float(r[3])
        out[r[0]] = {'name': r[1], 'category': r[2], 'score': score,
                     'volume': float(r[4] or 0), 'reach': float(r[5] or 0),
                     'has_signal': int(r[6] or 0),
                     'matched_items': int(float(r[7] or 0)),
                     'surfaces': r[8]}
    return out


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    """Rank correlation. Ties averaged."""
    n = len(pairs)
    if n < 3:
        return None

    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk

    a = ranks([p[0] for p in pairs])
    b = ranks([p[1] for p in pairs])
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    if da == 0 or db == 0:
        return None
    return num / (da * db)


def table(rows: list[list[str]], head: list[str]) -> str:
    out = ['| ' + ' | '.join(head) + ' |',
           '|' + '|'.join(['---'] * len(head)) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(str(x) for x in r) + ' |')
    return '\n'.join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--top', type=int, default=25)
    args = ap.parse_args()

    if not CH_PASS:
        print('CLICKHOUSE_PASSWORD not set', file=sys.stderr)
        return 2

    old = old_window(args.start, args.end)
    new = new_window(args.start, args.end)
    print(f'# IQ Rankers scoring comparison, {args.start} to {args.end}\n')
    print(f'Clickstream-era rows: {len(old)}. Scraped-signal rows: {len(new)}.\n')

    scored = {k: v for k, v in new.items() if v['score'] is not None}
    print(f'Scored on the new path: {len(scored)}. '
          f'No signal: {len(new) - len(scored)}.\n')

    # ---- top N under each
    print(f'\n## Top {args.top}, clickstream scoring\n')
    top_old = sorted(old.items(), key=lambda kv: -kv[1]['score'])[:args.top]
    print(table([[i + 1, v['name'], v['category'], v['score'],
                  int(v['volume'])] for i, (k, v) in enumerate(top_old)],
                ['#', 'Entity', 'Category', 'Score', 'Matched events']))

    print(f'\n## Top {args.top}, scraped-signal scoring\n')
    top_new = sorted(scored.items(), key=lambda kv: -kv[1]['score'])[:args.top]
    print(table([[i + 1, v['name'], v['category'], v['score'],
                  f"{int(v['reach']):,}", v['surfaces']]
                 for i, (k, v) in enumerate(top_new)],
                ['#', 'Entity', 'Category', 'Score', 'Daily US audience',
                 'Surfaces']))

    old_top_names = {k for k, _ in top_old}
    new_top_names = {k for k, _ in top_new}
    print(f'\nOverlap in the two top {args.top} lists: '
          f'{len(old_top_names & new_top_names)}.\n')

    # ---- rank correlation per category on the shared scored population
    shared = [k for k in scored if k in old]
    bycat = defaultdict(list)
    for k in shared:
        bycat[new[k]['category']].append((old[k]['score'], scored[k]['score']))
    print('\n## Rank correlation on the shared scored population\n')
    rows = []
    for cat, pairs in sorted(bycat.items(), key=lambda kv: -len(kv[1])):
        rho = spearman(pairs)
        rows.append([cat, len(pairs),
                     'n/a' if rho is None else f'{rho:+.3f}'])
    allrho = spearman([p for ps in bycat.values() for p in ps])
    rows.append(['ALL', sum(len(p) for p in bycat.values()),
                 'n/a' if allrho is None else f'{allrho:+.3f}'])
    print(table(rows, ['Category', 'Entities', 'Spearman rho']))

    # The composite is a deviation score and churns by design, so it is
    # the wrong series to judge agreement on. The measures underneath it
    # are the ones that should line up if both paths are looking at the
    # same world.
    print('\n### The same comparison on the measures underneath the score\n')
    urows = []
    for label, fa, fb in (('Matched events against activity', 'volume', 'volume'),
                          ('Unique people against audience', 'reach', 'reach')):
        pairs = [(old[k][fa], scored[k][fb]) for k in shared]
        r = spearman(pairs)
        urows.append([label, len(pairs), 'n/a' if r is None else f'{r:+.3f}'])
    print(table(urows, ['Pair', 'Entities', 'Spearman rho']))

    # ---- self-consistency
    #
    # The CW IQ Score is a deviation score: it answers "who is unusually
    # active for them right now", so it is supposed to churn. Before
    # reading anything into a cross-path correlation, measure how well
    # each path reproduces ITSELF from one window to the next. A path
    # whose own week-to-week correlation is 0.3 cannot possibly show a
    # high correlation against a different path, and the honest
    # comparison is whether the two churn to the same degree.
    from datetime import date as _date, timedelta as _td
    span = (_date.fromisoformat(args.end) - _date.fromisoformat(args.start)).days + 1
    p_end = (_date.fromisoformat(args.start) - _td(days=1)).isoformat()
    p_start = (_date.fromisoformat(p_end) - _td(days=span - 1)).isoformat()
    old_p, new_p = old_window(p_start, p_end), new_window(p_start, p_end)
    new_p_scored = {k: v for k, v in new_p.items() if v['score'] is not None}

    def _self(a, b, field):
        ks = [k for k in a if k in b]
        return spearman([(a[k][field], b[k][field]) for k in ks]), len(ks)

    print(f'\n## Self-consistency, {p_start} to {p_end} against '
          f'{args.start} to {args.end}\n')
    rows = []
    for label, a, b, field in (
            ('Clickstream score', old_p, old, 'score'),
            ('Scraped-signal score', new_p_scored, scored, 'score'),
            ('Clickstream activity', old_p, old, 'volume'),
            ('Scraped-signal activity', new_p, new, 'volume'),
            ('Clickstream reach', old_p, old, 'reach'),
            ('Scraped-signal reach', new_p, new, 'reach')):
        rho, n = _self(a, b, field)
        rows.append([label, n, 'n/a' if rho is None else f'{rho:+.3f}'])
    print(table(rows, ['Series', 'Entities', 'Spearman rho vs prior window']))

    # ---- biggest movers
    moves = []
    for k in shared:
        moves.append((scored[k]['score'] - old[k]['score'], k))
    moves.sort()
    print('\n## Biggest movers down (old score to new score)\n')
    print(table([[new[k]['name'], new[k]['category'], old[k]['score'],
                  scored[k]['score'], f'{d:+.1f}', int(old[k]['volume']),
                  new[k]['matched_items'], new[k]['surfaces'] or 'none']
                 for d, k in moves[:15]],
                ['Entity', 'Category', 'Old', 'New', 'Move',
                 'Old matched events', 'New board items', 'Surfaces']))
    print('\n## Biggest movers up\n')
    print(table([[new[k]['name'], new[k]['category'], old[k]['score'],
                  scored[k]['score'], f'{d:+.1f}', int(old[k]['volume']),
                  new[k]['matched_items'], new[k]['surfaces'] or 'none']
                 for d, k in reversed(moves[-15:])],
                ['Entity', 'Category', 'Old', 'New', 'Move',
                 'Old matched events', 'New board items', 'Surfaces']))

    # ---- coverage per category
    print('\n## Coverage per category\n')
    cov = defaultdict(lambda: [0, 0, 0])
    for k, v in new.items():
        cov[v['category']][0] += 1
        if v['has_signal']:
            cov[v['category']][1] += 1
        if v['matched_items'] > 0:
            cov[v['category']][2] += 1
    rows = []
    tot = [0, 0, 0]
    for cat, (n, sigd, matched) in sorted(cov.items(), key=lambda kv: -kv[1][0]):
        rows.append([cat, n, matched, f'{100.0 * matched / n:.1f}%',
                     n - sigd, f'{100.0 * (n - sigd) / n:.1f}%'])
        tot[0] += n
        tot[1] += sigd
        tot[2] += matched
    rows.append(['ALL', tot[0], tot[2], f'{100.0 * tot[2] / max(tot[0],1):.1f}%',
                 tot[0] - tot[1],
                 f'{100.0 * (tot[0] - tot[1]) / max(tot[0],1):.1f}%'])
    print(table(rows, ['Category', 'Entities', 'Matched in window',
                       'Match rate', 'No signal', 'No-signal share']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
