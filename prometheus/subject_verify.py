"""Which subject an ask names: the pending question key, the verify cache and pre-verify, the chat subject verify, the entity bind, the plausibility check, the fuzzy catalog match with its aliases and typo tolerance, and the two-base detect.

Moved out of prometheus/legacy/chat.py (rebuild stage 7, 2026-11-23) by
scripts/extract_legacy_core.py, behaviour unchanged. This module reads the
host through ``_H`` and the legacy chat namespace through ``_C``
(prometheus.legacy.C), both bound before it is imported; chat.py imports it
last and re-exports the moved names, so every name chat.py binds is read
through ``_C.<name>`` at call time exactly as it was when the code lived in
one file. ``_C.globals()`` is chat.py's own namespace, the one the lanes this
code hands it to expect. The suites read the moved text through
scripts/_pm_legacy_view.legacy_view(), which splices each block back at its
marker in chat.py.
"""
from prometheus.legacy import H as _H, C as _C  # noqa: E402,F401
import os
import threading
import traceback
import re
from prometheus import faults as _faults
from prometheus import subject_rules as _subject_rules
import time
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import seams as _seams, cohort_scope as _cohort_scope, shared_memory as _shared
from prometheus import named_subject as _ns_mod

# <<moved>>
# <<block: 1>>
_PM_PENDING_Q_S3_KEY = 'system/pm_pending_questions.json'


# <<block: 2>>
_PM_VERIFY_CACHE = {}


# <<block: 3>>
_PM_VERIFY_CACHE_LOCK = threading.Lock()


# <<block: 4>>
_PM_VERIFY_TTL_S = 24 * 3600


# <<block: 5>>
def _pm_verify_cached(subj, candidates):
    """(ok, subject, suggestion) for a subject, from the 24h in-process
    cache or the verification ladder (2026-10-06, speed: the same name
    is never web-verified twice in a day)."""
    key = re.sub(r'[^a-z0-9]+', ' ', str(subj or '').lower()).strip()
    now = time.time()
    with _C._PM_VERIFY_CACHE_LOCK:
        hit = _C._PM_VERIFY_CACHE.get(key)
        if hit and now - hit[0] < _C._PM_VERIFY_TTL_S:
            return hit[1]
    verify = getattr(_H, '_v1_subject_verified', None)
    if not callable(verify):
        return (True, subj, None)
    res = verify({'subject': subj, 'decision': 'new_build'}, 'new_build', None,
                 candidates=candidates)
    with _C._PM_VERIFY_CACHE_LOCK:
        _C._PM_VERIFY_CACHE[key] = (now, res)
        if len(_C._PM_VERIFY_CACHE) > 2000:
            for k in sorted(_C._PM_VERIFY_CACHE, key=lambda k: _C._PM_VERIFY_CACHE[k][0])[:500]:
                _C._PM_VERIFY_CACHE.pop(k, None)
    return res


# <<block: 6>>
def _pm_start_preverify(text, candidates):
    """Start the subject verification for the name the ask plainly
    carries while the draft model runs (2026-10-06, speed). Returns a
    Thread whose .result holds (guess, (ok, subj, suggestion)) or None."""
    try:
        if os.environ.get('PM_CHAT_SUBJECT_VERIFY', '1') == '0':
            return None
        import prometheus_analysis as _pma_pv
        guess = str(_pma_pv.guess_subject_from_text(text) or '').strip()
        # The ladder verifies the entity, never a wrapper phrase around
        # it ("the appeal of the Spiderwick Chronicles" -> the title).
        try:
            from prometheus import guards as _pg_pv
            core = str(_pg_pv.entity_core(guess) or '').strip()
            guess = core or guess
        except Exception as _fx:
            _faults.note('chat', _fx)
        if not guess or len(guess) < 2:
            return None
    except Exception:
        return None

    class _T(threading.Thread):
        result = None

        def run(self):
            try:
                self.result = (guess, _C._pm_verify_cached(guess, candidates))
            except Exception:
                self.result = None

    t = _T(daemon=True)
    t.start()
    return t


