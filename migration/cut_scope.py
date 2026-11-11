"""What a cut label is about: a part of the content, or a family size.

2026-11-11, Sony Pictures Television: the cut 'My Life with the Walter
Boys Viewers on Netflix - Season 3 Viewers' was built at 5.4% of its
parent. The engine read the label for a brand, found the token '3',
matched it to the parent's NUMBER_OF_CHILDREN row whose value is '3',
capped the cut at that row's share (5.9273%) and pinned the row at 100.
A season cut was sized as the people with three children.

Three readings of a label live here, so the cut engine never guesses:

* ``content_scope(label)``: a season, episode, part, volume or chapter
  cut ('Season 3 Viewers', 'Season 2', 'Episode 4 viewers', 'S2E4
  viewers', 'Part 2', 'Volume 1', 'Chapter 5', 'the final season'). A
  content scope is a slice of who watched (read, played) the parent; it
  is never a demographic pin and never a brand, so it takes no brand
  ceiling and pins no brand row.
* ``scope_row_fraction(df, scope, subject)``: the cut's size from the
  parent's own row for that season (or episode ...) when the parent
  carries one. That row is the count of the parent's people in the
  scope, so its share of the parent is the cut's fraction. When the
  parent has no such row the reasoned fraction applies.
* ``family_size_bucket(label)`` / ``family_size_pin(df, label)``: a
  label that IS about family size ('3 children', '3 kids', 'Parents of
  3', 'families with 4 or more kids') resolves to the parent's
  NUMBER_OF_CHILDREN bucket and is a demographic pin, sized off that
  bucket like every other demographic cut.

``is_demographic_category(name)`` is the full list of categories that
describe people rather than things they use (the nine canonical
demographic categories plus the family and language categories the
profiles carry). A brand lookup never matches a row in one of them.

Pure: regular expressions and a DataFrame read. No model, no network.
Regression: scripts/test_cut_scope_season.py.
"""
from __future__ import annotations

import re

__all__ = [
    "content_scope", "strip_content_scope", "scope_row_fraction",
    "family_size_bucket", "family_size_pin", "is_demographic_category",
    "DEMOGRAPHIC_CATEGORIES", "SCOPE_FILLER",
]

# Categories that describe the people, never a brand they use. Space
# and underscore spellings both appear on shipped files.
DEMOGRAPHIC_CATEGORIES = frozenset({
    "GENDER", "AGE", "ETHNICITY", "EDUCATION", "INCOME", "OCCUPATION",
    "PARENTAL STATUS", "PARENTAL_STATUS", "RELATIONSHIP",
    "SEXUAL ORIENTATION", "SEXUAL_ORIENTATION", "LOCATION",
    "NUMBER OF CHILDREN", "NUMBER_OF_CHILDREN",
    "AGE OF CHILDREN", "AGE_OF_CHILDREN",
    "PRIMARY LANGUAGE", "PRIMARY_LANGUAGE",
    "HOUSEHOLD SIZE", "HOUSEHOLD_SIZE", "MARITAL STATUS", "MARITAL_STATUS",
    "GENERATION", "REGION",
})


def is_demographic_category(name) -> bool:
    return re.sub(r"\s+", " ", str(name or "").strip().upper()) in DEMOGRAPHIC_CATEGORIES \
        or str(name or "").strip().upper().replace(" ", "_") in DEMOGRAPHIC_CATEGORIES


_WORD_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}
_ORDINAL = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
_UNIT = {
    "season": "season", "seasons": "season",
    "episode": "episode", "episodes": "episode", "ep": "episode",
    "eps": "episode",
    "part": "part", "parts": "part",
    "volume": "volume", "volumes": "volume", "vol": "volume",
    "vols": "volume",
    "chapter": "chapter", "chapters": "chapter",
}
_N = r"\d{1,3}|" + "|".join(_WORD_NUM)
_UNITS = "|".join(sorted(_UNIT, key=len, reverse=True))
# 'Season 3', 'Seasons 1-2', 'Season 1 and 2', 'Episode 4', 'Vol. 2'
_UNIT_NUM_RX = re.compile(
    r"\b(?P<unit>" + _UNITS + r")\.?\s*(?P<n>" + _N + r")"
    r"(?P<more>(?:\s*(?:-|to|through|and|&|,)\s*(?:" + _N + r"))*)\b",
    re.IGNORECASE)
# '3rd season', 'third season', 'the final season'
_ORD_UNIT_RX = re.compile(
    r"\b(?P<ord>\d{1,2}(?:st|nd|rd|th)|" + "|".join(_ORDINAL)
    + r"|final|last|latest|newest|current|premiere|debut)\s+"
    r"(?P<unit>" + _UNITS + r")\b", re.IGNORECASE)
# 'S3', 'S2E4' - only when the label carries nothing else (see below)
_SXE_RX = re.compile(r"\bs(?P<s>\d{1,2})(?:\s*e(?P<e>\d{1,3}))?\b",
                     re.IGNORECASE)

