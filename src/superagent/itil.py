"""ITIL 4 process logic implemented as plain, testable code.

The model never decides priority, SLA or state transitions. It only supplies signals
(impact, urgency, complexity), and these functions apply the organisation's rules.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .config import AgentPolicy
from .models import Case, CaseStatus, Outcome, OutcomeStatus, Ticket, TicketType, Triage, utcnow

# Impact (rows) x Urgency (cols), 1 = high. Standard ITIL priority matrix -> P1..P4.
PRIORITY_MATRIX: dict[tuple[int, int], int] = {
    (1, 1): 1, (1, 2): 2, (1, 3): 3,
    (2, 1): 2, (2, 2): 3, (2, 3): 4,
    (3, 1): 3, (3, 2): 4, (3, 3): 4,
}  # fmt: skip

PRIORITY_LABEL = {1: "P1 - Critical", 2: "P2 - High", 3: "P3 - Moderate", 4: "P4 - Low"}


def clamp_level(v: int | None, default: int = 3) -> int:
    try:
        return min(3, max(1, int(v if v is not None else default)))
    except (TypeError, ValueError):
        return default


def priority_from(impact: int | None, urgency: int | None) -> int:
    return PRIORITY_MATRIX[(clamp_level(impact), clamp_level(urgency))]


def finalize_triage(raw: Triage, ticket: Ticket) -> Triage:
    """Apply ITIL rules on top of the model's triage signals."""
    t = raw.model_copy()
    t.impact = clamp_level(t.impact)
    t.urgency = clamp_level(t.urgency)
    t.complexity = min(5, max(1, int(t.complexity or 2)))
    t.priority = priority_from(t.impact, t.urgency)
    # A Major Incident is P1 by definition.
    if t.major_incident:
        t.priority = 1
    # Respect a higher priority already set by the service desk (never silently downgrade).
    if ticket.priority and ticket.priority < t.priority:
        t.priority = ticket.priority
    return t


def sla_targets(policy: AgentPolicy, priority: int, opened_at: datetime) -> tuple[datetime, datetime]:
    cfg = policy.itil.sla_minutes.get(str(priority)) or policy.itil.sla_minutes["4"]
    return (
        opened_at + timedelta(minutes=cfg["response"]),
        opened_at + timedelta(minutes=cfg["resolution"]),
    )


def sla_status(case: Case, policy: AgentPolicy, now: datetime | None = None) -> str:
    """Return 'ok' | 'at_risk' | 'breached' for the resolution SLA."""
    if not case.sla_resolution_due or case.status in (CaseStatus.RESOLVED, CaseStatus.HANDED_OVER):
        return "ok"
    if case.status == CaseStatus.WAITING_REQUESTER:  # SLA clock paused while awaiting caller
        return "ok"
    now = now or utcnow()
    start = case.ticket.opened_at
    total = (case.sla_resolution_due - start).total_seconds() or 1
    used = (now - start).total_seconds()
    if used >= total:
        return "breached"
    if used / total >= policy.itil.sla_warning_ratio:
        return "at_risk"
    return "ok"


def may_auto_resolve(case: Case, outcome: Outcome, policy: AgentPolicy) -> tuple[bool, str]:
    """Decide whether a 'resolved' outcome can close the ticket without human approval."""
    rp = policy.resolution
    tr = case.triage
    if outcome.status != OutcomeStatus.RESOLVED:
        return False, "outcome is not 'resolved'"
    if not rp.auto_resolve:
        return False, "auto-resolve disabled (shadow/assist mode)"
    if outcome.confidence < rp.auto_resolve_threshold:
        return False, f"confidence {outcome.confidence:.2f} < {rp.auto_resolve_threshold:.2f}"
    if tr is None:
        return False, "no triage"
    if tr.major_incident or tr.security_related:
        return False, "major or security incident requires human closure"
    if tr.priority < rp.auto_resolve_max_priority:
        return False, f"priority P{tr.priority} requires human approval"
    if tr.ticket_type.value not in rp.auto_resolve_types:
        return False, f"type {tr.ticket_type.value} not allowed for auto-resolve"
    if rp.auto_resolve_categories and tr.category not in rp.auto_resolve_categories:
        return False, f"category '{tr.category}' not in auto-resolve list"
    if tr.ticket_type == TicketType.CHANGE_RELATED:
        return False, "change-related work requires the change process"
    return True, "policy allows auto-resolve"


# ---------------------------------------------------------------------------- redaction

_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token|client[_-]?secret)\b(\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9\-._~+/]{16,}=*"),
    re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b"),  # JWT
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(AccountKey|SharedAccessKey|SharedAccessSignature)(=)([^;\s]+)"),
    re.compile(r"\bsig=[A-Za-z0-9%]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
]


def redact(text: str) -> str:
    """Strip credentials and tokens from text before it is stored, posted or sent to a model."""
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        if pat.groups >= 3:
            out = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", out)
        else:
            out = pat.sub("[REDACTED]", out)
    return out
