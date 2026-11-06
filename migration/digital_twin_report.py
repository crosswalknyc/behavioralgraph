"""The studio report for a screening (Digital Twins IQ Pilot Testing,
2026-10-28): a deck and a PDF in the house system.

Pages, in order: the cover; what we found and what to do (the TLDR);
the topline with norms; the dial chart with the peaks and valleys marked
and the event rail; the quadrants; the mode read when the test has one
(trailer lift, title ranking, concept read, script notes); the
characters; the components; pace, confusion and the ending; the scenes
liked best and least with quotes; the questions the moderator put to
the room, during the cut and after it wrapped (one page each: the
question as asked and when, the framing, the split with the seat tally,
the read, the voices, what the room knew and what people were saying;
Jenna 2026-11-04); the recruit block; the forecast; key conclusions
and suggestions in plain words.

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
    GRAPHITE, SLATE, OFFWHITE, GREEN, OLIVE, ORCHID, PAVEMENT, BODY_DARK, MUTED_DARK, BODY_LIGHT, MUTED_LIGHT, _words, _pct, _clock, _scorecard_rows, _quadrant_rows, _norm_note, build_deck, _Doc, _fin,
    question_header, question_lead_line, fit_quote, proper_case, known_names,
)
# the room pages ride inside report_pages so every format carries them (2026-11-04)
from migration.digital_twin_exports import room_pages as _room_pages, moments_pages as _moments_pages, draw_roster_pdf as _draw_roster_pdf
# who was in the room, seat by seat, after the questions (2026-11-05)
from migration.digital_twin_exports import seat_pages as _seat_pages, draw_seats_pdf as _draw_seats_pdf

DUSK = '#B7B3D8'
COBALT = '#3358FF'
EVENT_COLOR = {'love': GREEN, 'laugh': GREEN, 'lean-in': COBALT, 'wow': ORCHID, 'confuse': DUSK, 'check-out': DUSK}
BANNED_WORDS = ('synth', 'synthetic', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap', 's3://', 'worker', 'persona', 'agent', 'screen engine', 'nrg', 'marketcast', 'fishdog')


def _people_short(v) -> str:
    """8,813,888 -> 8.81M; 743,399 -> 743K; smaller stays exact."""
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return ''
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 10_000:
        return f"{n / 1_000:.0f}K"
    return f"{int(round(n)):,}"


def _clean(text: str) -> str:
    """Report copy: no em or en dash, no door metaphor, no internal
    term. The design finish runs the same scrubs on the file; this
    keeps the page dicts clean before they are drawn."""
    s = str(text or '').replace('\u2014', ', ').replace('\u2013', '-')
    try:   # no calibrated / anchored / as aired / ratings-service sentence on a page (Jenna 2026-11-05)
        from migration.digital_twin_copy import strip_calibration_language
        s = strip_calibration_language(s)
    except Exception:
        pass
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


def _choice(sc: dict, session: dict) -> dict:
    """The norm choice on the card; an older card without one is read
    from its norm columns (the genre has a norm only when the columns
    say so; the all column is a stand-in when its money cells are)."""
    ch = sc.get('norm_choice') if isinstance(sc.get('norm_choice'), dict) else {}
    if ch.get('compared_label') or (ch.get('label') and 'stand_in' in ch):
        return ch
    try:
        from migration import digital_twin_norms as N
        norms = sc.get('norms') if isinstance(sc.get('norms'), dict) else {}
        fmt = str(sc.get('format') or (session or {}).get('format') or (session or {}).get('mode') or 'pilot')
        genre = str(sc.get('genre') or (session or {}).get('genre') or 'all')
        has = str(norms.get('genre_used') or 'all') not in ('', 'all')
        stand_in = N._column_stand_in(norms.get('all') or {}) if norms else ''
        fresh = N.norm_choice(fmt, genre, has_genre_norm=has, stand_in=stand_in)
        if ch:
            fresh.update({k: v for k, v in ch.items() if k in ('own_tests',)})
        return fresh
    except Exception:
        return ch


def _compared_to(sc: dict, session: dict) -> str:
    """'Compared to all pilots.' / 'Compared to procedural pilots.' /
    'Compared to feature screenings.' for the scorecard pages: the norm
    the card is actually read against, from the one helper the card and
    the CSV use (never a source)."""
    try:
        from migration.digital_twin_norms import compared_line
        line = compared_line(_choice(sc, session))
    except Exception:
        line = ''
    return _clean(line) if line else ''


def _denominator(session: dict, sc: dict, seats: int, people) -> str:
    """The plain statement of what the shares are of, once on the
    scorecard page and once on the TLDR."""
    if sc.get('denominator_line'):
        return str(sc['denominator_line'])
    try:
        from migration.digital_twin_pilot import denominator_line
        return denominator_line(seats, people)
    except Exception:
        return ''


def _tallies(sc: dict) -> dict:
    try:
        from migration.digital_twin_pilot import battery_tallies
        return battery_tallies(sc.get('battery') or {})
    except Exception:
        return {}


def _norm_name(sc: dict, metric: str) -> str:
    """'the feature screening norm' while the row's column is a stand-in,
    'the procedural pilot norm' when the genre has one, else 'the
    all-pilots norm' (from the choice), for a page's sub-line."""
    col = ((sc.get('norm_columns') or {}).get(metric)) or {}
    si = str(col.get('stand_in') or '')
    if si and not col.get('genre_own'):
        return f"the {si} norm"
    try:
        from migration.digital_twin_norms import norm_word
        ch = _choice(sc, {})
        if col.get('genre_own') or not ch.get('stand_in'):
            return norm_word(dict(ch, stand_in=''))
        return norm_word(ch)
    except Exception:
        return 'the norm'


def _norm_cell(sc: dict, metric: str, value) -> str:
    """'norm 51%' / 'feature screening norm 50%' for a table cell."""
    if value is None:
        return ''
    col = ((sc.get('norm_columns') or {}).get(metric)) or {}
    si = str(col.get('stand_in') or '')
    return f"{si} norm {float(value):.0f}%" if (si and not col.get('genre_own')) else f"norm {float(value):.0f}%"


def _headline(session: dict) -> dict:
    try:
        from migration.digital_twin_pilot import headline_read
        return headline_read(session) or {}
    except Exception as e:
        print(f"[twin-report] headline skipped: {e}")
        return {}


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


# ------------------------------------------------------- the questions
# The moderator's questions to the room (ASK THE ROOM), on the clock during
# the cut and after it wrapped alike. A record from the room engine
# (digital_twin_room.ask_room) carries options with the published share,
# the people and the seat tally, a stage, the framing, the read, the
# voices, what the room knew (facts_used) and what people were saying
# (talk); a record from the deterministic engine (digital_twin_panel.ask)
# carries a distribution, a read and voices and no stage.
PENDING_STAGES = ('researching', 'the room is thinking', 'moderator is reading', 'picking it back up')
STILL_ANSWERING = 'still answering when this report was made'
VOICES_ON_PAGE = 3
KNEW_ON_PAGE = 4
TALK_ON_PAGE = 3


def _short(text: str, n: int) -> str:
    """Cut at a word boundary with an ellipsis when the text runs past n."""
    t = ' '.join(str(text or '').split())
    if len(t) <= n:
        return t
    return t[:n].rsplit(' ', 1)[0].rstrip(',;:') + '...'


def _q_stage(rec: dict) -> str:
    """'done' (no stage means the answer landed inline), 'pending' or
    'failed'."""
    st = str((rec or {}).get('stage') or '').strip().lower()
    if not st or st == 'done':
        return 'done'
    if st == 'failed':
        return 'failed'
    return 'pending'     # the in-flight stages (PENDING_STAGES) and any stage the engine adds later


def _q_rows(rec: dict) -> list:
    """The split: [{label, pct, people, tally}] from the room engine's
    options when they carry shares, else from the distribution."""
    opts = (rec or {}).get('options') or []
    rows = opts if (opts and isinstance(opts[0], dict) and 'pct' in opts[0]) else ((rec or {}).get('distribution') or [])
    out = []
    for r in rows:
        if not isinstance(r, dict) or not str(r.get('label') or '').strip():
            continue
        try:
            pct = float(r.get('pct') or 0)
        except (TypeError, ValueError):
            pct = 0.0
        try:
            people = int(float(r.get('people') or 0))
        except (TypeError, ValueError):
            people = 0
        out.append({'label': ' '.join(str(r['label']).split()), 'pct': pct, 'people': people, 'tally': str(r.get('tally') or '').strip()})
    return out


def _q_lead(rows: list) -> Optional[dict]:
    return max(rows, key=lambda r: r['pct']) if rows else None


def _q_clock(rec: dict) -> str:
    """Where the playhead stood when the question was asked, from the
    stimulus the question carried, else the stamp the route put on it."""
    win = ((rec or {}).get('stimulus') or {}).get('window') if isinstance((rec or {}).get('stimulus'), dict) else None
    if isinstance(win, dict) and win.get('clock'):
        return str(win['clock'])
    if rec.get('playhead_clock'):
        return str(rec['playhead_clock'])
    if rec.get('playhead_s') is not None:
        return _clock(rec.get('playhead_s'))
    return ''


def _q_after_wrap(rec: dict, session: dict) -> bool:
    """True when the question came after the cut had played out: the
    playhead sat at the runtime, or the ask came after the stop."""
    media = (session or {}).get('media') or {}
    try:
        rt = float(media.get('runtime_s') or 0)
    except (TypeError, ValueError):
        rt = 0.0
    win = ((rec or {}).get('stimulus') or {}).get('window') if isinstance((rec or {}).get('stimulus'), dict) else None
    ph = (win or {}).get('playhead_s') if isinstance(win, dict) else None
    if ph is None:
        ph = rec.get('playhead_s')
    try:
        if rt > 0 and ph is not None and float(ph) >= rt - 0.5:
            return True
    except (TypeError, ValueError):
        pass
    stopped, asked = str((session or {}).get('stopped_at') or ''), str((rec or {}).get('asked_at') or '')
    return bool(stopped and asked and asked >= stopped)


def _q_when(rec: dict, session: dict) -> str:
    """'At 43:13, right after the CBS logo' from the stimulus frame;
    'After the wrap, at 43:13, ...' once the cut had played out; the bare
    clock when the stimulus carries no frame."""
    win = ((rec or {}).get('stimulus') or {}).get('window') if isinstance((rec or {}).get('stimulus'), dict) else None
    frame = ' '.join(str((win or {}).get('frame') or '').split()).rstrip('.') if isinstance(win, dict) else ''
    if len(frame) > 160:
        frame = frame[:160].rsplit(' ', 1)[0].rstrip(',;:')
    clock = _q_clock(rec)
    when = frame or (f"At {clock}" if clock else '')
    if _q_after_wrap(rec, session) and when:
        # the frame runs on after the lead-in; an acronym or a name that opens with two capitals keeps its case ('CBS logo', 'LL Cool J')
        from migration.digital_twin_pilot import lower_first
        when = re.sub(r'^At\b', 'After the wrap, at', when) if when.startswith('At ') else f"After the wrap, {lower_first(when)}"
    return when or 'Asked in the room'


def _sorted_questions(session: dict) -> list:
    qs = [q for q in ((session or {}).get('questions') or []) if isinstance(q, dict) and str(q.get('q') or '').strip()]
    return sorted(qs, key=lambda q: str(q.get('asked_at') or q.get('started_at') or ''))


def _question_page(rec: dict, session: dict, dark: bool, n: Optional[int] = None, kn: Optional[dict] = None) -> dict:
    """One question's page: the eyebrow 'Question N', the header the
    question as the moderator asked it (Jenna 2026-11-05: never a
    sentence drawn from the lead answer; a name the record knows in its
    own casing), the sub when it was asked and the lead answer with its
    share and people."""
    rows = _q_rows(rec)
    kn = kn or {}
    seats_total = rec.get('seats_total') or len(((session or {}).get('group') or {}).get('roster') or [])
    split = []
    for r in rows[:8]:
        note = f"{human_people(r['people'])} people" if r['people'] else ''
        if r['tally']:
            note = (note + ', ' if note else '') + f"{r['tally']} in the room"
        split.append((_clean(r['label']), _pct(r['pct']), _clean(note)))
    voices = []
    for v in (rec.get('voices') or [])[:VOICES_ON_PAGE]:
        if not isinstance(v, dict) or not str(v.get('line') or '').strip():
            continue
        who = ', '.join(str(x) for x in (v.get('name'), v.get('age'), v.get('city')) if x not in (None, ''))
        voices.append(_clean(f"{who}: {v['line']}" if who else str(v['line'])))
    lead_line = question_lead_line(rows, people_fmt=human_people)
    return {'kind': 'question', 'eyebrow': f"Question {n}" if n else 'Question', 'header': _clean(question_header(rec.get('q'), kn.get('names') or (), kn.get('labels') or ())),
            'sub': _clean(f"{_q_when(rec, session)}." + (f" {lead_line}" if lead_line else '')),
            'framing': _clean(str(rec.get('framing') or '')), 'head': ('Answer', 'Share', 'People and seats'), 'rows': split,
            'read': _clean(str(rec.get('read') or '')), 'voices': voices,
            'knew': [_clean(str(f)) for f in (rec.get('facts_used') or [])[:KNEW_ON_PAGE] if str(f or '').strip()],
            'talk': [_clean(str(t)) for t in (rec.get('talk') or [])[:TALK_ON_PAGE] if str(t or '').strip()],
            'seats_total': seats_total, 'dark': dark}


def _question_pages(session: dict, kn: Optional[dict] = None) -> list:
    """The section: an opener listing every question with its lead
    answer (when there is more than one, or one is still being answered),
    then one page per answered question, in the order they were asked.
    ``kn`` (``known_names``) puts the names the record knows in their own
    casing wherever a question prints."""
    qs = _sorted_questions(session)
    if not qs:
        return []
    kn = kn or {}
    cased = lambda q: proper_case(' '.join(str(q.get('q') or '').split()), kn.get('names') or (), kn.get('labels') or ())   # noqa: E731
    done = [q for q in qs if _q_stage(q) == 'done']
    pending = [q for q in qs if _q_stage(q) == 'pending']
    failed = [q for q in qs if _q_stage(q) == 'failed']
    notes = []
    if pending:
        count = 'one question' if len(pending) == 1 else f"{len(pending)} questions"
        notes.append(f"The room was {STILL_ANSWERING} on {count}: " + '; '.join(f"\"{_short(cased(q), 80)}\"" for q in pending[:3]) + '.')
    if failed:
        notes.append(f"The room did not finish {'one question' if len(failed) == 1 else str(len(failed)) + ' questions'}: "
                     + '; '.join(f"\"{_short(cased(q), 80)}\"" for q in failed[:3]) + '. Ask it again and it runs fresh.')
    pages = []
    if len(done) >= 2 or notes:
        after = [bool(_q_after_wrap(q, session)) for q in done]
        n = len(done)
        noun = 'question' if n == 1 else 'questions'
        if n == 0:
            header = 'The questions put to the room.'
        elif all(after):
            header = f"The room answered {n} {noun} after the cut wrapped."
        elif not any(after):
            header = f"The room answered {n} {noun} on the clock."
        else:
            header = f"The room answered {n} {noun}, on the clock and after the wrap."
        rows = []
        for q, aw in zip(done, after):
            lead = _q_lead(_q_rows(q))
            clock = _q_clock(q)
            rows.append(((f"{clock}  " if clock else '') + _clean(_short(cased(q), 64)), _pct(lead['pct']) if lead else '',
                         _clean((lead['label'][:46] if lead else '') + (' (after the wrap)' if aw else ''))))
        pages.append({'kind': 'table', 'header': _words(header), 'sub': 'Each question has its own page: the split, the read, the voices and what the room knew.' if done else '',
                      'head': ('Clock and question', 'Lead share', 'Lead answer'), 'rows': rows[:12], 'body': [_clean(x) for x in notes], 'dark': True})
    opener = bool(pages)     # the opener is dark; the question pages alternate from the other ground
    number = {id(q): k + 1 for k, q in enumerate(qs)}     # 'Question N' counts every question as asked, so the numbers match the room
    for i, q in enumerate(done):
        pages.append(_question_page(q, session, dark=(i % 2 == 1) if opener else (i % 2 == 0), n=number.get(id(q)), kn=kn))
    return pages


def _tldr_question_line(session: dict) -> str:
    """One line for the TLDR: how many questions the room answered and
    the lead answer on the last one, in words."""
    done = [q for q in _sorted_questions(session) if _q_stage(q) == 'done']
    if not done:
        return ''
    n = len(done)
    lead = _q_lead(_q_rows(done[-1]))
    noun = 'question' if n == 1 else 'questions'
    if lead is None:
        return f"The room answered {n} {noun}; the answers follow, one page each."
    return f"The room answered {n} {noun}; on the last, {_pct(lead['pct'])} went with \"{lead['label']}\"."


# ---------------------------------------------------------------- pages
def _tldr(session: dict, payload: dict) -> list:
    """The TLDR opens on the strongest honest finding (headline_read:
    intent against a soft Excellent when every money measure sits under
    its norm; the norm beaten when the card clears it), with the seat
    tally beside every share."""
    head = _head(session)
    sc = session.get('scorecard') or {}
    d = session.get('diagnostics') or {}
    mr = session.get('mode_read') or {}
    out = []
    hl = _headline(session)
    if hl.get('lines'):
        out.extend(hl['lines'][:3])
    elif head.get('total_positive_pct') is not None:
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
    out = [x for x in out if x]
    ql = _tldr_question_line(session)
    if ql:
        out = out[:4] + [ql]     # the questions line always makes the page; the page holds five
    return [_clean(x) for x in out if x][:5]


def _conclusions(session: dict) -> list:
    head = _head(session)
    sc = session.get('scorecard') or {}
    d = session.get('diagnostics') or {}
    out = []
    norms = (sc.get('norm_columns') or {})
    t = _tallies(sc)
    tp = head.get('total_positive_pct')
    tpc = norms.get('total_positive') or {}
    tpn = tpc.get('genre_norm') if tpc.get('genre_own') else tpc.get('norm')
    if tp is not None and tpn is not None:
        gap = float(tp) - float(tpn)
        who = _norm_name(sc, 'total_positive')
        seats = f", {t['total_positive']} seats" if t.get('total_positive') else ''
        out.append(f"The cut plays {'above' if gap >= 3 else 'below'} {who} on total positive ({_pct(tp)}{seats}, against {float(tpn):.0f}%)." if abs(gap) >= 3
                   else f"The cut plays at {who} on total positive ({_pct(tp)}{seats}).")
        hl = _headline(session)
        if hl.get('lead') == 'intent' and head.get('finish_pct') is not None:
            out.append(f"Intent is the strength: {_pct(head.get('finish_pct'))} would finish" + (f" ({t['finish']} seats)" if t.get('finish') else '')
                       + (f" and {_pct(head.get('tune_in_pct'))} would watch a second episode" + (f" ({t['tune_in']} seats)" if head.get('tune_in_pct') is not None and t.get('tune_in') else '') if head.get('tune_in_pct') is not None else '') + '; the next episode is the thing to sell.')
    pace = d.get('pace') or {}
    slow = sorted([a for a in pace.get('acts') or [] if (a.get('too_slow_pct') or 0) >= 25], key=lambda a: -(a.get('too_slow_pct') or 0))
    if slow:
        out.append(f"Trim the {slow[0]['label'].lower()} ({slow[0]['from_clock']} to {slow[0]['to_clock']}); {_pct(slow[0]['too_slow_pct'])} found it slow.")
    if (pace.get('confusion_pct') or 0) >= 15 and pace.get('confusions'):
        # the seat's own words ride as a quote after a colon, never glued into the sentence
        line = ' '.join(str(pace['confusions'][0].get('line') or '').split()).rstrip('.')
        out.append(f"Clear up what lost {_pct(pace['confusion_pct'])} of the room: \"{line}.\"" if line else f"Clear up what lost {_pct(pace['confusion_pct'])} of the room.")
    chars = (d.get('characters') or {}).get('characters') or []
    # the lead is the part the cut is built around (the role on the row); the card itself ranks the cast by the room's rating
    lead = next((c for c in chars if c.get('role') == 'lead'), chars[0] if chars else None)
    if lead and lead.get('vs_norm_pp') is not None:
        si = str(((sc.get('norm_columns') or {}).get('lead_character') or {}).get('stand_in') or '')
        lead_norm = f"the {si + ' ' if si else ''}lead norm of {float(lead['norm']):.0f}%"   # 'the lead norm of 51%' or 'the feature screening lead norm of 50%'
        out.append(f"Put {lead['name']} on the key art; the lead rates {_pct(lead['excellent_pct'])} excellent against {lead_norm}." if lead['vs_norm_pp'] >= 5
                   else f"The room is not sold on {lead['name']} yet ({_pct(lead['excellent_pct'])} excellent against {lead_norm}); give the lead a clearer want early." if lead['vs_norm_pp'] <= -8
                   else f"{lead['name']} holds the lead at the norm ({_pct(lead['excellent_pct'])} excellent).")
    # the character the room said it would come back for (its own answer, when more than half named one): give that
    # character more of the next episode (Jenna 2026-11-04)
    draw = next((c for c in chars if c.get('draw_seats') and c is not lead), None)
    if draw:
        seats = len(((session.get('group') or {}).get('roster')) or []) or draw.get('draw_seats')
        out.append(f"Give {draw['name']} more of the next episode, including its close: {draw['draw_seats']} of {seats} would come back for more ({_pct(draw['excellent_pct'])} excellent).")
    scenes = d.get('scenes') or {}
    if (scenes.get('liked_least') or []):
        s = scenes['liked_least'][0]
        out.append(f"The scene liked least sits at {s['clock']} ({s['mentions']} name it); cut or rework it before the next screening.")
    comps = (d.get('components') or {}).get('rows') or []
    weak = sorted([c for c in comps if c.get('vs_norm_pp') is not None], key=lambda c: c['vs_norm_pp'])
    if weak and weak[0]['vs_norm_pp'] <= -5:
        under = [c for c in weak if c['vs_norm_pp'] <= -5][:2]
        who = _norm_name(sc, f"component_{under[0].get('key')}")
        parts = ' and '.join(f"{c['label']} ({_pct(c['excellent_pct'])} against {float(c['norm']):.0f}%)" for c in under)
        out.append(f"{parts} {'are the components' if len(under) > 1 else 'is the component'} under {who}; that is where the gap sits.")
    mk = session.get('marketing_read') or {}
    if mk.get('trailer_moments'):
        out.append('For the trailer, start from ' + ', '.join(m['clock'] for m in mk['trailer_moments'][:3]) + '.')
    return [_clean(x) for x in out if x][:6]


TALENT_PER_PAGE = 3


def _talent_pages(session: dict) -> list:
    """What the room said about each person the setup named (Jenna
    2026-11-04): the sentences and one seat's line, three people to a
    page, right after the characters table. [] when the setup named
    nobody or the read is not on the test yet."""
    try:
        from migration.digital_twin_talent_read import report_lines
        people = report_lines(session)
    except Exception as e:
        print(f"[twin-report] talent read skipped: {e}")
        people = []
    if not people:
        return []
    pages = []
    for i in range(0, len(people), TALENT_PER_PAGE):
        chunk = people[i:i + TALENT_PER_PAGE]
        names = ', '.join(p['name'] for p in chunk)
        pages.append({'kind': 'talent', 'header': 'What the room said about the people you named.' if i == 0 else 'What the room said, continued.',
                      'sub': _clean(f"{names}: in the room's own words, with one line from a seat for each."),
                      'people': [{'name': _clean(p['name']), 'role': _clean(p.get('role') or ''), 'said': _clean(p.get('said') or ''), 'quote': _clean(p.get('quote') or ''), 'who': _clean(p.get('who') or '')} for p in chunk],
                      'dark': True})
    return pages


def _attach_room_line(rec: dict, cell: bool = False) -> str:
    """What the room gave the name, in a few words for the table cell.
    ``cell`` keeps it to the two shares that fit the column (the follow
    share and excellent); the draw clause rides in the full sentence
    under the table, never clipped mid-phrase."""
    o = rec.get('outputs') or {}
    v = o.get('room') or {}
    room = rec.get('room') if isinstance(rec.get('room'), dict) else None
    if room is None:
        return 'before the room watched'
    if not room.get('seen') or not v:
        return 'not in this cut'
    parts = v.get('parts') or {}
    bits = []
    if parts.get('follow') is not None:
        bits.append(f"{_pct(parts['follow'])} would follow")
    if parts.get('excellent') is not None:
        bits.append(f"{_pct(parts['excellent'])} excellent")
    if v.get('is_draw') and not cell:
        bits.append(f"{room.get('draw_seats')} of {room.get('seats')} the reason to come back")
    elif v.get('is_draw') and not bits:
        bits.append('the draw')
    return ', '.join(bits) or f"{_pct(v.get('verdict_pct'))} against an even room"


def _attach_pages(session: dict) -> list:
    """What each name attached to the test adds to the first month: the
    name's pull with this audience and the room's verdict in one lift,
    the billing shown as a fact (Jenna 2026-11-04). [] when no name was
    attached."""
    recs = [r for r in ((session or {}).get('talent_attach') or []) if isinstance(r, dict) and isinstance(r.get('outputs'), dict) and r.get('talent')]
    if not recs:
        return []
    recs = sorted(recs, key=lambda r: -float((r['outputs'].get('reach_lift_pct') or 0)))
    rows = [(_clean(f"{r['talent']} (billed {r.get('role') or 'lead'})"), f"+{_pct(r['outputs'].get('reach_lift_pct'))}", _clean(f"{_people_short(r['outputs'].get('expected'))} more people; {_attach_room_line(r, cell=True)}")) for r in recs[:8]]
    top = recs[0]
    header = _words(_clean(f"{top['talent']} adds the most to the first month, {_pct(top['outputs'].get('reach_lift_pct'))}.")) if len(recs) > 1 else _words(_clean(f"{top['talent']} adds {_pct(top['outputs'].get('reach_lift_pct'))} to the first month."))
    return [{'kind': 'table', 'header': header, 'sub': "Each name's pull with this audience and what the room made of them, in one lift. Billing is shown, never weighed.",
             'head': ('Name', 'First-month lift', 'People, and the room'), 'rows': rows, 'body': [_clean(r.get('read') or '') for r in recs[:3]], 'dark': True}]


def report_pages(payload: dict, session: dict, portrait_fn=None, with_chart: bool = True) -> list:
    """The one page list every export of a test is drawn from: the deck,
    the PDF and the CSV's report block (2026-11-04). ``portrait_fn(hash)
    -> JPEG bytes`` puts the faces on the room page; ``with_chart=False``
    skips drawing the dial image (the CSV carries the dial as numbers)."""
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
    # the people behind the room: the segments' people (the battery's whole-room total), never a stored stand-in; no count when no source carries one
    try:
        from migration.digital_twin_pilot import session_people
        behind = session_people(session, payload)
        room_total = session_people(session)     # the people the seated room stands for, never the twin's size (the seat pages' denominator)
    except Exception:
        behind = room_total = None
    seated = g.get('in_room') or len(g.get('roster') or [])
    # what the shares are of, said once on the TLDR and once on the scorecard page (Liz Huszarik 2026-11-05: 16 seats cannot
    # produce 49.4%, so say which denominator the shares use and keep the seat tally beside every share)
    denom = _denominator(session, sc, seated, behind) if head.get('total_positive_pct') is not None else ''
    pages = [{'kind': 'cover', 'header': f"{_clean(title)} x {name}", 'sub': _clean(f"What the {name} audience made of {session.get('cut_label') or 'the cut'}, in the room, on the clock."), 'dark': True}]
    pages.append({'kind': 'bullets', 'header': 'What we found, and what to do.', 'sub': _clean(denom), 'body': _tldr(session, payload) or ['The room watched the cut; the scorecard follows.'], 'dark': True})
    t = _tallies(sc)
    seat_note = lambda key, label: f"{label}, {t[key]}" if t.get(key) else label   # noqa: E731   the seats themselves beside the share
    tiles = [(_pct(head.get('total_positive_pct')) or f"{float(sc.get('overall_like') or 0):.1f}", seat_note('total_positive', 'Total positive') if head.get('total_positive_pct') is not None else 'Dial average'),
             (_pct(head.get('definitely_recommend_pct')) or '', seat_note('definitely_recommend', 'Definitely recommend')), (_pct(head.get('excellent_pct')) or '', seat_note('excellent', 'Excellent'))]
    if head.get('finish_pct') is not None:
        tiles.append((_pct(head.get('finish_pct')), seat_note('finish', 'Would finish')))
    # the norm the card is read against, named from the one helper the card and the CSV use (Jenna 2026-11-04: the user sets
    # it; Liz Huszarik 2026-11-05: never 'Pilot, procedural' next to an all-pilots footnote); older cards compute it from the columns
    compared = _compared_to(sc, session)
    hl = _headline(session)
    pages.append({'kind': 'tiles', 'header': _words(hl.get('header') or 'The scorecard.') if head.get('total_positive_pct') is not None else 'The scorecard.',
                  'sub': _clean(' '.join(x for x in (denom, compared) if x)) if denom else _clean((f"The room seated {seated} people, with {human_people(behind)} behind them. " if behind else f"The room seated {seated} people. ") + (compared or '')),
                  'tiles': [tl for tl in tiles if tl[0]][:4], 'body': [_clean(x) for x in (sc.get('norm_lines') or [])[:4]], 'dark': False})
    ch = _choice(sc, session)
    notes = [str(x) for x in (ch.get('notes') or ([ch['note']] if ch.get('note') else [])) if x]
    pages.append({'kind': 'table', 'header': 'Every question on the card, next to its norm.', 'sub': compared, 'head': ('Measure', 'Share, and seats', 'Norm'),
                  'rows': [(_clean(a), b, _clean(c)) for a, b, c in _scorecard_rows(sc)[:12]], 'body': [_clean(x) for x in notes][:3], 'dark': True})
    png = dial_chart_png(session) if with_chart else None
    if png:
        fav, dead = sc.get('favorite_moment') or {}, sc.get('dead_spot') or {}
        pages.append({'kind': 'image', 'header': _words(f"The room peaked at {fav.get('clock') or '-'} and dipped at {dead.get('clock') or '-'}."), 'sub': 'The dial second by second, the peaks and valleys marked, every reaction on the rail under it.',
                      'image': png, 'body': [_clean(f"Favorite moment, {fav.get('clock')}: {fav.get('line')}"), _clean(f"Dead spot, {dead.get('clock')}: {dead.get('line')}")], 'dark': True})
    pages.extend(_moments_pages(session, clean=_clean))     # the strongest reactions, seat by seat (the room export's moments page)
    qrows = _quadrant_rows(sc)
    if qrows:
        pages.append({'kind': 'table', 'header': 'The four quadrants, side by side.', 'sub': "Total positive, with definitely recommend and excellent beside it. Each group's shares are of the people its seats stand for; the seat count is in the label.",
                      'head': ('Group', 'Total positive', 'And'), 'rows': qrows[:10], 'dark': False})
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
        _cm = lambda c: {'lead': 'lead_character', 'second lead': 'secondary_character'}.get(c.get('role'), 'supporting_character')   # noqa: E731
        rows = [(f"{c['name']} ({c['role']})", _pct(c.get('excellent_pct')), _clean(f"{_norm_cell(sc, _cm(c), c['norm'])}, {c['screen_clock']} on screen, {c['time_vs_like']}") if c.get('norm') is not None else _clean(f"{c['screen_clock']} on screen, {c['time_vs_like']}")) for c in chars[:8]]
        fol = (d.get('characters') or {}).get('follow') or []
        # the follow shares are per person (a seat can follow more than one), so the sub-line says so and never reads as a split
        from migration.digital_twin_diagnostics import FOLLOW_NOTE, follow_line
        pages.append({'kind': 'table', 'header': _words(_clean((d['characters'].get('read') or 'The characters, rated.').split('. ')[0] + '.')),
                      'sub': _clean(f"{follow_line(fol)} {FOLLOW_NOTE}") if fol else '',
                      'head': ('Character', 'Excellent', 'Norm and screen time'), 'rows': rows, 'dark': False})
    # what the room said about the people the setup named (Jenna 2026-11-04), three to a page, right after their metrics
    pages.extend(_talent_pages(session))
    # what each name attached to the test adds to the first month, the room's verdict inside the lift (Jenna 2026-11-04)
    pages.extend(_attach_pages(session))
    comps = (d.get('components') or {}).get('rows') or []
    if comps:
        # the norm each component is read against is named for what it is (the feature screening card while the column is a stand-in)
        comp_norm_word = _norm_name(sc, 'component_story')
        pages.append({'kind': 'table', 'header': _words(_clean((d['components'].get('read') or 'The components, next to their norms.').split('. ')[0] + '.')),
                      'sub': _clean(f"Top box (excellent) for each component, with {comp_norm_word}."),
                      'head': ('Component', 'Excellent', 'Norm'), 'rows': [(c['label'], _pct(c.get('excellent_pct')), (_norm_cell(sc, f"component_{c.get('key')}", c['norm']) + (f" ({'+' if c['vs_norm_pp'] >= 0 else ''}{c['vs_norm_pp']})" if c.get('vs_norm_pp') is not None else '')) if c.get('norm') is not None else 'no norm yet') for c in comps], 'dark': True})
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
    # the questions the moderator put to the room, during the cut and after it wrapped (Jenna 2026-11-04); a name the
    # record knows (the cast, the title, the characters, the platforms, the cohorts) prints in its own casing however typed
    pages.extend(_question_pages(session, kn=known_names(session, payload)))
    # who was in the room, seat by seat (Jenna 2026-11-05): the face, who they are, the cohort they stood in for, the people
    # each one stands for (of the people the room stands for, the same figure as the scorecard page), and the bio
    pages.extend(_seat_pages(session, portrait_fn=portrait_fn, clean=_clean, people_total=room_total, start_dark=not bool(pages[-1].get('dark'))))
    rec = session.get('recruit') or {}
    if rec:
        w = rec.get('walkouts') or {}
        # seated once; the invited count and the invite ratio only when the recruit recorded them (never a stand-in 1 : 1)
        tiles = [(str(rec.get('seated') or g.get('in_room') or ''), 'Seated')]
        if rec.get('invited'):
            tiles.append((f"{int(rec['invited']):,}", 'Invited'))
        tiles.append((str(w.get('count') if w.get('count') is not None else 0), 'Walked out'))
        if rec.get('invite_ratio_n'):
            tiles.append((f"{rec['invite_ratio_n']} : 1", 'Invite ratio'))
        pages.append({'kind': 'tiles', 'header': _words(_clean(f"Recruited to {rec.get('concept') or 'the twin'}.")), 'sub': _clean(rec.get('stats_line') or ''), 'tiles': tiles[:4],
                      'body': [_clean(f"{q['quota']}: {q['target_pct']}% asked for, {q['seated_pct']}% seated") for q in (rec.get('quotas_met') or [])[:3]] + ([_clean(w.get('read'))] if w.get('read') else []), 'dark': True})
    pages.extend(_room_pages(session, portrait_fn=portrait_fn, clean=_clean))     # who was seated: the faces, the segments, the roster line
    cr = session.get('critics') or {}
    if cr.get('critic_score') is not None:
        pages.append({'kind': 'tiles', 'header': _words(f"Critic score forecast {cr['critic_score']:.0f}."), 'sub': _clean(cr.get('awards_read') or ''), 'tiles': [(f"{cr['critic_score']:.0f}", 'Critic score'), (f"{cr['range'][0]:.0f} to {cr['range'][1]:.0f}", 'Likely range')], 'body': [], 'dark': False})
    fc = session.get('forecast') if isinstance(session.get('forecast'), dict) else {}
    if fc.get('episode'):
        # the performance forecast (Jenna 2026-11-03): week one if released, the season, the subscriptions it would move
        ep, se, su = fc['episode'], fc.get('series') or {}, fc.get('subscriptions') or {}
        plat = su.get('platform') or 'the service'
        # the tile label fits the tile ('Would subscribe for it'); the base (people without the service) is named in the line under
        tiles = [(_people_short(ep.get('week_one_audience')), 'Week-one audience if released'),
                 (f"{se.get('season_completion_pct')}%", 'Would finish the season'),
                 (f"{se.get('second_screen_pct')}%", 'On a second screen'),
                 (f"{su.get('subscribe_pct_of_nonsubscribers')}%", 'Would subscribe for it')]
        body = [_clean(f"Range {_people_short(ep.get('low'))} to {_people_short(ep.get('high'))} in week one; season average {_people_short(se.get('season_average_audience'))}, finale {_people_short(se.get('finale_audience'))}."),
                _clean(f"{su.get('reactivate_pct_of_dormant')}% of dormant {plat} accounts would come back for it, about {int(su.get('reactivate_people') or 0):,} people; "
                       f"{su.get('subscribe_pct_of_nonsubscribers')}% of people without {plat}, about {int(su.get('subscribe_people') or 0):,}, would subscribe for it.")]
        # the recorded actual and the calibration block are audit, never a page line (Jenna 2026-11-05): the forecast reads as
        # our own, with no 'as aired' comparison and no note on what it was set against
        for n in [x for x in (_clean(str(n)) for n in (fc.get('notes') or [])[:2]) if x]:
            body.append(n)
        pages.append({'kind': 'tiles', 'header': _words(_clean(f"If released, {_people_short(ep.get('week_one_audience'))} people watch in the first week.")), 'sub': _clean(ep.get('definition') or ''), 'tiles': tiles, 'body': body, 'dark': False})
    pages.append({'kind': 'bullets', 'header': 'Key conclusions and what to do next.', 'sub': '', 'body': _conclusions(session) or ['Keep the favorite moment. Look hard at the dead spot.'], 'dark': False})
    for pg in pages:
        pg['header'] = _clean(pg.get('header') or '')
        pg['sub'] = _clean(pg.get('sub') or '')
        if pg.get('body'):
            pg['body'] = [_clean(b) for b in pg['body'] if b]
        for key in ('framing', 'read'):
            if pg.get(key):
                pg[key] = _clean(pg[key])
        for key in ('voices', 'knew', 'talk'):
            if pg.get(key):
                pg[key] = [_clean(v) for v in pg[key] if v]
    return pages


def build_report_deck(payload: dict, session: dict, portrait_fn=None) -> bytes:
    return build_deck(payload, pages=report_pages(payload, session, portrait_fn=portrait_fn))


PDF_FLOOR = 0.78 * 72     # nothing is drawn below this line; the footer sits under it


def _ellipsize(line: str, font: str, size: float, width: float) -> str:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    s = str(line or '')
    while s and stringWidth(s + '...', font, size) > width:
        s = s[:-1].rstrip()
    return (s + '...') if s else '...'


def _fit_para(d: _Doc, text: str, x: float, y: float, width: float, size: float, color: str, font: str = '', floor: float = PDF_FLOOR, max_lines: Optional[int] = None) -> float:
    """A paragraph that stops at the floor (and at max_lines), the last
    line it draws ending in an ellipsis when more remained."""
    from reportlab.lib import colors
    font = font or d.f['regular']
    lines = d._wrap(text, font, size, width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _ellipsize(lines[-1], font, size, width)
    lead = size * 1.42
    d.c.setFont(font, size)
    d.c.setFillColor(colors.HexColor(color))
    for i, line in enumerate(lines):
        if y < floor:
            break
        last = (y - lead) < floor and i < len(lines) - 1
        d.c.drawString(x, y, _ellipsize(line, font, size, width) if last else line)
        y -= lead
        if last:
            break
    return y


def _eyebrow(d: _Doc, text: str, x: float, y: float) -> float:
    from reportlab.lib import colors
    d.c.setFont(d.f['medium'], 7.5)
    d.c.setFillColor(colors.HexColor(MUTED_DARK if d.dark else MUTED_LIGHT))
    d.c.drawString(x, y, str(text).upper())
    return y - 12


def _draw_question_pdf(d: _Doc, pg: dict, y: float) -> float:
    """One question's page: the moderator's framing, the split across
    the page with the seat tally, then Prometheus's read and the voices
    on the left and what the room knew and what people were saying on
    the right (the left runs the full width when the right is empty).
    Body copy at 10pt, notes at 9pt; every block stops at the floor."""
    from reportlab.lib import colors
    ink = OFFWHITE if d.dark else GRAPHITE
    body = BODY_DARK if d.dark else BODY_LIGHT
    x, width = d.m, d.right - d.m
    if pg.get('framing'):
        y = _fit_para(d, pg['framing'], x, y, width, 10, body, max_lines=3) - 6
    rows = (pg.get('rows') or [])[:8]
    if rows:
        y = d.table(rows, x, y, width, head=pg.get('head') or ('Answer', 'Share', 'People and seats'))
    y -= 8
    knew, talk = (pg.get('knew') or [])[:KNEW_ON_PAGE], (pg.get('talk') or [])[:TALK_ON_PAGE]
    lw = width * 0.58 if (knew or talk) else width
    rx = x + lw + 22
    rw = d.right - rx
    yl = yr = y
    if pg.get('read') and yl > PDF_FLOOR + 24:
        yl = _eyebrow(d, "Prometheus's read", x, yl)
        yl = _fit_para(d, pg['read'], x, yl, lw, 10, ink, d.f['medium']) - 6
    voices = (pg.get('voices') or [])[:VOICES_ON_PAGE]
    if voices and yl > PDF_FLOOR + 24:
        yl = _eyebrow(d, 'In their words', x, yl)
        for v in voices:
            room = int((yl - PDF_FLOOR) // (10 * 1.42))     # the lines left above the floor
            if room < 1:
                break
            # a quote holds three lines: a longer one ends at the last complete sentence that fits, never mid-word
            v = fit_quote(v, lambda t: len(d._wrap(t, d.f['regular'], 10, lw)), min(3, room))
            if not v:
                break
            yl = _fit_para(d, v, x, yl, lw, 10, body, max_lines=3) - 5
    for label, items in (('What the room knew', knew), ('What people were saying', talk)):
        if not items or yr < PDF_FLOOR + 30:
            continue
        yr = _eyebrow(d, label, rx, yr)
        for it in items:
            if yr < PDF_FLOOR + 10:
                break
            d.c.setFillColor(colors.HexColor(GREEN if d.dark else OLIVE))
            d.c.circle(rx + 2.5, yr + 3.0, 1.6, stroke=0, fill=1)
            yr = _fit_para(d, it, rx + 10, yr, rw - 10, 9, body, max_lines=3) - 3
        yr -= 6
    return min(yl, yr)


def _draw_talent_pdf(d: _Doc, pg: dict, y: float) -> float:
    """The people the setup named, side by side (up to three columns):
    the name and the part as the eyebrow, what the room said, then the
    seat's line with who said it and when. Every block stops at the
    floor."""
    from reportlab.lib import colors
    people = (pg.get('people') or [])[:TALENT_PER_PAGE]
    if not people:
        return y
    ink = OFFWHITE if d.dark else GRAPHITE
    body = BODY_DARK if d.dark else BODY_LIGHT
    n = len(people)
    gutter = 20.0
    width = d.right - d.m
    cw = (width - gutter * (n - 1)) / n
    bottom = y
    for i, p in enumerate(people):
        x = d.m + i * (cw + gutter)
        yy = _eyebrow(d, f"{p.get('name') or ''}" + (f", {p['role']}" if p.get('role') else ''), x, y)
        yy = _fit_para(d, p.get('said') or '', x, yy, cw, 10, ink, d.f['medium']) - 8
        if p.get('quote') and yy > PDF_FLOOR + 30:
            d.c.setFillColor(colors.HexColor(GREEN if d.dark else OLIVE))
            d.c.rect(x, yy - 2, 1.6, 12, stroke=0, fill=1)
            yy = _fit_para(d, f"\"{p['quote']}\"", x + 8, yy, cw - 8, 9.5, body, max_lines=8) - 3
            if p.get('who') and yy > PDF_FLOOR + 10:
                d.c.setFont(d.f['regular'], 8)
                d.c.setFillColor(colors.HexColor(MUTED_DARK if d.dark else MUTED_LIGHT))
                d.c.drawString(x + 8, yy, str(p['who'])[:60])
                yy -= 12
        bottom = min(bottom, yy)
    return bottom


def build_report_pdf(payload: dict, session: dict, portrait_fn=None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.utils import ImageReader
    d = _Doc()
    pages = report_pages(payload, session, portrait_fn=portrait_fn)
    for i, pg in enumerate(pages, 1):
        dark = bool(pg.get('dark'))
        if pg['kind'] == 'cover':
            d.new_page(True, 'Pilot Testing', pg['header'], pg.get('sub') or '')
            continue
        y = d.new_page(dark, pg.get('eyebrow') or 'Pilot Testing', pg['header'], pg.get('sub') or '')     # a question page's eyebrow is 'Question N'
        if pg['kind'] == 'question':
            _draw_question_pdf(d, pg, y)
        elif pg['kind'] == 'talent':
            _draw_talent_pdf(d, pg, y)
        elif pg['kind'] == 'roster':
            _draw_roster_pdf(d, pg, y)
        elif pg['kind'] == 'seats':
            _draw_seats_pdf(d, pg, y)
        elif pg['kind'] == 'bullets':
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
