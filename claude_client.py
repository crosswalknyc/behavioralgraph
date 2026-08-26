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

# Model families that reject the `temperature` param (Anthropic began
# deprecating it on newer models; sending it returns a 400
# "temperature is deprecated for this model"). Checked upfront to skip
# a wasted roundtrip; the dynamic 400-retry below self-learns any new
# family and memoizes it here for the process lifetime.
_TEMP_UNSUPPORTED_FAMILIES = {"opus-5", "opus-4-8", "opus-4-7", "opus-4-6",
                              "thinking", "mythos"}
_temp_rejecting_models: set = set()


def _model_omits_temperature(model_id: str) -> bool:
    m = (model_id or "").lower()
    if model_id in _temp_rejecting_models:
        return True
    return any(f in m for f in _TEMP_UNSUPPORTED_FAMILIES)


def _is_temperature_rejected_error(e: Exception) -> bool:
    try:
        import anthropic
        if not isinstance(e, anthropic.BadRequestError):
            return False
    except Exception:
        return False
    return "temperature" in str(e).lower()


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
        # Explicit httpx timeouts so a stalled TCP / TLS read (e.g. Anthropic
        # API black-hole where headers never arrive) is killed instead of
        # blocking the worker forever. Field-tuned against UBG hangs that sat
        # in ssl.recv() for 13+ min with a single 600s wall-timeout that
        # never actually fired through to the underlying socket.
        #
        # 2026-08-20: httpx import made optional - anthropic 1.0.0 swapped
        # its HTTP stack to httpx2, and a missing classic httpx must not
        # take down the whole client. A plain seconds timeout still bounds
        # the read through the SDK.
        try:
            import httpx
            _http_timeout = httpx.Timeout(
                connect=30.0, read=180.0, write=60.0, pool=30.0)
        except ImportError:
            _http_timeout = 180.0
        _claude_client = anthropic.Anthropic(api_key=api_key, timeout=_http_timeout)
        return _claude_client
    except Exception as e:
        print(f"⚠️  Claude client init failed: {e}")
        _claude_init_failed = True
        return None


def _record_tagged_usage(usage_tag, model_id, resp,
                         duration_s=None) -> None:
    """Persist per-call usage when the caller tagged the request.

    usage_tag is a (surface, origin) tuple, e.g. ('interpret',
    'chatbot'), optionally (surface, origin, extras) where extras is
    an attribution dict (user, user_email, session_id, request_id,
    pay_per_use) for pay-as-you-go billing. duration_s is the
    wall-clock processing time of the call (metered-time billing,
    2026-08-26 Jenna: "pay per metered time and consumption").
    Untagged calls are skipped. Never raises."""
    if not usage_tag:
        return
    try:
        _u = getattr(resp, "usage", None)
        if _u is None:
            return
        extras = usage_tag[2] if len(usage_tag) > 2 else None
        import render_usage_log
        render_usage_log.record_call(usage_tag[0], usage_tag[1],
                                     model_id, _u, extras=extras,
                                     duration_s=duration_s)
    except Exception:
        pass


