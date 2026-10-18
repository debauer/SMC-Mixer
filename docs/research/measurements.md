# Measurement history

This archive records how the USB-MIDI mapping and readable state tables were
identified on running firmware **`SMC-Mixer_011`**. The current reference is
[control.md](../control.md); ordinary usage is documented in [tools.md](../tools.md).
Historical assumptions below are not additional device features or usage instructions.
The raw capture files are local working data and are not part of the repository.

## Initial Master-port capture

`aseqdump -p SINCO:1` showed MCU messages on channel 1 except faders, which used
Pitch Bend channels 1-8. Fader resolution was effectively 7 bits: the low data
byte was always zero. Top position gave `aseqdump` value 8064, equivalent to
raw value 16256 (`0x3f80`). Knobs sent CC 16-23 with value 1 clockwise.
Counter-clockwise value 65 was initially an assumption, later confirmed by the
DAW/USER capture below. Only single steps 1/65 occurred, even with fast turns;
the decoder also accepts MCU's larger step ranges up to 15/79.

The first capture did not include Stop (93), cursor left/right (98/99), master
fader (channel 9), fader touch (notes 104-111), or knob push (32-39). Subsequent
button tests confirmed Stop and both cursor buttons; no knob-push messages were
observed. A master fader/touch control has not been identified.

Channel strips were operated in this order: channels 1-8, each MUTE, SOLO, REC,
SELECT. The user confirmed their physical top-to-bottom order beside the fader.
The bottom row was operated left to right. MidiSuite image assets corroborate
the labels:

- `assets/images/smc_mixer/smc_mix_but_1..11.png`: Play, Pause, Record, Rewind,
  Fast Forward, Bank Left, Bank Right, Up, Down, Left, Right.
- `smc_mix_track_1..4.png`: M, S, R, empty Select square.
- `desktop_mixer.png`: two oval controls beside knob 8, identified by the user
  as Bluetooth and Shift. No dedicated Shift MIDI message was recorded.

Pause's physical icon sends MCU STOP, note 93. The measured DAW note map matches
the MCU standard; complete tables are maintained in [control.md](../control.md).

## Unsuccessful initial-value queries

Before the Sinco read command was established, the following were tried:

| Test | Result |
|---|---|
| MCU Device Query `F0 00 00 66 14 00 F7` on Master | No response |
| Universal Identity Request `F0 7E 7F 06 01 F7` on both ports | No response |
| USB reconnect / power cycle | 4 of 8 faders reported; identical on both ports |
| USB re-enumeration using `authorized` 0/1, without power cycle | 2 of 8 faders reported, different ones |
| Pitch Bend zero and maximum to all eight fader channels on Master | No response; knob LEDs blinked |
| Original probe run, 16 standard queries | No responses |

Different faders reported after each re-enumeration. ADC jitter was the likely
trigger, rather than a deliberate state dump; faders at endpoints hardly
reported. These experiments did not provide reliable initial positions.

The probe list later grew to 18 queries by adding Sinco Name/Version on each
port. In one such run, probe #8 received no SysEx response; once `E1 00 4C`
appeared only on Private. Possible causes included prior MCU/HUI probes or
separate sending/listening processes. The cause was not isolated. The optional
manufacturer scan was deferred in favor of source analysis and is not a
verified way to query state.

## Sinco Private-port response

The working test used a single `amidi` process to send and receive on Private:

```
amidi -p hw:<card>,0,0 -S 'F0 00 32 45 00 00 00 40 7F F7' -d -t 2
F0 00 32 45 58 01 00 40 29 4D 06 35 69 14 0D 5E 32 72 3E 41 09 13 06 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 20 3E F7
```

Three identical responses decoded to command `0x11`, 27 payload bytes containing
`SMC-Mixer_011` plus nulls, with a valid checksum. Only Private responded.
Passive listening without a query received no bytes. The Python library later
confirmed the same request through the ALSA sequencer with python-rtmidi.

### Memory-type survey

The original type scan read 16 bytes at address 0 for types `0x00`-`0x0f`:

