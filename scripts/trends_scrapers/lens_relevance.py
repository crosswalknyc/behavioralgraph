"""
Persona-lens relevance scorer.

For every visible item on the Trends IQ dashboard (podcasts / songs /
streaming titles / books / films / social posts / headlines / trending
people / trending searches), ask Claude Sonnet to score how relevant
that item is to each configured audience lens on a 0-100 scale.

The dashboard user picks a lens from a dropdown and the frontend
instantly filters every card to just the rows the persona would
actually be interested in.

Six lenses ship today:

  - ms_now_reader             : the MS NOW (formerly MSNBC) reader.
                                College-educated, urban / inner-suburban,
                                Democratic-leaning, skews 55+.
  - unlikely_collaborators_follower : the wellness / consciousness /
                                Elizabeth Gilbert follower cohort.
                                JSON-authored.
  - gen_z                     : US adults 18-28. TikTok-first,
                                identity-forward, BookTok, K-pop
                                fluent, boutique-fitness curious.
  - millennials               : US adults 29-44. Nostalgia-and-
                                mortgage decade, prestige-TV loyal,
                                heavy podcast reader.
  - gen_x                     : US adults 45-60. Peak-earning-plus-
                                peak-caregiving, cable still on,
                                classic-rock + Nashville-country,
                                F1 and college football.
  - baby_boomers              : US adults 61-79. Retirement-and-
                                legacy decade, cable-news anchor
                                loyalist, cruise-and-Viking-River
                                traveler, Costco and QVC shopper.

Output shape (kind='meta'):

    {
      "source":     "lens_scores",
      "kind":       "meta",
      "fetched_at": "...",
      "generated_at": "...",
      "lenses": [
        {"id": "ms_now_reader",
         "label": "MS NOW Reader",
         "emoji": "\U0001F4FA",
         "description": "..."},
        {"id": "millennials",
         "label": "Millennials (Ages 27-42)",
         "emoji": "\u2615",
         "description": "..."}
      ],
      "items": {
        "podcast:pod save america": {
          "kind":  "podcast",
          "title": "Pod Save America",
          "scores": {"ms_now_reader": 92, "millennials": 68}
        },
        ...
      },
      "count": 340
    }

Standalone:

    python3 -m scripts.trends_scrapers.lens_relevance
    python3 -m scripts.trends_scrapers.lens_relevance --only podcast
    python3 -m scripts.trends_scrapers.lens_relevance --dry-run
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import boto3

logger = logging.getLogger(__name__)


_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/'


_CLAUDE_MODEL = (os.environ.get('LENS_RELEVANCE_MODEL')
                  or os.environ.get('WEBSEARCH_MODEL')
                  or 'claude-sonnet-4-5')
_CONCURRENCY  = int(os.environ.get('LENS_RELEVANCE_CONCURRENCY') or '4')
_BATCH_SIZE   = int(os.environ.get('LENS_RELEVANCE_BATCH_SIZE')  or '25')
_TIMEOUT_S    = int(os.environ.get('LENS_RELEVANCE_TIMEOUT_S')   or '120')


# ---------------------------------------------------------------------------
# Text normalization - mirrors stream_estimates + trends_iq so a
# `podcast:crime junkie` key here matches the same key the dashboard
# builds when it renders a Crime Junkie row.
# ---------------------------------------------------------------------------
_STOPWORDS = {'the', 'a', 'an', 'and', 'or', 'of', 'in', 'on', 'to',
               'for', 'with', 'at', 'by', 'from', 'as', 'is', 'are',
               'was', 'were', 'be', 'been', 'being', 'this', 'that',
               'these', 'those'}


def _norm(text: str) -> str:
    if not text:
        return ''
    s = text.lower().lstrip('#').strip()
    s = re.sub(r'[^\w\s]+', ' ', s)
    return ' '.join(t for t in s.split() if t and t not in _STOPWORDS)


def _key(kind: str, title: str, artist: str = '') -> str:
    """Lookup key: `<kind>:<normalized title[+artist]>`. Must match the
    frontend's `_tiqLensKey()` byte-for-byte so filtering works."""
    if kind in ('song', 'book'):
        return f'{kind}:{_norm(f"{title} {artist}")}'
    return f'{kind}:{_norm(title)}'


