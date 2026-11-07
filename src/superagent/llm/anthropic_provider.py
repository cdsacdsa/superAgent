"""Claude provider (expert tier, default Claude Opus 5.5).

Works against the Claude API (``anthropic``) or Claude in Microsoft Foundry (``foundry``),
using the official Anthropic Python SDK. Foundry can authenticate with the container's
managed identity, so no API key is needed when both tiers run inside Azure.

Notes on the request shape for Claude Opus 5.5:
* adaptive thinking; depth is controlled with ``output_config.effort``
* no ``temperature``, no forced ``tool_choice``, no assistant prefill
* the full ``response.content`` (including thinking blocks) is appended back each turn
* on the Claude API, server-side refusal fallbacks are enabled (``fallbacks: "default"``).
  A final ``stop_reason == "refusal"`` is treated as "needs a human".
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from ..config import PriceEntry
from ..models import Usage
from .base import AgentRunResult, LLMProvider, ToolCall, ToolExecutor, parse_json_loose

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def build_anthropic_client(
    provider: str,
    api_key: str = "",
    base_url: str = "",
    foundry_resource: str = "",
    foundry_use_entra: bool = True,
    azure_client_id: str = "",
):
    if provider == "foundry":
        if foundry_use_entra and not api_key:
            from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

            cred = DefaultAzureCredential(managed_identity_client_id=azure_client_id or None)
            token_provider = get_bearer_token_provider(cred, "https://cognitiveservices.azure.com/.default")
            return anthropic.AsyncAnthropicFoundry(resource=foundry_resource, azure_ad_token_provider=token_provider)
        return anthropic.AsyncAnthropicFoundry(resource=foundry_resource, api_key=api_key)
    kwargs: dict[str, Any] = {"max_retries": 3}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return anthropic.AsyncAnthropic(**kwargs)


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        tier: str,
        model: str,
        client: Any,
        price: PriceEntry | None,
        effort: str = "high",
        server_side_fallback: bool = False,
    ):
        super().__init__(tier, model, price)
        self.client = client
        self.effort = effort
        self.server_side_fallback = server_side_fallback

    def _usage_from(self, resp: Any) -> Usage:
        u = resp.usage
        inp = (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) + (
            getattr(u, "cache_creation_input_tokens", 0) or 0
        )
        return self.usage(inp, u.output_tokens or 0)

    async def _create(self, **kwargs):
        kwargs.setdefault("model", self.model)
        kwargs.setdefault("thinking", {"type": "adaptive"})
        kwargs.setdefault("output_config", {"effort": self.effort})
        kwargs.setdefault("cache_control", {"type": "ephemeral"})
        if self.server_side_fallback:
            return await self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        return await self.client.messages.create(**kwargs)

    @staticmethod
    def _text(resp: Any) -> str:
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    async def complete_json(self, system, user, schema, schema_name):
        resp = await self._create(
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
        return parse_json_loose(self._text(resp)), self._usage_from(resp)

    async def run_agent(self, system, user, tools, execute: ToolExecutor, max_steps):
        terminal = {t.name for t in tools if t.terminal}
        tool_defs = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        result = AgentRunResult(terminal_call=None)
        nudged = False
        for step in range(1, max_steps + 1):
            result.steps = step
            try:
                resp = await self._create(max_tokens=16000, system=system, tools=tool_defs, messages=messages)
            except anthropic.RateLimitError as e:
                result.error = f"rate limited: {e}"
                return result
            except anthropic.APIStatusError as e:
                log.exception("expert-tier call failed")
                result.error = f"API error {e.status_code}: {e.message}"
                return result
            except anthropic.APIConnectionError as e:
                result.error = f"connection error: {e}"
                return result
            result.usage.add(self._usage_from(resp))

            if resp.stop_reason == "refusal":
                result.refused = True
                return result

            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "pause_turn":
                continue

            tool_uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
            if not tool_uses:
                result.final_text = self._text(resp)
                if not nudged and resp.stop_reason != "max_tokens":
                    nudged = True
                    messages.append(
                        {"role": "user", "content": "Finish by calling the `submit_outcome` tool with your conclusion."}
                    )
                    continue
                return result

            results: list[dict[str, Any]] = []
            stop_after = False
            for b in tool_uses:
                args = b.input if isinstance(b.input, dict) else json.loads(b.input or "{}")
                call = ToolCall(name=b.name, arguments=args, id=b.id)
                tr = await execute(call)
                if call.name in terminal and not tr.is_error:
                    result.terminal_call = call
                    return result
                results.append(
                    {"type": "tool_result", "tool_use_id": b.id, "content": tr.content, "is_error": tr.is_error}
                )
                stop_after = stop_after or tr.stop
            # all tool results go back in ONE user message
            messages.append({"role": "user", "content": results})
            if stop_after:
                result.stopped_by_tool = True
                return result
        result.exhausted = True
        return result
