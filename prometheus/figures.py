"""Figures by composition, with provenance (audit items 3 and 4, Jenna
2026-10-14: "do all 9 of them").

Until now the answer model typed every number into prose and a chain of
nets (vocabulary scrub, evidence bind, published bind, projection fix)
tried to catch what drifted. The principled shape is composition: the
figures come from the evidence, the model chooses which to use and how
to phrase the read, and a renderer formats every number.

  registry = Registry.from_blocks({'subscriber_iq file': block, ...})
  prompt   += registry.prompt_block()        # [f1] 284,541 = FanDuel viewers inside the sample ...
  reply     = registry.render(model_reply)   # [f1] -> 284,541 ; [calc: f3/f1*100 | pct] -> 0.55%
  ledger    = registry.ledger(reply)         # every figure with its source, plus any the model typed

The model is asked to write every count as a token, [f12], or as
arithmetic on tokens, [calc: f3/f1*100 | pct]; the renderer substitutes
and evaluates (safe arithmetic only). Any bare count that still appears
in the prose is bound to the nearest registry figure within two percent
(the evidence bind) and otherwise recorded with source 'model_typed', so
the ledger always says where a number came from.

Provenance vocabulary (the `source` on every figure):
  profile_file, subscriber_iq_file, journey_file, campaign_file,
  catalog_fact, ledger, research_anchor, view_data, derived (arithmetic
  the renderer evaluated), model_typed (the model wrote it unsourced).
"""
from __future__ import annotations

import ast
import operator as _op
import re
import traceback

SOURCES = ('profile_file', 'subscriber_iq_file', 'journey_file', 'campaign_file', 'catalog_fact', 'ledger',
           'research_anchor', 'view_data', 'derived', 'model_typed')

# which block header means which source
_HEADER_SOURCE = (
    ('SUBSCRIBER ACQUISITION EVIDENCE', 'subscriber_iq_file'),
    ('SUBSCRIBER IQ', 'subscriber_iq_file'),
    ('PUBLISHED MEASUREMENTS', 'catalog_fact'),
    ('CATALOG', 'catalog_fact'),
    ('LEDGER', 'ledger'),
    ('EARLIER ANSWERS', 'ledger'),
    ('MEASURED', 'profile_file'),
    ('PROFILE', 'profile_file'),
    ('JOURNEY', 'journey_file'),
    ('DIGITAL JOURNEY', 'journey_file'),
    ('ATTRIBUTION', 'campaign_file'),
    ('CAMPAIGN', 'campaign_file'),
    ('ANCHOR', 'research_anchor'),
    ('VIEW', 'view_data'),
    ('ON SCREEN', 'view_data'),
)
_NUM_RX = re.compile(r"(?<![\w.,/$])(\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?|\$?\d+(?:\.\d+)?%|\$?\d{4,}(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:M|K|B|million|thousand|billion)\b)(?![\w,]|\.\d)")
_HEADER_RX = re.compile(r"^\s*([A-Z][A-Z0-9 /&#'-]{5,}?)(?=\s*\(|:|\s-\s|$|\s+[a-z])")
_TOKEN_RX = re.compile(r"\[\s*f(\d+)\s*\]|\{\{\s*f(\d+)\s*\}\}")
_CALC_RX = re.compile(r"\[\s*calc\s*:\s*([^\]|]+?)\s*(?:\|\s*(pct1|pct2|pct|count|money|x|raw))?\s*\]", re.I)
_YEAR_RX = re.compile(r"^(19|20)\d\d$")


def _is_date(text, m):
    """A year inside a date, a month/day pair or an episode marker is not
    a figure (prometheus.stated_numbers, 2026-10-21)."""
    try:
        from . import stated_numbers as _sn
        return _sn.is_date_token(text, m.start(1), m.end(1))
    except Exception:
        return False

PROMPT_RULE = (
    "FIGURES BY TOKEN (hard rule): every count, percent and dollar figure you state must come from the FIGURES table below, "
    "written as its token, e.g. [f12], or as arithmetic on tokens, e.g. [calc: f3/f1*100 | pct] for a share, [calc: f2-f5 | count] for a "
    "difference, [calc: f7/f8 | x] for a multiple. Never retype digits for a count that is in the table. Never invent a figure that is "
    "not in the table or derivable from it by arithmetic; if the table lacks what the question needs, say so in words. Percents already "
    "in the table are quoted by token too."
)


def _fmt_count(v):
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def _fmt(value, kind):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if kind == 'pct':
        return f"{v:.1f}%"
    if kind == 'pct2':
        return f"{v:.2f}%"
    if kind == 'pct1':
        return f"{v:.1f}%"
    if kind == 'money':
        return f"${v:,.0f}" if abs(v) >= 100 else f"${v:,.2f}"
    if kind == 'x':
        return f"{v:.1f}x"
    if kind == 'raw':
        return f"{v:g}"
    return _fmt_count(v)


