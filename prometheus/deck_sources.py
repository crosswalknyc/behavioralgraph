"""Where a deck's material comes from when the reader asks for a deck of
something Prometheus already delivered (2026-10-26, Debi).

Debi got the Mark Rober / CrunchLabs Digital Journey as a PDF by email
and asked "Can you build a PPT please?" (a model reply promised one and
nothing was queued), then "refresh it please" (a profile brief), then
uploaded the PDF and asked to "recreate the uploaded deck into a PPT"
(the document lane: "I can't build or hand back a file here"). The deck
job already renders a PowerPoint from a view's digest plus the reads in
the thread; this module finds the material:

- `from_document(...)`: the file attached to the thread (its text rides
  as delivered reads, the deck restates it).
- `from_delivered(...)`: a Digital Journey the caller can see whose
  title the words name ("the Mark Rober deck", "the pdf you sent").

`resolve(text, ctx, history, user, host)` tries the document first, then
a delivered journey, and returns (ctx, history) for the deck core or
None. The material rides in `view_context['material']`; the view deck
digest restates it slide for slide. `is_conversion_ask(text)` is the gate: the reader wants a deck or
PowerPoint of something that already exists.
"""
from __future__ import annotations

import json
import re
import traceback

_FORMAT_RX = re.compile(r"\b(?:ppt|pptx|power\s?point|editable\s+(?:deck|slides|version)|slides|slide\s+deck|keynote|deck)\b", re.I)
_CONVERT_RX = re.compile(
    r"\b(?:recreate|re-create|rebuild|remake|redo|reproduce|convert|turn|make|build|give\s+me|send\s+me|export|save|render|same\s+(?:deck|thing|slides)|"
    r"version\s+of|as\s+an?\s+(?:ppt|pptx|power\s?point|editable)|into\s+an?\s+(?:ppt|pptx|power\s?point|slide)|instead\s+of\s+(?:a\s+)?pdf|not\s+(?:a\s+)?pdf)\b", re.I)
_EXISTING_RX = re.compile(
    r"\b(?:the\s+(?:same\s+)?(?:deck|pdf|file|slides|report|one)\b|(?:you|prometheus)\s+(?:sent|emailed|made|built|delivered)|"
    r"uploaded|attached|this\s+(?:deck|pdf|file|one)|it'?s\s+a\s+pdf|but\s+it\s+is\s+a\s+pdf|as\s+a\s+pdf|exactly\s+the\s+same|same\s+slides|same\s+numbers|refresh(?:ed)?\s+(?:it|the\s+deck))\b", re.I)


