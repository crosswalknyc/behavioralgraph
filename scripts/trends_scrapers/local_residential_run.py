"""
Run the residential-only scrapers from your laptop.

Some streaming services (Disney+, ESPN+ via Disney+, Max via
play.max.com) IP-block the Hetzner scraper datacenter box before any
cookie/auth check runs. From your MacBook on your home ISP, the same
URLs return a full catalog. This script runs those specific scrapers
locally and uploads snapshots to the same S3 location the Hetzner
scrapers write to, so the dashboard picks them up transparently.

Usage (one-shot, from bg-webapp/):
    python3 -m scripts.trends_scrapers.local_residential_run

Install as a launchd job (runs daily at 9am + on wake):
    python3 -m scripts.trends_scrapers.local_residential_run --install-launchd

Uninstall the launchd job:
    python3 -m scripts.trends_scrapers.local_residential_run --uninstall-launchd

Which scrapers run:
    - disneyplus    hits www.disneyplus.com/browse/*
    - espnplus      hits www.disneyplus.com/browse/espn

That's it - everything else (Netflix, Hulu, Prime Video, retailers,
Google Trends, X, YouTube, Instagram) stays on Hetzner where the daily
cron already handles them.

Auth:
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY must be in your shell env
    (or in ~/.aws/credentials). This is the same auth the retail scrapers
    already need on Hetzner.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger('local_residential_run')


# The list of scraper module names to run. Add here when a new residential-
# only scraper is added.
RESIDENTIAL_SCRAPERS = [
    ('disneyplus', 'Disney+'),
    ('espnplus',   'ESPN+'),
]


LAUNCHD_LABEL = 'com.crosswalknyc.trendsresidential'


def _run_scraper(module_name: str, label: str) -> int:
    """Fork a python subprocess to run the named scraper module. Isolated
    per-scraper so a crash in one doesn't take down the batch."""
    logger.info("running scraper: %s (%s)", module_name, label)
    cmd = [sys.executable, '-m', f'scripts.trends_scrapers.{module_name}']
    proc = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=str(Path(__file__).resolve().parents[2]))
    if proc.stdout:
        logger.info("[%s stdout] %s", module_name, proc.stdout.strip())
    if proc.stderr:
        logger.info("[%s stderr] %s", module_name, proc.stderr.strip())
    if proc.returncode != 0:
        logger.warning("scraper %s exited %d", module_name, proc.returncode)
    return proc.returncode


def _run_all() -> int:
    """Run every RESIDENTIAL_SCRAPERS entry in-process order. Returns 0
    if at least one succeeded, 1 if all failed."""
    ok_count = 0
    for module_name, label in RESIDENTIAL_SCRAPERS:
        rc = _run_scraper(module_name, label)
        if rc == 0:
            ok_count += 1
    logger.info("residential run complete: %d/%d scrapers ok",
                 ok_count, len(RESIDENTIAL_SCRAPERS))
    return 0 if ok_count else 1


# ────────────────────────────────────────────────────────────────────
# launchd install / uninstall
# ────────────────────────────────────────────────────────────────────
def _launchd_plist_path() -> Path:
    return Path.home() / 'Library' / 'LaunchAgents' / f'{LAUNCHD_LABEL}.plist'


def _launchd_log_dir() -> Path:
    p = Path.home() / 'Library' / 'Logs' / 'trends_residential'
    p.mkdir(parents=True, exist_ok=True)
    return p


def _launchd_plist_body(repo_root: Path, python_bin: str) -> str:
    log_dir = _launchd_log_dir()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python_bin}</string>
        <string>-m</string>
        <string>scripts.trends_scrapers.local_residential_run</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{repo_root}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
        <key>AWS_REGION</key>
        <string>us-east-2</string>
    </dict>

    <!-- 9am local time daily -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>{log_dir}/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>{log_dir}/stderr.log</string>
</dict>
</plist>
"""


def _install_launchd() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    python_bin = shutil.which('python3') or sys.executable
    plist_path = _launchd_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(_launchd_plist_body(repo_root, python_bin))
    logger.info("wrote plist -> %s", plist_path)

    # Load the plist. bootstrap uses the modern launchctl syntax; fall
    # back to load for older macOS.
    uid = os.getuid()
    domain = f'gui/{uid}'
    for cmd in (['launchctl', 'bootstrap', domain, str(plist_path)],
                 ['launchctl', 'load', '-w', str(plist_path)]):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                logger.info("launchd load ok via `%s`", ' '.join(cmd))
                break
            logger.info("`%s` -> rc=%d %s", ' '.join(cmd), proc.returncode,
                         proc.stderr.strip())
        except FileNotFoundError:
            continue
    else:
        logger.warning("launchctl not available - manually run `launchctl "
                        "load -w %s`", plist_path)
        return 1
    logger.info("Installed. Will run at 9am local daily. Logs: %s",
                 _launchd_log_dir())
    return 0


def _uninstall_launchd() -> int:
    plist_path = _launchd_plist_path()
    if plist_path.exists():
        uid = os.getuid()
        for cmd in (['launchctl', 'bootout', f'gui/{uid}', str(plist_path)],
                     ['launchctl', 'unload', '-w', str(plist_path)]):
            try:
                subprocess.run(cmd, capture_output=True, text=True)
            except FileNotFoundError:
                continue
        plist_path.unlink()
        logger.info("removed %s", plist_path)
    else:
        logger.info("no plist installed at %s", plist_path)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    ap = argparse.ArgumentParser()
    ap.add_argument('--install-launchd',   action='store_true')
    ap.add_argument('--uninstall-launchd', action='store_true')
    args = ap.parse_args()

    if args.install_launchd:
        return _install_launchd()
    if args.uninstall_launchd:
        return _uninstall_launchd()
    return _run_all()


if __name__ == '__main__':
    sys.exit(main())
