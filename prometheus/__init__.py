"""Prometheus: one brain, one entry point, many clients.

The package is the boundary between the Prometheus intelligence and
the surfaces that use it (dashboard widget, standalone app, customer
API). Every surface posts the same request shape to one entry point,
``prometheus.service.ask``, and renders the same response envelope.
The server decides what an ask is; no client carries routing logic.

Layout
------
host.py        explicit registry of the few app-level capabilities the
               brain needs (auth gate, wallet gates, history store, the
               two request cores). Bound once by ``init_app``.
understand.py  the single server-side decision step: which surface
               (analyze / interpret / deck), which mode, why.
envelope.py    the typed response contract every client renders.
service.py     ``ask()``: understand -> gate -> run -> persist -> wrap.
blueprint.py   the HTTP surface at /api/prometheus/v1/* (session or
               API key, one wallet either way).

Phase 1 (2026-10-01, Jenna: same repo, same wallet, dashboard stays
the main surface). The legacy ``_pm_*`` helpers still live in app.py
and are reached through ``host``; they migrate into this package in
tested slices behind the same contract, so no client ever changes.
"""

__version__ = '1.0.0'


def init_app(app, host_module):
    """Bind the host capabilities and register the HTTP surface.

    Called once at the end of app.py after every helper is defined.
    Never raises: a binding failure logs and leaves the legacy routes
    untouched, so the dashboard keeps working even if the new surface
    cannot start.
    """
    from . import host as _host
    try:
        _host.bind_from_app_module(host_module)
    except Exception as e:  # pragma: no cover - defensive at boot
        print(f"[prometheus] host bind failed: {e}")
        return False
    try:
        from .blueprint import bp
        app.register_blueprint(bp)
        print(f"[prometheus] v{__version__} surface registered at "
              f"{bp.url_prefix}")
        return True
    except Exception as e:  # pragma: no cover - defensive at boot
        print(f"[prometheus] blueprint registration failed: {e}")
        return False
