"""Physical MIDI input drives isolated application state, not host audio."""

from collections.abc import Callable
from dataclasses import replace

import pytest

from daemon_fakes import FakeAudio, FakeMedia, mixer_pair
from smc_mixer import Button, ButtonEvent, DeviceMode, FaderEvent, Layer, ModeEvent
from smc_mixer_daemon.commands import BackendError
from smc_mixer_daemon.config import Config, Strip
from smc_mixer_daemon.controller import Controller
from smc_mixer_daemon.inbox import Inbox
from smc_mixer_daemon.takeover import Takeover

STRIPS = (Strip(1, "sink", "speakers"), Strip(2, "sink", "headset"), Strip(3, "source", "mic"))


def test_direct_startup_and_external_changes_do_not_fight() -> None:
    mixer, _, _ = mixer_pair()
    audio = FakeAudio()
    controller = Controller(Config(STRIPS), mixer, audio, None)
    assert controller.start()
    assert audio.calls == [("volume", "speakers", 100), ("volume", "headset", 67), ("volume", "mic", 0)]
    audio.devices["sink", "speakers"] = replace(audio.devices["sink", "speakers"], volume=12, muted=True)
    audio.calls.clear()
    controller.refresh()
    assert not audio.calls
    assert controller.leds[Button.MUTE_1]
    controller.event(FaderEvent(1, 0, Layer.DAW))
    assert audio.calls == [("volume", "speakers", 0)]
    assert audio.devices["sink", "speakers"].muted
    mixer.close()


def test_buttons_once_per_press_and_independent_defaults() -> None:
    mixer, _, _ = mixer_pair()
    audio = FakeAudio()
    controller = Controller(Config(STRIPS), mixer, audio, None)
    controller.start()
    audio.calls.clear()
    press = ButtonEvent(Button.MUTE_1, True, Layer.DAW)
    controller.event(press)
    controller.event(press)
    assert audio.calls == [("mute", "speakers")]
    controller.event(ButtonEvent(Button.MUTE_1, False, Layer.DAW))
    controller.event(press)
    assert not controller.leds[Button.MUTE_1]
    controller.event(ButtonEvent(Button.SOLO_2, True, Layer.DAW))
    assert controller.leds[Button.SOLO_2] and not controller.leds[Button.SOLO_1]
    assert controller.leds[Button.SOLO_3]
    controller.event(ButtonEvent(Button.SOLO_3, True, Layer.DAW))
    assert audio.calls[-1] == ("select", "mic")
    mixer.close()


def test_unassigned_controls_cc_and_ambiguous_events_are_ignored() -> None:
    mixer, _, _ = mixer_pair()
    audio = FakeAudio()
    controller = Controller(Config(STRIPS), mixer, audio, None)
    controller.start()
    audio.calls.clear()
    for event in [
        FaderEvent(8, 0, Layer.DAW),
        FaderEvent(1, 0, Layer.CC),
        FaderEvent(1, 0, Layer.DAW, ambiguous=True),
        ButtonEvent(Button.REC_1, True, Layer.DAW),
        ButtonEvent(Button.SELECT_1, True, Layer.DAW),
    ]:
        controller.event(event)
    assert audio.calls == []
    mixer.close()


def test_mode_gating_and_fresh_snapshots_after_return() -> None:
    mixer, _, device = mixer_pair()
    audio = FakeAudio()
    controller = Controller(Config(STRIPS), mixer, audio, None)
    device.ram[0] = 1
    assert not controller.start()
    assert not audio.calls
    device.ram[0] = 0
    assert controller.start()
    audio.calls.clear()
    device.ram[0] = 1
    mixer.read_mode()
    controller.event(FaderEvent(1, 0, Layer.DAW))
    assert not audio.calls
    controller.suspend()
    device.ram[0] = 0
    device.ram[0x327] = 0
    assert controller.start()
    assert audio.calls[0] == ("volume", "speakers", 0)
    mixer.close()


