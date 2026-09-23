#!/usr/bin/env python3
"""Re-derive every channel on a FAST platform that publishes no schedule.

Philo, Plex, Sling Freestream and MyFree DIRECTV report no airings, so
the lineup order they emit is the source page's own, which is
alphabetical. Until 1d71c348 the collector labelled that position a
channel rank and the prompt stated an airings count of zero under a
header reading higher means more popular. The research step read a tier
off both, the tier became the value, and the board ranked the alphabet:
Philo opened on five channels beginning with A, Plex on 50 Cent Action,
AccuWeather NOW, 80's Sitcom Flashback, 60 Minutes and 365BLK.

The prompt is fixed, but the stored values are not. The nightly run
treats an item priced for the target day as covered and carries it
forward, so the artifact would survive the fix indefinitely. This
forces the re-derivation: it prices every channel on those four rails
from scratch under the corrected prompt, including the overflow the
old 2,600 ceiling was withholding.

Scope comes from the data, not a slug list: an item is in only when the
collector marked it `ranking_signal: False`, which happens only when
its whole platform reports no airings at all. A platform that publishes
a schedule cannot enter this pass even if named on the command line.

Every channel goes to the deeper model. On a rail with no ordering
signal the reasoning IS the signal, and the rank rule would otherwise
pick the cost tier alphabetically too.

Progress is checkpointed under a key of its own so a resume here can
never be mistaken for the nightly run's. Spend is capped and reported.

Never queries clickstream (`.cursor/rules/trends-rankers-never-clickstream.mdc`).

    python3 -m scripts.trends_scrapers.reprice_scheduleless_fast --dry-run
    python3 -m scripts.trends_scrapers.reprice_scheduleless_fast --cap-usd 60
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

_CHECKPOINT = '/tmp/scheduleless_fast_reprice_checkpoint.json'

# Suffix on the WIP checkpoint's date key. The nightly resumes from the
# bare target date; this pass must never be read as that run's progress,
# nor its own progress be picked up by the nightly.
_WIP_SUFFIX = '-scheduleless-fast'

# Below this a reading reads as a failed call rather than a quiet
# channel. Same bar the coverage gate applies on merge.
_MIN_CREDIBLE = 100


def _load_checkpoint(path: str) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _save_checkpoint(path: str, results: dict) -> None:
    try:
        with open(path, 'w') as fh:
            json.dump(results, fh)
    except Exception:
        logger.exception('checkpoint write failed (non-fatal)')


def collect(se, platforms: list) -> list[dict]:
    """Channels on scheduleless rails, priced or not.

    `ranking_signal` is the gate. The collector sets it False only when
    the whole platform reports no airings, so a scheduled rail cannot
    be dragged in by a `--platform` typo.
    """
    out = []
    for it in se._collect_fast_channels():
        if it.get('ranking_signal', True):
            continue
        if platforms and it.get('fast_platform') not in platforms:
            continue
        # No previous-day reference is stamped, deliberately. The
        # previous day IS the artifact being corrected, and handing it
        # over as an anchor with the continuity guard behind it would
        # pull each fresh reading straight back onto it.
        it['force_tier'] = 'hi'
        out.append(it)
    return out


def merge(se, results: dict, target_date_iso: str) -> dict:
    """Write the fresh readings into the stored snapshot, and nothing
    else. Only the keys priced here move."""
    from scripts.trends_scrapers import _base

    weak = [k for k, v in results.items()
            if (v.get('us_estimate') or 0) < _MIN_CREDIBLE]
    for k in weak:
        logger.info('dropping implausible reading %s (%s); the stored '
                    'value stays and tonight retries', k,
                    results[k].get('us_estimate'))
        results.pop(k, None)
    if not results:
        return {'written': 0, 'dropped': len(weak)}

    # Stamp the same day-over-day fields a live run stamps, against the
    # same baseline it reads: the previous series day's published
    # output. Without `as_of_date` the nightly would re-research these
    # in a few hours anyway, anchored to the very values this pass
    # exists to replace.
    prev_snap = se._read_dated_snapshot('stream_estimates', days_back=1)
    try:
        prev_iso = (datetime.fromisoformat(target_date_iso).date()
                    - timedelta(days=1)).isoformat()
    except Exception:
        prev_iso = ''
    se._attach_dod_trend(results, prev_snap, prev_date_iso=prev_iso,
                         today_iso=target_date_iso)

    snap = se._read_snapshot('stream_estimates') or {}
    items = snap.get('items') or {}
    items.update(results)
    snap['items'] = items
    snap['count'] = len(items)
    snap.setdefault('target_date', target_date_iso)
    snap['scheduleless_fast_repriced_at'] = (
        datetime.now(timezone.utc).isoformat())
    _base.write_snapshot('stream_estimates', snap)
    return {'written': len(results), 'dropped': len(weak),
            'baseline': prev_iso}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--cap-usd', type=float, default=60.0)
    ap.add_argument('--checkpoint', default=_CHECKPOINT)
    ap.add_argument('--platform', action='append', default=[],
                    help='restrict to these slugs (repeatable)')
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers._spend_monitor import SpendMonitor

    # Reason about the day the stored snapshot is about, so this pass
    # corrects that day's readings rather than opening a new one.
    snap = se._read_snapshot('stream_estimates') or {}
    target_date_iso = snap.get('target_date') or (
        datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    items = collect(se, args.platform)
    if not items:
        print('no scheduleless channels collected; nothing to do')
        return 0

    by_plat: dict = {}
    for it in items:
        by_plat[it['fast_platform']] = by_plat.get(it['fast_platform'], 0) + 1
    stored = snap.get('items') or {}
    unpriced = sum(
        1 for it in items
        if not (stored.get(se._lookup_key('fast_channel',
                                          it['display_title'],
                                          it.get('artist') or ''))
                or {}).get('us_estimate')
    )
    print(f'target day                    : {target_date_iso}')
    print(f'channels on scheduleless rails: {len(items)}')
    for slug in sorted(by_plat):
        print(f'   {slug:<18} {by_plat[slug]:>5}')
    print(f'   of which carry no value yet: {unpriced}')

    done = _load_checkpoint(args.checkpoint)
    if done:
        print(f'resuming: {len(done)} channel(s) already re-derived')
    todo = [it for it in items
            if se._lookup_key('fast_channel', it['display_title'],
                              it.get('artist') or '') not in done]

    # Project the work left, not the whole field. Projecting the field
    # means a resume with six channels outstanding reads as the cost of
    # all 1,584 and refuses under a cap that would comfortably cover it.
    # Deeper model throughout, on the discounted batch lane.
    est = len(todo) * ((3000 / 1e6) * 3.0 + (500 / 1e6) * 15.0) * 0.5
    print(f'still to re-derive               : {len(todo)}')
    print(f'projected spend (discounted lane): ${est:.2f}')
    if args.dry_run:
        print('dry-run: nothing researched')
        return 0
    if est > args.cap_usd:
        print(f'projected ${est:.2f} is over the ${args.cap_usd:.2f} '
              f'guard; stopping without spending')
        return 2

    meter = SpendMonitor(cap_usd=args.cap_usd, prefix='scheduleless_fast')
    results = dict(done)
    if todo:
        cp = {'target_date_iso': f'{target_date_iso}{_WIP_SUFFIX}',
              'kept_prior': {}, 'in_progress': {}, 'flushed_at': 0}
        fresh = se._research_all_batch(todo,
                                       target_date_iso=target_date_iso,
                                       spend_monitor=meter,
                                       checkpoint_state=cp)
        if len(fresh) < se._BATCH_FALLBACK_MIN_SHARE * len(todo):
            print(f'batch returned {len(fresh)}/{len(todo)}; pricing the '
                  f'remainder one at a time')
            rest = [it for it in todo
                    if se._lookup_key('fast_channel', it['display_title'],
                                      it.get('artist') or '') not in fresh]
            fresh.update(se._research_all(rest,
                                          target_date_iso=target_date_iso,
                                          spend_monitor=meter))
        results.update(fresh)
        _save_checkpoint(args.checkpoint, results)
        print(f're-derived {len(fresh)}/{len(todo)} channel(s) this pass')

    stats = merge(se, dict(results), target_date_iso)
    print(f'wrote {stats["written"]} reading(s) into the stored snapshot '
          f'(day-over-day baseline {stats.get("baseline") or "none"})')
    if stats['dropped']:
        print(f'held (no usable reading, tonight retries): {stats["dropped"]}')
    print(f'spend: ${meter.total():.2f}')

    try:
        import trends_iq
        n = trends_iq.invalidate_live_compute_view_caches()
        print(f'purged {n} live cache entries')
    except Exception:
        logger.exception('cache purge failed (non-fatal)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
