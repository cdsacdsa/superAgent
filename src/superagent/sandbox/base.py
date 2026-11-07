"""Sandbox interface. One sandbox *session* per case, reused across commands, destroyed at the end."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int = 0


class Sandbox(ABC):
    name: str = "sandbox"
    targets: tuple[str, ...] = ("linux",)

    @abstractmethod
    async def exec(self, session_id: str, command: str, timeout_s: int, network: str = "none") -> ExecResult: ...

    async def destroy(self, session_id: str) -> None:
        return None

    async def aclose(self) -> None:
        return None


class SandboxRouter(Sandbox):
    """Routes by target OS: e.g. linux -> ACA dynamic sessions, windows -> ephemeral VM."""

    name = "router"

    def __init__(self, by_target: dict[str, Sandbox]):
        self.by_target = by_target
        self.targets = tuple(by_target)

    def _pick(self, target: str) -> Sandbox:
        if target not in self.by_target:
            raise ValueError(f"no sandbox available for target '{target}' (available: {', '.join(self.targets)})")
        return self.by_target[target]

    async def exec_on(self, target: str, session_id: str, command: str, timeout_s: int, network: str = "none"):
        sb = self._pick(target)
        if len(getattr(sb, "targets", ())) > 1 and sb.name == "azure_vm":
            return await sb.exec(session_id, command, timeout_s, network, target=target)  # type: ignore[call-arg]
        return await sb.exec(session_id, command, timeout_s, network)

    async def exec(self, session_id, command, timeout_s, network="none"):
        return await self.exec_on(self.targets[0], session_id, command, timeout_s, network)

    async def destroy(self, session_id):
        for s in {id(s): s for s in self.by_target.values()}.values():
            await s.destroy(session_id)
