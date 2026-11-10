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
# A section label glued to the end of a sentence line (2026-10-26, Emmet
# Riley: "...high affinity audience. WHAT I HAVE IN FRONT OF ME RIGHT NOW"
# then a bullet; "...single touch. ONE THING TO SETTLE"). Two or more
# words in capitals after sentence punctuation, nothing after them.
_INLINE_CAPS_TAIL_RX = re.compile(
    r'^(?P<head>.*?[.!?:;])\s+(?P<label>[A-Z][A-Z0-9\'’&/+\-]*(?:[ ,]+[A-Z0-9][A-Z0-9\'’&/+\-]*){1,9})\s*:?\s*$')
# A label naming the move rather than carrying a finding ("WHAT I HAVE IN
# FRONT OF ME RIGHT NOW", "WHAT THE UVA PULL WILL CARRY", "ONE THING TO
# SETTLE", "THE READ", "BOTTOM LINE"). These go; a capitals line that
# states a finding ("SODA IS A CONQUEST CATEGORY HERE") is sentence-cased.
_MOVE_LABEL_RX = re.compile(
    r"^(?:WHAT\b|ONE (?:THING|QUESTION|MORE|NOTE|LAST|CALL)\b|TWO THINGS|THREE THINGS|"
    r"THE (?:ONE|SHORT|LONG|QUICK|READ|ASK|CASE|PLAN|ANSWER|NUMBERS?|TAKEAWAY|BOTTOM|OPEN|NEXT|CALL|RECOMMENDATION|UPSHOT|GIST|POINT|DETAIL|SETUP|SET-UP|WINDOW|COST|PRICE)\b|"
    r"HOW (?:TO|THIS|IT|I|WE|THAT)\b|WHY (?:THIS|IT|THAT)\b|WHERE (?:THIS|IT|TO|WE|THAT)\b|WHEN\b|WHO (?:THIS|IT|THEY|COUNTS)\b|"
    r"NEXT (?:STEPS?|UP)\b|BOTTOM LINE|IN SHORT|IN SUM|SO WHAT|TO SETTLE|OPEN QUESTIONS?|STILL OPEN|"
    r"KEY (?:POINTS?|TAKEAWAYS?|NUMBERS?|FACTS?)\b|BEFORE (?:WE|YOU|I)\b|ON (?:QUALIFICATION|THE WINDOW|THE COST|COST|PRICE|THE PULL|SIZE|SIZING)\b|"
    r"MY (?:READ|TAKE|CALL)\b|YOUR CALL|UP TO YOU|RIGHT NOW\b|IN FRONT OF ME)", re.I)
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


def _is_move_label(label):
    """A capitals label that names the move, not a finding."""
    s = ' '.join(str(label or '').strip().strip('*').split()).strip(' :-')
    return bool(s) and bool(_MOVE_LABEL_RX.match(s))


def _caps_words(label):
    s = str(label or '')
    letters = re.sub(r'[^A-Za-z]', '', s)
    words = [w for w in re.split(r'[\s,]+', s.strip()) if re.search(r'[A-Za-z]', w)]
    return words if (len(letters) >= 8 and len(words) >= 2 and s.upper() == s) else []


def split_inline_label(line):
    """('sentence text', 'LABEL') when a section label in capitals rides
    the end of a sentence line; (line, '') otherwise. Figures, acronyms
    and a short all-caps name in running text are never a label (two or
    more words, eight or more letters, after the sentence's own stop)."""
    m = _INLINE_CAPS_TAIL_RX.match(str(line or ''))
    if not m:
        return line, ''
    label = m.group('label').strip()
    if not _caps_words(label):
        return line, ''
    # a trailing acronym pair in a sentence ("... on CBS and NBC.") ends
    # with the sentence's stop and never matches; here the stop is BEFORE
    # the label, so the capitals are a heading glued to the line
    return m.group('head').rstrip(), label


def shape(reply):
    """The plain-English shaped reply. Figures untouched."""
    text = str(reply or '')
    if not text.strip() or len(text) > 20000:
        return reply
    try:
        lines = text.split('\n')
        # a label glued to the end of a sentence line becomes its own line
        # first, so the header rules below see it (2026-10-26)
        split_lines = []
        for ln in lines:
            head, label = split_inline_label(ln)
            if label:
                split_lines.append(head)
                split_lines.append(label)
            else:
                split_lines.append(ln)
        lines = split_lines
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
                if _is_move_label(ln):
                    # a label naming the move ("WHAT I HAVE IN FRONT OF ME
                    # RIGHT NOW", "ONE THING TO SETTLE") carries nothing a
                    # reader needs: it goes, and the paragraph break stays
                    if out_lines and out_lines[-1].strip():
                        out_lines.append('')
                    continue
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
            bullets = []
            for anchor, inner in defs:
                inner = inner[:1].upper() + inner[1:]
                if inner and inner[-1] not in '.!?':
                    inner += '.'
                bullets.append(f"- {anchor + ': ' if anchor else ''}{inner}")
            if 'How these are counted:' in body:
                # one block per reply: slot the definitions right under
                # the header the methodology layer already wrote
                head, tail = body.split('How these are counted:', 1)
                body = head + 'How these are counted:\n' + '\n'.join(bullets) + tail
            else:
                body = body + '\n' + '\n'.join(['', 'How these are counted:'] + bullets)
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


