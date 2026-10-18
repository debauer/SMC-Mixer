"""Persist a validated configuration as TOML (comments in an existing file are not preserved)."""

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from smc_mixer_daemon.config import Config, ConfigError, WebConfig, parse
from smc_mixer_daemon.mqtt_config import MqttConfig, MqttConfigError, parse_mqtt


def quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def render_mqtt(mqtt: MqttConfig) -> list[str]:
    lines = ["[mqtt]", f"enabled = {str(mqtt.enabled).lower()}", f"host = {quote(mqtt.host)}", f"port = {mqtt.port}"]
    if mqtt.username is not None:
        lines.append(f"username = {quote(mqtt.username)}")
    if mqtt.password is not None:
        lines.append(f"password = {quote(mqtt.password)}")
    lines += [f"tls = {str(mqtt.tls).lower()}", f"prefix = {quote(mqtt.prefix)}"]
    lines += [f"ignore = [{', '.join(quote(item) for item in mqtt.ignore)}]", ""]
    return lines


def render(config: Config) -> str:
    lines = ["[daemon]", f"soft_takeover = {str(config.soft_takeover).lower()}", ""]
    if config.player:
        lines += ["[media]", f"player = {quote(config.player)}", ""]
    if config.web != WebConfig():
        web = config.web
        enabled = str(web.enabled).lower()
        lines += ["[web]", f"enabled = {enabled}", f"host = {quote(web.host)}", f"port = {web.port}", ""]
    if config.mqtt != MqttConfig():
        lines += render_mqtt(config.mqtt)
    for strip in sorted(config.strips, key=lambda row: row.channel):
        lines += ["[[strips]]", f"channel = {strip.channel}", f"kind = {quote(strip.kind)}"]
        lines.append(f"name = {quote(strip.name)}")
        if strip.label:
            lines.append(f"label = {quote(strip.label)}")
        lines.append("")
    for binding in sorted(config.buttons, key=lambda row: row.button):
        lines += ["[[buttons]]", f"button = {quote(binding.button.name)}", f"action = {quote(binding.action)}"]
        if binding.channel is not None:
            lines.append(f"channel = {binding.channel}")
        lines.append(f"led = {quote(binding.led)}")
        if binding.command is not None:
            lines.append(f"command = {quote(binding.command)}")
        if binding.release_command is not None:
            lines.append(f"release_command = {quote(binding.release_command)}")
        lines.append("")
    for knob in sorted(config.knobs, key=lambda row: row.knob):
        lines += ["[[knobs]]", f"knob = {knob.knob}", f"action = {quote(knob.action)}"]
        if knob.channel is not None:
            lines.append(f"channel = {knob.channel}")
        lines += [f"step = {knob.step}", ""]
    return "\n".join(lines)


def broker_from_json(data: Any, current: MqttConfig) -> MqttConfig:
    """Broker settings from the settings page; an absent password keeps the stored one, null removes it."""
    keys = {"enabled", "host", "port", "username", "tls", "prefix", "password"}
    if not isinstance(data, dict) or data.keys() - keys:
        raise ConfigError("mqtt: expected an object with " + ", ".join(sorted(keys)))
    row = {key: value for key, value in data.items() if key != "password"}
    if "password" not in data:
        row["password"] = current.password
    elif data["password"] is not None:
        row["password"] = data["password"]
    try:
        return parse_mqtt({**row, "ignore": list(current.ignore)})
    except MqttConfigError as exc:
        raise ConfigError(str(exc)) from exc


def from_json(data: Any, current: Config) -> Config:
    """Validate an edited configuration; only the MQTT ignore list is editable, never web or broker settings."""
    if not isinstance(data, dict):
        raise ConfigError("configuration: expected an object")
    soft = data.get("soft_takeover", False)
    raw: dict[str, Any] = {"daemon": {"soft_takeover": soft}, "strips": data.get("strips", [])}
    for key in ("buttons", "knobs"):
        if key in data:
            raw[key] = data[key]
    if data.get("player") is not None:
        raw["media"] = {"player": data["player"]}
    ignore = data.get("mqtt_ignore", list(current.mqtt.ignore))
    broker = broker_from_json(data["mqtt"], current.mqtt) if "mqtt" in data else current.mqtt
    unknown = data.keys() - {"soft_takeover", "player", "strips", "buttons", "knobs", "mqtt_ignore", "mqtt"}
    if unknown:
        raise ConfigError(f"configuration: unknown keys: {', '.join(sorted(unknown))}")
    parsed = parse(raw)
    try:
        mqtt = replace(broker, ignore=parse_mqtt({"ignore": ignore}).ignore)
    except MqttConfigError as exc:
        raise ConfigError(str(exc)) from exc
    return Config(parsed.strips, parsed.soft_takeover, parsed.player, current.web, parsed.buttons, parsed.knobs, mqtt)


def save(path: Path, config: Config) -> None:
    """Write atomically so a crash never leaves a truncated configuration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(render(config))
        os.replace(temporary, path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise
