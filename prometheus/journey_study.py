"""The Music to Long Form study answers questions about its own data
(2026-11-05, Liz Huszarik).

Liz, with the Music to Long Form study open on Digital Journey IQ,
asked "in the Music to Longform data set, did the Instagram Social
touchpoints convert any movie viewing." and read "I can pull that read
on 🗺️ Digital Journey IQ. Pulling this data will cost $100." A question
about the dataset on the screen is a lookup into data we hold: free,
answered from the study's own file, before any lane can quote. And the
subject of anything said about it is the study's name, never the tab
label with its icon.

The study is a client-side demo (`static/data/jiq_music_catalog.json`:
sixty titles, the across-the-file / all-films / all-series rollups and
the landing read), so it has no payload under journey-iq/ on S3; the
journey lane reads it from here. What the file holds per scope:

  universe        every person with a tracked touch; listened / did not
  directions      Music first / Title first / No music touch, with the
                  share that watched and the share that went deep
  discovery       the first channel they found the music on (Music
                  streaming service, Social media, Search, Editorial
                  and press, Direct navigation) with listeners, then
                  watched, deep; Social media is ONE pooled channel
                  (TikTok, Instagram Reels, YouTube Shorts, X) and is
                  never split by platform
  social_formats  the clip type on social (mood edit, lyric overlay,
                  scene clip, meme, ...) with the same three counts
  no_music_entries how the viewers who never played a tracked song came in

Counts are people inside the Crosswalk 10 million (meta.incidence_of_10m)
and are stated exactly as stored, never restated as US figures. Shares
are the stored percentages.

    answer(text, ctx, user=..., history=..., host=...) -> raw reply dict | None
"""
from __future__ import annotations

import json
import os
import re
import threading

__all__ = ['STUDY_NAME', 'answer', 'is_study_view', 'names_study', 'load', 'is_dataset_question',
           'needs_split', 'resolve_scope']

STUDY_NAME = 'Music to Long Form'
_CATALOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'static', 'data', 'jiq_music_catalog.json')
_KEY_RX = re.compile(r"^__demo_music_(?:to_long_form|catalog|([a-z0-9_]+))__$")
_JOURNEY_VIEWS = frozenset(('journeyIQ', 'journey_iq'))

_cache = {'doc': None, 'path': ''}
_lock = threading.Lock()


def _squash(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or '').lower())


def names_study(text):
    """True when the words name the study ('Music to Long Form', 'Music
    to Longform', 'music-to-long-form data set')."""
    return 'musictolongform' in _squash(text)


def _view_data(ctx):
    vc = (ctx or {}).get('view_context') if isinstance(ctx, dict) else None
    vc = vc if isinstance(vc, dict) else {}
    data = vc.get('data') if isinstance(vc.get('data'), dict) else {}
    return vc, data


def study_key(ctx):
    """The music demo key the page carries ('__demo_music_to_long_form__',
    '__demo_music_barbie__'), '' when the page is not the study."""
    vc, data = _view_data(ctx)
    for k in (data.get('key'), (ctx or {}).get('journey_key') if isinstance(ctx, dict) else None,
              ((ctx or {}).get('journey') or {}).get('key') if isinstance(ctx, dict) and isinstance((ctx or {}).get('journey'), dict) else None):
        k = str(k or '').strip()
        if k and _KEY_RX.match(k):
            return k
    return ''


def is_study_view(ctx):
    """True when the Music to Long Form study is what is open on the
    page: a journey view whose data carries the study's key, its story
    mode, or its name."""
    vc, data = _view_data(ctx)
    if str(vc.get('view_id') or '').strip() not in _JOURNEY_VIEWS:
        return False
    if study_key(ctx):
        return True
    if str(data.get('story_mode') or '').strip().lower() == 'music_catalog':
        return True
    meta = data.get('meta') if isinstance(data.get('meta'), dict) else {}
    for v in (data.get('title'), data.get('project_name'), data.get('target_name'),
              meta.get('project_name'), meta.get('target_name'), meta.get('story_mode')):
        if v and (names_study(v) or str(v).strip().lower() == 'music_catalog'):
            return True
    return False


