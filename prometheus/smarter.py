"""Smaller contracts from the 2026-10-13 audit ("find other weaknesses and
how we can make this smarter"), kept together so the legacy chat module
and the service each need a line or two:

  prune_rulebook        the brief-drafting rulebook carries only the
                        sections an ask needs (speed)
  draft_output_ceiling  a plain ask gets a short output budget; a list of
                        subjects keeps the long one
  followup_lane         a short follow-up inherits the previous turn's
                        lane and subject
  capability_ask        "is there a way to", "can we", "how do we set up"
                        are answered with what we can do, never the opener
  negated_build         "don't build", "not a profile", "answer this from"
                        force answer mode
  offer_declined        "not now", "no thanks" close an offer
  brief_from_history    a run order with no stashed brief re-derives it
                        from the last confirm in the thread
  adjacent_read         the one most useful read we already hold on the
                        same subject, offered as a chip
  bind_to_published     a stated figure within a published one's family
                        is replaced by the published one (self-grade)
  thread_replay         the same question asked again in a thread gets the
                        same answer, with when it was given
"""
from __future__ import annotations

import datetime as dt
import json
import re
import traceback

# ------------------------------------------------------------ rulebook
# a section heading: a line opening with two or more ALL-CAPS words (10+
# chars) then punctuation, a newline, or a lowercase continuation
_SECTION_RX = re.compile(r"(?m)^(?=(?:[A-Z][A-Z0-9/&'.,#-]*[ -]){1,7}[A-Z][A-Z0-9/&'.,#-]{1,}(?:\s*[(:=-]|\n|\s+[a-z]))(?=[A-Z][A-Z0-9 /&().,#'-]{9,})")
_SUBIQ_RX = re.compile(r"\b(subscriber iq|sub iq|subiq|tracker|signups?|reactivat|churn|season \d|episode)", re.I)
_LIST_RX = re.compile(r"(,|\band\b|\bor\b|\beach\b|\bboth\b|\ball of\b|\bthe following\b)", re.I)
_PLATFORM_RX = re.compile(r"\b(netflix|prime video|amazon|hulu|disney|max|hbo|peacock|paramount|apple tv|starz|roku|tubi|pluto|youtube|spotify|tiktok|instagram|est|tvod|pvod|svod|avod|fast)\b", re.I)
_AUTO_RX = re.compile(r"\b(car|auto|automobile|truck|suv|ford|toyota|honda|chevy|chevrolet|tesla|bmw|lincoln|jeep|dealer)\b", re.I)
PRUNABLE = {
    'SUBSCRIBER IQ REQUESTS': lambda t, c: bool(_SUBIQ_RX.search(t)),
    'ANY-OF / ALL-OF TITLE LISTS': lambda t, c: bool(_LIST_RX.search(t)),
    'PARENT-SELECTION TIEBREAK': lambda t, c: bool(c),
    'UNIVERSE-QUALIFIER COMPATIBILITY': lambda t, c: bool(c),
    'SUBJECT-IDENTITY COMPATIBILITY': lambda t, c: bool(c),
    'PLATFORM SCOPE': lambda t, c: bool(_PLATFORM_RX.search(t)),
    'CAR-BRAND SELF-PINS': lambda t, c: bool(_AUTO_RX.search(t)),
    'PURCHASE-ON-A-PLATFORM': lambda t, c: bool(_PLATFORM_RX.search(t)) or bool(re.search(r"\b(buy|purchas|shop|consumer)", t, re.I)),
}


def prune_rulebook(system_prompt, text, candidates=None):
    """Drop whole rulebook sections an ask cannot need. A section is the
    text from one ALL-CAPS heading to the next. Anything not in PRUNABLE
    stays; when in doubt the section stays. Returns (prompt, dropped)."""
    try:
        s = str(system_prompt or '')
        t = str(text or '')
        starts = [m.start() for m in _SECTION_RX.finditer(s)]
        if len(starts) < 6:
            return s, []
        starts.append(len(s))
        out, dropped = [], []
        pos = 0
        for a, b in zip(starts, starts[1:]):
            if a > pos:
                out.append(s[pos:a])
            sec = s[a:b]
            head = sec.split('\n', 1)[0].upper()
            keep = True
            for key, needed in PRUNABLE.items():
                if head.startswith(key):
                    keep = needed(t, candidates)
                    if not keep:
                        dropped.append(key)
                    break
            if keep:
                out.append(sec)
            pos = b
        if pos < len(s):
            out.append(s[pos:])
        pruned = ''.join(out)
        # never prune away more than half; something is off with the split
        if len(pruned) < len(s) * 0.5:
            return s, []
        return pruned, dropped
    except Exception:
        traceback.print_exc()
        return system_prompt, []


