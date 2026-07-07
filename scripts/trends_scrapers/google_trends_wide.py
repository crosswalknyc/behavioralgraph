"""
Wide-pool Google Trends daily snapshot.

Solves the fundamental data problem the Movers panel hits: the raw
Google Trends RSS gives only ~10 terms per day per geo, and consecutive
days have near-total turnover. That means Climbing / Sustained buckets
(which need day-over-day overlap) literally cannot populate from the
single-geo US feed.

This scraper pulls the RSS from US + 15 large states in parallel,
UNIONs the results into a merged daily snapshot with 100-200 unique
terms, and writes it to:

    s3://dashboard-inputs/blue_iq/trends_rss_wide/v1/{YYYY-MM-DD}.json

The movers logic reads this wide pool preferentially and falls back
to the narrow US-only pool when the wide file doesn't exist yet.
With ~200 terms/day, day-over-day overlap probability jumps from ~0%
to ~15-30%, which is enough for Climbing / Sustained to fire.

Run via the daily scraper cron alongside the retailer/social scrapers.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)

# 15 largest states by population. Larger states means richer trend
# lists; we skip small states because their RSS is often nearly empty.
WIDE_GEOS = [
    'US', 'US-CA', 'US-TX', 'US-FL', 'US-NY', 'US-PA',
    'US-IL', 'US-OH', 'US-GA', 'US-NC', 'US-MI', 'US-NJ',
    'US-VA', 'US-WA', 'US-AZ', 'US-MA',
]

_BUCKET = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
_WIDE_PREFIX = 'blue_iq/trends_rss_wide/v1/'


def _s3():
    import boto3  # type: ignore
    return boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')


def _fetch_one_geo(geo: str) -> list[dict]:
    """Fetch the RSS for one geo via the existing external_signals path.
    Returns the parsed rows or an empty list on any failure."""
    try:
        import external_signals  # type: ignore
    except Exception as e:
        logger.warning("external_signals import failed: %s", e)
        return []
    try:
        # This call ALSO writes today's single-geo snapshot to S3 as a
        # side effect, so we're not duplicating work.
        rows = external_signals._trends_fetch_today(geo) or []
        # Tag each row with the geo it came from so we can trace it back
        # when a term shows up.
        for r in rows:
            r['_geo'] = geo
        logger.info("wide_pull %s: %d terms", geo, len(rows))
        return rows
    except Exception as e:
        logger.warning("wide_pull %s: %s", geo, e)
        return []


def build_wide_pool() -> dict:
    """Parallel-fetch all WIDE_GEOS, union the terms, write to S3."""
    all_rows: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch_one_geo, g): g for g in WIDE_GEOS}
        for fut in concurrent.futures.as_completed(futs):
            all_rows.extend(fut.result() or [])

    # Union by term (case-insensitive). Keep the MAX score seen across
    # geos; record contributing geos + peak-geo. This is the daily
    # widened trending pool.
    by_term: dict[str, dict] = {}
    for r in all_rows:
        term = (r.get('term') or '').strip()
        if not term:
            continue
        key = term.lower()
        score = int(r.get('score') or 0)
        entry = by_term.get(key)
        if entry is None:
            by_term[key] = {
                'term':      term,
                'score':     score,
                'related':   list(r.get('related') or [])[:6],
                'geos':      [r.get('_geo')] if r.get('_geo') else [],
                'peak_geo':  r.get('_geo') or '',
            }
        else:
            entry['geos'].append(r.get('_geo'))
            if score > entry['score']:
                entry['score']    = score
                entry['peak_geo'] = r.get('_geo') or entry['peak_geo']
                entry['term']     = term
            # Merge related, dedupe
            seen = {x.lower() for x in entry['related']}
            for rel in r.get('related') or []:
                if rel.lower() not in seen:
                    entry['related'].append(rel)
                    seen.add(rel.lower())
            entry['related'] = entry['related'][:6]

    # De-dup geos list; keep the count so we can rank by geographic breadth.
    for e in by_term.values():
        e['geos'] = sorted(set(g for g in e['geos'] if g))
        e['geo_count'] = len(e['geos'])

    merged = sorted(by_term.values(), key=lambda x: (-x['score'], -x['geo_count']))
    today_iso = datetime.now(timezone.utc).date().isoformat()

    payload = {
        'date':         today_iso,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'geos_pulled':  WIDE_GEOS,
        'geos_ok':      sorted({r.get('_geo') for r in all_rows if r.get('_geo')}),
        'unique_terms': len(merged),
        'terms':        merged,
    }

    key = f'{_WIDE_PREFIX}{today_iso}.json'
    try:
        _s3().put_object(
            Bucket=_BUCKET,
            Key=key,
            Body=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            ContentType='application/json',
            CacheControl='public, max-age=300',
        )
        logger.info("wide_pull complete: %d unique terms across %d geos -> s3://%s/%s",
                     len(merged), len(payload['geos_ok']), _BUCKET, key)
    except Exception as e:
        logger.exception("wide_pull s3 write failed: %s", e)

    return payload


def fetch() -> dict:
    """Scraper-orchestrator entry point. Runs the wide pull and
    normalizes into the standard scraper snapshot shape."""
    payload = build_wide_pool()
    return {
        'national':   payload.get('terms') or [],
        'unique_terms': payload.get('unique_terms'),
        'geos_ok':    payload.get('geos_ok'),
    }


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    result = build_wide_pool()
    print(f"wide_pool: {result['unique_terms']} unique terms across "
           f"{len(result['geos_ok'])}/{len(WIDE_GEOS)} geos")
