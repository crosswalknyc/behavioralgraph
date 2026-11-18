#!/usr/bin/env python3
"""Peacock and Disney+: 'could not look' is never 'not there' (2026-11-17).

Peacock. peacocktv.com answers every request from outside the US with its
/unavailable page ('Unavailable In Your Region'). The build server sits in
Germany; the resolver read that page as a title with no episodes and answered
0 rows, so a title on Peacock (Las Culturistas Culture Awards, Poker Face)
read as 'not on Peacock'. The resolver now raises DiscoveryUnavailable when
Peacock could not be read, and answers 0 only when Peacock's own pages or
index were read. A special or an event (one programme, no season tree) is
resolved from its own /watch-online/tv/<slug>/<UUID> page when that page is
handed in.

Disney+. The title search is a web search; a refusing engine can hang a
connection, and 3 queries x 3 engines x 2 attempts at 30 seconds each held a
build's seed step for about four and a half minutes. The search now has a
time limit per call and a budget, an engine that refused once is not asked
again in the same lookup, and a search no engine answered raises instead of
answering 0.

Runs offline: every fetch is stubbed.

    python3 scripts/test_streamscout_peacock_disney_could_not_look.py
"""
from __future__ import annotations

import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "streamscout"))

import disney_episode_identifier as dz  # noqa: E402
import peacock_episode_identifier as pk  # noqa: E402

FAILS: list = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


# ---------------------------------------------------------------- Peacock
UNAVAILABLE = ('<html><head><meta property="og:title" content="Unavailable In Your Region | Peacock"/></head><body>'
               '<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script></body></html>')
UID1 = "9f8650f2-ffdd-3f2c-a8f5-000000000001"
UID2 = "b70c1c5b-34cc-3f2c-a8f5-000000000002"
SPECIAL = "eed3137c-3642-3ab2-a895-46bc8b095d09"
SERIES_PAGE = ('<html><head><meta property="og:title" content="Watch Poker Face | Peacock"/></head><body>'
               '<script id="__NEXT_DATA__" type="application/json">{"props": {"a": ['
               '{"type": "ASSET/EPISODE", "number": 1, "episodeName": "Dead Mans Hand", '
               f'"url": "/watch-online/tv/poker-face/5522/seasons/1/episodes/dead-mans-hand/{UID1}"}}, '
               '{"type": "ASSET/EPISODE", "number": 2, "episodeName": "The Night Shift", '
               f'"url": "/watch-online/tv/poker-face/5522/seasons/1/episodes/the-night-shift/{UID2}"}}]}}}}'
               '</script></body></html>')
SPECIAL_PAGE = ('<html><head><meta property="og:title" content="Watch Las Culturistas Culture Awards 2026 | Peacock"/></head>'
                '<body><script id="__NEXT_DATA__" type="application/json">{"props": {"a": [{"type": "ASSET/PROGRAMME"}]}}'
                '</script></body></html>')
SITEMAP_TOP = "<sitemapindex><loc>https://www.peacocktv.com/sitemap-content_page_entertainment_us-0.xml</loc></sitemapindex>"
SITEMAP_TV = "<urlset><loc>https://www.peacocktv.com/watch-online/tv/poker-face/5522</loc></urlset>"
SPECIAL_URL = f"https://www.peacocktv.com/watch-online/tv/las-culturistas-culture-awards-2026/{SPECIAL}"


def not_found(url):
    raise urllib.error.HTTPError(url, 404, "Not Found", None, None)


def peacock_run(opener, **kw):
    """resolve() with urlopen replaced: opener(url) -> (code, final url, html) or raises."""
    class _Resp:
        def __init__(self, code, final, body):
            self._c, self._f, self._b = code, final, body.encode("utf-8")
            self.headers = {}

        def read(self): return self._b
        def getcode(self): return self._c
        def geturl(self): return self._f
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _urlopen(req, timeout=30):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        return _Resp(*opener(url))

    real = pk.urllib.request.urlopen
    pk.urllib.request.urlopen = _urlopen
    pk._TV_INDEX = None
    pk._MOVIE_INDEX = None
    try:
        return pk.resolve(**kw), None
    except Exception as e:  # noqa: BLE001
        return None, e
    finally:
        pk.urllib.request.urlopen = real
        pk._TV_INDEX = None
        pk._MOVIE_INDEX = None


def us(url):
    """Peacock read from a US address."""
    if url.endswith("/sitemap.xml"):
        return 200, url, SITEMAP_TOP
    if "sitemap-content_page_entertainment" in url:
        return 200, url, SITEMAP_TV
    if url.endswith("/stream-tv/poker-face") or url.endswith("/watch-online/tv/poker-face/5522"):
        return 200, url, SERIES_PAGE
    if url == SPECIAL_URL:
        return 200, url, SPECIAL_PAGE
    if "peacocktv.com" in url:
        not_found(url)
    return 202, url, "bots use duckduckgo too"          # the web-search fallback is refused


