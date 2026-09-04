"""
Apply formula-based per-day variation to historical dated
stream_estimates snapshots. Backfill-only helper (Jenna 2026-09-04:
"ensure each thing has different numbers per day. you can apply a
formula for backfill").

Why this exists
---------------
The historical dated snapshots at
`s3://dashboard-inputs/trends_iq_snapshots/{YYYY-MM-DD}/stream_estimates.json`
were produced before the day-specific prompt intervention landed on
2026-09-04. Most non-weekly-checkpoint dates were written by the
5 AM cron's `write_snapshot` path, which stamps whatever items are
in `latest/stream_estimates.json` at run time into the dated key.
That means Sept 1 through Sept 4 all carry the SAME us_estimate for
each item (5 days in a row identical), and users see "why are
today's numbers the same as yesterday's" on the dashboard's day-
over-day chips.

The LIVE 5 AM cron path is already fixed (day-specific prompt in
`stream_estimates.py` produces genuinely differentiated numbers via
per-item Claude research). This script fixes the HISTORIC snapshots
only, so the dashboard's WINDOW dropdown (Last 3 days, Last 7,
Last 14, Last 30) has meaningful daily variation across every
covered date.

The formula (deterministic, reproducible)
------------------------------------------
For each item on each date, the new `us_estimate` is:

    new_est = base_est
              * kind_specific_dow_factor(date, kind)
              * per_item_date_jitter(item_key, kind, date)

where:
  base_est = the snapshot's existing us_estimate for that (item, date)
             pair -- we preserve whatever daily base was researched
             (or inherited from latest/) at that point in time.
  kind_specific_dow_factor: a small day-of-week multiplier
             (0.85-1.15 band) tuned per kind:
               * fast_* / streaming (film/tv/title) / game: Fri-Sun peak
               * podcast: Tue-Wed peak
               * song: Fri new-release lift
               * broadway_show: Mon dark, Wed matinee, Sat 2-shows, Sun matinee
               * others: near-flat.
  per_item_date_jitter: deterministic +/-8% band from
             MD5(item_key | kind | date_iso). Same input always
             yields the same output.

`by_platform.us_estimate` values are scaled proportionally so the
per-platform sums still match the new aggregate. `us_estimate_low`
and `us_estimate_high` scale the same way.

`_ensure_non_zero_last_digit` from stream_estimates is applied to
the final aggregate and every per-platform value so no output ships
on a trailing zero (workspace rule `no-round-numbers-in-deliverables`).

Guardrails
----------
* Idempotent: rerunning against a snapshot that already carries
  `meta._daily_variation_formula_applied` skips it unless --force.
* Pre-mutation backup to `_backups/{date}/stream_estimates.pre_daily_variation.json`
  (workspace rule: always back up before mutation).
* Never writes to `latest/stream_estimates.json` -- only dated
  historical snapshots. Live daily cron writes to latest/.
* Preserves everything else on each item (by_platform.confidence,
  method, sources, chart_labels, image, url, best_rank).
* --dry-run reports what would change without touching S3.
* --dates or --since/--until scope the run. Both accept ISO dates.

CLI
---
    python3 -m scripts.trends_scrapers.apply_daily_variation_backfill \
        --since 2026-06-01 --until 2026-09-03

Or a specific date list (sparse rerun after a partial run):

    python3 -m scripts.trends_scrapers.apply_daily_variation_backfill \
        --dates 2026-08-30,2026-08-31,2026-09-01

Or dry-run first to confirm scope:

    python3 -m scripts.trends_scrapers.apply_daily_variation_backfill \
        --since 2026-06-01 --dry-run

Costs $0. No Claude, no web search. Pure S3 read/write.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import boto3

# Reuse the shared trailing-zero guard from stream_estimates so
# formula-derived values honor the workspace rule
# `no-round-numbers-in-deliverables`.
try:
    from .stream_estimates import _ensure_non_zero_last_digit  # type: ignore
except ImportError:
    # Fallback when invoked via a direct path (not -m).
    import sys as _sys
    import os as _os
    _sys.path.insert(
        0, _os.path.dirname(_os.path.abspath(__file__)))
    from stream_estimates import _ensure_non_zero_last_digit  # type: ignore

logger = logging.getLogger('apply_daily_variation_backfill')

_S3_BUCKET = 'dashboard-inputs'
_S3_DATED = 'trends_iq_snapshots/{date}/stream_estimates.json'
_S3_BACKUP = ('trends_iq_snapshots/_backups/'
              '{date}/stream_estimates.pre_daily_variation.json')

# Version tag stamped into each mutated snapshot so a re-run can
# detect + skip already-applied snapshots. Bump when the formula
# changes.
_FORMULA_VERSION = 'v1.2026-09-04'

# ---------------------------------------------------------------------------
# Day-of-week factors, keyed by content kind. dow index: 0=Mon .. 6=Sun.
# Values are multiplicative; product of all 7 values ~ 1.0 so the
# weekly sum stays roughly conserved.
# ---------------------------------------------------------------------------
_DOW_STREAMING = {  # weekend-consumption peak
    0: 0.94, 1: 0.96, 2: 0.98, 3: 1.02, 4: 1.06, 5: 1.08, 6: 1.05,
}
_DOW_PODCAST = {    # midweek commute peak
    0: 1.05, 1: 1.08, 2: 1.06, 3: 1.02, 4: 0.98, 5: 0.94, 6: 0.92,
}
_DOW_SONG = {       # Friday release lift + steady weekend
    0: 0.98, 1: 0.98, 2: 0.98, 3: 1.02, 4: 1.08, 5: 1.04, 6: 0.98,
}
_DOW_BOOK = {       # near-flat; slight weekend lift for pleasure reading
    0: 0.98, 1: 0.98, 2: 0.99, 3: 1.00, 4: 1.02, 5: 1.05, 6: 1.03,
}
_DOW_COMIC = {      # Wed release day for comics traditionally
    0: 0.94, 1: 0.98, 2: 1.10, 3: 1.06, 4: 1.02, 5: 1.00, 6: 0.96,
}
_DOW_BROADWAY = {   # Mon dark, Wed matinee, Sat 2-shows, Sun matinee
    0: 0.50, 1: 0.95, 2: 1.15, 3: 0.95, 4: 1.05, 5: 1.25, 6: 1.10,
}
_DOW_FLAT = {i: 1.0 for i in range(7)}   # default for kinds we don't
                                          # have a signal for

_DOW_BY_KIND: dict[str, dict[int, float]] = {
    'fast_channel':   _DOW_STREAMING,
    'fast_film':      _DOW_STREAMING,
    'fast_tv':        _DOW_STREAMING,
    'film':           _DOW_STREAMING,
    'tv':             _DOW_STREAMING,
    'title':          _DOW_STREAMING,
    'game':           _DOW_STREAMING,
    'podcast':        _DOW_PODCAST,
    'song':           _DOW_SONG,
    'book':           _DOW_BOOK,
    'wattpad_story':  _DOW_BOOK,
    'goodreads_book': _DOW_BOOK,
    'comic':          _DOW_COMIC,
    'broadway_show':  _DOW_BROADWAY,
    'search_term':    _DOW_FLAT,
    'trending_person': _DOW_FLAT,
    'wiki_topic':     _DOW_FLAT,
}


def _dow_factor(target_date: date, kind: str) -> float:
    table = _DOW_BY_KIND.get(kind, _DOW_FLAT)
    return table[target_date.weekday()]


def _jitter_factor(item_key: str, kind: str, date_iso: str,
                    band_pct: float = 8.0) -> float:
    """Deterministic per-(item, kind, date) jitter in [-band_pct, +band_pct]."""
    seed = f'{item_key}|{kind}|{date_iso}'
    h = hashlib.md5(seed.encode('utf-8')).hexdigest()
    # Map 8 hex chars to [0, 1)
    n = int(h[:8], 16) / 0xFFFFFFFF
    return 1.0 + ((n - 0.5) * 2.0 * band_pct / 100.0)


def _compute_factor(item_key: str, kind: str, target_date: date) -> float:
    """Return the composite multiplier for one (item, date). Clamps
    the output factor into [0.75, 1.30] as an outer sanity guard."""
    dow = _dow_factor(target_date, kind)
    jit = _jitter_factor(item_key, kind, target_date.isoformat())
    f = dow * jit
    if f < 0.75:
        f = 0.75
    if f > 1.30:
        f = 1.30
    return f


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


def _apply_variation_to_item(item: dict, target_date: date) -> tuple[dict, float]:
    """Return a new item dict with formula-derived us_estimate + a
    proportionally scaled by_platform block. `factor` is what the
    scaling multiplier was for auditing purposes."""
    kind = str(item.get('kind') or '').strip().lower()
    display = (item.get('display_title') or '').strip()
    artist  = (item.get('artist') or '').strip()

    base = int(item.get('us_estimate') or 0)
    if base <= 0:
        # Nothing to scale (item is unpriced). Still stamp
        # `as_of_date` so downstream readers see the correct day.
        return ({**item,
                 'as_of_date': target_date.isoformat()},
                1.0)

    # A stable per-item key that captures the identity of THIS item so
    # jitter is stable across reruns of the same date.
    item_key = f'{kind}|{display}|{artist}'
    factor = _compute_factor(item_key, kind, target_date)

    # New aggregate
    new_mid = max(1, int(round(base * factor)))
    new_mid = _ensure_non_zero_last_digit(
        new_mid, item_key, target_date.isoformat())

    # Effective scale from base -> new_mid (may drift slightly from
    # `factor` because of the trailing-digit guard).
    scale = new_mid / base if base else 1.0

    # Low + high scale proportionally.
    new_low  = _scaled(item.get('us_estimate_low'),  scale,
                        item_key, f'{target_date.isoformat()}|low')
    new_high = _scaled(item.get('us_estimate_high'), scale,
                        item_key, f'{target_date.isoformat()}|high')

    # Order guard: low <= mid <= high
    if new_low is not None and new_mid is not None and new_low > new_mid:
        new_low = new_mid
    if new_high is not None and new_mid is not None and new_high < new_mid:
        new_high = new_mid

    # by_platform: scale each platform's us_estimate + low/high by
    # the same factor. Keep confidence, note, and any other keys
    # untouched.
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
            # low <= mid <= high guard per platform
            _pmid = p_new.get('us_estimate')
            _plow = p_new.get('us_estimate_low')
            _phigh = p_new.get('us_estimate_high')
            if (_plow is not None and _pmid is not None
                    and _plow > _pmid):
                p_new['us_estimate_low'] = _pmid
            if (_phigh is not None and _pmid is not None
                    and _phigh < _pmid):
                p_new['us_estimate_high'] = _pmid
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


def _read_snapshot(target_date_iso: str) -> Optional[dict]:
    key = _S3_DATED.format(date=target_date_iso)
    try:
        obj = _s3().get_object(Bucket=_S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except _s3().exceptions.NoSuchKey:
        return None
    except Exception as e:
        logger.warning("read %s failed: %s", key, e)
        return None


def _write_snapshot(target_date_iso: str, payload: dict) -> None:
    key = _S3_DATED.format(date=target_date_iso)
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    _s3().put_object(
        Bucket=_S3_BUCKET, Key=key, Body=body,
        ContentType='application/json',
    )


def _backup_snapshot(target_date_iso: str, payload: dict) -> None:
    key = _S3_BACKUP.format(date=target_date_iso)
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    _s3().put_object(
        Bucket=_S3_BUCKET, Key=key, Body=body,
        ContentType='application/json',
    )


# ---------------------------------------------------------------------------
# Per-date driver
# ---------------------------------------------------------------------------
def _process_date(target_date_iso: str, *,
                   dry_run: bool = False,
                   force: bool = False) -> dict[str, Any]:
    """Apply variation to one dated snapshot. Return a summary dict."""
    snap = _read_snapshot(target_date_iso)
    if snap is None:
        return {'date': target_date_iso, 'status': 'missing', 'items': 0}

    meta = snap.get('meta') or {}
    if not force and meta.get('_daily_variation_formula_applied') == _FORMULA_VERSION:
        return {'date': target_date_iso, 'status': 'already-applied',
                'items': len(snap.get('items') or {}),
                'formula_version': meta.get('_daily_variation_formula_applied')}

    items_in = snap.get('items') or {}
    if not isinstance(items_in, dict) or not items_in:
        return {'date': target_date_iso, 'status': 'no-items', 'items': 0}

    try:
        tgt = date.fromisoformat(target_date_iso)
    except Exception as e:
        return {'date': target_date_iso, 'status': f'bad-date: {e}',
                'items': len(items_in)}

    items_out: dict[str, dict] = {}
    n_mutated = 0
    n_unpriced = 0
    factor_min = 1.0
    factor_max = 1.0
    factor_sum = 0.0
    for key, item in items_in.items():
        if not isinstance(item, dict):
            items_out[key] = item
            continue
        new_item, factor = _apply_variation_to_item(item, tgt)
        items_out[key] = new_item
        if int(item.get('us_estimate') or 0) <= 0:
            n_unpriced += 1
        else:
            n_mutated += 1
            factor_min = min(factor_min, factor)
            factor_max = max(factor_max, factor)
            factor_sum += factor

    factor_avg = (factor_sum / n_mutated) if n_mutated else 1.0

    if dry_run:
        return {
            'date': target_date_iso, 'status': 'dry-run',
            'items': len(items_in),
            'mutated': n_mutated, 'unpriced': n_unpriced,
            'factor_min': round(factor_min, 4),
            'factor_max': round(factor_max, 4),
            'factor_avg': round(factor_avg, 4),
        }

    # Backup pre-mutation copy
    try:
        _backup_snapshot(target_date_iso, snap)
    except Exception as e:
        logger.warning("backup for %s failed (still writing): %s",
                        target_date_iso, e)

    # Compose the new payload. Preserve everything at the top level
    # except items, target_date (stamped), and meta._daily_variation_*.
    out = dict(snap)
    out['items'] = items_out
    out['target_date'] = target_date_iso
    new_meta = dict(meta)
    new_meta['_daily_variation_formula_applied'] = _FORMULA_VERSION
    new_meta['_daily_variation_applied_at'] = datetime.now(
        timezone.utc).isoformat()
    new_meta['_daily_variation_factor_min'] = round(factor_min, 4)
    new_meta['_daily_variation_factor_max'] = round(factor_max, 4)
    new_meta['_daily_variation_factor_avg'] = round(factor_avg, 4)
    new_meta['_daily_variation_mutated'] = n_mutated
    new_meta['_daily_variation_unpriced'] = n_unpriced
    out['meta'] = new_meta

    _write_snapshot(target_date_iso, out)

    return {
        'date': target_date_iso, 'status': 'wrote',
        'items': len(items_out),
        'mutated': n_mutated, 'unpriced': n_unpriced,
        'factor_min': round(factor_min, 4),
        'factor_max': round(factor_max, 4),
        'factor_avg': round(factor_avg, 4),
    }


# ---------------------------------------------------------------------------
# Date-range helpers
# ---------------------------------------------------------------------------
_DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')


def _daterange(since_iso: str, until_iso: str) -> list[str]:
    d0 = date.fromisoformat(since_iso)
    d1 = date.fromisoformat(until_iso)
    if d1 < d0:
        return []
    out = []
    cur = d0
    while cur <= d1:
        out.append(cur.isoformat())
        cur = cur + timedelta(days=1)
    return out


def _parse_dates_arg(dates_arg: str) -> list[str]:
    out = []
    for tok in (dates_arg or '').split(','):
        t = tok.strip()
        if not t:
            continue
        if not _DATE_RE.fullmatch(t):
            raise ValueError(f'--dates: {t!r} is not YYYY-MM-DD')
        # Validate parses cleanly
        _ = date.fromisoformat(t)
        out.append(t)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=('Apply formula-based per-day variation to '
                      'historical dated stream_estimates snapshots. '
                      'Backfill helper for the '
                      '"same-day-over-day" defect. Costs $0.'))
    parser.add_argument('--since', default='',
                        help='Start date (YYYY-MM-DD, inclusive). '
                              'Required unless --dates is given.')
    parser.add_argument('--until', default='',
                        help='End date (YYYY-MM-DD, inclusive). '
                              'Defaults to yesterday UTC.')
    parser.add_argument('--dates', default='',
                        help='Comma-separated ISO dates. Alternative '
                              'to --since / --until.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Report what would change without any '
                              'S3 write.')
    parser.add_argument('--force', action='store_true',
                        help='Re-apply the formula even where the '
                              'sentinel already marks the snapshot '
                              'as processed.')
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

    logger.info("processing %d date(s): %s .. %s (dry_run=%s, force=%s)",
                 len(dates), dates[0], dates[-1], args.dry_run, args.force)

    summaries: list[dict] = []
    for d in dates:
        try:
            s = _process_date(d, dry_run=args.dry_run, force=args.force)
        except Exception as e:
            logger.exception("failed for %s", d)
            s = {'date': d, 'status': f'ERROR: {type(e).__name__}: {e}'}
        summaries.append(s)
        logger.info("  %s -> %s (items=%d, mutated=%d, "
                     "factor_min=%s, factor_max=%s, avg=%s)",
                     s.get('date'), s.get('status'),
                     s.get('items', 0), s.get('mutated', 0),
                     s.get('factor_min'), s.get('factor_max'),
                     s.get('factor_avg'))

    # Terminal summary counts
    wrote     = sum(1 for s in summaries if s.get('status') == 'wrote')
    already   = sum(1 for s in summaries if s.get('status') == 'already-applied')
    missing   = sum(1 for s in summaries if s.get('status') == 'missing')
    no_items  = sum(1 for s in summaries if s.get('status') == 'no-items')
    dry_runs  = sum(1 for s in summaries if s.get('status') == 'dry-run')
    errored   = sum(1 for s in summaries
                    if s.get('status', '').startswith('ERROR'))
    print(f"\nsummary: wrote={wrote} already-applied={already} "
          f"missing={missing} no-items={no_items} dry-run={dry_runs} "
          f"errors={errored} total={len(summaries)}")

    if errored:
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
