"""The tabs of the Trends engine's two products, defined once (2026-11-23).

The same engine powers two SELECT PRODUCT options: Trends (the daily
board) and Rankers (the ranked media boards). Until today the tab list
lived in three places: the per-seat grant catalog in app.py
(``_TIQ_TRENDS_TAB_KEYS`` / ``_TIQ_RANKERS_TAB_KEYS``), the tab strip in
templates/index.html (``tabs`` plus ``_rankersOrder`` in the Trends
renderer) and a hand-typed line in prometheus/dashboard_map.py that still
said five Rankers when the page had six. Liz asked Prometheus to list every
Ranker and what each ranks, and got "Which audience should I read for
this?" (Jenna 2026-11-23: "should list what products we have within these
as shes asking").

This module is the one definition. app.py reads its keys for the tab
grants; prometheus.dashboard_map reads it for every answer about the
dashboard itself; scripts/test_pm_dashboard_catalog.py pins the page's
tab strip (keys, order and on-screen labels) to it, so the three cannot
drift again.

Each tab: ``(key, label, ranks)``. ``key`` is the grant key and the
``data-tiq-tab`` value on the page; ``label`` is the on-screen name;
``ranks`` is what the tab ranks, in plain words, the way a reader would be
told. The services inside a tab (which streaming services, which FAST
services) are not here: they live in trends_iq's own platform lists and
the live store, which prometheus.dashboard_map reads at answer time.

Pure module: no Flask, no S3, no imports beyond the standard library.
"""
from __future__ import annotations

__all__ = ['RANKERS_TABS', 'TRENDS_TABS', 'RANKERS_TAB_KEYS', 'TRENDS_TAB_KEYS', 'tab', 'label_of']

# Rankers: the ranked media boards (SELECT PRODUCT > Rankers). Order = the strip.
RANKERS_TABS = (
    ('fast', 'FAST',
     "the top films and shows on the free streaming services with ads, ranked by yesterday's US views, "
     "with a channel list that ranks the live channels on each service"),
    ('music', 'Music',
     "the top songs, ranked by yesterday's US streams"),
    ('podcasts', 'Podcasts',
     "the top podcasts, ranked by yesterday's US listeners"),
    ('streaming', 'Streaming',
     "the top films and TV shows on each paid streaming service, films and TV side by side, ranked by yesterday's US views"),
    ('gaming', 'Gaming',
     "the top games, ranked by yesterday's US players"),
    ('youtube', 'YouTube',
     "the top 100 videos in the United States on YouTube's own daily chart, in YouTube's order, with the view counts "
     "YouTube publishes"),
)

# Trends: the daily board (SELECT PRODUCT > Trends). Order = the strip.
TRENDS_TABS = (
    ('searches', 'Trending', 'the top searches and hashtags right now, ranked by search volume'),
    ('movers', 'Movers', 'what is gaining, holding or losing steam, day over day and week over week'),
    ('headlines', 'Headlines', 'the top stories from the major news outlets, by story and by outlet'),
    ('people', 'Trending people', 'the people rising across news, Wikipedia, social and search'),
    ('books', 'Books', 'the best-selling and trending books'),
    ('comics', 'Comics', 'the best-selling comics, manga and graphic novels'),
    ('broadway', 'Broadway', 'every running Broadway show, ranked by weekly ticket buyers'),
    ('films', 'Films', 'the top films in theaters right now'),
    ('watchlist', 'Watchlist', 'the trends you have starred'),
)

RANKERS_TAB_KEYS = tuple(k for k, _l, _r in RANKERS_TABS)
TRENDS_TAB_KEYS = tuple(k for k, _l, _r in TRENDS_TABS)


def tab(key):
    """{'key', 'label', 'ranks', 'product'} for a tab key, or None."""
    k = str(key or '').strip().lower()
    for prod, table in (('rankers', RANKERS_TABS), ('trends', TRENDS_TABS)):
        for kk, label, ranks in table:
            if kk == k:
                return {'key': kk, 'label': label, 'ranks': ranks, 'product': prod}
    return None


def label_of(key):
    t = tab(key)
    return t['label'] if t else ''
