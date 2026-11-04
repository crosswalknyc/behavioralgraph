#!/usr/bin/env python3
"""Find and restore Prometheus thread turns a stale save dropped (2026-11-04).

Jenna: "the data should always be storing it so that it reconciles and
should always keep track." Until prometheus.thread_merge landed, a
dashboard tab holding a stale copy of a thread could post its copy over
the stored one and every plain turn the server held (the reader's own
questions, the plain answers) was dropped. This tool reads the record
back together:

* every ``<tid>.pre_*.json`` sibling written beside a live thread on the
  audited days (the before/after copies a repair left) is merged back
  into the live thread, by timestamp, never duplicating, never changing
  a text;
* every question in the ask log (system/usage/ask_log/<day>/) that no
  live thread of that user carries is put back as a user turn, stamped
  at the moment the server received it (the log's ts minus its handling
  time), into the thread the log names or the thread whose turns sit
  nearest in time. A question the log truncated (600 characters) is
  reported, never restored from the stub. Replies are not restored from
  the log (it keeps 400 characters of them).

Dry run by default. ``--apply`` writes: a ``<tid>.pre_turn_restore_<ts>.json``
copy of the live thread first, then the merged thread through the same
ETag-guarded merge every save uses, then the index's turn count.

    python3 scripts/pm_thread_restore_audit.py --days 2026-11-03 2026-11-04
    python3 scripts/pm_thread_restore_audit.py --days 2026-11-03 2026-11-04 --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import thread_merge as tm  # noqa: E402

BUCKET = os.environ.get('S3_BUCKET', 'dashboard-inputs')
THREADS = 'system/synth_chat_threads/'
ASK_LOG = 'system/usage/ask_log/'
TRUNCATED_AT = 600          # the ask log keeps this many characters of a question
NEAR_HOURS = 3.0            # a question with no thread named lands in the thread whose turns sit this close


def safe_user(username):
    return ''.join(c for c in (username or 'anon') if c.isalnum() or c in '-_.@').lower()


def iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def parse_iso(s):
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception:
        return None


def list_keys(s3, prefix):
    out = []
    token = None
    while True:
        kw = dict(Bucket=BUCKET, Prefix=prefix, MaxKeys=1000)
        if token:
            kw['ContinuationToken'] = token
        r = s3.list_objects_v2(**kw)
        for o in r.get('Contents') or []:
            out.append((o['Key'], o.get('LastModified')))
        if not r.get('IsTruncated'):
            return out
        token = r.get('NextContinuationToken')


def load_json(s3, key, default=None):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read().decode('utf-8'))
    except Exception:
        return default


def norm(text):
    return ' '.join(str(text or '').split()).lower()


# The same words this close to the moment the log received them are the
# same send, wherever they were saved (a project switch mid-ask lands the
# turn in the thread the widget moved to; a stale tab's copy carries the
# original stamp). The same words minutes apart are another send.
PRESENT_WINDOW_SECONDS = 3 * 60


def question_present(q, threads, received=None):
    """True when a live thread of the user carries this question as a user
    turn near the moment the log received it (a truncated log entry
    matches the turn it is a prefix of; a clarify-merged turn carries the
    question inside it). The same words at another time are another turn."""
    nq = norm(q)
    if not nq:
        return True
    truncated = len(q) >= TRUNCATED_AT
    for turns in threads.values():
        for t in turns:
            if str(t.get('role') or '') != 'user':
                continue
            nt = norm(t.get('text'))
            if not (nt == nq or (truncated and nt.startswith(nq)) or (len(nq) > 40 and nq in nt)):
                continue
            if received is None:
                return True
            e = parse_iso(t.get('ts'))
            if e is None or abs((e - received).total_seconds()) <= PRESENT_WINDOW_SECONDS:
                return True
    return False


def thread_hint(entry):
    raw = str(((entry.get('extra') or {}).get('thread')) or '')
    m = re.search(r"'id':\s*'([0-9a-zA-Z_-]+)'", raw) or re.search(r'"id":\s*"([0-9a-zA-Z_-]+)"', raw)
    return m.group(1) if m else ''


def nearest_thread(at, threads):
    best, gap = '', None
    for tid, turns in threads.items():
        for t in turns:
            e = parse_iso(t.get('ts'))
            if not e:
                continue
            g = abs((e - at).total_seconds())
            if gap is None or g < gap:
                best, gap = tid, g
    if gap is not None and gap <= NEAR_HOURS * 3600:
        return best
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', nargs='+', required=True, help='YYYY-MM-DD (UTC) days to audit')
    ap.add_argument('--user', default='', help='only this user')
    ap.add_argument('--apply', action='store_true', help='write the restores (dry run otherwise)')
    args = ap.parse_args()
    import boto3
    s3 = boto3.client('s3')
    days = set(args.days)
    now = datetime.now(timezone.utc)
    stamp = now.strftime('%Y-%m-%dT%H%M%SZ')

    # --- the thread store ------------------------------------------------
    objects = list_keys(s3, THREADS)
    live, siblings = {}, {}
    for key, lm in objects:
        rel = key[len(THREADS):]
        if '/_removed/' in key or rel.count('/') != 1:
            continue
        user, name = rel.split('/', 1)
        if name == 'index.json' or not name.endswith('.json'):
            continue
        base = name[:-5]
        if '.pre_' in base:
            tid = base.split('.pre_', 1)[0]
            tag = base.split('.pre_', 1)[1]
            day = ''
            m = re.search(r'(20\d\d-\d\d-\d\d)', tag)
            if m:
                day = m.group(1)
            elif lm is not None:
                day = lm.astimezone(timezone.utc).strftime('%Y-%m-%d')
            if day in days:
                siblings.setdefault((user, tid), []).append(key)
            continue
        live.setdefault(user, {})[base] = key

    # --- the ask log -----------------------------------------------------
    asks = []
    for day in sorted(days):
        for key, _lm in list_keys(s3, ASK_LOG + day + '/'):
            if not key.endswith('.json'):
                continue
            d = load_json(s3, key) or {}
            u = str(d.get('user') or '')
            if not u or u.startswith('canary:') or not str(d.get('question') or '').strip():
                continue
            if str((d.get('extra') or {}).get('user_signal') or '') == 'repeat':
                continue        # the approval of an earlier ask re-logs that ask's words; nothing new was typed
            asks.append(dict(d, _key=key))

    users = set(siblings_user for siblings_user, _t in siblings) | {safe_user(a['user']) for a in asks}
    if args.user:
        users = {u for u in users if u == safe_user(args.user)}

    report, plans = [], {}
    for user in sorted(users):
        idx = load_json(s3, f'{THREADS}{user}/index.json', {}) or {}
        index_ids = {t.get('id') for t in (idx.get('threads') or [])}
        keys = {tid: k for tid, k in (live.get(user) or {}).items() if tid in index_ids}
        threads = {tid: (load_json(s3, k, []) or []) for tid, k in keys.items()}

        # a) before/after copies beside a live thread
        for (su, tid), copies in sorted(siblings.items()):
            if su != user:
                continue
            if tid not in threads:
                report.append(f'{user}/{tid}: {len(copies)} copy file(s) but no live thread in the index; not touched')
                continue
            cur = threads[tid]
            have = set(tm.keys_of(cur))
            for ck in sorted(copies):
                copy = load_json(s3, ck, []) or []
                missing = [t for t in copy if isinstance(t, dict) and (tm.stamp(t) + (tm.text_key(t.get('text')),)) not in have]
                if not missing:
                    report.append(f'{user}/{tid}: {ck.rsplit("/", 1)[1]} holds nothing the live thread lacks')
                    continue
                for t in missing:
                    plans.setdefault((user, tid), []).append((t, 'copy ' + ck.rsplit('/', 1)[1]))
                    have.add(tm.stamp(t) + (tm.text_key(t.get('text')),))
                    threads[tid] = threads[tid] + [t]       # the ask log is judged against the thread as it will read

        # b) the ask log's questions
        last_seen = {}
        for a in sorted((x for x in asks if safe_user(x['user']) == user), key=lambda x: str(x.get('ts') or '')):
            q = str(a.get('question') or '')
            at = parse_iso(a.get('ts')) or now
            ms = a.get('ms')
            try:
                received = at - timedelta(milliseconds=float(ms)) if ms else at - timedelta(seconds=5)
            except Exception:
                received = at - timedelta(seconds=5)
            prev = last_seen.get(norm(q))
            last_seen[norm(q)] = received
            if prev is not None and abs((received - prev).total_seconds()) <= 2:
                continue        # one send logged twice
            if question_present(q, threads, received):
                continue
            hint = thread_hint(a)
            tid = hint if hint in threads else nearest_thread(received, threads)
            label = f'{user} ask {a.get("ts")} ' + repr(q[:70])
            if not tid:
                report.append(label + ': missing from every live thread; no thread near it in time; not restored')
                continue
            if len(q) >= TRUNCATED_AT:
                report.append(label + f': missing from {tid}; the log holds only the first {TRUNCATED_AT} characters; not restored from the stub')
                continue
            turn = {'role': 'user', 'text': q, 'ts': iso(received),
                    'meta': {'restored_from': 'ask_log', 'restored_at': iso(now), 'ask_key': a['_key']}}
            plans.setdefault((user, tid), []).append((turn, 'ask log ' + a['_key'].rsplit('/', 1)[1]))
            threads[tid] = threads[tid] + [turn]

    # --- report ----------------------------------------------------------
    for line in report:
        print('  note:', line)
    if not plans:
        print('nothing to restore')
        return 0
    for (user, tid), items in sorted(plans.items()):
        key = f'{THREADS}{user}/{tid}.json'
        cur = load_json(s3, key, []) or []
        incoming = [t for t, _src in items]
        merged = tm.merge_turns(cur, incoming)
        print(f'\n{user}/{tid}: {len(cur)} turns -> {len(merged)} ({len(merged) - len(cur)} restored)')
        for t, src in sorted(items, key=lambda x: tm.turn_epoch(x[0])):
            print(f'   + {str(t.get("ts"))[:24]:24s} {str(t.get("role")):5s} {str(t.get("text") or "")[:72].replace(chr(10), " ")!r}  <- {src}')
        backup = f'{THREADS}{user}/{tid}.pre_turn_restore_{stamp}.json'
        if not args.apply:
            print(f'   would back up to s3://{BUCKET}/{backup}')
            continue
        s3.copy_object(Bucket=BUCKET, CopySource={'Bucket': BUCKET, 'Key': key}, Key=backup,
                       MetadataDirective='COPY')
        written = tm.merge_save(s3, BUCKET, key, incoming)
        idx_key = f'{THREADS}{user}/index.json'
        idx = load_json(s3, idx_key, {}) or {}
        for th in idx.get('threads') or []:
            if th.get('id') == tid:
                th['turns'] = len(written)
        s3.put_object(Bucket=BUCKET, Key=idx_key, Body=json.dumps(idx, indent=2).encode('utf-8'),
                      ContentType='application/json')
        print(f'   written: {len(written)} turns; backup s3://{BUCKET}/{backup}')
    if not args.apply:
        print('\n(dry run; add --apply to write)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
