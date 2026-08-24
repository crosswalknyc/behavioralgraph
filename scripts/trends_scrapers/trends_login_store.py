"""
Secure login-credential store for Trends IQ auto-login.

Credentials for the streaming / retail sites the auto-login engine
signs into live in the macOS Keychain, one item per domain. They are
NEVER written to the repo, an env file, S3, or Hetzner. The Keychain
encrypts them at rest with the operator's login password and the OS
gates read access per-process.

Storage layout
---------------
One generic-password Keychain item per domain:
    service = "CrosswalkTrendsLogin"
    account = "<domain>"            e.g. "netflix.com"
    secret  = {"username": "...", "password": "..."}   (JSON blob)

The username+password are packed into the single secret blob so one
Keychain unlock returns both. The blob is fed to `security` over stdin
(twice, to satisfy its confirm prompt) so the password never appears in
the process argument list (`ps`), shell history, or any log.

CLI
---
    # Store credentials for a domain (prompts; password never echoes)
    python3 -m scripts.trends_scrapers.trends_login_store set netflix.com

    # Confirm what's stored (prints the username only, never the password)
    python3 -m scripts.trends_scrapers.trends_login_store get netflix.com

    # List every domain that has stored credentials
    python3 -m scripts.trends_scrapers.trends_login_store list

    # Remove a domain's credentials
    python3 -m scripts.trends_scrapers.trends_login_store delete netflix.com

macOS only. On any other OS the store degrades to "no credentials",
which makes the auto-login engine skip password login and fall back to
the persistent-profile session (and, failing that, the operator email).
"""

from __future__ import annotations

import getpass
import json
import subprocess
import sys

KEYCHAIN_SERVICE = "CrosswalkTrendsLogin"
_SECURITY = "/usr/bin/security"

# The domains auto-login knows how to sign into. Used by `list` to show
# which have credentials and by the engine to decide what to attempt.
# Kept here (not imported from donate_cookies) so this module stays
# dependency-free and importable on its own.
KNOWN_LOGIN_DOMAINS = [
    "netflix.com", "disneyplus.com", "hulu.com", "hbomax.com",
    "amazon.com", "peacocktv.com", "britbox.com", "mgmplus.com",
    "starz.com", "xbox.com", "audible.com", "open.spotify.com",
    "music.amazon.com",
    "target.com", "walmart.com", "etsy.com", "sephora.com",
    "lululemon.com", "bestbuy.com", "nike.com", "ulta.com",
    "amctheatres.com", "regmovies.com",
]


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _norm(domain: str) -> str:
    return (domain or "").strip().lower().lstrip(".")


def set_credentials(domain: str, username: str, password: str) -> bool:
    """Store (username, password) for `domain` in the Keychain,
    overwriting any existing item. Returns True on success. The secret
    is passed over stdin so it never lands in argv."""
    if not _is_macos():
        raise RuntimeError("Keychain credential storage requires macOS.")
    domain = _norm(domain)
    blob = json.dumps({"username": username, "password": password},
                      ensure_ascii=False)
    # `security add-generic-password -w` with no inline value reads the
    # secret from the terminal, prompting twice ("password data" +
    # "retype"). Feeding the blob twice over stdin satisfies both.
    proc = subprocess.run(
        [_SECURITY, "add-generic-password", "-U",
         "-s", KEYCHAIN_SERVICE, "-a", domain,
         "-D", "Crosswalk Trends IQ login", "-w"],
        input=f"{blob}\n{blob}\n", text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        print(f"  keychain write failed for {domain}: "
              f"{proc.stderr.strip()}", file=sys.stderr)
        return False
    return True


def get_credentials(domain: str) -> tuple[str, str] | None:
    """Return (username, password) for `domain`, or None if not stored /
    not macOS / unreadable."""
    if not _is_macos():
        return None
    domain = _norm(domain)
    proc = subprocess.run(
        [_SECURITY, "find-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", domain, "-w"],
        text=True, capture_output=True,
    )
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        u = data.get("username")
        p = data.get("password")
        if u and p:
            return u, p
    except Exception:
        pass
    return None


def has_credentials(domain: str) -> bool:
    return get_credentials(domain) is not None


def delete_credentials(domain: str) -> bool:
    if not _is_macos():
        return False
    domain = _norm(domain)
    proc = subprocess.run(
        [_SECURITY, "delete-generic-password",
         "-s", KEYCHAIN_SERVICE, "-a", domain],
        text=True, capture_output=True,
    )
    return proc.returncode == 0


def list_stored_domains() -> list[str]:
    """Return the subset of KNOWN_LOGIN_DOMAINS that currently have
    credentials in the Keychain."""
    return [d for d in KNOWN_LOGIN_DOMAINS if has_credentials(d)]


# ────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────
def _cmd_set(domain: str) -> int:
    domain = _norm(domain)
    if not domain or "." not in domain:
        print(f"invalid domain: {domain!r}", file=sys.stderr)
        return 2
    print(f"Storing login for {domain} in the macOS Keychain.")
    print("Nothing is written to the repo, S3, or the server.\n")
    username = input(f"  Username / email for {domain}: ").strip()
    if not username:
        print("aborted: empty username", file=sys.stderr)
        return 2
    pw1 = getpass.getpass(f"  Password for {domain} (hidden): ")
    pw2 = getpass.getpass("  Re-enter password: ")
    if pw1 != pw2:
        print("aborted: passwords did not match", file=sys.stderr)
        return 2
    if not pw1:
        print("aborted: empty password", file=sys.stderr)
        return 2
    ok = set_credentials(domain, username, pw1)
    if ok:
        print(f"\nStored credentials for {domain}.")
        print(f"Next: complete a one-time assisted sign-in so 2FA is "
              f"cleared and the session persists:")
        print(f"    python3 -m scripts.trends_scrapers.trends_auto_login "
              f"--setup {domain}")
        return 0
    return 1


def _cmd_get(domain: str) -> int:
    creds = get_credentials(domain)
    if not creds:
        print(f"no credentials stored for {_norm(domain)}")
        return 1
    username, _ = creds
    # Never print the password.
    print(f"{_norm(domain)}: username={username}  password=********  (stored)")
    return 0


def _cmd_list() -> int:
    stored = list_stored_domains()
    if not stored:
        print("no credentials stored yet.")
        print("add one with: python3 -m scripts.trends_scrapers."
              "trends_login_store set <domain>")
        return 0
    print(f"{len(stored)} domain(s) with stored credentials:")
    for d in stored:
        print(f"  {d}")
    return 0


def _cmd_delete(domain: str) -> int:
    ok = delete_credentials(domain)
    print(f"{'deleted' if ok else 'nothing to delete for'} {_norm(domain)}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not _is_macos():
        print("trends_login_store requires macOS (Keychain).", file=sys.stderr)
        return 3
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    rest = argv[1:]
    if cmd == "set" and rest:
        return _cmd_set(rest[0])
    if cmd == "get" and rest:
        return _cmd_get(rest[0])
    if cmd == "list":
        return _cmd_list()
    if cmd == "delete" and rest:
        return _cmd_delete(rest[0])
    print("usage: trends_login_store {set|get|delete} <domain> | list",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
