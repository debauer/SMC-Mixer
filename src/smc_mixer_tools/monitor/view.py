"""Fixed-width Unicode front panel for the SMC-Mixer."""

from smc_mixer.controls import Button, strip_button
from smc_mixer.events import KnobEvent
from smc_mixer_tools.monitor.state import MonitorState

WIDTH = 104
FADER_ROWS = 10


def button(label: str, control: Button, state: MonitorState) -> str:
    return f"*{label}*" if control in state.pressed else f"[{label}]"


def knob_text(event: KnobEvent | None) -> str:
    if event is None:
        return "--"
    if event.delta is not None:
        return f"{event.delta:+03d}"
    return f"{event.value:03d}"


def knob_indicator(event: KnobEvent | None) -> str:
    match event:
        case KnobEvent(delta=int(delta)):
            return "↻" if delta > 0 else "↺"
        case KnobEvent(value=int(value)):
            return "↙←↖↑↗→↘"[(value * 6 + 63) // 127]
        case _:
            return "?"


def render(state: MonitorState) -> list[str]:
    """Return equally wide lines, including the frame."""

    # The heavy box and block characters are intentional: this is a Unicode TUI.
    def line(text: str = "") -> str:
        return "┃" + text[: WIDTH - 2].ljust(WIDTH - 2) + "┃"

    def channels(cells: list[str], side: str = "") -> str:
        return line("  " + "".join(cell.ljust(11) for cell in cells) + side)

    separator = "┣" + "━" * (WIDTH - 2) + "┫"
    connection = "DEMO" if state.demo else "ONLINE" if state.connected else "OFFLINE"
    mode = state.mode.value if state.mode is not None else "--"
    protocol = state.protocol.value.upper() if state.protocol is not None else "--"
    lines = [
        "┏" + "━" * (WIDTH - 2) + "┓",
        line(f" M-VAVE SMC-MIXER                 USB: {connection:7s}   MODE: {mode:4s}   LAST MIDI: {protocol}"),
        line(),
        channels([f"  CH {i}" for i in range(1, 9)]),
        channels([" ┏━━━━━┓"] * 8, "( BT  )"),
        channels([f" ┃  {knob_indicator(event)}  ┃" for event in state.knobs]),
        channels([" ┗━━━━━┛"] * 8, "(SHIFT)"),
        channels([f"   {knob_text(event)}" for event in state.knobs]),
        line(),
    ]
    rows = {0: ("M", "MUTE"), 2: ("S", "SOLO"), 4: ("R", "REC"), 6: ("SEL", "SELECT")}
    for row in range(FADER_ROWS):
        cells = []
        for channel, value in enumerate(state.faders, 1):
            position = None if value is None else (127 - value) * (FADER_ROWS - 1) // 127
            track = "█" if row == position else "┃"
            key = ""
            if row in rows:
                label, name = rows[row]
                key = button(label.center(3), strip_button(name, channel), state)
            cells.append(f"  {track} {key}")
        lines.append(channels(cells))
    lines.extend(
        [
            channels([f"  {value:03d}" if value is not None else "  ---" for value in state.faders]),
            line(),
            separator,
            line(),
            line(
                "  "
                + " ".join(
                    button(label, control, state)
                    for label, control in (
                        (" > ", Button.PLAY),
                        ("||", Button.STOP),
                        ("REC", Button.RECORD),
                        ("<<", Button.REWIND),
                        (">>", Button.FAST_FORWARD),
                        ("BANK <", Button.BANK_LEFT),
                        ("BANK >", Button.BANK_RIGHT),
                        ("^", Button.UP),
                        ("v", Button.DOWN),
                        ("<", Button.LEFT),
                        (">", Button.RIGHT),
                    )
                )
            ),
            line(),
            separator,
            *[line(" " + entry) for entry in list(state.history) + [""] * (4 - len(state.history))],
            line(" *Button* = pressed | Knob: relative / absolute | Shift: unknown | LEDs: unknown | q / Ctrl+C"),
            "┗" + "━" * (WIDTH - 2) + "┛",
        ]
    )
    return lines
