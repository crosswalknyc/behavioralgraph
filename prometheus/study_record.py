"""A question about a published study is answered from the study.

Cara Dorfman at Amazon, 2026-11-16. She was checking the Amazon vs
direct issue of The Read against her own numbers and asked how its
figures were built: which rule makes a subscription active, what the
churn base is, whether bundles sit in a monthly-only curve, how many
starts each service had. Prometheus ran fresh reads on her questions and
the reads drew figures the issue never carried (month-start account
counts that implied a different churn rate, a monthly-only curve,
survival past day 90). She rebuilt the churn from them and it did not
tie. A published study has one set of figures, and they are on record.

This lane answers from that record and from nothing else:

  * the record is the decision of record the issue banked (its figures,
    their definitions, the per-service rows), packaged with the code in
    prometheus/study_records/ so the answer needs no store, plus any
    other decision of record the ledger holds;
  * each numbered part of the message is answered on its own;
  * a definition is the record's own definition, said in the fixed words
    of prometheus.study_statements (no model writes any of it);
  * a per-service table is typed out as text, only the services and the
    columns that were asked for;
  * where the record does not hold what was asked, the answer says the
    published study does not carry it;
  * a figure the asker quotes that the record does not hold is named as
    not a figure from the study;
  * nothing is launched, drafted, priced or banked.

match(text, history)   the record the ask is about, or None
respond(text, history) the answer payload, or None when the ask is not
                       about a record (or is a general ask the stored
                       summary already answers)

Never raises.
"""
from __future__ import annotations

import json
import os
import re

from . import study_statements as _st

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'study_records')
_PACKS = {'cache': None}

# the words that say the ask is about a published study
_STUDY_RX = re.compile(
    r"\b(?:published|that|this|the|same|your|their|our)\s+(?:same\s+)?(?:published\s+)?(?:[\w+'-]+\s+){0,4}?"
    r"(?:study|white\s+paper)\b|\bstudy'?s\b|\bin\s+the\s+read\b|\bissue\s+of\s+the\s+read\b|\bthe\s+read\s+issue\b|"
    r"\bpublished\s+(?:issue|report|newsletter|figures?|numbers?|curves?|rates?)\b", re.I)
_COUNT_RX = re.compile(r"(?<![\d,.$])\d{1,3}(?:,\d{3})+(?![\d,]*\.\d)(?!\d)")
_PCT_RX = re.compile(r"(?<![\d.,$])(\d{1,3}\.\d{1,2})\s?%")
_NUM_ANY_RX = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_ITEM_RX = re.compile(r"(?m)^[ \t]*(\d{1,2})[.)][ \t]+(?=\S)")
_NO_BUILD_SENT_RX = re.compile(
    r"(?:(?<=^)|(?<=[.!?\n]))\s*(?:(?:please\s+)?(?:do\s+not|don'?t|dont)\s+(?:build|run|start|queue)\s+[^.!?\n]{0,40}[.!]|"
    r"text\s+table\s+only\s*[.!]?|give\s+(?:me\s+)?a\s+text\s+table\s*[.!]?)", re.I)


# ----------------------------------------------------------------- records
def _load_packs():
    if _PACKS['cache'] is not None:
        return _PACKS['cache']
    out = []
    try:
        for name in sorted(os.listdir(_DIR)):
            if not name.endswith('.json'):
                continue
            try:
                with open(os.path.join(_DIR, name), encoding='utf-8') as fh:
                    doc = json.load(fh)
                if isinstance(doc, dict) and doc.get('id') and doc.get('metrics'):
                    doc['sheet'] = _st.SHEETS.get(doc['id']) or {}
                    out.append(doc)
            except Exception as e:
                print(f"[study-record] pack {name} not loaded: {e}")
    except Exception as e:
        print(f"[study-record] packs not loaded: {e}")
    _PACKS['cache'] = out
    return out


