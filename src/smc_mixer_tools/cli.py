"""Command-line entry point."""

import argparse
import curses
import sys

from smc_mixer.errors import SmcMixerError
from smc_mixer_tools.monitor.app import run_monitor


def main() -> int:
    parser = argparse.ArgumentParser(prog="smc-mixer")
    commands = parser.add_subparsers(dest="command", required=True)
    monitor = commands.add_parser("monitor", help="Observe live mixer state without changing LEDs")
    monitor.add_argument("--demo", action="store_true", help="Run an animation without a connected mixer")
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(1, "smc-mixer: The monitor requires an interactive terminal.\n")
    try:
        run_monitor(demo=args.demo)
    except KeyboardInterrupt:
        return 0
    except (SmcMixerError, curses.error, RuntimeError, OSError) as exc:
        print(f"smc-mixer: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
