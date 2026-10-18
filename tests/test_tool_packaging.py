"""Package boundaries and preserved command entry points, without MIDI hardware."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from smc_mixer_tools.diagnostics.amidi import MidiParser

ROOT = Path(__file__).resolve().parents[1]


def test_library_import_does_not_load_tools_or_curses(tmp_path: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, smc_mixer; "
            "assert not any(n.startswith('smc_mixer_tools') for n in sys.modules); "
            "assert 'curses' not in sys.modules",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )


def test_tools_import_without_root_script_modules(tmp_path: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "import smc_mixer_tools.monitor.app, smc_mixer_tools.probes.mode, smc_mixer_tools.probes.knobs; "
            "import smc_mixer_tools.diagnostics.mcu, smc_mixer_tools.diagnostics.probe; "
            "assert not {'mcu_tool', 'mcu_names', 'sinco_tool', 'sinco_codec', 'probe_smc'} & sys.modules.keys()",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )


def test_no_python_scripts_remain_in_repository_root() -> None:
    assert list(ROOT.glob("*.py")) == []


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("mcu-tool", ["--help"]),
        ("sinco-tool", ["--help"]),
        ("probe-smc", ["--dry-run"]),
        ("probe-knobs", ["--help"]),
        ("probe-mode", ["--help"]),
        ("unpack-fwsc", ["--help"]),
        ("analyze-fwsc", ["--help"]),
    ],
)
def test_console_entry_points_work_from_another_directory(command: str, args: list[str], tmp_path: Path) -> None:
    executable = shutil.which(command)
    assert executable is not None
    result = subprocess.run(
        [executable, *args],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "usage:" in result.stdout or command == "probe-smc"


def test_extracted_midi_parser_preserves_running_status_and_realtime() -> None:
    parser = MidiParser()
    assert parser.feed(bytes.fromhex("90 10 7f 11 00")) == [bytes.fromhex("90 10 7f"), bytes.fromhex("90 11 00")]
    assert parser.feed(bytes.fromhex("f0 00")) == []
    assert parser.feed(bytes.fromhex("f8 32 45 f7")) == [b"\xf8", bytes.fromhex("f0 00 32 45 f7")]
