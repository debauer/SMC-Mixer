"""The optional `[mqtt]` table; broker settings are read at startup, `ignore` follows reloads."""

from dataclasses import dataclass
from typing import Any

from smc_mixer import Button


class MqttConfigError(ValueError):
    """Invalid MQTT configuration."""


@dataclass(frozen=True)
class MqttConfig:
    enabled: bool = False
    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    tls: bool = False
    prefix: str = "smc"
    ignore: tuple[str, ...] = ()  # inputs that are neither published nor controlled: button names, KNOB_n, FADER_n


KEYS = {"enabled", "host", "port", "username", "password", "tls", "prefix", "ignore"}
INPUTS = {button.name for button in Button} | {f"KNOB_{n}" for n in range(1, 9)} | {f"FADER_{n}" for n in range(1, 9)}


def text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise MqttConfigError(f"{location}: expected a nonempty text without surrounding whitespace")
    return value


def parse_mqtt(value: Any) -> MqttConfig:
    if not isinstance(value, dict):
        raise MqttConfigError("mqtt: expected a table")
    if unknown := value.keys() - KEYS:
        raise MqttConfigError(f"mqtt: unknown keys: {', '.join(sorted(unknown))}")
    default = MqttConfig()
    for key in ("enabled", "tls"):
        if type(value.get(key, False)) is not bool:
            raise MqttConfigError(f"mqtt.{key}: expected true or false")
    port = value.get("port", default.port)
    if type(port) is not int or not 1 <= port <= 65535:
        raise MqttConfigError("mqtt.port: expected an integer from 1 to 65535")
    prefix = text(value.get("prefix", default.prefix), "mqtt.prefix")
    if any(char in prefix for char in "+#") or prefix.startswith("/") or prefix.endswith("/") or "//" in prefix:
        raise MqttConfigError("mqtt.prefix: no wildcards, no leading, trailing or doubled slashes")
    username, password = value.get("username"), value.get("password")
    if username is not None:
        text(username, "mqtt.username")
    if password is not None and (not isinstance(password, str) or "\x00" in password):
        raise MqttConfigError("mqtt.password: expected text")
    if password is not None and username is None:
        raise MqttConfigError("mqtt.password: needs mqtt.username")
    ignore = value.get("ignore", [])
    if not isinstance(ignore, list) or not all(isinstance(item, str) for item in ignore):
        raise MqttConfigError("mqtt.ignore: expected a list of input names")
    if unknown_inputs := set(ignore) - INPUTS:
        raise MqttConfigError(f"mqtt.ignore: unknown inputs: {', '.join(sorted(unknown_inputs))}")
    return MqttConfig(
        value.get("enabled", False),
        text(value.get("host", default.host), "mqtt.host"),
        port,
        username,
        password,
        value.get("tls", False),
        prefix,
        tuple(sorted(set(ignore))),
    )
