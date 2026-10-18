"""Configurable button bindings: validation, actions and LED feedback on fake boundaries."""

import itertools
import tomllib
from collections.abc import Callable

import pytest

from daemon_fakes import FakeAudio, FakeMedia, mixer_pair
from smc_mixer import Button, ButtonEvent, Layer
from smc_mixer_daemon import actions, config_store
from smc_mixer_daemon.audio import Device, Snapshot
from smc_mixer_daemon.buttons import Binding, effective
from smc_mixer_daemon.commands import BackendError
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


def test_selection_reverted_by_the_audio_server_is_reported(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(actions.time, "sleep", lambda _: None)

    class Stubborn(FakeAudio):
        def select(self, device: Device, valid: Callable[[], bool] = lambda: True) -> None:
            self.calls.append(("select", device.name))

        def ports(self, device: Device) -> str:
            return "active port 'x'; ports: mic2: not available"

    mixer, _, _ = mixer_pair()
    audio = Stubborn()
    audio.devices["source", "mic2"] = Device("source", "mic2", 4, "Mic 2", False, 40)
    strips = (Strip(3, "source", "mic"), Strip(4, "source", "mic2"))
    controller = Controller(Config(strips), mixer, audio, None)
    assert controller.start()
    caplog.set_level("INFO")
    tap(controller, Button.SELECT_4)
    assert "default source is still mic after selecting mic2" in caplog.text
    assert "mic2: not available" in caplog.text
    tap(controller, Button.SELECT_3)
    assert "default source is now mic" in caplog.text


def test_slow_session_manager_is_not_reported_as_rejection(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Slow(FakeAudio):
        pending: str | None = None

        def select(self, device: Device, valid: Callable[[], bool] = lambda: True) -> None:
            self.pending = device.name

        def snapshot(self) -> Snapshot:
            snapshot = super().snapshot()
            if self.pending and self.calls.count(("snapshot",)) >= 1:
                self.source, self.pending = self.pending, None
            self.calls.append(("snapshot",))
            return snapshot

    monkeypatch.setattr(actions.time, "sleep", lambda _: None)
    mixer, _, _ = mixer_pair()
    audio = Slow()
    audio.devices["source", "mic2"] = Device("source", "mic2", 4, "Mic 2", False, 40)
    controller = Controller(Config((Strip(3, "source", "mic"), Strip(4, "source", "mic2"))), mixer, audio, None)
    assert controller.start()
    caplog.set_level("INFO")
    tap(controller, Button.SELECT_4)
    assert "is now mic2" in caplog.text and "still" not in caplog.text


def test_play_and_pause_leds_follow_player_state(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr("smc_mixer_daemon.controller.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("smc_mixer_daemon.actions.time.monotonic", lambda: clock[0])
    media = FakeMedia()
    controller, _ = started(Binding(Button.RECORD, "play_pause", led="active"), media=media)
    controller.poll_media()
    assert controller.media_state == "Paused"
    assert (controller.leds[Button.PLAY], controller.leds[Button.STOP], controller.leds[Button.RECORD]) == (
        False,
        True,
        False,
    )
    media.state = "Playing"
    controller.poll_media()  # throttled: still the old state
    assert controller.media_state == "Paused"
    clock[0] += 1.1
    controller.poll_media()
    assert (controller.leds[Button.PLAY], controller.leds[Button.STOP], controller.leds[Button.RECORD]) == (
        True,
        False,
        True,
    )
    media.state = None
    clock[0] += 1.1
    controller.poll_media()
    assert controller.media_state is None and not controller.leds[Button.PLAY] and not controller.leds[Button.STOP]


def test_media_status_parses_playerctl_output() -> None:
    from smc_mixer_daemon.commands import BackendError
    from smc_mixer_daemon.media import Media

    def runner(args: list[str]) -> str:
        return "firefox.instance1" if "--list-all" in args else "Playing"

    assert Media("firefox", runner).status() == "Playing"
    with pytest.raises(BackendError):
        Media("firefox", lambda args: "firefox" if "--list-all" in args else "weird").status()


def test_next_and_previous_blink_three_times(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    for module in ("controller", "actions"):
        monkeypatch.setattr(f"smc_mixer_daemon.{module}.time.monotonic", lambda: clock[0])
    media = FakeMedia()
    controller, _ = started(Binding(Button.FAST_FORWARD, "next"), Binding(Button.UP, "previous"), media=media)
    tap(controller, Button.FAST_FORWARD)
    tap(controller, Button.UP)  # no LED on this button: must not fail
    seen = []
    for _ in range(40):
        controller.feedback()
        seen.append(controller.leds[Button.FAST_FORWARD])
        clock[0] += 0.06
    edges = sum(1 for a, b in itertools.pairwise(seen) if not a and b) + int(seen[0])
    assert edges == 3 and not seen[-1] and not controller.pulses
    assert media.actions == ["next", "previous"]


def test_failed_media_request_does_not_blink() -> None:
    class Broken(FakeMedia):
        def control(self, action: str, valid: Callable[[], bool] = lambda: True) -> None:
            raise BackendError("no player")

    controller, _ = started(Binding(Button.FAST_FORWARD, "next"), media=Broken())
    tap(controller, Button.FAST_FORWARD)
    assert not controller.pulses


def test_output_mute_toggles_every_sink_and_lights_led() -> None:
    controller, audio = started(Binding(Button.REC_4, "out_mute", led="active"))
    tap(controller, Button.REC_4)  # headset was already muted: mute the rest
    assert audio.calls == [("setmute", "speakers", True), ("setmute", "headset", True)]
    assert controller.leds[Button.REC_4]
    tap(controller, Button.REC_4)
    assert audio.calls[-2:] == [("setmute", "speakers", False), ("setmute", "headset", False)]
    assert not controller.leds[Button.REC_4]
    assert all(call[1] != "mic" for call in audio.calls)


def test_stateful_actions_default_to_a_state_led_unless_set_otherwise() -> None:
    parsed = parse(
        {
            "buttons": [
                {"button": "REC_1", "action": "out_mute"},
                {"button": "REC_2", "action": "out_mute", "led": "off"},
                {"button": "REC_3", "action": "next"},
                {"button": "UP", "action": "mic_mute"},
            ]
        }
    )
    assert [b.led for b in parsed.buttons] == ["active", "off", "off", "off"]
