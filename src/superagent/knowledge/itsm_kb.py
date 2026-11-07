"""Knowledge from the ITSM tool itself: KB articles and resolved similar tickets."""

from __future__ import annotations

from ..itsm.base import ITSMConnector
from .base import KnowledgeHit, KnowledgeSource


class ItsmKnowledgeSource(KnowledgeSource):
    name = "itsm_kb"

    def __init__(self, itsm: ITSMConnector):
        self.itsm = itsm

    async def search(self, query: str, top_k: int = 5) -> list[KnowledgeHit]:
        articles = await self.itsm.search_kb(query, top_k)
        hits = [KnowledgeHit(source=self.name, title=a["title"], content=a.get("content", ""), url=a.get("url", ""))
                for a in articles]  # fmt: skip
        for t in await self.itsm.search_similar(query, max(2, top_k // 2)):
            notes = t.raw.get("close_notes") or next((c.text for c in reversed(t.comments) if c.internal), "")
            hits.append(
                KnowledgeHit(source="similar_tickets", title=f"{t.id}: {t.title}",
                             content=f"Resolution: {notes}"[:1500], url=t.url)
            )  # fmt: skip
        return hits
