"""ITIL-style handover note for functional escalation to a human team."""

from __future__ import annotations

from ..itil import PRIORITY_LABEL, redact
from ..models import Case


def build_handover_note(case: Case, reason: str, agent_name: str) -> str:
    tr = case.triage
    lines = [f"=== {agent_name} handover ===", f"Reason for escalation: {reason}", ""]
    if tr:
        lines += [
            f"Classification: {tr.ticket_type.value} / {tr.category} | {PRIORITY_LABEL[tr.priority]} "
            f"(impact {tr.impact}, urgency {tr.urgency}) | complexity {tr.complexity}/5",
            f"Summary: {tr.summary}",
            "",
        ]
    by_kind: dict[str, list[str]] = {}
    for f in case.findings:
        by_kind.setdefault(f.kind, []).append(f.text + (f" [evidence: {f.evidence[:200]}]" if f.evidence else ""))
    for kind, title in [("symptom", "Symptoms"), ("evidence", "Evidence collected"), ("hypothesis", "Hypotheses"),
                        ("ruled_out", "Ruled out"), ("action", "Actions taken"), ("kb_reference", "Knowledge consulted")]:  # fmt: skip
        if by_kind.get(kind):
            lines.append(f"{title}:")
            lines += [f"  - {x}" for x in by_kind[kind][-10:]]
    if case.sandbox_runs:
        lines.append("Sandbox tests:")
        lines += [f"  - `{r.command[:150]}` -> {'exit ' + str(r.exit_code) if r.allowed else 'blocked: ' + r.reason}"
                  for r in case.sandbox_runs[-8:]]  # fmt: skip
    for o in case.outcomes[-2:]:
        lines += ["", f"{o.tier} tier conclusion ({o.status.value}, confidence {o.confidence:.2f}):"]
        if o.root_cause:
            lines.append(f"  Suspected root cause: {o.root_cause}")
        if o.work_note:
            lines.append(f"  Notes: {o.work_note}")
        if o.resolution_steps:
            lines.append("  Suggested steps: " + "; ".join(o.resolution_steps))
    if case.change_drafts:
        lines.append("")
        lines.append("Draft change requests: " + "; ".join(c.get("title", "") for c in case.change_drafts))
    answered = [h for h in case.help_requests if h.answered_at]
    if answered:
        lines.append("")
        lines.append("Human input received:")
        lines += [f"  - {h.question[:120]} -> {h.answer[:200]}" for h in answered[-5:]]
    lines += ["", f"Models used: {', '.join(case.tiers_used) or 'none'} | AI cost so far: ${case.total_cost:.4f}"]
    return redact("\n".join(lines))
