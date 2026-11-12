"""Brand Partnership IQ: a page the campaign created has no history before it.

2026-11-11, Gap x JYT. The valuation's touchpoint table showed 2,211
visits to gap.com/browse/gap-x-jyt in the 51 days BEFORE the partnership
was announced, and 1,287 to the mall tour landing page. Neither page
existed yet. The research returned a before count for every touchpoint
it listed and nothing asked whether the touchpoint could have had one.

The rule, in one place for the engine and the deck:

  * A touchpoint is the campaign's own when the research marks it new,
    when its name carries the talent or the campaign's name (a brand
    page that names the talent exists because of the partnership), or
    when it is the announcement itself (a partnership release).
  * The campaign's own touchpoint has no visits before the campaign's
    first public date: the earliest of its drop dates and the first day
    of the campaign window. When the before window ends ahead of that
    date, its before count is zero. When the before window runs past it
    (a collection that dropped a week ahead of the window being read),
    the count stands.

`migration/bpiq_synthesis.build_payload` applies it where the before
counts are made, so a new valuation never carries one. The deck builder
applies the same rule when it draws the table, so a valuation saved
before this date prints correctly too. Pure functions, no I/O.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Iterable, Optional

# The announcement itself: published on the day the campaign went public.
_ANNOUNCEMENT = re.compile(
    r"\b(?:partnership|collaboration|collab|campaign|capsule|collection)\s+(?:press\s+)?"
    r"(?:release|announcement|launch\s+page|landing\s+page)\b")
# Words in a campaign's name that say nothing on their own.
_GENERIC = {"the", "a", "an", "and", "x", "by", "for", "with", "of", "collection", "campaign", "capsule",
            "partnership", "collaboration", "collab", "launch", "drop"}


def _norm(text) -> str:
    """Lower case, every run of punctuation a single space, padded so a
    whole-word test is a substring test ('gap.com/browse/gap-x-jyt' ->
    ' gap com browse gap x jyt ')."""
    return " " + " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split()) + " "


def _forms(names: Iterable, *, pairing: bool = False) -> list:
    """The ways a name can appear inside a touchpoint's name. 'JYT (Just
    Your Type)' gives 'jyt' and 'just your type'; 'Jennifer Beals' also
    gives 'jenniferbeals' (a handle or a slug). A campaign name is split
    at its commas and colons ('GANNI x lululemon, Joy Is a Power Move')
    and each part must carry at least one word of its own."""
    out = []
    for raw in names or ():
        raw = str(raw or "").strip()
        if not raw:
            continue
        parts = [raw]
        parts += re.findall(r"\(([^)]+)\)", raw)                 # the long form in brackets
        parts.append(re.sub(r"\([^)]*\)", " ", raw))             # the name without it
        if pairing:
            parts += re.split(r"[,:;|]", raw)
        for p in parts:
            n = _norm(p).strip()
            if not n:
                continue
            words = n.split()
            if pairing:
                if len(n) < 6 or not [w for w in words if w not in _GENERIC and len(w) >= 3]:
                    continue
            elif len(n) < 3:
                continue
            out.append(n)
            if len(words) > 1:
                out.append("".join(words))                       # the slug or handle form
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def is_campaign_asset(name, *, talent: Iterable = (), campaign: Iterable = (), flag=None) -> bool:
    """True when the touchpoint is the campaign's own: the research marked
    it new, its name carries the talent or the campaign's name, or it is
    the announcement."""
    if flag is True or (isinstance(flag, str) and flag.strip().lower() in ("true", "yes", "1")):
        return True
    n = _norm(name)
    if not n.strip():
        return False
    for form in _forms(talent) + _forms(campaign, pairing=True):
        if f" {form} " in n:
            return True
    return bool(_ANNOUNCEMENT.search(n))


def _date(value) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def first_public_date(drop_dates, event_start) -> Optional[_dt.date]:
    """The day the campaign went public: the earliest of its drop dates
    and the first day of the campaign window."""
    days = [d for d in (_date(x) for x in list(drop_dates or []) + [event_start]) if d is not None]
    return min(days) if days else None


def no_history_before(name, *, talent: Iterable = (), campaign: Iterable = (), pre_end=None,
                      drop_dates=None, event_start=None, flag=None) -> bool:
    """True when this touchpoint cannot have a before count: it is the
    campaign's own and the before window ends ahead of the campaign's
    first public date. Never raises."""
    try:
        if not is_campaign_asset(name, talent=talent, campaign=campaign, flag=flag):
            return False
        public = first_public_date(drop_dates, event_start)
        end = _date(pre_end)
        if public is None or end is None:
            return False
        return end < public
    except Exception:
        return False


def context_from_payload(data: dict) -> dict:
    """The keyword arguments `no_history_before` needs, read from a saved
    valuation (the deck builder's side)."""
    data = data if isinstance(data, dict) else {}
    camp = data.get("campaign") if isinstance(data.get("campaign"), dict) else {}
    q = data.get("qualifier_value")
    talent = [str(v) for v in q] if isinstance(q, (list, tuple)) else [str(q or "")]
    pre = data.get("pre_period") if isinstance(data.get("pre_period"), dict) else {}
    event = data.get("event_period") if isinstance(data.get("event_period"), dict) else {}
    return {
        "talent": [t for t in talent if t.strip()],
        "campaign": [str(x) for x in (data.get("campaign_name"), camp.get("name")) if str(x or "").strip()],
        "pre_end": pre.get("end"),
        "drop_dates": camp.get("drop_dates") or [],
        "event_start": event.get("start") or data.get("start_date"),
    }