# Words that say who, not what (the cut engine's behavioral nouns plus
# joiners). A label that is only these around an 'S3' is a season cut;
# 'Galaxy S3 owners' is not.
_FILLER = {
    "viewer", "viewers", "watcher", "watchers", "fan", "fans",
    "audience", "audiences", "reader", "readers", "listener",
    "listeners", "player", "players", "streamer", "streamers",
    "people", "who", "watched", "watch", "saw", "read", "played",
    "the", "of", "only", "just", "a", "an", "on", "in", "for",
    "finishers", "completers", "starters", "bingers",
}
SCOPE_FILLER = frozenset(_FILLER)


def _to_int(tok):
    t = str(tok or "").strip().lower()
    if t.isdigit():
        return int(t)
    return _WORD_NUM.get(t)


def content_scope(label):
    """The content scope a cut label names, or None.

    Returns {'unit': 'season' | 'episode' | 'part' | 'volume' |
    'chapter', 'numbers': [int, ...], 'text': the matched words,
    'span': (start, end)}. 'numbers' is empty for a scope named without
    a number ('the final season'). A label about children, kids or
    family size is never a content scope.
    """
    s = str(label or "")
    if not s.strip():
        return None
    if family_size_bucket(s) is not None:
        return None
    m = _UNIT_NUM_RX.search(s)
    if m:
        nums = []
        for tok in re.findall(_N, m.group("n") + " " + (m.group("more") or ""),
                              flags=re.IGNORECASE):
            v = _to_int(tok)
            if v is not None and v not in nums:
                nums.append(v)
        more = (m.group("more") or "").strip().lower()
        if len(nums) == 2 and re.match(r"^(?:-|to|through)", more) \
                and nums[1] > nums[0] and nums[1] - nums[0] <= 40:
            nums = list(range(nums[0], nums[1] + 1))
        return {"unit": _UNIT[m.group("unit").lower()], "numbers": nums,
                "text": m.group(0), "span": m.span()}
    m = _ORD_UNIT_RX.search(s)
    if m:
        o = m.group("ord").lower()
        n = _ORDINAL.get(o)
        if n is None and o[:1].isdigit():
            n = int(re.match(r"\d+", o).group(0))
        return {"unit": _UNIT[m.group("unit").lower()],
                "numbers": [n] if n is not None else [],
                "text": m.group(0), "span": m.span()}
    m = _SXE_RX.search(s)
    if m:
        rest = (s[:m.start()] + " " + s[m.end():]).lower()
        words = [w for w in re.split(r"[^a-z0-9]+", rest) if w]
        if all(w in _FILLER for w in words):
            if m.group("e"):
                return {"unit": "episode", "numbers": [int(m.group("e"))],
                        "season": int(m.group("s")),
                        "text": m.group(0), "span": m.span()}
            return {"unit": "season", "numbers": [int(m.group("s"))],
                    "text": m.group(0), "span": m.span()}
    return None


def strip_content_scope(label) -> str:
    """The label without its content-scope words ('Netflix Season 3
    Viewers' -> 'Netflix Viewers'); the label itself when it names no
    content scope."""
    s = str(label or "")
    sc = content_scope(s)
    if not sc:
        return s
    a, b = sc["span"]
    return re.sub(r"\s+", " ", (s[:a] + " " + s[b:])).strip(" -:,")


# ---------------------------------------------------------------------------
# family size
# ---------------------------------------------------------------------------
_KIDS = r"(?:kids?|children|child)"
_WHO = (r"(?:parents?|families|family|households?|homes?|moms?|dads?|"
        r"mothers?|fathers?|people|adults|viewers|fans)")
_PLUS = r"(?P<plus>\s*\+|\s+or\s+more|\s+plus)?"
_FAMILY_RXS = (
    # 'Parents of 3', 'families with 3 kids', 'households with 4 or more children'
    re.compile(r"^\s*" + _WHO + r"\s+(?:of|with)\s+(?P<n>" + _N + r")" + _PLUS
               + r"(?:\s+" + _KIDS + r")?\s*$", re.IGNORECASE),
    # '3 children', '3 kids', '4+ kids', '3-child households', 'three children'
    re.compile(r"^\s*(?P<n>" + _N + r")" + _PLUS + r"[\s-]*" + _KIDS
               + r"(?:\s+" + _WHO + r")?\s*$", re.IGNORECASE),
)
_NO_KIDS_RX = re.compile(
    r"^\s*(?:no\s+" + _KIDS + r"|childless|child[- ]free|without\s+" + _KIDS
    + r"|" + _WHO + r"\s+(?:with\s+no|without)\s+" + _KIDS + r")\s*$",
    re.IGNORECASE)


def family_size_bucket(label):
    """(n, plus) when the label is a family-size cut ('3 children' ->
    (3, False), '4+ kids' -> (4, True), 'no kids' -> (0, False)); None
    otherwise. A season, an episode or any other number in a label is
    never a family size: the label has to be about children."""
    s = str(label or "")
    if not s.strip():
        return None
    if _NO_KIDS_RX.match(s):
        return (0, False)
    for rx in _FAMILY_RXS:
        m = rx.match(s)
        if m:
            n = _to_int(m.group("n"))
            if n is None:
                return None
            return (n, bool(m.group("plus")))
    return None


