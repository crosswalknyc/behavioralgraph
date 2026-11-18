"""Brand Partnership IQ: the two windows of a valuation, resolved once.

A valuation carries the campaign's own dates (`event_period`, with
`start_date` / `end_date`), a before window (`pre_period`) and a second
window (`post_period`). Which days the valuation's after counts cover
depends on how the valuation was made, and until 2026-11-18 each
surface read it its own way:

  * the deck printed `post_period`, so a 177-day season read "17 days,
    Nov 1 to Nov 17" (the days from the season's end to the day the
    valuation was made);
  * the dashboard's line under the title printed the campaign's first
    day through `post_period.end` with the two lengths added, so the
    same valuation read "Post-window: 2026-05-08 -> 2026-11-17 (194d)"
    and an order whose window is the campaign's own dates read twice
    its length.

This module is the one reading. The deck builder
(`migration/bpiq_deck_builder.py`) takes its windows from here, and the
results route hands the dashboard the finished line (`with_window_view`),
so the page prints what the deck prints and neither can drift from the
other. Nothing here writes: a stored valuation is never changed, and
`with_window_view` returns a copy for the response.

Pure functions, no I/O, no third-party import. Never raises on a
malformed valuation.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

VIEW_KEY = "_bpiq_window"        # where the response carries the view; never saved to a valuation
ARROW = "\u2192"
DOT = "\u00b7"


def _dict(o: Any) -> dict:
    return o if isinstance(o, dict) else {}


def _num(n: Any, default: float = 0.0) -> float:
    if isinstance(n, bool):
        return float(n)
    if isinstance(n, (int, float)):
        return float(n)
    try:
        return float(str(n).replace(",", "").replace("$", "").replace("%", "").strip() or default)
    except (TypeError, ValueError):
        return float(default)


def _count(n: Any, default: int = 0) -> int:
    try:
        return int(round(_num(n, default)))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def ymd(iso: Any) -> Optional[tuple]:
    """(year, month, day) for a YYYY-MM-DD string, else None."""
    try:
        y, m, d = (int(x) for x in str(iso or "")[:10].split("-"))
        if 1 <= m <= 12 and 1 <= d <= 31:
            return y, m, d
    except (TypeError, ValueError):
        pass
    return None


def span_days(start: Any, end: Any) -> int:
    """The days from `start` to `end`, both counted; 0 when either is
    not a date or the range runs backwards."""
    a, b = ymd(start), ymd(end)
    if a is None or b is None:
        return 0
    try:
        n = (_dt.date(*b) - _dt.date(*a)).days + 1
    except ValueError:
        return 0
    return n if n > 0 else 0


def day_after(iso: Any) -> str:
    p = ymd(iso)
    if p is None:
        return ""
    try:
        return (_dt.date(*p) + _dt.timedelta(days=1)).isoformat()
    except ValueError:
        return ""


def after_window(data: dict) -> dict:
    """The window the valuation's after counts cover, as {'start',
    'end', 'days', 'basis'}: the one window every date and day count is
    read from (2026-11-18, Coach x WNBA: the counts were for the season,
    May 8 to Oct 31, 177 days, and every page of the deck but the cover
    labeled them '17 days, Nov 1 to Nov 17', the days from the season's
    end to the day the valuation was made).

    Which window the counts cover is read from the valuation itself:

      * no second window, or one of no length: the campaign's dates;
      * a second window that runs from the campaign's first day (the
        attribution window an order confirms: the campaign plus the
        days after it): that window;
      * a second window that starts later: the valuation's own daily
        rate (`totals.post_hits` over `post_hits_per_day`) says how many
        days the after counts cover. They cover that later window (a
        read of the days after an airing), the campaign's dates (the
        later window is only the days up to when the valuation was
        made), or the campaign and the later window together (the live
        pull). With no daily rate to read, a valuation whose stated
        length is the two together reads as the two together, and any
        other reads as the later window.

    Never raises; a valuation with no dates gives its stated length."""
    data = _dict(data)
    ev, po = _dict(data.get("event_period")), _dict(data.get("post_period"))
    ev_s = str(ev.get("start") or data.get("start_date") or "")[:10]
    ev_e = str(ev.get("end") or data.get("end_date") or "")[:10]
    po_s, po_e = str(po.get("start") or "")[:10], str(po.get("end") or "")[:10]
    has_event = bool(ymd(ev_s) and ymd(ev_e))
    has_post = bool(ymd(po_s) and ymd(po_e))
    stated = _count(data.get("post_period_days")) or _count(data.get("attribution_window_days"))
    ev_days = _count(ev.get("days")) or span_days(ev_s, ev_e)
    po_days = _count(po.get("days")) if po.get("days") is not None else span_days(po_s, po_e)
    event = {"start": ev_s, "end": ev_e, "days": ev_days or stated, "basis": "event"}
    post = {"start": po_s, "end": po_e, "days": po_days or stated, "basis": "post"}
    if not has_post:
        if has_event:
            return event
        return {"start": "", "end": "", "days": _count(po.get("days")) or stated, "basis": "stated"}
    if not has_event:
        return post
    if po_days <= 0:
        return event
    if po_s <= ev_s:
        return post
    joined = po_s <= day_after(ev_e)
    both = {"start": ev_s, "end": po_e, "days": span_days(ev_s, po_e), "basis": "event and post"}
    # the two together are a third reading only when they are not the campaign's dates again
    # (a second window that ends where the campaign ends adds no days to it)
    options = [post, event] + ([both] if joined and both["end"] != event["end"] else [])
    totals = _dict(data.get("totals"))
    hits, rate = _num(totals.get("post_hits")), _num(totals.get("post_hits_per_day"))
    if hits > 0 and rate > 0:
        fits = [w for w in options if w["days"] > 0 and abs(round(hits / w["days"], 2) - rate) <= 0.011]
        lengths = {w["days"] for w in fits}
        if len(lengths) == 1 and len(fits) == 1:
            return fits[0]
        if len(lengths) == 1 and {w["basis"] for w in fits} == {"post", "event"}:
            # the two windows are the same length: a valuation the engine wrote counts the campaign's dates
            diag = _dict(data.get("diagnostics"))
            engine = bool(diag.get("run_key")) or "post_window_clipped_to_today" in diag \
                or bool(_dict(data.get("windows")).get("matched_pre_window"))
            return event if engine else post
    if joined and _count(data.get("post_period_days")) == ev_days + po_days and ev_days > 0:
        return both
    return post


def before_window(data: dict) -> dict:
    """The before window, as {'start', 'end', 'days'}: the valuation's
    own `pre_period`, its length from the valuation when it states one
    and from the dates when it does not."""
    data = _dict(data)
    pre = _dict(data.get("pre_period"))
    start, end = str(pre.get("start") or "")[:10], str(pre.get("end") or "")[:10]
    days = _count(pre.get("days")) or _count(data.get("pre_period_days")) or span_days(start, end)
    return {"start": start, "end": end, "days": days}


def campaign_dates(data: dict) -> dict:
    """The campaign's own dates as every surface prints them: the
    valuation's `start_date` and `end_date`, else its `event_period`."""
    data = _dict(data)
    ev = _dict(data.get("event_period"))
    start = str(data.get("start_date") or ev.get("start") or "")[:10]
    end = str(data.get("end_date") or ev.get("end") or "")[:10]
    return {"start": start, "end": end, "days": span_days(start, end)}


