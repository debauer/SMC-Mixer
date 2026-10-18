"""Use the local JieLi toolchain to emit a PI32v2 listing for a flat app image."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULT_TOOLCHAIN = Path("build/jieli-linux-toolchains-20260730.1/pi32v2/bin")
DEFAULT_BASE = 0x1E00000
DEFAULT_ENTRY = 0x1E00120


def validate_addresses(base: int, entry: int, size: int) -> None:
    if base < 0 or entry < base or entry >= base + size:
        raise ValueError("entry must point inside the image's mapped address range")
    if base + size > 0x100000000:
        raise ValueError("mapped image exceeds the 32-bit address space")


def assembly_source(image: Path) -> str:
    escaped = str(image.resolve()).replace("\\", "\\\\").replace('"', '\\"')
    return f'.section .text\n.global firmware_image\nfirmware_image:\n.incbin "{escaped}"\n'


def _run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, input=input_text, text=True, capture_output=True, check=False)
    if result.returncode:
        details = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{shlex.join(command)} failed: {details}")
    return result


def disassemble(image: Path, output: Path, toolchain: Path, base: int, entry: int) -> None:
    contents = image.read_bytes()
    validate_addresses(base, entry, len(contents))
    tools = {name: toolchain / name for name in ("clang", "ld", "objdump")}
    missing = [str(path) for path in tools.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"PI32v2 toolchain command(s) missing: {', '.join(missing)}")
    output.mkdir(parents=True, exist_ok=False)
    object_file = output / "app_blob.o"
    elf_file = output / "app_image.elf"
    listing = output / "app.disasm"
    compile_command = [
        str(tools["clang"]),
        "-target",
        "pi32v2",
        "-mcpu=r3",
        "-x",
        "assembler",
        "-c",
        "-o",
        str(object_file),
        "-",
    ]
    _run(compile_command, input_text=assembly_source(image))
    link_command = [str(tools["ld"]), f"-Ttext=0x{base:X}", "-e", f"0x{entry:X}", "-o", str(elf_file), str(object_file)]
    _run(link_command)
    disassemble_command = [
        str(tools["objdump"]),
        "-d",
        "--triple=pi32v2",
        "--mcpu=r3",
        "--print-imm-hex",
        str(elf_file),
    ]
    with listing.open("w", encoding="utf-8") as stream:
        result = subprocess.run(disassemble_command, stdout=stream, stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{shlex.join(disassemble_command)} failed: {result.stderr.strip()}")
    metadata = {
        "input": str(image),
        "input_bytes": len(contents),
        "input_sha256": hashlib.sha256(contents).hexdigest(),
        "architecture": "PI32v2",
        "cpu": "r3",
        "load_base": base,
        "entry_candidate": entry,
        "entry_source": "app_area_head offset field; not runtime-verified",
        "commands": [compile_command, link_command, disassemble_command],
        "listing": listing.name,
        "note": "Static disassembly only; unknown instructions and compressed/encrypted regions may remain.",
    }
    (output / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path, help="SFC-descrambled flat app.bin")
    parser.add_argument("--toolchain", type=Path, default=DEFAULT_TOOLCHAIN, help="pi32v2/bin directory")
    parser.add_argument("--base", type=lambda value: int(value, 0), default=DEFAULT_BASE, help="ELF load base")
    parser.add_argument(
        "--entry", type=lambda value: int(value, 0), default=DEFAULT_ENTRY, help="entry-point candidate"
    )
    parser.add_argument("--out", type=Path, help="new output directory (default: <app>.pi32v2)")
    args = parser.parse_args()
    output = args.out or args.app.with_name(args.app.name + ".pi32v2")
    try:
        disassemble(args.app, output, args.toolchain, args.base, args.entry)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"disassemble-pi32v2: {exc}", file=sys.stderr)
        return 1
    print(f"PI32v2/r3 listing: {output / 'app.disasm'}")
    print(f"ELF image: {output / 'app_image.elf'}")
    print("The candidate entry point and decoded listing still need manual validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
