"""Execution and LED state of button actions; runs only on the control thread."""

import logging
from typing import TYPE_CHECKING

from smc_mixer_daemon.audio import Device, Snapshot
from smc_mixer_daemon.buttons import MEDIA_ACTIONS, Binding
from smc_mixer_daemon.commands import BackendError

if TYPE_CHECKING:
    from smc_mixer_daemon.controller import Controller

log = logging.getLogger(__name__)


def inputs(snapshot: Snapshot) -> list[Device]:
    return [d for d in snapshot.devices.values() if d.kind == "source" and not d.monitor]


class Actions:
    def __init__(self, controller: "Controller") -> None:
        self.c = controller

    def device(self, channel: int | None) -> Device | None:
        strip = next((row for row in self.c.config.strips if row.channel == channel), None)
        snapshot = self.c.snapshot
        return snapshot.devices.get((strip.kind, strip.name)) if snapshot and strip else None

    def press(self, binding: Binding) -> None:
        action = binding.action
        if action in MEDIA_ACTIONS:
            self.media(MEDIA_ACTIONS[action])
            return
        # Audio actions need current topology, not state retained across device changes.
        self.c.refresh()
        if not self.c.ready or not self.c.snapshot:
            return
        try:
            self.audio(binding)
        finally:
            self.c.refresh()

    def audio(self, binding: Binding) -> None:
        action, audio, snapshot = binding.action, self.c.audio, self.c.snapshot
        if snapshot is None:
            return
        if action in ("mute_ch", "solo_ch"):
            device = self.device(binding.channel)
            if device is None:
                return
            if action == "mute_ch":
                audio.toggle_mute(device)
            else:
                audio.select(device, lambda: self.c.ready)
        elif action == "mic_mute":
            devices = inputs(snapshot)
            muting = any(not d.muted for d in devices)
            for device in devices:
                audio.mute(device, muting)
        elif action == "ptt":
            self.set_inputs_muted(False)
        elif action == "out_cycle":
            self.cycle_output()

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

    def media(self, action: str) -> None:
        if self.c.media:
            try:
                self.c.media.control(action, lambda: self.c.ready)
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
        if binding.action in ("mute_ch", "solo_ch"):
            device = self.device(binding.channel)
            if device is None:
                return False
            return device.muted if binding.action == "mute_ch" else snapshot.selected(device)
        if binding.action == "mic_mute":
            devices = inputs(snapshot)
            return bool(devices) and all(d.muted for d in devices)
        return binding.action == "ptt" and binding.button in self.c.held
