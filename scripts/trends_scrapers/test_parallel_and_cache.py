"""Offline regression test for the Trends IQ fan-out helper and the
lens_relevance score-reuse cache.

Hermetic: no network, no S3, no API key. Run it before any change to
`_parallel.py`, `lens_relevance.py`, or `headline_estimates.py`.

    python3 -m scripts.trends_scrapers.test_parallel_and_cache
"""

from __future__ import annotations

import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BGWEBAPP = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _BGWEBAPP not in sys.path:
    sys.path.insert(0, _BGWEBAPP)

from scripts.trends_scrapers import _parallel  # noqa: E402

_FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = '') -> None:
    if cond:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        _FAILURES.append(name)


# ---------------------------------------------------------------------------
def test_worker_count() -> None:
    print("worker_count")
    for var in (_parallel.SEQUENTIAL_ENV, 'X_WC'):
        os.environ.pop(var, None)
    check('default applies', _parallel.worker_count('X_WC', 32) == 32)

    os.environ['X_WC'] = '7'
    check('env override wins', _parallel.worker_count('X_WC', 32) == 7)

    os.environ['X_WC'] = 'banana'
    check('garbage falls back to default',
          _parallel.worker_count('X_WC', 32) == 32)

    os.environ['X_WC'] = '0'
    check('zero falls back to default (never hangs)',
          _parallel.worker_count('X_WC', 32) == 32)

    os.environ['X_WC'] = '9999'
    check('capped at maximum',
          _parallel.worker_count('X_WC', 32, maximum=64) == 64)

    os.environ[_parallel.SEQUENTIAL_ENV] = '1'
    check('kill switch forces 1 worker',
          _parallel.worker_count('X_WC', 32) == 1)
    os.environ.pop(_parallel.SEQUENTIAL_ENV)
    os.environ.pop('X_WC')


def test_imap_semantics() -> None:
    print("imap_unordered")
    items = list(range(50))

    got = {i: r for i, r, e in
           _parallel.imap_unordered(lambda x: x * 2, items, 8)}
    check('every item returns a result', len(got) == 50)
    check('values correct', all(got[i] == i * 2 for i in items))

    seq = [(i, r) for i, r, e in
           _parallel.imap_unordered(lambda x: x * 2, items, 1)]
    check('workers=1 preserves input order',
          [i for i, _ in seq] == items)

    # One bad item must not take the run down.
    def boom(x: int) -> int:
        if x == 13:
            raise ValueError('bad item')
        return x

    ok, errs = 0, 0
    for _it, _r, e in _parallel.imap_unordered(boom, items, 8):
        if e is None:
            ok += 1
        else:
            errs += 1
    check('one raising item is isolated', ok == 49 and errs == 1,
          f'ok={ok} errs={errs}')

    # Same in the sequential path.
    ok, errs = 0, 0
    for _it, _r, e in _parallel.imap_unordered(boom, items, 1):
        if e is None:
            ok += 1
        else:
            errs += 1
    check('sequential path isolates too', ok == 49 and errs == 1)

    seen: set[int] = set()
    lock = threading.Lock()

    def track(x: int) -> int:
        with lock:
            seen.add(threading.get_ident())
        time.sleep(0.02)
        return x

    list(_parallel.imap_unordered(track, items, 8))
    check('pool actually runs concurrently', len(seen) > 1,
          f'threads={len(seen)}')

    seen.clear()
    list(_parallel.imap_unordered(track, items[:5], 1))
    check('workers=1 creates no pool', len(seen) == 1,
          f'threads={len(seen)}')

    check('empty work list is a no-op',
          list(_parallel.imap_unordered(lambda x: x, [], 8)) == [])


def test_backoff() -> None:
    print("call_with_backoff")

    class Rate(Exception):
        pass

    calls = {'n': 0}

    def flaky():
        calls['n'] += 1
        if calls['n'] < 2:
            raise Rate('rate_limit_error: 429 too many requests')
        return 'done'

    t0 = time.time()
    check('retries a transient failure',
          _parallel.call_with_backoff(flaky, attempts=2, base_delay=0.05,
                                      salt='t') == 'done')
    check('slept before the retry', time.time() - t0 >= 0.02)

    calls['n'] = 0

    def always_bad():
        calls['n'] += 1
        raise ValueError('malformed prompt')

    try:
        _parallel.call_with_backoff(always_bad, attempts=3, base_delay=0.01)
        check('non-retryable raises', False)
    except ValueError:
        check('non-retryable raises', True)
    check('non-retryable is not retried', calls['n'] == 1,
          f'tries={calls["n"]}')

    check('429 classified retryable',
          _parallel.is_retryable(Exception('Error code: 429')))
    check('529 overloaded classified retryable',
          _parallel.is_retryable(Exception('overloaded_error')))
    check('read timeout classified retryable',
          _parallel.is_retryable(Exception('Request timed out')))
    check('bad request not retryable',
          not _parallel.is_retryable(Exception('invalid_request_error')))


def test_content_key() -> None:
    print("content_key")
    a = _parallel.content_key('podcast', 'Crime Junkie', '', 'ctx')
    b = _parallel.content_key('podcast', 'Crime Junkie', '', 'ctx')
    c = _parallel.content_key('podcast', 'Crime Junkie', '', 'other')
    check('stable for identical input', a == b)
    check('changes when any part changes', a != c)
    check('None and empty string agree',
          _parallel.content_key('x', None) == _parallel.content_key('x', ''))
    # Field boundaries must not be ambiguous.
    check('no field-boundary collision',
          _parallel.content_key('ab', 'c') != _parallel.content_key('a', 'bc'))


