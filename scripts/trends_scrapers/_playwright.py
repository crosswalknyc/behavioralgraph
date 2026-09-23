"""
Shared Playwright helper for scrapers that hit bot-detected sites.

Every retailer that ships JA3 / TLS fingerprint detection (Walmart,
Target, Best Buy, Lululemon, Etsy, Sephora, Ulta) can be scraped via a
headless Chromium session as long as we:

    1. Set a real UA + viewport + locale + timezone
    2. Prefer real Google Chrome (channel="chrome") over stock Chromium
       because DataDome / PerimeterX also fingerprint the browser
       binary
    3. Use --headless=new (the "new" headless mode) which passes the
       navigator.webdriver / user-agent-data checks the classic
       --headless does not
    4. Launch with --disable-blink-features=AutomationControlled
    5. Apply playwright-stealth patches when available
    6. Inject donated cookies from the operator's real Chrome BEFORE
       navigation (the operator visits the site in Chrome, runs
       donate_cookies.py, and we pick those cookies up here)
    7. Warm the cookie jar on the homepage before hitting the target URL
    8. Wait for hydration + short scroll to trigger lazy-load

Hetzner one-time setup:

    # Real Google Chrome (not the Playwright-bundled Chromium)
    wget -qO- https://dl.google.com/linux/linux_signing_key.pub \\
        | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg
    echo "deb [signed-by=/etc/apt/keyrings/google-chrome.gpg] \\
          http://dl.google.com/linux/chrome/deb/ stable main" \\
        > /etc/apt/sources.list.d/google-chrome.list
    apt-get update
    apt-get install -y google-chrome-stable

    pip3 install --user --break-system-packages playwright playwright-stealth
    # channel="chrome" uses system Chrome so no `playwright install` needed
    # for that channel, BUT install-deps is still worth running once for
    # system libs Playwright expects.
    python3 -m playwright install-deps chromium

Usage:

    from ._playwright import render_pages
    results = render_pages([
        ('Best Sellers', 'https://www.target.com/c/bullseyes-top-picks/...'),
    ], homepage='https://www.target.com/', cookie_domain='target.com')
"""

from __future__ import annotations

import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)


UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/125.0.0.0 Safari/537.36')


def _lazy_playwright():
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        return sync_playwright
    except ImportError:
        return None


def _try_stealth(page) -> None:
    try:
        from playwright_stealth import stealth_sync  # type: ignore
        stealth_sync(page)
    except ImportError:
        pass
    except Exception as e:
        logger.debug("playwright_stealth failed: %s", e)


def _launch_browser(pw, *, prefer_chrome: bool = True,
                     proxy: Optional[dict] = None):
    """Launch a browser, preferring real Google Chrome (channel='chrome')
    over the bundled Chromium. Falls back to Chromium if Chrome isn't
    installed. Uses --headless=new which is much harder to detect than
    the classic --headless.

    `proxy` is the dict returned by `_proxy.playwright_proxy()` -
    {server, username, password}. Pass None to bypass the proxy.
    """
    common_args = [
        '--no-sandbox',
        '--disable-blink-features=AutomationControlled',
        '--disable-dev-shm-usage',
        '--disable-features=IsolateOrigins,site-per-process',
        # --headless=new (aka "new headless") uses the real Chrome
        # rendering path instead of the stripped-down classic headless,
        # so navigator.webdriver == false and userAgentData looks real.
        '--headless=new',
    ]
    launch_kwargs = {'headless': True, 'args': common_args}
    if proxy:
        launch_kwargs['proxy'] = proxy
    if prefer_chrome:
        try:
            b = pw.chromium.launch(channel='chrome', **launch_kwargs)
            logger.info("playwright launched: channel=chrome (system Google Chrome)%s",
                         '  proxy=' + proxy['server'] if proxy else '')
            return b, 'chrome'
        except Exception as e:
            logger.info("channel=chrome unavailable (%s), falling back to Chromium", e)
    b = pw.chromium.launch(**launch_kwargs)
    logger.info("playwright launched: bundled Chromium%s",
                 '  proxy=' + proxy['server'] if proxy else '')
    return b, 'chromium'


