# Connectors

## ITSM

### ServiceNow (`itsm/servicenow.py`)
- **API:** Table API (`/api/now/table/...`) with basic auth for the integration user.
- **Queue:** `assignment_group.nameIN<groups>^state=1^ORstate=2^assigned_toISEMPTY`
- **State mapping:**

  | Agent state | ServiceNow incident state |
  |-------------|---------------------------|
  | new | 1 New |
  | in progress | 2 In Progress |
  | on hold, awaiting caller | 3 On Hold + `hold_reason` 1 |
  | on hold, other | 3 On Hold + `hold_reason` 4 |
  | resolved | 6 Resolved, with `close_code` + `close_notes` |
  | closed | 7 |
  | cancelled | 8 |
- **Notes:** `work_notes` are internal; `comments` are customer-visible.
- **Knowledge:** `kb_knowledge` and resolved incidents, both searched with `123TEXTQUERY321` full-text queries.
- **Escalation:** reassign by looking up the group's sys_id in `sys_user_group`.
- **Problems:** created in the `problem` table.
- **Close codes:** instances customise them. Set `close_code` values that exist on your instance through the
  prompt (or adapt `Outcome.close_code`).
- **Other tables:** the connector defaults to `incident`. For catalog tasks or other tables, create a second
  connector instance with `table="sc_task"`.

### Jira Service Management (`itsm/jira_sm.py`)
- **API:** REST v3 (`/rest/api/3/search/jql`, transitions) and the Service Desk API for public and internal comments.
- **Queue:** the JSM project, status category "To Do", unassigned, with the label `superagent`.
- **Transitions:** matched by name. Configure them in `DEFAULT_TRANSITIONS` if your workflow uses different names.
- **Handover:** adds a label for the target group (e.g. `l3-support`) and unassigns the issue.

### Mock (`itsm/mock.py`)
- In-memory, seeded with sample tickets and KB articles. Used for the demo, tests and the Azure demo mode.

## Knowledge

### SharePoint (`knowledge/sharepoint.py`)
- **API:** Microsoft Graph `POST /v1.0/search/query` over `driveItem` and `listItem`, with `region` set (required for app-only calls).
- **Content:** returns hit summaries. For text-like files (`.txt`, `.md`, `.ps1`, …) it also downloads the content (first 6 kB).
- **Auth:** the managed identity by default. Grant permissions with `scripts/grant-graph-permissions.sh`. An app registration with a client secret is also supported.
- **Least privilege:** use `Sites.Selected` instead of `Sites.Read.All` and grant the identity read access to
  specific sites (`POST /sites/{site-id}/permissions`).

### RAG API (`knowledge/rag_api.py`) <a id="rag-api"></a>
Default contract:
```http
POST {SA_RAG_API_URL}
Authorization: Bearer {SA_RAG_API_KEY}
{"query": "vpn drops every 10 minutes", "top_k": 5}

200 {"results": [{"title": "...", "content": "...", "url": "...", "score": 0.82}]}
```
If your service differs, construct `RagApiSource` in `container.py` with:
- `request_template`, e.g. `'{"search": "{query}", "count": {top_k}}'`
- `results_path`, e.g. `"data.hits"`
- `field_map`, e.g. `{"content": "chunk.text", "url": "metadata.source"}`
- `key_header`, e.g. `"x-api-key"`

### Azure AI Search (`knowledge/azure_search.py`)
- Keyword search over the `superagent-kedb` index. Resolved cases are written back (`upsert`) as known errors.
- Create the index with `scripts/create-search-index.py`. For semantic or vector search, extend the request body.
- Load your product docs, runbooks and known errors with `scripts/ingest-docs.py <endpoint> <folder>`: Markdown,
  text and HTML, split at headings, one search document per section. Re-run it after edits (upserts by path).
  `samples/knowledge/` holds the demo product's docs.

### Confluence (`knowledge/atlassian.py`)
- CQL `type = page AND text ~ "..."` through `GET /wiki/rest/api/content/search`, with the page body rendered to text.
- Basic auth with an API token of a user that can only read the spaces the agent should see
  (`SA_ATLASSIAN_*`, optionally `SA_CONFLUENCE_SPACES`). Query operators from the model are stripped.

