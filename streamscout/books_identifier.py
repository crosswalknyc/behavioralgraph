#!/usr/bin/env python3
"""
books_identifier.py — StreamScout resolver for **Books** (purchase + listen +
library), the print/ebook/audiobook sibling of the streaming resolvers.

A book (or a whole franchise) shows up in clickstream across three kinds of
surface, and we grab a URL for each one we can reach with NO login:

  purchase / read     Amazon Kindle .......  amazon.com/dp/<ASIN>
                      Apple Books (eBook) .  books.apple.com/us/book/<slug>/id<ID>
                      Google Play Books ...  play.google.com/store/books/details?id=<ID>
  audiobook / listen  Audible .............  audible.com/pd/<slug>/<ASIN>
                      Amazon (audio ed.) ..  amazon.com/dp/<ASIN>
                      Apple Books (audio) .  books.apple.com/us/audiobook/<slug>/id<ID>
  library (holds)     Libby / OverDrive ...  overdrive.com/media/<ID>

The hard part with books is DISAMBIGUATION: indie titles like "Cruel Saints" or
"Tears of Betrayal" collide with dozens of unrelated books. So we **anchor on the
author**: Apple's audiobook catalog returns a clean author for the query, and we
reuse that author to filter every other source (killing "Michelle Hauck"-style
near-name false matches). Pass --author to anchor explicitly; otherwise we derive
it automatically.

Two ways to run:
  • Single title (StreamScout platform "books"):
        resolve(title="Merciless Saints") -> (show, rows)
  • Whole franchise (distinct book titles that roll up to ONE SHOW):
        python3 books_identifier.py --franchise "Merciless Saints" \\
            --titles "Merciless Saints" "Cruel Saints" "Ruthless Saints" \\
                     "Tears of Betrayal" "Tears of Salvation"

Output row schema (unified with the rest of StreamScout):
    {show, season, episode, title, identifier, watch_url, platform, format}
For a franchise, SHOW is the franchise name on every row and SEASON reads
"Book 1", "Book 2", … . For a single title, SHOW is the book title and SEASON is
blank. PLATFORM is set per row (Audible / Amazon / Apple Books / Google Play
Books / Libby), so one run fans out across every store.
"""
import argparse
import csv
import difflib
import gzip
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime

PLATFORM = "Books"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# canonical URL builders per source
APPLE = "https://books.apple.com"
AUDIBLE = "https://www.audible.com"
AMAZON = "https://www.amazon.com"
GPLAY = "https://play.google.com/store/books/details?id=%s"
OVERDRIVE = "https://www.overdrive.com"

_STOP = {"the", "a", "an", "of", "and", "or", "to", "for", "in", "on"}


# ── HTTP ──────────────────────────────────────────────────────────────────────
def _http_get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en;q=0.9",
                                               **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def _http_json(url, headers=None, timeout=20):
    txt = _http_get(url, headers=headers, timeout=timeout)
    try:
        return json.loads(txt) if txt else None
    except Exception:  # noqa: BLE001
        return None


def _q(s):
    return urllib.parse.quote(s or "")


