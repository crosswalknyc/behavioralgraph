"""Short answers before any work starts: the garbled-input normalization, the numeric follow-up, the status check on a running build, the typed approval with no brief, the work-order verbs (status, ETA, cancel), the Subscriber IQ lookup, and the cut-request intercept.

Moved out of prometheus/legacy/chat.py (rebuild stage 7, 2026-11-23) by
scripts/extract_legacy_core.py, behaviour unchanged. This module reads the
host through ``_H`` and the legacy chat namespace through ``_C``
(prometheus.legacy.C), both bound before it is imported; chat.py imports it
last and re-exports the moved names, so every name chat.py binds is read
through ``_C.<name>`` at call time exactly as it was when the code lived in
one file. ``_C.globals()`` is chat.py's own namespace, the one the lanes this
code hands it to expect. The suites read the moved text through
scripts/_pm_legacy_view.legacy_view(), which splices each block back at its
marker in chat.py.
"""
from prometheus.legacy import H as _H, C as _C  # noqa: E402,F401
import traceback
import re
from prometheus import faults as _faults
from prometheus import subject_rules as _subject_rules
from datetime import datetime, timedelta, date, timezone
from prometheus import plan_allowance as _plan
from prometheus import order_quote as _oq

# <<moved>>
# <<block: 1>>
# GARBLED-INPUT NORMALIZATION (2026-09-28, Phase 4): "top 1 0 starz
# series" hit the calm fallback because a split number reads as noise.
# Join single digits split by a space when they form a plausible
# number, and collapse runs of spaces. Routing and the model both see
# the repaired text; nothing else changes.
_PM_SPLIT_DIGIT_RE = re.compile(r'\b(\d)\s+(\d)\b')


# <<block: 2>>
def _pm_normalize_ask(text):
    t = str(text or '')
    if not t:
        return t
    prev = None
    while prev != t:
        prev = t
        t = _C._PM_SPLIT_DIGIT_RE.sub(r'\1\2', t)
    return re.sub(r'[ \t]{2,}', ' ', t)


# <<block: 3>>
# NUMERIC FOLLOW-UP (2026-09-28, Phase 3): "what is that as a share of
# the US population?" after a delivered count is arithmetic on the
# last answer, not a fresh read. Resolve the count from the last
# agent turn and answer instantly.
_PM_US_SHARE_RE = re.compile(
    r'\b(?:as a |what )?(?:share|percent(?:age)?)\s+of\s+(?:the\s+)?'
    r'(?:us|u\.s\.|american)\s*(?:population|pop|adults|country|'
    r'gen pop)?\b', re.IGNORECASE)


# <<block: 4>>
_PM_COUNT_IN_REPLY_RE = re.compile(
    r'(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*[MKB])\s*'
    r'(?:us\s+)?(?:unique\s+)?(?:viewers|people|users|accounts|'
    r'subscribers|buyers|players|individuals)', re.IGNORECASE)


# <<block: 5>>
def _pm_parse_count(tok):
    tok = str(tok).strip()
    try:
        if tok[-1] in 'MKBmkb':
            mult = {'k': 1e3, 'm': 1e6, 'b': 1e9}[tok[-1].lower()]
            return float(tok[:-1].strip()) * mult
        return float(tok.replace(',', ''))
    except Exception:
        return None


# <<block: 6>>
def _pm_numeric_followup_reply(text, history):
    """US-population share arithmetic on the last delivered count, or
    None when the ask is not that shape or no count resolves."""
    if not _C._PM_US_SHARE_RE.search(str(text or '')) \
            or len(str(text or '')) > 120:
        return None
    for turn in reversed(list(history or [])[-8:]):
        if (turn.get('role') or '') == 'user':
            continue
        matches = _C._PM_COUNT_IN_REPLY_RE.findall(
            str(turn.get('text') or ''))
        if not matches:
            continue
        vals = [v for v in (_C._pm_parse_count(m) for m in matches)
                if v and v > 999]
        if not vals:
            continue
        n = max(vals)
        share = n / 329_900_000.0 * 100.0
        share_s = (f"{share:.2f}%" if share < 1 else f"{share:.1f}%")
        return (f"That is {share_s} of the US population - "
                f"{int(n):,} of 329.9M people.")
    return None


