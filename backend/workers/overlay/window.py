"""The overlay window (Tk): always on top, never takes focus by itself, shows on the
display the pointer is on. Keyboard: click it (or Tab into it) and use Tab / Enter / Space;
Y approves, N denies, Escape stops (or closes when nothing is running). Global shortcuts
(`core.cli approve|deny|stop`) work without focusing it at all.
"""

import contextlib
import ctypes
import ctypes.util
import sys
import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont

from contracts import APP_NAME
from contracts.overlay import FromOverlay, OverlayDecide, OverlayDismiss, OverlayStop, OverlayView

from .chips import TaskChips
from .orb import Orb, reduced_motion
from .placement import Rect, monitor_at, monitors, place
from .thread import ThreadView

WIDTH = 420
# The app's dark tokens (frontend/src/styles/tokens.css): it floats over any app, so one theme.
BG, BORDER, FG, TEXT_2, MUTED = "#141416", "#28282a", "#ffffff", "#c4c2c3", "#9a9a9c"
BUTTON, BUTTON_ACTIVE, INK = "#1e1e21", "#2a2a2d", "#0b0b0c"
MINT, AMBER, DANGER = "#6ee7b7", "#f5b25b", "#ef7d74"
PHASES = {  # label, colour, orb motion
    "listening": ("Listening", MINT, "listening"),
    "thinking": ("Thinking", TEXT_2, "working"),
    "acting": ("Working", MINT, "working"),
    "done": ("Done", MINT, "still"),
    "error": ("Problem", DANGER, "still"),
    "needs_chat": ("Chat window needed", AMBER, "still"),
}
ACTIVE = ("listening", "thinking", "acting")
FADE_STEPS, FADE_MS = 6, 30


