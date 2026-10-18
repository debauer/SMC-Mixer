"""Color roles and terminal capability handling."""

import curses
from unittest.mock import Mock

import pytest

from smc_mixer_tools.monitor.app import draw
from smc_mixer_tools.monitor.colors import color_spans, init_colors
from smc_mixer_tools.monitor.state import MonitorState


def test_semantic_colors_preserve_text_positions() -> None:
    text = "┃ █ * M * * S * * R * *SEL* *REC* *BANK <* ONLINE OFFLINE DEMO ┃"
    spans = color_spans(text)
    assert [role for _, _, role in spans] == [
        "frame",
        "fader",
        "mute",
        "solo",
        "record",
        "select",
        "record",
        "pressed",
        "online",
        "offline",
        "demo",
        "frame",
    ]
    for start, token, _ in spans:
        assert text[start : start + len(token)] == token


def test_monochrome_terminal_does_not_initialize_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(curses, "has_colors", lambda: False)
    start = Mock()
    monkeypatch.setattr(curses, "start_color", start)
    assert init_colors() == {}
    start.assert_not_called()


@pytest.mark.parametrize("default_background", [True, False])
def test_palette_reuses_pairs(monkeypatch: pytest.MonkeyPatch, default_background: bool) -> None:
    monkeypatch.setattr(curses, "has_colors", lambda: True)
    monkeypatch.setattr(curses, "start_color", Mock())
    defaults = Mock(side_effect=None if default_background else curses.error("unsupported"))
    monkeypatch.setattr(curses, "use_default_colors", defaults)
    pair = Mock()
    monkeypatch.setattr(curses, "init_pair", pair)
    monkeypatch.setattr(curses, "color_pair", lambda number: number << 8)
    palette = init_colors()
    assert len(palette) == 12
    assert pair.call_count == 6
    assert all(call.args[2] == (-1 if default_background else curses.COLOR_BLACK) for call in pair.call_args_list)
    assert palette["record"] == palette["offline"]
    assert palette["fader"] == palette["online"]
    assert not palette["frame"] & curses.A_BOLD
    assert palette["record"] & curses.A_BOLD


def test_draw_applies_attributes_without_changing_layout() -> None:
    screen = Mock()
    screen.getmaxyx.return_value = (40, 120)
    draw(screen, MonitorState(), {"frame": 100, "online": 200, "pressed": 300})
    colored = [call.args for call in screen.addnstr.call_args_list if len(call.args) == 5]
    assert any(args[2] == "ONLINE" and args[4] == 200 for args in colored)
    assert any(args[1] == 0 and args[4] == 100 for args in colored)
