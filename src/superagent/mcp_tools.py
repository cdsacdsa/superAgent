"""Tools from MCP servers: a product's own API, GitHub, Atlassian, internal admin tools, ...

Only the tools named in a server's ``allowed_tools`` are offered to the model, so the agent never
sees a write tool you did not opt into. Calls go through the case toolbox like the built-in tools:
they are audited as case events and their output is redacted and truncated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from .config import McpServerConfig
from .llm.base import ToolSpec

log = logging.getLogger(__name__)

Connect = Callable[[McpServerConfig], AbstractAsyncContextManager[Any]]


def tool_name(server: str, tool: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", f"mcp_{server}__{tool}")[:64]


def _resolve(value: str) -> str:
    return os.environ.get(value[4:], "") if value.startswith("env:") else value


@asynccontextmanager
async def connect_http(cfg: McpServerConfig):
    from mcp import Client
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    headers = {k: _resolve(v) for k, v in cfg.headers.items()}
    async with create_mcp_http_client(headers=headers) as http:
        async with Client(streamable_http_client(cfg.url, http_client=http),
                          read_timeout_seconds=cfg.timeout_seconds) as client:  # fmt: skip
            yield client


class McpToolBridge:
    def __init__(self, servers: list[McpServerConfig], connect: Connect | None = None):
        self.servers = [s for s in servers if s.allowed_tools]
        self._connect = connect or connect_http
        self._specs: dict[str, list[ToolSpec]] = {}  # server -> tools (only servers that answered)
        self._routes: dict[str, tuple[McpServerConfig, str]] = {}
        self._lock = asyncio.Lock()

    async def tool_specs(self) -> list[ToolSpec]:
        """Discovered lazily; a server that is down is retried on the next investigation."""
        async with self._lock:
            for cfg in self.servers:
                if cfg.name not in self._specs:
                    try:
                        self._specs[cfg.name] = await self._discover(cfg)
                    except Exception as e:  # noqa: BLE001 - one broken server must not stop the agent
                        log.warning("MCP server %s unavailable: %s", cfg.name, e)
        return [spec for specs in self._specs.values() for spec in specs]

    async def _discover(self, cfg: McpServerConfig) -> list[ToolSpec]:
        async with self._connect(cfg) as client:
            listed = await asyncio.wait_for(client.list_tools(), cfg.timeout_seconds)
        allowed = set(cfg.allowed_tools)
        specs = []
        for t in listed.tools:
            if t.name not in allowed:
                continue
            name = tool_name(cfg.name, t.name)
            self._routes[name] = (cfg, t.name)
            label = cfg.name + (f": {cfg.description}" if cfg.description else "")
            specs.append(ToolSpec(name, f"[{label}] {t.description or t.name}"[:1000],
                                  t.input_schema or {"type": "object", "properties": {}}))  # fmt: skip
        missing = allowed - {t.name for t in listed.tools}
        if missing:
            log.warning("MCP server %s does not offer allowed tools: %s", cfg.name, ", ".join(sorted(missing)))
        return specs

    def owns(self, name: str) -> bool:
        return name in self._routes

    async def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        cfg, tool = self._routes[name]
        async with self._connect(cfg) as client:
            res = await asyncio.wait_for(client.call_tool(tool, arguments), cfg.timeout_seconds)
        parts = []
        for c in res.content:
            text = getattr(c, "text", None)
            parts.append(text if text is not None else f"[{getattr(c, 'type', 'content')} content omitted]")
        if not parts and res.structured_content is not None:
            parts.append(json.dumps(res.structured_content, default=str))
        return "\n".join(parts) or "(no output)", bool(res.is_error)
