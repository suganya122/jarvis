"""Process entry point: `python -m telegram.main`, driven by core over stdin/stdout."""

import asyncio
import logging
import sys
import threading

from pydantic import ValidationError

from contracts import CONTRACT_VERSION
from contracts.channel import (
    ChannelAskApproval,
    ChannelCloseApproval,
    ChannelConfigure,
    ChannelReady,
    ChannelSend,
    ChannelStatus,
    ChannelTyping,
    FromChannel,
    ToChannel,
    to_channel_adapter,
)

from .bot import TelegramBot, Unauthorized

log = logging.getLogger("telegram")


def emit(message: FromChannel) -> None:
    sys.stdout.write(message.model_dump_json() + "\n")
    sys.stdout.flush()


def read_stdin(loop: asyncio.AbstractEventLoop, inbox: "asyncio.Queue[ToChannel | None]") -> None:
    for raw in sys.stdin.buffer:
        try:
            loop.call_soon_threadsafe(inbox.put_nowait, to_channel_adapter.validate_json(raw))
        except ValidationError:
            log.warning("ignoring a malformed message from core")
    loop.call_soon_threadsafe(inbox.put_nowait, None)  # core closed the pipe: exit


async def serve() -> None:
    loop = asyncio.get_running_loop()
    inbox: asyncio.Queue[ToChannel | None] = asyncio.Queue()
    threading.Thread(target=read_stdin, args=(loop, inbox), daemon=True).start()
    first = await inbox.get()
    if not isinstance(first, ChannelConfigure):
        return
    emit(ChannelReady(contract_version=CONTRACT_VERSION))
    token = first.secrets.get("bot_token", "")
    if not token:
        emit(ChannelStatus(state="error", detail="no bot token"))
        return
    bot = TelegramBot(token, emit, api_base=first.options.get("api_base", ""))
    poller = asyncio.create_task(bot.run())
    try:
        while (message := await inbox.get()) is not None:
            try:
                if isinstance(message, ChannelSend):
                    await bot.send(message.conversation_id, message.text)
                elif isinstance(message, ChannelAskApproval):
                    await bot.ask(message.request)
                elif isinstance(message, ChannelCloseApproval):
                    await bot.close_approval(message.approval_id, message.outcome)
                elif isinstance(message, ChannelTyping):
                    await bot.typing(message.conversation_id)
            except (ConnectionError, Unauthorized) as exc:
                log.warning("couldn't deliver %s: %s", message.type, type(exc).__name__)
    finally:
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        await bot.close()


def main() -> None:
    # Never log HTTP requests: their URLs contain the bot token.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="telegram plugin: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
