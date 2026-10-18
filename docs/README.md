# Documentation

Start with the [project README](../README.md) for installation and a minimal
library or monitor example.

## Usage guides

| Guide | Contents |
|---|---|
| [Library API](api.md) | Connections, cached state, explicit reads, events, LEDs, threads, errors and reconnect |
| [Tools](tools.md) | Live monitor, CLI diagnostics, guided measurements and offline firmware workflow |
| [Linux daemon](daemon.md) | Audio volume, mute, default-device routing, MPRIS transport and user service |

## Technical references

| Reference | Contents |
|---|---|
| [Hardware](control.md) | Physical controls, DAW/USER mappings, state tables, LEDs and limitations |
| [USB and Sinco protocol](usb-midi.md) | USB descriptors, port routing, packet fields, SysEx packing and memory reads |
| [Firmware](firmware.md) | Firmware 013 container structures, CRC findings, extraction results and PI32v2 architecture |

Runtime state tables were verified on device firmware **011**. The separate
firmware **013** image was analyzed offline; its contents do not establish
runtime addresses or hardware behavior on 013.

## Research archive

These notes retain experiment chronology, raw evidence, rejected hypotheses and
manufacturer-tool investigations. Use the technical references above for current
conclusions and supported behavior.

| Research | Contents |
|---|---|
| [Measurements](research/measurements.md) | MIDI captures, query experiments, mode/fader/knob discoveries, run IDs and LED tests |
| [MidiSuite](research/vendor-midisuite.md) | Windows configuration editor and address-based configuration protocol |
| [M-UPGRADE](research/vendor-m-upgrade.md) | Windows updater, MIDI/HID transport and USB packing reference vector |
| [SincoOTA](research/vendor-sinco-ota.md) | Android updater, FWSC wrapper and decompiled Sinco protocol |
