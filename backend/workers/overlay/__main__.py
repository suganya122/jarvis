"""Voice overlay process: `python -m workers.overlay`, driven by core over stdin/stdout.

Without a display or without Tk it reports `available: false` once and waits for stdin
to close, so core doesn't keep restarting it.
"""

import os
import queue
import sys
import threading

from pydantic import ValidationError

from contracts import CONTRACT_VERSION
from contracts.overlay import (
    FromOverlay,
    OverlayHide,
    OverlayLevel,
    OverlayReady,
    OverlayShow,
    OverlayTasks,
    ToOverlay,
    to_overlay_adapter,
)

POLL_MS = 25


def send(message: FromOverlay) -> None:
    sys.stdout.write(message.model_dump_json() + "\n")
    sys.stdout.flush()


def read_stdin(inbox: "queue.Queue[ToOverlay | None]") -> None:
    for raw in sys.stdin.buffer:
        try:
            inbox.put(to_overlay_adapter.validate_json(raw))
        except ValidationError:
            continue
    inbox.put(None)


def unavailable(detail: str) -> None:
    send(OverlayReady(contract_version=CONTRACT_VERSION, available=False, detail=detail))
    for _ in sys.stdin.buffer:  # stay up quietly until core closes the pipe
        pass


def main() -> None:
    try:
        import tkinter as tk
    except ImportError:
        hint = " (Ubuntu/Debian: sudo apt install python3-tk)" if sys.platform.startswith("linux") else ""
        unavailable(f"Tk isn't installed{hint}")
        return
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        unavailable("no X11 display (on Wayland it needs XWayland)")
        return
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        unavailable(f"can't open a window: {exc}")
        return
    from .window import OverlayWindow

    window = OverlayWindow(root, send)
    backend = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "X11")
    send(
        OverlayReady(
            contract_version=CONTRACT_VERSION, available=True, detail=f"Tk {tk.TkVersion}, {backend}"
        )
    )
    inbox: queue.Queue[ToOverlay | None] = queue.Queue()
    threading.Thread(target=read_stdin, args=(inbox,), daemon=True).start()

    def pump() -> None:
        while True:
            try:
                message = inbox.get_nowait()
            except queue.Empty:
                break
            if message is None:
                root.destroy()
                return
            if isinstance(message, OverlayShow):
                window.show(message.view)
            elif isinstance(message, OverlayHide):
                window.hide()
            elif isinstance(message, OverlayLevel):
                window.level(message.level)
            elif isinstance(message, OverlayTasks):
                window.chips.show(message.tasks)
        root.after(POLL_MS, pump)

    root.after(POLL_MS, pump)
    root.mainloop()


if __name__ == "__main__":
    main()
