"""The two remaining slow steps of a generated read (Jenna 2026-11-23,
round 2 follow-up 1): the main model call and the verifier's revise round.

Measured on 130 live reads over 14 days before this change: wall median
107 s, p90 327 s; the model 75 s median, 178 s p90, 92 s median when it
ran a web-search round of its own (43 of 130 reads) against 64 s when it
did not; the verifier's revise round ran on 22 of 130 reads and cost 63 s
median, 147 s p90 when it did. The research step (the knowledge layer's
one call) ran on the strongest chain with web search: 128 s on the live
read of 2026-11-23.

What changes, without changing an answer:

* The main call searches the web only when nothing is in hand
  (`model_tools`): the knowledge layer already made the research call or
  found the figures on file (held, recorded, across products), and those
  ride the prompt as binding; the model's own search is then a second
  lookup of the same facts. A read with nothing on file and no anchors
  keeps the tool. `PM_MODEL_WEB_SEARCH=auto|always|never`.
* The verifier runs its deterministic rescue first: every finding names
  the figure the reply got wrong and the figure it should carry (or the
  word that must not be there), so the draft is fixed in place and
  re-verified before any revise call; the revise and auto-correct model
  calls run only when the rescue does not clear the checks, and without
  web search (the findings carry the measured figures). The rescue was
  already the last resort that shipped to readers; it is now the first.
  `PM_VERIFY_RESCUE_FIRST=0` restores the old order.
* The research step and the valuation's event lookup take the fast
  strong tier first (`_PM_FAST_SURFACES` in the chat module: the
  interpret model, then the analysis chain), since gathering anchors with
  web search does not need the strongest model; the read itself still
  does. `PM_RESEARCH_TIER=strong` restores the old chain.

Nothing a reader sees names any of this.
"""
from __future__ import annotations

import os

__all__ = ['web_search_mode', 'model_tools', 'rescue_first_enabled', 'research_tier', 'FAST_SURFACES', 'anchors_in_hand',
           'rescue_first', 'SkipRevise']

ENV_WEB_SEARCH = 'PM_MODEL_WEB_SEARCH'
ENV_RESCUE_FIRST = 'PM_VERIFY_RESCUE_FIRST'
ENV_RESEARCH_TIER = 'PM_RESEARCH_TIER'
FAST_SURFACES = ('pm_knowledge_research', 'pm_valuation_events')


def web_search_mode() -> str:
    v = os.environ.get(ENV_WEB_SEARCH, 'auto').strip().lower()
    return v if v in ('auto', 'always', 'never') else 'auto'


def rescue_first_enabled() -> bool:
    return os.environ.get(ENV_RESCUE_FIRST, '1').strip().lower() not in ('0', 'false', 'no', 'off')


def research_tier() -> str:
    v = os.environ.get(ENV_RESEARCH_TIER, 'fast').strip().lower()
    return v if v in ('fast', 'strong') else 'fast'


def anchors_in_hand(base, anchors_block='', ledger_block='') -> dict:
    """What the prompt already carries for the subject: the knowledge
    layer's trail on the base (`_knowledge`: anchors researched or read
    back, figures across products, a near twin, a bound valuation, the
    held marks), the cross-module anchors block, the ledger block."""
    b = base if isinstance(base, dict) else {}
    kn = b.get('_knowledge') if isinstance(b.get('_knowledge'), dict) else {}
    held = any(isinstance(m, dict) and m.get('kind') == 'held' for m in (kn.get('marks') or []))
    return {
        'anchors': int(kn.get('anchors') or 0),
        'shared': int(kn.get('shared_figures') or 0),
        'twin': bool(kn.get('twin')),
        'valuation': bool(kn.get('valuation')),
        'held': held,
        'researched': bool(kn.get('research_call') or kn.get('cached')),
        'cross_module': bool(str(anchors_block or '').strip()),
        'ledger': bool(str(ledger_block or '').strip()),
    }


def model_tools(base, web_search_tool, anchors_block='', ledger_block='', stages=None) -> list:
    """The tools for the main model call: the web search tool when the
    read has nothing in hand on the subject (no researched anchors, no
    figures on file, no held rows, no near twin, no bound valuation), else
    none. `always` and `never` override. `stages` takes
    `model_web_search` (1 or 0) when given."""
    mode = web_search_mode()
    if mode == 'always':
        out = [web_search_tool] if web_search_tool else []
    elif mode == 'never':
        out = []
    else:
        have = anchors_in_hand(base, anchors_block, ledger_block)
        # the subject's own facts: researched anchors, figures across products,
        # a held profile, a near twin or a bound valuation. A cross-module or
        # ledger block alone is not grounding on the subject, so it keeps the tool.
        grounded = have['anchors'] or have['shared'] or have['held'] or have['twin'] or have['valuation']
        out = [] if grounded else ([web_search_tool] if web_search_tool else [])
    if isinstance(stages, dict):
        stages['model_web_search'] = 1 if out else 0
    return out


class SkipRevise(Exception):
    """The in-place rescue already cleared the checks: no revise round."""


def rescue_first(pmv, last_draft, verdict, stages=None, **kw):
    """The verifier's deterministic rescue before any revise call: the
    draft fixed in place from its own findings and re-verified
    (`prometheus_verify.rescue_in_place`). Returns its tuple (data, res,
    reply, family, verdict, n_fixed) when the checks cleared, else None.
    `stages` takes `verify_rescue` (ms) and `verify_rescue_fixes`. Off
    under PM_VERIFY_RESCUE_FIRST=0. Never raises."""
    if not rescue_first_enabled() or pmv is None:
        return None
    import time
    t0 = time.monotonic()
    try:
        out = pmv.rescue_in_place(last_draft, verdict, **kw)
    except Exception:
        out = None
    if isinstance(stages, dict):
        stages['verify_rescue'] = int((time.monotonic() - t0) * 1000)
    if out:
        if isinstance(stages, dict):
            stages['verify_rescue_fixes'] = int(out[5] or 0)
        print(f"[pm-verify] rescued in place first ({out[5]} fix(es)); no revise round")
    return out
