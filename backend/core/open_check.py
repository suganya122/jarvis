"""After a page is opened in the user's browser, look at what actually loaded.

"Opened in the browser" only means the browser was asked. A wrong address loads a 404 or an
error page, and saying "I've opened it" then is wrong. So the window is read (through the
hands tools, when they're there) and what it shows goes back with the result: an error page
is called one, so the model finds the right page instead.
"""

import asyncio
import re
from collections.abc import Callable

from contracts.common import Trust
from contracts.tools import Tool, ToolContext, ToolResult

OPENS_PAGES = frozenset({"open_url"})
LOAD_WAITS_S = (1.5, 2.0, 2.5)
"""Pauses before each look: pages take a moment, and the title is the last thing to change."""
NOT_LOADED = re.compile(r"^(?:|new tab|untitled|loading.*|start page|favou?rites|about:blank)$", re.I)
CODE = r"(?:404|403|410|50[0-4])"
ERROR_PAGE = re.compile(
    "|".join(
        (
            rf"(?:^|\berror\s*|\bhttp\s*){CODE}\b",  # "404 Not Found", "Error 404"
            rf"\b{CODE}\W{{0,3}}(?:error|not found|forbidden|page|bad gateway|unavailable)",
            r"not found|page (?:isn.t|is not|can.t be|cannot be|could not be) (?:found|reached|displayed)",
            r"can.t (?:be )?reach|can.t open|failed to open|(?:isn.t|is not|no longer) available",
            r"does(?:n.t| not) exist|problem loading|server not found|took too long to respond",
            r"access denied|forbidden|bad gateway|something went wrong|oops",
        )
    ),
    re.I,
)
ACTIVE = re.compile(r"^(w\d+)\s+(.+?)\s+\(active\)\s*$", re.M)


async def check_opened(
    result: ToolResult, tool: Callable[[str], Tool | None], ctx: ToolContext
) -> ToolResult:
    """The result, with what the browser shows now (or as it was, when nothing can read windows)."""
    lister, finder = tool("list_windows"), tool("find_elements")
    if lister is None or not result.ok:
        return result
    shown, window = "", ""
    for wait in LOAD_WAITS_S:
        await asyncio.sleep(wait)
        listing = await lister.run({}, ctx)
        found = ACTIVE.search(listing.output) if listing.ok else None
        if found is None:
            continue
        window, shown = found.group(1), found.group(2)
        if not NOT_LOADED.match(shown.rsplit(" — ", 1)[-1].strip()):
            break
    if not shown:
        return result
    error = ERROR_PAGE.search(shown.rsplit(" — ", 1)[-1])
    if error is None and finder is not None:  # an app can show "Page not found" under its usual title
        for words in ("not found", "404"):
            seen = await finder.run({"window_id": window, "query": words, "limit": 3}, ctx)
            names = re.findall(r"'([^'\n]*)'", seen.output) if seen.ok else []
            error = next((m for n in names if (m := ERROR_PAGE.search(n.strip()))), None)
            if error:
                break
    if error:
        note = (
            f"The browser now shows: “{shown}”. That's an error page ({error.group()}), not what was "
            "wanted: don't say it's open. Find the right page (search the site or the web for it), "
            "open that, and look again."
        )
    else:
        note = f"The browser now shows: “{shown}”. Check it's what they asked for before saying it's open."
    return result.model_copy(update={"output": f"{result.output}\n{note}", "trust": Trust.UNTRUSTED})
