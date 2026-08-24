#!/usr/bin/env python3
"""Pull The Lincoln Lawyer (Netflix) — Seasons 1-4 independently, one CSV per season.

The Lincoln Lawyer is a Netflix original legal drama from David E. Kelley
(developed/showrun by Ted Humphrey), based on Michael Connelly's Mickey
Haller novels. Manuel Garcia-Rulfo stars as Mickey Haller with Becki
Newton (Lorna), Jazz Raycole (Izzy), Angus Sampson (Cisco), and Neve
Campbell (Maggie McPherson). CBS originally developed and scrapped the
project; Netflix picked it up and it became a durable four-season
franchise with a fifth (final) season ordered.

Season structure (all verified):
    S1: Fri 05/13/2022 — 10 eps, all-at-once binge  (The Brass Verdict)
    S2: Thu 07/06/2023 — eps 1-5, Thu 08/03/2023 — eps 6-10  (The Fifth Witness)
    S3: Thu 10/17/2024 — 10 eps, all-at-once binge  (The Gods of Guilt)
    S4: Thu 02/05/2026 — 10 eps, all-at-once binge  (The Law of Innocence)

Every season premiered post-1/1/2021 → fully within the panel tracking
window. No pre-2021 disclaimer fires. Each season is pulled
INDEPENDENTLY with its own campaign window, reach, conversion,
new/reactivated split, and pre-existing overrides. S2's two-part drop
is ONE season pull: campaign 07/06 → 08/03 with the 30-day attribution
window running past the Part 2 drop (through 09/02/2023).

Reach + conversion anchors are grounded in (per-season detail in each
config block below):
    - Netflix "What We Watched" engagement reports: S2 = 292.3M hrs /
      35.7M Views (H2 2023, #10 of the half); S3 = 276.8M hrs / 32.5M
      Views (H2 2024, #15); S4 = 295.5M hrs / 35.0M Views (H1 2026,
      #11 — see reference/netflix_what_we_watched/ in this repo).
    - Netflix weekly Top 10: S1 debut 45.09M hrs (3 days, #2), wk2
      108.09M hrs (#1), 305.27M hrs total May 8-Jun 19 2022; S2 P1
      31.4M hrs first 4 days + 8.3M Views wk of 7/10, P2 week 55.2M
      hrs / 6.7M Views, 23.3M cumulative Views by 8/6/23; S3 7.0M
      Views first 4 days, 8.5M wk2, weekly decay through 11/24; S4
      9.6M Views wk of 2/9/26 (#1 English TV).
    - Nielsen US streaming charts: S1 884M min premiere week + 1.66B
      min wk2 (best in show history); S2 1.41B min (7/3-9/23) + 1.7B
      min (7/31-8/6/23); S3 1.638B min (10/14-20/24, third-best week
      in show history).
    - Global Views → US reach conversion per netflix-what-we-watched
      rule: adult scripted legal procedural ≈ one-third US share
      (Anglo-skewed: Top 10 in 74 countries S1 / 81 countries S2 but
      "not making an impact in Asia" per trade coverage), with a
      per-season Views/Reach ratio reasoned from sampler-vs-loyalist
      mix (NOT one flat constant across seasons).
    - Netflix US paid-sub base at each launch: ~66M (May 2022, the
      churn-crisis spring), ~68M (Jul 2023, paid-sharing crackdown
      era), ~77M (Oct 2024), ~90M (Feb 2026, Antenna Q1'26).

Per workspace mandate: every per-season number below is independently
reasoned from that season's own evidence. No cross-season multiplier,
no decay formula, no shared rates. The S2 new-share UPTICK vs S1 (0.53
vs 0.48) is deliberate: the May-2023 US paid-sharing crackdown
converted password-borrowers into first-time account holders, a
documented external event unique to S2's window.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))

# Force Claude reasoning + load .env for ANTHROPIC_API_KEY (same pattern
# as pull_beef_seasons.py / pull_the_bear_s5.py). Using = instead of
# setdefault so an empty env var from the parent shell still gets
# flipped on.
os.environ["USE_CLAUDE_REASONING"] = "1"
_ENV_FILE = _REPO / ".env"
if _ENV_FILE.exists():
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(_ENV_FILE)
    except Exception:
        for _line in _ENV_FILE.read_text().splitlines():
            if not _line or _line.lstrip().startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from SVOD_Churn_Attribution import run_synthetic_attribution  # noqa: E402


def _eps_binge(start: str, count: int) -> list[dict]:
    """Netflix all-at-once binge release — all episodes drop day one."""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    return [
        {
            "episode_num":   i + 1,
            "air_date":      start_dt,
            "display_label": f"Episode {i + 1}",
        }
        for i in range(count)
    ]


def _eps_two_part(part1: str, part2: str, per_part: int = 5) -> list[dict]:
    """Two-part Netflix drop — eps 1-5 on part1 date, 6-10 on part2 date."""
    d1 = datetime.strptime(part1, "%Y-%m-%d")
    d2 = datetime.strptime(part2, "%Y-%m-%d")
    eps = []
    for i in range(per_part):
        eps.append({"episode_num": i + 1, "air_date": d1,
                    "display_label": f"Episode {i + 1}"})
    for i in range(per_part):
        eps.append({"episode_num": per_part + i + 1, "air_date": d2,
                    "display_label": f"Episode {per_part + i + 1}"})
    return eps


def _last_episode_date(episode_dates: list[dict]) -> datetime:
    return max(e["air_date"] for e in episode_dates)


# ── Genre + dashboard bucket ───────────────────────────────────────────
LEGAL_DRAMA   = "Legal Drama"
DASHBOARD_CAT = "SERIES - NETFLIX ORIGINAL DRAMA"


# ──────────────────────────────────────────────────────────────────────
# Per-season configs. Four independent trackers, one CSV each.
# ──────────────────────────────────────────────────────────────────────

CONFIGS: list[dict] = [
    # ─── Season 1 (May 13, 2022 — 10 eps binge) ────────────────────────
    #
    # ROW-BY-ROW REASONING:
    #
    # reach_us = 12,400,000
    #   S1 was a sleeper phenomenon: debuted #2 globally with 45.09M hrs
    #   in 3 days, then #1 English TV wk of 5/16-22 with 108.09M hrs
    #   (dethroning Ozark S4), #1 for three straight weeks, 305.27M
    #   global hrs across its 5/8-6/19 Top 10 run. First-28-day global
    #   hrs ≈ 263M (45.09 + 108.09 + ~70 + ~40) at 8:19 season runtime
    #   → ≈ 31.6M global Views-equivalent; first-30-day ≈ 32M.
    #   US share 0.36 — Anglo-skewed (Top 10 in 74 countries but trade
    #   coverage flagged it "popular in Australia, the UK, and the US
    #   while not making an impact in Asia"; above the ~1/3 scripted
    #   baseline). US views ≈ 11.5M. Views/Reach 0.95 — discovery hits
    #   carry a heavy casual-sampler pool (watch 1-2 eps, count
    #   fractionally in Views but fully in reach), which outweighs
    #   rewatch for a first-run procedural. reach ≈ 11.5/0.95 ≈ 12.1M.
    #   Nielsen US cross-check: 884M min premiere week + 1.66B wk2
    #   (best week in show history) + ~1.3B + ~0.9B ≈ 5.2B min TV-only
    #   in 28 days ≈ 116M all-device hrs ≈ 13.9M US completion-
    #   equivalents — brackets the anchor from above. Anchor: 12.4M.
    #
    # conv_pct = 1.15%
    #   May 2022 was Netflix's churn-crisis spring (Q2'22 lost 970K
    #   subs globally; UCAN -1.3M) — gross adds still flowed but the
    #   acquisition environment was the weakest in Netflix history.
    #   LL S1 was a massive ORGANIC in-base discovery hit (autoplay /
    #   rec-driven, older-skewing CBS-procedural audience largely
    #   already subscribed), the archetype that drives retention more
    #   than acquisition. But four straight weeks as the #1 US title
    #   pulls a real signup layer. Below prestige-buzz tier (Beef S1
    #   1.8%), well above deep-catalog. Anchor: 1.15% → ~143K US
    #   signups. Sanity: Antenna had Stranger Things S4 driving ~300K
    #   US signups that same window; LL as a moderate non-event driver
    #   at roughly half that is proportionate.
    #
    # new_share = 0.48
    #   Pre-crackdown 2022: mature service, and the older-skewing
    #   broadcast-procedural demo is disproportionately LAPSED-sub
    #   territory (had Netflix in the Ozark/House of Cards era,
    #   churned, returned for a courtroom drama everyone was talking
    #   about) → slight reactivation tilt. Anchor: 0.48 new / 0.52
    #   reactivated.
    #
    # pre_existing: omitted — series launch, no prior season exists
    #   (is_new=True → pipeline pins 0).
    {
        "project_name":  "The_Lincoln_Lawyer_-_Season_1",
        "title":         "The Lincoln Lawyer Season 1",
        "platform":      "netflix",
        "start":         "2022-05-13",
        "genre":         LEGAL_DRAMA,
        "cadence":       "Binge",
        "is_new":        True,
        "reach_us":      12_400_000,
        "conv_pct":      1.15,
        "new_share":     0.48,
        "episode_dates": _eps_binge("2022-05-13", 10),
        "context_note": (
            "The Lincoln Lawyer Season 1 — Netflix original legal drama, 10 "
            "episodes released all-at-once on Friday May 13, 2022. Created "
            "for TV by David E. Kelley, showrun by Ted Humphrey, adapting "
            "Michael Connelly's second Mickey Haller novel The Brass "
            "Verdict. Manuel Garcia-Rulfo stars as LA defense attorney "
            "Mickey Haller working out of a chauffeured Lincoln Navigator; "
            "Becki Newton (Lorna), Jazz Raycole (Izzy), Angus Sampson "
            "(Cisco), Neve Campbell (Maggie McPherson), Christopher Gorham "
            "(Trevor Elliott) co-star. CBS originally developed and "
            "scrapped the series; Netflix rescued it and it became the "
            "sleeper hit of spring 2022: #2 global debut with 45.09M hours "
            "in 3 days, then #1 English-language TV for three straight "
            "weeks (108.09M hours week of May 16-22, dethroning Ozark S4), "
            "Top 10 in 74 countries, 305.27M global hours across its "
            "May 8 - June 19 Top 10 run. Nielsen US: 884M minutes premiere "
            "week, 1.66B minutes week 2 — the best US week in the show's "
            "history. Audience skews older (45+), female-leaning, "
            "broadcast-procedural-native — CBS-refugee IP with the 2011 "
            "Matthew McConaughey film brand as an on-ramp. Netflix US paid "
            "subs at launch: ~66M, mid churn-crisis spring 2022 (Netflix "
            "lost 970K global subs in Q2'22), the weakest acquisition "
            "environment in Netflix history — signups here are organic "
            "discovery-driven, not event-driven. Season 2 was ordered one "
            "month after launch. Completion-rate expectation: mid-to-high "
            "60s — hooky case-of-the-week + serialized trial structure "
            "completes well, but a discovery hit this broad carries a "
            "large casual-sampler pool that dilutes full-season "
            "completion. Second-screen expectation: low 30s — older-"
            "skewing courtroom audience is attentive-viewing-first, with "
            "modest Twitter/Reddit case-theory discourse."
        ),
    },

    # ─── Season 2 (July 6 + August 3, 2023 — two 5-ep parts) ───────────
    #
    # ROW-BY-ROW REASONING:
    #
    # reach_us = 10,200,000
    #   Netflix WWR H2 2023 actuals: 292.3M hrs / 35.7M Views (8:11
    #   runtime), #10 show of the half. The campaign+attribution frame
    #   for this pull runs 7/6 → 9/2/23 (P1 drop through 30 days past
    #   P2); weekly Top 10 shape (P1: 31.4M hrs first 4 days, 8.3M
    #   Views wk of 7/10 at 4.14h P1 runtime; P2: 55.2M hrs / 6.7M
    #   Views wk of 7/31-8/6; 23.3M cumulative Views by 8/6; Netflix
    #   Tudum cited 40M cumulative franchise views by 8/29) puts ≈ 29M
    #   of the half's 35.7M global Views inside that frame (~81%,
    #   matching the two-hump decay). US share 0.34 — S2 reached Top
    #   10 in 81 countries vs S1's 74, slightly more international
    #   than S1 but still Anglo-anchored. US views ≈ 9.9M. Views/Reach
    #   0.98 — the two-part release adds P1-recap rewatch ahead of P2
    #   (inflates Views) while still carrying a sampler pool; nearly
    #   neutral. reach ≈ 10.1M. Nielsen US cross-check: 1.41B min wk
    #   of 7/3-9 (P1) and 1.7B min wk of 7/31-8/6 (P2, second-best
    #   week in show history) — title-level including S1 catch-up, so
    #   directionally consistent with a high-single-digit-millions S2
    #   uniques read. Anchor: 10.2M.
    #
    # conv_pct = 1.05%
    #   July 2023 = the US paid-sharing-crackdown summer (enforcement
    #   began May 23, 2023): Antenna measured the biggest US signup
    #   days since COVID lockdowns in the following weeks. A returning
    #   procedural intrinsically converts below its launch season
    #   (motivated fans already subscribed), but the crackdown
    #   uniquely monetized LL's password-borrowing viewers into their
    #   own accounts inside this exact window. Net: slightly below S1
    #   but held up by the crackdown tailwind. Anchor: 1.05% → ~60K
    #   US signups on the post-pre-existing clean sample.
    #
    # new_share = 0.53
    #   DELIBERATE UPTICK vs S1 (0.48): borrower-conversion accounts
    #   are NEW accounts by definition (the borrower never had one).
    #   Antenna's crackdown-window mix showed new-account share of
    #   gross adds spiking vs the 2022 baseline. This is a documented
    #   external event specific to S2's window, not a franchise-decay
    #   pattern. Anchor: 0.53 new / 0.47 reactivated.
    #
    # pre_existing_pct = 0.44
    #   S1's US reach was enormous (~12M) but sampler-heavy, and 13.5
    #   months elapsed between seasons. Meanwhile S2's window pulled
    #   heavy concurrent S1 catch-up (S1 re-charted at 3.4M Views the
    #   week of 7/10 and drew 20.5M global Views across H2 2023 —
    #   catch-up bingers reaching S2 within the same window read as
    #   fresh samples, not prior-window viewers). Roughly 4.5M of
    #   S2's ~10.2M US viewers had watched S1 in a prior window.
    #   Anchor: 0.44.
    {
        "project_name":  "The_Lincoln_Lawyer_-_Season_2",
        "title":         "The Lincoln Lawyer Season 2",
        "platform":      "netflix",
        "start":         "2023-07-06",
        "genre":         LEGAL_DRAMA,
        "cadence":       "Binge",
        "is_new":        False,
        "reach_us":      10_200_000,
        "conv_pct":      1.05,
        "new_share":     0.53,
        "pre_existing_pct": 0.44,
        "episode_dates": _eps_two_part("2023-07-06", "2023-08-03", 5),
        "context_note": (
            "The Lincoln Lawyer Season 2 — Netflix original legal drama, "
            "10 episodes released in TWO PARTS: episodes 1-5 on Thursday "
            "July 6, 2023 and episodes 6-10 on Thursday August 3, 2023. "
            "Adapts Michael Connelly's The Fifth Witness: Mickey defends "
            "chef Lisa Trammell (Lana Parrilla) in the murder of a real-"
            "estate developer. Manuel Garcia-Rulfo, Becki Newton, Jazz "
            "Raycole, Angus Sampson return; Neve Campbell recurs. This "
            "pull treats the full two-part run as ONE season: campaign "
            "July 6 - August 3 with the 30-day attribution window running "
            "through September 2, 2023. Netflix What We Watched H2 2023 "
            "actuals: 292.3M global hours / 35.7M Views — the #10 show on "
            "Netflix for the half. Weekly shape: Part 1 opened soft "
            "(31.4M hours first 4 days) then hit #1 English TV with 8.3M "
            "Views the week of July 10; Part 2 re-took #1 the week of "
            "July 31 - August 6 with 55.2M hours / 6.7M Views; 23.3M "
            "cumulative Views by August 6 and Top 10 in 81 countries. "
            "Nielsen US: 1.41B minutes week of July 3-9 and 1.7B minutes "
            "week of July 31 - August 6 (second-best US week in show "
            "history); concurrent S1 catch-up surged (S1 re-charted at "
            "3.4M Views mid-July and 17.4M hours the P2 week). CRITICAL "
            "window context: the US paid-sharing crackdown (enforcement "
            "from May 23, 2023) made July 2023 a record signup period — "
            "password-borrowing Lincoln Lawyer viewers were converted "
            "into their own NEW accounts inside this exact window, "
            "tilting the new-vs-reactivated mix toward new accounts "
            "relative to both S1 (2022) and later seasons. Netflix US "
            "paid subs at launch: ~68M. The two-part split disrupted "
            "binge momentum (soft P1 open, P2 rally). Completion-rate "
            "expectation: low 60s — the month-long part boundary sheds "
            "casual P1 viewers who never return for P2. Second-screen "
            "expectation: mid 30s — two release events doubled the "
            "social-discourse windows (case-theory threads, P2 "
            "anticipation) vs a single binge drop."
        ),
    },

    # ─── Season 3 (October 17, 2024 — 10 eps binge) ────────────────────
    #
    # ROW-BY-ROW REASONING:
    #
    # reach_us = 8,500,000
    #   Netflix WWR H2 2024 actuals: 276.8M hrs / 32.5M Views (8:31
    #   runtime), #15 show of the half. Weekly Top 10 detail: 7.0M
    #   Views first 4 days (#2 behind Outer Banks S4), 8.5M wk2 (#1
    #   English TV), 5.0M wk3, 3.4M wk4, 2.5M wk5, 1.7M wk6 → first-
    #   30-day global Views ≈ 25.7M (weeks 1-4 = 23.9M + partial wk5).
    #   US share 0.33 — franchise now fully internationalized (S2 hit
    #   Top 10 in 81 countries); scripted-original baseline. US views
    #   ≈ 8.5M. Views/Reach 1.00 — year-3 audience is loyalist-
    #   dominated (fewer one-episode samplers than S1/S2) with modest
    #   in-window rewatch; the two effects cancel. reach ≈ 8.5M.
    #   Nielsen US cross-check: 1.638B min week of 10/14-20 — third-
    #   best week in show history, #1 overall streaming title, "across
    #   30 episodes" (includes S1/S2 catch-up; S1 re-charted at 2.2M
    #   global Views that week). Anchor: 8.5M.
    #
    # conv_pct = 0.85%
    #   October 2024: Netflix US ~77M paid subs (UCAN 84.8M Q3'24),
    #   ad-tier scaling, no crackdown tailwind left — the borrower
    #   pool was already monetized in 2023. Year-3 franchise
    #   maintenance: the LL-motivated conversion pool is largely
    #   exhausted; what remains is lapsed-sub return plus a thin
    #   never-subscribed layer competing against a crowded fall slate
    #   (Outer Banks S4, Monsters, Love Is Blind all in-window).
    #   Anchor: 0.85% → ~32K US signups.
    #
    # new_share = 0.38
    #   No crackdown effect (unlike S2's window), franchise year 3:
    #   the dominant acquisition motion is lapsed subscribers churning
    #   back in for a known-quantity fall return. Genuinely-new
    #   accounts limited to late franchise discoverers. Anchor: 0.38
    #   new / 0.62 reactivated.
    #
    # pre_existing_pct = 0.56
    #   Two prior seasons with ~10-12M US reach each and 14.5 months
    #   since S2B. The year-3 audience is majority franchise-return:
    #   direct S2→S3 continuity viewers plus S1-era lapsed viewers
    #   pulled back by the fall marketing beat. Concurrent catch-up
    #   (S1 re-charting during S3's launch week) still feeds a real
    #   new-viewer layer, keeping pre-existing well under the
    #   returning-franchise ceiling. Anchor: 0.56.
    {
        "project_name":  "The_Lincoln_Lawyer_-_Season_3",
        "title":         "The Lincoln Lawyer Season 3",
        "platform":      "netflix",
        "start":         "2024-10-17",
        "genre":         LEGAL_DRAMA,
        "cadence":       "Binge",
        "is_new":        False,
        "reach_us":      8_500_000,
        "conv_pct":      0.85,
        "new_share":     0.38,
        "pre_existing_pct": 0.56,
        "episode_dates": _eps_binge("2024-10-17", 10),
        "context_note": (
            "The Lincoln Lawyer Season 3 — Netflix original legal drama, "
            "10 episodes released all-at-once on Thursday October 17, "
            "2024. Adapts Michael Connelly's The Gods of Guilt: Mickey "
            "defends a client accused of murdering Gloria Dayton (Glory "
            "Days), a case that turns personal. Manuel Garcia-Rulfo, "
            "Becki Newton, Jazz Raycole, Angus Sampson return; Neve "
            "Campbell appears; the season's emotional finale sets up the "
            "Sam Scales murder cliffhanger resolved in Season 4. Netflix "
            "What We Watched H2 2024 actuals: 276.8M global hours / 32.5M "
            "Views — #15 show on Netflix for the half. Weekly Top 10 "
            "shape: 7.0M Views in the first 4 days (#2 behind Outer Banks "
            "S4), 8.5M Views week 2 (#1 English TV), then 5.0M / 3.4M / "
            "2.5M / 1.7M across weeks 3-6 — first-30-day global Views "
            "about 25.7M. Nielsen US: 1.638B minutes week of October "
            "14-20, the third-best US week in show history and the #1 "
            "overall streaming title, with S1 re-charting (2.2M Views) on "
            "concurrent catch-up viewing. Franchise stage: YEAR-3 "
            "maintenance on a mature platform — Netflix US paid subs "
            "~77M at launch, ad-tier scaling, and no paid-sharing-"
            "crackdown signup tailwind left (that pool was monetized in "
            "2023, before this window). Audience is loyalist-dominated: "
            "older-skewing, procedural-native viewers returning 14.5 "
            "months after Season 2 Part 2 against a crowded fall slate "
            "(Outer Banks S4, Monsters: The Menendez Story, Love Is "
            "Blind S7 all in-window). Completion-rate expectation: low "
            "70s — the sampler pool is mostly gone by year 3; loyal "
            "returners binge the full arc. Second-screen expectation: "
            "high 20s — the older courtroom-drama core is lean-back, "
            "attentive viewing with lighter live-social chatter than "
            "younger-skewing binge hits."
        ),
    },

    # ─── Season 4 (February 5, 2026 — 10 eps binge) ────────────────────
    #
    # ROW-BY-ROW REASONING:
    #
    # reach_us = 9,100,000
    #   Netflix WWR H1 2026 actuals (this repo,
    #   reference/netflix_what_we_watched/): 295.5M hrs / 35.0M Views,
    #   released 2026-02-05 — #11 show of the half with ~4.8 months of
    #   in-window runway. Weekly Top 10: 9.6M Views week of Feb 9 (#1
    #   English TV; stronger week-2 than S3's 8.5M). First-30-day
    #   global Views ≈ 27.5M: S3's decay shape scaled to S4's stronger
    #   open gives ~28-29M, while the apples-to-apples half-window
    #   comparison (S3 accumulated ~36.5M Views over its own first 4.8
    #   months vs S4's 35.0M) says S4 sits ~4% under S3 on the full
    #   tail — so the 30-day figure lands between those pulls, ≈27.5M.
    #   US share 0.34 — US legal-thriller staple with US-heavy cast
    #   additions (Cobie Smulders, Sasha Alexander, Constance Zimmer);
    #   a hair above the one-third baseline. US views ≈ 9.4M.
    #   Views/Reach 1.03 — the year-4 audience is the most loyalist-
    #   pure of the run (near-zero samplers) and the Mickey-on-trial
    #   arc drew finale-week partial rewatch, tipping Views slightly
    #   above uniques. reach ≈ 9.1M. Anchor: 9.1M — a real rebound
    #   above S3 (8.5M), consistent with the strongest source-book
    #   arc of the series and pre-final-season anticipation.
    #
    # conv_pct = 0.75%
    #   February 2026: Netflix US ~90M paid subs (Antenna Q1'26) —
    #   deepest saturation of any LL window; the franchise's
    #   acquisition pool is four seasons depleted. February is a
    #   re-subscription month (post-holiday churners returning), which
    #   shows up in the reactivated column, not conversion rate. The
    #   S5-final-season renewal news added urgency for lapsed fans but
    #   doesn't mint never-subscribed converts at year 4. Anchor:
    #   0.75% → ~27K US signups.
    #
    # new_share = 0.33
    #   Year-4 franchise event on a ~90M-sub base: the motivated-but-
    #   unsubscribed pool is nearly empty; acquisition is dominated by
    #   lapsed subscribers churning back for the trial-of-Mickey arc
    #   plus catch-up (S3 pulled 8.5M Views in H1 2026 concurrent with
    #   S4's run — lapsed viewers returning to binge forward). Anchor:
    #   0.33 new / 0.67 reactivated.
    #
    # pre_existing_pct = 0.61
    #   Three prior seasons, 15.5 months since S3, and the deepest
    #   catalog base of the run feeding continuity (S3 re-charted hard
    #   during S4's window). The Law of Innocence arc specifically
    #   rewards prior-season investment (Sam Scales cliffhanger
    #   resolution), concentrating returners. Still below the 0.65
    #   ceiling because every LL season has added a genuine new-viewer
    #   layer via Netflix's recommendation flywheel. Anchor: 0.61.
    {
        "project_name":  "The_Lincoln_Lawyer_-_Season_4",
        "title":         "The Lincoln Lawyer Season 4",
        "platform":      "netflix",
        "start":         "2026-02-05",
        "genre":         LEGAL_DRAMA,
        "cadence":       "Binge",
        "is_new":        False,
        "reach_us":      9_100_000,
        "conv_pct":      0.75,
        "new_share":     0.33,
        "pre_existing_pct": 0.61,
        "episode_dates": _eps_binge("2026-02-05", 10),
        "context_note": (
            "The Lincoln Lawyer Season 4 — Netflix original legal drama, "
            "10 episodes released all-at-once on Thursday February 5, "
            "2026. Adapts Michael Connelly's The Law of Innocence, the "
            "series' highest-stakes arc: Mickey Haller is charged with "
            "the murder of former client Sam Scales (resolving the S3 "
            "finale cliffhanger) and must defend himself from inside the "
            "system, going head-to-head with the DA's office and the "
            "FBI. Manuel Garcia-Rulfo, Becki Newton, Jazz Raycole, Angus "
            "Sampson return; Neve Campbell returns for all episodes; "
            "Cobie Smulders, Sasha Alexander, and Constance Zimmer join "
            "the cast. Netflix What We Watched H1 2026 actuals: 295.5M "
            "global hours / 35.0M Views — the #11 show on Netflix for "
            "the half, one slot behind The Night Agent S3 (36.1M Views). "
            "Weekly Top "
            "10: 9.6M Views the week of February 9 — #1 English TV and a "
            "stronger week-2 than Season 3 managed (8.5M). First-30-day "
            "global Views about 27.5M. Franchise stage: YEAR-4 event on "
            "the deepest saturation base of the run — Netflix US paid "
            "subs ~90M at launch (Antenna Q1'26). Season 5 (adapting "
            "Resurrection Walk) was already ordered as the final season, "
            "adding anticipation. February slot is a post-holiday "
            "re-subscription month: acquisition skews heavily to lapsed "
            "subscribers churning back for the trial-of-Mickey arc, with "
            "concurrent S3 catch-up (S3 pulled 8.5M Views in H1 2026 "
            "during S4's run). Completion-rate expectation: low-to-mid "
            "70s — the most loyalist-pure audience of the franchise "
            "binging a single serialized trial arc; highest completion "
            "of the run. Second-screen expectation: around 30 — verdict "
            "speculation and final-season casting chatter lift social "
            "activity slightly above S3's lean-back baseline, still "
            "below younger-skewing binge hits."
        ),
    },
]


def build_config(spec: dict) -> dict:
    start = datetime.strptime(spec["start"], "%Y-%m-%d")
    last_ep = _last_episode_date(spec["episode_dates"])
    cfg = {
        "project_name":        spec["project_name"],
        "show_search_terms":   [spec["title"]],
        "platform_name":       spec["platform"],
        "campaign_start":      start,
        "campaign_end":        last_ep,
        "exclusion_days":      180,
        "attribution_window":  30,
        "genre":               spec["genre"],
        "content_cadence":     spec["cadence"],
        "is_new_show":         spec["is_new"],
        "episode_dates":       spec["episode_dates"],
        "upload_to_s3":        True,
        "s3_bucket":           "svod-acquisition",
        "dashboard_category":  DASHBOARD_CAT,
        "output_dir":          "/tmp/svod_synthetic_runs",
        "context_note":        spec["context_note"],
    }
    if "reach_us" in spec:
        cfg["reach_us_override"] = spec["reach_us"]
    if "conv_pct" in spec:
        cfg["conversion_pct"] = float(spec["conv_pct"])
    if "new_share" in spec:
        cfg["reactivation_pct_override"] = max(0.0, min(1.0, 1.0 - float(spec["new_share"])))
    if "pre_existing_pct" in spec:
        # Pipeline reads config['pre_existing_pct'] directly; config
        # overrides are not clamped (only research-derived values cap
        # at 0.65).
        cfg["pre_existing_pct"] = float(spec["pre_existing_pct"])
    return cfg


def main() -> None:
    print(f"⚖️  The Lincoln Lawyer — pulling all {len(CONFIGS)} seasons independently")
    print()
    results: list[tuple[str, str, str]] = []
    for idx, spec in enumerate(CONFIGS, 1):
        print(f"\n{'=' * 70}\n  [{idx}/{len(CONFIGS)}] {spec['title']}\n{'=' * 70}")
        try:
            cfg = build_config(spec)
            r = run_synthetic_attribution(cfg)
            key = r.get("s3_key") if isinstance(r, dict) else None
            reach = r.get("reach_us") if isinstance(r, dict) else None
            sign = r.get("new_signups_us") if isinstance(r, dict) else None
            if key and reach is not None and sign is not None:
                print(f"  ✅ uploaded {key}  reach={reach:,} signups={sign:,}")
            else:
                print(f"  ⚠️ unexpected result: {r}")
            results.append(("ok", spec["title"], str(key)))
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  ❌ {spec['title']}: {e}")
            results.append(("fail", spec["title"], str(e)))

    print("\n" + "=" * 70)
    print(f"Done. {sum(1 for s, _, _ in results if s == 'ok')}/{len(results)} succeeded.")
    for s, t, msg in results:
        tag = "✅" if s == "ok" else "❌"
        print(f"  {tag} {t}: {msg}")


if __name__ == "__main__":
    main()
