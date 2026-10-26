"""Background jobs as chips in the overlay's corner: what each one says, saying out loud when a
job you watched finish is done, and tidying finished ones away."""

import asyncio

from contracts.automation import TaskInfo
from contracts.overlay import OverlayTask
from core.overlay_tasks import TaskChips, chip, title


def task(status: str, **kw: object) -> TaskInfo:
    prompt = "find the three best laptops for college students under $1000"
    base = {"task_id": "t1", "session_id": None, "prompt": prompt, "origin": "user", "status": status,
            "created_at": 0.0}  # fmt: skip
    return TaskInfo.model_validate({**base, **kw})


def test_a_chip_says_what_the_job_is_doing() -> None:
    assert (
        title("find the three best laptops for college students under $1000")
        == "find the three best laptops for college…"
    )
    assert chip(task("running", current_tool="browser")).detail == "Working on it: browser"
    assert chip(task("waiting_approval")).state == "waiting"
    done = chip(task("done", preview="Lenovo, ASUS, MacBook Air.\nSaved them to a Google Doc."))
    assert (done.state, done.detail) == ("done", "Saved them to a Google Doc.")
    assert chip(task("failed", error="The page wouldn't load.")).detail == "The page wouldn't load."
    assert chip(task("cancelled")).state == "failed"


def test_chips_appear_finish_out_loud_and_tidy_away() -> None:
    async def run() -> tuple[list[list[OverlayTask]], list[str]]:
        shown: list[list[OverlayTask]] = []
        said: list[str] = []

        async def show(tasks: list[OverlayTask]) -> None:
            shown.append(tasks)

        async def announce(text: str) -> None:
            said.append(text)

        chips = TaskChips(show, announce, keep_s=0.1)
        await chips.update(task("running"))
        await chips.update(task("done", preview="Done."))
        await chips.update(task("done", preview="Done."))  # the same news twice: said once
        await asyncio.sleep(0.2)
        return shown, said

    shown, said = asyncio.run(run())
    assert [t[0].state for t in shown[:3]] == ["running", "done", "done"]
    assert shown[-1] == []  # tidied away after a while
    assert said == ["Your background task, find the three best laptops for college, is done."]


def test_a_job_that_was_never_seen_running_is_not_announced() -> None:
    async def run() -> list[str]:
        said: list[str] = []

        async def show(_tasks: list[OverlayTask]) -> None:
            return None

        async def announce(text: str) -> None:
            said.append(text)

        await TaskChips(show, announce, keep_s=0.05).update(task("done"))  # e.g. after a restart
        await asyncio.sleep(0.1)
        return said

    assert asyncio.run(run()) == []
