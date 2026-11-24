"""A pasted list of titles is answered title by title (Nov 23 2026 ask
audit, fix 6; Jenna 2026-11-23: "A pasted list of titles is answered
title by title").

Armine pasted five titles with their availability windows ("The
titles/windows are: Forever My Girl (10/1/26-10/31/26), Moonfall
(10/1/26-11/9/26), ...") and asked for each one's US audience on
YouTube's Free with ads shelf. The read ran once over the whole list:
the five titles' figures disagreed with the reads she then ran one
title at a time, and the answer and the emailed file were labeled with
a phrase from her own text ("Including U.S. Unique viewers").

The rule: when an ask carries several titles (a list after "the titles
are:", a comma or line list, quoted titles, "these five"), the read runs
one read per title, in parallel where the read core allows, every piece
on the same platform scope and the same definitions, each title in its
own window when the list gives one and the ask's one window otherwise.
The answer names each title with its own figures and the file carries
one row per title. Nothing from the pasted text stands in for the
subject.

* `plan(text, reading=None)`: the deterministic plan off the words (a
  resumed job makes the same plan), or None for a single-title ask.
* `piece_question(text, plan, piece)`: the reader's words kept whole,
  then the piece pinned to one title and its window.
* `assemble(plan, results, ...)`: one result in the read core's shape:
  per-title metrics, a breakdown by title whose shares add to 100, the
  landed and missing titles on `_title_list`.
* `fill(...)`: a title whose read never came back is sized at the low
  end of its landed peers, salted, marked `_filled`; ops hears, the
  reader never does (the matrix read's rule, Jenna 2026-11-04).
* `format_reply(res)` and `csv_rows(entry)`: the chat answer and the
  file, one block and one row per title.

Pricing is untouched: a list is one ask, quoted and charged as one read,
the way it was before.

Pure text and dicts; the read core is handed in as a callable.
Regression: scripts/test_pm_audit_1123_title_list.py.
"""
from __future__ import annotations

import hashlib
import re

__all__ = ['plan', 'listed_single', 'piece_question', 'assemble', 'fill', 'describe', 'label', 'is_title_list', 'format_reply',
           'csv_rows', 'titles_in', 'MAX_TITLES', 'MAX_WORKERS']

MAX_TITLES = 24
MAX_WORKERS = 3
FILL_LOW, FILL_HIGH = 0.86, 0.96

_WORDS = {2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten',
          11: 'eleven', 12: 'twelve'}

# the list cue: "the titles/windows are:", "titles are:", "the films:", "for the following titles:", "these titles:"
_LIST_CUE_RX = re.compile(
    r"\b(?:titles?|films?|movies?|shows?|series|episodes?|books?|podcasts?|games?|programs?|specials?)(?:\s*(?:/|and|&)\s*windows?)?"
    r"(?:(?:\s+(?:are|is|were|include[sd]?|below|listed|as\s+follows|in\s+question|we\s+(?:want|need|care\s+about)))?\s*(?::|-\s|\u2013\s|\u2014\s)"
    r"|\s+(?:are|is|were)\s+(?=(?-i:[\"\u201c'A-Z0-9])))"
    r"\s*(?=(?P<body>[^\n]{4,1500}))", re.I)
# a line of a pasted list: "- Title (window)", "1. Title", "Title (10/1/26-10/31/26)"
_LINE_ITEM_RX = re.compile(r"^\s*(?:[-*\u2022]|\d{1,2}[.)])?\s*(?P<item>[^\n]{2,120}?)\s*$")
_DATE_PAREN_RX = re.compile(r"\(\s*(?P<win>[^()]{6,60}?)\s*\)\s*$")
_DATE_TOKEN_RX = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b|\b20\d{2}-\d{2}-\d{2}\b", re.I)
# a quoted title: double quotes anywhere; a straight single quote only as a quote mark (at a word boundary on both
# sides), never the apostrophe in "YouTube's" or "we'd"
_QUOTED_RX = re.compile(r"(?:[\"\u201c\u201d](?P<d>[^\"\u201c\u201d\n]{2,80})[\"\u201c\u201d]"
                        r"|(?:(?<=^)|(?<=[\s(,:;]))'(?P<s>[^'\n]{2,80})'(?=$|[\s),.:;!?]))")
