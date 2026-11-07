# Security

## Principles
1. **The agent cannot change production.** No tool writes to production systems. Fixes that need a change are drafted as RFCs for humans.
2. **Least privilege, no shared secrets in code.** One user-assigned managed identity. Each role is scoped to a single resource. Secrets live in Key Vault.
3. **Untrusted input everywhere.** Tickets, comments, knowledge content and command output are treated as data.
4. **Humans own risky decisions.** Approvals are required for closures (by policy), sandbox network access, P1/P2, security and Major Incidents.
5. **Everything is audited.** Append-only events in Cosmos DB plus Application Insights traces.

## Identity and access

| Principal | Access | Scope |
|-----------|--------|-------|
| Agent managed identity | AcrPull | the registry |
| | Key Vault Secrets User | the vault |
| | Cosmos DB Built-in Data Contributor | the account (local auth disabled) |
| | Service Bus Data Owner | the namespace (local auth disabled) |
| | Azure ContainerApps Session Executor | the session pool |
| | VM Contributor + Network Contributor | **`rg-<env>-sandbox` only** (if the VM sandbox is enabled) |
| | Search Index Data Contributor | the search service (optional) |
| | Cognitive Services User | Foundry resource (optional; granted by you) |
| | Graph `Sites.Read.All`, `Files.Read.All` (or `Sites.Selected`) | tenant (optional; admin consent) |
| ITSM integration user | itil role, or a custom least-privilege role | agent assignment groups |
| Console users | Entra ID sign-in, optionally restricted to a group | console only |

## Sandbox isolation
- **ACA dynamic sessions.** Each case gets its own Hyper-V isolated session. The pool has `EgressDisabled`. Sessions are
  destroyed on case close or after the cooldown. Sessions have no identity and no secrets. Access is only through the
  Entra-protected pool management endpoint.
- **VM sandbox.**
  - A dedicated resource group, a VNet with `defaultOutboundAccess: false`, and an NSG that denies Internet egress,
    lateral VNet traffic and all inbound.
  - No public IP. A random admin password that is never stored. Commands run only via Run Command.
  - Trusted Launch. A TTL tag plus a reaper job.
- **Policy engine (`sandbox/policy.py`).** Blocks:
  - destructive commands and fork bombs
  - crypto-miners and reverse shells
  - instance metadata and identity endpoints
  - cloud control-plane CLIs
  - credential dumping and credential files

  It also bounds runtime and output, requires approval for any network mode, and logs every blocked attempt.
- **Output handling.** Command output is truncated, redacted and wrapped as `<untrusted_output>` before the model sees it.

## Prompt-injection defences
- Ticket and knowledge content is wrapped in `<untrusted_ticket>` and `<untrusted_document>`, and the system prompt instructs the model to treat it as data.
- The model's tools are limited and policy-gated. The worst a hijacked model can do is:
  - write a misleading internal note or finding
  - request help
  - run a sandboxed command
  - propose an outcome, which still goes through the approval and auto-resolve policy
- Customer messages are created only by the orchestrator from `submit_outcome`, after redaction. Auto-resolve is restricted by priority, type, category and confidence.
- Triage cannot lower a priority set by the service desk. Security tickets bypass the agent entirely.

## Data protection
- **Redaction (`itil.redact`).** Passwords, tokens, JWTs, keys, connection strings and private keys are redacted:
  - in ticket text sent to models
  - in tool output
  - in notes, handovers and customer messages
- **Data residency.**
  - Cosmos DB, Service Bus and Key Vault stay in your chosen region.
  - The expert tier can run in Microsoft Foundry.
  - The fast tier can use an Azure AI Foundry deployment instead of OpenRouter.
  - Check your organisation's policy before you send ticket data to external model providers.
- **Retention.** Audit events have a 1-year TTL (`defaultTtl`). Case documents are kept until you delete them.

## Network
- The API is the only public endpoint, and it uses HTTPS only. Webhooks require a shared secret (constant-time comparison).
- **Optional hardening:**
  - Container Apps environment with VNet integration
  - private endpoints for Cosmos, Service Bus and Key Vault
  - IP restrictions on the ingress
  - Azure Front Door with WAF

## Threat model (summary)

| Threat | Mitigation |
|--------|------------|
| Malicious ticket text instructs the agent to exfiltrate data | Untrusted-data framing; no outbound network in the sandbox; no data-export tools; redaction; human approval |
| Agent "fixes" production incorrectly | No production tools; RFC drafts only; resolution approval policy |
| Runaway cost | Per-ticket and daily budgets; step limits; budget exhaustion → human |
| Sandbox breakout or abuse | Hyper-V isolation / isolated VM; no egress; no identity; deny policy; TTL |
| Stolen console access | Entra ID with assignment required and conditional access; audit of every action |
| Credential leakage in notes | Redaction; system prompt rule; credentials never handed to the sandbox |
| Duplicate or conflicting processing | Leases with optimistic concurrency; idempotent intake; duplicate detection |
