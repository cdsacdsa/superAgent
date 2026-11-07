#!/usr/bin/env bash
# Grant Microsoft Graph application permissions (SharePoint search) to the agent's managed identity.
# Requires: Global Administrator / Privileged Role Administrator (admin consent).
# Usage: ./scripts/grant-graph-permissions.sh <managed-identity-principal-id>
set -euo pipefail
MI="${1:?principal id of the managed identity (azd output AZURE_MANAGED_IDENTITY_PRINCIPAL_ID)}"
GRAPH_APP_ID=00000003-0000-0000-c000-000000000000
GRAPH_SP=$(az ad sp show --id "$GRAPH_APP_ID" --query id -o tsv)
for ROLE in Sites.Read.All Files.Read.All; do
  ROLE_ID=$(az ad sp show --id "$GRAPH_APP_ID" --query "appRoles[?value=='$ROLE' && contains(allowedMemberTypes,'Application')].id" -o tsv)
  echo "Granting $ROLE ($ROLE_ID)"
  az rest --method POST --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$MI/appRoleAssignments" \
    --headers "Content-Type=application/json" \
    --body "{\"principalId\":\"$MI\",\"resourceId\":\"$GRAPH_SP\",\"appRoleId\":\"$ROLE_ID\"}" -o none || echo "  (already granted?)"
done
echo "Done. Tip: to limit access to specific sites use Sites.Selected instead (see docs/connectors.md)."
