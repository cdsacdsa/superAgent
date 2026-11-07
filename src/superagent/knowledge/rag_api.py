"""Generic connector for an existing RAG service.

Default contract (adapt with ``field_map`` / ``request_template`` if your service differs):

    POST {url}
    {"query": "...", "top_k": 5}
    ->
    {"results": [{"title": "...", "content": "...", "url": "...", "score": 0.83}, ...]}
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .base import KnowledgeHit, KnowledgeSource


def _dig(obj: Any, path: str) -> Any:
    for part in path.split(".") if path else []:
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit():
            obj = obj[int(part)]
        else:
            return None
    return obj


class RagApiSource(KnowledgeSource):
    name = "rag"

    def __init__(
        self,
        url: str,
        api_key: str = "",
        key_header: str = "Authorization",
        results_path: str = "results",
        field_map: dict[str, str] | None = None,
        request_template: str = '{"query": "{query}", "top_k": {top_k}}',
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.url = url
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers[key_header] = f"Bearer {api_key}" if key_header.lower() == "authorization" else api_key
        self.http = httpx.AsyncClient(timeout=30, headers=headers, transport=transport)
        self.results_path = results_path
        self.field_map = {"title": "title", "content": "content", "url": "url", "score": "score", **(field_map or {})}
        self.request_template = request_template

    async def aclose(self):
        await self.http.aclose()

    async def search(self, query: str, top_k: int = 5) -> list[KnowledgeHit]:
        body = json.loads(
            self.request_template.replace("{query}", json.dumps(query)[1:-1]).replace("{top_k}", str(top_k))
        )
        r = await self.http.post(self.url, json=body)
        r.raise_for_status()
        items = _dig(r.json(), self.results_path) or []
        out = []
        for it in items[:top_k]:
            fm = self.field_map
            out.append(
                KnowledgeHit(
                    source=self.name,
                    title=str(_dig(it, fm["title"]) or "RAG result"),
                    content=str(_dig(it, fm["content"]) or ""),
                    url=str(_dig(it, fm["url"]) or ""),
                    score=float(_dig(it, fm["score"]) or 0),
                )
            )
        return out
