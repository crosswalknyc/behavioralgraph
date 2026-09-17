#!/usr/bin/env python3
"""Precompute + cache MTA v5 payloads for every campaign with the tab on.

Same shape as ``precompute_mta_v4_paths_caches.py`` (the v4 sibling this
script replaces on the standing precompute path). What v5 changes on top
of v4:

  * Every touchpoint row on every slice now carries the two odds-ratio
    95% bands (``odds_ratio_low_95`` + ``odds_ratio_high_95``, both
    clamped to ``[0.01, 100]``) so the coefficient card can render the
    "1.20 (95% CI 1.08 to 1.34)" line inline for a media buyer.
  * Every paths nest row now leads with the funnel-stage prefix
    (``Top of funnel:``, ``Mid funnel:``, ``Lower funnel:``,
    ``Conversion:``) so the ladder reads as a tier without the reader
    having to cross-reference the stage code.

Both the loader and the writer bump ``mta_iq.SCHEMA_VERSION`` from 4
to 5, so any v4 cache the loader picks up is treated as stale and
force-rebuilt in place under the same S3 key. This script forces the
rebuild explicitly (``use_cache=False``) so a stale cache can never
survive a run.

Reads ``enabled_tabs.mta`` from every entry in
``s3://dashboard-inputs/intent/registry.json``; kicks
``compute_mta_coefficients(slug)`` for each hit. Six campaigns today:
goat, chime, chime_financial_mypay, doordash_the_big_beef,
the_influencer_project_hades, dhar_mann_minions_and_monsters.

The pre-mutation v4 cache is copied to the same key under
``_backups/`` with a ``.pre_v5_<ts>.json`` suffix so an operator can
one-line revert.

Prints one section per campaign:
    * overall sample size + baseline conversion + top-3 touchpoints
      (with odds ratio + 95% band) + paths nest first-row prefix check
    * per-audience: cohort_size + baseline conversion + read strength
      + top-1 touchpoint (asset + coefficient + OR band) + paths nest
      exposed count
    * any audiences that got skipped (overlap_bp missing or zero)
    * cache bytes on S3 after the write

Environment: needs ``CH_HOST/CH_PORT/CH_USER/CH_PASSWORD`` for the
S3-backed overview + assets lookups plus the audiences catalog, and
``AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY`` for S3 cache reads +
writes.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime
from pathlib import Path


# path setup: bg-webapp/scripts/ -> bg-webapp/ on sys.path
HERE = Path(__file__).resolve().parent
BGWEBAPP = HERE.parent
sys.path.insert(0, str(BGWEBAPP))


# Overwrite target date is fixed so every campaign lands under the same
# key on the same date, matching the request in the operator ask.
AS_OF_TARGET = "2026-09-17"

# Funnel-stage prefix set. Every v5 nest row's label MUST lead with one
# of these; the check below is a shape guard on the label copy so a
# stale label build can't slip through.
_STAGE_PREFIXES = {
    "1_exposed":  "Top of funnel:",
    "2_infoseek": "Mid funnel:",
    "3_ticketer": "Lower funnel:",
    "4_paid":     "Conversion:",
}


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


def _backup_prev_v4_cache(slug: str, as_of: str) -> str | None:
    """Copy the current cache (if any) to _backups/ with a .pre_v5_<ts>.json
    suffix. Returns the backup key on success, None on miss / error."""
    import boto3  # type: ignore
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-2"))
    src_key = f"intent/{slug}/mta/coefficients_{as_of}.json"
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    dst_key = f"_backups/{src_key}.pre_v5_{ts}.json"
    try:
        # HEAD first so a missing-source is a silent skip (fresh campaigns
        # that never had a v4 cache on this date land here).
        s3.head_object(Bucket="dashboard-inputs", Key=src_key)
    except Exception:
        return None
    try:
        s3.copy_object(
            Bucket="dashboard-inputs",
            Key=dst_key,
            CopySource={"Bucket": "dashboard-inputs", "Key": src_key},
        )
        return dst_key
    except Exception as e:
        print(f"  [backup] copy failed for {src_key} -> {dst_key}: {e}",
              file=sys.stderr)
        return None


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
        print(f"{indent}  {r.get('stage'):12s} {(r.get('label') or '')[:64]:64s} "
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


def _assert_v5_shape(slice_p: dict, label: str) -> list[str]:
    """Verify the v5 invariants on the cached slice.

    * every non-'0_tam' nest row's label leads with the funnel-stage
      prefix from _STAGE_PREFIXES
    * every touchpoint row carries odds_ratio + odds_ratio_low_95 +
      odds_ratio_high_95 numeric fields
    * odds_ratio_low_95 and odds_ratio_high_95 both sit inside the
      clamp band [0.01, 100]
    * the clamped odds ratio (max(0.01, min(100.0, odds_ratio))) is
      bracketed by the band. Per spec, only the interval is clamped;
      ``odds_ratio`` itself is left as ``exp(coefficient)`` so a
      pathological saturating cohort can legitimately expose a
      point-estimate outside the clamp band. Comparing the CLAMPED
      point-estimate to the band is what "the CI brackets the reading
      inside the display band" means in practice.
    """
    errs: list[str] = []
    paths = slice_p.get("paths") or {}
    nest = paths.get("nest") or []
    for r in nest:
        stage = r.get("stage")
        label_str = str(r.get("label") or "")
        prefix = _STAGE_PREFIXES.get(stage)
        if prefix is None:
            continue  # 0_tam has no prefix rule
        if not label_str.startswith(prefix):
            errs.append(
                f"{label}: nest[{stage}].label lacks '{prefix}' prefix: {label_str!r}"
            )
    tps = slice_p.get("touchpoints") or []
    for tp in tps:
        for key in ("odds_ratio", "odds_ratio_low_95", "odds_ratio_high_95"):
            v = tp.get(key)
            if v is None:
                errs.append(f"{label}: touchpoint {tp.get('touchpoint_id')} lacks {key}")
                break
        else:
            orp = float(tp["odds_ratio"])
            lo = float(tp["odds_ratio_low_95"])
            hi = float(tp["odds_ratio_high_95"])
            if lo < 0.01 - 1e-6 or hi > 100.0 + 1e-6:
                errs.append(
                    f"{label}: touchpoint {tp.get('touchpoint_id')} band [{lo}, {hi}] "
                    f"outside clamp [0.01, 100]"
                )
            if lo > hi + 1e-6:
                errs.append(
                    f"{label}: touchpoint {tp.get('touchpoint_id')} band [{lo}, {hi}] "
                    f"inverted (low > high)"
                )
            clamped_or = max(0.01, min(100.0, orp))
            if not (lo - 1e-6 <= clamped_or <= hi + 1e-6):
                errs.append(
                    f"{label}: touchpoint {tp.get('touchpoint_id')} band [{lo}, {hi}] "
                    f"does not bracket clamped odds_ratio {clamped_or} "
                    f"(raw odds_ratio={orp})"
                )
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
        if cohort_meta.get("category"):
            line2 += f"  category={cohort_meta.get('category')}"
        if cohort_meta.get("thin_read"):
            line2 += "  THIN_READ"
    print(line2)
    if tps:
        top = tps[:show_top_n]
        for r in top:
            title = (r.get("asset_title") or "")[:30]
            channel = (r.get("channel") or "")[:10]
            coef = r.get("coefficient", 0)
            orp = r.get("odds_ratio", 1.0)
            lo = r.get("odds_ratio_low_95", orp)
            hi = r.get("odds_ratio_high_95", orp)
            print(f"{indent}    - {title:30s} [{channel}]  "
                  f"coef={coef:+.4f}  OR={orp:.3f} (CI {lo:.3f} to {hi:.3f})")
    if show_paths and slice_p.get("paths"):
        _summarize_paths(slice_p["paths"], indent=indent + "  ")


def _summarize_wrapper(payload: dict) -> list[str]:
    """Print a compact summary of the wrapper + verify v5 invariants on
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
    errs.extend(_assert_v5_shape(overall, f"{slug}::overall"))

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
            errs.extend(_assert_v5_shape(
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


def _refetch_and_verify_schema(slug: str, as_of: str) -> tuple[int, int, bool]:
    """Fetch the cache back from S3 and confirm schema_version + nest row
    count + odds-ratio-band presence on the overall slice. Returns
    (schema_version, nest_len, has_or_band) so the caller can gate on all
    three."""
    import boto3  # type: ignore
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-2"))
    key = f"intent/{slug}/mta/coefficients_{as_of}.json"
    try:
        resp = s3.get_object(Bucket="dashboard-inputs", Key=key)
        p = json.loads(resp["Body"].read().decode("utf-8"))
    except Exception as e:
        print(f"  [refetch] get failed for {key}: {e}", file=sys.stderr)
        return (-1, -1, False)
    sv = int(p.get("schema_version") or 0)
    overall = p.get("overall") or {}
    paths = overall.get("paths") or {}
    nest = paths.get("nest") or []
    tps = overall.get("touchpoints") or []
    has_or = bool(tps) and ("odds_ratio_low_95" in (tps[0] or {}))
    return (sv, len(nest), has_or)


def main() -> int:
    import mta_iq  # type: ignore
    if int(mta_iq.SCHEMA_VERSION) != 5:
        print(f"mta_iq.SCHEMA_VERSION is {mta_iq.SCHEMA_VERSION}, expected 5; "
              "code out of sync with this script",
              file=sys.stderr)
        return 4
    slugs = _load_enabled_campaigns()
    if not slugs:
        print("no campaigns with enabled_tabs.mta = true", file=sys.stderr)
        return 1
    print(f"MTA v5 precompute for {len(slugs)} campaigns: {slugs}")

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
    backup_keys: list[str] = []
    for slug in slugs:
        # Back up the pre-existing v4 cache (if any) before the overwrite.
        b = _backup_prev_v4_cache(slug, AS_OF_TARGET)
        if b:
            print(f"\n[{slug}] backed up prior cache to s3://dashboard-inputs/{b}")
            backup_keys.append(f"s3://dashboard-inputs/{b}")
        else:
            print(f"\n[{slug}] no prior cache at as_of={AS_OF_TARGET} (fresh write)")
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
            sv, nest_len, has_or = _refetch_and_verify_schema(slug, as_of)
            if sv == 5 and nest_len == 5 and has_or:
                print(f"  refetch verify: schema_version={sv}  "
                      f"overall.paths.nest rows={nest_len}  "
                      f"odds_ratio_low_95 on touchpoint[0]=yes  OK")
                cache_keys.append(f"s3://dashboard-inputs/"
                                    f"intent/{slug}/mta/coefficients_{as_of}.json")
            else:
                print(f"  refetch verify FAILED: schema_version={sv}  "
                      f"nest_len={nest_len}  has_or_band={has_or}",
                      file=sys.stderr)
                invariant_errs.append(
                    f"{slug}: refetch schema={sv} nest={nest_len} or_band={has_or}"
                )

    print("\n" + "-" * 66)
    if failures:
        print(f"FAILED slugs: {failures}")
        return 2
    if invariant_errs:
        print(f"INVARIANT FAILURES ({len(invariant_errs)}):")
        for e in invariant_errs:
            print(f"  - {e}")
        return 3
    print(f"OK. {len(slugs)} campaigns cached with schema_version=5.")
    print("\nBackup keys (pre-mutation v4 caches, one per campaign that had one):")
    for k in backup_keys:
        print(f"  {k}")
    print("\nCache keys:")
    for k in cache_keys:
        print(f"  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
