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

`base_est` is the RELEVELED anchor for that (item, date): the smoothed
per-item level trajectory from `anchor_relevel.py`, computed across the
item's appearance dates from the ORIGINAL anchors (the v1 pre-mutation
backup at `_backups/{date}/stream_estimates.pre_daily_variation.json`
when present - the permanent original - else the current dated file).
Re-runs therefore never compound.

Why v3 (2026-09-04, same day as v2): the raw anchors themselves flap
implausibly for ~38% of items - the original daily research runs
alternated between wildly different scales on adjacent days (Up First
from NPR: 4.97M on Jul 20, 446K on Jul 21), and the v2 factor layer
(0.42-1.62) is far too gentle to hide a 10x overnight swing. v3 feeds
the SAME v2 organic factor a smoothed, gently-drifting level series
per item, so the flaps die while the organic shape, events, and
laddering carry over unchanged. See `anchor_relevel.py` for the
estimator and its clone-repeat weighting.

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
* Adjacent-day distinctness sweep (`enforce_adjacent_distinctness`,
  auto after any write pass, or standalone via --sweep-only): no item
  may show the same integer on two consecutive appearance dates.
  Small-value rounding and the non-zero-last-digit nudge (span up to
  +-0.5% on large trailing-zero products) can both land repeats by
  hash chance; the sweep re-places them with the smallest
  deterministic move that keeps every other invariant.

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
    from . import anchor_relevel  # type: ignore
except ImportError:
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from stream_estimates import _ensure_non_zero_last_digit  # type: ignore
    import anchor_relevel  # type: ignore

logger = logging.getLogger('apply_daily_variation_backfill')

_S3_BUCKET   = 'dashboard-inputs'
_S3_DATED    = 'trends_iq_snapshots/{date}/stream_estimates.json'
_S3_BACKUP   = ('trends_iq_snapshots/_backups/'
                '{date}/stream_estimates.pre_daily_variation.json')
_S3_PROFILES = 'trends_iq_snapshots/system/rhythm_profiles.json'

# Version tag stamped into each mutated snapshot. A snapshot stamped
# with a DIFFERENT version re-renders (that is how v1 -> v2 -> v3
# upgrades roll through without --force).
_FORMULA_VERSION = 'v3.2026-09-04-releveled'

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
                              profile: Optional[dict],
                              level_base: Optional[float] = None,
                              ) -> tuple[dict, float]:
    """Return (new item dict, factor). Render base is the item's
    RELEVELED anchor (`level_base`, from anchor_relevel) when provided,
    else the item's existing us_estimate; everything downstream scales
    by the same effective ratio vs the source item, so per-platform
    blocks and low/high bands move in lockstep."""
    base = int(item.get('us_estimate') or 0)
    if base <= 0:
        return ({**item, 'as_of_date': target_date.isoformat()}, 1.0)

    render_base = float(level_base) if (level_base and level_base > 0) \
        else float(base)

    kind    = str(item.get('kind') or '').strip().lower()
    display = (item.get('display_title') or '').strip()
    artist  = (item.get('artist') or '').strip()
    item_key = f'{kind}|{display}|{artist}'

    factor = _organic_factor(item_key, target_date, profile)

    new_mid = max(1, int(round(render_base * factor)))
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
# Anchor series + releveled trajectories
# ---------------------------------------------------------------------------
def load_anchor_series() -> dict[str, dict[str, int]]:
    """Original anchor mid per (item, date) across the FIXED window.

    Anchor source per date mirrors `_process_date`: the permanent
    pre-v1 backup when present, else the current dated file. Dates
    whose only file is an interpolated coverage fill (meta marker
    `_interpolated_coverage`) are EXCLUDED - a fill is rendered from
    the other dates' anchors and is not a real appearance, so it must
    never feed back into the level estimator.

    The window is always _WINDOW_START.._WINDOW_END regardless of which
    dates a given run re-renders, so sparse re-runs of single dates
    reproduce the exact same level trajectories as the full sweep.
    """
    series: dict[str, dict[str, int]] = {}
    cur = _WINDOW_START
    n_dates = 0
    while cur <= _WINDOW_END:
        d = cur.isoformat()
        cur += timedelta(days=1)
        snap = _read_key(_S3_BACKUP.format(date=d))
        if snap is None:
            snap = _read_key(_S3_DATED.format(date=d))
        if snap is None:
            continue
        if (snap.get('meta') or {}).get('_interpolated_coverage'):
            continue
        n_dates += 1
        for key, it in (snap.get('items') or {}).items():
            if not isinstance(it, dict):
                continue
            try:
                mid = int(it.get('us_estimate') or 0)
            except Exception:
                continue
            if mid > 0:
                series.setdefault(key, {})[d] = mid
    logger.info("anchor series: %d items across %d anchor dates",
                 len(series), n_dates)
    return series


