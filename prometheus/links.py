"""Links and files are signals (2026-10-09, item 5).

eriley pasted a YouTube channel link, then wrote "but I gave you his
youtube channel link" when the next reply ignored it; a product URL
(https://kartel.ai/) in a campaign question was treated as noise. A URL
in an ask names something: a channel is the subject's identity on that
platform, a website is the brand or product the reader means, a clip
is the thing being measured. The thread carries every link forward.

  extract(text)            -> [{url, domain, kind, handle, label, slugs}]
  thread_links(history)    -> links from earlier user turns, newest first
  block(links)             -> prompt block for a generated read
  apply_to_draft(draft, links) -> brand-input signals + platform scope on
                              a build draft (never overwrites a subject
                              the reader named)

Pure functions, no network, no model call.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse, parse_qs

_URL_RX = re.compile(r"(?:https?://|www\.)[^\s<>()\"']+", re.I)
_TRAIL = '.,;:!?)\'"]}'
_TLDS_DROP = ('com', 'net', 'org', 'io', 'ai', 'co', 'tv', 'fm', 'app', 'us', 'uk', 'me', 'gg', 'ly', 'shop', 'store')
_SOCIAL = {
    'youtube.com': 'youtube', 'youtu.be': 'youtube', 'instagram.com': 'instagram', 'tiktok.com': 'tiktok',
    'x.com': 'x', 'twitter.com': 'x', 'facebook.com': 'facebook', 'fb.com': 'facebook', 'twitch.tv': 'twitch',
    'linkedin.com': 'linkedin', 'threads.net': 'threads', 'snapchat.com': 'snapchat', 'patreon.com': 'patreon',
    'substack.com': 'substack', 'kick.com': 'kick', 'rumble.com': 'rumble', 'pinterest.com': 'pinterest',
    'bsky.app': 'bluesky', 'spotify.com': 'spotify', 'open.spotify.com': 'spotify', 'podcasts.apple.com': 'apple podcasts',
}
_GENERIC_PATHS = ('p', 'reel', 'reels', 'watch', 'shorts', 'video', 'status', 'posts', 'photo', 'channel', 'c', 'user',
                  'embed', 'live', 'playlist', 'results', 'search', 'explore', 'home', 'feed', 'hashtag', 'tag')


def _strip_www(host):
    host = str(host or '').lower()
    return host[4:] if host.startswith('www.') else host


def _brand_from_domain(domain):
    parts = [p for p in str(domain or '').lower().split('.') if p]
    while parts and parts[-1] in _TLDS_DROP:
        parts.pop()
    core = parts[-1] if parts else ''
    core = re.sub(r'[^a-z0-9]+', ' ', core).strip()
    return ' '.join(w[:1].upper() + w[1:] for w in core.split()) if core else ''


def _one(raw):
    u = raw.rstrip(_TRAIL)
    full = u if u.lower().startswith('http') else 'https://' + u
    try:
        p = urlparse(full)
    except Exception:
        return None
    host = _strip_www(p.netloc.split('@')[-1].split(':')[0])
    if not host or '.' not in host:
        return None
    segs = [s for s in p.path.split('/') if s]
    platform = _SOCIAL.get(host) or _SOCIAL.get('.'.join(host.split('.')[-2:]))
    handle, kind, label = '', 'website', ''
    if platform:
        if platform == 'youtube':
            if host == 'youtu.be' or (segs and segs[0] in ('watch', 'shorts', 'embed', 'live')) or 'v' in parse_qs(p.query):
                kind = 'video'
                vid = (parse_qs(p.query).get('v') or [segs[-1] if segs else ''])[0]
                label, handle = f'YouTube video {vid}'.strip(), vid
            elif segs and segs[0].startswith('@'):
                kind, handle = 'channel', segs[0][1:]
            elif len(segs) >= 2 and segs[0] in ('c', 'user', 'channel'):
                kind, handle = 'channel', segs[1]
            else:
                kind = 'channel' if segs else 'website'
                handle = segs[0] if segs else ''
            if kind == 'channel':
                label = f'@{handle}' if handle else 'YouTube'
        else:
            if not segs:
                kind, label = 'website', platform
            elif segs[0].lstrip('@').lower() in _GENERIC_PATHS:
                kind, label = 'post', f'{platform} post'      # instagram.com/p/<id>, /reel/<id>
            else:
                handle = segs[0].lstrip('@')
                if len(segs) == 1:
                    kind, label = 'social_profile', f'@{handle}'
                else:
                    kind, label = 'post', f'{platform} post by @{handle}'   # x.com/<handle>/status/<id>
        platform_name = platform
    else:
        platform_name = ''
        label = _brand_from_domain(host)
    slugs = []
    if kind == 'channel' and handle:
        slugs = [f'youtube.com/@{handle}', handle]
    elif kind == 'social_profile' and handle:
        slugs = [f"{host}/{'@' if host.endswith('tiktok.com') else ''}{handle}", handle]
    elif kind == 'website':
        slugs = [host] + ([_brand_from_domain(host).lower()] if _brand_from_domain(host) else [])
    elif kind in ('video', 'post'):
        slugs = [host + p.path + (f"?v={handle}" if kind == 'video' and 'v' in parse_qs(p.query) else '')]
    return {'url': full, 'domain': host, 'kind': kind, 'platform': platform_name, 'handle': handle,
            'label': label or host, 'slugs': [s for s in slugs if s]}


def extract(text):
    out, seen = [], set()
    try:
        for m in _URL_RX.finditer(str(text or '')):
            link = _one(m.group(0))
            if link and link['url'].lower() not in seen:
                seen.add(link['url'].lower())
                out.append(link)
    except Exception:
        return out
    return out


def thread_links(history, limit=12):
    """Links from earlier user turns, newest first, deduped."""
    out, seen = [], set()
    for h in reversed([h for h in (history or []) if isinstance(h, dict)]):
        if str(h.get('role') or '').lower() != 'user':
            continue
        for link in extract(str(h.get('text') or h.get('content') or '')):
            if link['url'].lower() in seen:
                continue
            seen.add(link['url'].lower())
            out.append(dict(link, from_thread=True))
            if len(out) >= limit:
                return out
    return out


def merge(current, earlier):
    seen, out = set(), []
    for link in list(current or []) + list(earlier or []):
        if link['url'].lower() in seen:
            continue
        seen.add(link['url'].lower())
        out.append(link)
    return out


def describe(link):
    k = link.get('kind')
    if k == 'channel':
        return f"{link['url']} is the YouTube channel {link['label']}: the subject's identity on YouTube"
    if k == 'social_profile':
        return f"{link['url']} is the {link['platform']} account {link['label']}: the subject's identity on {link['platform']}"
    if k == 'video':
        return f"{link['url']} is a YouTube video: the clip being asked about"
    if k == 'post':
        return f"{link['url']} is a {link['platform']} post: the clip or post being asked about"
    return f"{link['url']} is the website of {link['label']}: the brand or product the reader means"


def block(links):
    """Prompt block for a generated read. '' when there are no links."""
    links = [l for l in (links or []) if l]
    if not links:
        return ''
    lines = ['LINKS THE READER GAVE', '=====================',
             'Every link names something; none is noise. A website is the brand or product the reader',
             'means (a campaign for it runs against the open audience); a channel or account is the',
             'subject\'s identity on that platform; a video or post is the clip being measured. Use the',
             'name, never the raw address, in the reply.']
    for l in links[:8]:
        tail = ' (from earlier in this conversation)' if l.get('from_thread') else ''
        lines.append(f"- {describe(l)}{tail}")
    return '\n'.join(lines)


def apply_to_draft(draft, links):
    """Brand-input signals and platform scope from the links, onto a
    build draft. Never overwrites a subject the reader named; a website
    becomes the subject only when the draft has none. Returns the
    number of signals added."""
    if not isinstance(draft, dict) or not links:
        return 0
    added = 0
    sig = draft.get('clickstream_signals')
    if not isinstance(sig, list):
        sig = []
    have = {(str(s.get('host') or '').lower(), str(s.get('path_pattern') or s.get('path') or '')) for s in sig if isinstance(s, dict)}
    scope = set(draft.get('platform_scope') or []) if isinstance(draft.get('platform_scope'), list) else set()
    for l in links:
        host = l['domain']
        if l['kind'] == 'channel' and l['handle']:
            path = f"/@{l['handle']}"
        elif l['kind'] == 'social_profile' and l['handle']:
            path = f"/{l['handle']}"
        elif l['kind'] in ('video', 'post'):
            path = urlparse(l['url']).path or '/'
        else:
            path = '/'
        if (host.lower(), path) not in have:
            sig.append({'host': host, 'path_pattern': path, 'param_hint': '',
                        'evidence': 'link the reader gave in the ask'})
            have.add((host.lower(), path))
            added += 1
        if l['kind'] in ('channel', 'social_profile', 'video', 'post') and l.get('platform'):
            scope.add(l['platform'])
        if l['kind'] == 'website' and not str(draft.get('subject') or '').strip() and l['label']:
            draft['subject'] = l['label']
            draft['link_subject'] = True
    if added:
        draft['clickstream_signals'] = sig
    if scope:
        draft['platform_scope'] = sorted(scope)
    handles = [l['handle'] for l in links if l.get('handle') and l['kind'] in ('channel', 'social_profile')]
    if handles:
        draft['link_handles'] = sorted(set(handles))
    return added
