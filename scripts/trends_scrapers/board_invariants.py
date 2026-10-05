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
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

CAP_SEAT_BAND = 0.05        # within 5% of the service's daily cap
CAP_SEAT_TIGHT = 0.005      # and within 0.5% of each other
MAX_FIX_ATTEMPTS = 3        # fixer passes per gate while still converging

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
            # Wide enough that a value is never cut mid-number: the
            # 2026-10-03 alert read 'below_v: 57281' for a 572,81x row.
            lines.append('     ' + json.dumps(v, ensure_ascii=False)[:320])
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


# ---------------------------------------------------------------------------
# Terminal fixer: arithmetic on the rendered board, written to the store
# ---------------------------------------------------------------------------
# Spacing between neighbouring positions when a value has to be moved
# under the one above it. Same band the chart coherence pass uses, so a
# row this pass places is indistinguishable from one that pass placed.
_STEP_MIN = 0.015
_STEP_MAX = 0.060


def _step(salt: str) -> float:
    from scripts.trends_scrapers import stream_estimates as se
    return _STEP_MIN + se._h01(salt) * (_STEP_MAX - _STEP_MIN)


def _under(value: float, limit: Optional[int], title: str, salt: str) -> int:
    """Natural last digits on `value`, never above `limit`, never below 1."""
    from scripts.trends_scrapers import published_chart_coherence as pcc
    lim = int(limit) if limit else None
    return max(1, pcc._natural_under(max(1, int(round(value))), lim,
                                     title or '', salt))


def _resolve(se, ces, store: dict, slug: str, prefix: str, it: dict,
             is_channel: bool) -> tuple[list[str], Optional[int]]:
    """The store keys the rendered row reads, and the store's reading.

    The candidates come back whether or not the store has an entry under
    any of them (a chart entrant the store has never seen gets created
    under the first); the reading is None when the store has no value
    for this service, which is also the carried-forward case, where the
    page is showing yesterday's number because today's store has no
    block for the service. Only a store reading that EXISTS and differs
    from what rendered is a real mismatch.
    """
    name = (it.get('name') if is_channel else it.get('title')) or ''
    norm = se._cp_normalize(name)
    if not norm:
        return [], None
    if is_channel:
        cands = [f'fast_channel:{slug}:{norm}']
    else:
        cands = ces.entry_key_candidates(prefix, _kind(it), norm)
    return cands, ces.reading_for(store, cands, slug)


def _seed_entry(store: dict, key: str, it: dict, slug: str, day: str,
                is_channel: bool, unit_label: Optional[str]) -> None:
    """Create the store entry a rendered row has none of.

    2026-10-05: titles that entered a chart in the morning scrape (Ice
    Age, Men In Black 3, Grumpier Old Men, Grizzly Night) had no entry
    under any key, so every writer silently skipped them and the rows
    stayed where the page had them. The entry is modelled on the
    rendered row; the service block is added by `write_across`.
    """
    kind = key.split(':', 1)[0]
    if kind.startswith('fast_'):
        kind = kind[len('fast_'):]
    ent: dict[str, Any] = {
        'kind': 'channel' if is_channel else kind,
        'display_title': (it.get('name') if is_channel else it.get('title')) or '',
        'artist': '',
        'image': it.get('image'),
        'url': it.get('url'),
        'us_estimate': 0,
        'confidence': 'low',
        'as_of_date': day,
    }
    if unit_label:
        ent['unit_label'] = unit_label
    for f in ('published_rank', 'published_chart', 'published_group'):
        if it.get(f) is not None:
            ent[f] = it[f]
    if it.get('published_rank'):
        ent['best_rank'] = it['published_rank']
        if it.get('published_chart'):
            ent['chart_labels'] = [f"{it['published_chart']} #{it['published_rank']}"]
    store[key] = ent


def _rail_unit(rows: list) -> Optional[str]:
    """The unit label the rail's own rows carry (for entries this pass
    has to create)."""
    for it in rows:
        blk = it.get('us_streams') if isinstance(it.get('us_streams'), dict) else None
        if blk and blk.get('unit_label'):
            return blk['unit_label']
    return None


