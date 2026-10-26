"""Contract tests: protocol shape, TS mirror drift, and the import-boundary rule."""

import ast
import re
from pathlib import Path
from typing import get_args

from contracts.model import ModelProvider
from contracts.ws import ClientMessage, ServerMessage
from models.anthropic_provider import AnthropicProvider
from models.config import AnthropicConfig, LocalConfig
from models.fake import FakeProvider
from models.openai_compat import OpenAICompatProvider

BACKEND = Path(__file__).resolve().parents[1]
TS_CONTRACTS = BACKEND.parent / "frontend" / "src" / "contracts.ts"


def _type_literals(union: object) -> set[str]:
    members = get_args(get_args(union)[0])
    return {m.model_fields["type"].default for m in members}


def test_every_provider_satisfies_the_contract() -> None:
    providers = [
        FakeProvider(),
        AnthropicProvider(AnthropicConfig(), api_key=lambda: None),
        OpenAICompatProvider(LocalConfig()),
    ]
    for provider in providers:
        assert isinstance(provider, ModelProvider)


def test_typescript_mirror_has_the_same_message_types() -> None:
    source = TS_CONTRACTS.read_text(encoding="utf-8")
    ts_types = set(re.findall(r'type:\s*"([a-z._]+)"', source))
    assert ts_types == _type_literals(ClientMessage) | _type_literals(ServerMessage)


def _first_party_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
    return names


def test_import_boundaries() -> None:
    rules = {
        "contracts": {"core", "models", "workers", "skills", "plugins"},
        "core": {"workers"},
        "workers": {"core", "models", "skills"},
        "models": {"core", "workers"},
        "plugins": {"core", "models", "workers", "skills"},
    }
    for package, forbidden in rules.items():
        for path in (BACKEND / package).rglob("*.py"):
            bad = _first_party_imports(path) & forbidden
            assert not bad, f"{path.relative_to(BACKEND)} imports {bad}"


def test_no_file_over_400_lines() -> None:
    for package in ("contracts", "core", "models", "workers", "skills", "plugins"):
        for path in (BACKEND / package).rglob("*.py"):
            lines = path.read_text(encoding="utf-8").count("\n")
            assert lines <= 400, f"{path.name} has {lines} lines"


def test_app_version_matches_the_package_version() -> None:
    import tomllib

    from contracts import APP_ID, APP_VERSION

    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == APP_VERSION
    assert project["name"] == APP_ID  # the pipx/console-script name
