"""The whole world as a scope (Jenna 2026-11-23, after Samantha Kolton's
"Can you run a global profile IQ analysis for guinness the beer?" came
back as the US Guinness profile).

A 'global' / 'worldwide' / 'international' / 'around the world' / 'global
footprint' / 'all markets' ask is a universe qualifier: the subject's
worldwide footprint, built the way the Omaze international precedent
builds a country (spec country 'Global', migration.international_profiles)
and named '<Subject> - Global'. A US profile is never its match; an
existing '<Subject> - Global' file is; a US ask never matches the global
file.

This module holds the two paragraphs the interpret rulebook carries for
it (prometheus/legacy/core_interpret.py reads them in place, so the legacy
view grows by nothing) and the one place that names the worldwide bucket
labels the interpret step emits for tu_demos. The deterministic side (the
qualifier gate, the normalized match, the country guard) lives in app.py
next to the rules it extends; the engine side lives in
migration/international_profiles.py.
"""
from __future__ import annotations

__all__ = ['INTERPRET_RULE_6_GLOBAL', 'INTERPRET_RULE_WHOLE_WORLD', 'DEMO_BUCKETS_NOTE']

DEMO_BUCKETS_NOTE = (
    "INCOME in US dollars: 'Under $10,000', '$10,000 - $24,999', "
    "'$25,000 - $49,999', '$50,000 - $99,999', '$100,000+'; EDUCATION: "
    "'No Formal Qualification', 'Primary School', 'Secondary / High "
    "School', 'Vocational / Technical', 'Bachelors Degree', 'Postgraduate "
    "Degree'; ETHNICITY by world region: 'South Asian', 'East Asian', "
    "'White / European', 'Black / African', 'Hispanic / Latino', "
    "'Southeast Asian', 'Middle Eastern / North African', 'Mixed / Other'"
)

# appended to SEMANTIC GUARDS #6 (INTERNATIONAL)
INTERPRET_RULE_6_GLOBAL = (
    " GLOBAL (2026-11-23): a 'global' / 'worldwide' / 'international' / "
    "'around the world' / 'global footprint' / 'all markets' ask with no "
    "single country named is the subject's WORLDWIDE footprint: "
    "country='Global', subject and name '<Subject> - Global' (the dash "
    "form, e.g. 'Guinness - Global'), universe_anchor = the researched "
    "worldwide audience count (not the US count), tu_demos in the "
    "worldwide buckets (" + DEMO_BUCKETS_NOTE + "). The deliverable "
    "carries world cities for LOCATION and global brands and "
    "publications. A proper name that carries the word ('Global "
    "Citizen', 'International Fencing Federation') is not a scope.\n"
)

# appended to the UNIVERSE-QUALIFIER COMPATIBILITY hard rule
INTERPRET_RULE_WHOLE_WORLD = (
    "THE WHOLE WORLD IS A SCOPE (2026-11-23): a global / worldwide / "
    "international ask is a universe qualifier. A US profile is NEVER "
    "its existing match, refresh, or derive_cut parent (a global "
    "footprint is not a cut of the US file). An existing '<Subject> - "
    "Global' file IS the match for a global ask, and is NEVER the match "
    "for a plain (US) ask. With no global file held, the decision is "
    "new_build with country='Global' and subject '<Subject> - Global', "
    "as the INTERNATIONAL guard says.\n\n"
)
