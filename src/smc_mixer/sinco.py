"""Sinco configuration protocol: packet framing and SysEx 7-bit encoding.

Packet layout (SincoOTA, BluetoothPacketTools.kt), multi-byte fields little endian:
    00 59 | cmd | len (3) | payload | ~sum(payload) & 0xFF

Over USB-MIDI the packet is packed LSB-first into 7-bit groups and wrapped in
F0 ... F7. Verified against the self-test golden vector in M-UPGRADE
upgrade_core.exe and against the device (firmware 011).
"""

from __future__ import annotations

from dataclasses import dataclass

from smc_mixer.errors import ProtocolError

HEADER = b"\x00\x59"
CMD_ERROR = 0x00
CMD_QUERY_NAME_VER = 0x11
CMD_WRITE = 0x22
CMD_READ = 0x23
READ_ECHO_LEN = 8  # read replies start with the echoed request: type, address, length


@dataclass(frozen=True)
class Packet:
    cmd: int
    payload: bytes


def checksum(payload: bytes) -> int:
    return ~sum(payload) & 0xFF


def build_packet(cmd: int, payload: bytes = b"") -> bytes:
    return HEADER + bytes([cmd]) + len(payload).to_bytes(3, "little") + payload + bytes([checksum(payload)])


def read_payload(kind: int, address: int, length: int) -> bytes:
    return bytes([kind]) + address.to_bytes(4, "little") + length.to_bytes(3, "little")


def pack7(data: bytes) -> bytes:
    acc = nbits = 0
    out = bytearray()
    for b in data:
        acc |= b << nbits
        nbits += 8
        while nbits >= 7:
            out.append(acc & 0x7F)
            acc >>= 7
            nbits -= 7
    if nbits:
        out.append(acc & 0x7F)
    return bytes(out)


def unpack7(data: bytes) -> bytes:
    acc = nbits = 0
    out = bytearray()
    for b in data:
        if b > 0x7F:
            raise ProtocolError(f"byte {b:02X} is not a 7-bit value")
        acc |= b << nbits
        nbits += 7
        while nbits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nbits -= 8
    return bytes(out)  # leftover bits are padding


def encode_sysex(cmd: int, payload: bytes = b"") -> bytes:
    return b"\xf0" + pack7(build_packet(cmd, payload)) + b"\xf7"


def decode_sysex(msg: bytes) -> Packet:
    if len(msg) < 3 or msg[0] != 0xF0 or msg[-1] != 0xF7:
        raise ProtocolError("not a complete SysEx message")
    raw = unpack7(msg[1:-1])
    if len(raw) < 7 or raw[:2] != HEADER:
        raise ProtocolError("missing Sinco header 00 59")
    length = int.from_bytes(raw[3:6], "little")
    if len(raw) < 7 + length:
        raise ProtocolError(f"packet too short for length {length}")
    payload = raw[6 : 6 + length]
    if raw[6 + length] != checksum(payload):
        raise ProtocolError("checksum mismatch")
    return Packet(raw[2], payload)
