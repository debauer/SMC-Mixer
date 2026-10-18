# Firmware `SMC-Mixer_013.fwsc`

Status: 2026-10-17. Analyzed statically; the FWSC wrapper was removed, and the
UFW/Jieli ENC header and SFC app area were descrambled. Nothing was flashed.
The connected device reports firmware **011**; this separate **013** image does
not establish its runtime state-table addresses. See the
[tools guide](tools.md#offline-firmware-workflow) for extraction/disassembly commands
and [documentation index](README.md) for the other references.

| Analyzed file (not distributed with the repository) | Size | SHA-256 |
|---|---:|---|
| `SMC-Mixer_013.fwsc` | 479700 bytes | `113e8d4bdc9724fdb0eac4307a202eee827e4bec50578aa5ab4c28b76644c5f6` |

The user supplied the file; its source is undocumented. Authenticity has not
been verified because no manufacturer reference checksum is available.

## Findings [verified]

- Container: Jieli UFW. The `JLUFW` marker is at file offset `0x751C4`, followed by `0xFF` padding to EOF.
- Chip: Jieli **AC636N** (`AC636N_TRANS` at `0x424`); update-code string for `AC696x` (`AC696x_updatev` at `0x2B12E`); family `JL-BR22` (at `0x2DE44`).
- Readable bootloader/updater fragments include `2_3_0_0`, `PB05 RESET`, `PB01_00_0`, `UPDATE_JUMP` (3 occurrences), and `POWER_PIN`.
- The file has high entropy: 7.86 bits/byte overall, and 7.06–7.99 across 16 sections. Repeated byte patterns at the beginning suggest XOR scrambling across regions containing plaintext zeroes [assumption].
- Version `013` is taken from the filename.
- The `.fwsc` extension is consistent with the `FWSC firmware` string in M-UPGRADE; see [M-UPGRADE research](research/vendor-m-upgrade.md).

## Removing the FWSC wrapper

SincoOTA contains `Tools.unpackFWSCFile()`. It processes the first 20 blocks of
48 bytes, each containing 47 payload bytes and one obfuscated metadata byte.
After these 960 bytes, the remainder is appended unchanged. Model and version
are decoded from the metadata; the input filename must match them.

The `unpack-fwsc` helper performs only this wrapper-removal step. Its manifest
records hashes, sizes, decoded model/version and the `JLUFW` marker offset.

For this image the read-only parser reports model `SMC-Mixer`, version 13,
479700 input bytes and 479680 payload bytes. The resulting high-entropy Jieli
UFW payload is **not decrypted** by this step. It is not a complete firmware
dump or a flashable backup.

## UFW structure and static extraction

The `analyze-fwsc` tool validates the UFW header and file-list CRCs, descrambles Jieli ENC
headers and the SFC-protected app area, and writes bounded UFW entries plus
recognized flash, boot and app files under `files/ufw`, `files/flash`,
`files/boot` and `files/application` in a new `<file>.analysis/` directory.
Raw UFW entries are preserved even if their raw-byte CRC differs; the report
marks the status instead of discarding the data. `isd_config.ini` contains the
chipkey record and is extracted as a raw file. Keep the analysis directory
local. Entries whose logical address ranges do not fit in the available flash
image are listed in `analysis.json` with `extracted: false`. The tool does not
run updater code or communicate with the mixer.

### Results for `SMC-Mixer_013.fwsc` [CRC-verified]

- UFW chip ID: `AC636N`; flash product: `AC636N_TRANS`.
- UFW header CRC and the CRC for the nine-entry list are valid. Entries include
  `flash.bin` (0x24000 bytes), `ota.bin` (0x2ab1f bytes), `flash2.bin` (0x23b00
  bytes) and `isd_config.ini`.
- `flash.bin` and `flash2.bin` each have a valid data CRC. The descrambled flash
  header reports version `0.01`, flash size `0x7f000`, filesystem version 3 and
  block alignment 16.
- `isd_config.ini` contains a CRC-valid chipkey record. The decoded value is
  used internally for SFC descrambling and is not written to the report. The
  extracted `files/flash/isd_config.ini` contains the chipkey record in the
  firmware format; keep this directory local.
- The Jieli flash entry is named `uboot.boot` and contains one bank (3644 bytes,
  load address `0x12000`) with a valid bank-data CRC. Bank flag `0x40` indicates
  compression; the bank has not been decompressed. The filename does not prove
  this is Das U-Boot or a Linux bootloader; there is no such evidence here.
- The SFC-protected app area yields two CRC-valid files: `app.bin` (140220 bytes,
  SHA-256 `9b7e373ee2f96ab5988687ef643e2f904716964d04d6faf0e4684352e4d00139`)
  and `cfg_tool.bin` (864 bytes, SHA-256
  `338c782759b148a9c30556d579e11a799feab58571fae442acfd663250114291`).
- `cfg_tool.bin` contains `AC696X_1` through `AC696X_20`; this alone does not
  identify the target chip as AC696X. The UFW chip ID and flash product both
  indicate AC636N.
- `app.bin` contains strings including `SMC-Mixer_013`,
  `BTCTRLER-@20210611-$071ffa2`, `UPDATE-@20210611-$6db99fe`, `reset_io`,
  `sinco`, `JLAISDKsincoaudio` and `UARTUPDATE`. Its entropy is about 6.91
  bits/byte. The firmware is partly readable; the initial disassembly is described
  below, while compression/further encryption of remaining regions is unresolved.
- The Jieli `app_area_head` entry has offset value **`0x1e00120`**, interpreted
  by the format parser as an entry-point address. This is only a disassembly
  candidate; it has not been checked against a runtime capture or manufacturer
  ELF file.

The run extracts 14 files: nine bounded UFW entries (including `flash.bin`,
`flash2.bin`, `ota.bin`, `USRFLASH` and `isd_config.ini`), two physical files
from `flash.bin`, one UBOOT bank and two SFC-descrambled app files. Five other
logical app regions lie outside the available `flash.bin` image and are listed
as not extracted in the manifest.

For the raw UFW entries `USRFLASH`, `isd_config.ini`, `script.ver` and
`blimit.bin`, a simple CRC over raw bytes does not match the table value. Their
bytes are still preserved unchanged and marked as CRC mismatches; this is not
interpreted as corruption or as decrypted content.

**Interim conclusion:** The UFW container, flash file table, Jieli bootloader
bank and SFC app files have been structurally parsed and CRC-verified. The app
code has not been fully decrypted or analyzed.

## Architecture and disassembly

- Further protocol details may be in `app.bin`. The initial disassembly does not
  yet identify its protocol handlers or resolve all remaining regions.
- According to the official [Jieli AC63 SDK](https://github.com/Jieli-Tech/fw-AC63_BT_SDK),
  specifically its [BR25 Makefile](https://github.com/Jieli-Tech/fw-AC63_BT_SDK/blob/master/apps/spp_and_le/board/br25/Makefile),
  AC636N is built under platform BR25. The Makefile uses `-target pi32v2
  -mcpu=r3` and defines `__GCC_PI32V2__`. The target ISA is **Jieli PI32v2,
  CPU variant r3**: proprietary, not ARM or RISC-V.
- The SDK uses Jieli's LLVM/Clang-based toolchain. The [official Linux setup](https://doc.zh-jieli.com/Tools/zh-cn/dev_tools/toolchains/linux_lib.html)
  installs it under `/opt/jieli`; the SDK uses `/opt/jieli/pi32v2/bin/clang`,
  `objdump` and `objcopy`, plus a custom `lto-wrapper`.
- Ghidra support is available in Quarkslab's [Jieli PI32v2 processor module](https://github.com/quarkslab/ghidra-jieli).
  Its README says most PI32v2 instructions are covered, but some remain unknown.
  The [original implementation](https://github.com/kagaimiq/ghidra-jieli)
  describes PI32v2 support as early-stage. Parallel instructions, repeat blocks,
  conditional blocks and parts of the instruction semantics may limit analysis.
- For an initial Ghidra import, use `files/application/app.bin`, candidate entry
  `0x1e00120` and processor PI32v2. Verify load address and byte order against a
  known function and Jieli's `llvm-objdump` before drawing control-flow
  conclusions. `uboot-bank0.bin` is separate, marked compressed and has load
  address `0x12000`; it must be decompressed before disassembly.
- The toolchain archive `jieli-linux-toolchains-20260730.1.tar.xz` is downloaded separately
  and not distributed here; see the [offline firmware workflow](tools.md#offline-firmware-workflow)
  for extraction. `disassemble-pi32v2` uses its `pi32v2/bin/{clang,ld,objdump}`. The listing
  manifest records the exact commands.
- The initial static run produced an `ELF32-pi32v2` image and a 2.75 MB listing
  with 50161 decoded instruction records; 1096 are marked `<unknown instruction>`
  (about 2.18%). Some occur in an early table/vector-like region; the remainder
  has not been classified.
- Startup at base `0x1e00000` looks plausible: it initializes `sp` and `ssp` to
  `0x11200`, copies initialization regions, then jumps into the main code. This
  load base is plausible but has not been checked against the original ELF.
- At candidate entry `0x1e00120`, the listing decodes `r4 = r4 & 0xFFFFFF`; a
  stack prologue begins just before at `0x1e0010e`. In linear disassembly the
  candidate therefore falls in the middle of plausible control flow. Whether
  the base/entry is wrong, the device intentionally enters an existing block, or
  this is a disassembly artifact remains open. Treat the container value as a
  candidate only.

Flashing is not recommended without a specific need: SincoOTA indicates that
firmware files are bound to a device key (`UPGRADE_RESULT_ENCRYPTED_KEY_NOT_MATCH`),
the image source and authenticity are unverified, and a failed update could
render the device unusable.