# ---------------------------------------------------------------------------
# Lens definitions - the persona descriptions Claude reasons against.
# Each description must be rich enough that Claude can decide "would this
# specific persona click on / consume / engage with this specific item?"
# for any of the ~500 items the dashboard surfaces.
# ---------------------------------------------------------------------------
_LENSES: list[dict[str, Any]] = [
    {
        'id':          'ms_now_reader',
        'label':       'MS NOW Reader',
        'emoji':       '\U0001F4FA',                       # 📺
        'description': ('Politics-forward center-left audience; core '
                         'MS NOW (formerly MSNBC) viewer.'),
        # Persona text is written kind-by-kind so Claude has a clear
        # per-surface rubric.  Every kind that shows up on the
        # dashboard (headlines / podcasts / songs / books / films /
        # search / social / person) has at least one HIGH, MID, and
        # LOW named example so Claude never has to guess "what would
        # this cohort listen to?" or "what film would they watch?".
        'persona': (
            "MS NOW (formerly MSNBC) reader / viewer.\n"
            "DEMOGRAPHIC: US adults skewing 55+, college-educated, "
            "urban / inner-suburban, ~85% Democratic-leaning, ~65% "
            "female, higher-income (median ~$85K HHI), heavy news "
            "consumers, NPR / PBS pledgers, Sunday NYT subscribers.\n"
            "\n"
            "IDENTITY: they view themselves as informed, empathetic, "
            "and defenders of institutions.  Consumption is oriented "
            "around news, ideas, and culturally-serious "
            "entertainment.  They still watch cable + linear TV.\n"
            "\n"
            "===================================================\n"
            "SCORING BY KIND (use the full 0-100 range)\n"
            "===================================================\n"
            "\n"
            "HEADLINES\n"
            "  HIGH (85-100): Trump-administration accountability, "
            "DOJ/FBI, Congressional hearings, Supreme Court "
            "decisions, foreign policy (Ukraine, Israel/Gaza, "
            "China), climate policy, voting rights, DEI-erasure, "
            "reproductive rights, Democratic strategy, big-tech "
            "antitrust, philanthropy accountability.\n"
            "  MID (50-70): business coverage IF politically-charged "
            "(Musk, Zuckerberg, banking crisis, OPEC), health-policy "
            "stories, culturally-political entertainment coverage.\n"
            "  LOW (5-25): pure market moves, individual company "
            "earnings without political angle, celebrity gossip, "
            "sports scores, tech product reviews without policy hook.\n"
            "\n"
            "PODCASTS\n"
            "  HIGH (85-100): The Rachel Maddow Show, Pod Save "
            "America, The Bulwark Daily, The Ezra Klein Show, The "
            "Daily (NYT), Up First (NPR), Deadline White House, "
            "Amicus (Slate), Prosecuting Donald Trump, The New "
            "Yorker Radio Hour, Fresh Air, The Weekly Show with Jon "
            "Stewart, Democracy Now.\n"
            "  MID (45-65): Radiolab, This American Life, Serial, "
            "Reveal, 60 Minutes, prestige-narrative shows.\n"
            "  LOW (5-20): Joe Rogan, Ben Shapiro, Tucker Carlson, "
            "Charlie Kirk, Matt Walsh, Candace Owens, Fearless with "
            "Jason Whitlock, most sports-talk (Bill Simmons, Pat "
            "McAfee), most true-crime, most gaming / anime "
            "podcasts.\n"
            "\n"
            "SONGS - MS NOW readers DO listen to music, so DO NOT "
            "cap songs artificially low.  Their consumption skews "
            "singer-songwriter / classic-rock canon / Americana / "
            "'NPR Tiny Desk' territory.\n"
            "  HIGH (75-95): Joni Mitchell, James Taylor, Carole "
            "King, Paul Simon, Fleetwood Mac, Bruce Springsteen, "
            "Bonnie Raitt, Bob Dylan, Van Morrison, Neil Young, "
            "Norah Jones, Brandi Carlile, Alison Krauss, Chris "
            "Stapleton, Adele, John Prine, Emmylou Harris, Bon Iver, "
            "Sufjan Stevens, Sara Bareilles.\n"
            "  MID (45-65): mainstream rock canon (Journey, Tears "
            "for Fears, U2, Elton John, Billy Joel), Adele, "
            "Kacey Musgraves.\n"
            "  LOW (5-25): current pop/hip-hop chart hits (Post "
            "Malone, Doja Cat, Bad Bunny), K-pop, viral TikTok "
            "sounds, EDM/DJ mixes, Morgan Wallen (a bit lower - "
            "some MS NOW readers do NOT like his politics), Latin-"
            "language reggaeton, most Spanish-language chart music.\n"
            "\n"
            "BOOKS\n"
            "  HIGH (85-100): Trump-era accountability journalism "
            "(Maggie Haberman, Bob Woodward, Michael Wolff, "
            "Ronan Farrow), Patrick Radden Keefe, prestige "
            "literary fiction (Ann Patchett, Elizabeth Strout, "
            "George Saunders, Colson Whitehead), New Yorker / "
            "Atlantic / NYRB compendiums, biographies of "
            "presidents / justices / activists, climate books.\n"
            "  MID (45-65): general literary fiction, memoirs, "
            "prestige nonfiction.\n"
            "  LOW (5-20): BookTok romantasy (Sarah J. Maas, "
            "Rebecca Yarros, Colleen Hoover), YA fantasy, cozy "
            "mysteries, sports biographies, self-help / "
            "productivity, evangelical / conservative-imprint "
            "(Regnery, Dinesh D'Souza), niche fandom / gaming "
            "novelizations.\n"
            "\n"
            "FILMS / TV\n"
            "  HIGH (80-95): prestige drama (Succession, The "
            "Diplomat, Slow Horses, The Morning Show, The Crown), "
            "docs and biopics of political / artistic figures, "
            "Oscar-bait indie, historical drama, PBS Frontline / "
            "Ken Burns, Handmaid's Tale, All The Light We Cannot "
            "See, Oppenheimer.\n"
            "  MID (45-65): high-quality genre with cultural weight "
            "(The Last of Us, Yellowjackets, House of Cards, mid "
            "Christopher Nolan).\n"
            "  LOW (5-25): superhero tentpoles, YA fantasy "
            "adaptations, reality dating (Love Island / The "
            "Bachelor / 90 Day Fiancé), horror franchises, "
            "kids/family animation, most action franchises.\n"
            "\n"
            "SEARCHES\n"
            "  HIGH (75-95): politicians (AOC, Bernie, Kamala, "
            "Trump), justice/legal terms (indictment, subpoena, "
            "opinion, precedent), foreign-policy hotspots, climate "
            "events (heatwave, flood, wildfire, IPCC), Supreme "
            "Court cases, election terms.\n"
            "  MID (40-65): business-adjacent politics (Musk, "
            "Zuckerberg), health-policy terms.\n"
            "  LOW (5-25): pop-culture beefs, sports box scores, "
            "streamer names, meme stocks, celebrity-couple gossip, "
            "Spanish-language sports queries, WWE / UFC results, "
            "K-pop groups.\n"
            "\n"
            "PEOPLE (trending)\n"
            "  HIGH (80-95): Democratic politicians, progressive "
            "activists, prestige journalists, SCOTUS justices, "
            "senior admin officials, foreign leaders in the news, "
            "Nobel laureates.\n"
            "  MID (40-65): major cultural figures with political "
            "weight (Bruce Springsteen, Meryl Streep, Bill Gates).\n"
            "  LOW (5-25): TikTok influencers, sports stars, "
            "reality-TV cast, K-pop idols, gaming streamers.\n"
            "\n"
            "SOCIAL (Reddit / TikTok / YouTube posts)\n"
            "  HIGH (70-90): posts about politics, breaking news, "
            "SCOTUS, elections, climate.\n"
            "  MID (40-60): general 'interesting news' posts, "
            "clever observational humor.\n"
            "  LOW (5-25): fandom posts, gaming clips, K-pop, "
            "fitness / diet / hustle content, MLM content."
        ),
    },
    # NOTE: the inline `millennials` block was retired 2026-09-02.
    # The full generational stack (gen_z / millennials / gen_x /
    # baby_boomers) is now authored in data/personas/*.json alongside
    # `unlikely_collaborators_follower` so operators can edit each
    # persona brief without touching Python.  See _JSON_PERSONA_FILES
    # below.  The `_retired_inline_millennials` placeholder that
    # follows is a KEEP-OUT: `_LENSES.extend(...)` filters it out
    # via `id.startswith('_retired')` before returning so it never
    # appears in the LENS dropdown, but the persona text is preserved
    # as historical reference for the JSON author.
    {
        'id':          '_retired_inline_millennials',
        'label':       'Millennials (Ages 27-42) [retired inline]',
        'emoji':       '\u2615',                            # ☕
        'description': ('Retired inline copy.  See data/personas/'
                         'millennials.json for the live brief.'),
        'persona': (
            "Millennial audience, ages 27-42 as of 2026 (born "
            "1981-1996).\n"
            "DEMOGRAPHIC: US adults, roughly 50/50 gender, 60% "
            "suburban / 30% urban / 10% rural, ~70% college-"
            "attended, median HHI ~$75K, ~55% married, ~40% have "
            "kids under 12.\n"
            "\n"
            "IDENTITY: nostalgia-forward, career-and-mortgage-"
            "anxious, high podcast consumers, wellness-attuned, "
            "'main character energy' online.  Skeptical of cable "
            "news, receptive to prestige TV, deeply engaged with "
            "fandom (Marvel/Star Wars/HOTD/Taylor Swift).\n"
            "\n"
            "===================================================\n"
            "SCORING BY KIND (use the full 0-100 range)\n"
            "===================================================\n"
            "\n"
            "HEADLINES\n"
            "  HIGH (80-95): personal-finance (student loans, "
            "mortgage rates, 401k, index funds, side hustles), "
            "tech-product reviews (iPhone, Pixel, Whoop, Oura, "
            "Peloton), wellness / mental-health, parenting "
            "anxiety (screen time, IVF, daycare costs, Bluey), "
            "AI-productivity stories, DTC brand news, celebrity "
            "scandal / relationship news, TV-recap-worthy shows.\n"
            "  MID (45-65): general business coverage, national "
            "news events, tech-industry moves without a personal "
            "hook.\n"
            "  LOW (5-25): pure Congressional-procedure stories, "
            "AARP / retirement / senior-focused, dense foreign-"
            "policy analysis, philanthropy-sector inside-baseball.\n"
            "\n"
            "PODCASTS\n"
            "  HIGH (85-100): SmartLess, Armchair Expert with "
            "Dax Shepard, Call Her Daddy, My Favorite Murder, "
            "Crime Junkie, Serial, The Daily, Office Ladies, "
            "Morbid, Anna Faris Is Unqualified, Huberman Lab, "
            "How I Built This, Diary of a CEO, Ten Percent "
            "Happier, The Ringer, Bill Simmons.\n"
            "  MID (45-65): NPR journalism podcasts, prestige-"
            "narrative shows, general true-crime.\n"
            "  LOW (5-20): The Rachel Maddow Show (millennials "
            "don't listen to cable), Tucker Carlson, Charlie "
            "Kirk, Candace Owens, most religious podcasts, "
            "boomer-oriented sports-talk (Colin Cowherd), "
            "specialized-hobby podcasts (Vortex Optics, "
            "Dogman Encounters).\n"
            "\n"
            "SONGS\n"
            "  HIGH (85-100): Taylor Swift (all eras), Olivia "
            "Rodrigo, Sabrina Carpenter, Chappell Roan, Beyoncé, "
            "Post Malone, Morgan Wallen, Zach Bryan, Kendrick "
            "Lamar, Drake, Doja Cat, Ariana Grande, Bad Bunny, "
            "Billie Eilish, SZA, Dua Lipa, plus 2000s / 2010s "
            "nostalgia (Fall Out Boy, Paramore, Kanye pre-2018, "
            "One Direction, Rihanna, Katy Perry, Backstreet Boys "
            "throwbacks).\n"
            "  MID (50-70): current adult-alt (Hozier, Noah "
            "Kahan, Phoebe Bridgers), classic-rock crossovers "
            "(Fleetwood Mac renaissance).\n"
            "  LOW (5-25): pre-70s / adult-standards, Latin-"
            "language regional-Mexican unless viral (banda / "
            "corridos low unless it's Peso Pluma / Fuerza "
            "Regida), niche K-pop B-sides, most classical / "
            "opera, most Christian contemporary.\n"
            "\n"
            "BOOKS\n"
            "  HIGH (85-100): BookTok romantasy (Sarah J. Maas, "
            "Rebecca Yarros / Fourth Wing / Iron Flame / Onyx "
            "Storm, Colleen Hoover / Verity, Emily Henry, Taylor "
            "Jenkins Reid / Daisy Jones), self-help + finance "
            "(Atomic Habits, Psychology of Money, Ramit Sethi), "
            "prestige nonfiction (Educated, Bad Blood, Braiding "
            "Sweetgrass), memoirs of comedians and pop-culture "
            "figures.\n"
            "  MID (45-65): general literary fiction, prestige "
            "biography, sci-fi crossover (Project Hail Mary), "
            "productivity nonfiction.\n"
            "  LOW (5-25): conservative-imprint political books, "
            "evangelical / prosperity gospel, older cozy mystery "
            "series, most in-hobby niche (Dungeon Crawler Carl "
            "series unless part of Millennial-gamer aesthetic - "
            "in which case MID), boomer memoirs.\n"
            "\n"
            "FILMS / TV\n"
            "  HIGH (85-100): franchise / nostalgia (Marvel, Star "
            "Wars, HOTD / GOT, Harry Potter, LOTR / Rings of "
            "Power, Barbie, Oppenheimer, Cobra Kai, Stranger "
            "Things, Fallout), prestige TV (The Bear, Ted Lasso, "
            "Only Murders in the Building, Yellowjackets, "
            "Succession, Severance, Beef, White Lotus, Bridgerton, "
            "The Last of Us), animated-for-adults, Bluey (parent-"
            "millennials).\n"
            "  MID (45-65): general Netflix rom-com, indie "
            "drama, mid-tier action.\n"
            "  LOW (5-25): PBS Frontline / Ken Burns docs (skew "
            "older), classic Hollywood catalog (unless "
            "nostalgia-flavor 90s), kids animation without "
            "cross-appeal.\n"
            "\n"
            "SEARCHES\n"
            "  HIGH (80-95): reboots / franchises (Star Wars "
            "canceled, HOTD, Marvel), Taylor Swift terms, sports "
            "AT the fandom level (Aces vs Liberty for female "
            "millennials, Chase Briscoe / F1 / NFL for male), "
            "tech-product reviews (Pixel, Whoop, Peloton), "
            "parenting terms (IVF, daycare, Bluey), personal-"
            "finance terms (student loan forgiveness, mortgage "
            "rates, index fund).\n"
            "  MID (40-65): general news terms, wellness (diet, "
            "protein, gut health).\n"
            "  LOW (5-25): SCOTUS docket terms, Congressional-"
            "procedure terms, senior-focused terms (Medicare "
            "supplement), foreign-language sports-league terms "
            "unless the person follows Liga MX, obscure political "
            "figures.\n"
            "\n"
            "PEOPLE (trending)\n"
            "  HIGH (80-95): actors from prestige TV, pop-star "
            "principals (Taylor Swift, Sabrina Carpenter, "
            "Beyoncé), Marvel / Star Wars leads, athletes with "
            "cultural crossover (Travis Kelce, Caitlin Clark), "
            "podcast hosts they listen to, viral influencers.\n"
            "  MID (40-65): major athletes, mid-tier actors, "
            "authors of BookTok titles.\n"
            "  LOW (5-25): cable-news anchors, senior politicians "
            "(unless meme-tier), boomer icons, foreign politicians "
            "without US-cultural crossover, hyper-local figures.\n"
            "\n"
            "SOCIAL (Reddit / TikTok / YouTube posts)\n"
            "  HIGH (75-95): pop-culture / fandom posts, "
            "wellness / life-hack content, career and money "
            "posts, Bluey / parenting humor, gaming clips.\n"
            "  MID (40-60): general observational humor, "
            "news commentary.\n"
            "  LOW (5-25): partisan cable-news style content, "
            "religious / evangelical posts, boomer-topic posts."
        ),
    },
]


