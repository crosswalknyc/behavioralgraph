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
S3_COOKIES_PREFIX = 'trends_iq_cookies/'

# Donated cookies older than this are ignored - most retailer/session
# cookies expire in 30-90 days but the anti-bot session tokens (Akamai
# _abck, DataDome datadome, PerimeterX _px) rotate faster, and stale
# ones look more suspicious than none at all. 48h is the sweet spot.
DEFAULT_COOKIE_MAX_AGE_H = 48

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
             cookie_domain: str | None = None,
             impersonate: str = 'chrome124',
             use_proxy: bool = False) -> Optional[Any]:
    """GET with retries + jittered backoff. Returns None if every attempt
    fails. Never raises - callers should check `.ok`.

    When curl_cffi is available (recommended for retailer scrapes) we use
    its Chrome-TLS impersonation to slip past JA3-fingerprint bot walls.
    Falls back to plain `requests` if curl_cffi isn't installed.

    Pass `cookie_domain='target.com'` (etc.) to auto-inject donated
    cookies for that domain. Explicit `cookies=...` still wins on key
    collisions.

    Pass `use_proxy=True` to route through the IPRoyal residential
    proxy (config via IPROYAL_PROXY_* env vars). Silently falls back to
    a direct connection if the env vars aren't set. Only turn this on
    for sites that IP-block datacenter ranges (Max, Walmart, Best Buy,
    Sephora, Lululemon, Disney+) - most retailers are fine over direct.
    """
    if cookie_domain:
        donated = load_donated_cookies(cookie_domain)
        if donated:
            merged = dict(donated)
            if cookies:
                merged.update(cookies)
            cookies = merged
            logger.info("http_get %s: injected %d donated cookies for %s",
                         url, len(donated), cookie_domain)

    proxies = None
    if use_proxy:
        from ._proxy import get_proxy_config, curl_cffi_proxies
        proxy_cfg = get_proxy_config()
        proxies   = curl_cffi_proxies(proxy_cfg)
        if proxies:
            logger.info("http_get %s: using residential proxy %s",
                         url, proxy_cfg['host'])
        else:
            logger.info("http_get %s: use_proxy=True but IPROYAL_PROXY_* "
                         "not configured; falling back to direct", url)

    last_err = None
    for attempt in range(retries):
        try:
            if _HAS_CURL_CFFI:
                r = _cc_requests.get(url, headers=headers or browser_headers(),
                                       cookies=cookies, timeout=timeout,
                                       impersonate=impersonate,
                                       proxies=proxies,
                                       allow_redirects=True)
            else:
                r = requests.get(url, headers=headers or browser_headers(),
                                  cookies=cookies, timeout=timeout,
                                  proxies=proxies,
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


# ────────────────────────────────────────────────────────────────────────────
# Donated cookies
# ────────────────────────────────────────────────────────────────────────────
# In-process cache so we don't hit S3 once per HTTP call. Keyed by domain,
# value is (donated_at_epoch, {cookies + metadata}). Cleared on process
# restart, which happens daily via the cron.
_COOKIE_CACHE: dict[str, tuple[float, dict]] = {}


def _load_cookie_payload(domain: str) -> Optional[dict]:
    """Read the raw donation payload for `domain` from S3. Cached in
    process. Returns the full payload dict {donated_at, cookies, ...}
    or None if no donation exists."""
    cached = _COOKIE_CACHE.get(domain)
    if cached is not None:
        return cached[1]
    try:
        s3 = _s3_client()
        key = f'{S3_COOKIES_PREFIX}{domain}.json'
        resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
        raw = resp['Body'].read().decode('utf-8')
        payload = json.loads(raw)
    except Exception as e:
        logger.debug("no cookie donation for %s: %s", domain, e)
        _COOKIE_CACHE[domain] = (time.time(), {})
        return None
    _COOKIE_CACHE[domain] = (time.time(), payload)
    return payload


def _cookie_age_hours(payload: dict) -> Optional[float]:
    donated_at = payload.get('donated_at') if payload else None
    if not donated_at:
        return None
    try:
        dt = datetime.fromisoformat(donated_at.replace('Z', '+00:00'))
    except Exception:
        return None
    delta = datetime.now(timezone.utc) - dt
    return delta.total_seconds() / 3600.0


def load_donated_cookies(domain: str, *,
                         max_age_hours: float = DEFAULT_COOKIE_MAX_AGE_H
                         ) -> dict[str, str]:
    """Return a `{name: value}` cookie dict for `domain`, suitable for
    plugging into `requests.get(cookies=...)` or `curl_cffi.get(cookies=...)`.

    Returns `{}` when there's no donation, when it's stale, or when the
    S3 read fails - so callers can always use this without guards.
    """
    payload = _load_cookie_payload(domain)
    if not payload:
        return {}
    age = _cookie_age_hours(payload)
    if age is not None and age > max_age_hours:
        logger.info("donated cookies for %s are %.1fh old (>%.1fh) - ignoring",
                     domain, age, max_age_hours)
        return {}
    out: dict[str, str] = {}
    for c in payload.get('cookies') or []:
        name  = c.get('name')
        value = c.get('value')
        if name and value:
            out[name] = value
    return out


def load_donated_cookies_playwright(domain: str, *,
                                     max_age_hours: float = DEFAULT_COOKIE_MAX_AGE_H
                                     ) -> list[dict]:
    """Return a list of cookie dicts formatted for Playwright's
    `context.add_cookies(...)`. Same freshness rules as
    `load_donated_cookies`."""
    payload = _load_cookie_payload(domain)
    if not payload:
        return []
    age = _cookie_age_hours(payload)
    if age is not None and age > max_age_hours:
        return []
    out: list[dict] = []
    for c in payload.get('cookies') or []:
        name = c.get('name')
        value = c.get('value')
        dom = c.get('domain') or f'.{domain}'
        if not (name and value):
            continue
        entry = {
            'name':   name,
            'value':  value,
            'domain': dom,
            'path':   c.get('path') or '/',
        }
        if c.get('expires'):
            entry['expires'] = int(c['expires'])
        if c.get('secure'):
            entry['secure'] = True
        if c.get('httpOnly'):
            entry['httpOnly'] = True
        # Playwright requires sameSite in {Strict, Lax, None}. Default Lax.
        entry['sameSite'] = c.get('sameSite') or 'Lax'
        out.append(entry)
    return out


def cookie_donation_status(domain: str) -> dict:
    """Return a small dict describing the freshness of the donation for
    `domain`. Used by `cookies_status.py` and by scraper log lines."""
    payload = _load_cookie_payload(domain)
    if not payload:
        return {'domain': domain, 'donated': False}
    age_h = _cookie_age_hours(payload)
    fresh = age_h is not None and age_h <= DEFAULT_COOKIE_MAX_AGE_H
    return {
        'domain':     domain,
        'donated':    True,
        'age_hours':  round(age_h, 1) if age_h is not None else None,
        'count':      len(payload.get('cookies') or []),
        'donated_at': payload.get('donated_at'),
        'donor_host': payload.get('donor_host'),
        'fresh':      fresh,
    }


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
