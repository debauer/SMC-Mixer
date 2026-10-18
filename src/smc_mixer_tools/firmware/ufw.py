"""Low-level Jieli UFW, JLFS, ENC and SFC structures with CRC validation."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

CRC_POLY = 0x1021
ENC_KEY = 0xFFFF
UFW_ENTRY_SIZE = 0x50
JLFS_HEADER_SIZE = 32


def crc16(data: bytes | bytearray, initial: int = 0) -> int:
    crc = initial
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ CRC_POLY) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def jl_enc(data: bytearray, offset: int, length: int, key: int = ENC_KEY) -> None:
    for index in range(length):
        data[offset + index] ^= key & 0xFF
        key = ((key << 1) ^ (CRC_POLY if key & 0x8000 else 0)) & 0xFFFF


def jl_sfc(data: bytearray, offset: int, length: int, base: int, key: int) -> None:
    for start in range(0, length, 32):
        size = min(32, length - start)
        block_offset = offset + start
        jl_enc(data, block_offset, size, key ^ ((block_offset - base) >> 2))


def decode_chipkey(blob: bytes) -> int:
    if len(blob) < 32:
        raise ValueError("chipkey blob is shorter than 32 bytes")
    total = sum(blob[:16]) & 0xFF
    threshold = 0xAA if total >= 0xE0 else 0x55 if total <= 0x10 else total
    return sum(1 << bit for bit in range(16) if (blob[16 + bit] ^ blob[15 - bit]) < threshold)


@dataclass
class Entry:
    kind: int
    index: int
    data_crc: int
    offset: int
    size: int
    name: str
    flags: int | None = None
    reserved: int | None = None
    crc_valid: bool | None = None


def parse_ufw(data: bytes) -> tuple[dict[str, Any], list[Entry]]:
    if len(data) < 64:
        raise ValueError("UFW is shorter than its 64-byte header")
    header = bytearray(data[:64])
    jl_enc(header, 0, 64)
    header_crc = int.from_bytes(header[:2], "little")
    if not header_crc or crc16(header[2:]) != header_crc:
        raise ValueError("UFW header CRC mismatch (unsupported format or wrong wrapper)")
    _, list_crc, image_size, count, word3, word4, raw_chip = struct.unpack("<HHIHHI48s", header)
    header_size = 64 + count * UFW_ENTRY_SIZE
    if image_size > len(data) or header_size > len(data):
        raise ValueError("UFW header declares data beyond end of file")
    raw_entries = bytearray(data[64:header_size])
    if crc16(raw_entries) != list_crc:
        raise ValueError("UFW entry-list CRC mismatch")
    entries = []
    for offset in range(0, len(raw_entries), UFW_ENTRY_SIZE):
        record = bytearray(raw_entries[offset : offset + UFW_ENTRY_SIZE])
        jl_enc(record, 0, len(record))
        kind, index, data_crc, reserved, address, size, _size2, _reserved2, raw_name = struct.unpack(
            "<HHHHIII44s16s", record
        )
        name = raw_name.split(b"\x00", 1)[0].decode("ascii", errors="replace")
        valid = address <= len(data) and size <= len(data) - address
        actual_crc = crc16(data[address : address + size]) if valid and size else 0
        entries.append(
            Entry(
                kind,
                index,
                data_crc,
                address,
                size,
                name,
                reserved=reserved,
                crc_valid=actual_crc == data_crc if valid else None,
            )
        )
    return {
        "chip": raw_chip.split(b"\x00", 1)[0].decode("ascii", errors="replace"),
        "image_size": image_size,
        "entry_count": count,
        "header_crc_valid": True,
        "entry_list_crc_valid": True,
        "reserved_words": [word3, word4],
    }, entries


def read_jlfs_entry(data: bytes | bytearray, offset: int, key: int = ENC_KEY) -> Entry:
    record = bytearray(data[offset : offset + JLFS_HEADER_SIZE])
    if len(record) != JLFS_HEADER_SIZE:
        raise ValueError(f"truncated JLFS entry at 0x{offset:X}")
    jl_enc(record, 0, JLFS_HEADER_SIZE, key)
    stored_crc, body = struct.unpack("<H30s", record)
    if crc16(body) != stored_crc:
        raise ValueError(f"JLFS entry header CRC mismatch at 0x{offset:X}")
    data_crc, address, size, flags, reserved, index, raw_name = struct.unpack("<HIIBBH16s", body)
    return Entry(
        0,
        index,
        data_crc,
        address,
        size,
        raw_name.split(b"\x00", 1)[0].decode("ascii", errors="replace"),
        flags=flags,
        reserved=reserved,
    )


def parse_flash_header(data: bytes | bytearray) -> dict[str, Any]:
    header = bytearray(data[:32])
    jl_enc(header, 0, len(header))
    stored_crc = int.from_bytes(header[:2], "little")
    if not stored_crc or crc16(header[2:]) != stored_crc:
        raise ValueError("flash.bin header CRC mismatch")
    _, burner_size, version, flash_size, fs_version, alignment, reserved, options, product = struct.unpack(
        "<HH4sIBBBB16s", header
    )
    # Printable VID/PID values are stored alongside their scrambled header copies.
    raw = data[:32]
    header[4:8] = raw[4:8]
    header[16:32] = raw[16:32]
    _, _, version, flash_size, fs_version, alignment, reserved, options, product = struct.unpack(
        "<HH4sIBBBB16s", header
    )
    return {
        "header_crc_valid": True,
        "burner_size": burner_size,
        "version": version.decode("ascii", errors="replace"),
        "flash_size": flash_size,
        "filesystem_version": fs_version,
        "block_alignment": alignment,
        "reserved": reserved,
        "options": options,
        "product": product.split(b"\x00", 1)[0].rstrip(b"\xff").decode("ascii", errors="replace"),
    }


def top_entries(flash: bytearray) -> list[Entry]:
    entries = []
    offset = 32
    for _ in range(256):
        entry = read_jlfs_entry(flash, offset)
        data_offset = entry.offset
        if data_offset <= len(flash) and entry.size <= len(flash) - data_offset:
            entry.crc_valid = crc16(flash[data_offset : data_offset + entry.size]) == entry.data_crc
        entries.append(entry)
        offset += JLFS_HEADER_SIZE
        if entry.index:
            return entries
    raise ValueError("top-level JLFS has no terminating entry")


def parse_bank_headers(flash: bytearray, boot: Entry) -> list[dict[str, Any]]:
    banks = []
    count = 1
    for index in range(min(boot.size // 16, 32)):
        offset = boot.offset + index * 16
        record = bytearray(flash[offset : offset + 16])
        jl_enc(record, 0, len(record))
        header, stored_crc = struct.unpack("<14sH", record)
        if crc16(header) != stored_crc:
            raise ValueError(f"boot bank header CRC mismatch at 0x{offset:X}")
        bank_count, size, load_address, bank_offset, data_crc = struct.unpack("<HHIIH", header)
        if index == 0:
            count = bank_count
        start = boot.offset + bank_offset
        if start > len(flash) or size > len(flash) - start:
            raise ValueError(f"boot bank {index} exceeds flash.bin")
        image = bytearray(flash[start : start + size])
        jl_enc(image, 0, len(image))
        banks.append(
            {
                "index": index,
                "size": size,
                "load_address": load_address,
                "offset": start,
                "data_crc_valid": crc16(image) == data_crc,
                "data": bytes(image),
            }
        )
        if index + 1 >= count:
            return banks
    raise ValueError("boot bank table is truncated")


def app_files(flash: bytearray, app_head: Entry, chipkey: int) -> list[tuple[Entry, bytes | None]]:
    base = app_head.offset
    first = bytearray(flash[base : base + JLFS_HEADER_SIZE])
    jl_sfc(first, 0, len(first), 0, chipkey)
    stored_crc, body = struct.unpack("<H30s", first)
    if crc16(body) != stored_crc:
        raise ValueError("app-area header CRC mismatch")
    _crc, _address, area_size, flags, _reserved, _index, raw_name = struct.unpack("<HIIBBH16s", body)
    name = raw_name.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    if name != "app_area_head" or flags != 0x83 or area_size < 32 or area_size > len(flash) - base:
        raise ValueError("unsupported app-area directory header")
    jl_sfc(flash, base + 32, area_size - 32, base, chipkey)
    found: list[tuple[Entry, bytes | None]] = []
    offset = base + JLFS_HEADER_SIZE
    for _ in range(256):
        entry = read_jlfs_entry(flash, offset, key=0)  # SFC decoding already decrypted this area.
        offset += JLFS_HEADER_SIZE
        start = base + entry.offset
        if start <= len(flash) and entry.size <= len(flash) - start:
            contents = bytes(flash[start : start + entry.size])
            entry.crc_valid = crc16(contents) == entry.data_crc
            found.append((entry, contents))
        else:
            found.append((entry, None))
        if entry.index:
            return found
    raise ValueError("app directory has no terminating entry")
