#!/bin/sh
# azd postprovision hook: prints the next steps after infrastructure is created.
set -e
echo ""
echo "==================== SuperAgent provisioned ===================="
echo "Console:        ${SERVICE_API_ENDPOINT}"
echo "Resource group: ${AZURE_RESOURCE_GROUP}"
echo "Key Vault:      ${AZURE_KEY_VAULT_NAME}"
echo "Session pool:   ${SESSION_POOL_ENDPOINT}"
echo ""
echo "Next steps:"
echo " 1. Console auth: set SA_ENTRA_CLIENT_ID/SA_ENTRA_CLIENT_SECRET (see docs/deployment-guide.md)"
echo "    or use the console API key: ${SERVICE_API_ENDPOINT}/?key=<SA_CONSOLE_API_KEY>"
echo " 2. SharePoint: ./scripts/grant-graph-permissions.sh ${AZURE_MANAGED_IDENTITY_PRINCIPAL_ID}"
echo " 3. Smoke test: ./scripts/smoke-test.sh ${SERVICE_API_ENDPOINT}"
echo "================================================================"
