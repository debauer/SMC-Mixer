"""Runtime ingress, absence, CLI read-only guarantees and child cleanup."""

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from daemon_fakes import FakeAudio, mixer_pair
from smc_mixer import PortNotFoundError
from smc_mixer_daemon import cli, runtime
from smc_mixer_daemon.commands import BackendError
from smc_mixer_daemon.config import Config, Strip
from smc_mixer_daemon.subscription import Subscription


def test_check_and_devices_never_run_daemon_or_write_audio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[[strips]]\nchannel = 1\nkind = "sink"\nname = "speakers"\n')
    audio = FakeAudio()
    monkeypatch.setattr(cli, "Audio", lambda: audio)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/fake/pactl" if name == "pactl" else None)
    monkeypatch.setattr(cli, "run", lambda *args: pytest.fail("read-only command started daemon"))
    for args in (["check", "--config", str(path)], ["devices"]):
        monkeypatch.setattr(sys, "argv", ["smc-mixer-daemon", *args])
        assert cli.main() == 0
    assert not audio.calls
    assert "Configuration valid" in capsys.readouterr().out
    path.write_text('[[strips]]\nchannel = 1\nkind = "sink"\nname = "absent"\n')
    monkeypatch.setattr(sys, "argv", ["smc-mixer-daemon", "check", "--config", str(path)])
    assert cli.main() == 1
    path.write_text("invalid = true")
    assert cli.main() == 2


def test_missing_executable_is_configuration_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[media]\nplayer = "firefox"')
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["smc-mixer-daemon", "run", "--config", str(path)])
    assert cli.main() == 2


def test_loop_initializes_then_coalesces_midi_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    mixer, master, _ = mixer_pair()
    audio = FakeAudio()
    monkeypatch.setattr(runtime.SmcMixer, "open", lambda: mixer)
    monkeypatch.setattr(runtime, "Audio", lambda: audio)

    class Stop(threading.Event):
        ticks = 0

        def wait(self, timeout: float | None = None) -> bool:
            self.ticks += 1
            if self.ticks == 2:
                audio.calls.clear()
                for value in range(128):
                    master.emit(bytes([0xE0, 0, value]))
                master.emit(bytes([0x90, 16, 127]))
                master.emit(bytes([0x90, 16, 0]))
            if self.ticks >= 4:
                self.set()
            return self.is_set()

    stop = Stop()
    runtime.control_loop(Config((Strip(1, "sink", "speakers"),)), stop, Subscription())
    assert audio.calls == [("volume", "speakers", 100), ("mute", "speakers")]
    assert master.closed


def test_loop_survives_initial_absence_without_busy_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = []

    def absent() -> None:
        attempts.append(1)
        raise PortNotFoundError("not attached")

    monkeypatch.setattr(runtime.SmcMixer, "open", absent)

    class Stop(threading.Event):
        ticks = 0

        def wait(self, timeout: float | None = None) -> bool:
            self.ticks += 1
            if self.ticks > 5:
                self.set()
            return self.is_set()

    runtime.control_loop(Config((), player="firefox"), Stop(), Subscription())
    assert attempts == [1]


def test_subscription_terminates_and_reaps_child(monkeypatch: pytest.MonkeyPatch) -> None:
    original = subprocess.Popen
    processes = []

    def spawn(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        assert args[0] == ["pactl", "subscribe"]
        child = original(
            [sys.executable, "-u", "-c", "import time; print('Event change'); time.sleep(30)"],
            **kwargs,
        )
        processes.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", spawn)
    subscription = Subscription()
    subscription.start()
    assert subscription.changed.wait(2)
    subscription.close()
    assert len(processes) == 1 and processes[0].poll() is not None
    assert not subscription._thread.is_alive()


def test_audio_failure_recovers_without_replaying_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    mixer, master, _ = mixer_pair()
    audio = FakeAudio()
    original = audio.snapshot
    count = 0

    def snapshot():
        nonlocal count
        count += 1
        if count == 1:
            raise BackendError("server down")
        return original()

    monkeypatch.setattr(audio, "snapshot", snapshot)
    monkeypatch.setattr(runtime.SmcMixer, "open", lambda: mixer)
    monkeypatch.setattr(runtime, "Audio", lambda: audio)
    clock = 100.0
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock)

    class Stop(threading.Event):
        ticks = 0

        def wait(self, timeout: float | None = None) -> bool:
            nonlocal clock
            clock += 2
            self.ticks += 1
            if self.ticks == 3:
                master.emit(bytes([0x90, 16, 127]))
                master.emit(bytes([0x90, 16, 0]))
            if self.ticks >= 5:
                self.set()
            return self.is_set()

    runtime.control_loop(Config((Strip(1, "sink", "speakers"),)), Stop(), Subscription())
    assert count >= 2
    assert [call for call in audio.calls if call[0] == "mute"] == [("mute", "speakers")]
