# Business case: SuperAgent as the foundation of 2nd and 3rd-line support

## The problem
- L2/L3 engineers spend much of their time on repeat issues that are already documented somewhere
  (KB, SharePoint, old tickets) but take a long time to find.
- Tickets wait in queues. Handovers between levels lose context ("what did you already try?").
- SLA breaches cluster around peaks, nights and weekends.
- Knowledge stays in people's heads. Problem management is reactive.

## The solution
An AI support engineer that works the queue 24/7 inside the existing ITSM process:
- **Resolves** routine incidents and requests, with cited sources and a customer-ready message.
- **Investigates** harder cases: it reproduces them in a sandbox, tests hypotheses, and gets a second opinion from a frontier model.
- **Escalates cleanly**, with a complete ITIL handover note. L3 engineers start where the agent stopped.
- **Feeds continual improvement**: known-error write-back, problem detection, knowledge-gap visibility.
- **Stays governed**: ITIL rules in code, human approval gates, full audit trail, budgets. It runs in our Azure tenant.

## Expected value (fill in with your baseline)

| KPI | Baseline (measure) | Target after 3 months |
|-----|--------------------|----------------------|
| Tickets handled end-to-end by the agent (L2 queue) | 0 % | 30–50 % |
| Mean time to resolve (agent-handled) | `<hours>` | minutes |
| Time to first meaningful response | `<hours>` | < 5 min, 24/7 |
| SLA compliance (P3/P4) | `<x %>` | +10–20 pp |
| Reopen rate of agent-resolved tickets | n/a | ≤ human baseline |
| Escalations with a complete handover note | `<x %>` | 100 % |
| L2/L3 hours freed per month | 0 | `tickets × resolution rate × avg handling time` |
| AI cost per ticket | n/a | < $0.25 average (see model-routing.md) |

**Example:** 2,000 L2 tickets/month × 35 % handled × 25 min average handling time ≈ **290 engineer-hours/month**
freed for project work and L3 depth, for about $150–400/month in model and Azure costs.

## Rollout plan

| Phase | Duration | Scope | Go/no-go criteria |
|-------|----------|-------|-------------------|
| 0 – Shadow | 2–4 weeks | 1 queue, `auto_resolve: false`; the agent proposes, humans approve | ≥ 70 % of proposals approved unchanged; no security findings |
| 1 – Assist | 4 weeks | Auto-resolve P3/P4 in 3–5 low-risk categories | Reopen rate ≤ baseline; CSAT stable |
| 2 – Autonomous L2 | ongoing | More queues and categories; VM sandbox; Teams workflows | KPI targets met for 4 consecutive weeks |
| 3 – L3 foundation | ongoing | Problem management automation, change drafting, more knowledge sources, runbook automation | Problem backlog trend down |

## Governance
- **Owner:** Service Desk / Support Operations manager. **Technical owner:** platform team.
- **Change:** policy changes (`config/agent.yaml`) go through normal change management. Each deployment is versioned in Git.
- **Risk register:** model errors (mitigated by approval gates and confidence thresholds), data protection (Azure-hosted
  options, redaction), cost (budgets), and vendor dependency (provider-agnostic design: switching models is configuration).
- **Review:** weekly accuracy sampling in Phase 0–1, then monthly.

## Why this design
- **ITIL-native.** Priorities, SLAs, work notes vs comments, problem and change practices are enforced by code, not left to a model.
- **Cost-aware.** A very cheap model does most of the work. The frontier model is used only when it adds value, and before a human is asked.
- **Safe experimentation.** Isolated sandboxes let the agent prove a fix before proposing it.
- **Azure-native and simple to run.** One `azd up`, managed identity, serverless data stores and scale to demand.
- **Extensible.** It is the base for 2nd and 3rd-line automation: new connectors, tools and policies plug in without redesign.

## Demo script (15 minutes)
1. Show the console board and the Teams channel.
2. Create a ticket ("Outlook keeps asking for password"). Show the triage card, the fast-tier investigation, the KB citation and the proposal card. Approve it, and show the ITSM ticket resolved with the customer message.
3. Create a hard, intermittent ticket. Show the escalation to Opus 5.5, the sandbox tests, and the handover note in L3's queue.
4. Show the security ticket routed straight to SecOps.
5. Show `/api/metrics`, cost per ticket, and the audit timeline.
