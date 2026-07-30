"""
Residential-proxy support (IPRoyal-compatible).

Some retailers and streaming services (Max/HBO, Walmart, Best Buy,
Sephora, Lululemon, Disney+/ESPN+, ...) IP-gate datacenter ranges at the
CDN edge - the request never reaches the app tier, so cookies alone
can't fix it. Wiring in a residential proxy sidesteps the block.

Config
------
Set these env vars (locally in a `.env`, or in the shell that spawns
the scrapers on Hetzner). All four MUST be present for the proxy to
engage; any missing value = proxy disabled and we fall back to direct.

    IPROYAL_PROXY_HOST   e.g. "geo.iproyal.com"
    IPROYAL_PROXY_PORT   e.g. "12321"
    IPROYAL_PROXY_USER   e.g. "ieGEJyrbsU2khnKV"
    IPROYAL_PROXY_PASS   e.g. "PyvP5rANnjy4RBxg"

Optional geo/sticky targeting (only supported by some IPRoyal products;
Jenna's default Residential subscription rotates per-request and does
NOT accept these suffixes - the proxy answers 407 if it sees them):

    IPROYAL_PROXY_COUNTRY   e.g. "US"        (2-letter ISO)
    IPROYAL_PROXY_STICKY    e.g. "30m"       (session length)

Leave both blank for the default rotation.

Usage
-----
The two scraper entry points read config themselves; scrapers just pass
`use_proxy=True`:

    from ._base import http_get
    r = http_get(url, use_proxy=True, cookie_domain='walmart.com')

    from ._playwright import render_pages
    rendered = render_pages(pages, use_proxy=True,
                             cookie_domain='hbomax.com', ...)

For debugging you can validate the current proxy config with:

    python3 -m scripts.trends_scrapers._proxy
"""

from __future__ import annotations

import logging
import os
import random
import string
from typing import Optional

logger = logging.getLogger(__name__)


def _sticky_username(base_user: str, country: Optional[str],
                      sticky: Optional[str]) -> str:
    """IPRoyal-style username encoding:
        <user>_country-us_session-abc123_lifetime-30m
    Every non-empty part is appended; unspecified parts are dropped.
    """
    parts = [base_user]
    if country:
        parts.append(f"country-{country.lower()}")
    if sticky:
        # Random 8-char session id so consecutive scraper invocations
        # don't collide on the same sticky IP (rotation is still
        # per-invocation, but requests within one run share an IP).
        session = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        parts.append(f"session-{session}")
        parts.append(f"lifetime-{sticky}")
    return '_'.join(parts)


def get_proxy_config(*, sticky_session: bool = False) -> Optional[dict]:
    """Return `{server, username, password, url}` if IPROYAL_PROXY_* is
    fully configured, otherwise None.

    - `server`   : `http://host:port` (what Playwright needs)
    - `username` : encoded with country/session per env config
    - `password` : as configured
    - `url`      : full `http://user:pass@host:port` (what curl_cffi
                    expects in its `proxies` dict)
    """
    host = os.environ.get('IPROYAL_PROXY_HOST', '').strip()
    port = os.environ.get('IPROYAL_PROXY_PORT', '').strip()
    user = os.environ.get('IPROYAL_PROXY_USER', '').strip()
    pwd  = os.environ.get('IPROYAL_PROXY_PASS', '').strip()
    if not (host and port and user and pwd):
        return None

    country = os.environ.get('IPROYAL_PROXY_COUNTRY', '').strip() or None
    sticky  = os.environ.get('IPROYAL_PROXY_STICKY',  '').strip() or None
    if not sticky_session:
        sticky = None  # per-request rotation

    encoded_user = _sticky_username(user, country, sticky)
    server = f'http://{host}:{port}'
    url    = f'http://{encoded_user}:{pwd}@{host}:{port}'
    return {
        'server':   server,
        'username': encoded_user,
        'password': pwd,
        'url':      url,
        'host':     host,
        'port':     port,
    }


def curl_cffi_proxies(cfg: Optional[dict]) -> Optional[dict]:
    """Format for `curl_cffi.requests.get(proxies=...)`."""
    if not cfg:
        return None
    return {'http': cfg['url'], 'https': cfg['url']}


def playwright_proxy(cfg: Optional[dict]) -> Optional[dict]:
    """Format for `browser.launch(proxy=...)` or `context.new_context(proxy=...)`.
    Playwright requires the server URL AND username/password as separate
    fields (not embedded in the URL) or the auth challenge silently fails."""
    if not cfg:
        return None
    return {
        'server':   cfg['server'],
        'username': cfg['username'],
        'password': cfg['password'],
    }


def probe_exit_country(timeout: int = 10) -> Optional[str]:
    """Probe the current exit country through the configured proxy.
    Returns the 2-letter ISO country code (e.g. 'US') or None on error.

    Used by scrapers that MUST land on a US exit (Max, Disney+, Best Buy,
    HBO). If your IPRoyal dashboard has Country/Region set to
    "United States" this always returns 'US' - the check is essentially
    free (~50ms).

    If it's set to "Random" you get whatever the current exit resolves
    to and the caller can decide to retry.
    """
    cfg = get_proxy_config()
    if not cfg:
        return None
    try:
        from curl_cffi import requests as cc  # type: ignore
        r = cc.get('http://ip-api.com/json/',
                    proxies=curl_cffi_proxies(cfg),
                    impersonate='chrome124', timeout=timeout)
        import json as _json
        return _json.loads(r.text).get('countryCode')
    except Exception as e:
        logger.debug("probe_exit_country failed: %s", e)
        return None


def _probe() -> None:
    """Sanity check: print the resolved config (with password redacted)
    and test the outbound IP if httpx/curl_cffi is available."""
    cfg = get_proxy_config()
    if not cfg:
        print("[proxy] not configured - set IPROYAL_PROXY_HOST/PORT/USER/PASS")
        return
    redacted = dict(cfg)
    redacted['password'] = '***'
    redacted['url'] = f"http://{cfg['username']}:***@{cfg['host']}:{cfg['port']}"
    print(f"[proxy] {redacted}")

    try:
        from curl_cffi import requests as cc  # type: ignore
        r = cc.get('https://api.ipify.org?format=json',
                    proxies=curl_cffi_proxies(cfg),
                    impersonate='chrome124', timeout=15)
        print(f"[proxy] outbound IP via IPRoyal: {r.text}")
    except ImportError:
        print("[proxy] curl_cffi not installed, skipping outbound-IP probe")
    except Exception as e:
        print(f"[proxy] outbound-IP probe FAILED: {type(e).__name__}: {e}")


if __name__ == '__main__':
    _probe()
