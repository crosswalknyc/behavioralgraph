"""Research-anchored subject_raw estimates for profile subjects NOT found in Gen Pop.

Each value represents the estimated audience size (out of a 10M panel) for
the subject. Anchored to nearby Gen Pop entries:

  Tom Hanks (TALENT)         2,494,410
  Denzel Washington (TALENT) 3,336,693
  Adam Sandler (TALENT)      5,406,630
  Kim Kardashian (TALENT)    5,206,260
  Robin Roberts (TALENT)     2,294,430
  Paul Rudd (TALENT)         3,551,932
  Glenn Close (TALENT)       1,995,730
  Daniel Day Lewis (TALENT)    268,500   (prestige-niche)
  The Simpsons (FRANCHISE)   3,198,040
  Marvel (FRANCHISE)         6,172,355

External anchors (Nielsen reach, MAU statistics, public reporting):
  Tracker S2 (CBS)  ~9.5M weekly P2+ viewers -> ~5.2M panel-comparable
  Krapopolis        ~700K Fox primetime animation
  Wordle            ~2.5-3M daily players (NYT)
  Chime             ~22M US active users (~900K panel)
  Bonvoy            ~30M US active members (~1.4M panel)

Estimates are individual research per subject and intentionally land at
organic integers (no trailing zeros). Each value should look like a real
Gen Pop reading, not an estimate snapped to 50K/100K boundaries.

A saved size starts every fresh build of its subject (2026-11-20, Jenna:
"you'd save the number so that you have the audience size to use if someone
were to pull it"; migration/saved_subject_size.py). Only a messy entry is a
saved size: the round entries below are the older placeholder table, read by
sweeps and audits, and never size a build. WINDOWS dates an entry (an undated
one stands for a trailing year); ALIASES lists other ways the subject is
written (case, spacing and punctuation never need one). The file is mirrored
byte for byte at bg-webapp/scripts/_subject_estimates.py.
"""

