"""In-memory ITSM used for local development, demos and tests."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from ..models import Comment, Ticket, TicketState, TicketType, utcnow
from .base import ITSMConnector

SAMPLE_TICKETS = [
    {
        "id": "INC0010001",
        "title": "VPN disconnects every 10 minutes for remote user",
        "description": "Since Monday my GlobalProtect VPN drops roughly every 10 minutes. "
        "Windows 11 laptop, home Wi-Fi. Other users in my team are fine.",
        "category": "network",
        "impact": 3,
        "urgency": 2,
        "requester": "Ana Silva",
    },
    {
        "id": "INC0010002",
        "title": "Outlook keeps asking for password",
        "description": "Outlook desktop prompts for credentials repeatedly after the password change yesterday.",
        "category": "email",
        "impact": 3,
        "urgency": 3,
        "requester": "John Smith",
    },
    {
        "id": "RITM0010003",
        "title": "Request access to Finance SharePoint site",
        "description": "Please grant me read access to the Finance team site, I joined the FP&A team.",
        "category": "access",
        "type": "service_request",
        "impact": 3,
        "urgency": 3,
        "requester": "Maria Costa",
    },
    {
        "id": "INC0010004",
        "title": "Intermittent HTTP 502 errors on internal payroll API",
        "description": "Since the last deployment, around 5% of calls to payroll-api return 502 Bad Gateway "
        "through the API gateway. No pattern by time of day yet.",
        "category": "application",
        "impact": 2,
        "urgency": 2,
        "requester": "Payroll Ops",
    },
]

SAMPLE_KB = [
    {
        "id": "KB0001",
        "title": "Outlook repeatedly prompts for password after password change",
        "content": "Cause: cached credentials in Windows Credential Manager. Fix: close Outlook, open "
        "Credential Manager, remove entries starting with 'MicrosoftOffice16_Data', restart Outlook and "
        "sign in with modern auth. If it persists, reset the Outlook profile.",
        "url": "mock://kb/KB0001",
    },
    {
        "id": "KB0002",
        "title": "GlobalProtect VPN drops on Windows 11 with Wi-Fi power saving",
        "content": "Cause: Wi-Fi adapter power management turns off the radio when idle. Fix: Device "
        "Manager > Network adapters > Wi-Fi > Power Management > untick 'Allow the computer to turn off "
        "this device'. Also set the GlobalProtect keep-alive to 60s.",
        "url": "mock://kb/KB0002",
    },
    {
        "id": "KB0003",
        "title": "SharePoint site access requests",
        "content": "Access to departmental SharePoint sites is granted by the site owner. Service desk "
        "verifies manager approval, then adds the user to the site's Visitors group.",
        "url": "mock://kb/KB0003",
    },
]


class MockITSM(ITSMConnector):
    name = "mock"

    def __init__(self, seed: bool = True, state_file: str | None = None):
        self.tickets: dict[str, Ticket] = {}
        self.kb = list(SAMPLE_KB)
        self.problems: dict[str, dict] = {}
        self._pid = itertools.count(1)
        self.state_file = Path(state_file) if state_file else None
        if seed:
            for t in SAMPLE_TICKETS:
                self.add(**t)

    def add(self, id: str, title: str, description: str = "", **kw) -> Ticket:
        t = Ticket(
            id=id,
            system_id=id,
            title=title,
            description=description,
            type=TicketType(kw.pop("type", "incident")),
            assignment_group=kw.pop("assignment_group", "SuperAgent"),
            url=f"mock://ticket/{id}",
            **kw,
        )
        self.tickets[id] = t
        return t

    def _get(self, ticket: Ticket) -> Ticket:
        return self.tickets[ticket.id]

    def _touch(self, t: Ticket) -> None:
        t.updated_at = utcnow()
        if self.state_file:
            self.state_file.write_text(
                json.dumps({k: v.model_dump(mode="json") for k, v in self.tickets.items()}, indent=1)
            )

    async def list_queue(self, assignment_groups, limit=50):
        return [
            t
            for t in self.tickets.values()
            if t.assignment_group in assignment_groups and t.state == TicketState.NEW
        ][:limit]

    async def get_ticket(self, ticket_id):
        return self.tickets[ticket_id].model_copy(deep=True)

    async def assign_to_agent(self, ticket):
        t = self._get(ticket)
        t.assigned_to = "superagent"
        self._touch(t)

    async def add_work_note(self, ticket, text):
        t = self._get(ticket)
        t.comments.append(Comment(author="superagent", text=text, internal=True))
        self._touch(t)

    async def add_customer_comment(self, ticket, text):
        t = self._get(ticket)
        t.comments.append(Comment(author="superagent", text=text, internal=False))
        self._touch(t)

    async def set_state(self, ticket, state, reason=""):
        t = self._get(ticket)
        t.state = state
        self._touch(t)

    async def set_priority(self, ticket, impact, urgency):
        from ..itil import priority_from

        t = self._get(ticket)
        t.impact, t.urgency, t.priority = impact, urgency, priority_from(impact, urgency)
        self._touch(t)

    async def resolve(self, ticket, close_code, close_notes):
        t = self._get(ticket)
        t.state = TicketState.RESOLVED
        t.raw["close_code"] = close_code
        t.raw["close_notes"] = close_notes
        self._touch(t)

    async def reassign(self, ticket, group, note):
        t = self._get(ticket)
        t.assignment_group = group
        t.assigned_to = ""
        t.state = TicketState.IN_PROGRESS
        t.comments.append(Comment(author="superagent", text=note, internal=True))
        self._touch(t)

    async def search_similar(self, query, limit=5):
        words = {w for w in query.lower().split() if len(w) > 3}
        scored = []
        for t in self.tickets.values():
            if t.state not in (TicketState.RESOLVED, TicketState.CLOSED):
                continue
            text = f"{t.title} {t.description}".lower()
            score = sum(1 for w in words if w in text)
            if score:
                scored.append((score, t))
        return [t for _, t in sorted(scored, key=lambda x: -x[0])[:limit]]

    async def search_kb(self, query, limit=5):
        words = {w for w in query.lower().split() if len(w) > 3}
        scored = []
        for a in self.kb:
            text = f"{a['title']} {a['content']}".lower()
            score = sum(1 for w in words if w in text)
            if score:
                scored.append((score, a))
        return [a for _, a in sorted(scored, key=lambda x: -x[0])[:limit]]

    async def create_problem(self, title, description, related):
        pid = f"PRB{next(self._pid):07d}"
        self.problems[pid] = {"title": title, "description": description, "related": related}
        return pid