_ORDINAL_LIST_RX = re.compile(r"\b(?:these|those|the|all|each\s+of\s+the)\s+(?P<n>two|three|four|five|six|seven|eight|nine|ten|\d{1,2})\s+(?:titles?|films?|movies?|shows?|series)\b", re.I)

# words that end an item or mean it is not a title
_NOT_TITLE_RX = re.compile(
    r"^(?:and|or|the|a|an|this|that|these|those|each|every|all|any|some|other|more|please|also|etc\.?|as well|"
    r"as well as|similar|example|window|windows|title|titles|availability|us|u\.s\.|total|unique|views?|plays?|viewers?|"
    r"reach|metrics?|data|performance|engagement|audience|size|level|section|shelf|same|below|above|following)$", re.I)
_METRIC_WORDS = frozenset(('unique', 'total', 'views', 'view', 'plays', 'play', 'viewers', 'reach', 'metrics', 'metric',
                           'minutes', 'hours', 'share', 'penetration', 'index', 'engagement', 'performance', 'audience',
                           'size', 'level', 'window', 'windows', 'availability', 'titles', 'title', 'data', 'figures',
                           'month', 'months', 'monthly', 'week', 'weeks', 'weekly', 'day', 'days', 'daily', 'year', 'years',
                           'quarter', 'quarters', 'breakdown', 'demographics', 'demos'))
# a count of titles or a quantifier over them is not a title ("21 films", "all 10 of these", "all titles including X")
_COUNT_ITEM_RX = re.compile(r"^(?:all\s+|every\s+|each\s+|the\s+)?(?:\d+\s+(?:of\s+these|of\s+them|titles?|films?|movies?|shows?|series|episodes?|seasons?|books?)\b"
                            r"|(?:all|every|each|any|other|the\s+rest\s+of\s+the)\s+(?:\d+\s+)?(?:of\s+(?:these|them)|titles?|films?|movies?|shows?|series|episodes?|seasons?|books?)\b"
                            r"|season\s+\d+\b|s\d{1,2}\b)", re.I)
# the words say the ask is about titles or watching; a bare list of names with no such word is a list of something else
_TITLE_CUE_RX = re.compile(r"\b(?:titles?|films?|movies?|shows?|series|episodes?|seasons?|books?|podcasts?|games?|specials?|documentar(?:y|ies)|"
                           r"watch(?:ed|ing|es)?|stream(?:ed|ing|s)?|view(?:ed|ers|ing|ership)?|play(?:ed|ers|ing|s)?|read(?:ers|ing)?|listen(?:ed|ers|ing)?|"
                           r"premiere[ds]?|aired|released?)\b", re.I)
# a time grid across the titles ("month by month", "each of the 12 months", "weekly") or a numbered-groups ask keeps the one read
_TIME_GRID_RX = re.compile(r"\b(?:month\s+by\s+month|week\s+by\s+week|day\s+by\s+day|each\s+of\s+the\s+\d+\s+(?:months|weeks|days)|by\s+month\b|monthly\s+(?:viewers|hours|figures|counts|table)|weekly\s+(?:viewers|hours|figures|counts|table))", re.I)
_NUMBERED_GROUPS_RX = re.compile(r"\(\s*1\s*\)[^\n]{2,120}?\(\s*2\s*\)")
# an item whose parenthetical lists more names is a group of titles, not a title ("Drama (It Ends with Us, Oppenheimer)")
_GROUP_PAREN_RX = re.compile(r"\(([^()]*,[^()]*)\)")
# an item that opens like a sentence or a request is not a title
_SENTENCE_LEAD_RX = re.compile(r"^(?:i|we|you|they|it|please|can|could|would|what|how|which|who|when|where|why|give|show|tell|"
                               r"pull|run|size|include|including|also|then|so|but|if|for|with|plus|along)\b", re.I)
_STOP_WORDS = frozenset(('the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for', 'and', 'or', 'with', 'by', 'from', 'vs', 'versus',
                         'its', "it's", 'their', 'our', 'your', 'each', 'every', 'all', 'this', 'that', 'these', 'those', 'is',
                         'are', 'was', 'were', 'be', 'as', 'we', 'you', 'i', 'us', 'they', 'it', 'if', 'possible', 'specifically',
                         'including', 'such', 'like', 'please', 'also', 'during', 'between', 'through', 'across', 'per', 'any',
                         'other', 'available', 'help', 'understand', 'want', 'need', 'looking', 'size', 'potential'))


