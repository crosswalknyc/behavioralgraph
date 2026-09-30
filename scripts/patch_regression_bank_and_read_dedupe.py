#!/usr/bin/env python3
"""Self-growing regression set + in-flight dedupe on analysis asks.

Jenna 2026-09-30:
1. "Every time a user says an answer was wrong and it regenerates,
   that question should become a permanent nightly regression case
   automatically, so a mistake fixed once stays fixed."
2. "Same user sends the same question while one is running: reply
   with progress instead of running it twice (builds already have
   this; analysis doesn't)."
"""
import ast
from pathlib import Path

P = Path(__file__).resolve().parent.parent / 'app.py'
src = P.read_text(encoding='utf-8')


def splice(s, old, new, desc):
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f"[{desc}] anchor count {n}")
    return s.replace(old, new)


# ------------------------------------------------------------------
# Edit 1: regression-bank helpers, ahead of the feedback forwarder.
# ------------------------------------------------------------------
old1 = "def _pm_forward_user_feedback(username, text, kind):"
new1 = '''_PM_REGRESSION_CASES_KEY = 'system/pm_regression_cases.json'


def _pm_regression_q_key(text):
    """Stable key for one question: casefold, alnum-only, md5[:16]."""
    norm = re.sub(r'[^a-z0-9]+', ' ',
                  str(text or '').casefold()).strip()
    return hashlib.md5(norm.encode('utf-8')).hexdigest()[:16]


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
        return [_pm_compact_for_bank(v, depth + 1)
                for v in value[:25]]
    if isinstance(value, dict):
        out = {}
        for k in list(value.keys())[:40]:
            out[str(k)[:80]] = _pm_compact_for_bank(
                value[k], depth + 1)
        return out
    return str(value)[:200]


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
            key = _pm_regression_q_key(question)
            if not key or not str(question or '').strip():
                return
            try:
                resp = s3_client.get_object(
                    Bucket=S3_BUCKET, Key=_PM_REGRESSION_CASES_KEY)
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
                if isinstance(turn, dict) \
                        and turn.get('role') != 'user':
                    rejected = str(turn.get('text') or '')[:400]
                    break
            vc = view_ctx if isinstance(view_ctx, dict) else {}
            view_id = str(vc.get('view_id') or '').strip()
            view_data = (_pm_compact_for_bank(vc.get('data'))
                         if vc.get('data') else None)
            subj = ''
            try:
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
                'created': _pm_iso_now(),
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
            s3_client.put_object(
                Bucket=S3_BUCKET, Key=_PM_REGRESSION_CASES_KEY,
                Body=json.dumps({'cases': cases}).encode('utf-8'),
                ContentType='application/json')
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_bank, daemon=True).start()


def _pm_forward_user_feedback(username, text, kind):'''
src = splice(src, old1, new1, 'bank helpers')

# ------------------------------------------------------------------
# Edit 2: capture at the complaint-regenerate point, before the
# rerun reassigns text.
# ------------------------------------------------------------------
old2 = """        _prev_q = _pm_prev_user_question(history, text)
        if _prev_q:
            text = _prev_q"""
new2 = """        _prev_q = _pm_prev_user_question(history, text)
        if _prev_q:
            # The rejected question joins the nightly regression set
            # (Jenna 2026-09-30) with the complaint and the on-screen
            # context it failed under.
            _pm_bank_regression_case(
                _fb_user, _prev_q, text, history,
                (body.get('page_context') or {}).get('view_context'))
            text = _prev_q"""
src = splice(src, old2, new2, 'capture wire')

