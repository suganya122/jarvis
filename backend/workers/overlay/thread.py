"""The conversation inside the overlay box: what you said (a bubble on the right), each step
(ticked off when done), the answer, and problems. Newest last; only the last few fit."""

import tkinter as tk

from contracts.overlay import OverlayItem

SHOWN = 6
BG, FG, TEXT_2, MUTED = "#141416", "#ffffff", "#c4c2c3", "#9a9a9c"
BUBBLE, MINT, DANGER = "#232327", "#6ee7b7", "#ef7d74"


class ThreadView:
    def __init__(self, parent: tk.Misc, width: int, font: tuple[str, int], small: tuple[str, int]) -> None:
        self.frame = tk.Frame(parent, bg=BG)
        self._width = width
        self._font, self._small = font, small
        self._shown: list[tuple[str, str, bool]] = []

    def render(self, items: list[OverlayItem], partial: str, status: str) -> None:
        """Redraw only when something changed (a partial transcript arrives many times a second)."""
        rows: list[tuple[str, str, bool]] = [(i.kind, i.text, i.done) for i in items[-SHOWN:]]
        if partial:
            rows.append(("hearing", partial, False))
        if status:
            rows.append(("status", status, False))
        if rows == self._shown:
            return
        self._shown = rows
        for child in self.frame.winfo_children():
            child.destroy()
        for kind, text, done in rows:
            self._row(kind, text, done)

    def _row(self, kind: str, text: str, done: bool) -> None:
        if kind in ("you", "hearing"):  # your words: a bubble on the right
            holder = tk.Frame(self.frame, bg=BG)
            holder.pack(fill="x", pady=(6, 0))
            colour = FG if kind == "you" else MUTED  # still being heard: dimmer
            bubble = tk.Label(holder, text=text, bg=BUBBLE, fg=colour, font=self._font,
                              wraplength=self._width - 90, justify="left", padx=10, pady=5)  # fmt: skip
            bubble.pack(anchor="e")
        elif kind == "step":
            row = tk.Frame(self.frame, bg=BG)
            row.pack(fill="x", pady=(4, 0))
            mark, colour = ("✓", MINT) if done else ("…", MUTED)
            tk.Label(row, text=mark, bg=BG, fg=colour, font=self._small, width=2, anchor="n").pack(
                side="left", anchor="n"
            )
            tk.Label(row, text=text, bg=BG, fg=TEXT_2, font=self._small, wraplength=self._width - 40,
                     justify="left", anchor="w").pack(side="left", fill="x")  # fmt: skip
        else:  # the answer, a problem, or what it's doing right now
            colour = {"answer": FG, "error": DANGER}.get(kind, TEXT_2)
            font = self._font if kind != "status" else self._small
            tk.Label(self.frame, text=text, bg=BG, fg=colour, font=font, wraplength=self._width - 8,
                     justify="left", anchor="w").pack(fill="x", pady=(6, 0))  # fmt: skip
