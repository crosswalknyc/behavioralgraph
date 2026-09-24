"""Shared password check for the CAA annual-value share page."""
import hashlib
import hmac

_SALT = b"crosswalk-caa-gate-v1|"
_PASS_HASH = hashlib.sha256(_SALT + b"greenlight").hexdigest()
COOKIE_NAME = "caa_gate"
COOKIE_MAX_AGE = 14 * 24 * 3600


def password_ok(pw: str) -> bool:
    got = hashlib.sha256(_SALT + (pw or "").encode()).hexdigest()
    return hmac.compare_digest(got, _PASS_HASH)


def cookie_token(secret) -> str:
    if isinstance(secret, str):
        secret = secret.encode()
    if not secret:
        secret = b"crosswalk-caa-gate"
    return hmac.new(secret, _PASS_HASH.encode(), hashlib.sha256).hexdigest()


def cookie_ok(token: str, secret) -> bool:
    if not token:
        return False
    return hmac.compare_digest(token, cookie_token(secret))
