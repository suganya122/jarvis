"""Background jobs as small chips in the top-right corner of the pointer's display, in their
own always-on-top window that never takes focus. Click a finished chip to put it away."""

import sys
import tkinter as tk
from collections.abc import Callable

from contracts.overlay import OverlayTask

from .placement import EDGE, TOP_MARGIN, Rect

WIDTH = 300
BG, BORDER, FG, MUTED = "#141416", "#28282a", "#ffffff", "#9a9a9c"
DOT = {"running": "#6ee7b7", "waiting": "#f5b25b", "done": "#6ee7b7", "failed": "#ef7d74"}
MARK = {"running": "●", "waiting": "●", "done": "✓", "failed": "!"}


class TaskChips:
    def __init__(self, root: tk.Tk, font: tuple[str, int], small: tuple[str, int],
                 monitor: Callable[[], Rect]) -> None:  # fmt: skip
        self._win = tk.Toplevel(root, bg=BG)
        self._win.withdraw()
        self._win.attributes("-topmost", True)
        if sys.platform == "darwin":
            self._win.tk.call(
                "::tk::unsupported::MacWindowStyle", "style", str(self._win), "help", "noActivates"
            )
        else:
            self._win.overrideredirect(True)
        self._font, self._small = font, small
        self._monitor = monitor
        self._dismissed: set[str] = set()
        self._tasks: list[OverlayTask] = []

    def show(self, tasks: list[OverlayTask]) -> None:
        self._tasks = tasks
        visible = [t for t in tasks if t.task_id not in self._dismissed]
        for child in self._win.winfo_children():
            child.destroy()
        if not visible:
            self._win.withdraw()
            return
        for task in visible:
            chip = tk.Frame(self._win, bg=BG, highlightthickness=1, highlightbackground=BORDER)
            chip.pack(fill="x", pady=(0, 6))
            tk.Label(chip, text=MARK[task.state], fg=DOT[task.state], bg=BG, font=self._font, width=2).pack(
                side="left", padx=(8, 0), pady=8, anchor="n"
            )
            text = tk.Frame(chip, bg=BG)
            text.pack(side="left", fill="x", padx=(2, 10), pady=6)
            tk.Label(text, text=task.title, fg=FG, bg=BG, font=self._font, anchor="w", justify="left",
                     wraplength=WIDTH - 50).pack(anchor="w")  # fmt: skip
            tk.Label(text, text=task.detail, fg=MUTED, bg=BG, font=self._small, anchor="w", justify="left",
                     wraplength=WIDTH - 50).pack(anchor="w")  # fmt: skip
            if task.state in ("done", "failed"):
                for widget in (chip, *chip.winfo_children(), *text.winfo_children()):
                    widget.bind("<Button-1>", self._dismisser(task.task_id))
        tk.Frame(self._win, bg=BG, width=WIDTH, height=0).pack()
        self._win.update_idletasks()
        area = self._monitor()
        x = area.x + area.w - WIDTH - EDGE * 3
        self._win.geometry(f"+{x}+{area.y + TOP_MARGIN}")
        self._win.deiconify()
        self._win.lift()

    def _dismisser(self, task_id: str) -> Callable[["tk.Event[tk.Misc]"], None]:
        return lambda _event: self._dismiss(task_id)

    def _dismiss(self, task_id: str) -> None:
        self._dismissed.add(task_id)
        self.show(self._tasks)