def _n_word(n):
    return _WORDS.get(int(n), str(n))


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or '').lower()).strip()


def _clean_item(s):
    s = ' '.join(str(s or '').split())
    s = re.sub(r"^(?:[-*\u2022]|\d{1,2}[.)])\s*", '', s)
    s = re.sub(r"^(?:and|or|plus|then)\s+", '', s, flags=re.I)
    s = s.strip(' ,;:.-\u2013\u2014')
    return s


def _split_window(item):
    """('Title', {'start','end','label'} or None) off 'Title (10/1/26-10/31/26)' / 'Title - Oct 1 to Oct 31 2026'."""
    item = _clean_item(item)
    win = None
    m = _DATE_PAREN_RX.search(item)
    if m and _DATE_TOKEN_RX.search(m.group('win')):
        win = _window_of(m.group('win'))
        item = _clean_item(item[:m.start()])
    else:
        m2 = re.search(r"\s[-\u2013\u2014:]\s*(?P<win>[^()]{6,60})$", item)
        if m2 and _DATE_TOKEN_RX.search(m2.group('win')):
            w = _window_of(m2.group('win'))
            if w:
                win = w
                item = _clean_item(item[:m2.start()])
    return item, win


def _window_of(phrase):
    try:
        from . import inline_dates as _idates
        w = _idates.parse_inline_window(str(phrase or ''))
        if isinstance(w, dict) and w.get('start') and w.get('end'):
            lab = ''
            try:
                lab = _idates.window_label(str(w['start'])[:10], str(w['end'])[:10])
            except Exception:
                lab = ''
            return {'start': str(w['start'])[:10], 'end': str(w['end'])[:10], 'label': lab or f"{w['start']} to {w['end']}"}
    except Exception:
        pass
    return None


def _looks_like_title(name):
    """A title: one to ten words, at least one word that is not a stop or
    metric word, a capital or a digit somewhere, never a metric phrase or
    a lone platform."""
    n = ' '.join(str(name or '').split()).strip(' .,;:')
    if not n or len(n) > 90 or _NOT_TITLE_RX.match(n) or _SENTENCE_LEAD_RX.match(n) or _COUNT_ITEM_RX.match(n):
        return False
    toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\u2019&.:+!?-]*", n)
    if not toks or len(toks) > 10:
        return False
    low = [t.lower().strip(".'\u2019") for t in toks]
    content = [t for t in low if t not in _STOP_WORDS and t not in _METRIC_WORDS]
    if not content:
        return False
    if all(t in _METRIC_WORDS or t in _STOP_WORDS or t in ('us', 'u.s') for t in low):
        return False
    if not re.search(r"[A-Z0-9]", n):
        return False
    try:
        from . import platform_scope as _ps
        if _ps.canon(n):
            return False          # "YouTube" alone is where, not a title
    except Exception:
        pass
    return True


def _body_end(body):
    """Where the list body ends: the first sentence end outside a
    parenthetical that is followed by a space and a capital, or the end
    of the text. A dot inside an abbreviation ("U.S.") or a date is not
    a sentence end."""
    depth = 0
    for i, ch in enumerate(body):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        elif ch == '.' and depth == 0:
            nxt = body[i + 1:i + 3]
            if not nxt.strip() or (nxt[:1] == ' ' and nxt[1:2].isupper()):
                return i
    return len(body)


