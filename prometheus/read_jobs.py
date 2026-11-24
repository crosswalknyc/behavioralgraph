"""Background read jobs: the matrix read in pieces and its finish, the read job runner, the in-flight and recent-read checks, the notify-when-done route, the titles scope question, the held-read clarifier, and the challenged-number heads-up.

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
import traceback
import re
from prometheus import faults as _faults
from prometheus import read_registry as _read_registry
from prometheus import ledger_gate as _ledger_gate, probe_files as _probe_files, cut_list as _cut_list, primary_profile as _primary_profile
from prometheus import promise_truth as _PT, read_repair as _read_repair, grid_facts as _grid_facts
import time
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import read_recovery as _rr

# <<moved>>
# <<block: 1>>
def _pm_finish_matrix_read(res, *, text, base, pm_user, followups=None, verify=None, derivation=''):
    """Bank one assembled matrix read: the reply (top rows in the chat,
    the full table named in the file), the ledger entry (every row and
    bucket), the CSV with every row, and the payload the job lands
    (2026-11-04). Request-context free; never raises past the reply."""
    import prometheus_analysis as pma
    import insights_ledger as il
    from prometheus import figure_binding as _fb
    from prometheus import methodology as _meth
    reply = pma.format_generated_metrics_reply(res)
    # the same arithmetic coherence pass the single read gets (2026-11-05)
    _arith_trail = ''
    try:
        from prometheus import arith_coherence as _arith
        reply, _ai = _arith.apply(reply, res, subject=str(res.get('subject') or ''))
        _arith_trail = _arith.trail(_ai)
        if _arith_trail:
            print(f"[pm-matrix] arithmetic coherence: {_arith_trail[:700]}")
        if _ai.get('fixes') and isinstance(verify, dict):
            verify = dict(verify, arith={'fixed': len(_ai['fixes']), 'trail': _arith_trail[:800]})
    except Exception:
        traceback.print_exc()
    try:
        if str((base or {}).get('source') or '') != 'view':
            _C._pm_name_two_audiences(res, text, base)
    except Exception:
        traceback.print_exc()
    _aud = _meth.audience_label(res, '')
    reply = __import__('prometheus.read_opener', fromlist=['with_opener']).with_opener(reply, _aud)   # one opener (2026-11-11)
    try:
        reply = _meth.ensure(reply, res, question=text, base_label=str(base.get('subject') or ''),
                             domain=str(base.get('domain') or ''),
                             view_context=(base.get('view_context') if str(base.get('source') or '') == 'view' else None))
    except Exception:
        traceback.print_exc()
    followups = [f for f in list(followups or []) if isinstance(f, str) and f.strip()][:3]
    fam = res.get('metric_family') or 'audience'
    try:
        _fb.stamp_definitions(res)
        _ps, _pc = _C._pm_persist_identity(res, base)
        _metrics = res.get('metrics')
        try:
            _metrics = il.attach_sample_people(_metrics, universe=_aud)
        except Exception:
            pass
        il.persist(subject=_ps, metric_family=fam, question=text, route='reasoned_metrics',
                   metrics=_metrics, anchors=[], window_start=res.get('window_start'),
                   window_end=res.get('window_end'), window_label=res.get('window_label'),
                   reply=reply, followups=followups,
                   base_profile_key=base.get('s3_key') or (base.get('subject') if str(base.get('source') or '') == 'view' else ''),
                   cohort=_pc, breakdown=res.get('breakdown'),
                   derivation=(derivation or 'matrix read, pieces assembled') + (f"; {_arith_trail}" if _arith_trail else ''),
                   verify=verify, universe=_aud)
    except Exception:
        traceback.print_exc()
    try:
        _C._pm_remember_ask(pm_user, text, subject=res.get('subject'), cohort=res.get('cohort'), route='generated')
    except Exception as _fx:
        _faults.note('chat', _fx)
    _file_payload = {}
    try:
        _fe = {'subject': res.get('subject'), 'cohort': res.get('cohort'), 'question': text,
               'metrics': res.get('metrics'), 'breakdown': res.get('breakdown'),
               'ws': res.get('window_start'), 'we': res.get('window_end'), 'wl': res.get('window_label'),
               'family': fam}   # the file's pool noun and note follow the family (2026-11-06)
        _file_payload = _C._pm_answer_file_payload(_fe, auto_save=bool(_C._PM_FILE_ASK_RE.search(str(text or ''))),
                                                username=pm_user, question=text)
        if _file_payload and 'Email me this file' not in followups:
            followups.append('Email me this file')
    except Exception:
        traceback.print_exc()
    try:
        from prometheus import charts as _charts
        _chart_payload = _charts.publish(res, text, pm_user, _H.s3_client, _H.S3_BUCKET)
    except Exception:
        _chart_payload = {}
    out = {'success': True, 'action': 'answer', 'reply': reply, **(_chart_payload or {}), 'figures': [],
           'followups': followups, 'offer_deck': False, 'deck_angle': None, 'profile': res.get('subject'),
           'metrics': list(res.get('metrics') or []),
           'breakdown': (dict(res['breakdown']) if isinstance(res.get('breakdown'), dict) else None),
           'matrix': dict(res.get('_matrix') or {}), '_family': fam, '_verify': verify, '_stages_ms': {},
           **_file_payload}
    return out


# <<block: 2>>
_PM_READ_PREFIX = 'system/prometheus_reads/'


# <<block: 3>>
_PM_READ_INFLIGHT_PREFIX = 'system/prometheus_reads/_inflight/'


# <<block: 4>>
def _pm_read_inflight_doc_key(user):
    return _read_registry.doc_key(user)


# <<block: 5>>
def _pm_read_inflight_check(user, text):
    """The caller's own running job for this question (shared fold), or None
    (prometheus.read_registry; Jenna 2026-09-30, 2026-10-26)."""
    return _read_registry.inflight(_H.s3_client, _H.S3_BUCKET, _C._PM_READ_PREFIX, user, text)


# <<block: 6>>
def _pm_read_recent_check(user, text):
    """The caller's landed read on this question inside a day, or the running one."""
    return _read_registry.recent(_H.s3_client, _H.S3_BUCKET, _C._PM_READ_PREFIX, user, text)


