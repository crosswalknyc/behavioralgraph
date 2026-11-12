"""The deck a which-subject pick builds, and a deck made from a read
(2026-11-11, Samantha Kolton at CAA).

She asked "Can you turn this into a deck?" right after a partnership
value read, was asked which subject (Gap x JYT, Gap Customers or Paige
Spiranac), picked "Gap x JYT", and the reply said "Building the Paige
Spiranac insights deck". Gap x JYT is a read in the thread, not a
profile in the library, so the pick resolved to nothing and the deck
fell back to the profile open on her screen: the deck was planned on
the wrong profile's file with the read only in the chat history, and it
came back with a new-buyer count the read never gave (48,217 against
15,147) and a product the collection does not have.

Three rules live here, pure and testable:

- `settle(pick, text, ctx, history, resolve, ...)`: the deck's subject
  is the one the reader picked or named. A library profile of that name
  first; else the read delivered on it in this thread, which becomes
  the deck's material; never another candidate from the list. With
  nothing to build on, one plain line says so and no deck starts.
- `confirm_line(resolution)`: the line that confirms the build names
  that subject and says plainly which deck it is. A deck made from a
  partnership value read says it restates the read and is not the
  Brand Partnership valuation.
- `hold_plan_to_read(plan, ctx, replan)`: a deck made from a read takes
  its figures from the read. A count the plan gives for a measure the
  read already states (first-time buyers, buyers, people reached, the
  partnership's value) at a different figure is a conflict: the plan is
  made once more with the conflicts spelled out, and whatever is left
  is set to the read's own figure in place. The deck always ships.

User-facing copy is Prometheus first person. No em dashes. Nothing here
says how a number was produced.
"""
from __future__ import annotations

import json
import re

READ_SOURCE = 'read'
_PAIR_SPLIT_RX = re.compile(r"\s+[xX\u00d7]\s+")
_METHOD_WORDS_RX = re.compile(
    r"\b(?:reasoned|modeled|modelled|estimated|synth\w*|measured row|not a measured|researched|anchored|calibrated|"
    r"derived|inferred|assumption|assumed)\b", re.I)
_PARTNERSHIP_RX = re.compile(r"\bpartnership\b", re.I)


def _fold(text):
    return re.sub(r"[^a-z0-9]+", '', str(text or '').lower())


def _norm(text):
    return ' '.join(str(text or '').split())


def pair_parts(name):
    """('Gap', 'JYT') for 'Gap x JYT'; None for a single name."""
    parts = [p.strip() for p in _PAIR_SPLIT_RX.split(_norm(name), maxsplit=1)]
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return None


def same_subject(a, b):
    """Two spellings of one subject. A pairing is the same pairing in
    either order ('Gap x JYT' and 'JYT x Gap')."""
    fa, fb = _fold(a), _fold(b)
    if not fa or not fb:
        return False
    if fa == fb:
        return True
    pa, pb = pair_parts(a), pair_parts(b)
    if pa and pb:
        return {_fold(pa[0]), _fold(pa[1])} == {_fold(pb[0]), _fold(pb[1])}
    if pa or pb:
        return False          # a pairing is never one of its sides
    try:
        from . import subject_rules as _sr
        return bool(_sr.same_entity(a, b))
    except Exception:
        return min(len(fa), len(fb)) >= 5 and (fa in fb or fb in fa)


# ------------------------------------------------------------ the read
def thread_reads(history):
    """The reads delivered in this thread, newest first: each agent turn
    that carries a read's own record (its subject and job id)."""
    out = []
    for turn in reversed(list(history or [])):
        if not isinstance(turn, dict) or str(turn.get('role') or '') not in ('agent', 'assistant'):
            continue
        meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
        subj = _norm(meta.get('subject') or turn.get('subject') or '')
        job = str(meta.get('read_job_id') or turn.get('read_job_id') or '').strip()
        text = str(turn.get('text') or turn.get('content') or '').strip()
        if not subj or not text:
            continue
        if not (job or str(meta.get('kind') or '') == 'read'):
            continue
        out.append({'subject': subj, 'job_id': job, 'text': text})
    return out


