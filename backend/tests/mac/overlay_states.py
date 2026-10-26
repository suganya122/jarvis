"""macOS CI: show the overlay box in each state on a real Mac and screenshot the whole screen.
Run from backend/: python tests/mac/overlay_states.py OUT_DIR"""

import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contracts.overlay import OverlayApproval, OverlayItem, OverlayTask, OverlayView
from workers.overlay.window import OverlayWindow

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
you = OverlayItem(kind="you", text="Book a flight from Chennai to Delhi on Friday")
FLIGHT = "IndiGo 6E 2341 leaves at 06:10 and lands at 08:55 for ₹4,812. Want me to book it?"
STATES = {
    "1-listening": OverlayView(
        phase="listening", partial="book a flight from chennai to", status="Live: just talk."
    ),
    "2-working": OverlayView(phase="acting", items=[you,
        OverlayItem(kind="step", text="Open google.com/travel/flights", done=True),
        OverlayItem(kind="step", text="Search Chennai → Delhi, Friday", done=False)]),
    "3-done": OverlayView(phase="done", items=[you,
        OverlayItem(kind="step", text="Open google.com/travel/flights"),
        OverlayItem(kind="step", text="Search Chennai → Delhi, Friday"),
        OverlayItem(kind="answer", text=FLIGHT)]),
    "4-approval": OverlayView(phase="acting", items=[you],
        approval=OverlayApproval(approval_id="a", tool="browser_finish", risk="purchase",
                                 summary="Pay ₹4,812 for IndiGo 6E 2341, Chennai → Delhi, Friday 06:10")),
}  # fmt: skip

root = tk.Tk()
window = OverlayWindow(root, lambda _m: None)
for name, view in STATES.items():
    window.show(view)
    if name == "2-working":
        window.chips.show([OverlayTask(task_id="t", title="find the three best laptops under $1000…",
                                       state="running", detail="Working on it: browser")])  # fmt: skip
    end = time.time() + 1.5
    while time.time() < end:
        root.update()
        time.sleep(0.02)
    subprocess.run(["screencapture", "-x", str(out / f"overlay-{name}.png")], check=False)
    print(
        "captured",
        name,
        "at",
        root.winfo_rootx(),
        root.winfo_rooty(),
        root.winfo_width(),
        "x",
        root.winfo_height(),
    )
root.destroy()
