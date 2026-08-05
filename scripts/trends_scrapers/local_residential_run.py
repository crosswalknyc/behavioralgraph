"""
Run the residential-only scrapers from your laptop.

Some streaming services (Disney+, ESPN+ via Disney+, Max via
play.hbomax.com) IP-block the Hetzner scraper datacenter box before any
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
#
# Netflix (2026-07) is here because we switched from the public weekly TSV
# (only updated Tuesdays) to an authenticated daily scrape of the
# logged-in netflix.com homepage's "Top 10 in the U.S. Today" rows.
# The auth path uses the operator's donated netflix.com cookies, which
# live on their laptop, so this scraper naturally belongs here rather
# than on Hetzner.
RESIDENTIAL_SCRAPERS = [
    ('disneyplus',    'Disney+'),
    ('espnplus',      'ESPN+'),
    ('max_streaming', 'Max'),
    ('netflix',       'Netflix'),
    # Hulu (2026-07): moved here after the Hetzner cron kept returning
    # 0 items even with donated cookies - Hulu's WAF fingerprints the
    # datacenter IP even before the cookie check runs. From the Mac's
    # residential IP with the same cookies, Playwright gets a full
    # 60-title homepage back on first try.
    ('hulu',          'Hulu'),
    # Social content scrapers (2026-07): switched from hashtag lists to
    # actual trending posts/videos/tweets. All three need a real logged-in
    # session, so they run from the laptop (where cookies live) rather
    # than Hetzner.
    ('tiktok',        'TikTok'),
    ('instagram',     'Instagram'),
    ('x_twitter',     'X'),
    # Reddit (2026-08-05): moved here so the Playwright shreddit-post
    # path runs from the Mac's residential IP. Hetzner IPs are on
    # Reddit's WAF blocklist for BOTH the JSON endpoints and Playwright
    # renders of /r/<sub>/hot/, which reduces the Hetzner cron to
    # RSS-only fallback rows - those don't carry `score` or
    # `comment-count` attrs, so the dashboard renders posts without
    # upvote/comment chips. Running from the laptop restores the full
    # engagement stats on every row.
    ('reddit',        'Reddit'),
    # Film ticketing (2026-07-28): all 5 platforms IP-block the Hetzner
    # datacenter. Fandango serves a "Message To Our Fans" placebo page,
    # Cinemark 403s at the edge, AMC/Regal serve Akamai captcha shells,
    # and Atom Tickets refuses to hydrate. From the Mac's residential IP,
    # Fandango + Cinemark return 30 titles each via plain HTTP. AMC/Regal
    # remain captcha-walled even from residential + Playwright + stealth;
    # donated amctheatres.com / regmovies.com cookies from a signed-in
    # Chrome session may unblock them (a passed-captcha session is
    # trusted for a while) but that path is opt-in via donate_cookies.py.
    ('film_ticketing', 'Film Ticketing'),
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


def _refresh_cookies() -> int:
    """Auto-donate cookies for every DEFAULT_DOMAINS entry before running
    the scrapers. Runs the same `donate_cookies.py` script the operator
    would run manually, so residential + Hetzner + all cookie-degraded
    scrapers get fresh sessions on every daily run. No-op if the
    subprocess exits non-zero (missing keychain access, no Chrome
    profile, etc.) - the scraper batch still proceeds with whatever
    cookies are currently in S3.
    """
    logger.info("refreshing donated cookies from local Chrome ...")
    cmd = [sys.executable, '-m',
           'scripts.trends_scrapers.donate_cookies']
    proc = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=str(Path(__file__).resolve().parents[2]))
    if proc.stdout:
        logger.info("[donate_cookies stdout] %s", proc.stdout.strip())
    if proc.stderr:
        logger.info("[donate_cookies stderr] %s", proc.stderr.strip())
    if proc.returncode != 0:
        logger.warning("donate_cookies exited %d; continuing with existing "
                        "cookies in S3", proc.returncode)
    return proc.returncode


def _run_all() -> int:
    """Refresh donated cookies from local Chrome, then run every
    RESIDENTIAL_SCRAPERS entry. Returns 0 if at least one scraper
    succeeded, 1 if all failed."""
    _refresh_cookies()
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
        <!-- Critical: without PYTHONPATH, `python3 -m scripts...` fails
             with ModuleNotFoundError even when WorkingDirectory is set.
             launchd does not add the WorkingDirectory to sys.path. -->
        <key>PYTHONPATH</key>
        <string>{repo_root}</string>
    </dict>

    <!-- 8am local time daily. Refreshes cookies + runs residential
         scrapers before the dashboard's morning traffic peak. -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <!-- If Mac was asleep at 8am, fire when it wakes. Also fires at
         agent-load time so `--install-launchd` runs the batch once
         immediately for smoke testing. -->
    <key>RunAtLoad</key>
    <true/>

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
    logger.info("Installed. Will run at 8am local daily (and at load). "
                 "Logs: %s", _launchd_log_dir())
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
