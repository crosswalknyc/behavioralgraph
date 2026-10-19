#!/usr/bin/env python3
"""The PDF for a media-week journey (``journey_kind == 'media_week'``):
a week in the media life of a title's viewers. Plain English on the
Crosswalk design, one finding per page, the table that proves it, one
line on what it means. Every number is read off the payload; no model
call.

    from migration.journey_pdf_media_week import build_pdf
    pdf_bytes = build_pdf(payload)
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from migration.journey_pdf import _Doc, _fmt, _pct, BODY_DARK


def _section(payload: dict, needle: str) -> Optional[dict]:
    for d in (payload.get('fragrance_shop_journey') or {}).get('detours') or []:
        if needle.lower() in str(d.get('title') or '').lower():
            return d
    return None


def _rows(section: Optional[dict], base: int, limit: int = 9, with_index: bool = False) -> List[Tuple[str, str, str]]:
    out = []
    for r in ((section or {}).get('rows') or [])[:limit]:
        n = int(r.get('accounts') or 0)
        third = (f"{r['index']:g}x" if (with_index and r.get('index')) else _pct(n, base))
        out.append((str(r.get('label') or ''), _fmt(n), third))
    return out


def _doing_lines(section: Optional[dict], limit: int = 7) -> List[str]:
    out = []
    for r in ((section or {}).get('rows') or [])[:limit]:
        d = str(r.get('doing') or '').strip()
        if d:
            out.append(f"{r.get('label')}: {d}")
    return out


def build_pdf(payload: dict) -> bytes:
    meta = payload.get('meta') or {}
    j = payload.get('fragrance_shop_journey') or {}
    jm = j.get('meta') or {}
    copy = j.get('copy') or {}
    T = str(meta.get('customer_brand') or '').strip() or 'the title'
    P = str(meta.get('streaming_home') or jm.get('streaming_home') or '').strip() or 'the service'
    window = str(meta.get('window_label') or jm.get('window') or '')
    spine = j.get('spine') or []
    v1 = int(spine[1]['accounts']) if len(spine) > 1 else 0
    v2 = int(spine[2]['accounts']) if len(spine) > 2 else 0
    v3 = int(spine[3]['accounts']) if len(spine) > 3 else 0
    fork = {f.get('id'): f for f in (j.get('fork') or [])}
    night = int((fork.get('paid_first') or {}).get('accounts') or 0)
    read = payload.get('the_read') or {}

    d = _Doc()
    full_w = d.right - d.m
    half_w = (full_w - 0.28 * 72) / 2
    col2 = d.m + half_w + 0.28 * 72

    # 01 cover
    d.new_page(True, 'Digital Journey IQ', str(copy.get('titleHtml') or f'A week in the life of a {T} viewer.'),
               f"{T} viewers, unique US people, {window}. Every count is a person, not a household.")
    d.paragraph(f"What {T} viewers stream, hear and open in a typical in-season week: where they watch, how fast, which day, "
                f"which hours, and the shows, services, podcasts, social platforms, news and games around the episode.",
                d.m, d.y - 10, full_w * 0.72, size=12, color=BODY_DARK)

    # 02 TLDR
    d.new_page(False, 'The short version', f"{_fmt(v1)} people streamed {T} in the window; most of their week is elsewhere.",
               f"{_pct(v2, v1)} come back to the title off-screen in the week; {_pct(v3, v2)} of those watch the next episode within 7 days.")
    tl = list(read.get('tldr') or [])[:5] or list(payload.get('facts') or [])[:5]
    d.bullets(tl, d.m, d.y, full_w, size=12.5)
    who = (payload.get('who_they_are') or {}).get('read') or ''
    d.meaning(who or f"The {T} viewer is reachable all week, not only on episode night. The pages that follow show where.")

    # 03 the week in three steps + the fork
    d.new_page(False, 'The week', 'Watch, come back off-screen, watch the next one.',
               f"Each step keeps a share of the one before it. {_fmt(v1)} viewers; {_fmt(v3)} watched the next episode inside the week.")
    rows, prev = [], None
    for st in spine[1:]:
        n = int(st.get('accounts') or 0)
        rows.append((str(st.get('label') or ''), _fmt(n), _pct(n, prev) if prev else 'start'))
        prev = n
    d.table('The three steps', rows, d.m, d.y, half_w, head=('Step', 'People', 'Kept'))
    order = ('paid_first', 'abandoned', 'retargeted', 'returned', 'paid_return')
    frows = [(str(fork[k].get('label')), _fmt(fork[k].get('accounts')), f"{float(fork[k].get('kept') or 0):.1f}%") for k in order if k in fork]
    d.table('Episode night or later in the week', frows, col2, d.y, half_w, head=('Branch', 'People', 'Kept'))
    d.meaning(str(copy.get('forkRead') or f"{_pct(night, v2)} of the off-screen group watched the next episode the night it dropped."))

    # 04 where + how fast
    where, speed = _section(payload, 'Where they stream'), _section(payload, 'How fast')
    top_where = (where or {}).get('rows', [{}])[0] if where else {}
    d.new_page(False, 'Where and how fast', f"{top_where.get('label', P)} carries {_pct(top_where.get('accounts', 0), v1)} of {T} viewers.",
               f"Where the episode plays, and how long after it drops. Rows sum to every viewer.")
    d.table(f'Where they stream {T}', _rows(where, v1), d.m, d.y, half_w, head=('Surface', 'People', 'Share'))
    d.table('How fast they watch a new episode', _rows(speed, v1), col2, d.y, half_w, head=('When', 'People', 'Share'))
    sp0 = (speed or {}).get('rows', [{}])[0] if speed else {}
    d.meaning(f"{_pct(sp0.get('accounts', 0), v1)} watch {sp0.get('label', 'the night it dropped').lower()}; the rest of the week is catch-up, "
              f"which is where clips, recaps and the next-day push do their work.")

    # 05 day by day
    days = _section(payload, 'day by day')
    top_day = max(((days or {}).get('rows') or [{'label': '', 'accounts': 0}]), key=lambda r: int(r.get('accounts') or 0))
    d.new_page(False, 'The week, day by day', f"{top_day.get('label', 'Episode night')} is the biggest {T} day, but every day carries some of it.",
               f"The day each viewer's {T} time mostly lands, and what the rest of that day's media looks like.")
    y = d.table('Their week, day by day', _rows(days, v1, limit=7), d.m, d.y, half_w, head=('Day', 'People', 'Share'))
    d.bullets(_doing_lines(days, 7), col2, d.y, half_w, size=9.5)
    d.meaning(f"Plan the week, not the hour: {_pct(top_day.get('accounts', 0), v1)} of viewers do their {T} time on {top_day.get('label', 'episode night')}, "
              f"which leaves most of the audience reachable on the other six days.")

    # 06 dayparts
    dp = _section(payload, 'daypart')
    top_dp = max(((dp or {}).get('rows') or [{'label': '', 'accounts': 0}]), key=lambda r: int(r.get('accounts') or 0))
    d.new_page(False, 'An episode day, hour by hour', f"{_pct(top_dp.get('accounts', 0), v1)} of plays land in {top_dp.get('label', 'prime time').lower()}.",
               'When the episode plays, and the other media in each part of the day.')
    d.table('An episode day, by daypart', _rows(dp, v1, limit=5), d.m, d.y, half_w, head=('Daypart', 'People', 'Share'))
    d.bullets(_doing_lines(dp, 5), col2, d.y, half_w, size=9.5)
    d.meaning('The morning and the workday belong to audio, news and social; the episode owns prime time; late night goes to short video and library streaming.')

    # 07 to 11 the overlaps
    from migration.journey_media_week import leaders_line
    for needle, eyebrow, meaning_fn in (
        ('Other shows', 'Other shows',
         lambda r: f"These are the shows to pair with {T} in promos and lead-ins; the index says how much more this audience watches them than US adults do."),
        ('Streaming services', 'Streaming services',
         lambda r: "Where the same people spend the rest of their streaming week. The broadest services top the reach list for any audience; the index is the lean."),
        ('Podcasts and audio', 'Podcasts and audio',
         lambda r: "Audio is the commute and the workday for this audience; the title's own recap shows sit next to music and radio apps."),
        ('Social platforms', 'Social',
         lambda r: "The platforms, and what they do there: the groups, the threads, the creator accounts, the clips. Facebook tops reach for most adult audiences; the index shows where this one leans."),
        ('News, sports', 'News, sports and games',
         lambda r: "The rest of the week's screen time: news apps, sports apps and leagues, and the games on the phone."),
    ):
        sec = _section(payload, needle)
        rows = (sec or {}).get('rows') or []
        if not rows:
            continue
        top = rows[0]
        head = leaders_line(sec, T) or f"{top['label']} reaches {_pct(top['accounts'], v1)} of {T} viewers in a week."
        d.new_page(False, eyebrow, head, 'Share of viewers who did this in a typical week, ranked by reach. The index against US adults (weekly over weekly) shows where this audience leans. Rows do not sum.')
        d.table(str(sec.get('title') or needle), _rows(sec, v1, limit=10, with_index=True), d.m, d.y, half_w, head=('', 'People', 'Index'))
        d.bullets(_doing_lines(sec, 8), col2, d.y, half_w, size=9.5)
        d.meaning(meaning_fn(top))

    # 12 what to do
    recs = list(read.get('recommendations') or [])[:4]
    d.new_page(True, 'What to do next', f"Reach the {T} viewer across the week, not only on episode night.",
               'Each step carries the number that justifies it.')
    y = d.y
    if recs:
        y = d.bullets(recs, d.m, d.y, full_w, size=12.5)
    reads = list(read.get('reads') or [])[:4]
    if reads and y > 1.9 * 72:
        d.paragraph('  '.join(reads), d.m, y - 22, full_w, size=10.5, color=BODY_DARK)
    d.meaning(f"Basis: the Crosswalk clickstream panel of 10M US consumers, projected to the United States; {window}; unique US people.")
    return d.finish()
