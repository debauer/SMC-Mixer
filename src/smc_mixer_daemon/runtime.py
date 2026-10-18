"""User-session daemon lifecycle and serialized control loop."""

import logging
import threading
import time
from collections.abc import Callable
from itertools import islice
from pathlib import Path

from smc_mixer import ButtonEvent, DeviceMode, FaderEvent, KnobEvent, MidiProtocol, SmcMixer, SmcMixerError
from smc_mixer_daemon.audio import Audio
from smc_mixer_daemon.commands import BackendError
from smc_mixer_daemon.config import Config, ConfigError, load
from smc_mixer_daemon.controller import Controller
from smc_mixer_daemon.inbox import Inbox
from smc_mixer_daemon.media import Media
from smc_mixer_daemon.mqtt import Mqtt
from smc_mixer_daemon.status import Board, BoardHandler, describe
from smc_mixer_daemon.subscription import Subscription
from smc_mixer_daemon.takeover import percent
from smc_mixer_daemon.web import server as web_server
from smc_mixer_daemon.web.api import Api
from smc_mixer_daemon.web.auth import ensure_token

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


def control_loop(
    config: Config,
    stop: threading.Event,
    subscription: Subscription,
    board: Board | None = None,
    mqtt: Mqtt | None = None,
) -> None:
    """Run until stopped or until `board.reload` asks for a restart with a new configuration."""
    board = board or Board(config)
    audio = Audio()
    media = Media(config.player) if config.player else None
    notices = Notices()
    mixer: SmcMixer | None = None
    controller: Controller | None = None
    inbox = Inbox()
    next_connect = next_initialize = next_audio = next_publish = 0.0
    audio_delay = 1.0
    try:
        while not stop.is_set() and not board.reload.is_set():
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
                if controller.generation != generation or not mixer.connected:
                    board.clear_keys()
                for event in events:
                    if isinstance(event, ButtonEvent) and event.protocol == MidiProtocol.MCU and not event.ambiguous:
                        board.key(event.button.name, event.pressed)
                        if mqtt:
                            mqtt.button(event.button.name, event.pressed)
                    elif isinstance(event, KnobEvent) and event.protocol == MidiProtocol.MCU and event.delta:
                        board.turn(event.knob, event.delta)
                        if mqtt:
                            mqtt.knob(event.knob, event.delta)
                    elif isinstance(event, FaderEvent) and event.protocol == MidiProtocol.MCU and not event.ambiguous:
                        if mqtt:
                            mqtt.fader(event.fader, percent(event.value))
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
                    commands = mqtt.commands() if mqtt else []
                    for command in commands:
                        if controller.ready:
                            controller.actions.remote(command)
                        else:
                            log.info("ignoring MQTT command %s: mixer control is not active", command)
                    controller.poll_media()
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
            elif mqtt:
                mqtt.commands()  # nobody to execute them; stale commands must not pile up or fire later
            if tick >= next_publish:
                board.publish(describe(mixer, controller, config))
                if mqtt:
                    mqtt.channels(config.strips, controller.snapshot if controller and controller.ready else None)
                next_publish = tick + 0.2
            stop.wait(max(0, 0.025 - (time.monotonic() - tick)))
    finally:
        try:
            if controller:
                controller.shutdown()
        finally:
            if mixer:
                mixer.close()


def run(config_path: Path, config: Config, stop: threading.Event) -> None:
    board = Board(config)
    handler = BoardHandler(board)
    logging.getLogger("smc_mixer_daemon").addHandler(handler)
    subscription = Subscription()
    subscription.start()
    mqtt = Mqtt(config.mqtt) if config.mqtt.enabled else None
    if mqtt:
        mqtt.start()
    web = None
    if config.web.enabled:
        token_file = config_path.parent / "web-token"
        api = Api(board, config_path, token=ensure_token(token_file), token_file=token_file)
        web = web_server.start(config.web, api)
    try:
        while not stop.is_set():
            board.reload.clear()
            board.config = config
            if mqtt:
                mqtt.configure(config.mqtt)
            control_loop(config, stop, subscription, board, mqtt)
            if board.reload.is_set() and not stop.is_set():
                try:
                    config = load(config_path)
                except ConfigError as exc:
                    log.error("reload failed, keeping previous configuration: %s", exc)
                else:
                    log.info("configuration reloaded")
    finally:
        if web:
            web[0].shutdown()
            web[0].server_close()
        subscription.close()
        if mqtt:
            mqtt.close()
        logging.getLogger("smc_mixer_daemon").removeHandler(handler)
