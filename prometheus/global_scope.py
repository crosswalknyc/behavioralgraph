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

__all__ = ['INTERPRET_RULE_6_GLOBAL', 'INTERPRET_RULE_WHOLE_WORLD', 'DEMO_BUCKETS_NOTE', 'spec_markets']


def spec_markets(draft, scrub, subject=''):
    """The draft's researched world metros as the spec carries them:
    ``{'location_markets': [{'market': 'City, Country', 'share': float}, ...]}``
    when the draft holds three or more usable rows, else ``{}``.
    ``migration.international_profiles.markets_for_spec`` reads the list
    and lets it win over the generic world list, so a worldwide build
    shows the subject's own footprint. ``scrub`` is the spec step's own
    text scrub (chat._spec_from_draft hands its local one in). Never
    raises."""
    out = []
    try:
        rows = draft.get('location_markets') if isinstance(draft, dict) else None
        for m in (rows or [])[:60]:
            if not isinstance(m, dict):
                continue
            label = str(m.get('market') or m.get('label') or '').strip()
            try:
                share = float(m.get('share'))
            except (TypeError, ValueError):
                continue
            if not label or share <= 0:
                continue
            try:
                label = str(scrub(label, field='location_market', subject=subject, max_len=60, single_line=True) or '').strip()
            except Exception:
                label = label[:60]
            if label:
                out.append({'market': label, 'share': round(share, 4)})
    except Exception:
        return {}
    return {'location_markets': out} if len(out) >= 3 else {}

DEMO_BUCKETS_NOTE = (
    "INCOME in US dollar equivalents: 'Under US$10,000', 'US$10,000 - "
    "US$24,999', 'US$25,000 - US$49,999', 'US$50,000 - US$74,999', "
    "'US$75,000 - US$99,999', 'US$100,000 - US$149,999', 'US$150,000+', "
    "'Prefer Not to Say'; EDUCATION: 'Secondary School or Less', "
    "'Vocational or Technical Qualification', 'Bachelors Degree', "
    "'Masters Degree', 'Doctorate / PhD', 'Prefer Not to Say'; ETHNICITY "
    "by heritage region: 'Asian Heritage', 'European Heritage', 'African "
    "Heritage', 'Latin American Heritage', 'Middle Eastern / North "
    "African Heritage', 'Mixed / Other'"
)

# appended to SEMANTIC GUARDS #6 (INTERNATIONAL)
INTERPRET_RULE_6_GLOBAL = (
    " GLOBAL (2026-11-23): a 'global' / 'worldwide' / 'international' / "
    "'around the world' / 'global footprint' / 'all markets' ask with no "
    "single country named is the subject's WORLDWIDE footprint: "
    "country='Global', subject and name '<Subject> - Global' (the dash "
    "form, e.g. 'Guinness - Global'), universe_anchor = the researched "
    "worldwide audience count (not the US count), tu_demos in the "
    "worldwide buckets (" + DEMO_BUCKETS_NOTE + "), and "
    "`location_markets`: the subject's own top world metros (25 to 40, "
    "labeled 'City, Country' such as 'New York, USA', 'London, UK', "
    "'Berlin, Germany'; shares of its worldwide audience summing to "
    "100, researched from where the subject really has reach, never "
    "a generic world list). The deliverable carries those cities for "
    "LOCATION and global brands and publications. A proper name that "
    "carries the word ('Global Citizen', 'International Fencing "
    "Federation') is not a scope.\n"
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
