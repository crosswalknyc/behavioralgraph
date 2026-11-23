"""
The residential lane installs to its own checkout and starts through
lane_launcher.py (2026-11-23). Offline; no launchctl, no clone.

    PYTHONPATH=. python3 scripts/trends_scrapers/test_lane_install.py
"""

from __future__ import annotations

import plistlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.trends_scrapers import local_residential_run as lrr  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print('ok ', msg)


def test_plist_is_valid_and_runs_the_launcher():
    body = lrr._launchd_plist_body(Path('/Users/x/.crosswalk/trends_lane/bg-webapp'),
                                   '/opt/homebrew/bin/python3')
    d = plistlib.loads(body.encode('utf-8'))   # strict XML: no '--' in comments
    check(d['ProgramArguments'] == [
        '/opt/homebrew/bin/python3',
        '/Users/x/.crosswalk/trends_lane/bg-webapp/scripts/trends_scrapers/lane_launcher.py'],
        'the job starts lane_launcher.py from the lane copy')
    check(d['WorkingDirectory'] == '/Users/x/.crosswalk/trends_lane/bg-webapp',
          'the job works in the lane copy')
    check(d['EnvironmentVariables']['PYTHONPATH'] == d['WorkingDirectory'],
          'PYTHONPATH is the lane copy')
    check(d['StartCalendarInterval'] == {'Hour': 8, 'Minute': 0} and d['RunAtLoad'] is True,
          'still 8 AM daily and at load')


def test_existing_lane_copy_is_reused_and_env_linked():
    real_dir, real_root = lrr.LANE_DIR, lrr.LANE_ROOT
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        try:
            lrr.LANE_DIR = tmp / 'trends_lane'
            lrr.LANE_ROOT = lrr.LANE_DIR / 'bg-webapp'
            (lrr.LANE_ROOT / '.git').mkdir(parents=True)
            source = tmp / 'finished_codes' / 'bg-webapp'
            source.mkdir(parents=True)
            (source.parent / '.env').write_text('X=1\n')
            got = lrr._ensure_lane_copy(source)
            check(got == lrr.LANE_ROOT, 'an existing lane copy is used as it is')
            link = lrr.LANE_DIR / '.env'
            check(link.is_symlink() and link.resolve() == (source.parent / '.env').resolve(),
                  'the repo-root .env is linked beside the lane copy')
            check(lrr._ensure_lane_copy(source) == lrr.LANE_ROOT, 'a second install is a no-op')
        finally:
            lrr.LANE_DIR, lrr.LANE_ROOT = real_dir, real_root


def test_launcher_runs_from_its_own_checkout():
    from scripts.trends_scrapers import lane_launcher
    here = Path(__file__).resolve().parents[2]
    check(lane_launcher.LANE == here, 'the launcher moves the checkout it lives in')
    src = Path(lane_launcher.__file__).read_text(encoding='utf-8')
    check("'reset', '-q', '--hard', 'FETCH_HEAD'" in src and "'fetch', '-q', '--depth', '1', 'origin', 'main'" in src,
          'the launcher moves the copy to origin/main')
    check('import scripts' not in src and 'from scripts' not in src,
          'the launcher needs nothing from the checkout it updates')


if __name__ == '__main__':
    test_plist_is_valid_and_runs_the_launcher()
    test_existing_lane_copy_is_reused_and_env_linked()
    test_launcher_runs_from_its_own_checkout()
    print('ALL OK')
