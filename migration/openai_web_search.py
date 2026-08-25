"""Shared OpenAI live web-search helper (Responses API).

OpenAI deprecated the chat-completions search models
(`gpt-4o-search-preview`, `gpt-4o-mini-search-preview`) - they return
404 model_not_found as of 2026-08. Discovered 2026-08-20 when the
Go-GURT Consumers (18-24) build failed persona research: stage 1
404'd, the plain gpt-4o fallback couldn't web-search, and the empty
doc failed the build (correctly - the worker guard did its job).

Live web search now runs through the Responses API `web_search` tool
on current models. This module is the single shared entry point so
every caller (chatbot persona research, BG.py persona agent + demo
research, brand-intelligence refresh) migrates in one place and the
next model deprecation is a one-line fix.

Usage:
    from migration.openai_web_search import openai_web_search_call
    text = openai_web_search_call(prompt)                  # cascade
    text = openai_web_search_call(prompt, model='gpt-4.1') # pin first

Env:
    OPENAI_WEB_SEARCH_MODEL  override the primary model (optional)

Verified alive on this account 2026-08-20: gpt-5.2, gpt-4.1, gpt-5
(all three answered a live-fact probe through the web_search tool).

2026-08-20 (Jenna directive after the retirement outage): if EVERY
OpenAI model fails, the helper now falls back to Claude with its own
server-side web_search tool by default, so a full OpenAI-side outage
degrades to a different engine instead of a dead build. Callers that
run their own labeled fallback ladder (synth_persona_research) pass
claude_fallback=False to keep their engine attribution honest.
"""
from __future__ import annotations
import os

# Primary first. gpt-5.2 gave the crispest sourced answer in the
# 2026-08-20 probe; gpt-4.1 is the non-reasoning fallback (cheaper,
# no thinking-token overhead); gpt-5 is the last resort.
DEFAULT_MODEL_CASCADE = ('gpt-5.2', 'gpt-4.1', 'gpt-5')

# Claude fallback: current web_search tool descriptor first, then the
# legacy one (matches migration/hybrid_reasoning.py + persona research).
CLAUDE_FALLBACK_MODEL = os.environ.get(
    'CLAUDE_WEB_SEARCH_MODEL', 'claude-sonnet-4-6')
_CLAUDE_SEARCH_TOOLS = (
    {'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 10},
    {'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 10},
)


def _claude_web_search_fallback(prompt: str, timeout: float) -> str:
    """All-OpenAI-down fallback: Claude with live web search. Returns
    '' when Claude is also unavailable (no key / both tool descriptors
    rejected)."""
    try:
        import anthropic
    except Exception as e:
        print(f'[openai-web-search] anthropic import failed: {e}')
        return ''
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not api_key:
        print('[openai-web-search] ANTHROPIC_API_KEY not set; '
              'no Claude fallback available')
        return ''
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
    for tool in _CLAUDE_SEARCH_TOOLS:
        try:
            resp = client.messages.create(
                model=CLAUDE_FALLBACK_MODEL,
                max_tokens=12000,
                temperature=0.3,
                messages=[{'role': 'user', 'content': prompt}],
                tools=[tool],
            )
            try:
                try:
                    from migration import usage_tracker as _ut
                except Exception:
                    import usage_tracker as _ut  # type: ignore
                _u = getattr(resp, 'usage', None)
                _stu = (getattr(_u, 'server_tool_use', None)
                        if _u is not None else None)
                if isinstance(_stu, dict):
                    _ws = int(_stu.get('web_search_requests') or 0)
                else:
                    _ws = int(getattr(_stu, 'web_search_requests', 0) or 0)
                _ut.record(CLAUDE_FALLBACK_MODEL, _u,
                           web_search_queries=_ws)
            except Exception:
                pass
            parts = [b.text for b in (getattr(resp, 'content', None) or [])
                     if getattr(b, 'type', None) == 'text'
                     and getattr(b, 'text', '')]
            text = '\n'.join(parts).strip()
            if text:
                print(f'[openai-web-search] Claude fallback answered '
                      f'({CLAUDE_FALLBACK_MODEL}, {tool["type"]})')
                return text
        except Exception as e:
            print(f'[openai-web-search] Claude fallback '
                  f'({tool["type"]}) failed: {type(e).__name__}: '
                  f'{str(e)[:160]}')
    return ''


def openai_web_search_call(prompt: str,
                           model: str | None = None,
                           timeout: float = 240.0,
                           claude_fallback: bool = True) -> str:
    """Run `prompt` through the Responses API with the web_search tool.

    Tries the given/env/default model first, then the rest of the
    cascade, then (by default) Claude with its own web_search tool.
    Returns the final text, or '' when every engine fails.
    """
    _openai_ok = True
    try:
        from openai import OpenAI
    except Exception as e:
        print(f'[openai-web-search] openai import failed: {e}')
        _openai_ok = False
    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    if _openai_ok and not api_key:
        print('[openai-web-search] OPENAI_API_KEY not set')
        _openai_ok = False

    if _openai_ok:
        models: list[str] = []
        pinned = (model or '').strip() or \
            os.environ.get('OPENAI_WEB_SEARCH_MODEL', '').strip()
        if pinned:
            models.append(pinned)
        for m in DEFAULT_MODEL_CASCADE:
            if m not in models:
                models.append(m)

        client = OpenAI(api_key=api_key, timeout=timeout)
        for m in models:
            try:
                resp = client.responses.create(
                    model=m,
                    tools=[{'type': 'web_search'}],
                    input=prompt,
                )
                text = (getattr(resp, 'output_text', '') or '').strip()
                if text:
                    return text
                print(f'[openai-web-search] {m} returned empty '
                      f'output_text; trying next model')
            except Exception as e:
                print(f'[openai-web-search] {m} failed: '
                      f'{type(e).__name__}: {str(e)[:200]}')

    if claude_fallback:
        print('[openai-web-search] every OpenAI path failed; '
              'falling back to Claude web search')
        return _claude_web_search_fallback(prompt, timeout)
    return ''


__all__ = ['openai_web_search_call', 'DEFAULT_MODEL_CASCADE',
           'CLAUDE_FALLBACK_MODEL']