def claude_reason_json(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    max_retries: int = 3,
    raise_on_error: bool = False,
    usage_tag=None,
) -> str:
    """Send a single-shot reasoning prompt to Claude; return the raw text.

    Returns "" on any failure so callers can fall back to GPT or the panel
    default. The prompt should ask for JSON output; the caller is responsible
    for parsing.

    raise_on_error=True (2026-08-21): surface the underlying API error to
    the caller instead of returning "". The silent-"" contract made model
    fallback chains impossible - a permanent error (model not available to
    the key, bad request, auth) looked identical to a model that answered
    with unparseable prose, so callers reported "non-JSON output" and never
    advanced to their next candidate model. Existing callers that want the
    quiet fallback keep the default.
    """
    client = get_claude_client()
    if client is None:
        if raise_on_error:
            raise RuntimeError(
                "Claude client unavailable (anthropic not installed or "
                "ANTHROPIC_API_KEY missing)")
        return ""

    model_id = (
        model
        or os.environ.get("CLAUDE_REASONING_MODEL")
        or "claude-sonnet-4-5"
    )

    # Prompt caching: explicit cache_control on the system block so Anthropic
    # caches the (large, static) system text for 5 min. Cache hit pays ~10%
    # of base input cost; first write pays 125%. Min cacheable prefix on Opus
    # is 4096 tokens; shorter prompts are silently skipped. Disable via
    # CLAUDE_DISABLE_PROMPT_CACHE=1.
    _cache_disabled = (os.environ.get("CLAUDE_DISABLE_PROMPT_CACHE") or "").strip().lower() in ("1", "true", "yes", "on")

    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            if isinstance(system, str) and system and not _cache_disabled:
                _system_param = [{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }]
            else:
                _system_param = system
            _kwargs = dict(
                model=model_id,
                max_tokens=max_tokens,
                system=_system_param,
                messages=[{"role": "user", "content": user}],
            )
            if not _model_omits_temperature(model_id):
                _kwargs["temperature"] = temperature

            def _issue(_kw):
                # 2026-08-21: the SDK refuses non-streaming create when
                # max_tokens implies >10 min runtime (~24k on Sonnet);
                # stream + accumulate for big budgets, same response.
                if int(_kw.get("max_tokens") or 0) >= 12000:
                    with client.messages.stream(**_kw) as _s:
                        return _s.get_final_message()
                return client.messages.create(**_kw)

            _t0 = time.monotonic()
            try:
                resp = _issue(_kwargs)
            except Exception as _te:
                # Newer models 400 on `temperature`. Strip, memoize the
                # model, retry once immediately (2026-08-21: opus-5 /
                # opus-4-8 / opus-4-7 all reject it, which silently
                # broke the Prometheus analyze model chain).
                if ("temperature" in _kwargs
                        and _is_temperature_rejected_error(_te)):
                    print(f"⚠️  {model_id} rejects `temperature`; "
                          f"retrying without it")
                    _temp_rejecting_models.add(model_id)
                    _kwargs.pop("temperature", None)
                    resp = _issue(_kwargs)
                else:
                    raise
            _record_tagged_usage(usage_tag, model_id, resp,
                                 duration_s=time.monotonic() - _t0)
            try:
                _u = getattr(resp, "usage", None)
                if _u is not None:
                    _cr = getattr(_u, "cache_read_input_tokens", 0) or 0
                    _cw = getattr(_u, "cache_creation_input_tokens", 0) or 0
                    if _cr or _cw:
                        _in = getattr(_u, "input_tokens", 0) or 0
                        _out = getattr(_u, "output_tokens", 0) or 0
                        print(f"[claude-cache] read={_cr:,} write={_cw:,} "
                              f"input={_in:,} output={_out:,}")
            except Exception:
                pass
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
                    # Wall-clock watchdog raises builtin TimeoutError when a
                    # black-holed connection outlives the hard wall - always
                    # worth a retry on a fresh connection (2026-08-20).
                    TimeoutError,
                )
                if not isinstance(e, transient):
                    print(f"⚠️  Claude permanent error ({type(e).__name__}): {e}")
                    if raise_on_error:
                        raise
                    return ""
            except Exception as _cls_err:
                # The transient-classification itself failed (anthropic
                # import error etc.) - treat as transient unless the
                # caller wants errors surfaced.
                if raise_on_error and _cls_err is e:
                    raise
            wait = 2 ** attempt
            print(f"⚠️  Claude transient error (attempt {attempt+1}/{max_retries}, retry in {wait}s): {e}")
            time.sleep(wait)

    print(f"⚠️  Claude exhausted retries: {last_err}")
    if raise_on_error and last_err is not None:
        raise last_err
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

    _omit_temperature = _model_omits_temperature(model_id)

    # Prompt caching: explicit cache_control on the system block caches the
    # tools+system prefix for 5 min. Hits pay ~10% input cost vs 125% on first
    # write. Explicit breakpoint (not automatic top-level) because automatic
    # caching defaults to the last cacheable block — the per-request user
    # message — which never repeats. Per the Anthropic docs hierarchy
    # (tools → system → messages), a breakpoint on system caches both tools
    # and system. Min cacheable prefix on Opus is 4096 tokens. Disable via
    # CLAUDE_DISABLE_PROMPT_CACHE=1.
    _cache_disabled = (os.environ.get("CLAUDE_DISABLE_PROMPT_CACHE") or "").strip().lower() in ("1", "true", "yes", "on")

    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            if isinstance(system, str) and system and not _cache_disabled:
                _system_param = [{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }]
            else:
                _system_param = system
            kwargs = dict(
                model=model_id,
                max_tokens=max_tokens,
                system=_system_param,
                messages=[{"role": "user", "content": user}],
            )
            if not _omit_temperature:
                kwargs["temperature"] = temperature
            # Tool-using calls (web_search, code_execution, ...) legitimately
            # need 3-8 min for the model to run 8-12 sub-requests then compose.
            # Per-request override leaves the default 180s read in place for
            # the fast text-only calls.
            #
            # 2026-08-20: the override MUST stay a granular httpx.Timeout -
            # a plain float's read timer resets on every received byte,
            # which lets a black-holed connection hang recv forever.
            if tools:
                kwargs["tools"] = tools
                try:
                    import httpx as _hx
                    _tool_timeout = _hx.Timeout(
                        connect=30.0, read=600.0, write=60.0, pool=30.0)
                except ImportError:
                    _tool_timeout = 600.0
                _request_client = client.with_options(timeout=_tool_timeout)
            else:
                _request_client = client
            # Wall-clock watchdog (2026-08-20): bound the whole request with
            # a hard wall so a connection that trickles keepalive bytes
            # (defeating the per-read timer) cannot freeze this thread. The
            # hung helper thread is deliberately leaked; the retry loop
            # reissues on a fresh connection. Mirrors
            # migration/claude_client.py (Protein addon-cut stall, run
            # 56saCwlWGl5fAw sat 34 min in ssl.recv).
            from concurrent.futures import (
                ThreadPoolExecutor as _WallPool,
                TimeoutError as _WallTimeout,
            )
            _wall_s = 900.0 if tools else 420.0

            def _issue_request():
                # 2026-08-21: SDK raises ValueError("Streaming is
                # required...") client-side for large max_tokens (~24k
                # on Sonnet). Stream + accumulate for big budgets.
                if not tools and int(kwargs.get("max_tokens") or 0) >= 12000:
                    with _request_client.messages.stream(**kwargs) as _s:
                        return _s.get_final_message()
                return _request_client.messages.create(**kwargs)

            _pool = _WallPool(max_workers=1)
            try:
                _fut = _pool.submit(_issue_request)
                try:
                    resp = _fut.result(timeout=_wall_s)
                except _WallTimeout:
                    raise TimeoutError(
                        f"claude_messages wall-timeout after {_wall_s:.0f}s "
                        f"(attempt {attempt + 1}/{max_retries}); reissuing "
                        f"on a fresh connection")
                except Exception as _te:
                    # Self-learn `temperature` deprecation on models the
                    # static family list doesn't know yet (2026-08-21).
                    if ("temperature" in kwargs
                            and _is_temperature_rejected_error(_te)):
                        print(f"⚠️  {model_id} rejects `temperature`; "
                              f"retrying without it")
                        _temp_rejecting_models.add(model_id)
                        kwargs.pop("temperature", None)
                        _fut2 = _pool.submit(_issue_request)
                        resp = _fut2.result(timeout=_wall_s)
                    else:
                        raise
            finally:
                _pool.shutdown(wait=False)
            try:
                _u = getattr(resp, "usage", None)
                if _u is not None:
                    _cr = getattr(_u, "cache_read_input_tokens", 0) or 0
                    _cw = getattr(_u, "cache_creation_input_tokens", 0) or 0
                    if _cr or _cw:
                        _in = getattr(_u, "input_tokens", 0) or 0
                        _out = getattr(_u, "output_tokens", 0) or 0
                        print(f"[claude-cache] read={_cr:,} write={_cw:,} "
                              f"input={_in:,} output={_out:,}")
            except Exception:
                pass
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
                    # Wall-clock watchdog raises builtin TimeoutError when a
                    # black-holed connection outlives the hard wall - always
                    # worth a retry on a fresh connection (2026-08-20).
                    TimeoutError,
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
