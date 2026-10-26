"""Facts the user asked the assistant to remember."""

from typing import Literal

from pydantic import Field

from contracts.common import ContractModel

MAX_MEMORY_CHARS = 500


class Memory(ContractModel):
    memory_id: str
    text: str = Field(min_length=1, max_length=MAX_MEMORY_CHARS)
    source: Literal["user", "assistant"]
    """user: typed "remember …" or added in the Memory panel. assistant: the model saved it (approved)."""
    created_at: float
    updated_at: float
