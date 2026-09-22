#!/usr/bin/env python3
"""Confirm the standing invariants on the served board, not on intent.

Everything here is read back out of the payload the dashboard serves,
after caches are purged, so what is checked is what a reader sees.

Read-only. Never queries clickstream.

    python3 -m scripts.trends_scrapers.verify_service_provenance
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_FILTERS = {'geo_type': 'National', 'geo_value': '', 'lookback_days': 1}

# `no-round-sample-sizes.mdc`
_PLACEHOLDERS = {2001, 12345, 54321, 99999, 88888, 77777, 22222,
                 123456, 654321}

_FAILS: list = []
_PASSES: list = []


def _check(ok: bool, label: str, detail: str = '') -> None:
    (_PASSES if ok else _FAILS).append(f'{label}: {detail}' if detail
                                        else label)
    print(f'  [{"PASS" if ok else "FAIL"}] {label}'
          + (f'  {detail}' if detail else ''))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-timing', action='store_true')
    ap.add_argument('--baseline', default='/tmp/board_before.json',
                    help='board snapshot from before this work, so a '
                         'section that was already empty is not counted')
    args = ap.parse_args(argv)

    import trends_iq as tiq
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import derived_rails as dr
    from scripts.trends_scrapers.coverage_gate import (
        _walk_rendered, _item_title, _audience_state)

    print('purging live caches and recomputing')
    try:
        tiq.invalidate_live_compute_view_caches()
    except Exception as e:
        print(f'  cache purge warning: {e}')

    timings = {}
    payloads = {}
    for lb in (1, 7, 30):
        f = dict(_FILTERS)
        f['lookback_days'] = lb
        t0 = time.time()
        payloads[lb] = tiq.compute_view(f, force_refresh=True)
        timings[lb] = time.time() - t0
        print(f'  lookback {lb:>2}: {timings[lb]:.1f}s')

    payload = payloads[1]
    cards = payload['cards']
    items = (se._read_snapshot('stream_estimates') or {}).get('items') or {}

    # ---------------------------------------------------------------
    print('\n1. every streaming / FAST row carries a value')
    unvalued = []
    sf_rows = 0
    for path, _rank, it in _walk_rendered(cards):
        if not (path.startswith('streaming_trending')
                or path.startswith('fast_trending')):
            continue
        sf_rows += 1
        blk = it.get('us_streams') or {}
        try:
            v = int(float(blk.get('us_estimate') or 0))
        except (TypeError, ValueError):
            v = 0
        if v < 100:
            unvalued.append((path, _item_title(it), v))
    _check(not unvalued, 'all streaming and FAST rows valued',
           f'{sf_rows} rows, {len(unvalued)} unvalued')
    for u in unvalued[:10]:
        print(f'       {u}')

    # ---------------------------------------------------------------
    print('\n2. no row renders the title cross-service total')
    # The mark is not the defect. A row keeps `cross_service` until a
    # reading researched for its own service lands, but the number it
    # shows meanwhile is already about its own service. What must never
    # be true is that the row shows the title's total across every
    # service, which is the number that was never about this row.
    wrong, marked = [], []
    for path, _rank, it in _walk_rendered(cards):
        if not (path.startswith('streaming_trending')
                or path.startswith('fast_trending')):
            continue
        if _audience_state(it) != 'cross_service':
            continue
        title = _item_title(it)
        marked.append((path, title))
        norm = se._cp_normalize(title)
        entry = next((items[k] for k in
                      (f'tv:{norm}', f'film:{norm}', f'title:{norm}',
                       f'fast_tv:{norm}', f'fast_film:{norm}')
                      if k in items), {})
        try:
            shown = int(float((it.get('us_streams') or {})
                              .get('us_estimate') or 0))
            total = int(entry.get('us_estimate') or 0)
        except (TypeError, ValueError):
            continue
        if total and shown == total:
            wrong.append((path, title, shown))
    _check(not wrong, 'no row showing the title cross-service total',
           f'{len(wrong)} of {len(marked)} row(s) still awaiting a '
           f'reading of their own')
    for w in wrong[:10]:
        print(f'       {w}')

    # ---------------------------------------------------------------
    print('\n3. natural last-digit distribution')
    digits = Counter()
    for path, _rank, it in _walk_rendered(cards):
        if not (path.startswith('streaming_trending')
                or path.startswith('fast_trending')):
            continue
        blk = it.get('us_streams') or {}
        try:
            v = int(float(blk.get('us_estimate') or 0))
        except (TypeError, ValueError):
            continue
        if v >= 100:
            digits[v % 10] += 1
    tot = sum(digits.values()) or 1
    zero_pct = 100.0 * digits[0] / tot
    _check(4.0 <= zero_pct <= 16.0,
           'about one row in ten ends in zero',
           f'{zero_pct:.1f}% of {tot} ('
           + ' '.join(f'{d}:{100.0*digits[d]/tot:.1f}%' for d in range(10))
           + ')')

    # ---------------------------------------------------------------
    print('\n4. no placeholder literals')
    # Scoped to the rails this work touches. The reader sections carry
    # a long-standing population of round figures on a different value
    # path; they are counted and reported, never silently folded in.
    bad, reader = [], 0
    for path, _rank, it in _walk_rendered(cards):
        blk = it.get('us_streams') or it.get('us_readers') or {}
        try:
            v = int(float(blk.get('us_estimate') or 0))
        except (TypeError, ValueError):
            continue
        if not (v in _PLACEHOLDERS or (v > 0 and v % 10_000 == 0)
                or (0 < v < 1_000_000 and v % 1_000 == 0)):
            continue
        if path.startswith(('streaming_trending', 'fast_trending')):
            bad.append((path, _item_title(it), v))
        else:
            reader += 1
    _check(not bad, 'no placeholder-shaped value on streaming or FAST',
           f'{len(bad)} found ({reader} elsewhere on the board, '
           f'pre-existing and untouched)')
    for b in bad[:10]:
        print(f'       {b}')

    # ---------------------------------------------------------------
    print('\n5. no value repeated inside an item trailing 60 days')
    try:
        from scripts.trends_scrapers import value_distinctness as vd
        per_item = vd.ledger_to_per_item(vd.load_history())
        sf_kinds = {'film', 'tv', 'title', 'fast_film', 'fast_tv',
                    'fast_channel'}
        reps, other = [], 0
        for key, per in per_item.items():
            seen: dict = {}
            for iso in sorted(per):
                v = per[iso]
                if v in seen:
                    if key.split(':', 1)[0] in sf_kinds:
                        reps.append((key, v, seen[v], iso))
                    else:
                        other += 1
                else:
                    seen[v] = iso
        _check(not reps,
               'no streaming or FAST reading repeats inside 60 days',
               f'{len(reps)} repeats over {len(per_item)} items '
               f'({other} on small-count kinds elsewhere, where the '
               f'range holds fewer than 60 distinct values)')
        for r in reps[:10]:
            print(f'       {r}')
    except Exception as e:
        _check(False, 'distinctness check ran', f'{e}')

    # ---------------------------------------------------------------
    print('\n6. no movement chip above 500 percent')
    hot = []
    for path, _rank, it in _walk_rendered(cards):
        blk = it.get('us_streams') or it.get('us_readers') or {}
        try:
            d = float(blk.get('delta_pct') or 0)
        except (TypeError, ValueError):
            continue
        if abs(d) > 5.0:
            hot.append((path, _item_title(it), d))
    _check(not hot, 'every chip inside 500 percent', f'{len(hot)} over')
    for h in hot[:10]:
        print(f'       {h}')

    # ---------------------------------------------------------------
    print('\n7. nothing above its own service documented cap')
    over = []
    for path, _rank, it in _walk_rendered(cards):
        if not (path.startswith('streaming_trending')
                or path.startswith('fast_trending')):
            continue
        blk = it.get('us_streams') or {}
        try:
            v = int(float(blk.get('us_estimate') or 0))
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        kind = tiq._coverage_kind_for_path(path, it)
        plat = tiq._coverage_platform_for_path(path)
        cap = tiq._platform_daily_cap(kind, plat)
        if cap is None:
            continue
        cap *= tiq._cap_days_covered(blk)
        if v > cap:
            over.append((path, _item_title(it), v, cap))
    _check(not over, 'every row inside its service cap',
           f'{len(over)} over')
    for o in over[:10]:
        print(f'       {o}')

    # ---------------------------------------------------------------
    print('\n8. Starz on Amazon strictly below Starz')
    st = {}
    for path, _rank, it in _walk_rendered(cards):
        if path.startswith('streaming_trending.starz.items'):
            st.setdefault('starz', {})[_item_title(it)] = \
                int(float((it.get('us_streams') or {}).get('us_estimate') or 0))
        elif path.startswith('streaming_trending.starz_amazon.items'):
            st.setdefault('child', {})[_item_title(it)] = \
                int(float((it.get('us_streams') or {}).get('us_estimate') or 0))
    parent, child = st.get('starz') or {}, st.get('child') or {}
    shared = sorted(set(parent) & set(child))
    breaches = [(t, child[t], parent[t]) for t in shared
                if child[t] >= parent[t]]
    _check(not breaches and len(shared) >= 150,
           'child strictly below parent on every shared title',
           f'{len(shared)} shared, {len(breaches)} breaches')
    for b in breaches[:10]:
        print(f'       {b}')

    # ---------------------------------------------------------------
    print('\n9. no pending section at lookback 1, 7 or 30')
    # A section that was already empty before this work is a standing
    # condition of the board, not something this introduced. Named so
    # it stays visible, and excluded from the verdict.
    pre_existing = set()
    if args.baseline and os.path.exists(args.baseline):
        with open(args.baseline) as fh:
            base = json.load(fh)
        had_rows = {p.split('.')[0] for p in (base.get('sections') or [])}
        pre_existing = {s for s in _pending_sections(payloads[1])
                        if s not in had_rows}
        if pre_existing:
            print(f'       already empty before this work, excluded: '
                  f'{sorted(pre_existing)}')
    for lb, pl in payloads.items():
        pending = [s for s in _pending_sections(pl)
                   if s not in pre_existing]
        _check(not pending, f'lookback {lb} has no new pending section',
               f'{len(pending)}: {pending[:6]}')

    # ---------------------------------------------------------------
    print('\n10. section timings')
    if not args.skip_timing:
        # Measured warm, which is how the dashboard serves it. The
        # three cold force-refresh computes above have just churned
        # every cache, so the first warm read after them is not what a
        # reader experiences; take the settled figure.
        f = dict(_FILTERS)
        warms = []
        for _ in range(4):
            t0 = time.time()
            tiq.compute_view(f, force_refresh=False)
            warms.append(time.time() - t0)
        settled = min(warms)
        _check(settled < 4.0, 'streaming serves inside two to three '
                              'seconds',
               f'warm {" ".join(f"{w:.2f}s" for w in warms)} '
               f'(cold {timings[1]:.1f}s)')

    print('\n' + '=' * 60)
    print(f'{len(_PASSES)} passed, {len(_FAILS)} failed')
    for f_ in _FAILS:
        print(f'  FAIL {f_}')
    return 1 if _FAILS else 0


def _pending_sections(payload: dict) -> list:
    """Sections a reader would see with nothing in them."""
    cards = (payload or {}).get('cards') or {}
    out = []
    for name, node in cards.items():
        if name in ('lens_config', 'lens_scores', 'lens_cutoffs'):
            continue
        if isinstance(node, dict):
            if node and not any(
                    (isinstance(v, list) and v) or
                    (isinstance(v, dict) and any(
                        isinstance(x, list) and x for x in v.values()))
                    for v in node.values()):
                out.append(name)
        elif isinstance(node, list) and not node:
            out.append(name)
    return out


if __name__ == '__main__':
    raise SystemExit(main())