# ---------------------------------------------------------------------------
# JSON-authored personas (bg-webapp/data/personas/*.json)
#
# Some personas are rich enough that the operator wants to review the
# brief as its own artifact, not just a Python string.  For those, the
# JSON file is the source of truth: this loader reads the file and
# builds the same shape of {id, label, emoji, description, persona}
# entry the inline _LENSES dicts above use.  The persona prompt string
# is composed section-by-section from the JSON (one_line +
# demographics + psychographics + consumption_signals + themes +
# filter_rules_for_agent) with headers Claude reads naturally.
#
# To add another JSON-authored persona: drop a new file in
# data/personas/, add its stem to _JSON_PERSONA_FILES, and add
# calibration anchors in the _ANCHORS dict below keyed by lens_id.
# The frontend LENS dropdown picks the new entry up automatically
# from the daily scraper's lens_scores.json.
# ---------------------------------------------------------------------------
_JSON_PERSONA_FILES: list[str] = [
    'unlikely_collaborators_follower',
    'gen_z',
    'millennials',
    'gen_x',
    'baby_boomers',
]

# Data dir sits at repo-root / bg-webapp / data / personas.  The
# scraper runs from the bg-webapp root on both local dev and Hetzner,
# so a path anchored to __file__ resolves in both.
import pathlib as _pathlib  # noqa: E402
_PERSONA_DIR = (_pathlib.Path(__file__).resolve().parent.parent.parent
                / 'data' / 'personas')


def _bullet_lines(prefix: str, items: list) -> list[str]:
    """Render a list of strings as a fixed-width bullet block."""
    out: list[str] = []
    for it in (items or []):
        s = str(it).strip()
        if not s:
            continue
        out.append(f"{prefix}- {s}")
    return out


def _compose_persona_prompt(doc: dict) -> str:
    """Turn a JSON persona doc into the persona-prompt string Claude
    reads at scoring time.  Mirrors the shape of the inline
    ms_now_reader / millennials prompts so the batch prompt template
    stays consistent.  Every section that appears in the JSON gets a
    labeled block; missing keys are simply omitted."""
    demo   = doc.get('demographics')       or {}
    psych  = doc.get('psychographics')     or {}
    cons   = doc.get('consumption_signals') or {}
    care   = doc.get('themes_they_care_about') or []
    avoid  = doc.get('themes_they_avoid')      or []
    rules  = doc.get('filter_rules_for_agent') or []

    lines: list[str] = []
    label = doc.get('display_name') or doc.get('lens_id') or 'AUDIENCE'
    lines.append(f"{label}.")
    if doc.get('one_line'):
        lines.append(str(doc['one_line']))
    lines.append('')

    if demo:
        lines.append('DEMOGRAPHIC:')
        for k, v in demo.items():
            if isinstance(v, list):
                lines.append(f"  {k}: {', '.join(str(x) for x in v)}")
            else:
                lines.append(f"  {k}: {v}")
        lines.append('')

    if psych:
        lines.append('IDENTITY / TASTE:')
        if psych.get('values'):
            lines += _bullet_lines('  values ', psych['values'])
        if psych.get('aesthetics'):
            lines += _bullet_lines('  aesthetics ', psych['aesthetics'])
        if psych.get('tone_they_respond_to'):
            lines.append(f"  tone they respond to: {psych['tone_they_respond_to']}")
        if psych.get('tone_they_reject'):
            lines.append(f"  tone they reject:     {psych['tone_they_reject']}")
        lines.append('')

    if cons:
        lines.append('CONSUMPTION SIGNALS (observed in this profile):')
        for section, val in cons.items():
            if isinstance(val, dict):
                lines.append(f"  {section}:")
                for sk, sv in val.items():
                    if isinstance(sv, list):
                        lines.append(f"    {sk}: {', '.join(str(x) for x in sv[:20])}")
                    else:
                        lines.append(f"    {sk}: {sv}")
            elif isinstance(val, list):
                lines.append(f"  {section}: {', '.join(str(x) for x in val[:20])}")
            else:
                lines.append(f"  {section}: {val}")
        lines.append('')

    if care:
        lines.append('THEMES THEY CARE ABOUT:')
        lines += _bullet_lines('  ', care)
        lines.append('')

    if avoid:
        lines.append('THEMES THEY AVOID:')
        lines += _bullet_lines('  ', avoid)
        lines.append('')

    if rules:
        lines.append('=========================================================')
        lines.append('SCORING RULES (apply to every item in the batch)')
        lines.append('=========================================================')
        lines.append('Use the FULL 0-100 range. HIGH (75-95) for items that '
                     'directly land on a KEEP rule below.  LOW (5-25) for '
                     'items that land on a DROP rule.  MID (35-55) for items '
                     'that are plausible but not core.')
        lines.append('')
        lines += _bullet_lines('  ', rules)

    return '\n'.join(lines)


