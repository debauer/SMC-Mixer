"""Remove the FWSC wrapper used by SincoOTA; this does not decrypt the firmware."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

BLOCK_SIZE = 48
BLOCK_DATA = 47
HEADER_BLOCKS = 20
HEADER_SIZE = BLOCK_SIZE * HEADER_BLOCKS
SIGNATURE = b"JLUFW"


def metadata(data: bytes, filename: str) -> tuple[str, int]:
    """Decode the 20 FWSC marker bytes using the input filename stem."""
    stem = Path(filename).stem.encode("ascii")
    markers = bytearray(data[index * BLOCK_SIZE + BLOCK_DATA] for index in range(HEADER_BLOCKS))
    for index in range(min(len(stem), HEADER_BLOCKS)):
        markers[index] = (markers[index] - index - 1) & 0xFF

    try:
        separator = markers.index(ord("_"))
    except ValueError:
        raise ValueError("FWSC header has no model/version separator") from None
    model_bytes = markers[:separator]
    if not model_bytes or any(byte < 0x20 or byte > 0x7E for byte in model_bytes):
        raise ValueError("FWSC header contains an invalid model name")
    version_digits = bytearray()
    for byte in markers[separator + 1 :]:
        if 0x30 <= byte <= 0x39:
            version_digits.append(byte)
        elif byte == ord("}"):
            break
    if not version_digits:
        raise ValueError("FWSC header contains no firmware version")
    model = model_bytes.decode("ascii")
    version = int(version_digits)
    parts = Path(filename).stem.rsplit("_", 1)
    if len(parts) != 2 or not parts[1].isdigit() or parts[0] != model or int(parts[1]) != version:
        raise ValueError("decoded model/version does not match the FWSC filename")
    return model, version


def unwrap(data: bytes, filename: str) -> tuple[str, int, bytes]:
    """Strip the 20 interleaved FWSC metadata bytes, leaving the UFW payload intact."""
    if len(data) < HEADER_SIZE:
        raise ValueError(f"FWSC file too short: {len(data)} bytes (need at least {HEADER_SIZE})")
    model, version = metadata(data, filename)
    payload = b"".join(data[offset : offset + BLOCK_DATA] for offset in range(0, HEADER_SIZE, BLOCK_SIZE))
    payload += data[HEADER_SIZE:]
    if SIGNATURE not in payload:
        raise ValueError("unwrapped payload has no JLUFW signature; input filename may not match FWSC metadata")
    return model, version, payload


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unpack(source: Path, destination: Path) -> tuple[str, int, bytes]:
    data = source.read_bytes()
    model, version, payload = unwrap(data, source.name)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "firmware.ufw").write_bytes(payload)
    manifest = {
        "source": source.name,
        "model": model,
        "version": version,
        "container_bytes": len(data),
        "container_sha256": sha256(data),
        "payload_file": "firmware.ufw",
        "payload_bytes": len(payload),
        "payload_sha256": sha256(payload),
        "jluwf_signature_offset": payload.index(SIGNATURE),
        "decrypted": False,
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return model, version, payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("firmware", type=Path, help="Input .fwsc file")
    parser.add_argument("--out", type=Path, help="New output directory (default: <file>.unpacked)")
    args = parser.parse_args()
    destination = args.out or args.firmware.with_name(args.firmware.name + ".unpacked")
    try:
        model, version, payload = unpack(args.firmware, destination)
    except (OSError, ValueError) as exc:
        print(f"unpack-fwsc: {exc}", file=sys.stderr)
        return 1
    print(f"Container: {args.firmware}")
    print(f"Model/version: {model}_{version:03d}")
    print(f"FWSC wrapper removed: {len(payload)} bytes")
    print(f"Output: {destination / 'firmware.ufw'}")
    print("Note: Firmware payload is unchanged and has not been decrypted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
