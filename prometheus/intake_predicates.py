"""Intake predicates (moved out of the legacy chat module 2026-10-20 to
keep it under its line ratchet; behavior unchanged).

- is_incidence_request: the message is a sample-size / incidence QUESTION,
  not a build instruction and not a consumption count.
- is_discovery_request: the message states a business goal without an
  audience, so the reader needs help choosing what to build.
"""
import re


def is_incidence_request(text):
    """True when the message is a sample-size / incidence QUESTION,
    not a build instruction. Tuned for roundabout phrasings.
    """
    t = (text or '').strip().lower()
    if not t or len(t) > 600:
        return False
    # Negative gates: explicit sample-size INSTRUCTIONS are build
    # parameters, not questions.
    #   "use a sample size of 50,000" / "set the sample to 40k" /
    #   "the sample size should be 25000"
    if re.search(r'\b(use|set|with|lock|make)\b[^.?!]{0,40}'
                  r'\bsample( |-)?size\b', t):
        return False
    if re.search(r'\bsample( |-)?size\b[^.?!]{0,20}'
                  r'\b(of|should be|=|:)?\s*[\d,]{4,}', t):
        return False
    # A consumption count is a viewership read, never a sample check
    # (2026-10-20, Marie Flores at WBD: "how many people watched
    # Everybody Loves Raymond on Prime Video in September 2026 ... by
    # platform as a table" drew the sample-check card and an empty
    # answer). Same guard the sizing lane carries: "how many people
    # watched X" prices as a viewership read unless the ask names the
    # sample, the incidence or the data set itself.
    if re.search(r'\bhow many\b[^.?!]{0,60}\b(?:watch(?:ed|es|ing)?'
                  r'|view(?:ed|s|ing)?|stream(?:ed|s|ing)?|read|reads'
                  r'|listen(?:ed|s|ing)?|play(?:ed|s|ing)?|bought|buy'
                  r'|buys|purchased|downloaded|binged?|tuned in'
                  r'|subscribed?)\b', t) \
            and not re.search(r'\b(?:sample|incidence|data ?set)\b', t):
        return False
    strong = re.search(
        r'\bincidence\b'
        r'|\bsample( |-)?size\b'
        r'|\bsample check\b|\bcheck (the |a |our )?sample\b'
        r'|\bhow (many|big|large|much)\b[^.?!]{0,80}'
        r'\b(panel(ist)?s?|sample|audience|people|respondents|n)\b'
        r'|\b(do|would|will|did) we have enough\b'
        r'|\benough (panel(ist)?s?|sample|data|people|respondents)\b'
        r'|\bis the (sample|panel|n|audience|base) '
        r'(big |large )?enough\b'
        r'|\bpanel(ist)? count\b|\bhow many panel(ist)?s\b'
        r'|\bfeasib(le|ility)\b'
        r'|\bn( |-)?size\b|\bwhat(\'| i)?s the n\b',
        t)
    return bool(strong)


def is_discovery_request(text):
    """True when the message states a business goal (pitch, meeting,
    deck, client) WITHOUT defining an audience - the case where the
    user needs help figuring out what kind of profile to build.
    Direct build asks return False so they behave exactly as today.
    """
    t = (text or '').strip().lower()
    if not t or len(t) > 500:
        return False
    # Direct build ask with an audience attached -> NOT discovery.
    if re.search(r'\b(build|run|make|create|pull|queue|launch|refresh)'
                  r'\b[^.?!]{0,40}\b(profile|cut|audience|cohort)s?\b'
                  r'\s+(of|on|for)\s+\S', t):
        return False
    # Explicit audience descriptors -> they already know the cut.
    if re.search(r'\b(viewers|buyers|purchasers|shoppers|subscribers|'
                  r'listeners|owners|fans|users|moms|dads|parents)\b'
                  r'[^.?!]{0,30}\b(of|who|for)\b', t):
        return False
    pitch = re.search(
        r"\b(i'?m|im|we'?re|were|i am|we are)\s+pitching\b"
        r"|\bpitching\s+[a-z0-9]"
        r"|\bprepping\s+(a\s+|the\s+)?(deck|pitch|meeting|presentation)\b"
        r"|\b(meeting|presenting)\s+(with|to)\s+[a-z0-9]"
        r"|\brfp\s+(for|from)\b"
        r"|\b(new\s+)?(client|prospect)\s+is\s+[a-z0-9]"
        r"|\bwhat\s+(kind|type)\s+of\s+profile\b"
        r"|\bhelp\s+me\s+figure\s+out\s+(what|which|who)\b"
        r"|\bwho\s+should\s+(we|i)\s+(profile|pull|target)\b",
        t)
    return bool(pitch)