def _load_json_lenses() -> list[dict[str, Any]]:
    """Read every JSON persona file in _JSON_PERSONA_FILES and return
    the shape _LENSES expects.  Missing files log a warning and the
    lens is simply skipped so the scraper never crashes on a bad
    disk state - the frontend's response to a missing lens id is to
    hide the option, which is the correct degraded behavior."""
    out: list[dict[str, Any]] = []
    for stem in _JSON_PERSONA_FILES:
        path = _PERSONA_DIR / f'{stem}.json'
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            logger.warning("lens_relevance: persona doc missing %s", path)
            continue
        except Exception as e:
            logger.warning("lens_relevance: persona doc %s: %s", path, e)
            continue
        lens_id = doc.get('lens_id') or stem
        out.append({
            'id':          lens_id,
            'label':       doc.get('display_name') or lens_id,
            'emoji':       doc.get('emoji') or '\U0001F9ED',
            'description': (doc.get('one_line') or '')[:200],
            'persona':     _compose_persona_prompt(doc),
        })
    return out


# Filter out any inline entries whose id starts with `_retired_`
# (see the `_retired_inline_millennials` KEEP-OUT above) before we
# extend with the JSON-authored set.  This keeps the dropdown clean
# without deleting the inline persona-text history from source.
_LENSES = [l for l in _LENSES if not str(l.get('id', '')).startswith('_retired_')]

# Append JSON-authored personas to the inline set.  Order matters
# only for the LENS dropdown ordering on the frontend (which mirrors
# the order lens_config lands in the payload).  Current order:
# ms_now_reader, unlikely_collaborators_follower, gen_z, millennials,
# gen_x, baby_boomers.
_LENSES.extend(_load_json_lenses())