def load(path=None):
    """The study file, parsed once per process. None when it is absent."""
    p = path or os.environ.get('PM_MUSIC_STUDY_PATH') or _CATALOG_PATH
    with _lock:
        if _cache['doc'] is not None and _cache['path'] == p:
            return _cache['doc']
    try:
        with open(p, 'r', encoding='utf-8') as fh:
            doc = json.load(fh)
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    with _lock:
        _cache['doc'], _cache['path'] = doc, p
    return doc


# ----------------------------------------------------------------- the words
_PLATFORM_RX = re.compile(
    r"\b(?:instagram|insta|ig|reels?|tiktok|tik tok|youtube(?: shorts?)?|shorts|facebook|fb|snapchat|snap|twitter|x\.com|"
    r"(?:on|from|via) x\b|x posts?|threads|pinterest|reddit|twitch|bluesky)\b", re.I)
_SOCIAL_RX = re.compile(r"\bsocial(?: media)?\b|\bfeeds?\b|\bposts?\b", re.I)
_CHANNEL_RX = re.compile(
    r"\b(?:touch ?points?|channels?|platforms?|surfaces?|search|streaming(?: service)?|spotify|apple music|editorial|press|"
    r"direct navigation|social(?: media)?|where (?:did )?they f(?:i|ou)nd|found (?:it|the song|the music)|first (?:touch|channel|step))\b", re.I)
_CONVERT_RX = re.compile(r"\b(?:convert(?:ed|s|ing|sion)?|conversion rate|drove|drive|led to|went (?:on )?to (?:watch|the title|a film|a movie)|"
                         r"then watched|watch(?:ed|ing)?|view(?:ed|ing|s)?|moved? (?:from|to)|went from|finish(?:ed)?|deep|paid)\b", re.I)
_DIRECTION_RX = re.compile(r"\b(?:music[- ]first|song[- ]first|heard (?:a|the) song first|title[- ]first|show[- ]first|film[- ]first|content[- ]first|"
                           r"no music touch|never (?:played|touched)|without (?:the )?(?:song|music)|directions?|doors?|how many went from)\b", re.I)
_FORMAT_RX = re.compile(r"\b(?:clip types?|formats?|scene clips?|memes?|mood edits?|lyric|dance (?:clip|trend)s?|fancams?|reactions?|"
                        r"which (?:clip|edit|post type))\b", re.I)
_FILM_RX = re.compile(r"\b(?:films?|movies?|theatrical|cinema|box office)\b", re.I)
_SERIES_RX = re.compile(r"\b(?:series|shows?|seasons?|episodes?|tv)\b", re.I)
_SUMMARY_RX = re.compile(r"\b(?:summar(?:y|ize)|overview|walk me through|tell me about|what does (?:the|this) (?:study|data|journey|file) (?:show|say)|"
                         r"headline|big picture|takeaway|what stands out)\b", re.I)
_DATASET_RX = re.compile(r"\b(?:data ?set|dataset|data|study|file|journey|funnel|read|this view|on (?:the |my )?screen)\b", re.I)
_QUESTION_RX = re.compile(r"\?|\b(?:did|do|does|how (?:many|much|did|does|do|well|often)|what|which|who|where|when|is|are|was|were|"
                          r"can you|could you|show me|give me|tell me|walk me|list|break (?:it |this |that )?down|compare|summar(?:y|ize))\b", re.I)
# splits the study never carries: a person-level demographic, a place,
# a single platform inside the pooled social channel
_DEMO_RX = re.compile(r"\b(?:age(?:s|d)?|gender|women|men|female|male|income|hhi|education|ethnicit(?:y|ies)|race|parents?|kids|"
                      r"demographics?|demo(?:s)?|household|married|occupation|generation|gen z|millennials?|boomers?)\b", re.I)
_GEO_RX = re.compile(r"\b(?:dma|state|states|city|cities|region|regions|market|markets|zip|county|counties|geograph\w*|"
                     r"los angeles|new york|chicago|dallas|texas|california|florida)\b", re.I)
