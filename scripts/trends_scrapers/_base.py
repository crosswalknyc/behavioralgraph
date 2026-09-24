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
# Full signed-in sessions (cookies plus localStorage plus IndexedDB),
# written by donate_storage_state.py. Separate prefix from the cookie
# donations because the contents are more sensitive and the lifetime
# is different: see load_donated_storage_state below.
S3_STORAGE_STATE_PREFIX = 'trends_iq_storage_state/'

# Donated cookies older than this are ignored - most retailer/session
# cookies expire in 30-90 days but the anti-bot session tokens (Akamai
# _abck, DataDome datadome, PerimeterX _px) rotate faster, and stale
# ones look more suspicious than none at all. 48h is the sweet spot.
DEFAULT_COOKIE_MAX_AGE_H = 48

# A storage state is a real streaming session, not an anti-bot token.
# Those refresh tokens are good for weeks, and the thing that ends one
# is a sign-out or a password change, not the clock. Expiring them at
# 48h would send an operator to re-authorize a session that still
# works. The honest expiry signal is the content check in
# `_auth_guard`, which asks the platform rather than the timestamp,
# so this ceiling is only a backstop against a truly ancient donation.
DEFAULT_STORAGE_STATE_MAX_AGE_H = 24 * 30

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

    # Backstop for the 60-day distinctness rule (Jenna 2026-09-15). The
    # estimator already applies it in the value assignment; enforcing it
    # again here means a snapshot cannot repeat a reading no matter who
    # composed it, including a one-off repair that merges keys forward
    # by hand. That is how 12,467 keys came back as their predecessor's
    # integer on 2026-09-15. Idempotent, so the second application on a
    # clean payload changes nothing, and never raises.
    if source == 'stream_estimates' and isinstance(payload.get('items'), dict):
        try:
            from . import value_distinctness as _vd
            _vd.enforce_on_payload(payload, now.strftime('%Y-%m-%d'))
        except Exception:
            logger.exception('stream_estimates: distinctness backstop '
                             'skipped (non-fatal)')

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
        day_iso = now.strftime('%Y-%m-%d')
        dated_prefix = S3_DATED_PREFIX.format(date=day_iso)
        key_dated = f'{dated_prefix}{source}.json'
        put = s3.put_object(Bucket=S3_BUCKET, Key=key_dated, Body=body,
                             ContentType='application/json')

        # Lean sibling index for the window sum. The dashboard reads
        # up to 60 dated days per request and only needs three fields
        # out of this payload, so it reads them from a copy about
        # twenty times smaller. See stream_window_index.py.
        if source == 'stream_estimates':
            try:
                from . import stream_window_index as _swi
                _swi.write_index(day_iso, payload,
                                 source_etag=put.get('ETag'),
                                 source_bytes=len(body), s3=s3)
            except Exception:
                logger.exception('stream window index: write skipped '
                                 'for %s (non-fatal)', day_iso)


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


# ────────────────────────────────────────────────────────────────────────────
# Donated storage state (cookies + localStorage + IndexedDB)
# ────────────────────────────────────────────────────────────────────────────
# A cookie jar is enough for a retailer. It is not enough for a
# streaming SPA, which keeps its access and refresh tokens in
# IndexedDB. Restoring one of these into `browser.new_context(
# storage_state=...)` is what makes a scraper genuinely signed in
# rather than a visitor reading the marketing site.
#
# This is additive. `load_donated_cookies_playwright` still works and
# is still the right call for every source where cookie donation
# already succeeds.
_STORAGE_STATE_CACHE: dict[str, tuple[float, dict]] = {}


def _load_storage_state_payload(domain: str) -> Optional[dict]:
    """Read the raw storage-state donation for `domain` from S3.

    Cached per process, same as the cookie path, so a scraper that
    renders several pages pays one S3 read.
    """
    cached = _STORAGE_STATE_CACHE.get(domain)
    if cached is not None:
        return cached[1] or None
    payload = None
    try:
        s3 = _s3_client()
        key = f'{S3_STORAGE_STATE_PREFIX}{domain}.json'
        resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
        payload = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception as e:
        logger.debug("no storage-state donation for %s: %s", domain, e)
    _STORAGE_STATE_CACHE[domain] = (time.time(), payload or {})
    return payload


