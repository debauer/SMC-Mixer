"""Command adapters use names, bounded calls and explicit player routing."""

import json
import subprocess

import pytest

from smc_mixer_daemon.audio import Audio
from smc_mixer_daemon.commands import BackendError, CommandError, run
from smc_mixer_daemon.media import Media


class Pactl:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.streams = [{"index": 10, "sink": 4}, {"index": 11, "sink": 8}]
        self.fail: set[int] = set()
        self.introduce = False

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[1:3] == ["--format=json", "list"]:
            if args[3] == "sink-inputs":
                return json.dumps(self.streams)
            return json.dumps(
                [
                    {
                        "index": 8,
                        "name": "speaker name; literal",
                        "mute": True,
                        "volume": {"front-left": {"value": 32768}, "front-right": {"value": 65536}},
                        "monitor_of_sink": None,
                    }
                ]
            )
        if args[1] == "move-sink-input":
            index = int(args[2])
            if index in self.fail:
                raise BackendError("move failed")
            for row in self.streams:
                if row["index"] == index:
                    row["sink"] = 8
            if self.introduce:
                self.streams.append({"index": 12, "sink": 4})
                self.introduce = False
        return "speaker name; literal"


def test_snapshot_and_volume_preserve_names_and_mute() -> None:
    pactl = Pactl()
    audio = Audio(pactl)
    snapshot = audio.snapshot()
    device = snapshot.devices["sink", "speaker name; literal"]
    assert device.volume == 75 and device.muted and snapshot.selected(device)
    audio.volume(device, 100)
    assert pactl.calls[-1] == ["pactl", "set-sink-volume", "speaker name; literal", "100%"]
    assert not any("mute" in call[1] for call in pactl.calls)


def test_output_switch_moves_streams_including_followup() -> None:
    pactl = Pactl()
    pactl.introduce = True
    audio = Audio(pactl)
    audio.select(audio.snapshot().devices["sink", "speaker name; literal"])
    assert [row["sink"] for row in pactl.streams] == [8, 8, 8]
    writes = [call for call in pactl.calls if call[1] in ("set-default-sink", "move-sink-input")]
    assert [call[1] for call in writes] == ["set-default-sink", "move-sink-input", "move-sink-input"]


def test_partial_move_failure_is_logged_and_other_streams_move(caplog: pytest.LogCaptureFixture) -> None:
    pactl = Pactl()
    pactl.fail.add(10)
    pactl.streams.append({"index": 12, "sink": 4})
    audio = Audio(pactl)
    audio.select(audio.snapshot().devices["sink", "speaker name; literal"])
    assert "stream 10" in caplog.text
    assert pactl.streams[-1]["sink"] == 8


def test_source_switch_does_not_move_recordings_and_invalid_generation_does_nothing() -> None:
    pactl = Pactl()
    audio = Audio(pactl)
    device = audio.snapshot().devices["source", "speaker name; literal"]
    pactl.calls.clear()
    audio.select(device, lambda: False)
    assert pactl.calls == []
    audio.select(device)
    assert pactl.calls == [["pactl", "set-default-source", device.name]]


@pytest.mark.parametrize(
    "data", ["not JSON", "{}", '[{"mute": false}]', '[{"index": 1, "name": "x", "mute": false, "volume": {}}]']
)
def test_invalid_audio_state(data: str) -> None:
    with pytest.raises(BackendError):
        Audio(lambda _: data).snapshot()


def test_media_explicit_resolution_and_separate_actions() -> None:
    calls = []

    def command(args: list[str]) -> str:
        calls.append(args)
        return "firefox.instance1\nqobuz" if args[1] == "--list-all" else ""

    media = Media("firefox", command)
    media.control("play")
    media.control("pause")
    assert calls[1] == ["playerctl", "--player", "firefox.instance1", "play"]
    assert calls[3][-1] == "pause"
    for players in ("", "firefox.instance1\nfirefox.instance2"):
        with pytest.raises(BackendError, match="expected one match"):
            Media("firefox", lambda _, value=players: value).resolve()


def test_no_players_is_not_a_bus_error() -> None:
    def missing(_: list[str]) -> str:
        raise CommandError(["playerctl"], 1, "No players found")

    assert Media("x", missing).players() == []
    with pytest.raises(BackendError):
        Media("x", missing).resolve()


def test_media_request_drops_if_connection_changes_during_player_resolution() -> None:
    connected = True
    calls = []

    def command(args: list[str]) -> str:
        nonlocal connected
        calls.append(args)
        connected = False
        return "firefox"

    Media("firefox", command).control("play", lambda: connected)
    assert calls == [["playerctl", "--list-all"]]


def test_command_timeout_and_nonzero_are_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*args: object, **kwargs: object) -> None:
        assert kwargs["timeout"] == 2
        assert "shell" not in kwargs
        raise subprocess.TimeoutExpired(["pactl"], 2)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(BackendError, match="timed out"):
        run(["pactl", "info"])
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, "", "bad"))
    with pytest.raises(CommandError, match="bad"):
        run(["pactl", "info"])


def test_monitor_sources_are_recognized_by_name_class_or_sink_link() -> None:
    from smc_mixer_daemon.audio import is_monitor

    assert is_monitor({"name": "alsa_output.pci-0000_01_00.1.hdmi-stereo-extra3.monitor"})
    assert is_monitor({"name": "x", "properties": {"device.class": "monitor"}})
    assert is_monitor({"name": "x", "monitor_of_sink": 0})
    assert is_monitor({"name": "x", "monitor_of_sink": "alsa_output.x"})
    assert not is_monitor({"name": "alsa_input.usb-mic", "monitor_of_sink": None, "properties": {}})
    assert not is_monitor({"name": "alsa_input.usb-mic", "monitor_of_sink": "n/a"})


def test_port_summary_lists_availability_for_diagnostics() -> None:
    import json

    from smc_mixer_daemon.audio import Audio, Device

    rows = [
        {
            "name": "alsa_input.mic2",
            "active_port": "analog-input-mic2",
            "ports": [{"name": "analog-input-mic2", "availability": "not available"}, {"name": "other"}],
        }
    ]
    audio = Audio(lambda args: json.dumps(rows))
    device = Device("source", "alsa_input.mic2", 1, "Mic 2", False, 10)
    assert (
        audio.ports(device)
        == "active port 'analog-input-mic2'; ports: analog-input-mic2: not available; other: unknown"
    )
    assert audio.ports(Device("source", "gone", 2, "x", False, 0)) == "device no longer listed"