_ORDER_RX = re.compile(r"^\W*(?:email|send|mail|download|export|save|attach|build|create|make|draft|write|run|set ?up|launch|"
                       r"please (?:email|send|mail|download|export|build|create|make|draft|write|run))\b", re.I)
_TITLE_WORDS = {'the', 'a', 'an', 'of', 'and', 'to', 'in', 'on', 'vol', 'part'}


_CAP_PHRASE_RX = re.compile(r"\b[A-Z][A-Za-z0-9&'+.-]*(?:\s+(?:and\s+|&\s+|of\s+|the\s+)?[A-Z0-9][A-Za-z0-9&'+.-]*)+")
_NOT_A_TITLE = frozenset(('instagram', 'insta', 'reels', 'tiktok', 'tik', 'tok', 'youtube', 'shorts', 'facebook', 'snapchat', 'twitter',
                          'social', 'media', 'music', 'long', 'form', 'longform', 'search', 'editorial', 'press', 'direct', 'navigation',
                          'spotify', 'apple', 'amazon', 'netflix', 'hulu', 'max', 'peacock', 'paramount', 'disney', 'prime', 'video',
                          'digital', 'journey', 'iq', 'crosswalk', 'prometheus', 'the', 'and', 'of', 'to', 'in', 'on', 'for', 'data',
                          'set', 'dataset', 'study', 'film', 'films', 'movie', 'movies', 'series', 'show', 'shows', 'title', 'titles',
                          'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october',
                          'november', 'december', 'us', 'usa', 'x'))


def names_other_title(text):
    """True when the words carry a capitalized multi-word name that is
    not the study, a platform, a channel or a month ('Babylon 5', 'The
    Influencer Project'): with the study open, that question is about
    something else and never reads off the study."""
    t = ' '.join(str(text or '').split())
    for m in _CAP_PHRASE_RX.finditer(t):
        if m.start() == 0:
            continue                                    # a sentence opener is not a name
        words = [w.lower().strip(".'") for w in re.findall(r"[A-Za-z0-9&'+.-]+", m.group(0))]
        if names_study(m.group(0)):
            continue
        rest = [w for w in words if w not in _NOT_A_TITLE]
        if len(rest) >= 2 or (rest and any(w.isdigit() for w in words)):
            return True
    return False


def is_dataset_question(text):
    """A question about what the study holds: it asks (a question word
    or a question mark) about a touchpoint, channel, platform, direction,
    clip format or conversion, or asks for the study's summary. Orders
    (email, send, build, download) are never this."""
    t = ' '.join(str(text or '').split())
    if not t or _ORDER_RX.match(t):
        return False
    if not _QUESTION_RX.search(t):
        return False
    return bool(_CHANNEL_RX.search(t) or _PLATFORM_RX.search(t) or _SOCIAL_RX.search(t) or _DIRECTION_RX.search(t)
                or _FORMAT_RX.search(t) or _CONVERT_RX.search(t) or _SUMMARY_RX.search(t))


def needs_split(text):
    """'platform' | 'demographic' | 'geography' | '' : the cut the words
    ask for that the study does not carry."""
    t = str(text or '')
    if _DEMO_RX.search(t):
        return 'demographic'
    if _GEO_RX.search(t):
        return 'geography'
    return ''


def _fmt(n):
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return str(n)


def _pct(v):
    """The stored percentage as the dashboard prints it (one decimal)."""
    try:
        return f"{float(v):.1f}%"
    except Exception:
        return str(v)


def _lo(s):
    s = str(s or '').strip()
    return s[:1].lower() + s[1:] if s else s


def _figure(label, value, kind='count'):
    return {'label': str(label)[:80], 'value': value, 'kind': kind, 'source': 'view_data', 'id': None}


# ----------------------------------------------------------------- scopes
def _title_slug(doc, text, key):
    """The catalog slug the page key or the words name, '' when none."""
    cat = doc.get('catalog') if isinstance(doc.get('catalog'), dict) else {}
    m = _KEY_RX.match(key or '')
    if m and m.group(1) and m.group(1) in cat and isinstance(cat[m.group(1)], dict):
        return m.group(1)
    low = ' ' + re.sub(r"[^a-z0-9]+", ' ', str(text or '').lower()) + ' '
    best, best_len = '', 0
    for slug, t in cat.items():
        if not isinstance(t, dict):
            continue
        title = str((t.get('meta') or {}).get('title') or '')
        toks = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in _TITLE_WORDS]
        if toks and all(f' {w} ' in low for w in toks) and len(toks) > best_len:
            best, best_len = slug, len(toks)
    return best


