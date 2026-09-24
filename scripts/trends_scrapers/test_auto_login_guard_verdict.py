#!/usr/bin/env python3
"""Regression: the login paths use `_auth_guard`'s verdict, never a proxy.

The defect this pins (2026-09-24). `donate_cookies.py --auto-login
hbomax.com` printed `already: persistent session still valid`, donated,
and re-ran the scraper, which then wrote a snapshot with zero titles.
`--verify` seconds later said SIGNED_OUT. The path that decided whether
to sign in used a URL heuristic ("off the login page and no password
field means signed in"); the path that checked afterwards used the
content classification in `_auth_guard`. HBO Max's logged-out bounce
(`www.hbomax.com/?reason=anonymous`, a marketing page with no form) is
off the login page and has no password field, so the heuristic called
it signed in.

One verdict, used everywhere. A false "already signed in" is worse
than a false "signed out": the second costs one login, the first
silently freezes a rail for a day.

Hermetic: no network, no browser, no Keychain, no S3, no clickstream.

    python3 -m scripts.trends_scrapers.test_auto_login_guard_verdict
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers import _auth_guard as guard  # noqa: E402
from scripts.trends_scrapers import trends_auto_login as al  # noqa: E402

FAILURES: list = []


def check(ok: bool, what: str) -> None:
    print(f'{"PASS" if ok else "FAIL"}  {what}')
    if not ok:
        FAILURES.append(what)


# The two HBO Max pages, shaped like the ones measured on 2026-09-24.
HBOMAX_ANON_URL = ('https://www.hbomax.com/?reason=anonymous&returnUrl='
                   'https%3A%2F%2Fplay.hbomax.com%2F')
HBOMAX_MARKETING = (
    '<html><body>HBO Max | Stream Series and Movies. Sign in. Sign up now. '
    'Choose an HBO&nbsp;Max plan or bundle to start streaming. Find Your '
    'Perfect Plan. Basic With Ads. Standard. Premium.</body></html>')
HBOMAX_HOME_URL = 'https://play.hbomax.com/home'
HBOMAX_APP = (
    '<html><body>HBO Max. Continue Watching. My List. Top 10 Series Today. '
    'Lanterns. 1000-lb Sisters.</body></html>')


class _Loc:
    def __init__(self, n=0):
        self._n = n

    def count(self):
        return self._n

    @property
    def first(self):
        return self

    def nth(self, _i):
        return self

    def click(self, **_kw):
        raise RuntimeError('nothing to click')


class _Mouse:
    def wheel(self, *_a):
        pass


class FakePage:
    """The smallest page a login path can be run against.

    `routes` maps a requested URL prefix to the (final url, html) the
    platform answers with, which is how the logged-out bounce is
    modelled: ask for the app, land on `?reason=anonymous`.
    """

    def __init__(self, routes: dict[str, tuple[str, str]]):
        self.routes = routes
        self.url = 'about:blank'
        self._html = ''
        self.mouse = _Mouse()
        self.visited: list[str] = []

    def goto(self, url, **_kw):
        self.visited.append(url)
        for prefix, (final, html) in self.routes.items():
            if url.startswith(prefix):
                self.url, self._html = final, html
                return
        self.url, self._html = url, ''

    def content(self):
        return self._html

    def inner_text(self, _sel):
        return guard.page_text(self._html)

    def wait_for_timeout(self, _ms):
        pass

    def wait_for_selector(self, sel, **_kw):
        raise TimeoutError(sel)

    def query_selector(self, _sel):
        return None

    def evaluate(self, *_a, **_kw):
        return []

    def fill(self, sel, _v):
        raise RuntimeError(f'no such field {sel}')

    def input_value(self, _sel):
        return ''

    def get_by_role(self, *_a, **_kw):
        return _Loc(0)

    def get_by_text(self, *_a, **_kw):
        return _Loc(0)

    def locator(self, *_a, **_kw):
        return _Loc(0)


class FakeCtx:
    def __init__(self, page):
        self._page = page

    def new_page(self):
        return self._page

    def cookies(self):
        return []

    def clear_cookies(self, **_kw):
        pass


def _signed_out_page() -> FakePage:
    return FakePage({
        'https://play.hbomax.com': (HBOMAX_ANON_URL, HBOMAX_MARKETING),
        'https://www.hbomax.com': (HBOMAX_ANON_URL, HBOMAX_MARKETING),
        # The real login form, which this fake deliberately renders
        # WITHOUT fields, so a login attempt has to stop with a
        # reason rather than pretend it worked.
        'https://auth.hbomax.com': (
            'https://auth.hbomax.com/login?flow=login',
            '<html><body>Sign In. Legal Terms and Privacy. Agree.'
            '</body></html>'),
    })


def _signed_in_page() -> FakePage:
    return FakePage({
        'https://play.hbomax.com': (HBOMAX_HOME_URL, HBOMAX_APP),
        'https://auth.hbomax.com': (HBOMAX_HOME_URL, HBOMAX_APP),
    })


# ────────────────────────────────────────────────────────────────────
def test_the_heuristic_was_wrong() -> None:
    print('\n== The retired heuristic calls the logged-out bounce signed in ==')
    page = _signed_out_page()
    page.goto('https://play.hbomax.com/')
    recipe = al._R['hbomax.com']
    check(al._looks_logged_in(page, recipe) is True,
          'the URL heuristic reads ?reason=anonymous as signed in (the '
          'defect)')
    v, d = al.session_verdict(page, 'hbomax.com', recipe, navigate=False)
    check(v == 'signed_out',
          f'the shared verdict on the same page is signed_out ({d})')


def test_login_path_does_not_report_already() -> None:
    print('\n== attempt_login never says "already" on a signed-out page ==')
    page = _signed_out_page()
    ctx = FakeCtx(page)
    real = al.store.get_credentials
    al.store.get_credentials = lambda _d: ('operator@example.com', 'x')
    try:
        status, detail = al.attempt_login(ctx, 'hbomax.com',
                                          al._R['hbomax.com'], headed=False)
    finally:
        al.store.get_credentials = real
    check(status != 'already',
          f'status is not "already" on the anonymous bounce (got {status!r})')
    check(status in ('needs_human', 'error', 'auth_failed'),
          f'a failed sign-in stops with a reason ({status}: {detail[:50]})')
    check(any(u.startswith('https://auth.hbomax.com') for u in page.visited),
          'the path went on to the real login page')
    check(page.visited and page.visited[0] == guard.app_url('hbomax.com'),
          'the decision was made on the app page, not the login page')


def test_signed_in_profile_is_already() -> None:
    print('\n== a profile that proves signed in is "already", once ==')
    page = _signed_in_page()
    ctx = FakeCtx(page)
    status, detail = al.attempt_login(ctx, 'hbomax.com',
                                      al._R['hbomax.com'], headed=False)
    check(status == 'already', f'a real app page reads as already ({status})')
    check('rails' in detail, 'and the evidence names the rail it saw')
    check(not any(u.startswith('https://auth.hbomax.com')
                  for u in page.visited),
          'no login page was opened for a good session')


def test_forced_login_skips_the_short_circuit() -> None:
    print('\n== force_login signs in even when the profile proves signed in ==')
    page = _signed_in_page()
    dropped = []
    ctx = FakeCtx(page)
    ctx.clear_cookies = lambda **kw: dropped.append(kw)
    status, _d = al.attempt_login(ctx, 'hbomax.com', al._R['hbomax.com'],
                                  headed=False, force_login=True)
    check(page.visited and page.visited[0].startswith('https://auth.hbomax.com'),
          'force_login goes straight to the login page')
    check(bool(dropped), 'and drops the platform cookies first so the login '
                         'page cannot route the old session past itself')
    pat = dropped[0].get('domain') if dropped else None
    check(isinstance(pat, re.Pattern) and pat.search('.api.hbomax.com')
          and not pat.search('.disneyplus.com'),
          'only this platform\'s cookies are dropped')


def test_unknown_is_not_signed_in() -> None:
    print('\n== an unrecognised app page is not "already" ==')
    page = FakePage({'https://play.hbomax.com': (
        'https://play.hbomax.com/', '<html><body>loading</body></html>')})
    recipe = dict(al._R['hbomax.com'])
    # Short budget so the poll does not sit on the (no-op) timer.
    v, _d = al.session_verdict(page, 'hbomax.com', recipe, budget_ms=1)
    check(v != 'signed_in', f'an un-hydrated page is not signed in ({v})')


def test_chooser_is_interstitial() -> None:
    print('\n== the HBO Max household chooser is its own verdict ==')
    v, d = guard.classify_auth('hbomax.com',
                               "<html><body>Who's Watching? Kellie. Lucy. "
                               "jenna. Ana. New Profile. Edit</body></html>",
                               'https://play.hbomax.com/profile-picker')
    check(v == 'interstitial', f'/profile-picker reads as a chooser ({d})')
    spec = guard.site_spec('hbomax.com')
    check(bool(spec.get('chooser_tiles')) and spec.get('profile_name'),
          'HBO Max registers how to clear it')


def test_heal_wiring() -> None:
    print('\n== render_pages can re-issue a session ==')
    import inspect
    from scripts.trends_scrapers import _playwright as pwm
    from scripts.trends_scrapers import _base
    check('_heal_attempted' in inspect.signature(pwm.render_pages).parameters,
          'render_pages carries the one-retry guard')
    check(callable(getattr(pwm, '_self_heal_session', None)),
          'the heal hook exists')
    check(callable(getattr(_base, 'forget_donations', None)),
          'the donation cache can be dropped for the retry')
    real = sys.platform
    try:
        sys.platform = 'linux'
        check(pwm._self_heal_session('hbomax.com') is False,
              'off the Mac there is no Keychain, so no heal is attempted')
    finally:
        sys.platform = real


def test_token_age_reads_iat_only() -> None:
    print('\n== the token age helper returns a timestamp, never the token ==')
    import base64
    import json
    import time
    iat = int(time.time()) - 3600 * 20
    body = base64.urlsafe_b64encode(json.dumps(
        {'iat': iat, 'type': 'ACCESS_TOKEN'}).encode()).decode().rstrip('=')
    token = f'hdr.{body}.sig'

    class Ctx:
        def cookies(self):
            return [{'name': 'st', 'domain': '.api.hbomax.com',
                     'value': token}]
    age = al.token_age_hours(Ctx(), al._R['hbomax.com'])
    check(age is not None and 19.9 < age < 20.1,
          f'a 20h-old token reads as 20h ({age})')
    check(token not in repr(age), 'the token itself is not returned')
    check(al._R['hbomax.com']['max_token_age_h'] < 24,
          'HBO Max re-issues before the day-long token can lapse')


def main() -> int:
    test_the_heuristic_was_wrong()
    test_login_path_does_not_report_already()
    test_signed_in_profile_is_already()
    test_forced_login_skips_the_short_circuit()
    test_unknown_is_not_signed_in()
    test_chooser_is_interstitial()
    test_heal_wiring()
    test_token_age_reads_iat_only()
    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILURE(S)')
        for f in FAILURES:
            print(f'  - {f}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