# ---------------------------------------------------------------------------
# Anchor items - concrete calibration examples pinned at the top of
# every batch prompt.  Claude scores these first (visible to itself as
# already-decided) so it can peg the rest of the batch against a known
# scale, instead of inventing a new distribution per batch.  Choose
# spread anchors: one at 90+, a mid-tier, a low-tier, per kind that
# appears in the batch.
# ---------------------------------------------------------------------------
_ANCHORS: dict[str, list[dict[str, Any]]] = {
    'ms_now_reader': [
        {'kind': 'podcast',  'title': 'The Rachel Maddow Show',       'score': 98},
        {'kind': 'podcast',  'title': 'Pod Save America',              'score': 92},
        {'kind': 'headline', 'title': 'Trump DOJ moves to dismiss ...','score': 88},
        {'kind': 'book',     'title': 'Regime Change (Haberman)',      'score': 95},
        {'kind': 'song',     'title': 'The River (Bruce Springsteen)', 'score': 82},
        {'kind': 'song',     'title': 'Landslide (Fleetwood Mac)',     'score': 78},
        {'kind': 'song',     'title': 'Not Like Us (Kendrick Lamar)',  'score': 22},
        {'kind': 'film',     'title': 'Oppenheimer',                   'score': 82},
        {'kind': 'film',     'title': 'Fast & Furious 12',             'score': 12},
        {'kind': 'search',   'title': 'kamala harris',                 'score': 92},
        {'kind': 'search',   'title': 'aces vs liberty',               'score': 12},
        {'kind': 'podcast',  'title': 'The Tucker Carlson Show',       'score': 5},
    ],
    'millennials': [
        {'kind': 'podcast',  'title': 'SmartLess',                     'score': 95},
        {'kind': 'podcast',  'title': 'My Favorite Murder',            'score': 90},
        {'kind': 'podcast',  'title': 'The Rachel Maddow Show',        'score': 18},
        {'kind': 'song',     'title': 'The Fate of Ophelia (Taylor Swift)', 'score': 95},
        {'kind': 'song',     'title': 'I Had Some Help (Post Malone / Morgan Wallen)', 'score': 92},
        {'kind': 'song',     'title': 'Landslide (Fleetwood Mac)',     'score': 55},
        {'kind': 'book',     'title': 'Fourth Wing (Rebecca Yarros)',  'score': 95},
        {'kind': 'book',     'title': 'Atomic Habits (James Clear)',   'score': 92},
        {'kind': 'book',     'title': 'Regime Change (Haberman)',      'score': 32},
        {'kind': 'film',     'title': 'House of the Dragon',           'score': 92},
        {'kind': 'film',     'title': 'PBS Frontline: Ukraine',        'score': 18},
        {'kind': 'search',   'title': 'house of the dragon season 4',  'score': 92},
        {'kind': 'search',   'title': 'kamala harris',                 'score': 30},
    ],
    # Unlikely Collaborators Follower - young, queer-inclusive,
    # wellness-and-consciousness-forward.  Anchors deliberately span
    # the range: personal-growth podcasts and prestige indie score
    # very high; hustle-culture and cable-news score very low; pop
    # anchors (Taylor Swift, Sabrina Carpenter) sit high but not
    # 100 because this follower's music taste is broader (Latin,
    # hip-hop, audiophile) than a straight-Swift-core lens.
    'unlikely_collaborators_follower': [
        {'kind': 'podcast',  'title': 'On Purpose with Jay Shetty',            'score': 96},
        {'kind': 'podcast',  'title': 'The Mel Robbins Podcast',               'score': 95},
        {'kind': 'podcast',  'title': 'Armchair Expert with Dax Shepard',      'score': 92},
        {'kind': 'podcast',  'title': 'Unlocking Us with Brene Brown',         'score': 94},
        {'kind': 'podcast',  'title': 'We Can Do Hard Things',                 'score': 92},
        {'kind': 'podcast',  'title': 'Ten Percent Happier',                   'score': 88},
        {'kind': 'podcast',  'title': 'The Joe Rogan Experience',              'score': 12},
        {'kind': 'podcast',  'title': 'The Tucker Carlson Show',               'score': 5},
        {'kind': 'song',     'title': 'The Fate of Ophelia (Taylor Swift)',    'score': 88},
        {'kind': 'song',     'title': 'Espresso (Sabrina Carpenter)',          'score': 86},
        {'kind': 'song',     'title': 'Good Luck Babe! (Chappell Roan)',       'score': 92},
        {'kind': 'song',     'title': 'BIRDS OF A FEATHER (Billie Eilish)',    'score': 88},
        {'kind': 'song',     'title': 'Not Like Us (Kendrick Lamar)',          'score': 72},
        {'kind': 'song',     'title': 'God\u2019s Country (Blake Shelton)',    'score': 22},
        {'kind': 'book',     'title': 'All The Way To The River (Elizabeth Gilbert)', 'score': 98},
        {'kind': 'book',     'title': 'Big Magic (Elizabeth Gilbert)',         'score': 96},
        {'kind': 'book',     'title': 'Atlas of the Heart (Brene Brown)',      'score': 94},
        {'kind': 'book',     'title': 'Fourth Wing (Rebecca Yarros)',          'score': 82},
        {'kind': 'book',     'title': 'Atomic Habits (James Clear)',           'score': 68},
        {'kind': 'book',     'title': 'Rich Dad Poor Dad',                     'score': 15},
        {'kind': 'book',     'title': 'The Art of the Deal (Trump)',           'score': 5},
        {'kind': 'film',     'title': 'The Bear',                              'score': 92},
        {'kind': 'film',     'title': 'Past Lives',                            'score': 94},
        {'kind': 'film',     'title': 'Everything Everywhere All At Once',     'score': 93},
        {'kind': 'film',     'title': 'Barbie',                                'score': 92},
        {'kind': 'film',     'title': 'Oppenheimer',                           'score': 85},
        {'kind': 'film',     'title': 'Andor',                                 'score': 90},
        {'kind': 'film',     'title': 'The Bachelorette',                      'score': 30},
        {'kind': 'film',     'title': 'Fast & Furious 12',                     'score': 20},
        {'kind': 'headline', 'title': 'Elizabeth Gilbert launches new Perception Box workshop', 'score': 96},
        {'kind': 'headline', 'title': 'Supreme Court hears LGBTQ+ workplace case', 'score': 88},
        {'kind': 'headline', 'title': 'Federal Reserve leaves rates unchanged', 'score': 25},
        {'kind': 'headline', 'title': 'Nvidia earnings beat analyst estimates', 'score': 18},
        {'kind': 'search',   'title': 'jay shetty new podcast',                'score': 92},
        {'kind': 'search',   'title': 'headspace app free trial',              'score': 88},
        {'kind': 'search',   'title': 'best pilates studios near me',          'score': 86},
        {'kind': 'search',   'title': 'medicare supplement plans',             'score': 8},
        {'kind': 'search',   'title': 'nfl draft 2026',                        'score': 30},
        {'kind': 'person',   'title': 'Elizabeth Gilbert',                     'score': 98},
        {'kind': 'person',   'title': 'Brene Brown',                           'score': 94},
        {'kind': 'person',   'title': 'Jay Shetty',                            'score': 92},
        {'kind': 'person',   'title': 'Cillian Murphy',                        'score': 82},
        {'kind': 'person',   'title': 'Tucker Carlson',                        'score': 5},
        {'kind': 'person',   'title': 'Andrew Tate',                           'score': 3},
    ],

    # -----------------------------------------------------------------
    # Gen Z (US adults 18-28) - phone-native, TikTok-first, identity-
    # forward, BookTok, K-pop and Latin-crossover fluent, boutique-
    # fitness curious, wellness-app obsessed.  Anchors deliberately
    # span the range: youth-culture and identity items score high;
    # cable-news anchor drama, cruise travel, Medicare, and boomer-
    # coded talent all score low.
    # -----------------------------------------------------------------
    'gen_z': [
        {'kind': 'podcast',  'title': 'Call Her Daddy',                        'score': 94},
        {'kind': 'podcast',  'title': 'Anything Goes with Emma Chamberlain',   'score': 95},
        {'kind': 'podcast',  'title': 'Rotten Mango',                          'score': 88},
        {'kind': 'podcast',  'title': 'Distractible (Markiplier)',             'score': 82},
        {'kind': 'podcast',  'title': 'The Daily',                             'score': 32},
        {'kind': 'podcast',  'title': 'The Ben Shapiro Show',                  'score': 6},
        {'kind': 'podcast',  'title': '60 Minutes',                            'score': 10},
        {'kind': 'song',     'title': 'Good Luck Babe! (Chappell Roan)',       'score': 95},
        {'kind': 'song',     'title': 'Espresso (Sabrina Carpenter)',          'score': 94},
        {'kind': 'song',     'title': 'Not Like Us (Kendrick Lamar)',          'score': 90},
        {'kind': 'song',     'title': 'La Diabla (Peso Pluma)',                'score': 86},
        {'kind': 'song',     'title': 'God\u2019s Country (Blake Shelton)',    'score': 10},
        {'kind': 'song',     'title': 'Fly Me to the Moon (Frank Sinatra)',    'score': 12},
        {'kind': 'book',     'title': 'Fourth Wing (Rebecca Yarros)',          'score': 95},
        {'kind': 'book',     'title': 'A Court of Thorns and Roses (Sarah J. Maas)', 'score': 94},
        {'kind': 'book',     'title': 'Regime Change (Maggie Haberman)',       'score': 12},
        {'kind': 'book',     'title': 'Being Ready When The Luck Happens (Ina Garten)', 'score': 15},
        {'kind': 'film',     'title': 'Wednesday',                             'score': 94},
        {'kind': 'film',     'title': 'Euphoria',                              'score': 93},
        {'kind': 'film',     'title': 'Bottoms',                               'score': 90},
        {'kind': 'film',     'title': 'Barbie',                                'score': 90},
        {'kind': 'film',     'title': 'Yellowstone',                           'score': 18},
        {'kind': 'film',     'title': 'Downton Abbey: A New Era',              'score': 10},
        {'kind': 'search',   'title': 'chappell roan tour',                    'score': 92},
        {'kind': 'search',   'title': 'depop resellers',                       'score': 88},
        {'kind': 'search',   'title': 'medicare supplement plans',             'score': 5},
        {'kind': 'search',   'title': 'edward jones near me',                  'score': 8},
        {'kind': 'search',   'title': 'viking river cruise',                   'score': 6},
        {'kind': 'person',   'title': 'Chappell Roan',                         'score': 96},
        {'kind': 'person',   'title': 'Alix Earle',                            'score': 94},
        {'kind': 'person',   'title': 'Kai Cenat',                             'score': 90},
        {'kind': 'person',   'title': 'Al Roker',                              'score': 10},
        {'kind': 'person',   'title': 'Barbara Corcoran',                      'score': 12},
    ],

    # -----------------------------------------------------------------
    # Gen X (US adults 45-60) - peak-earning-plus-peak-caregiving,
    # cable still on, classic-rock and Nashville-country, F1 and
    # college football, Costco and Trader Joe's, Fidelity and
    # Vanguard.  Anchors: prestige slower-burn TV and classic-rock
    # canon score high; hyper-Gen-Z youth-influencer and BookTok
    # romantasy score low.
    # -----------------------------------------------------------------
    'gen_x': [
        {'kind': 'podcast',  'title': 'The Daily',                             'score': 92},
        {'kind': 'podcast',  'title': 'The Bill Simmons Podcast',              'score': 90},
        {'kind': 'podcast',  'title': 'Fresh Air',                             'score': 90},
        {'kind': 'podcast',  'title': 'Pod Save America',                      'score': 82},
        {'kind': 'podcast',  'title': 'Huberman Lab',                          'score': 85},
        {'kind': 'podcast',  'title': 'Call Her Daddy',                        'score': 25},
        {'kind': 'podcast',  'title': 'Anything Goes with Emma Chamberlain',   'score': 15},
        {'kind': 'song',     'title': 'Stairway to Heaven (Led Zeppelin)',     'score': 95},
        {'kind': 'song',     'title': 'Everlong (Foo Fighters)',               'score': 92},
        {'kind': 'song',     'title': 'Tennessee Whiskey (Chris Stapleton)',   'score': 90},
        {'kind': 'song',     'title': 'Something in the Orange (Zach Bryan)',  'score': 88},
        {'kind': 'song',     'title': 'Espresso (Sabrina Carpenter)',          'score': 55},
        {'kind': 'song',     'title': 'Super Shy (NewJeans)',                  'score': 15},
        {'kind': 'song',     'title': 'La Diabla (Peso Pluma)',                'score': 30},
        {'kind': 'book',     'title': 'Empire of Pain (Patrick Radden Keefe)', 'score': 94},
        {'kind': 'book',     'title': 'A Time for Mercy (John Grisham)',       'score': 90},
        {'kind': 'book',     'title': 'Outlive (Peter Attia)',                 'score': 92},
        {'kind': 'book',     'title': 'Fourth Wing (Rebecca Yarros)',          'score': 28},
        {'kind': 'book',     'title': 'Twisted Love (Ana Huang)',              'score': 15},
        {'kind': 'film',     'title': 'Slow Horses',                           'score': 94},
        {'kind': 'film',     'title': 'The Diplomat',                          'score': 92},
        {'kind': 'film',     'title': 'Yellowstone',                           'score': 90},
        {'kind': 'film',     'title': 'The Bear',                              'score': 88},
        {'kind': 'film',     'title': 'Hazbin Hotel',                          'score': 8},
        {'kind': 'film',     'title': 'KPop Demon Hunters',                    'score': 12},
        {'kind': 'search',   'title': '401k rollover',                         'score': 90},
        {'kind': 'search',   'title': 'masters golf leaderboard',              'score': 88},
        {'kind': 'search',   'title': 'f1 miami grand prix',                   'score': 85},
        {'kind': 'search',   'title': 'kai cenat live stream',                 'score': 10},
        {'kind': 'search',   'title': 'livvy dunne',                           'score': 12},
        {'kind': 'person',   'title': 'Bruce Springsteen',                     'score': 92},
        {'kind': 'person',   'title': 'Nick Offerman',                         'score': 88},
        {'kind': 'person',   'title': 'Aubrey Plaza',                          'score': 85},
        {'kind': 'person',   'title': 'Salish Matter',                         'score': 5},
        {'kind': 'person',   'title': 'Kai Cenat',                             'score': 12},
    ],

    # -----------------------------------------------------------------
    # Baby Boomers (US adults 61-79) - retirement-and-legacy decade,
    # cable-news anchor loyalist, cruise-and-Viking-River traveler,
    # Costco and QVC shopper, AARP member, Fidelity and Edward Jones.
    # Anchors: prestige slower-burn TV, classic music, and cable-news
    # anchor drama score high; BookTok, K-pop, crypto trading, and
    # youth-influencer creator content all score low.
    # -----------------------------------------------------------------
    'baby_boomers': [
        {'kind': 'podcast',  'title': 'The Daily',                             'score': 92},
        {'kind': 'podcast',  'title': '60 Minutes',                            'score': 96},
        {'kind': 'podcast',  'title': 'Fresh Air',                             'score': 94},
        {'kind': 'podcast',  'title': 'The New Yorker Radio Hour',             'score': 90},
        {'kind': 'podcast',  'title': 'The Bill Simmons Podcast',              'score': 60},
        {'kind': 'podcast',  'title': 'Call Her Daddy',                        'score': 8},
        {'kind': 'podcast',  'title': 'Distractible (Markiplier)',             'score': 5},
        {'kind': 'song',     'title': 'Fly Me to the Moon (Frank Sinatra)',    'score': 95},
        {'kind': 'song',     'title': 'Stayin\u2019 Alive (Bee Gees)',         'score': 92},
        {'kind': 'song',     'title': 'Believe (Cher)',                        'score': 90},
        {'kind': 'song',     'title': 'Born to Run (Bruce Springsteen)',       'score': 92},
        {'kind': 'song',     'title': 'Good Luck Babe! (Chappell Roan)',       'score': 18},
        {'kind': 'song',     'title': 'La Diabla (Peso Pluma)',                'score': 10},
        {'kind': 'song',     'title': 'Super Shy (NewJeans)',                  'score': 5},
        {'kind': 'book',     'title': 'A Time for Mercy (John Grisham)',       'score': 92},
        {'kind': 'book',     'title': 'The Demon of Unrest (Erik Larson)',     'score': 94},
        {'kind': 'book',     'title': 'Being Ready When The Luck Happens (Ina Garten)', 'score': 90},
        {'kind': 'book',     'title': 'Fourth Wing (Rebecca Yarros)',          'score': 12},
        {'kind': 'book',     'title': 'It Ends With Us (Colleen Hoover)',      'score': 15},
        {'kind': 'film',     'title': 'Yellowstone',                           'score': 92},
        {'kind': 'film',     'title': 'Blue Bloods',                           'score': 90},
        {'kind': 'film',     'title': 'The Crown',                             'score': 92},
        {'kind': 'film',     'title': 'Downton Abbey: A New Era',              'score': 90},
        {'kind': 'film',     'title': 'Euphoria',                              'score': 8},
        {'kind': 'film',     'title': 'Wednesday',                             'score': 22},
        {'kind': 'film',     'title': 'Hazbin Hotel',                          'score': 5},
        {'kind': 'search',   'title': 'medicare advantage plans',              'score': 92},
        {'kind': 'search',   'title': 'viking river cruise',                   'score': 90},
        {'kind': 'search',   'title': 'edward jones near me',                  'score': 88},
        {'kind': 'search',   'title': 'kai cenat live stream',                 'score': 5},
        {'kind': 'search',   'title': 'depop resellers',                       'score': 8},
        {'kind': 'person',   'title': 'Ina Garten',                            'score': 94},
        {'kind': 'person',   'title': 'Julia Roberts',                         'score': 92},
        {'kind': 'person',   'title': 'Bruce Springsteen',                     'score': 90},
        {'kind': 'person',   'title': 'Barbara Corcoran',                      'score': 85},
        {'kind': 'person',   'title': 'Alix Earle',                            'score': 10},
        {'kind': 'person',   'title': 'Kai Cenat',                             'score': 5},
        {'kind': 'person',   'title': 'Livvy Dunne',                           'score': 8},
    ],
}


