"""A digital twin built from a list of content URLs (Jenna 2026-11-04:
"whats the update on getting the twins built off of an upload like a
list of content urls?").

The list DEFINES the audience: the twin's people are the US people who
watched, read or visited that content in the window. Two doors, one
build:

  Twins tab   'Build a twin from a list' beside 'Build a twin': a CSV /
              TXT / XLSX of URLs (or pasted URLs), a plain audience
              name, the window, the parsed list read back with the
              price, confirm.
  Prometheus  a sheet or text file with URL rows on the thread (or URLs
              pasted in the message) plus twin words: 'build a twin from
              these URLs', 'make a digital twin of the people who watched
              these'. The name and window are collected the way every
              other intake collects, one confirm card with the price.

On confirm the audience is pulled through the profile engine as a
file-fed universe (the spec carries the engine's own marker,
``universe_seed_csv`` / ``seed_file``, so BRAND INPUT reads the literal
``CSV``; the URL rows sit under system/universe_seeds/<run_id>.csv; the
watch and listen URLs ride ``content_seed_urls`` the way a viewers
profile's seeds do), and when that profile registers the twin is built
from it (``digital_twin_synthesis.build_twin`` on the new profile_key,
with the outside record researched). The twin lands on the requester's
Twins tab; the finished note lands in their chat thread and their email
through the house paths.

Price: the Profile IQ build plus the twin with its first two runs, both
at the live rates, charged once at confirm. A genuine upstream build
failure refunds both with the partner-safe line; nothing is ever held
and nothing ever says rebuild.

Everything here is fail-safe: a parse that finds no URL says so plainly,
a title lookup that cannot run leaves the host, a model read that fails
falls back to a US-shaped draft the engine's own passes then reshape.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import time
import traceback
from datetime import date, timedelta
from urllib.parse import urlsplit, urlunsplit

FLOW = 'digital_twin_list'
RUN_LABEL = 'Build the twin'
LIST_PREFIX = 'system/digital_twins/url_lists/'
SEED_PREFIX = 'system/universe_seeds/'
MAX_URLS = 5000
LIST_TTL_S = 7 * 24 * 3600
BUCKET = 'dashboard-inputs'
PROFILE_PRICE_KEY, PROFILE_PRICE_DEFAULT = 'api_chatbot_profile_iq_build', 300.0
TWIN_PRICE_KEY, TWIN_PRICE_DEFAULT = 'digital_twins_iq', 750.0
RUN_PRICE_KEY, RUN_PRICE_DEFAULT = 'digital_twin_run', 100.0
PROFILE_CREDITS = 5          # _V1_CREDITS['new_build']; the wallet prices the pull type live
TWIN_CREDITS = 15            # digital_twins_api.CREDITS['digital_twins_iq']
PROFILE_PULL_TYPE = 'Chatbot Profile IQ (new_build)'

ASK_NAME = ("What should I call this audience? A plain name is best, like 'Yellowstone and Landman viewers' "
            "or 'Our fall campaign readers'.")
ASK_LIST = ("Attach the list (a CSV, TXT or XLSX with one URL per row) or paste the URLs here, and tell me "
            "what to call the audience. I read the list back with the price before anything runs.")
FAIL_LINE = 'This build could not be completed. Credits for this run have been refunded.'

_URL_RX = re.compile(r"https?://[^\s<>\"'`]+", re.I)
_BARE_RX = re.compile(r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:[/?#][^\s]*)?$", re.I)
_URL_HEADER_RX = re.compile(r"^(?:url|urls|link|links|href|hrefs|permalink|page|page url|content url|watch url|video url|asset url|address|web address)$", re.I)
_TRAIL = '.,;:)]}>\'"'
_PRIVATE_HOSTS = ('localhost', '127.', '0.0.0.0', '10.', '192.168.', '169.254.')

# host -> (label the reader sees, kind, content-map spelling for the seed platform list)
_HOSTS = {
    'netflix.com': ('Netflix', 'watch', 'Netflix'),
    'hulu.com': ('Hulu', 'watch', 'Hulu'),
    'peacocktv.com': ('Peacock', 'watch', 'Peacock'),
    'max.com': ('Max', 'watch', 'HBO MAX'),
    'hbomax.com': ('Max', 'watch', 'HBO MAX'),
    'paramountplus.com': ('Paramount+', 'watch', 'Paramount Plus'),
    'disneyplus.com': ('Disney+', 'watch', 'Disney Plus'),
    'primevideo.com': ('Prime Video', 'watch', 'Amazon'),
    'tv.apple.com': ('Apple TV+', 'watch', 'Apple TV'),
    'appletv.com': ('Apple TV+', 'watch', 'Apple TV'),
    'youtube.com': ('YouTube', 'watch', 'YouTube'),
    'youtu.be': ('YouTube', 'watch', 'YouTube'),
    'tubitv.com': ('Tubi', 'watch', 'Tubi'),
    'tubi.tv': ('Tubi', 'watch', 'Tubi'),
    'pluto.tv': ('Pluto TV', 'watch', 'Pluto'),
    'plutotv.com': ('Pluto TV', 'watch', 'Pluto'),
    'therokuchannel.roku.com': ('The Roku Channel', 'watch', 'Roku'),
    'starz.com': ('Starz', 'watch', 'Starz'),
    'mgmplus.com': ('MGM+', 'watch', 'MGM Plus'),
    'amcplus.com': ('AMC+', 'watch', 'AMC Plus'),
    'nbc.com': ('NBC', 'watch', 'NBC'),
    'twitch.tv': ('Twitch', 'watch', 'Twitch'),
    'tiktok.com': ('TikTok', 'watch', 'TikTok'),
    'instagram.com': ('Instagram', 'watch', 'Instagram'),
    'spotify.com': ('Spotify', 'listen', 'Spotify'),
    'podcasts.apple.com': ('Apple Podcasts', 'listen', 'Apple Podcasts'),
    'music.apple.com': ('Apple Music', 'listen', 'Apple Music'),
    'audible.com': ('Audible', 'listen', 'Audible'),
    'siriusxm.com': ('SiriusXM', 'listen', 'SiriusXM'),
    'pandora.com': ('Pandora', 'listen', 'Pandora'),
    'iheart.com': ('iHeart', 'listen', 'iHeart'),
}
_AMAZON_VIDEO_RX = re.compile(r"/gp/video/|/video/detail/|amzn1\.dv\.gti|/dp/B0[A-Z0-9]{8}.*(?:video|prime)", re.I)


# ---------------------------------------------------------------- URLs
def _clean_url(raw):
    """A normalized http(s) URL or '' when the cell is not one."""
    s = str(raw or '').strip().strip(_TRAIL).strip()
    if not s:
        return ''
    if not re.match(r"^https?://", s, re.I):
        if _BARE_RX.match(s):
            s = 'https://' + s
        else:
            return ''
    try:
        p = urlsplit(s)
    except Exception:
        return ''
    host = (p.hostname or '').lower()
    if not host or '.' not in host or any(host.startswith(x) or host == x.rstrip('.') for x in _PRIVATE_HOSTS):
        return ''
    if p.scheme.lower() not in ('http', 'https'):
        return ''
    netloc = host + (f":{p.port}" if p.port and p.port not in (80, 443) else '')
    path = p.path or '/'
    return urlunsplit((p.scheme.lower(), netloc, path, p.query, ''))


def _dedupe(urls):
    seen, out = set(), []
    for u in urls:
        k = u.lower().rstrip('/')
        if k in seen:
            continue
        seen.add(k)
        out.append(u)
        if len(out) >= MAX_URLS:
            break
    return out


def urls_from_text(text):
    """(urls, dropped): every URL in free text or a pasted list. A line
    that is one bare address (www.site.com/path) counts; a line that is
    words does not; a line that fails to parse is a dropped row."""
    text = str(text or '')
    found = [m.group(0) for m in _URL_RX.finditer(text)]
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text) if ln.strip()]
    bare = [ln for ln in lines if not _URL_RX.search(ln) and _BARE_RX.match(ln.split(',')[0].strip())]
    cleaned = [c for c in (_clean_url(u) for u in found + [b.split(',')[0] for b in bare]) if c]
    dropped = max(0, len(found) + len(bare) - len(cleaned))
    return _dedupe(cleaned), dropped


def _pick_url_column(headers, rows):
    """The column that carries the links: named for it, else the first
    whose values are mostly URLs (header row optional)."""
    for i, h in enumerate(headers or []):
        if _URL_HEADER_RX.match(str(h or '').strip()):
            return i
    width = max([len(headers or [])] + [len(r) for r in rows[:200]] or [0])
    for i in range(width):
        vals = [str(r[i]).strip() for r in rows[:200] if i < len(r) and str(r[i]).strip()]
        if vals and sum(1 for v in vals if _clean_url(v)) >= max(1, (len(vals) + 1) // 2):
            return i
    return -1


def urls_from_rows(rows):
    """(urls, dropped) from a table (list of row lists). The first row is
    a header when it carries no URL; a sheet of bare URLs has none."""
    rows = [list(r) for r in (rows or []) if any(str(c or '').strip() for c in r)]
    if not rows:
        return [], 0
    first = rows[0]
    header_row = not any(_clean_url(c) for c in first)
    headers = [str(c or '') for c in first] if header_row else []
    data = rows[1:] if header_row else rows
    col = _pick_url_column(headers, data)
    if col < 0:
        return [], len(data)
    cleaned, dropped = [], 0
    for r in data:
        cell = r[col] if col < len(r) else ''
        u = _clean_url(cell)
        if u:
            cleaned.append(u)
        else:
            dropped += 1
    return _dedupe(cleaned), dropped


def urls_from_bytes(name, data):
    """(urls, dropped) from an uploaded CSV / TSV / TXT / MD / XLSX file.
    Only the link column is read; nothing else in the file is kept."""
    ext = os.path.splitext(str(name or '').lower())[1]
    data = data or b''
    if ext in ('.xlsx', '.xlsm'):
        try:
            import openpyxl  # type: ignore
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            rows = []
            for ws in wb.worksheets:
                for r in ws.iter_rows(values_only=True):
                    rows.append(['' if c is None else str(c) for c in r])
                if rows:
                    break
            return urls_from_rows(rows)
        except Exception:
            traceback.print_exc()
            return [], 0
    text = data.decode('utf-8-sig', errors='replace') if isinstance(data, (bytes, bytearray)) else str(data)
    if ext in ('.csv', '.tsv'):
        delim = '\t' if ext == '.tsv' else ','
        try:
            sample = text[:4096]
            if ext == '.csv' and sample.count(';') > sample.count(',') and sample.count(';') > 0:
                delim = ';'
            rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        except Exception:
            rows = [[ln] for ln in text.splitlines()]
        urls, dropped = urls_from_rows(rows)
        if urls:
            return urls, dropped
    return urls_from_text(text)


# ------------------------------------------------------------ the list
def _host_of(url):
    try:
        return re.sub(r"^www\.", '', (urlsplit(url).hostname or '').lower())
    except Exception:
        return ''


def _platform_for(host, url):
    """(label, kind, seed_platform) for a host; an article or a site when
    the host is not a watch or listen platform."""
    h = host
    while h:
        hit = _HOSTS.get(h)
        if hit:
            if h in ('youtube.com', 'youtu.be') and not re.search(r"/watch|youtu\.be/|/shorts/|/live/|/@|/channel/|/c/|/playlist", url):
                return 'YouTube', 'watch', 'YouTube'
            return hit
        h = h.partition('.')[2] if '.' in h else ''
    if host.endswith('amazon.com'):
        if _AMAZON_VIDEO_RX.search(url):
            return 'Prime Video', 'watch', 'Amazon'
        return 'Amazon', 'site', ''
    return '', 'article', ''


def classify(urls):
    """Items with host, platform label, kind (watch, listen, article,
    site) and the content map's spelling of the platform."""
    items = []
    for u in urls:
        host = _host_of(u)
        label, kind, seed = _platform_for(host, u)
        items.append({'url': u, 'host': host, 'platform': label, 'kind': kind, 'seed_platform': seed})
    return items


