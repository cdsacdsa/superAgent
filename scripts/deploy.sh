#!/usr/bin/env bash
# Deploy without azd (az CLI only). Usage:
#   ENV_NAME=superagent-dev LOCATION=westeurope SA_FAST_API_KEY=... SA_EXPERT_API_KEY=... ./scripts/deploy.sh
set -euo pipefail
: "${ENV_NAME:?set ENV_NAME}" "${LOCATION:?set LOCATION}"
cd "$(dirname "$0")/.."

echo ">> Provisioning infrastructure (subscription scope)"
az deployment sub create --name "superagent-${ENV_NAME}" --location "$LOCATION" \
  --template-file infra/main.bicep \
  --parameters environmentName="$ENV_NAME" location="$LOCATION" \
    itsmProvider="${SA_ITSM_PROVIDER:-mock}" chatProvider="${SA_CHAT_PROVIDER:-console}" \
    expertProvider="${SA_EXPERT_PROVIDER:-anthropic}" foundryResource="${SA_FOUNDRY_RESOURCE:-}" \
    servicenowInstance="${SA_SERVICENOW_INSTANCE:-}" servicenowUser="${SA_SERVICENOW_USER:-}" \
    ragApiUrl="${SA_RAG_API_URL:-}" enableVmSandbox="${SA_ENABLE_VM_SANDBOX:-false}" \
    fastApiKey="${SA_FAST_API_KEY:-}" expertApiKey="${SA_EXPERT_API_KEY:-}" \
    servicenowPassword="${SA_SERVICENOW_PASSWORD:-}" teamsWebhookUrl="${SA_TEAMS_WEBHOOK_URL:-}" \
    ragApiKey="${SA_RAG_API_KEY:-}" webhookSecret="${SA_WEBHOOK_SECRET:-}" consoleApiKey="${SA_CONSOLE_API_KEY:-}" \
  --query properties.outputs -o json > .deploy-outputs.json

RG=$(python3 -c "import json;print(json.load(open('.deploy-outputs.json'))['AZURE_RESOURCE_GROUP']['value'])")
ACR=$(python3 -c "import json;print(json.load(open('.deploy-outputs.json'))['AZURE_CONTAINER_REGISTRY_NAME']['value'])")
TAG=$(date +%Y%m%d%H%M%S)

echo ">> Building image in ACR ($ACR)"
az acr build -r "$ACR" -t "superagent:$TAG" .

IMAGE="$ACR.azurecr.io/superagent:$TAG"
PREFIX=$(echo "$ENV_NAME" | tr '[:upper:]_' '[:lower:]-' | cut -c1-12)
echo ">> Updating container apps"
az containerapp update -g "$RG" -n "ca-${PREFIX}-api" --image "$IMAGE" -o none
az containerapp update -g "$RG" -n "ca-${PREFIX}-worker" --image "$IMAGE" -o none

python3 -c "import json;o=json.load(open('.deploy-outputs.json'));print('Console:', o['SERVICE_API_ENDPOINT']['value'])"
