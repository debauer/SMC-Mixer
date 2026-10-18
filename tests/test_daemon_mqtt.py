"""MQTT bridge: topics, ignore list, incoming commands, configuration and the LED override."""

import tomllib
from types import SimpleNamespace
from typing import Any

import pytest

from daemon_fakes import FakeAudio, mixer_pair
from smc_mixer import Button
from smc_mixer_daemon import config_store
from smc_mixer_daemon.config import Config, ConfigError, Strip, parse
from smc_mixer_daemon.controller import Controller
from smc_mixer_daemon.mqtt import Command, Mqtt, parse_command
from smc_mixer_daemon.mqtt_config import MqttConfig

STRIPS = (Strip(1, "sink", "speakers"), Strip(2, "sink", "headset"), Strip(3, "source", "mic"))
OK, REFUSED = SimpleNamespace(is_failure=False), SimpleNamespace(is_failure=True)


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, bool]] = []
        self.subscribed: list[str] = []
        self.calls: list[str] = []
        self.will: tuple[str, str, bool] | None = None

    def username_pw_set(self, username: str, password: str | None) -> None:
        self.calls.append(f"login {username}")

    def tls_set(self) -> None:
        self.calls.append("tls")

    def will_set(self, topic: str, payload: str, retain: bool) -> None:
        self.will = topic, payload, retain

    def reconnect_delay_set(self, low: int, high: int) -> None:
        self.calls.append("delay")

    def connect_async(self, host: str, port: int) -> None:
        self.calls.append(f"connect {host}:{port}")

    def loop_start(self) -> None:
        self.calls.append("loop_start")

    def loop_stop(self) -> None:
        self.calls.append("loop_stop")

    def disconnect(self) -> None:
        self.calls.append("disconnect")

    def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    def publish(self, topic: str, payload: str, retain: bool = False) -> Any:
        self.published.append((topic, payload, retain))
        return SimpleNamespace(wait_for_publish=lambda timeout: None)


def bridge(**options: Any) -> tuple[Mqtt, FakeClient]:
    client = FakeClient()
    mqtt = Mqtt(MqttConfig(enabled=True, **options), lambda config: client)
    mqtt.start()
    mqtt.on_connect(client, None, None, OK)
    client.published.clear()
    return mqtt, client


def message(topic: str, payload: str) -> SimpleNamespace:
    return SimpleNamespace(topic=topic, payload=payload.encode())


def test_start_connects_with_login_will_and_subscriptions() -> None:
    client = FakeClient()
    mqtt = Mqtt(MqttConfig(True, "broker", 1884, "me", "secret", True, "home/smc"), lambda config: client)
    mqtt.start()
    assert client.calls == ["login me", "tls", "delay", "connect broker:1884", "loop_start"]
    assert client.will == ("home/smc/status", "offline", True)
    mqtt.on_connect(client, None, None, OK)
    assert client.published == [("home/smc/status", "online", True)]
    assert client.subscribed == ["home/smc/led/+/set", "home/smc/channel/+/volume/set", "home/smc/channel/+/mute/set"]
    mqtt.close()
    assert client.published[-1] == ("home/smc/status", "offline", True) and client.calls[-2:] == [
        "disconnect",
        "loop_stop",
    ]


def test_refused_connection_publishes_nothing() -> None:
    client = FakeClient()
    mqtt = Mqtt(MqttConfig(True), lambda config: client)
    mqtt.on_connect(client, None, None, REFUSED)
    mqtt.button("REC_1", True)
    assert not mqtt.connected and not client.published and not client.subscribed


def test_inputs_are_published_and_ignored_inputs_are_not() -> None:
    mqtt, client = bridge(ignore=("REC_2", "KNOB_2", "FADER_2"))
    mqtt.button("REC_1", True)
    mqtt.button("REC_1", False)
    mqtt.button("REC_2", True)
    mqtt.knob(1, -3)
    mqtt.knob(2, 1)
    mqtt.fader(1, 40)
    mqtt.fader(2, 40)
    assert client.published == [
        ("smc/button/REC_1", "ON", False),
        ("smc/button/REC_1", "OFF", False),
        ("smc/knob/1", "-3", False),
        ("smc/fader/1", "40", True),
    ]
    mqtt.configure(MqttConfig(ignore=("REC_1",)))
    client.published.clear()
    mqtt.button("REC_1", True)
    mqtt.button("REC_2", True)
    assert client.published == [("smc/button/REC_2", "ON", False)]


def test_channel_state_is_published_on_change_and_again_after_reconnect() -> None:
    mqtt, client = bridge(ignore=("FADER_2",))
    audio = FakeAudio()
    mqtt.channels(STRIPS, audio.snapshot())
    assert client.published == [
        ("smc/channel/1/volume", "50", True),
        ("smc/channel/1/mute", "OFF", True),
        ("smc/channel/3/volume", "75", True),
        ("smc/channel/3/mute", "OFF", True),
    ]
    client.published.clear()
    mqtt.channels(STRIPS, audio.snapshot())
    assert not client.published
    audio.volume(audio.devices["sink", "speakers"], 60)
    mqtt.channels(STRIPS, audio.snapshot())
    assert client.published == [("smc/channel/1/volume", "60", True)]
    mqtt.on_connect(client, None, None, OK)
    client.published.clear()
    mqtt.channels(STRIPS, audio.snapshot())
    assert len(client.published) == 4
    mqtt.channels(STRIPS, None)


