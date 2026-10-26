"""Small shared types for the chat pipeline."""

from collections.abc import Awaitable, Callable

from contracts.ws import ServerMessage

Emit = Callable[[ServerMessage], Awaitable[None]]