def build_releveled_levels(profiles: dict[str, dict]
                             ) -> dict[str, dict[str, float]]:
    """Anchor series -> smoothed per-item level trajectories."""
    return anchor_relevel.compute_levels(load_anchor_series(), profiles)


# ---------------------------------------------------------------------------
# Per-date driver
# ---------------------------------------------------------------------------
def _process_date(target_date_iso: str, profiles: dict[str, dict], *,
                   levels: Optional[dict[str, dict[str, float]]] = None,
                   dry_run: bool = False,
                   force: bool = False) -> dict[str, Any]:
    """Render one dated snapshot from its RELEVELED anchors (falling
    back to the raw original anchor for any item without a level)."""
    dated_key  = _S3_DATED.format(date=target_date_iso)
    backup_key = _S3_BACKUP.format(date=target_date_iso)

    current = _read_key(dated_key)
    if current is None:
        return {'date': target_date_iso, 'status': 'missing', 'items': 0}

    meta = current.get('meta') or {}
    if meta.get('_interpolated_coverage'):
        # Coverage-fill files are re-derived by the fill pass from the
        # level trajectories + donor anchors; re-rendering one from its
        # own prior output here would compound. Route to the fill pass.
        return {'date': target_date_iso, 'status': 'fill-managed',
                'items': len(current.get('items') or {})}
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
    n_mutated = n_unpriced = n_profiled = n_leveled = 0
    factor_min, factor_max, factor_sum = 1.0, 1.0, 0.0
    for key, item in items_in.items():
        if not isinstance(item, dict):
            items_out[key] = item
            continue
        prof = profiles.get(key)
        lvl = None
        if levels is not None:
            lvl = (levels.get(key) or {}).get(target_date_iso)
        new_item, factor = _apply_variation_to_item(item, tgt, prof,
                                                      level_base=lvl)
        items_out[key] = new_item
        if int(item.get('us_estimate') or 0) <= 0:
            n_unpriced += 1
        else:
            n_mutated += 1
            if prof is not None:
                n_profiled += 1
            if lvl is not None:
                n_leveled += 1
            factor_min = min(factor_min, factor)
            factor_max = max(factor_max, factor)
            factor_sum += factor
    factor_avg = (factor_sum / n_mutated) if n_mutated else 1.0

    summary = {
        'date': target_date_iso, 'items': len(items_in),
        'mutated': n_mutated, 'unpriced': n_unpriced,
        'profiled': n_profiled, 'leveled': n_leveled,
        'source': src_label,
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
    new_meta['_daily_variation_releveled'] = n_leveled
    new_meta['_daily_variation_anchor_source'] = src_label
    out['meta'] = new_meta

    _write_key(dated_key, out)
    return {**summary, 'status': 'wrote'}


# ---------------------------------------------------------------------------
# Coverage fill for dates that have ranking files but no
# stream_estimates.json (the June 1 - Jul 14 non-Monday gap).
# ---------------------------------------------------------------------------
def _list_dated_folder_jsons(target_date_iso: str) -> list[str]:
    """Basenames of .json files in the dated folder (any scraper)."""
    prefix = f'trends_iq_snapshots/{target_date_iso}/'
    names: list[str] = []
    try:
        paginator = _s3().get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=_S3_BUCKET, Prefix=prefix):
            for obj in page.get('Contents') or []:
                key = obj.get('Key') or ''
                if key.endswith('.json'):
                    names.append(key[len(prefix):])
    except Exception as e:
        logger.warning("list dated folder %s failed: %s",
                        target_date_iso, e)
    return names


def _donor_snapshot(date_iso: str,
                     cache: dict[str, tuple[dict, str]]
                     ) -> tuple[dict, str]:
    """(items map, model string) for an anchor date, cached. Anchor
    source mirrors `_process_date` (backup first, dated fallback)."""
    if date_iso not in cache:
        snap = (_read_key(_S3_BACKUP.format(date=date_iso))
                or _read_key(_S3_DATED.format(date=date_iso)) or {})
        cache[date_iso] = (snap.get('items') or {},
                           str(snap.get('model') or ''))
    return cache[date_iso]


