"""Confines every path to the allowed folders and keeps protected locations out."""

import os
import sys
from collections.abc import Iterable
from pathlib import Path

# macOS and Windows filesystems are usually case-insensitive: compare case-folded there.
_FOLD = sys.platform in ("darwin", "win32")


class PathDenied(Exception):
    pass


def _key(path: Path) -> str:
    text = os.path.normcase(str(path))
    return text.casefold() if _FOLD else text


def _inside(path: Path, root: Path) -> bool:
    p, r = _key(path), _key(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def _expand(raw: str) -> Path:
    return Path(os.path.expanduser(raw)).resolve()


class PathGuard:
    def __init__(self, roots: Iterable[str], denied: Iterable[str]) -> None:
        self.roots = [_expand(r) for r in roots]
        self.denied = [_expand(d) for d in denied]
        self._home = Path.home().resolve()

    def resolve(self, raw: str) -> Path:
        """Absolute, symlink-free path that is allowed, or PathDenied."""
        if not raw or "\x00" in raw:
            raise PathDenied("empty or invalid path")
        candidate = Path(os.path.expanduser(raw))
        if not candidate.is_absolute():
            if not self.roots:
                raise PathDenied("no folders are allowed in Settings")
            candidate = self.roots[0] / candidate
        path = candidate.resolve()  # follows symlinks, so links can't escape a root
        if not any(_inside(path, root) for root in self.roots):
            raise PathDenied(f"{self.display(path)} is outside the folders allowed in Settings")
        if any(_inside(path, denied) for denied in self.denied):
            raise PathDenied(f"{self.display(path)} is a protected location")
        return path

    def display(self, path: Path) -> str:
        if _inside(path, self._home):
            rest = str(path)[len(str(self._home)) :].lstrip(os.sep)
            return "~" + (os.sep + rest if rest else "")
        return str(path)
