"""Pilot Testing: preparing the cut on the read worker (2026-10-28).

The web process never holds ffmpeg; the read worker does the looking.
``pilot_prepare`` rides the same engine queue as every other job
(``read_recovery.start_engine_job``; kind registered in
``_MODULE_KINDS`` and ``ENGINE_KINDS``). The job:

  1. pulls the uploaded cut to a temp file,
  2. reads it (scene changes, frames, silences, transcript when Whisper
     is installed; migration/digital_twin_cut.py),
  3. captions the frames (migration/digital_twin_watch.py),
  4. runs ``prepare_watch`` on the session (the persona watch, the
     battery, the guide, the norms), and
  5. writes the session back onto the twin, re-read just before the
     write so a question or a rename that landed meanwhile is kept.

Status lives under the Digital Twins job prefix so the page polls it
with the same route. A missing tool is a note on the session and a log
line, never a failure: the clock beat sheet runs and Play is enabled.
Nothing from the file outlives the job on disk.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import traceback

from .host import host
from . import digital_twin_job as J

JOB_PREFIX = J.JOB_PREFIX
KIND = 'pilot_prepare'
status_write = J.status_write
status_read = J.status_read


def _s3():
    return host.s3_client, (host.bucket if host.has('bucket') else 'dashboard-inputs')


def start(job_id, username, inputs, extras=None, start_fn=None):
    """Queue the preparation. Returns the status record."""
    from . import read_recovery as _rr
    inputs = dict(inputs or {})
    inputs['op'] = KIND
    starter = start_fn or _rr.start_engine_job
    return starter(KIND, job_id, username, inputs, extras or {}, status_write, run_job)


def _download(s3, bucket, key, suffix):
    fd, path = tempfile.mkstemp(suffix=suffix or '.mp4', prefix='dtiq-cut-')
    os.close(fd)
    with open(path, 'wb') as fh:
        body = s3.get_object(Bucket=bucket, Key=key)['Body']
        while True:
            bit = body.read(4 * 1024 * 1024)
            if not bit:
                break
            fh.write(bit)
    return path


def _find(payload, sid):
    return next((s for s in (payload or {}).get('pilot_sessions') or [] if str(s.get('id')) == str(sid)), None)


def run_job(job_id, username, inputs, extras=None):
    from migration import digital_twins as DT
    from migration import digital_twin_pilot as PI
    from migration import digital_twin_cut as CUT
    inputs = dict(inputs or {})
    key = str(inputs.get('key') or '')
    sid = str(inputs.get('session_id') or '')
    rec = status_read(job_id) or {'job_id': job_id, 'user': username, 'inputs': inputs}
    rec.update({'status': 'running', 'queued': False, 'op': KIND, 'started_run_at': time.time(), 'stage': 'reading the cut', 'twin_key': key, 'session_id': sid})
    status_write(job_id, rec)
    s3, bucket = _s3()
    tmp_path = None
    analysis = {}
    try:
        payload = DT.load_twin(s3, key)
        if not payload:
            raise RuntimeError('twin not found for the cut')
        sess = _find(payload, sid)
        if not sess:
            raise RuntimeError('test not found for the cut')
        media = sess.get('media') or {}

        def tell(msg):
            rec['stage'] = str(msg or 'reading the cut')[:120]
            status_write(job_id, rec)
            try:
                sess['prepare_note'] = 'Preparing the cut'
                sess['prepare_stage'] = rec['stage']
            except Exception:
                pass
        twins_by_key = {}
        for tk in sess.get('twin_keys') or [key]:
            t = payload if tk == key else DT.load_twin(s3, tk)
            if t:
                twins_by_key[tk] = t
        if key not in twins_by_key:
            twins_by_key[key] = payload
        mk = media.get('s3_key')
        tools = CUT.tools_available()
        notes = []
        if mk:
            try:
                tell('fetching the cut')
                tmp_path = _download(s3, bucket, mk, os.path.splitext(str(media.get('filename') or ''))[1] or '.mp4')
            except Exception as e:
                print(f"[pilot-prepare {job_id}] fetch failed: {e}")
                notes.append('the file could not be fetched this time')
                tmp_path = None
        if tmp_path:
            if not tools.get('ffmpeg'):
                print(f"[pilot-prepare {job_id}] ffmpeg missing on this host; clock beat sheet (install ffmpeg on the read worker for the scene read)")
            if not tools.get('whisper'):
                print(f"[pilot-prepare {job_id}] whisper missing on this host; no transcript (pip install openai-whisper on the read worker)")
            analysis = CUT.analyze_cut(tmp_path, duration_s=float(media.get('runtime_s') or 0), progress=tell)
            if analysis.get('frames'):
                tell('the room is looking at the frames')
                try:
                    from migration import digital_twin_watch as W
                    caps = W.caption_frames(analysis['frames'], transcript=analysis.get('transcript'), title=str(media.get('title') or sess.get('title') or ''),
                                            notes=str(inputs.get('notes') or ''), usage_extras=extras)
                    analysis['captions'] = caps.get('captions') or {}
                    analysis['genre_votes'] = caps.get('genre_votes') or {}
                    analysis['format_votes'] = caps.get('format_votes') or {}
                    if caps.get('failed'):
                        analysis.setdefault('notes', []).append(f"{caps['failed']} frame batches did not answer")
                except Exception as e:
                    print(f"[pilot-prepare {job_id}] captions skipped: {e}")
                    analysis.setdefault('notes', []).append('captions skipped')
        else:
            analysis = {'runtime_s': float(media.get('runtime_s') or 0) or None, 'scenes': [], 'frames': [], 'transcript': sess.get('transcript'), 'silences': None,
                        'tools': tools, 'notes': notes + (['no file on this test: the clock runs on a slate'] if not mk else []), 'source': 'clock'}
        analysis.setdefault('notes', [])
        analysis['notes'] = list(analysis.get('notes') or []) + notes
        tell('the room is watching')
        PI.prepare_watch(sess, twins_by_key, duration_s=float(analysis.get('runtime_s') or media.get('runtime_s') or 0),
                         title=str(media.get('title') or sess.get('title') or ''), notes=str(inputs.get('notes') or ''),
                         transcript=analysis.get('transcript') or sess.get('transcript'), analysis=analysis, usage_extras=extras, progress=tell, s3=s3)
        # the further assets on a trailer or binge test: the same room watches each one (a sub-read per asset)
        extra_assets = [a for a in (sess.get('assets') or []) if isinstance(a, dict) and a.get('kind', 'video') == 'video' and int(a.get('slot') or 0) >= 1 and a.get('s3_key')]
        if extra_assets:
            from migration import digital_twin_modes as MODES
            for a in extra_assets:
                a_tmp = None
                a_an = {}
                try:
                    tell(f"the room is watching {a.get('label') or 'the next asset'}")
                    a_tmp = _download(s3, bucket, a['s3_key'], os.path.splitext(str(a.get('filename') or ''))[1] or '.mp4')
                    a_an = CUT.analyze_cut(a_tmp, duration_s=float(a.get('runtime_s') or 0), progress=tell)
                    if a_an.get('frames'):
                        try:
                            from migration import digital_twin_watch as W
                            caps = W.caption_frames(a_an['frames'], transcript=a_an.get('transcript'), title=str(a.get('title') or a.get('label') or ''), usage_extras=extras)
                            a_an['captions'] = caps.get('captions') or {}
                            a_an['genre_votes'] = caps.get('genre_votes') or {}
                        except Exception as e:
                            print(f"[pilot-prepare {job_id}] asset captions skipped: {e}")
                    MODES.prepare_extra_asset(sess, a, twins_by_key, analysis=a_an, transcript=a_an.get('transcript'), usage_extras=extras, s3=s3, progress=tell)
                except Exception as e:
                    print(f"[pilot-prepare {job_id}] asset {a.get('label')} skipped: {e}")
                finally:
                    try:
                        CUT.cleanup(a_an)
                    except Exception:
                        pass
                    if a_tmp and os.path.isfile(a_tmp):
                        try:
                            os.remove(a_tmp)
                        except Exception:
                            pass
                    wd = (a_an or {}).get('work_dir')
                    if wd and os.path.isdir(wd):
                        shutil.rmtree(wd, ignore_errors=True)
            try:
                MODES.finish_mode_read(sess, twins_by_key, usage_extras=extras, prior=sess.get('prior_read') or None)
            except Exception as e:
                print(f"[pilot-prepare {job_id}] mode read after assets skipped: {e}")
        sess.pop('prepare_stage', None)
        sess['prepare_job_id'] = job_id
        if str((status_read(job_id) or {}).get('status') or '') == 'cancelled':
            print(f"[pilot-prepare {job_id}] cancelled while preparing; nothing persisted")
            return
        # write back onto a fresh copy of the twin so nothing that landed meanwhile is lost
        fresh = DT.load_twin(s3, key) or payload
        cur = _find(fresh, sid)
        if cur is not None:
            keep = {k: cur.get(k) for k in ('questions', 'title', 'play_charged', 'questions_paid', 'panelists_paid', 'purge_media_after', 'genre_set_by_user', 'mode_set_by_user',
                                              'comps', 'talent_attach', 'linked_run_id', 'recuts', 'play_price_key', 'play_quantity') if k in cur}
            if keep.get('genre_set_by_user') and cur.get('genre'):
                keep['genre'] = cur.get('genre')
            if keep.get('mode_set_by_user') and cur.get('mode'):
                keep['mode'] = cur.get('mode')
            if cur.get('status') in ('playing', 'stopped') and cur.get('play_charged'):
                keep['status'] = cur['status']
            sess.update({k: v for k, v in keep.items() if v is not None})
            idx = next(i for i, s in enumerate(fresh.get('pilot_sessions') or []) if str(s.get('id')) == sid)
            fresh['pilot_sessions'][idx] = sess
        else:
            fresh.setdefault('pilot_sessions', []).insert(0, sess)
        DT.save_twin(s3, key, fresh)
        w = sess.get('watch') or {}
        ready = f"The room is seated and the cut is ready to play: {sess.get('title')}."
        rec.update({'status': 'done', 'success': True, 'stage': 'done', 'finished_at': time.time(), 's3_key': key, 'session_id': sid,
                    'watch_source': w.get('source'), 'persona': bool(w.get('persona')), 'notes': (w.get('notes') or [])[:6], 'ready_message': ready})
        status_write(job_id, rec)
        print(f"[pilot-prepare {job_id}] ready: source={w.get('source')} persona={w.get('persona')} scenes={w.get('scenes_n')} captions={w.get('captions_n')} notes={w.get('notes')}")
        return rec
    except Exception as e:
        traceback.print_exc()
        rec.update({'status': 'error', 'stage': 'stopped', 'finished_at': time.time(),
                    'error': 'The cut could not be prepared. Press Play and the room runs on the clock; nothing more is charged.', 'detail': str(e)[:300]})
        status_write(job_id, rec)
        raise
    finally:
        try:
            from migration import digital_twin_cut as CUT
            CUT.cleanup(analysis)
        except Exception:
            pass
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        wd = (analysis or {}).get('work_dir')
        if wd and os.path.isdir(wd):
            shutil.rmtree(wd, ignore_errors=True)


__all__ = ['JOB_PREFIX', 'KIND', 'status_write', 'status_read', 'start', 'run_job']
