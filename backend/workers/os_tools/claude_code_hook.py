"""Claude Code's permission hook in a project Jarvis runs: Jarvis's own rules decide, so Claude
Code never stops to ask for an ordinary step (running the app, its tests, installs).

Claude Code runs it before every tool (`.claude/settings.local.json`, written by
core.projects.claude_code) with the call as JSON on stdin, and reads the decision from stdout.
Work inside the project folder goes ahead: commands, and deleting or overwriting what's in it
(build output, its own files). What could hurt the user's computer is refused with the reason,
so Claude Code goes another way and says so in its report: a command Jarvis would ask about
(administrator rights, stopping programs, disks, secrets, a download piped into a shell) that
isn't confined to the folder, or a file written outside it.
"""

import json
import shlex
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # run by Claude Code, not imported

from workers.os_tools.shell_full import why_it_asks

CONFIG = ".jarvis/hook.json"
"""In the project folder: {"protected": [...]} (Jarvis's data, keys, the keychain)."""
EDITS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
INSIDE_IS_FINE = frozenset(
    {"deletes files", "overwrites a file", "empties a file", "changes permissions on a whole folder",
     "discards work in git"}
)  # fmt: skip
"""Reasons that are routine when every path in the command is inside the project."""
ELSEWHERE = ("/", "~", "$HOME", "${HOME}")


def inside(path: str, folder: Path) -> bool:
    """In the project, or in the temporary folder (Claude Code's own scratch files)."""
    try:
        where = Path(path).expanduser().resolve()
        return any(where.is_relative_to(base.resolve()) for base in (folder, Path(tempfile.gettempdir())))
    except (OSError, ValueError):
        return False


def stays_inside(command: str) -> bool:
    """Every path the command names is relative and never climbs out ('dist', not '../x' or '~/')."""
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    for word in words:
        value = word.split("=", 1)[-1]
        if value in ("/dev/null", "/dev/stdout", "/dev/stderr") or value.startswith("/tmp/"):  # noqa: S108
            continue
        if value.startswith(ELSEWHERE) or ".." in Path(value).parts:
            return False
    return True


def decide(call: dict[str, object], folder: Path, protected: list[str]) -> tuple[str, str]:
    """('allow' | 'deny', why) for one tool call."""
    tool = str(call.get("tool_name", ""))
    given = call.get("tool_input")
    args = given if isinstance(given, dict) else {}
    if tool in EDITS:
        path = str(args.get("file_path") or args.get("notebook_path") or "")
        if path and not inside(path, folder):
            return "deny", f"Jarvis keeps this project's work in {folder}: write the file there instead."
        return "allow", "inside the project"
    if tool == "Bash":
        command = str(args.get("command", ""))
        why = why_it_asks(command, protected)
        if why and not (why in INSIDE_IS_FINE and stays_inside(command)):
            return "deny", (
                f"Jarvis didn't run this: it {why}, and that needs the user's yes. Do it another way "
                "inside the project folder, or say in your final report exactly what you need and why."
            )
        return "allow", "Jarvis allows it"
    return "allow", "Jarvis allows it"


def main() -> None:
    call = json.load(sys.stdin)
    folder = Path(str(call.get("cwd") or ".")).resolve()
    try:
        protected = list(json.loads((folder / CONFIG).read_text()).get("protected", []))
    except (OSError, ValueError):
        protected = []
    decision, why = decide(call, folder, protected)
    if decision == "deny":
        with (folder / ".jarvis" / "refused.jsonl").open("a") as log:  # for the report to the user
            log.write(
                json.dumps({"tool": call.get("tool_name"), "input": call.get("tool_input"), "why": why})
                + "\n"
            )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": why,
    }}))  # fmt: skip


if __name__ == "__main__":
    main()
