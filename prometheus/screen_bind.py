"""Whether an ask points at the open screen: the deixis, shape and scope patterns, the screen bind verdict, the view vocabulary, foreign names, whether the view owns the ask, the open-screen confirm, and the short-name identity.

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
import traceback
import re
from prometheus import subject_rules as _subject_rules
from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session, make_response
from prometheus import named_subject as _ns_mod

# <<moved>>
# <<block: 1>>
_PM_SCREEN_DEIXIS_RE = re.compile(
    r"\b(?:this|that|these|those)\s+(?:audience|profile|cohort|cut|"
    r"view|page|data|chart|file|group|base|universe|fan\s*base|"
    r"fans?|viewers?|people|subscribers?|users?|buyers?|shoppers?)\b"
    r"|\b(?:on|for|from|about)\s+(?:this|the)\s+(?:screen|page|view)\b"
    r"|\bopen(?:ed)?\s+on\s+(?:my|the|your)\s+screen\b"
    r"|\b(?:they|them|their|themselves)\b",
    re.I)


# <<block: 2>>
_PM_PROFILE_SHAPE_RE = re.compile(
    r"\b(?:age|gender|income|ethnicit\w*|education|occupation|"
    r"demograph\w*|demos?|breakdown|split|skew\w*|over.?index\w*|"
    r"index(?:es|ing)?|penetration|avid|casual)\b", re.I)


# <<block: 3>>
_PM_MARKET_SCOPE_RE = re.compile(
    r"\b(?:the\s+us|in\s+the\s+us|u\.s\.|usa|america(?:ns?)?|"
    r"nationwide|nationally|overall|in\s+general|gen\s?pop|"
    r"the\s+market|industry|everyone|average\s+(?:person|american|"
    r"household)|us\s+(?:adults|households|consumers|viewers|"
    r"population|homes))\b", re.I)


# <<block: 4>>
_PM_DEFINITE_REF_RE = re.compile(
    r"\b(?:the|its)\s+(?:show|title|series|movie|film|brand|"
    r"audience|profile|fan\s*base)\b", re.I)


# <<block: 5>>
def _pm_screen_bind_verdict(text, page, base, page_key=''):
    """page | confirm (Jenna 2026-10-06: nothing is inferred from the
    screen; the page binds only when the ask names it outright)."""
    return _ns_mod.screen_bind_verdict(text, page, base, page_key,
                                       _H._normalize_for_match,
                                       _C._PM_CLARIFY_STOP_TOKENS)


# <<block: 6>>
_PM_VIEW_DEIXIS_RE = re.compile(
    r"\bthis\s+(?:campaign|window|screen|page|view|board|leaderboard|"
    r"journey|study|chart|table|data|dashboard|report)\b"
    r"|\bon\s+(?:this|the)\s+screen\b"
    r"|\bthese\s+(?:numbers|results|rows|trends)\b", re.I)


# <<block: 7>>
_PM_CAMPAIGN_ASK_RE = re.compile(
    r"\b(?:campaigns?|attribution|roas|ad\s+spend)\b", re.I)


# <<block: 8>>
# Journey-step vocabulary (2026-09-30 Jenna: "where is the fall-off
# from the ticket checkout page?" on the Attribution view drew a
# profile disambiguation). Anyone asking about a conversion journey
# uses these nouns whatever the campaign; on the Attribution view they
# ground the ask, and at the no-base last rung a subjectless hit gets
# the campaign steer instead of memory options.
_PM_FUNNEL_ASK_RE = re.compile(
    r"\b(?:fall[\s-]?offs?|drop[\s-]?offs?|funnel|check[\s-]?outs?|"
    r"tickets?|ticket(?:ing)?\s+pages?|carts?|purchase\s+pages?|"
    r"abandon\w*|first[\s-]touch|last[\s-]touch|assists?|"
    r"retarget\w*|touch[\s-]?points?|converters?|"
    r"conversion\s+rates?|exposed|info[\s-]?seek\w*)\b", re.I)


# <<block: 9>>
_PM_VIEW_VOCAB_STOP = frozenset((
    'this', 'that', 'what', 'where', 'when', 'which', 'with', 'from',
    'have', 'does', 'data', 'view', 'show', 'tell', 'about', 'many',
    'much', 'they', 'them', 'then', 'than', 'were', 'will', 'your',
    'mean', 'like', 'into', 'over', 'under', 'most', 'more', 'less',
    'here', 'there', 'page', 'pages', 'screen', 'week', 'month',
    'year', 'window', 'number', 'numbers', 'share', 'total', 'rate',
    'rates', 'percent', 'account', 'accounts', 'people', 'audience'))


# <<block: 10>>
def _pm_view_vocab_hit(text, vc):
    """The ask speaks the open view's own vocabulary: distinctive
    tokens from the ask that appear in the serialized on-screen data.
    Two distinctive hits, or one distinctive bigram, bind the view.
    Never raises; an empty or tiny context never matches."""
    try:
        import json as _json
        blob = _json.dumps(vc, ensure_ascii=False, default=str).lower()
    except Exception:
        return False
    if len(blob) < 80:
        return False
    toks = re.findall(r"[a-z][a-z0-9'-]{3,}", str(text or '').lower())
    distinct = [t for t in toks if t not in _C._PM_VIEW_VOCAB_STOP]
    hits = {t for t in distinct if t in blob}
    if len(hits) >= 2:
        return True
    for a, b in zip(toks, toks[1:]):
        if a in _C._PM_VIEW_VOCAB_STOP and b in _C._PM_VIEW_VOCAB_STOP:
            continue
        if f"{a} {b}" in blob:
            return True
    return False


# <<block: 11>>
def _pm_foreign_names(text, vc):
    """Capitalized names in the ask that this view does not carry.

    Shared words are not a subject. Debi Pomerantz, 2026-10-21: she
    asked about Colin and Samir's Lexus campaign with the NCIS: New
    York premiere journey open. The journey text contains audience,
    episode, conversion, and youtube, so the vocabulary overlap bound
    the view and the reply came back as that journey's conversion.
    """
    try:
        import json as _json
        blob = _json.dumps(vc, ensure_ascii=False, default=str).lower()
    except Exception:
        blob = ''
    title = str((vc or {}).get('view_title') or '').lower()
    blob = blob + ' ' + title
    skip = _C._PM_VIEW_VOCAB_STOP | frozenset((
        'have', 'has', 'had', 'this', 'that', 'they', 'them', 'with',
        'from', 'your', 'what', 'when', 'where', 'which', 'would',
        'could', 'should', 'there', 'their', 'about', 'after', 'before',
        'into', 'over', 'under', 'also', 'just', 'only', 'been', 'were',
        'will', 'each', 'other', 'than', 'then', 'here'))
    raw = str(text or '')
    absent = []
    for m in re.finditer(r'\b([A-Z][A-Za-z0-9&]{3,})\b', raw):
        w = m.group(1).lower()
        if w in skip or w in blob:
            continue
        # A capital that only exists because it starts a sentence
        # ("Analyze the data") is not a name. A sentence start still
        # counts when a second name follows ("Colin and Samir").
        prev = raw[:m.start()]
        at_start = (not prev.strip()) or bool(
            re.search(r'[.!?]\s*$', prev))
        if at_start:
            rest = raw[m.end():]
            if not re.match(
                    r'\s+(?:and|&)\s+[A-Z]|\s+[A-Z][A-Za-z0-9&]{3,}',
                    rest):
                continue
        absent.append(w)
    return absent


# <<block: 12>>
def _pm_view_owns_ask(text, ctx):
    """True when the on-screen view owns this ask (2026-09-30 Jenna:
    "What drove conversion in this campaign window?" on the
    Attribution view must ground in the campaign on screen, never
    disambiguate between profiles). The view context only exists when
    the user is on a data-bearing non-profile view, so Profile IQ
    asks never land here.

    A name the ask brings that the view does not carry wins. The view
    is not the subject of a different campaign."""
    if not isinstance(ctx, dict):
        return False
    vc = ctx.get('view_context') or {}
    view_id = str(vc.get('view_id') or '').strip()
    if not view_id:
        return False
    t = str(text or '')
    # An ask that names the open product's own title or the product itself
    # is the product's ask; the other capitalized words are its labels
    # typed from the screen (Trailer Launch, Reviews and Opening Week), not
    # foreign names (2026-10-22, Alexia). A different subject still wins
    # through names_other_subject at the call site.
    try:
        from prometheus import view_base as _vb
        if _vb.names_view(t, vc, product_nouns=False):
            return True
    except Exception:
        traceback.print_exc()
    if _C._pm_foreign_names(t, vc):
        return False
    if _C._PM_VIEW_DEIXIS_RE.search(t):
        return True
    if __import__('prometheus.screen_scope', fromlist=['enabled']).enabled():
        return bool(__import__('prometheus.screen_scope', fromlist=['view_named']).view_named(t, vc))   # the words name what the view is about or point at it, or the lane asks (Jenna 2026-11-20)
    if view_id == 'intentIQ' and (
            _C._PM_CAMPAIGN_ASK_RE.search(t)
            or _C._PM_FUNNEL_ASK_RE.search(t)
            or re.search(r"\bconversions?\b", t, re.I)):
        return True
    # The ask uses words that are literally on the screen (audience
    # names, journey step labels, surfaces): the view owns it whatever
    # the phrasing (2026-09-30 Jenna, checkout fall-off follow-up).
    if _C._pm_view_vocab_hit(t, vc):
        return True
    return False


# <<block: 13>>
def _pm_open_screen_confirm(text, ctx, history=None):
    """Route an ask against the profile open on screen.

    Jenna 2026-10-06 (supersedes the 2026-09-29 question-driven
    default, which itself supersedes the 2026-09-28 always-confirm):
    "Strip the dashboard assumptions out of the lanes ... just ensure
    it always asks to confirm." Returns:
    - {'route': 'bind', 'subject': named} when the ask names its own
      subject (in the text or as a catalog subject) - the caller
      answers on it and the first line says so.
    - None only when no profile is open, the ask names its audiences,
      another handler owns the ask, or the ask names the open page
      outright (the user said it; nothing is assumed).
    - otherwise the one-tap confirm with the page as the first chip,
      the catalog alternative when one resolved, and "Something else".
    """
    page = str((ctx.get('primary') or {}).get('name') or '').strip()
    if not page:
        return None
    # The ask already names its audiences. Do not reduce it to the
    # profile that happens to be open.
    if _C._pm_ask_names_its_audiences(text):
        return None
    try:
        if _C._pm_titles_ask_needs_scope(text, page):
            return None
    except Exception:
        traceback.print_exc()
    # A question about the previous answer or a term in it ("what's the
    # 10 million sample you're mentioning") is a turn in the
    # conversation, never a reason to offer the page (2026-10-20 Jenna,
    # Tania Nasr at Paramount+ drew "Do you want this on BET?").
    try:
        if _subject_rules.definition_turn(text, page=page):
            _C._pm_ask_hint(route='screen_bind', outcome='definition_turn', subject=page)
            return None
    except Exception:
        traceback.print_exc()
    # One subject, bound once (I1, 2026-10-20): a continuation never
    # confirms the page, and a subject the words name (any case, inside
    # "people who watched the Among Us series on Paramount+", even when
    # the page is named as its platform) binds before any page test.
    _b_route = _subject_rules.bind_or_continue(text, ctx, history, _C._pm_ask_hint)
    if _b_route is not None:
        return _b_route or None
    page_key = str((ctx.get('primary') or {}).get('s3_key') or '')
    # What the user names is not an assumption (2026-10-07 Jenna: Gunna
    # asked on the Warriors page drew "Golden State or Gen_Pop?"; "a
    # script for Brock" on the Brock page drew a confirm). A named
    # library subject binds (its cut when cued); the page named
    # outright, first name included, binds the page or its named cut.
    _norm, _stop = _H._normalize_for_match, _C._PM_CLARIFY_STOP_TOKENS
    try:
        _ent = _ns_mod.named_entry(text, _C._profile_catalog_for_chat(),
                                   _norm, _stop)
    except Exception:
        traceback.print_exc()
        _ent = None
    _pg_named = _ns_mod.page_named(text, page, _norm, _stop)
    # On screen, continued (2026-10-13): deixis, the thread's last answer on this page, or a brand that is a row on it binds the page
    _page_signal = (_ns_mod.view_deixis(text) or _ns_mod.thread_on_page(history or [], page, _norm)
                    or (bool(_C._pm_page_brand_named(text, page_key)) and not __import__('prometheus.screen_scope', fromlist=['enabled']).enabled()))   # a row named is not a pointer (Jenna 2026-11-20)
    if _ent and str(_ent.get('s3_key') or '') and _ns_mod.platform_reference(
            text, str(_ent.get('display_name') or _ent.get('subject') or '').split(' - ')[0]):
        _ent = None   # "his youtube channel link" names a surface, not the YouTube profile
    if _ent and str(_ent.get('s3_key') or ''):
        _ent_name = str(_ent.get('display_name')
                        or _ent.get('subject') or '').strip()
        if not _ns_mod.same_family(_ent, page, _norm, _stop):
            _C._pm_ask_hint(route='screen_bind', outcome='bound_named_subject',
                         subject=_ent_name)
            return {'route': 'bind', 'subject': _ent_name}
        if _pg_named and str(_ent.get('s3_key')) != page_key:
            _C._pm_ask_hint(route='screen_bind', outcome='bound_named_cut',
                         subject=_ent_name)
            return {'route': 'bind', 'subject': _ent_name}
    named = ''
    if not _pg_named:
        # The ask names a subject of its own (2026-10-08 Jenna): it binds
        # that subject, in the library or not. The page is only ever
        # offered for an ask that names nothing.
        try:
            named = _C._pm_page_clarify_subject(text, page) or _C._pm_consumption_subject(text)
            if not named:   # a phrase lifted from prose must read as a name ("Age Or Income" did not, 2026-10-07)
                named = _ns_mod.mentioned_subject(text, page, _norm, _stop, _C._pm_plausible_subject)
                _in_lib = bool(named) and any(_norm(_ns_mod.family_name(e)) == _norm(named)
                                               for e in _C._profile_catalog_for_chat())
                if named and not _ns_mod.alternative_ok(named, text, _in_lib, _C._pm_plausible_subject):
                    named = ''
        except Exception:
            traceback.print_exc()
        if not named:
            # the Reader's own subject for the words wins over the open tab, lowercase or not (2026-10-20 Jay Patel, "the film mutiny")
            named = _subject_rules.current_reading_subject(page, _norm)
            if named:
                _C._pm_ask_hint(route='screen_bind', outcome='bound_reader_subject', subject=named)
        if named and _norm(named) != _norm(page):
            _C._pm_ask_hint(route='screen_bind', outcome='bound_named_text',
                         subject=named)
            return {'route': 'bind', 'subject': named}
    _alt_named = named
    attach = True
    try:
        base = _C._pm_generation_base('', text, ctx=ctx, prefer_catalog=True)
    except Exception:
        traceback.print_exc()
        base = None
    if base and str(base.get('source') or '') != 'page':
        bkey = str(base.get('s3_key') or '')
        bsub = _H._normalize_for_match(
            str(base.get('subject') or '').split(' - ')[0])
        psub = _H._normalize_for_match(page.split(' - ')[0])
        same = (bool(page_key) and bkey == page_key) or (
            bool(bsub) and bool(psub)
            and (bsub == psub or bsub in psub or psub in bsub))
        attach = same
    if not attach:
        # a catalog subject outside the page's family. Written in the
        # ask (its distinctive tokens are all there): it binds, the
        # page is not offered (2026-10-08 Jenna). Only a fuzzy hit
        # the user did not write still confirms with both chips.
        _bsub = str((base or {}).get('subject') or '').strip()
        if _bsub and _ns_mod.platform_reference(text, _bsub.split(' - ')[0]):
            _bsub = ''
        _bsub_d = _ns_mod.distinct_tokens(_bsub.split(' - ')[0], _norm, _stop)
        if _bsub and _bsub_d and _bsub_d <= set(_norm(text).split()):
            _C._pm_ask_hint(route='screen_bind', outcome='bound_named_subject',
                         subject=_bsub)
            return {'route': 'bind', 'subject': _bsub}
        if _bsub and (_pg_named or _page_signal):
            # the page is named or on screen; an unwritten library hit is a near-miss of it, not a second subject (Brock vs Block)
            _C._pm_ask_hint(route='screen_bind', outcome='bound_screen',
                         subject=page)
            return None
        if _bsub and not _alt_named:
            _alt_named = _bsub
    # Always confirm (2026-10-06 Jenna): nothing named, nothing assumed;
    # one "Yes, {page}" per thread binds later asks on the same page.
    verdict = _C._pm_screen_bind_verdict(text, page, base, page_key)
    # The open profile is context, never a default subject (2026-10-20
    # Jenna, Mark at Bent Pixels: three turns into DraftKings users, with
    # "I am not interested in salish matter" already said, the deck lane
    # still asked "Do you want this on Salish Matter?"). A page the
    # reader has said no to in this thread is never the lead chip, never
    # bound by an earlier confirm, never the answer-now base; the
    # thread's own subjects come first.
    _osq = _subject_rules.open_screen_question(text, page, history)
    if _osq and (_osq['outcome'].endswith('page_rejected') or not _alt_named):
        _C._pm_ask_hint(route='open_screen_confirm', outcome=_osq.pop('outcome'), subject=page)
        return jsonify(_osq)
    if _alt_named:
        verdict = 'confirm'
    elif verdict == 'confirm' and (
            _page_signal
            or _C._pm_thread_confirmed_page(history, page)
            or (_C._pm_page_confirmed_recently(_C._pm_ask_log_user(''), page) and not __import__('prometheus.screen_scope', fromlist=['enabled']).enabled())):   # another thread's yes binds nothing here (2026-11-20)
        _C._pm_ask_hint(route='screen_bind', outcome='bound_thread_confirmed',
                     subject=page)
        return {'route': 'bind', 'subject': page}
    if verdict == 'confirm' and _C._pm_answer_now_active():
        # The reader said answer now: the thing on their screen is
        # the answer's base, stated in the reply with the other file
        # as a switch chip. No third question (2026-10-02 S5).
        verdict = 'page'
    if verdict == 'confirm' and not __import__('prometheus.screen_scope', fromlist=['enabled']).enabled() and any(   # asked once is not answered once: the question is asked again (Jenna 2026-11-20)
            str(t.get('role') or '') != 'user' and str(t.get('text') or t.get('content') or '').startswith(f'Do you want this on {page}')
            for t in (history or [])):
        # never twice in a thread (2026-10-13 audit); asked once, the page is the base
        _C._pm_ask_hint(route='screen_bind', outcome='bound_after_one_confirm', subject=page)
        verdict = 'page'
    if verdict == 'page':
        _C._pm_ask_hint(route='screen_bind', outcome='bound_screen',
                     subject=page)
        return None
    yes = f'Yes, {page}'
    _opts = [{'label': yes, 'subject': page}]
    _alt = _alt_named or str((base or {}).get('subject') or '').strip()
    if _alt and not _ns_mod.alternative_ok(_alt, text, bool((base or {}).get('s3_key')), _C._pm_plausible_subject):
        _alt = ''   # a platform surface or a phrase that is not a name is never a chip (2026-10-13)
    if _alt and _H._normalize_for_match(_alt) != _H._normalize_for_match(page):
        _opts.append({'label': f'On {_alt}', 'subject': _alt})
    _C._pm_ask_hint(route='open_screen_confirm',
                 outcome='asked_open_screen', subject=page)
    return jsonify({
        'success': True, 'action': 'answer',
        'reply': (f'Do you want this on {page} (open on your screen)'
                  + (f' or on {_alt}?' if len(_opts) > 1 else '?')),
        'followups': [o['label'] for o in _opts] + ['Something else'],
        'offer_deck': False, 'deck_angle': None,
        'memory_confirm': {'question': text, 'options': _opts},
    })


# <<block: 14>>
def _pm_page_brand_named(text, page_key):   # a brand on the open profile that the ask names, or '' (2026-10-13)
    return _ns_mod.page_brand_on_file(text, page_key, _H.s3_client, _H.S3_BUCKET, _H._normalize_for_match)


# <<block: 15>>
def _pm_short_name_identity(toks, raw_text):
    """Short-name subject identity (2026-09-23 Jenna, 'How many people
    have watched BET content in the last 12 months' quoted a research
    report instead of answering from the BET profile).

    The catalog matcher's identity-token floor (distinctive tokens
    must total >= 4 chars) keeps generic short words from binding
    wrongly, but it also made 2-5 letter subjects (BET, CNN, NFL, GAP)
    unmatchable as bases. Mirror of the hostmap initialism rule: a
    single 2-5 letter subject token IS an identity when the ask
    carries it as a standalone ALL-CAPS word. 'watched BET content'
    binds the BET profile; 'how much do people bet' stays unbound."""
    try:
        if len(toks) != 1:
            return False
        tok = str(toks[0] or '')
        if not (2 <= len(tok) <= 5) or not tok.isalpha():
            return False
        return bool(re.search(r'\b' + re.escape(tok.upper()) + r'\b',
                              str(raw_text or '')))
    except Exception:
        return False


__all__ = ['_PM_SCREEN_DEIXIS_RE', '_PM_PROFILE_SHAPE_RE', '_PM_MARKET_SCOPE_RE', '_PM_DEFINITE_REF_RE', '_pm_screen_bind_verdict', '_PM_VIEW_DEIXIS_RE', '_PM_CAMPAIGN_ASK_RE', '_PM_FUNNEL_ASK_RE', '_PM_VIEW_VOCAB_STOP', '_pm_view_vocab_hit', '_pm_foreign_names', '_pm_view_owns_ask', '_pm_open_screen_confirm', '_pm_page_brand_named', '_pm_short_name_identity']