def draft_output_ceiling(text, default=6000, long=16000):
    """A list of subjects (multi-spec array) keeps the long budget."""
    t = str(text or '')
    if len(re.findall(r",", t)) >= 2 or re.search(r"\b(each|both|all of|the following|separately|individual)\b", t, re.I) \
            or len(re.findall(r"\band\b", t, re.I)) >= 2 or len(t) > 600:
        return long
    return default


# ------------------------------------------------------------ follow-ups
INHERIT_LANES = {'catalog_lookup', 'status_lookup', 'deliverable_lookup', 'kpi_definition', 'brand_coverage'}
_NEW_SUBJECT_RX = re.compile(r"\b(for|about|on)\s+[A-Z][\w'&.-]+(\s+[A-Z][\w'&.-]+)*", re.U)


def followup_lane(text, history):
    """The previous turn's lane when this ask is a short follow-up with no
    new named subject ("any MMA fighters?" after a catalog answer). Returns
    the lane name or ''."""
    try:
        t = ' '.join(str(text or '').split())
        if not t or len(t.split()) > 12:
            return ''
        prev = None
        for turn in reversed(list(history or [])):
            if str(turn.get('role') or '') != 'user':
                prev = turn
                break
        if not isinstance(prev, dict):
            return ''
        meta = prev.get('meta') or {}
        lane = str(meta.get('reason') or meta.get('route') or (prev.get('decision') or {}).get('reason') or '')
        if lane not in INHERIT_LANES:
            return ''
        if re.match(r"^(and|also|what about|how about|any|same for|now|then|ok|okay)\b", t, re.I) or t.endswith('?') or len(t.split()) <= 6:
            return lane
        return ''
    except Exception:
        return ''


# ------------------------------------------------------- capability asks
_CAPABILITY_RX = re.compile(r"^\W*(is there a way to|is there any way to|can (?:we|you|crosswalk|prometheus)|could (?:we|you)|how (?:can|do|would|could) (?:we|i|you)\s+(?:set up|build|get|see|track|measure|do)|do you (?:do|offer|have a way)|are you able to|would it be possible to|what would it take to)", re.I)
_PRODUCT_WORDS = (
    (re.compile(r"\b(attribution|track(?:ing)? (?:a )?campaign|which (?:creator|post|asset)s? (?:drove|drive))", re.I),
     'Attribution IQ', 'tracks a campaign\'s creators, posts and assets to the people who reached a ticketing site, a product page or a signup, with first touch, last touch and assists',
     'Set up Attribution IQ'),
    (re.compile(r"\b(digital journey|journey|how people (?:came|got) to|path to)", re.I),
     'a Digital Journey', 'shows the path from a social post, clip or search to the watch, purchase or ticketing visit, step by step, with the paths off the page',
     'Build a Digital Journey'),
    (re.compile(r"\b(brand partnership|partnership|sponsor|valuation|what is .* worth)", re.I),
     'Brand Partnership IQ', 'values a talent or title against a brand: audience fit, earned media, brand lift and conversion lenses, one dollar figure',
     'Value a brand partnership'),
    (re.compile(r"\b(subscriber|signups?|churn|retention|reactivat)", re.I),
     'Subscriber IQ', 'tracks a title on a streaming service: accounts that watched, new and reactivated subscribers whose first watch was the title, and how long they stayed',
     'Pull a Subscriber IQ'),
    (re.compile(r"\b(audience|profile|fans?|viewers|buyers|attended|went to|people who)", re.I),
     'a Profile IQ profile', 'builds the audience of any person, title, brand or behavior (people who attended a tour, bought a product, watched a title) with demographics and the brands, platforms and talent they over-index on',
     'Run a profile'),
)


def capability_ask(text):
    """{'reply', 'followups'} for a can-we / is-there-a-way ask, or None."""
    t = ' '.join(str(text or '').split())
    if not t or not _CAPABILITY_RX.match(t):
        return None
    hits = [(name, what, chip) for rx, name, what, chip in _PRODUCT_WORDS if rx.search(t)]
    if not hits:
        hits = [_PRODUCT_WORDS[-1][1:]]
    name, what, chip = hits[0]
    subj = re.sub(_CAPABILITY_RX, '', t).strip(' ?.!')
    reply = (f"Yes. {name[0].upper() + name[1:]} {what}. "
             f"Tell me the subject{(' (' + subj[:80] + ')') if subj else ''} and the window and I will line it up; "
             f"it comes back as a brief to approve before anything is charged.")
    return {'reply': reply, 'followups': [chip]}


