"""Microsoft Teams channel via a *Workflows* webhook ("Post to a channel when a webhook
request is received"). It posts Adaptive Cards with buttons linking to the agent console.

Setup: in the Teams channel choose ... > Workflows > "Post to a channel when a webhook request
is received", copy the URL and store it in Key Vault as ``teams-webhook-url``.
"""

from __future__ import annotations

import httpx

from .base import ChatChannel, Notification

ICON = {
    "started": "🎫", "triaged": "🧭", "escalated": "🧠", "help_needed": "🙋", "approval_needed": "✅",
    "resolved": "🟢", "handed_over": "🤝", "sla": "⏰", "major": "🚨", "digest": "📊", "requester": "💬",
}  # fmt: skip


def adaptive_card(n: Notification) -> dict:
    body: list[dict] = [
        {
            "type": "TextBlock",
            "size": "Medium",
            "weight": "Bolder",
            "wrap": True,
            "color": {"good": "Good", "warning": "Warning", "attention": "Attention"}.get(n.severity, "Default"),
            "text": f"{ICON.get(n.kind, 'ℹ️')} {n.ticket_id + ' · ' if n.ticket_id else ''}{n.title}",
        }
    ]
    if n.mention:
        body.append({"type": "TextBlock", "text": f"**{n.mention}**", "wrap": True})
    if n.text:
        body.append({"type": "TextBlock", "text": n.text[:3500], "wrap": True})
    if n.facts:
        body.append({"type": "FactSet", "facts": [{"title": k, "value": str(v)[:300]} for k, v in n.facts.items()]})
    actions = []
    if n.url:
        label = {"help_needed": "Answer", "approval_needed": "Review & approve"}.get(n.kind, "Open case")
        actions.append({"type": "Action.OpenUrl", "title": label, "url": n.url})
    if n.ticket_url and n.ticket_url.startswith("http"):
        actions.append({"type": "Action.OpenUrl", "title": "Open ticket", "url": n.ticket_url})
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": body,
    }
    if actions:
        card["actions"] = actions
    return card


class TeamsChannel(ChatChannel):
    name = "teams"

    def __init__(self, webhook_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.url = webhook_url
        self.http = httpx.AsyncClient(timeout=20, transport=transport)

    async def send(self, n: Notification) -> None:
        payload = {
            "type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": adaptive_card(n)}],
        }
        r = await self.http.post(self.url, json=payload)
        r.raise_for_status()