def _ledger_records():
    """Decisions of record the ledger holds beyond the packaged ones, in
    the same shape (no table, no sheet): subject, title, aliases, window,
    metrics with their definitions, the stored summary."""
    out = []
    try:
        import insights_ledger as il
        doc = il._load_index() or {}
        have = {str(p.get('subject') or '').strip().lower() for p in _load_packs()}
        for sub in (doc.get('subjects') or {}).values():
            if not isinstance(sub, dict):
                continue
            rec = None
            for e in (sub.get('entries') or []):
                if isinstance(e, dict) and il.is_record_entry(e) and not e.get('retired') and e.get('metrics'):
                    rec = e
                    break
            if not rec or str(rec.get('subject') or '').strip().lower() in have:
                continue
            out.append({
                'id': str(rec.get('route') or '')[len(getattr(il, 'RECORD_ROUTE_PREFIX', 'decision_of_record_')):] or str(rec.get('k') or ''),
                'subject': str(rec.get('subject') or ''), 'title': str(rec.get('q') or '').rstrip('.'),
                'short_name': 'the published study', 'family': str(rec.get('family') or ''),
                'window': {'start': rec.get('ws') or '', 'end': rec.get('we') or '', 'label': rec.get('wl') or ''},
                'metrics': [m for m in (rec.get('metrics') or []) if isinstance(m, dict)],
                'reply': str(rec.get('reply') or ''), 'aliases': list(sub.get('aliases') or []),
                'questions': [str(x.get('q') or '') for x in (sub.get('entries') or []) if isinstance(x, dict) and il.is_record_entry(x)],
                'followups': list(rec.get('followups') or []), 'services': [], 'rows': {}, 'columns': [], 'paths': [], 'sheet': {}})
    except Exception as e:
        print(f"[study-record] ledger records not read: {e}")
    return out


def records(with_ledger=False):
    recs = list(_load_packs())
    if with_ledger:
        recs.extend(_ledger_records())
    return recs


def _fold(text):
    t = str(text or '').replace('\u2019', "'").replace('\u2018', "'").replace('\u201c', '"').replace('\u201d', '"')
    return ' '.join(t.split())


def _fmt(value, unit):
    try:
        if unit in ('count', 'subscriptions', 'people', 'accounts'):
            return f"{int(round(float(value))):,}"
        if unit == 'usd':
            return f"${float(value):,.2f}"
        v = float(value)
        return f"{int(v):,}" if (v == int(v) and abs(v) >= 1000) else (f"{v:g}" if v != int(v) else f"{v:.1f}")
    except Exception:
        return str(value)


def _record_numbers(rec):
    """Every figure the record holds, as the strings a reader would type."""
    if rec.get('_numbers') is not None:
        return rec['_numbers']
    nums = set()

    def add(v):
        try:
            f = float(str(v).replace(',', ''))
        except Exception:
            return
        nums.add(f"{f:g}")
        nums.add(f"{f:.1f}")
        nums.add(f"{f:.2f}")
        if f == int(f):
            nums.add(str(int(f)))
            nums.add(f"{int(f):,}")
        nums.add(str(int(round(f))))

    for m in rec.get('metrics') or []:
        add(m.get('value'))
        for n in _NUM_ANY_RX.findall(str(m.get('definition') or '')):
            add(n)
    for path_rows in (rec.get('rows') or {}).values():
        for row in path_rows.values():
            for v in row.values():
                add(v)
    for n in _NUM_ANY_RX.findall(str(rec.get('reply') or '')):
        add(n)
    rec['_numbers'] = nums
    return nums


def _headline_numbers(rec):
    """The figures of the study as a whole: its metrics and the
    all-services row, without the per-service rows."""
    if rec.get('_headline') is not None:
        return rec['_headline']
    slim = {'metrics': rec.get('metrics'), 'reply': rec.get('reply'),
            'rows': {pk: {k: v for k, v in (rows or {}).items() if k == rec.get('all_label')}
                     for pk, rows in (rec.get('rows') or {}).items()}}
    rec['_headline'] = _record_numbers(slim)
    return rec['_headline']


def _strong_counts(rec):
    """The record's own large counts: one of them in an ask names the study."""
    if rec.get('_counts') is not None:
        return rec['_counts']
    out = set()
    for m in rec.get('metrics') or []:
        try:
            v = float(m.get('value'))
            if v >= 10000 and v == int(v) and str(m.get('unit') or '') != 'usd':
                out.add(f"{int(v):,}")
        except Exception:
            pass
    for path_rows in (rec.get('rows') or {}).values():
        for row in path_rows.values():
            try:
                out.add(f"{int(row.get('starts')):,}")
            except Exception:
                pass
    rec['_counts'] = out
    return out


