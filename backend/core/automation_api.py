"""HTTP API for background tasks, routines and watchers (same bearer token as api.py)."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from contracts.automation import Routine, TaskInfo, Watcher
from core.api import _rt
from core.schedule import next_run
from core.scheduler import describe_routine

router = APIRouter(prefix="/api")


class RoutineView(BaseModel):
    routine: Routine
    summary: str


class RoutinePatch(BaseModel):
    enabled: bool


class WatcherPatch(BaseModel):
    status: Literal["active", "paused"]


@router.get("/tasks")
async def list_tasks(request: Request) -> list[TaskInfo]:
    return await _rt(request).automation_store.tasks()


@router.post("/tasks/{task_id}/{action}", status_code=204)
async def control_task(
    request: Request, task_id: str, action: Literal["cancel", "resume", "discard"]
) -> None:
    rt = _rt(request)
    bg = rt.background
    done = await {"cancel": bg.cancel, "resume": bg.resume, "discard": bg.discard}[action](task_id)
    if not done:
        raise HTTPException(409, f"can't {action} that task now")
    await rt.store.audit("user", f"task.{action}", {"task_id": task_id})


async def _routine(request: Request, routine_id: str) -> Routine:
    for routine in await _rt(request).automation_store.routines():
        if routine.routine_id == routine_id:
            return routine
    raise HTTPException(404, "no such routine")


@router.get("/routines")
async def list_routines(request: Request) -> list[RoutineView]:
    routines = await _rt(request).automation_store.routines()
    return [RoutineView(routine=r, summary=describe_routine(r)) for r in routines]


@router.patch("/routines/{routine_id}")
async def patch_routine(request: Request, routine_id: str, patch: RoutinePatch) -> RoutineView:
    rt = _rt(request)
    routine = await _routine(request, routine_id)
    update: dict[str, object] = {"enabled": patch.enabled}
    if patch.enabled:  # schedule from now, so turning it on never fires a backlog
        upcoming = next_run(routine.schedule, rt.automations.now())
        if upcoming is None:
            raise HTTPException(409, "that time has passed; make a new reminder instead")
        update["next_run_at"] = upcoming.timestamp()
    routine = routine.model_copy(update=update)
    await rt.automation_store.save_routine(routine)
    await rt.store.audit("user", "routine.update", {"routine_id": routine_id, "enabled": patch.enabled})
    return RoutineView(routine=routine, summary=describe_routine(routine))


@router.post("/routines/{routine_id}/run", status_code=204)
async def run_routine(request: Request, routine_id: str) -> None:
    rt = _rt(request)
    if rt.paused:
        raise HTTPException(409, "paused")
    routine = await _routine(request, routine_id)
    await rt.automations.run_routine(routine, manual=True)
    await rt.store.audit("user", "routine.run", {"routine_id": routine_id})


@router.delete("/routines/{routine_id}", status_code=204)
async def delete_routine(request: Request, routine_id: str) -> None:
    rt = _rt(request)
    if not await rt.automation_store.delete_routine(routine_id):
        raise HTTPException(404, "no such routine")
    await rt.store.audit("user", "routine.delete", {"routine_id": routine_id})


@router.get("/watchers")
async def list_watchers(request: Request) -> list[Watcher]:
    return [w for w, _ in await _rt(request).automation_store.watchers()]


@router.patch("/watchers/{watcher_id}")
async def patch_watcher(request: Request, watcher_id: str, patch: WatcherPatch) -> Watcher:
    rt = _rt(request)
    for watcher, _ in await rt.automation_store.watchers():
        if watcher.watcher_id == watcher_id:
            # Resuming starts fresh: the next check sets a new baseline for 'changes'.
            updated = watcher.model_copy(update={"status": patch.status, "last_detail": ""})
            await rt.automation_store.save_watcher(updated, {} if patch.status == "active" else None)
            await rt.store.audit("user", "watcher.update", {"watcher_id": watcher_id, "status": patch.status})
            return updated
    raise HTTPException(404, "no such watcher")


@router.delete("/watchers/{watcher_id}", status_code=204)
async def delete_watcher(request: Request, watcher_id: str) -> None:
    rt = _rt(request)
    if not await rt.automation_store.delete_watcher(watcher_id):
        raise HTTPException(404, "no such watcher")
    await rt.store.audit("user", "watcher.delete", {"watcher_id": watcher_id})