# <<block: 7>>
def _pm_read_inflight_mark(user, text, job_id, subject=''):
    _read_registry.mark(_H.s3_client, _H.S3_BUCKET, user, text, job_id, subject=subject)


# <<block: 8>>
# ------------------------------------------------------------------
# "Email me when it is ready" for long tasks (2026-09-02).
#
# A generated read (analyze background job) and a deck build each take
# a few minutes. When one starts, the chat offers to email the
# finished OUTPUT to the requester. The opt-in is captured AFTER
# kickoff (the offer is the ack's follow-up), so the email address is
# threaded to the background thread through an S3 side-file keyed by
# job id - the same job-id side-channel idea deck attribution uses at
# enqueue, but S3-backed so the confirm POST and the worker thread can
# land on different workers.
#
# On completion both jobs ALSO append the finished output to the
# requester's chat thread (the existing per-user history store), so
# the read / deck link is waiting when they return even if the tab
# that started it is gone. The chat re-hydration is unconditional; the
# email is the opt-in extra.
# ------------------------------------------------------------------
_PM_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


# <<block: 9>>
def _pm_iso_now():
    return time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())


# <<block: 10>>
def _pm_history_has_job_turn(history, meta_key, job_id):
    for t in (history or []):
        try:
            if (t or {}).get('meta', {}).get(meta_key) == job_id:
                return True
        except Exception:
            continue
    return False


# <<block: 11>>
def _pm_payload_has_figures(payload):
    """A finished read's payload carries figures (a table, metrics, a
    file, a figure ledger) or a stated reason (not quantifiable, held).
    Anything else is an empty pass and never lands as the answer."""
    p = payload if isinstance(payload, dict) else {}
    if p.get('not_quantifiable') or p.get('held') or p.get('_held') or p.get('faulted'):
        return True
    if p.get('metrics') or p.get('breakdown') or p.get('file_link') or p.get('figures'):
        return True
    reply = str(p.get('reply') or '')
    low = reply.lower()
    if 'pulling this data will cost' in low or low.startswith('which ') or low.startswith('i can pull') \
            or low.startswith('i can split') or 'name the show, brand or person' in low:
        return False
    return bool(re.search(r"\d", reply)) and len(reply) > 120


