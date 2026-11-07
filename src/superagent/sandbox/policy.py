"""Command policy for the troubleshooting sandbox.

The sandbox is already isolated (no egress, no production credentials, disposable). The
policy is defence in depth: it blocks obviously destructive or abusive commands, forces
approval for network access, and bounds runtime and output size.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import SandboxPolicy

DENY_PATTERNS = [
    (r"\brm\s+-[a-z]*r[a-z]*f?\s+/(\s|$|\*)", "recursive delete of filesystem root"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
    (r"\bmkfs(\.\w+)?\b|\bdd\s+if=.*of=/dev/", "disk formatting / raw device writes"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "power state changes"),
    (r"\b(xmrig|minerd|cpuminer|ethminer)\b", "crypto-mining"),
    (r"\bnc\b.*\s-e\s|bash\s+-i\s+>&\s*/dev/tcp|/dev/tcp/\d", "reverse shell"),
    (r"169\.254\.169\.254|metadata\.azure\.com|IDENTITY_ENDPOINT|MSI_ENDPOINT", "instance metadata / identity endpoint"),
    (r"\b(az|azcopy|kubectl)\s+.*(login|delete|create|set)\b", "cloud control-plane commands"),
    (r"(?i)\b(Invoke-Mimikatz|mimikatz|sekurlsa|lsadump)\b", "credential dumping"),
    (r"/etc/shadow|\.ssh/id_[rd]sa|\.aws/credentials|\.azure/", "credential file access"),
    (r"(?i)\bFormat-Volume\b|\bRemove-Item\s+.*-Recurse.*\s[A-Z]:\\\s*$", "destructive Windows commands"),
]


@dataclass
class PolicyDecision:
    allowed: bool
    needs_approval: bool
    reason: str


class CommandPolicy:
    def __init__(self, policy: SandboxPolicy):
        self.policy = policy
        self.patterns = [(re.compile(p, re.I), why) for p, why in DENY_PATTERNS] + [
            (re.compile(p, re.I), "organisation deny rule") for p in policy.extra_deny_patterns
        ]

    def check(self, command: str, network: str = "none", approved: bool = False) -> PolicyDecision:
        cmd = (command or "").strip()
        if not cmd:
            return PolicyDecision(False, False, "empty command")
        if len(cmd) > 8000:
            return PolicyDecision(False, False, "command too long")
        for pat, why in self.patterns:
            if pat.search(cmd):
                return PolicyDecision(False, False, f"blocked by policy: {why}")
        if network != "none" and self.policy.network_requires_approval and not approved:
            return PolicyDecision(False, True, "network access from the sandbox requires human approval")
        return PolicyDecision(True, False, "allowed")

    def truncate(self, text: str) -> str:
        limit = self.policy.max_output_chars
        if text and len(text) > limit:
            half = limit // 2
            return f"{text[:half]}\n...[{len(text) - limit} chars truncated]...\n{text[-half:]}"
        return text or ""
