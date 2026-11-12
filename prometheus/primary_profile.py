"""The primary profile is the default. A year cut is the subject only
when the reader's words name its year (2026-11-12).

A subject on the dashboard has one primary: its newest Total Universe,
shown under the plain name ("Reba McEntire", "Costco"). An older window
sits beside it as a year cut: "<Name> - <YYYY> Total Universe",
"<Name> - <YYYY> Avid Fan" (`profile-date-refresh-primary`). The cut
never displaces the primary.

What went wrong: a read on "Reba McEntire fans" ran on "Reba McEntire -
2024 Total Universe". The library subjects the Reader is shown for an
ask list the year cuts next to the primary, the model picked one, and
the pick was carried to the file a read runs on as written. Nothing in
code said which of a family's files is the default. The other way
round was broken too: "what did Costco shoppers buy in 2025" ran on the
current file, because a year in the words was never matched to the
family's year cut.

The rule, held here and called at the two places a subject is bound to a
library profile (the Reader's library pick, `prometheus.reader`; the
file a read runs on, `_pm_generation_base`):

  * a year cut with no such year in the words gives way to the family's
    default (the primary; for a year cut of a tier, the tier's own file
    when there is one: "Costco - 2025 Avid Fan" gives way to "Costco -
    Avid Fan");
  * a primary, or a tier, with one year named in the words gives way to
    that year's cut when the family has one;
  * a year cut that is the only file on record for its family stands;
  * the profile open on the reader's screen is never taken away from
    them: a year cut that is open stays the base of "what do they buy".

A year is named by the words when it stands on its own ("in 2025", "the
2025 file", "2025 Costco shoppers"). A year inside a date ("October 31,
2026", "10/31/2026"), a span ("2025-26") and a year that is part of the
subject's own name ("2026 Florida Gubernatorial Voters") do not name a
year cut. Two different years in the words name no single cut.

Nothing here raises: on any error the name or the base is returned as
it came.
"""
from __future__ import annotations

import re
import time

__all__ = ['year_cut', 'years_named', 'settle_name', 'settle_base', 'held', 'library_names']

_YEAR_CUT_RX = re.compile(
    r"^(?P<family>.+?)\s+-\s+(?:YTD\s+)?(?P<year>(?:19|20)\d\d)(?:\s+(?P<rest>.+))?$", re.I)
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DATE_RX = re.compile(
    r"\b" + _MONTH + r"\.?\s+(?:\d{1,2}(?:st|nd|rd|th)?\s*,?\s+)?(?:19|20)\d\d\b|"       # October 31, 2026 / Oct 2026
    r"\b\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTH + r"\.?,?\s+(?:19|20)\d\d\b|"                 # 31 October 2026
    r"\b\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d\d\b|"                                             # 10/31/2026
    r"\b(?:19|20)\d\d-\d{1,2}(?:-\d{1,2})?\b|"                                              # 2026-10-31, 2025-26
    r"\b(?:19|20)\d\d/\d{2,4}\b", re.I)                                                     # 2025/26
_YEAR_RX = re.compile(r"(?<![\w$.,/-])((?:19|20)\d\d)(?![\w/-]|[.,]\d)")


def _clean(s):
    return ' '.join(str(s or '').split())


def year_cut(name):
    """{'family', 'year', 'rest'} for "<Name> - <YYYY> Total Universe",
    "<Name> - <YYYY> Avid Fan", "<Name> - <YYYY> YTD ...", "<Name> -
    <YYYY>"; None for anything else."""
    m = _YEAR_CUT_RX.match(_clean(name))
    if not m:
        return None
    return {'family': m.group('family').strip(), 'year': m.group('year'), 'rest': _clean(m.group('rest') or '')}


def years_named(words, family=''):
    """The years the words name on their own: never a year inside a date
    or a span, never a year that is part of the subject's own name."""
    t = _DATE_RX.sub(' ', str(words or ''))
    own = set(_YEAR_RX.findall(str(family or '')))
    return {y for y in _YEAR_RX.findall(t) if y not in own}


def _tier(rest):
    """The tier a year cut's suffix names, '' for the Total Universe:
    "YTD Avid Male Fan" -> "Avid Male Fan"."""
    r = re.sub(r"^YTD\b\s*", '', _clean(rest), flags=re.I)
    return '' if not r or r.lower() == 'total universe' else r


