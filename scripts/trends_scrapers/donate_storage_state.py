"""Full-session donation: cookies plus localStorage plus IndexedDB.

Why cookies alone were never going to work
------------------------------------------
`donate_cookies.py` copies Chrome's cookie jar. That is the right
answer for a retailer or a social site, where the session really does
live in a cookie, and those donations work today.

It is structurally insufficient for a modern streaming SPA, which
keeps its access and refresh tokens in IndexedDB. The donated
`hbomax.com` jar is 52 cookies and every one of them is analytics or
ad tracking. There is a cookie literally named `session` in there and
it does not authenticate anything. Rendering `play.hbomax.com` with
that jar returns the marketing site: "Choose an HBO Max plan or bundle
to start streaming", the Basic With Ads / Standard / Premium cards, no
Top 10, no Trending Now, no Continue Watching.

So this module donates the whole session instead:
`BrowserContext.storage_state(indexed_db=True)`, which carries cookies,
localStorage and IndexedDB together. A scraper restores it with
`browser.new_context(storage_state=...)` and is genuinely signed in.

Where the session comes from
----------------------------
A dedicated, persistent Chrome profile that lives outside the repo:

    ~/Library/Application Support/CrosswalkTrendsLogin/chrome-profile

A separate `--user-data-dir` is the same shape as
`start_chrome_for_cookie_export.command`, which has always launched
Chrome on its own data dir because Chrome will not expose remote
debugging on the default one. Here the reason is adjacent: the Default
profile is locked whenever Jenna has Chrome open, and at 5.1 GB it is
not something to copy. A dedicated profile sidesteps both, and unlike
that script's `/tmp` directory this one persists, so signing in is a
once-per-expiry event rather than a once-per-run event.

Passwords are never involved. This captures a session that a human
already established in a browser window. If a site needs a credential,
that is a human at a keyboard, not a secret in a config file.

Usage
-----
    # Sign in once. Opens a real Chrome window, one tab per platform.
    python3 scripts/trends_scrapers/donate_cookies.py --login

    # Same, for one platform
    python3 scripts/trends_scrapers/donate_cookies.py --login hbomax.com

    # Automated re-donation from the profile (what the daily job runs)
    python3 scripts/trends_scrapers/donate_cookies.py --storage-state

    # Is every platform still signed in?
    python3 scripts/trends_scrapers/donate_cookies.py --verify

Security
--------
A storage state is more sensitive than a cookie jar because it can
carry access and refresh tokens. It lands in the same private bucket
as the cookie donations, under its own prefix, with AES256 at rest.
No value from it is ever logged, printed or written into the repo:
every diagnostic goes through `_auth_guard.redact_storage_state`,
which reports counts only. Capture is filtered per platform, so a
donation for one service never carries another service's tokens.

Rotate by signing out of the site in the profile window, or delete the
S3 object and re-run `--login`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import _auth_guard as guard
from ._playwright import UA, _lazy_playwright, _try_stealth

logger = logging.getLogger('donate_storage_state')

S3_BUCKET = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_PREFIX = 'trends_iq_storage_state/'

# Shared with trends_auto_login so there is exactly one signed-in
# profile on the machine rather than a second one that drifts.
PROFILE_DIR = (Path.home() / 'Library' / 'Application Support'
               / 'CrosswalkTrendsLogin' / 'chrome-profile')

# Platforms whose scrapers are session-gated. Order is the order the
# tabs open in, so the highest-friction sign-in is in front.
DEFAULT_STORAGE_DOMAINS = [
    'hbomax.com',
    'disneyplus.com',
    'hulu.com',
    'amazon.com',        # Prime Video
    'netflix.com',
    'peacocktv.com',
    'music.amazon.com',
]

# How long `--login` waits for a human to finish signing in.
_LOGIN_WAIT_S = int(os.environ.get('TRENDS_LOGIN_WAIT_S', '900'))
_POLL_S = 6


# ────────────────────────────────────────────────────────────────────
# S3
# ────────────────────────────────────────────────────────────────────
def _s3():
    import boto3
    return boto3.client(
        's3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')


def upload_storage_state(domain: str, state: dict, *,
                         verdict: str, detail: str,
                         dry_run: bool = False) -> str:
    """Write one platform's session to S3 (or /tmp on a dry run).

    The payload records how the session was judged at capture time so
    a later reader can tell a proven session from a hopeful one
    without re-rendering the page.
    """
    payload = {
        'domain': domain,
        'storage_state': state,
        'donated_at': datetime.now(timezone.utc).isoformat(),
        'donor_host': os.uname().nodename,
        'verdict': verdict,
        'verdict_detail': detail,
        'summary': guard.redact_storage_state(state),
    }
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

    if dry_run:
        out = Path(f'/tmp/donated_storage_state_{domain}.json')
        out.write_bytes(body)
        try:
            out.chmod(0o600)
        except Exception:
            pass
        return f'file://{out}'

    key = f'{S3_PREFIX}{domain}.json'
    _s3().put_object(
        Bucket=S3_BUCKET, Key=key, Body=body,
        ContentType='application/json',
        ServerSideEncryption='AES256',
        CacheControl='no-cache, max-age=0',
    )
    return f's3://{S3_BUCKET}/{key}'


# ────────────────────────────────────────────────────────────────────
# Narrowing a whole-browser state down to one platform
# ────────────────────────────────────────────────────────────────────
def _host_of(origin: str) -> str:
    o = (origin or '').split('://', 1)[-1]
    return o.split('/', 1)[0].split(':', 1)[0].lower()


def _host_matches(host: str, target: str) -> bool:
    h = (host or '').lstrip('.').lower()
    t = (target or '').lstrip('.').lower()
    if not h or not t:
        return False
    return h == t or h.endswith('.' + t)


def filter_state_for_domain(state: dict, domain: str) -> dict:
    """Keep only the cookies and origins that belong to `domain`.

    The persistent profile holds every platform at once. Uploading the
    whole thing under each domain would hand every scraper every other
    platform's tokens, so each donation is narrowed to the hosts that
    service actually uses.
    """
    from .donate_cookies import _cookie_applies_to_target

    hosts = guard.session_hosts(domain)

    cookies = [c for c in (state.get('cookies') or [])
               if any(_cookie_applies_to_target(c.get('domain') or '', h)
                      for h in hosts)]
    origins = [o for o in (state.get('origins') or [])
               if any(_host_matches(_host_of(o.get('origin') or ''), h)
                      for h in hosts)]
    return {'cookies': cookies, 'origins': origins}


# ────────────────────────────────────────────────────────────────────
# Browser
# ────────────────────────────────────────────────────────────────────
def _launch_profile(pw, *, headed: bool):
    """Open the dedicated scraping profile.

    Same separate-`--user-data-dir` shape as
    `start_chrome_for_cookie_export.command`, persistent rather than
    in /tmp so the session survives a reboot.
    """
    args = ['--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage', '--no-first-run',
            '--no-default-browser-check']
    if not headed:
        args.append('--headless=new')
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return pw.chromium.launch_persistent_context(
        str(PROFILE_DIR), channel='chrome', headless=not headed,
        args=args, user_agent=UA,
        viewport={'width': 1440, 'height': 900},
        locale='en-US', timezone_id='America/New_York',
    )


def _page_html(page) -> str:
    try:
        return page.content() or ''
    except Exception:
        return ''


def _page_url(page) -> str:
    try:
        return page.url or ''
    except Exception:
        return ''


def _judge(page, domain: str) -> tuple[str, str]:
    return guard.classify_auth(domain, _page_html(page), _page_url(page))


# The chooser dismissal and the polling settle live in `_auth_guard`,
# because `render_pages` needs exactly the same two behaviours before
# it will trust a page. Aliased here so this module reads the way it
# did when it owned them.
dismiss_profile_chooser = guard.dismiss_profile_chooser
_settle = guard.settle_and_judge


# ────────────────────────────────────────────────────────────────────
# Seeding the scraping profile from everyday Chrome
# ────────────────────────────────────────────────────────────────────
# The sessions already exist in Jenna's Chrome. What was missing was
# never the login, it was the half of the session that cookies cannot
# carry, and that half is sitting in two directories on disk.
#
# So rather than making her sign in again, copy the session stores
# OUT of the Default profile (read only, never written to) into the
# dedicated scraping profile. Measured 2026-09-23: 73 MB and under a
# second, against 5.1 GB for the whole profile.
#
# The cookie half deliberately does NOT come this way. Chrome's cookie
# DB is encrypted against a Keychain item, and a copy opened by a
# headless Chrome under a different data dir cannot prompt for
# Keychain access, so it silently reads back empty. That is measurable:
# seeding alone left Peacock and Prime Video logged out, and both came
# straight back once their cookies were injected from Python, which
# decrypts through the Keychain without a prompt. Cookies therefore
# come from the existing donation path, which already works, and this
# supplies only the IndexedDB and localStorage that path cannot see.
_CHROME_DIR = (Path.home() / 'Library' / 'Application Support'
               / 'Google' / 'Chrome')

# Written once a human has signed into the scraping profile itself.
#
# localStorage is a single LevelDB shared by every origin, so seeding
# it is all-or-nothing. Before this marker exists the scraping profile
# has no session worth keeping and Chrome's copy is strictly better.
# After it exists, the profile holds a sign-in that Chrome does not
# have, and overwriting the store would throw that away. The absence
# of this marker is also why an empty directory must not count as
# "already seeded": the scraping profile ships with a 296 KB empty
# localStorage from its own first launch, and treating that as
# existing state is what left Disney+ signed out on the first run.
_OWN_SESSION_MARKER = PROFILE_DIR / '.has_own_sessions'


def mark_own_session() -> None:
    """Record that this profile now holds a sign-in of its own."""
    try:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        _OWN_SESSION_MARKER.write_text(
            datetime.now(timezone.utc).isoformat() + '\n')
    except Exception as e:
        logger.info('could not mark the profile: %s', e)


def _origin_dir_names(hosts: list[str]) -> list[str]:
    return [f'https_{h}_0.indexeddb' for h in hosts]


def seed_profile_from_chrome(domains: list[str], *,
                             profile: str = 'Default',
                             force: bool = False) -> dict[str, str]:
    """Copy IndexedDB and localStorage for `domains` out of Chrome.

    Returns `{domain: note}`. Never raises: a platform that cannot be
    seeded just needs the one-time sign-in instead.

    Existing origins in the scraping profile are kept unless `force`,
    because a session established by signing into the scraping profile
    is the newer one and must not be overwritten by whatever Chrome
    last had.
    """
    import glob
    import shutil

    notes: dict[str, str] = {}
    src_root = _CHROME_DIR / profile
    if not src_root.exists():
        for d in domains:
            notes[d] = 'no Chrome profile to seed from'
        return notes

    dst_root = PROFILE_DIR / 'Default'
    dst_root.mkdir(parents=True, exist_ok=True)

    # Shared localStorage DB. One LevelDB for every origin, so it is
    # all-or-nothing and only copied when the scraping profile has
    # none of its own.
    src_ls = src_root / 'Local Storage'
    dst_ls = dst_root / 'Local Storage'
    if src_ls.exists() and (force or not _OWN_SESSION_MARKER.exists()):
        try:
            if dst_ls.exists():
                shutil.rmtree(dst_ls)
            shutil.copytree(src_ls, dst_ls)
        except Exception as e:
            logger.info('could not seed localStorage: %s', e)

    dst_idb = dst_root / 'IndexedDB'
    dst_idb.mkdir(exist_ok=True)
    for domain in domains:
        copied, skipped = 0, 0
        for stem in _origin_dir_names(guard.session_hosts(domain)):
            for path in glob.glob(str(src_root / 'IndexedDB' / f'{stem}.*')):
                src = Path(path)
                dst = dst_idb / src.name
                if dst.exists() and not force:
                    skipped += 1
                    continue
                try:
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                    copied += 1
                except Exception as e:
                    logger.info('could not seed %s: %s', src.name, e)
        if copied:
            notes[domain] = f'seeded {copied} store(s) from Chrome'
        elif skipped:
            notes[domain] = 'already present in the scraping profile'
        else:
            notes[domain] = 'nothing in Chrome to seed'

    # A LOCK copied out of a running Chrome stops the new one opening
    # the store at all.
    for lock in PROFILE_DIR.rglob('LOCK'):
        try:
            lock.unlink()
        except Exception:
            pass
    return notes


def chrome_cookies_for(domains: list[str]) -> list[dict]:
    """Read cookies for `domains` out of Chrome, in Playwright shape.

    Reuses the reader the cookie donation already relies on, so there
    is one implementation of Chrome's cookie-scope rules rather than
    two that can disagree.
    """
    from .donate_cookies import _read_chrome_cookies

    out: list[dict] = []
    seen: set[tuple] = set()
    for domain in domains:
        for c in _read_chrome_cookies(domain):
            key = (c.get('name'), c.get('domain'), c.get('path'))
            if key in seen or not (c.get('name') and c.get('value')):
                continue
            seen.add(key)
            entry = {'name': c['name'], 'value': c['value'],
                     'domain': c['domain'], 'path': c.get('path') or '/',
                     'sameSite': c.get('sameSite') or 'Lax'}
            if c.get('expires'):
                entry['expires'] = int(c['expires'])
            if c.get('secure'):
                entry['secure'] = True
            if c.get('httpOnly'):
                entry['httpOnly'] = True
            out.append(entry)
    return out


# ────────────────────────────────────────────────────────────────────
# --login : one human sign-in, then automated forever after
# ────────────────────────────────────────────────────────────────────
def run_login(domains: list[str], *, dry_run: bool = False,
              seed: bool = True) -> int:
    """Get every platform to a signed-in state, then donate it.

    Most of the work is not the sign-in. The sessions already exist in
    everyday Chrome, so this seeds them across first and only asks for
    a human on the platforms that are genuinely signed out. In the
    common case Jenna signs into one tab, not seven.
    """
    sp = _lazy_playwright()
    if sp is None:
        print('Playwright is not installed. Install with:\n'
              '  pip3 install --break-system-packages playwright')
        return 3

    known = [d for d in domains if guard.site_spec(d)]
    for d in (d for d in domains if not guard.site_spec(d)):
        print(f'  {d:<20s} no sign-in page registered, skipping')
    if not known:
        print('Nothing to sign into.')
        return 1

    print()
    print('=' * 70)
    print('  Crosswalk Trends IQ  |  sign-in')
    print('=' * 70)
    print()
    print('Carrying your existing Chrome sessions across first ...')
    if seed:
        for domain, note in seed_profile_from_chrome(known).items():
            print(f'  {guard.site_label(domain):<16s} {note}')
    print()
    print('A Chrome window is opening with one tab per platform. It is a')
    print('separate profile reserved for this, so your everyday Chrome can')
    print('stay open and nothing here touches it.')
    print()
    print('Anything that says NEEDS SIGN-IN below needs you to sign into')
    print('that tab. Nothing is typed for you and no password is stored.')
    print('If a tab shows a "who is watching" screen, click your profile so')
    print('the page lands on the real home rails.')
    print()

    results: dict[str, tuple[str, str]] = {}
    with sp() as pw:
        try:
            ctx = _launch_profile(pw, headed=True)
        except Exception as e:
            print(f'Could not open the Chrome profile: {e}')
            return 3

        cookies = chrome_cookies_for(known)
        if cookies:
            try:
                ctx.add_cookies(cookies)
                print(f'  carried {len(cookies)} cookies across from Chrome')
            except Exception as e:
                print(f'  could not carry cookies across: {e}')
        print()

        pages: dict[str, Any] = {}
        for domain in known:
            try:
                page = ctx.new_page()
                _try_stealth(page)
                page.goto(guard.app_url(domain),
                          wait_until='domcontentloaded', timeout=60000)
                pages[domain] = page
            except Exception as e:
                print(f'  {guard.site_label(domain):<16s} could not open: '
                      f'{type(e).__name__}')

        # First pass tells us who actually needs a human.
        done: set[str] = set()
        for domain, page in pages.items():
            verdict, detail = _settle(page, domain)
            results[domain] = (verdict, detail)
            if verdict == 'signed_in':
                done.add(domain)
                print(f'  [already signed in]  {guard.site_label(domain)}')
            else:
                print(f'  [NEEDS SIGN-IN]      {guard.site_label(domain)}')

        waiting = [d for d in pages if d not in done]
        if waiting:
            print()
            print(f'Sign into these {len(waiting)} tab(s): '
                  f'{", ".join(guard.site_label(d) for d in waiting)}')
            print(f'Waiting up to {_LOGIN_WAIT_S // 60} minutes. Press Ctrl-C '
                  f'when you are done and whatever is signed in gets saved.')
            print()
            deadline = time.time() + _LOGIN_WAIT_S
            try:
                while time.time() < deadline and len(done) < len(pages):
                    time.sleep(_POLL_S)
                    for domain in list(waiting):
                        if domain in done:
                            continue
                        verdict, detail = _settle(pages[domain], domain,
                                                  settle_ms=1500)
                        results[domain] = (verdict, detail)
                        if verdict == 'signed_in':
                            done.add(domain)
                            print(f'  [signed in]  '
                                  f'{guard.site_label(domain)}          ')
                    left = int(deadline - time.time())
                    still = [guard.site_label(d) for d in waiting
                             if d not in done]
                    if still:
                        print(f'  waiting on: {", ".join(still)} '
                              f'({left // 60}m {left % 60}s left)',
                              end='\r', flush=True)
            except KeyboardInterrupt:
                print('\n  stopping here; saving whatever is signed in')
            if any(d in done for d in waiting):
                # A human signed into this profile, so it now holds a
                # session everyday Chrome does not have. Later runs
                # must stop overwriting its localStorage from Chrome.
                mark_own_session()

        print()
        rc = _capture_and_upload(ctx, list(pages), dry_run=dry_run,
                                 results=results)
        try:
            ctx.close()
        except Exception:
            pass
    return rc


# ────────────────────────────────────────────────────────────────────
# --storage-state : headless re-donation from the profile
# ────────────────────────────────────────────────────────────────────
def run_recapture(domains: list[str], *, dry_run: bool = False,
                  seed: bool = True) -> int:
    """Re-donate with no interaction. What the daily run calls.

    Only uploads a platform that still proves signed in. A logged-out
    capture would otherwise overwrite a good donation with a dead one,
    which is the quiet way a rail goes dark.
    """
    sp = _lazy_playwright()
    if sp is None:
        print('Playwright is not installed.')
        return 3

    known = [d for d in domains if guard.site_spec(d)]
    if not known:
        print('No session-gated platforms in that list.')
        return 1

    if seed and sys.platform == 'darwin':
        for domain, note in seed_profile_from_chrome(known).items():
            logger.info('%s: %s', domain, note)

    results: dict[str, tuple[str, str]] = {}
    with sp() as pw:
        try:
            ctx = _launch_profile(pw, headed=False)
        except Exception as e:
            print(f'Could not open the Chrome profile: {e}')
            return 3

        if sys.platform == 'darwin':
            try:
                ctx.add_cookies(chrome_cookies_for(known))
            except Exception as e:
                logger.info('could not carry cookies across: %s', e)

        opened: list[str] = []
        for domain in known:
            try:
                page = ctx.new_page()
                _try_stealth(page)
                page.goto(guard.app_url(domain),
                          wait_until='domcontentloaded', timeout=60000)
                results[domain] = _settle(page, domain)
                opened.append(domain)
            except Exception as e:
                results[domain] = ('unknown', f'{type(e).__name__}: {e}')
                opened.append(domain)
        rc = _capture_and_upload(ctx, opened, dry_run=dry_run,
                                 results=results)
        try:
            ctx.close()
        except Exception:
            pass
    return rc


def _capture_and_upload(ctx, domains: list[str], *, dry_run: bool,
                        results: dict[str, tuple[str, str]]) -> int:
    """Capture once, narrow per platform, upload the proven ones."""
    try:
        full = ctx.storage_state(indexed_db=True)
    except TypeError:
        # Older Playwright without the indexed_db argument would give
        # us cookies only, which is the exact gap this module exists
        # to close. Refuse rather than donate a state that cannot
        # carry a streaming session.
        print('This Playwright is too old to capture IndexedDB. '
              'Upgrade with: pip3 install --break-system-packages -U playwright')
        return 3
    except Exception as e:
        print(f'Could not read the session from the profile: {e}')
        return 3

    print()
    print(f'{"platform":<18s} {"status":<14s} session')
    print('-' * 68)
    donated, held = 0, 0
    for domain in domains:
        verdict, detail = results.get(domain, ('unknown', 'not checked'))
        state = filter_state_for_domain(full, domain)
        summary = guard.describe_storage_state(state)
        label = guard.site_label(domain)

        if verdict != 'signed_in':
            held += 1
            print(f'{label:<18s} {"NOT SIGNED IN":<14s} {detail[:90]}')
            continue
        try:
            uri = upload_storage_state(domain, state, verdict=verdict,
                                       detail=detail, dry_run=dry_run)
        except Exception as e:
            held += 1
            print(f'{label:<18s} {"UPLOAD FAILED":<14s} {e}')
            continue
        donated += 1
        print(f'{label:<18s} {"signed in":<14s} {summary}')
        logger.info('donated %s session to %s', domain, uri)

    print()
    print(f'{donated} platform(s) donated, {held} still need a sign-in.')
    if held:
        print('Re-run and sign into the remaining tabs with:')
        print('  python3 scripts/trends_scrapers/donate_cookies.py --login')
    return 0 if donated else 1


# ────────────────────────────────────────────────────────────────────
# --verify : does the DONATED session still render a signed-in app?
# ────────────────────────────────────────────────────────────────────
def run_verify(domains: list[str], *, use_proxy: bool = False) -> int:
    """Render each platform from the S3 donation and report identity.

    This deliberately does not read the local profile. The question it
    answers is the one that matters to a rail: when a scraper picks up
    the donated session, does the platform hand back its app or its
    marketing page?
    """
    from ._base import load_donated_storage_state, storage_state_status

    sp = _lazy_playwright()
    if sp is None:
        print('Playwright is not installed.')
        return 3

    known = [d for d in domains if guard.site_spec(d)]
    proxy_dict = None
    if use_proxy:
        from ._proxy import get_proxy_config, playwright_proxy
        proxy_dict = playwright_proxy(get_proxy_config())

    print()
    print(f'{"platform":<18s} {"donation":<10s} {"status":<14s} evidence')
    print('-' * 100)

    bad = 0
    with sp() as pw:
        launch: dict[str, Any] = {
            'headless': True,
            'args': ['--headless=new', '--no-sandbox',
                     '--disable-blink-features=AutomationControlled'],
        }
        if proxy_dict:
            launch['proxy'] = proxy_dict
        try:
            browser = pw.chromium.launch(channel='chrome', **launch)
        except Exception:
            browser = pw.chromium.launch(**launch)

        for domain in known:
            label = guard.site_label(domain)
            status = storage_state_status(domain)
            state = load_donated_storage_state(domain)
            if not state:
                bad += 1
                age = 'none' if not status.get('donated') else 'stale'
                print(f'{label:<18s} {age:<10s} {"NO SESSION":<14s} '
                      f'run --login for {domain}')
                continue
            age_h = status.get('age_hours')
            age = f'{age_h:.0f}h' if age_h is not None else '?'

            ctx = None
            try:
                ctx = browser.new_context(
                    storage_state=state, user_agent=UA,
                    viewport={'width': 1440, 'height': 900},
                    locale='en-US', timezone_id='America/New_York',
                    extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
                )
                page = ctx.new_page()
                _try_stealth(page)
                page.goto(guard.app_url(domain),
                          wait_until='domcontentloaded', timeout=60000)
                # Same settle the capture side uses. Verify has to
                # wait for hydration exactly as long, or it reports
                # "unknown" for platforms that are signed in and sends
                # an operator to fix a session that works.
                verdict, detail = _settle(page, domain)
                geo, geo_detail = guard.classify_geo(_page_html(page))
                if verdict == 'signed_in' and geo != 'us':
                    verdict, detail = 'wrong region', geo_detail
            except Exception as e:
                verdict, detail = 'error', f'{type(e).__name__}: {e}'
            finally:
                if ctx is not None:
                    try:
                        ctx.close()
                    except Exception:
                        pass

            if verdict != 'signed_in':
                bad += 1
            shown = 'signed in' if verdict == 'signed_in' else verdict.upper()
            print(f'{label:<18s} {age:<10s} {shown:<14s} {detail[:70]}')

        try:
            browser.close()
        except Exception:
            pass

    print()
    if bad:
        print(f'{bad} platform(s) are not signed in. Re-authorize with:')
        print('  python3 scripts/trends_scrapers/donate_cookies.py --login')
    else:
        print('Every platform is signed in.')
    return 1 if bad else 0


# ────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────
def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    ap = argparse.ArgumentParser(
        description='Donate a full signed-in session (cookies, '
                    'localStorage, IndexedDB) to the Trends IQ scrapers.')
    ap.add_argument('domains', nargs='*',
                    help='Platforms to handle (default: all session-gated).')
    ap.add_argument('--login', action='store_true',
                    help='Open a Chrome window and sign in once.')
    ap.add_argument('--recapture', action='store_true',
                    help='Headless re-donation from the saved profile.')
    ap.add_argument('--verify', action='store_true',
                    help='Report signed-in or not, per platform.')
    ap.add_argument('--dry-run', action='store_true',
                    help='Write to /tmp instead of S3.')
    ap.add_argument('--use-proxy', action='store_true',
                    help='With --verify: route through the residential proxy.')
    ap.add_argument('--no-seed', action='store_true',
                    help='Do not carry existing sessions across from '
                         'everyday Chrome first.')
    ap.add_argument('--force-seed', action='store_true',
                    help='Overwrite sessions already in the scraping '
                         'profile with whatever Chrome currently has.')
    args = ap.parse_args(argv)

    domains = [d.strip().lower() for d in args.domains if d.strip()] \
        or DEFAULT_STORAGE_DOMAINS

    if args.force_seed:
        seed_profile_from_chrome(domains, force=True)

    if args.verify:
        return run_verify(domains, use_proxy=args.use_proxy)
    if args.recapture:
        return run_recapture(domains, dry_run=args.dry_run,
                             seed=not args.no_seed)
    if sys.platform != 'darwin':
        print('Signing in runs on the operator Mac, which has a real '
              'Chrome and a US residential address.')
        return 3
    return run_login(domains, dry_run=args.dry_run, seed=not args.no_seed)


if __name__ == '__main__':
    sys.exit(main())
