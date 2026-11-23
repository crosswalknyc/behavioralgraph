#!/usr/bin/env python3
"""
Start the residential lane from its own copy of main.

The 8 AM launchd job (com.crosswalknyc.trendsresidential, installed by
`local_residential_run --install-launchd`) runs this file from a
private checkout at ~/.crosswalk/trends_lane/bg-webapp. Before every
run the checkout is moved to origin/main, then the lane starts, so the
laptop always runs what is published.

Why (2026-11-23, Jenna approved): the job used to run straight from
~/Desktop/finished_codes/bg-webapp, a shared working copy that other
sessions edit and that had fallen 1,075 commits behind main. Nothing
shipped for the residential sources since then had run; Hallmark+ sat
on one read for five days. The private copy is never edited by hand,
so moving it is always safe. If the update fails (no network, GitHub
down) the run goes ahead on the copy already on disk and the log says
so.

The repo-root .env the lane reads sits beside the checkout as a link
to ~/Desktop/finished_codes/.env, so a key change there reaches the
lane too. This file only uses the standard library: it has to run
before the checkout it lives in is updated.
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LANE = Path(__file__).resolve().parents[2]


def _git() -> str:
    for cand in ('/opt/homebrew/bin/git', '/usr/local/bin/git', '/usr/bin/git'):
        if os.path.exists(cand):
            return cand
    return shutil.which('git') or 'git'


def log(msg: str) -> None:
    print(f"{datetime.now().isoformat(timespec='seconds')} lane: {msg}", flush=True)


def update() -> None:
    git = _git()
    try:
        subprocess.run([git, '-C', str(LANE), 'fetch', '-q', '--depth', '1', 'origin', 'main'],
                       check=True, timeout=300)
        subprocess.run([git, '-C', str(LANE), 'reset', '-q', '--hard', 'FETCH_HEAD'],
                       check=True, timeout=120)
        head = subprocess.run([git, '-C', str(LANE), 'log', '--oneline', '-1'],
                              capture_output=True, text=True, timeout=30).stdout.strip()
        log(f"running main {head[:90]}")
    except Exception as e:
        log(f"could not update ({e}); running the copy already here")


def main() -> None:
    if '--no-update' in sys.argv:
        sys.argv.remove('--no-update')
    else:
        update()
    os.chdir(LANE)
    env = dict(os.environ, PYTHONPATH=str(LANE))
    os.execve(sys.executable,
              [sys.executable, '-m', 'scripts.trends_scrapers.local_residential_run']
              + sys.argv[1:], env)


if __name__ == '__main__':
    main()
