"""A tiny stand-in for the Telegram Bot API, for tests (no network, no real bot)."""

import asyncio
import itertools
import socket
import threading
import time
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

TOKEN = "123456:TEST-token-abcdefghijklmnop"


class FakeTelegram:
    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.updates: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        app = Starlette(routes=[
            Route("/bot{token}/{method}", self._handle, methods=["POST"]),
            # Test control (for driving it from another process, e.g. a browser test).
            Route("/_test/say", self._say, methods=["POST"]),
            Route("/_test/tap", self._tap, methods=["POST"]),
            Route("/_test/calls", self._calls, methods=["GET"]),
        ])  # fmt: skip
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> "FakeTelegram":
        self._thread.start()
        while not self._server.started:
            time.sleep(0.01)
        return self

    def __exit__(self, *_: object) -> None:
        self._server.should_exit = True
        self._thread.join(5)

    # ---- what the test does as "the user" -----------------------------------------

    def user_says(self, text: str, user_id: int = 42, chat_type: str = "private") -> None:
        with self._lock:
            self.updates.append({"update_id": next(self._ids), "message": {
                "message_id": next(self._ids), "text": text,
                "chat": {"id": user_id, "type": chat_type},
                "from": {"id": user_id, "is_bot": False, "username": "ann"}}})  # fmt: skip

    def user_taps(self, data: str, user_id: int = 42) -> None:
        with self._lock:
            self.updates.append({"update_id": next(self._ids), "callback_query": {
                "id": str(next(self._ids)), "data": data, "from": {"id": user_id}}})  # fmt: skip

    def sent(self, method: str = "sendMessage") -> list[dict[str, Any]]:
        return [params for name, params in list(self.calls) if name == method]

    def wait_for(self, method: str, count: int = 1, timeout: float = 10) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(found := self.sent(method)) >= count:
                return found
            time.sleep(0.02)
        raise AssertionError(f"no {method} x{count}; calls: {[c[0] for c in self.calls]}")

    async def _say(self, request: Request) -> JSONResponse:
        body = await request.json()
        self.user_says(body["text"], int(body.get("user_id", 42)))
        return JSONResponse({"ok": True})

    async def _tap(self, request: Request) -> JSONResponse:
        body = await request.json()
        self.user_taps(body["data"], int(body.get("user_id", 42)))
        return JSONResponse({"ok": True})

    async def _calls(self, request: Request) -> JSONResponse:
        return JSONResponse([[name, params] for name, params in list(self.calls) if name != "getUpdates"])

    # ---- the API ----------------------------------------------------------------------

    async def _handle(self, request: Request) -> JSONResponse:
        if request.path_params["token"] != self.token:
            return JSONResponse({"ok": False, "description": "Unauthorized"}, status_code=401)
        method = request.path_params["method"]
        params = await request.json() if await request.body() else {}
        self.calls.append((method, params))
        if method == "getMe":
            return JSONResponse({"ok": True, "result": {"id": 1, "is_bot": True, "username": "test_bot"}})
        if method == "getUpdates":
            deadline = time.monotonic() + min(float(params.get("timeout", 0)), 0.3)
            while True:
                with self._lock:
                    ready = [u for u in self.updates if u["update_id"] >= params.get("offset", 0)]
                if ready or time.monotonic() >= deadline:
                    return JSONResponse({"ok": True, "result": ready})
                await asyncio.sleep(0.02)
        if method == "sendMessage":
            return JSONResponse({"ok": True, "result": {"message_id": next(self._ids)}})
        return JSONResponse({"ok": True, "result": True})


if __name__ == "__main__":  # python -m tests.fake_telegram  (prints its URL, runs until killed)
    with FakeTelegram() as fake:
        print(fake.url, flush=True)
        threading.Event().wait()
