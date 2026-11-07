// Isolated network for ephemeral sandbox VMs. Deployed into its own resource group so that the
// agent identity's VM/Network Contributor rights are confined to the sandbox.
param location string
param tags object
param prefix string
param principalId string
param token string = uniqueString(resourceGroup().id)
@description('Optional: the deploying user; may upload product installers.')
param userPrincipalId string = ''

var roles = {
  vmContributor: '9980e02c-c2be-4d73-94e8-173b1dc7cf3c'
  networkContributor: '4d97b98b-1d4f-4787-a291-c67834d212e7'
  blobDataReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  blobDelegator: 'db58b8e5-c6ad-4a2a-8342-4190687cbf4a'
  blobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
}

resource nsg 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'nsg-${prefix}-sandbox'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'deny-internet-outbound'
        properties: {
          priority: 100
          direction: 'Outbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: 'Internet'
          destinationPortRange: '*'
        }
      }
      {
        // Run Command and the VM agent need the Azure platform endpoints
        name: 'allow-azure-cloud-outbound'
        properties: {
          priority: 90
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: 'AzureCloud'
          destinationPortRange: '443'
        }
      }
      {
        name: 'deny-vnet-lateral'
        properties: {
          priority: 110
          direction: 'Outbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '*'
        }
      }
      {
        name: 'deny-all-inbound'
        properties: {
          priority: 4000
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: 'vnet-${prefix}-sandbox'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.250.0.0/24'] }
    subnets: [
      {
        name: 'sandbox'
        properties: {
          addressPrefix: '10.250.0.0/25'
          networkSecurityGroup: { id: nsg.id }
          defaultOutboundAccess: false
        }
      }
    ]
  }
}

resource vmRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, principalId, roles.vmContributor)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.vmContributor)
  }
}

resource netRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, principalId, roles.networkContributor)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.networkContributor)
  }
}

// Product installers for the sandbox VMs. Entra-only (no account keys); VMs get a short-lived
// user-delegation SAS from the agent, so they need no identity of their own.
resource installers 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take('stsbx${replace(prefix, '-', '')}${token}', 24)
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

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: installers
  name: 'default'
}

resource installersContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'installers'
}

resource miBlobRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in [roles.blobDataReader, roles.blobDelegator]: {
  name: guid(installers.id, principalId, r)
  scope: installers
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r)
  }
}]

resource userBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(userPrincipalId)) {
  name: guid(installers.id, userPrincipalId, roles.blobDataContributor)
  scope: installers
  properties: {
    principalId: userPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}

output subnetId string = vnet.properties.subnets[0].id
output installersUrl string = '${installers.properties.primaryEndpoints.blob}installers'
