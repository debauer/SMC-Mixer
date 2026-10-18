"""Monitor behavior and read-only MIDI integration, without hardware."""

from unittest.mock import Mock

import pytest

from fakes import FADER_VALUES, FakePort, device_responder
from smc_mixer import DeviceMode, MidiProtocol, ModeEvent, UserKnobsEvent, sinco
from smc_mixer.controls import Button
from smc_mixer.events import ButtonEvent, ConnectionEvent, FaderEvent, KnobEvent, Layer, UnknownEvent
from smc_mixer.mixer import SmcMixer
from smc_mixer_tools.cli import main
from smc_mixer_tools.monitor.app import HistoryHandler, demo_step, draw, run_monitor, run_screen
from smc_mixer_tools.monitor.state import MonitorState
from smc_mixer_tools.monitor.view import WIDTH, render


@pytest.fixture(autouse=True)
def monochrome_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("smc_mixer_tools.monitor.app.init_colors", lambda: {})


def test_event_state_and_disconnect() -> None:
    state = MonitorState()
    state.apply(ButtonEvent(Button.MUTE_1, True, Layer.DAW))
    assert Button.MUTE_1 in state.pressed
    state.apply(ButtonEvent(Button.MUTE_1, False, Layer.CC))
    assert not state.pressed
    assert state.protocol == MidiProtocol.CC
    state.apply(FaderEvent(8, 127, Layer.DAW))
    state.apply(KnobEvent(3, Layer.DAW, delta=-1))
    assert state.faders[7] == 127
    assert state.knobs[2].delta == -1
    state.apply(ButtonEvent(Button.RECORD, True, Layer.DAW))
    state.apply(ConnectionEvent(False))
    assert not state.connected
    assert not state.pressed
    assert state.knobs == [None] * 8
    assert state.protocol is None
    assert state.faders[7] == 127
    assert len(state.history) == 4
    state.apply(ConnectionEvent(True))
    assert state.connected


def test_render_positions_buttons_and_unknowns() -> None:
    state = MonitorState()
    state.apply(FaderEvent(1, 127, Layer.DAW))
    state.apply(FaderEvent(2, 0, Layer.DAW))
    state.apply(ButtonEvent(Button.MUTE_1, True, Layer.DAW))
    state.apply(KnobEvent(1, Layer.DAW, delta=-1))
    state.apply(KnobEvent(2, Layer.CC, value=64))
    lines = render(state)
    assert all(len(line) == WIDTH for line in lines)
    assert "█ * M *" in lines[9]
    assert "█" not in lines[10]
    assert "█" in lines[18]
    assert "127" in lines[19] and "000" in lines[19] and "---" in lines[19]
    assert "-01" in lines[7] and "064" in lines[7]
    assert "LEDs: unknown" in "\n".join(lines)
    state.apply(UnknownEvent(bytes.fromhex("b0 7f 01")))
    assert "b0 7f 01" in state.history[0]


@pytest.mark.parametrize(
    ("event", "indicator"),
    [
        (None, "?"),
        (KnobEvent(1, Layer.DAW, delta=1), "↻"),
        (KnobEvent(1, Layer.DAW, delta=-1), "↺"),
        (KnobEvent(1, Layer.CC, value=0), "↙"),
        (KnobEvent(1, Layer.CC, value=64), "↑"),
        (KnobEvent(1, Layer.CC, value=127), "↘"),
    ],
)
def test_knob_face_shows_position_or_relative_direction(event: KnobEvent | None, indicator: str) -> None:
    state = MonitorState()
    if event is not None:
        state.apply(event)
    lines = render(state)
    assert lines[5].startswith(f"┃   ┃  {indicator}  ┃")
    assert all(len(line) == WIDTH for line in lines)


def test_small_terminal_and_resize() -> None:
    screen = Mock()
    screen.getmaxyx.return_value = (10, 40)
    draw(screen, MonitorState())
    assert "Terminal too small" in screen.addnstr.call_args_list[0].args[2]
    screen.reset_mock()
    screen.getmaxyx.return_value = (50, 120)
    draw(screen, MonitorState())
    assert screen.addnstr.call_count == len(render(MonitorState()))


def test_demo_releases_keys() -> None:
    state = MonitorState(demo=True)
    for step in range(64):
        demo_step(state, step)
        assert all(value is not None and 0 <= value <= 127 for value in state.faders)
        assert len(state.pressed) == (1 if step % 2 == 0 else 0)


