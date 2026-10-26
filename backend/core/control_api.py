"""Small controls for the macOS menu bar and the page: pause everything, and read text aloud
in the assistant's own voice. (The page also pauses over its WebSocket.)"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.runtime import Runtime

router = APIRouter(prefix="/api")


def _rt(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


class PauseIn(BaseModel):
    paused: bool


@router.post("/control/pause", status_code=204)
async def control_pause(request: Request, body: PauseIn) -> None:
    """Pause everything (the microphone closes) or resume: used by the macOS menu bar."""
    await _rt(request).set_paused(body.paused)


class SayIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/voice/say")
async def voice_say(request: Request, body: SayIn) -> dict[str, bool]:
    """Read a reply aloud through the assistant's own voice (Piper or the OS voice)."""
    loop = _rt(request).voice_loop
    if loop is None:
        raise HTTPException(409, "voice output isn't running (started with --no-hands-free)")
    return {"spoken": await loop.say(body.text)}