def _write_row(se, ces, store: dict, stats: dict, rail: str, slug: str,
               day: str, r: dict, new_v: int, why: str, is_channel: bool,
               unit_label: Optional[str], salt0: str) -> bool:
    """Put `new_v` on the store for one rendered row, creating the entry
    or the service block when the store lacks them."""
    if not ces.present(store, r['cands']):
        # The chart's own kind leads the candidates; a row whose kind
        # the page does not state is seeded under the generic title
        # key rather than guessed as a film.
        key = r['cands'][0]
        if not _kind(r['it']) and not is_channel:
            generic = [k for k in r['cands'] if k.split(':', 1)[0].endswith('title')]
            if generic:
                key = generic[0]
        _seed_entry(store, key, r['it'], slug, day, is_channel, unit_label)
        stats['created_entries'] = stats.get('created_entries', 0) + 1
    res = ces.write_across(se, store, r['cands'], slug, new_v, f'{salt0}|{why}')
    if not (res.get('set') or res.get('created')):
        # A blank row's block exists with no reading, and the shared
        # setter moves readings rather than creating them. Seat it
        # directly.
        for k in ces.present(store, r['cands']):
            it = store[k]
            blk = (it.get('by_platform') or {}).get(slug)
            if isinstance(blk, dict) and not blk.get('us_estimate'):
                blk['us_estimate'] = new_v
                blk['us_estimate_low'] = max(1, int(new_v * 0.74))
                blk['us_estimate_high'] = max(new_v, int(new_v * 1.36))
                if not isinstance(it.get('us_estimate'), int) or it['us_estimate'] <= 0:
                    it['us_estimate'] = new_v
                res['set'] = res.get('set', 0) + 1
    if res.get('set') or res.get('created'):
        stats['moved'] += 1
        stats['detail'].append({'rail': rail, 'title': r['title'], 'to': new_v, 'why': why})
        return True
    stats['unresolved'] += 1
    return False


