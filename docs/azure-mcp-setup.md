# Connecting the Azure MCP server (AI-assisted deployment and operations)

The repository ships a project-level `.mcp.json` that registers the official **Azure MCP Server**
(`@azure/mcp`, stdio via `npx`). With it, Claude Code (locally or in a cloud session) can do the following in your subscription:
- list and inspect the deployed resources
- read Container Apps, Cosmos DB, Service Bus, Key Vault and Monitor data
- validate Bicep and help with deployments

## 1. Create a service principal for the session

Run this in Azure Cloud Shell or anywhere you are logged in with `az`. Prefer a dedicated subscription or resource group.

```bash
az account set --subscription <SUBSCRIPTION_ID>
```

```bash
az ad sp create-for-rbac --name superagent-claude-deployer --role Contributor --scopes /subscriptions/<SUBSCRIPTION_ID> --years 1
```

The Bicep creates role assignments for the managed identity. Contributor cannot do that, so add:

```bash
az role assignment create --assignee <appId> --role "Role Based Access Control Administrator" --scope /subscriptions/<SUBSCRIPTION_ID>
```

## 2. Provide the credentials as environment variables

Set these where Claude Code runs:
- **Cloud session:** in the environment's settings
- **Local:** in your shell

Variables:
- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID` (the `appId`)
- `AZURE_CLIENT_SECRET` (the `password`)
- `AZURE_SUBSCRIPTION_ID`

Never paste the secret into a chat. These variables are stored in plain text on the environment, so use a
short-lived secret and rotate it after testing.

## 3. Start a new session

MCP servers load when a session starts. In the new session, the `azure` server's tools appear. Typical asks:
- "List the resources in rg-superagent-dev"
- "Show the latest worker logs"
- "Check the Service Bus dead-letter count"

To deploy from that session with the same identity:

```bash
azd auth login --client-id "$AZURE_CLIENT_ID" --client-secret "$AZURE_CLIENT_SECRET" --tenant-id "$AZURE_TENANT_ID"
```

```bash
azd up
```

## Network
If your environment uses a restricted allowlist, allow these hosts:
- `login.microsoftonline.com`
- `management.azure.com`
- `graph.microsoft.com`
- `mcr.microsoft.com`
- the app's data-plane hosts: `*.servicebus.windows.net`, `*.documents.azure.com`, `*.vault.azure.net` and `*.azurecontainerapps.io`

Wildcard entries may not be accepted. In that case, list each exact host.