def _names_hit(rec, t):
    sheet = rec.get('sheet') or {}
    low = t.lower()
    for rx in sheet.get('names') or ():
        if re.search(rx, low, re.I):
            ctx = sheet.get('context')
            if not ctx or re.search(ctx, low, re.I) or _STUDY_RX.search(t):
                return True
    title = _fold(rec.get('title') or '').lower()
    if title and len(title) >= 24 and title in low:
        return True
    if not sheet:                          # a ledger record: its longer aliases, with the study said
        for a in rec.get('aliases') or []:
            al = _fold(a).lower()
            if len(al.split()) >= 3 and al in low and _STUDY_RX.search(t):
                return True
    return False


def _figures_hit(rec, t):
    sheet = rec.get('sheet') or {}
    if any(c in t for c in _strong_counts(rec)):
        return True
    if any(r in t for r in sheet.get('rounded') or ()):
        return True
    ctx = sheet.get('context')
    if ctx and not re.search(ctx, t, re.I):
        return False
    for a, b in sheet.get('pairs') or ():
        if re.search(r"(?<![\d.])" + re.escape(a) + r"(?![\d])", t) and re.search(r"(?<![\d.])" + re.escape(b) + r"(?![\d])", t):
            return True
    return False


def _turn_text(t):
    return str((t or {}).get('text') or (t or {}).get('content') or '')


def _history_record(recs, history, lookback=40):
    """The record this thread has been about: the reader named it, or an
    answer here came from it."""
    turns = [t for t in (history or []) if isinstance(t, dict)][-lookback:]
    for t in reversed(turns):
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        txt = _fold(_turn_text(t))
        for rec in recs:
            if str(t.get('role') or '') == 'user':
                if _names_hit(rec, txt) or any(c in txt for c in _strong_counts(rec)):
                    return rec
                continue
            sr = meta.get('study_record') if isinstance(meta.get('study_record'), dict) else {}
            if sr.get('record') == rec.get('id') or str(meta.get('record') or '').endswith(str(rec.get('id') or '\x00')):
                return rec
            subj = str(rec.get('subject') or '')
            if subj and (str(meta.get('subject') or '') == subj or txt.startswith('On ' + subj + ':')):
                return rec
            sn = str(rec.get('short_name') or '')
            if sn and sn != 'the published study' and sn.lower() in txt.lower():
                return rec
    return None


def _last_agent_is_study(history, recs=()):
    """The record id when the answer just before this ask came from a
    record (its mark on the turn, or the lane's own opening words)."""
    for t in reversed([t for t in (history or []) if isinstance(t, dict)]):
        if str(t.get('role') or '') == 'user':
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        sr = meta.get('study_record')
        if isinstance(sr, dict) and sr.get('record'):
            return sr.get('record')
        txt = _fold(_turn_text(t))
        for rec in recs:
            sn = str(rec.get('short_name') or '')
            if sn and sn != 'the published study' and txt.startswith('From ' + sn):
                return rec.get('id')
        return None
    return None


# "the published study", "that published issue": a definite reference to a study of ours
_PUBLISHED_RX = re.compile(
    r"\b(?:the|that|this|your|their|our|same)\s+(?:same\s+)?published\s+(?:[\w+'-]+\s+){0,4}?(?:study|issue|white\s+paper|report)\b", re.I)
UNKNOWN = ("I do not hold a published study that matches that, so I cannot answer from its record. "
           "Give me the study's title and I will look again.")


def unknown_study(text, no_build=False):
    """The reply for a question about a published study no record matches:
    one plain line, never a read on whatever the words happen to name."""
    try:
        if not _PUBLISHED_RX.search(_fold(text)):
            return None
        reply = UNKNOWN + (' Nothing was built for this.' if no_build else '')
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False,
                'deck_angle': None, 'profile': None,
                'study_record': {'record': None, 'subject': None, 'parts': 0, 'topics': ['unknown_study'], 'carried': ['no']}}
    except Exception:
        return None


def names_study(text):
    return bool(_STUDY_RX.search(_fold(text)))


def _match(text, history=None):
    """(record, how): how is 'named' when the ask itself names the study
    (its title, its usual names, a figure only it prints), 'thread' when
    the ask says "the study" or follows an answer from it in a thread
    that has been about it. (None, '') otherwise."""
    try:
        t = _fold(text)
        if not t:
            return None, ''
        study = bool(_STUDY_RX.search(t))
        recs = records(with_ledger=study)
        for rec in recs:
            if _names_hit(rec, t) or _figures_hit(rec, t):
                return rec, 'named'
        if study:
            rec = _history_record(recs, history)
            if rec:
                return rec, 'thread'
        rid = _last_agent_is_study(history, recs)
        if rid and len(t) <= 600:
            for rec in recs:
                if rec.get('id') == rid and (_topics_for(rec, t) or _services_named(rec, t)):
                    return rec, 'thread'
        return None, ''
    except Exception as e:
        print(f"[study-record] match failed: {e}")
        return None, ''


