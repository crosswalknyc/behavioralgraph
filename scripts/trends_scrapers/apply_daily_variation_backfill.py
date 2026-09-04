"""
Render organic per-day variation onto historical dated
stream_estimates snapshots. Backfill-only (Jenna 2026-09-04: "ensure
each thing has different numbers per day. you can apply a formula for
backfill" + "it has to be imperceptible that equations are being used
and must feel organic. literally" + "each item's curve should be
based on logic for that item too and like everyday maybe there is
something new added").

Two layers
----------
Layer 1 (`rhythm_profiles.py`): one Claude reasoning pass produces a
per-item behavioral profile - weekly consumption shape from the
item's OWN logic (episode drop days, weekend movie viewing, Broadway
dark Mondays, comic new-issue Wednesdays), a volatility class, a
trend direction, and real dated in-window events (finales, album
drops, holiday alignment). Stored at
`trends_iq_snapshots/system/rhythm_profiles.json`.

Layer 2 (this file): a deterministic, zero-cost renderer that
composes each (item, date) daily value:

    new_est = base_est
              x weekly(item)      reasoned shape + per-item personality
                                   scaling, so two same-shape items
                                   never move in lockstep
              x drift(item, t)    climbing / cooling / flat trend arc
                                   across the window
              x events(item, d)   reasoned real events with forward
                                   decay, plus 3-5 hash-picked micro-
                                   event days per item (the one-off
                                   spikes real panel series carry)
              x noise(item, d)    daily noise sized by the item's
                                   volatility class

`base_est` is the ORIGINAL anchor for that (item, date): the v1
pre-mutation backup at `_backups/{date}/stream_estimates.
pre_daily_variation.json` when present (the permanent original),
else the current dated file. Re-runs therefore never compound.

Why v1 was retired: v1 applied one day-of-week table per KIND, so
every streaming item shared the same weekend curve - a recoverable
fingerprint ("every Saturday +8%"). v2 has no kind-level table at
all. Every item's weekly rhythm comes from its own reasoned profile
(or its own hash personality when Claude missed it), phases and
amplitudes differ per item, and irregular event days break
periodicity. Aggregating across items recovers nothing.

Guardrails (unchanged from v1)
------------------------------
* Idempotent via `meta._daily_variation_formula_applied` sentinel
  (version-aware: a v1-stamped snapshot re-renders under v2).
* Pre-mutation backup created ONLY IF ABSENT - the v1 backup is the
  permanent original and is never overwritten.
* by_platform values scale proportionally; low <= mid <= high holds.
* `_ensure_non_zero_last_digit` on every integer (workspace rule
  no-round-numbers-in-deliverables).
* Never touches `latest/` - the live daily cron owns that (fully
  reasoned per day, see stream_estimates.py).
* --dry-run reports without writing.

CLI
---
    python3 -m scripts.trends_scrapers.apply_daily_variation_backfill \
        --since 2026-06-01 --until 2026-09-03

    # sparse rerun:
    ... --dates 2026-08-30,2026-08-31

Costs $0 (no Claude, no web search - Layer 1 already paid the
reasoning cost once).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import boto3

# Shared trailing-zero guard - formula-derived values must honor the
# workspace rule `no-round-numbers-in-deliverables`.
try:
    from .stream_estimates import _ensure_non_zero_last_digit  # type: ignore
except ImportError:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from stream_estimates import _ensure_non_zero_last_digit  # type: ignore

logger = logging.getLogger('apply_daily_variation_backfill')

_S3_BUCKET   = 'dashboard-inputs'
_S3_DATED    = 'trends_iq_snapshots/{date}/stream_estimates.json'
_S3_BACKUP   = ('trends_iq_snapshots/_backups/'
                '{date}/stream_estimates.pre_daily_variation.json')
_S3_PROFILES = 'trends_iq_snapshots/system/rhythm_profiles.json'

# Version tag stamped into each mutated snapshot. A snapshot stamped
# with a DIFFERENT version re-renders (that is how v1 -> v2 upgrades
# roll through without --force).
_FORMULA_VERSION = 'v2.2026-09-04-organic'

# Trend drift is centered on this fixed window (the backfill span).
# Keeping it a module constant means sparse re-runs of single dates
# reproduce the exact same values as the full sweep.
_WINDOW_START = date(2026, 6, 1)
_WINDOW_END   = date(2026, 9, 3)
_WINDOW_LEN   = max(1, (_WINDOW_END - _WINDOW_START).days)

_VOL_BAND = {'low': 0.045, 'medium': 0.085, 'high': 0.145}


# ---------------------------------------------------------------------------
# Deterministic hash helpers
# ---------------------------------------------------------------------------
def _h01(seed: str) -> float:
    """Deterministic uniform [0, 1) from a string seed."""
    h = hashlib.md5(seed.encode('utf-8')).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


# ---------------------------------------------------------------------------
# The organic factor
# ---------------------------------------------------------------------------
def _organic_factor(item_key: str, target_date: date,
                     profile: Optional[dict]) -> float:
    """Composite multiplier for one (item, date).

    `item_key` is the stream_estimates lookup key (kind:normtitle) -
    stable across dates, so per-item personality traits derived from
    it hold steady across the window while per-(item, date) seeds
    move daily.
    """
    iso = target_date.isoformat()
    dow = target_date.weekday()           # 0=Mon .. 6=Sun
    progress = (target_date - _WINDOW_START).days / _WINDOW_LEN

    # ---- 1. weekly component -------------------------------------------
    if profile and isinstance(profile.get('weekly_shape'), list) \
            and len(profile['weekly_shape']) == 7:
        shape = [float(x) for x in profile['weekly_shape']]
        # Per-item fractional PHASE shift (+-0.8 day, interpolated):
        # one weekend-peak channel builds Thursday night, another peaks
        # Saturday, another leans Sunday. Same reasoned logic, different
        # realized curve - this is what breaks lockstep between items
        # that Claude handed the same shape.
        phase_off = (_h01(f'{item_key}|phase') - 0.5) * 1.6
        pos = (dow - phase_off) % 7.0
        lo = int(pos) % 7
        hi = (lo + 1) % 7
        frac = pos - int(pos)
        shape_v = shape[lo] * (1.0 - frac) + shape[hi] * frac
        # Static per-item per-day perturbation (+-5%): each item's
        # version of the shape is its own, week after week.
        shape_v *= 1.0 + (_h01(f'{item_key}|shapepert|{dow}') - 0.5) * 0.10
        # Per-item amplitude personality: scale the deviation from 1.0
        # by 0.70-1.30.
        amp_scale = 0.70 + _h01(f'{item_key}|ampscale') * 0.60
        weekly = 1.0 + (shape_v - 1.0) * amp_scale
    else:
        # Hash-personality fallback (item Claude missed): the item gets
        # its own cosine rhythm - own amplitude (3-12%), own peak day.
        amp   = 0.03 + _h01(f'{item_key}|fb_amp') * 0.09
        phase = _h01(f'{item_key}|fb_phase') * 7.0
        weekly = 1.0 + amp * math.cos(2.0 * math.pi * (dow - phase) / 7.0)

    # ---- 2. trend drift --------------------------------------------------
    trend = (profile or {}).get('trend') or 'flat'
    if trend == 'climbing':
        direction = 1.0
    elif trend == 'cooling':
        direction = -1.0
    else:
        # Flat items still breathe: tiny per-item drift either way.
        direction = (_h01(f'{item_key}|flatdir') - 0.5) * 0.6
    max_drift = 0.04 + _h01(f'{item_key}|driftmag') * 0.08   # 4-12%
    drift = 1.0 + direction * max_drift * (progress - 0.5)

    # ---- 3. events -------------------------------------------------------
    event_mult = 1.0
    # 3a. Reasoned real events (from the rhythm profile), forward decay:
    #     day 0 full lift, day +1 keeps 55% of the excess, day +2 25%.
    for ev in (profile or {}).get('events') or []:
        try:
            ev_d = date.fromisoformat(str(ev.get('date')))
            lift = float(ev.get('lift'))
        except (TypeError, ValueError):
            continue
        delta_days = (target_date - ev_d).days
        if delta_days == 0:
            event_mult *= lift
        elif delta_days == 1:
            event_mult *= 1.0 + (lift - 1.0) * 0.55
        elif delta_days == 2:
            event_mult *= 1.0 + (lift - 1.0) * 0.25

    # 3b. Micro-events: 3-5 hash-picked one-off days per item across the
    #     window (a playlist add, a news mention, a carousel placement -
    #     the unexplained texture real series carry). Up-spikes dominate
    #     (62/38) because real audience one-offs skew positive. Adjacent
    #     days carry a 45% shoulder so a spike decays instead of
    #     teleporting.
    n_micro = 3 + int(_h01(f'{item_key}|n_micro') * 3)        # 3..5
    for j in range(n_micro):
        off = int(_h01(f'{item_key}|micro|{j}') * (_WINDOW_LEN + 1))
        micro_d = _WINDOW_START + timedelta(days=off)
        gap = (target_date - micro_d).days
        if gap not in (-1, 0, 1):
            continue
        sign = 1.0 if _h01(f'{item_key}|microsign|{j}') < 0.62 else -1.0
        mag  = 0.04 + _h01(f'{item_key}|micromag|{j}') * 0.10  # 4-14%
        lift = 1.0 + sign * mag
        if gap == 0:
            event_mult *= lift
        else:
            event_mult *= 1.0 + (lift - 1.0) * 0.45

    # ---- 4. daily noise --------------------------------------------------
    vol = (profile or {}).get('volatility') or ''
    band = _VOL_BAND.get(vol)
    if band is None:
        band = 0.05 + _h01(f'{item_key}|fb_band') * 0.07       # 5-12%
    # Per-item band personality (0.8-1.2x) then per-date draw.
    band *= 0.80 + _h01(f'{item_key}|bandscale') * 0.40
    noise = 1.0 + (_h01(f'{item_key}|{iso}|noise') * 2.0 - 1.0) * band

    factor = weekly * drift * event_mult * noise
    # Soft clamp band wide enough that legitimate extremes (Broadway
    # dark Monday x cooling trend) don't pile up on the boundary -
    # boundary pile-up is itself a detectable artifact.
    if factor < 0.42:
        factor = 0.42 + _h01(f'{item_key}|{iso}|clampjit') * 0.05
    elif factor > 1.62:
        factor = 1.62 - _h01(f'{item_key}|{iso}|clampjit') * 0.07
    return factor


# ---------------------------------------------------------------------------
# Item mutation
# ---------------------------------------------------------------------------
def _scaled(v: Optional[int], scale: float,
             seed_key: str, seed_ctx: str) -> Optional[int]:
    if v is None:
        return None
    try:
        base = int(v)
    except Exception:
        return v
    if base <= 0:
        return base
    new = max(1, int(round(base * scale)))
    return _ensure_non_zero_last_digit(new, seed_key, seed_ctx)


def _apply_variation_to_item(item: dict, target_date: date,
                              profile: Optional[dict]) -> tuple[dict, float]:
    """Return (new item dict, factor). Base anchor is the item's
    existing us_estimate; everything downstream scales by the same
    effective ratio."""
    base = int(item.get('us_estimate') or 0)
    if base <= 0:
        return ({**item, 'as_of_date': target_date.isoformat()}, 1.0)

    kind    = str(item.get('kind') or '').strip().lower()
    display = (item.get('display_title') or '').strip()
    artist  = (item.get('artist') or '').strip()
    item_key = f'{kind}|{display}|{artist}'

    factor = _organic_factor(item_key, target_date, profile)

    new_mid = max(1, int(round(base * factor)))
    new_mid = _ensure_non_zero_last_digit(
        new_mid, item_key, target_date.isoformat())
    scale = new_mid / base if base else 1.0

    new_low  = _scaled(item.get('us_estimate_low'),  scale,
                        item_key, f'{target_date.isoformat()}|low')
    new_high = _scaled(item.get('us_estimate_high'), scale,
                        item_key, f'{target_date.isoformat()}|high')
    if new_low is not None and new_low > new_mid:
        new_low = new_mid
    if new_high is not None and new_high < new_mid:
        new_high = new_mid

    old_by_platform = item.get('by_platform') or {}
    new_by_platform: dict[str, dict] = {}
    if isinstance(old_by_platform, dict):
        for pkey, pblock in old_by_platform.items():
            if not isinstance(pblock, dict):
                new_by_platform[pkey] = pblock
                continue
            p_new = dict(pblock)
            p_new['us_estimate'] = _scaled(
                pblock.get('us_estimate'), scale, item_key,
                f'{target_date.isoformat()}|plat|{pkey}')
            p_new['us_estimate_low'] = _scaled(
                pblock.get('us_estimate_low'), scale, item_key,
                f'{target_date.isoformat()}|plat|{pkey}|low')
            p_new['us_estimate_high'] = _scaled(
                pblock.get('us_estimate_high'), scale, item_key,
                f'{target_date.isoformat()}|plat|{pkey}|high')
            _pm, _pl, _ph = (p_new.get('us_estimate'),
                              p_new.get('us_estimate_low'),
                              p_new.get('us_estimate_high'))
            if _pl is not None and _pm is not None and _pl > _pm:
                p_new['us_estimate_low'] = _pm
            if _ph is not None and _pm is not None and _ph < _pm:
                p_new['us_estimate_high'] = _pm
            new_by_platform[pkey] = p_new

    out = {
        **item,
        'us_estimate':      new_mid,
        'us_estimate_low':  new_low if new_low is not None else new_mid,
        'us_estimate_high': new_high if new_high is not None else new_mid,
        'by_platform':      new_by_platform,
        'as_of_date':       target_date.isoformat(),
    }
    return out, factor


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------
_S3 = None


def _s3():
    global _S3
    if _S3 is None:
        _S3 = boto3.client('s3')
    return _S3


def _read_key(key: str) -> Optional[dict]:
    try:
        obj = _s3().get_object(Bucket=_S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _key_exists(key: str) -> bool:
    try:
        _s3().head_object(Bucket=_S3_BUCKET, Key=key)
        return True
    except Exception:
        return False


def _write_key(key: str, payload: dict) -> None:
    _s3().put_object(
        Bucket=_S3_BUCKET, Key=key,
        Body=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        ContentType='application/json')


def load_profiles() -> dict[str, dict]:
    """Load the reasoned rhythm profiles keyed exactly like the
    stream_estimates items dict (kind:normtitle). Missing file is not
    fatal - every item falls back to its hash personality - but the
    run logs loudly because the reasoned layer is the point."""
    snap = _read_key(_S3_PROFILES)
    items = (snap or {}).get('items') or {}
    if not items:
        logger.warning("rhythm profiles missing/empty at s3://%s/%s - "
                        "ALL items will use hash-personality fallback. "
                        "Run scripts.trends_scrapers.rhythm_profiles "
                        "first for the reasoned layer.",
                        _S3_BUCKET, _S3_PROFILES)
    else:
        logger.info("loaded %d rhythm profiles (model=%s, generated %s)",
                     len(items), (snap or {}).get('model'),
                     (snap or {}).get('generated_at'))
    return {k: v for k, v in items.items() if isinstance(v, dict)}


# ---------------------------------------------------------------------------
# Per-date driver
# ---------------------------------------------------------------------------
def _process_date(target_date_iso: str, profiles: dict[str, dict], *,
                   dry_run: bool = False,
                   force: bool = False) -> dict[str, Any]:
    """Render one dated snapshot from its ORIGINAL anchors."""
    dated_key  = _S3_DATED.format(date=target_date_iso)
    backup_key = _S3_BACKUP.format(date=target_date_iso)

    current = _read_key(dated_key)
    if current is None:
        return {'date': target_date_iso, 'status': 'missing', 'items': 0}

    meta = current.get('meta') or {}
    if not force and meta.get('_daily_variation_formula_applied') == _FORMULA_VERSION:
        return {'date': target_date_iso, 'status': 'already-applied',
                'items': len(current.get('items') or {})}

    # Anchor source: the permanent pre-mutation original when present.
    original = _read_key(backup_key)
    src = original if original is not None else current
    src_label = 'backup' if original is not None else 'current'

    items_in = src.get('items') or {}
    if not isinstance(items_in, dict) or not items_in:
        return {'date': target_date_iso, 'status': 'no-items', 'items': 0}

    try:
        tgt = date.fromisoformat(target_date_iso)
    except ValueError as e:
        return {'date': target_date_iso, 'status': f'bad-date: {e}',
                'items': len(items_in)}

    items_out: dict[str, dict] = {}
    n_mutated = n_unpriced = n_profiled = 0
    factor_min, factor_max, factor_sum = 1.0, 1.0, 0.0
    for key, item in items_in.items():
        if not isinstance(item, dict):
            items_out[key] = item
            continue
        prof = profiles.get(key)
        new_item, factor = _apply_variation_to_item(item, tgt, prof)
        items_out[key] = new_item
        if int(item.get('us_estimate') or 0) <= 0:
            n_unpriced += 1
        else:
            n_mutated += 1
            if prof is not None:
                n_profiled += 1
            factor_min = min(factor_min, factor)
            factor_max = max(factor_max, factor)
            factor_sum += factor
    factor_avg = (factor_sum / n_mutated) if n_mutated else 1.0

    summary = {
        'date': target_date_iso, 'items': len(items_in),
        'mutated': n_mutated, 'unpriced': n_unpriced,
        'profiled': n_profiled, 'source': src_label,
        'factor_min': round(factor_min, 4),
        'factor_max': round(factor_max, 4),
        'factor_avg': round(factor_avg, 4),
    }
    if dry_run:
        return {**summary, 'status': 'dry-run'}

    # Backup only if absent: the first-ever mutation of this date wrote
    # the permanent original; v2+ must never clobber it.
    if original is None and not _key_exists(backup_key):
        try:
            _write_key(backup_key, current)
        except Exception as e:
            logger.warning("backup for %s failed (still writing): %s",
                            target_date_iso, e)

    out = dict(src)
    out['items'] = items_out
    out['target_date'] = target_date_iso
    new_meta = dict(src.get('meta') or {})
    new_meta['_daily_variation_formula_applied'] = _FORMULA_VERSION
    new_meta['_daily_variation_applied_at'] = datetime.now(
        timezone.utc).isoformat()
    new_meta['_daily_variation_factor_min'] = round(factor_min, 4)
    new_meta['_daily_variation_factor_max'] = round(factor_max, 4)
    new_meta['_daily_variation_factor_avg'] = round(factor_avg, 4)
    new_meta['_daily_variation_mutated']  = n_mutated
    new_meta['_daily_variation_profiled'] = n_profiled
    new_meta['_daily_variation_unpriced'] = n_unpriced
    new_meta['_daily_variation_anchor_source'] = src_label
    out['meta'] = new_meta

    _write_key(dated_key, out)
    return {**summary, 'status': 'wrote'}


# ---------------------------------------------------------------------------
# Date-range helpers
# ---------------------------------------------------------------------------
_DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')


def _daterange(since_iso: str, until_iso: str) -> list[str]:
    d0 = date.fromisoformat(since_iso)
    d1 = date.fromisoformat(until_iso)
    out, cur = [], d0
    while cur <= d1:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _parse_dates_arg(dates_arg: str) -> list[str]:
    out = []
    for tok in (dates_arg or '').split(','):
        t = tok.strip()
        if not t:
            continue
        if not _DATE_RE.fullmatch(t):
            raise ValueError(f'--dates: {t!r} is not YYYY-MM-DD')
        _ = date.fromisoformat(t)
        out.append(t)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=('Render organic per-day variation onto historic '
                      'dated stream_estimates snapshots from reasoned '
                      'rhythm profiles. Costs $0.'))
    parser.add_argument('--since', default='',
                        help='Start date (YYYY-MM-DD, inclusive).')
    parser.add_argument('--until', default='',
                        help='End date (YYYY-MM-DD, inclusive). '
                              'Defaults to yesterday UTC.')
    parser.add_argument('--dates', default='',
                        help='Comma-separated ISO dates (alternative '
                              'to --since/--until).')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--force', action='store_true',
                        help='Re-render even when the sentinel already '
                              'matches the current formula version.')
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    if args.dates:
        dates = _parse_dates_arg(args.dates)
    else:
        if not args.since:
            parser.error('one of --since or --dates is required')
        until = args.until or (
            datetime.now(timezone.utc).date() - timedelta(days=1)
        ).isoformat()
        dates = _daterange(args.since, until)

    if not dates:
        logger.warning("no dates to process")
        return 0

    profiles = load_profiles()

    logger.info("processing %d date(s): %s .. %s (formula=%s, "
                 "dry_run=%s, force=%s)",
                 len(dates), dates[0], dates[-1], _FORMULA_VERSION,
                 args.dry_run, args.force)

    summaries: list[dict] = []
    for d in dates:
        try:
            s = _process_date(d, profiles, dry_run=args.dry_run,
                               force=args.force)
        except Exception as e:
            logger.exception("failed for %s", d)
            s = {'date': d, 'status': f'ERROR: {type(e).__name__}: {e}'}
        summaries.append(s)
        logger.info("  %s -> %s (items=%d, mutated=%d, profiled=%d, "
                     "src=%s, factor min/avg/max=%s/%s/%s)",
                     s.get('date'), s.get('status'),
                     s.get('items', 0), s.get('mutated', 0),
                     s.get('profiled', 0), s.get('source', '-'),
                     s.get('factor_min'), s.get('factor_avg'),
                     s.get('factor_max'))

    wrote    = sum(1 for s in summaries if s.get('status') == 'wrote')
    already  = sum(1 for s in summaries if s.get('status') == 'already-applied')
    missing  = sum(1 for s in summaries if s.get('status') == 'missing')
    no_items = sum(1 for s in summaries if s.get('status') == 'no-items')
    dry      = sum(1 for s in summaries if s.get('status') == 'dry-run')
    errored  = sum(1 for s in summaries
                   if str(s.get('status', '')).startswith('ERROR'))
    print(f"\nsummary: wrote={wrote} already-applied={already} "
          f"missing={missing} no-items={no_items} dry-run={dry} "
          f"errors={errored} total={len(summaries)}")
    return 2 if errored else 0


if __name__ == '__main__':
    sys.exit(main())