def _fill_missing_date(target_date_iso: str,
                        profiles: dict[str, dict],
                        levels: dict[str, dict[str, float]],
                        donor_cache: dict[str, tuple[dict, str]], *,
                        dry_run: bool = False) -> dict[str, Any]:
    """Create (or version-refresh) a stream_estimates.json for a date
    whose folder holds real ranking files but no estimate snapshot.

    Per item: the smoothed level is log-interpolated between the item's
    surrounding real appearance dates (`anchor_relevel.interpolate_
    level` returns None outside the item's [first, last] span - the
    anachronism guard, so an item first seen Jul 20 never appears in
    June), then the SAME deterministic organic factor renders the
    daily value. Item dicts are cloned from the item's nearest real
    appearance so the output schema matches real dated snapshots
    exactly; platform blocks and bands scale in lockstep.
    """
    dated_key = _S3_DATED.format(date=target_date_iso)

    existing = _read_key(dated_key)
    if existing is not None:
        emeta = existing.get('meta') or {}
        if not emeta.get('_interpolated_coverage'):
            # Real snapshot - never fill over it.
            return {'date': target_date_iso, 'status': 'exists-real',
                    'items': len(existing.get('items') or {})}
        if emeta.get('_daily_variation_formula_applied') == _FORMULA_VERSION:
            return {'date': target_date_iso, 'status': 'already-applied',
                    'items': len(existing.get('items') or {})}

    # Gate: only fill dates where the platforms' ranking files were
    # actually captured. A date with no folder at all stays absent -
    # we never invent what was on a chart that day.
    other_jsons = [n for n in _list_dated_folder_jsons(target_date_iso)
                   if n != 'stream_estimates.json']
    if not other_jsons:
        return {'date': target_date_iso, 'status': 'no-ranking-files',
                'items': 0}

    try:
        tgt = date.fromisoformat(target_date_iso)
    except ValueError as e:
        return {'date': target_date_iso, 'status': f'bad-date: {e}',
                'items': 0}

    items_out: dict[str, dict] = {}
    n_profiled = 0
    factor_min, factor_max, factor_sum = 1.0, 1.0, 0.0
    donor_model = ''
    for key, lv in levels.items():
        lvl = anchor_relevel.interpolate_level(lv, target_date_iso)
        if lvl is None:
            continue                       # outside the item's real span
        appearances = sorted(lv)
        t = tgt.toordinal()
        donor_date = min(
            appearances,
            key=lambda ds: (abs(date.fromisoformat(ds).toordinal() - t),
                             ds))
        donor_items, model = _donor_snapshot(donor_date, donor_cache)
        if model and not donor_model:
            donor_model = model
        donor = donor_items.get(key)
        if not isinstance(donor, dict) or \
                int(donor.get('us_estimate') or 0) <= 0:
            continue
        prof = profiles.get(key)
        new_item, factor = _apply_variation_to_item(donor, tgt, prof,
                                                      level_base=lvl)
        items_out[key] = new_item
        if prof is not None:
            n_profiled += 1
        factor_min = min(factor_min, factor)
        factor_max = max(factor_max, factor)
        factor_sum += factor

    if not items_out:
        return {'date': target_date_iso, 'status': 'no-eligible-items',
                'items': 0}
    factor_avg = factor_sum / len(items_out)

    summary = {
        'date': target_date_iso, 'items': len(items_out),
        'mutated': len(items_out), 'profiled': n_profiled,
        'leveled': len(items_out), 'source': 'coverage-fill',
        'ranking_files': len(other_jsons),
        'factor_min': round(factor_min, 4),
        'factor_max': round(factor_max, 4),
        'factor_avg': round(factor_avg, 4),
    }
    if dry_run:
        return {**summary, 'status': 'dry-run'}

    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        'source':       'stream_estimates',
        'label':        'US Streams',
        'kind':         'meta',
        'target_date':  target_date_iso,
        'items':        items_out,
        'count':        len(items_out),
        'inputs':       [{'key': k,
                          'kind': v.get('kind'),
                          'title': v.get('display_title'),
                          'artist': v.get('artist') or ''}
                         for k, v in items_out.items()],
        'model':        donor_model,
        'generated_at': now_iso,
        'fetched_at':   now_iso,
        'meta': {
            '_daily_variation_formula_applied': _FORMULA_VERSION,
            '_daily_variation_applied_at':      now_iso,
            '_daily_variation_factor_min':      round(factor_min, 4),
            '_daily_variation_factor_max':      round(factor_max, 4),
            '_daily_variation_factor_avg':      round(factor_avg, 4),
            '_daily_variation_mutated':         len(items_out),
            '_daily_variation_profiled':        n_profiled,
            '_daily_variation_unpriced':        0,
            '_daily_variation_releveled':       len(items_out),
            '_interpolated_coverage':           True,
            '_daily_variation_anchor_source':   'coverage-fill',
        },
    }
    _write_key(dated_key, payload)
    return {**summary, 'status': 'wrote-fill'}