def _parse_number(tok):
    """('284,541' -> (284541.0, 'count')), ('0.55%' -> (0.55, 'pct')),
    ('$1,200' -> (1200.0, 'money')), ('2.9M' -> (2900000.0, 'count'))."""
    t = str(tok or '').strip()
    kind = 'count'
    if t.endswith('%'):
        kind = 'pct'
        t = t[:-1]
    if t.startswith('$'):
        kind = 'money'
        t = t[1:]
    mult = 1.0
    m = re.match(r"^([\d.,]+)\s*(M|K|B|million|thousand|billion)$", t, re.I)
    if m:
        t = m.group(1)
        mult = {'k': 1e3, 'thousand': 1e3, 'm': 1e6, 'million': 1e6, 'b': 1e9, 'billion': 1e9}[m.group(2).lower()]
    try:
        return float(t.replace(',', '')) * mult, kind
    except ValueError:
        return None, kind


class Registry:
    def __init__(self):
        self.figures = []          # [{'id','value','kind','label','source','detail'}]
        self._by_key = {}

    # ------------------------------------------------------------ build
    def add(self, value, label, source='view_data', detail='', kind='count'):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        if source not in SOURCES:
            source = 'view_data'
        key = (round(v, 4), kind, str(label or '')[:60].lower())
        if key in self._by_key:
            return self._by_key[key]
        fid = f"f{len(self.figures) + 1}"
        self.figures.append({'id': fid, 'value': v, 'kind': kind, 'label': str(label or 'figure')[:80],
                             'source': source, 'detail': str(detail or '')[:120]})
        self._by_key[key] = fid
        return fid

    @classmethod
    def from_blocks(cls, blocks, limit=120):
        """blocks: {source_or_header: text}. Every number in the text
        becomes a figure with a short label (the words before it) and the
        block's source."""
        reg = cls()
        for name, text in (blocks or {}).items():
            src = name if name in SOURCES else source_for_header(name)
            reg.add_text(str(text or ''), src, limit=limit)
        return reg

    def add_text(self, text, default_source='view_data', limit=120):
        cur_src = default_source
        cur_hdr = ''
        for line in str(text or '').split('\n'):
            hm = _HEADER_RX.match(line)
            if hm:
                cur_src = source_for_header(hm.group(1), cur_src)   # an unknown header inherits the block's source
                cur_hdr = ' '.join(w for w in hm.group(1).title().split() if w.isalpha())[:40]
            for m in _NUM_RX.finditer(line):
                if _is_date(line, m):
                    continue
                if len(self.figures) >= limit:
                    return
                tok = m.group(1)
                if _YEAR_RX.match(tok.replace(',', '')):
                    continue
                v, kind = _parse_number(tok)
                if v is None:
                    continue
                if kind == 'count' and v < 1000 and '%' not in tok:
                    continue   # small counts are rarely the figures a reader needs by token
                label = _label_before(line, m.start(), cur_hdr)
                tail = line[m.end():m.end() + 24].strip()
                if tail.lower().startswith('us') or tail.startswith('people'):
                    label = (label + ' (US people)')[:80]
                self.add(v, label, cur_src, kind=kind)

    # ------------------------------------------------------------ prompt
    def prompt_suffix(self):
        blk = self.prompt_block()
        return ('\n\n' + blk) if blk else ''

    def prompt_block(self):
        if not self.figures:
            return ''
        rows = []
        for f in self.figures:
            rows.append(f"[{f['id']}] {_fmt(f['value'], f['kind'])} = {f['label']} ({f['source'].replace('_', ' ')})")
        return PROMPT_RULE + "\n\nFIGURES:\n" + '\n'.join(rows)

    # ------------------------------------------------------------ render
    def value(self, fid):
        for f in self.figures:
            if f['id'] == fid:
                return f
        return None

    def _calc(self, expr, kind):
        names = {f['id']: f['value'] for f in self.figures}
        node = ast.parse(expr.strip(), mode='eval')
        ops = {ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul, ast.Div: _op.truediv, ast.USub: _op.neg, ast.UAdd: _op.pos}

        def ev(n):
            if isinstance(n, ast.Expression):
                return ev(n.body)
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
                return float(n.value)
            if isinstance(n, ast.Name) and n.id in names:
                return float(names[n.id])
            if isinstance(n, ast.BinOp) and type(n.op) in ops:
                return ops[type(n.op)](ev(n.left), ev(n.right))
            if isinstance(n, ast.UnaryOp) and type(n.op) in ops:
                return ops[type(n.op)](ev(n.operand))
            raise ValueError('unsupported expression')
        v = ev(node)
        return v

    def render(self, text):
        """Tokens and calcs become formatted figures; records what was
        rendered (self.rendered) for the ledger."""
        self.rendered = []
        out = str(text or '')

        def _tok(m):
            fid = 'f' + (m.group(1) or m.group(2))
            f = self.value(fid)
            if not f:
                return m.group(0)
            txt = _fmt(f['value'], f['kind'])
            self.rendered.append({**f, 'via': 'token', 'text': txt})
            return txt

        def _calc(m):
            expr, kind = m.group(1), (m.group(2) or 'count').lower()
            try:
                v = self._calc(expr, kind)
            except Exception:
                return m.group(0)
            if v != v or v in (float('inf'), float('-inf')):
                return m.group(0)
            txt = _fmt(v, kind)
            self.rendered.append({'id': f"calc:{expr.strip()}", 'value': v, 'kind': kind, 'label': expr.strip(), 'source': 'derived', 'detail': expr.strip(), 'via': 'calc', 'text': txt})
            return txt
        out = _TOKEN_RX.sub(_tok, out)
        out = _CALC_RX.sub(_calc, out)
        # a token the model malformed ([f99], [calc: ...] on a missing figure) must never reach a reader
        out = re.sub(r"\[\s*f\d+\s*\]", 'that figure', out)
        out = re.sub(r"\[\s*calc\s*:[^\]]*\]", 'that figure', out)
        return out

    # ------------------------------------------------------------ ledger
    def ledger(self, final_text, tol=0.02):
        """Every figure the final text carries, with its source: rendered
        tokens and calcs, registry figures the text quotes verbatim, and
        anything else typed by the model (source model_typed)."""
        seen = set()
        out = []
        for r in getattr(self, 'rendered', []) or []:
            key = (round(float(r['value']), 4), r['kind'])
            if key not in seen:
                seen.add(key)
                out.append({'value': r['value'], 'kind': r['kind'], 'label': r['label'], 'source': r['source'], 'id': r.get('id')})
        rendered_text = {str(r.get('text') or '') for r in (getattr(self, 'rendered', []) or [])}
        by_val = {}
        for f in self.figures:
            by_val.setdefault((round(f['value'], 4), f['kind']), f)
        for m in _NUM_RX.finditer(str(final_text or '')):
            if _is_date(str(final_text or ''), m):
                continue
            tok = m.group(1)
            if _YEAR_RX.match(tok.replace(',', '')) or tok in rendered_text:
                continue
            v, kind = _parse_number(tok)
            if v is None or (kind == 'count' and v < 1000):
                continue
            key = (round(v, 4), kind)
            if key in seen:
                continue
            seen.add(key)
            f = by_val.get(key)
            if f:
                out.append({'value': v, 'kind': kind, 'label': f['label'], 'source': f['source'], 'id': f['id']})
                continue
            near = None
            for (fv, fk), ff in by_val.items():
                if fk == kind and fv and abs(fv - v) / abs(fv) <= tol:
                    near = ff
                    break
            out.append({'value': v, 'kind': kind, 'label': (near or {}).get('label') or 'typed by the model',
                        'source': 'model_typed', 'id': None, 'near': (near or {}).get('id')})
        return out


