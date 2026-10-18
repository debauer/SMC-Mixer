"""Public state API, cache coherence and firmware-checked snapshots."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from fakes import FADER_VALUES, USER_KNOB_VALUES, FakeDevice, FakePort
from smc_mixer import (
    DeviceMode,
    KnobEvent,
    Layer,
    MidiProtocol,
    ModeEvent,
    ProtocolError,
    ResponseTimeoutError,
    SmcMixer,
    UnsupportedFirmwareError,
    UserKnobsEvent,
    sinco,
)


def make(user: bool = True, firmware: str = "SMC-Mixer_011") -> tuple[SmcMixer, FakeDevice, FakePort, FakePort]:
    device = FakeDevice(firmware, user)
    master, private = FakePort(), FakePort(device.respond)
    return SmcMixer(master, private), device, master, private


def test_public_reads_initialize_caches_without_fabricating_movement() -> None:
    mixer, _, master, _ = make()
    assert mixer.mode is None
    assert mixer.user_knobs == [None] * 8
    assert mixer.read_mode() == DeviceMode.USER
    assert mixer.read_user_knobs() == USER_KNOB_VALUES
    assert mixer.user_knobs == USER_KNOB_VALUES
    assert list(mixer.events(timeout=0)) == [ModeEvent(DeviceMode.USER), UserKnobsEvent(tuple(USER_KNOB_VALUES))]
    assert master.sent == []
    copy = mixer.user_knobs
    copy[0] = 0
    assert mixer.user_knobs == USER_KNOB_VALUES
    mixer.close()


@pytest.mark.parametrize(("byte", "mode"), [(0x80, DeviceMode.DAW), (0x81, DeviceMode.USER)])
def test_mode_masks_unrelated_bits(byte: int, mode: DeviceMode) -> None:
    mixer, device, _, _ = make()
    device.ram[0] = byte
    assert mixer.read_mode() == mode
    mixer.close()


def test_refresh_only_reads_missing_snapshots() -> None:
    mixer, _, _, private = make()
    mixer.refresh_state()
    assert mixer.faders == FADER_VALUES
    assert mixer.user_knobs == USER_KNOB_VALUES
    private.sent.clear()
    mixer.refresh_state()
    requests = [sinco.decode_sysex(message) for message in private.sent]
    assert len(requests) == 1 and requests[0].cmd == sinco.CMD_READ
    assert int.from_bytes(requests[0].payload[1:5], "little") == 0
    mixer.close()


def test_mode_and_protocol_are_independent_and_cc_updates_known_user_values() -> None:
    mixer, device, master, _ = make()
    mixer.refresh_state()
    list(mixer.events(timeout=0))
    master.emit(bytes([0xB0, 30, 51]))
    assert mixer.user_knobs[0] == 51
    event = next(mixer.events(timeout=0))
    assert event == KnobEvent(1, Layer.CC, value=51)
    assert event.protocol == MidiProtocol.CC
    device.ram[0] = 0
    assert mixer.read_mode() == DeviceMode.DAW
    assert mixer.user_knobs == [None] * 8
    master.emit(bytes([0xB0, 30, 52]))
    assert mixer.mode == DeviceMode.DAW  # CC alone does not prove USER or Shift.
    assert mixer.user_knobs == [None] * 8
    master.emit(bytes([0xB0, 16, 65]))
    assert mixer.user_knobs == [None] * 8
    mixer.close()


def test_user_read_in_daw_is_rejected_without_reading_user_table() -> None:
    mixer, _, _, private = make(user=False)
    with pytest.raises(ProtocolError, match="require USER"):
        mixer.read_user_knobs()
    reads = [sinco.decode_sysex(message) for message in private.sent]
    assert not any(p.cmd == sinco.CMD_READ and int.from_bytes(p.payload[1:5], "little") == 0x246 for p in reads)
    assert mixer.mode == DeviceMode.DAW
    mixer.close()


def test_other_firmware_is_rejected_before_state_addresses_are_read() -> None:
    mixer, _, _, private = make(firmware="SMC-Mixer_013")
    with pytest.raises(UnsupportedFirmwareError):
        mixer.read_mode()
    with pytest.raises(UnsupportedFirmwareError):
        mixer.read_user_knobs()
    assert all(sinco.decode_sysex(message).cmd == sinco.CMD_QUERY_NAME_VER for message in private.sent)
    assert mixer.mode is None
    mixer.close()


@pytest.mark.parametrize("value", [128, 256, 65535])
def test_invalid_user_words_are_not_masked_into_valid_values(value: int) -> None:
    mixer, device, _, _ = make()
    device.ram[0x246:0x248] = value.to_bytes(2, "little")
    with pytest.raises(ProtocolError, match=r"outside 0\.\.127"):
        mixer.read_user_knobs()
    assert mixer.mode == DeviceMode.USER
    assert mixer.user_knobs == [None] * 8
    device.ram[0x246:0x248] = (127).to_bytes(2, "little")
    mixer.refresh_state()
    assert mixer.user_knobs[0] == 127
    mixer.close()


def test_mode_change_during_snapshot_does_not_publish_user_values_in_daw() -> None:
    device = FakeDevice(user=True)

    def respond(message: bytes) -> list[bytes]:
        packet = sinco.decode_sysex(message)
        result = device.respond(message)
        if packet.cmd == sinco.CMD_READ and int.from_bytes(packet.payload[1:5], "little") == 0x246:
            device.ram[0] = 0
        return result

    with SmcMixer(FakePort(), FakePort(respond)) as mixer:
        with pytest.raises(ProtocolError, match="mode changed"):
            mixer.read_user_knobs()
        assert mixer.mode == DeviceMode.DAW
        assert mixer.user_knobs == [None] * 8
        assert not any(isinstance(event, UserKnobsEvent) for event in mixer.events(timeout=0))


def test_newer_midi_values_win_over_an_in_flight_ram_snapshot() -> None:
    device = FakeDevice(user=True)
    master = FakePort()

    def respond(message: bytes) -> list[bytes]:
        packet = sinco.decode_sysex(message)
        result = device.respond(message)  # Response contains the old RAM value.
        if packet.cmd == sinco.CMD_READ and int.from_bytes(packet.payload[1:5], "little") == 0x246:
            master.emit(bytes([0xB0, 30, 61]))
        return result

    with SmcMixer(master, FakePort(respond)) as mixer:
        values = mixer.read_user_knobs()
        assert values[0] == mixer.user_knobs[0] == 61
        snapshots = [event for event in mixer.events(timeout=0) if isinstance(event, UserKnobsEvent)]
        assert snapshots[-1].values[0] == 61


def test_newer_fader_midi_wins_over_an_in_flight_ram_snapshot() -> None:
    device = FakeDevice()
    master = FakePort()

    def respond(message: bytes) -> list[bytes]:
        packet = sinco.decode_sysex(message)
        result = device.respond(message)
        if packet.cmd == sinco.CMD_READ and int.from_bytes(packet.payload[1:5], "little") == 0x326:
            master.emit(bytes([0xE0, 0, 50]))
        return result

    with SmcMixer(master, FakePort(respond)) as mixer:
        assert mixer.read_faders()[0] == 50
        assert mixer.faders[0] == 50


def test_ambiguous_endpoint_is_flagged_and_corrected_by_a_read_snapshot() -> None:
    mixer, device, master, _ = make()
    mixer.refresh_state()
    list(mixer.events(timeout=0))
    device.ram[0x246:0x248] = b"\x7f\x00"
    master.emit(bytes([0xB0, 30, 127]))
    event = next(mixer.events(timeout=0))
    assert event.ambiguous
    assert mixer.user_knobs[0] == USER_KNOB_VALUES[0]
    mixer.refresh_state()
    assert mixer.user_knobs[0] == 127
    mixer.close()


def test_mode_timeout_invalidates_cached_user_values() -> None:
    device = FakeDevice(user=True)
    enabled = True

    def respond(message: bytes) -> list[bytes]:
        return device.respond(message) if enabled else []

    with SmcMixer(FakePort(), FakePort(respond)) as mixer:
        mixer.refresh_state()
        enabled = False
        with pytest.raises(ResponseTimeoutError):
            mixer.read_mode(timeout=0.01)
        assert mixer.mode is None
        assert mixer.user_knobs == [None] * 8
        enabled = True
        mixer.refresh_state()
        assert mixer.user_knobs == USER_KNOB_VALUES


def test_short_mode_and_knob_responses_are_explicit_errors() -> None:
    device = FakeDevice(user=True)
    short_address = 0

    def respond(message: bytes) -> list[bytes]:
        packet = sinco.decode_sysex(message)
        if packet.cmd == sinco.CMD_READ and int.from_bytes(packet.payload[1:5], "little") == short_address:
            return [sinco.encode_sysex(packet.cmd, packet.payload)]
        return device.respond(message)

    with SmcMixer(FakePort(), FakePort(respond)) as mixer:
        with pytest.raises(ProtocolError, match="mode response"):
            mixer.read_mode()
        short_address = 0x246
        with pytest.raises(ProtocolError, match="USER knob table"):
            mixer.read_user_knobs()
        assert mixer.user_knobs == [None] * 8


def test_parallel_sinco_requests_do_not_consume_each_others_responses() -> None:
    first_sent, second_started, second_sent = threading.Event(), threading.Event(), threading.Event()
    first_payload = b""

    def respond(message: bytes) -> list[bytes]:
        nonlocal first_payload
        packet = sinco.decode_sysex(message)
        address = int.from_bytes(packet.payload[1:5], "little")
        if address == 10:
            first_payload = packet.payload
            first_sent.set()
            return []  # A deliberately pending response while another client starts a read.
        second_sent.set()
        return [sinco.encode_sysex(packet.cmd, packet.payload + b"\x14")]

    private = FakePort(respond)
    with SmcMixer(FakePort(), private) as mixer, ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(mixer.read_memory, 4, 10, 1)
        assert first_sent.wait(1)

        def read_second() -> bytes:
            second_started.set()
            return mixer.read_memory(4, 20, 1)

        second = pool.submit(read_second)
        assert second_started.wait(1)
        assert not second_sent.wait(0.05)
        private.emit(sinco.encode_sysex(sinco.CMD_READ, first_payload + b"\x0a"))
        assert first.result(timeout=2) == b"\x0a"
        assert second.result(timeout=2) == b"\x14"
