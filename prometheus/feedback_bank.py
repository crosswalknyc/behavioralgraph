"""What a reader says back: the email-when-ready and wrong-answer intercepts, the repeat and rejected-answer replays, the refusal reader, the feedback forward, the regression bank, the cannot-do lane, and the build notify registration.

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
import json
import threading
import traceback
import re
from prometheus import faults as _faults
import hashlib
import time
from datetime import datetime, timedelta, date, timezone

# <<moved>>
# <<block: 1>>
# DELIVERY + FEEDBACK INTERCEPTS (2026-09-28, the W39 weekly review):
# "please email david carter@... when it is ready" and "the key art is
# from an older series, please replace it" each burned a full paid
# read. A delivery request registers the ready-notification on the
# caller's in-flight builds; product feedback and methodology
# challenges acknowledge, forward to ops, and never generate.
_PM_EMAIL_ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")


# <<block: 2>>
_PM_EMAIL_WHEN_READY_RE = re.compile(
    # '.' stays inside the spans: addresses carry dots
    # (david.carter@spe.sony.com). 2026-09-30: notify / tell /
    # ping / alert / let me know / update me all register the
    # ready-notification, same as email.
    r"\b(?:e-?mail|send|notify|tell|ping|alert|update|let\s+me\s+know)\b"
    r"[^?!\n]{0,80}?\bwhen\b[^?!\n]{0,30}?"
    r"\b(?:ready|done|finish(?:e[sd])?|complete[sd]?|lands?|arrives?)\b",
    re.IGNORECASE)


# <<block: 3>>
_PM_WRONG_ANSWER_RES = (
    # "thats not what i asked for" and family (2026-09-28, Casey).
    re.compile(r"\bnot\s+what\s+i\s+(?:was\s+)?ask(?:ed|ing)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:wrong|incorrect)\s+"
               r"(?:answer|read|response|data|numbers?)\b", re.IGNORECASE),
    re.compile(r"\b(?:that|this)\s+(?:is|was|'?s)\s+"
               r"(?:wrong|incorrect|not\s+right)\b", re.IGNORECASE),
    re.compile(r"\b(?:you\s+)?did\s*n[o']?t\s+answer\s+"
               r"(?:my|the)\b", re.IGNORECASE),
    re.compile(r"\bdoes\s*n[o']?t\s+answer\s+(?:my|the)\s+"
               r"question\b", re.IGNORECASE),
    re.compile(r"\banswer\s+(?:my|the)\s+(?:actual\s+)?"
               r"question\b", re.IGNORECASE),
    re.compile(r"\btry\s+(?:that\s+)?again\b", re.IGNORECASE),
    re.compile(r"\bstill\s+(?:wrong|not\s+right)\b", re.IGNORECASE),
    re.compile(r"\bre\s*-?\s*read\s+my\s+question\b", re.IGNORECASE),
)


# <<block: 4>>
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
            if any(rx.search(t) for rx in _C._PM_WRONG_ANSWER_RES):
                continue
            return t
    except Exception as _fx:
        _faults.note('chat', _fx)
    return ''


# <<block: 5>>
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


# <<block: 6>>
def _pm_replay_rejected_in_thread(entry, history):
    """True when the stored entry's reply already appeared in this thread
    and the reader's next message pushed back on it (prometheus.user_signal.
    rejected_reply). A replay of those words is the loop Jessie hit on
    2026-11-04; the ask runs fresh instead. Never raises."""
    try:
        from prometheus import user_signal as _us
        reply = str((entry or {}).get('reply') or '')
        if reply and _us.rejected_reply(reply, history):
            print(f"[pm-replay] stored read {str((entry or {}).get('k') or '')!r} was pushed back on in this thread; running fresh")
            return True
    except Exception as _fx:
        _faults.note('chat', _fx)
    return False


# <<block: 7>>
_PM_REFUSAL_RX = re.compile(
    r"(?:re-?\s?aim|could\s+not\s+lock|cannot\s+lock|"
    r"rather\s+than\s+guess|tell\s+me\s+the\s+specific\s+read|"
    r"name\s+the\s+cohort,\s+the\s+category|"
    r"rephrase\s+(?:the|your)\s+question|"
    r"ask\s+(?:me\s+)?(?:a\s+|the\s+)?(?:different|another)\s+"
    r"question)", re.IGNORECASE)


# <<block: 8>>
def _pm_reads_as_refusal(reply, mode=''):
    """A generated answer that declines to answer, or carries no
    numbers at all, is not a read (2026-09-28: the model authored
    "could not lock the numbers... re-aim than guess" and it shipped).
    Mode chips are render-shape commands and skip the digit test."""
    t = str(reply or '')
    if not t:
        return False
    if _C._PM_REFUSAL_RX.search(t):
        return True
    if mode:
        return False
    digits = len(re.findall(r"\d", t))
    return digits < 2 and len(t) < 900


# <<block: 9>>
_PM_FEEDBACK_RES = (
    # wrong / stale artwork, images, titles on a card
    re.compile(r"\b(?:key\s*art|artwork|thumbnail|poster|image|logo)\b"
               r"[^.?!\n]{0,60}\b(?:wrong|older|old|incorrect|replace|"
               r"outdated|from an?\b)", re.IGNORECASE),
    re.compile(r"\breplace\s+(?:the|this|that)\s+"
               r"(?:key\s*art|artwork|thumbnail|poster|image|logo)\b",
               re.IGNORECASE),
)


# <<block: 10>>
_PM_METHOD_CHALLENGE_RES = (   # "how is X calculated" is a definition question, answered, never a challenge (Jenna 2026-10-14)
    re.compile(r"\b(?:seems?|looks?|is)\s+(?:very|too|way too)\s+"
               r"(?:high|low)\b", re.IGNORECASE),
)


# <<block: 11>>
_PM_FEEDBACK_SENT = {}


# <<block: 12>>
_PM_REGRESSION_CASES_KEY = 'system/pm_regression_cases.json'


# <<block: 13>>
def _pm_regression_q_key(text):
    """Stable key for one question: the shared fold (prometheus.question_key), md5[:16]."""
    from prometheus import question_key as _qk   # local: this function is extracted by source in a test
    return _qk.key(text)


# <<block: 14>>
def _pm_compact_for_bank(value, depth=0):
    """Trim a view-context payload for the regression registry: long
    strings cut, lists and dicts capped, depth capped. Keeps the
    on-screen vocabulary the nightly replay matches against."""
    if depth >= 4:
        return None
    if isinstance(value, str):
        return value[:400]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_C._pm_compact_for_bank(v, depth + 1)
                for v in value[:25]]
    if isinstance(value, dict):
        out = {}
        for k in list(value.keys())[:40]:
            out[str(k)[:80]] = _C._pm_compact_for_bank(
                value[k], depth + 1)
        return out
    return str(value)[:200]


# <<block: 15>>
def _pm_bank_regression_case(username, question, complaint, history,
                             view_ctx):
    """A question the reader called wrong becomes a permanent nightly
    regression case (Jenna 2026-09-30: a mistake fixed once stays
    fixed). Registry: system/pm_regression_cases.json, replayed every
    night by migration/pm_regression_nightly.py on the build server.
    Dedupe by normalized question. Fire-and-forget off the request
    thread; never raises into the chat path."""
    def _bank():
        try:
            key = _C._pm_regression_q_key(question)
            if not key or not str(question or '').strip():
                return
            try:
                resp = _H.s3_client.get_object(
                    Bucket=_H.S3_BUCKET, Key=_C._PM_REGRESSION_CASES_KEY)
                doc = json.loads(resp['Body'].read().decode('utf-8'))
            except Exception:
                doc = {}
            cases = doc.get('cases') if isinstance(doc, dict) else None
            if not isinstance(cases, list):
                cases = []
            if any(isinstance(c, dict) and c.get('id') == key
                   for c in cases):
                return
            rejected = ''
            for turn in reversed(list(history or [])):
                if isinstance(turn, dict)                         and turn.get('role') != 'user':
                    rejected = str(turn.get('text') or '')[:400]
                    break
            # The learning loop (2026-10-06): the figures the rejected
            # reply banked leave the corpus catalog, so the regenerated
            # answer and every later one cannot lean on them.
            try:
                from migration import corpus_catalog as _cc_rej
                _full_rejected = ''
                for turn in reversed(list(history or [])):
                    if isinstance(turn, dict) and turn.get('role') != 'user':
                        _full_rejected = str(turn.get('text') or '')
                        break
                _tid = str(getattr(_C._PM_REQ_THREAD, 'tid', '') or '')
                if not _tid and username:
                    try:
                        _tid = str((_C._load_threads_index(username) or {}).get('active') or '')
                    except Exception:
                        _tid = ''
                if _full_rejected:
                    _cc_rej.retire_answer(username or '', _tid, _full_rejected)
            except Exception:
                traceback.print_exc()
            vc = view_ctx if isinstance(view_ctx, dict) else {}
            view_id = str(vc.get('view_id') or '').strip()
            view_data = (_C._pm_compact_for_bank(vc.get('data'))
                         if vc.get('data') else None)
            subj = ''
            try:
                import prometheus_analysis as pma
                subj = str(pma.guess_subject_from_text(question)
                           or '').strip()
            except Exception:
                subj = ''
            blob_len = (len(json.dumps(view_data))
                        if view_data is not None else 0)
            if view_id and blob_len >= 80:
                check = 'view_grounded'
            elif subj:
                check = 'subject_named'
            else:
                check = 'context_followup'
            cases.append({
                'id': key,
                'created': _C._pm_iso_now(),
                'user': str(username or '')[:80],
                'question': str(question or '')[:500],
                'complaint': str(complaint or '')[:300],
                'rejected_answer': rejected,
                'view_id': view_id,
                'view_title': str(vc.get('view_title') or '')[:200],
                'view_data': view_data,
                'subject_at_capture': subj[:120],
                'check': check,
                'muted': False,
            })
            _H.s3_client.put_object(
                Bucket=_H.S3_BUCKET, Key=_C._PM_REGRESSION_CASES_KEY,
                Body=json.dumps({'cases': cases}).encode('utf-8'),
                ContentType='application/json')
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_bank, daemon=True).start()


# <<block: 16>>
def _pm_forward_user_feedback(username, text, kind):
    """One SES note to ops per distinct feedback message per hour.
    Never raises, never blocks the reply. A probe never mails."""
    try:
        if _C._pm_probe_caller() or _C._pm_is_probe_user(username):
            return
        sig = hashlib.md5(
            f"{username}|{str(text)[:200]}".encode()).hexdigest()
        now = time.time()
        if now - _C._PM_FEEDBACK_SENT.get(sig, 0) < 3600:
            return
        _C._PM_FEEDBACK_SENT[sig] = now

        def _send():
            try:
                import boto3 as _b3
                ses = _b3.client('ses', region_name='us-east-2')
                ses.send_email(
                    Source='BehavioralGraph <jenna@crosswalknyc.com>',
                    Destination={'ToAddresses': [
                        'jenna@crosswalknyc.com',
                        'jessie@crosswalknyc.com']},
                    Message={
                        'Subject': {'Data':
                                    f'Prometheus user feedback: '
                                    f'{username or "unknown"}'},
                        'Body': {'Text': {'Data': (
                            f'Kind: {kind}\n'
                            f'User: {username}\n\n'
                            f'{str(text)[:1500]}\n')}}})
            except Exception:
                traceback.print_exc()
        threading.Thread(target=_send, daemon=True).start()
    except Exception:
        traceback.print_exc()


# <<block: 17>>
def _pm_cant_do_lane(username, text):
    """(reply, chips, kind) when the message asks for an action this
    chat cannot take (add a user, refund credits, rename or re-image a
    profile, edit a number by hand, contact a third party, set up a
    recurring run, push a file into another tool), else None. The
    note goes to the team on the same hourly-deduped path as product
    feedback; the reply says what Prometheus can do instead. No model
    call (2026-10-02 S8)."""
    try:
        from prometheus import capabilities as _pc
        if not _pc.enabled():
            return None
        kind = _pc.action_request(text)
        if not kind:
            return None
        reply, chips = _pc.cant_do_reply(kind)
        _C._pm_forward_user_feedback(username, text, 'action_request_' + kind)
        return reply, chips, kind
    except Exception:
        traceback.print_exc()
        return None


# <<block: 18>>
_PM_BUILD_NOTIFY_PREFIX = 'system/pm_build_notify/'


# <<block: 19>>
def _pm_register_build_notify(run_id, emails, requested_by):
    """Stash a ready-notification opt-in for one queue run. The worker
    merges these into the recipient list of the branded completion
    email at send time."""
    try:
        _H.s3_client.put_object(
            Bucket=_H.S3_BUCKET,
            Key=f"{_C._PM_BUILD_NOTIFY_PREFIX}{run_id}.json",
            Body=json.dumps({
                'run_id': run_id,
                'emails': list(emails),
                'requested_by': requested_by,
                'requested_at': time.time(),
            }).encode('utf-8'),
            ContentType='application/json')
        return True
    except Exception:
        traceback.print_exc()
        return False


# <<block: 20>>
_PM_EMAIL_ADDR_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


__all__ = ['_PM_EMAIL_ADDR_RE', '_PM_EMAIL_WHEN_READY_RE', '_PM_WRONG_ANSWER_RES', '_pm_prev_user_question', '_pm_replay_repeat_block', '_pm_replay_rejected_in_thread', '_PM_REFUSAL_RX', '_pm_reads_as_refusal', '_PM_FEEDBACK_RES', '_PM_METHOD_CHALLENGE_RES', '_PM_FEEDBACK_SENT', '_PM_REGRESSION_CASES_KEY', '_pm_regression_q_key', '_pm_compact_for_bank', '_pm_bank_regression_case', '_pm_forward_user_feedback', '_pm_cant_do_lane', '_PM_BUILD_NOTIFY_PREFIX', '_pm_register_build_notify']
