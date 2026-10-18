"""Serialized, read-only Sinco requests, shared by direct reads and state polling."""

import queue
import threading
from collections.abc import Callable

from smc_mixer import sinco
from smc_mixer.constants import STATE_FIRMWARE
from smc_mixer.errors import ProtocolError, ResponseTimeoutError, UnsupportedFirmwareError


class Configuration:
    def __init__(self, send: Callable[[bytes], None]) -> None:
        self.lock = threading.RLock()
        self._send = send
        self._responses: queue.Queue[bytes] = queue.Queue()
        self._firmware: str | None = None

    def receive(self, message: bytes) -> None:
        if message[:1] == b"\xf0":
            self._responses.put(message)

    def reset(self) -> None:
        with self.lock:
            self._firmware = None
            while not self._responses.empty():
                self._responses.get_nowait()

    def request(self, command: int, payload: bytes, timeout: float) -> sinco.Packet:
        with self.lock:
            while not self._responses.empty():
                self._responses.get_nowait()
            self._send(sinco.encode_sysex(command, payload))
            try:
                response = self._responses.get(timeout=timeout)
            except queue.Empty:
                raise ResponseTimeoutError(f"no answer to command 0x{command:02X} within {timeout} s") from None
            return sinco.decode_sysex(response)

    def firmware(self, timeout: float) -> str:
        with self.lock:
            packet = self.request(sinco.CMD_QUERY_NAME_VER, b"", timeout)
            if packet.cmd != sinco.CMD_QUERY_NAME_VER:
                raise ProtocolError(f"unexpected answer 0x{packet.cmd:02X} to name/version query")
            self._firmware = packet.payload.split(b"\x00", 1)[0].decode("ascii", errors="replace")
            return self._firmware

    def require_supported(self, timeout: float) -> None:
        name = self._firmware if self._firmware is not None else self.firmware(timeout)
        if name != STATE_FIRMWARE:
            raise UnsupportedFirmwareError(f"state tables only verified for {STATE_FIRMWARE}, device runs {name}")

    def read_memory(self, kind: int, address: int, length: int, timeout: float) -> bytes:
        echo = sinco.read_payload(kind, address, length)
        packet = self.request(sinco.CMD_READ, echo, timeout)
        if packet.cmd != sinco.CMD_READ or packet.payload[: sinco.READ_ECHO_LEN] != echo:
            raise ProtocolError(f"read of type 0x{kind:02X} at 0x{address:X} rejected (answer 0x{packet.cmd:02X})")
        return packet.payload[sinco.READ_ECHO_LEN :]
