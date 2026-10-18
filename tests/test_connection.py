from __future__ import annotations

import threading
import time

import pytest

from fakes import FADER_VALUES, USER_KNOB_VALUES, FakeDevice, FakePort, device_responder
from smc_mixer import Button, ConnectionEvent, DeviceMode, DisconnectedError, HotplugConfig, LedState, SmcMixer
from smc_mixer.connection import Watcher, ports_present

PORTS = ["SINCO:SINCO SMC-Mixer-Private 28:0", "SINCO:SINCO SMC-Mixer-Master 28:1"]


class FakeScanner:
    def __init__(self) -> None:
        self.current = list(PORTS)
        self.closed = False

    def ports(self) -> list[str]:
        return list(self.current)

    def close(self) -> None:
        self.closed = True


class Opener:
    """Creates fake ports like RtMidiPort would; can be told to fail."""

    def __init__(self) -> None:
        self.opened: dict[str, list[FakePort]] = {"SMC-Mixer-Master": [], "SMC-Mixer-Private": []}
        self.fail = False
        self.device = FakeDevice()

    def __call__(self, name: str) -> FakePort:
        if self.fail:
            raise OSError("port not ready")
        port = FakePort(self.device.respond if "Private" in name else None)
        self.opened[name].append(port)
        return port

    def latest(self, name: str) -> FakePort:
        return self.opened[name][-1]


def make(interval: float = 3600, state_poll_interval: float | None = None) -> tuple[SmcMixer, FakeScanner, Opener]:
    scanner, opener = FakeScanner(), Opener()
    config = HotplugConfig(scanner, opener, "SMC-Mixer-Master", "SMC-Mixer-Private", interval)
    master, private = opener("SMC-Mixer-Master"), opener("SMC-Mixer-Private")
    return SmcMixer(master, private, config, state_poll_interval=state_poll_interval), scanner, opener


def test_reconnect_reloads_mode_and_user_knobs_and_ignores_old_port_callbacks() -> None:
    mixer, scanner, opener = make(state_poll_interval=3600)
    assert mixer.mode == DeviceMode.DAW
    old_master = opener.latest("SMC-Mixer-Master")
    scanner.current = []
    mixer.check_connection()
    assert mixer.mode is None and mixer.user_knobs == [None] * 8
    opener.device.ram[0] = 1
    scanner.current = list(PORTS)
    mixer.check_connection()
    assert mixer.mode == DeviceMode.USER
    assert mixer.user_knobs == USER_KNOB_VALUES
    old_master.emit(bytes([0xB0, 30, 42]))
    assert mixer.user_knobs == USER_KNOB_VALUES
    opener.latest("SMC-Mixer-Master").emit(bytes([0xB0, 30, 43]))
    assert mixer.user_knobs[0] == 43
    mixer.close()


def test_reconnect_does_not_reuse_old_firmware_support() -> None:
    mixer, scanner, opener = make(state_poll_interval=3600)
    assert mixer.mode == DeviceMode.DAW
    scanner.current = []
    mixer.check_connection()
    opener.device.firmware = "SMC-Mixer_013"
    scanner.current = list(PORTS)
    mixer.check_connection()
    assert mixer.connected
    assert mixer.mode is None and mixer.user_knobs == [None] * 8
    assert mixer.faders == [None] * 8
    mixer.close()


def test_ports_present() -> None:
    assert ports_present(PORTS, "SMC-Mixer-Master", "SMC-Mixer-Private")
    assert not ports_present(PORTS[:1], "SMC-Mixer-Master", "SMC-Mixer-Private")


def test_unplug_and_replug_restores_state() -> None:
    mixer, scanner, opener = make()
    mixer.read_faders()
    mixer.set_led(Button.MUTE_1)
    mixer.set_led(Button.PLAY, LedState.BLINK)
    mixer.set_led(Button.REC_1, LedState.OFF)
    mixer.set_all_mute_leds(True)
    mixer.set_knob_blink(1, True)
    old_master = opener.latest("SMC-Mixer-Master")

    scanner.current = []
    assert mixer.check_connection() is False
    assert not mixer.connected and old_master.closed
    with pytest.raises(DisconnectedError):
        mixer.set_led(Button.MUTE_2)  # rejected, so it must not be restored later
    with pytest.raises(DisconnectedError):
        mixer.firmware()

    scanner.current = list(PORTS)
    assert mixer.check_connection() is True
    new_master = opener.latest("SMC-Mixer-Master")
    assert new_master is not old_master
    assert mixer.faders == FADER_VALUES
    assert set(new_master.sent) == {
        bytes([0x90, int(Button.MUTE_1), 127]),
        bytes([0x90, int(Button.PLAY), 1]),
        bytes([0x90, 115, 127]),
        bytes([0xE0, 0, 0]),  # knob 1 blink again (fader 1 at 127)
    }
    events = [e for e in mixer.events(timeout=0.01) if isinstance(e, ConnectionEvent)]
    assert events == [ConnectionEvent(False), ConnectionEvent(True)]
    mixer.close()
    assert scanner.closed


def test_reconnect_with_unreadable_faders_still_connects() -> None:
    mixer, scanner, opener = make()
    mixer.read_faders()
    scanner.current = []
    mixer.check_connection()
    scanner.current = list(PORTS)
    real_call = opener.__call__

    def other_firmware(name: str) -> FakePort:
        if "Private" in name:
            return FakePort(device_responder("SMC-Mixer_013"))
        return real_call(name)

    mixer._hotplug = HotplugConfig(scanner, other_firmware, "SMC-Mixer-Master", "SMC-Mixer-Private")
    assert mixer.check_connection() is True
    assert mixer.faders == [None] * 8  # stale positions are dropped, not kept
    assert ConnectionEvent(True) in list(mixer.events(timeout=0.01))
    mixer.close()


def test_reopen_failure_is_retried() -> None:
    mixer, scanner, opener = make()
    scanner.current = []
    mixer.check_connection()
    scanner.current = list(PORTS)
    opener.fail = True
    assert mixer.check_connection() is False
    opener.fail = False
    assert mixer.check_connection() is True
    mixer.close()


def test_no_change_while_connected() -> None:
    mixer, _, opener = make()
    assert mixer.check_connection() is True
    assert len(opener.opened["SMC-Mixer-Master"]) == 1
    assert list(mixer.events(timeout=0.01)) == []
    mixer.close()


def test_watcher_thread_detects_unplug() -> None:
    mixer, scanner, _ = make(interval=0.01)
    seen = threading.Event()
    mixer.add_listener(lambda e: seen.set() if e == ConnectionEvent(False) else None)
    scanner.current = []
    assert seen.wait(2)
    mixer.close()


def test_watcher_survives_failing_check() -> None:
    calls: list[int] = []

    def check() -> None:
        calls.append(1)
        raise RuntimeError("boom")

    watcher = Watcher(check, 0.01)
    deadline = time.monotonic() + 2
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    watcher.stop()
    assert len(calls) >= 2


def test_close_tolerates_failing_port_close() -> None:
    class Broken(FakePort):
        def close(self) -> None:
            raise OSError("device gone")

    mixer = SmcMixer(Broken(), FakePort())
    mixer.close()
    assert not mixer.connected
