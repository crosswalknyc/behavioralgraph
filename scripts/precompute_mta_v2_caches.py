#!/usr/bin/env python3
"""Precompute + cache MTA v2 payloads for every campaign with the tab on.

Reads `enabled_tabs.mta` from every entry in `metadata/intent_registry.json`;
kicks compute_mta_coefficients(slug) for each hit. Because the fitter now
carries schema_version 2, any v1 cache the loader picks up is treated as
stale and force-rebuilt in place under the same S3 key. Prints one line
per campaign with the shipping numbers (sample_size, baseline conversion,
journeys count, top journey signature, top co-exposure pair).

Environment: needs `CH_HOST/CH_PORT/CH_USER/CH_PASSWORD` for the S3-backed
overview + assets lookups, and `AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY`
for S3 cache writes.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path


# ── path setup: bg-webapp/scripts/ -> bg-webapp/ on sys.path ────────────────
HERE = Path(__file__).resolve().parent
BGWEBAPP = HERE.parent
sys.path.insert(0, str(BGWEBAPP))


def _load_enabled_campaigns() -> list[str]:
    """Return the campaign slugs whose registry entry has enabled_tabs.mta."""
    import boto3  # type: ignore
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-2"))
    obj = s3.get_object(Bucket="dashboard-inputs",
                         Key="intent/registry.json")
    reg = json.loads(obj["Body"].read().decode("utf-8"))
    out = []
    for entry in reg.get("titles", []):
        tabs = (entry.get("enabled_tabs") or {})
        if tabs.get("mta"):
            out.append(entry["title_slug"])
    return sorted(out)


def _fmt_pct(x, digits=2) -> str:
    try:
        return f"{100.0 * float(x):.{digits}f}%"
    except Exception:
        return "-"


def _summarize(payload: dict) -> None:
    slug = payload.get("campaign_slug", "?")
    ok = payload.get("success", False)
    ss = payload.get("sample_size", 0)
    br = payload.get("conversion_rate", 0.0)
    js = payload.get("journeys") or []
    ce = payload.get("co_exposure") or {}
    tps = payload.get("touchpoints") or []
    cache_key = payload.get("cache_key")
    print(f"\n── {slug} ──────────────────────────────────────────────────────")
    print(f"  success       : {ok}")
    print(f"  sample_size   : {ss:,}")
    print(f"  baseline conv : {_fmt_pct(br)}")
    print(f"  touchpoints   : {len(tps)}")
    print(f"  journeys      : {len(js)}")
    print(f"  cache_key     : s3://dashboard-inputs/{cache_key}"
          if cache_key else "  cache_key     : <not cached>")
    if js:
        top = max(js, key=lambda r: int(r.get("converted_n") or 0))
        chips = " + ".join((t.get("asset_title") or "")[:22]
                            for t in (top.get("touchpoints") or [])[:4])
        more = len(top.get("touchpoints") or []) - 4
        if more > 0:
            chips += f" (+{more} more)"
        print(f"  top journey   : [{chips}] "
              f"exposed={int(top.get('exposed_n') or 0):,} "
              f"converted={int(top.get('converted_n') or 0):,} "
              f"rate={_fmt_pct(top.get('conversion_rate'))} "
              f"lift={float(top.get('lift_vs_baseline') or 0):.2f}x")
    # Top off-diagonal cell
    tps_ce = ce.get("touchpoints") or []
    mat = ce.get("matrix") or []
    if tps_ce and mat:
        best = (-1.0, -1, -1)
        for i, row in enumerate(mat):
            for j, v in enumerate(row):
                if i == j:
                    continue
                v = float(v or 0)
                if v > best[0]:
                    best = (v, i, j)
        if best[1] >= 0:
            a = tps_ce[best[1]].get("asset_title") or "?"
            b = tps_ce[best[2]].get("asset_title") or "?"
            print(f"  top co-exposure: {a[:26]} -> {b[:26]} @ "
                  f"{_fmt_pct(best[0], 1)}")


def _verify_cache_bytes(slug: str, as_of: str) -> int:
    import boto3  # type: ignore
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-2"))
    key = f"intent/{slug}/mta/coefficients_{as_of}.json"
    try:
        head = s3.head_object(Bucket="dashboard-inputs", Key=key)
        return int(head["ContentLength"])
    except Exception as e:
        print(f"  [verify] head failed for {key}: {e}", file=sys.stderr)
        return -1


def main() -> int:
    import mta_iq  # type: ignore
    # Enabled campaigns per the standing MTA rollout (registry-driven, so
    # a future enable ships without touching this script).
    slugs = _load_enabled_campaigns()
    if not slugs:
        print("no campaigns with enabled_tabs.mta = true", file=sys.stderr)
        return 1
    print(f"MTA v2 precompute for {len(slugs)} campaigns: {slugs}")

    # Small check: reject Omaze UK by construction so a registry drift can't
    # silently enable it here.
    for s in slugs:
        if "omaze" in s.lower() and "uk" in s.lower():
            print(f"  [guard] Omaze UK ({s}) is not eligible for MTA; skipping",
                  file=sys.stderr)
    slugs = [s for s in slugs if not ("omaze" in s.lower() and "uk" in s.lower())]

    failures = []
    for slug in slugs:
        try:
            payload = mta_iq.compute_mta_coefficients(slug, use_cache=False)
        except Exception as e:
            print(f"[{slug}] compute raised: {e}", file=sys.stderr)
            failures.append(slug)
            continue
        _summarize(payload)
        as_of = payload.get("as_of") or ""
        if as_of:
            size = _verify_cache_bytes(slug, as_of)
            print(f"  cache bytes   : {size:,}" if size > 0 else
                  f"  cache bytes   : <head miss>")

    print("\n──────────────────────────────────────────────────────────────")
    if failures:
        print(f"FAILED slugs: {failures}")
        return 2
    print(f"OK. {len(slugs)} campaigns cached with schema_version=2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