_NEGATED_BUILD_RX = re.compile(r"\b(don'?t|do not|no need to|without|not)\s+(?:build|run|pull|create|make|start)\b|\b(no|not) (?:a )?(?:new )?(?:profile|build|pull|run)\b|\banswer (?:this|it|that) (?:from|with|using)\b|\bjust (?:answer|tell me)\b|\bfrom (?:the )?existing\b", re.I)


def negated_build(text):
    """True when the ask forbids a build: answer mode only."""
    return bool(_NEGATED_BUILD_RX.search(str(text or '')))


_NEGATION_LEAD_RX = re.compile(
    r"^\W*(?:(?:please\s+)?(?:don'?t|do not|no need to)\s+(?:build|run|pull|create|make|start)[^.:;!?\n]*[.:;!?]?\s*)+"
    r"(?:(?:just\s+)?answer (?:this|it|that|me)(?: (?:from|with|using) [^:.\n]*)?[:.]?\s*)?", re.I)


def strip_build_negation(text):
    """The question itself, with the "don't build a new profile, answer
    this from the existing ones:" lead removed, when a question remains."""
    t = str(text or '')
    rest = _NEGATION_LEAD_RX.sub('', t, count=1).strip()
    return rest if len(rest.split()) >= 3 else t


_DECLINE_RX = re.compile(r"^\W*(not now|no thanks|no thank you|not yet|maybe later|later|no,? (?:that'?s )?(?:ok|fine|all)|skip|pass|nope|nah)\W*$", re.I)


def offer_declined(text):
    return bool(_DECLINE_RX.match(str(text or '')))


DECLINE_REPLY = "Understood, nothing is queued. Ask me anything else when you are ready."


# ------------------------------------------------- brief from history
_CONFIRM_RX = re.compile(r"^(here'?s the (?:digital journey|brand partnership|attribution|flywheel|profile)[^\n]*|i'?ll (?:build|run|value|set up)[^\n]*)", re.I)
_FLOW_RX = (('digital_journey', re.compile(r"digital journey", re.I)), ('brand_partnership', re.compile(r"brand partnership", re.I)),
            ('attribution', re.compile(r"attribution", re.I)), ('flywheel', re.compile(r"flywheel", re.I)))


def brief_from_history(history):
    """(flow, confirm_text) for the last confirm card in the thread, or
    (None, ''). The confirm text reads back through the flow's own parser
    as if the reader had typed it, so a run order never dead-ends on a
    missing stash."""
    for turn in reversed(list(history or [])):
        if str(turn.get('role') or '') == 'user':
            continue
        txt = str(turn.get('text') or turn.get('content') or '').strip()
        if not txt or not _CONFIRM_RX.match(txt):
            continue
        for flow, rx in _FLOW_RX:
            if rx.search(txt[:160]):
                return flow, txt
        return None, ''
    return None, ''


def rearm_brief_from_thread(body, history, user, ppu, intake_ui, intake_turn):
    """A run order with no stashed brief re-reads the last confirm card in
    the thread through its flow's own reader and arms that flow's confirm
    on the request ("yes, run it" dead-ended on "no brief waiting" whenever
    the stash was gone). Returns the flow or ''. `intake_ui` and
    `intake_turn` are the chat module's table and turn function."""
    try:
        flow, confirm_text = brief_from_history(history)
        if not flow or flow not in (intake_ui or {}):
            return ''
        kind, confirm_name, run_label, payload_key, collect_key, labels = intake_ui[flow]
        turn = intake_turn(flow, confirm_text, dict(body, text=confirm_text), history, user, ppu, False)
        parsed = turn.get(payload_key) if isinstance(turn, dict) else None
        if not isinstance(parsed, dict) or not parsed:
            return ''
        key = {'digital_journey': 'jiq_confirm', 'brand_partnership': 'bpiq_confirm',
               'attribution': 'aiq_confirm', 'flywheel': 'fw_confirm'}[flow]
        body[key] = parsed
        print(f"[brief-from-thread] {flow} re-read from the last confirm card")
        return flow
    except Exception:
        traceback.print_exc()
        return ''