def render_pages(pages: list[tuple[str, str]], *,
                 homepage: Optional[str] = None,
                 cookie_domain: Optional[str] = None,
                 wait_ms: int = 3500,
                 scroll_ms: int = 1500,
                 timeout_ms: int = 45000,
                 wait_selectors: Optional[list[str]] = None,
                 hydration_wait_ms: int = 10000,
                 use_proxy: bool = False,
                 assert_signed_in: Optional[str] = None
                 ) -> list[tuple[str, str]]:
    """Render each `(label, url)` and return list of `(label, html)`.

    Pass `cookie_domain='target.com'` (etc.) to auto-inject the latest
    donated cookies for that domain. See `donate_cookies.py`.

    `wait_selectors` is a list of CSS selectors. If any of them appears
    in the DOM within `hydration_wait_ms`, we consider the page ready
    and snapshot immediately after (so client-side-rendered product
    grids like Target's have time to hydrate). If none appears within
    the budget we still fall through to the fixed `wait_ms` timer, so
    servers that ship products directly in SSR HTML aren't slowed down.

    Pass `assert_signed_in='hbomax.com'` on a session-gated source.
    Every rendered page then has to PROVE it is an authenticated US
    page before it is handed back, and the first one that cannot
    raises out of here rather than returning.

    That is deliberately harsher than the rest of this function, which
    logs and moves on. These platforms do not fail by erroring: HBO
    Max answers HTTP 200 with its full marketing site, plan cards and
    promotional artwork included. It parses. A scraper that returns it
    publishes a plan picker as a viewership chart, and the rail looks
    populated and plausible while being wrong. Raising is what makes
    that impossible. See `_auth_guard`.

    Pass `use_proxy=True` to route every request through the IPRoyal
    residential proxy (config via IPROYAL_PROXY_* env vars). Silently
    disables if the env vars aren't set. Enable this only for sites
    that IP-gate datacenter ranges - it costs proxy bandwidth per byte.

    On import failure or launch failure returns [] so callers can degrade
    gracefully to a "coming soon" tile.
    """
    sp = _lazy_playwright()
    if sp is None:
        logger.warning("playwright not installed - install with "
                        "`pip3 install --break-system-packages playwright playwright-stealth` "
                        "then `python3 -m playwright install-deps chromium`")
        return []

    proxy_dict = None
    if use_proxy:
        from ._proxy import get_proxy_config, playwright_proxy
        cfg = get_proxy_config()
        proxy_dict = playwright_proxy(cfg)
        if not proxy_dict:
            logger.info("playwright: use_proxy=True but IPROYAL_PROXY_* not "
                         "configured; falling back to direct")

    results: list[tuple[str, str]] = []
    with sp() as pw:
        try:
            browser, channel = _launch_browser(pw, prefer_chrome=True,
                                                 proxy=proxy_dict)
        except Exception as e:
            logger.warning("playwright launch failed: %s", e)
            return []

        ctx_kwargs = {
            'user_agent': UA,
            'viewport': {'width': 1440, 'height': 900},
            'locale': 'en-US',
            'timezone_id': 'America/New_York',
            'extra_http_headers': {'Accept-Language': 'en-US,en;q=0.9'},
        }

        # Prefer a donated STORAGE STATE over a donated cookie jar.
        #
        # A cookie jar is the whole session for a retailer. For a
        # streaming SPA it is not: those keep their access and refresh
        # tokens in IndexedDB, so a cookie-only injection renders the
        # logged-out marketing page rather than the app. A storage
        # state carries cookies, localStorage and IndexedDB together
        # and has to be handed to the context at construction, which
        # is why this runs before new_context rather than after.
        #
        # Additive: when no storage state has been donated for this
        # domain we fall through to the cookie path below exactly as
        # before, which is still correct for every retail and social
        # source where cookie donation already works.
        donated_state = None
        if cookie_domain:
            try:
                from ._base import load_donated_storage_state, storage_state_status
                donated_state = load_donated_storage_state(cookie_domain)
                if donated_state:
                    from ._auth_guard import describe_storage_state
                    st = storage_state_status(cookie_domain)
                    ctx_kwargs['storage_state'] = donated_state
                    logger.info("playwright[%s]: restored donated session "
                                "(%s, age=%.0fh)",
                                cookie_domain,
                                describe_storage_state(donated_state),
                                st.get('age_hours') or -1)
            except Exception as e:
                logger.info("storage-state restore for %s failed: %s",
                            cookie_domain, e)
                donated_state = None

        ctx = browser.new_context(**ctx_kwargs)

        # Inject donated cookies BEFORE any navigation so the very first
        # request lands with the operator's real session state. Skipped
        # when a storage state was restored: that state already carries
        # its own cookies, and layering a second session's jar for the
        # same domain on top is how you get a half-authenticated
        # context that fails in a new way.
        if cookie_domain and not donated_state:
            try:
                from ._base import load_donated_cookies_playwright, cookie_donation_status
                donated = load_donated_cookies_playwright(cookie_domain)
                status = cookie_donation_status(cookie_domain)
                if donated:
                    ctx.add_cookies(donated)
                    logger.info("playwright[%s]: injected %d cookies (age=%.1fh)",
                                 cookie_domain, len(donated),
                                 status.get('age_hours') or -1)
                else:
                    logger.warning("playwright[%s]: NO DONATED COOKIES - "
                                    "run `python3 scripts/trends_scrapers/"
                                    "donate_cookies.py %s` from your laptop",
                                    cookie_domain, cookie_domain)
            except Exception as e:
                logger.info("cookie injection for %s failed: %s", cookie_domain, e)

        page = ctx.new_page()
        _try_stealth(page)

        # Pre-flight identity check, once, on the platform's app home.
        # Everything after this point is only worth rendering if this
        # passes, so it runs before the homepage warm-up and raises
        # straight out of render_pages.
        if assert_signed_in:
            from ._auth_guard import prove_signed_in
            evidence = prove_signed_in(page, assert_signed_in,
                                       source=f'{assert_signed_in} pre-flight')
            logger.info("playwright[%s]: session proven (%s)",
                        assert_signed_in, evidence)

        if homepage:
            try:
                page.goto(homepage, wait_until='domcontentloaded',
                           timeout=timeout_ms)
                page.wait_for_timeout(2500 + random.randint(0, 1500))
            except Exception as e:
                logger.info("playwright homepage warmup failed for %s: %s",
                             homepage, e)

        for label, url in pages:
            try:
                page.goto(url, wait_until='domcontentloaded',
                           timeout=timeout_ms)
                # If the caller supplied hydration selectors, try each in
                # turn. First one that appears wins; if none does, we
                # fall through to the fixed wait_ms (same as before).
                hydrated = False
                if wait_selectors:
                    per_sel_budget = max(1000, hydration_wait_ms // max(1, len(wait_selectors)))
                    for sel in wait_selectors:
                        try:
                            page.wait_for_selector(sel, timeout=per_sel_budget,
                                                     state='attached')
                            hydrated = True
                            logger.debug("playwright %s: hydrated on selector %s",
                                          label, sel)
                            break
                        except Exception:
                            continue
                if not hydrated:
                    page.wait_for_timeout(wait_ms + random.randint(0, 800))
                page.mouse.wheel(0, 2400)
                page.wait_for_timeout(scroll_ms)
                html = page.content()

                # Backstop for a session that dies mid-run. A browse
                # page cannot prove a session, so this only refuses a
                # page that IS a wall. Raising here rather than below
                # is the point: the auth errors must escape the
                # handler that swallows ordinary render failures.
                if assert_signed_in:
                    from ._auth_guard import refuse_if_signed_out
                    refuse_if_signed_out(page, assert_signed_in,
                                         source=f'{assert_signed_in} {label}')

                if html and len(html) > 5000:
                    results.append((label, html))
                else:
                    logger.info("playwright %s: got %d-byte body, skipping",
                                 label, len(html or ''))
            except Exception as e:
                from ._auth_guard import AuthWallError, GeoMismatchError
                if isinstance(e, (AuthWallError, GeoMismatchError)):
                    try:
                        ctx.close()
                        browser.close()
                    except Exception:
                        pass
                    raise
                logger.warning("playwright %s (%s): %s", label, url, e)

        try:
            ctx.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
    return results
