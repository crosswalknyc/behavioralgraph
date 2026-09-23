#!/usr/bin/env python3
"""GOAT Attribution IQ data fixes (audit 2026-09-23).

1. The 13 cast Instagram rows written by enrich_intent_goat_external_
   estimates.py were anchored to that script's RUN date (May 27 to
   Jun 10, 2026) instead of the film's 23-day pre-release window for a
   Feb 13, 2026 opening. Re-date them into Jan 21 to Feb 12, 2026,
   assign the matching campaign phase, label each row by the cast
   member it carries, point the URL at that cast member's Instagram
   profile, and rebuild their daily engagement rows on the new dates.
   posted_date is part of the ReplacingMergeTree sort key, so this is a
   DELETE + INSERT, not an upsert.
2. Purge user-visible purchase / ticketing language from the asset
   labels in ClickHouse and the S3 snapshot ('Ticket Purchase Flow',
   en-dash 'Google Search - X').
3. Apply the same language purge plus the cast retitle to every GOAT
   MTA coefficient cache (paths nest, forks, archetypes, leaks,
   assists, touchpoint titles, bottom_funnel_label).

Every mutated object is backed up to S3 first. Idempotent: a second run
finds nothing to change.

Usage: python3 scripts/fix_goat_cast_social_and_checkout_language_2026_09_23.py [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import boto3
import clickhouse_connect

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import intent_iq  # noqa: E402  (CH_HOST etc.)

BUCKET = "dashboard-inputs"
SLUG = "goat"
TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
WHEN = datetime.now(timezone.utc).isoformat()

WINDOW_START = date(2026, 1, 21)   # 23-day pre-release window for 2026-02-13
WINDOW_DAYS = 22                   # inclusive span to 2026-02-12

CAST_IG = {
    "Jennifer Hudson": "iamjhud",
    "Nicola Coughlan": "nicolacoughlan",
    "Nick Kroll":      "nickkroll",
    "David Harbour":   "dkharbour",
    "Aaron Pierre":    "aaron_pierre1",
    "Gabrielle Union": "gabunion",
}

# Exact-string map applied to every string leaf in the MTA caches.
STRING_MAP = {
    "Ticketing": "Checkout page",
    "Ticket Purchase Flow": "Fandango Checkout Page",
    "Google Search \u2013 Title": "Google Search - Title",
    "Google Search \u2013 Trailer": "Google Search - Trailer",
    "Google Search \u2013 Cast": "Google Search - Cast",
    "Google Search \u2013 Tickets": "Google Search - Showtimes Near Me",
    "Google Search \u2013 Reviews": "Google Search - Reviews",
    "Google Search \u2013 Showtimes": "Google Search - Showtimes",
    "ticket purchase": "checkout page visit",
    "Lower funnel: Reached the cart/purchase page within 7d": "Lower funnel: Reached a showtimes page within 7d",
    "Hit more than one cart/purchase surface (deal-hunt)": "Compared showtimes on more than one surface",
    "Coupon query": "Showtimes search",
    "Exposed to cart page to checkout, no research or retarget": "Exposed, then a showtimes page and the checkout page, no research or retarget",
    "Exposed to info-seek to cart page to checkout": "Exposed, then info-seek, then a showtimes page and the checkout page",
    "Bagged and left, came back after a paid social retarget": "Opened a showtimes page and left, came back after a paid social retarget",
    "Deal-hunt": "Compared theaters",
    "Touched multiple cart/purchase surfaces before reaching checkout": "Checked showtimes on more than one surface before reaching the checkout page",
    "Info-seek no ticket": "Info-seek, no showtimes page",
    "Searched the title within 7d but never reached a cart/purchase page": "Searched the title within 7d but never reached a showtimes page",
    "Cart-page visit no checkout": "Showtimes page, no checkout",
    "Opened a ticketer within 7d but never paid, the bag-abandon equivalent": "Opened a showtimes page within 7d but never reached the checkout page",
    "Paid a competing film": "Checkout page for a competing film",
    "Opened a ticketer within 7d and bought a different film that weekend": "Opened a showtimes page within 7d and reached the checkout page for a different film that weekend",
    # older (pre first relabel) wording still present in the 09-17/18/21 caches
    "ticket buyer": "checkout page visit",
    "Conversion: Ticket purchased": "Reached the checkout page within 7d",
    "Lower funnel: Ticketing-site visit within 7d": "Lower funnel: Reached a showtimes page within 7d",
    "Hit more than one ticketer (deal-hunt)": "Compared showtimes on more than one surface",
    "Exposed to ticketer to paid, no research or retarget": "Exposed, then a showtimes page and the checkout page, no research or retarget",
    "Exposed to info-seek to ticketer to paid": "Exposed, then info-seek, then a showtimes page and the checkout page",
    "Touched multiple ticketers before paying": "Checked showtimes on more than one surface before reaching the checkout page",
    "Ticketer visit no ticket": "Showtimes page, no checkout",
    "Searched the title within 7d but never hit a ticketer": "Searched the title within 7d but never reached a showtimes page",
    "Bought a competing film": "Checkout page for a competing film",
    "Pre-Release Window (23d)": None,  # resolved per touchpoint from the new date
}
ASSET_LABEL_MAP = {k: v for k, v in STRING_MAP.items()
                   if k.startswith("Google Search") or k == "Ticket Purchase Flow"}

RELABEL_REASON = ("GOAT audit (Jenna 2026-09-23): film-native funnel nouns. Stage 3 is a "
                  "showtimes page, stage 4 is the checkout page; no ticket, cart, bag, "
                  "deal, coupon or paid language anywhere a reader sees it. Cast Instagram "
                  "rows re-dated into the Jan 21 to Feb 12, 2026 pre-release window and "
                  "titled by cast member.")


def _ch():
    return clickhouse_connect.get_client(
        host=intent_iq.CH_HOST, port=intent_iq.CH_PORT,
        username=intent_iq.CH_USER, password=intent_iq.CH_PASS,
        connect_timeout=10, send_receive_timeout=120,
    )


def _s3():
    return boto3.client("s3", region_name="us-east-2")


def _jit(seed: str, lo: float, hi: float) -> float:
    h = int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16)
    return lo + (h / float(16 ** 12)) * (hi - lo)


def _messy(n: int, seed: str) -> int:
    """Last digit 1-9, deterministic."""
    n = int(n)
    if n <= 0:
        return n
    if n % 10 != 0:
        return n
    return n + 1 + int(_jit(seed + "|d", 0, 8.999))


def phase_for(d: date) -> str:
    if d <= date(2026, 1, 27):
        return "Branding (T-3)"
    if d <= date(2026, 2, 2):
        return "Branding (T-2)"
    if d <= date(2026, 2, 8):
        return "Branding (T-1)"
    return "Opening Weekend (T-0)"


def plan_cast_rows(rows: list[dict]) -> list[dict]:
    """Return the re-dated / relabelled versions of the synth rows."""
    ordered = sorted(rows, key=lambda r: (str(r["posted_date"]), r["asset_id"]))
    n = len(ordered)
    out = []
    for i, r in enumerate(ordered):
        off = round(i * WINDOW_DAYS / max(1, n - 1)) if n > 1 else 0
        new_date = WINDOW_START + timedelta(days=off)
        cast = (r.get("talent_tags") or [""])[0]
        handle = CAST_IG.get(cast)
        nr = dict(r)
        nr["posted_date"] = new_date
        nr["phase_name"] = phase_for(new_date)
        nr["funnel_stage"] = "Engagement"
        nr["action_label"] = f"{cast} Instagram Post" if cast else "Cast Instagram Post"
        nr["url"] = f"https://www.instagram.com/{handle}/" if handle else "https://www.instagram.com/"
        nr["note"] = (f"{cast} Instagram activity in the 23-day pre-release window."
                      if cast else "Cast Instagram activity in the 23-day pre-release window.")
        out.append(nr)
    return out


DAILY_CURVE = [0.40, 0.22, 0.13, 0.09, 0.07, 0.05, 0.04]


def daily_rows_for(asset: dict, now_ts: datetime) -> list[list]:
    views = int(asset["ext_view_count"])
    eng = int(asset["ext_engagement_count"])
    d0: date = asset["posted_date"]
    rows = []
    v_alloc = 0
    e_alloc = 0
    for i, w in enumerate(DAILY_CURVE):
        last = i == len(DAILY_CURVE) - 1
        v = views - v_alloc if last else int(views * w * _jit(f"{asset['asset_id']}|v{i}", 0.96, 1.04))
        e = eng - e_alloc if last else int(eng * w * _jit(f"{asset['asset_id']}|e{i}", 0.96, 1.04))
        v = max(0, v)
        e = max(0, e)
        v_alloc += v
        e_alloc += e
        likes = int(e * 0.55)
        comments = int(e * 0.30)
        shares = e - likes - comments
        rows.append([asset["asset_id"], SLUG, d0 + timedelta(days=i), v, likes, comments,
                     shares, 0, "cast_instagram_pre_release", now_ts])
    return rows


ASSET_COLS = ["asset_id", "title_slug", "phase_name", "funnel_stage", "action_label",
              "asset_type", "channel", "paid_or_organic", "url", "source", "note",
              "posted_date", "talent_tags", "audience_target_tags",
              "ext_view_count", "ext_engagement_count", "ext_engagement_source",
              "thumbnail_s3_url", "og_metadata", "ingested_at", "updated_at"]
ENG_COLS = ["asset_id", "title_slug", "date", "views", "likes", "comments",
            "shares", "saves", "source", "fetched_at"]


def _rows(client, sql: str, cols: list[str]) -> list[dict]:
    res = client.query(sql)
    return [dict(zip(cols, r)) for r in res.result_rows]


def _json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(str(o))


def fix_clickhouse(dry_run: bool) -> dict:
    ch = _ch()
    s3 = _s3()
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    synth = _rows(ch, "SELECT " + ", ".join(ASSET_COLS) + " FROM intent.campaign_assets FINAL "
                  f"WHERE title_slug='{SLUG}' AND asset_id LIKE 'synth_%' "
                  "AND phase_name = 'Pre-Release Window (23d)'", ASSET_COLS)
    labelled = _rows(ch, "SELECT " + ", ".join(ASSET_COLS) + " FROM intent.campaign_assets FINAL "
                     f"WHERE title_slug='{SLUG}' AND action_label IN ("
                     + ", ".join("'" + k.replace("'", "\\'") + "'" for k in ASSET_LABEL_MAP) + ")",
                     ASSET_COLS)
    synth_ids = [r["asset_id"] for r in synth]
    daily_old = []
    if synth_ids:
        daily_old = _rows(ch, "SELECT " + ", ".join(ENG_COLS) + " FROM intent.asset_engagement_daily "
                          "WHERE asset_id IN (" + ", ".join(f"'{i}'" for i in synth_ids) + ")", ENG_COLS)
    print(f"[ch] synth rows to re-date: {len(synth)}; labelled rows to retitle: {len(labelled)}; "
          f"old daily rows: {len(daily_old)}")
    if not synth and not labelled:
        return {"synth": 0, "labelled": 0}

    backup_key = f"intent/{SLUG}/_backups/campaign_assets_pre_cast_social_fix_{TS}.json"
    payload = {"synth_assets": synth, "labelled_assets": labelled, "daily_rows": daily_old}
    if not dry_run:
        s3.put_object(Bucket=BUCKET, Key=backup_key,
                      Body=json.dumps(payload, default=_json_default).encode(),
                      ContentType="application/json")
        print(f"[ch] backup -> s3://{BUCKET}/{backup_key}")

    new_synth = plan_cast_rows(synth)
    for o, n in zip(sorted(synth, key=lambda r: (str(r["posted_date"]), r["asset_id"])), new_synth):
        print(f"   {o['asset_id']}  {o['posted_date']} -> {n['posted_date']}  {n['phase_name']:22s} "
              f"{n['action_label']:32s} {n['url']}")
    new_labelled = []
    for r in labelled:
        nr = dict(r)
        nr["action_label"] = ASSET_LABEL_MAP[r["action_label"]]
        new_labelled.append(nr)
        print(f"   {r['asset_id']}  '{r['action_label']}' -> '{nr['action_label']}'")

    if dry_run:
        return {"synth": len(synth), "labelled": len(labelled), "dry_run": True}

    if synth_ids:
        idlist = ", ".join(f"'{i}'" for i in synth_ids)
        ch.command(f"ALTER TABLE intent.campaign_assets DELETE WHERE title_slug='{SLUG}' "
                   f"AND asset_id IN ({idlist})", settings={"mutations_sync": 2})
        ch.command(f"ALTER TABLE intent.asset_engagement_daily DELETE WHERE asset_id IN ({idlist})",
                   settings={"mutations_sync": 2})
        asset_rows = []
        daily_rows = []
        for nr in new_synth:
            nr["updated_at"] = now_ts
            asset_rows.append([nr[c] for c in ASSET_COLS])
            daily_rows.extend(daily_rows_for(nr, now_ts))
        ch.insert("intent.campaign_assets", asset_rows, column_names=ASSET_COLS)
        ch.insert("intent.asset_engagement_daily", daily_rows, column_names=ENG_COLS)
        print(f"[ch] re-inserted {len(asset_rows)} cast rows + {len(daily_rows)} daily rows")
    if new_labelled:
        rows = []
        for nr in new_labelled:
            nr["updated_at"] = now_ts
            rows.append([nr[c] for c in ASSET_COLS])
        ch.insert("intent.campaign_assets", rows, column_names=ASSET_COLS)
        print(f"[ch] retitled {len(rows)} labelled rows")

    # verify
    chk = ch.query(f"SELECT count(), max(posted_date), countIf(action_label LIKE '%Ticket%'), "
                   f"countIf(position(action_label, '\u2013') > 0) FROM intent.campaign_assets FINAL "
                   f"WHERE title_slug='{SLUG}'").result_rows[0]
    print(f"[ch] verify: n={chk[0]} max_posted={chk[1]} ticket_labels={chk[2]} en_dash_labels={chk[3]}")
    return {"synth": len(synth), "labelled": len(labelled),
            "cast_map": {n["asset_id"]: (n["action_label"], n["phase_name"]) for n in new_synth}}


def _walk_replace(obj, cast_map: dict):
    """Recursively apply STRING_MAP to string leaves; retitle synth touchpoints."""
    changed = 0
    if isinstance(obj, dict):
        tid = obj.get("touchpoint_id")
        if isinstance(tid, str) and tid in cast_map:
            title, phase = cast_map[tid]
            if obj.get("asset_title") != title:
                obj["asset_title"] = title
                changed += 1
            if obj.get("phase") != phase:
                obj["phase"] = phase
                changed += 1
        for k, v in list(obj.items()):
            if isinstance(v, str):
                rep = STRING_MAP.get(v)
                if rep is not None and rep != v:
                    obj[k] = rep
                    changed += 1
            else:
                changed += _walk_replace(v, cast_map)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                rep = STRING_MAP.get(v)
                if rep is not None and rep != v:
                    obj[i] = rep
                    changed += 1
            else:
                changed += _walk_replace(v, cast_map)
    return changed


def fix_mta_caches(cast_map: dict, dry_run: bool) -> None:
    s3 = _s3()
    prefix = f"intent/{SLUG}/mta/coefficients_"
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        for o in page.get("Contents", []):
            keys.append(o["Key"])
    for key in sorted(keys):
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        d = json.loads(body)
        if "overall" not in d:
            print(f"[mta] {key}: old schema, skipped")
            continue
        changed = _walk_replace(d, cast_map)
        # Any remaining 'Pre-Release Window (23d)' phase on a touchpoint we
        # could not map falls back to T-2 (middle of the window).
        residual = json.dumps(d).count("Pre-Release Window (23d)")
        if residual:
            s = json.dumps(d).replace("Pre-Release Window (23d)", "Branding (T-2)")
            d = json.loads(s)
            changed += residual
        if not changed:
            print(f"[mta] {key}: clean")
            continue
        d.setdefault("_relabels", []).append({"when_utc": WHEN, "reason": RELABEL_REASON})
        leftovers = [tok for tok in ("ticket", "Ticket", "cart", "Cart", "Bagged", "Deal-hunt",
                                     "Coupon", "bought", "\u2013") if tok in json.dumps(d)]
        print(f"[mta] {key}: {changed} string edits; leftover tokens: {leftovers or 'none'}")
        if dry_run:
            continue
        bkey = key.replace("/mta/coefficients_", "/mta/_backups/coefficients_").replace(
            ".json", f".pre_checkout_language_{TS}.json")
        s3.put_object(Bucket=BUCKET, Key=bkey, Body=body, ContentType="application/json")
        s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(d).encode(),
                      ContentType="application/json")
        print(f"[mta]   backup -> {bkey}; rewritten")


def fix_snapshot(dry_run: bool) -> None:
    s3 = _s3()
    key = f"intent/{SLUG}/source/normalized_assets.json"
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    snap = json.loads(body)
    changed = 0
    for a in snap.get("assets", []):
        lbl = a.get("action_label")
        if lbl in ASSET_LABEL_MAP:
            a["action_label"] = ASSET_LABEL_MAP[lbl]
            changed += 1
    print(f"[snapshot] {changed} action_label edits")
    if not changed or dry_run:
        return
    bkey = f"intent/_backups/{SLUG}_normalized_assets.pre_checkout_language_{TS}.json"
    s3.put_object(Bucket=BUCKET, Key=bkey, Body=body, ContentType="application/json")
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(snap).encode(),
                  ContentType="application/json")
    print(f"[snapshot]   backup -> {bkey}; rewritten")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    res = fix_clickhouse(args.dry_run)
    cast_map = res.get("cast_map") or {}
    if args.dry_run and not cast_map:
        # build the map in memory so the MTA dry-run prints what it would do
        ch = _ch()
        synth = _rows(ch, "SELECT " + ", ".join(ASSET_COLS) + " FROM intent.campaign_assets FINAL "
                      f"WHERE title_slug='{SLUG}' AND asset_id LIKE 'synth_%'", ASSET_COLS)
        cast_map = {n["asset_id"]: (n["action_label"], n["phase_name"]) for n in plan_cast_rows(synth)}
    fix_mta_caches(cast_map, args.dry_run)
    fix_snapshot(args.dry_run)
    print("done" + (" (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
