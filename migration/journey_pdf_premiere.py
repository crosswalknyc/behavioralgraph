#!/usr/bin/env python3
"""The PDF for a premiere journey (``journey_kind == 'premiere'``): the
people who watched a new series' first episode through, where they came
from, how they found out, and who brought them. Plain English on the
Crosswalk design, one finding per page, the table that proves it, one line
on what it means. Every star is shown on every measure, side by side; the
top value on each line is marked. Every number is read off the payload;
no model call.

    from migration.journey_pdf_premiere import build_pdf
    pdf_bytes = build_pdf(payload)
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from migration.journey_pdf import _Doc, _fmt, _pct, BODY_DARK, BODY_LIGHT, GRAPHITE, GREEN, MUTED_DARK, MUTED_LIGHT, OFFWHITE, OLIVE, PAVEMENT


def _section(payload: dict, needle: str) -> Optional[dict]:
    for d in (payload.get('fragrance_shop_journey') or {}).get('detours') or []:
        if needle.lower() in str(d.get('title') or '').lower():
            return d
    return None


def _pos(name: str) -> str:
    n = str(name or '').strip()
    return n + ("'" if n.endswith('s') else "'s")


def _short(label: str) -> str:
    """The table cell keeps the short form; the long definition sits in the page's subhead."""
    t = str(label or '')
    if t.startswith('New to the franchise'):
        return 'New to the franchise'
    if t.startswith('Watched ') and ' within 7 days of ' in t:
        return 'Watched episode 2 within 7 days'
    return t.replace(' during the episode', '').replace(': no episode of an earlier series in the past year', '')


def _rows(section: Optional[dict], base: int, limit: int = 9) -> List[Tuple[str, str, str]]:
    out = []
    for r in ((section or {}).get('rows') or [])[:limit]:
        n = int(r.get('accounts') or 0)
        out.append((_short(r.get('label')), _fmt(n), _pct(n, base)))
    return out


def _split_rows(row: Optional[dict]) -> List[Tuple[str, str, str]]:
    base = int((row or {}).get('accounts') or 0)
    return [(str(b.get('label') or ''), _fmt(b.get('accounts')), _pct(b.get('accounts'), base)) for b in ((row or {}).get('breakdown') or [])[:8]]


def _grid(d: _Doc, title: str, head: List[str], rows: List[List[str]], x: float, y: float, width: float,
          lead_col: Optional[List[int]] = None, note: str = '') -> float:
    """A table with any number of value columns; lead_col[i] marks the
    column to light up on row i (the top value), or None."""
    from reportlab.lib import colors
    c = d.c
    ink = OFFWHITE if d.dark else GRAPHITE
    body = BODY_DARK if d.dark else BODY_LIGHT
    rule = PAVEMENT if d.dark else '#C9C8C0'
    hot = GREEN if d.dark else OLIVE
    if title:
        c.setFont(d.f['bold'], 11.5)
        c.setFillColor(colors.HexColor(ink))
        for line in d._wrap(title, d.f['bold'], 11.5, width):
            c.drawString(x, y, line)
            y -= 15
        y -= 2
    ncol = len(head) - 1
    label_w = width * 0.40
    col_w = (width - label_w) / max(1, ncol)
    c.setFont(d.f['medium'], 8)
    c.setFillColor(colors.HexColor(MUTED_DARK if d.dark else MUTED_LIGHT))
    c.drawString(x, y, str(head[0]).upper())
    for i, h in enumerate(head[1:]):
        c.drawRightString(x + label_w + col_w * (i + 1), y, str(h).upper())
    y -= 5
    c.setStrokeColor(colors.HexColor(rule))
    c.setLineWidth(0.6)
    c.line(x, y, x + width, y)
    y -= 13
    for ri, row in enumerate(rows):
        lab = str(row[0])
        c.setFont(d.f['regular'], 9.2)
        c.setFillColor(colors.HexColor(ink))
        lines = d._wrap(lab, d.f['regular'], 9.2, label_w - 8)[:2]
        for li, line in enumerate(lines):
            c.drawString(x, y - li * 11, line)
        lead = (lead_col or [None] * len(rows))[ri] if lead_col else None
        for i, val in enumerate(row[1:]):
            is_lead = (lead is not None and lead == i)
            c.setFont(d.f['bold' if is_lead else 'regular'], 9.5)
            c.setFillColor(colors.HexColor(hot if is_lead else body))
            c.drawRightString(x + label_w + col_w * (i + 1), y, str(val))
        y -= 14.5 + (11 if len(lines) > 1 else 0)
    if note:
        y -= 2
        y = d.paragraph(note, x, y, width, size=8.5, color=MUTED_DARK if d.dark else MUTED_LIGHT)
    return y


