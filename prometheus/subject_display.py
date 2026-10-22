"""The human display of a subject, never an internal key.

2026-10-22 (Casey Pearson's Jackass: Best & Last thread): the counting
block under a viewership read printed "Base: the BEST_OF_THE_BEST
audience". The label came from the catalog entry's `profile_subject`,
which on 137 files is the upper-snake file stem (BEST_OF_THE_BEST,
2_CHAINZ, ARIANA_GRANDE) rather than the display name, and the base
pick carried it straight into the reply. An internal identifier is
never a name a reader sees.

Three places share this one rule:

- the chat catalog (`_profile_catalog_for_chat`) takes the display
  name over a stem-shaped `profile_subject`;
- the counting block (`methodology.block`) renders every base label
  through `display()` and prefers the audience the read is about;
- the vocabulary scrub (`scrub_user_text`) humanizes any key shape
  that still reaches the text, outside URLs, so it cannot ship from
  any lane.
"""
import re

__all__ = ['KEY_RX', 'STAMP_RX', 'looks_like_key', 'humanize_key',
           'display', 'scrub_keys']

# UPPER_SNAKE with at least one underscore: BEST_OF_THE_BEST, 2_CHAINZ,
# PARAMOUNT_PLUS_SUBSCRIBER_LIFECYCLE. A plain acronym (NFL, HBO) is not
# a key.
KEY_RX = re.compile(r"(?<![A-Za-z0-9_])[A-Z0-9][A-Z0-9+&'.-]*(?:_[A-Z0-9][A-Z0-9+&'.-]*)+(?![A-Za-z0-9_])")
# A dated profile stem or key: Paramount+_05_29_2026_23_00(.csv),
# Simu_Liu_10_20_2026_12_00. The stem is the subject; the stamp is not.
STAMP_RX = re.compile(r"(?<![A-Za-z0-9_/])([A-Za-z0-9][A-Za-z0-9+&'.-]*(?:_[A-Za-z0-9][A-Za-z0-9+&'.-]*)*?)"
                      r"_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}(?:\.csv)?(?![A-Za-z0-9_])")
# Any underscored stem with no spaces (Michael_B_Jordan, Simu_Liu) is a
# key when it stands alone as a subject; inside prose only the upper
# and dated shapes are rewritten (a lower snake handle is left alone).
_STEM_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9+&'.-]*(?:_[A-Za-z0-9][A-Za-z0-9+&'.-]*)+")
_URL_RX = re.compile(r"https?://[^\s)\]>]+|\([^)\s]*/[^)\s]*\)")
_SMALL = {'of', 'the', 'and', 'in', 'on', 'at', 'for', 'to', 'a', 'an', 'or', 'by', 'vs', 'with', 'de', 'la', 'y'}
# Short all-caps tokens stay as written (ABC, NFL, CBS, UFC, MTV, DC).
_KEEP_CAPS_LEN = 3


def looks_like_key(s):
    """True for an upper-snake key or a dated file stem."""
    s = str(s or '').strip()
    if not s:
        return False
    if s.lower().endswith('.csv'):
        return True
    return bool(KEY_RX.fullmatch(s) or STAMP_RX.fullmatch(s)
                or _STEM_RX.fullmatch(s))


def _word(tok, first):
    if tok.lower() in _SMALL and not first:
        return tok.lower()
    if tok.isupper() and len(tok) <= _KEEP_CAPS_LEN and tok.isalpha():
        return tok
    if tok.isupper() or tok.islower():
        return tok[:1].upper() + tok[1:].lower()
    return tok


def humanize_key(s):
    """BEST_OF_THE_BEST -> Best of the Best; 2_CHAINZ -> 2 Chainz;
    ABC_NEWS -> ABC News; Paramount+_05_29_2026_23_00.csv -> Paramount+."""
    s = str(s or '').strip()
    if not s:
        return s
    m = STAMP_RX.fullmatch(s)
    if m:
        s = m.group(1)
    if s.lower().endswith('.csv'):
        s = s[:-4]
    parts = [p for p in s.split('_') if p]
    if not parts:
        return s
    if len(parts) == 1 and not parts[0].isupper():
        return parts[0]
    return ' '.join(_word(p, i == 0) for i, p in enumerate(parts))


def display(subject, fallback=''):
    """The name a reader sees: `fallback` (a display name) when the
    subject is key-shaped and a fallback exists, else the humanized
    subject. Never returns a key shape."""
    subj = str(subject or '').strip()
    fb = str(fallback or '').strip()
    if not subj:
        return humanize_key(fb) if looks_like_key(fb) else fb
    if looks_like_key(subj):
        if fb and not looks_like_key(fb):
            return fb
        return humanize_key(subj)
    return subj


def _scrub_segment(seg):
    seg = STAMP_RX.sub(lambda m: humanize_key(m.group(0)), seg)
    seg = KEY_RX.sub(lambda m: humanize_key(m.group(0)), seg)
    return seg


def scrub_keys(text):
    """Humanize every key shape in `text` outside URLs and link
    targets. Never raises; returns the input on failure."""
    try:
        s = str(text or '')
        if not s or not ('_' in s):
            return text
        out, pos = [], 0
        for m in _URL_RX.finditer(s):
            out.append(_scrub_segment(s[pos:m.start()]))
            out.append(m.group(0))
            pos = m.end()
        out.append(_scrub_segment(s[pos:]))
        return ''.join(out)
    except Exception:
        return text
