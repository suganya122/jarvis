"""Local model catalog and downloads: user-started only, resumable, checksum-verified.

Nothing is ever downloaded silently. Every entry records its license; only permissively
licensed models are listed.
"""

import asyncio
import hashlib
import os
import shutil
import sys
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from contracts.ws import DownloadProgress

Tier = Literal["small", "standard"]
PROGRESS_INTERVAL_S = 0.25


@dataclass(frozen=True)
class ModelSpec:
    id: str
    title: str
    url: str
    sha256: str
    size: int
    license: str
    tier: Tier
    folder: str
    """Top-level folder inside the archive."""


CATALOG = (
    ModelSpec(
        id="vosk-en-small",
        title="English, small (fast; for older computers)",
        url="https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        sha256="30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498",
        size=41_205_931,
        license="Apache-2.0",
        tier="small",
        folder="vosk-model-small-en-us-0.15",
    ),
    ModelSpec(
        id="vosk-en-standard",
        title="English, standard (more accurate)",
        url="https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip",
        sha256="d9838b4aaa82a75c4a17f5aca300eaca129aaab2a7cbf951bafbb500eb9c4334",
        size=130_557_655,
        license="Apache-2.0",
        tier="standard",
        folder="vosk-model-en-us-0.22-lgraph",
    ),
)
BY_ID = {m.id: m for m in CATALOG}


def memory_gb() -> float:
    if sys.platform == "win32":
        return 8.0  # no sysconf on Windows: assume a typical machine
    try:
        return float(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / 1e9
    except (ValueError, OSError):
        return 8.0


def recommended_tier() -> Tier:
    """Small models on low-end machines; everything degrades gracefully without a GPU."""
    return "standard" if memory_gb() >= 8 and (os.cpu_count() or 1) >= 4 else "small"


def recommended_model() -> ModelSpec:
    tier = recommended_tier()
    return next(m for m in CATALOG if m.tier == tier)


class DownloadError(Exception):
    pass


def safe_extract(archive: Path, dest: Path) -> None:
    """Unzip, refusing any entry that would land outside dest (zip-slip) or is a link."""
    root = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            target = (root / info.filename).resolve()
            if not target.is_relative_to(root):
                raise DownloadError(f"unsafe path in archive: {info.filename}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise DownloadError(f"links aren't allowed in model archives: {info.filename}")
        zf.extractall(root)


def _size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


Report = Callable[[DownloadProgress], Awaitable[None]]


class ModelFiles:
    def __init__(self, root: Path, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._transport = transport
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def path(self, spec: ModelSpec) -> Path:
        return self.root / spec.id

    def installed(self, spec: ModelSpec) -> bool:
        return (self.path(spec) / "conf").is_dir()

    def downloading(self, model_id: str) -> bool:
        task = self._tasks.get(model_id)
        return task is not None and not task.done()

    def start(self, spec: ModelSpec, report: Report) -> bool:
        if self.downloading(spec.id) or self.installed(spec):
            return False
        task = asyncio.create_task(self._download(spec, report))
        self._tasks[spec.id] = task
        return True

    async def wait(self, model_id: str) -> None:
        task = self._tasks.get(model_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    def delete(self, spec: ModelSpec) -> None:
        shutil.rmtree(self.path(spec), ignore_errors=True)
        (self.root / f"{spec.id}.part").unlink(missing_ok=True)

    async def _download(self, spec: ModelSpec, report: Report) -> None:
        part = self.root / f"{spec.id}.part"
        try:
            await self._fetch(spec, part, report)
            await report(
                DownloadProgress(model_id=spec.id, done=spec.size, total=spec.size, state="verifying")
            )
            if await asyncio.to_thread(_sha256, part) != spec.sha256:
                part.unlink(missing_ok=True)
                raise DownloadError("the download was corrupted (checksum mismatch); try again")
            await report(
                DownloadProgress(model_id=spec.id, done=spec.size, total=spec.size, state="installing")
            )
            await asyncio.to_thread(self._install, spec, part)
            await report(DownloadProgress(model_id=spec.id, done=spec.size, total=spec.size, state="done"))
        except (DownloadError, httpx.HTTPError, OSError, zipfile.BadZipFile) as exc:
            message = (
                str(exc) if isinstance(exc, DownloadError) else f"download failed ({type(exc).__name__})"
            )
            await report(
                DownloadProgress(model_id=spec.id, done=0, total=spec.size, state="error", error=message)
            )

    async def _fetch(self, spec: ModelSpec, part: Path, report: Report) -> None:
        have = await asyncio.to_thread(_size, part)
        if have > spec.size:
            await asyncio.to_thread(part.unlink)
            have = 0
        if have == spec.size:
            return
        headers = {"Range": f"bytes={have}-"} if have else {}
        async with (
            httpx.AsyncClient(transport=self._transport, follow_redirects=True, timeout=60) as client,
            client.stream("GET", spec.url, headers=headers) as response,
        ):
            if response.status_code == 200:
                have = 0  # the server ignored Range: start over
            elif response.status_code != 206:
                raise DownloadError(f"the model server answered HTTP {response.status_code}")
            last = 0.0
            with part.open("ab" if have else "wb") as fh:
                async for chunk in response.aiter_bytes(1 << 16):
                    fh.write(chunk)
                    have += len(chunk)
                    if have > spec.size:
                        raise DownloadError("the download is larger than expected")
                    if time.monotonic() - last > PROGRESS_INTERVAL_S:
                        last = time.monotonic()
                        await report(DownloadProgress(model_id=spec.id, done=have, total=spec.size,
                                                      state="downloading"))  # fmt: skip
        if have != spec.size:
            raise DownloadError("the download stopped early; start it again to resume")

    def _install(self, spec: ModelSpec, part: Path) -> None:
        staging = self.root / f"{spec.id}.staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        try:
            safe_extract(part, staging)
            inner = staging / spec.folder
            if not (inner / "conf").is_dir():
                raise DownloadError("the archive doesn't contain the expected model")
            target = self.path(spec)
            shutil.rmtree(target, ignore_errors=True)
            inner.rename(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        part.unlink(missing_ok=True)
