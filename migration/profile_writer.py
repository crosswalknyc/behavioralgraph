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


def _normalize_numeric_artifacts(df: pd.DataFrame,
                                 verbose: bool = True) -> tuple:
    """Write-path format assertion (2026-08-24, Dylan/Erin avid audit:
    four cells shipped with a trailing '%' inside numeric columns and
    broke downstream parsing).

    Scans the four numeric columns (BP / Category Share / Raw / Proj)
    for cells that do not parse as float but DO parse after stripping
    '%', thousands commas, and stray whitespace - and normalizes them
    in place. Cells that are empty or genuinely non-numeric (metadata
    rows) are left alone. Logs loudly when it fires so a regression
    upstream is visible in the write log. Idempotent.

    Returns (df, n_fixed).
    """
    n_fixed = 0
    examples = []
    for col in (BP_COL, CS_COL, RAW_COL, PROJ_COL):
        if col not in df.columns:
            continue
        if df[col].dtype.name not in ("object", "O"):
            continue  # already numeric dtype; nothing to strip
        for idx in df.index:
            cell = df.at[idx, col]
            if cell is None:
                continue
            s = str(cell).strip()
            if s == "" or s.lower() in ("nan", "none"):
                continue
            try:
                float(s)
                continue  # clean numeric string
            except ValueError:
                pass
            cleaned = s.replace("%", "").replace(",", "").strip()
            try:
                float(cleaned)
            except ValueError:
                continue  # genuinely non-numeric (metadata); leave alone
            df.at[idx, col] = cleaned
            n_fixed += 1
            if len(examples) < 5:
                examples.append(f"{col}[{idx}]: {s!r} -> {cleaned!r}")
    if n_fixed and verbose:
        print(f"  [profile_writer] numeric-artifact assertion fired: "
              f"normalized {n_fixed} cell(s) with non-numeric artifacts "
              f"(%/commas) in BP/Share/Raw/Proj columns")
        for ex in examples:
            print(f"      {ex}")
    return df, n_fixed


def _load_tu_source(tu_source_key: str, s3) -> Optional[pd.DataFrame]:
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=tu_source_key)
        return pd.read_csv(io.BytesIO(obj["Body"].read()), dtype=object,
                            keep_default_na=False)
    except Exception as e:
        print(f"  [profile_writer] TU source fetch failed "
              f"({tu_source_key}): {e}")
        return None


def _is_avid_cut_basename(s3_key: str) -> bool:
    base = os.path.basename(str(s3_key or ""))
    if base.lower().endswith(".csv"):
        base = base[:-4]
    if " - " not in base:
        return False
    return "AVID" in base.split(" - ", 1)[1].upper()


# Ship-gate invariant classes with a safe DETERMINISTIC fixer. Only
# these ever trigger the fix-and-regate pass below; every judgment-
# required class (I1 rogue pins, I5 demo sums, I9 hidden brands, ...)
# still quarantines on first block.
_AUTOFIX_GATE_CODES = ("I11", "I12", "I13", "I14", "I15", "I16", "I17",
                       "I18", "I20")


def _detect_ladder_rows(df):
    """Count of rows still carrying the I14 fractional-ladder signature."""
    try:
        try:
            from migration.fractional_ladders import (
                detect_fractional_ladders, ladder_in_scope,
            )
        except ImportError:
            from fractional_ladders import (  # type: ignore
                detect_fractional_ladders, ladder_in_scope,
            )
        bp_col = next((c for c in df.columns
                       if str(c).lower().startswith("brand penetration")),
                      None)
        if not bp_col or "Column" not in df.columns:
            return 0
        triples = []
        for idx, cat, raw in zip(df.index, df["Column"], df[bp_col]):
            s = str(raw or "").strip().rstrip("%").replace(",", "")
            try:
                v = float(s)
            except ValueError:
                continue
            cat_u = str(cat or "").strip().upper()
            if ladder_in_scope(cat_u, v):
                triples.append((idx, cat_u, v))
        return len(detect_fractional_ladders(triples)["flagged_ids"])
    except Exception:
        return 0


