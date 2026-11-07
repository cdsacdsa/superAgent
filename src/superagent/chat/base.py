"""Chat notifications: ticket progress, help requests, approvals, SLA alerts, digests."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel

log = logging.getLogger(__name__)

Severity = Literal["info", "good", "warning", "attention"]


class Notification(BaseModel):
    kind: str  # started | triaged | escalated | help_needed | approval_needed | resolved | handed_over | sla | major | digest
    title: str
    text: str = ""
    ticket_id: str = ""
    facts: dict[str, str] = {}
    url: str = ""  # link to the console case page
    ticket_url: str = ""
    severity: Severity = "info"
    mention: str = ""  # e.g. "@L3 Support"


class ChatChannel(ABC):
    name = "chat"

    @abstractmethod
    async def send(self, n: Notification) -> None: ...

    async def safe_send(self, n: Notification) -> None:
        try:
            await self.send(n)
        except Exception as e:  # noqa: BLE001 - chat must never break ticket processing
            log.warning("chat notification failed (%s): %s", n.kind, e)


class ConsoleChannel(ChatChannel):
    """Logs notifications (and keeps them in memory for the local console/tests)."""

    name = "console"

    def __init__(self):
        self.sent: list[Notification] = []

    async def send(self, n: Notification) -> None:
        self.sent.append(n)
        log.info("[chat:%s] %s %s | %s", n.kind, n.ticket_id, n.title, n.text[:200])
