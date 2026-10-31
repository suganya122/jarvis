"""The real OS-tools worker process, driven through WorkerHost (no OS mocks)."""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from contracts.common import Capability, RiskLevel, Trust
from contracts.worker import WorkerConfig
from core.worker_host import WorkerHost, worker_env
from workers.os_tools.paths import PathDenied, PathGuard
from workers.os_tools.shell_tool import HARD_BLOCKED

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX programs")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "allowed" / "docs").mkdir(parents=True)
    (tmp_path / "allowed" / "secret").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "allowed" / "docs" / "a.txt").write_bytes(b"hello\n")  # exact bytes on every OS (no CRLF)
    (tmp_path / "outside" / "x.txt").write_text("nope")
    return tmp_path


def config(root: Path, **overrides: object) -> WorkerConfig:
    values: dict[str, object] = {
        "allowed_roots": (str(root / "allowed"),),
        "denied_paths": (str(root / "allowed" / "secret"),),
        "shell_allowlist": ("ls", "echo", "sleep", "cat"),
        "max_output_chars": 2000,
        "command_timeout_s": 5,
    }
    values.update(overrides)
    return WorkerConfig.model_validate(values)


@pytest.fixture
async def host(root: Path) -> AsyncIterator[WorkerHost]:
    worker = WorkerHost("os_tools", "workers.os_tools", call_timeout_s=20)
    await worker.start(config(root))
    yield worker
    await worker.stop()


# ---- path guard (pure) ----------------------------------------------------------


def test_path_guard(root: Path) -> None:
    guard = PathGuard([str(root / "allowed")], [str(root / "allowed" / "secret")])
    assert guard.resolve("docs/a.txt") == (root / "allowed" / "docs" / "a.txt").resolve()
    for bad in (str(root / "outside" / "x.txt"), "../outside/x.txt", str(root / "allowed" / "secret"), ""):
        with pytest.raises(PathDenied):
            guard.resolve(bad)


@posix_only
def test_path_guard_blocks_symlink_escape(root: Path) -> None:
    os.symlink(root / "outside", root / "allowed" / "link")
    guard = PathGuard([str(root / "allowed")], [])
    with pytest.raises(PathDenied):
        guard.resolve("link/x.txt")


def test_worker_env_has_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("ASSISTANT_DATA_DIR", "/x")
    env = worker_env()
    assert not {"ANTHROPIC_API_KEY", "GITHUB_TOKEN", "ASSISTANT_DATA_DIR"} & env.keys()


# ---- worker process -------------------------------------------------------------


async def test_lists_and_reads_as_untrusted(host: WorkerHost, root: Path) -> None:
    names = {t.name for t in host.tools}
    assert {"list_directory", "read_text_file", "write_text_file", "run_command", "run_shell", "open_url",
            "open_path", "system_info", "open_app", "arrange_windows", "media_control", "file_info",
            "desktop_notify", *(["mac_automation"] if sys.platform == "darwin" else [])} == names  # fmt: skip
    listing = await host.invoke("list_directory", {"path": str(root / "allowed")})
    assert listing.ok and "docs/" in listing.output and listing.trust is Trust.UNTRUSTED
    read = await host.invoke("read_text_file", {"path": str(root / "allowed" / "docs" / "a.txt")})
    assert read.output == "hello\n" and read.trust is Trust.UNTRUSTED


async def test_confinement_applies_to_every_tool(host: WorkerHost, root: Path) -> None:
    outside = str(root / "outside" / "x.txt")
    for tool, args in [
        ("read_text_file", {"path": outside}),
        ("list_directory", {"path": str(root / "allowed" / "secret")}),
        ("write_text_file", {"path": outside, "content": "x"}),
        ("open_path", {"path": outside}),
    ]:
        assessment = await host.assess(tool, args)
        assert assessment.error, tool
        result = await host.invoke(tool, args)  # invoke re-checks even without assess
        assert not result.ok, tool
    assert (root / "outside" / "x.txt").read_text() == "nope"


async def test_write_create_then_overwrite_is_destructive(host: WorkerHost, root: Path) -> None:
    target = str(root / "allowed" / "docs" / "new.txt")
    created = await host.assess("write_text_file", {"path": target, "content": "v1"})
    assert created.risk is RiskLevel.WRITE and not created.error
    assert (await host.invoke("write_text_file", {"path": target, "content": "v1"})).ok
    refused = await host.assess("write_text_file", {"path": target, "content": "v2"})
    assert refused.error and "overwrite" in refused.error
    replace = await host.assess("write_text_file", {"path": target, "content": "v2", "overwrite": True})
    assert replace.risk is RiskLevel.DESTRUCTIVE and not replace.error
    assert (await host.invoke("write_text_file", {"path": target, "content": "v2", "overwrite": True})).ok
    assert await asyncio.to_thread(Path(target).read_text) == "v2"


async def test_a_file_that_starts_by_itself_later_counts_as_a_routine(host: WorkerHost, root: Path) -> None:
    agents = root / "allowed" / "LaunchAgents"
    await asyncio.to_thread(agents.mkdir)
    later = await host.assess("write_text_file", {"path": str(agents / "x.plist"), "content": "<plist/>"})
    assert later.capabilities == (Capability.SCHEDULE,) and not later.error
    note = str(root / "allowed" / "docs" / "n.txt")
    plain = await host.assess("write_text_file", {"path": note, "content": "x"})
    assert plain.capabilities == ()


async def test_invalid_args_are_rejected(host: WorkerHost) -> None:
    assessment = await host.assess("list_directory", {"path": "~", "rm": True})
    assert assessment.error and "invalid arguments" in assessment.error


