"""Export everything, delete everything, start at login, and uninstall."""

import plistlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts import APP_ID
from core import cli, paths
from core.app import DaemonOptions, create_app
from core.autostart import Autostart
from core.secret_store import ANTHROPIC_KEY_NAME, MemoryBackend
from models.fake import FakeProvider
from tests.conftest import BASE, PORT, Harness

KEY = "sk-ant-test-0123456789abcdefghij"


def daemon(data: Path, home: Path, keychain: MemoryBackend) -> tuple[TestClient, dict[str, str]]:
    options = DaemonOptions(
        data_dir=data,
        port=PORT,
        secret_backend=keychain,
        provider_override=FakeProvider(),
        env={},
        start_workers=False,
        start_overlay=False,
        autostart=Autostart(platform="linux", home=home),
    )
    app = create_app(options)
    client = TestClient(app, base_url=BASE)
    return client, {"Authorization": f"Bearer {app.state.token}"}


def test_export_has_everything_but_no_secrets(harness: Harness) -> None:
    auth = harness.auth
    harness.client.post("/api/memories", headers=auth, json={"text": "likes tea"})
    harness.client.put("/api/secrets/anthropic", headers=auth, json={"api_key": KEY})
    response = harness.client.get("/api/export", headers=auth)
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith(f'attachment; filename="{APP_ID}-export-')
    data = response.json()
    tables = data["tables"]
    assert {"memories", "audit_log", "settings", "skills", "routines", "cost_ledger"} <= set(tables)
    assert tables["memories"][0]["body"]["text"] == "Likes tea"  # JSON columns come out as objects
    assert KEY not in response.text and harness.token not in response.text
    assert harness.client.get("/api/export").status_code == 401


def test_delete_all_removes_only_app_files(tmp_path: Path) -> None:
    data, home, keychain = tmp_path / "data", tmp_path / "home", MemoryBackend()
    data.mkdir()
    (data / "models").mkdir()
    (data / "models" / "big.bin").write_bytes(b"x")
    (data / "notes-of-mine.txt").write_text("not the app's")  # e.g. a data dir pointed at a shared folder
    client, auth = daemon(data, home, keychain)
    with client:
        assert client.put("/api/secrets/anthropic", headers=auth, json={"api_key": KEY}).status_code == 204
        assert client.put("/api/autostart", headers=auth, json={"enabled": True}).json()["enabled"]
        assert client.post("/api/data/delete-all", headers=auth, json={"confirm": "nope"}).status_code == 422
        assert (
            client.post("/api/data/delete-all", headers=auth, json={"confirm": "DELETE"}).status_code == 202
        )
    assert sorted(p.name for p in data.iterdir()) == ["notes-of-mine.txt"]
    assert keychain.values.get(ANTHROPIC_KEY_NAME) is None
    assert not (home / ".config" / "autostart" / f"{APP_ID}.desktop").exists()


def test_delete_all_removes_the_folder_when_it_was_only_ours(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    client, auth = daemon(data, tmp_path / "home", MemoryBackend())
    with client:
        client.post("/api/data/delete-all", headers=auth, json={"confirm": "DELETE"})
    assert not data.exists()


def test_autostart_entries_per_os(tmp_path: Path) -> None:
    linux = Autostart(platform="linux", home=tmp_path, command=["/opt/app/bin/python", "-m", "core"])
    assert not linux.enabled()
    linux.enable()
    desktop = (tmp_path / ".config" / "autostart" / f"{APP_ID}.desktop").read_text()
    assert "Exec=/opt/app/bin/python -m core" in desktop and "Type=Application" in desktop
    linux.disable()
    assert not linux.enabled()

    mac = Autostart(platform="darwin", home=tmp_path, command=["/Apps/py", "-m", "core"])
    mac.enable()
    plist = plistlib.loads((tmp_path / "Library" / "LaunchAgents" / f"{APP_ID}.plist").read_bytes())
    assert plist == {"Label": APP_ID, "ProgramArguments": ["/Apps/py", "-m", "core"], "RunAtLoad": True}
    mac.disable()
    assert not mac.enabled()
    assert not Autostart(platform="sunos").supported


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_autostart_windows_registry() -> None:
    entry = Autostart(platform="win32", command=["C:\\py\\pythonw.exe", "-m", "core"])
    was = entry.enabled()
    entry.enable()
    assert entry.enabled()
    if not was:
        entry.disable()
        assert not entry.enabled()


def test_autostart_api(harness: Harness) -> None:
    state = harness.client.get("/api/autostart", headers=harness.auth).json()
    assert state == {"supported": True, "enabled": False}
    assert harness.client.put("/api/autostart", headers=harness.auth, json={"enabled": True}).json()[
        "enabled"
    ]
    assert not harness.client.put("/api/autostart", headers=harness.auth, json={"enabled": False}).json()[
        "enabled"
    ]


def test_cli_uninstall_when_the_app_is_not_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    data = tmp_path / "data"
    data.mkdir()
    paths.token_path(data).write_text("t")
    paths.db_path(data).write_text("db")
    keychain = MemoryBackend()
    keychain.set(ANTHROPIC_KEY_NAME, KEY)
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(data))
    monkeypatch.setattr(cli, "KeyringBackend", lambda: keychain)
    monkeypatch.setattr(cli, "Autostart", lambda: Autostart(platform="linux", home=tmp_path / "home"))
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert cli.uninstall(port=1, yes=False) == 1 and paths.db_path(data).exists()  # said no
    assert cli.uninstall(port=1, yes=True) == 0
    assert not data.exists() and keychain.values.get(ANTHROPIC_KEY_NAME) is None
    assert "pipx uninstall" in capsys.readouterr().out
