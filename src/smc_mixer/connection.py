"""Hotplug support: detect unplug / power-off and reopen the ports when the mixer returns."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from smc_mixer.transport import MidiPort, PortScanner

log = logging.getLogger(__name__)

PortOpener = Callable[[str], MidiPort]


@dataclass(frozen=True)
class HotplugConfig:
    scanner: PortScanner
    opener: PortOpener
    master_name: str
    private_name: str
    interval: float = 0.5  # seconds between port list checks


def ports_present(ports: list[str], *names: str) -> bool:
    return all(any(name in port for port in ports) for name in names)


def open_pair(opener: PortOpener, master_name: str, private_name: str) -> tuple[MidiPort, MidiPort]:
    master = opener(master_name)
    try:
        private = opener(private_name)
    except Exception:  # backend-specific error types: close the first port, then propagate
        master.close()
        raise
    return master, private


class Watcher:
    """Calls `check` every `interval` seconds on a daemon thread until stopped."""

    def __init__(self, check: Callable[[], object], interval: float) -> None:
        self._check = check
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="smc-mixer-hotplug", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._check()
            except Exception:  # keep watching; a failed check is retried on the next tick
                log.exception("hotplug check failed")

    @property
    def is_current_thread(self) -> bool:
        return threading.current_thread() is self._thread

    def stop(self, wait: bool = True) -> None:
        self._stop.set()
        if wait and not self.is_current_thread:
            self._thread.join()
