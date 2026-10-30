"""After the other AI stops: Jarvis checks the work and gets it right, round after round, until
there are no errors and no bugs left.

Each round is a turn of Jarvis's own, in the project's conversation, following through on what the
user asked for: read what the AI said (and the files, for Claude Code), test the result itself
(open it, look at it, try it), answer the AI or get the user's answer, and send precise fixes or a
full review brief (test everything, fix every bug, make the design professional). It ends only
when the AI's report and Jarvis's own checks both find nothing wrong: then the project is done,
the result is opened, and the user is told they can try it. MAX_ROUNDS is a safety stop so a
loop can't run up the bill; the user is told if it's reached. Rounds wait while the user is busy
with Jarvis or has paused it, and the kill switch stops them."""

import asyncio
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
MAX_ROUNDS = 8
SAY_WITHIN_S = 600.0
WAIT_FOR_PAUSE_S = 30.0
"""A check waits this long for a pause in the conversation before it's left to the next look."""
ANNOUNCE = "announce:"
"""How long news waits for a pause in the conversation before it's left on screen only."""


class Host(Protocol):
    """What a review needs from the running assistant (core.runtime.Runtime)."""

    @property
    def store(self) -> "Store": ...

    @property
    def chat(self) -> "ChatService": ...

    def can_follow_up(self) -> bool:
        """Not paused, and not in the middle of something with the user."""
        ...

    def track(self, task: "asyncio.Task[None]") -> None:
        """So the kill switch and pause cancel it like any other turn."""
        ...

    async def broadcast(self, message: ServerMessage) -> None: ...

    async def tell(self, title: str, said: str) -> None: ...


def follow_up(project: Project, round_: int) -> str:
    who = project.built_with or "The AI"
    last = round_ >= MAX_ROUNDS
    ending = (
        f"This is round {MAX_ROUNDS}, the safety limit: send nothing more. Save the project as paused and "
        "tell the user plainly what still isn't right, so they can decide."
        if last
        else "Only when its report shows every test passing, 0 errors and 0 known bugs, and your own checks "
        "found nothing wrong either: send nothing more, save the project as done, open the result for the "
        "user, and tell them it's finished and fully checked so they can try it now, with what was built "
        "and what was fixed, in two or three sentences."
    )
    return f"""(Automatic check, round {round_}: {who} has stopped working on the project "{project.name}".
This is the follow-through the user asked for when they started it, not a new request from them.)
Get it to 0 errors and 0 bugs:
1. Read {who}'s window (focus it, find_elements, read_element), and for Claude Code also the project
   folder's files: did it finish, stop on an error, or ask something?
2. If it asked something, answer from the project if you can. If only the user can answer, ask them in one
   short sentence and stop there; the project waits for them.
3. Every round, check it yourself; never take its report alone: open what was built (its preview in that
   window, the page, its local address or the app), look at it (capture_screen), try its main features with
   the window tools, and read the code for obvious mistakes. Note every error, bug, broken or missing
   feature, and anything that looks unprofessional. If you truly can't see it, ask it for a link or a file
   you can open, and check that before calling anything done.
4. If anything is wrong, unfinished, or it reported errors, send it a precise request that lists exactly
   what to fix. Once it's built, the first time, send a full review brief written like a senior QA
   engineer and product designer: run every test and the build (Claude Code: run them in the project and
   add tests where there are none), test every feature on phone and desktop widths, console errors,
   broken links, edge cases, accessibility and security; fix every bug; raise the design to a
   professional, modern, attractive standard that suits "{project.name}" (a coherent palette, typography
   and spacing, polished components and images, clear hierarchy, hover and focus states, subtle
   motion); and report what it checked, what failed, what it fixed, and what's left.
5. {ending}
Whenever you send it something, save the project as sent again (same window and working sign) so it is
watched. Say in one sentence what you're doing."""


class Reviewer:
    def __init__(self, store: ProjectStore, host: Host) -> None:
        self._store = store
        self._host = host

    async def review(self, project: Project) -> None:
        """One round. Returns at once if it has to wait (the user is busy, or it's paused)."""
        for _ in range(int(WAIT_FOR_PAUSE_S / 0.5)):
            if self._host.can_follow_up():
                break
            await asyncio.sleep(0.5)
        else:
            return  # still due: the watcher offers it again on its next look
        if project.rounds >= MAX_ROUNDS:
            stuck = f"I've checked {project.name} {MAX_ROUNDS} times and it still isn't right; it needs you."
            await self._store.update(project, status="paused", detail=stuck, review_due=False)
            await self._host.tell(project.name, stuck)
            return
        session = await self._host.store.ensure_session(project.session_id or None, project.name)
        rounds = project.rounds + 1
        await self._store.update(
            project,
            rounds=rounds,
            status="reviewing",
            detail=f"Checking, round {rounds}.",
            session_id=session,
        )
        said: list[str] = []

        async def emit(message: ServerMessage) -> None:
            if isinstance(message, ChatDelta):
                said.append(message.text)
            await self._host.broadcast(message)

        send = ChatSend(
            request_id=f"project-{uuid.uuid4().hex[:12]}",
            session_id=session,
            text=follow_up(project, rounds),
            force_model=True,  # never a learned skill or an instant command
        )
        try:
            await self._host.chat.run(send, emit)
        except Exception:
            log.exception("checking project %s failed; it will be tried again", project.name)
            await self._store.update(project, review_due=True, status="answered")
            return
        now = await self._store.named(project.name)
        if now is not None and now.id == project.id and now.status == "reviewing":
            # It neither sent anything nor finished: it asked the user, or stopped short.
            await self._store.update(now, status="answered", detail="Waiting for you.", review_due=False)
        else:
            await self._store.update(project, review_due=False)
        if reply := "".join(said).strip():
            await self._host.tell(project.name, reply)


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
        """Not hearing them or busy with a request: waiting for the wake word, off, or in a live
        conversation with nobody talking (see HandsFree.free)."""
        loop = self._rt.voice_loop
        return loop is None or loop.loop.free()

    def can_follow_up(self) -> bool:
        me = asyncio.current_task()
        turns = any(  # a typed chat, a chat app; not Jarvis's own news being said
            not t.done() and t is not me and not t.get_name().startswith(ANNOUNCE) for t in self._rt._tasks
        )
        return not self._rt.paused and self._idle_voice() and not turns

    def track(self, task: "asyncio.Task[None]") -> None:
        self._rt.track(task)

    async def broadcast(self, message: ServerMessage) -> None:
        await self._rt.broadcast(message)

    async def tell(self, title: str, said: str) -> None:
        """On screen (and as a desktop notification) at once, and aloud as soon as Jarvis is free:
        if they're talking to it right then, it waits for them to finish rather than not say it."""
        await self._rt.notifier.notify(title, said[:500], "task")
        if self._rt.voice_loop is not None:
            self._rt.track(asyncio.create_task(self._say_when_free(said), name=f"{ANNOUNCE}{title}"))

    async def _say_when_free(self, said: str) -> None:
        for _ in range(int(SAY_WITHIN_S / 0.5)):
            loop = self._rt.voice_loop
            if loop is None:
                return
            if loop.loop.free() and not self._rt.paused:
                await loop.say(said)
                return
            await asyncio.sleep(0.5)
        log.info("never free to say it aloud; it's on screen")
