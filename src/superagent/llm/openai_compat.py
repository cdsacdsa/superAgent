"""OpenAI-compatible provider: DeepSeek V4.1 Flash via OpenRouter, the DeepSeek API, or
Azure AI Foundry (any endpoint that speaks /chat/completions with function calling)."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from ..config import PriceEntry
from ..models import Usage
from .base import AgentRunResult, LLMProvider, ToolCall, ToolExecutor, ToolSpec, parse_json_loose

log = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        tier: str,
        model: str,
        base_url: str,
        api_key: str,
        price: PriceEntry | None,
        client: AsyncOpenAI | None = None,
    ):
        super().__init__(tier, model, price)
        headers = {}
        if "openrouter.ai" in (base_url or ""):
            headers = {"HTTP-Referer": "https://github.com/cdsacdsa/superAgent", "X-Title": "SuperAgent"}
        self.client = client or AsyncOpenAI(
            base_url=base_url or None,
            api_key=api_key or "not-set",
            default_headers=headers,
            timeout=180,
            max_retries=3,
        )

    def _usage(self, resp: Any) -> Usage:
        u = getattr(resp, "usage", None)
        if not u:
            return Usage(calls=1)
        return self.usage(u.prompt_tokens or 0, u.completion_tokens or 0)

    @staticmethod
    def _tool_defs(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
            }
            for t in tools
        ]

    async def complete_json(self, system, user, schema, schema_name):
        sys = (
            f"{system}\n\nRespond with ONLY a JSON object that matches this JSON schema "
            f"(no prose, no code fences):\n{json.dumps(schema)}"
        )
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=2000,
        )
        text = resp.choices[0].message.content or "{}"
        return parse_json_loose(text), self._usage(resp)

    async def run_agent(self, system, user, tools, execute: ToolExecutor, max_steps):
        terminal = {t.name for t in tools if t.terminal}
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        result = AgentRunResult(terminal_call=None)
        nudged = False
        for step in range(1, max_steps + 1):
            result.steps = step
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=self._tool_defs(tools),
                    tool_choice="auto",
                    max_tokens=4000,
                )
            except Exception as e:  # network / provider error -> let the router escalate
                log.exception("fast-tier call failed")
                result.error = f"{type(e).__name__}: {e}"
                return result
            result.usage.add(self._usage(resp))
            choice = resp.choices[0]
            msg = choice.message
            calls = msg.tool_calls or []
            assistant: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            if calls:
                assistant["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"},
                    }
                    for c in calls
                ]
            messages.append(assistant)

            if not calls:
                result.final_text = msg.content or ""
                if not nudged:  # model answered in prose: ask once for the terminal tool
                    nudged = True
                    messages.append(
                        {
                            "role": "user",
                            "content": "You must finish by calling the `submit_outcome` tool with your "
                            "conclusion. Call it now (or call another tool if you need more evidence).",
                        }
                    )
                    continue
                return result

            stop_after = False
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    messages.append(
                        {"role": "tool", "tool_call_id": c.id, "content": "ERROR: arguments were not valid JSON"}
                    )
                    continue
                call = ToolCall(name=c.function.name, arguments=args, id=c.id)
                if call.name in terminal:
                    tr = await execute(call)
                    if not tr.is_error:
                        result.terminal_call = call
                        return result
                    messages.append({"role": "tool", "tool_call_id": c.id, "content": tr.content})
                    continue
                tr = await execute(call)
                messages.append(
                    {"role": "tool", "tool_call_id": c.id, "content": ("ERROR: " if tr.is_error else "") + tr.content}
                )
                stop_after = stop_after or tr.stop
            if stop_after:
                result.stopped_by_tool = True
                return result
        result.exhausted = True
        return result
