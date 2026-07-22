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
    'max.com',
    'amazon.com',      # for Prime Video (same session as amazon shopping)
    # ESPN+ intentionally omitted - programming lives at
    # disneyplus.com/browse/espn (Disney bundle), so the disneyplus.com
    # cookie already covers it. Add 'plus.espn.com' here only if you
    # ever want to scrape the standalone ESPN+ web player.
]

S3_BUCKET = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
S3_PREFIX = 'trends_iq_cookies/'


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
    args = ap.parse_args()

    domains = args.domains or DEFAULT_DOMAINS
    print(f"Donating cookies for {len(domains)} domain(s) "
          f"{'to /tmp' if args.dry_run else f'to s3://{S3_BUCKET}/{S3_PREFIX}'}")
    print()

    # Legacy-domain hints. When a domain rebrands, Chrome may still hold
    # cookies for the old host name and none for the new one. Explaining
    # this once, in place, saves a lot of "the cookies were donated but
    # the scraper still fails" back-and-forth.
    LEGACY_DOMAIN_HINTS = {
        'max.com':       'hbomax.com',   # rebrand 2023
        'x.com':         'twitter.com',  # rebrand 2023
    }

    for domain in domains:
        cookies = _read_chrome_cookies(domain, profile=args.profile)
        if not cookies:
            print(f"  {domain:<20s} 0 cookies - skipping (are you logged in / have you visited it?)")
            legacy = LEGACY_DOMAIN_HINTS.get(domain)
            if legacy:
                legacy_cookies = _read_chrome_cookies(legacy, profile=args.profile)
                if legacy_cookies:
                    print(f"  {domain:<20s} ! Chrome has {len(legacy_cookies)} "
                          f"cookies for the legacy '{legacy}' domain but none "
                          f"for '{domain}'.")
                    print(f"  {domain:<20s}   Visit https://play.{domain} in "
                          f"Chrome and sign in there. Chrome will drop fresh "
                          f"cookies under the current domain; re-run this "
                          f"script and the donation will succeed.")
            continue
        try:
            uri = _upload_to_s3(domain, cookies, dry_run=args.dry_run)
        except Exception as e:
            print(f"  {domain:<20s} upload failed: {e}")
            continue
        print(f"  {domain:<20s} {len(cookies):>3d} cookies -> {uri}")

    print()
    print("Done. Scrapers on Hetzner will pick these up on the next run.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
