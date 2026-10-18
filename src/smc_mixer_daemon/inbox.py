"""Thread-safe ingress with fader coalescing and lifecycle invalidation."""

import threading
from collections import deque

from smc_mixer import ButtonEvent, ConnectionEvent, Event, FaderEvent, MidiProtocol, ModeEvent


class Inbox:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation = 0
        self._events: deque[Event] = deque()
        self._faders: dict[int, FaderEvent] = {}

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def put(self, event: Event) -> None:
        with self._lock:
            if isinstance(event, (ModeEvent, ConnectionEvent)):
                self._generation += 1
                self._events.clear()
                self._faders.clear()
            elif isinstance(event, FaderEvent) and event.protocol == MidiProtocol.MCU and not event.ambiguous:
                self._faders[event.fader] = event
            elif isinstance(event, ButtonEvent):
                self._events.extend(self._faders.values())
                self._faders.clear()
                self._events.append(event)

    def take(self) -> tuple[int, list[Event]]:
        with self._lock:
            self._events.extend(self._faders.values())
            self._faders.clear()
            events = list(self._events)
            self._events.clear()
            return self._generation, events
