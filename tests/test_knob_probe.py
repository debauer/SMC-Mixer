"""Guided measurement and candidate rejection against a simulated mixer."""

import json
from io import StringIO
from pathlib import Path

import pytest

from fakes import FakePort
from smc_mixer import sinco
from smc_mixer_tools.probes.knob_analysis import Sample, summarize
from smc_mixer_tools.probes.knobs import Session, main
from smc_mixer_tools.probes.recording import Recorder


@pytest.mark.parametrize("shift", [False, True])
@pytest.mark.parametrize("value", [0, 1, 16, 64, 65, 80, 127])
def test_recorder_preserves_raw_cc_values(shift: bool, value: int, capsys: pytest.CaptureFixture[str]) -> None:
    stream = StringIO()
    recorder = Recorder(stream)
    recorder.begin("knob_1", (1, shift))
    recorder.raw(bytes([0xB4, 30, value]))
    assert recorder.last_value() == value
    assert recorder.source() == (5, 30)
    assert f"absolute {value:03d}" in capsys.readouterr().out
    assert json.loads(stream.getvalue())["hex"] == bytes([0xB4, 30, value]).hex(" ")


def test_multiple_cc_sources_are_not_silently_assigned_to_a_knob() -> None:
    recorder = Recorder(StringIO())
    recorder.begin("no_shift_1", (1, False))
    recorder.raw(bytes([0xB0, 30, 50]))
    recorder.raw(bytes([0xB0, 31, 42]))
    assert recorder.last_value() is None
    assert recorder.source() is None
    recorder.begin("no_shift_1", (1, False))
    recorder.raw(bytes([0xB0, 30, 60]))
    assert recorder.last_value() == 60


def test_candidate_requires_persistence_and_excludes_noise_and_faders() -> None:
    def sample(name: str, value: int, *, knob: int | None = None, transient: int = 0) -> Sample:
        data = bytearray(0x80)
        data[0] = value  # True knob value.
        data[1] = transient  # Last-event buffer.
        data[2] = value  # Noisy at baseline, should be excluded.
        data[0x27] = value  # Known fader value at 0x327.
        return Sample(name, bytes(data), bytes(data), knob, value if knob else None)

    baseline = sample("initial", 0)
    baseline.second = b"\x00\x00\x01" + baseline.second[3:]
    samples = [
        baseline,
        sample("low", 17, knob=1, transient=17),
        sample("high", 83, knob=1, transient=83),
        sample("other_knob", 83, transient=44),
    ]
    report = summarize(samples, 0x300)
    assert "Knob 1: 0x0300\n" in report
    assert "1 unstable baseline bytes" in report


def setup_device(monkeypatch: pytest.MonkeyPatch) -> tuple[FakePort, FakePort, bytearray]:
    ram = bytearray(0x400)
    ram[0] = 1  # Verified USER mode bit.

    def responder(message: bytes) -> list[bytes]:
        request = sinco.decode_sysex(message)
        if request.cmd == sinco.CMD_QUERY_NAME_VER:
            return [sinco.encode_sysex(request.cmd, b"SMC-Mixer_011")]
        assert request.cmd == sinco.CMD_READ  # No configuration writes or reset commands.
        assert request.payload[0] == 4
        address = int.from_bytes(request.payload[1:5], "little")
        length = int.from_bytes(request.payload[5:8], "little")
        assert length <= 32
        return [sinco.encode_sysex(request.cmd, request.payload + ram[address : address + length])]

    master, private = FakePort(), FakePort(responder)
    monkeypatch.setattr("smc_mixer_tools.probes.knobs.RtMidiPort", lambda name: master if "Master" in name else private)
    monkeypatch.setattr("smc_mixer_tools.probes.knobs.time.sleep", lambda _: None)
    return master, private, ram


