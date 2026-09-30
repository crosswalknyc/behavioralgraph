#!/usr/bin/env python3
"""View-grounded asks stay on the view; campaign asks never offer
profile names.

Jenna 2026-09-30: a user on the Attribution view tapped the built-in
chip "What drove conversion in this campaign window?" and Prometheus
answered "Do you mean for Paw Patrol Series Viewers, or Obsession?".
Two fixes:

1. When the open view carries the data the ask points at (screen
   deixis like "this campaign window" / "this screen", or campaign
   vocabulary while the Attribution view is open), the ask flows to
   the main analysis pass grounded in the on-screen data. The
   open-profile verdict and the profile-subject generate ladder never
   run on it.
2. The generate pass's last-resort memory clarify never offers
   profile referents for a campaign ask. With no campaign
   identifiable it asks which campaign and points at the Attribution
   IQ tab.
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
# 1. Helpers before the open-screen verdict function.
# ------------------------------------------------------------------
HELPERS = '''_PM_VIEW_DEIXIS_RE = re.compile(
    r"\\bthis\\s+(?:campaign|window|screen|page|view|board|leaderboard|"
    r"journey|study|chart|table|data|dashboard|report)\\b"
    r"|\\bon\\s+(?:this|the)\\s+screen\\b"
    r"|\\bthese\\s+(?:numbers|results|rows|trends)\\b", re.I)

_PM_CAMPAIGN_ASK_RE = re.compile(
    r"\\b(?:campaigns?|attribution|roas|ad\\s+spend)\\b", re.I)


def _pm_view_owns_ask(text, ctx):
    """True when the on-screen view owns this ask (2026-09-30 Jenna:
    "What drove conversion in this campaign window?" on the
    Attribution view must ground in the campaign on screen, never
    disambiguate between profiles). The view context only exists when
    the user is on a data-bearing non-profile view, so Profile IQ
    asks never land here."""
    if not isinstance(ctx, dict):
        return False
    vc = ctx.get('view_context') or {}
    view_id = str(vc.get('view_id') or '').strip()
    if not view_id:
        return False
    t = str(text or '')
    if _PM_VIEW_DEIXIS_RE.search(t):
        return True
    if view_id == 'intentIQ' and (
            _PM_CAMPAIGN_ASK_RE.search(t)
            or re.search(r"\\bconversions?\\b", t, re.I)):
        return True
    return False


def _pm_open_screen_confirm(text, ctx):'''

sp("def _pm_open_screen_confirm(text, ctx):", HELPERS,
   "view-ownership helpers")

# ------------------------------------------------------------------
# 2. Route override: view-owned asks skip the open-profile verdict
#    and the generate deflection, landing in the main analysis pass
#    where the on-screen block grounds the answer.
# ------------------------------------------------------------------
sp("""    # Yes re-sends with bind_subject, which returns above this point.
    if isinstance(ctx, dict) and not str(body.get('mode') or '').strip():""",
   """    # Yes re-sends with bind_subject, which returns above this point.
    # View-grounded asks stay on the view (2026-09-30 Jenna: the
    # Attribution view's own chip "What drove conversion in this
    # campaign window?" asked "Do you mean for Paw Patrol Series
    # Viewers, or Obsession?"). When the open view carries the data
    # the ask points at, the answer grounds in that view; the
    # open-profile verdict and the profile-subject generate ladder
    # never run on it.
    _vc_owns = False
    try:
        _vc_owns = _pm_view_owns_ask(text, ctx)
    except Exception:
        traceback.print_exc()
    if _vc_owns:
        if _route in ('generate', 'memory_confirm'):
            _route = ''
        _pm_ask_hint(route='view_grounded',
                     subject=str(((ctx or {}).get('view_context')
                                  or {}).get('view_title') or ''))
    if isinstance(ctx, dict) and not _vc_owns \\
            and not str(body.get('mode') or '').strip():""",
   "route override for view-owned asks")

# ------------------------------------------------------------------
# 3. Campaign guard ahead of the memory clarify in the generate pass.
# ------------------------------------------------------------------
sp("""        try:
            import prometheus_memory as pmm
            _named = (subj_hint""",
   """        # A campaign ask never disambiguates between profiles
        # (2026-09-30 Jenna). Reaching this rung means no campaign is
        # on screen: say what to open instead of offering audience
        # names from memory.
        if _PM_CAMPAIGN_ASK_RE.search(str(text or '')):
            _pm_ask_hint(outcome='campaign_clarify')
            return jsonify({
                'success': True, 'action': 'answer',
                'reply': ('Which campaign is this about? Open it in '
                          'the Attribution IQ tab and ask from there, '
                          'or give me the campaign name and its '
                          'window.'),
                'followups': [], 'offer_deck': False,
                'deck_angle': None})
        try:
            import prometheus_memory as pmm
            _named = (subj_hint""",
   "campaign guard before memory clarify")

ast.parse(src)
APP.write_text(src)
print(f"[done] {APP} patched ({len(src):,} bytes)")
