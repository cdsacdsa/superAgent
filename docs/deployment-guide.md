# Deployment guide (Azure)

The deployment has three stages. Each one ends with something working, so stop wherever you need to.

1. **Demo deployment** (~20 min): everything on Azure with the mock ITSM. Proves the platform, models and sandbox.
2. **Connect your ITSM, chat and knowledge** (~1–2 h, mostly waiting for permissions).
3. **Harden for production**: Entra ID console sign-in, Foundry models, VM sandbox, alerts.

---

## 0. Prerequisites

| What | Why |
|------|-----|
| Azure subscription, with **Owner** (or Contributor + *Role Based Access Control Administrator*) on it | The Bicep creates role assignments for the managed identity |
| [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) `azd` ≥ 1.10 and Azure CLI `az` | Deployment |
| An OpenRouter API key (or DeepSeek / Azure AI Foundry) | Fast tier |
| An Anthropic API key, **or** a Microsoft Foundry resource with Claude Opus 5.5 deployed | Expert tier |
| Resource providers registered: `Microsoft.App`, `Microsoft.DocumentDB`, `Microsoft.ServiceBus`, `Microsoft.KeyVault`, `Microsoft.ContainerRegistry`, `Microsoft.OperationalInsights` | Run `az provider register -n <ns>` once |

Docker is **not** required: images are built in Azure Container Registry (`remoteBuild: true`).

Pick a region where Container Apps **dynamic sessions** are available (e.g. `westeurope`, `northeurope`,
`eastus`, `swedencentral`).

---

## 1. Demo deployment

```bash
git clone https://github.com/cdsacdsa/superAgent && cd superAgent
```

```bash
azd auth login
```

```bash
azd env new superagent-dev
```

```bash
azd env set AZURE_LOCATION westeurope
```

```bash
azd env set SA_FAST_API_KEY "<openrouter key>"
```

```bash
azd env set SA_EXPERT_API_KEY "<anthropic key>"
```

```bash
azd env set SA_CONSOLE_API_KEY "$(openssl rand -hex 24)"
```

```bash
azd up
```

`azd up` provisions `infra/main.bicep`, builds the image in ACR and deploys the `api` and `worker` container apps.
At the end it prints the console URL (`SERVICE_API_ENDPOINT`).

**Verify:**

```bash
./scripts/smoke-test.sh "$(azd env get-value SERVICE_API_ENDPOINT)" "$(azd env get-value SA_CONSOLE_API_KEY)"
```

Open `https://<api-url>/?key=<console key>` to see the board, the case timeline and the approval forms.
In demo mode the board has a "Create a test ticket" form.

What gets created (resource group `rg-<env>`):

| Resource | SKU | Monthly cost (idle → light use) |
|----------|-----|-------------------------------|
| Container Apps env + 2 apps | Consumption | ~$0–30 |
| Session pool (sandbox) | Consumption, pay per session | ~$0–10 |
| Cosmos DB | Serverless | ~$1–5 |
| Service Bus | Standard | ~$10 |
| Key Vault, ACR Basic, Log Analytics, App Insights | — | ~$5–15 |

---

## 2. Connect your systems

Set the values with `azd env set` and run `azd provision` (or `azd up`) again. Secrets go to Key Vault automatically.

### 2.1 ServiceNow

1. Create an integration user `svc_superagent` with the `itil` role. Least privilege: a custom role with
   read/write on `incident`, read on `kb_knowledge` / `sys_user_group` / `sys_journal_field`, and create on `problem`.
2. Create the assignment group **SuperAgent** (and make sure **L3 Support** and **Security Operations** exist).
   Route the ticket categories you want the agent to handle to this group, through assignment rules or the service desk.
3. Optional (instant pickup instead of polling every 60 s): a Business Rule on `incident` (after insert/update,
   condition *Assignment group is SuperAgent*) that sends an outbound REST message:
   `POST https://<api-url>/webhooks/servicenow` with header `X-SuperAgent-Secret: <SA_WEBHOOK_SECRET>`
   and body `{"number": "${number}"}`.

```bash
azd env set SA_ITSM_PROVIDER servicenow
```

```bash
azd env set SA_SERVICENOW_INSTANCE contoso.service-now.com
```

```bash
azd env set SA_SERVICENOW_USER svc_superagent
```

```bash
azd env set SA_SERVICENOW_PASSWORD "<password>"
```

```bash
azd env set SA_WEBHOOK_SECRET "$(openssl rand -hex 24)"
```

```bash
azd up
```

Switching from `mock` to a real ITSM also switches the worker to **production mode**: a separate worker
app, the Service Bus queue and KEDA scaling.

> Jira Service Management works the same way with `SA_ITSM_PROVIDER=jira` plus `SA_JIRA_BASE_URL`,
> `SA_JIRA_USER`, `SA_JIRA_API_TOKEN` and `SA_JIRA_PROJECT_KEY`. Tickets carrying the label `superagent` are picked up.