# <<block: 12>>
def _pm_run_matrix_read(plan, *, text, history, mr, base, digest_block, anchors_block, led,
                        pm_user, pm_ppu, stage_cb=None):
    """A big matrix ask inside one read job (2026-11-04, Liz): every
    piece runs through the same read core on the same base, window and
    definitions (bank=False); the pieces assemble into one result;
    `_pm_finish_matrix_read` banks once, writes one file and returns the
    one payload the job lands. A piece that fails retries once inside
    `matrix_read.run`; a piece that still fails gets one bounded second
    pass and then the deterministic fill from the landed pieces
    (`matrix_read.complete`), so the table lands whole. The reader is
    never told a piece was missing (Jenna 2026-11-04: "it should never
    say anything like that ever. it should only send it to us."): a
    fill goes to ops (jenna@ + jessie@); a read where nothing landed at
    all is held (the calm line) and ops hears, never the reader."""
    from prometheus import matrix_read as _mx

    def _core(piece_text, piece_label):
        out = _C._pm_generate_read_core(
            text=piece_text, history=history, mr=mr, base=base,
            digest_block=digest_block, anchors_block=anchors_block,
            led=led, pm_user=pm_user, pm_ppu=pm_ppu, stage_cb=None, bank=False)
        if not isinstance(out, dict) or not out.get('success') or out.get('_held'):
            return None
        res = out.get('_res')
        return res if isinstance(res, dict) and (res.get('breakdown') or res.get('metrics')) else None

    results = _mx.run(plan, core=_core, text=text, stage_cb=stage_cb)
    subject = str((base or {}).get('subject') or '').strip() or 'this audience'
    _cinfo = {'second_pass': [], 'filled': [], 'how': {}}
    if results and len(results) < len(plan['pieces']):
        try:
            results, _cinfo = _mx.complete(plan, results, core=_core, text=text, stage_cb=stage_cb,
                                           subject=subject, genpop=_C._pm_genpop_dimension_shares)
        except Exception:
            traceback.print_exc()
    if stage_cb is not None:
        try:
            stage_cb('putting the table together')
        except Exception:
            pass
    res = _mx.assemble(plan, results, subject=subject, text=text)
    if not results or not res.get('breakdown'):
        # nothing landed at all: nothing to complete from. The read is
        # held (the calm line; the sweep gives it a fresh pass) and ops
        # hears; the reader is never told the pieces did not come back
        try:
            _H._chatbot_error_email(
                'brief-chat/matrix',
                f"matrix read: none of {len(plan['pieces'])} pieces came back ({_mx.describe(plan)}); "
                f"held with the calm line, the user needs the answer by email | {text[:140]}",
                user_email=pm_user, payload={'question': text[:300], 'subject': subject, 'base': base.get('s3_key')})
        except Exception:
            traceback.print_exc()
        return {'success': True, 'action': 'answer', 'reply': _H._CHATBOT_CALM_MESSAGE, 'followups': [],
                'offer_deck': False, 'deck_angle': None, '_held': True, '_family': 'audience',
                '_verify': None, '_stages_ms': {}}
    res['_missing'] = []
    # the finished table re-verifies (the scrub, the base rows) for the
    # stamp on the job and the ledger entry; the table ships whole either way
    _stamp = None
    try:
        import prometheus_verify as _pmv
        _lk = None
        try:
            _lk = _pmv.load_base_lookup(_H.s3_client, _H.S3_BUCKET, base.get('s3_key'), base.get('subject') or '')
        except Exception:
            _lk = None
        _vd = _pmv.verify_read(reply=__import__('prometheus_analysis').format_generated_metrics_reply(
            __import__('prometheus_analysis').enforce_metrics_coherence(res) | {'_matrix': res.get('_matrix')}),
            res=res, family='audience', base_lookup=_lk, question=text)
        _stamp = _pmv.stamp(_vd, revised=bool(_cinfo.get('second_pass') or _cinfo.get('filled')))
        if not _vd.get('ok'):
            print(f"[pm-matrix] the assembled table did not clear the checks: {_vd.get('findings')}")
            _cinfo['findings'] = list(_vd.get('findings') or [])
    except Exception:
        traceback.print_exc()
    if _cinfo.get('filled'):
        try:
            _lines = [f"Matrix pieces filled by arithmetic: {', '.join(_cinfo['filled'])} | {text[:140]}",
                      f"{_mx.describe(plan)}; {len(results) - len(_cinfo['filled'])} piece(s) landed from the passes"
                      + (f", {len(_cinfo['second_pass'])} of them on the second pass ({', '.join(_cinfo['second_pass'])})" if _cinfo.get('second_pass') else '')
                      + f"; {len(_cinfo['filled'])} filled from the landed pieces. The reader received the whole table; nothing about this reached them."]
            for _lab in _cinfo['filled']:
                _lines.append(f"Filled: {_lab}: {_cinfo['how'].get(_lab) or 'from the landed pieces'}")
            if _cinfo.get('findings'):
                _lines.append('Findings on the assembled table: ' + ' | '.join(str(f) for f in _cinfo['findings'][:8]))
            _lines.append(f"Question: {text[:600]}")
            _H._chatbot_error_email('brief-chat/complete', '\n'.join(_lines), user_email=pm_user,
                                    payload={'question': text[:300], 'subject': subject, 'base': base.get('s3_key')})
        except Exception:
            traceback.print_exc()
    return _C._pm_finish_matrix_read(res, text=text, base=base, pm_user=pm_user, verify=_stamp,
                                  derivation=f"matrix read: {_mx.describe(plan)}; landed {len(results) - len(_cinfo.get('filled') or [])} of "
                                             f"{len(plan['pieces'])}; second pass {len(_cinfo.get('second_pass') or [])}; "
                                             f"filled {len(_cinfo.get('filled') or [])}")