@posix_only
async def test_shell_allowlist_and_hard_block(host: WorkerHost, root: Path) -> None:
    cwd = str(root / "allowed")
    ok = await host.invoke("run_command", {"argv": ["echo", "hi"], "cwd": cwd})
    assert ok.ok and "hi" in ok.output and ok.trust is Trust.UNTRUSTED
    for argv, needle in [
        (["rm", "-rf", "docs"], "never allowed"),
        (["sudo", "ls"], "never allowed"),
        (["bash", "-c", "ls"], "never allowed"),
        (["grep", "x"], "allowlist"),
        (["/bin/ls"], "program name only"),
        (["cat", str(root / "outside" / "x.txt")], "outside"),
        (["cat", "../outside/x.txt"], "outside"),
        (["cat", "secret"], "protected"),
    ]:
        assessment = await host.assess("run_command", {"argv": argv, "cwd": cwd})
        assert assessment.error and needle in assessment.error, (argv, assessment.error)
    assert (root / "allowed" / "docs").exists()


def test_hard_block_cannot_be_allowlisted(root: Path) -> None:
    assert {"rm", "sudo", "dd", "shutdown", "bash", "python", "curl"} <= HARD_BLOCKED


@posix_only
async def test_command_timeout_and_cancel(root: Path) -> None:
    worker = WorkerHost("os_tools", "workers.os_tools")
    await worker.start(config(root, command_timeout_s=1))
    try:
        timed_out = await worker.invoke(
            "run_command", {"argv": ["sleep", "30"], "cwd": str(root / "allowed")}
        )
        assert not timed_out.ok and "timeout" in timed_out.output
        task = asyncio.create_task(
            worker.invoke("run_command", {"argv": ["sleep", "30"], "cwd": str(root / "allowed")})
        )
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await worker.invoke("system_info", {})).ok  # worker still healthy
    finally:
        await worker.stop()


async def test_open_tools_refuse_unsafe_targets(host: WorkerHost, root: Path) -> None:
    script = root / "allowed" / "run.sh"
    script.write_text("echo hi")
    assert (await host.assess("open_path", {"path": str(script)})).error
    assert (await host.assess("open_url", {"url": "file:///etc/passwd"})).error
    assert (await host.assess("open_url", {"url": "javascript:alert(1)"})).error
    ok = await host.assess("open_url", {"url": "https://example.com/a"})
    assert not ok.error and "example.com" in ok.summary


async def test_worker_crash_is_contained_and_restarts(host: WorkerHost) -> None:
    assert host._proc is not None
    host._proc.kill()
    await asyncio.to_thread(host._proc.wait)
    await asyncio.sleep(0.1)
    result = await host.invoke("system_info", {})  # transparently restarted
    assert result.ok and "cpus" in result.output


def test_full_stack_daemon_uses_worker_tools(root: Path, tmp_path: Path) -> None:
    import json

    from core.config import Settings
    from models.fake import FakeProvider, FakeStep
    from tests.conftest import handshake, make_harness

    settings = Settings()
    settings.tools.allowed_roots = [str(root / "allowed")]
    provider = FakeProvider(
        script=[
            FakeStep(tool_calls=[("list_directory", {"path": str(root / "allowed" / "docs")})]),
            FakeStep(text="ok"),
        ]
    )
    data = tmp_path / "data"
    data.mkdir()
    for harness in make_harness(data, provider, start_workers=True, settings=settings):
        assert harness.client.get("/api/status", headers=harness.auth).json()["os_tools"] is True
        offered = {t["name"] for t in harness.client.get("/api/tools", headers=harness.auth).json()}
        assert "open_url" in offered and not any(
            n.startswith("browser_") for n in offered
        )  # no browser of its own
        with harness.ws() as ws:
            handshake(ws)
            ws.send_text(json.dumps({"type": "chat.send", "request_id": "r1", "text": "list docs"}))
            while json.loads(ws.receive_text())["type"] != "chat.done":
                pass
        result = provider.requests[-1].messages[-1].tool_results[0]
        assert "a.txt" in result.output and result.output.startswith("<untrusted_content>")


async def test_file_info_reports_status_inside_allowed_folders_only(host: WorkerHost, root: Path) -> None:
    present = await host.invoke("file_info", {"path": str(root / "allowed" / "docs" / "a.txt")})
    assert present.ok and present.evidence["exists"] is True and present.evidence["size"] == 6
    missing = await host.invoke("file_info", {"path": str(root / "allowed" / "docs" / "later.pdf")})
    assert missing.ok and missing.evidence["exists"] is False
    assert not (await host.invoke("file_info", {"path": str(root / "outside" / "x.txt")})).ok


def test_desktop_notification_text_is_never_a_script(monkeypatch: pytest.MonkeyPatch) -> None:
    from contracts import APP_NAME
    from workers.os_tools import notify_tools

    hostile = '"; do shell script "rm -rf ~" --'
    monkeypatch.setattr(notify_tools.sys, "platform", "darwin")
    argv = notify_tools._command(hostile, "body")
    assert argv is not None and argv[-2:] == [hostile, "body"] and hostile not in argv[2]
    monkeypatch.setattr(notify_tools.sys, "platform", "linux")
    monkeypatch.setattr(notify_tools.shutil, "which", lambda _: "/usr/bin/notify-send")
    assert notify_tools._command(hostile, "$(id)") == [
        "notify-send",
        f"--app-name={APP_NAME}",
        hostile,
        "$(id)",
    ]
