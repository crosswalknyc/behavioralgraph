#!/usr/bin/env python3
"""GOAT Attribution IQ: give the 16 generic-labelled YouTube assets
('Media Asset #9') their real names, resolved from YouTube oEmbed on
2026-09-23 and cleaned up (Sony trafficking slates -> spot name,
duration, tag; creator uploads -> 'Creator: Title').

Applies to ClickHouse intent.campaign_assets (ReplacingMergeTree upsert,
same sort key), every GOAT MTA coefficient cache touchpoint whose
touchpoint_id matches, and the S3 normalized snapshot. Backups first.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import boto3
import clickhouse_connect

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import intent_iq  # noqa: E402

BUCKET = "dashboard-inputs"
SLUG = "goat"
TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

# asset_id -> new label (oEmbed title, cleaned)
LABELS = {
    # Sony Pictures Entertainment paid YouTube spots
    "GOAT \u2013 DC THOUGHTS SUBBED VERTICAL :25 TAG01": "YouTube Spot: DC Thoughts (subtitled vertical :25, Tag 01)",
    "GOAT \u2013 SHOT BOYS W INTRO REVISED WIDE :30 TAG06": "YouTube Spot: Shot Boys w/ Intro (:30, Tag 06)",
    "GOAT \u2013 GREAT FINAL WIDE :30 TAG01": "YouTube Spot: Great (:30, Tag 01)",
    "GOAT \u2013 DC STEPH TICKETS CD WIDE :15 TAG06": "YouTube Spot: DC Steph Countdown (:15, Tag 06)",
    "GOAT \u2013 DC THOUGHTS SUBBED VERTICAL :25 TAG06": "YouTube Spot: DC Thoughts (subtitled vertical :25, Tag 06)",
    "GOAT - BOUNCE WIDE 30 Tag06 H264": "YouTube Spot: Bounce (:30, Tag 06)",
    "GOAT - DC SNEAK PEEK WIDE :27": "YouTube Spot: DC Sneak Peek (:27)",
    "GOAT - HISTORY CHAIR WIDE :30": "YouTube Spot: History Chair (:30)",
    "GOAT - DS LIPSTICK WIDE 37 Tag07 H264 rev 1": "YouTube Spot: DS Lipstick (:37, Tag 07)",
    "GOAT - DC THEATER WIDE 20 Tag07 H264 rev 1": "YouTube Spot: DC Theater (:20, Tag 07)",
    "GOAT - DS TEAM COLLAGE WIDE 37 Tag07 H264 rev 1": "YouTube Spot: DS Team Collage (:37, Tag 07)",
    # creator integrations
    "FIRE & ICE BATTLE \U0001F525\U0001F976": "Dude Perfect: Fire & Ice Battle",
    "Kim Kardashian Goes Sneaker Shopping With Complex": "Complex: Kim Kardashian Goes Sneaker Shopping",
    "I Went UNDERCOVER as a REALISTIC E-GIRL..": "IBella: I Went Undercover as a Realistic E-Girl",
    "$30 roblox game are we fr": "Sketch: $30 Roblox Game, Are We Fr",
}

ASSET_COLS = ["asset_id", "title_slug", "phase_name", "funnel_stage", "action_label",
              "asset_type", "channel", "paid_or_organic", "url", "source", "note",
              "posted_date", "talent_tags", "audience_target_tags",
              "ext_view_count", "ext_engagement_count", "ext_engagement_source",
              "thumbnail_s3_url", "og_metadata", "ingested_at", "updated_at"]


def main() -> int:
    dry = "--dry-run" in sys.argv
    oembed = json.load(open("/tmp/goat_yt_oembed.json"))  # (aid, lbl, date, views, url, title, author)
    id_to_label = {}
    for aid, _lbl, _d, _v, _url, title, _author in oembed:
        new = LABELS.get(title)
        if not new:
            print(f"  no mapping for {aid}: {title!r}")
            continue
        id_to_label[aid] = new
    print(f"{len(id_to_label)} assets to relabel")

    ch = clickhouse_connect.get_client(host=intent_iq.CH_HOST, port=intent_iq.CH_PORT,
                                       username=intent_iq.CH_USER, password=intent_iq.CH_PASS)
    s3 = boto3.client("s3", region_name="us-east-2")
    idlist = ", ".join(f"'{i}'" for i in id_to_label)
    rows = ch.query("SELECT " + ", ".join(ASSET_COLS) + " FROM intent.campaign_assets FINAL "
                    f"WHERE title_slug='{SLUG}' AND asset_id IN ({idlist})").result_rows
    rows = [dict(zip(ASSET_COLS, r)) for r in rows]
    for r in rows:
        print(f"  {r['asset_id']}  '{r['action_label']}' -> '{id_to_label[r['asset_id']]}'")
    if dry:
        return 0

    def dflt(o):
        return o.isoformat() if hasattr(o, "isoformat") else str(o)
    bkey = f"intent/{SLUG}/_backups/campaign_assets_pre_youtube_relabel_{TS}.json"
    s3.put_object(Bucket=BUCKET, Key=bkey, Body=json.dumps(rows, default=dflt).encode(),
                  ContentType="application/json")
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)
    ins = []
    for r in rows:
        r["action_label"] = id_to_label[r["asset_id"]]
        r["updated_at"] = now_ts
        ins.append([r[c] for c in ASSET_COLS])
    ch.insert("intent.campaign_assets", ins, column_names=ASSET_COLS)
    print(f"[ch] relabelled {len(ins)} rows; backup {bkey}")

    # MTA caches
    prefix = f"intent/{SLUG}/mta/coefficients_"
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        for o in page.get("Contents", []):
            key = o["Key"]
            body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
            d = json.loads(body)
            if "overall" not in d:
                continue
            n = 0

            def walk(x):
                nonlocal n
                if isinstance(x, dict):
                    tid = x.get("touchpoint_id")
                    if tid in id_to_label and x.get("asset_title") != id_to_label[tid]:
                        x["asset_title"] = id_to_label[tid]
                        n += 1
                    for v in x.values():
                        walk(v)
                elif isinstance(x, list):
                    for v in x:
                        walk(v)
            walk(d)
            if not n:
                print(f"[mta] {key}: clean")
                continue
            d.setdefault("_relabels", []).append({
                "when_utc": datetime.now(timezone.utc).isoformat(),
                "reason": "GOAT audit 2026-09-23: generic 'Media Asset #N' YouTube titles replaced with the real spot / creator names."})
            bk = key.replace("/mta/coefficients_", "/mta/_backups/coefficients_").replace(".json", f".pre_youtube_relabel_{TS}.json")
            s3.put_object(Bucket=BUCKET, Key=bk, Body=body, ContentType="application/json")
            s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(d).encode(), ContentType="application/json")
            print(f"[mta] {key}: {n} titles; backup {bk}")

    # snapshot
    key = f"intent/{SLUG}/source/normalized_assets.json"
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    snap = json.loads(body)
    n = 0
    for a in snap.get("assets", []):
        if a.get("asset_id") in id_to_label and a.get("action_label") != id_to_label[a["asset_id"]]:
            a["action_label"] = id_to_label[a["asset_id"]]
            n += 1
    if n:
        bk = f"intent/_backups/{SLUG}_normalized_assets.pre_youtube_relabel_{TS}.json"
        s3.put_object(Bucket=BUCKET, Key=bk, Body=body, ContentType="application/json")
        s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(snap).encode(), ContentType="application/json")
    print(f"[snapshot] {n} labels; done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