def days_after_campaign(data: dict, after: Optional[dict] = None) -> int:
    """How many of the days the after counts cover fall after the
    campaign's last day: 0 when the counts are the campaign's own dates,
    30 for a campaign read through the 30 days after it, the whole
    length of a window that starts once the campaign is over. With no
    dates to read, the length the valuation states."""
    data = _dict(data)
    after = after if isinstance(after, dict) else after_window(data)
    c_end = campaign_dates(data)["end"]
    a_start, a_end = str(after.get("start") or ""), str(after.get("end") or "")
    if not (ymd(c_end) and ymd(a_start) and ymd(a_end)):
        return _count(data.get("attribution_window_days"))
    if a_end <= c_end:
        return 0
    return span_days(max(day_after(c_end), a_start), a_end)


def meta_line(campaign: dict, before: dict, after: dict, extra_days: int) -> str:
    """The line the dashboard prints under a valuation's title. The
    wording and the order are the page's own, unchanged; the after
    window and the days after the campaign are the resolved ones."""
    return f" {DOT} ".join([
        f"Campaign: {campaign.get('start', '')} {ARROW} {campaign.get('end', '')}",
        f"Attribution: {int(extra_days)}d",
        f"Pre-window: {before.get('start', '')} {ARROW} {before.get('end', '')} ({int(before.get('days') or 0)}d)",
        f"Post-window: {after.get('start', '')} {ARROW} {after.get('end', '')} ({int(after.get('days') or 0)}d)",
    ])


def _one_view(data: dict) -> dict:
    campaign, before, after = campaign_dates(data), before_window(data), after_window(data)
    extra = days_after_campaign(data, after)
    return {
        "campaign": campaign,
        "before": before,
        "after": {k: after.get(k) for k in ("start", "end", "days")},
        "days_after_campaign": extra,
        "line": meta_line(campaign, before, after, extra),
    }


def year_slice(data: dict, year: dict) -> dict:
    """One year of a multi-year valuation, as the dashboard's year view
    cuts it: the year's own dates are the campaign's, with no days
    after. Only the fields a window is read from."""
    data, year = _dict(data), _dict(year)
    start, end = year.get("start"), year.get("end")
    return {
        "start_date": start or data.get("start_date"),
        "end_date": end or data.get("end_date"),
        "event_period": {"start": start, "end": end, "days": year.get("days")},
        "post_period": {"start": end, "end": end, "days": 0},
        "pre_period": data.get("pre_period"),
        "pre_period_days": data.get("pre_period_days"),
        "attribution_window_days": 0,
    }


def window_view(data: dict) -> dict:
    """The resolved windows of a valuation for a surface that prints
    them: the campaign's dates, the before window, the after window the
    counts cover, the days of it that fall after the campaign, and the
    dashboard's line. `by_year` carries the same for each year of a
    multi-year valuation, keyed by the year. Dates and day counts only:
    no figure of the valuation is in the view."""
    data = _dict(data)
    try:
        view = _one_view(data)
    except Exception:
        return {}
    by_year = {}
    for y in (data.get("yearly_breakdown") or []) if isinstance(data.get("yearly_breakdown"), list) else []:
        if not isinstance(y, dict) or y.get("year") in (None, ""):
            continue
        try:
            by_year[str(y.get("year"))] = _one_view(year_slice(data, y))
        except Exception:
            continue
    if by_year:
        view["by_year"] = by_year
    return view


def with_window_view(data: Any) -> Any:
    """The valuation as a response carries it: a copy with the resolved
    windows under `_bpiq_window`. The valuation handed in is not changed
    (it is the stored document, read for this response), and a
    valuation that cannot be read comes back as it was."""
    if not isinstance(data, dict):
        return data
    try:
        out = dict(data)
        out[VIEW_KEY] = window_view(data)
        return out
    except Exception:
        return data


__all__ = ["VIEW_KEY", "ymd", "span_days", "day_after", "after_window", "before_window", "campaign_dates",
           "days_after_campaign", "meta_line", "year_slice", "window_view", "with_window_view"]
