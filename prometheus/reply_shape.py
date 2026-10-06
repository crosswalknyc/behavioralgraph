"""Reply shape (2026-10-06, Jenna's audit item 6: "replies read like
reports, not answers").

Chat replies came back with label-style headers in capitals
("STOREFRONT SPLIT OF THE 1,147,863 TRANSACTORS"), every figure
followed by a long parenthetical definition, and the answer buried
under the scaffold. The plain-English standard already governs decks;
this applies the same bar to chat at the one exit every reply passes:

  * a line in capitals that is a heading becomes a sentence-case
    sentence (no information is dropped);
  * long parenthetical definitions (90+ characters) move out of the
    sentence into one closing block, "How these are counted", so the
    figures read first and the definitions are still there;
  * a scaffold label on its own line (MEASURED READ, ANSWER, SUMMARY,
    EVIDENCE) goes;
  * runs of blank lines collapse; figures are never touched.

Deterministic, idempotent, fail-safe (returns the input on any error).
"""
from __future__ import annotations

import re

_SCAFFOLD_RX = re.compile(
    r'^\s*(?:\*\*|#+\s*)?(?:measured read|answer(?: first)?|summary|evidence|'
    r'so what|tl;?dr|the reads?|reads?|reasoning|key takeaways?|bottom line|'
    r'headline|interpretive reads?|what this means|the numbers?|metrics)'
    r'\s*(?:\(([^)]{0,160})\))?\s*[:\-]?\s*(?:\*\*)?\s*$', re.I)
_CAPS_HEADER_RX = re.compile(r'^\s*(?:\*\*)?([A-Z0-9][A-Z0-9 ,.\'’&/+%:$\-]{11,})(?:\*\*)?\s*$')
_LONG_PAREN_RX = re.compile(r'\s*\(([^()]{90,}?)\)')
_SMALL_WORDS = {'a', 'an', 'the', 'of', 'on', 'in', 'and', 'or', 'to', 'for', 'by', 'with',
                'at', 'from', 'vs', 'per', 'this', 'that', 'these', 'those', 'us', 'tvod',
                'svod', 'avod', 'q1', 'q2', 'q3', 'q4', 'iq', 'ctr', 'roi', 'cpm', 'nfl', 'nba',
                'mlb', 'nhl', 'tv', 'hbo', 'amc', 'bet', 'espn', 'cnn', 'fx', 'tbs', 'ufc'}
_KEEP_UPPER = {'US', 'TVOD', 'SVOD', 'AVOD', 'IQ', 'CTR', 'ROI', 'CPM', 'NFL', 'NBA', 'MLB',
               'NHL', 'TV', 'HBO', 'AMC', 'BET', 'ESPN', 'CNN', 'FX', 'TBS', 'UFC', 'Q1', 'Q2',
               'Q3', 'Q4', 'YTD', 'DMA', 'CBS', 'NBC', 'ABC', 'FOX', 'MTV', 'PVOD', 'EST'}


def _sentence_case(line):
    words = line.strip().strip('*').strip().split()
    out = []
    for i, w in enumerate(words):
        core = re.sub(r'[^A-Za-z0-9]', '', w)
        if core.upper() in _KEEP_UPPER or (core.isdigit()) or re.match(r'^[\d,.$%]+$', w):
            out.append(w if core.upper() not in _KEEP_UPPER else re.sub(r'[A-Za-z]+', core.upper(), w, 1))
            continue
        lw = w.lower()
        out.append(lw.capitalize() if i == 0 else lw)
    s = ' '.join(out).strip()
    if s and s[-1] not in '.:!?':
        s += '.'
    return s


def _is_caps_header(line):
    m = _CAPS_HEADER_RX.match(line)
    if not m:
        return False
    body = m.group(1)
    letters = re.sub(r'[^A-Za-z]', '', body)
    if len(letters) < 8:
        return False
    words = [w for w in re.split(r'\s+', body.strip()) if re.search(r'[A-Za-z]', w)]
    return len(words) >= 2 and body.upper() == body


def shape(reply):
    """The plain-English shaped reply. Figures untouched."""
    text = str(reply or '')
    if not text.strip() or len(text) > 20000:
        return reply
    try:
        lines = text.split('\n')
        out_lines = []
        for ln in lines:
            if _SCAFFOLD_RX.match(ln):
                # keep any parenthetical the label carried (a window
                # qualifier) as a plain sentence
                m = _SCAFFOLD_RX.match(ln)
                if m and m.group(1):
                    q = m.group(1).strip()
                    q = q[:1].upper() + q[1:]
                    if q and q[-1] not in '.:!?':
                        q += '.'
                    # a window qualifier reads as a window line
                    if re.match(r'^(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}'
                                r'|\d{4}|\d{1,2}/\d{1,2})', q) \
                            and re.search(r'\bto\b', q) and re.search(r'\b(?:19|20)\d{2}\b', q):
                        q = 'Window: ' + q
                    out_lines.append(q)
                continue
            if _is_caps_header(ln):
                out_lines.append(_sentence_case(ln))
                continue
            out_lines.append(ln)
        body = '\n'.join(out_lines)
        # long parenthetical definitions move to one closing block
        defs = []

        def _pull(m):
            inner = m.group(1).strip()
            if re.search(r'\d', inner) and len(defs) < 8:
                lead = m.string[max(0, m.start() - 80):m.start()]
                anchor = re.findall(r'(\d[\d,]*(?:\.\d+)?%?|[A-Z][A-Za-z0-9+ ]{2,40})\s*$', lead.strip())
                defs.append((anchor[0].strip() if anchor else '', inner))
                return ''
            return m.group(0)

        body = _LONG_PAREN_RX.sub(_pull, body)
        body = re.sub(r'[ \t]+([.,;:])', r'\1', body)
        body = re.sub(r'\n{3,}', '\n\n', body).strip()
        if defs:
            block = ['', 'How these are counted:']
            for anchor, inner in defs:
                inner = inner[:1].upper() + inner[1:]
                if inner and inner[-1] not in '.!?':
                    inner += '.'
                block.append(f"- {anchor + ': ' if anchor else ''}{inner}")
            body = body + '\n' + '\n'.join(block)
        return body if body.strip() else reply
    except Exception:
        return reply


def shape_payload(raw):
    """Apply to the reply field of an analyze payload, in place."""
    if not isinstance(raw, dict):
        return raw
    v = raw.get('reply')
    if isinstance(v, str) and v.strip():
        shaped = shape(v)
        if shaped != v:
            raw['reply'] = shaped
            raw.setdefault('_shaped', True)
    return raw


def shape_finished_read(payload, held=False):
    """A finished background read lands via the status doc the widget
    polls and the thread copy, bypassing the envelope; shape it here.
    A held read carries no answer to shape. Never raises."""
    if held or not isinstance(payload, dict):
        return payload
    try:
        return shape_payload(payload)
    except Exception:
        return payload