def test_lens_cache() -> None:
    print("lens_relevance score reuse")
    os.environ.setdefault('AWS_REGION', 'us-east-2')
    from scripts.trends_scrapers import lens_relevance as lr

    it = {'kind': 'podcast', 'title': 'Crime Junkie', 'artist': '',
          'context': 'audiochuck', 'seen_on': ['Apple']}
    fp = lr._item_fingerprint(it)

    same_provenance = dict(it, seen_on=['Apple', 'Spotify', 'iHeart'])
    check('seen_on churn does NOT invalidate',
          lr._item_fingerprint(same_provenance) == fp)

    check('context change DOES invalidate',
          lr._item_fingerprint(dict(it, context='different')) != fp)
    check('title change DOES invalidate',
          lr._item_fingerprint(dict(it, title='Morbid')) != fp)
    check('kind change DOES invalidate',
          lr._item_fingerprint(dict(it, kind='song')) != fp)

    lens = {'id': 'gen_z', 'persona': 'a persona brief'}
    p1 = lr._persona_fingerprint(lens)
    check('persona fingerprint stable',
          lr._persona_fingerprint(dict(lens)) == p1)
    check('persona edit invalidates',
          lr._persona_fingerprint(dict(lens, persona='edited')) != p1)

    real = next((l for l in lr._LENSES if l['id'] == 'gen_z'), None)
    if real:
        before = lr._persona_fingerprint(real)
        saved = lr._ANCHORS['gen_z']
        try:
            lr._ANCHORS['gen_z'] = list(saved) + [
                {'kind': 'song', 'title': 'zzz', 'score': 1}]
            check('anchor edit invalidates that lens',
                  lr._persona_fingerprint(real) != before)
        finally:
            lr._ANCHORS['gen_z'] = saved
        check('restoring anchors restores fingerprint',
              lr._persona_fingerprint(real) == before)

    # Cache load must reject a snapshot from a different prompt version
    # and a lens whose persona moved, without touching the network.
    snap = {
        'prompt_version': lr._PROMPT_VERSION,
        'persona_fingerprints': {'gen_z': 'FP_A', 'gen_x': 'FP_B'},
        'items': {
            'podcast:crime junkie': {
                'ck': 'CK1',
                'scores': {'gen_z': 88, 'gen_x': 41},
                'tilts':  {'gen_z': 2.1, 'gen_x': 0.8},
                'why':    {'gen_z': 'core true crime'},
            },
            'song:no ck here': {'scores': {'gen_z': 50}},
        },
    }
    orig_read = lr._read
    try:
        lr._read = lambda src: snap if src == 'lens_scores' else None
        cache = lr._load_prior_scores({'gen_z': 'FP_A', 'gen_x': 'FP_B'})
        check('reuses matching pairs', len(cache) == 2, f'n={len(cache)}')
        check('carries score/tilt/why',
              cache[('gen_z', 'CK1')] == {'score': 88, 'tilt': 2.1,
                                           'why': 'core true crime'})
        check('item without ck is skipped',
              all(k[1] == 'CK1' for k in cache))

        cache = lr._load_prior_scores({'gen_z': 'FP_CHANGED', 'gen_x': 'FP_B'})
        check('changed persona drops only that lens',
              set(cache) == {('gen_x', 'CK1')}, f'{sorted(cache)}')

        snap['prompt_version'] = 'something-else'
        check('prompt version bump drops everything',
              lr._load_prior_scores({'gen_z': 'FP_A'}) == {})
        snap['prompt_version'] = lr._PROMPT_VERSION

        os.environ[_parallel.SEQUENTIAL_ENV] = '0'
        os.environ['LENS_RELEVANCE_CACHE'] = '0'
        check('cache kill switch empties the cache',
              lr._load_prior_scores({'gen_z': 'FP_A'}) == {})
        os.environ.pop('LENS_RELEVANCE_CACHE')

        lr._read = lambda src: None
        check('missing snapshot degrades to full rescore',
              lr._load_prior_scores({'gen_z': 'FP_A'}) == {})
    finally:
        lr._read = orig_read


def test_headline_module() -> None:
    print("headline_estimates wiring")
    os.environ.setdefault('AWS_REGION', 'us-east-2')
    from scripts.trends_scrapers import headline_estimates as he
    check('concurrency is env-tunable',
          he._parallel.worker_count(he._CONCURRENCY_ENV,
                                     he._CONCURRENCY_DEFAULT)
          == he._CONCURRENCY_DEFAULT)
    os.environ[he._CONCURRENCY_ENV] = '3'
    check('override honoured',
          he._parallel.worker_count(he._CONCURRENCY_ENV,
                                     he._CONCURRENCY_DEFAULT) == 3)
    os.environ.pop(he._CONCURRENCY_ENV)
    check('retry budget unchanged at 2', he._ATTEMPTS == 2)
    check('no live calls without a key', he._research_all([]) == {})


def main() -> int:
    test_worker_count()
    test_imap_semantics()
    test_backoff()
    test_content_key()
    test_lens_cache()
    test_headline_module()
    print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)} -> {_FAILURES}")
        return 1
    print("all checks passed")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
