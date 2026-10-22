"""Where a stated figure came from (2026-10-22, Alexia on The Influencer
Project).

She asked "where does the 12,004,203 potential ticket buyers number come
from? I only see 6.6M that were exposed and only 1% that reached a
ticketing site". The figure was in the previous reply. The lanes read the
turn as a status check ("I only see"), as a brand coverage ask (the
commas split 12,004,203 into the brands 12 and 004) and as a box office
ask. None of them looked at the previous answer.

The rule: a question that quotes a figure Prometheus stated earlier in
the thread is a question about that answer. It is answered from the
record of the answer (the insights ledger entry whose reply carries the
figure: its base, window and subject), and when the open product carries
different numbers the reply says which dataset each figure belongs to.
No model call, no charge, never a status or coverage lane.
"""
from __future__ import annotations

import re

_FIGURE_RX = re.compile(r"\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$?\d{4,}(?:\.\d+)?")
_ABBR_RX = re.compile(r"\b(\d+(?:\.\d+)?)\s*([mkb])\b", re.I)
# the words that make a quoted figure a question about where it came from
_PROVENANCE_RX = re.compile(
    r"\b(?:where\s+(?:does|did|do|is|are)\b.*?\b(?:come|coming|came)\s+from|where\s+(?:did|does|do)\s+(?:you|that|this|it)\s+(?:get|come|pull|take)|"
    r"(?:come|comes|coming|came)\s+from|what\s+is\s+(?:that|this|the)\s+.*?\b(?:based\s+on|from)|how\s+(?:did|do)\s+you\s+(?:get|arrive\s+at|reach|come\s+up\s+with)|"
    r"source\s+of|not\s+(?:in|on)\s+the\s+(?:report|dashboard|data|screen)|i\s+only\s+see|doesn'?t\s+match|does\s+not\s+match|"
    r"why\s+(?:is|does|do)\s+(?:it|that|this|the\s+\w+)\s+(?:say|show|read))\b", re.I)


def _digits(raw):
    return re.sub(r"[^\d.]", "", str(raw or ''))


def figures_in(text):
    """Figures the words state, as digit strings ('12004203'), largest
    first. Abbreviated millions ('6.6M') come back as ('6.6M', 6600000)
    style approximations so a 6,671,723 on screen can be matched loosely."""
    t = str(text or '')
    out = []
    for m in _FIGURE_RX.finditer(t):
        d = _digits(m.group(0))
        if d and float(d or 0) >= 1000:
            out.append(d)
    seen = set()
    uniq = [d for d in out if not (d in seen or seen.add(d))]
    return sorted(uniq, key=lambda d: -float(d))


def abbreviated_in(text):
    """[(raw, value)] for 6.6M / 34K style figures in the words."""
    out = []
    for m in _ABBR_RX.finditer(str(text or '')):
        try:
            v = float(m.group(1)) * {'k': 1e3, 'm': 1e6, 'b': 1e9}[m.group(2).lower()]
        except Exception:
            continue
        out.append((m.group(0), int(v)))
    return out


def quoted_from_thread(text, history, max_turns=12):
    """The figure the question quotes that Prometheus stated earlier:
    {'figure': '12,004,203', 'digits': '12004203', 'turn': i, 'sentence'}
    or None. Only agent turns count; a figure the user typed on their own
    is not a quote."""
    figs = figures_in(text)
    if not figs or not _PROVENANCE_RX.search(str(text or '')):
        return None
    turns = [h for h in (history or []) if isinstance(h, dict)]
    agent_turns = [(i, h) for i, h in enumerate(turns)
                   if str(h.get('role') or '').lower() in ('agent', 'assistant')][-max_turns:]
    # the origin is the first agent turn that stated the figure; a
    # provenance reply that quotes it back is never the origin
    for d in figs:
        for i, h in agent_turns:
            txt = str(h.get('text') or h.get('content') or '')
            if _IS_PROVENANCE_REPLY_RX.search(txt):
                continue
            for m in _FIGURE_RX.finditer(txt):
                if _digits(m.group(0)) == d:
                    return {'figure': m.group(0), 'digits': d, 'turn': i, 'sentence': _excerpt(txt, m.start(), m.end())}
    return None


_IS_PROVENANCE_REPLY_RX = re.compile(r"\b(?:came from the earlier re(?:ply|ad)|is the US projection of the .{1,120} profile in Profile IQ)\b")


