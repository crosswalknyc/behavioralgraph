"""A probe leaves nothing behind (Jenna 2026-11-11: "these errors cannot
happen").

The standing rule: a `canary:` identity (the golden set, the canary, the
smoke, a regression replay, a sandbox key) leaves no artifacts, banks
nothing, charges nothing and never mails. The ledger hole was closed in
prometheus.ledger_gate. Two holes were left:

  * Files. A probe's replay of a stored read wrote a fresh CSV under
    `generated_data/` every time, exactly as a reader's replay does
    (seven on the night of 2026-11-11 from one question; the nightly
    golden run left six a night; three more from a replay of another
    reader's brief). Every writer builds its own key and calls the
    store itself, so there was no one place that knew who was asking.
  * Job records. A probe that runs a real read (the smoke, a sandbox
    key) leaves the job's record under `system/prometheus_reads/` until
    the weekly clean-up.

One choke point each:

  * `install(client)` puts one rule on the store client itself, and on
    every client made after it: when the work in flight is a probe's
    (`ledger_gate.is_probe`, the one answer to who is asking), a key
    under a prefix a reader could be handed a link to
    (`READER_PREFIXES`) is written, read and signed under
    `PROBE_PREFIX` instead. The caller's code is untouched: it gets a
    link, the replay can open the file to grade it, and nothing lands
    where a reader's files live. Only a file the probe wrote is read
    from there; any other file is read where it is.
  * `job_ended(job_id)`, called by the job scope when a probe's job
    ends, leaves a small note under `PROBE_PREFIX`; `sweep` (the worker's
    regular pass, and the nightly clean-up as a backstop) removes the
    note, the job's record and every probe file once they are an hour
    old. An hour, not at once: the smoke polls its job for the answer.

`run_access(check, key)` is the read-only side of the same identity: the
golden door (shared secret, build server only) has no session, so a
deck request that named a library profile was refused as "Not
authenticated" and could not be graded. A probe from that door may
resolve a library profile for a deck; it still builds no deck and writes
no file.

Nothing here raises into a request.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict

__all__ = ['READER_PREFIXES', 'PROBE_PREFIX', 'reader_facing', 'probe_key', 'is_probe', 'install', 'job_ended',
           'sweep', 'sweep_with_host', 'run_access', 'redirected', 'clear_redirected']

READER_PREFIXES = ('generated_data/', 'generated_decks/', 'deliverables/')
PROBE_PREFIX = 'system/prometheus_probe/'
FILES_PREFIX = PROBE_PREFIX + 'files/'
JOBS_PREFIX = PROBE_PREFIX + 'jobs/'
KEEP_S = 3600                      # a probe's files and job records live an hour, so the replay can read them
_JOB_RECORDS = {'read': 'system/prometheus_reads/', 'deck': 'system/prometheus_decks/'}
_OPS = ('PutObject', 'GetObject', 'HeadObject', 'DeleteObject', 'CopyObject', 'CreateMultipartUpload')
_MARK = '_pm_probe_files'
_REDIRECTED = []                   # the short in-process trail the tests and the logs read
_REDIRECTED_MAX = 60


def reader_facing(key):
    k = str(key or '').lstrip('/')
    return any(k.startswith(p) for p in READER_PREFIXES)


def probe_key(key):
    """Where a probe's copy of a reader-facing key lives."""
    return FILES_PREFIX + str(key or '').lstrip('/')


def is_probe():
    """True when the work in flight is a probe's: the request says so,
    or the job was launched by one. Never raises."""
    try:
        from . import ledger_gate as _lg
        if _lg.is_probe():
            return True
    except Exception:
        pass
    try:
        from . import faults as _f
        return bool(_f.is_probe())
    except Exception:
        return False


def _note(op, key):
    try:
        _REDIRECTED.append({'op': str(op), 'key': str(key)[:300], 'at': time.time()})
        del _REDIRECTED[:-_REDIRECTED_MAX]
        if op in _WRITES:
            print(f"[probe-files] a probe's file is kept apart: {str(key)[:160]}")
    except Exception:
        pass


def redirected():
    return list(_REDIRECTED)


def clear_redirected():
    del _REDIRECTED[:]


_WRITES = frozenset(('PutObject', 'CopyObject', 'CreateMultipartUpload', 'put_object', 'copy_object', 'upload_file',
                     'upload_fileobj', 'copy'))
