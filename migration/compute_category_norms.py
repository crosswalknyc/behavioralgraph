#!/usr/bin/env python3
"""Pre-compute per-category Brand Penetration (BP) norms across every
profile in `s3://dashboard-inputs/system/s3_cache.json`.

For each category C (ACTOR, COMEDIAN, DIGITAL BANKING, ...) we walk
every job whose `category == C`, fetch its profile CSV, and compute the
**sample-size-weighted average BP** for every (Column, Value) tuple
present in that profile.

The resulting `category_norms.json` is consumed by the dashboard's
Data Cuts popover: when a user toggles "Show Category Norm", the
frontend treats the norm for the current profile's category as a
synthetic comparison run, with its own bar / column / index alongside
prior data cuts and Gen Pop.

Output schema (persisted to `s3://dashboard-inputs/system/category_norms.json`):

    {
      "generated_at": "2026-06-11T16:14:00Z",
      "generated_by": "compute_category_norms.py",
      "min_profiles": 3,
      "norms": {
        "ACTOR": {
          "category": "ACTOR",
          "profile_count": 737,
          "total_sample": 91234567,         # raw sum across all profiles
          "weighted_avg_sample_size": 28471, # sample-weighted single-profile representative (Rule #3a anchor)
          "weighted_avg_projection": 939186, # = weighted_avg_sample_size / 10M * 329.9M (Rule #3a derived)
          "behavioral": {
            "QSR": [{ "name": "McDonald's", "pct": 53.2143 }, ...],
            "SOCIAL MEDIA": [{...}],
            ...
          },
          "demographics": {
            "AGE": { "18-24": 12.34, "25-34": 18.56, ... },
            "GENDER": { ... },
            ...
          },
          "interests": { "Travel": 23.45, ... },
          "locations": [{ "name": "New York, NY", "pct": 4.56 }, ...]
        },
        "COMEDIAN": { ... },
        ...
      },
      "skipped_categories": {
        "UNCATEGORIZED": "fewer than 3 profiles",
        ...
      }
    }

Usage:
    python migration/compute_category_norms.py              # process all categories
    python migration/compute_category_norms.py --only ACTOR # one category
    python migration/compute_category_norms.py --workers 16 # bump parallelism
    python migration/compute_category_norms.py --dry-run    # compute but don't upload
    python migration/compute_category_norms.py --min 5      # require >=5 profiles

The script is idempotent and safe to re-run. The running web service
picks up changes the next time a user requests
`/api/category-norms/<category>` (the endpoint always reads fresh from
S3 via a short TTL cache).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import threading
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import boto3
import pandas as pd

S3_BUCKET = "dashboard-inputs"
S3_CACHE_KEY = "system/s3_cache.json"
NORMS_KEY = "system/category_norms.json"
S3_REGION = os.environ.get("AWS_DEFAULT_REGION") or "us-east-2"

# Rule #3a (PROFILE_AUDIT_RECIPE / profile-iq-pipeline-rules): every
# profile's Raw count is anchored on a fixed 10M panel sample and the
# US projection is Raw/10M * 329.9M. The category norm follows the
# same canonical math — it ships a single representative sample size
# (sample-weighted average across the category's profiles) and the
# projection is derived from that, NOT computed independently from
# whatever projection numbers happen to live in the source CSVs.
CANONICAL_SAMPLE_SIZE = 10_000_000
US_POPULATION = 329_900_000

# Lock the set of demographic categories to the same canonical list the
# frontend uses (matches PIPELINE_DEMO_SCHEMA in post_generation_enforcers).
DEMO_CATEGORIES = {
    "AGE", "GENDER", "ETHNICITY", "INCOME", "EDUCATION",
    "RELATIONSHIP", "SEXUAL_ORIENTATION", "PARENTAL_STATUS", "OCCUPATION",
}

# Rows that aren't real data — skip them when building the norm.
METADATA_ROWS = {
    "BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY",
    "INPUT_METADATA", "SUBJECT", "AVID FAN", "CASUAL FAN",
}

# Map alias category labels back to the canonical version frontend
# expects (so a job tagged "INTEREST" still folds into "INTERESTS").
CATEGORY_ALIASES = {
    "INTEREST": "INTERESTS",
}


def _norm_cat(c: str) -> str:
    if not c:
        return ""
    c = str(c).strip().upper()
    return CATEGORY_ALIASES.get(c, c)


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

def _make_s3():
    endpoint = f"https://s3.{S3_REGION}.amazonaws.com"
    return boto3.client("s3", region_name=S3_REGION, endpoint_url=endpoint)


def load_jobs_cache(s3) -> list[dict]:
    obj = s3.get_object(Bucket=S3_BUCKET, Key=S3_CACHE_KEY)
    cache = json.loads(obj["Body"].read().decode("utf-8"))
    jobs = cache.get("jobs") or []
    return jobs


def fetch_profile_csv(s3, key: str) -> pd.DataFrame | None:
    """Pull a profile CSV by S3 key. Returns None on any error so the
    main loop can keep going without aborting on one corrupt file."""
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        body = obj["Body"].read()
        # pandas tolerates BOMs and quoting better than csv.reader here.
        df = pd.read_csv(io.BytesIO(body), dtype=str, keep_default_na=False)
        return df
    except Exception as e:
        print(f"  ! fetch failed for {key}: {e}")
        return None


# ---------------------------------------------------------------------------
# Profile parsing
# ---------------------------------------------------------------------------

def extract_sample_size(df: pd.DataFrame) -> int:
    """Same heuristic as `extract_sample_size_from_csv` in app.py:
    SAMPLE SIZE row, prefer columns D (Category Share) and E (Original
    Raw Numbers) over C (Brand Penetration) when C is suspiciously low.
    """
    if "Column" not in df.columns:
        return 0
    ss_rows = df[df["Column"].astype(str).str.upper().str.strip() == "SAMPLE SIZE"]
    if ss_rows.empty:
        return 0
    row = ss_rows.iloc[0]
    candidates: list[int] = []
    for col in ("Category Share", "Original Raw Numbers", "Brand Penetration (Row)"):
        if col not in df.columns:
            continue
        raw = str(row.get(col, "") or "").replace(",", "").strip()
        if not raw:
            continue
        try:
            n = int(float(raw))
            if n > 0:
                candidates.append(n)
        except (ValueError, TypeError):
            pass
    if not candidates:
        return 0
    chosen = candidates[0]
    if chosen < 1000 and len(candidates) > 1:
        chosen = max(candidates)
    return chosen


def extract_projected_us(df: pd.DataFrame) -> int:
    """Pull each profile's US Gen Pop Projection so the norm aggregator
    can compute a sample-weighted-average projection across the category.

    Matches the dashboard's parseAllData logic: BRAND INPUT row's
    `US Gen Pop Projection` column (falls back to `Gen Pop Projection`
    or the 6th positional column to tolerate older header variants).
    Returns 0 if the row or column is missing — the accumulator just
    skips that profile's projection contribution rather than dragging
    the weighted average toward zero.
    """
    if "Column" not in df.columns:
        return 0
    bi_rows = df[df["Column"].astype(str).str.upper().str.strip() == "BRAND INPUT"]
    if bi_rows.empty:
        return 0
    row = bi_rows.iloc[0]
    for col in ("US Gen Pop Projection", "Gen Pop Projection"):
        if col in df.columns:
            raw = str(row.get(col, "") or "").replace(",", "").strip()
            if not raw:
                continue
            try:
                n = int(float(raw))
                if n > 0:
                    return n
            except (ValueError, TypeError):
                pass
    return 0


def _safe_float(v: Any) -> float:
    if v is None:
        return 0.0
    s = str(v).strip()
    if not s:
        return 0.0
    try:
        return float(s.replace(",", "").rstrip("%"))
    except (ValueError, TypeError):
        return 0.0


def parse_profile_rows(df: pd.DataFrame) -> dict:
    """Walk every row and bucket it into the right shape:
      - demographics[CAT][bucket] = pct
      - behavioral[CAT] = [(value, pct)]
      - interests[value] = pct
      - locations = [(value, pct)]
    BP comes from `Brand Penetration (Row)` per Rule #3a (NEVER fall
    back to Category Share).
    """
    out: dict[str, Any] = {
        "demographics": defaultdict(dict),
        "behavioral": defaultdict(list),
        "interests": {},
        "locations": [],
    }
    if "Column" not in df.columns or "Value" not in df.columns:
        return out
    bp_col = "Brand Penetration (Row)"
    if bp_col not in df.columns:
        return out

    for _, row in df.iterrows():
        col_raw = str(row["Column"] or "").strip()
        val_raw = str(row["Value"] or "").strip()
        if not col_raw or not val_raw:
            continue
        col = col_raw.upper()
        if col in METADATA_ROWS:
            continue
        pct = _safe_float(row.get(bp_col))
        # We allow 0 here because zeros are real audience data for
        # under-indexing rows and we want them weighted in.

        if col in DEMO_CATEGORIES:
            # Last-write wins per (cat, bucket) within one profile —
            # matches the way parseAllData treats demographic rows.
            out["demographics"][col][val_raw] = pct
        elif col in ("INTEREST", "INTERESTS"):
            out["interests"][val_raw] = pct
        elif col == "LOCATION":
            out["locations"].append((val_raw, pct))
        else:
            out["behavioral"][col].append((val_raw, pct))

    # Convert defaultdicts to regular dicts for clean JSON later.
    out["demographics"] = {k: dict(v) for k, v in out["demographics"].items()}
    out["behavioral"] = {k: list(v) for k, v in out["behavioral"].items()}
    return out


# ---------------------------------------------------------------------------
# Norm aggregation
# ---------------------------------------------------------------------------

class CategoryAccumulator:
    """Streams per-profile parsed payloads into running weighted sums.

    For each (col, bucket/value) we track:
      - weighted_sum = sum(pct * sample_size)
      - weight_total = sum(sample_size)   (only profiles that PARTICIPATED
                                            in that row — see note below)

    Important: if a profile doesn't have row X, we DON'T count its
    sample size against X (this would silently dilute the norm with
    zeros that just mean "not measured"). This mirrors how the frontend
    treats absent rows.
    """

    def __init__(self, category: str):
        self.category = category
        self.profile_count = 0
        self.total_sample = 0
        # Sample-size-weighted accumulator for the per-category
        # "representative sample size" the Norm row displays. We track
        # sum(s_i * s_i) here and divide by total_sample (= sum(s_i))
        # at finalize() to get the sample-weighted average:
        #
        #     weighted_avg_sample_size = sum(s_i^2) / sum(s_i)
        #
        # That collapses the category to a SINGLE representative
        # panel-sample number on the same order of magnitude as one
        # profile (a few thousand to a few tens of thousands) instead
        # of the raw sum across hundreds of profiles, which would
        # balloon past the entire 10M panel and produce a meaningless
        # "sample size" cell. Weighting by sample size itself lets
        # larger / more statistically robust profiles drive the norm
        # — same rationale as everywhere else in this file.
        #
        # The projection then follows directly from Rule #3a:
        #     weighted_avg_projection = weighted_avg_sample_size
        #                               / CANONICAL_SAMPLE_SIZE
        #                               * US_POPULATION
        # which keeps the Norm row's Sample Size <-> US # relationship
        # numerically consistent with the dashboard's canonical math
        # regardless of any stale projection columns that may exist
        # in the source CSVs.
        self.sample_weighted_sum: float = 0.0
        # Kept for diagnostics / back-compat — the dashboard prefers
        # the derived value above, but these still surface in the
        # JSON so an admin can sanity-check the input shape.
        self.proj_weighted_sum: float = 0.0
        self.total_projection: int = 0
        self.profiles_with_projection: int = 0
        # demo: { CAT: { bucket: [wsum, wtot] } }
        self.demo: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
        # behavioral: { CAT: { value: [wsum, wtot] } }
        self.beh: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
        self.interests: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        self.locations: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])

    def ingest(self, parsed: dict, sample_size: int, projected_us: int = 0) -> None:
        if not parsed or sample_size <= 0:
            return
        self.profile_count += 1
        self.total_sample += sample_size
        # Self-weighted: weight each profile's sample size by its own
        # sample size. Finalize divides by sum(s_i) to get the
        # sample-weighted average sample size (see __init__ docstring).
        self.sample_weighted_sum += float(sample_size) * float(sample_size)
        if projected_us > 0:
            self.proj_weighted_sum += float(projected_us) * float(sample_size)
            self.total_projection += int(projected_us)
            self.profiles_with_projection += 1

        for cat, buckets in (parsed.get("demographics") or {}).items():
            for bucket, pct in buckets.items():
                acc = self.demo[cat][bucket]
                acc[0] += pct * sample_size
                acc[1] += sample_size

        for cat, items in (parsed.get("behavioral") or {}).items():
            seen: set[str] = set()
            for value, pct in items:
                # Collapse cross-spelling within one profile by keeping
                # the first occurrence (matches parseAllData's "if not
                # exists" guard).
                if value in seen:
                    continue
                seen.add(value)
                acc = self.beh[cat][value]
                acc[0] += pct * sample_size
                acc[1] += sample_size

        for value, pct in (parsed.get("interests") or {}).items():
            acc = self.interests[value]
            acc[0] += pct * sample_size
            acc[1] += sample_size

        loc_seen: set[str] = set()
        for value, pct in (parsed.get("locations") or []):
            if value in loc_seen:
                continue
            loc_seen.add(value)
            acc = self.locations[value]
            acc[0] += pct * sample_size
            acc[1] += sample_size

    def finalize(self) -> dict:
        def avg(acc: list[float]) -> float:
            wsum, wtot = acc
            if wtot <= 0:
                return 0.0
            # 4-decimal rounding matches Rule #3 (never perfectly round
            # to 2dp boundaries unless it's a legit 0).
            return round(wsum / wtot, 4)

        # ------------------------------------------------------------------
        # Rule #3a math for the Norm row's Sample Size + US #.
        #
        # Step 1: collapse the category's profile-level sample sizes to a
        # single representative number via the sample-weighted average:
        #     weighted_avg_sample_size = sum(s_i^2) / sum(s_i)
        # This puts the displayed "Sample Size" cell on the same order of
        # magnitude as a single profile (which is what users expect),
        # rather than the raw sum across hundreds of profiles.
        #
        # Step 2: derive the US projection canonically from that single
        # representative sample size:
        #     weighted_avg_projection = round(
        #         weighted_avg_sample_size / 10_000_000 * 329_900_000
        #     )
        # This keeps the Norm row internally consistent with the
        # dashboard's canonical Raw -> Proj math (the same formula every
        # profile's BRAND INPUT row obeys after recompute_raw_and_projection
        # runs), independent of whatever projection values happen to live
        # in the source CSVs. Older norms files used a separate
        # sample-weighted average of the source projection columns; that
        # could drift whenever a source CSV's projection was stale, so
        # we now ALWAYS derive from sample size + Rule #3a.
        # ------------------------------------------------------------------
        weighted_avg_sample_size = 0
        if self.total_sample > 0 and self.sample_weighted_sum > 0:
            weighted_avg_sample_size = int(round(
                self.sample_weighted_sum / float(self.total_sample)
            ))
        weighted_avg_projection = 0
        if weighted_avg_sample_size > 0:
            weighted_avg_projection = int(round(
                weighted_avg_sample_size / CANONICAL_SAMPLE_SIZE * US_POPULATION
            ))

        # Diagnostic: the OLD weighted-average-of-projection-columns
        # number, kept around so an admin can sanity-check drift between
        # canonical (Rule #3a derived) and source-derived projections.
        legacy_weighted_avg_projection = 0
        if self.total_sample > 0 and self.proj_weighted_sum > 0:
            legacy_weighted_avg_projection = int(round(
                self.proj_weighted_sum / float(self.total_sample)
            ))

        result: dict[str, Any] = {
            "category": self.category,
            "profile_count": self.profile_count,
            # Raw stats — the sum across every ingested profile. Useful
            # for "n=" diagnostics; the dashboard does NOT use these for
            # the Sample Size / US # display anymore (see *_weighted_avg_*
            # fields below).
            "total_sample": int(self.total_sample),
            "total_projection": int(self.total_projection),
            "profiles_with_projection": int(self.profiles_with_projection),
            # CANONICAL Norm row values — the dashboard's Crosswalk
            # Respondents card reads `weighted_avg_sample_size` and the
            # US # card reads `weighted_avg_projection`. Both follow
            # Rule #3a; their ratio is always Raw/10M * 329.9M.
            "weighted_avg_sample_size": weighted_avg_sample_size,
            "weighted_avg_projection": weighted_avg_projection,
            # For drift detection only — see comment above.
            "legacy_weighted_avg_projection": legacy_weighted_avg_projection,
            "demographics": {},
            "behavioral": {},
            "interests": {},
            "locations": [],
        }

        for cat, buckets in self.demo.items():
            result["demographics"][cat] = {b: avg(acc) for b, acc in buckets.items()}

        for cat, items in self.beh.items():
            arr = [
                {"name": value, "pct": avg(acc)}
                for value, acc in items.items()
            ]
            # Sort by pct desc so consumers can lop off a top-N easily.
            arr.sort(key=lambda r: r["pct"], reverse=True)
            result["behavioral"][cat] = arr

        result["interests"] = {value: avg(acc) for value, acc in self.interests.items()}

        result["locations"] = [
            {"name": value, "pct": avg(acc)}
            for value, acc in self.locations.items()
        ]
        result["locations"].sort(key=lambda r: r["pct"], reverse=True)

        return result


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def compute_norm_for_category(s3, category: str, jobs: list[dict], *, workers: int = 8) -> dict | None:
    """Fetch + parse every profile in `jobs` and accumulate into a
    single category norm. Returns None when no profiles were ingestible
    (no CSVs found / all sample sizes 0)."""
    acc = CategoryAccumulator(category)
    lock = threading.Lock()
    ok = [0]
    fail = [0]

    def _process(job: dict) -> None:
        key = (job.get("s3_key") or job.get("job_id") or "").strip()
        if not key:
            return
        # purgatory entries are not "live" profiles.
        if key.startswith("purgatory/"):
            return
        df = fetch_profile_csv(s3, key)
        if df is None:
            with lock:
                fail[0] += 1
            return
        size = extract_sample_size(df)
        if size <= 0:
            with lock:
                fail[0] += 1
            return
        proj = extract_projected_us(df)
        parsed = parse_profile_rows(df)
        with lock:
            acc.ingest(parsed, size, proj)
            ok[0] += 1

    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_process, jobs))
    elapsed = time.time() - started

    if acc.profile_count == 0:
        return None
    final = acc.finalize()
    final["_meta"] = {
        "fetched_ok": ok[0],
        "fetched_failed": fail[0],
        "elapsed_seconds": round(elapsed, 2),
    }
    return final


def load_norms(s3) -> dict:
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=NORMS_KEY)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return {"norms": {}, "skipped_categories": {}}
    except Exception:
        return {"norms": {}, "skipped_categories": {}}


def save_norms(s3, payload: dict) -> None:
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=NORMS_KEY,
        Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Compute per-category BP norms.")
    p.add_argument("--workers", type=int, default=8,
                   help="Parallel S3 fetches per category (default 8).")
    p.add_argument("--only", default="",
                   help="Compute only this category (e.g. ACTOR). Repeatable via comma.")
    p.add_argument("--min", type=int, default=1,
                   help="Skip categories with fewer than N profiles (default 1 — i.e. compute everything).")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute but don't upload to S3.")
    args = p.parse_args()

    only_cats: set[str] | None = None
    if args.only:
        only_cats = {_norm_cat(c) for c in args.only.split(",") if c.strip()}

    s3 = _make_s3()
    print(f"Loading {S3_CACHE_KEY} from s3://{S3_BUCKET}/ …")
    jobs = load_jobs_cache(s3)
    print(f"  -> {len(jobs)} jobs")

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for j in jobs:
        cat = _norm_cat(j.get("category") or "")
        if not cat:
            continue
        by_cat[cat].append(j)

    targets = sorted(by_cat.keys())
    if only_cats:
        targets = [c for c in targets if c in only_cats]
    if not targets:
        print("No categories match the filter; nothing to do.")
        return 0

    # Preserve previously-computed norms for categories we're not
    # touching this run (so --only doesn't accidentally wipe them).
    existing = load_norms(s3)
    norms = dict(existing.get("norms") or {})
    skipped: dict[str, str] = dict(existing.get("skipped_categories") or {})

    for cat in targets:
        cat_jobs = by_cat[cat]
        if len(cat_jobs) < args.min:
            msg = f"fewer than {args.min} profiles ({len(cat_jobs)})"
            print(f"[skip] {cat}: {msg}")
            skipped[cat] = msg
            norms.pop(cat, None)
            continue
        print(f"[compute] {cat}: {len(cat_jobs)} profiles, workers={args.workers}")
        try:
            norm = compute_norm_for_category(s3, cat, cat_jobs, workers=args.workers)
        except Exception as e:
            print(f"  ! exception: {e}")
            traceback.print_exc()
            continue
        if not norm:
            print(f"  ! no profiles were parseable; leaving prior norm in place")
            continue
        meta = norm.pop("_meta", {})
        # Show both the raw sum and the canonical weighted-average so an
        # operator can eyeball whether the math collapsed sanely.
        # Rule #3a derived projection should be ~3.3% of total US pop
        # times (avg_sample / 10M).
        was = norm.get("weighted_avg_sample_size", 0)
        wap = norm.get("weighted_avg_projection", 0)
        print(f"  ✓ {norm['profile_count']} profiles, total_sample={norm['total_sample']:,}, "
              f"avg_sample={was:,}, projected_us={wap:,}, "
              f"behav cats={len(norm['behavioral'])}, demo cats={len(norm['demographics'])}, "
              f"interests={len(norm['interests'])}, locations={len(norm['locations'])} "
              f"({meta.get('elapsed_seconds')}s, fail={meta.get('fetched_failed')})")
        norms[cat] = norm
        skipped.pop(cat, None)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "compute_category_norms.py",
        "min_profiles": args.min,
        "norms": norms,
        "skipped_categories": skipped,
    }

    if args.dry_run:
        print(f"\n[dry-run] Would upload {len(norms)} category norms to "
              f"s3://{S3_BUCKET}/{NORMS_KEY}")
        return 0

    print(f"\nUploading {len(norms)} category norms to s3://{S3_BUCKET}/{NORMS_KEY} …")
    save_norms(s3, payload)
    print("✅ Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
