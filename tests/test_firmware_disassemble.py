"""Address validation and safe flat-binary assembly source generation."""

from pathlib import Path

import pytest

from smc_mixer_tools.firmware.disassemble import assembly_source, validate_addresses


def test_firmware_entry_candidate_is_inside_mapped_image() -> None:
    validate_addresses(0x1E00000, 0x1E00120, 140220)


@pytest.mark.parametrize(
    ("base", "entry", "size"),
    [(-1, 0, 4), (0x100, 0xFF, 4), (0x100, 0x104, 4), (0xFFFFFFF0, 0xFFFFFFF0, 32)],
)
def test_invalid_mappings_fail_before_running_a_toolchain(base: int, entry: int, size: int) -> None:
    with pytest.raises(ValueError):
        validate_addresses(base, entry, size)


def test_assembly_source_embeds_an_escaped_absolute_path(tmp_path: Path) -> None:
    image = tmp_path / 'app "firmware".bin'
    source = assembly_source(image)
    assert ".section .text" in source
    assert f".incbin {chr(34)}{str(image.resolve()).replace(chr(34), chr(92) + chr(34))}{chr(34)}" in source
