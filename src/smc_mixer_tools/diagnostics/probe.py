#!/usr/bin/env python3
"""Probe the M-Wave SMC-Mixer for MIDI/SysEx replies via ALSA amidi.

Sends query messages to both MIDI ports of the mixer while listening on both
ports, and reports every received message that is not fader noise (pitch bend
or realtime). Requires amidi (alsa-utils) and stdbuf (coreutils).
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from dataclasses import dataclass

from smc_mixer import sinco
from smc_mixer_tools.diagnostics.amidi import (
    EXIT_ABORT,
    EXIT_OK,
    EXIT_RUNTIME,
    EXIT_USAGE,
    Listener,
    ProbeError,
    find_ports,
    is_noise,
    send,
)
from smc_mixer_tools.diagnostics.formatting import describe_sysex

log = logging.getLogger("probe_smc")


@dataclass(frozen=True)
class Probe:
    name: str
    port: str
    data: bytes
    risky: bool

    def hex(self) -> str:
        return self.data.hex(" ").upper()


def sysex(*body: int) -> bytes:
    return bytes([0xF0, *body, 0xF7])


def build_probes(scan: bool) -> list[Probe]:
    probes: list[Probe] = []
    for port in ("private", "master"):
        probes.append(Probe("Universal Identity Request (alle)", port, sysex(0x7E, 0x7F, 0x06, 0x01), False))
        probes.append(Probe("Universal Identity Request (ID 0)", port, sysex(0x7E, 0x00, 0x06, 0x01), False))
        for model, label in ((0x10, "Logic Control"), (0x11, "Logic XT"), (0x14, "MCU"), (0x15, "MCU XT")):
            probes.append(Probe(f"Mackie Device Query {label}", port, sysex(0x00, 0x00, 0x66, model, 0x00), False))
        probes.append(Probe("Mackie Version Request", port, sysex(0x00, 0x00, 0x66, 0x14, 0x13, 0x00), False))
        probes.append(Probe("HUI Ping", port, bytes([0x90, 0x00, 0x00]), False))
        probes.append(Probe("Sinco Name/Version", port, sinco.encode_sysex(sinco.CMD_QUERY_NAME_VER), False))
    if scan:
        for port in ("private", "master"):
            for mfr in range(0x01, 0x7E):
                for payload in ((), (0x00,), (0x01,)):
                    name = f"Manufacturer scan ID {mfr:02X} payload {bytes(payload).hex(' ').upper() or '-'}"
                    probes.append(Probe(name, port, sysex(mfr, *payload), True))
    return probes


def run(probes: list[Probe], start: int, wait: float) -> int:
    ports = find_ports()
    log.info("Ports: %s", ports)
    listeners = [Listener(port, hw) for port, hw in ports.items()]
    hits = 0
    last = start - 1
    try:
        time.sleep(0.5)  # let amidi open the ports before the first probe
        for i in range(start, len(probes)):
            dead = [lst for lst in listeners if not lst.alive()]
            if dead:
                details = "; ".join(f"{lst.port}: {lst.stderr()}" for lst in dead)
                raise ProbeError(f"Listener exited after probe #{last} (mixer disconnected?): {details}")
            probe = probes[i]
            log.info("#%d %s -> %s: %s", i, probe.name, probe.port, probe.hex())
            send(ports[probe.port], probe.data)
            last = i
            time.sleep(wait)
            received = [msg for lst in listeners for msg in lst.take()]
            relevant = [msg for msg in received if not is_noise(msg.data)]
            for msg in received:
                if is_noise(msg.data):
                    log.info("  Ignored noise <- %s: %s", msg.port, msg.hex())
            if relevant:
                hits += 1
                print(f"#{i} {probe.name} -> {probe.port}: {probe.hex()}", flush=True)
                for msg in relevant:
                    print(f"    <- {msg.port}: {msg.hex()}", flush=True)
                    if msg.data[0] == 0xF0:
                        print(f"       {describe_sysex(msg.data)}", flush=True)
        time.sleep(wait)
        if not all(lst.alive() for lst in listeners):
            raise ProbeError(f"Listener exited after final probe #{last} (mixer disconnected?)")
    finally:
        for lst in listeners:
            lst.stop()
    print(f"Done: sent {len(probes) - start} probes; {hits} received responses.", flush=True)
    return EXIT_OK


def confirm_scan(probes: list[Probe], start: int) -> None:
    count = sum(p.risky for p in probes[start:])
    print(
        f"WARNING: --scan sends {count} unknown SysEx commands to the mixer. This may change settings,\n"
        "trigger a factory reset, or enter firmware update mode.",
        file=sys.stderr,
    )
    if not sys.stdin.isatty():
        raise ProbeError("No TTY for confirmation; pass --yes to proceed.")
    if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        raise KeyboardInterrupt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scan", action="store_true", help="Also send the risky manufacturer SysEx scan")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation for --scan")
    parser.add_argument("--wait", type=float, default=0.4, help="Delay per probe in seconds (default: 0.4)")
    parser.add_argument("--start", type=int, default=0, help="Resume at probe number N")
    parser.add_argument("--dry-run", action="store_true", help="Print probes without sending them")
    parser.add_argument("-v", "--verbose", action="store_true", help="Log each probe to stderr")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    probes = build_probes(args.scan)
    if not 0 <= args.start < len(probes) or args.wait <= 0:
        print(f"Invalid: --start must be 0..{len(probes) - 1}; --wait must be > 0.", file=sys.stderr)
        return EXIT_USAGE
    if args.dry_run:
        for i in range(args.start, len(probes)):
            p = probes[i]
            print(f"#{i} {'RISKANT ' if p.risky else ''}{p.name} -> {p.port}: {p.hex()}")
        return EXIT_OK
    for tool in ("amidi", "stdbuf"):
        if shutil.which(tool) is None:
            print(f"{tool} not found (install alsa-utils or coreutils).", file=sys.stderr)
            return EXIT_USAGE
    try:
        if args.scan and not args.yes:
            confirm_scan(probes, args.start)
        return run(probes, args.start, args.wait)
    except ProbeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME
    except KeyboardInterrupt:
        print("Aborted.", file=sys.stderr)
        return EXIT_ABORT


if __name__ == "__main__":
    raise SystemExit(main())