def _children_bucket_value(row_label):
    """(n, plus) for a NUMBER_OF_CHILDREN row label ('3' -> (3, False),
    '4+' -> (4, True), 'No Kids' / 'None' -> (0, False)); None when the
    label is not a count."""
    t = str(row_label or "").strip().lower()
    if t in ("none", "no kids", "no children", "0 children", "zero"):
        return (0, False)
    m = re.match(r"^(\d{1,2})\s*(\+|or more|plus)?\s*(?:kids?|children|child)?$", t)
    if m:
        return (int(m.group(1)), bool(m.group(2)))
    m = re.match(r"^(" + "|".join(_WORD_NUM) + r")\s*(?:kids?|children|child)?$", t)
    if m:
        return (_WORD_NUM[m.group(1)], False)
    return None


def family_size_pin(df_source, label):
    """(category, [row labels]) on the parent for a family-size label,
    or None. '3 children' pins the row that counts exactly three; a
    count at or past the parent's open-ended row ('4+') pins that row;
    '2 or more' pins every row from two up."""
    want = family_size_bucket(label)
    if want is None:
        return None
    n, plus = want
    try:
        cats = df_source["Column"].astype(str).str.upper().str.strip()
    except Exception:
        return None
    for cat in ("NUMBER_OF_CHILDREN", "NUMBER OF CHILDREN"):
        mask = cats == cat
        if not mask.any():
            continue
        rows = []
        for v in df_source.loc[mask, "Value"].astype(str):
            bv = _children_bucket_value(v)
            if bv is not None:
                rows.append((v.strip(), bv[0], bv[1]))
        if not rows:
            return None
        picked = []
        for lbl, rn, rplus in rows:
            if plus:
                if rn >= n or (rplus and rn <= n):
                    picked.append(lbl)
            elif rn == n and not rplus:
                picked.append(lbl)
            elif rplus and n >= rn and not any(r2 == n and not p2 for _l, r2, p2 in rows):
                picked.append(lbl)
        if picked:
            return cat, list(dict.fromkeys(picked))
        return None
    return None


# ---------------------------------------------------------------------------
# the parent's own row for the scope
# ---------------------------------------------------------------------------
_META_CATEGORIES = {
    "", "BRAND INPUT", "SAMPLE SIZE", "SUBJECT", "BRAND CATEGORY",
    "INPUT_METADATA", "AVID FAN", "CASUAL FAN", "BRAND ID", "REPORT INPUT",
}
_SUBJECT_NOISE = {
    "viewers", "viewer", "fans", "fan", "on", "the", "of", "and", "a",
    "netflix", "hulu", "max", "hbo", "prime", "video", "amazon", "disney",
    "peacock", "paramount", "plus", "apple", "tv", "youtube", "series",
    "show", "audience", "watchers",
}


def _fnum(v):
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _bp_column(df):
    for c in df.columns:
        if str(c).strip().lower().startswith("brand penetration"):
            return c
    return None


def scope_row_fraction(df_source, scope, subject=""):
    """(cohort_fraction, row_value, category, bp) from the parent's own
    row for this content scope, or (None, '', '', 0.0) when the parent
    carries none.

    A row is the scope's row when its value names the same unit and the
    same single number ('Season 3', 'My Life with the Walter Boys:
    Season 3', 'S3') and the rest of the value is nothing, or words of
    the parent's own subject. A row that names another title's season is
    never read. A scope of several numbers or of no number is not read
    off one row; the reasoned fraction applies there.
    """
    none = (None, "", "", 0.0)
    try:
        if not scope or len(scope.get("numbers") or []) != 1:
            return none
        unit, num = scope["unit"], int(scope["numbers"][0])
        bp_col = _bp_column(df_source)
        if bp_col is None:
            return none
        subj_words = {w for w in re.split(r"[^a-z0-9]+", str(subject or "").lower()) if w}
        best = none
        cats = df_source["Column"].astype(str).str.upper().str.strip()
        for cat, val, bp in zip(cats, df_source["Value"].astype(str), df_source[bp_col]):
            if cat in _META_CATEGORIES or is_demographic_category(cat):
                continue
            sc = content_scope(val)
            if not sc or sc["unit"] != unit or sc.get("numbers") != [num]:
                continue
            if scope.get("season") is not None and sc.get("season") not in (None, scope.get("season")):
                continue
            rest = strip_content_scope(val).lower()
            rest_words = {w for w in re.split(r"[^a-z0-9]+", rest) if w}
            if rest_words - subj_words - _SUBJECT_NOISE - _FILLER:
                continue        # another title's season
            v = _fnum(bp)
            if v is None or v <= 0 or v > 100:
                continue
            own_cat = cat.rstrip("S") in ("SEASON", "EPISODE", "PART", "VOLUME", "CHAPTER")
            score = (1 if own_cat else 0, v)
            if best[0] is None or score > best[4]:
                best = (max(0.005, min(0.999, v / 100.0)), str(val).strip(), cat, float(v), score)
        return best[:4] if best[0] is not None else none
    except Exception:
        return none
