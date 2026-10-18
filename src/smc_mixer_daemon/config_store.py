"""Persist a validated configuration as TOML (comments in an existing file are not preserved)."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from smc_mixer_daemon.config import Config, ConfigError, WebConfig, parse


def quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def render(config: Config) -> str:
    lines = ["[daemon]", f"soft_takeover = {str(config.soft_takeover).lower()}", ""]
    if config.player:
        lines += ["[media]", f"player = {quote(config.player)}", ""]
    if config.web != WebConfig():
        web = config.web
        enabled = str(web.enabled).lower()
        lines += ["[web]", f"enabled = {enabled}", f"host = {quote(web.host)}", f"port = {web.port}", ""]
    for strip in sorted(config.strips, key=lambda row: row.channel):
        lines += ["[[strips]]", f"channel = {strip.channel}", f"kind = {quote(strip.kind)}"]
        lines += [f"name = {quote(strip.name)}", ""]
    return "\n".join(lines)


def from_json(data: Any, current: Config) -> Config:
    """Validate an edited configuration; the web section is never editable remotely."""
    if not isinstance(data, dict):
        raise ConfigError("configuration: expected an object")
    soft = data.get("soft_takeover", False)
    raw: dict[str, Any] = {"daemon": {"soft_takeover": soft}, "strips": data.get("strips", [])}
    if data.get("player") is not None:
        raw["media"] = {"player": data["player"]}
    unknown = data.keys() - {"soft_takeover", "player", "strips"}
    if unknown:
        raise ConfigError(f"configuration: unknown keys: {', '.join(sorted(unknown))}")
    parsed = parse(raw)
    return Config(parsed.strips, parsed.soft_takeover, parsed.player, current.web)


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
