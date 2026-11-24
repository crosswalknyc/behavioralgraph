"""Host proxy for the legacy Prometheus code (2026-10-01).

The legacy chat module (``prometheus.legacy.chat``) used to live inline
in app.py and refers to a few dozen app globals by name: the Flask
``app``, ``s3_client``, ``requires_auth``, ``load_users``, the credits
constants, the brief-draft helpers. Instead of importing app (circular
and host-specific) the moved code reads those names as ``_H.<name>``
through this proxy. The host binds its own module namespace once, before
importing the chat module, and every lookup resolves at call time
against whatever is bound. A second host (the standalone Prometheus
service) binds a namespace that provides the same names.

Nothing here knows app.py's spelling of anything; the names are
whatever the moved code already used.

Since rebuild stage 7 (2026-11-23) ``chat.py`` is a shim: its imports, one
marker line per block that moved out (``# <<extracted: <module> block N>>``),
``_EXPORTS``, and the tail re-imports that bind every moved name back into
its namespace. The code lives in ``prometheus/<family>.py`` modules
(library_match, thread_history, model_chain, brief_prompts, brief_draft,
answer_gate, interpret_turn, spec_from_draft, incidence_check,
clarify_route, approve_route, generated_read, read_jobs, generation_base,
subject_verify, ask_memory, page_analysis, page_context, screen_bind,
base_clarify, intake_turn, ask_intercepts, feedback_bank, access_gates,
file_replies, product_orders, deck_route, date_snapshot, correction_page)
beside the earlier families still here (threads, jobs, land, notify, watch,
rankers). Each reads this namespace through ``C`` at call time, so a
patch of ``chat.<name>`` reaches every caller. The suites read the legacy
text through ``scripts/_pm_legacy_view.legacy_view()``, which splices the
blocks back at their markers; a change to a moved function goes in its
module, never here. The tool that moves a family is
``scripts/extract_legacy_core.py``.
"""
from prometheus.host import HostNotBound

__all__ = ['H', 'C', 'bind', 'bind_core', 'exports', 'HostNotBound']


class _HostProxy:
    """Attribute reads forward to the bound host namespace."""

    def __init__(self):
        object.__setattr__(self, '_mod', None)

    def _bind(self, mod):
        object.__setattr__(self, '_mod', mod)

    @property
    def bound(self):
        return object.__getattribute__(self, '_mod') is not None

    def __getattr__(self, name):
        mod = object.__getattribute__(self, '_mod')
        if mod is None:
            raise HostNotBound(
                f'prometheus.legacy host is not bound (wanted {name!r})')
        try:
            return getattr(mod, name)
        except AttributeError:
            raise HostNotBound(name) from None

    def __setattr__(self, name, value):
        raise AttributeError('the host proxy is read-only')

    def get(self, name, default=None):
        """``globals().get(name)`` equivalent against the host namespace."""
        mod = object.__getattribute__(self, '_mod')
        if mod is None:
            return default
        return getattr(mod, name, default)

    def globals(self):
        """The bound module's own namespace dict, what ``globals()`` returned
        inside chat.py. An extracted core (stage 5, 2026-11-20) writes
        ``_C.globals()`` where chat.py wrote ``globals()``, so a lane handed
        the namespace (the wizard's store, the insight hook, the twin lane)
        still reads chat.py's helpers from it."""
        mod = object.__getattribute__(self, '_mod')
        if mod is None:
            raise HostNotBound('prometheus.legacy host is not bound (wanted globals())')
        return vars(mod)


H = _HostProxy()

# ``C`` is the same proxy shape pointed at the legacy chat module itself
# (2026-10-02 RCA W3). Families extracted out of chat.py (threads.py,
# jobs.py, ...) read the helpers that still live in chat.py as
# ``_C.<name>`` at call time, so a family never imports chat.py and
# chat.py imports the families last. Tests that exec the legacy text
# strip ``_C.`` exactly as they strip ``_H.`` (scripts/_pm_test_source).
C = _HostProxy()


def bind(mod):
    """Bind the host namespace (a module or any object with attributes)."""
    H._bind(mod)
    try:   # the legacy code reads the store client off the host module: the probe rule sits on that client too
        from prometheus import probe_files as _pf
        _pf.install(getattr(mod, 's3_client', None))
    except Exception:
        pass
    try:   # and the store's document mirror (rebuild stage 6, 2026-11-20): every put of a thread, a read job
        # document or the ledger index is mirrored after S3 takes it; off unless the store is on
        from prometheus import store as _st
        _st.install_mirror(getattr(mod, 's3_client', None))
    except Exception:
        pass


def bind_core(mod):
    """Bind the legacy chat module as the core the families read from."""
    C._bind(mod)


def exports(module):
    """The moved names a host re-exports into its own namespace."""
    names = getattr(module, '_EXPORTS', ())
    return {n: getattr(module, n) for n in names}
