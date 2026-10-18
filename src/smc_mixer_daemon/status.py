"""Thread-safe status published by the control loop for the web interface."""

import logging
import threading
import time
from collections import deque
from typing import Any

from smc_mixer import SmcMixer
from smc_mixer_daemon.config import Config
from smc_mixer_daemon.controller import Controller


class Board:
    """The control loop publishes plain data; web threads only read copies."""

    def __init__(self, config: Config | None = None) -> None:
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {"mixer": {"connected": False, "mode": None, "control": "waiting"}}
        self._log: deque[dict[str, str]] = deque(maxlen=60)
        self._config = config
        self.reload = threading.Event()

    @property
    def config(self) -> Config | None:
        with self._lock:
            return self._config

    @config.setter
    def config(self, value: Config) -> None:
        with self._lock:
            self._config = value

    def publish(self, status: dict[str, Any]) -> None:
        with self._lock:
            self._status = status

    def note(self, level: str, message: str) -> None:
        with self._lock:
            self._log.append({"t": time.strftime("%H:%M:%S"), "level": level, "msg": message})

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {**self._status, "log": list(self._log)[::-1]}


class BoardHandler(logging.Handler):
    """Mirror daemon log records into the status log."""

    def __init__(self, board: Board) -> None:
        super().__init__(logging.INFO)
        self.board = board

    def emit(self, record: logging.LogRecord) -> None:
        self.board.note(record.levelname.lower(), record.getMessage())


def describe(mixer: SmcMixer | None, controller: Controller | None, config: Config) -> dict[str, Any]:
    """Snapshot of mixer and strip state; reads attributes only, never talks to the hardware."""
    connected = bool(mixer and mixer.connected)
    mode = mixer.mode.value if mixer and mixer.mode else None
    control = "waiting"
    if connected:
        control = "active" if controller and controller.ready else "suspended"
    strips = []
    for strip in sorted(config.strips, key=lambda row: row.channel):
        row: dict[str, Any] = {"channel": strip.channel, "kind": strip.kind, "name": strip.name, "available": False}
        snapshot = controller.snapshot if controller and controller.ready else None
        device = snapshot.devices.get((strip.kind, strip.name)) if snapshot else None
        if snapshot and device:
            state = controller.states.get(strip.channel) if controller else None
            row |= {
                "available": True,
                "description": device.description,
                "volume": round(device.volume),
                "muted": device.muted,
                "default": snapshot.selected(device),
                "owned": state[1].owned if state else None,
            }
        strips.append(row)
    return {
        "mixer": {"connected": connected, "mode": mode, "control": control},
        "faders": list(mixer.faders) if connected and mixer else [None] * 8,
        "strips": strips,
        "leds": {button.name: on for button, on in controller.leds.items()} if controller else {},
        "media": {"player": config.player},
        "soft_takeover": config.soft_takeover,
    }
