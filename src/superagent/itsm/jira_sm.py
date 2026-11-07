"""Jira Service Management (Cloud) connector.

Uses the Jira REST API v3 for search/transitions and the Service Desk API for
public (customer) vs internal comments. Transition names are configurable because
JSM workflows differ per project.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from ..models import Comment, Ticket, TicketState, TicketType
from .base import ITSMConnector

DEFAULT_TRANSITIONS = {
    TicketState.IN_PROGRESS: ["In Progress", "Start progress", "Work in progress"],
    TicketState.ON_HOLD_CALLER: ["Waiting for customer", "Pending"],
    TicketState.ON_HOLD_OTHER: ["Pending", "Waiting for support"],
    TicketState.RESOLVED: ["Resolve", "Resolve this issue", "Resolved", "Done"],
    TicketState.CANCELLED: ["Cancel", "Cancel request"],
}
PRIORITY_NAMES = {1: "Highest", 2: "High", 3: "Medium", 4: "Low"}


def _adf_text(node: Any) -> str:
    """Flatten Atlassian Document Format to plain text."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        return "".join(_adf_text(c) for c in node.get("content", [])) + (
            "\n" if node.get("type") in ("paragraph", "heading") else ""
        )
    if isinstance(node, list):
        return "".join(_adf_text(c) for c in node)
    return ""


def _adf(text: str) -> dict:
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": line or " "}]} for line in text.split("\n")],
    }


def _ts(s: str | None) -> datetime:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)


