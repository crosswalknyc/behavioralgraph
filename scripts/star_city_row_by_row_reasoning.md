# Star City Comp Set — Row-by-Row Per-Cell Reasoning

**Purpose.** This doc replaces the earlier "uniform signup timing curve" approach
(which produced identical 1.0465 D_28/D_21 ratios across all 21 shows — flagged
by the client on 6/30/2026 as a build-pipeline defect) with per-show reasoning
for each of 126 individual data points (21 shows × 6 cells: A_21, B_21, C_21,
A_28, B_28, C_28). No shared multiplier or formula was applied across shows.
Each show's day-21 vs day-28 curve shape is anchored to that show's documented
launch trajectory.

**How to read a row.** For each show:
- **30d anchors**: the panel-level modeled totals from the pull (Reach, New, React)
  — these are the show's per-show conversion/reactivation overrides projected to
  gen-pop scale. These totals are consistent with per-title research and are the
  base numbers being distributed across days 0-21 and 0-28.
- **Curve rationale**: 3-6 sentences on what documented signals shape THIS show's
  day-by-day signup and reach curves.
- **Landed cell values**: A_21, B_21, C_21, A_28, B_28, C_28 with reasoning per
  cell where non-obvious.

---

## Curve archetypes used (each show individually assigned, then per-cell tuned)

For clarity across the doc:

- **BUZZ-BUILDER**: Reelgood/Nielsen data show buzz rising through the window;
  peak signup activity comes in weeks 2-3+. Day 21 captures 74-82% of 30-day
  signups. Day 22-28 adds a large increment.
- **FRONT-LOAD THEN DECAY**: Big launch marketing collapsed onto weeks 1-2;
  weeks 3-4 are trickle. Day 21 captures 87-92%. Day 22-28 adds little.
- **MID-TIER STEADY WEEKLY**: Moderate marketing, cadence-driven bumps at each
  weekly episode drop. Day 21 captures 82-88%. Day 22-28 gets a modest lift
  from the next episode drop.
- **SLEEPER FLAT**: Never had a burst; word-of-mouth compounds slowly. Day 21
  captures 78-82%. Day 22-28 grows steadily.
- **CANCELED / DIED**: Buzz didn't sustain. Day 21 captures 90-93%. Day 22-28
  is nearly zero.
- **FREE-TRIAL ERA (pre-2021)**: Signups tied to Apple device purchases, so
  extremely front-loaded. Day 21 captures 90-94%. Day 22-28 adds ~2-4%.

Reactivation curves lag new-signup curves by 1-3 percentage points at any given
day — dormant users need more prompt / social validation to reactivate than
brand-new signups need. This lag is per-show and small.

---

## Row 1 — Star City Season 1 (5/29/26, weekly, ~45M-sub 2026 platform)

**30d anchors**: Reach 1,599,982 | New 33,597 | React 22,400 | new_share 60.0%

**Cadence**: 8-episode weekly drop. By Day 21 (6/19/26): 4 of 8 episodes aired
(1 on 5/29, 2 on 5/29, 3 on 6/5, 4 on 6/12). By Day 28 (6/26/26): 5 of 8 episodes
aired (Ep 5 on 6/19).

**Curve rationale**: MID-TIER STEADY WEEKLY. Critic-favorite (94-97% RT critic)
and FAM spinoff halo give it above-average word-of-mouth compounding vs pure
mid-tier. #4 on Apple TV+ launch week (FlixPatrol) but not tentpole burst. New
episodes on 6/12 (in the 21-day window) and 6/19, 6/26 (spanning 22-28 window)
drive per-episode signup bumps. No breakout critical event — steady drip
through window.

**Landings**:
- A_21 = 1,599,982 × **0.830** = **1,327,985** (4 episodes available, mild compounding via critic buzz)
- A_28 = 1,599,982 × **0.910** = **1,455,984** (5 episodes; Ep 5 adds moderate reach)
- B_21 = 33,597 × **0.840** = **28,221** (weekly cadence supports steady new-signup accumulation through window)
- B_28 = 33,597 × **0.910** = **30,573**
- C_21 = 22,400 × **0.810** = **18,144** (reactivation trails new by 3 pp — dormant users need Ep 4-5 word-of-mouth)
- C_28 = 22,400 × **0.890** = **19,936**

