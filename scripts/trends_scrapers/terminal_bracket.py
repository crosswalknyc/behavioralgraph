"""Terminal pass: no rendered Trends row ships without a reasoned value.

Jenna, 2026-09-25, on the "still missing a US Audience value" email:
*"it should not have not found these but should have figured out how to
reason answers to them"*.

The coverage gate (`coverage_gate.run_gate`) researches every blank
row, merges what comes back, re-audits, and used to email whatever was
left with "the dashboard shows them without an audience chip until the
next run". That is the hold-for-the-next-run pattern every other
pipeline in this repo already retired (`no-rebuild-level-correction`).
This module is the missing terminal step: a row the research could not
price is not an unknown, because the platform's own list already says
where it sits.

Which neighbours
----------------
The render sorts a blank row to the bottom of its list, so its rendered
position says nothing about it. The row's TRUE position is read from
what the platform said:

  published chart   `published_rank` inside its `published_chart`
                    (the Top 10 Series and Top 10 Movies rails). A
                    blank #9 is bracketed by the chart's #8 and #10.
  catalog shelf     `bucket_rank` inside its `collection` (a browse
                    shelf below the chart). A blank shelf position 32
                    is bracketed by shelf positions 31 and 33.
  neither           the rendered `rank`, which on those lists is our
                    descending order.

A group whose valued rows cannot bracket the row falls back to the
rendered list as a whole.

How a value is reasoned
-----------------------
For a blank row at position i in its group:

  both neighbours   The nearest valued rows above (a, at j) and below
                    (b, at k) bracket it. The value sits on the
                    geometric line between them at the row's position
                    in the gap, nudged by a per-row salt that stays
                    inside the gap, so a run of blank rows still
                    descends and no two share a value:
                        v = exp(ln b + (ln a - ln b) * t),
                        t = (k - i) / (k - j) shifted by up to
                            +/- 0.4 / (k - j)
  above only        A step below the row above at the group's OWN
                    local slope: the median ratio between consecutive
                    valued rows nearest the gap, salted by a few
                    percent, held in [0.80, 0.995]. A run of blank rows
                    chains from the row just filled. (A fixed steep
                    step was tried first and took ESPN+ from 11,738 to
                    205 in 18 rows; its valued rows fall a few percent
                    a row.)
  below only        The inverse: a step above the row below at the
                    local slope, held under the service's daily
                    ceiling, chaining upward.
  no valued row     The list is blank end to end. Position 1 is sized
                    off the service's daily ceiling at a salted share
                    in [0.055, 0.125], and each following row steps
                    down by a salted ratio in [0.90, 0.97]. With no
                    ceiling on file, the reference is the median
                    top-row value across the other lists of the same
                    kind, at a salted share of it.

Every value then takes natural last digits (`_natural_last_digits`),
stays strictly inside its bracket wherever an integer fits there (a
list whose readings bottom out at 1 ties at 1, as its own rows already
do, rather than leaving a row blank), and is written as a reading FOR the
service the row is on (`by_platform[service]`) with
`est_basis='bracketed'`, so the row on Max shows a number about Max and
nothing else. The same title on two lists of one service (`.items` and
`.tv`) is written once; `.items` is walked first because it is the
finer list, and a value inside its neighbours is inside the coarser
list's neighbours too.

What it is not
--------------
Not a rank tier. A rank tier was one number per position regardless of
list, which said nothing about the title; this reads the two titles the
platform placed either side of it, on this service, today. It is the
same move `chart_set_reasoning` makes for an unpublished title inside a
published chart, applied at the end of the day to whatever is left.

Not permanent. A bracketed reading renders today. The gate counts it
apart from researched rows and puts it back into tonight's research,
so a real reading replaces it as soon as one lands.

This supersedes the "or nothing" arm of the provenance rule in
`trends_iq._coverage_is_service_rail`: a row on a service rail shows a
reading for that service, or its own service-scoped last reading, or a
value bracketed by its neighbours on that service. Never another
service's number, never a position tier.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from statistics import median
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASIS = 'bracketed'

# Prefer the finer list of a service so the coarser one inherits a
# value already inside its own bracket.
_LIST_ORDER = {'items': 0, 'channels': 0, 'tv': 1, 'films': 1}


def _list_rank(path: str) -> int:
    return _LIST_ORDER.get(path.rsplit('.', 1)[-1], 2)


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
    try:
        h = int(float(it.get('holds') or 0))
        if h > 0:
            return h
    except (TypeError, ValueError):
        pass
    return 0


def _valued(cg, it: dict) -> int:
    """The value a neighbour contributes to a bracket, 0 when it has
    none the gate would count as rendered."""
    return _row_value(it) if cg._audience_state(it) != 'missing' else 0


def _order_key(it: dict) -> tuple[str, int]:
    """(group, position) the platform gave this row. See module doc."""
    pr = it.get('published_rank')
    if isinstance(pr, (int, float)) and pr > 0:
        # One service publishes two charts under one label (Disney+
        # names both "Disney+ Top 10 US Today"), so the Film / TV split
        # is part of the group or the two charts interleave.
        # Netflix tells its two charts apart by `published_group`
        # (us_tv / us_films) under one `published_chart` label;
        # Disney+ by `category_display`. Both travel in the key.
        grp = it.get('published_chart') or 'chart'
        sub = it.get('published_group') or ''
        cat = it.get('category_display') or ''
        return f'chart:{grp}|{sub}|{cat}', int(pr)
    br = it.get('bucket_rank')
    coll = it.get('collection')
    if coll and isinstance(br, (int, float)) and br > 0:
        return f'shelf:{coll}', int(br)
    return '', 0


def _local_slope(values: list[int], at: int, *, direction: int) -> float:
    """Median ratio (next / previous, so <= 1 on a descending list)
    between consecutive valued rows nearest position `at`, looking
    backwards (direction -1) or forwards (+1). 0.0 when fewer than two
    valued rows are available to read a slope from."""
    idx = [i for i, v in enumerate(values) if v > 0]
    if direction < 0:
        idx = [i for i in idx if i < at][-8:]
    else:
        idx = [i for i in idx if i > at][:8]
    ratios = []
    for p, q in zip(idx, idx[1:]):
        if values[p] > 0 and values[q] > 0 and q > p:
            ratios.append((values[q] / values[p]) ** (1.0 / (q - p)))
    if not ratios:
        return 0.0
    return float(median(ratios))


def _reason_sequence(se, values: list[int], missing_idx: list[int],
                     *, salt_base: str, ceiling: int,
                     kind_reference: int) -> dict[int, int]:
    """Values for the blank positions of one ordered sequence.

    `values` is the sequence's current value per position (0 = blank),
    in the platform's order; `missing_idx` the positions to fill.
    Returns {position: value}. Values are strictly inside their
    brackets and strictly descending through a run.
    """
    n = len(values)
    out: dict[int, int] = {}
    filled = dict(enumerate(values))   # position -> value, grows as we go

    def h(i: int, tag: str) -> float:
        return se._h01(f'{salt_base}|{i}|{tag}')

    def above(i: int) -> tuple[Optional[int], int]:
        for j in range(i - 1, -1, -1):
            if filled.get(j, 0) > 0:
                return j, filled[j]
        return None, 0

    def below(i: int) -> tuple[Optional[int], int]:
        for k in range(i + 1, n):
            if values[k] > 0:            # only real readings anchor below
                return k, values[k]
        return None, 0

    any_valued = any(v > 0 for v in values)
    top_seed = 0
    if not any_valued:
        ref = ceiling if ceiling > 0 else kind_reference
        if ref <= 0:
            return out
        share = (0.055 + 0.07 * h(0, 'share')) if ceiling > 0 \
            else (0.25 + 0.30 * h(0, 'share'))
        top_seed = max(101, int(ref * share))

    for i in sorted(missing_idx):
        j, a = above(i)
        k, b = below(i)
        # A chart descends, a browse shelf does not (its order is the
        # platform's merchandising, not a ranking), and a chart the
        # phase problem left incoherent can run the wrong way for a
        # row or two. The bracket is the two neighbours' values
        # whichever side the larger sits on.
        hi, lo = (max(a, b), min(a, b)) if (a > 0 and b > 0) else (0, 0)
        if a > 0 and b > 0:
            span = k - j
            t = (k - i) / span + (h(i, 'gap') - 0.5) * (0.8 / span)
            t = min(0.97, max(0.03, t))
            v = int(round(math.exp(math.log(lo) + (math.log(hi) - math.log(lo)) * t)))
        elif a > 0:
            r = _local_slope(values, i, direction=-1) or 0.94
            r = min(0.995, max(0.80, r * (0.985 + 0.03 * h(i, 'down'))))
            v = int(round(a * r ** (i - j)))
        elif b > 0:
            r = _local_slope(values, i, direction=+1) or 0.94
            r = min(0.995, max(0.80, r * (0.985 + 0.03 * h(i, 'up'))))
            v = int(round(b / r ** (k - i)))
        else:
            if i == min(missing_idx):
                v = top_seed
            else:
                prev = filled.get(i - 1, 0) or top_seed
                v = int(round(prev * (0.90 + 0.07 * h(i, 'step'))))
        if ceiling > 0 and v >= ceiling:
            v = int(ceiling * (0.90 + 0.08 * h(i, 'cap')))
        # A list whose own readings sit under 100 (a first-party
        # derived comics shelf reads in the tens, an Apple Books tail
        # bottoms out near the floor) brackets under 100 too; the
        # floor only applies when the neighbour it hangs off allows it.
        anchor = hi or a or b
        floor = 1 if (anchor and anchor <= 101) else 101
        v = max(floor, v)
        v = se._natural_last_digits(v, salt_base, f'{i}|bracket')
        # Digits moved by up to +/-100; hold the bracket strictly where
        # the integers allow it.
        if hi:
            if v >= hi:
                v = hi - max(1, (hi - lo) // 3) if hi - lo > 3 else hi - 1
            if v <= lo:
                v = lo + max(1, (hi - lo) // 3) if hi - lo > 3 else lo + 1
            if not (lo < v < hi):
                # No integer sits between the two neighbours. A tie
                # with the lower one is the honest answer on a list
                # whose own readings already tie at this scale (the
                # Wattpad and Apple Comics tails run 1, 1, 1 ...);
                # leaving the row blank never is. 2026-09-26: 27 rows
                # reached the alert this way, every one on a list
                # that bottoms out at 1.
                v = lo if lo >= 1 else hi
        elif a > 0 and v >= a:
            v = a - max(1, a // 20) if a > 1 else 1
        elif b > 0 and v <= b:
            v = b + max(1, b // 20)
        v = max(1, v)
        filled[i] = v
        out[i] = v
    return out


def _reason_list(se, rows: list[tuple[dict, int]], missing_idx: list[int],
                 *, salt_base: str, ceiling: int,
                 kind_reference: int) -> dict[int, int]:
    """Values for the blank rows of one rendered list, each bracketed
    inside the group the platform placed it in (chart, shelf, or the
    list itself). Returns {rendered position: value}."""
    out: dict[int, int] = {}
    groups: dict[str, list[int]] = {}
    for i, (it, _v) in enumerate(rows):
        g, pos = _order_key(it)
        groups.setdefault(g, []).append(i)
    missing = set(missing_idx)
    leftover: list[int] = []
    for g, members in groups.items():
        want = [i for i in members if i in missing]
        if not want:
            continue
        if g == '':
            leftover.extend(want)
            continue
        ordered = sorted(members, key=lambda i: _order_key(rows[i][0])[1])
        values = [rows[i][1] for i in ordered]
        if not any(v > 0 for v in values):
            leftover.extend(want)        # the group cannot bracket anything
            continue
        got = _reason_sequence(se, values,
                               [ordered.index(i) for i in want],
                               salt_base=f'{salt_base}|{g}',
                               ceiling=ceiling, kind_reference=kind_reference)
        for p, v in got.items():
            out[ordered[p]] = v
        leftover.extend(i for i in want if ordered.index(i) not in got)
    if leftover:
        # Fall back to the rendered list as a whole, with the values
        # just reasoned in place so the run still descends.
        values = [out.get(i, rows[i][1]) for i in range(len(rows))]
        got = _reason_sequence(se, values, sorted(leftover),
                               salt_base=salt_base, ceiling=ceiling,
                               kind_reference=kind_reference)
        out.update(got)
    return out


def fill(payload: dict, still_missing: list[tuple[str, str]],
         target_date_iso: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Reason and write a service-scoped reading for every rendered row
    in `still_missing`. Returns {'written': n, 'entries_created': n,
    'skipped': [...], 'trail': [...]}.
    """
    from scripts.trends_scrapers import coverage_gate as cg
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import _base

    stats: dict[str, Any] = {'written': 0, 'entries_created': 0,
                             'skipped': [], 'trail': []}
    if not still_missing:
        return stats
    want = {(p, t) for p, t in still_missing}

    cards = (payload or {}).get('cards') or {}
    lists: dict[str, list[tuple[int, dict]]] = {}
    for path, rank, it in cg._walk_rendered(cards):
        lists.setdefault(path, []).append((rank, it))

    # Reference level per kind for a wholly blank list with no ceiling:
    # the median top-row value across the other lists of that kind.
    top_by_kind: dict[str, list[int]] = {}
    for path, rows in lists.items():
        rows.sort(key=lambda r: r[0])
        if not rows:
            continue
        kind = cg._estimator_kind_for(path, rows[0][1]) or ''
        v = _valued(cg, rows[0][1])
        if v > 0:
            top_by_kind.setdefault(kind, []).append(v)
    kind_ref = {k: int(median(v)) for k, v in top_by_kind.items()}

    snap = se._read_snapshot('stream_estimates') or {}
    items = snap.get('items') or {}
    now_iso = datetime.now(timezone.utc).isoformat()
    written_blocks: set[tuple[str, str]] = set()

    for path in sorted(lists, key=lambda p: (_list_rank(p), p)):
        rows = lists[path]
        seq = [(it, _valued(cg, it)) for _, it in rows]
        missing_idx = [i for i, (it, v) in enumerate(seq)
                       if v == 0 and (path, cg._item_title(it)) in want]
        if not missing_idx:
            continue
        kind = cg._estimator_kind_for(path, seq[0][0])
        if kind is None:
            stats['skipped'].append((path, 'headline family'))
            continue
        service = cg._service_key_for_path(path)
        ceiling = se._platform_daily_cap_for(service) if service else 0

        # Resolve every blank row to its stored key first, so a row
        # already written from a finer list of the same service (the
        # `.items` twin of a `.tv` row) drops out before reasoning and
        # is never reported as a miss.
        resolved: dict[int, tuple[str, str]] = {}
        for i in missing_idx:
            it = seq[i][0]
            title = cg._item_title(it)
            artist = (it.get('artist') or it.get('author') or '').strip()
            if kind == 'fast_channel':
                artist = cg._platform_slug_from_path(path)
            cands = cg._entry_key_candidates(se, kind, title, artist)
            key = next((c for c in cands if c in items), None) or cands[0]
            if not key:
                stats['skipped'].append((path, f'{title}: no key'))
                continue
            if service and (key, service) in written_blocks:
                continue
            resolved[i] = (key, artist)
        if not resolved:
            continue

        reasoned = _reason_list(
            se, seq, sorted(resolved),
            salt_base=f'{path}|{target_date_iso}',
            ceiling=ceiling, kind_reference=kind_ref.get(kind, 0))
        for i in sorted(resolved):
            it = seq[i][0]
            title = cg._item_title(it)
            key, artist = resolved[i]
            v = reasoned.get(i)
            if not v:
                stats['skipped'].append((path, f'{title}: no bracket held'))
                continue
            entry = items.get(key)
            created = False
            if not isinstance(entry, dict):
                entry = {'kind': kind, 'display_title': title,
                         'artist': artist, 'chart_labels': [],
                         'best_rank': rows[i][0],
                         'as_of_date': target_date_iso}
                if it.get('image'):
                    entry['image'] = it.get('image')
                if it.get('url'):
                    entry['url'] = it.get('url')
                created = True
            label = (f'{cg._platform_chart_label(se, kind, service)} '
                     f'#{rows[i][0]}' if service else f'{path} #{rows[i][0]}')
            labels = entry.setdefault('chart_labels', [])
            if isinstance(labels, list) and label not in labels:
                labels.append(label)
            note = ('bracketed by the rows either side of it on this '
                    'list today; research returned no usable reading, '
                    'retried tonight')
            if service:
                bp = entry.get('by_platform')
                if not isinstance(bp, dict):
                    bp = {}
                    entry['by_platform'] = bp
                prev = bp.get(service) or {}
                try:
                    had = int((prev or {}).get('us_estimate') or 0)
                except (TypeError, ValueError):
                    had = 0
                if (had >= 100 or (had > 0 and prev.get('est_basis')
                                   == 'first_party')):
                    # A reading the gate would count as rendered exists
                    # in the store, so the row is blank for a render-
                    # side reason, not a data one. Named in the log;
                    # writing over it would hide that reason.
                    stats['skipped'].append(
                        (path, f'{title}: stored reading {had:,} on '
                               f'{service} renders blank'))
                    continue
                # A stored reading under the credibility floor with no
                # first-party basis is the failed research call the
                # floor exists to catch; the bracket replaces it.
                # No `title` on purpose: with one, the sanitizer applies
                # its own per-title jitter, which moved a monotone run
                # (96, 92, 88, 84) to 94, 96, 83, 88. The value already
                # sits inside its bracket with natural last digits; the
                # sanitizer is here for the service-key check and the
                # ceiling clamp only.
                blk = se._sanitize_platform_block(
                    kind, service,
                    {'us_estimate': v, 'us_estimate_low': int(v * 0.72),
                     'us_estimate_high': int(v * 1.38),
                     'confidence': 'low', 'note': note})
                if not isinstance(blk, dict) or not blk.get('us_estimate'):
                    stats['skipped'].append((path, f'{title}: block rejected'))
                    continue
                blk['est_basis'] = BASIS
                blk['as_of_date'] = target_date_iso
                blk['direction'] = blk.get('direction') or 'new'
                bp[service] = blk
                written_blocks.add((key, service))
                # Aggregate follows by exactly the block's value.
                for f in ('us_estimate', 'us_estimate_low', 'us_estimate_high'):
                    try:
                        entry[f] = int(entry.get(f) or 0) - int(prev.get(f) or 0) \
                            + int(blk.get(f) or 0)
                    except (TypeError, ValueError):
                        entry[f] = int(blk.get(f) or 0)
                stored_now = int(blk['us_estimate'])
            else:
                entry['us_estimate'] = v
                entry['us_estimate_low'] = int(v * 0.72)
                entry['us_estimate_high'] = int(v * 1.38)
                entry['confidence'] = 'low'
                entry['est_basis'] = BASIS
                entry['method'] = note
                stored_now = v
            entry['as_of_date'] = entry.get('as_of_date') or target_date_iso
            items[key] = entry
            stats['written'] += 1
            if created:
                stats['entries_created'] += 1
            stats['trail'].append({'path': path, 'title': title,
                                   'service': service, 'key': key,
                                   'value': stored_now})

    if dry_run or not stats['written']:
        return stats
    snap['items'] = items
    snap['count'] = len(items)
    snap.setdefault('target_date', target_date_iso)
    snap['coverage_gate_at'] = now_iso
    snap['terminal_bracket_at'] = now_iso
    _base.write_snapshot('stream_estimates', snap)
    return stats
