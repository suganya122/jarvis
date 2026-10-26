"""Browser worker state: one browser (started on first use), the page as last seen, and
per-site politeness timing."""

import asyncio
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

from contracts import APP_ID
from contracts.browser import Browser, PageElement, PageSnapshot
from contracts.worker import BrowserConfig, WorkerConfig
from workers.common.base import Args
from workers.common.base import WorkerTool as _WorkerTool

__all__ = ["WORKER_NAME", "Args", "Env", "WorkerTool"]

WORKER_NAME = "browser"
BrowserFactory = Callable[[BrowserConfig], Browser]


def _playwright(config: BrowserConfig) -> Browser:
    from workers.browser.pw import PlaywrightBrowser

    return PlaywrightBrowser(
        config.mode, Path(config.profile_dir), config.headless, config.channel, config.cdp_url
    )


class Env:
    factory: ClassVar[BrowserFactory] = _playwright
    """Tests swap in the fake browser here."""

    def __init__(self, config: WorkerConfig) -> None:
        fallback = BrowserConfig(profile_dir=str(Path(tempfile.gettempdir()) / f"{APP_ID}-browser"))
        self.config = config.browser or fallback
        self._browser: Browser | None = None
        self.page: PageSnapshot | None = None
        self._last_load: dict[str, float] = {}

    @property
    def browser(self) -> Browser:
        if self._browser is None:
            self._browser = type(self).factory(self.config)
        return self._browser

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        self._browser, self.page = None, None

    async def polite(self, url: str) -> None:
        """At most one page load per site every `min_seconds_between_pages`."""
        host = urlparse(url).hostname or ""
        wait = self._last_load.get(host, 0) + self.config.min_seconds_between_pages - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_load[host] = time.monotonic()

    def find(self, ref: str | dict[str, str]) -> PageElement | None:
        """By this page's ref ('e12'), or by a stable locator ({'kind', 'name'}) from a skill."""
        if self.page is None:
            return None
        for el in self.page.elements:
            if (isinstance(ref, str) and el.ref == ref) or (isinstance(ref, dict) and el.locator() == ref):
                return el
        return None

    @property
    def host(self) -> str:
        return urlparse(self.page.url).hostname or "" if self.page else ""


class WorkerTool[A: Args](_WorkerTool[A, Env]):
    source: ClassVar[str] = WORKER_NAME
