#!/usr/bin/env python3
"""Precompute + cache MTA v4 paths-to-conversion payloads for every campaign
with the tab on.

Reads `enabled_tabs.mta` from every entry in `metadata/intent_registry.json`;
kicks compute_mta_coefficients(slug) for each hit. Because the fitter now
carries schema_version 4, any v1 / v2 / v3 cache the loader picks up is
treated as stale and force-rebuilt in place under the same S3 key. The
rebuild writes a nested wrapper (``overall`` + ``audiences``) where every
slice now carries a ``paths`` block next to ``touchpoints``, ``journeys``,
and ``co_exposure`` -- one round trip powers the entire multi-touch card
strip on the dashboard.

Prints one section per campaign:
    * overall sample size + baseline conversion + top-3 coefficients +
      top journey signature + paths nest (5 rows)
    * per-audience: cohort_size + baseline conversion + read strength +
      top-1 touchpoint (asset + coefficient) + paths nest exposed count
    * any audiences that got skipped (overlap_bp missing or zero)
    * cache bytes on S3 after the write

Environment: needs `CH_HOST/CH_PORT/CH_USER/CH_PASSWORD` for the S3-backed
overview + assets lookups plus the audiences catalog, and
`AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY` for S3 cache writes.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path


# path setup: bg-webapp/scripts/ -> bg-webapp/ on sys.path
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


def _summarize_paths(paths: dict, indent: str = "    ") -> None:
    """Print the 5-row nest + a one-liner for each of forks / where /
    attribution / time / archetypes / leaks so the run log is enough to
    confirm the cache is healthy without re-fetching."""
    if not paths:
        print(f"{indent}(no paths block)")
        return
    nest = paths.get("nest") or []
    print(f"{indent}paths.nest ({len(nest)} rows):")
    for r in nest:
        drop = r.get("drop_from_prior")
        drop_s = f"{int(drop):,}" if drop is not None else "-"
        print(f"{indent}  {r.get('stage'):12s} {(r.get('label') or '')[:56]:56s} "
              f"US={int(r.get('us_accounts') or 0):>13,d}  "
              f"share={float(r.get('share_of_us_gen_pop_pct') or 0):>7.4f}%  "
              f"drop={drop_s:>13s}")
    print(f"{indent}  conversion_noun={paths.get('conversion_noun')}  "
          f"panel={int(paths.get('panel_sample') or 0):,}  "
          f"us_projection_factor={paths.get('us_projection_factor')}")
    forks = paths.get("forks") or []
    print(f"{indent}  forks: " + ", ".join(
        f"{f.get('of_stage')}({int(f.get('yes') or 0):,}/{int(f.get('no') or 0):,})"
        for f in forks
    ))
    tp = (paths.get("where") or {}).get("ticketer_partition") or []
    print(f"{indent}  where.ticketer_partition: " + ", ".join(
        f"{r.get('surface')} {float(r.get('pct') or 0):.1f}%" for r in tp
    ))
    ft = (paths.get("attribution") or {}).get("first_touch") or []
    print(f"{indent}  attribution.first_touch: " + ", ".join(
        f"{r.get('touchpoint')} {float(r.get('pct') or 0):.1f}%" for r in ft
    ))
    ttc = paths.get("time_to_conversion") or []
    print(f"{indent}  time_to_conversion: " + ", ".join(
        f"{r.get('bucket')} {float(r.get('pct') or 0):.1f}%" for r in ttc
    ))
    arch = paths.get("path_archetypes") or []
    print(f"{indent}  path_archetypes: " + ", ".join(
        f"{r.get('archetype')} {float(r.get('pct') or 0):.1f}%" for r in arch
    ))
    lk = paths.get("leaks") or []
    print(f"{indent}  leaks: " + ", ".join(
        f"{l.get('leak')} ({int(l.get('us_accounts') or 0):,})" for l in lk
    ))


def _assert_slice_paths_invariants(slice_p: dict, label: str) -> list[str]:
    """Re-verify the invariants on the cached slice. Returns a list of
    error strings; empty list means all good. Mirrors the asserts in
    mta_iq._assert_paths_invariants for defense-in-depth on ship."""
    errs: list[str] = []
    paths = slice_p.get("paths") or {}
    nest = paths.get("nest") or []
    if len(nest) != 5:
        return [f"{label}: nest must have 5 rows, got {len(nest)}"]
    # Monotonicity
    for i in range(1, 5):
        if int(nest[i]["us_accounts"]) > int(nest[i - 1]["us_accounts"]):
            errs.append(f"{label}: nest[{i}] > nest[{i-1}]")
    def _stage(k):
        for r in nest:
            if r["stage"] == k:
                return int(r["us_accounts"])
        return 0
    stage2, stage3, stage4 = _stage("2_infoseek"), _stage("3_ticketer"), _stage("4_paid")
    # Partition sums
    for key in ("first_touch", "last_touch"):
        s = sum(int(r["us_accounts"]) for r in (paths.get("attribution") or {}).get(key) or [])
        if s != stage4:
            errs.append(f"{label}: attribution.{key} sum {s} != stage4 {stage4}")
    tp = (paths.get("where") or {}).get("ticketer_partition") or []
    s = sum(int(r["us_accounts"]) for r in tp)
    if s != stage3:
        errs.append(f"{label}: where.ticketer_partition sum {s} != stage3 {stage3}")
    for key in ("time_to_conversion", "path_archetypes"):
        s = sum(int(r["us_accounts"]) for r in paths.get(key) or [])
        if s != stage4:
            errs.append(f"{label}: {key} sum {s} != stage4 {stage4}")
    # Overlap caps
    for r in (paths.get("where") or {}).get("infoseek_overlap") or []:
        if int(r["us_accounts"]) > stage2:
            errs.append(f"{label}: infoseek_overlap {r.get('surface')} > stage2")
    for r in (paths.get("attribution") or {}).get("assists") or []:
        if int(r["us_accounts"]) > stage4:
            errs.append(f"{label}: assists {r.get('touchpoint')} > stage4")
    # Leak math
    leaks = paths.get("leaks") or []
    if len(leaks) < 2:
        errs.append(f"{label}: leaks < 2 rows")
    else:
        if int(leaks[0]["us_accounts"]) + stage3 != stage2:
            errs.append(f"{label}: leak math infoseek+ticketer != infoseek_base")
        if int(leaks[1]["us_accounts"]) + stage4 != stage3:
            errs.append(f"{label}: leak math visit_no_pay+paid != ticketer_base")
    return errs


def _summarize_slice(label: str, slice_p: dict, *, indent: str = "    ",
                      cohort_meta: dict | None = None,
                      show_top_n: int = 3,
                      show_paths: bool = True) -> None:
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
    if show_paths and slice_p.get("paths"):
        _summarize_paths(slice_p["paths"], indent=indent + "  ")


def _summarize_wrapper(payload: dict) -> list[str]:
    """Print a compact summary of the wrapper + verify invariants on
    every slice. Returns a list of invariant-fail strings (empty means
    all slices passed)."""
    slug = payload.get("campaign_slug", "?")
    schema_v = payload.get("schema_version")
    display = payload.get("display_name", slug)
    print(f"\n----- {slug}  ({display})  schema_v{schema_v} -----")
    print(f"  cache_key: s3://dashboard-inputs/{payload.get('cache_key', '?')}")
    overall = payload.get("overall") or {}
    _summarize_slice("OVERALL (all exposed viewers)", overall, indent="  ",
                      show_paths=True)

    errs: list[str] = []
    errs.extend(_assert_slice_paths_invariants(overall, f"{slug}::overall"))

    audiences = payload.get("audiences") or {}
    skipped = payload.get("audiences_skipped") or []
    if audiences:
        print(f"\n  AUDIENCES ({len(audiences)} slices cached):")
        for aud_slug, slice_p in audiences.items():
            meta = slice_p.get("cohort_meta") or {}
            label = meta.get("audience_label") or aud_slug
            heading = f"[{aud_slug}]  {label}"
            _summarize_slice(heading, slice_p, indent="    ",
                              cohort_meta=meta, show_top_n=1,
                              show_paths=False)
            paths = slice_p.get("paths") or {}
            n = paths.get("nest") or []
            if n:
                paid = next((int(r["us_accounts"]) for r in n
                              if r["stage"] == "4_paid"), 0)
                exp = next((int(r["us_accounts"]) for r in n
                              if r["stage"] == "1_exposed"), 0)
                print(f"      paths: exposed={exp:,}  paid={paid:,}")
            errs.extend(_assert_slice_paths_invariants(
                slice_p, f"{slug}::aud:{aud_slug}"))
    else:
        print("  AUDIENCES: (none cached)")
    if skipped:
        print(f"\n  SKIPPED ({len(skipped)}):")
        for s in skipped:
            reason = s.get("reason") or "unspecified"
            print(f"    - [{s.get('audience_slug')}]  "
                  f"{s.get('audience_label')}  ({reason})")
    return errs


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


def _refetch_and_verify_schema(slug: str, as_of: str) -> tuple[int, int]:
    """Fetch the cache back from S3 and confirm schema_version + nest
    row count on the overall slice. Returns (schema_version, nest_len)
    so the caller can gate on both."""
    import boto3  # type: ignore
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-2"))
    key = f"intent/{slug}/mta/coefficients_{as_of}.json"
    try:
        resp = s3.get_object(Bucket="dashboard-inputs", Key=key)
        p = json.loads(resp["Body"].read().decode("utf-8"))
    except Exception as e:
        print(f"  [refetch] get failed for {key}: {e}", file=sys.stderr)
        return (-1, -1)
    sv = int(p.get("schema_version") or 0)
    overall = p.get("overall") or {}
    paths = overall.get("paths") or {}
    nest = paths.get("nest") or []
    return (sv, len(nest))


# Overwrite target date is fixed so every campaign lands under the same
# key on the same date, matching the request in the operator ask.
AS_OF_TARGET = "2026-09-17"


def main() -> int:
    import mta_iq  # type: ignore
    slugs = _load_enabled_campaigns()
    if not slugs:
        print("no campaigns with enabled_tabs.mta = true", file=sys.stderr)
        return 1
    print(f"MTA v4 paths precompute for {len(slugs)} campaigns: {slugs}")

    # Guardrail: Omaze UK stays off MTA; reject it here so a registry
    # drift can't silently enable it.
    for s in slugs:
        if "omaze" in s.lower() and "uk" in s.lower():
            print(f"  [guard] Omaze UK ({s}) is not eligible for MTA; skipping",
                  file=sys.stderr)
    slugs = [s for s in slugs if not ("omaze" in s.lower() and "uk" in s.lower())]

    failures: list[str] = []
    invariant_errs: list[str] = []
    cache_keys: list[str] = []
    for slug in slugs:
        try:
            payload = mta_iq.compute_mta_coefficients(
                slug, as_of=AS_OF_TARGET, use_cache=False,
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[{slug}] compute raised: {e}", file=sys.stderr)
            failures.append(slug)
            continue
        if not payload.get("success"):
            print(f"[{slug}] compute returned success=False: "
                  f"{payload.get('error')}", file=sys.stderr)
            failures.append(slug)
            continue
        errs = _summarize_wrapper(payload)
        if errs:
            invariant_errs.extend(errs)
            for e in errs:
                print(f"  INVARIANT FAIL: {e}", file=sys.stderr)
        as_of = payload.get("as_of") or ""
        if as_of:
            size = _verify_cache_bytes(slug, as_of)
            if size > 0:
                print(f"  cache bytes on S3: {size:,}")
            else:
                print("  cache bytes on S3: <head miss>")
            sv, nest_len = _refetch_and_verify_schema(slug, as_of)
            if sv == 4 and nest_len == 5:
                print(f"  refetch verify: schema_version={sv}  "
                      f"overall.paths.nest rows={nest_len}  OK")
                cache_keys.append(f"s3://dashboard-inputs/"
                                    f"intent/{slug}/mta/coefficients_{as_of}.json")
            else:
                print(f"  refetch verify FAILED: schema_version={sv}  "
                      f"nest_len={nest_len}", file=sys.stderr)
                invariant_errs.append(f"{slug}: refetch schema={sv} nest={nest_len}")

    print("\n" + "-" * 66)
    if failures:
        print(f"FAILED slugs: {failures}")
        return 2
    if invariant_errs:
        print(f"INVARIANT FAILURES ({len(invariant_errs)}):")
        for e in invariant_errs:
            print(f"  - {e}")
        return 3
    print(f"OK. {len(slugs)} campaigns cached with schema_version=4.")
    print("\nCache keys:")
    for k in cache_keys:
        print(f"  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