def summarize(items):
    hosts, plats, kinds = {}, {}, {'watch': 0, 'listen': 0, 'article': 0, 'site': 0}
    for it in items:
        hosts[it['host']] = hosts.get(it['host'], 0) + 1
        k = it.get('kind') or 'article'
        kinds[k] = kinds.get(k, 0) + 1
        if it.get('platform'):
            plats[it['platform']] = plats.get(it['platform'], 0) + 1
    return {'count': len(items), 'hosts': sorted(hosts.items(), key=lambda kv: (-kv[1], kv[0])),
            'platforms': dict(sorted(plats.items(), key=lambda kv: (-kv[1], kv[0]))), 'kinds': kinds}


def brand_category_for(summary):
    """(canonical BRAND CATEGORY, the reason in plain words). The list's
    plurality decides: watch pages read as STREAMING/PLATFORM, podcast or
    music pages as PODCAST or STREAMING MUSIC, articles and sites as
    MEDIA; a tie goes to MEDIA, the broadest content family."""
    k = dict((summary or {}).get('kinds') or {})
    n = int((summary or {}).get('count') or 0) or 1
    watch, listen, read = int(k.get('watch') or 0), int(k.get('listen') or 0), int(k.get('article') or 0) + int(k.get('site') or 0)
    plats = (summary or {}).get('platforms') or {}
    top = ', '.join(f"{p} {c}" for p, c in list(plats.items())[:4])
    if watch > max(listen, read):
        return 'STREAMING/PLATFORM', f"most of the list is streaming watch pages ({watch} of {n}: {top})"
    if listen > max(watch, read):
        pods = sum(c for p, c in plats.items() if p in ('Apple Podcasts', 'Spotify', 'iHeart', 'SiriusXM', 'Pandora'))
        music = sum(c for p, c in plats.items() if p in ('Apple Music', 'Audible'))
        if pods >= music and any(p in plats for p in ('Apple Podcasts', 'iHeart')):
            return 'PODCAST', f"most of the list is podcast pages ({listen} of {n}: {top})"
        return 'STREAMING MUSIC', f"most of the list is listening pages ({listen} of {n}: {top})"
    if read > max(watch, listen):
        return 'MEDIA', f"most of the list is articles and sites ({read} of {n})"
    return 'MEDIA', 'the list mixes watch pages and articles evenly, so it reads as media'


