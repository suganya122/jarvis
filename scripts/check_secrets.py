#!/usr/bin/env python3
"""Refuse to commit or publish secrets. Run by the git pre-commit hook (scripts/install-hooks.sh)
and by CI on every push. Exit 1 and say where, if anything looks like a real key or token.

Test fixtures use obviously fake keys (sk-ant-test-…, sk-ant-api03-abcdefghijklmnop, …), which
are allowed; anything else shaped like a credential is not. Files that must never be tracked
(.env, auth tokens, databases) are refused by name.
"""

import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "AWS key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    "Slack token": re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "Telegram bot token": re.compile(r"\b\d{8,10}:AA[0-9A-Za-z_\-]{33}\b"),
}
FAKE = re.compile(r"sk-ant-(?:test|api03-abcdefghijklmnop|abcdefghijklmnopqrstuvwx|xxx|real)")
NEVER_TRACKED = re.compile(r"(^|/)(\.env|auth_token|.*\.sqlite3(-.*)?)$")


def tracked(staged: bool) -> list[str]:
    args = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"] if staged else ["git", "ls-files"]
    return [f for f in subprocess.run(args, capture_output=True, text=True, check=True).stdout.splitlines() if f]


def main() -> int:
    staged = "--staged" in sys.argv
    problems = []
    for name in tracked(staged):
        if NEVER_TRACKED.search(name):
            problems.append(f"{name}: this file must never be committed")
            continue
        path = Path(name)
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for kind, pattern in PATTERNS.items():
                for match in pattern.finditer(line):
                    if kind == "Anthropic API key" and FAKE.match(match.group(0)):
                        continue
                    problems.append(f"{name}:{line_no}: looks like a {kind} ({match.group(0)[:12]}…)")
    if problems:
        print("Refusing: possible secrets found. Remove them (keep keys in .env, which git ignores):")
        print("\n".join(f"  {p}" for p in problems))
        return 1
    print(f"No secrets found in {'staged' if staged else 'tracked'} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
