"""Cost-aware model router: fast tier first, expert tier when needed, human after that."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..config import RoutingPolicy
from ..models import Case, Outcome, OutcomeStatus
from .base import AgentRunResult, LLMProvider

FAST, EXPERT = "fast", "expert"


@dataclass
class Decision:
    action: str  # "accept" | "escalate_expert" | "human"
    reason: str


class ModelRouter:
    def __init__(self, fast: LLMProvider, expert: LLMProvider | None, policy: RoutingPolicy):
        self.fast = fast
        self.expert = expert
        self.policy = policy
        self._expert_spend: dict[date, float] = {}

    # -- budgets ---------------------------------------------------------------------
    def set_expert_spend_today(self, usd: float) -> None:
        self._expert_spend[date.today()] = usd

    def record_expert_spend(self, usd: float) -> None:
        today = date.today()
        self._expert_spend[today] = self._expert_spend.get(today, 0.0) + usd

    def expert_spend_today(self) -> float:
        return self._expert_spend.get(date.today(), 0.0)

    def expert_available(self, case: Case) -> tuple[bool, str]:
        if not self.policy.expert_enabled or self.expert is None:
            return False, "expert tier disabled"
        if case.total_cost >= self.policy.per_ticket_budget_usd:
            return False, f"per-ticket budget ${self.policy.per_ticket_budget_usd:.2f} reached"
        if self.expert_spend_today() >= self.policy.daily_expert_budget_usd:
            return False, f"daily expert budget ${self.policy.daily_expert_budget_usd:.2f} reached"
        return True, "ok"

    # -- routing ---------------------------------------------------------------------
    def initial_tier(self, case: Case) -> str:
        t = case.triage
        ok, _ = self.expert_available(case)
        if t and ok:
            if t.complexity >= self.policy.expert_direct_complexity:
                return EXPERT
            if self.policy.p1_expert_first and (t.priority == 1 or t.major_incident):
                return EXPERT
        return FAST

    def provider(self, tier: str) -> LLMProvider:
        return self.expert if tier == EXPERT and self.expert else self.fast

    def max_steps(self, tier: str) -> int:
        return self.policy.expert_max_steps if tier == EXPERT else self.policy.fast_max_steps

    def decide(self, case: Case, tier: str, run: AgentRunResult, outcome: Outcome | None) -> Decision:
        """What to do after an investigation run on ``tier``."""
        can_escalate, why_not = self.expert_available(case)
        can_escalate = can_escalate and tier == FAST

        def escalate_or_human(reason: str) -> Decision:
            if can_escalate:
                return Decision("escalate_expert", reason)
            return Decision("human", f"{reason}; {why_not if tier == FAST else 'expert tier could not resolve'}")

        if run.refused:
            return Decision("human", "model declined the request (safety refusal)")
        if outcome is None:
            reason = run.error or ("step budget exhausted" if run.exhausted else "no valid outcome submitted")
            return escalate_or_human(reason)

        s = outcome.status
        if s in (OutcomeStatus.NEEDS_REQUESTER_INFO, OutcomeStatus.NEEDS_HUMAN):
            return Decision("accept", f"outcome {s.value}")
        if s == OutcomeStatus.RESOLVED:
            if outcome.confidence >= self.policy.min_confidence:
                return Decision("accept", f"resolved with confidence {outcome.confidence:.2f}")
            return escalate_or_human(f"low confidence {outcome.confidence:.2f}")
        # ESCALATE_TO_EXPERT or CANNOT_RESOLVE
        return escalate_or_human(f"outcome {s.value}")
