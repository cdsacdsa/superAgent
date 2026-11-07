# Operations runbook

## Daily
- Read the **daily digest** in Teams: volume, auto-resolution rate, handovers and cost.
- Clear the **waiting for a human** column on the console board (help requests and approvals).
- Review handovers. Each handover note shows where the agent's knowledge or tools fell short.
  The fix is usually a KB article, a better runbook, or a new connector.

## Weekly
- Sample 10 resolved cases and check correctness, tone and citations. Look at the reopen rate in the ITSM tool.
- Tune `config/agent.yaml` (thresholds, categories). Add prompt overrides for recurring mistakes.
- Check the cost trend in `/api/metrics` and on the OpenRouter / Anthropic / Azure cost dashboards.

## Useful queries (Log Analytics)

```kusto
// Errors in the worker
ContainerAppConsoleLogs_CL
| where ContainerAppName_s endswith "-worker" and Log_s has "ERROR"
| project TimeGenerated, Log_s | order by TimeGenerated desc

// Routing decisions
ContainerAppConsoleLogs_CL | where Log_s has "routing" | project TimeGenerated, Log_s
```

Per-case detail is easier to read on the console timeline (`/cases/<id>`) or via `GET /api/cases/<id>/events`.

## Troubleshooting

| Symptom | Check | Fix |
|---------|-------|-----|
| No tickets picked up | Worker logs `poller:`; the ITSM queue query | Assignment group name in `itil.assignment_groups`; integration user permissions; `SA_POLL_ENABLED` |
| 401/403 from ServiceNow | Integration user roles | Grant `itil` or the custom role; check ACLs on `sys_journal_field` |
| Every case is handed over with "API error 401" | Model keys | `azd env set SA_FAST_API_KEY …` then `azd provision` (updates Key Vault), then restart the revision |
| Expert tier never used | `/api/metrics` → `expert_spend_today_usd`; routing events | Budget reached? `expert_enabled`? Key valid? |
| Sandbox errors `403` | Role *Azure ContainerApps Session Executor* on the pool | Re-run `azd provision` |
| Sandbox network command blocked | Expected: approval required | Approve it in the console |
| Cases stuck in `investigating` | Lease expired? Worker crashed? | Leases expire after 15 min; send `POST /api/tickets/<id>/process` or restart the worker |
| Messages in the Service Bus dead-letter queue | Exception in the worker logs | Fix the cause, then re-submit with Service Bus Explorer |
| Teams cards not arriving | Workflow run history in Teams | The workflow URL may have been regenerated: update `SA_TEAMS_WEBHOOK_URL` |
| Console 401 | Easy Auth or API key | Use `/?key=<SA_CONSOLE_API_KEY>`, or check the Entra app's redirect URI |

## Restart / redeploy
To redeploy the code (and the policy file):

```bash
azd deploy
```

To restart the worker, run these two commands. The first prints the active revision name; pass it to the second:

```bash
az containerapp revision list -g <rg> -n ca-<prefix>-worker --query "[0].name" -o tsv
```

```bash
az containerapp revision restart -g <rg> -n ca-<prefix>-worker --revision <revision-name>
```

## Pausing the agent (kill switch)
To stop all processing immediately, deactivate the worker's active revision:

```bash
az containerapp revision deactivate -g <rg> -n ca-<prefix>-worker --revision <revision-name>
```

To resume, run `az containerapp revision activate` with the same arguments.
In demo mode the worker runs inside the API, so deactivate the API revision instead.
Tickets stay in the ITSM queue, and human engineers can pick them up as usual.

## Backup / DR
- Cosmos DB serverless includes continuous backup (periodic by default). The ITSM tool remains the system of record.
- To rebuild everything:

```bash
azd up
```

Cases are recreated idempotently from the ITSM queue.
