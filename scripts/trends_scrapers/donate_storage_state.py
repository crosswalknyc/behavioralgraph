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


def _belongs_to_platform(host: str, hosts: list[str]) -> bool:
    """True when `host` is one of the platform's hosts, a parent of one,
    or a subdomain of one.

    All three directions matter, and missing the third was a real
    defect. `_cookie_applies_to_target` answers only "would Chrome send
    this cookie TO that host", which is the parent direction: a cookie
    on `.hbomax.com` reaches `play.hbomax.com`. It says no for a cookie
    scoped to a CHILD host, which is correct for a page request and
    wrong for deciding ownership.

    Measured 2026-09-23: HBO Max's session cookie `st` is scoped to
    `.api.hbomax.com`, the host its app calls for everything. The
    parent-only test dropped it from the donation, so the one cookie
    the app authenticates with was the one cookie we never donated.
    Nothing else in the profile carries that session: HBO Max's
    IndexedDB is Amplitude and Braze analytics with no auth store at
    all, so there was no second copy to fall back on.

    Widening to subdomains cannot leak across platforms, because every
    host it admits sits under a domain this platform already owns.
    """
    from .donate_cookies import _cookie_applies_to_target

    h = (host or '').lstrip('.').lower()
    if not h:
        return False
    for target in hosts:
        t = (target or '').lstrip('.').lower()
        if not t:
            continue
        # parent direction: the cookie would be sent to `t`
        if _cookie_applies_to_target(h, t):
            return True
        # child direction: `h` is a subdomain of a host we own
        if h.endswith('.' + t):
            return True
    return False


