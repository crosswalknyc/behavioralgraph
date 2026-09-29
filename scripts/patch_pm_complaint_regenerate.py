#!/usr/bin/env python3
"""Wrong-answer complaints regenerate; replays never repeat; refusals retry.

From the 2026-09-28 daily review (Casey's Paramount+ second-screen ask):
1. "thats not what i asked for" hit ledger_replay and served the same
   rejected entry again. New intercept: complaint phrasing regenerates
   the PREVIOUS question fresh, skipping the replay path entirely.
2. The identical question re-asked four times in 90 seconds replayed
   the identical entry each time. New guard: a question this user was
   already served as a replay within the last 25 minutes runs fresh
   instead of replaying again.
3. Her first ask ("top titles inside Kids & Family") got a
   model-authored refusal ("could not lock the numbers... re-aim").
   New guard: a refusal-shaped or numberless answer retries once with
   a produce-the-read instruction, then falls back to the measured
   read pass (which carries the held-read email machinery).
"""
import sys
from pathlib import Path

APP = Path(sys.argv[1] if len(sys.argv) > 1 else "app.py")
src = APP.read_text(encoding="utf-8")


def splice(old, new, desc):
    global src
    n = src.count(old)
    if n != 1:
        raise RuntimeError(f"[{desc}] anchor found {n}x (need exactly 1)")
    src = src.replace(old, new)


# ---- 1. Module-level helpers, in front of the feedback pattern block ----
OLD_DEFS = "_PM_FEEDBACK_RES = ("
NEW_DEFS = '''_PM_WRONG_ANSWER_RES = (
    # "thats not what i asked for" and family (2026-09-28, Casey).
    re.compile(r"\\bnot\\s+what\\s+i\\s+(?:was\\s+)?ask(?:ed|ing)\\b",
               re.IGNORECASE),
    re.compile(r"\\b(?:wrong|incorrect)\\s+"
               r"(?:answer|read|response|data|numbers?)\\b", re.IGNORECASE),
    re.compile(r"\\b(?:that|this)\\s+(?:is|was|'?s)\\s+"
               r"(?:wrong|incorrect|not\\s+right)\\b", re.IGNORECASE),
    re.compile(r"\\b(?:you\\s+)?did\\s*n[o']?t\\s+answer\\s+"
               r"(?:my|the)\\b", re.IGNORECASE),
    re.compile(r"\\bdoes\\s*n[o']?t\\s+answer\\s+(?:my|the)\\s+"
               r"question\\b", re.IGNORECASE),
    re.compile(r"\\banswer\\s+(?:my|the)\\s+(?:actual\\s+)?"
               r"question\\b", re.IGNORECASE),
    re.compile(r"\\btry\\s+(?:that\\s+)?again\\b", re.IGNORECASE),
    re.compile(r"\\bstill\\s+(?:wrong|not\\s+right)\\b", re.IGNORECASE),
    re.compile(r"\\bre\\s*-?\\s*read\\s+my\\s+question\\b", re.IGNORECASE),
)


def _pm_prev_user_question(history, complaint):
    """The reader's last substantive question before a wrong-answer
    complaint: newest user turn that is not itself complaint-shaped
    and long enough to be a real ask."""
    try:
        for turn in reversed(list(history or [])):
            if str((turn or {}).get('role') or '') != 'user':
                continue
            t = str(turn.get('text') or '').strip()
            if not t or len(t) < 15:
                continue
            if t == str(complaint or '').strip():
                continue
            if any(rx.search(t) for rx in _PM_WRONG_ANSWER_RES):
                continue
            return t
    except Exception:
        pass
    return ''


def _pm_replay_repeat_block(pm_user, question, window_s=1500):
    """True when this user was ALREADY served a library replay for
    this same question within the window. Re-asking the identical
    question minutes after a replay means the stored read did not
    satisfy - the ask runs fresh instead of replaying again
    (2026-09-28, the same entry served four times in 90 seconds)."""
    try:
        import prometheus_memory as pmm
        qn = ' '.join(re.sub(r"[^a-z0-9 ]+", ' ',
                             str(question or '').lower()).split())
        if not qn or not pm_user:
            return False
        now = datetime.now(timezone.utc)
        for rec in (pmm.recall(pm_user, 12) or []):
            if str((rec or {}).get('route') or '') != 'replay':
                continue
            rqn = ' '.join(re.sub(r"[^a-z0-9 ]+", ' ',
                                  str(rec.get('question') or '')
                                  .lower()).split())
            if rqn != qn:
                continue
            try:
                ts = datetime.fromisoformat(
                    str(rec.get('ts') or '').replace('Z', '+00:00'))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if (now - ts).total_seconds() <= window_s:
                return True
        return False
    except Exception:
        return False


_PM_REFUSAL_RX = re.compile(
    r"(?:re-?\\s?aim|could\\s+not\\s+lock|cannot\\s+lock|"
    r"rather\\s+than\\s+guess|tell\\s+me\\s+the\\s+specific\\s+read|"
    r"name\\s+the\\s+cohort,\\s+the\\s+category|"
    r"rephrase\\s+(?:the|your)\\s+question|"
    r"ask\\s+(?:me\\s+)?(?:a\\s+|the\\s+)?(?:different|another)\\s+"
    r"question)", re.IGNORECASE)


def _pm_reads_as_refusal(reply, mode=''):
    """A generated answer that declines to answer, or carries no
    numbers at all, is not a read (2026-09-28: the model authored
    "could not lock the numbers... re-aim than guess" and it shipped).
    Mode chips are render-shape commands and skip the digit test."""
    t = str(reply or '')
    if not t:
        return False
    if _PM_REFUSAL_RX.search(t):
        return True
    if mode:
        return False
    digits = len(re.findall(r"\\d", t))
    return digits < 2 and len(t) < 900


_PM_FEEDBACK_RES = ('''
splice(OLD_DEFS, NEW_DEFS, "module defs")

