"""The real browser worker (Playwright + Chrome/Chromium) against the local test airline.

Skipped when no browser is installed. The final hand-over opens a visible window, so the
worker gets a private Xvfb display on Linux.
"""

import asyncio
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from contracts.worker import BrowserConfig, WorkerConfig
from core.worker_host import WorkerHost
from tests.a11y_session import _free_display
from tests.fake_airline import FakeAirline


def _browser_available() -> bool:
    cache = Path.home() / ".cache" / "ms-playwright"
    return bool(
        shutil.which("google-chrome")
        or shutil.which("msedge")
        or (cache.exists() and any(cache.glob("chromium-*")))
    )


pytestmark = pytest.mark.skipif(
    not _browser_available(), reason="no Chrome, Edge or Playwright Chromium installed"
)


@pytest.fixture
def airline() -> Iterator[FakeAirline]:
    with FakeAirline() as site:
        yield site


@pytest.fixture
def display() -> Iterator[dict[str, str]]:
    if sys.platform != "linux" or not shutil.which("Xvfb"):
        yield {}
        return
    n = _free_display()
    xvfb = subprocess.Popen(
        ["Xvfb", f":{n}", "-nolisten", "tcp", "-screen", "0", "1280x900x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)
    yield {"DISPLAY": f":{n}", "WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"}
    xvfb.terminate()
    xvfb.wait(5)


def config(tmp_path: Path, **browser: object) -> WorkerConfig:
    # Chrome keeps a socket in the profile folder and socket paths are short (~107 bytes),
    # so test profiles get a short path of their own (the real one is in the app data folder).
    profile = tempfile.mkdtemp(prefix="br-")
    settings = {
        "profile_dir": profile,
        "allow_private_hosts": True,
        "min_seconds_between_pages": 0.2,
    } | browser
    return WorkerConfig(
        allowed_roots=(),
        denied_paths=(),
        shell_allowlist=(),
        max_output_chars=8000,
        command_timeout_s=30,
        browser=BrowserConfig.model_validate(settings),
    )


@pytest.fixture
async def worker(tmp_path: Path, display: dict[str, str]) -> AsyncIterator[WorkerHost]:
    host = WorkerHost("browser", "workers.browser", call_timeout_s=60, extra_env=display)
    await host.start(config(tmp_path))
    yield host
    await host.invoke("browser_close", {})
    await host.stop()


def ref(page: str, kind: str, name: str) -> str:
    m = re.search(rf"\[(e\d+)\] {kind} “{re.escape(name)}”", page)
    assert m, f"no {kind} “{name}” in:\n{page}"
    return m.group(1)


async def test_booking_flow_in_real_chrome_stops_before_paying(
    worker: WorkerHost, airline: FakeAirline
) -> None:
    page = (await worker.invoke("browser_open", {"url": airline.url})).output
    assert "Page: SkyCheap: find flights" in page and "options: Economy, Business" in page
    await worker.invoke("browser_type", {"ref": ref(page, "textbox", "From"), "text": "BLR"})
    await worker.invoke("browser_type", {"ref": ref(page, "textbox", "To"), "text": "GOI"})
    page = (await worker.invoke("browser_click", {"ref": ref(page, "button", "Search flights")})).output
    assert airline.searches == ["BLR-GOI"] and "BLR → GOI" in page
    pay_now = ref(page, "button", "Pay now")
    assert "(final step: use browser_finish; the user clicks it)" in page  # the injected "Pay now" is flagged
    blocked = await worker.assess("browser_click", {"ref": pay_now})
    assert blocked.error and "commits you" in blocked.error
    refused = await worker.invoke("browser_click", {"ref": pay_now})  # even straight to the worker
    assert not refused.ok and airline.paid == 0

    page = (await worker.invoke("browser_click", {"ref": ref(page, "link", "Select 6E 201 ₹3,980")})).output
    await worker.invoke("browser_type", {"ref": ref(page, "textbox", "Full name"), "text": "Ann Lee"})
    await worker.invoke("browser_type", {"ref": ref(page, "textbox", "Email"), "text": "ann@example.com"})
    cont = ref(page, "button", "Continue to payment")
    assert (
        await worker.assess("browser_click", {"ref": cont})
    ).risk == "external_send"  # sends details: asks
    page = (await worker.invoke("browser_click", {"ref": cont})).output
    assert "Page: Payment" in page

    card = await worker.assess("browser_type", {"ref": ref(page, "textbox", "Card number"), "text": "4111"})
    assert card.error and "never type" in card.error
    assert (await worker.assess("browser_type", {"ref": ref(page, "textbox", "CVV"), "text": "123"})).error
    final = ref(page, "button", "Pay ₹3,980")
    finish = {"ref": final, "what": "BLR→GOI 6E 201", "price": "₹3,980", "refund_terms": "non-refundable"}
    assessed = await worker.assess("browser_finish", finish)
    assert assessed.risk == "purchase" and assessed.summary.startswith(
        "Final step on 127.0.0.1: “Pay ₹3,980”"
    )
    handed = await worker.invoke("browser_finish", finish)
    assert handed.ok and "won't click it" in handed.output
    page = (await worker.invoke("browser_read", {})).output  # the visible window, same page, nothing paid
    assert "Page: Payment" in page and airline.paid == 0


async def test_captcha_and_passwords_in_real_chrome(worker: WorkerHost, airline: FakeAirline) -> None:
    captcha = await worker.invoke("browser_open", {"url": f"{airline.url}/captcha"})
    assert "PAUSED: the site shows a CAPTCHA" in captcha.output and captcha.evidence["handover"]
    login = (await worker.invoke("browser_open", {"url": f"{airline.url}/login"})).output
    assert "prefilled-secret" not in login  # password values never reach the model
    assert "ann@example.com" in login
    assert "(a password field: the user types this)" in login


async def test_addresses_are_checked_before_loading(tmp_path: Path) -> None:
    host = WorkerHost("browser", "workers.browser", call_timeout_s=30)
    await host.start(config(tmp_path, allow_private_hosts=False))
    try:
        assessed = await host.assess("browser_open", {"url": "http://192.168.1.1/admin"})
        assert assessed.error and "local network" in assessed.error
        result = await host.invoke("browser_open", {"url": "file:///etc/passwd"})
        assert not result.ok and "http and https" in result.output
    finally:
        await host.stop()


@pytest.mark.skipif(not shutil.which("google-chrome"), reason="needs Google Chrome")
async def test_own_browser_mode_uses_a_new_tab_and_leaves_the_rest(
    tmp_path: Path, airline: FakeAirline
) -> None:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    chrome = subprocess.Popen(  # noqa: ASYNC220  (test setup: start the user's Chrome stand-in)
        [
            "google-chrome",
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={tmp_path / 'mine'}",
            "--no-first-run",
            f"{airline.url}/login",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    host = WorkerHost("browser", "workers.browser", call_timeout_s=30)
    try:
        await host.start(config(tmp_path, mode="own", cdp_url=f"http://127.0.0.1:{port}"))
        for _ in range(50):
            result = await host.invoke("browser_open", {"url": airline.url})
            if result.ok:
                break
            await asyncio.sleep(0.2)
        assert "SkyCheap" in result.output
        # in the user's own (logged-in) browser, actions are "changes something", not "read"
        assert (await host.assess("browser_open", {"url": airline.url})).risk == "write"
        await host.invoke("browser_close", {})
        assert chrome.poll() is None  # their browser keeps running
    finally:
        await host.stop()
        chrome.terminate()
        chrome.wait(10)
