#!/usr/bin/env python3
"""Precompute + cache MTA v3 audience-nested payloads for every campaign
with the tab on.

Reads `enabled_tabs.mta` from every entry in `metadata/intent_registry.json`;
kicks compute_mta_coefficients(slug) for each hit. Because the fitter now
carries schema_version 3, any v1 or v2 cache the loader picks up is
treated as stale and force-rebuilt in place under the same S3 key. The
rebuild writes a nested wrapper (``overall`` + ``audiences``) so the
browser-side audience dropdown swap is one round trip.

Prints one section per campaign:
    * overall sample size + baseline conversion + top-3 coefficients +
      top journey signature
    * per-audience: cohort_size + baseline conversion + read strength +
      top-1 touchpoint (asset + coefficient)
    * any audiences that got skipped (overlap_bp missing or zero)

Environment: needs `CH_HOST/CH_PORT/CH_USER/CH_PASSWORD` for the S3-backed
overview + assets lookups plus the audiences catalog, and
`AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY` for S3 cache writes.
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


def _summarize_slice(label: str, slice_p: dict, *, indent: str = "    ",
                      cohort_meta: dict | None = None,
                      show_top_n: int = 3) -> None:
    ss = int(slice_p.get("sample_size") or 0)
    br = float(slice_p.get("conversion_rate") or 0.0)
    fit = slice_p.get("model_fit") or {}
    strength = str(fit.get("quality") or "weak")
    tps = slice_p.get("touchpoints") or []
    js = slice_p.get("journeys") or []
    print(f"{indent}{label}")
    line2 = (
        f"{indent}  cohort_size={ss:,}  baseline={_fmt_pct(br)}  "
        f"read_strength={strength}"
    )
    if cohort_meta:
        overlap = cohort_meta.get("overlap_bp")
        if overlap is not None:
            try:
                line2 += f"  overlap_bp={float(overlap):.2f}%"
            except Exception:
                pass
        if cohort_meta.get("thin_read"):
            line2 += "  THIN_READ"
    print(line2)
    if tps:
        top = tps[:show_top_n]
        for r in top:
            title = (r.get("asset_title") or "")[:34]
            channel = (r.get("channel") or "")[:12]
            coef = r.get("coefficient", 0)
            print(f"{indent}    - {title}  [{channel}]  coef={coef:+.4f}")
    if js:
        top_j = max(js, key=lambda r: int(r.get("converted_n") or 0))
        chips = " + ".join((t.get("asset_title") or "")[:22]
                            for t in (top_j.get("touchpoints") or [])[:3])
        more = len(top_j.get("touchpoints") or []) - 3
        if more > 0:
            chips += f" (+{more} more)"
        print(f"{indent}    top journey: [{chips}] "
              f"exp={int(top_j.get('exposed_n') or 0):,} "
              f"conv={int(top_j.get('converted_n') or 0):,} "
              f"lift={float(top_j.get('lift_vs_baseline') or 0):.2f}x")


def _summarize_wrapper(payload: dict) -> None:
    slug = payload.get("campaign_slug", "?")
    schema_v = payload.get("schema_version")
    display = payload.get("display_name", slug)
    print(f"\n── {slug}  ({display})  schema_v{schema_v} ─────────────────")
    print(f"  cache_key: s3://dashboard-inputs/{payload.get('cache_key', '?')}")
    overall = payload.get("overall") or {}
    _summarize_slice("OVERALL (all exposed viewers)", overall, indent="  ")

    audiences = payload.get("audiences") or {}
    skipped = payload.get("audiences_skipped") or []
    if audiences:
        print(f"\n  AUDIENCES ({len(audiences)} slices cached):")
        for aud_slug, slice_p in audiences.items():
            meta = slice_p.get("cohort_meta") or {}
            label = meta.get("audience_label") or aud_slug
            heading = f"[{aud_slug}]  {label}"
            _summarize_slice(heading, slice_p, indent="    ",
                              cohort_meta=meta, show_top_n=1)
    else:
        print("  AUDIENCES: (none cached)")
    if skipped:
        print(f"\n  SKIPPED ({len(skipped)}):")
        for s in skipped:
            reason = s.get("reason") or "unspecified"
            print(f"    - [{s.get('audience_slug')}]  {s.get('audience_label')}  ({reason})")


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
    slugs = _load_enabled_campaigns()
    if not slugs:
        print("no campaigns with enabled_tabs.mta = true", file=sys.stderr)
        return 1
    print(f"MTA v3 audience-nested precompute for {len(slugs)} campaigns: {slugs}")

    # Guardrail: Omaze UK stays off MTA; reject it here so a registry
    # drift can't silently enable it.
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
        if not payload.get("success"):
            print(f"[{slug}] compute returned success=False: {payload.get('error')}",
                  file=sys.stderr)
            failures.append(slug)
            continue
        _summarize_wrapper(payload)
        as_of = payload.get("as_of") or ""
        if as_of:
            size = _verify_cache_bytes(slug, as_of)
            if size > 0:
                print(f"  cache bytes on S3: {size:,}")
            else:
                print("  cache bytes on S3: <head miss>")

    print("\n──────────────────────────────────────────────────────────────")
    if failures:
        print(f"FAILED slugs: {failures}")
        return 2
    print(f"OK. {len(slugs)} campaigns cached with schema_version=3.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
