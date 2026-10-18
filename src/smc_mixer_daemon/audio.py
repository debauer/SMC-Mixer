"""PulseAudio-compatible device state and routing, including pipewire-pulse."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from smc_mixer_daemon.commands import BackendError, Runner, run

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Device:
    kind: str
    name: str
    index: int
    description: str
    muted: bool
    volume: float
    monitor: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.name


@dataclass(frozen=True)
class Snapshot:
    devices: dict[tuple[str, str], Device]
    sink: str
    source: str

    def selected(self, device: Device) -> bool:
        return device.name == (self.sink if device.kind == "sink" else self.source)


def integer(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"expected a nonnegative integer, got {value!r}")
    return value


def is_monitor(row: dict[str, Any]) -> bool:
    """Monitor sources mirror an output ("Monitor of ... Output"); they are not microphones."""
    properties = row.get("properties")
    device_class = properties.get("device.class") if isinstance(properties, dict) else None
    return (
        str(row.get("name", "")).endswith(".monitor")
        or device_class == "monitor"
        or row.get("monitor_of_sink") not in (None, "", "n/a", 4294967295, -1)
    )


class Audio:
    def __init__(self, runner: Runner = run) -> None:
        self.run = runner

    def objects(self, category: str) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.run(["pactl", "--format=json", "list", category]))
            if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
                raise ValueError("expected an array of objects")
            return data
        except (ValueError, TypeError) as exc:
            raise BackendError(f"invalid pactl {category} JSON: {exc}") from exc

    def snapshot(self) -> Snapshot:
        devices = {}
        try:
            for kind in ("sink", "source"):
                for row in self.objects(kind + "s"):
                    device_name = row["name"]
                    muted = row["mute"]
                    if not isinstance(device_name, str) or not device_name or type(muted) is not bool:
                        raise ValueError("invalid device name/mute")
                    volumes = [integer(channel["value"]) for channel in row["volume"].values()]
                    if not volumes:
                        raise ValueError("device has no volume channels")
                    device = Device(
                        kind,
                        device_name,
                        integer(row["index"]),
                        str(row.get("description", device_name)),
                        muted,
                        sum(volumes) / len(volumes) * 100 / 65536,
                        kind == "source" and is_monitor(row),
                    )
                    if device.key in devices:
                        raise ValueError(f"duplicate device name {device_name}")
                    devices[device.key] = device
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise BackendError(f"invalid pactl device state: {exc}") from exc
        defaults = {
            kind: self.run(["pactl", f"get-default-{kind}"]) if any(key[0] == kind for key in devices) else ""
            for kind in ("sink", "source")
        }
        return Snapshot(devices, defaults["sink"], defaults["source"])

    def volume(self, device: Device, percent: int) -> None:
        if not 0 <= percent <= 100:
            raise ValueError("volume must be 0-100 percent")
        self.run(["pactl", f"set-{device.kind}-volume", device.name, f"{percent}%"])

    def toggle_mute(self, device: Device) -> None:
        self.run(["pactl", f"set-{device.kind}-mute", device.name, "toggle"])

    def mute(self, device: Device, muted: bool) -> None:
        self.run(["pactl", f"set-{device.kind}-mute", device.name, "1" if muted else "0"])

    def playback(self) -> list[tuple[int, int]]:
        try:
            return [(integer(row["index"]), integer(row["sink"])) for row in self.objects("sink-inputs")]
        except (KeyError, ValueError) as exc:
            raise BackendError(f"invalid sink-input state: {exc}") from exc

    def select(self, device: Device, valid: Callable[[], bool] = lambda: True) -> None:
        if not valid():
            return
        self.run(["pactl", f"set-default-{device.kind}", device.name])
        if device.kind != "sink":
            return
        failures: dict[int, str] = {}
        for _ in range(2):
            for index, sink in self.playback():
                if not valid():
                    return
                if sink != device.index:
                    try:
                        self.run(["pactl", "move-sink-input", str(index), device.name])
                        failures.pop(index, None)
                    except BackendError as exc:
                        failures[index] = str(exc)
        remaining = {index for index, sink in self.playback() if sink != device.index}
        for index in failures.keys() & remaining:
            log.warning("playback stream %s could not be moved: %s", index, failures[index])
