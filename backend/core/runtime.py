"""Long-lived daemon state shared by HTTP and WebSocket handlers."""

import asyncio
import logging
import os
from collections.abc import Iterable
from pathlib import Path

from contracts.context import ActiveContext
from contracts.model import ModelProvider
from contracts.overlay import OverlayTask
from contracts.tools import Tool
from contracts.voice import TranscriptEvent
from contracts.worker import BrowserConfig, WorkerConfig
from contracts.ws import ControlState, DownloadProgress, PluginStatus, ServerMessage, TaskUpdate, VoiceCommand
from core.approvals import ApprovalBroker
from core.automation_store import AutomationStore
from core.automation_tools import CreateRoutine, CreateWatcher
from core.background import BackgroundManager
from core.channels import ChannelDeps, ChannelService
from core.chat import ChatService
from core.chat_types import Emit
from core.config import Settings
from core.context import ContextService
from core.context import render as render_context
from core.dictation import DictationService
from core.memory import MemoryStore, render, select
from core.memory_tools import Forget, Recall, Remember
from core.model_files import BY_ID, ModelFiles, ModelSpec
from core.notify import Notifier
from core.overlay import OverlayController
from core.overlay_host import OverlayHost
from core.overlay_tasks import TaskChips
from core.plugin_manager import PluginManager
from core.providers import build_provider
from core.redact import Redactor
from core.schedule import local_now
from core.scheduler import Automations
from core.secret_store import SecretStore
from core.skill_runner import SkillRunner
from core.spend import CappedProvider, SpendGuard
from core.store import Store
from core.tool_exec import ChatPermissions, ToolExecutor
from core.toolbox import CurrentTime, ToolRegistry
from core.voice import VoiceHub
from core.voice_loop import VoiceLoop, apple_speech_helper, mac_app
from core.watchers import WatcherService
from core.worker_host import RemoteTool, WorkerHost
from skills.store import SkillStore

log = logging.getLogger(__name__)
OS_WORKER = "os_tools"
VOICE_WORKER = "voice"
HANDS_WORKER = "hands"


def _expand(path: str) -> str:
    return str(Path(os.path.expanduser(path)).resolve())


