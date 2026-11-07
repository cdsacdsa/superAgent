"""Domain model: tickets (ITSM view) and cases (the agent's own working record)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- ITSM side


class TicketType(StrEnum):
    INCIDENT = "incident"
    SERVICE_REQUEST = "service_request"
    PROBLEM = "problem"
    CHANGE_RELATED = "change_related"


class TicketState(StrEnum):
    """Normalised ITIL states; each connector maps these to its tool's values."""

    NEW = "new"
    IN_PROGRESS = "in_progress"
    ON_HOLD_CALLER = "on_hold_awaiting_caller"
    ON_HOLD_OTHER = "on_hold_other"
    RESOLVED = "resolved"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class Comment(BaseModel):
    author: str = ""
    text: str
    internal: bool = False  # work note (internal) vs customer-visible comment
    created_at: datetime = Field(default_factory=utcnow)


class Ticket(BaseModel):
    id: str  # stable id used as case id (e.g. INC0012345 / SD-123)
    system_id: str = ""  # connector-specific id (sys_id / issue id)
    title: str
    description: str = ""
    type: TicketType = TicketType.INCIDENT
    state: TicketState = TicketState.NEW
    priority: int | None = None  # 1..4 as reported by the ITSM tool
    impact: int | None = None
    urgency: int | None = None
    category: str = ""
    requester: str = ""
    requester_email: str = ""
    assignment_group: str = ""
    assigned_to: str = ""
    configuration_item: str = ""
    opened_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    comments: list[Comment] = []
    url: str = ""
    raw: dict[str, Any] = {}


# --------------------------------------------------------------------------- agent side


class CaseStatus(StrEnum):
    NEW = "new"
    TRIAGED = "triaged"
    INVESTIGATING = "investigating"
    EXPERT_REVIEW = "expert_review"
    WAITING_HUMAN = "waiting_human"
    WAITING_REQUESTER = "waiting_requester"
    PROPOSED = "proposed"
    RESOLVED = "resolved"
    HANDED_OVER = "handed_over"
    FAILED = "failed"


TERMINAL_STATUSES = {CaseStatus.RESOLVED, CaseStatus.HANDED_OVER}


class Triage(BaseModel):
    ticket_type: TicketType = TicketType.INCIDENT
    category: str = "general"
    impact: int = 3  # 1 high .. 3 low
    urgency: int = 3
    priority: int = 4  # computed by itil.priority_from(impact, urgency)
    complexity: int = 2  # 1 trivial .. 5 very hard
    major_incident: bool = False
    security_related: bool = False
    summary: str = ""
    keywords: list[str] = []
    rationale: str = ""


class Finding(BaseModel):
    kind: Literal["symptom", "evidence", "hypothesis", "ruled_out", "action", "kb_reference"]
    text: str
    evidence: str = ""
    tier: str = ""
    at: datetime = Field(default_factory=utcnow)


class OutcomeStatus(StrEnum):
    RESOLVED = "resolved"
    NEEDS_REQUESTER_INFO = "needs_requester_info"
    NEEDS_HUMAN = "needs_human"
    ESCALATE_TO_EXPERT = "escalate_to_expert"
    CANNOT_RESOLVE = "cannot_resolve"


class Outcome(BaseModel):
    status: OutcomeStatus
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    root_cause: str = ""
    resolution_steps: list[str] = []
    customer_message: str = ""
    work_note: str = ""
    questions_for_requester: list[str] = []
    question_for_human: str = ""
    kb_citations: list[str] = []
    close_code: str = "Solved (Permanently)"
    is_workaround: bool = False
    tier: str = ""


class HelpRequest(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    kind: Literal["question", "approval"] = "question"
    question: str
    context: str = ""
    urgency: Literal["low", "normal", "high"] = "normal"
    blocking: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    answered_at: datetime | None = None
    answered_by: str = ""
    answer: str = ""
    approved: bool | None = None
    payload: dict[str, Any] = {}  # e.g. the proposed outcome or sandbox command awaiting approval


class SandboxRun(BaseModel):
    command: str
    purpose: str = ""
    target: str = "linux"
    network: str = "none"
    allowed: bool = True
    reason: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    at: datetime = Field(default_factory=utcnow)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd
        self.calls += other.calls


class Case(BaseModel):
    id: str  # == ticket id
    ticket: Ticket
    status: CaseStatus = CaseStatus.NEW
    triage: Triage | None = None
    findings: list[Finding] = []
    sandbox_runs: list[SandboxRun] = []
    help_requests: list[HelpRequest] = []
    outcomes: list[Outcome] = []
    change_drafts: list[dict[str, Any]] = []
    tiers_used: list[str] = []
    usage: dict[str, Usage] = {}
    attempts: int = 0
    current_tier: str = ""
    hold_since: datetime | None = None
    requester_reminders: int = 0
    problem_id: str = ""
    handover_note: str = ""
    sla_response_due: datetime | None = None
    sla_resolution_due: datetime | None = None
    sla_notified: list[str] = []
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    lease_owner: str = ""
    lease_until: datetime | None = None
    etag: str | None = None

    @property
    def total_cost(self) -> float:
        return round(sum(u.cost_usd for u in self.usage.values()), 6)

    @property
    def pending_help(self) -> list[HelpRequest]:
        return [h for h in self.help_requests if h.answered_at is None]

    @property
    def last_outcome(self) -> Outcome | None:
        return self.outcomes[-1] if self.outcomes else None


class CaseEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    case_id: str
    type: str  # e.g. triaged, tool_call, model_call, escalated, itsm_update, resolved
    message: str
    data: dict[str, Any] = {}
    at: datetime = Field(default_factory=utcnow)


class QueueMessage(BaseModel):
    kind: Literal["process_ticket", "resume_case"]
    ticket_id: str
    reason: str = ""
