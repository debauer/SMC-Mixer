"""Knob bindings: relative volume changes, coalescing and configuration."""

import tomllib

import pytest

from daemon_fakes import FakeAudio, mixer_pair
from smc_mixer import Button, ButtonEvent, KnobEvent, Layer
from smc_mixer_daemon import config_store
from smc_mixer_daemon.config import Config, ConfigError, Strip, parse
from smc_mixer_daemon.controller import Controller
from smc_mixer_daemon.inbox import Inbox
from smc_mixer_daemon.knobs import KnobBinding

STRIPS = (Strip(1, "sink", "speakers"), Strip(2, "sink", "headset"), Strip(3, "source", "mic"))


def started(*knobs: KnobBinding) -> tuple[Controller, FakeAudio]:
    mixer, _, _ = mixer_pair()
    audio = FakeAudio()
    controller = Controller(Config(STRIPS, knobs=knobs), mixer, audio, None)
    assert controller.start()
    audio.calls.clear()
    return controller, audio


def turn(controller: Controller, knob: int, delta: int) -> None:
    controller.event(KnobEvent(knob, Layer.DAW, delta=delta))


def test_channel_knob_changes_volume_relatively_and_accumulates() -> None:
    controller, audio = started(KnobBinding(1, "vol_ch", 2, step=5))
    # headset starts at 67% (its fader position, applied at startup)
    turn(controller, 1, 1)
    turn(controller, 1, 2)
    turn(controller, 1, -1)
    assert audio.calls == [("volume", "headset", 72), ("volume", "headset", 82), ("volume", "headset", 77)]


def test_volume_is_clamped_and_never_unmutes() -> None:
    controller, audio = started(KnobBinding(2, "vol_ch", 2, step=20))
    turn(controller, 2, 5)
    assert audio.calls == [("volume", "headset", 100)]
    turn(controller, 2, 3)  # already at the limit: no write
    assert len(audio.calls) == 1
    turn(controller, 2, -9)
    assert audio.calls[-1] == ("volume", "headset", 0)
    assert audio.devices["sink", "headset"].muted is True  # was muted before and stays so


def test_default_output_and_input_knobs_follow_the_current_default() -> None:
    controller, audio = started(KnobBinding(1, "vol_out", step=10), KnobBinding(2, "vol_in", step=10))
    turn(controller, 1, -1)  # speakers are the default sink at 100%
    turn(controller, 2, 1)  # mic is at 0% (fader position)
    assert audio.calls == [("volume", "speakers", 90), ("volume", "mic", 10)]
    audio.sink = "headset"
    controller.refresh()
    turn(controller, 1, -1)
    assert audio.calls[-1] == ("volume", "headset", 57)


def test_unbound_ambiguous_and_suspended_knobs_do_nothing() -> None:
    controller, audio = started(KnobBinding(1, "vol_out"))
    turn(controller, 2, 1)
    controller.event(KnobEvent(1, Layer.CC, value=64))
    controller.event(KnobEvent(1, Layer.DAW, delta=1, ambiguous=True))
    controller.suspend()
    turn(controller, 1, 1)
    assert not audio.calls


def test_inbox_sums_knob_steps_and_keeps_order_with_buttons() -> None:
    inbox = Inbox()
    for delta in (1, 1, 1, -1):
        inbox.put(KnobEvent(3, Layer.DAW, delta=delta))
    inbox.put(ButtonEvent(Button.PLAY, True, Layer.DAW))
    inbox.put(KnobEvent(3, Layer.DAW, delta=1))
    inbox.put(KnobEvent(4, Layer.DAW, delta=1))
    inbox.put(KnobEvent(4, Layer.DAW, delta=-1))  # cancels out
    _, events = inbox.take()
    assert [type(e).__name__ for e in events] == ["KnobEvent", "ButtonEvent", "KnobEvent"]
    assert [getattr(e, "delta", None) for e in events if isinstance(e, KnobEvent)] == [2, 1]


BASE = {"strips": [{"channel": 1, "kind": "sink", "name": "a"}]}


@pytest.mark.parametrize(
    "knobs",
    [
        [{"knob": 0, "action": "vol_out"}],
        [{"knob": 9, "action": "vol_out"}],
        [{"knob": 1, "action": "explode"}],
        [{"knob": 1, "action": "vol_ch"}],
        [{"knob": 1, "action": "vol_ch", "channel": 5}],
        [{"knob": 1, "action": "vol_out", "channel": 1}],
        [{"knob": 1, "action": "vol_out", "step": 0}],
        [{"knob": 1, "action": "vol_out", "step": 21}],
        [{"knob": 1, "action": "vol_out"}, {"knob": 1, "action": "vol_in"}],
        [{"knob": 1, "action": "vol_out", "typo": 1}],
    ],
)
def test_invalid_knob_bindings_are_rejected(knobs: list[dict[str, object]]) -> None:
    with pytest.raises(ConfigError):
        parse({**BASE, "knobs": knobs})


def test_knobs_round_trip_through_toml_and_json() -> None:
    data = {
        **BASE,
        "knobs": [
            {"knob": 2, "action": "vol_ch", "channel": 1, "step": 5},
            {"knob": 1, "action": "vol_out", "channel": None, "step": 2},
        ],
    }
    config = config_store.from_json(data, Config(()))
    again = parse(tomllib.loads(config_store.render(config)))
    assert sorted(again.knobs, key=lambda k: k.knob) == sorted(config.knobs, key=lambda k: k.knob)
    assert again.knobs[0].knob == 1 and again.knobs[1] == KnobBinding(2, "vol_ch", 1, 5)
    assert parse({"knobs": [{"knob": 1, "action": "vol_in"}]}).knobs[0].step == 2
