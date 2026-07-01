"""Anthropic API key pool dedicated to avid + audience-cut skin synthesis.

Per Jenna 2026-06-12 directive: a separate set of API keys are saved
specifically for avid-skin work, so the avid-skin orchestrators get
their own rate-limit budget independent of the main BG.py pipeline.

The pool is loaded from a plaintext file:

    /root/finished_codes/.env.avid_skins      # on Hetzner
    <repo_root>/.env.avid_skins               # locally if present

File format: one key per line prefixed with ``KEY=``. Lines starting
with ``#`` and blank lines are ignored. Example::

    # primary
    KEY=sk-ant-api03-...
    KEY=sk-ant-api03-...

The file MUST be gitignored (we add `.env.avid_skins` to .gitignore).

Workflow for parallel orchestrators:

    from avid_key_pool import load_keys, set_worker_key
    keys = load_keys()                    # list[str], len >= 1
    # In each worker process (BEFORE importing claude_client):
    set_worker_key(keys[worker_idx % len(keys)])

`set_worker_key` writes the chosen key into ``ANTHROPIC_API_KEY`` in
``os.environ`` so claude_client.get_claude_client() picks it up on
first call inside the worker.
"""
from __future__ import annotations

import os
from typing import List, Optional

DEFAULT_PATHS = (
    "/root/finished_codes/.env.avid_skins",
    os.path.expanduser("~/finished_codes/.env.avid_skins"),
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".env.avid_skins",
    ),
)


def load_keys(path: Optional[str] = None) -> List[str]:
    """Load the avid-skin API key pool. Returns a list of keys in file
    order. If the file is missing or empty, falls back to a single-
    element list containing ``ANTHROPIC_API_KEY`` (so callers can
    treat the result uniformly). Returns an empty list only if NO
    key is available anywhere.
    """
    candidates = [path] if path else list(DEFAULT_PATHS)
    keys: List[str] = []
    for p in candidates:
        if not p or not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("KEY="):
                        v = line[4:].strip().strip('"').strip("'")
                        if v and v.startswith("sk-ant"):
                            keys.append(v)
            if keys:
                break  # first existing file wins
        except Exception:
            continue
    if not keys:
        primary = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        if primary:
            keys.append(primary)
    return keys


def set_worker_key(key: str) -> None:
    """Override ``ANTHROPIC_API_KEY`` in ``os.environ`` for this process.
    Must be called BEFORE any module that imports claude_client (the
    SDK client caches the key on first use)."""
    if key and key.startswith("sk-ant"):
        os.environ["ANTHROPIC_API_KEY"] = key


def assign_keys(n_workers: int, keys: Optional[List[str]] = None
                ) -> List[str]:
    """Assign a key to each of `n_workers` worker slots. If there are
    fewer keys than workers, the keys are repeated in round-robin
    order so every worker gets one.
    """
    if keys is None:
        keys = load_keys()
    if not keys:
        return [""] * n_workers
    return [keys[i % len(keys)] for i in range(n_workers)]


__all__ = ["load_keys", "set_worker_key", "assign_keys", "DEFAULT_PATHS"]
