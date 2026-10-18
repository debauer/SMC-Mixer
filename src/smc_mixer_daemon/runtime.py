"""User-session daemon lifecycle and serialized control loop."""

import logging
import threading
import time
from collections.abc import Callable
from itertools import islice

from smc_mixer import DeviceMode, SmcMixer, SmcMixerError
from smc_mixer_daemon.audio import Audio
from smc_mixer_daemon.commands import BackendError
from smc_mixer_daemon.config import Config
from smc_mixer_daemon.controller import Controller
from smc_mixer_daemon.inbox import Inbox
from smc_mixer_daemon.media import Media
from smc_mixer_daemon.subscription import Subscription

log = logging.getLogger(__name__)


def generation_guard(source: Inbox, target: Controller, stop: threading.Event) -> Callable[[], bool]:
    return lambda: not stop.is_set() and source.generation == target.generation


class Notices:
    def __init__(self) -> None:
        self.last: dict[str, tuple[str, float]] = {}

    def error(self, area: str, error: object) -> None:
        message = str(error)
        previous, reported = self.last.get(area, ("", 0.0))
        if previous != message or time.monotonic() - reported >= 30:
            log.warning("%s: %s", area, message)
            self.last[area] = message, time.monotonic()


def control_loop(config: Config, stop: threading.Event, subscription: Subscription) -> None:
    audio = Audio()
    media = Media(config.player) if config.player else None
    notices = Notices()
    mixer: SmcMixer | None = None
    controller: Controller | None = None
    inbox = Inbox()
    next_connect = next_initialize = next_audio = 0.0
    audio_delay = 1.0
    try:
        while not stop.is_set():
            tick = time.monotonic()
            if mixer is None and tick >= next_connect:
                try:
                    mixer = SmcMixer.open()
                    inbox = Inbox()
                    mixer.add_listener(inbox.put)
                    controller = Controller(config, mixer, audio, media)
                    controller.valid = generation_guard(inbox, controller, stop)
                    log.info("mixer ports opened")
                except (SmcMixerError, OSError, RuntimeError) as exc:
                    notices.error("waiting for mixer", exc)
                    next_connect = tick + 2
            if mixer is not None and controller is not None:
                # The library also queues listener-delivered events. Drain its copy.
                for _ in islice(mixer.events(timeout=0), 4096):
                    pass
                generation, events = inbox.take()
                try:
                    if controller.generation != generation:
                        controller.suspend()
                        controller.generation = generation
                        next_initialize = 0
                    elif not mixer.connected or mixer.mode != DeviceMode.DAW:
                        controller.suspend()
                    if not controller.active and tick >= next_initialize:
                        next_initialize = tick + 2
                        if mixer.connected:
                            if not controller.start():
                                notices.error("control suspended", "confirmed DAW mode and fresh faders required")
                            events = []
                    if controller.ready:
                        if tick >= next_audio and (subscription.changed.is_set() or controller.snapshot is None):
                            subscription.changed.clear()
                            controller.refresh()
                            audio_delay = 1
                            next_audio = time.monotonic() + 0.1
                        elif tick >= next_audio + 2:
                            controller.refresh()
                            audio_delay = 1
                            next_audio = time.monotonic()
                        for event in events:
                            if inbox.generation != generation or stop.is_set():
                                break
                            controller.event(event)
                    controller.feedback()
                except BackendError as exc:
                    notices.error("audio backend", exc)
                    controller.offline()
                    next_audio = next_initialize = time.monotonic() + audio_delay
                    audio_delay = min(10, audio_delay * 2)
                except SmcMixerError as exc:
                    notices.error("mixer control", exc)
                    controller.suspend()
                    next_initialize = time.monotonic() + 2
            stop.wait(max(0, 0.025 - (time.monotonic() - tick)))
    finally:
        try:
            if controller:
                controller.shutdown()
        finally:
            if mixer:
                mixer.close()


def run(config: Config, stop: threading.Event) -> None:
    subscription = Subscription()
    subscription.start()
    try:
        control_loop(config, stop, subscription)
    finally:
        subscription.close()
