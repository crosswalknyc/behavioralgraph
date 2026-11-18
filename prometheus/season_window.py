"""A season question with no window answers from the widest window held
(Jenna 2026-11-18).

Outlander: Blood of My Blood held six trackers: for Season 2 a four-day
premiere, the first 30 days and the season to date; for Season 1 a
four-day premiere, the full run and a later re-run of almost the same
window. "How many signups did Season 2 drive" was answered from
whichever file's name happened to sit closest to the words (the
premiere), the stored figures answered from another (the first 30
days), and "Season 1" went to the newest file (the re-run) instead of
the full run it was first read on.

The rule, for every title:

  * a question that names a title and a season and no window, premiere,
    date range or day count is answered from the WIDEST window held for
    that season that the reader may open;
  * a tie or a near tie (a full run and a re-run of almost the same
    window) goes to the file held first, which is the one the rest of
    the title was read against;
  * a question that names a window gets that window: the premiere, the
    first N days, the season to date, the full run, explicit dates;
  * a reader who names one file in full ("Season 2 to date") gets that
    file;
  * a question with no season on a title held by season gets one file
    per season, each at its widest window, never two windows of one
    season side by side;
  * the answer says the window it used, in plain words.

Pure: no storage, no model, no host. The library's files arrive as
plain dicts ({'show', 'key', 'lm', 'start', 'end', 'platform'}); the
reader's access arrives as ``can_open(key, show) -> bool``. What a seat
may open is decided elsewhere and is not changed here: a reader who
cannot open the widest file is answered from the widest one they can,
and a reader who can open none of a season's windows, or whose access
cannot be read, gets the lookup exactly as it was before this rule.
Never raises.
"""
from __future__ import annotations

import re
from datetime import date

__all__ = ['ask', 'title_base', 'name_season', 'name_tail', 'files_named', 'built_stamp', 'window_label',
           'attach_seasons', 'pick', 'picks_for_title', 'reader_can_open', 'stored_scope', 'window_line']

_DOTS_RX = re.compile(r'\b(?:[A-Za-z]\.){2,}')
_SEASON_RX = re.compile(r'\bseason (\d{1,2})\b')
_KEY_TS_RX = re.compile(r'_(\d{2})_(\d{2})_(\d{4})_(\d{2})_(\d{2})(?:\.csv)?$')
_MONTHS = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'sept': 9,
           'oct': 10, 'nov': 11, 'dec': 12}
_MON_NAME = ('', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
_MON_WORD = (r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|'
             r'sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)')
_MD_RX = re.compile(r'\b(' + _MON_WORD + r') (\d{1,2})(?:st|nd|rd|th)?(?: (20\d{2}))?\b')
_ISO_RX = re.compile(r'\b(20\d{2}) (\d{2}) (\d{2})\b')
_NUM_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
              'ten': 10, 'fourteen': 14, 'thirty': 30, 'sixty': 60, 'ninety': 90}
_N = r'(\d{1,3}|' + '|'.join(_NUM_WORDS) + r')'
_FIRST_RX = re.compile(r'\b(?:first|opening|initial) ' + _N + r' (day|week|month)s?\b')
_FIRST_UNIT_RX = re.compile(r'\b(?:first|opening) (week|month)\b')
_NDAY_RX = re.compile(r'\b(\d{1,3}) day (?:window|read|mark|view|cut)\b|\bday (\d{1,3})\b|\bt(\d{1,3})d\b')
_PREMIERE_RX = re.compile(r'\b(?:premiere|premier|premiered|debut|opening (?:window|weekend|week|night|days?)|'
                          r'launch (?:window|weekend|week|day|days)|first weekend|opening)\b')
# 'since its premiere', 'since it launched': everything from the opening on, which is the season to date
_SINCE_OPEN_RX = re.compile(r'\bsince (?:\w+ ){0,3}?(?:premiere|premier|premiered|debut|debuted|launch|launched|'
                            r'release|released)\b')
_TO_DATE_RX = re.compile(r'\b(?:to date|so far|std|thus far|through (?:today|now)|up to (?:today|now)|'
                         r'as of (?:today|now)|until (?:today|now))\b')
_FULL_RX = re.compile(r'\b(?:full|whole|entire|complete|overall|all episodes|season long|start to finish|'
                      r'end to end|in total)\b')
