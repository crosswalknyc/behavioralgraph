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


def _launch_browser(pw, *, prefer_chrome: bool = True):
    """Launch a browser, preferring real Google Chrome (channel='chrome')
    over the bundled Chromium. Falls back to Chromium if Chrome isn't
    installed. Uses --headless=new which is much harder to detect than
    the classic --headless.
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
    if prefer_chrome:
        try:
            b = pw.chromium.launch(channel='chrome', headless=True,
                                    args=common_args)
            logger.info("playwright launched: channel=chrome (system Google Chrome)")
            return b, 'chrome'
        except Exception as e:
            logger.info("channel=chrome unavailable (%s), falling back to Chromium", e)
    b = pw.chromium.launch(headless=True, args=common_args)
    logger.info("playwright launched: bundled Chromium")
    return b, 'chromium'


def render_pages(pages: list[tuple[str, str]], *,
                 homepage: Optional[str] = None,
                 cookie_domain: Optional[str] = None,
                 wait_ms: int = 3500,
                 scroll_ms: int = 1500,
                 timeout_ms: int = 45000) -> list[tuple[str, str]]:
    """Render each `(label, url)` and return list of `(label, html)`.

    Pass `cookie_domain='target.com'` (etc.) to auto-inject the latest
    donated cookies for that domain. See `donate_cookies.py`.

    On import failure or launch failure returns [] so callers can degrade
    gracefully to a "coming soon" tile.
    """
    sp = _lazy_playwright()
    if sp is None:
        logger.warning("playwright not installed - install with "
                        "`pip3 install --break-system-packages playwright playwright-stealth` "
                        "then `python3 -m playwright install-deps chromium`")
        return []

    results: list[tuple[str, str]] = []
    with sp() as pw:
        try:
            browser, channel = _launch_browser(pw, prefer_chrome=True)
        except Exception as e:
            logger.warning("playwright launch failed: %s", e)
            return []

        ctx = browser.new_context(
            user_agent=UA,
            viewport={'width': 1440, 'height': 900},
            locale='en-US',
            timezone_id='America/New_York',
            extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
        )

        # Inject donated cookies BEFORE any navigation so the very first
        # request lands with the operator's real session state.
        if cookie_domain:
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
                page.wait_for_timeout(wait_ms + random.randint(0, 800))
                page.mouse.wheel(0, 2400)
                page.wait_for_timeout(scroll_ms)
                html = page.content()
                if html and len(html) > 5000:
                    results.append((label, html))
                else:
                    logger.info("playwright %s: got %d-byte body, skipping",
                                 label, len(html or ''))
            except Exception as e:
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
