#!/usr/bin/env python3
"""
xbox_store.py — hands-off Xbox (Microsoft Store) resolver for GameTool.

Xbox.com autosuggest / DisplayCatalog search is bot-walled to plain HTTP, but
the public search page server-renders its result grid. So we drive a headless
Chromium (the PlayStation/StreamScout pattern): load the real search page and
read each product card's title + canonical `/games/store/<slug>/<productId>`
link. The 12-char productId is the stable Store ID we key on. best_matches then
keeps only genuine editions of the queried game, so subscription tiles (Game
Pass) and unrelated grid entries fall away.

Falls back to [] if Playwright/Chromium is unavailable, in which case GameTool
offers the paste-a-URL path instead.
"""
import urllib.parse

from common import best_matches, hit

try:
    from playwright.sync_api import sync_playwright
    _HAVE_PW = True
except Exception:                       # noqa: BLE001
    _HAVE_PW = False

STORE = "Xbox"
BASE = "https://www.xbox.com/en-US"

# Pull (title, slug, 12-char productId) from every product anchor in the grid.
_JS = r"""() => {
  const out = [];
  document.querySelectorAll("a[href*='/games/store/']").forEach(a => {
    const m = (a.getAttribute('href') || '')
        .match(/\/games\/store\/([a-z0-9-]+)\/([A-Za-z0-9]{12})/);
    if (!m) return;
    let t = (a.getAttribute('aria-label') || a.textContent || '').trim();
    t = t.replace(/,\s*\$[\d.,]+.*$/, '').trim();   // drop trailing ", $24.99"
    out.push({title: t, slug: m[1], id: m[2]});
  });
  return out;
}"""

# subscription / service SKUs that ride the same search grid but aren't a game
_NOT_GAME_SLUG = {"xbox-game-pass-ultimate", "pc-game-pass", "xbox-game-pass",
                  "xbox-game-pass-core", "xbox-game-pass-standard", "ea-play"}


def search(title, limit=12):
    """Return one hit per Xbox store edition of `title` (title/slug/productId)."""
    if not _HAVE_PW:
        return []
    url = f"{BASE}/Search/Results?q=" + urllib.parse.quote(title)
    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda r: r.abort()
                   if r.request.resource_type in ("image", "media", "font")
                   else r.continue_())
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector("a[href*='/games/store/']", timeout=25000)
            except Exception:            # noqa: BLE001
                pass
            page.wait_for_timeout(1500)  # let the grid finish hydrating
            rows = page.evaluate(_JS) or []
        except Exception:                # noqa: BLE001
            rows = []
        finally:
            browser.close()

    seen, out = set(), []
    for r in rows:
        slug, gid = r.get("slug"), r.get("id")
        if not (slug and gid) or slug in _NOT_GAME_SLUG or gid in seen:
            continue
        seen.add(gid)
        name = r.get("title") or slug.replace("-", " ")
        out.append(hit(name, f"{BASE}/games/store/{slug}/{gid}", gid, "Digital", ""))
    return best_matches(title, out, key=lambda h: h["title"], limit=limit)
