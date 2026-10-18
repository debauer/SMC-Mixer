"""Access token that guards security-relevant changes (shell commands, MQTT broker) made over HTTP."""

import hmac
import os
import secrets
from pathlib import Path


class AuthError(PermissionError):
    """A request lacks the token required for the change."""


def ensure_token(path: Path) -> str:
    """Return the token stored at `path`, creating it (mode 0600) on first use."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(token + "\n")
    return token


def check(expected: str, given: str | None) -> None:
    if not given or not hmac.compare_digest(expected.encode(), given.encode()):
        raise AuthError("this change needs the access token")
