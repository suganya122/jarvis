"""HTTP API for plugins (chat apps): enable, secrets, options, pairing."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.api import _rt
from core.plugin_manager import PluginInfo, PluginManager
from core.secret_store import KeychainUnavailable

router = APIRouter(prefix="/api/plugins")


class EnabledIn(BaseModel):
    enabled: bool


class OptionsIn(BaseModel):
    options: dict[str, str] = Field(max_length=20)


class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=4096, pattern=r"^\S+$")


class PairCode(BaseModel):
    code: str
    expires_in_s: int


def _manager(request: Request, name: str) -> PluginManager:
    manager = _rt(request).plugins
    if name not in manager.plugins:
        raise HTTPException(404, "no such plugin")
    return manager


@router.get("")
async def list_plugins(request: Request) -> list[PluginInfo]:
    manager = _rt(request).plugins
    return [manager.info(name) for name in sorted(manager.plugins)]


@router.put("/{name}/enabled")
async def set_enabled(request: Request, name: str, body: EnabledIn) -> PluginInfo:
    manager = _manager(request, name)
    await manager.set_enabled(name, body.enabled)
    return manager.info(name)


@router.put("/{name}/options")
async def set_options(request: Request, name: str, body: OptionsIn) -> PluginInfo:
    manager = _manager(request, name)
    await manager.set_options(name, body.options)
    return manager.info(name)


@router.put("/{name}/secrets/{secret}", status_code=204)
async def set_secret(request: Request, name: str, secret: str, body: SecretIn) -> None:
    manager = _manager(request, name)
    if secret not in {f.name for f in manager.plugins[name].manifest.secrets}:
        raise HTTPException(404, "this plugin has no such secret")
    _rt(request).redactor.add_secret(body.value)  # keep it out of logs from now on
    try:
        await manager.set_secret(name, secret, body.value)
    except KeychainUnavailable as exc:
        raise HTTPException(503, f"OS keychain unavailable: {exc}") from exc


@router.delete("/{name}/secrets/{secret}", status_code=204)
async def delete_secret(request: Request, name: str, secret: str) -> None:
    manager = _manager(request, name)
    try:
        await manager.set_secret(name, secret, None)
    except KeychainUnavailable as exc:
        raise HTTPException(503, f"OS keychain unavailable: {exc}") from exc


@router.post("/{name}/pair")
async def pair(request: Request, name: str) -> PairCode:
    _manager(request, name)
    rt = _rt(request)
    code, ttl = rt.channels.new_code(name)
    await rt.store.audit("user", "channel.pair_code", {"channel": name})
    return PairCode(code=code, expires_in_s=ttl)


@router.delete("/{name}/paired/{user_id}", status_code=204)
async def unpair(request: Request, name: str, user_id: str) -> None:
    _manager(request, name)
    if not await _rt(request).channels.unpair(name, user_id):
        raise HTTPException(404, "that account isn't paired")