def load_read_doc(s3, bucket, prefix, job_id, uname):
    """A read's stored record, when it belongs to `uname`; None otherwise
    (the job id arrives in the thread the page sent, so the owner is
    checked here). Never raises."""
    jid = str(job_id or '').strip()
    if not re.fullmatch(r'[0-9a-f]{12}', jid) or not str(uname or '').strip():
        return None
    try:
        doc = json.loads(s3.get_object(Bucket=bucket, Key=f"{prefix}{jid}.json")['Body'].read().decode('utf-8'))
    except Exception:
        return None
    if not isinstance(doc, dict) or str(doc.get('user') or '').strip().lower() != str(uname).strip().lower():
        return None
    return doc


def _clean_definition(defn):
    """A figure's definition with any clause about how it was produced
    removed; '' when nothing plain is left."""
    keep = [c.strip() for c in re.split(r",\s+|;\s*", str(defn or "")) if c.strip() and not _METHOD_WORDS_RX.search(c)]
    return ', '.join(keep)


def _fmt_value(value, unit):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    u = str(unit or '').lower()
    if u in ('pct', 'percent', '%'):
        return f"{v:.1f}%"
    if u in ('usd', 'dollars', '$'):
        return f"${v:,.2f}" if abs(v - round(v)) > 1e-9 else f"${int(round(v)):,}"
    return f"{int(round(v)):,}" if abs(v - round(v)) < 1e-9 else f"{v:,.2f}"


def read_figures(payload):
    """[{label, value, unit, kind}] from a read's stored record."""
    out = []
    for m in ((payload or {}).get('metrics') or []):
        if not isinstance(m, dict) or m.get('value') in (None, ''):
            continue
        try:
            val = float(m['value'])
        except (TypeError, ValueError):
            continue
        label = _norm(m.get('label') or m.get('name') or '')
        unit = str(m.get('unit') or '').lower()
        out.append({'label': label, 'value': val, 'unit': unit,
                    'kind': measure_kind(f"{label} {str(m.get('name') or '').replace('_', ' ')}"),
                    'definition': _clean_definition(m.get('definition'))})
    return out


def read_material(read, payload=None):
    """The text a deck made from a read restates: the read as written,
    then its figures and its table, each exactly as delivered."""
    subj = read.get('subject') or 'this read'
    parts = [f"THE READ THIS DECK RESTATES, {subj}:\n{read.get('text') or ''}"]
    figs = read_figures(payload) if isinstance(payload, dict) else []
    if figs:
        lines = []
        for f in figs:
            line = f"- {f['label']}: {_fmt_value(f['value'], f['unit'])}"
            if f.get('definition'):
                line += f" ({f['definition']})"
            lines.append(line)
        parts.append("THE READ'S FIGURES (each one exactly as written; the deck gives no other figure for the same measure):\n"
                     + '\n'.join(lines))
    bd = (payload or {}).get('breakdown') if isinstance(payload, dict) else None
    if isinstance(bd, dict) and bd.get('rows'):
        rows = []
        for r in bd['rows'][:40]:
            if not isinstance(r, dict) or not r.get('label'):
                continue
            bits = []
            if r.get('share_pct') not in (None, ''):
                bits.append(f"{float(r['share_pct']):.1f}% share")
            if r.get('penetration_pct') not in (None, ''):
                bits.append(f"{float(r['penetration_pct']):.1f}% penetration")
            if r.get('value') not in (None, ''):
                bits.append(str(r['value']))
            note = _norm(r.get('note') or '')
            rows.append(f"- {r['label']}: " + ', '.join(bits) + (f". {note}" if note else ''))
        if rows:
            parts.append(f"THE READ'S TABLE, {bd.get('dimension') or 'breakdown'}"
                         + (f" ({bd.get('share_basis')})" if bd.get('share_basis') else '')
                         + " (these rows and these labels, no others):\n" + '\n'.join(rows))
    return parts


def is_partnership_read(read, payload=None):
    """A read of what a talent and brand partnership is worth."""
    if pair_parts(read.get('subject') or ''):
        blob = str(read.get('text') or '')[:600]
        if _PARTNERSHIP_RX.search(blob):
            return True
    for m in ((payload or {}).get('metrics') or []) if isinstance(payload, dict) else []:
        if isinstance(m, dict) and 'partnership' in f"{m.get('name')} {m.get('label')}".lower():
            return True
    return False


