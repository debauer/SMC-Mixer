# Library API

The public API is exported from `smc_mixer`. For installation and a minimal
example, start with the [project README](../README.md). Hardware mappings and
firmware-specific addresses are defined in [control.md](control.md).

## Connection and automatic state

```python
from smc_mixer import DeviceMode, SmcMixer

with SmcMixer.open(state_poll_interval=0.5) as mixer:
    print(mixer.connected)
    print(mixer.mode)
    print(mixer.faders)
    if mixer.mode == DeviceMode.USER:
        print(mixer.user_knobs)
```

`open()` resolves the Master and Private MIDI ports by name. Its defaults are:

| Parameter | Default | Purpose |
|---|---|---|
| `master_name` | `SMC-Mixer-Master` | Substring identifying the Master port |
| `private_name` | `SMC-Mixer-Private` | Substring identifying the Private port |
| `auto_reconnect` | `True` | Watch for disappearing and returning ports |
| `poll_interval` | `0.5` | Port-list polling interval in seconds |
| `state_poll_interval` | `0.5` | State polling interval in seconds; `None` disables it |

With state polling enabled, opening reads the mode and missing snapshots:
physical faders and, in USER mode, absolute USER knob values. Later polls read
the mode, reload USER knob snapshots after mode changes, retry failed reads, and resolve
position caches affected by ambiguous CC messages. Supported state reads require
firmware `SMC-Mixer_011`.

Automatic state-read failures are logged and leave MIDI input available. The
interval must be positive and finite, or `None`. State polling and port polling
are independent. To use only explicit reads and no port watcher, set both
`state_poll_interval=None` and `auto_reconnect=False`.

For custom ports or tests, use `SmcMixer(master, private, hotplug=None,
state_poll_interval=None)`. Unlike `open()`, the direct constructor does not
poll state by default. `MidiPort`, `PortScanner` and `HotplugConfig` are exported
for integrations. `list_ports()` returns the available MIDI input port names.

## Cached values and explicit reads

| API | Result |
|---|---|
| `connected` | Whether port handles are currently attached; disconnection detection depends on port polling |
| `mode` | Last read `DeviceMode.DAW` / `DeviceMode.USER`, or `None` |
| `faders` | Copy of eight last-known physical positions, each `int` 0-127 or `None` |
| `user_knobs` | Copy of eight last-known absolute USER values, each `int` 0-127 or `None` |
| `read_mode(timeout=1.0)` | Read and cache the current `DeviceMode` |
| `read_faders(timeout=1.0)` | Read positions and return `list[int]` |
| `read_user_knobs(timeout=1.0)` | Read absolute USER values and return `list[int]` |
| `refresh_state(timeout=1.0)` | Read mode and any missing or dirty position snapshots |
| `firmware(timeout=1.0)` | Query a name/version string, for example `SMC-Mixer_011` |
| `read_memory(kind, address, length, timeout=1.0)` | Return bytes using the read-only Sinco protocol |

Properties are thread-safe caches, not fresh hardware requests. List properties
return copies. Newer MIDI updates received during a snapshot read take priority
over older snapshot values. Relative DAW movements are not accumulated into
invented absolute knob positions.

`read_user_knobs()` checks USER mode before and after the read, validates table
length and values, and raises `ProtocolError` if the mode is wrong or changes
during the read. USER values are cleared when mode changes, becomes unknown,
or the device disconnects. Physical fader caches retain the last known values
while disconnected; reconnect rereads them, or clears them if recovery fails.

Explicit reads are available with automatic state polling disabled:

```python
from smc_mixer import DeviceMode, SmcMixer

with SmcMixer.open(state_poll_interval=None) as mixer:
    mode = mixer.read_mode()
    faders = mixer.read_faders()
    if mode == DeviceMode.USER:
        values = mixer.read_user_knobs()
```

