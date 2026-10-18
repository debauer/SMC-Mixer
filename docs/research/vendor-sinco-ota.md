# Manufacturer tool: SincoOTA (Android APK)

Status: 2026-10-17. Static analysis only (unpacked, `strings` on `classes*.dex`
and `AndroidManifest.xml`); not installed or run. No decompiler was used for the
initial survey.

Historical source investigation. Current USB framing and device-tested memory
tables are documented in [usb-midi.md](../usb-midi.md) and [control.md](../control.md).

| Analyzed file (manufacturer download, not distributed here) | SHA-256 |
|---|---|
| `SincoOTA.apk` | `449a1c62cac9689cfa96467297738869e01bf94b3e627e158abede223c2cb658` |

## Architecture [verified]

- Package `com.sinco.ota`, Kotlin with Jetpack Compose, Android Gradle Plugin 8.3.1.
- App code: `com.sinco.ota.data.BleManager`, `OTAManager`, `SendBleDataThread`, `com.sinco.ota.utils.BluetoothPacketTools`, `FilterDevice`, `presentation.BluetoothViewModel`.
- Embedded Jieli SDK `com.jieli.jl_bt_ota` (232 classes: `model/command`, `model/response`, `cmdHandler`, `interfaces/rcsp`, etc.).
- Native library `lib/*/libjl_ota_auth.so` (arm64-v8a, armeabi-v7a): Jieli authentication.
- Permissions include `BLUETOOTH_SCAN`, `BLUETOOTH_CONNECT`, `ACCESS_FINE_LOCATION`, `READ_EXTERNAL_STORAGE`, `WRITE_EXTERNAL_STORAGE` and `INTERNET`.

## Firmware is not included [verified]

- No firmware in `assets/` (only `dexopt/baseline.prof*`) or `res/` (largest file is a 105 KB JPEG).
- No download URL in the code (the only URL found is `goo.gle/compose-feedback`), despite requesting `INTERNET` permission.
- Firmware is selected using the Android document picker (`GET_CONTENT` / `OPEN_DOCUMENT`, type `*/*`).

## Transport: Bluetooth LE, not USB [verified]

| UUID | Meaning |
|---|---|
| `0000ae00-...` / `ae01` / `ae02` | Jieli RCSP service (standard in the Jieli SDK) [assumption: ae01 Write, ae02 Notify] |
| `0000ae40-...` / `ae41` / `ae42` | Second, custom service, probably for `readSincoData` / `writeSincoData` [assumption] |
| `00002902-...` | CCCD (enable notifications) |
| `00001101`, `0000110b`, `0000111e` | Classic Bluetooth (SPP, A2DP Sink, Handsfree) from the SDK |

The string table lists supported devices including `SMC-MIXER`/`SMC-Mixer`,
`SMC-PAD`, `SMC-PAD POCKET`, `SMK25`, `SMK25II`, `SMK25MINI`, `SMK25V2`,
`SMARTPAD`, `SMARTKEY25` and `SMARTCON`.

[Assumption] This suggests the SMC-Mixer has a BLE interface. This has not been
checked on the device.

## OTA sequence (Jieli SDK) [assumption]

Strings confirm the RCSP OTA scheme: `CMD_OTA_SEND_FIRMWARE_UPDATE_BLOCK`,
`CMD_OTA_GET_DEVICE_REFRESH_FIRMWARE_STATUS`, `parseTargetInfo` (firmware
version, mandatory-upgrade flag), `upgradeStep02: offset = %d, len = %d`,
`UPGRADE_RESULT_ENCRYPTED_KEY_NOT_MATCH` ("Key of the upgrade file does not
match") and `UPGRADE_RESULT_DOWNLOAD_BOOT_LOADER_SUCCESS`. Firmware files are
therefore bound to a device key.

## Sinco data protocol (service `ae40`)

| UUID | Role |
|---|---|
| `0000ae40-0000-1000-8000-00805F9B34FB` | `SINCO_BLE_SERVICE` |
| `0000ae41-...` | `SINCO_BLE_WRITE` (host -> device) |
| `0000ae42-...` | `SINCO_BLE_NOTIFICATION` (device -> host, enabled via CCCD `2902`) |
| `0000ae00-...` / `ae01` / `ae02` | `OTA_BLE_SERVICE` / `OTA_BLE_WRITE` / `OTA_BLE_NOTIFICATION` (Jieli SDK) |

Packet format (all multibyte fields little-endian):

```
Offset 0  00 59            Header (HEAD_ONE=0x00, HEAD_TWO=0x59)
Offset 2  cmd              0x11 CMD_QUERY_NAME_VER, 0x23 CMD_READ, 0x22 CMD_WRITE
Offset 3  len (3 bytes)    Payload length starting at offset 6
Offset 6  payload
End       checksum (1 byte) = ~(sum(payload)) & 0xFF
```

Payload by command:

| Command | Payload |
|---|---|
| `0x11` Name/Version | Empty; packet is `00 59 11 00 00 00 FF` |
| `0x23` Read | `type` (1) + `address` (4) + `length` (3), len = 8 |
| `0x22` Write | `type` (1) + `address` (4) + `length` (3) + data, len = 8 + data length |

Responses use the same format; the app checks the checksum from offset 6 to the
penultimate byte and returns the payload. The name/version response is expected
to be 34 bytes total. Up to three attempts are made, each with a 1 s wait.

In the app, only `0x11` (Name/Version) is actually called. `readSincoData` and
`writeSincoData` are implemented (`BluetoothViewModel`) but have no callers, so
valid `type` and `address` values are not documented there.

[Assumption] The address model (type + address + length) corresponds to MidiSuite
`Addr*Data` classes. It may be the same configuration protocol MidiSuite uses
over USB SysEx. USB SysEx framing (e.g. 7-bit encoding of checksum `0xFF`) was
unknown in the initial APK survey.

## Relevance

- Official phone-based firmware-update path; firmware must be obtained separately (see [firmware.md](../firmware.md)).
- **For fader initial values:** the read command and packet format are known. Valid `type`/`address` values and whether current fader positions are in readable memory were initially open; later USB experiments identified the firmware-011 tables in [control.md](../control.md).
