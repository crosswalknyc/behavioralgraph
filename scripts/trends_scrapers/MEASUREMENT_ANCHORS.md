# Measurement anchors for the daily streaming rankers

Jenna, 2026-10-26: *"should we use this research and findings to help with
our daily rankers? put all this to good use and do this kind of deep dive
on the other platforms so that we can project the best possible number
every day"* and *"remember in rankers we do daily views so duplication in
views can happen"*.

This note records what the Prime Video deep dive taught, what the audit of
the live rankers found, and the plan to make every platform's daily number
sit on published measurement. Code: `measurement_anchors.py` (read-only
audit + dry-run relevel, not yet wired) and `test_measurement_anchors.py`.

## The unit, stated once

A ranker reading is **daily account-views**: one account that played the
title on that calendar day counts once; the same account counts again on
every later day it returns (that is the duplication); three episodes in
one sitting are still one account-day; nobody on the couch is multiplied
in. It is deliberately not a unique-viewer count over a window and not a
play count. Everything below converts into this unit before it touches a
ranker.

## What the audit found (2026-10-26)

Rankers vs the level Nielsen's weekly minutes imply, same title, same
platform, a day inside the same week (Sept 17, Sept 24, Oct 1, Oct 8):

| platform | anchored titles | median ranker / implied | range |
|---|---|---|---|
| Netflix | 27 | 0.38 | 0.09 to 3.37 |
| HBO Max | 8 | 0.16 | 0.01 to 3.02 |
| Hulu | 10 | 0.54 | 0.01 to 1.93 |
| Paramount+ | 4 | 0.93 | 0.50 to 1.76 |
| Prime Video | 10 | 1.13 | 0.14 to 4.59 |
| Disney+ | 6 | 1.35 | 0.14 to 4.38 |

Three failure shapes, all fixable with anchors:

1. **Library series read far too low.** The Big Bang Theory on HBO Max at
   8K to 73K a day against ~1.5M implied by 950M weekly minutes; Bluey,
   Ms. Rachel, Young Sheldon, The Great British Baking Show, Murder in a
   Small Town all between 0.01x and 0.17x. The research step reasons a
   catalog title as a catalog title; Nielsen says these are the most
   watched things on their services.
2. **Films hold their launch level for weeks.** Toy Story 5 4.4x in its
   third Disney+ week, The Love Hypothesis 4.6x in its second, The
   Widower 3.4x, Backrooms 3.0x. Nothing in the pipeline tracks the
   published week-over-week decay.
3. **The catalog tail is too fat.** On Oct 22 the sum of readings was
   over the Gauge-implied daily budget on Paramount+ (3.5x), AMC+
   (4.8x), ESPN+ (14.7x), HBO Max (1.8x), Disney+ (1.8x), Starz (1.7x),
   Peacock (1.6x), Prime Video (1.5x); Netflix sat at 0.7x with its hits
   under-read. Hundreds of catalog titles at 15K to 40K a day each is
   where the budget goes. This is the September Prime Video defect
   (Legally Blonde at 4 to 6M a day) in milder form, and a platform
   budget check catches it mechanically.

## The three anchors

**1. Title anchors (weekly).** Nielsen's Streaming Top 10 (overall,
original, acquired, movies; US; TV sets; persons 2+; published Thursdays
for the week ending about 18 days earlier) and Luminate's weekly hours.
Conversion:

    daily account-views = weekly minutes x 1.12 (phones, tablets, PCs)
                          / persons-per-stream (co-viewing, by genre)
                          / minutes-per-account-day (by format)
                          / 7

| genre | persons per stream | minutes per account-day |
|---|---|---|
| binge drama (full-season drop) | 1.35 | 105 |
| weekly drama | 1.35 | 65 |
| procedural / library drama | 1.30 to 1.35 | 85 to 95 |
| sitcom | 1.30 | 70 |
| kids | 1.60 | 55 |
| reality | 1.20 | 80 |
| film | 1.25 | 100 |
| kids film | 1.60 | 95 |
| live sports | 1.75 | 160 |
| news | 1.20 | 45 |

A charting title holds its anchored level for the days of that week,
shaped by day of week (weekend +12%), and decays into the following week
by the published change when the next chart lands. The two-week lag is
acceptable: the level a title holds in week N is the best predictor of
week N+2 after the published decay, and far better than reasoning from a
cold start.

**2. Platform budgets (monthly).** Nielsen Gauge share of TV x total US TV
time (~100B persons-minutes a day; Nielsen's 16.7T streaming minutes in
2025 at a 44 to 47% share, cross-checked four ways) converted the same way
gives the most account-views a platform can hold in a day across all
titles. The sum of a platform's readings never exceeds it. The unanchored
tail is scaled down by one salted factor to fit under 90% of the budget,
in rank order; anchored titles are never scaled and nothing is ever
scaled up by a platform factor.

