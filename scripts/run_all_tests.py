#!/usr/bin/env python3
"""Run every scripts/test_*.py hermetically, each under a timeout, and
print one table: PASS / FAIL / HANG per suite.

    scripts/run_all_tests.sh                 # every suite, 240s each
    scripts/run_all_tests.sh --only pm_       # suites whose name has pm_
    scripts/run_all_tests.sh --jobs 6 --timeout 300
    scripts/run_all_tests.sh --root /path/to/finished_codes   # the parent repo

Hermetic by construction: REGRESSION_TEST_MODE=1 and
HOSTMAP_GAP_DOMAIN_RESEARCH=0 are set, no .env is loaded, the AWS
credential files are pointed at /dev/null and the model keys are
unset, so a suite that reaches for S3 or the model fails fast instead
of touching production. Each suite runs in its own session so a HANG is
killed with every child it spawned. Per-suite output lands under
--log-dir (default /tmp/run_all_tests/<root-name>/<suite>.log).

Exit code 0 only when every suite passes. Run it from a directory whose
basename is the repo's (bg-webapp, finished_codes): the test path shims
walk up for bg-webapp/app.py and hang from a bare worktree.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import glob
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(HERE)

STRIP_ENV = (
    'ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'AWS_ACCESS_KEY_ID',
    'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_PROFILE',
    'SYNTH_QUEUE_SECRET', 'CLICKHOUSE_PASSWORD', 'BRAVE_SEARCH_API_KEY',
)


def hermetic_env() -> dict:
    env = dict(os.environ)
    for k in STRIP_ENV:
        env.pop(k, None)
    env.update({
        'REGRESSION_TEST_MODE': '1',
        'HOSTMAP_GAP_DOMAIN_RESEARCH': '0',
        'AWS_SHARED_CREDENTIALS_FILE': '/dev/null',
        'AWS_CONFIG_FILE': '/dev/null',
        'AWS_EC2_METADATA_DISABLED': 'true',
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUNBUFFERED': '1',
    })
    return env


def run_one(path: str, root: str, timeout: int, python: str,
            log_dir: str) -> tuple[str, str, float, str]:
    name = os.path.basename(path)
    log_path = os.path.join(log_dir, name[:-3] + '.log')
    t0 = time.time()
    with open(log_path, 'wb') as log:
        proc = subprocess.Popen(
            [python, path], cwd=root, env=hermetic_env(),
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            rc = proc.wait(timeout=timeout)
            status = 'PASS' if rc == 0 else 'FAIL'
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
            status = 'HANG'
    return name, status, time.time() - t0, log_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--root', default=DEFAULT_ROOT,
                    help='repo root holding scripts/test_*.py')
    ap.add_argument('--timeout', type=int, default=240,
                    help='seconds per suite before it counts as HANG')
    ap.add_argument('--jobs', type=int, default=4,
                    help='suites run at once')
    ap.add_argument('--only', default='',
                    help='substring filter on the suite file name')
    ap.add_argument('--python', default=sys.executable)
    ap.add_argument('--log-dir', default='')
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    paths = sorted(glob.glob(os.path.join(root, 'scripts', 'test_*.py')))
    if args.only:
        paths = [p for p in paths if args.only in os.path.basename(p)]
    if not paths:
        print(f'no suites under {root}/scripts matching {args.only!r}')
        return 2
    log_dir = args.log_dir or os.path.join(
        '/tmp', 'run_all_tests', os.path.basename(root))
    os.makedirs(log_dir, exist_ok=True)

    print(f'{len(paths)} suites under {root} '
          f'(timeout {args.timeout}s, {args.jobs} at a time, '
          f'logs in {log_dir})', flush=True)
    results = []
    t_start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(run_one, p, root, args.timeout, args.python,
                          log_dir): p for p in paths}
        for fut in concurrent.futures.as_completed(futs):
            name, status, secs, log_path = fut.result()
            results.append((name, status, secs, log_path))
            print(f'  {status:4s} {secs:7.1f}s  {name}', flush=True)

    results.sort()
    width = max(len(r[0]) for r in results)
    print()
    print(f'{"suite":{width}s}  status  seconds')
    print(f'{"-" * width}  ------  -------')
    for name, status, secs, _ in results:
        print(f'{name:{width}s}  {status:6s}  {secs:7.1f}')
    counts = {s: sum(1 for r in results if r[1] == s)
              for s in ('PASS', 'FAIL', 'HANG')}
    print()
    print(f'{counts["PASS"]} pass, {counts["FAIL"]} fail, '
          f'{counts["HANG"]} hang out of {len(results)} '
          f'in {time.time() - t_start:.0f}s')
    bad = [r for r in results if r[1] != 'PASS']
    if bad:
        print()
        print('not passing:')
        for name, status, _, log_path in bad:
            print(f'  {status} {name}  ({log_path})')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
