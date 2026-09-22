"""Resolve and fetch the hero image for an Attribution IQ campaign.

The Weekly Summary PDF places a small campaign hero tile top-right of
the header, next to the title. That image needs to come from
somewhere the ingest already knows about; asking the client to upload
a poster per campaign is extra friction we don't want.

Resolution order (first hit wins):

  1. ``title.hero_image_url`` on the S3 snapshot. This is the manual
     override we expose later; if the client sends the actual poster
     URL through the ingest agent, it lives here.

  2. First official-trailer YouTube asset, transformed to
     ``https://i.ytimg.com/vi/<ID>/hqdefault.jpg``. YouTube always
     returns hqdefault for a real video (unlike maxresdefault, which
     404s for some uploads). Films will nearly always trip this
     because the trailer is asset #1.

  3. First asset carrying a non-empty ``thumbnail_s3_url`` (some
     ingest paths pre-cache one).

  4. First asset carrying an ``og_metadata.image`` (OG-scrape path).

  5. ``None``. The PDF renderer skips the tile and takes back the
     header space.

Bytes are downloaded with a hard 5-second timeout and a 2 MB size
cap, into memory. Any failure returns ``None`` and the PDF still
renders (fail-safe by design).

Never trust bytes from the client. Every image origin walks through
this resolver server-side, so the PDF endpoint can't be spoofed into
embedding a random attacker-controlled image inside a Crosswalk-
branded document.
"""
from __future__ import annotations

import logging
import re
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Hard caps
_TIMEOUT_S = 5.0
_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
_ALLOWED_HOSTS = {
    # YouTube-derived thumbnails (auto)
    "i.ytimg.com",
    "img.youtube.com",
    # S3 buckets we already read from
    "dashboard-inputs.s3.amazonaws.com",
    "dashboard-inputs.s3.us-east-2.amazonaws.com",
    "dashboard-inputs.s3.us-east-1.amazonaws.com",
    # Common CDN hosts where OG scrape and manual URLs will live
    "image.tmdb.org",
    "www.themoviedb.org",
    "media-amazon.com",
    "m.media-amazon.com",
    "img.icons8.com",
    "logo.clearbit.com",
    # Instagram / TikTok / X OG thumbs (paths under their CDNs)
    "scontent.cdninstagram.com",
    "p16-sign-va.tiktokcdn.com",
    "p19-sign-va.tiktokcdn.com",
    "abs.twimg.com",
    "pbs.twimg.com",
}

# YouTube video-id pattern; matches watch/shorts/embed/youtu.be surfaces.
_YT_ID_RE = re.compile(
    r"(?:v=|/shorts/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})"
)


def _extract_youtube_id(url: str) -> Optional[str]:
    """Return the 11-char video id from any YouTube URL, or ``None``."""
    if not url or "youtu" not in url:
        return None
    m = _YT_ID_RE.search(url)
    return m.group(1) if m else None


def resolve_hero_image_url(snapshot: Optional[dict]) -> Optional[str]:
    """Return the best hero-image URL for a campaign, or ``None``.

    ``snapshot`` is the normalized dict returned by
    ``intent_iq._load_normalized_snapshot(title_slug)``. Robust to
    missing / partial snapshots; every branch short-circuits on
    None or empty.
    """
    if not snapshot:
        return None

    title = snapshot.get("title") or {}

    # (1) Manual override on the title
    manual = (title.get("hero_image_url") or "").strip()
    if manual:
        return manual

    assets = snapshot.get("assets") or []

    # (2) First YouTube asset -> hqdefault thumbnail. Prefer an asset
    #     labeled Trailer / Trailer #2 / Teaser, else any YouTube.
    def _asset_priority(a: dict) -> int:
        kind = (a.get("asset_type") or "").lower()
        if "trailer" in kind:
            return 0
        if "teaser" in kind:
            return 1
        return 2

    yt_assets = sorted(
        [a for a in assets if "youtu" in (a.get("url") or "").lower()],
        key=_asset_priority,
    )
    for a in yt_assets:
        vid = _extract_youtube_id(a.get("url") or "")
        if vid:
            return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

    # (3) Pre-cached thumbnail on any asset
    for a in assets:
        thumb = (a.get("thumbnail_s3_url") or "").strip()
        if thumb:
            return thumb

    # (4) OG-scrape image on any asset
    for a in assets:
        og = a.get("og_metadata") or {}
        if isinstance(og, dict):
            img = (og.get("image") or "").strip()
            if img:
                return img

    return None


def _host_allowed(url: str) -> bool:
    """Only fetch from a small allow-list of hosts we already trust or
    can reason about. Prevents SSRF-shaped abuse if a snapshot ever
    carries an attacker-controlled URL."""
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = (u.hostname or "").lower()
        if not host:
            return False
        if host in _ALLOWED_HOSTS:
            return True
        # Wildcard: any *.cdninstagram.com, *.tiktokcdn.com,
        # *.tiktokcdn-us.com, *.twimg.com, *.ggpht.com, *.googleusercontent.com
        for suffix in (".cdninstagram.com", ".tiktokcdn.com",
                       ".tiktokcdn-us.com", ".twimg.com",
                       ".ggpht.com", ".googleusercontent.com",
                       ".ytimg.com", ".amazonaws.com"):
            if host.endswith(suffix):
                return True
        return False
    except Exception:
        return False


def fetch_hero_image_bytes(url: Optional[str]) -> Optional[bytes]:
    """Download the image bytes from ``url``, subject to timeout,
    size cap, and host allow-list. Returns ``None`` on any failure.

    The PDF renderer handles ``None`` by dropping the hero tile.
    """
    if not url:
        return None
    if not (url.startswith("http://") or url.startswith("https://")):
        return None
    if not _host_allowed(url):
        logger.info("hero image host not allow-listed: %s", url)
        return None
    try:
        req = urllib.request.Request(
            url,
            headers={
                # Some CDNs 403 on missing UA. Present as a real browser.
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept": "image/*,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            content_length = resp.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > _MAX_BYTES:
                        logger.info("hero image too large per Content-Length: %s", url)
                        return None
                except ValueError:
                    pass
            # Read in a bounded loop so a lying Content-Length can't
            # blow past the cap.
            buf = bytearray()
            chunk = resp.read(65536)
            while chunk:
                buf.extend(chunk)
                if len(buf) > _MAX_BYTES:
                    logger.info("hero image exceeded 2MB during read: %s", url)
                    return None
                chunk = resp.read(65536)
            data = bytes(buf)
            # Sanity: must be one of the common image magic numbers.
            # PNG 89 50 4E 47, JPEG FF D8 FF, GIF 47 49 46, WEBP 52 49 46 46 ... 57 45 42 50
            if not data:
                return None
            head = data[:4]
            if not (
                head.startswith(b"\x89PNG")
                or head.startswith(b"\xff\xd8\xff")
                or head.startswith(b"GIF8")
                or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
            ):
                logger.info("hero image is not PNG/JPEG/GIF/WEBP: %s", url)
                return None
            return data
    except Exception as e:
        logger.info("hero image fetch failed for %s: %s", url, e)
        return None


def resolve_and_fetch(snapshot: Optional[dict]) -> tuple[Optional[bytes], Optional[str]]:
    """Combined helper. Returns ``(bytes_or_None, resolved_url_or_None)``.

    The resolved URL is included in the tuple so the PDF endpoint can
    log which source it used, and future auditing has a paper trail.
    """
    url = resolve_hero_image_url(snapshot)
    if not url:
        return (None, None)
    return (fetch_hero_image_bytes(url), url)
