#!/usr/bin/env python3
"""Read-only access to the SMC-Mixer configuration protocol over USB-MIDI.

Each request runs one `amidi -S ... -d -t T` process on the "Private" port,
which is the pattern verified to receive the mixer's replies. There is
intentionally no write command.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys

from smc_mixer import sinco as sc
from smc_mixer.errors import ProtocolError
from smc_mixer_tools.diagnostics.amidi import EXIT_ABORT, EXIT_OK, EXIT_RUNTIME, EXIT_USAGE, ProbeError, find_ports

HEX_TOKEN = re.compile(r"^[0-9A-Fa-f]{2}$")

# Live fader table in RAM (read type 4), verified on firmware SMC-Mixer_011 only:
# 8 entries of 3 bytes, the middle byte is the 7-bit fader position.
FADER_FIRMWARE = "SMC-Mixer_011"
FADER_TYPE = 0x04
FADER_ADDR = 0x326
FADER_ENTRY = 3
FADER_COUNT = 8


def parse_faders(data: bytes) -> list[int]:
    if len(data) < FADER_ENTRY * FADER_COUNT:
        raise ProbeError(f"Fader table too short: {len(data)} bytes")
    return [data[i * FADER_ENTRY + 1] & 0x7F for i in range(FADER_COUNT)]


def firmware_name(hw: str, timeout: float) -> str:
    for pkt in request(hw, sc.CMD_QUERY_NAME_VER, b"", timeout):
        if pkt.cmd == sc.CMD_QUERY_NAME_VER:
            return pkt.payload.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    raise ProbeError("No response to name/version query")


def read_faders(hw: str, timeout: float) -> list[int]:
    """Current 7-bit positions of faders 1-8, read over the "Private" port."""
    name = firmware_name(hw, timeout)
    if name != FADER_FIRMWARE:
        raise ProbeError(f"Firmware {name!r}: fader address is only verified for {FADER_FIRMWARE}")
    data = read_chunk(hw, FADER_TYPE, FADER_ADDR, FADER_ENTRY * FADER_COUNT, timeout)
    if data is None:
        raise ProbeError("Fader table is not readable")
    return parse_faders(data)


def split_sysex(raw: bytes) -> list[bytes]:
    out: list[bytes] = []
    start = -1
    for i, b in enumerate(raw):
        if b == 0xF0:
            start = i
        elif b == 0xF7 and start >= 0:
            out.append(raw[start : i + 1])
            start = -1
    return out


def request(hw: str, cmd: int, payload: bytes, timeout: float) -> list[sc.Packet]:
    msg = sc.encode_sysex(cmd, payload)
    args = ["amidi", "-p", hw, "-S", msg.hex(" "), "-d", "-t", str(timeout)]
    res = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 5)
    if res.returncode != 0:
        raise ProbeError(f"amidi fehlgeschlagen: {res.stderr.strip()}")
    raw = bytes(int(t, 16) for t in res.stdout.split() if HEX_TOKEN.match(t))
    packets = []
    for sysex in split_sysex(raw):
        try:
            packets.append(sc.decode_sysex(sysex))
        except ProtocolError as exc:
            print(f"Ignoriert ({exc}): {sysex.hex(' ').upper()}", file=sys.stderr)
    return packets


def hexdump(data: bytes, base: int = 0) -> str:
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off : off + 16]
        text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
        lines.append(f"{base + off:08X}  {chunk.hex(' ').upper():<47}  {text}")
    return "\n".join(lines)


def print_packets(packets: list[sc.Packet], base: int = 0) -> None:
    if not packets:
        print("No response")
    for pkt in packets:
        print(f"command {pkt.cmd:02X}, {len(pkt.payload)} payload bytes")
        print(hexdump(pkt.payload, base))


def read_chunk(hw: str, kind: int, addr: int, length: int, timeout: float) -> bytes | None:
    """Return the data of one read, or None if the device answered with an error or not at all."""
    echo = sc.read_payload(kind, addr, length)
    for pkt in request(hw, sc.CMD_READ, echo, timeout):
        if pkt.cmd == sc.CMD_READ and pkt.payload[:8] == echo:
            return pkt.payload[8:]
    return None


def read_range(hw: str, kind: int, addr: int, length: int, chunk: int, timeout: float) -> tuple[bytes, str]:
    """Read in chunks; on an error the chunk is halved to find the exact end of the region."""
    data = bytearray()
    while len(data) < length:
        size = min(chunk, length - len(data))
        part = read_chunk(hw, kind, addr + len(data), size, timeout)
        if part is None:
            if size == 1:
                return bytes(data), f"Error response at address 0x{addr + len(data):X} (end of region?)"
            chunk = max(size // 2, 1)
            continue
        data += part
        if len(part) < size:
            return bytes(data), f"Short response at address 0x{addr + len(data):X}"
        print(f"  Read through 0x{addr + len(data):X}", file=sys.stderr)
    return bytes(data), "complete"


def diff(a: bytes, b: bytes) -> list[str]:
    lines = [f"{i:08X}  {x:02X} -> {y:02X}" for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y]
    if len(a) != len(b):
        lines.append(f"Different lengths: {len(a)} / {len(b)} bytes")
    return lines


def parse_int(text: str) -> int:
    return int(text, 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--timeout", type=float, default=1.0, help="Response timeout in seconds")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("info", help="Query device name/version (command 0x11)")
    sub.add_parser("faders", help=f"Read current fader positions 1-8 (only {FADER_FIRMWARE})")
    rd = sub.add_parser("read", help="Read memory (command 0x23)")
    rd.add_argument("--type", type=parse_int, required=True, help="Type byte, e.g. 0 or 0x01")
    rd.add_argument("--addr", type=parse_int, default=0, help="Address (default: 0)")
    rd.add_argument("--len", type=parse_int, default=16, help="Number of bytes (default: 16)")
    rd.add_argument("--chunk", type=parse_int, default=64, help="Bytes per request (default: 64)")
    rd.add_argument("--out", help="Also write raw data to this file")
    df = sub.add_parser("diff", help="Compare two files saved with read --out")
    df.add_argument("a", help="First file")
    df.add_argument("b", help="Second file")
    sc_types = sub.add_parser("scan-types", help="Try reading several type bytes")
    sc_types.add_argument("--first", type=parse_int, default=0, help="First type (default: 0)")
    sc_types.add_argument("--last", type=parse_int, default=15, help="Last type (default: 15)")
    sc_types.add_argument("--len", type=parse_int, default=16, help="Number of bytes (default: 16)")
    args = parser.parse_args()

    if args.command == "diff":
        try:
            with open(args.a, "rb") as fa, open(args.b, "rb") as fb:
                lines = diff(fa.read(), fb.read())
        except OSError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return EXIT_USAGE
            print("\n".join(lines) if lines else "No differences")
        return EXIT_OK
    if shutil.which("amidi") is None:
        print("amidi not found (install the alsa-utils package).", file=sys.stderr)
        return EXIT_USAGE
    try:
        hw = find_ports()["private"]
        if args.command == "info":
            print(firmware_name(hw, args.timeout))
        elif args.command == "faders":
            for i, value in enumerate(read_faders(hw, args.timeout), start=1):
                print(f"Fader {i}: {value:3d}  (Pitch Bend {value << 7})")
        elif args.command == "read":
            if args.chunk < 1 or args.len < 1:
                print("--len and --chunk must be >= 1.", file=sys.stderr)
                return EXIT_USAGE
            data, status = read_range(hw, args.type, args.addr, args.len, args.chunk, args.timeout)
            print(hexdump(data, args.addr))
            print(f"{len(data)} bytes, {status}", file=sys.stderr)
            if args.out:
                with open(args.out, "wb") as fh:
                    fh.write(data)
        else:
            if not 0 <= args.first <= args.last <= 0xFF:
                print("--first/--last muessen 0..255 sein, first <= last.", file=sys.stderr)
                return EXIT_USAGE
            for kind in range(args.first, args.last + 1):
                packets = request(hw, sc.CMD_READ, sc.read_payload(kind, 0, args.len), args.timeout)
                print(f"== Typ 0x{kind:02X}")
                print_packets(packets)
    except ProbeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except KeyboardInterrupt:
        print("Aborted.", file=sys.stderr)
        return EXIT_ABORT
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
