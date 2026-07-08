"""
One-off diagnostic: render each streaming platform's discover page with
donated cookies and save the rendered HTML to /tmp for offline
inspection. Not part of the daily run.

Usage (from bg-webapp/):
    python3 -m scripts.trends_scrapers._capture_html

Writes /tmp/streaming_capture/{platform}_{page_label}.html
"""

from __future__ import annotations

import os
from pathlib import Path

from ._playwright import render_pages


CAPTURES = [
    # (platform_key, cookie_domain, homepage, [(label, url), ...])
    (
        'disneyplus', 'disneyplus.com',
        'https://www.disneyplus.com/',
        [
            # Public /browse/* pages serve a real catalog even without a
            # logged-in session - great for our use case.
            ('browse_espn',      'https://www.disneyplus.com/browse/espn'),
            ('browse_originals', 'https://www.disneyplus.com/browse/originals'),
            ('browse_marvel',    'https://www.disneyplus.com/browse/marvel'),
            ('browse_movies',    'https://www.disneyplus.com/browse/movies'),
            ('browse_series',    'https://www.disneyplus.com/browse/series'),
        ],
    ),
    (
        'hulu', 'hulu.com',
        'https://www.hulu.com/',
        [
            ('home',   'https://www.hulu.com/hub/home'),
            ('tv',     'https://www.hulu.com/hub/tv'),
            ('movies', 'https://www.hulu.com/hub/movies'),
        ],
    ),
    (
        'max', 'max.com',
        'https://www.max.com/',
        [
            ('home',     'https://www.max.com/'),
            ('series',   'https://www.max.com/series'),
            ('movies',   'https://www.max.com/movies'),
        ],
    ),
    (
        'primevideo', 'amazon.com',
        'https://www.amazon.com/gp/video/storefront',
        [
            ('storefront', 'https://www.amazon.com/gp/video/storefront'),
            ('tv',         'https://www.amazon.com/gp/video/browse/tv'),
        ],
    ),
    (
        'espnplus', 'plus.espn.com',
        'https://plus.espn.com/',
        [
            ('home',   'https://plus.espn.com/'),
            ('live',   'https://plus.espn.com/live'),
            ('movies', 'https://plus.espn.com/movies'),
            ('sports', 'https://plus.espn.com/sports'),
        ],
    ),
]


def main() -> int:
    out_dir = Path('/tmp/streaming_capture')
    out_dir.mkdir(parents=True, exist_ok=True)

    for platform, cookie_domain, homepage, pages in CAPTURES:
        print(f"\n=== {platform} ({cookie_domain}) ===")
        rendered = render_pages(
            pages,
            homepage=homepage,
            cookie_domain=cookie_domain,
            wait_ms=4500,
            scroll_ms=3000,
            timeout_ms=45000,
            hydration_wait_ms=12000,
        )
        for label, html in rendered:
            fp = out_dir / f"{platform}_{label}.html"
            fp.write_text(html, encoding='utf-8')
            print(f"  {platform} {label}: {len(html):>8d} bytes -> {fp}")

    print(f"\nAll captures written to {out_dir}/")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
