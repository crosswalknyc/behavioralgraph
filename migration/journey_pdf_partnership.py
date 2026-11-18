"""The analysis PDF for a purchase journey on a partnership we already
valued (`migration.journey_bpiq_anchor`). Same deck system as the other
journey PDFs: landscape letter, Crosswalk palette, plain one-line
headers, a "what this means" line on every page.

The standard purchase PDF reads a journey that starts on a post and ends
on an order inside 30 days. A valued partnership starts on the people the
partnership reached, runs a whole season, and lands on the valuation's
own counts, so it has its own words (2026-11-17, Coach x WNBA).
"""
from __future__ import annotations

import re
from typing import List, Optional

from migration.journey_pdf import _Doc, _fmt, _pct, _rows_for, BODY_DARK
from migration import journey_gold_purchase as G


def _find(payload: dict, rx: str) -> Optional[dict]:
    j = payload.get('fragrance_shop_journey') or {}
    return next((d for d in (j.get('detours') or []) if re.search(rx, str(d.get('title') or ''), re.I)), None)


def _n(v) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def _money(v) -> str:
    return f"${float(v):,.2f}"


def _low(t: str) -> str:
    t = str(t or '')
    return t[:1].lower() + t[1:] if re.match(r"^(?:A|An|The|Saw|Read|Went|Left|Took|Got|Came|Kept|Put|Did|Bought|Sent|[A-Z][a-z]+ed)\b", t) else t


def _short(t: str) -> str:
    """A label inside a one-line header: the part before a colon or comma."""
    return re.split(r"[:,]", str(t or ''), 1)[0].strip()


def _top(sec: Optional[dict]) -> Optional[dict]:
    rows = [r for r in ((sec or {}).get('rows') or []) if _n(r.get('accounts')) > 0]
    return max(rows, key=lambda r: _n(r.get('accounts'))) if rows else None


def facts(payload: dict) -> dict:
    """Everything the words need, read from the payload alone."""
    j = payload.get('fragrance_shop_journey') or {}
    meta = payload.get('meta') or {}
    va = meta.get('valuation_anchor') or {}
    spine = j.get('spine') or []
    fork = {str(f.get('id')): _n(f.get('accounts')) for f in (j.get('fork') or [])}
    by = {str(s.get('id')): s for s in spine}
    out = {
        'brand': str(va.get('brand') or meta.get('product') or ''),
        'project': str(va.get('project') or meta.get('project_name') or ''),
        'names': str(va.get('names') or ''),
        'reached': _n(va.get('reached')), 'engaged': _n(va.get('engaged')), 'bought': _n(va.get('converted')),
        'added': _n(va.get('added')), 'order_value': va.get('order_value'), 'conversion_value': va.get('conversion_value'),
        'window': (f"{meta.get('start_date')} to {meta.get('end_date')}" if meta.get('start_date') and meta.get('end_date') else ''),
        'platform': str(meta.get('platform_label') or '').split(' (')[0].strip(),
        'reached_label': str((by.get('reached') or {}).get('label') or 'Saw the content'),
        'cart': _n(spine[-2].get('accounts')) if len(spine) > 2 else 0,
        'page': _n(spine[-3].get('accounts')) if len(spine) > 3 else 0,
        'abandoned': fork.get('abandoned', 0), 'paid_return': fork.get('paid_return', 0),
    }
    t = _find(payload, r"^time from")
    rows = [(str(r.get('label') or ''), _n(r.get('accounts'))) for r in ((t or {}).get('rows') or [])]
    out['time_rows'] = rows
    out['late'] = sum(n for lb, n in rows if re.search(r"31|90|more than", lb, re.I))
    return out