class JiraServiceManagementConnector(ITSMConnector):
    name = "jira"

    def __init__(
        self,
        base_url: str,
        user: str,
        api_token: str,
        project_key: str,
        agent_account_id: str = "",
        transitions: dict[TicketState, list[str]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base = base_url.rstrip("/")
        self.project = project_key
        self.agent_account_id = agent_account_id
        self.transitions = transitions or DEFAULT_TRANSITIONS
        self.http = httpx.AsyncClient(
            base_url=self.base, auth=(user, api_token), timeout=30, transport=transport,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )  # fmt: skip

    async def aclose(self):
        await self.http.aclose()

    def _to_ticket(self, issue: dict, comments: list[Comment] | None = None) -> Ticket:
        f = issue.get("fields", {})
        itype = (f.get("issuetype") or {}).get("name", "").lower()
        pname = (f.get("priority") or {}).get("name", "")
        prio = {v: k for k, v in PRIORITY_NAMES.items()}.get(pname)
        status_cat = ((f.get("status") or {}).get("statusCategory") or {}).get("key", "")
        state = {"new": TicketState.NEW, "indeterminate": TicketState.IN_PROGRESS, "done": TicketState.RESOLVED}.get(
            status_cat, TicketState.IN_PROGRESS
        )
        return Ticket(
            id=issue["key"],
            system_id=issue.get("id", ""),
            title=f.get("summary", ""),
            description=_adf_text(f.get("description")),
            type=TicketType.SERVICE_REQUEST if "request" in itype else TicketType.INCIDENT,
            state=state,
            priority=prio,
            requester=((f.get("reporter") or {}).get("displayName", "")),
            assigned_to=((f.get("assignee") or {}).get("displayName", "")),
            opened_at=_ts(f.get("created")),
            updated_at=_ts(f.get("updated")),
            comments=comments or [],
            url=f"{self.base}/browse/{issue['key']}",
            raw={"labels": f.get("labels", [])},
        )

    async def _search(self, jql: str, limit: int) -> list[dict]:
        r = await self.http.post(
            "/rest/api/3/search/jql",
            json={"jql": jql, "maxResults": limit,
                  "fields": ["summary", "description", "issuetype", "priority", "status", "reporter", "assignee",
                             "created", "updated", "labels"]},
        )  # fmt: skip
        r.raise_for_status()
        return r.json().get("issues", [])

    async def list_queue(self, assignment_groups, limit=50):
        # JSM has no assignment groups; we use queue labels (e.g. "superagent") or an unassigned filter.
        labels = ",".join(f'"{g.lower()}"' for g in assignment_groups)
        jql = (
            f'project = "{self.project}" AND statusCategory = "To Do" AND assignee IS EMPTY '
            f"AND labels IN ({labels}) ORDER BY created ASC"
        )
        return [self._to_ticket(i) for i in await self._search(jql, limit)]

    async def get_ticket(self, ticket_id):
        r = await self.http.get(f"/rest/api/3/issue/{ticket_id}")
        r.raise_for_status()
        issue = r.json()
        c = await self.http.get(f"/rest/servicedeskapi/request/{ticket_id}/comment", params={"limit": 100})
        comments = []
        if c.status_code == 200:
            for v in c.json().get("values", []):
                comments.append(
                    Comment(author=(v.get("author") or {}).get("displayName", ""), text=v.get("body", ""),
                            internal=not v.get("public", True), created_at=_ts((v.get("created") or {}).get("iso8601")))
                )  # fmt: skip
        return self._to_ticket(issue, comments)

    async def _comment(self, ticket: Ticket, text: str, public: bool):
        r = await self.http.post(
            f"/rest/servicedeskapi/request/{ticket.id}/comment", json={"body": text, "public": public}
        )
        r.raise_for_status()

    async def _transition(self, ticket: Ticket, state: TicketState):
        r = await self.http.get(f"/rest/api/3/issue/{ticket.id}/transitions")
        r.raise_for_status()
        wanted = [n.lower() for n in self.transitions.get(state, [])]
        for t in r.json().get("transitions", []):
            if t["name"].lower() in wanted or (t.get("to") or {}).get("name", "").lower() in wanted:
                rr = await self.http.post(f"/rest/api/3/issue/{ticket.id}/transitions", json={"transition": {"id": t["id"]}})
                rr.raise_for_status()
                return
        raise ValueError(f"No transition to {state} available for {ticket.id}")

    async def assign_to_agent(self, ticket):
        if self.agent_account_id:
            r = await self.http.put(f"/rest/api/3/issue/{ticket.id}/assignee", json={"accountId": self.agent_account_id})
            r.raise_for_status()
        await self._transition(ticket, TicketState.IN_PROGRESS)

    async def add_work_note(self, ticket, text):
        await self._comment(ticket, text, public=False)

    async def add_customer_comment(self, ticket, text):
        await self._comment(ticket, text, public=True)

    async def set_state(self, ticket, state, reason=""):
        if reason:
            await self._comment(ticket, reason, public=False)
        await self._transition(ticket, state)

    async def set_priority(self, ticket, impact, urgency):
        from ..itil import priority_from

        p = priority_from(impact, urgency)
        r = await self.http.put(f"/rest/api/3/issue/{ticket.id}", json={"fields": {"priority": {"name": PRIORITY_NAMES[p]}}})
        r.raise_for_status()

    async def resolve(self, ticket, close_code, close_notes):
        await self._comment(ticket, f"Resolution ({close_code}): {close_notes}", public=False)
        await self._transition(ticket, TicketState.RESOLVED)

    async def reassign(self, ticket, group, note):
        await self._comment(ticket, note, public=False)
        body: dict[str, Any] = {"update": {"labels": [{"add": group.lower().replace(" ", "-")}, {"remove": "superagent"}]},
                                "fields": {"assignee": None}}  # fmt: skip
        r = await self.http.put(f"/rest/api/3/issue/{ticket.id}", json=body)
        r.raise_for_status()

    async def search_similar(self, query, limit=5):
        q = query.replace('"', " ")
        jql = f'project = "{self.project}" AND statusCategory = "Done" AND text ~ "{q}" ORDER BY updated DESC'
        return [self._to_ticket(i) for i in await self._search(jql, limit)]

    async def search_kb(self, query, limit=5):
        r = await self.http.get("/rest/servicedeskapi/knowledgebase/article", params={"query": query, "limit": limit},
                                headers={"X-ExperimentalApi": "opt-in"})  # fmt: skip
        if r.status_code != 200:
            return []
        return [
            {"title": a.get("title", ""), "content": (a.get("excerpt") or ""),
             "url": ((a.get("content") or {}).get("iframeSrc") or (a.get("source") or {}).get("pageId", ""))}
            for a in r.json().get("values", [])
        ]  # fmt: skip

    async def create_problem(self, title, description, related):
        r = await self.http.post(
            "/rest/api/3/issue",
            json={"fields": {"project": {"key": self.project}, "issuetype": {"name": "Problem"}, "summary": title,
                             "description": _adf(description + "\n\nRelated: " + ", ".join(related))}},
        )  # fmt: skip
        r.raise_for_status()
        return r.json()["key"]
