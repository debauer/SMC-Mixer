"""Mode-bit detection, protocol verification and a complete simulated measurement."""

import json
from io import StringIO
from pathlib import Path

import pytest

from fakes import FakePort
from smc_mixer import sinco
from smc_mixer_tools.probes.mode import ModeRecorder, main
from smc_mixer_tools.probes.mode_analysis import ModeSample, candidates


def samples() -> list[ModeSample]:
    result = []
    for stage, mode in enumerate(("DAW", "USER", "DAW", "USER"), 1):
        for phase in ("before", "after", "idle"):
            value = 0xA0 | (8 if mode == "USER" else 0)
            # Bit 0 is noisy in the very same byte; bit 3 should still be found.
            # Byte 1 imitates a mode-dependent last-message buffer, not persistent mode state.
            transient = 8 if mode == "USER" and phase != "before" else 0
            result.append(
                ModeSample(
                    f"{stage}_{phase}", mode, stage, bytes([value, transient]), bytes([value | 1, transient]), True
                )
            )
    return result


def test_finds_stable_bit_despite_adjacent_noise_and_rejects_send_buffer() -> None:
    assert candidates(samples()) == [(0, 8, 0, 8)]
    inverted = samples()
    for sample in inverted:
        sample.first = bytes([sample.first[0] ^ 8, sample.first[1]])
        sample.second = bytes([sample.second[0] ^ 8, sample.second[1]])
    assert candidates(inverted) == [(0, 8, 8, 0)]


def test_requires_two_complete_verified_stages_per_mode() -> None:
    data = samples()
    assert candidates(data[:-1]) == []
    data[-1].verified = False
    assert candidates(data) == []
    assert candidates([]) == []


def test_unverified_attempt_is_excluded() -> None:
    data = samples()
    data.append(ModeSample("wrong_before", "DAW", 1, bytes([8, 0]), bytes([8, 0]), False))
    assert candidates(data) == [(0, 8, 0, 8)]


def test_protocol_confirmation_requires_press_and_release() -> None:
    recorder = ModeRecorder(StringIO())
    recorder.begin("1_daw_verify")
    recorder.raw(bytes([0x90, 16, 127]))
    assert not recorder.matches("DAW")
    recorder.raw(bytes([0x80, 16, 0]))
    assert recorder.matches("DAW")
    assert not recorder.matches("USER")
    recorder.begin("2_user_verify")
    recorder.raw(bytes([0xB0, 20, 127]))
    recorder.raw(bytes([0xB0, 20, 0]))
    assert recorder.matches("USER")
    recorder.raw(bytes([0x90, 16, 127]))
    assert not recorder.matches("USER")


def device(monkeypatch: pytest.MonkeyPatch) -> tuple[FakePort, FakePort, bytearray]:
    ram = bytearray(0x400)

    def respond(message: bytes) -> list[bytes]:
        request = sinco.decode_sysex(message)
        if request.cmd == sinco.CMD_QUERY_NAME_VER:
            return [sinco.encode_sysex(request.cmd, b"SMC-Mixer_011")]
        assert request.cmd == sinco.CMD_READ
        assert request.payload[0] == 4
        address = int.from_bytes(request.payload[1:5], "little")
        length = int.from_bytes(request.payload[5:8], "little")
        assert length <= 32
        if address <= 0x2E0 < address + length:
            ram[0x2E0] ^= 1
        return [sinco.encode_sysex(request.cmd, request.payload + ram[address : address + length])]

    master, private = FakePort(), FakePort(respond)
    monkeypatch.setattr("smc_mixer_tools.probes.mode.RtMidiPort", lambda name: master if "Master" in name else private)
    monkeypatch.setattr("smc_mixer_tools.probes.mode.time.sleep", lambda _: None)
    return master, private, ram


def test_guided_session_only_reads_and_reports_mode_bit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    master, private, ram = device(monkeypatch)
    output = tmp_path / "measurement"
    monkeypatch.setattr("sys.argv", ["probe-mode", "--out", str(output)])
    calls = 0

    def user_input(prompt: str) -> str:
        nonlocal calls
        mode = ("DAW", "USER", "DAW", "USER")[calls // 2]
        if calls % 2 == 0:
            ram[0x2E0] = 0xA0 | (8 if mode == "USER" else 0)
            ram[0x2E1] = 0
        elif mode == "DAW":
            master.emit(bytes([0x90, 16, 127]))
            master.emit(bytes([0x90, 16, 0]))
        else:
            master.emit(bytes([0xB0, 20, 127]))
            master.emit(bytes([0xB0, 20, 0]))
            ram[0x2E1] = 8  # Last-message buffer, invalid before the press.
        calls += 1
        return ""

    monkeypatch.setattr("builtins.input", user_input)
    assert main() == 0
    assert calls == 8
    assert master.sent == []
    assert master.closed and private.closed
    text = (output / "report.txt").read_text()
    assert "0x02E0   0x08   0x00    0x08" in text
    assert "0x02E1" not in text
    metadata = json.loads((output / "samples.json").read_text())
    assert len(metadata) == 12 and all(sample["verified"] for sample in metadata)
    assert len(list(output.glob("*.bin"))) == 24
    assert len((output / "midi.jsonl").read_text().splitlines()) == 8


def test_abort_saves_unconfirmed_measurement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    master, private, _ = device(monkeypatch)
    output = tmp_path / "partial"
    monkeypatch.setattr("sys.argv", ["probe-mode", "--out", str(output)])
    calls = 0

    def user_input(prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise KeyboardInterrupt
        return ""

    monkeypatch.setattr("builtins.input", user_input)
    assert main() == 130
    metadata = json.loads((output / "samples.json").read_text())
    assert len(metadata) == 1 and not metadata[0]["verified"]
    assert "No reliable bit candidates" in (output / "report.txt").read_text()
    assert master.closed and private.closed


def test_wrong_protocol_repeats_stage_without_confirming_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    master, _, _ = device(monkeypatch)
    output = tmp_path / "wrong_mode"
    monkeypatch.setattr("sys.argv", ["probe-mode", "--out", str(output)])
    calls = 0

    def user_input(prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            master.emit(bytes([0xB0, 20, 127]))  # USER while DAW was requested.
            master.emit(bytes([0xB0, 20, 0]))
        if calls == 3:
            assert "[1/4]" in prompt
            raise KeyboardInterrupt
        return ""

    monkeypatch.setattr("builtins.input", user_input)
    assert main() == 130
    assert "DAW mode was not confirmed unambiguously" in capsys.readouterr().out
    metadata = json.loads((output / "samples.json").read_text())
    assert len(metadata) == 1 and not metadata[0]["verified"]
