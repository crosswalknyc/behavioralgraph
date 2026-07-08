"""Reusable image-backfill helpers for the admin "Populate Missing Images" button.

This module is a distilled, in-process version of `scripts/backfill_profile_images.py`
that lives inside `bg-webapp/` so it ships with the Render deployment. The CLI
script remains the source of truth for large batch runs; this module exposes
only what the Flask endpoint needs:

  * `master_category(cat)`         - category bucket (TALENT / CONTENT / BRAND / ...)
  * `normalize_search_name(name)`  - strip cohort / fan-tier / year suffixes
  * `resolve_image_url(name, master)` -> (url, source_tag) or (None, 'none')
  * `download_image(url)`          -> (bytes, ext) or None

Rules (per user, 2026-07-01, restated 2026-07-08):
  * TALENT master category  -> IMDB headshot (`nm` result)
      Fallbacks: Wikipedia thumbnail, DuckDuckGo image
  * CONTENT (shows / movies) -> IMDB title poster (`tt` result)
      Fallback: Wikipedia thumbnail
  * BRAND / PLATFORMS / SPORT / TRENDS / OTHER
      -> Wikipedia thumbnail, DuckDuckGo image, Google favicon
        (Google image search fallback is emulated via DuckDuckGo's image API
         because Google's search API requires an API key + billing setup)

Every downloaded image is:
  1. Fetched to memory (size-checked, max 2 MB per admin uploader)
  2. Returned to the caller who uploads it to S3 and updates the image cache

Nothing in this module touches S3 or the Flask request. That belongs in the
caller (see the /api/admin/backfill-missing-images endpoint in app.py).
"""
from __future__ import annotations

import io
import json
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
    "TRENDS": {"TRENDS", "SHOPPING INTENT"},
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


def duckduckgo_image(name: str) -> Optional[str]:
    """DuckDuckGo image search - stands in for Google image search fallback."""
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

    Sources by bucket:
      TALENT     -> IMDB (nm), Wikipedia, DuckDuckGo image
      CONTENT    -> IMDB (tt), Wikipedia
      OTHER      -> Wikipedia, DuckDuckGo image, Google favicon
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
        # For content buckets like ADULT ANIMATION, fall back to image search
        ddg_url = duckduckgo_image(q)
        if ddg_url:
            return ddg_url, "duckduckgo"
        return None, "none"

    # BRAND / PLATFORMS / SPORT / TRENDS / GEN POP / OTHER
    wiki_url = wiki_lookup(q)
    if wiki_url:
        return wiki_url, "wiki"
    ddg_url = duckduckgo_image(q)
    if ddg_url:
        return ddg_url, "duckduckgo"
    fav_url = google_favicon(q)
    if fav_url:
        return fav_url, "google-favicon"
    return None, "none"
