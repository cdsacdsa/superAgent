"""Mock sandbox for tests and the offline demo."""

from __future__ import annotations

from .base import ExecResult, Sandbox


class MockSandbox(Sandbox):
    name = "mock"
    targets = ("linux", "windows")

    def __init__(self, responses: dict[str, ExecResult] | None = None):
        self.responses = responses or {}
        self.commands: list[tuple[str, str]] = []
        self.destroyed: list[str] = []

    async def exec(self, session_id, command, timeout_s, network="none"):
        self.commands.append((session_id, command))
        for key, res in self.responses.items():
            if key in command:
                return res
        return ExecResult(0, f"[mock sandbox] executed: {command}\n", "", 5)

    async def destroy(self, session_id):
        self.destroyed.append(session_id)
