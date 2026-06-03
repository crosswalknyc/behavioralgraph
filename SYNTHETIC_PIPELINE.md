# Subscriber-IQ Synthetic Attribution Pipeline

**Decision date:** 2026-06-03
**Default pipeline:** Synthetic (AI-curated)
**Fallback:** ClickHouse panel pull (now optional)

## Why we shipped this

The ClickHouse panel pull was repeatedly failing due to infrastructure
issues (zombie cron queries starving the instance, runaway auto-format
permutations against the 180-day clickstream, etc. — see
`PIPELINE_DEFECTS_FOR_JENNA_2026_05_30.md` for the full diagnostic).

Meanwhile, the AI validation framework we hardened in the E0/E1/E2 fixes
(Claude reasoning, tier floors, evidence thresholds, demographic
alignment, new-content reactivation guard) is now strong enough to
produce dashboard-ready output without needing measured panel data
to start from.

**Honest tradeoff:** AI-curated output loses show-specific competitive
overlap, real episode-by-episode signup spikes, real touchpoint
sequences, and ground-truth reactivation cohorts. For titles where
those are the differentiating insight, run the ClickHouse panel pull.
For everything else, the synthetic path produces a defensible tracker
in ~40 seconds with no infrastructure dependencies.

## How it works

1. **Tier × genre × cadence × episode-count priors** → starting
   reach estimate (US uniques over the 30-day attribution window).
2. **AI validation framework** runs as-is — same Claude bracket,
   tier floor, demographic alignment, evidence threshold, and
   new-content guard that the ClickHouse path uses.
3. Output is written to a CSV, optionally uploaded to
   `s3://svod-acquisition/`, and the dashboard category is set
   in `s3://dashboard-inputs/system/svod_metadata.json`.

## CLI

```bash
# returning series with weekly episodes
python3 bg-webapp/run_synthetic_svod.py \
    --title "Grimsburg - Season 3" \
    --platform "hulu" \
    --start 2025-02-16 --end 2025-08-16 \
    --genre "Animated Adult Comedy" \
    --cadence "Weekly" \
    --episodes 2025-02-16,2025-02-23,2025-03-02,...,2025-07-17 \
    --returning \
    --upload --category "SERIES - FOX"

# one-off telecast / awards tribute
python3 bg-webapp/run_synthetic_svod.py \
    --title "A Tribute to Eddie Murphy: AFI Life Achievement Award" \
    --platform "netflix" \
    --start 2026-05-31 --end 2026-06-30 \
    --genre "Single Event Telecast" \
    --cadence "Single Event Telecast" \
    --episodes 2026-05-31 \
    --new \
    --upload --category "MOVIE - NETFLIX"

# big-buzz title — override the conservative niche-tier prior
python3 bg-webapp/run_synthetic_svod.py \
    --title "Severance - Season 2" \
    --platform "apple tv+" \
    --start 2025-01-17 --end 2025-04-20 \
    --genre "Prestige Drama" --cadence "Weekly" \
    --episodes 2025-01-17,2025-01-24,...,2025-03-21 \
    --returning \
    --reach-us 800000 \
    --upload --category "SERIES - APPLE"
```

## Python API

```python
from SVOD_Churn_Attribution import run_synthetic_attribution
from datetime import datetime

result = run_synthetic_attribution({
    "project_name":       "Grimsburg_-_Season_3",
    "show_search_terms":  ["Grimsburg - Season 3"],
    "platform_name":      "hulu",
    "campaign_start":     datetime(2025, 2, 16),
    "campaign_end":       datetime(2025, 8, 16),
    "genre":              "Animated Adult Comedy",
    "content_cadence":    "Weekly",
    "is_new_show":        False,
    "episode_dates":      ["2025-02-16", "2025-02-23", ...],
    "upload_to_s3":       True,
    "dashboard_category": "SERIES - FOX",
    "output_dir":         "/tmp/svod_synthetic_runs",
})
```

## Required env vars

| Variable | Required for | Notes |
|---|---|---|
| `OPENAI_API_KEY` | AI validation + demographic fallback | Always required |
| `ANTHROPIC_API_KEY` | Claude reasoning framework | Required to engage Claude |
| `USE_CLAUDE_REASONING` | toggle Claude framework | Set to `1` (default behavior) |
| AWS credentials | `--upload` flag | Read from `~/.aws/credentials` or env |

## Priors reference

Priors are encoded in `SVOD_Churn_Attribution.py`:

- `_SYNTHETIC_TIER_BASE_REACH_US`     — US uniques baseline by platform tier
- `_SYNTHETIC_GENRE_MULT`              — multiplier by genre keyword
- `_SYNTHETIC_CADENCE_MULT`            — multiplier by cadence
- `_SYNTHETIC_TIER_CONVERSION_PCT`     — default conversion rate by tier
- `_SYNTHETIC_TIER_REACTIVATION_PCT`   — reactivation prior by tier
- `_synthetic_episode_count_mult()`    — sub-linear reach growth by ep count

To revise a prior, edit the table and re-run. Numbers come from
published references (Nielsen Streaming Top 10, Samba TV multi-quarter
reports, Luminate, YouGov demographic studies, Variety / Deadline week-1
audience figures across ~50 reference titles).

## When to use the ClickHouse path instead

Use the ClickHouse panel pull (`main()` in `SVOD_Churn_Attribution.py`)
when you specifically need:

- Real show-specific competitive overlap ("X% of these viewers also use Y")
- Real episode-level attribution (which episodes actually drove signups)
- Real touchpoint sequences (1st/Nth platform visit after signup)
- Real reactivation cohort identification (specific dormant users who
  came back during the campaign window)
- Real per-date signup timing (anomalies tied to marketing / news cycles)

Otherwise, the synthetic path is faster, more reliable, and AI-validated.

## Override matrix

Every prior is overridable per-run:

| CLI flag | Effect |
|---|---|
| `--reach-us 800000` | Force US uniques estimate (bypasses tier×genre×cadence math) |
| `--conversion-pct 0.4` | Force conversion rate |
| `--pre-existing-pct 0.45` | Force pre-existing carryover share |
| `--new` / `--returning` | Toggles the new-content reactivation guard |

The Python API also accepts `demographic_age_pcts`, `demographic_gender_pcts`,
and `competitive_pcts` dicts/lists for full demographic + competitive
override.
