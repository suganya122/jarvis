from pathlib import Path

import pytest

from workers.os_tools.apps import App, best_match, parse_desktop_file, score
from workers.os_tools.media import MediaUnavailable, linux_command, macos_script

APPS = [
    App("Firefox", "/a/firefox.desktop"),
    App("Google Chrome", "/a/chrome.desktop"),
    App("Chrome Remote Desktop", "/a/crd.desktop"),
    App("Visual Studio Code", "/a/code.desktop"),
    App("Calculator", "/a/calc.desktop"),
    App("Text Editor", "/a/te.desktop"),
    App("Text Expander", "/a/tx.desktop"),
]


@pytest.mark.parametrize(
    ("query", "expected"),
    [("firefox", "Firefox"), ("FireFox", "Firefox"), ("chrome", "Google Chrome"), ("calc", "Calculator"),
     ("visual studio code", "Visual Studio Code"), ("firefx", "Firefox")],
)  # fmt: skip
def test_best_match(query: str, expected: str) -> None:
    app, _ = best_match(query, APPS)
    assert app is not None and app.name == expected


def test_no_match_and_ambiguous() -> None:
    assert best_match("photoshop", APPS) == (None, [])
    app, ties = best_match("text", APPS)
    assert app is None and {a.name for a in ties} == {"Text Editor", "Text Expander"}


def test_score_bounds() -> None:
    assert score("", "Firefox") == 0
    assert score("firefox", "Firefox") == 1


def test_desktop_file_parsing(tmp_path: Path) -> None:
    good = tmp_path / "good.desktop"
    good.write_text("[Desktop Entry]\nType=Application\nName=Good App\nExec=good %U\n")
    hidden = tmp_path / "hidden.desktop"
    hidden.write_text("[Desktop Entry]\nType=Application\nName=Hidden\nNoDisplay=true\n")
    link = tmp_path / "link.desktop"
    link.write_text("[Desktop Entry]\nType=Link\nName=Web\n")
    broken = tmp_path / "broken.desktop"
    broken.write_bytes(b"\xff\xfe garbage")
    assert parse_desktop_file(good) == App("Good App", str(good))
    assert parse_desktop_file(hidden) is None
    assert parse_desktop_file(link) is None
    assert parse_desktop_file(broken) is None


def test_linux_media_commands() -> None:
    all_tools = {"playerctl": True, "wpctl": True, "pactl": True}
    assert linux_command("next", all_tools) == ["playerctl", "next"]
    assert linux_command("volume_up", all_tools) == ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%+"]
    assert linux_command("mute", {"pactl": True}) == ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"]
    with pytest.raises(MediaUnavailable):
        linux_command("play_pause", {"wpctl": True})
    with pytest.raises(MediaUnavailable):
        linux_command("volume_down", {})


def test_macos_scripts_are_fixed_strings() -> None:
    assert "output volume" in macos_script("volume_up")
    assert 'tell application "Spotify" to next track' in macos_script("next")
