"""Shared ALSA/amidi access and raw MIDI stream parsing for diagnostics."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_ABORT = 130

PORT_NAMES = {"private": "SMC-Mixer-Private", "master": "SMC-Mixer-Master"}
HW_ID = re.compile(r"^hw:\d+,\d+,\d+$")
HEX_TOKEN = re.compile(r"^[0-9A-Fa-f]{2}$")
log = logging.getLogger("probe_smc")


class ProbeError(Exception):
    """Expected diagnostic failure, reported without a traceback."""


@dataclass(frozen=True)
class Received:
    port: str
    data: bytes

    def hex(self) -> str:
        return self.data.hex(" ").upper()


def data_len(status: int) -> int:
    if 0x80 <= status <= 0xBF or 0xE0 <= status <= 0xEF or status == 0xF2:
        return 2
    if 0xC0 <= status <= 0xDF or status in (0xF1, 0xF3):
        return 1
    return 0


def is_noise(msg: bytes) -> bool:
    """Pitch bend comes from jittering faders, realtime bytes are clock/sensing."""
    return 0xE0 <= msg[0] <= 0xEF or msg[0] >= 0xF8


class MidiParser:
    """Splits a raw MIDI byte stream into complete messages."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._running = 0

    def feed(self, data: bytes) -> list[bytes]:
        out: list[bytes] = []
        for b in data:
            if b >= 0xF8:
                out.append(bytes([b]))
                continue
            if self._buf[:1] == b"\xf0":
                if b < 0x80 or b == 0xF7:
                    self._buf.append(b)
                    if b == 0xF7:
                        out.append(bytes(self._buf))
                        self._buf = bytearray()
                    continue
                out.append(bytes(self._buf))
                self._buf = bytearray()
            if b >= 0x80:
                self._buf = bytearray([b])
                self._running = b if b < 0xF0 else 0
                if b == 0xF0:
                    continue
            elif self._buf:
                self._buf.append(b)
            elif self._running:
                self._buf = bytearray([self._running, b])
            else:
                log.debug("stray data byte %02X ignored", b)
                continue
            if len(self._buf) == 1 + data_len(self._buf[0]):
                out.append(bytes(self._buf))
                self._buf = bytearray()
        return out


class Listener:
    """Runs amidi -d on one port and collects parsed messages."""

    def __init__(self, port: str, hw: str) -> None:
        self.port = port
        self._parser = MidiParser()
        self._lock = threading.Lock()
        self._messages: list[Received] = []
        self._proc = subprocess.Popen(
            ["stdbuf", "-o0", "amidi", "-p", hw, "-d"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        assert self._proc.stdout is not None
        fd = self._proc.stdout.fileno()
        tail = ""
        while chunk := os.read(fd, 4096):
            text = tail + chunk.decode("ascii", errors="replace")
            cut = max(text.rfind(" "), text.rfind("\n"))
            text, tail = text[: cut + 1], text[cut + 1 :]
            raw = bytes(int(t, 16) for t in text.split() if HEX_TOKEN.match(t))
            msgs = self._parser.feed(raw)
            with self._lock:
                self._messages.extend(Received(self.port, m) for m in msgs)

    def alive(self) -> bool:
        return self._proc.poll() is None

    def stderr(self) -> str:
        assert self._proc.stderr is not None
        return self._proc.stderr.read().decode(errors="replace").strip() if not self.alive() else ""

    def take(self) -> list[Received]:
        with self._lock:
            msgs, self._messages = self._messages, []
        return msgs

    def stop(self) -> None:
        if self.alive():
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()


def find_ports() -> dict[str, str]:
    res = subprocess.run(["amidi", "-l"], capture_output=True, text=True, timeout=5)
    if res.returncode != 0:
        raise ProbeError(f"amidi -l fehlgeschlagen: {res.stderr.strip()}")
    ports: dict[str, str] = {}
    for line in res.stdout.splitlines():
        parts = line.split()
        for key, name in PORT_NAMES.items():
            if name in line and len(parts) > 1 and HW_ID.match(parts[1]):
                ports[key] = parts[1]
    missing = set(PORT_NAMES) - set(ports)
    if missing:
        raise ProbeError(f"Ports not found: {', '.join(sorted(missing))}. Is the mixer connected?")
    return ports


def send(hw: str, data: bytes) -> None:
    res = subprocess.run(["amidi", "-p", hw, "-S", data.hex(" ")], capture_output=True, text=True, timeout=5)
    if res.returncode != 0:
        raise ProbeError(f"Senden an {hw} fehlgeschlagen: {res.stderr.strip()}")