# ── text matching (author-anchored) ───────────────────────────────────────────
def _toks(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def _norm(s):
    return " ".join(_toks(s))


def _seq(a, b):
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def title_match(query, cand):
    """True if `cand` is the same book as `query`. Requires every DISTINCTIVE
    (non-stop-word) query token to be present, plus a healthy overall ratio —
    so "Merciless Saints" won't match bare "Saints" and "Tears of Betrayal"
    needs both 'tears' and 'betrayal'."""
    qt, ct = _toks(query), _toks(cand)
    if not qt or not ct:
        return False
    distinctive = set(qt) - _STOP
    if not distinctive:
        distinctive = set(qt)
    if not distinctive.issubset(set(ct)):
        return False
    if _seq(query, cand) >= 0.5:
        return True
    # Long-subtitle case (common on Amazon/retail): the whole query phrase
    # appears verbatim in the candidate, e.g. "Cruel Saints" inside
    # "Cruel Saints: An Arranged Marriage Mafia Romance (The Saints Series)".
    nq = _norm(query)
    return bool(nq) and nq in _norm(cand)


def author_match(anchor, cand_author):
    """Surname-level author guard. With a known anchor author, the candidate's
    author must carry the anchor's LAST name (Michelle *Heard* != Michelle
    *Hauck*). No anchor -> accept (nothing to filter on)."""
    a = _toks(anchor)
    if not a:
        return True
    c = _toks(cand_author)
    if not c:
        return False
    return a[-1] in c


def hit(title, url, ident, platform, fmt, author=""):
    return {"show": "", "season": "", "episode": "", "title": (title or "").strip(),
            "identifier": url, "watch_url": url, "platform": platform,
            "format": fmt, "store_id": str(ident or ""), "author": author}


# ── Apple Books (iTunes Search API — audiobook + ebook) ───────────────────────
def _apple_search(title, entity, author=None, limit=8):
    d = _http_json("https://itunes.apple.com/search?term=%s&entity=%s"
                   "&country=us&limit=%d" % (_q(title), entity, limit)) or {}
    out = []
    for r in d.get("results", []):
        name = r.get("collectionName") or r.get("trackName") or ""
        url = (r.get("collectionViewUrl") or r.get("trackViewUrl") or "").split("?")[0]
        who = r.get("artistName") or ""
        if not (name and url):
            continue
        if not title_match(title, name):
            continue
        if author and not author_match(author, who):
            continue
        m = re.search(r"/id(\d+)", url)
        out.append((name, url, m.group(1) if m else "", who))
    return out


def apple_books(title, author=None):
    """Apple Books audiobook + ebook rows (audiobook is the reliable one)."""
    rows, seen = [], set()
    for entity, plat, fmt in (("audiobook", "Apple Books", "Audiobook"),
                              ("ebook", "Apple Books", "eBook")):
        for name, url, ident, who in _apple_search(title, entity, author):
            if url in seen:
                continue
            seen.add(url)
            rows.append(hit(name, url, ident, plat, fmt, who))
    return rows


def apple_author(title):
    """Derive the anchor author from Apple's clean audiobook catalog."""
    d = _http_json("https://itunes.apple.com/search?term=%s&entity=audiobook"
                   "&country=us&limit=5" % _q(title)) or {}
    for r in d.get("results", []):
        name = r.get("collectionName") or r.get("trackName") or ""
        if title_match(title, name) and r.get("artistName"):
            return r["artistName"]
    return ""


# ── Libby / OverDrive (public search -> universal /media/<id>) ────────────────
def libby(title, author=None, max_check=6):
    """Search OverDrive (Libby's catalog) and return the universal, library-
    agnostic overdrive.com/media/<id> for each matching format (eBook +
    Audiobook). Each media page's og:title is 'eBook - <Title>' / 'Audiobook -
    <Title>' and carries 'by <Author>', which we use to filter."""
    q = title + (" " + author if author else "")
    html = _http_get("%s/search?q=%s" % (OVERDRIVE, _q(q)))
    ids, seen = [], set()
    for m in re.finditer(r"/media/(\d+)", html):
        i = m.group(1)
        if i not in seen:
            seen.add(i)
            ids.append(i)
    rows = []
    for i in ids[:max_check]:
        page = _http_get("%s/media/%s" % (OVERDRIVE, i))
        if not page:
            continue
        mt = re.search(r'<meta property="og:title" content="([^"]+)"', page)
        og = (mt.group(1) if mt else "").strip()
        fmt = "eBook" if og.lower().startswith("ebook") else (
            "Audiobook" if "audiobook" in og.lower() else "")
        name = re.sub(r"^\s*(?:e?book|audiobook)\s*-\s*", "", og, flags=re.I).strip()
        who = ""
        ma = re.search(r"\bby ([A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){0,3})", page)
        if ma:
            who = ma.group(1)
        if not title_match(title, name):
            continue
        if author and not author_match(author, who):
            continue
        rows.append(hit(name, "%s/media/%s" % (OVERDRIVE, i), i, "Libby", fmt, who))
    return rows


# ── Google Play Books (best-effort: Google Books API; tolerates rate limits) ──
def google_play_books(title, author=None):
    key = os.environ.get("GOOGLE_BOOKS_API_KEY", "").strip()
    q = "intitle:" + title + (" inauthor:" + author if author else "")
    url = ("https://www.googleapis.com/books/v1/volumes?q=%s&country=US&maxResults=5"
           % _q(q)) + ("&key=" + key if key else "")
    d = _http_json(url)
    if not d:
        return []
    rows, seen = [], set()
    for it in d.get("items", []):
        vi = it.get("volumeInfo", {})
        name = vi.get("title", "")
        who = ", ".join(vi.get("authors", []) or [])
        vid = it.get("id", "")
        if not (name and vid):
            continue
        if not title_match(title, name):
            continue
        if author and not author_match(author, who):
            continue
        if vid in seen:
            continue
        seen.add(vid)
        rows.append(hit(name, GPLAY % vid, vid, "Google Play Books", "eBook", who))
    return rows


# ── Audible + Amazon (audio + Kindle + print) via one Playwright pass ─────────
# A richer search-result evaluator than Audible's: per card we pull the ASIN,
# title, AUTHOR line, and EVERY format badge — so we can author-gate (critical
# for generic titles like "Cruel Saints", which collide across authors) and pick
# the right edition per format.
_AMZ_RICH_JS = r"""() => {
  const FMT = /^(Kindle(?: Edition)?|Paperback|Hardcover|Mass Market Paperback|Audiobook|Audible Audiobook|Board [Bb]ook|Library Binding|Spiral-bound)$/;
  const out = [];
  document.querySelectorAll(
      "div[data-asin][data-component-type='s-search-result']").forEach(d => {
    const asin = d.getAttribute('data-asin');
    if (!asin) return;
    const h = d.querySelector('h2');
    const title = (h ? h.textContent : '').trim();
    if (!title) return;
    let author = '';
    // (a) explicit "by <Author>" secondary row
    d.querySelectorAll('div.a-row').forEach(r => {
      const t = (r.textContent || '').trim();
      if (!author && /^by\s+/i.test(t)) {
        author = t.replace(/^by\s+/i, '')
                  .split(/\s*[(|·]|,\s+/)[0].trim();
      }
    });
    // (b) fallback: an author/contributor page link (/e/ or byline anchor)
    if (!author) {
      const a = d.querySelector("a[href*='/e/'], a.s-underline-text[href*='field-author']");
      if (a) author = (a.textContent || '').replace(/^by\s+/i, '').trim();
    }
    const fmts = new Set();
    d.querySelectorAll('a, span').forEach(e => {
      const t = (e.textContent || '').trim();
      if (FMT.test(t)) fmts.add(t.toLowerCase());
    });
    const txt = d.textContent || '';
    ['Kindle', 'Paperback', 'Hardcover', 'Audiobook'].forEach(f => {
      if (new RegExp('\\b' + f + '\\b', 'i').test(txt)) fmts.add(f.toLowerCase());
    });
    out.push({ asin, title, author, fmts: [...fmts] });
  });
  return out.slice(0, 24);
}"""

# what counts as each edition (substring test against a card's format badges)
_FMT_ACCEPT = {
    "audiobook": ("audiobook",),
    "kindle": ("kindle",),
    "print": ("paperback", "hardcover", "mass market", "library binding",
              "board book", "spiral"),
}


def audible_amazon(title, author=None):
    """One Chromium context: the Audible listen link plus the three Amazon
    editions (audiobook / Kindle / print), each author-gated and retried. Returns
    [] gracefully if Playwright/Chromium is missing."""
    try:
        import audible_identifier as A
    except Exception:  # noqa: BLE001
        return []
    try:
        sp = A._sync_playwright()
    except Exception:  # noqa: BLE001
        return []
    rows = []
    q = title + (" " + author if author else "")
    with sp() as pw:
        browser, ctx, page = A._new_page(pw, headless=True)
        try:
            # Audible listen link (Audible's own site)
            picks = A._best_audible(page, q, want_one=True)
            if picks and title_match(title, picks[0].get("title", "")):
                p = picks[0]
                if not author:
                    author = p.get("author", "") or author
                aud_url = "%s/pd/%s/%s" % (AUDIBLE, p.get("slug") or A.slugify(title),
                                           p["asin"])
                rows.append(hit(p["title"], aud_url, p["asin"], "Audible",
                                "Audiobook", p.get("author", "")))
            # Amazon editions — hardened, author-gated
            for edition, label in (("audiobook", "Audiobook"), ("kindle", "Kindle"),
                                   ("print", "Print")):
                asin = _amazon_edition(A, page, title, author, edition)
                if asin:
                    rows.append(hit(title, "%s/dp/%s" % (AMAZON, asin), asin,
                                    "Amazon", label, author or ""))
        finally:
            try:
                ctx.close(); browser.close()
            except Exception:  # noqa: BLE001
                pass
    return rows


_EDITION_SUFFIX = {"audiobook": "audiobook", "kindle": "kindle", "print": "paperback"}


def _amazon_edition(A, page, title, author=None, edition="print", tries=2):
    """Return the best amazon.com ASIN for one edition of `title`. Format-targeted
    search + a rich card read; author-gated with a STRICT (author must match) pass
    that relaxes to author-agnostic only if nothing matched — so a generic title
    won't grab the wrong author's book while a real edition exists."""
    accept = _FMT_ACCEPT[edition]
    suffix = _EDITION_SUFFIX[edition]
    q = title + (" " + author if author else "") + " " + suffix
    cands = []
    for attempt in range(tries):
        page.goto("%s/s?k=%s" % (AMAZON, A._q(q)), wait_until="domcontentloaded")
        page.wait_for_timeout(2200 + attempt * 1500)
        if re.search(r"captcha|Enter the characters you see|Robot or human",
                     page.content(), re.I):
            continue                      # bot wall — reload and retry
        cands = page.evaluate(_AMZ_RICH_JS) or []
        if cands:
            break

    def _pick(require_author):
        best, best_sc = None, 0.0
        for c in cands:
            t = c.get("title", "")
            if not title_match(title, t):
                continue
            cfmts = " ".join(c.get("fmts", [])).lower()
            if accept and not any(f in cfmts for f in accept):
                continue                  # wrong edition on this card
            ca = c.get("author", "")
            au_ok = (not author) or (ca and author_match(author, ca))
            if require_author and not au_ok:
                continue
            sc = _seq(title, t)
            if au_ok and author and ca:
                sc += 0.25                # reward a confirmed author match
            if any(f in cfmts for f in accept):
                sc += 0.2
            if sc > best_sc:
                best, best_sc = c, sc
        return best["asin"] if best and best_sc >= 0.5 else None

    # strict author pass first; fall back to author-agnostic only if empty
    return _pick(require_author=True) or _pick(require_author=False)


# ── physical-retail proxy terms (bot-walled retailers) ────────────────────────
# We can't scrape B&N / BAM / Target / Walmart / Bookshop / Costco hands-off, but
# their product paths carry a stable prefix that acts as a commerce-context guard.
# So we emit a franchise-grain proxy term per prefix (retailer-agnostic — one
# 'p/…' covers every /p/ storefront).
#
# IMPORTANT: these feed the CONTENT map, not the hostmap. The content map does
# NOT strip punctuation, so the slug stays URL-shaped (hyphenated): p/merciless-
# saints, NOT the space-normalized hostmap form. Each template embeds {slug}.
_PROXY_PREFIXES = [
    ("p/{slug}",       "Target / Books-A-Million (/p/)"),
    ("ip/{slug}",      "Walmart (/ip/)"),
    ("w/{slug}",       "Barnes & Noble (/w/)"),
    ("books/{slug}",   "Bookshop.org (/p/books/)"),
    ("{slug}.product", "Costco (.product.)"),
]


def _proxy_slug(title):
    """URL-style slug for the content map: lowercase, drop apostrophes, then
    collapse any run of non-alphanumerics to a single hyphen (matches how these
    retailers slugify — "Tears of Betrayal" -> "tears-of-betrayal")."""
    s = (title or "").lower().replace("'", "").replace("\u2019", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def retail_proxies(title):
    """Deterministic content-map proxy terms for the bot-walled retailers."""
    s = _proxy_slug(title)
    if not s:
        return []
    return [hit(title, tmpl.format(slug=s), "", label, "Proxy", "")
            for tmpl, label in _PROXY_PREFIXES]


# ── the sweep ─────────────────────────────────────────────────────────────────
API_SOURCES = (apple_books, libby, google_play_books)


def sweep(title, author=None, use_browser=True, proxies=True):
    """Every book URL we can find for a single title. Auto-derives the author
    anchor from Apple's audiobook catalog if not supplied. With ``proxies`` we
    also append the deterministic physical-retail proxy terms."""
    if not author:
        author = apple_author(title)
    rows = []
    for fn in API_SOURCES:
        try:
            rows.extend(fn(title, author))
        except Exception as e:  # noqa: BLE001
            print("    ! %s failed: %s" % (getattr(fn, "__name__", fn), e))
    if use_browser:
        try:
            rows.extend(audible_amazon(title, author))
        except Exception as e:  # noqa: BLE001
            print("    ! audible/amazon failed: %s" % e)
    if proxies:
        rows.extend(retail_proxies(title))
    # de-dupe on the final URL
    seen, out = set(), []
    for r in rows:
        u = r["identifier"]
        if u and u not in seen:
            seen.add(u)
            out.append(r)
    return author, out


# ── StreamScout-compatible entry point (single title) ─────────────────────────
def resolve(title=None, url=None, kind="series", seasons=None, author=None,
            use_browser=True, proxies=True):
    """StreamScout calls this. Sweeps every book store for one title and returns
    (show, rows) with a per-row PLATFORM label. SHOW defaults to the book title;
    SEASON stays blank for a single title."""
    if not title:
        return ("", [])
    _author, rows = sweep(title, author=author, use_browser=use_browser,
                          proxies=proxies)
    for r in rows:
        r["show"] = title
        r["season"] = ""
    return (title, rows)


# ── franchise batch (distinct titles -> one SHOW) ─────────────────────────────
def resolve_franchise(franchise, titles, author=None, use_browser=True,
                      proxies=True):
    """Sweep each book in a franchise; stamp SHOW = franchise on every row and
    SEASON = 'Book N'. Author is derived once (from book 1) and reused as the
    anchor for the whole series unless supplied."""
    all_rows = []
    anchor = author
    for idx, t in enumerate(titles, 1):
        if not anchor:
            anchor = apple_author(t)
        print("  · Book %d: %s   (author anchor: %s)"
              % (idx, t, anchor or "—"))
        _a, rows = sweep(t, author=anchor, use_browser=use_browser,
                         proxies=proxies)
        for r in rows:
            r["show"] = franchise
            r["season"] = "Book %d" % idx
            r["book_title"] = t
        by_plat = {}
        for r in rows:
            by_plat.setdefault(r["platform"], 0)
            by_plat[r["platform"]] += 1
        print("      -> %d link(s): %s" % (
            len(rows), ", ".join("%s×%d" % (k, v) for k, v in sorted(by_plat.items()))
            or "none"))
        all_rows.extend(rows)
    return anchor, all_rows


# ── CSV + CLI ─────────────────────────────────────────────────────────────────
def _write_csv(show, rows, outdir, production=""):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", (show or "books").lower()).strip("-")
    path = os.path.join(outdir, "lookup_books_%s_%s.csv" % (safe, stamp))
    # sort: by book (season), then platform, then format
    rows = sorted(rows, key=lambda r: (r.get("season", ""), r.get("platform", ""),
                                       r.get("format", "")))
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "URL", "PRODUCTION", "PLATFORM", "SEASON"])
        for r in rows:
            w.writerow([r.get("show") or show, r["identifier"], production,
                        r.get("platform", ""), r.get("season", "")])
    return path


