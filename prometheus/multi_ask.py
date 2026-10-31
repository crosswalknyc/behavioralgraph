"""Two asks in one message (Jenna 2026-10-30).

"How big is the Reba audience, and what are the top five QSRs for
Gilmore Girls viewers?" used to get one quote (the size) and the second
question was dropped once the first was paid: the priced gate ran on the
whole message and the split-and-answer-both logic lived only on the plain
analysis path, after the gate.

Now each question routes on its own, through the same door:

  * every question that comes back as a priced offer is listed in ONE
    itemized quote with its own price and a total; Approve charges each
    part at its quoted price (its own ledger line) and runs each read;
    Cancel runs none of them;
  * a question that answers at the metered rate, or free, answers right
    away, in the same reply, next to the quote;
  * one question is one question: the lane steps aside and the single
    path runs exactly as before.

Jenna asked whether to quote the asks together or one at a time. One
itemized quote: a reader with two questions approves once, sees what each
costs, and gets both answers. The charges stay per ask on the ledger.

The lane sits in prometheus.service.ask ahead of the where_watched and
sizing lanes; each part re-enters ask() with ``_multi_part`` set so the
lane never recurses, and a per-part ``panel_confirm`` carries the chip's
own confirm on Approve. The widget and the pending-offer store pass the
``parts`` through unchanged.
"""
from __future__ import annotations

import re

_CLAUSE_OPENERS = (r"what|which|who|how|where|when|why|do|does|did|is|are|was|were|can|could|"
                   r"would|will|should|has|have|tell me|show me|list|compare")
# ", and what are ..." / "; how many ..." / " and also which ..." splits a
# one-sentence double ask into its two questions
_CLAUSE_SPLIT_RX = re.compile(
    r"(?:[,;]\s*|\s+)(?:and|plus|also|then)(?:\s+(?:also|then))?\s+(?=(?:" + _CLAUSE_OPENERS + r")\b)", re.I)
_PRICE_RX = re.compile(r"[\d,]+(?:\.\d+)?")
# a clause that stands as its own question names something: a capitalized word past its first, a figure, a quoted title
_SUBJECT_SIGNAL_RX = re.compile(r"(?<!^)(?<![.?!]\s)\b[A-Z][A-Za-z0-9&'+.-]+|\d|[\"\u201c\u2018']\w")
_MIN_WORDS = 3


def split(text, pma=None):
    """The distinct questions in one message, in order; [] for one."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 1500:
        return []
    qs = []
    try:
        if pma is None:
            import prometheus_analysis as pma   # noqa: WPS433
        qs = list(pma.split_questions(t) or [])
    except Exception:
        qs = []
    if len(qs) >= 2:
        return qs[:6]
    # a single sentence carrying two questions joined by ", and what ..."
    parts = [re.sub(r"\s+(?:and|plus|also|then)$", '', p.strip(' ,;'), flags=re.I).strip() for p in _CLAUSE_SPLIT_RX.split(t)]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return []
    if any(len(p.split()) < _MIN_WORDS for p in parts):
        return []
    # the later clauses must bring their own subject (a name, a number, a
    # quoted title); "compare Reba and Gilmore Girls and tell me which
    # skews older" is one ask with its framing, not two
    for p in parts[1:]:
        if not _SUBJECT_SIGNAL_RX.search(p):
            return []
    parts = [p if p.endswith('?') else p + '?' for p in parts]
    return parts[:6]


def _raw_of(result):
    """The legacy payload inside whatever a part's ask returned."""
    if result is None:
        return {}
    env = result[0] if isinstance(result, tuple) else result
    if hasattr(env, 'get_json'):
        try:
            env = env.get_json(silent=True) or {}
        except Exception:
            env = {}
    if not isinstance(env, dict):
        return {}
    raw = env.get('raw') if isinstance(env.get('raw'), dict) else env.get('_raw')
    return raw if isinstance(raw, dict) else env


def _reply_of(raw):
    for k in ('reply', 'text', 'error'):
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ''


def _price_value(label):
    m = _PRICE_RX.search(str(label or ''))
    if not m:
        return None
    try:
        return float(m.group(0).replace(',', ''))
    except ValueError:
        return None


def _currency_prefix(label):
    s = str(label or '').strip()
    return s[:1] if s and not s[0].isdigit() else '$'


def _fmt_total(labels):
    vals = [_price_value(x) for x in labels]
    if not vals or any(v is None for v in vals):
        return ''
    total = sum(vals)
    pre = _currency_prefix(labels[0])
    return f"{pre}{total:,.0f}" if float(total).is_integer() else f"{pre}{total:,.2f}"


def _lead_of(offer_reply):
    """The offer's plain sentence on what the reader gets, without its
    own cost line."""
    s = str(offer_reply or '').strip()
    s = re.sub(r"\s*Pulling this data will cost[^.]*\.\s*$", '', s, flags=re.I).strip()
    return s


def _jobs(raws):
    out = []
    for r in raws:
        j = r.get('read_job_id') or r.get('job_id')
        if j and str(j) not in out:
            out.append(str(j))
    return out


def _followups(raws, limit=4):
    out = []
    for r in raws:
        for c in (r.get('followups') or []):
            if isinstance(c, str) and c.strip() and c not in out:
                out.append(c)
    return out[:limit]


def _part_block(i, question, raw):
    text = _reply_of(raw) or 'Working on it. That answer will land in this thread when it is ready.'
    return f"{i}) {question}\n{text}"


