#!/usr/bin/env python3
"""
audible_identifier.py — StreamScout resolver for **Audible** (no login).

Audible audiobooks live in TWO places that both show up in clickstream:

  * the direct Audible product page  ->  audible.com/pd/<slug>/<AUDIBLE_ASIN>
  * a parallel Amazon "Audible Audio Edition" page under a DIFFERENT ASIN
    ->  amazon.com/dp/<AMAZON_ASIN>

So for every title we emit TWO rows — one Audible, one Amazon — each carrying
its own PLATFORM label (via the per-row ``platform`` key StreamScout honours).

Discovery is a headless Chromium browse of the public US search pages (Audible
and Amazon both serve results to a plain headless session — no login, no key):

  * title (series)  -> every Audible search hit that matches the query, each
                       paired with its Amazon twin.
  * title (movie)   -> the single best-matching audiobook (+ its Amazon twin).
  * --url hint      -> a pasted audible.com /pd or /series link, or an
                       amazon.com /dp link; we resolve the other side from it.

Output row schema (unified with the rest of StreamScout):
    {season, episode, title, identifier, watch_url, platform}
SEASON/EPISODE are blank (audiobooks aren't seasoned).
"""
import argparse
import csv
import difflib
import os
import re
import sys
from datetime import datetime

PLATFORM = "Audible"
AUDIBLE = "https://www.audible.com"
AMAZON = "https://www.amazon.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_ASIN = r"B0[A-Z0-9]{8}"
_PD_IN_URL = re.compile(r"/pd/([^?/]*?)/(" + _ASIN + r")", re.I)
_DP_IN_URL = re.compile(r"/(?:dp|gp/product)/(" + _ASIN + r")", re.I)
_SERIES_IN_URL = re.compile(r"/series/([^?/]*?)/(" + _ASIN + r")", re.I)


# ── fuzzy title matching ──────────────────────────────────────────────────────
try:
    from match_gate import is_relevant           # shared over-match relevance floor
except ImportError:                              # keep the sibling importable
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from match_gate import is_relevant