def resolve_scope(doc, text, ctx):
    """(scope_label, analysis, meta, slug): all films when the words say
    film or movie, all series when they say series or show, the open
    title when one is open or named, else across the file."""
    key = study_key(ctx)
    roll = doc.get('rollups') if isinstance(doc.get('rollups'), dict) else {}
    cat = doc.get('catalog') if isinstance(doc.get('catalog'), dict) else {}
    t = str(text or '')
    film, series = bool(_FILM_RX.search(t)), bool(_SERIES_RX.search(t))
    slug = '' if (film or series) else _title_slug(doc, t, key)
    if slug and isinstance(cat.get(slug), dict) and isinstance(cat[slug].get('analysis'), dict):
        entry = cat[slug]
        return f"on {str((entry.get('meta') or {}).get('title') or slug)}", entry['analysis'], entry.get('meta') or {}, slug
    pick = '__film__' if film and not series else '__series__' if series and not film else '__all__'
    r = roll.get(pick) if isinstance(roll.get(pick), dict) else None
    if not r:
        r = roll.get('__all__') if isinstance(roll.get('__all__'), dict) else None
        pick = '__all__'
    if not r or not isinstance(r.get('analysis'), dict):
        return '', {}, {}, ''
    meta = r.get('meta') or {}
    label = {'__film__': f"on the {meta.get('n_titles') or 21} films", '__series__': f"on the {meta.get('n_titles') or 40} series"}.get(pick, 'across the file')
    return label, r['analysis'], meta, pick


def _head(meta, scope_pick):
    roll_meta = meta if isinstance(meta, dict) else {}
    if scope_pick in ('__all__', '__film__', '__series__'):
        wl = str(roll_meta.get('window_label') or '').strip()
        if scope_pick == '__all__':
            return f"From the {STUDY_NAME} study ({roll_meta.get('n_titles') or 61} titles, each in its own release window):"
        return f"From the {STUDY_NAME} study ({wl or 'each title in its own release window'}):"
    wl = str(roll_meta.get('window_label') or roll_meta.get('window') or '').strip()
    title = str(roll_meta.get('title') or '').strip()
    return f"From the {STUDY_NAME} study, {title} ({wl}):" if wl else f"From the {STUDY_NAME} study, {title}:"


def _rows(analysis):
    rows = [r for r in (analysis.get('discovery') or []) if isinstance(r, dict) and r.get('channel')]
    rows.sort(key=lambda r: -float(r.get('listeners') or 0))
    return rows


def _social_row(analysis):
    for r in _rows(analysis):
        if 'social' in str(r.get('channel') or '').lower():
            return r
    return None


def _depth_label(meta, analysis):
    for src in (analysis.get('meta') if isinstance(analysis.get('meta'), dict) else {}, meta or {}):
        d = str(src.get('depth_label') or '').strip()
        if d:
            return _lo(d)
    return 'finished the title or paid again'


def _social_detail(doc):
    """The platforms the pooled social channel covers, from the per-title
    rows ('TikTok, Instagram Reels, YouTube Shorts, X')."""
    cat = doc.get('catalog') if isinstance(doc.get('catalog'), dict) else {}
    for t in cat.values():
        if not isinstance(t, dict):
            continue
        for r in ((t.get('analysis') or {}).get('discovery') or []):
            if isinstance(r, dict) and 'social' in str(r.get('channel') or '').lower() and r.get('detail'):
                return str(r['detail']).strip()
    return 'TikTok, Instagram Reels, YouTube Shorts and X'


def _and_list(s):
    parts = [p.strip() for p in str(s or '').split(',') if p.strip()]
    if len(parts) <= 1:
        return s
    return ', '.join(parts[:-1]) + ' and ' + parts[-1]