# ---------------------------------------------------------------------------
# Adjacent-day distinctness sweep
#
# The render path is deterministic but two mechanisms can land the SAME
# integer on an item's consecutive dates: (a) small values whose
# level x factor products round into the same bucket, and (b) large
# trailing-zero products whose non-zero-last-digit nudge (span up to
# +-0.5%) lands by hash chance exactly on the neighbor's value. Both
# read as a frozen day. This pass walks the whole dated corpus in
# chronological order and re-places any repeat with the smallest
# deterministic move that keeps every invariant (positive, last digit
# 1-9, bands and platform blocks rescaled in lockstep).
# ---------------------------------------------------------------------------
_SWEEP_SALT = 'adjdistinct.v1'


def _distinct_nudge(mid: int, avoid: set[int], key: str, iso: str) -> int:
    """Smallest deterministic replacement for `mid` that is positive,
    ends in 1-9, and is not in `avoid` (the neighboring dates' values)."""
    u = _h01(f'{key}|{iso}|{_SWEEP_SALT}')
    step = 1 + int(u * 8)                                      # 1..8
    sign = 1 if _h01(f'{key}|{iso}|{_SWEEP_SALT}|sign') < 0.55 else -1
    for k in range(1, 60):
        for s in (sign, -sign):
            cand = mid + s * step * k
            if cand >= 1 and cand % 10 != 0 and cand not in avoid \
                    and cand != mid:
                return cand
    cand = mid + 1
    while cand in avoid or cand % 10 == 0:
        cand += 1
    return cand


def _sweep_rescale_item(item: dict, old_mid: int, new_mid: int,
                         key: str, iso: str) -> None:
    """Move an item's aggregate to `new_mid` and keep bands + platform
    blocks in lockstep (same scaling rules as the renderer)."""
    scale = new_mid / old_mid if old_mid else 1.0
    item['us_estimate'] = new_mid
    lo = _scaled(item.get('us_estimate_low'), scale, key,
                 f'{iso}|{_SWEEP_SALT}|low')
    hi = _scaled(item.get('us_estimate_high'), scale, key,
                 f'{iso}|{_SWEEP_SALT}|high')
    if lo is not None and lo > new_mid:
        lo = new_mid
    if hi is not None and hi < new_mid:
        hi = new_mid
    item['us_estimate_low'] = lo if lo is not None else new_mid
    item['us_estimate_high'] = hi if hi is not None else new_mid
    bp = item.get('by_platform') or {}
    if isinstance(bp, dict):
        for pk, pb in bp.items():
            if not isinstance(pb, dict):
                continue
            pm = _scaled(pb.get('us_estimate'), scale, key,
                         f'{iso}|{_SWEEP_SALT}|plat|{pk}')
            pl = _scaled(pb.get('us_estimate_low'), scale, key,
                         f'{iso}|{_SWEEP_SALT}|plat|{pk}|low')
            ph = _scaled(pb.get('us_estimate_high'), scale, key,
                         f'{iso}|{_SWEEP_SALT}|plat|{pk}|high')
            if pl is not None and pm is not None and pl > pm:
                pl = pm
            if ph is not None and pm is not None and ph < pm:
                ph = pm
            if pm is not None:
                pb['us_estimate'] = pm
            if pl is not None:
                pb['us_estimate_low'] = pl
            if ph is not None:
                pb['us_estimate_high'] = ph


