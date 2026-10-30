"""A pasted watch link for Pilot Testing.

Any streamer or digital link: Paramount+, Netflix, Hulu, Max, Disney+,
Prime Video, Peacock, Apple TV, YouTube, a file URL, and the rest.
The room watches that title on the clock (dial, comments, questions,
scorecard). Embeddable players (YouTube, Vimeo, a direct file) play in
the stage. Login-walled services open on their own site so the person
in the room can watch with the twins. Never downloads the file.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

# page = open on the service (login wall). embed = in-stage player.
# video = a direct file the stage <video> can play.
KIND_PAGE = 'page'
KIND_EMBED = 'embed'
KIND_VIDEO = 'video'

# host (no www) -> (platform name, short button label)
_HOSTS = {
    'netflix.com': ('Netflix', 'Netflix'),
    'hulu.com': ('Hulu', 'Hulu'),
    'peacocktv.com': ('Peacock', 'Peacock'),
    'max.com': ('Max', 'Max'),
    'hbomax.com': ('Max', 'Max'),
    'play.hbomax.com': ('Max', 'Max'),
    'paramountplus.com': ('Paramount Plus', 'Paramount+'),
    'disneyplus.com': ('Disney Plus', 'Disney+'),
    'amazon.com': ('Amazon', 'Prime Video'),
    'primevideo.com': ('Amazon', 'Prime Video'),
    'tv.apple.com': ('Apple TV', 'Apple TV'),
    'appletv.com': ('Apple TV', 'Apple TV'),
    'youtube.com': ('YouTube', 'YouTube'),
    'youtu.be': ('YouTube', 'YouTube'),
    'm.youtube.com': ('YouTube', 'YouTube'),
    'music.youtube.com': ('YouTube', 'YouTube'),
    'tv.youtube.com': ('YouTube TV', 'YouTube TV'),
    'nbc.com': ('NBC', 'NBC'),
    'starz.com': ('Starz', 'Starz'),
    'tubitv.com': ('Tubi', 'Tubi'),
    'tubi.tv': ('Tubi', 'Tubi'),
    'pluto.tv': ('Pluto', 'Pluto'),
    'plutotv.com': ('Pluto', 'Pluto'),
    'mgmplus.com': ('MGM Plus', 'MGM+'),
    'amcplus.com': ('AMC+', 'AMC+'),
    'amc.com': ('AMC+', 'AMC+'),
    'britbox.com': ('BritBox', 'BritBox'),
    'espn.com': ('ESPN+', 'ESPN+'),
    'plus.espn.com': ('ESPN+', 'ESPN+'),
    'crunchyroll.com': ('Crunchyroll', 'Crunchyroll'),
    'discoveryplus.com': ('Discovery+', 'Discovery+'),
    'paramount.com': ('Paramount', 'Paramount'),
    'cbs.com': ('Paramount Plus', 'Paramount+'),
    'fubo.tv': ('Fubo', 'Fubo'),
    'sling.com': ('Sling', 'Sling'),
    'therokuchannel.roku.com': ('The Roku Channel', 'Roku'),
    'roku.com': ('The Roku Channel', 'Roku'),
    'watch.plex.tv': ('Plex', 'Plex'),
    'plex.tv': ('Plex', 'Plex'),
    'freevee.com': ('Freevee', 'Freevee'),
    'vimeo.com': ('Vimeo', 'Vimeo'),
    'player.vimeo.com': ('Vimeo', 'Vimeo'),
    'dailymotion.com': ('Dailymotion', 'Dailymotion'),
    'dai.ly': ('Dailymotion', 'Dailymotion'),
    'twitch.tv': ('Twitch', 'Twitch'),
    'tiktok.com': ('TikTok', 'TikTok'),
    'instagram.com': ('Instagram', 'Instagram'),
    'facebook.com': ('Facebook', 'Facebook'),
    'fb.watch': ('Facebook', 'Facebook'),
    'x.com': ('X', 'X'),
    'twitter.com': ('X', 'X'),
    'open.spotify.com': ('Spotify', 'Spotify'),
    'spotify.com': ('Spotify', 'Spotify'),
    'music.apple.com': ('Apple Music', 'Apple Music'),
    'soundcloud.com': ('SoundCloud', 'SoundCloud'),
    'dropbox.com': ('Dropbox', 'Dropbox'),
    'drive.google.com': ('Google Drive', 'Google Drive'),
}

_LOGIN_WALL = frozenset({
    'Netflix', 'Hulu', 'Peacock', 'Max', 'Paramount Plus', 'Disney Plus',
    'Amazon', 'Apple TV', 'NBC', 'Starz', 'MGM Plus', 'AMC+', 'BritBox',
    'ESPN+', 'Discovery+', 'YouTube TV', 'Fubo', 'Sling',
})

_VIDEO_EXT = frozenset({'.mp4', '.mov', '.webm', '.m4v', '.m3u8'})
_SKIP_SEGS = frozenset({
    'shows', 'show', 'video', 'videos', 'watch', 'title', 'titles', 'play',
    'movies', 'movie', 'series', 'browse', 'gp', 'detail', 'us', 'en',
    'watch', 'playback', 'vod', 'season', 'episode', 'episodes', 'film',
    'films', 'tv', 'home', 'video', 'embed', 'player', 'channel', 'c',
})
_YT_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
_VIMEO_ID = re.compile(r'^\d{6,12}$')
_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
_TOKEN = re.compile(r'^[A-Za-z0-9_-]{16,}$')
_OG_TITLE = re.compile(r'property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_OG_TITLE_REV = re.compile(r'content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', re.I)
_META_TITLE = re.compile(r'<title[^>]*>([^<]{2,200})</title>', re.I)
_DURATION = re.compile(r'(?:duration|runtime|length)["\']?\s*[:=]\s*["\']?(\d{2,7})', re.I)

FetchFn = Callable[[str], Optional[tuple]]  # url -> (code, body, final_url) or None


def _norm_host(host: str) -> str:
    h = (host or '').strip().lower()
    if h.startswith('www.'):
        h = h[4:]
    return h


def _host_match(host: str) -> Optional[tuple]:
    h = _norm_host(host)
    if h in _HOSTS:
        return _HOSTS[h]
    parts = h.split('.')
    for i in range(len(parts) - 1):
        key = '.'.join(parts[i:])
        if key in _HOSTS:
            return _HOSTS[key]
    return None


def parse_url(raw: str) -> dict:
    """Split a pasted string into host, path, query, and a cleaned https URL."""
    s = ' '.join(str(raw or '').split())
    if not s:
        return {}
    if '://' not in s:
        s = 'https://' + s.lstrip('/')
    try:
        p = urllib.parse.urlparse(s)
    except Exception:
        return {}
    if p.scheme not in ('http', 'https'):
        return {}
    host = _norm_host(p.hostname or '')
    if not host or '.' not in host:
        return {}
    segs = [urllib.parse.unquote(x) for x in p.path.split('/') if x]
    q = urllib.parse.parse_qs(p.query or '')
    url = urllib.parse.urlunparse((p.scheme, (p.hostname or host), p.path or '/', '', p.query, ''))
    if not url.endswith('/') and not os.path.splitext(p.path or '')[1] and not p.query:
        url = url.rstrip('/') + '/'
    return {'url': url, 'host': host, 'segs': segs, 'query': q, 'path': p.path or '/'}


def _looks_id(seg: str) -> bool:
    s = str(seg or '')
    if not s:
        return True
    if _UUID.match(s) or _YT_ID.match(s) or _VIMEO_ID.match(s) or _TOKEN.match(s):
        return True
    if s.lower().startswith(('umc.cmc.', 'amzn1.', 'av:', 'entity-')):
        return True
    if s.isdigit() and len(s) >= 5:
        return True
    return False


def _pretty_slug(seg: str) -> str:
    s = urllib.parse.unquote(str(seg or ''))
    s = s.replace('+', ' ').replace('_', ' ').replace('-', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    if not s:
        return ''
    small = {'and', 'or', 'the', 'of', 'a', 'an', 'to', 'in'}
    words = []
    for i, w in enumerate(s.split(' ')):
        lw = w.lower()
        if lw in small and i > 0:
            words.append(lw)
        else:
            words.append(w[:1].upper() + w[1:])
    return ' '.join(words)


def _title_from_path(host: str, segs: list) -> str:
    for seg in reversed(segs):
        low = str(seg or '').lower()
        if low in _SKIP_SEGS or _looks_id(seg):
            continue
        if os.path.splitext(low)[1] in _VIDEO_EXT:
            return _pretty_slug(os.path.splitext(seg)[0])
        name = _pretty_slug(seg)
        if name and len(name) >= 2:
            return name
    return ''


def _youtube_id(parsed: dict) -> str:
    host = parsed.get('host') or ''
    segs = parsed.get('segs') or []
    q = parsed.get('query') or {}
    if host in ('youtu.be',):
        return segs[0] if segs and _YT_ID.match(segs[0]) else ''
    for key in ('v', 'vi'):
        vals = q.get(key) or []
        if vals and _YT_ID.match(vals[0]):
            return vals[0]
    for i, seg in enumerate(segs):
        if seg in ('embed', 'shorts', 'live', 'v') and i + 1 < len(segs) and _YT_ID.match(segs[i + 1]):
            return segs[i + 1]
    return ''


def _vimeo_id(parsed: dict) -> str:
    segs = parsed.get('segs') or []
    for seg in reversed(segs):
        if _VIMEO_ID.match(seg):
            return seg
    return ''


def _content_id(parsed: dict, platform: str) -> str:
    segs = parsed.get('segs') or []
    host = parsed.get('host') or ''
    if platform == 'YouTube':
        return _youtube_id(parsed)
    if platform == 'Vimeo':
        return _vimeo_id(parsed)
    if platform == 'Paramount Plus':
        for i, seg in enumerate(segs):
            if seg == 'video' and i + 1 < len(segs) and _TOKEN.match(segs[i + 1]):
                return segs[i + 1]
        if segs and _TOKEN.match(segs[-1]) and len(segs[-1]) >= 24:
            return segs[-1]
        return ''
    if platform == 'Netflix':
        for i, seg in enumerate(segs):
            if seg in ('watch', 'title') and i + 1 < len(segs) and re.match(r'^\d{5,12}$', segs[i + 1]):
                return segs[i + 1]
        return ''
    if platform in ('Hulu', 'Peacock', 'Max', 'Disney Plus'):
        for seg in reversed(segs):
            m = re.match(r'^(?:entity-)?([0-9a-f-]{36})$', seg, re.I)
            if m:
                return m.group(1).lower()
        return ''
    if platform == 'Amazon':
        for i, seg in enumerate(segs):
            if seg == 'detail' and i + 1 < len(segs) and _TOKEN.match(segs[i + 1]):
                return segs[i + 1]
        for seg in segs:
            if str(seg).lower().startswith('amzn1.dv.gti.'):
                return seg
        return ''
    if platform == 'Apple TV':
        for seg in reversed(segs):
            m = re.match(r'^umc\.cmc\.([0-9a-z]{10,})$', seg, re.I)
            if m:
                return m.group(1)
        return ''
    if host.endswith('youtu.be') or 'youtube' in host:
        return _youtube_id(parsed)
    return ''


def _is_short(parsed: dict) -> bool:
    segs = [s.lower() for s in (parsed.get('segs') or [])]
    return 'shorts' in segs or (parsed.get('host') or '').endswith('tiktok.com')


def _is_movie_path(parsed: dict) -> bool:
    segs = [s.lower() for s in (parsed.get('segs') or [])]
    return 'movies' in segs or 'movie' in segs or 'film' in segs


def _direct_video(parsed: dict) -> bool:
    path = (parsed.get('path') or '').split('?')[0].lower()
    ext = os.path.splitext(path)[1]
    return ext in _VIDEO_EXT


def default_runtime_s(kind: str, *, short: bool = False, movie: bool = False, mode: str = '') -> float:
    """A clock the room can run when the page does not name a runtime.
    Messy on purpose so it does not read as a placeholder."""
    m = str(mode or '').lower()
    if m in ('trailer', 'spot'):
        return 127.0
    if short or m in ('title', 'key_art', 'concept'):
        return 48.0
    if kind == KIND_EMBED and not movie:
        return 663.0
    if movie or m == 'feature':
        return 6127.0
    return 2543.0


def embed_url(parsed: dict, platform: str, content_id: str) -> str:
    if _direct_video(parsed):
        return parsed.get('url') or ''
    if platform == 'YouTube' and content_id:
        return f'https://www.youtube.com/embed/{content_id}?rel=0&modestbranding=1'
    if platform == 'Vimeo' and content_id:
        return f'https://player.vimeo.com/video/{content_id}'
    if platform == 'Dailymotion' and content_id:
        return f'https://www.dailymotion.com/embed/video/{content_id}'
    if platform == 'Twitch':
        segs = parsed.get('segs') or []
        if segs and segs[0] == 'videos' and len(segs) > 1:
            return f'https://player.twitch.tv/?video={segs[1]}&parent=localhost'
        return ''
    return ''


def _kind(parsed: dict, platform: str, embed: str) -> str:
    if _direct_video(parsed):
        return KIND_VIDEO
    if embed and platform not in _LOGIN_WALL:
        return KIND_EMBED
    return KIND_PAGE


def _clean_title(raw: str) -> str:
    s = ' '.join(str(raw or '').split())
    s = s.replace('\u2014', '-').replace('\u2013', '-')
    s = re.sub(r'\s*[-|:]\s*(?:watch\s+on|streaming\s+on|on)\s+.+$', '', s, flags=re.I)
    s = re.sub(r'^\s*watch\s+', '', s, flags=re.I)
    s = re.sub(r'\s+[-|:]\s+(?:Netflix|Hulu|Max|Paramount\+?|Disney\+?|Prime Video|Peacock|YouTube|Hulu|Apple TV).*$', '', s, flags=re.I)
    return s.strip()[:160]


def _title_from_html(html: str) -> str:
    if not html:
        return ''
    m = _OG_TITLE.search(html) or _OG_TITLE_REV.search(html)
    if m:
        return _clean_title(m.group(1))
    m = _META_TITLE.search(html)
    if m:
        return _clean_title(m.group(1))
    return ''


def _runtime_from_html(html: str) -> float:
    if not html:
        return 0.0
    m = _DURATION.search(html)
    if not m:
        return 0.0
    try:
        n = float(m.group(1))
    except (TypeError, ValueError):
        return 0.0
    if n > 20_000:          # milliseconds
        n = n / 1000.0
    if 30 <= n <= 15_000:
        return n
    return 0.0


def _may_fetch() -> bool:
    if os.environ.get('REGRESSION_TEST_MODE') == '1':
        return False
    if os.environ.get('DTW_WATCH_URL_FETCH') == '0':
        return False
    return True


def _default_fetch(url: str) -> Optional[tuple]:
    if not _may_fetch():
        return None
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read(400_000)
            final = getattr(resp, 'url', url) or url
            code = getattr(resp, 'status', 200) or 200
            text = body.decode('utf-8', errors='replace')
            return code, text, final
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def _youtube_oembed(url: str, fetch: FetchFn) -> str:
    q = urllib.parse.urlencode({'url': url, 'format': 'json'})
    got = fetch(f'https://www.youtube.com/oembed?{q}')
    if not got:
        return ''
    _, body, _ = got
    try:
        data = json.loads(body)
    except Exception:
        return ''
    return _clean_title(str(data.get('title') or ''))


def resolve(raw: str, *, hint_title: str = '', runtime_s: float = 0,
            mode: str = '', fetch: Optional[FetchFn] = None) -> dict:
    """Turn a pasted link into the media the room watches.

    Always returns a record when the string is an http(s) URL. Network
    is optional (og:title, YouTube oembed) and off in tests.
    """
    parsed = parse_url(raw)
    if not parsed:
        return {}
    hit = _host_match(parsed['host'])
    if hit:
        platform, short = hit
    else:
        parts = [p for p in (parsed['host'] or '').split('.') if p]
        name = _pretty_slug(parts[-2] if len(parts) >= 2 else (parts[0] if parts else '')) or 'the web'
        platform, short = name, name
    content_id = _content_id(parsed, platform)
    embed = embed_url(parsed, platform, content_id)
    kind = _kind(parsed, platform, embed)
    short_form = _is_short(parsed)
    movie = _is_movie_path(parsed)
    title = _clean_title(hint_title) or _title_from_path(parsed['host'], parsed['segs'])
    try:
        runtime = float(runtime_s or 0)
    except (TypeError, ValueError):
        runtime = 0.0
    fetcher = fetch if fetch is not None else _default_fetch
    if fetcher and (not title or not runtime):
        if platform == 'YouTube' and parsed.get('url'):
            yt = _youtube_oembed(parsed['url'], fetcher)
            if yt:
                title = title or yt
        page = fetcher(parsed['url'])
        if page:
            _, html, _final = page
            title = title or _title_from_html(html)
            if not runtime:
                runtime = _runtime_from_html(html)
    if not runtime:
        runtime = default_runtime_s(kind, short=short_form, movie=movie, mode=mode)
    open_label = f'Watch on {short}' if short else 'Open the episode'
    return {
        'url': parsed['url'],
        'host': parsed['host'],
        'platform': platform,
        'platform_label': short,
        'content_id': content_id,
        'embed_url': embed,
        'kind': kind,
        'title': title,
        'show': title if not movie else '',
        'season': '',
        'episode': '',
        'runtime_s': float(runtime),
        'open_label': open_label,
        'source': 'watch_url',
    }


def attach_media(media: dict, rec: dict, *, fallback_title: str = '') -> dict:
    """Stamp a media dict with the resolved link. Leaves an uploaded
    file in place if one is already on the test."""
    out = dict(media or {})
    if not rec:
        return out
    if out.get('s3_key'):
        out['watch_url'] = rec.get('url') or ''
        out['platform'] = rec.get('platform') or out.get('platform') or ''
        return out
    title = rec.get('title') or out.get('title') or fallback_title or ''
    out.update({
        'source': 'watch_url',
        'url': rec.get('url') or '',
        'platform': rec.get('platform') or '',
        'platform_label': rec.get('platform_label') or '',
        'content_id': rec.get('content_id') or '',
        'embed_url': rec.get('embed_url') or '',
        'kind': rec.get('kind') or KIND_PAGE,
        'title': str(title)[:160],
        'show': rec.get('show') or '',
        'season': rec.get('season') or '',
        'episode': rec.get('episode') or '',
        'runtime_s': float(rec.get('runtime_s') or out.get('runtime_s') or 0),
        'open_label': rec.get('open_label') or 'Open the episode',
    })
    return out
