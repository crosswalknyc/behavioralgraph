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
"""
from prometheus.host import HostNotBound

__all__ = ['H', 'bind', 'exports', 'HostNotBound']


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


H = _HostProxy()


def bind(mod):
    """Bind the host namespace (a module or any object with attributes)."""
    H._bind(mod)


def exports(module):
    """The moved names a host re-exports into its own namespace."""
    names = getattr(module, '_EXPORTS', ())
    return {n: getattr(module, n) for n in names}
