#!/usr/bin/env python3
"""The open profile is no longer the default subject (Jenna 2026-09-29).

"the biggest issue we seem to keep having is that people ask questions
un related to what's on the screen but it constantly treats it as
what's on screen. shoud we make the default be NOT on screen and then
it can ask if you mean that if it thinks it looks like you might?"

Question-driven routing, supersedes the 2026-09-28 always-confirm:
1. An ask that names its own subject binds that subject silently.
2. An ask that points at the screen (deixis, audience pronouns, the
   page named outright, elliptical profile-shaped asks) binds the
   page silently.
3. Everything else answers as if nothing were open.
4. Only the cut-vs-parent tension still confirms, now with both
   options as chips.
Every generated answer opens by naming the audience it used, and an
answer that went away from the open page carries a one-tap
"On {page} instead" switch chip.
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"
src = APP.read_text()


def sp(old, new, desc):
    global src
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"[fail] {desc}: anchor x{n}")
    src = src.replace(old, new)
    print(f"[ok] {desc}")


# ------------------------------------------------------------------
# 1. Verdict helpers + rewritten routing contract docstring.
# ------------------------------------------------------------------
OLD_DEF = '''def _pm_open_screen_confirm(text, ctx):
    """Ask before an answer is attached to the profile open on screen.

    Jenna 2026-09-28: always confirm that attachment. Never treat the
    open profile as the subject until the user says yes.

    BASE CLARIFY (2026-09-24 Jenna): before answering against the
    open page, an ask that names a DIFFERENT subject gets the
    question instead of a guess - 'Did you want this on Landman
    (open on your screen) or on Emily in Paris?'. The chips re-run
    the original ask bound to the picked subject.

    Titles asks that already asked, board views that are not about
    the selected profile, and a catalog subject that is not the open
    profile return None. Yes on the open-screen question re-sends
    the original ask with bind_subject.
    """'''

NEW_DEF = '''_PM_SCREEN_DEIXIS_RE = re.compile(
    r"\\b(?:this|that|these|those)\\s+(?:audience|profile|cohort|cut|"
    r"view|page|data|chart|file|group|base|universe|fan\\s*base|"
    r"fans?|viewers?|people|subscribers?|users?|buyers?|shoppers?)\\b"
    r"|\\b(?:on|for|from|about)\\s+(?:this|the)\\s+(?:screen|page|view)\\b"
    r"|\\bopen(?:ed)?\\s+on\\s+(?:my|the|your)\\s+screen\\b"
    r"|\\b(?:they|them|their|themselves)\\b",
    re.I)

_PM_PROFILE_SHAPE_RE = re.compile(
    r"\\b(?:age|gender|income|ethnicit\\w*|education|occupation|"
    r"demograph\\w*|demos?|breakdown|split|skew\\w*|over.?index\\w*|"
    r"index(?:es|ing)?|penetration|avid|casual)\\b", re.I)

_PM_MARKET_SCOPE_RE = re.compile(
    r"\\b(?:the\\s+us|in\\s+the\\s+us|u\\.s\\.|usa|america(?:ns?)?|"
    r"nationwide|nationally|overall|in\\s+general|gen\\s?pop|"
    r"the\\s+market|industry|everyone|average\\s+(?:person|american|"
    r"household)|us\\s+(?:adults|households|consumers|viewers|"
    r"population|homes))\\b", re.I)

_PM_DEFINITE_REF_RE = re.compile(
    r"\\b(?:the|its)\\s+(?:show|title|series|movie|film|brand|"
    r"audience|profile|fan\\s*base)\\b", re.I)


def _pm_screen_bind_verdict(text, page, base, page_key=''):
    """page | away | confirm - what the open page is to this ask.

    Jenna 2026-09-29: the default is NOT the screen. The question
    decides and the screen is a tiebreaker: asks that point at the
    screen bind it silently, general asks answer as if nothing were
    open, and only the cut-vs-parent tension still confirms.
    """
    t = str(text or '')
    tl = ' ' + _normalize_for_match(t) + ' '
    # The catalog resolved a DIFFERENT file in the page's own subject
    # family (scott, Spiderwick cut vs parent, 2026-09-29): torn.
    if base and str(base.get('source') or '') == 'catalog' \\
            and str(base.get('s3_key') or '') != str(page_key or ''):
        return 'confirm'
    # The ask names the page outright: the page, silently.
    try:
        page_toks = [w for w in _normalize_for_match(
            str(page or '').split(' - ')[0]).split()
            if len(w) >= 4 and w not in _PM_CLARIFY_STOP_TOKENS]
    except Exception:
        page_toks = []
    if page_toks and any(f' {w} ' in tl for w in page_toks):
        return 'page'
    # "the show" / "its audience" style definite reference: the page
    # when the page IS the whole subject; torn when a cut is open.
    if _PM_DEFINITE_REF_RE.search(t):
        return 'confirm' if ' - ' in str(page or '') else 'page'
    # Deixis and audience pronouns point at the screen.
    if _PM_SCREEN_DEIXIS_RE.search(t):
        return 'page'
    # Elliptical profile-shaped ask (age breakdown, income skew):
    # incomplete without a subject, so the screen supplies it,
    # unless the ask scopes itself to the market.
    if _PM_PROFILE_SHAPE_RE.search(t) and len(t.strip()) <= 90 \\
            and not _PM_MARKET_SCOPE_RE.search(t):
        return 'page'
    return 'away'


def _pm_open_screen_confirm(text, ctx):
    """Route an ask against the profile open on screen.

    Jenna 2026-09-29 (supersedes the 2026-09-28 always-confirm): the
    open page is NOT the default subject. Returns:
    - {'route': 'bind', 'subject': named} when the ask names its own
      subject - the caller answers on it silently.
    - {'route': 'away'} for a general ask - the caller answers as if
      nothing were open.
    - None when the page binds silently (the ask points at it) or
      another handler owns the ask.
    - a confirm response only for the genuinely torn cut-vs-parent
      case, with both options as chips.
    """'''

sp(OLD_DEF, NEW_DEF, "verdict helpers + routing contract")

# ------------------------------------------------------------------
# 2. Named subject binds silently instead of asking.
# ------------------------------------------------------------------
OLD_NAMED = """    if named:
        _pm_ask_hint(route='base_clarify', outcome='asked_base',
                     subject=page)
        return jsonify({
            'success': True, 'action': 'answer',
            'reply': (f'Did you want this on {page} (open on '
                      f'your screen) or on {named}?'),
            'followups': [f'On {page}', f'On {named}',
                          'Something else'],
            'offer_deck': False, 'deck_angle': None,
            'memory_confirm': {'question': text, 'options': [
                {'label': f'On {page}', 'subject': page},
                {'label': f'On {named}', 'subject': named}]}})"""

NEW_NAMED = """    if named:
        # The ask names its own subject (2026-09-29 Jenna): the open
        # page never hijacks it. Bind the named subject silently; the
        # answer states the audience it used and carries the page as
        # a one-tap switch chip.
        _pm_ask_hint(route='ask_named_subject', outcome='bound_named',
                     subject=named)
        return {'route': 'bind', 'subject': named}"""

sp(OLD_NAMED, NEW_NAMED, "named subject binds silently")

# ------------------------------------------------------------------
# 3. Verdict-gated tail: silent page bind, silent away, torn confirm.
# ------------------------------------------------------------------
OLD_TAIL = """    if not attach:
        return None
    yes = f'Yes, {page}'
    _pm_ask_hint(route='open_screen_confirm',
                 outcome='asked_open_screen', subject=page)
    return jsonify({
        'success': True, 'action': 'answer',
        'reply': (f'Do you want this on {page} (open on your screen)?'),
        'followups': [yes, 'Something else'],
        'offer_deck': False, 'deck_angle': None,
        'memory_confirm': {
            'question': text,
            'options': [{'label': yes, 'subject': page}],
        },
    })"""

NEW_TAIL = """    if not attach:
        return None
    # Question-driven default (2026-09-29 Jenna: "make the default be
    # NOT on screen"). The page binds silently only when the ask
    # points at it; general asks answer as if nothing were open; only
    # the cut-vs-parent tension still confirms.
    verdict = _pm_screen_bind_verdict(text, page, base, page_key)
    if verdict == 'page':
        _pm_ask_hint(route='screen_bind', outcome='bound_screen',
                     subject=page)
        return None
    if verdict == 'away':
        _pm_ask_hint(route='screen_detach', outcome='answered_away',
                     subject=page)
        return {'route': 'away'}
    yes = f'Yes, {page}'
    _opts = [{'label': yes, 'subject': page}]
    _alt = str((base or {}).get('subject') or '').strip()
    if _alt and _normalize_for_match(_alt) != _normalize_for_match(page):
        _opts.append({'label': f'On {_alt}', 'subject': _alt})
    _pm_ask_hint(route='open_screen_confirm',
                 outcome='asked_open_screen', subject=page)
    return jsonify({
        'success': True, 'action': 'answer',
        'reply': (f'Do you want this on {page} (open on your screen)'
                  + (f' or on {_alt}?' if len(_opts) > 1 else '?')),
        'followups': [o['label'] for o in _opts] + ['Something else'],
        'offer_deck': False, 'deck_angle': None,
        'memory_confirm': {'question': text, 'options': _opts},
    })"""

sp(OLD_TAIL, NEW_TAIL, "verdict-gated confirm tail")

# ------------------------------------------------------------------
# 4. Callsite consumes the routing dicts.
# ------------------------------------------------------------------
OLD_CALL = """        else:
            _osc = _pm_open_screen_confirm(text, ctx)
            if _osc is not None:
                return _osc"""

NEW_CALL = """        else:
            _osc = _pm_open_screen_confirm(text, ctx)
            if isinstance(_osc, dict):
                _sw_page = str((ctx.get('primary') or {}).get('name')
                               or '').strip()
                if _osc.get('route') == 'bind':
                    # ctx stays out so a named subject with no base
                    # anywhere steers to its build instead of falling
                    # back onto the open page.
                    return _pm_generate_metrics_response(
                        user, text, history, prefer_catalog=True,
                        bind_subject=_osc.get('subject'),
                        switch_page=_sw_page)
                return _pm_generate_metrics_response(
                    user, text, history, switch_page=_sw_page)
            if _osc is not None:
                return _osc"""

sp(OLD_CALL, NEW_CALL, "callsite routes bind/away")

# ------------------------------------------------------------------
# 5. switch_page kwarg on the generate pass.
# ------------------------------------------------------------------
sp("""bind_subject=None, bind_cohort=None,
                                  panel_confirm=None):""",
   """bind_subject=None, bind_cohort=None,
                                  panel_confirm=None,
                                  switch_page=None):""",
   "switch_page kwarg")

# ------------------------------------------------------------------
# 6. The answer names the audience it used (before persist so the
#    ledger and replays carry it).
# ------------------------------------------------------------------
OLD_FUPS = """    followups = [pma.scrub_user_text(str(f).strip())[:160]
                 for f in (data.get('followups') or [])
                 if str(f).strip()][:3]
    if pma.CSV_OFFER_CHIP not in followups:
        followups.append(pma.CSV_OFFER_CHIP)
    _t_stage = time.monotonic()"""

NEW_FUPS = """    followups = [pma.scrub_user_text(str(f).strip())[:160]
                 for f in (data.get('followups') or [])
                 if str(f).strip()][:3]
    if pma.CSV_OFFER_CHIP not in followups:
        followups.append(pma.CSV_OFFER_CHIP)
    # The answer opens by naming the audience it used (2026-09-29
    # Jenna: the screen no longer binds by default, so the binding is
    # stated up front and a wrong one is visible in the first line).
    _aud = str(res.get('subject') or '').strip()
    if str(res.get('cohort') or '').strip():
        _aud = f"{_aud} - {str(res.get('cohort')).strip()}"
    if _aud and _aud.lower() not in str(reply or '')[:90].lower():
        reply = f"On {_aud}:\\n\\n{reply}"
    _t_stage = time.monotonic()"""

sp(OLD_FUPS, NEW_FUPS, "audience line opens the answer")

# ------------------------------------------------------------------
# 7. One-tap switch back to the open page.
# ------------------------------------------------------------------
OLD_RET = """    _pm_ask_hint(
        outcome=('corrected' if _pm_auto_corrected else 'answered'),
        subject=res.get('subject'))
    return {
        'success': True, 'action': 'answer', 'reply': reply,
        'followups': followups, 'offer_deck': False, 'deck_angle': None,
        'model': result.get('model'),
        'profile': res.get('subject'),
        '_family': fam0,
        '_verify': _verify_stamp,
        '_stages_ms': stages,
        **_file_payload}"""

NEW_RET = """    _pm_ask_hint(
        outcome=('corrected' if _pm_auto_corrected else 'answered'),
        subject=res.get('subject'))
    # The ask answered away from the profile that was open: carry a
    # one-tap switch chip that re-runs it bound to that profile
    # (2026-09-29). Not persisted - the chip is contextual.
    _sw_payload = {}
    _sw = str(switch_page or '').strip()
    if _sw and _normalize_for_match(_sw) != _normalize_for_match(
            str(res.get('subject') or '')):
        _sw_chip = f'On {_sw} instead'
        if _sw_chip not in followups:
            followups.append(_sw_chip)
        _sw_payload = {'memory_confirm': {
            'question': text,
            'options': [{'label': _sw_chip, 'subject': _sw}]}}
    return {
        'success': True, 'action': 'answer', 'reply': reply,
        'followups': followups, 'offer_deck': False, 'deck_angle': None,
        'model': result.get('model'),
        'profile': res.get('subject'),
        '_family': fam0,
        '_verify': _verify_stamp,
        '_stages_ms': stages,
        **_sw_payload,
        **_file_payload}"""

sp(OLD_RET, NEW_RET, "one-tap switch chip")

ast.parse(src)
APP.write_text(src)
print(f"[done] {APP} patched ({len(src):,} bytes)")
