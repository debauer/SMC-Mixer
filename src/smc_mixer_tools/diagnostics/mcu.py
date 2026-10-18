#!/usr/bin/env python3
"""Map and drive buttons and LEDs of the SMC-Mixer on the "Master" port (Mackie Control).

buttons             listen and print every button/knob/fader event with its MCU name
leds                interactive sweep: light each note LED, ask what lit, write a table
led --note N        switch a single LED (velocity 127 on, 1 blink, 0 off)
ring --knob K ...   set a V-Pot LED ring via CC 48-55
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time

from smc_mixer_tools.diagnostics.amidi import (
    EXIT_ABORT,
    EXIT_OK,
    EXIT_RUNTIME,
    EXIT_USAGE,
    Listener,
    ProbeError,
    find_ports,
    send,
)
from smc_mixer_tools.diagnostics.mcu_names import cc_name, note_name
from smc_mixer_tools.diagnostics.sinco import read_faders


def describe(msg: bytes) -> str:
    status, kind, ch = msg[0], msg[0] & 0xF0, (msg[0] & 0x0F) + 1
    if kind in (0x80, 0x90) and len(msg) == 3:
        state = "on" if kind == 0x90 and msg[2] else "off"
        return f"Note {msg[1]:3d} {state:3}  {note_name(msg[1])}"
    if kind == 0xB0 and len(msg) == 3:
        cc, val = msg[1], msg[2]
        # MCU V-Pot: relative steps, 1..15 right, 65..79 left. In the CC protocol
        # In USER mode CC 20-23 are MUTE buttons (127/0), so only MCU step ranges count as turns.
        if 16 <= cc <= 23 and (1 <= val <= 15 or 65 <= val <= 79):
            step = f"+{val}" if val < 64 else f"-{val - 64}"
            return f"CC {cc:3d} = {val:3d} ({step})  {cc_name(cc)}"
        return f"CC {cc:3d} = {val:3d} (channel {ch})"
    if kind == 0xE0 and len(msg) == 3:
        raw = msg[1] | (msg[2] << 7)
        return f"Pitch Bend channel {ch} = {raw:5d}  FADER {ch}"
    return f"{msg.hex(' ').upper()} (status {status:02X})"


def cmd_buttons(hw: str, seconds: float) -> None:
    listener = Listener("master", hw)
    print("Lausche auf Port Master, Strg+C beendet.", file=sys.stderr)
    end = time.monotonic() + seconds if seconds > 0 else None
    try:
        while end is None or time.monotonic() < end:
            if not listener.alive():
                raise ProbeError(f"amidi beendet: {listener.stderr()}")
            for msg in listener.take():
                print(describe(msg.data), flush=True)
            time.sleep(0.02)
    finally:
        listener.stop()


def note_on(hw: str, note: int, velocity: int) -> None:
    send(hw, bytes([0x90, note & 0x7F, velocity & 0x7F]))


def cmd_leds(hw: str, first: int, last: int, out: str) -> None:
    if not sys.stdin.isatty():
        raise ProbeError("leds is interactive and requires a TTY.")
    rows = []
    print("Each note lights one LED. Describe it, press Enter for none, or q to finish.", file=sys.stderr)
    try:
        for note in range(first, last + 1):
            note_on(hw, note, 0x7F)
            try:
                answer = input(f"Note {note:3d} (MCU: {note_name(note)}): ").strip()
            finally:
                note_on(hw, note, 0x00)
            if answer.lower() == "q":
                break
            rows.append((note, answer))
    finally:
        with open(out, "w", encoding="ascii", errors="replace") as fh:
            fh.write("| Note | MCU name | LED on device |\n|---|---|---|\n")
            for note, answer in rows:
                fh.write(f"| {note} | {note_name(note)} | {answer or '-'} |\n")
        print(f"Wrote {len(rows)} entries to {out}.", file=sys.stderr)


def knob_led_value(current: int, blink: bool) -> int:
    """Pitch bend MSB to send: far from the fader position to blink, equal to it to stop."""
    if not blink:
        return current
    return 0 if current >= 64 else 127


def cmd_knob_led(ports: dict[str, str], knob: int, blink: bool) -> None:
    # The firmware blinks a knob LED while the host's fader value differs from the
    # physical position (verified on firmware 011); this is the only known way to drive it.
    current = read_faders(ports["private"], 1.0)[knob - 1]
    send(ports["master"], bytes([0xE0 | (knob - 1), 0x00, knob_led_value(current, blink)]))


def parse_int(text: str) -> int:
    return int(text, 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    bt = sub.add_parser("buttons", help="Listen for and name control events")
    bt.add_argument("--seconds", type=float, default=0, help="Exit after N seconds (default: until Ctrl+C)")
    ls = sub.add_parser("leds", help="Interactively inspect LEDs by MIDI note")
    ls.add_argument("--first", type=parse_int, default=0, help="First note (default: 0)")
    ls.add_argument("--last", type=parse_int, default=127, help="Last note (default: 127)")
    ls.add_argument("--out", default="build/led-map.md", help="Output table (default: build/led-map.md)")
    ld = sub.add_parser("led", help="Set one LED")
    ld.add_argument("--note", type=parse_int, required=True, help="MIDI note 0-127")
    ld.add_argument("--velocity", type=parse_int, default=0x7F, help="127 on, 1 blink, 0 off (default: 127)")
    rg = sub.add_parser("ring", help="Set an MCU V-Pot LED ring (CC 48-55)")
    rg.add_argument("--knob", type=parse_int, required=True, help="Knob 1-8")
    rg.add_argument("--value", type=parse_int, required=True, help="MCU ring value 0-127 (mode<<4 | position)")
    kl = sub.add_parser("knob-led", help="Blink or stop a knob LED using pitch bend")
    kl.add_argument("--knob", type=parse_int, required=True, help="Knob 1-8 (= fader channel)")
    kl.add_argument("state", choices=["blink", "off"], help="blink or off")
    args = parser.parse_args()

    for tool in ("amidi", "stdbuf"):
        if shutil.which(tool) is None:
            print(f"{tool} not found (install alsa-utils or coreutils).", file=sys.stderr)
            return EXIT_USAGE
    valid = {
        "buttons": lambda: True,
        "leds": lambda: 0 <= args.first <= args.last <= 127,
        "led": lambda: 0 <= args.note <= 127 and 0 <= args.velocity <= 127,
        "ring": lambda: 1 <= args.knob <= 8 and 0 <= args.value <= 127,
        "knob-led": lambda: 1 <= args.knob <= 8,
    }
    if not valid[args.command]():
        print("Value out of range; see --help.", file=sys.stderr)
        return EXIT_USAGE
    try:
        ports = find_ports()
        hw = ports["master"]
        if args.command == "knob-led":
            cmd_knob_led(ports, args.knob, args.state == "blink")
        elif args.command == "buttons":
            cmd_buttons(hw, args.seconds)
        elif args.command == "leds":
            cmd_leds(hw, args.first, args.last, args.out)
        elif args.command == "led":
            note_on(hw, args.note, args.velocity)
        else:
            send(hw, bytes([0xB0, 0x2F + args.knob, args.value]))
    except ProbeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except (KeyboardInterrupt, EOFError):
        print("\nStopped.", file=sys.stderr)
        return EXIT_ABORT if args.command != "buttons" else EXIT_OK
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
