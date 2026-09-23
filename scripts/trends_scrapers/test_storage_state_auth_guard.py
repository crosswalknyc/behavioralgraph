#!/usr/bin/env python3
"""Regression: the session-gated scrapers refuse to publish the wrong
page, and a donation never carries another platform's tokens.

Every platform in here fails the same way Plex does: HTTP 200 with a
complete, plausible, wrong page. So the fixtures are not invented.
Each one is the shape of a page actually served on 2026-09-23, and the
three that cost the most time to work out each get their own test:

  * PRIME VIDEO, the subtle one. Its logged-out storefront renders
    "Watchlist" in the nav, so that word is not evidence of anything.
    Worse, a signed-in account with nothing part-watched renders NO
    Continue Watching rail either, so rails alone cannot separate the
    two pages. 34,573 characters of signed-in storefront came back
    with no rail at all. The only difference is the nav greeting:
    "Hello, sign in" against "Hello, <name>". Both directions are
    pinned here because getting either wrong silently publishes a
    logged-out catalog as a chart.

  * HBO MAX, the loud one and the reason this exists. It bounces an
    unauthenticated visitor to `?reason=anonymous` and then serves the
    full marketing site: plan cards, artwork, title text. It parses.
    The guard must REFUSE, so these assert the exception rather than a
    falsy return.

  * THE PROFILE CHOOSERS. A signed-in account parked on "who's
    watching" is authenticated but renders no rails. It must read as
    its own verdict, not as logged out, or an operator gets sent to
    re-authorize a session that was fine. The trap is that "manage
    profiles" sits in the nav of the signed-in Netflix, Peacock and
    Prime Video pages, so it cannot be a chooser marker.

Hermetic: no network, no S3, no browser, no clickstream.

    python3 -m scripts.trends_scrapers.test_storage_state_auth_guard
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers import _auth_guard as guard  # noqa: E402
from scripts.trends_scrapers import donate_storage_state as dss  # noqa: E402

FAILURES: list = []


def check(ok: bool, what: str) -> None:
    print(f'{"PASS" if ok else "FAIL"}  {what}')
    if not ok:
        FAILURES.append(what)


def _page(body: str) -> str:
    return f'<html><body>{body}</body></html>'


# Verbatim-shaped copy from the pages measured on 2026-09-23.
HBOMAX_MARKETING = _page(
    'HBO Max | Stream Series and Movies. Browse. Sign in. Sign up. '
    'Must-see series, movies &amp; more. Choose an HBO&nbsp;Max plan or '
    'bundle to start streaming. Find Your Perfect Plan. Basic With Ads. '
    'Standard. Premium. Sign up now.')
HBOMAX_APP = _page(
    'HBO Max. Continue Watching. Trending Now. Top 10 in the U.S. Today. '
    'My List. The Last of Us. House of the Dragon.')

DISNEY_APP = _page(
    'For You | Disney+. Jenna. Home. Live. Watchlist. Movies. Series. '
    'Originals. Continue Watching. Recommended For You.')
DISNEY_LOGIN = _page(
    'Login to Disney+. Enter your email to continue. Log in to Disney+ '
    'with your MyDisney account.')

HULU_CHOOSER = _page(
    "Hulu | Profiles. Profiles. Who's watching? Jenna. Anastasia. "
    'Guest. Kellie. Lucy. Kids.')
HULU_WELCOME = _page(
    'Stream TV and Movies Live and Online | Hulu. Log in. Bundle plans '
    'starting at $12.99/month. Select your plan. Cancel anytime. '
    'Start your free trial.')
HULU_APP = _page('Hulu. Continue Watching. Because You Watched. My Stuff.')

# The two Prime Video pages that look almost identical.
PRIME_LOGGED_OUT = _page(
    'Prime Video: Watch Movies, TV Shows, Sports, and Live TV. '
    'Hello, sign in. Account &amp; Lists. Home. Store. Watchlist. '
    'Free with ads. The Boys. Reacher.')
PRIME_SIGNED_IN_NO_RAIL = _page(
    'Prime Video: Watch Movies, TV Shows, Sports, and Live TV. '
    'Hello, Aunt. Account. Your Account. Home. Store. Watchlist. '
    'My Stuff. Manage profiles. The Boys. Reacher.')
PRIME_SIGNED_IN_RAIL = _page(
    'Prime Video. Hello, Aunt. Continue Watching. Watchlist. '
    'Manage profiles.')

NETFLIX_APP = _page(
    'Home - Netflix. Home. Shows. Movies. Games. New &amp; Popular. '
    'My Netflix. Continue Watching. Top 10. New on Netflix. '
    'Manage profiles.')
NETFLIX_LOGIN = _page(
    'Netflix. Enter your info to sign in or get started with a new '
    'account. Email or mobile number. Password. Continue. Get help.')

PEACOCK_APP = _page(
    'Home - Peacock. Home. Movies. TV Shows. Sports. My Stuff. '
    'Channels. Continue Watching. Trending Now. Manage profiles.')
PEACOCK_MARKETING = _page(
    'Peacock: Stream TV and Movies Online. Sign in. Get started. '
    'Hit movies, must-see TV, and live sports. Choose a Peacock plan '
    'or bundle to start streaming. Cancel anytime.')


# ────────────────────────────────────────────────────────────────────
def test_hbomax_refuses() -> None:
    print('\n== HBO Max: the marketing page is refused, not parsed ==')

    # The platform's own redirect settles it before any copy is read.
    v, d = guard.classify_auth(
        'hbomax.com', HBOMAX_MARKETING,
        'https://www.hbomax.com/?reason=anonymous&returnUrl=https%3A%2F%2Fp')
    check(v == 'signed_out', 'the anonymous bounce reads as logged out')
    check('reason=anonymous' in d, 'the verdict names the redirect it saw')

    # And the copy alone is enough when no URL is to hand.
    v, _d = guard.classify_auth('hbomax.com', HBOMAX_MARKETING)
    check(v == 'signed_out', 'the plan picker alone reads as logged out')

    # It must RAISE. A falsy return would let a caller publish the
    # promotional titles sitting on that page.
    raised = None
    try:
        guard.assert_signed_in('hbomax.com', HBOMAX_MARKETING,
                               source='max', url='https://www.hbomax.com/'
                                                 '?reason=anonymous')
    except guard.AuthWallError as e:
        raised = e
    check(raised is not None, 'a logged-out HBO Max raises')
    if raised is not None:
        msg = str(raised)
        check('--login' in msg, 'the failure says how to re-authorize')
        check('hbomax.com' in msg, 'the failure names the platform')

    v, _d = guard.classify_auth('hbomax.com', HBOMAX_APP,
                                'https://play.hbomax.com/')
    check(v == 'signed_in', 'the real app is accepted')
    check(guard.assert_signed_in('hbomax.com', HBOMAX_APP, source='max',
                                 url='https://play.hbomax.com/'),
          'the real app returns its evidence')


def test_prime_video_greeting() -> None:
    print('\n== Prime Video: the nav greeting is the only difference ==')

    v, _d = guard.classify_auth(
        'amazon.com', PRIME_LOGGED_OUT,
        'https://www.amazon.com/gp/video/storefront')
    check(v == 'signed_out',
          'a logged-out storefront is refused even though it has titles')

    # This is the trap. "Watchlist" is in the logged-out nav.
    check('watchlist' not in
          [m.lower() for m in guard.site_spec('amazon.com')['signed_in']],
          'bare "watchlist" is not treated as a signed-in rail')

    v, d = guard.classify_auth(
        'amazon.com', PRIME_SIGNED_IN_NO_RAIL,
        'https://www.amazon.com/gp/video/storefront')
    check(v == 'signed_in',
          'a signed-in account with no Continue Watching rail is accepted')
    check('aunt' not in d.lower(),
          'the verdict does not repeat the account holder name')

    v, _d = guard.classify_auth('amazon.com', PRIME_SIGNED_IN_RAIL,
                                'https://www.amazon.com/gp/video/storefront')
    check(v == 'signed_in', 'a signed-in account with a rail is accepted')


def test_profile_choosers() -> None:
    print('\n== Profile choosers: authenticated, but not the app ==')

    v, _d = guard.classify_auth('hulu.com', HULU_CHOOSER,
                                'https://www.hulu.com/profiles?next=/hub/home')
    check(v == 'interstitial', 'the Hulu chooser is its own verdict')

    v, _d = guard.classify_auth('hulu.com', HULU_CHOOSER)
    check(v == 'interstitial', "\"who's watching\" alone is enough")

    raised = None
    try:
        guard.assert_signed_in('hulu.com', HULU_CHOOSER, source='hulu',
                               url='https://www.hulu.com/profiles')
    except guard.AuthWallError as e:
        raised = e
    check(raised is not None, 'a chooser raises rather than publishing')
    check(raised is not None and 'chooser' in str(raised).lower(),
          'the failure says it was a chooser, not an expired session')

    # The trap: "manage profiles" is in the nav of three signed-in
    # pages. Treating it as a chooser marker would park all of them.
    for domain, html, url in (
            ('netflix.com', NETFLIX_APP, 'https://www.netflix.com/browse'),
            ('peacocktv.com', PEACOCK_APP,
             'https://www.peacocktv.com/watch/home?orig_ref=direct'),
            ('amazon.com', PRIME_SIGNED_IN_NO_RAIL,
             'https://www.amazon.com/gp/video/storefront')):
        v, _d = guard.classify_auth(domain, html, url)
        check(v == 'signed_in',
              f'"manage profiles" in the {guard.site_label(domain)} nav does '
              f'not park it')


def test_other_platforms() -> None:
    print('\n== Disney+, Hulu, Netflix, Peacock ==')

    cases = [
        ('disneyplus.com', DISNEY_APP,
         'https://www.disneyplus.com/home', 'signed_in'),
        ('disneyplus.com', DISNEY_LOGIN,
         'https://www.disneyplus.com/identity/login/enter-email',
         'signed_out'),
        ('hulu.com', HULU_APP, 'https://www.hulu.com/hub/home', 'signed_in'),
        ('hulu.com', HULU_WELCOME, 'https://www.hulu.com/welcome',
         'signed_out'),
        ('netflix.com', NETFLIX_APP, 'https://www.netflix.com/browse',
         'signed_in'),
        ('netflix.com', NETFLIX_LOGIN,
         'https://www.netflix.com/login?nextpage=x', 'signed_out'),
        ('peacocktv.com', PEACOCK_APP,
         'https://www.peacocktv.com/watch/home', 'signed_in'),
        ('peacocktv.com', PEACOCK_MARKETING,
         'https://www.peacocktv.com/', 'signed_out'),
    ]
    for domain, html, url, want in cases:
        v, _d = guard.classify_auth(domain, html, url)
        check(v == want,
              f'{guard.site_label(domain)} {url.split("/")[-1] or "home"!r} '
              f'reads as {want}')


def test_upsell_does_not_demote() -> None:
    print('\n== An upsell strip on a working app page does not demote it ==')
    # Real app pages carry bundle and cancel-anytime copy in footers.
    # Checking rails BEFORE logged-out copy is what keeps this safe.
    html = _page(
        'Peacock. Continue Watching. Trending Now. Upgrade your plan. '
        'Cancel anytime. Choose your plan.')
    v, _d = guard.classify_auth('peacocktv.com', html,
                                'https://www.peacocktv.com/watch/home')
    check(v == 'signed_in',
          'a footer upsell does not turn a signed-in page into a wall')

    # But with no rails at all, that same copy IS the page.
    html = _page('Peacock. Upgrade your plan. Cancel anytime. '
                 'Choose your plan.')
    v, _d = guard.classify_auth('peacocktv.com', html,
                                'https://www.peacocktv.com/')
    check(v == 'signed_out', 'the same copy with no rails is a wall')


def test_script_bodies_are_ignored() -> None:
    print('\n== Marker matching ignores script and style bodies ==')
    html = ('<html><body><script>var experiments = '
            '{"choose your plan": true, "continue watching": true};'
            '</script><p>Continue Watching</p></body></html>')
    text = guard.page_text(html)
    check('experiments' not in text, 'script bodies are dropped')
    v, _d = guard.classify_auth('peacocktv.com', html,
                                'https://www.peacocktv.com/watch/home')
    check(v == 'signed_in', 'a JSON blob of variants does not decide it')


def test_unknown_is_a_refusal() -> None:
    print('\n== An unrecognised page is not evidence of a session ==')
    v, _d = guard.classify_auth('hbomax.com', _page('nothing familiar here'),
                                'https://play.hbomax.com/')
    check(v == 'unknown', 'an unrecognised page is unknown')
    raised = None
    try:
        guard.assert_signed_in('hbomax.com', _page('nothing familiar here'),
                               source='max', url='https://play.hbomax.com/')
    except guard.AuthWallError as e:
        raised = e
    check(raised is not None, 'unknown refuses rather than publishing')


def test_geo() -> None:
    print('\n== A non-US storefront is refused ==')
    html = _page('HBO Max. Continue Watching. Trending Now. '
                 'This title is not available in your country.')
    raised = None
    try:
        guard.assert_signed_in('hbomax.com', html, source='max',
                               url='https://play.hbomax.com/')
    except guard.GeoMismatchError as e:
        raised = e
    check(raised is not None,
          'a signed-in page serving another market still refuses')
    check(raised is not None and 'residential' in str(raised).lower(),
          'the failure says where the scraper has to run')
    check(guard.classify_geo(HBOMAX_APP)[0] == 'us',
          'an ordinary US page passes the geo check')


# ────────────────────────────────────────────────────────────────────
def test_donation_is_narrowed() -> None:
    print('\n== A donation carries one platform and no others ==')
    whole_browser = {
        'cookies': [
            {'name': 'st', 'value': 'TOKEN_A', 'domain': '.hbomax.com',
             'path': '/'},
            {'name': 'sess', 'value': 'TOKEN_B', 'domain': 'play.hbomax.com',
             'path': '/'},
            {'name': 'dsn', 'value': 'TOKEN_C', 'domain': '.disneyplus.com',
             'path': '/'},
            {'name': 'nfx', 'value': 'TOKEN_D', 'domain': '.netflix.com',
             'path': '/'},
        ],
        'origins': [
            {'origin': 'https://play.hbomax.com',
             'localStorage': [{'name': 'access', 'value': 'TOKEN_E'}],
             'indexedDB': [{'name': 'authdb'}]},
            {'origin': 'https://www.disneyplus.com',
             'localStorage': [{'name': 'access', 'value': 'TOKEN_F'}]},
        ],
    }
    hbo = dss.filter_state_for_domain(whole_browser, 'hbomax.com')
    names = {c['name'] for c in hbo['cookies']}
    check(names == {'st', 'sess'},
          'only the platform its own cookies are donated')
    check(all('disneyplus' not in (o['origin'])
              for o in hbo['origins']),
          "another platform's origin storage is left out")
    check(len(hbo['origins']) == 1, 'exactly the one origin comes across')

    disney = dss.filter_state_for_domain(whole_browser, 'disneyplus.com')
    check({c['name'] for c in disney['cookies']} == {'dsn'},
          'the same narrowing holds the other way round')

    blob = str(hbo)
    for token in ('TOKEN_C', 'TOKEN_D', 'TOKEN_F'):
        check(token not in blob,
              f'{token} from another platform is absent from the donation')


def test_redaction_never_leaks() -> None:
    print('\n== Diagnostics describe a session without reproducing it ==')
    state = {
        'cookies': [{'name': 'sess', 'value': 'SUPER_SECRET_TOKEN',
                      'domain': '.hbomax.com', 'path': '/'}],
        'origins': [{'origin': 'https://play.hbomax.com',
                      'localStorage': [{'name': 'refresh',
                                        'value': 'REFRESH_SECRET'}],
                      'indexedDB': [{'name': 'authdb'}]}],
    }
    summary = str(guard.redact_storage_state(state))
    line = guard.describe_storage_state(state)
    for secret in ('SUPER_SECRET_TOKEN', 'REFRESH_SECRET'):
        check(secret not in summary, f'{secret} is absent from the summary')
        check(secret not in line, f'{secret} is absent from the log line')
    check(guard.redact_storage_state(state)['cookies'] == 1,
          'the summary still counts the cookies')
    check(guard.redact_storage_state(state)['indexed_db_stores'] == 1,
          'the summary still counts the IndexedDB stores')
    check(guard.redact_storage_state(None)['cookies'] == 0,
          'an absent session summarises rather than raising')


def test_registration() -> None:
    print('\n== registration ==')
    for domain in dss.DEFAULT_STORAGE_DOMAINS:
        spec = guard.site_spec(domain)
        check(spec is not None, f'{domain} has auth markers')
        if not spec:
            continue
        check(bool(spec.get('app_url')), f'{domain} has a page to probe')
        check(domain in guard.session_hosts(domain),
              f'{domain} owns its own host')
        check(bool(spec.get('signed_in') or spec.get('signed_in_regex')),
              f'{domain} can prove a session')
        check(bool(spec.get('signed_out')), f'{domain} can prove a wall')

    # The loader has to be additive: cookie donation still works.
    from scripts.trends_scrapers import _base
    for fn in ('load_donated_cookies', 'load_donated_cookies_playwright',
               'cookie_donation_status', 'load_donated_storage_state',
               'storage_state_status'):
        check(hasattr(_base, fn), f'_base.{fn} is available')
    check(_base.S3_STORAGE_STATE_PREFIX != _base.S3_COOKIES_PREFIX,
          'sessions are stored apart from cookie donations')


def main() -> int:
    test_hbomax_refuses()
    test_prime_video_greeting()
    test_profile_choosers()
    test_other_platforms()
    test_upsell_does_not_demote()
    test_script_bodies_are_ignored()
    test_unknown_is_a_refusal()
    test_geo()
    test_donation_is_narrowed()
    test_redaction_never_leaks()
    test_registration()
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
