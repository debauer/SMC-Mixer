"""Web interface: config persistence, API, origin protection and status publication."""

import json
import threading
import tomllib
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from daemon_fakes import FakeAudio, FakeMedia, mixer_pair
from smc_mixer_daemon import config_store, runtime
from smc_mixer_daemon.audio import Device
from smc_mixer_daemon.config import Config, ConfigError, Strip, WebConfig, load, parse
from smc_mixer_daemon.controller import Controller
from smc_mixer_daemon.status import Board, describe
from smc_mixer_daemon.subscription import Subscription
from smc_mixer_daemon.web import server
from smc_mixer_daemon.web.api import Api

STRIPS = (Strip(1, "sink", "speakers"), Strip(3, "source", "mic"))


class FakePlayers(FakeMedia):
    def players(self) -> list[str]:
        return ["firefox.instance1"]


@pytest.fixture
def web(tmp_path: Path) -> Iterator[tuple[int, Board, Path]]:
    path = tmp_path / "config.toml"
    board = Board(Config(STRIPS, player="firefox"))
    api = Api(board, path, FakeAudio, FakePlayers)
    instance = server.Server(WebConfig(port=0), api)
    threading.Thread(target=instance.serve_forever, daemon=True).start()
    yield instance.server_address[1], board, path
    instance.shutdown()
    instance.server_close()