# <<block: 7>>
# STATUS-CHECK INTERCEPT (2026-09-28 Jenna, keith's "is eastside golf
# running?" got an empty step on one surface and a duplicate 5-credit
# build offer on the other: "these were clearly a status update check.
# needs to recognize that in the future"). Subject-named status
# questions answer from the caller's own runs; they never reach the
# analysis pass, the answer library, or the build interpreter. The
# interceptor only speaks when a run actually matches the named
# subject, so "is yellowstone still running?" as an airing question
# falls through to analysis untouched when no Yellowstone run exists.
_PM_STATUS_ASK_RES = (
    re.compile(r"^\s*(?:is|are)\s+(?:my\s+|the\s+)?(.+?)\s+(?:still\s+)?"
               r"(?:running|building|done|finished|ready|complete(?:d)?|"
               r"going|in\s+progress|live\s+yet)\s*\??\s*$", re.I),
    re.compile(r"^\s*(?:did|has|have)\s+(?:my\s+|the\s+)?(.+?)\s+"
               r"(?:finish(?:ed)?|complete(?:d)?|land(?:ed)?|"
               r"come\s+back|run(?:\s+yet)?)\s*\??\s*$", re.I),
    re.compile(r"^\s*(?:status|progress|eta)\s+(?:of|on|for)\s+"
               r"(.+?)\s*\??\s*$", re.I),
    re.compile(r"^\s*how(?:'s|\s+is)\s+(?:my\s+|the\s+)?(.+?)\s+"
               r"(?:coming(?:\s+along)?|going|doing|progressing)"
               r"\s*\??\s*$", re.I),
)


# <<block: 8>>
_PM_STATUS_TAIL_TOKENS = {'build', 'builds', 'profile', 'profiles',
                          'run', 'runs', 'pull', 'pulls', 'cut',
                          'cuts', 'report', 'job', 'request'}


# <<block: 9>>
def _pm_status_ask_subject(text):
    """Return the subject phrase of a status question, or ''."""
    t = str(text or '').strip()
    if not t or len(t) > 120:
        return ''
    for rx in _C._PM_STATUS_ASK_RES:
        m = rx.match(t)
        if not m:
            continue
        words = [w for w in re.split(r'\s+', m.group(1).strip()) if w]
        while words and _H._normalize_for_match(words[-1]) in \
                _C._PM_STATUS_TAIL_TOKENS:
            words.pop()
        if words:
            return ' '.join(words[:6])
    return ''


# <<block: 10>>
def _pm_status_matching_runs(user, phrase, limit=40):
    """The caller's runs whose subject matches the phrase (token
    containment either way), most recent first. Empty on any listener
    trouble - the ask then falls through to normal routing."""
    if not _H.SYNTH_QUEUE_SECRET or not _H.SYNTH_QUEUE_URL:
        return []
    uid = (user.get('email') or user.get('username') or '').strip()
    if not uid:
        return []
    try:
        import requests as _rq
        resp = _rq.get(
            f"{_H.SYNTH_QUEUE_URL}/synth/list",
            params={'limit': limit, 'user': uid},
            headers={'X-Synth-Auth': _H.SYNTH_QUEUE_SECRET}, timeout=12)
        if resp.status_code != 200:
            return []
        docs = resp.json() or []
    except Exception:
        return []
    want = {w for w in _H._normalize_for_match(phrase).split()
            if w not in _C._PM_BASE_GENERIC_TOKENS}
    if not want:
        return []
    out = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        subj = {w for w in _H._normalize_for_match(
                    str(doc.get('subject') or '')).split()
                if w not in _C._PM_BASE_GENERIC_TOKENS}
        if subj and (want <= subj or subj <= want):
            out.append(doc)
    return out


