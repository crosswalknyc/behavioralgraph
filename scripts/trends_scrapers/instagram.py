"""
Instagram trending scraper via instagrapi.

Uses instagrapi's Explore + Reels-tray feeds against a logged-in account
to surface what Instagram is pushing on the Explore tab today. Explore
is IG's closest analog to "trending" - it's the algorithmic feed the
Discover/search tab is built on and is what marketing teams watch to
gauge which reels/creators are getting distribution today.

Credentials live in `/root/finished_codes/.env.trends_scrapers` on
Hetzner (mode 600, gitignored). Sourced by the cron entrypoint:

    INSTAGRAM_USERNAME=...
    INSTAGRAM_PASSWORD=...
    INSTAGRAM_SESSIONID=...           # Chrome sessionid cookie (see below)
    INSTAGRAM_SESSION_PATH=/root/.instagrapi_session.json

Auth strategy, in priority order:

  1. **Persisted session file** at INSTAGRAM_SESSION_PATH. Reused across
     every daily cron run - this is the hot path 364 days a year.
  2. **Donated sessionid** from `donate_cookies.py instagram.com`
     (uploaded to `s3://dashboard-inputs/trends_iq_cookies/
     instagram.com.json`). Self-service refresh: when the session
     expires, Jenna just re-donates from Chrome and the scraper picks
     up the new sessionid on the next cron.
  3. **INSTAGRAM_SESSIONID env var** as legacy handoff.
  4. **Password login** as last-resort fallback.

Known limitation (2026-07): Instagram tightened their mobile-API
fingerprint check. A Chrome-issued sessionid gets `login_required`
when instagrapi (which impersonates a Pixel 8 Pro app) uses it against
`i.instagram.com/api/v1/*`, even from a US residential IP - IG can tell
the session originated in a browser context. Practical workarounds if
Instagram signal becomes critical:

  - Use a headed Playwright login flow that mints a session by driving
    the mobile-web app + solving IG's device challenge, then hands the
    session to instagrapi.
  - Run instagrapi through the actual Instagram Android app under
    scrcpy/mitmproxy to capture a mobile-context sessionid.
  - Or accept Instagram as "coming soon" until IG changes their check.

Until one of those lands, the scraper still runs, records the
`login_required` reason in the snapshot's `error` field, and returns 0
items so the dashboard shows a clear placeholder rather than stale
data.

Rate discipline: the daily cron pulls one Explore page (~24 items).
Nothing else. That's ~365 requests/year from a single warm session -
well below the auto-lockout threshold. If IG invalidates the session,
grab a fresh sessionid cookie from Chrome and update the env var.

Standalone:

    source /root/finished_codes/.env.trends_scrapers
    python3 -m scripts.trends_scrapers.instagram
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from ._base import load_donated_cookies, run_scraper

logger = logging.getLogger(__name__)


def _sessionid_from_donation() -> str:
    """Pull `sessionid` out of the donated instagram.com cookies on S3.

    Jenna donates cookies via `donate_cookies.py instagram.com` from her
    laptop, which lands in `s3://dashboard-inputs/trends_iq_cookies/
    instagram.com.json`. This lets us skip the env-var handoff entirely -
    when the session expires she just re-runs donate_cookies from Chrome
    and the scraper picks up the new sessionid on the next cron run.
    """
    donated = load_donated_cookies('instagram.com')
    return (donated.get('sessionid') or '').strip()


def _new_client():
    """Fresh instagrapi Client with pacing settings applied."""
    try:
        from instagrapi import Client  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "instagrapi not installed - run `pip3 install --break-system-packages "
            "instagrapi pillow` on Hetzner") from e
    cl = Client()
    cl.delay_range = [1, 3]  # match instagrapi recommended pacing
    return cl


def _persist(cl, session_path: str) -> None:
    """Write instagrapi settings to disk with mode-600 permissions."""
    try:
        cl.dump_settings(session_path)
        os.chmod(session_path, 0o600)
    except Exception as e:
        logger.warning("instagram: failed to persist session: %s", e)


def _hydrate_from_sessionid(cl, sessionid: str) -> None:
    """Inject a Chrome-issued `sessionid` cookie into instagrapi.

    This lets us skip the mobile-API login endpoint entirely. instagrapi
    exposes `login_by_sessionid()` for exactly this workflow.
    """
    cl.login_by_sessionid(sessionid)


def _validate(cl) -> None:
    """Cheap authenticated ping to confirm the session is live."""
    cl.get_timeline_feed()


def _client():
    """Build an authenticated instagrapi Client.

    Priority order:
      1. Donated sessionid cookie from S3 (Chrome-issued via
         `donate_cookies.py instagram.com`). Preferred because it's
         self-service - Jenna re-donates from her laptop when the
         session expires and Hetzner picks it up on the next cron.
      2. INSTAGRAM_SESSIONID env var. Legacy handoff; still supported.
      3. Persisted settings at INSTAGRAM_SESSION_PATH from a prior run.
         Only tried when the donated sessionid changes (we key the
         persisted file by sessionid so a fresh donation forces a fresh
         auth handshake instead of resurrecting an invalid session).
      4. Password login (rarely works from datacenter IPs; last resort).
    """
    session_path = os.environ.get('INSTAGRAM_SESSION_PATH',
                                    '/root/.instagrapi_session.json')
    donated_sid  = _sessionid_from_donation()
    env_sid      = os.environ.get('INSTAGRAM_SESSIONID', '').strip()
    sessionid    = donated_sid or env_sid
    if donated_sid:
        logger.info("instagram: using donated sessionid from S3 "
                     "(trends_iq_cookies/instagram.com.json)")
    username  = os.environ.get('INSTAGRAM_USERNAME', '').strip()
    password  = os.environ.get('INSTAGRAM_PASSWORD', '').strip()

    # Key the persisted session file to the current sessionid so a fresh
    # donation doesn't reuse a stale settings file.
    if sessionid:
        sid_tag = sessionid.split('%')[0][-6:]  # last 6 chars of the numeric prefix
        session_path = f'{session_path}.{sid_tag}'

    # 1/3. Reuse the persisted session file if it matches the current
    #      sessionid. (session_path is tagged with sessionid above, so
    #      an old persisted file for a different sessionid is invisible.)
    if os.path.exists(session_path):
        try:
            cl = _new_client()
            cl.load_settings(session_path)
            _validate(cl)
            logger.info("instagram: reused persisted session at %s", session_path)
            return cl
        except Exception as e:
            logger.info("instagram: persisted session invalid (%s); "
                         "trying sessionid cookie", type(e).__name__)

    # 2. Hydrate from a browser-issued sessionid cookie.
    if sessionid:
        try:
            cl = _new_client()
            _hydrate_from_sessionid(cl, sessionid)
            _validate(cl)
            _persist(cl, session_path)
            logger.info("instagram: hydrated new session from sessionid cookie")
            return cl
        except Exception as e:
            logger.warning("instagram: sessionid hydrate failed (%s); "
                             "falling back to password login", e)

    # 4. Password login (last resort; frequently blocked from datacenter IPs).
    if not (username and password):
        raise RuntimeError(
            "No usable IG credential. Set INSTAGRAM_SESSIONID (preferred) "
            "or INSTAGRAM_USERNAME+PASSWORD in "
            "/root/finished_codes/.env.trends_scrapers")
    cl = _new_client()
    cl.login(username, password)
    _persist(cl, session_path)
    logger.info("instagram: authenticated via password login")
    return cl


def _serialize_media(m, rank: int) -> dict:
    """Turn an instagrapi Media object into our normalized snapshot row."""
    try:
        caption = (getattr(m, 'caption_text', '') or '').strip()
    except Exception:
        caption = ''
    try:
        user = getattr(m, 'user', None)
        username = getattr(user, 'username', '') if user else ''
        full_name = getattr(user, 'full_name', '') if user else ''
    except Exception:
        username = ''
        full_name = ''

    topic = caption[:120] if caption else (f'@{username}' if username else 'Trending post')

    image = ''
    for attr in ('thumbnail_url', 'display_url'):
        v = getattr(m, attr, None)
        if v:
            image = str(v)
            break
    if not image:
        try:
            resources = getattr(m, 'resources', None) or []
            if resources:
                first = resources[0]
                image = str(getattr(first, 'thumbnail_url', '') or '')
        except Exception:
            pass

    code = getattr(m, 'code', '') or ''
    media_type = getattr(m, 'media_type', None)
    product_type = getattr(m, 'product_type', '') or ''
    # 2 with product_type='clips' means Reels. media_type=1 is a photo,
    # media_type=2 is a video, media_type=8 is a carousel.
    is_reel = (media_type == 2 and product_type == 'clips')
    url = f'https://www.instagram.com/reel/{code}/' if is_reel else \
          f'https://www.instagram.com/p/{code}/'

    return {
        'rank':      rank,
        'topic':     topic,
        'username':  username,
        'full_name': full_name,
        'url':       url,
        'image':     image,
        'likes':     int(getattr(m, 'like_count', 0) or 0),
        'comments':  int(getattr(m, 'comment_count', 0) or 0),
        'views':     int(getattr(m, 'view_count', 0) or 0) if media_type == 2 else None,
        'kind':      'reel' if is_reel else ('video' if media_type == 2 else
                     ('photo' if media_type == 1 else 'carousel' if media_type == 8 else 'post')),
    }


def _pull_explore(cl, limit: int) -> list[dict]:
    """Pull the Explore feed and normalize."""
    medias = []
    try:
        # instagrapi's Explore paginator returns Media objects.
        result = cl.explore_page()
        if isinstance(result, tuple):
            medias = result[0]
        else:
            medias = result or []
    except Exception as e:
        logger.warning("instagram explore fetch failed: %s", e)
        return []
    out: list[dict] = []
    for i, m in enumerate(medias[:limit]):
        try:
            out.append(_serialize_media(m, rank=i + 1))
        except Exception as e:
            logger.debug("instagram media serialize failed: %s", e)
    return out


def fetch() -> dict[str, Any]:
    try:
        cl = _client()
    except Exception as e:
        return {
            'national':  [],
            'available': False,
            'error':     f'{type(e).__name__}: {e}',
        }
    national = _pull_explore(cl, limit=24)
    return {
        'national':  national,
        'available': bool(national),
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('instagram', 'Instagram', 'social', fetch)
    print(f"instagram: national={len(result.get('national', []))} "
           f"error={result.get('error')}", file=sys.stderr)