def main():
    ap = argparse.ArgumentParser(
        description="Books -> purchase / audiobook / library URLs across every "
                    "store (Amazon, Audible, Apple Books, Google Play, Libby).")
    ap.add_argument("--title", help="a single book title")
    ap.add_argument("--franchise", help="franchise SHOW name (rolls titles up)")
    ap.add_argument("--titles", nargs="+", help="the book titles in the franchise")
    ap.add_argument("--author", help="anchor author (else auto-derived)")
    ap.add_argument("--production", default="", help="PRODUCTION column value")
    ap.add_argument("--no-browser", action="store_true",
                    help="skip the Audible/Amazon Playwright pass (API sources only)")
    ap.add_argument("--no-proxies", action="store_true",
                    help="skip the physical-retail <prefix>/<title> proxy rows")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    args = ap.parse_args()

    use_browser = not args.no_browser
    proxies = not args.no_proxies
    if args.franchise and args.titles:
        print("\nSweeping franchise %r (%d books) …" % (args.franchise,
                                                        len(args.titles)))
        anchor, rows = resolve_franchise(args.franchise, args.titles,
                                         author=args.author, use_browser=use_browser,
                                         proxies=proxies)
        show = args.franchise
    else:
        title = args.title or input("What book?: ").strip()
        if not title:
            print("Need a --title or --franchise + --titles."); return 1
        print("\nSweeping book %r …" % title)
        anchor, rows = sweep(title, author=args.author, use_browser=use_browser,
                             proxies=proxies)
        for r in rows:
            r["show"] = title
        show = title

    if not rows:
        print("  Nothing found."); return 2
    path = _write_csv(show, rows, args.outdir, args.production)
    plats = {}
    for r in rows:
        plats[r["platform"]] = plats.get(r["platform"], 0) + 1
    print("\nFound %d link(s) for %r  (author anchor: %s):"
          % (len(rows), show, anchor or "—"))
    for r in sorted(rows, key=lambda r: (r.get("season", ""), r.get("platform", ""))):
        tag = ("%s " % r["season"]) if r.get("season") else ""
        print("   %-8s [%-18s %-9s] %s"
              % (tag, r.get("platform", ""), r.get("format", ""), r["identifier"]))
    print("\n  by platform: " + ", ".join("%s×%d" % (k, v)
                                          for k, v in sorted(plats.items())))
    print("\nCSV: %s\n" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
