"""Poster art resolved on the nightly run, not per request.

Why this exists
---------------
Streaming rows carry poster art that no scraper captures, so the read
side resolves it at render time through TVMaze, then Wikipedia, then
the iTunes Search API. Each lookup is up to four calls to three
outside services, around 250ms, and the answer was kept only in
process memory. A freshly deployed worker therefore re-resolved the
whole board on its first request: 279 lookups, 70.8s of work across
the pool, 11.9s on the section's own clock.

Artwork does not change between one request and the next. It barely
changes between one month and the next. So the resolving moves to the
nightly run and the read side loads the answers:

    trends_iq_snapshots/latest/streaming_posters.json

A title that appears after the nightly run still resolves live on
first sight, exactly as before, so nothing waits a day for its art.

Shape
-----
One record per lookup, holding the same key the in-process cache uses
(the normalised title and the wanted kind) and the same value:

    {"entries": [{"t": "severance", "k": "tv",
                  "u": "https://...", "r": "2026-09-21",
                  "s": "2026-09-21"}]}

`r` is when the art was last resolved and `s` when the title was last
on the board. A record list rather than an object keyed by title,
because a normalised title can contain any character and escaping it
into a JSON key would only be a way to get it wrong.

Misses are recorded too, as an empty `u`. That is most of the saving:
a title with no art anywhere is the one that pays for all three
sources before giving up, and the read side already treats a cached
miss as final for the life of the worker.

Freshness
---------
A record is re-resolved once it passes `max_age_days`, oldest first
and capped per run, so the board is revisited about monthly without
any night paying to resolve all of it. A record whose title has been
off the board for `prune_days` is dropped.

Nothing here writes a rendered value: art is a URL on a row, and the
nightly resolution runs the same chain, against the same sources, that
the read side would have run itself.

No clickstream is involved.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

S3_BUCKET = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_LATEST_PREFIX = 'trends_iq_snapshots/latest/'

CACHE_BASENAME = 'streaming_posters'
CACHE_VERSION = 1

# Re-resolve a record once it is older than this, so art that changed
# upstream is picked up without re-resolving the board every night.
DEFAULT_MAX_AGE_DAYS = 30
# Ceiling on how many stale records one run re-resolves, so a night
# where everything expires at once does not turn into a full rebuild.
DEFAULT_MAX_REFRESH = 400
# Drop a record whose title has been off the board this long.
DEFAULT_PRUNE_DAYS = 90


def cache_key() -> str:
    return f'{S3_LATEST_PREFIX}{CACHE_BASENAME}.json'


def _s3_client():
    import boto3  # type: ignore
    from botocore.config import Config  # type: ignore
    region = os.environ.get('AWS_REGION') or 'us-east-2'
    return boto3.client('s3', region_name=region,
                        config=Config(max_pool_connections=64))


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _older_than(stamp: Optional[str], days: int) -> bool:
    if not stamp:
        return True
    try:
        return (datetime.now(timezone.utc).date()
                - date.fromisoformat(stamp)).days > days
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read(s3: Any = None) -> Optional[dict]:
    """Load the published cache, or None when it is absent, unreadable
    or written by a different version of this module."""
    try:
        s3 = s3 or _s3_client()
        resp = s3.get_object(Bucket=S3_BUCKET, Key=cache_key())
        data = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get('cache_version') != CACHE_VERSION:
        return None
    if not isinstance(data.get('entries'), list):
        return None
    return data


def seed(target: dict, payload: Optional[dict]) -> int:
    """Fill `target` with the published answers.

    `target` is the read side's in-process cache, keyed exactly as it
    keys itself: `(normalised_title_lowercased, 'film' | 'tv')`. A key
    already present is left alone, so a live resolution this process
    already made always wins over the stored one.

    Returns how many keys were added.
    """
    if not isinstance(payload, dict):
        return 0
    added = 0
    for rec in payload.get('entries') or []:
        if not isinstance(rec, dict):
            continue
        t = rec.get('t')
        k = rec.get('k')
        if not isinstance(t, str) or k not in ('film', 'tv'):
            continue
        key = (t, k)
        if key in target:
            continue
        target[key] = rec.get('u') or ''
        added += 1
    return added


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(*, max_age_days: int = DEFAULT_MAX_AGE_DAYS,
          max_refresh: int = DEFAULT_MAX_REFRESH,
          prune_days: int = DEFAULT_PRUNE_DAYS,
          s3: Any = None) -> dict:
    """Resolve every title currently on the board and publish the
    answers.

    Carries yesterday's answers forward so a run only pays for titles
    that are new or due a refresh, then renders the streaming section
    once, which resolves whatever is left through the read side's own
    lookup chain. What gets written is that chain's output, so the
    stored answer and a live one cannot disagree.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
    import trends_iq as T  # noqa: E402

    s3 = s3 or _s3_client()
    prior = read(s3=s3) or {}
    prior_recs = {}
    for rec in prior.get('entries') or []:
        if isinstance(rec, dict) and isinstance(rec.get('t'), str) \
                and rec.get('k') in ('film', 'tv'):
            prior_recs[(rec['t'], rec['k'])] = rec

    today = _today()

    # Oldest first, so the refresh cap spends itself on the records
    # that have gone longest without a look.
    stale = sorted(
        (k for k, r in prior_recs.items()
         if _older_than(r.get('r'), max_age_days)),
        key=lambda k: prior_recs[k].get('r') or '')
    refreshing = set(stale[:max(0, max_refresh)])

    # Seed the read side with everything we are keeping, then render.
    # Anything not seeded resolves live during the render.
    T._WIKI_POSTER_CACHE.clear()
    carried = 0
    for key, rec in prior_recs.items():
        if key in refreshing:
            continue
        T._WIKI_POSTER_CACHE[key] = rec.get('u') or ''
        carried += 1
    # The render would otherwise load last night's answers itself and
    # hand back the very records we just dropped for a refresh, so tell
    # it the cache is already seeded. This one is ours to fill.
    T._POSTER_CACHE_SEEDED = True

    before = set(T._WIKI_POSTER_CACHE)
    # Rendering the section is what walks the current board and asks
    # for each title's art. The payload itself is thrown away.
    T._fetch_streaming_trending(None, 1, keywords=None)
    resolved_now = set(T._WIKI_POSTER_CACHE) - before
    on_board = set(T._WIKI_POSTER_CACHE)

    entries = []
    kept_stale = 0
    for key, url in T._WIKI_POSTER_CACHE.items():
        prev = prior_recs.get(key) or {}
        entries.append({
            't': key[0],
            'k': key[1],
            'u': url or '',
            'r': today if (key in resolved_now or key in refreshing)
                 else (prev.get('r') or today),
            's': today,
        })
    # Records for titles that have left the board stay until they have
    # been gone `prune_days`, so a title that cycles back does not pay
    # to be resolved again.
    for key, rec in prior_recs.items():
        if key in on_board:
            continue
        if _older_than(rec.get('s'), prune_days):
            continue
        entries.append(dict(rec))
        kept_stale += 1

    entries.sort(key=lambda r: (r['t'], r['k']))
    payload = {
        'cache_version': CACHE_VERSION,
        'built_at': datetime.now(timezone.utc).isoformat(),
        'entry_count': len(entries),
        'with_art': sum(1 for r in entries if r['u']),
        'entries': entries,
    }

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3.put_object(Bucket=S3_BUCKET, Key=cache_key(), Body=body,
                  ContentType='application/json',
                  CacheControl='public, max-age=60')
    logger.info('streaming poster cache: wrote s3://%s/%s '
                '(%.2f MB, %d entries, %d with art)',
                S3_BUCKET, cache_key(), len(body) / 1e6,
                len(entries), payload['with_art'])

    return {
        'key': cache_key(),
        'entries': len(entries),
        'with_art': payload['with_art'],
        'carried': carried,
        'resolved_now': len(resolved_now),
        'refreshed': len(refreshing),
        'kept_off_board': kept_stale,
        'bytes': len(body),
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    ap = argparse.ArgumentParser(
        description=('Resolve streaming poster art on the nightly run '
                     'so the read side loads it instead.'))
    ap.add_argument('--max-age-days', type=int, default=DEFAULT_MAX_AGE_DAYS)
    ap.add_argument('--max-refresh', type=int, default=DEFAULT_MAX_REFRESH)
    ap.add_argument('--prune-days', type=int, default=DEFAULT_PRUNE_DAYS)
    args = ap.parse_args()
    print(json.dumps(build(max_age_days=args.max_age_days,
                           max_refresh=args.max_refresh,
                           prune_days=args.prune_days), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
