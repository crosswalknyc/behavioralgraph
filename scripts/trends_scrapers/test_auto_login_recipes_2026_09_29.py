#!/usr/bin/env python3
"""Regression: the 2026-09-29 auto-login recipe fixes.

Tonight's run reported four broken recipes. Each fix is pinned here
against a fake page so the shape cannot drift back:

  audible.com        the Amazon session is REUSED. When the profile is
                     signed into Amazon, Audible's app page proves it
                     and no credential is even read.
  britbox.com        the login URL was a 404; the form is single-step
                     with #email / #pwd / signin_submit. The invisible
                     reCAPTCHA badge on the page is not a wall.
  xbox.com           Microsoft's passkey-first flow is walked by a
                     dedicated flow with WebAuthn hidden; the password
                     is entered at most once; a rejection is
                     auth_failed, never a retry.
  open.spotify.com   new recipe on the direct password route; signed-in
                     is proven by the account widget, because the
                     anonymous web player renders a full app too.
  starz.com          'playlist' is a signed-in tell; 'claim today' a
                     signed-out one.

Hermetic: no network, no browser, no Keychain, no S3. No credential
value appears anywhere in this file; the fake store hands back
placeholders and the checks count fills, never read them.

    python3 -m scripts.trends_scrapers.test_auto_login_recipes_2026_09_29
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers import _auth_guard as guard  # noqa: E402
from scripts.trends_scrapers import trends_auto_login as al  # noqa: E402
from scripts.trends_scrapers.test_auto_login_guard_verdict import (  # noqa: E402
    FakeCtx, FakePage, FAILURES, check)


class _El:
    def __init__(self, page, sel):
        self._page, self._sel = page, sel

    def click(self, **_kw):
        self._page.clicked.append(self._sel)
        after = self._page.after_click.get(self._sel)
        if after:
            self._page.url, self._page._html = after


class FormPage(FakePage):
    """A FakePage with real-ish form fields.

    `fields` is the set of selectors that exist on the page. Filling
    records the selector only (never the value). `after_click` maps a
    clicked selector to the (url, html) the site answers with.
    `interactive_captcha` is what the captcha JS would report.
    """

    def __init__(self, routes, fields=(), after_click=None,
                 interactive_captcha=False):
        super().__init__(routes)
        self.fields = set(fields)
        self.after_click = after_click or {}
        self.interactive_captcha = interactive_captcha
        self.filled: list[str] = []
        self.clicked: list[str] = []
        self.init_scripts: list[str] = []

    def add_init_script(self, js):
        self.init_scripts.append(js)

    def wait_for_selector(self, sel, **_kw):
        if sel in self.fields:
            return _El(self, sel)
        raise TimeoutError(sel)

    def query_selector(self, sel):
        return _El(self, sel) if sel in self.fields else None

    def fill(self, sel, _value):
        if sel not in self.fields:
            raise RuntimeError(f'no such field {sel}')
        self.filled.append(sel)

    def evaluate(self, js, *_a, **_kw):
        if js is al._INTERACTIVE_CAPTCHA_JS:
            return self.interactive_captcha
        return []


def _with_fake_store(fn, *, creds=('operator@example.com', 'placeholder')):
    real = al.store.get_credentials
    calls = []

    def fake(domain):
        calls.append(domain)
        return creds
    al.store.get_credentials = fake
    try:
        return fn(), calls
    finally:
        al.store.get_credentials = real


# ────────────────────────────────────────────────────────────────────
def test_audible_reuses_the_amazon_session() -> None:
    print('\n== audible.com reuses the Amazon session, no credential read ==')
    check(guard.app_url('audible.com').endswith('/ep/podcasts'),
          'the Audible probe page is /ep/podcasts (the bare home never '
          'paints a rail)')
    check('amazon.com' in (guard.site_spec('audible.com') or {}).get('hosts', []),
          'audible.com shares the Amazon host in its spec')
    page = FormPage({
        'https://www.audible.com/ep/podcasts': (
            'https://www.audible.com/ep/podcasts',
            '<html><body>Audible. Podcasts. Wish List. Your Library. '
            'Continue Listening.</body></html>'),
    })
    (status, detail), calls = _with_fake_store(
        lambda: al.attempt_login(FakeCtx(page), 'audible.com',
                                 al._R['audible.com'], headed=False))
    check(status == 'already',
          f'a shared Amazon session reads as already ({status}: {detail[:60]})')
    check(calls == [], 'no credential was read from the store')
    check(page.filled == [], 'nothing was typed into any field')
    check(not any('signin' in u or '/ap/' in u for u in page.visited),
          'the Amazon sign-in page was never opened')

    anon = FormPage({'https://www.audible.com/ep/podcasts': (
        'https://www.audible.com/ep/podcasts',
        '<html><body>Audible. Podcasts. Sign in. Try Audible free.'
        '</body></html>')})
    v, _d = al.session_verdict(anon, 'audible.com', al._R['audible.com'],
                               budget_ms=1)
    check(v != 'signed_in', f'an anonymous podcasts page is not signed in ({v})')


def test_invisible_captcha_is_not_a_wall() -> None:
    print('\n== an invisible reCAPTCHA badge is not a verification wall ==')
    js = al._INTERACTIVE_CAPTCHA_JS
    check('size=invisible' in js, 'the JS skips size=invisible anchors')
    check('badge' in js, 'the JS skips the .grecaptcha-badge wrapper')
    quiet = FormPage({}, interactive_captcha=False)
    quiet._html = '<html><body>Sign in. Email. Password. Protected by '\
                  'reCAPTCHA.</body></html>'
    check(al._challenge_reason(quiet) is None,
          'a page with only a passive badge has no challenge reason')
    loud = FormPage({}, interactive_captcha=True)
    loud._html = quiet._html
    r = al._challenge_reason(loud)
    check(r is not None and 'captcha' in r,
          f'a visible captcha widget is still a wall ({r})')
    otp = FormPage({}, fields={'input[autocomplete="one-time-code"]'})
    otp._html = quiet._html
    check(al._challenge_reason(otp) is not None, 'an OTP field is a wall')
    check(not hasattr(al, '_CHALLENGE_SELECTORS'),
          'the old any-captcha-iframe selector list is gone')


def test_britbox_recipe_shape() -> None:
    print('\n== britbox.com recipe points at the real single-step form ==')
    r = al._R['britbox.com']
    check(r['login_url'] == 'https://www.britbox.com/us/account/login',
          'login_url is /us/account/login (the old /signin was a 404)')
    check('input#email' in r['user_sel'] and 'input#pwd' in r['pass_sel'],
          'selectors name the measured #email / #pwd fields')
    check(r['submit_sel'][0] == 'button[name="signin_submit"]',
          'submit is the named signin_submit button')
    check(not r.get('two_step'), 'single-step: both fields on one page')


def test_spotify_recipe_and_element_verdict() -> None:
    print('\n== open.spotify.com: direct password route, widget-proven ==')
    r = al._R['open.spotify.com']
    check('allow_password=1' in r['login_url'] and 'method=password' in r['login_url'],
          'the login URL takes the direct password route (no emailed code)')
    check('accounts.spotify.com' in r['login_url'], 'on accounts.spotify.com')
    check(bool(r.get('verify_ok_sel')) and bool(r.get('verify_bad_sel')),
          'the recipe names both the signed-in and the signed-out controls')
    check(guard.site_spec('open.spotify.com') is None,
          'Spotify is not a guard site, so the element path is the verdict')

    app = 'https://open.spotify.com/'
    widget = r['verify_ok_sel'][0]
    login_btn = r['verify_bad_sel'][0]

    signed_in = FormPage({app: (app, '<html><body>Your Library</body></html>')},
                         fields={widget})
    v, d = al.session_verdict(signed_in, 'open.spotify.com', r)
    check(v == 'signed_in', f'the account widget proves signed in ({v}: {d})')

    anon = FormPage({app: (app, '<html><body>Your Library. Sign up. Log in'
                                '</body></html>')}, fields={login_btn})
    v, d = al.session_verdict(anon, 'open.spotify.com', r)
    check(v == 'signed_out',
          f'the anonymous player (which also has "Your Library") is signed_out ({v})')
    check(al._looks_logged_in(anon, r) is True,
          'the retired URL heuristic would have called that anonymous player '
          'signed in (the defect the element path prevents)')

    blank = FormPage({app: (app, '<html><body>loading</body></html>')})
    v, _d = al.session_verdict(blank, 'open.spotify.com', r)
    check(v == 'unknown', f'no controls at all is unknown, never signed in ({v})')


def test_spotify_rejection_is_auth_failed_once() -> None:
    print('\n== a Spotify rejection is auth_failed after ONE password fill ==')
    r = al._R['open.spotify.com']
    app = 'https://open.spotify.com/'
    login_btn = r['verify_bad_sel'][0]
    user_sel, pass_sel, submit = r['user_sel'][0], r['pass_sel'][0], r['submit_sel'][0]
    rejected = ('<html><body>Log in with a password. Incorrect email address '
                'or password. Email. Password. Log in</body></html>')
    page = FormPage(
        {app: (app, '<html><body>Sign up. Log in</body></html>'),
         'https://accounts.spotify.com': (r['login_url'],
                                          '<html><body>Log in with a password. '
                                          'Email. Password.</body></html>')},
        fields={login_btn, user_sel, pass_sel, submit},
        after_click={submit: (r['login_url'], rejected)})
    (status, detail), calls = _with_fake_store(
        lambda: al.attempt_login(FakeCtx(page), 'open.spotify.com', r,
                                 headed=False))
    check(status == 'auth_failed',
          f'"incorrect email address or password" is auth_failed ({status}: {detail[:50]})')
    check(page.filled.count(pass_sel) == 1,
          f'the password field was filled exactly once ({page.filled.count(pass_sel)})')
    check(page.clicked.count(submit) == 1, 'and submitted exactly once')
    check(calls == ['open.spotify.com'], 'the store was consulted once, by domain')


def test_xbox_flow_shape_and_single_password() -> None:
    print('\n== xbox.com walks the Microsoft passkey fork, one password ==')
    r = al._R['xbox.com']
    check(r.get('flow') == 'xbox' and callable(al._FLOWS.get('xbox')),
          'the recipe dispatches to the registered xbox flow')
    check(r.get('block_webauthn') is True, 'WebAuthn is hidden for Microsoft')
    check(r['login_url'].startswith('https://www.xbox.com/en-US/auth/msa?action=logIn'),
          'login_url is the MSA auth round trip, not the /play page')
    check('PublicKeyCredential' in al._BLOCK_WEBAUTHN_JS,
          'the init script removes PublicKeyCredential')

    login = 'https://login.live.com/oauth20_authorize.srf?client_id=x'
    email_sel, pass_sel, nxt = ('input#usernameEntry', 'input#passwordEntry',
                                'button[data-testid="primaryButton"]')
    page = FormPage(
        {'https://www.xbox.com/en-US/auth/msa': (
            login, '<html><body>Sign in. Email, phone, or Skype.</body></html>'),
         'https://www.xbox.com/en-US/play': (
            'https://www.xbox.com/en-US/play',
            '<html><body>Xbox. Join Game Pass. Sign in.</body></html>')},
        fields={email_sel, pass_sel, nxt},
        after_click={nxt: (login, '<html><body>Enter your password. That '
                                  'password is incorrect for your Microsoft '
                                  'account.</body></html>')})
    (status, detail), _calls = _with_fake_store(
        lambda: al.attempt_login(FakeCtx(page), 'xbox.com', r, headed=False))
    check(page.init_scripts == [al._BLOCK_WEBAUTHN_JS],
          'the WebAuthn block was injected before navigation')
    check(status == 'auth_failed',
          f'Microsoft\'s rejection is auth_failed ({status}: {detail[:50]})')
    check(page.filled.count(pass_sel) == 1,
          f'the password was entered exactly once ({page.filled.count(pass_sel)})')
    check(page.filled.count(email_sel) == 1, 'and the email once')

    # With no password field reachable the flow stops with a reason and
    # never types a password anywhere.
    stuck = FormPage(
        {'https://www.xbox.com/en-US/auth/msa': (
            login, '<html><body>Get a code to sign in. Send code. Other ways '
                   'to sign in.</body></html>'),
         'https://www.xbox.com/en-US/play': (
            'https://www.xbox.com/en-US/play',
            '<html><body>Join Game Pass.</body></html>')},
        fields={email_sel, nxt})
    (status, detail), _c = _with_fake_store(
        lambda: al.attempt_login(FakeCtx(stuck), 'xbox.com', r, headed=False))
    check(status == 'needs_human', f'no password step reachable is needs_human ({status})')
    check(stuck.filled.count(pass_sel) == 0, 'and no password was typed')


def test_starz_markers() -> None:
    print('\n== starz.com: "playlist" is signed in, "claim today" signed out ==')
    v, d = guard.classify_auth(
        'starz.com', '<html><body>Home Series Movies Playlist. Watch now.'
                     '</body></html>', 'https://www.starz.com/us/en/')
    check(v == 'signed_in', f'the Playlist nav proves signed in ({d})')
    v, d = guard.classify_auth(
        'starz.com', '<html><body>STARZ. $5/mo for 3 mo. Claim today. Home '
                     'Series Movies</body></html>', 'https://www.starz.com/us/en/')
    check(v == 'signed_out', f'the anonymous offer page is signed out ({d})')


def main() -> int:
    test_audible_reuses_the_amazon_session()
    test_invisible_captcha_is_not_a_wall()
    test_britbox_recipe_shape()
    test_spotify_recipe_and_element_verdict()
    test_spotify_rejection_is_auth_failed_once()
    test_xbox_flow_shape_and_single_password()
    test_starz_markers()
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
