"""Decode MIDI messages from the "Master" port into typed events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from smc_mixer.constants import (
    CC_BUTTON_JUMP,
    CHANNELS,
    KNOB_CC_FIRST,
    USER_FADER_CC_FIRST,
    USER_KNOB_CC_FIRST,
)
from smc_mixer.controls import USER_CC_BUTTON, Button, DeviceMode, MidiProtocol


class Layer(Enum):
    """Legacy event discriminator; prefer event.protocol for new code."""

    DAW = "daw"
    CC = "cc"
    SHIFT = "shift"  # Deprecated input value, never emitted by Decoder.


class ControlEvent:
    layer: Layer
    ambiguous: bool

    @property
    def protocol(self) -> MidiProtocol:
        return MidiProtocol.MCU if self.layer == Layer.DAW else MidiProtocol.CC


@dataclass(frozen=True)
class ButtonEvent(ControlEvent):
    button: Button
    pressed: bool
    layer: Layer
    ambiguous: bool = False


@dataclass(frozen=True)
class KnobEvent(ControlEvent):
    knob: int  # 1-8
    layer: Layer
    delta: int | None = None  # DAW layer: relative steps, positive = clockwise
    value: int | None = None  # CC protocol: absolute 0-127
    ambiguous: bool = False


@dataclass(frozen=True)
class FaderEvent(ControlEvent):
    fader: int  # 1-8
    value: int  # 0-127
    layer: Layer
    ambiguous: bool = False


@dataclass(frozen=True)
class UnknownEvent:
    message: bytes


@dataclass(frozen=True)
class ConnectionEvent:
    """Mixer disappeared (unplugged / switched off) or was reconnected."""

    connected: bool


@dataclass(frozen=True)
class ModeEvent:
    """Read device mode changed; None means unknown/disconnected, not an inferred mode."""

    mode: DeviceMode | None


@dataclass(frozen=True)
class UserKnobsEvent:
    """Read snapshot of all eight USER knobs, distinct from received MIDI movement."""

    values: tuple[int, ...]


Event = ButtonEvent | KnobEvent | FaderEvent | UnknownEvent | ConnectionEvent | ModeEvent | UserKnobsEvent


class Decoder:
    """Decode the default MCU/CC mappings, marking heuristic CC-endpoint interpretations."""

    def __init__(self) -> None:
        self._last_cc: dict[int, int] = {}

    def reset(self) -> None:
        self._last_cc.clear()

    def decode(self, msg: bytes) -> Event:
        if len(msg) != 3:
            return UnknownEvent(msg)
        status, data1, data2 = msg
        kind, channel = status & 0xF0, status & 0x0F
        if kind == 0xE0 and channel < CHANNELS:
            return FaderEvent(channel + 1, data2, Layer.DAW)  # 7-bit resolution, LSB is always 0
        if channel != 0:
            return UnknownEvent(msg)
        if kind in (0x80, 0x90) and data1 in Button._value2member_map_:
            return ButtonEvent(Button(data1), kind == 0x90 and data2 > 0, Layer.DAW)
        if kind == 0xB0:
            return self._decode_cc(msg, data1, data2)
        return UnknownEvent(msg)

    def _decode_cc(self, msg: bytes, cc: int, value: int) -> Event:
        last = self._last_cc.get(cc)
        self._last_cc[cc] = value
        knob = cc - KNOB_CC_FIRST + 1
        if 1 <= knob <= CHANNELS and (1 <= value <= 15 or 65 <= value <= 79):
            return KnobEvent(knob, Layer.DAW, delta=value if value < 64 else 64 - value)
        button = USER_CC_BUTTON.get(cc)
        user_knob = cc - USER_KNOB_CC_FIRST + 1
        user_fader = cc - USER_FADER_CC_FIRST + 1
        continuous = 1 <= user_knob <= CHANNELS or 1 <= user_fader <= CHANNELS
        ambiguous = button is not None and continuous and value in (0, 127)
        if button is not None and value in (0, 127):
            jump = last is None or abs(value - last) > CC_BUTTON_JUMP
            if not continuous or jump:
                return ButtonEvent(button, value == 127, Layer.CC, ambiguous=ambiguous)
        if 1 <= user_knob <= CHANNELS:
            return KnobEvent(user_knob, Layer.CC, value=value, ambiguous=ambiguous)
        if 1 <= user_fader <= CHANNELS:
            return FaderEvent(user_fader, value, Layer.CC, ambiguous=ambiguous)
        return UnknownEvent(msg)