### 2.2 Microsoft Teams updates

In the target channel open **⋯ → Workflows → "Post to a channel when a webhook request is received"**. Name it
*SuperAgent* and copy the URL.

```bash
azd env set SA_CHAT_PROVIDER teams
```

```bash
azd env set SA_TEAMS_WEBHOOK_URL "<workflow url>"
```

```bash
azd up
```

### 2.3 SharePoint knowledge

The worker's managed identity calls Microsoft Graph search. Grant it application permissions (this needs a
Global Administrator or Privileged Role Administrator):

```bash
./scripts/grant-graph-permissions.sh "$(azd env get-value AZURE_MANAGED_IDENTITY_PRINCIPAL_ID)"
```

```bash
azd env set SA_SHAREPOINT_ENABLED true
```

```bash
azd up
```

To restrict search to IT sites, set `SA_SHAREPOINT_SITE_FILTER` to a KQL filter such as
`path:"https://contoso.sharepoint.com/sites/ITKnowledge"`. This is applied as an app setting; see
[configuration.md](configuration.md).

### 2.4 Your RAG API

```bash
azd env set SA_RAG_API_URL https://rag.contoso.com/api/search
```

```bash
azd env set SA_RAG_API_KEY "<key>"
```

```bash
azd up
```

The expected contract and how to adapt it are in [connectors.md](connectors.md#rag-api).

### 2.5 Known Error DB (Azure AI Search, optional)

```bash
azd env set SA_ENABLE_SEARCH true
```

```bash
azd up
```

```bash
python scripts/create-search-index.py "https://srch-<prefix>-<token>.search.windows.net"
```

---

## 3. Production hardening

### 3.1 Entra ID sign-in for the console
1. Create an app registration named *SuperAgent Console*:
   - Redirect URI (Web): `https://<api-url>/.auth/login/aad/callback`
   - Enable ID tokens
   - Create a client secret
2. Optionally, restrict access to an *L2/L3 engineers* group: Enterprise app → Properties → *Assignment required*, then assign the group.
3. Set the registration values and redeploy:

```bash
azd env set SA_ENTRA_CLIENT_ID "<app id>"
```

```bash
azd env set SA_ENTRA_CLIENT_SECRET "<secret>"
```

```bash
azd up
```

The console now requires sign-in. Webhooks and health probes are excluded and use the shared secret.
The signed-in user's UPN is recorded on every approval and answer.

### 3.2 Keep models inside Azure (Microsoft Foundry)
1. In a Microsoft Foundry resource, deploy **Claude Opus 5.5**. For the fast tier you can also deploy a DeepSeek model.
2. Grant the managed identity **Cognitive Services User** on the Foundry resource:

```bash
az role assignment create --assignee "$(azd env get-value AZURE_MANAGED_IDENTITY_PRINCIPAL_ID)" --role "Cognitive Services User" --scope "<foundry resource id>"
```

3. Point the expert tier at Foundry and redeploy:

```bash
azd env set SA_EXPERT_PROVIDER foundry
```

```bash
azd env set SA_FOUNDRY_RESOURCE "<foundry resource name>"
```

```bash
azd up
```

No API key is needed. The SDK authenticates with the managed identity.

### 3.3 Windows / full-OS sandbox VMs

```bash
azd env set SA_ENABLE_VM_SANDBOX true
```

```bash
azd up
```

This creates `rg-<env>-sandbox` with a VNet/NSG that denies Internet egress and lateral traffic. The managed
identity gets VM and Network Contributor on **that resource group only**. The agent then offers a `windows`
sandbox target, creating a VM per case on demand, running commands through Run Command, and deleting it on
close or after `vm_ttl_minutes`.

### 3.4 Go live gradually
1. **Shadow:** keep `resolution.auto_resolve: false` for 2–4 weeks. Every proposal is reviewed. Track proposal accuracy.
2. **Assist:** set `auto_resolve: true` with `auto_resolve_categories: [email, endpoint, access]` and `auto_resolve_max_priority: 3`.
3. Widen the categories as the KPIs allow (see [business-case.md](business-case.md)).

To change `config/agent.yaml`, edit it and run `azd deploy` (the policy is baked into the image).

### 3.5 Alerts (recommended)
In Application Insights, create alerts for:
- exceptions from `superagent.worker`
- the Service Bus dead-letter count > 0
- worker replicas = 0 (production mode)
- `/api/metrics` `sla_breached_open > 0`, as an availability test with a content match

---

## Deploying without azd

```bash
az login
```

```bash
ENV_NAME=superagent-dev LOCATION=westeurope SA_FAST_API_KEY=... SA_EXPERT_API_KEY=... ./scripts/deploy.sh
```

## Tearing down

```bash
azd down --purge
```

This deletes both resource groups and purges Key Vault.
