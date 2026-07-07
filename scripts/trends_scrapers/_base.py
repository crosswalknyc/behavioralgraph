"""
Shared infrastructure for Trends IQ scrapers.

Every daily scraper writes a normalized snapshot JSON to S3 at

    s3://dashboard-inputs/trends_iq_snapshots/latest/{source}.json

and a date-stamped copy at

    s3://dashboard-inputs/trends_iq_snapshots/{YYYY-MM-DD}/{source}.json

The `latest/` prefix is the one the app reads at request time (via
`trends_iq._read_snapshot`); the date-stamped copies exist so we can look
back at what was trending on a given day without re-scraping.

Snapshot shape (kind='social'):

    {
      "source":     "x",
      "kind":       "social",
      "label":      "X",
      "fetched_at": "2026-07-07T09:00:00+00:00",
      "national":   [{ "rank": 1, "topic": "...", "url": "...", ... }, ...],
      "by_state":   { "California": [...], ... },     # optional
      "by_dma":     { "New York": [...], ... },       # optional
      "error":      null | "reason"
    }

Snapshot shape (kind='retailer'):

    {
      "source":     "bestbuy",
      "kind":       "retailer",
      "label":      "Best Buy",
      "fetched_at": "...",
      "national":   [{ "rank": 1, "name": "...", "url": "...", "image": "...", "price": "..." }, ...],
      "categories": [ { "label": "Electronics", "items": [...] }, ... ],  # optional
      "error":      null | "reason"
    }
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import requests

logger = logging.getLogger(__name__)

# curl_cffi is a drop-in requests replacement that impersonates real
# browsers at the TLS layer (JA3 fingerprint = actual Chrome). Most modern
# bot-detection stacks (Akamai, PerimeterX / HUMAN, Cloudflare Turnstile,
# DataDome) key off the TLS fingerprint before they even look at the
# User-Agent header, so a stock `requests` client is trivially detected
# even with perfect headers. curl_cffi solves that for ~90% of retailer
# sites. Falls through to plain `requests` if the package isn't installed.
try:
    from curl_cffi import requests as _cc_requests  # type: ignore
    _HAS_CURL_CFFI = True
except ImportError:
    _cc_requests = None  # type: ignore
    _HAS_CURL_CFFI = False


S3_BUCKET = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_LATEST_PREFIX = 'trends_iq_snapshots/latest/'
S3_DATED_PREFIX  = 'trends_iq_snapshots/{date}/'

DEFAULT_HTTP_TIMEOUT_S = 20
DEFAULT_RETRY_COUNT    = 3
DEFAULT_RETRY_SLEEP_S  = 4

# Rotating user agents. Bot-detection heuristics get tripped by identical
# UAs across every request; rotating between three current desktop browsers
# is enough to stay under the radar on the sites that only do basic UA
# fingerprinting. Sites with real bot protection (Walmart, Target) still
# need Playwright regardless.
_UA_POOL = [
    ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
     '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'),
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
     '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'),
    ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) '
     'Gecko/20100101 Firefox/125.0'),
]


def browser_headers(*, referer: str = '', extra: dict | None = None) -> dict:
    """Realistic desktop-browser headers. Overrideable via `extra` for
    site-specific niceties (Sephora wants a Sephora referer, Nike wants
    Accept-Language=en-US, etc.)."""
    h = {
        'User-Agent':          random.choice(_UA_POOL),
        'Accept':              ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                                 'image/avif,image/webp,image/apng,*/*;q=0.8'),
        'Accept-Language':     'en-US,en;q=0.9',
        # Explicitly drop `br` (brotli) - requests doesn't auto-decompress
        # brotli unless the `brotli` package is installed, and we don't
        # want to introduce a hard dep just for one encoding.
        'Accept-Encoding':     'gzip, deflate',
        'Cache-Control':       'no-cache',
        'Sec-Ch-Ua':           '"Chromium";v="124", "Not-A.Brand";v="99"',
        'Sec-Ch-Ua-Mobile':    '?0',
        'Sec-Ch-Ua-Platform':  '"macOS"',
        'Sec-Fetch-Dest':      'document',
        'Sec-Fetch-Mode':      'navigate',
        'Sec-Fetch-Site':      'none',
        'Sec-Fetch-User':      '?1',
        'Upgrade-Insecure-Requests': '1',
    }
    if referer:
        h['Referer'] = referer
        h['Sec-Fetch-Site'] = 'same-origin'
    if extra:
        h.update(extra)
    return h


def http_get(url: str, *, timeout: int = DEFAULT_HTTP_TIMEOUT_S,
             retries: int = DEFAULT_RETRY_COUNT,
             headers: dict | None = None,
             cookies: dict | None = None,
             impersonate: str = 'chrome124') -> Optional[Any]:
    """GET with retries + jittered backoff. Returns None if every attempt
    fails. Never raises - callers should check `.ok`.

    When curl_cffi is available (recommended for retailer scrapes) we use
    its Chrome-TLS impersonation to slip past JA3-fingerprint bot walls.
    Falls back to plain `requests` if curl_cffi isn't installed.
    """
    last_err = None
    for attempt in range(retries):
        try:
            if _HAS_CURL_CFFI:
                r = _cc_requests.get(url, headers=headers or browser_headers(),
                                       cookies=cookies, timeout=timeout,
                                       impersonate=impersonate,
                                       allow_redirects=True)
            else:
                r = requests.get(url, headers=headers or browser_headers(),
                                  cookies=cookies, timeout=timeout,
                                  allow_redirects=True)
            status = getattr(r, 'status_code', 0)
            if status == 429 or status >= 500:
                last_err = f"http {status}"
                sleep_s = DEFAULT_RETRY_SLEEP_S * (attempt + 1) + random.random()
                logger.info("http_get %s: %s (attempt %d/%d, sleeping %.1fs)",
                             url, last_err, attempt + 1, retries, sleep_s)
                time.sleep(sleep_s)
                continue
            return r
        except Exception as e:  # curl_cffi raises its own exception types
            last_err = f"{type(e).__name__}: {e}"
            sleep_s = DEFAULT_RETRY_SLEEP_S * (attempt + 1) + random.random()
            logger.info("http_get %s: %s (attempt %d/%d, sleeping %.1fs)",
                         url, last_err, attempt + 1, retries, sleep_s)
            time.sleep(sleep_s)
    logger.warning("http_get %s: exhausted retries; last=%s", url, last_err)
    return None


# ────────────────────────────────────────────────────────────────────────────
# S3
# ────────────────────────────────────────────────────────────────────────────
def _s3_client():
    import boto3  # type: ignore
    region = os.environ.get('AWS_REGION') or 'us-east-2'
    return boto3.client('s3', region_name=region)


def write_snapshot(source: str, payload: dict, *,
                   also_dated: bool = True) -> None:
    """Write the snapshot to S3. Always writes `latest/{source}.json`;
    when `also_dated` is True (default), also writes today's dated copy
    so we retain history.

    Adds/overrides `fetched_at` and `source` on the payload before write."""
    now = datetime.now(timezone.utc)
    payload = dict(payload or {})
    payload.setdefault('source', source)
    payload['fetched_at'] = now.isoformat()

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3 = _s3_client()

    key_latest = f'{S3_LATEST_PREFIX}{source}.json'
    s3.put_object(Bucket=S3_BUCKET, Key=key_latest, Body=body,
                   ContentType='application/json',
                   CacheControl='public, max-age=60')
    logger.info("wrote s3://%s/%s (%d bytes, %d national items)",
                 S3_BUCKET, key_latest, len(body),
                 len(payload.get('national') or []))

    if also_dated:
        dated_prefix = S3_DATED_PREFIX.format(date=now.strftime('%Y-%m-%d'))
        key_dated = f'{dated_prefix}{source}.json'
        s3.put_object(Bucket=S3_BUCKET, Key=key_dated, Body=body,
                       ContentType='application/json')


def read_snapshot(source: str) -> Optional[dict]:
    """Read `latest/{source}.json` from S3. Returns None if the object
    doesn't exist or the read fails. Used by trends_iq.py at request time."""
    try:
        s3 = _s3_client()
        key = f'{S3_LATEST_PREFIX}{source}.json'
        resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
        raw = resp['Body'].read().decode('utf-8')
        return json.loads(raw)
    except Exception as e:
        logger.debug("read_snapshot %s: %s", source, e)
        return None