# ---------------------------------------------------------------------------
# Collect items from every dashboard snapshot on S3
# ---------------------------------------------------------------------------
_s3 = boto3.client('s3')


def _read(source: str) -> Optional[dict]:
    key = f'{_S3_LATEST}{source}.json'
    try:
        body = _s3.get_object(Bucket=_S3_BUCKET, Key=key)['Body'].read()
    except Exception as e:
        logger.info("lens_relevance: no snapshot %s (%s)", source, e)
        return None
    try:
        return json.loads(body)
    except Exception as e:
        logger.warning("lens_relevance: bad JSON %s (%s)", source, e)
        return None


def _collect_all_items() -> list[dict]:
    """Union of every renderable item across every latest snapshot,
    keyed by (kind, normalized title[+artist]).  Duplicates across
    sources fold into a single scoring row so we don't waste tokens
    reasoning about the same podcast twice."""
    per: dict[str, dict] = {}

    def _add(kind: str, title: str, *, artist: str = '',
              extra: str = '', source_label: str = '') -> None:
        title = (title or '').strip()
        if not title:
            return
        k = _key(kind, title, artist)
        if k not in per:
            per[k] = {
                'key':          k,
                'kind':         kind,
                'title':        title,
                'artist':       (artist or '').strip(),
                'context':      extra.strip(),
                'seen_on':      [],
            }
        if source_label and source_label not in per[k]['seen_on']:
            per[k]['seen_on'].append(source_label)

    # Podcasts
    pod = _read('podcast_charts') or {}
    for slug, panel in (pod.get('sources') or {}).items():
        for it in (panel.get('items') or []):
            _add('podcast', it.get('title') or '',
                  artist=it.get('artist') or '',
                  source_label=panel.get('label') or slug)

    # Songs
    music = _read('music_charts') or {}
    for slug, panel in (music.get('sources') or {}).items():
        for it in (panel.get('items') or []):
            _add('song', it.get('title') or '',
                  artist=it.get('artist') or '',
                  source_label=panel.get('label') or slug)

    # Streaming (per-platform Top-10 lists). Every platform has its
    # own snapshot file with `national` (mixed) plus `us_films` +
    # `us_tv` split lists on some platforms. We score every visible
    # title as either film or tv; the platform is the source_label
    # so lens reasoning can differentiate ("Millennials love Netflix's
    # rewatch nostalgia titles but skip Disney+'s kids catalog").
    for slug, label in (('netflix',    'Netflix'),
                         ('disneyplus', 'Disney+'),
                         ('hulu',       'Hulu'),
                         ('max',        'HBO Max'),
                         ('primevideo', 'Prime Video'),
                         ('espnplus',   'ESPN+')):
        snap = _read(slug) or {}
        for pool_key, kind in (('us_films', 'film'),
                                ('us_tv',    'tv'),
                                ('national', 'title')):
            for it in (snap.get(pool_key) or [])[:20]:
                _add(kind, it.get('title') or '',
                      source_label=snap.get('label') or label)

    # Films (ticketing)
    films = _read('film_ticketing') or {}
    for slug, panel in (films.get('sources') or {}).items():
        for it in (panel.get('items') or []):
            _add('film', it.get('title') or '',
                  source_label=panel.get('label') or slug)

    # Books (Amazon / Apple / Audible)
    books = _read('book_charts') or {}
    for slug, panel in (books.get('sources') or {}).items():
        for it in (panel.get('items') or []):
            _add('book', it.get('title') or '',
                  artist=it.get('artist') or '',
                  source_label=panel.get('label') or slug)

    # Libby (ebook / audiobook / magazine)
    libby = _read('libby_trends') or {}
    for slug, panel in (libby.get('sources') or {}).items():
        for it in (panel.get('items') or []):
            _add('book', it.get('title') or '',
                  artist=it.get('artist') or '',
                  source_label=panel.get('label') or slug)

    # Headlines - business + philanthropy share the same `headline:`
    # keyspace with the top-headlines feed that trends_iq.py composites
    # at runtime from GDELT. We can only score what has a snapshot;
    # top headlines from GDELT don't have a separate snapshot file
    # (they're recomputed per-request), so we score the two topic
    # feeds we do have plus every article on their `by_source` breakouts.
    for src in ('philanthropy_news', 'business_news'):
        snap = _read(src) or {}
        seen = set()
        for it in (snap.get('national') or [])[:80]:
            t = (it.get('title') or '').strip()
            if not t or t in seen:
                continue
            seen.add(t)
            _add('headline', t,
                  extra=it.get('source_label') or it.get('source') or '',
                  source_label=snap.get('label') or src)
        for source_key, lst in (snap.get('by_source') or {}).items():
            for it in (lst or [])[:20]:
                t = (it.get('title') or '').strip()
                if not t or t in seen:
                    continue
                seen.add(t)
                _add('headline', t,
                      extra=str(source_key),
                      source_label=snap.get('label') or src)

    # Trending searches - the default landing tab is populated from
    # google_wide.json (top ~600 terms with score + related headlines).
    # This is by far the biggest coverage gap in the prior scraper:
    # searches was the first tab a user sees and NONE of it was scored.
    gw = _read('google_wide') or {}
    for it in (gw.get('national') or [])[:120]:
        term = (it.get('term') or it.get('title') or '').strip()
        if not term:
            continue
        # Related headlines are prime context - they tell the scorer
        # what the search is actually ABOUT.
        related = it.get('related') or []
        extra = '; '.join(related[:3])[:220] if related else ''
        _add('search', term, extra=extra,
              source_label='Trending searches')

    # Wikipedia trending. `national` is the union of people + events +
    # orgs + places; `people` is the classifier-filtered US-person
    # subset the frontend actually renders on the Trending People
    # card. Score BOTH sets so no matter which the frontend picks,
    # we have a bag.
    wiki = _read('wikipedia_trending') or {}
    wiki_seen = set()
    for it in (wiki.get('people') or []):
        title = it.get('title') or it.get('name') or ''
        if title and title not in wiki_seen:
            wiki_seen.add(title)
            _add('person', title,
                  extra=(it.get('description') or '')[:180],
                  source_label='Wikipedia trending / people')
    for it in (wiki.get('national') or [])[:40]:
        title = it.get('title') or it.get('name') or ''
        if title and title not in wiki_seen:
            wiki_seen.add(title)
            _add('person', title,
                  extra=(it.get('description') or '')[:180],
                  source_label='Wikipedia trending')

    # Social (Reddit / YouTube / TikTok posts).
    for src, key in (('reddit', 'reddit'),
                     ('youtube', 'youtube'),
                     ('tiktok', 'tiktok')):
        snap = _read(src) or {}
        for it in (snap.get('national') or [])[:30]:
            _add('social', it.get('title') or it.get('topic') or '',
                  extra=snap.get('label') or key,
                  source_label=snap.get('label') or key)

    return list(per.values())


