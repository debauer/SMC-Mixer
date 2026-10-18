"""Control the M-Vave / Sinco SMC-Mixer over USB-MIDI."""

from __future__ import annotations

from smc_mixer.connection import HotplugConfig
from smc_mixer.controls import LED_BUTTONS, Button, DeviceMode, LedState, MidiProtocol, strip_button
from smc_mixer.errors import (
    DisconnectedError,
    PortNotFoundError,
    ProtocolError,
    ResponseTimeoutError,
    SmcMixerError,
    UnsupportedFirmwareError,
)
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
from smc_mixer.mixer import SmcMixer
from smc_mixer.transport import MidiPort, PortScanner, list_ports

__all__ = [
    "LED_BUTTONS",
    "Button",
    "ButtonEvent",
    "ConnectionEvent",
    "DeviceMode",
    "DisconnectedError",
    "Event",
    "FaderEvent",
    "HotplugConfig",
    "KnobEvent",
    "Layer",
    "LedState",
    "MidiPort",
    "MidiProtocol",
    "ModeEvent",
    "PortNotFoundError",
    "PortScanner",
    "ProtocolError",
    "ResponseTimeoutError",
    "SmcMixer",
    "SmcMixerError",
    "UnknownEvent",
    "UnsupportedFirmwareError",
    "UserKnobsEvent",
    "list_ports",
    "strip_button",
]
