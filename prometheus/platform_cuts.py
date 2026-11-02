"""One title asked across streaming services is ONE build.

Jay Patel (Amazon), 2026-11-02:

    can you make three distinct profiles
    1/ customers who watched "Wuthering Heights" on a _any_ streaming service?
    2/ customers who watched it on Amazon Prime Video?
    3/ customers who watched it on HBO Max?
    talk me through how you will build these 3 reports, including any
    relevant queries/filters

The interpret step read that as three builds, the model wrote three
full drafts, ran past its output ceiling twice (181 seconds) and the
turn died on the calm line. Jenna: "this would just be a wuthering
heights profile with two cuts based on streaming service ie so one
would be 100% in HBO max and one would be 100% APV."

So: the title's viewers on any service are the Total Universe (with
the Avid tier), and every service the reader named is a behavioral
add-on cut off that universe, pinned to 100 on the service (the
add-on cut engine's defining-brand pin). The model is handed the one
sentence that describes the universe; the cuts ride the draft
deterministically. A "talk me through how you build it" question in
the same message is answered in the brief itself (method_note), in the
words Jenna wrote for Jay.

Pure text in, dicts out. Never raises into a build.
"""
from __future__ import annotations

import re

# Service -> the name the grid carries (hostmap / Gen Pop spelling).
# The keys are lower-case alias folds.
PLATFORM_ALIASES = {
    'amazon prime video': 'Amazon Prime Video', 'prime video': 'Amazon Prime Video',
    'amazon prime': 'Amazon Prime Video', 'apv': 'Amazon Prime Video', 'amazon video': 'Amazon Prime Video',
    'amazon': 'Amazon Prime Video', 'primevideo': 'Amazon Prime Video',
    'hbo max': 'HBO Max', 'hbomax': 'HBO Max', 'max': 'HBO Max', 'hbo': 'HBO Max',
    'netflix': 'Netflix', 'hulu': 'Hulu', 'disney+': 'Disney+', 'disney plus': 'Disney+', 'disney': 'Disney+',
    'apple tv+': 'Apple TV+', 'apple tv plus': 'Apple TV+', 'apple tv': 'Apple TV+', 'appletv': 'Apple TV+',
    'peacock': 'Peacock', 'paramount+': 'Paramount+', 'paramount plus': 'Paramount+', 'paramount': 'Paramount+',
    'tubi': 'Tubi', 'pluto tv': 'Pluto TV', 'pluto': 'Pluto TV', 'the roku channel': 'The Roku Channel',
    'roku channel': 'The Roku Channel', 'roku': 'The Roku Channel', 'youtube': 'YouTube', 'youtube tv': 'YouTube TV',
    'starz': 'Starz', 'mgm+': 'MGM+', 'mgm plus': 'MGM+', 'britbox': 'BritBox', 'crunchyroll': 'Crunchyroll',
    'fandango at home': 'Fandango at Home', 'vudu': 'Fandango at Home', 'google play': 'Google Play',
    'amc+': 'AMC+', 'amc plus': 'AMC+', 'shudder': 'Shudder', 'discovery+': 'Discovery+', 'espn+': 'ESPN+',
    'spotify': 'Spotify', 'apple music': 'Apple Music', 'amazon music': 'Amazon Music', 'pandora': 'Pandora',
    'siriusxm': 'SiriusXM', 'audible': 'Audible', 'kindle': 'Kindle', 'twitch': 'Twitch', 'kick': 'Kick',
}

_ANY_RX = re.compile(
    r"^\W*(?:on\s+)?(?:a\s+|an\s+)?(?:any|all|every|whatever|whichever)\s+"
    r"(?:streaming\s+)?(?:service|services|platform|platforms|streamer|streamers|app|apps|channel|channels|store|stores)?s?\b"
    r"|^\W*(?:anywhere|any\s*where|everywhere|wherever|regardless of (?:the )?(?:service|platform))\b", re.I)

_VERBS = r"(?:watched|streamed|saw|seen|viewed|played|listened to|heard|read|rented|bought|purchased|binged|caught|finished)"
_NOUNS = r"(?:customers?|people|viewers?|users?|subscribers?|audiences?|those|anyone|folks|households?|accounts?|fans?|members?|persons?|individuals?|consumers?|watchers?|streamers?)"

