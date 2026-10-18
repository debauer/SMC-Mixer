"""Execution and LED state of button actions; runs only on the control thread."""

import logging
import time
from typing import TYPE_CHECKING

from smc_mixer import Button
from smc_mixer_daemon.audio import Device, Snapshot
from smc_mixer_daemon.buttons import MEDIA_ACTIONS, Binding
from smc_mixer_daemon.commands import BackendError

if TYPE_CHECKING:
    from smc_mixer_daemon.controller import Controller

log = logging.getLogger(__name__)

MEDIA_STATE = {"play": "Playing", "play_pause": "Playing", "pause": "Paused"}
SETTLE_TRIES = 5
SETTLE_DELAY = 0.2


def outputs(snapshot: Snapshot) -> list[Device]:
    return [d for d in snapshot.devices.values() if d.kind == "sink"]


def inputs(snapshot: Snapshot) -> list[Device]:
    return [d for d in snapshot.devices.values() if d.kind == "source" and not d.monitor]


class Actions:
    def __init__(self, controller: "Controller") -> None:
        self.c = controller
        self.remembered: dict[str, set[str]] = {}

    def device(self, channel: int | None) -> Device | None:
        strip = next((row for row in self.c.config.strips if row.channel == channel), None)
        snapshot = self.c.snapshot
        return snapshot.devices.get((strip.kind, strip.name)) if snapshot and strip else None

    def press(self, binding: Binding) -> None:
        action = binding.action
        if action in MEDIA_ACTIONS:
            self.media(MEDIA_ACTIONS[action], binding.button)
            return
        # Audio actions need current topology, not state retained across device changes.
        self.c.refresh()
        if not self.c.ready or not self.c.snapshot:
            return
        try:
            self.audio(binding)
        finally:
            self.c.refresh()
        if binding.action == "select_ch":
            self.verify_selection(binding)

    def verify_selection(self, binding: Binding) -> None:
        device, snapshot = self.device(binding.channel), self.c.snapshot
        if device is None or snapshot is None:
            return
        # The session manager applies the new default asynchronously; give it a moment before judging.
        for attempt in range(SETTLE_TRIES):
            current = snapshot.sink if device.kind == "sink" else snapshot.source
            if current == device.name:
                log.info("default %s is now %s", device.kind, device.name)
                return
            if attempt + 1 < SETTLE_TRIES:
                time.sleep(SETTLE_DELAY)
                self.c.refresh()
                if self.c.snapshot is None:
                    return
                snapshot = self.c.snapshot
        try:
            ports = self.c.audio.ports(device)
        except BackendError as exc:
            ports = f"ports unavailable: {exc}"
        log.warning(
            "default %s is still %s after selecting %s; the audio server rejected or reverted it (%s)",
            device.kind,
            current,
            device.name,
            ports,
        )

    def audio(self, binding: Binding) -> None:
        action, audio, snapshot = binding.action, self.c.audio, self.c.snapshot
        if snapshot is None:
            return
        if action in ("mute_ch", "select_ch"):
            device = self.device(binding.channel)
            if device is None:
                return
            if action == "mute_ch":
                audio.toggle_mute(device)
            else:
                audio.select(device, lambda: self.c.ready)
        elif action == "mic_mute":
            self.toggle_all("source", inputs(snapshot))
        elif action == "out_mute":
            self.toggle_all("sink", outputs(snapshot))
        elif action == "ptt":
            self.set_inputs_muted(False)
        elif action == "out_cycle":
            self.cycle_output()

    def toggle_all(self, kind: str, devices: list[Device]) -> None:
        """Mute everything, or undo it while keeping devices that were muted before."""
        if any(not d.muted for d in devices):
            self.remembered[kind] = {d.name for d in devices if d.muted}
            for device in devices:
                self.c.audio.mute(device, True)
        else:
            keep = self.remembered.pop(kind, set())
            for device in devices:
                self.c.audio.mute(device, device.name in keep)

    def release(self, binding: Binding) -> None:
        if binding.action != "ptt" or not self.c.ready:
            return
        self.c.refresh()
        try:
            self.set_inputs_muted(True)
        finally:
            self.c.refresh()

    def set_inputs_muted(self, muted: bool) -> None:
        if self.c.snapshot:
            for device in inputs(self.c.snapshot):
                self.c.audio.mute(device, muted)

    def cycle_output(self) -> None:
        snapshot = self.c.snapshot
        if snapshot is None:
            return
        sinks = [
            d
            for strip in sorted(self.c.config.strips, key=lambda s: s.channel)
            if strip.kind == "sink"
            if (d := snapshot.devices.get((strip.kind, strip.name)))
        ]
        if not sinks:
            return
        names = [d.name for d in sinks]
        following = names.index(snapshot.sink) + 1 if snapshot.sink in names else 0
        self.c.audio.select(sinks[following % len(sinks)], lambda: self.c.ready)

    def media(self, action: str, button: Button) -> None:
        if self.c.media:
            try:
                self.c.media.control(action, lambda: self.c.ready)
                self.c.next_media_poll = time.monotonic() + 0.15  # let the player apply it first
                if action in ("next", "previous"):
                    self.c.pulses[button] = time.monotonic()
            except BackendError as exc:
                log.warning("media request failed: %s", exc)

    def lit(self, binding: Binding) -> bool:
        if not self.c.ready:
            return False
        if binding.led == "on":
            return True
        snapshot = self.c.snapshot
        if binding.led != "active" or not snapshot:
            return False
        if binding.action in ("mute_ch", "select_ch"):
            device = self.device(binding.channel)
            if device is None:
                return False
            return device.muted if binding.action == "mute_ch" else snapshot.selected(device)
        if binding.action == "mic_mute":
            devices = inputs(snapshot)
            return bool(devices) and all(d.muted for d in devices)
        if binding.action == "out_mute":
            devices = outputs(snapshot)
            return bool(devices) and all(d.muted for d in devices)
        if binding.action in MEDIA_STATE:
            return self.c.media_state == MEDIA_STATE[binding.action]
        return binding.action == "ptt" and binding.button in self.c.held
