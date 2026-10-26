"""Stable targets for windows and controls.

The model works with short session ids (w1, e7). Learned skills can't: ids change every
session. So every target can also be given as a locator (app, role, name), and every
action reports its target as a locator for the skill learner.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class WindowLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: str
    title: str = ""
    """Optional substring of the window title."""


class ElementLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: WindowLocator
    role: str
    name: str


WindowRef = str | WindowLocator
ElementRef = str | ElementLocator


class LocateError(Exception):
    pass


def window_meta(app: str, title: str) -> dict[str, Any]:
    return {"app": app, "title": title}


def element_meta(role: str, name: str, window: dict[str, Any]) -> dict[str, Any]:
    return {"role": role, "name": name, "window": {"app": window.get("app", "")}}


def find_window(windows: list[Any], loc: WindowLocator) -> Any:
    """Pick the best window for a locator: same app, title match if given, active first."""
    app = loc.app.casefold()
    matches = [w for w in windows if w.app.casefold() == app]
    if loc.title:
        titled = [w for w in matches if loc.title.casefold() in w.title.casefold()]
        matches = titled or matches
    if not matches:
        raise LocateError(f"no open window of '{loc.app}'")
    return sorted(matches, key=lambda w: not w.focused)[0]


def find_element(elements: list[Any], loc: ElementLocator) -> Any:
    name = loc.name.casefold()
    for e in elements:
        if e.role == loc.role and e.name.casefold() == name:
            return e
    raise LocateError(f"couldn't find {loc.role} '{loc.name}' in {loc.window.app}")
