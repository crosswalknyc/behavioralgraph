"""The one vocabulary table for every reader-facing exit (stage 4 of the
2026-11-19 rebuild, approved by Jenna 2026-11-20).

Before this module the words the exits police lived in about twelve
lists across forty files: ``prometheus_analysis._SCRUB_RULES``,
``guards._METHOD_SENTENCE_RX`` and its swaps, ``guards.SAMPLE_VOCAB_RX``,
``guards.PARTNER_BANNED_TOKENS`` and the partner belt, ``envelope._INFRA_RX``,
``outbound_mail.BANNED``, ``twin_notify.BANNED_RX``, ``app._V1_BANNED_TOKENS``,
the correction mail's list and the knowledge compiler's leak gate. A
template written in one module was mangled by a scrub written in another
("the countUS consumers we measure", 2026-11-19) because neither knew the
other's vocabulary. Now every list is defined here and only here. The old
names stay in their modules as aliases of these objects, so every import
and every test keeps working; a new word goes in this table, with its
kind and its swap, and nowhere else.

What the table carries, in the order the exit applies it
(``prometheus.exit.ORDER``):

  * the held-out exact lines (``methodology.OTHERS_ALLOWLIST``: how Nielsen
    and Peacock count a view), held before any pass and put back byte for
    byte after the last one;
  * source names (``migration.source_names``: one list, imported, never
    copied);
  * the dash rule (em, en and horizontal bar to a hyphen);
  * the word swaps: the synth family, pipeline, hostmap, enforcer, modeled,
    estimated, derived, panel, household to person and households to people
    (Jenna 2026-11-20), HH to Accounts (Jenna 2026-08-31),
    infrastructure, model and vendor names, field names typed into prose;
  * the method-sentence pattern, its safe swaps and its last-resort subs;
  * the cause narrative (what happened to the data);
  * the citation and off-clickstream sentence patterns;
  * the sample vocabulary;
  * the partner belt (tokens, attribution, hard words, swaps);
  * the mail check list and the twin notice pattern;
  * the knowledge compiler's leak gate;
  * the field kinds a pass applies to.

Nothing here is reader-facing text. No em dashes.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- kinds
# What a string is, which decides which passes run on it.
PROSE = 'prose'            # a reply, a mail body, deck and PDF text, a CSV cell
NAME = 'name'              # a subject, title, brand or person: never rewritten
CHIP = 'chip'              # a quick-reply label
BRIEF_FIELD = 'brief_field'  # the brief card's own sentence: the sample pass only
DECK_COPY = 'deck_copy'    # prose plus the door rule
PARTNER = 'partner'        # a key caller's string: the belt, fails closed
FILENAME = 'filename'      # a delivered file's name
MAIL = 'mail'              # outbound copy: prose plus the mail check
KINDS = (PROSE, NAME, CHIP, BRIEF_FIELD, DECK_COPY, PARTNER, FILENAME, MAIL)

# ---------------------------------------------------------------- dashes
# No em dashes (no-em-dashes.mdc); the en dash and the horizontal bar go
# the same way. The em dash reads as " - " so two words never join.
DASHES = (('\u2014', ' - '), ('\u2013', '-'), ('\u2015', ' - '))
DASH_RX = re.compile('[\u2014\u2013\u2015]')

# ------------------------------------------------------------ model names
# The model is never named to a reader; a person or a brand that shares
# the word is never rewritten (stage 4, 2026-11-20: "Jean-Claude Van
# Damme" read "Jean-The analysis Van Damme", "Opus One" and "Sonnet 18" went
# the same way, and a key caller's answer naming them came back empty).
# "Claude" is the model unless a hyphen joins it to a given name or a
# capitalized surname follows (Claude Monet); "Claude Opus", "Claude's",
# "Claude wrote" and "Claude AI" are the model. "Opus" and "Sonnet" are
# the model unless a capitalized word follows (Opus One, Sonnet Software),
# a two-digit number follows (Sonnet 18), or the poem's article precedes
# (a sonnet); "Opus 4", "Sonnet 4.6", "on Opus" are the model.
_CAP_WORD = r'(?-i:[A-Z][a-z]+)'
_MODEL_TAIL = r'(?:opus|sonnet|haiku|ai|model|code|instant|pro|max)'
CLAUDE_SRC = r'(?<![\w-])claude\b(?!\s+(?!' + _MODEL_TAIL + r'\b)' + _CAP_WORD + r'\b)'
OPUS_SRC = r'(?<!magnum )\bopus\b(?!\s+' + _CAP_WORD + r'\b)'
SONNET_SRC = r'(?<!\ba )(?<!\ban )\bsonnet\b(?!\s+' + _CAP_WORD + r'\b)(?!\s+\d{2,}\b)'
MODEL_NAME_SRC = r'(?:' + CLAUDE_SRC + r'|\banthropic\b|' + OPUS_SRC + r'|' + SONNET_SRC + r'|\bopen\s?ai\b|\bgpt[-0-9a-z.]*\b|\bllms?\b)'
MODEL_NAME_RX = re.compile(MODEL_NAME_SRC, re.I)

# ------------------------------------------------------------ word swaps
# Banned internal vocabulary with the product word that replaces it.
# Applied case-insensitively; a capitalized match keeps its capital. A
# pattern wrapped in (?-i:...) is case-sensitive on purpose.
# (was prometheus_analysis._SCRUB_RULES)
WORD_SWAPS = (
    (r'\bsynthesi[sz]ed\b', 'built'),
    (r'\bsynthesi[sz]es?\b', 'build'),
    (r'\bsynthesis\b', 'build'),
    (r'\bsynthetic(?:ally)?\b', 'measured'),
    (r'\bsynths?\b', 'build'),
    (r'\bpipelines?\b', 'process'),
    (r'\bhostmap(?:ped|s)?\b', 'brand universe'),
    (r'\benforcers?\b', 'check'),
    (r'\bmodell?ed\b', 'measured'),
    # Gap / methodology disclosure words (2026-08-27, Jenna / Paw
    # Patrol 4-6 defect): the answer is the read, never the mechanics.
    (r'\bestimated\b', 'measured'),
    (r'\bderived\b', 'measured'),
    (r'\bextrapolated\b', 'measured'),
    (r'\binterpolated\b', 'measured'),
    (r'\bpanel[- ]projected\b', 'projected'),
    (r'\bpanelists\b', 'viewers'),
    (r'\bpanelist\b', 'viewer'),
    (r'\bpanel\b', 'audience'),
    # Individual-level language (standing rule): counts and cohorts
    # are viewers / people / accounts, never households. A household is
    # a person and households are people (Jenna 2026-11-20); the three
    # phrases above them keep their English (income stays income, a
    # household name is a well-known name, household reach is reach).
    (r'\bhousehold income\b', 'income'),
    (r'\bhousehold names\b', 'well-known names'),
    (r'\bhousehold name\b', 'well-known name'),
    (r'\bhousehold (?:penetration|reach)\b', 'reach'),
    (r'\bhouseholds\b', 'people'),
    (r'\bhousehold\b', 'person'),
)
# HHs is Accounts, title case, a direct substitution (Jenna 2026-08-31,
# individual-level-language.mdc). Case-sensitive so the agency HHS and a
# clock's HH:MM are left alone. Its own tuple because the payload exit
# applies these three to every text field on their own (exit._light).
HH_SWAPS = (
    (r'(?-i:\bHHs\b)', 'Accounts'),
    (r'(?-i:\bper[- ]HH\b)', 'per account'),
    (r'(?-i:\bHH\b)(?![:\d])', 'account'),
)
WORD_SWAPS = WORD_SWAPS + HH_SWAPS + (
    (r'\bhetzner\b', 'server'),
    (r'\bclickhouse\b', 'server'),
    (r'\bsystemd\b', 'server'),
    (CLAUDE_SRC, 'the analysis'),
    (r'\banthropic\b', 'the analysis'),
    (OPUS_SRC, 'the analysis'),
    (SONNET_SRC, 'the analysis'),
    (r'\bopen\s?ai\b', 'the analysis'),
    (r'\bgpt[-0-9a-z.]*\b', 'the analysis'),
    (r'\bllm\b', 'analysis'),
    # breakdown field names typed into prose (Cara Dorfman 2026-11-10: a
    # month table's header read "(penetration_pct is the same shared
    # audience as a share"): the plain word, never the key
    (r'\bpenetration_pct\b', 'penetration'),
    (r'\bshare_pct\b', 'share'),
)
WORD_SWAPS_COMPILED = tuple((re.compile(pat, re.IGNORECASE), rep) for pat, rep in WORD_SWAPS)
HH_SWAPS_COMPILED = tuple((re.compile(pat, re.IGNORECASE), rep) for pat, rep in HH_SWAPS)

# 'estimate' as a noun (2026-10-06): a figure, never an estimate.
ESTIMATE_NOUN_RX = re.compile(r'\b([Ee])stimates?\b')


def _swap(s, compiled):
    for rx, rep in compiled:
        if rx.pattern.startswith('(?-i:'):
            # a case-sensitive rule (the HH rules) says its replacement as
            # written: HHs is Accounts, a bare HH is account
            s = rx.sub(rep, s)
            continue
        s = rx.sub(lambda m, _r=rep: (_r[:1].upper() + _r[1:]) if m.group(0)[:1].isupper() else _r, s)
    return s


def swap_words(text):
    """The word swaps over one string, capital kept on a capitalized match."""
    return _swap(str(text or ''), WORD_SWAPS_COMPILED)


def swap_hh(text):
    """The three HH rules alone (every payload field gets these)."""
    return _swap(str(text or ''), HH_SWAPS_COMPILED)


def swap_estimate_noun(text):
    return ESTIMATE_NOUN_RX.sub(
        lambda m: ('Figure' if m.group(1) == 'E' else 'figure') + ('s' if m.group(0).endswith('s') else ''),
        str(text or ''))


# ------------------------------------------------- citations, off-clickstream
# First-party frame guards (2026-09-28): a sentence that cites a research
# vendor or asserts behaviour a clickstream cannot observe (awareness, ad
# recall, stated intent, in-store traffic, linear tune-in) goes whole.
# (was prometheus_analysis._CITATION_RX / _OFFCLICK_RX)
CITATION_RX = re.compile(
    r'\b(?:statista|nielsen|pew(?:\s+research)?|emarketer|yougov|'
    r'comscore|sensor\s*tower|data\.ai|mri[\s-]?simmons|kantar|'
    r'parrot\s+analytics|antenna|samba\s*tv|luth)\b'
    r'|\baccording to (?!the profile\b|the file\b|the data\b|'
    r'your dashboard\b|crosswalk\b|the read\b)'
    r'|\bas reported by\b|\bsourced? from\b|\bper (?:a|an|the)? ?'
    r'(?:survey|study|report)\b|\bsurveys? (?:found|show|suggest)\b'
    r'|\bindustry (?:reports?|estimates?) (?:say|show|suggest)\b',
    re.IGNORECASE)

OFFCLICK_RX = re.compile(
    r'\b(?:brand\s+)?awareness\b|\bad\s+recall\b|\brecall(?:ed)?\s+'
    r'seeing\b|\bintend(?:s)?\s+to\s+(?:buy|purchase|subscribe)\b'
    r'|\bstated\s+intent\b|\bfoot\s*traffic\b|\bfootfall\b'
    r'|\bin[\s-]store\s+(?:visits?|traffic|purchases?)\b'
    r'|\b(?:watched|viewed|tuned\s+in)\s+on\s+(?:cable|linear|'
    r'broadcast\s+tv)\b|\bover[\s-]the[\s-]air\b'
    r'|\bword\s+of\s+mouth\b|\battended\s+in\s+person\b',
    re.IGNORECASE)

# ------------------------------------------------------------ source names
# One list, in migration.source_names; imported here so every exit reads
# the same names. The hard names are measurement firms and research
# houses; a sentence that still names one after the citation is cut goes.
try:
    from migration.source_names import (HARD_ALT as SOURCE_HARD_ALT, CASE_ALT as SOURCE_CASE_ALT,
                                        PHRASE_ALT as SOURCE_PHRASE_ALT, ATTRIB_ALT as SOURCE_ATTRIB_ALT,
                                        HARD_RX as SOURCE_HARD_RX, names_source, strip_named_sources)
except Exception:   # the belt below carries its own vendor list as the fallback
    SOURCE_HARD_ALT = SOURCE_CASE_ALT = SOURCE_PHRASE_ALT = SOURCE_ATTRIB_ALT = ''
    SOURCE_HARD_RX = None

    def names_source(text):
        return False

    def strip_named_sources(text):
        return str(text or '')

# -------------------------------------------------------- method language
# 2026-10-05 (Jenna): "never mention anything that sounds synthetic".
# A reader never learns how a figure was made. First-person method talk
# and the words that imply a built number. Audience behaviour ("they
# research products before buying") is not method talk and is left alone.
# (was guards._METHOD_SENTENCE_RX)
METHOD_SENTENCE_RX = re.compile(
    r"(?:\b(?:we|i|crosswalk|prometheus|the (?:read|journey|figures?|numbers?|"
    r"counts?|profile|deck|data))\b[^.!?\n]{0,60}\b(?:researched|anchored|"
    r"modeled|modelled|estimated|synthesi[sz]ed|synthetic|derived|calibrated|"
    r"reasoned|simulated|extrapolated|imputed)\b)"
    r"|\bresearched (?:against|from|on its own|it)\b"
    r"|\banchored? (?:to|against|on)\b|\bre-?anchored\b"
    r"|\bplaceholders?\b|\bsynthetic\b|\bsynth(?:ed|s)?\b|\bmodel(?:ed|led)\b"
    r"|\bdefect\b|\bon my side\b|\bpipeline\b|\bhostmap\b|" + CLAUDE_SRC +
    r"|\bthe (?:corpus|ledger|catalog)\b|\bweb[- ]search\b|\bresearch(?:ed)? (?:call|pass|step)\b"
    r"|\bestimated\b|\bthe research\b|\bour research\b"
    # the adjective ("the researched count"), confidence-tier labels
    # (never public, 2026-09-14) and panel vocabulary (2026-10-13 audit)
    r"|\bresearched\b|\btiers? [12]\b|\bpanel(?:ist)?s?\b",
    re.IGNORECASE)
SENTENCE_SPLIT_RX = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'(\[])')

# Phrase swaps that keep the sentence's meaning; a sentence these clear
# is kept (its figures matter). (was guards._METHOD_SAFE_SWAPS)
METHOD_SAFE_SWAPS = (
    (re.compile(r'\bestimated audience\b', re.I), 'audience range'),
    (re.compile(r'\bresearched (universe|count|figure|number|total|base)\b', re.I), r'US \1'),
    (re.compile(r'\btier (\d)\b', re.I), r'group \1'),
    (re.compile(r'\bpanelists\b', re.I), 'people'),
    (re.compile(r'\bpanelist\b', re.I), 'person'),
    (re.compile(r'\bthe panel\b', re.I), 'the data'),
    (re.compile(r'\bpanel\b', re.I), 'data'),
    (re.compile(r'\bthe research and the path take\b', re.I), 'it takes'),
    (re.compile(r'\banchored to\b', re.I), 'in line with'),
    (re.compile(r'\bre-?anchored\b', re.I), 'updated'),
)
# Last resort when a paragraph would otherwise vanish. (was guards._METHOD_WORD_SUBS)
METHOD_WORD_SUBS = METHOD_SAFE_SWAPS + (
    (re.compile(r'\bresearched against\b', re.I), 'checked against'),
    (re.compile(r'\bresearched\b', re.I), 'read'),
    (re.compile(r'\b(?:estimated|modeled|modelled|synthetic|synthesized|synthesised) ', re.I), ''),
    (re.compile(r'\bplaceholders?\b', re.I), 'names'),
    (re.compile(r'\ba defect on my side\b', re.I), 'an error'),
    (re.compile(r'\bdefect\b', re.I), 'error'),
)

# The cause narrative (Jenna 2026-10-19): a reader never hears what
# happened, what we removed, what was wrong or what changed. The mail
# form is stricter (bugs, mistakes, apologies). (was guards._CAUSE_CORE)
CAUSE_CORE = (
    r"\bthe cause (?:was|is|of)\b|\bon our (?:side|end)\b|\broot cause\b|\bcorrect(?:ed|ion|ions|ing)\b|"
    r"\bre-?read\b|\bre-?r[au]n\b|\brebuil[dt]\b|\bre-?released?\b|\bflatten(?:ed|ing|s)?\b|\binvent(?:ed|ing|s)?\b|"
    r"\bpulled\b[^.!?\n]{0,40}\btoward\b|\bshould not have\b|\bshould have (?:shown|read|been|sat)\b|\bfrom the start\b|"
    r"\bwe (?:removed|fixed|patched|repaired|rewrote)\b|\bstep that ran\b|\bafter each build\b|\bwhat (?:we )?changed\b|"
    r"\bwhat happened\b|\bthe (?:fix|repair|patch)\b")
CAUSE_MAIL_EXTRA = r"|\bbugs?\b|\bmistakes?\b|\bour (?:error|fault)\b|\bapolog(?:y|ies|ize|ise|ized|ised)\b|\bwas wrong\b|\bglitch\b"
CAUSE_SENTENCE_RX = re.compile(CAUSE_CORE, re.IGNORECASE)
CAUSE_MAIL_RX = re.compile(CAUSE_CORE + CAUSE_MAIL_EXTRA, re.IGNORECASE)

# ------------------------------------------------------- sample vocabulary
# I5 (2026-10-20): the reader hears US people in plain words; the ten
# million product fact is the one reply that keeps its number.
# (was guards.SAMPLE_VOCAB_RX)
SAMPLE_VOCAB_RX = re.compile(
    r"\bsample check\b|\bsample size\b|\bsample\b|\bincidence\b|\b(?:10|ten) million\b|\bpanelists?\b|\bavid tier\b|\btrailing year\b|\bindividuals\b", re.I)

# A title or a proper name that carries the word ("Sample the album", the
# film "Sample", "Free Sample Friday") is never our sizing noun and is
# never rewritten (Jenna 2026-11-20). The name is the capital S form that
# is not one of our own: not the sizing line ("Sample: 12,345"), not the
# compounds the pass rewrites on their own ("Sample check", "Sample size",
# "Sample count"), not "Sample of <n>". Lower-case sample stays ours.
SAMPLE_NAME_RX = re.compile(
    r"(?<!\w)Samples?\b(?![-:])(?!\s+(?:(?i:check|sizes?|counts?)\b|(?i:of)\s+(?:~|(?i:about|roughly)\s+)?\d))")
_SAMPLE_NAME_TOKEN = 'xqnameholdq{i}qx'
_SAMPLE_NAME_TOKEN_RX = re.compile(r'xqnameholdq(\d+)qx')


def hold_sample_names(text):
    """Swap every Sample-as-a-name for a token the sample pass cannot
    read; ``release_sample_names`` puts them back exactly."""
    t = str(text or '')
    held = []

    def _take(m):
        held.append(m.group(0))
        return _SAMPLE_NAME_TOKEN.format(i=len(held) - 1)
    return SAMPLE_NAME_RX.sub(_take, t), held


def release_sample_names(text, held):
    if not held:
        return text
    return _SAMPLE_NAME_TOKEN_RX.sub(lambda m: held[int(m.group(1))] if int(m.group(1)) < len(held) else m.group(0), str(text or ''))

# ------------------------------------------------------- deliverable jargon
# Labels the engine coins for itself never reach a reader (2026-10-13).
# (was guards._JARGON_SUBS)
JARGON_SUBS = (
    (re.compile(r'\bpixel[- ]miss(?:es|ed)?(?: class| cohort)?\b', re.I), 'watched on a service they already had'),
    (re.compile(r'\b(?:the )?pixel[- ]miss(?:ed)? (?:class|group|cohort)\b', re.I), 'people who watched on a service they already had'),
    (re.compile(r'\bcohort[- ]id\b', re.I), 'group'),
    (re.compile(r'\bthe (\w+) partition\b', re.I), r'the \1 split'),
    (re.compile(r'\bpartitioned\b', re.I), 'split'),
    (re.compile(r'\bdeduped?\b', re.I), 'counted once'),
    (re.compile(r'\bnon-?converters?\b', re.I), 'people who did not watch'),
    (re.compile(r'\bconverters?\b', re.I), 'people who watched'),
    (re.compile(r'\bterminal (?:step|stage|event)\b', re.I), 'last step'),
    (re.compile(r'\bpenultimate (?:step|stage)\b', re.I), 'step before last'),
)

# Keys in a payload that carry identifiers, URLs and dates: never scrubbed.
# (was guards._SCRUB_SKIP_KEYS)
SCRUB_SKIP_KEYS = frozenset((
    'url', 'href', 'key', 's3_key', 'id', 'job_id', 'image', 'hero_image', 'thumbnail',
    'filename', 'filename_stem', 'slug', 'etag', 'source_key', 'created_by', 'created_at',
    'start_date', 'end_date', 'as_of', 'story_mode', 'target_type', 'kind', 'type', 'layout',
    'surface', 'ground', 'accent', 'color', 'hex', 'anchored_to', 'replayed_from',
))

# ---------------------------------------------------------- infrastructure
# Hard infrastructure words that must never reach an API consumer even if
# an upstream string slipped. (was envelope._INFRA_RX)
INFRA_RX = re.compile(
    r'\b(hetzner|clickhouse|systemd|traceback|anthropic|openai)\b|' + CLAUDE_SRC +
    r'|/root/|finished_codes|\.py:', re.I)

# ------------------------------------------------------------ partner belt
# 2026-10-14 (Jenna): "a client using it would NEVER see that anything is
# synthetic or external sources used". Substrings (lower-case) that never
# reach a key caller. Shared with app._V1_BANNED_TOKENS through
# guards.PARTNER_BANNED_TOKENS. (was guards.PARTNER_BANNED_TOKENS)
PARTNER_BANNED_TOKENS = (
    # model vendors and model names
    'claude', 'anthropic', 'opus', 'sonnet', 'openai', 'gpt', 'llm', 'ai stage', 'ai step',
    # how a figure was made
    'synth', 'synthesis', 'synthesize', 'synthesized', 'synthesised', 'synthetic',
    'modeled', 'modelled', 'estimated', 'estimate ', 'simulated', 'extrapolated', 'imputed',
    'calibrated', 'placeholder', 'researched', 'anchored', 'anchor value', 'web search', 'web-search',
    'search results', 'public data', 'publicly available', 'external data', 'external source',
    'third-party data', 'third party data', 'open-source data', 'according to ', 'as reported by',
    'row-by-row', 'row by row', 'row scoring', 'reasoning', 'chunk',
    # internal machinery
    'hostmap', 'enforcer', 'depin', 'gen_pop', 'gen pop', 'panelist', 'panel', 'category signal',
    'persona_doc', 'persona doc', 'persona brief', 'brief-interpret', 'brief interpret',
    'tu cohort', 'tu_cohort', 'tu build', 'tu_build', 'avid cohort', 'avid_cohort', 'cohort',
    'cut_done', 'tu_done', 'avid_done', 'pipeline', 'the corpus', 'the ledger', 'the catalog',
    'confidence tier', 'tier 1', 'tier 2',
    # infrastructure
    'hetzner', 'render', 'clickhouse', 'systemd', 's3://', 'dashboard-inputs',
    # crash mechanics
    'worker', 'attempt ', 'traceback', 'exception', 'stack trace', 'nameerror', 'typeerror', 'keyerror',
    # file paths, module names, config knobs
    '/root/', 'finished_codes', 'migration/', '.py:', 'api_key', 'anthropic_api',
    # library index internals (2026-11-23): store and source names
    'partner_library', 'profile_indexes', 's3_cache', 'quick_selects',
)

# Attribution clauses: the figure stays, the "according to X" goes.
# (was guards._PARTNER_SOURCES; the shared name list is appended)
PARTNER_SOURCES = (
    r'statista|nielsen|pew(?: research)?|comscore|similarweb|sensor tower|data\.ai|emarketer|yougov|'
    r'box office mojo|the-numbers\.com|deadline|variety|the hollywood reporter|wikipedia|imdb(?:pro)?|google trends|'
    r'sec filings?|10-k|earnings (?:reports?|calls?)|press reports?|trade press|industry reports?|'
    r'public(?:ly available)? (?:data|figures|reports?|sources?)|external (?:data|sources?)|third[- ]party (?:data|sources?)'
)
if SOURCE_ATTRIB_ALT:
    PARTNER_SOURCES = PARTNER_SOURCES + r'|' + SOURCE_ATTRIB_ALT
PARTNER_ATTRIB_RX = re.compile(
    r'(?:,\s*)?\b(?:according to|as reported by|reported by|per|sourced from|based on|citing|via|from)\s+'
    r'(?:the\s+|a\s+|an\s+)?(?:' + PARTNER_SOURCES + r')(?:\s+(?:data|figures|reports?|numbers|counts|estimates?))?\b[^.;,\n]*', re.I)
PARTNER_SOURCE_LINE_RX = re.compile(r'^\s*\(?\s*sources?\s*:[^\n]*$', re.I | re.M)
PARTNER_PAREN_SOURCE_RX = re.compile(r'\s*\((?:source|sources|per|via|according to)\b[^)]*\)', re.I)
PARTNER_SOURCE_INLINE_RX = re.compile(r'\(?\bsources?\s*:\s*[^.;\n)]*[.;)]?\s*', re.I)
PARTNER_CLAUSE_RX = re.compile(r'(?<=[.!?;])\s+')
PARTNER_WORD_SWAPS = METHOD_WORD_SUBS + (
    (re.compile(r'\b(?:US |us )?gen[ _-]?pop(?:ulation)?\b', re.I), 'US population'),
    (re.compile(r'\b(?:web|internet) search(?:es)?\b', re.I), 'our data'),
    (re.compile(r'\bsearch results\b', re.I), 'our data'),
    (re.compile(r'\b(?:public|publicly available|external|third[- ]party|open[- ]source) (?:data|sources?|figures|reports?)\b', re.I), 'our data'),
    (re.compile(r'\bsimulated\b', re.I), 'measured'),
    (re.compile(r'\bextrapolated\b', re.I), 'projected'),
    (re.compile(r'\bimputed\b', re.I), 'filled in'),
    (re.compile(r'\bcalibrated\b', re.I), 'checked'),
    (re.compile(r'\bcohorts\b', re.I), 'audiences'),
    (re.compile(r'\bcohort\b', re.I), 'audience'),
    (re.compile(r'\bthe (?:corpus|ledger|catalog)\b', re.I), 'our library'),
    (re.compile(r'\bpipeline\b', re.I), 'process'),
    (re.compile(r'\bconfidence tiers?\b', re.I), 'readings'),
    (re.compile(r'\bresearch(?:es|ing)?\b', re.I), 'read'),
    (re.compile(CLAUDE_SRC + r'|\b(?:anthropic|openai|gpt|llm)\b', re.I), 'Prometheus'),
)
# swaps that never change a sentence's meaning, run before the clause pass
PARTNER_PLAIN_SWAPS = (
    (re.compile(r'\b(?:US |us )?gen[ _-]?pop(?:ulation)?\b', re.I), 'US population'),
    (re.compile(r'\bpanelists\b', re.I), 'people'),
    (re.compile(r'\bpanelist\b', re.I), 'person'),
    (re.compile(r'\bcohorts\b', re.I), 'audiences'),
    (re.compile(r'\bcohort\b', re.I), 'audience'),
)
PARTNER_VERB_SWAPS = (
    (re.compile(r'\b(?:estimated|modell?ed|simulated|extrapolated|projected out|imputed)\s+(?=(?:about |around |roughly |some )?[\d$])', re.I), 'counted '),
    (re.compile(r'\b(?:researched|anchored|calibrated|modell?ed|estimated)\b', re.I), 'read'),
)
# Words that may legitimately appear as brands or ordinary English and
# must not count as a violation on their own.
PARTNER_TOKEN_SKIP = frozenset(('render', 'reasoning', 'estimate ', 'chunk', 'the catalog', 'the ledger',
                                'the corpus', 'cohort', 'attempt ', 'exception', 'according to ', 'as reported by'))
PARTNER_HARD_RX = re.compile(
    CLAUDE_SRC + r'|' + OPUS_SRC + r'|' + SONNET_SRC + r'|'
    r'\b(?:anthropic|openai|gpt-?\d?|llm|synth(?:etic|esis|esized|esised|ed|s)?|hostmap|enforcer|depin|'
    r'gen[_ ]pop|panelists?|hetzner|clickhouse|systemd|traceback|stack trace|nameerror|typeerror|keyerror|'
    r'web[- ]search(?:es)?|placeholders?|modell?ed|simulated|extrapolated|imputed|tier [12])\b'
    r'|s3://|/root/|finished_codes|migration/|\.py:|api_key|anthropic_api|dashboard-inputs', re.I)
PARTNER_INFRA_RX = re.compile(r'\b(hetzner|clickhouse|systemd|traceback|anthropic|openai)\b|' + CLAUDE_SRC + r'|/root/|finished_codes|\.py:|s3://', re.I)
# Data vendors are never an audience brand in our files; their name in a
# clause is an attribution however it is phrased.
PARTNER_VENDOR_RX = SOURCE_HARD_RX or re.compile(
    r'\b(?:statista|nielsen|comscore|similarweb|sensor tower|data\.ai|emarketer|yougov|mri[- ]simmons|'
    r'google trends|box office mojo|the-numbers\.com|parrot analytics|samba tv|ispot(?:\.tv)?|kantar|gwi|'
    r'global ?web ?index|luminate|circana|npd group|magid)\b', re.I)
PARTNER_SKIP_KEYS = SCRUB_SKIP_KEYS | frozenset((
    'trace_id', 'conversation_id', 'request_id', 'idempotency_key', 'schema', 'poll', 'endpoint', 'send',
    'unit', 'product', 'reason', 'action', 'status', 'basis', 'webhook_url', 'next', 'window'))

# ------------------------------------------------------------- mail check
# Words that tell a reader the figures were built, or name our machinery;
# a body that still carries one after the scrub is refused, never sent.
# Shared by outbound_mail and scripts/pm_correction_email.
# (was outbound_mail.BANNED)
MAIL_BANNED = ('synth', 'modeled', 'estimated', 'pipeline', 'hostmap',
               'panel', 'coefficient', 'odds ratio', 'regression', 'logit',
               'claude', 'hetzner', 'clickstream', 'gen pop', 'genpop',
               'research', 'anchor', 'placeholder', 'defect', 'synthetic',
               'reasoned', 'derived from', 'calibrat',
               '\u2014', '\u2013')


def mail_banned_tokens(text):
    low = str(text or '').lower()
    return [b for b in MAIL_BANNED if b in low]


# ------------------------------------------------------------ twin notices
# words that name our machinery; the twin notice templates never use them
# (was twin_notify.BANNED_RX)
TWIN_NOTICE_RX = re.compile(r"\b(synth\w*|model\w*|panel\w*|sample\w*|pipeline\w*|queue\w*|worker\w*|claude|anthropic|hostmap|persona\w*|s3)\b", re.I)

# ------------------------------------------------------ knowledge leak gate
# The compiled knowledge pack is read by the model, never by a reader, but
# a canon section that carried one of these would teach the model the word.
# scripts/build_prometheus_knowledge_pack.py (parent repo) reads this list
# when the package is importable and keeps its own copy as the fallback.
LEAK_GATE_PATTERNS = (
    ('synth family', r'\bsynth\w*\b'),
    ('pipeline', r'\bpipelines?\b'),
    ('hostmap', r'\bhostmap\w*\b'),
    ('enforcer', r'\benforcers?\b'),
    ('depin', r'\bdepin\w*\b'),
    ('jitter', r'\bjitter\w*\b'),
    ('multiplier', r'\bmultipliers?\b'),
    ('panel family', r'\bpanel\w*\b'),
    ('modeled', r'\bmodell?ed\b'),
    ('estimate family', r'\bestimat\w*\b'),
    ('derive family', r'\bderiv\w*\b'),
    ('extrapolate', r'\bextrapolat\w*\b'),
    ('interpolate', r'\binterpolat\w*\b'),
    ('model name: claude', r'\bclaude\b'),
    ('model name: anthropic', r'\banthropic\b'),
    ('model name: opus', r'\bopus\b'),
    ('model name: sonnet', r'\bsonnet\b'),
    ('model name: gpt', r'\bgpt[-\w.]*\b'),
    ('model name: openai', r'\bopen\s?ai\b'),
    ('model name: llm', r'\bllms?\b'),
    ('model name: gemini', r'\bgemini\b'),
    ('infra: hetzner', r'\bhetzner\b'),
    ('infra: clickhouse', r'\bclickhouse\b'),
    ('infra: systemd', r'\bsystemd\b'),
    ('infra: gunicorn', r'\bgunicorn\b'),
)

# ------------------------------------------------------------- held lines
# The views methodology may say how Nielsen and Peacock count a view
# (Jenna 2026-11-11), as a definition, never as a source. Those exact
# strings are the only sentences in the product that may name a ratings
# service; the exit holds them out before its first pass and puts them
# back byte for byte after its last.


def allowlist():
    """The exact lines held out of every pass: how Nielsen and Peacock count
    a view (methodology.OTHERS_ALLOWLIST) and the house lines below."""
    try:
        from prometheus import methodology as _meth
        return tuple(_meth.OTHERS_ALLOWLIST) + house_lines()
    except Exception:
        return house_lines()


# House lines: exact sentences of the product's own that a pass would
# otherwise misread (stage 4 template check, 2026-11-20). The provenance
# answer says "Crosswalk's own first-party data" and names in-store
# purchases as outside what is reported; the sizing definition says
# counts "are not accounts, households or impressions"; the income
# overlay says what income means. Each is the contrast or the definition
# the standing rules allow, in the words Jenna approved. Held out by exact
# text, never by pattern: a sentence that says any of this in other words
# is still scrubbed.
_HOUSE_TOKEN = 'xqhouseq{}qx'
_HOUSE_TOKEN_RX = re.compile(r'xqhouseq(\d+)qx', re.I)   # a scrub may capitalize a sentence it re-joins
# (module, name): the name is the line, or a no-argument function that returns it
HOUSE_LINE_SOURCES = (
    ('prometheus.guards', 'PROVENANCE_REPLY'),            # "Crosswalk's own first-party data", in-store outside it
    ('prometheus.methodology', 'PEOPLE'),                 # "not accounts, households or impressions"
    ('prometheus.segment_sizing', 'INCOME_LINE'),         # "Income is household income." (the HHI overlay)
    ('prometheus.methodology_answers', 'age_source_line'),  # "never a household guess"
)
_HOUSE_CACHE = {}


def house_lines():
    """The exact house lines, read once per process."""
    if 'lines' in _HOUSE_CACHE:
        return _HOUSE_CACHE['lines']
    import importlib
    out = []
    for modname, attr in HOUSE_LINE_SOURCES:
        try:
            val = getattr(importlib.import_module(modname), attr)
            if callable(val):
                val = val()
            if isinstance(val, str) and val.strip():
                out.append(val)
        except Exception:
            continue
    lines = tuple(out)
    if len(lines) == len(HOUSE_LINE_SOURCES):     # cache only a complete read
        _HOUSE_CACHE['lines'] = lines
    return lines


def hold(text):
    """(text with the held lines replaced by inert tokens, held). The
    view-definition lines ride on methodology's own shield; the house
    lines on this module's. ``held`` is what release() needs."""
    t = str(text or '')
    try:
        from prometheus import methodology as _meth
        t, held_defs = _meth.shield_definitions(t)
    except Exception:
        held_defs = []
    held_house = []
    try:
        lines = house_lines()
        if any(s[:-1] in t for s in lines):
            t = _HOUSE_TOKEN_RX.sub('', t)        # a token is only ever ours
            for i, s in enumerate(lines):
                core = s[:-1] if s.endswith('.') else s
                if core in t:
                    t = t.replace(core, _HOUSE_TOKEN.format(i))
                    held_house.append(i)
    except Exception:
        held_house = []
    if not held_defs and not held_house:
        return t, []
    return t, {'defs': held_defs, 'house': held_house}


