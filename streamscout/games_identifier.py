#!/usr/bin/env python3
"""games_identifier.py — StreamScout resolver for VIDEO GAMES.

Makes ``games`` a first-class StreamScout platform, serviceable exactly like any
other: pick it from the menu, type a game (or franchise) title, and it runs the
live multi-store search and returns rows in StreamScout's schema —

    SHOW · URL · PRODUCTION · PLATFORM · SEASON

where URL is the punctuation-preserving CONTENT-map term (the operative path
segment(s), real slug punctuation kept, one "/" max, region/tracking dropped,
id-anchored where possible) and PLATFORM is the store. SEASON is unused for
games. SHOW is the searched franchise, identical on every row.

This module is a thin adapter: the whole engine (store registry, the live/API
searchers, the headless PlayStation/Xbox/Epic resolvers, the content-map term
builders) lives in ``streamscout/gametool_content/``. We just import it and map
its rows into the shape StreamScout's ``resolver_lookup`` expects.

Contract (matches every other ``*_identifier.py``):
    resolve(title, kind=..., seasons=..., url=None) -> (show, rows)
    each row: {show, season, episode, title, identifier, watch_url, platform,
               production}
    identifier -> the CSV URL column;  platform -> the CSV PLATFORM column.
"""
import importlib
import os
import sys

# The engine uses bare imports (``from common import …``, ``from stores import
# …``), so its own folder must lead sys.path.
_GC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "gametool_content")
if _GC_DIR not in sys.path:
    sys.path.insert(0, _GC_DIR)


def resolve(title, kind="movie", seasons=None, url=None):
    """Search every reachable store for ``title`` and return (show, rows).

    ``kind``/``seasons`` are ignored (games have no seasons) but accepted so the
    signature matches StreamScout's other resolvers. If ``url`` is given (the
    menu's paste-a-URL fallback), a single pasted product link is parsed instead
    of running a search — handy for the bot-walled stores."""
    gc = importlib.import_module("gametool_content")
    stores = importlib.import_module("stores")
    to_term = stores.to_term
    parse_url = stores.parse_url
    STORES = stores.STORES

    raw = []
    if url:
        parsed = parse_url(url)
        if parsed:
            label, sid, canon = parsed
            key = next((k for k, v in STORES if v[0] == label), "")
            raw = [{"title": title, "url": canon, "store_id": sid,
                    "production": "", "_store": label, "_key": key}]
    else:
        # non-interactive: no per-store paste prompts inside the menu flow
        raw = gc.run(title, only=None, want_paste=False)

    rows, seen = [], set()
    for r in raw:
        term = to_term(r.get("_store", ""), r.get("url", ""))
        if not term or term in seen:          # skip blanks / dupe terms
            continue
        seen.add(term)
        rows.append({
            "show": title,                    # franchise — identical every row
            "season": "",
            "episode": "",
            "title": "",
            "identifier": term,               # -> URL column (content-map term)
            "watch_url": "",
            "platform": r.get("_store", ""),  # -> PLATFORM column (the store)
            "production": r.get("production", "") or "",
        })
    return title, rows


if __name__ == "__main__":
    import json
    q = " ".join(sys.argv[1:]) or "Hades"
    show, rows = resolve(q)
    print(f"{show}: {len(rows)} term(s)")
    print(json.dumps(rows, indent=2)[:2000])