# a window the question names that this module does not place on a file:
# the lookup is left as it was for these
_OTHER_RX = re.compile(
    r'\b(?:last|past|previous|prior|trailing|latest) (?:\d{1,3} |' + '|'.join(_NUM_WORDS) + r' )?(?:day|week|month|year)s?\b|'
    r'\bthis (?:week|month|year)\b|\byesterday\b|\bweek (?:\d{1,2}|one|two|three|four)\b|\bq[1-4]\b|'
    r'\b(?:in|during|for|since|until|through|by) ' + _MON_WORD + r'\b|\b' + _MON_WORD + r' \d{1,2}\b|'
    r'\b' + _MON_WORD + r' 20\d{2}\b|\b20\d{2} \d{2} \d{2}\b|\b\d{1,2} \d{1,2} 20\d{2}\b|'
    r'\b(?:in|during|for|since) 20\d{2}\b')
_TAIL_WORDS_RX = re.compile(r'\b(?:to date|full|complete|total)\b')


def _norm(s):
    """Lowercase, 'S.W.A.T.' -> 'swat', 'S2' -> 'season 2', punctuation
    to spaces. The library lookup's own normal form."""
    t = str(s or '')
    t = _DOTS_RX.sub(lambda m: m.group(0).replace('.', ''), t)
    t = t.lower()
    t = re.sub(r'\b(?:ssn|sea|seas)\.?\s*(\d{1,2})\b', r'season \1', t)
    t = re.sub(r'\bs(\d{1,2})\b(?!\d)', r'season \1', t)
    t = re.sub(r'[^a-z0-9]+', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def _d(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v or '').strip()[:10])
    except Exception:
        return None


def _days(c):
    try:
        return (c['end'] - c['start']).days + 1
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# What the question asks for
# ---------------------------------------------------------------------------

def ask(text):
    """{'seasons': [n, ...], 'kind': None | 'full' | 'premiere' | 'days'
    | 'to_date' | 'dates' | 'other', 'days': n, 'dates': ((m, d, y), (m,
    d, y))}. ``kind`` None is a question that names no window."""
    out = {'seasons': [], 'kind': None, 'days': None, 'dates': None}
    try:
        t = ' ' + _norm(text) + ' '
        for s in _SEASON_RX.findall(t):
            n = int(s)
            if n not in out['seasons']:
                out['seasons'].append(n)
        mds = [(_MONTHS[m[0][:3]], int(m[1]), int(m[2]) if m[2] else None) for m in _MD_RX.findall(t)]
        isos = [(int(m[1]), int(m[2]), int(m[0])) for m in _ISO_RX.findall(t)]
        pair = isos[:2] if len(isos) >= 2 else (mds[:2] if len(mds) >= 2 else None)
        if pair:
            out['kind'], out['dates'] = 'dates', (pair[0], pair[1])
            return out
        m = _FIRST_RX.search(t)
        if m:
            n = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORDS.get(m.group(1), 0)
            n *= {'day': 1, 'week': 7, 'month': 30}[m.group(2)]
            if n > 0:
                out['kind'], out['days'] = 'days', n
                return out
        m = _FIRST_UNIT_RX.search(t)
        if m:
            out['kind'], out['days'] = 'days', 7 if m.group(1) == 'week' else 30
            return out
        m = _NDAY_RX.search(t)
        if m:
            n = int(next(g for g in m.groups() if g))
            if n > 0:
                out['kind'], out['days'] = 'days', n
                return out
        if _SINCE_OPEN_RX.search(t):
            out['kind'] = 'to_date'
        elif _PREMIERE_RX.search(t):
            out['kind'] = 'premiere'
        elif _TO_DATE_RX.search(t):
            out['kind'] = 'to_date'
        elif _OTHER_RX.search(t):
            out['kind'] = 'other'
        elif _FULL_RX.search(t):
            out['kind'] = 'full'
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# What a file is
# ---------------------------------------------------------------------------

def name_season(show):
    """The season a file's name carries, or None."""
    m = _SEASON_RX.search(' ' + _norm(show) + ' ')
    return int(m.group(1)) if m else None


def title_base(show):
    """The title a file belongs to: its normalized name up to the
    season ('outlander blood of my blood season 2 to date' ->
    'outlander blood of my blood')."""
    full = _norm(show)
    m = _SEASON_RX.search(full)
    return (full[:m.start()] if m else full).strip()


def name_tail(show):
    """What a file's name says after its season ('to date', 'part 2',
    'netflix library'), '' when it stops at the season or has none."""
    full = _norm(show)
    m = _SEASON_RX.search(full)
    return full[m.end():].strip() if m else ''