class Runtime:
    def __init__(
        self,
        store: Store,
        secrets: SecretStore,
        settings: Settings,
        redactor: Redactor,
        data_dir: Path,
        provider_override: ModelProvider | None = None,
        extra_tools: Iterable[Tool] = (),
    ) -> None:
        self.store = store
        self.secrets = secrets
        self.redactor = redactor
        self._data_dir = data_dir
        self._settings = settings
        self._override = provider_override
        self._provider = provider_override or build_provider(settings, secrets)
        self.spend = SpendGuard(store, lambda: self._settings)
        self._capped = CappedProvider(lambda: self._provider, self.spend)
        self.memory = MemoryStore(store.conn)
        self.registry = ToolRegistry()
        self.registry.register(CurrentTime())
        for memory_tool in (Remember(self.memory), Forget(self.memory), Recall(self.memory)):
            self.registry.register(memory_tool)
        for tool in extra_tools:
            self.registry.register(tool)
        self.approvals = ApprovalBroker()
        executor = ToolExecutor(self.registry, self.approvals, store, lambda: self._settings)
        self.context = ContextService(self.registry, lambda: self._settings, store)
        executor.guard = self.context.guard
        self.context.on_snapshot = self._show_target
        self.skill_runner = SkillRunner(SkillStore(store.conn), executor, store)
        self.chat = ChatService(
            store,
            self.provider,
            lambda: self._settings,
            self.registry,
            executor,
            skills=self.skill_runner,
            context=self.memory_context,
            active=self._active_context,
        )
        self.models = ModelFiles(data_dir / "models")
        self.dictation = DictationService(
            lambda: self._settings, self.provider, store, executor, self.registry
        )
        self.notifier = Notifier(self.broadcast, self.registry, lambda: self._settings, store)
        self.automation_store = AutomationStore(store.conn)
        self.background = BackgroundManager(
            self.automation_store, self.chat, lambda: self._settings, self.broadcast, self.notifier
        )
        self.automations = Automations(
            self.automation_store,
            self.background,
            self.notifier,
            WatcherService(self.registry, self.notifier, lambda: self._settings),
            paused=lambda: self.paused,
        )
        self.registry.register(CreateRoutine(lambda: self.automations, local_now))
        self.registry.register(CreateWatcher(lambda: self.automations))
        self.channels = ChannelService(
            ChannelDeps(
                store=store,
                chat=self.chat,
                approvals=self.approvals,
                settings=lambda: self._settings,
                save_settings=self.update_settings,
                paused=lambda: self.paused,
                track=self.track,
                on_paired=self._plugin_changed,
            )
        )
        self.plugins = PluginManager(
            data_dir / "plugins",
            self.channels,
            secrets,
            store,
            lambda: self._settings,
            self.update_settings,
            self.broadcast,
            protect=redactor.add_secret,
        )
        self.overlay = OverlayController(
            self.approvals,
            self._overlay_stop,
            lambda: self._settings.voice.overlay,
            auto_send=lambda: self._settings.voice.auto_send_silence,
        )
        self.overlay_host: OverlayHost | None = None
        self.voice = VoiceHub(lambda: self.workers.get(VOICE_WORKER), self.chat, self.dictation.run)
        self.paused = False
        self.voice_loop: VoiceLoop | None = None
        self.task_chips = TaskChips(self._show_chips, self._announce)
        self.workers: dict[str, WorkerHost] = {}
        self._listeners: set[Emit] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def settings(self) -> Settings:
        return self._settings

    def provider(self) -> ModelProvider:
        """The active provider behind the spend caps: every model call goes through here."""
        return self._capped

    async def _show_target(self, ctx: ActiveContext) -> None:
        await self.overlay.set_target(ctx.label())

    def _active_context(self, perms: ChatPermissions) -> str:
        pinned = self.context.pinned(perms)
        return render_context(pinned) if pinned else ""

    async def memory_context(self, request: str) -> str:
        settings = self._settings.memory
        if not settings.enabled:
            return ""
        return render(select(await self.memory.all(), request, settings.max_prompt_chars))

    async def update_settings(self, settings: Settings) -> None:
        await self.store.save_settings(settings)
        self._settings = settings
        if self._override is None:
            self._provider = build_provider(settings, self.secrets)
        for host in self.workers.values():
            if host.alive:
                await host.configure(self.worker_config())
        if self.voice_loop is not None:
            await self.voice_loop.settings_changed()
        await self.store.audit("user", "settings.update", {"provider": settings.provider})

    # ---- workers -----------------------------------------------------------

    def worker_config(self) -> WorkerConfig:
        tools = self._settings.tools
        # The app's own data (auth token, database) is always off limits.
        denied = [*tools.denied_paths, str(self._data_dir)]
        return WorkerConfig(
            allowed_roots=tuple(_expand(p) for p in tools.allowed_roots),
            denied_paths=tuple(_expand(p) for p in denied),
            shell_allowlist=tuple(tools.shell_allowlist),
            max_output_chars=tools.max_output_chars,
            command_timeout_s=tools.command_timeout_s,
            blocked_apps=tuple(tools.blocked_apps),
            capture_max_edge=tools.capture_max_edge,
            stt_model_dir=self.stt_model_dir(),
            apple_speech_helper=self.apple_speech_helper(),
            mac_app=mac_app(),
            browser=BrowserConfig(
                mode=self._settings.browser.mode,
                profile_dir=str(self._data_dir / "browser"),
                headless=not self._settings.browser.show_window,
                channel=self._settings.browser.channel,
                cdp_url=self._settings.browser.cdp_url,
                allow_private_hosts=self._settings.browser.allow_private_hosts,
                min_seconds_between_pages=self._settings.browser.min_seconds_between_pages,
            ),
        )

    def apple_speech_helper(self) -> str:
        return apple_speech_helper(self._settings.voice.stt_engine)

    def stt_ready(self) -> bool:
        """Speech can be recognised: Apple's on a Mac, or a downloaded model."""
        return bool(self.apple_speech_helper() or self.stt_model_dir())

    def stt_model_dir(self) -> str:
        spec = BY_ID.get(self._settings.voice.stt_model or "")
        return str(self.models.path(spec)) if spec and self.models.installed(spec) else ""

    @property
    def worker(self) -> WorkerHost | None:
        return self.workers.get(OS_WORKER)

    def worker_alive(self, name: str) -> bool:
        host = self.workers.get(name)
        return host is not None and host.alive

    async def start_workers(self, extra_env: dict[str, str] | None = None) -> None:
        workers = (
            (OS_WORKER, "workers.os_tools"),
            (HANDS_WORKER, "workers.hands"),
            (VOICE_WORKER, "workers.voice"),
            ("browser", "workers.browser"),
        )
        for name, module in workers:
            host = WorkerHost(name, module, extra_env=extra_env)
            try:
                infos = await host.start(self.worker_config())
            except Exception:
                log.exception("%s worker failed to start; continuing without it", name)
                await host.stop()
                continue
            self.workers[name] = host
            if name == VOICE_WORKER:
                host.on_transcript = self._on_transcript
            for info in infos:
                self.registry.register(RemoteTool(info, host))
            log.info("%s ready: %s", name, ", ".join(i.name for i in infos))

    def _on_transcript(self, event: TranscriptEvent) -> None:
        """Hands-free streams go to the voice loop, the chat window's to the voice hub."""
        if self.voice_loop is not None and self.voice_loop.owns(event):
            self.voice_loop.on_transcript(event)
        else:
            self.voice.on_transcript(event)

    async def start_voice_loop(self, extra_env: dict[str, str] | None = None) -> None:
        """Always listening for the wake word (microphone and speaker in their own process)."""
        self.voice_loop = VoiceLoop(
            data_dir=self._data_dir,
            store=self.store,
            chat=self.chat,
            overlay=self.overlay,
            approvals=self.approvals,
            settings=lambda: self._settings,
            paused=lambda: self.paused,
            voice_worker=lambda: self.workers.get(VOICE_WORKER),
            stt_model_ready=self.stt_ready,
            begin_context=self.context.begin,
            for_voice=self.context.for_voice,
            extra_env=extra_env,
        )
        await self.voice_loop.start()

    async def _plugin_changed(self, name: str) -> None:
        info = self.plugins.info(name)
        await self.broadcast(PluginStatus(name=name, state=info.state, detail=info.detail))

    async def _overlay_stop(self) -> None:
        await self.stop_all("stopped from the voice overlay")
        await self.broadcast(VoiceCommand(action="stop"))  # the chat window stops the microphone

    async def start_overlay(self, extra_env: dict[str, str] | None = None) -> None:
        host = OverlayHost(self.overlay, extra_env)
        try:
            await host.start()
        except OSError:
            log.exception("voice overlay failed to start; continuing without it")
            return
        self.overlay_host = host
        self.overlay.surface = host
        self.overlay.visible = lambda: host.available is True
        self.overlay.fallback = self._voice_notice

    async def _voice_notice(self, title: str, body: str) -> None:
        """The overlay can't show here: a desktop notification instead (the chat has the rest)."""
        await self.notifier.notify(title, body, "voice", in_app=False)

    def listener_count(self) -> int:
        return len(self._listeners)

    async def start_plugins(self) -> None:
        await self.plugins.start_enabled()

    async def start_automation(self) -> None:
        await self.background.recover()
        await self.automations.start()

    async def shutdown(self) -> None:
        if self.voice_loop is not None:
            await self.voice_loop.stop()
        await self.automations.stop()
        await self.plugins.shutdown()
        if self.overlay_host is not None:
            await self.overlay_host.stop()
        await self.background.shutdown()
        await self.stop_all("daemon shutting down", audit=False)
        for host in self.workers.values():
            await host.stop()

    # ---- connections, kill switch, pause -------------------------------------

    def add_listener(self, emit: Emit) -> None:
        self._listeners.add(emit)

    def remove_listener(self, emit: Emit) -> None:
        self._listeners.discard(emit)

    def track(self, task: asyncio.Task[None]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop_all(self, reason: str = "stopped by you", audit: bool = True) -> int:
        """Kill switch: deny every pending approval and cancel every running turn."""
        denied = self.approvals.deny_all(reason)
        if self.voice_loop is not None:
            self.voice_loop.interrupt()  # stop talking and drop a spoken request
        tasks = [t for t in self._tasks if not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        background = await self.background.cancel_all()
        if audit:
            await self.store.audit(
                "user",
                "control.stop_all",
                {"turns": len(tasks), "background": background, "approvals": denied},
            )
        return len(tasks) + background

    async def broadcast(self, message: ServerMessage) -> None:
        await asyncio.gather(*(emit(message) for emit in list(self._listeners)), return_exceptions=True)
        if isinstance(message, TaskUpdate):  # also on screen with no page open: a chip in the corner
            await self.task_chips.update(message.task)

    async def _show_chips(self, tasks: list[OverlayTask]) -> None:
        if self.overlay_host is not None and self._settings.voice.overlay:
            await self.overlay_host.tasks(tasks)

    async def _announce(self, text: str) -> None:
        """Say it out loud, only if Jarvis isn't in the middle of a conversation."""
        if self.voice_loop is not None and self.voice_loop.loop.state == "idle":
            await self.voice_loop.say(text)

    async def download_model(self, spec: ModelSpec) -> bool:
        async def report(progress: DownloadProgress) -> None:
            await self.broadcast(progress)
            if progress.state == "done":
                await self.store.audit("system", "model.installed", {"model": spec.id})
                if self._settings.voice.stt_model is None:  # first model: start using it
                    voice = self._settings.voice.model_copy(update={"stt_model": spec.id})
                    await self.update_settings(self._settings.model_copy(update={"voice": voice}))

        started = self.models.start(spec, report)
        if started:
            await self.store.audit("user", "model.download", {"model": spec.id, "bytes": spec.size})
        return started

    async def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if paused:
            await self.stop_all("paused", audit=False)
        await self.store.audit("user", "control.pause", {"paused": paused})
        state = ControlState(paused=paused)
        await asyncio.gather(*(emit(state) for emit in list(self._listeners)), return_exceptions=True)