def match(text, history=None):
    """The record the ask is about, or None."""
    return _match(text, history)[0]


# ------------------------------------------------------------------- parts
def parts_of(text):
    """The message as its parts: [{'n', 'title', 'text'}]. A numbered list
    is its items (a short opening line is the item's title); paragraphs
    split by a blank line are parts; otherwise the message is one part."""
    raw = str(text or '').replace('\r', '')
    marks = list(_ITEM_RX.finditer(raw))
    seq = [int(m.group(1)) for m in marks]
    out = []
    if len(marks) >= 2 and seq[:2] == [1, 2]:
        for i, m in enumerate(marks):
            body = raw[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(raw))].strip()
            out.append({'n': str(seq[i]), **_titled(body)})
        lead = raw[:marks[0].start()].strip()
        if lead and len(lead.split()) >= 6:
            out.insert(0, {'n': '', 'title': '', 'text': lead})
        return out
    paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if len(paras) >= 2:
        return [{'n': str(i + 1), 'title': '', 'text': p} for i, p in enumerate(paras)]
    return [{'n': '', 'title': '', 'text': raw.strip()}]


def _titled(body):
    """A numbered item whose first line (or first short sentence) is a
    label: 'Active status', 'What "via Amazon" includes.'"""
    cleaned = _NO_BUILD_SENT_RX.sub(' ', body).strip()
    first, _, rest = cleaned.partition('\n')
    first = first.strip()
    if rest.strip() and first and len(first.split()) <= 8 and not first.endswith('?'):
        return {'title': first.rstrip('.').strip(), 'text': body}
    m = re.match(r"^([^.?!\n]{3,60})\.\s+(?=\S)", cleaned)
    if m and len(m.group(1).split()) <= 6 and not re.match(r"(?i)^(?:for|in|of|how|what|when|does|do|is|are|give|type|list)\b", m.group(1).strip() + ' ') \
            or (m and re.match(r'(?i)^what\s+\W?(?:via\s+amazon|direct)\W?\s+includes$', m.group(1).strip())):
        return {'title': m.group(1).strip(), 'text': body}
    return {'title': '', 'text': body}


# ------------------------------------------------------------------ topics
def _topics_for(rec, part_text):
    sheet = rec.get('sheet') or {}
    t = _fold(part_text)
    hits = []
    for n, topic in enumerate(sheet.get('topics') or ()):
        try:
            m = re.search(topic['when'], t, re.I)
        except re.error:
            continue
        if m:
            hits.append((m.start(), n, topic))
    drop = set()
    for _pos, _n, topic in hits:
        drop.update(topic.get('drops') or ())
    # in the order the part asks them
    return [x for _pos, _n, x in sorted(hits, key=lambda h: (h[0], h[1])) if x['id'] not in drop]


def _services_named(rec, text):
    low = ' ' + _fold(text).lower() + ' '
    out = []
    for s in rec.get('services') or []:
        if re.search(r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9+])", low):
            out.append(s)
    return out


def _values(rec):
    """The placeholder values a statement is filled from: every column of
    the all-services row on both paths, formatted, and the window."""
    if rec.get('_values') is not None:
        return rec['_values']
    vals = {}
    rows = rec.get('rows') or {}
    units = {c['key']: c.get('unit') for c in rec.get('columns') or []}
    allk = rec.get('all_label') or ''
    for pk, suffix in (('amazon', 'a'), ('direct', 'd')):
        row = (rows.get(pk) or {}).get(allk) or {}
        for k, v in row.items():
            vals[f"{k}_{suffix}"] = _fmt(v, units.get(k) or '')
        try:
            vals[f"curve_{suffix}"] = '/'.join(str(int(round(float(row[d])))) for d in ('d30', 'd60', 'd90'))
        except Exception:
            vals[f"curve_{suffix}"] = ''
    win = rec.get('window') or {}
    vals['window'] = str(win.get('label') or '')
    try:
        from datetime import date
        y, m, _d = (int(x) for x in str(win.get('start') or '').split('-'))
        vals['window_start_month'] = date(y, m, 1).strftime('%B %Y')
    except Exception:
        vals['window_start_month'] = 'the window opened'
    rec['_values'] = vals
    return vals


