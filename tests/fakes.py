"""In-memory MIDI ports that mimic the SMC-Mixer (firmware 011) for tests."""

from __future__ import annotations

from collections.abc import Callable

from smc_mixer import sinco
from smc_mixer.transport import MessageCallback

# type 4, 0x326..0x33D captured on firmware 011 with known fader positions
FADER_TABLE = bytes.fromhex("12 7F 01 4A 55 01 0B 00 01 65 3B 01 38 7F 01 49 00 01 5A 20 01 72 69 01")
FADER_VALUES = [0x7F, 0x55, 0x00, 0x3B, 0x7F, 0x00, 0x20, 0x69]
USER_KNOB_VALUES = [108, 120, 87, 90, 92, 95, 97, 101]


class FakePort:
    def __init__(self, responder: Callable[[bytes], list[bytes]] | None = None) -> None:
        self.sent: list[bytes] = []
        self.closed = False
        self._callback: MessageCallback | None = None
        self._responder = responder

    def send(self, message: bytes) -> None:
        self.sent.append(message)
        for reply in self._responder(message) if self._responder else []:
            self.emit(reply)

    def set_callback(self, callback: MessageCallback) -> None:
        self._callback = callback

    def close(self) -> None:
        self.closed = True

    def emit(self, message: bytes) -> None:
        assert self._callback is not None
        self._callback(message)


class FakeDevice:
    def __init__(self, firmware: str = "SMC-Mixer_011", user: bool = False) -> None:
        self.firmware = firmware
        self.ram = bytearray(0x400)
        self.ram[0] = int(user)
        self.ram[0x326 : 0x326 + len(FADER_TABLE)] = FADER_TABLE
        self.ram[0x246:0x256] = b"".join(value.to_bytes(2, "little") for value in USER_KNOB_VALUES)

    def respond(self, message: bytes) -> list[bytes]:
        req = sinco.decode_sysex(message)
        if req.cmd == sinco.CMD_QUERY_NAME_VER:
            return [b"\xe1\x00\x4c", sinco.encode_sysex(req.cmd, self.firmware.encode().ljust(27, b"\x00"))]
        kind = req.payload[0]
        addr = int.from_bytes(req.payload[1:5], "little")
        length = int.from_bytes(req.payload[5:8], "little")
        if req.cmd == sinco.CMD_READ and kind == 0x04 and addr + length <= len(self.ram):
            return [sinco.encode_sysex(req.cmd, req.payload + bytes(self.ram[addr : addr + length]))]
        return [sinco.encode_sysex(sinco.CMD_ERROR, b"\x01")]


def device_responder(firmware: str = "SMC-Mixer_011") -> Callable[[bytes], list[bytes]]:
    return FakeDevice(firmware).respond
