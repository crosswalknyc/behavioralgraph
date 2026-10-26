"""Journey-figure questions read from the delivered journey first.

Jenna 2026-10-14: two different numbers for one question is its own
trust problem. A question about a step or a table of a Digital Journey
the caller can see ("how many clicked through from a Babylon 5 post",
"where did the first post sit", "time from the view to the first
episode") is answered from the stored journey file, exactly, with no
model call and nothing metered. The deep read only runs when there is
no delivered journey to read from.

    answer(text, ctx, host=host, user=user, history=history) -> raw reply dict | None
"""
from __future__ import annotations

import gzip
import json
import re
import time

__all__ = ['answer', 'find_journey', 'journey_vocabulary', 'VOCAB_RX']

VOCAB_RX = re.compile(
    r"\b(?:digital journey|journey|conversion(?: rate)?|click(?:ed|s)? ?through|clicked|opened the (?:\w+ )?page|title page|"
    r"saw (?:a|the) (?:social )?post|social post|first post|where (?:did )?the first post|posts? before|how many posts|"
    r"looked before|where (?:did )?they look|time (?:from|to|between)|same day|within (?:7|seven|30|thirty) days|first episode|"
    r"last touch|assist(?:s|ed)?|already watching|outside the (?:30|thirty)|every viewer|viewers in the window|"
    r"watched (?:within|an episode)|first play|came back|left the page|abandon|(?:which|what) post|see first|saw first|"
    r"(?:which|what) episode|play(?:ed)? first|episode (?:did )?they (?:play|start|watch)|how many posts)\b", re.I)
_STOP = frozenset(('the', 'and', 'for', 'with', 'from', 'that', 'this', 'how', 'many', 'what', 'where', 'did', 'does', 'who',
                   'on', 'of', 'in', 'to', 'a', 'an', 'is', 'are', 'was', 'were', 'journey', 'digital', 'prime', 'video',
                   'netflix', 'hulu', 'max', 'peacock', 'paramount', 'disney', 'apple', 'tv', 'show', 'series', 'film', 'movie'))

_STEP_RX = (
    ('conversion', re.compile(r"\bconver(?:ted|sion|t)\b", re.I)),
    ('watched', re.compile(r"\b(?:watched|first play|played|streamed|viewers? (?:within|in) (?:30|thirty)|watch step)\b", re.I)),
    ('page', re.compile(r"\b(?:opened|open(?:ed)? the (?:\w+ )?page|title page|landed on|reached the page|page on)\b", re.I)),
    ('click', re.compile(r"\b(?:click(?:ed|s)? ?through|clicked|click-through|clickthrough)\b", re.I)),
    ('social', re.compile(r"\b(?:saw (?:a|the) (?:social )?post|social post viewers|saw the posts|social view|post viewers|were reached|reached by (?:a |the )?post)\b", re.I)),
)
_SECTION_RX = (
    ('first_post', re.compile(r"\b(?:first post they saw|which post|what post|first post|post (?:type|family|families)|kind of post|s(?:ee|aw) first)\b", re.I)),
    ('post_surface', re.compile(r"\b(?:where (?:did )?the (?:first )?post s(?:i|a)t|where (?:the|it) (?:first )?post (?:sat|sit|was|appeared)|which platform|what platform|where did it sit|post surface|platform (?:did|was) the post|where (?:was|did) (?:the|it) (?:first )?(?:post|seen))\b", re.I)),
    ('posts_before', re.compile(r"\b(?:how many posts|posts before|post frequency|frequency|number of posts|posts (?:did|it) t(?:ook|ake))\b", re.I)),
    ('looked_before', re.compile(r"\b(?:looked before|where (?:did )?they look|search(?:ed)? before|before (?:prime video|the platform|netflix|hulu|peacock|max|disney|they watched)|stop(?:s|ped)? (?:before|between))\b", re.I)),
    ('time_to_play', re.compile(r"\b(?:time (?:from|to|between)|how (?:long|fast|quickly|soon)|same day|within (?:7|seven|30|thirty) days|days (?:from|to|between)|timing)\b", re.I)),
    ('first_episode', re.compile(r"\b(?:first episode|which episode|what episode|entry point|episode (?:did )?they (?:play|start|watch))\b", re.I)),
    ('last_touch', re.compile(r"\b(?:last touch|final touch|last step before|what closed|closed the)\b", re.I)),
    ('assists', re.compile(r"\b(?:assist(?:s|ed|ing)?|along the way|helped|in the mix)\b", re.I)),
    ('rest_overlap', re.compile(r"\b(?:already watching|outside the (?:30|thirty)|31 to 90|after day 30|before the post(?:s)?|the rest)\b", re.I)),
    ('every_viewer', re.compile(r"\b(?:every viewer|all viewers|viewers in the window|total viewers|how many (?:people )?watched .* in the window|saw no post|never saw a post)\b", re.I)),
)
_FORK_RX = re.compile(r"\b(?:came back|left the page|abandon(?:ed)?|return(?:ed)?|same session|retarget|nudge|win[- ]back)\b", re.I)
# "what is this conversion?", "what counts as a conversion", "how is conversion defined":
# the reader wants the definition, not the figure again (Debi 2026-10-21)
_DEFINE_RX = re.compile(
    r"\b(?:what\s+(?:is|does|do\s+you\s+mean\s+by|counts?\s+as|makes)\s+(?:a|an|this|that)\s+(?:conversion|convert(?:ed|s)?)\b|"
    r"what\s+is\s+(?:the\s+)?conversion\s+(?:here|measuring|based\s+on|defined\s+as|in\s+this\s+case)\b|what\s+does\s+(?:the\s+)?conversion\s+(?:mean|measure|count)\b|"
    r"(?:conversion|converted)\s+(?:mean|means|defined|definition)\b|(?:define|definition\s+of|meaning\s+of|explain)\s+(?:the\s+|this\s+)?conversion\b|"
    r"how\s+(?:is|do\s+you)\s+(?:define|measure|count)\s+(?:a\s+|the\s+)?conversion\b|what\s+is\s+this\s+(?:\d+(?:\.\d+)?%|number|rate|percent(?:age)?))", re.I)
