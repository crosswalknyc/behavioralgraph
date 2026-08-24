"""
Auto-login engine for Trends IQ cookie donation.

Signs into the streaming / retail sites using credentials from the
macOS Keychain (trends_login_store.py), harvests the resulting session
cookies, and donates them to S3 in the same format donate_cookies.py
uses - then kicks the post-donation refresh so the dashboard updates.

The robustness comes from a DEDICATED PERSISTENT browser profile
(a Chrome user-data-dir that survives across runs), not from
re-typing the password every time. You sign in once (assisted, so you
can clear the site's 2FA / CAPTCHA by hand in a visible window); after
that the profile keeps the session alive and every future run just
re-harvests and re-donates with zero interaction. If a session ever
dies and the site allows plain password re-login, the engine does that
automatically; if the site demands a texted/emailed code or a CAPTCHA
(Amazon, Microsoft/Xbox, Netflix, Disney+ routinely do), it stops and
emails the operator to run the one-time assisted sign-in again - it
NEVER hammers the login (one attempt per run) so accounts don't get
security-held.

Two modes
---------
    # One-time / occasional assisted sign-in (opens a VISIBLE browser;
    # auto-fills the stored credentials; you finish any 2FA / CAPTCHA
    # by hand). Do this once per site, or whenever a site logs you out.
    python3 -m scripts.trends_scrapers.trends_auto_login --setup netflix.com

    # Automated (headless) - reuse the persistent session, re-login with
    # the password only if it's a site that allows it, else email. This
    # is what the daily job runs.
    python3 -m scripts.trends_scrapers.trends_auto_login            # all stored
    python3 -m scripts.trends_scrapers.trends_auto_login --domains starz.com,mgmplus.com

Flags
-----
    --setup            headed, interactive assisted login (see above)
    --domains a,b      restrict to these domains (default: all stored)
    --dry-run          harvest to /tmp, don't upload / refresh / email
    --no-refresh       upload cookies but skip the scraper/dashboard refresh
    --no-email         don't send the operator email on a blocked login

macOS + Playwright + real Google Chrome required (same stack the
residential scrapers already use). Run from bg-webapp/ via `-m` so the
package-relative imports resolve.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from ._playwright import UA, _lazy_playwright, _try_stealth
from . import donate_cookies
from . import trends_login_store as store

logger = logging.getLogger("trends_auto_login")

# Dedicated persistent Chrome profile. Lives outside the repo, under
# the standard macOS app-support dir. Sessions signed in here survive
# across runs, which is what lets the daily job re-donate with no
# interaction after a single assisted sign-in.
_PROFILE_DIR = (Path.home() / "Library" / "Application Support"
                / "CrosswalkTrendsLogin" / "chrome-profile")

# How long the assisted (--setup) flow waits for you to finish a 2FA /
# CAPTCHA challenge in the visible window before giving up.
_SETUP_WAIT_S = 240
# Per-page settle budget after a submit click.
_SETTLE_MS = 6000


# ────────────────────────────────────────────────────────────────────
# Per-site login recipes.
#
# Each recipe is best-effort: site login DOM changes often, so success
# detection leans on generic signals (password field disappeared, URL
# left the login path, no challenge/error text) rather than brittle
# post-login selectors. `two_step` sites ask for the email, then reveal
# the password field after a "continue/next" click (Amazon, Microsoft).
# Domains with credentials but no recipe still work in --setup (we open
# login_url and you sign in by hand); automated mode emails for them.
# ────────────────────────────────────────────────────────────────────
_R = {
    "netflix.com": {
        "login_url": "https://www.netflix.com/login",
        "user_sel": ['input[name="userLoginId"]', 'input[type="email"]'],
        "pass_sel": ['input[name="password"]', 'input[type="password"]'],
        "submit_sel": ['button[data-uia="login-submit-button"]',
                       'button[type="submit"]'],
        "success_url_substr": ["/browse", "/profiles"],
    },
    "disneyplus.com": {
        "login_url": "https://www.disneyplus.com/login",
        "user_sel": ['input[type="email"]', 'input[name="email"]'],
        "continue_sel": ['button[data-testid="login-continue-button"]',
                         'button[type="submit"]'],
        "pass_sel": ['input[type="password"]', 'input[name="password"]'],
        "submit_sel": ['button[data-testid="password-continue-login"]',
                       'button[type="submit"]'],
        "two_step": True,
        "success_url_substr": ["/home", "/select-profile", "/browse"],
    },
    "hulu.com": {
        "login_url": "https://auth.hulu.com/web/login",
        "user_sel": ['input[name="email"]', 'input[type="email"]'],
        "pass_sel": ['input[name="password"]', 'input[type="password"]'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["/hub", "/home", "/welcome"],
    },
    "hbomax.com": {
        "login_url": "https://play.hbomax.com/signin",
        "user_sel": ['input[type="email"]', 'input[name="email"]'],
        "pass_sel": ['input[type="password"]', 'input[name="password"]'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["/home", "/browse"],
    },
    "amazon.com": {
        "login_url": ("https://www.amazon.com/ap/signin?openid.return_to="
                      "https://www.amazon.com/&openid.mode=checkid_setup&"
                      "openid.ns=http://specs.openid.net/auth/2.0"),
        "user_sel": ['input#ap_email', 'input[name="email"]',
                     'input[type="email"]'],
        "continue_sel": ['input#continue', '#continue'],
        "pass_sel": ['input#ap_password', 'input[name="password"]',
                     'input[type="password"]'],
        "submit_sel": ['input#signInSubmit', '#signInSubmit'],
        "two_step": True,
        "success_url_substr": ["amazon.com/?", "/gp/", "/ref="],
    },
    "music.amazon.com": {  # same Amazon auth as amazon.com
        "login_url": "https://music.amazon.com/",
        "user_sel": ['input#ap_email', 'input[type="email"]'],
        "continue_sel": ['input#continue', '#continue'],
        "pass_sel": ['input#ap_password', 'input[type="password"]'],
        "submit_sel": ['input#signInSubmit', '#signInSubmit'],
        "two_step": True,
        "success_url_substr": ["music.amazon.com"],
    },
    "audible.com": {  # Amazon auth
        "login_url": "https://www.audible.com/signin",
        "user_sel": ['input#ap_email', 'input[type="email"]'],
        "continue_sel": ['input#continue', '#continue'],
        "pass_sel": ['input#ap_password', 'input[type="password"]'],
        "submit_sel": ['input#signInSubmit', '#signInSubmit'],
        "two_step": True,
        "success_url_substr": ["audible.com/library", "audible.com/?",
                               "audible.com/home"],
    },
    "xbox.com": {  # Microsoft account (login.live.com), two-step + "stay signed in?"
        "login_url": "https://www.xbox.com/en-US/play",
        "user_sel": ['input[type="email"]', 'input#i0116'],
        "continue_sel": ['input#idSIButton9', '#idSIButton9',
                         'button[type="submit"]'],
        "pass_sel": ['input[type="password"]', 'input#i0118'],
        "submit_sel": ['input#idSIButton9', '#idSIButton9',
                       'button[type="submit"]'],
        "two_step": True,
        "success_url_substr": ["xbox.com/en-US/play", "xbox.com/play"],
    },
    "peacocktv.com": {
        "login_url": "https://www.peacocktv.com/signin",
        "user_sel": ['input[type="email"]', 'input[name="email"]'],
        "pass_sel": ['input[type="password"]', 'input[name="password"]'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["/browse", "/watch", "/account"],
    },
    "britbox.com": {
        "login_url": "https://www.britbox.com/us/account/signin",
        "user_sel": ['input[type="email"]', 'input[name="email"]',
                     'input[name="username"]'],
        "pass_sel": ['input[type="password"]', 'input[name="password"]'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["/us/home", "/us/account"],
    },
    "mgmplus.com": {
        "login_url": "https://www.mgmplus.com/login",
        "user_sel": ['input[type="email"]', 'input[name="email"]'],
        "pass_sel": ['input[type="password"]', 'input[name="password"]'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["/home", "/account", "/browse"],
    },
    "starz.com": {
        "login_url": "https://www.starz.com/us/en/login",
        "user_sel": ['input[type="email"]', 'input[name="email"]'],
        "pass_sel": ['input[type="password"]', 'input[name="password"]'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["/us/en", "/account"],
    },
    # Retailers + ticketing: aggressive bot walls, plain email/password
    # form. Recipes are generic; automated login often gets challenged,
    # so these usually rely on --setup + the persistent session.
    "target.com": {
        "login_url": "https://www.target.com/login",
        "user_sel": ['input#username', 'input[type="email"]'],
        "pass_sel": ['input#password', 'input[type="password"]'],
        "submit_sel": ['button#login', 'button[type="submit"]'],
        "success_url_substr": ["target.com/", "/account"],
    },
    "walmart.com": {
        "login_url": "https://www.walmart.com/account/login",
        "user_sel": ['input[type="email"]', 'input#email'],
        "pass_sel": ['input[type="password"]', 'input#password'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["walmart.com/", "/account"],
    },
    "etsy.com": {
        "login_url": "https://www.etsy.com/signin",
        "user_sel": ['input#join_neu_email_field', 'input[type="email"]'],
        "pass_sel": ['input#join_neu_password_field', 'input[type="password"]'],
        "submit_sel": ['button[type="submit"]', 'button[name="submit_attempt"]'],
        "success_url_substr": ["etsy.com/", "/your/"],
    },
    "sephora.com": {
        "login_url": "https://www.sephora.com/account/login",
        "user_sel": ['input[type="email"]', 'input#email'],
        "pass_sel": ['input[type="password"]', 'input#password'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["sephora.com/", "/account"],
    },
    "lululemon.com": {
        "login_url": "https://shop.lululemon.com/login",
        "user_sel": ['input[type="email"]', 'input#email'],
        "pass_sel": ['input[type="password"]', 'input#password'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["lululemon.com/", "/account"],
    },
    "bestbuy.com": {
        "login_url": "https://www.bestbuy.com/identity/signin",
        "user_sel": ['input#fld-e', 'input[type="email"]'],
        "pass_sel": ['input#fld-p1', 'input[type="password"]'],
        "submit_sel": ['button[type="submit"]', 'button.cia-form__controls__submit'],
        "success_url_substr": ["bestbuy.com/", "/account"],
    },
    "nike.com": {
        "login_url": "https://www.nike.com/login",
        "user_sel": ['input[type="email"]', 'input[name="emailAddress"]'],
        "pass_sel": ['input[type="password"]', 'input[name="password"]'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["nike.com/", "/member"],
    },
    "ulta.com": {
        "login_url": "https://www.ulta.com/account/signin",
        "user_sel": ['input[type="email"]', 'input#login-email'],
        "pass_sel": ['input[type="password"]', 'input#login-password'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["ulta.com/", "/account"],
    },
    "amctheatres.com": {
        "login_url": "https://www.amctheatres.com/sign-in",
        "user_sel": ['input[type="email"]', 'input[name="email"]'],
        "pass_sel": ['input[type="password"]', 'input[name="password"]'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["amctheatres.com/", "/account"],
    },
    "regmovies.com": {
        "login_url": "https://www.regmovies.com/account/sign-in",
        "user_sel": ['input[type="email"]', 'input[name="email"]'],
        "pass_sel": ['input[type="password"]', 'input[name="password"]'],
        "submit_sel": ['button[type="submit"]'],
        "success_url_substr": ["regmovies.com/", "/account"],
    },
}


# Which scraper source name to name in the operator email when a login
# is blocked. Falls back to the domain stem.
def _source_for(domain: str) -> str:
    try:
        from .refresh_after_donation import DOMAIN_REFRESH_MAP
        spec = DOMAIN_REFRESH_MAP.get(domain, {})
        mods = spec.get("local") or spec.get("runall") or []
        if mods:
            return mods[0]
    except Exception:
        pass
    return domain.split(".")[0]


# ────────────────────────────────────────────────────────────────────
# Detection helpers
# ────────────────────────────────────────────────────────────────────
_CHALLENGE_SELECTORS = [
    'input[autocomplete="one-time-code"]',
    'input[name*="otp" i]', 'input[id*="otp" i]',
    'input[name*="code" i]', 'input[id*="code" i]',
    'iframe[src*="recaptcha"]', 'iframe[src*="hcaptcha"]',
    'iframe[src*="arkoselabs"]', 'iframe[src*="funcaptcha"]',
    'iframe[title*="captcha" i]', 'iframe[src*="datadome"]',
    '#px-captcha', '[id*="captcha" i]',
]
_CHALLENGE_TEXT = [
    "verify it's you", "verify your identity", "one-time", "one time passcode",
    "authenticator", "approve the sign", "approve this request",
    "we sent a code", "we sent you a code", "enter the code", "enter code",
    "two-step", "two step verification", "verification code",
    "check your email", "check your phone", "confirm your identity",
    "solve this puzzle", "are you a robot", "unusual activity",
]
_ERROR_TEXT = [
    "password is incorrect", "incorrect password", "wrong password",
    "your password is incorrect", "doesn't match", "does not match",
    "couldn't sign you in", "cannot find an account", "we cannot find",
    "no account found", "invalid email or password",
    "that password is incorrect",
]


def _first_present(page, selectors, *, timeout_ms=4000):
    """Return the first selector (from the list) that attaches within the
    budget, or None."""
    per = max(500, timeout_ms // max(1, len(selectors)))
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=per, state="attached")
            return sel
        except Exception:
            continue
    return None


def _page_text(page) -> str:
    try:
        return (page.inner_text("body") or "").lower()
    except Exception:
        try:
            return (page.content() or "").lower()
        except Exception:
            return ""


def _challenge_reason(page) -> str | None:
    """Return a short reason string if a 2FA/CAPTCHA/verify wall is on
    the page, else None."""
    for sel in _CHALLENGE_SELECTORS:
        try:
            if page.query_selector(sel):
                return f"verification step detected ({sel})"
        except Exception:
            continue
    text = _page_text(page)
    for phrase in _CHALLENGE_TEXT:
        if phrase in text:
            return f"verification step detected ('{phrase}')"
    return None


def _error_present(page) -> bool:
    text = _page_text(page)
    return any(p in text for p in _ERROR_TEXT)


def _looks_logged_in(page, recipe) -> bool:
    """Heuristic: logged in if the URL reached a known post-login path,
    or there's no password field anywhere and we're off the login URL."""
    url = (page.url or "").lower()
    for sub in recipe.get("success_url_substr", []):
        if sub.lower() in url:
            # A success substring like "amazon.com/" also matches the
            # login page host, so pair it with "no password field".
            if not _has_password_field(page):
                return True
    login_url = (recipe.get("login_url") or "").lower()
    on_login = bool(login_url) and (url.split("?")[0] in login_url
                                    or "signin" in url or "/login" in url
                                    or "/ap/" in url)
    if not on_login and not _has_password_field(page):
        return True
    return False


def _has_password_field(page) -> bool:
    try:
        return page.query_selector('input[type="password"]') is not None
    except Exception:
        return False


# ────────────────────────────────────────────────────────────────────
# Cookie harvest
# ────────────────────────────────────────────────────────────────────
def _harvest(ctx, domain: str) -> list[dict]:
    """Pull cookies from the persistent context that apply to `domain`,
    mapped into donate_cookies' upload format."""
    out = []
    try:
        raw = ctx.cookies()
    except Exception as e:
        logger.warning("cookie harvest failed for %s: %s", domain, e)
        return out
    for c in raw:
        cdom = c.get("domain") or ""
        if not donate_cookies._cookie_applies_to_target(cdom, domain):
            continue
        exp = c.get("expires")
        out.append({
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": cdom,
            "path": c.get("path") or "/",
            "expires": int(exp) if exp and exp > 0 else None,
            "secure": bool(c.get("secure")),
            "httpOnly": bool(c.get("httpOnly")),
            "sameSite": c.get("sameSite") or "Lax",
        })
    return out


