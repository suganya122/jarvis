"""Daemon entry point: `python -m core`."""

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

import uvicorn

from contracts import APP_NAME
from contracts.app import DEFAULT_WAKE_WORD
from core import env_file, paths, redact
from core.app import DaemonOptions, create_app
from core.config import DEFAULT_PORT
from models.fake import FakeProvider

PACKAGED_STATIC = Path(__file__).resolve().parent / "webui"
"""Where an installed package keeps the built web UI."""
SOURCE_STATIC = Path(__file__).resolve().parents[2] / "frontend" / "dist"
# A source checkout serves the live frontend build; an installed package its bundled copy.
DEFAULT_STATIC = SOURCE_STATIC if (SOURCE_STATIC / "index.html").exists() else PACKAGED_STATIC
DEV_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="core", description=f"{APP_NAME} local daemon")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open", action="store_true", help="open the web UI in your browser")
    parser.add_argument("--dev", action="store_true", help="allow the Vite dev server origin")
    parser.add_argument("--fake-model", action="store_true", help="use an echo model (no API calls)")
    parser.add_argument("--static-dir", type=Path, default=DEFAULT_STATIC)
    parser.add_argument("--no-overlay", action="store_true", help="don't show the voice overlay window")
    parser.add_argument("--no-hands-free", action="store_true", help="don't listen for the wake word")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server: uvicorn.Server | None = None

    def request_exit() -> None:
        if server is not None:
            server.should_exit = True

    data_dir = paths.data_dir()
    env_path = env_file.find_env_file(data_dir)
    from_file = env_file.load(env_path)
    if (
        env_path is None
    ):  # no .env yet: make one to paste the key into (a source checkout's, else the data folder's)
        checkout = env_file.CHECKOUT_ROOT
        env_path = checkout / ".env" if (checkout / "backend").is_dir() else data_dir / ".env"
        env_file.create_template(env_path)
        print(f"Created {env_path}: paste your ANTHROPIC_API_KEY into it and restart.", flush=True)
    options = DaemonOptions(
        env={**os.environ, **from_file},
        data_dir=data_dir,
        port=args.port,
        extra_origins=DEV_ORIGINS if args.dev else set(),
        static_dir=args.static_dir,
        provider_override=FakeProvider(delay_s=0.03) if args.fake_model else None,
        request_exit=request_exit,
        start_overlay=not args.no_overlay,
        start_voice_loop=not args.no_hands_free,
    )
    app = create_app(options)
    redact.install(app.state.redactor)

    ui_port = 5173 if args.dev else args.port
    url = f"http://127.0.0.1:{ui_port}/#token={app.state.token}"
    # Printed to the terminal only (never logged): the token is the key to the daemon.
    key_note = "ANTHROPIC_API_KEY found in .env" if "ANTHROPIC_API_KEY" in from_file else None
    if key_note:
        print(f"  {key_note}", flush=True)
    talk = (
        f"Say “{DEFAULT_WAKE_WORD.title()}” (or the wake word you set) to talk to it.\n"
        if not args.no_hands_free
        else ""
    )
    if sys.stdout.isatty():  # a terminal, not a log file: the link carries the daemon's token
        print(f"\n{APP_NAME} is running. {talk}Settings and history (optional):\n  {url}\n", flush=True)
    else:
        print(f"{APP_NAME} is running. {talk}".strip(), flush=True)
    if os.environ.get("JARVIS_SPEECH_HELPER"):
        _exit_with_parent(request_exit)
    if args.open:
        webbrowser.open(url)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=args.port,
        ws_max_size=256 * 1024,
        server_header=False,
        log_config=None,
    )
    server = uvicorn.Server(config)
    server.run()


def _exit_with_parent(request_exit: Callable[[], None]) -> None:
    """Started by Jarvis.app: if it goes away (stopped from outside, crashed), go with it, rather
    than run on without the helpers it provides (speech, audio, window control)."""
    parent = os.getppid()

    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(2)
        print("Jarvis.app is gone; stopping", flush=True)
        request_exit()

    threading.Thread(target=watch, daemon=True).start()


if __name__ == "__main__":
    main()
