"""MQTT settings page: guarded broker edits, live reconnect and connection status."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from daemon_fakes import FakeAudio
from smc_mixer_daemon import config_store, runtime
from smc_mixer_daemon.config import Config, ConfigError, Strip, load
from smc_mixer_daemon.mqtt import Mqtt
from smc_mixer_daemon.mqtt_config import MqttConfig
from smc_mixer_daemon.status import Board, describe
from smc_mixer_daemon.web.api import Api
from smc_mixer_daemon.web.auth import AuthError
from test_daemon_mqtt import OK, REFUSED, FakeClient

STRIPS = (Strip(1, "sink", "speakers"),)
BASE: dict[str, Any] = {
    "soft_takeover": False,
    "player": None,
    "strips": [{"channel": 1, "kind": "sink", "name": "speakers"}],
}
BROKER = {"enabled": True, "host": "broker", "port": 1884, "username": "me", "tls": True, "prefix": "home/smc"}


def api(tmp_path: Path, mqtt: MqttConfig | None = None) -> Api:
    return Api(Board(Config(STRIPS, mqtt=mqtt or MqttConfig())), tmp_path / "c.toml", FakeAudio, token="secret")


def test_config_json_reports_settings_but_never_the_password(tmp_path: Path) -> None:
    service = api(tmp_path, MqttConfig(True, "b", 1883, "me", "hunter2"))
    row = service.get("/api/config")["mqtt"]
    assert row == {
        "enabled": True,
        "host": "b",
        "port": 1883,
        "username": "me",
        "password_set": True,
        "tls": False,
        "prefix": "smc",
    }
    assert "hunter2" not in str(service.get("/api/config"))


def test_broker_changes_need_the_token_but_the_ignore_list_does_not(tmp_path: Path) -> None:
    service = api(tmp_path)
    for token in (None, "wrong"):
        with pytest.raises(AuthError):
            service.put("/api/config", {**BASE, "mqtt": BROKER}, token)
    assert not (tmp_path / "c.toml").exists()
    service.put("/api/config", {**BASE, "mqtt": {**BROKER, "password": "pw"}}, "secret")
    saved = load(tmp_path / "c.toml").mqtt
    assert saved == MqttConfig(True, "broker", 1884, "me", "pw", True, "home/smc")
    service.put("/api/config", {**BASE, "mqtt": BROKER, "mqtt_ignore": ["KNOB_1"]}, None)  # same broker settings
    assert load(tmp_path / "c.toml").mqtt.ignore == ("KNOB_1",)
    with pytest.raises(AuthError):
        service.put("/api/config", {**BASE, "mqtt": {**BROKER, "host": "elsewhere"}}, None)
    with pytest.raises(AuthError):
        service.put("/api/config", {**BASE, "mqtt": {**BROKER, "password": "other"}}, None)


def test_password_is_kept_replaced_or_removed() -> None:
    current = MqttConfig(True, "b", username="me", password="old")
    keep = config_store.from_json(
        {**BASE, "mqtt": {"enabled": True, "host": "b", "username": "me"}}, Config(STRIPS, mqtt=current)
    )
    assert keep.mqtt.password == "old"
    new = config_store.from_json({**BASE, "mqtt": {"username": "me", "password": "new"}}, Config(STRIPS, mqtt=current))
    assert new.mqtt.password == "new"
    gone = config_store.from_json({**BASE, "mqtt": {"username": "me", "password": None}}, Config(STRIPS, mqtt=current))
    assert gone.mqtt.password is None and gone.mqtt.username == "me"
    anonymous = config_store.from_json(
        {**BASE, "mqtt": {"username": None, "password": None}}, Config(STRIPS, mqtt=current)
    )
    assert anonymous.mqtt.username is None and anonymous.mqtt.password is None


@pytest.mark.parametrize(
    "mqtt",
    [
        {"port": 0},
        {"port": "1883"},
        {"prefix": "a/#"},
        {"host": ""},
        {"tls": "yes"},
        {"extra": 1},
        {"password": "p"},
        "text",
    ],
)
def test_invalid_broker_settings_are_rejected(mqtt: Any) -> None:
    with pytest.raises(ConfigError, match="mqtt"):
        config_store.from_json({**BASE, "mqtt": mqtt}, Config(STRIPS))


def test_connection_state_for_the_web_interface() -> None:
    client = FakeClient()
    mqtt = Mqtt(MqttConfig(True), lambda config: client)
    config = Config(STRIPS, mqtt=MqttConfig(True))
    assert describe(None, None, config, mqtt)["mqtt"] == {"enabled": True, "connected": False, "error": ""}
    mqtt.on_connect_fail(client, None)
    assert describe(None, None, config, mqtt)["mqtt"]["error"] == "broker unreachable"
    mqtt.on_connect(client, None, None, REFUSED)
    assert describe(None, None, config, mqtt)["mqtt"]["error"].startswith("refused")
    mqtt.on_connect(client, None, None, OK)
    assert describe(None, None, config, mqtt)["mqtt"] == {"enabled": True, "connected": True, "error": ""}
    mqtt.on_disconnect(client, None, None, OK)
    assert describe(None, None, config, mqtt)["mqtt"] == {
        "enabled": True,
        "connected": False,
        "error": "connection lost",
    }
    assert describe(None, None, Config(STRIPS))["mqtt"] == {"enabled": False, "connected": False, "error": ""}
    assert "log" in describe(None, None, config)["mqtt"]["error"]  # enabled but no client: points to the log


def test_sync_reconnects_only_for_changed_broker_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[FakeClient] = []

    def make(config: MqttConfig) -> Mqtt:
        clients.append(FakeClient())
        return Mqtt(config, lambda _config: clients[-1])

    monkeypatch.setattr(runtime, "Mqtt", make)
    assert runtime.sync_mqtt(None, MqttConfig()) is None and not clients
    first = runtime.sync_mqtt(None, MqttConfig(True))
    assert first and "loop_start" in clients[0].calls
    same = runtime.sync_mqtt(first, MqttConfig(True, ignore=("REC_1",)))
    assert same is first and first.ignore == {"REC_1"} and len(clients) == 1
    moved = runtime.sync_mqtt(first, replace(MqttConfig(True), host="other"))
    assert moved is not first and "disconnect" in clients[0].calls and len(clients) == 2
    assert runtime.sync_mqtt(moved, MqttConfig(False)) is None and "disconnect" in clients[1].calls