def test_missing_device_reappears_with_new_index_and_fresh_position() -> None:
    mixer, _, device = mixer_pair()
    audio = FakeAudio()
    absent = audio.devices.pop(("sink", "headset"))
    controller = Controller(Config(STRIPS), mixer, audio, None)
    controller.start()
    assert not controller.leds[Button.MUTE_2]
    audio.calls.clear()
    device.ram[0x32A] = 127
    audio.devices[absent.key] = replace(absent, index=99)
    controller.refresh()
    assert audio.calls == [("volume", "headset", 100)]
    assert controller.leds[Button.MUTE_2]
    mixer.close()


def test_audio_recovery_and_generation_invalidation() -> None:
    mixer, _, _ = mixer_pair()
    audio = FakeAudio()
    valid = True
    controller = Controller(Config(STRIPS), mixer, audio, None, lambda: valid)
    controller.start()
    controller.offline()
    audio.calls.clear()
    controller.refresh()
    assert len(audio.calls) == 3
    audio.calls.clear()
    valid = False
    controller.event(ButtonEvent(Button.SOLO_2, True, Layer.DAW))
    assert audio.calls == []
    mixer.close()


def test_soft_takeover_crossing_and_external_rearm() -> None:
    mixer, _, _ = mixer_pair()
    audio = FakeAudio()
    controller = Controller(Config(STRIPS, soft_takeover=True), mixer, audio, None)
    controller.start()
    assert not audio.calls and controller.blinks[1]
    controller.event(FaderEvent(1, 100, Layer.DAW))
    assert not audio.calls
    controller.event(FaderEvent(1, 50, Layer.DAW))
    assert audio.calls == [("volume", "speakers", 39)]
    controller.refresh()
    assert not controller.blinks[1]
    audio.devices["sink", "speakers"] = replace(audio.devices["sink", "speakers"], volume=90)
    controller.refresh()
    assert controller.blinks[1]
    audio.calls.clear()
    controller.event(FaderEvent(1, 60, Layer.DAW))
    assert not audio.calls
    mixer.close()


def test_takeover_tolerance_self_echoes_and_clamped_target() -> None:
    state = Takeover(0, 50, True)
    assert not state.move(48)
    assert state.move(49)
    state.written(49)
    state.written(60)
    state.observe(49.001)
    state.observe(60.001)
    assert state.owned
    state.observe(120)
    assert not state.owned
    assert state.move(100)
    assert Takeover(0, 100, False).move(0)


def test_media_failure_does_not_disable_audio(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenMedia(FakeMedia):
        def control(self, action: str, valid: Callable[[], bool] = lambda: True) -> None:
            raise BackendError("no player")

    mixer, _, _ = mixer_pair()
    audio = FakeAudio()
    controller = Controller(Config(STRIPS), mixer, audio, BrokenMedia())
    controller.start()
    controller.event(ButtonEvent(Button.PLAY, True, Layer.DAW))
    controller.event(FaderEvent(1, 0, Layer.DAW))
    assert "media request failed" in caplog.text and controller.ready
    assert audio.calls[-1] == ("volume", "speakers", 0)
    mixer.close()


def test_transport_and_shutdown_do_not_change_audio() -> None:
    mixer, _, _ = mixer_pair()
    audio, media = FakeAudio(), FakeMedia()
    controller = Controller(Config(STRIPS), mixer, audio, media)
    controller.start()
    controller.event(ButtonEvent(Button.PLAY, True, Layer.DAW))
    controller.event(ButtonEvent(Button.STOP, True, Layer.DAW))
    assert media.actions == ["play", "pause"]
    audio.calls.clear()
    controller.shutdown()
    assert not audio.calls and not any(controller.leds.values()) and not any(controller.blinks.values())
    mixer.close()


def test_inbox_coalesces_without_crossing_buttons_and_discards_previous_generation() -> None:
    inbox = Inbox()
    for value in range(128):
        inbox.put(FaderEvent(1, value, Layer.DAW))
    inbox.put(ButtonEvent(Button.SOLO_1, True, Layer.DAW))
    inbox.put(FaderEvent(1, 0, Layer.DAW))
    generation, events = inbox.take()
    assert generation == 0 and len(events) == 3
    assert events[0] == FaderEvent(1, 127, Layer.DAW) and events[-1] == FaderEvent(1, 0, Layer.DAW)
    inbox.put(FaderEvent(1, 100, Layer.DAW))
    inbox.put(ModeEvent(DeviceMode.USER))
    assert inbox.take() == (1, [])
