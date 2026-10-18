"""LED commands and the output state that must survive a reconnect."""

import threading
from collections.abc import Callable

from smc_mixer.constants import CHANNELS, NOTE_ALL_MUTE_LEDS
from smc_mixer.controls import LED_BUTTONS, Button, LedState
from smc_mixer.errors import ProtocolError


class Outputs:
    def __init__(self, send: Callable[[bytes], None], faders: Callable[[], list[int | None]]) -> None:
        self._send, self._faders = send, faders
        self._lock = threading.Lock()
        self._leds: dict[int, int] = {}
        self._knob_blink: dict[int, bool] = {}

    def set_led(self, button: Button, state: LedState | bool) -> None:
        if button not in LED_BUTTONS:
            raise ValueError(f"{button.name} has no controllable LED")
        level = LedState.ON if state is True else LedState.OFF if state is False else state
        self._send(bytes([0x90, int(button), int(level)]))
        with self._lock:
            self._leds[int(button)] = int(level)

    def set_all_mute_leds(self, on: bool) -> None:
        level = LedState.ON if on else LedState.OFF
        self._send(bytes([0x90, NOTE_ALL_MUTE_LEDS, level]))
        with self._lock:
            self._leds[NOTE_ALL_MUTE_LEDS] = int(level)

    def all_leds_off(self) -> None:
        for button in sorted(LED_BUTTONS):
            self.set_led(button, LedState.OFF)
        self.set_all_mute_leds(False)

    def set_knob_blink(self, knob: int, blink: bool) -> None:
        if not 1 <= knob <= CHANNELS:
            raise ValueError(f"knob must be 1-8, got {knob}")
        current = self._faders()[knob - 1]
        if current is None:
            raise ProtocolError(f"position of fader {knob} unknown; call read_faders() first")
        target = (0 if current >= 64 else 127) if blink else current
        self._send(bytes([0xE0 | (knob - 1), 0, target]))
        with self._lock:
            self._knob_blink[knob] = blink

    def restore(self) -> None:
        with self._lock:
            leds, blinks = dict(self._leds), dict(self._knob_blink)
        for note, velocity in leds.items():
            if velocity:
                self._send(bytes([0x90, note, velocity]))
        for knob, blink in blinks.items():
            if blink and self._faders()[knob - 1] is not None:
                self.set_knob_blink(knob, True)
