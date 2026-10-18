"""Automatic state tracking and watcher lifecycle through the public API."""

import threading

import pytest

from fakes import USER_KNOB_VALUES, FakeDevice, FakePort
from smc_mixer import DeviceMode, HotplugConfig, ModeEvent, SmcMixer, UserKnobsEvent


def fake_open(monkeypatch: pytest.MonkeyPatch, device: FakeDevice) -> tuple[FakePort, FakePort]:
    master, private = FakePort(), FakePort(device.respond)
    monkeypatch.setattr("smc_mixer.mixer.RtMidiPort", lambda name: master if "Master" in name else private)
    return master, private


def test_open_initializes_and_automatically_tracks_mode_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    device = FakeDevice()
    master, _ = fake_open(monkeypatch, device)
    ready = threading.Event()
    with SmcMixer.open(auto_reconnect=False, state_poll_interval=0.01) as mixer:
        assert mixer.mode == DeviceMode.DAW

        def listener(event: object) -> None:
            if isinstance(event, UserKnobsEvent):
                ready.set()

        mixer.add_listener(listener)
        device.ram[0] = 1
        assert ready.wait(2)
        assert mixer.mode == DeviceMode.USER
        assert mixer.user_knobs == USER_KNOB_VALUES
        assert not master.sent
    assert mixer.mode is None
    assert mixer.user_knobs == [None] * 8


def test_automatic_state_polling_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    device = FakeDevice(user=True)
    _, private = fake_open(monkeypatch, device)
    with SmcMixer.open(auto_reconnect=False, state_poll_interval=None) as mixer:
        assert not private.sent
        assert mixer.mode is None
        assert mixer.read_mode() == DeviceMode.USER


@pytest.mark.parametrize("exception", [RuntimeError, KeyboardInterrupt])
def test_failed_initial_backend_read_closes_allocated_ports(
    monkeypatch: pytest.MonkeyPatch,
    exception: type[BaseException],
) -> None:
    def fail(message: bytes) -> list[bytes]:
        raise exception("backend unavailable")

    master, private = FakePort(), FakePort(fail)
    monkeypatch.setattr("smc_mixer.mixer.RtMidiPort", lambda name: master if "Master" in name else private)
    with pytest.raises(exception, match="backend unavailable"):
        SmcMixer.open(auto_reconnect=False)
    assert master.closed and private.closed


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_invalid_state_intervals_fail_before_opening_ports(interval: float, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_open(name: str) -> None:
        raise AssertionError("invalid settings must not allocate MIDI ports")

    monkeypatch.setattr("smc_mixer.mixer.RtMidiPort", unexpected_open)
    with pytest.raises(ValueError, match="state_poll_interval"):
        SmcMixer.open(state_poll_interval=interval)


def test_close_from_state_listener_does_not_deadlock() -> None:
    class Scanner:
        def ports(self) -> list[str]:
            return ["Master", "Private"]

        def close(self) -> None:
            self.closed = True

    device = FakeDevice()
    scanner = Scanner()
    config = HotplugConfig(scanner, lambda name: FakePort(), "Master", "Private", 0.01)
    mixer = SmcMixer(FakePort(), FakePort(device.respond), config, state_poll_interval=0.01)
    closed = threading.Event()

    def listener(event: object) -> None:
        if isinstance(event, ModeEvent) and event.mode == DeviceMode.USER:
            mixer.close()
            closed.set()

    mixer.add_listener(listener)
    device.ram[0] = 1
    assert closed.wait(2)
    assert not mixer.connected
    assert scanner.closed
    modes = [event.mode for event in mixer.events(timeout=0) if isinstance(event, ModeEvent)]
    assert modes == [DeviceMode.DAW, DeviceMode.USER, None]
    mixer.close()  # Closing again is harmless, including after a callback initiated shutdown.


def test_unsupported_firmware_keeps_midi_available_and_deduplicates_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    device = FakeDevice("SMC-Mixer_013")
    master, private = fake_open(monkeypatch, device)
    with SmcMixer.open(auto_reconnect=False, state_poll_interval=3600) as mixer:
        assert mixer.connected and mixer.mode is None
        mixer._poll_state()
        assert sum("refreshing mixer state failed" in record.message for record in caplog.records) == 1
        assert len(private.sent) == 1  # Firmware rejection does not trigger RAM reads.
        master.emit(bytes([0xB0, 16, 1]))
        assert any(getattr(event, "delta", None) == 1 for event in mixer.events(timeout=0))