def short_points(payload: dict) -> List[str]:
    """The short version, in plain sentences. Used by the PDF and by the
    lines that travel with a delivered journey."""
    f = facts(payload)
    if not (f['reached'] and f['engaged'] and f['bought']):
        return []
    B = f['brand']
    pts = [
        (f"{f['reached']:,} people {_low(f['reached_label'])} in the window. {f['engaged']:,} of them "
         f"({f['engaged'] / f['reached'] * 100:.1f}%) engaged with {B} content"
         + (f", {f['added']:,} more than in the year before." if f['added'] else '.')),
        (f"{f['bought']:,} bought on {f['platform'] or 'the brand site'}, {f['bought'] / f['engaged'] * 100:.1f}% of the people who engaged."
         + (f" At {_money(f['order_value'])} an order that is {_money(f['conversion_value'] or f['bought'] * float(f['order_value']))}, "
            f"the same figure as the partnership valuation." if f['order_value'] else '')),
    ]
    fc = _top(_find(payload, r"^first .*content they saw"))
    if fc:
        pts.append(f"For {_n(fc['accounts']):,} of the people who engaged ({_n(fc['accounts']) / f['engaged'] * 100:.1f}%), "
                   f"the first {B} content was {_low(_short(fc['label']))}.")
    pl = [r for r in ((_find(payload, r"^where they engaged with") or {}).get('rows') or []) if _n(r.get('accounts')) > 0]
    if len(pl) > 1:
        pts.append(f"{pl[0]['label']} carried the most {B} engagement, {_n(pl[0]['accounts']):,} people, ahead of "
                   f"{pl[1]['label']} with {_n(pl[1]['accounts']):,}.")
    if f['cart'] and f['abandoned']:
        pts.append(f"{f['abandoned']:,} of the {f['cart']:,} who added a piece to the bag left without buying "
                   f"({f['abandoned'] / f['cart'] * 100:.1f}%), and {f['paid_return']:,} of them came back and bought.")
    if f['late'] and f['bought']:
        pts.append(f"{f['late'] / f['bought'] * 100:.1f}% of the orders came more than 30 days after the first content. "
                   f"The season did the selling, not one night.")
    tp = _top(G.section(payload, 'first_product') or _find(payload, r"^what they bought first"))
    if tp:
        pts.append(f"The most common first order was {_low(tp['label'])}, for {_n(tp['accounts']):,} of the {f['bought']:,} buyers.")
    return pts


def short_lines(payload: dict) -> List[str]:
    f = facts(payload)
    pts = short_points(payload)
    if not pts:
        return []
    head = f"The short version, {f['window']}, unique US people:" if f['window'] else "The short version, unique US people:"
    return [head] + [f"- {p}" for p in pts] + ['', (
        f"How this ties to the valuation you already have: the people reached, the people who engaged with {f['brand']} and the "
        f"buyers are the same counts as the {f['project']} partnership valuation, in the same window.")]


def moves(payload: dict) -> List[str]:
    out = []
    for m in ((payload.get('the_read') or {}).get('moves') or []):
        if isinstance(m, dict) and str(m.get('move') or '').strip():
            mv, why = str(m['move']).strip(), str(m.get('why') or '').strip()
            out.append(f"{mv if mv.endswith('.') else mv + '.'} {why if not why or why.endswith('.') else why + '.'}".strip())
    return out[:4] or G.recommendations(payload)[:4]


