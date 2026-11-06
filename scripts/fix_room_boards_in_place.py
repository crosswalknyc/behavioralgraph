#!/usr/bin/env python3
"""Focus Group IQ boards, fixed in place (Jenna 2026-11-05: "It cant ever
have all the same answers on something in digital twins focus group").

    python3 scripts/fix_room_boards_in_place.py scan
    python3 scripts/fix_room_boards_in_place.py distinct  <twin key> <session id> <qid> [<qid> ...]
    python3 scripts/fix_room_boards_in_place.py rerun     <twin key> <session id> <qid> [<qid> ...]

scan      every room on every twin: the questions whose board carries a
          signature (three or more options sharing a share, Other or
          Something else leading or above 20%, a uniform board, any two
          rows alike).
distinct  the guard alone, in place: ties broken, people recomputed, the
          segments following, the headline and the read's split re-said;
          the seats' words stay. Falls back to a full re-run when the
          tenths would move a seat.
rerun     the question runs again through the room with the fixed code
          against the live models (the record is replaced by id, the seat
          memory refreshed, the job document refreshed).

Every touched twin is backed up first under digital-twins/_backups/
<name>.pre_board_guard_<ts>.json.gz. No email.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BUCKET = 'dashboard-inputs'
PREFIX = 'digital-twins/admin/'
BACKUPS = 'digital-twins/_backups/'


def _s3():
    import boto3
    return boto3.client('s3')


def _twin_keys(s3) -> list:
    out = []
    pag = s3.get_paginator('list_objects_v2')
    for page in pag.paginate(Bucket=BUCKET, Prefix='digital-twins/'):
        for o in page.get('Contents', []):
            k = o['Key']
            if k.endswith('.json.gz') and '/_backups/' not in k and '/portraits/' not in k and '/covers/' not in k and '.pre_' not in k.split('/')[-1]:
                out.append(k)
    return sorted(out)


def _board_rows(q: dict) -> list:
    return q.get('options') or q.get('distribution') or []


def scan(s3, keys=None) -> list:
    from migration import digital_twin_board as B
    from migration import digital_twin_synthesis as SYN
    from migration.digital_twin_room import all_sessions
    found = []
    for k in keys or _twin_keys(s3):
        payload = SYN.load_twin(s3, k)
        if not payload:
            continue
        for s in all_sessions(payload):
            for q in s.get('questions') or []:
                rows = _board_rows(q)
                if not rows:
                    continue
                flags = B.board_flags(rows)
                if flags:
                    found.append({'twin': k, 'session': s.get('id'), 'title': s.get('title'), 'kind': s.get('kind') or 'room', 'qid': q.get('id'), 'q': q.get('q'), 'q_kind': q.get('kind'), 'flags': flags,
                                  'board': [(str(r.get('label') or '')[:44], round(float(r.get('pct') or 0.0), 1), int(r.get('people') or 0)) for r in rows]})
    return found


def _backup(s3, key: str, payload: dict) -> str:
    name = key.split('/')[-1].replace('.json.gz', '')
    bk = f"{BACKUPS}{name}.pre_board_guard_{time.strftime('%Y%m%d_%H%M%S')}.json.gz"
    s3.put_object(Bucket=BUCKET, Key=bk, Body=gzip.compress(json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')), ContentType='application/json', ContentEncoding='gzip')
    return bk


def _job_refresh(s3, rec: dict, twin_key: str, sid: str):
    """The job document's copy of the question follows the record."""
    from migration import digital_twin_room as R
    try:
        job = R.job_read(s3, str(rec.get('job_id') or ''))
        if job:
            job['question'] = {k: v for k, v in rec.items() if k != 'trail'}
            job['updated_at'] = time.time()
            job['status'], job['stage'] = 'done', 'done'
            R._put_json(s3, R.job_key(str(rec['job_id'])), job)
    except Exception as e:
        print(f"[boards] job refresh skipped: {e}")