_SUMMARY_RX = re.compile(r"\b(?:summar(?:y|ize)|overview|walk me through|tell me about|what does the journey|the whole journey|headline|short version)\b", re.I)


def _fmt(n):
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return str(n)


def _pct(a, b, nd=1):
    try:
        a, b = float(a), float(b)
        return f"{a / b * 100:.{nd}f}%" if b else '0%'
    except Exception:
        return ''


def _tokens(s):
    return [t for t in re.findall(r"[a-z0-9]+", str(s or '').lower()) if t not in _STOP and (len(t) >= 3 or t.isdigit())]


def journey_vocabulary(text):
    return bool(VOCAB_RX.search(str(text or '')))


def _title_of(run):
    name = str(run.get('project_name') or run.get('target') or '')
    return name.rsplit(' on ', 1)[0].strip() if ' on ' in name else name.strip()


def _visible(run, user, uname):
    if str(run.get('created_by') or '') == uname:
        return True
    role = str((user or {}).get('role') or '').lower()
    if role in ('admin', 'super_admin'):
        return True
    if not ((user or {}).get('has_journey_iq_access') or (user or {}).get('has_analysis_iq_access')):
        return False
    allowed = (user or {}).get('allowed_journey_iq_runs')
    if allowed is None or (isinstance(allowed, list) and ('*' in allowed or run.get('key') in allowed)):
        return True
    return False


_CAP_PHRASE_RX = re.compile(r"\b[A-Z][A-Za-z0-9&'+.-]*(?:\s+(?:and\s+|&\s+|of\s+|the\s+)?[A-Z][A-Za-z0-9&'+.-]*)*")
_NOT_A_NAME = frozenset((
    'i', 'we', 'you', 'the', 'a', 'an', 'this', 'that', 'these', 'those', 'what', 'which', 'how', 'when', 'where', 'why', 'who',
    'is', 'are', 'was', 'were', 'can', 'could', 'would', 'should', 'do', 'does', 'did', 'please', 'prometheus', 'crosswalk', 'us',
    'usa', 'u.s.', 'ok', 'okay', 'yes', 'no', 'and', 'or', 'but', 'so', 'if', 'then', 'also', 'from', 'for', 'to', 'on', 'in', 'at',
    'by', 'with', 'of', 'as', 'my', 'our', 'your', 'its', 'it', 'they', 'them', 'there', 'here', 'now', 'today', 'conversion',
    'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december',
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'q1', 'q2', 'q3', 'q4',
    'youtube', 'tiktok', 'instagram', 'facebook', 'snapchat', 'twitter', 'x', 'reddit', 'netflix', 'hulu', 'peacock', 'max', 'hbo',
    'paramount', 'disney', 'prime', 'video', 'amazon', 'apple', 'tv', 'roku', 'tubi', 'spotify', 'google', 'pre', 'roll', 'mid',
    'new', 'york', 'los', 'angeles', 'journey', 'digital', 'brand', 'partnership', 'iq', 'attribution', 'profile'))


