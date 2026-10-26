"""Virtual keyboard + wheel through Linux uinput (pure Python ioctls, no daemon).

Works under Wayland and X11 because events enter at the kernel input layer. Callers
must confirm the target window is active first; uinput itself can't target a window.
"""

import os
import struct
import sys
import threading
import time

from workers.hands.keys import KEYCODES

DEVICE = "/dev/uinput"
EV_SYN, EV_KEY, EV_REL = 0, 1, 2
SYN_REPORT = 0
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0, 1, 6, 8
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045566
UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405C5503, 0x5501, 0x5502
BUS_VIRTUAL = 0x06
SETTLE_SECONDS = 0.6  # the compositor needs a moment to pick up a new device
EVENT = struct.Struct("llHHi")


def available() -> str | None:
    if not sys.platform.startswith("linux"):
        return "raw key input is only implemented on Linux so far"
    if not os.access(DEVICE, os.W_OK):
        return f"no write access to {DEVICE} (add a udev rule or join the 'input' group)"
    return None


def encode(kind: int, code: int, value: int) -> bytes:
    now = time.time()
    return EVENT.pack(int(now), int((now % 1) * 1_000_000), kind, code, value)


def combo_events(codes: list[int]) -> list[bytes]:
    """Press keys in order, release in reverse, with a sync after each change."""
    sync = encode(EV_SYN, SYN_REPORT, 0)
    events: list[bytes] = []
    for code in codes:
        events += [encode(EV_KEY, code, 1), sync]
    for code in reversed(codes):
        events += [encode(EV_KEY, code, 0), sync]
    return events


def wheel_events(direction: str, amount: int) -> list[bytes]:
    axis = REL_WHEEL if direction in ("up", "down") else REL_HWHEEL
    step = 1 if direction in ("up", "right") else -1
    sync = encode(EV_SYN, SYN_REPORT, 0)
    return [ev for _ in range(amount) for ev in (encode(EV_REL, axis, step), sync)]


class VirtualInput:
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._fd: int | None = None

    def _open(self) -> int:
        if sys.platform == "win32":
            raise OSError("uinput is Linux-only")
        import fcntl  # POSIX-only; imported here so this module loads on every OS

        if self._fd is not None:
            return self._fd
        fd = os.open(DEVICE, os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        for code in {*KEYCODES.values(), BTN_LEFT, BTN_RIGHT, BTN_MIDDLE}:
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_REL)
        for axis in (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL):
            fcntl.ioctl(fd, UI_SET_RELBIT, axis)
        name = b"assistant-virtual-input".ljust(80, b"\0")
        fcntl.ioctl(fd, UI_DEV_SETUP, struct.pack("HHHH80sI", BUS_VIRTUAL, 0x1, 0x1, 1, name, 0))
        fcntl.ioctl(fd, UI_DEV_CREATE)
        time.sleep(SETTLE_SECONDS)
        self._fd = fd
        return fd

    def send(self, events: list[bytes]) -> None:
        with self._lock:
            fd = self._open()
            for event in events:
                os.write(fd, event)
                time.sleep(0.002)

    def close(self) -> None:
        if sys.platform == "win32":
            return
        import fcntl

        if self._fd is not None:
            fcntl.ioctl(self._fd, UI_DEV_DESTROY)
            os.close(self._fd)
            self._fd = None