# <<block: 7>>
def _pm_chat_subject_verify(spec_draft, candidates, text, preverify=None, history=None, followup=None):
    """Run the subject-verification ladder on a fresh chat build.
    Returns a guidance response when the subject does not resolve,
    else None (2026-10-02 S6). Never raises; any failure is None.
    `preverify` is the thread _pm_start_preverify returned: when its
    guess is this draft's subject the result is reused (no second
    web check); otherwise the ladder runs through the 24h cache."""
    try:
        if os.environ.get('PM_CHAT_SUBJECT_VERIFY', '1') == '0':
            return None
        dec = str((spec_draft or {}).get('decision') or '')
        if dec not in ('new_build', 'cut_needs_parent'):
            return None
        _fu_ask = __import__('prometheus.followup_refine', fromlist=['settle_subject']).settle_subject(spec_draft, text, history, followup, _C._pm_ask_hint)   # a sentence is never looked up as a name: the thread's subject, or which title (2026-11-18)
        subj = str(spec_draft.get('subject') or '').split(' - ', 1)[0].strip()
        if not subj or _fu_ask:
            return (jsonify(_fu_ask), 400) if _fu_ask else None
        if spec_draft.get('_persona_universe') or spec_draft.get('ask_ip_scope') or spec_draft.get('clarify_question') \
                or _cohort_scope.apply_to_draft(spec_draft, text):   # compound cohort anchored on an entity (2026-10-12)
            return None
        # A category audience ("yoga clothing buyers", "craft beer
        # drinkers") is a persona universe: there is no entity to find.
        # The normalizer that marks the draft runs after this ladder, so
        # the same test is asked here (golden g044, 2026-11-02: "I could
        # not find Yoga Clothing as a brand, person, or title").
        try:
            _pca = getattr(_H, '_v1_persona_class_ask', None)
            if callable(_pca) and _pca(text):
                spec_draft['_persona_universe'] = True
                spec_draft['subject_verified'] = True
                return None
        except Exception:
            pass
        verify = getattr(_H, '_v1_subject_verified', None)
        if not callable(verify):
            return None
        res = None
        if preverify is not None:
            try:
                preverify.join(timeout=20)
                pr = getattr(preverify, 'result', None)
                if pr and re.sub(r'[^a-z0-9]+', ' ', pr[0].lower()).strip() == \
                        re.sub(r'[^a-z0-9]+', ' ', subj.lower()).strip():
                    res = pr[1]
            except Exception:
                res = None
        if res is None:
            res = _C._pm_verify_cached(subj, candidates)
        ok, _subj, suggest = res
        if ok:
            return None
        from prometheus import match_question as _mq   # "Euphoria US ... Did you mean Euphoria?" to the person who typed Euphoria (2026-11-17)
        _un = _mq.unresolved(subj, suggest, text)
        if _un.get('adopt'):
            spec_draft['subject'] = spec_draft['name'] = _un['adopt']
            spec_draft.pop('file_stem', None)
            return None
        _C._pm_ask_hint(route='subject_unresolved', outcome='asked_subject',
                     subject=subj)
        chips, copy = list(_un.get('chips') or []), _un['copy']
        payload = {'success': False, 'guidance': True, 'error': copy}
        if chips:
            payload['followups'] = chips
        return jsonify(payload), 400
    except Exception:
        traceback.print_exc()
        return None


# <<block: 8>>
def _pm_entity_core_bind(subj):
    """(core, library_subject_or_'') for a build-subject candidate.
    The core strips measure wrappers and audience tails
    (prometheus.guards.entity_core); the library subject is the
    catalog entry the core resolves to, when there is one
    (2026-10-02 S6)."""
    try:
        from prometheus import guards as _pg
        core = _pg.entity_core(subj)
    except Exception:
        core = str(subj or '').strip()
    if not core:
        return '', ''
    if core != str(subj or '').strip():
        print(f"[pm-entity] {subj!r} -> core {core!r}")
    lib = ''
    try:
        hit = _C._pm_library_match(core)
        if hit and hit.get('subject'):
            lib = str(hit['subject'])
    except Exception:
        traceback.print_exc()
    return core, lib


# <<block: 9>>
def _pm_plausible_subject(subj):
    """A subject string that is only ordinary words, or starts or ends
    on a connective, is not a subject (2026-10-02, 'Three Actually
    Influence Product Purchases and')."""
    try:
        from prometheus import referents as _refs
        return bool(_refs.plausible_subject(subj))
    except Exception:
        return bool(str(subj or '').strip())


# <<block: 10>>
# Fuzzy catalog tier (2026-09-28, Phase 1 of the improvement plan):
# exact token matching means 'Emily in Parris' or 'Kardashians' can
# miss the catalog and trigger a duplicate build of a profile the
# library already carries. One typo'd token per subject is tolerated
# (edit distance 1 on tokens of 5+, 2 on 8+); ambiguous fuzzy hits
# never bind.
_PM_SUBJECT_ALIASES = {
    'kuwtk': 'keeping up with the kardashians',
    'hbomax': 'hbo max',
    'gotw': 'the god of the woods',
}