def abroad(url):
    """Every peacocktv.com request lands on the out-of-region page."""
    if "peacocktv.com" in url:
        return 200, "https://www.peacocktv.com/unavailable", UNAVAILABLE
    return 202, url, "bots use duckduckgo too"


def dead(url):
    raise OSError("connection reset")


print("[1] Peacock from outside the US: could not look")
got, err = peacock_run(abroad, title="Poker Face")
check("a series behind the out-of-region page raises instead of answering 0",
      isinstance(err, pk.DiscoveryUnavailable), repr(got or err))
check("the reason names the out-of-region page", "out-of-region" in str(err or ""))
got, err = peacock_run(abroad, title="The Wild Robot", kind="movie")
check("a movie behind the out-of-region page raises too", isinstance(err, pk.DiscoveryUnavailable), repr(got or err))
got, err = peacock_run(abroad, title="Las Culturistas Culture Awards", url=SPECIAL_URL)
check("a handed-in page behind the out-of-region page raises (the page's own tree is never read as the title)",
      isinstance(err, pk.DiscoveryUnavailable), repr(got or err))
got, err = peacock_run(dead, title="Poker Face")
check("nothing at peacocktv.com answering raises", isinstance(err, pk.DiscoveryUnavailable), repr(got or err))

print("[2] Peacock from the US")
got, err = peacock_run(us, title="Poker Face")
check("a series on Peacock resolves", err is None and got and [r["identifier"] for r in got[1]] == [UID1, UID2], repr(got or err))
check("the show is named off the page", err is None and got and got[0] == "Poker Face", repr(got))
got, err = peacock_run(us, title="Amandaland")
check("a title Peacock does not carry answers 0 without raising (its pages and index were read)",
      err is None and got == ("Amandaland", []), repr(got or err))
got, err = peacock_run(us, title="Amandaland", kind="movie")
check("a movie Peacock does not carry answers 0 without raising (its sitemap was read)",
      err is None and got == ("Amandaland", []), repr(got or err))

print("[3] a special or an event: one programme, no season tree")
got, err = peacock_run(us, title="Las Culturistas Culture Awards", url=SPECIAL_URL)
check("its own page, handed in, resolves to its playback id",
      err is None and got and [r["identifier"] for r in got[1]] == [SPECIAL], repr(got or err))
check("named off the page", err is None and got and got[0] == "Las Culturistas Culture Awards 2026", repr(got))
got, err = peacock_run(us, title="Las Culturistas Culture Awards")
check("by title alone it is not guessed: 0 rows, no raise", err is None and got and got[1] == [], repr(got or err))
got, err = peacock_run(us, title="Poker Face", url="https://www.peacocktv.com/stream-tv/poker-face")
check("a series page handed in still returns its episodes, never the page id",
      err is None and got and [r["identifier"] for r in got[1]] == [UID1, UID2], repr(got or err))

print("[3b] Peacock web search is bounded (a miss cost 188 seconds through the dashboard)")
SEARCH_CALLS: list = []


def us_search_hangs(url):
    if "duckduckgo" in url:
        SEARCH_CALLS.append(url)
        raise TimeoutError("timed out")
    return us(url)


got, err = peacock_run(us_search_hangs, title="Amandaland")
check("engines that hang are each asked once, not once per query (2 calls, not 6)", len(SEARCH_CALLS) == 2, str(len(SEARCH_CALLS)))
check("the answer is still an honest 0: Peacock's own pages and index were read", err is None and got == ("Amandaland", []), repr(got or err))
check("the worst case is seconds: 2 engines x 8 seconds inside a 25 second budget",
      2 * pk.SEARCH_CALL_TIMEOUT_S <= pk.SEARCH_BUDGET_S <= 30, f"{pk.SEARCH_CALL_TIMEOUT_S} / {pk.SEARCH_BUDGET_S}")
del SEARCH_CALLS[:]
REAL_FETCH_PK = pk.fetch
TIMEOUTS: list = []


def _spy_fetch(url, data=None, timeout=30):
    if "duckduckgo" in url:
        TIMEOUTS.append(float(timeout))
    return REAL_FETCH_PK(url, data=data, timeout=timeout)


pk.fetch = _spy_fetch
try:
    got, err = peacock_run(us, title="Amandaland")          # the engines answer with their refusal page
finally:
    pk.fetch = REAL_FETCH_PK
