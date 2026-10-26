"""The Telegram plugin: its bot client, plugin discovery, and the whole path through the
real plugin process against a fake Bot API server."""

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from contracts.channel import ApprovalRequest, ChannelDecision, ChannelInbound, ChannelStatus, FromChannel
from core.plugins import FIRST_PARTY, discover
from models.fake import FakeProvider, FakeStep
from plugins.telegram.bot import TelegramBot, chunks
from tests.conftest import Harness, make_harness
from tests.fake_telegram import TOKEN, FakeTelegram


@pytest.fixture
def server() -> Iterator[FakeTelegram]:
    with FakeTelegram() as fake:
        yield fake


# ---- the bot client -----------------------------------------------------------------


async def test_bot_turns_updates_into_channel_messages(server: FakeTelegram) -> None:
    out: list[FromChannel] = []
    bot = TelegramBot(TOKEN, out.append, api_base=server.url)
    server.user_says("hello there")
    server.user_says("hi all", user_id=-100, chat_type="group")  # groups are ignored
    server.user_taps("ok:abc123")
    poller = asyncio.create_task(bot.run())
    for _ in range(100):
        if len(out) >= 3:
            break
        await asyncio.sleep(0.02)
    poller.cancel()
    await asyncio.gather(poller, return_exceptions=True)
    assert isinstance(out[0], ChannelStatus) and out[0].state == "connected" and out[0].detail == "@test_bot"
    inbound = [m.message for m in out if isinstance(m, ChannelInbound)]
    assert [(m.conversation_id, m.user_id, m.text) for m in inbound] == [("42", "42", "hello there")]
    decisions = [m.decision for m in out if isinstance(m, ChannelDecision)]
    assert [(d.approval_id, d.user_id, d.approved) for d in decisions] == [("abc123", "42", True)]
    assert server.sent("answerCallbackQuery")
    await bot.close()


async def test_bot_sends_plain_text_and_approval_buttons(server: FakeTelegram) -> None:
    bot = TelegramBot(TOKEN, lambda _: None, api_base=server.url)
    await bot.send("42", "x" * 5000)
    request = ApprovalRequest(approval_id="a1", conversation_id="42", tool="note", risk="write",
                              summary="note x", reason="it asks")  # fmt: skip
    await bot.ask(request)
    await bot.close_approval("a1", "approved by you")
    messages = server.sent()
    assert [len(m["text"]) for m in messages[:2]] == [4000, 1000]
    assert all("parse_mode" not in m for m in messages)  # never formatted
    keyboard = messages[2]["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in keyboard] == ["ok:a1", "no:a1"]
    edit = server.sent("editMessageText")[0]
    assert edit["text"].endswith("→ approved by you") and "reply_markup" not in edit
    await bot.close()


async def test_a_bad_token_stops_with_a_clear_status(server: FakeTelegram) -> None:
    out: list[FromChannel] = []
    bot = TelegramBot("999:wrong-token-xxxxxxxx", out.append, api_base=server.url)
    await asyncio.wait_for(bot.run(), 5)  # returns instead of retrying forever
    assert isinstance(out[-1], ChannelStatus) and out[-1].detail == "Telegram rejected the bot token"
    await bot.close()


def test_long_text_splits_at_line_breaks() -> None:
    text = "a" * 3000 + "\n" + "b" * 3000
    assert chunks(text) == ["a" * 3000, "b" * 3000]
    assert chunks("") == [""]


# ---- discovery ------------------------------------------------------------------------


def test_discovery_rules(tmp_path: Path) -> None:
    def plugin(name: str, folder: str | None = None, **over: object) -> None:
        manifest = {"name": name, "display_name": name, "version": "1", "contract_version": "1.0",
                    "provides": ["channel"], "entrypoint": f"{name}.main"} | over  # fmt: skip
        target = tmp_path / (folder or name)
        target.mkdir()
        (target / "plugin.json").write_text(json.dumps(manifest))

    plugin("mychat")
    plugin("telegram", description="an impostor")  # can't replace the first-party one
    plugin("wrongdir", folder="other")
    plugin("oldone", contract_version="0.3")
    plugin("sneaky", entrypoint="os.main")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "plugin.json").write_text("{")
    found = discover(tmp_path)
    assert sorted(found) == ["mychat", "telegram"]
    assert found["telegram"].first_party and found["telegram"].folder == FIRST_PARTY / "telegram"
    assert not found["mychat"].first_party


# ---- end to end: daemon + real plugin process + fake Telegram ---------------------------


def test_end_to_end_through_the_plugin_process(tmp_path: Path, server: FakeTelegram) -> None:
    gen = make_harness(tmp_path, FakeProvider(script=[FakeStep(text="Hello from your computer.")]))
    harness: Harness = next(gen)
    try:
        auth, client = harness.auth, harness.client
        info = client.put("/api/plugins/telegram/enabled", headers=auth, json={"enabled": True}).json()
        assert info["state"] == "error" and "bot token" in info["detail"]  # can't start without its token
        client.put("/api/plugins/telegram/options", headers=auth, json={"options": {"api_base": server.url}})
        assert client.put("/api/plugins/telegram/secrets/bot_token", headers=auth,
                          json={"value": TOKEN}).status_code == 204  # fmt: skip
        state = _wait_state(harness, "connected")
        assert state["detail"] == "@test_bot" and state["secrets_set"] == {"bot_token": True}

        server.user_says("hello")
        assert "private" in server.wait_for("sendMessage")[0]["text"]
        code = client.post("/api/plugins/telegram/pair", headers=auth).json()["code"]
        server.user_says(f"/pair {code}")
        assert server.wait_for("sendMessage", 2)[1]["text"].startswith("Paired.")

        server.user_says("hi")
        assert server.wait_for("sendMessage", 3)[2]["text"] == "Hello from your computer."
        # A typed command from a chat app gets no "direct" pass: it asks, with buttons.
        server.user_says("remember I like tea")
        ask = server.wait_for("sendMessage", 4)[3]
        assert ask["text"].startswith("Allow remember?")
        approval = ask["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        server.user_taps(approval)
        assert server.wait_for("sendMessage", 5)[4]["text"] == "Got it. I'll remember: I like tea"
        assert server.sent("editMessageText")[0]["text"].endswith("→ approved by you")
        assert [m["text"] for m in client.get("/api/memories", headers=auth).json()] == ["I like tea"]

        assert (
            client.put("/api/plugins/telegram/enabled", headers=auth, json={"enabled": False}).json()["state"]
            == "stopped"
        )
        log = json.dumps(client.get("/api/audit?limit=500", headers=auth).json())
        assert (
            TOKEN not in log
            and json.dumps(client.get("/api/settings", headers=auth).json()).find(TOKEN) == -1
        )
    finally:
        next(gen, None)


def _wait_state(harness: Harness, state: str) -> dict[str, object]:
    for _ in range(300):
        info = next(p for p in harness.client.get("/api/plugins", headers=harness.auth).json()
                    if p["manifest"]["name"] == "telegram")  # fmt: skip
        if info["state"] == state:
            return info
        time.sleep(0.05)
    raise AssertionError(f"plugin never reached {state}: {info}")
