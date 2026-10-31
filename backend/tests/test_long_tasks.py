"""Long tasks run to the end, and "opened" means the right page loaded.

What happened on the user's Mac: paying on the BESCOM portal stopped with "this is taking more
work than I do in one go", and a page that loaded as a 404 was reported as opened.
"""

from typing import Any

import pytest

from contracts.common import Trust
from contracts.model import ChatMessage, ImageData, ModelError, ModelRequest, ModelTier, Role, ToolResultBlock
from contracts.tools import ToolContext, ToolResult
from core import open_check
from core.context_trim import KEEP_WHOLE, SHORTEN_AT, SHORTENED, shorten_old_steps
from core.spend import CappedProvider
from models.fake import FakeProvider

CTX = ToolContext(trace_id="t", session_id="s", trust=Trust.USER)


class Reader:
    """list_windows / find_elements as the hands worker answers them."""

    def __init__(self, titles: list[str], found: str = "No matching controls.") -> None:
        self._titles, self._found = titles, found
        self.queries: list[str] = []

    def get(self, name: str) -> Any:
        reader = self

        class Tool:
            async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
                if name == "list_windows":
                    title = reader._titles.pop(0) if len(reader._titles) > 1 else reader._titles[0]
                    return ToolResult(
                        ok=True, output=f"w1  Safari — {title}  (active)\nw2  Finder — Downloads"
                    )
                reader.queries.append(args["query"])
                return ToolResult(ok=True, output=reader._found)

        return Tool() if name in ("list_windows", "find_elements") else None


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(open_check, "LOAD_WAITS_S", (0.0, 0.0, 0.0))


async def check(reader: Reader) -> str:
    opened = ToolResult(ok=True, output="Opened in the browser.")
    return (await open_check.check_opened(opened, reader.get, CTX)).output


async def test_a_404_is_called_an_error_page_not_opened() -> None:
    said = await check(Reader(["Untitled", "404 Not Found"]))  # waits for the page to load first
    assert "“Safari — 404 Not Found”" in said and "error page" in said and "don't say it's open" in said


async def test_a_page_not_found_under_the_sites_own_title_is_caught_too() -> None:
    reader = Reader(["WSS PORTAL"], found="e4  text 'Sorry, the page you requested was not found'")
    said = await check(reader)
    assert "error page" in said and reader.queries[0] == "not found"


async def test_a_good_page_is_reported_as_what_it_shows() -> None:
    said = await check(Reader(["BESCOM Quick Payment"]))
    assert "“Safari — BESCOM Quick Payment”" in said and "error page" not in said
    plain = ToolResult(ok=True, output="Opened in the browser.")
    assert await open_check.check_opened(plain, lambda _: None, CTX) == plain  # nothing reads windows


def steps(n: int) -> list[ChatMessage]:
    shot = ImageData(media_type="image/png", data_b64="AAAA", width=1, height=1)
    out = [ChatMessage(role=Role.USER, text="pay my BESCOM bill")]
    for i in range(n):
        page = f"<untrusted_content>\npage {i} " + "x" * 5000 + "\n</untrusted_content>\nThe content above…"
        out += [
            ChatMessage(role=Role.ASSISTANT, text=""),
            ChatMessage(
                role=Role.USER,
                text="",
                tool_results=(ToolResultBlock(call_id=f"c{i}", output=page, images=(shot,)),),
            ),
        ]
    return out


def test_old_steps_are_shortened_in_batches_and_the_latest_stay_whole() -> None:
    assert shorten_old_steps(steps(SHORTEN_AT)) == steps(SHORTEN_AT)  # not yet: the cache stays valid
    long = shorten_old_steps(steps(SHORTEN_AT + 1))
    results = [m.tool_results[0] for m in long if m.tool_results]
    old, recent = results[:-KEEP_WHOLE], results[-KEEP_WHOLE:]
    assert all(r.output.endswith(SHORTENED) and not r.images and len(r.output) < 800 for r in old)
    assert all(r.output.startswith("<untrusted_content>") and "</untrusted_content>" in r.output for r in old)
    assert all(not r.output.endswith(SHORTENED) and r.images for r in recent)
    assert shorten_old_steps(long) == long  # shortened once


async def test_the_spending_cap_never_stops_a_request_halfway() -> None:
    class Guard:
        capped = False

        async def refusal(self) -> ModelError | None:
            return ModelError(code="spend_cap", message="cap reached") if self.capped else None

    guard = Guard()
    provider = CappedProvider(lambda: FakeProvider(reply="ok"), guard)  # type: ignore[arg-type]

    async def kinds(trace: str) -> list[str]:
        hi = (ChatMessage(role=Role.USER, text="hi"),)
        request = ModelRequest(trace_id=trace, system="", messages=hi, tier=ModelTier.BALANCED)
        return [type(e).__name__ async for e in provider.stream(request)]

    assert "ModelError" not in await kinds("paying")
    guard.capped = True  # reached during that request
    assert "ModelError" not in await kinds("paying")  # its next steps still run
    assert await kinds("a new request") == ["ModelError"]
