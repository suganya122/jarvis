"""Abilities: new tools the assistant builds for itself when nothing it has can do what was asked.

    spec     what an ability is (manifest + main.py) and the checks before saving or running it
    builder  the most capable model writes it; it is installed, test-run and fixed in a few rounds
    runner   each ability runs in its own process, packages in its own folder, no secrets
    host     the small program that process runs
    tools    build_ability, list_abilities, remove_ability, and one tool per ability

Building always asks the user (core.policy.ALWAYS_ASKS), even with full automation: it writes and
runs new code. Using an ability then follows the usual rules for what it declared it does.
"""

import logging
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from contracts.model import Completed, ModelProvider
from core.abilities.builder import Builder
from core.abilities.spec import AbilitySpec, load
from core.abilities.tools import PREFIX, AbilityTool, BuildAbility, ListAbilities, RemoveAbility
from core.toolbox import ToolRegistry

log = logging.getLogger(__name__)


class Abilities:
    def __init__(
        self,
        folder: Path,
        registry: ToolRegistry,
        provider: Callable[[], ModelProvider],
        record_cost: Callable[[str, Completed], Awaitable[None]],
    ) -> None:
        self._folder = folder
        self._registry = registry
        self._specs: dict[str, AbilitySpec] = {}
        builder = Builder(provider, record_cost, folder)
        for tool in (
            BuildAbility(builder, lambda: set(self._specs), self.add),
            ListAbilities(lambda: list(self._specs.values())),
            RemoveAbility(self.remove),
        ):
            registry.register(tool)
        self._load()

    def _load(self) -> None:
        if not self._folder.is_dir():
            return
        for path in sorted(self._folder.iterdir()):
            if path.name.startswith(".") or not path.is_dir():
                continue
            spec = load(path)
            if isinstance(spec, str):
                log.warning("ability %s not loaded: %s", path.name, spec)
                continue
            self.add(spec)

    def add(self, spec: AbilitySpec) -> str:
        """Make a built (or saved) ability a tool. Returns the tool's name."""
        self._registry.remove(PREFIX + spec.name)
        tool = AbilityTool(spec, self._folder / spec.name)
        self._registry.register(tool)
        self._specs[spec.name] = spec
        return tool.info.name

    def remove(self, name: str) -> bool:
        if name not in self._specs:
            return False
        self._registry.remove(PREFIX + name)
        del self._specs[name]
        shutil.rmtree(self._folder / name, ignore_errors=True)
        return True
