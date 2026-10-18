# Tools guide

Run tools from the checkout with `uv run <command>`, or use their installed
console commands directly. `--help` is the reference for complete arguments.

| Command | Purpose |
|---|---|
| `smc-mixer monitor` | Read-only live terminal monitor |
| `mcu-tool` | Inspect MCU/CC events and test LEDs via `amidi` |
| `sinco-tool` | Read Sinco configuration/state memory via `amidi` |
| `probe-smc` | Investigate device-query responses |
| `probe-mode` | Guided DAW/USER bit search |
| `probe-knobs` | Guided USER knob-table search |
| `unpack-fwsc` | Remove the FWSC wrapper offline |
| `analyze-fwsc` | Parse Jieli structures and extract bounded entries offline |
| `disassemble-pi32v2` | Produce a listing with the local Jieli toolchain |

## Live monitor

```sh
uv run smc-mixer monitor
uv run smc-mixer monitor --demo
```

Use a UTF-8 terminal at least **105 columns by 33 rows**. Colors are enabled
when supported; smaller terminals display a resize notice. Quit with **q** or
**Ctrl+C**. Demo mode needs no mixer and alternates DAW and USER examples.

The monitor shows faders, buttons, knob positions/direction, connection status,
device mode and four recent events. It uses the library's state API and sends
read requests without changing LEDs or configuration.

| Display | Meaning |
|---|---|
| `MODE: DAW` / `USER` | Mode read from the device; `--` means unavailable |
| `LAST MIDI: MCU` / `CC` | Last received protocol, separate from mode and Shift state |
| Fader block and `000`-`127` | Last-known physical position; `---` means unknown |
| Knob position arrow | Known absolute value: minimum lower-left, midpoint up, maximum lower-right |
| Knob rotation arrow | Last relative DAW movement; signed step count appears below |
| `?` in a knob | Unknown until a USER snapshot is read or movement is received |
| `*Button*` | Pressed button; this is not its hardware LED state |
| `? (ambiguous CC)` in history | The source of a shared endpoint CC was interpreted heuristically |

Frames/fader tracks are cyan, fader positions green. Pressed Mute/Solo/Record/
Select buttons use yellow/cyan/red/magenta. Connection is green online, red
offline and yellow in demo; DAW is cyan and USER magenta.

