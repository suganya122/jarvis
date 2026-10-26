"""The real browser: Playwright driving Chrome/Edge/Chromium.

Isolated mode (default) uses its own persistent profile in the app data folder, headless
so it never takes the screen; handing over relaunches the same profile in a visible
window at the same page (cookies and logins carry over; unsaved form fields may not).
"Own browser" mode attaches to the user's Chrome over its local debugging port and works
in a new tab, so their logged-in sessions are used only when they opt in.
"""

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from contracts.browser import BrowserMode, PageElement, PageSnapshot

log = logging.getLogger(__name__)
NAV_TIMEOUT_MS = 30_000
ACTION_TIMEOUT_MS = 8_000
SETTLE_MS = 2_500
REF_ATTR = "data-assistant-ref"
HIGHLIGHT = "3px solid #f5b25b"

_SCRIPT = (Path(__file__).parent / "snapshot.js").read_text(encoding="utf-8")
SNAPSHOT_JS = _SCRIPT[_SCRIPT.index("() =>") :]  # without the header comment: Playwright must see a function


class BrowserUnavailable(Exception):
    pass


class PlaywrightBrowser:
    def __init__(
        self, mode: BrowserMode, profile_dir: Path, headless: bool, channel: str, cdp_url: str
    ) -> None:
        self._mode = mode
        self._profile = profile_dir
        self._headless = headless
        self._channel = channel
        self._cdp_url = cdp_url
        self._pw: Any = None
        self._context: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()

    # ---- lifecycle -------------------------------------------------------------

    async def _ensure(self) -> Any:
        if self._page is not None and not self._page.is_closed():
            return self._page
        if self._pw is None:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
        if self._mode == "own":
            try:
                browser = await self._pw.chromium.connect_over_cdp(self._cdp_url, timeout=5_000)
            except Exception as exc:
                raise BrowserUnavailable(
                    f"couldn't reach your Chrome at {self._cdp_url}: start it with --remote-debugging-port "
                    "(see Settings → Browser)"
                ) from exc
            self._context = browser.contexts[0] if browser.contexts else await browser.new_context()
        else:
            self._context = await self._launch(self._headless)
        self._context.set_default_timeout(ACTION_TIMEOUT_MS)
        self._context.on("page", self._adopt)  # links that open a new tab
        self._page = (
            await self._context.new_page()
            if self._mode == "own" or not self._context.pages
            else self._context.pages[0]
        )
        return self._page

    async def _launch(self, headless: bool) -> Any:
        self._profile.mkdir(parents=True, exist_ok=True)
        channels = [self._channel] if self._channel != "auto" else ["chrome", "msedge", None]
        last: Exception | None = None
        for channel in channels:
            try:
                return await self._pw.chromium.launch_persistent_context(
                    str(self._profile),
                    headless=headless,
                    channel=channel or None,
                    viewport={"width": 1280, "height": 900},
                    accept_downloads=False,
                    args=["--no-first-run", "--no-default-browser-check"],
                )
            except Exception as exc:  # this channel isn't installed (or failed): try the next
                first_line = str(exc).splitlines()[0][:300]
                log.warning("browser %s didn't start: %s", channel or "chromium", first_line)
                last = exc
        detail = str(last).strip().splitlines()[0][:200] if last else ""
        log.warning("browser launch failed: %s", last)
        raise BrowserUnavailable(
            "no browser could be started (install Google Chrome or Microsoft Edge, or run "
            f"`python -m playwright install chromium`): {detail}"
        ) from last

    def _adopt(self, page: Any) -> None:
        self._page = page

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            if self._mode == "isolated" and self._context is not None:
                await self._context.close()
            elif self._page is not None:
                await self._page.close()  # own browser: close only our tab
        with contextlib.suppress(Exception):
            if self._pw is not None:
                await self._pw.stop()
        self._pw = self._context = self._page = None

    # ---- Browser -----------------------------------------------------------------

    async def snapshot(self) -> PageSnapshot:
        page = await self._ensure()
        data = await page.evaluate(SNAPSHOT_JS)
        frames = tuple(dict.fromkeys([*data["frames"], *(f.url for f in page.frames if f.url)]))
        elements = tuple(PageElement.model_validate(e) for e in data["elements"])
        return PageSnapshot(url=data["url"], title=data["title"][:200], text=data["text"], elements=elements,
                            frames=frames)  # fmt: skip

    async def _settle(self, page: Any) -> None:
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=SETTLE_MS)

    async def open(self, url: str) -> PageSnapshot:
        async with self._lock:
            page = await self._ensure()
            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await self._settle(page)
            return await self.snapshot()

    def _el(self, page: Any, ref: str) -> Any:
        return page.locator(f'[{REF_ATTR}="{ref}"]')

    async def click(self, ref: str) -> PageSnapshot:
        async with self._lock:
            page = await self._ensure()
            await self._el(page, ref).click()
            await self._settle(self._page)
            return await self.snapshot()

    async def fill(self, ref: str, text: str, submit: bool) -> PageSnapshot:
        async with self._lock:
            page = await self._ensure()
            target = self._el(page, ref)
            await target.fill(text)
            if submit:
                await target.press("Enter")
                await self._settle(self._page)
            return await self.snapshot()

    async def select(self, ref: str, option: str) -> PageSnapshot:
        async with self._lock:
            page = await self._ensure()
            await self._el(page, ref).select_option(label=option)
            return await self.snapshot()

    async def back(self) -> PageSnapshot:
        async with self._lock:
            page = await self._ensure()
            await page.go_back(wait_until="domcontentloaded")
            await self._settle(page)
            return await self.snapshot()

    async def _reopen(self, url: str) -> None:
        self._context.on("page", self._adopt)
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        if url and url != "about:blank":
            await self._page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)

    async def show(self, highlight: dict[str, str] | None) -> None:
        async with self._lock:
            page = await self._ensure()
            if self._mode == "isolated" and self._headless:
                url = page.url
                await self._context.close()
                try:
                    self._context = await self._launch(False)
                    self._headless = False  # stays visible until closed, so the user can finish
                except BrowserUnavailable:
                    self._context = await self._launch(True)  # keep working where we were
                    await self._reopen(url)
                    raise BrowserUnavailable(
                        "couldn't open a visible browser window on this desktop"
                    ) from None
                await self._reopen(url)
            await self._page.bring_to_front()
            if highlight:
                snap = await self.snapshot()
                match = next((e for e in snap.elements if e.locator() == highlight), None)
                if match is not None:
                    target = self._el(self._page, match.ref)
                    await target.scroll_into_view_if_needed()
                    await target.evaluate(
                        f"el => {{ el.style.outline = '{HIGHLIGHT}'; el.style.outlineOffset = '2px'; }}"
                    )
