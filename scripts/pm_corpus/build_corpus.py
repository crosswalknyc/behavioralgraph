#!/usr/bin/env python3
"""Build the replay corpus from the real ask log (rebuild stage 1).

For every real ask in the last N days this records what was asked, what
was open, the thread's last two turns before it, the Reader's logged
reading, and what the reader got (route, outcome, class, the reply's
first sentence, whether a job started, whether money was quoted,
whether a long-wait mail went out). The result is the specification the
replay (replay_corpus.py) runs every later stage against.

Where it goes, and where it never goes:

  * ~/.cache/crosswalk_replay/corpus/<stamp>/cases.jsonl   (mode 600)
  * s3://dashboard-inputs/system/pm_replay_corpus/<stamp>/cases.jsonl.gz
    (the staff-only system/ prefix of the private dashboard bucket)
  * never git, never a public bucket, never a client-visible key. The
    manifest beside the cases carries counts only; nothing this script
    prints is a question or a reply. Seats are aliased s1, s2, ... in
    the cases; the alias map stays in the local manifest only.

Run with staff credentials in the environment:

    python3 scripts/pm_corpus/build_corpus.py --days 30
    python3 scripts/pm_corpus/build_corpus.py --days 30 --no-upload
    python3 scripts/pm_corpus/build_corpus.py --from-jsonl ~/.cache/crosswalk_replay/raw/ask_log.jsonl

Read-only against production: it lists and gets objects; the only
writes are the corpus files above.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
WEBAPP = os.path.dirname(os.path.dirname(HERE))
if WEBAPP not in sys.path:
    sys.path.insert(0, WEBAPP)

BUCKET = os.environ.get('S3_BUCKET') or 'dashboard-inputs'
ASK_PREFIX = 'system/usage/ask_log/'
THREAD_PREFIX = 'system/synth_chat_threads/'
MAIL_PREFIX = 'system/outbound_mail/'
CORPUS_PREFIX = 'system/pm_replay_corpus/'
CACHE_ROOT = os.path.expanduser(os.environ.get('PM_REPLAY_CACHE') or '~/.cache/crosswalk_replay')

# Identities that are not readers (the probes, the replay stand-in, the
# unknown door). prometheus.probe_identity carries the same list for
# the live path; this copy keeps the builder importable without it.
SKIP_USERS = frozenset({'', 'unknown', 'replay', 'sandbox_partner', 'canary', 'smoke', 'probe', 'golden', 'tester', 'grader'})
_PROBE_RX = re.compile(r"(?:^|[^a-z])(?:canary|smoke|sandbox|regression|golden|probe)(?:[^a-z]|$)", re.I)

HISTORY_TURNS = 2
_META_KEEP = ('options', 'offer', 'panel_offer', 'read_job_id', 'deck_job_id', 'bpiq_job_id', 'jiq_job_id',
              'spec_draft', 'draft', 'landed_read_id', 'pending', 'kind', 'route', 'bpiq_wizard')


def is_real_seat(user):
    u = str(user or '').strip()
    if not u or u.lower() in SKIP_USERS or u.lower().startswith('canary'):
        return False
    return not _PROBE_RX.search(u)


# ------------------------------------------------------------------ parse
def parse_repr(text):
    """The dict a truncated Python repr carried (the ask log keeps
    extra.reading and extra.thread as str(dict)[:200]). Whole when it
    parses; otherwise the leading key / value pairs that do."""
    s = str(text or '').strip()
    if not s:
        return {}
    if isinstance(text, dict):
        return dict(text)
    try:
        v = ast.literal_eval(s)
        return v if isinstance(v, dict) else {}
    except Exception:
        pass
    out = {}
    for m in re.finditer(r"'([a-z_]+)':\s*('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|[-0-9.]+|True|False|None)", s):
        k = m.group(1)
        raw = m.group(2)
        try:
            out[k] = ast.literal_eval(raw)
        except Exception:
            out[k] = raw.strip("'\"")
    # subjects: the as_written / catalog pairs that are whole in the prefix
    subs = []
    for m in re.finditer(r"\{'as_written':\s*'((?:[^'\\]|\\.)*)',\s*'catalog':\s*'((?:[^'\\]|\\.)*)',\s*'kind':\s*'([a-z]*)'\}", s):
        subs.append({'as_written': m.group(1), 'catalog': m.group(2), 'kind': m.group(3)})
    if subs:
        out['subjects'] = subs
    return out


def parse_reading(extra):
    rd = parse_repr((extra or {}).get('reading'))
    if not rd or not rd.get('intent'):
        return None
    try:
        conf = float(rd.get('confidence'))
    except (TypeError, ValueError):
        conf = None
    subs = rd.get('subjects') if isinstance(rd.get('subjects'), list) else []
    subs = [s for s in subs if isinstance(s, dict)][:6]
    flags = rd.get('flags') if isinstance(rd.get('flags'), list) else []
    return {'intent': str(rd.get('intent'))[:40], 'confidence': conf, 'subjects': subs,
            'flags': [str(f)[:40] for f in flags][:8],
            'restate': str(rd.get('restate') or '')[:240] or None,
            'truncated': not isinstance((extra or {}).get('reading'), dict)}


def parse_thread(extra):
    th = parse_repr((extra or {}).get('thread'))
    if not th:
        return {}
    return {k: th.get(k) for k in ('id', 'subject_key', 'subject_label', 'product') if th.get(k) is not None}


def first_sentence(text, limit=160):
    t = ' '.join(str(text or '').split())
    if not t:
        return ''
    m = re.search(r"^(.{12,}?[.!?])(?:\s|$)", t)
    s = m.group(1) if m else t
    return s[:limit]


def _ts(s):
    try:
        return datetime.strptime(str(s or '')[:19].replace('T', ' ').replace('Z', ''), '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except Exception:
        return None


def pick_history(turns, question, ts, n=HISTORY_TURNS):
    """The n turns before this ask in its thread. The ask's own user
    turn is found by text (and the nearest stamp when the same words
    were sent more than once); failing that, the last n turns stamped
    before the ask."""
    turns = [t for t in (turns or []) if isinstance(t, dict)]
    if not turns:
        return []
    qn = ' '.join(str(question or '').split()).lower()
    at = _ts(ts)
    idx = None
    best = None
    for i, t in enumerate(turns):
        if str(t.get('role') or '') != 'user':
            continue
        if ' '.join(str(t.get('text') or '').split()).lower() != qn:
            continue
        tt = _ts(t.get('ts'))
        gap = abs((tt - at).total_seconds()) if (tt and at) else 1e9
        if best is None or gap < best:
            best, idx = gap, i
    if idx is None and at is not None:
        before = [i for i, t in enumerate(turns) if (_ts(t.get('ts')) or at) <= at]
        idx = (before[-1] + 1) if before else 0
    if idx is None:
        idx = len(turns)
    out = []
    for t in turns[max(0, idx - n):idx]:
        slim = {'role': 'user' if str(t.get('role') or '') == 'user' else 'agent',
                'text': ' '.join(str(t.get('text') or t.get('content') or '').split())[:1200],
                'ts': t.get('ts')}
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        keep = {}
        for k in _META_KEEP:
            v = meta.get(k)
            if v in (None, '', [], {}):
                continue
            try:
                if len(json.dumps(v)) <= 1500:
                    keep[k] = v
            except Exception:
                continue
        if keep:
            slim['meta'] = keep
        out.append(slim)
    return out


def case_id(rec):
    base = f"{rec.get('user')}|{rec.get('ts')}|{rec.get('question')}"
    return hashlib.sha1(base.encode('utf-8')).hexdigest()[:12]


def recorded_of(rec, mailed=None):
    try:
        from prometheus import intent_classes as _ic
        cls = _ic.class_of(rec.get('route'), rec.get('outcome'), rec.get('surface'))
    except Exception:
        cls = 'other'
    reply = str(rec.get('reply') or '')
    outcome = str(rec.get('outcome') or '')
    quoted = outcome in ('panel_offer', 'year_package_offer', 'quoted') or bool(re.search(r"\$\s?\d", reply))
    return {'route': str(rec.get('route') or ''), 'outcome': outcome, 'class': cls,
            'reply_first': first_sentence(reply), 'job': bool(rec.get('job_id')), 'quoted': quoted,
            'charged': None, 'mailed': mailed, 'ms': int(rec.get('ms') or 0),
            'promised_outcome': rec.get('promised_outcome')}


# --------------------------------------------------------------------- S3
def _s3():
    import boto3
    return boto3.client('s3', region_name=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION') or 'us-east-2')


def _list(s3, prefix):
    keys, token = [], None
    while True:
        kw = {'Bucket': BUCKET, 'Prefix': prefix}
        if token:
            kw['ContinuationToken'] = token
        r = s3.list_objects_v2(**kw)
        keys += [o['Key'] for o in (r.get('Contents') or [])]
        token = r.get('NextContinuationToken')
        if not token:
            break
    return keys


def _get_json(s3, key, default=None):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read().decode('utf-8') or 'null')
    except Exception:
        return default


def read_ask_log(s3, days, until=None):
    until = until or datetime.now(timezone.utc).date()
    day_list = [(until - timedelta(days=i)).isoformat() for i in range(int(days))]
    with ThreadPoolExecutor(max_workers=16) as ex:
        per_day = list(ex.map(lambda d: _list(s3, f'{ASK_PREFIX}{d}/'), day_list))
    keys = [k for ks in per_day for k in ks if k.endswith('.json') and '/_' not in k[len(ASK_PREFIX):]]
    with ThreadPoolExecutor(max_workers=32) as ex:
        recs = [r for r in ex.map(lambda k: _get_json(s3, k), keys) if isinstance(r, dict)]
    return recs


def read_mail_log(s3, days, until=None):
    """Long-wait answer mails by day: (day, to, logged_at) triples."""
    until = until or datetime.now(timezone.utc).date()
    out = []
    try:
        day_list = [(until - timedelta(days=i)).isoformat() for i in range(int(days))]
        with ThreadPoolExecutor(max_workers=16) as ex:
            per_day = list(ex.map(lambda d: _list(s3, f'{MAIL_PREFIX}{d}/'), day_list))
        keys = [k for ks in per_day for k in ks if k.endswith('.json')]
        with ThreadPoolExecutor(max_workers=32) as ex:
            for rec in ex.map(lambda k: _get_json(s3, k), keys):
                if isinstance(rec, dict) and rec.get('sent') and str(rec.get('caller') or '') == 'long_ask':
                    out.append((str(rec.get('to') or '').lower(), str(rec.get('logged_at') or '')))
    except Exception:
        pass
    return out


def _mailed(rec, mails):
    """True / False when the mail log was read, None when it was not."""
    if mails is None:
        return None
    u = str(rec.get('user') or '').lower()
    at = _ts(rec.get('ts'))
    if not u or at is None:
        return False
    for to, when in mails:
        if not (to == u or to.startswith(u + '@') or (u.startswith(to.split('@')[0] + '') and '@' in to and to.split('@')[0] == u)):
            continue
        mt = _ts(when)
        if mt and 0 <= (mt - at).total_seconds() <= 1200:
            return True
    return False


# ------------------------------------------------------------------- build
def build_cases(recs, thread_loader, mails=None, *, seats=None, days_label=''):
    """Cases from ask-log records. `thread_loader(user, tid) -> turns`."""
    seats = seats if seats is not None else {}
    cases = []
    for rec in sorted(recs, key=lambda r: str(r.get('ts') or '')):
        if not isinstance(rec, dict) or not is_real_seat(rec.get('user')):
            continue
        q = str(rec.get('question') or '').strip()
        if not q:
            continue
        extra = rec.get('extra') if isinstance(rec.get('extra'), dict) else {}
        user = str(rec.get('user') or '')
        alias = seats.setdefault(user, f"s{len(seats) + 1}")
        thread = parse_thread(extra)
        history = []
        if thread.get('id'):
            try:
                history = pick_history(thread_loader(user, str(thread['id'])), q, rec.get('ts'))
            except Exception:
                history = []
        case = {
            'id': case_id(rec), 'ts': rec.get('ts'), 'day': str(rec.get('ts') or '')[:10],
            'question': q[:600], 'view': str(rec.get('view') or ''), 'surface': str(rec.get('surface') or 'analyze'),
            'mode': rec.get('mode') or None, 'subject': str(rec.get('subject') or '') or None,
            'thread': thread or None, 'history': history,
            'reading': parse_reading(extra),
            'recorded': recorded_of(rec, mailed=_mailed(rec, mails)),
            'seat': alias,
            'signals': {k: extra.get(k) for k in ('user_signal', 'result', 'decision', 'order') if extra.get(k) is not None},
        }
        cases.append(case)
    return cases


def manifest_of(cases, *, days, stamp, seats):
    by_class = Counter(c['recorded']['class'] for c in cases)
    by_day = Counter(c['day'] for c in cases)
    return {
        'stamp': stamp, 'built_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'days': int(days), 'n': len(cases), 'seats': len(seats),
        'with_history': sum(1 for c in cases if c['history']),
        'with_reading': sum(1 for c in cases if c['reading']),
        'by_class': dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
        'by_day': dict(sorted(by_day.items())),
        'quoted': sum(1 for c in cases if c['recorded']['quoted']),
        'jobs': sum(1 for c in cases if c['recorded']['job']),
        'mailed': sum(1 for c in cases if c['recorded']['mailed']),
    }


def write_local(cases, manifest, seats, stamp):
    root = os.path.join(CACHE_ROOT, 'corpus', stamp)
    os.makedirs(root, mode=0o700, exist_ok=True)
    cases_path = os.path.join(root, 'cases.jsonl')
    with open(cases_path, 'w', encoding='utf-8') as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False) + '\n')
    os.chmod(cases_path, 0o600)
    with open(os.path.join(root, 'manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=1)
    # the alias map stays local: it is the only place a seat alias meets a username
    seats_path = os.path.join(root, 'seats.json')
    with open(seats_path, 'w', encoding='utf-8') as fh:
        json.dump(seats, fh, indent=1)
    os.chmod(seats_path, 0o600)
    with open(os.path.join(CACHE_ROOT, 'corpus', 'latest'), 'w') as fh:
        fh.write(stamp + '\n')
    return root


def upload(s3, cases, manifest, stamp):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as gz:
        for c in cases:
            gz.write((json.dumps(c, ensure_ascii=False) + '\n').encode('utf-8'))
    s3.put_object(Bucket=BUCKET, Key=f'{CORPUS_PREFIX}{stamp}/cases.jsonl.gz', Body=buf.getvalue(),
                  ContentType='application/gzip')
    s3.put_object(Bucket=BUCKET, Key=f'{CORPUS_PREFIX}{stamp}/manifest.json', Body=json.dumps(manifest, indent=1).encode('utf-8'),
                  ContentType='application/json')
    s3.put_object(Bucket=BUCKET, Key=f'{CORPUS_PREFIX}latest.json', Body=json.dumps({'stamp': stamp, 'n': manifest['n']}).encode('utf-8'),
                  ContentType='application/json')
    return f's3://{BUCKET}/{CORPUS_PREFIX}{stamp}/'


def latest_stamp():
    try:
        with open(os.path.join(CACHE_ROOT, 'corpus', 'latest')) as fh:
            return fh.read().strip()
    except Exception:
        return ''


def load_cases(path_or_stamp='latest'):
    """Cases from a local corpus (a stamp, 'latest', or a path to a
    .jsonl / .jsonl.gz)."""
    p = str(path_or_stamp or 'latest')
    if p == 'latest':
        p = latest_stamp()
        if not p:
            raise FileNotFoundError('no local corpus yet; run build_corpus.py')
    if not os.path.exists(p):
        p = os.path.join(CACHE_ROOT, 'corpus', p, 'cases.jsonl')
    opener = gzip.open if p.endswith('.gz') else open
    out = []
    with opener(p, 'rt', encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def fetch_from_s3(stamp='latest'):
    """Pull a corpus the box built down to the local cache; returns its stamp."""
    s3 = _s3()
    if stamp == 'latest':
        stamp = (_get_json(s3, f'{CORPUS_PREFIX}latest.json') or {}).get('stamp') or ''
        if not stamp:
            raise FileNotFoundError('no corpus in S3 yet')
    body = s3.get_object(Bucket=BUCKET, Key=f'{CORPUS_PREFIX}{stamp}/cases.jsonl.gz')['Body'].read()
    manifest = _get_json(s3, f'{CORPUS_PREFIX}{stamp}/manifest.json') or {}
    cases = [json.loads(l) for l in gzip.decompress(body).decode('utf-8').splitlines() if l.strip()]
    write_local(cases, manifest, {}, stamp)
    return stamp


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--until', default='', help='last day to include, YYYY-MM-DD (default today, UTC)')
    ap.add_argument('--from-jsonl', default='', help='read ask-log records from a local jsonl instead of S3')
    ap.add_argument('--no-upload', action='store_true')
    ap.add_argument('--no-mail-join', action='store_true')
    ap.add_argument('--stamp', default='')
    args = ap.parse_args(argv)
    until = datetime.strptime(args.until, '%Y-%m-%d').date() if args.until else datetime.now(timezone.utc).date()
    stamp = args.stamp or datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    s3 = _s3()
    t0 = time.time()
    if args.from_jsonl:
        with open(os.path.expanduser(args.from_jsonl), encoding='utf-8') as fh:
            recs = [json.loads(l) for l in fh if l.strip()]
        cutoff = (until - timedelta(days=args.days - 1)).isoformat()
        recs = [r for r in recs if cutoff <= str(r.get('ts') or '')[:10] <= until.isoformat()]
    else:
        recs = read_ask_log(s3, args.days, until)
    print(f'ask log: {len(recs)} records over {args.days} days ({time.time() - t0:.0f}s)')
    mails = None if args.no_mail_join else read_mail_log(s3, args.days, until)
    cache = {}

    def _thread(user, tid):
        k = f'{THREAD_PREFIX}{user}/{tid}.json'
        if k not in cache:
            doc = _get_json(s3, k, [])
            cache[k] = doc if isinstance(doc, list) else []
        return cache[k]

    seats = {}
    cases = build_cases(recs, _thread, mails, seats=seats)
    manifest = manifest_of(cases, days=args.days, stamp=stamp, seats=seats)
    root = write_local(cases, manifest, seats, stamp)
    print(f"corpus {stamp}: {manifest['n']} cases from {manifest['seats']} seats; "
          f"{manifest['with_history']} with thread context, {manifest['with_reading']} with a reading; "
          f"{manifest['quoted']} quoted, {manifest['jobs']} started a job, {manifest['mailed']} mailed")
    print('by class: ' + ', '.join(f'{k} {v}' for k, v in manifest['by_class'].items()))
    print(f'local: {root}')
    if not args.no_upload:
        print('s3: ' + upload(s3, cases, manifest, stamp))
    return 0


if __name__ == '__main__':
    sys.exit(main())
