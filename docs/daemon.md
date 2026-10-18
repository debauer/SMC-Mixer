# Linux audio and media daemon

`smc-mixer-daemon` connects the SMC-Mixer to PipeWire's PulseAudio-compatible
interface and an explicitly selected MPRIS player. It runs in your user session.

## Requirements

- Linux, PipeWire with `pipewire-pulse`, and access to the user's audio server.
- `pactl` (usually `libpulse` or `pulseaudio-utils`) and, for media control,
  `playerctl` in PATH. No additional Python dependencies are needed.
- A connected SMC-Mixer in **DAW mode**, running verified firmware **011**.
  Unknown/unsupported state or USER mode suspends control rather than guessing.
- For YouTube/Qobuz, a browser/client that exposes its playback through MPRIS
  on the same session D-Bus. Test your actual client; not all clients support it.

## Configure and run

From the checkout:

```sh
uv sync --python 3.12
uv run smc-mixer-daemon devices
mkdir -p ~/.config/smc-mixer-daemon
cp contrib/config.example.toml ~/.config/smc-mixer-daemon/config.toml
```

Edit the copied file. Assign up to eight strips using the displayed **device
names**, not numeric IDs. An optional `label = "Desk speakers"` (at most 40
characters) gives a strip a display name in the web interface; it never affects
device matching. `sink` means output, `source` means input. Omitted
channels are inactive; missing devices are never replaced with another device.
Monitor sources and EasyEffects virtual sources (`easyeffects*`) are hidden from `devices` and the web interface but can be assigned explicitly in the file.

The example contains placeholder audio names and must be edited. Set
`media.player` to a player from `playerctl --list-all`, or remove `[media]` to
disable media integration. A base name such as `firefox` may resolve to one
`firefox.instance...` player; multiple matches are rejected. Use the full name
to distinguish multiple instances. Resolution is repeated for each button press.

```sh
uv run smc-mixer-daemon check
uv run smc-mixer-daemon run
```

Both `devices` and `check` are read-only: they do not open MIDI ports or change
audio, LEDs or playback. `check` reports unavailable assignments/player/server
with exit code 1 and invalid configuration/missing required executables with 2.
`run` waits for missing hardware and recovers after audio-server or mixer loss.

The default configuration path is
`$XDG_CONFIG_HOME/smc-mixer-daemon/config.toml`, or
`~/.config/smc-mixer-daemon/config.toml` when that variable is unset. Pass
`check --config PATH` or `run --config PATH` to override it. Unknown keys,
duplicate device/channel assignments and invalid values are rejected. Restart
the daemon after editing the file by hand; saving from the web interface reloads it.

## Web interface

`run` also serves a local web interface, by default at <http://127.0.0.1:8765/>.
It shows the mixer connection and mode, all eight strips (device volume, physical
fader position, M/SEL LEDs, soft-takeover state) and the daemon's recent log, and it
edits the strip assignments, soft takeover and media player. **Save** validates the
configuration, rewrites `config.toml` (comments in the file are not preserved) and
reloads the daemon in-process; the mixer is briefly released and re-initialized.
Every physical button press lights its key in the page (steady while held, plus a short
glow), whether or not it is bound.
The device and player lists come live from `pactl` and `playerctl`.

```toml
[web]
enabled = true      # false disables the interface
host = "127.0.0.1"  # loopback only by default
port = 8765
```

The `[web]` table is optional, read at startup and cannot be changed from the page;
restart the daemon after editing it. The interface has no login. It binds to loopback by default and rejects requests with a
foreign `Host` or `Origin` header and non-JSON writes, so other web pages cannot
change the configuration through your browser. If you bind to another address, anyone
who can reach it can edit the configuration (but not the shell commands, see below); the
daemon logs a warning.

## Controls

