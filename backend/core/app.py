"""Application factory."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from contracts import APP_NAME
from contracts.model import ModelProvider
from contracts.tools import Tool
from core import db, paths
from core.api import router
from core.auth import load_or_create_token
from core.automation_api import router as automation_router
from core.autostart import Autostart
from core.control_api import router as control_router
from core.data_api import router as data_router
from core.data_control import wipe
from core.knowledge.api import router as knowledge_router
from core.plugin_api import router as plugin_router
from core.ratelimit import KeyedLimiter
from core.redact import Redactor
from core.runtime import Runtime
from core.secret_store import KeyringBackend, SecretBackend, SecretStore
from core.security import SecurityMiddleware, loopback_hosts, loopback_origins
from core.store import Store
from core.ws import chat_socket


@dataclass
class DaemonOptions:
    data_dir: Path
    port: int
    extra_origins: set[str] = field(default_factory=set)
    """Only for the Vite dev server; never enabled in normal runs."""
    static_dir: Path | None = None
    secret_backend: SecretBackend | None = None
    provider_override: ModelProvider | None = None
    env: dict[str, str] | None = None
    start_workers: bool = True
    start_overlay: bool = True
    """Run the voice overlay window process (tests and --no-overlay turn it off)."""
    start_voice_loop: bool = False
    """Listen for the wake word on the real microphone (`python -m core` turns it on; tests don't)."""
    start_plugins: bool = True
    """Start the plugins the user enabled (chat apps)."""
    start_automation: bool = True
    """Run the routine/watcher loop and recover interrupted tasks (tests drive these by hand)."""
    worker_env: dict[str, str] | None = None
    """Extra environment for worker processes (tests point hands at a private desktop)."""
    extra_tools: tuple[Tool, ...] = ()
    autostart: Autostart | None = None
    """Start-at-login backend (tests point it at a temporary home folder)."""
    request_exit: Callable[[], None] | None = None
    """Asks the server to shut down (set by `python -m core`); used after "delete all data"."""


def create_app(options: DaemonOptions) -> FastAPI:
    token = load_or_create_token(paths.token_path(options.data_dir))
    redactor = Redactor()
    redactor.add_secret(token)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        conn = await db.connect(paths.db_path(options.data_dir))
        store = Store(conn, redactor)
        secrets = SecretStore(options.secret_backend or KeyringBackend(), env=options.env)
        app.state.secrets = secrets
        redactor.add_secret(secrets.anthropic_key())
        settings = await store.load_settings()
        runtime = Runtime(
            store,
            secrets,
            settings,
            redactor,
            data_dir=options.data_dir,
            provider_override=options.provider_override,
            extra_tools=options.extra_tools,
        )
        app.state.runtime = runtime
        if options.start_workers:
            await runtime.start_workers(options.worker_env)
        if options.start_automation:
            await runtime.start_automation()
        if options.start_overlay:
            await runtime.start_overlay(options.worker_env)
        if options.start_voice_loop:
            await runtime.start_voice_loop(options.worker_env)
        if options.start_plugins:
            await runtime.start_plugins()
        await store.audit("system", "daemon.start", {"port": options.port})
        try:
            yield
        finally:
            await runtime.shutdown()
            await store.audit("system", "daemon.stop", {})
            await store.close()
            if app.state.wipe_requested:
                removed = wipe(options.data_dir, secrets, app.state.autostart, runtime.plugins.secret_names())
                print(
                    f"All {APP_NAME} data was deleted ({', '.join(removed) or 'nothing found'}).", flush=True
                )

    app = FastAPI(title=APP_NAME, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.state.redactor = redactor
    app.state.autostart = options.autostart or Autostart()
    app.state.request_exit = options.request_exit
    app.state.wipe_requested = False
    app.include_router(router)
    app.include_router(control_router)
    app.include_router(automation_router)
    app.include_router(data_router)
    app.include_router(knowledge_router)
    app.include_router(plugin_router)
    app.add_api_websocket_route("/ws", chat_socket)

    if options.static_dir and (options.static_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=options.static_dir, html=True), name="ui")
    else:

        @app.get("/", response_class=PlainTextResponse)
        async def no_ui() -> str:
            return f"{APP_NAME} daemon is running, but the web UI is not built (npm run build in frontend/)."

    app.add_middleware(
        SecurityMiddleware,
        token=token,
        allowed_hosts=loopback_hosts(options.port),
        allowed_origins=loopback_origins(options.port) | options.extra_origins,
        limiter=KeyedLimiter(240),
    )
    return app
