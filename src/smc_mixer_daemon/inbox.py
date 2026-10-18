"""Thread-safe ingress with fader coalescing and lifecycle invalidation."""

import threading
from collections import deque

from smc_mixer import ButtonEvent, ConnectionEvent, Event, FaderEvent, KnobEvent, Layer, MidiProtocol, ModeEvent


class Inbox:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._generation = 0
        self._events: deque[Event] = deque()
        self._faders: dict[int, FaderEvent] = {}
        self._knobs: dict[int, KnobEvent] = {}

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
                self._knobs.clear()
            elif isinstance(event, KnobEvent) and event.protocol == MidiProtocol.MCU and event.delta:
                # Sum steps between loop ticks; a fast turn becomes one volume write.
                total = event.delta + (self._knobs[event.knob].delta or 0 if event.knob in self._knobs else 0)
                self._knobs[event.knob] = KnobEvent(event.knob, Layer.DAW, delta=total)
            elif isinstance(event, FaderEvent) and event.protocol == MidiProtocol.MCU and not event.ambiguous:
                self._faders[event.fader] = event
            elif isinstance(event, ButtonEvent):
                self._flush()
                self._events.append(event)

    def _flush(self) -> None:
        self._events.extend(self._faders.values())
        self._events.extend(event for event in self._knobs.values() if event.delta)
        self._faders.clear()
        self._knobs.clear()

    def take(self) -> tuple[int, list[Event]]:
        with self._lock:
            self._flush()
            events = list(self._events)
            self._events.clear()
            return self._generation, events