On supported firmware, initial positions are loaded without moving controls.
Errors appear in history and automatic reads retry. Mode changes clear old knob
displays and button presses, then entering USER loads fresh absolute values.
Disconnection retains the last fader positions; reconnect refreshes supported
state. Bluetooth, Shift and actual LED states are not readable. See
[hardware limitations](control.md#limitations) and [API state behavior](api.md).

## Device diagnostics

The `amidi` diagnostics require ALSA's `alsa-utils`; continuous listeners also
use `stdbuf` from `coreutils`. The regular Python monitor uses `python-rtmidi`.

```sh
uv run mcu-tool buttons
uv run sinco-tool info
uv run sinco-tool faders
uv run sinco-tool read --type 4 --addr 0x326 --len 0x18
uv run probe-smc --dry-run
```

`sinco-tool` provides read-only `info`, `faders`, `read`, `diff` and `scan-types`
commands. `read --out PATH` also saves binary data; its parent directory must
exist. `diff A B` compares saved dumps. Read responses and memory types are
documented in [usb-midi.md](usb-midi.md#read-requests-and-responses).

LED operations actively send commands:

```sh
uv run mcu-tool led --note 16 --velocity 127
uv run mcu-tool knob-led --knob 1 blink
uv run mcu-tool knob-led --knob 1 off
```

`mcu-tool leds` runs an interactive note sweep and writes descriptions to
`build/led-map.md` by default; the parent directory must exist. `ring` sends MCU
ring commands, which have no visible effect on the tested device.

`probe-smc` normally sends 18 queries on both ports. `--dry-run` prints them
without sending. `--start N` resumes at a probe number. The optional `--scan`
adds unknown manufacturer SysEx probes and prompts for confirmation: those
messages may change settings, reset the device or enter update mode. The
[measurement archive](research/measurements.md) records the unsuccessful query
experiments and later working Private-port requests.

## Guided measurements

These tools search for firmware-specific memory fields; ordinary applications
should use the [library read methods](api.md#cached-values-and-explicit-reads).
They require firmware `SMC-Mixer_011`, send read requests, and leave completed
samples on disk after interruption. Output directories must be new.

### Mode-bit search

```sh
uv run probe-mode --type 4 --addr 0 --len 1
```

Follow DAW -> USER -> DAW -> USER, release all buttons including Shift, and
confirm with Enter. Press and release MUTE 1 only when prompted. The tool
confirms each mode from that button's protocol, then compares paired snapshots
before/after the press and at idle. A candidate bit must remain constant within
each mode across two complete confirmed cycles and differ between modes.

The general search defaults remain type 4, address `0x2c0`, length 192. `--type`,
`--addr` and `--len` select another window; the known mode byte is documented in
[control.md](control.md#state-tables). No result means no candidate in that window,
not that a mode field cannot exist elsewhere.

### USER knob search

```sh
uv run probe-knobs --addr 0x246 --len 0x20
uv run probe-knobs --addr 0x246 --len 0x20 --all-knobs
uv run probe-knobs --initial-only
```

Stay in USER mode with Shift released and move only the specified knob. Small
turns to different values are sufficient; stop before pressing Enter and keep
controls still during reads. The tool confirms USER before/after each pair and
uses raw channel/CC values rather than the normal event decoder.

The default quick search uses knobs 1 and 2: two values each and a knob-1
cross-check, for 5 adjustments and 14 snapshots. `--all-knobs` uses all eight,
for 17 adjustments and 38 snapshots. Later samples of a knob must use the same
CC source and a different value; the cross-check must also differ from the last
other-knob value. An idle sample helps reject transient send-buffer matches.

Default window: type 4, address 0, length 512. `--initial-only` captures current
state without movement prompts. A missing quick-search result for knobs 1 and 2
does not rule out fields for other knobs.

### Measurement output

`--out PATH` selects a new directory; otherwise results go under
`build/mode-probe/<timestamp>/` or `build/knob-probe/<timestamp>/`:

| File | Contents |
|---|---|
| `session.json` | Firmware, read range and measurement settings |
| `midi.jsonl` | Raw Master-port messages with phase and time |
| `samples.json` | Snapshot metadata and reference values/mode confirmation |
| `*.bin` | Paired memory snapshots |
| `report.txt` | Changes, unstable bytes and candidates |

Both tools limit windows to 512 bytes and read in 32-byte chunks. Broad type-4
scans previously caused a USB disconnect; use targeted ranges. The known table
discoveries and rejected candidates are retained in
[research/measurements.md](research/measurements.md).

## Offline firmware workflow

Firmware `013` is a separately supplied file; device state addresses were tested
on running firmware `011`. Firmware and toolchain archives are not distributed
in Git. Replace the example paths with your own copies of the files.

```sh
uv run unpack-fwsc path/to/SMC-Mixer_013.fwsc --out build/SMC-Mixer_013.unpacked
uv run analyze-fwsc build/SMC-Mixer_013.unpacked/firmware.ufw --out build/SMC-Mixer_013.analysis
tar -xf path/to/jieli-linux-toolchains-20260730.1.tar.xz -C build
uv run disassemble-pi32v2 build/SMC-Mixer_013.analysis/files/application/app.bin
```

- `unpack-fwsc` removes the interleaved wrapper metadata, preserving the UFW
  payload. It writes `firmware.ufw` and a hash/model/version `manifest.json`.
  Keep the original matching model/version filename for FWSC inputs.
- `analyze-fwsc` accepts FWSC directly or an unpacked UFW. It validates headers,
  descrambles recognized Jieli structures and extracts bounded raw and decoded
  entries. `analysis.json` records CRC status, hashes and unavailable regions.
  Mismatching raw CRCs are recorded rather than suppressing bounded entries.
- `disassemble-pi32v2` wraps a flat app image into an ELF and invokes local Jieli
  `clang`, `ld` and `objdump`. It writes `app_blob.o`, `app_image.elf`, `app.disasm`
  and `manifest.json`. Use `--toolchain`, `--base`, `--entry` and `--out` for an
  explicit setup. The default toolchain path matches the extracted archive above.

Each output directory must be new; by default it is `<input>.unpacked`,
`<input>.analysis` or `<app>.pi32v2`. `analyze-fwsc` creates parent directories,
so the first two steps above also prepare `build/` before archive extraction.

Raw configuration files can include chipkey records. Extracted content may still
contain compressed code or unresolved regions. Disassembly defaults are hypotheses
for the analyzed 013 image, not validated runtime mappings. Read the
[firmware findings](firmware.md) before interpreting a listing; a raw extract is
not automatically a complete or flashable backup.
