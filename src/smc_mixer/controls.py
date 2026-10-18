"""Device modes, MIDI protocols, controls and their verified default mappings."""

from __future__ import annotations

from enum import Enum, IntEnum


class DeviceMode(Enum):
    DAW = "DAW"
    USER = "USER"


class MidiProtocol(Enum):
    MCU = "mcu"
    CC = "cc"


class Button(IntEnum):
    """Physical buttons; values are DAW/MCU note numbers (also the LED notes)."""

    REC_1, REC_2, REC_3, REC_4, REC_5, REC_6, REC_7, REC_8 = range(0, 8)
    SOLO_1, SOLO_2, SOLO_3, SOLO_4, SOLO_5, SOLO_6, SOLO_7, SOLO_8 = range(8, 16)
    MUTE_1, MUTE_2, MUTE_3, MUTE_4, MUTE_5, MUTE_6, MUTE_7, MUTE_8 = range(16, 24)
    SELECT_1, SELECT_2, SELECT_3, SELECT_4, SELECT_5, SELECT_6, SELECT_7, SELECT_8 = range(24, 32)
    BANK_LEFT = 46
    BANK_RIGHT = 47
    REWIND = 91
    FAST_FORWARD = 92
    STOP = 93  # labelled with a pause symbol on the device
    PLAY = 94
    RECORD = 95
    UP = 96
    DOWN = 97
    LEFT = 98
    RIGHT = 99


class LedState(IntEnum):
    """Note-on velocity for button LEDs."""

    OFF = 0
    BLINK = 1
    ON = 127


def strip_button(row: str, channel: int) -> Button:
    """Button of a channel strip, e.g. strip_button("MUTE", 3) -> Button.MUTE_3."""
    if not 1 <= channel <= 8:
        raise ValueError(f"channel must be 1-8, got {channel}")
    return Button[f"{row.upper()}_{channel}"]


# Buttons with an LED that follows the button's note (verified LED sweep).
LED_BUTTONS = frozenset(b for b in Button if b < 32) | {
    Button.REWIND,
    Button.FAST_FORWARD,
    Button.STOP,
    Button.PLAY,
    Button.RECORD,
}

# USER default CC mapping. Shared CCs need heuristics at 0/127; this is not a Shift indication.
USER_BUTTON_CC: dict[Button, int] = {
    **{Button(Button.MUTE_1 + i): 20 + i for i in range(8)},
    **{Button(Button.SOLO_1 + i): 28 + i for i in range(8)},
    **{Button(Button.REC_1 + i): 36 + i for i in range(8)},
    **{Button(Button.SELECT_1 + i): 44 + i for i in range(8)},
    Button.PLAY: 52,
    Button.STOP: 53,
    Button.RECORD: 54,
    Button.REWIND: 55,
    Button.FAST_FORWARD: 56,
    Button.UP: 57,
    Button.DOWN: 58,
    Button.LEFT: 59,
    Button.RIGHT: 60,
}
USER_CC_BUTTON: dict[int, Button] = {cc: b for b, cc in USER_BUTTON_CC.items()}

# Historical names retained for imports; the Shift behavior has not been independently verified.
SHIFT_BUTTON_CC = USER_BUTTON_CC
SHIFT_CC_BUTTON = USER_CC_BUTTON