# <<block: 11>>
def _pm_status_reply_for_runs(runs):
    """One plain status line per matching run (up to 3). Failed runs
    read as still building - the ops-hold convention keeps internal
    failures invisible while the rerun completes under the same id."""
    lines = []
    for doc in runs[:3]:
        subj = str(doc.get('subject') or 'Your profile').strip()
        st = str(doc.get('status') or '').strip().lower()
        if st == 'complete':
            if doc.get('subiq_s3_key'):
                lines.append(
                    f"{subj} is finished. The Subscriber IQ read is "
                    f"live in the Subscriber IQ tab now.")
            else:
                line = (f"{subj} is finished and live in the Select "
                        f"Profile dropdown.")
                if doc.get('tu_key'):
                    line += f" File: {doc['tu_key']}"
                lines.append(line)
        elif st in ('failed', 'error', 'ops_hold'):
            from prometheus.status_lane import client_failure_line
            _cl = client_failure_line(doc.get('error')) if st == 'failed' else ''
            if _cl:
                lines.append(f"{subj}: {_cl}")
            else:
                lines.append(
                    f"{subj} is still building - it is taking a little "
                    f"longer than usual. It lands in the Select Profile "
                    f"dropdown and this chat confirms the moment it "
                    f"finishes.")
        else:
            si, tot = doc.get('step_index'), doc.get('step_total') or 11
            lab = str(doc.get('step_label') or '').strip()
            if isinstance(si, int) and tot:
                pct = max(0, min(100, int(round(100.0 * si / tot))))
                line = f"{subj} is building now - step {si} of {tot}"
                if lab:
                    line += f" ({lab})"
                line += f", about {pct}%."
            else:
                line = f"{subj} is {st or 'queued'}."
            line += (" It lands in the Select Profile dropdown and "
                     "this chat confirms the moment it finishes.")
            lines.append(line)
    return '\n'.join(lines)


# <<block: 12>>
_PM_STATUS_LEAD_TOKENS = {'my', 'the', 'our', 'that', 'this'}


# <<block: 13>>
def _pm_library_match(phrase):
    """The catalog profile the phrase names (distinctive-token
    containment either way, like the run match), or None."""
    pt = [w for w in _H._normalize_for_match(phrase).split()
          if w not in _C._PM_STATUS_TAIL_TOKENS]
    if not pt:
        return None
    pset = set(pt)
    best = None
    try:
        for entry in _C._profile_catalog_for_chat():
            subj = str(entry.get('subject')
                       or entry.get('display_name') or '').strip()
            if not subj or ' - ' in subj:
                continue
            st = set(_H._normalize_for_match(subj).split())
            if not st:
                continue
            if st <= pset or pset <= st:
                score = len(st & pset)
                if best is None or score > best[0]:
                    best = (score, {
                        'subject': subj,
                        's3_key': str(entry.get('s3_key') or ''),
                        'last_modified': str(entry.get('last_modified')
                                             or ''),
                    })
    except Exception:
        traceback.print_exc()
    return best[1] if best else None


# <<block: 14>>
def _pm_status_lane(user, text):
    """Deterministic answer for a subject-named status question on
    either chat surface (2026-10-02 S3 lanes). Returns
    (reply, followups, outcome, subject) or None when the text is not
    a status question. A status question never reaches the model: a
    matching run reads its step, a library profile reads as finished,
    anything else reads as not started with a build chip."""
    phrase = _C._pm_status_ask_subject(text)
    if not phrase:
        return None
    words = phrase.split()
    while len(words) > 1 and words[0].lower() in _C._PM_STATUS_LEAD_TOKENS:
        words.pop(0)
    phrase = ' '.join(words)
    try:
        runs = _C._pm_status_matching_runs(user, phrase)
    except Exception:
        runs = []
    if runs:
        return (_C._pm_status_reply_for_runs(runs), [], 'answered',
                str(runs[0].get('subject') or phrase))
    lib = _C._pm_library_match(phrase)
    if lib:
        when = ''
        try:
            from datetime import datetime as _dt
            d = _dt.strptime(str(lib.get('last_modified') or '')[:10],
                             '%Y-%m-%d')
            when = d.strftime('%b %-d, %Y')
        except Exception:
            when = ''
        reply = (f"Nothing named {phrase} is building for your account "
                 f"right now. {lib['subject']} is already in the library"
                 + (f" (finished {when})." if when else ".")
                 + " Open it from Select Profile, or ask me to read it "
                 "here.")
        return (reply, [f"Analyze {lib['subject']}"], 'in_library',
                lib['subject'])
    reply = (f"Nothing named {phrase} is building for your account, and "
             f"it is not in the library yet. Say 'Build a profile for "
             f"{phrase}' and I will set up the brief for you to approve.")
    return (reply, [f"Build a profile for {phrase}"], 'not_found', phrase)


