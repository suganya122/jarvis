import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from contracts.model import ModelProvider
from contracts.tools import Tool
from core.app import DaemonOptions, create_app
from core.autostart import Autostart
from core.config import Settings
from core.secret_store import MemoryBackend
from models.fake import FakeProvider

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = BASE


class Harness:
    def __init__(self, client: TestClient, token: str, provider: ModelProvider) -> None:
        self.client = client
        self.token = token
        self.provider = provider

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def ws(self, token: str | None = None, origin: str = ORIGIN):  # type: ignore[no-untyped-def]
        protos = ["assistant.v1", f"token.{token if token is not None else self.token}"]
        return self.client.websocket_connect(
            "/ws", subprotocols=protos, headers={"origin": origin, "host": f"127.0.0.1:{PORT}"}
        )


def make_harness(
    tmp_path: Path,
    provider: ModelProvider,
    *,
    start_workers: bool = False,
    extra_tools: tuple[Tool, ...] = (),
    settings: Settings | None = None,
    worker_env: dict[str, str] | None = None,
) -> Iterator[Harness]:
    options = DaemonOptions(
        data_dir=tmp_path,
        start_workers=start_workers,
        worker_env=worker_env,
        extra_tools=extra_tools,
        port=PORT,
        secret_backend=MemoryBackend(),
        provider_override=provider,
        env={},
        autostart=Autostart(home=tmp_path / "home"),
        start_overlay=False,  # tests never open windows
    )
    app = create_app(options)
    with TestClient(app, base_url=BASE) as client:
        harness = Harness(client, app.state.token, provider)
        if settings is not None:
            response = client.put(
                "/api/settings", headers=harness.auth, json=settings.model_dump(mode="json")
            )
            assert response.status_code == 200, response.text
        yield harness


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    yield from make_harness(tmp_path, FakeProvider())


def handshake(ws: Any) -> dict[str, Any]:
    """Consume the greeting (hello + control.state) and return the hello frame."""
    hello = json.loads(ws.receive_text())
    assert hello["type"] == "hello"
    assert json.loads(ws.receive_text())["type"] == "control.state"
    return hello