def names_other_subject(text, title):
    """True when the words name a person, brand or title that is not the
    journey's own (a capitalized phrase outside the title's tokens, after
    platforms, months and sentence openers are set aside)."""
    t = ' '.join(str(text or '').split())
    own = set(_tokens(title)) | {w.lower() for w in re.findall(r"[A-Za-z0-9]+", str(title or ''))}
    for m in _CAP_PHRASE_RX.finditer(t):
        words = re.findall(r"[A-Za-z0-9&'+.-]+", m.group(0))
        opener = m.start() == 0 or t[max(0, m.start() - 2):m.start()].strip() in ('.', '?', '!', ':', ';', '-', '"')
        rest = [w.lower().strip(".'") for w in words if w.lower().strip(".'") not in _NOT_A_NAME]
        if opener and len(words) == 1:
            continue          # a sentence-initial word is not a name on its own
        if not rest or all(w in own or len(w) < 3 or w.isdigit() for w in rest):
            continue
        return True
    return False


def find_journey(text, *, s3, user, uname, bucket='dashboard-inputs', ctx=None):
    """The newest delivered journey the caller can see whose title the
    question names (every distinctive title token present, a number in
    the title too), or the journey open on the page. None otherwise."""
    try:
        from migration import journey_iq as _jiq
        runs = _jiq.list_runs(s3, limit=500)
    except Exception:
        return None
    low = ' ' + re.sub(r"[^a-z0-9]+", ' ', str(text or '').lower()) + ' '
    page_key = ''
    try:
        page_key = str(((ctx or {}).get('journey') or {}).get('key') or (ctx or {}).get('journey_key') or '')
    except Exception:
        page_key = ''
    visible = [r for r in runs if isinstance(r, dict) and r.get('key') and _visible(r, user, uname)]
    if page_key:
        for run in visible:
            if run.get('key') == page_key:
                # the open journey answers a question about itself, never one
                # that names another subject (Debi 2026-10-21: the Colin and
                # Samir x Lexus campaign drew the NCIS: New York journey line,
                # three times, because that journey was open)
                if names_other_subject(text, _title_of(run)):
                    break
                return run
    # a token that names only one title ("reservations") can stand for it;
    # "Anthony Bourdain's No Reservations" is asked about as "No Reservations"
    counts = {}
    for run in visible:
        for t in set(_tokens(_title_of(run))):
            counts[t] = counts.get(t, 0) + 1
    best, best_score = None, 0.0
    for run in visible:
        toks = _tokens(_title_of(run))
        if not toks:
            continue
        present = [t for t in toks if f' {t} ' in low]
        if len(present) == len(toks):
            score = 1.0
        elif any(len(t) >= 8 and counts.get(t, 0) == 1 for t in present) and not any(t.isdigit() and t not in present for t in toks):
            score = 0.8
        else:
            continue
        if score > best_score or (score == best_score and str(run.get('created_at') or '') > str(best.get('created_at') or '')):
            best, best_score = run, score
    return best


def _load(s3, key, bucket):
    raw = s3.get_object(Bucket=bucket, Key=key)['Body'].read()
    try:
        raw = gzip.decompress(raw)
    except Exception:
        pass
    return json.loads(raw.decode('utf-8'))


def _spine(payload):
    """The steps after the TAM row (the TAM is the first row whatever its id)."""
    j = payload.get('fragrance_shop_journey') or {}
    sp = [s for s in (j.get('spine') or []) if isinstance(s, dict)]
    if sp and (str(sp[0].get('id') or '').lower() in ('tam', 'us_gen_pop', 'genpop', 'us_population') or int(sp[0].get('accounts') or 0) >= 100_000_000):
        sp = sp[1:]
    return sp


def _window(payload):
    m = payload.get('meta') or {}
    s, e = m.get('start_date'), m.get('end_date')
    return f"{s} to {e}" if s and e else str(m.get('window_label') or 'the window')


def _figure(label, value, kind='count'):
    return {'label': str(label)[:80], 'value': value, 'kind': kind, 'source': 'view_data', 'id': None}


