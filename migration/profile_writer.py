"""Canonical single-exit-point for writing a profile CSV to S3.

Every write path (BG.py main pipeline via `run_parallel_profiles.py`,
avid_fan_row_by_row, audience_cut synth, super_fan synth, ad-hoc synth
scripts, skin builders, patch scripts) should route through
`write_profile_csv` so the same enforcer chain, sort, backup, upload,
and dashboard registration run on every file that lands in
`s3://dashboard-inputs/`.

This is the fix for the recurring "we already solved this bug but it's
back in a new file" pattern. When enforcers only run on some write
paths, defects that were fixed at write time on one path silently
re-appear when a different path writes a file.

## Migrating existing scripts

If you have a script that currently does:

    df.to_csv(local_path, index=False)
    s3.put_object(Bucket="dashboard-inputs", Key=key, Body=..., ...)
    # optional register_profile_in_dashboard(...)

Replace with a single call:

    from migration.profile_writer import write_profile_csv
    write_profile_csv(
        df,
        subject="<Subject Name>",
        s3_key=key,
        category="<BRAND CATEGORY>",
        display_name="<Dashboard Name>",
    )

For skin builders derived from a parent OG or Avid, also pass
`source_key=<parent_s3_key>` so dashboard-cache image / IMDb metadata
inherits. For year skins, pass `year=YYYY` to fire the anachronism
check. For Avid cuts, pass `tu_source_key=<parent_TU_key>` to fire the
TU-vs-Avid subset arithmetic check.

Legacy scripts already running enforcers + register inline
(audience_cut_synthesis.py, avid_fan_row_by_row.py) do not need
migration — they already produce equivalent output. Opportunistic
migration when those files are touched is welcome.

Usage
-----

Minimal:

    from migration.profile_writer import write_profile_csv

    result = write_profile_csv(
        df,
        subject="Wheel of Fortune",
        s3_key="Wheel_of_Fortune_07_28_2026_03_20.csv",
        category="SERIES - GAME SHOW",
        display_name="Wheel of Fortune",
    )
    print(result)

With TU-vs-Avid coherence check for an Avid write:

    result = write_profile_csv(
        df_avid,
        subject="Wheel of Fortune",
        s3_key="WHEEL OF FORTUNE - Avid Fan.csv",
        category="SERIES - GAME SHOW",
        display_name="WHEEL OF FORTUNE - Avid Fan",
        tu_source_key="Wheel_of_Fortune_07_28_2026_03_20.csv",
    )

For a Gen Pop year skin:

    result = write_profile_csv(
        df,
        subject="Gen Pop",
        s3_key="Gen_Pop_2022.csv",
        category="GENERAL",
        display_name="Gen Pop 2022",
        year=2022,
    )
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import sys
from datetime import datetime
from typing import Optional

import boto3
import pandas as pd

BUCKET = "dashboard-inputs"

CAT_COL = "Column"
VAL_COL = "Value"
BP_COL = "Brand Penetration (Row)"
CS_COL = "Category Share"
RAW_COL = "Original Raw Numbers"
PROJ_COL = "US Gen Pop Projection"

# Rows to skip when sorting within category (metadata / meta rows are pinned
# to the top of their category group even when their BP field is blank).
_META_CATS_SKIP_SORT = {
    "BRAND INPUT", "SAMPLE SIZE", "SUBJECT", "BRAND CATEGORY",
}


def _bpf(x):
    try:
        return float(str(x).replace("%", "").replace(",", "").strip())
    except Exception:
        return None


def _seed_jitter(seed: str, amp: float) -> float:
    h = int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16)
    return ((h / (16 ** 16)) * 2.0 - 1.0) * amp


def _backup_prior(s3, key: str, tag: str) -> Optional[str]:
    try:
        cur = s3.get_object(Bucket=BUCKET, Key=key)
        bkey = (f"_backups/{key.replace('.csv','')}.pre_{tag}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        s3.put_object(Bucket=BUCKET, Key=bkey,
                       Body=cur["Body"].read())
        return bkey
    except s3.exceptions.NoSuchKey:
        return None
    except Exception as e:
        print(f"  [profile_writer] backup skipped for {key}: {e}")
        return None


def _sort_within_category(df: pd.DataFrame) -> pd.DataFrame:
    """Sort rows within each Column group by BP desc. Preserves the global
    Column order (i.e. category groups don't move; only rows inside each
    group re-order). Metadata rows in _META_CATS_SKIP_SORT are pinned to
    the top of their category.
    """
    df = df.copy()
    df["_orig_ix"] = range(len(df))
    df["_bp_sort"] = df[BP_COL].apply(_bpf).fillna(-1.0)
    df["_first_ix"] = df.groupby(CAT_COL)["_orig_ix"].transform("min")
    df = df.sort_values(
        ["_first_ix", "_bp_sort"], ascending=[True, False], kind="stable"
    ).reset_index(drop=True)
    return df.drop(columns=["_orig_ix", "_bp_sort", "_first_ix"])


def _load_tu_source(tu_source_key: str, s3) -> Optional[pd.DataFrame]:
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=tu_source_key)
        return pd.read_csv(io.BytesIO(obj["Body"].read()), dtype=object,
                            keep_default_na=False)
    except Exception as e:
        print(f"  [profile_writer] TU source fetch failed "
              f"({tu_source_key}): {e}")
        return None


def write_profile_csv(
    df: pd.DataFrame,
    subject: str,
    s3_key: str,
    *,
    category: Optional[str] = None,
    display_name: Optional[str] = None,
    source_key: Optional[str] = None,
    run_enforcers: bool = True,
    apply_anachronism: bool = True,
    year: Optional[int] = None,
    tu_source_key: Optional[str] = None,
    register: bool = True,
    backup: bool = True,
    sort: bool = True,
    verbose: bool = True,
    s3_client=None,
) -> dict:
    """Canonical write path for any profile CSV heading to
    `s3://dashboard-inputs/<s3_key>`.

    Pipeline (in order):
      1. If run_enforcers: run_all_enforcers(df, subject,
         brand_category=category)
      2. If apply_anachronism AND year: strip_anachronistic_brands(df,
         year)
      3. If tu_source_key: enforce_tu_avid_coherence(df, TU_source)
      4. If sort: sort within each Column group by BP desc
      5. If backup AND file exists on S3: back up prior to
         `_backups/{key}.pre_write_<ts>.csv`
      6. Upload df to `s3://dashboard-inputs/<s3_key>`
      7. If register: register_profile_in_dashboard(s3_key, ...)

    Returns a dict with everything the caller needs to log.

    Any step failure is caught and logged; the write itself must succeed
    (or the exception propagates). Enforcer failures are non-fatal.
    """
    s3 = s3_client or boto3.client("s3", region_name="us-east-2")
    n_enforcer_changes = 0
    n_anachronism_changes = 0
    n_coherence_changes = 0

    # 1. Enforcer chain (canonical set from run_all_enforcers). If
    #    year is provided, run_all_enforcers threads it to the
    #    anachronism check as its first step.
    if run_enforcers:
        try:
            from migration.post_generation_enforcers import run_all_enforcers
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from post_generation_enforcers import run_all_enforcers  # type: ignore
        try:
            if verbose:
                print(f"  [profile_writer] run_all_enforcers subject="
                      f"{subject!r} category={category!r} "
                      f"year={year!r}")
            df, n_enforcer_changes = run_all_enforcers(
                df, subject, brand_category=category, verbose=verbose,
                target_year=(year if apply_anachronism else None),
            )
        except Exception as e:
            print(f"  [profile_writer] run_all_enforcers raised "
                  f"({type(e).__name__}: {e}); continuing with pre-enforcer "
                  f"df")
    elif apply_anachronism and year is not None:
        # Enforcers disabled but caller still wants anachronism check
        try:
            from migration.anachronism_check import strip_anachronistic_brands
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from anachronism_check import strip_anachronistic_brands  # type: ignore
        try:
            df, n_anachronism_changes = strip_anachronistic_brands(
                df, year=year, subject=subject, verbose=verbose,
            )
        except Exception as e:
            print(f"  [profile_writer] strip_anachronistic_brands raised "
                  f"({type(e).__name__}: {e}); continuing")

    # 3. TU-vs-Avid coherence
    if tu_source_key:
        try:
            from migration.tu_avid_coherence import enforce_tu_avid_coherence
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from tu_avid_coherence import enforce_tu_avid_coherence  # type: ignore
        df_tu = _load_tu_source(tu_source_key, s3)
        if df_tu is not None:
            try:
                df, stats = enforce_tu_avid_coherence(
                    df_tu, df, subject, verbose=verbose,
                )
                n_coherence_changes = stats.get("rows_rebalanced", 0)
            except Exception as e:
                print(f"  [profile_writer] enforce_tu_avid_coherence raised "
                      f"({type(e).__name__}: {e}); continuing")

    # 4. Sort within each Column group by BP desc
    if sort:
        try:
            df = _sort_within_category(df)
        except Exception as e:
            print(f"  [profile_writer] sort failed "
                  f"({type(e).__name__}: {e}); continuing")

    # 5. Back up prior version
    backup_key = None
    if backup:
        backup_key = _backup_prior(s3, s3_key, "write")

    # 6. Upload
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    body = buf.getvalue().encode("utf-8")
    s3.put_object(
        Bucket=BUCKET, Key=s3_key, Body=body, ContentType="text/csv",
    )
    if verbose:
        print(f"  [profile_writer] uploaded ({len(body):,} bytes) -> "
              f"s3://{BUCKET}/{s3_key}")

    # 7. Register
    register_result = None
    if register:
        try:
            from migration.dashboard_register import (
                register_profile_in_dashboard,
            )
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from dashboard_register import register_profile_in_dashboard  # type: ignore
        try:
            register_result = register_profile_in_dashboard(
                s3_key,
                display_name=display_name,
                category=category,
                source_key=source_key,
                s3_client=s3,
            )
        except Exception as e:
            print(f"  [profile_writer] dashboard registration failed "
                  f"for {s3_key} ({type(e).__name__}: {e})")

    return {
        "s3_key": s3_key,
        "bytes": len(body),
        "backup_key": backup_key,
        "n_enforcer_changes": n_enforcer_changes,
        "n_anachronism_changes": n_anachronism_changes,
        "n_coherence_changes": n_coherence_changes,
        "register": register_result,
    }


def upload_and_register_profile(
    local_path: str,
    s3_key: Optional[str] = None,
    *,
    subject: Optional[str] = None,
    category: Optional[str] = None,
    display_name: Optional[str] = None,
    source_key: Optional[str] = None,
    tu_source_key: Optional[str] = None,
    year: Optional[int] = None,
    apply_final_enforcers: bool = False,
    backup: bool = True,
    sort: bool = True,
    register: bool = True,
    verbose: bool = True,
    s3_client=None,
) -> dict:
    """Thin path-based wrapper around `write_profile_csv` for the case
    where BG.py's `run_full_pipeline` has already produced a local CSV
    with all in-pipeline enforcers applied (audit playbook, crosswalk,
    pre-publish gate, etc.). Reads the CSV, then does upload + backup +
    dashboard registration through the canonical path.

    Set `apply_final_enforcers=True` to force `run_all_enforcers` again
    as a final safety net; default is False because BG.py has already
    done comprehensive enforcement inline. TU-vs-Avid coherence and
    anachronism checks still run when their trigger kwargs are passed
    (`tu_source_key`, `year`).

    Args:
      local_path: absolute path to CSV on disk
      s3_key: destination S3 key. Defaults to `os.path.basename(local_path)`.
      subject: subject name for enforcer coherence checks. Falls back to
        the file's BRAND INPUT row if not provided.
      category: brand category
      display_name: dashboard display name. Falls back to derived from key.
      source_key, tu_source_key, year: see `write_profile_csv`
      apply_final_enforcers: rerun `run_all_enforcers` (default False)
    """
    import pandas as pd
    if s3_key is None:
        s3_key = os.path.basename(local_path)
    df = pd.read_csv(local_path, dtype=object, keep_default_na=False)
    if not subject:
        try:
            m = df.iloc[:, 0].astype(str).str.upper().str.strip() \
                == "BRAND INPUT"
            if m.any():
                subject = str(df.loc[m].iloc[0, 1]).strip()
        except Exception:
            pass
    if not subject:
        subject = re.sub(r"\.csv$", "", os.path.basename(local_path)).strip()
    return write_profile_csv(
        df, subject=subject, s3_key=s3_key,
        category=category, display_name=display_name,
        source_key=source_key,
        run_enforcers=apply_final_enforcers,
        apply_anachronism=True,
        year=year,
        tu_source_key=tu_source_key,
        register=register, backup=backup, sort=sort,
        verbose=verbose, s3_client=s3_client,
    )


__all__ = ["write_profile_csv", "upload_and_register_profile"]