# ────────────────────────────────────────────────────────────────────
# Single-domain login attempt
# ────────────────────────────────────────────────────────────────────
def _fill_first(page, selectors, value) -> bool:
    sel = _first_present(page, selectors, timeout_ms=6000)
    if not sel:
        return False
    try:
        page.fill(sel, value)
        return True
    except Exception as e:
        logger.debug("fill %s failed: %s", sel, e)
        return False


def _click_first(page, selectors) -> bool:
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                el.click()
                return True
        except Exception:
            continue
    return False


def attempt_login(ctx, domain: str, recipe: dict, *, headed: bool) -> tuple[str, str]:
    """Try to end up logged in for `domain` in the given persistent
    context. Returns (status, detail). status is one of:
        'already'      - persistent session already valid, no login needed
        'success'      - password login (or assisted 2FA) succeeded
        'needs_human'  - a 2FA/CAPTCHA wall; a person must sign in once
        'auth_failed'  - credentials rejected (NOT retried)
        'no_recipe'    - domain has creds but no login recipe
        'error'        - unexpected failure
    """
    if not recipe or not recipe.get("login_url"):
        return "no_recipe", "no automated login recipe for this domain"

    page = ctx.new_page()
    _try_stealth(page)
    try:
        page.goto(recipe["login_url"], wait_until="domcontentloaded",
                  timeout=45000)
    except Exception as e:
        return "error", f"navigation failed: {e}"
    page.wait_for_timeout(2500)

    # 1) Persistent session already good? (No password field, off login.)
    if _looks_logged_in(page, recipe):
        return "already", "persistent session still valid"

    creds = store.get_credentials(domain)
    if not creds:
        # Only reachable in --setup where the caller opened the page for
        # a fully-manual sign-in.
        if headed:
            return _wait_for_human(page, recipe, "manual sign-in (no stored creds)")
        return "no_recipe", "no stored credentials"
    username, password = creds

    # 2) Fill the login form (single or two-step).
    if not _fill_first(page, recipe["user_sel"], username):
        # Some sites gate the email box behind a cookie/consent wall.
        reason = _challenge_reason(page)
        if reason and headed:
            return _wait_for_human(page, recipe, reason)
        return ("needs_human", reason or "could not find the username field")

    if recipe.get("two_step"):
        _click_first(page, recipe.get("continue_sel", []))
        page.wait_for_timeout(2500)

    # A challenge can appear right after the email step (Amazon/MS).
    reason = _challenge_reason(page)
    if reason:
        return _wait_for_human(page, recipe, reason) if headed \
            else ("needs_human", reason)

    if not _fill_first(page, recipe["pass_sel"], password):
        reason = _challenge_reason(page)
        if reason:
            return _wait_for_human(page, recipe, reason) if headed \
                else ("needs_human", reason)
        return "needs_human", "could not find the password field"

    _click_first(page, recipe["submit_sel"])
    page.wait_for_timeout(_SETTLE_MS)

    # 3) Classify the result. Order matters: a rejected password must be
    # caught BEFORE any retry (there is no retry - one attempt only).
    if _error_present(page):
        return "auth_failed", "the site rejected the stored password"
    reason = _challenge_reason(page)
    if reason:
        return _wait_for_human(page, recipe, reason) if headed \
            else ("needs_human", reason)
    # Give slow SPAs a moment to redirect to the logged-in view.
    for _ in range(6):
        if _looks_logged_in(page, recipe):
            return "success", "password login succeeded"
        page.wait_for_timeout(1500)
    # Last check for a challenge that rendered late.
    reason = _challenge_reason(page)
    if reason:
        return _wait_for_human(page, recipe, reason) if headed \
            else ("needs_human", reason)
    return "needs_human", "login did not reach a signed-in state"


