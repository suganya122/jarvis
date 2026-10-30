"""Projects built with another AI: kept and in context until done, watched while that AI works,
and the user told when it answers. Plus turning a permission on (every step the system allows)."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from contracts.common import RiskLevel, Trust
from contracts.tools import ToolAssessment, ToolContext, ToolInfo, ToolResult
from contracts.ws import ChatDelta, ChatSend
from core import db
from core.projects import Projects
from core.projects.review import MAX_ROUNDS
from core.projects.watch import NEVER_SEEN_AFTER, find_window
from core.toolbox import ToolRegistry
from workers.hands import setup_tool

CTX = ToolContext(trace_id="t", session_id="s", trust=Trust.USER)
LISTING = "w1  Finder — Downloads\nw2  Google Chrome — Bakery website - Claude  (active)"


class Scripted:
    """A stand-in tool whose outputs are given in turn."""

    def __init__(self, name: str, outputs: list[str]) -> None:
        self.info = ToolInfo(name=name, description="", input_schema={}, risk=RiskLevel.READ,
                             capabilities=(), source="test")  # fmt: skip
        self.outputs = outputs
        self.calls: list[dict[str, Any]] = []

    async def assess(self, args: dict[str, Any]) -> ToolAssessment:
        return ToolAssessment(risk=RiskLevel.READ, summary="")

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.calls.append(args)
        out = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        return ToolResult(ok=True, output=out)


class FakeHost:
    """The running assistant, as far as projects see it: a chat that records its turns."""

    def __init__(self) -> None:
        self.told: list[tuple[str, str]] = []
        self.sends: list[ChatSend] = []
        self.free = True
        self.reply = "Claude built it; I've asked it to test everything and polish the design."

        host = self

        class Store:
            async def ensure_session(self, session_id: str | None, first_text: str) -> str:
                return session_id or "project-session"

        class Chat:
            async def run(self, send: ChatSend, emit: Any, *_: Any, **__: Any) -> None:
                host.sends.append(send)
                await emit(ChatDelta(request_id=send.request_id, text=host.reply))

        self.store, self.chat = Store(), Chat()

    def can_follow_up(self) -> bool:
        return self.free

    async def broadcast(self, message: Any) -> None:
        return None

    async def tell(self, title: str, said: str) -> None:
        self.told.append((title, said))


class Rig:
    def __init__(self, conn: Any, windows: list[str], controls: list[str]) -> None:
        self.registry = ToolRegistry()
        self.windows, self.controls = Scripted("list_windows", windows), Scripted("find_elements", controls)
        self.registry.register(self.windows)
        self.registry.register(self.controls)
        self.host = FakeHost()
        self.told = self.host.told
        self.projects = Projects(conn, self.registry, self.host)  # type: ignore[arg-type]

    async def save(self, **args: Any) -> ToolResult:
        tool = self.registry.get("save_project")
        assert tool is not None
        return await tool.run(args, CTX)

    async def settle(self) -> None:
        """Let a check that was started finish."""
        for task in list(self.projects.watcher._reviewing.values()):
            await task


@pytest.fixture
async def conn(tmp_path: Path) -> AsyncIterator[Any]:
    connection = await db.connect(tmp_path / "p.sqlite3")
    yield connection
    await connection.close()


async def test_a_project_is_understood_step_by_step_and_never_forgotten(conn: Any) -> None:
    rig = Rig(conn, [LISTING], ["No matching controls."])
    rig.projects.watcher.start = lambda: None  # type: ignore[method-assign]  # watched below, by hand
    await rig.save(name="Bakery website", idea="a site for my mom's bakery; order cakes on WhatsApp")
    await rig.save(name="bakery website", learned=["customers in Chennai, mostly on phones"])
    await rig.save(
        name="Bakery",
        learned=["orders only on WhatsApp, no online payment", "orders only on WhatsApp, no online payment"],
    )

    async def unrelated(request: str) -> str:
        return "What you know about the user's world: …"

    context = await rig.projects.around(unrelated)("what's the weather in Chennai")  # another request
    assert context.startswith("What you know") and "Projects the user is working on" in context
    assert "Bakery website [understanding]" in context and "mostly on phones" in context
    assert context.count("no online payment") == 1  # said twice, kept once

    refused = await rig.save(name="Bakery website", status="sent", built_with="Claude")
    assert not refused.ok and "which window" in refused.output
    await rig.save(name="Bakery website", brief="Build a website…", status="sent", built_with="Claude",
                   watch_window="Claude", working_marker="Stop")  # fmt: skip
    status = await rig.registry.get("project_status").run({"name": "bakery"}, CTX)  # type: ignore[union-attr]
    assert "brief:\nBuild a website…" in status.output and "[sent with Claude]" in status.output

    again = Projects(conn, ToolRegistry(), FakeHost())  # type: ignore[arg-type]
    assert "Bakery website [sent with Claude]" in await again.context()  # after a restart too

    await rig.save(name="Bakery website", status="done")
    assert await rig.projects.context() == ""  # done: no longer carried along


async def test_when_the_other_ai_stops_jarvis_checks_the_work_in_rounds(conn: Any) -> None:
    working, idle = "e4  button 'Stop response'", "No matching controls."
    rig = Rig(conn, [LISTING], [working, working, idle, idle])
    rig.projects.watcher.start = lambda: None  # type: ignore[method-assign]
    await rig.save(name="Bakery website", idea="site", status="sent", built_with="Claude",
                   watch_window="Claude", working_marker="Stop")  # fmt: skip
    await rig.projects.watcher.poll()
    assert rig.controls.calls[0] == {"window_id": "w2", "query": "Stop", "limit": 3}  # the Claude window
    assert "[working with Claude]" in await rig.projects.context()
    rig.host.free = False  # the user is talking to Jarvis: the check waits
    for _ in range(3):
        await rig.projects.watcher.poll()
    await rig.settle()
    assert rig.told == [
        ("Bakery website", "Claude has stopped on Bakery website. I'm checking the work now.")
    ]
    assert rig.host.sends == []
    rig.host.free = True
    await rig.projects.watcher.poll()
    await rig.settle()
    (send,) = rig.host.sends
    assert send.session_id == "project-session"
    assert "round 1 of 3" in send.text and "not a new request" in send.text
    assert "fix every bug" in send.text and "professional, modern, attractive" in send.text
    assert rig.told[-1] == ("Bakery website", rig.host.reply)  # what it did, said to the user
    status = await rig.registry.get("project_status").run({"name": "Bakery"}, CTX)  # type: ignore[union-attr]
    assert "[reviewing with Claude]" in status.output
    await rig.projects.watcher.poll()
    await rig.settle()
    assert len(rig.host.sends) == 1  # one check per stop


async def test_after_the_last_round_it_only_tells_the_user(conn: Any) -> None:
    rig = Rig(conn, [LISTING], ["e4  button 'Stop'", "No matching controls."])
    rig.projects.watcher.start = lambda: None  # type: ignore[method-assign]
    await rig.save(
        name="Bakery website",
        status="sent",
        built_with="Claude",
        watch_window="Claude",
        working_marker="Stop",
    )
    project = await rig.projects.store.find("Bakery website")
    assert project is not None
    project.rounds = MAX_ROUNDS
    await rig.projects.store.save(project)
    for _ in range(3):
        await rig.projects.watcher.poll()
    await rig.settle()
    assert rig.host.sends == [] and rig.told == [
        ("Bakery website", "Claude has stopped on Bakery website: done, or asking you something.")
    ]


async def test_a_window_that_closes_or_never_shows_work_is_reported_not_watched_forever(conn: Any) -> None:
    rig = Rig(conn, ["w1  Finder — Downloads"], ["No matching controls."])
    rig.projects.watcher.start = lambda: None  # type: ignore[method-assign]
    await rig.save(
        name="Study plan", status="sent", built_with="ChatGPT", watch_window="ChatGPT", working_marker="Stop"
    )
    for _ in range(3):
        await rig.projects.watcher.poll()
    assert rig.told == [("Study plan", "I can't see the ChatGPT window any more.")]

    quick = Rig(conn, ["w5  Google Chrome — ChatGPT"], ["No matching controls."])
    quick.projects.watcher.start = lambda: None  # type: ignore[method-assign]
    await quick.save(
        name="Poem", status="sent", built_with="ChatGPT", watch_window="ChatGPT", working_marker="Stop"
    )
    for _ in range(NEVER_SEEN_AFTER):
        await quick.projects.watcher.poll()
    await quick.settle()
    assert ("Poem", "ChatGPT seems to have answered on Poem. I'm checking the work now.") in quick.told


def test_the_window_is_found_by_part_of_its_title() -> None:
    assert find_window(LISTING, "claude") == "w2"
    assert find_window(LISTING, "Teams") is None
    assert find_window(LISTING, "") is None


# ---- turning a permission on ---------------------------------------------------------------


def test_turning_accessibility_on_does_every_step_macos_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr(setup_tool, "_run", lambda argv: ran.append(argv) or True)
    asked: list[bool] = []

    class Hands:
        def trusted(self) -> bool:
            return False

        def ask_trust(self) -> None:
            asked.append(True)

    class Env:
        hands = Hands()

    result = setup_tool._mac_setup(Env(), "accessibility")  # type: ignore[arg-type]
    assert ran[0] == ["tccutil", "reset", "Accessibility", "local.assistant.jarvis"]  # the stale entry
    assert asked == [True]  # macOS's own prompt
    assert ran[1][0] == "open" and ran[1][1].endswith("Privacy_Accessibility")  # the exact page
    assert result.ok and "The last step is yours" in result.output

    Hands.trusted = lambda self: True  # type: ignore[method-assign]
    ran.clear()
    assert "already on" in setup_tool._mac_setup(Env(), "accessibility").output and ran == []  # type: ignore[arg-type]
    shown = setup_tool._mac_setup(Env(), "screen_recording")  # type: ignore[arg-type]
    assert ran == [["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"]]
    assert "Screen Recording" in shown.output


def test_the_permission_tool_never_runs_from_the_background() -> None:
    from core.policy import FOCUS_STEALING_TOOLS

    assert {"setup_permission", "paste_text"} <= FOCUS_STEALING_TOOLS
    assert asyncio.iscoroutinefunction(setup_tool.SetupPermission.run)
