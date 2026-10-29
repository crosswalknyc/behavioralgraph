"""The studio report for a screening (Digital Twins IQ Pilot Testing,
2026-10-28): a deck and a PDF in the house system.

Pages, in order: the cover; what we found and what to do (the TLDR);
the topline with norms; the dial chart with the peaks and valleys marked
and the event rail; the quadrants; the mode read when the test has one
(trailer lift, title ranking, concept read, script notes); the
characters; the components; pace, confusion and the ending; the scenes
liked best and least with quotes; the recruit block; key conclusions and
suggestions in plain words.

One-line headers of twelve words or fewer; no source names; no doors;
no em dashes; the chart drawn with the house palette. The finish
(Crosswalk Inter embedded, the brand lint) runs through
``digital_twin_exports`` on every file.
"""
from __future__ import annotations

import io
import re
from typing import Optional

from migration.digital_twin_engine import human_people, pct1
from migration.digital_twin_exports import (
    GRAPHITE, SLATE, OFFWHITE, GREEN, ORCHID, PAVEMENT, BODY_DARK, MUTED_DARK, _words, _pct, _clock, _scorecard_rows, _quadrant_rows, _norm_note, build_deck, _Doc, _fin,
)

DUSK = '#B7B3D8'
COBALT = '#3358FF'
EVENT_COLOR = {'love': GREEN, 'laugh': GREEN, 'lean-in': COBALT, 'wow': ORCHID, 'confuse': DUSK, 'check-out': DUSK}
BANNED_WORDS = ('synth', 'synthetic', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap', 's3://', 'worker', 'persona', 'agent', 'screen engine', 'nrg', 'marketcast', 'fishdog')


def _clean(text: str) -> str:
    """Report copy: no em or en dash, no door metaphor, no internal
    term. The design finish runs the same scrubs on the file; this
    keeps the page dicts clean before they are drawn."""
    s = str(text or '').replace('\u2014', ', ').replace('\u2013', '-')
    try:
        from migration.design_skill import scrub_door_metaphor
        s = scrub_door_metaphor(s)
    except Exception:
        pass
    for w in BANNED_WORDS:
        s = re.sub(re.escape(w), 'the room', s, flags=re.I) if w not in ('screen engine', 'nrg', 'marketcast', 'fishdog') else re.sub(re.escape(w), 'the market', s, flags=re.I)
    return ' '.join(s.split())


def _head(session: dict) -> dict:
    return (((session or {}).get('scorecard') or {}).get('battery') or {}).get('headline') or {}


# ---------------------------------------------------------------- chart
def dial_chart_png(session: dict, *, width_px: int = 3400, height_px: int = 1190, splits: Optional[tuple] = None) -> Optional[bytes]:
    """The dial over the cut as a PNG: the room's line, the peaks and
    valleys marked, the event rail under the clock. None when matplotlib
    is not on the machine."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except Exception as e:
        print(f"[twin-report] chart skipped: {e}")
        return None
    dial = (session.get('dial') or {})
    ts, vs = dial.get('t') or [], dial.get('value') or []
    if not ts or not vs:
        return None
    n = min(len(ts), len(vs))
    ts, vs = [float(t) for t in ts[:n]], [float(v) for v in vs[:n]]
    dur = float((session.get('media') or {}).get('runtime_s') or ts[-1] or 1)
    face = None
    try:
        from migration import design_skill as ds
        fp = ds.font_path('Regular')
        if fp:
            font_manager.fontManager.addfont(str(fp))
            face = font_manager.FontProperties(fname=str(fp)).get_name()
            for w in ('Medium', 'Bold'):
                p = ds.font_path(w)
                if p:
                    font_manager.fontManager.addfont(str(p))
    except Exception:
        face = None
    dpi = max(72, int(round(width_px / 16.0)))     # the figure stays 16 by 5.6 inches, so type reads the same at any pixel width
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi, facecolor=GRAPHITE)
    ax = fig.add_axes([0.05, 0.22, 0.92, 0.70])
    ax.set_facecolor(GRAPHITE)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.axhline(50, color='#2B3A3E', linewidth=1)
    sp = dial.get('splits') or {}
    for key, color in (splits or (('women', ORCHID), ('men', COBALT))):
        line = (sp.get(key) or {}).get('value') or []
        if line:
            m = min(len(line), n)
            ax.plot(ts[:m], line[:m], color=color, linewidth=1.3, alpha=0.9, label=(sp.get(key) or {}).get('label') or key)
    ax.plot(ts, vs, color=OFFWHITE, linewidth=2.4, label='The room')
    for p in session.get('probes') or []:
        try:
            t = float(p.get('t') or 0)
            v = float(p.get('dial') or 0)
        except (TypeError, ValueError):
            continue
        ax.scatter([t], [v], s=70, color=GREEN if p.get('kind') == 'peak' else DUSK, zorder=5)
        ax.annotate(('peak ' if p.get('kind') == 'peak' else 'valley ') + str(p.get('clock') or _clock(t)), (t, v), textcoords='offset points', xytext=(0, 10 if p.get('kind') == 'peak' else -16),
                    ha='center', fontsize=8, color=GREEN if p.get('kind') == 'peak' else DUSK, fontname=face or None)
    ax.set_xlim(0, dur)
    ax.set_ylim(0, 100)
    ax.tick_params(colors=MUTED_DARK, labelsize=8)
    ticks = [0] + [dur * k / 6.0 for k in range(1, 7)]
    ax.set_xticks(ticks)
    ax.set_xticklabels([_clock(t) for t in ticks], fontname=face or None)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_yticklabels(['0', '25', '50', '75', '100'], fontname=face or None)
    ax.grid(axis='y', color='#1E2B2F', linewidth=0.6)
    leg = ax.legend(loc='upper left', frameon=False, fontsize=8, labelcolor=BODY_DARK, ncol=3, prop={'family': face} if face else None)
    # the event rail
    rail = fig.add_axes([0.05, 0.08, 0.92, 0.09])
    rail.set_facecolor(GRAPHITE)
    for sp_ in rail.spines.values():
        sp_.set_visible(False)
    rail.set_xlim(0, dur)
    rail.set_ylim(0, 1)
    rail.set_xticks([])
    rail.set_yticks([])
    rail.axhline(0.5, color=PAVEMENT, linewidth=1)
    for e in (session.get('events') or [])[:400]:
        try:
            t = float(e.get('t') or 0)
        except (TypeError, ValueError):
            continue
        rail.vlines(t, 0.2, 0.8, color=EVENT_COLOR.get(str(e.get('type') or ''), BODY_DARK), linewidth=1.2, alpha=0.9)
    rail.text(0, 0.95, 'Loved, laughed, leaned in (green, blue), wow (orchid), lost or checked out (dusk)', fontsize=7.5, color=MUTED_DARK, va='bottom', fontname=face or None)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, facecolor=GRAPHITE)
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------- pages
def _tldr(session: dict, payload: dict) -> list:
    head = _head(session)
    sc = session.get('scorecard') or {}
    d = session.get('diagnostics') or {}
    mr = session.get('mode_read') or {}
    out = []
    if head.get('total_positive_pct') is not None:
        out.append(f"Total positive {_pct(head.get('total_positive_pct'))}, definitely recommend {_pct(head.get('definitely_recommend_pct'))}" + (f", excellent {_pct(head.get('excellent_pct'))}." if head.get('excellent_pct') is not None else '.'))
    for l in (sc.get('norm_lines') or [])[:1]:
        out.append(l)
    if mr.get('read') and mr.get('mode') not in ('pilot', 'feature', 'episode', 'binge'):
        out.append(str(mr['read']).split('. ')[0].rstrip('.') + '.')
    ch = (d.get('characters') or {}).get('read')
    if ch:
        out.append(ch.split('. ')[0].rstrip('.') + '.')
    pace = d.get('pace') or {}
    if pace.get('running_time_read'):
        out.append(pace['running_time_read'])
    fav, dead = sc.get('favorite_moment') or {}, sc.get('dead_spot') or {}
    if fav.get('clock') and dead.get('clock'):
        out.append(f"Keep {fav['clock']}; look hard at {dead['clock']}.")
    return [_clean(x) for x in out if x][:5]


def _conclusions(session: dict) -> list:
    head = _head(session)
    sc = session.get('scorecard') or {}
    d = session.get('diagnostics') or {}
    out = []
    norms = (sc.get('norm_columns') or {})
    tp = head.get('total_positive_pct')
    tpn = (norms.get('total_positive') or {}).get('norm')
    if tp is not None and tpn is not None:
        gap = float(tp) - float(tpn)
        out.append(f"The cut plays {'above' if gap >= 3 else 'below' if gap <= -3 else 'at'} the norm on total positive ({_pct(tp)} against {float(tpn):.0f}%)." if abs(gap) >= 3 else f"The cut plays at the norm on total positive ({_pct(tp)}).")
    pace = d.get('pace') or {}
    slow = sorted([a for a in pace.get('acts') or [] if (a.get('too_slow_pct') or 0) >= 25], key=lambda a: -(a.get('too_slow_pct') or 0))
    if slow:
        out.append(f"Trim the {slow[0]['label'].lower()} ({slow[0]['from_clock']} to {slow[0]['to_clock']}); {_pct(slow[0]['too_slow_pct'])} found it slow.")
    if (pace.get('confusion_pct') or 0) >= 15 and pace.get('confusions'):
        out.append(f"Clear up {pace['confusions'][0]['line'][0].lower() + pace['confusions'][0]['line'][1:]}")
    chars = (d.get('characters') or {}).get('characters') or []
    if chars and chars[0].get('vs_norm_pp') is not None:
        lead = chars[0]
        out.append(f"Put {lead['name']} on the key art; the lead rates {_pct(lead['excellent_pct'])} excellent against a {float(lead['norm']):.0f}% norm." if lead['vs_norm_pp'] >= 5
                   else f"The room is not sold on {lead['name']} yet ({_pct(lead['excellent_pct'])} excellent against {float(lead['norm']):.0f}%); give the lead a clearer want early." if lead['vs_norm_pp'] <= -8
                   else f"{lead['name']} holds the lead at the norm ({_pct(lead['excellent_pct'])} excellent).")
    scenes = d.get('scenes') or {}
    if (scenes.get('liked_least') or []):
        s = scenes['liked_least'][0]
        out.append(f"The scene liked least sits at {s['clock']} ({s['mentions']} name it); cut or rework it before the next screening.")
    comps = (d.get('components') or {}).get('rows') or []
    weak = sorted([c for c in comps if c.get('vs_norm_pp') is not None], key=lambda c: c['vs_norm_pp'])
    if weak and weak[0]['vs_norm_pp'] <= -5:
        out.append(f"{weak[0]['label']} is the component under the norm ({_pct(weak[0]['excellent_pct'])} against {float(weak[0]['norm']):.0f}%).")
    mk = session.get('marketing_read') or {}
    if mk.get('trailer_moments'):
        out.append('For the trailer, start from ' + ', '.join(m['clock'] for m in mk['trailer_moments'][:3]) + '.')
    return [_clean(x) for x in out if x][:6]


def report_pages(payload: dict, session: dict) -> list:
    m = payload.get('meta') or {}
    name = str(m.get('name') or m.get('subject') or 'the twin')
    sc = session.get('scorecard') or {}
    head = _head(session)
    media = session.get('media') or {}
    title = str(session.get('title') or media.get('title') or 'the cut')
    d = session.get('diagnostics') or {}
    mr = session.get('mode_read') or {}
    mode = str(session.get('mode') or 'pilot')
    g = session.get('group') or {}
    pages = [{'kind': 'cover', 'header': f"{_clean(title)} x {name}", 'sub': _clean(f"What the {name} audience made of {session.get('cut_label') or 'the cut'}, in the room, on the clock."), 'dark': True}]
    pages.append({'kind': 'bullets', 'header': 'What we found, and what to do.', 'sub': '', 'body': _tldr(session, payload) or ['The room watched the cut; the scorecard follows.'], 'dark': True})
    tiles = [(_pct(head.get('total_positive_pct')) or f"{float(sc.get('overall_like') or 0):.1f}", 'Total positive' if head.get('total_positive_pct') is not None else 'Dial average'),
             (_pct(head.get('definitely_recommend_pct')) or '', 'Definitely recommend'), (_pct(head.get('excellent_pct')) or '', 'Excellent')]
    if head.get('finish_pct') is not None:
        tiles.append((_pct(head.get('finish_pct')), 'Would finish'))
    pages.append({'kind': 'tiles', 'header': _words(f"Total positive {_pct(head.get('total_positive_pct'))}, with the norm beside every number.") if head.get('total_positive_pct') is not None else 'The scorecard.',
                  'sub': _clean(f"{g.get('in_room') or len(g.get('roster') or [])} people in the room, {human_people(sc.get('people'))} behind them. {str(sc.get('format') or 'pilot').title()}" + (f", {sc.get('genre')}." if sc.get('genre') else '.')),
                  'tiles': [t for t in tiles if t[0]][:4], 'body': [_clean(x) for x in (sc.get('norm_lines') or [])[:3]], 'dark': False})
    pages.append({'kind': 'table', 'header': 'Every question on the card, next to its norm.', 'sub': '', 'head': ('Measure', 'Room', 'Norm'), 'rows': [(_clean(a), b, _clean(c)) for a, b, c in _scorecard_rows(sc)[:12]], 'dark': True})
    png = dial_chart_png(session)
    if png:
        fav, dead = sc.get('favorite_moment') or {}, sc.get('dead_spot') or {}
        pages.append({'kind': 'image', 'header': _words(f"The room peaked at {fav.get('clock') or '-'} and dipped at {dead.get('clock') or '-'}."), 'sub': 'The dial second by second, the peaks and valleys marked, every reaction on the rail under it.',
                      'image': png, 'body': [_clean(f"Favorite moment, {fav.get('clock')}: {fav.get('line')}"), _clean(f"Dead spot, {dead.get('clock')}: {dead.get('line')}")], 'dark': True})
    qrows = _quadrant_rows(sc)
    if qrows:
        pages.append({'kind': 'table', 'header': 'The four quadrants, side by side.', 'sub': 'Total positive, with definitely recommend and excellent beside it.', 'head': ('Group', 'Total positive', 'And'), 'rows': qrows[:10], 'dark': False})
    # the mode read
    if mode in ('trailer', 'spot') and mr.get('interest_after_pct') is not None:
        pages.append({'kind': 'tiles', 'header': _words(f"Definite interest moves {'+' if (mr.get('definite_interest_lift_pp') or 0) >= 0 else ''}{mr.get('definite_interest_lift_pp')} points after the trailer."),
                      'sub': _clean(mr.get('who_for', {}).get('line') or ''), 'tiles': [(_pct(mr.get('interest_before_pct')), 'Definitely interested before'), (_pct(mr.get('interest_after_pct')), 'Definitely interested after'),
                                                                                       (_pct(mr.get('would_watch_pct')), 'Would watch or see it'), (_pct(mr.get('recommend_pct')), 'Definitely recommend')],
                      'body': [_clean(f"{s['clock']}: {s['line']}") for s in (mr.get('sold_it') or [])[:3]], 'dark': True})
        if (mr.get('compare') or {}).get('ranked'):
            pages.append({'kind': 'table', 'header': _words(_clean((mr['compare'].get('read') or 'The assets side by side.').split('. ')[0] + '.')), 'sub': 'Definite interest lift, interest after, would watch.',
                          'head': ('Asset', 'Lift', 'After and would watch'), 'rows': [(r['asset_label'], f"{'+' if (r.get('definite_interest_lift_pp') or 0) >= 0 else ''}{r.get('definite_interest_lift_pp')} pts", f"{_pct(r.get('interest_after_pct'))} after, {_pct(r.get('would_watch_pct'))} would watch") for r in mr['compare']['ranked']], 'dark': False})
    elif mode in ('title', 'key_art') and mr.get('options'):
        rows = [(f"{o['rank']}. {o['label']}", _pct(o['preference_pct']), _clean(o['reasons'][0]['line']) if o.get('reasons') else (f"fits {_pct(o.get('fit_pct'))}" if o.get('fit_pct') is not None else '')) for o in mr['options'][:6]]
        pages.append({'kind': 'table', 'header': _words(_clean(mr.get('read') or 'The options, ranked.')), 'sub': 'The share who prefer each option, with a reason from the room.', 'head': ('Option', 'Prefer', 'Why'), 'rows': rows, 'dark': True})
    elif mode == 'concept' and mr.get('definitely_interested_pct') is not None:
        pages.append({'kind': 'tiles', 'header': _words(f"{_pct(mr.get('definitely_interested_pct'))} are definitely interested in the idea."), 'sub': _clean((mr.get('who_for') or {}).get('line') or ''),
                      'tiles': [(_pct(mr.get('definitely_interested_pct')), 'Definitely interested'), (_pct(mr.get('unique_pct')), 'Stands out'), (_pct(mr.get('would_watch_pct')), 'Would watch'),
                                (_pct((mr.get('strongest') or [{}])[0].get('pct')), f"Strongest: {(mr.get('strongest') or [{}])[0].get('label', '')}")],
                      'body': [_clean(f"Biggest doubt: {(mr.get('doubt') or [{}])[0].get('label', '')} ({_pct((mr.get('doubt') or [{}])[0].get('pct'))}).")], 'dark': True})
    elif mode == 'script' and mr.get('scenes'):
        rows = []
        for s in mr['scenes'][:12]:
            notes = ' '.join(v for v in (s.get('notes') or {}).values() if v)
            if notes:
                rows.append((f"{s['clock']} {s['slug'][:40]}", f"{s.get('dial') if s.get('dial') is not None else '-'}", _clean(notes)[:90]))
        if rows:
            pages.append({'kind': 'table', 'header': 'Scene by scene: where the script works and where it drags.', 'sub': _clean(mr.get('read') or ''), 'head': ('Scene', 'Dial', 'Note'), 'rows': rows[:12], 'dark': True})
    # characters
    chars = (d.get('characters') or {}).get('characters') or []
    if chars:
        rows = [(f"{c['name']} ({c['role']})", _pct(c.get('excellent_pct')), _clean(f"norm {float(c['norm']):.0f}%, {c['screen_clock']} on screen, {c['time_vs_like']}") if c.get('norm') is not None else _clean(f"{c['screen_clock']} on screen, {c['time_vs_like']}")) for c in chars[:8]]
        fol = (d.get('characters') or {}).get('follow') or []
        pages.append({'kind': 'table', 'header': _words(_clean((d['characters'].get('read') or 'The characters, rated.').split('. ')[0] + '.')), 'sub': _clean(('Who they would follow into a second episode: ' + ', '.join(f"{f['name']} {_pct(f['pct'])}" for f in fol[:3]) + '.') if fol else ''),
                      'head': ('Character', 'Excellent', 'Norm and screen time'), 'rows': rows, 'dark': False})
    comps = (d.get('components') or {}).get('rows') or []
    if comps:
        pages.append({'kind': 'table', 'header': _words(_clean((d['components'].get('read') or 'The components, next to their norms.').split('. ')[0] + '.')), 'sub': 'Top box (excellent) for each component, with the norm.',
                      'head': ('Component', 'Excellent', 'Norm'), 'rows': [(c['label'], _pct(c.get('excellent_pct')), (f"norm {float(c['norm']):.0f}%" + (f" ({'+' if c['vs_norm_pp'] >= 0 else ''}{c['vs_norm_pp']})" if c.get('vs_norm_pp') is not None else '')) if c.get('norm') is not None else 'no norm yet') for c in comps], 'dark': True})
    pace = d.get('pace') or {}
    if pace.get('acts'):
        rows = [(a['label'] + f" ({a['from_clock']} to {a['to_clock']})", _pct(a.get('just_right_pct')) + ' just right', f"{_pct(a.get('too_slow_pct'))} too slow, {_pct(a.get('too_fast_pct'))} too fast") for a in pace['acts']]
        rows.append(('Beginning on its own', _pct((pace.get('beginning') or {}).get('excellent_pct')), 'excellent'))
        rows.append(('Ending on its own', _pct((pace.get('ending') or {}).get('excellent_pct')), 'excellent'))
        rows.append(('Confused somewhere', _pct(pace.get('confusion_pct')), _clean(pace['confusions'][0]['line']) if pace.get('confusions') else 'no written confusions'))
        pages.append({'kind': 'table', 'header': _words(_clean(pace.get('running_time_read') or 'Pace by act, confusion, the beginning and the ending.')), 'sub': '', 'head': ('Stretch', 'Read', 'And'), 'rows': rows[:10], 'dark': False})
    scenes = d.get('scenes') or {}
    if scenes.get('liked_best'):
        rows = [(f"{s['clock']} {s['on_screen'][:48]}", f"{s['mentions']} name it", _clean(s['quotes'][0]['line']) if s.get('quotes') else '') for s in scenes['liked_best'][:6]]
        pages.append({'kind': 'table', 'header': 'The scenes liked best, in the room\'s words.', 'sub': '', 'head': ('Scene', 'Mentions', 'Quote'), 'rows': rows, 'dark': True})
    if scenes.get('liked_least'):
        rows = [(f"{s['clock']} {s['on_screen'][:48]}", f"{s['mentions']} name it", _clean(s['quotes'][0]['line']) if s.get('quotes') else '') for s in scenes['liked_least'][:6]]
        nots = scenes.get('not_recommending') or []
        pages.append({'kind': 'table', 'header': 'The scenes liked least, and why some would not recommend it.', 'sub': _clean(('Reasons for not recommending: ' + '; '.join(n['line'] for n in nots[:3])) if nots else ''),
                      'head': ('Scene', 'Mentions', 'Quote'), 'rows': rows, 'dark': False})
    rec = session.get('recruit') or {}
    if rec:
        w = rec.get('walkouts') or {}
        tiles = [(str(rec.get('seated') or g.get('in_room') or ''), 'Seated'), (f"{rec.get('invited'):,}" if rec.get('invited') else str(rec.get('seated') or ''), 'Invited' if rec.get('invited') else 'Seated'),
                 (str(w.get('count') if w.get('count') is not None else 0), 'Walked out'), (rec.get('invite_ratio_n') and f"{rec['invite_ratio_n']} : 1" or '1 : 1', 'Invite ratio')]
        pages.append({'kind': 'tiles', 'header': _words(_clean(f"Recruited to {rec.get('concept') or 'the twin'}.")), 'sub': _clean(rec.get('stats_line') or ''), 'tiles': tiles[:4],
                      'body': [_clean(f"{q['quota']}: {q['target_pct']}% asked for, {q['seated_pct']}% seated") for q in (rec.get('quotas_met') or [])[:3]] + ([_clean(w.get('read'))] if w.get('read') else []), 'dark': True})
    cr = session.get('critics') or {}
    if cr.get('critic_score') is not None:
        pages.append({'kind': 'tiles', 'header': _words(f"Critic score forecast {cr['critic_score']:.0f}."), 'sub': _clean(cr.get('awards_read') or ''), 'tiles': [(f"{cr['critic_score']:.0f}", 'Critic score'), (f"{cr['range'][0]:.0f} to {cr['range'][1]:.0f}", 'Likely range')], 'body': [], 'dark': False})
    pages.append({'kind': 'bullets', 'header': 'Key conclusions and what to do next.', 'sub': '', 'body': _conclusions(session) or ['Keep the favorite moment. Look hard at the dead spot.'], 'dark': False})
    for pg in pages:
        pg['header'] = _clean(pg.get('header') or '')
        pg['sub'] = _clean(pg.get('sub') or '')
        if pg.get('body'):
            pg['body'] = [_clean(b) for b in pg['body'] if b]
    return pages


def build_report_deck(payload: dict, session: dict) -> bytes:
    return build_deck(payload, pages=report_pages(payload, session))


def build_report_pdf(payload: dict, session: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.utils import ImageReader
    d = _Doc()
    pages = report_pages(payload, session)
    for i, pg in enumerate(pages, 1):
        dark = bool(pg.get('dark'))
        if pg['kind'] == 'cover':
            d.new_page(True, 'Pilot Testing', pg['header'], pg.get('sub') or '')
            continue
        y = d.new_page(dark, 'Pilot Testing', pg['header'], pg.get('sub') or '')
        if pg['kind'] == 'bullets':
            y = d.bullets(pg.get('body') or [], d.m, y, d.right - d.m)
        elif pg['kind'] == 'table':
            y = d.table((pg.get('rows') or [])[:12], d.m, y, d.right - d.m, head=pg.get('head') or ('', '', ''))
            for b in (pg.get('body') or [])[:3]:
                y = d.paragraph(b, d.m, y - 4, d.right - d.m, size=10)
        elif pg['kind'] == 'tiles':
            y = d.tiles(pg.get('tiles') or [], d.m, y, d.right - d.m)
            for b in (pg.get('body') or [])[:4]:
                y = d.paragraph(b, d.m, y - 2, d.right - d.m, size=10.5)
        elif pg['kind'] == 'image' and pg.get('image'):
            w = d.right - d.m
            h = w * 1190.0 / 3400.0
            try:
                d.c.drawImage(ImageReader(io.BytesIO(pg['image'])), d.m, y - h, width=w, height=h, mask='auto')
                y = y - h - 14
            except Exception:
                pass
            for b in (pg.get('body') or [])[:2]:
                y = d.paragraph(b, d.m, y, d.right - d.m, size=10)
    return _fin(d.finish(), '.pdf')


def report_filename(payload: dict, session: dict, ext: str) -> str:
    m = payload.get('meta') or {}
    return re.sub(r"[^A-Za-z0-9]+", '_', f"{m.get('name') or m.get('subject')} Screening Report {session.get('id')}").strip('_') + ext


__all__ = ['dial_chart_png', 'report_pages', 'build_report_deck', 'build_report_pdf', 'report_filename', 'BANNED_WORDS']
