#!/usr/bin/env python3
"""
CLI wrapper around SVOD_Churn_Attribution.run_synthetic_attribution().

Generates a Subscriber-IQ tracker CSV without a ClickHouse panel pull, runs
it through the AI validation framework (Claude reasoning, tier floor,
demographic alignment, evidence threshold, new-content guard), and
optionally uploads to s3://svod-acquisition/ + sets the dashboard category.

Usage:
    python3 bg-webapp/run_synthetic_svod.py \
        --title "Grimsburg - Season 3" \
        --platform "hulu" \
        --start 2025-02-16 --end 2025-08-16 \
        --genre "Animated Adult Comedy" \
        --cadence "Weekly" \
        --episodes 2025-02-16,2025-02-23,2025-03-02,2025-03-09,2025-03-16,2025-03-23,2025-04-30,2025-05-29,2025-06-05,2025-06-12,2025-06-19,2025-07-10,2025-07-17 \
        --returning \
        --category "SERIES - FOX" \
        --upload

For one-off telecasts pass --new (instead of --returning) and a single
--episodes date. For a movie pass --new and no --episodes.

Required env vars:
    OPENAI_API_KEY        — for AI viewership validation + demographic
                            alignment fallback.
    ANTHROPIC_API_KEY     — for Claude reasoning framework.
    USE_CLAUDE_REASONING  — set to "1" to enable Claude (default).
    AWS credentials       — only required if --upload is set.
"""
from __future__ import annotations
import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "migration"))
sys.path.insert(0, str(HERE))

# Importing SVOD_Churn_Attribution triggers the claude_client pin and the
# OUTPUT_DIVISOR / US_POPULATION / SAMPLE_REPRESENTS constants used by the
# synthetic builders.
import SVOD_Churn_Attribution as svod   # noqa: E402


def _sanitize_filename(s: str) -> str:
    """Make a string safe for use as an S3 key / CSV filename."""
    s = s.strip()
    # Replace spaces with underscores; collapse repeat underscores.
    s = re.sub(r'\s+', '_', s)
    # Strip filesystem-hostile chars
    s = re.sub(r'[<>:"/\\|?*\']', '', s)
    return s


def _parse_episodes(s: str | None) -> list[str]:
    if not s:
        return []
    raw = [t.strip() for t in s.split(',') if t.strip()]
    out = []
    for r in raw:
        # Accept YYYY-MM-DD and MM-DD-YYYY
        for fmt in ("%Y-%m-%d", "%m-%d-%Y"):
            try:
                d = datetime.strptime(r, fmt)
                out.append(d.strftime("%Y-%m-%d"))
                break
            except ValueError:
                continue
        else:
            raise SystemExit(f"Could not parse episode date {r!r} "
                             f"(expected YYYY-MM-DD or MM-DD-YYYY)")
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate a Subscriber-IQ tracker CSV from minimal "
                    "inputs (no ClickHouse needed).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('--title',    required=True,
                   help='Display title for the CSV "Show/Content Tracked" cell '
                        '(e.g. "Grimsburg - Season 3").')
    p.add_argument('--platform', required=True,
                   help='Streaming platform (e.g. netflix, hulu, '
                        'amazon prime video, disney+, hbo max, peacock, '
                        'paramount+, apple tv+).')
    p.add_argument('--start',    required=True,
                   help='Campaign start date YYYY-MM-DD.')
    p.add_argument('--end',      required=True,
                   help='Campaign end date YYYY-MM-DD (typically 30 days '
                        'past the last episode air date).')
    p.add_argument('--genre',    default='',
                   help='Genre label, e.g. "Serialized Drama", '
                        '"Animated Adult Comedy", "Single Event Telecast".')
    p.add_argument('--cadence',  default='Weekly',
                   help='Content cadence: Weekly | All at Once | '
                        'Single Event Telecast (default: Weekly).')
    p.add_argument('--episodes', default=None,
                   help='Comma-separated episode air dates (YYYY-MM-DD). '
                        'Omit for movies / one-off specials.')
    p.add_argument('--new',       action='store_true',
                   help='Mark as a brand-new show (no prior seasons / '
                        'franchise to reactivate against).')
    p.add_argument('--returning', action='store_true',
                   help='Mark as a returning franchise (S2+, sequel, '
                        'reboot). Implies pre_existing_pct ≈ 30%%.')
    p.add_argument('--pre-existing-pct', type=float, default=None,
                   help='Override pre-existing-viewer share (0.0-1.0). '
                        'Default: 0.0 for --new, 0.30 for --returning.')
    p.add_argument('--reach-us',        type=int, default=None,
                   help='Override the tier×genre×cadence-derived US '
                        'uniques estimate.')
    p.add_argument('--conversion-pct',  type=float, default=None,
                   help='Override conversion rate (%% of clean sample).')
    p.add_argument('--output-dir', default='/tmp/svod_synthetic_runs',
                   help='Where to write the CSV locally (default: '
                        '/tmp/svod_synthetic_runs).')
    p.add_argument('--upload',   action='store_true',
                   help='Upload to s3://svod-acquisition/ when done.')
    p.add_argument('--category',
                   help='Dashboard category for the metadata entry '
                        '(e.g. "MOVIE - NETFLIX", "SERIES - FOX"). '
                        'Required if --upload is set.')
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.new and args.returning:
        raise SystemExit("Specify exactly one of --new or --returning.")
    if args.upload and not args.category:
        raise SystemExit("--upload requires --category (so the dashboard "
                         "knows where to surface the title).")

    is_new = bool(args.new) or (not args.returning)  # default new

    try:
        cs = datetime.strptime(args.start, "%Y-%m-%d")
        ce = datetime.strptime(args.end,   "%Y-%m-%d")
    except ValueError as e:
        raise SystemExit(f"Bad date: {e}")

    episode_dates = _parse_episodes(args.episodes)

    project_name = _sanitize_filename(args.title)

    config = {
        "project_name":        project_name,
        "show_search_terms":   [args.title],
        "platform_name":       args.platform.lower(),
        "campaign_start":      cs,
        "campaign_end":        ce,
        "exclusion_days":      180,
        "attribution_window":  30,
        "genre":               args.genre,
        "content_cadence":     args.cadence,
        "is_new_show":         is_new,
        "episode_dates":       episode_dates,
        "output_dir":          args.output_dir,
        "upload_to_s3":        bool(args.upload),
        "dashboard_category":  args.category,
    }
    if args.pre_existing_pct is not None:
        config["pre_existing_pct"] = args.pre_existing_pct
    if args.reach_us is not None:
        config["reach_us_override"] = args.reach_us
    if args.conversion_pct is not None:
        config["conversion_pct"] = args.conversion_pct

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    result = svod.run_synthetic_attribution(config)

    print("\n" + "=" * 70)
    print("  RESULT")
    print("=" * 70)
    for k, v in result.items():
        print(f"  {k}: {v}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
