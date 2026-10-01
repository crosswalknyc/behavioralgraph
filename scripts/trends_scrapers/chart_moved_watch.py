#!/usr/bin/env python3
"""Re-level any published chart that moved since it was last priced,
then verify the board. Runs hourly on the build server.

Why this exists (2026-10-01). The charts move at different hours:
Netflix publishes its Top 10 at 09:10 UTC from the daily ingest, the
residential captures land mid-afternoon, the nightly estimator runs at
06:00 UTC. Between a chart moving and the next pricing pass, the
page shows the new chart with the OLD readings against it, or no
reading at all for a title that just arrived: six Netflix entrants
rendered empty cells for most of a day and the chart above them read
out of order. The board gate caught it and emailed; nothing closed it
until the next scheduled pass.

This pass closes it inside the hour. It is `residential_chart_pricing`
in its default scope (only charts whose snapshot is newer than their
last levelling, so an hour in which nothing moved costs nothing and
writes nothing) followed by the board gate when something was
written, so the fix is verified on the rendered page and anything
that survives is reported once.

Usage:
    python3 -m scripts.trends_scrapers.chart_moved_watch
    python3 -m scripts.trends_scrapers.chart_moved_watch --no-gate

Scheduled (installed 2026-10-01) in root's crontab on the build server:
    25 * * * *  cd /root/finished_codes/bg-webapp && set -a &&
                . /root/finished_codes/.env.trends_scrapers && set +a;
                /usr/bin/python3 -m scripts.trends_scrapers.chart_moved_watch
                >> /var/log/chart_moved_watch.log 2>&1
Log rotates daily via /etc/logrotate.d/chart_moved_watch (14 kept).
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def _run(mod: str, args: list[str], timeout_s: int) -> tuple[int, str]:
    cmd = [sys.executable, '-m', mod] + args
    try:
        proc = subprocess.run(cmd, cwd=_repo_root(), capture_output=True,
                              text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        logger.error('%s timed out after %ds', mod, timeout_s)
        return 124, ''
    text = ((proc.stdout or '') + '\n' + (proc.stderr or '')).strip()
    if text:
        logger.info('[%s]\n%s', mod, text[-8000:])
    return proc.returncode, text


_STORE_WRITERS = ('scripts.trends_scrapers.run_all',
                  'scripts.trends_scrapers.stream_estimates',
                  'scripts.trends_scrapers.residential_chart_pricing',
                  'scripts.trends_scrapers.coverage_gate',
                  'scripts.trends_scrapers.board_invariants')


def _other_store_writers() -> str:
    """Names of other processes on this host that write the estimates
    store, or '' when none is running."""
    me = os.getpid()
    try:
        proc = subprocess.run(['ps', '-eo', 'pid=,args='],
                              capture_output=True, text=True, timeout=20)
    except Exception:
        return ''
    found = []
    for line in (proc.stdout or '').splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid_s, args = parts
        try:
            if int(pid_s) == me:
                continue
        except ValueError:
            continue
        if 'chart_moved_watch' in args:
            continue
        for w in _STORE_WRITERS:
            if w in args:
                found.append(w.rsplit('.', 1)[-1])
                break
    return ', '.join(sorted(set(found)))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description='Re-price the charts that moved, then verify the board')
    ap.add_argument('--no-gate', action='store_true',
                    help='re-price only; skip the board verification')
    ap.add_argument('--all', action='store_true',
                    help='every declared chart, not only the moved ones')
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    # Never race the nightly suite or another pricing pass for the
    # store: both read it whole and write it whole, and the later
    # writer would silently drop the earlier one's readings. The
    # nightly re-prices everything anyway; the next hour picks up
    # whatever moved after it.
    busy = _other_store_writers()
    if busy:
        logger.info('another store writer is running (%s); skipping '
                    'this hour', busy)
        return 0

    price_args = ['--all'] if args.all else []
    rc, out = _run('scripts.trends_scrapers.residential_chart_pricing',
                   price_args, timeout_s=60 * 60)
    wrote = 'written             : True' in out
    if rc != 0:
        logger.error('chart re-price exited %d', rc)
    if not wrote:
        logger.info('no chart moved since its last levelling; board '
                    'untouched, gate not run')
        return rc
    if args.no_gate:
        return rc
    # Something was written: verify it on the rendered page. The gate
    # runs the fixer again only if the page still fails, and emails
    # only what survives that.
    grc, _ = _run('scripts.trends_scrapers.board_invariants', ['--gate'],
                  timeout_s=2 * 60 * 60)
    return grc


if __name__ == '__main__':
    sys.exit(main())
