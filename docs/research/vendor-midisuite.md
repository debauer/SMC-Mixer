# Manufacturer tool: MidiSuite (Flutter, Windows)

Status: 2026-10-17. Static analysis only (unpacked, `strings`, `xxd`); not run.
Historical source investigation. See the current [USB/Sinco reference](../usb-midi.md)
and [control mapping](../control.md) for device-tested conclusions.

| File | SHA-256 |
|---|---|
| `vendor/MidiSuite_new.zip` | `c2c116f4610fb541f0e4b3b8e23ea52fb51a80ca9843be8667242622ad46b4cf` |

## Findings

- Explicit SMC-Mixer support: `package:musical_instruments/core/devices/smc_mixer/smc_mixer_data.dart`; UI under `desktop/pages/smc_mixer/`.
- Data classes: `SmcMixerSliderData`, `SmcMixerKnobData`, `SmcMixerBtnData`, `SmcMixerGlobData`, `SmcMixerSysData`, `SmcMixerUsrData`, `SmcMixerUsrListData`.
- UI strings indicate load/save configuration, factory reset, import/export and entry of a "System Code". It is a mapping/configuration editor; no live fader monitor was identified.
- Protocol modules: `core/usb/sysex_codec.dart` (`SysexCodec`), `core/usb/midi_transport.dart` (`MidiTransport`, `MidiPacket`), `core/data/addr_data.dart`.
- [Assumption] Classes `AddrBaseData`, `AddrUnsigned8Data`, `AddrSigned8Data` and `AddrUnsigned16Data` suggest address-based configuration reads and writes via SysEx.
- MIDI access uses `flutter_midi_command` (standard MIDI ports; probably the Private port).
- Firmware updates refer to SincoOTA ("Please use SincoOTA to update the device firmware"); see [vendor-sinco-ota.md](vendor-sinco-ota.md).
- SysEx byte constants are compiled Dart AOT constants in `data/app.so` and cannot be recovered with `strings`.
- `assets/rom1b/*.syx` are Yamaha DX7 voice banks (`F0 43 00 09 20 00`); `assets/bin/*.bin` are default configurations for other devices. Neither is relevant to this mixer.

## Relevance

MidiSuite is a likely source for the device-configuration read command. Whether
current fader positions are present in readable memory was initially unknown;
they were later found in the firmware-011 RAM table documented in
[control.md](../control.md).
