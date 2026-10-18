"""Bounded command execution without shell interpolation."""

import os
import subprocess
from collections.abc import Callable

Runner = Callable[[list[str]], str]


class BackendError(Exception):
    """An audio or media operation could not be completed."""


class CommandError(BackendError):
    def __init__(self, args: list[str], code: int, stderr: str) -> None:
        self.code = code
        self.stderr = stderr
        super().__init__(f"{args!r} exited {code}: {stderr.strip()}")


def run(args: list[str]) -> str:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackendError(f"{args!r}: {exc}") from exc
    if result.returncode:
        raise CommandError(args, result.returncode, result.stderr)
    return result.stdout.strip()
