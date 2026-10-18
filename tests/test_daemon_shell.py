"""Shell actions: execution, limits, token-guarded configuration and validation."""

import logging
import os
import stat
import threading
import time
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from daemon_fakes import FakeAudio, mixer_pair
from smc_mixer import Button, ButtonEvent, Layer
from smc_mixer_daemon import config_store
from smc_mixer_daemon.buttons import Binding
from smc_mixer_daemon.config import Config, ConfigError, Strip, parse
from smc_mixer_daemon.controller import Controller
from smc_mixer_daemon.shell import ShellRunner
from smc_mixer_daemon.status import Board
from smc_mixer_daemon.web.api import Api
from smc_mixer_daemon.web.auth import AuthError, ensure_token


def wait_for(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.02)


def test_command_runs_through_sh_c_with_shell_features(tmp_path: Path) -> None:
    runner = ShellRunner()
    target = tmp_path / "out.txt"
    assert runner.start("REC_1", f"echo one && echo two > '{target}'; exit 0")
    wait_for(target.exists)
    wait_for(lambda: target.read_text() == "two\n")


def test_failure_is_logged_with_exit_code_and_output(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    ShellRunner().start("REC_2", "echo boom >&2; exit 3")
    wait_for(lambda: "exit 3: boom" in caplog.text)


def test_timeout_kills_the_whole_process_group(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    marker = tmp_path / "late"
    ShellRunner(timeout=0.3).start("REC_3", f"(sleep 1.5; touch '{marker}') & sleep 5")
    wait_for(lambda: "killed after" in caplog.text)
    time.sleep(1.8)
    assert not marker.exists()


def test_close_kills_running_commands_and_refuses_new_ones(tmp_path: Path) -> None:
    runner = ShellRunner()
    marker = tmp_path / "late"
    runner.start("REC_1", f"sleep 1; touch '{marker}'")
    time.sleep(0.3)
    runner.close()
    assert runner.start("REC_1", "true") is False
    time.sleep(1.4)
    assert not marker.exists()


class Recorder(ShellRunner):
    def __init__(self) -> None:
        super().__init__()
        self.started: list[tuple[str, str]] = []

    def start(self, label: str, command: str) -> bool:
        self.started.append((label, command))
        return True


def started(*buttons: Binding) -> tuple[Controller, Recorder]:
    mixer, _, _ = mixer_pair()
    controller = Controller(Config((Strip(1, "sink", "speakers"),), buttons=buttons), mixer, FakeAudio(), None)
    recorder = Recorder()
    controller.shell = recorder
    assert controller.start()
    return controller, recorder


def test_press_and_release_run_their_commands_once_per_edge() -> None:
    controller, recorder = started(Binding(Button.REC_1, "shell", command="a", release_command="b"))
    press, release = ButtonEvent(Button.REC_1, True, Layer.DAW), ButtonEvent(Button.REC_1, False, Layer.DAW)
    controller.event(press)
    controller.event(press)
    controller.event(release)
    assert recorder.started == [("REC_1", "a"), ("REC_1", "b")]


def test_press_without_release_command_and_while_suspended() -> None:
    controller, recorder = started(Binding(Button.REC_1, "shell", command="a"))
    controller.event(ButtonEvent(Button.REC_1, True, Layer.DAW))
    controller.event(ButtonEvent(Button.REC_1, False, Layer.DAW))
    controller.suspend()
    controller.event(ButtonEvent(Button.REC_1, True, Layer.DAW))
    assert recorder.started == [("REC_1", "a")]


BASE = {"strips": [{"channel": 1, "kind": "sink", "name": "a"}]}


@pytest.mark.parametrize(
    "row",
    [
        {"action": "shell"},
        {"action": "shell", "command": " "},
        {"action": "shell", "command": 5},
        {"action": "shell", "command": "x" * 4001},
        {"action": "shell", "command": "a\x00b"},
        {"action": "shell", "command": "a", "release_command": ""},
        {"action": "shell", "command": "a", "led": "active"},
        {"action": "next", "command": "a"},
        {"action": "next", "release_command": "a"},
    ],
)
def test_invalid_shell_bindings_are_rejected(row: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        parse({**BASE, "buttons": [{"button": "REC_1", **row}]})


def test_multiline_commands_survive_toml_round_trip() -> None:
    command = "echo 'a \"quoted\"'\nls | wc -l  # \u00e4\\"
    config = config_store.from_json(
        {**BASE, "buttons": [{"button": "REC_1", "action": "shell", "command": command, "release_command": "true"}]},
        Config(()),
    )
    again = parse(tomllib.loads(config_store.render(config)))
    assert again.buttons[0].command == command and again.buttons[0].release_command == "true"


def api(tmp_path: Path, token: str | None = "secret") -> Api:
    board = Board(Config((Strip(1, "sink", "speakers"),)))
    return Api(board, tmp_path / "c.toml", FakeAudio, token=token, token_file=tmp_path / "web-token")


SHELL = {
    "soft_takeover": False,
    "player": None,
    "strips": [{"channel": 1, "kind": "sink", "name": "speakers"}],
    "buttons": [{"button": "REC_1", "action": "shell", "command": "touch x", "release_command": None}],
}


def test_changing_shell_commands_needs_the_token(tmp_path: Path) -> None:
    service = api(tmp_path)
    for token in (None, "", "wrong"):
        with pytest.raises(AuthError):
            service.put("/api/config", SHELL, token)
    assert not (tmp_path / "c.toml").exists()
    saved = service.put("/api/config", SHELL, "secret")
    assert saved["buttons"][0]["command"] == "touch x" and saved["token_file"].endswith("web-token")
    assert service.board.reload.is_set()


def test_unchanged_commands_and_other_edits_need_no_token(tmp_path: Path) -> None:
    service = api(tmp_path)
    service.put("/api/config", SHELL, "secret")
    service.put("/api/config", {**SHELL, "soft_takeover": True}, None)  # same commands: allowed without token
    with pytest.raises(AuthError):
        service.put("/api/config", {**SHELL, "buttons": [{**SHELL["buttons"][0], "command": "rm x"}]}, None)  # type: ignore[index]
    with pytest.raises(AuthError):
        service.put("/api/config", {**SHELL, "buttons": []}, None)  # removing a command is a change too


def test_without_a_configured_token_shell_cannot_be_changed_over_http(tmp_path: Path) -> None:
    with pytest.raises(AuthError, match="no access token"):
        api(tmp_path, token=None).put("/api/config", SHELL, "anything")


def test_token_file_is_private_stable_and_random(tmp_path: Path) -> None:
    path = tmp_path / "dir" / "web-token"
    token = ensure_token(path)
    assert len(token) >= 24 and ensure_token(path) == token
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert ensure_token(tmp_path / "other") != token


def test_http_passes_the_token_header(tmp_path: Path) -> None:
    import json
    from http.client import HTTPConnection

    from smc_mixer_daemon.config import WebConfig
    from smc_mixer_daemon.web import server

    instance = server.Server(WebConfig(port=0), api(tmp_path))
    threading.Thread(target=instance.serve_forever, daemon=True).start()
    try:
        results = []
        for token in (None, "secret"):
            connection = HTTPConnection("127.0.0.1", instance.server_address[1])
            headers = {"Content-Type": "application/json", **({"X-SMC-Token": token} if token else {})}
            connection.request("PUT", "/api/config", json.dumps(SHELL), headers)
            results.append(connection.getresponse().status)
        assert results == [403, 200]
    finally:
        instance.shutdown()
        instance.server_close()