def _step_answer(payload, wants, title, platform):
    sp = _spine(payload)
    if len(sp) < 2:
        return None, []
    idx = {'social': 0, 'click': 1 if len(sp) > 2 else None, 'page': len(sp) - 2 if len(sp) >= 3 else None, 'watched': len(sp) - 1}
    lines, figs = [], []
    if 'conversion' in wants:
        start, end = int(sp[0].get('accounts') or 0), int(sp[-1].get('accounts') or 0)
        if start and end:
            e_lab = str(sp[-1].get('label') or 'reached the last step')
            lines.append(f"{_fmt(end)} of the {_fmt(start)} who {str(sp[0].get('label') or 'started').strip()[:1].lower() + str(sp[0].get('label') or 'started')[1:]} "
                         f"{e_lab[:1].lower() + e_lab[1:]}: a conversion of {_pct(end, start)}.")
            figs.append(_figure('Conversion', round(end / start * 100, 2), 'pct'))
            figs.append(_figure(str(sp[0].get('label') or 'Start'), start))
            figs.append(_figure(e_lab, end))
    order = {'social': 0, 'click': 1, 'page': 2, 'watched': 3}
    for w in sorted([w for w in wants if w != 'conversion'], key=lambda x: order.get(x, 9)):
        i = idx.get(w)
        if i is None or i >= len(sp):
            continue
        st = sp[i]
        n = int(st.get('accounts') or 0)
        prev = int(sp[i - 1].get('accounts') or 0) if i > 0 else 0
        lab = str(st.get('label') or '')
        line = f"{_fmt(n)} {lab[:1].lower() + lab[1:]}"
        if prev:
            line += f" ({_pct(n, prev)} of the step before)"
        lines.append(line + '.')
        figs.append(_figure(lab, n))
    return lines, figs


def _define_conversion(payload, title, platform):
    """What the journey's conversion is: who counts, out of whom, and the
    arithmetic behind the percentage. Plain words, no new figures."""
    sp = _spine(payload)
    if len(sp) < 2:
        return None, []
    start, end = int(sp[0].get('accounts') or 0), int(sp[-1].get('accounts') or 0)
    if not (start and end):
        return None, []
    s_lab = str(sp[0].get('label') or 'started').strip()
    e_lab = str(sp[-1].get('label') or 'reached the last step').strip()
    e_doing = str(sp[-1].get('doing') or '').strip().rstrip('.')
    lo = lambda x: x[:1].lower() + x[1:]
    lines = [f"Conversion here is the share of people who {lo(s_lab)} who went on to the last step of the journey: {lo(e_lab)}."
             + (f" That step counts anyone who {lo(e_doing)}." if e_doing and e_doing.lower() != e_lab.lower() else ''),
             f"The arithmetic: {_fmt(end)} who {lo(e_lab)}, divided by {_fmt(start)} who {lo(s_lab)}, is {_pct(end, start)}.",
             f"So {_pct(end, start)} is not a share of all of the United States or of {title}'s existing audience; it is how much of the people the journey starts with made it to the end."]
    figs = [_figure('Conversion', round(end / start * 100, 2), 'pct'), _figure(s_lab, start), _figure(e_lab, end)]
    return lines, figs


def _section_answer(payload, key, title, platform):
    from migration import journey_gold as G
    sec = G.section(payload, key)
    if not sec or not sec.get('rows'):
        return None, []
    rows = [r for r in sec['rows'] if isinstance(r, dict)]
    kind = sec.get('kind') or ('overlap' if key in ('looked_before', 'assists') else 'partition')
    base = int(sec.get('base') or 0) or (sum(int(r.get('accounts') or 0) for r in rows) if kind == 'partition' else max(int(r.get('accounts') or 0) for r in rows))
    lines = [f"{sec['title']} ({'share of ' + _fmt(base) if base else 'counts'}):"]
    figs = []
    for r in rows[:8]:
        n = int(r.get('accounts') or 0)
        lines.append(f"- {r.get('label')}: {_fmt(n)}" + (f" ({_pct(n, base)})" if base else ''))
        figs.append(_figure(f"{sec['title'][:40]}: {r.get('label')}", n))
    if kind == 'overlap':
        lines.append('One person can appear in more than one row.')
    return lines, figs


def _fork_answer(payload):
    j = payload.get('fragrance_shop_journey') or {}
    fork = [f for f in (j.get('fork') or []) if isinstance(f, dict)]
    if not fork:
        return None, []
    lines, figs = ['The title page, who left and who came back:'], []
    for f in fork:
        n = int(f.get('accounts') or 0)
        lines.append(f"- {f.get('label') or f.get('id')}: {_fmt(n)}" + (f" ({float(f.get('kept') or 0):.1f}% of the branch above)" if f.get('kept') is not None else ''))
        figs.append(_figure(str(f.get('label') or f.get('id')), n))
    return lines, figs