def _split_outside_parens(body, seps, and_too=False):
    parts, buf, depth, i = [], '', 0, 0
    while i < len(body):
        ch = body[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        if depth == 0 and ch in seps:
            parts.append(buf)
            buf = ''
            i += 1
            continue
        if and_too and depth == 0 and body[i:i + 5].lower() == ' and ' and buf.strip():
            parts.append(buf)
            buf = ''
            i += 5
            continue
        buf += ch
        i += 1
    parts.append(buf)
    return parts


# a clause that follows the last item and names where or when, not a title: "on YouTube in October", "for Q4"
_TAIL_WHEN_RX = re.compile(
    r"\s+(?:in|for|during|over|between|across|within|since|through|this|last|past)\s+(?:the\s+)?(?:(?:last|past|trailing|first|next)\s+)?"
    r"(?:\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]|20\d\d|window|month|week|year|quarter|period|season|summer|fall|winter|spring|holiday|its|their|each|every|all)"
    r".*$", re.I)
_TAIL_WHERE_RX = re.compile(r"\s+(?:on|via|through|at|across)\s+(?:the\s+)?(?P<plat>[A-Za-z][\w+&'\u2019 .-]{1,40}?)(?=\s+(?:in|for|during|over|between|this|last|past|since)\b|\s*$)", re.I)


def _strip_tail_clause(item):
    """'Meet the Browns on YouTube in October' -> 'Meet the Browns' when
    the tail names a platform or a time, never a title."""
    it = ' '.join(str(item or '').split())
    m = _TAIL_WHEN_RX.search(it)
    if m and m.start() > 0:
        it = it[:m.start()].strip(' ,;:.-')
    m2 = _TAIL_WHERE_RX.search(it)
    if m2 and m2.start() > 0:
        plat = m2.group('plat').strip()
        try:
            from . import platform_scope as _ps
            is_plat = bool(_ps.canon(plat)) or any(_ps.canon(w) for w in plat.split())
        except Exception:
            is_plat = False
        if is_plat:
            it = it[:m2.start()].strip(' ,;:.-')
    return it


def _split_items(body):
    """The items of a list body: ';' or newlines first, else commas and
    'and' outside parentheses. The body ends at its sentence end."""
    body = str(body or '').strip()
    if not body:
        return []
    body = body[:_body_end(body)]
    parts = []
    for chunk in _split_outside_parens(body, ';\n'):
        parts.extend(_split_outside_parens(chunk, ',', and_too=True))     # two levels: "A, B; C and D"
    return [x for x in (_clean_item(y) for y in parts) if x]


_COMPARE_RX = re.compile(r"\b(?:compare|comparison|vs\.?|versus|against|overlap|difference between|side by side|head to head|which (?:one )?(?:is|has|does)|who (?:skews|has|is))\b", re.I)


def titles_in(text, reading=None):
    """[{'title', 'window'}] the words list, in order; [] when they list
    fewer than two. Deterministic off the words; the Reader's title
    subjects only confirm or add a title the words also carry."""
    t = str(text or '')
    if not t.strip() or len(t) > 6000:
        return []
    # a brief with its own population and structure, a time grid across the titles, or a numbered-groups ask
    # keeps the one read (the read brief, the matrix and the grid have their own paths)
    try:
        from . import read_brief as _rb
        if _rb.is_brief(t) or _rb.stands_on_its_own(t):
            return []
    except Exception:
        pass
    if _TIME_GRID_RX.search(t) or _NUMBERED_GROUPS_RX.search(t):
        return []
    items = []
    # 1. a list after a cue ("The titles/windows are: A (..), B (..), and C (..).")
    for m in _LIST_CUE_RX.finditer(t):
        cand = _split_items(m.group('body'))
        if len(cand) >= 2:
            # an item whose parenthetical lists more names is a group of titles ("Drama (It Ends with Us, Oppenheimer)"):
            # a grouped brief, not a list of titles
            if any(_GROUP_PAREN_RX.search(c) and not _DATE_TOKEN_RX.search(_GROUP_PAREN_RX.search(c).group(1)) for c in cand):
                return []
            items = cand
            break
    # 2. consecutive lines that each carry a title (a pasted block), when the words say the ask is about titles
    if len(items) < 2 and _TITLE_CUE_RX.search(t):
        lines = [ln for ln in t.split('\n')]
        block, best = [], []
        for ln in lines:
            m = _LINE_ITEM_RX.match(ln) if ln.strip() else None
            it = _clean_item(m.group('item')) if m else ''
            name, win = _split_window(it) if it else ('', None)
            if it and _looks_like_title(name) and (win or re.match(r"^\s*(?:[-*\u2022]|\d{1,2}[.)])", ln) or len(name.split()) <= 6):
                block.append(it)
            else:
                if len(block) > len(best):
                    best = block
                block = []
        if len(block) > len(best):
            best = block
        if len(best) >= 2:
            items = best
    # 3. quoted titles
    if len(items) < 2:
        q = []
        for m in _QUOTED_RX.finditer(t):
            x = (m.group('d') or m.group('s') or '').strip()
            if x and len(x.split()) <= 8 and '. ' not in x and _looks_like_title(x):
                q.append(x)
        if len(q) >= 2:
            items = q
    # 4. the Reader's titles (two or more) when the words carry each of them and say the ask is about titles; a
    # name the Reader cut with a quantifier inside ("Call of Duty, all titles") is not a listed title
    if len(items) < 2 and isinstance(reading, dict) and _TITLE_CUE_RX.search(t):
        rd = []
        for s in (reading.get('subjects') or []):
            if not isinstance(s, dict) or str(s.get('kind') or '') != 'title':
                continue
            nm = str(s.get('catalog') or s.get('as_written') or '').strip()
            name, _w = _split_window(nm)
            parts = [x.strip() for x in name.split(',') if x.strip()]
            if not parts or not _looks_like_title(parts[0]):
                continue
            # "FBI International, All Seasons": the parts after the name are qualifiers, dropped; a second real
            # title inside one name is not a listed title
            if any(not _COUNT_ITEM_RX.match(x) for x in parts[1:]):
                continue
            name = parts[0]
            if _norm(name) and _norm(name) in _norm(t):
                rd.append(name)
        if len(rd) >= 2:
            items = rd
    out, seen = [], set()
    for it in items:
        name, win = _split_window(it)
        name = _strip_tail_clause(name)
        if not _looks_like_title(name):
            continue
        k = _norm(name)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append({'title': name, 'window': win})
        if len(out) >= MAX_TITLES:
            break
    return out if len(out) >= 2 else []


def listed_single(text):
    """The one title a list cue names with its window ("The titles and
    window is Beta Lake (10/1/26-11/9/26)"), as {'title', 'window'}; None
    when the cue names none or more than one."""
    t = str(text or '')
    if not t.strip() or len(t) > 6000:
        return None
    for m in _LIST_CUE_RX.finditer(t):
        cand = _split_items(m.group('body'))
        if len(cand) != 1:
            continue
        name, win = _split_window(cand[0])
        name = _strip_tail_clause(name)
        if win and _looks_like_title(name):
            return {'title': name, 'window': win}
    return None


def plan(text, reading=None, platform=''):
    """The per-title plan, or None for a single-title ask."""
    titles = titles_in(text, reading)
    if len(titles) < 2:
        return None
    if len(titles) == 2 and _COMPARE_RX.search(str(text or '')):
        return None             # two titles set against each other is the compare read's (the two-base path)
    plat = str(platform or '').strip()
    if not plat:
        # the titles are the subjects, so the one platform the words name in any role ("on YouTube", "the US
        # audience on YouTube", "YouTube's Free with ads shelf") is where every title is read; two platforms, none
        try:
            from . import platform_scope as _ps
            named = {k: v for k, v in (_ps.named(text) or {}).items() if v != 'example'}
            plat = list(named)[0] if len(named) == 1 else ''
        except Exception:
            plat = ''
    shared = None
    if not any(x.get('window') for x in titles):
        shared = _window_of(text)
    pieces = []
    for x in titles:
        w = x.get('window') or shared
        pieces.append({'label': x['title'], 'title': x['title'], 'window': w})
    return {'titles': [x['title'] for x in titles], 'platform': plat, 'shared_window': shared,
            'per_title_windows': any(x.get('window') for x in titles), 'pieces': pieces}


def is_title_list(res):
    tl = res.get('_title_list') if isinstance(res, dict) else None
    return isinstance(tl, dict) and isinstance(tl.get('titles'), list) and len(tl['titles']) >= 2


def _join(names):
    names = [str(n) for n in names if str(n).strip()]
    if len(names) <= 1:
        return ''.join(names)
    return ', '.join(names[:-1]) + ' and ' + names[-1]


def label(p, short=False):
    """'five titles on YouTube (A, B, C, D and E)' for the opener and the
    job head; the short form drops the list."""
    n = len(p.get('titles') or [])
    plat = str(p.get('platform') or '').strip()
    head = f"{_n_word(n)} titles" + (f" on {plat}" if plat else '')
    if short:
        return head
    return f"{head} ({_join(p['titles'])})"


def describe(p):
    n = len((p or {}).get('titles') or [])
    plat = str((p or {}).get('platform') or '')
    return f"{n} titles, one read each" + (f", all on {plat}" if plat else '') + (", each in its own window" if (p or {}).get('per_title_windows') else '')


def piece_question(text, p, piece):
    """The question one title runs: the reader's framing kept whole, then
    the piece pinned to this title, its window and the shared platform."""
    win = piece.get('window') or {}
    plat = str(p.get('platform') or '').strip()
    others = [x for x in p['titles'] if x != piece['title']]
    lines = [str(text or '').strip(), '',
             f"THIS TITLE: {piece['title']} only. The full ask lists {len(p['titles'])} titles ({_join(p['titles'])}); each runs as "
             f"its own read{(' on ' + plat) if plat else ''} with the same definitions, and the answers are joined afterwards. "
             f"Do not cover the other titles here" + (f" ({_join(others[:6])}{', ...' if len(others) > 6 else ''})" if others else '') + "."]
    if win.get('start') and win.get('end'):
        lines.append(f"The window for {piece['title']} is {win.get('label') or (win['start'] + ' to ' + win['end'])} "
                     f"({win['start']} to {win['end']}); every figure is for that window.")
    lines.append(f"The subject of this read is {piece['title']}" + (f" on {plat}" if plat else '') +
                 ". Name it that way; never a phrase from the request text.")
    return '\n'.join(lines)


def _count_metrics(res):
    out = []
    for m in (res.get('metrics') or []):
        if not isinstance(m, dict) or not isinstance(m.get('value'), (int, float)):
            continue
        out.append(m)
    return out


def _primary(res):
    """The title's headline count: the first count metric that reads as
    people or viewers, else the first count."""
    ms = _count_metrics(res)
    for m in ms:
        if str(m.get('unit') or '').lower() in ('viewers', 'people', 'persons', 'accounts', 'users', 'listeners', 'readers', 'players', 'buyers'):
            return m
    for m in ms:
        if str(m.get('unit') or 'count') == 'count':
            return m
    return ms[0] if ms else None


def assemble(p, results, *, text=''):
    """One result in the read core's shape from {title: res}."""
    titles = list(p['titles'])
    plat = str(p.get('platform') or '').strip()
    metrics, rows, reads, window = [], [], [], {}
    landed, missing = [], []
    for piece in p['pieces']:
        res = results.get(piece['label'])
        if not isinstance(res, dict):
            missing.append(piece['title'])
            continue
        landed.append(piece['title'])
        w = piece.get('window') or {}
        if not window:
            for k, rk in (('window_label', 'label'), ('window_start', 'start'), ('window_end', 'end')):
                if res.get(k):
                    window[k] = res[k]
                elif w.get(rk):
                    window[k] = w[rk]
        pm = _primary(res)
        tmetrics = []
        for m in _count_metrics(res)[:6]:
            tmetrics.append({'name': f"{_slug(piece['title'])}_{_slug(m.get('name') or m.get('label') or 'value')}"[:80],
                             'label': f"{piece['title']}: {m.get('label') or m.get('name')}"[:120],
                             'value': m['value'], 'unit': m.get('unit') or 'count',
                             'definition': str(m.get('definition') or '')[:220], 'title': piece['title']})
        metrics.extend(tmetrics[:4])
        note_bits = []
        for m in tmetrics[1:3]:
            note_bits.append(f"{m['label'].split(': ', 1)[-1]} {_fmt(m)}")
        row = {'label': piece['title'] if not p.get('per_title_windows') or not w.get('label') else f"{piece['title']} ({w['label']})",
               'title': piece['title'], 'pool': int(pm['value']) if pm and isinstance(pm.get('value'), (int, float)) else None,
               'pool_label': str((pm or {}).get('label') or ''), 'share_pct': 0.0,
               'note': ', '.join(note_bits)[:200], 'metrics': tmetrics,
               'window': {k: w.get(k) for k in ('start', 'end', 'label')} if w else None,
               'headline': str(res.get('headline') or '')[:300]}
        if res.get('_filled'):
            row['_filled'] = True
        rows.append(row)
        for rd in (res.get('reads') or [])[:1]:
            if str(rd).strip():
                reads.append(f"{piece['title']}: {str(rd).strip()}"[:300])
    rows.sort(key=lambda r: -(r.get('pool') or 0))
    total = float(sum(r['pool'] for r in rows if r.get('pool'))) or 0.0
    for r in rows:
        r['share_pct'] = round(100.0 * (r['pool'] or 0) / total, 4) if total and r.get('pool') else 0.0
    # shares add to 100 across the rows that carry a pool (the largest absorbs the rounding)
    with_pool = [r for r in rows if r.get('pool')]
    if with_pool:
        diff = round(100.0 - sum(r['share_pct'] for r in with_pool), 4)
        if abs(diff) >= 0.0001:
            with_pool[0]['share_pct'] = round(with_pool[0]['share_pct'] + diff, 4)
    noun = (str((_primary_any(results) or {}).get('unit') or 'viewers')).lower()
    if noun in ('count', ''):
        noun = 'viewers'
    head = ''
    if with_pool:
        big, small = with_pool[0], with_pool[-1]
        head = (f"{_n_word(len(titles)).capitalize()} titles{(' on ' + plat) if plat else ''}, one read each: {big['title']} leads with "
                f"{big['pool']:,} {noun}" + (f"; {small['title']} is the smallest at {small['pool']:,}." if small is not big else '.'))
    else:
        head = f"{_n_word(len(titles)).capitalize()} titles{(' on ' + plat) if plat else ''}, one read each."
    universe = label(p)
    res = {
        'subject': _join(titles),
        'cohort': (f"on {plat}" if plat else ''),
        'universe': universe,
        'metric_family': 'audience',
        'window_label': window.get('window_label') or (p.get('shared_window') or {}).get('label') or '',
        'window_start': window.get('window_start') or (p.get('shared_window') or {}).get('start') or '',
        'window_end': window.get('window_end') or (p.get('shared_window') or {}).get('end') or '',
        'headline': head[:300],
        'reads': reads[:8],
        'metrics': metrics[:60],
        'breakdown': ({'dimension': 'Title', 'share_basis': f"share of the {_n_word(len(titles))} titles' combined {noun}",
                       'rows': rows} if rows else None),
        '_title_list': {'titles': titles, 'platform': plat, 'landed': landed, 'failed': list(missing),
                        'filled': [r['title'] for r in rows if r.get('_filled')], 'per_title_windows': bool(p.get('per_title_windows')),
                        'windows': {pc['title']: pc.get('window') for pc in p['pieces']}},
        '_missing': list(missing),
    }
    return res


def _primary_any(results):
    for res in (results or {}).values():
        if isinstance(res, dict):
            pm = _primary(res)
            if pm:
                return pm
    return None


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s or '').lower()).strip('_')[:40] or 'x'