def load_donated_storage_state(
        domain: str, *,
        max_age_hours: float = DEFAULT_STORAGE_STATE_MAX_AGE_H
) -> Optional[dict]:
    """Return a Playwright storage-state dict for `domain`, or None.

    Pass the result straight to `browser.new_context(storage_state=...)`.
    Returns None when nothing was donated, when the donation is past
    the backstop age, or when the read fails, so callers can fall back
    to cookie-only injection without a guard.

    Never logs a value from the state. The log line reports counts via
    `_auth_guard.describe_storage_state`.
    """
    payload = _load_storage_state_payload(domain)
    if not payload:
        return None
    age = _cookie_age_hours(payload)
    if age is not None and age > max_age_hours:
        logger.info("donated session for %s is %.0fh old (>%.0fh) - ignoring",
                    domain, age, max_age_hours)
        return None
    state = payload.get('storage_state')
    if not isinstance(state, dict):
        return None
    if not (state.get('cookies') or state.get('origins')):
        return None
    return state


def forget_donations(domain: str) -> None:
    """Drop the per-process caches for `domain` so the next read hits S3.

    Needed after a session is re-issued mid-run: `render_pages` heals a
    dead session by signing in again and re-donating, and the retry
    that follows has to see the new donation rather than the cached
    dead one.
    """
    _COOKIE_CACHE.pop(domain, None)
    _STORAGE_STATE_CACHE.pop(domain, None)


def storage_state_status(domain: str) -> dict:
    """Freshness and shape of the storage-state donation for `domain`.

    Value-free by construction: the `summary` field carries counts
    only, because a storage state can hold access and refresh tokens
    and none of them belong in a log or a status table.
    """
    payload = _load_storage_state_payload(domain)
    if not payload:
        return {'domain': domain, 'donated': False}
    age_h = _cookie_age_hours(payload)
    return {
        'domain':     domain,
        'donated':    True,
        'age_hours':  round(age_h, 1) if age_h is not None else None,
        'donated_at': payload.get('donated_at'),
        'donor_host': payload.get('donor_host'),
        'verdict':    payload.get('verdict'),
        'summary':    payload.get('summary') or {},
        'fresh':      age_h is not None
                      and age_h <= DEFAULT_STORAGE_STATE_MAX_AGE_H,
    }


# ---------------------------------------------------------------------------
# Hydration-failure diagnosis
# ---------------------------------------------------------------------------
# A cookie-gated scraper that waits on a selector and times out has NOT
# established why it timed out. Three different causes produce the same
# timeout and only one of them is a cookie problem:
#
#   no_cookies       nothing was donated for this domain
#   session_rejected cookies were sent but the site bounced us to a
#                    sign-in wall, so the session is genuinely dead
#   page_changed     the session was accepted and the page rendered,
#                    but the element we wait for is not there any more
#
# Asserting "cookies likely expired" on all three sends an operator to
# re-donate a session that was never broken, and fires the cookie-gap
# email for a page that simply changed shape. Only `session_rejected`
# should ask for a re-donation, and only `session_rejected` /
# `no_cookies` should notify.
HYDRATION_NO_COOKIES       = 'no_cookies'
HYDRATION_SESSION_REJECTED = 'session_rejected'
HYDRATION_PAGE_CHANGED     = 'page_changed'

# A redirect to one of these paths is hard evidence the session was
# rejected. Matched against the FINAL url, not the requested one.
_SIGNIN_URL_MARKERS = (
    '/ap/signin', '/ap/cvf', '/signin', '/sign-in', '/login',
    '/accounts/login', '/auth/login', '/users/sign_in',
)

# Text that only appears on an actual sign-in form. Deliberately narrow:
# a "Sign in" link in a nav bar is not evidence of anything, so bare
# "sign in" is not on this list.
_SIGNIN_TEXT_MARKERS = (
    'enter your password', 'sign in to your account', 'sign in with your',
    'keep me signed in', 'forgot your password', 'create your account',
    'log in to continue', 'sign in to continue',
)