Budgets at the August 2026 shares: Netflix 71.0M, Prime Video 38.7M,
Disney+ 21.2M, Hulu 21.2M, Peacock 14.7M, HBO Max 12.0M, Paramount+
10.1M, Starz 1.8M, AMC+ / BritBox / MGM+ 0.9M, the two partner-calibrated
channels from their own first-party feed.

**3. Carriage shares** for the "X on Amazon" rails (`carriage_mix.py`).
From the 2026-10-26 research: Paramount+ about a fifth of its US base
through Channels (Antenna ~10M of ~48M), FOX One a fifth to a third,
Apple TV about a quarter of sign-ups, Starz near 44% (29% of Starz revenue
is Amazon per its 10-K), Peacock materially above the 2.4% band (about
1M through Channels in its first three months).

## What the Prime Video deep dive also settled

- Amazon's published viewer figures (Neagley 40M in 13 days, Reacher S4
  66M in 28 days, 130M+ US monthly) are household-credited persons, the
  same class of metric Netflix now defines explicitly (monthly active
  viewers = accounts x people per household). Divide by 1.7 to 1.8 to
  reach account level before using them as a ranker anchor.
- Amazon's weekly worldwide Top 10 is ranked by accounts that watched
  any season; it is an order anchor only.
- The digital transaction charts (GfK/DEG, Apple, Comscore VOD,
  Fandango) are weekly and public; they are the anchor for any
  purchase or rental rail.

## Wiring plan (not done; the tree is mid-cherry-pick tonight)

1. `nielsen_top10.py` scraper: nielsen.com/data-center/top-ten/ every
   Thursday into `ANCHOR_WEEKS` (S3 doc under
   `trends_iq_snapshots/system/measurement_anchors/weeks.json`), with the
   genre assigned by the title's existing category and a platform share
   for multi-platform rows from the carriage model. Luminate's weekly
   hours as a second feed where published.
2. Monthly: add the Gauge row when Nielsen publishes (Sept and Oct 2026
   arrive together after the methodology change).
3. `run_all`: after `first_party_calibration.apply_to_store`, call
   `measurement_anchors.relevel_items` behind `TRENDS_MEASUREMENT_ANCHORS=1`
   (engine host env only, never a request field), then the existing
   distinctness and terminal passes get the last word.
4. Dashboard chips keep the unit label "daily US account-views".
5. Regression: `test_measurement_anchors.py` plus a board-level test that
   no platform exceeds its budget on a synthetic day.

## Per-platform deep-dive backlog (the Prime Video method, in order)

| platform | the anchors to pull | notes |
|---|---|---|
| Netflix | its own weekly Top 10 views (views = hours / runtime; already in `chart_set_reasoning`), semiannual engagement report, Gauge 7.7% | convert views to account-days with the format table; the biggest fix is library series |
| Disney+ / Hulu | Gauge Disney 4.8% split, Nielsen weekly (Bluey, Toy Story 5, AHS, Grey's, Bob's Burgers), Disney 10-K subscriber counts | kids co-viewing 1.6 matters most here |
| HBO Max | Gauge WBD 1.4%, Nielsen weekly (Big Bang, Young Sheldon, Lanterns), WBD 10-Q subscribers | the 0.16x library under-read is the worst on the board |
| Paramount+ | Gauge Paramount 2.3% less Pluto, Nielsen weekly (SpongeBob, Lioness, Mayor of Kingstown), Paramount 10-K 78.9M global | tail is 3.5x over budget today |
| Peacock | Gauge 1.6%, Nielsen weekly (Love Island, Super Mario Galaxy Movie, Disclosure Day), Comcast 10-K ~41M | NFL Sundays spike the budget 25% |
| Apple TV | no Gauge line (~0.6%), Nielsen weekly (Ted Lasso, Silo), Luminate | rankers are missing Apple's charting titles entirely |
| Starz / MGM+ / AMC+ / BritBox | Starz 10-K 17.6M US subs, AMC 10-K 10.4M streaming subs, BritBox ~4M; Antenna carriage | small budgets; the Amazon rails derive from them |
| ESPN+ | Gauge Disney sliver; Nielsen sports events | 14.7x over budget today, the single largest overrun |
| FAST (Pluto, Tubi, Roku, Prime live) | Gauge shares (Tubi 1.9%, Roku 2.9%, Pluto ~1.2%), Amagi / Wurl genre shares, lineup scrapes | channel rails are schedule-ranked today, not viewing-ranked |

Never queries clickstream (`trends-rankers-never-clickstream.mdc`).
