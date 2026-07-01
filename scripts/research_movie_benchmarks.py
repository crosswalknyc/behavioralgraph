"""Pre-research metadata for the 80 movie titles in genre_benchmarks.csv.

Reads /Users/jennamenking/Downloads/genre_benchmarks.csv and, for each
unique Title, queries Claude (with web_search) to populate:

  - production_company   (canonical studio string; used for category)
  - streaming_platform   (primary US streaming home today; lower-case
                          pipeline-friendly form: "netflix", "hulu",
                          "hbo max", "amazon prime video", "disney+",
                          "peacock", "paramount+", "apple tv+")
  - streaming_release_us (YYYY-MM-DD, the first US streaming-availability
                          date on the platform above)
  - mapped_genre         (one of the canonical pipeline movie genres;
                          see GENRE_MAP below)
  - canonical_installment (for franchises like John Wick / Dune /
                           Hunger Games, the specific movie to anchor
                           the run to — typically the latest installment
                           because its data is freshest and most relevant
                           to current dashboards)
  - context_note         (short paragraph for run_synthetic_svod
                          --context-note)

Saves the result to bg-webapp/scripts/movie_benchmarks_research.json so a
follow-up runner can iterate without re-spending Claude tokens.

Why batched by genre: cheaper and faster than 80 single-title calls, and
Claude's reasoning is more consistent across same-genre titles when it
sees them together. Each genre group is ~8 titles.

Usage:
    cd bg-webapp && python3 scripts/research_movie_benchmarks.py
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from claude_client import claude_messages  # noqa: E402

CSV_PATH = Path("/Users/jennamenking/Downloads/genre_benchmarks.csv")
OUT_PATH = HERE / "scripts" / "movie_benchmarks_research.json"

# Genre map: source CSV label → pipeline-compatible genre label.
# Kept aligned with the genre strings already in use across the 92 existing
# CSVs in s3://svod-acquisition/ (Movie - Action, Adult Animation, etc.).
GENRE_MAP = {
    "Action / Action Thriller":      "Movie - Action",
    "Sci-Fi / Fantasy":              "Movie - Sci-Fi Fantasy",
    "Elevated Horror":               "Movie - Elevated Horror",
    "Pop / Camp Horror":             "Movie - Camp Horror",
    "Family Animation":              "Movie - Family Animation",
    "Family Live Action":            "Movie - Family Live Action",
    "Bro Comedy":                    "Movie - Bro Comedy",
    "Female Ensemble Comedy":        "Movie - Female Ensemble Comedy",
    "Romantic Comedy":               "Movie - Romantic Comedy",
    "Prestige Drama / Awards Drama": "Movie - Prestige Drama",
}


def load_unique_titles() -> "OrderedDict[str, dict]":
    """Return OrderedDict[title -> {genre, sample_stream}] preserving CSV order."""
    out: "OrderedDict[str, dict]" = OrderedDict()
    with open(CSV_PATH, "r", errors="replace") as f:
        rdr = csv.reader(f)
        next(rdr)  # header
        for r in rdr:
            if not r or not r[0].strip():
                continue
            title = r[0].strip()
            genre = r[1].strip() if len(r) > 1 else ""
            stream = r[3].strip().strip('"') if len(r) > 3 else ""
            if title not in out:
                out[title] = {"genre_source": genre, "sample_stream": stream}
    return out


def research_genre_batch(genre_source: str, titles: list[str]) -> list[dict]:
    """Ask Claude to research a batch of titles in the same source genre.

    Returns a list of dicts (same order as `titles`) with the metadata
    fields documented above.
    """
    target_genre = GENRE_MAP.get(genre_source, f"Movie - {genre_source}")

    titles_block = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(titles))
    system = (
        "You are a streaming-industry research assistant. For each movie or "
        "franchise listed below, return the canonical production company / "
        "primary studio (the entity an analyst would credit), the primary US "
        "streaming platform where viewers can stream it today, the US "
        "streaming-availability date on that platform (YYYY-MM-DD; if the "
        "earliest streaming date isn't precisely known, give your best "
        "estimate from the title's known SVOD-window history), and a short "
        "context note for downstream analytics.\n\n"
        "RULES:\n"
        " - Output STRICT JSON only — no prose before/after.\n"
        " - Schema: { \"results\": [ { \"title\": str, \"production_company\": "
        "str, \"streaming_platform\": str, \"streaming_release_us\": str, "
        "\"canonical_installment\": str, \"context_note\": str } ] }.\n"
        " - One entry per input title, in the same order.\n"
        " - production_company: a single short uppercase-friendly label like "
        "\"Lionsgate\", \"Sony\", \"Warner Bros.\", \"Universal\", \"Disney\", "
        "\"Netflix\", \"A24\", \"Blumhouse\", \"Amazon MGM\", \"Apple\". When "
        "multiple companies share credit, pick the most analyst-recognizable "
        "one — usually the major-studio distributor.\n"
        " - streaming_platform: one of {netflix, hulu, hbo max, amazon prime "
        "video, disney+, peacock, paramount+, apple tv+}. If a title rotates "
        "platforms, pick the platform it's currently on in the US as of "
        "mid-2026; if unsure, give the platform where it has been most "
        "consistently available.\n"
        " - streaming_release_us: YYYY-MM-DD. For films originally released "
        "theatrically, give the date the film first became available on the "
        "chosen platform's SVOD library (NOT theatrical release). For "
        "Netflix Originals, that's the worldwide premiere date.\n"
        " - canonical_installment: for franchises with multiple installments "
        "(e.g. \"John Wick\" → John Wick / Chapter 2 / 3 / 4) name the "
        "single installment whose streaming run is the freshest in the "
        "platform window — typically the most recent sequel. Use the FULL "
        "title including the chapter/part qualifier. For standalone films "
        "echo the input title.\n"
        " - context_note: 2-3 sentence analyst-facing summary. Mention the "
        "production company, lead cast, why this title is the benchmark for "
        "the source genre, and (for franchises) which installment you're "
        "anchoring the metrics to.\n"
        " - Do not invent platforms. If a title was theatrical-only or PVOD-"
        "only with no SVOD home, pick \"\" for streaming_platform and "
        "\"\" for streaming_release_us and note that in context_note.\n"
    )
    user = (
        f"Source genre bucket from the analyst's benchmark sheet: "
        f"{genre_source!r}\n"
        f"Pipeline-side mapped genre we'll write into the tracker: "
        f"{target_genre!r}\n\n"
        f"Titles to research ({len(titles)}):\n{titles_block}\n\n"
        f"Return JSON now."
    )
    print(f"\n🔍 Researching genre={genre_source!r} "
          f"({len(titles)} titles) via Claude (web search on)…")
    raw = claude_messages(
        system=system,
        user=user,
        max_tokens=4096,
        temperature=0.2,
        tools=[{"type": "web_search_20250305", "name": "web_search",
                "max_uses": 8}],
    )
    if not raw:
        print(f"   ⚠️ Claude returned empty — falling back to stubs")
        return [
            {"title": t, "production_company": "", "streaming_platform": "",
             "streaming_release_us": "", "canonical_installment": t,
             "context_note": ""}
            for t in titles
        ]
    # Extract JSON — Claude sonnet sometimes wraps responses in conversational
    # prose + ```json fences. Try several extraction strategies in order:
    #   1. Markdown code fence: ```json ... ```
    #   2. First top-level {...} block via bracket-counting (handles nested)
    #   3. Naive last-resort: raw string
    results: list = []
    candidates: list[str] = []
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if fence_match:
        candidates.append(fence_match.group(1))
    # Bracket-balanced scan from first '{'
    first = raw.find("{")
    if first != -1:
        depth = 0
        for j, ch in enumerate(raw[first:], start=first):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(raw[first:j+1])
                    break
    candidates.append(raw)  # last resort
    for cand in candidates:
        try:
            data = json.loads(cand)
            results = data.get("results") or []
            if results:
                break
        except json.JSONDecodeError:
            continue
    if not results:
        print(f"   ⚠️ JSON parse failed across {len(candidates)} candidates")
        print(f"      raw[:500]: {raw[:500]!r}")
    # Reorder/align to input order so a caller can zip(titles, results) safely.
    by_title = {(r.get("title") or "").strip().lower(): r for r in results}
    aligned = []
    for t in titles:
        r = by_title.get(t.strip().lower(), {})
        aligned.append({
            "title": t,
            "production_company": (r.get("production_company") or "").strip(),
            "streaming_platform": (r.get("streaming_platform") or "").strip().lower(),
            "streaming_release_us": (r.get("streaming_release_us") or "").strip(),
            "canonical_installment": (r.get("canonical_installment") or t).strip(),
            "context_note": (r.get("context_note") or "").strip(),
        })
    return aligned


def main() -> int:
    if not CSV_PATH.exists():
        print(f"❌ Missing input: {CSV_PATH}")
        return 1

    titles = load_unique_titles()
    print(f"📂 Loaded {len(titles)} unique titles from {CSV_PATH.name}")

    # Group titles by source genre for batched research.
    by_genre: "OrderedDict[str, list[str]]" = OrderedDict()
    for t, meta in titles.items():
        by_genre.setdefault(meta["genre_source"], []).append(t)

    print(f"📦 Genre groups: {len(by_genre)}")
    for g, ts in by_genre.items():
        print(f"   - {g}: {len(ts)} titles")

    # Run Claude research, one batch per genre.
    research: dict[str, dict] = {}
    for g, ts in by_genre.items():
        results = research_genre_batch(g, ts)
        for r in results:
            t = r["title"]
            research[t] = {
                **r,
                "genre_source": g,
                "mapped_genre": GENRE_MAP.get(g, f"Movie - {g}"),
                "sample_stream": titles[t]["sample_stream"],
            }
        # Persist incrementally in case a later genre fails.
        OUT_PATH.write_text(json.dumps(research, indent=2))
        print(f"   💾 Saved progress → {OUT_PATH.name} ({len(research)} titles)")

    print(f"\n✅ Research complete: {len(research)} titles → {OUT_PATH}")

    # Print a tabular summary so the analyst can sanity-check before runs.
    print("\n=== RESEARCH SUMMARY ===")
    print(f"{'Title':<40} {'Studio':<18} {'Platform':<22} "
          f"{'StreamDate':<12} Genre")
    for t, r in research.items():
        print(f"{t[:39]:<40} {r['production_company'][:17]:<18} "
              f"{r['streaming_platform'][:21]:<22} "
              f"{r['streaming_release_us']:<12} {r['mapped_genre']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