def release(text, held):
    """The held lines back, byte for byte."""
    t = str(text or '')
    if not held:
        return t
    if isinstance(held, list):          # an older caller holding methodology's list alone
        held = {'defs': held, 'house': []}
    house = held.get('house') or []
    if house:
        lines = house_lines()

        def _back(m):
            i = int(m.group(1))
            if i in house and 0 <= i < len(lines):
                s = lines[i]
                return s[:-1] if s.endswith('.') else s
            return ''
        t = _HOUSE_TOKEN_RX.sub(_back, t)
    defs = held.get('defs') or []
    if defs:
        try:
            from prometheus import methodology as _meth
            t = _meth.unshield_definitions(t, defs)
        except Exception:
            pass
    return t


# ------------------------------------------------------------- the checker
# What a reader must never see, as one function. Used by exit.check, the
# template test and the property tests. A kind decides which families
# apply; NAME is never checked (a brand may be called anything).
_HH_RX = re.compile(r'\bHHs?\b(?![:\d])|\bper[- ]HH\b|\bhouseholds?\b', re.I)
_METHOD_WORD_RX = re.compile(
    r'\b(?:synth\w*|pipelines?|hostmap\w*|enforcers?|modell?ed|estimated?|estimates|derived|extrapolated|'
    r'interpolated|panel(?:ist)?s?|hetzner|clickhouse|systemd|'
    r'penetration_pct|share_pct|placeholders?|calibrated|imputed|simulated)\b|' + MODEL_NAME_SRC, re.I)