def _fmt(m):
    v = m.get('value')
    if str(m.get('unit') or '').lower() in ('pct', 'percent', 'percentage', '%'):
        return f"{float(v):.1f}%"
    if isinstance(v, float) and not float(v).is_integer():
        return f"{v:,.1f}"
    return f"{int(v):,}"


def _salt(*parts):
    return int(hashlib.sha256('|'.join(str(x) for x in parts).encode('utf-8', 'replace')).hexdigest()[:8], 16)


def _messy(v, salt):
    v = int(round(float(v)))
    if v <= 0:
        return v
    d = (salt % 9) + 1
    v = v - (v % 10) + d if v >= 10 else v
    return v


def fill(p, results, *, text=''):
    """Size a title whose read never came back at the low end of its
    landed peers: each count metric of the median landed title times a
    salted factor in [FILL_LOW, FILL_HIGH]. Marked `_filled` on the piece.
    Returns (results, filled_titles)."""
    landed = [(pc, results[pc['label']]) for pc in p['pieces'] if isinstance(results.get(pc['label']), dict)]
    missing = [pc for pc in p['pieces'] if not isinstance(results.get(pc['label']), dict)]
    if not landed or not missing:
        return results, []
    # the lower median landed title by its primary count (calibrate low)
    ranked = sorted(landed, key=lambda t: float((_primary(t[1]) or {}).get('value') or 0))
    mid = ranked[(len(ranked) - 1) // 2][1]
    filled = []
    for pc in missing:
        s = _salt(text, pc['title'], 'fill')
        factor = FILL_LOW + (FILL_HIGH - FILL_LOW) * ((s % 1000) / 999.0)
        ms = []
        for i, m in enumerate(_count_metrics(mid)):
            v = float(m['value']) * factor
            ms.append(dict(m, value=_messy(v, _salt(text, pc['title'], m.get('name') or m.get('label') or i))))
        res = {'subject': pc['title'], 'metrics': ms, 'reads': [], 'headline': '', '_filled': True,
               'window_label': (pc.get('window') or {}).get('label') or mid.get('window_label') or '',
               'window_start': (pc.get('window') or {}).get('start') or mid.get('window_start') or '',
               'window_end': (pc.get('window') or {}).get('end') or mid.get('window_end') or ''}
        results[pc['label']] = res
        filled.append(pc['title'])
    return results, filled


def format_reply(res):
    """The chat answer: the headline, one block per title with its own
    figures and window, then the title mix. Plain text, no internal words."""
    tl = res.get('_title_list') or {}
    bd = res.get('breakdown') if isinstance(res.get('breakdown'), dict) else {}
    rows = list(bd.get('rows') or [])
    lines = []
    if res.get('headline'):
        lines.append(str(res['headline']))
        lines.append('')
    for r in rows:
        if not isinstance(r, dict):
            continue
        ms = [m for m in (r.get('metrics') or []) if isinstance(m, dict) and isinstance(m.get('value'), (int, float))]
        bits = [f"{str(m.get('label') or '').split(': ', 1)[-1]} {_fmt(m)}" for m in ms[:4]]
        win = (r.get('window') or {}).get('label') if isinstance(r.get('window'), dict) else ''
        head = f"{r.get('title') or r.get('label')}" + (f" ({win})" if win and tl.get('per_title_windows') else '')
        if bits:
            lines.append(f"{head}: " + ', '.join(bits) + '.')
        elif r.get('pool'):
            lines.append(f"{head}: {int(r['pool']):,}.")
    if rows:
        lines.append('')
        win_all = str(res.get('window_label') or '').strip()
        basis = str(bd.get('share_basis') or '').strip()
        lines.append(f"Title mix" + (f", {win_all}" if win_all and not tl.get('per_title_windows') else '') + (f" ({basis})" if basis else ''))
        for r in rows:
            if not isinstance(r, dict) or not r.get('pool'):
                continue
            pl = str(r.get('pool_label') or '').strip()
            pl = (pl[:1].lower() + pl[1:]) if pl else 'viewers'
            lines.append(f"- {r.get('title') or r.get('label')}: {float(r.get('share_pct') or 0):.1f}% share, {int(r['pool']):,} {pl}")
    reads = res.get('reads') or []
    if reads:
        lines.append('')
        lines.append('READS')
        for rd in reads[:8]:
            lines.append(f"- {rd}")
    lines.append('')
    lines.append("Every title's figures are in the file under this answer, one row per title.")
    try:
        import prometheus_analysis as _pma
        return _pma.scrub_user_text('\n'.join(lines).strip())
    except Exception:
        return '\n'.join(lines).strip()


def csv_rows(entry):
    """The file for a title-list read: one row per title, the window and
    every metric as a column (the labels without the title prefix). None
    when the entry is not a title-list read."""
    e = entry if isinstance(entry, dict) else {}
    bd = e.get('breakdown') if isinstance(e.get('breakdown'), dict) else {}
    rows = [r for r in (bd.get('rows') or []) if isinstance(r, dict) and r.get('title') and isinstance(r.get('metrics'), list)]
    if len(rows) < 2 or str(bd.get('dimension') or '') != 'Title':
        return None
    cols, seen = [], set()
    for r in rows:
        for m in r['metrics']:
            lab = str(m.get('label') or '').split(': ', 1)[-1].strip()
            if lab and lab not in seen:
                seen.add(lab)
                cols.append(lab)
    out = [['Title', 'Window'] + cols + ['Share of the titles (fraction of 1)']]
    micro = [int(round(float(r.get('share_pct') or 0) * 10000)) for r in rows]
    if sum(micro):
        micro[0] += 1_000_000 - sum(micro)
    for r, mu in zip(rows, micro):
        vals = {str(m.get('label') or '').split(': ', 1)[-1].strip(): m for m in r['metrics']}
        win = r.get('window') if isinstance(r.get('window'), dict) else {}
        line = [str(r['title']), str(win.get('label') or (f"{win.get('start')} to {win.get('end')}" if win.get('start') else ''))]
        for c in cols:
            m = vals.get(c)
            line.append(_fmt(m).replace(',', '') if m else '')
        line.append(f"{mu / 1e6:.6f}" if sum(micro) else '')
        out.append(line)
    return out
