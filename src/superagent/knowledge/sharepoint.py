"""SharePoint / OneDrive knowledge via the Microsoft Graph Search API.

App-only access needs the application permissions ``Sites.Read.All`` and ``Files.Read.All``
granted to the agent's managed identity or app registration (``scripts/grant-graph-permissions.sh``).
Graph requires the ``region`` field for app-only search requests.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from .base import KnowledgeHit, KnowledgeSource

GRAPH = "https://graph.microsoft.com/v1.0"
TEXT_EXT = (".txt", ".md", ".csv", ".log", ".json", ".ps1", ".sh", ".yaml", ".yml")


class _TokenProvider:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, mi_client_id: str = ""):
        self.tenant_id, self.client_id, self.client_secret = tenant_id, client_id, client_secret
        self.mi_client_id = mi_client_id
        self._cred = None

    async def token(self) -> str:
        if self._cred is None:
            if self.client_secret:
                from azure.identity.aio import ClientSecretCredential

                self._cred = ClientSecretCredential(self.tenant_id, self.client_id, self.client_secret)
            else:
                from azure.identity.aio import DefaultAzureCredential

                self._cred = DefaultAzureCredential(managed_identity_client_id=self.mi_client_id or None)
        tok = await self._cred.get_token("https://graph.microsoft.com/.default")
        return tok.token


class SharePointSource(KnowledgeSource):
    name = "sharepoint"

    def __init__(
        self,
        tenant_id: str = "",
        client_id: str = "",
        client_secret: str = "",
        region: str = "EMEA",
        query_filter: str = "",
        mi_client_id: str = "",
        fetch_content: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        token_provider: Any = None,
    ):
        self.tokens = token_provider or _TokenProvider(tenant_id, client_id, client_secret, mi_client_id)
        self.region = region
        self.query_filter = query_filter
        self.fetch_content = fetch_content
        self.http = httpx.AsyncClient(timeout=30, transport=transport)

    async def aclose(self):
        await self.http.aclose()

    async def search(self, query: str, top_k: int = 5) -> list[KnowledgeHit]:
        token = await self.tokens.token()
        headers = {"Authorization": f"Bearer {token}"}
        q = f"{query} {self.query_filter}".strip()
        body = {
            "requests": [
                {
                    "entityTypes": ["driveItem", "listItem"],
                    "query": {"queryString": q},
                    "from": 0,
                    "size": top_k,
                    "region": self.region,
                }
            ]
        }
        r = await self.http.post(f"{GRAPH}/search/query", json=body, headers=headers)
        r.raise_for_status()
        hits: list[KnowledgeHit] = []
        values = r.json().get("value") or [{}]
        for container in values[0].get("hitsContainers", []):
            for h in container.get("hits", []):
                res = h.get("resource", {})
                title = res.get("name") or (res.get("fields") or {}).get("title") or "SharePoint item"
                summary = re.sub(r"<[^>]+>", "", h.get("summary", ""))
                content = summary
                if self.fetch_content and str(title).lower().endswith(TEXT_EXT):
                    content = (await self._download(res, headers)) or summary
                hits.append(
                    KnowledgeHit(source=self.name, title=str(title), content=content,
                                 url=res.get("webUrl", ""), score=float(h.get("rank", 0) or 0))
                )  # fmt: skip
        return hits

    async def _download(self, res: dict, headers: dict) -> str:
        drive_id = (res.get("parentReference") or {}).get("driveId")
        item_id = res.get("id")
        if not drive_id or not item_id:
            return ""
        r = await self.http.get(f"{GRAPH}/drives/{drive_id}/items/{item_id}/content", headers=headers, follow_redirects=True)
        if r.status_code != 200:
            return ""
        return r.text[:6000]