def hits(text, kind=PROSE):
    """The words in ``text`` a reader of this ``kind`` must never see,
    after the pipeline has run. Empty means clean."""
    if kind == NAME:
        return []
    t, held = hold(str(text or ''))
    t, _names = hold_sample_names(t)
    out = []
    if DASH_RX.search(t):
        out.append('dash')
    if kind == BRIEF_FIELD:
        if SAMPLE_VOCAB_RX.search(t):
            out.extend(m.group(0) for m in SAMPLE_VOCAB_RX.finditer(t))
        return out
    if SOURCE_HARD_RX is not None and SOURCE_HARD_RX.search(t):
        out.extend(m.group(0) for m in SOURCE_HARD_RX.finditer(t))
    out.extend(m.group(0) for m in _METHOD_WORD_RX.finditer(t))
    out.extend(m.group(0) for m in _HH_RX.finditer(t))
    if INFRA_RX.search(t):
        out.extend(m.group(0) for m in INFRA_RX.finditer(t))
    if kind in (PROSE, DECK_COPY, MAIL, CHIP):
        try:
            from prometheus import guards as _g
            out.extend('method sentence: ' + s.strip()[:60] for s in _g.method_language(t))
        except Exception:
            pass
    if kind == MAIL:
        out.extend(b for b in mail_banned_tokens(t) if b not in out)
    if kind == PARTNER:
        low = t.lower()
        out.extend(tok for tok in PARTNER_BANNED_TOKENS if tok in low and tok not in PARTNER_TOKEN_SKIP)
    return out


