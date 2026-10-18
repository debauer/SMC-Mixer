"""High-level access to the SMC-Mixer: events, LEDs, fader positions, configuration reads."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable, Iterator
from types import TracebackType

from smc_mixer.configuration import Configuration
from smc_mixer.connection import HotplugConfig, Watcher, open_pair, ports_present
from smc_mixer.constants import DEFAULT_TIMEOUT, MASTER_PORT_NAME, PRIVATE_PORT_NAME, RESTORE_ATTEMPTS
from smc_mixer.controls import Button, DeviceMode, LedState
from smc_mixer.device_state import DeviceState
from smc_mixer.errors import DisconnectedError, SmcMixerError
from smc_mixer.events import ConnectionEvent, Event
from smc_mixer.outputs import Outputs
from smc_mixer.transport import MidiPort, RtMidiPort, RtMidiScanner

log = logging.getLogger(__name__)
Listener = Callable[[Event], None]


class SmcMixer:
    """Mixer connected over USB. Use SmcMixer.open() or pass ports (e.g. fakes in tests).

    With hotplug (default for open()), unplugging or switching off the mixer emits
    ConnectionEvent(False); output calls then raise DisconnectedError. When it returns,
    the ports are reopened, faders/LEDs restored and state polling refreshes mode/USER knobs.
    Listeners run on MIDI, watcher or explicit-read caller threads.
    """

    def __init__(
        self,
        master: MidiPort,
        private: MidiPort,
        hotplug: HotplugConfig | None = None,
        *,
        state_poll_interval: float | None = None,
    ) -> None:
        if state_poll_interval is not None and not 0 < state_poll_interval < float("inf"):
            raise ValueError("state_poll_interval must be positive and finite or None")
        self._lock = threading.Lock()  # protects state shared with callback threads
        self._port_lock = threading.RLock()  # protects the port handles during reconnects
        self._lifecycle_lock = threading.RLock()
        self._listeners: list[Listener] = []
        self._events: queue.Queue[Event] = queue.Queue()
        self._configuration = Configuration(self._send_private)
        self._state = DeviceState(self._configuration, self._emit)
        self._outputs = Outputs(self._send, lambda: self.faders)
        self._last_state_error: str | None = None
        self._state_poll_interval = state_poll_interval
        self._closed = False
        self._state_watcher: Watcher | None = None
        self._hotplug = hotplug
        self._master: MidiPort | None = None
        self._private: MidiPort | None = None
        self._attach(master, private)
        self._watcher = Watcher(self.check_connection, hotplug.interval) if hotplug else None
        try:
            if state_poll_interval is not None:
                self._poll_state()
            self._state_watcher = (
                Watcher(self._poll_state, state_poll_interval) if state_poll_interval is not None else None
            )
        except BaseException:
            self.close()
            raise

    @classmethod
    def open(
        cls,
        master_name: str = MASTER_PORT_NAME,
        private_name: str = PRIVATE_PORT_NAME,
        auto_reconnect: bool = True,
        poll_interval: float = 0.5,
        *,
        state_poll_interval: float | None = 0.5,
    ) -> SmcMixer:
        """Open ports, initialize state and track it every 0.5 s by default.

        Set state_poll_interval=None for explicit reads only; hotplug polling is independent.
        """
        if state_poll_interval is not None and not 0 < state_poll_interval < float("inf"):
            raise ValueError("state_poll_interval must be positive and finite or None")
        master, private = open_pair(RtMidiPort, master_name, private_name)
        if not auto_reconnect:
            return cls(master, private, state_poll_interval=state_poll_interval)
        config = HotplugConfig(RtMidiScanner(), RtMidiPort, master_name, private_name, poll_interval)
        return cls(master, private, config, state_poll_interval=state_poll_interval)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        watchers = [watcher for watcher in (self._state_watcher, self._watcher) if watcher is not None]
        wait = not any(watcher.is_current_thread for watcher in watchers)
        for watcher in watchers:
            watcher.stop(wait=wait)
        if self._hotplug:
            self._hotplug.scanner.close()
        self._detach()

    def __enter__(self) -> SmcMixer:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    # --- connection --------------------------------------------------------------

    @property
    def connected(self) -> bool:
        with self._port_lock:
            return self._master is not None

    def _attach(self, master: MidiPort, private: MidiPort) -> None:
        with self._port_lock:
            self._master = master
            self._private = private
            master.set_callback(lambda message: self._on_master(message) if self._master is master else None)
            private.set_callback(lambda message: self._on_private(message) if self._private is private else None)

    def _detach(self) -> None:
        with self._configuration.lock:
            with self._port_lock:
                ports, self._master, self._private = (self._master, self._private), None, None
            self._configuration.reset()
            self._state.invalidate()
        for port in ports:
            if port is None:
                continue
            try:
                port.close()
            except Exception:  # the device may already be gone; closing is best effort here
                log.warning("closing a MIDI port failed", exc_info=True)

    def check_connection(self) -> bool:
        """Detect unplug / return of the mixer once (called periodically by the watcher)."""
        with self._lifecycle_lock:
            return self._check_connection()

    def _check_connection(self) -> bool:
        if self._closed:
            return False
        if self._hotplug is None:
            return self.connected
        cfg = self._hotplug
        present = ports_present(cfg.scanner.ports(), cfg.master_name, cfg.private_name)
        if self.connected and not present:
            self._detach()
            log.info("mixer disconnected")
            self._emit(ConnectionEvent(False))
        elif not self.connected and present:
            try:
                master, private = open_pair(cfg.opener, cfg.master_name, cfg.private_name)
            except Exception:  # port may not be ready yet; retried on the next check
                log.warning("reopening the mixer ports failed, retrying", exc_info=True)
                return False
            self._attach(master, private)
            self._restore()
            log.info("mixer reconnected")
            self._emit(ConnectionEvent(True))
            if self._state_poll_interval is not None:
                self._poll_state()
        return self.connected

    def _restore(self) -> None:
        for attempt in range(1, RESTORE_ATTEMPTS + 1):
            try:
                self.read_faders()
                break
            except SmcMixerError as exc:
                log.warning(
                    "reading fader positions after reconnect failed (%d/%d): %s", attempt, RESTORE_ATTEMPTS, exc
                )
                self._state.clear_faders()
        self._outputs.restore()

    def _send(self, message: bytes) -> None:
        with self._port_lock:
            if self._master is None:
                raise DisconnectedError("mixer is not connected")
            self._master.send(message)

    # --- input -----------------------------------------------------------------

    def _on_master(self, message: bytes) -> None:
        self._state.receive(message)

    def _emit(self, event: Event) -> None:
        with self._lock:
            listeners = list(self._listeners)
        self._events.put(event)
        for listener in listeners:
            listener(event)

    def _on_private(self, message: bytes) -> None:
        self._configuration.receive(message)

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.remove(listener)

    def events(self, timeout: float | None = None) -> Iterator[Event]:
        """Yield events as they arrive; stops after `timeout` seconds without an event."""
        while True:
            try:
                yield self._events.get(timeout=timeout)
            except queue.Empty:
                return

    @property
    def faders(self) -> list[int | None]:
        """Last known positions 0-127 of faders 1-8 (None until read or moved)."""
        return self._state.faders

    # --- output ----------------------------------------------------------------

    def set_led(self, button: Button, state: LedState | bool = LedState.ON) -> None:
        self._outputs.set_led(button, state)

    def set_all_mute_leds(self, on: bool) -> None:
        """Separate layer over the MUTE LEDs; switching it off keeps individual MUTE LEDs."""
        self._outputs.set_all_mute_leds(on)

    def all_leds_off(self) -> None:
        self._outputs.all_leds_off()

    def set_knob_blink(self, knob: int, blink: bool) -> None:
        """Blink the LED of knob 1-8 (only blinking is possible).

        The firmware blinks it while the host's value for the fader differs from the
        physical position, so this needs a known fader position (read_faders()).
        """
        self._outputs.set_knob_blink(knob, blink)

    # --- configuration protocol (port "Private") --------------------------------

    def _send_private(self, message: bytes) -> None:
        with self._port_lock:
            if self._private is None:
                raise DisconnectedError("mixer is not connected")
            self._private.send(message)

    def firmware(self, timeout: float = DEFAULT_TIMEOUT) -> str:
        """Firmware name, e.g. "SMC-Mixer_011"."""
        return self._configuration.firmware(timeout)

    def read_memory(self, kind: int, address: int, length: int, timeout: float = DEFAULT_TIMEOUT) -> bytes:
        """Read-only access (Sinco read 0x23). Known readable types: 0x00, 0x04 (RAM), 0x05.

        Keep reads small: a 4 KB read of type 4 once made the device drop off USB briefly.
        """
        return self._configuration.read_memory(kind, address, length, timeout)

    def read_faders(self, timeout: float = DEFAULT_TIMEOUT) -> list[int]:
        """Current physical positions 0-127 of faders 1-8, also stored in `faders`."""
        return self._state.read_faders(timeout)

    @property
    def mode(self) -> DeviceMode | None:
        """Last read device mode; None until read or when disconnected/unavailable."""
        return self._state.mode

    @property
    def user_knobs(self) -> list[int | None]:
        """Last read/received USER knob values; unknown outside confirmed USER mode."""
        return self._state.user_knobs

    def read_mode(self, timeout: float = DEFAULT_TIMEOUT) -> DeviceMode:
        return self._state.read_mode(timeout)

    def read_user_knobs(self, timeout: float = DEFAULT_TIMEOUT) -> list[int]:
        return self._state.read_user_knobs(timeout)

    def refresh_state(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        """Read mode and missing snapshots; explicitly raises read/firmware errors."""
        self._state.refresh(timeout)

    def _poll_state(self) -> None:
        with self._lifecycle_lock:
            if self._closed or not self.connected:
                return
            try:
                self.refresh_state(timeout=0.2)
                self._last_state_error = None
            except SmcMixerError as exc:
                if not self._closed and str(exc) != self._last_state_error:
                    log.warning("refreshing mixer state failed: %s", exc)
                self._last_state_error = str(exc)
