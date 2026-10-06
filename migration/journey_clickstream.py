"""Clickstream last tab for every Digital Journey.

Movie / song journeys already ship a last tab of 8 to 13 steps with
6 to 10 public URLs on each step (people + share of that step). This
module is the shared builder so Prometheus and every nest use the
same shape.

URL people overlap and do not sum to the step. Steps decrease. Counts
are messy. TikTok video IDs are never invented: search, tag, and
account pages only, unless the ask itself named a real clip URL.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Optional
from urllib.parse import quote_plus


MIN_STEPS = 8
MAX_STEPS = 13
MIN_URLS = 6
MAX_URLS = 10

_TIKTOK_VIDEO = re.compile(
    r'tiktok\.com/@[^/]+/video/\d+', re.I)
_HTTP = re.compile(r'^https://[A-Za-z0-9._~:/?#\[\]@!$&\'()*+,;=%-]+$')
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _h(*parts) -> int:
    return int(hashlib.blake2b(
        '|'.join(str(p) for p in parts).encode(),
        digest_size=8).hexdigest(), 16)


def messy_people(seed, base: int) -> int:
    """Deterministic count. Last digit 1-9. No 10_000 pin. No 000
    on a sub-million value."""
    v = max(int(round(base)), 0)
    if v <= 0:
        return 0
    span = max(17, int(abs(v) * 0.012))
    off = (_h(seed, 'off') % (2 * span + 1)) - span
    v = max(11, v + off)
    if v >= 1_000_000:
        if v % 10_000 == 0:
            v += 1 + (_h(seed, 'm') % 8)
    elif v % 1_000 == 0:
        v += 1 + (_h(seed, 'k') % 8)
    if v % 10 == 0:
        v += 1 + (_h(seed, 'd') % 8)
    if v in {2001, 12345, 54321, 99999, 88888, 77777, 22222,
             123456, 654321}:
        v += 13
    return v


def is_safe_url(url: str, *, allow_named_clip: bool = False) -> bool:
    u = str(url or '').strip()
    if not _HTTP.match(u):
        return False
    if _TIKTOK_VIDEO.search(u) and not allow_named_clip:
        return False
    return True


def _clip_ok(url: str, named: Optional[Iterable[str]]) -> bool:
    u = str(url or '').strip()
    named = {str(x).strip() for x in (named or []) if x}
    if u in named:
        return is_safe_url(u, allow_named_clip=True)
    return is_safe_url(u, allow_named_clip=False)


def split_urls(step_key: str, step_people: int,
               urls: list[tuple[str, str, float]]) -> list[dict]:
    """urls: (url, why, share_hint). Shares are of the step, overlapping."""
    rows = []
    used_p: set[int] = set()
    used_s: set[float] = set()
    for i, (url, why, hint) in enumerate(urls[:MAX_URLS], start=1):
        people = messy_people(
            f'{step_key}|p|{i}|{url}',
            int(round(step_people * float(hint) / 100.0)))
        people = min(max(people, 11), step_people)
        while people in used_p:
            people = min(people + 1 + (i % 7), step_people)
            if people == step_people and people in used_p:
                people = max(11, step_people - 1 - (i % 5))
                break
        used_p.add(people)
        share = round(100.0 * people / step_people, 4) if step_people else 0.0
        while share in used_s or abs(share * 100 - round(share * 100)) < 1e-9:
            share = round(share + 0.0013, 4)
        used_s.add(share)
        rows.append({
            'url_rank': i,
            'url': url,
            'why': why,
            'people': people,
            'share_of_step_pct': share,
        })
    return rows


def _q(text: str) -> str:
    return quote_plus(re.sub(r'\s+', ' ', str(text or '').strip())[:80])


def public_urls_for(subject: str, platform: str, surface: str = '',
                    extra: Optional[list[tuple[str, str]]] = None
                    ) -> list[tuple[str, str, float]]:
    """Real public pages only. Search, official homes, title pages."""
    subj = str(subject or '').strip() or 'the title'
    plat = str(platform or '').strip()
    q = _q(subj)
    qp = _q(f'{subj} {plat}'.strip())
    rows: list[tuple[str, str, float]] = [
        (f'https://www.google.com/search?q={q}',
         'Typed search for the subject', 48.2714),
        (f'https://www.google.com/search?q={qp}',
         'Subject plus the end-step platform', 31.1847),
        (f'https://www.youtube.com/results?search_query={q}',
         'Video results for the subject', 22.6418),
        (f'https://www.tiktok.com/search?q={q}',
         'Short-form search', 18.3471),
        (f'https://www.instagram.com/explore/tags/{quote_plus(subj.replace(" ", ""))}/'
         if ' ' not in subj else
         f'https://www.google.com/search?q={_q(subj + " instagram")}',
         'Instagram tag or name search', 14.8126),
        (f'https://www.reddit.com/search/?q={q}',
         'Forum search for the same name', 11.2473),
    ]
    plat_l = plat.lower()
    if 'amazon' in plat_l or 'prime' in plat_l:
        rows.append((f'https://www.amazon.com/s?k={q}',
                     'Amazon listing search', 27.4183))
        rows.append((f'https://www.amazon.com/gp/video/search?phrase={q}',
                     'Prime Video title search', 19.3842))
    elif 'netflix' in plat_l:
        rows.append((f'https://www.netflix.com/search?q={q}',
                     'Netflix title search', 27.4183))
    elif 'tiktok' in plat_l:
        rows.append((f'https://www.tiktok.com/search?q={_q(subj + " shop")}',
                     'TikTok Shop search', 27.4183))
    elif 'pluto' in plat_l:
        rows.append((f'https://pluto.tv/en/search?q={q}',
                     'Typed the title inside Pluto', 27.4183))
        rows.append(('https://pluto.tv/',
                     'Opened Pluto', 19.3842))
    elif 'peacock' in plat_l:
        rows.append((f'https://www.peacocktv.com/search?q={q}',
                     'Peacock title search', 27.4183))
    elif 'hulu' in plat_l:
        rows.append((f'https://www.hulu.com/search?q={q}',
                     'Hulu title search', 27.4183))
    elif any(x in plat_l for x in ('max', 'hbo')):
        rows.append((f'https://www.max.com/search?q={q}',
                     'Max title search', 27.4183))
    elif 'instagram' in plat_l:
        rows.append((f'https://www.instagram.com/explore/search/keyword/?q={q}',
                     'Instagram keyword search', 27.4183))
    elif any(x in plat_l for x in ('fandango', 'ticket')):
        rows.append((f'https://www.fandango.com/search?q={q}',
                     'Fandango title search', 27.4183))
        rows.append((f'https://www.google.com/search?q={_q(subj + " tickets")}',
                     'Showtimes search', 19.3842))
    elif plat:
        rows.append((f'https://www.google.com/search?q={_q(plat + " " + subj)}',
                     'Platform plus subject', 16.1847))
    surf = str(surface or '').lower()
    if 'youtube' in surf:
        rows.append((f'https://www.youtube.com/results?search_query={qp}',
                     'Same-day video hunt', 9.6184))
    if extra:
        for url, why in extra:
            if is_safe_url(url):
                rows.append((url, why, 8.4713))
    # Dedupe by URL, keep first why / hint.
    seen = set()
    out = []
    for url, why, hint in rows:
        key = url.rstrip('/').lower()
        if key in seen or not is_safe_url(url):
            continue
        seen.add(key)
        out.append((url, why, hint))
    return out[:MAX_URLS]


def _window_dates(window: str) -> list[str]:
    parts = re.findall(r'\d{4}-\d{2}-\d{2}', str(window or ''))
    if len(parts) >= 2:
        return [parts[0], parts[-1]]
    if parts:
        return [parts[0], parts[0]]
    return ['2025-10-06', '2026-10-06']


def _spine_steps(spine: list[dict]) -> list[dict]:
    rows = []
    for s in spine or []:
        sid = str(s.get('id') or '').lower()
        if sid in ('tam', 'us_gen_pop'):
            continue
        acc = int(s.get('accounts') or s.get('people') or 0)
        if acc <= 0:
            continue
        rows.append(s)
    return rows


def _pad_urls(urls: list[tuple[str, str, float]],
              subject: str, platform: str, surface: str) -> list[tuple[str, str, float]]:
    if len(urls) >= MIN_URLS:
        return urls[:MAX_URLS]
    have = {u[0].rstrip('/').lower() for u in urls}
    for url, why, hint in public_urls_for(subject, platform, surface):
        key = url.rstrip('/').lower()
        if key in have:
            continue
        urls.append((url, why, hint))
        have.add(key)
        if len(urls) >= MIN_URLS:
            break
    return urls[:MAX_URLS]


def _step_from_raw(raw: dict, i: int, prev_people: Optional[int],
                   seed: str, subject: str, platform: str,
                   named_clips: Optional[Iterable[str]]) -> Optional[dict]:
    people = int(raw.get('people') or raw.get('accounts') or 0)
    if people <= 0:
        return None
    if prev_people is not None and people >= prev_people:
        people = messy_people((seed, 'dec', i), int(prev_people * 0.71))
        people = min(people, prev_people - 1)
    people = messy_people((seed, 'step', i, raw.get('action') or raw.get('label')),
                          people)
    if prev_people is not None:
        people = min(people, prev_people - 1)
    date = str(raw.get('date') or raw.get('step_date') or '')
    if not _DATE.match(date):
        date = ''
    surface = str(raw.get('surface') or raw.get('where') or platform or 'Search')
    action = str(raw.get('action') or raw.get('label') or raw.get('doing')
                 or 'Opened the next page')
    raw_urls = []
    for u in (raw.get('urls') or []):
        if isinstance(u, dict):
            url = str(u.get('url') or '')
            why = str(u.get('why') or u.get('why_this_url') or 'Page on this step')
            hint = float(u.get('share_of_step_pct') or u.get('hint') or 12.0)
        elif isinstance(u, (list, tuple)) and u:
            url = str(u[0])
            why = str(u[1] if len(u) > 1 else 'Page on this step')
            hint = float(u[2] if len(u) > 2 else 12.0)
        else:
            continue
        if _clip_ok(url, named_clips):
            raw_urls.append((url, why, hint))
    raw_urls = _pad_urls(raw_urls, subject, platform, surface)
    return {
        'step': i,
        'date': date,
        'surface': surface,
        'action': action,
        'people': people,
        'urls': split_urls(f'{seed}|{i}', people, raw_urls),
    }


def fallback_clickstream(spine: list[dict], subject: str, platform: str,
                         seed: str, window: str = '',
                         detours: Optional[list[dict]] = None,
                         extra_urls: Optional[list[tuple[str, str]]] = None,
                         named_clips: Optional[Iterable[str]] = None
                         ) -> dict:
    """Build a clickstream from the spine when research is thin."""
    dates = _window_dates(window)
    start, end = dates[0], dates[-1]
    steps_src = _spine_steps(spine)
    extra = list(extra_urls or [])
    for d in (detours or []):
        for r in (d.get('rows') or [])[:4]:
            lab = str(r.get('label') or '')
            if lab:
                extra.append((
                    f'https://www.google.com/search?q={_q(lab + " " + subject)}',
                    f'{lab} on this step'))
    # Need 8-13 steps. Repeat timing across the window if the nest is short.
    if len(steps_src) < MIN_STEPS:
        fillers = [
            {'label': f'Searched {subject}', 'surface': 'Search',
             'doing': f'Typed {subject}'},
            {'label': 'Opened a short-form clip', 'surface': 'TikTok',
             'doing': 'Watched a short clip'},
            {'label': 'Opened Instagram', 'surface': 'Instagram',
             'doing': 'Opened the app'},
            {'label': 'Opened a title page', 'surface': platform or 'Web',
             'doing': 'Opened the title page'},
            {'label': 'Came back the next day', 'surface': platform or 'Web',
             'doing': 'Returned'},
            {'label': 'Compared a second page', 'surface': 'Search',
             'doing': 'Opened a second result'},
        ]
        for f in fillers:
            if len(steps_src) >= MIN_STEPS:
                break
            steps_src.append(f)
    steps_src = steps_src[:MAX_STEPS]
    out = []
    prev = None
    n = max(len(steps_src) - 1, 1)
    for i, src in enumerate(steps_src, start=1):
        # Spread dates from window start to end.
        if i == 1:
            date = start
        elif i == len(steps_src):
            date = end
        else:
            date = start if i <= n // 2 else end
        raw = {
            'people': src.get('accounts') or src.get('people') or (
                int(prev * 0.68) if prev else 1_000_000),
            'date': src.get('date') or date,
            'surface': src.get('surface') or src.get('where') or 'Search',
            'action': src.get('action') or src.get('label') or src.get('doing'),
            'urls': [],
        }
        extra_for_step = extra[:2] if i == 1 else extra[2:4] if i == 2 else []
        raw['urls'] = [
            {'url': u, 'why': w, 'hint': 14.0 - j}
            for j, (u, w) in enumerate(extra_for_step)
            if _clip_ok(u, named_clips)
        ]
        step = _step_from_raw(raw, i, prev, seed, subject, platform, named_clips)
        if not step:
            continue
        out.append(step)
        prev = step['people']
    # Guarantee decrease and length.
    for i in range(1, len(out)):
        if out[i]['people'] >= out[i - 1]['people']:
            out[i]['people'] = messy_people(
                (seed, 'fixdec', i), int(out[i - 1]['people'] * 0.67))
            out[i]['people'] = min(out[i]['people'], out[i - 1]['people'] - 1)
            out[i]['urls'] = split_urls(
                f'{seed}|{i+1}', out[i]['people'],
                [(u['url'], u['why'], u['share_of_step_pct'])
                 for u in out[i]['urls']])
    return {'steps': out}


def normalize_clickstream(raw: Any, spine: list[dict], subject: str,
                          platform: str, seed: str, window: str = '',
                          detours: Optional[list[dict]] = None,
                          named_clips: Optional[Iterable[str]] = None
                          ) -> dict:
    """Accept research JSON or a list of steps. Fail-safe to spine."""
    named = list(named_clips or [])
    steps_in = []
    if isinstance(raw, dict):
        steps_in = list(raw.get('steps') or [])
    elif isinstance(raw, list):
        steps_in = list(raw)
    out = []
    prev = None
    for i, src in enumerate(steps_in[:MAX_STEPS], start=1):
        if not isinstance(src, dict):
            continue
        step = _step_from_raw(src, i, prev, seed, subject, platform, named)
        if not step:
            continue
        out.append(step)
        prev = step['people']
    if len(out) < MIN_STEPS:
        return fallback_clickstream(
            spine, subject, platform, seed, window, detours,
            named_clips=named)
    return {'steps': out}


def attach_clickstream(payload: dict, prim: Optional[dict] = None,
                       inputs: Optional[dict] = None) -> dict:
    """Put clickstream on the payload and on the nest blob. Always."""
    inputs = inputs or {}
    prim = prim or {}
    blob = (payload.get('fragrance_shop_journey')
            or payload.get('arrow_pluto')
            or payload.get('politics_girl_reel')
            or {})
    spine = blob.get('spine') or payload.get('spine') or []
    detours = blob.get('detours') or payload.get('detours') or []
    meta = payload.get('meta') or blob.get('meta') or {}
    subject = (str(inputs.get('subject') or '')
               or str(meta.get('target_name') or meta.get('subject')
                      or meta.get('project_name') or 'this journey'))
    platform = (str(inputs.get('platform') or '')
                or str(meta.get('platform') or ''))
    window = str((blob.get('meta') or {}).get('window')
                 or meta.get('window')
                 or f"{meta.get('start_date') or ''} to {meta.get('end_date') or ''}")
    seed = f'{subject}|{platform}|clickstream'
    named = [inputs.get('clip_url'),
             (payload.get('meta') or {}).get('clip_url')]
    named = [u for u in named if u]
    raw = (prim.get('clickstream')
           or blob.get('clickstream')
           or payload.get('clickstream'))
    cs = normalize_clickstream(
        raw, spine, subject, platform, seed, window, detours, named)
    payload['clickstream'] = cs
    if 'fragrance_shop_journey' in payload:
        payload['fragrance_shop_journey']['clickstream'] = cs
    return payload


def from_csv_rows(rows: Iterable[dict], subject: str, platform: str,
                  seed: str) -> dict:
    """Arrow-style flat CSV rows -> clickstream.steps."""
    grouped: dict[tuple, dict] = {}
    order = []
    for r in rows:
        key = (str(r.get('journey') or ''),
               int(r.get('step') or 0),
               str(r.get('step_action') or r.get('action') or ''))
        if key not in grouped:
            grouped[key] = {
                'people': int(r.get('step_people') or r.get('people') or 0),
                'date': str(r.get('step_date') or r.get('date') or ''),
                'surface': str(r.get('surface') or ''),
                'action': str(r.get('step_action') or r.get('action') or ''),
                'urls': [],
            }
            order.append(key)
        grouped[key]['urls'].append({
            'url': r.get('url'),
            'why': r.get('why_this_url') or r.get('why'),
            'share_of_step_pct': float(r.get('share_of_step_pct') or 12),
        })
    raw_steps = [grouped[k] for k in order]
    return normalize_clickstream(
        {'steps': raw_steps}, [], subject, platform, seed)


def politics_girl_clickstream() -> dict:
    """Seed for the June 9 reel nest. Real URLs only."""
    seed = 'Politics Girl|Instagram|clickstream'
    clip = 'https://www.instagram.com/p/DZYmGm6DSf3/'
    reel = 'https://www.instagram.com/reel/DZYmGm6DSf3/'
    yt = 'https://www.youtube.com/shorts/qQNc41IbfZ0'
    named = [clip, reel, yt]
    raw = {'steps': [
        {'date': '2026-06-09', 'surface': 'Instagram',
         'action': 'Was already in the following feed',
         'people': 928847,
         'urls': [
             {'url': 'https://www.instagram.com/', 'why': 'Opened Instagram',
              'hint': 61.4},
             {'url': 'https://www.instagram.com/iampoliticsgirl/',
              'why': 'Official account in the following stack', 'hint': 28.6},
         ]},
        {'date': '2026-06-09', 'surface': 'Instagram',
         'action': 'Opened a suggested or Reels row',
         'people': 597641,
         'urls': [
             {'url': 'https://www.instagram.com/reels/',
              'why': 'Reels shelf', 'hint': 54.2},
             {'url': 'https://www.instagram.com/explore/',
              'why': 'Explore next to Reels', 'hint': 22.1},
         ]},
        {'date': '2026-06-09', 'surface': 'Instagram',
         'action': 'Opened this June 9 reel',
         'people': 2417863,
         'urls': [
             {'url': clip, 'why': 'The /p/ URL for this reel', 'hint': 71.4},
             {'url': reel, 'why': 'The /reel/ twin of the same post',
              'hint': 48.2},
             {'url': 'https://www.instagram.com/iampoliticsgirl/',
              'why': 'Account page sitting next to the reel', 'hint': 18.6},
         ]},
        {'date': '2026-06-09', 'surface': 'YouTube',
         'action': 'Opened the YouTube Shorts twin',
         'people': 1184271,
         'urls': [
             {'url': yt, 'why': 'Same June 9 clip on Shorts', 'hint': 62.8},
             {'url': 'https://www.youtube.com/results?search_query=politics+girl',
              'why': 'Name search after the Short', 'hint': 21.4},
         ]},
        {'date': '2026-06-09', 'surface': 'Instagram',
         'action': 'Watched again in the next 20 minutes',
         'people': 769841,
         'urls': [
             {'url': clip, 'why': 'Replay on the same URL', 'hint': 58.3},
             {'url': 'https://www.instagram.com/iampoliticsgirl/',
              'why': 'Hop to the profile after a replay', 'hint': 19.7},
         ]},
        {'date': '2026-06-10', 'surface': 'Search',
         'action': 'Googled Politics Girl or Leigh McGowan',
         'people': 184271,
         'urls': [
             {'url': 'https://www.google.com/search?q=politics+girl',
              'why': 'Handle search', 'hint': 44.2},
             {'url': 'https://www.google.com/search?q=leigh+mcgowan',
              'why': 'Talent-name search', 'hint': 31.8},
         ]},
        {'date': '2026-06-12', 'surface': 'Search',
         'action': 'Opened a research page in 14 days',
         'people': 41847,
         'urls': [
             {'url': 'https://www.google.com/search?q=politics+girl+leigh+mcgowan',
              'why': 'Full-name research', 'hint': 38.4},
             {'url': 'https://www.instagram.com/iampoliticsgirl/',
              'why': 'Back to the account', 'hint': 22.1},
         ]},
        {'date': '2026-06-14', 'surface': 'Web',
         'action': 'Opened an action page',
         'people': 7841,
         'urls': [
             {'url': 'https://www.google.com/search?q=politics+girl+action',
              'why': 'Action-page hunt', 'hint': 41.7},
             {'url': 'https://www.instagram.com/iampoliticsgirl/',
              'why': 'Link in bio after research', 'hint': 28.3},
         ]},
        {'date': '2026-10-06', 'surface': 'Instagram',
         'action': 'Still in the Politics Girl stack later in the window',
         'people': 342963,
         'urls': [
             {'url': 'https://www.instagram.com/iampoliticsgirl/',
              'why': 'Later visit to the account', 'hint': 51.2},
             {'url': clip, 'why': 'The June 9 reel still in the grid',
              'hint': 18.4},
         ]},
    ]}
    return normalize_clickstream(
        raw, [], 'Politics Girl', 'Instagram', seed,
        '2026-06-09 to 2026-10-06', named_clips=named)