def combine_answers(parts, raws):
    """One reply for several answered questions: numbered, the question
    restated, the answer under it. Background reads ride as a job list
    (the widget polls each) and the first job in the single field."""
    blocks = [_part_block(i, q, r) for i, (q, r) in enumerate(zip(parts, raws), start=1)]
    jobs = _jobs(raws)
    out = {'success': True, 'action': 'answer', 'reply': '\n\n'.join(blocks),
           'followups': _followups(raws), 'offer_deck': False, 'deck_angle': None,
           'multi_ask': {'count': len(parts)}}
    if jobs:
        out['read_job_id'] = jobs[0]
        out['read_job_ids'] = jobs
    for r in raws:
        if r.get('subject') and not out.get('subject'):
            out['subject'] = r['subject']
        for k in ('memory_confirm', 'bpiq_wizard'):
            if r.get(k) and k not in out:
                out[k] = r[k]
    return out


def combined_offer(text, parts, raws, approve_chip='Approve', cancel_chip='Cancel'):
    """One itemized quote for the priced parts; metered and free answers
    ride along above it. ``panel_offer`` carries every priced part."""
    priced, answered = [], []
    for q, r in zip(parts, raws):
        po = r.get('panel_offer') if isinstance(r.get('panel_offer'), dict) else None
        if po:
            priced.append((q, r, po))
        else:
            answered.append((q, r))
    lines = []
    if answered:
        for i, (q, r) in enumerate(answered, start=1):
            lines.append(_part_block(i, q, r))
    n = len(priced)
    head = ("Two of these are priced reads" if n == 2 else f"{n} of these are priced reads") if answered \
        else ("Two priced reads here" if n == 2 else f"{n} priced reads here")
    lines.append(head + ", quoted together and run together:")
    labels = []
    offer_parts = []
    for i, (q, r, po) in enumerate(priced, start=1):
        label = str(po.get('price_label') or '').strip() or 'the quoted price'
        labels.append(label)
        lead = _lead_of(_reply_of(r)) or q
        lines.append(f"{i}. {lead} {label}.")
        part = {'text': str(po.get('question') or q)[:600], 'subject': str(po.get('subject') or '')[:120],
                'kind': str(po.get('kind') or 'report'), 'dimension': str(po.get('dimension') or ''),
                'frame': str(po.get('frame') or ''), 'price_label': label}
        if po.get('years') and po.get('years') != 1:
            part['years'] = po['years']
        offer_parts.append(part)
    total = _fmt_total(labels)
    tail = f"Approve runs both for {total} in all" if total and n == 2 else \
        (f"Approve runs all {n} for {total} in all" if total else "Approve runs them")
    lines.append(tail + "; Cancel runs none of them.")
    jobs = _jobs([r for _, r in answered])
    out = {'success': True, 'action': 'answer', 'reply': '\n\n'.join(lines),
           'followups': [approve_chip, cancel_chip], 'offer_deck': False, 'deck_angle': None,
           'subject': offer_parts[0]['subject'] if offer_parts else '',
           'panel_offer': {'question': str(text)[:600], 'subject': offer_parts[0]['subject'] if offer_parts else '',
                           'kind': 'multi', 'frame': '', 'price_label': total or labels[0],
                           'parts': offer_parts},
           'multi_ask': {'count': len(parts), 'priced': n}}
    if jobs:
        out['read_job_id'] = jobs[0]
        out['read_job_ids'] = jobs
    return out


def confirm_parts(panel_confirm):
    """The per-part confirms inside a multi confirm, or []."""
    if not isinstance(panel_confirm, dict) or str(panel_confirm.get('kind') or '') != 'multi':
        return []
    out = []
    for p in (panel_confirm.get('parts') or []):
        if not isinstance(p, dict) or not str(p.get('text') or '').strip():
            continue
        pc = {'subject': str(p.get('subject') or ''), 'kind': str(p.get('kind') or 'report'),
              'dimension': str(p.get('dimension') or '')}
        if p.get('years') and p.get('years') != 1:
            pc['years'] = p['years']
        out.append((str(p['text']).strip(), pc))
    return out


def handle(text, body, run_one, *, pma=None, approve_chip='Approve', cancel_chip='Cancel'):
    """None when the lane does not apply; else the combined raw payload.

    ``run_one(question, panel_confirm, prior_parts)`` runs one question
    through the whole door, with the message's earlier questions behind
    it, and returns the ask result (envelope tuple or payload).
    """
    if not isinstance(body, dict) or body.get('_multi_part'):
        return None
    pc = body.get('panel_confirm')
    if not isinstance(pc, dict) or not pc:
        extra = body.get('extra') if isinstance(body.get('extra'), dict) else {}
        pc = extra.get('panel_confirm') if isinstance(extra.get('panel_confirm'), dict) else None
    confirms = confirm_parts(pc)
    if confirms:
        parts = [q for q, _ in confirms]
        raws = [_raw_of(run_one(q, part_pc, parts[:i])) for i, (q, part_pc) in enumerate(confirms)]
        out = combine_answers(parts, raws)
        out['multi_ask']['confirmed'] = True
        return out
    if isinstance(pc, dict) and pc:
        return None             # a single confirm rides the single path
    parts = split(text, pma=pma)
    if len(parts) < 2:
        return None
    # a later part reads with the earlier ones behind it ("do they also
    # watch Yellowstone" binds "they" to the first question's audience)
    raws = [_raw_of(run_one(q, None, parts[:i])) for i, q in enumerate(parts)]
    if any(isinstance(r.get('panel_offer'), dict) and r.get('panel_offer') for r in raws):
        return combined_offer(text, parts, raws, approve_chip=approve_chip, cancel_chip=cancel_chip)
    return combine_answers(parts, raws)