def _list(items: list) -> str:
    items = [str(x) for x in items if x]
    return items[0] if len(items) == 1 else (', '.join(items[:-1]) + ' and ' + items[-1] if items else '')


def _all_stars(stars: list, mid: str, suffix: str = '') -> str:
    """'Jennifer Beals 71.6%, LL Cool J 68.2%, Scott Caan 63.3%' in the payload's star order."""
    return ', '.join(f"{s['name']} {float(s['m'][mid]['pct']):.1f}%{suffix}" for s in stars)


def build_pdf(payload: dict) -> bytes:
    meta = payload.get('meta') or {}
    j = payload.get('fragrance_shop_journey') or {}
    jm = j.get('meta') or {}
    copy = j.get('copy') or {}
    T = str(meta.get('customer_brand') or '').strip() or 'the title'
    P = str(meta.get('streaming_home') or jm.get('streaming_home') or '').strip() or 'the streaming home'
    window = str(meta.get('window_label') or jm.get('window') or '')
    spine = j.get('spine') or []
    v1 = int(spine[1]['accounts']) if len(spine) > 1 else 0
    v3 = int(spine[3]['accounts']) if len(spine) > 3 else 0
    v4 = int(spine[-1]['accounts']) if spine else 0
    fork = {f.get('id'): f for f in (j.get('fork') or [])}
    pf = int((fork.get('paid_first') or {}).get('accounts') or 0)
    read = payload.get('the_read') or {}
    cb = j.get('cast_drivers') or {}
    stars = cb.get('stars') or []
    measures = [m for m in (cb.get('measures') or []) if m.get('id') != 'draw']
    names = [s['name'] for s in stars]
    leaders = cb.get('leaders') or {}

    d = _Doc()
    full_w = d.right - d.m
    half_w = (full_w - 0.28 * 72) / 2
    col2 = d.m + half_w + 0.28 * 72

    # 01 cover
    d.new_page(True, 'Digital Journey IQ', str(copy.get('titleHtml') or f'The {T} premiere audience.'),
               f"The people who watched the first episode of {T} through to the end. Unique US people, {window}.")
    d.paragraph(f"Where they came from, how they found out about the show, who brought them, and what they did during and after the episode. "
                f"Every star is shown on the same measures, side by side.", d.m, d.y - 10, full_w * 0.72, size=12, color=BODY_DARK)

    # 02 TLDR
    star_sum = sum(int(s['accounts']) for s in stars)
    shares = ', '.join(f"{s['name']} {float(s['share_pct']):.1f}%" for s in stars)
    d.new_page(False, 'The short version', f"{_fmt(v4)} people watched the first episode of {T} through.",
               (f"The leads brought {_pct(star_sum, v4)} of them: {shares}. Here is what matters and what to do." if stars and v4 else ''))
    tl = list(read.get('tldr') or [])[:5] or list(payload.get('facts') or [])[:5]
    d.bullets(tl, d.m, d.y, full_w, size=12.5)
    who = (payload.get('who_they_are') or {}).get('read') or ''
    d.meaning(who or f"The premiere audience is reachable through the two leads and the franchise block. The pages that follow show how each group behaved.")

    # 03 the path + the fork
    d.new_page(False, 'The path', f"{_fmt(v1)} heard about it; {_fmt(v4)} watched the first episode through.",
               'Each step keeps a share of the one before it. The fork shows who finished the first time they pressed play.')
    rows, prev = [], None
    for st in spine[1:]:
        n = int(st.get('accounts') or 0)
        rows.append((str(st.get('label') or ''), _fmt(n), _pct(n, prev) if prev else 'start'))
        prev = n
    d.table('The four steps', rows, d.m, d.y, half_w, head=('Step', 'People', 'Kept'))
    order = ('paid_first', 'abandoned', 'retargeted', 'returned', 'paid_return')
    frows = [(str(fork[k].get('label')), _fmt(fork[k].get('accounts')), f"{float(fork[k].get('kept') or 0):.1f}%") for k in order if k in fork]
    d.table('One sitting, or stopped and came back', frows, col2, d.y, half_w, head=('Branch', 'People', 'Kept'))
    d.meaning(str(copy.get('forkRead') or f"{_pct(pf, v3)} of the people who started the episode finished it in one sitting."))

    # 04 where they came from
    origin = _section(payload, 'Where they came from')
    new_row = next((r for r in ((origin or {}).get('rows') or []) if str(r.get('label', '')).startswith('New to the franchise')), {})
    nb = (new_row.get('breakdown') or [{}])[0] if new_row else {}
    bd_by = {b.get('label'): b for b in (new_row.get('breakdown') or [])} if new_row else {}
    new_split = ', '.join(f"{b.get('label')} {_pct(b.get('accounts'), new_row.get('accounts'))}"
                          for b in (new_row.get('breakdown') or []) if b.get('label') in names)   # biggest first
    d.new_page(False, 'Where they came from',
               (f"{nb.get('label', 'One star')} brought the most first-time franchise viewers: {new_split}."
                if nb and new_split else 'Where the first-episode audience came from.'),
               f"{_pct(new_row.get('accounts'), v4)} of first-episode viewers had watched no episode of an earlier series in the past year. "
               f"The left table splits everyone; the right splits the newcomers by who brought them." if new_row else '')
    wide_w = full_w * 0.56
    col_r = d.m + wide_w + 0.28 * 72
    d.table('Where they came from', _rows(origin, v4, limit=6), d.m, d.y, wide_w, head=('', 'People', 'Share'))
    d.table('The newcomers, by who brought them (share of the newcomers)', _split_rows(new_row), col_r, d.y, d.right - col_r, head=('', 'People', 'Share'))
    d.meaning(f"The franchise habit delivered most of the audience; the newcomers are the growth, and they came through the leads in this order: {new_split}." if new_split else '')

    # 05 how they found out
    disc, lt = _section(payload, 'first touch'), _section(payload, 'Last touch')
    top = max(((disc or {}).get('rows') or [{'label': '', 'accounts': 0}]), key=lambda r: int(r.get('accounts') or 0))
    d.new_page(False, 'How they found out', f"{top.get('label', 'A promo')} was the first touch for {_pct(top.get('accounts'), v4)} of them.",
               'The first digital surface that put the show in front of each viewer, and the last one before they pressed play.')
    wide_w = full_w * 0.56
    col_r = d.m + wide_w + 0.28 * 72
    y1 = d.table('How they found out: the first touch', _rows(disc, v4, limit=9), d.m, d.y, wide_w, head=('', 'People', 'Share'))
    y2 = d.table('Last touch before they pressed play', _rows(lt, v4, limit=8), col_r, d.y, d.right - col_r, head=('', 'People', 'Share'))
    post = next((r for r in ((disc or {}).get('rows') or []) if 'cast member' in str(r.get('label', '')).lower() and r.get('breakdown')), None)
    if post:
        d.table("A cast member's own post, by star (share of that row)", _split_rows(post), col_r, y2 - 14, d.right - col_r, head=('', 'People', 'Share'))
    d.meaning('The first touch is where the audience was built; the last touch is where the premiere was closed. Both get credit.')

    # 06 the stars, the same measures
    if stars:
        head = ['Measure'] + names + ['All viewers']
        grid_rows, lead_cols = [], []
        grid_rows.append(['People they brought to the first episode'] + [_fmt(s['accounts']) for s in stars] + [_fmt(v4)])
        lead_cols.append(max(range(len(stars)), key=lambda i: stars[i]['accounts']))
        grid_rows.append(['Share of the first-episode audience'] + [f"{float(s['share_pct']):.1f}%" for s in stars] + ['100%'])
        lead_cols.append(max(range(len(stars)), key=lambda i: float(stars[i]['share_pct'])))
        for m in measures:
            mid = m['id']
            vals = [float(s['m'][mid]['pct']) for s in stars]
            allv = (cb.get('all') or {}).get(mid) or {}
            grid_rows.append([m['label']] + [f"{v:.1f}%" for v in vals] + ([f"{float(allv.get('pct')):.1f}%"] if allv else ['']))
            lead_cols.append(max(range(len(vals)), key=lambda i: vals[i]))
        d.new_page(False, 'The stars, side by side',
                   str(copy.get('castHead') or f"{names[0]} brought the most people to the first episode."),
                   'The same measures for every star. Each line after the first two is the share of the people that star brought. The top value on each line is marked.')
        _grid(d, '', head, grid_rows, d.m, d.y, full_w, lead_cols, note=str(cb.get('note') or ''))
        by_leader = {}
        for m in measures:
            by_leader.setdefault(leaders.get(m['id']) or '', []).append(m['label'].split(':')[0].split(',')[0].lower())
        parts = [f"{_pos(n)} people lead on {_list(v)}" for n, v in by_leader.items() if n]
        trail = [n for n in names if n not in by_leader]
        d.meaning(f"{names[0]} brought the most people. " + '; '.join(parts) + '.'
                  + (f" {_list([_pos(n) + ' people' for n in trail])} trail on every line." if trail else '') if len(names) >= 2 else '')

        # 07 the second screen: the purchase rate by star, with the room around it
        ss = _section(payload, 'second screen')
        buy = next((r for r in ((ss or {}).get('rows') or []) if str(r.get('label', '')).startswith('Clicked an ad and bought')), None)
        ad_lead = leaders.get('ad_buy') or names[0]
        ad_vals = {s['name']: float(s['m']['ad_buy']['pct']) for s in stars}
        d.new_page(False, 'The second screen', f"Clicked an ad and bought during the episode: {_all_stars(stars, 'ad_buy')}.",
                   f"The share of the people each star brought who clicked an ad on a second screen and completed a purchase inside the episode's hour"
                   + (f"; all viewers {_pct(buy.get('accounts'), v4)}." if buy else '.'))
        d.table('What they did on a second screen during the episode', _rows(ss, v4, limit=6), d.m, d.y, half_w, head=('', 'People', 'Share'))
        ad_rows = [[s['name'], f"{float(s['m']['ad_buy']['pct']):.1f}%", _fmt(s['m']['ad_buy']['accounts'])] for s in stars]
        _grid(d, 'Clicked an ad and bought something during the episode, by star', ['Star', 'Rate', 'People'], ad_rows, col2, d.y, half_w,
              [0 if s['name'] == ad_lead else None for s in stars], note='Rate is the share of the people that star brought to the first episode.')
        others = ', '.join(f"{n} {ad_vals[n]:.1f}%" for n in names if n != ad_lead)
        d.meaning(f"The ad break works hardest on {_pos(ad_lead)} people: {ad_vals[ad_lead]:.1f}% clicked an ad and completed a purchase inside the episode's own hour, against {others}.")

    # 08 when, where, and episode 2
    when, where, e2 = _section(payload, 'When they watched'), _section(payload, 'Where it played'), _section(payload, 'episode 2')
    live = next((r for r in ((when or {}).get('rows') or []) if str(r.get('label', '')).startswith('Live on')), {})
    back = next((r for r in ((e2 or {}).get('rows') or []) if str(r.get('label', '')).startswith('Watched ')), {})
    e2_lead = leaders.get('episode2') or ''
    e2_vals = {s['name']: float(s['m']['episode2']['pct']) for s in stars}
    d.new_page(False, 'When and where, and the second episode',
               f"{_pct(back.get('accounts'), v4)} came back for episode 2: {_all_stars(stars, 'episode2')}." if back and stars else 'When and where they watched.',
               'The share of the people each star brought who watched episode 2 within 7 days, then when and where the first episode played.' if stars else '')
    y1 = d.table('When they watched it', _rows(when, v4, limit=5), d.m, d.y, half_w, head=('', 'People', 'Share'))
    y2 = d.table('Where it played', _rows(where, v4, limit=7), col2, d.y, half_w, head=('', 'People', 'Share'))
    d.table('Did they come back for episode 2', _rows(e2, v4, limit=2), d.m, y1 - 14, half_w, head=('', 'People', 'Share'))
    d.meaning(f"{_pct(live.get('accounts'), v4)} watched live; the rest arrived across the week on {P} and the live TV streamers, which is where the episode 2 push lands." if live else '')

    # 09 what to do
    recs = list(read.get('recommendations') or [])[:4]
    d.new_page(True, 'What to do next', 'Keep the leads in front of the audience, and point the episode 2 push at the fans who came back most.',
               (f"Came back for episode 2: {_all_stars(stars, 'episode2')}. Each step below carries the number that justifies it." if stars
                else 'Each step carries the number that justifies it.'))
    y = d.y
    if recs:
        y = d.bullets(recs, d.m, d.y, full_w, size=12.5)
    reads = list(read.get('reads') or [])[:4]
    if reads and y > 1.9 * 72:
        d.paragraph('  '.join(reads), d.m, y - 22, full_w, size=10.5, color=BODY_DARK)
    d.meaning(f"Basis: the Crosswalk clickstream panel of 10M US consumers, projected to the United States; {window}; unique US people.")
    return d.finish()
