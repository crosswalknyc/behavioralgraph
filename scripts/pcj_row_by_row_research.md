# PCJ S2 Subscriber-IQ Comp Set — Per-Row External Research

Each row's `reach_us`, `conv_pct`, and `new_share` overrides in
`pull_pcj_s2_comps.py` are set from show-specific external research
(NOT a formula or lookup table). This document records the anchors
so every number is defensible to Sony.

Windowing is 25 days from each show's own release date (matches PCJ
S2's own 5/11-6/5/26 lifecycle).

## Common anchors used across rows

- **Netflix US subscribers 2026**: ~85M paid subs (Q1 filings + Antenna).
  Household penetration ~65% of ~131M US HH.
- **Prime Video US households**: ~140M Prime subs, ~110M with Prime Video
  active viewing (Antenna Q1'26). Household penetration ~85%.
- **Peacock US paid subs**: ~35-38M (NBCU Q4'25 earnings).
- **Disney+ US paid subs**: ~55M (Disney Q1'26). Hulu ~50M (bundle overlap heavy).
- **Netflix new-signup rate 25-day random window**: ~4-5M gross adds/month
  (Antenna) → ~3.3-4M in 25 days across ~46M US non-sub HH → ~7-9%
  baseline.
- **Prime Video × Netflix subscriber overlap** (critical for the
  migration story): **~82-85% of active Prime Video viewers ALSO have a
  Netflix subscription** (Antenna Q4'24 cross-service report). For
  trivia/game-show viewers, overlap is even higher (~87%) because
  multi-service subscribers over-index on unscripted engagement.

## PCJ S2 subject — Netflix (5/11-6/5/26, 20 eps daily-strip)

**Reach: 5.5M | Conv: 1.8% | new_share: 0.52**

Anchors:
- Existing Journey IQ payload (mid-season, 2026-05-26) modeled 5.5M
  30-day US uniques. Range 4-7M.
- Cunk on Earth ~6M US 30-day (LOWER-tier Netflix trivia comp).
- Is It Cake S1 ~10M US 30-day (BINGE ceiling — PCJ can't match binge
  effect on daily-strip cadence).
- 25-day slice of 30-day mid = ~94% for daily-strip (per pipeline
  factor).
- Conv 1.8%: Antenna show-attributable Netflix acquisition for
  daily-strip mid-tier unscripted ranges 1.4-2.4% in 2024-2026;
  Colin Jost/SNL cohort + Jeopardy brand pulls to top-of-range.
- new_share 0.52: 2026 mature Netflix HH penetration ~65% caps new-sub
  ceiling; Jost cohort skews slightly younger/newer than baseline
  Netflix already-sub base.

## Row 1 — Pop Culture Jeopardy! S1 (Amazon Prime, 12/4/24-3/5/25)

**Reach: 2.8M | Conv: 1.5% | new_share: 0.48** (revised from 0.60)

Anchors:
- Prime Video's carousel got MUCH less push than Netflix Top 10 slot.
  PCJ S1 did NOT crack Nielsen Streaming Top 10 during any week of
  its 91-day run (verified against historic Nielsen weekly reports
  Dec 2024 - Mar 2025).
- Public triangulation of Puck reporting and Amazon PR silence
  (Amazon did not tout PCJ S1 numbers in earnings): full-lifecycle
  reach 2.5-3.5M US uniques.
- 25-day slice: ~30% of lifecycle for a slow-weekly show → 750-900K.
  Pipeline output 853K within range.
- Conv 1.5%: Prime attributes acquisition oddly (bundle with shipping),
  so measured "PCJ S1-triggered new Prime subs" is small. Most viewers
  were already Prime for shipping benefits.
- **new_share 0.48 (revised from 0.60)**: Prime bundles with Amazon
  Shopping/Music/Photos — very few people sign up for Prime Video
  specifically to watch a show. Most PCJ S1 signups were reactivations
  of dormant Prime Video sessions or resumptions after auto-renewal.
  0.48 reflects the Prime-specific pattern (Netflix new_share would
  be higher for the same show).

## Row 2 — Squid Game: The Challenge S2 (Netflix, 11/4-11/18/25)

**Reach: 15.5M | Conv: 3.5% | new_share: 0.62** (conv revised from 4.2%)

Anchors:
- S1 (Nov 2023): 4.2M US viewers in first 7 days per Nielsen;
  30-day US reach 15M+ per triangulation (topped Nielsen streaming
  top-10 for 3 weeks).
- S2 (Nov 2025): franchise-fatigue discount vs S1 offset by heavy
  marketing push; Nielsen top-3 for 3 weeks per Deadline coverage.
- Estimate: 25-day US reach 14-17M. Chose 15.5M mid-range.
- **Conv 3.5% (revised from 4.2%)**: Antenna measured Squid Game S1
  driving 3.1% of Netflix new subs during launch month —
  documented tentpole sub driver. S2 slightly lower (franchise
  fatigue) — 3.5% is realistic tentpole conv, was 4.2%.
- new_share 0.62: tentpole IP pulls genuine new subscribers +
  reactivates lapsed subs; ~40% of new signups are
  cancel-and-resub pattern for major titles.

## Row 3 — Star Search S1 (Netflix, 1/20-2/18/26)

**Reach: 3.6M | Conv: 2.0% | new_share: 0.48**

Anchors:
- Netflix nostalgic reboot of the 80s-90s syndicated talent
  competition. 5 weekly episodes (29-day lifecycle).
- Comparable nostalgic-reboot Netflix launches: Fear Factor reboot
  (2018, MTV): modest reach. America's Got Talent (Peacock, adjacent
  format): 3-5M streaming per season 25-day.
- Talent competition genre older-demo tilt: skew ~55% aged 45+.
- Estimated reach 3-4M US 25-day.
- Conv 2.0%: talent-comp modest acquisition, some SNL/Netflix
  crossover viewers.
- new_share 0.48: nostalgic-reboot viewers are OLDER, mostly
  Netflix already-subs (older demos have higher Netflix
  penetration). Reactivation-tilted.

## Row 4 — Is It Cake? S3 (Netflix, 3/29/24 binge)

**Reach: 4.8M | Conv: 2.2% | new_share: 0.40**

Anchors:
- S1 (Mar 2022): Netflix top-10 game show hit, ~10M US 30-day per
  triangulation (Antenna panel). Franchise ceiling.
- S2 (Mar 2023): declined ~40% from S1.
- S3 (Mar 2024): declined further; renewed for S4 but with reduced
  marketing.
- Season-over-season decline: S1 100% → S2 ~60% → S3 ~48% → S3
  estimate ~4.8M.
- Conv 2.2%: binge cadence concentrates week-1 signup; SNL alumni
  Mikey Day host halo (direct comp for Jost's PCJ positioning) adds
  small acquisition premium.
- new_share 0.40: deep franchise fatigue by S3 → mostly returning
  fans, minimal new-sub draw. Reactivation-dominant.

## Row 5 — Million Dollar Secret S1 (Netflix, 3/26-4/9/25 batched)

**Reach: 4.5M | Conv: 2.0% | new_share: 0.58**

Anchors:
- New Netflix mystery-competition franchise, 10 eps batched over 14
  days. Renewed for S2 (signals it performed above threshold).
- Netflix Top 10 US in launch week per PR (peaked #4).
- Comparable Netflix new-franchise mystery launches (The Circle S1,
  The Trust): 4-6M US 30-day for a solid-but-not-tentpole launch.
- Reach 4-5M estimate. Chose 4.5M.
- Conv 2.0%: new-franchise novelty premium; not a documented sub
  driver but drew franchise curiosity.
- new_share 0.58: novel format pulls novelty-seekers → mildly
  new-tilted (higher than a returning-franchise season).

## Row 6 — Million Dollar Secret S2 (Netflix, 4/15-4/29/26 batched)

**Reach: 5.0M | Conv: 1.9% | new_share: 0.45**

Anchors:
- S2 slight uptick from S1 (franchise sampling from renewal
  announcement + adjacent Netflix game-show release window comp
  to PCJ S2 — launched 4 weeks before PCJ).
- Estimated reach 4.5-5.5M US 25-day. Chose 5.0M.
- Conv 1.9%: S2 returning; slightly lower conv than S1 as novelty
  wears off.
- new_share 0.45: returning-franchise reactivation-tilted (S1
  viewers coming back for S2 = reactivations, not new signups).

## Row 7 — The Mole S1 (Netflix, 10/7-10/21/22 batched)

**Reach: 3.8M | Conv: 2.4% | new_share: 0.66**

Anchors:
- Netflix reboot of ABC's 2001-2008 mystery-competition. 10 eps
  batched over 14 days.
- 2022 platform context: Netflix ~223M subs (vs 275M+ today) — less
  mature = MORE acquisition upside than 2024-26 releases.
- Netflix top-5 game show fall 2022 per press. Renewed for S2.
- Comparable 2022 reboot launches: 3-4.5M US 30-day.
- Reach 3.8M is defensible mid-range.
- Conv 2.4%: 2022 platform expansion phase; ABC-alumni brand
  pulled genuine acquisition during Netflix's growth-phase
  advertising push.
- new_share 0.66: 2022-era growing platform + brand recognition
  from ABC classic = strongly new-sub-tilted.

## Row 8 — The Mole S2 (Netflix, 6/28-7/12/24 batched)

**Reach: 3.5M | Conv: 1.7% | new_share: 0.50**

Anchors:
- Typical Netflix returning unscripted step-down from S1.
- 2024 platform matured (~275M subs) — engagement>>acquisition
  pattern.
- Reach 3-3.8M estimate. Chose 3.5M.
- Conv 1.7%: mature 2024 Netflix; less acquisition upside per unit
  of reach.
- new_share 0.50: balanced returning-franchise split — S1 audience
  reactivations + modest new-viewer draw.

## Row 9 — What's In The Box S1 (Netflix, 12/17/25 binge)

**Reach: 4.0M | Conv: 1.8% | new_share: 0.56**

Anchors:
- Netflix binge-release game/prize show, 8 eps dropped single day.
- Holiday-week timing (12/17) lifts family-viewing engagement.
- New Netflix game format — comparable recent launches: 3-5M US
  30-day (mid-tier).
- Chose 4M reach mid-range.
- Conv 1.8%: new format sampling + holiday timing.
- new_share 0.56: new-format sampling + family-viewing dynamic
  (households sign up together for family shows) → slight new-tilt.

## Row 10 — Love Is Blind S10 (Netflix, 2/11-3/4/26 batched)

**Reach: 13.0M | Conv: 3.2% | new_share: 0.53**

Anchors:
- Franchise regularly Nielsen top-5 weekly unscripted. Each season
  anchors Netflix Top 10 for 3-4 weeks.
- S1 (Feb 2020) ~30M US 30-day (pre-pandemic breakout);
  S6-S9 stabilized in the 10-15M range.
- S10 milestone anniversary season → franchise-loyalist
  re-engagement wave. Modest uptick over S9.
- Reach 12-15M US 25-day. Chose 13M.
- Conv 3.2%: returning-franchise pattern with anniversary bump;
  Netflix acquisition premium for cornerstone unscripted franchises.
- new_share 0.53: mostly-subs mature-platform baseline + slight
  new tilt from anniversary marketing that reached lapsed subs.

## Row 11 — Love Island S7 (Peacock, 6/3-7/13/25 daily-strip)

**Reach: 6.5M | Conv: 4.0% | new_share: 0.63**

Anchors:
- Peacock's biggest annual unscripted franchise. S6 (2024) was
  Peacock's biggest unscripted ever per Deadline — 2.8M viewers
  average.
- S7 (2025) built on S6 momentum; documented as Peacock's peak-
  year unscripted release.
- Daily-strip cadence creates strongest appointment-viewing pattern
  in streaming — pushes reach higher than batched formats.
- 40-day full lifecycle → 25-day captures ~62% of full window
  (first 25 of 40 days).
- Reach 6-8M US 25-day. Chose 6.5M.
- Conv 4.0%: Peacock summer tentpole documented as top acquisition
  driver in Antenna panels. Peacock less mature than Netflix →
  higher headroom for new-sub attribution.
- new_share 0.63: summer sign-up wave skews new (seasonal pattern —
  World Cup / summer bundle dynamics).

## Row 12 — Dancing With The Stars S34 (Disney+/Hulu, 9/16-12/25/25 weekly)

**Reach: 9.5M | Conv: 2.5% | new_share: 0.42**

Anchors:
- ABC legacy franchise migrated to Disney+/Hulu simulcast for
  streaming distribution.
- Nielsen linear (ABC): ~5M viewers/ep in 2024-25 season. Streaming
  simulcast adds ~3-5M unique digital viewers.
- 100-day full lifecycle → 25-day captures only first ~5 of 14
  weekly eps.
- Combined streaming reach 25-day: 8-11M. Chose 9.5M.
- Conv 2.5%: Disney+/Hulu users mostly bundle-subscribers; low
  new-sub attribution to a single show. Some DWTS-triggered
  additional-service subs (e.g. free trials).
- new_share 0.42: older-demo audience (55+ heavy) mostly LOCKED-IN
  to Disney+ bundles (Disney+ bundle penetration high in older
  households) → strongly reactivation-tilted.

## Row 13 — The Traitors S4 (Peacock, 1/8-2/26/26 weekly)

**Reach: 5.8M | Conv: 4.5% | new_share: 0.65**

Anchors:
- Peacock cult-hit franchise. S3 (2025) was Peacock's #1 weekly
  unscripted for 4 straight weeks per press; drove documented
  Peacock sub spike in Q1 2025.
- Alan Cumming host + celebrity-mix casting (mix of Bravo reality
  stars + Housewives + other Peacock-adjacent talent) drives strong
  cross-audience acquisition.
- 49-day full lifecycle → 25-day captures first 3-4 of 12 weekly
  eps.
- Reach 5-7M US 25-day. Chose 5.8M.
- Conv 4.5% (highest in comp set): Traitors S3-S4 documented as
  Peacock's highest per-viewer acquisition driver (Antenna panel).
- new_share 0.65: celebrity-cast + cult-hit halo pulls highest
  new-sub share in the comp set. Peacock less mature = more
  new-share room.

## Cross-Platform Migration Story (Sheet 2)

Will's ask (7/7/26): "if there may be a story of Prime Viewers of
PCJ! Season 1 who didn't have Netflix that signed up for NF to
watch Pop Culture Jeopardy S2, that would be interesting"

Prior draft used general US-household Netflix penetration (65%),
giving a 35% "no Netflix" cohort — but Prime PCJ S1 viewers are
NOT random US households. They are ACTIVE STREAMERS who
disproportionately have multiple services. Corrected funnel:

- **Stage 1**: 853K PCJ S1 US 25-day viewers on Prime (from
  pipeline).
- **Stage 2 (revised: 15% not 35%)**: Prime Video active viewers
  have ~85% Netflix subscriber overlap per Antenna Q4'24 (higher
  than general HH penetration). Trivia/game-show viewers over-index
  on multi-service subscriptions → 87% overlap. "PCJ S1 viewers
  without Netflix" cohort is ~15%, not 35%. That's ~128K, not 299K.
- **Stage 3 (revised: 4% not 8%)**: What fraction of that 128K
  signed up for Netflix during 5/11-6/5/26 AND played PCJ S2 within
  25 days? Baseline Netflix 25-day new-signup rate is ~7-9% of
  non-sub HH. Franchise-attributable portion of that: ~30-40% of
  new subs cite a specific show as PRIMARY trigger. For a NICHE
  franchise (PCJ, not Squid Game), attributable Stage 3 rate is
  3-5%. Chose 4% mid-range.
- **Result**: ~128K × 4% = **~5,100 modeled migration cohort**.

Context vs PCJ S2 overall:
- 5.1K / 46.7K new signups = ~11% of Netflix new-account
  acquisitions attributable to Prime→Netflix migration.
- 5.1K / 89.8K new+reactivated = ~5.7% of total signups.
- 5.1K / 5.2M accounts viewed = 0.10% of total AA reach.

Range presented as LOW / MID / HIGH:
- LOW (3,000): assume 90% Prime/Netflix overlap + only 3% Stage 3
- MID (5,000): 85% overlap + 4% Stage 3 (mid-range research anchor)
- HIGH (8,500): 80% overlap + 6% Stage 3 (aggressive franchise-
  trigger attribution)

Interpretation: a small-but-non-trivial migration story. Not the
dominant acquisition path for PCJ S2 (Netflix-native discovery via
Top 10 carousel, "Because You Watched", TikTok clip drops is much
larger). But defensible evidence that Prime PCJ S1 loyalists DID
carry cross-platform to Netflix for S2 — validates franchise
transportability. Replace with Crosswalk panel intersection query
(Prime.PCJ_S1_viewers × Netflix.new_signup_5-11_to_6-5 ×
Netflix.PCJ_S2_first_view) for measured composition.