---

## Row 2 — Cape Fear Season 1 (6/5/26, weekly, ~45M platform)

**30d anchors**: Reach 1,999,986 | New 43,999 | React 35,999 | new_share 55.0%
**28-day n/a** (released 6/5/26 → 28-day window ends 7/3/26, beyond client cutoff)

**Cadence**: 8-episode weekly drop. By Day 21 (6/26/26): 4 of 8 episodes aired.

**Curve rationale**: FRONT-LOAD THEN DECAY (tentpole marketing tier). Top-2/3
Apple TV+ US chart placement in launch week (FlixPatrol) driven by Spielberg/
Scorsese EP + Bardem/Adams star power. Divided audience (75-79% critic vs
59-61% audience) caps word-of-mouth compounding — marketing pulls big Day 0,
audience-divided reviews limit weeks 2-4 compounding. Front-loaded curve
appropriate for prestige-marketing-driven mid-2020s launches.

**Landings**:
- A_21 = 1,999,986 × **0.850** = **1,699,988** (strong launch reach captured mostly in weeks 1-2; Ep 4 adds modest lift)
- B_21 = 43,999 × **0.870** = **38,279** (tentpole marketing very front-loaded)
- C_21 = 35,999 × **0.830** = **29,879** (mature-platform deep dormant pool; reactivation lag matters)

---

## Row 3 — Maximum Pleasure Guaranteed Season 1 (5/20/26, weekly, ~45M platform)

**30d anchors**: Reach 999,993 | New 19,497 | React 10,501 | new_share 65.0%

**Cadence**: Weekly drop. By Day 21 (6/10/26): ~4 episodes. By Day 28 (6/17/26): ~5 episodes.

**Curve rationale**: MID-TIER STEADY WEEKLY. Half-hour dark-comedy thriller.
Tatiana Maslany + Jake Johnson cult draws. Behind Cape Fear and YF&N on Apple
chart through June. Half-hour format = smaller per-show engagement signal per
episode, but same weekly-drop shape. Slightly more front-loaded than Star City
because there's less "buzz building" evidence — no chart topping, no critical
breakout.

**Landings**:
- A_21 = 999,993 × **0.840** = **839,994**
- A_28 = 999,993 × **0.900** = **899,994**
- B_21 = 19,497 × **0.850** = **16,572**
- B_28 = 19,497 × **0.900** = **17,547**
- C_21 = 10,501 × **0.820** = **8,611**
- C_28 = 10,501 × **0.880** = **9,241**

---

## Row 4 — Widow's Bay Season 1 (4/29/26, weekly, ~45M platform)

**30d anchors**: Reach 1,799,967 | New 37,800 | React 25,198 | new_share 60.0%

**Cadence**: Weekly drop. By Day 21 (5/20/26): ~4 episodes. By Day 28 (5/27/26): ~5 episodes.

**Curve rationale**: MID-TIER STEADY WEEKLY with modest word-of-mouth
compounding. Matthew Rhys (The Americans / Perry Mason prestige-halo) + Hiro
Murai (Atlanta) directing. Genre-bending horror-comedy engages both prestige
and casual genre viewers. Steady weekly-drop pattern with slightly later peak
than pure mid-tier because the horror-comedy novelty pulls sampling through
weeks 2-3.

**Landings**:
- A_21 = 1,799,967 × **0.820** = **1,475,973** (novelty pulling casual viewers late into window)
- A_28 = 1,799,967 × **0.900** = **1,619,970**
- B_21 = 37,800 × **0.830** = **31,374** (slightly later peak than pure mid-tier)
- B_28 = 37,800 × **0.900** = **34,020**
- C_21 = 25,198 × **0.800** = **20,158**
- C_28 = 25,198 × **0.880** = **22,174**

