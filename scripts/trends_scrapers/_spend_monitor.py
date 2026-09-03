"""
SpendMonitor - hard USD cap for Anthropic Claude API usage.

Reads per-model token prices from the constant below (Sonnet 4.5 and
Haiku 4.5 - the two tiers `stream_estimates.py` uses). Every response
(batch or serial) is metered via `response.usage.input_tokens` /
`response.usage.output_tokens`; batch responses are billed at 50%.
Web-search tool calls are metered at $10 / 1000 searches.

Interface:

    monitor = SpendMonitor(cap_usd=100.0, prefix='backfill_sparse')
    monitor.record_response(usage_dict, model='claude-sonnet-4-5',
                            batch=False, num_searches=1)
    if monitor.tripped():
        # halt everything, cancel pending batches, exit non-zero.
        ...
    monitor.preflight_estimate(num_msgs=200,
                               tokens_in=3000, tokens_out=500,
                               model='claude-sonnet-4-5', batch=True)

The rate table matches the Anthropic pricing docs
(https://docs.anthropic.com/en/docs/build-with-claude/batch-processing
and the models overview page):

  claude-sonnet-4-5 (Sonnet 4.5): $3 / MTok input, $15 / MTok output
  claude-haiku-4-5  (Haiku 4.5):  $1 / MTok input, $5 / MTok output
  web_search:                     $10 / 1000 searches

Batch mode: input + output prices are multiplied by 0.5. Web search
tool calls are NOT discounted in batch mode.

This module is intentionally free of any Claude SDK dependency: the
caller passes usage counts and model IDs in.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# Model prices per million tokens (standard / non-batch). Keys accept
# both the Anthropic alias (claude-sonnet-4-5) and the pinned snapshot
# form (claude-sonnet-4-5-20250929) so callers don't have to normalise.
_PRICES_PER_MTOK: dict[str, dict[str, float]] = {
    'claude-sonnet-4-5':            {'in': 3.00, 'out': 15.00},
    'claude-sonnet-4-5-20250929':   {'in': 3.00, 'out': 15.00},
    'claude-haiku-4-5':             {'in': 1.00, 'out': 5.00},
    'claude-haiku-4-5-20251001':    {'in': 1.00, 'out': 5.00},
}

# Web search: flat $10 per 1000 tool calls (Anthropic docs, native
# web_search_20250305 tool). NOT discounted in batch mode.
_WEB_SEARCH_USD_PER_CALL = 10.0 / 1000.0
WEB_SEARCH_USD_PER_CALL = _WEB_SEARCH_USD_PER_CALL   # public alias


def _price_for(model: str) -> dict[str, float]:
    """Return {'in': ..., 'out': ...} per MTok for `model`. Falls back
    to the Sonnet 4.5 rate on unknown models so an accidental slug
    typo counts as the more expensive tier (safer)."""
    if not model:
        return _PRICES_PER_MTOK['claude-sonnet-4-5']
    m = model.strip()
    if m in _PRICES_PER_MTOK:
        return _PRICES_PER_MTOK[m]
    # Prefix match on the alias for future pinned snapshots.
    for alias, price in _PRICES_PER_MTOK.items():
        if m.startswith(alias):
            return price
    logger.warning("SpendMonitor: unknown model %r; charging at "
                    "Sonnet 4.5 rate (safer).", m)
    return _PRICES_PER_MTOK['claude-sonnet-4-5']


def cost_of(input_tokens: int, output_tokens: int,
            model: str, batch: bool = False,
            num_searches: int = 0) -> float:
    """Return USD cost for one response. Pure function - safe to call
    outside the monitor for one-off preflight."""
    p = _price_for(model)
    factor = 0.5 if batch else 1.0
    cost_in  = (max(0, int(input_tokens))  / 1_000_000.0) * p['in']  * factor
    cost_out = (max(0, int(output_tokens)) / 1_000_000.0) * p['out'] * factor
    cost_search = max(0, int(num_searches)) * _WEB_SEARCH_USD_PER_CALL
    return cost_in + cost_out + cost_search


@dataclass
class SpendMonitor:
    """Thread-safe running-total spend meter with a hard cap.

    All prices are per-response, so `record_response` should be called
    once per completed Claude turn (or once per per-item result read
    from a batch fetch). Once the running total crosses `cap_usd`, the
    monitor is TRIPPED and every subsequent `tripped()` call returns
    True. The caller is responsible for actually halting the loop and
    (for batch mode) cancelling any pending batches.
    """

    cap_usd: float
    prefix: str = ''
    _total: float = 0.0
    _tripped: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, cost: float, note: str = '') -> None:
        """Direct cost record - used by callers who computed cost
        externally (e.g. preflight-estimated a batch that then landed
        exactly, or an S3-only op with no Claude call)."""
        with self._lock:
            self._total += float(cost or 0.0)
            if self._total >= self.cap_usd and not self._tripped:
                self._tripped = True
                logger.error("[%s SpendMonitor] TRIPPED at $%.2f "
                              "(cap $%.2f). %s",
                              self.prefix or 'spend', self._total,
                              self.cap_usd, note or '')

    def record_response(self, usage: dict, model: str,
                        batch: bool = False,
                        num_searches: int = 0) -> float:
        """Meter one Claude response. `usage` should be a dict-like
        with `input_tokens` and `output_tokens` (matches both the raw
        SDK object attributes and Batch API result payloads).

        Returns the incremental USD cost so the caller can log per-
        item. Includes `cache_creation_input_tokens` and
        `cache_read_input_tokens` in the input total if present
        (charged separately at cache-write / cache-read rates in
        production; here we count them as regular input for a
        conservative overestimate, which is exactly what we want for
        a hard cap)."""
        in_tok = 0
        out_tok = 0
        if isinstance(usage, dict):
            in_tok  = int(usage.get('input_tokens')  or 0)
            out_tok = int(usage.get('output_tokens') or 0)
            # Cache tokens - roll into input total as an overestimate.
            in_tok += int(usage.get('cache_creation_input_tokens') or 0)
            in_tok += int(usage.get('cache_read_input_tokens')     or 0)
            # server_tool_use.web_search_requests reports the actual
            # number of web_search calls the model made in the turn.
            stu = usage.get('server_tool_use') or {}
            if isinstance(stu, dict):
                num_searches = max(num_searches,
                                    int(stu.get('web_search_requests') or 0))
        else:
            # SDK object - read attributes.
            in_tok  = int(getattr(usage, 'input_tokens',  0) or 0)
            out_tok = int(getattr(usage, 'output_tokens', 0) or 0)
            in_tok += int(getattr(usage, 'cache_creation_input_tokens', 0) or 0)
            in_tok += int(getattr(usage, 'cache_read_input_tokens',     0) or 0)
            stu = getattr(usage, 'server_tool_use', None)
            if stu is not None:
                num_searches = max(num_searches,
                                    int(getattr(stu, 'web_search_requests', 0) or 0))

        cost = cost_of(in_tok, out_tok, model=model,
                        batch=batch, num_searches=num_searches)
        self.record(cost, note=f'model={model} batch={batch} '
                                 f'in={in_tok} out={out_tok} '
                                 f'searches={num_searches}')
        return cost

    def preflight_estimate(self, num_msgs: int,
                           tokens_in_per_msg: int = 3000,
                           tokens_out_per_msg: int = 500,
                           model: str = 'claude-sonnet-4-5',
                           batch: bool = False,
                           searches_per_msg: int = 0) -> float:
        """Return the estimated USD cost of running `num_msgs`
        responses at the given per-message token / search assumption.
        No side effects."""
        per = cost_of(tokens_in_per_msg, tokens_out_per_msg,
                       model=model, batch=batch,
                       num_searches=searches_per_msg)
        return per * max(0, int(num_msgs))

    def tripped(self) -> bool:
        with self._lock:
            return self._tripped

    def total(self) -> float:
        with self._lock:
            return self._total

    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self.cap_usd - self._total)

    def would_fit(self, cost: float) -> bool:
        """Return True iff recording `cost` would keep the running
        total strictly below the cap."""
        with self._lock:
            return (self._total + float(cost or 0.0)) < self.cap_usd

    def summary(self) -> dict:
        with self._lock:
            return {
                'prefix':    self.prefix,
                'cap_usd':   round(self.cap_usd, 4),
                'total_usd': round(self._total,  4),
                'remaining': round(max(0.0, self.cap_usd - self._total), 4),
                'tripped':   self._tripped,
            }


__all__ = [
    'SpendMonitor',
    'cost_of',
]