__all__ = [
    'PROSE', 'NAME', 'CHIP', 'BRIEF_FIELD', 'DECK_COPY', 'PARTNER', 'FILENAME', 'MAIL', 'KINDS',
    'DASHES', 'DASH_RX', 'WORD_SWAPS', 'WORD_SWAPS_COMPILED', 'HH_SWAPS', 'HH_SWAPS_COMPILED', 'ESTIMATE_NOUN_RX',
    'swap_words', 'swap_hh', 'swap_estimate_noun',
    'CITATION_RX', 'OFFCLICK_RX', 'CLAUDE_SRC', 'OPUS_SRC', 'SONNET_SRC', 'MODEL_NAME_SRC', 'MODEL_NAME_RX',
    'SOURCE_HARD_ALT', 'SOURCE_CASE_ALT', 'SOURCE_PHRASE_ALT', 'SOURCE_ATTRIB_ALT', 'SOURCE_HARD_RX',
    'names_source', 'strip_named_sources',
    'METHOD_SENTENCE_RX', 'SENTENCE_SPLIT_RX', 'METHOD_SAFE_SWAPS', 'METHOD_WORD_SUBS',
    'CAUSE_CORE', 'CAUSE_MAIL_EXTRA', 'CAUSE_SENTENCE_RX', 'CAUSE_MAIL_RX',
    'SAMPLE_VOCAB_RX', 'SAMPLE_NAME_RX', 'hold_sample_names', 'release_sample_names', 'JARGON_SUBS', 'SCRUB_SKIP_KEYS', 'INFRA_RX',
    'PARTNER_BANNED_TOKENS', 'PARTNER_SOURCES', 'PARTNER_ATTRIB_RX', 'PARTNER_SOURCE_LINE_RX',
    'PARTNER_PAREN_SOURCE_RX', 'PARTNER_SOURCE_INLINE_RX', 'PARTNER_CLAUSE_RX', 'PARTNER_WORD_SWAPS',
    'PARTNER_PLAIN_SWAPS', 'PARTNER_VERB_SWAPS', 'PARTNER_TOKEN_SKIP', 'PARTNER_HARD_RX', 'PARTNER_INFRA_RX',
    'PARTNER_VENDOR_RX', 'PARTNER_SKIP_KEYS',
    'MAIL_BANNED', 'mail_banned_tokens', 'TWIN_NOTICE_RX', 'LEAK_GATE_PATTERNS',
    'allowlist', 'hold', 'release', 'hits',
]