def _channel_lines(analysis, meta, scope_label, lead_channel=None):
    rows = _rows(analysis)
    if not rows:
        return [], []
    lines = [f"How the first channels compare {scope_label} (people in the study who found the music there, how many then watched a title, and the share):"]
    figs = []
    ranked = sorted(rows, key=lambda r: -float(r.get('conversion_pct') or 0))
    for r in ranked:
        ch = str(r.get('channel'))
        lines.append(f"- {ch}: {_fmt(r.get('listeners'))} people, {_fmt(r.get('watched'))} watched, {_pct(r.get('conversion_pct'))}")
        figs.append(_figure(f"{ch}: then watched", int(r.get('watched') or 0)))
    return lines, figs


def _social_block(doc, analysis, meta, scope_label, scope_pick):
    s = _social_row(analysis)
    if not s:
        return [], []
    depth = _depth_label(meta, analysis)
    noun = 'a film' if scope_pick == '__film__' else 'a series' if scope_pick == '__series__' else 'a title'
    lines = [f"What the social touchpoint carried {scope_label}:",
             f"- {_fmt(s.get('listeners'))} people in the study found the music through social media.",
             f"- {_fmt(s.get('watched'))} of them then watched {noun}: {_pct(s.get('conversion_pct'))}."]
    figs = [_figure('Social media: listeners', int(s.get('listeners') or 0)),
            _figure('Social media: then watched', int(s.get('watched') or 0)),
            _figure('Social media: share that watched', float(s.get('conversion_pct') or 0), 'pct')]
    if s.get('deep') is not None and s.get('depth_pct') is not None:
        lines.append(f"- {_fmt(s.get('deep'))} {depth}: {_pct(s.get('depth_pct'))} of those who watched.")
        figs.append(_figure(f"Social media: {depth[:50]}", int(s.get('deep') or 0)))
    if s.get('music_first') and s.get('music_first_watched') is not None:
        lines.append(f"- {_fmt(s.get('music_first'))} heard the song first on social; {_fmt(s.get('music_first_watched'))} of those went on to watch: "
                     f"{_pct(s.get('music_first_conversion_pct'))}.")
        figs.append(_figure('Social media: music first, then watched', int(s.get('music_first_watched') or 0)))
    return lines, figs


def _across_line(doc):
    roll = (doc.get('rollups') or {}).get('__all__') if isinstance(doc.get('rollups'), dict) else None
    if not isinstance(roll, dict):
        return '', []
    s = _social_row(roll.get('analysis') or {})
    n = (roll.get('meta') or {}).get('n_titles') or 61
    if not s:
        return '', []
    return (f"Across all {n} titles, films and series together, social media reached {_fmt(s.get('listeners'))} people in the study "
            f"and {_fmt(s.get('watched'))} of them watched a title: {_pct(s.get('conversion_pct'))}."), \
           [_figure('Social media, all titles: then watched', int(s.get('watched') or 0))]


def _direction_lines(analysis, meta, scope_label):
    dirs = [d for d in (analysis.get('directions') or []) if isinstance(d, dict)]
    if not dirs:
        return [], []
    depth = _depth_label(meta, analysis)
    lines = [f"The three ways in {scope_label}:"]
    figs = []
    for d in dirs:
        lab = str(d.get('label') or d.get('direction') or '')
        if float(d.get('conversion_pct') or 0) >= 100:
            lines.append(f"- {lab}: {_fmt(d.get('viewers'))} watched first and then went to the music; {_pct(d.get('depth_pct'))} {depth}.")
        else:
            lines.append(f"- {lab}: {_fmt(d.get('reached'))} people, {_fmt(d.get('viewers'))} watched ({_pct(d.get('conversion_pct'))}); "
                         f"{_pct(d.get('depth_pct'))} of the watchers {depth}.")
        figs.append(_figure(f"{lab}: watched", int(d.get('viewers') or 0)))
    return lines, figs


