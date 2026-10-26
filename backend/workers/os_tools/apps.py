"""Index of installed apps per OS, and fuzzy name matching."""

import configparser
import difflib
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

CACHE_SECONDS = 60.0
MIN_SCORE = 0.75


@dataclass(frozen=True)
class App:
    name: str
    path: str
    """.desktop file (Linux), .app bundle (macOS), or Start Menu .lnk (Windows)."""


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def score(query: str, name: str) -> float:
    q, n = normalize(query), normalize(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    words = n.split()
    extra = 0.01 * max(0, len(words) - len(q.split()))  # prefer "Google Chrome" over "Chrome Remote Desktop"
    if q in words or (len(q) >= 3 and n.startswith(q)):
        return 0.9 - extra
    if " ".join(words[-len(q.split()) :]) == q:  # "chrome" -> "google chrome"
        return 0.85 - extra
    return difflib.SequenceMatcher(None, q, n).ratio() * 0.85


def best_match(query: str, apps: list[App]) -> tuple[App | None, list[App]]:
    """The single best app, or None plus the tied candidates when it's ambiguous."""
    scored = sorted(((score(query, a.name), a) for a in apps), key=lambda t: -t[0])
    top = [a for s, a in scored if s >= MIN_SCORE and s == scored[0][0]] if scored else []
    unique = list({normalize(a.name): a for a in top}.values())
    if len(unique) == 1:
        return unique[0], []
    return None, unique


# ---- discovery ------------------------------------------------------------------


def _linux_dirs() -> list[Path]:
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    data_dirs = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    roots = [
        data_home,
        *data_dirs,
        "/var/lib/flatpak/exports/share",
        str(Path.home() / ".local/share/flatpak/exports/share"),
    ]
    dirs = [Path(r) / "applications" for r in roots if r]
    dirs.append(Path("/var/lib/snapd/desktop/applications"))
    return dirs


def parse_desktop_file(path: Path) -> App | None:
    parser = configparser.RawConfigParser(interpolation=None, strict=False)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    entry = parser["Desktop Entry"]
    if (
        entry.get("Type") != "Application"
        or entry.get("NoDisplay") == "true"
        or entry.get("Hidden") == "true"
    ):
        return None
    name = entry.get("Name")
    return App(name=name, path=str(path)) if name else None


def _scan_linux() -> list[App]:
    apps: dict[str, App] = {}
    for directory in _linux_dirs():
        if not directory.is_dir():
            continue
        for file in directory.rglob("*.desktop"):
            app = parse_desktop_file(file)
            if app is not None:
                apps.setdefault(file.name, app)  # earlier dirs (user) win
    return list(apps.values())


def _scan_macos() -> list[App]:
    roots = [Path("/Applications"), Path("/System/Applications"), Path.home() / "Applications"]
    apps = []
    for root in roots:
        if not root.is_dir():
            continue
        for bundle in [*root.glob("*.app"), *root.glob("*/*.app")]:
            apps.append(App(name=bundle.stem, path=str(bundle)))
    return apps


def _scan_windows() -> list[App]:
    roots = [os.environ.get("APPDATA", ""), os.environ.get("PROGRAMDATA", "")]
    apps = []
    for root in roots:
        menu = Path(root) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if root and menu.is_dir():
            for link in menu.rglob("*.lnk"):
                if "uninstall" not in link.stem.casefold():
                    apps.append(App(name=link.stem, path=str(link)))
    return apps


class AppIndex:
    def __init__(self) -> None:
        self._apps: list[App] = []
        self._loaded_at = -CACHE_SECONDS

    def apps(self) -> list[App]:
        if time.monotonic() - self._loaded_at > CACHE_SECONDS:
            if sys.platform == "darwin":
                self._apps = _scan_macos()
            elif sys.platform == "win32":
                self._apps = _scan_windows()
            else:
                self._apps = _scan_linux()
            self._loaded_at = time.monotonic()
        return self._apps