_DELETES = frozenset(('DeleteObject', 'delete_object'))
_WRITTEN = OrderedDict()           # reader-facing keys this process wrote for a probe (the probe copy exists)
_WRITTEN_MAX = 4000
_WRITTEN_LOCK = threading.Lock()


def _remember(key):
    with _WRITTEN_LOCK:
        _WRITTEN[str(key)] = time.time()
        _WRITTEN.move_to_end(str(key))
        while len(_WRITTEN) > _WRITTEN_MAX:
            _WRITTEN.popitem(last=False)


def _wrote(key):
    with _WRITTEN_LOCK:
        return str(key) in _WRITTEN


def _rewrite(key, op=''):
    """The key to use when the asker is a probe and the key is one a
    reader could be handed. A write goes to the probe's own copy, always.
    A read (and a link) goes to the probe's copy only when this process
    wrote one: a probe that reads a file it did not write (a reader's own
    deck, looked up by a golden case) reads the reader's file as it is,
    at no extra cost (2026-11-12: looking for a probe copy first, one
    request per file, pushed a three-minute deliverables lookup past the
    nightly grader's limit). A probe never deletes a reader's file."""
    try:
        if key and reader_facing(key) and is_probe():
            if op in _WRITES:
                _remember(key)
                _note(op, key)
                return probe_key(key)
            if op in _DELETES or _wrote(key):
                return probe_key(key)
    except Exception:
        pass
    return key


# ---------------------------------------------------------------- the client
def _handler_for(client=None):
    """botocore 'provide-client-params' for one client: the request's own
    parameters, returned changed when the key is a probe's."""
    def handler(params, model=None, context=None, **_kw):
        try:
            if not isinstance(params, dict) or 'Key' not in params:
                return None
            if isinstance(context, dict):
                if context.get(_MARK):
                    return None          # this client's own handler has already settled the key
                if client is not None:
                    context[_MARK] = True
            key = params.get('Key')
            new = _rewrite(key, getattr(model, 'name', '') or '')
            return dict(params, Key=new) if new != key else None
        except Exception:
            return None
    return handler


def _register(events, client=None):
    fn = _handler_for(client)
    for op in _OPS:
        if client is None:
            events.register(f'provide-client-params.s3.{op}', fn, unique_id=f'pm-probe-files-{op}')
        else:
            # ahead of the rule a client inherits from the session, which then stands aside
            events.register_first(f'provide-client-params.s3.{op}', fn, unique_id=f'pm-probe-files-own-{op}')


def _wrap_plain(client):
    """A store client that is not botocore's (the in-memory store the
    tests use): the same rule on its own methods."""
    def wrap(name, key_arg):
        fn = getattr(client, name, None)
        if not callable(fn) or getattr(fn, _MARK, False):
            return

        def inner(*a, **k):
            if key_arg in k:
                k[key_arg] = _rewrite(k[key_arg], name)
            return fn(*a, **k)
        setattr(inner, _MARK, True)
        try:
            setattr(client, name, inner)
        except Exception:
            pass
    for name in ('put_object', 'get_object', 'head_object', 'delete_object', 'copy_object'):
        wrap(name, 'Key')
    gen = getattr(client, 'generate_presigned_url', None)
    if callable(gen) and not getattr(gen, _MARK, False):
        def signed(method, Params=None, **k):
            p = dict(Params or {})
            if 'Key' in p:
                p['Key'] = _rewrite(p['Key'], 'put_object' if str(method) == 'put_object' else 'presign')
            return gen(method, Params=p, **k)
        setattr(signed, _MARK, True)
        try:
            setattr(client, 'generate_presigned_url', signed)
        except Exception:
            pass


_TRANSFERS = {'upload_file': 2, 'upload_fileobj': 2, 'download_file': 1, 'download_fileobj': 1, 'copy': 2}


def _wrap_transfers(client):
    """The managed transfers run the request on a worker thread, where
    nobody knows who is asking: the key is settled here, on the calling
    thread, before the transfer starts."""
    for name, pos in _TRANSFERS.items():
        fn = getattr(client, name, None)
        if not callable(fn) or getattr(fn, _MARK, False):
            continue

        def inner(*a, _fn=fn, _pos=pos, _name=name, **k):
            if 'Key' in k:
                k['Key'] = _rewrite(k['Key'], _name)
            elif len(a) > _pos:
                a = a[:_pos] + (_rewrite(a[_pos], _name),) + a[_pos + 1:]
            return _fn(*a, **k)
        setattr(inner, _MARK, True)
        try:
            setattr(client, name, inner)
        except Exception:
            pass