_PART_WORD_RX = re.compile(r'\b(part|volume|chapter)(s?) (one|two|three|four|five|six)\b')
_PART_TAIL_RX = re.compile(r'^(part|volume|chapter) (\d{1,2})$')
_PART_WORDS = {'part': 'part', 'parts': 'part', 'volume': 'volume', 'volumes': 'volume', 'chapter': 'chapter',
               'chapters': 'chapter'}
_PART_JOINS = frozenset(('and', 'or', 'vs', 'versus', 'to', 'through', 'with', 'against', 'plus', 'compared'))


def _parts_after(t, season):
    """The parts named right after one season: 'season 4 part 1 and
    part 2' -> {('part', 1), ('part', 2)}."""
    out = set()
    for m in re.finditer(r'\bseason ' + str(int(season)) + r' ((?:\w+ ){1,10})', t):
        word = None
        for tok in m.group(1).split():
            if tok in _PART_WORDS:
                word = _PART_WORDS[tok]
            elif tok.isdigit() and word:
                out.add((word, int(tok)))
            elif tok not in _PART_JOINS:
                break
    return out


def files_named(files, text):
    """The files the words name in full, tail and all ('Season 2 to
    date', 'Season 4 Part 2', 'season 4 part two', 'Season 4 Part 1 and
    Part 2'; with no season said at all, 'Part 2' alone)."""
    try:
        t = ' ' + _norm(text) + ' '
        t = _PART_WORD_RX.sub(lambda m: f"{m.group(1)}{m.group(2)} {_NUM_WORDS[m.group(3)]}", t)
        any_season = bool(_SEASON_RX.search(t))
        out = []
        for f in files or []:
            if not isinstance(f, dict) or not f.get('key'):
                continue
            tail = name_tail(f.get('show'))
            if not tail:
                continue
            hit = (' ' + _norm(f.get('show')) + ' ') in t
            pm = _PART_TAIL_RX.match(tail)
            if not hit and pm:
                if any_season:
                    hit = (pm.group(1), int(pm.group(2))) in _parts_after(t, name_season(f.get('show')))
                else:
                    hit = f' {tail} ' in t
            if hit:
                out.append(f['key'])
        return out
    except Exception:
        return []


def built_stamp(key, lm=''):
    """Sortable 'YYYYMMDDHHMM' for when a file was first built: the
    stamp in its name (a later correction keeps the name), else its
    last-modified time."""
    m = _KEY_TS_RX.search(str(key or ''))
    if m:
        mo, dd, yy, hh, mi = m.groups()
        return f"{yy}{mo}{dd}{hh}{mi}"
    return re.sub(r'[^0-9]', '', str(lm or ''))[:12] or '999999999999'


def window_label(start, end):
    """'Sep 18 to Nov 18, 2026' / 'Dec 19, 2025 to Feb 20, 2026'."""
    s, e = _d(start), _d(end)
    if not s or not e:
        return ''
    if s.year == e.year:
        return f"{_MON_NAME[s.month]} {s.day} to {_MON_NAME[e.month]} {e.day}, {e.year}"
    return f"{_MON_NAME[s.month]} {s.day}, {s.year} to {_MON_NAME[e.month]} {e.day}, {e.year}"


def _prep(files):
    out = []
    for f in files or []:
        if not isinstance(f, dict) or not f.get('key'):
            continue
        c = dict(f)
        c['start'], c['end'] = _d(f.get('start')), _d(f.get('end'))
        c['built'] = built_stamp(c.get('key'), c.get('lm'))
        c['platform'] = _norm(c.get('platform'))
        c['season'] = name_season(c.get('show'))
        c['season_from'] = 'name' if c['season'] is not None else None
        out.append(c)
    return out


def attach_seasons(files):
    """Each file with its season: the one in its name, else the season
    whose files it sits inside (a file named for the title alone that
    covers the first 30 days of Season 2 belongs to Season 2). A file
    that spans more than one season, or none, keeps no season."""
    cands = _prep(files)
    spans = {}
    for c in cands:
        if c['season'] is not None and c['start'] and c['end']:
            a, b = spans.get(c['season'], (c['start'], c['end']))
            spans[c['season']] = (min(a, c['start']), max(b, c['end']))
    for c in cands:
        if c['season'] is not None or not c['start'] or not c['end']:
            continue
        inside = [s for s, (a, b) in spans.items() if a <= c['start'] and c['end'] <= b]
        if len(inside) == 1:
            c['season'], c['season_from'] = inside[0], 'window'
            continue
        if inside:
            continue
        opens = [s for s, (a, _b) in spans.items() if abs((c['start'] - a).days) <= 3]
        if len(opens) == 1 and not any(s != opens[0] and c['start'] < a <= c['end'] for s, (a, _b) in spans.items()):
            c['season'], c['season_from'] = opens[0], 'window'
    return cands


