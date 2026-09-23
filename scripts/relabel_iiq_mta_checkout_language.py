#!/usr/bin/env python3
"""Purge purchase / ticketing wording from an Attribution IQ film title's
MTA coefficient caches (paths nest, forks, archetypes, leaks, assists,
bottom_funnel_label, touchpoint notes). Reuses the GOAT string map and
adds the variants seen on other titles. Backs up each cache first.

Usage: python3 scripts/relabel_iiq_mta_checkout_language.py --slug the_influencer_project_hades [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import boto3

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fix_goat_cast_social_and_checkout_language_2026_09_23 import STRING_MAP as GOAT_MAP  # noqa: E402

BUCKET = "dashboard-inputs"
TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

STRING_MAP = {k: v for k, v in GOAT_MAP.items() if v}
STRING_MAP.update({
    "Exposed to info-seek to ticketer to checkout page": "Exposed, then info-seek, then a showtimes page and the checkout page",
    "Exposed to ticketer to checkout page, no research or retarget": "Exposed, then a showtimes page and the checkout page, no research or retarget",
    "Touched multiple ticketers before reaching the checkout page": "Checked showtimes on more than one surface before reaching the checkout page",
    "Reached a cart/purchase page within 7d and reached the checkout page for a different film that weekend":
        "Opened a showtimes page within 7d and reached the checkout page for a different film that weekend",
    "Reached a cart/purchase page within 7d but never reached the checkout page, the bag-abandon equivalent":
        "Opened a showtimes page within 7d but never reached the checkout page",
})
# substring swaps for free-text notes
SUBSTRINGS = [
    ("pre-ticket-sale windows", "windows before showtimes go live"),
    ("pre-ticket-sale window", "window before showtimes go live"),
    ("tickets opened ~", "showtimes went live ~"),
    ("cart/purchase page", "showtimes page"),
    ("ticketing site", "showtimes site"),
]
REASON = ("Film-native funnel nouns (Jenna 2026-09-23): stage 3 is a showtimes page, "
          "stage 4 is the checkout page; no ticket, cart, bag, deal or coupon language "
          "anywhere a reader sees it.")


def fix_str(s: str) -> str:
    rep = STRING_MAP.get(s)
    if rep:
        return rep
    for a, b in SUBSTRINGS:
        if a in s:
            s = s.replace(a, b)
    return s


def walk(o):
    n = 0
    if isinstance(o, dict):
        for k, v in list(o.items()):
            if k == "_relabels":
                continue
            if isinstance(v, str):
                nv = fix_str(v)
                if nv != v:
                    o[k] = nv
                    n += 1
            else:
                n += walk(v)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            if isinstance(v, str):
                nv = fix_str(v)
                if nv != v:
                    o[i] = nv
                    n += 1
            else:
                n += walk(v)
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    s3 = boto3.client("s3", region_name="us-east-2")
    prefix = f"intent/{a.slug}/mta/coefficients_"
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        for o in page.get("Contents", []):
            key = o["Key"]
            body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
            d = json.loads(body)
            if "overall" not in d:
                print(f"[mta] {key}: old schema, skipped")
                continue
            n = walk(d)
            probe = json.loads(json.dumps(d))
            probe.pop("_relabels", None)
            left = sorted({s for s in re.findall(r'"([^"]*)"', json.dumps(probe))
                           if re.search(r"ticket|cart|bought|\bbag|deal|coupon|purchase", s, re.I)
                           and s not in ("3_ticketer", "ticketer_partition")})
            print(f"[mta] {key}: {n} edits; leftovers: {left or 'none'}")
            if not n or a.dry_run:
                continue
            d.setdefault("_relabels", []).append({"when_utc": datetime.now(timezone.utc).isoformat(), "reason": REASON})
            bk = key.replace("/mta/coefficients_", "/mta/_backups/coefficients_").replace(".json", f".pre_checkout_language_{TS}.json")
            s3.put_object(Bucket=BUCKET, Key=bk, Body=body, ContentType="application/json")
            s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(d).encode(), ContentType="application/json")
            print(f"[mta]   backup -> {bk}; rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