def distinct(s3, twin_key: str, sid: str, qids: list) -> list:
    """The guard alone, in place. Returns [(qid, before, after, note)]."""
    from migration import digital_twin_board as B
    from migration import digital_twin_synthesis as SYN
    from migration import digital_twin_room as R
    payload = SYN.load_twin(s3, twin_key)
    sess = R.find_session(payload, sid)
    if not payload or not sess:
        raise SystemExit(f"room not found: {twin_key} {sid}")
    bk = _backup(s3, twin_key, payload)
    print(f"[boards] backup {bk}")
    roster = (sess.get('group') or {}).get('roster') or []
    segments = (sess.get('group') or {}).get('segments') or []
    total = int(sum(int(sg.get('people') or 0) for sg in segments))
    out, rerun = [], []
    for qid in qids:
        rec = next((q for q in sess.get('questions') or [] if q.get('id') == qid), None)
        if not rec or not rec.get('options'):
            print(f"[boards] {qid}: no board, skipped")
            continue
        before = [(o['label'], o['pct'], o['people']) for o in rec['options']]
        shares_before = {o['id']: float(o['pct']) for o in rec['options']}
        seed = f"{sid}|{rec.get('id')}|{rec.get('q')}|board-fix"
        rep = B.guard_board(rec['options'], rec.get('by_segment') or [], total, len(roster), seed, priority=B.seat_priority(rec['options'], rec.get('anchor') or {}), answers=rec.get('answers'))
        shares_after = {o['id']: float(o['pct']) for o in rec['options']}
        if R.allocate_seats(shares_before, len(roster)) != R.allocate_seats(shares_after, len(roster)):
            print(f"[boards] {qid}: the tenths would move a seat; queued for a full re-run")
            rerun.append(qid)
            # put the board back as it was: the re-run rebuilds it
            for o, b in zip(rec['options'], before):
                o['pct'], o['people'] = b[1], b[2]
            continue
        rec['distribution'] = [{'label': o['label'], 'pct': o['pct'], 'people': o['people']} for o in rec['options']]
        head, head_people = R.headline_for(rec['options'], total, len(roster), R._twin_name(payload), watched_any=rec.get('watched_any'))
        rec['headline'], rec['headline_people'] = head, head_people
        if rec.get('read'):
            rec['read'] = R.cohere_read(rec['read'], rec['options'], len(roster), head)
        if rec.get('read_short'):
            rec['read_short'] = R.cohere_read(rec['read_short'], rec['options'], len(roster), R.short_read(head, 1) if head else '')
        rec.setdefault('trail', {})['board_guard'] = rep
        rec['trail']['board_fix'] = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'mode': 'distinct', 'backup': bk}
        after = [(o['label'], o['pct'], o['people']) for o in rec['options']]
        out.append((qid, before, after, 'distinct'))
        _job_refresh(s3, rec, twin_key, sid)
    sess['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    SYN.save_twin(s3, twin_key, payload)
    for qid, before, after, note in out:
        print(f"[boards] {qid} ({note})")
        for b, a in zip(before, after):
            print(f"    {b[0][:46]:<46} {b[1]:>6}% {b[2]:>12,}  ->  {a[1]:>6}% {a[2]:>12,}")
    if rerun:
        out += rerun_questions(s3, twin_key, sid, rerun)
    return out


def rerun_questions(s3, twin_key: str, sid: str, qids: list) -> list:
    """The question runs again through the room (the fixed code, the live
    models): the same record id, job id and words; the seats answer
    again with their memory of the earlier questions intact."""
    from migration import digital_twin_synthesis as SYN
    from migration import digital_twin_room as R
    from migration.digital_twin_engine import VERSION
    out = []
    for qid in qids:
        payload = SYN.load_twin(s3, twin_key)
        sess = R.find_session(payload, sid)
        if not payload or not sess:
            raise SystemExit(f"room not found: {twin_key} {sid}")
        bk = _backup(s3, twin_key, payload)
        print(f"[boards] backup {bk}")
        idx = next((i for i, q in enumerate(sess.get('questions') or []) if q.get('id') == qid), None)
        if idx is None:
            print(f"[boards] {qid}: not found, skipped")
            continue
        rec = sess['questions'][idx]
        before = [(o['label'], o['pct'], o['people']) for o in rec.get('options') or []]
        before_read = str(rec.get('read') or '')
        twins_by_key = {twin_key: payload}
        for tk in sess.get('twin_keys') or []:
            if tk and tk not in twins_by_key:
                t = SYN.load_twin(s3, tk)
                if t:
                    twins_by_key[tk] = t
        group = sess.get('group') or {}
        twin = twins_by_key.get(twin_key) or payload
        last = sess['questions'][idx - 1] if idx > 0 else None
        kept = {k: rec.get(k) for k in ('anchor', 'watched_any', 'mentions', 'multi_select')}
        for k, v in (('options', []), ('distribution', []), ('by_segment', []), ('read', ''), ('read_short', ''), ('voices', []), ('answers', []), ('probes_next', []), ('facts_used', []), ('tally', []),
                     ('anchor', None), ('watched_any', None), ('mentions', None)):
            rec[k] = v
        rec.pop('multi_select', None)
        rec['option_labels'] = []   # the moderator codes the board again
        rec['finished_at'] = None
        trail = rec.setdefault('trail', {})
        trail.setdefault('reruns', []).append({'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'why': 'board guard (2026-11-05)', 'backup': bk, 'board_before': before, 'kept': {k: bool(v) for k, v in kept.items()}})
        rec['stage'] = 'researching'
        job_id = str(rec.get('job_id') or '') or __import__('uuid').uuid4().hex[:12]
        rec['job_id'] = job_id
        ctx = {'rec': rec, 'session': sess, 'twins_by_key': twins_by_key, 'twin': twin, 'roster': list(group.get('roster') or []), 'models': R.Models(),
               's3': s3, 'twin_key': twin_key, 'job_id': job_id, 'seed': f"{sid}|{idx}|{rec.get('q')}|{VERSION}|{R.ROOM_VERSION}", 'session_seed': f"room|{sid}|{VERSION}",
               'last': last, 'asked_by': str(rec.get('asked_by') or ''), 'subject': R._twin_name(twin), 't0': time.time(), 'created_at': time.time(), 'notify': {}, 'wait_for_save': False}
        t0 = time.time()
        R.run_question(ctx)
        after = [(o['label'], o['pct'], o['people']) for o in rec.get('options') or []]
        print(f"[boards] {qid} re-run in {round(time.time() - t0)}s, fallbacks={rec.get('trail', {}).get('fallbacks')}, coding={json.dumps((rec.get('trail') or {}).get('coding') or {}, default=str)[:400]}")
        print('    before:')
        for b in before:
            print(f"      {str(b[0])[:52]:<52} {b[1]:>6}% {int(b[2]):>12,}")
        print('    after:')
        for a in after:
            print(f"      {str(a[0])[:52]:<52} {a[1]:>6}% {int(a[2]):>12,}")
        print(f"    headline: {rec.get('headline')}")
        print(f"    read: {rec.get('read')}")
        out.append((qid, before, after, 'rerun', before_read, rec.get('read'), rec.get('headline')))
    return out


def main(argv):
    if not argv or argv[0] not in ('scan', 'distinct', 'rerun'):
        print(__doc__)
        return 2
    s3 = _s3()
    if argv[0] == 'scan':
        found = scan(s3)
        for f in found:
            print(f"{f['twin'].split('/')[-1][:60]} | {f['session']} ({f['kind']}: {str(f['title'])[:28]}) | {f['qid']} [{f['q_kind']}] {str(f['q'])[:70]!r}")
            print('     ', f['flags'])
            print('     ', f['board'])
        print(f"flagged questions: {len(found)}")
        return 0
    if len(argv) < 4:
        print(__doc__)
        return 2
    twin_key, sid, qids = argv[1], argv[2], argv[3:]
    if argv[0] == 'distinct':
        distinct(s3, twin_key, sid, qids)
    else:
        rerun_questions(s3, twin_key, sid, qids)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
