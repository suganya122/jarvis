"""macOS CI: arrange real windows (TextEdit) with the System Events backend, read them back.
Prints what happened; Accessibility may not be granted on CI, which is reported, not hidden."""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workers.common.wm import WindowManagerError, detect
from workers.os_tools.window_tools import Placement, arrange

subprocess.run(["open", "-a", "TextEdit"], check=False)
time.sleep(4)
try:
    wm = detect()
    print("backend:", wm.name)
    print("screens:", wm.screens())
    print("windows:", [(w.app, w.title, w.x, w.y, w.width, w.height) for w in wm.windows()][:8])
    lines, ok = arrange(wm, [Placement(app="TextEdit", slot="left")], lambda _a: False)
    print("arrange:", lines, "verified:", ok)
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    subprocess.run(["screencapture", "-x", str(out / "windows-textedit-left.png")], check=False)
except WindowManagerError as exc:
    print("window placement unavailable here:", exc)