# <<block: 11>>
def _pm_edit_distance(a, b, cap=3):
    """Small bounded Levenshtein; returns cap when clearly beyond."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) >= cap:
        return cap
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        best = cur[0]
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (a[i - 1] != b[j - 1]))
            best = min(best, cur[j])
        if best >= cap:
            return cap
        prev = cur
    return min(prev[lb], cap)


# <<block: 12>>
def _pm_token_typo_eq(a, b):
    a, b = str(a), str(b)
    if a == b:
        return True
    if len(a) < 5 or len(b) < 5:
        return False
    allow = 2 if min(len(a), len(b)) >= 8 else 1
    return _C._pm_edit_distance(a, b, cap=allow + 1) <= allow


# <<block: 13>>
def _pm_expand_alias_tokens(toks, raw_text):
    """Add canonical tokens for any house alias present in the text."""
    out = set(toks)
    norm = _H._normalize_for_match(raw_text)
    flat = norm.replace(' ', '')
    for alias, canon in _C._PM_SUBJECT_ALIASES.items():
        if alias in flat or alias in norm:
            out.update(_H._normalize_for_match(canon).split())
    return out


# <<block: 14>>
def _pm_fuzzy_catalog_subject(text, extra_tokens=None):   # the one library subject the ask nearly names, or None
    try:
        return _ns_mod.fuzzy_catalog_subject(text, _subject_rules.library_named(_C._profile_catalog_for_chat(), text), normalize=_H._normalize_for_match,
                                             typo_eq=_C._pm_token_typo_eq, expand_alias=_C._pm_expand_alias_tokens,
                                             generic_tokens=_C._PM_BASE_GENERIC_TOKENS, extra_tokens=extra_tokens)
    except Exception:
        traceback.print_exc()
        return None


# <<block: 15>>
# TWO-BASE READS (2026-09-28, Phase 3 of the improvement plan):
# comparison and overlap asks name two audiences, and the generation
# pass bound exactly one, so "how much do X and Y overlap" was
# half-answered. When the ask carries comparison vocabulary and two
# catalog subjects resolve (or one plus the open page), the read gets
# BOTH digests: the second rides the existing COMPARISON PROFILE
# rendering in the digest bundle.
_PM_TWO_BASE_VOCAB_RE = re.compile(
    r'\b(?:compare[ds]?|comparison|versus|vs\.?|overlap|'
    r'both audiences|side by side|head to head|against)\b',
    re.IGNORECASE)


# <<block: 16>>
def _pm_two_base_detect(text, ctx=None):
    """Return {'pair': [side1, side2]} when the ask is a two-audience
    read with both sides resolvable, else None. Each side carries
    subject + s3_key. The open page fills side one when exactly one
    other subject is named."""
    t = str(text or '')
    if not _C._PM_TWO_BASE_VOCAB_RE.search(t):
        return None
    toks = set(_H._normalize_for_match(t).split())
    # a platform named under another name counts as the library's spelling
    # ("Prime Video" is Amazon Prime Video; Cara Dorfman 2026-11-10)
    try:
        from prometheus import base_pair as _bp
        toks |= set(_bp.alias_tokens(t))
    except Exception:
        _bp = None
    found = {}
    try:
        for entry in _subject_rules.library_named(_C._profile_catalog_for_chat(), t):
            subj = str(entry.get('subject')
                       or entry.get('display_name') or '').strip()
            st = [w for w in _H._normalize_for_match(subj).split()
                  if w not in _C._PM_BASE_GENERIC_TOKENS]
            if not st or sum(len(w) for w in st) < 4:
                continue
            if set(st) <= toks and not __import__('prometheus.cut_list', fromlist=['is_cut']).is_cut(subj, t):   # a profile reached only through a category the words list is not a second audience (2026-11-12)
                key = _H._normalize_for_match(subj)
                is_tu = ' - ' not in str(entry.get('display_name')
                                         or subj)
                if key not in found or is_tu:
                    found[key] = {
                        'subject': subj,
                        's3_key': str(entry.get('s3_key') or ''),
                        'display_name': str(entry.get('display_name') or subj)}
    except Exception:
        traceback.print_exc()
    # two sides are two families: a Total Universe is never paired with
    # its own cut (Cara's Tubi x Prime Video read ran as TUBI - Avid Fan x TUBI)
    if _bp is not None:
        pair = _bp.two_families(found)
    else:
        pair = [v for v in found.values() if v.get('s3_key')]
        pair.sort(key=lambda v: -len(v['subject']))
    pair = [{'subject': v['subject'], 's3_key': v['s3_key']} for v in pair]
    if len(pair) == 1 and ctx and (ctx.get('primary') or {}) \
            .get('s3_key'):
        pname = str(ctx['primary'].get('name') or '').strip()
        if _H._normalize_for_match(pname) != \
                _H._normalize_for_match(pair[0]['subject']):
            pair = [{'subject': pname or 'the open profile',
                     's3_key': str(ctx['primary']['s3_key'])},
                    pair[0]]
    if len(pair) >= 2:
        return {'pair': pair[:2]}
    return None


__all__ = ['_PM_PENDING_Q_S3_KEY', '_PM_VERIFY_CACHE', '_PM_VERIFY_CACHE_LOCK', '_PM_VERIFY_TTL_S', '_pm_verify_cached', '_pm_start_preverify', '_pm_chat_subject_verify', '_pm_entity_core_bind', '_pm_plausible_subject', '_PM_SUBJECT_ALIASES', '_pm_edit_distance', '_pm_token_typo_eq', '_pm_expand_alias_tokens', '_pm_fuzzy_catalog_subject', '_PM_TWO_BASE_VOCAB_RE', '_pm_two_base_detect']
