"""ServiceNow connector (Table API). Works with the ``incident`` table by default.

Required ServiceNow setup (see docs/connectors.md):
* an integration user (e.g. ``svc_superagent``) with the ``itil`` role, or a custom role allowing
  read/write on incident, read on kb_knowledge and sys_user_group, and create on problem
* an assignment group (default ``SuperAgent``) that routes tickets to the agent
* optional: a Business Rule + REST Message that POSTs to ``/webhooks/servicenow`` on insert/update
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from ..models import Comment, Ticket, TicketState, TicketType
from .base import ITSMConnector

STATE_TO_SN = {
    TicketState.NEW: "1",
    TicketState.IN_PROGRESS: "2",
    TicketState.ON_HOLD_CALLER: "3",
    TicketState.ON_HOLD_OTHER: "3",
    TicketState.RESOLVED: "6",
    TicketState.CLOSED: "7",
    TicketState.CANCELLED: "8",
}
SN_TO_STATE = {"1": TicketState.NEW, "2": TicketState.IN_PROGRESS, "3": TicketState.ON_HOLD_OTHER,
               "6": TicketState.RESOLVED, "7": TicketState.CLOSED, "8": TicketState.CANCELLED}  # fmt: skip
HOLD_REASON = {TicketState.ON_HOLD_CALLER: "1", TicketState.ON_HOLD_OTHER: "4"}  # 1 Awaiting Caller, 4 Vendor

FIELDS = (
    "sys_id,number,short_description,description,state,priority,impact,urgency,category,caller_id,"
    "assignment_group,assigned_to,cmdb_ci,opened_at,sys_updated_on,hold_reason"
)


def _v(rec: dict, field: str) -> str:
    x = rec.get(field, "")
    return x.get("value", "") if isinstance(x, dict) else (x or "")


def _d(rec: dict, field: str) -> str:
    x = rec.get(field, "")
    return x.get("display_value", "") if isinstance(x, dict) else (x or "")


def _ts(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return datetime.now(UTC)


def _int(s: str) -> int | None:
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


class ServiceNowConnector(ITSMConnector):
    name = "servicenow"

    def __init__(
        self,
        instance: str,
        user: str,
        password: str,
        agent_user_sys_id: str = "",
        table: str = "incident",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        host = instance.replace("https://", "").rstrip("/")
        self.base = f"https://{host}"
        self.table = table
        self.agent_user_sys_id = agent_user_sys_id
        self.http = httpx.AsyncClient(
            base_url=self.base,
            auth=(user, password),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30,
            transport=transport,
        )
        self._group_cache: dict[str, str] = {}

    async def aclose(self):
        await self.http.aclose()

    # -- helpers ---------------------------------------------------------------------
    async def _get(self, path: str, params: dict[str, Any]) -> list[dict]:
        r = await self.http.get(path, params=params)
        r.raise_for_status()
        return r.json().get("result", [])

    async def _patch(self, sys_id: str, body: dict[str, Any]) -> None:
        r = await self.http.patch(f"/api/now/table/{self.table}/{sys_id}", json=body)
        r.raise_for_status()

    async def _group_id(self, name: str) -> str:
        if name not in self._group_cache:
            rows = await self._get(
                "/api/now/table/sys_user_group", {"sysparm_query": f"name={name}", "sysparm_fields": "sys_id"}
            )
            if not rows:
                raise ValueError(f"ServiceNow group '{name}' not found")
            self._group_cache[name] = rows[0]["sys_id"]
        return self._group_cache[name]

    def _to_ticket(self, rec: dict, comments: list[Comment] | None = None) -> Ticket:
        number = _v(rec, "number")
        return Ticket(
            id=number,
            system_id=_v(rec, "sys_id"),
            title=_v(rec, "short_description"),
            description=_v(rec, "description"),
            type=TicketType.INCIDENT if self.table == "incident" else TicketType.SERVICE_REQUEST,
            state=SN_TO_STATE.get(_v(rec, "state"), TicketState.IN_PROGRESS),
            priority=_int(_v(rec, "priority")),
            impact=_int(_v(rec, "impact")),
            urgency=_int(_v(rec, "urgency")),
            category=_v(rec, "category"),
            requester=_d(rec, "caller_id"),
            assignment_group=_d(rec, "assignment_group"),
            assigned_to=_d(rec, "assigned_to"),
            configuration_item=_d(rec, "cmdb_ci"),
            opened_at=_ts(_v(rec, "opened_at")),
            updated_at=_ts(_v(rec, "sys_updated_on")),
            comments=comments or [],
            url=f"{self.base}/nav_to.do?uri={self.table}.do?sys_id={_v(rec, 'sys_id')}",
            raw={"sys_id": _v(rec, "sys_id")},
        )

    # -- interface -------------------------------------------------------------------
    async def list_queue(self, assignment_groups, limit=50):
        groups = ",".join(assignment_groups)
        q = f"assignment_group.nameIN{groups}^state=1^ORstate=2^assigned_toISEMPTY^ORDERBYopened_at"
        rows = await self._get(
            f"/api/now/table/{self.table}",
            {"sysparm_query": q, "sysparm_limit": limit, "sysparm_fields": FIELDS, "sysparm_display_value": "all"},
        )
        return [self._to_ticket(r) for r in rows]

    async def get_ticket(self, ticket_id):
        rows = await self._get(
            f"/api/now/table/{self.table}",
            {"sysparm_query": f"number={ticket_id}", "sysparm_fields": FIELDS, "sysparm_display_value": "all",
             "sysparm_limit": 1},
        )
        if not rows:
            raise KeyError(ticket_id)
        rec = rows[0]
        journal = await self._get(
            "/api/now/table/sys_journal_field",
            {"sysparm_query": f"element_id={_v(rec, 'sys_id')}^ORDERBYsys_created_on",
             "sysparm_fields": "element,value,sys_created_by,sys_created_on", "sysparm_limit": 100},
        )
        comments = [
            Comment(author=j.get("sys_created_by", ""), text=j.get("value", ""),
                    internal=j.get("element") == "work_notes", created_at=_ts(j.get("sys_created_on", "")))
            for j in journal if j.get("element") in ("comments", "work_notes")
        ]  # fmt: skip
        return self._to_ticket(rec, comments)

    async def assign_to_agent(self, ticket):
        body: dict[str, Any] = {"state": "2"}
        if self.agent_user_sys_id:
            body["assigned_to"] = self.agent_user_sys_id
        await self._patch(ticket.system_id, body)

    async def add_work_note(self, ticket, text):
        await self._patch(ticket.system_id, {"work_notes": text})

    async def add_customer_comment(self, ticket, text):
        await self._patch(ticket.system_id, {"comments": text})

    async def set_state(self, ticket, state, reason=""):
        body: dict[str, Any] = {"state": STATE_TO_SN[state]}
        if state in HOLD_REASON:
            body["hold_reason"] = HOLD_REASON[state]
        if reason:
            body["work_notes"] = reason
        await self._patch(ticket.system_id, body)

    async def set_priority(self, ticket, impact, urgency):
        # ServiceNow computes priority from impact/urgency with its own lookup rules.
        await self._patch(ticket.system_id, {"impact": str(impact), "urgency": str(urgency)})

    async def resolve(self, ticket, close_code, close_notes):
        await self._patch(
            ticket.system_id,
            {"state": "6", "close_code": close_code, "close_notes": close_notes},
        )

    async def reassign(self, ticket, group, note):
        gid = await self._group_id(group)
        await self._patch(ticket.system_id, {"assignment_group": gid, "assigned_to": "", "work_notes": note})

    async def search_similar(self, query, limit=5):
        rows = await self._get(
            f"/api/now/table/{self.table}",
            {"sysparm_query": f"123TEXTQUERY321={query}^stateIN6,7", "sysparm_limit": limit,
             "sysparm_fields": FIELDS + ",close_notes", "sysparm_display_value": "all"},
        )
        out = []
        for r in rows:
            t = self._to_ticket(r)
            t.raw["close_notes"] = _v(r, "close_notes")
            out.append(t)
        return out

    async def search_kb(self, query, limit=5):
        rows = await self._get(
            "/api/now/table/kb_knowledge",
            {"sysparm_query": f"123TEXTQUERY321={query}^workflow_state=published", "sysparm_limit": limit,
             "sysparm_fields": "sys_id,number,short_description,text"},
        )
        return [
            {"title": f"{r.get('number')} {r.get('short_description')}", "content": r.get("text", ""),
             "url": f"{self.base}/kb_view.do?sysparm_article={r.get('number')}"}
            for r in rows
        ]  # fmt: skip

    async def create_problem(self, title, description, related):
        r = await self.http.post(
            "/api/now/table/problem",
            json={"short_description": title, "description": description + "\n\nRelated: " + ", ".join(related)},
        )
        r.raise_for_status()
        return r.json()["result"]["number"]
