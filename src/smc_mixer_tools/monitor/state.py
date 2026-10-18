"""Display state of the observing monitor, independent of terminal rendering."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from smc_mixer.controls import Button, DeviceMode, MidiProtocol
from smc_mixer.events import (
    ButtonEvent,
    ConnectionEvent,
    Event,
    FaderEvent,
    KnobEvent,
    Layer,
    ModeEvent,
    UnknownEvent,
    UserKnobsEvent,
)


@dataclass
class MonitorState:
    connected: bool = True
    demo: bool = False
    protocol: MidiProtocol | None = None
    mode: DeviceMode | None = None
    faders: list[int | None] = field(default_factory=lambda: [None] * 8)
    knobs: list[KnobEvent | None] = field(default_factory=lambda: [None] * 8)
    pressed: set[Button] = field(default_factory=set)
    history: deque[str] = field(default_factory=lambda: deque(maxlen=4))

    def record(self, message: str) -> None:
        self.history.appendleft(f"{datetime.now():%H:%M:%S}  {message}")

    def set_mode(self, mode: DeviceMode | None) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        self.pressed.clear()
        self.knobs = [None] * 8
        self.protocol = None
        if mode is not None:
            self.record(f"MODE: {mode.value}")

    def apply(self, event: Event) -> None:
        if isinstance(event, ModeEvent):
            self.set_mode(event.mode)
            return
        if isinstance(event, UserKnobsEvent):
            if self.mode == DeviceMode.USER:
                self.knobs = [KnobEvent(knob, Layer.CC, value=value) for knob, value in enumerate(event.values, 1)]
                self.record("USER knob values loaded")
            return
        if isinstance(event, ConnectionEvent):
            self.connected = event.connected
            self.mode = None
            self.pressed.clear()
            self.knobs = [None] * 8
            self.protocol = None
            self.record("USB ONLINE" if event.connected else "USB OFFLINE - last known fader values")
            return
        if isinstance(event, UnknownEvent):
            self.record(f"UNKNOWN  {event.message.hex(' ')}")
            return
        self.protocol = event.protocol
        if isinstance(event, ButtonEvent):
            if event.pressed:
                self.pressed.add(event.button)
            else:
                self.pressed.discard(event.button)
            description = f"{event.button.name}  {'DOWN' if event.pressed else 'UP'}"
        elif isinstance(event, FaderEvent):
            self.faders[event.fader - 1] = event.value
            description = f"CH {event.fader} / FADER / {event.value:03d}"
        else:
            self.knobs[event.knob - 1] = event
            value = f"{event.delta:+d}" if event.delta is not None else f"{event.value:03d}"
            description = f"CH {event.knob} / KNOB / {value}"
        suffix = " ? (ambiguous CC)" if event.ambiguous else ""
        self.record(f"{event.protocol.value.upper():5s}  {description}{suffix}")

    def sync(self, mode: DeviceMode | None, faders: list[int | None], user_knobs: list[int | None]) -> None:
        """Display the library's latest cache even when MIDI and snapshot events overlap."""
        self.set_mode(mode)
        self.faders = faders
        if mode == DeviceMode.USER:
            self.knobs = [
                KnobEvent(knob, Layer.CC, value=value) if value is not None else None
                for knob, value in enumerate(user_knobs, 1)
            ]