# A read answers; it never queues anything. Jay Patel (Amazon), Godzilla
# Minus One, 2026-10-30: "Start the June 1 2024 to June 1 2025 build now"
# reached the read job, which wrote "The ... build is started and in the
# queue at position 1 ... at 306 usd". Nothing was queued and the price
# was typed. Any sentence in a read that claims a build was started,
# queued, approved, or priced is dropped, and one plain line tells the
# reader how to actually order it.
_FALSE_BUILD_CLAIM_RX = re.compile(
    r"\b(?:build|profile|pull|run)\b[^.\n]{0,80}?\b(?:is|was|has been|are|were|got|now|already)\s+"
    r"(?:started|queued|kicked off|launched|underway|in (?:the )?queue|in line|running|approved|"
    r"accepted|scheduled|booked|confirmed)\b"
    r"|\b(?:in the queue at position|at position \d|number \d+ in line|quoted and accepted)\b"
    r"|^\W*(?:build|total) cost:?\s*\$?\d", re.I | re.M)
_ORDER_LINE = "No build has been started from this answer. To build it, say: build this profile."


def strip_false_build_claims(text):
    """Drop every line or sentence in a read that claims a build was
    started, queued, or priced; append the order line once. Text with
    no such claim comes back untouched."""
    t = str(text or '')
    if not t or not _FALSE_BUILD_CLAIM_RX.search(t):
        return t
    kept = []
    changed = False
    for line in t.split('\n'):
        if _FALSE_BUILD_CLAIM_RX.search(line):
            # a bullet or label line goes whole; a prose line loses the sentence
            parts = re.split(r"(?<=[.!?])\s+", line)
            keep = [x for x in parts if not _FALSE_BUILD_CLAIM_RX.search(x)]
            if len(keep) != len(parts):
                changed = True
            if keep and not re.match(r"^\W*[-*\u2022]\s|^\W*\w[^:]{0,40}:\s", line):
                kept.append(' '.join(keep))
            elif keep and len(keep) == len(parts):
                kept.append(line)
        else:
            kept.append(line)
    out = re.sub(r"\n{3,}", "\n\n", '\n'.join(kept)).strip()
    if changed or out != t.strip():
        if _ORDER_LINE not in out:
            out = (out + "\n\n" + _ORDER_LINE).strip()
    return out


# A read answers from the data; it never puts anything on a dashboard
# surface. Sony Television, 2026-11-10: a reasoned read on My Life with
# the Walter Boys told the seat "It is in the picker now: ... is built as
# a Digital Journey and shows under Select a journey", and, asked again,
# "It is on the dashboard now: ... sits as a Digital Journey card", while
# no journey-iq/ object and no journey job existed. A sentence that says
# an artifact sits in a picker, dropdown, tab or card may be printed only
# after the registry object exists (the payload carries its key); a read
# that has none says the read is here in the chat and offers the real
# build. The same discipline covers the Select Profile dropdown and the
# Twins tab.
_ARTIFACT_CLAIMS = (
    ('journey', re.compile(
        r"\b(?:in the picker|(?:under|in) select a journey|select a journey|(?:is )?built as a digital journey|"
        r"(?:sits|shows|appears|is) (?:now )?(?:as|under|in|on) (?:a |the |your )?digital journey (?:card|tab|picker|list|shelf)|"
        r"digital journey card|(?:is|are) (?:now )?(?:live|up|sitting|showing) (?:in|on) (?:the |your )?digital journey (?:iq )?tab|"
        r"(?:in|on) your journey (?:picker|list|shelf|tab))\b", re.I)),
    ('profile', re.compile(
        r"\b(?:(?:in|on) (?:the |your )?select profile dropdown|select profile dropdown|(?:in|on) your (?:profile )?dropdown|"
        r"(?:is|are) (?:now )?(?:live|up|sitting|showing) (?:in|on) (?:the |your )?profile iq tab|added as a profile|"
        r"sits as a profile(?: iq)? card|(?:in|on) your profile (?:picker|list|shelf))\b", re.I)),
    ('twin', re.compile(
        r"\b(?:(?:in|on) (?:the |your )?twins tab|twins tab|(?:is|are) (?:now )?(?:live|up|sitting) (?:in|on) digital twins iq|"
        r"sits as a (?:digital )?twin(?: card)?)\b", re.I)),
)
# "It is on the dashboard now" / "It is in the dashboard" with no product
# named reads as a journey claim when the read speaks of a journey, a
# profile claim when it speaks of a profile; otherwise it is a surface
# claim no read may make
_DASHBOARD_NOW_RX = re.compile(r"\b(?:it is|it's|its|is|are|they are|now) (?:now )?(?:on|in) the dashboard(?: now)?\b|\b(?:on|in) the dashboard now\b", re.I)
_ARTIFACT_KEYS = {'journey': ('journey_key', 'jiq_job_id'), 'profile': ('profile_key', 's3_key', 'profile'), 'twin': ('twin_key', 'twin_job_id')}
_ARTIFACT_CHIP = {'journey': 'Add it to Digital Journey IQ', 'profile': 'Build this profile', 'twin': 'Build a twin from this audience'}
HERE_LINE = 'The read is here in the chat.'


