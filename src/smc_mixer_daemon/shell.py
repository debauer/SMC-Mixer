"""Run user-configured shell commands without blocking the control loop."""

import contextlib
import logging
import os
import signal
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

TIMEOUT = 30.0  # seconds before the whole process group is killed
MAX_RUNNING = 8
OUTPUT_TAIL = 300


class ShellRunner:
    """Each command runs as `sh -c <command>` in its own process group on a worker thread."""

    def __init__(self, timeout: float = TIMEOUT) -> None:
        self.timeout = timeout
        self._lock = threading.Lock()
        self._running: dict[int, subprocess.Popen[str]] = {}
        self._closed = False

    def start(self, label: str, command: str) -> bool:
        with self._lock:
            if self._closed or len(self._running) >= MAX_RUNNING:
                log.warning(
                    "shell %s: not started (%s)", label, "shutting down" if self._closed else "too many running"
                )
                return False
        threading.Thread(target=self._run, args=(label, command), name=f"shell-{label}", daemon=True).start()
        return True

    def _run(self, label: str, command: str) -> None:
        try:
            process = subprocess.Popen(
                ["sh", "-c", command],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                cwd=Path.home(),
                start_new_session=True,
            )
        except OSError as exc:
            log.warning("shell %s: could not start: %s", label, exc)
            return
        with self._lock:
            self._running[process.pid] = process
        try:
            output, _ = process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self._kill(process)
            output, _ = process.communicate()
            log.warning("shell %s: killed after %.0f s", label, self.timeout)
            return
        finally:
            with self._lock:
                self._running.pop(process.pid, None)
        if process.returncode:
            log.warning("shell %s: exit %s: %s", label, process.returncode, output.strip()[-OUTPUT_TAIL:])
        else:
            log.info("shell %s: ok", label)

    @staticmethod
    def _kill(process: subprocess.Popen[str]) -> None:
        with contextlib.suppress(ProcessLookupError):  # already exited
            os.killpg(process.pid, signal.SIGKILL)

    def close(self) -> None:
        """Stop accepting commands and kill those still running."""
        with self._lock:
            self._closed = True
            running = list(self._running.values())
        for process in running:
            self._kill(process)