# ---------------------------------------------------------------------------
# Which file answers
# ---------------------------------------------------------------------------

def _near(a, b):
    return abs(a - b) <= max(3, int(round(0.05 * max(a, b))))


def _first_held(group):
    return sorted(group, key=lambda c: (c['built'], str(c['key'])))[0]


def _widest(cands):
    w = max(_days(c) for c in cands)
    return _first_held([c for c in cands if _near(_days(c), w)])


def _md_date(md, year):
    try:
        return date(md[2] or year, md[0], md[1])
    except Exception:
        return None


def pick(cands, intent):
    """The one file among ``cands`` (one title, one season, one
    platform; every one with a window) that answers ``intent``. None
    when the question names a window this rule does not place: the
    caller leaves the lookup as it was."""
    try:
        cands = [c for c in cands or [] if c.get('start') and c.get('end')]
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        kind = (intent or {}).get('kind')
        if kind in (None, 'full'):
            return _widest(cands)
        s0 = min(c['start'] for c in cands)
        opening = [c for c in cands if abs((c['start'] - s0).days) <= 3]
        if kind == 'to_date':
            last = max(c['end'] for c in cands)
            return _widest([c for c in cands if (last - c['end']).days <= 3])
        if kind == 'premiere':
            short = [c for c in opening if _days(c) <= 10]
            if not short:
                return _widest(cands)
            m = min(_days(c) for c in short)
            return _first_held([c for c in short if _days(c) == m])
        if kind == 'days':
            n = int((intent or {}).get('days') or 0)
            slack = 1 if n < 10 else 2
            cover = [c for c in opening if _days(c) >= n - slack]
            if not cover:
                return _widest(cands)
            m = min(_days(c) for c in cover)
            return _first_held([c for c in cover if _near(_days(c), m)])
        if kind == 'dates':
            a, b = (intent or {}).get('dates') or (None, None)
            scored = []
            for c in cands:
                s = _md_date(a, c['start'].year)
                e = _md_date(b, c['end'].year)
                if not s or not e or e < s:
                    continue
                inter = (min(e, c['end']) - max(s, c['start'])).days + 1
                union = (max(e, c['end']) - min(s, c['start'])).days + 1
                scored.append((max(inter, 0) / float(union or 1), c))
            if not scored:
                return None
            best = max(j for j, _c in scored)
            if best < 0.2:
                return None
            return _first_held([c for j, c in scored if best - j <= 0.05])
        return None
    except Exception:
        return None


def _one_named(group, intent, can_open):
    """Several files under one full name ('Season 1 to date' built
    twice): one answers. The window rule among the ones the reader may
    open; otherwise the newest, which is what the lookup returned before."""
    if len(group) == 1:
        return group[0]
    seen = [c for c in group if can_open(c['key'], c.get('show') or '')] if callable(can_open) else group
    p = None
    if len(seen) == 1:
        p = seen[0]
    elif seen:
        p = pick(seen, intent)
    return p or sorted(group, key=lambda c: (str(c.get('lm') or ''), c['built'], str(c['key'])))[-1]


