"""Before Jarvis acts on something it heard, abilities that listen (core.abilities: say, one that only
lets the user's own voice through) may hold it back. Whatever they decide, the person hears about it:
a request held back is never just silence, and a check that isn't working is said once, while Jarvis
answers anyway (a broken check must never make it deaf)."""

import logging
import re
from collections.abc import Awaitable, Callable

from core.hands_free_types import HandsFreeDeps

log = logging.getLogger(__name__)

QUIET_S = 60.0
"""Live (all talk is a request): held-back talk is answered at most this often, so a TV or other
people talking in the room don't get a reply to every sentence."""
FIX_IT = "If that was you, type to me in the Jarvis window and I'll fix it."


class VoiceGate:
    def __init__(self, deps: HandsFreeDeps, say: Callable[[str], Awaitable[None]]) -> None:
        self._d, self._say = deps, say
        self._held = 0  # held back in a row
        self._said_at: float | None = None
        self._broken = False

    async def allows(self, audio: bytes, text: str) -> bool:
        if self._d.check_voice is None or not audio:
            return True
        try:
            act, about = await self._d.check_voice(audio, text)
        except Exception:
            log.warning("an ability that listens failed; acting on the request", exc_info=True)
            act, about = True, "my voice check isn't working"
        if not act:
            await self._held_back(about)
            return False
        self._held = 0
        if about and not self._broken:  # once, until it works again
            await self._say(f"Heads up: {_plain(about)}, so I'm answering anyone for now. Ask me to fix it.")
        self._broken = bool(about)
        return True

    async def _held_back(self, why: str) -> None:
        self._held += 1
        now = self._d.clock()
        if self._d.settings().stay_live and self._said_at is not None and now - self._said_at < QUIET_S:
            return
        self._said_at = now
        said = f"I didn't act on that: {_plain(why)}." if why else "I didn't act on that."
        await self._say(f"{said} {FIX_IT}" if self._held == 1 else said)


def _plain(text: str) -> str:
    """'not your voice (match 0.42 < 0.70).' -> 'not your voice': the details aren't for saying."""
    return re.split(r"\s[(\[]", text, maxsplit=1)[0].strip().rstrip(".")
