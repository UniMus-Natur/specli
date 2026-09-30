"""Secret storage for specli context passwords.

Prefer the OS keyring (Windows Credential Locker, macOS Keychain, Linux
Secret Service). Fall back to ``~/.specli/credentials.yaml`` (mode 0600)
when keyring is unavailable or fails.
"""

from __future__ import annotations

import sys

from specli import configstore

KEYRING_SERVICE = "specli"


def _keyring_username(context_name: str) -> str:
    return f"context:{context_name}"


def set_password(context_name: str, password: str, *, prefer_keyring: bool = True) -> str:
    """Store password; return ``\"keyring\"`` or ``\"file\"`` indicating backend used."""
    if prefer_keyring:
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, _keyring_username(context_name), password)
            # Remove file copy if present so keyring is the only source.
            configstore.delete_context_password_file(context_name)
            return "keyring"
        except Exception as exc:
            print(f"[specli] keyring unavailable ({exc}); storing in credentials file", file=sys.stderr)

    configstore.set_context_password_file(context_name, password)
    return "file"


def get_password(context_name: str) -> str | None:
    try:
        import keyring

        value = keyring.get_password(KEYRING_SERVICE, _keyring_username(context_name))
        if value:
            return value
    except Exception:
        pass

    creds = configstore.load_credentials()
    entry = creds.get(context_name) or {}
    password = (entry.get("password") or "").strip()
    return password or None


def delete_password(context_name: str) -> None:
    try:
        import keyring

        try:
            keyring.delete_password(KEYRING_SERVICE, _keyring_username(context_name))
        except keyring.errors.PasswordDeleteError:
            pass
    except Exception:
        pass
    configstore.delete_context_password_file(context_name)
