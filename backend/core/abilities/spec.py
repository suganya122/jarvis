"""What a built ability is: a manifest plus one Python file, checked before anything is saved or run."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts.common import Capability, RiskLevel

NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*(\[[A-Za-z0-9_,\-]+\])?([<>=!~]=?[A-Za-z0-9_.*]+)?$")
MAX_PACKAGES = 8
MAX_CODE_CHARS = 40_000
MANIFEST = "ability.json"
CODE = "main.py"
DECLARABLE = frozenset(
    {Capability.NET_FETCH, Capability.FS_READ, Capability.FS_WRITE, Capability.SCREEN_READ,
     Capability.INPUT_CONTROL, Capability.SEND_EXTERNAL, Capability.PURCHASE}
)  # fmt: skip
"""What an ability may say it does, on top of running code (shell.run), which every ability does."""
HOOKS = ("", "hears")
ORDER = (RiskLevel.WRITE, RiskLevel.EXTERNAL_SEND, RiskLevel.DESTRUCTIVE, RiskLevel.PURCHASE)
"""Running new code is never just reading: an ability is at least WRITE, whatever it declares."""


@dataclass(frozen=True, slots=True)
class AbilitySpec:
    name: str
    description: str
    goal: str
    input_schema: dict[str, Any]
    risk: RiskLevel
    capabilities: tuple[Capability, ...]
    packages: tuple[str, ...]
    code: str
    test_args: dict[str, Any]
    created_at: float = 0.0
    hook: str = ""
    """"hears": it is also asked about every request Jarvis hears, before Jarvis acts on it."""

    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "goal": self.goal,
            "input_schema": self.input_schema,
            "risk": self.risk.value,
            "capabilities": [c.value for c in self.capabilities],
            "packages": list(self.packages),
            "test_args": self.test_args,
            "created_at": self.created_at,
            "hook": self.hook,
        }


def check(manifest: dict[str, Any], code: str, goal: str) -> AbilitySpec | str:
    """The spec, or what is wrong with it (in words the builder model can fix)."""
    name = str(manifest.get("name", ""))
    if not NAME.match(name):
        return "name must be 3-41 lowercase letters, digits or _, starting with a letter"
    description = str(manifest.get("description", "")).strip()
    if not 10 <= len(description) <= 400:
        return "description must be one or two sentences (10-400 characters)"
    schema = manifest.get("input_schema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return 'input_schema must be a JSON schema with "type": "object"'
    try:
        risk = RiskLevel(str(manifest.get("risk", "write")))
    except ValueError:
        return "risk must be one of: " + ", ".join(r.value for r in ORDER)
    risk = max(risk, RiskLevel.WRITE, key=lambda r: ORDER.index(r) if r in ORDER else -1)
    try:
        caps = tuple(Capability(str(c)) for c in manifest.get("capabilities") or [])
    except ValueError:
        return "capabilities must be from: " + ", ".join(sorted(c.value for c in DECLARABLE))
    if not set(caps) <= DECLARABLE:
        return "capabilities must be from: " + ", ".join(sorted(c.value for c in DECLARABLE))
    packages = tuple(str(p).strip() for p in manifest.get("packages") or [])
    if len(packages) > MAX_PACKAGES or not all(PACKAGE.match(p) for p in packages):
        return f"packages must be at most {MAX_PACKAGES} plain PyPI names (optionally with a version)"
    test_args = manifest.get("test_args")
    if not isinstance(test_args, dict):
        return "test_args must be an object: arguments for a harmless test run"
    hook = str(manifest.get("hook") or "")
    if hook not in HOOKS:
        return 'hook must be "" or "hears"'
    if not code.strip() or len(code) > MAX_CODE_CHARS:
        return f"main.py must be 1-{MAX_CODE_CHARS} characters"
    try:
        compile(code, CODE, "exec")
    except SyntaxError as exc:
        return f"main.py doesn't compile: line {exc.lineno}: {exc.msg}"
    if not re.search(r"^def run\(", code, re.MULTILINE):
        return "main.py must define a top-level `def run(args: dict) -> str`"
    return AbilitySpec(
        name=name,
        description=description,
        goal=goal,
        input_schema=schema,
        risk=risk,
        capabilities=caps,
        packages=packages,
        code=code,
        test_args=test_args,
        created_at=float(manifest.get("created_at") or 0.0),
        hook=hook,
    )


def save(spec: AbilitySpec, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / CODE).write_text(spec.code, encoding="utf-8")
    (folder / MANIFEST).write_text(json.dumps(spec.manifest(), indent=2), encoding="utf-8")


def load(folder: Path) -> AbilitySpec | str:
    try:
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        code = (folder / CODE).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return f"unreadable: {exc}"
    if not isinstance(manifest, dict):
        return "unreadable manifest"
    return check(manifest, code, str(manifest.get("goal", "")))