def _ship_gate_autofix_pass(df, subject, s3_key, s3, *,
                            tu_source_key=None, sort=True, verbose=True):
    """Deterministic fix-and-regate ahead of the blocking ship gate
    (2026-08-26 Danny Go - Avid Fan incident: the fresh-build avid path
    shipped 299 rows whose Raw out-counted the parent TU; the gate's
    I12 caught them at the terminal check and quarantined a file the
    standing subset enforcer could have corrected in seconds).

    Runs the gate's invariants READ-ONLY on the serialized frame. When
    every violation class present has a safe deterministic fixer, the
    standing fixers run here:

      I12 avid subset raws -> enforce_avid_subset_coherence bound to
          the published parent TU (tu_source_key when the caller knows
          it, else the gate's own parent resolution). This is the
          arithmetic Raw cap the hardened enforcer already implements -
          reasoning tilt is preserved, no multipliers.
      I11 reach above 100  -> enforce_bp_hard_ceiling.
      I13 viewer carriage  -> enforce_viewer_carriage_constraint
          (2026-08-26 Jenna JKL/Rosie mandate): the carrying
          platforms of a consumption-scoped universe are lifted so
          their union covers ~100%, reading the same cached carriage
          facts the gate checked against. Pure arithmetic on the
          reasoned tilt, no multipliers.
      I14 fractional ladders -> dejitter_fractional_ladders
          (2026-08-26 Liz QA, Bethenny avid): shared-4dp-suffix
          integer-step ladders re-salted per (subject, brand,
          category), downward-only so the I12 subset invariant is
          never re-broken by the fix itself.
      I15 TALENT self-inclusion -> enforce_native_cluster_self_pin
          (same escalation, DEFECT 2): talent-archetype subjects
          self-include in TALENT at exactly 100.
      I20 top-cluster convergence -> respread_top_cluster_convergence
          (2026-08-27 Liz batch, YMCA/Toca streaming grids): converged
          category leaders re-spread with salted downward gaps, rank
          order preserved.

    Then Raw / Projection / Category Share recompute (write safety
    net), re-sort, numeric-artifact normalize, and Gen Pop baseline
    columns re-append, so the caller re-serializes corrected bytes and
    the FULL blocking gate re-runs on them. Violations that survive
    the fix attempt (true anomalies) quarantine exactly as before.

    Returns (df, summary_str_or_None). Never raises: any internal
    error returns the frame unchanged so the blocking gate stays the
    verdict.
    """
    try:
        from migration.final_ship_gate import (
            check_final_ship_invariants,
            _resolve_parent_tu,
        )
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from final_ship_gate import (  # type: ignore
            check_final_ship_invariants,
            _resolve_parent_tu,
        )
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    violations, _meta = check_final_ship_invariants(
        buf.getvalue().encode("utf-8"), s3_key, subject,
        s3_client=s3, verbose=False,
    )
    if not violations:
        return df, None
    fixable = [v for v in violations
               if v.get("code") in _AUTOFIX_GATE_CODES]
    if not fixable:
        return df, None
    codes = sorted({v.get("code") for v in fixable})
    print(f"  [profile_writer] ship-gate pre-check flagged "
          f"{len(fixable)} fixable violation(s) ({', '.join(codes)}); "
          f"applying deterministic fix + full re-gate before upload")

    try:
        from migration.post_generation_enforcers import (
            run_write_safety_net as _rwsn_gate,
            enforce_bp_hard_ceiling as _bp_ceiling_gate,
        )
    except ImportError:
        from post_generation_enforcers import (  # type: ignore
            run_write_safety_net as _rwsn_gate,
            enforce_bp_hard_ceiling as _bp_ceiling_gate,
        )

    # Gen Pop baseline columns were appended at step 6.5; strip them so
    # the fixers and the recompute see the canonical six-column frame,
    # re-appended below.
    try:
        try:
            from migration.genpop_baseline import (
                strip_genpop_columns as _strip_gp,
                append_genpop_columns as _append_gp,
            )
        except ImportError:
            from genpop_baseline import (  # type: ignore
                strip_genpop_columns as _strip_gp,
                append_genpop_columns as _append_gp,
            )
        df = _strip_gp(df)
    except Exception:
        _append_gp = None

    n_fixed = 0
    if any(v.get("code") == "I17" for v in fixable):
        # I17 (2026-08-26 Jenna convention correction): own-property /
        # owner-platform rows pin at exactly 100.0000 in base and
        # every cut. Deterministic, no parent needed.
        try:
            try:
                from migration.post_generation_enforcers import (
                    pin_own_property_rows as _own_pin_fix,
                )
            except ImportError:
                from post_generation_enforcers import (  # type: ignore
                    pin_own_property_rows as _own_pin_fix,
                )
            df, _n_own = _own_pin_fix(df, subject, verbose=verbose)
            n_fixed += int(_n_own or 0)
            print(f"  [profile_writer] I17 own-property pin fix: "
                  f"{_n_own} row(s) pinned to 100")
        except Exception as e:
            print(f"  [profile_writer] I17 own-property pin fix raised "
                  f"({type(e).__name__}: {e}); gate keeps the verdict")
    if any(v.get("code") == "I12" for v in fixable):
        # I12 (avid subset raws): enforce_avid_subset_coherence caps
        # out-counting rows vs the resolved parent, raw-verified.
        df_parent = None
        parent_label = None
        if tu_source_key:
            df_parent = _load_tu_source(tu_source_key, s3)
            parent_label = tu_source_key
        if df_parent is None:
            try:
                parent_key, parent_body = _resolve_parent_tu(
                    s3_key, s3, verbose=verbose)
                if parent_body:
                    df_parent = pd.read_csv(
                        io.BytesIO(parent_body), dtype=object,
                        keep_default_na=False)
                    parent_label = parent_key
            except Exception as e:
                print(f"  [profile_writer] I12 parent resolution "
                      f"failed ({e}); subset fix skipped")
        if df_parent is not None:
            try:
                try:
                    from migration.avid_fan_row_by_row import (
                        enforce_avid_subset_coherence as _subset_fix,
                    )
                except ImportError:
                    from avid_fan_row_by_row import (  # type: ignore
                        enforce_avid_subset_coherence as _subset_fix,
                    )
                df, _st = _subset_fix(df, df_parent, subject,
                                      verbose=verbose)
                n_capped = int(_st.get("capped_up", 0) or 0)
                n_lifted = int(_st.get("lifted_down", 0) or 0)
                n_dir = int(_st.get("direction_lifted", 0) or 0)
                n_fixed += n_capped + n_lifted + n_dir
                print(f"  [profile_writer] I12 subset fix vs "
                      f"{parent_label}: capped_up={n_capped} "
                      f"lifted_down={n_lifted} direction_lifted={n_dir}")
            except Exception as e:
                print(f"  [profile_writer] I12 subset fix raised "
                      f"({type(e).__name__}: {e}); gate keeps the "
                      f"verdict")
    if any(v.get("code") == "I11" for v in fixable):
        try:
            df, _n_ceiling = _bp_ceiling_gate(df, subject,
                                              verbose=verbose)
            n_fixed += int(_n_ceiling or 0)
            print(f"  [profile_writer] I11 ceiling fix: "
                  f"{_n_ceiling} row(s) repaired")
        except Exception as e:
            print(f"  [profile_writer] I11 ceiling fix raised "
                  f"({type(e).__name__}: {e}); gate keeps the verdict")
    if any(v.get("code") == "I13" for v in fixable):
        try:
            try:
                from migration.post_generation_enforcers import (
                    enforce_viewer_carriage_constraint as _carriage_fix,
                )
            except ImportError:
                from post_generation_enforcers import (  # type: ignore
                    enforce_viewer_carriage_constraint as _carriage_fix,
                )
            # The enforcer reads the same cached carriage facts the
            # gate checked against (S3 sidecar); no live research at
            # the gate.
            df, _n_car = _carriage_fix(df, subject, verbose=verbose)
            n_fixed += int(_n_car or 0)
            print(f"  [profile_writer] I13 viewer-carriage fix: "
                  f"{_n_car} row(s) lifted")
        except Exception as e:
            print(f"  [profile_writer] I13 viewer-carriage fix raised "
                  f"({type(e).__name__}: {e}); gate keeps the verdict")
    if any(v.get("code") == "I14" for v in fixable):
        try:
            try:
                from migration.post_generation_enforcers import (
                    dejitter_fractional_ladders as _ladder_fix,
                )
            except ImportError:
                from post_generation_enforcers import (  # type: ignore
                    dejitter_fractional_ladders as _ladder_fix,
                )
            # Downward-only per-row re-salt: Raw can only shrink, so
            # the I12 subset invariant survives the fix by construction.
            df, _n_lad = _ladder_fix(df, subject, verbose=verbose)
            n_fixed += int(_n_lad or 0)
            print(f"  [profile_writer] I14 fractional-ladder fix: "
                  f"{_n_lad} row(s) re-salted")
            # 2dp-era corner (N.0100 ladders): every in-decade downward
            # landing is X.00xx round-banned, so the downward fixer can
            # stall at 0 moved. The standing round-display dejitter
            # relocates those rows (bidirectional ±0.013-0.04pp), then
            # one more ladder pass clears leftover shared suffixes.
            if _detect_ladder_rows(df):
                try:
                    from migration.post_generation_enforcers import (
                        dejitter_x5x0_displays as _x5x0_fix,
                    )
                except ImportError:
                    from post_generation_enforcers import (  # type: ignore
                        dejitter_x5x0_displays as _x5x0_fix,
                    )
                df, _n_x5 = _x5x0_fix(df, subject, verbose=False)
                df, _n_lad2 = _ladder_fix(df, subject, verbose=False)
                n_fixed += int(_n_x5 or 0) + int(_n_lad2 or 0)
                print(f"  [profile_writer] I14 second stage (round-display"
                      f" dejitter): {_n_x5} + {_n_lad2} row(s)")
        except Exception as e:
            print(f"  [profile_writer] I14 fractional-ladder fix raised "
                  f"({type(e).__name__}: {e}); gate keeps the verdict")
    if any(v.get("code") == "I15" for v in fixable):
        try:
            try:
                from migration.post_generation_enforcers import (
                    enforce_native_cluster_self_pin as _talent_pin_fix,
                )
            except ImportError:
                from post_generation_enforcers import (  # type: ignore
                    enforce_native_cluster_self_pin as _talent_pin_fix,
                )
            df, _n_pin = _talent_pin_fix(df, subject, verbose=verbose)
            n_fixed += int(_n_pin or 0)
            print(f"  [profile_writer] I15 TALENT self-inclusion fix: "
                  f"{_n_pin} row(s) pinned/inserted")
        except Exception as e:
            print(f"  [profile_writer] I15 TALENT self-pin fix raised "
                  f"({type(e).__name__}: {e}); gate keeps the verdict")
    if any(v.get("code") == "I16" for v in fixable):
        try:
            try:
                from migration.post_generation_enforcers import (
                    enforce_self_property_coherence as _spc_fix,
                )
            except ImportError:
                from post_generation_enforcers import (  # type: ignore
                    enforce_self_property_coherence as _spc_fix,
                )
            df, _n_spc = _spc_fix(df, subject, verbose=verbose)
            n_fixed += int(_n_spc or 0)
            print(f"  [profile_writer] I16 self-property coherence "
                  f"fix: {_n_spc} row(s) re-leveled")
        except Exception as e:
            print(f"  [profile_writer] I16 self-property fix raised "
                  f"({type(e).__name__}: {e}); gate keeps the verdict")
    if any(v.get("code") == "I18" for v in fixable):
        try:
            try:
                from migration.post_generation_enforcers import (
                    depin_exact_100_non_subject as _depin_fix,
                )
            except ImportError:
                from post_generation_enforcers import (  # type: ignore
                    depin_exact_100_non_subject as _depin_fix,
                )
            # Cut-defining rows (the ' - ' suffix of the deliverable
            # name, e.g. 'Spotify Fan', 'Los Angeles Ca') land in the
            # high 99.9x band per the cut-skin convention.
            _base = str(s3_key or "").rsplit("/", 1)[-1]
            _base = _base[:-4] if _base.lower().endswith(".csv") else _base
            _cut_label = (_base.split(" - ", 1)[1].strip()
                          if " - " in _base else None)
            df, _n_dp = _depin_fix(df, subject, verbose=verbose,
                                   cut_label=_cut_label)
            n_fixed += int(_n_dp or 0)
            print(f"  [profile_writer] I18 exact-100 de-pin fix: "
                  f"{_n_dp} row(s) de-pinned")
        except Exception as e:
            print(f"  [profile_writer] I18 exact-100 de-pin fix raised "
                  f"({type(e).__name__}: {e}); gate keeps the verdict")
    if any(v.get("code") == "I20" for v in fixable):
        try:
            try:
                from migration.post_generation_enforcers import (
                    respread_top_cluster_convergence as _conv_fix,
                )
            except ImportError:
                from post_generation_enforcers import (  # type: ignore
                    respread_top_cluster_convergence as _conv_fix,
                )
            # Salted downward descent, order preserved; downward-only
            # so the I12 subset invariant survives by construction.
            df, _n_conv = _conv_fix(df, subject, verbose=verbose)
            n_fixed += int(_n_conv or 0)
            print(f"  [profile_writer] I20 convergence re-spread fix: "
                  f"{_n_conv} row(s) re-spread")
        except Exception as e:
            print(f"  [profile_writer] I20 convergence re-spread raised "
                  f"({type(e).__name__}: {e}); gate keeps the verdict")

    # Recompute the downstream chain from the corrected BPs, re-sort,
    # re-assert numeric formats, re-append baseline columns.
    try:
        df, _ = _rwsn_gate(df, subject, verbose=False)
    except Exception as e:
        print(f"  [profile_writer] post-fix safety net raised "
              f"({type(e).__name__}: {e})")
    if sort:
        try:
            df = _sort_within_category(df)
        except Exception:
            pass
    try:
        df, _ = _normalize_numeric_artifacts(df, verbose=False)
    except Exception:
        pass
    if _append_gp is not None:
        try:
            df = _append_gp(df, s3_client=s3, verbose=False)
        except Exception as e:
            print(f"  [profile_writer] post-fix genpop re-append "
                  f"skipped: {e}")

    summary = (f"{n_fixed} row(s) corrected before publish "
               f"({', '.join(codes)})") if n_fixed else None
    return df, summary


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
    run_gate: bool = True,
    gate_raise_on_fail: bool = False,
    verbose: bool = True,
    s3_client=None,
    follower_ceiling: Optional[int] = None,
    pin_rows: Optional[list] = None,
    keep_avid_row: Optional[bool] = None,
    ship_gate: bool = True,
    s3_metadata: Optional[dict] = None,
    carriage_doc: Optional[dict] = None,
) -> dict:
    """Canonical write path for any profile CSV heading to
    `s3://dashboard-inputs/<s3_key>`.

    Pipeline (in order):
      1. If run_enforcers: run_all_enforcers(df, subject,
         brand_category=category)
      2. If apply_anachronism AND year: strip_anachronistic_brands(df,
         year)
      3. If tu_source_key: enforce_tu_avid_coherence(df, TU_source)
      3.5 MANDATORY run_write_safety_net(df, subject) -- idempotent
         format normalize + Raw/Proj recompute + Category Share
         recompute + streaming-share health check + meta-row CS scrub.
         Cannot be disabled. See
         `post_generation_enforcers.run_write_safety_net` for why
         (Kane Brown 08_06 / Honey Pot 08_03 / Summer's Eve trio
         signature: large full-audience pulls drop Category Share on
         90+ blocks, smaller Avid cuts don't).
      4. If sort: sort within each Column group by BP desc
      5. If run_gate: run_pre_publish_gate(df, subject). Fires G1-G18
         defect detectors including the 2026-08-03 additions:
           G8  SHARE_SUM (non-demo Category Share sum != 100 ± 3pp)
           G9  SHARE_EQ_BP (writer wrote Share = BP directly)
           G10 STREAMING_SHARE_PIN (one row pinned, rest null)
         Defects log by default; set gate_raise_on_fail=True to abort
         the write.
      6. If backup AND file exists on S3: back up prior to
         `_backups/{key}.pre_write_<ts>.csv`
      6.8 DETERMINISTIC FIX-AND-REGATE (2026-08-26 Danny Go mandate):
         read-only pre-check of the ship-gate invariants; I12 (avid
         subset raws, fixed by enforce_avid_subset_coherence bound to
         the published parent TU) and I11 (reach above 100, fixed by
         enforce_bp_hard_ceiling) auto-remediate in real time, the
         chain recomputes + re-sorts, and the blocking gate re-runs on
         the corrected bytes. Judgment-required classes never
         auto-fix; surviving violations still quarantine.
      6.9 FINAL SHIP GATE (2026-08-24 Jenna mandate): the independent
         terminal invariant check in migration/final_ship_gate.py runs
         on the EXACT bytes about to upload. On any violation the
         write is BLOCKED: the rejected bytes land in _quarantine/, a
         debounced hold notice is recorded for Jenna + Jessie (emails
         only if the hold outlives the window; see
         migration/hold_notice_debounce), and ShipGateError
         propagates to the caller. ship_gate=False (local ops override
         only, via migration/local_override_profile.py) downgrades to
         report-only. There is NO env-flag downgrade.
      7. Upload df to `s3://dashboard-inputs/<s3_key>`
      7.5 cancel_on_publish(s3_key): a gate-green publish silently
         resolves any pending hold notice for this deliverable.
      8. If register: register_profile_in_dashboard(s3_key, ...)

    Returns a dict with everything the caller needs to log, including
    `gate_defects` (list of strings from the gate).

    Any step failure is caught and logged; the write itself must succeed
    (or the exception propagates). Enforcer failures are non-fatal.
    """
    s3 = s3_client or boto3.client("s3", region_name="us-east-2")
    n_enforcer_changes = 0
    n_anachronism_changes = 0
    n_coherence_changes = 0
    gate_defects: list = []

    # Gen Pop baseline columns (Jenna 2026-08-22): strip any copies from
    # the input df so no enforcer / gate / polish pass sees unexpected
    # columns; re-appended fresh right before upload (step 6.5 below).
    try:
        try:
            from migration.genpop_baseline import strip_genpop_columns
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from genpop_baseline import strip_genpop_columns  # type: ignore
        df = strip_genpop_columns(df)
    except Exception as e:
        print(f"  [profile_writer] genpop-column strip skipped: {e}")

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
            # AVID FAN keep/strip (2026-08-24 reasoned-era reversal):
            # TUs keep their reasoned AVID FAN row; derived cuts never
            # carry one. Cut deliverables are always named
            # '{Subject} - {Cut}' (avid-and-cut-skin rule 6b), so a
            # ' - ' in the s3_key basename is the cut signature.
            # Callers can override explicitly.
            if keep_avid_row is None:
                keep_avid_row = " - " not in os.path.basename(s3_key or "")
            df, n_enforcer_changes = run_all_enforcers(
                df, subject, brand_category=category, verbose=verbose,
                target_year=(year if apply_anachronism else None),
                follower_ceiling=follower_ceiling,
                keep_avid_row=keep_avid_row,
                # Viewer-carriage facts from the build spec (2026-08-26
                # Jenna JKL/Rosie mandate). None -> the enforcer
                # auto-resolves on TU paths via detection + S3 cache.
                carriage_doc=carriage_doc,
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
            # 3b. Avid subset Raw invariant vs the published parent TU
            # (2026-08-26 Danny Go incident). The enforcer chain in
            # step 1 reasons per-row with no parent frame; an avid cut
            # written with a known TU source must satisfy
            # round(avid_bp/100 x avid_sample) <=
            # round(parent_bp/100 x parent_sample) on every shared row
            # BEFORE the terminal gate sees the bytes. Same standing
            # fixer the derive_cut paths run (Phase 4b) - arithmetic
            # Raw cap, reasoning tilt preserved, no multipliers.
            if _is_avid_cut_basename(s3_key):
                try:
                    try:
                        from migration.avid_fan_row_by_row import (
                            enforce_avid_subset_coherence,
                        )
                    except ImportError:
                        from avid_fan_row_by_row import (  # type: ignore
                            enforce_avid_subset_coherence,
                        )
                    df, _sub_stats = enforce_avid_subset_coherence(
                        df, df_tu, subject, verbose=verbose,
                    )
                    n_coherence_changes += int(
                        _sub_stats.get("capped_up", 0) or 0)
                    n_coherence_changes += int(
                        _sub_stats.get("lifted_down", 0) or 0)
                except Exception as e:
                    print(f"  [profile_writer] avid subset coherence "
                          f"raised ({type(e).__name__}: {e}); "
                          f"continuing")

    # 3.5 MANDATORY write-time safety net (2026-08-06). Idempotent,
    #     cheap, always runs regardless of `run_enforcers`. Repairs the
    #     recurring "large full-audience pull loses Category Share" bug
    #     (Kane Brown 08_06 / Honey Pot 08_03 / Summer's Eve trio) by
    #     re-running the four format/CS/Raw-Proj normalizers as a
    #     terminal pass. See
    #     `post_generation_enforcers.run_write_safety_net` docstring.
    n_safety_net_changes = 0
    try:
        try:
            from migration.post_generation_enforcers import (
                run_write_safety_net,
            )
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from post_generation_enforcers import (  # type: ignore
                run_write_safety_net,
            )
        df, safety_stats = run_write_safety_net(
            df, subject, verbose=verbose,
        )
        n_safety_net_changes = sum(
            v for v in safety_stats.values() if isinstance(v, int) and v > 0
        )
    except Exception as e:
        print(f"  [profile_writer] write-safety-net raised "
              f"({type(e).__name__}: {e}); continuing")

    # 4. Sort within each Column group by BP desc
    if sort:
        try:
            df = _sort_within_category(df)
        except Exception as e:
            print(f"  [profile_writer] sort failed "
                  f"({type(e).__name__}: {e}); continuing")

    # 5. Pre-publish gate (defect scanner). By default this logs
    # violations but does not block the write. Set
    # `gate_raise_on_fail=True` on write paths where you want to abort.
    if run_gate:
        try:
            from migration.post_generation_enforcers import (
                run_pre_publish_gate,
            )
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from post_generation_enforcers import (  # type: ignore
                run_pre_publish_gate,
            )
        try:
            gate_defects = run_pre_publish_gate(
                df, subject,
                project_name=display_name or s3_key,
                raise_on_fail=gate_raise_on_fail,
                verbose=verbose,
            )
        except Exception as e:
            print(f"  [profile_writer] pre-publish gate raised "
                  f"({type(e).__name__}: {e})")
            if gate_raise_on_fail:
                raise

        # 5.5 G12 PHANTOM_ZERO is BLOCKING (2026-08-24, Erin Brooks:
        # gate flagged phantom-zero rows on both files, then uploaded
        # anyway because this path runs with raise_on_fail=False).
        # Remediation-first: strip the phantom rows with the standing
        # enforcer, re-run the safety net so LOCATION/CS/Raw stay
        # coherent, then re-run the gate. Only hard-fail when the fix
        # cannot clear the defect - never upload a G12-flagged file.
        _g12_hits = [d for d in (gate_defects or [])
                     if "G12 PHANTOM_ZERO" in str(d)]
        if _g12_hits:
            print(f"  [profile_writer] G12 PHANTOM_ZERO flagged "
                  f"({len(_g12_hits)}); applying strip + regate before "
                  f"upload")
            try:
                try:
                    from migration.post_generation_enforcers import (
                        strip_phantom_zero_rows,
                        run_write_safety_net as _rwsn_g12,
                    )
                except ImportError:
                    from post_generation_enforcers import (  # type: ignore
                        strip_phantom_zero_rows,
                        run_write_safety_net as _rwsn_g12,
                    )
                df, _n_stripped = strip_phantom_zero_rows(
                    df, subject, verbose=verbose,
                )
                df, _ = _rwsn_g12(df, subject, verbose=verbose)
                if sort:
                    df = _sort_within_category(df)
                gate_defects = run_pre_publish_gate(
                    df, subject,
                    project_name=display_name or s3_key,
                    raise_on_fail=False,
                    verbose=verbose,
                )
            except Exception as _g12_err:
                raise RuntimeError(
                    f"G12 PHANTOM_ZERO auto-fix failed for {subject} "
                    f"({s3_key}): {_g12_err}"
                )
            _g12_still = [d for d in (gate_defects or [])
                          if "G12 PHANTOM_ZERO" in str(d)]
            if _g12_still:
                raise RuntimeError(
                    f"G12 PHANTOM_ZERO persists after strip + regate "
                    f"for {subject} ({s3_key}); refusing to upload a "
                    f"flagged file: {_g12_still[0]}"
                )
            print(f"  [profile_writer] G12 cleared after strip "
                  f"({_n_stripped} row(s) removed); proceeding")

        # 5.55 G2 / G5 hard impossibilities are BLOCKING (2026-08-24
        # Jenna mandate, same precedent as G12): a projection above US
        # population (G2) or a demo category off sum=100 (G5) is not a
        # judgment call - it is arithmetic that cannot be true.
        # Remediation-first: re-run the safety net (recomputes Raw and
        # Proj from BP) and the demo renormalizer, re-gate, and only
        # hard-fail when the fix cannot clear the defect. Detector
        # errors ("detector errored") are excluded - those are checker
        # failures, not confirmed impossibilities, and the terminal
        # ship gate independently re-checks the same invariants below.
        def _hard_hits(defect_list):
            return [d for d in (defect_list or [])
                    if (str(d).startswith("G2 PROJECTION:")
                        or str(d).startswith("G5 DEMO_SUM:"))
                    and "detector errored" not in str(d)]

        _g25_hits = _hard_hits(gate_defects)
        if _g25_hits:
            print(f"  [profile_writer] G2/G5 hard impossibility "
                  f"flagged ({len(_g25_hits)}); applying remediation + "
                  f"regate before upload")
            try:
                try:
                    from migration.post_generation_enforcers import (
                        renormalize_demographics_to_100,
                        run_write_safety_net as _rwsn_g25,
                    )
                except ImportError:
                    from post_generation_enforcers import (  # type: ignore
                        renormalize_demographics_to_100,
                        run_write_safety_net as _rwsn_g25,
                    )
                df, _ = renormalize_demographics_to_100(
                    df, subject=subject, verbose=verbose,
                )
                df, _ = _rwsn_g25(df, subject, verbose=verbose)
                if sort:
                    df = _sort_within_category(df)
                gate_defects = run_pre_publish_gate(
                    df, subject,
                    project_name=display_name or s3_key,
                    raise_on_fail=False,
                    verbose=verbose,
                )
            except Exception as _g25_err:
                raise RuntimeError(
                    f"G2/G5 auto-fix failed for {subject} "
                    f"({s3_key}): {_g25_err}"
                )
            _g25_still = _hard_hits(gate_defects)
            if _g25_still:
                raise RuntimeError(
                    f"G2/G5 hard impossibility persists after "
                    f"remediation + regate for {subject} ({s3_key}); "
                    f"refusing to upload a flagged file: "
                    f"{_g25_still[0]}"
                )
            print(f"  [profile_writer] G2/G5 cleared after "
                  f"remediation; proceeding")

    # 5.6 Terminal invariant polish (2026-08-20 EST Buyers batch). The
    # gate's auto-patchers (G1/G13) and several late enforcers (lux
    # caps, MPB deband) mutate BPs AFTER the depin + self-pin + sort
    # steps above, which shipped files with exact-2dp rows, a URL-
    # variant seed string pinned in the native grid, a peer-capped
    # subject row, and unsorted categories. Re-assert the invariants
    # here (echo strip -> subject re-pin -> depin -> Raw/Proj + CS
    # recompute) and re-sort, so nothing after this point can violate
    # them. Idempotent + Claude-free.
    n_polish_changes = 0
    try:
        try:
            from migration.post_generation_enforcers import (
                run_final_invariant_polish,
            )
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from post_generation_enforcers import (  # type: ignore
                run_final_invariant_polish,
            )
        df, polish_stats = run_final_invariant_polish(
            df, subject, verbose=verbose,
        )
        n_polish_changes = sum(
            v for v in polish_stats.values()
            if isinstance(v, int) and v > 0
        )
        if sort:
            df = _sort_within_category(df)
    except Exception as e:
        print(f"  [profile_writer] final invariant polish raised "
              f"({type(e).__name__}: {e}); continuing")

    # 5.65 Spec pin re-assert (2026-08-24 Furious audit D3): the
    # approved spec's subject_rows pins are re-enforced AFTER the polish
    # so no late pass (persona noise, sanity fixes, gate patches) can
    # leave a viewers-scope platform pin drifted off 100. Alias-aware
    # ('Hulu' lands on 'Disney+/Hulu'); logs LOUDLY on zero matches.
    if pin_rows:
        try:
            try:
                from migration.post_generation_enforcers import (
                    enforce_spec_pin_rows,
                    run_write_safety_net as _rwsn_pins,
                )
            except ImportError:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from post_generation_enforcers import (  # type: ignore
                    enforce_spec_pin_rows,
                    run_write_safety_net as _rwsn_pins,
                )
            df, _n_pins, _unmatched_pins = enforce_spec_pin_rows(
                df, subject, pin_rows, verbose=verbose,
                carriage_doc=carriage_doc,
            )
            if _n_pins:
                df, _ = _rwsn_pins(df, subject, verbose=False)
                if sort:
                    df = _sort_within_category(df)
        except Exception as e:
            print(f"  [profile_writer] spec pin re-assert raised "
                  f"({type(e).__name__}: {e}); continuing")

    # 5.7 Numeric-artifact assertion (2026-08-24): last-line format
    # guarantee that no '%' / comma artifact survives in the four
    # numeric columns of the shipped file. See
    # _normalize_numeric_artifacts.
    try:
        df, _n_artifacts = _normalize_numeric_artifacts(df, verbose=verbose)
    except Exception as e:
        print(f"  [profile_writer] numeric-artifact assertion raised "
              f"({type(e).__name__}: {e}); continuing")

    # 5.75 Pre-upload invariant audit (2026-08-24 Furious audit D2):
    # loud tripwire for exact-2dp floods, unsorted categories, percent-
    # string cells, missing SUBJECT row, eroded self-pins. Report-only.
    try:
        try:
            from migration.post_generation_enforcers import (
                audit_upload_invariants,
            )
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from post_generation_enforcers import (  # type: ignore
                audit_upload_invariants,
            )
        audit_upload_invariants(df, subject, context=s3_key, verbose=verbose)
    except Exception as e:
        print(f"  [profile_writer] upload audit raised "
              f"({type(e).__name__}: {e}); continuing")

    # 5b. 2026-08-14 (iJustine incident): pre-flight subject-research gate.
    # LLM estimates the subject's expected US digital engager reach and
    # BLOCKS the write when the panel projection is orders of magnitude
    # below expected (indicating hostmap coverage gap or wrong subject
    # linkage). OPT-IN via env SYNTH_RESEARCH_GATE=1 because it costs an
    # extra LLM call per profile.
    try:
        from migration.small_sample_hardening import (
            preflight_subject_research_gate,
        )
        should_write, reason = preflight_subject_research_gate(
            df, subject, brand_category=category, verbose=verbose,
        )
        if not should_write:
            msg = (
                f"[profile_writer] pre-flight research gate BLOCKED "
                f"write for {subject}: {reason}"
            )
            print(f"  🛑 {msg}")
            if gate_raise_on_fail:
                raise RuntimeError(msg)
    except Exception as e:
        if "BLOCKED" in str(e):
            raise
        if verbose:
            print(f"  [profile_writer] research gate skipped: {e}")

    # 6. Back up prior version
    backup_key = None
    if backup:
        backup_key = _backup_prior(s3, s3_key, "write")

    # 6.5 Gen Pop baseline columns (Jenna 2026-08-22): appended as the
    # very last transform, after every enforcer / safety net / polish /
    # gate, so the raw file ships with the current Gen Pop value and
    # index for every matched row and nothing upstream ever sees the
    # extra columns. Non-fatal on any failure.
    try:
        try:
            from migration.genpop_baseline import append_genpop_columns
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from genpop_baseline import append_genpop_columns  # type: ignore
        df = append_genpop_columns(df, s3_client=s3, verbose=verbose)
    except Exception as e:
        print(f"  [profile_writer] genpop baseline append skipped: {e}")

    # 6.8 Deterministic fix-and-regate (2026-08-26 Danny Go mandate:
    # the gate must FIX fixable violations in real time and publish,
    # not quarantine). Read-only invariant pre-check; when I12 (avid
    # subset raws) or I11 (reach above 100) are present, the standing
    # deterministic fixers run, the chain recomputes, and the blocking
    # gate below re-runs on the corrected bytes. Judgment-required
    # classes never auto-fix; anything that survives the fix attempt
    # still quarantines. Never raises - the gate stays the verdict.
    ship_gate_autofix = None
    try:
        df, ship_gate_autofix = _ship_gate_autofix_pass(
            df, subject, s3_key, s3,
            tu_source_key=tu_source_key, sort=sort, verbose=verbose,
        )
        if ship_gate_autofix:
            print(f"  [profile_writer] ship-gate auto-fix: "
                  f"{ship_gate_autofix}")
    except Exception as e:
        print(f"  [profile_writer] ship-gate auto-fix pass skipped "
              f"({type(e).__name__}: {e})")

    # 6.9 FINAL SHIP GATE (2026-08-24 Jenna mandate: profiles must
    # never ship with defect classes like today's four). Independent
    # module - own CSV parse, own numeric coercion, no shared enforcer
    # helpers - run on the EXACT bytes about to upload. On violations
    # it quarantines the rejected bytes, emails the hold notice, and
    # raises ShipGateError; deliberately NOT wrapped in a swallowing
    # try/except so nothing after this line can ship a flagged file.
    # ship_gate=False (explicit argument, local ops override only)
    # still runs the checks report-only.
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    body = buf.getvalue().encode("utf-8")
    try:
        from migration.final_ship_gate import run_final_ship_gate
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from final_ship_gate import run_final_ship_gate  # type: ignore
    run_final_ship_gate(
        body, s3_key, subject,
        enforce=bool(ship_gate),
        s3_client=s3, verbose=verbose,
    )

    # 6.95 PRE-SHIP REASONED VETTING (2026-08-26 Jenna mandate: "there
    # has to be research and reasoning done before shipping a
    # profile"). After the mechanical gate approves the bytes, a
    # consolidated research-backed review (migration/pre_ship_vetting)
    # judges face validity per category against the audience's demo
    # composition, benchmark-anchored plausibility on high-stakes
    # grids, subject coherence, and slug/naming sanity. New keys only
    # (existing keys are in-place corrections of already-reviewed
    # content). PASS publishes; deterministic benchmark-backed fixes
    # apply in place and re-run the mechanical gate; judgment holds
    # quarantine via PreShipVettingError (a ShipGateError subclass, so
    # every caller's existing hold handling applies). Infra failures
    # inside the review fail OPEN so an outage cannot wedge publishes.
    try:
        from migration.pre_ship_vetting import vet_before_publish
    except ImportError:
        from pre_ship_vetting import (  # type: ignore
            vet_before_publish,
        )
    df, body, _vet_report = vet_before_publish(
        df, body, subject, s3_key,
        category=category, s3_client=s3,
        enforce=bool(ship_gate), verbose=verbose,
    )

    # 7. Upload. s3_metadata (e.g. refresh-generation for refresh
    # chains) rides the object so the next refresh can read it back
    # via head_object.
    _put_kwargs = dict(Bucket=BUCKET, Key=s3_key, Body=body,
                       ContentType="text/csv")
    if s3_metadata:
        _put_kwargs["Metadata"] = {
            str(k): str(v) for k, v in s3_metadata.items()}
    s3.put_object(**_put_kwargs)
    if verbose:
        print(f"  [profile_writer] uploaded ({len(body):,} bytes) -> "
              f"s3://{BUCKET}/{s3_key}")

    # 7.2 Ship-ledger record for cross-file constant detection
    # (2026-08-27 Liz batch: Visa inside a 2.1-index window on seven
    # unrelated same-day avids). One tiny JSON object per shipped
    # file; the vetting prescan on FUTURE files reads the trailing
    # window. Best-effort, never blocks a publish.
    try:
        try:
            from migration.cross_file_constants import record_ship
        except ImportError:
            from cross_file_constants import record_ship  # type: ignore
        record_ship(df, subject, s3_key, s3_client=s3)
    except Exception as e:
        print(f"  [profile_writer] ship-ledger record skipped "
              f"({type(e).__name__}: {e})")

    # 7.5 A gate-green publish resolves any pending hold notice for
    # this deliverable (2026-08-27 debounce policy): the machinery
    # repaired and republished, so the earlier hold is cancelled
    # silently and logged as auto-resolved. Best-effort, never blocks.
    try:
        try:
            from migration.hold_notice_debounce import cancel_on_publish
        except ImportError:
            from hold_notice_debounce import cancel_on_publish  # type: ignore
        cancel_on_publish(s3_key, s3_client=s3, verbose=verbose)
    except Exception as e:
        print(f"  [profile_writer] hold-notice cancel skipped "
              f"({type(e).__name__}: {e})")

    # 8. Register
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
        "n_safety_net_changes": n_safety_net_changes,
        "n_polish_changes": n_polish_changes,
        "gate_defects": gate_defects,
        "ship_gate_autofix": ship_gate_autofix,
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

    Regardless of `apply_final_enforcers`, `write_profile_csv` always
    runs the lightweight `run_write_safety_net` (format normalizer,
    Raw/Proj recompute, Category Share recompute, streaming-share
    health, meta-CS scrub) as a mandatory terminal pass. That is what
    catches the Kane Brown 08_06 / Honey Pot 08_03 signature where
    BG.py's inline CS writer skips 90+ non-meta blocks on large pulls.
    `apply_final_enforcers=True` additionally re-runs the full
    `run_all_enforcers` chain (Claude-free but expensive); default is
    False because the write safety net covers the CS-loss bug at a
    fraction of the cost.

    TU-vs-Avid coherence and anachronism checks still run when their
    trigger kwargs are passed (`tu_source_key`, `year`).

    Args:
      local_path: absolute path to CSV on disk
      s3_key: destination S3 key. Defaults to `os.path.basename(local_path)`.
      subject: subject name for enforcer coherence checks. Falls back to
        the file's BRAND INPUT row if not provided.
      category: brand category
      display_name: dashboard display name. Falls back to derived from key.
      source_key, tu_source_key, year: see `write_profile_csv`
      apply_final_enforcers: rerun full `run_all_enforcers` (default
        False). The write-time safety net always runs regardless.
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
