"""Device state, firmware-checked snapshots and MIDI updates independent of any UI."""

import threading
from collections.abc import Callable

from smc_mixer.configuration import Configuration
from smc_mixer.constants import (
    CHANNELS,
    FADER_TABLE_ADDR,
    FADER_TABLE_ENTRY,
    FADER_TABLE_TYPE,
    MODE_ADDRESS,
    MODE_MASK,
    STATE_MEMORY_TYPE,
    USER_KNOB_ADDRESS,
    USER_KNOB_ENTRY,
)
from smc_mixer.controls import DeviceMode, MidiProtocol
from smc_mixer.errors import ProtocolError, SmcMixerError
from smc_mixer.events import ControlEvent, Decoder, Event, FaderEvent, KnobEvent, ModeEvent, UserKnobsEvent


class DeviceState:
    def __init__(self, configuration: Configuration, emit: Callable[[Event], None]) -> None:
        self._configuration = configuration
        self._emit = emit
        self._lock = threading.RLock()
        self._decoder = Decoder()
        self._mode: DeviceMode | None = None
        self._user_knobs: list[int | None] = [None] * CHANNELS
        self._faders: list[int | None] = [None] * CHANNELS
        self._knob_revisions = [0] * CHANNELS
        self._fader_revisions = [0] * CHANNELS
        self._generation = 0
        self._ambiguous_revision = 0
        self._needs_user_snapshot = True
        self._needs_fader_snapshot = False

    @property
    def mode(self) -> DeviceMode | None:
        with self._lock:
            return self._mode

    @property
    def user_knobs(self) -> list[int | None]:
        with self._lock:
            return list(self._user_knobs)

    @property
    def faders(self) -> list[int | None]:
        with self._lock:
            return list(self._faders)

    def set_mode(self, mode: DeviceMode | None) -> None:
        with self._lock:
            if self._mode == mode:
                return
            self._mode = mode
            self._generation += 1
            self._user_knobs = [None] * CHANNELS
            self._needs_user_snapshot = True
            self._decoder.reset()
        self._emit(ModeEvent(mode))

    def invalidate(self) -> None:
        self.set_mode(None)
        with self._lock:
            self._generation += 1
            self._user_knobs = [None] * CHANNELS
            self._decoder.reset()

    def clear_faders(self) -> None:
        with self._lock:
            self._faders = [None] * CHANNELS

    def receive(self, message: bytes) -> None:
        with self._lock:
            event = self._decoder.decode(message)
            if isinstance(event, ControlEvent) and event.ambiguous:
                self._ambiguous_revision += 1
                self._needs_user_snapshot |= 30 <= message[1] <= 37
                self._needs_fader_snapshot |= 40 <= message[1] <= 47
            if isinstance(event, FaderEvent) and not event.ambiguous:
                self._faders[event.fader - 1] = event.value
                self._fader_revisions[event.fader - 1] += 1
            elif (
                isinstance(event, KnobEvent)
                and event.protocol == MidiProtocol.CC
                and self._mode == DeviceMode.USER
                and not event.ambiguous
            ):
                self._user_knobs[event.knob - 1] = event.value
                self._knob_revisions[event.knob - 1] += 1
        self._emit(event)

    def read_mode(self, timeout: float) -> DeviceMode:
        try:
            with self._configuration.lock:
                self._configuration.require_supported(timeout)
                data = self._configuration.read_memory(STATE_MEMORY_TYPE, MODE_ADDRESS, 1, timeout)
                if len(data) != 1:
                    raise ProtocolError(f"mode response: {len(data)} instead of 1 byte")
                mode = DeviceMode.USER if data[0] & MODE_MASK else DeviceMode.DAW
                self.set_mode(mode)
                return mode
        except SmcMixerError:
            self.invalidate()
            raise

    def read_user_knobs(self, timeout: float) -> list[int]:
        with self._configuration.lock:
            if self.read_mode(timeout) != DeviceMode.USER:
                raise ProtocolError("USER knob values require USER mode")
            with self._lock:
                generation = self._generation
                revisions = list(self._knob_revisions)
                ambiguous_revision = self._ambiguous_revision
                self._needs_user_snapshot = True
            length = CHANNELS * USER_KNOB_ENTRY
            data = self._configuration.read_memory(STATE_MEMORY_TYPE, USER_KNOB_ADDRESS, length, timeout)
            if len(data) != length:
                raise ProtocolError(f"USER knob table: {len(data)} instead of {length} bytes")
            values = [
                int.from_bytes(data[i : i + USER_KNOB_ENTRY], "little") for i in range(0, length, USER_KNOB_ENTRY)
            ]
            if any(value > 127 for value in values):
                raise ProtocolError("USER knob table contains values outside 0..127")
            if self.read_mode(timeout) != DeviceMode.USER:
                raise ProtocolError("mode changed while reading USER knobs")
            with self._lock:
                if generation != self._generation:
                    raise ProtocolError("connection or mode changed while reading USER knobs")
                for index, value in enumerate(values):
                    current = self._user_knobs[index]
                    if revisions[index] == self._knob_revisions[index] or current is None:
                        self._user_knobs[index] = value
                    else:
                        values[index] = current
                self._needs_user_snapshot = ambiguous_revision != self._ambiguous_revision
            self._emit(UserKnobsEvent(tuple(values)))
        return values

    def read_faders(self, timeout: float) -> list[int]:
        with self._configuration.lock:
            self._configuration.require_supported(timeout)
            with self._lock:
                generation = self._generation
                revisions = list(self._fader_revisions)
                ambiguous_revision = self._ambiguous_revision
                self._needs_fader_snapshot = True
            length = FADER_TABLE_ENTRY * CHANNELS
            data = self._configuration.read_memory(FADER_TABLE_TYPE, FADER_TABLE_ADDR, length, timeout)
            if len(data) < length:
                raise ProtocolError(f"fader table too short: {len(data)} bytes")
            values = [data[i * FADER_TABLE_ENTRY + 1] & 0x7F for i in range(CHANNELS)]
            with self._lock:
                if generation != self._generation:
                    raise ProtocolError("connection or mode changed while reading faders")
                for index, value in enumerate(values):
                    current = self._faders[index]
                    if revisions[index] == self._fader_revisions[index] or current is None:
                        self._faders[index] = value
                    else:
                        values[index] = current
                self._needs_fader_snapshot = ambiguous_revision != self._ambiguous_revision
            return values

    def refresh(self, timeout: float) -> None:
        with self._configuration.lock:
            mode = self.read_mode(timeout)
            with self._lock:
                knobs_needed = self._needs_user_snapshot
                faders_needed = self._needs_fader_snapshot or any(value is None for value in self._faders)
            if mode == DeviceMode.USER and knobs_needed:
                self.read_user_knobs(timeout)
            if faders_needed:
                self.read_faders(timeout)
