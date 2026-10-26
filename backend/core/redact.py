"""Secret redaction for logs and the audit log."""

import logging
import re

_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-~+/=]{8,}"),
    re.compile(r"(token\.)[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(#token=)[A-Za-z0-9_\-]{16,}"),
]
MASK = "[REDACTED]"


class Redactor:
    def __init__(self) -> None:
        self._literals: set[str] = set()

    def add_secret(self, value: str | None) -> None:
        if value and len(value) >= 8:
            self._literals.add(value)

    def __call__(self, text: str) -> str:
        for literal in self._literals:
            text = text.replace(literal, MASK)
        for pattern in _PATTERNS:
            text = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + MASK, text)
        return text


class RedactingFilter(logging.Filter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redact = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.getMessage())
        record.args = None
        return True


def install(redactor: Redactor) -> None:
    """Attach redaction to every handler of the root and uvicorn loggers."""
    log_filter = RedactingFilter(redactor)
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.addFilter(log_filter)
        for handler in logger.handlers:
            handler.addFilter(log_filter)
