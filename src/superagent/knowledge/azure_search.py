"""Azure AI Search knowledge source (also used as the agent's Known Error / resolution memory).

Index schema expected (create with ``scripts/create-search-index.py``):
``id`` (key), ``title``, ``content``, ``url``, ``source``, ``ticket_id``, ``category``.
Uses the managed identity (role: Search Index Data Contributor) unless an API key is set.
"""

from __future__ import annotations

import httpx

from .base import KnowledgeHit, KnowledgeSource

API_VERSION = "2024-07-01"


class AzureSearchSource(KnowledgeSource):
    name = "azure_search"

    def __init__(self, endpoint: str, index: str, api_key: str = "", mi_client_id: str = "",
                 transport: httpx.AsyncBaseTransport | None = None):  # fmt: skip
        self.endpoint = endpoint.rstrip("/")
        self.index = index
        self.api_key = api_key
        self.mi_client_id = mi_client_id
        self._cred = None
        self.http = httpx.AsyncClient(timeout=30, transport=transport)

    async def aclose(self):
        await self.http.aclose()

    async def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"api-key": self.api_key}
        if self._cred is None:
            from azure.identity.aio import DefaultAzureCredential

            self._cred = DefaultAzureCredential(managed_identity_client_id=self.mi_client_id or None)
        tok = await self._cred.get_token("https://search.azure.com/.default")
        return {"Authorization": f"Bearer {tok.token}"}

    async def search(self, query: str, top_k: int = 5) -> list[KnowledgeHit]:
        r = await self.http.post(
            f"{self.endpoint}/indexes/{self.index}/docs/search?api-version={API_VERSION}",
            json={"search": query, "top": top_k, "queryType": "simple", "select": "title,content,url,source"},
            headers=await self._headers(),
        )
        r.raise_for_status()
        return [
            KnowledgeHit(source=self.name, title=d.get("title", ""), content=d.get("content", ""),
                         url=d.get("url", ""), score=float(d.get("@search.score", 0)))
            for d in r.json().get("value", [])
        ]  # fmt: skip

    async def upsert(self, docs: list[dict]) -> None:
        """Write resolved cases back as Known Error / resolution knowledge."""
        r = await self.http.post(
            f"{self.endpoint}/indexes/{self.index}/docs/index?api-version={API_VERSION}",
            json={"value": [{"@search.action": "mergeOrUpload", **d} for d in docs]},
            headers=await self._headers(),
        )
        r.raise_for_status()
