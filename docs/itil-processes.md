# ITIL 4 practices implemented

SuperAgent separates the **process** from the **intelligence**. The ITIL rules live in deterministic code
(`itil.py`, `agent/orchestrator.py`) and in policy (`config/agent.yaml`). The models provide signals
(classification, findings, a proposed resolution) and never decide process outcomes on their own.

## Incident management

| Step | Implementation |
|------|----------------|
| Logging & categorisation | Triage returns type, category, impact, urgency, complexity and flags |
| Prioritisation | `priority_from(impact, urgency)` uses the standard 3×3 matrix → P1–P4. A Major Incident is always P1. The agent **never downgrades** a priority set by the service desk |
| SLA | Response/resolution targets per priority (`itil.sla_minutes`). The SLA monitor alerts at 75 % of the window (`sla_warning_ratio`) and at breach. The clock is **paused** while On Hold – Awaiting Caller |
| Initial diagnosis | Knowledge search (KB, similar tickets, SharePoint, RAG) before any experiment |
| Investigation & diagnosis | Hypotheses are tested in the sandbox. Each step is recorded in the case file (symptom / evidence / hypothesis / ruled_out / action) |
| Functional escalation | Fast tier → expert tier → human team (`handover_group`), with an ITIL-style handover note |
| Hierarchical escalation | SLA breach and Major Incident notifications mention the responsible groups |
| Resolution & recovery | Close code (`Solved (Permanently)` / `Solved (Work Around)`), close notes with root cause, steps, references and confidence, plus a customer-facing message |
| Closure | Resolved → Closed is left to the ITSM tool's own auto-close (and the caller's confirmation) |
| Communication | Internal **work notes** vs customer **comments** are always kept separate |

### Major incidents
When triage detects widespread impact (`major_incident=true`), the agent:
1. sets P1,
2. notifies `major_incident_group` immediately with an attention card,
3. adds a work note,
4. keeps investigating on the **expert tier** (`p1_expert_first`),
5. **never auto-resolves**: a human must approve.

### Security incidents
Tickets flagged `security_related` (phishing, malware, compromise, data leak) are routed **immediately** to
`security_group`. The agent does not investigate them itself, which preserves forensic integrity and chain of custody.

## Request fulfilment
Service requests (access, software, information) follow the same flow. The agent checks the fulfilment
procedure in the KB and, where the action needs privileged access, asks for approval or hands over.
Auto-resolve can be limited to specific categories (`auto_resolve_categories`).

## Problem management
- **Reactive.** When `problem_threshold` similar incidents (same category with overlapping keywords) occur within
  `problem_window_hours`, the agent proposes a Problem record in chat, or creates it if `auto_create_problem: true`.
  Later related incidents are linked to it.
- **Known errors.** When AI Search is enabled, resolved cases are written back to the KEDB index
  (symptoms → root cause → fix). The next similar ticket finds them first.
- Workarounds are flagged as such (`is_workaround` → `Solved (Work Around)`) so problem managers can find them.

## Change enablement
The agent has **no tool that changes production**. When a fix needs a change, it calls `draft_change_request`
(title, justification, implementation, backout and test plans, risk, change type). The draft is attached to the
case for the change manager or CAB. Tickets classified `change_related` are never auto-resolved.

## Knowledge management
Every resolution cites its sources (`kb_citations`). Resolved cases feed the KEDB. Low-confidence or unclear
cases produce knowledge gaps that are visible in the handover notes.

## Service level management (reporting)
`/api/metrics` and the daily digest report:
- volume and auto-resolution rate
- resolution by tier and handovers
- SLA breaches
- AI cost per case

See [business-case.md](business-case.md) for KPI targets.

## Human-in-the-loop (governance)
| Situation | Behaviour |
|-----------|-----------|
| Proposed resolution, auto-resolve not allowed | Approval card → console → Approve / Reject (+ feedback → re-investigation on the expert tier) |
| Agent blocked on a person | `request_human_help` → card → console answer → case resumes with the answer as trusted input |
| Sandbox needs network | Approval request; once approved, the exact same command is allowed |
| Requester information needed | Customer comment with questions, **On Hold – Awaiting Caller**, automatic resume on reply, one reminder after `requester_timeout_hours`, handover after twice that |

### Rollout modes
| Mode | `auto_resolve` | Behaviour |
|------|----------------|-----------|
| Shadow (default) | false | Every resolution is a proposal for a human to approve |
| Assist | true, `auto_resolve_categories` limited, `auto_resolve_max_priority: 3` | Low-risk tickets close automatically |
| Autonomous L2 | true, broader categories | Humans handle exceptions and handovers |
