"""The hands worker's macOS side against a stand-in for `Jarvis --hands` that speaks the same line
protocol (the real one is Swift and needs a Mac with Accessibility allowed)."""

import json
import stat
import sys
from pathlib import Path

import pytest

from contracts.hands import Rect
from workers.hands.errors import AtspiError
from workers.hands.mac import MacHands
from workers.hands.mac_keys import MAC_KEYCODES

FAKE_HELPER = """#!{python}
import json, sys
trusted = {trusted}
log = open({log!r}, "a")
for line in sys.stdin:
    msg = json.loads(line)
    log.write(line); log.flush()
    op, reply = msg["op"], {{"id": msg["id"], "ok": True}}
    if op == "trusted":
        reply["result"] = {{"trusted": trusted}}
    elif not trusted:
        reply = {{"id": msg["id"], "ok": False, "error": "turn on Jarvis in Accessibility"}}
    elif op == "windows":
        reply["result"] = [
            {{"window_id": "w1", "app": "Safari", "title": "Flights", "focused": True, "on_screen": True,
              "bounds": {{"x": 0, "y": 25, "width": 800, "height": 600}}}},
            {{"window_id": "w2", "app": "1Password", "title": "Vault", "focused": False, "on_screen": True,
              "bounds": None}},
        ]
    elif op == "elements":
        reply["result"] = [{{"element_id": "e5", "role": "button", "name": "Search", "actions": ["press"],
                            "states": [], "text": None, "bounds": None, "depth": 3}}]
    elif op == "front_app":
        reply["result"] = "Safari"
    else:
        reply["result"] = {{"ok": True, "evidence": {{"op": op}}}}
    print(json.dumps(reply), flush=True)
"""

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="a script helper needs a POSIX shebang")


def hands(tmp_path: Path, trusted: bool = True) -> tuple[MacHands, Path]:
    log = tmp_path / "requests.jsonl"
    path = tmp_path / "Jarvis"
    path.write_text(FAKE_HELPER.format(python=sys.executable, trusted=trusted, log=str(log)))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return MacHands(str(path), blocked_apps=["1password"]), log


def requests(log: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_windows_and_controls_come_back_typed_and_blocked_apps_stay_hidden(tmp_path: Path) -> None:
    h, _ = hands(tmp_path)
    windows = h.list_windows()
    assert [(w.app, w.title, w.focused) for w in windows] == [("Safari", "Flights", True)]
    assert windows[0].bounds == Rect(x=0, y=25, width=800, height=600)
    [button] = h.find_elements("w1", "search")
    assert (button.role, button.name, button.actions) == ("button", "Search", ("press",))
    with pytest.raises(AtspiError, match="blocklist"):
        h.focus("w2")  # never touched, even by id
    assert h.activate("e5").ok


def test_key_combos_become_mac_key_codes_and_flags(tmp_path: Path) -> None:
    h, log = hands(tmp_path)
    h.list_windows()
    assert h.key("w1", "Cmd+Shift+T").ok
    sent = requests(log)[-1]
    assert sent["keycode"] == MAC_KEYCODES["t"] and sent["flags"] == ["shift", "meta"]
    h.scroll("w1", "down", 3)
    assert requests(log)[-1]["dy"] == -3


def test_without_accessibility_it_says_how_and_asks_macos_once(tmp_path: Path) -> None:
    h, log = hands(tmp_path, trusted=False)
    for _ in range(2):
        with pytest.raises(AtspiError, match="Accessibility"):
            h.list_windows()
    prompts = [r for r in requests(log) if r["op"] == "trusted" and r.get("prompt")]
    assert len(prompts) == 1  # macOS's own "allow Jarvis" dialog, not a nag every time
    assert h.trusted() is False


def test_every_key_name_has_a_mac_code() -> None:
    from workers.hands.keys import KEYCODES, MODIFIERS

    missing = [k for k in KEYCODES if k not in MAC_KEYCODES and k not in MODIFIERS]
    assert missing == []