check("a refusal page takes the engine out for the rest of the lookup", len(TIMEOUTS) == 2, str(len(TIMEOUTS)))
check("every search call carries the short time limit", TIMEOUTS and all(t_ <= pk.SEARCH_CALL_TIMEOUT_S for t_ in TIMEOUTS), str(TIMEOUTS))

# ---------------------------------------------------------------- Disney+
RESULTS_NONE = "<html><body>" + ("<div class='result'>unrelated things</div>" * 200) + "</body></html>"
RESULTS_HIT = ("<html><body>" + ("<div class='result'>x</div>" * 150)
               + '<a href="https://www.disneyplus.com/browse/entity-1a2b3c4d-0000-1111-2222-333344445555">Bluey</a></body></html>')
CHALLENGE = "<html><body>" + ("<p>Unfortunately, bots use DuckDuckGo too. anomaly</p>" * 60) + "</body></html>"
CALLS: list = []


def disney_search(stub, title="Nurses", kind="series", budget=None):
    real_fetch, real_budget = dz.fetch, dz.SEARCH_BUDGET_S
    del CALLS[:]

    def _fetch(url, data=None, timeout=30):
        CALLS.append((url.split("?")[0], float(timeout)))
        return stub(url, data, timeout)
    dz.fetch = _fetch
    if budget is not None:
        dz.SEARCH_BUDGET_S = budget
    t0 = time.monotonic()
    try:
        return dz.search_disney_url(title, kind), None, time.monotonic() - t0
    except Exception as e:  # noqa: BLE001
        return None, e, time.monotonic() - t0
    finally:
        dz.fetch = real_fetch
        dz.SEARCH_BUDGET_S = real_budget


print("[4] Disney+ search: every engine refuses")
got, err, took = disney_search(lambda u, d, t: (202, u, CHALLENGE))
check("a search no engine answered raises instead of answering 0", isinstance(err, dz.DiscoveryUnavailable), repr(got or err))
check("each engine is asked once, then left alone for the rest of the lookup (3 calls, not 18)", len(CALLS) == 3, str(len(CALLS)))
check("every call carries the short time limit", all(t <= dz.SEARCH_CALL_TIMEOUT_S for _u, t in CALLS), str(CALLS[:3]))


def hang(u, d, t):
    raise TimeoutError("timed out")


got, err, took = disney_search(hang)
check("engines that time out raise, after one try each", isinstance(err, dz.DiscoveryUnavailable) and len(CALLS) == 3, str(len(CALLS)))

print("[5] Disney+ search: the budget")
CLOCK = {"t": 1000.0}
real_monotonic = dz.time.monotonic
dz.time.monotonic = lambda: CLOCK["t"]


def slow_none(u, d, t):
    CLOCK["t"] += 20.0                      # each call burns 20 seconds on the clock
    raise TimeoutError("timed out") if "duckduckgo" in u else OSError("reset")


try:
    got, err, _took = disney_search(slow_none, budget=45.0)
finally:
    dz.time.monotonic = real_monotonic
check("the search stops inside its budget and raises (could not look)", isinstance(err, dz.DiscoveryUnavailable), repr(got or err))
check("no more than three calls fit a 45 second budget at 20 seconds each", len(CALLS) <= 3, str(len(CALLS)))
check("the worst case is bounded: 3 engines x 10 seconds, never minutes",
      3 * dz.SEARCH_CALL_TIMEOUT_S <= dz.SEARCH_BUDGET_S <= 60, f"{dz.SEARCH_CALL_TIMEOUT_S} / {dz.SEARCH_BUDGET_S}")

print("[6] Disney+ search: a results page that was read")


def bing_only(u, d, t):
    if "bing.com" in u:
        return 200, u, RESULTS_NONE
    return 202, u, CHALLENGE


got, err, took = disney_search(bing_only)
check("a results page with no Disney+ page for the title is an honest miss: None, no raise", err is None and got is None, repr(got or err))


def bing_hit(u, d, t):
    if "bing.com" in u:
        return 200, u, RESULTS_HIT
    return 202, u, CHALLENGE


got, err, took = disney_search(bing_hit, title="Bluey")
check("a results page with the title's page returns it", err is None and got and "/browse/entity-1a2b3c4d" in got, repr(got or err))

print("[7] Disney+ resolve: a pasted page skips the search; a refused search surfaces as could-not-look")
real_discover = dz.discover_url


def _raise(title, kind):
    raise dz.DiscoveryUnavailable("no search engine answered")


dz.discover_url = _raise
try:
    try:
        dz.resolve(title="Nurses")
        check("resolve lets the could-not-look through", False)
    except dz.DiscoveryUnavailable:
        check("resolve lets the could-not-look through", True)
finally:
    dz.discover_url = real_discover

if FAILS:
    print(f"\n{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("\nall checks passed")
