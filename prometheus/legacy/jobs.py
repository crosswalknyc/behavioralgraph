"""Prometheus job status (2026-10-02 RCA W3, second family out of chat.py).

The six job status writers (read, deck, Brand Partnership IQ, Digital
Journey IQ, Flywheel, Attribution IQ), the owner check, the queue poll
for a profile build and the six status routes the widget polls. Every
writer stamps ``pm.job.v1`` (prometheus.seams). Reads the host through
``_H`` and the legacy chat module through ``_C``; see threads.py for the
pattern. Behavior unchanged from chat.py.
"""
import json
import re
import traceback
from flask import jsonify, request, session

from prometheus.legacy import H as _H, C as _C  # noqa: E402
from prometheus import seams as _seams  # noqa: E402

__all__ = ['api_synth_chat_status', '_pm_job_owner_ok', '_pm_read_status_write', 'api_synth_chat_read_status', '_pm_deck_status_write', '_pm_bpiq_status_write', '_pm_jiq_status_write', '_pm_fw_status_write', '_pm_aiq_status_write', 'api_synth_chat_deck_status', 'api_synth_chat_bpiq_status', 'api_synth_chat_jiq_status', 'api_synth_chat_fw_status', 'api_synth_chat_aiq_status']


@_H.app.route('/api/brief-chat/status/<run_id>', methods=['GET'])
@_H.app.route('/api/synth-chat/status/<run_id>', methods=['GET'])  # legacy alias
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/status')
def api_synth_chat_status(run_id):
    """Poll Hetzner queue for run status.

    Session-authenticated dashboard users only. Partner API keys must
    use GET /api/v1/profiles/<run_id> instead - that path scrubs
    internal cohort/status enum values before returning JSON.
    """
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    if not _H.SYNTH_QUEUE_SECRET or not _H.SYNTH_QUEUE_URL:
        _H._chatbot_error_email('brief-chat/status',
                             'profile engine not configured '
                             '(queue URL/secret missing)',
                             tb='(configuration check)')
        return jsonify(_H._chatbot_calm_payload())
    try:
        import requests as _requests
        resp = _requests.get(
            f"{_H.SYNTH_QUEUE_URL}/synth/status/{run_id}", timeout=15,
            headers={'X-Synth-Auth': _H.SYNTH_QUEUE_SECRET},
        )
        if resp.status_code == 404:
            return jsonify({'success': True,
                             'status': {'run_id': run_id, 'status': 'unknown'}})
        if resp.status_code != 200:
            _H._chatbot_error_email(
                'brief-chat/status',
                f'status returned {resp.status_code}: '
                f'{_H._clean_queue_error_text(resp.text)[:400]}',
                tb=str(resp.text or '')[:2000] or '(empty status reply)')
            return jsonify(_H._chatbot_calm_payload())
        doc = resp.json() or {}
        # Terminal run failure: the chat renders the calm line; the
        # real failure detail goes to ops by email (deduped per run
        # via the signature hash) and is scrubbed from the response.
        if str(doc.get('status') or '').strip().lower() in ('error',
                                                            'failed'):
            _H._chatbot_error_email(
                'brief-chat/status',
                f"run {run_id} finished with status="
                f"{doc.get('status')}: "
                f"{str(doc.get('error') or '')[:400]}",
                tb='(run failure reported by the engine status feed)')
            doc = dict(doc)
            doc['error'] = ''
        payload = {'success': True, 'status': doc}
        # A finished Prometheus pull belongs to the user who pulled it.
        # Explicit-list users (self-serve Prometheus plan) get the TU
        # and Avid keys appended to allowed_runs; '*' users are a no-op.
        try:
            if str(doc.get('status') or '').strip().lower() == 'complete':
                from site_signup import grant_runs_to_user as _grant_runs
                _grant_runs(session.get('username'), [
                    doc.get('tu_s3_key') or doc.get('s3_key')
                    or doc.get('output_key'),
                    doc.get('avid_s3_key'),
                ])
        except Exception:
            traceback.print_exc()
        # Build-first follow-through (2026-09-24 Jenna): a completed
        # run whose subject matches a stashed question hands the
        # question back so the chat re-asks it automatically against
        # the fresh base. One-shot: the pop clears the stash entry.
        try:
            if str(doc.get('status') or '').strip().lower() == 'complete':
                _pq_user = (user.get('username') or user.get('email')
                            or '')
                _pq = _C._pm_pop_pending_question(
                    _pq_user, str(doc.get('subject') or ''))
                if _pq:
                    payload['pending_question'] = _pq
        except Exception:
            traceback.print_exc()
        return jsonify(payload)
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/status', e)
        return jsonify(_H._chatbot_calm_payload())


