# Configuration reference

There are two layers:
- **Environment variables** (prefix `SA_`). These hold endpoints, provider selection and secrets. In Azure they are
  set by Bicep, and the secrets are Key Vault references. Locally they come from a `.env` file. See `src/superagent/config.py → Settings`.
- **Policy file** `config/agent.yaml`. This holds behaviour: thresholds, ITIL rules, budgets and prices. See `AgentPolicy`.

## Environment variables

| Variable | Default | Notes |
|----------|---------|-------|
| `SA_ENVIRONMENT` | `local` | `local` allows unauthenticated console/webhooks; anything else requires auth |
| `SA_PUBLIC_BASE_URL` | `http://localhost:8000` | Used for links in chat cards |
| `SA_CONSOLE_API_KEY` | | API-key auth for the console (`?key=` once, then a cookie; or the `X-API-Key` header) |
| `SA_WEBHOOK_SECRET` | | Required header `X-SuperAgent-Secret` on `/webhooks/*` |
| `SA_RUN_WORKER_INPROC` | `true` | Run the worker inside the API process (local/demo) |
| `SA_WORKER_CONCURRENCY` | `4` | Parallel cases per worker replica |
| `SA_POLL_ENABLED` | `true` | Poll the ITSM queue every `poll_interval_seconds` |
| **Models** | | |
| `SA_FAST_PROVIDER` | `openai_compat` | `openai_compat` \| `anthropic` \| `foundry` \| `fake` |
| `SA_FAST_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible endpoint |
| `SA_FAST_MODEL` | `deepseek/deepseek-v4.1-flash` | |
| `SA_FAST_API_KEY` | | secret |
| `SA_FAST_REASONING_EFFORT` | | Sent as `reasoning_effort` (e.g. `low` for GPT-5-class models); `max_completion_tokens` is used automatically when a model requires it |
| `SA_FAST_USE_ENTRA` | `false` | Call a Foundry OpenAI-compatible endpoint with the managed identity instead of a key (set by Bicep when `SA_ENABLE_FOUNDRY=true`) |
| `SA_EXPERT_PROVIDER` | `anthropic` | `anthropic` \| `foundry` \| `openai_compat` \| `fake` |
| `SA_EXPERT_MODEL` | `claude-opus-5-5` | `anthropic/claude-opus-5.5` when using OpenRouter |
| `SA_EXPERT_API_KEY` | | secret (Claude API key; not needed with Foundry + managed identity) |
| `SA_EXPERT_BASE_URL` | | Optional override (e.g. a gateway) |
| `SA_FOUNDRY_RESOURCE` | | Microsoft Foundry resource name |
| `SA_FOUNDRY_USE_ENTRA` | `true` | Use the managed identity for Foundry |
| **ITSM** | | |
| `SA_ITSM_PROVIDER` | `mock` | `mock` \| `servicenow` \| `jira` |
| `SA_SERVICENOW_INSTANCE` / `_USER` / `_PASSWORD` | | |
| `SA_SERVICENOW_AGENT_USER_SYS_ID` | | Optional: tickets get *Assigned to* the integration user |
| `SA_JIRA_BASE_URL` / `_USER` / `_API_TOKEN` / `_PROJECT_KEY` / `_AGENT_ACCOUNT_ID` | | |
| **Knowledge** | | |
| `SA_SHAREPOINT_ENABLED` | `false` | |
| `SA_GRAPH_TENANT_ID` / `_CLIENT_ID` / `_CLIENT_SECRET` | | Leave the secret empty to use the managed identity |
| `SA_GRAPH_REGION` | `EMEA` | Required by Graph search for app-only calls (`NAM`, `EMEA`, `APC`, …) |
| `SA_SHAREPOINT_SITE_FILTER` | | KQL appended to every query |
| `SA_RAG_API_URL` / `_KEY` / `_KEY_HEADER` | | |
| `SA_AZURE_SEARCH_ENDPOINT` / `_INDEX` / `_KEY` | | KEDB + product docs (`scripts/ingest-docs.py`); the key is optional (managed identity) |
| `SA_ATLASSIAN_BASE_URL` / `_USER` / `_API_TOKEN` | | Atlassian Cloud site and a read-only user for Confluence/Jira knowledge (token is a secret) |
| `SA_CONFLUENCE_ENABLED` / `SA_CONFLUENCE_SPACES` | `false` / | Search Confluence pages; comma-separated space keys (empty = all readable) |
| `SA_JIRA_KB_ENABLED` / `SA_JIRA_KB_PROJECTS` | `false` / | Search past Jira issues; comma-separated project keys (empty = all readable) |
| **Sandbox** | | |
| `SA_SANDBOX_PROVIDER` | `mock` | `aca_sessions` \| `docker` \| `mock` \| `disabled` |
| `SA_ACA_POOL_ENDPOINT` / `SA_ACA_POOL_TYPE` | | `python` (built-in) or `custom` |
| `SA_DOCKER_IMAGE` | `superagent-sandbox:latest` | Local Docker sandbox (falls back to `python:3.12-slim`) |
| `SA_VM_SUBSCRIPTION_ID` / `_RESOURCE_GROUP` / `_SUBNET_ID` / `_LOCATION` / `_SIZE` | | Enables the `windows` target |
| `SA_VM_SETUP_SCRIPT` / `SA_VM_INSTALLER_BLOB_URL` | | Script run once on each new sandbox VM (e.g. installs your product); `{installer_url}` becomes a 2-hour read-only link to the blob |
| `SA_ACA_PRODUCT_POOL_ENDPOINT` | | Custom-container pool running the product under test (`product` target) |
| `SA_DOCKER_PRODUCT_IMAGE` | | Local alternative: Docker image built from `sandbox/product/` |
| `SA_PRODUCT_SANDBOX_NOTES` | demo text | What the model is told about the product target (URL, logs, `browse`) |
| **Tools** | | |
| `SA_MCP_TOKEN` | | secret; referenced from `mcp_servers[].headers` as `env:SA_MCP_TOKEN` |
| **Chat** | | |
| `SA_CHAT_PROVIDER` | `console` | `teams` \| `slack` \| `console` |
| `SA_TEAMS_WEBHOOK_URL` / `SA_SLACK_WEBHOOK_URL` | | secrets |
| **State** | | |
| `SA_STORE_PROVIDER` | `sqlite` | `cosmos` \| `sqlite` \| `memory` |
| `SA_COSMOS_ENDPOINT` / `SA_COSMOS_DATABASE` | | |
| `SA_QUEUE_PROVIDER` | `memory` | `servicebus` \| `memory` |
| `SA_SERVICEBUS_NAMESPACE` / `SA_SERVICEBUS_QUEUE` | | `<name>.servicebus.windows.net` |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | | Enables Azure Monitor |
| `AZURE_CLIENT_ID` | | Client id of the user-assigned managed identity |

To add an app setting in Azure that the Bicep does not expose (e.g. `SA_SHAREPOINT_SITE_FILTER`), add it to
`commonEnv` in `infra/modules/apps.bicep`. Or, for a quick test, run:

```bash
az containerapp update -g <rg> -n <app> --set-env-vars KEY=VALUE
```

## Policy file (`config/agent.yaml`)

The annotated default file is the reference. The most important knobs:

| Key | Default | Effect |
|-----|---------|--------|
| `routing.expert_enabled` | `true` | Use Claude Opus 5.5 before handing over |
| `routing.min_confidence` | `0.7` | Below this, the fast tier's result is escalated |
| `routing.expert_direct_complexity` | `5` | Start directly on the expert tier |
| `routing.p1_expert_first` | `true` | P1 / Major Incidents start on the expert tier |
| `routing.expert_effort` | `high` | Opus 5.5 effort level |
| `routing.per_ticket_budget_usd` / `daily_expert_budget_usd` | `3` / `5` | Hard cost guardrails (raise the daily cap after the pilot) |
| `resolution.auto_resolve` | `false` | Shadow mode when false |
| `resolution.auto_resolve_threshold` | `0.85` | Confidence needed to close without a human |
| `resolution.auto_resolve_max_priority` | `3` | P1/P2 always need approval |
| `resolution.auto_resolve_categories` | `[]` | Allow-list of categories (empty = all) |
| `itil.assignment_groups` | `[SuperAgent]` | Queues the agent works |
| `itil.handover_group` / `security_group` / `major_incident_group` | | Escalation targets |
| `itil.sla_minutes` | P1 4h … P4 3d | Resolution/response targets |
| `itil.problem_threshold` / `problem_window_hours` | `3` / `72` | Problem detection |
| `sandbox.max_runtime_seconds` / `max_output_chars` | `120` / `12000` | Per-command bounds |
| `sandbox.network_requires_approval` | `true` | Human approval for any sandbox network access |
| `sandbox.extra_deny_patterns` | `[]` | Your own regex deny rules |
| `mcp_servers` | `[]` | MCP servers whose `allowed_tools` the agent may call (see [connectors.md](connectors.md#mcp-tools)) |
| `prices` | | USD per 1M tokens for cost tracking |

## Prompts

Default prompts are in `src/superagent/agent/prompts.py`. To override them without touching code, create any of
the following in `config/prompts/`:
- `triage_system.md`
- `investigator_system.md` (supports the `{agent_name}` placeholder)
- `expert_addendum.md`

Typical additions are your organisation's support tone, tool names and escalation contacts.
