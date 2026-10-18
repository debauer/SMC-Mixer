"""Configuration failures are explicit, before opening any device."""

from pathlib import Path

import pytest

from smc_mixer_daemon.config import ConfigError, load

BASE = '[[strips]]\nchannel = 1\nkind = "sink"\nname = "speakers"\n'


def test_defaults_and_media_only(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(BASE)
    assert not load(path).soft_takeover
    assert load(path).strips[0].name == "speakers"
    path.write_text('[daemon]\nsoft_takeover = true\n[media]\nplayer = "firefox"\n')
    config = load(path)
    assert config.soft_takeover and config.player == "firefox" and not config.strips


@pytest.mark.parametrize(
    "text",
    [
        "",
        "nonsense = true",
        "strips = 1",
        "[media]",
        '[media]\nplayer = "firefox,*"',
        "[daemon]\nsoft_takeover = 1\n" + BASE,
        BASE.replace("channel = 1", "channel = true"),
        BASE.replace("channel = 1", "channel = 9"),
        BASE.replace('"sink"', '"invalid"'),
        BASE.replace('"speakers"', '" "'),
        BASE + BASE,
        BASE + BASE.replace("channel = 1", "channel = 2"),
        BASE + "typo = false\n",
        "[[strips]]\nchannel = 1",
    ],
)
def test_invalid_config(tmp_path: Path, text: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(ConfigError):
        load(path)


def test_missing_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"config\.toml"):
        load(tmp_path / "config.toml")