def picks_for_title(files, intent, can_open=None, named_keys=()):
    """The files of ONE title that answer the question.

    ``files``       every library file of the title
    ``intent``      ``ask(question)``, its seasons already narrowed to
                    the ones named for this title
    ``can_open``    the reader's access, or None when nobody is named
    ``named_keys``  files the reader named in full, tail and all

    Returns a list of files (each with 'season', 'label', 'collapsed':
    True when it was chosen among several windows of its season), [] when
    the title holds none of the seasons asked, or None when the rule does
    not apply (the title is not held by season, no season of it is held
    on more than one window, a window could not be read, or the reader
    can open none of the windows of a season asked): the caller keeps
    what it had.
    """
    try:
        cands = attach_seasons(files)
        if not cands or not any(c['season_from'] == 'name' for c in cands):
            return None
        # a title held on one file per season is left exactly as it was
        sizes = {}
        for c in cands:
            if c['season'] is not None:
                sizes[(c['season'], c['platform'])] = sizes.get((c['season'], c['platform']), 0) + 1
        if not sizes or max(sizes.values()) < 2:
            return None
        by_name, order = {}, []
        for c in cands:
            if c['key'] in set(named_keys or ()):
                n = _norm(c.get('show'))
                if n not in by_name:
                    by_name[n] = []
                    order.append(n)
                by_name[n].append(c)
        named = []
        for n in order:
            c = _one_named(by_name[n], intent, can_open)
            named.append(dict(c, collapsed=len(by_name[n]) > 1 and bool(c.get('start') and c.get('end')),
                              label=window_label(c['start'], c['end'])))
        asked = [s for s in ((intent or {}).get('seasons') or [])]
        held = sorted({c['season'] for c in cands if c['season'] is not None}, reverse=True)
        loose = [c for c in cands if c['season'] is None]
        if named:
            # the reader named a file in full: it answers its own season;
            # any other season the question names is read by the rule
            took = {c['season'] for c in named}
            want = [s for s in asked if s in held and s not in took]
            if not want:
                return named
        elif asked:
            want = [s for s in asked if s in held]
            if not want:
                return [dict(c, collapsed=False, label=window_label(c['start'], c['end'])) for c in loose]
        else:
            want = held
        out = list(named)
        for s in want:
            part = [c for c in cands if c['season'] == s]
            if any(not c['start'] or not c['end'] for c in part):
                return None
            for plat in sorted({c['platform'] for c in part}):
                group = [c for c in part if c['platform'] == plat]
                seen = [c for c in group if can_open(c['key'], c.get('show') or '')] if callable(can_open) else group
                if not seen:
                    # none of this season's windows is this reader's to open: the rule does
                    # not choose for them, and the lookup stays exactly as it was
                    return None
                p = seen[0] if len(seen) == 1 else pick(seen, intent)
                if p is None:
                    return None
                out.append(dict(p, collapsed=len(group) > 1, label=window_label(p['start'], p['end'])))
        if not asked and not named:
            out.extend(dict(c, collapsed=False, label=window_label(c['start'], c['end'])) for c in loose)
        return out
    except Exception:
        return None


def window_line(p, intent=None):
    """The line that rides the evidence when a file was chosen among
    several windows of its season, so the answer says which one it is
    reading. '' when there is nothing to say."""
    try:
        if not p or not p.get('collapsed') or not p.get('label'):
            return ''
        return (f"WINDOW OF THIS READ: {p['label']}. Say once, in plain words and near the first figure, that these "
                f"figures cover {p['label']}. Give the dates only; never say how the window was chosen and never "
                f"call it a file, a run or a tracker.")
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------

_NOBODY = frozenset(('unknown', 'anon', 'replay'))
_READER_TTL_S = 20
_reader_cache = {}      # seat name -> (read at, the seat's record)


def _opens_nothing(key, show=''):
    return False


def _seat(host, label):
    """The named seat's record, read at most once every few seconds."""
    import time as _time
    now = _time.time()
    hit = _reader_cache.get(label)
    if hit and now - hit[0] < _READER_TTL_S:
        return hit[1]
    from . import priced_read as _pr
    user = _pr.live_user(host, label)
    user = user if isinstance(user, dict) and user else None
    _reader_cache[label] = (now, user)
    if len(_reader_cache) > 200:
        for k, _v in sorted(_reader_cache.items(), key=lambda kv: kv[1][0])[:100]:
            _reader_cache.pop(k, None)
    return user


def reader_can_open():
    """``(key, show) -> bool`` for the reader of the work in flight.

    None when no seat is reading (an operator's script, a probe that is
    not a seat): the rule then chooses among every window held. A reader
    who is named but whose access cannot be read here (no host, no
    record of the seat) opens nothing as far as this rule knows, so the
    lookup stays exactly as it was for them: the rule never hands a seat
    a window it could not be shown to open."""
    try:
        from . import ledger_gate as _lg
        cur = _lg.current()
        label = str((cur[0] if cur else '') or '').strip()
        if not label or label.lower() in _NOBODY or label.lower().startswith('canary:'):
            return None
        from .host import host as _host
        if not _host.bound or not _host.has('can_open_subiq'):
            return _opens_nothing
        user = _seat(_host, label)
        if not user:
            return _opens_nothing

        def _can(key, show=''):
            try:
                return bool(_host.can_open_subiq(user, key, show))
            except Exception:
                return False
        return _can
    except Exception:
        return _opens_nothing


