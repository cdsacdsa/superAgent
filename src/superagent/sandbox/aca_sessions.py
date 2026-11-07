"""Azure Container Apps dynamic sessions sandbox.

Every case gets its own session (``identifier=<case id>``): a Hyper-V isolated container
in a session pool with egress disabled. Sessions are reclaimed by the pool after the cooldown.

Two pool types are supported:
* ``python`` (built-in PythonLTS code interpreter, no image to build). Shell commands run
  through ``subprocess`` inside the session.
* ``custom``: our own image from ``sandbox/`` (troubleshooting tools + a tiny exec API).

The worker's managed identity needs the role *Azure ContainerApps Session Executor* on the pool.
"""

from __future__ import annotations

import json
import re
import time

import httpx

from .base import ExecResult, Sandbox

API_VERSION = "2024-02-02-preview"
SCOPE = "https://dynamicsessions.io/.default"


def _safe_identifier(session_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9-]", "-", session_id).lower()[:100]


class AcaSessionsSandbox(Sandbox):
    name = "aca_sessions"
    targets = ("linux",)

    def __init__(self, pool_endpoint: str, pool_type: str = "python", mi_client_id: str = "",
                 transport: httpx.AsyncBaseTransport | None = None, token_provider=None):  # fmt: skip
        self.endpoint = pool_endpoint.rstrip("/")
        self.pool_type = pool_type
        self.mi_client_id = mi_client_id
        self._token_provider = token_provider
        self._cred = None
        self.http = httpx.AsyncClient(timeout=300, transport=transport)

    async def aclose(self):
        await self.http.aclose()

    async def _token(self) -> str:
        if self._token_provider:
            return await self._token_provider()
        if self._cred is None:
            from azure.identity.aio import DefaultAzureCredential

            self._cred = DefaultAzureCredential(managed_identity_client_id=self.mi_client_id or None)
        return (await self._cred.get_token(SCOPE)).token

    async def exec(self, session_id, command, timeout_s, network="none"):
        # Egress is controlled by the pool configuration (EgressDisabled); `network` is enforced by policy.
        ident = _safe_identifier(session_id)
        headers = {"Authorization": f"Bearer {await self._token()}"}
        t0 = time.monotonic()
        if self.pool_type == "custom":
            r = await self.http.post(
                f"{self.endpoint}/exec",
                params={"identifier": ident},
                json={"command": command, "timeout": timeout_s},
                headers=headers,
            )
            r.raise_for_status()
            d = r.json()
            return ExecResult(d.get("exit_code", -1), d.get("stdout", ""), d.get("stderr", ""),
                              int((time.monotonic() - t0) * 1000))  # fmt: skip

        code = (
            "import subprocess, json\n"
            f"r = subprocess.run({json.dumps(command)}, shell=True, capture_output=True, text=True, timeout={int(timeout_s)})\n"
            "print(json.dumps({'exit_code': r.returncode, 'stdout': r.stdout[-20000:], 'stderr': r.stderr[-8000:]}))\n"
        )
        r = await self.http.post(
            f"{self.endpoint}/code/execute",
            params={"api-version": API_VERSION, "identifier": ident},
            json={"properties": {"codeInputType": "inline", "executionType": "synchronous", "code": code}},
            headers=headers,
        )
        r.raise_for_status()
        props = r.json().get("properties", {})
        dur = int((time.monotonic() - t0) * 1000)
        stdout = props.get("stdout", "")
        try:
            d = json.loads(stdout.strip().splitlines()[-1])
            return ExecResult(int(d["exit_code"]), d["stdout"], d["stderr"], dur)
        except (ValueError, KeyError, IndexError):
            return ExecResult(0 if props.get("status") == "Success" else 1, stdout, props.get("stderr", ""), dur)
