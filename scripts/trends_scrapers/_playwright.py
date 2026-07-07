"""
Shared Playwright helper for scrapers that hit bot-detected sites.

Every retailer that ships JA3 / TLS fingerprint detection (Walmart,
Target, Best Buy, Lululemon, Etsy, Sephora, and increasingly Nike too)
can be scraped via a headless Chromium session as long as we:

    1. Set a real UA + viewport + locale + timezone
    2. Launch with `--disable-blink-features=AutomationControlled`
    3. Optionally apply playwright-stealth patches
    4. Warm the cookie jar on the homepage before hitting the target URL
    5. Wait for the page's main hydration + scroll to trigger lazy load

Setup (one-time on Hetzner):

    apt-get install -y libnss3 libatk-bridge2.0-0 libcups2 libdrm2 \\
        libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \\
        libgbm1 libpango-1.0-0 libcairo2 libasound2
    pip3 install --user --break-system-packages playwright playwright-stealth
    python3 -m playwright install chromium

Usage:

    from ._playwright import render_pages
    results = render_pages([
        ('Best Sellers', 'https://www.bestbuy.com/site/best-sellers/...'),
        ('Trending',     'https://www.bestbuy.com/site/what-s-new/...'),
    ], homepage='https://www.bestbuy.com/')
    # results is list[tuple[label, html]] - one entry per successful load.
"""

from __future__ import annotations

import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)


UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/124.0.0.0 Safari/537.36')


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


def render_pages(pages: list[tuple[str, str]], *,
                 homepage: Optional[str] = None,
                 wait_ms: int = 3500,
                 scroll_ms: int = 1500,
                 timeout_ms: int = 45000) -> list[tuple[str, str]]:
    """Render each `(label, url)` and return list of `(label, html)`.

    On import failure or launch failure returns [] so callers can degrade
    gracefully to a "coming soon" tile.
    """
    sp = _lazy_playwright()
    if sp is None:
        logger.warning("playwright not installed - install with "
                        "`pip3 install --break-system-packages playwright playwright-stealth` "
                        "then `python3 -m playwright install chromium`")
        return []

    results: list[tuple[str, str]] = []
    with sp() as pw:
        try:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--disable-features=IsolateOrigins,site-per-process',
                ],
            )
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
                # A short scroll triggers lazy-loaded product tiles on most
                # retailer bestseller pages.
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