def call(
    port: int, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    connection = HTTPConnection("127.0.0.1", port)
    data = json.dumps(body).encode() if body is not None else None
    connection.request(method, path, data, {"Content-Type": "application/json", **(headers or {})})
    response = connection.getresponse()
    text = response.read()
    return response.status, json.loads(text) if response.getheader("Content-Type") == "application/json" else {}


def test_render_round_trips_through_strict_parser() -> None:
    config = Config(
        (Strip(2, "source", 'mic "quoted" ä\\'), Strip(1, "sink", "speakers")),
        soft_takeover=True,
        player="firefox",
        web=WebConfig(host="::1", port=9000),
    )
    again = parse(tomllib.loads(config_store.render(config)))
    assert again == Config(tuple(sorted(config.strips, key=lambda s: s.channel)), True, "firefox", config.web)


def test_web_section_validation() -> None:
    assert parse({"strips": [], "media": {"player": "x"}}).web == WebConfig()
    for web in ({"port": 0}, {"port": "80"}, {"enabled": 1}, {"host": " "}, {"extra": 1}):
        with pytest.raises(ConfigError):
            parse({"media": {"player": "x"}, "web": web})


def test_from_json_validates_and_keeps_web_settings() -> None:
    current = Config(STRIPS, web=WebConfig(port=9001))
    data = {"soft_takeover": True, "player": None, "strips": [{"channel": 2, "kind": "sink", "name": "headset"}]}
    assert config_store.from_json(data, current) == Config((Strip(2, "sink", "headset"),), True, None, current.web)
    for bad in (
        {"strips": [{"channel": 9, "kind": "sink", "name": "x"}]},
        {"strips": [], "player": None},
        {"strips": [], "player": "a", "web": {}},
        [],
    ):
        with pytest.raises(ConfigError):
            config_store.from_json(bad, current)


def test_save_is_atomic_and_leaves_no_temporary_files(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    config_store.save(path, Config(STRIPS, player="firefox"))
    assert load(path) == Config(STRIPS, False, "firefox")
    assert [entry.name for entry in path.parent.iterdir()] == ["config.toml"]


def test_status_config_devices_and_static_files(web: tuple[int, Board, Path]) -> None:
    port, board, _ = web
    assert call(port, "GET", "/api/status")[1]["mixer"]["control"] == "waiting"
    assert call(port, "GET", "/api/config")[1]["strips"][0] == {
        "channel": 1,
        "kind": "sink",
        "name": "speakers",
        "label": None,
    }
    devices = call(port, "GET", "/api/devices")[1]
    assert {"kind": "sink", "name": "speakers", "description": "Speakers", "default": True} in devices["devices"]
    assert devices["players"] == ["firefox.instance1"]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as page:
        assert b"<h1" in page.read()
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/app.js") as script:
        assert script.headers["Content-Type"].startswith("text/javascript")
    assert call(port, "GET", "/api/nope")[0] == 404
    assert call(port, "GET", "/../pyproject.toml")[0] == 404
    assert not board.reload.is_set()


def test_save_writes_file_and_requests_reload(web: tuple[int, Board, Path]) -> None:
    port, board, path = web
    body = {"soft_takeover": True, "player": None, "strips": [{"channel": 2, "kind": "sink", "name": "headset"}]}
    status, saved = call(port, "PUT", "/api/config", body)
    assert status == 200 and saved["soft_takeover"] is True
    assert load(path) == Config((Strip(2, "sink", "headset"),), True)
    assert board.reload.is_set() and board.config == load(path)
    status, error = call(port, "PUT", "/api/config", {"strips": [], "player": None})
    assert status == 400 and "configure at least one" in error["error"]
    assert load(path) == Config((Strip(2, "sink", "headset"),), True)


@pytest.mark.parametrize(
    "headers",
    [{"Origin": "http://evil.example"}, {"Host": "evil.example"}, {"Content-Type": "text/plain"}],
)
def test_writes_reject_foreign_origin_host_and_non_json(web: tuple[int, Board, Path], headers: dict[str, str]) -> None:
    port, board, path = web
    status, _ = call(port, "PUT", "/api/config", {"strips": [], "player": "x"}, headers)
    assert status == 403
    assert not path.exists() and not board.reload.is_set()


def test_reads_reject_rebinding_host(web: tuple[int, Board, Path]) -> None:
    assert call(web[0], "GET", "/api/config", headers={"Host": "evil.example"})[0] == 403


def test_oversized_and_invalid_bodies_are_rejected(web: tuple[int, Board, Path]) -> None:
    port = web[0]
    assert call(port, "PUT", "/api/config", "x" * 70000)[0] == 413
    connection = HTTPConnection("127.0.0.1", port)
    connection.request("PUT", "/api/config", b"{not json", {"Content-Type": "application/json"})
    assert connection.getresponse().status == 400


def test_describe_publishes_strip_state_without_hardware_access() -> None:
    config = Config((Strip(1, "sink", "speakers"), Strip(2, "sink", "missing"), Strip(3, "source", "mic")))
    assert describe(None, None, config)["mixer"] == {"connected": False, "mode": None, "control": "waiting"}
    assert describe(None, None, config)["strips"][0]["available"] is False
    mixer, _, _ = mixer_pair()
    controller = Controller(config, mixer, FakeAudio(), None)
    assert controller.start()
    controller.refresh()
    status = describe(mixer, controller, config)
    assert status["mixer"] == {"connected": True, "mode": "DAW", "control": "active"}
    assert status["faders"] == mixer.faders
    speakers, missing, mic = status["strips"]
    assert speakers | {"description": "Speakers"} == speakers
    assert (speakers["volume"], speakers["muted"], speakers["default"]) == (100, False, True)
    assert missing == {"channel": 2, "kind": "sink", "name": "missing", "label": None, "available": False}
    assert (mic["volume"], mic["default"], mic["owned"]) == (0, True, True)
    mixer.close()


def test_loop_publishes_status_and_exits_for_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    mixer, _, _ = mixer_pair()
    monkeypatch.setattr(runtime.SmcMixer, "open", lambda: mixer)
    monkeypatch.setattr(runtime, "Audio", FakeAudio)
    board = Board()

    class Stop(threading.Event):
        ticks = 0

        def wait(self, timeout: float | None = None) -> bool:
            self.ticks += 1
            if self.ticks == 3:
                board.reload.set()
            return self.is_set()

    stop = Stop()
    runtime.control_loop(Config((Strip(1, "sink", "speakers"),)), stop, Subscription(), board)
    assert stop.ticks == 3 and not stop.is_set()
    assert board.status()["mixer"]["connected"] is False or "strips" in board.status()
    assert mixer.connected is False


def test_http_error_is_reported_when_audio_backend_fails(tmp_path: Path) -> None:
    class Broken(FakeAudio):
        def snapshot(self) -> Any:
            raise runtime.BackendError("pactl unavailable")

    api = Api(Board(Config(STRIPS)), tmp_path / "c.toml", Broken, FakePlayers)
    instance = server.Server(WebConfig(port=0), api)
    threading.Thread(target=instance.serve_forever, daemon=True).start()
    try:
        status, body = call(instance.server_address[1], "GET", "/api/devices")
        assert status == 503 and "pactl unavailable" in body["error"]
    finally:
        instance.shutdown()
        instance.server_close()
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"http://127.0.0.1:{instance.server_address[1]}/", timeout=1)


def test_buttons_are_saved_through_the_api(web: tuple[int, Board, Path]) -> None:
    port, board, path = web
    body = {
        "soft_takeover": False,
        "player": "firefox",
        "strips": [{"channel": 1, "kind": "sink", "name": "speakers"}],
        "buttons": [{"button": "REC_1", "action": "mic_mute", "channel": None, "led": "active"}],
    }
    status, saved = call(port, "PUT", "/api/config", body)
    assert status == 200 and saved["buttons"] == [{**body["buttons"][0], "command": None, "release_command": None}]
    assert load(path).buttons[0].action == "mic_mute" and board.reload.is_set()
    body["buttons"] = [{"button": "MUTE_1", "action": "mic_mute", "channel": None, "led": "off"}]
    status, error = call(port, "PUT", "/api/config", body)
    assert status == 400 and "belong to configured strips" in error["error"]


def test_board_tracks_held_and_last_pressed_keys() -> None:
    board = Board()
    board.key("REC_1", True)
    board.key("PLAY", True)
    board.key("REC_1", False)
    keys = board.status()["keys"]
    assert keys == {"held": ["PLAY"], "last": "PLAY", "presses": 2}
    assert board.status()["log"][0]["msg"] == "PLAY pressed"
    board.clear_keys()
    assert board.status()["keys"]["held"] == []


def test_loop_records_every_physical_press_even_when_unbound(monkeypatch: pytest.MonkeyPatch) -> None:
    mixer, master, _ = mixer_pair()
    monkeypatch.setattr(runtime.SmcMixer, "open", lambda: mixer)
    monkeypatch.setattr(runtime, "Audio", FakeAudio)
    board = Board()

    class Stop(threading.Event):
        ticks = 0

        def wait(self, timeout: float | None = None) -> bool:
            self.ticks += 1
            if self.ticks == 2:
                master.emit(bytes([0x90, 46, 127]))  # BANK_LEFT: unbound
            if self.ticks == 3:
                self.set()
            return self.is_set()

    runtime.control_loop(Config((Strip(1, "sink", "speakers"),)), Stop(), Subscription(), board)
    assert board.status()["keys"] == {"held": ["BANK_LEFT"], "last": "BANK_LEFT", "presses": 1}


def test_strip_labels_round_trip_validate_and_publish() -> None:
    config = Config((Strip(1, "sink", "speakers", 'Boxen "links"'), Strip(2, "sink", "headset")))
    again = parse(tomllib.loads(config_store.render(config)))
    assert again.strips == (Strip(1, "sink", "speakers", 'Boxen "links"'), Strip(2, "sink", "headset", None))
    assert Api(Board(), Path("x"), FakeAudio).config_json(again)["strips"][0]["label"] == 'Boxen "links"'
    assert describe(None, None, again)["strips"][0]["label"] == 'Boxen "links"'
    base = {"strips": [{"channel": 1, "kind": "sink", "name": "x", "label": ""}]}
    base["strips"][0]["label"] = None  # type: ignore[assignment]
    assert config_store.from_json(base, Config(())).strips[0].label is None
    for label in ("", " padded ", "x" * 41, 5):
        base["strips"][0]["label"] = label  # type: ignore[assignment]
        with pytest.raises(ConfigError):
            config_store.from_json(base, Config(()))


def test_devices_hide_monitors_and_easyeffects_sources_but_not_sinks() -> None:
    audio = FakeAudio()
    for name, monitor in (("easyeffects_source", False), ("EasyEffects_mic", False), ("out.monitor", True)):
        audio.devices["source", name] = Device("source", name, 9, name, False, 50, monitor)
    audio.devices["sink", "easyeffects_sink"] = Device("sink", "easyeffects_sink", 10, "EE", False, 50)
    api = Api(Board(Config(STRIPS)), Path("unused.toml"), lambda: audio, FakePlayers)
    names = {(d["kind"], d["name"]) for d in api.devices()["devices"]}
    assert names == {("sink", "speakers"), ("sink", "headset"), ("source", "mic"), ("sink", "easyeffects_sink")}
