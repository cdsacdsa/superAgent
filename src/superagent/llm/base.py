"""Provider-neutral LLM interface.

Each provider runs its *own* native tool loop (OpenAI-style function calling for DeepSeek,
the Anthropic Messages API for Claude). The agent code only sees ``ToolSpec``, ``ToolCall``
and ``AgentRunResult``, so switching or adding providers does not touch the orchestrator.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import PriceEntry
from ..models import Usage


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema (object)
    terminal: bool = False  # calling this tool ends the loop (e.g. submit_outcome)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = ""


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    stop: bool = False  # tool asks the loop to stop (e.g. blocking help request)


ToolExecutor = Callable[[ToolCall], Awaitable[ToolResult]]


@dataclass
class AgentRunResult:
    terminal_call: ToolCall | None  # the terminal tool call (outcome), if any
    final_text: str = ""
    steps: int = 0
    stopped_by_tool: bool = False
    exhausted: bool = False
    refused: bool = False
    usage: Usage = field(default_factory=Usage)
    error: str = ""


class LLMProvider(ABC):
    tier: str
    model: str

    def __init__(self, tier: str, model: str, price: PriceEntry | None):
        self.tier = tier
        self.model = model
        self.price = price or PriceEntry()

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.price.input_per_mtok + output_tokens * self.price.output_per_mtok) / 1e6

    def usage(self, input_tokens: int, output_tokens: int) -> Usage:
        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self.cost(input_tokens, output_tokens),
            calls=1,
        )

    @abstractmethod
    async def complete_json(
        self, system: str, user: str, schema: dict[str, Any], schema_name: str
    ) -> tuple[dict[str, Any], Usage]:
        """Single structured call (used for triage)."""

    @abstractmethod
    async def run_agent(
        self,
        system: str,
        user: str,
        tools: list[ToolSpec],
        execute: ToolExecutor,
        max_steps: int,
    ) -> AgentRunResult:
        """Run a tool-using loop until a terminal tool is called, a tool stops it, or steps run out."""


def parse_json_loose(text: str) -> dict[str, Any]:
    """Parse JSON from model text, tolerating ```json fences or prose around it."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise
