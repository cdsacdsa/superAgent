# Build the product sandbox image (sandbox/product/) in Azure Container Registry and point the
# deployment at it. Run after the first `azd provision` (the registry must exist), then `azd provision`
# again to create or update the "product" session pool.
#   ./scripts/build-product-image.ps1 [-Tag 2] [-Local]
# -Local builds with your local Docker and pushes, for subscriptions where ACR Tasks are not allowed.
param([string]$Tag = (Get-Date -Format "yyyyMMddHHmmss"), [switch]$Local)
$ErrorActionPreference = "Stop"
$acr = azd env get-value AZURE_CONTAINER_REGISTRY_NAME
$server = azd env get-value AZURE_CONTAINER_REGISTRY_ENDPOINT
$image = "$server/superagent-product:$Tag"
if ($Local) {
    az acr login -n $acr
    docker build -t $image -f sandbox/product/Dockerfile sandbox/
    docker push $image
} else {
    az acr build -r $acr -t "superagent-product:$Tag" -f sandbox/product/Dockerfile sandbox/
}
azd env set SA_PRODUCT_SANDBOX_IMAGE $image
Write-Output "Product sandbox image: $image. Now run: azd provision"
