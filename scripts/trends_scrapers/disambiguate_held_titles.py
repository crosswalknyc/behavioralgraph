#!/usr/bin/env python3
"""Resolve the rows the estimator HELD because the title is ambiguous.

The cross-service provenance defect is down to a small tail of titles
whose name does not identify a work. Fargo is a 1996 film and an FX
anthology series, and Hulu carries both. Alone is a History survival
series and several unrelated films. The Curse on BritBox is a 2022
British comedy caper, not the 2023 Nathan Fielder series. The Naked
Gun is a 1988 original and a 2025 remake. Sherlock on BritBox is not
the same audience as Sherlock on Hulu even though it is the same
work.

A single cheap lookup on the bare string cannot tell those apart, so
the estimator held them, which was the right call and stays the right
call. This pass gives the research what it was missing and asks
again:

  1. THE SERVICE, as the disambiguating hint. "Fargo on Hulu" and
     "Fargo on Prime Video" are two different questions.
  2. THE WORK, from detail already sitting in the rendered row and
     never passed along: release year, film or series, the catalog's
     own path, the one-line synopsis.
  3. A CARRIAGE CHECK. If the named service does not offer the title
     to US viewers, there is no audience to size and the answer is no
     number. Nothing is written for that service and the row is named
     in the report so the panel it came from can be looked at.
  4. A STRONGER MODEL, because rank is the wrong signal here: a
     rank-154 row that nobody could identify is hard precisely
     because it is obscure, and the rank rule would send it to the
     light tier with no search.

HOLDING IS STILL CORRECT. A block comes back at zero with a note
beginning NOT CARRIED or CANNOT IDENTIFY, or comes back below the
service's own credibility floor, and nothing is written. The row
keeps a number about its own service, this pass reports why it held,
and tonight's run tries again. Never guess, and never substitute
another service's number.

Merging is narrow, exactly as the provenance pass does it: only
`by_platform[<service>]` moves, so the title's rows on services that
were already reading correctly do not move a digit.

Never queries clickstream
(`.cursor/rules/trends-rankers-never-clickstream.mdc`).

    python3 -m scripts.trends_scrapers.disambiguate_held_titles --dry-run
    python3 -m scripts.trends_scrapers.disambiguate_held_titles --cap-usd 40
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

_FILTERS = {'geo_type': 'National', 'geo_value': '', 'lookback_days': 1}
_CHECKPOINT = '/tmp/disambiguate_held_checkpoint.json'

# Pinned rather than an alias. This pass is small enough that the
# model choice is not a cost question, and a pinned snapshot keeps
# the spend report honest: `_spend_monitor` prices this exact string,
# where an unpriced Opus alias would bill at the deliberately
# pessimistic unknown-Opus rate.
_DEFAULT_MODEL = 'claude-opus-4-5-20251101'

# The nightly default is 2000 and it is the right default there. This
# pass asks a harder question (which work is this, does the service
# carry it, and only then how big is it) of a deeper model, and on
# the first run three of sixteen items stopped on max_tokens just
# past 2100 with the JSON half-written. Those three billed and
# returned nothing. Only the output ceiling moves, and only for this
# pass; generated tokens are what bill, so a cap nobody reaches costs
# nothing.
_MAX_TOKENS = 4000

# How a returned block asked to be held. The model is told to lead
# the note with one of these when it cannot honestly produce a
# number.
_HOLD_PREFIXES = ('not carried', 'cannot identify')


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


def collect(payload: dict) -> list:
    """Targets whose rows have no reading of their own on some
    service. Same population the provenance pass prices; what is left
    in it now is the ambiguous tail."""
    from scripts.trends_scrapers.coverage_gate import collect_missing
    (_stream_items, _headline_items, _total, _res, _base_n,
     cap_targets) = collect_missing(payload)
    return [t for t in cap_targets
            if 'cross_service' in (t.get('states') or ())]


def _hold_reason(blk: dict) -> str:
    """'' when the block is usable, otherwise why it is not."""
    if not isinstance(blk, dict):
        return 'no block returned'
    note = str(blk.get('note') or '').strip().lower()
    for p in _HOLD_PREFIXES:
        if note.startswith(p):
            return p
    try:
        v = int(blk.get('us_estimate') or 0)
    except (TypeError, ValueError):
        return 'unreadable value'
    if v <= 0:
        return 'returned no number'
    return ''


def _describe(t: dict) -> str:
    ident = t.get('identity') or {}
    bits = []
    for p in sorted(t['platforms']):
        rec = ident.get(p) or {}
        year = rec.get('year') or '?'
        cat = (rec.get('category') or '?').lower()
        bits.append(f'{p}:{cat} {year}')
    return ', '.join(bits)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true',
                    help='show the population and the prompt, spend nothing')
    ap.add_argument('--cap-usd', type=float, default=40.0)
    ap.add_argument('--model', default=_DEFAULT_MODEL)
    ap.add_argument('--max-tokens', type=int, default=_MAX_TOKENS)
    ap.add_argument('--checkpoint', default=_CHECKPOINT)
    ap.add_argument('--trail', default='/tmp/disambiguate_held_trail.json')
    ap.add_argument('--show-prompt', action='store_true')
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    # Bind the model before anything imports the estimator, so the
    # tier constant resolves to it; re-bind afterwards as well, because
    # an already-imported estimator would have missed the env var.
    os.environ['STREAM_ESTIMATES_MODEL_HI'] = args.model
    os.environ['STREAM_ESTIMATES_MAX_TOKENS'] = str(args.max_tokens)

    import trends_iq
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import coverage_gate as cg
    from scripts.trends_scrapers._spend_monitor import SpendMonitor

    se._MODEL_HI = args.model
    se._WEBSEARCH_MAX_TOKENS = int(args.max_tokens)

    target_date_iso = (datetime.now(timezone.utc).date()
                       - timedelta(days=1)).isoformat()

    payload = trends_iq.compute_view(dict(_FILTERS), force_refresh=True)
    targets = collect(payload)
    rows = sum(len(t['rows']) for t in targets)
    blocks = sum(len(t['platforms']) for t in targets)

    print(f'held titles                  : {len(targets)}')
    print(f'service readings to research : {blocks}')
    print(f'rendered rows behind them    : {rows}')
    print(f'model                        : {args.model}')
    print()
    for t in sorted(targets, key=lambda x: x['display_title'].lower()):
        print(f'  {t["display_title"]:32} {t["kind"]:10} {_describe(t)}')
    print()

    if args.show_prompt and targets:
        preview = cg._cap_research_items(se, targets[:1], with_identity=True,
                                          force_tier='hi', force_search=True)
        print('=' * 70)
        print(se._build_prompt(preview[0], target_date_iso=target_date_iso))
        print('=' * 70)
        print()

    if args.dry_run:
        print('dry-run: nothing researched')
        return 0

    done = _load_checkpoint(args.checkpoint)
    if done:
        print(f'resuming: {len(done)} title(s) already researched')
    todo = [t for t in targets if t['entry_key'] not in done]
    items = cg._cap_research_items(se, todo, with_identity=True,
                                    force_tier='hi', force_search=True)

    meter = SpendMonitor(cap_usd=args.cap_usd, prefix='disambiguate_held')
    results = dict(done)
    if items:
        cp = {'target_date_iso': f'{target_date_iso}-disambiguate-held',
              'kept_prior': {}, 'in_progress': {}, 'flushed_at': 0}
        fresh = se._research_all_batch(items,
                                        target_date_iso=target_date_iso,
                                        spend_monitor=meter,
                                        checkpoint_state=cp)
        if len(fresh) < se._BATCH_FALLBACK_MIN_SHARE * len(items):
            print(f'batch returned {len(fresh)}/{len(items)}; researching '
                  f'the remainder one at a time')
            rest = [it for it in items
                    if se._lookup_key(it['kind'], it['display_title'],
                                       it.get('artist') or '') not in fresh]
            fresh.update(se._research_all(rest,
                                           target_date_iso=target_date_iso,
                                           spend_monitor=meter))
        results.update(fresh)
        _save_checkpoint(args.checkpoint, results)
        print(f'researched {len(fresh)}/{len(items)} title(s) this pass')
    print()

    # Classify before merging, so the report can say WHY a block held
    # rather than only that it did. The merge itself refuses the same
    # blocks on its own credibility floor; this is the explanation,
    # not a second gate.
    verdicts: list[dict] = []
    for t in targets:
        res = results.get(t['entry_key'])
        by_plat = (res or {}).get('by_platform') or {}
        for p in sorted(t['platforms']):
            blk = by_plat.get(p)
            reason = _hold_reason(blk)
            rec = {'title': t['display_title'], 'kind': t['kind'],
                   'entry_key': t['entry_key'], 'service': p,
                   'held': bool(reason), 'reason': reason,
                   'identity': (t.get('identity') or {}).get(p) or {}}
            if isinstance(blk, dict):
                rec['value'] = blk.get('us_estimate')
                rec['note'] = str(blk.get('note') or '')[:400]
            verdicts.append(rec)

    stats = cg._merge_cap_platform_blocks(results, targets, target_date_iso)
    print(f'wrote {stats["blocks"]} service reading(s) into '
          f'{stats["entries"]} stored entry(ies)')

    not_carried = [v for v in verdicts if v['reason'] == 'not carried']
    unidentified = [v for v in verdicts if v['reason'] == 'cannot identify']
    other_held = [v for v in verdicts
                  if v['held'] and v['reason'] not in
                  ('not carried', 'cannot identify')]

    if not_carried:
        print()
        print(f'NOT CARRIED on the service the row claims '
              f'({len(not_carried)}). Nothing was written for these. A '
              f'service that does not have a title has no audience for '
              f'it, so the row should not be on that panel at all and '
              f'the panel source is what needs looking at:')
        for v in not_carried:
            print(f'   {v["title"]} on {v["service"]}: {v["note"][:180]}')
    if unidentified:
        print()
        print(f'STILL HELD, work not identifiable ({len(unidentified)}). '
              f'Correct outcome: these keep a number about their own '
              f'service and are researched again tonight:')
        for v in unidentified:
            print(f'   {v["title"]} on {v["service"]}: {v["note"][:180]}')
    if other_held:
        print()
        print(f'HELD for other reasons ({len(other_held)}):')
        for v in other_held:
            print(f'   {v["title"]} on {v["service"]}: {v["reason"]}')

    with open(args.trail, 'w') as fh:
        json.dump({'verdicts': verdicts, 'merge': stats['trail']}, fh,
                  indent=1, default=str)
    print()
    print(f'trail -> {args.trail}')
    print(f'spend: ${meter.total():.2f}')

    try:
        n = trends_iq.invalidate_live_compute_view_caches()
        print(f'purged {n} live cache entries')
    except Exception:
        logger.exception('cache purge failed (non-fatal)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
