"""The ticket lifecycle. Deterministic code owns the ITIL process; the LLMs only triage and investigate."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from ..chat.base import ChatChannel, Notification
from ..config import AgentPolicy
from ..itil import PRIORITY_LABEL, finalize_triage, may_auto_resolve, redact, sla_status, sla_targets
from ..itsm.base import ITSMConnector
from ..knowledge.base import KnowledgeHub
from ..llm.base import AgentRunResult
from ..llm.router import EXPERT, FAST, ModelRouter
from ..mcp_tools import McpToolBridge
from ..models import (
    Case,
    CaseEvent,
    CaseStatus,
    HelpRequest,
    Outcome,
    OutcomeStatus,
    QueueMessage,
    TicketState,
    Triage,
    Usage,
    utcnow,
)
from ..queue.base import WorkQueue
from ..sandbox.base import SandboxRouter
from ..sandbox.policy import CommandPolicy
from ..store.base import CaseStore
from . import prompts
from .handover import build_handover_note
from .tools import CaseToolbox, help_notification, tool_specs

log = logging.getLogger(__name__)


@dataclass
class Services:
    policy: AgentPolicy
    itsm: ITSMConnector
    knowledge: KnowledgeHub
    router: ModelRouter
    store: CaseStore
    queue: WorkQueue
    chat: ChatChannel
    sandbox: SandboxRouter | None
    base_url: str = "http://localhost:8000"
    worker_id: str = "worker-1"
    kedb_writer: object | None = None  # AzureSearchSource with .upsert(), optional
    sandbox_notes: dict[str, str] = field(default_factory=dict)  # target -> what the model should know about it
    mcp: McpToolBridge | None = None
    command_policy: CommandPolicy = field(init=False)

    def __post_init__(self):
        self.command_policy = CommandPolicy(self.policy.sandbox)

    @property
    def sandbox_targets(self) -> list[str]:
        return list(self.sandbox.targets) if self.sandbox else []

    async def event(self, case: Case, type_: str, message: str, data: dict | None = None) -> None:
        await self.store.add_event(CaseEvent(case_id=case.id, type=type_, message=redact(message)[:2000], data=data or {}))

    def case_url(self, case: Case) -> str:
        return f"{self.base_url}/cases/{case.id}"

    async def notify(self, case: Case, kind: str, title: str, text: str = "", severity="info", mention="",
                     facts: dict | None = None) -> None:  # fmt: skip
        base = {"Priority": PRIORITY_LABEL[case.triage.priority] if case.triage else "?",
                "Status": case.status.value, "AI cost": f"${case.total_cost:.4f}"}  # fmt: skip
        await self.chat.safe_send(
            Notification(kind=kind, title=title, text=redact(text), ticket_id=case.id, facts={**base, **(facts or {})},
                         url=self.case_url(case), ticket_url=case.ticket.url, severity=severity, mention=mention)
        )  # fmt: skip

    async def notify_help(self, case: Case, hr: HelpRequest) -> None:
        await self.event(case, "help_requested", hr.question, {"help_id": hr.id, "kind": hr.kind})
        await self.chat.safe_send(help_notification(case, hr, self.base_url))


class Orchestrator:
    def __init__(self, services: Services):
        self.s = services

    @property
    def policy(self) -> AgentPolicy:
        return self.s.policy

    # ============================================================ entry points
    async def intake(self, ticket_id: str, reason: str = "poll") -> bool:
        """Create a case for a new ticket and enqueue it. Idempotent."""
        ticket = await self.s.itsm.get_ticket(ticket_id)
        case = Case(id=ticket.id, ticket=ticket)
        created = await self.s.store.create_if_absent(case)
        if created:
            await self.s.event(case, "intake", f"ticket received via {reason}")
            await self.s.queue.send(QueueMessage(kind="process_ticket", ticket_id=ticket.id, reason=reason))
        return created

    async def requeue(self, ticket_id: str, reason: str = "manual") -> bool:
        """Queue an existing open case again, e.g. after its queue message was lost (in-memory queue restart).
        Cases waiting for a person or already closed are left alone."""
        case = await self.s.store.get(ticket_id)
        if case is None or case.status not in (CaseStatus.NEW, CaseStatus.TRIAGED, CaseStatus.INVESTIGATING,
                                                CaseStatus.EXPERT_REVIEW):  # fmt: skip
            return False
        await self.s.event(case, "requeued", f"queued again via {reason}")
        await self.s.queue.send(QueueMessage(kind="process_ticket", ticket_id=ticket_id, reason=reason))
        return True

    async def handle(self, msg: QueueMessage) -> None:
        case = await self.s.store.acquire(msg.ticket_id, self.s.worker_id)
        if case is None:
            existing = await self.s.store.get(msg.ticket_id)
            if existing is None and msg.kind == "process_ticket":
                await self.intake(msg.ticket_id, reason="queue")
            elif existing is not None and msg.kind == "resume_case":
                await self.s.queue.send(msg, delay_s=30)  # case busy: retry the resume shortly
            return
        try:
            await self._process(case, msg)
        except Exception as e:
            log.exception("processing %s failed", case.id)
            await self.s.event(case, "error", f"processing failed: {type(e).__name__}: {e}")
            # count on the stored case (``case`` may be stale after _process saved newer versions)
            current = await self.s.store.get(case.id) or case
            current.attempts += 1
            current = await self._save(current)
            if current.attempts >= 3:
                await self._handover(current, f"agent error after {current.attempts} attempts: {type(e).__name__}")
                return  # handed over: do not redeliver
            raise
        finally:
            fresh = await self.s.store.get(case.id)
            if fresh and fresh.lease_owner == self.s.worker_id:
                await self.s.store.release(fresh)

    # ============================================================ main flow
    async def _process(self, case: Case, msg: QueueMessage) -> None:
        if case.status in (CaseStatus.RESOLVED, CaseStatus.HANDED_OVER):
            return
        if case.status == CaseStatus.NEW:
            case = await self._claim_and_triage(case)
            if case.status != CaseStatus.TRIAGED:
                return  # e.g. security handover
        if case.status == CaseStatus.WAITING_HUMAN and any(h.blocking for h in case.pending_help):
            return
        if case.status == CaseStatus.PROPOSED:
            return  # waiting for approval decision (handled by apply_human_response)
        if case.status == CaseStatus.WAITING_REQUESTER and msg.kind != "resume_case":
            return
        await self._investigate(case)

    async def _save(self, case: Case) -> Case:
        return await self.s.store.save(case)

    async def _claim_and_triage(self, case: Case) -> Case:
        s = self.s
        ticket = case.ticket
        await s.itsm.assign_to_agent(ticket)
        await s.itsm.add_work_note(
            ticket, f"[{self.policy.agent_name}] Ticket acknowledged and assigned to the support agent. Triage started."
        )
        raw, usage = await s.router.fast.complete_json(
            prompts.triage_system(), prompts.triage_user(case), prompts.TRIAGE_SCHEMA, "triage"
        )
        self._add_usage(case, FAST, usage)
        try:
            tri = Triage.model_validate(raw)
        except Exception:  # noqa: BLE001 - malformed triage -> safe defaults, more complex
            tri = Triage(summary=ticket.title, complexity=3, rationale="triage parse failed; defaults applied")
        tri = finalize_triage(tri, ticket)
        case.triage = tri
        case.sla_response_due, case.sla_resolution_due = sla_targets(self.policy, tri.priority, ticket.opened_at)
        case.status = CaseStatus.TRIAGED
        case = await self._save(case)
        await s.event(case, "triaged", f"{tri.ticket_type.value}/{tri.category} {PRIORITY_LABEL[tri.priority]} "
                                       f"complexity {tri.complexity}", tri.model_dump())  # fmt: skip
        if ticket.impact != tri.impact or ticket.urgency != tri.urgency:
            try:
                await s.itsm.set_priority(ticket, tri.impact, tri.urgency)
            except Exception as e:  # noqa: BLE001
                log.warning("could not set priority: %s", e)
        await s.itsm.add_work_note(
            ticket,
            f"[{self.policy.agent_name}] Triage: {tri.ticket_type.value} | category {tri.category} | "
            f"{PRIORITY_LABEL[tri.priority]} (impact {tri.impact}, urgency {tri.urgency}) | complexity {tri.complexity}/5.\n"
            f"{tri.summary}",
        )
        await s.notify(case, "started", f"Working on: {ticket.title[:120]}", tri.summary,
                       facts={"Type": tri.ticket_type.value, "Category": tri.category,
                              "Complexity": f"{tri.complexity}/5"})  # fmt: skip

        if tri.security_related:
            await s.notify(case, "major", "Security-related ticket routed to Security", tri.rationale,
                           severity="attention", mention=self.policy.itil.security_group)  # fmt: skip
            await self._handover(case, "security-related ticket: routed to the security team per policy",
                                 group=self.policy.itil.security_group)  # fmt: skip
            return await self.s.store.get(case.id) or case
        if tri.major_incident:
            await s.notify(case, "major", "Possible MAJOR INCIDENT", f"{ticket.title}\n\n{tri.summary}",
                           severity="attention", mention=self.policy.itil.major_incident_group)  # fmt: skip
            await s.itsm.add_work_note(ticket, f"[{self.policy.agent_name}] Major Incident criteria met: "
                                               f"{self.policy.itil.major_incident_group} notified.")  # fmt: skip
        await self._check_problem_pattern(case)
        return case

    async def _investigate(self, case: Case) -> None:
        s = self.s
        tier = case.current_tier or s.router.initial_tier(case)
        for _ in range(2):  # at most fast -> expert
            case.current_tier = tier
            case.status = CaseStatus.EXPERT_REVIEW if tier == EXPERT else CaseStatus.INVESTIGATING
            if tier not in case.tiers_used:
                case.tiers_used.append(tier)
            case = await self._save(case)
            provider = s.router.provider(tier)
            await s.event(case, "investigation_started", f"{tier} tier ({provider.model})")

            n_outcomes = len(case.outcomes)
            toolbox = CaseToolbox(s, case, tier)
            extra_tools = await s.mcp.tool_specs() if s.mcp else []
            run: AgentRunResult = await provider.run_agent(
                prompts.investigator_system(self.policy.agent_name, expert=(tier == EXPERT)),
                prompts.case_brief(case, tier, s.knowledge.names, s.sandbox_targets),
                tool_specs(s.sandbox_targets, s.knowledge.names, s.sandbox_notes) + extra_tools,
                toolbox,
                s.router.max_steps(tier),
            )
            case = toolbox.case
            self._add_usage(case, tier, run.usage)
            if tier == EXPERT:
                s.router.record_expert_spend(run.usage.cost_usd)
            outcome = case.outcomes[-1] if len(case.outcomes) > n_outcomes else None
            await s.event(case, "investigation_finished",
                          f"{tier}: steps={run.steps} outcome={outcome.status.value if outcome else 'none'} "
                          f"conf={outcome.confidence if outcome else 0:.2f} cost=${run.usage.cost_usd:.4f}"
                          + (f" error={run.error}" if run.error else ""))  # fmt: skip

            if run.stopped_by_tool and outcome is None:
                case.status = CaseStatus.WAITING_HUMAN
                await self._save(case)
                await s.itsm.add_work_note(case.ticket, f"[{self.policy.agent_name}] Waiting for input from "
                                                        "an internal engineer (see support chat).")  # fmt: skip
                return

            decision = s.router.decide(case, tier, run, outcome)
            await s.event(case, "routing", f"{decision.action}: {decision.reason}")
            if decision.action == "escalate_expert":
                await s.notify(case, "escalated", "Escalated to expert model", decision.reason,
                               facts={"Model": s.router.provider(EXPERT).model})  # fmt: skip
                tier = EXPERT
                continue
            if decision.action == "human":
                await self._handover(case, decision.reason)
                return
            await self._apply_outcome(case, outcome)  # type: ignore[arg-type]
            return

    # ============================================================ outcomes
    async def _apply_outcome(self, case: Case, o: Outcome) -> None:
        s, t = self.s, case.ticket
        name = self.policy.agent_name
        if o.status == OutcomeStatus.RESOLVED:
            ok, why = may_auto_resolve(case, o, self.policy)
            if ok:
                await self._resolve(case, o, approved_by="policy")
                return
            hr = HelpRequest(kind="approval", question=f"Approve proposed resolution for {case.id}? ({why})",
                             context=_outcome_summary(o), blocking=True, payload={"outcome": o.model_dump(mode="json")})  # fmt: skip
            case.help_requests.append(hr)
            case.status = CaseStatus.PROPOSED
            await self._save(case)
            await s.itsm.add_work_note(t, f"[{name}] Proposed resolution (awaiting human approval: {why}):\n"
                                          f"{_outcome_summary(o)}")  # fmt: skip
            await s.notify_help(case, hr)
            return
        if o.status == OutcomeStatus.NEEDS_REQUESTER_INFO:
            msg = o.customer_message
            if o.questions_for_requester:
                msg += "\n\n" + "\n".join(f"- {q}" for q in o.questions_for_requester)
            await s.itsm.add_customer_comment(t, redact(msg))
            await s.itsm.set_state(t, TicketState.ON_HOLD_CALLER, f"[{name}] Awaiting information from the caller.")
            case.status = CaseStatus.WAITING_REQUESTER
            case.hold_since = utcnow()
            await self._save(case)
            await s.notify(case, "requester", "Waiting for requester information", "\n".join(o.questions_for_requester))
            return
        if o.status == OutcomeStatus.NEEDS_HUMAN:
            if not any(h.blocking for h in case.pending_help):
                hr = HelpRequest(question=o.question_for_human or o.work_note or "Agent needs help on this case",
                                 context=o.work_note)  # fmt: skip
                case.help_requests.append(hr)
                await s.notify_help(case, hr)
            case.status = CaseStatus.WAITING_HUMAN
            await self._save(case)
            return
        await self._handover(case, f"outcome {o.status.value}")

    async def _resolve(self, case: Case, o: Outcome, approved_by: str) -> None:
        s, t = self.s, case.ticket
        if self.policy.resolution.post_customer_updates and o.customer_message:
            await s.itsm.add_customer_comment(t, redact(o.customer_message))
        close_notes = _outcome_summary(o) + f"\n\nResolved by {self.policy.agent_name} ({o.tier} tier), approved by {approved_by}."
        code = "Solved (Work Around)" if o.is_workaround else (o.close_code or "Solved (Permanently)")
        await s.itsm.resolve(t, code, redact(close_notes))
        case.status = CaseStatus.RESOLVED
        await self._save(case)
        await s.event(case, "resolved", f"resolved ({code}) approved by {approved_by}")
        await s.notify(case, "resolved", f"Resolved: {t.title[:120]}", o.root_cause, severity="good",
                       facts={"Tier": o.tier, "Confidence": f"{o.confidence:.2f}", "Approved by": approved_by})  # fmt: skip
        await self._cleanup(case)
        await self._kedb_writeback(case, o)

    async def _handover(self, case: Case, reason: str, group: str | None = None) -> None:
        s = self.s
        group = group or self.policy.itil.handover_group
        note = build_handover_note(case, reason, self.policy.agent_name)
        case.handover_note = note
        await s.itsm.reassign(case.ticket, group, note)
        case.status = CaseStatus.HANDED_OVER
        await self._save(case)
        await s.event(case, "handed_over", f"to {group}: {reason}")
        await s.notify(case, "handed_over", f"Handed over to {group}", reason, severity="warning", mention=group)
        await self._cleanup(case)

    async def _cleanup(self, case: Case) -> None:
        if self.s.sandbox:
            try:
                await self.s.sandbox.destroy(case.id)
            except Exception as e:  # noqa: BLE001
                log.info("sandbox cleanup for %s failed: %s", case.id, e)

    async def _kedb_writeback(self, case: Case, o: Outcome) -> None:
        w = self.s.kedb_writer
        if not w or not case.triage:
            return
        try:
            await w.upsert([{  # type: ignore[attr-defined]
                "id": case.id, "title": case.ticket.title, "source": "resolved_ticket", "ticket_id": case.id,
                "category": case.triage.category, "url": case.ticket.url,
                "content": f"Symptoms: {case.triage.summary}\nRoot cause: {o.root_cause}\nFix: " + "; ".join(o.resolution_steps),
            }])  # fmt: skip
        except Exception as e:  # noqa: BLE001
            log.warning("KEDB write-back failed: %s", e)

    async def _check_problem_pattern(self, case: Case) -> None:
        """Problem management: N similar incidents in a window -> propose (or create) a Problem record."""
        tr = case.triage
        if not tr or tr.ticket_type.value != "incident":
            return
        window = utcnow() - timedelta(hours=self.policy.itil.problem_window_hours)
        kws = {k.lower() for k in tr.keywords}
        similar = [
            c for c in await self.s.store.list(limit=500)
            if c.id != case.id and c.triage and c.created_at >= window and c.triage.category == tr.category
            and len(kws & {k.lower() for k in c.triage.keywords}) >= 2
        ]  # fmt: skip
        if len(similar) + 1 < self.policy.itil.problem_threshold:
            return
        if any(c.problem_id for c in similar):
            case.problem_id = next(c.problem_id for c in similar if c.problem_id)
            await self._save(case)
            return
        related = [case.id] + [c.id for c in similar]
        text = f"{len(related)} similar '{tr.category}' incidents in {self.policy.itil.problem_window_hours}h: {', '.join(related)}"
        if self.policy.itil.auto_create_problem:
            pid = await self.s.itsm.create_problem(f"Recurring: {tr.summary[:120]}", text, related)
            case.problem_id = pid
            await self._save(case)
            await self.s.notify(case, "major", f"Problem {pid} created", text, severity="warning")
        else:
            case.problem_id = "proposed"
            await self._save(case)
            await self.s.notify(case, "major", "Problem record suggested", text, severity="warning",
                                mention=self.policy.itil.handover_group)  # fmt: skip

    # ============================================================ human responses (from console)
    async def apply_human_response(self, case_id: str, help_id: str, answer: str, by: str,
                                   approved: bool | None = None) -> Case:  # fmt: skip
        case = await self.s.store.get(case_id)
        if case is None:
            raise KeyError(case_id)
        hr = next((h for h in case.help_requests if h.id == help_id), None)
        if hr is None:
            raise KeyError(help_id)
        if hr.answered_at:
            return case
        hr.answer, hr.answered_by, hr.answered_at, hr.approved = answer, by, utcnow(), approved
        await self.s.event(case, "human_response", f"{by}: {'approved' if approved else 'rejected' if approved is False else 'answered'}"
                           f" {answer[:300]}", {"help_id": help_id})  # fmt: skip

        if hr.kind == "approval" and "outcome" in hr.payload:
            o = Outcome.model_validate(hr.payload["outcome"])
            if approved:
                await self._save(case)
                await self._resolve(case, o, approved_by=by)
                return case
            # rejected: re-investigate with feedback if given, otherwise hand over
            if answer.strip() and case.attempts < 2:
                case.attempts += 1
                case.status = CaseStatus.INVESTIGATING
                case.current_tier = EXPERT if self.s.router.expert_available(case)[0] else case.current_tier
                await self._save(case)
                await self.s.queue.send(QueueMessage(kind="resume_case", ticket_id=case.id, reason="proposal_rejected"))
            else:
                await self._save(case)
                await self._handover(case, f"proposed resolution rejected by {by}")
            return case

        await self._save(case)
        if not any(h.blocking for h in case.pending_help) and case.status in (CaseStatus.WAITING_HUMAN, CaseStatus.INVESTIGATING,
                                                                              CaseStatus.EXPERT_REVIEW):  # fmt: skip
            await self.s.queue.send(QueueMessage(kind="resume_case", ticket_id=case.id, reason="human_answered"))
        return case

    # ============================================================ housekeeping (SLA, requester replies)
    async def housekeeping(self) -> None:
        s = self.s
        active = await s.store.list(status=[CaseStatus.TRIAGED, CaseStatus.INVESTIGATING, CaseStatus.EXPERT_REVIEW,
                                            CaseStatus.WAITING_HUMAN, CaseStatus.WAITING_REQUESTER, CaseStatus.PROPOSED])  # fmt: skip
        for case in active:
            st = sla_status(case, self.policy)
            if st != "ok" and st not in case.sla_notified:
                case.sla_notified.append(st)
                await self._save(case)
                await s.notify(case, "sla", f"SLA {st.replace('_', ' ')}", f"Resolution due {case.sla_resolution_due:%Y-%m-%d %H:%M} UTC",
                               severity="attention" if st == "breached" else "warning",
                               mention=self.policy.itil.handover_group if st == "breached" else "")  # fmt: skip
            if case.status == CaseStatus.WAITING_REQUESTER:
                await self._check_requester_reply(case)

    async def _check_requester_reply(self, case: Case) -> None:
        s = self.s
        ticket = await s.itsm.get_ticket(case.id)
        since = case.hold_since or case.updated_at
        replied = any(not c.internal and c.author.lower() not in ("superagent", self.policy.agent_name.lower())
                      and c.created_at > since for c in ticket.comments)  # fmt: skip
        if replied or ticket.state == TicketState.IN_PROGRESS:
            case.ticket = ticket
            await s.itsm.set_state(ticket, TicketState.IN_PROGRESS, f"[{self.policy.agent_name}] Caller replied; resuming.")
            case.status = CaseStatus.INVESTIGATING
            await self._save(case)
            await s.queue.send(QueueMessage(kind="resume_case", ticket_id=case.id, reason="requester_replied"))
            return
        waited = utcnow() - since
        limit = timedelta(hours=self.policy.itil.requester_timeout_hours)
        if waited > 2 * limit:
            await self._handover(case, "no reply from caller after reminder")
        elif waited > limit and case.requester_reminders == 0:
            case.requester_reminders = 1
            await self._save(case)
            await s.itsm.add_customer_comment(ticket, "Friendly reminder: we are waiting for the information requested "
                                                      "above to continue working on your ticket.")  # fmt: skip

    # ============================================================ helpers
    @staticmethod
    def _add_usage(case: Case, tier: str, usage: Usage) -> None:
        case.usage.setdefault(tier, Usage()).add(usage)


def _outcome_summary(o: Outcome) -> str:
    parts = []
    if o.root_cause:
        parts.append(f"Root cause: {o.root_cause}")
    if o.resolution_steps:
        parts.append("Resolution:\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(o.resolution_steps, 1)))
    if o.work_note:
        parts.append(f"Notes: {o.work_note}")
    if o.kb_citations:
        parts.append("References: " + ", ".join(o.kb_citations))
    parts.append(f"Confidence: {o.confidence:.2f} ({o.tier} tier)")
    return "\n".join(parts)
