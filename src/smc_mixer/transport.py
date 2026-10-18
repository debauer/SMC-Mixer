"""MIDI port access via python-rtmidi, behind a small interface that tests can fake."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from smc_mixer.errors import PortNotFoundError

MessageCallback = Callable[[bytes], None]


class MidiPort(Protocol):
    """Bidirectional MIDI port: send raw messages, receive them via a callback."""

    def send(self, message: bytes) -> None: ...

    def set_callback(self, callback: MessageCallback) -> None: ...

    def close(self) -> None: ...


def _find(names: list[str], part: str) -> int:
    matches = [i for i, name in enumerate(names) if part in name]
    if not matches:
        raise PortNotFoundError(f"no MIDI port containing {part!r}; available: {names}")
    if len(matches) > 1:
        raise PortNotFoundError(f"several MIDI ports contain {part!r}: {[names[i] for i in matches]}")
    return matches[0]


class RtMidiPort:
    """Opens the input and output of the port whose name contains `name_part`."""

    def __init__(self, name_part: str, client_name: str = "smc-mixer") -> None:
        import rtmidi  # imported here so the rest of the package works without the C extension

        self._in = rtmidi.MidiIn(name=client_name)
        self._out = rtmidi.MidiOut(name=client_name)
        try:
            self._in.open_port(_find(self._in.get_ports(), name_part))
            self._out.open_port(_find(self._out.get_ports(), name_part))
        except Exception:  # rtmidi raises its own error types; release both handles, then re-raise
            self.close()
            raise
        self._in.ignore_types(sysex=False, timing=True, active_sense=True)

    def send(self, message: bytes) -> None:
        self._out.send_message(list(message))

    def set_callback(self, callback: MessageCallback) -> None:
        def handler(event: tuple[list[int], float], _data: Any = None) -> None:
            callback(bytes(event[0]))

        self._in.set_callback(handler)

    def close(self) -> None:
        for port in (self._in, self._out):
            port.close_port()
            port.delete()


class PortScanner(Protocol):
    """Lists the currently available MIDI port names (for hotplug detection)."""

    def ports(self) -> list[str]: ...

    def close(self) -> None: ...


class RtMidiScanner:
    """One long-lived rtmidi client; get_ports() re-queries the system on every call."""

    def __init__(self, client_name: str = "smc-mixer-scan") -> None:
        import rtmidi

        self._in = rtmidi.MidiIn(name=client_name)

    def ports(self) -> list[str]:
        return list(self._in.get_ports())

    def close(self) -> None:
        self._in.delete()


def list_ports() -> list[str]:
    """Names of all MIDI input ports, for diagnostics."""
    import rtmidi

    midi_in = rtmidi.MidiIn()
    try:
        return list(midi_in.get_ports())
    finally:
        midi_in.delete()
