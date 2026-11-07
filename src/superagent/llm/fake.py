"""Scripted / heuristic fake provider for tests and offline demos (no API keys needed)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..config import PriceEntry
from .base import AgentRunResult, LLMProvider, ToolCall, ToolExecutor

Script = list[list[ToolCall]]  # one list of tool calls per model turn


class FakeProvider(LLMProvider):
    """``triage``: dict returned by complete_json. ``scripts``: queue of agent scripts; each
    run_agent call consumes the next one. A script may be a callable(user_prompt) -> Script."""

    def __init__(
        self,
        tier: str,
        triage: dict[str, Any] | None = None,
        scripts: list[Script | Callable[[str], Script]] | None = None,
        model: str = "fake-model",
    ):
        super().__init__(tier, model, PriceEntry(input_per_mtok=1.0, output_per_mtok=5.0))
        self.triage = triage
        self.scripts = list(scripts or [])
        self.prompts: list[str] = []

    async def complete_json(self, system, user, schema, schema_name):
        self.prompts.append(user)
        return (self.triage or heuristic_triage(user)), self.usage(500, 150)

    async def run_agent(self, system, user, tools, execute: ToolExecutor, max_steps):
        self.prompts.append(user)
        script = self.scripts.pop(0) if self.scripts else heuristic_script(self.tier)
        if callable(script):
            script = script(user)
        terminal = {t.name for t in tools if t.terminal}
        result = AgentRunResult(terminal_call=None)
        for step, turn in enumerate(script[:max_steps], start=1):
            result.steps = step
            result.usage.add(self.usage(2000, 300))
            stop = False
            for call in turn:
                tr = await execute(call)
                if call.name in terminal and not tr.is_error:
                    result.terminal_call = call
                    return result
                stop = stop or tr.stop
            if stop:
                result.stopped_by_tool = True
                return result
        result.exhausted = len(script) >= max_steps
        return result


def heuristic_triage(text: str) -> dict[str, Any]:
    low = text.lower()
    major = any(w in low for w in ("outage", "all users", "everyone", "down for", "major"))
    security = any(w in low for w in ("phishing", "malware", "ransomware", "compromised", "breach"))
    complexity = 4 if any(w in low for w in ("intermittent", "random", "sometimes", "performance")) else 2
    return {
        "ticket_type": "service_request" if re.search(r"\b(request|access to|install|new laptop)\b", low) else "incident",
        "category": "network" if "vpn" in low or "network" in low else "application",
        "impact": 1 if major else 3,
        "urgency": 1 if major else 2,
        "complexity": complexity,
        "major_incident": major,
        "security_related": security,
        "summary": text.strip().splitlines()[0][:160] if text.strip() else "",
        "keywords": [w for w in re.findall(r"[a-zA-Z]{4,}", low)][:6],
        "rationale": "heuristic triage (fake provider)",
    }


def heuristic_script(tier: str) -> Script:
    """Demo behaviour: the fast tier investigates but is unsure; the expert tier resolves."""
    if tier == "fast":
        return [
            [ToolCall("search_knowledge", {"query": "known issue resolution"})],
            [ToolCall("sandbox_exec", {"command": "uname -a && cat /etc/os-release | head -3", "purpose": "baseline"})],
            [ToolCall("record_finding", {"kind": "hypothesis", "text": "Likely a known configuration issue."})],
            [
                ToolCall(
                    "submit_outcome",
                    {
                        "status": "escalate_to_expert",
                        "confidence": 0.45,
                        "root_cause": "Unclear",
                        "work_note": "Fast tier could not confirm the root cause.",
                    },
                )
            ],
        ]
    return [
        [ToolCall("search_knowledge", {"query": "root cause and fix"})],
        [
            ToolCall(
                "submit_outcome",
                {
                    "status": "resolved",
                    "confidence": 0.9,
                    "root_cause": "Configuration drift documented in KB.",
                    "resolution_steps": ["Apply the documented fix", "Verify with the requester"],
                    "customer_message": "We identified the cause and applied the documented fix. "
                    "Please confirm everything works on your side.",
                    "work_note": "Expert tier resolved using KB guidance.",
                    "kb_citations": ["mock://kb/KB0001"],
                },
            )
        ],
    ]
