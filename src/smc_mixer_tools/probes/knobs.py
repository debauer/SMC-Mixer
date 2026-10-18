"""Interactive measurements with raw MIDI logging and paired small RAM reads."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from smc_mixer.constants import MASTER_PORT_NAME, PRIVATE_PORT_NAME
from smc_mixer.controls import DeviceMode
from smc_mixer.errors import SmcMixerError
from smc_mixer.mixer import SmcMixer
from smc_mixer.transport import RtMidiPort
from smc_mixer_tools.probes.knob_analysis import Sample, summarize
from smc_mixer_tools.probes.recording import Recorder, RecordingPort


class Session:
    def __init__(self, mixer: SmcMixer, recorder: Recorder, directory: Path, address: int, length: int) -> None:
        self.mixer, self.recorder, self.directory = mixer, recorder, directory
        self.address, self.length = address, length
        self.samples: list[Sample] = []

    def require_user(self) -> None:
        if self.mixer.read_mode() != DeviceMode.USER:
            raise ValueError(
                "DAW mode is active; this measurement requires USER mode. Existing snapshots are preserved."
            )

    def read(self) -> bytes:
        data = bytearray()
        while len(data) < self.length:
            size = min(32, self.length - len(data))
            chunk = self.mixer.read_memory(4, self.address + len(data), size)
            if len(chunk) != size:
                raise ValueError(f"Short RAM response: received {len(chunk)} of {size} bytes")
            data.extend(chunk)
            if len(data) < self.length:
                time.sleep(0.05)
        return bytes(data)

    def capture(
        self,
        name: str,
        knob: int | None = None,
        value: int | None = None,
        source: tuple[int, int] | None = None,
    ) -> None:
        self.require_user()
        self.recorder.begin(name + "_read")
        print("  Do not move any controls. Reading two small RAM snapshots ...", flush=True)
        parts = []
        for suffix in ("a", "b"):
            data = self.read()
            (self.directory / f"{name}_{suffix}.bin").write_bytes(data)
            parts.append(data)
            if suffix == "a":
                time.sleep(0.2)
        self.require_user()
        self.samples.append(Sample(name, parts[0], parts[1], knob, value, source))
        metadata = [dict(name=s.name, mode="USER", knob=s.knob, value=s.value, source=s.source) for s in self.samples]
        (self.directory / "samples.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        self.report()
        # The library also queues decoded events; raw MIDI above is the reference for this experiment.
        for _ in self.mixer.events(timeout=0):
            continue
        print(f"  Saved: {name}")

    def report(self) -> None:
        if self.samples:
            (self.directory / "report.txt").write_text(summarize(self.samples, self.address), encoding="utf-8")

    def action(self, name: str, instruction: str, knob: int, different_from: int | None = None) -> None:
        print(f"\n--- {name} ---\n{instruction}")
        self.require_user()
        self.recorder.begin(name, (knob, False))
        previous = next((s for s in reversed(self.samples) if s.knob == knob), None)
        while True:
            input("Turn the knob WITHOUT Shift, stop when you see a new value, then press Enter: ")
            value = self.recorder.last_value()
            source = self.recorder.source()
            if (
                value is not None
                and value != different_from
                and (previous is None or (source == previous.source and value != previous.value))
            ):
                break
            print("Need exactly one CC source, the same source as before, and a different value.")
            if different_from is not None:
                print(f"For the cross-check, the value must also differ from {different_from}.")
            print("Move ONLY the specified knob. Incoming MIDI values are shown above.")
            self.recorder.begin(name, (knob, False))
        self.capture(name, knob, value, source)

    def run(self, initial_only: bool, all_knobs: bool = False) -> None:
        knobs = tuple(range(1, 9)) if all_knobs else (1, 2)
        print("\nThis run measures USER-mode knobs only (absolute values 0..127).")
        print("Keep Shift released. Do not touch faders or other buttons. Stay in USER mode.")
        if not initial_only:
            print(f"{'Full run' if all_knobs else 'Quick search'}: {len(knobs) * 2 + 1} knob adjustments.")
            print("Small turns are enough. Do not try to hit specific target values.")
        self.recorder.begin("initial_wait")
        while True:
            input("Select USER mode and release all buttons. Do not turn any knob yet. Then press Enter: ")
            if self.mixer.read_mode() == DeviceMode.USER:
                break
                print("DAW detected. Switch the device to USER mode, then confirm again.")
        print("USER mode confirmed by the mode byte.")
        self.capture("initial")
        if initial_only:
            return
        for knob in knobs:
            for label in ("first", "second"):
                self.action(
                    f"user_{knob}_{label}",
                    f"USER mode, WITHOUT Shift: Turn only knob {knob} a little to a different value.\n"
                    "When a new value appears, stop turning and press Enter. Reverse direction at an endpoint.",
                    knob,
                )
        self.action(
            "user_1_recheck",
            "WITHOUT Shift, turn only knob 1 a little to another value.\n"
            "This cross-check distinguishes stored knob values from a shared send buffer.",
            1,
            different_from=self.samples[-1].value,
        )
        print("\nIdle sample: keep Shift released and do not touch any controls.")
        time.sleep(2)
        self.capture("idle")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guided search for USER-mode knob initial values (read-only; Shift released)"
    )
    parser.add_argument("--addr", type=lambda s: int(s, 0), default=0, help="RAM start address (default: 0)")
    parser.add_argument("--len", type=lambda s: int(s, 0), default=0x200, help="Range size (default: 512, max: 512)")
    parser.add_argument("--out", type=Path, help="New output directory; must not already exist")
    parser.add_argument(
        "--initial-only", action="store_true", help="Read only the initial snapshot; do not prompt for turns"
    )
    parser.add_argument(
        "--all-knobs", action="store_true", help="Test all eight knobs (default: quick search with knobs 1 and 2)"
    )
    args = parser.parse_args()
    if args.addr < 0 or args.addr + args.len > 0x100000000 or not 1 <= args.len <= 512:
        parser.error("Address must fit in 32 bits and --len must be 1..512")
    directory = args.out or Path("build/knob-probe") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        directory.mkdir(parents=True, exist_ok=False)
        print(f"Measurement data: {directory.resolve()}\nPress Ctrl+C to abort; saved snapshots are kept.")
        with (directory / "midi.jsonl").open("w", encoding="utf-8") as stream:
            recorder = Recorder(stream)
            master = RtMidiPort(MASTER_PORT_NAME)
            try:
                private = RtMidiPort(PRIVATE_PORT_NAME)
            except Exception:
                master.close()
                raise
            with SmcMixer(RecordingPort(master, recorder), private) as mixer:
                firmware = mixer.firmware()
                print(f"Firmware: {firmware}")
                (directory / "session.json").write_text(
                    json.dumps(
                        {
                            "firmware": firmware,
                            "type": 4,
                            "address": args.addr,
                            "length": args.len,
                            "chunk_size": 32,
                            "mode": "USER",
                            "mode_check": {"type": 4, "address": 0, "mask": 1},
                            "knobs": [] if args.initial_only else list(range(1, 9)) if args.all_knobs else [1, 2],
                            "started": datetime.now().isoformat(),
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                if firmware != "SMC-Mixer_011":
                    raise ValueError("This measurement is prepared for firmware SMC-Mixer_011.")
                session = Session(mixer, recorder, directory, args.addr, args.len)
                try:
                    session.run(args.initial_only, args.all_knobs)
                finally:
                    session.report()
        print(f"\nDone. Report: {directory / 'report.txt'}")
        print("Keep the report and measurement data for further analysis.")
        return 0
    except KeyboardInterrupt:
        print(f"\nAborted. Existing data: {directory}", file=sys.stderr)
        return 130
    except (SmcMixerError, OSError, RuntimeError, ValueError, EOFError) as exc:
        print(f"Error: {exc}\nExisting data: {directory}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