def _excerpt(txt, start, end, width=150):
    """The clause around the figure, trimmed to a readable width."""
    s = max(txt.rfind('. ', 0, start) + 1, txt.rfind(':', 0, start) + 1, txt.rfind('\n', 0, start) + 1, 0)
    e_candidates = [p for p in (txt.find('. ', end), txt.find('\n', end), txt.find(';', end)) if p > 0]
    e = min(e_candidates) if e_candidates else len(txt)
    sent = ' '.join(txt[s:e].split()).strip(' :')
    if len(sent) > width:
        lo = max(0, (start - s) - width // 2)
        sent = ('...' if lo else '') + sent[lo:lo + width].strip() + '...'
    return sent


def ledger_record(digits, ledger=None):
    """The newest insights-ledger entry whose reply states the figure:
    {'subject', 'base', 'window_label', 'route', 'k', 'ts'} or None."""
    try:
        il = ledger or __import__('insights_ledger')
        doc = il._load_index()
    except Exception:
        return None
    best = None
    for sk, bucket in ((doc or {}).get('subjects') or {}).items():
        for e in (bucket.get('entries') or []):
            if not isinstance(e, dict):
                continue
            reply = str(e.get('reply') or '')
            if digits not in re.sub(r"[^\d]", "", reply):
                continue
            if not any(_digits(m.group(0)) == digits for m in _FIGURE_RX.finditer(reply)):
                continue
            rec = {'subject': str(e.get('subject') or bucket.get('subject') or ''), 'base': str(e.get('base') or ''),
                   'window_label': str(e.get('wl') or e.get('window_label') or ''), 'route': str(e.get('route') or ''),
                   'k': str(e.get('k') or ''), 'ts': str(e.get('ts') or ''), 'retired': bool(e.get('retired'))}
            if best is None or rec['ts'] > best['ts']:
                best = rec
    return best


def _profile_name(base_key):
    name = str(base_key or '').rsplit('/', 1)[-1]
    name = re.sub(r"\.csv$", "", name, flags=re.I)
    name = re.sub(r"_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}$", "", name)
    return name.replace('_', ' ').strip()


def _view_numbers(view_context):
    """The open product's headline figures as plain lines, for the
    contrast half of the answer. [] when nothing is on screen."""
    vc = view_context if isinstance(view_context, dict) else {}
    data = vc.get('data') if isinstance(vc.get('data'), dict) else {}
    cn = data.get('campaign_numbers') if isinstance(data.get('campaign_numbers'), dict) else {}
    lines = []
    title = str(vc.get('view_title') or '').strip()
    exp = cn.get('exposed_us_people')
    tix = cn.get('ticketing_us_accounts')
    rate = cn.get('conversion_rate_pct')
    if isinstance(exp, (int, float)) and exp:
        lines.append(f"{int(exp):,} US people were exposed to the campaign")
    if isinstance(tix, (int, float)) and tix:
        conv = str(cn.get('conversion_definition') or 'went to a ticketing site or app for a ticket within 7 days').strip()
        conv = re.sub(r"\b(\d+)\s*d\b", r"\1 days", conv)
        conv = conv[0].lower() + conv[1:] if conv else conv
        lines.append(f"{int(tix):,} {conv}" + (f" ({rate}%)" if isinstance(rate, (int, float)) else ''))
    if lines and title:
        return [f"The Attribution IQ numbers for {title} on your screen are the campaign's own: " + '; '.join(lines) + '.']
    return lines


def answer(text, history, ctx=None, ledger=None, host=None):
    """The provenance reply for a quoted figure, or None when the turn is
    not one. {'reply', 'figure', 'record'}"""
    q = quoted_from_thread(text, history)
    if not q:
        return None
    rec = ledger_record(q['digits'], ledger)
    fig = q['figure']
    parts = []
    if rec and rec.get('base'):
        prof = _profile_name(rec['base'])
        win = rec.get('window_label') or ''
        parts.append(f"{fig} is the US projection of the {prof} profile in Profile IQ"
                     + (f", an audience read for {win}" if win else '') + '.')
    elif rec and rec.get('subject'):
        win = rec.get('window_label') or ''
        parts.append(f"{fig} came from the earlier read on {rec['subject']}" + (f" ({win})" if win else '') + '.')
    else:
        subj = ''
        try:
            from . import continuation as _cont
            origin = [h for h in (history or []) if isinstance(h, dict)][q['turn']]
            m = _cont._READ_HEAD_RX.match(str(origin.get('text') or origin.get('content') or ''))
            subj = m.group('who').strip() if m else ''
            if not subj:
                subj = str((_cont.previous_deliverable(history) or {}).get('subject') or '').strip()
        except Exception:
            subj = ''
        where = f"the earlier read on {subj} in this thread" if subj else "the earlier reply in this thread"
        parts.append(f"{fig} came from {where}, not from the data on your screen.")
    vc = (ctx or {}).get('view_context') if isinstance(ctx, dict) else None
    if isinstance(vc, dict) and str(vc.get('view_id') or '') == 'intentIQ' \
            and not ((vc.get('data') or {}).get('campaign_numbers')):
        try:
            hyd = getattr(host, 'intent_view_hydrate', None) if host is not None else None
            vc = hyd(vc) if callable(hyd) else vc
        except Exception:
            pass
    contrast = _view_numbers(vc)
    if contrast:
        parts.append("It is a different dataset from the campaign on your screen and does not describe the campaign.")
        parts.extend(contrast)
        parts.append("Use the campaign's numbers for anything about this campaign; the profile describes who the potential ticket buyers are, not what the campaign reached.")
    reply = ' '.join(p for p in parts if p)
    return {'reply': reply, 'figure': fig, 'record': rec}