def seed_platforms(items):
    """The content map's spelling of every watch / listen platform on the
    list, in order of weight (rides content_seed_platforms)."""
    counts = {}
    for it in items:
        if it.get('kind') in ('watch', 'listen') and it.get('seed_platform'):
            counts[it['seed_platform']] = counts.get(it['seed_platform'], 0) + 1
    return [p for p, _c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def seed_urls(items):
    return [it['url'] for it in items if it.get('kind') in ('watch', 'listen')]


# --------------------------------------------------------------- titles
def _fetch_allowed():
    if os.environ.get('REGRESSION_TEST_MODE') == '1':
        return False
    return os.environ.get('TWIN_URL_LIST_FETCH', '1') not in ('0', 'false', 'no', 'off')


def _clean_title(t, host=''):
    t = re.sub(r"\s+", ' ', str(t or '')).strip()
    t = re.sub(r"\s*[|:\-]\s*(?:Netflix(?: Official Site)?|Hulu|Peacock|Max|Paramount\+|Disney\+|Prime Video|Apple TV\+?|YouTube|Tubi|Pluto TV|Spotify|Apple Podcasts|Audible|Twitch|The New York Times|The Washington Post|CNN|BBC|NPR|Variety|Deadline|Vulture)\s*$", '', t, flags=re.I)
    t = re.sub(r"^(?:Watch|Stream)\s+", '', t, flags=re.I)
    t = re.sub(r"\s*\|\s*.*$", '', t) if len(t) > 90 else t
    return t[:120]


def titles_from_content_map(items, connect_fn=None, run_id=''):
    """Titles for watch URLs the content map already holds (the same
    table a viewers profile seeds from), by the platform's own URL
    fragment. Never raises; a missing connection leaves the items."""
    try:
        from migration.viewer_content_scope import content_mapping_fragment
    except Exception:
        return 0
    frags = {}
    for it in items:
        if it.get('kind') != 'watch' or it.get('title'):
            continue
        try:
            plat, frag = content_mapping_fragment(it['url'])
        except Exception:
            plat, frag = None, ''
        if plat and frag and re.match(r"^[A-Za-z0-9_./?=:-]+$", frag):
            frags.setdefault(frag, []).append(it)
    if not frags:
        return 0
    try:
        if connect_fn is None:
            if os.environ.get('REGRESSION_TEST_MODE') == '1':
                return 0
            from migration.clickhouse_connector import connect_clickhouse as connect_fn  # type: ignore
        conn = connect_fn()
        cur = conn.cursor()
        keys = list(frags)[:80]
        where = ' OR '.join(f"positionCaseInsensitive(URL, '{k}') > 0" for k in keys)
        cur.execute(f"SELECT SHOW, URL, PLATFORM FROM reference.content_mapping WHERE {where} LIMIT 400")
        rows = cur.fetchall() or []
        try:
            cur.close()
            conn.close()
        except Exception:
            pass
    except Exception as e:
        print(f"[{run_id}] url-list content map lookup skipped: {e}")
        return 0
    n = 0
    for row in rows:
        try:
            show, url, plat = (str(row[0] or '').strip(), str(row[1] or ''), str(row[2] or '').strip())
        except Exception:
            continue
        for k, its in frags.items():
            if k.lower() in url.lower():
                for it in its:
                    if not it.get('title') and show:
                        it['title'] = show
                        it['title_source'] = 'content map'
                        if plat and not it.get('seed_platform'):
                            it['seed_platform'] = plat
                        n += 1
    return n


def titles_from_pages(items, limit=40, timeout=6.0, fetch=None):
    """Best effort page titles (og:title, then <title>) for items still
    without one. ``fetch(url) -> html`` is injectable; off in tests and
    with TWIN_URL_LIST_FETCH=0. Never raises."""
    todo = [it for it in items if not it.get('title')][:limit]
    if not todo:
        return 0
    if fetch is None:
        if not _fetch_allowed():
            return 0

        def fetch(url):
            import requests
            r = requests.get(url, timeout=timeout, headers={'User-Agent': 'Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/124 Safari/537.36',
                                                              'Accept-Language': 'en-US,en;q=0.9'}, allow_redirects=True)
            return r.text[:200_000] if r.ok else ''
    n = 0
    for it in todo:
        try:
            html = fetch(it['url']) or ''
        except Exception:
            html = ''
        if not html:
            continue
        m = re.search(r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']{2,200})", html, re.I) \
            or re.search(r"<meta[^>]+content=[\"']([^\"']{2,200})[\"'][^>]+property=[\"']og:title[\"']", html, re.I) \
            or re.search(r"<title[^>]*>([^<]{2,200})</title>", html, re.I | re.S)
        if m:
            t = _clean_title(_html_unescape(m.group(1)), it.get('host'))
            if t and not re.search(r"^(?:access denied|just a moment|attention required|page not found|error|sign in|log in)\b", t, re.I):
                it['title'] = t
                it['title_source'] = 'page'
                n += 1
    return n


def _html_unescape(s):
    try:
        import html
        return html.unescape(str(s or ''))
    except Exception:
        return str(s or '')


def resolve_titles(items, run_id='', connect_fn=None, fetch=None):
    """Content map first, then the pages. Returns how many titles landed."""
    n = 0
    try:
        n += titles_from_content_map(items, connect_fn=connect_fn, run_id=run_id)
    except Exception:
        traceback.print_exc()
    try:
        n += titles_from_pages(items, fetch=fetch)
    except Exception:
        traceback.print_exc()
    return n


# -------------------------------------------------------------- window
def default_window():
    try:
        from migration.event_window import default_window as _dw
        s, e = _dw()
        return {'start': str(s), 'end': str(e), 'label': 'Trailing 12 months'}
    except Exception:
        today = date.today()
        try:
            start = today.replace(year=today.year - 1)
        except ValueError:
            start = today - timedelta(days=365)
        return {'start': start.isoformat(), 'end': today.isoformat(), 'label': 'Trailing 12 months'}


def window_from_text(text):
    """An explicit window in the words, else the standing default.
    'YYYY-MM-DD to YYYY-MM-DD', 'last 6 months', 'calendar 2025' /
    'in 2025'."""
    t = ' '.join(str(text or '').split())
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:to|through|thru|-|until)\s*(\d{4}-\d{2}-\d{2})", t)
    if m:
        return {'start': m.group(1), 'end': m.group(2), 'label': f"{m.group(1)} to {m.group(2)}", 'explicit': True}
    m = re.search(r"\b(?:last|trailing|past)\s+(\d{1,2})\s+months?\b", t, re.I)
    if m:
        n = max(1, min(int(m.group(1)), 36))
        today = date.today()
        month = today.month - n
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        day = min(today.day, 28)
        return {'start': date(year, month, day).isoformat(), 'end': today.isoformat(), 'label': f"Trailing {n} months", 'explicit': True}
    m = re.search(r"\b(?:calendar|in|for|during)\s+(20\d{2})\b", t, re.I)
    if m:
        y = int(m.group(1))
        today = date.today()
        end = date(y, 12, 31) if y < today.year else today
        return {'start': f"{y}-01-01", 'end': end.isoformat(), 'label': f"Calendar {y}", 'explicit': True}
    return default_window()


def _normalize_window(window):
    w = window if isinstance(window, dict) else {}
    s, e = str(w.get('start') or '')[:10], str(w.get('end') or '')[:10]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s) and re.match(r"^\d{4}-\d{2}-\d{2}$", e) and s <= e:
        out = {'start': s, 'end': e, 'label': str(w.get('label') or f"{s} to {e}")}
        dw = default_window()
        if (s, e) == (dw['start'], dw['end']):
            out['label'] = 'Trailing 12 months'
        return out
    return default_window()


def window_words(window):
    w = _normalize_window(window)

    def _p(iso):
        try:
            return date.fromisoformat(iso).strftime('%b %-d, %Y')
        except Exception:
            return iso
    return f"{w.get('label') or 'Trailing 12 months'} ({_p(w['start'])} to {_p(w['end'])})"


# --------------------------------------------------------------- store
def _s3():
    try:
        from .host import host
        return host.s3_client, (host.bucket if host.has('bucket') else BUCKET)
    except Exception:
        import boto3
        return boto3.client('s3'), BUCKET


def list_key(token):
    return f"{LIST_PREFIX}{re.sub(r'[^A-Za-z0-9_-]+', '', str(token or ''))}.json"


def store_list(owner, items, *, name='', window=None, file_name='', dropped=0, s3=None, bucket=None):
    """Write the parsed list (URLs only, nothing else from the upload)
    and return its record. The token is what the confirm step hands
    back; the job reads the record by key."""
    if s3 is None:
        s3, bucket = _s3()
    bucket = bucket or BUCKET
    token = secrets.token_urlsafe(12).replace('-', 'x').replace('_', 'y')
    summary = summarize(items)
    cat, why = brand_category_for(summary)
    rec = {'token': token, 'key': list_key(token), 'owner': str(owner or ''), 'name': str(name or '')[:140],
           'window': _normalize_window(window), 'file_name': str(file_name or '')[:160], 'created_at': time.time(),
           'count': len(items), 'dropped': int(dropped or 0), 'summary': summary, 'brand_category': cat, 'category_reason': why,
           'items': [{k: it.get(k, '') for k in ('url', 'host', 'platform', 'kind', 'seed_platform', 'title')} for it in items]}
    s3.put_object(Bucket=bucket, Key=rec['key'], Body=json.dumps(rec, default=str).encode('utf-8'), ContentType='application/json')
    return rec


def load_list(key_or_token, s3=None, bucket=None):
    if s3 is None:
        s3, bucket = _s3()
    bucket = bucket or BUCKET
    key = key_or_token if str(key_or_token or '').startswith(LIST_PREFIX) else list_key(key_or_token)
    try:
        rec = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8'))
        return rec if isinstance(rec, dict) and rec.get('items') else None
    except Exception:
        return None


