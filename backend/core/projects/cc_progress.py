"""What Claude Code is doing, readable, for the Terminal window the user can watch.

Run by Jarvis (core.projects.claude_code) as `python cc_progress.py <stream.jsonl>`: it follows
the file Claude Code writes its progress to and prints each step, then the final report."""

import json
import sys
import time
from pathlib import Path

WAIT_S = 3 * 3600


def line(event: dict[str, object]) -> str | None:
    kind = event.get("type")
    if kind == "result":
        ok = "finished" if not event.get("is_error") else "stopped with an error"
        return f"\n== Claude Code {ok} ==\n{event.get('result', '')}\n"
    message = event.get("message")
    if kind != "assistant" or not isinstance(message, dict):
        return None
    said = []
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and str(block.get("text", "")).strip():
            said.append(str(block["text"]).strip())
        elif block.get("type") == "tool_use":
            given = block.get("input") if isinstance(block.get("input"), dict) else {}
            what = given.get("description") or given.get("command") or given.get("file_path") or ""  # type: ignore[union-attr]
            said.append(f"▸ {block.get('name')}: {str(what)[:140]}")
    return "\n".join(said) or None


def main() -> None:
    stream = Path(sys.argv[1])
    print(
        f"Claude Code is working in {stream.parent.parent}. Jarvis is watching; you can close this window.\n"
    )
    deadline, seen = time.time() + WAIT_S, 0
    while time.time() < deadline:
        try:
            lines = stream.read_text(errors="replace").splitlines()
        except OSError:
            lines = []
        for raw in lines[seen:]:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if text := line(event):
                print(text, flush=True)
            if event.get("type") == "result":
                return
        seen = len(lines)
        time.sleep(1.0)


if __name__ == "__main__":
    main()
