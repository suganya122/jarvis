"""Primitives shared by every contract."""

import re
import uuid
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict

CONTRACT_VERSION: Final = "1.0"
"""Bumped on any breaking change to a contract. Plugins declare the version they target."""


class ContractModel(BaseModel):
    """Base for all contract messages: immutable and strict about unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RiskLevel(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    EXTERNAL_SEND = "external_send"
    PURCHASE = "purchase"

    @property
    def always_requires_approval(self) -> bool:
        """Deleting and paying always ask, whatever the settings. Sending asks unless the user has
        chosen full automation (tools.full_automation): see core.policy.decide."""
        return self in {RiskLevel.DESTRUCTIVE, RiskLevel.PURCHASE}


_NUMBER = r"(\d[\d,]*(?:\.\d+)?)"
MONEY = re.compile(
    rf"(?:[₹$€£¥₨]|\b(?:rs|inr|usd|eur|gbp|aed|sgd)\.?)\s*{_NUMBER}|{_NUMBER}\s*(?:[₹$€£¥₨]|(?:rupees|dollars)\b)",
    re.I,
)
"""An amount of money, e.g. '₹1,200', 'Rs. 120', '450 rupees'."""


def amounts(text: str) -> list[float]:
    """Every amount of money in the text: 'Pay ₹134 (₹77 + fees)' -> [134.0, 77.0]."""
    return [float((m.group(1) or m.group(2)).replace(",", "")) for m in MONEY.finditer(text)]


class Capability(StrEnum):
    SCREEN_READ = "screen.read"
    INPUT_CONTROL = "input.control"
    SHELL_RUN = "shell.run"
    FS_READ = "fs.read"
    FS_WRITE = "fs.write"
    NET_FETCH = "net.fetch"
    SEND_EXTERNAL = "send.external"
    PURCHASE = "purchase"
    SCHEDULE = "schedule"
    """Create routines and watchers that act later, unattended."""
    MEMORY = "memory"
    """Save or delete facts that are given to the model in later chats."""
    BROWSER = "browser"
    """Browse websites in the assistant's browser: open pages, click, fill in forms."""


class Trust(StrEnum):
    """Where content came from. Untrusted content can never grant permissions."""

    USER = "user"
    UNTRUSTED = "untrusted"


def new_trace_id() -> str:
    return uuid.uuid4().hex
