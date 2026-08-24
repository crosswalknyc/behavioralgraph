"""
Cookie donation CLI - run this on your LAPTOP (macOS) to hand a valid
logged-in session to the Hetzner scrapers.

Why this exists
---------------
Retailer and social sites (Target, Walmart, Etsy, Sephora, Instagram, ...)
ship aggressive bot detection (DataDome, PerimeterX, Akamai). From a
Hetzner datacenter IP with no cookies, they either serve a captcha or
return an empty shell. From your laptop in Chrome, you already have a
valid, un-captcha'd session because you've browsed the site normally.

This script reads those Chrome cookies (via the OS keychain), packages
them into a JSON blob per domain, and uploads them to a private S3
prefix. Scrapers on Hetzner pull the latest donation and inject the
cookies into curl_cffi and Playwright before hitting the target. That's
enough to punch through most detection stacks.

Usage
-----
    # Donate cookies for every domain in DEFAULT_DOMAINS
    python3 scripts/trends_scrapers/donate_cookies.py

    # Donate a specific domain (or several)
    python3 scripts/trends_scrapers/donate_cookies.py target.com etsy.com

    # Use a non-default Chrome profile
    python3 scripts/trends_scrapers/donate_cookies.py --profile "Profile 1"

    # Dry-run (write to /tmp instead of S3)
    python3 scripts/trends_scrapers/donate_cookies.py --dry-run

After a successful donation the script automatically refreshes the
data for whatever the donated domain feeds: residential-only scrapers
re-run right here in this terminal, Hetzner-run sources are kicked off
over SSH in the background, and the dashboard cache is purged + the
default view re-warmed so the new data shows on the next page load.
Pass --no-refresh to skip that (the daily residential batch does,
since it runs every scraper itself right after donating).

Setup (one-time)
----------------
    pip3 install --user --break-system-packages browser-cookie3 boto3

macOS will prompt Keychain Access for "Chrome Safe Storage" the first
time - click "Always Allow" once and it's silent thereafter.

Security
--------
Cookies are live logged-in sessions. They land in
`s3://dashboard-inputs/trends_iq_cookies/{domain}.json` with
server-side AES256 encryption. The bucket is private. Rotate anything
you'd consider sensitive by logging out and back in on that domain,
which invalidates the donated session.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


# Domains we want donations for. Order = scraper priority (Instagram first
# because it's the highest-friction to re-establish; retailers next; then
# streaming services). ESPN+ and HBO Max/Max come through the Disney+ and
# Hulu bundles respectively - donating disneyplus.com covers ESPN+, and
# donating hulu.com covers Max on the bundle plan.
DEFAULT_DOMAINS = [
    # Social - content-focused scrapers (2026-07 switch: hashtags -> real
    # posts/videos/tweets). All three need a logged-in session because
    # the public explore/discover pages hide the good content behind
    # walls for anonymous visitors.
    'instagram.com',   # instagram.com/explore trending posts
    'tiktok.com',      # tiktok.com/discover trending videos (public /discover)
    'x.com',           # x.com/explore trending tweets (requires login)
    # TikTok Creative Center gates its trending hashtag list behind login
    # as of 2026-07: anonymous visitors see 3 preview cards + a "Log in"
    # CTA. Donating a Creative-Center-logged-in session (visit
    # https://ads.tiktok.com/business/creativecenter/inspiration/popular/
    # hashtag/pc/en and click "Log in" once, then run this) unlocks the
    # full 20-hashtag weekly list. Any TikTok/Business account works.
    'ads.tiktok.com',
    'target.com',
    'walmart.com',
    'etsy.com',
    'sephora.com',
    'lululemon.com',
    'bestbuy.com',
    'nike.com',
    'ulta.com',
    # Streaming - top 6 services. Bundle notes:
    #   - disneyplus.com session unlocks Disney+ and ESPN+
    #   - hulu.com session unlocks Hulu and Max (on bundle plan)
    'netflix.com',     # 2026-07: switched from weekly TSV to authenticated daily
    'disneyplus.com',
    'hulu.com',
    # HBO Max reverted from the max.com rebrand back to hbomax.com in
    # 2025. Fresh Chrome cookies now sit under `.hbomax.com` /
    # `play.hbomax.com`; the old `max.com` origin no longer serves
    # the app shell. Sign in at play.hbomax.com to get a valid session.
    'hbomax.com',
    'amazon.com',      # for Prime Video (same session as amazon shopping)
    # ESPN+ intentionally omitted - programming lives at
    # disneyplus.com/browse/espn (Disney bundle), so the disneyplus.com
    # cookie already covers it. Add 'plus.espn.com' here only if you
    # ever want to scrape the standalone ESPN+ web player.
    #
    # 2026-07-22: Peacock added for the Microdramas IQ module. The
    # peacocktv.com hub is paid-only, so a donated logged-in session
    # is required for the microdrama hub rails to render.
    'peacocktv.com',
    # 2026-07-22: ReelShort + DramaBox added for the Microdramas IQ
    # Competitors tab. Both apps expose a public top-chart web page,
    # but they anti-bot the datacenter IP heavily; donated cookies +
    # residential proxy gets us past that.
    'reelshort.com',
    'dramabox.com',
    # 2026-07-23: Podcasts / Books / Music new-source domains. All
    # three ship a React shell with zero inlined chart data for
    # anonymous visitors, so a logged-in session is required for the
    # Playwright follow-ups to reach the top-list rails.
    #   - music.amazon.com: Amazon Music Top 200 + Amazon Music Podcasts
    #     rails (same amazon.com session works for both)
    #   - audible.com: Audible best-sellers + Audible Podcasts. Amazon
    #     account with an Audible membership is enough.
    #   - open.spotify.com: Spotify Podcast Charts (their marketing
    #     charts endpoint currently 500s for anonymous clients; a fully-
    #     authed session lets us hit the Web API charts endpoint).
    'audible.com',
    'open.spotify.com',
    # 2026-07-28: Film-ticketing domains added for the Films tab. AMC
    # and Regal both use Akamai bot-detection that 403s anonymous
    # Playwright fingerprints; donated cookies from a signed-in laptop
    # Chrome session carry the passed-captcha token so daily scrapes
    # can reach the /movies rail. Fandango + Cinemark work without
    # cookies from a residential IP, so only these two are gated.
    'amctheatres.com',
    'regmovies.com',
    # 2026-08-20: BritBox + MGM+ added to the Streaming tab. BritBox
    # uses a real `bbuser` session cookie so an authenticated donation
    # unlocks the full trending-rail catalog on /us/home. MGM+ works
    # anonymously (their aws-waf-token is enough to slip past the WAF
    # challenge in headless Chrome), but if a full login is ever
    # required later the same donate flow captures it.
    'britbox.com',
    'mgmplus.com',
    # 2026-08-20: Starz added to Streaming tab. Auth session isn't
    # strictly required (public browse catalog ships in-DOM as
    # __NEXT_DATA__), but a donated session slips past their Segment
    # / Rokt analytics throttling faster.
    'starz.com',
    # 2026-08-20: Xbox Game Pass Ultimate added to new Gaming tab.
    # /en-US/play ships the full XGPU catalog inline as
    # window.__PRELOADED_STATE__. Cookies pull in the real XToken
    # session (`XBXXtkhttp%3A%2F%2Fxboxlive.com` etc.) so Adobe /
    # Clarity analytics don't throttle us during hydration.
    'xbox.com',
]

S3_BUCKET = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_PREFIX = 'trends_iq_cookies/'

# Where the datacenter-side scrapers live. After a donation for a
# Hetzner-run source (retailers, Prime Video, music/podcast/book
# charts) we SSH here and kick a detached refresh so the data updates
# within minutes instead of waiting for the next cron.
HETZNER_HOST     = os.environ.get('TRENDS_HETZNER_HOST', 'root@168.119.215.48')
HETZNER_REPO     = '/root/finished_codes/bg-webapp'
HETZNER_ENV_FILE = '/root/finished_codes/.env.trends_scrapers'


def _load_refresh_module():
    """Load refresh_after_donation.py from this directory via importlib
    so this script keeps working when run as a plain file (no package
    on sys.path)."""
    import importlib.util
    path = Path(__file__).resolve().parent / 'refresh_after_donation.py'
    spec = importlib.util.spec_from_file_location('refresh_after_donation', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _auto_refresh(donated_domains: list[str]) -> None:
    """Re-scrape whatever the donated domains feed, right now.

    Residential-only scrapers run locally in this terminal (the donated
    cookies came from this machine's Chrome, and these sites IP-block
    the datacenter anyway). Hetzner-run sources are triggered over SSH
    as a detached job so slow retailer warm-ups don't block the
    operator. Both paths end by purging today's live dashboard cache
    and re-warming the default view, so the dashboard shows the fresh
    data on the next load.
    """
    import subprocess

    try:
        refresh = _load_refresh_module()
    except Exception as e:
        print(f"auto-refresh unavailable ({e}); data will update on the "
              f"next scheduled run.")
        return

    local_mods, runall_sources, unmapped = refresh.plan_refresh(donated_domains)
    for domain in unmapped:
        print(f"  {domain:<20s} no auto-refresh target - picked up on the "
              f"next scheduled run")
    if not local_mods and not runall_sources:
        return

    repo_root = Path(__file__).resolve().parents[2]

    # Hetzner first (fire-and-forget) so it works in parallel with the
    # local scrapers below. The remote chain re-purges + re-warms when
    # it finishes, so it always gets the last word on the cache.
    if runall_sources:
        remote_cmd = (
            f"cd {HETZNER_REPO} && "
            f"[ -f {HETZNER_ENV_FILE} ] && set -a && . {HETZNER_ENV_FILE} && set +a; "
            f"nohup /usr/bin/python3 -m scripts.trends_scrapers.refresh_after_donation "
            f"--runall-sources {','.join(runall_sources)} --warm "
            f">> /var/log/trends_donate_refresh.log 2>&1 & echo TRIGGERED"
        )
        print(f"\nTriggering refresh on the scraper box for: "
              f"{', '.join(runall_sources)} ...")
        try:
            proc = subprocess.run(
                ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                 HETZNER_HOST, remote_cmd],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0 and 'TRIGGERED' in (proc.stdout or ''):
                print("  scraper box refresh started (runs in the background; "
                      "dashboard updates when it lands).")
            else:
                print(f"  scraper box trigger failed "
                      f"(rc={proc.returncode} {proc.stderr.strip()}); those "
                      f"sources will update on the next scheduled run.")
        except Exception as e:
            print(f"  scraper box unreachable ({e}); those sources will "
                  f"update on the next scheduled run.")

    # Residential scrapers run here, synchronously, with live output.
    # Warm the dashboard locally only when Hetzner isn't also running -
    # otherwise its chain finishes later and would be undone by a stale
    # local warm.
    if local_mods:
        print(f"\nRe-running local scrapers now: {', '.join(local_mods)} ...")
        cmd = [sys.executable, '-m',
               'scripts.trends_scrapers.refresh_after_donation',
               '--local-modules', ','.join(local_mods)]
        if not runall_sources:
            cmd.append('--warm')
        subprocess.run(cmd, cwd=str(repo_root))


def _cookie_applies_to_target(cookie_domain: str, target: str) -> bool:
    """Return True iff a cookie with `Domain=cookie_domain` would be
    sent by Chrome on a request to `target`. Cookies with a leading
    `.` (or bare parent-domain matches) are inherited by subdomains,
    which is how sessionid on `.tiktok.com` gets sent to `ads.tiktok.com`.

    Chrome's rule: `cookie_domain` matches `target` iff
    `target == cookie_domain` (with any leading `.` stripped) OR
    `target.endswith('.' + cookie_domain)`.
    """
    cd = (cookie_domain or '').lstrip('.').lower()
    tg = (target or '').lstrip('.').lower()
    if not cd or not tg:
        return False
    return tg == cd or tg.endswith('.' + cd)


def _read_chrome_cookies(domain: str, profile: str | None = None) -> list[dict]:
    """Return a list of cookie dicts that would apply to a request to
    `domain` from the user's Chrome profile. This includes cookies set
    on parent domains (e.g., `.tiktok.com` cookies apply to requests
    to `ads.tiktok.com`), matching real browser semantics.

    Previously we only asked browser_cookie3 for cookies whose Domain
    attribute contained `domain` verbatim - that skipped the actual
    auth cookies (sessionid, sid_guard) which sit on the parent domain
    for cross-subdomain SSO. This is why `ads.tiktok.com` donations
    were returning 5 cookies (settings, csrf) but no session cookie.
    """
    try:
        import browser_cookie3
    except ImportError:
        sys.exit(
            "browser_cookie3 is not installed. Install with:\n"
            "  pip3 install --user --break-system-packages browser-cookie3 boto3\n"
        )

    # Fetch WITHOUT a domain filter so we can apply proper cookie-scope
    # rules Python-side. `domain_name=''` (empty) returns everything;
    # then we filter to cookies whose Domain attribute applies to
    # `domain` under Chrome's Public Suffix rule.
    kwargs: dict = {}
    if profile:
        # browser_cookie3 accepts `cookie_file` for non-default profiles.
        # On macOS Chrome stores each profile at:
        #   ~/Library/Application Support/Google/Chrome/{profile}/Cookies
        chrome_dir = Path.home() / 'Library/Application Support/Google/Chrome' / profile
        kwargs['cookie_file'] = str(chrome_dir / 'Cookies')

    try:
        jar = browser_cookie3.chrome(**kwargs)
    except Exception as e:
        print(f"  ! chrome cookie read failed for {domain}: {e}", file=sys.stderr)
        return []

    cookies = []
    for c in jar:
        if not _cookie_applies_to_target(c.domain, domain):
            continue
        cookies.append({
            'name':     c.name,
            'value':    c.value,
            'domain':   c.domain,
            'path':     c.path or '/',
            'expires':  int(c.expires) if c.expires else None,
            'secure':   bool(c.secure),
            'httpOnly': bool(getattr(c, '_rest', {}).get('HttpOnly')) or False,
            # Playwright wants sameSite as one of Strict|Lax|None; Chrome
            # exports don't reliably carry it, default to Lax which is a
            # safe browser default.
            'sameSite': 'Lax',
        })
    return cookies


def _upload_to_s3(domain: str, cookies: list[dict], *, dry_run: bool) -> str:
    payload = {
        'domain':     domain,
        'cookies':    cookies,
        'donated_at': datetime.now(timezone.utc).isoformat(),
        'donor_host': os.uname().nodename,
        'count':      len(cookies),
    }
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

    if dry_run:
        out = Path(f'/tmp/donated_cookies_{domain}.json')
        out.write_bytes(body)
        return f'file://{out}'

    try:
        import boto3
    except ImportError:
        sys.exit("boto3 is not installed. `pip3 install --user --break-system-packages boto3`")

    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')
    key = f'{S3_PREFIX}{domain}.json'
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=body,
        ContentType='application/json',
        ServerSideEncryption='AES256',
        CacheControl='no-cache, max-age=0',
    )
    return f's3://{S3_BUCKET}/{key}'


def main() -> int:
    ap = argparse.ArgumentParser(description="Donate Chrome cookies to the Trends IQ scrapers.")
    ap.add_argument('domains', nargs='*', help='Domains to donate (default: all).')
    ap.add_argument('--profile', default=None,
                     help='Chrome profile name (default: Default). '
                          'Common alternatives: "Profile 1", "Profile 2".')
    ap.add_argument('--dry-run', action='store_true',
                     help='Write to /tmp instead of S3.')
    ap.add_argument('--no-refresh', action='store_true',
                     help='Skip the automatic post-donation data refresh '
                          '(used by local_residential_run, which runs the '
                          'full scraper batch itself right after donating).')
    ap.add_argument('--auto-login', action='store_true',
                     help='Sign in automatically using credentials stored in '
                          'the macOS Keychain and donate the resulting '
                          'session, instead of reading your live Chrome. See '
                          'trends_auto_login / trends_login_store.')
    ap.add_argument('--setup', action='store_true',
                     help='With --auto-login: open a visible browser so you '
                          'can finish any 2FA / CAPTCHA once; the persistent '
                          'profile keeps the session afterward.')
    args = ap.parse_args()

    # Auto-login path delegates to the Keychain-backed engine. Run via
    # `-m` so its package-relative imports resolve regardless of whether
    # THIS script was launched as a file or a module.
    if args.auto_login or args.setup:
        import subprocess
        cmd = [sys.executable, '-m', 'scripts.trends_scrapers.trends_auto_login']
        if args.setup:
            cmd.append('--setup')
        if args.dry_run:
            cmd.append('--dry-run')
        if args.no_refresh:
            cmd.append('--no-refresh')
        cmd += args.domains
        return subprocess.run(
            cmd, cwd=str(Path(__file__).resolve().parents[2])).returncode

    domains = args.domains or DEFAULT_DOMAINS
    print(f"Donating cookies for {len(domains)} domain(s) "
          f"{'to /tmp' if args.dry_run else f'to s3://{S3_BUCKET}/{S3_PREFIX}'}")
    print()

    # Legacy-domain hints. When a domain rebrands, Chrome may still hold
    # cookies for the old host name and none for the new one. Explaining
    # this once, in place, saves a lot of "the cookies were donated but
    # the scraper still fails" back-and-forth.
    # If Chrome has cookies under the OLD host (key of the inner tuple)
    # but the caller is asking for cookies for the CURRENT host
    # (the dict key), point them at the current sign-in URL so they
    # get a fresh session on the right origin. `sign_in_url` is the
    # SPECIFIC page to visit; some services keep `www.` as marketing
    # and put the actual session-issuing app on a subdomain.
    LEGACY_DOMAIN_HINTS: dict[str, tuple[str, str]] = {
        # WBD reverted the max.com rebrand in 2025 - app shell is
        # back at play.hbomax.com.
        'hbomax.com':    ('max.com',      'https://play.hbomax.com/'),
        # X's max.com-style rebrand from twitter.com (2023).
        'x.com':         ('twitter.com',  'https://x.com/'),
    }

    donated_ok: list[str] = []
    for domain in domains:
        cookies = _read_chrome_cookies(domain, profile=args.profile)
        if not cookies:
            print(f"  {domain:<20s} 0 cookies - skipping (are you logged in / have you visited it?)")
            hint = LEGACY_DOMAIN_HINTS.get(domain)
            if hint:
                legacy_host, sign_in_url = hint
                legacy_cookies = _read_chrome_cookies(legacy_host, profile=args.profile)
                if legacy_cookies:
                    print(f"  {domain:<20s} ! Chrome has {len(legacy_cookies)} "
                          f"cookies for the legacy '{legacy_host}' domain but "
                          f"none for '{domain}'.")
                    print(f"  {domain:<20s}   Visit {sign_in_url} in Chrome and "
                          f"sign in. Chrome will drop fresh cookies under the "
                          f"current domain; re-run this script and the "
                          f"donation will succeed.")
            continue
        try:
            uri = _upload_to_s3(domain, cookies, dry_run=args.dry_run)
        except Exception as e:
            print(f"  {domain:<20s} upload failed: {e}")
            continue
        print(f"  {domain:<20s} {len(cookies):>3d} cookies -> {uri}")
        donated_ok.append(domain)

    print()
    if args.dry_run or args.no_refresh or not donated_ok:
        print("Done. Scrapers will pick these up on their next run.")
        return 0

    _auto_refresh(donated_ok)
    print()
    print("Done. Donated sources were refreshed (or are refreshing in the "
          "background) - the dashboard shows the new data on its next load.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
