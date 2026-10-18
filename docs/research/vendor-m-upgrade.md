# Manufacturer tool: M-UPGRADE 3.10.1414 (Flutter, Windows)

Status: 2026-10-17. Static analysis only (unpacked, `strings`, `xxd`,
`objdump -h`); not run. This is the historical source investigation;
the current device-tested protocol is in [usb-midi.md](../usb-midi.md).

| File | SHA-256 |
|---|---|
| `vendor/M-UPGRADE.zip` | `0452d85c0abffdee5c3af376890c15f32ce8c830f82dd8c94cfc891bfa102e15` |

## Architecture

Firmware update tool only. Flutter UI (`m_upgrade.exe`, Dart package
`m_upgrade` with `protocol/ota_firmware.dart`, `transport/upgrade_backend.dart`,
`data/upgrade_controller.dart`) plus native core `upgrade_core.exe` (C++,
Windows Kernel Streaming for MIDI, `HID.DLL` for HID).

## Firmware is not included [verified]

- No firmware file in the assets (only `assets/branding/app_icon.png`).
- No download URL in the code; the tool uses `file_selector` and expects a user-selected file (strings `.ota`, `firmware.bin`, "No firmware selected", "Connect a device and select OTA firmware to start").
- No appended data block: each executable's size matches the sum of its PE sections. `.rsrc` in `m_upgrade.exe` contains only icons (PNG) and a manifest; `.rsrc` in `upgrade_core.exe` is 480 bytes.
- Firmware must therefore be obtained separately from the manufacturer.

## Update sequence (inferred from error messages) [assumption]

1. Device is detected as USB-MIDI and communicated with over SysEx via Windows Kernel Streaming ("OTA-MIDI"). Multi-port devices trigger "KS multi-port device is not supported" in the KS path; how M-UPGRADE handles the SMC-Mixer's two ports is unknown.
2. Model and version are checked (can be skipped with "Force upgrade").
3. Device reboots into a HID loader ("Waiting for the device to restart and detecting its MIDI / HID upgrade interface"), timeout 45 s.
4. Mutual authentication over HID ("RCSP authentication reset", challenge, "reverse authentication"). The core contains an embedded key table; its contents are intentionally not reproduced here.
5. Transfer: device requests blocks (command `E5` with sequence number, offset and length); `E1` provides firmware information. This matches the Jieli RCSP OTA scheme (see `CMD_OTA_SEND_FIRMWARE_UPDATE_BLOCK` in [vendor-sinco-ota.md](vendor-sinco-ota.md)).
6. Device reboots and model/version are checked again, timeout 90 s.

According to error strings, the `.ota` format contains model, version and
metadata ("Invalid OTA firmware: missing or incomplete model, version, or
metadata", "The firmware model does not match the device"). "FWSC firmware"
may be a format marker [assumption].

## Sinco protocol SysEx framing [verified]

Recovered by disassembling `upgrade_core.exe`. The self-test following
"MIDI query golden vector failed" (string VA `0x1400384C0`, check at
`0x1400175F2`) constructs the reference value at `0x14001740F`:

```
F0 00 32 45 00 00 00 40 7F F7
```

It compares this with the packet function output at `0x1400134B0`, called with
command `0x11` and an empty payload. The function writes header `00 59`
(`mov WORD PTR [rsp+0x30],0x5900`).

Packing the Sinco packet `00 59 11 00 00 00 FF` (format in
[vendor-sinco-ota.md](vendor-sinco-ota.md)) into LSB-first 7-bit groups and
framing it with `F0 ... F7` produces the exact reference vector:

```
USB-MIDI-SysEx = F0 | pack7_lsb(00 59 cmd len[3] payload checksum) | F7
```

There is no manufacturer ID; the first data byte `00` is part of the packed
header. Implemented in `smc_mixer.sinco` and tested against the reference vector
in `tests/test_sinco_codec.py`.

The responding port was unknown from this binary alone; later device tests
confirmed the Private port (see [measurements](measurements.md#sinco-private-port-response)). Other
specific SysEx frames (e.g. to enter update mode) were not found as byte
sequences in the binary. No RCSP magic `FE DC BA` was found.

## Relevance

- Provides USB framing for the Sinco configuration protocol (see above).
- Confirms a MIDI-triggered update mode exists, the main risk of `uv run probe-smc --scan`.
- Capturing an update would expose the OTA-MIDI protocol, but requires firmware and risks a failed update. Not recommended.
