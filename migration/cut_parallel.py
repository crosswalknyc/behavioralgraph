"""Shared parallelization plumbing for the audience-cut engines.

2026-08-20 (Jenna: "make the parallelization change for cuts to speed
them up"): the three cut engines (addon_cut_synthesis,
audience_cut_synthesis, avid_fan_row_by_row) reasoned their ~107
categories ONE Claude call at a time on the single env key, which made
a 3-credit cut ~3x slower than a full fresh build (the fresh-build
engine runs 15 workers over the leased key pool). This module gives
the cut engines the same machinery: a key pool + per-thread direct
calls with prompt caching.

NOTHING about the reasoning changes: same prompts, same row-by-row
coverage, and chunks WITHIN a category stay sequential (workspace
rule: big categories are chunked across sequential calls, never
truncated). Only independent categories run concurrently.
"""

import os
import random
import time


def load_cut_key_pool():
    """Best-available Anthropic key pool for cut synthesis.

    Order: avid_key_pool file (production Hetzner pool) ->
    ANTHROPIC_KEY_POOL env (comma-separated) -> [ANTHROPIC_API_KEY].
    Returns [] when nothing is configured (callers fall back to the
    claude_client singleton path).
    """
    try:
        try:
            from migration.avid_key_pool import load_keys
        except Exception:
            from avid_key_pool import load_keys  # type: ignore
        keys = load_keys()
        if keys:
            return list(keys)
    except Exception:
        pass
    pool_str = os.environ.get("ANTHROPIC_KEY_POOL", "").strip()
    if pool_str:
        keys = [k.strip() for k in pool_str.split(",") if k.strip()]
        if keys:
            return keys
    single = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    return [single] if single else []


def resolve_cut_workers(pool):
    """Concurrent category calls for a cut run.

    CUT_CATEGORY_WORKERS env overrides. Default: one worker per pooled
    key up to 10; with a single key, 4 concurrent calls stay far under
    the per-key Sonnet rate limit (calls run 30-90s each).
    """
    env = os.environ.get("CUT_CATEGORY_WORKERS", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    n = len(pool or [])
    if n <= 1:
        return 4
    return max(4, min(10, n))


def cut_claude_call(*, system, user, api_key=None,
                    max_tokens=24000, temperature=0.3):
    """One category-chunk reasoning call for a cut engine.

    With `api_key`: per-thread direct call (own Anthropic client, the
    system block prompt-cached, usage recorded in the run ledger) via
    the fresh-build engine's `_direct_claude_call`, with one retry on
    a different jittered backoff. Without: the legacy claude_client
    singleton (single env key), preserving pre-parallel behavior for
    ad-hoc local runs.

    Returns response text or '' on failure (never raises).
    """
    if api_key:
        _direct = None
        try:
            from scripts.synth_engine_row_by_row import _direct_claude_call
            _direct = _direct_claude_call
        except Exception:
            try:
                from synth_engine_row_by_row import (  # type: ignore
                    _direct_claude_call,
                )
                _direct = _direct_claude_call
            except Exception:
                _direct = None
        if _direct is not None:
            for _attempt in (1, 2):
                try:
                    resp = _direct(system, user, api_key,
                                   max_tokens=max_tokens,
                                   temperature=temperature)
                except Exception:
                    resp = ""
                if resp:
                    return resp
                time.sleep(2.0 + random.random() * 3.0)
            return ""
        # fall through to the singleton path when the engine module
        # isn't importable in this context

    try:
        from claude_client import claude_messages
    except Exception:
        try:
            from migration.claude_client import claude_messages
        except Exception:
            return ""
    try:
        return claude_messages(system=system, user=user,
                               max_tokens=max_tokens,
                               temperature=temperature)
    except Exception:
        return ""
