"""Mackie Control (MCU) names for diagnostic output."""

from __future__ import annotations

_GROUPS = {0: "REC", 8: "SOLO", 16: "MUTE", 24: "SELECT", 32: "V-POT PUSH", 104: "FADER TOUCH"}

_SINGLE = {
    40: "ASSIGN TRACK",
    41: "ASSIGN SEND",
    42: "ASSIGN PAN",
    43: "ASSIGN PLUG-IN",
    44: "ASSIGN EQ",
    45: "ASSIGN INSTRUMENT",
    46: "BANK LEFT",
    47: "BANK RIGHT",
    48: "CHANNEL LEFT",
    49: "CHANNEL RIGHT",
    50: "FLIP",
    51: "GLOBAL VIEW",
    52: "NAME/VALUE",
    53: "SMPTE/BEATS",
    62: "MIDI TRACKS",
    63: "INPUTS",
    64: "AUDIO TRACKS",
    65: "AUDIO INSTRUMENT",
    66: "AUX",
    67: "BUSSES",
    68: "OUTPUTS",
    69: "USER",
    70: "SHIFT",
    71: "OPTION",
    72: "CONTROL",
    73: "ALT",
    74: "READ/OFF",
    75: "WRITE",
    76: "TRIM",
    77: "TOUCH",
    78: "LATCH",
    79: "GROUP",
    80: "SAVE",
    81: "UNDO",
    82: "CANCEL",
    83: "ENTER",
    84: "MARKER",
    85: "NUDGE",
    86: "CYCLE",
    87: "DROP",
    88: "REPLACE",
    89: "CLICK",
    90: "SOLO (global)",
    91: "REWIND",
    92: "FAST FWD",
    93: "STOP",
    94: "PLAY",
    95: "RECORD",
    96: "CURSOR UP",
    97: "CURSOR DOWN",
    98: "CURSOR LEFT",
    99: "CURSOR RIGHT",
    100: "ZOOM",
    101: "SCRUB",
    102: "USER A",
    103: "USER B",
    112: "FADER TOUCH MASTER",
    113: "SMPTE LED",
    114: "BEATS LED",
    115: "RUDE SOLO LED",
    118: "RELAY",
}


def note_name(note: int) -> str:
    for base, label in _GROUPS.items():
        if base <= note < base + 8:
            return f"{label} {note - base + 1}"
    if 54 <= note <= 61:
        return f"F{note - 53}"
    return _SINGLE.get(note, "-")


def cc_name(cc: int) -> str:
    if 16 <= cc <= 23:
        return f"V-POT {cc - 15} (Turn)"
    if 48 <= cc <= 55:
        return f"V-POT {cc - 47} (LED ring)"
    if cc == 60:
        return "JOG WHEEL"
    return "-"
