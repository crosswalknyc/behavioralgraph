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
"""
from __future__ import annotations
import os

# Primary first. gpt-5.2 gave the crispest sourced answer in the
# 2026-08-20 probe; gpt-4.1 is the non-reasoning fallback (cheaper,
# no thinking-token overhead); gpt-5 is the last resort.
DEFAULT_MODEL_CASCADE = ('gpt-5.2', 'gpt-4.1', 'gpt-5')


def openai_web_search_call(prompt: str,
                           model: str | None = None,
                           timeout: float = 240.0) -> str:
    """Run `prompt` through the Responses API with the web_search tool.

    Tries the given/env/default model first, then the rest of the
    cascade. Returns the model's final text, or '' when every model
    fails (caller decides how to fall back - typically Claude with its
    own web_search tool, then a no-search model).
    """
    try:
        from openai import OpenAI
    except Exception as e:
        print(f'[openai-web-search] openai import failed: {e}')
        return ''
    api_key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not api_key:
        print('[openai-web-search] OPENAI_API_KEY not set')
        return ''

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
            print(f'[openai-web-search] {m} returned empty output_text; '
                  f'trying next model')
        except Exception as e:
            print(f'[openai-web-search] {m} failed: '
                  f'{type(e).__name__}: {str(e)[:200]}')
    return ''


__all__ = ['openai_web_search_call', 'DEFAULT_MODEL_CASCADE']
