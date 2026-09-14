"""Delta scorer for lens_relevance snapshots.

Reads the current `lens_scores.json`, collects the current item set,
identifies items that have NO score for one or more configured lenses,
and scores ONLY those gaps. Existing scores are preserved verbatim.

Motivation: adding new item kinds (like `game`) or fresh scraper
sources (like FAST title pools) shouldn't force a full re-score of
every existing item for every lens. The existing per-kind cutoffs
already reflect the persona's calibrated distribution, so preserving
them is the right default. Only fill the gaps.

Usage on Hetzner (where ANTHROPIC_API_KEY is present):

    python3 -m scripts.trends_scrapers.score_new_items_only

Optional flags mirror _bedrock_scorer:
    --backend anthropic|bedrock   default anthropic
    --no-upload                   dump to /tmp/lens_scores.new.json only
    --dry-run                     show gap counts and exit
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import boto3

_HERE = os.path.dirname(os.path.abspath(__file__))
_BGWEBAPP = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _BGWEBAPP not in sys.path:
    sys.path.insert(0, _BGWEBAPP)

from scripts.trends_scrapers import lens_relevance as lr  # noqa: E402
from scripts.trends_scrapers._bedrock_scorer import (      # noqa: E402
    BedrockAnthropicClient, _upload,
)

logger = logging.getLogger('lens_gap_scorer')


def _build_client(backend: str):
    if backend == 'bedrock':
        return BedrockAnthropicClient()
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY not set (or pass --backend bedrock)')
    import anthropic  # type: ignore
    return anthropic.Anthropic(api_key=api_key)


def _load_prior() -> dict:
    s3 = boto3.client('s3')
    try:
        obj = s3.get_object(Bucket=lr._S3_BUCKET,
                             Key=f'{lr._S3_LATEST}lens_scores.json')
        return json.loads(obj['Body'].read())
    except Exception as e:
        logger.warning('no prior lens_scores.json (%s), starting from empty', e)
        return {}


def run(*, backend: str = 'anthropic', dry_run: bool = False,
         no_upload: bool = False) -> dict[str, Any]:
    prior = _load_prior()
    prior_items = prior.get('items') or {}

    items = lr._collect_all_items()
    logger.info('collected %d items across snapshots', len(items))

    # Seed the combined map from prior scores (verbatim), so anything
    # that already has a score for a lens keeps it.
    combined: dict[str, dict] = {}
    for it in items:
        row: dict[str, Any] = {
            'kind':   it['kind'],
            'title':  it['title'],
            'scores': {},
            'why':    {},
        }
        if it.get('artist'):
            row['artist'] = it['artist']
        prior_row = prior_items.get(it['key']) or {}
        for lens_id, s in (prior_row.get('scores') or {}).items():
            row['scores'][lens_id] = int(s)
        for lens_id, w in (prior_row.get('why') or {}).items():
            row['why'][lens_id] = str(w)
        combined[it['key']] = row

    # Figure out per-lens gaps. `_LENSES` is the source of truth for
    # which lenses should end up in `lenses[]` on the payload.
    lens_ids = [l['id'] for l in lr._LENSES]
    gaps: dict[str, list[dict]] = {lid: [] for lid in lens_ids}
    for it in items:
        row = combined[it['key']]
        for lens_id in lens_ids:
            if lens_id not in row['scores']:
                gaps[lens_id].append(it)

    logger.info('gap counts per lens:')
    for lens_id in lens_ids:
        by_kind: dict[str, int] = {}
        for it in gaps[lens_id]:
            by_kind[it['kind']] = by_kind.get(it['kind'], 0) + 1
        logger.info('  %s: %d missing (%s)',
                     lens_id, len(gaps[lens_id]),
                     ', '.join(f'{k}={v}' for k, v in sorted(by_kind.items())))

    if dry_run:
        return {'combined': combined, 'gaps': {k: len(v) for k, v in gaps.items()},
                 'dry_run': True}

    # Score each gap.
    client = _build_client(backend)
    lenses_by_id = {l['id']: l for l in lr._LENSES}
    for lens_id, gap_items in gaps.items():
        if not gap_items:
            logger.info('  %s: no gap, skipping', lens_id)
            continue
        lens = lenses_by_id.get(lens_id)
        if not lens:
            logger.warning('  %s: not registered in _LENSES, skipping', lens_id)
            continue
        logger.info('=== scoring %s (%d gap items) ===', lens_id, len(gap_items))
        t0 = time.time()
        out = lr._score_lens(client, lens, gap_items)
        logger.info('=== %s done: scored %d/%d gaps in %.1fs ===',
                     lens_id, len(out), len(gap_items), time.time() - t0)
        for it in gap_items:
            hit = out.get(it['key'])
            if not hit:
                continue
            combined[it['key']]['scores'][lens_id] = int(hit['score'])
            if hit.get('why'):
                combined[it['key']]['why'][lens_id] = str(hit['why'])

    # Drop rows without any scores and empty why blocks.
    final: dict[str, dict] = {}
    for k, row in combined.items():
        if not row.get('why'):
            row.pop('why', None)
        if row.get('scores'):
            final[k] = row

    active_lens_ids: set[str] = set()
    for row in final.values():
        active_lens_ids.update(row.get('scores', {}).keys())
    lens_meta = [
        {'id': l['id'], 'label': l['label'],
         'emoji': l['emoji'], 'description': l['description']}
        for l in lr._LENSES if l['id'] in active_lens_ids
    ]
    cutoffs = lr._compute_cutoffs(final, [l['id'] for l in lens_meta])

    result = {
        'source':        'lens_scores',
        'kind':          'meta',
        'fetched_at':    datetime.now(timezone.utc).isoformat(),
        'generated_at':  datetime.now(timezone.utc).isoformat(),
        'items':         final,
        'lenses':        lens_meta,
        'cutoffs':       cutoffs,
        'count':         len(final),
    }
    return result


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    ap = argparse.ArgumentParser()
    ap.add_argument('--backend', choices=['anthropic', 'bedrock'],
                     default='anthropic')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-upload', action='store_true')
    args = ap.parse_args()

    result = run(backend=args.backend, dry_run=args.dry_run,
                  no_upload=args.no_upload)
    if args.dry_run:
        print(f"[dry-run] gaps = {result.get('gaps')}", file=sys.stderr)
        return

    if args.no_upload:
        with open('/tmp/lens_scores.new.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"[no-upload] wrote /tmp/lens_scores.new.json count={result['count']}",
              file=sys.stderr)
        return

    key, backup = _upload(result)
    print(f"UPLOADED: {key}", file=sys.stderr)
    if backup:
        print(f"BACKUP:   {backup}", file=sys.stderr)
    print(f"count={result['count']} lenses={[l['id'] for l in result['lenses']]}",
           file=sys.stderr)

    # Same live compute_view cache invalidation as _bedrock_scorer.
    try:
        import pathlib as _pathlib
        _root = _pathlib.Path(__file__).resolve().parent.parent.parent
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        from trends_iq import invalidate_live_compute_view_caches  # noqa: E402
        n = invalidate_live_compute_view_caches()
        print(f"INVALIDATED: {n} live compute_view cache entries",
               file=sys.stderr)
    except Exception as e:
        print(f"WARN: compute_view cache invalidation failed: {e}",
               file=sys.stderr)


if __name__ == '__main__':
    main()
