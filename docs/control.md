# Hardware reference

Current device findings apply to running firmware **`SMC-Mixer_011`**. The
separately supplied firmware **013** was analyzed offline; it does not verify
these runtime addresses on 013. See [measurement history](research/measurements.md)
for captures, experiments and provenance, and [api.md](api.md) for library behavior.

## Physical controls

There are eight channel strips, each with a knob, fader and four buttons beside
the fader. Button order from top to bottom is **MUTE, SOLO, REC, SELECT**. Their
icons are M, S, R and an empty square.

The bottom row has eleven buttons, listed below. The second button has a
**Pause** icon but sends MCU **STOP**, note 93. The two oval buttons beside knob
8 are identified as Bluetooth (upper) and Shift (lower). No dedicated Shift
MIDI message or independently verified Shift state is available.

## Device modes and MIDI encoding

| Mode | Faders | Knobs | Buttons |
|---|---|---|---|
| DAW | Pitch Bend, channels 1-8 | CC 16-23, relative steps | MCU notes |
| USER | CC 40-47, absolute 0-127 | CC 30-37, absolute 0-127 | Default CC mapping below |

DAW/USER was confirmed by manually switching modes **without Shift**. A CC
message alone therefore does not show that Shift is held. The library reports
the read device mode separately from a message's MCU/CC protocol.

In DAW, each clockwise knob step sends value 1; counter-clockwise sends 65.
Only single steps were observed, including fast turns. The decoder also accepts
the MCU step ranges 1-15 and 65-79. Knob presses produced no message; MCU notes
32-39 expected for V-Pot pushes were not observed.

Physical fader resolution is 7 bits. DAW Pitch Bend has a zero low byte and a
high byte equal to position 0-127; raw value is `position << 7`, with top
position 16256 (`0x3f80`). At USER knob minimum, turning further left produced
no message. Absolute values are device-maintained counters, not physical dial angles.

## DAW channel mapping

Buttons send Note On velocity 127 when pressed and Note Off or Note On velocity
0 when released. Except faders, messages use MIDI channel 1.

| Control | Channel 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| MUTE note | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 |
| SOLO note | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 |
| REC note | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
| SELECT note | 24 | 25 | 26 | 27 | 28 | 29 | 30 | 31 |
| Knob CC | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 |
| Fader Pitch Bend channel | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |

## USER channel mapping

The default CC mapping is captured in the older button/control sequence and
confirmed by later USER-mode fader, MUTE and knob tests. Earlier notes called
this a Shift layer; that interpretation has been superseded. Buttons use
127 pressed / 0 released; continuous controls use absolute 0-127. MIDI channel 1.

| Control | Channel 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| MUTE CC | 20 | 21 | 22 | 23 | 24 | 25 | 26 | 27 |
| SOLO CC | 28 | 29 | 30 | 31 | 32 | 33 | 34 | 35 |
| REC CC | 36 | 37 | 38 | 39 | 40 | 41 | 42 | 43 |
| SELECT CC | 44 | 45 | 46 | 47 | 48 | 49 | 50* | 51 |
| Knob CC | 30 | 31 | 32 | 33 | 34 | 35 | 36 | 37 |
| Fader CC | 40 | 41 | 42 | 43 | 44 | 45 | 46 | 47 |

*SELECT 7 = CC 50 is inferred from the sequence. The capture showed CC 51,
which the user identified as an accidental click; 50 has not been measured
individually. Custom mapping through MidiSuite is suspected, not characterized
by this library's default decoder.

## Bottom-row mapping

| Position | Physical button | DAW note | Historical CC mapping |
|---|---|---|---|
| 1 | Play | 94 | 52 |
| 2 | Pause (MCU STOP) | 93 | 53 |
| 3 | Record | 95 | 54 |
| 4 | Rewind | 91 | 55 |
| 5 | Fast Forward | 92 | 56 |
| 6 | Bank Left (`<<`) | 46 | No message observed |
| 7 | Bank Right (`>>`) | 47 | No message observed |
| 8 | Up | 96 | 57 |
| 9 | Down | 97 | 58 |
| 10 | Left | 98 | 59 |
| 11 | Right | 99 | 60 |

