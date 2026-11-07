// Microsoft Foundry: one AI Services resource with the fast-tier and expert-tier model deployments.
// Both tiers call it with the managed identity; API keys (local auth) are disabled.
param location string
param tags object
param prefix string
param token string
param principalId string
@description('Optional: the deploying user, so they can call the models from their own machine.')
param userPrincipalId string = ''

@description('Fast tier model: { name, format, version, sku, capacity }. version may be empty (= default).')
param fastModel object
@description('Expert tier model: { name, format, version, sku, capacity }.')
param expertModel object

var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var name = 'ais-${prefix}-${token}'

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource fast 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: fastModel.name
  sku: { name: fastModel.sku, capacity: fastModel.capacity }
  properties: {
    model: union({ format: fastModel.format, name: fastModel.name }, empty(fastModel.version) ? {} : { version: fastModel.version })
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

// Deployments on one account are created one at a time.
resource expert 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: expertModel.name
  dependsOn: [fast]
  sku: { name: expertModel.sku, capacity: expertModel.capacity }
  properties: {
    model: union({ format: expertModel.format, name: expertModel.name }, empty(expertModel.version) ? {} : { version: expertModel.version })
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource miRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, principalId, cognitiveServicesUser)
  scope: foundry
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUser)
  }
}

resource userRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(userPrincipalId)) {
  name: guid(foundry.id, userPrincipalId, cognitiveServicesUser)
  scope: foundry
  properties: {
    principalId: userPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUser)
  }
}

output name string = foundry.name
output openaiBaseUrl string = 'https://${name}.services.ai.azure.com/openai/v1/'
output fastDeployment string = fast.name
output expertDeployment string = expert.name
