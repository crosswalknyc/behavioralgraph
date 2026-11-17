#!/usr/bin/env python3
"""Regression test: a blocked Amazon title search is 'unverified', never 0.

On 2026-10-26 Amazon's title search came back empty for The Rainmaker,
Riverdale, The Big Bang Theory and The Hitman's Bodyguard. The resolver
returned 0 rows without saying it could not look, and that zero was read
as "Amazon never resolves from a bare title". The same titles resolve by
title when the search page loads (2026-11-17, Euphoria), so a search the
resolver could not read must raise, which StreamScout reports as
``failed`` / unverified, and only a search it did read may answer 0.

Runs offline: every fetch is stubbed.

    python3 scripts/test_streamscout_amazon_bare_title.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "streamscout"))

import amazon_episode_identifier as az  # noqa: E402

_REAL_FETCH = az.fetch
_REAL_PLAIN = az._plain_fetch
FAILS: list = []
WEB_CALLS: list = []       # every web-search request a lookup made


def check(label, ok):
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        FAILS.append(label)


def results_page(*asins, text="unrelated things to watch"):
    """A full-size Amazon search results page carrying these video links."""
    links = "".join(f'<a href="/gp/video/detail/{a}/">{text}</a>' for a in asins)
    return "<html><body>" + links + ("<div>filler</div>" * 3000) + "</body></html>"


def title_page(name, asin, episodes=2):
    """A readable title page (large enough not to read as a wall stub)."""
    return (f'<html><head><meta property="og:title" content="{name}"/></head>'
            f"<body>{asin}" + ("<div>filler</div>" * 1500) + "</body></html>")


WALL_STUB = "<html><body>Click the button below to continue shopping</body></html>"


def _entry(asin, n, kind):
    """One entry of a title page's `self` map, in Amazon's own shape."""
    return (f'"{asin}":{{"asins":["{asin}"],"compactGTI":"{"0" * 16}{asin}",'
            f'"gti":"amzn1.dv.gti.{"0" * 8}-0000-0000-0000-{n:012d}",'
            f'"sequenceNumber":{n},"titleType":"{kind}"}}')


# A readable one-season series page for 'Riverdale': a season shell and two episodes.
SERIES_PAGE = ('<html><head><meta property="og:title" content="Riverdale"/></head><body>'
               + _entry("B0GGGGGGGG", 1, "season") + _entry("B0HHHHHHH1", 1, "episode")
               + _entry("B0HHHHHHH2", 2, "episode") + ("<div>filler</div>" * 1500)
               + "</body></html>")


def web_refused(url, timeout=12):
    """Both web-search engines refuse us (the 2026-11-17 state)."""
    WEB_CALLS.append(url)
    return None, url, ""


def run(stub, web=web_refused, **kw):
    az.fetch = stub
    az._plain_fetch = web
    del WEB_CALLS[:]
    try:
        return az.resolve(**kw), None
    except Exception as e:  # noqa: BLE001
        return None, e
    finally:
        az.fetch = _REAL_FETCH
        az._plain_fetch = _REAL_PLAIN


def main():
    # 1. every request fails (the 503 wall): could not look
    got, err = run(lambda url, timeout=25: (None, url, ""), title="Riverdale")
    check("search unreachable raises instead of answering 0",
          isinstance(err, az._SearchBlocked))

    # 2. Amazon answers 200 with the small wall stub: could not look
    got, err = run(lambda url, timeout=25: (200, url, WALL_STUB), title="Riverdale")
    check("the 200 wall stub raises instead of answering 0",
          isinstance(err, az._SearchBlocked))

    # 3. a full-size page with no video links is not a results page
    blank = "<html><body>" + ("<div>filler</div>" * 3000) + "</body></html>"
    got, err = run(lambda url, timeout=25: (200, url, blank), title="Riverdale")
    check("a page with no video links raises instead of answering 0",
          isinstance(err, az._SearchBlocked))

    # 4. the search page was read and the title is not on it: an honest 0
    def read_no_match(url, timeout=25):
        if "amazon.com/s?" in url:
            return 200, url, results_page("B0AAAAAAAA", "B0BBBBBBBB")
        return None, url, ""
    got, err = run(read_no_match, title="Qzxv Plorbnak")
    check("search read with no match answers 0 without raising",
          err is None and got == ("Qzxv Plorbnak", []))

    # 5. the search page lists the title but every title page is walled
    def read_pages_walled(url, timeout=25):
        if "amazon.com/s?" in url:
            return 200, url, results_page("B0CCCCCCCC", text="Riverdale")
        if "/gp/video/detail/" in url:
            return 200, url, WALL_STUB
        return None, url, ""
    got, err = run(read_pages_walled, title="Riverdale")
    check("candidates found but every title page walled raises",
          isinstance(err, az._SearchBlocked))

    # 6. the search page lists a look-alike whose page is readable: honest 0
    def read_lookalike(url, timeout=25):
        if "amazon.com/s?" in url:
            return 200, url, results_page("B0DDDDDDDD", text="Riverdale")
        if "/gp/video/detail/" in url:
            return 200, url, title_page("Scandal", "B0DDDDDDDD")
        return None, url, ""
    got, err = run(read_lookalike, title="Riverdale")
    check("a readable look-alike page answers 0 without raising",
          err is None and got == ("Riverdale", []))

    # 6b. one candidate page walled, one readable look-alike: the walled
    #     page may have been the title, so this is could-not-look
    def read_one_walled(url, timeout=25):
        if "amazon.com/s?" in url:
            return 200, url, results_page("B0EEEEEEEE", "B0FFFFFFFF", text="Riverdale")
        if "B0EEEEEEEE" in url:
            return 200, url, WALL_STUB
        if "B0FFFFFFFF" in url:
            return 200, url, title_page("Scandal", "B0FFFFFFFF")
        return None, url, ""
    got, err = run(read_one_walled, title="Riverdale")
    check("one walled candidate page among readable ones still raises",
          isinstance(err, az._SearchBlocked))

    # 6c. refused web search fails fast: two requests, then the rest skipped,
    #     and never through the browser fallback (fetch is not called for it)
    seen_by_fetch: list = []

    def read_no_match_logged(url, timeout=25):
        seen_by_fetch.append(url)
        return read_no_match(url, timeout)
    got, err = run(read_no_match_logged, title="Qzxv Plorbnak")
    check("refused web search is tried once per engine, then skipped",
          len(WEB_CALLS) == 2)
    check("web search never goes through the browser-fallback fetch",
          not any("duckduckgo" in u for u in seen_by_fetch))

    # 6d. a title Amazon's own search finds never waits on a web search
    def read_match(url, timeout=25):
        if "amazon.com/s?" in url:
            return 200, url, results_page("B0GGGGGGGG", text="Riverdale")
        if "/gp/video/detail/" in url:
            return 200, url, SERIES_PAGE
        return None, url, ""
    got, err = run(read_match, title="Riverdale")
    check("a confident match from Amazon's search resolves with rows",
          err is None and got is not None and got[0] == "Riverdale" and len(got[1]) > 0)
    check("a confident match from Amazon's search makes no web-search request",
          WEB_CALLS == [])

    # 7. a pinned page keeps its old behaviour: no raise from discovery
    got, err = run(lambda url, timeout=25: (None, url, ""), title="Riverdale",
                   url="https://www.amazon.com/gp/video/detail/B01MU3G91Q")
    check("a pinned page that cannot be read still answers without raising",
          err is None and got is not None and got[1] == [])

    # 8. StreamScout reports the raise as failed (unverified), not as 0 seeds
    import streamscout_query as ssq
    az.fetch = lambda url, timeout=25: (None, url, "")
    az._plain_fetch = web_refused
    try:
        rep = ssq.query_report("Riverdale", platforms=["amazon"], kind="series")
    finally:
        az.fetch = _REAL_FETCH
        az._plain_fetch = _REAL_PLAIN
    check("query_report lists amazon under failed with no rows",
          not rep.get("rows") and "amazon" in (rep.get("failed") or {})
          and "_SearchBlocked" in str(rep["failed"]["amazon"]))

    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
