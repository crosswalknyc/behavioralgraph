"""The analysis PDF for a PURCHASE journey in the standard shape
(`migration.journey_gold_purchase`). Same deck system as the watch PDF
(`migration.journey_pdf`): landscape letter, Crosswalk palette, plain
one-line headers, a "what this means" line on every page, and a page of
its own for the people who left the cart and why (Jenna, 2026-10-16).
"""
from __future__ import annotations

import re
from typing import List, Tuple

from migration.journey_pdf import _Doc, _fmt, _pct, _rows_for, _top, _lower_first, BODY_DARK
from migration import journey_gold_purchase as G


def _extra(payload: dict, rx: str):
    j = payload.get('fragrance_shop_journey') or {}
    return next((d for d in (j.get('detours') or []) if re.search(rx, str(d.get('title') or ''), re.I)), None)


def _sub(row: dict, label: str) -> int:
    return int(next((b.get('accounts') for b in (row.get('breakdown') or []) if b.get('label') == label), 0) or 0)


def build_pdf(payload: dict) -> bytes:
    s = G.ship_summary(payload)
    T, P = s['title'], s['platform']
    meta = payload.get('meta') or {}
    window = s['window'] or str(meta.get('window_label') or '')
    d = _Doc()
    full_w = d.right - d.m
    half_w = (full_w - 0.28 * 72) / 2
    col2 = d.m + half_w + 0.28 * 72

    # 01 cover
    d.new_page(True, 'Digital Journey IQ', f"{T} bought online: the path from the first post to the order.",
               f"Unique US people, {window}. Every count is a person, not a household.")
    d.paragraph(f"How content about {T} turned into an online order, step by step, who left the cart and why, "
                f"and the eleven tables behind it.", d.m, d.y - 10, full_w * 0.7, size=12, color=BODY_DARK)

    # 02 TLDR
    d.new_page(False, 'The short version', f"{_fmt(s['paid'])} people bought {T} online within 30 days of seeing a post.",
               f"{s['conversion']:.2f}% of everyone who saw a post. Here is what matters and what to do.")
    d.bullets(G.analysis_points(payload), d.m, d.y, full_w, size=12.5)
    d.meaning(f"The posts reached {_fmt(s['social'])} people and {_fmt(s['paid'])} of them paid. The cart lost "
              f"{_fmt(s['abandoned'])} more; the rest of this file shows what those people did next and what would have kept them.")

    # 03 the path
    d.new_page(False, 'The path', f"From the first post to the paid order, step by step.",
               f"Each step keeps a share of the one before it. {_fmt(s['social'])} saw a post; {_fmt(s['paid'])} paid.")
    spine = (payload.get('fragrance_shop_journey') or {}).get('spine') or []
    rows, prev = [], None
    for st in spine[1:]:
        n = int(st.get('accounts') or 0)
        rows.append((str(st.get('label') or ''), _fmt(n), _pct(n, prev) if prev else 'start'))
        prev = n
    d.table('The spine', rows, d.m, d.y, half_w, head=('Step', 'People', 'Kept'))
    fork = (payload.get('fragrance_shop_journey') or {}).get('fork') or []
    frows = [(str(f.get('label') or f.get('id')), _fmt(f.get('accounts')), f"{float(f.get('kept') or 0):.1f}%") for f in fork]
    d.table('The cart: who paid on the spot, who left, who came back', frows, col2, d.y, half_w, head=('Branch', 'People', 'Kept'),
            note='Kept is the share of the branch above it. Same-session orders and orders after a return add up to the paid step.')
    d.meaning(f"{_pct(s['cart'], s['page'])} of the people who opened a product page added {T} to a cart, and {_pct(s['paid'], s['cart'])} of those paid. "
              f"The cart is where the path is won or lost.")

    # 04 where it started
    fp, ps, pb = G.section(payload, 'first_post'), G.section(payload, 'post_surface'), G.section(payload, 'posts_before')
    top_fp, top_ps = _top(fp), _top(ps)
    hdr = (f"{top_fp[0][0]} on {top_ps[0][0]} opened the most paths." if top_fp and top_ps else "Where the path started.")
    d.new_page(False, 'Where it started', hdr[:1].upper() + hdr[1:],
               f"The first {T} content each of the {_fmt(s['social'])} people saw, where it sat, and how many posts it took before they looked {T} up.")
    y1 = d.table('First content they saw', _rows_for(fp, s['social']), d.m, d.y, half_w)
    y2 = d.table('Where the first post sat', _rows_for(ps, s['social']), col2, d.y, half_w)
    d.table(f"How many posts before they looked {T} up", _rows_for(pb, s['intent']), d.m, min(y1, y2) - 16, half_w,
            note=f"Share of the {_fmt(s['intent'])} who engaged.")
    one = next((int(r.get('accounts') or 0) for r in (pb or {}).get('rows') or [] if str(r.get('label', '')).startswith('1 post')), 0)
    d.meaning(f"One post was enough for {_pct(one, s['intent'])} of the people who engaged. The rest needed a run of posts, "
              f"so frequency is part of the plan, not a nice-to-have.")

    # 05 from the post to the order
    lb, tb, fpr = G.section(payload, 'looked_before'), G.section(payload, 'time_to_buy'), G.section(payload, 'first_product')
    d.new_page(False, 'From the post to the order', f"Most orders came within a week of the first post.",
               f"{_pct(s['same_day'], s['paid'])} the same day, {_pct(s['within_7'], s['paid'])} within a week, "
               f"{_pct(s['within_30'], s['paid'])} in days 8 to 30.")
    y1 = d.table('Where they looked before the cart', _rows_for(lb, s['page']), d.m, d.y, half_w,
                 note=f"Share of the {_fmt(s['page'])} who opened a product page. A person can appear in more than one row.")
    y2 = d.table('Time from the first post to the order', _rows_for(tb, s['paid']), col2, d.y, half_w)
    d.table('What they bought first', _rows_for(fpr, s['paid'], limit=6), col2, y2 - 16, half_w)
    top_lb = _top(lb)
    d.meaning((f"{top_lb[0][0]} was the most common stop between the post and the cart. " if top_lb else '') +
              f"The window to convert is short: after the first week only {_pct(s['within_30'], s['paid'])} of the orders were still to come.")

    # 06 what closed it
    lt, asst = G.section(payload, 'last_touch'), G.section(payload, 'assists')
    top_lt = _top(lt)
    d.new_page(False, 'What closed it', (f"{top_lt[0][0]} was the last touch before most orders." if top_lt else "The last touch before the order."),
               f"The final step before the {_fmt(s['paid'])} orders, what else was in the mix, and where the order was placed.")
    y1 = d.table('Last touch before the order', _rows_for(lt, s['paid']), d.m, d.y, half_w)
    y2 = d.table('What assisted the order', _rows_for(asst, s['paid']), col2, d.y, half_w, note='Assists overlap: one buyer can have several.')
    paid_where = _extra(payload, r"where they paid")
    if paid_where:
        d.table('Where they paid for it', _rows_for(paid_where, s['paid']), d.m, y1 - 16, half_w)
    d.meaning("Credit the last touch for the close and the assists for the warm-up. Both are needed to read the next campaign fairly.")

    # 07 why they left
    al = G.section(payload, 'after_left')
    al_rows = (al or {}).get('rows') or []
    top_why = al_rows[0] if al_rows else None
    d.new_page(False, 'Why they left', (f"The biggest group of the {_fmt(s['abandoned'])} who left the cart went looking for a discount." if top_why and re.search(r"discount|coupon|code", str(top_why.get('label')), re.I)
                                        else f"{_fmt(s['abandoned'])} people left the cart without paying."),
               f"The next thing each group did after leaving, and how many of them came back and paid, bought a competitor or bought elsewhere, or went quiet.")
    reason_rows = [(str(r.get('label') or ''), _fmt(r.get('accounts')), _pct(r.get('accounts'), s['abandoned'])) for r in al_rows[:8]]
    y1 = d.table('What they did after they left the cart', reason_rows, d.m, d.y, full_w, head=('Next step', 'People', 'Share'),
                 note=f"Share of the {_fmt(s['abandoned'])} who left the cart.")
    tot = {lab: sum(_sub(r, lab) for r in al_rows) for lab in G.AFTER_LEFT_SUBROWS}
    out_rows = [(lab, _fmt(n), _pct(n, s['abandoned'])) for lab, n in tot.items()]
    d.table('What happened next, all leavers', out_rows, d.m, y1 - 14, half_w, head=('Outcome', 'People', 'Share'))
    back_rows = []
    for r in al_rows[:5]:
        cb = _sub(r, G.AFTER_LEFT_SUBROWS[0])
        back_rows.append((str(r.get('label') or ''), _fmt(cb), _pct(cb, r.get('accounts'))))
    d.table('Who came back and paid, by reason', back_rows, col2, y1 - 14, half_w, head=('Next step', 'Came back', 'Of group'))
    safety = next((r for r in al_rows if re.search(r"caffeine|ingredient|safe|danger|health", str(r.get('label')), re.I)), None)
    comp = next((r for r in al_rows if re.search(r"competitor|alternative", str(r.get('label')), re.I)), None)
    bits = []
    if top_why:
        bits.append(f"{_pct(top_why.get('accounts'), s['abandoned'])} of the people who left {_lower_first(str(top_why.get('label')))}; "
                    f"{_pct(_sub(top_why, G.AFTER_LEFT_SUBROWS[0]), top_why.get('accounts'))} of them came back and paid.")
    if safety:
        bits.append(f"{_fmt(safety.get('accounts'))} left to check caffeine, ingredients or safety, and only {_pct(_sub(safety, G.AFTER_LEFT_SUBROWS[0]), safety.get('accounts'))} returned.")
    if comp:
        bits.append(f"{_fmt(comp.get('accounts'))} went to a competitor and {_pct(_sub(comp, G.AFTER_LEFT_SUBROWS[2]), comp.get('accounts'))} of them bought the other brand.")
    d.meaning(' '.join(bits) or f"{_fmt(s['paid_return'])} of the {_fmt(s['abandoned'])} who left came back and paid.")

    # 08 the whole audience
    ro, ev = G.section(payload, 'rest_overlap'), G.section(payload, 'every_buyer')
    d.new_page(False, 'The whole audience', f"{_fmt(s['buyers'])} people bought {T} online in the window.",
               f"The journey keeps the {_fmt(s['paid'])} whose order came within 30 days of a post. This page shows everyone else.")
    d.table('Social buyers outside the 30-day window', _rows_for(ro, s['carried'] - s['paid'] if s['carried'] > s['paid'] else None), d.m, d.y, half_w,
            note=f"Share of the {_fmt(s['carried'] - s['paid'])} buyers who engaged socially but bought outside the 30 days.")
    d.table(f"Every online {T} buyer in the window", _rows_for(ev, s['buyers']), col2, d.y, half_w)
    d.meaning(f"{_pct(s['no_post'], s['buyers'])} of the online buyers never saw a post. Social is the biggest door we can measure here, not the only one.")

    # 09 what to do
    d.new_page(True, 'What to do next', f"Four moves for the next {T} flight.", "Each one comes with the number that backs it.")
    d.bullets(G.recommendations(payload)[:4], d.m, d.y, full_w, size=12.5)
    d.meaning(f"Reply to the email this came with to run the same path for another product, or to cut this one by retailer, content family or flavor.")
    return d.finish()


__all__ = ['build_pdf']
