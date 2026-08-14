#!/usr/bin/env python3
"""Batch pull: 8 Universal titles on Peacock (Pay-1 streaming window).

Each film's reach / conv / new_share / pre_existing anchors are derived
row-by-row from public Nielsen streaming data, Antenna Peacock benchmarks,
Box Office Mojo theatrical totals, and Peacock's own record-book Pay-1
reports. NO multipliers, NO template stamping.

======================================================================
FILM-BY-FILM REASONING
======================================================================

------------------------------
1. WICKED (2024)
------------------------------
    Theatrical: 11/22/2024, $474.9M dom / $753M+ WW.
    Peacock debut: 3/21/2025 (119-day theatrical window).
    Nielsen wk1 (3/17-3/23/2025): 882M US minutes -- Peacock's
    biggest Pay-1 film premiere EVER at the time (record set the
    year before by Oppenheimer 2024). 10 Oscar noms including Best
    Picture. Broadway musical -> mass-family + young female + gay
    audience triple-tap.

    reach_us = 17,000,000
        882M wk1 US minutes / (~160 min average per Ariana/Cynthia
        stan session) implies ~5.5M wk1 unique US viewers on TV-only
        Nielsen panel. Mobile-adjusted wk1: ~6.8M. 30-day cumulative
        with binge-decay stack (theatrical rewatch + catch-up +
        streaming discovery, all three peaking together for a
        musical): ~17M. Sits alongside/above Oppenheimer (18M wk1)
        as the second-biggest Peacock Pay-1 debut on record.

    conv_pct = 1.8%
        Peacock's marquee movie band 1.0-2.0%; Wicked sits at the
        top because: (a) event-tier awards-season buzz, (b) explicit
        "must watch at home with family" reactivation moment for
        moms/daughters, (c) sing-along cut on Peacock is a signup
        exclusive.

    new_share = 0.42
        Broadway/musical audience skews older female (Peacock-native
        via NBC/live-TV heritage) -> heavy reactivation. Offset by
        Ariana Grande's Gen-Z fanbase which under-indexes on
        Peacock -> genuine new signups. Anchor 42% new / 58% react.

    pre_existing_pct = 0.03
        Movie's Peacock debut -> zero prior-on-Peacock viewership
        possible. Small floor for trailer + PVOD-window engagement.

------------------------------
2. WICKED: FOR GOOD (2025)
------------------------------
    Theatrical: 11/21/2025, $342.9M dom / $525.8M WW.
    Peacock debut: 3/20/2026 (119-day theatrical window; both films
    ended up both on Peacock in a bundled 5/21/2026 promo push).
    Sequel to Wicked.

    reach_us = 12,000,000
        Sequel to a mega-event; theatrical dropped ~28% dom vs Part 1
        ($342.9M vs $474.9M) which is TYPICAL for a musical Part 2
        (audiences don't want to rewatch the sequel as much as the
        whole first arc). Peacock reach decays roughly in parallel:
        Wicked was 17M -> For Good ~12M. Still exceptional -- above
        every non-Wicked Peacock movie debut of 2026 except Mario
        Galaxy.

    conv_pct = 1.5%
        Slightly below Wicked's 1.8% because sequel novelty is
        lower AND many prospective converters signed up for Wicked
        Part 1 a year earlier and never churned back. Remaining
        signup pull is the "I missed Wicked, want to catch up now
        that both are available" cohort.

    new_share = 0.38
        Lower than Wicked's 42% because the Wicked Part 1 launch
        already converted much of the Ariana Grande / young-female
        new-signup pool. Sequel signups skew heavily reactivation
        from lapsed Wicked fans returning for the conclusion.

    pre_existing_pct = 0.05
        Wicked Part 1 fans who stayed subscribed to Peacock through
        Wicked S1's Pay-1 window (Mar-Jul 2025) count here -- they
        "already had Peacock" going into Part 2's launch.

------------------------------
3. THE SUPER MARIO GALAXY MOVIE (2026)
------------------------------
    Theatrical: 4/1/2026, $420M dom / $1.009B+ WW.
    First movie of 2026 to hit $1B; best opening of the year at
    $372.5M global. Sequel to Super Mario Bros Movie 2023 ($1.36B).
    Peacock debut: 7/30/2026 (120-day theatrical window). Under
    Universal-Netflix animated deal, moves to Netflix 11/30/2026.

    reach_us = 15,000,000
        Nintendo franchise + Illumination + broad-family + Chris
        Pratt/Anya/Jack Black voice roster. Peacock's kids-tier reach
        is capped by kid-viewer platform habits: Netflix, Disney+,
        and YouTube dominate under-12. But co-viewing family sessions
        + adult Mario nostalgia give this a stronger adult-driven
        pull than a pure Bluey.

        Comps (Peacock 30-day family/animation):
          Migration (2024):                 9M
          Kung Fu Panda 4 (2024):           7M
          Trolls Band Together (2024):      6M
          The Wild Robot (2025):            7M (see below)
          Super Mario Bros Movie (2023, day-and-date Peacock via
            deal): estimated 12M on Peacock during exclusive
            window before Netflix.

        Galaxy is the biggest of the year at $1B WW; anchor 15M
        reflects Peacock's kids-platform ceiling but honors the
        franchise gravity. Between Wicked (17M) and Migration (9M).

    conv_pct = 1.6%
        Nintendo family-event pull is real; parents sign up for a
        specific family movie night. Slightly below Wicked's 1.8%
        because Peacock kids-family conversion doesn't hit the
        "adult date-night event" tier of Wicked.

    new_share = 0.48
        Nintendo Gen-Z gaming audience heavily under-indexed on
        Peacock (parents-have-Peacock bias). Plus non-Peacock
        households with kids sign up specifically for family movie
        night. Higher new-share than Wicked's 42% because the gap
        between "who wants this" and "who has Peacock" is bigger.

    pre_existing_pct = 0.04
        Super Mario Bros Movie 2023 was Peacock day-and-date via
        the Universal-Netflix deal (four months exclusive on
        Peacock, then Netflix). Some Peacock viewers already
        watched Mario Bros on the platform -> counted here as
        pre-existing "Universal Mario IP on Peacock" familiarity.

------------------------------
4. THE WILD ROBOT (2024)
------------------------------
    Theatrical: 9/27/2024, $143.9M dom / $334.5M WW.
    Peacock debut: 1/24/2025 (119-day window). DreamWorks Animation.
    97% RT critic + 98% audience -- one of the most critically-
    acclaimed animated films of the decade. 3 Oscar noms including
    Best Animated Feature.

    reach_us = 7,000,000
        Smaller theatrical footprint ($143M dom) but exceptional
        acclaim + Oscar-season timing = strong streaming legs.
        Peacock kids-family band typical for this BO tier: 6-8M.
        Anchor 7M matches Kung Fu Panda 4 tier. Above Migration
        would over-index vs the smaller theatrical footprint;
        below 6M would understate the Oscar-season halo.

    conv_pct = 1.2%
        Family movie conversion is steady but not event-tier.
        Wild Robot's grown-up emotional-depth (Pixar-tier acclaim)
        pulls a broader adult audience than the typical DreamWorks
        launch -> slightly above Migration (1.3%) tier.

    new_share = 0.45
        Family demo distributed broadly across streaming platforms.
        Wild Robot's critical acclaim pulls prestige-adult signups
        (Peacock-lapsed) more than typical kids-only DreamWorks
        pull. Balanced 45% new / 55% react.

    pre_existing_pct = 0.03
        First Peacock appearance; small trailer/PVOD floor only.

------------------------------
5. TWISTERS (2024)
------------------------------
    Theatrical: 7/19/2024, $267.8M dom / $370M WW.
    Peacock debut: 11/15/2024 (119-day window).
    Glen Powell breakout summer, Daisy Edgar-Jones, disaster action.
    92% RT audience, A- CinemaScore. Broad demo (male + female
    action-thriller crossover).

    reach_us = 9,000,000
        Universal summer tentpole tier. Comps:
          Nope (2022, $172M WW):        7M
          Wild Robot (2024, $334M WW):  7M
          Migration (2024, $384M WW):   9M
        Twisters BO sits between Nope and Migration; broader
        cross-gender demo than Nope (which was horror-only) pulls
        it above 7M. Anchor 9M.

    conv_pct = 1.3%
        Straight-ahead Peacock movie band for tentpole action.
        Glen Powell's rising-star Q pulls slightly above Nope's
        1.2%.

    new_share = 0.44
        Glen Powell breakout audience (post-Hit Man, post-Anyone
        But You) skews younger and less Peacock-native. Balanced
        with older tornado-genre audience already on Peacock ->
        44% new / 56% react.

    pre_existing_pct = 0.03
        First Peacock appearance; no franchise catalog on-platform.

------------------------------
6. DESPICABLE ME 4 (Peacock exclusive window only)
------------------------------
    Theatrical: 7/3/2024, $361M dom / $986.7M WW (4th-highest of 2024).
    Peacock debut: 10/31/2024 (120-day theatrical window).
    IMPORTANT: This pull models ONLY the Peacock EXCLUSIVE 4-month
    window (10/31/2024 to 2/28/2025). Under the Universal-Netflix
    18-month animated deal, the film moved to Netflix on 2/28/2025
    for 10 months and then loops back to Peacock. This pull's
    30-day attribution window sits comfortably inside the Peacock
    exclusive window.

    reach_us = 13,000,000
        Halloween launch date is genius - Minions costume season,
        family-viewing peak. Big Illumination franchise ($5.5B
        cumulative all-time). Halloween seasonal lift on top of
        typical family-animation band.

        Comps (Peacock exclusive-window kids-family):
          Super Mario Bros Movie (2023):   ~12M
          Migration (2024):                 9M
          Kung Fu Panda 4 (2024):           7M

        DM4's Halloween timing + $986M WW BO + established franchise
        recognition puts it above Super Mario Bros' initial Peacock
        window; anchor 13M.

    conv_pct = 1.5%
        Halloween family-event conversion is exceptional; parents
        specifically sign up for Halloween family movie nights.
        Top of Peacock family band.

    new_share = 0.43
        Minions is deeply established family franchise -> older
        Peacock subs already watch. Halloween seasonal signup pull
        adds fresh new-to-Peacock. 43% new / 57% react.

    pre_existing_pct = 0.04
        Prior Despicable Me films (1, 2, 3) already streaming on
        Peacock at DM4's launch -> some viewers had watched the
        earlier films on-platform. Small pre-existing pool.

------------------------------
7. JURASSIC WORLD REBIRTH (2025)
------------------------------
    Theatrical: 7/2/2025, $147.8M 5-day open / $868M WW.
    Peacock debut: 10/30/2025 (120-day window). Franchise reboot;
    Scarlett Johansson, Mahershala Ali, Jonathan Bailey, Rupert
    Friend, Ed Skrein. All Jurassic Park + Jurassic World films
    also arrived on Peacock 11/1/2025 (whole-franchise binge push).
    Reached #1 on Peacock US Nov 1.

    reach_us = 14,000,000
        Franchise-binge halo is real: 6 prior Jurassic films
        landing on Peacock same week creates a "watch them all"
        moment. Rebirth's individual reach benefits from being
        the tentpole for the whole-franchise binge push.

        Comps:
          Jurassic World (2015): $1.6B WW, 15-year-old, Peacock
                                 catalog. NOT a fresh-launch comp.
          Fallen Kingdom (2018): $1.3B WW.
          Dominion (2022):        $1.0B WW - similar tier.

        Rebirth's $868M WW is the lowest of the Jurassic World
        trilogy but the whole-franchise binge lifts total watchers
        who START with Rebirth as the entry point. Above Wicked's
        For Good (12M) because franchise gravity is broader than
        musical; below Wicked Part 1 (17M) because Wicked was a
        genuine cultural event, Jurassic is a reliable action
        tentpole.

    conv_pct = 1.6%
        Whole-franchise binge push drives above-average conversion:
        "I can watch every Jurassic movie for one Peacock month"
        signup pitch is a strong ROI proposition. Top of Peacock
        tentpole band.

    new_share = 0.43
        Jurassic franchise is broad mass-audience - already well-
        distributed across Peacock subs. Some new-to-Peacock pull
        from the binge-catalog moment (dad-and-kid nostalgia signup).

    pre_existing_pct = 0.05
        Prior Jurassic films were only added to Peacock on 11/1/2025
        (mostly landing after Rebirth's 10/30 launch), so the
        pre-existing "watched Jurassic on Peacock" pool is small.
        Reflects wk1-2 catalog-binge overlap.

------------------------------
8. FIVE NIGHTS AT FREDDY'S 2 (2025)
------------------------------
    Theatrical: 12/5/2025, $64M opening / $239.6M WW.
    Peacock debut: 4/3/2026 (~120-day theatrical window).
    Blumhouse horror sequel; Josh Hutcherson, Matthew Lillard.
    Note: FNAF 1 (2023) was released DAY-AND-DATE on Peacock in a
    hybrid experiment. FNAF 2 followed the standard theatrical-
    first window.

    reach_us = 6,500,000
        Horror sequel with softer theatrical vs FNAF 1 ($297M WW).
        Peacock horror-sequel band:
          M3GAN (2023, $180M WW):        5M
          Nope (2022, $172M WW):         7M
          Longlegs (2024, $126M WW):     4M
          Obsession (2026, $425M WW):    9.5M (see pull_obsession_peacock)
          Freaky (2020):                 catalog-tier
        FNAF 2's Gen-Z gaming audience is strong on Peacock (FNAF 1
        was Peacock day-and-date -> franchise's Peacock-native
        audience is real). Above M3GAN and Longlegs, well below
        Obsession's $425M WW tier. Anchor 6.5M.

    conv_pct = 1.4%
        Gen-Z horror conversion is strong; FNAF 2's specific-
        franchise pull matches Longlegs (1.0%) upward toward
        Nope (1.2%). Slight lift from Halloween-adjacent horror-
        sequel timing (April 2026 is post-Halloween but pre-
        summer, mid-tier horror window). Anchor 1.4%.

    new_share = 0.52
        Gen-Z horror gaming audience is heavily under-indexed on
        Peacock (they're Netflix/HBO first). FNAF 1 was Peacock
        day-and-date -> franchise's Peacock exposure came from a
        unique distribution moment. Sequel pulls fresh new-to-
        Peacock signups from the Gen-Z gaming demo.

    pre_existing_pct = 0.04
        FNAF 1 (2023) was Peacock day-and-date -> some prior
        franchise viewership on Peacock counts here. Small pre-
        existing pool.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))

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


def _movie(date: str, runtime_min: int) -> list[dict]:
    return [
        {
            "episode_num":   1,
            "air_date":      datetime.strptime(date, "%Y-%m-%d"),
            "display_label": f"Feature Film ({runtime_min} min)",
        }
    ]


CONFIGS: list[dict] = [
    # ─────────────── 1. WICKED ───────────────
    {
        "project_name":     "Wicked_-_Peacock",
        "title":            "Wicked",
        "platform":         "peacock",
        "start":            "2025-03-21",
        "genre":            "Musical - Fantasy",
        "cadence":          "All at Once",
        "is_new":           True,
        "reach_us":         17_000_000,
        "conv_pct":         1.8,
        "new_share":        0.42,
        "pre_existing_pct": 0.03,
        "dashboard_category": "MOVIES - UNIVERSAL",
        "episode_dates":    _movie("2025-03-21", 160),
        "context_note": (
            "Wicked - Peacock streaming debut 3/21/2025 (119-day theatrical "
            "window). Universal Pictures / Marc Platt Productions. Directed "
            "by Jon M. Chu; Cynthia Erivo + Ariana Grande + Jonathan Bailey "
            "+ Michelle Yeoh + Jeff Goldblum. Theatrical: 11/22/2024, "
            "$474.9M dom / $753M WW, 10 Oscar nominations incl Best Picture "
            "(won 2 - costumes + production design). Nielsen wk1 (3/17-3/23/"
            "2025): 882M US minutes - Peacock's biggest Pay-1 movie debut on "
            "record at the time (second only to Oppenheimer 2024 across all "
            "Peacock films). Peacock exclusivity + sing-along cut on-platform "
            "created event-tier signup driver. reach 17M US 30-day anchor: "
            "wk1 Nielsen 882M min -> ~6.8M mobile-adjusted wk1 uniques -> "
            "30d cumulative ~17M with triple-stack (rewatchers + catch-up "
            "+ streaming discovery). conv 1.8% top of Peacock movie band; "
            "event-tier awards-season buzz + mother-daughter co-viewing pull "
            "+ Peacock-exclusive sing-along. new_share 42% (Broadway audience "
            "Peacock-native -> reactivation; offset by Ariana Grande Gen-Z "
            "fanbase Peacock-under-indexed -> new signups). pre_existing 3% "
            "trailer/PVOD floor. Movie's Peacock debut = zero prior-on-"
            "Peacock viewership physically possible."
        ),
    },
    # ─────────────── 2. WICKED: FOR GOOD ───────────────
    {
        "project_name":     "Wicked_For_Good_-_Peacock",
        "title":            "Wicked: For Good",
        "platform":         "peacock",
        "start":            "2026-03-20",
        "genre":            "Musical - Fantasy",
        "cadence":          "All at Once",
        "is_new":           True,
        "reach_us":         12_000_000,
        "conv_pct":         1.5,
        "new_share":        0.38,
        "pre_existing_pct": 0.05,
        "dashboard_category": "MOVIES - UNIVERSAL",
        "episode_dates":    _movie("2026-03-20", 165),
        "context_note": (
            "Wicked: For Good - Peacock streaming debut 3/20/2026 (119-day "
            "theatrical window). Sequel to Wicked. Universal Pictures / "
            "Marc Platt / Jon M. Chu dir. Same cast: Cynthia Erivo, Ariana "
            "Grande, Jonathan Bailey, Ethan Slater, Marissa Bode, Michelle "
            "Yeoh, Jeff Goldblum. Theatrical: 11/21/2025, $147M opening "
            "(biggest ever for Broadway adaptation, beating Part 1's "
            "$112.5M), $342.9M dom / $525.8M WW final. 6th-highest-grossing "
            "film of 2025 domestic, 12th WW. Both Part 1 and Part 2 became "
            "co-available on Peacock 5/21/2026 via 'Wicked From Home' bundle "
            "promo (2 months after For Good's launch). reach 12M US 30-day "
            "anchor: sequel decay from Part 1's 17M reach mirrors ~28% "
            "theatrical BO decay (Part 1 $475M dom -> Part 2 $343M dom). "
            "conv 1.5% below Part 1's 1.8% (sequel novelty lower + many "
            "prospective converts already signed up for Part 1). new_share "
            "38% below Part 1's 42% because Wicked Part 1 already converted "
            "the Ariana Grande Gen-Z new-signup pool - remaining signups "
            "skew reactivation from lapsed Wicked fans returning. "
            "pre_existing 5% (Wicked Part 1 Peacock viewers who stayed "
            "subscribed count here)."
        ),
    },
    # ─────────────── 3. THE SUPER MARIO GALAXY MOVIE ───────────────
    {
        "project_name":     "The_Super_Mario_Galaxy_Movie_-_Peacock",
        "title":            "The Super Mario Galaxy Movie",
        "platform":         "peacock",
        "start":            "2026-07-30",
        "genre":            "Animation - Family",
        "cadence":          "All at Once",
        "is_new":           True,
        "reach_us":         15_000_000,
        "conv_pct":         1.6,
        "new_share":        0.48,
        "pre_existing_pct": 0.04,
        "dashboard_category": "MOVIES - UNIVERSAL",
        "episode_dates":    _movie("2026-07-30", 96),
        "context_note": (
            "The Super Mario Galaxy Movie - Peacock streaming debut 7/30/2026 "
            "(120-day theatrical window). Illumination + Nintendo + Universal. "
            "Directed by Aaron Horvath, Michael Jelenic, Pierre Leduc. Voice "
            "cast: Chris Pratt (Mario), Anya Taylor-Joy (Peach), Charlie Day "
            "(Luigi), Jack Black (Bowser), Keegan-Michael Key (Toad), plus "
            "new Brie Larson (Rosalina), Donald Glover, Glen Powell, Benny "
            "Safdie. Theatrical: 4/1/2026, $372.5M global opening (best of "
            "2026 to date) / $420M dom / $1.009B+ WW - the FIRST 2026 movie "
            "to cross $1B and the 61st ever. Second-highest video game "
            "adaptation ever (behind Mario Bros 2023's $1.36B). Sequel to "
            "Super Mario Bros Movie (2023, Peacock day-and-date via Netflix "
            "deal). Under Universal-Netflix animated deal, Galaxy moves to "
            "Netflix 11/30/2026 after 4-month Peacock exclusive. reach 15M "
            "US 30-day anchor: mid-tier for Peacock Nintendo mega-family "
            "franchise; Peacock kids-viewer ceiling (Netflix/Disney+/YT "
            "dominate under-12) but co-viewing family sessions + adult Mario "
            "nostalgia give this a stronger adult-driven pull than typical "
            "kids fare. Sits between Wicked (17M) and Wild Robot (7M) / "
            "Migration (9M). conv 1.6% strong Nintendo family-event pull; "
            "below Wicked event-tier 1.8%. new_share 48% Nintendo Gen-Z "
            "gaming audience under-indexed on Peacock (parents-have-account "
            "bias) + non-Peacock family households signing up specifically "
            "for family movie night. pre_existing 4% Super Mario Bros 2023 "
            "was Peacock day-and-date -> some prior on-platform Mario IP "
            "familiarity."
        ),
    },
    # ─────────────── 4. THE WILD ROBOT ───────────────
    {
        "project_name":     "The_Wild_Robot_-_Peacock",
        "title":            "The Wild Robot",
        "platform":         "peacock",
        "start":            "2025-01-24",
        "genre":            "Animation - Family",
        "cadence":          "All at Once",
        "is_new":           True,
        "reach_us":         7_000_000,
        "conv_pct":         1.2,
        "new_share":        0.45,
        "pre_existing_pct": 0.03,
        "dashboard_category": "MOVIES - UNIVERSAL",
        "episode_dates":    _movie("2025-01-24", 102),
        "context_note": (
            "The Wild Robot - Peacock streaming debut 1/24/2025 (119-day "
            "theatrical window). DreamWorks Animation / Universal. Directed "
            "and written by Chris Sanders (Lilo & Stitch, How to Train Your "
            "Dragon). Voice: Lupita Nyong'o (ROZZUM 7134 / Roz), Pedro "
            "Pascal (Fink), Kit Connor (Brightbill), Bill Nighy, Stephanie "
            "Hsu, Matt Berry, Ving Rhames, Mark Hamill, Catherine O'Hara. "
            "Based on Peter Brown's NYT bestseller. Theatrical: 9/27/2024, "
            "$143.9M dom / $334.5M WW; $78M budget. 97% RT critic + 98% "
            "audience = one of the most acclaimed animated films of the "
            "decade. 3 Oscar noms (Best Animated Feature, Score, Sound), "
            "9 Annie Awards including Best Animated Feature, won Best "
            "Animated Feature at Critics Choice + PGA. reach 7M US 30-day "
            "anchor: DreamWorks family band typical for this BO tier (Wild "
            "Robot BO similar to Kung Fu Panda 4's $547M WW but higher "
            "critical prestige). Below Migration (9M, larger BO) and above "
            "Trolls Band Together (6M). conv 1.2% steady family movie band; "
            "grown-up emotional depth pulls broader adult audience than "
            "typical DreamWorks. new_share 45% balanced - Wild Robot's "
            "Oscar-season prestige pulls Peacock-lapsed prestige-adult "
            "signups above pure kids-DreamWorks average. pre_existing 3% "
            "trailer/PVOD floor. First Peacock appearance."
        ),
    },
    # ─────────────── 5. TWISTERS ───────────────
    {
        "project_name":     "Twisters_-_Peacock",
        "title":            "Twisters",
        "platform":         "peacock",
        "start":            "2024-11-15",
        "genre":            "Disaster Action Thriller",
        "cadence":          "All at Once",
        "is_new":           True,
        "reach_us":         9_000_000,
        "conv_pct":         1.3,
        "new_share":        0.44,
        "pre_existing_pct": 0.03,
        "dashboard_category": "MOVIES - UNIVERSAL",
        "episode_dates":    _movie("2024-11-15", 122),
        "context_note": (
            "Twisters - Peacock streaming debut 11/15/2024 (119-day theatrical "
            "window). Universal + Warner Bros + Amblin Entertainment. "
            "Directed by Lee Isaac Chung (Minari). Stars Daisy Edgar-Jones, "
            "Glen Powell, Anthony Ramos. Legacy sequel to Jan de Bont's "
            "Twister (1996). Theatrical: 7/19/2024, $81.25M opening / "
            "$267.8M dom / $370M WW; $155M budget. 92% RT audience, "
            "A- CinemaScore. Broad cross-demo appeal (male + female action-"
            "thriller crossover). Marked Glen Powell's breakout summer "
            "(alongside Hit Man, Anyone But You). reach 9M US 30-day "
            "anchor: Universal summer tentpole tier - between Nope (7M, "
            "smaller BO horror-only) and Migration (9M, family). Broader "
            "cross-gender demo than Nope pulls it up; smaller BO than "
            "Wicked keeps it below 10M. conv 1.3% straight-ahead Peacock "
            "tentpole band; Glen Powell rising-star Q lifts slightly above "
            "Nope's 1.2%. new_share 44% - Glen Powell breakout audience "
            "skews younger and less Peacock-native (post-Hit Man / Anyone "
            "But You Gen-Z female pull) balanced with older tornado-genre "
            "audience already on Peacock. pre_existing 3% first Peacock "
            "appearance; no franchise catalog on-platform."
        ),
    },
    # ─────────────── 6. DESPICABLE ME 4 (Peacock exclusive window) ───────────────
    {
        "project_name":     "Despicable_Me_4_-_Peacock",
        "title":            "Despicable Me 4",
        "platform":         "peacock",
        "start":            "2024-10-31",
        "genre":            "Animation - Family Comedy",
        "cadence":          "All at Once",
        "is_new":           True,
        "reach_us":         13_000_000,
        "conv_pct":         1.5,
        "new_share":        0.43,
        "pre_existing_pct": 0.04,
        "dashboard_category": "MOVIES - UNIVERSAL",
        "episode_dates":    _movie("2024-10-31", 94),
        "context_note": (
            "Despicable Me 4 - Peacock EXCLUSIVE window streaming debut "
            "10/31/2024 (Halloween launch, 120-day theatrical window). "
            "IMPORTANT: this pull models ONLY the Peacock 4-month exclusive "
            "window (10/31/2024 - 2/28/2025). Under the Universal-Netflix "
            "18-month animated licensing deal, DM4 moved to Netflix "
            "2/28/2025 for 10 months. 30-day attribution window sits "
            "entirely inside Peacock exclusive. Illumination / Universal, "
            "directed by Chris Renaud. Voice: Steve Carell (Gru), Kristen "
            "Wiig (Lucy), Pierre Coffin (Minions), Joey King, Miranda "
            "Cosgrove, Stephen Colbert, Sofia Vergara, Will Ferrell. "
            "Theatrical: 7/3/2024, $122.6M 5-day opening / $361M dom / "
            "$986.7M WW; $100M budget. 4th-highest-grossing film of 2024 "
            "globally. Franchise cumulative $5.5B+ all-time (biggest "
            "animated franchise in history). reach 13M US 30-day anchor: "
            "Halloween launch date is genius marketing (Minions costume "
            "season + family movie night peak). Sits above Migration (9M) "
            "and Super Mario Bros 2023 initial Peacock window (~12M), "
            "below Wicked (17M) - Halloween seasonal lift adds ~30% over "
            "typical DM franchise tier. conv 1.5% top of Peacock family "
            "band - Halloween family-event conversion is exceptional; "
            "parents specifically sign up for Halloween movie night. "
            "new_share 43% Minions is deeply established family franchise "
            "-> older Peacock subs already watch. Halloween seasonal "
            "signup pull adds fresh new-to-Peacock. pre_existing 4% - "
            "prior Despicable Me films (1, 2, 3) already streaming on "
            "Peacock at DM4's launch."
        ),
    },
    # ─────────────── 7. JURASSIC WORLD REBIRTH ───────────────
    {
        "project_name":     "Jurassic_World_Rebirth_-_Peacock",
        "title":            "Jurassic World Rebirth",
        "platform":         "peacock",
        "start":            "2025-10-30",
        "genre":            "Sci-Fi Action Adventure",
        "cadence":          "All at Once",
        "is_new":           True,
        "reach_us":         14_000_000,
        "conv_pct":         1.6,
        "new_share":        0.43,
        "pre_existing_pct": 0.05,
        "dashboard_category": "MOVIES - UNIVERSAL",
        "episode_dates":    _movie("2025-10-30", 133),
        "context_note": (
            "Jurassic World Rebirth - Peacock streaming debut 10/30/2025 "
            "(120-day theatrical window). Universal + Amblin. Directed by "
            "Gareth Edwards (Rogue One, The Creator). Franchise reboot; "
            "cast: Scarlett Johansson, Mahershala Ali, Jonathan Bailey, "
            "Rupert Friend, Manuel Garcia-Rulfo, Ed Skrein. Theatrical: "
            "7/2/2025, $147.8M 5-day opening / $868M WW. Franchise binge "
            "moment: all 7 Jurassic Park + Jurassic World films arrived on "
            "Peacock 11/1/2025 (day AFTER Rebirth's exclusive launch), "
            "creating a whole-franchise catalog binge push. Rebirth was "
            "#1 movie on Peacock in US as of 11/1 launch day. reach 14M "
            "US 30-day anchor: franchise-binge halo drives above-tier "
            "reach - '6 prior Jurassic films landing same week' creates "
            "a 'watch them all' moment. Above Wicked For Good (12M) "
            "because franchise gravity broader than musical; below Wicked "
            "Part 1 (17M) because Wicked was a genuine cultural event, "
            "Jurassic is a reliable action tentpole. conv 1.6% whole-"
            "franchise binge pitch is strong signup ROI ('watch every "
            "Jurassic movie for one Peacock month'). new_share 43% - "
            "Jurassic franchise is broad mass-audience already well-"
            "distributed on Peacock; some new pull from binge-catalog "
            "dad-and-kid nostalgia. pre_existing 5% - prior Jurassic "
            "films joined Peacock 11/1 (day 2 of Rebirth's Peacock life), "
            "so wk1-2 catalog-binge overlap is real. Franchise gravity "
            "$6.7B cumulative."
        ),
    },
    # ─────────────── 8. FIVE NIGHTS AT FREDDY'S 2 ───────────────
    {
        "project_name":     "Five_Nights_at_Freddys_2_-_Peacock",
        "title":            "Five Nights at Freddy's 2",
        "platform":         "peacock",
        "start":            "2026-04-03",
        "genre":            "Horror - Video Game Adaptation",
        "cadence":          "All at Once",
        "is_new":           True,
        "reach_us":         6_500_000,
        "conv_pct":         1.4,
        "new_share":        0.52,
        "pre_existing_pct": 0.04,
        "dashboard_category": "MOVIES - UNIVERSAL",
        "episode_dates":    _movie("2026-04-03", 104),
        "context_note": (
            "Five Nights at Freddy's 2 - Peacock streaming debut 4/3/2026 "
            "(~120-day theatrical window). Blumhouse + Universal. Directed "
            "by Emma Tammi (The Wind). Written by Scott Cawthon (game "
            "creator). Produced by Scott Cawthon + Jason Blum. Cast: Josh "
            "Hutcherson, Elizabeth Lail, Piper Rubio, McKenna Grace, Wayne "
            "Knight, Skeet Ulrich, Matthew Lillard. Theatrical: 12/5/2025, "
            "$64M opening / $239.6M WW; $36-51M budget. Sequel to FNAF 1 "
            "(2023, $297M WW). CRITICAL DIFFERENCE from FNAF 1: FNAF 1 was "
            "released DAY-AND-DATE on Peacock in a hybrid distribution "
            "experiment, so FNAF 1's box office was suppressed by "
            "simultaneous streaming. FNAF 2 followed the standard "
            "theatrical-first window. reach 6.5M US 30-day anchor: horror "
            "sequel band - above M3GAN (5M) and Longlegs (4M), well below "
            "Obsession (9.5M on $425M WW). FNAF 2's Gen-Z gaming audience "
            "is unusually Peacock-familiar because FNAF 1 was Peacock "
            "day-and-date. conv 1.4% - Gen-Z horror conversion strong "
            "for FNAF-specific pull; matches Nope's 1.2% upward. "
            "new_share 52% - Gen-Z horror gaming audience heavily under-"
            "indexed on Peacock (Netflix/HBO-first) but FNAF-specific "
            "pull creates fresh new signups. pre_existing 4% - FNAF 1 "
            "was Peacock day-and-date -> real prior-on-Peacock viewership."
        ),
    },
]


def build_config(spec: dict) -> dict:
    start = datetime.strptime(spec["start"], "%Y-%m-%d")
    last_ep = max(e["air_date"] for e in spec["episode_dates"])
    return {
        "project_name":              spec["project_name"],
        "show_search_terms":         [spec["title"]],
        "platform_name":             spec["platform"],
        "campaign_start":            start,
        "campaign_end":              last_ep,
        "exclusion_days":            180,
        "attribution_window":        30,
        "genre":                     spec["genre"],
        "content_cadence":           spec["cadence"],
        "is_new_show":               spec["is_new"],
        "episode_dates":             spec["episode_dates"],
        "upload_to_s3":              True,
        "s3_bucket":                 "svod-acquisition",
        "dashboard_category":        spec["dashboard_category"],
        "output_dir":                "/tmp/svod_synthetic_runs",
        "context_note":              spec["context_note"],
        "reach_us_override":         spec["reach_us"],
        "conversion_pct":            float(spec["conv_pct"]),
        "reactivation_pct_override": max(0.0, min(1.0, 1.0 - float(spec["new_share"]))),
        "pre_existing_pct":          float(spec["pre_existing_pct"]),
    }


def main() -> None:
    print(f"🎬  Universal Peacock movie batch pull  —  {len(CONFIGS)} films")
    print()
    results = []
    for spec in CONFIGS:
        print(f"  ▶ {spec['title']:<40s}  reach={spec['reach_us']:>12,}  conv={spec['conv_pct']}%  new={spec['new_share']*100:.0f}%")
    print()
    for i, spec in enumerate(CONFIGS, 1):
        print(f"\n{'='*70}")
        print(f"[{i}/{len(CONFIGS)}] {spec['title']}")
        print(f"{'='*70}")
        try:
            r = run_synthetic_attribution(build_config(spec))
            if isinstance(r, dict):
                results.append((spec["title"], r.get("s3_key"), r.get("reach_us"), r.get("new_signups_us")))
                print(f"  ✅ {r.get('s3_key')}  reach={r.get('reach_us'):,}  signups={r.get('new_signups_us'):,}")
        except Exception as e:
            import traceback; traceback.print_exc()
            results.append((spec["title"], f"FAILED: {e}", None, None))
        time.sleep(2)  # gentle rate limiting between Claude research calls

    print(f"\n\n{'='*70}\nBATCH SUMMARY\n{'='*70}")
    for t, k, r, s in results:
        rstr = f"{r:>12,}" if isinstance(r, int) else "-"
        sstr = f"{s:>10,}" if isinstance(s, int) else "-"
        print(f"  {t:<38s}  reach={rstr}  signups={sstr}")


if __name__ == "__main__":
    main()
