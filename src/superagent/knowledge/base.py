"""Knowledge source interface and the fan-out aggregator used by the ``search_knowledge`` tool."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from pydantic import BaseModel

log = logging.getLogger(__name__)


class KnowledgeHit(BaseModel):
    source: str
    title: str
    content: str
    url: str = ""
    score: float = 0.0


class KnowledgeSource(ABC):
    name: str = "source"

    @abstractmethod
    async def search(self, query: str, top_k: int = 5) -> list[KnowledgeHit]: ...

    async def aclose(self) -> None:  # pragma: no cover
        return None


class KnowledgeHub:
    """Queries all enabled sources in parallel. A failing source never breaks the search."""

    def __init__(self, sources: list[KnowledgeSource], max_chars_per_hit: int = 1800):
        self.sources = sources
        self.max_chars = max_chars_per_hit

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.sources]

    async def search(self, query: str, top_k: int = 5, sources: list[str] | None = None) -> list[KnowledgeHit]:
        selected = [s for s in self.sources if not sources or s.name in sources]

        async def one(s: KnowledgeSource) -> list[KnowledgeHit]:
            try:
                return await asyncio.wait_for(s.search(query, top_k), timeout=30)
            except Exception as e:  # noqa: BLE001
                log.warning("knowledge source %s failed: %s", s.name, e)
                return []

        results = await asyncio.gather(*(one(s) for s in selected))
        seen: set[str] = set()
        merged: list[KnowledgeHit] = []
        # round-robin across sources so one source cannot crowd out the others
        for i in range(top_k):
            for hits in results:
                if i < len(hits):
                    h = hits[i]
                    key = h.url or h.title
                    if key in seen:
                        continue
                    seen.add(key)
                    h.content = h.content[: self.max_chars]
                    merged.append(h)
        return merged[: top_k * 2]