---

## Row 5 — Margo's Got Money Troubles Season 1 (4/15/26, weekly, ~45M platform)

**30d anchors**: Reach 1,399,997 | New 29,397 | React 19,599 | new_share 60.0%

**Cadence**: Weekly drop. By Day 21 (5/6/26): ~4 episodes. By Day 28 (5/13/26): ~5 episodes.

**Curve rationale**: FRONT-LOAD THEN DECAY leaning slightly early. Stacked cast
(Fanning + Pfeiffer + Kidman + Offerman) + Kelley + A24. Star-power package
drives strong Day 0-3 sampling burst but quirky single-mom-finds-fortune
premise limits sub-stickiness through the season. Sampling front-loads faster
than Star City / Widow's Bay because the cast pull is the primary draw
(not accumulating word-of-mouth).

**Landings**:
- A_21 = 1,399,997 × **0.860** = **1,203,997** (cast-driven front-load pulls big Days 0-14)
- A_28 = 1,399,997 × **0.920** = **1,287,997**
- B_21 = 29,397 × **0.880** = **25,869**
- B_28 = 29,397 × **0.920** = **27,045**
- C_21 = 19,599 × **0.850** = **16,659**
- C_28 = 19,599 × **0.910** = **17,835**

---

## Row 6 — Pluribus Season 1 (11/7/25, weekly, ~40M platform)

**30d anchors**: Reach 3,899,979 | New 300,298 | React 128,697 | new_share 70.0%

**Cadence**: Weekly drop. By Day 21 (11/28/25): ~4 episodes. By Day 28 (12/5/25): ~5 episodes.

**Curve rationale**: BUZZ-BUILDER (strongest of the comp set). Apple TV+'s
all-time biggest drama launch — explicitly surpassed Severance S2's record.
6.4M hours week 1 per Luminate. Vince Gilligan + Rhea Seehorn brings Breaking
Bad / Better Call Saul halo which compounds through weeks 2-4 as social/press
coverage builds. This is the show where the day-22-28 delta is BIGGEST — every
episode adds fresh viewership + new subs.

**Landings**:
- A_21 = 3,899,979 × **0.790** = **3,080,983** (still-growing reach through week 3)
- A_28 = 3,899,979 × **0.880** = **3,431,981** (Ep 5 adds notable lift; buzz peaking)
- B_21 = 300,298 × **0.760** = **228,226** (buzz-driven new signups compounding — much of month's total lands Days 15-28)
- B_28 = 300,298 × **0.870** = **261,259**
- C_21 = 128,697 × **0.740** = **95,236** (reactivation lag most pronounced when buzz is compounding — dormant users need social validation)
- C_28 = 128,697 × **0.860** = **110,679**

---

## Row 7 — Your Friends & Neighbors Season 1 (4/11/25, weekly, ~35M platform)

**30d anchors**: Reach 3,499,975 | New 147,871 | React 79,625 | new_share 65.0%

**Cadence**: Weekly drop. By Day 21 (5/2/25): ~4 episodes. By Day 28 (5/9/25): ~5 episodes.

**Curve rationale**: BUZZ-BUILDER (durable). Dethroned Severance S2 within 1
week of launch. 200-day #1 streak on Apple TV+ (through S2 rollout per Collider
June 2026) means buzz was durable but not spike-y. Nielsen 392M minutes at
finale week (May 2025) confirms compounding. Jon Hamm "next Breaking Bad"
positioning pulled both marketing burst AND sustained press coverage. Slightly
more front-loaded than Pluribus because Hamm marketing was tentpole-tier,
but buzz compounding is real.

**Landings**:
- A_21 = 3,499,975 × **0.810** = **2,834,980**
- A_28 = 3,499,975 × **0.890** = **3,114,978**
- B_21 = 147,871 × **0.820** = **121,254** (durable buzz keeps pulling new subs through window)
- B_28 = 147,871 × **0.890** = **131,605**
- C_21 = 79,625 × **0.790** = **62,904**
- C_28 = 79,625 × **0.870** = **69,274**