The CC column comes from the historical press-order capture. See
[research notes](research/measurements.md#separating-daw-user-and-shift) for its
scope and the correction of the Shift interpretation.

## State tables

All three tables use the Private port, Sinco read command `0x23`, memory type
**4**, and are verified for **firmware 011 only**. Addresses are offsets in the
protocol's memory region, not established absolute CPU addresses.

| State | Address | Length | Layout | Library method |
|---|---|---|---|---|
| DAW/USER mode | `0x0000` | 1 byte | Bit mask `0x01`: clear = DAW, set = USER | `read_mode()` |
| Physical faders 1-8 | `0x0326` | 24 bytes | Eight 3-byte records; middle byte is position 0-127 | `read_faders()` |
| USER knobs 1-8 | `0x0246` | 16 bytes | Eight unsigned little-endian 16-bit values, range 0-127 | `read_user_knobs()` |

Mode's remaining bits are uncharacterized. Fader record bytes 0 and 2 stayed
constant in the recorded tests but their meaning is unknown. USER knob K is at
`0x0246 + 2 * (K - 1)`; observed upper bytes are zero. A matching second knob
table at `0x0256` has unknown purpose and is not used as a fallback.

Mode was confirmed across repeated DAW/USER transitions and a targeted one-byte
run, with before/after/idle samples. Knob 1 and 2 table values matched their MIDI
movements and persisted while another knob moved; all eight were subsequently
confirmed on the device with the monitor. The
[archive](research/measurements.md#mode-bit-search) retains run IDs and rejected
mode candidates; [knob evidence](research/measurements.md#user-knob-table-search)
records the two table copies and earlier full-run values.

Firmware 013 offsets must be reverified before those state reads are supported.
The offline image's flash-header version string `0.01` is not its running
firmware version; see [firmware.md](firmware.md).

## LEDs

Send Note On on Master with the button's note number and velocity
**127 = on, 1 = blink, 0 = off**. This was tested on firmware 011.

| Note | LED |
|---|---|
| 0-7 | REC channels 1-8 |
| 8-15 | SOLO channels 1-8 |
| 16-23 | MUTE channels 1-8 |
| 24-31 | SELECT channels 1-8 |
| 91-95 | Rewind, Fast Forward, Stop, Play, Record, respectively |
| 115 | Separate layer over all MUTE LEDs (MCU name: RUDE SOLO LED) |

Note 115 does not overwrite individual MUTE states: MUTE 1 remains on after
switching the all-MUTE layer on and off. Bank/cursor buttons have no controllable
LEDs. Other notes 32-119 did not light LEDs; 120-127 were not tested.

### Knob LEDs and soft takeover

MCU V-Pot ring commands on CC 48-55 produced **no visible effect** on the tested
device. Knob LEDs can instead blink while the host fader value differs from its
physical position. For example, with fader 1 at top:

| Master message | Result |
|---|---|
| `E0 00 00` | Only knob 1 blinks |
| `E0 00 7F` | Blinking stops |

The library reads the physical fader, sends a far-away host value for blinking
and the matching value to stop it. This does not move the fader or cause a MIDI
response. Continuous knob illumination and ring patterns are not established
through this mechanism. Automatic stop on physical pickup remains untested.
The [Mixxx reference](research/measurements.md#external-mapping-reference)
corroborates the soft-takeover behavior.

## Limitations

- CC 30-37 and 40-47 overlap buttons and continuous controls. At 0/127, the
  source is not uniquely identifiable from a message. The API marks heuristic
  decoding as `ambiguous` and refreshes position tables; it cannot prove the
  original source of that message.
- Shift and Bluetooth button states, and actual LED states, have no verified
  read mechanism. A CC event must not be treated as a Shift press.
- No knob-push events, master fader or fader-touch messages were identified.
- SELECT 7's USER CC and historical Bank-button behavior require the
  qualifications above; arbitrary MidiSuite configurations are not decoded.
- State addresses are supported only on firmware 011. The separate 013 file
  does not establish hardware behavior or continuous knob-LED support on 013.
- Unplugging/power-off removes both MIDI ports. rtmidi may silently lose outgoing
  messages rather than report the loss; the library detects this by port polling.
  A complete unplug/replug between polls may be missed. See
  [reconnect behavior](api.md#reconnection-and-errors).