def reading_line(reading):
    """One line that tells a drafting model how the Reader understood the
    request, or '' (2026-10-14)."""
    try:
        r = reading if isinstance(reading, dict) else None
        if not r or not r.get('intent'):
            return ''
        subs = ', '.join(((s.get('catalog') or s.get('as_written') or '') + (f" (library: {s['catalog']})" if s.get('catalog') and s.get('as_written') and s['catalog'] != s['as_written'] else ''))
                         for s in (r.get('subjects') or []) if (s.get('catalog') or s.get('as_written')))
        bits = [f"intent {r['intent']}"]
        if subs:
            bits.append(f"subject(s) {subs}")
        if r.get('window'):
            bits.append(f"window {r['window']}")
        if r.get('platform'):
            bits.append(f"platform {r['platform']}")
        if r.get('flags'):
            bits.append('flags ' + ', '.join(str(f) for f in r['flags']))
        if r.get('wants'):
            bits.append(f"they want: {r['wants']}")
        return "HOW THE REQUEST WAS READ (honor this; it was read with the whole thread and the library in view): " + '; '.join(bits) + '.'
    except Exception:
        return ''


def with_reading(user_prompt, reading):
    line = reading_line(reading)
    return (str(user_prompt or '') + "\n\n" + line) if line else user_prompt


def jsonable(obj):
    """A JSON-safe copy of a job's inputs (for the running doc the
    sweeper restarts from). Drops what json cannot carry."""
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return {}


# -------------------------------------------------- adjacent read offer
_KIND_CHIP = {'journey': 'Open the {name} Digital Journey', 'bpiq': 'Open the {name} Brand Partnership read',
              'attribution': 'Open the {name} Attribution IQ campaign', 'subiq': 'Open the {name} Subscriber IQ tracker',
              'profile': 'Open the {name} profile', 'package': 'Resend the {name} data files', 'deck': 'Open the {name} deck'}


def adjacent_read(subject, uname, *, host=None, s3=None, exclude_kind=''):
    """One chip naming the most useful other read we hold on the subject,
    or None."""
    try:
        if not str(subject or '').strip():
            return None
        from . import precedent as _prec
        found = _prec.resolve(subject, uname, host=host, s3=s3)
        if not found or str(found.get('kind') or '') == str(exclude_kind or ''):
            return None
        tpl = _KIND_CHIP.get(str(found.get('kind') or ''))
        if not tpl:
            return None
        name = str(found.get('name') or subject)
        return {'label': tpl.format(name=name)[:80], 'kind': found.get('kind'), 'key': found.get('key')}
    except Exception:
        return None


# ------------------------------------------------------ self-grade bind
_FIG_RX = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d{4,})(?![\w.%])")
_UNIT_WORDS = ('viewers', 'accounts', 'people', 'users', 'subscribers', 'transactions', 'views', 'signups', 'buyers', 'visitors', 'minutes')


def _label_tokens(s):
    return {w for w in re.findall(r"[a-z0-9]+", str(s or '').lower()) if len(w) > 3 or w.isdigit()}


def bind_to_published(reply, subject, *, ledger=None, min_overlap=3):
    """Replace a stated count whose sentence shares at least `min_overlap`
    distinctive tokens with a published metric's name and differs from it
    by more than 5% with the published count. Conservative: one swap per
    metric, counts only, never percentages. Returns (reply, swaps)."""
    swaps = []
    try:
        il = ledger
        if il is None:
            import insights_ledger as il
        led = il.consult(subject=subject, question=str(reply or '')[:200]) or {}
        entries = led.get('entries') or []
        if not entries:
            return reply, swaps
        published = []
        for e in entries:
            for m in (e.get('metrics') or []):
                try:
                    v = float(m.get('value'))
                except (TypeError, ValueError):
                    continue
                unit = str(m.get('unit') or '').lower()
                if unit in ('%', 'pct', 'percent') or v < 1000:
                    continue
                published.append((str(m.get('name') or ''), v, _label_tokens(m.get('name'))))
        if not published:
            return reply, swaps
        out = str(reply or '')
        sentences = re.split(r"(?<=[.!?])\s+|\n", out)
        used = set()
        for sent in sentences:
            toks = _label_tokens(sent)
            for m in _FIG_RX.finditer(sent):
                try:
                    v = float(m.group(1).replace(',', ''))
                except ValueError:
                    continue
                best = None
                for name, pv, ptoks in published:
                    if name in used:
                        continue
                    ov = len(toks & ptoks)
                    if ov >= min_overlap and any(u in sent.lower() for u in _UNIT_WORDS):
                        if best is None or ov > best[0]:
                            best = (ov, name, pv)
                if best and best[2] > 0 and abs(v - best[2]) / best[2] > 0.05 and max(v, best[2]) / max(min(v, best[2]), 1) < 20:
                    new_s = sent.replace(m.group(1), f"{int(round(best[2])):,}", 1)
                    out = out.replace(sent, new_s, 1)
                    used.add(best[1])
                    swaps.append({'from': m.group(1), 'to': f"{int(round(best[2])):,}", 'metric': best[1]})
                    break
        return out, swaps
    except Exception:
        traceback.print_exc()
        return reply, swaps


