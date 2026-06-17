"""Run the synthetic SVOD pipeline for every title in the benchmark sheet.

Reads bg-webapp/scripts/movie_benchmarks_research.json (produced by
research_movie_benchmarks.py) and, for each unique title, invokes
run_synthetic_attribution() in-process with the researched metadata.
Uploads each CSV + .research.json sidecar to s3://svod-acquisition/ and
sets the dashboard category to "MOVIES - <STUDIO>".

In-process invocation (vs spawning subprocess per title):
  - Skips Python/import startup x80
  - Lets us batch progress reporting and failure tracking
  - Lets us catch individual exceptions cleanly without aborting the run

Resume support: if a title already has a CSV at s3://svod-acquisition/
matching the title slug, the run is SKIPPED unless --force is passed.
This makes the script safe to re-run if something fails mid-batch.

Usage:
    cd bg-webapp && python3 scripts/run_movie_benchmarks.py
    cd bg-webapp && python3 scripts/run_movie_benchmarks.py --limit 5
    cd bg-webapp && python3 scripts/run_movie_benchmarks.py --filter "John Wick"
    cd bg-webapp && python3 scripts/run_movie_benchmarks.py --force
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "migration"))

import boto3  # noqa: E402
import SVOD_Churn_Attribution as svod  # noqa: E402

RESEARCH = HERE / "scripts" / "movie_benchmarks_research.json"
OUT_DIR  = Path("/tmp/svod_movie_benchmarks")
LOG_PATH = HERE / "scripts" / "movie_benchmarks_runlog.json"
BUCKET   = "svod-acquisition"


# ── Conversion-rate calibration (analyst-tuned, 2026-06-17) ─────────────
# The pipeline's default conversion-rate table is tier-based (0.55% for
# Netflix/Prime, 0.85% Hulu/Disney+/HBO Max, …). That's a reasonable prior
# for *flagship* original content — but for these benchmark runs we're
# spanning a 10x range from "tentpole Netflix Original event movie" to
# "licensed catalog title that landed on the platform via a distribution
# deal". The analyst's intuition (correct): a catalog title like Trap
# House (an Aura Entertainment movie that came to Netflix months after
# theatrical) doesn't drive new subscriber acquisition the way Damon &
# Affleck's The Rip does — it entertains existing subs but converts
# almost nobody. Empirically that's an order-of-magnitude difference.
#
# So we override conversion_pct on a per-title basis based on whether
# the title is "native" (made by the platform's parent studio family) or
# "licensed catalog" (came from a different studio).
#
# Maps studio → set of SVOD platforms owned by that studio family.
_STUDIO_OWNED_PLATFORMS: dict[str, set[str]] = {
    "Netflix":              {"netflix"},
    "Disney":               {"disney+", "hulu"},   # Disney is majority owner of Hulu
    "20th Century Studios": {"disney+", "hulu"},   # Disney subsidiary
    "Searchlight":          {"disney+", "hulu"},   # Disney subsidiary
    "Warner Bros.":         {"hbo max"},
    "Universal":            {"peacock"},
    "Paramount":            {"paramount+"},
    "Amazon MGM":           {"amazon prime video"},
    "Apple":                {"apple tv+"},
    "Hulu":                 {"hulu"},
    "Sony":                 set(),                  # Sony has no SVOD platform
    "Lionsgate":            set(),
    "A24":                  set(),
    "Blumhouse":            set(),                  # Universal-distributed but indie
    "IFC Films":            set(),
    "StudioCanal":          set(),
    "Annapurna":            set(),
}


def _calibrated_conversion_pct(studio: str, platform: str) -> tuple[float, str]:
    """Return (conversion_pct, source_label) for a benchmark movie run.

    Native title (studio owns the streaming platform): 0.50%
        — flagship SVOD-acquisition driver, the platform paid for it
          specifically to attract subscribers.
    Licensed catalog title (studio doesn't own the platform): 0.06%
        — entertains existing subscribers but rarely the reason new
          subs sign up; viewers know the title will rotate to another
          platform eventually.
    """
    owned = _STUDIO_OWNED_PLATFORMS.get(studio.strip(), set())
    if platform.strip().lower() in owned:
        return 0.50, f"native (studio={studio!r} owns {platform!r})"
    return 0.06, (
        f"licensed-catalog (studio={studio!r} does not own {platform!r})"
    )


def _slug(title: str) -> str:
    """Filesystem-safe slug for the title, matching run_synthetic_svod.py."""
    s = title.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[<>:\"/\\|?*']", "", s)
    return s


def _already_in_s3(s3, title: str) -> str | None:
    """Return the existing S3 key if a tracker for `title` is already there."""
    slug = _slug(title).lower()
    pager = s3.get_paginator("list_objects_v2")
    for page in pager.paginate(Bucket=BUCKET):
        for o in (page.get("Contents") or []):
            k = o["Key"]
            if "/" in k:
                continue
            if not k.lower().endswith(".csv"):
                continue
            kbase = k.lower().rsplit(".", 1)[0]
            # Strip the trailing _MM_DD_YYYY_HH_MM timestamp
            kbase_no_ts = re.sub(
                r"_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}$", "", kbase
            )
            if kbase_no_ts == slug:
                return k
    return None


def build_config(title: str, meta: dict) -> dict:
    """Construct the dict consumed by run_synthetic_attribution."""
    studio   = (meta.get("production_company") or "").strip()
    platform = (meta.get("streaming_platform") or "").strip().lower()
    release  = (meta.get("streaming_release_us") or "").strip()
    genre    = (meta.get("mapped_genre") or "").strip()
    canonical= (meta.get("canonical_installment") or title).strip()
    ctx      = (meta.get("context_note") or "").strip()

    if not release or not re.match(r"^\d{4}-\d{2}-\d{2}$", release):
        raise ValueError(f"{title}: invalid streaming_release_us {release!r}")
    if not platform:
        raise ValueError(f"{title}: missing streaming_platform")

    start = _dt.datetime.strptime(release, "%Y-%m-%d")
    end   = start

    cat_studio = studio.upper().rstrip(".")
    category = f"MOVIES - {cat_studio}" if cat_studio else "MOVIES"

    conv_pct, conv_source = _calibrated_conversion_pct(studio, platform)

    benchmark_note = (
        f"This is a GENRE BENCHMARK run for the '{meta.get('mapped_genre','')}' "
        f"category. Anchoring metrics to the canonical installment "
        f"'{canonical}' as a representative of the franchise/IP '{title}'. "
        f"Production company: {studio or 'unknown'}. Primary US streaming "
        f"platform: {platform or 'unknown'}. SVOD availability date used: "
        f"{release}. Use US household-level streaming reach typical for the "
        f"genre + platform tier; expect Day-0/Day-1 conversion bias "
        f"characteristic of binge-able movie drops on this platform. "
        f"Analyst-calibrated conversion rate: {conv_pct}% ({conv_source}). "
        f"{ctx}"
    )

    config: dict = {
        "project_name":         _slug(title),
        "show_search_terms":    [title],
        "platform_name":        platform,
        "campaign_start":       start,
        "campaign_end":         end,
        "exclusion_days":       180,
        "attribution_window":   30,
        "genre":                genre,
        "content_cadence":      "Binge",
        "is_new_show":          True,
        "conversion_pct":       conv_pct,
        "episode_dates":        [{
            "episode_num":   1,
            "air_date":      start,  # datetime, NOT date — downstream calls .date() on it
            "display_label": canonical,
        }],
        "upload_to_s3":         True,
        "s3_bucket":            BUCKET,
        "dashboard_category":   category,
        "output_dir":           str(OUT_DIR),
        "context_note":         benchmark_note,
    }
    return config


def run_one(title: str, meta: dict, s3, *, force: bool, dry_run: bool) -> dict:
    rec: dict = {
        "title":     title,
        "studio":    meta.get("production_company"),
        "platform":  meta.get("streaming_platform"),
        "genre":     meta.get("mapped_genre"),
        "status":    "?",
        "s3_key":    None,
        "category":  None,
        "reach_us":  None,
        "error":     None,
        "started":   _dt.datetime.now().isoformat(timespec="seconds"),
    }
    existing = _already_in_s3(s3, title)
    if existing and not force:
        rec["status"] = "skip_already_in_s3"
        rec["s3_key"] = existing
        return rec
    try:
        config = build_config(title, meta)
        rec["category"] = config["dashboard_category"]
        if dry_run:
            rec["status"] = "dry_run_ok"
            rec["s3_key"] = (
                f"{config['project_name']}_<TS>.csv  → "
                f"category={config['dashboard_category']}, "
                f"platform={config['platform_name']}, "
                f"date={config['campaign_start'].date()}"
            )
            return rec
        result = svod.run_synthetic_attribution(config)
        rec["status"]   = "ok"
        rec["s3_key"]   = result.get("s3_key")
        rec["reach_us"] = result.get("reach_us")
    except Exception as e:
        rec["status"] = "error"
        rec["error"]  = f"{type(e).__name__}: {e}"
        rec["traceback"] = traceback.format_exc()
    rec["finished"] = _dt.datetime.now().isoformat(timespec="seconds")
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit",  type=int, default=None,
                    help="Only run the first N titles (after filtering).")
    ap.add_argument("--filter", default="",
                    help="Substring filter on title (case-insensitive).")
    ap.add_argument("--force",  action="store_true",
                    help="Re-run even if a tracker already exists in S3.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan but don't actually run anything.")
    args = ap.parse_args(argv)

    if not RESEARCH.exists():
        print(f"❌ Missing research file: {RESEARCH}")
        return 1
    research = json.loads(RESEARCH.read_text())

    titles = [
        t for t in research.keys()
        if not args.filter or args.filter.lower() in t.lower()
    ]
    if args.limit:
        titles = titles[: args.limit]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    s3 = boto3.client("s3")
    log: list[dict] = []
    print(f"📦 Running {len(titles)} titles "
          f"(mode={'DRY-RUN' if args.dry_run else 'APPLY'}, "
          f"force={args.force})\n")
    for i, t in enumerate(titles, 1):
        meta = research[t]
        print(f"--- [{i:3d}/{len(titles)}] {t} "
              f"(studio={meta.get('production_company')}, "
              f"platform={meta.get('streaming_platform')}, "
              f"date={meta.get('streaming_release_us')}) ---")
        rec = run_one(t, meta, s3, force=args.force, dry_run=args.dry_run)
        log.append(rec)
        # Persist log incrementally
        LOG_PATH.write_text(json.dumps(log, indent=2))
        icon = {
            "ok":                 "✅",
            "skip_already_in_s3": "⚪",
            "dry_run_ok":         "📝",
            "error":              "💥",
        }.get(rec["status"], "?")
        print(f"   {icon} {rec['status']} → {rec.get('s3_key') or '(no key)'}"
              + (f"   reach_us={rec['reach_us']:,}" if rec.get("reach_us") else "")
              + (f"   ERROR: {rec['error']}" if rec.get("error") else ""))

    # Summary
    print(f"\n📊 SUMMARY ({len(log)} runs)")
    from collections import Counter
    by_status = Counter(r["status"] for r in log)
    for st, n in by_status.most_common():
        print(f"   {st}: {n}")
    if any(r["status"] == "error" for r in log):
        print("\n❗ Errors:")
        for r in log:
            if r["status"] == "error":
                print(f"   - {r['title']}: {r['error']}")
    print(f"\n💾 Full log: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