# one item: "<noun> who watched <title> on <service>"
_ITEM_RX = re.compile(
    r"(?:(?:the\s+)?" + _NOUNS + r"\s+(?:who|that|which)\s+(?:have\s+|had\s+|'ve\s+)?|(?:a\s+)?profile\s+(?:of|for|on)\s+(?:the\s+)?(?:" + _NOUNS + r"\s+(?:who|that)\s+(?:have\s+|had\s+)?)?)?"
    r"(?P<verb>" + _VERBS + r")\s+"
    r"(?P<title>\"[^\"]{1,80}\"|'[^']{1,80}'|\u201c[^\u201d]{1,80}\u201d|it|the\s+(?:film|movie|show|series|title|same(?:\s+(?:film|movie|show|series|title))?)|[^,;?!\n]{1,80}?)"
    r"\s+(?:on|via|through|using|with)\s+(?P<plat>[^,;?!\n]{2,60}?)(?:\s*[,(]\s*[^\n]*)?\s*[?.!]*\s*$", re.I)

# the audience noun first: "viewers of The Bear on Hulu", "The Bear viewers on Hulu"
_OF_RX = re.compile(
    r"(?P<noun>viewers|watchers|streamers|listeners|readers|players|audience|fans|subscribers)\s+(?:of|for)\s+"
    r"(?P<title>\"[^\"]{1,80}\"|'[^']{1,80}'|\u201c[^\u201d]{1,80}\u201d|it|the\s+(?:film|movie|show|series|title)|[^,;?!\n]{1,80}?)"
    r"\s+(?:on|via|through)\s+(?P<plat>[^,;?!\n]{2,60}?)(?:\s*[,(]\s*[^\n]*)?\s*[?.!]*\s*$", re.I)
_NOUN_VERB = {'viewers': 'watched', 'watchers': 'watched', 'streamers': 'streamed', 'listeners': 'listened to',
              'readers': 'read', 'players': 'played', 'audience': 'watched', 'fans': 'watched', 'subscribers': 'watched'}

_ITEM_SPLIT_RX = re.compile(
    r"(?:^|\n)\s*(?:\(?\d{1,2}[/.)\]:-]|[-*\u2022\u2013]|\(?[a-z][/.)])\s+", re.M)

_METHOD_RX = re.compile(
    r"\b(?:talk|walk)\s+me\s+through\b|\bhow\s+(?:will|would|do|did|does)\s+(?:you|prometheus|it)\s+(?:build|construct|create|make|put together|assemble|pull|run|define|derive)\b"
    r"|\bexplain\s+(?:to me\s+)?(?:how|the\s+(?:method|methodology|approach|process|logic))\b|\b(?:method|methodology|approach|logic|process)\s+(?:you|behind|used|for|of)\b"
    r"|\b(?:queries|filters|query|filter|criteria|definitions?|rules)\s+(?:you(?:'ll| will)?\s+)?(?:use|used|apply|applied)\b|\bincluding\s+(?:any\s+)?(?:relevant\s+)?(?:queries|filters)\b"
    r"|\bhow\s+(?:is|are)\s+(?:this|these|the|each|it)\s+(?:profile|report|audience|cut|universe)s?\s+(?:built|constructed|defined|derived|made|put together)\b", re.I)

_LEAD_COUNT_RX = re.compile(
    r"\b(?:make|build|run|create|pull|give me|i need|i want|can (?:you|we) (?:make|build|run|create|pull|do))\s+(?:me\s+)?(?:(\d{1,2}|two|three|four|five|six)\s+)?(?:distinct|separate|different|individual)?\s*(?:profiles?|reports?|audiences?|builds?|cuts?)\b", re.I)


def asks_method(text):
    """True when the words ask how the build is done ("talk me through
    how you will build these", "what filters do you use")."""
    return bool(_METHOD_RX.search(str(text or '')))


def _canon_platform(raw):
    s = ' '.join(str(raw or '').replace('_', ' ').replace('*', ' ').split()).strip(' .,;:')
    low = re.sub(r"^(?:the|a|an)\s+", '', s.lower()).strip()
    low = re.sub(r"\s+(?:app|service|platform|streaming service|channel|store)$", '', low).strip()
    if low in PLATFORM_ALIASES:
        return PLATFORM_ALIASES[low]
    squashed = re.sub(r"[^a-z0-9+]+", '', low)
    for k, v in PLATFORM_ALIASES.items():
        if re.sub(r"[^a-z0-9+]+", '', k) == squashed:
            return v
    return ''


