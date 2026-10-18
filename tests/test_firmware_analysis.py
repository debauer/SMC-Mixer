"""Pure-Python tests for UFW metadata parsing and Jieli header descrambling."""

import struct

import pytest

from smc_mixer_tools.firmware.ufw import Entry, app_files, crc16, jl_enc, jl_sfc, parse_flash_header, parse_ufw


def encrypted(data: bytes) -> bytes:
    result = bytearray(data)
    jl_enc(result, 0, len(result))
    return bytes(result)


def make_ufw() -> bytes:
    image = b"sample flash image"
    record = struct.pack(
        "<HHHHIII44s16s",
        0,
        0,
        crc16(image),
        0,
        144,
        len(image),
        len(image),
        b"".ljust(44, b"\0"),
        b"flash.bin\0".ljust(16, b"\0"),
    )
    entry = encrypted(record)
    header = bytearray(
        struct.pack(
            "<HHIHHI48s",
            0,
            crc16(entry),
            144 + len(image),
            1,
            4,
            0x200,
            b"AC636N\0".ljust(48, b"\0"),
        )
    )
    header[:2] = crc16(header[2:]).to_bytes(2, "little")
    return encrypted(bytes(header)) + entry + image


def test_parse_valid_ufw_header_entry_and_data_crc() -> None:
    info, entries = parse_ufw(make_ufw())
    assert info["chip"] == "AC636N"
    assert info["image_size"] == 144 + len(b"sample flash image")
    assert info["header_crc_valid"] and info["entry_list_crc_valid"]
    assert entries[0].name == "flash.bin"
    assert entries[0].crc_valid is True


def test_parse_rejects_corrupt_header_and_entry_list() -> None:
    data = bytearray(make_ufw())
    data[0] ^= 1
    with pytest.raises(ValueError, match="header CRC"):
        parse_ufw(bytes(data))
    data = bytearray(make_ufw())
    data[64] ^= 1
    with pytest.raises(ValueError, match="entry-list CRC"):
        parse_ufw(bytes(data))


def test_jieli_flash_header_decodes_only_after_crc_verified_descramble() -> None:
    raw_version = b"0.01"
    raw_product = b"AC636N_TRANS".ljust(16, b"\xff")
    keystream = bytearray(32)
    jl_enc(keystream, 0, len(keystream))
    version = bytes(a ^ b for a, b in zip(raw_version, keystream[4:8], strict=True))
    product = bytes(a ^ b for a, b in zip(raw_product, keystream[16:32], strict=True))
    header = bytearray(struct.pack("<HH4sIBBBB16s", 0, 0x220, version, 0x7F000, 3, 16, 0xA4, 0, product))
    header[:2] = crc16(header[2:]).to_bytes(2, "little")
    raw_header = bytearray(encrypted(bytes(header)))
    raw_header[4:8] = raw_version
    raw_header[16:32] = raw_product
    assert parse_flash_header(raw_header)["product"] == "AC636N_TRANS"
    damaged = bytearray(raw_header)
    damaged[10] ^= 1
    with pytest.raises(ValueError, match=r"flash\.bin header CRC"):
        parse_flash_header(damaged)


def test_app_area_sfc_decrypt_extracts_only_crc_validated_files() -> None:
    base, key, contents = 0x100, 0x980F, b"extracted firmware file"
    area_size = 0x100
    flash = bytearray(base + area_size)
    app_body = struct.pack("<HIIBBH16s", 0, 0, area_size, 0x83, 0, 0, b"app_area_head\0".ljust(16, b"\0"))
    app_header = struct.pack("<H30s", crc16(app_body), app_body)
    name = b"app.bin\0".ljust(16, b"\0")
    file_body = struct.pack("<HIIBBH16s", crc16(contents), 0x80, len(contents), 0x82, 0, 1, name)
    file_header = struct.pack("<H30s", crc16(file_body), file_body)
    flash[base : base + 32] = app_header
    flash[base + 32 : base + 64] = file_header
    flash[base + 0x80 : base + 0x80 + len(contents)] = contents
    jl_sfc(flash, base, area_size, base, key)  # The SFC operation is symmetric.
    directory = Entry(0, 0, 0, base, 0xFFFFFFFF, "app_dir_head")
    extracted = app_files(flash, directory, key)
    assert [(entry.name, data, entry.crc_valid) for entry, data in extracted] == [("app.bin", contents, True)]
