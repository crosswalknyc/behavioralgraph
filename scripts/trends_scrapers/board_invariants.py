"""Board invariants: the rules a platform's own people would check.

Jenna, 2026-09-28: "did you set up something so these errors cannot
continue to happen." Until this file, no. Six passes run in sequence
each night (per-item research, set-level chart sizing, coverage,
ceilings, derived rails, provenance) and any later pass can undo what
an earlier one guaranteed. Every guard we had watched one pass. Nothing
watched the page.

This module watches the page. It evaluates the RENDERED board, the
same payload the dashboard serves, against the invariants the rankers
are sold on, and reports every violation with the row, the value and
the number it should be under. It is the last step of every pricing
pass (nightly and residential): audit, hand violations to the in-place
fixers, re-audit, and alert only on a survivor. It is also the morning
audit, so what an operator reads and what the pipeline enforces are
the same list.

The invariants
--------------
I1  CHART ORDER      On a published chart the platform's order is the
                     order. Sorting our values must reproduce it: every
                     adjacent pair on a chart descends.
I2  CHART PRESENT    A service declared as publishing a chart renders
                     one today (its own capture or a marked carry from
                     the most recent day that had it). A rail with no
                     chart marks on a chart service is a failure to
                     capture, not a quiet day.
I3  CATALOG UNDER    A title not on the platform's chart cannot read
                     higher than the chart's last slot for its kind
                     (series under series #N, film under film #N). If
                     it could, the platform would have charted it.
I4  NO CAP SEATS     No two rows on the board within 0.5% of each other
                     AND within 5% of a service's daily cap. A cluster
                     just under a ceiling is a clamp, not a reading.
I5  RAIL ORDER       Where no chart owns the order, a rail descends by
                     value. A row out of place is a value changed after
                     the sort.
I6  NO BLANKS        Every non-Film row carries a value (the terminal
                     bracket's contract, re-checked on the page).

Usage
-----
    python3 -m scripts.trends_scrapers.board_invariants            # live
    python3 -m scripts.trends_scrapers.board_invariants --view /tmp/view.json
    python3 -m scripts.trends_scrapers.board_invariants --json

`audit(payload)` returns a dict the callers use; `main` prints a
report. Auditing never raises and never writes.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from typing import Any, Optional

logger = logging.getLogger(__name__)

CAP_SEAT_BAND = 0.05        # within 5% of the service's daily cap
CAP_SEAT_TIGHT = 0.005      # and within 0.5% of each other

_SECTIONS = (('streaming_trending', 'streaming'), ('fast_trending', 'fast'))


def _row_value(it: dict) -> int:
    for f in ('us_streams', 'us_readers'):
        blk = it.get(f)
        if isinstance(blk, dict):
            try:
                v = int(float(blk.get('us_estimate') or 0))
            except (TypeError, ValueError):
                v = 0
            if v > 0:
                return v
    return 0


def _kind(it: dict) -> str:
    cd = (it.get('category_display') or '').strip().lower()
    if cd == 'film':
        return 'film'
    if cd:
        return 'tv'
    return ''


def _chart_group(it: dict) -> tuple[str, int]:
    pr = it.get('published_rank')
    if isinstance(pr, (int, float)) and pr > 0:
        grp = it.get('published_chart') or 'chart'
        sub = it.get('published_group') or ''
        cat = it.get('category_display') or ''
        return f'{grp}|{sub}|{cat}', int(pr)
    return '', 0


def _declared_chart_slugs() -> set:
    try:
        from scripts.trends_scrapers import stream_estimates as se
        return {s for s, _l in se._charted_slugs()}
    except Exception:
        return set()


def _daily_caps() -> dict:
    caps: dict = {}
    try:
        from scripts.trends_scrapers import stream_estimates as se
        for table in (se._STREAMING_PLATFORMS_META, se._FAST_PLATFORMS_META):
            for p in table:
                weekly = int(p.get('ceiling') or 0)
                if weekly > 0:
                    caps[p['key']] = max(1, weekly // 7)
    except Exception:
        pass
    return caps


def audit(payload: dict) -> dict[str, Any]:
    """Evaluate every invariant on a compute_view payload."""
    cards = (payload or {}).get('cards') or {}
    declared = _declared_chart_slugs()
    caps = _daily_caps()
    out: dict[str, Any] = {
        'I1_chart_order': [], 'I2_chart_present': [], 'I3_catalog_under': [],
        'I4_cap_seats': [], 'I5_rail_order': [], 'I6_blanks': [],
        'rails': {},
    }

    for section, _label in _SECTIONS:
        panels = cards.get(section) or {}
        if not isinstance(panels, dict):
            continue
        for slug, panel in panels.items():
            if not isinstance(panel, dict):
                continue
            rows = panel.get('items') if isinstance(panel.get('items'), list) else []
            if not rows and isinstance(panel.get('channels'), list):
                rows = panel['channels']
            if not rows:
                continue
            rail = f'{section}.{slug}'
            summary = {'rows': len(rows), 'blank': 0, 'charts': {},
                       'catalog_above': 0, 'rail_inversions': 0}

            # Chart groups in platform order, and catalog per kind.
            charts: dict[str, list] = defaultdict(list)
            catalog: dict[str, list] = defaultdict(list)
            for it in rows:
                v = _row_value(it)
                if not v:
                    summary['blank'] += 1
                    out['I6_blanks'].append({'rail': rail, 'title': it.get('title')})
                g, pos = _chart_group(it)
                if g:
                    charts[g].append((pos, v, it.get('title')))
                elif it.get('collection') and it.get('bucket_rank'):
                    pass   # a browse shelf is merchandising order, not a rank
                else:
                    catalog[_kind(it)].append((v, it.get('title')))

            # I1 chart order.
            floors: dict[str, int] = {}
            for g, seq in charts.items():
                seq.sort()
                vals = [v for _p, v, _t in seq]
                inv = [(seq[i][2], vals[i], seq[i + 1][2], vals[i + 1])
                       for i in range(len(vals) - 1)
                       if vals[i] and vals[i + 1] and vals[i + 1] > vals[i]]
                summary['charts'][g] = {'n': len(vals), 'inversions': len(inv),
                                        'top': vals[0] if vals else 0,
                                        'last': min(v for v in vals if v) if any(vals) else 0}
                for a_t, a_v, b_t, b_v in inv:
                    out['I1_chart_order'].append(
                        {'rail': rail, 'chart': g, 'above': a_t, 'above_v': a_v,
                         'below': b_t, 'below_v': b_v})
                kind = g.rsplit('|', 1)[-1].strip().lower()
                kind = 'film' if kind == 'film' else ('tv' if kind else '')
                sub = g.split('|')[1].lower() if '|' in g else ''
                if not kind and sub:
                    kind = 'film' if ('film' in sub or 'movie' in sub) else 'tv'
                last = summary['charts'][g]['last']
                if last:
                    floors[kind] = max(floors.get(kind, 0), last) if kind in floors else last

            # I2 chart present.
            if slug in declared and not charts:
                out['I2_chart_present'].append({'rail': rail})

            # I3 catalog under the chart floor, per kind. A catalog row
            # with no kind is judged against the lower floor.
            for kind, rows_k in catalog.items():
                floor = floors.get(kind)
                if floor is None and floors:
                    floor = min(floors.values())
                if not floor:
                    continue
                for v, t in rows_k:
                    if v > floor:
                        summary['catalog_above'] += 1
                        out['I3_catalog_under'].append(
                            {'rail': rail, 'kind': kind or '?', 'title': t,
                             'value': v, 'floor': floor})

            # I5 rail order where no chart owns it: the rendered list
            # after the chart block must descend.
            tail = [(_row_value(it), it.get('title')) for it in rows
                    if not _chart_group(it)[0]]
            for i in range(len(tail) - 1):
                a, b = tail[i][0], tail[i + 1][0]
                if a and b and b > a:
                    summary['rail_inversions'] += 1
                    out['I5_rail_order'].append(
                        {'rail': rail, 'above': tail[i][1], 'above_v': a,
                         'below': tail[i + 1][1], 'below_v': b})

            # I4 cap seats: rows within 5% of the service's cap that
            # sit within 0.5% of each other.
            cap = caps.get(slug)
            if cap:
                near = sorted(v for v in (_row_value(it) for it in rows)
                              if v and v >= cap * (1 - CAP_SEAT_BAND))
                for i in range(len(near) - 1):
                    if near[i + 1] - near[i] <= near[i + 1] * CAP_SEAT_TIGHT:
                        out['I4_cap_seats'].append(
                            {'rail': rail, 'a': near[i], 'b': near[i + 1], 'cap': cap})

            out['rails'][rail] = summary

    # I4 across rails: the same seat on two services that share a cap.
    by_cap: dict[int, list] = defaultdict(list)
    for section, _l in _SECTIONS:
        for slug, panel in (cards.get(section) or {}).items():
            cap = caps.get(slug)
            rows = (panel or {}).get('items') if isinstance(panel, dict) else None
            if not cap or not rows:
                continue
            top = max((_row_value(it) for it in rows), default=0)
            if top >= cap * (1 - CAP_SEAT_BAND):
                by_cap[cap].append((slug, top))
    for cap, tops in by_cap.items():
        tops.sort(key=lambda x: x[1])
        for i in range(len(tops) - 1):
            if tops[i + 1][1] - tops[i][1] <= tops[i + 1][1] * CAP_SEAT_TIGHT:
                out['I4_cap_seats'].append(
                    {'rail': f'{tops[i][0]} + {tops[i + 1][0]}', 'a': tops[i][1],
                     'b': tops[i + 1][1], 'cap': cap, 'cross_service': True})

    out['violations'] = sum(len(out[k]) for k in
                            ('I1_chart_order', 'I2_chart_present', 'I3_catalog_under',
                             'I4_cap_seats', 'I5_rail_order', 'I6_blanks'))
    return out


def format_report(res: dict) -> str:
    lines = [f"board invariants: {res['violations']} violation(s)"]
    for key, label in (('I1_chart_order', 'I1 chart order'),
                       ('I2_chart_present', 'I2 chart present'),
                       ('I3_catalog_under', 'I3 catalog under chart'),
                       ('I4_cap_seats', 'I4 cap seats'),
                       ('I5_rail_order', 'I5 rail order'),
                       ('I6_blanks', 'I6 blanks')):
        items = res.get(key) or []
        lines.append(f"  {label}: {len(items)}")
        for v in items[:6]:
            lines.append('     ' + json.dumps(v, ensure_ascii=False)[:180])
        if len(items) > 6:
            lines.append(f"     ... and {len(items) - 6} more")
    lines.append('  rails:')
    for rail, s in sorted(res.get('rails', {}).items()):
        ch = ' '.join(f"{c['n'] - c['inversions'] - 1}/{c['n'] - 1}"
                      for c in s['charts'].values()) or '-'
        flag = ' <-' if (s['blank'] or s['catalog_above'] or s['rail_inversions']
                         or any(c['inversions'] for c in s['charts'].values())) else ''
        lines.append(f"     {rail:40s} rows {s['rows']:>4} blank {s['blank']:>2} "
                     f"chart {ch:14s} catalog>floor {s['catalog_above']:>3} "
                     f"tail inversions {s['rail_inversions']:>3}{flag}")
    return '\n'.join(lines)


def _fresh_view() -> Optional[dict]:
    try:
        import trends_iq  # noqa: E402
        try:
            trends_iq.invalidate_live_compute_view_caches()
        except Exception:
            pass
        return trends_iq.compute_view(
            {'geo_type': 'National', 'geo_value': '', 'lookback_days': 1},
            force_refresh=True)
    except Exception:
        logger.exception('board invariants: could not compute the view')
        return None


def _run_fixer() -> int:
    """The in-place fixer for chart and catalog levels: the same set-
    level pass the lane runs, over every declared chart. Own
    subprocess so a crash cannot take the caller down."""
    import os
    import subprocess
    repo = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    try:
        proc = subprocess.run(
            [sys.executable, '-m',
             'scripts.trends_scrapers.residential_chart_pricing', '--all'],
            cwd=repo, capture_output=True, text=True, timeout=60 * 60)
        if proc.stdout:
            logger.info('[board fixer] %s', proc.stdout.strip()[-2000:])
        return proc.returncode
    except Exception:
        logger.exception('board invariants: fixer failed to run')
        return 1


def gate(payload: Optional[dict] = None, *, fix: bool = True,
         alert: bool = True) -> dict[str, Any]:
    """Audit the rendered board; if anything fails, run the in-place
    fixer once, recompute, re-audit; alert only on what survives.

    This is the last step of every pricing pass. It never raises and
    never blocks a run: the board is already published by the time it
    runs, and its job is to make the next read of that board correct.
    """
    payload = payload or _fresh_view()
    if payload is None:
        return {'violations': -1, 'error': 'no view'}
    first = audit(payload)
    logger.info('board invariants (before): %d violation(s)', first['violations'])
    if first['violations'] == 0:
        return first
    logger.info('\n' + format_report(first))
    result = first
    if fix:
        fixable = (len(first['I1_chart_order']) + len(first['I3_catalog_under'])
                   + len(first['I4_cap_seats']) + len(first['I5_rail_order']))
        if fixable:
            rc = _run_fixer()
            logger.info('board invariants: fixer exited %d; re-auditing', rc)
            again = _fresh_view()
            if again is not None:
                result = audit(again)
                result['fixed'] = first['violations'] - result['violations']
                logger.info('board invariants (after): %d violation(s), %d fixed',
                            result['violations'], result['fixed'])
    if result['violations'] and alert:
        try:
            from scripts.trends_scrapers.run_guard import send_alert
            body = ('The rendered Trends IQ board failed its invariants after '
                    'the pricing pass and one in-place fix attempt. These are '
                    'the rules a platform\'s own people would check: chart '
                    'order, chart present, catalog under the chart, no cap '
                    'seats, rail order, no blanks. A survivor here is a defect '
                    'in a pass, not a wait for the next run.\n\n'
                    + format_report(result) + '\n')
            send_alert('board_invariants',
                       'Trends IQ: board invariants failed after the pricing pass',
                       body)
        except Exception:
            logger.exception('board invariants: alert failed (non-fatal)')
    return result


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description='Trends IQ board invariants')
    ap.add_argument('--view', help='a saved compute_view payload (JSON)')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--gate', action='store_true',
                    help='audit, fix in place once, re-audit, alert on survivors')
    args = ap.parse_args(argv)
    if args.gate:
        res = gate()
        print(format_report(res) if 'rails' in res else res)
        return 0 if res.get('violations') == 0 else 1
    if args.view:
        payload = json.load(open(args.view))
    else:
        import trends_iq  # noqa: E402
        payload = trends_iq.compute_view(
            {'geo_type': 'National', 'geo_value': '', 'lookback_days': 1},
            force_refresh=True)
    res = audit(payload)
    if args.json:
        print(json.dumps({k: v for k, v in res.items() if k != 'rails'},
                         ensure_ascii=False, indent=1))
    else:
        print(format_report(res))
    return 0 if res['violations'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