def read_angle(subject, text=''):
    """The brief a deck made from a read is planned under."""
    ask = _norm(text)
    lead = f"{ask} " if ask and len(ask) <= 200 else ''
    return (f"{lead}A deck of the read already delivered on {subject}: every number, table and line exactly as the read "
            f"states it, nothing added. No new count for anything the read already counts, no product, piece or name "
            f"the read does not carry, and only as many pages as the read fills.")[:400]


def from_read(named, history, load_read=None):
    """The resolution for a deck on a read delivered in this thread, or
    None. `load_read(job_id)` returns the read's stored record (the
    caller checks it belongs to the reader) or None."""
    if not _norm(named):
        return None
    read = next((r for r in thread_reads(history) if same_subject(r['subject'], named)), None)
    if not read:
        return None
    payload = None
    if read.get('job_id') and callable(load_read):
        try:
            doc = load_read(read['job_id'])
            payload = (doc or {}).get('payload') if isinstance(doc, dict) and isinstance(doc.get('payload'), dict) else doc
            if not isinstance(payload, dict):
                payload = None
        except Exception:
            payload = None
    subject = read['subject']
    figs = read_figures(payload) if payload else []
    partnership = is_partnership_read(read, payload)
    ctx = {'primary': None, 'cuts': [], 'extras': [], 'deck_source': READ_SOURCE,
           'view_context': {'view_id': 'read', 'view_title': subject[:80],
                            # words only: the page-count projection never touches a read's figures
                            'data': {'read': subject, 'kind': 'partnership value read' if partnership else 'read'},
                            'material': read_material(read, payload),
                            'read_figures': [{k: f[k] for k in ('label', 'value', 'unit', 'kind')} for f in figs],
                            'read_job_id': read.get('job_id') or ''}}
    return {'ctx': ctx, 'subject': subject, 'partner': '', 'clarify': None, 'view_deck': True,
            'from_read': True, 'partnership_read': partnership}


def _brief_subject(text):
    try:
        import prometheus_analysis as pma
        return _norm((pma.extract_deck_brief(text) or {}).get('subject') or '')
    except Exception:
        return ''


def _page_name(ctx):
    try:
        name = _norm(((ctx or {}).get('primary') or {}).get('name') or '')
    except Exception:
        name = ''
    return name.split(' - ')[0].strip() or name


def settle(pick, text, ctx, history, resolve, load_read=None):
    """(resolution, err, message) for a deck ask.

    `resolve(text, ctx) -> (resolution, err)` is the library resolver.
    With a `pick` (the which-subject chip the reader tapped) the deck is
    on that subject or it does not start: a library profile of that
    name, else the read delivered on it in this thread, else the page
    when the page is what was picked, else the library's own did-you-mean
    list, else `message` (one plain line; resolution and err are None).
    With no pick the words decide as before, except that words naming a
    read delivered in this thread build from that read."""
    pick = _norm(pick)
    named = pick or _brief_subject(text)
    res, err = resolve(f"build the {pick} insights deck" if pick else text, ctx)
    if not named:
        return res, err, ''
    ok = bool(res) and not err and not res.get('clarify')
    if ok and not res.get('used_open_page') and same_subject(res.get('subject'), named):
        return res, err, ''
    rd = from_read(named, history, load_read)
    if rd:
        return rd, None, ''
    if not pick:
        return res, err, ''
    if ok and same_subject(res.get('subject'), pick):
        return res, err, ''              # the page itself was the pick
    page = _page_name(ctx)
    if page and same_subject(page, pick):
        res2, err2 = resolve(text, ctx)
        if res2 and not err2 and not res2.get('clarify') and same_subject(res2.get('subject'), pick):
            return res2, err2, ''
    if res and not err and res.get('clarify'):
        return res, err, ''              # the library's own did-you-mean chips for the picked name
    return None, None, (f"I do not have a profile or a finished read on {pick} to build the deck from. "
                        f"Run the read on {pick} first, or open its profile, then ask for the deck again.")


