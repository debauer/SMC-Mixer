# USB and Sinco protocol reference

This page describes the device-tested transport and the protocol implemented in
`smc_mixer.sinco`. Physical controls and state-table offsets are in
[control.md](control.md); experiments and source investigations are retained in
[research/measurements.md](research/measurements.md).

## USB enumeration

| Field | Value |
|---|---|
| VID:PID | `4353:4b4d` |
| Manufacturer / product | `Jieli Technology` / `SINCO` |
| Interface name | `SMC-Mixer` |
| USB | 1.10, Full Speed, 100 mA |
| Linux driver | `snd-usb-audio`, class-compliant |

Interface 0 is Audio Control (class 01/01), with no endpoints. Interface 1 is
MIDI Streaming (class 01/03), with 64-byte bulk endpoints `0x04` OUT and `0x84`
IN. Class-specific descriptors expose two virtual MIDI cables per direction:
Embedded IN jacks 1/3 on OUT, and Embedded OUT jacks 8/10 on IN.

## Ports and routing

| ALSA port | Name | Raw MIDI identifier | Purpose |
|---|---|---|---|
| 0 | `SINCO SMC-Mixer-Private` | `hw:<card>,0,0` | Sinco name/version and memory requests/responses |
| 1 | `SINCO SMC-Mixer-Master` | `hw:<card>,0,1` | MCU/CC controls and LED commands |

The ALSA card number can change after enumeration. Resolve by name, as the
library does; `amidi -l` and `aseqdump -p SINCO:1` are useful diagnostics.
The Private-port name/version response was measured repeatedly and also works
through python-rtmidi/ALSA sequencer. Private can echo fader messages; the library
uses only SysEx there. Unsolicited passive listening does not produce a full
state dump.

Master emits Mackie Control in DAW mode and the default CC mapping in USER mode.
Except DAW faders (Pitch Bend channels 1-8), the measured mappings use MIDI
channel 1. A message's MCU/CC encoding does not establish the mode or Shift
state; mode is read separately. See [control mappings](control.md#device-modes-and-midi-encoding).

## Sinco packet

All multibyte fields are little-endian. Before USB 7-bit packing, the packet is:

| Offset | Length | Field |
|---|---|---|
| 0 | 2 | Header `00 59` |
| 2 | 1 | Command |
| 3 | 3 | Payload length |
| 6 | Variable | Payload |
| `6 + length` | 1 | `~sum(payload) & 0xff` |

This checksum is an 8-bit one's complement of the payload sum, not the CRC16
used in firmware containers. Responses use the same packet framing.

| Command | Meaning | Payload |
|---|---|---|
| `0x11` | Query name/version | Empty |
| `0x23` | Read memory | Type + address + length |
| `0x22` | Write memory, known from updater sources | Type + address + length + data |
| `0x00` | Error response observed on rejected reads | Example payload `01` |

The library exposes read-only generic memory access. The presence of a write
command in the source protocol does not make it a public `SmcMixer` write API.

## USB SysEx framing

```text
F0 | pack7_lsb(00 59 cmd payload_length[3] payload checksum) | F7
```

`pack7_lsb` treats the packet as an LSB-first bitstream and emits seven bits per
MIDI data byte. The initial encoded `00` is part of the packet, not a separate
manufacturer-ID field. The codec checks complete SysEx framing, packet header,
declared payload length, 7-bit input and checksum.

The empty name/version request is the verified reference vector:

```text
Raw packet: 00 59 11 00 00 00 FF
USB SysEx:  F0 00 32 45 00 00 00 40 7F F7
```

Firmware 011 responds on Private with command `0x11`, a 27-byte payload containing
`SMC-Mixer_011` followed by null bytes, and a valid checksum. The library decodes
the name up to the first null. The complete capture is in
[the archive](research/measurements.md#sinco-private-port-response).

## Read requests and responses

Request payload for command `0x23`:

| Field | Length |
|---|---|
| Memory type (`kind`) | 1 byte |
| Address | 4 bytes, little-endian |
| Requested byte length | 3 bytes, little-endian |

A successful response uses command `0x23` and starts with the exact eight-byte
request echo, followed by the data. The library validates the echo so serialized
read responses cannot be mistaken for a different address/length. Higher-level
state readers also validate their table sizes and firmware support.

The type survey tested `0x00`-`0x0f` at address 0:

| Type | Established interpretation |
|---|---|
| `0x00` | Stable data; flash-related view suspected, full mapping/extent not established |
| `0x04` | Live RAM/state; known mode, fader and USER knob tables |
| `0x05` | Configuration; prefix matches MidiSuite's `assets/bin/SSM.bin` |
| Other tested types | Error command `0x00`, payload `01` |

These observations do not establish every address or all 256 possible types.
Protocol addresses are region offsets, not automatically absolute CPU addresses.
State tables are documented once in [control.md](control.md#state-tables).

Use small targeted reads. A historical 4 KB type-4 scan caused a brief USB loss;
the guided tools use 32-byte requests. See [diagnostic usage](tools.md#device-diagnostics)
for current commands and [the archive](research/measurements.md#fader-table-discovery)
for the original broad-scan experiment and uncertainty about its cause.

## Sources and unresolved questions

Packet fields came from [SincoOTA source analysis](research/vendor-sinco-ota.md).
USB packing was corroborated by the M-UPGRADE self-test and its packet function
at `0x1400134b0`; see [M-UPGRADE research](research/vendor-m-upgrade.md).
`tests/test_sinco_codec.py` checks the reference vector and captured reply.

Unknowns include the full type-0 address map, meanings of other memory fields,
and exact device commands for entering update mode. The failed historical probe
run does not contradict the independently working Private-port request; its
cause remains unisolated in [measurement history](research/measurements.md#unsuccessful-initial-value-queries).
