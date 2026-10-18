"""Daemon test boundaries that cannot touch host audio or media."""

from collections.abc import Callable
from dataclasses import replace

from fakes import FakeDevice, FakePort
from smc_mixer import SmcMixer
from smc_mixer_daemon.audio import Audio, Device, Snapshot
from smc_mixer_daemon.commands import BackendError
from smc_mixer_daemon.media import Media


class FakeAudio(Audio):
    def __init__(self) -> None:
        self.devices = {
            ("sink", "speakers"): Device("sink", "speakers", 1, "Speakers", False, 50),
            ("sink", "headset"): Device("sink", "headset", 2, "Headset", True, 25),
            ("source", "mic"): Device("source", "mic", 3, "Microphone", False, 75),
        }
        self.sink, self.source = "speakers", "mic"
        self.calls: list[tuple[object, ...]] = []

    def snapshot(self) -> Snapshot:
        return Snapshot(dict(self.devices), self.sink, self.source)

    def volume(self, device: Device, percent: int) -> None:
        self.calls.append(("volume", device.name, percent))
        self.devices[device.key] = replace(self.devices[device.key], volume=percent)

    def mute(self, device: Device, muted: bool) -> None:
        self.calls.append(("setmute", device.name, muted))
        self.devices[device.key] = replace(self.devices[device.key], muted=muted)

    def toggle_mute(self, device: Device) -> None:
        self.calls.append(("mute", device.name))
        self.devices[device.key] = replace(self.devices[device.key], muted=not self.devices[device.key].muted)

    def select(self, device: Device, valid: Callable[[], bool] = lambda: True) -> None:
        if valid():
            self.calls.append(("select", device.name))
            if device.kind == "sink":
                self.sink = device.name
            else:
                self.source = device.name


class FakeMedia(Media):
    def __init__(self) -> None:
        self.actions: list[str] = []
        self.state: str | None = "Paused"

    def status(self) -> str:
        if self.state is None:
            raise BackendError("no player")
        return self.state

    def control(self, action: str, valid: Callable[[], bool] = lambda: True) -> None:
        if valid():
            self.actions.append(action)


def mixer_pair() -> tuple[SmcMixer, FakePort, FakeDevice]:
    device = FakeDevice()
    master = FakePort()
    return SmcMixer(master, FakePort(device.respond)), master, device