def confirm_line(resolution):
    """The line that confirms a deck build: the subject the deck is on
    and, for a deck made from a read, which deck it is."""
    r = resolution or {}
    subject = _norm(r.get('subject') or '')
    tail = ' The download link lands here when it is ready.'
    if r.get('from_read'):
        if r.get('partnership_read'):
            return (f"Building an insights deck from the {subject} read you already have. It restates that read's "
                    f"figures; it is not the Brand Partnership valuation." + tail)
        return f"Building an insights deck from the {subject} read you already have." + tail
    # a name that opens with its own article keeps it and takes no second one ("Building The Rainmaker TV Series
    # insights deck", never "the The Rainmaker"; 2026-11-12)
    lead = 'Building ' if re.match(r"(?i)the\s", subject) else 'Building the '
    return lead + (subject + ' ' if subject else '') + 'insights deck.' + tail


# ------------------------------------------- the deck keeps the read's figures
_KIND_RXS = (
    ('new_buyers', re.compile(
        r"first[- ]time\s+(?:\w+\s+){0,2}(?:buyers?|purchasers?|customers?|orders?)|new[- ]to[- ](?:brand|\w+)\s+(?:buyers?|customers?)|"
        r"\bnew\s+(?:\w+\s+){0,2}(?:buyers?|purchasers?|customers?)|\bfirst\s+(?:\w+\s+){0,2}order|\bbuyers?\s+(?:are|were)\s+new\b|"
        r"\bnewly\s+acquired\b|\bacquired\s+(?:\w+\s+){0,2}buyers?", re.I)),
    ('value', re.compile(r"partnership(?:'s)?\s+value|value\s+of\s+the\s+partnership|\bbrand\s+value\b|\bvalue\s+(?:to\s+date|booked|carried)\b", re.I)),
    ('buyers', re.compile(r"\b(?:buyers?|purchasers?)\b", re.I)),
    ('audience', re.compile(r"\b(?:audience|engagers?|people\s+reached|reached|reach|touched|projected\s+us\s+people)\b", re.I)),
)
_NUM_RX = re.compile(r"(?<![\w.,$])(\$?)(\d{1,3}(?:,\d{3})+|\d{4,})(\.\d+)?(?![\d,]*\s*(?:%|pt\b|pp\b|x\b))(?!\.\d)")


def measure_kind(label):
    """The measure a label names (first-time buyers, buyers, people
    reached, the partnership's value), or ''."""
    t = _norm(label)
    for kind, rx in _KIND_RXS:
        if rx.search(t):
            return kind
    return ''


def _nearest_kind(text, start, end, reach=64):
    best, best_d = '', None
    lo, hi = max(0, start - reach), min(len(text), end + reach)
    seg = text[lo:hi]
    for kind, rx in _KIND_RXS:
        for m in rx.finditer(seg):
            a, b = lo + m.start(), lo + m.end()
            d = 0 if (a < end and b > start) else min(abs(a - end), abs(start - b))
            if best_d is None or d < best_d or (d == best_d and not best):
                best, best_d = kind, d
        # the table is ordered most specific first: an equal distance keeps the earlier kind
    return best


def _units(node, path=()):
    """Every dict of the plan with its own string fields joined (a tile's
    value and its label read together), and every loose string in a list."""
    if isinstance(node, dict):
        strs = [(k, v) for k, v in node.items() if isinstance(v, str) and v.strip()]
        if strs:
            yield node, strs
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from _units(v)
    elif isinstance(node, list):
        loose = [(i, v) for i, v in enumerate(node) if isinstance(v, str) and v.strip()]
        if loose:
            yield node, loose
        for v in node:
            if isinstance(v, (dict, list)):
                yield from _units(v)


def _is_year(tok):
    return tok.isdigit() and len(tok) == 4 and 1900 <= int(tok) <= 2100


