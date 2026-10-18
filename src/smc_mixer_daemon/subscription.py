"""Supervised pactl notifications; the reader never changes application state."""

import logging
import os
import subprocess
import threading
import time

log = logging.getLogger(__name__)


class Subscription:
    def __init__(self) -> None:
        self.changed = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._thread = threading.Thread(target=self._read, name="pactl-subscribe", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _read(self) -> None:
        delay = 1.0
        reported = 0.0
        while not self._stop.is_set():
            process = None
            last_line = ""
            try:
                with self._lock:
                    if self._stop.is_set():
                        return
                    process = subprocess.Popen(
                        ["pactl", "subscribe"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        env={**os.environ, "LC_ALL": "C"},
                    )
                    self._process = process
                self.changed.set()
                assert process.stdout is not None
                for line in process.stdout:
                    last_line = line.strip()
                    if line.startswith("Event"):
                        self.changed.set()
                        delay = 1
                process.wait(timeout=2)
                if not self._stop.is_set() and time.monotonic() - reported >= 30:
                    log.warning("pactl subscription ended (%s): %s", process.returncode, last_line)
                    reported = time.monotonic()
            except (OSError, subprocess.TimeoutExpired) as exc:
                if time.monotonic() - reported >= 30:
                    log.warning("pactl subscription failed: %s", exc)
                    reported = time.monotonic()
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    if process.stdout:
                        process.stdout.close()
                with self._lock:
                    self._process = None
                self.changed.set()
            if self._stop.wait(delay):
                return
            delay = min(10, delay * 2)

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()
        self._thread.join(timeout=3)
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.kill()
        self._thread.join()
