"""Slack channel via an incoming webhook (Block Kit)."""

from __future__ import annotations

import httpx

from .base import ChatChannel, Notification
from .teams import ICON


class SlackChannel(ChatChannel):
    name = "slack"

    def __init__(self, webhook_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.url = webhook_url
        self.http = httpx.AsyncClient(timeout=20, transport=transport)

    async def send(self, n: Notification) -> None:
        head = f"{ICON.get(n.kind, 'ℹ️')} *{n.ticket_id + ' · ' if n.ticket_id else ''}{n.title}*"
        blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": head}}]
        if n.text:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": n.text[:2900]}})
        if n.facts:
            blocks.append({"type": "section", "fields": [{"type": "mrkdwn", "text": f"*{k}*\n{v}"} for k, v in list(n.facts.items())[:10]]})
        if n.url:
            blocks.append({"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Open case"}, "url": n.url}]})
        r = await self.http.post(self.url, json={"text": head, "blocks": blocks})
        r.raise_for_status()