def artifact_claim_kinds(text):
    """The artifact kinds a text claims exist on a dashboard surface."""
    t = str(text or '')
    kinds = [k for k, rx in _ARTIFACT_CLAIMS if rx.search(t)]
    if not kinds and _DASHBOARD_NOW_RX.search(t):
        low = t.lower()
        kinds = ['journey'] if 'journey' in low else ['twin'] if 'twin' in low else ['profile']
    return kinds


def strip_false_artifact_claims(text, exists=None):
    """Drop every sentence in a read that claims an artifact sits in a
    picker, dropdown, tab or card when ``exists(kind)`` is not True for
    that kind; append the here line once. Returns (text, kinds_dropped).
    Text with no such claim comes back untouched."""
    t = str(text or '')
    kinds = [k for k in artifact_claim_kinds(t) if not (exists and exists(k))]
    if not t or not kinds:
        return t, []
    rxs = [rx for k, rx in _ARTIFACT_CLAIMS if k in kinds] + [_DASHBOARD_NOW_RX]

    def _hit(s):
        return any(rx.search(s) for rx in rxs)
    kept = []
    for line in t.split('\n'):
        if not _hit(line):
            kept.append(line)
            continue
        # a sentence inside a line goes; a line that is one claim goes whole.
        # "On X: It is in the picker now: Y is built ..., carrying A down to B."
        # keeps its figures: the claim clause up to the first colon or comma
        # after the surface words is cut, the counts that follow stay.
        parts = re.split(r"(?<=[.!?])\s+", line)
        keep = []
        for p in parts:
            if not _hit(p):
                keep.append(p)
                continue
            m = re.search(r"(?:,\s*)?\b(?:carrying|running|with|holding|from)\b\s+(\d[\d,]*\s+people\b.*)$", p, re.I)
            if m:
                keep.append(m.group(1)[:1].upper() + m.group(1)[1:])
        if keep:
            kept.append(' '.join(keep))
    out = re.sub(r"\n{3,}", "\n\n", '\n'.join(kept)).strip()
    if HERE_LINE not in out:
        out = (out + "\n\n" + HERE_LINE).strip() if out else HERE_LINE
    return out, kinds


def shape_finished_read(payload, held=False, artifact_exists=None):
    """A finished background read lands via the status doc the widget
    polls and the thread copy, bypassing the envelope; shape it here.
    A held read carries no answer to shape. ``artifact_exists(kind)``
    may say a journey / profile / twin the read names is really on its
    surface; by default the payload's own keys decide. Never raises."""
    if held or not isinstance(payload, dict):
        return payload
    try:
        for k in ('answer', 'reply', 'text', 'message'):
            if isinstance(payload.get(k), str) and payload.get(k):
                payload[k] = strip_false_build_claims(payload[k])
    except Exception:
        pass
    try:
        def _exists(kind):
            if artifact_exists is not None:
                try:
                    if artifact_exists(kind):
                        return True
                except Exception:
                    pass
            return any(payload.get(k) for k in _ARTIFACT_KEYS.get(kind, ()))
        dropped = []
        for k in ('answer', 'reply', 'text', 'message'):
            if isinstance(payload.get(k), str) and payload.get(k):
                payload[k], kinds = strip_false_artifact_claims(payload[k], exists=_exists)
                dropped.extend(x for x in kinds if x not in dropped)
        if dropped:
            chips = [_ARTIFACT_CHIP[k] for k in dropped if k in _ARTIFACT_CHIP]
            fu = payload.get('followups') if isinstance(payload.get('followups'), list) else []
            payload['followups'] = chips + [f for f in fu if f not in chips][:max(0, 4 - len(chips))]
            payload['artifact_claims_dropped'] = dropped
    except Exception:
        pass
    try:
        return shape_payload(payload)
    except Exception:
        return payload