# <<block: 15>>
# TYPED APPROVAL WITH NO BRIEF (2026-10-02 S3 lanes). The widget turns
# "approved" into the Approve button while a brief is on screen; the
# bare word only reaches this surface when the card is gone. Approval
# vocabulary only - a plain "yes" can still answer a server question.
_PM_APPROVAL_WORD_RE = re.compile(
    r"^\s*(?:yes[,.!]?\s*)?(?:approved?|approve\s+(?:it|this|that|the\s+"
    r"brief)|run\s+it|ship\s+it|proceed|start\s+(?:it|the\s+build)|"
    r"looks\s+good[,.]?\s*(?:approve|run\s+it|go)|go\s+ahead(?:\s+and\s+"
    r"(?:run|build)\s+it)?)\s*[.! ]*$", re.I)


# <<block: 16>>
_PM_APPROVE_NO_DRAFT_COPY = (
    "There is no brief waiting for approval in this thread, so there is "
    "nothing to run yet. Tell me what you want (for example 'Build a "
    "profile for Eastside Golf' or 'Compare Hulu and Peacock') and I "
    "will set up the brief for you to approve.")


# <<block: 17>>
def _pm_last_agent_asked(history):
    """True when the most recent agent turn in the thread ended on a
    question, so a bare confirm word is an answer to it."""
    for h in reversed([h for h in (history or []) if isinstance(h, dict)]):
        role = str(h.get('role') or '').lower()
        if role in ('agent', 'assistant'):
            txt = str(h.get('text') or h.get('content') or '').strip()
            return txt.endswith('?')
        if role == 'user':
            return False
    return False


# <<block: 18>>
def _pm_approval_word(text):
    """True when the message is approval vocabulary and nothing else."""
    t = str(text or '')
    try:
        from prometheus import user_signal as _us
        t = _us._DATE_RANGE_SUFFIX_RE.sub('', t)
    except Exception as _fx:
        _faults.note('chat', _fx)
    return bool(_C._PM_APPROVAL_WORD_RE.match(t))


# <<block: 19>>
# WORK-ORDER VERBS (2026-09-30 Jenna: "do all of them"). Status, ETA,
# and cancel are account services: subjectless asks ("status?", "are
# you still working on my report?", "how long will this take?",
# "stop") answer deterministically from the caller's own runs on BOTH
# chat surfaces, free, before any gate or model call. Evidence from
# the ask log: emma's nine-turn work-order thread got an ETA shrug and
# a page-analysis answer to "Are you still working on my report?";
# jessie typed "stop" three times and got three page analyses.
_PM_WO_WORK_NOUNS = (
    r"(?:report|profile|build|run|pull|file|request|order|job|"
    r"deliverable|csv|deck|cut|analysis)")


