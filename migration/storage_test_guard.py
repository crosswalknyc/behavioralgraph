"""A test process cannot write to a live store (2026-11-12).

Jenna: "Make it impossible, not just absent tonight."

The suites run on machines that hold real credentials. The build
server's nightly handed every suite its whole environment until
2026-11-12, and a suite run by hand from a shell with the dashboard's
environment loaded has them too. Suites that reached a real client
through the code they drive wrote to production: fixture figures banked
into the saved-answers ledger every night from 2026-10-14, test rows in
the mail log, two real builds ordered on 2026-11-05, four emails sent to
an outside address in mid-October.

The rule sits on the storage client itself, the way the probe rule does
(prometheus.probe_files), so it does not depend on which module made the
client or on how the suite was started. Under REGRESSION_TEST_MODE=1:

  * every write through a real S3 client is refused (PutObject, the
    deletes, CopyObject, a multipart upload and the managed transfers
    built on them) unless
      - the bucket is one a suite stands up for itself (TEST_BUCKETS, a
        name that starts or ends with "test", PM_TEST_BUCKETS), or
      - the client talks to a local stand-in rather than AWS, or
      - the key is under a scratch prefix a test named with
        `allow_scratch` (it must sit under SCRATCH_ROOT);
  * every mail send through a real SES client is refused.

Reads are untouched. A fake store a suite hands over is not a real
client and never comes here. The refusal is an S3-shaped error
(`TestModeWriteRefused`, a ClientError), so code that already survives a
failed write behaves as it does when S3 says no.

Installed once per process, on the client class, by `install()`: every
client that exists and every client made later is covered, and the rule
stays in force for the life of the process whatever a suite later does
to the flag. It is called
when the `migration` package is imported and by the modules that make
the clients the suites reach (the ledger, the ask log, the Prometheus
package). Outside a test process nothing is installed and nothing
changes.

Mirrored into bg-webapp/migration/ by scripts/sync_module_twins.py.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys

__all__ = ['SCRATCH_ROOT', 'TEST_BUCKETS', 'TestModeWriteRefused', 'active', 'install', 'installed', 'allow_scratch',
           'refused', 'clear_refused', 'why_refused']

SCRATCH_ROOT = '_test_scratch/'
TEST_BUCKETS = frozenset(('b', 'x', 'test', 'stub-bucket', 'subscriber-bucket', 'bucket', 'my-bucket', 'fake-bucket'))
S3_WRITES = frozenset((
    'PutObject', 'DeleteObject', 'DeleteObjects', 'CopyObject', 'CreateMultipartUpload', 'UploadPart', 'UploadPartCopy',
    'CompleteMultipartUpload', 'AbortMultipartUpload', 'PutObjectAcl', 'PutObjectTagging', 'DeleteObjectTagging',
    'RestoreObject', 'PutBucketLifecycleConfiguration', 'PutBucketPolicy', 'DeleteBucket', 'CreateBucket'))
MAIL_SENDS = frozenset(('SendEmail', 'SendRawEmail', 'SendTemplatedEmail', 'SendBulkTemplatedEmail', 'SendBulkEmail'))
LOG_ENV = 'PM_TEST_GUARD_LOG'            # a file the refusals are appended to, one JSON line each (a measurement aid)
_STATE_ATTR = '_pm_test_guard_state'
_MARK = '_pm_test_guard'


def started_as_a_test():
    """True when the process was started to run tests: its entry script
    is a test file, or a test runner started it. A tool that loads the
    suites' harness and then switches the flag off to do real work (the
    portrait backfill does) is not one: for it the flag alone decides."""
    try:
        main = sys.modules.get('__main__')
        path = str(getattr(main, '__file__', '') or (sys.argv[0] if sys.argv else '') or '').replace(os.sep, '/')
    except Exception:
        path = ''
    name = path.rsplit('/', 1)[-1]
    if name.startswith('test_') or name in ('pytest', 'py.test', 'conftest.py'):
        return True
    if '/unittest/' in path or '/pytest/' in path or '/_pytest/' in path:
        return True
    return bool(os.environ.get('PYTEST_CURRENT_TEST')) or 'pytest' in sys.modules


def active():
    """True in a test process. A process that was started as a test and
    saw the flag stays one: a suite that switches the flag off to
    exercise production behaviour is still a suite (one did, and its
    next write went to the live corrections store)."""
    if os.environ.get('REGRESSION_TEST_MODE') == '1':
        return True
    try:
        import botocore.client as bc
        return bool((getattr(bc.BaseClient, _STATE_ATTR, None) or {}).get('test_process'))
    except Exception:
        return False


def _state():
    """One state for the process, kept on the client class so every copy
    of this module (the parent's and the mirror) shares it."""
    import botocore.client as bc
    st = getattr(bc.BaseClient, _STATE_ATTR, None)
    if not isinstance(st, dict):
        st = {'scratch': set(), 'refused': []}
        setattr(bc.BaseClient, _STATE_ATTR, st)
    return st


try:
    from botocore.exceptions import ClientError as _ClientError
except Exception:                                           # pragma: no cover - botocore is always present with boto3
    _ClientError = Exception


class TestModeWriteRefused(_ClientError):
    """A write a test process tried to make through a real client."""

    __test__ = False                                        # not a test class, whatever a collector thinks of the name

    def __init__(self, what, operation_name='PutObject'):
        msg = f"a test process may not write to a live store: {what}"
        try:
            super().__init__({'Error': {'Code': 'TestModeWriteRefused', 'Message': msg},
                              'ResponseMetadata': {'HTTPStatusCode': 403}}, operation_name)
        except Exception:
            Exception.__init__(self, msg)
        self.what = what


def _is_test_bucket(bucket):
    b = str(bucket or '').strip().lower()
    if not b:
        return False
    if b in TEST_BUCKETS or b.startswith('test') or b.endswith('test') or b.endswith('-tests'):
        return True
    extra = {x.strip().lower() for x in str(os.environ.get('PM_TEST_BUCKETS') or '').split(',') if x.strip()}
    return b in extra


def _scratch_ok(key, st):
    k = str(key or '')
    return bool(k) and any(k.startswith(p) for p in st['scratch'])


def why_refused(client, operation_name, api_params):
    """'' when the call may go ahead, else what was refused."""
    try:
        svc = str(client.meta.service_model.service_name)
    except Exception:
        return ''
    op = str(operation_name or '')
    p = api_params if isinstance(api_params, dict) else {}
    if svc in ('ses', 'sesv2'):
        return f"mail ({op})" if op in MAIL_SENDS else ''
    if svc != 's3' or op not in S3_WRITES:
        return ''
    bucket = str(p.get('Bucket') or '')
    if _is_test_bucket(bucket):
        return ''
    try:
        ep = str(getattr(client.meta, 'endpoint_url', '') or '')
    except Exception:
        ep = ''
    if ep and 'amazonaws.com' not in ep:
        return ''                                           # a local stand-in store, not AWS
    st = _state()
    if op == 'DeleteObjects':
        keys = [str((o or {}).get('Key') or '') for o in ((p.get('Delete') or {}).get('Objects') or [])]
        if keys and all(_scratch_ok(k, st) for k in keys):
            return ''
        return f"{op} {bucket}/ ({len(keys)} keys)"
    key = str(p.get('Key') or '')
    if _scratch_ok(key, st):
        return ''
    return f"{op} {bucket}/{key}"


def _note(what, operation_name):
    try:
        st = _state()
        st['refused'].append(what)
        del st['refused'][:-500]
        path = os.environ.get(LOG_ENV)
        if path:
            import sys
            with open(path, 'a') as fh:
                fh.write(json.dumps({'refused': what, 'op': operation_name, 'pid': os.getpid(),
                                     'argv': ' '.join(sys.argv)[:160]}) + '\n')
    except Exception:
        pass


def installed():
    try:
        import botocore.client as bc
        return bool(getattr(bc.BaseClient._make_api_call, _MARK, False))
    except Exception:
        return False


def install(force=False):
    """Put the rule on the client class. A no-op outside a test process
    (unless `force`), and when it is already there. Returns True when the
    rule is in place."""
    in_test = os.environ.get('REGRESSION_TEST_MODE') == '1'
    if not (force or in_test or active()):
        return False
    try:
        import botocore.client as bc
    except Exception:
        return False
    if in_test and started_as_a_test():
        _state()['test_process'] = True
    orig = bc.BaseClient._make_api_call
    if getattr(orig, _MARK, False):
        return True

    def _make_api_call(self, operation_name, api_params):
        if active():
            what = why_refused(self, operation_name, api_params)
            if what:
                _note(what, operation_name)
                raise TestModeWriteRefused(what, operation_name)
        return orig(self, operation_name, api_params)

    setattr(_make_api_call, _MARK, True)
    _make_api_call.__wrapped__ = orig
    bc.BaseClient._make_api_call = _make_api_call
    _state()
    return True


@contextlib.contextmanager
def allow_scratch(prefix):
    """Let this test write under `prefix`, which must sit under
    SCRATCH_ROOT. The only way a test process writes to a real bucket."""
    p = str(prefix or '')
    if not p.startswith(SCRATCH_ROOT) or len(p) <= len(SCRATCH_ROOT):
        raise ValueError(f"a scratch prefix sits under {SCRATCH_ROOT!r}; got {p!r}")
    st = _state()
    added = p not in st['scratch']
    st['scratch'].add(p)
    try:
        yield p
    finally:
        if added:
            st['scratch'].discard(p)


def refused():
    """What this process has been refused, oldest first."""
    try:
        return list(_state()['refused'])
    except Exception:
        return []


def clear_refused():
    try:
        del _state()['refused'][:]
    except Exception:
        pass