# ---------------------------------------------------------------------------
# The stored figures
# ---------------------------------------------------------------------------

_TRACKER_REPLY_RX = re.compile(r'^Subscriber IQ tracker (\S+\.csv)\s*$')


def tracker_file_of(entry):
    """The tracker a stored row mirrors, or ''."""
    try:
        if not isinstance(entry, dict) or entry.get('retired') or entry.get('route') != 'delivered_subiq':
            return ''
        m = _TRACKER_REPLY_RX.match(str(entry.get('reply') or '').strip())
        return m.group(1) if m else ''
    except Exception:
        return ''


def _show_of_key(key):
    stem = str(key or '').rsplit('/', 1)[-1]
    stem = stem[:-4] if stem.endswith('.csv') else stem
    m = re.match(r'^(.+?)_(\d{2}_\d{2}_\d{4}_\d{2}_\d{2})$', stem)
    return (m.group(1) if m else stem).replace('_', ' ')


def _library_files(base):
    """Every live file of one title, from the bound host's library.
    None when no library can be read here."""
    try:
        from .host import host as _host
        if not _host.bound or not _host.has('subiq_bucket'):
            return None
        import prometheus_analysis as _pma
        return _pma.subiq_title_files(_host.s3_client, _host.subiq_bucket, base)
    except Exception:
        return None


def stored_scope(index_doc, skey, question, subject=None, live=None, library=None, can_open='reader'):
    """Which stored bucket answers a question about a title held on
    several windows, and which stored tracker rows are another window's.

    Returns None when the rule does not apply (the resolved subject has
    no tracker rows, its title is not held by season, the question names
    a window this rule does not place). Otherwise {'key': the bucket that
    mirrors the chosen file, or None when that file has no stored row;
    'keep': the chosen files; 'family': every tracker file stored for the
    title; 'labels': {file: window}}. The caller moves to ``key`` and
    leaves the rows of ``family - keep`` out of the answer: a 30-day
    figure is not the answer to a question about the season.
    """
    try:
        subjects = (index_doc or {}).get('subjects') or {}
        if skey:
            bucket = subjects.get(skey) or {}
            own = [f for f in (tracker_file_of(e) for e in (bucket.get('entries') or [])) if f]
            base = title_base(_show_of_key(own[0])) if own \
                else title_base(str(bucket.get('subject') or '').replace(' - ', ' '))
        else:
            # the words resolved to no stored subject: a title with stored
            # tracker rows that the question names whole still has one
            base, qn0 = '', ' ' + _norm(f"{question} {subject or ''}") + ' '
            for v in subjects.values():
                for e in (v.get('entries') or []):
                    f = tracker_file_of(e)
                    b = title_base(_show_of_key(f)) if f else ''
                    if len(b) > max(5, len(base)) and f' {b} ' in qn0:
                        base = b
        if not base or len(base) < 3:
            return None
        rows = {}       # tracker file -> (bucket key, entry)
        for k, v in subjects.items():
            if callable(live) and not live(v):
                continue
            for e in (v.get('entries') or []):
                f = tracker_file_of(e)
                if f and title_base(_show_of_key(f)) == base:
                    rows[f] = (k, e)
        if not rows:
            return None
        files = (library or _library_files)(base)
        if files is None:
            files = [{'show': _show_of_key(f), 'key': f, 'lm': '', 'start': e.get('ws'), 'end': e.get('we'),
                      'platform': ''} for f, (_k, e) in rows.items()]
        if len(files) < 2:
            return None
        intent = ask(question)
        if intent['kind'] == 'other':
            return None
        if not intent['seasons'] and subject:
            # the subject the ask is bound to names the season the words left out
            intent['seasons'] = list(ask(subject).get('seasons') or [])
        named = files_named(files, f"{question} {subject or ''}")
        if can_open == 'reader':
            can_open = reader_can_open()
        picks = picks_for_title(files, intent, can_open=can_open, named_keys=named)
        if picks is None:
            return None
        if not intent['seasons'] and not named:
            # no season named: the stored side follows the latest season's read
            seasoned = [p for p in picks if p.get('season') is not None]
            picks = seasoned[:1] or picks[:1]
        keep = [p['key'] for p in picks]
        key = next((rows[f][0] for f in keep if f in rows), None)
        return {'key': key, 'keep': set(keep), 'family': set(rows),
                'labels': {p['key']: p.get('label') or '' for p in picks}}
    except Exception:
        return None
