"""Browser contract: the page as the assistant sees it, and the interface every browser
backend implements (Playwright for real, an in-memory fake for tests).

The assistant reads the page structure (interactive elements with short refs, plus
trimmed visible text), never raw HTML, and only falls back to screenshots elsewhere.
Everything in a snapshot is untrusted content from a website.
"""

from typing import Literal, Protocol

from pydantic import Field

from contracts.common import ContractModel

ElementKind = Literal["link", "button", "textbox", "checkbox", "radio", "select", "other"]
BrowserMode = Literal["isolated", "own"]


class PageElement(ContractModel):
    ref: str
    """Short id for this snapshot only, e.g. 'e12'. Skills store `locator()` instead."""
    kind: ElementKind
    name: str = Field(default="", max_length=200)
    """Accessible name: label, aria-label, visible text, or placeholder."""
    input_type: str = ""
    """For inputs: text, email, password, …; for buttons: submit / button."""
    autocomplete: str = ""
    field_name: str = ""
    """The input's name/id attribute (helps spot card and password fields)."""
    value: str = Field(default="", max_length=200)
    """Current value; always empty for password and card fields."""
    options: tuple[str, ...] = ()
    checked: bool | None = None
    form: str = ""
    """Id of the form it belongs to ('' = none)."""

    def locator(self) -> dict[str, str]:
        return {"kind": self.kind, "name": self.name}


class PageSnapshot(ContractModel):
    url: str
    title: str = ""
    text: str = ""
    """Visible text, whitespace-collapsed and trimmed."""
    elements: tuple[PageElement, ...] = ()
    frames: tuple[str, ...] = ()
    """URLs of embedded frames (CAPTCHA widgets live in frames)."""


class Browser(Protocol):
    async def open(self, url: str) -> PageSnapshot: ...

    async def snapshot(self) -> PageSnapshot: ...

    async def click(self, ref: str) -> PageSnapshot: ...

    async def fill(self, ref: str, text: str, submit: bool) -> PageSnapshot: ...

    async def select(self, ref: str, option: str) -> PageSnapshot: ...

    async def back(self) -> PageSnapshot: ...

    async def show(self, highlight: dict[str, str] | None) -> None:
        """Hand over: make the browser visible to the user, optionally highlighting the element
        with this locator (refs don't survive a relaunch)."""
        ...

    async def close(self) -> None: ...
