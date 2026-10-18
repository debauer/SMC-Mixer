"""Raw MIDI recording shared by guided measurement tools."""

import json
import threading
import time
from typing import TextIO

from smc_mixer.transport import MessageCallback, MidiPort


class Recorder:
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.lock = threading.Lock()
        self.phase = "connect"
        self.expected: tuple[int, bool] | None = None
        self.sources: dict[tuple[int, int], int] = {}

    def begin(self, name: str, expected: tuple[int, bool] | None = None) -> None:
        with self.lock:
            self.phase, self.expected, self.sources = name, expected, {}

    def raw(self, message: bytes) -> None:
        with self.lock:
            self.stream.write(
                json.dumps({"time": time.time(), "phase": self.phase, "port": "Master", "hex": message.hex(" ")}) + "\n"
            )
            self.stream.flush()
            if self.expected is None or len(message) != 3 or message[0] & 0xF0 != 0xB0:
                return
            knob, shift = self.expected
            channel, cc = (message[0] & 0x0F) + 1, message[1]
            value = message[2]
            self.sources[channel, cc] = value
            mode = "with Shift" if shift else "without Shift"
            print(
                f"  Knob {knob} ({mode}): absolute {value:03d} | channel {channel}, CC {cc} [{message.hex(' ')}]",
                flush=True,
            )

    def last_value(self) -> int | None:
        with self.lock:
            return next(iter(self.sources.values())) if len(self.sources) == 1 else None

    def source(self) -> tuple[int, int] | None:
        with self.lock:
            return next(iter(self.sources)) if len(self.sources) == 1 else None


class RecordingPort:
    """Decorate a port without applying the event decoder's CC heuristics."""

    def __init__(self, port: MidiPort, recorder: Recorder) -> None:
        self.port, self.recorder = port, recorder

    def send(self, message: bytes) -> None:
        self.port.send(message)

    def close(self) -> None:
        self.port.close()

    def set_callback(self, callback: MessageCallback) -> None:
        def receive(message: bytes) -> None:
            self.recorder.raw(message)
            callback(message)

        self.port.set_callback(receive)
