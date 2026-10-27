"""The knowledge graph: what the assistant knows about the user's world (people, places, apps,
preferences, routines), learned from what they say and used without asking again.

    schema    the closed vocabulary of facts
    store     SQLite: entities, facts, forgetting
    resolve   who or what a word refers to (names, "my brother", mis-heard names)
    retrieve  from the graph to a few lines for the model (no model call)
    learn     proposing facts from the user's own words, and checking every one
    tools     recall and forget, for the model
    api       list, edit and delete, for the settings page
"""

from collections.abc import Awaitable, Callable

import aiosqlite

from contracts.model import Completed, ModelProvider
from core.config import Settings
from core.knowledge.learn import Learner
from core.knowledge.retrieve import Retriever
from core.knowledge.store import KnowledgeStore
from core.knowledge.tools import ForgetKnowledge, RecallKnowledge
from core.memory import MemoryStore, render, select


class Knowledge:
    def __init__(
        self,
        conn: aiosqlite.Connection,
        memory: MemoryStore,
        provider: Callable[[], ModelProvider],
        settings: Callable[[], Settings],
        record_cost: Callable[[str, Completed], Awaitable[None]],
    ) -> None:
        self.store = KnowledgeStore(conn)
        self._memory = memory
        self._settings = settings
        self._retriever = Retriever(self.store)
        self._learner = Learner(self.store, provider, settings, record_cost)
        self._ready = False
        self.tools = (RecallKnowledge(self.store), ForgetKnowledge(self.store))

    async def context(self, request: str) -> str:
        """What to tell the model for this request: the saved notes, then what the graph knows
        about the people and things the request mentions."""
        memory = self._settings().memory
        if not memory.enabled:
            return ""
        notes = render(select(await self._memory.all(), request, memory.max_prompt_chars))
        if not memory.graph:
            return notes
        if not self._ready:
            await self.store.ensure_self()
            self._ready = True
        graph = await self._retriever.context(request, memory.graph_max_chars)
        return "\n\n".join(part for part in (notes, graph) if part)

    def learn(self, said: str, reply: str) -> None:
        """After a turn: queue what the user said to learn from (in the background)."""
        self._learner.schedule(said, reply)