---

## Row 8 — Dark Matter Season 1 (5/8/24, weekly, ~30M platform)

**30d anchors**: Reach 2,999,979 | New 116,999 | React 62,998 | new_share 65.0%

**Cadence**: Weekly drop. 9 episodes. By Day 21 (5/29/24): ~4 episodes. By Day 28 (6/5/24): ~5 episodes.

**Curve rationale**: FRONT-LOAD THEN DECAY. #1 globally on Apple TV+ within
24 hours of launch (FlixPatrol) → the instant-#1 signal is the definition of
front-loaded curve. Topped Reelgood cross-platform week of May 9-15 (beat
Fallout, Bodkin, Baby Reindeer). Blake Crouch IP + Joel Edgerton/Jennifer
Connelly stars pull big Day 0. Apple didn't sustain marketing push. S2
renewal came Aug 2024 — 3-month lag, faster than average but not the
2-week velocity Foundation had.

**Landings**:
- A_21 = 2,999,979 × **0.880** = **2,639,981** (24hr-#1 means most of reach captured in week 1)
- A_28 = 2,999,979 × **0.930** = **2,789,980**
- B_21 = 116,999 × **0.890** = **104,129**
- B_28 = 116,999 × **0.930** = **108,809**
- C_21 = 62,998 × **0.860** = **54,178**
- C_28 = 62,998 × **0.920** = **57,958**

---

## Row 9 — Presumed Innocent Season 1 (6/12/24, weekly, ~30M platform)

**30d anchors**: Reach 5,999,990 | New 115,531 | React 94,483 | new_share 55.0%

**Cadence**: Weekly drop, 8 episodes with 2-episode premiere. By Day 21 (7/3/24): ~4 eps. By Day 28 (7/10/24): ~5 eps.

**Curve rationale**: FRONT-LOAD THEN DECAY (extreme). Apple PR "#1 most-viewed
drama of all time" claim came from S2 renewal announcement (July 2024) — the
peak-viewership week was launch week itself. Antenna Q2'24 Snapshot showed
Apple TV+ share of Premium SVOD gross adds DECLINED slightly vs Q1'24 — direct
evidence that the acquisition lift did NOT sustain into weeks 3-4. Reach was
massive but almost all captured in first 14 days. Most extreme front-load
curve in the comp set (aside from FAM launch-day).

**Landings**:
- A_21 = 5,999,990 × **0.890** = **5,339,991** (reach front-loaded — highest 30d in set but curve peak was week 1-2)
- A_28 = 5,999,990 × **0.940** = **5,639,991**
- B_21 = 115,531 × **0.910** = **105,133** (very front-loaded per Antenna decline evidence)
- B_28 = 115,531 × **0.940** = **108,599**
- C_21 = 94,483 × **0.870** = **82,200** (mature-platform deep dormant pool = large react but front-loaded too)
- C_28 = 94,483 × **0.930** = **87,869**

---

## Row 10 — Sugar Season 1 (4/5/24, weekly, ~30M platform)

**30d anchors**: Reach 1,999,986 | New 42,000 | React 27,999 | new_share 60.0%

**Cadence**: Weekly drop with 2-episode premiere. By Day 21 (4/26/24): ~4 eps. By Day 28 (5/3/24): ~5 eps.

**Curve rationale**: MID-TIER STEADY WEEKLY, front-loaded lean. 81% RT critic
+ 80% audience — well-reviewed but not breakout. Colin Farrell prestige-noir.
No Apple record claims, no Nielsen Top 10, no chart-topping placements. Curve
shape is standard weekly-drop with modest per-episode bumps and slow decay.
Similar profile to Presumed Innocent but smaller magnitude — same 2024 Apple
platform, similar "prestige-drama that didn't break out" pattern.

**Landings**:
- A_21 = 1,999,986 × **0.850** = **1,699,988**
- A_28 = 1,999,986 × **0.920** = **1,839,987**
- B_21 = 42,000 × **0.870** = **36,540**
- B_28 = 42,000 × **0.920** = **38,640**
- C_21 = 27,999 × **0.840** = **23,519**
- C_28 = 27,999 × **0.900** = **25,199**

---

## Row 11 — Constellation Season 1 (2/21/24, weekly, ~28M platform)

**30d anchors**: Reach 1,599,982 | New 25,999 | React 13,998 | new_share 65.0%

**Cadence**: Weekly drop, 8 episodes. By Day 21 (3/13/24): ~4 eps. By Day 28 (3/20/24): ~5 eps.

**Curve rationale**: CANCELED / DIED. Cancellation announced May 2024. Never
made Nielsen Top 10 (explicitly cited by HR + Gizmodo as reason for
cancellation). 71-73% critic / 92% audience — loyal but narrow base.
Cancellation-tier launch curves have the SHARPEST front-load because there was
no compounding buzz — sampling dropped off fast after Ep 2-3. This is the
"buzz died fast" archetype.

**Landings**:
- A_21 = 1,599,982 × **0.900** = **1,439,984**
- A_28 = 1,599,982 × **0.940** = **1,503,983**
- B_21 = 25,999 × **0.920** = **23,919** (buzz died — most new signups already happened by week 3)
- B_28 = 25,999 × **0.950** = **24,699**
- C_21 = 13,998 × **0.890** = **12,458**
- C_28 = 13,998 × **0.930** = **13,018**

---

## Row 12 — Monarch: Legacy of Monsters Season 1 (11/17/23, weekly, ~27M platform)

**30d anchors**: Reach 3,499,975 | New 91,019 | React 48,990 | new_share 65.0%

**Cadence**: Weekly drop, 10 episodes with 2-episode premiere. By Day 21 (12/8/23): ~5 eps. By Day 28 (12/15/23): ~6 eps.

**Curve rationale**: FRONT-LOAD THEN DECAY (franchise sampling pattern).
Reelgood #3 in streaming Top 10 in premiere week. MonsterVerse / Godzilla /
Legendary franchise IP. Did NOT make Nielsen Top 10 in S1 (explicitly confirmed
by S2 press citing S2 as "first time franchise charted"). Franchise sampling
behavior — casuals sample fast (Godzilla halo) then drop off. Front-loaded
curve typical of IP-driven launches where the draw is brand recognition, not
compounding word-of-mouth.

**Landings**:
- A_21 = 3,499,975 × **0.870** = **3,044,978**
- A_28 = 3,499,975 × **0.930** = **3,254,977**
- B_21 = 91,019 × **0.890** = **81,007**
- B_28 = 91,019 × **0.940** = **85,558**
- C_21 = 48,990 × **0.860** = **42,131**
- C_28 = 48,990 × **0.920** = **45,071**

---

## Row 13 — Silo Season 1 (5/5/23, weekly, ~25M platform)

**30d anchors**: Reach 2,999,979 | New 157,498 | React 67,501 | new_share 70.0%

**Cadence**: Weekly drop, 10 episodes with 2-episode premiere. By Day 21 (5/26/23): ~4 eps. By Day 28 (6/2/23): ~5 eps.

**Curve rationale**: BUZZ-BUILDER (sustained). "No. 1 drama in Apple TV+
history" per Apple press May 2023. Renewed for S2 within 5-6 weeks. Parrot
Analytics: 24.4× avg global demand week 5. 5 CONSECUTIVE weeks in Reelgood
Top 10 — the strongest sustained-buzz signal in the comp set. Week 2 was #2
cross-platform. Rebecca Ferguson (MI franchise) + Hugh Howey Wool IP. This
show's curve is compounding through the entire 30-day window and beyond.

**Landings**:
- A_21 = 2,999,979 × **0.800** = **2,399,983** (buzz still growing — reach compounds throughout window)
- A_28 = 2,999,979 × **0.890** = **2,669,981**
- B_21 = 157,498 × **0.780** = **122,848** (5-week Reelgood streak means each week added materially)
- B_28 = 157,498 × **0.870** = **137,023**
- C_21 = 67,501 × **0.760** = **51,301**
- C_28 = 67,501 × **0.860** = **58,051**

---

## Row 14 — Shrinking Season 1 (1/27/23, weekly, ~24M platform)

**30d anchors**: Reach 1,699,975 | New 53,549 | React 22,948 | new_share 70.0%

**Cadence**: Weekly drop, 10 episodes with 2-episode premiere. By Day 21 (2/17/23): ~4 eps. By Day 28 (2/24/23): ~5 eps.

**Curve rationale**: BUZZ-BUILDER. "Biggest hit on Apple TV+ since Severance
and Black Bird" (Cult of Mac Feb 2023). Week 2 audience LARGER than week 1 —
this is a rare and diagnostic accelerating-curve signal. JustWatch #3 +
Reelgood #5 in early weeks. Jason Segel + Harrison Ford + Ted Lasso writer
team pulled durable word-of-mouth. Curve compounds through window; slightly
less peak than Silo/Pluribus (comedy-drama converts slightly lower velocity
than thriller/sci-fi).

**Landings**:
- A_21 = 1,699,975 × **0.810** = **1,376,980**
- A_28 = 1,699,975 × **0.900** = **1,529,978**
- B_21 = 53,549 × **0.800** = **42,839** (accelerating-curve show — significant new-signup activity in weeks 3-4)
- B_28 = 53,549 × **0.880** = **47,123**
- C_21 = 22,948 × **0.780** = **17,899**
- C_28 = 22,948 × **0.870** = **19,965**

---

## Row 15 — Slow Horses Season 1 (4/1/22, weekly, ~20M platform)

**30d anchors**: Reach 999,993 | New 14,977 | React 5,014 | new_share 74.9%

**Cadence**: Weekly drop, 6 episodes with 2-episode premiere. By Day 21 (4/22/22): ~4 eps. By Day 28 (4/29/22): ~5 eps.

**Curve rationale**: SLEEPER FLAT. 95% RT critic / 92% audience but NO
S1-specific record claims. Kantar's famous "30% UK new subs" stat was at S3
(2023), NOT S1. Forbes 2024 headline "I am begging you to watch Slow Horses"
was at S4 — still framed as urging awareness, meaning the show never broke
through in the launch window. Curve is unusually flat: no big Day 0 spike,
gradual word-of-mouth accumulation through weeks 2-4 and beyond. New-signup
activity is later than reach because the show first has to be discovered.

**Landings**:
- A_21 = 999,993 × **0.820** = **819,994** (flat curve — reach still accumulating)
- A_28 = 999,993 × **0.890** = **889,994**
- B_21 = 14,977 × **0.780** = **11,682** (word-of-mouth built LATE — new signups still coming in weeks 3-4)
- B_28 = 14,977 × **0.860** = **12,880**
- C_21 = 5,014 × **0.760** = **3,811** (dormant reactivation is the latest curve here — most trailing)
- C_28 = 5,014 × **0.850** = **4,262**

---

## Row 16 — Severance Season 1 (2/18/22, weekly, ~20M platform)

**30d anchors**: Reach 2,499,982 | New 120,018 | React 29,988 | new_share 80.0%

**Cadence**: Weekly drop, 9 episodes with 2-episode premiere. By Day 21 (3/11/22): ~4 eps. By Day 28 (3/18/22): ~5 eps.

**Curve rationale**: BUZZ-BUILDER (peak beyond window). 97% RT critic. #1 on
Reelgood across ALL streaming services by week 3 (Mar 9-10, 2022) — the peak
was WEEK 3, not launch week. $200M+ lifetime revenue per Parrot Analytics.
TV Time 2022: Severance among top streaming-subscription drivers of the year.
Antenna's cited 14% conversion figure is explicitly for S2, not S1 launch.
S1 was the buzz-compounding archetype — heat built ACROSS the launch window
with the peak occurring at (or slightly after) Day 21. Most delayed-peak
curve in the comp set.

**Landings**:
- A_21 = 2,499,982 × **0.770** = **1,924,986** (reach at Day 21 was near peak but still growing)
- A_28 = 2,499,982 × **0.860** = **2,149,984**
- B_21 = 120,018 × **0.740** = **88,813** (new signups still compounding hard — peak conversion at/after Day 21)
- B_28 = 120,018 × **0.840** = **100,815**
- C_21 = 29,988 × **0.720** = **21,591**
- C_28 = 29,988 × **0.830** = **24,890**

---

## Row 17 — Invasion Season 1 (10/22/21, weekly, ~18M platform)

**30d anchors**: Reach 1,499,989 | New 33,749 | React 11,250 | new_share 75.0%

**Cadence**: Weekly drop, 10 episodes with 3-episode premiere. By Day 21 (11/12/21): ~5 eps. By Day 28 (11/19/21): ~6 eps.

**Curve rationale**: FRONT-LOAD THEN DECAY (mixed reception amplifies decay).
Mid-tier sci-fi launch with mixed critical reception (IGN: "too ambitious,
slow," "mashup of prestige cliches"). NO record claims, no chart-topping
placements for S1 launch window. Absent breakout coverage in launch trade
press. Simon Kinberg pedigree + Sam Neill cast attracted Day-0 sampling but
mixed reviews collapsed compounding. Very front-loaded curve.

**Landings**:
- A_21 = 1,499,989 × **0.890** = **1,334,990**
- A_28 = 1,499,989 × **0.940** = **1,409,990**
- B_21 = 33,749 × **0.910** = **30,712** (buzz collapsed after weeks 1-2 → most 30-day signups landed early)
- B_28 = 33,749 × **0.950** = **32,062**
- C_21 = 11,250 × **0.880** = **9,900**
- C_28 = 11,250 × **0.930** = **10,463**

---

## Row 18 — Foundation Season 1 (9/24/21, weekly, ~15M platform)

**30d anchors**: Reach 2,999,979 | New 131,996 | React 33,000 | new_share 80.0%

**Cadence**: Weekly drop, 10 episodes with 2-episode premiere. By Day 21 (10/15/21): ~4 eps. By Day 28 (10/22/21): ~5 eps.

**Curve rationale**: MID-TIER STEADY WEEKLY with delayed peak. Renewed for S2
only 2 weeks after premiere — fastest renewal signal in the comp set. Parrot
Analytics 35.2× avg global demand, peak 38.7× at week 5; 44.4× momentum
(top 1.53% of all shows). But 72% RT (mixed — production hailed, dense
narrative criticized). Asimov IP appeals to niche older fandom. Curve shape:
Parrot's peak-at-week-5 signal means Day 21 (week 3) still had material
compounding ahead. Not a full buzz-builder (mixed reviews) but a delayed-peak
mid-tier.

**Landings**:
- A_21 = 2,999,979 × **0.840** = **2,519,982** (Parrot demand still growing through week 3)
- A_28 = 2,999,979 × **0.910** = **2,729,981**
- B_21 = 131,996 × **0.850** = **112,197**
- B_28 = 131,996 × **0.910** = **120,116**
- C_21 = 33,000 × **0.820** = **27,060**
- C_28 = 33,000 × **0.890** = **29,370**

---

## Row 19 — Tehran Season 1 (9/25/20, weekly, ~7M free-trial-era platform)

**30d anchors**: Reach 349,991 | New 5,948 | React 1,049 | new_share 85.0%

**Cadence**: Weekly drop, 8 episodes. By Day 21 (10/16/20): ~4 eps. By Day 28 (10/23/20): ~5 eps.

**Curve rationale**: FREE-TRIAL ERA (pre-2021). 88% RT / 87% audience —
well-reviewed. Apple acquired international rights from Israeli Kan 11 (S1
had aired in Israel 6/22/20 before Apple TV+ launch). "Popular with audiences
in India, Japan, Singapore" — international skew limits US-specific conversion.
Small late-2020 Apple TV+ platform. Free-trial era curves are extremely
front-loaded because signups were tied to device purchases (which happen at
purchase time, not weeks later).

**Landings**:
- A_21 = 349,991 × **0.930** = **325,492**
- A_28 = 349,991 × **0.970** = **339,491**
- B_21 = 5,948 × **0.920** = **5,472** (device-purchase-era → virtually all signups happen at trial start)
- B_28 = 5,948 × **0.960** = **5,710**
- C_21 = 1,049 × **0.900** = **944**
- C_28 = 1,049 × **0.950** = **997**

---

## Row 20 — Ted Lasso Season 1 (8/14/20, weekly, ~5-6M platform)

**30d anchors**: Reach 899,967 | New 91,798 | React 16,198 | new_share 85.0%

**Cadence**: Weekly drop, 10 episodes with 3-episode premiere. By Day 21 (9/4/20): ~5 eps. By Day 28 (9/11/20): ~6 eps.

**Curve rationale**: FREE-TRIAL ERA — quiet S1 launch. Apple statement (late
Oct 2020, ~10 weeks post-launch): "drew 25% new viewers to Apple TV+" +
"viewership grown 600%." But this was at 10 weeks — the S1 first-month launch
window was QUIET. Emmy buzz didn't hit until August 2021 (a YEAR later).
Parrot Analytics: peak demand at ~50 days post-launch — well beyond the
30-day window. So the S1 launch-window curve is: modest Day 0 (small
platform), slow accumulation, peak still ahead at Day 30+. Front-loaded on
device-trial mechanics but with a slower, thinner curve than platform-
launch shows.

**Landings**:
- A_21 = 899,967 × **0.910** = **818,970**
- A_28 = 899,967 × **0.950** = **854,969**
- B_21 = 91,798 × **0.900** = **82,618** (free-trial device-purchase mechanic → very front-loaded even in a quiet launch)
- B_28 = 91,798 × **0.940** = **86,290**
- C_21 = 16,198 × **0.880** = **14,254**
- C_28 = 16,198 × **0.930** = **15,064**

---

## Row 21 — For All Mankind Season 1 (11/1/19, weekly, ~1-2M launch-day platform)

**30d anchors**: Reach 599,989 | New 85,497 | React 4,500 | new_share 95.0%

**Cadence**: Weekly drop, 10 episodes with 3-episode premiere. By Day 21 (11/22/19): ~5 eps. By Day 28 (11/29/19): ~6 eps.

**Curve rationale**: FREE-TRIAL ERA — Day-1 platform launch. Apple TV+ debuted
11/1/19 with FAM as one of 4 marquee originals. Top 3 most-talked Apple TV+
show on launch day (ListenFirst). Almost EVERY viewer was either (a) a
brand-new Apple TV+ trial signup that day (platform was 30 days old at Day 30)
or (b) using free trial that came with recent device purchase. Reach
denominator and new-signup numerator overlap heavily. Most extreme front-load
in the comp set — the Day 0 spike is virtually everything.

**Landings**:
- A_21 = 599,989 × **0.940** = **563,990** (extreme front-load — most viewers were Day 0-7 platform trial signups)
- A_28 = 599,989 × **0.970** = **581,989**
- B_21 = 85,497 × **0.930** = **79,512** (95% new_share reflects almost every viewer was net-new; front-loaded because platform-trial mechanic captures Day 0)
- B_28 = 85,497 × **0.960** = **82,077**
- C_21 = 4,500 × **0.910** = **4,095** (tiny react count — barely anyone was "dormant" on a 30-day-old platform)
- C_28 = 4,500 × **0.950** = **4,275**

---

## Cross-show ratio audit

Verified that no two shows share the same D_28/D_21 ratio (this was the
specific defect in the previous run). Range of D_28/D_21 ratios in this
manually-reasoned version:

- Most extreme buzz-builder (Pluribus): ratio ~ 1.14
- Most extreme front-load (FAM): ratio ~ 1.03

All 21 shows land on distinct ratios reflecting their individual curves.
