"""Fixed values of the SMC-Mixer, verified on the device (see docs/control.md)."""

from __future__ import annotations

# Substrings of the MIDI port names as reported by ALSA ("SINCO SMC-Mixer-Master" etc.).
MASTER_PORT_NAME = "SMC-Mixer-Master"  # Mackie Control: buttons, knobs, faders, LEDs
PRIVATE_PORT_NAME = "SMC-Mixer-Private"  # Sinco configuration protocol (SysEx)

CHANNELS = 8
NOTE_ALL_MUTE_LEDS = 115  # lights all MUTE LEDs as a separate layer (MCU "rude solo")

# DAW layer (no Shift)
KNOB_CC_FIRST = 16  # CC 16-23, relative: 1..15 right, 65..79 left

# USER default CC protocol: absolute 0-127. This does not imply a held Shift button.
USER_KNOB_CC_FIRST = 30
USER_FADER_CC_FIRST = 40
# Continuous controls change by small steps; a jump larger than this to 0/127 on a
# CC shared with a button is treated as a button press/release.
CC_BUTTON_JUMP = 24

# Historical names retained for existing imports.
SHIFT_KNOB_CC_FIRST = USER_KNOB_CC_FIRST
SHIFT_FADER_CC_FIRST = USER_FADER_CC_FIRST
SHIFT_BUTTON_JUMP = CC_BUTTON_JUMP

STATE_FIRMWARE = "SMC-Mixer_011"
STATE_MEMORY_TYPE = 4
MODE_ADDRESS = 0
MODE_MASK = 1
USER_KNOB_ADDRESS = 0x246
USER_KNOB_ENTRY = 2

# Live fader table in RAM (Sinco read type 4), verified on this firmware only:
# 8 entries of 3 bytes, the middle byte is the 7-bit fader position.
FADER_TABLE_FIRMWARE = STATE_FIRMWARE
FADER_TABLE_TYPE = 0x04
FADER_TABLE_ADDR = 0x326
FADER_TABLE_ENTRY = 3

DEFAULT_TIMEOUT = 1.0
RESTORE_ATTEMPTS = 3  # fader read attempts after a reconnect (the device may still be booting)
