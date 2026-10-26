"""Plugin lifecycle: discover, enable/disable, keychain secrets, live status for the UI."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel

from contracts.channel import ChannelState
from contracts.plugin import PluginManifest
from contracts.ws import PluginStatus, ServerMessage
from core.channel_host import ChannelHost
from core.channels import ChannelService
from core.config import PairedAccount, Settings
from core.plugins import Plugin, discover
from core.secret_store import KeychainUnavailable, SecretStore
from core.store import Store

log = logging.getLogger(__name__)


class PluginInfo(BaseModel):
    manifest: PluginManifest
    first_party: bool
    enabled: bool
    state: ChannelState
    detail: str
    options: dict[str, str]
    secrets_set: dict[str, bool]
    paired: list[PairedAccount]


class PluginManager:
    def __init__(
        self,
        user_dir: Path | None,
        channels: ChannelService,
        secrets: SecretStore,
        store: Store,
        settings: Callable[[], Settings],
        save_settings: Callable[[Settings], Awaitable[None]],
        broadcast: Callable[[ServerMessage], Awaitable[None]],
        first_party_dir: Path | None = None,
        protect: Callable[[str], None] = lambda _: None,
    ) -> None:
        self._protect = protect  # keeps secret values out of logs
        self.plugins: dict[str, Plugin] = (
            discover(user_dir, first_party_dir) if first_party_dir else discover(user_dir)
        )
        self._channels = channels
        self._secrets = secrets
        self._store = store
        self._settings = settings
        self._save = save_settings
        self._broadcast = broadcast
        self._hosts: dict[str, ChannelHost] = {}
        self._status: dict[str, tuple[ChannelState, str]] = {}
        self._pending: set[asyncio.Future[None]] = set()

    # ---- lifecycle -----------------------------------------------------------

    async def start_enabled(self) -> None:
        for name, enabled in self._settings().plugins.enabled.items():
            if enabled and name in self.plugins:
                await self.start(name)

    async def start(self, name: str) -> None:
        plugin = self.plugins[name]
        await self.stop(name)
        try:
            values = {f.name: self._secrets.get_plugin_secret(name, f.name) for f in plugin.manifest.secrets}
        except KeychainUnavailable as exc:
            self._set(name, "error", f"the keychain isn't available ({exc})")
            return
        for value in values.values():
            if value:
                self._protect(value)
        missing = [f.label for f in plugin.manifest.secrets if not values.get(f.name)]
        if missing:
            self._set(name, "error", f"add the {', '.join(missing)} first")
            return
        options = {f.name: "" for f in plugin.manifest.options} | self._settings().plugins.options.get(
            name, {}
        )
        host = ChannelHost(plugin, options, {k: v for k, v in values.items() if v}, self._set)
        self._hosts[name] = host
        await host.start()
        self._channels.attach(host)

    async def stop(self, name: str) -> None:
        host = self._hosts.pop(name, None)
        if host is not None:
            await self._channels.detach(name)
            await host.stop()
        self._set(name, "stopped", "")

    async def shutdown(self) -> None:
        for name in list(self._hosts):
            await self.stop(name)

    # ---- user changes ----------------------------------------------------------

    async def set_enabled(self, name: str, enabled: bool) -> None:
        settings = self._settings()
        plugins = settings.plugins.model_copy(update={"enabled": {**settings.plugins.enabled, name: enabled}})
        await self._save(settings.model_copy(update={"plugins": plugins}))
        await self._store.audit("user", "plugin.enable" if enabled else "plugin.disable", {"plugin": name})
        await (self.start(name) if enabled else self.stop(name))

    async def set_options(self, name: str, options: dict[str, str]) -> None:
        known = {f.name for f in self.plugins[name].manifest.options}
        settings = self._settings()
        clean = {k: v.strip() for k, v in options.items() if k in known}
        plugins = settings.plugins.model_copy(update={"options": {**settings.plugins.options, name: clean}})
        await self._save(settings.model_copy(update={"plugins": plugins}))
        await self._restart_if_enabled(name)

    async def set_secret(self, name: str, secret: str, value: str | None) -> None:
        if value:
            self._secrets.set_plugin_secret(name, secret, value)
        else:
            self._secrets.delete_plugin_secret(name, secret)
        await self._store.audit(
            "user", "plugin.secret", {"plugin": name, "secret": secret, "set": bool(value)}
        )
        await self._restart_if_enabled(name)

    async def _restart_if_enabled(self, name: str) -> None:
        if self._settings().plugins.enabled.get(name):
            await self.start(name)

    def secret_names(self) -> list[tuple[str, str]]:
        return [(p.name, f.name) for p in self.plugins.values() for f in p.manifest.secrets]

    # ---- status ----------------------------------------------------------------

    def _set(self, name: str, state: ChannelState, detail: str) -> None:
        if self._status.get(name) == (state, detail):
            return
        self._status[name] = (state, detail)
        log.info("plugin %s: %s %s", name, state, detail)
        # Status can come from the host's reader callback (not a coroutine): broadcast in the background.
        task = asyncio.ensure_future(self._broadcast(PluginStatus(name=name, state=state, detail=detail)))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def info(self, name: str) -> PluginInfo:
        plugin = self.plugins[name]
        settings = self._settings().plugins
        state, detail = self._status.get(name, ("stopped", ""))
        try:
            secrets_set = {
                f.name: bool(self._secrets.get_plugin_secret(name, f.name)) for f in plugin.manifest.secrets
            }
        except KeychainUnavailable:
            secrets_set = {f.name: False for f in plugin.manifest.secrets}
        return PluginInfo(
            manifest=plugin.manifest,
            first_party=plugin.first_party,
            enabled=settings.enabled.get(name, False),
            state=state,
            detail=detail,
            options={f.name: settings.options.get(name, {}).get(f.name, "") for f in plugin.manifest.options},
            secrets_set=secrets_set,
            paired=self._channels.paired(name),
        )