# ---------------------------------------------------------------------------
# Claude batch prompt
# ---------------------------------------------------------------------------
def _batch_prompt(lens: dict, batch: list[dict]) -> str:
    """Build the per-batch prompt.  Order of sections:
      1. Task framing (use full 0-100 range, no artificial bias)
      2. Persona description (kind-by-kind rubric)
      3. Anchor calibration items with pinned scores (Claude reads
         these first so it locks the scale before rating the batch)
      4. Kind-specific reasoning rubric per item in the batch
      5. Items list with rich context per row
      6. Strict output format"""
    # Collect the kinds present in this batch so we can filter anchors
    # to only the relevant ones (keeps the prompt tight).
    kinds_in_batch = {it['kind'] for it in batch}
    anchors = [a for a in _ANCHORS.get(lens['id'], [])
                if a['kind'] in kinds_in_batch
                or a['kind'] in ('podcast', 'song', 'book')]  # always keep music/podcast/book anchors as scale-anchors

    lines = [
        "You are an audience-strategist scoring items for a specific "
        "persona.  For each item, output an integer 0-100 that answers: "
        "'How likely is this exact persona to click on, stream, read, "
        "watch, or otherwise engage with THIS specific item this week?'",
        "",
        "USE THE FULL 0-100 RANGE.  Do NOT compress everything into "
        "the 30-55 band.  Items that are core-audience content for "
        "this persona SHOULD score 85-100.  Items the persona would "
        "actively avoid SHOULD score 5-20.  Items that are plausible "
        "but not core belong in 40-65.  A blanket 'bias low' is WRONG "
        "and produces useless filters.",
        "",
        "Score based on: (a) does this match a core-interest area for "
        "the persona? (b) does the persona's cohort actually consume "
        "this KIND of content? (c) is this specific item core, "
        "adjacent, or anti-aligned within that kind?  A generic search "
        "term with no context should score 40-55 (unknown intent), NOT "
        "the middle of the persona's average.",
        "",
        "=========================================================",
        "PERSONA: " + lens['label'],
        "=========================================================",
        lens['persona'],
        "",
        "=========================================================",
        "CALIBRATION ANCHORS (already-decided scores for this persona)",
        "=========================================================",
        "Use these as the reference scale.  Do NOT rescore them; they "
        "are shown so you can peg the batch consistently.",
    ]
    for a in anchors:
        lines.append(f'  score={a["score"]:3d}  kind={a["kind"]:<8}  title="{a["title"]}"')

    lines += [
        "",
        "=========================================================",
        "OUTPUT FORMAT (STRICT)",
        "=========================================================",
        "Return a single JSON array with one object per input item, IN "
        "THE SAME ORDER.  Each object:",
        '  { "id": <int>, "score": <int 0-100>, "why": "<8-14 word rationale specific to THIS item and THIS persona>" }',
        "Return ONLY the JSON array, no prose before or after.",
        "",
        "=========================================================",
        "ITEMS TO SCORE (" + str(len(batch)) + ")",
        "=========================================================",
    ]
    for i, it in enumerate(batch):
        title = it['title'][:180]
        row   = f'[{i}] kind={it["kind"]}  title="{title}"'
        if it.get('artist'):
            row += f'\n     artist="{it["artist"][:100]}"'
        if it.get('context'):
            row += f'\n     context="{it["context"][:240]}"'
        if it.get('seen_on'):
            row += f'\n     seen_on={it["seen_on"][:3]}'
        lines.append(row)
    return '\n'.join(lines)


_JSON_ARRAY_RE = re.compile(r'\[[\s\S]*\]')


def _parse_batch(text: str, batch_len: int) -> list[Optional[dict]]:
    """Parse Claude's JSON array back into a list aligned with the
    input batch.  Returns [None] for any slot Claude skipped or
    returned malformed."""
    if not text:
        return [None] * batch_len
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        return [None] * batch_len
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return [None] * batch_len
    if not isinstance(arr, list):
        return [None] * batch_len
    by_id: dict[int, dict] = {}
    for row in arr:
        if not isinstance(row, dict):
            continue
        try:
            rid = int(row.get('id'))
        except Exception:
            continue
        try:
            score = int(row.get('score') or 0)
        except Exception:
            score = 0
        score = max(0, min(100, score))
        why = (row.get('why') or '').strip()[:200]
        by_id[rid] = {'score': score, 'why': why}
    out: list[Optional[dict]] = []
    for i in range(batch_len):
        out.append(by_id.get(i))
    return out


def _score_batch(client, lens: dict, batch: list[dict]) -> list[Optional[dict]]:
    prompt = _batch_prompt(lens, batch)
    try:
        # 4096 tokens gives Claude room to write a real why-string per
        # item (was 2048 which sometimes truncated mid-JSON).
        resp = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{'role': 'user', 'content': prompt}],
            timeout=_TIMEOUT_S,
        )
    except Exception as e:
        logger.info("lens_relevance %s batch (n=%d): %s",
                     lens['id'], len(batch), e)
        return [None] * len(batch)
    text = ''.join(getattr(b, 'text', '') for b in (resp.content or []))
    return _parse_batch(text, len(batch))


