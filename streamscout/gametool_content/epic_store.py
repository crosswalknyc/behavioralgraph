#!/usr/bin/env python3
"""
epic_store.py — hands-off Epic Games Store resolver for GameTool.

Epic's GraphQL search sits behind Cloudflare for plain HTTP, but the public
`/browse?q=` page renders a real, query-filtered result grid that a headless
Chromium can read (Cloudflare passes for a genuine browser). Each product card
carries an aria-label ("1 of 4, Base Game, Hades, $24.99") and a
`/p/<slug>` link — we parse the type + clean name, drop add-ons/DLC, and key on
the slug. best_matches then keeps only real editions of the queried game (when a
title isn't on Epic the page shows popular fallbacks, which don't match and fall
away).

Falls back to [] if Playwright/Chromium is unavailable, in which case GameTool
offers the paste-a-URL path instead.
"""
import re
import urllib.parse

from common import best_matches, hit

try:
    from playwright.sync_api import sync_playwright
    _HAVE_PW = True
except Exception:                       # noqa: BLE001
    _HAVE_PW = False

STORE = "Epic Games Store"
BASE = "https://store.epicgames.com/en-US"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

_JS = r"""() => {
  const out = [];
  document.querySelectorAll("a[href*='/p/']").forEach(a => {
    const m = (a.getAttribute('href') || '').match(/\/p\/([a-z0-9-]+)/);
    if (!m) return;
    out.push({label: (a.getAttribute('aria-label') || a.textContent || '').trim(),
              slug: m[1]});
  });
  return out;
}"""

# card "type" segments that aren't the base game someone bought/played
_SKIP_TYPE = {"add-on", "dlc"}
_LABEL = re.compile(
    r"(Base Game|Edition|Add-On|Bundle|Game|DLC|Demo|App),\s*(.+?)"
    r"(?:,\s*(?:\$[\d.,]+|Free).*)?$")


def _parse(label, slug):
    """(type, clean-name) from an Epic aria-label; slug-derived name if unlabeled."""
    lab = re.sub(r"^\d+\s+of\s+\d+,\s*", "", label or "")   # drop "1 of 4, "
    m = _LABEL.match(lab)
    if m:
        return m.group(1).lower(), m.group(2).strip()
    name = re.sub(r",\s*(?:\$[\d.,]+|Free).*$", "", lab).strip()
    return "", (name or slug.replace("-", " "))


def search(title, limit=8):
    """Return one hit per Epic edition of `title` (base game + editions)."""
    if not _HAVE_PW:
        return []
    url = (f"{BASE}/browse?q=" + urllib.parse.quote(title)
           + "&sortBy=relevancy&sortDir=DESC&count=24")
    rows = []
    with sync_playwright() as p:
        # Epic fronts the store with a Cloudflare JS challenge. A plain headless
        # Chromium gets the "Just a moment…" wall; disabling the automation flag
        # + masking navigator.webdriver + a realistic context clears it.
        browser = p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=_UA, locale="en-US",
                                  viewport={"width": 1280, "height": 900})
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # poll until the challenge clears and the result grid renders
            for _ in range(10):
                page.wait_for_timeout(1500)
                if page.evaluate(
                        "document.querySelectorAll(\"a[href*='/p/']\").length") > 0:
                    break
            rows = page.evaluate(_JS) or []
        except Exception:                # noqa: BLE001
            rows = []
        finally:
            browser.close()

    seen, out = set(), []
    for r in rows:
        slug = r.get("slug")
        if not slug or slug in seen:
            continue
        typ, name = _parse(r.get("label"), slug)
        if typ in _SKIP_TYPE:
            continue
        seen.add(slug)
        out.append(hit(name, f"{BASE}/p/{slug}", slug, "Digital (PC)", ""))
    return best_matches(title, out, key=lambda h: h["title"], limit=limit)