# <<block: 20>>
_PM_WO_STATUS_RES = (
    re.compile(r"^\s*(?:status|any\s+updates?|progress|"
               r"status\s+update|what(?:'s|\s+is)\s+the\s+status)"
               r"\s*[?.!]*\s*$", re.I),
    re.compile(r"^\s*are\s+you\s+still\s+working(?:\s+on\s+"
               r"(?:it|that|this|my\s+[\w ]{1,40}|the\s+[\w ]{1,40}))?"
               r"\s*[?.!]*\s*$", re.I),
    re.compile(r"^\s*(?:is|are)\s+(?:it|that|this|they|my\s+"
               + _PM_WO_WORK_NOUNS + r"s?)\s+(?:done|ready|"
               r"finished|complete[d]?)(?:\s+yet)?\s*[?.!]*\s*$",
               re.I),
    re.compile(r"^\s*where(?:'s|\s+is)\s+(?:my|the)\s+"
               r"[\w ]{0,30}?" + _PM_WO_WORK_NOUNS +
               r"\s*[?.!]*\s*$", re.I),
    re.compile(r"^\s*did\s+(?:it|my\s+" + _PM_WO_WORK_NOUNS +
               r")\s+(?:finish|complete|land|go\s+through)"
               r"(?:\s+yet)?\s*[?.!]*\s*$", re.I),
)


# <<block: 21>>
_PM_WO_ETA_RES = (
    re.compile(r"^\s*(?:what(?:'s|\s+is)\s+the\s+)?eta"
               r"\s*[?.!]*\s*$", re.I),
    re.compile(r"^\s*how\s+much\s+longer(?:\s+(?:will|does|is)\s+"
               r"(?:it|this|that)[\w ]{0,20})?\s*[?.!]*\s*$", re.I),
    re.compile(r"\bhow\s+long\s+(?:will|does|do|should)\s+"
               r"(?:it|this|that|the\s+(?:build|run|report|profile|"
               r"pull)|my\s+[\w ]{1,30}?)\s*.{0,40}?\btake\b", re.I),
    re.compile(r"\bwhen\s+will\s+(?:it|that|this|my\s+[\w ]{1,40}|"
               r"the\s+[\w ]{1,40})\s+be\s+"
               r"(?:done|ready|finished|complete)\b", re.I),
)


# <<block: 22>>
_PM_WO_CANCEL_RE = re.compile(
    r"^\s*(?:please\s+|ok(?:ay)?[,\s]+|never\s*mind[,\s]+|"
    r"actually[,\s]+)?"
    r"(?:stop|cancel|abort|kill)\b"
    r"(?!\s+(?:showing|sending|giving|telling|asking|using|"
    r"putting|adding|including|counting|saying|repeating)\b)"
    r"(?P<tail>(?:\s+(?:it|that|this|everything|all|the|my))?"
    r"[\w .&'-]{0,50}?)\s*[.!]*\s*$", re.I)


# <<block: 23>>
def _pm_subiq_lookup_answer(user, title):
    """Answer "do you see the X Subscriber IQ?" from the library and
    the caller's own runs (2026-10-02, Bria: the question drafted a
    10-credit duplicate of a read finished twelve hours earlier).
    Returns (reply, followups). Never charges, never drafts."""
    import prometheus_analysis as _pma
    title = str(title or '').strip()
    rows = []
    try:
        if title == '*':
            rows = _pma.list_subiq_shows(
                _H.s3_client, _H.SUBSCRIBER_S3_BUCKET)[:5]
        else:
            rows = _pma.match_subiq_shows(
                _H.s3_client, _H.SUBSCRIBER_S3_BUCKET, title)
    except Exception:
        traceback.print_exc()
        rows = []

    def _when(lm):
        try:
            from datetime import datetime as _dt
            d = _dt.strptime(str(lm or '')[:16], '%Y-%m-%dT%H:%M')
            return d.strftime('%b %-d, %Y')
        except Exception:
            return ''

    if rows:
        if title == '*':
            names = ', '.join(r[0] for r in rows)
            return (f"The newest Subscriber IQ reads in the library are "
                    f"{names}. Name one and I will pull it up, or open "
                    f"any of them from the Subscriber IQ tab.",
                    [f"Analyze {rows[0][0]}"])
        show, _key, lm = rows[0]
        when = _when(lm)
        lead = (f"Yes. {show} is in the Subscriber IQ library"
                + (f" (finished {when})." if when else "."))
        if len(rows) > 1:
            others = ', '.join(r[0] for r in rows[1:])
            lead += f" {others} {'is' if len(rows) == 2 else 'are'} there too."
        reply = (lead + " Open it from the Subscriber IQ tab, or ask me "
                 "about it here. No charge to view it.")
        return reply, [f"Analyze {show}",
                       f"Top 3 insights on {show}"]
    # Not in the library: still building?
    try:
        runs = _C._pm_status_matching_runs(user, title) if title != '*' else []
    except Exception:
        runs = []
    if runs:
        return _C._pm_status_reply_for_runs(runs), []
    nice = title if title != '*' else 'that title'
    return (f"I do not see a Subscriber IQ for {nice} yet. I can build "
            f"it{_plan.build_cost_clause(_C._pm_seat_reads_included(_oq.build_usd(_H, 'subscriber_iq')), _C._pm_usd_label(_oq.build_usd(_H, 'subscriber_iq')))}"
            f", and it lands in the Subscriber IQ tab when it "
            f"finishes.",
            [f"Pull Subscriber IQ for {nice}"] if title != '*' else [])


