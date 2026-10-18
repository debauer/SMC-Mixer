"""Offline UFW/Jieli static analyzer and conservative extractor."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from smc_mixer_tools.firmware.ufw import (
    Entry,
    app_files,
    crc16,
    decode_chipkey,
    parse_bank_headers,
    parse_flash_header,
    parse_ufw,
    top_entries,
)
from smc_mixer_tools.firmware.unpack_fwsc import unwrap


def safe_name(name: str) -> str:
    if not name or Path(name).name != name or name in (".", ".."):
        raise ValueError(f"unsafe firmware filename {name!r}")
    return name


def _file_record(entry: Entry, contents: bytes | None) -> dict[str, object]:
    return {
        "name": entry.name,
        "offset": entry.offset,
        "size": entry.size,
        "crc_valid": entry.crc_valid,
        "sha256": hashlib.sha256(contents).hexdigest() if contents is not None else None,
        "extracted": contents is not None,
    }


def analyze(payload: bytes, destination: Path) -> dict[str, Any]:
    ufw_info, ufw_entries = parse_ufw(payload)
    main_entry = next((entry for entry in ufw_entries if entry.kind == 0), None)
    if main_entry is None or not main_entry.crc_valid:
        raise ValueError("UFW flash.bin entry missing or CRC-invalid")
    flash = bytearray(payload[main_entry.offset : main_entry.offset + main_entry.size])
    flash_info = parse_flash_header(flash)
    files = top_entries(flash)
    config = next((entry for entry in files if entry.name == "isd_config.ini"), None)
    boot = next((entry for entry in files if entry.name == "uboot.boot"), None)
    app_head = next((entry for entry in files if entry.name == "app_dir_head"), None)
    if config is None or boot is None or app_head is None:
        raise ValueError("flash.bin lacks uboot.boot, isd_config.ini or app_dir_head")
    if config.offset > len(flash) or config.size > len(flash) - config.offset:
        raise ValueError("isd_config.ini exceeds flash.bin")
    config_data = flash[config.offset : config.offset + config.size]
    if len(config_data) < 34:
        raise ValueError("isd_config.ini is too short for a chipkey record")
    key_blob, key_crc = struct.unpack_from("<32sH", config_data)
    if crc16(key_blob) != key_crc:
        raise ValueError("isd_config.ini chipkey CRC mismatch")
    chipkey = decode_chipkey(key_blob)
    banks = parse_bank_headers(flash, boot)
    applications = app_files(flash, app_head, chipkey)

    destination.mkdir(parents=True, exist_ok=False)
    output = destination / "files"
    for category in ("ufw", "flash", "boot", "application"):
        (output / category).mkdir(parents=True)
    extracted_files = []

    def save(category: str, name: str, contents: bytes, crc_valid: bool | None) -> None:
        filename = safe_name(name)
        path = Path(category) / filename
        target = output / path
        target.write_bytes(contents)
        extracted_files.append(
            {
                "file": path.as_posix(),
                "bytes": len(contents),
                "crc_valid": crc_valid,
                "sha256": hashlib.sha256(contents).hexdigest(),
            }
        )

    # Preserve every bounded UFW entry, including entries whose raw CRC uses another transform.
    for entry in ufw_entries:
        if entry.offset <= len(payload) and entry.size <= len(payload) - entry.offset:
            save(
                "ufw",
                f"{entry.index:02d}_{entry.name or 'unnamed.bin'}",
                payload[entry.offset : entry.offset + entry.size],
                entry.crc_valid,
            )
    # Save physical top-level flash files, including protected/config files as raw bytes.
    for entry in files:
        if entry.offset <= len(flash) and entry.size <= len(flash) - entry.offset:
            save("flash", entry.name, bytes(flash[entry.offset : entry.offset + entry.size]), entry.crc_valid)

    bank_records = []
    for bank in banks:
        name = f"uboot-bank{bank['index']}.bin"
        contents = bank.pop("data")
        save("boot", name, contents, bank["data_crc_valid"])
        bank_records.append({**bank, "file": name, "sha256": hashlib.sha256(contents).hexdigest()})
    application_records = []
    for entry, contents in applications:
        if contents is not None:
            save("application", entry.name, contents, entry.crc_valid)
        application_records.append(_file_record(entry, contents))
    report: dict[str, object] = {
        "container_payload_bytes": len(payload),
        "container_payload_sha256": hashlib.sha256(payload).hexdigest(),
        "ufw": ufw_info,
        "ufw_entries": [asdict(entry) for entry in ufw_entries],
        "flash_header": flash_info,
        "flash_image": {"name": main_entry.name, "bytes": main_entry.size, "crc_valid": main_entry.crc_valid},
        "chipkey_record_crc_valid": True,
        "boot_banks": bank_records,
        "application_files": application_records,
        "extracted_files": extracted_files,
        "decryption_scope": "UFW/ENC/SFC metadata descrambled; payload code may remain compressed or encrypted",
    }
    (destination / "analysis.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Statically parse/extract a Jieli UFW inside a FWSC container")
    parser.add_argument("firmware", type=Path, help="input .fwsc or unpacked .ufw")
    parser.add_argument("--out", type=Path, help="new output directory (default: <input>.analysis)")
    args = parser.parse_args()
    destination = args.out or args.firmware.with_name(args.firmware.name + ".analysis")
    try:
        source = args.firmware.read_bytes()
        if args.firmware.suffix.lower() == ".fwsc":
            model, version, payload = unwrap(source, args.firmware.name)
            print(f"FWSC wrapper: {model} {version}")
        else:
            payload = source
        report = analyze(payload, destination)
    except (OSError, ValueError, struct.error) as exc:
        print(f"analyze-fwsc: {exc}", file=sys.stderr)
        return 1
    header = report["flash_header"]
    print(f"UFW chip: {report['ufw']['chip']}; flash product: {header['product']}")
    print(f"UFW entries: {report['ufw']['entry_count']}; flash header CRC: valid")
    print(
        f"Boot banks: {len(report['boot_banks'])}; CRCs: {all(bank['data_crc_valid'] for bank in report['boot_banks'])}"
    )
    print(f"Descrambled application files: {len(report['application_files'])}")
    for entry in report["application_files"]:
        status = (
            "not extracted" if entry["crc_valid"] is None else "CRC valid" if entry["crc_valid"] else "CRC mismatch"
        )
        print(f"  {entry['name']}: {entry['size']} bytes, {status}")
    print(f"Files extracted: {len(report['extracted_files'])}")
    print(f"Output: {destination}")
    print("Analysis is static; executable code may still be compressed or encrypted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