### Jira issues as knowledge (`knowledge/atlassian.py`)
- Past bugs and support tickets: JQL `text ~ "..."` through `POST /rest/api/3/search/jql`, returning summary,
  description, status, resolution and the last three comments. Scope it with `SA_JIRA_KB_PROJECTS`.
- Independent of the ITSM connector: the tickets can live in ServiceNow while bug history lives in Jira.

### ITSM knowledge (`knowledge/itsm_kb.py`)
- Always on. It combines KB articles with resolved similar tickets from the connected ITSM tool.

The `KnowledgeHub` queries every source in parallel with a 30 s timeout, interleaves the results so that no
single source dominates, and removes duplicates. A failing source is logged and skipped.

## Chat

| Channel | Setup | Format |
|---------|-------|--------|
| Teams (`chat/teams.py`) | Workflows → *Post to a channel when a webhook request is received* (or *Post to a chat when a webhook request is received* for a group chat) | Adaptive Card 1.4 with FactSet and "Answer / Review & approve / Open ticket" buttons |
| Slack (`chat/slack.py`) | Incoming webhook | Block Kit |
| Console (`chat/base.py`) | — | Logs only |

Two-way interaction works through the console links in the cards. Entra ID secures the console, and every answer
records who gave it. A Teams bot (Bot Framework) can be added later on top of the same `/api/cases/{id}/help/{help_id}` endpoint.

## Sandbox

| Provider | Isolation | Use |
|----------|-----------|-----|
| `aca_sessions` (`python` pool) | Hyper-V container per case, egress disabled, Entra-authenticated management endpoint | Default in Azure; no image to build |
| `aca_sessions` (`custom` pool) | Same, with your own image from `sandbox/` (dig, openssl, ldap-utils, smbclient, …) | Richer tooling |
| `aca_sessions` (`product` target) | Same isolation; image from `sandbox/product/`: your product + a headless browser (`browse`) + the exec API | Open the product and poke around (demo: Contoso Orders) |
| `azure_vm` | Ephemeral VM in an isolated RG/VNet, NSG denies Internet egress, no public IP, Run Command only | Windows / full-OS repros; desktop products via `SA_VM_SETUP_SCRIPT` |
| `docker` | `--network none --read-only --cap-drop ALL`, non-root, memory/pid limits | Local development |
| `mock` | — | Tests and demo |

To create a custom container pool:

```bash
az acr build -r <acr> -t superagent-sandbox:1 sandbox/
```

```bash
az containerapp sessionpool create -g <rg> -n sp-custom --environment <cae> --container-type CustomContainer --image <acr>.azurecr.io/superagent-sandbox:1 --target-port 8080 --network-status EgressDisabled --registry-server <acr>.azurecr.io --registry-identity <identity-resource-id> --cooldown-period 900 --max-sessions 20
```

Then set `SA_ACA_POOL_TYPE=custom` and `SA_ACA_POOL_ENDPOINT` to the new pool's management endpoint.

The **product** pool is created by the Bicep when `SA_PRODUCT_SANDBOX_IMAGE` is set (`scripts/build-product-image.*`).
Custom-container pools run on dedicated E16 nodes billed while any session is *ready or active*, so the
Bicep keeps zero ready sessions and the minimum 300 s cooldown: you pay only while a case uses the product.

## MCP tools <a id="mcp-tools"></a>

`mcp_tools.py` connects to MCP servers (streamable HTTP) listed under `mcp_servers` in `config/agent.yaml`:
a product's own API, GitHub, Atlassian or internal tools. Only the tools in `allowed_tools` are offered to the
model (list read-only tools); they appear as `mcp_<server>__<tool>`, go through the same audit log, redaction and
truncation as built-in tools, and their output is wrapped as untrusted. A server that is down is skipped and
retried on the next investigation. Secrets go in headers as `env:NAME` (in Azure: Key Vault `mcp-token`).

## Adding a connector

1. Subclass the interface: `ITSMConnector`, `KnowledgeSource`, `ChatChannel`, `Sandbox`.
2. Add settings to `config.py` and wire them in `container.py`.
3. Add a mocked-HTTP test like those in `tests/test_connectors.py`.

Example: `knowledge/atlassian.py` (Confluence and Jira) is a good template for a REST knowledge source.