def filter_state_for_domain(state: dict, domain: str) -> dict:
    """Keep only the cookies and origins that belong to `domain`.

    The persistent profile holds every platform at once. Uploading the
    whole thing under each domain would hand every scraper every other
    platform's tokens, so each donation is narrowed to the hosts that
    service actually uses.
    """
    hosts = guard.session_hosts(domain)
    cookies = [c for c in (state.get('cookies') or [])
               if _belongs_to_platform(c.get('domain') or '', hosts)]
    origins = [o for o in (state.get('origins') or [])
               if _belongs_to_platform(_host_of(o.get('origin') or ''), hosts)]
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

    Asks per HOST rather than per domain. The reader answers "would
    Chrome send this cookie to X", so asking it only about
    `hbomax.com` never returns the `.api.hbomax.com` cookie the app
    actually authenticates with. Asking about each of the platform's
    hosts in turn does.
    """
    from .donate_cookies import _read_chrome_cookies

    hosts: list[str] = []
    for domain in domains:
        for h in guard.session_hosts(domain):
            if h not in hosts:
                hosts.append(h)

    out: list[dict] = []
    seen: set[tuple] = set()
    for domain in hosts:
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
# ────────────────────────────────────────────────────────────────────
# Capture from a real Chrome that is already open
# ────────────────────────────────────────────────────────────────────
# WHY A SESSION SIGNED INTO BY HAND DID NOT SURVIVE A PLAYWRIGHT
# RELAUNCH OF THE SAME PROFILE (resolved 2026-09-24)
#
# On 2026-09-23 HBO Max was signed into in a real Chrome window opened
# on the scraping profile, and a Playwright launch of that same
# profile minutes later was signed out. The two browsers do not share
# a cookie jar even though they share a directory: Playwright starts
# Chrome with `--use-mock-keychain` and `--password-store=basic`
# (see `driver/package/lib/server/chromium/chromiumSwitches.js`), so
# a cookie a hand-started Chrome encrypted against the real macOS
# Keychain key is unreadable to the Playwright-launched one, and the
# reverse. HBO Max's whole session is one cookie (`st` on
# `.api.hbomax.com`), so it vanished. localStorage and IndexedDB are
# not encrypted, which is why those halves came across and the cookie
# half did not.
#
# Consequence: the scraping profile must only ever be opened by
# Playwright. A hand-started Chrome on it can donate over CDP (this
# section), because the live browser decrypts its own cookies and the
# JSON storage state carries them in the clear, but it cannot leave a
# session behind for the next Playwright launch. The Keychain-backed
# auto-login (`trends_auto_login`) signs in inside Playwright, which is
# what makes the profile's session durable.
#
# Start one the same way `start_chrome_for_cookie_export.command`
# does, pointed at the scraping profile:
#
#   open -na "Google Chrome" --args \
#       --user-data-dir="$HOME/Library/Application Support/\
# CrosswalkTrendsLogin/chrome-profile" --remote-debugging-port=9223
_DEFAULT_CDP_PORTS = (9223, 9222)


def find_live_chrome(cdp_url: Optional[str] = None) -> Optional[str]:
    """Return a reachable CDP endpoint, or None."""
    import urllib.request

    candidates = [cdp_url] if cdp_url else [
        f'http://127.0.0.1:{p}' for p in _DEFAULT_CDP_PORTS]
    for base in candidates:
        if not base:
            continue
        try:
            with urllib.request.urlopen(f'{base}/json/version', timeout=3):
                return base
        except Exception:
            continue
    return None


def run_from_live_chrome(domains: list[str], *, cdp_url: Optional[str] = None,
                         dry_run: bool = False, pw=None) -> int:
    """Donate from a hand-started Chrome instead of relaunching one.

    `pw` lets a caller that already has Playwright open hand it in.
    Starting a second one inside the first raises, which is how the
    recapture fallback first failed.
    """
    sp = _lazy_playwright()
    if sp is None and pw is None:
        print('Playwright is not installed.')
        return 3

    endpoint = find_live_chrome(cdp_url)
    if not endpoint:
        print('No Chrome with remote debugging is open. Start one on the')
        print('scraping profile, sign in, then re-run this:')
        print()
        print('  open -na "Google Chrome" --args \\')
        print(f'    --user-data-dir="{PROFILE_DIR}" \\')
        print('    --remote-debugging-port=9223')
        return 1

    known = [d for d in domains if guard.site_spec(d)]
    if not known:
        print('No session-gated platforms in that list.')
        return 1

    print(f'Attached to the Chrome already open at {endpoint}.')
    print('Its own tabs are left alone.')
    print()

    results: dict[str, tuple[str, str]] = {}
    owns_pw = pw is None
    manager = sp() if owns_pw else None
    if owns_pw:
        pw = manager.__enter__()
    try:
        try:
            browser = pw.chromium.connect_over_cdp(endpoint)
        except Exception as e:
            print(f'Could not attach: {e}')
            return 3
        if not browser.contexts:
            print('That browser has no context to read.')
            return 3
        ctx = browser.contexts[0]

        opened = []
        for domain in known:
            try:
                page = ctx.new_page()
                opened.append(page)
                page.goto(guard.app_url(domain),
                          wait_until='domcontentloaded', timeout=60000)
                results[domain] = _settle(page, domain)
            except Exception as e:
                results[domain] = ('unknown', f'{type(e).__name__}: {e}')

        rc = _capture_and_upload(ctx, known, dry_run=dry_run, results=results,
                                 pw=pw)

        for page in opened:
            try:
                page.close()
            except Exception:
                pass
        # Deliberately NOT browser.close(). It is not ours to close.
        return rc
    finally:
        if owns_pw and manager is not None:
            manager.__exit__(None, None, None)


def capture_state_for(ctx, domain: str) -> dict:
    """Capture this context's session and narrow it to one platform.

    Touches every host the platform owns first, because
    `storage_state` only reports origins a page has visited (see
    `_touch_origins`). Returns `{'cookies': [], 'origins': []}` when
    nothing could be read, never raises.
    """
    touched = _touch_origins(ctx, domain)
    try:
        full = ctx.storage_state(indexed_db=True)
    except TypeError:
        full = ctx.storage_state()
    except Exception as e:
        logger.info('could not read the session for %s: %s', domain, e)
        full = {}
    finally:
        for page in touched:
            try:
                page.close()
            except Exception:
                pass
    return filter_state_for_domain(full or {}, domain)


def state_survives(pw, domain: str, state: dict, *,
                   browser=None) -> tuple[bool, str]:
    """Restore `state` into a fresh context and judge the app page.

    THE one question every consumer asks. `--verify` asks it of the
    S3 donation, a scraper asks it of the same donation through
    `render_pages`, and the capture side asks it here BEFORE
    uploading. Same restore, same URL, same `settle_and_judge`, same
    verdict, so the path that decides whether to sign in can never be
    more optimistic than the path that checks afterwards.

    `browser` lets a caller share one headless launch across several
    platforms. Never raises: anything that cannot be checked reads as
    not surviving, which errs toward a sign-in rather than a stale
    rail.
    """
    if not (state.get('cookies') or state.get('origins')):
        return False, 'nothing to donate for this platform'
    own = browser is None
    if own:
        try:
            browser = pw.chromium.launch(
                channel='chrome', headless=True,
                args=['--headless=new', '--no-sandbox',
                      '--disable-blink-features=AutomationControlled'])
        except Exception as e:
            return False, f'could not open a check browser: {e}'
    check = None
    try:
        check = browser.new_context(
            storage_state=state, user_agent=UA,
            viewport={'width': 1440, 'height': 900},
            locale='en-US', timezone_id='America/New_York',
            extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'})
        page = check.new_page()
        _try_stealth(page)
        page.goto(guard.app_url(domain),
                  wait_until='domcontentloaded', timeout=60000)
        verdict, detail = _settle(page, domain)
        return verdict == 'signed_in', detail
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'
    finally:
        if check is not None:
            try:
                check.close()
            except Exception:
                pass
        if own:
            try:
                browser.close()
            except Exception:
                pass


def confirm_donations_survive(pw, ctx, domains: list[str]
                              ) -> dict[str, tuple[bool, str]]:
    """Capture what we WOULD donate for each domain and render it.

    This is the same question `--verify` asks, asked early: not "does
    this tab look signed in" but "does this session still work once it
    has been captured and restored", which is what every scraper does.

    One headless browser is shared across the whole set, so the cost
    is one launch rather than one per platform. Never raises; a
    platform that cannot be checked is reported as not surviving,
    which errs toward offering a sign-in tab rather than skipping one.
    """
    out: dict[str, tuple[bool, str]] = {}
    browser = None
    try:
        browser = pw.chromium.launch(
            channel='chrome', headless=True,
            args=['--headless=new', '--no-sandbox',
                  '--disable-blink-features=AutomationControlled'])
    except Exception as e:
        return {d: (False, f'could not open a check browser: {e}')
                for d in domains}

    for domain in domains:
        state = capture_state_for(ctx, domain)
        out[domain] = state_survives(pw, domain, state, browser=browser)
    try:
        browser.close()
    except Exception:
        pass
    return out


def run_login(domains: list[str], *, dry_run: bool = False,
              seed: bool = True, force: bool = False) -> int:
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

        # First pass: what does the live tab say?
        done: set[str] = set()
        looks_ok: list[str] = []
        for domain, page in pages.items():
            verdict, detail = _settle(page, domain)
            results[domain] = (verdict, detail)
            if verdict == 'signed_in':
                looks_ok.append(domain)

        # Second pass: does that survive being donated?
        #
        # A tab looking signed in is NOT the question. The question is
        # whether the session still works once it has been captured
        # and restored, which is what every scraper actually does and
        # what --verify measures. On 2026-09-23 those two answers
        # disagreed: this path printed "already signed in" for HBO Max
        # and offered no tab, while --verify minutes later said signed
        # out on the same profile. The optimistic detector was the one
        # deciding to skip the work, so Jenna sat waiting for a window
        # that never asked her for anything.
        #
        # So a tab is only skipped when the donation itself verifies.
        survived: dict[str, tuple[bool, str]] = {}
        if looks_ok and not force:
            print(f'  checking {len(looks_ok)} session(s) survive donation ...')
            survived = confirm_donations_survive(pw, ctx, looks_ok)

        for domain in pages:
            label = guard.site_label(domain)
            if force:
                print(f'  [OPEN]               {label}  (--force)')
                continue
            if domain not in looks_ok:
                print(f'  [NEEDS SIGN-IN]      {label}')
                continue
            ok, why = survived.get(domain, (False, 'not checked'))
            if ok:
                done.add(domain)
                print(f'  [already signed in]  {label}')
            else:
                print(f'  [NEEDS SIGN-IN]      {label}  (the tab looks '
                      f'signed in, but the session does not survive being '
                      f'donated: {why[:60]})')

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
                    left = max(0, int(deadline - time.time()))
                    still = [guard.site_label(d) for d in waiting
                             if d not in done]
                    if still:
                        # Padded so a shorter line never leaves the
                        # tail of a longer one behind it.
                        print(f'  waiting on: {", ".join(still)} '
                              f'({left // 60}m {left % 60}s left)'.ljust(78),
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
                                 results=results, pw=pw)
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
            # A Chrome already open on this profile holds its lock, so
            # the relaunch cannot have it. That is a normal state, not
            # a failure: somebody is signed in over there right now.
            # Read from that browser instead of reporting a dead run,
            # which is what would otherwise quietly stop the daily
            # donation for as long as the window stayed open.
            endpoint = find_live_chrome()
            if endpoint:
                print('The scraping profile is open in Chrome already, so '
                      'reading the session from that window instead.')
                print('Note: that window only has the platforms signed in '
                      'inside it. The relaunch path also carries sessions '
                      'across from everyday Chrome, so close that window '
                      'when you are done to get the full set. Platforms '
                      'that are not signed in there keep their existing '
                      'donation rather than being overwritten.')
                return run_from_live_chrome(domains, cdp_url=endpoint,
                                            dry_run=dry_run, pw=pw)
            print(f'Could not open the Chrome profile: {e}')
            return 3

        def judge_all(targets: list[str]) -> None:
            for domain in targets:
                try:
                    page = ctx.new_page()
                    _try_stealth(page)
                    page.goto(guard.app_url(domain),
                              wait_until='domcontentloaded', timeout=60000)
                    results[domain] = _settle(page, domain)
                except Exception as e:
                    results[domain] = ('unknown', f'{type(e).__name__}: {e}')

        # The profile's OWN session first. A platform the Keychain
        # auto-login signed into minutes ago holds a fresher token than
        # everyday Chrome does, and `add_cookies` replaces by name, so
        # layering Chrome's jar on top unconditionally would overwrite
        # a live HBO Max `st` with a stale one and sign the profile
        # out. Chrome's cookies are only carried across for platforms
        # the profile cannot prove on its own.
        judge_all(known)
        opened: list[str] = list(known)
        if sys.platform == 'darwin':
            fallback = [d for d in known
                        if results.get(d, ('unknown', ''))[0] != 'signed_in']
            if fallback:
                try:
                    ctx.add_cookies(chrome_cookies_for(fallback))
                    judge_all(fallback)
                except Exception as e:
                    logger.info('could not carry cookies across: %s', e)
        rc = _capture_and_upload(ctx, opened, dry_run=dry_run,
                                 results=results, pw=pw)
        try:
            ctx.close()
        except Exception:
            pass
    return rc


def _touch_origins(ctx, domain: str) -> list:
    """Open a throwaway document on each of the platform's hosts.

    `storage_state` only reports origins that a page in this context
    has actually visited. It is not reading the profile off disk, it
    is reporting what it has seen, so an origin nobody opened comes
    back with no localStorage and no IndexedDB even though both exist.

    That is the whole explanation for the "32 cookies and ZERO
    origins" capture over CDP on 2026-09-23. Nothing was wrong with
    the CDP route; no page had been opened on those origins. Opening
    one on each host first turns the same call into 4 origins, 137
    localStorage keys and 9 IndexedDB stores.

    `/robots.txt` is used because it is cheap and, unlike the app
    routes, does not redirect away from the host when signed out,
    which is exactly when we most need its storage.
    """
    opened = []
    for host in guard.session_hosts(domain):
        try:
            page = ctx.new_page()
            page.goto(f'https://{host}/robots.txt',
                      wait_until='domcontentloaded', timeout=20000)
            opened.append(page)
        except Exception as e:
            logger.debug('could not touch %s: %s', host, e)
            try:
                page.close()
            except Exception:
                pass
    return opened


def _capture_and_upload(ctx, domains: list[str], *, dry_run: bool,
                        results: dict[str, tuple[str, str]],
                        pw=None) -> int:
    """Capture once, narrow per platform, upload the proven ones.

    With `pw` given, every platform that judged signed in is ALSO
    restored into a fresh context and judged again before its upload
    (`state_survives`). That is the question `--verify` asks after the
    fact, asked before the write, so a capture that looked signed in
    but does not survive restoration is held rather than overwriting
    a good donation with a dead one.
    """
    # Register every host before the capture, or its storage is
    # invisible to `storage_state`. Held open until after the call.
    touched: list = []
    for domain in domains:
        touched.extend(_touch_origins(ctx, domain))
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
    finally:
        for page in touched:
            try:
                page.close()
            except Exception:
                pass

    check_browser = None
    if pw is not None:
        try:
            check_browser = pw.chromium.launch(
                channel='chrome', headless=True,
                args=['--headless=new', '--no-sandbox',
                      '--disable-blink-features=AutomationControlled'])
        except Exception as e:
            logger.info('could not open a check browser: %s', e)

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
        if check_browser is not None:
            ok, why = state_survives(pw, domain, state, browser=check_browser)
            if not ok:
                held += 1
                print(f'{label:<18s} {"NOT SIGNED IN":<14s} the profile '
                      f'looked signed in but the captured session does '
                      f'not survive restoration: {why[:60]}')
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

    if check_browser is not None:
        try:
            check_browser.close()
        except Exception:
            pass

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
    ap.add_argument('--from-live-chrome', action='store_true',
                    help='Donate from a Chrome you already started by '
                         'hand, instead of relaunching the profile.')
    ap.add_argument('--cdp-url', default=None,
                    help='With --from-live-chrome: the debugging endpoint '
                         '(default: try 9223 then 9222).')
    ap.add_argument('--force', action='store_true',
                    help='With --login: open every tab, even ones that '
                         'already look signed in.')
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
    if args.from_live_chrome:
        return run_from_live_chrome(domains, cdp_url=args.cdp_url,
                                    dry_run=args.dry_run)
    if args.recapture:
        return run_recapture(domains, dry_run=args.dry_run,
                             seed=not args.no_seed)
    if sys.platform != 'darwin':
        print('Signing in runs on the operator Mac, which has a real '
              'Chrome and a US residential address.')
        return 3
    return run_login(domains, dry_run=args.dry_run, seed=not args.no_seed,
                     force=args.force)


if __name__ == '__main__':
    sys.exit(main())
