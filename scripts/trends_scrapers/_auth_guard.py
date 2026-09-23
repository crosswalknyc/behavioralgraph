"""Content-identity checks for the session-gated scrapers.

Why this exists
---------------
The platforms in this file do not fail by erroring. They fail by
answering HTTP 200 with a complete, plausible, WRONG page.

  * HBO Max serves the full marketing site to an unauthenticated
    visitor: "Choose an HBO Max plan or bundle to start streaming",
    "Find Your Perfect Plan", the Basic With Ads / Standard / Premium
    cards. It has artwork, it has title text, it parses. What it does
    not have is a Top 10, a Trending Now or a Continue Watching rail,
    because those only exist for a signed-in account.
  * Disney+, Hulu and Prime Video all do the same thing in their own
    shape: a browse or storefront page that renders promotional
    titles to anyone.

A scraper that checks "did I get HTML" publishes that marketing page
as if it were a viewership chart. So the test has to be identity, not
success: does this page prove we are signed in, and is it the US?

This mirrors the guard already shipping on Plex Live TV, where
`epg.provider.plex.tv` answers 200 with a complete German lineup from
a datacenter address. Same class of defect, same answer: assert on
what the page IS, and refuse rather than publish when it cannot be
proven.

Vocabulary
----------
`classify_auth` returns one of three verdicts:

    signed_in   the page carries a rail that only exists for an
                authenticated account
    signed_out  the page carries a plan picker, a sign-in wall or a
                marketing hero. This is the dangerous one, because it
                is populated.
    unknown     neither set of markers matched. Treated as a refusal
                by `assert_signed_in`, because an unrecognised page is
                not evidence of a session.

Nothing in this module logs a token, a cookie value or a storage-state
value. `redact_storage_state` exists so diagnostics can describe a
session without printing one.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


class AuthWallError(RuntimeError):
    """Raised when a session-gated page cannot be proven signed in.

    Carrying its own type lets a caller distinguish "we were served the
    logged-out marketing page" from an ordinary parse miss, which is
    the whole point of the guard.
    """


class GeoMismatchError(RuntimeError):
    """Raised when a page proves it was served a non-US storefront."""


# ────────────────────────────────────────────────────────────────────
# Per-site markers
#
# signed_in: rails that ONLY exist for an authenticated account.
#   Phrases rather than CSS, because every one of these platforms
#   reshuffles its class names far more often than it renames
#   "Continue Watching".
#
#   Navigation words are deliberately absent. Measured 2026-09-23: the
#   logged-out Prime Video storefront renders "Watchlist" in its nav,
#   and the Peacock profile chooser renders "My Stuff" and "Channels"
#   in its. Either one as a signed_in marker would call a logged-out
#   page authenticated, which is the exact defect this file exists to
#   prevent. Only account-scoped rails qualify.
#
# signed_out: plan pickers, upsell heroes and sign-in walls.
#
# Checked in the order described on `classify_auth`, which is not the
# order they appear here. A marker matches case-insensitively against
# whitespace-collapsed page text, so it survives the Unicode isolate
# characters and ragged inter-tag whitespace these SPAs emit.
# ────────────────────────────────────────────────────────────────────
_SITES: dict[str, dict[str, Any]] = {
    'hbomax.com': {
        'label': 'HBO Max',
        'app_url': 'https://play.hbomax.com/',
        # Hosts whose cookies and origin storage belong to this
        # session. play.* issues the token the app actually uses;
        # auth.* is where the sign-in round trip lands.
        'hosts': ['hbomax.com', 'play.hbomax.com', 'auth.hbomax.com',
                  'www.hbomax.com'],
        'signed_in': ['continue watching', 'keep watching', 'my list',
                      'because you watched', 'jump back in', 'top 10',
                      'trending now'],
        'signed_out': ['choose an hbo max plan', 'find your perfect plan',
                       'basic with ads', 'start streaming',
                       'choose your plan', 'select your plan',
                       'sign up now', 'plans and pricing'],
    },
    'disneyplus.com': {
        'label': 'Disney+',
        'app_url': 'https://www.disneyplus.com/home',
        'hosts': ['disneyplus.com', 'www.disneyplus.com'],
        'signed_in': ['continue watching', 'keep watching',
                      'because you watched', 'recommended for you',
                      'my list'],
        'signed_out': ['start streaming', 'sign up now', 'get disney+',
                       'choose your plan', 'select your plan',
                       'bundle and save', 'start your subscription'],
    },
    'hulu.com': {
        'label': 'Hulu',
        'app_url': 'https://www.hulu.com/hub/home',
        'hosts': ['hulu.com', 'www.hulu.com', 'auth.hulu.com'],
        'signed_in': ['continue watching', 'keep watching',
                      'because you watched', 'jump back in',
                      'recommended for you'],
        'signed_out': ['start your free trial', 'select your plan',
                       'choose your plan', 'sign up now',
                       'switch to hulu', 'plans and pricing',
                       'get hulu', 'start watching free'],
    },
    'amazon.com': {
        'label': 'Prime Video',
        'app_url': 'https://www.amazon.com/gp/video/storefront',
        'hosts': ['amazon.com', 'www.amazon.com'],
        # The anonymous storefront renders a browsable catalog AND the
        # word "Watchlist" in its nav, so neither a title grid nor that
        # word is evidence. "Continue watching" is: it needs an
        # account. Verified against both pages on 2026-09-23.
        'signed_in': ['continue watching', 'because you watched',
                      'keep watching', 'your watchlist',
                      'purchases and rentals'],
        # An account with nothing part-watched has no Continue
        # Watching rail at all, so rails alone cannot answer this one.
        # Measured 2026-09-23: a signed-in storefront rendered 34,573
        # characters with no rail, and the only thing separating it
        # from the logged-out page was the nav greeting. Amazon writes
        # "Hello, sign in" when it does not know you and "Hello,
        # <name>" when it does, which is exactly the question.
        'signed_in_regex': [r'hello,\s+(?!sign\s*in\b)[a-z0-9]'],
        'signed_out': ['hello, sign in', 'sign in to your account',
                       'create your amazon account', 'new to amazon',
                       'enter your email or mobile'],
    },
    'netflix.com': {
        'label': 'Netflix',
        'app_url': 'https://www.netflix.com/browse',
        'hosts': ['netflix.com', 'www.netflix.com'],
        'signed_in': ['continue watching', 'my list', 'top 10',
                      'because you watched', 'keep watching',
                      'new on netflix'],
        'signed_out': ['unlimited movies, tv shows', 'join now',
                       'finish sign up', 'ready to watch?',
                       'restart your membership'],
    },
    'peacocktv.com': {
        'label': 'Peacock',
        'app_url': 'https://www.peacocktv.com/watch/home',
        'hosts': ['peacocktv.com', 'www.peacocktv.com'],
        'signed_in': ['continue watching', 'keep watching',
                      'because you watched', 'trending now'],
        'signed_out': ['choose your plan', 'start watching free',
                       'sign up now', 'pick your plan',
                       'choose a peacock plan', 'plans and pricing'],
    },
    'starz.com': {
        'label': 'Starz',
        'app_url': 'https://www.starz.com/us/en/',
        'hosts': ['starz.com', 'www.starz.com'],
        'signed_in': ['continue watching', 'my list', 'keep watching',
                      'because you watched'],
        'signed_out': ['start your subscription', 'sign up now',
                       'choose your plan', 'get starz'],
    },
    'mgmplus.com': {
        'label': 'MGM+',
        'app_url': 'https://www.mgmplus.com/',
        'hosts': ['mgmplus.com', 'www.mgmplus.com'],
        'signed_in': ['continue watching', 'my list', 'keep watching',
                      'because you watched'],
        'signed_out': ['start your free trial', 'sign up now',
                       'choose your plan', 'subscribe now'],
    },
    'britbox.com': {
        'label': 'BritBox',
        'app_url': 'https://www.britbox.com/us/home',
        'hosts': ['britbox.com', 'www.britbox.com'],
        'signed_in': ['continue watching', 'my list', 'keep watching',
                      'because you watched'],
        'signed_out': ['start your free trial', 'sign up now',
                       'choose your plan', 'subscribe now'],
    },
    'music.amazon.com': {
        'label': 'Amazon Music',
        # The bare home page never paints a rail in headless Chrome,
        # so it cannot prove anything either way. The podcasts browse
        # page is what the scrapers actually read and it hydrates, so
        # it is the honest probe.
        'app_url': 'https://music.amazon.com/podcasts',
        'hosts': ['music.amazon.com', 'amazon.com'],
        'signed_in': ['recently played', 'continue listening',
                      'made for you', 'your library', 'your playlists',
                      'top podcasts', 'popular podcasts',
                      'browse podcasts', 'trending podcasts'],
        'signed_in_regex': [r'hello,\s+(?!sign\s*in\b)[a-z0-9]'],
        'signed_out': ['hello, sign in', 'sign in to your account',
                       'create your amazon account', 'new to amazon',
                       'stream music and podcasts free'],
    },
    'audible.com': {
        'label': 'Audible',
        'app_url': 'https://www.audible.com/',
        'hosts': ['audible.com', 'www.audible.com', 'amazon.com'],
        'signed_in': ['your library', 'continue listening',
                      'your credits', 'wish list'],
        'signed_in_regex': [r'hello,\s+(?!sign\s*in\b)[a-z0-9]'],
        'signed_out': ['hello, sign in', 'sign in to your account',
                       'new to amazon', 'create your amazon account'],
    },
    'xbox.com': {
        'label': 'Xbox Game Pass',
        'app_url': 'https://www.xbox.com/en-US/play',
        'hosts': ['xbox.com', 'www.xbox.com'],
        'signed_in': ['recently played', 'jump back in',
                      'continue playing', 'my library'],
        'signed_out': ['sign in to play', 'join game pass',
                       'choose your plan', 'start your free trial'],
    },
}

# A URL that proves the session was rejected. Strongest signal there
# is, because it is the platform's own verdict rather than our reading
# of its copy. HBO Max is the clearest: it bounces an unauthenticated
# visitor to `www.hbomax.com/?reason=anonymous` and then serves a
# complete marketing site at HTTP 200.
_SIGNED_OUT_URL_MARKERS = (
    'reason=anonymous', '/login', '/signin', '/sign-in', '/sign_in',
    '/enter-email', '/welcome', '/ap/signin', '/identity/login',
    '/auth/login', '/signup', '/sign-up', '/subscribe', '/plans',
    '/getstarted', '/get-started',
)

# A URL that proves the session is GOOD but parked on a household
# profile chooser. Authenticated, but no rails render until a profile
# is picked, so a scraper waiting on a rail selector sits there and
# times out. Same wall `_amazon_music` already clears for Amazon
# Music; Hulu, Netflix, Disney+ and Peacock all have their own.
_INTERSTITIAL_URL_MARKERS = (
    '/profiles', 'who-is-listening', '/select-profile', '/whoswatching',
    '/identity/who',
)

# Text form of the same chooser.
#
# "manage profiles" is deliberately NOT here. Measured 2026-09-23: it
# renders in the nav of the signed-in Netflix browse page, the
# signed-in Peacock home and the signed-in Prime Video storefront, so
# treating it as a chooser marker would park three working platforms.
_INTERSTITIAL_TEXT_MARKERS = (
    "who's watching", 'who is watching', 'choose a profile',
    'choose an existing profile', 'who is listening', 'select a profile',
    'switch profile',
)

# A storefront that names a country other than the US is the Plex
# failure in another costume: a full lineup for the wrong market.
_NON_US_MARKERS = (
    'this title is not available in your', 'not available in your country',
    'not available in your region', 'currently unavailable in your location',
    'sorry, this content is not available in your',
)


def known_domains() -> list[str]:
    """Every domain this module can adjudicate, in table order."""
    return list(_SITES)


def site_spec(domain: str) -> Optional[dict]:
    return _SITES.get((domain or '').lower())


def site_label(domain: str) -> str:
    spec = site_spec(domain)
    return spec['label'] if spec else domain


def app_url(domain: str) -> Optional[str]:
    spec = site_spec(domain)
    return spec.get('app_url') if spec else None


def session_hosts(domain: str) -> list[str]:
    """Hosts whose cookies and origin storage belong to this session.

    Used to narrow a whole-browser storage state down to one platform,
    so a donation for one service never carries another service's
    tokens.
    """
    spec = site_spec(domain)
    if spec and spec.get('hosts'):
        return list(spec['hosts'])
    return [domain]


# ────────────────────────────────────────────────────────────────────
# Classification
# ────────────────────────────────────────────────────────────────────
_TAG_RE = re.compile(r'<(script|style|noscript)\b.*?</\1>',
                     re.IGNORECASE | re.DOTALL)
_STRIP_TAGS_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')
# The isolate / directional marks HBO Max wraps its aria labels in.
_ISOLATES_RE = re.compile(r'[\u2066-\u2069\u200e\u200f\u061c]')


def page_text(html: str) -> str:
    """Flatten HTML to lowercase, whitespace-collapsed visible-ish text.

    Script and style bodies are dropped first. Without that, a marker
    like "choose your plan" matches a JSON blob of every experiment
    variant the page might render, and every page looks logged out.
    """
    if not html:
        return ''
    body = _TAG_RE.sub(' ', html)
    body = _STRIP_TAGS_RE.sub(' ', body)
    body = _ISOLATES_RE.sub('', body)
    return _WS_RE.sub(' ', body).lower()


def _matched(text: str, markers: Iterable[str]) -> list[str]:
    return [m for m in markers if m in text]


def classify_auth(domain: str, html: str,
                  url: Optional[str] = None) -> tuple[str, str]:
    """Return `(verdict, detail)` for a rendered page.

    verdict is 'signed_in', 'signed_out', 'interstitial' or 'unknown'.
    `detail` names the evidence that decided it, so a failure report
    says what it actually saw rather than asserting a cause.

    Pass `url` whenever you have it. Where a platform lands is its own
    verdict on the session and it is far more stable than its copy:
    `?reason=anonymous`, `/identity/login/enter-email`, `/welcome` and
    `/login` each settle the question outright.

    The order is load-bearing:

      1. Chooser URL. A signed-in account parked on a profile picker
         must not be read as logged out just because the picker is
         thin, and Hulu's picker sits at `/profiles?next=/hub/home`.
      2. Logged-out URL. The platform's own redirect.
      3. Chooser text.
      4. Signed-in rails. These come BEFORE the logged-out copy check
         so that an upsell strip in the footer of a working app page
         cannot demote it. A real app has rails; a marketing page has
         no "Continue Watching", which is what makes this safe.
      5. Logged-out copy.
    """
    spec = site_spec(domain)
    if not spec:
        return 'unknown', f'no auth markers registered for {domain}'

    u = (url or '').lower()
    if u:
        hit = next((m for m in _INTERSTITIAL_URL_MARKERS if m in u), None)
        if hit:
            return 'interstitial', f'parked on a profile chooser ({hit!r} in URL)'
        hit = next((m for m in _SIGNED_OUT_URL_MARKERS if m in u), None)
        if hit:
            return 'signed_out', f'redirected to a sign-in wall ({hit!r} in URL)'

    text = page_text(html)
    if not text:
        return 'unknown', 'empty page'

    mid_hits = _matched(text, _INTERSTITIAL_TEXT_MARKERS)
    if mid_hits:
        return 'interstitial', ('profile chooser: '
                                + ', '.join(repr(h) for h in mid_hits[:3]))

    in_hits = _matched(text, spec.get('signed_in') or [])
    if in_hits:
        return 'signed_in', ('signed-in rails: '
                             + ', '.join(repr(h) for h in in_hits[:4]))

    for pattern in spec.get('signed_in_regex') or []:
        m = re.search(pattern, text)
        if m:
            # The match can contain an account holder's name, so it is
            # summarised rather than quoted.
            return 'signed_in', 'the page greets a signed-in account'

    out_hits = _matched(text, spec.get('signed_out') or [])
    if out_hits:
        return 'signed_out', ('logged-out page: '
                              + ', '.join(repr(h) for h in out_hits[:4]))

    return 'unknown', ('no signed-in rail and no plan picker found in '
                       f'{len(text)} chars of page text')


def classify_geo(html: str) -> tuple[str, str]:
    """Return `('us', ...)` or `('non_us', reason)`.

    Only a positive non-US signal demotes a page. Absence of evidence
    is not evidence of the wrong market, and these storefronts rarely
    name the US explicitly when they are serving it.
    """
    text = page_text(html)
    hits = _matched(text, _NON_US_MARKERS)
    if hits:
        return 'non_us', f'non-US storefront: {hits[0]!r}'
    return 'us', 'no non-US storefront markers'


def assert_signed_in(domain: str, html: str, *, source: str,
                     url: Optional[str] = None) -> str:
    """Refuse to continue unless `html` proves an authenticated US page.

    Returns the detail string on success. Raises `AuthWallError` or
    `GeoMismatchError` otherwise, so a caller publishes nothing rather
    than publishing a marketing page as a chart.
    """
    verdict, detail = classify_auth(domain, html, url)
    label = site_label(domain)

    if verdict == 'signed_in':
        geo, geo_detail = classify_geo(html)
        if geo != 'us':
            raise GeoMismatchError(
                f'{source}: {label} served a non-US page ({geo_detail}). '
                f'Run this scraper from the residential Mac, or point the '
                f'residential proxy at a US exit.')
        return detail

    if verdict == 'signed_out':
        raise AuthWallError(
            f'{source}: {label} served the logged-out page, not the app '
            f'({detail}). Nothing was published, because that page carries '
            f'promotional titles that would read as a chart. Re-authorize '
            f'with: python3 scripts/trends_scrapers/donate_cookies.py '
            f'--login {domain}')

    if verdict == 'interstitial':
        raise AuthWallError(
            f'{source}: {label} stopped on an account chooser rather than '
            f'the app ({detail}). Re-authorize and pick the profile once '
            f'with: python3 scripts/trends_scrapers/donate_cookies.py '
            f'--login {domain}')

    raise AuthWallError(
        f'{source}: could not prove {label} is signed in ({detail}). '
        f'Nothing was published. Re-authorize with: python3 '
        f'scripts/trends_scrapers/donate_cookies.py --login {domain}')




# ────────────────────────────────────────────────────────────────────
# Browser-side helpers
# ────────────────────────────────────────────────────────────────────
# These take a live Playwright page. They live here rather than in the
# donation module because `render_pages` needs exactly the same two
# behaviours before it will trust a page: clear a household profile
# chooser, and wait long enough for a rail to paint before judging.
# One implementation, so a scraper and a donation can never disagree
# about whether a platform is signed in.
def _judge_page(page, domain: str) -> tuple[str, str]:
    try:
        html = page.content() or ''
    except Exception:
        html = ''
    try:
        url = page.url or ''
    except Exception:
        url = ''
    return classify_auth(domain, html, url)

# ────────────────────────────────────────────────────────────────────
# Household profile choosers
# ────────────────────────────────────────────────────────────────────
# Every one of these platforms now interposes a "who's watching"
# screen on a signed-in account. It answers HTTP 200 on the app shell
# and renders no rails, so a scraper waiting on a rail selector sits
# there until its timeout and then reports a dead session. The session
# is fine; it never got past the chooser.
#
# Clearing it HERE rather than in each scraper is the whole advantage
# of donating a storage state: picking a profile writes the profile
# cookie into the context, so the captured session is already past the
# chooser and every scraper that restores it lands on the home rails.
#
# `_amazon_music.dismiss_profile_picker` already solved this for
# Amazon Music. That one is delegated to rather than reimplemented.
_CHOOSER_SKIP_LABELS = {
    'add profile', 'add a new one', 'manage profiles', 'done', 'cancel',
    'edit', 'kids', 'guest', 'sign out', 'log out', 'account', 'help',
    'exit', 'back', 'skip', 'continue', 'next', 'previous',
}


def dismiss_profile_chooser(page, domain: str, *,
                            settle_ms: int = 7000) -> tuple[bool, str]:
    """Pick the first real profile so the app renders. Never raises."""
    if domain == 'music.amazon.com':
        try:
            from ._amazon_music import dismiss_profile_picker
            return dismiss_profile_picker(page, settle_ms=settle_ms)
        except Exception as e:
            return False, f'amazon-music chooser helper failed: {e}'

    try:
        labels = page.evaluate("""() => {
            const out = [];
            const sel = 'button, a, [role="button"], [data-testid*="profile" i]';
            document.querySelectorAll(sel).forEach((el) => {
                const t = (el.innerText || el.getAttribute('aria-label') || '')
                    .trim();
                if (t && t.length <= 40) out.push(t);
            });
            return out;
        }""") or []
    except Exception as e:
        return False, f'could not read the chooser: {e}'

    seen: set[str] = set()
    candidates: list[str] = []
    for t in labels:
        key = t.strip().lower()
        if key in _CHOOSER_SKIP_LABELS or key in seen:
            continue
        seen.add(key)
        candidates.append(t.strip())

    if not candidates:
        return False, f'no profile to pick (saw {labels[:6]})'

    for label in candidates[:4]:
        try:
            loc = page.get_by_text(label, exact=True)
            if loc.count() == 0:
                continue
            loc.first.click(timeout=5000)
            page.wait_for_timeout(settle_ms)
            verdict, _detail = _judge(page, domain)
            if verdict != 'interstitial':
                return True, f'picked profile {label!r}'
        except Exception:
            continue
    return False, f'could not clear the chooser (tried {candidates[:3]})'


def settle_and_judge(page, domain: str, *, settle_ms: int = 4000,
            budget_ms: int = 40000) -> tuple[str, str]:
    """Land on a page we can judge, then judge it.

    Polls rather than sampling once. Measured 2026-09-23: Hulu and
    Amazon Music both accept the session and route to the real app
    URL, then take well past ten seconds to paint a rail. Reading them
    at a fixed ten seconds returned "unknown" for two platforms that
    were signed in the whole time, which would have held a good
    donation and sent an operator to fix a session that worked.

    Verdicts are not equally final, so they end the poll differently:

      signed_in   final, nothing better is coming
      signed_out  final, a sign-in wall does not become a home page
      interstitial not final, clear the chooser and keep looking
      unknown     not final, this is what un-hydrated looks like
    """
    deadline = time.time() + (budget_ms / 1000.0)
    verdict, detail = 'unknown', 'not yet rendered'
    dismissals = 0

    page.wait_for_timeout(settle_ms)
    while True:
        verdict, detail = _judge_page(page, domain)
        if verdict in ('signed_in', 'signed_out'):
            return verdict, detail

        if verdict == 'interstitial' and dismissals < 2:
            dismissals += 1
            ok, note = dismiss_profile_chooser(page, domain)
            logger.info('%s: profile chooser, %s', domain, note)
            if ok:
                # Re-request what we actually asked for. The chooser's
                # returnTo does not always land there.
                try:
                    page.goto(app_url(domain),
                              wait_until='domcontentloaded', timeout=60000)
                    page.wait_for_timeout(settle_ms)
                except Exception:
                    pass
                continue

        if time.time() >= deadline:
            return verdict, detail

        # A nudge down the page starts the lazy rails these SPAs hold
        # back until something looks at them.
        try:
            page.mouse.wheel(0, 1200)
        except Exception:
            pass
        page.wait_for_timeout(2500)



def prove_signed_in(page, domain: str, *, source: str) -> str:
    """Navigate to the platform's app home and prove the session there.

    This is the pre-flight a session-gated scraper runs ONCE, before
    it renders anything it means to parse. Returns the evidence on
    success and raises `AuthWallError` or `GeoMismatchError` otherwise.

    Why the app home specifically, and not each page the scraper
    wants: the rails that prove a session live on the home view. A
    browse or genre page is signed-in content but carries no Continue
    Watching and no plan picker, so asking it to prove the session
    fails every time. Measured 2026-09-23: asserting per page turned
    Disney+ `browse`, Hulu `Movies` and Prime Video `Explore` into
    refusals while all three sessions were good. The session is a
    property of the browser context, so proving it once proves it for
    every page rendered in that context.
    """
    target = app_url(domain)
    if not target:
        raise AuthWallError(f'{source}: no app page registered for {domain}')
    page.goto(target, wait_until='domcontentloaded', timeout=60000)
    verdict, detail = settle_and_judge(page, domain)
    html = ''
    try:
        html = page.content() or ''
    except Exception:
        pass
    if verdict == 'signed_in':
        geo, geo_detail = classify_geo(html)
        if geo != 'us':
            raise GeoMismatchError(
                f'{source}: {site_label(domain)} served a non-US page '
                f'({geo_detail}). Run this scraper from the residential '
                f'Mac, or point the residential proxy at a US exit.')
        return detail
    # Re-raise through the shared message so every refusal reads the
    # same and names the same one command.
    return assert_signed_in(domain, html, source=source, url=page.url)


def refuse_if_signed_out(page, domain: str, *, source: str) -> None:
    """Per-page backstop once the session has already been proven.

    Deliberately weaker than `prove_signed_in`. A deep page cannot be
    asked to prove a session, but it CAN be caught being a sign-in
    wall, which is what a session dying mid-run looks like. So only a
    definite verdict refuses here; 'unknown' is the normal reading of
    a browse page and passes.
    """
    verdict, detail = _judge_page(page, domain)
    if verdict in ('signed_out', 'interstitial'):
        raise AuthWallError(
            f'{source}: {site_label(domain)} dropped to a sign-in wall '
            f'partway through ({detail}). Nothing was published. '
            f'Re-authorize with: python3 scripts/trends_scrapers/'
            f'donate_cookies.py --login {domain}')


# ────────────────────────────────────────────────────────────────────
# Redaction
# ────────────────────────────────────────────────────────────────────
def redact_storage_state(state: Optional[dict]) -> dict:
    """Describe a storage state without reproducing any of it.

    A storage state is more sensitive than a cookie jar: it can carry
    access and refresh tokens in IndexedDB and localStorage. Every
    diagnostic path in this package goes through here, so a log line
    or an operator email can say how much session there is without
    ever printing a value.
    """
    if not state:
        return {'cookies': 0, 'origins': 0, 'local_storage_keys': 0,
                'indexed_db_stores': 0}
    origins = state.get('origins') or []
    ls_keys = 0
    idb = 0
    for origin in origins:
        ls_keys += len(origin.get('localStorage') or [])
        idb += len(origin.get('indexedDB') or [])
    return {
        'cookies': len(state.get('cookies') or []),
        'origins': len(origins),
        'origin_hosts': sorted({(o.get('origin') or '') for o in origins}),
        'local_storage_keys': ls_keys,
        'indexed_db_stores': idb,
    }


def describe_storage_state(state: Optional[dict]) -> str:
    """One-line, value-free summary for a log or a status table."""
    r = redact_storage_state(state)
    return (f"{r['cookies']} cookies, {r['origins']} origins, "
            f"{r['local_storage_keys']} local-storage keys, "
            f"{r['indexed_db_stores']} IndexedDB stores")