# <<block: 24>>
def _pm_workorder_intent(text):
    """'status' | 'eta' | 'cancel' | None. Anchored, short, and
    subjectless-friendly so real analysis questions never match."""
    t = str(text or '').strip()
    if not t or len(t) > 90 or '\n' in t:
        return None
    if _C._PM_WO_CANCEL_RE.match(t) and '?' not in t:
        return 'cancel'
    for rx in _C._PM_WO_ETA_RES:
        if rx.search(t):
            return 'eta'
    for rx in _C._PM_WO_STATUS_RES:
        if rx.match(t):
            return 'status'
    return None


# <<block: 25>>
def _pm_user_runs(user, limit=40, active=False, status=None):
    """The caller's runs from the build engine, newest first. [] on
    any listener trouble so the ask falls through to normal routing."""
    if not _H.SYNTH_QUEUE_SECRET or not _H.SYNTH_QUEUE_URL:
        return []
    uid = (user.get('email') or user.get('username') or '').strip()
    if not uid and not status:
        return []
    try:
        import requests as _rq
        params = {'limit': limit}
        if uid and status is None:
            params['user'] = uid
        if active:
            params['active'] = 1
        if status:
            params['status'] = status
        resp = _rq.get(
            f"{_H.SYNTH_QUEUE_URL}/synth/list", params=params,
            headers={'X-Synth-Auth': _H.SYNTH_QUEUE_SECRET}, timeout=12)
        if resp.status_code != 200:
            return []
        docs = resp.json() or []
        return [d for d in docs if isinstance(d, dict)]
    except Exception:
        return []