# <<block: 13>>
_GENPOP_DIMENSION_COLUMNS = {'age': 'AGE', 'gender': 'GENDER', 'income': 'INCOME', 'education': 'EDUCATION',
                             'ethnicity': 'ETHNICITY', 'household': 'PARENTAL STATUS'}


# <<block: 14>>
def _pm_genpop_dimension_shares(dimension):
    """US baseline shares for a demographic dimension, [(band, share)],
    from the Gen Pop file (the fill for a matrix piece whose dimension no
    landed piece carried). None when the dimension is not a demographic
    one or the file cannot be read. Never raises."""
    try:
        col = _C._GENPOP_DIMENSION_COLUMNS.get(str(dimension or '').strip().lower().split()[0])
        if not col:
            return None
        import prometheus_analysis as pma
        df, _etag = pma.load_profile_df(_H.s3_client, _H.S3_BUCKET, 'Gen_Pop_2026.csv')
        bp_c = pma._bp_col(df)
        out = []
        for cat, grp in df.groupby('Column', sort=False):
            if pma._norm_cat(cat) != col:
                continue
            for label, bpv in zip(grp['Value'].tolist(), grp[bp_c].tolist()):
                v = pma._parse_bp(bpv)
                if v is not None and str(label or '').strip():
                    out.append((str(label).strip(), float(v)))
        return out if len(out) >= 2 else None
    except Exception:
        return None


