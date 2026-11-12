"""Two saved journeys under one show's name: the words that tell them
apart pick one (Jenna 2026-11-11).

The library held two Abbott Elementary journeys the same evening:

    Abbott Elementary Season 6 on Hulu, Hulu on Disney+ and the ABC app
    Abbott Elementary Library (Seasons 1 to 5) on Hulu, Hulu on Disney+ and HBO Max

and "the Abbott Elementary library journey" resolved to neither. Three
readers of a journey's name each failed their own way:

  * ``journey_lane.find_journey`` wanted every word of a journey's title
    in the question, or one long word no other journey carries; the show's
    name is in both titles and "library" is seven letters;
  * ``send_file.match_journeys`` and ``wayfinding._match`` drop the word
    library before they match (it usually means "in my library"), so the
    ask named both and drew "I hold more than one journey by that name".

The rule, one place:

  1. The journeys in play are the ones whose own name (the words before
     the first qualifier: Season, Library, Part, a number, "on <platform>")
     is in the reader's words.
  2. One in play is the answer.
  3. Several in play are told apart by the words that differ between
     them and that the reader used: a word only one of them carries
     (library, a season number, a platform), a season number that falls
     inside one's range ("season 3" is inside "Seasons 1 to 5"), and the
     plain senses of library (library, catalog, back catalog, older /
     earlier / past / previous seasons, reruns) and current (current, new,
     newest, latest, this season, in season, now airing, premiere), where
     current means the highest season a title carries and never the one
     called a library.
  4. A clear winner is the answer. A tie is ONE short question that names
     the journeys apart by those same words, with a chip for each.

``pick(words, items)``     -> {'item': one, 'how': 'only'|'told_apart'} or
                              {'ask': question, 'options': [names], 'tied': [items]}
                              or {} when the words name no journey.
``narrow(words, items)``   the list a caller that asks its own question
                           can use: the one picked, else the tied ones,
                           else [].

``items`` are dicts carrying a name under ``name``, ``project_name`` or
``target``. Pure text; no model, no S3, no host. Never raises.
Regression: scripts/test_pm_truth_2026_11_11.py.
"""
from __future__ import annotations

import re

__all__ = ['pick', 'narrow', 'name_of', 'own_name', 'qualifier']

_STOP = frozenset((
    'the', 'a', 'an', 'of', 'on', 'in', 'at', 'to', 'for', 'and', 'or', 'with', 'from', 'by', 'my', 'your', 'our', 'me', 'us',
    'journey', 'journeys', 'digital', 'iq', 'watch', 'file', 'workbook', 'excel', 'xlsx', 'csv', 'spreadsheet', 'breakdown',
    'report', 'read', 'one', 'that', 'this', 'it', 'is', 'are', 'was', 'what', 'which', 'where', 'how', 'does', 'did', 'do',
    'say', 'says', 'show', 'shows', 'open', 'email', 'send', 'please', 'can', 'you', 'i', 'we', 'about', 'dashboard'))
# the words that open the part of a journey's name that tells it from its siblings
_QUAL_OPEN_RX = re.compile(
    r"\s+(?:\(|season\b|seasons\b|library\b|catalog(?:ue)?\b|part\b|volume\b|vol\b|chapter\b|episode\b|episodes\b|"
    r"s\d{1,2}\b|on\s+[A-Z0-9]|premiere\b|finale\b|launch\b|relaunch\b|year\s+\d|(?:19|20)\d\d\b|[-:,]\s)", re.I)
_LIBRARY_RX = re.compile(
    r"\b(?:library|catalog(?:ue)?|back[- ]catalog(?:ue)?|older seasons?|earlier seasons?|past seasons?|previous seasons?|"
    r"prior seasons?|old seasons?|first (?:five|four|three|two|\d+) seasons|reruns?|rewatch(?:es|ing)?)\b", re.I)
_CURRENT_RX = re.compile(
    r"\b(?:current|new(?:est)?|latest|most recent|this season|in[- ]season|now airing|airing now|this year'?s?|"
    r"premiere|new season|current season|latest season)\b", re.I)
_SEASON_NO_RX = re.compile(r"\bseasons?\s*(\d{1,2})\b|\bs(\d{1,2})\b", re.I)
_RANGE_RX = re.compile(r"\bseasons?\s*(\d{1,2})\s*(?:to|through|thru|-|\u2013|\u2014|and)\s*(\d{1,2})\b", re.I)


def name_of(item):
    if not isinstance(item, dict):
        return ''
    return ' '.join(str(item.get('name') or item.get('project_name') or item.get('target') or '').split())


def _toks(s):
    return [w for w in re.findall(r"[a-z0-9+]+", str(s or '').lower()) if w not in _STOP]