| Control | Action |
|---|---|
| Fader | Assigned device volume, 0-100%; all device channels linked |
| M | Toggle mute; its LED reflects actual mute state |
| SELECT on an output strip | Select default output and move all current playback streams there |
| SELECT on an input strip | Select default input, without explicitly moving active recordings |
| SELECT LED | Device is the current default of its kind (input/output independently) |
| Play | Play/resume the configured MPRIS player; LED on while it is playing |
| Physical Pause (MCU STOP) | Pause the configured MPRIS player; LED on while it is paused |
| All other buttons (R, S/SOLO, Bank, transport, navigation, and M/SELECT of unassigned strips) | Unassigned until bound, see below |
| Knobs | Unassigned until bound, see below |

Buttons act once per press, not again on release (Push-to-Talk also acts on release). Fader changes do not unmute.
Changes made through desktop controls update M/SELECT LEDs (audio state is re-read on every PipeWire event and at least every 2 seconds, and all LEDs are resent every 2 seconds). The player state is read about once a second, so Play/Pause LEDs also follow pauses started in the player itself; they are off while the player is missing, ambiguous or reports Stopped. Other button LEDs are off unless a binding lights them.

### Direct faders and optional soft takeover

By default, **startup and reconnection apply the physical fader positions to
assigned devices immediately**. Returning to DAW mode, audio-server recovery or
an assigned device appearing also triggers a fresh-position initialization.
Mute/default states are read from the system and are not reset at startup.

Physical 0-127 maps to rounded percentage 0-100. Volume changes from other apps
are not continuously overwritten: the next fader movement takes direct control.
Pending fader updates are coalesced in 25 ms loop batches, with button ordering
preserved. Actual latency also depends on audio commands and device response.

To enable pickup instead:

```toml
[daemon]
soft_takeover = true
```

Startup/reconnect then reads system volume without applying faders. A fader takes
ownership when it crosses the current volume or reaches within one percentage
point. The corresponding knob LED blinks while waiting. External volume changes
re-arm pickup; recognized acknowledgments of the daemon's own writes do not.
Targets above 100% clamp to 100%, since hardware control is limited to 0-100%.
Self-write recognition uses a two-second acknowledgment window; identical
concurrent external writes cannot be distinguished from those acknowledgments.

### Output routing details

SELECT sets the default sink first, then moves PulseAudio-visible playback streams
from all outputs. A bounded second pass catches streams appearing during the
switch. The daemon does not permanently enforce routing; applications/session
policy can route future streams differently. Native PipeWire links outside
`pipewire-pulse` are not managed.

A stream can disappear or reject a move. Other moves continue; remaining failed
moves are logged. There is no atomic rollback. The SELECT LED reports the actual
default output, not whether every stream was successfully moved. Input SELECT does
not migrate recording streams or convert an input device into a playback stream.

### Button bindings

Any button can be bound in the web interface (or by hand) with `[[buttons]]` tables.
Strip M/SELECT, Play and Pause keep the behavior above unless overridden; `action = "none"`
disables a default. M/SELECT of a configured strip cannot be rebound.

```toml
[[buttons]]
button = "REC_1"      # Button name: REC_n, SELECT_n, MUTE_n, SOLO_n, BANK_LEFT, PLAY, UP, ...
action = "mic_mute"
led = "active"        # off, active, on; default: active for actions with state, else off

[[buttons]]
button = "REC_3"
action = "mute_ch"    # needs channel = a configured strip
channel = 1
```

| Action | Effect | LED `active` |
|---|---|---|
| `mute_ch`, `select_ch` | Same as the strip's M / SELECT for `channel` | muted / default device |
| `mic_mute` | Mute all inputs (monitor sources excluded); pressing again unmutes them except those that were already muted before | all inputs muted |
| `out_mute` | Mute all outputs; pressing again unmutes them except those that were already muted before | all outputs muted |
| `shell` | Run `command` with `/bin/sh -c` on press and optionally `release_command` on release; see below | - |
| `ptt` | Unmute the muted inputs while held; on release mute again only those that were muted before the press | held |
| `out_cycle` | Next sink among the configured output strips as default, moving streams like SELECT | - |
| `play_pause`, `play`, `pause` | MPRIS on the configured player | playing / playing / paused |
| `next`, `previous` | MPRIS on the configured player; the button's LED blinks three times after a successful request | - |