def _wait_for_human(page, recipe, reason: str) -> tuple[str, str]:
    """(Headed --setup only.) Pause and let the operator finish the
    challenge in the visible window; poll until logged in or timeout."""
    print(f"    -> {reason}")
    print(f"    -> finish signing in in the browser window "
          f"(waiting up to {_SETUP_WAIT_S}s)...")
    deadline = time.time() + _SETUP_WAIT_S
    while time.time() < deadline:
        try:
            if _looks_logged_in(page, recipe):
                return "success", "assisted sign-in completed"
        except Exception:
            pass
        page.wait_for_timeout(2000)
    return "needs_human", "assisted sign-in timed out"


# ────────────────────────────────────────────────────────────────────
# Orchestration
# ────────────────────────────────────────────────────────────────────
def run(domains: list[str], *, headed: bool, dry_run: bool,
        do_refresh: bool, do_email: bool) -> int:
    sp = _lazy_playwright()
    if sp is None:
        print("Playwright is not installed. Install with:\n"
              "  pip3 install --break-system-packages playwright playwright-stealth\n"
              "  python3 -m playwright install-deps chromium")
        return 3

    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    donated: list[str] = []
    blocked: list[tuple[str, str, str]] = []   # (domain, status, detail)

    args = ["--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage", "--no-first-run",
            "--no-default-browser-check"]
    if not headed:
        args.append("--headless=new")

    with sp() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                str(_PROFILE_DIR), channel="chrome", headless=not headed,
                args=args, user_agent=UA,
                viewport={"width": 1440, "height": 900},
                locale="en-US", timezone_id="America/New_York",
            )
        except Exception as e:
            print(f"could not launch Chrome persistent profile: {e}")
            return 3

        for domain in domains:
            recipe = _R.get(domain)
            print(f"\n[{domain}] {'assisted setup' if headed else 'auto-login'} ...")
            try:
                status, detail = attempt_login(ctx, domain, recipe or {},
                                                headed=headed)
            except Exception as e:
                status, detail = "error", f"{type(e).__name__}: {e}"
            print(f"  {status}: {detail}")

            if status in ("already", "success"):
                cookies = _harvest(ctx, domain)
                if not cookies:
                    print(f"  ! signed in but harvested 0 cookies - skipping")
                    blocked.append((domain, "no_cookies",
                                    "signed in but no cookies to donate"))
                    continue
                try:
                    uri = donate_cookies._upload_to_s3(domain, cookies,
                                                       dry_run=dry_run)
                    print(f"  donated {len(cookies)} cookies -> {uri}")
                    donated.append(domain)
                except Exception as e:
                    print(f"  ! cookie upload failed: {e}")
            else:
                blocked.append((domain, status, detail))

        try:
            ctx.close()
        except Exception:
            pass

    # Email fallback for anything that needs a human. auth_failed emails
    # too (the stored password is wrong / expired), but never retries.
    if do_email and not dry_run and blocked:
        try:
            from .cookie_gap_notify import notify_cookie_gap
            for domain, status, detail in blocked:
                if status in ("no_cookies",):
                    continue
                notify_cookie_gap(
                    _source_for(domain), domain,
                    reason=(f"auto-login could not complete ({status}: "
                            f"{detail}). Run a one-time assisted sign-in: "
                            f"python3 -m scripts.trends_scrapers."
                            f"trends_auto_login --setup {domain}"),
                )
        except Exception as e:
            logger.info("cookie-gap email failed: %s", e)

    # Refresh the data for whatever we donated.
    if donated and do_refresh and not dry_run:
        try:
            donate_cookies._auto_refresh(donated)
        except Exception as e:
            logger.info("post-donation refresh failed: %s", e)

    print(f"\nAuto-login summary: {len(donated)} donated, "
          f"{len(blocked)} blocked/needs-setup.")
    if blocked:
        for domain, status, _ in blocked:
            print(f"  needs attention: {domain} ({status})")
    return 0 if donated or not domains else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="Trends IQ auto-login + cookie donation")
    ap.add_argument("--setup", action="store_true",
                    help="headed assisted sign-in (finish 2FA/CAPTCHA by hand)")
    ap.add_argument("--domains", default="",
                    help="comma-separated domains (default: all with stored creds)")
    ap.add_argument("positional", nargs="*",
                    help="domains as positional args (same as --domains)")
    ap.add_argument("--dry-run", action="store_true",
                    help="harvest to /tmp; no upload/refresh/email")
    ap.add_argument("--no-refresh", action="store_true",
                    help="upload cookies but skip the data refresh")
    ap.add_argument("--no-email", action="store_true",
                    help="don't email the operator on a blocked login")
    args = ap.parse_args(argv)

    if sys.platform != "darwin":
        print("trends_auto_login runs on the operator Mac (Keychain + "
              "residential IP).")
        return 3

    domains = [d.strip().lower() for d in
               (args.domains.split(",") if args.domains else []) if d.strip()]
    domains += [d.strip().lower() for d in args.positional if d.strip()]
    if not domains:
        domains = store.list_stored_domains()
    if not domains:
        print("No domains to process. Store credentials first:\n"
              "  python3 -m scripts.trends_scrapers.trends_login_store set <domain>")
        return 0

    return run(domains, headed=args.setup, dry_run=args.dry_run,
               do_refresh=not args.no_refresh, do_email=not args.no_email)


if __name__ == "__main__":
    sys.exit(main())
