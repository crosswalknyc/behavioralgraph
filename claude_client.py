"""Claude (Anthropic) client wrapper for the SVOD attribution reasoning steps.

Used for the *judgment-heavy* reasoning steps in SVOD_Churn_Attribution.py:
  - _reason_conversion_rate (show-aware conversion validation)
  - _reason_reactivation_rate (dormant-account reactivation share)
  - viewer-research safety-net validation

Web research stays on GPT-4o-search-preview (it has native web grounding;
Claude does not).  Per-step routing is controlled by env vars:

  USE_CLAUDE_REASONING     - if truthy, route reasoning calls to Claude
  CLAUDE_REASONING_MODEL   - override model id (default: claude-sonnet-4-5)
  ANTHROPIC_API_KEY        - required for any Claude call

If anthropic is not installed or the API key is missing, every helper
returns "" and the caller is expected to fall back to GPT.
"""

from __future__ import annotations

import os
import time
from typing import Optional

_claude_client = None
_claude_init_failed = False


def is_claude_reasoning_enabled() -> bool:
    """True iff USE_CLAUDE_REASONING is truthy AND anthropic key is set.

    Truthy values: "1", "true", "yes", "on" (case-insensitive).
    """
    val = (os.environ.get("USE_CLAUDE_REASONING") or "").strip().lower()
    if val not in ("1", "true", "yes", "on"):
        return False
    return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())


def is_hybrid_enabled() -> bool:
    """True iff USE_HYBRID_REASONING is truthy AND anthropic key is set.

    Mirrors `migration/claude_client.is_hybrid_enabled` so this module can
    serve both the SVOD attribution path and the BG.py hybrid-reasoning
    pipeline regardless of sys.path resolution order.
    """
    val = (os.environ.get("USE_HYBRID_REASONING") or "").strip().lower()
    if val not in ("1", "true", "yes", "on"):
        return False
    return bool((os.environ.get("ANTHROPIC_API_KEY") or "").strip())


def get_claude_client():
    """Return a singleton anthropic.Anthropic client, or None on failure."""
    global _claude_client, _claude_init_failed
    if _claude_client is not None:
        return _claude_client
    if _claude_init_failed:
        return None

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        _claude_init_failed = True
        return None

    try:
        import anthropic
        _claude_client = anthropic.Anthropic(api_key=api_key, timeout=180.0)
        return _claude_client
    except Exception as e:
        print(f"⚠️  Claude client init failed: {e}")
        _claude_init_failed = True
        return None


def claude_reason_json(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    max_retries: int = 3,
) -> str:
    """Send a single-shot reasoning prompt to Claude; return the raw text.

    Returns "" on any failure so callers can fall back to GPT or the panel
    default. The prompt should ask for JSON output; the caller is responsible
    for parsing.
    """
    client = get_claude_client()
    if client is None:
        return ""

    model_id = (
        model
        or os.environ.get("CLAUDE_REASONING_MODEL")
        or "claude-sonnet-4-5"
    )

    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = client.messages.create(
                model=model_id,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            blocks = resp.content or []
            for b in blocks:
                txt = getattr(b, "text", None)
                if txt:
                    return txt
            return ""
        except Exception as e:
            last_err = e
            try:
                import anthropic
                transient = (
                    anthropic.RateLimitError,
                    anthropic.APIConnectionError,
                    anthropic.APITimeoutError,
                    anthropic.InternalServerError,
                )
                if not isinstance(e, transient):
                    print(f"⚠️  Claude permanent error ({type(e).__name__}): {e}")
                    return ""
            except Exception:
                pass
            wait = 2 ** attempt
            print(f"⚠️  Claude transient error (attempt {attempt+1}/{max_retries}, retry in {wait}s): {e}")
            time.sleep(wait)

    print(f"⚠️  Claude exhausted retries: {last_err}")
    return ""


def claude_messages(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    max_tokens: int = 4096,
    temperature: float = 0.4,
    max_retries: int = 3,
    tools: Optional[list] = None,
) -> str:
    """Single-shot Claude call returning text. Mirrors migration/claude_client.

    Used by:
      - BG.py persona_research_agent (Opus 4.7 + web_search primary path)
      - migration/hybrid_reasoning.holistic_sanity_check
      - any future Claude-driven reasoning step

    Supports `tools=` for native web_search. Returns "" on any failure so
    callers can fall back to GPT.

    Note: Opus 4.7+ and other extended-thinking models reject the temperature
    parameter (it's fixed for those models). We strip it for those families.
    """
    client = get_claude_client()
    if client is None:
        return ""

    model_id = (
        model
        or os.environ.get("CLAUDE_REASONING_MODEL")
        or "claude-sonnet-4-5"
    )

    _model_lc = (model_id or "").lower()
    _omit_temperature = (
        "opus-4-7" in _model_lc
        or "opus-4-6" in _model_lc
        or "thinking" in _model_lc
        or "mythos" in _model_lc
    )

    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            kwargs = dict(
                model=model_id,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            if not _omit_temperature:
                kwargs["temperature"] = temperature
            if tools:
                kwargs["tools"] = tools
            resp = client.messages.create(**kwargs)
            # Concatenate all text blocks (web_search responses interleave
            # tool_use, server_tool_use, web_search_tool_result and text).
            blocks = resp.content or []
            text_parts: list[str] = []
            for b in blocks:
                txt = getattr(b, "text", None)
                if txt:
                    text_parts.append(txt)
            return "\n".join(text_parts) if text_parts else ""
        except Exception as e:
            last_err = e
            try:
                import anthropic
                transient = (
                    anthropic.RateLimitError,
                    anthropic.APIConnectionError,
                    anthropic.APITimeoutError,
                    anthropic.InternalServerError,
                )
                if not isinstance(e, transient):
                    print(f"⚠️  Claude permanent error ({type(e).__name__}): {e}")
                    return ""
            except Exception:
                pass
            wait = 2 ** attempt
            print(f"⚠️  Claude transient error (attempt {attempt+1}/{max_retries}, retry in {wait}s): {e}")
            time.sleep(wait)

    print(f"⚠️  Claude exhausted retries: {last_err}")
    return ""


__all__ = [
    "is_claude_reasoning_enabled",
    "is_hybrid_enabled",
    "get_claude_client",
    "claude_reason_json",
    "claude_messages",
]
