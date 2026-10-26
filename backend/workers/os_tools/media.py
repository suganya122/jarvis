"""Media keys and volume, per OS. Command builders are pure so they can be tested anywhere."""

import shutil
import subprocess
import sys
from typing import Literal

MediaAction = Literal["play_pause", "next", "previous", "stop", "volume_up", "volume_down", "mute"]

WINDOWS_KEYS: dict[MediaAction, int] = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
    "mute": 0xAD,
    "volume_down": 0xAE,
    "volume_up": 0xAF,
}
_MPRIS = {"play_pause": "play-pause", "next": "next", "previous": "previous", "stop": "stop"}
_MAC_PLAYER = {"play_pause": "playpause", "next": "next track", "previous": "previous track", "stop": "pause"}


class MediaUnavailable(Exception):
    pass


def linux_command(action: MediaAction, which: dict[str, bool]) -> list[str]:
    if action in _MPRIS:
        if which.get("playerctl"):
            return ["playerctl", _MPRIS[action]]
        raise MediaUnavailable("media keys need 'playerctl' installed")
    if which.get("wpctl"):
        arg = {"volume_up": ["set-volume", "@DEFAULT_AUDIO_SINK@", "5%+"],
               "volume_down": ["set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"],
               "mute": ["set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"]}[action]  # fmt: skip
        return ["wpctl", *arg]
    if which.get("pactl"):
        arg = {"volume_up": ["set-sink-volume", "@DEFAULT_SINK@", "+5%"],
               "volume_down": ["set-sink-volume", "@DEFAULT_SINK@", "-5%"],
               "mute": ["set-sink-mute", "@DEFAULT_SINK@", "toggle"]}[action]  # fmt: skip
        return ["pactl", *arg]
    raise MediaUnavailable("volume control needs 'wpctl' or 'pactl'")


def macos_script(action: MediaAction) -> str:
    if action == "volume_up":
        return "set volume output volume ((output volume of (get volume settings)) + 6)"
    if action == "volume_down":
        return "set volume output volume ((output volume of (get volume settings)) - 6)"
    if action == "mute":
        return "set volume output muted (not (output muted of (get volume settings)))"
    verb = _MAC_PLAYER[action]
    # There's no system-wide media key from the command line; drive the common players.
    return "\n".join(
        f'if application "{app}" is running then tell application "{app}" to {verb}'
        for app in ("Spotify", "Music")
    )


def available() -> str | None:
    """None when media control can work here, else why not."""
    if sys.platform in ("win32", "darwin"):
        return None
    if not (shutil.which("playerctl") or shutil.which("wpctl") or shutil.which("pactl")):
        return "install 'playerctl' (media) and 'wpctl' or 'pactl' (volume)"
    return None


def perform(action: MediaAction) -> None:
    if sys.platform == "win32":
        import ctypes

        user32 = ctypes.windll.user32
        key = WINDOWS_KEYS[action]
        user32.keybd_event(key, 0, 0, 0)
        user32.keybd_event(key, 0, 2, 0)  # KEYEVENTF_KEYUP
        return
    if sys.platform == "darwin":
        argv = ["osascript", "-e", macos_script(action)]
    else:
        which = {name: shutil.which(name) is not None for name in ("playerctl", "wpctl", "pactl")}
        argv = linux_command(action, which)
    # Fixed programs and fixed arguments only; nothing comes from the model.
    subprocess.run(argv, check=True, timeout=5, capture_output=True)  # noqa: S603
