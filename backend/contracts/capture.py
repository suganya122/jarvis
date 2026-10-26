"""Capture contract: on-demand screenshots. Never continuous, never stored on disk.

Accessibility text (contracts.hands) is always preferred; images are the fallback.
"""

from typing import Literal, Protocol, runtime_checkable

from contracts.common import ContractModel
from contracts.model import ImageData

CaptureKind = Literal["screen", "window", "select"]
"""'select' lets the user pick a region or window with the OS picker."""


class CaptureInfo(ContractModel):
    capture_id: str
    width: int
    """Size of the image the model saw."""
    height: int
    screen_x: int = 0
    """Where the image's top-left sits on the desktop, in desktop coordinates."""
    screen_y: int = 0
    scale: float = 1.0
    """Full-capture pixels per image pixel (from downscaling)."""
    desktop_width: int
    """Size of the whole desktop capture this image was cut from, in its own pixels."""
    desktop_height: int
    source: str
    """Backend, e.g. 'portal', 'x11', 'macos', 'windows'."""


class CapturedImage(ContractModel):
    info: CaptureInfo
    image: ImageData
    preview: ImageData


class HighlightBox(ContractModel):
    capture_id: str
    x: int
    y: int
    width: int
    height: int
    """In the pixel coordinates of that capture's image."""
    label: str = ""


class CaptureError(Exception):
    pass


@runtime_checkable
class Capture(Protocol):
    def capture(
        self, kind: CaptureKind, max_edge: int, window_bounds: tuple[int, int, int, int] | None = None
    ) -> CapturedImage:
        """Raises CaptureError when blocked (permission, blocklisted app, unsupported)."""
        ...