| Type | Response |
|---|---|
| `0x00` | `A2 4E 20 02 2F 10 4C C9 00 F0 07 00 03 10 A4 00` |
| `0x04` | `00 00 00 00 00 1E 1F 00 35 00 00 00 00 00 00 00` |
| `0x05` | `05 00 10 00 00 06 F0 7F 7F 06 01 F7 00 00 00 00` |
| Other tested types | Command `0x00`, payload `01` |

Read responses echo the eight-byte type/address/length request before data.
Type 5 begins like MidiSuite's `assets/bin/SSM.bin`, identifying a configuration
block. Type 4 was later confirmed to contain live RAM state.

## Fader-table discovery

Initial experiment: read 4096 bytes from types 0 and 4 at address 0 with all
faders down, then move only fader 1 to the top and compare new dumps. This was
a historical broad scan, **not the recommended current procedure**: the first
type-4 read briefly removed the device from USB, after which it returned.
Reading peripheral registers may have caused a crash/restart; the cause was
not proven. Subsequent work uses small targeted reads.

- Type 0 had no differences and did not reach a region end within 4096 bytes.
  A configuration/flash region was suspected.
- Type 4 changed at many locations and behaved like RAM.
- At `0x326`, eight three-byte records had a middle byte matching fader values.
  Fader 1 changed `00 -> 7f`; fader 8 was `01` in the initial dump, consistent
  with jitter. Byte 0 and byte 2 appeared constant.
- `0x6b4` changed `00 -> 7f`, and a u16 LE value at `0x6b6` changed 0 -> 1020,
  near a 10-bit maximum: a possible intermediate for the last processed fader.
- `0x922`-`0x96e` held 16 u16 values at stride 4, around `0x74bc`, with slight
  noise and no established fader relationship.

A targeted read at `0x320`, length `0x20`, followed deliberately set positions:

| Fader | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| Set to | top | ~60% | bottom | middle | top | bottom | ~25% | ~75% |
| Read | `7f` | `55` | `00` | `3b` | `7f` | `00` | `20` | `69` |

This confirmed the middle bytes of the 24-byte table at `0x326`. Byte 0 values
`12 4a 0b 65 38 49 5a 72` and byte 2 (`01`) stayed constant; their meaning remains
unknown. These are state-table measurements for 011, not addresses established
from the separately obtained 013 firmware file.

## Separating DAW, USER and Shift

The early CC capture was initially attributed to holding Shift. Its operation
order was channel strips 1-8 (M/S/R/Select), bottom row left to right, knobs 1-8,
then faders 1-8. The user later clarified that the two contrasting captures were
made by switching **DAW/USER modes, without Shift**:

| Control | DAW capture | USER capture |
|---|---|---|
| Fader 1 | Pitch Bend channel 1, e.g. 9216..9856 | CC 40, values 70..74 |
| MUTE 1 | Note 16 pressed/released | CC 20, 127/0 |
| Knob 1 | CC 16, repeated 1/65 | CC 30, values 27..30 |

This corrected the interpretation, not the recorded CC numbers. Shift's actual
effect still needs independent tests. In the older button capture, Select 7
showed CC 51, which the user identified as an accidental click; CC 50 is inferred
from the sequence, not individually measured. Bank Left/Right produced no
messages in the historical CC test; the remaining bottom-row assignments were
inferred from press order. USER knob turns at minimum produced no message when
turning further left; the observed initial counter value in that test was zero.

## Mode-bit search

The guided sequence was DAW -> USER -> DAW -> USER with paired snapshots before
MUTE 1, after the press/release, and at idle. DAW was confirmed with
`90 10 7f` / `90 10 00`, USER with `b0 14 7f` / `b0 14 00`.

| Mode-probe run (local output) | Range | Result |
|---|---|---|
| `20261017-191112-673266` | Type 4, `0x2c0..0x37f` | All 24 snapshots identical |
| `20261017-191609-620874` | Type 5, `0x00..0x3f` | All 24 snapshots identical |
| `20261017-192207-327709` | Type 4, `0x200..0x2bf` | Changes, but no stable mode bit |
| `20261017-192433-083084` | Type 4, `0x000..0x1ff` | Bit 0 at 0, `0x20`, `0x22` matched modes |
| `20261017-192658-534880` | Type 4, address 0, one byte | All 24 samples reconfirmed `00` / `01` |