def terminal_fix(payload: dict, *, write: bool = True) -> dict[str, Any]:
    """Correct, in place, whatever the set-level pass left on the board.

    The set-level fixer reasons every chart and catalog again and is
    the right first answer; on 2026-10-03 it ran three times and left
    one Netflix series pair reading the wrong way round (The Great
    British Baking Show above LEGO ONE PIECE on the chart, 174,193
    against 572,81x on the page), and the gate's only remaining move
    was an email. This is the move it has now. Pure arithmetic on the
    values the page is showing, in the order the page is showing them:

      I1  a chart position reading at or above the one above it is
          placed a drawn step (1.5% to 6%) under it, cascading down;
      I6  a blank chart or tail row takes a step under its upper
          neighbour (or a step over its lower one at the top);
      I3  a catalog row reading above the chart's last position is
          placed under that floor, breaches keeping their own order;
      I5  the tail after the chart descends in render order;
      I4  two rows seated within 0.5% of each other at the platform
          cap are spaced apart.

    Every value is written across every sibling key the render could
    resolve (`chart_entry_sync.write_across`), capped at the service's
    daily ceiling, with natural last digits. A row whose store reading
    does not match what rendered is used as an anchor but never
    written, because the page is reading something this pass cannot
    see. I2 (no chart published) is not a value and is left alone.
    """
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import chart_entry_sync as ces
    from scripts.trends_scrapers import _base

    board = se._read_snapshot('stream_estimates') or {}
    store = board.get('items') or {}
    stats: dict[str, Any] = {'moved': 0, 'unresolved': 0, 'mismatch': 0,
                             'rails': 0, 'written': False, 'detail': []}
    if not store:
        stats['error'] = 'no store'
        return stats
    caps = _daily_caps()
    cards = (payload or {}).get('cards') or {}
    day = board.get('target_date') or ''
    top_rows: dict[str, tuple] = {}

    for section, _label in _SECTIONS:
        for slug, panel in (cards.get(section) or {}).items():
            if not isinstance(panel, dict):
                continue
            is_channel = False
            rows = panel.get('items') if isinstance(panel.get('items'), list) else []
            if not rows and isinstance(panel.get('channels'), list):
                rows, is_channel = panel['channels'], True
            if not rows:
                continue
            rail = f'{section}.{slug}'
            prefix = se.published_chart_key_prefix(slug)
            cap = caps.get(slug)
            salt0 = f'{day}|terminal|{slug}'

            # One record per rendered row: value as rendered, store
            # keys, and whether the store agrees with the page.
            recs = []
            for i, it in enumerate(rows):
                v = _row_value(it)
                cands, reading = _resolve(se, ces, store, slug, prefix,
                                          it, is_channel)
                # Writable unless the store holds a reading for this
                # service that is not what the page shows. No reading
                # at all (carried forward, or an entry the store has
                # never seen) is writable: the write creates the block
                # or the entry and the page reads it from then on.
                writable = bool(cands) and (reading is None or reading == v)
                if cands and not writable and v:
                    stats['mismatch'] += 1
                g, pos = _chart_group(it)
                recs.append({'i': i, 'it': it, 'v': v, 'cands': cands,
                             'writable': writable, 'g': g, 'pos': pos,
                             'title': (it.get('name') if is_channel
                                       else it.get('title')) or '',
                             'shelf': bool(it.get('collection')
                                           and it.get('bucket_rank'))})

            moves: list[tuple[dict, int, str]] = []

            def _place(r, new_v, why):
                new_v = max(1, int(new_v))
                if new_v == r['v']:
                    return
                if r['cands'] and r['writable']:
                    moves.append((r, new_v, why))
                else:
                    stats['unresolved'] += 1
                r['v'] = new_v   # anchors below use the placed value

            # I1 + chart blanks: each chart in its published order.
            charts: dict[str, list] = defaultdict(list)
            for r in recs:
                if r['g']:
                    charts[r['g']].append(r)
            floors: dict[str, int] = {}
            for g, seq in charts.items():
                seq.sort(key=lambda r: r['pos'])
                prev: Optional[int] = None
                for j, r in enumerate(seq):
                    salt = f'{salt0}|{g}|{r["pos"]}'
                    limit = cap
                    if prev is not None:
                        limit = prev - 1 if limit is None else min(limit, prev - 1)
                    if not r['v']:
                        if prev is not None:
                            _place(r, _under(prev * (1 - _step(salt)), limit,
                                             r['title'], salt), 'I6 chart blank')
                        else:
                            nxt = next((x['v'] for x in seq[j + 1:] if x['v']), 0)
                            if nxt:
                                _place(r, _under(nxt * (1 + _step(salt)), cap,
                                                 r['title'], salt), 'I6 chart blank')
                    elif prev is not None and r['v'] >= prev:
                        _place(r, _under(prev * (1 - _step(salt)), limit,
                                         r['title'], salt), 'I1 chart order')
                    elif cap and r['v'] > cap:
                        _place(r, _under(cap, limit, r['title'], salt), 'cap')
                    if r['v']:
                        prev = r['v']
                kind = g.rsplit('|', 1)[-1].strip().lower()
                kind = 'film' if kind == 'film' else ('tv' if kind else '')
                sub = g.split('|')[1].lower() if '|' in g else ''
                if not kind and sub:
                    kind = 'film' if ('film' in sub or 'movie' in sub) else 'tv'
                last = min((r['v'] for r in seq if r['v']), default=0)
                if last:
                    floors[kind] = (max(floors[kind], last) if kind in floors
                                    else last)

            # I3: catalog rows above their kind's floor go under it,
            # keeping their order among themselves.
            if floors:
                for kind in set(_kind(r['it']) for r in recs):
                    floor = floors.get(kind)
                    if floor is None:
                        floor = min(floors.values())
                    breach = [r for r in recs
                              if not r['g'] and not r['shelf'] and r['v'] > floor
                              and _kind(r['it']) == kind]
                    breach.sort(key=lambda r: -r['v'])
                    cur = floor
                    for r in breach:
                        salt = f'{salt0}|catalog|{r["title"]}'
                        new_v = _under(cur * (1 - _step(salt)), cur - 1,
                                       r['title'], salt)
                        _place(r, new_v, 'I3 catalog under chart')
                        cur = r['v']

            # I5 + tail blanks: the rendered tail descends.
            tail = [r for r in recs if not r['g']]
            prev = None
            for j, r in enumerate(tail):
                salt = f'{salt0}|tail|{r["i"]}'
                if not r['v']:
                    if prev is not None:
                        _place(r, _under(prev * (1 - _step(salt)), prev - 1,
                                         r['title'], salt), 'I6 blank')
                    else:
                        nxt = next((x['v'] for x in tail[j + 1:] if x['v']), 0)
                        if nxt:
                            _place(r, _under(nxt * (1 + _step(salt)), cap,
                                             r['title'], salt), 'I6 blank')
                elif prev is not None and r['v'] > prev:
                    _place(r, _under(prev * (1 - _step(salt)), prev - 1,
                                     r['title'], salt), 'I5 rail order')
                if r['v']:
                    prev = r['v']

            # I4: seats at the cap spaced apart.
            if cap:
                near = sorted((r for r in recs if r['v'] >= cap * (1 - CAP_SEAT_BAND)),
                              key=lambda r: -r['v'])
                for j in range(1, len(near)):
                    a, b = near[j - 1], near[j]
                    if a['v'] - b['v'] <= a['v'] * CAP_SEAT_TIGHT:
                        salt = f'{salt0}|seat|{b["title"]}'
                        _place(b, _under(a['v'] * (1 - _step(salt)), a['v'] - 1,
                                         b['title'], salt), 'I4 cap seat')

            if moves:
                stats['rails'] += 1
            for r, new_v, why in moves:
                _write_row(se, ces, store, stats, rail, slug, day, r, new_v,
                           why, is_channel, _rail_unit(rows), salt0)
            top_rows[rail] = (slug, recs, rows, is_channel)

    # I4 across services: two rails under one cap whose top rows sit
    # within 0.5% of each other near it. The lower of the pair takes
    # a drawn step under the higher.
    by_cap: dict[int, list] = defaultdict(list)
    for rail, (slug, recs, rows, is_channel) in top_rows.items():
        cap = caps.get(slug)
        if not cap or not recs:
            continue
        best = max((r for r in recs if r['v']), key=lambda r: r['v'], default=None)
        if best and best['v'] >= cap * (1 - CAP_SEAT_BAND):
            by_cap[cap].append((best['v'], rail, slug, best, rows, is_channel))
    for cap, tops in by_cap.items():
        tops.sort(key=lambda x: x[0])
        for i in range(len(tops) - 1):
            lo, hi = tops[i], tops[i + 1]
            if hi[0] - lo[0] <= hi[0] * CAP_SEAT_TIGHT:
                _, rail, slug, r, rows, is_channel = lo
                salt = f'{day}|terminal|{slug}|xseat|{r["title"]}'
                new_v = _under(hi[0] * (1 - _step(salt)), hi[0] - 1, r['title'], salt)
                if r['cands'] and r['writable'] and new_v != r['v']:
                    stats['rails'] += 1
                    _write_row(se, ces, store, stats, rail, slug, day, r, new_v,
                               'I4 cap seat across services', is_channel,
                               _rail_unit(rows), f'{day}|terminal|{slug}')
                    r['v'] = new_v
                    lo = (new_v,) + lo[1:]
                else:
                    stats['unresolved'] += 1

    for d in stats['detail'][:40]:
        logger.info('board terminal fix: %s %r -> %s (%s)',
                    d['rail'], d['title'], f"{d['to']:,}", d['why'])
    if stats['moved'] and write:
        board['items'] = store
        board['count'] = len(store)
        board['board_terminal_fix_at'] = datetime.now(timezone.utc).isoformat()
        _base.write_snapshot('stream_estimates', board)
        stats['written'] = True
    logger.info('board terminal fix: %d value(s) placed on %d rail(s), '
                '%d unresolved, %d store/page mismatch(es), written=%s',
                stats['moved'], stats['rails'], stats['unresolved'],
                stats['mismatch'], stats['written'])
    return stats


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
        # Both streams, and to stdout as well as the logger: the fixer
        # logs its per-chart outcome (and any failed model call) on
        # stderr, and a run of it that silently did nothing was
        # invisible when only stdout travelled (2026-10-01).
        for name, text in (('stdout', proc.stdout), ('stderr', proc.stderr)):
            text = (text or '').strip()
            if text:
                logger.info('[board fixer %s] %s', name, text[-6000:])
                print(f'[board fixer {name}]\n{text[-6000:]}', flush=True)
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
    print(f"board invariants (before): {first['violations']} violation(s)",
          flush=True)
    if first['violations'] == 0:
        return first
    logger.info('\n' + format_report(first))
    result = first
    if fix:
        # Blanks are fixable too. The fixer's set pass prices every
        # title on a chart and its catalog sizing prices every row
        # under one, so a rail whose chart moved during the day (the
        # six new Netflix entrants on 2026-10-01 rendered empty cells
        # from 09:10 UTC until the next pass) is closed here rather
        # than reported and left for tonight. I2 is the one invariant
        # no pass can fix: a service that published no chart.
        fixable = (len(first['I1_chart_order']) + len(first['I3_catalog_under'])
                   + len(first['I4_cap_seats']) + len(first['I5_rail_order'])
                   + len(first['I6_blanks']))
        # Up to MAX_FIX_ATTEMPTS passes, continuing only while a pass
        # still reduces the count. The nightly on 2026-10-01 went
        # 37 -> 7 -> 7: a second attempt that changes nothing is a
        # defect in the fixer and gets reported, not retried forever;
        # a pass that is still converging gets to finish the job.
        attempt = 0
        again: Optional[dict] = None
        while fixable and attempt < MAX_FIX_ATTEMPTS:
            attempt += 1
            rc = _run_fixer()
            logger.info('board invariants: fixer attempt %d exited %d; '
                        're-auditing', attempt, rc)
            print(f'board invariants: fixer attempt {attempt} exited {rc}; '
                  f're-auditing', flush=True)
            again = _fresh_view()
            if again is None:
                logger.error('board invariants: the view could not be '
                             'recomputed after the fixer; reporting the '
                             'last audit')
                print('board invariants: view recompute failed after the '
                      'fixer; the report below predates this attempt',
                      flush=True)
                break
            before_n = result['violations']
            result = audit(again)
            result['fixed'] = first['violations'] - result['violations']
            result['fix_attempts'] = attempt
            logger.info('board invariants (after attempt %d): %d violation(s), '
                        '%d fixed so far', attempt, result['violations'],
                        result['fixed'])
            print(f"board invariants (after attempt {attempt}): "
                  f"{result['violations']} violation(s), {result['fixed']} "
                  f"fixed so far", flush=True)
            fixable = (len(result['I1_chart_order']) + len(result['I3_catalog_under'])
                       + len(result['I4_cap_seats']) + len(result['I5_rail_order'])
                       + len(result['I6_blanks']))
            if result['violations'] >= before_n:
                break   # no progress: another identical pass will not help

        # Whatever the set-level pass left is placed arithmetically,
        # on the page's own values in the page's own order, and the
        # board is re-read. The gate's last move used to be an email;
        # a survivor is a defect in a pass and gets corrected here,
        # not reported and left (Jenna 2026-10-05: "they should self
        # fix").
        if fixable and again is not None:
            try:
                tf = terminal_fix(again)
                result['terminal_fix'] = {k: tf.get(k) for k in
                                          ('moved', 'rails', 'unresolved',
                                           'mismatch', 'written')}
                print(f"board invariants: terminal fix placed {tf.get('moved', 0)} "
                      f"value(s) on {tf.get('rails', 0)} rail(s), "
                      f"{tf.get('unresolved', 0)} unresolved", flush=True)
                if tf.get('written'):
                    final = _fresh_view()
                    if final is not None:
                        result_after = audit(final)
                        result_after['fixed'] = (first['violations']
                                                 - result_after['violations'])
                        result_after['fix_attempts'] = result.get('fix_attempts', 0)
                        result_after['terminal_fix'] = result['terminal_fix']
                        result = result_after
                        logger.info('board invariants (after terminal fix): '
                                    '%d violation(s)', result['violations'])
                        print(f"board invariants (after terminal fix): "
                              f"{result['violations']} violation(s)", flush=True)
            except Exception:
                logger.exception('board invariants: terminal fix failed '
                                 '(non-fatal)')
    if result['violations'] and alert:
        try:
            from scripts.trends_scrapers.run_guard import send_alert
            tf = result.get('terminal_fix') or {}
            body = ('The rendered Trends IQ board failed its invariants after '
                    'the pricing pass, the in-place set-level fix attempts, '
                    'and the terminal arithmetic placement. These are '
                    'the rules a platform\'s own people would check: chart '
                    'order, chart present, catalog under the chart, no cap '
                    'seats, rail order, no blanks. A survivor here is a row '
                    'the store cannot reach from the page (no entry under any '
                    'key the render reads, or a service that published no '
                    'chart), not a wait for the next run.\n\n'
                    f"terminal placement: {tf.get('moved', 0)} value(s) placed, "
                    f"{tf.get('unresolved', 0)} unresolved, "
                    f"{tf.get('mismatch', 0)} store/page mismatch(es)\n\n"
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
    ap.add_argument('--terminal-fix', action='store_true',
                    help='audit, then place every survivor arithmetically '
                         'and write the store (no set-level pass, no alert)')
    ap.add_argument('--dry-run', action='store_true',
                    help='with --terminal-fix: compute and log, write nothing')
    args = ap.parse_args(argv)
    if args.gate:
        res = gate()
        print(format_report(res) if 'rails' in res else res)
        return 0 if res.get('violations') == 0 else 1
    if args.terminal_fix:
        payload = _fresh_view()
        if payload is None:
            return 2
        before = audit(payload)
        print(format_report(before))
        if not before['violations']:
            return 0
        tf = terminal_fix(payload, write=not args.dry_run)
        print(json.dumps({k: v for k, v in tf.items() if k != 'detail'}))
        for d in tf.get('detail') or []:
            print(f"   {d['rail']:40s} {d['title']!r} -> {d['to']:,} ({d['why']})")
        if args.dry_run or not tf.get('written'):
            return 0
        after = audit(_fresh_view() or {})
        print(format_report(after))
        return 0 if after.get('violations') == 0 else 1
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