@pytest.mark.parametrize("firmware", ["SMC-Mixer_011", "SMC-Mixer_013"])
@pytest.mark.parametrize("user", [False, True])
def test_monitor_only_reads_device(monkeypatch: pytest.MonkeyPatch, firmware: str, user: bool) -> None:
    master = FakePort()
    responder = device_responder(firmware)
    values = [0, 16, 32, 64, 80, 96, 112, 127]

    def respond(message: bytes) -> list[bytes]:
        request = sinco.decode_sysex(message)
        if request.cmd == sinco.CMD_READ:
            address = int.from_bytes(request.payload[1:5], "little")
            if address in (0, 0x246):
                data = bytes([user]) if address == 0 else b"".join(value.to_bytes(2, "little") for value in values)
                return [sinco.encode_sysex(request.cmd, request.payload + data)]
        return responder(message)

    private = FakePort(respond)
    mixer = SmcMixer(master, private)
    monkeypatch.setattr(SmcMixer, "open", lambda: mixer)
    observed = []

    def wrapper(callback, state, device, logs):
        observed.append(state)
        master.emit(bytes([0x90, int(Button.PLAY), 127]))
        screen = Mock()
        screen.getmaxyx.return_value = (50, 120)
        screen.getch.return_value = ord("q")
        callback(screen, state, device, logs)

    monkeypatch.setattr("smc_mixer_tools.monitor.app.curses.wrapper", wrapper)
    run_monitor()
    assert not master.sent  # No LED notes, pitch bends or other master-port writes.
    assert all(
        sinco.decode_sysex(message).cmd in (sinco.CMD_QUERY_NAME_VER, sinco.CMD_READ) for message in private.sent
    )
    assert master.closed and private.closed
    assert Button.PLAY in observed[0].pressed
    if firmware.endswith("011"):
        assert observed[0].faders == FADER_VALUES
        assert observed[0].mode == (DeviceMode.USER if user else DeviceMode.DAW)
        if user:
            assert [event.value for event in observed[0].knobs] == values
        else:
            assert observed[0].knobs == [None] * 8
    else:
        assert observed[0].mode is None
        assert observed[0].faders == [None] * 8
        assert any("Initial values unavailable" in entry for entry in observed[0].history)


def test_reconnect_takes_new_fader_snapshot() -> None:
    mixer = Mock()
    mixer.connected = True
    mixer.mode = DeviceMode.USER
    mixer.user_knobs = [32] * 8
    mixer.events.return_value = iter(
        [
            ConnectionEvent(False),
            ConnectionEvent(True),
            ModeEvent(DeviceMode.USER),
            UserKnobsEvent((32,) * 8),
        ]
    )
    mixer.faders = FADER_VALUES
    screen = Mock()
    screen.getmaxyx.return_value = (50, 120)
    screen.getch.return_value = ord("q")
    state = MonitorState()
    run_screen(screen, state, mixer, HistoryHandler())
    assert state.connected
    assert state.faders == FADER_VALUES
    assert state.mode == DeviceMode.USER
    assert [event.value for event in state.knobs] == [32] * 8


def test_mode_change_clears_stale_controls_and_keeps_physical_faders() -> None:
    state = MonitorState()
    state.set_mode(DeviceMode.DAW)
    state.apply(FaderEvent(1, 72, Layer.DAW))
    state.apply(ButtonEvent(Button.MUTE_1, True, Layer.DAW))
    state.apply(KnobEvent(1, Layer.DAW, delta=-1))
    state.set_mode(DeviceMode.USER)
    assert not state.pressed
    assert state.knobs == [None] * 8
    assert state.protocol is None
    assert state.faders[0] == 72
    state.apply(KnobEvent(1, Layer.CC, value=80))
    text = "\n".join(render(state))
    assert "MODE: USER" in text
    assert "LAST MIDI: CC" in text
    assert "080" in text
    assert "SHIFT" not in state.history[0]
    state.apply(ConnectionEvent(False))
    assert state.mode is None


def test_demo_does_not_open_midi(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = Mock(side_effect=AssertionError("Demo must not open MIDI ports"))
    monkeypatch.setattr(SmcMixer, "open", opener)
    screen = Mock()
    screen.getmaxyx.return_value = (50, 120)
    screen.getch.return_value = ord("q")
    monkeypatch.setattr("smc_mixer_tools.monitor.app.curses.wrapper", lambda callback, *args: callback(screen, *args))
    run_monitor(demo=True)
    opener.assert_not_called()


def test_cli_requires_terminal(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.argv", ["smc-mixer", "monitor"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit, match="1"):
        main()
    assert "interactive terminal" in capsys.readouterr().err