# <<block: 15>>
@_ledger_gate.job_scope()
def _pm_run_read_job(job_id, pm_user, pm_ppu, text, history, mr, base,
                     digest_block, anchors_block, led,
                     panel_charge=None, probe=False, matrix_plan=None):
    """Background body of one generated read. Writes the finished
    payload to the S3-backed job status the widget polls; a locked
    phone or reloaded tab picks the read up when it returns. As the
    read advances, each phase transition lands on the job JSON as a
    user-safe `stage` (one tiny S3 put per transition) so the widget
    can narrate progress (2026-08-28, p1-staged-progress).

    `pm_ppu` (2026-09-04): now carries the pre-captured request-thread
    user attribution (user, user_email) merged with pay-as-you-go
    billing extras. Threaded through to _pm_generate_read_core so
    every model call inside the read attributes to the requesting
    user, even though this function runs on a background thread with
    no Flask request context."""
    head = {'job_id': job_id, 'user': pm_user, 'probe': bool(probe),
            'question': text[:300], 'started_at': time.time()}
    head = _rr.carry_resume(job_id, head)   # keep the launch's resume record
    _beat = _rr.start_heartbeat(job_id, head)   # also registers for handoff

    def _stage(label):
        try:
            _C._pm_read_status_write(job_id, {
                **head, 'status': 'working', 'stage': str(label)[:60],
                'stage_at': time.time()})
        except Exception as _fx:
            _faults.note('chat', _fx)

    try:
        if matrix_plan:
            payload = _C._pm_run_matrix_read(matrix_plan, text=text, history=history, mr=mr, base=base,
                                          digest_block=digest_block, anchors_block=anchors_block,
                                          led=led, pm_user=pm_user, pm_ppu=pm_ppu, stage_cb=_stage)
        else:
            payload = _C._pm_generate_read_core(
                text=text, history=history, mr=mr, base=base,
                digest_block=digest_block, anchors_block=anchors_block,
                led=led, pm_user=pm_user, pm_ppu=pm_ppu, stage_cb=_stage)
        payload.pop('_family', None)
        held = bool(payload.pop('_held', False))
        _verify = payload.pop('_verify', None)
        # a read that produced no figures never lands as an empty or a
        # transport line, and never as a line that tells the reader
        # nothing came back (Jenna 2026-11-04: "it should never say
        # anything like that ever. it should only send it to us."): it
        # is held with the calm line, the sweep gives it a fresh pass,
        # and ops hears what happened
        if payload.get('success') and not held and (payload.get('faulted') or not _C._pm_payload_has_figures(payload)):
            _why = str(payload.get('reply') or '').strip()
            try:
                _H._chatbot_error_email(
                    'brief-chat/read-job',
                    f"generated read came back with no figures ({str(payload.get('faulted') or 'no_figures')}); "
                    f"held with the calm line for a fresh pass, the user needs the answer by email | {text[:140]}"
                    + (f" | reply was: {_why[:300]}" if _why else ''),
                    user_email=pm_user, payload={'job_id': job_id, 'question': text[:300],
                                                 'subject': head.get('subject') or (base or {}).get('subject')})
            except Exception:
                traceback.print_exc()
            payload = {'success': True, 'action': 'answer', 'reply': _H._CHATBOT_CALM_MESSAGE, 'followups': [],
                       'offer_deck': False, 'deck_angle': None}
            held = True
        # The finished read bypasses the envelope, so the plain-English
        # shaper runs here (2026-10-06).
        from prometheus import reply_shape as _rs
        _rs.shape_finished_read(payload, held)
        __import__('prometheus.people_bounds', fromlist=['guard_payload']).guard_payload(payload)   # a people count never passes the US population or its own audience (2026-11-11)
        __import__('prometheus.partnership_value', fromlist=['guard_read']).guard_read(payload, question=text, history=history)   # a partnership's dollar value is the valuation's to state (Jenna 2026-11-11)
        __import__('prometheus.reply_truth', fromlist=['finish_read']).finish_read(payload, question=text)   # a read claims nothing it has no record of (2026-11-11)
        _stages = payload.pop('_stages_ms', None)
        _chat = __import__('prometheus.build_offer', fromlist=['with_offer']).with_offer(payload, base=base, user=pm_user, question=text, history=history, job_id=job_id, thread_id=str(head.get('thread_id') or ''), via=head.get('via'), held=held, probe=probe)   # a read on a subject we do not hold ends with the build offer in the chat copy only; emails carry the read alone (Jenna 2026-11-23)
        if payload.get('success'):
            _done = {**head,
                     'status': 'held' if held else 'done',
                     'finished_at': time.time(), 'payload': _chat}
            if isinstance(_stages, dict) and _stages:
                _done['stages_ms'] = _stages
            if isinstance(_verify, dict) and _verify:
                _done['verify'] = _verify
            # the file the read wrote, as a job file the API door lists
            # and serves under the same key (2026-11-04)
            _fl = payload.get('file_link') if isinstance(payload.get('file_link'), dict) else {}
            if _fl.get('key') and _fl.get('name'):
                _done['files'] = {'csv': {'key': str(_fl['key']), 'name': str(_fl['name'])}}
            # Land the finished read in the requester's chat thread so
            # it is waiting when they return, and (if they opted in)
            # email them the read itself. A held read still lands in
            # the thread as its calm one-liner, but carries no output
            # to email, so only a clean read fires the notify. The
            # thread is written BEFORE the job reads done (2026-11-05):
            # the widget's poll refreshes the thread on done and finds
            # the server's copy, so the server is the writer and the
            # widget's own copy is the fallback, not the other way round.
            if panel_charge and not held and payload.get('data_floor') == 'before':
                _C._pm_panel_refund(panel_charge)   # the read was refused at the data floor: nothing is charged (Jenna 2026-11-18)
            _C._pm_append_read_to_history(pm_user, job_id, _chat, held=held, started_at=head.get('started_at'))
            _C._pm_read_status_write(job_id, _done)
            _C._pm_watch_notify(pm_user, text, payload, payload.get('profile'), probe=probe,
                             via=__import__('prometheus.service', fromlist=['job_doc_field']).job_doc_field(job_id, 'via'))
            __import__('prometheus.ride_along', fromlist=['on_read_landed']).on_read_landed(pm_user, job_id, payload, held=held, probe=probe)   # the deck asked for with the question (2026-11-11)
            if not held and not probe:
                _sent = _C._pm_flush_notify(job_id, 'read',
                                         {**payload, 'question': text})
                # The calm line on a held read promises the answer by email; a retry that lands keeps it
                # (2026-10-22 audit: Casey's re-run landed six hours later, in the thread only).
                if not _sent and _rr.promised_email(job_id) and _C._pm_user_email(pm_user):
                    _sent = bool(_C._pm_send_output_email('read', _C._pm_user_email(pm_user), {**payload, 'question': text}, sync=True))
                # an open promise on this question (an earlier copy of it died behind the calm line) is kept: emailed once, the record closed as delivered (2026-11-18)
                _PT.keep(pm_user, text, job_id=job_id, emailed=_sent, thread_id=str(head.get('thread_id') or ''),
                         send=lambda: bool(_C._pm_user_email(pm_user)) and _C._pm_send_output_email('read', _C._pm_user_email(pm_user), {**payload, 'question': text}, sync=True))
            else:
                _C._pm_notify_delete(job_id)
                # A held research report never delivered: the charge
                # reverses (the read itself stays held per house
                # practice; the user was told it needs another pass).
                if panel_charge:
                    _C._pm_panel_refund(panel_charge)
        elif not probe and _rr.hold_for_fresh_pass(job_id, head, write=_C._pm_read_status_write, why='the pass came back failed', land=lambda _p: _C._pm_append_read_to_history(pm_user, job_id, _p, held=True, started_at=head.get('started_at'))):
            held = True   # ended without an answer on its first run: held for one fresh pass before a person owes it (2026-11-18)
            _C._pm_notify_delete(job_id)
            if panel_charge:
                _C._pm_panel_refund(panel_charge)
        else:
            probe or _PT.job_failed('read', pm_user, job_id, text, land=lambda _p: _C._pm_append_read_to_history(pm_user, job_id, _p, held=True))
            _C._pm_read_status_write(job_id, {**head, 'status': 'error'})
            _C._pm_notify_delete(job_id)
            _C._pm_settle_ask(job_id, 'faulted', '', head.get('started_at'))
            if panel_charge:
                _C._pm_panel_refund(panel_charge)
        print(f"[pm-loop] read {job_id} "
              f"{'held' if held else 'done' if payload.get('success') else 'failed'} "
              f"for {pm_user}")
    except Exception as e:
        traceback.print_exc()
        _H._chatbot_error_email('brief-chat/read-job', e,
                             user_email=pm_user,
                             payload={'job_id': job_id,
                                      'text': text[:200]})
        if probe or not _rr.hold_for_fresh_pass(job_id, head, write=_C._pm_read_status_write, why=e, land=lambda _p: _C._pm_append_read_to_history(pm_user, job_id, _p, held=True, started_at=head.get('started_at'))):   # a job that died on its first run is held for one fresh pass (2026-11-18)
            probe or _PT.job_failed('read', pm_user, job_id, text, land=lambda _p: _C._pm_append_read_to_history(pm_user, job_id, _p, held=True))
            try:
                _C._pm_read_status_write(job_id, {**head, 'status': 'error'})
            except Exception as _fx:
                _faults.note('chat', _fx)
        _C._pm_notify_delete(job_id)
        if panel_charge:
            _C._pm_panel_refund(panel_charge)
    finally:
        _beat.set()