def install(client=None):
    """Put the rule on `client` and on every store client made after
    this call. Safe to call more than once. Returns the client."""
    try:
        import boto3
        _register(boto3._get_default_session().events)     # every client made from here on
    except Exception:
        pass
    if client is None:
        return client
    try:
        events = getattr(getattr(client, 'meta', None), 'events', None)
        if events is not None and hasattr(events, 'register'):
            _register(events, client)
            _wrap_transfers(client)
        else:
            _wrap_plain(client)
            _wrap_transfers(client)
    except Exception:
        pass
    return client


# ---------------------------------------------------------------- job records
def _store(host=None):
    if host is None:
        from .host import host as _h
        host = _h
    return host.s3_client, host.bucket


def job_ended(job_id, kind='read', host=None, now=None):
    """A probe's job ended: leave the note the pass removes it by. Called
    by the job scope for a probe's job only. Never raises."""
    try:
        jid = re.sub(r'[^A-Za-z0-9_-]', '', str(job_id or ''))[:40]
        if not jid:
            return False
        s3, bucket = _store(host)
        doc = {'job_id': jid, 'kind': str(kind or 'read'), 'records': [pre + jid + '.json' for pre in _JOB_RECORDS.values()],
               'ended_at': float(now or time.time())}
        s3.put_object(Bucket=bucket, Key=f"{JOBS_PREFIX}{jid}.json", Body=json.dumps(doc).encode('utf-8'), ContentType='application/json')
        return True
    except Exception:
        return False


def _list(s3, bucket, prefix):
    out, token = [], None
    while True:
        kw = dict(Bucket=bucket, Prefix=prefix)
        if token:
            kw['ContinuationToken'] = token
        r = s3.list_objects_v2(**kw)
        out += list(r.get('Contents') or [])
        if not r.get('IsTruncated'):
            return out
        token = r.get('NextContinuationToken')


def _age(obj, now):
    lm = obj.get('LastModified')
    try:
        return now - lm.timestamp()
    except Exception:
        return 0.0 if lm is None else KEEP_S + 1.0


def sweep(s3, bucket, now=None, keep_s=KEEP_S, log=print):
    """One pass: remove every probe file and every ended probe job's
    record (and its note) that is more than `keep_s` old. Returns
    {'files': n, 'jobs': n}. Never raises."""
    now = float(now or time.time())
    out = {'files': 0, 'jobs': 0}
    try:
        for o in _list(s3, bucket, JOBS_PREFIX):
            key = str(o.get('Key') or '')
            try:
                doc = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8'))
            except Exception:
                doc = {}
            ended = float((doc or {}).get('ended_at') or 0) or (now - _age(o, now))
            if now - ended < keep_s:
                continue
            for rec in list((doc or {}).get('records') or [])[:4]:
                if str(rec).startswith(tuple(_JOB_RECORDS.values())):
                    try:
                        s3.delete_object(Bucket=bucket, Key=str(rec))
                    except Exception:
                        pass
            s3.delete_object(Bucket=bucket, Key=key)
            out['jobs'] += 1
        for o in _list(s3, bucket, FILES_PREFIX):
            if _age(o, now) < keep_s:
                continue
            s3.delete_object(Bucket=bucket, Key=str(o.get('Key')))
            out['files'] += 1
        if (out['files'] or out['jobs']) and log:
            log(f"[probe-files] cleared {out['files']} probe file(s) and {out['jobs']} probe job record(s)")
    except Exception:
        pass
    return out


def sweep_with_host(host, now=None):
    try:
        if not (host.has('s3_client') and host.has('bucket') and host.bucket):
            return {'files': 0, 'jobs': 0}
        return sweep(host.s3_client, host.bucket, now=now)
    except Exception:
        return {'files': 0, 'jobs': 0}


# ---------------------------------------------------------------- read-only library access
def run_access(check, key):
    """The library access check for a deck request. A probe from the
    golden door (no session; shared secret, build server only) may
    resolve a library profile: it builds no deck and writes no file, so
    the only thing it learns is the profile's name. Everyone else gets
    `check(key)` exactly as before."""
    try:
        from flask import g
        from . import faults as _f
        if _f.is_probe() and str(getattr(g, '_pm_ask_user', '') or '').lower().startswith('canary:'):
            return True, None
    except Exception:
        pass
    return check(key)
