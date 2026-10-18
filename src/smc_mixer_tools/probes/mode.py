"""Guided DAW/USER transitions with protocol confirmation and read-only memory samples."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from smc_mixer.constants import MASTER_PORT_NAME, PRIVATE_PORT_NAME
from smc_mixer.errors import SmcMixerError
from smc_mixer.mixer import SmcMixer
from smc_mixer.transport import RtMidiPort
from smc_mixer_tools.probes.mode_analysis import ModeSample, report
from smc_mixer_tools.probes.recording import Recorder, RecordingPort


class ModeRecorder(Recorder):
    def __init__(self, stream: TextIO) -> None:
        super().__init__(stream)
        self.evidence: set[tuple[str, bool]] = set()

    def begin(self, name: str, expected: tuple[int, bool] | None = None) -> None:
        super().begin(name, expected)
        with self.lock:
            self.evidence.clear()

    def raw(self, message: bytes) -> None:
        super().raw(message)
        with self.lock:
            if not self.phase.endswith("_verify") or len(message) != 3:
                return
            status, number, value = message
            mode = None
            if status & 0xF0 in (0x80, 0x90) and number == 16:
                mode = "DAW"
            elif status & 0xF0 == 0xB0 and number == 20 and value in (0, 127):
                mode = "USER"
            if mode:
                pressed = (status & 0xF0 != 0x80 and value > 0) if mode == "DAW" else value == 127
                self.evidence.add((mode, pressed))
            print(f"  MIDI [{message.hex(' ')}]" + (f" -> {mode} mapping" if mode else ""), flush=True)

    def matches(self, mode: str) -> bool:
        with self.lock:
            return self.evidence == {(mode, True), (mode, False)}


class ModeSession:
    def __init__(
        self,
        mixer: SmcMixer,
        recorder: ModeRecorder,
        directory: Path,
        kind: int,
        address: int,
        length: int,
    ) -> None:
        self.mixer, self.recorder, self.directory = mixer, recorder, directory
        self.kind, self.address, self.length = kind, address, length
        self.samples: list[ModeSample] = []

    def save(self) -> None:
        metadata = [dict(name=s.name, mode=s.mode, stage=s.stage, verified=s.verified) for s in self.samples]
        (self.directory / "samples.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        (self.directory / "report.txt").write_text(report(self.samples, self.kind, self.address), encoding="utf-8")

    def capture(self, name: str, mode: str, stage: int, verified: bool) -> ModeSample:
        self.recorder.begin(name + "_read")
        print(f"  {name}: Do not touch any controls. Reading two snapshots ...", flush=True)
        parts = []
        for suffix in ("a", "b"):
            data = bytearray()
            while len(data) < self.length:
                size = min(32, self.length - len(data))
                chunk = self.mixer.read_memory(self.kind, self.address + len(data), size)
                if len(chunk) != size:
                    raise ValueError(f"Short memory response: received {len(chunk)} of {size} bytes")
                data.extend(chunk)
                time.sleep(0.05)
            part = bytes(data)
            (self.directory / f"{name}_{suffix}.bin").write_bytes(part)
            parts.append(part)
            time.sleep(0.2)
        sample = ModeSample(name, mode, stage, parts[0], parts[1], verified)
        self.samples.append(sample)
        self.save()
        for _ in self.mixer.events(timeout=0):
            continue
        return sample

    def run(self) -> None:
        print("\nSequence: DAW -> USER -> DAW -> USER. Do not power-cycle or unplug the mixer.")
        print("Switch modes on the device. Keep Shift released throughout the measurement.")
        print("Do not move faders or knobs. Press MUTE 1 only when prompted.")
        for stage, mode in enumerate(("DAW", "USER", "DAW", "USER"), 1):
            attempt = 0
            while True:
                attempt += 1
                name = f"{stage}_{mode.lower()}_try{attempt}"
                self.recorder.begin(name + "_switch")
                input(f"\n[{stage}/4] Select {mode} mode and release all buttons, including Shift. Then press Enter: ")
                time.sleep(0.5)
                before = self.capture(name + "_before", mode, stage, False)
                self.recorder.begin(name + "_verify")
                input("Now, WITHOUT Shift, press and release MUTE 1 once, then press Enter: ")
                if not self.recorder.matches(mode):
                    print(f"  {mode} mode was not confirmed unambiguously. This attempt will NOT be analyzed.")
                    print("  Check the selected mode and release Shift. This step will be repeated.")
                    continue
                before.verified = True
                self.save()
                print(f"  {mode} protocol confirmed.")
                self.capture(name + "_after", mode, stage, True)
                print("  Idle sample: keep hands off the controls ...", flush=True)
                time.sleep(2)
                self.capture(name + "_idle", mode, stage, True)
                break


def main() -> int:
    parser = argparse.ArgumentParser(description="Guided, read-only search for the DAW/USER mode bit")
    parser.add_argument("--type", type=lambda s: int(s, 0), choices=[4, 5], default=4, help="Memory type (default: 4)")
    parser.add_argument("--addr", type=lambda s: int(s, 0), default=0x2C0, help="Start address (default: 0x2c0)")
    parser.add_argument("--len", type=lambda s: int(s, 0), default=0xC0, help="Range size 1..512 (default: 192)")
    parser.add_argument("--out", type=Path, help="New output directory")
    args = parser.parse_args()
    if args.addr < 0 or args.addr + args.len > 0x100000000 or not 1 <= args.len <= 512:
        parser.error("Address must fit in 32 bits and --len must be 1..512")
    directory = args.out or Path("build/mode-probe") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        directory.mkdir(parents=True, exist_ok=False)
        print(f"Measurement data: {directory.resolve()}\nCtrl+C aborts; saved data will be kept.")
        with (directory / "midi.jsonl").open("w", encoding="utf-8") as stream:
            recorder = ModeRecorder(stream)
            master = RtMidiPort(MASTER_PORT_NAME)
            try:
                private = RtMidiPort(PRIVATE_PORT_NAME)
            except Exception:
                master.close()
                raise
            with SmcMixer(RecordingPort(master, recorder), private) as mixer:
                firmware = mixer.firmware()
                (directory / "session.json").write_text(
                    json.dumps(
                        {
                            "firmware": firmware,
                            "type": args.type,
                            "address": args.addr,
                            "length": args.len,
                            "chunk_size": 32,
                            "started": datetime.now().isoformat(),
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                if firmware != "SMC-Mixer_011":
                    raise ValueError("This measurement is prepared for firmware SMC-Mixer_011.")
                session = ModeSession(mixer, recorder, directory, args.type, args.addr, args.len)
                try:
                    session.run()
                finally:
                    session.save()
        print(f"\nDone. Please share this report: {directory / 'report.txt'}")
        print("Candidates must then be confirmed with targeted repeat reads.")
        return 0
    except KeyboardInterrupt:
        print(f"\nAborted. Existing data: {directory}", file=sys.stderr)
        return 130
    except (SmcMixerError, OSError, RuntimeError, ValueError, EOFError) as exc:
        print(f"Error: {exc}\nExisting data: {directory}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
