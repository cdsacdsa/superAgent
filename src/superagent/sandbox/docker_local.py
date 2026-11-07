"""Local Docker sandbox for development: a locked-down container per case."""

from __future__ import annotations

import asyncio
import re
import time

from .base import ExecResult, Sandbox


class DockerSandbox(Sandbox):
    name = "docker"
    targets = ("linux",)

    def __init__(self, image: str = "superagent-sandbox:latest", fallback_image: str = "python:3.12-slim"):
        self.image = image
        self.fallback_image = fallback_image
        self._started: dict[str, str] = {}

    @staticmethod
    def _name(session_id: str) -> str:
        return "sa-sbx-" + re.sub(r"[^a-zA-Z0-9_.-]", "-", session_id).lower()

    async def _run(self, *args: str, timeout: int = 60) -> tuple[int, str, str]:
        p = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(p.communicate(), timeout=timeout)
        except TimeoutError:
            p.kill()
            return 124, "", f"timed out after {timeout}s"
        return p.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    async def _ensure(self, session_id: str, network: str) -> str:
        name = self._name(session_id)
        if name in self._started:
            return name
        image = self.image
        rc, _, _ = await self._run("docker", "image", "inspect", image)
        if rc != 0:
            image = self.fallback_image
        net = "none" if network == "none" else "bridge"
        rc, _, err = await self._run(
            "docker", "run", "-d", "--rm", "--name", name, f"--network={net}",
            "--read-only", "--tmpfs", "/tmp:rw,size=256m,mode=1777", "--tmpfs", "/work:rw,size=256m,uid=65534,gid=65534",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=512m", "--cpus=1",
            "--pids-limit=128", "--user=65534:65534", "-w", "/work",
            image, "sleep", "7200", timeout=180,
        )  # fmt: skip
        if rc != 0 and "already in use" not in err:
            raise RuntimeError(f"docker run failed: {err.strip()}")
        self._started[name] = image
        return name

    async def exec(self, session_id, command, timeout_s, network="none"):
        name = await self._ensure(session_id, network)
        t0 = time.monotonic()
        rc, out, err = await self._run(
            "docker", "exec", name, "sh", "-c", f"timeout {int(timeout_s)} sh -c {_sh_quote(command)}",
            timeout=timeout_s + 10,
        )  # fmt: skip
        return ExecResult(rc, out, err, int((time.monotonic() - t0) * 1000))

    async def destroy(self, session_id):
        name = self._name(session_id)
        self._started.pop(name, None)
        await self._run("docker", "rm", "-f", name)


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"
