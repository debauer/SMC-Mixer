"""MQTT bridge: publishes mixer inputs and accepts LED, volume and mute commands.

Topics below `<prefix>/`:
  status                      online/offline (retained, last will)
  button/<NAME>               ON while held, OFF on release
  knob/<n>                    signed relative steps
  fader/<n>                   physical position 0-100 (retained)
  channel/<n>/volume|mute     device state of a configured strip (retained)
  led/<NAME>/set              ON, OFF or AUTO (back to the normal LED behavior)
  channel/<n>/volume/set      0-100
  channel/<n>/mute/set        ON or OFF
"""

import logging
import queue
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from smc_mixer import LED_BUTTONS
from smc_mixer_daemon.audio import Snapshot
from smc_mixer_daemon.config import ConfigError, Strip
from smc_mixer_daemon.mqtt_config import MqttConfig

log = logging.getLogger(__name__)

LED_NAMES = {button.name for button in LED_BUTTONS}
SWITCH = {"ON": True, "OFF": False}


@dataclass(frozen=True)
class Command:
    kind: str  # led, volume or mute
    target: str | int  # button name or channel
    value: int | bool | None  # led: None means automatic


def parse_command(prefix: str, topic: str, payload: bytes) -> Command | None:
    """Return the command for a message, or None (with a warning) if it is not valid."""
    parts = topic.removeprefix(prefix + "/").split("/")
    text = payload.decode("utf-8", "replace").strip()
    try:
        if len(parts) == 3 and parts[0] == "led" and parts[1] in LED_NAMES and parts[2] == "set":
            if text.upper() == "AUTO":
                return Command("led", parts[1], None)
            return Command("led", parts[1], SWITCH[text.upper()])
        if len(parts) == 4 and parts[0] == "channel" and parts[3] == "set" and parts[1] in tuple("12345678"):
            channel = int(parts[1])
            if parts[2] == "mute":
                return Command("mute", channel, SWITCH[text.upper()])
            if parts[2] == "volume" and 0 <= (volume := round(float(text))) <= 100:
                return Command("volume", channel, volume)
    except (KeyError, ValueError):
        pass
    log.warning("ignoring MQTT message %s = %r", topic, text)
    return None


def paho_client(config: MqttConfig) -> Any:
    try:
        from paho.mqtt import client as paho
    except ImportError as exc:
        raise ConfigError("mqtt is enabled but paho-mqtt is not installed (pip install 'smc-mixer[mqtt]')") from exc
    return paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=f"{config.prefix.replace('/', '-')}-daemon")


class Mqtt:
    def __init__(self, config: MqttConfig, factory: Callable[[MqttConfig], Any] = paho_client) -> None:
        self.config = config
        self.ignore = frozenset(config.ignore)
        self.connected = False
        self.sent: dict[str, str] = {}
        self.incoming: queue.SimpleQueue[Command] = queue.SimpleQueue()
        self.client = factory(config)

    def topic(self, *parts: object) -> str:
        return "/".join((self.config.prefix, *map(str, parts)))

    def start(self) -> None:
        client, config = self.client, self.config
        if config.username is not None:
            client.username_pw_set(config.username, config.password)
        if config.tls:
            client.tls_set()
        client.will_set(self.topic("status"), "offline", retain=True)
        client.on_connect, client.on_disconnect = self.on_connect, self.on_disconnect
        client.on_message = self.on_message
        client.reconnect_delay_set(1, 30)
        client.connect_async(config.host, config.port)
        client.loop_start()

    def configure(self, config: MqttConfig) -> None:
        """Apply a reloaded configuration; broker settings only change on restart."""
        self.ignore = frozenset(config.ignore)

    def on_connect(self, client: Any, userdata: Any, flags: Any, reason: Any, properties: Any = None) -> None:
        if reason.is_failure:
            log.warning("MQTT connection refused: %s", reason)
            return
        self.connected = True
        self.sent.clear()  # retained state is republished after a reconnect
        log.info("MQTT connected to %s:%s, topics below %s/", self.config.host, self.config.port, self.config.prefix)
        client.publish(self.topic("status"), "online", retain=True)
        for pattern in ("led/+/set", "channel/+/volume/set", "channel/+/mute/set"):
            client.subscribe(self.topic(pattern))

    def on_disconnect(self, client: Any, userdata: Any, flags: Any, reason: Any, properties: Any = None) -> None:
        if self.connected:
            log.warning("MQTT disconnected: %s", reason)
        self.connected = False

    def on_message(self, client: Any, userdata: Any, message: Any) -> None:
        command = parse_command(self.config.prefix, message.topic, bytes(message.payload))
        if command is None:
            return
        key = command.target if command.kind == "led" else f"FADER_{command.target}"
        if key not in self.ignore:
            self.incoming.put(command)

    def commands(self) -> list[Command]:
        found = []
        while not self.incoming.empty():
            found.append(self.incoming.get_nowait())
        return found

    def publish(self, key: str, topic: str, payload: str, retain: bool = False, changed_only: bool = False) -> None:
        if key in self.ignore or not self.connected:
            return
        if changed_only and self.sent.get(topic) == payload:
            return
        self.client.publish(topic, payload, retain=retain)
        if changed_only:
            self.sent[topic] = payload

    def button(self, name: str, pressed: bool) -> None:
        self.publish(name, self.topic("button", name), "ON" if pressed else "OFF")

    def knob(self, knob: int, delta: int) -> None:
        self.publish(f"KNOB_{knob}", self.topic("knob", knob), str(delta))

    def fader(self, fader: int, percent: int) -> None:
        self.publish(f"FADER_{fader}", self.topic("fader", fader), str(percent), retain=True)

    def channels(self, strips: tuple[Strip, ...], snapshot: Snapshot | None) -> None:
        for strip in strips if snapshot else ():
            device = snapshot.devices.get((strip.kind, strip.name)) if snapshot else None
            if device is None:
                continue
            key, base = f"FADER_{strip.channel}", ("channel", strip.channel)
            self.publish(key, self.topic(*base, "volume"), str(round(device.volume)), True, True)
            self.publish(key, self.topic(*base, "mute"), "ON" if device.muted else "OFF", True, True)

    def close(self) -> None:
        if self.connected:
            info = self.client.publish(self.topic("status"), "offline", retain=True)
            try:
                info.wait_for_publish(1)
            except (RuntimeError, ValueError) as exc:
                log.warning("MQTT offline notice not delivered: %s", exc)
        self.connected = False  # a deliberate disconnect is not worth a warning
        self.client.disconnect()
        self.client.loop_stop()