class _Safe(dict):
    def __missing__(self, key):
        return '{' + key + '}'


_COLUMN_WORDS = (
    ('starts', r"\bstarts\b|\bnumber\s+of\s+(?:new\s+)?(?:signups|subscriptions)\b"),
    ('churn', r"\bchurn\b"),
    ('samplers', r"\bone\s+title\b|\bsamplers?\b"),
    ('m2m', r"\bmonth[- ]to[- ]month\b"),
    ('annual', r"\bannual\b|\bprepaid\b|\bbundles?\b"),
    ('months', r"\bmonths\s+kept\b|\bfirst-?year\s+months\b"),
    ('price', r"\blist\s+price\b|\bprice\b"),
    ('value', r"\bfirst-?year\s+value\b|\bworth\b|\bltv\b"),
    ('switch', r"\bstart\s+another\b|\bswitch\w*\b|\bcancell?ers\s+go\b|\bgo\s+next\b"),
    ('winback', r"\bwin-?backs?\b|\bcomes?\s+back\b|\bback\s+at\s+the\s+same\b"),
    ('share', r"\bshare\s+of\s+(?:the\s+|each\s+)?path\b"),
)
_DAY_LIST_RX = re.compile(r"\bday[- ]?\s*(\d{2,3}(?:\s*(?:,|and|&|/)\s*(?:day\s+)?\d{2,3})*(?:\s*,?\s*(?:and|&)\s*(?:day\s+)?\d{2,3})?)", re.I)
_SURVIVAL_RX = re.compile(r"\bsurviv\w+|\bretention\b|\bstill\s+active\b|\bretained\b", re.I)
_ALL_SERVICES_RX = re.compile(r"\b(?:ten|10|all|each\s+of\s+the)\s+services\b|\beach\s+service\b|\bper[- ]service\s+table\b|\bevery\s+service\b", re.I)


def _columns_asked(rec, text):
    t = _fold(text)
    have = {c['key'] for c in rec.get('columns') or []}
    cols = []
    days = []
    for m in _DAY_LIST_RX.finditer(t):
        days.extend(int(x) for x in re.findall(r"\d{2,3}", m.group(1)))
    day_cols = [f"d{d}" for d in (30, 60, 90) if d in days]
    if not day_cols and _SURVIVAL_RX.search(t):
        day_cols = ['d30', 'd60', 'd90']
    for key, rx in _COLUMN_WORDS:
        if key in have and re.search(rx, t, re.I):
            cols.append(key)
    order = [c['key'] for c in rec.get('columns') or []]
    picked = [k for k in order if k in set(cols) | set(day_cols)]
    return picked or [k for k in ('starts', 'churn', 'd30', 'd60', 'd90') if k in have]


def table(rec, services=None, columns=None, with_all=None):
    """The per-service rows as a text table: one line per service and path."""
    cols = [c for c in (rec.get('columns') or []) if c['key'] in (columns or [])]
    svcs = [s for s in (rec.get('services') or []) if not services or s in services]
    paths = rec.get('paths') or []
    if not cols or not svcs or not paths:
        return ''
    lines = [' | '.join(['Service', 'Path'] + [c['label'] for c in cols])]

    def cell(v, unit):
        s = _fmt(v, unit)
        return s + '%' if unit == 'pct' else s

    names = list(svcs)
    if with_all if with_all is not None else len(svcs) == len(rec.get('services') or []):
        names.append(rec.get('all_label') or '')
    for s in names:
        for p in paths:
            row = ((rec.get('rows') or {}).get(p['key']) or {}).get(s)
            if not row:
                continue
            lines.append(' | '.join([s, p['label']] + [cell(row.get(c['key']), c.get('unit')) for c in cols]))
    return '\n'.join(lines)


