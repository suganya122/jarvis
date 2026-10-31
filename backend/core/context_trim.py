"""Long tasks: the latest steps stay whole, older ones are shortened as it goes.

Every step sends the whole conversation again. On real sites each read of a window is thousands
of characters and each screenshot more, so a long task would outgrow what the model can take
and stop halfway. What an earlier step saw is stale anyway (the page has moved on): its first
lines stay, its screenshots go. Done in batches, so most steps still reuse the cached prefix.
"""

from contracts.model import ChatMessage, ToolResultBlock

KEEP_WHOLE = 8
"""The most recent tool-result messages kept as they were."""
SHORTEN_AT = 16
"""Shorten once this many are whole (then down to KEEP_WHOLE): a batch, not every step."""
SHORT_CHARS = 600
SHORTENED = "\n[shortened: an earlier step. Read the window again for what it shows now.]"
OPEN, CLOSE = "<untrusted_content>", "</untrusted_content>"


def shorten_old_steps(messages: list[ChatMessage]) -> list[ChatMessage]:
    whole = [i for i, m in enumerate(messages) if m.tool_results and any(_long(r) for r in m.tool_results)]
    if len(whole) <= SHORTEN_AT:
        return messages
    out = list(messages)
    for i in whole[:-KEEP_WHOLE]:
        results = tuple(_short(r) if _long(r) else r for r in out[i].tool_results)
        out[i] = out[i].model_copy(update={"tool_results": results})
    return out


def _long(r: ToolResultBlock) -> bool:
    return bool(r.images) or (len(r.output) > SHORT_CHARS and not r.output.endswith(SHORTENED))


def _short(r: ToolResultBlock) -> ToolResultBlock:
    text = r.output
    if text.startswith(OPEN) and CLOSE in text:  # keep outside content marked as such
        inner = text[len(OPEN) : text.index(CLOSE)].strip()
        text = f"{OPEN}\n{inner[:SHORT_CHARS]}…\n{CLOSE}"
    elif len(text) > SHORT_CHARS:
        text = text[:SHORT_CHARS] + "…"
    return r.model_copy(update={"output": text + SHORTENED, "images": ()})