def _label_before(line, pos, header=''):
    """The words that name a figure: the text before it on the line, cut
    at the last ';' or ':' or ')' boundary, with earlier numbers and
    their units dropped. A date token (2026-09) stays; an empty label
    falls back to the section header plus that date."""
    head = line[:pos]
    cut = max(head.rfind(';'), head.rfind(': ') + 1 if head.rfind(': ') >= 0 else -1, head.rfind(')'))
    if cut >= 0 and cut < len(head) - 2:
        head = head[cut + 1:]
    head = re.sub(r"[\[\]{}]", ' ', head)
    words, date = [], ''
    for w in head.split():
        ws = w.strip('(),;:')
        if re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?|Q[1-4]|\d{4}", ws):
            date = ws
        elif not re.search(r"\d", w) and ws not in ('US', 'us', '', '-'):
            words.append(w)
    label = ' '.join(words[-7:]).strip(' ,;:(-')
    if not label and header:
        label = header
    if date:
        label = (label + ' ' + date).strip()
    return label or 'figure'


def source_for_header(header, default='view_data'):
    h = str(header or '').upper()
    for key, src in _HEADER_SOURCE:
        if key in h:
            return src
    return default


def compose(reply, registry, evidence_text=''):
    """Render tokens, then bind any leftover typed count to the evidence
    (the standing net), then build the provenance ledger. Returns
    (text, ledger)."""
    try:
        text = registry.render(reply) if registry else str(reply or '')
        if evidence_text:
            try:
                from . import smarter as _sm
                text, _sw = _sm.bind_to_evidence(text, evidence_text)
            except Exception:
                pass
        led = registry.ledger(text) if registry else []
        return text, led
    except Exception:
        traceback.print_exc()
        return str(reply or ''), []


def typed_share(ledger):
    """Share of figures in an answer the model typed unsourced (0..1)."""
    led = list(ledger or [])
    if not led:
        return 0.0
    return round(sum(1 for f in led if f.get('source') == 'model_typed') / float(len(led)), 3)


def registry_for(*blocks, limit=160):
    """One registry from any mix of evidence blocks (strings; None and
    empties skipped). Sources come from each block's headers."""
    reg = Registry()
    try:
        for b in blocks:
            if b and str(b).strip():
                reg.add_text(str(b), 'view_data', limit=limit)
    except Exception:
        traceback.print_exc()
    return reg
