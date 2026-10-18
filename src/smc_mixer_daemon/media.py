"""Explicit, non-broadcast MPRIS player selection."""

from collections.abc import Callable

from smc_mixer_daemon.commands import BackendError, CommandError, Runner, run


class Media:
    def __init__(self, selector: str, runner: Runner = run) -> None:
        self.selector = selector
        self.run = runner

    def players(self) -> list[str]:
        try:
            return self.run(["playerctl", "--list-all"]).splitlines()
        except CommandError as exc:
            if exc.code == 1 and "No players found" in exc.stderr:
                return []
            raise

    def resolve(self) -> str:
        matches = [name for name in self.players() if name == self.selector or name.startswith(self.selector + ".")]
        if len(matches) != 1:
            raise BackendError(f"media player {self.selector!r}: expected one match, found {matches}")
        return matches[0]

    def status(self) -> str:
        """Playback state of the selected player: Playing, Paused or Stopped."""
        state = self.run(["playerctl", "--player", self.resolve(), "status"])
        if state not in ("Playing", "Paused", "Stopped"):
            raise BackendError(f"unexpected playerctl status {state!r}")
        return state

    def control(self, action: str, valid: Callable[[], bool] = lambda: True) -> None:
        if action not in ("play", "pause", "play-pause", "next", "previous"):
            raise ValueError(f"unsupported media action {action!r}")
        if valid():
            player = self.resolve()
            if valid():
                self.run(["playerctl", "--player", player, action])
