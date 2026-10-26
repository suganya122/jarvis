"""HTTP API. Every route except /api/health requires the bearer token (see security.py)."""

import sys
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from contracts import APP_NAME, APP_VERSION, CONTRACT_VERSION
from contracts.common import Trust
from contracts.model import ModelTier, ProviderStatus
from contracts.skills import Skill
from contracts.tools import ToolContext, ToolInfo
from contracts.ws import VoiceCommand
from core.config import Settings
from core.model_files import BY_ID, CATALOG, ModelSpec, recommended_model
from core.runtime import Runtime
from core.secret_store import KeychainUnavailable, KeySource
from core.spend import SpendStatus
from core.store import AuditEntry, SessionInfo, StoredMessage
from skills.matcher import literal_chars
from skills.templates import slots_in

router = APIRouter(prefix="/api")
BACKEND_DIR = Path(__file__).resolve().parents[1]


def _rt(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


class Health(BaseModel):
    ok: Literal[True] = True


class Info(BaseModel):
    app_name: str
    app_version: str
    contract_version: str


class TierInfo(BaseModel):
    tier: ModelTier
    model: str


class Status(BaseModel):
    provider: ProviderStatus
    os_tools: bool
    hands: bool
    paused: bool
    api_key_source: KeySource
    keychain_error: str | None
    default_tier: ModelTier
    tiers: list[TierInfo]


class ApiKeyIn(BaseModel):
    api_key: str = Field(min_length=20, max_length=512, pattern=r"^\S+$")


@router.get("/health")
async def health() -> Health:
    return Health()


@router.get("/info")
async def info() -> Info:
    return Info(app_name=APP_NAME, app_version=APP_VERSION, contract_version=CONTRACT_VERSION)


@router.get("/status")
async def status(request: Request) -> Status:
    rt = _rt(request)
    tiers = rt.settings.anthropic.tiers
    return Status(
        provider=await rt.provider().status(),
        os_tools=rt.worker_alive("os_tools"),
        hands=rt.worker_alive("hands"),
        paused=rt.paused,
        api_key_source=rt.secrets.anthropic_key_source(),
        keychain_error=rt.secrets.keychain_error,
        default_tier=rt.settings.default_tier,
        tiers=[TierInfo(tier=t, model=c.model) for t, c in tiers.items()],
    )


@router.get("/settings")
async def get_settings(request: Request) -> Settings:
    return _rt(request).settings


@router.put("/settings")
async def put_settings(request: Request, settings: Settings) -> Settings:
    await _rt(request).update_settings(settings)
    return settings


@router.put("/secrets/anthropic", status_code=204)
async def set_api_key(request: Request, body: ApiKeyIn) -> None:
    rt = _rt(request)
    rt.redactor.add_secret(body.api_key)
    try:
        rt.secrets.set_anthropic_key(body.api_key)
    except KeychainUnavailable as exc:
        raise HTTPException(503, "OS keychain unavailable; set ANTHROPIC_API_KEY instead") from exc
    await rt.store.audit("user", "secret.set", {"name": "anthropic_api_key"})


@router.delete("/secrets/anthropic", status_code=204)
async def delete_api_key(request: Request) -> None:
    rt = _rt(request)
    try:
        rt.secrets.delete_anthropic_key()
    except KeychainUnavailable as exc:
        raise HTTPException(503, "OS keychain unavailable") from exc
    await rt.store.audit("user", "secret.delete", {"name": "anthropic_api_key"})


@router.get("/sessions")
async def list_sessions(request: Request) -> list[SessionInfo]:
    return await _rt(request).store.list_sessions()


@router.get("/sessions/{session_id}/messages")
async def session_messages(request: Request, session_id: str) -> list[StoredMessage]:
    return await _rt(request).store.transcript(session_id)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(request: Request, session_id: str) -> None:
    rt = _rt(request)
    if not await rt.store.delete_session(session_id):
        raise HTTPException(404, "no such session")
    rt.chat.forget_session(session_id)
    await rt.store.audit("user", "session.delete", {"session_id": session_id})


@router.get("/cost")
async def cost(request: Request) -> SpendStatus:
    return await _rt(request).spend.status()


@router.get("/tools")
async def tools(request: Request) -> list[ToolInfo]:
    return _rt(request).registry.infos()


@router.get("/audit")
async def audit(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    before_id: int | None = Query(default=None, ge=1),
) -> list[AuditEntry]:
    return await _rt(request).store.list_audit(limit=limit, before_id=before_id)


class SetupCheck(BaseModel):
    ok: bool
    report: str
    details: dict[str, object]


@router.get("/setup/hands")
async def setup_hands(request: Request) -> SetupCheck:
    """Read-only diagnostics for window/control access (no arguments, no side effects)."""
    tool = _rt(request).registry.get("accessibility_status")
    if tool is None:
        return SetupCheck(ok=False, report="The window-control worker is not running.", details={})
    ctx = ToolContext(trace_id="setup", session_id="setup", trust=Trust.USER)
    result = await tool.run({}, ctx)
    return SetupCheck(ok=result.ok, report=result.output, details=result.evidence)


class SkillPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    triggers: list[str] | None = Field(default=None, min_length=1, max_length=8)
    enabled: bool | None = None
    reactivate: bool = False
    """Bring a demoted skill back into use (clears its failure streak)."""


@router.get("/skills")
async def list_skills(request: Request) -> list[Skill]:
    return await _rt(request).skill_runner.skills.all()


@router.patch("/skills/{skill_id}")
async def patch_skill(request: Request, skill_id: str, patch: SkillPatch) -> Skill:
    rt = _rt(request)
    skill = await rt.skill_runner.skills.get(skill_id)
    if skill is None:
        raise HTTPException(404, "no such skill")
    update: dict[str, object] = {}
    if patch.name is not None:
        update["name"] = patch.name.strip()
    if patch.triggers is not None:
        slots = {p.name for p in skill.params}
        triggers = tuple(" ".join(t.split()) for t in patch.triggers if t.strip())
        for trigger in triggers:
            if slots_in(trigger) != slots:
                raise HTTPException(
                    422, f"each trigger must use exactly these slots: {sorted(slots) or 'none'}"
                )
            if literal_chars(trigger) < 4:
                raise HTTPException(422, "a trigger needs some fixed words, not only slots")
        update["triggers"] = triggers
    if patch.enabled is not None:
        update["enabled"] = patch.enabled
    if patch.reactivate:
        update.update(status="active", consecutive_failures=0)
    updated = skill.model_copy(update=update)
    await rt.skill_runner.skills.save(updated)
    await rt.store.audit("user", "skill.edit", {"skill": skill_id, "fields": sorted(update)})
    return updated


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_skill(request: Request, skill_id: str) -> None:
    rt = _rt(request)
    if not await rt.skill_runner.skills.delete(skill_id):
        raise HTTPException(404, "no such skill")
    await rt.store.audit("user", "skill.delete", {"skill": skill_id})


class VoiceModelInfo(BaseModel):
    id: str
    title: str
    size: int
    license: str
    tier: str
    installed: bool
    downloading: bool
    active: bool
    recommended: bool


class VoiceStatus(BaseModel):
    engine_running: bool
    models: list[VoiceModelInfo]
    shortcut_commands: dict[str, str]
    """Commands to bind to desktop keyboard shortcuts (global hotkeys)."""
    overlay: "OverlayStatus"
    hands_free: "HandsFreeStatus"


class HandsFreeStatus(BaseModel):
    state: Literal["off", "idle", "listening", "thinking", "speaking", "approval"]
    detail: str
    voice: str
    """Which voice speaks: "natural (Piper)" or "the computer's own voice"."""
    natural_voice_command: str
    """Run this to install the natural voice (a one-time download)."""
    microphone: str
    """The microphone in use and why ("HD Webcam (chosen automatically: …)"); "" before it opens."""
    microphones: list[dict[str, str]]
    """Inputs to choose from: {"id", "name"}."""


class OverlayStatus(BaseModel):
    enabled: bool
    available: bool | None
    """None while the overlay window process is starting (or was never started)."""
    detail: str


class VoiceCommandIn(BaseModel):
    action: Literal["toggle_chat", "toggle_dictation", "stop", "approve", "deny"]
    """approve / deny answer the approval the voice overlay is showing."""


def _model(request: Request, model_id: str) -> ModelSpec:
    spec = BY_ID.get(model_id)
    if spec is None:
        raise HTTPException(404, "no such model")
    return spec


@router.get("/voice")
async def voice_status(request: Request) -> VoiceStatus:
    rt = _rt(request)
    recommended = recommended_model().id
    active = rt.settings.voice.stt_model
    models = [
        VoiceModelInfo(
            id=m.id,
            title=m.title,
            size=m.size,
            license=m.license,
            tier=m.tier,
            installed=rt.models.installed(m),
            downloading=rt.models.downloading(m.id),
            active=m.id == active and rt.models.installed(m),
            recommended=m.id == recommended,
        )
        for m in CATALOG
    ]
    # Desktop shortcuts run from the home folder, so change into the backend first.
    cli = f'sh -c \'cd "{BACKEND_DIR}" && exec "{sys.executable}" -m core.cli'
    commands = {
        "Talk to the assistant": f"{cli} voice'",
        "Dictate into any app": f"{cli} dictate'",
        "Stop everything": f"{cli} stop'",
        "Approve what the voice overlay asks": f"{cli} approve'",
        "Deny what the voice overlay asks": f"{cli} deny'",
    }
    host = rt.overlay_host
    overlay = OverlayStatus(
        enabled=rt.settings.voice.overlay,
        available=host.available if host else False,
        detail=host.detail if host else "not started (--no-overlay)",
    )
    loop = (
        rt.voice_loop.status()
        if rt.voice_loop
        else {"state": "off", "detail": "not started (--no-hands-free)"}
    )
    hands_free = HandsFreeStatus(
        state=loop["state"],
        detail=loop["detail"],
        voice=loop.get("voice", "the computer's own voice"),
        microphone=loop.get("microphone", ""),
        microphones=[m.model_dump() for m in rt.voice_loop.ears.microphones] if rt.voice_loop else [],
        natural_voice_command=f"{cli} install-voice'",
    )
    return VoiceStatus(
        engine_running=bool(rt.stt_model_dir()) and rt.worker_alive("voice"),
        models=models,
        shortcut_commands=commands,
        overlay=overlay,
        hands_free=hands_free,
    )


@router.post("/voice/models/{model_id}/download", status_code=202)
async def download_model(request: Request, model_id: str) -> dict[str, bool]:
    """Starts only when the user asks; progress arrives as download.progress events."""
    return {"started": await _rt(request).download_model(_model(request, model_id))}


@router.post("/voice/models/{model_id}/use")
async def use_model(request: Request, model_id: str) -> dict[str, bool]:
    rt = _rt(request)
    spec = _model(request, model_id)
    if not rt.models.installed(spec):
        raise HTTPException(409, "download the model first")
    voice = rt.settings.voice.model_copy(update={"stt_model": spec.id})
    await rt.update_settings(rt.settings.model_copy(update={"voice": voice}))
    return {"ok": True}


@router.delete("/voice/models/{model_id}", status_code=204)
async def delete_model(request: Request, model_id: str) -> None:
    rt = _rt(request)
    spec = _model(request, model_id)
    rt.models.delete(spec)
    if rt.settings.voice.stt_model == spec.id:
        voice = rt.settings.voice.model_copy(update={"stt_model": None})
        await rt.update_settings(rt.settings.model_copy(update={"voice": voice}))
    await rt.store.audit("user", "model.delete", {"model": spec.id})


class SayIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/voice/say")
async def voice_say(request: Request, body: SayIn) -> dict[str, bool]:
    """Read a reply aloud through the assistant's own voice (Piper or the OS voice)."""
    loop = _rt(request).voice_loop
    if loop is None:
        raise HTTPException(409, "voice output isn't running (started with --no-hands-free)")
    return {"spoken": await loop.say(body.text)}


@router.post("/voice/command", status_code=204)
async def voice_command(request: Request, body: VoiceCommandIn) -> None:
    """Used by desktop keyboard shortcuts (see core/cli.py) to drive the open UI."""
    rt = _rt(request)
    loop = rt.voice_loop
    if body.action == "toggle_chat" and loop is not None and loop.loop.state != "off":
        loop.loop.wake_now()  # hands-free is running: the shortcut is the same as saying the wake word
        return
    if body.action in ("toggle_chat", "toggle_dictation") and not rt.overlay.listening:
        rt.context.begin()  # where the user is right now: before anything else can change it
    await rt.overlay.hotkey(body.action, rt.listener_count())
    if body.action in ("approve", "deny"):
        return
    if body.action == "stop":
        await rt.stop_all()
    await rt.broadcast(VoiceCommand(action=body.action))
