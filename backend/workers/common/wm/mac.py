"""macOS: System Events (Accessibility) through osascript's JavaScript, and NSScreen for the
screens. Needs Accessibility permission for the app running the assistant (System Settings →
Privacy & Security → Accessibility)."""

import json
import subprocess
from typing import Any

from . import Screen, Win, WindowManagerError

PERMISSION = (
    "macOS needs Accessibility permission to move windows: System Settings → Privacy & Security → "
    "Accessibility → turn on Jarvis (or Terminal), then try again"
)

LIST = """
function run() {
  const se = Application("System Events");
  const out = [];
  for (const p of se.applicationProcesses.whose({backgroundOnly: false})()) {
    let wins;
    try { wins = p.windows(); } catch (e) { continue; }
    const pid = p.unixId(), front = p.frontmost();
    wins.forEach((w, i) => {
      try {
        const pos = w.position(), size = w.size();
        let mini = false;
        try { mini = w.attributes.byName("AXMinimized").value(); } catch (e) {}
        out.push({id: pid + ":" + i, app: p.name(), title: w.name() || "", x: pos[0], y: pos[1],
                  width: size[0], height: size[1], minimized: mini, focused: front && i === 0});
      } catch (e) {}
    });
  }
  return JSON.stringify(out);
}
"""

SCREENS = """
ObjC.import("AppKit");
function run() {
  const screens = $.NSScreen.screens, out = [];
  const top = screens.objectAtIndex(0).frame.size.height;  // the menu-bar screen sets the origin
  for (let i = 0; i < screens.count; i++) {
    const s = screens.objectAtIndex(i), v = s.visibleFrame;
    out.push({index: i, x: v.origin.x, y: top - (v.origin.y + v.size.height), width: v.size.width,
              height: v.size.height, primary: i === 0, name: ObjC.unwrap(s.localizedName) || ""});
  }
  return JSON.stringify(out);
}
"""

PLACE = """
function run(argv) {
  const [pid, index, x, y, w, h] = argv.map(Number);
  const p = Application("System Events").applicationProcesses.whose({unixId: pid})[0];
  const win = p.windows[index];
  try { win.attributes.byName("AXMinimized").value = false; } catch (e) {}
  win.position = [x, y];
  win.size = [w, h];
  win.position = [x, y];  // again: moving between screens can clamp the first move
  return "ok";
}
"""

FOCUS = """
function run(argv) {
  const [pid, index] = argv.map(Number);
  const p = Application("System Events").applicationProcesses.whose({unixId: pid})[0];
  p.frontmost = true;
  try { p.windows[index].actions.byName("AXRaise").perform(); } catch (e) {}
  return "ok";
}
"""


def _jxa(script: str, *args: str) -> Any:
    try:
        argv = ["osascript", "-l", "JavaScript", "-e", script, *args]
        done = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WindowManagerError(f"osascript failed: {exc}") from exc
    if done.returncode != 0:
        err = done.stderr.lower()
        if "assistive" in err or "not allowed" in err or "-1719" in err or "-25211" in err:
            raise WindowManagerError(PERMISSION)
        raise WindowManagerError(f"osascript failed: {done.stderr.strip()[:160]}")
    return json.loads(done.stdout) if done.stdout.strip().startswith(("[", "{")) else done.stdout.strip()


class MacWindows:
    name = "macOS"

    def windows(self) -> list[Win]:
        return [
            Win(
                id=w["id"],
                app=w["app"],
                title=w["title"],
                x=int(w["x"]),
                y=int(w["y"]),
                width=int(w["width"]),
                height=int(w["height"]),
                minimized=bool(w["minimized"]),
                focused=bool(w["focused"]),
            )
            for w in _jxa(LIST)
        ]

    def screens(self) -> list[Screen]:
        return [
            Screen(
                index=s["index"],
                x=int(s["x"]),
                y=int(s["y"]),
                width=int(s["width"]),
                height=int(s["height"]),
                primary=bool(s["primary"]),
                builtin="built-in" in s["name"].lower(),
                name=s["name"],
            )
            for s in _jxa(SCREENS)
        ]

    def place(self, win: Win, x: int, y: int, width: int, height: int) -> None:
        pid, index = win.id.split(":")
        _jxa(PLACE, pid, index, str(x), str(y), str(width), str(height))

    def maximize(self, win: Win, screen: Screen) -> None:
        self.place(win, screen.x, screen.y, screen.width, screen.height)

    def focus(self, win: Win) -> None:
        pid, index = win.id.split(":")
        _jxa(FOCUS, pid, index)