def _format_lines(analysis, scope_label):
    fm = [f for f in (analysis.get('social_formats') or []) if isinstance(f, dict) and f.get('fmt')]
    if not fm:
        return [], []
    fm.sort(key=lambda f: -float(f.get('conversion_pct') or 0))
    lines = [f"Social clip types {scope_label} (people who found the music through it, how many then watched, the share):"]
    figs = []
    for f in fm[:9]:
        lines.append(f"- {f.get('fmt')}: {_fmt(f.get('listeners'))} people, {_fmt(f.get('watched'))} watched, {_pct(f.get('conversion_pct'))}")
        figs.append(_figure(f"{f.get('fmt')}: then watched", int(f.get('watched') or 0)))
    return lines, figs


def _universe_lines(analysis, scope_label):
    u = analysis.get('universe') if isinstance(analysis.get('universe'), dict) else {}
    if not u:
        return [], []
    lines = [f"{_fmt(u.get('total'))} people in the study had a tracked touch {scope_label}; {_fmt(u.get('listened'))} played a tracked song "
             f"({_pct(u.get('listened_pct'))}) and {_fmt(u.get('viewers'))} watched a title."]
    return lines, [_figure('People with a tracked touch', int(u.get('total') or 0)), _figure('Watched a title', int(u.get('viewers') or 0))]


def _counting_line(doc):
    """The study's own counting note, in the house header so the generic
    view definition (US viewers, projected) never attaches to a read
    whose counts are people in the study."""
    note = ''
    try:
        note = str(((doc.get('landing') or {}).get('meta') or {}).get('window_note') or '').strip()
    except Exception:
        note = ''
    win = 'inside that title\'s own release window'
    m = re.search(r"(three months before[^.]*after)", note, re.I)
    if m:
        win += f" ({m.group(1).strip()})"
    return (f"What we count as a view: a person in the {STUDY_NAME} study who watched a title {win}, "
            f"counted once per title. Counts are people in the study, not a US projection.")


# ----------------------------------------------------------------- the offer
def _offer(text, dim, user, history, host, honest):
    """One honest line, then the quote for the cut the study does not
    hold, labeled with the study's name (never a tab label)."""
    try:
        from . import priced_read as _pr
        uname = str((user or {}).get('username') or (user or {}).get('email') or '').strip()
        label, _credits = _pr._derived_price(uname)
        staff = False
        try:
            staff = bool(_pr.staff_user(user) or _pr.seat_is_free(user, uname, host))
        except Exception:
            staff = False
        reply, chips, body = _pr.compose_offer(f"I can pull that read on {STUDY_NAME}.", label, text,
                                               {'question': str(text or '')[:600], 'subject': STUDY_NAME, 'kind': 'derived',
                                                'frame': '', 'dimension': dim or 'platform'}, staff=staff)
    except Exception:
        return {'success': True, 'action': 'answer', 'reply': honest, 'followups': [], 'offer_deck': False,
                'deck_angle': None, 'subject': STUDY_NAME, 'figures': []}
    return {'success': True, 'action': 'answer', 'reply': f"{honest} {reply}".strip(), 'followups': chips,
            'offer_deck': False, 'deck_angle': None, 'panel_offer': body, 'subject': STUDY_NAME, 'figures': []}