def tokens(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def similarity(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    sa, sb = set(ta), set(tb)
    jacc = len(sa & sb) / len(sa | sb)
    seq = difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    return 0.5 * jacc + 0.5 * seq


def slug_to_title(slug):
    """'Becoming-Lady-Miss-Jacqueline-Audiobook' -> 'Becoming Lady Miss Jacqueline'."""
    s = re.sub(r"[-_]+", " ", slug or "").strip()
    s = re.sub(r"\b(Audiobook|Livre Audio|Hoerbuch)\b\s*$", "", s, flags=re.I).strip()
    return s


# ── playwright plumbing ───────────────────────────────────────────────────────
def _sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Audible needs Playwright + Chromium. Install once:\n"
            "  python3 -m pip install playwright && "
            "python3 -m playwright install chromium") from exc
    return sync_playwright


def _new_page(pw, headless=True):
    b = pw.chromium.launch(headless=headless)
    ctx = b.new_context(user_agent=UA, viewport={"width": 1360, "height": 1000},
                        locale="en-US")
    ctx.set_default_timeout(30000)
    return b, ctx, ctx.new_page()


# ── Audible search + series enumeration ───────────────────────────────────────
_AUD_JS = """() => {
    const out = [], seen = {};
    document.querySelectorAll("a[href*='/pd/']").forEach(a => {
        const m = (a.getAttribute('href') || '').match(
            /\\/pd\\/([^?/]*?)\\/(B0[A-Z0-9]{8})/);
        if (!m) return;
        const asin = m[2];
        if (seen[asin]) return;
        seen[asin] = 1;
        // climb to the product card for a clean heading + author
        let card = a;
        for (let i = 0; i < 6 && card; i++) {
            if (card.matches && card.matches('li.productListItem, li.bc-list-item'))
                break;
            card = card.parentElement;
        }
        const h = card ? card.querySelector("h3, [class*='bc-heading']") : null;
        const au = card ? card.querySelector("[class*='authorLabel'], "
                          + "li.authorLabel") : null;
        out.push({ asin: asin, slug: m[1],
                   title: (h ? h.textContent : '').trim(),
                   author: (au ? au.textContent : '').replace(/^\\s*By:?\\s*/i, '')
                             .trim() });
    });
    return out;
}"""


def audible_search(page, query):
    page.goto("%s/search?keywords=%s" % (AUDIBLE, _q(query)),
              wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    hits = page.evaluate(_AUD_JS) or []
    for h in hits:
        if not h.get("title"):
            h["title"] = slug_to_title(h.get("slug", ""))
    return hits


def audible_series_books(page, series_url):
    page.goto(series_url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    hits = page.evaluate(_AUD_JS) or []
    for h in hits:
        if not h.get("title"):
            h["title"] = slug_to_title(h.get("slug", ""))
    return hits


def audible_title_for_asin(page, asin, slug):
    """Confirm a pasted /pd link and return its clean title."""
    page.goto("%s/pd/%s/%s" % (AUDIBLE, slug or "x", asin),
              wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    t = page.evaluate("() => { const h = document.querySelector('h1'); "
                      "return h ? h.textContent.trim() : ''; }")
    return t or slug_to_title(slug)


# ── Amazon twin (the "via Amazon" ASIN, distinct from Audible's) ───────────────
_AMZ_JS = """() => {
    const out = [];
    document.querySelectorAll(
        "div[data-asin][data-component-type='s-search-result']").forEach(d => {
        const asin = d.getAttribute('data-asin');
        const h = d.querySelector('h2 span, h2');
        const title = (h ? h.textContent : '').trim();
        const fmt = (d.textContent.match(
            /Audible Audiobook|Audiobook|Kindle Edition|Paperback|Hardcover/)
            || [''])[0];
        if (asin && title) out.push({ asin: asin, title: title, fmt: fmt });
    });
    return out;
}"""


def amazon_twin(page, title, author=""):
    """Best amazon.com ASIN for the Audible audiobook edition of `title`."""
    q = title + (" " + author if author else "") + " audiobook"
    page.goto("%s/s?k=%s" % (AMAZON, _q(q)), wait_until="domcontentloaded")
    page.wait_for_timeout(2200)
    if re.search(r"captcha|Enter the characters you see", page.content(), re.I):
        return None
    cands = page.evaluate(_AMZ_JS) or []
    best, best_score = None, 0.0
    for c in cands:
        sc = similarity(title, c["title"])
        if "audiobook" in (c.get("fmt") or "").lower():
            sc += 0.15                          # prefer the audiobook edition
        if sc > best_score:
            best, best_score = c, sc
    if best and best_score >= 0.55:
        return best["asin"]
    return None


def amazon_title_for_asin(page, asin):
    page.goto("%s/dp/%s" % (AMAZON, asin), wait_until="domcontentloaded")
    page.wait_for_timeout(1800)
    t = page.title() or ""
    t = re.sub(r"^Amazon\.com:\s*", "", t)
    t = re.sub(r"\s*\(Audible Audio Edition\).*$", "", t)
    return t.strip()


def _q(s):
    import urllib.parse
    return urllib.parse.quote(s or "")


# ── row assembly ──────────────────────────────────────────────────────────────
def _rows_for(title, aud_slug, aud_asin, amz_asin):
    rows = []
    if aud_asin:
        url = "%s/pd/%s/%s" % (AUDIBLE, aud_slug or slugify(title), aud_asin)
        rows.append({"show": title, "season": "", "episode": "", "title": title,
                     "identifier": url, "watch_url": url, "platform": "Audible"})
    if amz_asin:
        url = "%s/dp/%s" % (AMAZON, amz_asin)
        rows.append({"show": title, "season": "", "episode": "", "title": title,
                     "identifier": url, "watch_url": url, "platform": "Amazon"})
    return rows


def slugify(title):
    s = re.sub(r"[^A-Za-z0-9]+", "-", (title or "").strip()).strip("-")
    return (s + "-Audiobook") if s else "Audiobook"


def parse_url(url):
    """('audible-pd', asin, slug) | ('audible-series', asin, slug)
    | ('amazon', asin, '') | None."""
    u = url or ""
    m = _SERIES_IN_URL.search(u)
    if m and "audible." in u:
        return ("audible-series", m.group(2), m.group(1))
    m = _PD_IN_URL.search(u)
    if m:
        return ("audible-pd", m.group(2), m.group(1))
    if "amazon." in u:
        m = _DP_IN_URL.search(u)
        if m:
            return ("amazon", m.group(1), "")
    return None


# ── main resolve ──────────────────────────────────────────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None, headless=True):
    sp = _sync_playwright()
    with sp() as pw:
        browser, ctx, page = _new_page(pw, headless=headless)
        try:
            picks = []          # list of dicts: {title, slug, asin, author}
            show = title or ""

            hint = parse_url(url) if url else None
            if hint:
                kind_, asin, slug = hint
                if kind_ == "audible-series":
                    picks = audible_series_books(page, url)
                    show = slug_to_title(slug)
                elif kind_ == "audible-pd":
                    t = audible_title_for_asin(page, asin, slug)
                    picks = [{"title": t, "slug": slug, "asin": asin, "author": ""}]
                    show = t
                elif kind_ == "amazon":
                    # pasted an amazon /dp — recover the Audible side by title
                    t = amazon_title_for_asin(page, asin)
                    show = t or title or ""
                    aud = _best_audible(page, t or title or "", want_one=True)
                    a_asin = aud[0]["asin"] if aud else None
                    a_slug = aud[0]["slug"] if aud else None
                    return (show, _rows_for(show, a_slug, a_asin, asin))
            elif title:
                if kind == "movie":
                    picks = _best_audible(page, title, want_one=True)
                else:
                    picks = _best_audible(page, title, want_one=False)
                if picks:
                    show = picks[0]["title"] if kind == "movie" else title

            if not picks:
                return (show, [])

            rows = []
            for p in picks:
                amz = amazon_twin(page, p["title"], p.get("author", ""))
                rows.extend(_rows_for(p["title"], p.get("slug"), p.get("asin"), amz))
            return (show, rows)
        finally:
            try:
                ctx.close(); browser.close()
            except Exception:  # noqa: BLE001
                pass


def _best_audible(page, query, want_one=True):
    """Rank Audible search hits against the query.  want_one -> [best];
    else -> every hit that plausibly matches the query title."""
    hits = audible_search(page, query)
    scored = []
    qtok = set(tokens(query))
    for h in hits:
        ttok = set(tokens(h["title"]))
        shared = len(qtok & ttok)
        sc = similarity(query, h["title"])
        scored.append((sc, shared, h))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if not scored:
        return []
    if want_one:
        top = scored[0][2]
        return [top] if is_relevant(query, top["title"]) else []
    # series: keep only hits that are genuinely the same title. The shared
    # relevance floor drops incidental single-word overlaps and unrelated hits
    # (no more "least-bad" fallback that let unrelated audiobooks through).
    return [h for sc, shared, h in scored if is_relevant(query, h["title"])]


# ── standalone CLI ────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Audible title -> direct Audible + via-Amazon listen links.")
    ap.add_argument("--type", choices=["movie", "series"], default="series")
    ap.add_argument("--title")
    ap.add_argument("--url", help="an audible.com /pd or /series, or amazon.com "
                                   "/dp link")
    ap.add_argument("--production", default="", help="PRODUCTION column value")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    title = args.title
    if not args.url and not title:
        title = input("What title?: ").strip()
    if not args.url and not title:
        print("Need a --title or --url."); return 1

    try:
        show, rows = resolve(title=title, url=args.url, kind=args.type)
    except Exception as e:  # noqa: BLE001
        print(f"  ! Fetch/parse failed: {e!r}"); return 2
    if not rows:
        print("  ! Nothing found on Audible. Try --url with an audible.com /pd "
              "link."); return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (title or show or "audible").lower()).strip("-")
    out = os.path.join(args.outdir, f"lookup_audible_{args.type}_{safe}_{stamp}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([r["title"], r["identifier"], args.production,
                        r.get("platform", PLATFORM), ""])

    titles = len({r["title"] for r in rows})
    print(f"\n{len(rows)} link(s) across {titles} title(s) for {show!r}:")
    for r in rows:
        print(f"   [{r.get('platform','?'):7s}] {r['title'][:40]:40s} "
              f"{r['identifier']}")
    print(f"\nCSV: {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
