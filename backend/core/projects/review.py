"""After the other AI stops: Jarvis checks the work and gets it right, in a few rounds.

Each round is a turn of Jarvis's own, in the project's conversation, following through on what the
user asked for: read what the AI said (and the files, for Claude Code), answer it or get the user's
answer, have errors fixed, and once it's built, send a review brief (test everything, fix every
bug, make the design professional). The last round, or a clean report, ends it: the project is
done and the user hears what was built and fixed. Rounds wait while the user is talking to Jarvis
or has paused it."""

import logging
import uuid
from typing import TYPE_CHECKING, Protocol

from contracts.ws import ChatDelta, ChatSend, ServerMessage
from core.projects.store import Project, ProjectStore

if TYPE_CHECKING:
    from core.chat import ChatService
    from core.runtime import Runtime
    from core.store import Store

log = logging.getLogger(__name__)
MAX_ROUNDS = 3


class Host(Protocol):
    """What a review needs from the running assistant (core.runtime.Runtime)."""

    @property
    def store(self) -> "Store": ...

    @property
    def chat(self) -> "ChatService": ...

    def can_follow_up(self) -> bool:
        """Not paused, and not in the middle of a conversation with the user."""
        ...

    async def broadcast(self, message: ServerMessage) -> None: ...

    async def tell(self, title: str, said: str) -> None: ...


def follow_up(project: Project, round_: int) -> str:
    who = project.built_with or "The AI"
    ending = (
        "This is the last round: send nothing more. Save the project as done"
        if round_ >= MAX_ROUNDS
        else "If its last report shows everything checked and fixed with nothing left, send nothing more: "
        "save the project as done"
    )
    return f"""(Automatic check, round {round_} of {MAX_ROUNDS}: {who} has stopped working on the project
"{project.name}". This is the follow-through the user asked for when they started it, not a new request.)
Check the work and get it right:
1. Read {who}'s window (focus it, find_elements, read_element), and for Claude Code also the project
   folder's files: did it finish, stop on an error, or ask something?
2. If it asked something, answer it from the project if you can. If only the user can answer, ask them in
   one short sentence and stop there.
3. If anything is unfinished or broken, or it reported errors, send it a precise request to fix exactly that.
4. If it says it's finished, send it a review brief written like a senior QA engineer and product designer:
   run and test everything against the brief (every feature, phone and desktop widths, console errors,
   broken links, edge cases, accessibility, security); fix every bug found; then raise the design to a
   professional, modern, attractive standard that suits "{project.name}" (a coherent palette, typography
   and spacing, polished components and images, clear hierarchy, hover and focus states, subtle motion);
   and report what it checked, fixed and changed.
5. {ending}
   and tell the user in a sentence or two what was built and what was fixed.
Whenever you send it something, save the project as sent again (same window and working sign), so it is
watched. Say in one sentence what you're doing."""


class Reviewer:
    def __init__(self, store: ProjectStore, host: Host) -> None:
        self._store = store
        self._host = host

    async def review(self, project: Project) -> bool:
        """One round. False when it has to wait (the user is busy with Jarvis, or it's paused)."""
        if not self._host.can_follow_up():
            return False
        session = await self._host.store.ensure_session(project.session_id or None, project.name)
        rounds = project.rounds + 1
        await self._store.update(
            project, rounds=rounds, review_due=False, status="reviewing", detail=f"Checking, round {rounds}.",
            session_id=session,
        )  # fmt: skip
        said: list[str] = []

        async def emit(message: ServerMessage) -> None:
            if isinstance(message, ChatDelta):
                said.append(message.text)
            await self._host.broadcast(message)

        send = ChatSend(
            request_id=f"project-{uuid.uuid4().hex[:12]}",
            session_id=project.session_id,
            text=follow_up(project, project.rounds),
        )
        try:
            await self._host.chat.run(send, emit)
        except Exception:
            log.exception("checking project %s failed", project.name)
            return True
        if reply := "".join(said).strip():
            await self._host.tell(project.name, reply)
        return True


class RuntimeHost:
    """The running assistant as a review host. Attributes are read when needed: the chat, the
    notifier and the voice loop are made after the projects are."""

    def __init__(self, runtime: "Runtime") -> None:
        self._rt = runtime

    @property
    def store(self) -> "Store":
        return self._rt.store

    @property
    def chat(self) -> "ChatService":
        return self._rt.chat

    def _idle_voice(self) -> bool:
        loop = self._rt.voice_loop
        return loop is None or loop.loop.state == "idle"

    def can_follow_up(self) -> bool:
        return not self._rt.paused and self._idle_voice()

    async def broadcast(self, message: ServerMessage) -> None:
        await self._rt.broadcast(message)

    async def tell(self, title: str, said: str) -> None:
        """On screen (and as a desktop notification), and aloud when Jarvis isn't mid-conversation."""
        await self._rt.notifier.notify(title, said[:500], "task")
        if self._rt.voice_loop is not None and self._idle_voice():
            await self._rt.voice_loop.say(said)