def _score_lens(client, lens: dict, items: list[dict]) -> dict[str, dict]:
    """Score every item for a single lens.  Batches of `_BATCH_SIZE`
    items per Claude call.  Returns {key: {'score': int, 'why': str}}."""
    if not items:
        return {}
    out: dict[str, dict] = {}
    batches: list[list[dict]] = [
        items[i:i + _BATCH_SIZE]
        for i in range(0, len(items), _BATCH_SIZE)
    ]
    logger.info("lens_relevance %s: %d items -> %d batches (%s, concurrency=%d)",
                 lens['id'], len(items), len(batches),
                 _CLAUDE_MODEL, _CONCURRENCY)
    with concurrent.futures.ThreadPoolExecutor(max_workers=_CONCURRENCY) as ex:
        futs = {ex.submit(_score_batch, client, lens, b): b for b in batches}
        for bi, fut in enumerate(concurrent.futures.as_completed(futs), start=1):
            batch = futs[fut]
            try:
                results = fut.result(timeout=_TIMEOUT_S + 30)
            except Exception as e:
                logger.info("lens_relevance %s batch %d failed: %s",
                             lens['id'], bi, e)
                continue
            covered = 0
            for it, res in zip(batch, results):
                if res:
                    out[it['key']] = res
                    covered += 1
            logger.info("  %s [batch %2d/%d] -> %d/%d scored",
                         lens['id'], bi, len(batches),
                         covered, len(batch))
    return out


# ---------------------------------------------------------------------------
# Fetch entry point
# ---------------------------------------------------------------------------
def fetch(only_lens: Optional[str] = None, dry_run: bool = False) -> dict[str, Any]:
    items = _collect_all_items()
    logger.info("lens_relevance: collected %d unique items across all snapshots",
                 len(items))
    lens_meta = [
        {'id': l['id'], 'label': l['label'],
         'emoji': l['emoji'], 'description': l['description']}
        for l in _LENSES
    ]
    if dry_run:
        return {'items': {it['key']: {'kind': it['kind'],
                                         'title': it['title']}
                            for it in items},
                'lenses': lens_meta,
                'count':  len(items),
                'dry_run': True}

    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        return {'items': {}, 'lenses': lens_meta, 'count': 0,
                 'error': 'ANTHROPIC_API_KEY not set'}
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        return {'items': {}, 'lenses': lens_meta, 'count': 0,
                 'error': f'anthropic SDK missing: {e}'}
    client = anthropic.Anthropic(api_key=api_key)

    # Combine per-lens results into a single item-keyed dict:
    #   items[key] = {kind, title, scores: {lens_id: {score, why}}}
    per_lens: dict[str, dict[str, dict]] = {}
    for lens in _LENSES:
        if only_lens and lens['id'] != only_lens:
            continue
        per_lens[lens['id']] = _score_lens(client, lens, items)

    combined: dict[str, dict] = {}
    for it in items:
        row: dict[str, Any] = {
            'kind':   it['kind'],
            'title':  it['title'],
            'scores': {},
            'why':    {},   # {lens_id: "8-14 word rationale"}
        }
        if it.get('artist'):
            row['artist'] = it['artist']
        for lens_id, lens_out in per_lens.items():
            hit = lens_out.get(it['key'])
            if hit:
                row['scores'][lens_id] = hit['score']
                if hit.get('why'):
                    row['why'][lens_id] = hit['why']
        # Drop the `why` sub-dict if nothing landed (keeps payload lean).
        if not row['why']:
            row.pop('why', None)
        if row['scores']:
            combined[it['key']] = row

    # Per-kind top-50% cutoffs.  Frontend uses these instead of a
    # global threshold so kinds where the persona scores everything
    # lower (e.g. MS NOW songs never breaking 50) still get filtered
    # meaningfully - we show the top half of each kind rather than
    # blanking the whole tab.
    cutoffs = _compute_cutoffs(combined, [l['id'] for l in lens_meta])

    return {
        'items':        combined,
        'lenses':       lens_meta,
        'cutoffs':      cutoffs,
        'count':        len(combined),
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


def _compute_cutoffs(items: dict[str, dict],
                      lens_ids: list[str],
                      top_pct: float = 0.40,
                      floor: int = 20,
                      min_keep: int = 5) -> dict[str, dict[str, int]]:
    """For every (lens, kind) return the score threshold above which
    items should be considered 'in the persona's top N% for this kind'.

    Rationale: a global score threshold (>=55) hides entire tabs when
    the persona's cohort simply scores that kind lower on average.
    Per-kind cutoffs preserve the relative ranking Claude produced.

    Defaults tuned to keep roughly 25-45% of items per tab per lens
    (2026-09-02): `top_pct=0.40` picks the score at the 40th-percentile
    slot in descending order, so items >= that score make ~40% of the
    tab.  The `floor` still gates out actively anti-aligned items in
    the 5-19 band even when the whole kind is weak.

    Empty-tab safeguard: if the computed cutoff would keep fewer than
    `min_keep` items for a (lens, kind) pair, step the cutoff down 5
    points at a time until at least `min_keep` items pass or the
    cutoff hits 5.  This implements the "expand the cutoff by one
    notch and try again" rule so a tab never ships empty."""
    from collections import defaultdict
    by_lens_kind: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for entry in items.values():
        kind = entry.get('kind') or ''
        scores = entry.get('scores') or {}
        for lens_id, s in scores.items():
            by_lens_kind[lens_id][kind].append(int(s))
    out: dict[str, dict[str, int]] = {}
    for lens_id in lens_ids:
        out[lens_id] = {}
        for kind, arr in by_lens_kind.get(lens_id, {}).items():
            if not arr:
                continue
            arr_sorted = sorted(arr, reverse=True)
            idx = max(0, int(len(arr_sorted) * top_pct) - 1)
            cutoff = arr_sorted[idx] if arr_sorted else floor
            cutoff = max(cutoff, floor)
            # Anti-empty: if the cutoff excludes almost everything
            # (very rare for broad personas, more common for narrow
            # persona x niche-kind pairs like MS NOW x song), step
            # down 5 points at a time until at least `min_keep`
            # items pass, or we hit an absolute floor of 5.  The
            # frontend never has to render an empty tab.
            kept = sum(1 for s in arr_sorted if s >= cutoff)
            while kept < min_keep and cutoff > 5:
                cutoff = max(5, cutoff - 5)
                kept = sum(1 for s in arr_sorted if s >= cutoff)
            out[lens_id][kind] = cutoff
    return out


if __name__ == '__main__':
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO,
                          format='%(asctime)s %(levelname)s %(name)s %(message)s')
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', help='score only this lens id')
    ap.add_argument('--dry-run', action='store_true',
                     help='collect items but skip Claude calls')
    args = ap.parse_args()

    from ._base import run_scraper
    result = run_scraper(
        'lens_scores',
        'Persona lens relevance',
        'meta',
        lambda: fetch(only_lens=args.only, dry_run=args.dry_run),
    )
    print(f"lens_relevance: count={result.get('count')} "
           f"lenses={[l['id'] for l in result.get('lenses') or []]} "
           f"error={result.get('error')}",
           file=sys.stderr)

    # A fresh lens_scores.json invalidates every live compute_view
    # cache entry - those cached payloads still hold the OLD
    # lens_config until their stale_until elapses (up to 24h away).
    # The daily scraper cron self-heals via run_all.py's cache warm,
    # but a standalone `python -m scripts.trends_scrapers.lens_relevance`
    # run doesn't invalidate anything. Same guard as _bedrock_scorer.py;
    # historic (asof=past-date) entries are never touched.
    if not args.dry_run and not result.get('error'):
        try:
            import pathlib as _pathlib
            _root = _pathlib.Path(__file__).resolve().parent.parent.parent
            if str(_root) not in sys.path:
                sys.path.insert(0, str(_root))
            from trends_iq import invalidate_live_compute_view_caches  # noqa: E402
            n = invalidate_live_compute_view_caches()
            print(f"invalidated {n} live compute_view cache entries",
                   file=sys.stderr)
        except Exception as e:
            print(f"WARN: compute_view cache invalidation failed: {e}",
                   file=sys.stderr)
