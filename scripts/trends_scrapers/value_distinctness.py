"""Every item's readings are distinct across its own trailing 60 days.

Jenna 2026-09-15 (verbatim): *"it should not put the sanme exact number
two days in a row, should jitter it or make it go up down depending on
day of the week or whatever so that there are nevert the exact same
number for something repeating within the trailing 60 days."*

WHY THE OLD GUARANTEE WAS NOT ENOUGH
------------------------------------
Two guards already existed and both only ever looked one day back:
`_apply_inherited_daily_variation` walks an item that would otherwise
repeat YESTERDAY's integer, and `_enforce_min_daily_movement` nudges a
value that lands within 0.2% of YESTERDAY. Anything further apart than
one day was free to land on an old reading, and it did: 13,000 of the
18,314 items with two or more readings in the 60 days ending
2026-09-15 repeated a value, 22,970 duplicate readings in total.

Two separate causes produced those:

1. A repair pass. The 2026-09-15 snapshot records in its own
   `coverage_repair` block that a truncated read broke the merge and
   12,467 keys were restored from 2026-09-14. They were restored as
   integers, so the day walk never saw them, and 12,559 readings that
   day are their predecessor verbatim. That single day is where 3,334
   of the 3,444 repeating items above 100K come from.

2. The shape of the daily value itself. A day's value is
   `level x organic_factor(item, day)`. The factor is a weekly shape,
   a few percent of trend drift, events, and bounded noise: it is a
   STATIONARY oscillation, so the value orbits a fixed level instead of
   wandering away from it. Its 60 readings are therefore 60 draws from
   one narrow band, concentrated near the middle of that band, and
   revisiting a value is routine rather than improbable. That is why a
   uniqueness check bolted onto the generator would fight it forever:
   distinctness has to be a constraint the value assignment knows
   about, not a property it is hoped to have.

WHAT THIS MODULE DOES
---------------------
It gives the value assignment a memory. Each item carries a ledger of
the readings it already holds in the trailing 60 days. A day's value is
accepted when it is new to that ledger, which is the overwhelming
majority of days and makes the pass a no-op. When it collides, the
value moves along the item's OWN curve: the direction the item's
reasoned rhythm is already travelling that day, a step sized by that
item's own volatility, searched outward until the reading is new. The
step is a fraction of a percent, so the level does not move and the
series still reads as the same series.

Narrow bands are the honest exception. A Wattpad story whose true range
is 1 to 18 readers cannot hold 60 distinct integers, and inflating it to
manufacture uniqueness would falsify it. Those items instead maximise
SPACING: the reading picked is the one whose previous use is furthest
away in time, the adjacent-day rule stays absolute, and the row is
marked so the exception is identifiable rather than silent.

Guarantees on every resolved value:
  * inside the item's own observed band, never above the highest
    reading it already holds, so a per-service cap that held before
    still holds,
  * outside the dead-chip zone around the previous day,
  * natural last digits (`_natural_last_digits`, zeros at their natural
    rate) and never a placeholder literal,
  * deterministic, and idempotent: re-running accepts what is already
    distinct and changes nothing.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
from datetime import date, timedelta
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

WINDOW_DAYS = 60

# An item can only be held to 60 distinct readings when its honest band
# has room for 60 integers. The band a daily rhythm can carry a level
# through is about [0.80x, 1.25x], so the smallest level with room is
# around 134. Below that the item is band limited and gets the spacing
# rule instead.
BAND_LO = 0.80
BAND_HI = 1.25
MIN_DISTINCT_CAPACITY = WINDOW_DAYS

# The dashboard renders day-over-day at one decimal, so a move under
# 0.2% shows a dead chip. `_enforce_min_daily_movement` already clears
# that; a collision step must not push a value back inside it.
DEAD_ZONE = 0.002

# Collision steps stay well under the smallest move a reader could
# notice, so levels are untouched.
_STEP_MIN = 0.0006
_STEP_SPAN = 0.0022
_MAX_ATTEMPTS = 96

# Ledger of the readings each item already holds.
S3_BUCKET = 'dashboard-inputs'
S3_HISTORY_KEY = 'trends_iq_snapshots/system/value_history.json.gz'
S3_DATED = 'trends_iq_snapshots/{date}/stream_estimates.json'

# Stamped on a row whose band cannot hold 60 distinct readings, so the
# exception can be counted instead of hiding inside the corpus.
BAND_LIMITED_FIELD = 'value_band_limited'


def _h01(seed: str) -> float:
    h = hashlib.md5(seed.encode('utf-8')).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _lazy():
    """Import the estimator helpers late.

    `stream_estimates` calls into this module and this module needs its
    digit helper and the rhythm factor, so a module-level import would
    close the cycle. Everything is initialized by the time a value is
    resolved.
    """
    try:
        from .stream_estimates import _natural_last_digits
    except ImportError:
        from scripts.trends_scrapers.stream_estimates import \
            _natural_last_digits          # type: ignore
    try:
        from .apply_daily_variation_backfill import _organic_factor
    except ImportError:
        from scripts.trends_scrapers.apply_daily_variation_backfill \
            import _organic_factor        # type: ignore
    return _natural_last_digits, _organic_factor


def item_key_for(it: dict) -> str:
    """The rhythm personality key. Same composition the day walk uses,
    so a collision step rides the same curve the walk does."""
    kind = str((it or {}).get('kind') or '').strip().lower()
    display = ((it or {}).get('display_title') or '').strip()
    artist = ((it or {}).get('artist') or '').strip()
    return f'{kind}|{display}|{artist}'


# ---------------------------------------------------------------------------
# Band capacity
# ---------------------------------------------------------------------------
def band_bounds(values: Iterable[int]) -> tuple[int, int]:
    """The honest band around what this item has actually read."""
    vals = [int(v) for v in values if v and int(v) > 0]
    if not vals:
        return (1, 1)
    lo = max(1, int(min(vals) * BAND_LO))
    hi = max(lo, int(round(max(vals) * BAND_HI)))
    return (lo, hi)


def band_capacity(values: Iterable[int]) -> int:
    lo, hi = band_bounds(values)
    return hi - lo + 1


def is_band_limited(values: Iterable[int]) -> bool:
    """True when the item cannot hold 60 distinct readings without
    being moved outside the range it honestly occupies."""
    return band_capacity(values) < MIN_DISTINCT_CAPACITY


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def _acceptable(cand: int, value: int, ceiling: int,
                prev_value: Optional[int]) -> bool:
    if cand <= 0 or cand > ceiling:
        return False
    if prev_value and prev_value > 0:
        if cand == prev_value:
            return False
        if abs(cand / float(prev_value) - 1.0) < DEAD_ZONE:
            return False
    return True


def resolve_value(value: int,
                  item_key: str,
                  target_date: date,
                  history: dict[str, int],
                  prev_value: Optional[int] = None,
                  profile: Optional[dict] = None,
                  ceiling: Optional[int] = None,
                  ) -> tuple[int, str]:
    """Return `(value, disposition)` for one item-day.

    `history` maps ISO date to the reading this item holds on that day,
    covering the rest of the trailing 60 days. Dispositions are
    `'ok'` (already distinct, nothing moved), `'moved'` (walked along
    the item's curve until the reading was new), `'spaced'` (band
    limited, placed as far from its last use as the band allows).
    """
    try:
        value = int(value)
    except (TypeError, ValueError):
        return (value, 'ok')
    if value <= 0:
        return (value, 'ok')

    taken = {int(v) for v in history.values() if v and int(v) > 0}
    if value not in taken:
        return (value, 'ok')

    natural_digits, organic_factor = _lazy()
    all_values = list(taken) + [value]
    ceiling = int(ceiling) if ceiling else max(all_values)

    if is_band_limited(all_values):
        return (_space_out(value, item_key, target_date, history,
                           prev_value, ceiling), 'spaced')

    # Direction: keep travelling the way this item's own rhythm is
    # travelling today, so the correction reads as the curve rather
    # than as a nudge.
    try:
        f_t = organic_factor(item_key, target_date, profile)
        f_p = organic_factor(item_key, target_date - timedelta(days=1),
                             profile)
        lead = 1 if f_t >= f_p else -1
    except Exception:
        lead = 1 if _h01(f'{item_key}|dzlead') < 0.5 else -1

    iso = target_date.isoformat()
    step = value * (_STEP_MIN + _h01(f'{item_key}|dzstep') * _STEP_SPAN)
    unit = max(1, int(round(step)))

    for k in range(1, _MAX_ATTEMPTS + 1):
        # Search outward, the item's own direction first on each ring.
        ring, side = (k + 1) // 2, (k % 2)
        sign = lead if side == 1 else -lead
        raw = value + sign * ring * unit
        if raw <= 0:
            continue
        cand = natural_digits(raw, item_key, f'{iso}|distinct{k}')
        if cand in taken or cand == value:
            continue
        if _acceptable(cand, value, ceiling, prev_value):
            return (cand, 'moved')

    # Nothing on the curve landed clear. Take the nearest free integer
    # that still respects the ceiling and the dead zone.
    for delta in range(1, 20_000):
        for cand in (value - delta, value + delta):
            if cand in taken or cand == value:
                continue
            if _acceptable(cand, value, ceiling, prev_value):
                return (cand, 'moved')
    return (_space_out(value, item_key, target_date, history,
                       prev_value, ceiling), 'spaced')


def _space_out(value: int, item_key: str, target_date: date,
               history: dict[str, int], prev_value: Optional[int],
               ceiling: int) -> int:
    """Band-limited placement: of the readings this item's honest band
    allows, take the one whose previous use is furthest away in time.

    The adjacent-day rule stays absolute. When every reading in the band
    is in use, the furthest-away one is still the right answer, which is
    the whole reason this path exists.
    """
    lo, hi = band_bounds(list(history.values()) + [value])
    hi = min(hi, ceiling)
    if hi < lo:
        lo, hi = 1, max(1, ceiling)
    # Cap the candidate scan: a band limited item is small by
    # definition, and a wide band never reaches this path.
    if hi - lo > 4_000:
        hi = lo + 4_000

    last_use: dict[int, int] = {}
    for iso, v in history.items():
        try:
            gap = abs((date.fromisoformat(iso) - target_date).days)
            v = int(v)
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        last_use[v] = min(last_use.get(v, 10 ** 6), gap)

    best, best_score = None, None
    for cand in range(lo, hi + 1):
        if prev_value and cand == int(prev_value):
            continue          # adjacent-day rule is absolute
        gap = last_use.get(cand, 10 ** 6)
        # Deterministic tie-break so two band-limited items with the
        # same history do not land on the same reading.
        score = (gap, _h01(f'{item_key}|{target_date.isoformat()}|{cand}'))
        if best_score is None or score > best_score:
            best, best_score = cand, score
    if best is None:
        best = max(1, int(value) + 1)
    return int(best)


# ---------------------------------------------------------------------------
# Snapshot-level pass
# ---------------------------------------------------------------------------
def enforce_snapshot(items: dict[str, dict],
                     history: dict[str, dict[str, int]],
                     target_iso: str,
                     prev_items: Optional[dict[str, dict]] = None,
                     profiles: Optional[dict[str, dict]] = None,
                     ) -> dict[str, int]:
    """Make every reading in `items` distinct from what that item holds
    on the other days of its trailing 60. Mutates `items` in place.

    `history` maps item key -> {iso: reading} and must NOT include
    `target_iso` (the caller owns today's column). Returns a counter.
    """
    stats = {'checked': 0, 'moved': 0, 'spaced': 0, 'band_limited': 0}
    if not isinstance(items, dict):
        return stats
    try:
        tgt = date.fromisoformat(target_iso)
    except (TypeError, ValueError):
        return stats

    try:
        from .stream_estimates import _rescale_estimate_blocks
    except ImportError:
        from scripts.trends_scrapers.stream_estimates import \
            _rescale_estimate_blocks      # type: ignore

    profiles = profiles or {}
    prev_items = prev_items or {}

    for key, it in items.items():
        if not isinstance(it, dict):
            continue
        try:
            cur = int(it.get('us_estimate') or 0)
        except (TypeError, ValueError):
            continue
        if cur <= 0:
            continue
        past = {iso: v for iso, v in (history.get(key) or {}).items()
                if iso != target_iso}
        stats['checked'] += 1

        limited = is_band_limited(list(past.values()) + [cur])
        if limited:
            stats['band_limited'] += 1
            it[BAND_LIMITED_FIELD] = True
        elif it.get(BAND_LIMITED_FIELD):
            it.pop(BAND_LIMITED_FIELD, None)
        if not past:
            continue

        prev = prev_items.get(key) or {}
        try:
            prev_mid = int(prev.get('us_estimate') or 0) or None
        except (TypeError, ValueError):
            prev_mid = None

        new_mid, how = resolve_value(
            cur, item_key_for(it), tgt, past,
            prev_value=prev_mid, profile=profiles.get(key))
        if new_mid == cur or new_mid <= 0:
            continue
        _rescale_estimate_blocks(it, cur, new_mid, key,
                                 f'{target_iso}|distinct')
        stats['moved' if how == 'moved' else 'spaced'] += 1
    return stats


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------
def _s3():
    import boto3
    return boto3.client('s3')


def window_dates(end: date, days: int = WINDOW_DAYS) -> list[str]:
    return [(end - timedelta(days=i)).isoformat()
            for i in range(days)][::-1]


def load_history() -> dict[str, Any]:
    """Read the ledger. A missing or unreadable ledger degrades to an
    empty one: the pass then has nothing to compare against and leaves
    the day untouched, which is the safe direction."""
    try:
        body = _s3().get_object(Bucket=S3_BUCKET,
                                Key=S3_HISTORY_KEY)['Body'].read()
        return json.loads(gzip.decompress(body).decode('utf-8'))
    except Exception:
        logger.info('value_distinctness: no ledger yet')
        return {}


def save_history(ledger: dict[str, Any]) -> None:
    body = gzip.compress(
        json.dumps(ledger, ensure_ascii=False).encode('utf-8'))
    _s3().put_object(Bucket=S3_BUCKET, Key=S3_HISTORY_KEY, Body=body,
                     ContentType='application/json',
                     ContentEncoding='gzip')
    logger.info('value_distinctness: ledger -> s3://%s/%s (%d bytes, '
                '%d items)', S3_BUCKET, S3_HISTORY_KEY, len(body),
                len(ledger.get('items') or {}))


def ledger_to_per_item(ledger: dict[str, Any],
                       end: Optional[date] = None,
                       ) -> dict[str, dict[str, int]]:
    """{item key: {iso: reading}} for the trailing window."""
    dates = list(ledger.get('dates') or [])
    if not dates:
        return {}
    keep = set(dates)
    if end is not None:
        keep = set(window_dates(end))
    out: dict[str, dict[str, int]] = {}
    for key, col in (ledger.get('items') or {}).items():
        if not isinstance(col, list):
            continue
        per: dict[str, int] = {}
        for iso, v in zip(dates, col):
            if v and iso in keep:
                per[iso] = int(v)
        if per:
            out[key] = per
    return out


def per_item_to_ledger(per_item: dict[str, dict[str, int]],
                       dates: list[str]) -> dict[str, Any]:
    from datetime import datetime, timezone
    idx = {d: i for i, d in enumerate(dates)}
    items: dict[str, list] = {}
    for key, per in per_item.items():
        col: list = [None] * len(dates)
        hit = False
        for iso, v in per.items():
            i = idx.get(iso)
            if i is not None and v:
                col[i] = int(v)
                hit = True
        if hit:
            items[key] = col
    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'window_days': len(dates),
        'dates': dates,
        'items': items,
    }


def rebuild_from_snapshots(end: date, days: int = WINDOW_DAYS,
                           workers: int = 10) -> dict[str, Any]:
    """Read the dated snapshots and build the ledger from scratch.
    Used for the first build and whenever the ledger is lost."""
    from concurrent.futures import ThreadPoolExecutor
    dates = window_dates(end, days)

    def one(d: str) -> tuple[str, dict[str, int]]:
        try:
            body = _s3().get_object(
                Bucket=S3_BUCKET, Key=S3_DATED.format(date=d),
            )['Body'].read()
        except Exception:
            return d, {}
        snap = json.loads(body)
        out = {}
        for k, it in (snap.get('items') or {}).items():
            if not isinstance(it, dict):
                continue
            try:
                v = int(it.get('us_estimate') or 0)
            except (TypeError, ValueError):
                continue
            if v > 0:
                out[k] = v
        return d, out

    per_item: dict[str, dict[str, int]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for d, col in ex.map(one, dates):
            for k, v in col.items():
                per_item.setdefault(k, {})[d] = v
    return per_item_to_ledger(per_item, dates)


def update_ledger(ledger: dict[str, Any], target_iso: str,
                  items: dict[str, dict], end: Optional[date] = None,
                  ) -> dict[str, Any]:
    """Fold one day's readings into the ledger and trim to the window."""
    try:
        end = end or date.fromisoformat(target_iso)
    except (TypeError, ValueError):
        return ledger
    per_item = ledger_to_per_item(ledger)
    for key, it in (items or {}).items():
        if not isinstance(it, dict):
            continue
        try:
            v = int(it.get('us_estimate') or 0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            per_item.setdefault(key, {})[target_iso] = v
    dates = window_dates(end)
    keep = set(dates)
    trimmed = {k: {i: v for i, v in per.items() if i in keep}
               for k, per in per_item.items()}
    trimmed = {k: v for k, v in trimmed.items() if v}
    return per_item_to_ledger(trimmed, dates)


# ---------------------------------------------------------------------------
# The backstop
# ---------------------------------------------------------------------------
def enforce_on_payload(payload: dict, snapshot_iso: str) -> dict[str, int]:
    """Apply the rule to a stream-estimates payload on its way to S3 and
    fold the result into the ledger.

    Wired at the write boundary so no writer can publish a snapshot that
    repeats a reading, including a one-off repair pass that composes its
    output by hand. Never raises: an enforcement that cannot run leaves
    the payload exactly as it arrived.
    """
    stats = {'checked': 0, 'moved': 0, 'spaced': 0, 'band_limited': 0}
    items = (payload or {}).get('items')
    if not isinstance(items, dict) or not items:
        return stats
    try:
        end = date.fromisoformat(snapshot_iso)
    except (TypeError, ValueError):
        return stats

    ledger = load_history()
    if not (ledger.get('items') or {}):
        ledger = rebuild_from_snapshots(end - timedelta(days=1))
    history = ledger_to_per_item(ledger)

    profiles = {}
    try:
        from .stream_estimates import _load_rhythm_profiles
        profiles = _load_rhythm_profiles() or {}
    except Exception:
        pass

    prev_iso = (end - timedelta(days=1)).isoformat()
    prev_items = {k: {'us_estimate': per[prev_iso]}
                  for k, per in history.items() if prev_iso in per}

    stats = enforce_snapshot(items, history, snapshot_iso,
                             prev_items=prev_items, profiles=profiles)
    save_history(update_ledger(ledger, snapshot_iso, items, end=end))
    return stats