`led = "on"` lights the LED whenever control is active. Buttons without an LED (Bank,
navigation) reject any LED setting. The "already muted before" memory lives in the running daemon only: after a restart or
reload, or when everything was muted elsewhere, the next press unmutes everything.
Push-to-Talk never mutes an input that was unmuted before; a button held during a reconnect or mode change is not released
by the daemon.

### Knob bindings

In DAW mode the knobs send relative steps (no position, no push). Bind them in the web
interface or with `[[knobs]]` tables; each step changes a volume by `step` percent
(1-20, default 2). Steps arriving within one loop tick are summed into one volume write,
volume is clamped to 0-100 %, and a muted device stays muted.

```toml
[[knobs]]
knob = 1            # 1-8
action = "vol_ch"   # volume of the device on strip `channel`
channel = 1
step = 5

[[knobs]]
knob = 2
action = "vol_out"  # default output; vol_in: default input
```

A knob changes the device volume without moving the fader. In direct mode the next fader
movement takes over again; with `soft_takeover` the fader blinks until it catches up.

### Shell commands

A button with `action = "shell"` runs its `command` through `/bin/sh -c` (and, optionally,
`release_command` on release), as your user, in your home directory. Commands run on a
background thread, so they never block the mixer; stdin is closed, stdout and stderr are
captured, and a nonzero exit code is logged with the last output. A command still running
after 30 seconds is killed together with everything it started (use `&` or `setsid` inside
the command if something must outlive that), at most 8 run at once, and running commands are
killed when the daemon stops. No `led = "active"` (shell commands have no state).

```toml
[[buttons]]
button = "REC_5"
action = "shell"
command = "notify-send 'Hello'"
release_command = "true"   # optional
```

Anything that can write `config.toml` can already run code as you. The web interface is
another way to write it, so changing shell commands over HTTP needs an access token: the
daemon creates `web-token` (mode 0600) next to `config.toml` and the page asks for its
contents once per browser session (it is never sent to the page). Other settings need no
token, and unchanged commands are kept without one. Changes to commands are made over HTTP
only with that token, so other local users and web pages cannot add commands.

## Install a user service

For an executable independent of the checkout's development environment:

```sh
uv tool install --python 3.12 /absolute/path/to/m-wave_smc-mixer
```

The supplied service assumes the resulting executable is
`~/.local/bin/smc-mixer-daemon`. Adjust `ExecStart` to your installation's absolute
path if needed. The unit uses `%h` for your home directory. Ensure `pactl` and
`playerctl` are in the service's PATH; adjust it for distributions with other
binary locations. For custom configuration, append `--config /absolute/path`
after `run`, or set `XDG_CONFIG_HOME` in the unit.

```sh
mkdir -p ~/.config/systemd/user
cp contrib/systemd/smc-mixer-daemon.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now smc-mixer-daemon.service
systemctl --user status smc-mixer-daemon.service
journalctl --user -u smc-mixer-daemon.service -f
```

Use your desktop session's audio server and D-Bus; do not install it as a root
service. It waits if the mixer is absent. Runtime failures restart the service;
configuration errors (exit 2) require correction and an explicit restart.

```sh
systemctl --user restart smc-mixer-daemon.service
systemctl --user disable --now smc-mixer-daemon.service
```

SIGTERM/Ctrl+C stops child processes, clears daemon LED feedback where reachable
and closes MIDI ports. Shutdown does not restore previous volume, mute or routing.

## Verification

Automated tests use simulated MIDI devices and command adapters, including
startup/direct faders, optional pickup, external state, routing, missing devices,
media failure isolation, read-only commands and subscription-child cleanup.

Hardware acceptance still needs the user's desktop: two outputs, one input,
simultaneous browser/music playback, actual YouTube/Qobuz MPRIS support,
hotplug, audio-server restart, direct/pickup initialization and the user service.
See [hardware constraints](control.md#limitations) and [library API](api.md).