@pytest.mark.parametrize("first_knob_cc", [16, 30])
@pytest.mark.parametrize("knob_count", [2, 8])
def test_guided_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_knob_cc: int, knob_count: int) -> None:
    master, private, ram = setup_device(monkeypatch)
    output = tmp_path / "session"
    monkeypatch.setattr(
        "sys.argv", ["probe-knobs", "--out", str(output)] + (["--all-knobs"] if knob_count == 8 else [])
    )
    actions = iter(
        [(None, None, None)]
        + [
            action
            for knob in range(knob_count)
            for action in (
                (0xB4 if knob == 0 else 0xB0, first_knob_cc if knob == 0 else 30 + knob, 16 + knob * 3),
                (0xB4 if knob == 0 else 0xB0, first_knob_cc if knob == 0 else 30 + knob, 80 + knob * 3),
            )
        ]
        + ([(0xB4, first_knob_cc, 83)] if knob_count == 2 else [])  # Recheck must differ from knob 2's last value.
        + [(0xB4, first_knob_cc, 48)]
    )

    def user_input(prompt: str) -> str:
        status, cc, value = next(actions)
        if cc is not None:
            knob_index = 0 if cc == 16 else cc - 30
            ram[0x100 + knob_index] = value
            ram[0x120] = value  # Shared send buffer must not survive candidate filtering.
            master.emit(bytes([status, cc, value]))
        return ""

    monkeypatch.setattr("builtins.input", user_input)
    assert main() == 0
    assert master.closed and private.closed
    assert master.sent == []
    metadata = json.loads((output / "samples.json").read_text())
    assert len(metadata) == 2 * knob_count + 3  # Initial, first/second per knob, recheck, idle.
    assert all(sample["mode"] == "USER" for sample in metadata)
    assert metadata[1]["name"] == "user_1_first"
    assert metadata[1]["value"] == 16
    assert metadata[1]["source"] == [5, first_knob_cc]
    assert metadata[-2]["name"] == "user_1_recheck" and metadata[-2]["value"] == 48
    report = (output / "report.txt").read_text()
    for knob in range(1, knob_count + 1):
        assert f"Knob {knob}: 0x{0xFF + knob:04X}\n" in report
    if knob_count == 2:
        assert "Knob 3: not measured" in report
    assert len(list(output.glob("*.bin"))) == (2 * knob_count + 3) * 2
    session = json.loads((output / "session.json").read_text())
    assert session["address"] == 0
    assert session["knobs"] == list(range(1, knob_count + 1))
    midi = [json.loads(line) for line in (output / "midi.jsonl").read_text().splitlines()]
    assert any(entry["phase"] == f"user_{knob_count}_second" for entry in midi)


def test_abort_keeps_finished_measurements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    master, private, _ = setup_device(monkeypatch)
    output = tmp_path / "partial"
    monkeypatch.setattr("sys.argv", ["probe-knobs", "--out", str(output)])
    responses = iter(["", KeyboardInterrupt()])

    def user_input(prompt: str) -> str:
        answer = next(responses)
        if isinstance(answer, KeyboardInterrupt):
            raise answer
        return answer

    monkeypatch.setattr("builtins.input", user_input)
    assert main() == 130
    assert (output / "initial_a.bin").exists()
    assert (output / "report.txt").exists()
    assert master.closed and private.closed


def test_existing_output_is_not_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["probe-knobs", "--out", str(tmp_path)])
    assert main() == 1


def test_initial_only_needs_no_knob_movement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    master, private, ram = setup_device(monkeypatch)
    ram[0x100] = 87
    output = tmp_path / "initial"
    monkeypatch.setattr("sys.argv", ["probe-knobs", "--out", str(output), "--initial-only"])
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert main() == 0
    metadata = json.loads((output / "samples.json").read_text())
    assert [sample["name"] for sample in metadata] == ["initial"]
    assert (output / "initial_a.bin").read_bytes()[0x100] == 87
    assert master.closed and private.closed
    assert not master.sent


def test_initial_daw_mode_requires_switch_to_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, ram = setup_device(monkeypatch)
    ram[0] = 0
    output = tmp_path / "mode_check"
    monkeypatch.setattr("sys.argv", ["probe-knobs", "--out", str(output), "--initial-only"])
    prompts = 0

    def user_input(prompt: str) -> str:
        nonlocal prompts
        prompts += 1
        if prompts == 2:
            ram[0] = 1
        return ""

    monkeypatch.setattr("builtins.input", user_input)
    assert main() == 0
    assert prompts == 2


def test_mode_change_aborts_without_accepting_daw_as_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    master, private, ram = setup_device(monkeypatch)
    output = tmp_path / "mode_changed"
    monkeypatch.setattr("sys.argv", ["probe-knobs", "--out", str(output)])
    prompts = 0

    def user_input(prompt: str) -> str:
        nonlocal prompts
        prompts += 1
        if prompts == 2:
            ram[0] = 0
            master.emit(bytes([0xB0, 16, 65]))
        return ""

    monkeypatch.setattr("builtins.input", user_input)
    assert main() == 1
    metadata = json.loads((output / "samples.json").read_text())
    assert [sample["name"] for sample in metadata] == ["initial"]
    assert master.closed and private.closed


def test_mode_change_during_reads_rejects_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, ram = setup_device(monkeypatch)
    output = tmp_path / "mid_read_change"
    monkeypatch.setattr("sys.argv", ["probe-knobs", "--out", str(output), "--initial-only"])
    monkeypatch.setattr("builtins.input", lambda _: "")
    read = Session.read

    def read_and_switch(session: Session) -> bytes:
        data = read(session)
        ram[0] = 0
        return data

    monkeypatch.setattr(Session, "read", read_and_switch)
    assert main() == 1
    assert not (output / "samples.json").exists()
