"""Knob (encoder) bindings; in DAW mode the knobs send relative steps."""

from dataclasses import dataclass
from typing import Any

KNOB_ACTIONS = ("none", "vol_ch", "vol_out", "vol_in")
DEFAULT_STEP = 2
MAX_STEP = 20


class KnobBindingError(ValueError):
    """Invalid knob binding."""


@dataclass(frozen=True)
class KnobBinding:
    knob: int
    action: str
    channel: int | None = None
    step: int = DEFAULT_STEP


def parse_knob(row: dict[str, Any], location: str, channels: set[int]) -> KnobBinding:
    knob = row.get("knob")
    if type(knob) is not int or not 1 <= knob <= 8:
        raise KnobBindingError(f"{location}.knob: expected an integer from 1 to 8")
    action = row.get("action")
    if action not in KNOB_ACTIONS:
        raise KnobBindingError(f"{location}.action: expected one of {', '.join(KNOB_ACTIONS)}")
    channel = row.get("channel")
    if action == "vol_ch":
        if type(channel) is not int or channel not in channels:
            raise KnobBindingError(f"{location}.channel: expected a configured strip channel for vol_ch")
    elif channel is not None:
        raise KnobBindingError(f"{location}.channel: only valid for vol_ch")
    step = row.get("step", DEFAULT_STEP)
    if type(step) is not int or not 1 <= step <= MAX_STEP:
        raise KnobBindingError(f"{location}.step: expected an integer from 1 to {MAX_STEP} (percent per step)")
    return KnobBinding(knob, action, channel, step)
