"""Daemon commands; discovery and validation never instantiate a MIDI controller."""

import argparse
import logging
import shutil
import signal
import sys
import threading
from pathlib import Path

from smc_mixer_daemon.audio import Audio
from smc_mixer_daemon.commands import BackendError
from smc_mixer_daemon.config import ConfigError, default_path, load
from smc_mixer_daemon.media import Media
from smc_mixer_daemon.runtime import run


def require(command: str) -> None:
    if shutil.which(command) is None:
        raise ConfigError(f"required executable not found in PATH: {command}")


def devices() -> None:
    require("pactl")
    snapshot = Audio().snapshot()
    for device in snapshot.devices.values():
        if device.kind == "source" and device.monitor:
            continue
        marker = " [default]" if snapshot.selected(device) else ""
        print(f"{device.kind}: {device.name}{marker}\n  {device.description}")
    if shutil.which("playerctl"):
        print("MPRIS players:")
        for player in Media("").players():
            print(f"  {player}")
    else:
        print("MPRIS discovery unavailable: playerctl is not installed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("devices", help="List audio devices and MPRIS players without changing state")
    for command in ("check", "run"):
        sub = commands.add_parser(command, help="Validate configuration" if command == "check" else "Run daemon")
        sub.add_argument("--config", type=Path, default=default_path())
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "devices":
            devices()
            return 0
        config = load(args.config)
        require("pactl")
        if config.player:
            require("playerctl")
        if args.command == "check":
            snapshot = Audio().snapshot()
            missing = [strip for strip in config.strips if (strip.kind, strip.name) not in snapshot.devices]
            for strip in missing:
                print(f"Unavailable: channel {strip.channel}, {strip.kind} {strip.name}")
            if config.player:
                print(f"Media player: {Media(config.player).resolve()}")
            availability = "; some devices are unavailable" if missing else "; assigned devices available"
            print("Configuration valid" + availability)
            return 1 if missing else 0
        stop = threading.Event()
        previous = {sig: signal.signal(sig, lambda _sig, _frame: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            run(args.config, config, stop)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    except ConfigError as exc:
        print(f"smc-mixer-daemon: {exc}", file=sys.stderr)
        return 2
    except (BackendError, OSError, RuntimeError) as exc:
        print(f"smc-mixer-daemon: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
