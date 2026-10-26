"""Errors the hands backends raise, importable on every OS (the AT-SPI backend itself is Linux-only)."""


class AtspiError(Exception):
    pass
