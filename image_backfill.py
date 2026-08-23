"""Reusable image-backfill helpers for the admin "Populate Missing Images" button.

This module is a distilled, in-process version of `scripts/backfill_profile_images.py`
that lives inside `bg-webapp/` so it ships with the Render deployment. The CLI
script remains the source of truth for large batch runs; this module exposes
only what the Flask endpoint needs:

  * `master_category(cat)`         - category bucket (TALENT / CONTENT / BRAND / ...)
  * `normalize_search_name(name)`  - strip cohort / fan-tier / year suffixes
  * `resolve_image_url(name, master)` -> (url, source_tag) or (None, 'none')
  * `download_image(url)`          -> (bytes, ext) or None

Rules (per user, 2026-07-01, restated 2026-07-08, updated 2026-07-29):
  * TALENT master category  -> IMDB headshot (`nm` result)
      Fallbacks: Wikipedia thumbnail, Google image search, DuckDuckGo image
  * CONTENT (shows / movies) -> IMDB title poster (`tt` result)
      Fallbacks: Wikipedia thumbnail, Google image search, DuckDuckGo image
  * BRAND / PLATFORMS / SPORT / TRENDS / OTHER
      -> Wikipedia thumbnail, Google image search, DuckDuckGo image, Google favicon

Google image search (2026-07-29, per Jenna): when neither Wikipedia nor IMDb
returns a hit, we now go straight to a web image search instead of stopping
at the old DuckDuckGo fallback. The lookup prefers Google's Custom Search
JSON API when the `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_ID` env vars are
configured on the deployment (100 free queries / day; the admin button
only hits this for the handful of profiles missing an image in one batch,
so we stay in the free tier). If those env vars aren't set, the lookup
falls back to a Bing image search HTML scrape - Bing serves fully-populated
image results without an API key and without gating server-side user
agents behind a JavaScript wall the way modern Google does. In practice
this returns the same "search the web for a photo of X" result quality
that the user asked for.

Every downloaded image is:
  1. Fetched to memory (size-checked, max 2 MB per admin uploader)
  2. Returned to the caller who uploads it to S3 and updates the image cache

Nothing in this module touches S3 or the Flask request. That belongs in the
caller (see the /api/admin/backfill-missing-images endpoint in app.py).
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


# --------------------------------------------------------------------------- config

MAX_IMAGE_BYTES = 2 * 1024 * 1024
IMDB_SUGGEST_URL = "https://v3.sg.media-imdb.com/suggestion/x/{q}.json"
WIKI_SEARCH_URL = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
GOOGLE_FAVICON_URL = "https://www.google.com/s2/favicons?sz=256&domain={domain}"
GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"
BING_IMAGES_URL = "https://www.bing.com/images/search?form=HDRSC2&first=1&safeSearch=Strict&q={q}"
DDG_IMAGES_URL = "https://duckduckgo.com/i.js"

HEADERS_JSON = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.imdb.com/",
}
HEADERS_WIKI = {
    "User-Agent": "CrosswalkImageBackfill/1.0 (jenna@crosswalknyc.com)",
    "Accept": "application/json",
}
HEADERS_IMG = {
    "User-Agent": "CrosswalkImageBackfill/1.0 (jenna@crosswalknyc.com)",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://en.wikipedia.org/",
}

# Master-category buckets — mirrors MASTER_CATEGORIES in templates/index.html.
MASTER_CATEGORIES: dict[str, set[str]] = {
    "TALENT": {
        "ACTOR", "ATHLETE", "COMEDIAN", "CREATOR/INFLUENCER", "INFLUENCER/CREATOR",
        "EMERGING TALENT", "HOST/PERSONALITY", "MUSICIAN/BAND", "PODCASTER",
        "POLITICS/ACTIVIST", "WRITER/DIRECTOR/AUTHOR/ARTIST",
    },
    "CONTENT": {
        "ANIMATION", "AWARDS", "BROADCAST", "CABLE", "CONTENT", "FILM", "MOVIE",
        "MOVIES", "MUSIC EVENT", "MUSIC FESTIVAL", "MUSIC TOUR", "OSCARS",
        "PODCAST", "PODCASTS", "SERIES", "SHOW", "SHOWS", "SPORT EVENT",
        "SPORTING EVENT", "SPORTS EVENT", "TALK SHOW", "TELEVISION", "TOUR",
        "TV", "TV SERIES", "TV SHOW", "TVSERIES",
        "MOVIE - HORROR", "MOVIE - COMEDY", "MOVIE - ACTION", "MOVIE - DRAMA",
        "ANIMATION MOVIE", "FRANCHISE FILM", "GAME SHOW", "ADULT ANIMATION",
    },
    "PLATFORMS": {
        "APP/PLATFORM", "BROADCAST/CABLE", "MEDIA", "MOVIE THEATER", "PLATFORMS",
        "SEARCH ENGINE/AI", "SOCIAL MEDIA", "STREAMING MUSIC", "STREAMING VIDEO",
        "STREAMING/PLATFORM", "VIRTUAL MVPD/FAST", "VIRTUAL MVPD FAST",
        "VMVPD/FAST", "VMVPD",
    },
    "SPORT": {
        "MILB", "MLB", "NBA", "NFL", "SPORTS ORGANIZATIONS",
        "SPORTS ORGANIZATION", "WNBA",
    },
    "TRENDS": {"TRENDS"},
    "GEN POP": {"GEN POP", "GENERAL POPULATION"},
    "SVOD ACQUISITION": {"SVOD ACQUISITION"},
}


def master_category(cat: str) -> str:
    c = (cat or "").strip().upper()
    if c == "SVOD ACQUISITION":
        return "SVOD ACQUISITION"
    for master, subs in MASTER_CATEGORIES.items():
        if c in subs:
            return master
    if c.startswith("SERIES"):
        return "CONTENT"
    if c.startswith("MOVIE"):
        return "CONTENT"
    return "BRAND"


# --------------------------------------------------------------------------- name normalization

_COHORT_SUFFIX_RE = re.compile(
    r"(?:\s+(?:\d{4}(?:[-\u2013]\d{2,4})?|\d+\s*[Pp]lus|\d+\+|"
    r"avid\s*fan|casual\s*fan|super\s*fan|touchpoints?\s*superfan|"
    r"\d+\+?\s*touchpoints?(?:\s*superfan)?|total\s*universe(?:\s*\d{4})?))+\s*$",
    re.IGNORECASE,
)


def normalize_search_name(raw: str) -> str:
    """Strip cohort / variant / year suffixes so image lookup gets the bare subject."""
    s = (raw or "").replace("_", " ").strip()
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
    s = re.split(r"\s+[-\u2013\u2014]\s+", s, maxsplit=1)[0]
    s = _COHORT_SUFFIX_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _tokens(s: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", _strip_accents(s))
        if len(t) >= 2 and t not in {"the", "a", "an", "of", "and", "&", "feat", "ft"}
    }


def _collapsed(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _strip_accents(s))


def _plausible_match(query: str, label: str) -> bool:
    q, l = _tokens(query), _tokens(label)
    if not q or not l:
        return False
    if q.issubset(l) or l.issubset(q):
        return True
    qc, lc = _collapsed(query), _collapsed(label)
    if not qc or not lc:
        return False
    short, long = (qc, lc) if len(qc) <= len(lc) else (lc, qc)
    return len(short) >= 5 and short in long


# --------------------------------------------------------------------------- image sources

class RateLimited(Exception):
    """Upstream API returned 429/5xx - back off and skip this profile for now."""


def _http_get(url: str, headers: dict, *, timeout: float = 10.0, is_json: bool = False):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code == 429 or 500 <= e.code < 600:
            raise RateLimited(f"HTTP {e.code} for {url}") from e
        raise
    if is_json:
        return json.loads(data.decode("utf-8", errors="replace"))
    return data, content_type


def imdb_lookup(name: str, *, want_prefix: str) -> tuple[Optional[str], Optional[str]]:
    """Return (matched_label, imageUrl) for the first plausible match with `want_prefix`.

    want_prefix is 'nm' for people or 'tt' for titles (shows/movies).
    """
    if not name:
        return None, None
    url = IMDB_SUGGEST_URL.format(q=urllib.parse.quote(name.lower(), safe=""))
    try:
        data = _http_get(url, HEADERS_JSON, is_json=True)
    except RateLimited:
        raise
    except Exception:
        return None, None
    for it in data.get("d") or []:
        iid = (it.get("id") or "").strip()
        if not iid.startswith(want_prefix):
            continue
        label = (it.get("l") or "").strip()
        if not _plausible_match(name, label):
            continue
        img = ((it.get("i") or {}).get("imageUrl") or "").strip()
        if img:
            return label, img
    return None, None


def _wiki_search_title(name: str) -> Optional[str]:
    q = urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": name,
        "srlimit": 3, "format": "json", "origin": "*",
    })
    try:
        data = _http_get(f"{WIKI_SEARCH_URL}?{q}", HEADERS_WIKI, is_json=True)
    except Exception:
        return None
    hits = ((data or {}).get("query") or {}).get("search") or []
    return hits[0].get("title") if hits else None


def wiki_lookup(name: str) -> Optional[str]:
    if not name:
        return None
    candidates = [name, name.replace("&", "and")]
    searched = _wiki_search_title(name)
    if searched and searched not in candidates:
        candidates.insert(0, searched)
    for title in candidates:
        url = WIKI_SUMMARY_URL.format(title=urllib.parse.quote(title.replace(" ", "_")))
        try:
            data = _http_get(url, HEADERS_WIKI, is_json=True)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("type") in ("disambiguation",):
            continue
        img = ((data.get("originalimage") or {}).get("source")
               or (data.get("thumbnail") or {}).get("source"))
        if img:
            return img
    return None


def _domain_guesses(name: str) -> list[str]:
    s = _strip_accents(name)
    letters = re.sub(r"[^a-z0-9]+", "", s)
    hyphenated = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    guesses = []
    if letters:
        guesses.append(f"{letters}.com")
    if hyphenated and hyphenated != letters:
        guesses.append(f"{hyphenated}.com")
    first = re.split(r"\s+", s.strip())[0] if s.strip() else ""
    first_letters = re.sub(r"[^a-z0-9]+", "", first)
    if first_letters and first_letters not in {letters, hyphenated}:
        guesses.append(f"{first_letters}.com")
    return guesses


def google_favicon(name: str) -> Optional[str]:
    """Google favicon service at 256px. Ugly-but-recognizable last resort."""
    guesses = _domain_guesses(name)
    if not guesses:
        return None
    return GOOGLE_FAVICON_URL.format(domain=guesses[0])


def _google_cse_search(name: str) -> Optional[str]:
    """Google Custom Search JSON API. Free tier: 100 queries / day.

    Returns the first result whose URL looks like a real static image. Requires
    two env vars to be set on the deployment:
      * GOOGLE_CSE_API_KEY - a Google Cloud API key with Custom Search enabled
      * GOOGLE_CSE_ID      - the CSE id of an "image search across the web"
                              Programmable Search Engine
    Returns None (silently) if either env var is missing or the call fails.
    """
    api_key = (os.environ.get("GOOGLE_CSE_API_KEY") or "").strip()
    cse_id = (os.environ.get("GOOGLE_CSE_ID") or "").strip()
    if not api_key or not cse_id or not name:
        return None
    params = urllib.parse.urlencode({
        "key": api_key,
        "cx": cse_id,
        "q": name,
        "searchType": "image",
        "num": 5,
        "safe": "active",
        "imgSize": "large",
    })
    try:
        data = _http_get(
            f"{GOOGLE_CSE_URL}?{params}",
            {"User-Agent": HEADERS_JSON["User-Agent"], "Accept": "application/json"},
            is_json=True,
            timeout=8,
        )
    except Exception:
        return None
    for item in (data or {}).get("items") or []:
        link = (item.get("link") or "").strip()
        if not link.startswith(("http://", "https://")):
            continue
        low = link.lower().split("?", 1)[0]
        if low.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            return link
    return None


def _bing_image_scrape(name: str) -> Optional[str]:
    """Bing image search - the practical no-API-key stand-in for Google.

    Modern Google image search returns a "please enable JavaScript" gate to
    any server-side user agent, which makes an HTML scrape unreliable. Bing
    still serves the full image results page as static HTML with a JSON
    metadata blob per result (attribute `m="{...}"` with a `murl` field
    containing the original creator URL). That gives us Google-equivalent
    coverage without an API key.

    Returns the first result whose original media URL is a real static
    image (jpg / jpeg / png / webp). Skips SVGs and gifs (they render
    poorly in the profile grid).
    """
    if not name:
        return None
    url = BING_IMAGES_URL.format(q=urllib.parse.quote(name))
    req = urllib.request.Request(url, headers={
        "User-Agent": HEADERS_JSON["User-Agent"],
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    # Bing embeds each image result's metadata as m="{...}" where the JSON is
    # HTML-entity-encoded (&quot; instead of "). Pull out murl values directly
    # from the entity-encoded blob so we don't have to un-escape the whole
    # attribute value.
    murls = re.findall(r'&quot;murl&quot;:&quot;([^&\"]+)&quot;', html)
    for raw in murls:
        u = raw.strip()
        if not u.startswith(("http://", "https://")):
            continue
        low = u.lower().split("?", 1)[0]
        if low.endswith((".jpg", ".jpeg", ".png", ".webp")):
            return u
    # Second pass: some results carry the URL in a `m=` attribute that
    # url-encodes rather than html-entity-encodes. Try that shape too.
    metas = re.findall(r'\bm="(\{[^"]+\})"', html)
    for meta in metas:
        try:
            data = json.loads(meta.replace("&quot;", '"'))
        except Exception:
            continue
        u = (data.get("murl") or "").strip()
        if not u.startswith(("http://", "https://")):
            continue
        low = u.lower().split("?", 1)[0]
        if low.endswith((".jpg", ".jpeg", ".png", ".webp")):
            return u
    return None


def google_image_search(name: str) -> tuple[Optional[str], str]:
    """Web image search: prefer Google Custom Search API, fall back to Bing.

    Added 2026-07-29 (Jenna): when Wikipedia and IMDb both miss on a profile,
    the "Populate Missing Images" admin button now goes straight to a web
    image search instead of stopping at the DuckDuckGo proxy. When
    `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_ID` are set on the deployment we hit
    real Google via the Custom Search JSON API; otherwise we scrape Bing
    (which serves image results without a JavaScript gate and returns
    Google-equivalent quality for "find me a photo of X" queries).

    Returns (image_url, source_tag). source_tag is 'google-cse' when the
    real Google API served the hit, 'bing' when the Bing fallback served,
    or '' when nothing was found.
    """
    if not name:
        return None, ""
    hit = _google_cse_search(name)
    if hit:
        return hit, "google-cse"
    hit = _bing_image_scrape(name)
    if hit:
        return hit, "bing"
    return None, ""


def duckduckgo_image(name: str) -> Optional[str]:
    """DuckDuckGo image search - backup for when Google image search fails."""
    if not name:
        return None
    try:
        req = urllib.request.Request(
            f"https://duckduckgo.com/?q={urllib.parse.quote(name)}",
            headers={"User-Agent": HEADERS_JSON["User-Agent"]},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        m = re.search(r"vqd=['\"]?([\d-]+)", html)
        if not m:
            return None
        vqd = m.group(1)
        api = (
            f"{DDG_IMAGES_URL}?l=us-en&o=json&q={urllib.parse.quote(name)}"
            f"&vqd={vqd}&f=,,,,,&p=1"
        )
        req2 = urllib.request.Request(api, headers={
            "User-Agent": HEADERS_JSON["User-Agent"],
            "Referer": "https://duckduckgo.com/",
        })
        with urllib.request.urlopen(req2, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        for r in data.get("results") or []:
            img = r.get("image")
            if img and img.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png", ".webp")):
                return img
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------- image download

def _ext_from(content_type: str, url: str) -> str:
    ct = (content_type or "").lower().split(";")[0].strip()
    m = {
        "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
        "image/webp": "webp", "image/gif": "gif", "image/svg+xml": "svg",
    }.get(ct)
    if m:
        return m
    path = urllib.parse.urlparse(url).path.lower()
    for e in ("jpg", "jpeg", "png", "webp", "gif"):
        if path.endswith("." + e):
            return "jpeg" if e == "jpg" else e
    return "jpg"


def _wikimedia_thumb_candidates(url: str) -> list[str]:
    """Wikimedia refuses full-res originals with HTTP 400. Try a ladder of
    thumbnail sizes so we degrade gracefully as sizes miss."""
    if "upload.wikimedia.org" not in url:
        return [url]
    m = re.match(
        r"^(https?://upload\.wikimedia\.org/wikipedia/[^/]+)/"
        r"(?:thumb/)?([a-z0-9])/([a-z0-9]{2})/([^/]+?)(?:/(\d+)px-.+)?$",
        url, re.IGNORECASE,
    )
    if not m:
        return [url]
    base, a, ab, filename, existing_px = m.groups()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    thumb_name = filename if ext != "svg" else filename + ".png"

    def make(px: int) -> str:
        return f"{base}/thumb/{a}/{ab}/{filename}/{px}px-{thumb_name}"

    candidates: list[str] = []
    if existing_px:
        candidates.append(url)
    for px in (640, 800, 1024, 320, 1280, 240):
        u = make(px)
        if u not in candidates:
            candidates.append(u)
    return candidates


def download_image(url: str) -> Optional[tuple[bytes, str]]:
    """Fetch image bytes + normalized extension. Retries transient 429/5xx once."""
    if not url:
        return None
    candidates = _wikimedia_thumb_candidates(url) if "upload.wikimedia.org" in url else [url]

    data = ct = None
    for candidate in candidates:
        for attempt in range(2):
            try:
                data, ct = _http_get(candidate, HEADERS_IMG)
                break
            except RateLimited:
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                data = None
                break
            except urllib.error.HTTPError as e:
                if e.code == 400 and "upload.wikimedia.org" in candidate:
                    data = None
                    break
                data = None
                break
            except Exception:
                data = None
                break
        if data:
            break
    if data is None:
        return None
    if not data or len(data) < 200:
        return None
    if len(data) > MAX_IMAGE_BYTES:
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=82, optimize=True)
            if buf.tell() > MAX_IMAGE_BYTES:
                w, h = img.size
                scale = (MAX_IMAGE_BYTES / buf.tell()) ** 0.5
                img.thumbnail((int(w * scale), int(h * scale)))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=80, optimize=True)
            data = buf.getvalue()
            return data, "jpg"
        except Exception:
            return None
    ext = _ext_from(ct, url)
    if ext == "svg":
        try:
            import cairosvg
            data = cairosvg.svg2png(bytestring=data, output_width=512)
            ext = "png"
        except Exception:
            return None
    return data, ext


# --------------------------------------------------------------------------- resolver

def resolve_image_url(name: str, master: str) -> tuple[Optional[str], str]:
    """Return (image_url, source_tag) for the best available image, or (None, 'none').

    Sources by bucket (2026-07-29, per Jenna: fall back to Google image
    search when Wikipedia and IMDb both miss):
      TALENT     -> IMDB (nm), Wikipedia, Google image search, DuckDuckGo
      CONTENT    -> IMDB (tt), Wikipedia, Google image search, DuckDuckGo
      OTHER      -> Wikipedia, Google image search, DuckDuckGo, Google favicon
    """
    q = normalize_search_name(name)
    if not q:
        return None, "none"

    if master == "TALENT":
        try:
            label, url = imdb_lookup(q, want_prefix="nm")
        except RateLimited:
            return None, "rate_limited"
        if url:
            return url, f"imdb:{label}"
        wiki_url = wiki_lookup(q)
        if wiki_url:
            return wiki_url, "wiki"
        google_url, google_tag = google_image_search(q)
        if google_url:
            return google_url, google_tag
        ddg_url = duckduckgo_image(q)
        if ddg_url:
            return ddg_url, "duckduckgo"
        return None, "none"

    if master == "CONTENT":
        try:
            label, url = imdb_lookup(q, want_prefix="tt")
        except RateLimited:
            return None, "rate_limited"
        if url:
            return url, f"imdb-title:{label}"
        wiki_url = wiki_lookup(q)
        if wiki_url:
            return wiki_url, "wiki"
        google_url, google_tag = google_image_search(q)
        if google_url:
            return google_url, google_tag
        ddg_url = duckduckgo_image(q)
        if ddg_url:
            return ddg_url, "duckduckgo"
        return None, "none"

    # BRAND / PLATFORMS / SPORT / TRENDS / GEN POP / OTHER
    wiki_url = wiki_lookup(q)
    if wiki_url:
        return wiki_url, "wiki"
    google_url, google_tag = google_image_search(q)
    if google_url:
        return google_url, google_tag
    ddg_url = duckduckgo_image(q)
    if ddg_url:
        return ddg_url, "duckduckgo"
    fav_url = google_favicon(q)
    if fav_url:
        return fav_url, "google-favicon"
    return None, "none"
