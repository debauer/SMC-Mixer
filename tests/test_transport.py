"""RtMidiPort against a stand-in for the rtmidi module (no ALSA needed)."""

from __future__ import annotations

import sys
import types
from typing import Any, ClassVar

import pytest

from smc_mixer import PortNotFoundError
from smc_mixer.transport import RtMidiPort, list_ports

PORTS = [
    "Midi Through:Midi Through Port-0 14:0",
    "SINCO:SINCO SMC-Mixer-Private 28:0",
    "SINCO:SINCO SMC-Mixer-Master 28:1",
]


class FakeRtMidi:
    instances: ClassVar[list[FakeRtMidi]] = []

    def __init__(self, name: str = "") -> None:
        self.opened: int | None = None
        self.sent: list[list[int]] = []
        self.callback: Any = None
        self.ignored: dict[str, bool] = {}
        self.deleted = False
        FakeRtMidi.instances.append(self)

    def get_ports(self) -> list[str]:
        return PORTS

    def open_port(self, index: int) -> None:
        self.opened = index

    def close_port(self) -> None:
        self.opened = None

    def delete(self) -> None:
        self.deleted = True

    def ignore_types(self, **kwargs: bool) -> None:
        self.ignored = kwargs

    def set_callback(self, callback: Any) -> None:
        self.callback = callback

    def send_message(self, message: list[int]) -> None:
        self.sent.append(message)


@pytest.fixture(autouse=True)
def fake_rtmidi(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("rtmidi")
    module.MidiIn = FakeRtMidi  # type: ignore[attr-defined]
    module.MidiOut = FakeRtMidi  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "rtmidi", module)
    FakeRtMidi.instances = []


def test_open_send_receive_close() -> None:
    port = RtMidiPort("SMC-Mixer-Master")
    midi_in, midi_out = FakeRtMidi.instances
    assert midi_in.opened == 2 and midi_out.opened == 2
    assert midi_in.ignored == {"sysex": False, "timing": True, "active_sense": True}
    received: list[bytes] = []
    port.set_callback(received.append)
    midi_in.callback(([0x90, 16, 127], 0.001), None)
    port.send(b"\x90\x00\x7f")
    assert received == [b"\x90\x10\x7f"]
    assert midi_out.sent == [[0x90, 0x00, 0x7F]]
    port.close()
    assert midi_in.deleted and midi_out.deleted


def test_missing_port_releases_handles() -> None:
    with pytest.raises(PortNotFoundError):
        RtMidiPort("SMC-Mixer-Nope")
    assert all(p.deleted for p in FakeRtMidi.instances)


def test_ambiguous_port_name() -> None:
    with pytest.raises(PortNotFoundError, match="several"):
        RtMidiPort("SMC-Mixer")


def test_list_ports() -> None:
    assert list_ports() == PORTS