# ------------------------------------------------------------------
# Edit 3: in-flight read index helpers, after the status writer.
# ------------------------------------------------------------------
old3 = '''def _pm_read_status_write(job_id, payload):
    s3_client.put_object(
        Bucket=S3_BUCKET, Key=f"{_PM_READ_PREFIX}{job_id}.json",
        Body=json.dumps(payload).encode('utf-8'),
        ContentType='application/json')
'''
new3 = '''def _pm_read_status_write(job_id, payload):
    s3_client.put_object(
        Bucket=S3_BUCKET, Key=f"{_PM_READ_PREFIX}{job_id}.json",
        Body=json.dumps(payload).encode('utf-8'),
        ContentType='application/json')


_PM_READ_INFLIGHT_PREFIX = 'system/prometheus_reads/_inflight/'


def _pm_read_inflight_doc_key(user):
    safe = re.sub(r'[^a-z0-9_.-]+', '_',
                  str(user or 'anon').strip().lower()) or 'anon'
    return f"{_PM_READ_INFLIGHT_PREFIX}{safe}.json"


def _pm_read_inflight_check(user, text):
    """The caller's own running job for this exact question, or None.
    A same-question re-send attaches to the running job instead of
    starting a second copy (Jenna 2026-09-30; builds already had
    this). Self-expiring: the entry only counts while the job status
    still says working and it started under 15 minutes ago."""
    try:
        qk = _pm_regression_q_key(text)
        resp = s3_client.get_object(
            Bucket=S3_BUCKET, Key=_pm_read_inflight_doc_key(user))
        doc = json.loads(resp['Body'].read().decode('utf-8'))
        ent = doc.get(qk) if isinstance(doc, dict) else None
        if not isinstance(ent, dict):
            return None
        if time.time() - float(ent.get('started_at') or 0) > 900:
            return None
        job_id = str(ent.get('job_id') or '')
        if not job_id:
            return None
        st = s3_client.get_object(
            Bucket=S3_BUCKET, Key=f"{_PM_READ_PREFIX}{job_id}.json")
        status = json.loads(st['Body'].read().decode('utf-8'))
        if str(status.get('status') or '') != 'working':
            return None
        return {'job_id': job_id,
                'stage': str(status.get('stage') or '').strip()}
    except Exception:
        return None


def _pm_read_inflight_mark(user, text, job_id):
    """Record the running job under the caller's question key. Prunes
    entries past the 15 minute window on every write. Never raises."""
    try:
        qk = _pm_regression_q_key(text)
        key = _pm_read_inflight_doc_key(user)
        try:
            resp = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
            doc = json.loads(resp['Body'].read().decode('utf-8'))
        except Exception:
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        now = time.time()
        doc = {k: v for k, v in doc.items()
               if isinstance(v, dict)
               and now - float(v.get('started_at') or 0) <= 900}
        doc[qk] = {'job_id': job_id, 'started_at': now,
                   'question': str(text or '')[:200]}
        s3_client.put_object(
            Bucket=S3_BUCKET, Key=key,
            Body=json.dumps(doc).encode('utf-8'),
            ContentType='application/json')
    except Exception:
        traceback.print_exc()
'''
src = splice(src, old3, new3, 'inflight helpers')

# ------------------------------------------------------------------
# Edit 4: dedupe gate ahead of the fresh read job spawn.
# ------------------------------------------------------------------
old4 = """    if async_fresh:
        job_id = uuid.uuid4().hex[:12]
        _pm_read_status_write(job_id, {
            'job_id': job_id, 'user': _pm_user, 'status': 'working',
            'stage': 'reading the data',
            'question': text[:300], 'started_at': time.time()})
        threading.Thread("""
new4 = """    if async_fresh:
        # Same user, same question, while the first copy still runs:
        # hand back the running job's progress instead of a second
        # run (Jenna 2026-09-30). A paid report re-send refunds the
        # fresh charge before attaching to the running copy.
        _dup_read = _pm_read_inflight_check(_pm_user, text)
        if _dup_read:
            if panel_charge is not None:
                try:
                    _pm_panel_refund(panel_charge)
                except Exception:
                    traceback.print_exc()
            _pm_ask_hint(route='read_inflight_dedupe',
                         outcome='answered',
                         subject=base.get('subject'))
            _stage = (_dup_read.get('stage')
                      or 'working through the data')
            return jsonify({
                'success': True, 'action': 'answer',
                'read_job_id': _dup_read['job_id'],
                'reply': ('Already on it - that exact read is '
                          'running now (' + _stage + '). It lands '
                          'right here the moment it is ready, and I '
                          'did not start a second copy.'),
                'followups': [], 'offer_deck': False,
                'deck_angle': None})
        job_id = uuid.uuid4().hex[:12]
        _pm_read_status_write(job_id, {
            'job_id': job_id, 'user': _pm_user, 'status': 'working',
            'stage': 'reading the data',
            'question': text[:300], 'started_at': time.time()})
        _pm_read_inflight_mark(_pm_user, text, job_id)
        threading.Thread("""
src = splice(src, old4, new4, 'dedupe gate')

ast.parse(src)
P.write_text(src, encoding='utf-8')
print('all four edits applied, ast clean')
