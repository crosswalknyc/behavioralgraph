#!/usr/bin/env python3
"""Parity between the store's document mirror and S3 over the replay
corpus (Prometheus rebuild stage 6, 2026-11-20).

    python3 scripts/pm_store_parity.py --fixtures                 # the committed sanitized set
    python3 scripts/pm_store_parity.py --corpus latest --limit 300  # the real corpus (local cache; nothing printed but counts)

The harness is the replay's own (prometheus.replay, in process, fake
boto3, canned model, REGRESSION_TEST_MODE=1) with a SQLite store switched
on through PM_STORE_SQLITE. Every ask runs through the door; after each
one the documents it wrote to the fake S3 under the mirrored families
(threads and their index, generated-read job documents, the ledger index)
are compared byte for byte with the store's mirror. The second pass runs
the same asks with PM_STORE_READ=1 and the fake S3 emptied of the
mirrored keys after each write, so a thread or a job document is read
from the mirror and the asks still land.

Prints counts only. Never prints a question, a reply or a user name.
Exit code 0 when every written document has a byte-identical mirror.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
if WEB not in sys.path:
    sys.path.insert(0, WEB)
PM_CORPUS = os.path.join(HERE, 'pm_corpus')
if PM_CORPUS not in sys.path:
    sys.path.insert(0, PM_CORPUS)


def _load_cases(args):
    import replay_corpus as _rc
    if args.fixtures:
        cases = _rc.load_fixtures()
    else:
        import glob
        root = os.path.expanduser('~/.cache/crosswalk_replay/corpus')
        stamp = args.corpus
        if stamp == 'latest':
            link = os.path.join(root, 'latest')
            path = os.path.join(os.path.realpath(link) if os.path.islink(link) else link, 'cases.jsonl')
            if not os.path.exists(path):
                stamps = sorted(d for d in glob.glob(os.path.join(root, '*')) if os.path.isdir(d) and not d.endswith('latest'))
                path = os.path.join(stamps[-1], 'cases.jsonl') if stamps else ''
        else:
            path = os.path.join(root, stamp, 'cases.jsonl')
        if not path or not os.path.exists(path):
            raise SystemExit('no corpus at ' + str(path or root) + '; run with --fixtures or build the corpus first')
        cases = []
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if line:
                    cases.append(json.loads(line))
    if args.limit:
        cases = cases[:args.limit]
    return cases


def _item(c):
    th = c.get('thread') or {}
    return {'question': c['question'], 'view_id': c.get('view') or '',
            'subject': c.get('subject') or th.get('subject_label') or '',
            'history': list(c.get('history') or []), 'view_data': None,
            'logged_outcome': (c.get('recorded') or {}).get('outcome') or '',
            'mode': c.get('mode') or None, 'reading': c.get('reading') or None,
            'client_surface': c.get('surface') or None}


def run(cases, *, read_pass=True):
    db = tempfile.mktemp(prefix='pm_store_parity_', suffix='.db')
    os.environ['PM_STORE_SQLITE'] = db
    os.environ['PM_REPLAY_ALLOW_INPROCESS'] = '1'
    os.environ.pop('PM_STORE_READ', None)
    from prometheus import replay as _rp
    _rp.install_isolation()
    from prometheus import store as _st
    _st.reset_for_tests()
    h = _rp.harness()
    totals = {'cases': 0, 'checked': 0, 'same': 0, 'different': 0, 'only_s3': 0, 'by_family': {}, 'faults': 0}
    import threading

    def _settle(before):
        # a read runs on a background thread and lands in the thread after the
        # probe returns; the mirror follows each S3 put, so compare only once
        # the work the probe started has finished
        for t in list(threading.enumerate()):
            if t not in before and t is not threading.current_thread():
                try:
                    t.join(timeout=60)
                except Exception:
                    pass

    for c in cases:
        base = dict(_rp._STATE.get('baseline') or {})
        before_threads = set(threading.enumerate())
        live = _rp._probe_inprocess(**_item(c))
        _settle(before_threads)
        totals['cases'] += 1
        if str(live.get('route') or '').startswith('replay_fault'):
            totals['faults'] += 1
        objs = {k: v for k, v in (getattr(h.s3, 'objects', {}) or {}).items() if k not in base or base[k] != v}
        rep = _st.parity(objs)
        for k in ('checked', 'same', 'different', 'only_s3'):
            totals[k] += rep[k]
        for fam, f in rep['by_family'].items():
            t = totals['by_family'].setdefault(fam, {'checked': 0, 'same': 0, 'different': 0, 'only_s3': 0})
            for k in t:
                t[k] += f[k]
    totals['store_docs'] = _st.docs_count()
    read = None
    if read_pass:
        # the second pass: read from the mirror, S3 holding nothing of the mirrored families
        os.environ['PM_STORE_READ'] = '1'
        os.environ['PM_STORE_READ_VALIDATE'] = '0'   # the pass reads the mirror as it stands: S3 is emptied of the keys, so a checked read would hand them back
        hits0, miss0 = _st.STATS['doc_hit'], _st.STATS['doc_miss']
        read = {'cases': 0, 'landed': 0, 'faults': 0, 'mirror_reads': 0, 'mirror_misses': 0}
        for c in cases:
            before_threads = set(threading.enumerate())
            live = _rp._probe_inprocess(**_item(c))
            _settle(before_threads)
            read['cases'] += 1
            if str(live.get('route') or '').startswith('replay_fault'):
                read['faults'] += 1
            elif str(live.get('reply') or '').strip():
                read['landed'] += 1
            objs = getattr(h.s3, 'objects', {}) or {}
            for k in [k for k in objs if _st.mirrors(k)]:
                objs.pop(k, None)              # the next read of it can only come from the mirror
        read['mirror_reads'] = _st.STATS['doc_hit'] - hits0
        read['mirror_misses'] = _st.STATS['doc_miss'] - miss0
        os.environ.pop('PM_STORE_READ', None)
        os.environ.pop('PM_STORE_READ_VALIDATE', None)
    try:
        os.remove(db)
    except Exception:
        pass
    return totals, read


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--fixtures', action='store_true')
    ap.add_argument('--corpus', default='latest')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--no-read-pass', action='store_true')
    a = ap.parse_args(argv)
    cases = _load_cases(a)
    totals, read = run(cases, read_pass=not a.no_read_pass)
    print(f"parity over {totals['cases']} asks: {totals['checked']} mirrored documents written, "
          f"{totals['same']} byte-identical, {totals['different']} different, {totals['only_s3']} missing from the store; "
          f"{totals['store_docs']} rows in the mirror; {totals['faults']} replay faults")
    for fam, f in sorted(totals['by_family'].items()):
        print(f"  {fam:13s} written={f['checked']:5d} same={f['same']:5d} different={f['different']:3d} missing={f['only_s3']:3d}")
    if read:
        print(f"read pass (PM_STORE_READ=1, S3 emptied of mirrored keys as they land): {read['landed']} of {read['cases']} asks landed a reply, "
              f"{read['mirror_reads']} reads served from the mirror, {read['mirror_misses']} fell through to S3, {read['faults']} replay faults")
    ok = totals['different'] == 0 and totals['only_s3'] == 0
    print('PARITY OK' if ok else 'PARITY FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
