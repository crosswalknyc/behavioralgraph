"""Re-export shim -> migration.final_ship_gate (TWIN_SYNC_SHIM_V1).

MODULE-SHADOWING GUARD (2026-08-25): this root-level file used to be a
stale full copy of the maintained module and it shadowed the current
implementation for every root-first import (e.g. bg.py's
``from final_ship_gate import ...``), silently skipping newer logic.

The maintained implementation lives at ``migration/final_ship_gate.py``,
byte-synced from the parent repo's ``migration/final_ship_gate.py``.
This shim aliases THIS module name to that implementation in
``sys.modules``, so both import spellings resolve to the same module
object (all names re-exported, including underscore-prefixed helpers).

Do NOT replace this shim with a full copy. Byte-sync + shim-ness are
asserted by the parent repo's ``scripts/test_module_twin_sync.py`` (ci).
"""
import os as _os
import sys as _sys

_here = _os.path.dirname(_os.path.abspath(__file__))
if _here not in _sys.path:
    _sys.path.insert(0, _here)

import migration.final_ship_gate as _impl

_sys.modules[__name__] = _impl
