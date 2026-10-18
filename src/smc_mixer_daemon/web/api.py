"""JSON endpoints of the web interface; transport-independent so they can be tested directly."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from smc_mixer_daemon import config_store
from smc_mixer_daemon.audio import Audio
from smc_mixer_daemon.commands import BackendError
from smc_mixer_daemon.config import Config
from smc_mixer_daemon.media import Media
from smc_mixer_daemon.status import Board
from smc_mixer_daemon.web.auth import AuthError, check


def commands(config: Config) -> set[tuple[str, str | None, str | None]]:
    return {(b.button.name, b.command, b.release_command) for b in config.buttons if b.action == "shell"}


class NotFoundError(LookupError):
    """Unknown endpoint."""


class Api:
    def __init__(
        self,
        board: Board,
        config_path: Path,
        audio: Callable[[], Audio] = Audio,
        media: Callable[[], Media] = lambda: Media(""),
        token: str | None = None,
        token_file: Path | None = None,
    ) -> None:
        self.board, self.config_path, self.audio, self.media = board, config_path, audio, media
        self.token, self.token_file = token, token_file

    def running(self) -> Config:
        config = self.board.config
        if config is None:
            raise BackendError("daemon has not loaded a configuration yet")
        return config

    def get(self, path: str) -> dict[str, Any]:
        if path == "/api/status":
            return self.board.status()
        if path == "/api/config":
            return self.config_json(self.running())
        if path == "/api/devices":
            return self.devices()
        raise NotFoundError(path)

    def put(self, path: str, data: Any, token: str | None = None) -> dict[str, Any]:
        if path != "/api/config":
            raise NotFoundError(path)
        running = self.running()
        config = config_store.from_json(data, running)
        if commands(config) != commands(running):
            if self.token is None:
                raise AuthError("shell commands cannot be changed over HTTP: no access token configured")
            check(self.token, token)
        config_store.save(self.config_path, config)
        self.board.config = config
        self.board.reload.set()
        self.board.note("info", f"configuration saved to {self.config_path}; reloading")
        return self.config_json(config)

    def config_json(self, config: Config) -> dict[str, Any]:
        return {
            "token_file": str(self.token_file) if self.token_file else None,
            "soft_takeover": config.soft_takeover,
            "mqtt": {"enabled": config.mqtt.enabled, "prefix": config.mqtt.prefix},
            "mqtt_ignore": list(config.mqtt.ignore),
            "player": config.player,
            "strips": [{"channel": s.channel, "kind": s.kind, "name": s.name, "label": s.label} for s in config.strips],
            "buttons": [
                {
                    "button": b.button.name,
                    "action": b.action,
                    "channel": b.channel,
                    "led": b.led,
                    "command": b.command,
                    "release_command": b.release_command,
                }
                for b in config.buttons
            ],
            "knobs": [{"knob": k.knob, "action": k.action, "channel": k.channel, "step": k.step} for k in config.knobs],
        }

    def devices(self) -> dict[str, Any]:
        snapshot = self.audio().snapshot()
        devices = [
            {"kind": d.kind, "name": d.name, "description": d.description, "default": snapshot.selected(d)}
            for d in snapshot.devices.values()
            if d.offered
        ]
        players: list[str] = []
        player_error = None
        try:
            players = self.media().players()
        except BackendError as exc:
            player_error = str(exc)
        return {"devices": devices, "players": players, "player_error": player_error}
