"""The saved-profile library as the chat reads it: the catalog, the shortlist of matches for an ask, and the gate that decides whether a match stands.

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
from prometheus import faults as _faults
from prometheus import subject_display as _subject_display
from datetime import datetime, timedelta, date, timezone
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus.subject_rules import identity_swapped as _identity_swapped

# <<moved>>
# <<block: 1>>
# Freshness threshold for reusing an existing profile without asking the
# user to refresh. If a profile is older than this, we still surface it
# as a match but the default decision shifts to "offer refresh".
SYNTH_CHAT_FRESH_DAYS = 120


# <<block: 2>>
# How many candidate matches from s3_cache we surface to Claude's
# interpret prompt. Keep this tight (prompt size + Claude focus).
SYNTH_CHAT_MAX_CANDIDATES = 12


# <<block: 3>>
def _profile_catalog_for_chat():
    """Return a compact list of every profile in the dashboard catalog:
    [{s3_key, display_name, subject, category, last_modified, days_old}, ...]

    Reads from the in-memory `s3_cache['jobs']`. This is the same source
    of truth the Select Profile dropdown uses, so if it's in the dropdown
    it's in this list.

    Critical: forces a persisted-cache refresh probe BEFORE reading so
    profiles created in the last few minutes (including via the chatbot
    or partner API on OTHER Render workers) are visible here. Without
    this, a chat prompt like "do a cut of X" can miss a parent that
    was just built and misclassify the request as new_build.
    Directive 2026-08-17: cuts requested via chat were pulling as fresh
    builds because interpret was seeing a stale catalog.
    """
    # Best-effort cross-worker propagation. The helper is throttled so
    # it does at most one S3 HEAD per ~3s per worker.
    try:
        _H.maybe_refresh_persisted_cache_if_changed()
    except Exception as _fx:
        _faults.note('chat', _fx)
    from datetime import timezone as _tz
    now = datetime.now(_tz.utc)
    jobs = []
    try:
        jobs = list((_H.s3_cache or {}).get('jobs') or [])
    except Exception:
        jobs = []
    # Admin-hidden profiles (admin_quick_selects False) are invisible in
    # the Select Profile dropdown, so they must be invisible to interpret
    # matching too - otherwise a retired-but-kept-for-lineage file (e.g.
    # a superseded year-old TU) can win existing_match and hand a stale
    # window to a partner (Bethenny June TU, 2026-08-27). 60s TTL cache;
    # any load failure fails open (empty hidden set).
    hidden = _H._admin_hidden_profile_keys()
    out = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        s3_key = j.get('s3_key') or j.get('key')
        if not s3_key:
            continue
        if s3_key in hidden:
            continue
        display = (j.get('display_name') or j.get('project_name')
                    or j.get('profile_subject') or s3_key).strip()
        # The subject is the name a reader sees. On 137 catalog files
        # `profile_subject` is the upper-snake file stem
        # (BEST_OF_THE_BEST, 2_CHAINZ); it is matching material, never a
        # label (2026-10-22, the counting block in Casey Pearson's
        # Jackass: Best & Last thread printed it). Display name wins.
        subject = _subject_display.display(
            (j.get('profile_subject') or j.get('subject') or '').strip(), display) or display
        cat = (j.get('category') or '').strip()
        lm_iso = j.get('last_modified') or j.get('created_at')
        # Age from the filename timestamp first (immutable build stamp);
        # registry dates only when the key has no stamp. Bulk
        # maintenance clobbers last_modified, which made a 78-day-old
        # file report days_old=0 (client defect 2026-08-25).
        try:
            days_old = _H._profile_age_days(s3_key, lm_iso, now=now)
        except Exception:
            days_old = None
        out.append({
            's3_key': s3_key,
            'display_name': display,
            'subject': subject,
            'category': cat,
            'last_modified': lm_iso,
            'days_old': days_old,
        })
    return out


# <<block: 4>>
def _shortlist_profile_matches(prompt, catalog, max_candidates=SYNTH_CHAT_MAX_CANDIDATES):
    """Return the top-N profile candidates most likely to match the user's
    prompt. Uses token-overlap scoring against each candidate's subject
    and display_name. Cheap - runs in-memory over ~200-1000 records.

    Ranking signals, in order of importance:
      1. Token overlap between prompt and candidate subject/display_name.
      2. Base-profile boost: if the user's ask has NO year AND NO explicit
         cut modifier (avid, female, spotify, etc.), the base profile
         (shortest display_name, no suffix) beats sibling skins.
      3. Historical-year penalty: candidates whose display_name contains
         a year like '2023' or '2024' get penalized UNLESS the prompt
         also contains that exact year. This is what prevents 'reba
         mcentire past 60 days' from being routed at 'Reba McEntire -
         2024 Total Universe' when the base 'Reba McEntire' exists.
      4. Recency: newer profiles beat older ones on ties.

    Returns a list ordered best-first, each entry with the catalog
    fields plus `_score` (float, 0..1) so Claude can see how strong
    each candidate match is.
    """
    if not prompt or not catalog:
        return []
    p_norm = _H._normalize_for_match(prompt)
    # Version qualifiers ('us', 'uk', 'bbc') are shorter than the
    # 3-char token floor but distinguish same-name versions - keep
    # them (2026-08-25).
    p_version_tokens = _H._prompt_version_tokens(p_norm)
    p_tokens = set(t for t in p_norm.split()
                   if len(t) >= 3) | p_version_tokens
    if not p_tokens:
        return []
    # Extract explicit years from the prompt so historical-year skins
    # only rank high when the user actually asked for that year.
    prompt_years = set(_H._HISTORICAL_YEAR_RE.findall(prompt or ''))
    # Detect explicit cut modifiers so we know whether the user is asking
    # for a sibling skin ("avid Reba") or the base current profile ("Reba").
    prompt_has_cut_hint = bool(_H._prompt_extract_cut_hints(p_norm))
    scored = []
    for c in catalog:
        subject_norm = _H._normalize_for_match(c.get('subject') or '')
        display_norm = _H._normalize_for_match(c.get('display_name') or '')
        combined = f"{subject_norm} {display_norm}"
        cand_versions = _H._subject_version_tokens(
            subject_norm or _H._normalize_for_match(
                str(c.get('display_name') or '').split(' - ', 1)[0]))
        c_tokens = set(t for t in combined.split()
                       if len(t) >= 3) | cand_versions
        if not c_tokens:
            continue
        overlap = p_tokens & c_tokens
        if not overlap:
            for pt in p_tokens:
                if len(pt) >= 5 and pt in combined:
                    overlap = overlap | {pt}
        if not overlap:
            continue
        prompt_coverage = len(overlap) / max(len(p_tokens), 1)
        subject_hit = 1.0 if any(t in subject_norm.split() for t in overlap) else 0.5
        score = prompt_coverage * subject_hit

        # Version mismatch (2026-08-25): the prompt names one version
        # ('the office uk') and this candidate carries a DIFFERENT
        # version marker ('The Office US') - push it down hard so the
        # sibling version wins.
        if p_version_tokens and cand_versions \
                and not (p_version_tokens & cand_versions):
            score *= 0.15

        # Identity substitution (2026-10-30, the Ohio voters twin that was
        # seated from the Florida Gubernatorial Voters profile): when the
        # prompt carries a distinctive word the candidate lacks AND the
        # candidate carries a distinctive word the prompt lacks, one name
        # has been swapped for another (Ohio / Florida, Godzilla Minus One
        # / Godzilla x Kong). Audience nouns, years and the stop words do
        # not count, so 'Reba' still finds 'Reba McEntire' and 'Taylor
        # Swift Eras Tour' still sees 'Taylor Swift'. Same push as a
        # version mismatch.
        _ident_swapped = False
        try:
            _ident_swapped = _identity_swapped(
                prompt, str(c.get('display_name') or '').split(' - ', 1)[0] or c.get('subject') or '', combined)
        except Exception:
            _ident_swapped = False
        if _ident_swapped:
            score *= 0.15

        # Historical-year handling. If the display name contains a year
        # AND the prompt does NOT mention that exact year, this candidate
        # is a historical skin the user did not ask for -- push it down.
        display_years = set(_H._HISTORICAL_YEAR_RE.findall(
            c.get('display_name') or ''))
        if display_years and not (display_years & prompt_years):
            score *= 0.55
        elif display_years and (display_years & prompt_years):
            # User explicitly named a historical year and this candidate
            # matches -- reward it.
            score *= 1.15

        # Base-profile boost: when the user's ask has NO year and NO cut
        # modifier, prefer the shortest matching display name (i.e. the
        # base 'Reba McEntire', not 'Reba McEntire - Spotify Fan').
        if not prompt_years and not prompt_has_cut_hint:
            display_raw = str(c.get('display_name') or '')
            has_suffix = (' - ' in display_raw) or bool(display_years)
            if not has_suffix:
                score *= 1.30

        # Exact-entity floor (2026-08-28 client finding #3: a perfect
        # name match 2 hours after its build ranked 0.65 because the
        # prompt's filler words - 'audience of ... customers' -
        # diluted token coverage). When every token of a BASE
        # candidate's name appears in the prompt, the identity is
        # fully asserted: floor the ranking score so the interpret
        # model reads it as the strong match it is. Never applied
        # over a version or historical-year penalty, and never to
        # cut/year skins (their qualifier still has to earn the pick).
        _ent_toks = set(_H._normalize_for_match(
            str(c.get('display_name') or '').split(' - ', 1)[0]).split()
        ) or set(subject_norm.split())
        _penalized = bool(
            (p_version_tokens and cand_versions
             and not (p_version_tokens & cand_versions))
            or _ident_swapped
            or (display_years and not (display_years & prompt_years)))
        if (_ent_toks and _ent_toks <= set(p_norm.split())
                and not _penalized and not display_years
                and ' - ' not in str(c.get('display_name') or '')):
            score = max(score, 0.92)

        score = round(score, 4)
        if score < 0.15:
            continue
        c2 = dict(c)
        c2['_score'] = score
        scored.append(c2)
    # Sort by score desc, then by recency desc (newer wins on ties).
    def _sort_key(x):
        lm = x.get('last_modified') or ''
        return (-x['_score'], lm[::-1] if isinstance(lm, str) else '')
    scored.sort(key=_sort_key)
    return scored[:max_candidates]


# <<block: 5>>
def _synth_chat_gate(allow_api_key: bool = True):
    """Return (user_dict, error_response_or_None). None error means allow.

    By default accepts either a logged-in session (browser / dashboard
    use) OR an external partner API key sent as
    `X-Crosswalk-API-Key: <raw_key>`. Pass ``allow_api_key=False`` to
    force session-only auth - used by the internal dashboard chatbot
    routes (`/api/synth-chat/*`, `/api/brief-chat/*`) so partners get
    steered to the documented `/api/v1/*` surface instead of leaking
    internal terminology through the chatbot UI paths.
    """
    api_key = request.headers.get('X-Crosswalk-API-Key') if request else None
    if api_key:
        if not allow_api_key:
            # Partner keys must use the documented v1 API. Do NOT let
            # them see internal chatbot-UI JSON (which mentions "queue",
            # phase strings, etc.). Steer without exposing the internal
            # path names.
            return None, (jsonify({
                'success': False,
                'error': ('API keys are not accepted on this route. '
                          'Use POST /api/v1/profiles/check, POST '
                          '/api/v1/profiles/run, and GET /api/v1/'
                          'profiles/<run_id>.'),
            }), 403)
        u, uname = _H._lookup_user_by_api_key(api_key)
        if not u:
            return None, (jsonify({
                'success': False,
                'error': 'invalid or revoked X-Crosswalk-API-Key',
            }), 401)
        role = u.get('role', '')
        has_flag = bool(u.get('has_chatbot_profile_iq_access', False))
        if role != 'super_admin' and not has_flag:
            return None, (jsonify({
                'success': False,
                'error': 'API key does not have Chatbot Profile IQ access.',
            }), 403)
        u.setdefault('username', uname)
        u['_auth_via'] = 'api_key'
        # The ask log and the watch see the key owner as the user
        # (2026-10-06: API asks used to log as 'unknown').
        try:
            from flask import g as _g_ak
            _g_ak._pm_api_key_owner = uname or u.get('username') or u.get('email')
        except Exception as _fx:
            _faults.note('chat', _fx)
        return u, None

    user = _H.get_current_user()
    if not user:
        return None, (jsonify({'success': False, 'error': 'not authenticated'}), 401)
    role = user.get('role', '')
    has_flag = bool(user.get('has_chatbot_profile_iq_access', False))
    if role != 'super_admin' and not has_flag:
        return None, (jsonify({
            'success': False,
            'error': 'Chatbot Profile IQ access not granted for this account. '
                     'Contact your admin.',
        }), 403)
    user['_auth_via'] = 'session'
    # The users.json record carries no 'username' field (the dict key
    # IS the username), so inject the session's. Without this, every
    # job-owner check downstream compared the record's EMAIL against
    # the username the job status stored, and a user's own polls
    # 403'd (2026-09-25, keith's read-status / notify-when-done).
    user.setdefault('username', session['username'])
    return user, None


__all__ = ['SYNTH_CHAT_FRESH_DAYS', 'SYNTH_CHAT_MAX_CANDIDATES', '_profile_catalog_for_chat', '_shortlist_profile_matches', '_synth_chat_gate']