def seed_csv_bytes(items):
    """The seed file the engine's marker points at: the URL rows and what
    we read off them, nothing else."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['url', 'host', 'platform', 'kind', 'title'])
    for it in items:
        w.writerow([_export_cell(it.get('url')), it.get('host', ''), it.get('platform', ''), it.get('kind', ''), _export_cell(it.get('title', ''))])
    return buf.getvalue().encode('utf-8')


def _export_cell(cell):
    s = '' if cell is None else str(cell)
    return ("'" + s) if s and s.lstrip()[:1] in ('=', '+', '-', '@') else s


# --------------------------------------------------------------- price
def _usd(key, dflt, username):
    if os.environ.get('REGRESSION_TEST_MODE') == '1':
        return float(dflt)
    try:
        from prometheus.legacy.watch import _pm_usd
        v = float(_pm_usd(key, dflt, username) or 0)
        return v if v > 0 else float(dflt)
    except Exception:
        return float(dflt)


def _label(v, username):
    if os.environ.get('REGRESSION_TEST_MODE') != '1':
        try:
            from prometheus.legacy.watch import _pm_usd_label
            return _pm_usd_label(v, username)
        except Exception:
            pass
    return f"${v:,.0f}" if abs(v - round(v)) < 0.009 else f"${v:,.2f}"


def price(username=None):
    """The three numbers and the one line: the audience build, the twin
    with its first two runs, each further run."""
    p = _usd(PROFILE_PRICE_KEY, PROFILE_PRICE_DEFAULT, username)
    t = _usd(TWIN_PRICE_KEY, TWIN_PRICE_DEFAULT, username)
    r = _usd(RUN_PRICE_KEY, RUN_PRICE_DEFAULT, username)
    out = {'profile_usd': p, 'twin_usd': t, 'run_usd': r, 'total_usd': p + t,
           'profile_label': _label(p, username), 'twin_label': _label(t, username), 'run_label': _label(r, username), 'total_label': _label(p + t, username)}
    out['line'] = (f"Price: {out['total_label']} charged now ({out['profile_label']} for the audience build and {out['twin_label']} for the twin "
                   f"with its first two runs); each further run {out['run_label']}.")
    return out


# ---------------------------------------------------------------- copy
def _hosts_words(summary, limit=5):
    hosts = list((summary or {}).get('hosts') or [])
    parts = [f"{h} ({n})" for h, n in hosts[:limit]]
    more = len(hosts) - limit
    return ', '.join(parts) + (f" and {more} more host{'s' if more != 1 else ''}" if more > 0 else '')


def list_words(rec):
    """One plain line reading the list back: count, hosts, dropped rows."""
    s = rec.get('summary') or summarize(rec.get('items') or [])
    n = int(rec.get('count') or s.get('count') or 0)
    k = s.get('kinds') or {}
    shape = []
    if k.get('watch'):
        shape.append(f"{k['watch']} watch page{'s' if k['watch'] != 1 else ''}")
    if k.get('listen'):
        shape.append(f"{k['listen']} listening page{'s' if k['listen'] != 1 else ''}")
    read = int(k.get('article') or 0) + int(k.get('site') or 0)
    if read:
        shape.append(f"{read} article{'s' if read != 1 else ''} or site page{'s' if read != 1 else ''}")
    line = f"{n:,} URL{'s' if n != 1 else ''}" + (f" ({', '.join(shape)})" if shape else '') + f" across {_hosts_words(s)}"
    d = int(rec.get('dropped') or 0)
    if d:
        line += f"; {d} row{'s' if d != 1 else ''} with no usable link dropped"
    return line + '.'


def confirm_reply(rec, name, window, username=None):
    pr = price(username)
    lines = ['Here is the twin brief:',
             f"- Audience: {name}",
             f"- Defined by: {list_words(rec)}",
             f"- The people: US people who watched, read or visited this content in the window",
             f"- Window: {window_words(window)}",
             f"- Category: {rec.get('brand_category') or 'MEDIA'} ({rec.get('category_reason') or 'by the hosts on the list'})",
             f"- {pr['line']}",
             f"The audience is pulled first, then the twin is built from it; both land on your Twins tab and I confirm here. Say '{RUN_LABEL}' and I will line it up."]
    return '\n'.join(lines)


def promise(name):
    return (f"On it. Pulling the {name} audience from your list first (that is the long part, usually under an hour), then building the twin "
            f"from it. It lands on the Twins tab of Digital Twins IQ and I will confirm here and by email when it is ready.")


# ------------------------------------------------------- the interpret
def audience_prompt(name, rec, window):
    """The brief the interpret reads: the audience by name, defined by the
    list, one any-of universe, nothing matched or renamed."""
    items = rec.get('items') or []
    s = rec.get('summary') or summarize(items)
    w = _normalize_window(window)
    lines = []
    for it in items[:60]:
        title = str(it.get('title') or '').strip()
        plat = it.get('platform') or it.get('host')
        kind = {'watch': 'watch page', 'listen': 'listening page', 'site': 'site page'}.get(it.get('kind'), 'article')
        lines.append(f"- {title + ' ' if title else ''}({plat} {kind}: {it['url'][:160]})")
    more = len(items) - 60
    if more > 0:
        lines.append(f"- and {more} more URL{'s' if more != 1 else ''} across {_hosts_words(s)}")
    cat, _why = brand_category_for(s)
    return (f"run a profile on {name}\n\n"
            f"VERIFIED CONTEXT (already researched, treat as fact): {name} is ONE audience defined by an uploaded list of "
            f"{len(items)} content URLs. The universe is the US people who watched, read or visited ANY of this content between "
            f"{w['start']} and {w['end']} (an any-of union; a person counts once they consumed any item on the list). "
            f"The list is the qualifier, so this is a fresh new_build, never an existing_match, never a cut: keep the subject "
            f"exactly '{name}', emit no addon_cuts, set seed_source to null and content_show to null (the list itself is the seed), "
            f"and use brand_category '{cat}'. Size the universe from the combined US reach of the listed content, deduplicated "
            f"for overlap. Describe the audience in persona_notes from what these titles and sites are and who in the US consumes them. "
            f"The content:\n" + '\n'.join(lines))


def interpret_draft(prompt, usage_extras=None, draft_fn=None):
    """The interpret step on the composed brief (the approve-rescue
    pattern: the prompts, the pruned rulebook, one model call). Returns
    the draft dict with tu_demos, or None."""
    if draft_fn is not None:
        try:
            d = draft_fn(prompt)
            return d if isinstance(d, dict) and d.get('tu_demos') else None
        except Exception:
            traceback.print_exc()
            return None
    if os.environ.get('REGRESSION_TEST_MODE') == '1':
        return None
    try:
        from prometheus.legacy import chat as _chat
        from prometheus import smarter as _smart
        try:
            from iq_rankers import MASTER_CATEGORIES
        except Exception:
            MASTER_CATEGORIES = {}
        sys_p, usr_p = _chat._synth_chat_interpret_prompts(prompt, chat_history=None, master_categories=MASTER_CATEGORIES, candidate_matches=[])
        try:
            sys_p, _dropped = _smart.prune_rulebook(sys_p, prompt, None)
        except Exception:
            pass
        try:
            ceiling = _smart.draft_output_ceiling(prompt)
        except Exception:
            ceiling = 8192
        fresh = _chat._H._run_nflx_claude_agent(system_prompt=sys_p, user_prompt=usr_p, max_tokens=ceiling, temperature=0.4,
                                                 model=getattr(_chat, '_SYNTH_CHAT_INTERPRET_MODEL', None),
                                                 usage_tag=('interpret', 'twin-url-list', usage_extras))
        fd = (fresh or {}).get('data') or {}
        if isinstance(fd, list):
            fd = next((d for d in fd if isinstance(d, dict)), {})
        if (fresh or {}).get('success') and isinstance(fd, dict) and fd.get('tu_demos'):
            return fd
        print(f"[twin-url-list] interpret came back without demos: {str(fd)[:200]}")
        return None
    except Exception:
        traceback.print_exc()
        return None


_DEMO_COLUMNS = ('GENDER', 'AGE', 'ETHNICITY', 'EDUCATION', 'INCOME', 'OCCUPATION', 'PARENTAL_STATUS', 'RELATIONSHIP', 'SEXUAL_ORIENTATION')


def fallback_draft(name, rec, window, s3=None, gen_pop_rows=None):
    """A US-shaped draft when the model read fails: the Gen Pop demo
    shape (the engine's own demo passes reshape it from the brief), the
    list as the brief, a size from the list's weight. Never a round
    sample."""
    items = rec.get('items') or []
    s = rec.get('summary') or summarize(items)
    cat, _why = brand_category_for(s)
    rows = gen_pop_rows
    if rows is None:
        try:
            from migration.digital_twin_synthesis import _gen_pop_rows
            rows = _gen_pop_rows(s3) if s3 is not None else []
        except Exception:
            rows = []
    demos = {}
    for r in rows or []:
        col = str(r.get('Column') or '').strip().upper()
        if col in _DEMO_COLUMNS:
            try:
                bp = float(str(r.get('Brand Penetration (Row)') or '').replace('%', '').strip())
            except (TypeError, ValueError):
                continue
            demos.setdefault(col, {})[str(r.get('Value') or '').strip()] = round(bp, 4)
    if not demos:
        demos = {'GENDER': {'FEMALE': 50.7391, 'MALE': 48.1227, 'NON-BINARY': 1.1382},
                 'AGE': {'18-24': 11.4731, '25-34': 17.6218, '35-44': 17.2109, '45-54': 15.8873, '55-64': 16.3312, '65 OR OLDER': 21.4757}}
    h = int(hashlib.sha256(f"{name}|url-list|{len(items)}".encode()).hexdigest()[:8], 16)
    base = 6_400 + (len(items) * 311) % 9_000 + h % 4_000
    sample = base + 1 if base % 10 == 0 else base
    w = _normalize_window(window)
    return {'subject': name, 'brand_category': cat, 'audience_type': 'general', 'decision': 'new_build', 'run_avid': True,
            'subject_rows': [[cat, name]], 'extra_rows': [], 'tu_demos': demos, 'avid_demos': demos,
            'subject_raw_tu': sample, 'subject_raw_avid': max(600, int(sample * 0.23) + 3),
            'universe_anchor': int(sample * 32.99), 'engaged_share': 1.0,
            'anchor_source': f"US people who consumed the {len(items)} listed pages in the window",
            'persona_notes': (f"{name}: US people who watched, read or visited any of {len(items)} listed content URLs between "
                              f"{w['start']} and {w['end']}. Hosts: {_hosts_words(s, 8)}."),
            'date_range': {'start': w['start'], 'end': w['end']}, 'date_range_explicit': True}


def _draft_hygiene(draft, name, rec, window):
    """The parts of the draft the list decides, whatever the model said:
    the name, a fresh build, no cuts, no content-map seed, the window,
    the category by the hosts, no title pinned on a multi-title list."""
    d = dict(draft or {})
    d['subject'] = name
    d['name'] = name
    d.pop('file_stem', None)
    d['decision'] = 'new_build'
    d['run_avid'] = True
    for k in ('addon_cuts', 'existing_match_s3_key', 'existing_match_display_name', 'existing_match_days_old', 'subiq', 'derive_type', 'cut_label',
              'universe_mode', 'universe_note', 'event_window', 'identity_versions', 'franchise_titles', 'content_show', 'seed_source', 'named_platforms',
              'consumers_sample_fraction', 'ip_scope', 'platform_scope'):
        d.pop(k, None)
    d['addon_cuts'] = []
    d['seed_source'] = None
    d['content_show'] = None
    s = rec.get('summary') or summarize(rec.get('items') or [])
    cat, _why = brand_category_for(s)
    d['brand_category'] = cat
    w = _normalize_window(window)
    d['date_range'] = {'start': w['start'], 'end': w['end']}
    d['date_range_explicit'] = True
    # a list of several titles pins none of them: the subject's own row is the anchor
    titles = {str(it.get('title') or '').strip().lower() for it in (rec.get('items') or []) if str(it.get('title') or '').strip()}
    plats = set((s.get('platforms') or {}).keys())
    rows = []
    for row in (d.get('subject_rows') or []):
        try:
            col, val = (row[0], row[1]) if isinstance(row, (list, tuple)) else (row.get('column'), row.get('value'))
        except Exception:
            continue
        cu, vl = str(col or '').strip().upper(), str(val or '').strip().lower()
        if vl == name.lower():
            rows.append([col, val])
            continue
        if cu in ('SERIES', 'MOVIE', 'PODCAST', 'GAMES', 'GAME PLAYERS', 'VERTICAL SHORTS') and len(titles) > 1:
            d.setdefault('extra_rows', []).append([col, val])
            continue
        if cu in ('STREAMING/PLATFORM', 'STREAMING VIDEO', 'BROADCAST/CABLE', 'APP/PLATFORM', 'STREAMING MUSIC', 'FAST PLATFORM', 'VMVPD/FAST') and len(plats) > 1:
            d.setdefault('extra_rows', []).append([col, val])
            continue
        rows.append([col, val])
    if not rows:
        rows = [[cat, name]]
    d['subject_rows'] = rows
    return d


def spec_body(draft, *, name, rec, window, run_id, seed_key, requester, spec_from_draft=None, prompt=''):
    """The queue body for the file-fed build. ``spec_from_draft`` is the
    chat module's converter (injected in tests)."""
    d = _draft_hygiene(draft, name, rec, window)
    if spec_from_draft is None:
        from prometheus.legacy.chat import _spec_from_draft as spec_from_draft  # type: ignore
    spec = spec_from_draft(d)
    spec = dict(spec or {})
    items = rec.get('items') or []
    s = rec.get('summary') or summarize(items)
    cat, why = brand_category_for(s)
    w = _normalize_window(window)
    spec['name'] = name
    spec['file_stem'] = re.sub(r"[^A-Za-z0-9]+", '_', name).strip('_')[:80] or 'Audience'
    spec['brand_category'] = cat
    spec['universe_seed_csv'] = True
    spec['seed_file'] = seed_key
    spec['seed_source'] = None
    spec['content_show'] = None
    spec['date_range'] = f"{w['start']} TO {w['end']}"
    spec['url_list'] = {'key': rec.get('key') or '', 'count': len(items), 'hosts': [h for h, _n in (s.get('hosts') or [])][:20],
                        'category_reason': why}
    su = seed_urls(items)
    if su:
        spec['content_seed_urls'] = su[:_SEED_URL_CAP]
        spec['content_seed_platforms'] = seed_platforms(items)
    pn = str(spec.get('persona_notes') or '').strip()
    lead = (f"UNIVERSE DEFINITION: {name} = US people who watched, read or visited any of {len(items)} listed content pages "
            f"between {w['start']} and {w['end']} ({_hosts_words(s, 8)}). The list defines membership; every brand and demographic row "
            f"is reasoned for that group.")
    if lead not in pn:
        spec['persona_notes'] = (lead + ('\n\n' + pn if pn else ''))
    req = requester if isinstance(requester, dict) else {}
    uname = str(req.get('username') or '').strip()
    email = str(req.get('email') or '').strip()
    body = {'run_id': run_id, 'decision': 'new_build', 'run_avid': True, 'spec': spec,
            'user_email': email or uname or 'prometheus', 'username': uname, 'email_to': '',
            'prompt': (prompt or f"Build a twin of {name} from a list of {len(items)} content URLs")[:4000],
            'source': 'digital_twins_url_list', 'requested_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    try:
        from . import seams as _seams
        body = _seams.tag_queue(body)
    except Exception:
        pass
    return body


_SEED_URL_CAP = 400


# ---------------------------------------------------------- the build
def _queue_env():
    """(url, secret) for the engine's queue. The web tier names the URL
    in its env; on the build server the read worker has the secret and
    the listener is local (SYNTH_LISTENER_PORT, 8765 by default)."""
    url = str(os.environ.get('SYNTH_QUEUE_URL') or '').rstrip('/')
    secret = str(os.environ.get('SYNTH_QUEUE_SECRET') or '')
    if not url and secret:
        url = f"http://127.0.0.1:{str(os.environ.get('SYNTH_LISTENER_PORT') or '8765').strip()}"
    return url, secret


def queue_profile(body, post=None):
    """POST the body to the queue; returns (run_id, error_text)."""
    url, secret = _queue_env()
    if post is None:
        if not url or not secret:
            return '', 'the queue is not configured here'
        import requests

        def post(b):
            r = requests.post(f"{url}/synth/queue", json=b, timeout=30, headers={'X-Synth-Auth': secret, 'Content-Type': 'application/json'})
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {'error': r.text[:400]}
    try:
        code, doc = post(body)
    except Exception as e:
        return '', f"queue post failed: {e}"
    if code != 200:
        return '', f"queue returned {code}: {str((doc or {}).get('error') or doc)[:300]}"
    rid = str((doc or {}).get('run_id') or body.get('run_id') or '')
    return rid, ''


def profile_status(run_id, status_fn=None):
    """The engine's status document for a run, or {} (unknown / unreachable)."""
    url, secret = _queue_env()
    if status_fn is None:
        if not url or not secret:
            return {}
        import requests

        def status_fn(rid):
            r = requests.get(f"{url}/synth/status/{rid}", timeout=15, headers={'X-Synth-Auth': secret})
            return r.json() if r.status_code == 200 else {}
    try:
        doc = status_fn(run_id) or {}
    except Exception:
        doc = {}
    return doc if isinstance(doc, dict) else {}


def landed_key(doc):
    """The registered Total Universe key on an engine status document, or ''."""
    doc = doc if isinstance(doc, dict) else {}
    key = str(doc.get('tu_key') or doc.get('tu_s3_key') or doc.get('s3_key') or '')
    st = str(doc.get('status') or '')
    if key and (doc.get('tu_registered_in_dashboard') or st in ('tu_done', 'completed', 'complete', 'done')):
        return key
    return ''


def then_block(inputs, *, job_id, username, extras=None):
    """What rides the queue body as ``then``: the twin that waits on the
    audience. The engine hands it to the read worker, with the landed
    profile key on the inputs, the moment the Total Universe registers;
    on a genuine build failure it ends the twin with the same line and
    returns the twin's own charge."""
    keep = {k: v for k, v in dict(inputs or {}).items() if k not in ('pull_profile',)}
    keep['from_list'] = True
    keep['profile_key'] = ''
    keep['source'] = 'profile'
    keep['op'] = 'build'
    return {'kind': 'twin_build', 'job_id': str(job_id), 'username': str(username or ''), 'inputs': keep,
            'extras': dict(extras or {}), 'charged': dict((inputs or {}).get('charged') or {})}


def queue_profile_for_twin_job(inputs, *, job_id, username, run_id='', s3=None, bucket=None, progress=None, usage_extras=None,
                               draft_fn=None, spec_from_draft=None, post=None, status_fn=None, connect_fn=None, fetch=None, log=print):
    """Phase one of a list build, run inside the twin job: titles for the
    list, the brief, the draft, the spec body (with the ``then`` block),
    the seed file, the queue post. Returns a dict with state 'queued'
    (run_id set), 'landed' (tu_key set: an earlier attempt's audience
    already registered), 'not_queued' (error set) or 'no_list'. Never
    waits: the engine hands the twin back when the audience lands."""
    rec = inputs.get('url_list') if isinstance(inputs.get('url_list'), dict) else None
    if rec is None:
        rec = load_list(str(inputs.get('url_list_key') or ''), s3=s3, bucket=bucket)
    if not rec or not rec.get('items'):
        return {'tu_key': '', 'run_id': '', 'state': 'no_list', 'body': None}
    name = ' '.join(str(inputs.get('subject') or inputs.get('name') or rec.get('name') or '').split())[:140]
    window = _normalize_window(inputs.get('window') or rec.get('window'))
    run_id = run_id or str(inputs.get('profile_run_id') or '') or secrets.token_urlsafe(10)
    # a restarted job never posts the same audience twice: the run it already queued is the run
    prior = profile_status(run_id, status_fn=status_fn)
    if prior:
        key = landed_key(prior)
        st = str(prior.get('status') or '')
        if key:
            return {'tu_key': key, 'run_id': run_id, 'state': 'landed', 'body': None}
        if st in ('error', 'failed', 'cancelled'):
            return {'tu_key': '', 'run_id': run_id, 'state': 'cancelled' if st == 'cancelled' else 'failed', 'body': None}
        if st and st != 'unknown':
            log(f"[{run_id}] url-list: the audience is already {st}; waiting on it")
            return {'tu_key': '', 'run_id': run_id, 'state': 'queued', 'body': None, 'resumed': True}
    if callable(progress):
        progress('reading the titles on your list')
    items = rec['items']
    try:
        got = resolve_titles(items, run_id=run_id, connect_fn=connect_fn, fetch=fetch)
        log(f"[{run_id}] url-list: {got} title(s) resolved for {len(items)} URL(s)")
    except Exception:
        traceback.print_exc()
    rec['summary'] = summarize(items)
    if callable(progress):
        progress('reading the audience the list defines')
    prompt = audience_prompt(name, rec, window)
    draft = interpret_draft(prompt, usage_extras=usage_extras, draft_fn=draft_fn)
    if not draft:
        log(f"[{run_id}] url-list: model read unavailable; the US-shaped draft stands in")
        draft = fallback_draft(name, rec, window, s3=s3)
    seed_key = f"{SEED_PREFIX}{run_id}.csv"
    requester = {'username': username, 'email': str(inputs.get('requester_email') or '')}
    body = spec_body(draft, name=name, rec=rec, window=window, run_id=run_id, seed_key=seed_key, requester=requester,
                     spec_from_draft=spec_from_draft, prompt=str(inputs.get('ask_text') or ''))
    body['then'] = then_block(inputs, job_id=job_id, username=username, extras=usage_extras)
    if s3 is not None:
        try:
            s3.put_object(Bucket=bucket or BUCKET, Key=seed_key, Body=seed_csv_bytes(items), ContentType='text/csv')
        except Exception as e:
            log(f"[{run_id}] url-list: seed file write skipped: {e}")
    if callable(progress):
        progress('the audience is lined up')
    rid, err = queue_profile(body, post=post)
    if not rid:
        if 'already in progress' in str(err):
            log(f"[{run_id}] url-list: the audience is already building; waiting on it")
            return {'tu_key': '', 'run_id': run_id, 'state': 'queued', 'body': body, 'resumed': True}
        log(f"[{run_id}] url-list: {err}")
        return {'tu_key': '', 'run_id': run_id, 'state': 'not_queued', 'error': err, 'body': body}
    return {'tu_key': '', 'run_id': rid, 'state': 'queued', 'body': body}


WAITING_STAGE = 'pulling the audience from your list (usually under an hour); the twin follows'


# ------------------------------------------------------------ charging
def charge_both(user, uname, name, *, profile_run_id, twin_job_id, consume_credit=None, is_free=None):
    """Charge the audience build and the twin once, at confirm. Returns
    (charged, funds_error): charged = {'profile': credits, 'twin':
    credits} (0 where the seat is free); funds_error is the plain line
    when the account cannot cover it (nothing charged)."""
    charged = {'profile': 0, 'twin': 0}
    free = False
    try:
        if is_free is None:
            from prometheus.priced_read import seat_is_free
            free = bool(seat_is_free(user, uname, None))
        else:
            free = bool(is_free(user, uname))
    except Exception:
        free = str((user or {}).get('role') or '').lower().replace(' ', '_') in ('super_admin', 'superadmin') if isinstance(user, dict) else False
    if free:
        return charged, None
    if consume_credit is None:
        try:
            import sys as _sys
            app = _sys.modules.get('app')
            consume_credit = getattr(app, 'consume_credit', None)
        except Exception:
            consume_credit = None
    if consume_credit is None:
        return charged, None
    pr = price(uname)
    try:
        ok = bool(consume_credit(uname, description=f"Digital Twin audience build: {name}", pull_type=PROFILE_PULL_TYPE,
                                 credits_used=PROFILE_CREDITS, job_id=str(profile_run_id)))
    except Exception:
        traceback.print_exc()
        ok = False
    if not ok:
        return charged, (f"That build runs {pr['total_label']} ({pr['profile_label']} for the audience and {pr['twin_label']} for the twin) "
                         f"and your account cannot cover it right now. Add funds or ask your admin, and it runs the moment you are set.")
    charged['profile'] = PROFILE_CREDITS
    try:
        from migration.digital_twins import PULL_TYPES
        ok = bool(consume_credit(uname, description=f"Digital Twin: {name}", pull_type=PULL_TYPES['digital_twins_iq'],
                                 credits_used=TWIN_CREDITS, job_id=str(twin_job_id)))
    except Exception:
        traceback.print_exc()
        ok = False
    if not ok:
        refund(uname, PROFILE_CREDITS, f"Digital Twin audience build: {name} (not started)")
        charged['profile'] = 0
        return charged, (f"That build runs {pr['total_label']} and your account cannot cover the twin part right now. "
                         f"Add funds or ask your admin, and it runs the moment you are set.")
    charged['twin'] = TWIN_CREDITS
    return charged, None


def refund(uname, credits, reason, refund_credit=None):
    """Give a charge back (the twin, or both when the audience never
    queued). Never raises."""
    try:
        if int(credits or 0) <= 0:
            return False
        if refund_credit is None:
            import sys as _sys
            app = _sys.modules.get('app')
            refund_credit = getattr(app, 'refund_credit', None)
        if refund_credit is None:
            return False
        return bool(refund_credit(uname, credits=int(credits), reason=reason))
    except Exception:
        traceback.print_exc()
        return False


def start(user, uname, rec, name, window, *, thread_id='', purpose='', panelists=0, ask_text='', extras=None,
          start_fn=None, consume_credit=None, is_free=None):
    """The one starter both doors use: charge once, queue the twin job
    that pulls the audience and builds the twin. Returns (job_id, promise,
    funds_error)."""
    import uuid
    from . import digital_twin_job as J
    job_id = uuid.uuid4().hex[:12]
    profile_run_id = secrets.token_urlsafe(10)
    charged, err = charge_both(user, uname, name, profile_run_id=profile_run_id, twin_job_id=job_id, consume_credit=consume_credit, is_free=is_free)
    if err:
        return '', '', err
    email = str((user or {}).get('email') or '').strip() if isinstance(user, dict) else ''
    inputs = {'subject': name, 'name': name, 'subject_kind': 'audience', 'purpose': str(purpose or '')[:400], 'window': _normalize_window(window),
              'op': 'build', 'source': 'profile', 'profile_key': '', 'from_list': True, 'url_list_key': rec.get('key') or list_key(rec.get('token')),
              'profile_run_id': profile_run_id, 'charged': charged, 'requester_email': email, 'ask_text': str(ask_text or '')[:400],
              'panelists': int(panelists or 0) or None, 'thread_id': thread_id}
    if not inputs['panelists']:
        inputs.pop('panelists')
    J.start('twin_build', job_id, uname, inputs, thread_id=thread_id, extras=extras or {}, start_fn=start_fn)
    return job_id, promise(name), None


def run_pull_in_job(inputs, username, job_id, rec, status_write, *, s3=None, bucket=None, usage_extras=None, log=print, **kw):
    """The twin job's first phase for a list build. Returns the profile
    key when the audience already registered (the build continues), None
    when the audience is queued (the job stops here; the engine hands
    the twin back with the key when the Total Universe registers), or
    '' after a genuine failure (refund, the partner-safe line)."""
    def _progress(msg):
        rec['stage'] = str(msg or 'pulling the audience from your list')
        status_write(job_id, rec)
    res = queue_profile_for_twin_job(inputs, job_id=job_id, username=username, s3=s3, bucket=bucket, progress=_progress,
                                     usage_extras=usage_extras, log=log, **kw)
    rec['profile_run_id'] = res.get('run_id') or inputs.get('profile_run_id') or ''
    rec['profile_state'] = res.get('state')
    if res.get('tu_key'):
        rec['profile_key'] = res['tu_key']
        rec['stage'] = 'the audience is in; building the twin'
        status_write(job_id, rec)
        return res['tu_key']
    if res.get('state') == 'queued':
        rec.update({'status': 'waiting', 'queued': False, 'stage': WAITING_STAGE, 'waiting_on_profile': True})
        status_write(job_id, rec)
        return None
    charged = inputs.get('charged') if isinstance(inputs.get('charged'), dict) else {}
    name = str(inputs.get('subject') or inputs.get('name') or 'the audience')
    if res.get('state') in ('not_queued', 'no_list'):
        # the audience never reached the engine: both charges come back here. (A run the engine itself refused
        # or the requester stopped returns both from the engine: the audience charge by its run id, the twin's
        # through the then block.)
        refund(username, int(charged.get('twin') or 0), f"Digital Twin: {name}")
        refund(username, int(charged.get('profile') or 0), f"Digital Twin audience build: {name}")
    rec.update({'status': 'error', 'stage': 'stopped', 'finished_at': time.time(), 'error': FAIL_LINE, 'detail': str(res.get('error') or res.get('state') or '')[:300]})
    status_write(job_id, rec)
    return ''


# -------------------------------------------------- the Prometheus door
_TWIN_WORDS_RX = re.compile(r"\b(?:digital\s+)?twins?\b", re.I)
_BUILD_WORDS_RX = re.compile(r"\b(build|make|create|spin up|start|set up|pull|generate)\b", re.I)
_LIST_WORDS_RX = re.compile(r"\b(these|those|this list|the list|the attached|attached|uploaded|the file|the csv|the sheet|the spreadsheet|"
                            r"from the (?:csv|file|sheet|list)|in the (?:csv|file|sheet|list)|these urls|these links|this content|the content|"
                            r"list of (?:content )?(?:urls|links|pages)|urls?|links?)\b", re.I)
_PEOPLE_WORDS_RX = re.compile(r"\b(watched|read|visited|viewed|consumed|saw|listened|streamed|engaged)\b", re.I)
_NAME_RX = re.compile(r"\b(?:call(?:ed)?\s+(?:it|them|this|the audience)|name(?:d)?\s+(?:it|them|this|the audience)|audience\s+name|name)\s*[:=]?\s*[\"'“‘]?(?P<n>[^\"'”’\n]{3,100}?)[\"'”’]?\s*(?:[.;]|$|,\s*(?:and|then|with)\b)", re.I)
_QUOTED_RX = re.compile(r"[\"“](?P<n>[^\"”]{3,100})[\"”]")


def is_list_twin_ask(text, has_url_rows):
    """A twin order that points at a list of URLs: twin words plus list
    words (or a verb about consuming 'these'), with URL rows present on
    the thread or in the message. Deterministic; never fires on a plain
    'build a twin of Gilmore Girls'."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 2000 or not _TWIN_WORDS_RX.search(t):
        return False
    if re.search(r"\btwin\s+(?:bed|peaks|cities|falls|flame|turbo|engine|sister|brother|size)s?\b", t, re.I):
        return False
    urls, _d = urls_from_text(t)
    inline = len(urls) >= 2
    if not (has_url_rows or inline):
        return False
    if not (_BUILD_WORDS_RX.search(t) or re.search(r"\btwin\s+(?:of|for|from|off|on)\b", t, re.I)):
        return False
    return bool(_LIST_WORDS_RX.search(t) or _PEOPLE_WORDS_RX.search(t) or inline)


def name_from_text(text):
    """The audience name the words give, or ''. 'call it X', 'named X',
    'name: X', a quoted name, or the bare short answer to the name ask."""
    t = ' '.join(str(text or '').split())
    m = _NAME_RX.search(t)
    if m:
        n = m.group('n').strip(' .,;:')
        if 2 <= len(n.split()) <= 10 or (len(n) >= 4 and not _URL_RX.search(n)):
            return n[:140]
    m = _QUOTED_RX.search(t)
    if m and not _URL_RX.search(m.group('n')):
        return m.group('n').strip()[:140]
    return ''


def bare_name(text):
    """A short reply with no URL and no command words is the name."""
    t = ' '.join(str(text or '').split()).strip(' .')
    if not t or _URL_RX.search(t) or len(t) > 100 or len(t.split()) > 10:
        return ''
    if re.match(r"^(what|how|why|who|which|when|where|do|does|is|are|can|could|would|should|yes|no|ok|okay|cancel|never|thanks?|thank you|hi|hello|hey|sure|yep|yeah|please|build|run|open|show|list)\b", t, re.I):
        return ''
    return t[:140]


def _host_s3(host):
    """(s3, bucket) off either host shape: the Prometheus host (s3_client,
    bucket) or the legacy chat host (s3_client, S3_BUCKET)."""
    s3 = bucket = None
    for attr in ('s3_client',):
        try:
            s3 = getattr(host, attr)
        except Exception:
            s3 = None
    for attr in ('S3_BUCKET', 'bucket'):
        try:
            bucket = getattr(host, attr)
        except Exception:
            bucket = None
        if bucket:
            break
    if s3 is None:
        s3, b2 = _s3()
        bucket = bucket or b2
    return s3, (bucket or BUCKET)


def _attached_url_list(uname, tid, host):
    """(urls, dropped, file_name) from the most recent attached sheet or
    text file that carries links, else ([], 0, '')."""
    try:
        from . import documents as _docs
        s3, bucket = _host_s3(host)
        recs = _docs.attachments(uname, tid, s3, bucket) if uname else []
        for r in reversed(list(recs or [])):
            if r.get('kind') not in ('sheet', 'text'):
                continue
            data = _docs.document_bytes(r, s3, bucket)
            urls, dropped = urls_from_bytes(r.get('name') or 'list.txt', data)
            if urls:
                return urls, dropped, str(r.get('name') or '')
    except Exception:
        traceback.print_exc()
    return [], 0, ''


def thread_has_url_rows(uname, tid, host):
    try:
        from . import documents as _docs
        s3, bucket = _host_s3(host)
        for r in reversed(list(_docs.attachments(uname, tid, s3, bucket) or [])):
            if r.get('kind') == 'sheet' and any((s or {}).get('url_count') for s in (r.get('sheets') or [])):
                return True
            if r.get('kind') == 'text' and _URL_RX.search(str(r.get('excerpt') or '')):
                return True
    except Exception:
        pass
    return False


def intake_armed(history):
    """The last agent turn was this intake's name ask or its brief."""
    for t in reversed(history or []):
        if not isinstance(t, dict) or str(t.get('role') or '') not in ('agent', 'assistant'):
            continue
        txt = str(t.get('text') or t.get('content') or '')
        return (ASK_NAME[:40] in txt) or (txt.startswith('Here is the twin brief') and '- Defined by:' in txt) or txt.startswith(ASK_LIST[:30])
    return False


def _payload(reply, followups=None, **extra):
    try:
        from migration.digital_twin_synthesis import scrub_text as _sc
    except Exception:
        _sc = lambda s: s  # noqa: E731
    out = {'success': True, 'action': 'answer', 'reply': _sc(reply), 'followups': [_sc(f) for f in (followups or [])], 'offer_deck': False, 'deck_angle': None}
    out.update(extra)
    return out


def analyze_hook(text, body, history, user, ppu, uname, *, host, pending, start_fn=None, consume_credit=None, is_free=None):
    """The list door on the analyze surface. ``pending(op, user, flow,
    *args)`` is the chat's stash / pick. Returns a reply payload, or None
    when the ask is not this door's."""
    try:
        t = str(text or '').strip()
        low = ' '.join(t.lower().split())
        tid = str((body or {}).get('thread_id') or '')
        # 1. the confirm: the chip, the stashed brief, or the echoed card
        confirm = (body or {}).get('twin_list_confirm') or pending('pick', user, FLOW, t)
        s3, bucket = _host_s3(host)
        if isinstance(confirm, dict) and confirm.get('list_key') and confirm.get('name'):
            rec = load_list(confirm['list_key'], s3=s3, bucket=bucket)
            if not rec:
                return _payload('That list is no longer on the thread. Attach it again and I will read it back.', ['Cancel'])
            job_id, line, err = start(user, uname, rec, confirm['name'], confirm.get('window'), thread_id=tid, purpose=str(confirm.get('purpose') or ''),
                                      ask_text=t, extras=ppu or {}, start_fn=start_fn, consume_credit=consume_credit, is_free=is_free)
            if err:
                return _payload(err)
            return _payload(line, [], twin_job_id=job_id, job_id=job_id, job_type='twin_build', open_view='digitalTwinsIQ')
        armed = intake_armed(history)
        if not armed and not _TWIN_WORDS_RX.search(t):
            return None      # the cheap gate: no S3 read on an ordinary message
        if armed and re.match(r"^\s*(never ?mind|no( thanks?)?|not now|cancel( (that|it))?)[.! ]*$", low):
            pending('clear', user, FLOW)
            return _payload('No problem, nothing was charged. Ask me anything else when ready.')
        has_rows = thread_has_url_rows(uname, tid, host) if _TWIN_WORDS_RX.search(t) else False
        if not (is_list_twin_ask(t, has_rows) or (armed and (bare_name(t) or name_from_text(t) or urls_from_text(t)[0]))):
            return None
        # 2. the list: pasted in the message, else the one already read, else the attachment on the thread
        draft = pending('peek', user, FLOW) or {}
        if not isinstance(draft, dict):
            draft = {}
        urls, dropped = urls_from_text(t)
        file_name = 'your message'
        if len(urls) < 2:
            urls = []
            if not draft.get('list_key'):
                urls, dropped, file_name = _attached_url_list(uname, tid, host)
        if not urls and not draft.get('list_key'):
            return _payload(ASK_LIST, ['Cancel'], twin_list_collect=True)
        name = name_from_text(t) or (bare_name(t) if armed else '') or str(draft.get('name') or '')
        window = window_from_text(t) if re.search(r"\d{4}-\d{2}-\d{2}|\b(?:last|trailing|past)\s+\d{1,2}\s+months?\b|\b(?:calendar|in|for|during)\s+20\d{2}\b", t, re.I) \
            else (draft.get('window') or default_window())
        if urls:
            rec = store_list(uname, classify(urls), name=name, window=window, file_name=file_name, dropped=dropped, s3=s3, bucket=bucket)
        else:
            rec = load_list(draft['list_key'], s3=s3, bucket=bucket)
            if not rec:
                return _payload(ASK_LIST, ['Cancel'], twin_list_collect=True)
        brief = {'list_key': rec['key'], 'name': name, 'window': _normalize_window(window), 'file_name': file_name, 'count': rec.get('count')}
        if not name:
            pending('stash', user, FLOW, brief, '')
            return _payload(f"I read the list: {list_words(rec)} " + ASK_NAME, ['Cancel'], twin_list_collect=True)
        pending('stash', user, FLOW, brief, RUN_LABEL)
        return _payload(confirm_reply(rec, name, window, uname), [RUN_LABEL, 'Cancel'], twin_list_confirm_payload=brief, twin_list_collect=True)
    except Exception:
        traceback.print_exc()
        return None


__all__ = ['FLOW', 'RUN_LABEL', 'ASK_NAME', 'ASK_LIST', 'FAIL_LINE', 'urls_from_text', 'urls_from_rows', 'urls_from_bytes', 'classify', 'summarize',
           'brand_category_for', 'seed_platforms', 'seed_urls', 'resolve_titles', 'titles_from_pages', 'titles_from_content_map', 'default_window',
           'window_from_text', 'window_words', 'store_list', 'load_list', 'list_key', 'seed_csv_bytes', 'price', 'list_words', 'confirm_reply', 'promise',
           'audience_prompt', 'interpret_draft', 'fallback_draft', 'spec_body', 'queue_profile', 'profile_status', 'landed_key', 'then_block',
           'queue_profile_for_twin_job', 'WAITING_STAGE',
           'charge_both', 'refund', 'start', 'run_pull_in_job', 'is_list_twin_ask', 'name_from_text', 'bare_name', 'intake_armed', 'analyze_hook',
           'thread_has_url_rows']