class OverlayWindow:
    def __init__(self, root: tk.Tk, send: Callable[[FromOverlay], None]) -> None:
        self.root = root
        self._send = send
        self._view: OverlayView | None = None
        self.visible = False
        self._fade: str | None = None
        self._still = reduced_motion()
        root.title(f"{APP_NAME} voice")
        root.configure(background=BG, highlightthickness=1, highlightbackground=BORDER)
        root.withdraw()
        self._no_focus_setup()
        root.attributes("-topmost", True)
        base = tkfont.nametofont("TkDefaultFont").actual()
        size = max(base["size"], 10)
        bold, small = (base["family"], size, "bold"), (base["family"], max(size - 1, 9))
        # Header: the orb, what it's doing, and a close button (like a small floating panel).
        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=(10, 8), pady=(8, 0))
        self.orb = Orb(header, BG, self._still, size=40, radius=8)
        self.orb.canvas.pack(side="left")
        self.phase = tk.Label(header, text="", font=bold, bg=BG, fg=FG, anchor="w")
        self.phase.pack(side="left", padx=(6, 0))
        self.close = tk.Button(header, text="✕", command=self._dismiss, bg=BG, fg=MUTED,
                               activebackground=BUTTON, activeforeground=FG, relief="flat", bd=0, padx=6,
                               takefocus=True, highlightthickness=2, highlightcolor=MINT,
                               highlightbackground=BG)  # fmt: skip
        self.close.pack(side="right")
        body = tk.Frame(root, bg=BG)
        body.pack(fill="x", padx=16)
        # What "this"/"here" means, so the user can correct it ("no, the other one").
        self.target = tk.Label(
            body, text="", font=small, bg=BG, fg=MUTED, anchor="w", wraplength=WIDTH - 32, justify="left"
        )
        self.thread = ThreadView(body, WIDTH - 32, (base["family"], size), small)
        self.thread.frame.pack(fill="x")
        self.approval = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=AMBER)
        self.question = tk.Label(self.approval, text="", bg=BG, fg=FG, wraplength=WIDTH - 52, justify="left")
        self.question.pack(anchor="w", padx=10, pady=(8, 0))
        row = tk.Frame(self.approval, bg=BG)
        row.pack(anchor="w", padx=10, pady=8)
        self.approve = self._button(row, "Approve (Y)", lambda: self._decide(True), primary=True)
        self.deny = self._button(row, "Deny (N)", lambda: self._decide(False))
        self.approve.pack(side="left")
        self.deny.pack(side="left", padx=(8, 0))
        self.bottom = tk.Frame(root, bg=BG)
        self.bottom.pack(fill="x", padx=16, pady=(8, 12))
        self.stop = self._button(self.bottom, "Stop (Esc)", self._stop)
        self.hint = tk.Label(self.bottom, text="", font=small, bg=BG, fg=MUTED, anchor="w", justify="left",
                             wraplength=WIDTH - 140)  # fmt: skip
        self.hint.pack(side="left")
        tk.Frame(root, bg=BG, width=WIDTH, height=0).pack()  # fixed width; height follows the conversation
        self.chips = TaskChips(root, (base["family"], size), small, self._monitor)
        root.bind("<Escape>", lambda _e: self._escape())
        root.bind("<KeyPress-y>", lambda _e: self._decide(True))
        root.bind("<KeyPress-n>", lambda _e: self._decide(False))
        root.bind("<Button-1>", lambda _e: self.take_focus())  # keyboard use starts with a click

    def _button(
        self, parent: tk.Misc, text: str, command: Callable[[], None], primary: bool = False
    ) -> tk.Button:
        bg, fg, active = (MINT, INK, "#8af0c8") if primary else (BUTTON, FG, BUTTON_ACTIVE)
        button = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=active,
                           activeforeground=fg, relief="flat", padx=10, pady=4, takefocus=True, bd=0,
                           highlightthickness=2, highlightcolor=MINT, highlightbackground=BG)  # fmt: skip
        button.bind("<Return>", lambda _e: command())
        return button

    # ---- never steal focus --------------------------------------------------------

    def _no_focus_setup(self) -> None:
        if sys.platform == "darwin":
            # A utility ("help") window that doesn't activate the app when shown.
            self.root.tk.call(
                "::tk::unsupported::MacWindowStyle", "style", str(self.root), "help", "noActivates"
            )
        else:
            # X11: not managed by the window manager, so it's never given focus or a taskbar
            # entry. Windows: no title bar or taskbar button; WS_EX_NOACTIVATE is added on show.
            self.root.overrideredirect(True)

    def take_focus(self) -> None:
        """Only on a click (the user's choice). On X11 an override-redirect window gets no
        focus from the window manager and Tk's focus -force doesn't move X's focus, so ask
        the X server directly."""
        if sys.platform.startswith("linux"):
            _x11_set_focus(int(self.root.wm_frame(), 16))
        elif sys.platform == "win32":
            # WS_EX_NOACTIVATE also ignores clicks; right after the user's click Windows lets
            # us take the foreground explicitly.
            ctypes.windll.user32.SetForegroundWindow(int(self.root.wm_frame(), 16))  # type: ignore[attr-defined,unused-ignore]
        self.root.focus_force()

    def _show_without_focus(self) -> None:
        if sys.platform == "win32":
            self.root.update_idletasks()
            user32 = ctypes.windll.user32  # type: ignore[attr-defined,unused-ignore]  # Windows only
            hwnd = int(self.root.wm_frame(), 16)
            gwl_exstyle, ws_ex_noactivate, ws_ex_toolwindow, ws_ex_topmost = -20, 0x08000000, 0x80, 0x8
            style = user32.GetWindowLongW(hwnd, gwl_exstyle)
            user32.SetWindowLongW(
                hwnd, gwl_exstyle, style | ws_ex_noactivate | ws_ex_toolwindow | ws_ex_topmost
            )
            user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            self.root.state("normal")
        else:
            self.root.deiconify()
        self.root.lift()

    # ---- drawing ----------------------------------------------------------------

    def show(self, view: OverlayView) -> None:
        self._view = view
        label, color, motion = PHASES[view.phase]
        if view.approval:
            label, color, motion = "Needs your OK", AMBER, "still"
        self.phase.configure(text=label, fg=color)
        self.orb.set(motion, color)
        if view.target:
            self.target.configure(text=f"◎ {view.target}")
            self.target.pack(anchor="w", before=self.thread.frame, pady=(2, 0))
        else:
            self.target.pack_forget()
        status = view.status if view.phase != "listening" or not view.partial else ""
        self.thread.render(view.items, view.partial, status)
        if view.approval:
            self.question.configure(text=f"Allow {view.approval.tool}? {view.approval.summary}")
            self.approval.pack(fill="x", padx=16, pady=(8, 0), before=self.bottom)
        else:
            self.approval.pack_forget()
        self.hint.configure(text=hint(view))
        if view.phase in ACTIVE:  # Stop while something runs; the ✕ closes a finished one
            self.stop.pack(side="left", before=self.hint, padx=(0, 12))
        else:
            self.stop.pack_forget()
        if view.phase in ACTIVE or self.hint.cget("text"):
            self.bottom.pack(fill="x", padx=16, pady=(8, 12))
        else:
            self.bottom.pack_forget()  # a finished conversation: no empty footer
            self.thread.frame.pack_configure(pady=(0, 12))
        if self._fade is not None:  # shown again while fading out
            self.root.after_cancel(self._fade)
            self._fade = None
            self._alpha(1.0)
        if not self.visible:
            self.root.update_idletasks()
            x, y = self.position(self.root.winfo_reqwidth(), self.root.winfo_reqheight())
            self.root.geometry(f"+{x}+{y}")
            self._alpha(0.0 if not self._still else 1.0)
            self._show_without_focus()
            self.visible = True
            if not self._still:
                self._fade_step(1, +1)
        self.orb.start()

    def level(self, value: float) -> None:
        self.orb.level(value)

    def hide(self) -> None:
        self._view = None
        self.orb.stop()
        if not self.visible or self._fade is not None:
            return
        if self._still:
            self._withdraw()
        else:
            self._fade_step(FADE_STEPS - 1, -1)

    def _withdraw(self) -> None:
        self._fade = None
        self.root.withdraw()
        self._alpha(1.0)
        self.visible = False

    def _fade_step(self, step: int, direction: int) -> None:
        self._alpha(step / FADE_STEPS)
        if direction < 0 and step <= 0:
            self._withdraw()
        elif direction > 0 and step >= FADE_STEPS:
            self._fade = None
        else:
            self._fade = self.root.after(FADE_MS, self._fade_step, step + direction, direction)

    def _alpha(self, value: float) -> None:
        # Needs a compositor on X11; without one this does nothing and it simply appears.
        with contextlib.suppress(tk.TclError):
            self.root.attributes("-alpha", value)

    def position(self, width: int, height: int) -> tuple[int, int]:
        return place(self._monitor(), width, height)

    def _monitor(self) -> Rect:
        """The display the pointer is on (or, with no monitor list, the whole virtual screen)."""
        px, py = self.root.winfo_pointerxy()
        found = monitor_at(px, py, monitors())
        if found is None:
            found = Rect(self.root.winfo_vrootx(), self.root.winfo_vrooty(),
                         self.root.winfo_vrootwidth(), self.root.winfo_vrootheight())  # fmt: skip
        return found

    # ---- input ------------------------------------------------------------------

    def _decide(self, approved: bool) -> None:
        if self._view and self._view.approval:
            self._send(OverlayDecide(approval_id=self._view.approval.approval_id, approved=approved))

    def _stop(self) -> None:
        if self._view and self._view.phase in ACTIVE:
            self._send(OverlayStop())
        else:
            self._send(OverlayDismiss())
            self.hide()

    def _escape(self) -> None:
        self._stop()

    def _dismiss(self) -> None:
        """The ✕: stop anything running, and put the box away."""
        if self._view and self._view.phase in ACTIVE:
            self._send(OverlayStop())
        self._send(OverlayDismiss())
        self.hide()