# <<block: 16>>
@_H.app.route('/api/brief-chat/notify-when-done', methods=['POST'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/notify-when-done')
def api_synth_chat_notify_when_done():
    """Opt in to an email when a long read / deck finishes.

    Called after the task's ack, once the user confirms the offer with
    an address. The finished output always lands in the chat thread on
    return; this endpoint is only the opt-in email extra. The address
    rides an S3 side-file keyed by job id so the background thread
    picks it up on completion regardless of which worker serves this
    request. If the job already finished, the output email is sent
    right away instead of queued.

    Session-authenticated dashboard users only."""
    user, err = _C._synth_chat_gate(allow_api_key=False)
    if err:
        return err
    # Prometheus mode gate (2026-09-03, Jenna): notify-when-done is
    # only ever wired to a read / deck job the user already kicked
    # off, both of which are analysis-tier surfaces. Pull-only users
    # never see the offer chip on the frontend; this is defense in
    # depth.
    if not _C._pm_gate_analyze(user):
        return _C._pm_gate_refusal('analyze')
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({'success': False, 'error': 'bad request'}), 400
    job_id = str(body.get('job_id') or '').strip()
    kind = 'deck' if str(body.get('kind') or '') == 'deck' else 'read'
    email = _C._pm_clean_notify_email(body.get('email'))
    if not re.fullmatch(r'[0-9a-f]{12}', job_id):
        return jsonify({'success': False, 'error': 'bad job id'}), 400
    if not email:
        return jsonify({'success': False,
                        'error': 'enter a valid email'}), 400
    uname = (user.get('username') or user.get('email') or '').strip()
    prefix = _C._PM_DECK_PREFIX if kind == 'deck' else _C._PM_READ_PREFIX
    try:
        resp = _H.s3_client.get_object(
            Bucket=_H.S3_BUCKET, Key=f"{prefix}{job_id}.json")
        status = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return jsonify({'success': False, 'error': 'unknown job'}), 404
    if not _C._pm_job_owner_ok(status.get('user'), user):
        return jsonify({'success': False, 'error': 'not your job'}), 403
    st = str(status.get('status') or '').strip().lower()
    # Already finished: send the output now (a held read / any error
    # has no output to send, so those just confirm without a send).
    if kind == 'read' and st in ('done', 'held', 'error'):
        sent = False
        if st == 'done':
            sent = _C._pm_send_output_email(
                'read', email,
                {**(status.get('payload') or {}),
                 'question': str(status.get('question') or '')})
        return jsonify({'success': True, 'already_done': True,
                        'sent': bool(sent)})
    if kind == 'deck' and st in ('done', 'error'):
        sent = False
        if st == 'done':
            sent = _C._pm_send_output_email('deck', email, status)
        return jsonify({'success': True, 'already_done': True,
                        'sent': bool(sent)})
    # Still running: stash the opt-in for the worker to pick up.
    try:
        _C._pm_notify_write(job_id, {'job_id': job_id, 'kind': kind,
                                  'email': email, 'user': uname,
                                  'requested_at': time.time()})
    except Exception:
        traceback.print_exc()
        return jsonify({'success': False,
                        'error': 'could not save your request'}), 500
    return jsonify({'success': True, 'queued': True})


# <<block: 17>>
_PM_COHORT_WORDS_RE = re.compile(
    r'\b(millennials?|gen\s*z|gen\s*x|gen\s*alpha|boomers?|'
    r'women|men|females?|males?|moms?|dads?|parents|teens?|seniors?|'
    r'hispanic|black|latino|asian|lgbtq\+?|'
    r'\d{2}\s*(?:-|to)\s*\d{2})\b', re.I)


# <<block: 18>>
_PM_TITLES_SCOPE_RE = re.compile(
    r"\b(?:top|highest|leading|best|rank(?:ed|ing)?)\b"
    r".{0,60}\b(?:titles?|shows?|movies?|films?|series)\b"
    r"|\btop titles inside\b"
    r"|\b(?:titles?|shows?|movies?|films?)\b.{0,40}\b(?:views?|viewership)\b",
    re.I)


# <<block: 19>>
_PM_NAMED_SERVICE_RE = re.compile(
    r"\b(?:netflix|hulu|disney|peacock|tubi|pluto|roku|starz|"
    r"hbo|prime video|amazon prime|paramount|apple tv|youtube|espn)\b",
    re.I)


# <<block: 20>>
def _pm_titles_ask_needs_scope(text, page_subject):
    """True when a titles/shows ask does not name the open profile.

    Jenna 2026-09-25: the profile on screen is not the question.
    Ask 'Do you mean on Paramount+?' before answering. A service
    named in the ask is already the subject, so this stays quiet.
    """
    t = str(text or "")
    page = str(page_subject or "").strip()
    if not t or not page or not _C._PM_TITLES_SCOPE_RE.search(t):
        return False
    # A pasted block of figures (2026-10-05, Alexia quoting a reply
    # back: "these numbers are not displayed on this current
    # dashboard: 21.8M ticketing-site visitors ...") is not a titles
    # ask, whatever words sit inside it. Titles asks are short and
    # carry no counts.
    if len(t) > 240 or re.search(r"\d{1,3}(?:,\d{3})+|\b\d+(?:\.\d+)?[MK]\b|\d+(?:\.\d+)?%", t):
        return False
    page_toks = [w for w in re.findall(r"[a-z0-9]+", page.lower())
                 if len(w) >= 4]
    tl = t.lower()
    if page_toks and any(w in tl for w in page_toks):
        return False
    page_l = page.lower()
    named = [m.group(0).lower() for m in _C._PM_NAMED_SERVICE_RE.finditer(t)]
    if any(n not in page_l for n in named):
        return False
    return True


# <<block: 21>>
def _pm_held_read_clarify(text, subject):
    """Clarify + suggestions when a generated read cannot ship (Jenna
    2026-09-22, Scott's 'Compare Millennials against the Will And
    Grace audience': the read fabricated a cohort that exists as no
    file, was held, and the user got a dead end - 'it should ask him
    to clarify and suggest instead of just killing it').

    Comparison-of-cohort asks get the two honest paths: derive the
    cohort cut off the existing file and compare real files, or read
    what the current file measures about that cohort today. Everything
    else gets a plain re-aim ask. Returns (reply, chips)."""
    subj = str(subject or 'this audience').strip() or 'this audience'
    t = str(text or '')
    m = _C._PM_COHORT_WORDS_RE.search(t)
    if m and re.search(r'\bcompare|\bvs\.?\b|\bversus\b|\bagainst\b',
                       t, re.I):
        cohort = ' '.join(m.group(1).split()).title()
        reply = (
            f"I want to get this comparison right rather than hand "
            f"you numbers I am not sure of. When you say "
            f"\"{t.strip()}\", I read two possible asks:\n\n"
            f"1. The {subj} audience's own {cohort} cut against the "
            f"full {subj} audience - that cut is not on the shelf "
            f"yet, but it derives straight off the existing file "
            f"and then the comparison runs on two real files.\n"
            f"2. What the current {subj} file already measures "
            f"about {cohort} inside the audience today - no build "
            f"needed.\n\n"
            f"Which one do you want?")
        chips = [
            f"Cut {subj} by {cohort}, then compare to the full "
            f"audience",
            f"What does the {subj} file show about {cohort} today?",
        ]
        return reply, chips
    # Jenna 2026-09-25: never tell the user the read was held.
    # The promise is a follow-up email, not a re-aim.
    reply = ("Working on it! I will email you the results when "
             "they are ready.")
    return reply, []


# <<block: 22>>
# CHALLENGED-NUMBER HEADS-UP (2026-09-30 Jenna: consistency guard).
# When a reader questions a delivered figure ("seems very high", "why
# is this smaller than the previous analysis", "how is this
# calculated"), the answer still generates normally - the analysis
# prompt carries the reconcile instructions - and ops hears about it
# in the background so a genuine inconsistency never dies in a chat
# session. Evidence: sonytv challenged the Apple TV+ subscriber read
# and a Dark Matter season-over-season contradiction in the same week.
_PM_CHALLENGE_RES = (
    re.compile(r"\bseems?\s+(?:too\s+|very\s+|really\s+|way\s+too\s+)?"
               r"(?:high|low|off|wrong|inflated|small|big|large)\b", re.I),
    re.compile(r"\b(?:number|figure|count|value|read|projection)s?\s+"
               r"(?:looks?|seems?|feels?)\b", re.I),
    re.compile(r"\bhow\s+(?:is|was|did|are|were)\s+"
               r"(?:this|that|it|these|those|the\s+[\w %#]{1,30}?)\s+"
               r"(?:calculated|derived|measured|computed|determined|"
               r"arrived\s+at)\b", re.I),
    re.compile(r"\b(?:doesn'?t|does\s+not|didn'?t|did\s+not)\s+"
               r"(?:match|line\s+up\s+with|square\s+with|agree\s+"
               r"with)\b", re.I),
    re.compile(r"\bwhy\s+(?:is|was|does|did|are|were)\b.{0,70}?"
               r"\b(?:higher|lower|bigger|smaller|larger|different)\s+"
               r"than\b", re.I),
    re.compile(r"\b(?:very|extremely|surprisingly|unusually|"
               r"impossibly)\s+(?:high|low)\b", re.I),
    re.compile(r"\bcan'?t\s+be\s+right\b|\bdouble[- ]check\s+"
               r"(?:this|that|the)\b", re.I),
)


# <<block: 23>>
def _pm_challenge_headsup(user, text):
    """Fire-and-continue ops email when a reader challenges a figure.
    Never blocks or changes the answer path. Never raises."""
    try:
        t = str(text or '')
        if len(t) < 12 or not any(
                rx.search(t) for rx in _C._PM_CHALLENGE_RES):
            return
        if _C._pm_probe_caller() or _C._pm_is_probe_user((user or {}).get('username')):
            return   # a probe (golden set, canary, smoke) never mails ops (2026-10-14)
        _H._chatbot_error_email(
            'pm/number-challenged',
            'reader challenged a delivered figure (the answer still '
            'generated normally; review for consistency): ' + t[:400],
            user_email=(user.get('email') or user.get('username')),
            payload={'question': t[:400], '_no_promise': True})
    except Exception as _fx:
        _faults.note('chat', _fx)


__all__ = ['_pm_finish_matrix_read', '_PM_READ_PREFIX', '_PM_READ_INFLIGHT_PREFIX', '_pm_read_inflight_doc_key', '_pm_read_inflight_check', '_pm_read_recent_check', '_pm_read_inflight_mark', '_PM_EMAIL_RE', '_pm_iso_now', '_pm_history_has_job_turn', '_pm_payload_has_figures', '_pm_run_matrix_read', '_GENPOP_DIMENSION_COLUMNS', '_pm_genpop_dimension_shares', '_pm_run_read_job', 'api_synth_chat_notify_when_done', '_PM_COHORT_WORDS_RE', '_PM_TITLES_SCOPE_RE', '_PM_NAMED_SERVICE_RE', '_pm_titles_ask_needs_scope', '_pm_held_read_clarify', '_PM_CHALLENGE_RES', '_pm_challenge_headsup']
