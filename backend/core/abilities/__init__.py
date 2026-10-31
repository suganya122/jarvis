"""Abilities: new tools the assistant builds for itself when nothing it has can do what was asked.

    spec     what an ability is (manifest + main.py) and the checks before saving or running it
    builder  the most capable model writes it; it is installed, test-run and fixed in a few rounds
    runner   each ability runs in its own process, packages in its own folder, no secrets
    host     the small program that process runs
    tools    build_ability, list_abilities, remove_ability, and one tool per ability

Building writes and runs new code, so it happens only when the user asks for it or says yes to the
offer, never from a routine (core.policy.NEW_CODE). Using an ability then follows the usual rules
for what it declared it does.
An ability can also listen ("hears"): it's asked about every request Jarvis hears before Jarvis
acts on it, which is how Jarvis can teach itself, say, to answer only the user's own voice.
"""

import asyncio
import logging
import shutil
import time
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path

from contracts.ears import EARS_SAMPLE_RATE
from contracts.model import Completed, ModelProvider
from core.abilities import runner
from core.abilities.builder import Builder
from core.abilities.spec import AbilitySpec, load
from core.abilities.tools import PREFIX, AbilityTool, BuildAbility, ListAbilities, RemoveAbility
from core.toolbox import ToolRegistry

log = logging.getLogger(__name__)
HEARD = "heard"
KEEP_RECORDINGS = 20
HEARS_TIMEOUT_S = 8.0


def _keep_recording(folder: Path, pcm: bytes) -> Path:
    """The request as a WAV in the ability's own folder; only the last few are kept."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{time.time_ns()}.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(EARS_SAMPLE_RATE)
        out.writeframes(pcm)
    for old in sorted(folder.glob("*.wav"))[:-KEEP_RECORDINGS]:
        old.unlink(missing_ok=True)
    return path


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

    async def check_voice(self, audio: bytes, text: str) -> bool:
        """Before Jarvis acts on a request: every ability that listens may say "ignore" (say, it
        isn't the user's voice). A broken or slow one never makes Jarvis deaf: it counts as keep."""
        for spec in [s for s in self._specs.values() if s.hook == "hears"]:
            folder = self._folder / spec.name
            wav = await asyncio.to_thread(_keep_recording, folder / HEARD, audio)
            ran = await runner.run(folder, {"hook": "hears", "wav": str(wav), "text": text}, HEARS_TIMEOUT_S)
            if ran.ok and ran.output.strip().casefold().startswith("ignore"):
                log.info("ability %s: not acting on that request (%s)", spec.name, ran.output[:80])
                return False
            if not ran.ok:
                log.warning("ability %s couldn't check a request: %s", spec.name, ran.output[:200])
        return True

    def remove(self, name: str) -> bool:
        if name not in self._specs:
            return False
        self._registry.remove(PREFIX + name)
        del self._specs[name]
        shutil.rmtree(self._folder / name, ignore_errors=True)
        return True
