"""The agent's tools, bound to one case. Every call is policy-checked, redacted and audited."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ..chat.base import Notification
from ..itil import redact
from ..llm.base import ToolCall, ToolResult, ToolSpec
from ..models import Case, Finding, HelpRequest, Outcome, SandboxRun

if TYPE_CHECKING:
    from .orchestrator import Services

OUTCOME_SCHEMA = {
    "type": "object",
    "required": ["status", "confidence", "work_note"],
    "properties": {
        "status": {
            "type": "string",
            "enum": ["resolved", "needs_requester_info", "needs_human", "escalate_to_expert", "cannot_resolve"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "root_cause": {"type": "string"},
        "resolution_steps": {"type": "array", "items": {"type": "string"}},
        "customer_message": {"type": "string", "description": "Message to the requester (required if resolved or needs_requester_info)"},
        "work_note": {"type": "string", "description": "Internal summary for IT staff"},
        "questions_for_requester": {"type": "array", "items": {"type": "string"}},
        "question_for_human": {"type": "string"},
        "kb_citations": {"type": "array", "items": {"type": "string"}},
        "close_code": {"type": "string", "description": "e.g. 'Solved (Permanently)', 'Solved (Work Around)'"},
        "is_workaround": {"type": "boolean"},
    },
}  # fmt: skip


def tool_specs(sandbox_targets: list[str], sources: list[str]) -> list[ToolSpec]:
    specs = [
        ToolSpec(
            "search_knowledge",
            "Search enterprise knowledge (" + ", ".join(sources or ["none"]) + ") for documentation, known errors "
            "and runbooks. Use specific technical terms. Returns titles, excerpts and URLs to cite.",
            {"type": "object", "required": ["query"], "properties": {
                "query": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string", "enum": sources or ["none"]},
                            "description": "Optional subset of sources"}}},
        ),
        ToolSpec(
            "find_similar_tickets",
            "Find previously resolved tickets similar to a description, with their resolution notes.",
            {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
        ),
        ToolSpec(
            "get_ticket_details",
            "Re-read the current ticket including the latest comments and work notes.",
            {"type": "object", "properties": {}},
        ),
        ToolSpec(
            "record_finding",
            "Add an entry to the case file (read by colleagues and on handover).",
            {"type": "object", "required": ["kind", "text"], "properties": {
                "kind": {"type": "string", "enum": ["symptom", "evidence", "hypothesis", "ruled_out", "action", "kb_reference"]},
                "text": {"type": "string"}, "evidence": {"type": "string"}}},
        ),
        ToolSpec(
            "add_work_note",
            "Post a short internal progress note on the ticket (visible to IT staff only). Use sparingly.",
            {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}},
        ),
        ToolSpec(
            "request_human_help",
            "Ask a human engineer for help via the support chat. Use when blocked on something only a person can "
            "provide (access, a decision, production data, physical action). blocking=true pauses the case until "
            "they answer; blocking=false lets you continue meanwhile.",
            {"type": "object", "required": ["question"], "properties": {
                "question": {"type": "string"}, "context": {"type": "string"},
                "urgency": {"type": "string", "enum": ["low", "normal", "high"]},
                "blocking": {"type": "boolean"}}},
        ),
        ToolSpec(
            "draft_change_request",
            "Draft an ITIL Change Request (RFC) for a fix that needs a production change. It is NOT executed; "
            "humans and the CAB review it.",
            {"type": "object", "required": ["title", "justification", "implementation_plan", "backout_plan", "risk"],
             "properties": {"title": {"type": "string"}, "justification": {"type": "string"},
                            "implementation_plan": {"type": "string"}, "backout_plan": {"type": "string"},
                            "test_plan": {"type": "string"},
                            "risk": {"type": "string", "enum": ["low", "moderate", "high"]},
                            "change_type": {"type": "string", "enum": ["standard", "normal", "emergency"]}}},
        ),
        ToolSpec(
            "submit_outcome",
            "Finish the investigation with your conclusion. Call exactly once, at the end.",
            OUTCOME_SCHEMA,
            terminal=True,
        ),
    ]  # fmt: skip
    if sandbox_targets:
        specs.insert(
            3,
            ToolSpec(
                "sandbox_exec",
                "Run a shell command in an isolated, disposable troubleshooting sandbox (no production access, no "
                "network unless approved). Use it to reproduce issues, test commands/scripts, validate configs, "
                f"parse logs. Targets: {', '.join(sandbox_targets)} (windows = PowerShell). The session persists "
                "for this case, so files you create stay available.",
                {"type": "object", "required": ["command", "purpose"], "properties": {
                    "command": {"type": "string"}, "purpose": {"type": "string"},
                    "target": {"type": "string", "enum": sandbox_targets},
                    "network": {"type": "string", "enum": ["none", "internet"],
                                "description": "internet requires human approval"}}},
            ),  # fmt: skip
        )
    return specs


class CaseToolbox:
    def __init__(self, services: Services, case: Case, tier: str):
        self.s = services
        self.case = case
        self.tier = tier

    async def __call__(self, call: ToolCall) -> ToolResult:
        handler = getattr(self, f"t_{call.name}", None)
        if handler is None:
            return ToolResult(f"unknown tool {call.name}", is_error=True)
        await self.s.event(self.case, "tool_call", f"{self.tier}: {call.name}", {"args": _short(call.arguments)})
        try:
            res: ToolResult = await handler(**call.arguments)
        except TypeError as e:
            return ToolResult(f"invalid arguments: {e}", is_error=True)
        except Exception as e:  # noqa: BLE001 - tool failures are reported back to the model
            await self.s.event(self.case, "tool_error", f"{call.name} failed: {e}")
            return ToolResult(f"tool failed: {type(e).__name__}: {e}", is_error=True)
        res.content = redact(res.content)
        return res

    # -- knowledge ---------------------------------------------------------------------
    async def t_search_knowledge(self, query: str, sources: list[str] | None = None) -> ToolResult:
        hits = await self.s.knowledge.search(query, self.s.policy.knowledge_top_k, sources)
        if not hits:
            return ToolResult("No results. Try different terms or another source.")
        lines = [
            f"[{i}] ({h.source}) {h.title}\nurl: {h.url or 'n/a'}\n<untrusted_document>\n{h.content}\n</untrusted_document>"
            for i, h in enumerate(hits, 1)
        ]
        return ToolResult("\n\n".join(lines))

    async def t_find_similar_tickets(self, query: str) -> ToolResult:
        ts = await self.s.itsm.search_similar(query, 5)
        if not ts:
            return ToolResult("No similar resolved tickets found.")
        out = []
        for t in ts:
            notes = t.raw.get("close_notes") or next((c.text for c in reversed(t.comments) if c.internal), "")
            out.append(f"- {t.id}: {t.title}\n  resolution: {notes[:800]}\n  url: {t.url}")
        return ToolResult("\n".join(out))

    async def t_get_ticket_details(self) -> ToolResult:
        from .prompts import ticket_block

        self.case.ticket = await self.s.itsm.get_ticket(self.case.id)
        return ToolResult(ticket_block(self.case, max_comments=50))

    # -- case file ---------------------------------------------------------------------
    async def t_record_finding(self, kind: str, text: str, evidence: str = "") -> ToolResult:
        self.case.findings.append(Finding(kind=kind, text=redact(text), evidence=redact(evidence)[:2000], tier=self.tier))
        return ToolResult("recorded")

    async def t_add_work_note(self, text: str) -> ToolResult:
        await self.s.itsm.add_work_note(self.case.ticket, f"[{self.s.policy.agent_name}] {redact(text)}")
        return ToolResult("work note added")

    async def t_draft_change_request(self, **rfc) -> ToolResult:
        rfc["status"] = "draft"
        rfc["drafted_by_tier"] = self.tier
        self.case.change_drafts.append(rfc)
        await self.s.event(self.case, "change_draft", f"RFC drafted: {rfc.get('title')}", rfc)
        return ToolResult("RFC draft saved for human/CAB review. Mention it in your outcome.")

    # -- humans ------------------------------------------------------------------------
    async def t_request_human_help(self, question: str, context: str = "", urgency: str = "normal",
                                   blocking: bool = True) -> ToolResult:  # fmt: skip
        hr = HelpRequest(kind="question", question=redact(question), context=redact(context), urgency=urgency,
                         blocking=blocking)  # fmt: skip
        self.case.help_requests.append(hr)
        await self.s.notify_help(self.case, hr)
        if blocking:
            return ToolResult("Help requested. The case is paused until a human answers.", stop=True)
        return ToolResult("Help requested (non-blocking). Continue with what you can do meanwhile.")

    # -- sandbox -----------------------------------------------------------------------
    async def t_sandbox_exec(self, command: str, purpose: str, target: str | None = None,
                             network: str = "none") -> ToolResult:  # fmt: skip
        sb = self.s.sandbox
        if sb is None:
            return ToolResult("sandbox is disabled", is_error=True)
        target = target or (self.s.sandbox_targets[0] if self.s.sandbox_targets else "linux")
        approved = any(
            h.kind == "approval" and h.approved and h.payload.get("command") == command
            for h in self.case.help_requests
        )
        decision = self.s.command_policy.check(command, network, approved=approved)
        run = SandboxRun(command=redact(command), purpose=purpose, target=target, network=network,
                         allowed=decision.allowed, reason=decision.reason)  # fmt: skip
        if not decision.allowed:
            self.case.sandbox_runs.append(run)
            await self.s.event(self.case, "sandbox_blocked", decision.reason, {"command": command})
            if decision.needs_approval:
                hr = HelpRequest(kind="approval", question=f"Allow sandbox command with network access?\n`{command}`",
                                 context=purpose, blocking=False, payload={"command": command, "network": network})  # fmt: skip
                self.case.help_requests.append(hr)
                await self.s.notify_help(self.case, hr)
                return ToolResult("Network access needs human approval; request sent. Continue without it, or "
                                  "use request_human_help(blocking=true) if you cannot proceed.", is_error=True)  # fmt: skip
            return ToolResult(decision.reason, is_error=True)
        timeout = self.s.policy.sandbox.max_runtime_seconds
        res = await sb.exec_on(target, self.case.id, command, timeout, network)
        run.exit_code, run.duration_ms = res.exit_code, res.duration_ms
        run.stdout = self.s.command_policy.truncate(redact(res.stdout))
        run.stderr = self.s.command_policy.truncate(redact(res.stderr))
        self.case.sandbox_runs.append(run)
        await self.s.event(self.case, "sandbox_exec", f"exit {res.exit_code}: {command[:120]}",
                           {"duration_ms": res.duration_ms})  # fmt: skip
        return ToolResult(f"exit_code: {res.exit_code}\n<untrusted_output>\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}\n</untrusted_output>")

    # -- finish ------------------------------------------------------------------------
    async def t_submit_outcome(self, **kwargs) -> ToolResult:
        try:
            outcome = Outcome.model_validate({**kwargs, "tier": self.tier})
        except ValidationError as e:
            return ToolResult(f"invalid outcome: {e.errors()[:3]}. Fix and call submit_outcome again.", is_error=True)
        if outcome.status.value in ("resolved", "needs_requester_info") and not outcome.customer_message.strip():
            return ToolResult("customer_message is required for this status. Call submit_outcome again.", is_error=True)
        self.case.outcomes.append(outcome)
        return ToolResult("outcome recorded")


def _short(args: dict) -> dict:
    s = json.dumps(args, default=str)
    return args if len(s) < 1500 else {"truncated": s[:1500]}


def help_notification(case: Case, hr: HelpRequest, base_url: str) -> Notification:
    kind = "approval_needed" if hr.kind == "approval" else "help_needed"
    return Notification(
        kind=kind,
        title="Approval needed" if hr.kind == "approval" else "Agent needs help",
        text=hr.question + (f"\n\nContext: {hr.context}" if hr.context else ""),
        ticket_id=case.id,
        facts={"Ticket": case.ticket.title[:120], "Priority": f"P{case.triage.priority}" if case.triage else "?",
               "Urgency": hr.urgency, "Blocking": "yes" if hr.blocking else "no"},  # fmt: skip
        url=f"{base_url}/cases/{case.id}#help-{hr.id}",
        ticket_url=case.ticket.url,
        severity="warning" if hr.urgency == "high" else "info",
    )