def _clean_title(raw):
    t = ' '.join(str(raw or '').split()).strip()
    t = t.strip('"\'\u201c\u201d').strip()
    t = re.sub(r"^(?:the\s+)?(?:film|movie|show|series|title)\s+", '', t, flags=re.I).strip()
    t = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", '', t).strip()
    return t


def _is_pointer(raw):
    low = ' '.join(str(raw or '').lower().split()).strip('"\'')
    return low in ('it', 'the film', 'the movie', 'the show', 'the series', 'the title', 'the same',
                   'the same film', 'the same movie', 'the same show', 'the same series', 'the same title', 'that', 'this')


def _items(text):
    t = str(text or '')
    parts = [p for p in _ITEM_SPLIT_RX.split(t) if p and p.strip()]
    if len(parts) < 2:
        # inline lists: "...; people who watched it on Hulu; ..." or " and people who watched it on"
        parts = re.split(r";|\n|,?\s+(?:and|or|vs\.?|versus)\s+(?=" + _NOUNS + r"\s+(?:who|that))", t, flags=re.I)
    # an item is its first line; the question under the list is not an item
    return [p.strip().split('\n', 1)[0].strip() for p in parts if p and p.strip()]


def detect(text):
    """{'title', 'verb', 'platforms', 'any', 'items', 'method_question'}
    when the words ask for the same title's audience on two or more
    services (or one service and "any"), else None."""
    try:
        t = str(text or '').strip()
        if not t or len(t) > 2500:
            return None
        title, verb = '', ''
        plats, saw_any, n_items = [], False, 0
        for raw in _items(t):
            m = _ITEM_RX.search(raw)
            item_verb = m.group('verb').lower() if m else ''
            if not m:
                m = _OF_RX.search(raw)
                if m:
                    item_verb = _NOUN_VERB.get(m.group('noun').lower(), 'watched')
            if not m:
                continue
            n_items += 1
            tt = m.group('title')
            if not _is_pointer(tt):
                ct = _clean_title(tt)
                if ct and not title:
                    title = ct
                elif ct and title and re.sub(r"[^a-z0-9]+", '', ct.lower()) != re.sub(r"[^a-z0-9]+", '', title.lower()):
                    return None     # two different titles: a real batch, not this shape
            verb = verb or item_verb
            p_raw = ' '.join(str(m.group('plat') or '').replace('_', ' ').replace('*', ' ').split())
            if _ANY_RX.match(p_raw):
                saw_any = True
                continue
            p = _canon_platform(p_raw)
            if not p:
                return None         # an unknown service: leave the words to the model
            if p not in plats:
                plats.append(p)
        if not title or n_items < 2:
            return None
        if len(plats) < 1 or (len(plats) < 2 and not saw_any):
            return None
        return {'title': title, 'verb': verb or 'watched', 'platforms': plats, 'any': saw_any,
                'items': n_items, 'method_question': bool(_METHOD_RX.search(t))}
    except Exception:
        return None


def rewrite_for_interpret(text, hit):
    """The one sentence the interpret model reads: the title's audience on
    any service. A window the reader wrote stays in the sentence."""
    verb = str((hit or {}).get('verb') or 'watched')
    title = str((hit or {}).get('title') or '').strip()
    noun = {'listened to': 'listeners', 'heard': 'listeners', 'read': 'readers', 'played': 'players'}.get(verb, 'viewers')
    out = f'Run a profile of {title} {noun}: people who {verb} "{title}" on any streaming service.'
    win = ''
    try:
        from . import subject_rules as _sr
        win = str(_sr.window_in_text(text) or '').strip()
    except Exception:
        win = ''
    if win:
        out += f' {win}'
    return out


def cut_label(platform):
    return f'{platform} Viewers'


def addon_cuts(hit, title=''):
    """One behavioral add-on cut per named service, in the shape the
    worker's add-on runner and addon_cut_synthesis expect. The service
    is 100% of its cut by definition (the defining-brand pin)."""
    out = []
    title = str(title or (hit or {}).get('title') or '').strip()
    verb = str((hit or {}).get('verb') or 'watched')
    for p in (hit or {}).get('platforms') or []:
        cid = re.sub(r"[^a-z0-9]+", '_', f'{p} viewers'.lower()).strip('_')
        out.append({
            'cut_id': cid, 'label': cut_label(p), 'name_label': cut_label(p),
            'kind': 'behavioral', 'pin_category': None, 'pin_buckets': [],
            'platform': p, 'from_prompt_qualifier': True,
            'cohort_description': (f'US people who {verb} {title} on {p}. {p} is 100% of this cohort by '
                                   f'definition; a person may also have {verb} it on another service.'),
        })
    return out