def settle_name(name, words, names=None):
    """The library name a subject binds to, by the rule above. `names`
    is the library's display names; None when there is no library to ask
    (a year cut with no year in the words then gives way to the family's
    plain name, and a year in the words changes nothing)."""
    try:
        n = _clean(name)
        if not n:
            return name
        lib = None if names is None else {_clean(x).lower(): _clean(x) for x in names if _clean(x)}
        yc = year_cut(n)
        if yc:
            fam = yc['family']
            if yc['year'] in years_named(words, fam):
                return name
            tier = _tier(yc['rest'])
            if lib is None:
                return f"{fam} - {tier}" if tier else fam
            if tier and f"{fam} - {tier}".lower() in lib:
                return lib[f"{fam} - {tier}".lower()]
            if fam.lower() in lib:
                return lib[fam.lower()]
            return name                      # the only file on record for the family
        if lib is None:
            return name
        fam, _sep, suffix = n.partition(' - ')
        fam, suffix = fam.strip(), suffix.strip()
        named = years_named(words, fam)
        if len(named) != 1:
            return name
        y = next(iter(named))
        if suffix:
            cands = [f"{fam} - {y} {suffix}", f"{fam} - {y} YTD {suffix}"]
        else:
            cands = [f"{fam} - {y} Total Universe", f"{fam} - {y} YTD Total Universe", f"{fam} - {y}",
                     f"{fam} - {y} YTD", f"{fam} - YTD {y}"]
        for c in cands:
            if c.lower() in lib:
                return lib[c.lower()]
        return name
    except Exception:
        return name


_STAMP_RX = re.compile(r"_(\d{2})_(\d{2})_(\d{4})_(\d{2})_(\d{2})\.csv$", re.I)


def _newest(entries):
    """Of several files under one display name, the newest by the build
    stamp in the file name, then by the registry date."""
    def rank(e):
        m = _STAMP_RX.search(str(e.get('s3_key') or ''))
        stamp = (m.group(3), m.group(1), m.group(2), m.group(4), m.group(5)) if m else ('', '', '', '', '')
        return (stamp, str(e.get('last_modified') or ''))
    return max(entries, key=rank)


def settle_base(base, words, catalog):
    """The base of a read, by the rule. `base` is what the base selection
    returned ({'subject', 's3_key', 'source'}); `catalog` is the chat
    catalog list or a callable returning it. A base that is not a library
    profile (an open product, a Subscriber IQ run, none) is returned as
    it came; the open page keeps its year cut."""
    try:
        if not isinstance(base, dict):
            return base
        src = str(base.get('source') or '')
        key = str(base.get('s3_key') or '')
        if src not in ('catalog', 'page') or not key or key.startswith('subiq:'):
            return base
        entries = [e for e in (catalog() if callable(catalog) else (catalog or [])) if isinstance(e, dict) and e.get('s3_key')]
        ent = next((e for e in entries if str(e.get('s3_key')) == key), None)
        if not ent:
            return base
        dn = _clean(ent.get('display_name'))
        if not dn:
            return base
        if src == 'page' and year_cut(dn):
            return base                      # what the reader has open is theirs
        new = settle_name(dn, words, [e.get('display_name') for e in entries])
        if _clean(new).lower() == dn.lower():
            return base
        same = [e for e in entries if _clean(e.get('display_name')).lower() == _clean(new).lower()]
        if not same:
            return base
        hit = _newest(same)
        print(f"[pm-base] {dn!r} gives way to {hit.get('display_name')!r} "
              f"({'the words name no year' if year_cut(dn) else 'the words name the year'})")
        return {'subject': _clean(hit.get('subject') or hit.get('display_name')), 's3_key': str(hit.get('s3_key')), 'source': 'catalog'}
    except Exception:
        return base


def held(fn, catalog):
    """`fn` (the base selection) with the rule applied to what it
    returns. The words are the ask; a subject the reader bound themselves
    (a chip, an approved quote) rides in the ask the caller hands over."""
    def base(subject_hint, text, *args, **kwargs):
        out = fn(subject_hint, text, *args, **kwargs)
        bound = kwargs.get('bound') if 'bound' in kwargs else (args[2] if len(args) > 2 else False)
        words = f"{text or ''} {subject_hint or ''}" if bound else (text or '')
        return settle_base(out, words, catalog)
    base.__name__ = getattr(fn, '__name__', 'base')
    base.__doc__ = getattr(fn, '__doc__', None)
    base.__wrapped__ = fn
    return base


_LIB = {'at': 0.0, 'names': None}
_LIB_TTL_S = 120


def library_names(extra=None):
    """The library's display names (cached two minutes) plus `extra`.
    With no dashboard to ask, `extra` alone."""
    now = time.time()
    if _LIB['names'] is None or now - _LIB['at'] > _LIB_TTL_S:
        names = []
        try:
            from .legacy import chat as _chat
            names = [str(e.get('display_name') or '') for e in (_chat._profile_catalog_for_chat() or [])
                     if isinstance(e, dict) and isinstance(e.get('display_name'), str)]
        except Exception:
            names = []
        _LIB.update(at=now, names=names)
    return list(_LIB['names'] or []) + [str(x) for x in (extra or []) if x]
