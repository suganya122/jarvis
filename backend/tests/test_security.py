import sys

import pytest
from starlette.websockets import WebSocketDisconnect

from core.auth import bearer_token, load_or_create_token, token_matches
from core.ratelimit import TokenBucket
from tests.conftest import Harness


def test_health_is_public(harness: Harness) -> None:
    assert harness.client.get("/api/health").json() == {"ok": True}


def test_api_requires_token(harness: Harness) -> None:
    assert harness.client.get("/api/info").status_code == 401
    bad = harness.client.get("/api/info", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    assert harness.client.get("/api/info", headers=harness.auth).status_code == 200


def test_rejects_foreign_host_header(harness: Harness) -> None:
    # DNS rebinding: evil.example resolving to 127.0.0.1 still sends its own Host.
    response = harness.client.get("/api/health", headers={"host": "evil.example:8765"})
    assert response.status_code == 421


def test_rejects_foreign_origin(harness: Harness) -> None:
    response = harness.client.get("/api/info", headers={**harness.auth, "origin": "https://evil.example"})
    assert response.status_code == 403


def test_no_cors_and_security_headers(harness: Harness) -> None:
    response = harness.client.get("/api/info", headers={**harness.auth, "origin": "http://127.0.0.1:8765"})
    assert "access-control-allow-origin" not in response.headers
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_ws_requires_token(harness: Harness) -> None:
    with pytest.raises(WebSocketDisconnect), harness.ws(token="wrong") as ws:
        ws.receive_text()


def test_ws_rejects_foreign_origin(harness: Harness) -> None:
    with pytest.raises(WebSocketDisconnect), harness.ws(origin="https://evil.example") as ws:
        ws.receive_text()


def test_ws_accepts_valid_token_without_echoing_it(harness: Harness) -> None:
    with harness.ws() as ws:
        assert ws.accepted_subprotocol == "assistant.v1"
        assert '"type":"hello"' in ws.receive_text()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_token_file_is_private_and_stable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "auth_token"
    first = load_or_create_token(path)
    assert load_or_create_token(path) == first
    assert len(first) >= 32
    assert path.stat().st_mode & 0o077 == 0


def test_token_helpers() -> None:
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("Basic abc") is None
    assert bearer_token(None) is None
    assert token_matches("abc", "abc")
    assert not token_matches("abc", None)


def test_token_bucket() -> None:
    now = [0.0]
    bucket = TokenBucket(2, clock=lambda: now[0])
    assert bucket.allow() and bucket.allow()
    assert not bucket.allow()
    now[0] += 30  # half a minute refills one token
    assert bucket.allow()