def hint(view: OverlayView) -> str:
    """What to do next. The voice shortcut is whatever the user bound in their desktop's
    keyboard settings, so it's named as "your voice shortcut", not as keys we'd have to guess."""
    if view.approval:
        return "Click here, then Y or N. Your approve and deny shortcuts work too."
    if view.phase == "listening" and view.auto_send:
        return "Stop talking to run it, or press your voice shortcut again."
    if view.phase == "listening":
        return "Press your voice shortcut again to run it."
    if view.phase in ACTIVE:
        return "Your stop shortcut works from any app."
    if view.phase == "needs_chat":
        return f"Open {APP_NAME} in your browser, then try again."
    return ""


class _Xlib:
    """libX11 through ctypes, loaded on first use (only on a click, only on X11)."""

    loaded = False
    lib: ctypes.CDLL | None = None
    display: int | None = None


def _x11_set_focus(window_id: int) -> None:
    if not _Xlib.loaded:
        _Xlib.loaded = True
        path = ctypes.util.find_library("X11")
        if path:
            lib = ctypes.cdll.LoadLibrary(path)
            lib.XOpenDisplay.restype = ctypes.c_void_p
            lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            lib.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            lib.XFlush.argtypes = [ctypes.c_void_p]
            _Xlib.lib, _Xlib.display = lib, lib.XOpenDisplay(None)
    if _Xlib.lib is None or not _Xlib.display:
        return
    revert_to_parent, current_time = 2, 0
    _Xlib.lib.XSetInputFocus(_Xlib.display, window_id, revert_to_parent, current_time)
    _Xlib.lib.XFlush(_Xlib.display)
