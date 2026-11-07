#!/usr/bin/env bash
# Build the product sandbox image (sandbox/product/) in Azure Container Registry and point the
# deployment at it. Run after the first `azd provision`, then `azd provision` again.
#   ./scripts/build-product-image.sh [tag] [--local]
# --local builds with your local Docker and pushes, for subscriptions where ACR Tasks are not allowed.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${1:-$(date +%Y%m%d%H%M%S)}"
ACR=$(azd env get-value AZURE_CONTAINER_REGISTRY_NAME)
SERVER=$(azd env get-value AZURE_CONTAINER_REGISTRY_ENDPOINT)
IMAGE="$SERVER/superagent-product:$TAG"
if [ "${2:-}" = "--local" ]; then
  az acr login -n "$ACR"
  docker build -t "$IMAGE" -f sandbox/product/Dockerfile sandbox/
  docker push "$IMAGE"
else
  az acr build -r "$ACR" -t "superagent-product:$TAG" -f sandbox/product/Dockerfile sandbox/
fi
azd env set SA_PRODUCT_SANDBOX_IMAGE "$IMAGE"
echo "Product sandbox image: $IMAGE. Now run: azd provision"