def _render_topic(rec, topic, part_text, state):
    vals = _Safe(_values(rec))
    kind = topic.get('kind') or ''
    if kind == 'table':
        named = _services_named(rec, part_text)
        services = None if (_ALL_SERVICES_RX.search(_fold(part_text)) or not named) else named
        cols = _columns_asked(rec, part_text)
        tbl = table(rec, services=services, columns=cols)
        if not tbl:
            return ''
        win = (rec.get('window') or {}).get('label') or ''
        head = "From the published study" + (f", {win}" if win else '') + ", new subscriptions by service and signup path:"
        notes = []
        t = _fold(part_text)
        if 'annual' in cols and re.search(r"\bprepaid\b", t, re.I):
            notes.append("The plan column is annual plans and bundles. The study has no separate prepaid line.")
        if any(c in cols for c in ('d30', 'd60', 'd90')):
            notes.append("Day 30, 60 and 90 are the share of new starts still active that many days after each subscription's own day 0.")
        if 'starts' in cols:
            state['starts_table'] = state.get('part') or ''
        return '\n'.join([head, tbl] + notes)
    text = str(topic.get('text') or '')
    if '{service}' in text:
        named = _services_named(rec, part_text)
        vals['service'] = named[0] if named else 'service'
    out = text.format_map(vals)
    if kind == 'sample':
        named = _services_named(rec, part_text)
        t = _fold(part_text)
        rows = rec.get('rows') or {}
        if _ALL_SERVICES_RX.search(t) or re.search(r"\beach\s+of\s+the\s+ten\b|\beach\s+path\b", t, re.I) and not named:
            if state.get('starts_table'):
                out += f" The projected starts for each service and path are in the table under part {state['starts_table']}."
            else:
                tbl = table(rec, columns=['starts'])
                if tbl:
                    out += "\nProjected starts by service and path:\n" + tbl
        elif named and len(named) <= 4:
            bits = []
            for s in named:
                a = ((rows.get('amazon') or {}).get(s) or {}).get('starts')
                d = ((rows.get('direct') or {}).get(s) or {}).get('starts')
                if a and d:
                    bits.append(f"{s}: {_fmt(a, 'count')} on the Amazon path and {_fmt(d, 'count')} direct")
            if bits:
                out += " Projected starts for " + '; '.join(bits) + '.'
    return out


_STOP = frozenset((
    'the', 'a', 'an', 'of', 'in', 'on', 'for', 'to', 'and', 'or', 'is', 'are', 'was', 'were', 'what', 'how', 'many', 'much',
    'that', 'this', 'study', 'published', 'do', 'does', 'did', 'not', 'build', 'anything', 'give', 'me', 'text', 'table',
    'only', 'with', 'by', 'at', 'as', 'it', 'its', 'their', 'your', 'you', 'we', 'i', 'from', 'through', 'via', 'vs',
    'versus', 'amazon', 'direct', 'path', 'paths', 'each', 'both', 'which', 'who', 'they', 'them', 'new', 'same', 'be'))


def _toks(s):
    out = set()
    for w in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", _fold(s).lower()):
        if w in _STOP or len(w) < 3:
            continue
        out.add(w[:-1] if w.endswith('s') and len(w) > 4 else w)
    return out


def _metric_lines(rec, part_text, limit=4):
    """The record's own metrics that the part's words name (two or more
    of a metric label's content words), as '- label: value. definition'."""
    want = _toks(part_text)
    scored = []
    for i, m in enumerate(rec.get('metrics') or []):
        lab = _toks(m.get('label') or '')
        if not lab:
            continue
        hit = want & lab
        if len(hit) >= 2 or (len(lab) <= 2 and hit == lab):
            scored.append((-len(hit), i, m))
    lines = []
    for _n, _i, m in sorted(scored)[:limit]:
        unit = str(m.get('unit') or '')
        v = _fmt(m.get('value'), 'count' if unit in ('subscriptions', 'people', 'accounts') else unit)
        if unit == 'pct':
            v += '%'
        elif unit == 'x':
            v += 'x'
        elif unit in ('months', 'pct_pts'):
            v += ' months' if unit == 'months' else ' points'
        d = str(m.get('definition') or '').strip().rstrip('.')
        lines.append(f"- {m.get('label')}: {v}." + (f" {d[0].upper() + d[1:]}." if d else ''))
    return lines


def _foreign_figures(rec, part_text):
    """Counts and percentages the part quotes that the record does not hold."""
    have = _record_numbers(rec)
    t = _fold(part_text)
    # a percentage is checked against the headline figures unless the part names a service
    head = have if (_services_named(rec, part_text) or not rec.get('rows')) else _headline_numbers(rec)
    out = []
    for m in _COUNT_RX.finditer(t):
        s = m.group(0)
        if s not in have and s.replace(',', '') not in have and s not in out:
            out.append(s)
    for m in _PCT_RX.finditer(t):
        s = m.group(1)
        try:
            norm = f"{float(s):g}"
        except Exception:
            norm = s
        if s not in head and norm not in head and (s + '%') not in out:
            out.append(s + '%')
    return out


