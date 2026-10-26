"""Model downloads: resume, checksum, safe extraction, progress (no real network)."""

import hashlib
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from contracts.ws import DownloadProgress
from core.model_files import CATALOG, DownloadError, ModelFiles, ModelSpec, recommended_tier, safe_extract


def model_zip(folder: str = "m", extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{folder}/conf/model.conf", "x")
        zf.writestr(f"{folder}/am/final.mdl", b"\0" * 50_000)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def spec_for(data: bytes, sha: str | None = None) -> ModelSpec:
    return ModelSpec(id="test-model", title="t", url="https://models.example/m.zip",
                     sha256=sha or hashlib.sha256(data).hexdigest(), size=len(data), license="Apache-2.0",
                     tier="small", folder="m")  # fmt: skip


class Server:
    def __init__(self, data: bytes, honor_range: bool = True) -> None:
        self.data = data
        self.honor_range = honor_range
        self.ranges: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        header = request.headers.get("range")
        self.ranges.append(header)
        if header and self.honor_range:
            start = int(header.removeprefix("bytes=").rstrip("-"))
            return httpx.Response(206, content=self.data[start:])
        return httpx.Response(200, content=self.data)


async def run(files: ModelFiles, spec: ModelSpec) -> list[DownloadProgress]:
    seen: list[DownloadProgress] = []

    async def report(progress: DownloadProgress) -> None:
        seen.append(progress)

    assert files.start(spec, report)
    await files.wait(spec.id)
    return seen


async def test_download_verifies_and_installs(tmp_path: Path) -> None:
    data = model_zip()
    server = Server(data)
    files = ModelFiles(tmp_path, transport=httpx.MockTransport(server))
    states = [p.state for p in await run(files, spec_for(data))]
    assert states[-3:] == ["verifying", "installing", "done"]
    assert files.installed(spec_for(data)) and not (tmp_path / "test-model.part").exists()


async def test_resume_uses_http_range(tmp_path: Path) -> None:
    data = model_zip()
    (tmp_path / "test-model.part").write_bytes(data[:20_000])
    server = Server(data)
    files = ModelFiles(tmp_path, transport=httpx.MockTransport(server))
    assert (await run(files, spec_for(data)))[-1].state == "done"
    assert server.ranges == ["bytes=20000-"]


async def test_server_ignoring_range_restarts_cleanly(tmp_path: Path) -> None:
    data = model_zip()
    (tmp_path / "test-model.part").write_bytes(b"garbage" * 1000)
    files = ModelFiles(tmp_path, transport=httpx.MockTransport(Server(data, honor_range=False)))
    assert (await run(files, spec_for(data)))[-1].state == "done"


async def test_checksum_mismatch_installs_nothing(tmp_path: Path) -> None:
    data = model_zip()
    files = ModelFiles(tmp_path, transport=httpx.MockTransport(Server(data)))
    last = (await run(files, spec_for(data, sha="0" * 64)))[-1]
    assert last.state == "error" and "checksum" in last.error
    assert not files.installed(spec_for(data)) and not (tmp_path / "test-model.part").exists()


async def test_http_errors_are_reported(tmp_path: Path) -> None:
    files = ModelFiles(tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    last = (await run(files, spec_for(model_zip())))[-1]
    assert last.state == "error" and "404" in last.error


def test_zip_slip_is_refused(tmp_path: Path) -> None:
    evil = tmp_path / "evil.zip"
    evil.write_bytes(model_zip(extra={"../../escape.txt": b"x"}))
    (tmp_path / "out").mkdir()
    with pytest.raises(DownloadError, match="unsafe path"):
        safe_extract(evil, tmp_path / "out")
    assert not (tmp_path.parent / "escape.txt").exists()


def test_catalog_is_permissive_and_tiered() -> None:
    assert {m.license for m in CATALOG} == {"Apache-2.0"}
    assert {m.tier for m in CATALOG} == {"small", "standard"}
    assert all(len(m.sha256) == 64 and m.url.startswith("https://") for m in CATALOG)
    assert recommended_tier() in {"small", "standard"}