def _pm_job_owner_ok(payload_user, user):
    """True when this session may see the polled job (2026-09-25,
    keith's 403s). The job status stores the session username at
    kickoff; the gate's user dict historically carried only the email
    when the record lacked a username field. Accept any of the
    caller's identities (username, email, session username) so an
    owner can never be locked out of their own job; super admins see
    everything; a job with no recorded owner stays visible."""
    po = str(payload_user or '').strip().lower()
    if not po:
        return True
    if str(user.get('role') or '').strip().lower() == 'super_admin':
        return True
    idents = {str(user.get('username') or '').strip().lower(),
              str(user.get('email') or '').strip().lower()}
    try:
        idents.add(str(session.get('username') or '').strip().lower())
    except Exception:
        pass
    idents.discard('')
    return po in idents


def _pm_read_status_write(job_id, payload):
    payload = _seams.tag_job(payload, 'read')
    _H.s3_client.put_object(
        Bucket=_H.S3_BUCKET, Key=f"{_C._PM_READ_PREFIX}{job_id}.json",
        Body=json.dumps(payload).encode('utf-8'),
        ContentType='application/json')


@_H.app.route('/api/brief-chat/read-status/<job_id>', methods=['GET'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/read-status')
def api_synth_chat_read_status(job_id):
    """Poll one background generated read. Mirrors deck-status: the
    payload is the exact analyze response the sync path would have
    returned; on 'error' the widget shows the calm message."""
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    if not re.fullmatch(r'[0-9a-f]{12}', str(job_id or '')):
        return jsonify({'success': False, 'error': 'bad job id'}), 400
    try:
        resp = _H.s3_client.get_object(
            Bucket=_H.S3_BUCKET, Key=f"{_C._PM_READ_PREFIX}{job_id}.json")
        payload = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return jsonify({'success': False, 'error': 'unknown job'}), 404
    uname = (user.get('username') or user.get('email') or '').strip()
    if not _pm_job_owner_ok(payload.get('user'), user):
        return jsonify({'success': False, 'error': 'not your job'}), 403
    # A read a deploy stranded is picked back up from the poll itself,
    # and a resumed read's result answers under the original job id.
    try:
        from prometheus import read_recovery as _rr
        payload = _rr.follow(payload)
    except Exception:
        traceback.print_exc()
    payload.pop('resume', None)
    return jsonify({'success': True, **payload})


def _pm_deck_status_write(job_id, payload):
    payload = _seams.tag_job(payload, 'deck')
    _H.s3_client.put_object(
        Bucket=_H.S3_BUCKET, Key=f"{_C._PM_DECK_PREFIX}{job_id}.json",
        Body=json.dumps(payload).encode('utf-8'),
        ContentType='application/json')


def _pm_bpiq_status_write(job_id, payload):
    payload = _seams.tag_job(payload, 'bpiq')
    _H.s3_client.put_object(
        Bucket=_H.S3_BUCKET, Key=f"{_C._PM_BPIQ_JOB_PREFIX}{job_id}.json",
        Body=json.dumps(payload).encode('utf-8'),
        ContentType='application/json')


def _pm_jiq_status_write(job_id, payload):
    payload = _seams.tag_job(payload, 'jiq')
    _H.s3_client.put_object(
        Bucket=_H.S3_BUCKET, Key=f"{_C._PM_JIQ_JOB_PREFIX}{job_id}.json",
        Body=json.dumps(payload).encode('utf-8'),
        ContentType='application/json')


def _pm_fw_status_write(job_id, payload):
    payload = _seams.tag_job(payload, 'fw')
    _H.s3_client.put_object(
        Bucket=_H.S3_BUCKET, Key=f"{_C._PM_FW_JOB_PREFIX}{job_id}.json",
        Body=json.dumps(payload).encode('utf-8'),
        ContentType='application/json')


def _pm_aiq_status_write(job_id, payload):
    payload = _seams.tag_job(payload, 'aiq')
    _H.s3_client.put_object(
        Bucket=_H.S3_BUCKET, Key=f"{_C._PM_AIQ_JOB_PREFIX}{job_id}.json",
        Body=json.dumps(payload).encode('utf-8'),
        ContentType='application/json')


@_H.app.route('/api/brief-chat/deck-status/<job_id>', methods=['GET'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/deck-status')
def api_synth_chat_deck_status(job_id):
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    if not re.fullmatch(r'[0-9a-f]{12}', str(job_id or '')):
        return jsonify({'success': False, 'error': 'bad job id'}), 400
    try:
        resp = _H.s3_client.get_object(
            Bucket=_H.S3_BUCKET, Key=f"{_C._PM_DECK_PREFIX}{job_id}.json")
        payload = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return jsonify({'success': False, 'error': 'unknown job'}), 404
    uname = (user.get('username') or user.get('email') or '').strip()
    if not _pm_job_owner_ok(payload.get('user'), user):
        return jsonify({'success': False, 'error': 'not your job'}), 403
    payload = dict(payload)
    payload.pop('resume', None)   # the queued build's arguments stay server-side
    if str(payload.get('status') or '').strip().lower() == 'error':
        # The deck worker already emailed the failure to ops; the
        # poll reply carries no failure detail (calm-failure contract).
        payload['error'] = ''
    return jsonify({'success': True, **payload})


@_H.app.route('/api/brief-chat/bpiq-status/<job_id>', methods=['GET'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/bpiq-status')
def api_synth_chat_bpiq_status(job_id):
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    if not re.fullmatch(r'[0-9a-f]{12}', str(job_id or '')):
        return jsonify({'success': False, 'error': 'bad job id'}), 400
    try:
        resp = _H.s3_client.get_object(
            Bucket=_H.S3_BUCKET, Key=f"{_C._PM_BPIQ_JOB_PREFIX}{job_id}.json")
        payload = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return jsonify({'success': False, 'error': 'unknown job'}), 404
    uname = (user.get('username') or user.get('email') or '').strip()
    if not _pm_job_owner_ok(payload.get('user'), user):
        return jsonify({'success': False, 'error': 'not your job'}), 403
    return jsonify({'success': True, **payload})


@_H.app.route('/api/brief-chat/jiq-status/<job_id>', methods=['GET'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/jiq-status')
def api_synth_chat_jiq_status(job_id):
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    if not re.fullmatch(r'[0-9a-f]{12}', str(job_id or '')):
        return jsonify({'success': False, 'error': 'bad job id'}), 400
    try:
        resp = _H.s3_client.get_object(
            Bucket=_H.S3_BUCKET, Key=f"{_C._PM_JIQ_JOB_PREFIX}{job_id}.json")
        payload = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return jsonify({'success': False, 'error': 'unknown job'}), 404
    uname = (user.get('username') or user.get('email') or '').strip()
    if not _pm_job_owner_ok(payload.get('user'), user):
        return jsonify({'success': False, 'error': 'not your job'}), 403
    return jsonify({'success': True, **payload})


@_H.app.route('/api/brief-chat/fw-status/<job_id>', methods=['GET'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/fw-status')
def api_synth_chat_fw_status(job_id):
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    if not re.fullmatch(r'[0-9a-f]{12}', str(job_id or '')):
        return jsonify({'success': False, 'error': 'bad job id'}), 400
    try:
        resp = _H.s3_client.get_object(
            Bucket=_H.S3_BUCKET, Key=f"{_C._PM_FW_JOB_PREFIX}{job_id}.json")
        payload = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return jsonify({'success': False, 'error': 'unknown job'}), 404
    uname = (user.get('username') or user.get('email') or '').strip()
    if not _pm_job_owner_ok(payload.get('user'), user):
        return jsonify({'success': False, 'error': 'not your job'}), 403
    return jsonify({'success': True, **payload})


@_H.app.route('/api/brief-chat/aiq-status/<job_id>', methods=['GET'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/aiq-status')
def api_synth_chat_aiq_status(job_id):
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    if not re.fullmatch(r'[0-9a-f]{12}', str(job_id or '')):
        return jsonify({'success': False, 'error': 'bad job id'}), 400
    try:
        resp = _H.s3_client.get_object(
            Bucket=_H.S3_BUCKET, Key=f"{_C._PM_AIQ_JOB_PREFIX}{job_id}.json")
        payload = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return jsonify({'success': False, 'error': 'unknown job'}), 404
    uname = (user.get('username') or user.get('email') or '').strip()
    if not _pm_job_owner_ok(payload.get('user'), user):
        return jsonify({'success': False, 'error': 'not your job'}), 403
    return jsonify({'success': True, **payload})