# ------------------------------------------------- evidence bind (exact)
_EV_NUM_RX = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d{4,})(?![\w,]|\.\d)")


def evidence_numbers(evidence_text):
    """Every count (1,000 and up) the evidence carries, as ints."""
    out = set()
    for m in _EV_NUM_RX.finditer(str(evidence_text or '')):
        try:
            out.add(int(m.group(1).replace(',', '')))
        except ValueError:
            pass
    return out


def bind_to_evidence(reply, evidence_text, tol=0.02):
    """A count in the reply that sits within `tol` of a count the evidence
    carries, but is not that count, becomes it (2026-10-14: a Subscriber
    IQ answer said 21,115 and 11,569 where the evidence read 21,011 and
    11,511; the model re-derived what it had been handed). Exact matches
    and figures with no close evidence neighbour are left alone, so sums
    and ratios the model computed stay. Returns (reply, swaps)."""
    swaps = []
    try:
        ev = evidence_numbers(evidence_text)
        if not ev:
            return reply, swaps
        ev_sorted = sorted(ev)
        out = str(reply or '')

        def _fix(m):
            raw = m.group(1)
            try:
                v = int(raw.replace(',', ''))
            except ValueError:
                return raw
            if v in ev or v < 1000:
                return raw
            best = min(ev_sorted, key=lambda e: abs(e - v))
            if best > 0 and abs(best - v) / float(best) <= tol and abs(best - v) >= 1:
                swaps.append({'from': raw, 'to': f"{best:,}"})
                return f"{best:,}"
            return raw
        out = _EV_NUM_RX.sub(_fix, out)
        return out, swaps
    except Exception:
        traceback.print_exc()
        return reply, swaps


# ---------------------------------------------------------- thread replay
_REJECT_RX = re.compile(r"\b(wrong|incorrect|not right|doesn'?t (?:look|seem) right|that'?s not|redo|try again|no,? that)\b", re.I)


def _fold_q(s):
    return re.sub(r"[^a-z0-9 ]", ' ', str(s or '').lower()).split()


def thread_replay(text, history):
    """The earlier answer to this exact question in this thread, with when
    it was given, or None."""
    try:
        q = _fold_q(text)
        if len(q) < 3:
            return None
        turns = list(history or [])
        for i, turn in enumerate(turns):
            if str(turn.get('role') or '') != 'user':
                continue
            if _fold_q(turn.get('text') or turn.get('content')) != q:
                continue
            nxt = next((x for x in turns[i + 1:] if str(x.get('role') or '') != 'user'), None)
            if not nxt:
                continue
            ans = str(nxt.get('text') or nxt.get('content') or '').strip()
            meta = nxt.get('meta') or {}
            if not ans or meta.get('job') or meta.get('options') or len(ans) < 40:
                continue
            if re.search(r"(give me, in one message|which time window|here'?s the|brief drafted|ready to run)", ans, re.I):
                continue
            # an answer the reader pushed back on is never replayed
            nxt_user = next((x for x in turns[i + 1:] if str(x.get('role') or '') == 'user' and x is not turn), None)
            if nxt_user and _REJECT_RX.search(str(nxt_user.get('text') or nxt_user.get('content') or '')):
                continue
            when = str(nxt.get('ts') or '')
            try:
                d = dt.datetime.fromisoformat(when.replace('Z', '+00:00'))
                when_txt = d.strftime('%b %-d at %H:%M UTC')
            except Exception:
                when_txt = ''
            lead = f"Same read as earlier in this thread{(' (' + when_txt + ')') if when_txt else ''}:"
            return {'reply': lead + '\n\n' + ans, 'followups': list(meta.get('followups') or [])[:3]}
        return None
    except Exception:
        return None