@pytest.mark.parametrize(
    ("topic", "payload", "expected"),
    [
        ("smc/led/REC_1/set", "ON", Command("led", "REC_1", True)),
        ("smc/led/REC_1/set", " off ", Command("led", "REC_1", False)),
        ("smc/led/REC_1/set", "auto", Command("led", "REC_1", None)),
        ("smc/channel/2/volume/set", "42.4", Command("volume", 2, 42)),
        ("smc/channel/2/mute/set", "ON", Command("mute", 2, True)),
        ("smc/led/BANK_LEFT/set", "ON", None),  # no LED
        ("smc/led/REC_1/set", "maybe", None),
        ("smc/channel/9/mute/set", "ON", None),
        ("smc/channel/12/mute/set", "ON", None),
        ("smc/channel/1/volume/set", "101", None),
        ("smc/channel/1/volume/set", "loud", None),
        ("smc/channel/1/other/set", "1", None),
        ("smc/button/REC_1", "ON", None),
    ],
)
def test_incoming_messages_are_validated(topic: str, payload: str, expected: Command | None) -> None:
    assert parse_command("smc", topic, payload.encode()) == expected


def test_incoming_commands_are_queued_and_respect_the_ignore_list() -> None:
    mqtt, _ = bridge(ignore=("REC_2", "FADER_3"))
    for topic, payload in [
        ("smc/led/REC_1/set", "ON"),
        ("smc/led/REC_2/set", "ON"),
        ("smc/channel/3/mute/set", "ON"),
        ("smc/channel/1/volume/set", "10"),
        ("smc/led/REC_1/set", "bad"),
    ]:
        mqtt.on_message(None, None, message(topic, payload))
    assert mqtt.commands() == [Command("led", "REC_1", True), Command("volume", 1, 10)]
    assert mqtt.commands() == []


def started() -> tuple[Controller, FakeAudio]:
    mixer, _, _ = mixer_pair()
    audio = FakeAudio()
    controller = Controller(Config(STRIPS), mixer, audio, None)
    assert controller.start()
    audio.calls.clear()
    return controller, audio


def test_led_override_wins_until_auto() -> None:
    controller, _ = started()
    controller.actions.remote(Command("led", "REC_1", True))
    controller.actions.remote(Command("led", "SELECT_1", False))  # normally lit: speakers are the default
    assert controller.leds[Button.REC_1] and not controller.leds[Button.SELECT_1]
    controller.actions.remote(Command("led", "REC_1", None))
    controller.actions.remote(Command("led", "SELECT_1", None))
    assert not controller.leds[Button.REC_1] and controller.leds[Button.SELECT_1]


def test_volume_and_mute_commands_change_the_device_without_unmuting_or_moving_faders() -> None:
    controller, audio = started()
    controller.actions.remote(Command("volume", 3, 20))
    controller.actions.remote(Command("mute", 3, True))
    controller.actions.remote(Command("mute", 3, False))
    assert audio.calls == [("volume", "mic", 20), ("setmute", "mic", True), ("setmute", "mic", False)]
    audio.calls.clear()
    controller.actions.remote(Command("volume", 7, 20))  # no strip there
    assert not audio.calls


def test_mqtt_table_is_validated_and_round_trips() -> None:
    config = parse(
        {
            "strips": [{"channel": 1, "kind": "sink", "name": "a"}],
            "mqtt": {
                "enabled": True,
                "host": "b",
                "username": "u",
                "password": "p",
                "prefix": "x/y",
                "ignore": ["KNOB_2", "REC_1"],
            },
        }
    )
    assert config.mqtt == MqttConfig(True, "b", 1883, "u", "p", False, "x/y", ("KNOB_2", "REC_1"))
    assert parse(tomllib.loads(config_store.render(config))) == config
    assert parse({"strips": [{"channel": 1, "kind": "sink", "name": "a"}]}).mqtt == MqttConfig()
    assert "[mqtt]" not in config_store.render(parse({"strips": [{"channel": 1, "kind": "sink", "name": "a"}]}))


@pytest.mark.parametrize(
    "table",
    [
        {"unknown": 1},
        {"enabled": "yes"},
        {"port": 0},
        {"prefix": "a/#"},
        {"prefix": "/a"},
        {"prefix": "a//b"},
        {"password": "p"},
        {"ignore": ["NOPE"]},
        {"ignore": "REC_1"},
        {"host": " "},
    ],
)
def test_invalid_mqtt_tables_are_rejected(table: dict[str, Any]) -> None:
    with pytest.raises(ConfigError, match="mqtt"):
        parse({"strips": [{"channel": 1, "kind": "sink", "name": "a"}], "mqtt": table})


def test_web_edits_change_only_the_ignore_list() -> None:
    current = parse({"strips": [{"channel": 1, "kind": "sink", "name": "a"}], "mqtt": {"enabled": True, "host": "b"}})
    edited = config_store.from_json(
        {"strips": [{"channel": 1, "kind": "sink", "name": "a"}], "mqtt_ignore": ["FADER_1"]}, current
    )
    assert edited.mqtt == MqttConfig(True, "b", ignore=("FADER_1",))
    kept = config_store.from_json({"strips": [{"channel": 1, "kind": "sink", "name": "a"}]}, edited)
    assert kept.mqtt == edited.mqtt
    with pytest.raises(ConfigError, match=r"mqtt\.ignore"):
        config_store.from_json({"strips": [], "player": "p", "mqtt_ignore": ["NOPE"]}, current)
