"""Button bindings: the actions a physical button can trigger and their validation."""

from dataclasses import dataclass
from typing import Any

from smc_mixer import LED_BUTTONS, Button, strip_button

CHANNEL_ACTIONS = ("mute_ch", "select_ch")
MEDIA_ACTIONS = {"play": "play", "pause": "pause", "play_pause": "play-pause", "next": "next", "previous": "previous"}
ACTIONS = ("none", *CHANNEL_ACTIONS, "mic_mute", "ptt", "out_mute", "out_cycle", *MEDIA_ACTIONS)
# Actions whose state can drive the LED.
STATEFUL = frozenset({"mute_ch", "select_ch", "mic_mute", "ptt", "out_mute", "play", "pause", "play_pause"})
LEDS = ("off", "active", "on")


class BindingError(ValueError):
    """Invalid button binding."""


@dataclass(frozen=True)
class Binding:
    button: Button
    action: str
    channel: int | None = None
    led: str = "off"


def parse_binding(row: dict[str, Any], location: str, channels: set[int]) -> Binding:
    button_name = row.get("button")
    if not isinstance(button_name, str) or button_name not in Button.__members__:
        raise BindingError(f"{location}.button: expected one of the button names, e.g. REC_1 or PLAY")
    button = Button[button_name]
    action = row.get("action")
    if action not in ACTIONS:
        raise BindingError(f"{location}.action: expected one of {', '.join(ACTIONS)}")
    channel = row.get("channel")
    if action in CHANNEL_ACTIONS:
        if type(channel) is not int or channel not in channels:
            raise BindingError(f"{location}.channel: expected a configured strip channel for {action}")
    elif channel is not None:
        raise BindingError(f"{location}.channel: only valid for {', '.join(CHANNEL_ACTIONS)}")
    led = row.get("led", "off")
    if led not in LEDS:
        raise BindingError(f"{location}.led: expected one of {', '.join(LEDS)}")
    if led != "off" and button not in LED_BUTTONS:
        raise BindingError(f"{location}.led: {button.name} has no LED")
    if led == "active" and action not in STATEFUL:
        raise BindingError(f"{location}.led: action {action} has no state; use off or on")
    return Binding(button, action, channel, led)


def strip_buttons(channels: set[int]) -> set[Button]:
    return {strip_button(row, channel) for channel in channels for row in ("MUTE", "SELECT")}


def effective(explicit: tuple[Binding, ...], channels: set[int]) -> dict[Button, Binding]:
    """Built-in defaults (strip M/S, Play, Pause) overlaid with explicit bindings."""
    result = {
        Button.PLAY: Binding(Button.PLAY, "play", led="active"),
        Button.STOP: Binding(Button.STOP, "pause", led="active"),
    }
    for channel in sorted(channels):
        for row, action in (("MUTE", "mute_ch"), ("SELECT", "select_ch")):
            button = strip_button(row, channel)
            result[button] = Binding(button, action, channel, "active")
    result.update({binding.button: binding for binding in explicit})
    return {button: binding for button, binding in result.items() if binding.action != "none"}
