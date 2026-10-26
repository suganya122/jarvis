"""The real overlay box (Tk) going through every state in every order, on a private X display.
A finished conversation hides the footer; an approval must still find its place (macOS CI caught
this crashing)."""

import subprocess
import sys

from tests.test_overlay_window import BACKEND, needs_x, xdisplay  # noqa: F401 - fixture

SCRIPT = """
import itertools, tkinter as tk
from contracts.overlay import OverlayApproval, OverlayItem, OverlayView
from workers.overlay.window import OverlayWindow
you = OverlayItem(kind="you", text="Book a flight")
views = {
    "listening": OverlayView(phase="listening", partial="book a", status="Live: just talk."),
    "working": OverlayView(phase="acting", items=[you, OverlayItem(kind="step", text="Search", done=False)]),
    "done": OverlayView(phase="done", items=[you, OverlayItem(kind="answer", text="IndiGo at 06:10.")]),
    "approval": OverlayView(phase="acting", items=[you], approval=OverlayApproval(
        approval_id="a", tool="browser_finish", risk="purchase", summary="Pay 4812")),
}
root = tk.Tk()
w = OverlayWindow(root, lambda m: None)
for a, b in itertools.permutations(views, 2):
    w.show(views[a]); root.update()
    w.show(views[b]); root.update()
print("ALL ORDERS OK")
"""


@needs_x
def test_every_state_change_order_works(xdisplay: dict[str, str]) -> None:  # noqa: F811
    done = subprocess.run([sys.executable, "-c", SCRIPT], env=xdisplay, cwd=BACKEND, capture_output=True,
                          text=True, timeout=60, check=False)  # fmt: skip
    assert "ALL ORDERS OK" in done.stdout, done.stderr[-2000:]