# ---- 2. Complaint intercept in the analyze route ----
OLD_FB = """    _fb_user = (session.get('username') or user.get('username')
                or '').strip()
    if any(rx.search(text or '') for rx in _PM_FEEDBACK_RES):"""
NEW_FB = """    _fb_user = (session.get('username') or user.get('username')
                or '').strip()
    # WRONG-ANSWER COMPLAINT (2026-09-28, Casey's second-screen ask):
    # "thats not what i asked for" reruns the PREVIOUS question fresh.
    # It never replays the entry the reader just rejected, and it
    # never gets brushed off as artwork feedback.
    _pm_skip_replay = False
    if any(rx.search(text or '') for rx in _PM_WRONG_ANSWER_RES):
        _pm_forward_user_feedback(_fb_user, text, 'wrong_answer')
        _prev_q = _pm_prev_user_question(history, text)
        if _prev_q:
            text = _prev_q
            _pm_skip_replay = True
            _pm_ask_hint(route='complaint_regenerate')
        else:
            _pm_ask_hint(route='complaint_regenerate',
                         outcome='asked_what')
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': ('My fault - tell me what you were after '
                          '(the subject and the read you want) and I '
                          'will run it fresh right now.'),
                'followups': [], 'offer_deck': False,
                'deck_angle': None})
    if any(rx.search(text or '') for rx in _PM_FEEDBACK_RES):"""
splice(OLD_FB, NEW_FB, "complaint intercept")

# ---- 3. Replay return honors the flag + the repeat guard ----
OLD_RP = """    if _led_exact and _led_exact.get('reply') and _led_same_subject \\
            and _led_subj and not mode:"""
NEW_RP = """    if _led_exact and _led_exact.get('reply') and _led_same_subject \\
            and _led_subj and not mode and not _pm_skip_replay \\
            and not _pm_replay_repeat_block(_pm_user, text):"""
splice(OLD_RP, NEW_RP, "replay guard")

# ---- 4. Refusal guard on the generated answer ----
OLD_SCRUB = """    # Defense-in-depth vocabulary pass (2026-08-26): banned internal
    # terms replaced and em dashes stripped before the text reaches
    # the user. Mirrors the partner API's _V1_BANNED_TOKENS posture.
    reply = pma.scrub_user_text(reply)
    followups = [pma.scrub_user_text(str(f).strip())[:160]
                 for f in (data.get('followups') or [])
                 if str(f).strip()][:4]"""
NEW_SCRUB = """    # Defense-in-depth vocabulary pass (2026-08-26): banned internal
    # terms replaced and em dashes stripped before the text reaches
    # the user. Mirrors the partner API's _V1_BANNED_TOKENS posture.
    reply = pma.scrub_user_text(reply)
    # Refusal guard (2026-09-28): a draft that declines to answer or
    # carries no numbers retries once with a produce-the-read
    # instruction; a second refusal reroutes to the measured-read
    # pass, which owns the follow-up delivery machinery.
    if action == 'answer' and _pm_reads_as_refusal(reply, mode):
        _t_refuse = time.monotonic()
        _r2 = _pm_claude_json(
            pma.ANALYSIS_SYSTEM_PROMPT,
            user_prompt + (
                "\\n\\nYour previous draft declined to answer. That is "
                "not acceptable. Produce the read now: state the "
                "numbers for exactly what was asked, derived from the "
                "measures above. Do not ask the reader to rephrase, "
                "narrow, or pick a different question."),
            max_tokens=_max_tok, temperature=0.4,
            usage_extras=_pm_ppu)
        _pm_ask_stage('refusal_retry', t0=_t_refuse)
        _d2 = (_r2.get('data') or {}) if _r2.get('success') else {}
        if isinstance(_d2, list):
            _d2 = next((d for d in _d2 if isinstance(d, dict)), {})
        _reply2 = pma.scrub_user_text(
            str(_d2.get('reply') or '').strip())
        if _reply2 and not _pm_reads_as_refusal(_reply2, mode):
            reply = _reply2
            data = _d2
        else:
            return _pm_generate_metrics_response(
                user, text, history,
                metric_request={
                    'subject': (p_meta.get('name')
                                if ctx.get('primary') else '') or '',
                    'needed': text[:200]},
                anchors_block=xmod_block, charge_done=True,
                ctx=ctx, digest_block=digest or '')
    followups = [pma.scrub_user_text(str(f).strip())[:160]
                 for f in (data.get('followups') or [])
                 if str(f).strip()][:4]"""
splice(OLD_SCRUB, NEW_SCRUB, "refusal guard")

APP.write_text(src, encoding="utf-8")
print("complaint/replay/refusal patch applied (4 sites)")
