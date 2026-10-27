"""Primitives shared by every contract."""

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