def answer(text, ctx=None, *, host=None, user=None, history=None, s3=None, bucket='dashboard-inputs'):
    """A raw reply read off the delivered journey, or None when the
    question is not about a journey the caller can see."""
    t = str(text or '').strip()
    if not t or not journey_vocabulary(t):
        return None
    s3 = s3 or (host.s3_client if host is not None and host.has('s3_client') else None)
    if s3 is None:
        return None
    uname = str((user or {}).get('username') or (user or {}).get('email') or '').strip()
    run = find_journey(t, s3=s3, user=user, uname=uname, bucket=bucket, ctx=ctx)
    if not run:
        return None
    try:
        payload = _load(s3, run['key'], bucket)
    except Exception:
        return None
    from migration import journey_gold as G
    title, platform = G._title_platform(payload)
    name = str((payload.get('meta') or {}).get('project_name') or run.get('project_name') or title)
    window = _window(payload)
    lines, figs = [], []
    wants = [k for k, rx in _STEP_RX if rx.search(t)]
    if 'conversion' in wants and 'watched' in wants and not re.search(r"\bhow many (?:people )?watched\b", t, re.I):
        wants.remove('watched')
    if _DEFINE_RX.search(t[-240:]) and ('conversion' in wants or re.search(r"\bconver(?:sion|ted|t)\b", t, re.I)):
        lines, figs = _define_conversion(payload, title, platform)
        if lines:
            head = (f"From the journey for {name} ({window}):" if name.lower().startswith(('the ', 'a ', 'an ')) else f"From the {name} journey ({window}):")
            return {'success': True, 'action': 'answer', 'reply': head + '\n' + '\n'.join(lines), 'followups': [f"Where the first post sat for {title}", f"Time from the social view to the first episode for {title}"],
                    'offer_deck': False, 'deck_angle': None, 'figures': figs, 'profile': name, 'journey_key': run.get('key'),
                    'window': {'start': (payload.get('meta') or {}).get('start_date'), 'end': (payload.get('meta') or {}).get('end_date')}}
    keys = [k for k, rx in _SECTION_RX if rx.search(t)]
    # "where did the first post sit" is the surface table, not the post table
    if 'post_surface' in keys and 'first_post' in keys and not re.search(r"\b(?:which|what) post\b|post (?:type|family|families)", t, re.I):
        keys.remove('first_post')
    # "time from the view to the first episode" is the timing table, not the episode table
    if 'time_to_play' in keys and 'first_episode' in keys and re.search(r"\b(?:to|until|and) the first episode\b", t, re.I):
        keys.remove('first_episode')
    # "how many watched within 30 days" is the watch count, not the timing table
    if 'time_to_play' in keys and re.search(r"\bhow many\b", t, re.I) and not re.search(r"\b(?:time|how long|how fast|how quickly|how soon|same day|timing|split|break ?down)\b", t, re.I):
        keys.remove('time_to_play')
    for key in keys:
        ls, fg = _section_answer(payload, key, title, platform)
        if ls:
            lines.extend(ls)
            figs.extend(fg)
    if _FORK_RX.search(t) and not lines:
        ls, fg = _fork_answer(payload)
        if ls:
            lines.extend(ls)
            figs.extend(fg)
    if wants and not lines:
        ls, fg = _step_answer(payload, wants, title, platform)
        if ls:
            lines.extend(ls)
            figs.extend(fg)
    if not lines and (_SUMMARY_RX.search(t) or re.search(r"\bconver(?:sion|ted)\b", t, re.I) or wants):
        try:
            ship = G.ship_lines(G.apply(json.loads(json.dumps(payload))))
            lines = [ln.lstrip('- ') for ln in ship if ln]
            s = G.ship_summary(payload)
            figs = [_figure('Saw a social post', s['social']), _figure('Clicked through', s['click']), _figure('Opened the page', s['page']),
                    _figure('Watched within 30 days', s['watched']), _figure('Conversion', s['conversion'], 'pct')]
        except Exception:
            lines = []
    if not lines:
        return None
    head = (f"From the journey for {name} ({window}):" if name.lower().startswith(('the ', 'a ', 'an ')) else f"From the {name} journey ({window}):")
    reply = head + '\n' + '\n'.join(lines)
    followups = [f"Where the first post sat for {title}", f"Time from the social view to the first episode for {title}",
                 f"Last touch before the watch for {title}"][:3]
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': followups, 'offer_deck': False, 'deck_angle': None,
            'figures': figs[:40], 'profile': name, 'journey_key': run.get('key'), 'window': {'start': (payload.get('meta') or {}).get('start_date'),
                                                                                             'end': (payload.get('meta') or {}).get('end_date')}}