def classify_hydration_failure(*, target_url: str, selector: str,
                               final_url: str = '', page_text: str = '',
                               cookie_count: int = 0,
                               signed_in_markers: tuple[str, ...] = ()
                               ) -> tuple[str, str, bool]:
    """Work out why a hydration wait failed and say only what the
    evidence supports.

    Returns `(kind, message, notify_cookie_gap)` where `kind` is one of
    the `HYDRATION_*` constants, `message` is the operator-facing
    diagnostic, and `notify_cookie_gap` says whether the cookie-gap
    email is warranted.

    `signed_in_markers` are strings that, when present in `page_text`,
    prove the session was accepted (an account name, a members-only
    nav item). They veto the sign-in verdict, which is what stops a
    signed-in page that happens to carry the words "sign in" from being
    misread as a rejection.
    """
    final_url = final_url or target_url
    low_text  = (page_text or '').lower()
    low_url   = (final_url or '').lower()

    if cookie_count <= 0:
        return (
            HYDRATION_NO_COOKIES,
            (f'no donated cookies were available for this session, so '
             f'{target_url} was requested anonymously and never '
             f'rendered {selector}. Donate cookies for this domain.'),
            True,
        )

    signed_in = any(m.lower() in low_text for m in signed_in_markers if m)
    hit_signin_url  = any(m in low_url for m in _SIGNIN_URL_MARKERS)
    hit_signin_text = any(m in low_text for m in _SIGNIN_TEXT_MARKERS)

    if not signed_in and (hit_signin_url or hit_signin_text):
        where = f' (landed on {final_url})' if final_url != target_url else ''
        return (
            HYDRATION_SESSION_REJECTED,
            (f'the donated session was rejected and {target_url} bounced '
             f'to a sign-in wall{where}. Re-donate cookies for this '
             f'domain.'),
            True,
        )

    where = f', ended on {final_url}' if final_url != target_url else ''
    return (
        HYDRATION_PAGE_CHANGED,
        (f'the session was accepted but {target_url} did not render '
         f'`{selector}`{where}. The page or the selector has changed. '
         f'This is not a cookie problem and re-donating will not fix '
         f'it.'),
        False,
    )


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
        # A failed fetch must never publish an empty snapshot over a
        # good one. `stream_estimates` and its siblings accumulate an
        # `items` store across days; writing the bare error payload
        # wipes it, and every downstream row then falls back to the
        # rank-tier baseline until something re-prices the whole board.
        # That is exactly what happened on 2026-09-15: a truncated line
        # in the batch results stream raised out of the estimator, this
        # handler wrote a 255-byte stub over an 18,009-item store, and
        # the coverage gate spent the next five hours re-pricing from
        # scratch while the dashboard served rank-derived numbers.
        # Carry the accumulated keys forward and record the error
        # alongside them, so a failure degrades to "yesterday's values"
        # instead of "no values".
        # Scoped deliberately to the `items` accumulator. `national` is
        # left empty on failure exactly as before, so a dark scraper
        # still reads as dark on the board instead of silently showing
        # yesterday's rows as today's.
        try:
            prior = read_snapshot(source) or {}
        except Exception:
            prior = {}
        prior_items = prior.get('items')
        if isinstance(prior_items, dict) and prior_items and not payload.get('items'):
            payload['items'] = prior_items
            payload['count'] = len(prior_items)
            payload['error_preserved_prior_items'] = True
            for carried in ('target_date', 'generated_at'):
                if prior.get(carried) and not payload.get(carried):
                    payload[carried] = prior[carried]
            logger.warning(
                "scraper %s failed; preserved %d prior item(s) rather "
                "than publishing an empty accumulator",
                source, len(prior_items),
            )

    elapsed = time.time() - started
    payload['scrape_elapsed_s'] = round(elapsed, 2)
    try:
        write_snapshot(source, payload)
    except Exception as e:
        logger.exception("write_snapshot %s failed", source)
        payload['s3_write_error'] = str(e)
    return payload
