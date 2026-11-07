"""OpenAI-compatible provider: DeepSeek V4.1 Flash via OpenRouter, the DeepSeek API, or
Microsoft Foundry (any endpoint that speaks /chat/completions with function calling).

On Foundry (``https://<resource>.services.ai.azure.com/openai/v1/``) the container's managed
identity can replace the API key: pass ``api_key=azure_token_provider(client_id)``."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI, BadRequestError

from ..config import PriceEntry
from ..models import Usage
from .base import AgentRunResult, LLMProvider, ToolCall, ToolExecutor, ToolSpec, parse_json_loose

log = logging.getLogger(__name__)

FOUNDRY_SCOPE = "https://cognitiveservices.azure.com/.default"


def azure_token_provider(client_id: str = "") -> Callable[[], Awaitable[str]]:
    """Entra ID bearer tokens for Foundry, refreshed by azure-identity before they expire."""
    from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

    cred = DefaultAzureCredential(managed_identity_client_id=client_id or None)
    return get_bearer_token_provider(cred, FOUNDRY_SCOPE)


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        tier: str,
        model: str,
        base_url: str,
        api_key: str | Callable[[], Awaitable[str]],
        price: PriceEntry | None,
        client: AsyncOpenAI | None = None,
        reasoning_effort: str = "",
    ):
        super().__init__(tier, model, price)
        self.reasoning_effort = reasoning_effort  # e.g. "low" for GPT-5-class reasoning models
        self._limit_param = "max_tokens"  # switched to max_completion_tokens if the model asks for it
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

    async def _create(self, max_tokens: int, **kwargs: Any) -> Any:
        """chat.completions.create that also works with reasoning models (GPT-5 class), which reject
        ``max_tokens`` and count their reasoning against ``max_completion_tokens``."""
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        limit = max_tokens * 2 if self._limit_param == "max_completion_tokens" else max_tokens
        try:
            return await self.client.chat.completions.create(model=self.model, **{self._limit_param: limit}, **kwargs)
        except BadRequestError as e:
            if self._limit_param == "max_tokens" and "max_completion_tokens" in str(e):
                self._limit_param = "max_completion_tokens"
                return await self._create(max_tokens, **kwargs)
            raise

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
        resp = await self._create(
            2000,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
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
                resp = await self._create(4000, messages=messages, tools=self._tool_defs(tools), tool_choice="auto")
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