Memory reads expose a low-level research primitive. Use small, targeted ranges;
see the [Sinco protocol reference](usb-midi.md#read-requests-and-responses).

## Events

`events(timeout=None)` yields queued events until interrupted. With a numeric
timeout, iteration ends after that many seconds **without an event**, not after
a fixed total duration. `events(timeout=0)` drains the queue without waiting.
Events generated during `open()` are also queued.

| Event | Fields | Meaning |
|---|---|---|
| `ButtonEvent` | `button`, `pressed`, `protocol`, `ambiguous` | Received press/release |
| `KnobEvent` | `knob`, `delta`, `value`, `protocol`, `ambiguous` | Relative `delta` or absolute `value`; knobs are numbered 1-8 |
| `FaderEvent` | `fader`, `value`, `protocol`, `ambiguous` | Received position; faders are numbered 1-8 |
| `UnknownEvent` | `message` | Unrecognized raw MIDI bytes |
| `ConnectionEvent` | `connected` | Port disconnection/reconnection |
| `ModeEvent` | `mode` | Read mode changed or became unknown (`None`) |
| `UserKnobsEvent` | `values` | Read snapshot as an immutable tuple of eight absolute values |

Snapshot events are distinct from physical movement: reading a table does not
fabricate eight `KnobEvent`s. Explicit reads can emit mode and snapshot events
as well as updating properties.

```python
from smc_mixer import ButtonEvent, KnobEvent, ModeEvent, SmcMixer, UserKnobsEvent

with SmcMixer.open() as mixer:
    for event in mixer.events():
        match event:
            case ModeEvent(mode=mode):
                print("Mode:", mode)
            case UserKnobsEvent(values=values):
                print("USER snapshot:", values)
            case KnobEvent(knob=knob, delta=delta, value=value):
                print(knob, event.protocol, delta if delta is not None else value)
            case ButtonEvent(button=button, pressed=pressed):
                print(button.name, pressed, event.ambiguous)
```

### Mode, protocol and ambiguity

`DeviceMode` is the mode read from the device. `MidiProtocol.MCU` / `CC` describes
the MIDI encoding of a control event. CC does not prove USER mode or a held
Shift button and does not change the mode cache. Shift's independent behavior
remains unverified.

Some default CC numbers overlap buttons and continuous controls. Endpoint values
0/127 can therefore be ambiguous. `ambiguous=True` identifies a heuristic
interpretation; such values do not directly overwrite trusted position caches.
A subsequent automatic or explicit `refresh_state()` reads the relevant table.
This establishes the position, not the identity of the original message source.

The existing event constructor field `layer` remains available. MCU events use
`Layer.DAW`; CC events now use `Layer.CC`. `Layer.SHIFT` remains a legacy input
value but is never emitted by the decoder. Prefer `event.protocol` in new code.
Historical `SHIFT_*` mapping imports alias the current USER/CC constants.

### Listeners and threads

`add_listener(callback)` and `remove_listener(callback)` provide callback-based
delivery. Listeners run on the MIDI backend, watcher, or explicit-read caller
thread, depending on the source. Keep them short. Do not perform blocking Sinco
reads inside MIDI callbacks; use `events()` from an application thread for work
that can block.

## LED commands

```python
from smc_mixer import Button, LedState, SmcMixer

with SmcMixer.open() as mixer:
    mixer.set_led(Button.MUTE_1, True)
    mixer.set_led(Button.PLAY, LedState.BLINK)
    mixer.set_knob_blink(3, True)
```

| Method | Behavior |
|---|---|
| `set_led(button, state=LedState.ON)` | Set a button in `LED_BUTTONS`; accepts `LedState.ON/BLINK/OFF` or bool |
| `set_all_mute_leds(on)` | Set the separate layer over all MUTE LEDs |
| `all_leds_off()` | Turn off button LEDs and the all-MUTE layer |
| `set_knob_blink(knob, blink)` | Blink/stop knob LED 1-8; requires a known fader position |

Knob LEDs use a fader host/physical-value mismatch for blinking. Continuous knob
illumination and LED-ring patterns are not supported by this mechanism. Commands
previously sent by this instance are remembered for reconnect recovery; they are
not measurements of actual hardware LED state. Supported LEDs and the mechanism
are described in [control.md](control.md#leds).

## Reconnection and errors

On disconnection, mode and USER values become unknown and a
`ConnectionEvent(False)` is emitted. Calls requiring ports raise
`DisconnectedError`. Faders retain their last-known positions while offline.
On reconnect, ports are reopened, faders reread, prior LED commands restored,
and `ConnectionEvent(True)` emitted. When state polling is enabled, mode and USER
knobs are refreshed and firmware support is rechecked. The event iterator keeps
running. A complete unplug/replug between port polls may not be detected.

`close()` stops watchers and closes ports; it is safe to call repeatedly. Use a
context manager for normal ownership. With port monitoring disabled, the backend
may silently lose outgoing messages after unplugging.

| Exception | Meaning |
|---|---|
| `SmcMixerError` | Base class for library-defined errors |
| `PortNotFoundError` | No matching port, or an ambiguous name match |
| `DisconnectedError` | Required port is detached |
| `ResponseTimeoutError` | No Sinco response within the request timeout |
| `ProtocolError` | Invalid/rejected response, unsupported state contents, or a USER read in the wrong/changing mode |
| `UnsupportedFirmwareError` | A state table has not been verified for the reported firmware |

Explicit reads propagate errors. Automatic state refresh logs failures, keeps MIDI
input running and retries; a failed mode read invalidates USER values. Each
property remains a last-known cache, not an atomic snapshot of all controls.
