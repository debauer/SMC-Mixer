"""Strict startup configuration; absent devices are resolved at runtime."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smc_mixer_daemon.buttons import Binding, BindingError, parse_binding, strip_buttons


class ConfigError(ValueError):
    """Invalid daemon configuration."""


@dataclass(frozen=True)
class Strip:
    channel: int
    kind: str
    name: str


@dataclass(frozen=True)
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(frozen=True)
class Config:
    strips: tuple[Strip, ...]
    soft_takeover: bool = False
    player: str | None = None
    web: WebConfig = field(default_factory=WebConfig)
    buttons: tuple[Binding, ...] = ()


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "smc-mixer-daemon/config.toml"


def table(value: Any, keys: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{location}: expected a table")
    if unknown := value.keys() - keys:
        raise ConfigError(f"{location}: unknown keys: {', '.join(sorted(unknown))}")
    return value


def name(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or "\x00" in value:
        raise ConfigError(f"{location}: expected a nonempty name without surrounding whitespace")
    return value


def load(path: Path) -> Config:
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return parse(raw)


def parse_web(value: Any) -> WebConfig:
    row = table(value, {"enabled", "host", "port"}, "web")
    default = WebConfig()
    enabled = row.get("enabled", default.enabled)
    if type(enabled) is not bool:
        raise ConfigError("web.enabled: expected true or false")
    host = name(row.get("host", default.host), "web.host")
    port = row.get("port", default.port)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigError("web.port: expected an integer from 1 to 65535")
    return WebConfig(enabled, host, port)


def parse(raw: dict[str, Any]) -> Config:
    table(raw, {"daemon", "media", "strips", "web", "buttons"}, "configuration")
    daemon = table(raw.get("daemon", {}), {"soft_takeover"}, "daemon")
    soft = daemon.get("soft_takeover", False)
    if type(soft) is not bool:
        raise ConfigError("daemon.soft_takeover: expected true or false")
    player = None
    if "media" in raw:
        media = table(raw["media"], {"player"}, "media")
        player = name(media.get("player"), "media.player")
        if any(char in player for char in ",*?[]"):
            raise ConfigError("media.player: specify one player name, without lists or wildcards")
    entries = raw.get("strips", [])
    if not isinstance(entries, list):
        raise ConfigError("strips: expected [[strips]] tables")
    strips: list[Strip] = []
    for index, entry in enumerate(entries):
        row = table(entry, {"channel", "kind", "name"}, f"strips[{index}]")
        channel = row.get("channel")
        if type(channel) is not int or not 1 <= channel <= 8:
            raise ConfigError(f"strips[{index}].channel: expected an integer from 1 to 8")
        kind = row.get("kind")
        if kind not in ("sink", "source"):
            raise ConfigError(f"strips[{index}].kind: expected sink or source")
        strips.append(Strip(channel, kind, name(row.get("name"), f"strips[{index}].name")))
    if len({strip.channel for strip in strips}) != len(strips):
        raise ConfigError("strips: duplicate channel")
    if len({(strip.kind, strip.name) for strip in strips}) != len(strips):
        raise ConfigError("strips: duplicate device assignment")
    buttons = parse_buttons(raw.get("buttons", []), strips)
    if not strips and player is None and not buttons:
        raise ConfigError("configure at least one strip, button or media player")
    web = parse_web(raw["web"]) if "web" in raw else WebConfig()
    return Config(tuple(strips), soft, player, web, buttons)


def parse_buttons(entries: Any, strips: list[Strip]) -> tuple[Binding, ...]:
    if not isinstance(entries, list):
        raise ConfigError("buttons: expected [[buttons]] tables")
    channels = {strip.channel for strip in strips}
    bindings: list[Binding] = []
    for index, entry in enumerate(entries):
        row = table(entry, {"button", "action", "channel", "led"}, f"buttons[{index}]")
        try:
            bindings.append(parse_binding(row, f"buttons[{index}]", channels))
        except BindingError as exc:
            raise ConfigError(str(exc)) from exc
    if len({binding.button for binding in bindings}) != len(bindings):
        raise ConfigError("buttons: duplicate button")
    if taken := {binding.button for binding in bindings} & strip_buttons(channels):
        names = ", ".join(sorted(button.name for button in taken))
        raise ConfigError(f"buttons: {names} belong to configured strips; free the channel first")
    return tuple(bindings)
