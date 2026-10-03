"""Screen warm-up for Prometheus (2026-10-02 S7, speed).

The ask log shows the digest stage (parse the open profile and its
cuts, score against Gen Pop and the norms) costing about five seconds
on a median screen ask, paid again on every question because the
in-process caches miss between visits. The widget now tells the server
what is on screen the moment a profile loads or the composer takes
focus; the server builds the digest bundle in the background so the
first question finds it already cached and skips straight to the model.

Contract:

- `POST /api/brief-chat/warm` with `{page_context: {...}}` answers at
  once with `{success, warming, key}`; it never blocks on the work.
- One in-flight warm per screen key; a repeat within
  `WARM_MIN_INTERVAL_S` of the last finished warm is a no-op (the
  bundle TTL and the etag-keyed digest cache already hold it).
- Nothing here meters, logs an ask, or writes anywhere but the
  in-process caches. A failure is printed and swallowed; the ask
  path takes its normal live route.
- `PM_SCREEN_WARM=0` disables the whole thing.
"""
import os
import threading
import time
import traceback

from flask import jsonify, request

from prometheus.legacy import H as _H  # noqa: E402

WARM_MIN_INTERVAL_S = 45

_LOCK = threading.Lock()
_INFLIGHT = set()
_LAST_DONE = {}


def enabled():
    return str(os.environ.get('PM_SCREEN_WARM', '1')).strip().lower() \
        not in ('0', 'false', 'no', 'off')


def warm_key(page_context):
    """Stable key for what is on screen: the primary profile plus the
    first three cuts. '' when there is nothing cacheable (a view with
    no profile open sends its data with each ask)."""
    try:
        ctx = page_context if isinstance(page_context, dict) else {}
        prim = str(((ctx.get('primary') or {}).get('s3_key')) or '').strip()
        if not prim.lower().endswith('.csv'):
            return ''
        cuts = tuple(sorted(
            str(c.get('s3_key') or '').strip()
            for c in (ctx.get('cuts') or [])[:3]
            if isinstance(c, dict) and c.get('s3_key')))
        return prim + '|' + ','.join(cuts)
    except Exception:
        return ''


def warm_page_context(page_context, s3_client=None, bucket=None):
    """Synchronous body: prime every cache the screen ask path reads
    for this context. Returns a small report for logs and tests."""
    t0 = time.monotonic()
    s3 = s3_client if s3_client is not None else _H.s3_client
    bkt = bucket if bucket is not None else _H.S3_BUCKET
    report = {'key': warm_key(page_context), 'digest': False,
              'frame': False, 'genpop': False, 'norms': False}
    import prometheus_analysis as pma
    try:
        pma.load_genpop_map(s3, bkt)
        report['genpop'] = True
    except Exception:
        traceback.print_exc()
    try:
        pma.load_norms(s3, bkt)
        report['norms'] = True
    except Exception:
        traceback.print_exc()
    ctx = dict(page_context or {})
    ctx['cuts'] = list((ctx.get('cuts') or [])[:3])
    try:
        pma.get_digest_bundle(s3, bkt, ctx)
        report['digest'] = True
    except Exception as e:
        if not pma.is_missing_key_error(e):
            traceback.print_exc()
    # The named-entity rows on the anchors stage reload the primary
    # frame through the parsed-profile LRU; the index path above may
    # have skipped the parse, so prime the frame too.
    try:
        prim = str(((ctx.get('primary') or {}).get('s3_key')) or '')
        if prim.lower().endswith('.csv'):
            pma.load_profile_df(s3, bkt, prim)
            report['frame'] = True
    except Exception as e:
        if not pma.is_missing_key_error(e):
            traceback.print_exc()
    report['ms'] = int((time.monotonic() - t0) * 1000)
    return report


def _run(key, page_context):
    try:
        rep = warm_page_context(page_context)
        print(f"[pm-warm] {key[:80]!r} digest={rep.get('digest')} "
              f"frame={rep.get('frame')} {rep.get('ms')}ms")
    except Exception:
        traceback.print_exc()
    finally:
        with _LOCK:
            _INFLIGHT.discard(key)
            _LAST_DONE[key] = time.time()
            if len(_LAST_DONE) > 200:
                for k, _ in sorted(_LAST_DONE.items(),
                                   key=lambda kv: kv[1])[:50]:
                    _LAST_DONE.pop(k, None)


def schedule_warm(page_context, runner=None):
    """Dedupe and hand the warm to a background thread. Returns
    {'warming': bool, 'key': str, 'reason': str}. `runner` lets a test
    run the body inline."""
    if not enabled():
        return {'warming': False, 'key': '', 'reason': 'disabled'}
    key = warm_key(page_context)
    if not key:
        return {'warming': False, 'key': '', 'reason': 'nothing_to_warm'}
    with _LOCK:
        if key in _INFLIGHT:
            return {'warming': False, 'key': key, 'reason': 'in_flight'}
        last = _LAST_DONE.get(key, 0.0)
        if time.time() - last < WARM_MIN_INTERVAL_S:
            return {'warming': False, 'key': key, 'reason': 'recent'}
        _INFLIGHT.add(key)
    if runner is not None:
        runner(key, page_context)
    else:
        threading.Thread(target=_run, args=(key, page_context),
                         daemon=True, name='pm-warm').start()
    return {'warming': True, 'key': key, 'reason': 'scheduled'}


def reset_for_tests():
    with _LOCK:
        _INFLIGHT.clear()
        _LAST_DONE.clear()


@_H.app.route('/api/brief-chat/warm', methods=['POST'])
@_H.requires_auth
@_H._chatbot_route_guard('brief-chat/warm')
def api_brief_chat_warm():
    """The widget calls this when a profile loads or the composer
    takes focus. Always 200, never blocks, never meters."""
    body = request.get_json(silent=True) or {}
    ctx = body.get('page_context')
    res = schedule_warm(ctx if isinstance(ctx, dict) else {})
    return jsonify({'success': True, 'warming': bool(res.get('warming')),
                    'key': res.get('key') or ''})
