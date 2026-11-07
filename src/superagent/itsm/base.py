"""ITSM connector interface. Implementations map normalised ITIL concepts to a specific tool."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Ticket, TicketState


class ITSMConnector(ABC):
    name: str = "itsm"

    @abstractmethod
    async def list_queue(self, assignment_groups: list[str], limit: int = 50) -> list[Ticket]:
        """Open, unassigned (or agent-assigned, not yet worked) tickets in the agent's groups."""

    @abstractmethod
    async def get_ticket(self, ticket_id: str) -> Ticket: ...

    @abstractmethod
    async def assign_to_agent(self, ticket: Ticket) -> None: ...

    @abstractmethod
    async def add_work_note(self, ticket: Ticket, text: str) -> None:
        """Internal note, visible to IT staff only."""

    @abstractmethod
    async def add_customer_comment(self, ticket: Ticket, text: str) -> None:
        """Comment visible to the requester."""

    @abstractmethod
    async def set_state(self, ticket: Ticket, state: TicketState, reason: str = "") -> None: ...

    @abstractmethod
    async def set_priority(self, ticket: Ticket, impact: int, urgency: int) -> None: ...

    @abstractmethod
    async def resolve(self, ticket: Ticket, close_code: str, close_notes: str) -> None: ...

    @abstractmethod
    async def reassign(self, ticket: Ticket, group: str, note: str) -> None:
        """Hand the ticket to a human group (functional escalation)."""

    @abstractmethod
    async def search_similar(self, query: str, limit: int = 5) -> list[Ticket]:
        """Resolved tickets similar to ``query`` (for known-error reuse)."""

    @abstractmethod
    async def search_kb(self, query: str, limit: int = 5) -> list[dict]:
        """Knowledge articles: [{title, content, url}]"""

    @abstractmethod
    async def create_problem(self, title: str, description: str, related: list[str]) -> str:
        """Create a Problem record; return its id."""

    async def aclose(self) -> None:  # pragma: no cover
        return None