def own_name(name):
    """The show, brand or person a journey is about: its name before the
    first qualifier ('Abbott Elementary' of 'Abbott Elementary Library
    (Seasons 1 to 5) on Hulu ...')."""
    n = ' '.join(str(name or '').split())
    m = _QUAL_OPEN_RX.search(n)
    head = n[:m.start()] if m and m.start() >= 3 else n
    return head.strip(' -:,(')


def qualifier(name):
    """What follows the journey's own name: the part that tells it from
    another journey on the same subject."""
    n = ' '.join(str(name or '').split())
    return n[len(own_name(n)):].strip(' -:,')


def _seasons(name):
    """(single season numbers, (lo, hi) ranges) a name states."""
    q = str(name or '')
    ranges = [(min(int(a), int(b)), max(int(a), int(b))) for a, b in _RANGE_RX.findall(q)]
    covered = set()
    for lo, hi in ranges:
        covered.update((lo, hi))
    singles = [int(a or b) for a, b in _SEASON_NO_RX.findall(q)]
    singles = [s for s in singles if not any(lo <= s <= hi for lo, hi in ranges) or s not in covered]
    return singles, ranges


def _is_library(name):
    return bool(re.search(r"\b(?:library|catalog(?:ue)?)\b", str(name or ''), re.I)) or bool(_seasons(name)[1])


def _in_play(words, items):
    have = set(_toks(words))
    out = []
    for it in items or []:
        core = _toks(own_name(name_of(it)))
        if core and all(w in have for w in core) and any(len(w) >= 3 for w in core):
            out.append(it)
    return out


def _short_label(item, group):
    """How a reader tells this journey from the others in the group: its
    qualifier up to the platform ('Season 6', 'Library (Seasons 1 to 5)')."""
    q = qualifier(name_of(item))
    q = re.split(r"\s+on\s+(?=[A-Z0-9])", q)[0].strip(' -:,')
    return q or name_of(item)


def _score(words, item, group):
    """How strongly the reader's words point at this journey rather than
    the others in play."""
    w = ' '.join(str(words or '').split())
    have = set(_toks(w))
    name = name_of(item)
    mine = set(_toks(qualifier(name)))
    score = 0.0
    # a word of the qualifier the reader used that not every journey in play carries
    for tok in mine & have:
        carriers = sum(1 for g in group if tok in set(_toks(qualifier(name_of(g)))))
        if carriers < len(group):
            score += 2.0 if not tok.isdigit() else 0.5
    singles, ranges = _seasons(name)
    asked = [int(a or b) for a, b in _SEASON_NO_RX.findall(w)]
    for n in asked:
        if n in singles:
            score += 3.0
        elif any(lo <= n <= hi for lo, hi in ranges):
            score += 2.5
        else:
            score -= 2.0            # the reader named a season this journey does not cover
    if _LIBRARY_RX.search(w) and not asked:
        score += 3.0 if _is_library(name) else -1.0
    if _CURRENT_RX.search(w) and not asked and not re.search(r"\blibrary\b", w, re.I):
        top = max((max(_seasons(name_of(g))[0] or [0]) for g in group), default=0)
        mine_top = max(singles or [0])
        if not _is_library(name) and (mine_top == top and top > 0 or not any(_seasons(name_of(g))[0] for g in group)):
            score += 3.0
        elif _is_library(name):
            score -= 1.0
    return score


def pick(words, items):
    """The journey the words point at, or the one short question."""
    try:
        group = _in_play(words, items)
        if not group:
            return {}
        if len(group) == 1:
            return {'item': group[0], 'how': 'only'}
        # an exact name wins alone
        want = _toks(words)
        exact = [g for g in group if _toks(name_of(g)) == want]
        if len(exact) == 1:
            return {'item': exact[0], 'how': 'only'}
        scored = sorted(((round(_score(words, g, group), 2), n, g) for n, g in enumerate(group)), key=lambda x: (-x[0], x[1]))
        best, runner = scored[0], scored[1]
        if best[0] > 0 and best[0] > runner[0]:
            return {'item': best[2], 'how': 'told_apart'}
        tied = [g for s, _n, g in scored if s == best[0]] if best[0] > 0 else group
        tied = tied[:4]
        subject = own_name(name_of(tied[0]))
        labels = [_short_label(g, tied) for g in tied]
        if len(labels) == 2:
            listed = f"{labels[0]} or {labels[1]}"
        else:
            listed = ', '.join(labels[:-1]) + f" or {labels[-1]}"
        return {'ask': f"Which {subject} journey: {listed}?", 'options': [name_of(g) for g in tied], 'tied': tied,
                'subject': subject}
    except Exception:
        return {}


def narrow(words, items):
    """[the one picked], the tied ones, or [] when the words name none."""
    got = pick(words, items)
    if got.get('item') is not None:
        return [got['item']]
    return list(got.get('tied') or [])