def is_conversion_ask(text):
    """The reader wants a deck or PowerPoint of something that already
    exists (a delivered PDF, an attached file), not a new read."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 500:
        return False
    return bool(_FORMAT_RX.search(t) and _CONVERT_RX.search(t) and _EXISTING_RX.search(t))


def _chunks(text, size=3800, limit=6):
    t = ' '.join(str(text or '').split())
    out = []
    while t and len(out) < limit:
        out.append(t[:size])
        t = t[size:]
    return out


def from_document(text, uname, tid, s3, bucket):
    """(ctx, material) from the newest attachment on the thread, or None."""
    try:
        from . import documents as _docs
        recs = _docs.attachments(uname, tid, s3, bucket)
        if not recs:
            return None
        low = str(text or '').lower()
        target = None
        for r in reversed(recs):
            stem = str(r.get('name') or '').rsplit('.', 1)[0].lower()
            if stem and len(stem) >= 4 and stem in low:
                target = r
                break
        target = target or recs[-1]
        body = _docs.document_text(target, s3, bucket)
        if not body or len(body.split()) < 40:
            return None
        name = str(target.get('name') or 'the attached file')
        stem = re.sub(r"[_\-]+", ' ', name.rsplit('.', 1)[0]).strip()
        material = [f"THE ATTACHED FILE, {name}, part {i + 1}:\n{c}" for i, c in enumerate(_chunks(body))]
        ctx = {'primary': None, 'cuts': [], 'extras': [], 'deck_source': 'document',
               'view_context': {'view_id': 'document', 'view_title': stem[:80],
                                'data': {'document': name, 'words': len(body.split())}, 'material': material}}
        return ctx, material
    except Exception:
        traceback.print_exc()
        return None


def _journey_lines(payload):
    """The delivered journey as plain lines: the headline facts, the
    read, who they are, the steps, the fork, the breakdown tables and
    the cast measures. Every number as written in the payload."""
    out = []
    meta = payload.get('meta') or {}
    mode = str(meta.get('story_mode') or '')
    j = payload.get(mode) if mode and isinstance(payload.get(mode), dict) else next((v for k, v in payload.items() if isinstance(v, dict) and 'spine' in v), {})
    def _line(x):
        if isinstance(x, dict):
            lab, val = str(x.get('label') or x.get('title') or '').strip(), str(x.get('value') or x.get('text') or x.get('body') or x.get('read') or '').strip()
            return f"{lab}: {val}" if lab and val else (val or lab)
        return str(x or '').strip()
    facts = [_line(x) for x in (payload.get('facts') or []) if _line(x)]
    if facts:
        out.append('HEADLINE FACTS:\n' + '\n'.join(facts[:8]))
    rd = payload.get('the_read') or j.get('the_read') or {}
    if isinstance(rd, dict):
        for k, lab in (('tldr', 'TLDR'), ('reads', 'THE READ'), ('cards', 'THE READ'), ('recommendations', 'WHAT TO DO'), ('so_what', 'SO WHAT')):
            v = rd.get(k)
            vals = [_line(x) for x in (v if isinstance(v, list) else [v]) if _line(x)]
            if vals:
                out.append(lab + ':\n' + '\n'.join(vals[:8]))
    elif isinstance(rd, (list, str)):
        vals = [_line(x) for x in (rd if isinstance(rd, list) else [rd]) if _line(x)]
        if vals:
            out.append('THE READ:\n' + '\n'.join(vals[:8]))
    who = payload.get('who_they_are') or j.get('who_they_are') or {}
    if isinstance(who, dict):
        if who.get('read'):
            out.append('WHO THEY ARE:\n' + str(who['read']))
        else:
            demo = []
            for k, v in who.items():
                if isinstance(v, list) and v and all(isinstance(r, dict) and r.get('label') is not None for r in v):
                    demo.append(f"{str(k).replace('_', ' ').title()}: " + '; '.join(f"{r.get('label')} {float(r.get('pct') or 0):.1f}%" for r in v[:8] if r.get('pct') not in (None, '')))
            if demo:
                out.append('WHO THEY ARE:\n' + '\n'.join(demo))
    steps = [s for s in (j.get('spine') or []) if isinstance(s, dict) and s.get('label')]
    if steps:
        out.append('THE STEPS (unique US people):\n' + '\n'.join(
            f"{s.get('label')}: {int(s.get('accounts') or 0):,}" + (f" ({float(s.get('kept')):.1f}% of the step before)" if s.get('kept') not in (None, '', 100.0) and i else '')
            for i, s in enumerate(steps)))
    fork = [f for f in (j.get('fork') or []) if isinstance(f, dict) and f.get('label')]
    if fork:
        out.append('AFTER THE FIRST SITTING:\n' + '\n'.join(f"{f.get('label')}: {int(f.get('accounts') or 0):,}" + (f" ({float(f.get('kept')):.1f}%)" if f.get('kept') not in (None, '') else '') for f in fork))
    for d in (j.get('detours') or [])[:8]:
        if not isinstance(d, dict) or not d.get('rows'):
            continue
        rows = [r for r in d['rows'] if isinstance(r, dict) and r.get('label')]
        if rows:
            out.append(f"{str(d.get('title') or 'Breakdown').upper()}:" + (f" {d.get('note')}" if d.get('note') else '') + '\n' + '\n'.join(
                f"{r.get('label')}: {int(r.get('accounts') or 0):,}" + (f" ({float(r.get('pct')):.1f}%)" if r.get('pct') not in (None, '') else '') for r in rows[:10]))
    cd = j.get('cast_drivers') or {}
    if isinstance(cd, dict) and (cd.get('stars') or cd.get('groups')):
        measures = [m for m in (cd.get('measures') or []) if isinstance(m, dict) and m.get('id')]
        lines = []
        for r in list(cd.get('stars') or []) + list(cd.get('groups') or []):
            if not isinstance(r, dict) or not r.get('name'):
                continue
            m = r.get('m') or {}
            cells = []
            for mm in measures[:8]:
                v = m.get(mm['id']) if isinstance(m, dict) else None
                if not isinstance(v, dict):
                    continue
                if mm.get('kind') == 'draw':
                    cells.append(f"{mm.get('label')}: {int(v.get('accounts') or 0):,} ({float(v.get('pct') or 0):.1f}%)")
                elif v.get('pct') not in (None, ''):
                    cells.append(f"{mm.get('label')}: {float(v.get('pct')):.1f}%")
            if cells:
                lines.append(f"{r.get('name')}" + (f" ({r.get('role')})" if r.get('role') else '') + '. ' + '; '.join(cells))
        if lines:
            out.append('THE CAST AND THE OTHER REASONS PEOPLE CAME:' + (f" {cd.get('note')}" if cd.get('note') else '') + '\n' + '\n'.join(lines[:10]))
    return out


def _journey_context(run, payload):
    meta = payload.get('meta') or {}
    name = str(meta.get('project_name') or run.get('project_name') or '').strip()
    data = {'journey': name}
    try:
        from migration import journey_gold as G
        s = G.ship_summary(payload)
        data.update({k: s[k] for k in ('social', 'click', 'page', 'watched', 'conversion') if s.get(k)})
    except Exception:
        pass
    if not data.get('watched'):
        from . import journey_lane as _jl
        sp = _jl._spine(payload)
        if len(sp) >= 2:
            a, z = int(sp[0].get('accounts') or 0), int(sp[-1].get('accounts') or 0)
            if a and z:
                data.update({'start_step': sp[0].get('label'), 'start_count': a, 'last_step': sp[-1].get('label'), 'last_count': z, 'conversion': round(z / a * 100, 2)})
    for k in ('start_date', 'end_date', 'window_label', 'streaming_home', 'network', 'premiere_date', 'conversion_event', 'starting_point'):
        if meta.get(k):
            data[k] = meta[k]
    kp = payload.get('kpis') or {}
    if isinstance(kp, dict):
        data.update({k: v for k, v in list(kp.items())[:12] if isinstance(v, (int, float, str))})
    window = str(data.get('window_label') or (f"{data.get('start_date')} to {data.get('end_date')}" if data.get('start_date') and data.get('end_date') else '')).strip()
    material = [f"THE DELIVERED JOURNEY, {name}" + (f" ({window})" if window else '') + f", part {i + 1}:\n{blk}" for i, blk in enumerate(_journey_lines(payload))]
    ctx = {'primary': None, 'cuts': [], 'extras': [], 'deck_source': 'journey',
           'view_context': {'view_id': 'journeyIQ', 'view_title': name[:80], 'data': data, 'material': material}}
    return ctx, material


def _pick_delivered(text, runs, user, uname, now=None):
    """The journey the words mean: the caller's own first, then any the
    caller can see; a title whose tokens the words carry wins, and with
    no title named ("the pdf you sent me") the caller's newest journey
    from the last two weeks stands in."""
    from . import journey_lane as _jl
    import datetime as _dt
    low = ' ' + re.sub(r"[^a-z0-9]+", ' ', str(text or '').lower()) + ' '
    now = now or _dt.datetime.now(_dt.timezone.utc)
    best, best_score = None, 0.0
    for run in runs:
        if not isinstance(run, dict) or not run.get('key') or not _jl._visible(run, user, uname):
            continue
        toks = [t for t in dict.fromkeys(_jl._tokens(_jl._title_of(run))) if t not in ('build', 'kits', 'site', 'audience', 'journey', 'creator', 'full', 'dtc')]
        present = [t for t in toks if f' {t} ' in low]
        own = str(run.get('created_by') or '') == uname
        if toks and present and (len(present) >= 2 or any(len(t) >= 8 for t in present)):
            score = 1.0 + len(present) / len(toks) + (1.0 if own else 0.0)
        elif own and not present:
            try:
                age = now - _dt.datetime.fromisoformat(str(run.get('created_at') or '').replace('Z', '+00:00'))
            except Exception:
                continue
            if age.days > 14:
                continue
            score = 0.5
        else:
            continue
        if score > best_score or (score == best_score and str(run.get('created_at') or '') > str((best or {}).get('created_at') or '')):
            best, best_score = run, score
    return best


def from_delivered(text, user, uname, s3, bucket):
    """(ctx, material) from a Digital Journey the caller can see whose
    title the words name, or None."""
    try:
        from . import journey_lane as _jl
        from migration import journey_iq as _jiq
        runs = _jiq.list_runs(s3, limit=500)
        run = _pick_delivered(text, runs, user, uname)
        if not run:
            return None
        payload = _jl._load(s3, run['key'], bucket)
        if not isinstance(payload, dict):
            return None
        return _journey_context(run, payload)
    except Exception:
        traceback.print_exc()
        return None


def from_twin(text, ctx, user, uname, s3, bucket):
    """(ctx, material) from a Digital Twin: the one open on the page or
    the one the words name ('a deck of the Gilmore Girls twin'), or None
    (2026-10-26)."""
    try:
        from . import digital_twin_lane as _tl
        return _tl.deck_from_twin(text, ctx, user, uname, s3, bucket)
    except Exception:
        traceback.print_exc()
        return None


def resolve(text, ctx, history, user, host, tid=''):
    """(ctx, history) for the deck core when the ask converts something
    delivered; None otherwise (the deck core proceeds as before)."""
    try:
        s3, bucket = host.s3_client, host.S3_BUCKET
    except Exception:
        return None
    uname = str((user or {}).get('username') or (user or {}).get('email') or '').strip()
    _vc = (ctx or {}).get('view_context') if isinstance(ctx, dict) else None
    if (isinstance(_vc, dict) and str(_vc.get('view_id') or '') == 'digitalTwinsIQ') or re.search(r"\btwins?\b", str(text or ''), re.I):
        found = from_twin(text, ctx, user, uname, s3, bucket)   # the twin is the material, slide for slide
        if found:
            return dict(found[0]), list(history or [])
    if not is_conversion_ask(text):
        return None
    found = from_document(text, uname, tid, s3, bucket) if tid else None
    if not found:
        found = from_delivered(text, user, uname, s3, bucket)
    if not found:
        return None
    new_ctx, _material = found
    return dict(new_ctx), list(history or [])


__all__ = ['is_conversion_ask', 'from_document', 'from_delivered', 'from_twin', 'resolve']
