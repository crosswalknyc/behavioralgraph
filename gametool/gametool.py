#!/usr/bin/env python3
"""
GameTool (gametool.py) — StreamScout's sibling for video games.
================================================================
Give it a game title; it fetches the product / purchase / download URL on every
store it can reach and writes ONE consistent CSV to your Desktop:

    SHOW · HOSTMAP_TERM · URL · PRODUCTION · PLATFORM · FORMAT · STORE_ID

HOSTMAP_TERM = the normalized clickstream key — the operative path segment(s)
only, non-alphanumerics → spaces, one "/" max, region/tracking dropped, anchored
on the stable id (or prefix/slug at franchise grain). Drop it straight into the
hostmap. PLATFORM = the store (Steam, Nintendo eShop, GameStop, …); FORMAT =
Digital / Physical / App / Cloud / Key; STORE_ID = the stable id embedded in the
URL (Steam appid, ASIN, Xbox Store id, TCIN, …).

Two ways to run:
  • Interactive:   python3 gametool.py
  • One-shot:      python3 gametool.py --title "Hades" [--stores steam,gog,...]
                   python3 gametool.py --url "https://store.steampowered.com/app/1145360/"

Stores with live anonymous search (no login): Steam, GOG, Apple App Store,
Google Play, Battle.net. The bot-walled storefronts/retailers (Nintendo,
PlayStation, Xbox, Epic, Amazon Luna, Eneba, Loaded, Amazon, Best Buy, GameStop,
Walmart, Target) parse a pasted product URL into a clean id — paste one when
prompted, or pass --url.
"""
import argparse
import csv
import os
import re
import sys
from datetime import datetime

from stores import STORE_MAP, STORES, URL_PARSE_LABELS, parse_url, to_term


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "game"


def search_store(key, title):
    label, fmt, tier, fn = STORE_MAP[key]
    if not fn:
        return []
    try:
        rows = fn(title) or []          # each store uses its own tuned cap
    except Exception as e:  # noqa: BLE001
        print(f"    ! {label} search failed: {e}")
        return []
    for r in rows:
        r.setdefault("format", fmt)
        r["_store"] = label
        r["_key"] = key
    return rows


def run(title, only=None, want_paste=True):
    """Search every (selected) store for `title`; return unified rows."""
    keys = [k for k, _ in STORES if (not only or k in only)]
    rows, empty_paste = [], []
    for key in keys:
        label, fmt, tier, fn = STORE_MAP[key]
        if fn and tier == "headless":
            print(f"  … {label:20s} searching (headless browser) …")
        got = search_store(key, title) if fn else []
        if got:
            print(f"  ✓ {label:20s} {len(got)} hit(s)")
            rows.extend(got)
        else:
            tag = "no match" if fn else "paste-URL store"
            print(f"  · {label:20s} {tag}")
            # offer the paste path for any store whose product URLs we can parse
            # (covers paste-only stores AND a headless store that came back empty)
            if label in URL_PARSE_LABELS:
                empty_paste.append(key)
    # offer to enrich the paste-only stores from a pasted product URL
    if want_paste and empty_paste and sys.stdin.isatty():
        print("\n  Paste a product URL for any store below to add it "
              "(Enter to skip):")
        for key in empty_paste:
            label = STORE_MAP[key][0]
            u = ask(f"    {label}: ")
            if not u:
                continue
            parsed = parse_url(u)
            if not parsed:
                print("      (couldn't parse that URL — skipped)")
                continue
            plabel, sid, canon = parsed
            rows.append({"title": title, "url": canon, "store_id": sid,
                         "format": STORE_MAP[key][1], "production": "",
                         "_store": plabel, "_key": key})
    return rows


def write_csv(title, rows, outdir):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(outdir, f"gametool_{slug(title)}_{stamp}.csv")
    order = {k: i for i, (k, _) in enumerate(STORES)}
    rows = sorted(rows, key=lambda r: (order.get(r.get("_key"), 99),
                                       r.get("title", "")))
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SHOW", "HOSTMAP_TERM", "URL", "PRODUCTION", "PLATFORM",
                    "FORMAT", "STORE_ID"])
        for r in rows:
            w.writerow([r.get("title", ""),
                        to_term(r.get("_store", ""), r.get("url", "")),
                        r["url"], r.get("production", ""),
                        r.get("_store", ""), r.get("format", ""),
                        r.get("store_id", "")])
    return path


def main():
    ap = argparse.ArgumentParser(description="GameTool — game purchase/download "
                                             "URLs across every store.")
    ap.add_argument("--title")
    ap.add_argument("--url", help="parse a single pasted product URL")
    ap.add_argument("--stores", help="comma list to limit stores "
                                     "(e.g. steam,gog,nintendo)")
    ap.add_argument("--outdir", default=os.path.expanduser("~/Desktop"))
    ap.add_argument("--no-paste", action="store_true",
                    help="skip the interactive paste-URL prompts")
    args = ap.parse_args()

    if args.url:
        parsed = parse_url(args.url)
        if not parsed:
            print("  ! Couldn't recognize that store URL."); return 2
        label, sid, canon = parsed
        title = args.title or ""
        rows = [{"title": title, "url": canon, "store_id": sid, "format": "",
                 "production": "", "_store": label,
                 "_key": next((k for k, v in STORES if v[0] == label), "")}]
        path = write_csv(title or slug(canon), rows, args.outdir)
        print(f"\n  [{label}] {sid}  ->  {canon}\n  CSV: {path}")
        return 0

    title = args.title or ask("What game?: ")
    if not title:
        print("Need a --title (or --url)."); return 1

    only = None
    if args.stores:
        only = {s.strip().lower() for s in args.stores.split(",") if s.strip()}
        bad = only - set(STORE_MAP)
        if bad:
            print(f"  ! Unknown store(s): {', '.join(sorted(bad))}")
            print(f"    Valid: {', '.join(k for k, _ in STORES)}"); return 1

    print(f"\nSearching stores for {title!r} …")
    rows = run(title, only=only, want_paste=not args.no_paste)
    if not rows:
        print("\n  Nothing found. Try --url with a product link."); return 2

    path = write_csv(title, rows, args.outdir)
    stores = len({r["_store"] for r in rows})
    print(f"\nFound {len(rows)} link(s) across {stores} store(s) for {title!r}:")
    for r in sorted(rows, key=lambda r: r.get("_store", "")):
        term = to_term(r.get("_store", ""), r.get("url", ""))
        print(f"   [{r.get('_store',''):18s}] {term}")
    print(f"\nCSV: {path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
