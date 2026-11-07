// Running SuperAgent without a local machine:
// * a deploy-only identity for GitHub Actions (OIDC federated credential, no secrets): it can push
//   images and roll out new revisions of the two apps, nothing else; infra changes stay with a human
// * a storage container for azd remote environment state, so Azure Cloud Shell sees the same azd env
param location string
param tags object
param prefix string
param token string
@description('owner/repo whose workflow may deploy, e.g. "contoso/superAgent". Empty = no deploy identity.')
param githubRepo string = ''
param githubBranch string = 'main'
@description('Repo part of the OIDC subject. GitHub may send the ID form "owner@<id>/repo@<id>" (the login error shows it); empty = githubRepo.')
param githubSubjectRepo string = ''
param registryName string
param apiName string
param workerName string
@description('Users who may read/write the azd environment state (e.g. the admin running azd in Cloud Shell).')
param stateUserPrincipalId string = ''

var roles = {
  acrPush: '8311e382-0749-4cb8-b61a-304f252e45ec'
  reader: 'acdd72a7-3385-48ef-bd42-f606fba81ae7'
  containerAppsContributor: '358470bc-b998-42bd-ab17-a7e34c199c0f'
  blobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
}
var deployOn = !empty(githubRepo)

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

resource api 'Microsoft.App/containerApps@2025-01-01' existing = {
  name: apiName
}

resource worker 'Microsoft.App/containerApps@2025-01-01' existing = {
  name: workerName
}

resource deployer 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = if (deployOn) {
  name: 'id-${prefix}-github'
  location: location
  tags: tags
}

resource federated 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = if (deployOn) {
  parent: deployer
  name: 'github-${replace(githubBranch, '/', '-')}'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${empty(githubSubjectRepo) ? githubRepo : githubSubjectRepo}:ref:refs/heads/${githubBranch}'
    audiences: ['api://AzureADTokenExchange']
  }
}

// AcrPush to push images, Reader so `az acr login` can look the registry up
resource registryRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in [roles.acrPush, roles.reader]: if (deployOn) {
  name: guid(registry.id, 'github-deployer', r)
  scope: registry
  properties: {
    principalId: deployer!.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r)
  }
}]

resource apiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployOn) {
  name: guid(api.id, 'github-deployer', roles.containerAppsContributor)
  scope: api
  properties: {
    principalId: deployer!.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.containerAppsContributor)
  }
}

resource workerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployOn) {
  name: guid(worker.id, 'github-deployer', roles.containerAppsContributor)
  scope: worker
  properties: {
    principalId: deployer!.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.containerAppsContributor)
  }
}

// ---------------- azd remote environment state (Entra-only access)
resource state 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take('stazd${replace(prefix, '-', '')}${token}', 24)
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource stateBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: state
  name: 'default'
}

resource stateContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: stateBlobs
  name: 'azd-state'
}

resource stateUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(stateUserPrincipalId)) {
  name: guid(state.id, stateUserPrincipalId, roles.blobDataContributor)
  scope: state
  properties: {
    principalId: stateUserPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}

output deployClientId string = deployOn ? deployer!.properties.clientId : ''
output stateAccountName string = state.name
output stateContainerName string = stateContainer.name
