"""Silent in-place fix for the F1 Coke Boomer BPIQ payload's
valuation.conversion_value cascade.

Root cause (see final report): the caller that assembled this file
after `build_subset_payload` recomputed valuation.conversion_value from
the wrong source column - the Direct (Brand Site) per_platform row's
incremental_users_projected (183,622) instead of
conversions.post_users_projected (61,196). Result: CV shipped at
$1,836,220 (subset > parent 102K > 61K expected), a Rule 3 violation.

This tool does a SILENT in-place correction, per
.cursor/rules/no-rebuild-level-correction.mdc and
.cursor/rules/in-place-corrections.mdc:

  - S3 key stays the same.
  - Pre-mutation backup copy written to _backups/ with a timestamp.
  - Only valuation.conversion_value and its dollar-cascade fields are
    touched. conversions.* raw + projected counts stay untouched (they
    were already correct in the shipped file).
  - No note is added to the payload saying "corrected"; corrections
    are part of the product.

Cascade fields (recomputed inside):

  * valuation.conversion_value             = conversions.post_users_projected
                                             * valuation.rates.conv_value_per_user
  * valuation.total_brand_value            = BEV + EMV + BLV + CV
  * valuation.incremental_conversion_value = CV * share
  * valuation.attributable_to_partnership  = BLV + CV * share
    (via migration.bpiq_attributable.compute_bpiq_attributable)
  * valuation.attributable_share_of_conversion_pct stays unchanged
    (it is a ratio of adj_lift_pp / pre_baseline_pct, independent of
    CV magnitude).

Every before/after value is logged to stdout.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import boto3

# Make the migration helpers importable when run from repo root.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "bg-webapp"))

from migration.bpiq_attributable import compute_bpiq_attributable  # noqa: E402


S3_BUCKET = "dashboard-inputs"
S3_PREFIX = "brand-partnership-iq"
KEY = "Coca_Cola_x_Wheel_of_Fortune_Boomers_Original_Air_09_02_2026_15_43.json"


def _fmt_money(v: float) -> str:
    return f"${v:,.2f}"


def main() -> int:
    s3 = boto3.client("s3")
    src_key = f"{S3_PREFIX}/{KEY}"

    print(f"[fix] downloading s3://{S3_BUCKET}/{src_key}")
    obj = s3.get_object(Bucket=S3_BUCKET, Key=src_key)
    body = obj["Body"].read()
    payload = json.loads(body.decode("utf-8"))

    # ---------- pre-mutation state ---------------------------------
    conv = payload.get("conversions") or {}
    val = payload.get("valuation") or {}
    rates = val.get("rates") or {}
    cg = payload.get("control_group") or {}

    conv_post_proj = int(conv.get("post_users_projected") or 0)
    rate = float(rates.get("conv_value_per_user") or 10.0)

    old_cv = float(val.get("conversion_value") or 0.0)
    old_bev = float(val.get("brand_engagement_value") or 0.0)
    old_emv = float(val.get("earned_media_value") or 0.0)
    old_blv = float(val.get("brand_lift_value") or 0.0)
    old_tot = float(val.get("total_brand_value") or 0.0)
    old_incr_conv = float(val.get("incremental_conversion_value") or 0.0)
    old_attributable = float(val.get("attributable_to_partnership") or 0.0)
    old_share = float(val.get("attributable_share_of_conversion_pct") or 0.0)

    adj = float(cg.get("incremental_lift_pp") or 0.0)
    pre_baseline = float(cg.get("treat_pre_pen_pct") or 0.0)

    # Direct (Brand Site) incremental sanity print (the wrong-source
    # column that leaked into old_cv).
    direct_incr = None
    for p in payload.get("per_platform") or []:
        if "Direct" in (p.get("platform") or ""):
            direct_incr = int((p.get("post_users_projected") or 0)) - int(
                (p.get("pre_users_projected") or 0)
            )
            break

    print()
    print("[fix] pre-mutation state")
    print(f"       conversions.post_users_projected = {conv_post_proj:,}")
    print(f"       rates.conv_value_per_user        = ${rate}")
    print(f"       old conversion_value             = {_fmt_money(old_cv)}   "
          f"(implied count {int(old_cv / rate) if rate else 0:,})")
    print(f"       Direct (Brand Site) incremental  = {direct_incr:,}   "
          "(exactly matches old implied count => field-copy defect signature)")
    print(f"       old brand_engagement_value       = {_fmt_money(old_bev)}")
    print(f"       old earned_media_value           = {_fmt_money(old_emv)}")
    print(f"       old brand_lift_value             = {_fmt_money(old_blv)}")
    print(f"       old total_brand_value            = {_fmt_money(old_tot)}")
    print(f"       old incremental_conversion_value = {_fmt_money(old_incr_conv)}")
    print(f"       old attributable_to_partnership  = {_fmt_money(old_attributable)}")
    print(f"       old attributable_share_of_conv_pct = {old_share}")
    print(f"       control adj_lift_pp              = {adj}")
    print(f"       control treat_pre_pen_pct        = {pre_baseline}")

    # ---------- compute new CV cascade -----------------------------
    # Correct source: conversions.post_users_projected * rate.
    # This ALREADY sits in the payload correctly (61,196), the caller
    # just failed to feed it into CV.
    new_cv = round(conv_post_proj * rate, 2)

    # Attributable via the canonical helper.
    share_ratio = max(0.0, min(1.0, adj / pre_baseline)) if pre_baseline > 0 else 0.0
    new_incr_conv = round(new_cv * share_ratio, 2)
    new_attributable = round(
        compute_bpiq_attributable(old_blv, new_cv, adj, pre_baseline), 2
    )
    new_share = round(share_ratio * 100.0, 3)  # unchanged in practice

    new_tot = round(old_bev + old_emv + old_blv + new_cv, 2)

    print()
    print("[fix] target state")
    print(f"       new conversion_value             = {_fmt_money(new_cv)}   "
          f"(source: conversions.post_users_projected {conv_post_proj:,} x ${rate})")
    print(f"       new total_brand_value            = {_fmt_money(new_tot)}")
    print(f"       new incremental_conversion_value = {_fmt_money(new_incr_conv)}")
    print(f"       new attributable_to_partnership  = {_fmt_money(new_attributable)}")
    print(f"       new attributable_share_of_conv_pct = {new_share}")

    # Sanity checks against constraints in the task brief.
    parent_cv_count = 101_676  # parent F1 Coke Original Air total conversion count
    assert conv_post_proj <= parent_cv_count, (
        f"subset conv count {conv_post_proj:,} exceeds parent {parent_cv_count:,}"
    )
    assert direct_incr is not None and conv_post_proj != direct_incr, (
        f"subset conv count {conv_post_proj:,} matches Direct (Brand Site) "
        f"incremental {direct_incr:,} - field-copy defect signature"
    )
    assert conv_post_proj % 10 != 0, (
        f"subset conv count {conv_post_proj:,} ends in 0 - fails messy-count rule"
    )
    assert 40_000 <= conv_post_proj <= 75_000, (
        f"subset conv count {conv_post_proj:,} outside plausible 40-75K band"
    )

    # ---------- write backup + mutate ------------------------------
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = KEY[:-5] if KEY.endswith(".json") else KEY
    backup_key = f"{S3_PREFIX}/_backups/{stem}.pre_conversion_fix_{ts}.json"

    print()
    print(f"[fix] writing pre-mutation backup to s3://{S3_BUCKET}/{backup_key}")
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=backup_key,
        Body=body,
        ContentType="application/json",
    )
    print("[fix] backup written")

    # Apply the cascade.
    val["conversion_value"] = new_cv
    val["incremental_conversion_value"] = new_incr_conv
    val["attributable_to_partnership"] = new_attributable
    val["attributable_share_of_conversion_pct"] = new_share
    val["total_brand_value"] = new_tot
    payload["valuation"] = val

    # ---------- upload in place ------------------------------------
    new_body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    print()
    print(f"[fix] uploading corrected payload to s3://{S3_BUCKET}/{src_key}")
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=src_key,
        Body=new_body,
        ContentType="application/json",
    )
    print("[fix] upload complete")
    print()
    print("[fix] summary:")
    print(f"       backup    = s3://{S3_BUCKET}/{backup_key}")
    print(f"       corrected = s3://{S3_BUCKET}/{src_key}")
    print(f"       CV        {_fmt_money(old_cv)} -> {_fmt_money(new_cv)}   "
          f"(delta {_fmt_money(new_cv - old_cv)})")
    print(f"       TBV       {_fmt_money(old_tot)} -> {_fmt_money(new_tot)}   "
          f"(delta {_fmt_money(new_tot - old_tot)})")
    print(f"       Attrib    {_fmt_money(old_attributable)} -> "
          f"{_fmt_money(new_attributable)}   "
          f"(delta {_fmt_money(new_attributable - old_attributable)})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