def build_pdf(payload: dict) -> bytes:
    f = facts(payload)
    B = f['brand']
    meta = payload.get('meta') or {}
    j = payload.get('fragrance_shop_journey') or {}
    d = _Doc()
    full_w = d.right - d.m
    gut = 0.28 * 72
    half_w = (full_w - gut) / 2
    col2 = d.m + half_w + gut

    # 01 cover: the pairing, then the claim
    hero = str(meta.get('hero_image') or '')
    title = f['project'] or str(meta.get('project_name') or B)
    tw = full_w * (0.52 if hero else 1.0)
    d.new_page(True, 'Digital Journey IQ', title + ('' if title.endswith('.') else '.'),
               f"Unique US people, {f['window']}. Every count is a person, not a household.", text_width=tw)
    if hero:
        d.cover_photo(hero, credit=str(meta.get('hero_credit') or ''))
    d.paragraph(f"What the people who {_low(f['reached_label'])} did next: who engaged with {B}, who shopped, who bought, "
                f"who left the bag and why, and the {len(j.get('detours') or [])} tables behind it.",
                d.m, d.y - 10, tw * 0.9, size=12, color=BODY_DARK)

    # 02 the short version
    d.new_page(False, 'The short version', f"{_fmt(f['bought'])} of the people the partnership reached bought {B} online.",
               f"{f['bought'] / f['reached'] * 100:.2f}% of the {_fmt(f['reached'])} people reached. Here is what matters and what to do.")
    d.bullets(short_points(payload), d.m, d.y, full_w, size=12)
    d.meaning(f"The partnership reached {_fmt(f['reached'])} people and {_fmt(f['bought'])} of them bought. "
              f"The next pages show each step between.")

    # 03 the path
    spine = j.get('spine') or []
    d.new_page(False, 'The path', "From the first content to the order, step by step.",
               f"Each step keeps a share of the one before it. {_fmt(f['reached'])} people at the start; {_fmt(f['bought'])} bought.")
    rows, prev = [], None
    for st in spine[1:]:
        n = _n(st.get('accounts'))
        lab = str(st.get('label') or '')
        rows.append((lab if len(lab) <= 42 or prev else 'Reached by the partnership', _fmt(n), _pct(n, prev) if prev else 'start'))
        prev = n
    d.table('The spine', rows, d.m, d.y, half_w, head=('Step', 'People', 'Kept'))
    frows = [(str(x.get('label') or x.get('id')), _fmt(x.get('accounts')), f"{float(x.get('kept') or 0):.1f}%") for x in (j.get('fork') or [])]
    d.table('The bag: who bought on the spot, who left, who came back', frows, col2, d.y, half_w, head=('Branch', 'People', 'Kept'),
            note='Kept is the share of the branch above it. Same-session orders and orders after a return add up to the buyers.')
    after = [(float(st.get('kept') or 0), str(st.get('label') or '')) for st in spine[3:] if float(st.get('kept') or 0) > 0]
    worst = min(after) if after else None
    d.meaning(f"The biggest loss after engaging is at \"{_low(worst[1])}\": {worst[0]:.1f}% of the step before got there." if worst
              else f"{_pct(f['bought'], f['cart'])} of the people who added a piece to the bag bought.")

    # 04 where it started
    fc, pl, gr = _find(payload, r"^first .*content they saw"), _find(payload, r"^where they engaged with"), _find(payload, r"^growth in the")
    top_fc = _top(fc)
    d.new_page(False, 'Where it started',
               (f"{_short(top_fc['label'])} was the most common first {B} content." if top_fc else f"Where the {B} audience started."),
               f"The first {B} content each of the {_fmt(f['engaged'])} people who engaged saw, and where they engaged.")
    y1 = d.table(f'First {B} content they saw', _rows_for(fc, f['engaged']), d.m, d.y, half_w, head=('Content', 'People', 'Share'),
                 note=f"Share of the {_fmt(f['engaged'])} who engaged with {B}.")
    y2 = d.table(f'Where they engaged with {B}', _rows_for(pl, f['engaged'], limit=12), col2, d.y, half_w, head=('Place', 'People', 'Share'),
                 note='One person can engage in more than one place.')
    if gr:
        d.table(f'Growth in the {B} audience', _rows_for(gr, f['engaged']), d.m, y1 - 16, half_w, head=('', 'People', 'Share'))
    d.meaning((f"{_fmt(f['added'])} of the {_fmt(f['engaged'])} people who engaged were new to {B} in the window. " if f['added'] else '') +
              "The partnership content is where those people started.")

    # 05 from the content to the order
    lb, tb, fpr = G.section(payload, 'looked_before'), _find(payload, r"^time from"), _find(payload, r"^what they bought first")
    d.new_page(False, 'From the content to the order',
               (f"{_pct(f['late'], f['bought'])} of the orders came more than 30 days later." if f['late'] else "How long the order took."),
               "Where they looked before the bag, how long the order took, and what they bought first.")
    y1 = d.table('Where they looked before the bag', _rows_for(lb, f['page']), d.m, d.y, half_w, head=('', 'People', 'Share'),
                 note=f"Share of the {_fmt(f['page'])} on the step before the bag. A person can appear in more than one row.")
    y2 = d.table('Time from the first content to the order', _rows_for(tb, f['bought']), col2, d.y, half_w, head=('When', 'People', 'Share'))
    d.table('What they bought first', _rows_for(fpr, f['bought'], limit=8), col2, y2 - 16, half_w, head=('Piece', 'People', 'Share'))
    same = next((n for lbl, n in f['time_rows'] if re.match(r"same day", lbl, re.I)), 0)
    d.meaning((f"Only {_pct(same, f['bought'])} ordered the same day. " if same else '') +
              "Most buyers needed more than one beat of the season, so the content has to keep coming.")

    # 06 what closed it
    lt, asst, ft = G.section(payload, 'last_touch'), G.section(payload, 'assists'), _find(payload, r"^first touch")
    top_lt = _top(lt)
    d.new_page(False, 'What closed it',
               (f"The top last touch, {_low(_short(top_lt['label']))}, closed {_pct(top_lt.get('accounts'), f['bought'])} of the orders."
                if top_lt else "The last touch before the order."),
               f"The final step before the {_fmt(f['bought'])} orders and what else was in the mix.")
    y1 = d.table('Last touch before the order', _rows_for(lt, f['bought']), d.m, d.y, half_w, head=('Touch', 'People', 'Share'))
    d.table('What assisted the order', _rows_for(asst, f['bought']), col2, d.y, half_w, head=('Assist', 'People', 'Share'),
            note='Assists overlap: one buyer can have several.')
    if ft:
        d.table(str(ft.get('title')), _rows_for(ft, f['bought']), d.m, y1 - 16, half_w, head=('Touch', 'People', 'Share'))
    d.meaning("Credit the last touch for the close and the assists for the warm-up. Both are needed to read the partnership fairly.")

    # 07 why they left
    al, ww = G.section(payload, 'after_left'), G.section(payload, 'where_went')
    top_al = _top(al)
    d.new_page(False, 'Why they left', f"{_fmt(f['abandoned'])} people left the bag without buying.",
               "The next thing each group did after leaving, and the first place they went.")
    d.table('What they did after they left the bag', _rows_for(al, f['abandoned']), d.m, d.y, half_w, head=('Next step', 'People', 'Share'),
            note=f"Share of the {_fmt(f['abandoned'])} who left the bag.")
    d.table('Where they went next', _rows_for(ww, f['abandoned']), col2, d.y, half_w, head=('Destination', 'People', 'Share'))
    d.meaning((f"The biggest group, {_fmt(top_al.get('accounts'))} people, {_low(str(top_al.get('label')))}. " if top_al else '') +
              f"{_fmt(f['paid_return'])} of the people who left came back and bought.")

    # 08 what they bought and searched for
    tp, sr = G.section(payload, 'top_products'), G.section(payload, 'searched')
    lead = _top(tp)
    if (tp or {}).get('rows') or (sr or {}).get('rows'):
        d.new_page(False, 'What they bought and searched for',
                   (f"{lead['label']} led the orders at {_pct(lead.get('accounts'), f['bought'])}." if lead else "What they bought and typed."),
                   f"The pieces bought most across the {_fmt(f['bought'])} orders, and what people typed on the way.")
        if (tp or {}).get('rows'):
            d.table(str(tp.get('title') or 'Top 10 products bought'), _rows_for(tp, f['bought'], limit=11), d.m, d.y, half_w,
                    head=('Piece', 'People', 'Share'), note=f"Share of the {_fmt(f['bought'])} orders.")
        if (sr or {}).get('rows'):
            d.table('What they searched for', _rows_for(sr, _n(sr.get('base')) or None, limit=7), col2, d.y, half_w, head=('Cluster', 'People', 'Share'))
        c0 = _top(sr)
        d.meaning((f"{c0['label']} was the biggest search cluster. " if c0 else '') +
                  "The pieces the players carried show up in both lists.")

    # 09 who they are
    who = payload.get('who_they_are') or {}
    if any(who.get(k) for k in ('gender', 'age', 'ethnicity', 'income')):
        g = max((who.get('gender') or [{}]), key=lambda r: float(r.get('pct') or 0))
        a = max((who.get('age') or [{}]), key=lambda r: float(r.get('pct') or 0))
        d.new_page(False, 'Who they are', f"{float(g.get('pct') or 0):.1f}% of the people who engaged are {str(g.get('label') or '').lower()}.",
                   f"The {_fmt(f['engaged'])} people who engaged with {B} in the window.")
        def _who(key, name, x, y):
            rws = [(str(r.get('label') or ''), '', f"{float(r.get('pct') or 0):.1f}%") for r in (who.get(key) or [])[:8]]
            return d.table(name, rws, x, y, half_w, head=('', '', 'Share'))
        ya, yb = _who('gender', 'Gender', d.m, d.y), _who('age', 'Age', col2, d.y)
        _who('ethnicity', 'Ethnicity', d.m, ya - 14)
        _who('income', 'Income', col2, yb - 14)
        d.meaning(f"The largest age group is {a.get('label')}, {float(a.get('pct') or 0):.1f}% of the people who engaged.")

    # 10 what to do
    d.new_page(True, 'What to do next', "Four moves for the next season.", "Each one comes with the reason behind it.")
    d.bullets(moves(payload), d.m, d.y, full_w, size=12.5)
    d.meaning("The same path can be cut by player, by piece or by platform.")
    return d.finish()


__all__ = ['build_pdf', 'short_points', 'short_lines', 'facts', 'moves']
