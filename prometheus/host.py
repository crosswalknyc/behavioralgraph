"""Explicit registry of what the Prometheus package needs from the host
application.

Instead of reaching into app.py's 73,000 lines by name, the package
declares the handful of capabilities it depends on. ``init_app`` binds
them once from the app module. Anything not listed here is not a
dependency, which is what lets the package move to its own service
later: the second host just binds the same names.

Every attribute is read through ``host.<name>``. Missing bindings raise
``HostNotBound`` with the capability name, never a bare NameError.
"""

# Name in this registry -> name of the callable / constant on app.py.
# Keep this list the single place that knows app.py's spelling.
_APP_BINDINGS = {
    # identity and wallet
    'gate': '_synth_chat_gate',             # (allow_api_key) -> (user, err)
    'gate_pull': '_pm_gate_pull',           # (user) -> bool
    'gate_analyze': '_pm_gate_analyze',     # (user) -> bool
    'gate_refusal': '_pm_gate_refusal',     # (kind) -> Response
    'funds_gate': '_pm_funds_gate',         # (user) -> Response | None
    'access_gate': '_pm_access_gate',       # (user) -> Response | None
    'wallet_snapshot': '_caller_wallet_snapshot',  # (username) -> dict
    # request cores (split out of the legacy views in Phase 1)
    'interpret_core': '_pm_interpret_core',  # (user, body, text, history)
    'analyze_core': '_pm_analyze_core',      # (user, body, text, history)
    'deck_core': '_pm_deck_core',            # (user, body)
    # ask-level helpers the decision step reuses
    'bpiq_intent': '_pm_bpiq_intent',
    'jiq_intent': '_pm_jiq_intent',
    'fw_intent': '_pm_fw_intent',
    'aiq_intent': '_pm_aiq_intent',
    'pricing_question': '_pm_pricing_question',
    'ask_hint': '_pm_ask_hint',
    # thread store
    'load_history': '_load_synth_chat_history',
    'save_history': '_save_synth_chat_history',
    'load_threads_index': '_load_threads_index',
    'thread_key': '_pm_thread_key',
    'threads_index_key': '_pm_threads_index_key',
    'thread_title_from': '_pm_thread_title_from',
    's3_json': '_pm_s3_json',
    's3_put_json': '_pm_s3_put_json',
    'max_threads': '_PM_MAX_THREADS',
    # jobs
    'job_owner_ok': '_pm_job_owner_ok',
    'read_prefix': '_PM_READ_PREFIX',
    'deck_prefix': '_PM_DECK_PREFIX',
    'bpiq_prefix': '_PM_BPIQ_JOB_PREFIX',
    'jiq_prefix': '_PM_JIQ_JOB_PREFIX',
    'fw_prefix': '_PM_FW_JOB_PREFIX',
    'aiq_prefix': '_PM_AIQ_JOB_PREFIX',
    # storage + ops
    's3_client': 's3_client',
    'bucket': 'S3_BUCKET',
    'route_guard': '_chatbot_route_guard',   # (label) -> decorator
    'ask_logged': '_ask_logged',             # (surface) -> decorator
    'error_email': '_chatbot_error_email',   # (label, err, tb=None)
    'calm_payload': '_chatbot_calm_payload', # () -> dict
}

# Bindings that may legitimately be absent on an older host.
_OPTIONAL = {'max_threads', 'wallet_snapshot', 'access_gate'}


class HostNotBound(RuntimeError):
    pass


class _Host:
    def __init__(self):
        self._b = {}
        self.bound = False

    def bind(self, **caps):
        self._b.update(caps)
        self.bound = True

    def bind_from_app_module(self, mod):
        missing = []
        caps = {}
        for ours, theirs in _APP_BINDINGS.items():
            if hasattr(mod, theirs):
                caps[ours] = getattr(mod, theirs)
            elif ours not in _OPTIONAL:
                missing.append(theirs)
        if missing:
            raise HostNotBound('app.py is missing: ' + ', '.join(missing))
        self.bind(**caps)

    def has(self, name):
        return name in self._b

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        try:
            return self._b[name]
        except KeyError:
            raise HostNotBound(f'prometheus host capability not bound: {name}')


host = _Host()


def bind_from_app_module(mod):
    host.bind_from_app_module(mod)
