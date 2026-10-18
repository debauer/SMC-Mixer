"""Serialized application logic; never called on MIDI callback threads."""

import logging
import time
from collections.abc import Callable

from smc_mixer import (
    LED_BUTTONS,
    Button,
    ButtonEvent,
    DeviceMode,
    Event,
    FaderEvent,
    MidiProtocol,
    SmcMixer,
    SmcMixerError,
)
from smc_mixer_daemon.actions import Actions
from smc_mixer_daemon.audio import Audio, Device, Snapshot
from smc_mixer_daemon.buttons import effective
from smc_mixer_daemon.commands import BackendError
from smc_mixer_daemon.config import Config
from smc_mixer_daemon.media import Media
from smc_mixer_daemon.takeover import Takeover, percent

log = logging.getLogger(__name__)

MEDIA_POLL = 1.0


class Controller:
    def __init__(
        self,
        config: Config,
        mixer: SmcMixer,
        audio: Audio,
        media: Media | None,
        valid: Callable[[], bool] = lambda: True,
    ) -> None:
        self.config, self.mixer, self.audio, self.media = config, mixer, audio, media
        self.valid = valid
        self.generation = -1
        self.active = False
        self.snapshot: Snapshot | None = None
        self.states: dict[int, tuple[int, Takeover]] = {}
        self.available: dict[int, bool] = {}
        self.held: set[Button] = set()
        self.leds: dict[Button, bool] = {}
        self.blinks: dict[int, bool] = {}
        self.bindings = effective(config.buttons, {strip.channel for strip in config.strips})
        self.actions = Actions(self)
        self.media_state: str | None = None
        self.media_error = ""
        self.next_media_poll = 0.0

    @property
    def ready(self) -> bool:
        return self.active and self.valid() and self.mixer.connected and self.mixer.mode == DeviceMode.DAW

    def start(self) -> bool:
        self.active = False
        if self.mixer.read_mode() != DeviceMode.DAW:
            return False
        positions = self.mixer.read_faders()
        if self.mixer.read_mode() != DeviceMode.DAW or not self.valid() or not self.mixer.connected:
            return False
        self.states.clear()
        self.held.clear()
        self.leds.clear()
        self.blinks.clear()
        self.active = True
        self.mixer.set_all_mute_leds(False)
        self.refresh(positions)
        log.info("DAW controller initialized (%s takeover)", "soft" if self.config.soft_takeover else "direct")
        return True

    def suspend(self) -> None:
        self.active = False
        self.held.clear()
        self.states.clear()
        self.snapshot = None

    def offline(self) -> None:
        self.snapshot = None
        self.states.clear()
        self.held.clear()

    def refresh(self, positions: list[int] | None = None) -> None:
        snapshot = self.audio.snapshot()
        newcomers = [
            strip
            for strip in self.config.strips
            if (device := snapshot.devices.get((strip.kind, strip.name))) is not None
            and (strip.channel not in self.states or self.states[strip.channel][0] != device.index)
        ]
        if newcomers and self.ready:
            if positions is None:
                positions = self.mixer.read_faders()
            if not self.ready:
                return
        self.snapshot = snapshot
        available = set()
        for strip in self.config.strips:
            device = snapshot.devices.get((strip.kind, strip.name))
            present = device is not None
            if self.available.get(strip.channel) != present:
                report = log.info if present else log.warning
                report(
                    "channel %s device %s: %s %s",
                    strip.channel,
                    "available" if present else "unavailable",
                    strip.kind,
                    strip.name,
                )
                self.available[strip.channel] = present
            if device is None:
                continue
            available.add(strip.channel)
            if self.ready and strip in newcomers and positions is not None:
                state = Takeover(percent(positions[strip.channel - 1]), device.volume, self.config.soft_takeover)
                self.states[strip.channel] = device.index, state
                if not self.config.soft_takeover:
                    self.write(device, state, state.position)
            elif strip.channel in self.states:
                self.states[strip.channel][1].observe(device.volume)
        self.states = {channel: state for channel, state in self.states.items() if channel in available}
        self.feedback()

    def write(self, device: Device, state: Takeover, value: int) -> None:
        if self.ready:
            self.audio.volume(device, value)
            state.written(value)

    def event(self, event: Event) -> None:
        if not self.ready or not isinstance(event, (FaderEvent, ButtonEvent)):
            return
        if event.protocol != MidiProtocol.MCU or event.ambiguous:
            return
        if isinstance(event, FaderEvent):
            strip = next((row for row in self.config.strips if row.channel == event.fader), None)
            device = self.snapshot.devices.get((strip.kind, strip.name)) if self.snapshot and strip else None
            if device and event.fader in self.states:
                state = self.states[event.fader][1]
                value = percent(event.value)
                if state.move(value):
                    self.write(device, state, value)
                self.feedback()
            return
        binding = self.bindings.get(event.button)
        if not event.pressed:
            self.held.discard(event.button)
            if binding:
                self.actions.release(binding)
            return
        if event.button in self.held:
            return
        self.held.add(event.button)
        if binding:
            self.actions.press(binding)

    def poll_media(self) -> None:
        """Read the player state about once a second; an absent player is a state, not a crash."""
        if not self.media or not self.ready or time.monotonic() < self.next_media_poll:
            return
        self.next_media_poll = time.monotonic() + MEDIA_POLL
        try:
            state, error = self.media.status(), ""
        except BackendError as exc:
            state, error = None, str(exc)
        if error != self.media_error:
            self.media_error = error
            if error:
                log.warning("media state unavailable: %s", error)
        if state != self.media_state:
            self.media_state = state
            self.feedback()

    def feedback(self) -> None:
        if not self.mixer.connected:
            self.leds.clear()
            self.blinks.clear()
            return
        wanted = dict.fromkeys(LED_BUTTONS, False)
        for button, binding in self.bindings.items():
            if button in LED_BUTTONS:
                wanted[button] = self.actions.lit(binding)
        for button, on in wanted.items():
            if self.leds.get(button) != on:
                self.mixer.set_led(button, on)
                self.leds[button] = on
        for channel in range(1, 9):
            state = self.states.get(channel)
            blink = bool(self.ready and state and self.config.soft_takeover and not state[1].owned)
            if self.blinks.get(channel) != blink and self.mixer.faders[channel - 1] is not None:
                self.mixer.set_knob_blink(channel, blink)
                self.blinks[channel] = blink

    def shutdown(self) -> None:
        self.active = False
        try:
            self.feedback()
        except (SmcMixerError, OSError) as exc:
            log.warning("clearing daemon feedback failed: %s", exc)