# ----------------------------------------------------------------- answer
def answer(text, ctx=None, *, user=None, history=None, host=None, doc=None):
    """A reply read off the Music to Long Form study, or None when the
    words are not a question about it. The study must be on the page or
    named in the words; the words must ask about its data."""
    t = ' '.join(str(text or '').split())
    if not t:
        return None
    on_page = is_study_view(ctx)
    named = names_study(t)
    if not (on_page or named):
        return None
    if not is_dataset_question(t):
        return None
    if not named and names_other_title(t):
        return None                                     # a question about another title, asked with the study open
    doc = doc or load()
    if not doc:
        return None
    scope_label, analysis, meta, pick = resolve_scope(doc, t, ctx)
    if not analysis:
        return None
    head = _head(meta, pick)
    lines, figs = [], []
    split = needs_split(t)
    platform = bool(_PLATFORM_RX.search(t))
    if split:
        what = {'demographic': 'age, gender, income or education for its viewers',
                'geography': 'where its viewers live'}.get(split, 'that cut')
        honest = (f"The {STUDY_NAME} study does not carry {what}; it holds the first channel, the way in "
                  f"(music first, title first, no music touch), the clip type on social and the watch and finish counts.")
        return _offer(t, split, user, history, host, honest)
    if platform:
        # social is one pooled channel; the honest line first, then what
        # the pooled touchpoint did carry, then the comparison
        lines.append(f"The study does not split social by platform. Social media is one touchpoint there, pooling "
                     f"{_and_list(_social_detail(doc))}, so there is no Instagram-only count in this data."
                     if re.search(r"\binsta(?:gram)?\b|\big\b|\breels?\b", t, re.I) else
                     f"The study does not split social by platform. Social media is one touchpoint there, pooling "
                     f"{_and_list(_social_detail(doc))}, so there is no single-platform count in this data.")
        ls, fg = _social_block(doc, analysis, meta, scope_label, pick)
        lines.extend([''] + ls)
        figs.extend(fg)
        ls, fg = _channel_lines(analysis, meta, scope_label)
        lines.extend([''] + ls)
        figs.extend(fg)
        if pick != '__all__':
            al, af = _across_line(doc)
            if al:
                lines.extend(['', al])
                figs.extend(af)
        pf = re.search(r"\b(instagram|tiktok|youtube(?: shorts)?|facebook|snapchat|twitter|x)\b", t, re.I)
        pname = {'x': 'X', 'tiktok': 'TikTok', 'youtube': 'YouTube', 'youtube shorts': 'YouTube Shorts'}.get(
            (pf.group(1).lower() if pf else ''), (pf.group(1).title() if pf else 'A single platform'))
        lines.extend(['', f"{pname} on its own would be a new read on this study. If you want it, say so and I will set it up."])
    elif _SOCIAL_RX.search(t) and not _FORMAT_RX.search(t):
        ls, fg = _social_block(doc, analysis, meta, scope_label, pick)
        lines.extend(ls)
        figs.extend(fg)
        ls, fg = _channel_lines(analysis, meta, scope_label)
        lines.extend([''] + ls)
        figs.extend(fg)
    elif _FORMAT_RX.search(t):
        ls, fg = _format_lines(analysis, scope_label)
        lines.extend(ls)
        figs.extend(fg)
    elif _DIRECTION_RX.search(t):
        ls, fg = _direction_lines(analysis, meta, scope_label)
        lines.extend(ls)
        figs.extend(fg)
    elif _CHANNEL_RX.search(t) or _CONVERT_RX.search(t):
        rows = _rows(analysis)
        if rows:
            lead = max(rows, key=lambda r: float(r.get('conversion_pct') or 0))
            most = rows[0]
            lines.append(f"{most.get('channel')} found the most people ({_fmt(most.get('listeners'))}); "
                         f"{lead.get('channel')} converted the largest share of them to a watch ({_pct(lead.get('conversion_pct'))}).")
        ls, fg = _channel_lines(analysis, meta, scope_label)
        lines.extend([''] + ls if lines else ls)
        figs.extend(fg)
    if not lines or _SUMMARY_RX.search(t):
        ul, uf = _universe_lines(analysis, scope_label)
        dl, df = _direction_lines(analysis, meta, scope_label)
        cl, cf = _channel_lines(analysis, meta, scope_label)
        lines = (lines + [''] if lines else []) + ul + [''] + dl + [''] + cl
        figs.extend(uf + df + cf)
    if not lines:
        return None
    lines.extend(['', _counting_line(doc)])
    reply = head + '\n' + '\n'.join(lines)
    reply = re.sub(r"\n{3,}", "\n\n", reply).strip()
    followups = [f"Which clip type on social converted best {scope_label}" if not _FORMAT_RX.search(t) else f"How the three ways in compare {scope_label}",
                 "How the films compare with the series on social" if pick != '__series__' else "How the series compare with the films on social",
                 "Walk me through the whole study"]
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': followups[:3], 'offer_deck': False, 'deck_angle': None,
            'figures': figs[:40], 'profile': STUDY_NAME, 'subject': STUDY_NAME, 'journey_key': study_key(ctx) or '__demo_music_to_long_form__',
            'window': {'start': (meta or {}).get('window_start'), 'end': (meta or {}).get('window_end')}}
