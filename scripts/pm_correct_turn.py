#!/usr/bin/env python3
"""Correct one Prometheus thread turn by hand, the right way (Jenna 2026-11-23).

The week of 2026-11-16 every staff correction was a direct S3 write of the
thread JSON with ``meta.corrected_at``; the ledger row behind the wrong
words stayed live, the words could still replay from the job payload, and
nothing reached the replay corpus. This is the one complete path a staff
correction needs, through `prometheus.corrections.correct_turn`:

  1. a backup first: the thread to
     system/synth_chat_threads/_backups/<user>/<thread>.pre_correct_<ts>.json
     and every job document the correction touches to
     system/prometheus_reads/_backups/<job>.pre_correct_<ts>.json;
  2. the turn takes the new words, ``meta.corrected_at`` and
     ``meta.landed_at`` (merge-safe: the thread merge reads the stamp as the
     server's correction, a newer stamp wins); the wrong answer's chips,
     figures and links come off; the save is the ETag-guarded merge,
     mirrored to the store by the client hook, never a raw put;
  3. every read job document behind the turn (a resumed chain included) is
     settled through the house writer with the corrected reply and the
     stamp; the email-promise marker is turned off on every pass (the chain
     marker, the notify side-file, the open held records);
  4. the ledger retires the old words and sets them aside, scoped to this
     subject, window, seat and thread (never a house stock line, never
     another user's copy of the same words), and banks the figures you
     name, with the new words, as the decision of record for the subject
     (resolved from the turn's own question or the thread's own subject,
     never a neighbor turn);
  5. a sanitized fixture candidate is queued under
     system/pm_replay_corpus/candidates/ for the weekly corpus rebuild;
  6. a merge check against the backup before it returns: when a stale copy
     of the thread could write over the correction, the thread goes back
     to the backup and the report says so.

    python3 scripts/pm_correct_turn.py --user liz --thread api-8b09f13b-chat-1120-cefe --job 1fb22513ad09 \\
        --text-file corrected.txt --by jenna --note "positioning answer written by hand"

    python3 scripts/pm_correct_turn.py --user skolton --thread dcc24fb595 --starts-with "Across the full partnership period" \\
        --text-file corrected.txt --by jenna --subject "Anne Hathaway x Shiseido" --window 2023-09-06:2026-09-08 \\
        --figure "total_brand_value=104140107.40 usd" --figure "attributable_value=31262000 usd"

Find the turn by --job (the read job id on the turn), --turn-id, --index
(0-based, negative from the end) or --starts-with (the old words). Pass
--dry-run to see the turn it would change, the subject it would file under
and the jobs it would settle, and stop. Staff credentials only (the
bg-webapp .env loaded); never run under REGRESSION_TEST_MODE. Nothing is
emailed; nobody is told.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

THREADS_PREFIX = 'system/synth_chat_threads'
READ_PREFIX = 'system/prometheus_reads/'
NOTIFY_PREFIX = 'system/prometheus_notify/'


def _safe_user(username):
    return ''.join(c for c in (username or 'anon') if c.isalnum() or c in '-_.@').lower()


def _thread_key(username, tid):
    safe_t = ''.join(c for c in str(tid) if c.isalnum() or c in '-_')
    return f"{THREADS_PREFIX}/{_safe_user(username)}/{safe_t}.json"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--user', required=True, help='the seat (username) whose thread it is')
    ap.add_argument('--thread', required=True, help='the thread id')
    ap.add_argument('--job', default='', help='the read job id on the turn')
    ap.add_argument('--turn-id', default='')
    ap.add_argument('--index', type=int, default=None)
    ap.add_argument('--starts-with', default='', help='the first words of the turn to correct')
    ap.add_argument('--text', default='', help='the corrected words (or --text-file)')
    ap.add_argument('--text-file', default='')
    ap.add_argument('--by', required=True, help='who is correcting (jenna, jessie, liz)')
    ap.add_argument('--note', default='', help='why, in a few words (internal)')
    ap.add_argument('--subject', default='', help='the subject the corrected figures belong to (else the turn\'s own)')
    ap.add_argument('--window', default='', help='YYYY-MM-DD:YYYY-MM-DD for the corrected figures')
    ap.add_argument('--figure', action='append', default=[], help='name=value unit, repeatable (unit: usd, pct, people)')
    ap.add_argument('--no-learn', action='store_true', help='the thread and job only; no ledger, no fixture')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--bucket', default=os.environ.get('S3_BUCKET', 'dashboard-inputs'))
    a = ap.parse_args(argv)

    if os.environ.get('REGRESSION_TEST_MODE') == '1':
        print('refusing: REGRESSION_TEST_MODE=1 (a test process never corrects a live thread)')
        return 2
    new_text = a.text
    if a.text_file:
        new_text = Path(a.text_file).read_text(encoding='utf-8')
    new_text = str(new_text or '').strip()
    if not new_text and not a.dry_run:
        print('nothing to write: give --text or --text-file')
        return 2
    if not (a.job or a.turn_id or a.index is not None or a.starts_with):
        print('say which turn: --job, --turn-id, --index or --starts-with')
        return 2

    import boto3
    from prometheus import thread_merge as _tm
    from prometheus import corrections as C
    from prometheus import read_recovery as _rr
    from prometheus import seams as _seams
    _store = None
    try:
        from prometheus import store as _store
        _store.install_mirror()            # the store mirrors every put this client makes; never mirror by hand
    except Exception as e:
        print(f"(store mirror not installed: {e})")
    s3 = boto3.client('s3')
    bucket = a.bucket
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')

    def _get_json(key):
        try:
            return json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8'))
        except Exception:
            return None

    def _put_json(key, doc):
        s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(doc).encode('utf-8'), ContentType='application/json')

    def load(u, t):
        turns, _etag = _tm.read_turns(s3, bucket, _thread_key(u, t))
        return turns

    def save(u, t, history):
        return _tm.merge_save(s3, bucket, _thread_key(u, t), list(history or []))

    def restore(u, t, history):
        # the backup copy back verbatim: the merge would keep the stamped correction
        _put_json(_thread_key(u, t), list(history or []))

    def job_doc(j):
        return _get_json(f"{READ_PREFIX}{j}.json")

    def job_write(j, doc):
        # the house writer's shape: the job tag, the store's shadow row, then S3
        doc = _seams.tag_job(dict(doc), 'read')
        try:
            if _store is not None and _store.enabled():
                _store.upsert_job('read', dict(doc, job_id=doc.get('job_id') or j))
        except Exception:
            pass
        _put_json(f"{READ_PREFIX}{j}.json", doc)

    def backup(kind, ident, doc):
        if kind == 'thread':
            u, _, t = str(ident).partition('/')
            key = f"{THREADS_PREFIX}/_backups/{_safe_user(u)}/{''.join(c for c in t if c.isalnum() or c in '-_')}.pre_correct_{stamp}.json"
        else:
            key = f"{READ_PREFIX}_backups/{''.join(c for c in str(ident) if c.isalnum())}.pre_correct_{stamp}.json"
        _put_json(key, doc)
        return key

    def chain_marker_write(j, doc):
        _put_json(f"{_rr.CHAIN_PREFIX}{j}.json", doc)

    def notify_delete(j):
        try:
            s3.delete_object(Bucket=bucket, Key=f"{NOTIFY_PREFIX}{j}.json")
        except Exception:
            pass

    def held_list(prefix):
        try:
            r = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            return [str(o.get('Key') or '') for o in (r.get('Contents') or [])]
        except Exception:
            return []

    host = C.CorrectionHost(load_thread=load, save_thread=save, read_thread=load, restore_thread=restore, job_doc=job_doc, job_write=job_write,
                            chain_marker=lambda j: _get_json(f"{_rr.CHAIN_PREFIX}{j}.json") or {},
                            chain_marker_write=chain_marker_write, notify_delete=notify_delete, backup=backup,
                            held_list=held_list, held_get=_get_json, held_write=_put_json)

    history = load(a.user, a.thread)
    if not history:
        print(f"no thread at {_thread_key(a.user, a.thread)}")
        return 1
    i = C._find_turn(history, turn_id=a.turn_id, job_id=a.job, index=a.index, text_prefix=a.starts_with)
    if i is None:
        print('turn not found')
        return 1
    turn = history[i]
    meta = turn.get('meta') or {}
    print(f"turn {i} of {len(history)} | role {turn.get('role')} | ts {turn.get('ts')} | meta {json.dumps({k: v for k, v in meta.items() if k in ('read_job_id', 'landed_job_id', 'corrected_at', 'subject', 'kind')})}")
    print('old words: ' + ' '.join(str(turn.get('text') or '').split())[:300])
    jid = str(meta.get('read_job_id') or meta.get('landed_job_id') or a.job or '')
    doc = job_doc(jid) if jid else None
    subj, question, how = C.resolve_subject(turn, doc, history, i, given=a.subject)
    print(f"subject: {subj or '(none: nothing will be banked)'} (from {how}) | own question: {' '.join(str(question).split())[:160] or '(none)'}")
    if jid:
        print(f"jobs to settle: {', '.join(C.job_chain(jid, host))}")
    if C.is_stock_line(turn.get('text')):
        print('the old words are a house line: nothing is retired or set aside by them')
    if a.dry_run:
        print('new words: ' + ' '.join(new_text.split())[:300] if new_text else '(no new words given)')
        print('dry run: nothing written')
        return 0
    window = None
    if a.window:
        ws, _, we = a.window.partition(':')
        window = {'start': ws.strip(), 'end': we.strip()}
    report = C.correct_turn(a.user, a.thread, new_text, by=a.by, turn_id=a.turn_id, job_id=a.job, index=a.index,
                            text_prefix=a.starts_with, figures=a.figure, subject=a.subject, window=window, note=a.note,
                            host=host, learn=not a.no_learn)
    print(json.dumps(report, indent=2, default=str))
    return 0 if (report.get('merge_check') or {}).get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
