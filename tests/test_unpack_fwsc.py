"""Offline tests of the FWSC metadata wrapper, without decryption or hardware."""

import json
from pathlib import Path

import pytest

from smc_mixer_tools.firmware.unpack_fwsc import HEADER_BLOCKS, HEADER_SIZE, metadata, sha256, unpack, unwrap


def make_fwsc(name: str = "SMC-Mixer_013") -> tuple[bytes, bytes]:
    stem = Path(name).stem.encode("ascii")
    markers = bytearray(b"@" * HEADER_BLOCKS)
    for index, char in enumerate(stem):
        markers[index] = (char + index + 1) & 0xFF
    header_data = b"".join(bytes([index]) * 47 for index in range(HEADER_BLOCKS))
    tail = bytearray((index * 37 + 11) & 0xFF for index in range(300))
    tail[170:175] = b"JLUFW"
    wrapper = b"".join(
        header_data[index * 47 : (index + 1) * 47] + bytes([markers[index]]) for index in range(HEADER_BLOCKS)
    )
    return wrapper + tail, header_data + bytes(tail)


def test_metadata_and_payload_are_unwrapped_without_modifying_payload() -> None:
    data, expected = make_fwsc()
    assert len(data) == HEADER_SIZE + 300
    assert len(expected) == HEADER_SIZE - HEADER_BLOCKS + 300
    assert metadata(data, "SMC-Mixer_013.fwsc") == ("SMC-Mixer", 13)
    assert unwrap(data, "SMC-Mixer_013.fwsc") == ("SMC-Mixer", 13, expected)


def test_unwrap_rejects_truncated_headers_missing_signature_and_mismatched_name() -> None:
    data, _ = make_fwsc()
    with pytest.raises(ValueError, match="too short"):
        unwrap(data[: HEADER_SIZE - 1], "SMC-Mixer_013.fwsc")
    with pytest.raises(ValueError, match="does not match"):
        unwrap(data, "incorrect_013.fwsc")
    no_signature, _ = make_fwsc()
    damaged = no_signature[:HEADER_SIZE] + b"not a firmware image"
    with pytest.raises(ValueError, match="no JLUFW"):
        unwrap(damaged, "SMC-Mixer_013.fwsc")


def test_unpack_writes_payload_and_hash_manifest_without_overwriting(tmp_path: Path) -> None:
    data, expected = make_fwsc()
    source = tmp_path / "SMC-Mixer_013.fwsc"
    source.write_bytes(data)
    output = tmp_path / "unpacked"
    model, version, payload = unpack(source, output)
    assert (model, version, payload) == ("SMC-Mixer", 13, expected)
    assert (output / "firmware.ufw").read_bytes() == expected
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["container_sha256"] == sha256(data)
    assert manifest["payload_sha256"] == sha256(expected)
    assert manifest["jluwf_signature_offset"] == 940 + 170
    assert manifest["decrypted"] is False
    with pytest.raises(FileExistsError):
        unpack(source, output)
