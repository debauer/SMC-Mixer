from __future__ import annotations

import pytest

from fakes import FADER_VALUES, FakePort, device_responder
from smc_mixer import (
    Button,
    ButtonEvent,
    FaderEvent,
    Layer,
    LedState,
    ProtocolError,
    ResponseTimeoutError,
    SmcMixer,
    UnsupportedFirmwareError,
    sinco,
    strip_button,
)


def make_mixer(firmware: str = "SMC-Mixer_011") -> tuple[SmcMixer, FakePort, FakePort]:
    master, private = FakePort(), FakePort(device_responder(firmware))
    return SmcMixer(master, private), master, private


def test_firmware_and_faders() -> None:
    mixer, _, _ = make_mixer()
    assert mixer.firmware() == "SMC-Mixer_011"
    assert mixer.faders == [None] * 8
    assert mixer.read_faders() == FADER_VALUES
    assert mixer.faders == FADER_VALUES


def test_read_faders_refuses_other_firmware() -> None:
    mixer, _, _ = make_mixer("SMC-Mixer_013")
    with pytest.raises(UnsupportedFirmwareError):
        mixer.read_faders()


def test_read_memory_error_reply() -> None:
    mixer, _, _ = make_mixer()
    with pytest.raises(ProtocolError):
        mixer.read_memory(0x07, 0, 16)


def test_request_timeout() -> None:
    mixer = SmcMixer(FakePort(), FakePort())
    with pytest.raises(ResponseTimeoutError):
        mixer.firmware(timeout=0.05)


def test_events_listeners_and_fader_tracking() -> None:
    mixer, master, _ = make_mixer()
    seen: list[object] = []
    mixer.add_listener(seen.append)
    master.emit(bytes([0x90, 24, 127]))
    master.emit(bytes([0xE2, 0x00, 0x40]))
    expected = [ButtonEvent(Button.SELECT_1, True, Layer.DAW), FaderEvent(3, 64, Layer.DAW)]
    assert seen == expected
    assert list(mixer.events(timeout=0.01)) == expected
    assert mixer.faders[2] == 64
    mixer.remove_listener(seen.append)


def test_leds() -> None:
    mixer, master, _ = make_mixer()
    mixer.set_led(Button.REC_1)
    mixer.set_led(strip_button("mute", 8), LedState.BLINK)
    mixer.set_led(Button.PLAY, False)
    mixer.set_all_mute_leds(True)
    assert master.sent == [bytes([0x90, 0, 127]), bytes([0x90, 23, 1]), bytes([0x90, 94, 0]), bytes([0x90, 115, 127])]
    with pytest.raises(ValueError):
        mixer.set_led(Button.BANK_LEFT)


def test_all_leds_off() -> None:
    mixer, master, _ = make_mixer()
    mixer.all_leds_off()
    assert len(master.sent) == 38
    assert all(m[0] == 0x90 and m[2] == 0 for m in master.sent)


def test_knob_blink_uses_fader_position() -> None:
    mixer, master, _ = make_mixer()
    with pytest.raises(ProtocolError):
        mixer.set_knob_blink(1, True)
    mixer.read_faders()
    mixer.set_knob_blink(1, True)  # fader 1 at 127 -> send 0
    mixer.set_knob_blink(3, True)  # fader 3 at 0 -> send 127
    mixer.set_knob_blink(4, False)  # stop: send current 0x3B
    assert master.sent == [bytes([0xE0, 0, 0]), bytes([0xE2, 0, 127]), bytes([0xE3, 0, 0x3B])]
    with pytest.raises(ValueError):
        mixer.set_knob_blink(9, True)


def test_private_port_ignores_non_sysex_and_context_closes() -> None:
    mixer, master, private = make_mixer()
    private.emit(bytes([0xE0, 0x00, 0x10]))  # fader echo on private port is ignored
    assert mixer.firmware() == "SMC-Mixer_011"
    with mixer:
        pass
    assert master.closed and private.closed


def test_sinco_golden_vector() -> None:
    assert sinco.encode_sysex(sinco.CMD_QUERY_NAME_VER) == bytes.fromhex("F0 00 32 45 00 00 00 40 7F F7")
