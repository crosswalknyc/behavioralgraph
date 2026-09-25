"""Keep the streaming board file small enough to parse on the app host.

Why this exists
---------------
The dashboard reads `trends_iq_snapshots/latest/stream_estimates.json`
on every Trends IQ page load and parses the whole thing. That file was
16 MB on 2026-09-01 and 64 MB on 2026-09-25. Parsing the 64 MB version
takes a Python process to about 523 MB resident, and the app host has
512 MB. On 2026-09-25 at 17:10 UTC the streaming section missed its
90s compute budget for the first time and rendered as a placeholder,
because the parse had crossed into swap.

Twenty-six of those megabytes were reasoning prose the board never
renders: the per-item `method` and `day_specificity` paragraphs, the
per-item `sources` list, and the per-platform `note` inside
`by_platform`. The tooltip shows the count, the movement, the coverage
line and the as-of date (`_tiqAudienceChip` in templates/index.html);
none of the prose reaches a reader. It is an audit trail, and it is
kept, just not in the file the board has to parse.

What it does
------------
`split(payload)` returns two payloads from one:

  board      the original payload with the prose fields removed from
             every item. Every field the board or the window sum reads
             is untouched. This is what `stream_estimates.json` holds.

  reasoning  `{key: {method, day_specificity, sources,
             by_platform_note: {slug: note}}}` for every item that had
             any of them, plus the run's `target_date` / `generated_at`
             so a trail can be matched to its day. This is written as
             a sibling under the same prefix:

                 trends_iq_snapshots/{latest|YYYY-MM-DD}/
                     stream_estimates_reasoning.json

`merge(board, reasoning)` puts the prose back for an audit script that
wants the old single-file shape.

Where it runs
-------------
`_base.write_snapshot` calls `split` for `stream_estimates` right
before the S3 put, so the estimator itself keeps composing items with
the prose attached and every downstream pass that reads items in
memory (continuity, coherence, distinctness) still sees it. Only the
bytes at rest change. The window index (`stream_window_index`) is
written from the board payload, so its ETag check stays consistent.

Idempotent: splitting a payload that has already been split moves
nothing and returns an empty reasoning map. Never raises into a write.

Backfill
--------
`python3 -m scripts.trends_scrapers.stream_reasoning_split --backfill
--days 60` slims every existing dated file plus `latest/`, writing the
companion first and the slim board file second, and rebuilds the day's
window index so its ETag check passes. Nothing is deleted; the prose
moves.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)

REASONING_SUFFIX = '_reasoning'
COMPANION_SOURCE = 'stream_estimates' + REASONING_SUFFIX

# Item-level prose the board never renders.
ITEM_PROSE_FIELDS = ('method', 'day_specificity', 'sources')
# Per-platform prose inside `by_platform[slug]`.
PLATFORM_PROSE_FIELDS = ('note',)


def split(payload: dict) -> tuple[dict, dict]:
    """Return `(board_payload, reasoning_payload)`.

    Mutates nothing: both results are new top-level dicts, and each
    item that loses a field is copied before the field is dropped.
    """
    items = payload.get('items')
    if not isinstance(items, dict):
        return payload, {}

    board_items: dict[str, Any] = {}
    trail: dict[str, Any] = {}
    for key, it in items.items():
        if not isinstance(it, dict):
            board_items[key] = it
            continue
        rec: dict[str, Any] = {}
        slim = it
        copied = False
        for f in ITEM_PROSE_FIELDS:
            if f in it:
                if not copied:
                    slim = dict(it)
                    copied = True
                rec[f] = slim.pop(f)
        bp = it.get('by_platform')
        if isinstance(bp, dict):
            notes: dict[str, Any] = {}
            new_bp: Optional[dict] = None
            for slug, blk in bp.items():
                if isinstance(blk, dict) and any(f in blk for f in PLATFORM_PROSE_FIELDS):
                    if new_bp is None:
                        new_bp = dict(bp)
                    b2 = dict(blk)
                    for f in PLATFORM_PROSE_FIELDS:
                        if f in b2:
                            notes.setdefault(slug, {})[f] = b2.pop(f)
                    new_bp[slug] = b2
            if new_bp is not None:
                if not copied:
                    slim = dict(it)
                    copied = True
                slim['by_platform'] = new_bp
                rec['by_platform_note'] = {
                    s: (v['note'] if set(v) == {'note'} else v)
                    for s, v in notes.items()}
        board_items[key] = slim
        if rec:
            trail[key] = rec

    board = dict(payload)
    board['items'] = board_items
    if not trail:
        return board, {}
    reasoning = {
        'source': COMPANION_SOURCE,
        'companion_of': 'stream_estimates',
        'target_date': payload.get('target_date'),
        'generated_at': payload.get('generated_at'),
        'count': len(trail),
        'items': trail,
    }
    return board, reasoning


def merge(board: dict, reasoning: Optional[dict]) -> dict:
    """Return a copy of `board` with the prose restored from `reasoning`."""
    out = dict(board)
    items = dict(board.get('items') or {})
    trail = (reasoning or {}).get('items') or {}
    for key, rec in trail.items():
        it = items.get(key)
        if not isinstance(it, dict):
            continue
        it = dict(it)
        for f in ITEM_PROSE_FIELDS:
            if f in rec:
                it[f] = rec[f]
        notes = rec.get('by_platform_note') or {}
        if notes:
            bp = dict(it.get('by_platform') or {})
            for slug, n in notes.items():
                blk = dict(bp.get(slug) or {})
                if isinstance(n, dict):
                    blk.update(n)
                else:
                    blk['note'] = n
                bp[slug] = blk
            it['by_platform'] = bp
        items[key] = it
    out['items'] = items
    return out


def write_companion(s3: Any, bucket: str, key: str, reasoning: dict) -> int:
    """Write `reasoning` to `key`, MERGED over whatever companion is
    already there. Returns the byte count written.

    Every pass that rewrites a snapshot (coherence, distinctness, a
    one-off re-price of a few hundred rows) loads the board file, which
    no longer carries prose, patches its rows, and writes back. In
    memory that payload only holds prose for the rows the pass itself
    reasoned, so a plain overwrite here would shrink the trail to those
    rows. That happened on 2026-09-25 17:33 UTC: a 288-row Wattpad
    re-price wrote back seconds after the first slimming and the
    `latest/` companion went from 27,664 trails to 288. Newer trails
    win per key; older ones are kept.
    """
    merged = dict(reasoning)
    try:
        prev = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body']
                          .read().decode('utf-8'))
        items = dict(prev.get('items') or {})
        items.update(reasoning.get('items') or {})
        merged['items'] = items
        merged['count'] = len(items)
    except Exception:
        pass
    body = json.dumps(merged, ensure_ascii=False).encode('utf-8')
    s3.put_object(Bucket=bucket, Key=key, Body=body,
                  ContentType='application/json')
    return len(body)


# ---------------------------------------------------------------------------
# Backfill of files already on S3
# ---------------------------------------------------------------------------

def _backfill_prefix(s3: Any, bucket: str, prefix: str, *,
                     date_iso: Optional[str], dry_run: bool) -> Optional[dict]:
    """Slim `{prefix}stream_estimates.json` in place. Companion first,
    then the board file, then the window index for a dated day."""
    key = f'{prefix}stream_estimates.json'
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except Exception:
        return None
    raw = obj['Body'].read()
    payload = json.loads(raw.decode('utf-8'))
    board, reasoning = split(payload)
    if not reasoning:
        return {'key': key, 'before': len(raw), 'after': len(raw),
                'moved': 0, 'skipped': True}
    ckey = f'{prefix}{COMPANION_SOURCE}.json'
    body_b = json.dumps(board, ensure_ascii=False).encode('utf-8')

    result = {'key': key, 'before': len(raw), 'after': len(body_b),
              'moved': reasoning['count'], 'skipped': False}
    if dry_run:
        return result
    write_companion(s3, bucket, ckey, reasoning)
    put = s3.put_object(Bucket=bucket, Key=key, Body=body_b,
                        ContentType='application/json',
                        **({'CacheControl': 'public, max-age=60'}
                           if date_iso is None else {}))
    if date_iso:
        try:
            from . import stream_window_index as _swi
            _swi.write_index(date_iso, board, source_etag=put.get('ETag'),
                             source_bytes=len(body_b), s3=s3)
        except Exception:
            logger.exception('stream window index: rebuild for %s '
                             'failed (non-fatal)', date_iso)
    return result


def backfill(days: int, *, include_latest: bool = True,
             dry_run: bool = False) -> list[dict]:
    from . import _base
    from . import stream_window_index as _swi
    s3 = _base._s3_client()
    out: list[dict] = []
    if include_latest:
        r = _backfill_prefix(s3, _base.S3_BUCKET, _base.S3_LATEST_PREFIX,
                             date_iso=None, dry_run=dry_run)
        if r:
            out.append(r)
    for day in _swi.archive_days(days):
        r = _backfill_prefix(s3, _base.S3_BUCKET,
                             _base.S3_DATED_PREFIX.format(date=day),
                             date_iso=day, dry_run=dry_run)
        if r:
            out.append(r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--backfill', action='store_true')
    ap.add_argument('--days', type=int, default=60)
    ap.add_argument('--no-latest', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    if not args.backfill:
        ap.print_help()
        return 2
    results = backfill(args.days, include_latest=not args.no_latest,
                       dry_run=args.dry_run)
    before = after = 0
    for r in results:
        before += r['before']
        after += r['after']
        tag = 'already slim' if r['skipped'] else f"moved {r['moved']:,} trails"
        print(f"{r['key']:60s} {r['before']/1e6:6.1f} MB -> "
              f"{r['after']/1e6:6.1f} MB  {tag}")
    print(f"\n{len(results)} files, {before/1e6:.0f} MB -> {after/1e6:.0f} MB"
          f"{'  (dry run, nothing written)' if args.dry_run else ''}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
