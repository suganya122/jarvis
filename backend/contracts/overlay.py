"""Voice overlay: a small always-on-top window that shows what a voice request is doing.

Core runs it as a separate process (workers/overlay) and talks over stdin/stdout, one
JSON object per line. The overlay only displays state and sends back the user's button
presses; core decides everything. The microphone stays with the chat window.
"""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from contracts.common import ContractModel, RiskLevel

OverlayPhase = Literal["listening", "thinking", "acting", "done", "error", "needs_chat"]


class OverlayApproval(ContractModel):
    approval_id: str
    tool: str
    risk: RiskLevel
    summary: str


MAX_ITEMS = 10


class OverlayItem(ContractModel):
    """One line of the conversation in the box: what you said, a step, the answer, a problem."""

    kind: Literal["you", "step", "answer", "error"]
    text: str = Field(max_length=800)
    done: bool = True
    """False while a step is still running."""
    ref: str = ""
    """The tool call a step belongs to (so its "done" can find it)."""


class OverlayView(ContractModel):
    phase: OverlayPhase
    partial: str = Field(default="", max_length=500)
    """Live transcript while listening, then what was heard."""
    status: str = Field(default="", max_length=200)
    """One line: the current step, or the start of the reply."""
    approval: OverlayApproval | None = None
    target: str = Field(default="", max_length=200)
    """What "this"/"here" means for this request, e.g. 'Screen 2 · Chrome · Inbox'."""
    items: list[OverlayItem] = Field(default_factory=list, max_length=MAX_ITEMS)
    """The conversation so far (newest last). It stays in the box until the next one starts."""
    auto_send: bool = False
    """Listening ends by itself after a pause (so the hint can say so)."""


# ---- core -> overlay ---------------------------------------------------------------


class OverlayShow(ContractModel):
    type: Literal["overlay.show"] = "overlay.show"
    view: OverlayView


class OverlayHide(ContractModel):
    type: Literal["overlay.hide"] = "overlay.hide"


class OverlayLevel(ContractModel):
    """How loud the microphone is right now (0-1), a few times a second while listening,
    so the orb can react to the voice. Only the level: no audio reaches the overlay."""

    type: Literal["overlay.level"] = "overlay.level"
    level: float = Field(ge=0.0, le=1.0)


class OverlayTask(ContractModel):
    """A background job, as a chip in the corner: big jobs run there while you keep working."""

    task_id: str
    title: str = Field(max_length=80)
    state: Literal["running", "waiting", "done", "failed"]
    detail: str = Field(default="", max_length=160)


class OverlayTasks(ContractModel):
    type: Literal["overlay.tasks"] = "overlay.tasks"
    tasks: list[OverlayTask] = Field(default_factory=list, max_length=4)


ToOverlay = Annotated[OverlayShow | OverlayHide | OverlayLevel | OverlayTasks, Field(discriminator="type")]
to_overlay_adapter: TypeAdapter[ToOverlay] = TypeAdapter(ToOverlay)


# ---- overlay -> core ---------------------------------------------------------------


class OverlayReady(ContractModel):
    type: Literal["overlay.ready"] = "overlay.ready"
    contract_version: str
    available: bool
    detail: str = ""
    """Why it can't show (no display, no Tk), or which backend it uses."""


class OverlayStop(ContractModel):
    """The Stop button: stop listening and cancel whatever is running."""

    type: Literal["overlay.stop"] = "overlay.stop"


class OverlayDecide(ContractModel):
    type: Literal["overlay.decide"] = "overlay.decide"
    approval_id: str
    approved: bool


class OverlayDismiss(ContractModel):
    """Closed by the user (Escape with nothing running): hide until the next request."""

    type: Literal["overlay.dismiss"] = "overlay.dismiss"


FromOverlay = Annotated[
    OverlayReady | OverlayStop | OverlayDecide | OverlayDismiss, Field(discriminator="type")
]
from_overlay_adapter: TypeAdapter[FromOverlay] = TypeAdapter(FromOverlay)
