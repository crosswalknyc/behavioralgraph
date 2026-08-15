#!/usr/bin/env python3
"""
Fix duplicate-rank defect on 2026-07-24, 07-25, 07-26 snapshots.

A scraper bug on those three days assigned rank per-rail rather than
globally. As a result multiple titles ended up at the same rank
(rank=1 for two different titles, etc.). Every source (peacock,
reelshort, dramabox, goodshort, netshort) is affected.

Fix strategy: for each affected day + source, keep the BEST rank per
title (in case a title appeared on multiple rails), then renumber
titles 1..N by the retained rank (breaking ties by rail_position).
Non-numeric ranks are left alone. Serialisation is byte-preserving
otherwise: field order and title metadata are untouched.

Run in dry-run mode first:
    python3 scripts/microdramas_scrapers/fix_dup_ranks.py --dry-run

Actually write:
    python3 scripts/microdramas_scrapers/fix_dup_ranks.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

import boto3

AFFECTED_DAYS = ['2026-07-24', '2026-07-25', '2026-07-26']
SOURCES = ['peacock', 'reelshort', 'dramabox', 'goodshort', 'netshort']

BUCKET = 'dashboard-inputs'


def _identity(title: dict) -> str:
    """Stable identity key for dedupe. Same as the pipeline convention."""
    return (str(title.get('book_id') or title.get('key')
                 or title.get('title') or title.get('series') or '')
            .strip().lower())


def _renumber(titles: list[dict]) -> list[dict]:
    """Return `titles` sorted by (best_rank asc, rail_position asc) with
    every rank replaced by its position in the sort."""
    # Dedupe by identity, keeping the best rank
    by_id: dict[str, dict] = {}
    for t in titles:
        i = _identity(t)
        if not i:
            continue
        prev = by_id.get(i)
        cur_rank = t.get('rank') if isinstance(t.get('rank'), int) else 10_000
        if prev is None:
            by_id[i] = t
        else:
            prev_rank = prev.get('rank') if isinstance(prev.get('rank'), int) else 10_000
            if cur_rank < prev_rank:
                by_id[i] = t
    ordered = list(by_id.values())
    ordered.sort(key=lambda t: (
        t.get('rank') if isinstance(t.get('rank'), int) else 10_000,
        t.get('rail_position') if isinstance(t.get('rail_position'), int) else 999,
    ))
    for i, t in enumerate(ordered, start=1):
        t['rank'] = i
    return ordered


def _fix_snapshot(s3, key: str, dry_run: bool) -> tuple[int, int, int]:
    """Return (before_titles, before_unique_ranks, after_titles)."""
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    snap = json.loads(obj['Body'].read())
    titles = snap.get('titles') or []
    n_before = len(titles)
    ranks = [t.get('rank') for t in titles if isinstance(t.get('rank'), int)]
    dupe_ranks = [r for r, c in Counter(ranks).items() if c > 1]
    if not dupe_ranks:
        return (n_before, len(set(ranks)), n_before)  # no change needed

    fixed = _renumber(titles)
    snap['titles'] = fixed
    snap['rank_normalized'] = True
    snap.setdefault('_notes', []).append(
        f'ranks renumbered 1..{len(fixed)} to fix scraper duplicate-rank '
        f'defect (dupes at rank(s) {sorted(dupe_ranks)})'
    )

    if not dry_run:
        s3.put_object(
            Bucket=BUCKET, Key=key,
            Body=json.dumps(snap, indent=2).encode('utf-8'),
            ContentType='application/json',
            CacheControl='no-cache',
        )
    return (n_before, len(ranks) - len(dupe_ranks), len(fixed))


def _bump_cache_epoch(s3) -> None:
    """Force running Flask workers to invalidate their in-process caches."""
    key = 'microdramas_iq/cache/epoch.json'
    try:
        cur = json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read())
        epoch = int(cur.get('epoch', 0)) + 1
    except Exception:
        epoch = 1
    s3.put_object(
        Bucket=BUCKET, Key=key,
        Body=json.dumps({'epoch': epoch}).encode('utf-8'),
        ContentType='application/json',
        CacheControl='no-cache',
    )
    print(f'  bumped cache epoch -> {epoch}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--days', nargs='+', default=AFFECTED_DAYS)
    ap.add_argument('--sources', nargs='+', default=SOURCES)
    args = ap.parse_args()

    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')

    print(f'Fixing duplicate ranks on {len(args.days)} days x '
          f'{len(args.sources)} sources = {len(args.days) * len(args.sources)} snapshots')
    print(f'Dry-run: {args.dry_run}\n')

    total = 0
    fixed = 0
    for day in args.days:
        for source in args.sources:
            key = f'microdramas_iq/snapshots/{day}/{source}.json'
            try:
                nb, nu, na = _fix_snapshot(s3, key, args.dry_run)
            except s3.exceptions.NoSuchKey:
                print(f'  {day}/{source}.json: MISSING (skipped)')
                continue
            total += 1
            if nb != na or nb != nu:
                fixed += 1
                print(f'  {day}/{source}.json: {nb} titles, {nu} unique ranks '
                      f'-> {na} titles (renumbered 1..{na})')
            else:
                print(f'  {day}/{source}.json: already clean ({nb} titles, unique)')

    print(f'\nProcessed {total} snapshots, fixed {fixed}')

    if not args.dry_run and fixed > 0:
        _bump_cache_epoch(s3)

    return 0


if __name__ == '__main__':
    sys.exit(main())
