"""Command-line helpers: `python -m core.cli voice|dictate|stop|install-voice|uninstall`.

voice / dictate / stop / approve / deny: bind these to keys in your desktop's settings
(e.g. GNOME → Keyboard → Custom Shortcuts). They tell the running assistant to start/stop
listening, stop everything, or answer the approval the voice overlay shows. They use the
local auth token from the app data folder.

set-key / setup-voice / autostart on|off: setup steps the installer runs (see core/setup_cli.py).

setup-windows: what window placement needs here (GNOME: installs its extension; macOS: opens
Accessibility settings).

check-voice: is the microphone heard and can it speak (nothing is recorded).

install-voice / remove-voice: the natural (Piper) voice for spoken replies, downloaded only
when you run this (see core/voice_install.py).

uninstall: deletes all app data, the API key from the OS keychain, and the start-at-login
entry, then tells you how to remove the program itself.
"""

import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

from contracts import APP_ID, APP_NAME
from core import paths, setup_cli, voice_check, voice_install, windows_setup
from core.autostart import Autostart
from core.config import DEFAULT_PORT
from core.data_control import app_files, wipe
from core.plugins import discover
from core.secret_store import KeyringBackend, SecretStore

ACTIONS = {"voice": "toggle_chat", "dictate": "toggle_dictation", "stop": "stop", "approve": "approve",
           "deny": "deny"}  # fmt: skip


def _post(port: int, path: str, body: dict[str, object], token: str) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5):  # noqa: S310  (loopback only)
        pass


def send(action: str, port: int) -> int:
    token = paths.token_path(paths.data_dir()).read_text(encoding="utf-8").strip()
    try:
        _post(port, "/api/voice/command", {"action": ACTIONS[action]}, token)
        return 0
    except (urllib.error.URLError, OSError) as exc:
        print(f"The assistant isn't reachable on port {port}: {exc}", file=sys.stderr)
        return 1


def _running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1):
            return True
    except (urllib.error.URLError, OSError):
        return False


def uninstall(port: int, yes: bool) -> int:
    data_dir = paths.data_dir()
    if not yes:
        answer = input(f"Delete all {APP_NAME} data in {data_dir}, its keychain entry and login item? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Nothing was deleted.")
            return 1
    token_file = paths.token_path(data_dir)
    if _running(port) and token_file.exists():
        # A running daemon deletes its own data as it exits (its database is open).
        try:
            _post(port, "/api/data/delete-all", {"confirm": "DELETE"}, token_file.read_text().strip())
        except (urllib.error.URLError, OSError) as exc:
            print(f"Couldn't ask the running app to stop ({exc}). Stop it, then run this again.")
            return 1
        # Wait for the daemon to finish deleting (its database must be closed first; on
        # Windows open files can't be deleted at all).
        for _ in range(300):
            if not token_file.exists():
                break
            time.sleep(0.1)
    plugin_secrets = [
        (p.name, f.name) for p in discover(data_dir / "plugins").values() for f in p.manifest.secrets
    ]
    removed = wipe(data_dir, SecretStore(KeyringBackend()), Autostart(), plugin_secrets)
    left = app_files(data_dir)
    if left:
        print("Some files couldn't be deleted: " + ", ".join(str(p) for p in left), file=sys.stderr)
        return 1
    print(f"{APP_NAME} data deleted{': ' + ', '.join(removed) if removed else ''}.")
    print(f"To remove the program: run the bundle's uninstall script, or `pipx uninstall {APP_ID}`.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="core.cli", description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            *sorted(ACTIONS),
            "check-voice",
            "install-voice",
            "remove-voice",
            "setup-windows",
            "set-key",
            "setup-voice",
            "autostart",
            "uninstall",
        ],
    )
    parser.add_argument("--voice", choices=sorted(voice_install.VOICES), default=voice_install.DEFAULT_VOICE,
                        help="install-voice: which natural voice")  # fmt: skip
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--yes", action="store_true", help="uninstall without asking")
    parser.add_argument("state", nargs="?", choices=["on", "off"], default="on", help="autostart: on or off")
    parser.add_argument(
        "--model", default=None, help="setup-voice: a speech model id (default: the recommended one)"
    )
    parser.add_argument("--exec", default=None, help="autostart: the program to start at login")
    args = parser.parse_args()
    if args.action == "uninstall":
        sys.exit(uninstall(args.port, args.yes))
    if args.action == "set-key":
        sys.exit(setup_cli.set_key(paths.data_dir(), setup_cli.read_key_from_stdin()))
    if args.action == "setup-voice":
        sys.exit(asyncio.run(setup_cli.setup_voice(paths.data_dir(), args.model)))
    if args.action == "autostart":
        sys.exit(setup_cli.autostart(args.state != "off", args.exec))
    if args.action == "setup-windows":
        sys.exit(windows_setup.setup())
    if args.action == "check-voice":
        sys.exit(asyncio.run(voice_check.check(paths.data_dir())))
    if args.action == "install-voice":
        sys.exit(voice_install.install(paths.data_dir(), args.voice, args.yes))
    if args.action == "remove-voice":
        sys.exit(voice_install.remove(paths.data_dir()))
    sys.exit(send(args.action, args.port))


if __name__ == "__main__":
    main()
