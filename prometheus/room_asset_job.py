"""Focus Group IQ: reading a piece of content on the read worker (2026-11-05).

A video added to a room needs ffmpeg for its frames, and the web process
never holds ffmpeg; the read worker does the looking (the same arrangement
as the Pilot Testing cut, ``pilot_prepare_job``). ``room_asset`` rides the
engine queue (``read_recovery.start_engine_job``; kind registered in
``_MODULE_KINDS`` and ``ENGINE_KINDS``). The job pulls the stored file,
reads it (frames every few seconds and at the scene changes, the audio
track transcribed), captions the frames, runs the room on it and writes
the asset's status and the read onto the session, re-read just before the
write so a question that landed meanwhile is kept.

Images and audio are read on the web process itself
(``digital_twin_assets.start_processing`` decides); only a video with no
local ffmpeg comes here. A worker that cannot run the job marks the asset
failed with plain copy; the page never shows 'processing' forever
(``digital_twin_assets.sweep_assets`` is the backstop).
"""
from __future__ import annotations

import time
import traceback

from .host import host
from . import digital_twin_job as J

JOB_PREFIX = J.JOB_PREFIX
KIND = 'room_asset'
status_write = J.status_write
status_read = J.status_read


def _s3():
    return host.s3_client, (host.bucket if host.has('bucket') else 'dashboard-inputs')


def start(job_id, username, inputs, extras=None, start_fn=None):
    """Queue the read. Returns the status record."""
    from . import read_recovery as _rr
    inputs = dict(inputs or {})
    inputs['op'] = KIND
    starter = start_fn or _rr.start_engine_job
    return starter(KIND, job_id, username, inputs, extras or {}, status_write, run_job)


def run_job(job_id, username, inputs, extras=None):
    from migration import digital_twin_assets as A
    inputs = dict(inputs or {})
    key = str(inputs.get('key') or '')
    sid = str(inputs.get('session_id') or '')
    aid = str(inputs.get('asset_id') or '')
    rec = status_read(job_id) or {'job_id': job_id, 'user': username, 'inputs': inputs}
    rec.update({'status': 'running', 'queued': False, 'op': KIND, 'started_run_at': time.time(), 'stage': 'taking it in', 'twin_key': key, 'session_id': sid, 'asset_id': aid})
    status_write(job_id, rec)
    s3, _bucket = _s3()

    def tell(msg):
        rec['stage'] = str(msg or 'taking it in')[:120]
        status_write(job_id, rec)
    try:
        asset = A.process_asset(s3, key, sid, aid, usage_extras=extras, tell=tell)
        if not asset:
            raise RuntimeError('asset not found')
        rec.update({'status': 'done' if asset.get('status') == A.STATUS_READY else 'error', 'stage': asset.get('stage') or 'done', 'finished_at': time.time(),
                    'asset_status': asset.get('status'), 'read_id': asset.get('read_id'), 'error': asset.get('error')})
        status_write(job_id, rec)
    except Exception as e:
        traceback.print_exc()
        rec.update({'status': 'error', 'stage': 'failed', 'error': str(e)[:300], 'finished_at': time.time()})
        status_write(job_id, rec)
        try:   # the asset reads failed with plain copy rather than sitting at 'processing'
            from migration import digital_twins as DT
            payload = DT.load_twin(s3, key)
            sess = A._find_session(payload or {}, sid)
            asset = A.asset_by_id(sess, aid) if sess else None
            if asset is not None and asset.get('status') == A.STATUS_PROCESSING:
                asset.update({'status': A.STATUS_FAILED, 'stage': 'failed', 'note': A.FAILED_LINE, 'error': str(e)[:200], 'updated_at': A._now()})
                DT.save_twin(s3, key, payload)
        except Exception:
            traceback.print_exc()


__all__ = ['KIND', 'JOB_PREFIX', 'start', 'run_job', 'status_write', 'status_read']
