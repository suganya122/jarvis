"""HTTP API for memory and your data: export, delete everything, start at login."""

import json
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from contracts import APP_ID
from contracts.memory import MAX_MEMORY_CHARS, Memory
from core.api import _rt
from core.autostart import Autostart
from core.data_control import export_all

router = APIRouter(prefix="/api")


class MemoryIn(BaseModel):
    text: str = Field(min_length=2, max_length=MAX_MEMORY_CHARS)


class DeleteAllIn(BaseModel):
    confirm: Literal["DELETE"]
    """Typed by the user; a stray request can't wipe anything."""


class AutostartState(BaseModel):
    supported: bool
    enabled: bool


class AutostartIn(BaseModel):
    enabled: bool


@router.get("/memories")
async def list_memories(request: Request) -> list[Memory]:
    return await _rt(request).memory.all()


@router.post("/memories", status_code=201)
async def add_memory(request: Request, body: MemoryIn) -> Memory:
    rt = _rt(request)
    memory = await rt.memory.add(body.text, "user")
    await rt.store.audit("user", "memory.add", {"memory_id": memory.memory_id})
    return memory


@router.put("/memories/{memory_id}")
async def edit_memory(request: Request, memory_id: str, body: MemoryIn) -> Memory:
    rt = _rt(request)
    memory = await rt.memory.update(memory_id, body.text)
    if memory is None:
        raise HTTPException(404, "no such memory")
    await rt.store.audit("user", "memory.update", {"memory_id": memory_id})
    return memory


@router.delete("/memories/{memory_id}", status_code=204)
async def delete_memory(request: Request, memory_id: str) -> None:
    rt = _rt(request)
    if not await rt.memory.delete(memory_id):
        raise HTTPException(404, "no such memory")
    await rt.store.audit("user", "memory.delete", {"memory_id": memory_id})


@router.get("/export")
async def export(request: Request) -> Response:
    rt = _rt(request)
    await rt.store.audit("user", "data.export", {})
    body = json.dumps(await export_all(rt.store.conn), ensure_ascii=False, indent=1, default=str)
    name = f"{APP_ID}-export-{time.strftime('%Y%m%d-%H%M%S')}.json"
    return Response(
        body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )


@router.post("/data/delete-all", status_code=202)
async def delete_all(request: Request, body: DeleteAllIn) -> dict[str, bool]:
    """Stops everything, then the daemon exits and deletes its data on the way out."""
    rt = _rt(request)
    await rt.stop_all(reason="all data is being deleted")
    await rt.store.audit("user", "data.delete_all", {})
    request.app.state.wipe_requested = True
    exit_now = request.app.state.request_exit
    if exit_now is not None:
        exit_now()
    return {"stopping": True}


def _autostart(request: Request) -> Autostart:
    autostart: Autostart = request.app.state.autostart
    return autostart


@router.get("/autostart")
async def get_autostart(request: Request) -> AutostartState:
    a = _autostart(request)
    return AutostartState(supported=a.supported, enabled=a.supported and a.enabled())


@router.put("/autostart")
async def set_autostart(request: Request, body: AutostartIn) -> AutostartState:
    a = _autostart(request)
    if not a.supported:
        raise HTTPException(409, "starting at login isn't supported on this system")
    if body.enabled:
        a.enable()
    else:
        a.disable()
    await _rt(request).store.audit("user", "autostart.update", {"enabled": body.enabled})
    return AutostartState(supported=True, enabled=a.enabled())