# Format: "Subject Name" : estimated_raw_in_10M_panel
# Estimates rounded to nearest 50,000 to avoid false precision.
ESTIMATES = {
    # ─── A-list mainstream legacy actors (Hanks tier 2.5M, Denzel 3.3M) ───
    "Brad Pitt":             3_500_000,
    "Robert De Niro":        2_800_000,
    "Clint Eastwood":        2_400_000,
    "Jack Nicholson":        1_900_000,
    "Anthony Hopkins":       2_000_000,
    "Arnold Schwarzenegger": 3_200_000,
    "Daniel Craig":          2_800_000,
    "Jim Carrey":            3_000_000,
    "Matthew Mcconaughey":   3_000_000,
    "Donald Glover":         3_200_000,
    "Scarlett Johansson":    4_200_000,
    "Mahershala Ali":        1_800_000,
    "Penelope Cruz":         1_800_000,
    "Mel Gibson":            1_800_000,
    "Mike Myers":            1_800_000,
    "Michael Caine":         1_600_000,
    "Michael Keaton":        1_800_000,
    "Michael Douglas":       1_800_000,
    "Michael B Jordan":      3_400_000,
    "Mila Kunis":            2_200_000,
    "Owen Wilson":           2_400_000,
    "Patrick Stewart":       2_200_000,
    "Mark Hamill":           3_500_000,
    "Angelina Jolie":        4_200_000,
    "Chris Pine":            3_000_000,
    # ─── Older legends / niche / nostalgia ───
    "Mr T":                  1_500_000,
    "Christopher Lloyd":     1_400_000,
    "Chuck Norris":          2_200_000,
    "Dolph Lundgren":        1_000_000,
    "Jet Li":                1_400_000,
    "Jean claude Van Damme": 1_500_000,
    "Andy Serkis":           1_200_000,
    "Mel Brooks":            1_600_000,
    "Daniel Day lewis":      800_000,
    "Lily Tomlin":           1_400_000,
    "Edie Falco":            1_000_000,
    "Judi Dench":            1_800_000,
    "Barbra Streisand":      2_000_000,
    "Nate Bargatze":         1_600_000,
    "Larry David":           2_000_000,
    "Ali Wong":              1_800_000,
    # ─── Prestige / younger actors ───
    "Eddie Redmayne":        1_500_000,
    "Elle Fanning":          1_800_000,
    "Emma Thompson":         1_500_000,
    "Frances Mcdormand":     1_400_000,
    "Hong Chau":             600_000,
    "Eve Harlow":            500_000,
    "Lindsay Lohan":         1_800_000,
    "Lupita Nyong o":        1_800_000,
    "Octavia Spencer":       1_800_000,
    "Olivia Wilde":          1_800_000,
    "Regina King":           1_400_000,
    "Gabrielle Union":       2_000_000,
    # ─── News / talk / morning anchors ───
    "Oprah Winfrey":         5_200_000,
    "Anderson Cooper":       2_800_000,
    "Lester Holt":           2_400_000,
    "Joy Behar":             1_800_000,
    "Kelly Ripa":            2_400_000,
    "Hoda Kotb":             1_800_000,
    "George Stephanopoulos": 2_200_000,
    "Savannah Guthrie":      1_800_000,
    # ─── Sports media personalities (heavy social + podcast + ESPN footprint) ───
    "Stephen A Smith":       186_521,    # researched: 1.34M YT subs + 1.36M monthly podcast listeners + ESPN First Take + SiriusXM
    # ─── Kardashian/Jenner family (Kim K = 5.2M anchor) ───
    "Khloe Kardashian":      4_000_000,
    "Kourtney Kardashian":   3_500_000,
    "Kris Jenner":           3_800_000,
    "Kendall Jenner":        4_400_000,
    "Kylie Jenner":          4_800_000,
    # ─── Shows / franchises ───
    "Tracker":               5_200_000,   # top CBS broadcast drama, ~9.5M P2+
    "Universal Basic Guys":  700_000,     # Fox niche animation
    "Krapopolis":            750_000,     # Fox niche animation
    "Grimsburg":             550_000,     # Fox animation
    "Bobs Burgers":          3_200_000,   # established hit
    "Adult Animation":       5_200_000,   # composite persona — broad
    # ─── Sports teams ───
    "LA Sparks":             280_000,     # WNBA team
    "LA Sparks fast":        280_000,     # FAST-channel viewer cohort
    # ─── Personas / composites (anchored to product MAU) ───
    "Chime Banking Customers":   900_000,   # ~22M US Chime users / 240M adults * 10M
    "Wordle Players":            2_500_000, # NYT Wordle ~50M+ MAU, panel-comparable
    "Marriott Bonvoy Moments":   1_400_000, # ~30M US Bonvoy active members
    "American Express Events":   1_800_000, # ~50M US Amex cardholders, event-engaged subset
    "Valkyrae":                  1_400_000, # streamer ~3M followers, panel-comparable
    "Foosball":                  400_000,   # niche sport / activity
    # ─── Media brands / CPG (researched 2026-11-20, trailing 12 months to 2026-11-20) ───
    "VICE":                      379_915,   # 12,533,396 US: floor = @VICE YouTube US viewers at the 30-day pace (12.23M views x 43.7% US / 7.9 a viewer) 8,230,362; ceiling 30,061,480 (VICE, VICE News, Munchies, VICE Sports YouTube + @vice/@vicenews TikTok + vice.com US visitors not already counted); 20th percentile; contains VICE News (Gen Pop 6,359,152) and VICE Sports
    "VICE Sports":               48_172,    # 1,589,194 US: owned-channel followers (YT 1.14M, TikTok 376.4K, FB 288.8K, IG 93.9K) x US share, deduped = 697,666 floor; unique-viewer ceiling 5.08M; bottom third of the range
    "Just For Men":              116_787,   # 3,852,803 US: 125.5M US men x 8% cover gray (Pew 2025) x 61.2% on Just For Men x 44.7% touch online, + 1.13M non-user touchers (reference/underpinnings/vice_sports_just_for_men_2026_11_20.md)
}

# The window a saved size was researched on (start, end).
WINDOWS = {
    "VICE":         ("2025-11-20", "2026-11-20"),
    "VICE Sports":  ("2025-11-20", "2026-11-20"),
    "Just For Men": ("2025-11-20", "2026-11-20"),
}

# Other names a saved subject goes by, when they differ by more than case,
# spacing, punctuation, a leading "the" or a trailing "fans" / "audience"
# ("Subject": ("Other Name", ...)). Never a narrower universe (a channel's
# followers, one product line): that is a different build.
ALIASES = {}


def get_subject_raw(subject: str, default: int = 10_000_000) -> int:
    """Return the researched subject_raw, or default (10M) if not in table."""
    return ESTIMATES.get(subject, default)