def method_note(hit, subject='', cuts=None):
    """How the build works, in plain words (Jenna's note to Jay, 2026-11-02),
    naming this ask's services. '' when the ask did not ask."""
    if not (hit or {}).get('method_question'):
        return ''
    title = str(subject or (hit or {}).get('title') or 'the title').strip()
    plats = [str(c.get('platform') or c.get('label') or '').replace(' Viewers', '') for c in (cuts or [])] \
        or list((hit or {}).get('platforms') or [])
    plats = [p for p in plats if p]
    lines = [
        "How this gets built: first I find the title's digital play platforms, both where it streams with a "
        "subscription and where it can be rented or bought. I take every play code and URL from those "
        "placements and look through our data for the people who reached those exact destinations during "
        "the windows the title was available on each. I then pull a year of behavior for those people, read "
        "their demography, and process their behavioral traits into the profile.",
    ]
    defs = [f"The {title} total universe is everyone who streamed it anywhere it was available to rent, buy or "
            f"stream with a subscription."]
    for p in plats:
        defs.append(f"The {p} cut is the people who watched it on {p}.")
    if len(plats) >= 2:
        defs.append("The cuts are not mutually exclusive: one person can sit in more than one of them.")
    defs.append("The Avid tier is the people who watched it four or more times, on any of those services.")
    lines.append(' '.join(defs))
    return '\n\n'.join(lines)


def apply_to_draft(draft, hit, text=''):
    """Ride the service cuts (and the method note when asked) onto a
    finished interpret draft. Only a fresh build or a refresh carries
    them; an existing match hands the cuts to the cut path by making the
    decision cut_needs_parent-shaped through addon_cuts as well. Mutates
    in place; never raises."""
    try:
        if not isinstance(draft, dict) or not hit:
            return draft
        subject = str(draft.get('subject') or draft.get('name') or hit.get('title') or '').strip()
        cuts = addon_cuts(hit, subject)
        have = draft.get('addon_cuts') if isinstance(draft.get('addon_cuts'), list) else []
        seen = {str(c.get('cut_id') or '') for c in have if isinstance(c, dict)}
        for c in cuts:
            if c['cut_id'] not in seen:
                have.append(c)
                seen.add(c['cut_id'])
        draft['addon_cuts'] = have
        draft['platform_cuts'] = list(hit.get('platforms') or [])
        note = method_note(hit, subject, cuts)
        if note:
            draft['method_note'] = note
        # the universe is the title on every service: a service the
        # reader named only as a cut must never pin the whole universe
        for k in ('named_platforms', 'content_seed_platforms_named', 'home_platform_rows'):
            v = draft.get(k)
            if isinstance(v, list) and v:
                keep = [x for x in v if (str(x[1]) if isinstance(x, (list, tuple)) and len(x) > 1 else str(x))
                        .strip().lower() not in {q.lower() for q in hit.get('platforms') or []}]
                draft[k] = keep
        if draft.get('platform') and str(draft.get('platform')).strip().lower() in {q.lower() for q in hit.get('platforms') or []}:
            draft['platform'] = None
        return draft
    except Exception:
        return draft


def note_for_title_build(draft, text):
    """The how-it-is-built note for a single title audience whose ask
    carried the method question but named no second service. '' when
    the ask did not ask, or the draft is not a title's audience."""
    try:
        if not isinstance(draft, dict) or not asks_method(text):
            return ''
        is_title = bool(draft.get('content_show')) or str(draft.get('ip_scope') or '').lower() == 'consumers' \
            or str(draft.get('audience_type') or '').lower() in ('viewers', 'listeners', 'readers', 'players') \
            or str(draft.get('brand_category') or '').upper() in ('MOVIE', 'PODCAST', 'GAMES') \
            or str(draft.get('brand_category') or '').upper().startswith('SERIES')
        if not is_title:
            return ''
        plats = []
        for row in (draft.get('home_platform_rows') or []):
            if isinstance(row, (list, tuple)) and len(row) > 1 and str(row[1]).strip():
                plats.append(str(row[1]).strip())
        if not plats and draft.get('platform'):
            plats = [str(draft['platform']).strip()]
        hit = {'method_question': True, 'platforms': plats, 'title': str(draft.get('subject') or '')}
        cuts = [{'platform': p} for p in plats]
        return method_note(hit, str(draft.get('subject') or draft.get('name') or ''), cuts)
    except Exception:
        return ''