# ────────────────────────────────────────────────────────────────────────────
# Scraper wrapper
# ────────────────────────────────────────────────────────────────────────────
def run_scraper(source: str, label: str, kind: str,
                fetch_fn: Callable[[], dict]) -> dict:
    """Standard wrapper: call `fetch_fn()`, tag with metadata, write to S3.

    fetch_fn should return a dict that will be merged into the snapshot
    payload. Typical return shape:

        {"national": [...], "by_state": {...}, "categories": [...]}

    If fetch_fn raises, the snapshot is still written with `error` set
    (and `national=[]`) so the read-side always has something to serve.
    """
    started = time.time()
    payload: dict[str, Any] = {
        'source':   source,
        'label':    label,
        'kind':     kind,
        'national': [],
        'error':    None,
    }
    try:
        result = fetch_fn() or {}
        payload.update(result)
        if not isinstance(payload.get('national'), list):
            payload['national'] = []
    except Exception as e:
        logger.exception("scraper %s failed", source)
        payload['error'] = f"{type(e).__name__}: {e}"

    elapsed = time.time() - started
    payload['scrape_elapsed_s'] = round(elapsed, 2)
    try:
        write_snapshot(source, payload)
    except Exception as e:
        logger.exception("write_snapshot %s failed", source)
        payload['s3_write_error'] = str(e)
    return payload