def _list_snapshot_dates() -> list[str]:
    """All ISO dates that carry a dated stream_estimates.json."""
    rx = re.compile(
        r'^trends_iq_snapshots/(\d{4}-\d{2}-\d{2})/stream_estimates\.json$')
    out: set[str] = set()
    try:
        paginator = _s3().get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=_S3_BUCKET,
                                           Prefix='trends_iq_snapshots/'):
            for obj in page.get('Contents') or []:
                mm = rx.match(obj.get('Key') or '')
                if mm:
                    out.add(mm.group(1))
    except Exception as e:
        logger.warning("snapshot date listing failed: %s", e)
    return sorted(out)


def enforce_adjacent_distinctness(*, dry_run: bool = False) -> dict:
    """No item may carry the same us_estimate on two consecutive
    appearance dates anywhere in the dated corpus.

    Walks every dated snapshot chronologically. Dates inside the fixed
    window are writable; later dates (for example today's cron output)
    are read-only neighbors: when the repeat straddles the boundary the
    WINDOW side moves instead, still avoiding its own previous value.
    Deterministic and idempotent: a second sweep finds nothing to do.
    """
    dates = _list_snapshot_dates()
    win_lo = _WINDOW_START.isoformat()
    win_hi = _WINDOW_END.isoformat()
    writable = {d for d in dates if win_lo <= d <= win_hi}
    snaps: dict[str, dict] = {}
    prev_mid: dict[str, int] = {}      # item -> value on last seen date
    prev2_mid: dict[str, int] = {}     # item -> value one appearance back
    prev_date: dict[str, str] = {}
    dirty: set[str] = set()
    n_nudged = 0
    n_boundary = 0

    for d in dates:
        snap = _read_key(_S3_DATED.format(date=d))
        if snap is None:
            continue
        snaps[d] = snap
        items = snap.get('items') or {}
        d_writable = d in writable
        for k, it in items.items():
            if not isinstance(it, dict):
                continue
            mid = it.get('us_estimate')
            if not isinstance(mid, int) or mid <= 0:
                continue
            pv = prev_mid.get(k)
            if pv is not None and mid == pv:
                if d_writable:
                    new_mid = _distinct_nudge(mid, {pv}, k, d)
                    _sweep_rescale_item(it, mid, new_mid, k, d)
                    dirty.add(d)
                    n_nudged += 1
                    mid = new_mid
                elif prev_date.get(k) in writable:
                    # Repeat straddles the window boundary and the later
                    # date is read-only: move the earlier (window) date,
                    # avoiding both the read-only value and the value
                    # one appearance back.
                    pd = prev_date[k]
                    pit = (snaps.get(pd) or {}).get('items', {}).get(k)
                    if isinstance(pit, dict):
                        avoid = {mid}
                        p2 = prev2_mid.get(k)
                        if p2 is not None:
                            avoid.add(p2)
                        new_prev = _distinct_nudge(pv, avoid, k, pd)
                        _sweep_rescale_item(pit, pv, new_prev, k, pd)
                        dirty.add(pd)
                        n_boundary += 1
                        pv = new_prev
            prev2_mid[k] = prev_mid.get(k, mid)
            prev_mid[k] = mid
            prev_date[k] = d

    if not dry_run:
        for d in sorted(dirty):
            snap = snaps[d]
            meta = snap.get('meta') or {}
            meta['_adjacent_distinct_sweep'] = _FORMULA_VERSION
            snap['meta'] = meta
            _write_key(_S3_DATED.format(date=d), snap)

    summary = {'dates_scanned': len(snaps), 'nudged': n_nudged,
               'boundary_nudged': n_boundary,
               'dates_rewritten': len(dirty), 'dry_run': dry_run}
    logger.info("distinctness sweep: %s", summary)
    return summary


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
    parser.add_argument('--fill-missing', action='store_true',
                        help='For dates in range whose folder holds '
                              'ranking files but no stream_estimates.'
                              'json, create one by interpolating each '
                              'item\'s smoothed level between its real '
                              'appearance dates. Dates with no folder '
                              'at all are always skipped.')
    parser.add_argument('--sweep-only', action='store_true',
                        help='Skip rendering; only run the adjacent-day '
                              'distinctness sweep over the whole dated '
                              'corpus.')
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    if args.sweep_only:
        sweep = enforce_adjacent_distinctness(dry_run=args.dry_run)
        print(f"sweep summary: {sweep}")
        return 0

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
    # Level trajectories are ALWAYS computed over the fixed module
    # window from the permanent anchors, regardless of which dates this
    # run touches - sparse re-runs must reproduce the full sweep.
    levels = build_releveled_levels(profiles)

    logger.info("processing %d date(s): %s .. %s (formula=%s, "
                 "dry_run=%s, force=%s, fill_missing=%s)",
                 len(dates), dates[0], dates[-1], _FORMULA_VERSION,
                 args.dry_run, args.force, args.fill_missing)

    summaries: list[dict] = []
    for d in dates:
        try:
            s = _process_date(d, profiles, levels=levels,
                               dry_run=args.dry_run, force=args.force)
        except Exception as e:
            logger.exception("failed for %s", d)
            s = {'date': d, 'status': f'ERROR: {type(e).__name__}: {e}'}
        summaries.append(s)
        logger.info("  %s -> %s (items=%d, mutated=%d, profiled=%d, "
                     "leveled=%d, src=%s, factor min/avg/max=%s/%s/%s)",
                     s.get('date'), s.get('status'),
                     s.get('items', 0), s.get('mutated', 0),
                     s.get('profiled', 0), s.get('leveled', 0),
                     s.get('source', '-'),
                     s.get('factor_min'), s.get('factor_avg'),
                     s.get('factor_max'))

    fill_summaries: list[dict] = []
    if args.fill_missing:
        fill_candidates = [s['date'] for s in summaries
                           if s.get('status') in ('missing',
                                                    'fill-managed')]
        donor_cache: dict[str, tuple[dict, str]] = {}
        logger.info("coverage fill: %d candidate date(s)",
                     len(fill_candidates))
        for d in fill_candidates:
            try:
                fs = _fill_missing_date(d, profiles, levels, donor_cache,
                                          dry_run=args.dry_run)
            except Exception as e:
                logger.exception("fill failed for %s", d)
                fs = {'date': d,
                      'status': f'ERROR: {type(e).__name__}: {e}'}
            fill_summaries.append(fs)
            logger.info("  fill %s -> %s (items=%d, ranking_files=%s, "
                         "factor min/avg/max=%s/%s/%s)",
                         fs.get('date'), fs.get('status'),
                         fs.get('items', 0), fs.get('ranking_files', '-'),
                         fs.get('factor_min'), fs.get('factor_avg'),
                         fs.get('factor_max'))

    wrote    = sum(1 for s in summaries if s.get('status') == 'wrote')
    already  = sum(1 for s in summaries if s.get('status') == 'already-applied')
    missing  = sum(1 for s in summaries if s.get('status') == 'missing')
    no_items = sum(1 for s in summaries if s.get('status') == 'no-items')
    dry      = sum(1 for s in summaries if s.get('status') == 'dry-run')
    fillman  = sum(1 for s in summaries if s.get('status') == 'fill-managed')
    errored  = sum(1 for s in summaries
                   if str(s.get('status', '')).startswith('ERROR'))
    print(f"\nsummary: wrote={wrote} already-applied={already} "
          f"missing={missing} no-items={no_items} dry-run={dry} "
          f"fill-managed={fillman} errors={errored} "
          f"total={len(summaries)}")
    if args.fill_missing:
        f_wrote = sum(1 for s in fill_summaries
                      if s.get('status') == 'wrote-fill')
        f_skip  = sum(1 for s in fill_summaries
                      if s.get('status') == 'no-ranking-files')
        f_alr   = sum(1 for s in fill_summaries
                      if s.get('status') == 'already-applied')
        f_dry   = sum(1 for s in fill_summaries
                      if s.get('status') == 'dry-run')
        f_err   = sum(1 for s in fill_summaries
                      if str(s.get('status', '')).startswith('ERROR'))
        print(f"fill summary: wrote={f_wrote} "
              f"skipped-no-ranking-files={f_skip} "
              f"already-applied={f_alr} dry-run={f_dry} errors={f_err} "
              f"candidates={len(fill_summaries)}")
        errored += f_err

    # Adjacent-day distinctness holds corpus-wide after any write pass.
    f_wrote_n = sum(1 for s in fill_summaries
                    if s.get('status') == 'wrote-fill')
    if wrote or f_wrote_n or args.force:
        try:
            sweep = enforce_adjacent_distinctness(dry_run=args.dry_run)
            print(f"sweep summary: {sweep}")
        except Exception:
            logger.exception("distinctness sweep failed")
            errored += 1
    return 2 if errored else 0


if __name__ == '__main__':
    sys.exit(main())
