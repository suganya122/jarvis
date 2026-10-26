"""Local server hardening: Host/Origin validation, token auth, rate limits, headers.

Implemented as raw ASGI middleware so it covers WebSockets as well as HTTP.
No CORS headers are ever sent, so cross-origin pages can't read responses.
"""

import json
from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from contracts.ws import WS_TOKEN_PREFIX
from core.auth import bearer_token, token_matches
from core.ratelimit import KeyedLimiter

PUBLIC_API_PATHS = frozenset({"/api/health"})
WS_PATH = "/ws"

SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (
        b"content-security-policy",
        b"default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; "
        b"script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    ),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"cache-control", b"no-store"),
]


def loopback_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


def loopback_origins(port: int) -> set[str]:
    return {f"http://{host}" for host in loopback_hosts(port)}


def ws_token(subprotocols: Iterable[str]) -> str | None:
    for proto in subprotocols:
        if proto.startswith(WS_TOKEN_PREFIX):
            return proto[len(WS_TOKEN_PREFIX) :]
    return None


class SecurityMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        allowed_hosts: set[str],
        allowed_origins: set[str],
        limiter: KeyedLimiter,
    ) -> None:
        self.app = app
        self._token = token
        self._hosts = {h.lower() for h in allowed_hosts}
        self._origins = {o.lower() for o in allowed_origins}
        self._limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        problem = self._check(scope, headers)
        if problem is not None:
            status, reason = problem
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": reason})
            else:
                await _reject(send, status, reason)
            return
        if kind == "websocket":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + SECURITY_HEADERS
            await send(message)

        await self.app(scope, receive, send_with_headers)

    def _check(self, scope: Scope, headers: dict[str, str]) -> tuple[int, str] | None:
        # DNS rebinding defence: the Host must be our loopback address.
        if headers.get("host", "").lower() not in self._hosts:
            return 421, "invalid host"
        origin = headers.get("origin")
        if origin is not None and origin.lower() not in self._origins:
            return 403, "origin not allowed"
        client = scope.get("client")
        if not self._limiter.allow(client[0] if client else "unknown"):
            return 429, "rate limited"
        path: str = scope["path"]
        if scope["type"] == "websocket":
            if path != WS_PATH:
                return 404, "not found"
            protos = [p.strip() for p in headers.get("sec-websocket-protocol", "").split(",")]
            if not token_matches(self._token, ws_token(protos)):
                return 401, "unauthorized"
            return None
        needs_token = path.startswith("/api/") and path not in PUBLIC_API_PATHS
        if needs_token and not token_matches(self._token, bearer_token(headers.get("authorization"))):
            return 401, "unauthorized"
        return None


async def _reject(send: Send, status: int, reason: str) -> None:
    body = json.dumps({"error": reason}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), *SECURITY_HEADERS],
        }
    )
    await send({"type": "http.response.body", "body": body})