def _join(items):
    items = list(items)
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]


def _is_general(rec, text):
    """A general ask the stored summary answers ("churn through Amazon
    Channels vs direct"): one of the record's natural asks, or a short
    question that names its measures and nothing the sheet treats apart."""
    try:
        import insights_ledger as il
        qn = il.normalize_question(text)
        if qn and qn in {il.normalize_question(q) for q in rec.get('questions') or []}:
            return True
    except Exception:
        pass
    return len(_fold(text).split()) <= 24 and bool(_metric_lines(rec, text, limit=1))


def respond(text, history=None, *, record=None, no_build=None):
    """The answer payload for an ask about a published study, or None.

    None when the ask is about no record, and when it is a single general
    ask the stored summary answers (the ledger's own replay keeps that,
    with its file and its chips)."""
    try:
        if no_build is None:
            try:
                from . import no_build as _nb
                no_build = _nb.asked(text)
            except Exception:
                no_build = False
        rec, how = (record, 'named') if record else _match(text, history)
        if not rec:
            return unknown_study(text, no_build=bool(no_build))
        parts = parts_of(text)
        state = {}
        blocks, topics_used, carried = [], [], []
        any_topic = False
        for p in parts:
            state['part'] = p.get('n') or ''
            ptxt = p.get('text') or ''
            hits = _topics_for(rec, ptxt)
            if not hits and (rec.get('rows') and _services_named(rec, ptxt)) and (
                    _SURVIVAL_RX.search(ptxt) or _DAY_LIST_RX.search(ptxt) or any(re.search(rx, _fold(ptxt), re.I) for _k, rx in _COLUMN_WORDS)):
                hits = [x for x in (rec.get('sheet') or {}).get('topics') or () if x.get('kind') == 'table']
            lines = []
            for topic in hits:
                out = _render_topic(rec, topic, ptxt, state)
                if out:
                    lines.append(out)
                    topics_used.append(topic['id'])
                    carried.append(topic.get('carried') or 'yes')
            if hits:
                any_topic = True
            if not lines:
                ml = _metric_lines(rec, ptxt)
                if ml:
                    lines.extend(ml)
                    topics_used.append('metrics')
                    carried.append('yes')
                else:
                    lines.append(_st.NOT_MATCHED)
                    topics_used.append('not_matched')
                    carried.append('no')
            foreign = _foreign_figures(rec, ptxt)
            if foreign:
                lines.append(_st.NOT_FIGURES.format(figures=_join(foreign)))
            # the sheet's own label for what was answered; the reader's label only when nothing matched
            title = (hits[0].get('title') if hits else '') or p.get('title') or ''
            blocks.append({'n': p.get('n') or '', 'title': title, 'lines': lines})
        if len(parts) == 1 and not any_topic and not no_build and _is_general(rec, text):
            return None                       # the stored summary answers a general ask
        if how == 'thread' and not no_build and all(x == 'not_matched' for x in topics_used):
            return None                       # the thread is about the study; this ask is about something else
        win = (rec.get('window') or {}).get('label') or ''
        name = str(rec.get('short_name') or 'the published study')
        head = f"From {name}" + (f" ({win})" if win else '') + '.'
        if no_build:
            head += ' Nothing was built for this.'
        out = [head, '']
        multi = len(blocks) > 1
        for b in blocks:
            if multi:
                label = (b['n'] + '. ' if b['n'] else '') + (b['title'] or '')
                if label.strip():
                    out.append(label.strip())
            out.extend(b['lines'])
            out.append('')
        reply = '\n'.join(out).strip()
        chips = [c for c in (rec.get('followups') or []) if 'email' not in c.lower() and 'file' not in c.lower()][:2]
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': chips,
                'offer_deck': False, 'deck_angle': None, 'profile': None,
                'study_record': {'record': rec.get('id'), 'subject': rec.get('subject'), 'parts': len(blocks),
                                 'topics': topics_used, 'carried': carried}}
    except Exception as e:
        print(f"[study-record] respond failed: {e}")
        return None


__all__ = ['match', 'respond', 'records', 'parts_of', 'table', 'names_study', 'unknown_study']
