#!/usr/bin/env python3
"""match_gate.py — one shared relevance gate for StreamScout's title matching.

Every resolver that finds a show/podcast/channel by fuzzy search used to do the
same unsafe thing:

    best = max(candidates, key=similarity)
    return best            # <- no floor: the least-bad candidate always "wins"

…and then enumerated that match's ENTIRE catalog. So a search for *"Lady Miss
Jacqueline"* happily returned the whole *"The Lady Vanishes"* podcast (they share
only the word "lady") and dumped 1,400+ episodes — the over-match that polluted a
profile with junk.

``is_relevant(query, candidate)`` is the precision floor that stops that. A
candidate is accepted only when it plausibly *is the same title* as the query:
its distinctive (non-structural) words genuinely cover the query, or it's a clear
subset/superset, or the raw strings are near-identical. A lone shared common word
is rejected. When nothing clears the bar the resolver returns "no match" — which
is correct (the show simply isn't on that platform) and lets Prometheus fall to
its "specialized / needs help" branch instead of ingesting garbage.
"""
import difflib
import re

# Structural / generic words that carry no identifying signal for a title, so a
# match on these alone must NOT count. (Kept deliberately tight — real title
# words like "baby", "crime", "daily" stay in.)
_STOP = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with", "by",
    "at", "from", "this", "is", "it", "its", "show", "shows", "podcast",
    "podcasts", "series", "saga", "trilogy", "part", "vol", "volume", "season",
    "episode", "episodes", "ep", "audiobook", "audiobooks", "book", "official",
    "feat", "featuring", "ft", "tv", "radio", "network", "live", "original",
    "originals", "stories", "story", "presents",
}
_WORD = re.compile(r"[a-z0-9]+")

# Non-base FORMAT variants (dramatized adaptations, graphic novels) and
# third-party DERIVATIVES (study guides, summaries, fan fiction, companions).
# These pass is_relevant() because they carry the real title, yet they are NOT
# the actual work a Reader/Listener consumes — so resolvers drop them. NOTE: a
# legitimate foreign-language EDITION of the real title is *not* flagged here
# (a German or French reader of the book is still a reader of it).
_VARIANT_DROP = re.compile(
    r"(?i)(?<![a-z])(?:"
    r"dramatized|graphic\s+novel|study\s+guide|summary|analysis|workbook|"
    r"companion|trivia|quiz|unofficial|fan\s?fic(?:tion)?|cliffs?notes|"
    r"sparknotes|conversation\s+starters"
    r")(?![a-z])")


def is_variant_or_derivative(name):
    """True if `name` is a non-base format variant (dramatized, graphic novel)
    or a third-party derivative (study guide, summary, fan fiction, companion).
    Legitimate foreign-language editions of the real title are NOT flagged."""
    return bool(_VARIANT_DROP.search(name or ""))


def distinctive(s):
    """Lower-cased, de-structured token set for a title."""
    return {t for t in _WORD.findall((s or "").lower()) if t not in _STOP}


def ratio(a, b):
    """Whole-string similarity (0..1), robust to small typos/word order."""
    return difflib.SequenceMatcher(
        None, (a or "").lower().strip(), (b or "").lower().strip()).ratio()


def is_relevant(query, candidate, *, min_cover=0.6, ratio_floor=0.8):
    """True if ``candidate`` is plausibly the SAME title/show as ``query``.

    Accept when any of these hold:
      * both sides share ≥2 distinctive words AND one is a subset of the other
        (e.g. "Lady Miss Jacqueline" ⊆ "The Weddings of Lady Miss Jacqueline"),
      * the candidate covers ≥ ``min_cover`` of the query's distinctive words,
      * the raw strings are ≥ ``ratio_floor`` similar (typo/word-order safety).

    Reject a lone shared common word ("lady", "marriage", "committee") — the
    exact failure mode that caused the catalog dumps.
    """
    q, c = distinctive(query), distinctive(candidate)
    if not q or not c:                      # nothing distinctive to compare on
        return ratio(query, candidate) >= ratio_floor
    inter = q & c
    if not inter:                           # zero distinctive overlap -> no
        return False
    # A clear subset either direction, backed by ≥2 shared distinctive words, is
    # the same title (query is the show, or the show is the query + a subtitle).
    if len(inter) >= 2 and (q <= c or c <= q):
        return True
    cover_q = len(inter) / len(q)
    return cover_q >= min_cover or ratio(query, candidate) >= ratio_floor


if __name__ == "__main__":       # quick self-check
    CASES = [
        # (query, candidate, expected)
        ("Lady Miss Jacqueline Series", "The Lady Vanishes", False),
        ("The Weddings of Lady Miss Jacqueline",
         "One Extraordinary Marriage Show", False),
        ("Lady Miss Jacqueline Series",
         "The Weddings of Lady Miss Jacqueline", True),
        ("Becoming Lady Miss Jacqueline", "Becoming Lady Miss Jacqueline", True),
        ("Bereavement Committee", "Bereavement Committee", True),
        ("From the Desk of Lady Miss",
         "From the Desk of Lady Miss Jacqueline", True),
        ("The Weddings of Lady Miss Jacqueline",
         "Alles wird Asche Ein Bodenstein Kirchhoff Krimi 12", False),
        ("Baby This Is Keke Palmer", "Baby This Is Keke Palmer", True),
        ("Bereavement Committee", "The Committee", False),
        ("Hades", "Hades", True),
    ]
    ok = True
    for q, c, exp in CASES:
        got = is_relevant(q, c)
        flag = "ok " if got == exp else "FAIL"
        if got != exp:
            ok = False
        print(f"  [{flag}] is_relevant({q!r}, {c!r}) = {got} (want {exp})")
    print("ALL PASS" if ok else "SOME FAILED")
