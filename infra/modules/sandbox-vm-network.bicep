// Isolated network for ephemeral sandbox VMs. Deployed into its own resource group so that the
// agent identity's VM/Network Contributor rights are confined to the sandbox.
param location string
param tags object
param prefix string
param principalId string

var roles = {
  vmContributor: '9980e02c-c2be-4d73-94e8-173b1dc7cf3c'
  networkContributor: '4d97b98b-1d4f-4787-a291-c67834d212e7'
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

output subnetId string = vnet.properties.subnets[0].id