In the `0x200..0x2bf` run, `0x242` fluctuated `7b/7f` in both modes. `0x266`
increased `16 -> 1b -> 20 -> 25 -> 2e`, suggesting activity counting. `0x272`
retained the previous protocol after a mode switch and changed to `90` or `b0`
only after MUTE: a likely last-MIDI-status buffer, not an initial mode flag.
MUTE was also pressed during the fourth stage's first read, so that individual
before sample was not undisturbed.

At `0x20` and `0x22`, entire bytes continued to increase and wrap; matching their
low bits could be a counter effect. At address 0 the entire byte consistently
remained `00` for DAW and `01` for USER, including before the button probe. The
one-byte repeat followed a requested device power cycle. The dump itself records
the mode sequence, not independent evidence of that power cycle.

## USER knob-table search

The corrected workflow kept USER selected and Shift released, checking the mode
before and after each read. Raw CC data bypassed decoder endpoint heuristics.

- `20261017-195741-922757` (knob-probe, local output): partial run in `0x000..0x1ff`, no
  table found for the measured knobs.
- `20261017-195934-292634`: full eight-knob run, 19 samples / 38 snapshots in
  the same range; no persistent direct values, including simple 16-bit checks.
  Knob 3 sent another value 22 -> 23 during its low read. Recorded final values
  for knobs 3-8 were 87, 90, 92, 95, 97, 101.
- `20261017-200935-782985`: quick two-knob search in `0x200..0x3ff`; knob 2
  matched `0x248` / `0x258`, while knob 1 did not pass the final idle check.
- `20261017-201020-939358`: clean quick repeat, with the following matches:

| Knob | Addresses | MIDI/reference values |
|---|---|---|
| 1 | `0x246`, `0x256` | 93 -> 103 -> 108 |
| 2 | `0x248`, `0x258` | 110 -> 120 |

Both paired values matched, remained stable when the other knob moved, and
persisted at idle. Tables at `0x246` and `0x256` had a two-byte stride, zero high
bytes, and six other values matching the earlier full run. Initial values were
readable before movement. The user subsequently confirmed all eight primary
table values on the device with the monitor. The second table's purpose is
still open. Canonical layout and limits are in [control.md](../control.md#state-tables).

## LED and reconnect tests

- The LED sweep tested notes 0-119 with velocity 127 on and 0 off. MUTE/SELECT
  channels beyond the abbreviated log's "etc." were confirmed by the user.
  Notes 120-127 were not tested.
- Velocity 1 blinked note 0. Note 115 acted as a separate all-MUTE layer:
  MUTE 1 on using note 16, then 115 on/off, left MUTE 1 on.
- MCU V-Pot ring values `01 06 0b 16 26 36 40 00` on knob 1 (four MCU modes,
  center LED and off) produced no illumination.
- Pitch Bend sent to all eight channels made all knob LEDs blink. With fader 1
  at top, `E0 00 00` blinked only knob 1; `E0 00 7F` stopped it. Sending did not
  make the device report a value. Stopping by physically crossing the host value
  (pickup) was an assumption, not tested.
- With python-rtmidi, the port list was polled while sending an LED message once
  per second. Unplugging and power-off removed both ports. On return, both names
  and ALSA client numbers (`28:0`, `28:1`) matched the earlier connection.
  Sending to a vanished port did not raise an rtmidi error, motivating explicit
  port polling and reconnect handling.

## External mapping reference

Mixxx sources `res/controllers/MVave-SMC-Mixer.midi.xml` and
`MVave-SMC-Mixer-scripts.js`, commit `1b14de328a8c` (2025-03-09), were consulted as a local reference copy. The [manual](https://manual.mixxx.org/2.7/en/hardware/controllers/mvave_smc_mixer)
and mapping agree with the measured DAW messages and soft-takeover LED blinking.
Long presses and deck selection are software features; there is no Shift,
Bluetooth or SysEx handling in that mapping. A comment attributes a future
continuous-LED feature to M-Vave, but this was not independently checked and is
not established for firmware 013. The manual recommends USB-C over Bluetooth
for latency and smoother fader reporting.
