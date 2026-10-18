"""Configurable button bindings: validation, actions and LED feedback on fake boundaries."""

import tomllib
from collections.abc import Callable

import pytest

from daemon_fakes import FakeAudio, FakeMedia, mixer_pair
from smc_mixer import Button, ButtonEvent, Layer
from smc_mixer_daemon import config_store
from smc_mixer_daemon.audio import Device
from smc_mixer_daemon.buttons import Binding, effective
from smc_mixer_daemon.config import Config, ConfigError, Strip, parse
from smc_mixer_daemon.controller import Controller

STRIPS = (Strip(1, "sink", "speakers"), Strip(2, "sink", "headset"), Strip(3, "source", "mic"))


def started(*buttons: Binding, media: FakeMedia | None = None) -> tuple[Controller, FakeAudio]:
    mixer, _, _ = mixer_pair()
    audio = FakeAudio()
    controller = Controller(Config(STRIPS, buttons=buttons), mixer, audio, media)
    assert controller.start()
    audio.calls.clear()
    return controller, audio


def tap(controller: Controller, button: Button) -> None:
    controller.event(ButtonEvent(button, True, Layer.DAW))
    controller.event(ButtonEvent(button, False, Layer.DAW))


def test_defaults_are_unchanged_and_none_disables_a_default() -> None:
    bindings = effective((), {1})
    assert {b.action for b in bindings.values()} == {"mute_ch", "select_ch", "play", "pause"}
    assert Button.PLAY not in effective((Binding(Button.PLAY, "none"),), {1})


def test_mic_mute_toggles_all_real_inputs_and_lights_led() -> None:
    controller, audio = started(Binding(Button.REC_1, "mic_mute", led="active"))
    audio.devices["source", "mon"] = audio.devices["source", "mic"].__class__(
        "source", "mon", 9, "Monitor", False, 10, True
    )
    tap(controller, Button.REC_1)
    assert audio.calls == [("setmute", "mic", True)]
    assert controller.leds[Button.REC_1]
    tap(controller, Button.REC_1)
    assert audio.calls[-1] == ("setmute", "mic", False) and not controller.leds[Button.REC_1]
    assert all(call[1] != "mon" for call in audio.calls)


def test_push_to_talk_unmutes_while_held_only() -> None:
    controller, audio = started(Binding(Button.REC_2, "ptt", led="active"))
    controller.event(ButtonEvent(Button.REC_2, True, Layer.DAW))
    assert audio.calls == [("setmute", "mic", False)] and controller.leds[Button.REC_2]
    controller.event(ButtonEvent(Button.REC_2, True, Layer.DAW))
    assert len(audio.calls) == 1
    controller.event(ButtonEvent(Button.REC_2, False, Layer.DAW))
    assert audio.calls[-1] == ("setmute", "mic", True) and not controller.leds[Button.REC_2]


def test_output_cycle_rotates_configured_sinks_only() -> None:
    controller, audio = started(Binding(Button.REC_1, "out_cycle"))
    tap(controller, Button.REC_1)
    assert audio.calls == [("select", "headset")]
    tap(controller, Button.REC_1)
    assert audio.calls[-1] == ("select", "speakers")


def test_media_actions_and_always_on_led() -> None:
    media = FakeMedia()
    controller, _ = started(
        Binding(Button.RIGHT, "next"),
        Binding(Button.LEFT, "previous"),
        Binding(Button.RECORD, "play_pause", led="on"),
        media=media,
    )
    for button in (Button.RIGHT, Button.LEFT, Button.RECORD, Button.PLAY):
        tap(controller, button)
    assert media.actions == ["next", "previous", "play-pause", "play"]
    assert controller.leds[Button.RECORD]


def test_channel_actions_on_free_buttons() -> None:
    controller, audio = started(
        Binding(Button.REC_3, "mute_ch", 1, "active"), Binding(Button.SOLO_3, "select_ch", 2, "active")
    )
    tap(controller, Button.REC_3)
    tap(controller, Button.SOLO_3)
    assert audio.calls == [("mute", "speakers"), ("select", "headset")]
    assert controller.leds[Button.REC_3] and controller.leds[Button.SOLO_3]


def test_unbound_and_suspended_buttons_do_nothing() -> None:
    controller, audio = started(Binding(Button.REC_1, "mic_mute"))
    tap(controller, Button.REC_2)
    controller.suspend()
    tap(controller, Button.REC_1)
    assert not audio.calls


BASE = {"strips": [{"channel": 1, "kind": "sink", "name": "a"}]}


@pytest.mark.parametrize(
    "buttons",
    [
        [{"button": "NOPE", "action": "next"}],
        [{"button": "REC_1", "action": "explode"}],
        [{"button": "REC_1", "action": "mute_ch"}],
        [{"button": "REC_1", "action": "mute_ch", "channel": 5}],
        [{"button": "REC_1", "action": "next", "channel": 1}],
        [{"button": "REC_1", "action": "next", "led": "active"}],
        [{"button": "UP", "action": "next", "led": "on"}],
        [{"button": "REC_1", "action": "next", "led": "blink"}],
        [{"button": "REC_1", "action": "next"}, {"button": "REC_1", "action": "play"}],
        [{"button": "MUTE_1", "action": "next"}],
        [{"button": "REC_1", "action": "next", "typo": 1}],
    ],
)
def test_invalid_bindings_are_rejected(buttons: list[dict[str, object]]) -> None:
    with pytest.raises(ConfigError):
        parse({**BASE, "buttons": buttons})


def test_buttons_round_trip_and_free_channel_buttons_are_bindable() -> None:
    data = {
        **BASE,
        "buttons": [
            {"button": "MUTE_2", "action": "mic_mute", "channel": None, "led": "active"},
            {"button": "REC_1", "action": "select_ch", "channel": 1, "led": "off"},
        ],
    }
    config = config_store.from_json(data, Config(()))
    again = parse(tomllib.loads(config_store.render(config)))
    assert sorted(again.buttons, key=lambda b: b.button) == sorted(config.buttons, key=lambda b: b.button)
    assert len(again.buttons) == 2
    assert parse({"buttons": [{"button": "UP", "action": "next"}]}).buttons[0].action == "next"


def test_selection_reverted_by_the_audio_server_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    class Stubborn(FakeAudio):
        def select(self, device: Device, valid: Callable[[], bool] = lambda: True) -> None:
            self.calls.append(("select", device.name))

    mixer, _, _ = mixer_pair()
    audio = Stubborn()
    audio.devices["source", "mic2"] = Device("source", "mic2", 4, "Mic 2", False, 40)
    strips = (Strip(3, "source", "mic"), Strip(4, "source", "mic2"))
    controller = Controller(Config(strips), mixer, audio, None)
    assert controller.start()
    caplog.set_level("INFO")
    tap(controller, Button.SELECT_4)
    assert "default source is still mic after selecting mic2" in caplog.text
    tap(controller, Button.SELECT_3)
    assert "default source is now mic" in caplog.text
