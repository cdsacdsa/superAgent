// Shared platform: identity, monitoring, registry, Key Vault, Cosmos DB, Service Bus, (optional) AI Search
param location string
param tags object
param prefix string
param token string
param enableSearch bool
@secure()
param secrets object

var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  kvSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  serviceBusDataOwner: '090c5cfd-751d-490a-894a-3ce6f1109419'
  searchIndexDataContributor: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${prefix}-${token}'
  location: location
  tags: tags
}

// ---------------- monitoring
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${prefix}-${token}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${prefix}-${token}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

// ---------------- container registry
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${replace(prefix, '-', '')}${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, roles.acrPull)
  scope: acr
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

// ---------------- key vault (RBAC) + secrets
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-${take(prefix, 8)}-${token}'
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

// Empty values get a placeholder so Container Apps can always resolve the reference.
resource kvSecrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [for s in items(secrets): {
  parent: kv
  name: s.key
  properties: { value: empty(s.value) ? 'not-set' : s.value }
}]

resource kvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, identity.id, roles.kvSecretsUser)
  scope: kv
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.kvSecretsUser)
  }
}

// ---------------- cosmos db (serverless, Entra-only auth)
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: 'cosmos-${prefix}-${token}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [{ locationName: location, failoverPriority: 0 }]
    capabilities: [{ name: 'EnableServerless' }]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    disableLocalAuth: true
  }
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: 'superagent'
  properties: { resource: { id: 'superagent' } }
}

resource casesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDb
  name: 'cases'
  properties: { resource: { id: 'cases', partitionKey: { paths: ['/id'], kind: 'Hash' } } }
}

resource eventsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDb
  name: 'events'
  properties: {
    resource: {
      id: 'events'
      partitionKey: { paths: ['/case_id'], kind: 'Hash' }
      defaultTtl: 31536000 // keep audit events for 1 year
    }
  }
}

resource cosmosRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmos
  name: guid(cosmos.id, identity.id, 'data-contributor')
  properties: {
    principalId: identity.properties.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    scope: cosmos.id
  }
}

// ---------------- service bus
resource sb 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: 'sb-${prefix}-${token}'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard' }
  properties: { disableLocalAuth: true }
}

resource queue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: sb
  name: 'tickets'
  properties: {
    lockDuration: 'PT5M'
    maxDeliveryCount: 5
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P7D'
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'PT10M'
  }
}

resource sbRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sb.id, identity.id, roles.serviceBusDataOwner)
  scope: sb
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.serviceBusDataOwner)
  }
}

// ---------------- optional AI Search (KEDB / resolution memory)
resource search 'Microsoft.Search/searchServices@2023-11-01' = if (enableSearch) {
  name: 'srch-${prefix}-${token}'
  location: location
  tags: tags
  sku: { name: 'basic' }
  properties: {
    authOptions: { aadOrApiKey: { aadAuthFailureMode: 'http401WithBearerChallenge' } }
    semanticSearch: 'free'
  }
}

resource searchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableSearch) {
  name: guid(resourceGroup().id, identity.id, roles.searchIndexDataContributor)
  scope: search
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexDataContributor)
  }
}

output identityId string = identity.id
output identityClientId string = identity.properties.clientId
output identityPrincipalId string = identity.properties.principalId
output registryLoginServer string = acr.properties.loginServer
output registryName string = acr.name
output keyVaultUri string = kv.properties.vaultUri
output keyVaultName string = kv.name
output logAnalyticsWorkspaceId string = logs.id
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output serviceBusNamespace string = '${sb.name}.servicebus.windows.net'
output serviceBusQueue string = queue.name
output searchEndpoint string = enableSearch ? 'https://${search.name}.search.windows.net' : ''
