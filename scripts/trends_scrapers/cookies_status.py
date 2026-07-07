"""
Cookie donation status.

Prints one line per domain showing how fresh the donation is and how
many cookies are on file. Use this to decide which domains need a new
`donate_cookies.py` run.

Run locally OR on Hetzner:

    python3 scripts/trends_scrapers/cookies_status.py
"""

from __future__ import annotations

import sys

from ._base import cookie_donation_status, DEFAULT_COOKIE_MAX_AGE_H

DOMAINS = [
    'instagram.com',
    'target.com',
    'walmart.com',
    'etsy.com',
    'sephora.com',
    'lululemon.com',
    'bestbuy.com',
    'nike.com',
    'ulta.com',
]


def main() -> int:
    print(f"Cookie donation freshness (max age = {DEFAULT_COOKIE_MAX_AGE_H}h)")
    print(f"{'domain':<20s} {'status':<10s} {'age':>10s}  {'count':>5s}  donated_at")
    print('-' * 80)
    stale_or_missing = []
    for d in DOMAINS:
        s = cookie_donation_status(d)
        if not s.get('donated'):
            print(f"  {d:<20s} MISSING")
            stale_or_missing.append(d)
            continue
        age = s.get('age_hours')
        state = 'FRESH' if s.get('fresh') else 'STALE'
        if state == 'STALE':
            stale_or_missing.append(d)
        age_str = f"{age:.1f}h" if age is not None else '?'
        print(f"  {d:<20s} {state:<10s} {age_str:>10s}  {s.get('count', 0):>5d}  {s.get('donated_at', '?')}")
    print()
    if stale_or_missing:
        print(f"NEEDS DONATION: {', '.join(stale_or_missing)}")
        print()
        print("From your laptop, run:")
        print(f"  python3 scripts/trends_scrapers/donate_cookies.py {' '.join(stale_or_missing)}")
    else:
        print("All donations fresh.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
