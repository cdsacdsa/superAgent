"""Confluence pages and Jira issues as read-only knowledge (Atlassian Cloud REST APIs).

Use an API token of a dedicated user that can only *read* the spaces/projects the agent should
see; the connectors never write. Restrict further with ``confluence_spaces`` / ``jira_kb_projects``.
"""

from __future__ import annotations

import html
import json
import re

import httpx

from ..itsm.jira_sm import _adf_text
from .base import KnowledgeHit, KnowledgeSource

# Lucene/CQL/JQL operators; stripped so free text from the model is always a plain phrase search
_SPECIAL = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


def _terms(query: str) -> str:
    return " ".join(_SPECIAL.sub(" ", query).split())[:300]


def _html_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6])>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def _keys(csv: str) -> list[str]:
    return [k.strip() for k in (csv or "").split(",") if k.strip()]


class _AtlassianSource(KnowledgeSource):
    def __init__(self, base_url: str, user: str, api_token: str, scope: str = "",
                 transport: httpx.AsyncBaseTransport | None = None):  # fmt: skip
        self.base = base_url.rstrip("/")
        self.scope = _keys(scope)
        self.http = httpx.AsyncClient(base_url=self.base, auth=(user, api_token), timeout=30, transport=transport,
                                      headers={"Accept": "application/json"})  # fmt: skip

    async def aclose(self):
        await self.http.aclose()


class ConfluenceSource(_AtlassianSource):
    name = "confluence"

    async def search(self, query: str, top_k: int = 5) -> list[KnowledgeHit]:
        terms = _terms(query)
        if not terms:
            return []
        cql = f'type = page AND text ~ "{terms}"'
        if self.scope:
            cql += " AND space in (" + ", ".join(json.dumps(k) for k in self.scope) + ")"
        r = await self.http.get("/wiki/rest/api/content/search",
                                params={"cql": cql, "limit": top_k, "expand": "body.view,space"})  # fmt: skip
        r.raise_for_status()
        data = r.json()
        base = (data.get("_links") or {}).get("base") or f"{self.base}/wiki"
        hits = []
        for page in data.get("results", [])[:top_k]:
            webui = (page.get("_links") or {}).get("webui", "")
            space = (page.get("space") or {}).get("name", "")
            hits.append(KnowledgeHit(
                source=self.name,
                title=f"{page.get('title', '')}" + (f" ({space})" if space else ""),
                content=_html_text(((page.get("body") or {}).get("view") or {}).get("value", "")),
                url=f"{base}{webui}" if webui else "",
            ))  # fmt: skip
        return hits


class JiraKnowledgeSource(_AtlassianSource):
    """Past Jira issues (bugs, support tickets) with their resolution and latest comments."""

    name = "jira"

    async def search(self, query: str, top_k: int = 5) -> list[KnowledgeHit]:
        terms = _terms(query)
        if not terms:
            return []
        jql = f'text ~ "{terms}"'
        if self.scope:
            jql += " AND project in (" + ", ".join(json.dumps(k) for k in self.scope) + ")"
        jql += " ORDER BY updated DESC"
        r = await self.http.post("/rest/api/3/search/jql", json={
            "jql": jql, "maxResults": top_k,
            "fields": ["summary", "description", "status", "resolution", "issuetype", "comment"],
        })  # fmt: skip
        r.raise_for_status()
        hits = []
        for issue in r.json().get("issues", [])[:top_k]:
            f = issue.get("fields") or {}
            status = (f.get("status") or {}).get("name", "")
            resolution = (f.get("resolution") or {}).get("name", "")
            comments = ((f.get("comment") or {}).get("comments") or [])[-3:]
            parts = [
                f"Type: {(f.get('issuetype') or {}).get('name', '')} | Status: {status}"
                + (f" | Resolution: {resolution}" if resolution else ""),
                _adf_text(f.get("description")).strip(),
            ]
            parts += [f"Comment ({(c.get('author') or {}).get('displayName', '?')}): {_adf_text(c.get('body')).strip()}"
                      for c in comments]  # fmt: skip
            hits.append(KnowledgeHit(source=self.name, title=f"{issue.get('key')}: {f.get('summary', '')}",
                                     content="\n".join(p for p in parts if p), url=f"{self.base}/browse/{issue.get('key')}"))  # fmt: skip
        return hits