# <<block: 26>>
def _pm_typical_build_minutes():
    """Median wall minutes of recently completed builds, or None.
    Honest ETA basis: measured durations, never a made-up promise."""
    docs = _C._pm_user_runs({}, limit=60, status='complete')
    mins = []
    for d in docs:
        try:
            q = datetime.fromisoformat(
                str(d.get('queued_at') or '').replace('Z', '+00:00'))
            u = datetime.fromisoformat(
                str(d.get('updated_at') or '').replace('Z', '+00:00'))
            m = (u - q).total_seconds() / 60.0
            if 3 <= m <= 360:
                mins.append(m)
        except Exception:
            continue
    if not mins:
        return None
    mins.sort()
    return mins[len(mins) // 2]


# <<block: 27>>
def _pm_eta_line(doc, typical=None):
    """Plain remaining-time sentence for one active run, or ''."""
    try:
        typical = typical or _C._pm_typical_build_minutes()
        if not typical:
            return ''
        q = datetime.fromisoformat(
            str(doc.get('queued_at') or '').replace('Z', '+00:00'))
        elapsed = (datetime.now(timezone.utc) - q).total_seconds() / 60.0
        remaining = typical - elapsed
        if elapsed > 2 * typical:
            return ("It is taking longer than a typical build. It "
                    "finishes on its own and the completion email "
                    "goes out the moment it lands.")
        if remaining <= 4:
            return "Expect it within the next few minutes."
        return (f"Recent builds like this one finish in about "
                f"{int(round(typical))} minutes; this one has roughly "
                f"{int(round(remaining))} minutes to go.")
    except Exception:
        return ''


# <<block: 28>>
def _pm_cancel_target(user, text):
    """(doc, others) - the active run the cancel names, or the only
    active run. doc None when nothing matches / nothing active."""
    active = _C._pm_user_runs(user, active=True)
    if not active:
        return None, []
    m = _C._PM_WO_CANCEL_RE.match(str(text or '').strip())
    tail = (m.group('tail') or '').strip() if m else ''
    tail = re.sub(r'^(?:it|that|this|everything|all|the|my)\b\s*', '',
                  tail, flags=re.I).strip()
    tail = re.sub(r'\b(?:build|run|profile|pull|report|request|job)s?'
                  r'\s*$', '', tail, flags=re.I).strip()
    if tail:
        want = {w for w in _H._normalize_for_match(tail).split()
                if w not in _C._PM_BASE_GENERIC_TOKENS}
        for doc in active:
            subj = {w for w in _H._normalize_for_match(
                        str(doc.get('subject') or '')).split()
                    if w not in _C._PM_BASE_GENERIC_TOKENS}
            if want and subj and (want <= subj or subj <= want):
                return doc, [d for d in active if d is not doc]
        return None, active
    if len(active) == 1:
        return active[0], []
    return None, active


# <<block: 29>>
def _pm_workorder_reply(user, text, intent):
    """Deterministic answer for a work-order verb, or None to fall
    through to normal routing. Never raises."""
    try:
        if intent == 'cancel':
            doc, others = _C._pm_cancel_target(user, text)
            if doc is None and not others:
                return ("Nothing is building for your account right "
                        "now, so there is nothing to stop. If you "
                        "meant something else, say the word and I "
                        "will take care of it.")
            if doc is None and others:
                names = ', '.join(
                    str(d.get('subject') or 'unnamed')
                    for d in others[:4])
                return (f"You have more than one build going: "
                        f"{names}. Say 'cancel' plus the name and I "
                        f"will stop that one.")
            run_id = str(doc.get('run_id') or '')
            subj = str(doc.get('subject') or 'that build')
            try:
                import requests as _rq
                resp = _rq.post(
                    f"{_H.SYNTH_QUEUE_URL}/synth/cancel/{run_id}",
                    headers={'X-Synth-Auth': _H.SYNTH_QUEUE_SECRET},
                    timeout=12)
                ok = resp.status_code == 200
                st = (resp.json() or {}).get('status', '') if ok else ''
            except Exception:
                ok, st = False, ''
            if ok and st == 'cancelled':
                return (f"Stopped. {subj} was cancelled before it "
                        f"started and the charge comes back to your "
                        f"balance automatically. Nothing else was "
                        f"touched.")
            if ok:
                return (f"Stopping {subj} now. It unwinds at the next "
                        f"safe point and the charge comes back to "
                        f"your balance automatically. Nothing else "
                        f"was touched.")
            _H._chatbot_error_email(
                'pm/cancel',
                f'cancel request did not reach the engine '
                f'(run {run_id}, subject {subj})',
                user_email=(user.get('email') or user.get('username')),
                payload={'question': text[:300], 'run_id': run_id})
            return _H._CHATBOT_CALM_MESSAGE
        # status / eta share the same data
        active = _C._pm_user_runs(user, active=True)
        if intent == 'eta':
            if active:
                lead = _C._pm_status_reply_for_runs(active[:1])
                eta = _C._pm_eta_line(active[0])
                return (lead + ('\n' + eta if eta else '')).strip()
            typical = _C._pm_typical_build_minutes()
            if typical:
                return (f"Nothing is building for your account right "
                        f"now. For reference, a typical build "
                        f"finishes in about {int(round(typical))} "
                        f"minutes end to end.")
            return ("Nothing is building for your account right now. "
                    "Ask for any audience and I will kick one off.")
        # status
        if active:
            lead = _C._pm_status_reply_for_runs(active[:3])
            eta = _C._pm_eta_line(active[0])
            return (lead + ('\n' + eta if eta else '')).strip()
        recent = _C._pm_user_runs(user, limit=6)
        for doc in recent:
            st = str(doc.get('status') or '').lower()
            if st == 'complete':
                return _C._pm_status_reply_for_runs([doc])
        return ("Nothing is building for your account right now. "
                "Everything you have run is already live in the "
                "Select Profile dropdown. Ask for any audience and I "
                "will kick off a new one.")
    except Exception:
        traceback.print_exc()
        return None


# <<block: 30>>
# CUT-REQUEST INTERCEPT (2026-09-28 Jenna, keith's "Let's do one cut
# of males only and another cut of black consumers" answered with a
# replayed brand read: "it should have asked him what profile he was
# talking about and then helped prompt him to run cuts of it").
# Imperative cut vocabulary near 'cut(s)'; analytical questions about
# an existing cut ("what's the male cut of the audience") stay on the
# analysis path.
_PM_CUT_INTENT_RE = re.compile(
    r"\b(?:do|run|make|create|add|build|pull|need|want|get|give|lets|"
    r"let's|can you|could you|please)\b[^.?!\n]{0,40}?\bcuts?\b"
    r"|\bcut\s+(?:this|that|it|the)\b"
    # imperative-start command, incl. the composed chip:
    # 'Cut USA Today by males only and black consumers'
    r"|^\s*cuts?\b", re.IGNORECASE)


# <<block: 31>>
# 'one cut of males only and another cut of black consumers' ->
# ['males only', 'black consumers']
_PM_CUT_COHORT_RE = re.compile(
    r"\bcuts?\s+(?:of|for|by)\s+([a-z0-9][a-z0-9 +&'\-]{1,40}?)"
    r"(?=\s+(?:and|plus)\b|\s*[,.;!?\n]|$)", re.IGNORECASE)


# <<block: 32>>
_PM_CUT_IDIOM_RE = re.compile(
    r"\bcut\s+(?:to the chase|corners|it (?:out|down|short))\b",
    re.IGNORECASE)


# <<block: 33>>
def _pm_text_names_catalog_subject(text):
    """True when the ask itself names a catalog profile (every
    distinctive token of some catalog subject appears in the text)."""
    toks = set(_H._normalize_for_match(text).split())
    if not toks:
        return False
    try:
        for entry in _subject_rules.library_named(_C._profile_catalog_for_chat(), text):
            st = [w for w in _H._normalize_for_match(
                      str(entry.get('subject') or '')).split()
                  if w not in _C._PM_BASE_GENERIC_TOKENS]
            if st and sum(len(w) for w in st) >= 4 and set(st) <= toks:
                return True
    except Exception:
        traceback.print_exc()
    return False


__all__ = ['_PM_SPLIT_DIGIT_RE', '_pm_normalize_ask', '_PM_US_SHARE_RE', '_PM_COUNT_IN_REPLY_RE', '_pm_parse_count', '_pm_numeric_followup_reply', '_PM_STATUS_ASK_RES', '_PM_STATUS_TAIL_TOKENS', '_pm_status_ask_subject', '_pm_status_matching_runs', '_pm_status_reply_for_runs', '_PM_STATUS_LEAD_TOKENS', '_pm_library_match', '_pm_status_lane', '_PM_APPROVAL_WORD_RE', '_PM_APPROVE_NO_DRAFT_COPY', '_pm_last_agent_asked', '_pm_approval_word', '_PM_WO_WORK_NOUNS', '_PM_WO_STATUS_RES', '_PM_WO_ETA_RES', '_PM_WO_CANCEL_RE', '_pm_subiq_lookup_answer', '_pm_workorder_intent', '_pm_user_runs', '_pm_typical_build_minutes', '_pm_eta_line', '_pm_cancel_target', '_pm_workorder_reply', '_PM_CUT_INTENT_RE', '_PM_CUT_COHORT_RE', '_PM_CUT_IDIOM_RE', '_pm_text_names_catalog_subject']
