"""
NetShort microdramas scraper.

Pulls the top vertical-drama titles on NetShort (www.netshort.com web
storefront). NetShort is a rapidly-growing vertical-drama app in
North America, estimated ~2-4M MAU (data.ai, Q2 2026). Their own
homepage tagline claims "45,000+ viral short dramas".

## Data source

`https://www.netshort.com/` renders with Next.js React Server
Components. The RSC payload streams through `self.__next_f.push([1,
"<escaped-json>"])` chunks. Inside the escaped strings live 5 named
rails, each with a `data` array of drama titles:

  - `Trending Now`         (primary chart signal)
  - `New Releases`
  - `Recommended`
  - `Exclusive Originals`
  - `You Might Like`

Also duplicated for us: a top-of-DOM ld+json `ItemList "Trending
Now"` with 12 authoritative TVSeries items (name, url, image,
numberOfEpisodes). That gives us a reliable minimum floor even if
the RSC decoding shifts underneath us.

Public. No cookies, no proxy, no auth needed.

Per-title fields we capture:

  - `shortPlayId`         -> internal ID + deep-link
  - `shortPlayName`       -> title
  - `shortPlayNameUrl`    -> URL slug (deep-link path)
  - `shortPlayCover`      -> poster URL (awscover.netshort.com CDN)
  - `totalEpisode`        -> total episode count
  - `shotIntroduce`       -> synopsis
  - `labelList`           -> theme/genre tags
  - `language`
  - Rail (which of the 5 groups it appeared under)

Snapshot shape:

    {
      "source":     "netshort",
      "label":      "NetShort",
      "fetched_at": ISO8601,
      "titles": [
        { "rank", "title", "poster_url", "deep_link", "book_id",
          "genre", "themes", "rail", "rail_position",
          "episodes_count", "introduction" }
      ]
    }

Standalone dev run:
    python3 -m scripts.microdramas_scrapers.netshort
    python3 -m scripts.microdramas_scrapers.netshort --seed
    python3 -m scripts.microdramas_scrapers.netshort --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


NETSHORT_HOMEPAGE = 'https://www.netshort.com/'

_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_0) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15'
)


# Rail priority. Titles surfaced on higher-priority rails win rank
# positions first; ties broken by rail_position asc.
_RAIL_PRIORITY = [
    'Trending Now',
    'New Releases',
    'Exclusive Originals',
    'Recommended',
    'You Might Like',
]


# Map NetShort's own label taxonomy to Crosswalk's 6-bucket genre scheme.
# Their labels lean into common vertical-drama tropes; we normalize.
_TAG_TO_GENRE = {
    # Werewolf / supernatural / fantasy
    'werewolf':          'Werewolf',
    'wolf':              'Werewolf',
    'alpha':             'Werewolf',
    'mate':              'Werewolf',
    'vampire':           'Werewolf',
    'dragon':            'Werewolf',
    'ghost':             'Werewolf',
    'fantasy':           'Werewolf',
    'olympus':           'Werewolf',
    'god':               'Werewolf',

    # Billionaire
    'billionaire':       'Billionaire',
    'trillionaire':      'Billionaire',
    'wealthy':           'Billionaire',
    'heiress':           'Billionaire',
    'inheritance':       'Billionaire',

    # CEO / romance / office
    'ceo':               'CEO',
    'boss':              'CEO',
    'president':         'CEO',
    'romance':           'CEO',
    'sweet love':        'CEO',
    'love at first':     'CEO',
    'flash marriage':    'CEO',
    'contract':          'CEO',
    'contract husband':  'CEO',
    'contract wife':     'CEO',

    # Mafia / hidden identity / mech / action
    'mafia':             'Mafia',
    'gangster':          'Mafia',
    'assassin':          'Mafia',
    'bodyguard':         'Mafia',
    'hidden identity':   'Mafia',
    'undercover':        'Mafia',
    'overlord':          'Mafia',
    'mech':              'Mafia',
    'guard':             'Mafia',

    # Revenge / rebirth
    'revenge':           'Revenge',
    'rebirth':           'Revenge',
    'reborn':            'Revenge',
    'reckoning':         'Revenge',
    'betrayal':          'Revenge',
    'redemption':        'Revenge',
    'punishment':        'Revenge',

    # Second Chance / family / pregnancy
    'second chance':     'Second Chance',
    'divorce':           'Second Chance',
    'reunion':           'Second Chance',
    'pregnancy':         'Second Chance',
    'baby':              'Second Chance',
    'family':            'Second Chance',
    'friends to lovers': 'Second Chance',
    'love after marriage': 'Second Chance',

    # Horror / thriller (map to Mafia bucket - dark/tension adjacent)
    'horror':            'Mafia',
    'thriller':          'Mafia',
    'seduce':            'Mafia',
}


def _http_get(url: str) -> Optional[str]:
    try:
        import requests  # type: ignore
    except ImportError:
        logger.warning('netshort: requests unavailable; skipping live pull')
        return None
    try:
        r = requests.get(url, timeout=15, headers={'User-Agent': _UA})
        if r.status_code != 200:
            logger.info('netshort: GET %s -> %s', url, r.status_code)
            return None
        return r.text
    except Exception as e:
        logger.info('netshort: GET %s failed (%s)', url, e)
        return None


def _tags_to_genre(tags: list[str], title: str) -> str:
    hay = ' '.join(tags or []).lower()
    for k, v in _TAG_TO_GENRE.items():
        if k in hay:
            return v
    tl = (title or '').lower()
    for k, v in _TAG_TO_GENRE.items():
        if k in tl:
            return v
    return ''


def _decode_rsc(html: str) -> str:
    """Undo the Next.js RSC string escaping so we can regex-parse the
    payload with normal JSON idioms. Idempotent-safe on already-decoded
    text."""
    return (html
            .replace('\\"', '"')
            .replace('\\\\', '\\')
            .replace('\\/', '/')
            .replace('\\n', '\n'))


def _grab_object(text: str, start: int, max_len: int = 4000) -> Optional[str]:
    """Return the JSON object starting at index `start` (must point at
    '{'), or None if the object exceeds max_len or is malformed."""
    depth, i = 0, start
    in_str, esc = False, False
    while i < len(text) and (i - start) < max_len:
        c = text[i]
        if esc:
            esc = False
        elif c == '\\':
            esc = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def _extract_items(decoded: str) -> list[dict]:
    """Extract every {shortPlayLibraryId:..., ..., totalEpisode:...}
    object. Dedupe by shortPlayId."""
    seen: dict[str, dict] = {}
    for m in re.finditer(r'\{"shortPlayLibraryId"', decoded):
        obj_str = _grab_object(decoded, m.start())
        if not obj_str:
            continue
        try:
            obj = json.loads(obj_str)
        except json.JSONDecodeError:
            continue
        pid = obj.get('shortPlayId')
        if pid and pid not in seen:
            seen[pid] = obj
    return list(seen.values())


def _pair_items_to_groups(decoded: str, items: list[dict]) -> dict:
    """Return {shortPlayId: rail_name}. The RSC payload emits each
    group as {"groupName": "<rail>", "data": [<items>]}. We locate each
    groupName's position in `decoded`, then walk forward pairing each
    shortPlayId occurrence to the NEAREST PRECEDING groupName."""
    group_positions = []
    for m in re.finditer(r'"groupName"\s*:\s*"([^"]+)"', decoded):
        group_positions.append((m.start(), m.group(1)))

    if not group_positions:
        return {}

    # For each shortPlayId occurrence in decoded, find the last
    # groupName positioned before it. First-occurrence wins per id.
    pair: dict[str, str] = {}
    id_re = re.compile(r'"shortPlayId"\s*:\s*"?(\d+)"?')
    for m in id_re.finditer(decoded):
        pid = m.group(1)
        if pid in pair:
            continue
        p = m.start()
        # Bisect for the last group_positions <= p
        rail = ''
        for gp, gname in group_positions:
            if gp <= p:
                rail = gname
            else:
                break
        pair[pid] = rail
    return pair


def _shape_item(obj: dict, rail: str, rail_position: int) -> dict:
    labels_raw = obj.get('labelList') or obj.get('tags') or []
    if isinstance(labels_raw, dict):
        labels_raw = list(labels_raw.values())
    labels: list[str] = []
    if isinstance(labels_raw, list):
        for l in labels_raw:
            if l is None:
                continue
            if isinstance(l, dict):
                name = l.get('name') or l.get('label') or l.get('title')
                if name:
                    labels.append(str(name))
            elif isinstance(l, str) and l.strip():
                labels.append(l.strip())

    pid  = str(obj.get('shortPlayId') or '').strip()
    slug = str(obj.get('shortPlayNameUrl') or '').strip()
    title = str(obj.get('shortPlayName') or obj.get('fullEpisodeName') or '').strip()

    if slug:
        # slug looks like "/episode/xxx-2064962492549566465" or "/full-episodes/..."
        deep_link = 'https://netshort.com' + slug if slug.startswith('/') else slug
    else:
        deep_link = f'https://netshort.com/full-episodes/{pid}' if pid else ''

    return {
        'title':          title,
        'series':         title,
        'book_id':        pid,
        'poster_url':     obj.get('shortPlayCover') or obj.get('cover') or '',
        'deep_link':      deep_link,
        'tags':           labels,
        'themes':         labels,
        'genre':          _tags_to_genre(labels, title),
        'rail':           rail,
        'rail_position':  rail_position,
        'episodes_count': obj.get('totalEpisode') or obj.get('numberOfEpisodes'),
        'introduction':   (obj.get('shotIntroduce') or obj.get('description') or '')[:600],
        'language':       obj.get('language') or '',
        'is_new':         (rail == 'New Releases'),
    }


def _shape_ld_items(html: str) -> list[dict]:
    """Fallback: use the ld+json ItemList "Trending Now" (12 TVSeries)
    if the RSC path returns nothing."""
    out: list[dict] = []
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.+?)</script>',
                          html, re.DOTALL):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        graph = data.get('@graph') if isinstance(data, dict) else None
        if not graph:
            continue
        for node in graph:
            if node.get('@type') != 'ItemList':
                continue
            for it in node.get('itemListElement') or []:
                obj = it.get('item') or it
                if not isinstance(obj, dict):
                    continue
                url  = obj.get('url') or ''
                pid_match = re.search(r'-(\d{15,})$', url)
                pid = pid_match.group(1) if pid_match else ''
                out.append({
                    'title':          obj.get('name') or '',
                    'series':         obj.get('name') or '',
                    'book_id':        pid,
                    'poster_url':     obj.get('image') or '',
                    'deep_link':      url,
                    'tags':           [],
                    'themes':         [],
                    'genre':          _tags_to_genre([], obj.get('name') or ''),
                    'rail':           node.get('name') or 'Trending Now',
                    'rail_position':  int(it.get('position') or 0) or None,
                    'episodes_count': obj.get('numberOfEpisodes'),
                    'introduction':   '',
                    'language':       'en',
                    'is_new':         False,
                })
    return out


def _rank_items(items: list[dict]) -> list[dict]:
    """Rank by rail priority (Trending Now first) then rail_position asc."""
    def _sort_key(x):
        try:
            priority = _RAIL_PRIORITY.index(x.get('rail') or '')
        except ValueError:
            priority = len(_RAIL_PRIORITY)
        return (priority, x.get('rail_position') or 999)

    # Dedupe by book_id, keep first sighting (highest priority)
    seen: dict[str, dict] = {}
    for it in sorted(items, key=_sort_key):
        bid = it.get('book_id')
        key = bid or it.get('title') or ''
        if key and key not in seen:
            seen[key] = it

    ordered = list(seen.values())
    ordered.sort(key=_sort_key)
    for i, x in enumerate(ordered):
        x['rank'] = i + 1
    return ordered[:40]


def fetch_live() -> list[dict]:
    html = _http_get(NETSHORT_HOMEPAGE)
    if not html:
        return []

    # Primary path: RSC stream decode
    decoded = _decode_rsc(html)
    raw_items = _extract_items(decoded)
    id_to_rail = _pair_items_to_groups(decoded, raw_items)

    shaped = []
    for i, obj in enumerate(raw_items):
        pid = str(obj.get('shortPlayId') or '')
        rail = id_to_rail.get(pid, '')
        shaped.append(_shape_item(obj, rail, rail_position=i + 1))

    # Fallback path: if RSC found nothing, fall back to ld+json ItemList
    if not shaped:
        logger.info('netshort: RSC extraction empty, falling back to ld+json')
        shaped = _shape_ld_items(html)

    if not shaped:
        return []

    ranked = _rank_items(shaped)
    logger.info('netshort: %d raw items across %d rails -> %d ranked',
                 len(shaped),
                 len({s.get('rail') for s in shaped if s.get('rail')}),
                 len(ranked))
    return ranked


# ------------------------------------------------------------------
# Curated fallback (day-zero only, if live returns nothing).
# ------------------------------------------------------------------
CURATED_BASELINE = [
    {'rank':  1, 'title': "Swapped to a Beggar But He is Apollo",
     'genre': 'Werewolf', 'episodes_count': 48, 'rail': 'Trending Now', 'themes': ['Fantasy']},
    {'rank':  2, 'title': "Horror Game: Seduce My Ghost Teacher or Die",
     'genre': 'Mafia',    'episodes_count': 52, 'rail': 'Trending Now', 'themes': ['Horror']},
    {'rank':  3, 'title': "Fly Away Without Goodbye",
     'genre': 'Second Chance', 'episodes_count': 65, 'rail': 'Trending Now', 'themes': ['Romance']},
    {'rank':  4, 'title': "Endless Desire Highway",
     'genre': 'CEO', 'episodes_count': 58, 'rail': 'Trending Now', 'themes': ['Romance']},
    {'rank':  5, 'title': "Don't Mess with the New Guard",
     'genre': 'Mafia', 'episodes_count': 62, 'rail': 'Trending Now', 'themes': ['Guard']},
    {'rank':  6, 'title': "The Billionaire's Wet Nurse",
     'genre': 'Billionaire', 'episodes_count': 71, 'rail': 'Trending Now', 'themes': ['Billionaire']},
    {'rank':  7, 'title': "I Am the Prey of Three Alphas",
     'genre': 'Werewolf', 'episodes_count': 55, 'rail': 'Trending Now', 'themes': ['Werewolf']},
    {'rank':  8, 'title': "Scrap-Heap Mech King",
     'genre': 'Mafia', 'episodes_count': 68, 'rail': 'Trending Now', 'themes': ['Mech']},
    {'rank':  9, 'title': "My Cold Werewolf CEO's Mind Is Drowning In Me",
     'genre': 'Werewolf', 'episodes_count': 82, 'rail': 'New Releases', 'themes': ['Werewolf', 'CEO']},
    {'rank': 10, 'title': "Banished from Olympus, I Became Her Contract Husband",
     'genre': 'Werewolf', 'episodes_count': 78, 'rail': 'New Releases', 'themes': ['Fantasy']},
]


def fetch_baseline() -> list[dict]:
    return [dict(t) for t in CURATED_BASELINE]


def fetch() -> dict:
    titles = fetch_live()
    if not titles:
        logger.info('netshort: live pull empty, using curated baseline')
        titles = fetch_baseline()
    return {
        'source': 'netshort',
        'label':  'NetShort',
        'kind':   'microdramas_competitor',
        'titles': titles,
    }


def _write_snapshot(payload: dict) -> None:
    try:
        import boto3  # type: ignore
    except ImportError:
        sys.exit("boto3 required.")

    bucket = os.environ.get('MICRODRAMAS_IQ_BUCKET', 'dashboard-inputs')
    now = datetime.now(timezone.utc)
    payload = dict(payload or {})
    payload.setdefault('source', 'netshort')
    payload['fetched_at'] = now.isoformat()

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')

    key_latest = 'microdramas_iq/snapshots/latest/netshort.json'
    s3.put_object(Bucket=bucket, Key=key_latest, Body=body,
                   ContentType='application/json',
                   CacheControl='public, max-age=60')
    print(f'  wrote s3://{bucket}/{key_latest} '
           f'({len(body)} bytes, {len(payload.get("titles") or [])} titles)')

    key_dated = f'microdramas_iq/snapshots/{now.strftime("%Y-%m-%d")}/netshort.json'
    s3.put_object(Bucket=bucket, Key=key_dated, Body=body,
                   ContentType='application/json')
    print(f'  wrote s3://{bucket}/{key_dated}')


def main() -> int:
    ap = argparse.ArgumentParser(description='NetShort microdramas scraper.')
    ap.add_argument('--seed', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')

    payload = ({'source': 'netshort', 'label': 'NetShort',
                'kind': 'microdramas_competitor',
                'titles': fetch_baseline(), 'seed': True}
                if args.seed else fetch())

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    _write_snapshot(payload)
    return 0


if __name__ == '__main__':
    sys.exit(main())
