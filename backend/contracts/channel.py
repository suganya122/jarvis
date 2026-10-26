"""Channel contract: chat apps (Telegram, …) as plugin processes.

Core talks to a channel plugin over stdin/stdout, one JSON object per line (like workers).
The plugin only moves text: it never decides what runs. Core checks who is talking
(paired accounts only), runs the turn under the remote-request rules, and asks for
approvals through the channel.

`Channel` is the core-side adapter interface; `ChannelHost` implements it for plugin
processes and tests implement it in memory.
"""

from collections.abc import AsyncIterator
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import Field, TypeAdapter

from contracts.common import ContractModel, RiskLevel

MAX_CHANNEL_TEXT = 8_000

# ---- the adapter interface (core side) ------------------------------------------------


class InboundMessage(ContractModel):
    channel: str
    conversation_id: str
    user_id: str
    user_name: str = ""
    text: str = Field(max_length=MAX_CHANNEL_TEXT)


class OutboundMessage(ContractModel):
    channel: str
    conversation_id: str
    text: str


class ApprovalRequest(ContractModel):
    approval_id: str
    conversation_id: str
    tool: str
    risk: RiskLevel
    summary: str
    reason: str


class ApprovalDecision(ContractModel):
    approval_id: str
    user_id: str
    approved: bool


ChannelState = Literal["starting", "connected", "error", "stopped"]


@runtime_checkable
class Channel(Protocol):
    name: str

    def receive(self) -> AsyncIterator[InboundMessage | ApprovalDecision]: ...

    async def send(self, message: OutboundMessage) -> None: ...

    async def request_approval(self, request: ApprovalRequest) -> None:
        """Show Approve/Deny; the answer arrives later from receive() as an ApprovalDecision."""
        ...

    async def close_approval(self, approval_id: str, conversation_id: str, outcome: str) -> None: ...

    async def typing(self, conversation_id: str) -> None: ...


# ---- wire protocol: core -> plugin ----------------------------------------------------


class ChannelConfigure(ContractModel):
    type: Literal["channel.configure"] = "channel.configure"
    options: dict[str, str] = {}
    secrets: dict[str, str] = {}
    """Only the secrets the plugin's manifest declares, read from the OS keychain."""


class ChannelSend(ContractModel):
    type: Literal["channel.send"] = "channel.send"
    conversation_id: str
    text: str


class ChannelAskApproval(ContractModel):
    type: Literal["channel.approval"] = "channel.approval"
    request: ApprovalRequest


class ChannelCloseApproval(ContractModel):
    type: Literal["channel.approval_closed"] = "channel.approval_closed"
    approval_id: str
    conversation_id: str
    outcome: str


class ChannelTyping(ContractModel):
    type: Literal["channel.typing"] = "channel.typing"
    conversation_id: str


ToChannel = Annotated[
    ChannelConfigure | ChannelSend | ChannelAskApproval | ChannelCloseApproval | ChannelTyping,
    Field(discriminator="type"),
]
to_channel_adapter: TypeAdapter[ToChannel] = TypeAdapter(ToChannel)


# ---- wire protocol: plugin -> core ----------------------------------------------------


class ChannelReady(ContractModel):
    type: Literal["channel.ready"] = "channel.ready"
    contract_version: str


class ChannelInbound(ContractModel):
    type: Literal["channel.inbound"] = "channel.inbound"
    message: InboundMessage


class ChannelDecision(ContractModel):
    type: Literal["channel.decision"] = "channel.decision"
    decision: ApprovalDecision


class ChannelStatus(ContractModel):
    type: Literal["channel.status"] = "channel.status"
    state: ChannelState
    detail: str = ""


FromChannel = Annotated[
    ChannelReady | ChannelInbound | ChannelDecision | ChannelStatus, Field(discriminator="type")
]
from_channel_adapter: TypeAdapter[FromChannel] = TypeAdapter(FromChannel)