def conflicts(plan, figures):
    """The counts in a plan that disagree with the read it was made from:
    [{'kind', 'found', 'want', 'context'}]. A count stands when it equals
    any figure the read gives. A first-time-buyer count that differs is a
    conflict; a buyer, reach or value figure above the read's own is one
    too (a cut of the read's people is smaller, never larger)."""
    by_kind = {}
    values = []
    for f in figures or []:
        try:
            v = float(f.get('value'))
        except (TypeError, ValueError):
            continue
        if str(f.get('unit') or '').lower() in ('pct', 'percent', '%') or v < 100:
            continue
        values.append(v)
        if f.get('kind'):
            by_kind.setdefault(f['kind'], []).append(v)
    out = []
    if not by_kind:
        return out
    for _holder, fields in _units(plan):
        joined = ' | '.join(v for _k, v in fields)
        for m in _NUM_RX.finditer(joined):
            tok = m.group(2).replace(',', '')
            if _is_year(tok) and not m.group(1) and ',' not in m.group(2):
                continue
            try:
                num = float(tok + (m.group(3) or ''))
            except ValueError:
                continue
            if num < 100:
                continue
            if any(abs(num - v) <= max(1.0, 0.005 * v) for v in values):
                continue
            kind = _nearest_kind(joined, m.start(), m.end())
            if kind not in by_kind:
                continue
            ceiling = max(by_kind[kind])
            if kind == 'new_buyers' or num > ceiling * 1.005:
                want = min(by_kind[kind], key=lambda v: abs(v - num)) if kind != 'new_buyers' else by_kind[kind][0]
                if num > ceiling:
                    want = ceiling if kind != 'new_buyers' else want
                out.append({'kind': kind, 'found': m.group(0), 'want': want, 'context': joined[:160]})
    return out


def conflict_note(found):
    """The correction handed back to the planner, in plain words."""
    names = {'new_buyers': 'first-time buyers', 'buyers': 'buyers', 'audience': 'people reached',
             'value': "the partnership's value"}
    lines = []
    seen = set()
    for c in found[:12]:
        key = (c['kind'], c['found'])
        if key in seen:
            continue
        seen.add(key)
        want = c['want']
        lines.append(f"- {names.get(c['kind'], c['kind'])}: the draft says {c['found']}; the read says "
                     f"{('$' if c['found'].startswith('$') else '')}{int(round(want)):,}.")
    return ("CORRECTION (binding). The draft deck gave figures the read does not. Rebuild the deck with the read's own "
            "figures and remove every line that depends on a figure the read does not state:\n" + '\n'.join(lines))


def _force(plan, figures):
    """Set every conflicting count to the read's own figure, in place."""
    n = 0
    for holder, fields in _units(plan):
        for key, val in fields:
            joined = val
            found = conflicts({'x': joined}, figures)
            if not found:
                # a tile's label can sit in a sibling field: judge the value with its siblings
                sib = ' | '.join(v for _k, v in fields)
                found = [c for c in conflicts({'x': sib}, figures) if c['found'] in val]
            new = val
            for c in found:
                rep = ('$' if c['found'].startswith('$') else '') + f"{int(round(c['want'])):,}"
                new = new.replace(c['found'], rep)
            if new != val:
                holder[key] = new
                n += 1
    return n


def hold_plan_to_read(plan, ctx, replan=None, post=None):
    """A deck made from a read keeps the read's figures. Returns the plan
    (the same object, or the one `replan(note)` made, passed through
    `post`). A deck that is not made from a read is returned untouched."""
    try:
        vc = (ctx or {}).get('view_context') if isinstance(ctx, dict) else None
        if not (isinstance(ctx, dict) and ctx.get('deck_source') == READ_SOURCE and isinstance(vc, dict)):
            return plan
        figures = vc.get('read_figures') or []
        found = conflicts(plan, figures)
        if not found:
            return plan
        print(f"[deck-pick] {len(found)} figures disagree with the read: "
              + json.dumps([(c['kind'], c['found']) for c in found[:6]]))
        if callable(replan):
            try:
                again = replan(conflict_note(found))
            except Exception as e:
                print(f"[deck-pick] replan failed: {type(e).__name__}: {e}")
                again = None
            if isinstance(again, list):
                again = next((d for d in again if isinstance(d, dict)), None)
            if isinstance(again, dict) and callable(post):
                try:
                    again = post(again)
                except Exception:
                    again = None
            if isinstance(again, dict) and again.get('slides'):
                plan = again
                found = conflicts(plan, figures)
        if found:
            n = _force(plan, figures)
            print(f"[deck-pick] {n} fields set to the read's own figures")
        return plan
    except Exception as e:
        print(f"[deck-pick] hold skipped: {type(e).__name__}: {e}")
        return plan


__all__ = ['settle', 'from_read', 'load_read_doc', 'confirm_line', 'hold_plan_to_read', 'conflicts', 'conflict_note', 'thread_reads',
           'read_figures', 'read_material', 'read_angle', 'same_subject', 'pair_parts', 'measure_kind',
           'is_partnership_read', 'READ_SOURCE']
